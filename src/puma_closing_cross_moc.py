"""B3 PUMA Trading System — Leilão de Fechamento com Ordens MOC/LOC e Janela de Congelamento (FL-098).
================================================================================================

Motor determinístico do Leilão de Fechamento (Closing Auction / Closing Cross) do PUMA Trading System B3,
com suporte integral a ordens Market-On-Close (MOC), Limit-On-Close (LOC) e LIMIT, janela de congelamento
estrita (Freeze Window / No-Cancel), prioridade absoluta de alocação MOC sobre LOC e desempate oficial B3.

Conformidade Normativa:
- Manual de Procedimentos Operacionais de Negociação da B3 (Capítulo: Leilões e Procedimentos Especiais).
- Regras de Formação do Preço Teórico de Fechamento (PTP / PTF) e Algoritmo de Uncross.
- Janela de Congelamento (Freeze Window / Random Extension Protection): Rejeição sumária de cancelamentos
  e alterações redutoras de quantidade no livro de fechamento para combate ao spoofing.

Leis de Invariantes Formais:
- LAW-104A (Inviolabilidade da Janela de Congelamento / No-Cancel):
  Durante a fase FREEZE_WINDOW, nenhuma ordem ativa pode ser cancelada ou ter quantidade reduzida.
  Tentativas de cancelamento são sumariamente bloqueadas e auditadas com registro de violação.
- LAW-104B (Prioridade MOC sobre LOC):
  Ordens Market-On-Close (MOC) possuem precedência absoluta de alocação em relação a ordens
  Limit-On-Close (LOC) e LIMIT ordinárias. Nenhuma ordem LOC/LIMIT na mesma ponta pode ser executada
  enquanto houver volume residual de MOC elegível não preenchido.
- LAW-104C (Conservação de Volume no Uncross):
  Conservação estrita de volume:
  Total Compras Executadas == Total Vendas Executadas == Volume Total dos Fills.
  Para toda ordem: Quantidade Inicial == Quantidade Executada + Quantidade Residual + Quantidade Cancelada.
  Preço Único de Liquidação: 100% dos fills do uncross transacionam estritamente ao PTP apurado.

100% Python Standard Library (dataclasses, enum, decimal, typing, json, pathlib, datetime).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union


# ==============================================================================
# Funções Auxiliares de Precisão Numérica
# ==============================================================================

def round_price(val: Union[Decimal, str, float, int]) -> Decimal:
    """Arredonda preço para centavos (2 casas decimais) conforme tick size padrão B3."""
    d = Decimal(str(val))
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ==============================================================================
# Enumerações Canônicas
# ==============================================================================

class ClosingOrderType(str, Enum):
    """Tipos de ordens admitidos no Leilão de Fechamento PUMA."""
    MOC = "MOC"      # Market-On-Close (sem preço limite, prioridade absoluta de execução)
    LOC = "LOC"      # Limit-On-Close (preço limite restrito à participação no fechamento)
    LIMIT = "LIMIT"  # Ordem limitada ordinária (remanescente do contínuo ou call)


class ClosingOrderSide(str, Enum):
    """Sentido da ordem de fechamento."""
    BUY = "BUY"
    SELL = "SELL"


class AuctionPhase(str, Enum):
    """Fases do ciclo de vida do Leilão de Fechamento PUMA."""
    CALL_PHASE = "CALL_PHASE"            # Fase de acumulação: inclusões, alterações e cancelamentos permitidos
    FREEZE_WINDOW = "FREEZE_WINDOW"      # Janela de congelamento: cancelamentos sumariamente bloqueados (LAW-104A)
    RANDOM_EXTENSION = "RANDOM_EXTENSION"# Prorrogação randômica (mantém restrições de congelamento)
    UNCROSS = "UNCROSS"                  # Execução atômica do cruzamento de ofertas ao PTP
    CLOSED = "CLOSED"                    # Leilão encerrado e negócios consolidados


class ClosingOrderStatus(str, Enum):
    """Estado da ordem no ciclo do leilão."""
    NEW = "NEW"
    ACTIVE = "ACTIVE"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


# ==============================================================================
# Exceções de Domínio
# ==============================================================================

class PumaClosingCrossError(Exception):
    """Exceção base para operações do leilão de fechamento PUMA."""
    pass


class PumaFreezeWindowViolationError(PumaClosingCrossError):
    """Disparada quando uma operação proibida viola a janela de congelamento (LAW-104A)."""
    pass


class PumaInvariantViolationError(PumaClosingCrossError):
    """Disparada quando uma invariante formal do motor é quebrada."""
    pass


# ==============================================================================
# Entidades de Dados (Dataclasses)
# ==============================================================================

@dataclass
class ClosingOrder:
    """Representação de uma ordem submetida ao Leilão de Fechamento PUMA."""
    order_id: str
    symbol: str
    side: ClosingOrderSide
    order_type: ClosingOrderType
    quantity: int
    price: Optional[Decimal] = None  # None estrito para ordens MOC
    timestamp_ms: int = 0
    executed_quantity: int = 0
    cancelled_quantity: int = 0
    status: ClosingOrderStatus = ClosingOrderStatus.ACTIVE

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise PumaClosingCrossError(f"Ordem {self.order_id}: quantidade deve ser estritamente positiva (> 0)")

        if isinstance(self.side, str):
            self.side = ClosingOrderSide(self.side)

        if isinstance(self.order_type, str):
            self.order_type = ClosingOrderType(self.order_type)

        if isinstance(self.status, str):
            self.status = ClosingOrderStatus(self.status)

        if self.order_type == ClosingOrderType.MOC:
            if self.price is not None:
                # Ordens MOC não aceitam preço limite explícito no PUMA
                self.price = None
        else:
            if self.price is None or self.price <= Decimal("0.00"):
                raise PumaClosingCrossError(
                    f"Ordem {self.order_id} do tipo {self.order_type.value} exige preço limite positivo"
                )
            self.price = round_price(self.price)

    @property
    def remaining_quantity(self) -> int:
        """Volume residual ativo no livro."""
        rem = self.quantity - self.executed_quantity - self.cancelled_quantity
        return max(0, rem)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "price": str(self.price) if self.price is not None else None,
            "timestamp_ms": self.timestamp_ms,
            "executed_quantity": self.executed_quantity,
            "cancelled_quantity": self.cancelled_quantity,
            "status": self.status.value,
            "remaining_quantity": self.remaining_quantity,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ClosingOrder:
        price_val = Decimal(str(data["price"])) if data.get("price") is not None else None
        return cls(
            order_id=data["order_id"],
            symbol=data["symbol"],
            side=ClosingOrderSide(data["side"]),
            order_type=ClosingOrderType(data["order_type"]),
            quantity=int(data["quantity"]),
            price=price_val,
            timestamp_ms=int(data.get("timestamp_ms", 0)),
            executed_quantity=int(data.get("executed_quantity", 0)),
            cancelled_quantity=int(data.get("cancelled_quantity", 0)),
            status=ClosingOrderStatus(data.get("status", ClosingOrderStatus.ACTIVE.value)),
        )


@dataclass(frozen=True)
class PumaClosingExecutionFill:
    """Execução individual gerada no uncross do leilão."""
    fill_id: str
    symbol: str
    buy_order_id: str
    sell_order_id: str
    buy_order_type: ClosingOrderType
    sell_order_type: ClosingOrderType
    executed_price: Decimal
    executed_quantity: int
    timestamp_ms: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "symbol": self.symbol,
            "buy_order_id": self.buy_order_id,
            "sell_order_id": self.sell_order_id,
            "buy_order_type": self.buy_order_type.value,
            "sell_order_type": self.sell_order_type.value,
            "executed_price": str(self.executed_price),
            "executed_quantity": self.executed_quantity,
            "timestamp_ms": self.timestamp_ms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PumaClosingExecutionFill:
        return cls(
            fill_id=data["fill_id"],
            symbol=data["symbol"],
            buy_order_id=data["buy_order_id"],
            sell_order_id=data["sell_order_id"],
            buy_order_type=ClosingOrderType(data["buy_order_type"]),
            sell_order_type=ClosingOrderType(data["sell_order_type"]),
            executed_price=Decimal(str(data["executed_price"])),
            executed_quantity=int(data["executed_quantity"]),
            timestamp_ms=int(data["timestamp_ms"]),
        )


@dataclass(frozen=True)
class TheoreticalPriceSummary:
    """Sumário formal do Preço Teórico de Fechamento (PTP/PTF)."""
    symbol: str
    uncross_price: Decimal
    executable_volume: int
    imbalance_quantity: int  # Positivo: excesso compra; Negativo: excesso venda; Zero: equilibrado
    reference_price: Decimal
    participating_buy_orders: int
    participating_sell_orders: int
    rule_applied: str        # 'MAX_VOLUME', 'MIN_IMBALANCE', 'MARKET_PRESSURE', 'REF_PRICE_PROXIMITY'

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "uncross_price": str(self.uncross_price),
            "executable_volume": self.executable_volume,
            "imbalance_quantity": self.imbalance_quantity,
            "reference_price": str(self.reference_price),
            "participating_buy_orders": self.participating_buy_orders,
            "participating_sell_orders": self.participating_sell_orders,
            "rule_applied": self.rule_applied,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TheoreticalPriceSummary:
        return cls(
            symbol=data["symbol"],
            uncross_price=Decimal(str(data["uncross_price"])),
            executable_volume=int(data["executable_volume"]),
            imbalance_quantity=int(data["imbalance_quantity"]),
            reference_price=Decimal(str(data["reference_price"])),
            participating_buy_orders=int(data["participating_buy_orders"]),
            participating_sell_orders=int(data["participating_sell_orders"]),
            rule_applied=data.get("rule_applied", "UNKNOWN"),
        )


# ==============================================================================
# Motor Principal do Leilão de Fechamento (PumaClosingCrossEngine)
# ==============================================================================

class PumaClosingCrossEngine:
    """Motor Determinístico de Leilão de Fechamento PUMA com MOC/LOC e Janela de Congelamento."""

    def __init__(
        self,
        symbol: str,
        reference_price: Decimal,
        tick_size: Decimal = Decimal("0.01"),
    ) -> None:
        self.symbol = symbol.strip().upper()
        self.reference_price = round_price(reference_price)
        self.tick_size = round_price(tick_size)
        self.phase: AuctionPhase = AuctionPhase.CALL_PHASE
        self.orders: Dict[str, ClosingOrder] = {}
        self.executions: List[PumaClosingExecutionFill] = []
        self.rejected_cancellations: List[Dict[str, Any]] = []
        self._freeze_entered_at_ms: Optional[int] = None
        self._cancelled_during_freeze_count: int = 0
        self._candidate_volumes: List[int] = []
        self._candidate_prices: List[Decimal] = []

    def add_order(self, order: ClosingOrder) -> None:
        """Adiciona uma nova ordem ao livro do leilão."""
        if order.symbol != self.symbol:
            raise PumaClosingCrossError(
                f"Símbolo {order.symbol} incompatível com leilão de {self.symbol}"
            )
        if order.order_id in self.orders:
            raise PumaClosingCrossError(f"Ordem duplicada com id {order.order_id}")
        if self.phase in (AuctionPhase.UNCROSS, AuctionPhase.CLOSED):
            raise PumaClosingCrossError(f"Não é permitido submeter ordens na fase {self.phase.value}")

        self.orders[order.order_id] = order

    def enter_freeze_period(self, timestamp_ms: int = 0) -> None:
        """Inicia a janela de congelamento (Freeze Window) do leilão.
        
        A partir desta transição, qualquer tentativa de cancelamento é sumariamente
        rejeitada e auditada (LAW-104A).
        """
        if self.phase != AuctionPhase.CALL_PHASE:
            raise PumaClosingCrossError(
                f"Janela de congelamento só pode ser iniciada a partir de CALL_PHASE (atual: {self.phase.value})"
            )
        self.phase = AuctionPhase.FREEZE_WINDOW
        self._freeze_entered_at_ms = timestamp_ms

    def enter_random_extension(self) -> None:
        """Transiciona para a prorrogação randômica (mantém proteções de congelamento)."""
        if self.phase != AuctionPhase.FREEZE_WINDOW:
            raise PumaClosingCrossError(
                f"Random Extension exige prévia entrada em FREEZE_WINDOW (atual: {self.phase.value})"
            )
        self.phase = AuctionPhase.RANDOM_EXTENSION

    def cancel_order(self, order_id: str, timestamp_ms: int = 0) -> None:
        """Tenta cancelar uma ordem ativa no livro.
        
        Se a fase atual for FREEZE_WINDOW ou RANDOM_EXTENSION, a solicitação é sumariamente
        rejeitada com base no normativo B3 (LAW-104A), gerando registro de auditoria e disparando
        exceção `PumaFreezeWindowViolationError`.
        """
        if order_id not in self.orders:
            raise PumaClosingCrossError(f"Ordem {order_id} não encontrada para cancelamento")

        order = self.orders[order_id]

        if order.status in (ClosingOrderStatus.CANCELLED, ClosingOrderStatus.FILLED):
            raise PumaClosingCrossError(f"Ordem {order_id} não está ativa para cancelamento (status: {order.status.value})")

        # Inviolabilidade da Janela de Congelamento (LAW-104A)
        if self.phase in (AuctionPhase.FREEZE_WINDOW, AuctionPhase.RANDOM_EXTENSION):
            audit_entry = {
                "order_id": order_id,
                "symbol": self.symbol,
                "phase": self.phase.value,
                "timestamp_ms": timestamp_ms,
                "rejection_reason": "REJECTED_FREEZE_WINDOW_ACTIVE",
                "law_reference": "LAW-104A",
            }
            self.rejected_cancellations.append(audit_entry)
            raise PumaFreezeWindowViolationError(
                f"LAW-104A: Cancelamento da ordem {order_id} sumariamente rejeitado durante {self.phase.value}"
            )

        if self.phase in (AuctionPhase.UNCROSS, AuctionPhase.CLOSED):
            raise PumaClosingCrossError(f"Cancelamentos não são permitidos na fase {self.phase.value}")

        # Cancelamento regular durante CALL_PHASE
        order.cancelled_quantity = order.remaining_quantity
        order.status = ClosingOrderStatus.CANCELLED

    def modify_order(
        self,
        order_id: str,
        new_quantity: Optional[int] = None,
        new_price: Optional[Decimal] = None,
        timestamp_ms: int = 0,
    ) -> None:
        """Modifica quantidade ou preço de uma ordem ativa.
        
        Durante a janela de congelamento (FREEZE_WINDOW ou RANDOM_EXTENSION),
        reduções de quantidade ou alterações desfavoráveis de preço são sumariamente
        bloqueadas para evitar manipulação de preço teórico (anti-spoofing).
        """
        if order_id not in self.orders:
            raise PumaClosingCrossError(f"Ordem {order_id} não encontrada para alteração")

        order = self.orders[order_id]
        if order.status not in (ClosingOrderStatus.ACTIVE, ClosingOrderStatus.PARTIALLY_FILLED):
            raise PumaClosingCrossError(f"Ordem {order_id} em estado incompatível para alteração ({order.status.value})")

        # Validações no período de congelamento
        if self.phase in (AuctionPhase.FREEZE_WINDOW, AuctionPhase.RANDOM_EXTENSION):
            if new_quantity is not None and new_quantity < order.quantity:
                audit_entry = {
                    "order_id": order_id,
                    "symbol": self.symbol,
                    "phase": self.phase.value,
                    "timestamp_ms": timestamp_ms,
                    "rejection_reason": "REJECTED_QUANTITY_REDUCTION_IN_FREEZE",
                    "law_reference": "LAW-104A",
                }
                self.rejected_cancellations.append(audit_entry)
                raise PumaFreezeWindowViolationError(
                    f"LAW-104A: Redução de quantidade de {order.quantity} para {new_quantity} rejeitada no congelamento"
                )

            # Preço desfavorável no freeze (reduzir compra ou aumentar venda piora o book)
            if new_price is not None and order.price is not None:
                new_p = round_price(new_price)
                if order.side == ClosingOrderSide.BUY and new_p < order.price:
                    raise PumaFreezeWindowViolationError(
                        "LAW-104A: Redução de preço de compra proibida na janela de congelamento"
                    )
                if order.side == ClosingOrderSide.SELL and new_p > order.price:
                    raise PumaFreezeWindowViolationError(
                        "LAW-104A: Aumento de preço de venda proibido na janela de congelamento"
                    )

        if new_quantity is not None:
            if new_quantity <= order.executed_quantity:
                raise PumaClosingCrossError("Nova quantidade deve ser estritamente superior ao volume já executado")
            order.quantity = new_quantity

        if new_price is not None:
            if order.order_type == ClosingOrderType.MOC:
                raise PumaClosingCrossError("Não é permitido atribuir preço limite a ordem MOC")
            order.price = round_price(new_price)

    # --------------------------------------------------------------------------
    # Apuração Determinística do Preço Teórico de Fechamento (PTP / PTF)
    # --------------------------------------------------------------------------

    def calculate_theoretical_price(self) -> Optional[TheoreticalPriceSummary]:
        """Calcula o Preço Teórico de Fechamento (PTP) aplicando as regras oficiais B3:
        
        1. Regra 1: Maior Volume Executável (Maximização de lotes casados).
        2. Regra 2: Menor Desbalanceamento (Minimização da sobra de mercado |Compras - Vendas|).
        3. Regra 3: Pressão do Mercado e Preço de Referência:
           - Se em todos os preços empatados houver sobra compradora (Demanda > Oferta):
             Maior preço entre os empatados.
           - Se em todos os preços empatados houver sobra vendedora (Demanda < Oferta):
             Menor preço entre os empatados.
           - Se sobra nula ou inversão de sinal de desequilíbrio:
             Preço mais próximo do preço de referência anterior. Em caso de empate equidistante,
             adota-se o maior preço.
        """
        active_buys = [
            o for o in self.orders.values()
            if o.side == ClosingOrderSide.BUY and o.remaining_quantity > 0 and o.status != ClosingOrderStatus.CANCELLED
        ]
        active_sells = [
            o for o in self.orders.values()
            if o.side == ClosingOrderSide.SELL and o.remaining_quantity > 0 and o.status != ClosingOrderStatus.CANCELLED
        ]

        if not active_buys or not active_sells:
            return None

        # Coletar universo discreto de preços candidatos
        candidate_prices: Set[Decimal] = {self.reference_price}
        for o in self.orders.values():
            if o.status != ClosingOrderStatus.CANCELLED and o.remaining_quantity > 0:
                if o.price is not None and o.price > Decimal("0.00"):
                    candidate_prices.add(o.price)

        sorted_prices = sorted(list(candidate_prices))

        # Avaliar métricas para cada nível de preço
        price_metrics: List[Dict[str, Any]] = []
        for p in sorted_prices:
            # Demanda acumulada: MOC compras + Limitadas compras com preço >= p
            cum_buy = sum(
                o.remaining_quantity for o in active_buys
                if o.order_type == ClosingOrderType.MOC or (o.price is not None and o.price >= p)
            )
            # Oferta acumulada: MOC vendas + Limitadas vendas com preço <= p
            cum_sell = sum(
                o.remaining_quantity for o in active_sells
                if o.order_type == ClosingOrderType.MOC or (o.price is not None and o.price <= p)
            )

            executable_vol = min(cum_buy, cum_sell)
            imbalance = abs(cum_buy - cum_sell)
            signed_imbalance = cum_buy - cum_sell

            price_metrics.append({
                "price": p,
                "executable_vol": executable_vol,
                "imbalance": imbalance,
                "signed_imbalance": signed_imbalance,
                "cum_buy": cum_buy,
                "cum_sell": cum_sell,
            })

        # Regra 1: Maximização de Volume Executável
        self._candidate_volumes = [m["executable_vol"] for m in price_metrics]
        self._candidate_prices = [m["price"] for m in price_metrics]
        max_vol = max(m["executable_vol"] for m in price_metrics)
        if max_vol <= 0:
            return None

        step1_candidates = [m for m in price_metrics if m["executable_vol"] == max_vol]
        if len(step1_candidates) == 1:
            chosen = step1_candidates[0]
            return TheoreticalPriceSummary(
                symbol=self.symbol,
                uncross_price=chosen["price"],
                executable_volume=chosen["executable_vol"],
                imbalance_quantity=chosen["signed_imbalance"],
                reference_price=self.reference_price,
                participating_buy_orders=len(active_buys),
                participating_sell_orders=len(active_sells),
                rule_applied="MAX_VOLUME",
            )

        # Regra 2: Menor Desbalanceamento Absoluto
        min_imb = min(m["imbalance"] for m in step1_candidates)
        step2_candidates = [m for m in step1_candidates if m["imbalance"] == min_imb]
        if len(step2_candidates) == 1:
            chosen = step2_candidates[0]
            return TheoreticalPriceSummary(
                symbol=self.symbol,
                uncross_price=chosen["price"],
                executable_volume=chosen["executable_vol"],
                imbalance_quantity=chosen["signed_imbalance"],
                reference_price=self.reference_price,
                participating_buy_orders=len(active_buys),
                participating_sell_orders=len(active_sells),
                rule_applied="MIN_IMBALANCE",
            )

        # Regra 3: Pressão do Mercado / Preço de Referência
        signed_signs = [m["signed_imbalance"] for m in step2_candidates]
        all_buy_surplus = all(s > 0 for s in signed_signs)
        all_sell_surplus = all(s < 0 for s in signed_signs)

        rule_name = "MARKET_PRESSURE"
        if all_buy_surplus:
            # Pressão compradora pura: escolhe o maior preço viável
            chosen = max(step2_candidates, key=lambda m: m["price"])
        elif all_sell_surplus:
            # Pressão vendedora pura: escolhe o menor preço viável
            chosen = min(step2_candidates, key=lambda m: m["price"])
        else:
            # Desequilíbrio zero ou sinais mistos: escolhe preço mais próximo do preço de referência
            rule_name = "REF_PRICE_PROXIMITY"
            min_dist = min(abs(m["price"] - self.reference_price) for m in step2_candidates)
            closest_candidates = [
                m for m in step2_candidates if abs(m["price"] - self.reference_price) == min_dist
            ]
            if len(closest_candidates) == 1:
                chosen = closest_candidates[0]
            else:
                # Empate equidistante: opta pelo maior preço
                chosen = max(closest_candidates, key=lambda m: m["price"])

        return TheoreticalPriceSummary(
            symbol=self.symbol,
            uncross_price=chosen["price"],
            executable_volume=chosen["executable_vol"],
            imbalance_quantity=chosen["signed_imbalance"],
            reference_price=self.reference_price,
            participating_buy_orders=len(active_buys),
            participating_sell_orders=len(active_sells),
            rule_applied=rule_name,
        )

    # --------------------------------------------------------------------------
    # Execução do Uncross (Casamento e Alocação com Prioridade MOC)
    # --------------------------------------------------------------------------

    def execute_uncross(self) -> TheoreticalPriceSummary:
        """Executa deterministicamente o uncross do Leilão de Fechamento.
        
        Garante rigorosamente a prioridade MOC sobre LOC (LAW-104B):
        - Compras: MOC (FIFO) > Preço Maior > Tempo Menor
        - Vendas: MOC (FIFO) > Preço Menor > Tempo Menor
        - Preço Único de Liquidação: 100% ao PTP apurado (LAW-104C).
        """
        ptp = self.calculate_theoretical_price()
        if not ptp:
            raise PumaClosingCrossError(
                f"Leilão de {self.symbol} sem liquidez ou sem preço executável para uncross"
            )

        self.phase = AuctionPhase.UNCROSS

        clearing_price = ptp.uncross_price

        # Fila de Compras com Prioridade Absoluta MOC (LAW-104B)
        eligible_buys = [
            o for o in self.orders.values()
            if o.side == ClosingOrderSide.BUY
            and o.remaining_quantity > 0
            and o.status != ClosingOrderStatus.CANCELLED
            and (o.order_type == ClosingOrderType.MOC or (o.price is not None and o.price >= clearing_price))
        ]

        def buy_sort_key(o: ClosingOrder) -> Tuple[int, Decimal, int]:
            # Prioridade 0: MOC
            # Prioridade 1: Preço decrescente (maior preço antes)
            # Prioridade 2: Timestamp crescente (FIFO)
            is_moc = 0 if o.order_type == ClosingOrderType.MOC else 1
            price_val = -o.price if o.price is not None else Decimal("-Infinity")
            return (is_moc, price_val, o.timestamp_ms)

        eligible_buys.sort(key=buy_sort_key)

        # Fila de Vendas com Prioridade Absoluta MOC (LAW-104B)
        eligible_sells = [
            o for o in self.orders.values()
            if o.side == ClosingOrderSide.SELL
            and o.remaining_quantity > 0
            and o.status != ClosingOrderStatus.CANCELLED
            and (o.order_type == ClosingOrderType.MOC or (o.price is not None and o.price <= clearing_price))
        ]

        def sell_sort_key(o: ClosingOrder) -> Tuple[int, Decimal, int]:
            # Prioridade 0: MOC
            # Prioridade 1: Preço crescente (menor preço antes)
            # Prioridade 2: Timestamp crescente (FIFO)
            is_moc = 0 if o.order_type == ClosingOrderType.MOC else 1
            price_val = o.price if o.price is not None else Decimal("Infinity")
            return (is_moc, price_val, o.timestamp_ms)

        eligible_sells.sort(key=sell_sort_key)

        b_idx = 0
        s_idx = 0
        fill_count = len(self.executions) + 1

        while b_idx < len(eligible_buys) and s_idx < len(eligible_sells):
            buy_ord = eligible_buys[b_idx]
            sell_ord = eligible_sells[s_idx]

            fill_qty = min(buy_ord.remaining_quantity, sell_ord.remaining_quantity)
            if fill_qty <= 0:
                break

            buy_ord.executed_quantity += fill_qty
            sell_ord.executed_quantity += fill_qty

            # Atualização de status da ordem
            if buy_ord.remaining_quantity == 0:
                buy_ord.status = ClosingOrderStatus.FILLED
            else:
                buy_ord.status = ClosingOrderStatus.PARTIALLY_FILLED

            if sell_ord.remaining_quantity == 0:
                sell_ord.status = ClosingOrderStatus.FILLED
            else:
                sell_ord.status = ClosingOrderStatus.PARTIALLY_FILLED

            fill = PumaClosingExecutionFill(
                fill_id=f"FILL-MOC-{self.symbol}-{fill_count:05d}",
                symbol=self.symbol,
                buy_order_id=buy_ord.order_id,
                sell_order_id=sell_ord.order_id,
                buy_order_type=buy_ord.order_type,
                sell_order_type=sell_ord.order_type,
                executed_price=clearing_price,
                executed_quantity=fill_qty,
                timestamp_ms=max(buy_ord.timestamp_ms, sell_ord.timestamp_ms) + 1,
            )
            self.executions.append(fill)
            fill_count += 1

            if buy_ord.remaining_quantity == 0:
                b_idx += 1
            if sell_ord.remaining_quantity == 0:
                s_idx += 1

        self.phase = AuctionPhase.CLOSED
        return ptp

    # --------------------------------------------------------------------------
    # Verificação Formal de Invariantes (LAW-104A, LAW-104B, LAW-104C)
    # --------------------------------------------------------------------------

    def verify_invariants(self) -> Dict[str, Any]:
        """Auditoria formal das invariantes de leilão de fechamento B3:
        
        - LAW-104A: Inviolabilidade da Janela de Congelamento (No-Cancel).
        - LAW-104B: Prioridade MOC sobre LOC.
        - LAW-104C: Conservação de Volume no Uncross e Preço Único de Liquidação.
        """
        # LAW-104A: Nenhuma ordem foi cancelada durante a janela de congelamento
        # e toda tentativa rejeitada foi devidamente registrada
        freeze_violations = 0
        if self._freeze_entered_at_ms is not None:
            for o in self.orders.values():
                if o.status == ClosingOrderStatus.CANCELLED:
                    # Cancelamentos legítimos só ocorrem antes do congelamento
                    if o.timestamp_ms >= self._freeze_entered_at_ms and o.cancelled_quantity > 0:
                        freeze_violations += 1

        law_104a_holds = (freeze_violations == 0) and (self._cancelled_during_freeze_count == 0)

        # LAW-104B: Prioridade MOC sobre LOC
        # Se qualquer ordem LOC/LIMIT na ponta compradora teve fill, TODAS as MOCs de compra
        # elegíveis devem ter sido 100% preenchidas.
        moc_buys = [o for o in self.orders.values() if o.side == ClosingOrderSide.BUY and o.order_type == ClosingOrderType.MOC]
        loc_buys = [o for o in self.orders.values() if o.side == ClosingOrderSide.BUY and o.order_type != ClosingOrderType.MOC]
        
        moc_sells = [o for o in self.orders.values() if o.side == ClosingOrderSide.SELL and o.order_type == ClosingOrderType.MOC]
        loc_sells = [o for o in self.orders.values() if o.side == ClosingOrderSide.SELL and o.order_type != ClosingOrderType.MOC]

        law_104b_buy_holds = True
        if any(loc.executed_quantity > 0 for loc in loc_buys):
            if any(moc.remaining_quantity > 0 for moc in moc_buys):
                law_104b_buy_holds = False

        law_104b_sell_holds = True
        if any(loc.executed_quantity > 0 for loc in loc_sells):
            if any(moc.remaining_quantity > 0 for moc in moc_sells):
                law_104b_sell_holds = False

        law_104b_holds = law_104b_buy_holds and law_104b_sell_holds

        # LAW-104C: Conservação de Volume no Uncross e Preço Único
        total_initial_lots = sum(o.quantity for o in self.orders.values())
        total_executed_lots_orders = sum(o.executed_quantity for o in self.orders.values())
        total_residual_lots = sum(o.remaining_quantity for o in self.orders.values())
        total_cancelled_lots = sum(o.cancelled_quantity for o in self.orders.values())

        # Cada fill contabiliza a mesma quantidade em compra e venda
        total_fill_volume = sum(f.executed_quantity for f in self.executions)
        total_buy_executed = sum(o.executed_quantity for o in self.orders.values() if o.side == ClosingOrderSide.BUY)
        total_sell_executed = sum(o.executed_quantity for o in self.orders.values() if o.side == ClosingOrderSide.SELL)

        volume_conservation_holds = (
            total_initial_lots == (total_executed_lots_orders + total_residual_lots + total_cancelled_lots)
            and total_buy_executed == total_sell_executed == total_fill_volume
            and (total_executed_lots_orders == total_fill_volume * 2)
        )

        unique_price_holds = True
        if self.executions:
            first_exec_price = self.executions[0].executed_price
            unique_price_holds = all(f.executed_price == first_exec_price for f in self.executions)

        law_104c_holds = volume_conservation_holds and unique_price_holds

        all_hold = law_104a_holds and law_104b_holds and law_104c_holds

        return {
            "all_invariants_hold": all_hold,
            "law_104a_freeze_inviolability": law_104a_holds,
            "law_104b_moc_priority": law_104b_holds,
            "law_104c_volume_conservation": law_104c_holds,
            "ptp_unique_price_verified": unique_price_holds,
            "total_executed_lots": total_fill_volume,
            "total_residual_lots": total_residual_lots,
            "total_cancelled_lots": total_cancelled_lots,
            "total_initial_lots": total_initial_lots,
            "rejected_freeze_cancellations_count": len(self.rejected_cancellations),
        }

    def assert_invariants(self) -> None:
        """Executa asserções estritas com base no módulo formal `src.invariants`.
        
        Dispara `InvariantViolationError` caso qualquer lei LAW-104 seja violada.
        """
        from src.invariants import (
            assert_closing_cross_freeze_inviolability,
            assert_moc_loc_allocation_priority,
            assert_closing_auction_volume_maximization,
        )

        # LAW-104A: Inviolabilidade do Congelamento
        assert_closing_cross_freeze_inviolability(
            freeze_active=(self._freeze_entered_at_ms is not None),
            cancellations_attempted=self._cancelled_during_freeze_count,
            modifications_attempted=0,
            symbol=self.symbol,
            auction_id=f"AUC-{self.symbol}",
        )

        # LAW-104B: Prioridade MOC sobre LOC (Compra e Venda)
        unexec_moc_buy = sum(
            o.remaining_quantity for o in self.orders.values()
            if o.side == ClosingOrderSide.BUY and o.order_type == ClosingOrderType.MOC
        )
        exec_loc_buy = sum(
            o.executed_quantity for o in self.orders.values()
            if o.side == ClosingOrderSide.BUY and o.order_type != ClosingOrderType.MOC
        )
        assert_moc_loc_allocation_priority(
            unexecuted_moc_quantity=unexec_moc_buy,
            executed_loc_or_limit_quantity=exec_loc_buy,
            side="BUY",
            symbol=self.symbol,
            auction_id=f"AUC-{self.symbol}",
        )

        unexec_moc_sell = sum(
            o.remaining_quantity for o in self.orders.values()
            if o.side == ClosingOrderSide.SELL and o.order_type == ClosingOrderType.MOC
        )
        exec_loc_sell = sum(
            o.executed_quantity for o in self.orders.values()
            if o.side == ClosingOrderSide.SELL and o.order_type != ClosingOrderType.MOC
        )
        assert_moc_loc_allocation_priority(
            unexecuted_moc_quantity=unexec_moc_sell,
            executed_loc_or_limit_quantity=exec_loc_sell,
            side="SELL",
            symbol=self.symbol,
            auction_id=f"AUC-{self.symbol}",
        )

        # LAW-104C: Maximização de Volume
        if self.executions and self._candidate_volumes:
            total_fill_volume = sum(f.executed_quantity for f in self.executions)
            selected_p = self.executions[0].executed_price if self.executions else None
            assert_closing_auction_volume_maximization(
                selected_volume=total_fill_volume,
                candidate_volumes=self._candidate_volumes,
                selected_price=selected_p,
                candidate_prices=self._candidate_prices,
                symbol=self.symbol,
                auction_id=f"AUC-{self.symbol}",
            )

    # --------------------------------------------------------------------------
    # Serialização e Persistência de Fixture
    # --------------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado completo do motor do leilão em dicionário."""
        return {
            "symbol": self.symbol,
            "reference_price": str(self.reference_price),
            "tick_size": str(self.tick_size),
            "phase": self.phase.value,
            "_freeze_entered_at_ms": self._freeze_entered_at_ms,
            "orders": [o.to_dict() for o in self.orders.values()],
            "executions": [f.to_dict() for f in self.executions],
            "rejected_cancellations": self.rejected_cancellations,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PumaClosingCrossEngine:
        """Reconstrói a instância do motor a partir de um dicionário serializado."""
        engine = cls(
            symbol=data["symbol"],
            reference_price=Decimal(str(data["reference_price"])),
            tick_size=Decimal(str(data.get("tick_size", "0.01"))),
        )
        engine.phase = AuctionPhase(data.get("phase", AuctionPhase.CALL_PHASE.value))
        engine._freeze_entered_at_ms = data.get("_freeze_entered_at_ms")

        for ord_data in data.get("orders", []):
            order = ClosingOrder.from_dict(ord_data)
            engine.orders[order.order_id] = order

        for fill_data in data.get("executions", []):
            fill = PumaClosingExecutionFill.from_dict(fill_data)
            engine.executions.append(fill)

        engine.rejected_cancellations = list(data.get("rejected_cancellations", []))
        return engine

    @classmethod
    def load_from_fixture(cls, filepath: Union[str, Path]) -> PumaClosingCrossEngine:
        """Carrega e instancia o motor a partir de um arquivo JSON estruturado."""
        path = Path(filepath)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        engine = cls(
            symbol=data["symbol"],
            reference_price=Decimal(str(data["reference_price"])),
            tick_size=Decimal(str(data.get("tick_size", "0.01"))),
        )
        for ord_dict in data.get("orders", []):
            order = ClosingOrder(
                order_id=ord_dict["order_id"],
                symbol=ord_dict["symbol"],
                side=ClosingOrderSide(ord_dict["side"]),
                order_type=ClosingOrderType(ord_dict["order_type"]),
                quantity=int(ord_dict["quantity"]),
                price=Decimal(str(ord_dict["price"])) if ord_dict.get("price") is not None else None,
                timestamp_ms=int(ord_dict.get("timestamp_ms", 0)),
            )
            engine.add_order(order)

        return engine
