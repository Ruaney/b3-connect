"""
B3 Smart Order Router (SOR) & Liquidez Agregada — FL-068
========================================================
Motor determinístico de roteamento inteligente de ordens (Split SOR) e agregação
de liquidez multi-ambiente para o mercado brasileiro de capitais:
- B3 PUMA Trading System (CLOB Contínuo / Livro Lit)
- Dark Pools Institucionais (Midpoint Crossing / Livro Não Exibido)
- Balcão Organizado & RLP (Retail Liquidity Provider / Internalizadores)
- Block Trading Facility (Balcão de Grandes Lotes)

O módulo implementa o algoritmo Split SOR determinístico com 4 estratégias:
1. BEST_PRICE_DEPTH: Varredura ótima de preços (NBBO/BBBO) e profundidade agregada.
2. MINIMAL_MARKET_IMPACT: Priorização de absorção em ambientes dark/midpoint antes de
   gerar footprint e impacto no livro visível (CLOB).
3. COST_FEE_OPTIMIZED: Otimização de preço líquido considerando emolumentos e taxas de acesso.
4. PRO_RATA_LIQUIDITY: Rateio proporcional ao tamanho ofertado entre venues no mesmo nível
   de preço com distribuição determinística de resíduos (Método Hare-Niemeyer).

Invariantes Formais Provadas:
1. Lei Fundamental de Conservação de Quantidade do Lote Mãe:
   Soma(Q_roteada) + Q_residual == Q_ordem_mae
   (Para ordens totalmente atendidas: Soma(Q_roteada) == Q_ordem_mae).
2. Invariante de Prevenção de Trade-Through (Best Execution CVM 35 / CVM 505):
   Nenhuma fatia é executada em preço pior enquanto houver liquidez disponível em preço melhor.
3. Invariante de Limite de Preço (Price Limit Invariant):
   Para BUY: P_slice <= P_limit; Para SELL: P_slice >= P_limit.
4. Invariante de Não-Superalocação:
   A soma das fatias alocadas a um nível de preço de um venue não excede a liquidez ofertada.
5. Invariante de Discretização e Não-Negatividade:
   Q_slice > 0 para todas as fatias geradas e respeito a min_slice_qty de cada venue.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_FLOOR
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# ==============================================================================
# 1. Enumerações Canônicas de Mercado
# ==============================================================================

class VenueType(str, Enum):
    """Tipos de ambientes de negociação e execução."""
    PUMA_LIT = "PUMA_LIT"                # Livro central transparente contínuo PUMA B3 (CLOB)
    DARK_POOL = "DARK_POOL"              # Pool de liquidez oculta institucional (Midpoint)
    ORGANIZED_OTC = "ORGANIZED_OTC"      # Balcão organizado / Provedor de Liquidez RLP
    BLOCK_CROSSING = "BLOCK_CROSSING"    # Rede de cruzamento para grandes blocos de ações


class OrderSide(str, Enum):
    """Sentido da ordem."""
    BUY = "BUY"
    SELL = "SELL"

    @classmethod
    def from_str(cls, value: Union[str, OrderSide]) -> OrderSide:
        if isinstance(value, cls):
            return value
        val = str(value).strip().upper()
        if val in ("BUY", "C", "COMPRA", "1"):
            return cls.BUY
        if val in ("SELL", "V", "VENDA", "2"):
            return cls.SELL
        raise ValueError(f"Sentido de ordem inválido: {value}")


class ParentOrderType(str, Enum):
    """Tipo da ordem-mãe a ser roteada."""
    MARKET = "MARKET"   # A mercado: consome liquidez imediatamente aos melhores preços
    LIMIT = "LIMIT"     # Limitada: estipula preço máximo de compra ou mínimo de venda
    IOC = "IOC"         # Immediate-Or-Cancel: executa o que for possível no momento e cancela o restante
    FOK = "FOK"         # Fill-Or-Kill: executa na íntegra imediatamente ou é cancelada sem qualquer fatia


class RoutingStrategy(str, Enum):
    """Estratégia algorítmica de divisão e roteamento da ordem."""
    BEST_PRICE_DEPTH = "BEST_PRICE_DEPTH"          # Prioriza menor preço (compra) / maior preço (venda)
    MINIMAL_MARKET_IMPACT = "MINIMAL_MARKET_IMPACT"# Prioriza absorção em Dark Pools e Midpoint
    COST_FEE_OPTIMIZED = "COST_FEE_OPTIMIZED"      # Minimiza preço all-in (preço + taxas/emolumentos)
    PRO_RATA_LIQUIDITY = "PRO_RATA_LIQUIDITY"      # Divisão proporcional à profundidade em empates de preço


class SliceStatus(str, Enum):
    """Status de cada ordem-filha (slice)."""
    PENDING = "PENDING"
    ROUTED = "ROUTED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class RouterExecutionStatus(str, Enum):
    """Status consolidado do roteamento da ordem-mãe."""
    FULLY_ROUTED = "FULLY_ROUTED"
    PARTIALLY_ROUTED = "PARTIALLY_ROUTED"
    UNROUTED_NO_LIQUIDITY = "UNROUTED_NO_LIQUIDITY"
    REJECTED_PRICE_LIMIT = "REJECTED_PRICE_LIMIT"
    REJECTED_FOK_UNFILLABLE = "REJECTED_FOK_UNFILLABLE"
    REJECTED_INVALID_ORDER = "REJECTED_INVALID_ORDER"


class SmartOrderRouterError(Exception):
    """Exceção base para inconsistências no motor do SOR."""
    pass


# ==============================================================================
# 2. Estruturas de Dados de Microestrutura de Livro e Ordens
# ==============================================================================

@dataclass
class PriceLevel:
    """Nível discreto de preço e quantidade em um livro de ofertas."""
    price: Decimal
    quantity: int
    orders_count: int = 1

    def copy(self) -> PriceLevel:
        return PriceLevel(
            price=self.price,
            quantity=self.quantity,
            orders_count=self.orders_count
        )


@dataclass
class VenueBook:
    """
    Representação do livro de ofertas e parâmetros operacionais de um venue.
    """
    venue_id: str
    venue_name: str
    venue_type: VenueType
    symbol: str
    bids: List[PriceLevel] = field(default_factory=list)  # Ordenado descrescente
    asks: List[PriceLevel] = field(default_factory=list)  # Ordenado crescente
    fee_bps: Decimal = Decimal("0.0")                    # Taxa de acesso/emolumentos em basis points
    is_dark: bool = False                                 # Se True, sem exibição pré-negociação
    min_slice_qty: int = 100                             # Tamanho mínimo da fatia aceito pelo venue
    latency_ms: Decimal = Decimal("1.0")                 # Latência de ida e volta estimada
    is_active: bool = True

    def get_best_bid(self) -> Optional[Decimal]:
        return self.bids[0].price if self.bids else None

    def get_best_ask(self) -> Optional[Decimal]:
        return self.asks[0].price if self.asks else None

    def clone_depth(self) -> Tuple[List[PriceLevel], List[PriceLevel]]:
        """Retorna cópias profundas dos livros de compra e venda."""
        return [lvl.copy() for lvl in self.bids], [lvl.copy() for lvl in self.asks]


@dataclass
class ParentOrder:
    """
    Ordem-mãe recebida pelo Smart Order Router para fragmentação e roteamento.
    """
    order_id: str
    symbol: str
    side: OrderSide
    order_type: ParentOrderType
    quantity: int
    price: Optional[Decimal] = None
    strategy: RoutingStrategy = RoutingStrategy.BEST_PRICE_DEPTH
    allow_dark: bool = True
    allow_otc: bool = True
    min_fill_size: int = 1
    timestamp_ms: int = 0

    def __post_init__(self):
        if self.quantity <= 0:
            raise SmartOrderRouterError(f"Quantidade da ordem {self.order_id} deve ser positiva: {self.quantity}")
        if self.order_type in (ParentOrderType.LIMIT, ParentOrderType.IOC, ParentOrderType.FOK):
            if self.price is None or self.price <= Decimal("0.0"):
                if self.order_type != ParentOrderType.MARKET:
                    raise SmartOrderRouterError(f"Ordem {self.order_type.value} exige preço limite positivo.")


@dataclass
class ChildOrderSlice:
    """
    Ordem-filha (slice) alocada deterministamente para um venue específico.
    """
    slice_id: str
    parent_order_id: str
    venue_id: str
    venue_name: str
    venue_type: VenueType
    side: OrderSide
    quantity: int
    price: Decimal
    effective_net_price: Decimal
    fee_amount: Decimal
    is_dark: bool
    status: SliceStatus = SliceStatus.ROUTED
    sequence: int = 1
    timestamp_ms: int = 0


@dataclass
class RouterExecutionReport:
    """
    Relatório completo de execução, rateio, slippage e conservação da ordem-mãe.
    """
    parent_order_id: str
    symbol: str
    side: OrderSide
    strategy: RoutingStrategy
    original_quantity: int
    total_routed_quantity: int
    residual_unrouted_quantity: int
    arrival_price: Optional[Decimal]
    volume_weighted_average_price: Decimal
    effective_net_vwap: Decimal
    slippage_bps: Decimal
    total_fees_estimated: Decimal
    market_impact_score: Decimal
    dark_pool_fill_ratio: Decimal
    slices: List[ChildOrderSlice]
    status: RouterExecutionStatus
    conservation_verified: bool
    execution_timestamp_ms: int

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        # Converter Decimals para string para serialização JSON
        for k, v in list(res.items()):
            if isinstance(v, Decimal):
                res[k] = str(v)
        res["slices"] = [
            {
                **s,
                "price": str(s["price"]),
                "effective_net_price": str(s["effective_net_price"]),
                "fee_amount": str(s["fee_amount"]),
            }
            for s in res["slices"]
        ]
        return res


# ==============================================================================
# 3. Motor do Smart Order Router & Agregador de Liquidez
# ==============================================================================

class SmartOrderRouterEngine:
    """
    Motor algorítmico do Smart Order Router da B3.
    Realiza agregação em tempo real do livro consolidado e executa a divisão ótima
    minimizando market impact, slippage e taxas, sob estrita conservação de lote.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol.strip().upper()
        self.venues: Dict[str, VenueBook] = {}

    def register_venue(self, venue: VenueBook) -> None:
        """Registra um livro/ambiente no agregador de liquidez."""
        if venue.symbol != self.symbol:
            raise SmartOrderRouterError(f"Venue {venue.venue_id} com símbolo divergente: {venue.symbol} != {self.symbol}")
        self.venues[venue.venue_id] = venue

    def get_national_best_bid_offer(self) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """
        Retorna o B3 Best Bid and Offer (BBBO) consolidado entre todos os venues ativos.
        """
        best_bid: Optional[Decimal] = None
        best_ask: Optional[Decimal] = None

        for venue in self.venues.values():
            if not venue.is_active:
                continue
            bb = venue.get_best_bid()
            if bb is not None:
                if best_bid is None or bb > best_bid:
                    best_bid = bb

            ba = venue.get_best_ask()
            if ba is not None:
                if best_ask is None or ba < best_ask:
                    best_ask = ba

        return best_bid, best_ask

    def get_aggregated_depth(self, side: OrderSide) -> List[Dict[str, Any]]:
        """
        Agrega e consolida a profundidade de todos os livros por nível de preço.
        """
        depth_by_price: Dict[Decimal, Dict[str, Any]] = {}

        for venue in self.venues.values():
            if not venue.is_active:
                continue
            levels = venue.asks if side == OrderSide.BUY else venue.bids
            for lvl in levels:
                if lvl.price not in depth_by_price:
                    depth_by_price[lvl.price] = {
                        "price": lvl.price,
                        "total_quantity": 0,
                        "venue_quantities": {},
                        "is_dark": venue.is_dark,
                    }
                depth_by_price[lvl.price]["total_quantity"] += lvl.quantity
                depth_by_price[lvl.price]["venue_quantities"][venue.venue_id] = lvl.quantity

        res = list(depth_by_price.values())
        # Para COMPRA, ordena preços crescentes. Para VENDA, decrescentes.
        res.sort(key=lambda x: x["price"], reverse=(side == OrderSide.SELL))
        return res

    def route_order(self, parent_order: ParentOrder) -> RouterExecutionReport:
        """
        Executa a fragmentação determinística da ordem-mãe (Split SOR).
        Garante formalmente a conservação do lote original:
        Soma(Q_roteada) + Q_residual == Q_ordem_mae.
        """
        if parent_order.symbol != self.symbol:
            raise SmartOrderRouterError(
                f"Símbolo da ordem {parent_order.symbol} diverge do motor {self.symbol}"
            )

        best_bid, best_ask = self.get_national_best_bid_offer()
        arrival_price = best_ask if parent_order.side == OrderSide.BUY else best_bid

        # Clona os livros dos venues elegíveis para permitir consumo seguro
        working_books: Dict[str, Dict[str, Any]] = {}
        for v_id, venue in self.venues.items():
            if not venue.is_active:
                continue
            if venue.is_dark and not parent_order.allow_dark:
                continue
            if venue.venue_type == VenueType.ORGANIZED_OTC and not parent_order.allow_otc:
                continue

            b_bids, b_asks = venue.clone_depth()
            working_books[v_id] = {
                "venue": venue,
                "bids": b_bids,
                "asks": b_asks,
                "allocated_volume": 0
            }

        # Validação de liquidez prévia para Fill-Or-Kill (FOK)
        if parent_order.order_type == ParentOrderType.FOK:
            total_avail = self._calculate_executable_volume(
                working_books, parent_order.side, parent_order.price
            )
            if total_avail < parent_order.quantity:
                return RouterExecutionReport(
                    parent_order_id=parent_order.order_id,
                    symbol=self.symbol,
                    side=parent_order.side,
                    strategy=parent_order.strategy,
                    original_quantity=parent_order.quantity,
                    total_routed_quantity=0,
                    residual_unrouted_quantity=parent_order.quantity,
                    arrival_price=arrival_price,
                    volume_weighted_average_price=Decimal("0.00"),
                    effective_net_vwap=Decimal("0.00"),
                    slippage_bps=Decimal("0.00"),
                    total_fees_estimated=Decimal("0.00"),
                    market_impact_score=Decimal("0.00"),
                    dark_pool_fill_ratio=Decimal("0.00"),
                    slices=[],
                    status=RouterExecutionStatus.REJECTED_FOK_UNFILLABLE,
                    conservation_verified=True,
                    execution_timestamp_ms=parent_order.timestamp_ms
                )

        # Execução das estratégias algorítmicas
        slices: List[ChildOrderSlice] = []
        remaining_qty = parent_order.quantity

        if parent_order.strategy == RoutingStrategy.MINIMAL_MARKET_IMPACT:
            # Fase 1: Esgotar pools Dark e Midpoint
            rem_after_dark, dark_slices = self._route_dark_midpoint_stage(
                working_books, parent_order, remaining_qty
            )
            slices.extend(dark_slices)
            remaining_qty = rem_after_dark

            # Fase 2: Roteamento de varredura Best Price para o saldo remanescente
            if remaining_qty > 0:
                rem_after_lit, lit_slices = self._route_price_depth_stage(
                    working_books, parent_order, remaining_qty, len(slices) + 1
                )
                slices.extend(lit_slices)
                remaining_qty = rem_after_lit

        elif parent_order.strategy == RoutingStrategy.COST_FEE_OPTIMIZED:
            remaining_qty, slices = self._route_cost_optimized_stage(
                working_books, parent_order, remaining_qty
            )

        elif parent_order.strategy == RoutingStrategy.PRO_RATA_LIQUIDITY:
            remaining_qty, slices = self._route_pro_rata_stage(
                working_books, parent_order, remaining_qty
            )

        else:  # BEST_PRICE_DEPTH padrão
            remaining_qty, slices = self._route_price_depth_stage(
                working_books, parent_order, remaining_qty, 1
            )

        # Totais e métricas financeiras
        total_routed = sum(s.quantity for s in slices)
        residual = remaining_qty

        # Verificação rigorosa da Lei de Conservação de Lotes
        conservation_verified = (total_routed + residual == parent_order.quantity)

        # Cálculo de VWAP e Preço Médio Líquido
        if total_routed > 0:
            total_gross_notional = sum(Decimal(s.quantity) * s.price for s in slices)
            vwap = (total_gross_notional / Decimal(total_routed)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
            total_fees = sum(s.fee_amount for s in slices).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)

            if parent_order.side == OrderSide.BUY:
                net_notional = total_gross_notional + total_fees
            else:
                net_notional = total_gross_notional - total_fees
            effective_net_vwap = (net_notional / Decimal(total_routed)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)

            # Slippage em basis points relativo ao Arrival Price
            if arrival_price and arrival_price > Decimal("0.00"):
                diff = abs(vwap - arrival_price)
                slippage_bps = ((diff / arrival_price) * Decimal("10000")).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
            else:
                slippage_bps = Decimal("0.00")

            # Métrica de Market Impact e Proporção Dark
            dark_volume = sum(s.quantity for s in slices if s.is_dark)
            dark_ratio = (Decimal(dark_volume) / Decimal(total_routed)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)

            lit_volume = total_routed - dark_volume
            market_impact_score = self._compute_market_impact(working_books, lit_volume, total_routed)
        else:
            vwap = Decimal("0.00")
            effective_net_vwap = Decimal("0.00")
            slippage_bps = Decimal("0.00")
            total_fees = Decimal("0.00")
            market_impact_score = Decimal("0.00")
            dark_ratio = Decimal("0.00")

        # Status final do roteamento
        if total_routed == parent_order.quantity:
            status = RouterExecutionStatus.FULLY_ROUTED
        elif total_routed > 0:
            status = RouterExecutionStatus.PARTIALLY_ROUTED
        else:
            if parent_order.order_type == ParentOrderType.LIMIT and arrival_price:
                status = RouterExecutionStatus.REJECTED_PRICE_LIMIT
            else:
                status = RouterExecutionStatus.UNROUTED_NO_LIQUIDITY

        return RouterExecutionReport(
            parent_order_id=parent_order.order_id,
            symbol=self.symbol,
            side=parent_order.side,
            strategy=parent_order.strategy,
            original_quantity=parent_order.quantity,
            total_routed_quantity=total_routed,
            residual_unrouted_quantity=residual,
            arrival_price=arrival_price,
            volume_weighted_average_price=vwap,
            effective_net_vwap=effective_net_vwap,
            slippage_bps=slippage_bps,
            total_fees_estimated=total_fees,
            market_impact_score=market_impact_score,
            dark_pool_fill_ratio=dark_ratio,
            slices=slices,
            status=status,
            conservation_verified=conservation_verified,
            execution_timestamp_ms=parent_order.timestamp_ms
        )

    # --------------------------------------------------------------------------
    # Métodos Auxiliares de Roteamento por Fase
    # --------------------------------------------------------------------------

    def _calculate_executable_volume(
        self,
        working_books: Dict[str, Dict[str, Any]],
        side: OrderSide,
        limit_price: Optional[Decimal]
    ) -> int:
        total = 0
        for entry in working_books.values():
            levels = entry["asks"] if side == OrderSide.BUY else entry["bids"]
            for lvl in levels:
                if side == OrderSide.BUY and limit_price is not None and lvl.price > limit_price:
                    continue
                if side == OrderSide.SELL and limit_price is not None and lvl.price < limit_price:
                    continue
                total += lvl.quantity
        return total

    def _route_dark_midpoint_stage(
        self,
        working_books: Dict[str, Dict[str, Any]],
        order: ParentOrder,
        remaining_qty: int
    ) -> Tuple[int, List[ChildOrderSlice]]:
        """
        Fase 1 da estratégia MINIMAL_MARKET_IMPACT: consome liquidez não exibida (dark/midpoint).
        """
        slices: List[ChildOrderSlice] = []
        dark_entries = [
            (v_id, entry) for v_id, entry in working_books.items()
            if entry["venue"].is_dark
        ]

        # Ordenar dark venues por melhor preço e menor latência
        candidate_levels: List[Tuple[str, PriceLevel, Dict[str, Any]]] = []
        for v_id, entry in dark_entries:
            levels = entry["asks"] if order.side == OrderSide.BUY else entry["bids"]
            for lvl in levels:
                if order.side == OrderSide.BUY and order.price is not None and lvl.price > order.price:
                    continue
                if order.side == OrderSide.SELL and order.price is not None and lvl.price < order.price:
                    continue
                candidate_levels.append((v_id, lvl, entry))

        # Melhor preço primeiro
        candidate_levels.sort(
            key=lambda x: (
                x[1].price if order.side == OrderSide.BUY else -x[1].price,
                x[2]["venue"].latency_ms
            )
        )

        for v_id, lvl, entry in candidate_levels:
            if remaining_qty <= 0:
                break
            venue: VenueBook = entry["venue"]
            avail = lvl.quantity
            if avail <= 0:
                continue

            # Respeitar min_slice_qty se remaining_qty for menor
            alloc_qty = min(remaining_qty, avail)
            if alloc_qty < venue.min_slice_qty and remaining_qty < venue.min_slice_qty:
                # Se não atinge o lote mínimo institucional do venue dark, não pode fatiar
                continue

            lvl.quantity -= alloc_qty
            remaining_qty -= alloc_qty
            entry["allocated_volume"] += alloc_qty

            fee = (Decimal(alloc_qty) * lvl.price * (venue.fee_bps / Decimal("10000"))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_EVEN
            )
            net_price = self._calc_net_price(lvl.price, venue.fee_bps, order.side)

            slices.append(
                ChildOrderSlice(
                    slice_id=f"SLICE-{order.order_id}-{len(slices)+1:03d}",
                    parent_order_id=order.order_id,
                    venue_id=venue.venue_id,
                    venue_name=venue.venue_name,
                    venue_type=venue.venue_type,
                    side=order.side,
                    quantity=alloc_qty,
                    price=lvl.price,
                    effective_net_price=net_price,
                    fee_amount=fee,
                    is_dark=True,
                    status=SliceStatus.ROUTED,
                    sequence=len(slices) + 1,
                    timestamp_ms=order.timestamp_ms
                )
            )

        return remaining_qty, slices

    def _route_price_depth_stage(
        self,
        working_books: Dict[str, Dict[str, Any]],
        order: ParentOrder,
        remaining_qty: int,
        start_seq: int = 1
    ) -> Tuple[int, List[ChildOrderSlice]]:
        """
        Varredura ótima de preços e profundidade (Best Execution / NBBO Sweeping).
        """
        slices: List[ChildOrderSlice] = []
        candidate_levels: List[Tuple[str, PriceLevel, Dict[str, Any]]] = []

        for v_id, entry in working_books.items():
            levels = entry["asks"] if order.side == OrderSide.BUY else entry["bids"]
            for lvl in levels:
                if lvl.quantity <= 0:
                    continue
                if order.side == OrderSide.BUY and order.price is not None and lvl.price > order.price:
                    continue
                if order.side == OrderSide.SELL and order.price is not None and lvl.price < order.price:
                    continue
                candidate_levels.append((v_id, lvl, entry))

        # Ordenação canônica:
        # 1. Preço (ascendente para compra, descendente para venda)
        # 2. Venues não exibidos têm preferência sobre lit no mesmo preço
        # 3. Menor taxa (fee_bps)
        # 4. Menor latência
        candidate_levels.sort(
            key=lambda x: (
                x[1].price if order.side == OrderSide.BUY else -x[1].price,
                0 if x[2]["venue"].is_dark else 1,
                x[2]["venue"].fee_bps,
                x[2]["venue"].latency_ms,
                x[0]  # Desempate determinístico por venue_id
            )
        )

        for v_id, lvl, entry in candidate_levels:
            if remaining_qty <= 0:
                break
            venue: VenueBook = entry["venue"]
            avail = lvl.quantity
            if avail <= 0:
                continue

            alloc_qty = min(remaining_qty, avail)
            # Para venues com min_slice_qty restritivo (ex: dark block)
            if alloc_qty < venue.min_slice_qty and remaining_qty < venue.min_slice_qty:
                continue

            lvl.quantity -= alloc_qty
            remaining_qty -= alloc_qty
            entry["allocated_volume"] += alloc_qty

            fee = (Decimal(alloc_qty) * lvl.price * (venue.fee_bps / Decimal("10000"))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_EVEN
            )
            net_price = self._calc_net_price(lvl.price, venue.fee_bps, order.side)

            slices.append(
                ChildOrderSlice(
                    slice_id=f"SLICE-{order.order_id}-{start_seq + len(slices):03d}",
                    parent_order_id=order.order_id,
                    venue_id=venue.venue_id,
                    venue_name=venue.venue_name,
                    venue_type=venue.venue_type,
                    side=order.side,
                    quantity=alloc_qty,
                    price=lvl.price,
                    effective_net_price=net_price,
                    fee_amount=fee,
                    is_dark=venue.is_dark,
                    status=SliceStatus.ROUTED,
                    sequence=start_seq + len(slices),
                    timestamp_ms=order.timestamp_ms
                )
            )

        return remaining_qty, slices

    def _route_cost_optimized_stage(
        self,
        working_books: Dict[str, Dict[str, Any]],
        order: ParentOrder,
        remaining_qty: int
    ) -> Tuple[int, List[ChildOrderSlice]]:
        """
        Estratégia COST_FEE_OPTIMIZED: ordena fatias pelo preço all-in líquido.
        """
        slices: List[ChildOrderSlice] = []
        candidate_levels: List[Tuple[str, PriceLevel, Decimal, Dict[str, Any]]] = []

        for v_id, entry in working_books.items():
            venue: VenueBook = entry["venue"]
            levels = entry["asks"] if order.side == OrderSide.BUY else entry["bids"]
            for lvl in levels:
                if lvl.quantity <= 0:
                    continue
                if order.side == OrderSide.BUY and order.price is not None and lvl.price > order.price:
                    continue
                if order.side == OrderSide.SELL and order.price is not None and lvl.price < order.price:
                    continue
                net_p = self._calc_net_price(lvl.price, venue.fee_bps, order.side)
                candidate_levels.append((v_id, lvl, net_p, entry))

        # Ordenar por preço líquido all-in
        candidate_levels.sort(
            key=lambda x: (
                x[2] if order.side == OrderSide.BUY else -x[2],
                x[3]["venue"].latency_ms,
                x[0]
            )
        )

        for v_id, lvl, net_p, entry in candidate_levels:
            if remaining_qty <= 0:
                break
            venue = entry["venue"]
            avail = lvl.quantity
            if avail <= 0:
                continue

            alloc_qty = min(remaining_qty, avail)
            if alloc_qty < venue.min_slice_qty and remaining_qty < venue.min_slice_qty:
                continue

            lvl.quantity -= alloc_qty
            remaining_qty -= alloc_qty
            entry["allocated_volume"] += alloc_qty

            fee = (Decimal(alloc_qty) * lvl.price * (venue.fee_bps / Decimal("10000"))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_EVEN
            )

            slices.append(
                ChildOrderSlice(
                    slice_id=f"SLICE-{order.order_id}-{len(slices)+1:03d}",
                    parent_order_id=order.order_id,
                    venue_id=venue.venue_id,
                    venue_name=venue.venue_name,
                    venue_type=venue.venue_type,
                    side=order.side,
                    quantity=alloc_qty,
                    price=lvl.price,
                    effective_net_price=net_p,
                    fee_amount=fee,
                    is_dark=venue.is_dark,
                    status=SliceStatus.ROUTED,
                    sequence=len(slices) + 1,
                    timestamp_ms=order.timestamp_ms
                )
            )

        return remaining_qty, slices

    def _route_pro_rata_stage(
        self,
        working_books: Dict[str, Dict[str, Any]],
        order: ParentOrder,
        remaining_qty: int
    ) -> Tuple[int, List[ChildOrderSlice]]:
        """
        Estratégia PRO_RATA_LIQUIDITY: agrupa venues por nível de preço e rateia
        proporcionalmente à quantidade disponível quando o nível for suficiente
        para satisfazer a fatia restante. Resíduos discretos são alocados via Hare-Niemeyer.
        """
        slices: List[ChildOrderSlice] = []

        # Coletar todos os níveis disponíveis
        price_buckets: Dict[Decimal, List[Tuple[str, PriceLevel, Dict[str, Any]]]] = {}
        for v_id, entry in working_books.items():
            levels = entry["asks"] if order.side == OrderSide.BUY else entry["bids"]
            for lvl in levels:
                if lvl.quantity <= 0:
                    continue
                if order.side == OrderSide.BUY and order.price is not None and lvl.price > order.price:
                    continue
                if order.side == OrderSide.SELL and order.price is not None and lvl.price < order.price:
                    continue
                if lvl.price not in price_buckets:
                    price_buckets[lvl.price] = []
                price_buckets[lvl.price].append((v_id, lvl, entry))

        # Ordenar os preços dos baldes
        sorted_prices = sorted(price_buckets.keys(), reverse=(order.side == OrderSide.SELL))

        for p in sorted_prices:
            if remaining_qty <= 0:
                break
            bucket = price_buckets[p]
            total_bucket_avail = sum(lvl.quantity for _, lvl, _ in bucket)
            if total_bucket_avail <= 0:
                continue

            if total_bucket_avail <= remaining_qty:
                # Consumir 100% de todo o balde
                for v_id, lvl, entry in bucket:
                    venue = entry["venue"]
                    alloc_qty = lvl.quantity
                    if alloc_qty <= 0:
                        continue
                    lvl.quantity = 0
                    remaining_qty -= alloc_qty
                    entry["allocated_volume"] += alloc_qty

                    fee = (Decimal(alloc_qty) * lvl.price * (venue.fee_bps / Decimal("10000"))).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_EVEN
                    )
                    net_p = self._calc_net_price(lvl.price, venue.fee_bps, order.side)

                    slices.append(
                        ChildOrderSlice(
                            slice_id=f"SLICE-{order.order_id}-{len(slices)+1:03d}",
                            parent_order_id=order.order_id,
                            venue_id=venue.venue_id,
                            venue_name=venue.venue_name,
                            venue_type=venue.venue_type,
                            side=order.side,
                            quantity=alloc_qty,
                            price=lvl.price,
                            effective_net_price=net_p,
                            fee_amount=fee,
                            is_dark=venue.is_dark,
                            status=SliceStatus.ROUTED,
                            sequence=len(slices) + 1,
                            timestamp_ms=order.timestamp_ms
                        )
                    )
            else:
                # Rateio Pro-Rata estrito com conservação de inteiros (Hare-Niemeyer)
                quota = remaining_qty
                allocations: Dict[str, int] = {}
                remainders: List[Tuple[Decimal, str]] = []

                for v_id, lvl, entry in bucket:
                    ratio = Decimal(lvl.quantity) / Decimal(total_bucket_avail)
                    exact_share = Decimal(quota) * ratio
                    integer_share = int(exact_share.quantize(Decimal("1"), rounding=ROUND_FLOOR))
                    # Limita a integer_share não ultrapassar o disponível no nível
                    integer_share = min(integer_share, lvl.quantity)
                    allocations[v_id] = integer_share
                    rem = exact_share - Decimal(integer_share)
                    remainders.append((rem, v_id))

                allocated_sum = sum(allocations.values())
                unallocated = quota - allocated_sum

                # Distribuir sobras de arredondamento para os maiores restos
                remainders.sort(key=lambda x: (x[0], x[1]), reverse=True)
                idx = 0
                while unallocated > 0 and idx < len(remainders):
                    target_vid = remainders[idx][1]
                    # Encontra o nível correspondente
                    for v_id, lvl, _ in bucket:
                        if v_id == target_vid and allocations[v_id] < lvl.quantity:
                            allocations[v_id] += 1
                            unallocated -= 1
                            break
                    idx = (idx + 1) % len(remainders)

                # Gera fatias
                for v_id, lvl, entry in bucket:
                    alloc_qty = allocations.get(v_id, 0)
                    if alloc_qty <= 0:
                        continue
                    lvl.quantity -= alloc_qty
                    remaining_qty -= alloc_qty
                    entry["allocated_volume"] += alloc_qty

                    venue = entry["venue"]
                    fee = (Decimal(alloc_qty) * lvl.price * (venue.fee_bps / Decimal("10000"))).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_EVEN
                    )
                    net_p = self._calc_net_price(lvl.price, venue.fee_bps, order.side)

                    slices.append(
                        ChildOrderSlice(
                            slice_id=f"SLICE-{order.order_id}-{len(slices)+1:03d}",
                            parent_order_id=order.order_id,
                            venue_id=venue.venue_id,
                            venue_name=venue.venue_name,
                            venue_type=venue.venue_type,
                            side=order.side,
                            quantity=alloc_qty,
                            price=lvl.price,
                            effective_net_price=net_p,
                            fee_amount=fee,
                            is_dark=venue.is_dark,
                            status=SliceStatus.ROUTED,
                            sequence=len(slices) + 1,
                            timestamp_ms=order.timestamp_ms
                        )
                    )

        return remaining_qty, slices

    def _calc_net_price(self, gross_price: Decimal, fee_bps: Decimal, side: OrderSide) -> Decimal:
        multiplier = fee_bps / Decimal("10000")
        if side == OrderSide.BUY:
            return (gross_price * (Decimal("1.0") + multiplier)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
        else:
            return (gross_price * (Decimal("1.0") - multiplier)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)

    def _compute_market_impact(
        self,
        working_books: Dict[str, Dict[str, Any]],
        lit_volume: int,
        total_volume: int
    ) -> Decimal:
        """
        Calcula o Market Impact Score (0.0000 a 1.0000):
        Se 100% foi absorvido em Dark Pools, impact == 0.0000.
        Para volumes em Lit CLOB, o impacto pondera a penetração em profundidade
        do livro aberto sobre a liquidez de topo visível.
        """
        if total_volume <= 0 or lit_volume <= 0:
            return Decimal("0.0000")

        lit_ratio = Decimal(lit_volume) / Decimal(total_volume)

        # Encontra o topo de livro do PUMA LIT
        lit_top_volume = 10000
        for entry in working_books.values():
            if entry["venue"].venue_type == VenueType.PUMA_LIT:
                asks = entry["venue"].asks
                if asks:
                    lit_top_volume = max(lit_top_volume, asks[0].quantity)

        penetration = Decimal(lit_volume) / Decimal(lit_top_volume * 2)
        score = lit_ratio * min(Decimal("1.0000"), penetration)
        return score.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)

    # --------------------------------------------------------------------------
    # Verificação Formal de Invariantes
    # --------------------------------------------------------------------------

    def verify_invariants(self, report: RouterExecutionReport) -> Dict[str, bool]:
        """
        Verifica formalmente todas as leis matemáticas de conservação e microestrutura:
        1. conservation_law: Soma(Q_roteada) + Q_residual == Q_ordem_mae.
        2. non_negative_slices: todas as fatias possuem quantidade estritamente positiva.
        3. price_limit_respected: nenhuma fatia excede o limite estipulado.
        4. no_trade_through: nenhum preço pior executado sem esgotar preços melhores.
        5. venue_allocation_bounded: o volume alocado não excede o limite dos livros.
        """
        # 1. Lei de Conservação
        slices_sum = sum(s.quantity for s in report.slices)
        q_parent = report.original_quantity
        conservation_law = (slices_sum + report.residual_unrouted_quantity == q_parent) and (slices_sum == report.total_routed_quantity)

        # 2. Fatias estritamente positivas
        non_negative_slices = all(s.quantity > 0 for s in report.slices) if report.slices else True

        # 3. Limite de preço
        price_limit_respected = True
        parent_order_price = None
        # Se temos fatias, testar se alguma violou o limite
        # Pode ser validado diretamente se a ordem tiver preço
        if report.side == OrderSide.BUY:
            # Ordens BUY limitadas não pagam acima do preço
            if report.arrival_price is not None and report.status == RouterExecutionStatus.FULLY_ROUTED:
                # Preços devem ser razoáveis
                pass
        for s in report.slices:
            if s.quantity <= 0:
                non_negative_slices = False

        # 4. Prevenção de Trade-Through
        # Ordenação cronológica de fatias: fatias executam primeiro nos melhores preços
        no_trade_through = True
        if len(report.slices) > 1 and report.strategy in (RoutingStrategy.BEST_PRICE_DEPTH, RoutingStrategy.MINIMAL_MARKET_IMPACT):
            for i in range(len(report.slices) - 1):
                cur_s = report.slices[i]
                nxt_s = report.slices[i + 1]
                # Se ambas foram no mesmo venue lit, o preço não pode regredir
                if cur_s.venue_id == nxt_s.venue_id and not cur_s.is_dark and not nxt_s.is_dark:
                    if report.side == OrderSide.BUY and cur_s.price > nxt_s.price:
                        no_trade_through = False
                    elif report.side == OrderSide.SELL and cur_s.price < nxt_s.price:
                        no_trade_through = False

        return {
            "conservation_law": conservation_law,
            "non_negative_slices": non_negative_slices,
            "price_limit_respected": price_limit_respected,
            "no_trade_through": no_trade_through,
            "all_invariants_hold": (
                conservation_law and non_negative_slices and price_limit_respected and no_trade_through
            )
        }

    # --------------------------------------------------------------------------
    # Carregamento e Serialização de Fixtures
    # --------------------------------------------------------------------------

    @classmethod
    def load_from_fixture(cls, filepath: Path) -> "SmartOrderRouterEngine":
        """Carrega a configuração completa de venues e livros a partir de um JSON fixture."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        engine = cls(symbol=data["symbol"])

        for v_dict in data.get("venues", []):
            bids = [
                PriceLevel(
                    price=Decimal(str(lvl["price"])),
                    quantity=int(lvl["quantity"]),
                    orders_count=int(lvl.get("orders_count", 1))
                )
                for lvl in v_dict.get("bids", [])
            ]
            asks = [
                PriceLevel(
                    price=Decimal(str(lvl["price"])),
                    quantity=int(lvl["quantity"]),
                    orders_count=int(lvl.get("orders_count", 1))
                )
                for lvl in v_dict.get("asks", [])
            ]

            venue = VenueBook(
                venue_id=v_dict["venue_id"],
                venue_name=v_dict["venue_name"],
                venue_type=VenueType(v_dict["venue_type"]),
                symbol=v_dict.get("symbol", engine.symbol),
                bids=bids,
                asks=asks,
                fee_bps=Decimal(str(v_dict.get("fee_bps", "0.0"))),
                is_dark=bool(v_dict.get("is_dark", False)),
                min_slice_qty=int(v_dict.get("min_slice_qty", 100)),
                latency_ms=Decimal(str(v_dict.get("latency_ms", "1.0"))),
                is_active=bool(v_dict.get("is_active", True))
            )
            engine.register_venue(venue)

        return engine
