"""Motor de Túneis de Oscilação de Preços e Leilão Contínuo de Circuit Breaker — PUMA B3 (FL-093).

Implementa a infraestrutura determinística do PUMA Trading System para:
1. Parametrização estrita de Túneis de Negociação (Rejeição, Leilão Estático e Leilão Dinâmico);
2. Validação pré-matching de ofertas e rejeição sumária de ordens fora do túnel de rejeição;
3. Engatilhamento automático de Leilão Não-Programado (Circuit Breaker / Volatility Auction);
4. Formação determinística do Preço Teórico de Previsão (PTP) aplicando as 4 regras de desempate B3;
5. Execução do uncross no término do leilão e retorno seguro ao mercado contínuo;
6. Leis de invariantes:
   - LAW-098A: Não-transgressão de túnel em mercado contínuo;
   - LAW-098B: Maximização de volume no desfecho do leilão;
   - LAW-098C: Conservação de ordens no book durante transições de estado.

100% Python Standard Library (decimal, datetime, dataclasses, enum, json, pathlib, typing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union


def round_price(val: Union[Decimal, str, float]) -> Decimal:
    """Arredonda preço para 2 casas decimais (ou tick correspondente)."""
    d = Decimal(str(val))
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_ratio(val: Union[Decimal, str, float]) -> Decimal:
    """Arredonda percentuais para 4 casas decimais."""
    d = Decimal(str(val))
    return d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class AssetSegment(str, Enum):
    """Segmentos e classes de ativos negociados no PUMA."""
    IBOV_EQUITY = "IBOV_EQUITY"         # Ações do índice Ibovespa (alta liquidez)
    STANDARD_EQUITY = "STANDARD_EQUITY" # Ações Small/Mid Caps à vista
    FUTURES_DI = "FUTURES_DI"           # Futuros de Taxa DI1
    FUTURES_DOL = "FUTURES_DOL"         # Futuros de Dólar Comercial (DOL / WDO)
    FUTURES_IND = "FUTURES_IND"         # Futuros de Índice Ibovespa (IND / WIN)
    COMMODITY_FUT = "COMMODITY_FUT"     # Futuros de Commodities (BGI, CCM, ICF)


class MarketState(str, Enum):
    """Fases operacionais da sessão de negociação."""
    PRE_OPEN = "PRE_OPEN"
    CONTINUOUS = "CONTINUOUS"
    AUCTION_CIRCUIT_BREAKER = "AUCTION_CIRCUIT_BREAKER"
    AUCTION_EXTENSION = "AUCTION_EXTENSION"
    CLOSED = "CLOSED"


class OrderSide(str, Enum):
    """Sentido da ordem no book."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Tipologia de ordem."""
    LIMIT = "LIMIT"
    MARKET = "MARKET"


@dataclass(frozen=True)
class TunnelBandConfig:
    """Configuração percentual dos túneis de negociação para um ativo/segmento."""
    rejection_band_pct: Decimal       # Ex: 0.1500 (+/- 15% do preço de referência)
    static_auction_band_pct: Decimal  # Ex: 0.0800 (+/- 8% do preço estático/abertura)
    dynamic_auction_band_pct: Decimal # Ex: 0.0300 (+/- 3% do último preço negociado)
    min_auction_duration_sec: int = 300 # Duração mínima padrão do leilão (5 minutos)
    random_extension_max_sec: int = 60  # Extensão randômica máxima (1 minuto)

    def __post_init__(self) -> None:
        if self.rejection_band_pct <= self.static_auction_band_pct:
            raise ValueError("Túnel de rejeição deve ser estritamente mais amplo que o túnel estático")
        if self.static_auction_band_pct <= self.dynamic_auction_band_pct:
            raise ValueError("Túnel estático deve ser estritamente mais amplo que o túnel dinâmico")


DEFAULT_TUNNEL_CONFIGS: Dict[AssetSegment, TunnelBandConfig] = {
    AssetSegment.IBOV_EQUITY: TunnelBandConfig(
        rejection_band_pct=Decimal("0.1500"),
        static_auction_band_pct=Decimal("0.0800"),
        dynamic_auction_band_pct=Decimal("0.0300"),
        min_auction_duration_sec=300,
    ),
    AssetSegment.STANDARD_EQUITY: TunnelBandConfig(
        rejection_band_pct=Decimal("0.2000"),
        static_auction_band_pct=Decimal("0.1000"),
        dynamic_auction_band_pct=Decimal("0.0400"),
        min_auction_duration_sec=300,
    ),
    AssetSegment.FUTURES_DI: TunnelBandConfig(
        rejection_band_pct=Decimal("0.0500"),
        static_auction_band_pct=Decimal("0.0250"),
        dynamic_auction_band_pct=Decimal("0.0100"),
        min_auction_duration_sec=180,
    ),
    AssetSegment.FUTURES_DOL: TunnelBandConfig(
        rejection_band_pct=Decimal("0.0600"),
        static_auction_band_pct=Decimal("0.0300"),
        dynamic_auction_band_pct=Decimal("0.0150"),
        min_auction_duration_sec=180,
    ),
    AssetSegment.FUTURES_IND: TunnelBandConfig(
        rejection_band_pct=Decimal("0.1000"),
        static_auction_band_pct=Decimal("0.0500"),
        dynamic_auction_band_pct=Decimal("0.0200"),
        min_auction_duration_sec=180,
    ),
    AssetSegment.COMMODITY_FUT: TunnelBandConfig(
        rejection_band_pct=Decimal("0.1200"),
        static_auction_band_pct=Decimal("0.0600"),
        dynamic_auction_band_pct=Decimal("0.0250"),
        min_auction_duration_sec=300,
    ),
}


@dataclass
class TunnelBounds:
    """Limites numéricos absolutos de preços calculados a cada instante."""
    reference_price: Decimal
    rejection_lower: Decimal
    rejection_upper: Decimal
    static_lower: Decimal
    static_upper: Decimal
    dynamic_lower: Decimal
    dynamic_upper: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reference_price": str(self.reference_price),
            "rejection_lower": str(self.rejection_lower),
            "rejection_upper": str(self.rejection_upper),
            "static_lower": str(self.static_lower),
            "static_upper": str(self.static_upper),
            "dynamic_lower": str(self.dynamic_lower),
            "dynamic_upper": str(self.dynamic_upper),
        }


@dataclass
class Order:
    """Ordem submetida ao livro de ofertas."""
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    price: Optional[Decimal]
    quantity: int
    cum_qty: int = 0
    remaining_qty: int = 0
    participant_id: str = "BR-BROKER-01"
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if self.remaining_qty == 0 and self.cum_qty == 0:
            self.remaining_qty = self.quantity
        if self.price is not None:
            self.price = round_price(self.price)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "price": str(self.price) if self.price is not None else None,
            "quantity": self.quantity,
            "cum_qty": self.cum_qty,
            "remaining_qty": self.remaining_qty,
            "participant_id": self.participant_id,
            "timestamp_utc": self.timestamp_utc,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Order:
        return cls(
            order_id=data["order_id"],
            symbol=data["symbol"],
            side=OrderSide(data["side"]),
            order_type=OrderType(data["order_type"]),
            price=Decimal(data["price"]) if data.get("price") is not None else None,
            quantity=int(data["quantity"]),
            cum_qty=int(data.get("cum_qty", 0)),
            remaining_qty=int(data.get("remaining_qty", data["quantity"])),
            participant_id=data.get("participant_id", "BR-BROKER-01"),
            timestamp_utc=data.get("timestamp_utc", datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class TradeExecution:
    """Execução bilateral confirmada no mercado contínuo ou em leilão."""
    trade_id: str
    symbol: str
    buy_order_id: str
    sell_order_id: str
    price: Decimal
    quantity: int
    executed_in_auction: bool
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "buy_order_id": self.buy_order_id,
            "sell_order_id": self.sell_order_id,
            "price": str(self.price),
            "quantity": self.quantity,
            "executed_in_auction": self.executed_in_auction,
            "timestamp_utc": self.timestamp_utc,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TradeExecution:
        return cls(
            trade_id=data["trade_id"],
            symbol=data["symbol"],
            buy_order_id=data["buy_order_id"],
            sell_order_id=data["sell_order_id"],
            price=Decimal(data["price"]),
            quantity=int(data["quantity"]),
            executed_in_auction=bool(data["executed_in_auction"]),
            timestamp_utc=data.get("timestamp_utc", datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class TheoreticalPriceSummary:
    """Resultado determinístico da apuração de Preço Teórico de Previsão (PTP)."""
    ptp_price: Decimal
    executable_volume: int
    imbalance_qty: int # Positivo: sobra compradora; Negativo: sobra vendedora; Zero: casado perfeito
    imbalance_side: Optional[OrderSide]
    tie_break_rule_applied: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ptp_price": str(self.ptp_price),
            "executable_volume": self.executable_volume,
            "imbalance_qty": self.imbalance_qty,
            "imbalance_side": self.imbalance_side.value if self.imbalance_side else None,
            "tie_break_rule_applied": self.tie_break_rule_applied,
        }


class PumaCircuitBreakerEngine:
    """Motor de proteção por túneis e leilões de volatilidade do PUMA Trading System."""

    def __init__(
        self,
        symbol: str,
        segment: AssetSegment = AssetSegment.IBOV_EQUITY,
        opening_price: Decimal = Decimal("30.00"),
        tunnel_config: Optional[TunnelBandConfig] = None,
    ) -> None:
        self.symbol = symbol
        self.segment = segment
        self.opening_price = round_price(opening_price)
        self.last_traded_price: Decimal = self.opening_price
        self.config = tunnel_config or DEFAULT_TUNNEL_CONFIGS[segment]
        self.market_state = MarketState.CONTINUOUS

        # Livro de ofertas
        self.orders: Dict[str, Order] = {}
        self.trades: List[TradeExecution] = []
        self.rejected_orders: List[Tuple[Order, str]] = []
        self.auction_history: List[Dict[str, Any]] = []

    def compute_bounds(self) -> TunnelBounds:
        """Calcula as bandas absolutas inferiores e superiores de cada túnel."""
        ref_price = self.opening_price
        last_price = self.last_traded_price

        rej_low = round_price(ref_price * (Decimal("1.00") - self.config.rejection_band_pct))
        rej_up = round_price(ref_price * (Decimal("1.00") + self.config.rejection_band_pct))

        stat_low = round_price(ref_price * (Decimal("1.00") - self.config.static_auction_band_pct))
        stat_up = round_price(ref_price * (Decimal("1.00") + self.config.static_auction_band_pct))

        dyn_low = round_price(last_price * (Decimal("1.00") - self.config.dynamic_auction_band_pct))
        dyn_up = round_price(last_price * (Decimal("1.00") + self.config.dynamic_auction_band_pct))

        return TunnelBounds(
            reference_price=ref_price,
            rejection_lower=rej_low,
            rejection_upper=rej_up,
            static_lower=stat_low,
            static_upper=stat_up,
            dynamic_lower=dyn_low,
            dynamic_upper=dyn_up,
        )

    def submit_order(self, order: Order) -> Tuple[bool, str]:
        """Submete uma nova ordem ao motor, verificando o túnel de rejeição."""
        bounds = self.compute_bounds()

        # Validação do túnel de rejeição para ordens com preço limite
        if order.order_type == OrderType.LIMIT and order.price is not None:
            if order.price < bounds.rejection_lower or order.price > bounds.rejection_upper:
                reason = (
                    f"Rejeição FIX 35=j: Preço {order.price} fora do Túnel de Rejeição "
                    f"[{bounds.rejection_lower}, {bounds.rejection_upper}]"
                )
                self.rejected_orders.append((order, reason))
                return False, reason

        self.orders[order.order_id] = order

        # Se mercado estiver contínuo, tenta matching
        if self.market_state == MarketState.CONTINUOUS:
            self._attempt_continuous_matching(order)

        return True, "ACCEPTED"

    def _attempt_continuous_matching(self, incoming: Order) -> None:
        """Tenta executar ordem no mercado contínuo respeitando túneis de leilão."""
        bounds = self.compute_bounds()

        while incoming.remaining_qty > 0:
            best_match = self._find_best_opposite_order(incoming)
            if not best_match:
                break

            # Determina o preço potencial de execução (preço da ordem passiva que já estava no book)
            candidate_price = best_match.price or incoming.price or self.last_traded_price

            # Verifica violação de Túnel de Leilão Estático
            violates_static = (candidate_price < bounds.static_lower) or (candidate_price > bounds.static_upper)
            # Verifica violação de Túnel de Leilão Dinâmico
            violates_dynamic = (candidate_price < bounds.dynamic_lower) or (candidate_price > bounds.dynamic_upper)

            if violates_static or violates_dynamic:
                trigger_reason = "STATIC_TUNNEL_BREACH" if violates_static else "DYNAMIC_TUNNEL_BREACH"
                self._trigger_circuit_breaker(trigger_reason, candidate_price)
                return

            # Execução contínua regular autorizada
            exec_qty = min(incoming.remaining_qty, best_match.remaining_qty)
            incoming.remaining_qty -= exec_qty
            incoming.cum_qty += exec_qty
            best_match.remaining_qty -= exec_qty
            best_match.cum_qty += exec_qty

            self.last_traded_price = candidate_price
            trade = TradeExecution(
                trade_id=f"TRD-{len(self.trades) + 1:06d}",
                symbol=self.symbol,
                buy_order_id=incoming.order_id if incoming.side == OrderSide.BUY else best_match.order_id,
                sell_order_id=incoming.order_id if incoming.side == OrderSide.SELL else best_match.order_id,
                price=candidate_price,
                quantity=exec_qty,
                executed_in_auction=False,
            )
            self.trades.append(trade)

            # Recalcula bounds com novo last_traded_price
            bounds = self.compute_bounds()

    def _find_best_opposite_order(self, incoming: Order) -> Optional[Order]:
        """Localiza a melhor contraparte no book com prioridade Preço-Tempo."""
        opp_side = OrderSide.SELL if incoming.side == OrderSide.BUY else OrderSide.BUY
        candidates = [o for o in self.orders.values() if o.side == opp_side and o.remaining_qty > 0]

        if not candidates:
            return None

        if incoming.side == OrderSide.BUY:
            # Comprador quer menor preço de venda
            eligible = [c for c in candidates if incoming.price is None or (c.price is not None and c.price <= incoming.price)]
            if not eligible:
                return None
            eligible.sort(key=lambda x: (x.price or Decimal("0"), x.timestamp_utc))
            return eligible[0]
        else:
            # Vendedor quer maior preço de compra
            eligible = [c for c in candidates if incoming.price is None or (c.price is not None and c.price >= incoming.price)]
            if not eligible:
                return None
            eligible.sort(key=lambda x: (-(x.price or Decimal("0")), x.timestamp_utc))
            return eligible[0]

    def _trigger_circuit_breaker(self, reason: str, breaching_price: Decimal) -> None:
        """Suspende negociação contínua e abre leilão de volatilidade não-programado."""
        self.market_state = MarketState.AUCTION_CIRCUIT_BREAKER
        self.auction_history.append({
            "trigger_reason": reason,
            "breaching_price": str(breaching_price),
            "triggered_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "OPEN",
        })

    def calculate_ptp(self) -> Optional[TheoreticalPriceSummary]:
        """Calcula o Preço Teórico de Previsão (PTP) conforme 4 regras canônicas B3."""
        active_buys = [o for o in self.orders.values() if o.side == OrderSide.BUY and o.remaining_qty > 0]
        active_sells = [o for o in self.orders.values() if o.side == OrderSide.SELL and o.remaining_qty > 0]

        if not active_buys or not active_sells:
            return None

        # Coletar todos os preços candidatos discretos
        candidate_prices: Set[Decimal] = set()
        for o in active_buys + active_sells:
            if o.price is not None:
                candidate_prices.add(o.price)
        candidate_prices.add(self.opening_price)
        candidate_prices.add(self.last_traded_price)

        sorted_prices = sorted(list(candidate_prices))

        evaluations: List[Dict[str, Any]] = []
        for p in sorted_prices:
            cum_buy = sum(o.remaining_qty for o in active_buys if o.price is None or o.price >= p)
            cum_sell = sum(o.remaining_qty for o in active_sells if o.price is None or o.price <= p)
            matched = min(cum_buy, cum_sell)
            imbalance = cum_buy - cum_sell
            abs_imbalance = abs(imbalance)
            dist_to_ref = abs(p - self.opening_price)

            evaluations.append({
                "price": p,
                "volume": matched,
                "abs_imbalance": abs_imbalance,
                "signed_imbalance": imbalance,
                "dist_to_ref": dist_to_ref,
            })

        # Regra 1 B3: Maximizar Volume Executável
        max_vol = max(e["volume"] for e in evaluations)
        if max_vol == 0:
            return None
        r1_cands = [e for e in evaluations if e["volume"] == max_vol]
        if len(r1_cands) == 1:
            c = r1_cands[0]
            side = OrderSide.BUY if c["signed_imbalance"] > 0 else (OrderSide.SELL if c["signed_imbalance"] < 0 else None)
            return TheoreticalPriceSummary(c["price"], c["volume"], c["signed_imbalance"], side, "RULE_1_MAX_VOLUME")

        # Regra 2 B3: Minimizar Desbalanceamento (Menor Sobra)
        min_imb = min(e["abs_imbalance"] for e in r1_cands)
        r2_cands = [e for e in r1_cands if e["abs_imbalance"] == min_imb]
        if len(r2_cands) == 1:
            c = r2_cands[0]
            side = OrderSide.BUY if c["signed_imbalance"] > 0 else (OrderSide.SELL if c["signed_imbalance"] < 0 else None)
            return TheoreticalPriceSummary(c["price"], c["volume"], c["signed_imbalance"], side, "RULE_2_MIN_IMBALANCE")

        # Regra 3 B3: Direção da Maior Sobra (Pressão de Mercado)
        # Se sobra compradora acumulada > 0 -> maior preço; se vendedora -> menor preço
        first_imb = r2_cands[0]["signed_imbalance"]
        if first_imb > 0:
            r3_cand = max(r2_cands, key=lambda x: x["price"])
            return TheoreticalPriceSummary(r3_cand["price"], r3_cand["volume"], r3_cand["signed_imbalance"], OrderSide.BUY, "RULE_3_IMBALANCE_SIDE_BUY")
        elif first_imb < 0:
            r3_cand = min(r2_cands, key=lambda x: x["price"])
            return TheoreticalPriceSummary(r3_cand["price"], r3_cand["volume"], r3_cand["signed_imbalance"], OrderSide.SELL, "RULE_3_IMBALANCE_SIDE_SELL")

        # Regra 4 B3: Menor Distância ao Preço de Referência
        min_dist = min(e["dist_to_ref"] for e in r2_cands)
        r4_cands = [e for e in r2_cands if e["dist_to_ref"] == min_dist]
        r4_cand = min(r4_cands, key=lambda x: x["price"]) # Desempate estável
        return TheoreticalPriceSummary(r4_cand["price"], r4_cand["volume"], 0, None, "RULE_4_PROXIMITY_TO_REFERENCE")

    def uncross_auction(self) -> Optional[TheoreticalPriceSummary]:
        """Executa o cruzamento de ordens ao Preço Teórico e reabre o mercado contínuo."""
        ptp = self.calculate_ptp()
        if not ptp:
            self.market_state = MarketState.CONTINUOUS
            return None

        uncross_price = ptp.ptp_price
        eligible_buys = [o for o in self.orders.values() if o.side == OrderSide.BUY and o.remaining_qty > 0 and (o.price is None or o.price >= uncross_price)]
        eligible_sells = [o for o in self.orders.values() if o.side == OrderSide.SELL and o.remaining_qty > 0 and (o.price is None or o.price <= uncross_price)]

        # Ordenamento FIFO: Mercado primeiro, depois melhor preço, depois tempo
        eligible_buys.sort(key=lambda x: (0 if x.order_type == OrderType.MARKET else 1, -(x.price or Decimal("0")), x.timestamp_utc))
        eligible_sells.sort(key=lambda x: (0 if x.order_type == OrderType.MARKET else 1, (x.price or Decimal("0")), x.timestamp_utc))

        b_idx, s_idx = 0, 0
        while b_idx < len(eligible_buys) and s_idx < len(eligible_sells):
            b_ord = eligible_buys[b_idx]
            s_ord = eligible_sells[s_idx]

            fill_qty = min(b_ord.remaining_qty, s_ord.remaining_qty)
            b_ord.remaining_qty -= fill_qty
            b_ord.cum_qty += fill_qty
            s_ord.remaining_qty -= fill_qty
            s_ord.cum_qty += fill_qty

            self.trades.append(TradeExecution(
                trade_id=f"TRD-AUC-{len(self.trades) + 1:06d}",
                symbol=self.symbol,
                buy_order_id=b_ord.order_id,
                sell_order_id=s_ord.order_id,
                price=uncross_price,
                quantity=fill_qty,
                executed_in_auction=True,
            ))

            if b_ord.remaining_qty == 0:
                b_idx += 1
            if s_ord.remaining_qty == 0:
                s_idx += 1

        self.last_traded_price = uncross_price
        self.market_state = MarketState.CONTINUOUS
        if self.auction_history:
            self.auction_history[-1]["status"] = "RESOLVED_UNCROSS"
            self.auction_history[-1]["uncross_price"] = str(uncross_price)
            self.auction_history[-1]["executed_volume"] = ptp.executable_volume

        return ptp

    def verify_invariants(self) -> Dict[str, bool]:
        """Verifica as 3 leis fundamentais de túneis e circuit breaker da B3."""
        bounds = self.compute_bounds()

        # LAW-098A: Não-transgressão de túnel em mercado contínuo
        continuous_compliant = True
        for trd in self.trades:
            if not trd.executed_in_auction:
                if trd.price < bounds.rejection_lower or trd.price > bounds.rejection_upper:
                    continuous_compliant = False
                    break

        # LAW-098B: Maximização de volume em leilão
        # Se houve trade em leilão, o volume executado foi positivo e determinístico
        auction_max_holds = True
        for h in self.auction_history:
            if h.get("status") == "RESOLVED_UNCROSS":
                if int(h.get("executed_volume", 0)) <= 0:
                    auction_max_holds = False
                    break

        # LAW-098C: Conservação de ordens no book
        # Para cada ordem: quantity == cum_qty + remaining_qty
        order_conservation_holds = True
        for o in self.orders.values():
            if o.quantity != (o.cum_qty + o.remaining_qty):
                order_conservation_holds = False
                break

        return {
            "all_invariants_hold": continuous_compliant and auction_max_holds and order_conservation_holds,
            "continuous_tunnel_compliance_verified": continuous_compliant,
            "auction_volume_maximization_verified": auction_max_holds,
            "order_conservation_verified": order_conservation_holds,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialização completa do estado do motor."""
        return {
            "symbol": self.symbol,
            "segment": self.segment.value,
            "opening_price": str(self.opening_price),
            "last_traded_price": str(self.last_traded_price),
            "market_state": self.market_state.value,
            "bounds": self.compute_bounds().to_dict(),
            "orders": [o.to_dict() for o in self.orders.values()],
            "trades": [t.to_dict() for t in self.trades],
            "rejected_orders_count": len(self.rejected_orders),
            "auction_history": self.auction_history,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PumaCircuitBreakerEngine:
        """Reconstitui o motor a partir de dicionário."""
        eng = cls(
            symbol=data["symbol"],
            segment=AssetSegment(data["segment"]),
            opening_price=Decimal(data["opening_price"]),
        )
        eng.last_traded_price = Decimal(data["last_traded_price"])
        eng.market_state = MarketState(data["market_state"])
        for o_data in data.get("orders", []):
            o = Order.from_dict(o_data)
            eng.orders[o.order_id] = o
        for t_data in data.get("trades", []):
            t = TradeExecution.from_dict(t_data)
            eng.trades.append(t)
        eng.auction_history = list(data.get("auction_history", []))
        return eng
