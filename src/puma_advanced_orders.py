"""Módulo de Gestão e Execução de Ordens Avançadas no PUMA Trading System B3 (FL-056).

Implementa a especificação técnica de tipos especiais e algorítmicos de ordens
suportados nativamente pelo motor de negociação (Matching Engine) e algoritmos PUMA da B3:
1. Ordem Iceberg (Reserva Oculta / Tag 1138 DisplayQty)
2. Ordem Stop-Loss (Gatilho a Mercado / Tag 40 STOP)
3. Ordem Stop-Limit (Gatilho Limitado / Tag 40 STOP_LIMIT)
4. Ordem Trailing Stop (Gatilho Móvel Dinâmico)

Garante invariantes formais de conservação de quantidades financeiras:
- Quantidade_Executada + Quantidade_Oculta_Restante == Quantidade_Total
- Quantidade_Executada + Quantidade_Visivel + Quantidade_Oculta == Quantidade_Total
- Renovação de tranche com perda determinística de prioridade temporal.

100% Python Standard Library (dataclasses, Decimal, datetime, enum, json).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# ==============================================================================
# 1. Enumerações Canônicas
# ==============================================================================

class OrderSide(str, Enum):
    """Sentido da ordem no PUMA Trading System (FIX Tag 54)."""
    BUY = "BUY"    # Tag 54=1
    SELL = "SELL"  # Tag 54=2

    @classmethod
    def from_str(cls, value: Union[str, OrderSide]) -> OrderSide:
        if isinstance(value, cls):
            return value
        val = str(value).strip().upper()
        if "." in val:
            val = val.split(".")[-1]
        if val in ("BUY", "1", "C", "COMPRA"):
            return cls.BUY
        if val in ("SELL", "2", "V", "VENDA"):
            return cls.SELL
        raise ValueError(f"Sentido de ordem inválido: {value}")


class AdvancedOrderType(str, Enum):
    """Tipos especiais de ordens avançadas do PUMA (FIX Tag 40 / ExecInst)."""
    ICEBERG = "ICEBERG"              # Reserva Oculta (Tag 1138 DisplayQty)
    STOP_LOSS = "STOP_LOSS"          # Gatilho a Mercado (Tag 40=3 STOP)
    STOP_LIMIT = "STOP_LIMIT"        # Gatilho Limitado (Tag 40=4 STOP_LIMIT)
    TRAILING_STOP = "TRAILING_STOP"  # Gatilho Móvel Dinâmico (Ratchet Algorithm)

    @classmethod
    def from_str(cls, value: Union[str, AdvancedOrderType]) -> AdvancedOrderType:
        if isinstance(value, cls):
            return value
        val = str(value).strip().upper()
        if "." in val:
            val = val.split(".")[-1]
        if val in ("ICEBERG", "ICE", "RESERVE"):
            return cls.ICEBERG
        if val in ("STOP_LOSS", "STOP", "3"):
            return cls.STOP_LOSS
        if val in ("STOP_LIMIT", "4"):
            return cls.STOP_LIMIT
        if val in ("TRAILING_STOP", "TRAILING", "TS", "T"):
            return cls.TRAILING_STOP
        raise ValueError(f"Tipo de ordem avançada inválido: {value}")


class OrderTriggerStatus(str, Enum):
    """Estado do gatilho e ciclo de vida da ordem no livro PUMA."""
    PENDING_TRIGGER = "PENDING_TRIGGER"  # Dormente no livro de gatilhos (aguardando condição)
    TRIGGERED = "TRIGGERED"              # Gatilho ativado pelo Last Traded Price
    ACTIVE = "ACTIVE"                    # Presente no livro central de ofertas (CLOB)
    PARTIALLY_FILLED = "PARTIALLY_FILLED" # Pelo menos uma tranche ou execução parcial
    FILLED = "FILLED"                    # 100% da quantidade total executada
    CANCELLED = "CANCELLED"              # Cancelada pelo participante
    REJECTED = "REJECTED"                # Rejeitada por inconsistência de parâmetros


# ==============================================================================
# 2. Modelos de Dados (Dataclasses)
# ==============================================================================

@dataclass
class OrderExecutionTranche:
    """Representa uma tranche visível ativa ou histórica de uma ordem Iceberg."""
    tranche_id: str
    order_id: str
    tranche_index: int
    displayed_qty: int
    filled_qty: int
    remaining_hidden_qty: int
    price: Optional[Decimal]
    timestamp: datetime
    is_active: bool = True
    is_completed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tranche_id": self.tranche_id,
            "order_id": self.order_id,
            "tranche_index": self.tranche_index,
            "displayed_qty": self.displayed_qty,
            "filled_qty": self.filled_qty,
            "remaining_hidden_qty": self.remaining_hidden_qty,
            "price": str(self.price) if self.price is not None else None,
            "timestamp": self.timestamp.isoformat(),
            "is_active": self.is_active,
            "is_completed": self.is_completed,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OrderExecutionTranche:
        raw_ts = data["timestamp"]
        if isinstance(raw_ts, str):
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        else:
            ts = raw_ts

        price = Decimal(str(data["price"])) if data.get("price") is not None else None

        return cls(
            tranche_id=str(data["tranche_id"]),
            order_id=str(data["order_id"]),
            tranche_index=int(data["tranche_index"]),
            displayed_qty=int(data["displayed_qty"]),
            filled_qty=int(data["filled_qty"]),
            remaining_hidden_qty=int(data["remaining_hidden_qty"]),
            price=price,
            timestamp=ts,
            is_active=bool(data.get("is_active", True)),
            is_completed=bool(data.get("is_completed", False)),
        )


@dataclass
class BookFillEvent:
    """Evento de execução / match contra uma oferta no livro central."""
    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    fill_qty: int
    fill_price: Decimal
    timestamp: datetime
    remaining_visible: int
    remaining_hidden: int
    cum_qty: int
    tranche_index: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "fill_qty": self.fill_qty,
            "fill_price": str(self.fill_price),
            "timestamp": self.timestamp.isoformat(),
            "remaining_visible": self.remaining_visible,
            "remaining_hidden": self.remaining_hidden,
            "cum_qty": self.cum_qty,
            "tranche_index": self.tranche_index,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BookFillEvent:
        raw_ts = data["timestamp"]
        if isinstance(raw_ts, str):
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        else:
            ts = raw_ts

        return cls(
            fill_id=str(data["fill_id"]),
            order_id=str(data["order_id"]),
            symbol=str(data["symbol"]),
            side=OrderSide.from_str(data["side"]),
            fill_qty=int(data["fill_qty"]),
            fill_price=Decimal(str(data["fill_price"])),
            timestamp=ts,
            remaining_visible=int(data["remaining_visible"]),
            remaining_hidden=int(data["remaining_hidden"]),
            cum_qty=int(data["cum_qty"]),
            tranche_index=int(data.get("tranche_index", 1)),
        )


@dataclass
class AdvancedOrderSpec:
    """Especificação canônica de uma ordem avançada no PUMA Trading System."""
    order_id: str
    cl_ord_id: str
    symbol: str
    side: OrderSide
    order_type: AdvancedOrderType
    quantity: int
    price: Optional[Decimal] = None
    display_qty: Optional[int] = None
    stop_px: Optional[Decimal] = None
    trail_amount: Optional[Decimal] = None
    account: str = "DEFAULT"
    participant_code: int = 88
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Campos de Estado em Tempo de Execução
    trigger_status: OrderTriggerStatus = OrderTriggerStatus.ACTIVE
    executed_qty: int = 0
    visible_qty: int = 0
    hidden_qty: int = 0
    cancelled_qty: int = 0
    current_stop_px: Optional[Decimal] = None
    active_tranche_index: int = 0
    tranches: List[OrderExecutionTranche] = field(default_factory=list)
    time_priority_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not isinstance(self.side, OrderSide):
            self.side = OrderSide.from_str(self.side)
        if not isinstance(self.order_type, AdvancedOrderType):
            self.order_type = AdvancedOrderType.from_str(self.order_type)
        if not isinstance(self.trigger_status, OrderTriggerStatus):
            self.trigger_status = OrderTriggerStatus(self.trigger_status)

        if self.price is not None and not isinstance(self.price, Decimal):
            self.price = Decimal(str(self.price))
        if self.stop_px is not None and not isinstance(self.stop_px, Decimal):
            self.stop_px = Decimal(str(self.stop_px))
        if self.trail_amount is not None and not isinstance(self.trail_amount, Decimal):
            self.trail_amount = Decimal(str(self.trail_amount))

        if self.current_stop_px is None and self.stop_px is not None:
            self.current_stop_px = self.stop_px

        if self.time_priority_timestamp is None:
            self.time_priority_timestamp = self.timestamp

    @property
    def total_qty(self) -> int:
        """Alias para a quantidade total estipulada na ordem."""
        return self.quantity

    @property
    def leaves_qty(self) -> int:
        """Quantidade total remanescente a ser executada no mercado (Tag 151)."""
        return max(0, self.quantity - self.executed_qty)

    @property
    def remaining_hidden_qty(self) -> int:
        """Quantidade remanescente mantida em reserva pelo motor algorítmico."""
        return max(0, self.quantity - self.executed_qty)

    def is_complete(self) -> bool:
        """Retorna True se a ordem foi integralmente executada."""
        return self.executed_qty >= self.quantity

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "cl_ord_id": self.cl_ord_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "display_qty": self.display_qty,
            "price": str(self.price) if self.price is not None else None,
            "stop_px": str(self.stop_px) if self.stop_px is not None else None,
            "trail_amount": str(self.trail_amount) if self.trail_amount is not None else None,
            "account": self.account,
            "participant_code": self.participant_code,
            "timestamp": self.timestamp.isoformat(),
            "trigger_status": self.trigger_status.value,
            "executed_qty": self.executed_qty,
            "visible_qty": self.visible_qty,
            "hidden_qty": self.hidden_qty,
            "cancelled_qty": self.cancelled_qty,
            "current_stop_px": str(self.current_stop_px) if self.current_stop_px is not None else None,
            "active_tranche_index": self.active_tranche_index,
            "time_priority_timestamp": self.time_priority_timestamp.isoformat(),
            "tranches": [t.to_dict() for t in self.tranches],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AdvancedOrderSpec:
        raw_ts = data["timestamp"]
        if isinstance(raw_ts, str):
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        else:
            ts = raw_ts

        price = Decimal(str(data["price"])) if data.get("price") is not None else None
        stop_px = Decimal(str(data["stop_px"])) if data.get("stop_px") is not None else None
        trail_amount = Decimal(str(data["trail_amount"])) if data.get("trail_amount") is not None else None
        curr_stop = Decimal(str(data["current_stop_px"])) if data.get("current_stop_px") is not None else None

        spec = cls(
            order_id=str(data["order_id"]),
            cl_ord_id=str(data["cl_ord_id"]),
            symbol=str(data["symbol"]),
            side=OrderSide.from_str(data["side"]),
            order_type=AdvancedOrderType.from_str(data["order_type"]),
            quantity=int(data["quantity"]),
            price=price,
            display_qty=int(data["display_qty"]) if data.get("display_qty") is not None else None,
            stop_px=stop_px,
            trail_amount=trail_amount,
            account=str(data.get("account", "DEFAULT")),
            participant_code=int(data.get("participant_code", 88)),
            timestamp=ts,
            trigger_status=OrderTriggerStatus(data.get("trigger_status", "ACTIVE")),
            executed_qty=int(data.get("executed_qty", 0)),
            visible_qty=int(data.get("visible_qty", 0)),
            hidden_qty=int(data.get("hidden_qty", 0)),
            cancelled_qty=int(data.get("cancelled_qty", 0)),
            current_stop_px=curr_stop if curr_stop is not None else stop_px,
            active_tranche_index=int(data.get("active_tranche_index", 0)),
        )

        if "time_priority_timestamp" in data and data["time_priority_timestamp"]:
            raw_prio = data["time_priority_timestamp"]
            if isinstance(raw_prio, str):
                spec.time_priority_timestamp = datetime.fromisoformat(raw_prio.replace("Z", "+00:00"))
            else:
                spec.time_priority_timestamp = raw_prio

        if "tranches" in data and isinstance(data["tranches"], list):
            spec.tranches = [OrderExecutionTranche.from_dict(t) for t in data["tranches"]]

        return spec


# ==============================================================================
# 3. Motor de Ordens Avançadas PUMA Trading System
# ==============================================================================

class PumaAdvancedOrdersEngine:
    """Motor determinístico de roteamento, livro de ofertas e execução de ordens avançadas da B3."""

    def __init__(self) -> None:
        self.orders: Dict[str, AdvancedOrderSpec] = {}
        self.clob: Dict[str, List[AdvancedOrderSpec]] = {}       # Livro central ativo por símbolo
        self.stop_book: Dict[str, List[AdvancedOrderSpec]] = {}  # Livro de gatilhos dormentes por símbolo
        self.fill_events: List[BookFillEvent] = []
        self.last_traded_prices: Dict[str, Decimal] = {}
        self._fill_counter: int = 0
        self._tranche_counter: int = 0

    # --------------------------------------------------------------------------
    # Registro e Roteamento Inicial de Ordens
    # --------------------------------------------------------------------------

    def register_order(self, order: AdvancedOrderSpec) -> AdvancedOrderSpec:
        """Valida, registra e direciona uma ordem avançada para o subsistema correto."""
        if order.quantity <= 0:
            raise ValueError(f"Quantidade total deve ser positiva: {order.quantity}")

        if order.order_id in self.orders:
            raise ValueError(f"Ordem {order.order_id} já cadastrada no motor.")

        symbol = order.symbol.strip().upper()
        order.symbol = symbol

        if order.order_type == AdvancedOrderType.ICEBERG:
            self._setup_iceberg_order(order)
        elif order.order_type in (AdvancedOrderType.STOP_LOSS, AdvancedOrderType.STOP_LIMIT):
            self._setup_stop_order(order)
        elif order.order_type == AdvancedOrderType.TRAILING_STOP:
            self._setup_trailing_stop_order(order)
        else:
            raise ValueError(f"Tipo de ordem não suportado: {order.order_type}")

        self.orders[order.order_id] = order
        return order

    def _setup_iceberg_order(self, order: AdvancedOrderSpec) -> None:
        """Inicializa tranches e exposição visível para ordem Iceberg."""
        if order.price is None or order.price <= Decimal("0"):
            raise ValueError("Ordem Iceberg exige preço limite estipulado maior que zero.")

        if order.display_qty is None or order.display_qty <= 0:
            raise ValueError("Ordem Iceberg exige Tag 1138 DisplayQty positivo.")

        if order.display_qty > order.quantity:
            raise ValueError(f"DisplayQty ({order.display_qty}) não pode ser maior que Quantity ({order.quantity}).")

        # Configura primeira tranche visível e reserva oculta
        first_tranche_qty = order.display_qty
        order.visible_qty = first_tranche_qty
        order.hidden_qty = order.quantity - first_tranche_qty
        order.active_tranche_index = 1
        order.trigger_status = OrderTriggerStatus.ACTIVE
        order.time_priority_timestamp = order.timestamp

        self._tranche_counter += 1
        tranche = OrderExecutionTranche(
            tranche_id=f"TR-{order.order_id}-{self._tranche_counter:04d}",
            order_id=order.order_id,
            tranche_index=order.active_tranche_index,
            displayed_qty=first_tranche_qty,
            filled_qty=0,
            remaining_hidden_qty=order.hidden_qty,
            price=order.price,
            timestamp=order.timestamp,
            is_active=True,
            is_completed=False,
        )
        order.tranches.append(tranche)

        # Adiciona ao livro central de ofertas
        self.clob.setdefault(order.symbol, []).append(order)

    def _setup_stop_order(self, order: AdvancedOrderSpec) -> None:
        """Inicializa ordem Stop-Loss ou Stop-Limit no livro dormente de gatilhos."""
        if order.stop_px is None or order.stop_px <= Decimal("0"):
            raise ValueError(f"Ordem {order.order_type.value} exige StopPx positivo.")

        if order.order_type == AdvancedOrderType.STOP_LIMIT:
            if order.price is None or order.price <= Decimal("0"):
                raise ValueError("Ordem Stop-Limit exige Price limite válido para inserção após disparo.")

        order.current_stop_px = order.stop_px
        order.trigger_status = OrderTriggerStatus.PENDING_TRIGGER
        order.visible_qty = 0
        order.hidden_qty = order.quantity

        # Adiciona ao livro dormente de gatilhos
        self.stop_book.setdefault(order.symbol, []).append(order)

    def _setup_trailing_stop_order(self, order: AdvancedOrderSpec) -> None:
        """Inicializa ordem Trailing Stop no livro de gatilhos móveis."""
        if order.stop_px is None or order.stop_px <= Decimal("0"):
            raise ValueError("Ordem Trailing Stop exige StopPx inicial maior que zero.")

        if order.trail_amount is None or order.trail_amount <= Decimal("0"):
            raise ValueError("Ordem Trailing Stop exige TrailAmount positivo.")

        order.current_stop_px = order.stop_px
        order.trigger_status = OrderTriggerStatus.PENDING_TRIGGER
        order.visible_qty = 0
        order.hidden_qty = order.quantity

        # Adiciona ao livro dormente de gatilhos
        self.stop_book.setdefault(order.symbol, []).append(order)

    # --------------------------------------------------------------------------
    # Processamento de Negócios e Ticks de Mercado (Monitor de Gatilhos)
    # --------------------------------------------------------------------------

    def process_market_tick(
        self,
        tick: Union[Dict[str, Any], Any],
        timestamp: Optional[datetime] = None,
    ) -> List[AdvancedOrderSpec]:
        """Processa um tick/negócio de mercado e avalia disparos de ordens condicionais."""
        if isinstance(tick, dict):
            symbol = str(tick["symbol"]).strip().upper()
            price = Decimal(str(tick["price"]))
            raw_ts = tick.get("timestamp")
            if raw_ts and isinstance(raw_ts, str):
                ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            elif isinstance(raw_ts, datetime):
                ts = raw_ts
            else:
                ts = timestamp or datetime.now(timezone.utc)
        else:
            symbol = str(getattr(tick, "symbol")).strip().upper()
            price = Decimal(str(getattr(tick, "price")))
            ts = getattr(tick, "timestamp", timestamp or datetime.now(timezone.utc))

        self.last_traded_prices[symbol] = price
        triggered_orders: List[AdvancedOrderSpec] = []

        pending_orders = self.stop_book.get(symbol, [])
        remaining_pending: List[AdvancedOrderSpec] = []

        for order in pending_orders:
            if order.trigger_status != OrderTriggerStatus.PENDING_TRIGGER:
                continue

            if order.order_type == AdvancedOrderType.STOP_LOSS:
                if self._check_stop_trigger(order, price):
                    self._activate_stop_loss(order, price, ts)
                    triggered_orders.append(order)
                else:
                    remaining_pending.append(order)

            elif order.order_type == AdvancedOrderType.STOP_LIMIT:
                if self._check_stop_trigger(order, price):
                    self._activate_stop_limit(order, price, ts)
                    triggered_orders.append(order)
                else:
                    remaining_pending.append(order)

            elif order.order_type == AdvancedOrderType.TRAILING_STOP:
                # 1º: Atualiza gatilho se o mercado andou a favor
                self._update_trailing_stop(order, price)
                # 2º: Avalia se o preço atingiu o gatilho móvel atualizado
                if self._check_stop_trigger(order, price):
                    self._activate_trailing_stop(order, price, ts)
                    triggered_orders.append(order)
                else:
                    remaining_pending.append(order)
            else:
                remaining_pending.append(order)

        self.stop_book[symbol] = remaining_pending
        return triggered_orders

    def _check_stop_trigger(self, order: AdvancedOrderSpec, current_price: Decimal) -> bool:
        """Verifica se a cotação atual atingiu o preço de gatilho do stop."""
        stop_px = order.current_stop_px or order.stop_px
        if stop_px is None:
            return False

        if order.side == OrderSide.SELL:
            # Venda Stop (proteção de posição comprada): dispara quando preço cai <= StopPx
            return current_price <= stop_px
        else:
            # Compra Stop (proteção de posição vendida ou breakout): dispara quando preço sobe >= StopPx
            return current_price >= stop_px

    def _update_trailing_stop(self, order: AdvancedOrderSpec, current_price: Decimal) -> None:
        """Ajusta dinamicamente o StopPx do Trailing Stop segundo o avanço favorável do mercado."""
        assert order.trail_amount is not None
        assert order.current_stop_px is not None

        if order.side == OrderSide.SELL:
            # Trailing Stop de Venda: mercado sobe favoravelmente -> puxa StopPx para cima
            candidate_stop = current_price - order.trail_amount
            if candidate_stop > order.current_stop_px:
                order.current_stop_px = candidate_stop
        else:
            # Trailing Stop de Compra: mercado desce favoravelmente -> puxa StopPx para baixo
            candidate_stop = current_price + order.trail_amount
            if candidate_stop < order.current_stop_px:
                order.current_stop_px = candidate_stop

    def _activate_stop_loss(self, order: AdvancedOrderSpec, trigger_price: Decimal, timestamp: datetime) -> None:
        """Ativa ordem Stop-Loss convertendo-a em ordem a mercado para o CLOB."""
        order.trigger_status = OrderTriggerStatus.TRIGGERED
        order.time_priority_timestamp = timestamp
        order.visible_qty = order.quantity
        order.hidden_qty = 0
        self.clob.setdefault(order.symbol, []).append(order)

    def _activate_stop_limit(self, order: AdvancedOrderSpec, trigger_price: Decimal, timestamp: datetime) -> None:
        """Ativa ordem Stop-Limit inserindo ordem limitada no CLOB no preço Price."""
        order.trigger_status = OrderTriggerStatus.TRIGGERED
        order.time_priority_timestamp = timestamp
        order.visible_qty = order.quantity
        order.hidden_qty = 0
        self.clob.setdefault(order.symbol, []).append(order)

    def _activate_trailing_stop(self, order: AdvancedOrderSpec, trigger_price: Decimal, timestamp: datetime) -> None:
        """Ativa ordem Trailing Stop convertendo-a em ordem acionada para o CLOB."""
        order.trigger_status = OrderTriggerStatus.TRIGGERED
        order.time_priority_timestamp = timestamp
        order.visible_qty = order.quantity
        order.hidden_qty = 0
        self.clob.setdefault(order.symbol, []).append(order)

    # --------------------------------------------------------------------------
    # Execução de Matches e Gestão de Tranches (CLOB Fills)
    # --------------------------------------------------------------------------

    def process_fill(
        self,
        order_id: str,
        fill_qty: int,
        fill_price: Decimal,
        timestamp: Optional[datetime] = None,
    ) -> BookFillEvent:
        """Registra a execução de um match contra a ordem ativa no livro central."""
        if order_id not in self.orders:
            raise KeyError(f"Ordem {order_id} não encontrada no motor.")

        if fill_qty <= 0:
            raise ValueError(f"Quantidade de execução deve ser positiva: {fill_qty}")

        order = self.orders[order_id]
        ts = timestamp or datetime.now(timezone.utc)

        if order.is_complete():
            raise ValueError(f"Ordem {order_id} já está 100% preenchida (Filled).")

        if fill_qty > order.visible_qty:
            raise ValueError(
                f"Fill de {fill_qty} excede a quantidade visível disponível no book ({order.visible_qty})."
            )

        # Atualiza métricas de execução
        order.executed_qty += fill_qty
        order.visible_qty -= fill_qty

        # Atualiza a tranche ativa se for Iceberg
        if order.order_type == AdvancedOrderType.ICEBERG and order.tranches:
            current_tranche = order.tranches[-1]
            current_tranche.filled_qty += fill_qty
            if current_tranche.filled_qty >= current_tranche.displayed_qty:
                current_tranche.is_active = False
                current_tranche.is_completed = True

        # Criação do evento de fill
        self._fill_counter += 1
        fill_event = BookFillEvent(
            fill_id=f"FILL-{order.symbol}-{self._fill_counter:06d}",
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            fill_qty=fill_qty,
            fill_price=fill_price,
            timestamp=ts,
            remaining_visible=order.visible_qty,
            remaining_hidden=order.hidden_qty,
            cum_qty=order.executed_qty,
            tranche_index=order.active_tranche_index or 1,
        )
        self.fill_events.append(fill_event)

        # Verificação de renovação de tranche (Iceberg Refresh)
        if order.order_type == AdvancedOrderType.ICEBERG:
            if order.visible_qty == 0 and order.hidden_qty > 0:
                self._refresh_iceberg_tranche(order, ts)

        # Atualiza status final da ordem
        if order.is_complete():
            order.trigger_status = OrderTriggerStatus.FILLED
            order.visible_qty = 0
            order.hidden_qty = 0
            # Remove do CLOB ativo
            if order.symbol in self.clob and order in self.clob[order.symbol]:
                self.clob[order.symbol].remove(order)
        else:
            order.trigger_status = OrderTriggerStatus.PARTIALLY_FILLED

        # Validação formal imediata da invariante
        self._assert_conservation_invariants(order)

        return fill_event

    def _refresh_iceberg_tranche(self, order: AdvancedOrderSpec, timestamp: datetime) -> None:
        """Renova a tranche visível da ordem Iceberg com nova prioridade temporal."""
        assert order.display_qty is not None
        next_qty = min(order.display_qty, order.hidden_qty)
        order.visible_qty = next_qty
        order.hidden_qty -= next_qty
        order.active_tranche_index += 1

        # PERDA DE PRIORIDADE TEMPORAL: nova tranche recebe o carimbo de tempo da renovação
        order.time_priority_timestamp = timestamp

        self._tranche_counter += 1
        new_tranche = OrderExecutionTranche(
            tranche_id=f"TR-{order.order_id}-{self._tranche_counter:04d}",
            order_id=order.order_id,
            tranche_index=order.active_tranche_index,
            displayed_qty=next_qty,
            filled_qty=0,
            remaining_hidden_qty=order.hidden_qty,
            price=order.price,
            timestamp=timestamp,
            is_active=True,
            is_completed=False,
        )
        order.tranches.append(new_tranche)

    # --------------------------------------------------------------------------
    # Verificação Formal de Invariantes
    # --------------------------------------------------------------------------

    def _assert_conservation_invariants(self, order: AdvancedOrderSpec) -> None:
        """Assegura a conservação de lotes matematicamente."""
        if order.trigger_status == OrderTriggerStatus.CANCELLED:
            if order.executed_qty + order.cancelled_qty != order.quantity:
                raise AssertionError(
                    f"Quebra de Invariante em ordem cancelada {order.order_id}: "
                    f"Executado ({order.executed_qty}) + Cancelado ({order.cancelled_qty}) != Total ({order.quantity})"
                )
            return

        # Invariante contínua completa: Executado + Visível + Oculto == Total
        calculated_total = order.executed_qty + order.visible_qty + order.hidden_qty
        if calculated_total != order.quantity:
            raise AssertionError(
                f"Quebra de Invariante Contínua na ordem {order.order_id}: "
                f"Executado ({order.executed_qty}) + Visível ({order.visible_qty}) + Oculto ({order.hidden_qty}) "
                f"= {calculated_total} != Total ({order.quantity})"
            )

        # Invariante de saldo não executado: Executado + Oculta_Restante == Total
        if order.executed_qty + order.remaining_hidden_qty != order.quantity:
            raise AssertionError(
                f"Quebra de Invariante de Saldo Restante na ordem {order.order_id}: "
                f"Executado ({order.executed_qty}) + Oculta_Restante ({order.remaining_hidden_qty}) != Total ({order.quantity})"
            )

    def execute_iceberg_tranche(
        self,
        order_id: str,
        fill_price: Optional[Decimal] = None,
        timestamp: Optional[datetime] = None,
    ) -> BookFillEvent:
        """Executa integralmente a tranche visível atual de uma ordem Iceberg."""
        order = self.get_order(order_id)
        if order.visible_qty <= 0:
            raise ValueError(f"Ordem {order_id} não possui quantidade visível ativa para execução.")
        price = fill_price or order.price or Decimal("0")
        return self.process_fill(order_id, fill_qty=order.visible_qty, fill_price=price, timestamp=timestamp)

    def execute_order(
        self,
        order_id: str,
        fill_qty: Optional[int] = None,
        fill_price: Optional[Decimal] = None,
        timestamp: Optional[datetime] = None,
    ) -> BookFillEvent:
        """Executa uma ordem no CLOB com quantidade especificada ou todo o saldo visível."""
        order = self.get_order(order_id)
        qty = fill_qty if fill_qty is not None else order.visible_qty
        price = fill_price or order.price or self.last_traded_prices.get(order.symbol, Decimal("0"))
        return self.process_fill(order_id, fill_qty=qty, fill_price=price, timestamp=timestamp)

    def verify_order_conservation(self, order_id: str) -> bool:
        """Valida explicitamente a conservação de lotes para uma ordem específica."""
        if order_id not in self.orders:
            raise KeyError(f"Ordem {order_id} inexistente.")
        order = self.orders[order_id]
        self._assert_conservation_invariants(order)
        return True

    def verify_all_invariants(self) -> bool:
        """Varre todas as ordens registradas comprovando todas as leis de conservação."""
        for order in self.orders.values():
            self._assert_conservation_invariants(order)
        return True

    # --------------------------------------------------------------------------
    # Cancelamento e Consultas
    # --------------------------------------------------------------------------

    def cancel_order(self, order_id: str, timestamp: Optional[datetime] = None) -> AdvancedOrderSpec:
        """Cancela uma ordem ativa ou dormente no motor PUMA."""
        if order_id not in self.orders:
            raise KeyError(f"Ordem {order_id} inexistente.")

        order = self.orders[order_id]
        if order.is_complete():
            raise ValueError(f"Ordem {order_id} já concluída, não pode ser cancelada.")

        order.trigger_status = OrderTriggerStatus.CANCELLED
        order.cancelled_qty = order.leaves_qty
        order.visible_qty = 0
        order.hidden_qty = 0

        # Remove dos livros
        if order.symbol in self.clob and order in self.clob[order.symbol]:
            self.clob[order.symbol].remove(order)
        if order.symbol in self.stop_book and order in self.stop_book[order.symbol]:
            self.stop_book[order.symbol].remove(order)

        self._assert_conservation_invariants(order)
        return order

    def get_order(self, order_id: str) -> AdvancedOrderSpec:
        """Recupera especificação e estado atual de uma ordem."""
        if order_id not in self.orders:
            raise KeyError(f"Ordem {order_id} não cadastrada.")
        return self.orders[order_id]

    # --------------------------------------------------------------------------
    # Carga de Fixtures e Serialização
    # --------------------------------------------------------------------------

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        """Carrega e registra lote de ordens a partir de dicionário estruturado."""
        for ord_data in data.get("orders", []):
            spec = AdvancedOrderSpec.from_dict(ord_data)
            self.register_order(spec)

    def load_from_fixture(self, fixture_path: Union[str, Path]) -> None:
        """Carrega lote de ordens e execuções a partir de arquivo JSON de fixture."""
        path = Path(fixture_path)
        if not path.exists():
            raise FileNotFoundError(f"Fixture não encontrada: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.load_from_dict(data)
