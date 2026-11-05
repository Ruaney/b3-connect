"""Módulo de Reconstituição Determinística de Livro de Ofertas L3 Market By Order (MBO) — B3 (FL-091).

Implementa a reconstituição em tempo real e determinística do Livro de Ofertas L3 (Market By Order)
a partir de fluxos granulares de eventos de mercado (ITCH / SBE B3 UMDF Market Data) sob a regra
estrita de prioridade Preço-Tempo (FIFO) do PUMA Trading System da B3.

Funcionalidades Principais:
1. Processamento Granular de Eventos MBO:
   - OrderAdd: Inserção de nova ordem com OrderID exclusivo, lado (Bid/Ask), preço e volume.
   - OrderModify: Alteração de volume/preço. Redução mantém a prioridade de fila temporal existente;
     aumento de volume ou alteração de preço perde a prioridade e posiciona no final da fila.
   - OrderCancel: Cancelamento parcial ou total de oferta remanescente no book.
   - OrderExecute: Execução contra ordem passiva agressora, reduzindo o volume remanescente da ordem.
   - OrderDelete: Exclusão compulsória/definitiva de ordem (expiração FAK/FOK/IOC, fim de dia, cancelamento administrativo).
2. Livro de Ofertas Bidirecional L3 e Agregação L2:
   - Bids: ordenamento decrescente de preços (melhor preço mais alto).
   - Asks: ordenamento crescente de preços (melhor preço mais baixo).
   - Nível de preço com fila FIFO estrita em OrderedDict (lookup e movimentação O(1)).
   - Cálculo contínuo do Top of Book (BBO), Spread bid-ask, Preço Médio (Mid) e Micro-preço ponderado por volume.
   - Visão agregada L2 (Market By Price) com profundidade configurável.
3. Codec Binário Bidirecional (ITCH / SBE B3 UMDF):
   - Serialização e desserialização binária compacta Little-Endian via struct com precisão escalar fixa (10^8).
   - Suporte a decodificação de streams e pacotes contendo múltiplos eventos contíguos.
4. Auditoria Formal de Leis Invariantes de Mercado:
   - Lei da Conservação de Lotes: Lotes_Atuais == Lotes_Adicionados - Lotes_Executados - Lotes_Cancelados.
   - Lei do Não-Cruzamento de Livro em Mercado Contínuo: Best_Bid < Best_Ask (detecta crossed/locked books).
   - Lei da Monotonicidade Estrita de Preços entre níveis de compra e venda.
   - Lei da Prioridade Estrita Preço-Tempo (FIFO) dentro de cada nível de preço.
   - Lei da Consistência de Índices e Agregações de Volume.

Zero dependências externas (100% Python Standard Library).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from enum import Enum, IntEnum
import json
from pathlib import Path
import struct
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple, Union

# Integração com o núcleo formal de invariantes da B3
try:
    from src.invariants import InvariantViolationError, check
except ImportError:
    class InvariantViolationError(AssertionError):
        """Exceção de violação de invariante quando módulo de invariantes não está no path."""

        def __init__(
            self,
            message: str,
            law_code: Optional[str] = None,
            details: Optional[Dict[str, Any]] = None,
        ) -> None:
            super().__init__(message)
            self.message = message
            self.law_code = law_code
            self.details = details or {}

        def __str__(self) -> str:
            prefix = f"[{self.law_code}] " if self.law_code else ""
            return f"VIOLAÇÃO DE INVARIANTE: {prefix}{self.message}"

    def check(
        condition: bool,
        message: str,
        law_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not condition:
            raise InvariantViolationError(message, law_code=law_code, details=details)


# ---------------------------------------------------------------------------
# Constantes Numéricas e de Protocolo Binário B3 UMDF / ITCH
# ---------------------------------------------------------------------------

PRICE_SCALE: int = 100_000_000  # 10^8 (8 casas decimais de precisão)
PRICE_TOLERANCE: Decimal = Decimal("0.00000001")
MAGIC_HEADER_B3_MBO: int = 0xB34D  # Assinatura binária "B3M" (0xB3, 0x4D)
PROTOCOL_VERSION_MBO: int = 1


# ---------------------------------------------------------------------------
# Hierarquia de Exceções
# ---------------------------------------------------------------------------

class MboBookError(Exception):
    """Exceção base para erros no Livro de Ofertas Market By Order."""


class OrderNotFoundError(MboBookError):
    """Ordem referenciada por OrderID não existe no livro ativo."""


class DuplicateOrderError(MboBookError):
    """Tentativa de inserção de ordem com OrderID já existente no livro."""


class InvalidOrderError(MboBookError):
    """Parâmetros de ordem inválidos (preço <= 0, quantidade <= 0, etc)."""


class CrossedBookError(MboBookError, InvariantViolationError):
    """Livro de ofertas cruzado ou travado em modo de negociação contínua."""


class MboCodecError(MboBookError):
    """Falha de codificação/decodificação binária de pacotes ITCH/SBE MBO."""


class MboInvariantViolationError(MboBookError, InvariantViolationError):
    """Violação de invariante formal de conservação ou integridade de microestrutura."""


# ---------------------------------------------------------------------------
# Enums Canônicos de Microestrutura e Protocolo
# ---------------------------------------------------------------------------

class OrderSide(str, Enum):
    """Lado da Ordem no Livro de Ofertas B3."""

    BID = "BID"  # Compra (Bids)
    ASK = "ASK"  # Venda (Asks / Offers)

    @classmethod
    def from_code(cls, code: Union[str, int, bytes, OrderSide]) -> OrderSide:
        """Converte códigos comuns ('B', 'S', 0, 1, b'B', b'S') para OrderSide."""
        if isinstance(code, cls):
            return code
        if isinstance(code, bytes):
            code = code.decode("ascii")
        if isinstance(code, int):
            if code == 0:
                return cls.BID
            if code == 1:
                return cls.ASK
            raise ValueError(f"Código numérico inválido para OrderSide: {code}")
        val = str(code).upper().strip()
        if val in ("BID", "B", "BUY", "COMPRA", "0"):
            return cls.BID
        if val in ("ASK", "OFFER", "S", "SELL", "VENDA", "1"):
            return cls.ASK
        raise ValueError(f"Código de lado de ordem não reconhecido: {code}")

    @property
    def byte_code(self) -> bytes:
        """Retorna código de 1 byte padrão ITCH ('B' ou 'S')."""
        return b"B" if self == OrderSide.BID else b"S"

    @property
    def opposite(self) -> OrderSide:
        """Retorna o lado oposto do book."""
        return OrderSide.ASK if self == OrderSide.BID else OrderSide.BID


class TradingMode(str, Enum):
    """Modo Operacional da Sessão do Instrumento."""

    CONTINUOUS = "CONTINUOUS"  # Negociação Contínua (FIFO Estrito, Não Cruzamento Obrigatório)
    AUCTION = "AUCTION"        # Leilão (Permite cruzamento e apuração de Preço Teórico)
    HALTED = "HALTED"          # Negociação Suspensa / Travada
    CLOSED = "CLOSED"          # Mercado Fechado


class MboEventType(str, Enum):
    """Tipologia Granular de Eventos MBO B3."""

    ADD = "ADD"          # OrderAdd (Nova ordem)
    MODIFY = "MODIFY"    # OrderModify (Alteração de preço ou quantidade)
    CANCEL = "CANCEL"    # OrderCancel (Cancelamento parcial ou total)
    EXECUTE = "EXECUTE"  # OrderExecute (Execução de negócio)
    DELETE = "DELETE"    # OrderDelete (Exclusão definitiva da ordem)

    @property
    def byte_char(self) -> bytes:
        """Caractere representativo binário padrão ITCH."""
        mapping = {
            MboEventType.ADD: b"A",
            MboEventType.MODIFY: b"M",
            MboEventType.CANCEL: b"X",
            MboEventType.EXECUTE: b"E",
            MboEventType.DELETE: b"D",
        }
        return mapping[self]

    @classmethod
    def from_byte_char(cls, char_byte: bytes) -> MboEventType:
        """Converte caractere binário ITCH no enum correspondente."""
        char_str = char_byte.decode("ascii").upper()
        mapping = {
            "A": cls.ADD,
            "M": cls.MODIFY,
            "X": cls.CANCEL,
            "C": cls.CANCEL,
            "E": cls.EXECUTE,
            "D": cls.DELETE,
        }
        if char_str not in mapping:
            raise MboCodecError(f"Tipo de mensagem binária ITCH desconhecido: {char_byte!r}")
        return mapping[char_str]


# ---------------------------------------------------------------------------
# Funções Utilitárias de Conversão e Precisão
# ---------------------------------------------------------------------------

def to_decimal_price(value: Union[Decimal, float, int, str]) -> Decimal:
    """Converte valores numéricos em Decimal com normalização de precisão."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(str(value))


def scale_price_to_int(price: Decimal) -> int:
    """Multiplica o preço por 10^8 e arredonda para int64 binário."""
    return int(round(price * PRICE_SCALE))


def unscale_int_to_price(price_scaled: int) -> Decimal:
    """Converte inteiro escalado de volta para Decimal com 8 casas decimais."""
    return Decimal(price_scaled) / Decimal(PRICE_SCALE)


# ---------------------------------------------------------------------------
# Modelos de Eventos MBO Granulares
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrderAddEvent:
    """Evento MBO OrderAdd — Inclusão de Nova Ordem no Livro L3."""

    order_id: int
    security_id: int
    symbol: str
    side: OrderSide
    price: Decimal
    quantity: int
    sequence_number: int
    timestamp: datetime
    participant_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.order_id <= 0:
            raise InvalidOrderError(f"order_id deve ser estritamente positivo: {self.order_id}")
        if self.price <= 0:
            raise InvalidOrderError(f"price deve ser estritamente positivo: {self.price}")
        if self.quantity <= 0:
            raise InvalidOrderError(f"quantity deve ser estritamente positivo: {self.quantity}")
        if self.sequence_number < 0:
            raise InvalidOrderError(f"sequence_number inválido: {self.sequence_number}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": MboEventType.ADD.value,
            "order_id": self.order_id,
            "security_id": self.security_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "price": str(self.price),
            "quantity": self.quantity,
            "sequence_number": self.sequence_number,
            "timestamp": self.timestamp.isoformat(),
            "participant_id": self.participant_id,
        }


@dataclass(frozen=True)
class OrderModifyEvent:
    """Evento MBO OrderModify — Alteração de Volume ou Preço de Ordem Existente.

    Regra de Microestrutura B3 / PUMA FIFO:
    - Redução de volume no mesmo preço: MANTÉM a prioridade na fila temporal.
    - Aumento de volume no mesmo preço: PERDE a prioridade e vai para o final da fila de mesmo preço.
    - Alteração de preço: PERDE a prioridade e é inserida no final da fila do novo nível de preço.
    """

    order_id: int
    security_id: int
    symbol: str
    new_quantity: int
    new_price: Optional[Decimal] = None
    sequence_number: int = 0
    timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.order_id <= 0:
            raise InvalidOrderError(f"order_id deve ser estritamente positivo: {self.order_id}")
        if self.new_quantity <= 0:
            raise InvalidOrderError(f"new_quantity deve ser estritamente positivo: {self.new_quantity}")
        if self.new_price is not None and self.new_price <= 0:
            raise InvalidOrderError(f"new_price deve ser estritamente positivo: {self.new_price}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": MboEventType.MODIFY.value,
            "order_id": self.order_id,
            "security_id": self.security_id,
            "symbol": self.symbol,
            "new_quantity": self.new_quantity,
            "new_price": str(self.new_price) if self.new_price is not None else None,
            "sequence_number": self.sequence_number,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass(frozen=True)
class OrderCancelEvent:
    """Evento MBO OrderCancel — Cancelamento Parcial ou Total de Ordem."""

    order_id: int
    security_id: int
    symbol: str
    canceled_quantity: Optional[int] = None  # None ou >= qty restante significa cancelamento total
    sequence_number: int = 0
    timestamp: Optional[datetime] = None
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.order_id <= 0:
            raise InvalidOrderError(f"order_id deve ser estritamente positivo: {self.order_id}")
        if self.canceled_quantity is not None and self.canceled_quantity <= 0:
            raise InvalidOrderError(f"canceled_quantity deve ser > 0: {self.canceled_quantity}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": MboEventType.CANCEL.value,
            "order_id": self.order_id,
            "security_id": self.security_id,
            "symbol": self.symbol,
            "canceled_quantity": self.canceled_quantity,
            "sequence_number": self.sequence_number,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class OrderExecuteEvent:
    """Evento MBO OrderExecute — Execução contra Ordem Passiva."""

    order_id: int
    security_id: int
    symbol: str
    executed_quantity: int
    execution_price: Decimal
    trade_id: str
    aggressor_side: Optional[OrderSide] = None
    sequence_number: int = 0
    timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.order_id <= 0:
            raise InvalidOrderError(f"order_id deve ser estritamente positivo: {self.order_id}")
        if self.executed_quantity <= 0:
            raise InvalidOrderError(f"executed_quantity deve ser > 0: {self.executed_quantity}")
        if self.execution_price <= 0:
            raise InvalidOrderError(f"execution_price deve ser > 0: {self.execution_price}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": MboEventType.EXECUTE.value,
            "order_id": self.order_id,
            "security_id": self.security_id,
            "symbol": self.symbol,
            "executed_quantity": self.executed_quantity,
            "execution_price": str(self.execution_price),
            "trade_id": self.trade_id,
            "aggressor_side": self.aggressor_side.value if self.aggressor_side else None,
            "sequence_number": self.sequence_number,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass(frozen=True)
class OrderDeleteEvent:
    """Evento MBO OrderDelete — Exclusão Definitiva de Ordem do Book."""

    order_id: int
    security_id: int
    symbol: str
    sequence_number: int = 0
    timestamp: Optional[datetime] = None
    reason: Optional[str] = "ADMINISTRATIVE_OR_EXPIRED"

    def __post_init__(self) -> None:
        if self.order_id <= 0:
            raise InvalidOrderError(f"order_id deve ser estritamente positivo: {self.order_id}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": MboEventType.DELETE.value,
            "order_id": self.order_id,
            "security_id": self.security_id,
            "symbol": self.symbol,
            "sequence_number": self.sequence_number,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "reason": self.reason,
        }


MboEvent = Union[
    OrderAddEvent,
    OrderModifyEvent,
    OrderCancelEvent,
    OrderExecuteEvent,
    OrderDeleteEvent,
]


# ---------------------------------------------------------------------------
# Entidades do Livro de Ofertas L3 e Agregação L2
# ---------------------------------------------------------------------------

@dataclass
class L3Order:
    """Representação Individual de uma Ordem no Livro L3."""

    order_id: int
    security_id: int
    symbol: str
    side: OrderSide
    price: Decimal
    quantity: int
    initial_quantity: int
    priority_timestamp: datetime
    sequence_number: int
    participant_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "security_id": self.security_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "price": str(self.price),
            "quantity": self.quantity,
            "initial_quantity": self.initial_quantity,
            "priority_timestamp": self.priority_timestamp.isoformat(),
            "sequence_number": self.sequence_number,
            "participant_id": self.participant_id,
        }


class PriceLevel:
    """Nível de Preço com Fila Estrita Preço-Tempo (FIFO) baseada em OrderedDict."""

    def __init__(self, price: Decimal, side: OrderSide) -> None:
        self.price: Decimal = price
        self.side: OrderSide = side
        # Mantém a fila FIFO estrita: ordem de inserção determina a prioridade
        self.orders: OrderedDict[int, L3Order] = OrderedDict()
        self.total_quantity: int = 0

    @property
    def order_count(self) -> int:
        """Quantidade de ordens individuais no nível."""
        return len(self.orders)

    def add_order(self, order: L3Order) -> None:
        """Adiciona uma ordem ao final da fila FIFO."""
        if order.order_id in self.orders:
            raise DuplicateOrderError(f"Ordem {order.order_id} já existe no nível {self.price}")
        self.orders[order.order_id] = order
        self.total_quantity += order.quantity

    def remove_order(self, order_id: int) -> L3Order:
        """Remove a ordem do nível de preço."""
        if order_id not in self.orders:
            raise OrderNotFoundError(f"Ordem {order_id} não encontrada no nível {self.price}")
        order = self.orders.pop(order_id)
        self.total_quantity -= order.quantity
        return order

    def reduce_order_quantity(self, order_id: int, reduction: int) -> None:
        """Reduz a quantidade da ordem mantendo rigorosamente a prioridade na fila FIFO."""
        if order_id not in self.orders:
            raise OrderNotFoundError(f"Ordem {order_id} não encontrada no nível {self.price}")
        order = self.orders[order_id]
        if reduction <= 0 or reduction >= order.quantity:
            raise InvalidOrderError(f"Redução inválida ({reduction}) para quantidade {order.quantity}")
        order.quantity -= reduction
        self.total_quantity -= reduction

    def increase_order_quantity(
        self,
        order_id: int,
        increase: int,
        new_timestamp: datetime,
        new_sequence: int,
    ) -> None:
        """Aumenta a quantidade da ordem e a move para o FINAL da fila FIFO (perda de prioridade)."""
        if order_id not in self.orders:
            raise OrderNotFoundError(f"Ordem {order_id} não encontrada no nível {self.price}")
        order = self.orders[order_id]
        if increase <= 0:
            raise InvalidOrderError(f"Aumento inválido ({increase})")
        order.quantity += increase
        order.priority_timestamp = new_timestamp
        order.sequence_number = new_sequence
        self.total_quantity += increase
        # Move para o fim da fila em O(1)
        self.orders.move_to_end(order_id)

    def get_front_order(self) -> Optional[L3Order]:
        """Retorna a primeira ordem na fila FIFO de prioridade."""
        if not self.orders:
            return None
        return next(iter(self.orders.values()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "price": str(self.price),
            "side": self.side.value,
            "total_quantity": self.total_quantity,
            "order_count": self.order_count,
            "orders": [order.to_dict() for order in self.orders.values()],
        }


@dataclass(frozen=True)
class L2LevelView:
    """Visão Agregada de um Nível de Preço (Market By Price)."""

    price: Decimal
    total_quantity: int
    order_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "price": str(self.price),
            "total_quantity": self.total_quantity,
            "order_count": self.order_count,
        }


@dataclass(frozen=True)
class BBO:
    """Melhor Oferta de Compra e Venda (Top of Book - Best Bid and Offer)."""

    security_id: int
    symbol: str
    bid_price: Optional[Decimal]
    bid_quantity: int
    bid_order_count: int
    ask_price: Optional[Decimal]
    ask_quantity: int
    ask_order_count: int
    spread: Optional[Decimal]
    mid_price: Optional[Decimal]
    micro_price: Optional[Decimal]
    is_crossed: bool
    is_locked: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "security_id": self.security_id,
            "symbol": self.symbol,
            "bid_price": str(self.bid_price) if self.bid_price is not None else None,
            "bid_quantity": self.bid_quantity,
            "bid_order_count": self.bid_order_count,
            "ask_price": str(self.ask_price) if self.ask_price is not None else None,
            "ask_quantity": self.ask_quantity,
            "ask_order_count": self.ask_order_count,
            "spread": str(self.spread) if self.spread is not None else None,
            "mid_price": str(self.mid_price) if self.mid_price is not None else None,
            "micro_price": str(self.micro_price) if self.micro_price is not None else None,
            "is_crossed": self.is_crossed,
            "is_locked": self.is_locked,
        }


@dataclass(frozen=True)
class TradeRecord:
    """Registro Histórico de Negócio Executado."""

    trade_id: str
    order_id: int
    security_id: int
    symbol: str
    side: OrderSide
    price: Decimal
    quantity: int
    timestamp: datetime
    sequence_number: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "order_id": self.order_id,
            "security_id": self.security_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "price": str(self.price),
            "quantity": self.quantity,
            "timestamp": self.timestamp.isoformat(),
            "sequence_number": self.sequence_number,
        }


@dataclass
class ConservationLedger:
    """Livro Razão de Auditoria Contábil de Lotes do Book."""

    lots_added_initial: int = 0
    lots_added_modify: int = 0
    lots_executed: int = 0
    lots_canceled_partial: int = 0
    lots_canceled_full: int = 0
    lots_canceled_modify: int = 0
    lots_deleted: int = 0

    @property
    def total_lots_added(self) -> int:
        """Volume acumulado total inserido no livro."""
        return self.lots_added_initial + self.lots_added_modify

    @property
    def total_lots_executed(self) -> int:
        """Volume acumulado total consumido por execuções."""
        return self.lots_executed

    @property
    def total_lots_canceled(self) -> int:
        """Volume acumulado total expurgado por cancelamentos, reduções e exclusões."""
        return (
            self.lots_canceled_partial
            + self.lots_canceled_full
            + self.lots_canceled_modify
            + self.lots_deleted
        )

    @property
    def expected_current_lots(self) -> int:
        """Equação de Conservação: Lotes_Esperados == Adicionados - Executados - Cancelados."""
        return self.total_lots_added - self.total_lots_executed - self.total_lots_canceled

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lots_added_initial": self.lots_added_initial,
            "lots_added_modify": self.lots_added_modify,
            "total_lots_added": self.total_lots_added,
            "lots_executed": self.lots_executed,
            "total_lots_executed": self.total_lots_executed,
            "lots_canceled_partial": self.lots_canceled_partial,
            "lots_canceled_full": self.lots_canceled_full,
            "lots_canceled_modify": self.lots_canceled_modify,
            "lots_deleted": self.lots_deleted,
            "total_lots_canceled": self.total_lots_canceled,
            "expected_current_lots": self.expected_current_lots,
        }


# ---------------------------------------------------------------------------
# Motor de Reconstituição Determinística de Livro L3 (MarketByOrderL3Book)
# ---------------------------------------------------------------------------

class MarketByOrderL3Book:
    """Motor de Reconstituição Determinística do Livro de Ofertas L3 (Market By Order).

    Mantém a integridade de ordens individuais nos lados Compra (Bids) e Venda (Asks),
    com prioridade de fila FIFO em cada nível de preço, cálculo de BBO e auditoria
    formal de invariantes de conservação e não-cruzamento.
    """

    def __init__(
        self,
        security_id: int,
        symbol: str = "",
        trading_mode: TradingMode = TradingMode.CONTINUOUS,
    ) -> None:
        self.security_id: int = security_id
        self.symbol: str = symbol or f"SEC_{security_id}"
        self.trading_mode: TradingMode = trading_mode

        # Níveis de Preço: Preço -> PriceLevel
        self._bids: Dict[Decimal, PriceLevel] = {}
        self._asks: Dict[Decimal, PriceLevel] = {}

        # Índice Global de Ordens por OrderID: order_id -> L3Order
        self._orders: Dict[int, L3Order] = {}

        # Histórico de Execuções
        self._trades: List[TradeRecord] = []

        # Livro Razão Contábil de Lotes
        self._ledger: ConservationLedger = ConservationLedger()

        # Metadados de Sequência e Tempo
        self.last_sequence_number: int = 0
        self.last_event_timestamp: Optional[datetime] = None
        self.events_processed: int = 0

    # -----------------------------------------------------------------------
    # Propriedades de Acesso
    # -----------------------------------------------------------------------

    @property
    def total_orders_count(self) -> int:
        """Contagem total de ordens ativas no book."""
        return len(self._orders)

    @property
    def total_current_lots(self) -> int:
        """Volume total somado de todas as ordens ativas no book."""
        return sum(order.quantity for order in self._orders.values())

    @property
    def ledger(self) -> ConservationLedger:
        """Retorna o livro razão contábil de conservação."""
        return self._ledger

    def set_trading_mode(self, mode: TradingMode) -> None:
        """Altera o modo de negociação da sessão (ex: CONTINUOUS vs AUCTION)."""
        self.trading_mode = mode

    def clear(self) -> None:
        """Reinicia integralmente o livro de ofertas."""
        self._bids.clear()
        self._asks.clear()
        self._orders.clear()
        self._trades.clear()
        self._ledger = ConservationLedger()
        self.last_sequence_number = 0
        self.last_event_timestamp = None
        self.events_processed = 0

    # -----------------------------------------------------------------------
    # Aplicação de Eventos MBO
    # -----------------------------------------------------------------------

    def apply_order_add(
        self,
        order_id: int,
        side: Union[OrderSide, str],
        price: Union[Decimal, float, int, str],
        quantity: int,
        sequence_number: int,
        timestamp: Optional[datetime] = None,
        participant_id: Optional[str] = None,
    ) -> L3Order:
        """Processa a inclusão de uma nova ordem no livro L3 (OrderAdd)."""
        side_enum = OrderSide.from_code(side)
        dec_price = to_decimal_price(price)
        ts = timestamp or datetime.now(timezone.utc)

        if order_id in self._orders:
            raise DuplicateOrderError(
                f"Ordem {order_id} já cadastrada no ativo {self.symbol} (SecurityID: {self.security_id})"
            )
        if quantity <= 0:
            raise InvalidOrderError(f"Quantidade da ordem {order_id} deve ser > 0: {quantity}")
        if dec_price <= 0:
            raise InvalidOrderError(f"Preço da ordem {order_id} deve ser > 0: {dec_price}")

        order = L3Order(
            order_id=order_id,
            security_id=self.security_id,
            symbol=self.symbol,
            side=side_enum,
            price=dec_price,
            quantity=quantity,
            initial_quantity=quantity,
            priority_timestamp=ts,
            sequence_number=sequence_number,
            participant_id=participant_id,
        )

        side_levels = self._bids if side_enum == OrderSide.BID else self._asks
        if dec_price not in side_levels:
            side_levels[dec_price] = PriceLevel(price=dec_price, side=side_enum)

        side_levels[dec_price].add_order(order)
        self._orders[order_id] = order

        # Atualizações contábeis e de sequência
        self._ledger.lots_added_initial += quantity
        self.last_sequence_number = max(self.last_sequence_number, sequence_number)
        self.last_event_timestamp = ts
        self.events_processed += 1

        # Validação fail-safe imediata de não cruzamento em mercado contínuo
        if self.trading_mode == TradingMode.CONTINUOUS and self.is_crossed():
            bbo = self.get_bbo()
            raise CrossedBookError(
                f"Book cruzou após OrderAdd({order_id}, {side_enum.value}, {dec_price}): "
                f"BestBid={bbo.bid_price} >= BestAsk={bbo.ask_price}",
                law_code="FL-091-CROSSED-BOOK",
                details=bbo.to_dict(),
            )

        return order

    def apply_order_modify(
        self,
        order_id: int,
        new_quantity: int,
        new_price: Optional[Union[Decimal, float, int, str]] = None,
        sequence_number: int = 0,
        timestamp: Optional[datetime] = None,
    ) -> L3Order:
        """Processa a alteração de quantidade ou preço de uma ordem (OrderModify).

        Regras Estritas de Microestrutura FIFO B3:
        1. Alteração de Preço:
           - Remove do nível antigo e insere no novo nível de preço ao FINAL da fila FIFO (perde prioridade).
           - Quantidade maior: credita lots_added_modify.
           - Quantidade menor: debita lots_canceled_modify.
        2. Mesmo Preço com Redução de Volume:
           - Reduz o volume in-place MANTENDO a prioridade na fila FIFO.
           - Registra o cancelamento parcial em lots_canceled_modify.
        3. Mesmo Preço com Aumento de Volume:
           - Aumenta o volume e MOVE A ORDEM PARA O FINAL DA FILA (perde a prioridade temporal).
           - Registra o incremento em lots_added_modify.
        4. Mesmo Preço e Mesma Quantidade:
           - No-op na fila, apenas atualiza sequência e timestamp se fornecidos.
        """
        if order_id not in self._orders:
            raise OrderNotFoundError(f"Ordem {order_id} não encontrada para modificação.")
        if new_quantity <= 0:
            raise InvalidOrderError(f"new_quantity deve ser estritamente > 0: {new_quantity}")

        order = self._orders[order_id]
        old_price = order.price
        old_qty = order.quantity
        side_levels = self._bids if order.side == OrderSide.BID else self._asks
        old_level = side_levels[old_price]
        ts = timestamp or datetime.now(timezone.utc)
        seq = sequence_number or (self.last_sequence_number + 1)

        dec_new_price = to_decimal_price(new_price) if new_price is not None else old_price
        if dec_new_price <= 0:
            raise InvalidOrderError(f"Novo preço inválido: {dec_new_price}")

        # Caso 1: Alteração de Preço
        if dec_new_price != old_price:
            # Remove da fila do preço antigo
            old_level.remove_order(order_id)
            if old_level.order_count == 0:
                del side_levels[old_price]

            # Ajuste de conservação para delta de volume
            if new_quantity > old_qty:
                self._ledger.lots_added_modify += (new_quantity - old_qty)
            elif new_quantity < old_qty:
                self._ledger.lots_canceled_modify += (old_qty - new_quantity)

            # Atualiza ordem e insere no novo nível ao final da fila
            order.price = dec_new_price
            order.quantity = new_quantity
            order.priority_timestamp = ts
            order.sequence_number = seq

            if dec_new_price not in side_levels:
                side_levels[dec_new_price] = PriceLevel(price=dec_new_price, side=order.side)
            side_levels[dec_new_price].add_order(order)

        # Caso 2: Mesmo Preço com Redução de Volume (Mantém Prioridade FIFO)
        elif new_quantity < old_qty:
            delta = old_qty - new_quantity
            old_level.reduce_order_quantity(order_id, delta)
            self._ledger.lots_canceled_modify += delta
            # timestamp de prioridade é preservado!

        # Caso 3: Mesmo Preço com Aumento de Volume (Perde Prioridade FIFO)
        elif new_quantity > old_qty:
            delta = new_quantity - old_qty
            old_level.increase_order_quantity(order_id, delta, new_timestamp=ts, new_sequence=seq)
            self._ledger.lots_added_modify += delta

        # Caso 4: Mesma quantidade e mesmo preço (No-op com log)
        else:
            pass

        self.last_sequence_number = max(self.last_sequence_number, seq)
        self.last_event_timestamp = ts
        self.events_processed += 1

        if self.trading_mode == TradingMode.CONTINUOUS and self.is_crossed():
            bbo = self.get_bbo()
            raise CrossedBookError(
                f"Book cruzou após OrderModify({order_id}): BestBid={bbo.bid_price} >= BestAsk={bbo.ask_price}",
                law_code="FL-091-CROSSED-BOOK",
                details=bbo.to_dict(),
            )

        return order

    def apply_order_cancel(
        self,
        order_id: int,
        canceled_quantity: Optional[int] = None,
        sequence_number: int = 0,
        timestamp: Optional[datetime] = None,
        reason: Optional[str] = None,
    ) -> Optional[L3Order]:
        """Processa cancelamento parcial ou total de uma ordem (OrderCancel).

        - Se canceled_quantity for None ou >= order.quantity: cancelamento total (ordem removida).
        - Se canceled_quantity < order.quantity: cancelamento parcial (prioridade mantida).
        """
        if order_id not in self._orders:
            raise OrderNotFoundError(f"Ordem {order_id} não encontrada para cancelamento.")

        order = self._orders[order_id]
        side_levels = self._bids if order.side == OrderSide.BID else self._asks
        level = side_levels[order.price]
        ts = timestamp or datetime.now(timezone.utc)
        seq = sequence_number or (self.last_sequence_number + 1)

        if canceled_quantity is not None and canceled_quantity <= 0:
            raise InvalidOrderError(f"canceled_quantity inválida: {canceled_quantity}")

        # Cancelamento Total
        if canceled_quantity is None or canceled_quantity >= order.quantity:
            qty_canceled = order.quantity
            level.remove_order(order_id)
            if level.order_count == 0:
                del side_levels[order.price]
            del self._orders[order_id]
            self._ledger.lots_canceled_full += qty_canceled
            ret_order = None
        # Cancelamento Parcial (mantém prioridade FIFO)
        else:
            level.reduce_order_quantity(order_id, canceled_quantity)
            self._ledger.lots_canceled_partial += canceled_quantity
            ret_order = order

        self.last_sequence_number = max(self.last_sequence_number, seq)
        self.last_event_timestamp = ts
        self.events_processed += 1

        return ret_order

    def apply_order_execute(
        self,
        order_id: int,
        executed_quantity: int,
        execution_price: Optional[Union[Decimal, float, int, str]] = None,
        trade_id: Optional[str] = None,
        aggressor_side: Optional[Union[OrderSide, str]] = None,
        sequence_number: int = 0,
        timestamp: Optional[datetime] = None,
    ) -> TradeRecord:
        """Processa a execução de negócio contra uma ordem passiva no book (OrderExecute).

        - Reduz o volume da ordem passiva pelo montante executado.
        - Se o volume restante zerar, a ordem é removida do book.
        - Se restar volume, mantém rigorosamente sua posição na fila FIFO.
        - Gera o registro TradeRecord e atualiza o ledger contábil.
        """
        if order_id not in self._orders:
            raise OrderNotFoundError(f"Ordem {order_id} não encontrada para execução.")
        if executed_quantity <= 0:
            raise InvalidOrderError(f"executed_quantity deve ser > 0: {executed_quantity}")

        order = self._orders[order_id]
        if executed_quantity > order.quantity:
            raise InvalidOrderError(
                f"Execução de {executed_quantity} excede volume disponível {order.quantity} da ordem {order_id}"
            )

        exec_price = to_decimal_price(execution_price) if execution_price is not None else order.price
        ts = timestamp or datetime.now(timezone.utc)
        seq = sequence_number or (self.last_sequence_number + 1)
        t_id = trade_id or f"TRD_{self.security_id}_{seq}_{len(self._trades) + 1}"
        agg_side_enum = OrderSide.from_code(aggressor_side) if aggressor_side else order.side.opposite

        side_levels = self._bids if order.side == OrderSide.BID else self._asks
        level = side_levels[order.price]

        # Consome volume
        if executed_quantity == order.quantity:
            # Execução Total da Ordem
            level.remove_order(order_id)
            if level.order_count == 0:
                del side_levels[order.price]
            del self._orders[order_id]
        else:
            # Execução Parcial (mantém prioridade FIFO)
            level.reduce_order_quantity(order_id, executed_quantity)

        # Atualizações contábeis
        self._ledger.lots_executed += executed_quantity
        self.last_sequence_number = max(self.last_sequence_number, seq)
        self.last_event_timestamp = ts
        self.events_processed += 1

        trade = TradeRecord(
            trade_id=t_id,
            order_id=order_id,
            security_id=self.security_id,
            symbol=self.symbol,
            side=order.side,
            price=exec_price,
            quantity=executed_quantity,
            timestamp=ts,
            sequence_number=seq,
        )
        self._trades.append(trade)
        return trade

    def apply_order_delete(
        self,
        order_id: int,
        sequence_number: int = 0,
        timestamp: Optional[datetime] = None,
        reason: Optional[str] = "DELETED",
    ) -> L3Order:
        """Processa a exclusão definitiva de uma ordem do book (OrderDelete)."""
        if order_id not in self._orders:
            raise OrderNotFoundError(f"Ordem {order_id} não encontrada para exclusão.")

        order = self._orders[order_id]
        side_levels = self._bids if order.side == OrderSide.BID else self._asks
        level = side_levels[order.price]
        ts = timestamp or datetime.now(timezone.utc)
        seq = sequence_number or (self.last_sequence_number + 1)

        qty_deleted = order.quantity
        level.remove_order(order_id)
        if level.order_count == 0:
            del side_levels[order.price]
        del self._orders[order_id]

        self._ledger.lots_deleted += qty_deleted
        self.last_sequence_number = max(self.last_sequence_number, seq)
        self.last_event_timestamp = ts
        self.events_processed += 1

        return order

    def apply_event(self, event: MboEvent) -> Any:
        """Despacha e aplica deterministicamente qualquer evento MBO tipado."""
        if isinstance(event, OrderAddEvent):
            return self.apply_order_add(
                order_id=event.order_id,
                side=event.side,
                price=event.price,
                quantity=event.quantity,
                sequence_number=event.sequence_number,
                timestamp=event.timestamp,
                participant_id=event.participant_id,
            )
        if isinstance(event, OrderModifyEvent):
            return self.apply_order_modify(
                order_id=event.order_id,
                new_quantity=event.new_quantity,
                new_price=event.new_price,
                sequence_number=event.sequence_number,
                timestamp=event.timestamp,
            )
        if isinstance(event, OrderCancelEvent):
            return self.apply_order_cancel(
                order_id=event.order_id,
                canceled_quantity=event.canceled_quantity,
                sequence_number=event.sequence_number,
                timestamp=event.timestamp,
                reason=event.reason,
            )
        if isinstance(event, OrderExecuteEvent):
            return self.apply_order_execute(
                order_id=event.order_id,
                executed_quantity=event.executed_quantity,
                execution_price=event.execution_price,
                trade_id=event.trade_id,
                aggressor_side=event.aggressor_side,
                sequence_number=event.sequence_number,
                timestamp=event.timestamp,
            )
        if isinstance(event, OrderDeleteEvent):
            return self.apply_order_delete(
                order_id=event.order_id,
                sequence_number=event.sequence_number,
                timestamp=event.timestamp,
                reason=event.reason,
            )
        raise MboBookError(f"Tipo de evento MBO não reconhecido: {type(event)}")

    # -----------------------------------------------------------------------
    # Consultas de Estado: BBO, L2 Depth e L3 Orders
    # -----------------------------------------------------------------------

    def get_best_bid(self) -> Optional[PriceLevel]:
        """Retorna o melhor nível de compra (maior preço)."""
        if not self._bids:
            return None
        max_price = max(self._bids.keys())
        return self._bids[max_price]

    def get_best_ask(self) -> Optional[PriceLevel]:
        """Retorna o melhor nível de venda (menor preço)."""
        if not self._asks:
            return None
        min_price = min(self._asks.keys())
        return self._asks[min_price]

    def is_crossed(self) -> bool:
        """Verifica se o livro está cruzado (Best Bid > Best Ask)."""
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid is None or best_ask is None:
            return False
        return best_bid.price > best_ask.price

    def is_locked(self) -> bool:
        """Verifica se o livro está travado (Best Bid == Best Ask)."""
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid is None or best_ask is None:
            return False
        return best_bid.price == best_ask.price

    def get_bbo(self) -> BBO:
        """Calcula instantaneamente o Top of Book (BBO - Best Bid and Offer)."""
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()

        bid_price = best_bid.price if best_bid else None
        bid_qty = best_bid.total_quantity if best_bid else 0
        bid_count = best_bid.order_count if best_bid else 0

        ask_price = best_ask.price if best_ask else None
        ask_qty = best_ask.total_quantity if best_ask else 0
        ask_count = best_ask.order_count if best_ask else 0

        spread: Optional[Decimal] = None
        mid_price: Optional[Decimal] = None
        micro_price: Optional[Decimal] = None

        if bid_price is not None and ask_price is not None:
            spread = ask_price - bid_price
            mid_price = (bid_price + ask_price) / Decimal("2")
            total_bbo_vol = bid_qty + ask_qty
            if total_bbo_vol > 0:
                # Micro-preço ponderado por volume invertido do topo (W_mid = (P_bid * V_ask + P_ask * V_bid) / (V_bid + V_ask))
                micro_price = (
                    (bid_price * Decimal(ask_qty)) + (ask_price * Decimal(bid_qty))
                ) / Decimal(total_bbo_vol)

        crossed = self.is_crossed()
        locked = self.is_locked()

        return BBO(
            security_id=self.security_id,
            symbol=self.symbol,
            bid_price=bid_price,
            bid_quantity=bid_qty,
            bid_order_count=bid_count,
            ask_price=ask_price,
            ask_quantity=ask_qty,
            ask_order_count=ask_count,
            spread=spread,
            mid_price=mid_price,
            micro_price=micro_price,
            is_crossed=crossed,
            is_locked=locked,
        )

    def get_l2_depth(self, depth: int = 10) -> Dict[str, List[Dict[str, Any]]]:
        """Calcula a profundidade L2 agregada por nível de preço (Market By Price).

        - Bids: ordenados decrescentemente por preço (melhores preços primeiro).
        - Asks: ordenados crescentemente por preço (melhores preços primeiro).
        """
        sorted_bid_prices = sorted(self._bids.keys(), reverse=True)[:depth]
        sorted_ask_prices = sorted(self._asks.keys())[:depth]

        bids_l2 = [
            L2LevelView(
                price=p,
                total_quantity=self._bids[p].total_quantity,
                order_count=self._bids[p].order_count,
            ).to_dict()
            for p in sorted_bid_prices
        ]

        asks_l2 = [
            L2LevelView(
                price=p,
                total_quantity=self._asks[p].total_quantity,
                order_count=self._asks[p].order_count,
            ).to_dict()
            for p in sorted_ask_prices
        ]

        return {
            "bids": bids_l2,
            "asks": asks_l2,
        }

    def get_l3_orders(
        self,
        side: Optional[Union[OrderSide, str]] = None,
        price: Optional[Union[Decimal, float, int, str]] = None,
    ) -> List[L3Order]:
        """Retorna ordens L3 respeitando a ordenação estrita de prioridade preço-tempo."""
        result: List[L3Order] = []
        target_side = OrderSide.from_code(side) if side else None
        target_price = to_decimal_price(price) if price is not None else None

        # Bids (Preço decrescente -> FIFO)
        if target_side in (None, OrderSide.BID):
            for p in sorted(self._bids.keys(), reverse=True):
                if target_price is not None and p != target_price:
                    continue
                result.extend(self._bids[p].orders.values())

        # Asks (Preço crescente -> FIFO)
        if target_side in (None, OrderSide.ASK):
            for p in sorted(self._asks.keys()):
                if target_price is not None and p != target_price:
                    continue
                result.extend(self._asks[p].orders.values())

        return result

    def get_order(self, order_id: int) -> Optional[L3Order]:
        """Retorna uma ordem ativa pelo OrderID ou None se não existir."""
        return self._orders.get(order_id)

    def get_price_level(
        self,
        side: Union[OrderSide, str],
        price: Union[Decimal, float, int, str],
    ) -> Optional[PriceLevel]:
        """Retorna o PriceLevel para determinado lado e preço, se existir."""
        side_enum = OrderSide.from_code(side)
        dec_price = to_decimal_price(price)
        levels = self._bids if side_enum == OrderSide.BID else self._asks
        return levels.get(dec_price)

    # -----------------------------------------------------------------------
    # Verificação Rigorosa de Invariantes Formais (FL-091)
    # -----------------------------------------------------------------------

    def verify_invariants(self, raise_on_error: bool = False) -> Dict[str, bool]:
        """Audita rigorosamente as leis formais de conservação matemática e microestrutura B3:

        1. order_conservation:
           Lotes_Atuais == Lotes_Adicionados - Lotes_Executados - Lotes_Cancelados.
        2. no_crossed_book_continuous:
           Em mercado contínuo (CONTINUOUS), Best_Bid < Best_Ask (se ambos existirem).
        3. price_monotonicity:
           Níveis de Bid são estritamente decrescentes e Asks são estritamente crescentes.
        4. strict_fifo_priority:
           Dentro de cada nível de preço, a ordem na fila é monotonicamente crescente em sequência/tempo.
        5. level_quantity_consistency:
           O volume total de cada nível é idêntico à soma das ordens contidas nele.
        6. index_integrity:
           O índice global de ordens tem cardinalidade idêntica à soma de todas as ordens dos níveis.
        """
        results: Dict[str, bool] = {}

        # 1. Conservação Formal de Lotes
        current_lots = self.total_current_lots
        expected_lots = self._ledger.expected_current_lots
        conservation_ok = (current_lots == expected_lots)
        results["order_conservation"] = conservation_ok
        if not conservation_ok and raise_on_error:
            raise MboInvariantViolationError(
                f"Quebra da Lei de Conservação de Lotes: Atual={current_lots} != Esperado={expected_lots}",
                law_code="LAW-091-CONSERVATION",
                details={
                    "current_lots": current_lots,
                    "expected_lots": expected_lots,
                    "ledger": self._ledger.to_dict(),
                },
            )

        # 2. Invariante de Não Cruzamento em Mercado Contínuo
        if self.trading_mode == TradingMode.CONTINUOUS:
            no_crossed = not (self.is_crossed() or self.is_locked())
        else:
            no_crossed = True  # Em leilão, cruzamento é permitido e apura Preço Teórico
        results["no_crossed_book_continuous"] = no_crossed
        if not no_crossed and raise_on_error:
            bbo = self.get_bbo()
            raise CrossedBookError(
                f"Livro cruzado ou travado em mercado contínuo: Bid={bbo.bid_price} >= Ask={bbo.ask_price}",
                law_code="LAW-091-NO-CROSSED-CONTINUOUS",
                details=bbo.to_dict(),
            )

        # 3. Monotonicidade Estrita de Preços
        bid_prices = list(self._bids.keys())
        ask_prices = list(self._asks.keys())
        bids_sorted_desc = sorted(bid_prices, reverse=True)
        asks_sorted_asc = sorted(ask_prices)

        # Verifica se não há duplicações e se a ordem das chaves pode ser mantida de forma estrita
        bid_monotonic = len(bid_prices) == len(set(bid_prices))
        ask_monotonic = len(ask_prices) == len(set(ask_prices))
        price_mono_ok = bid_monotonic and ask_monotonic
        results["price_monotonicity"] = price_mono_ok
        if not price_mono_ok and raise_on_error:
            raise MboInvariantViolationError(
                "Níveis de preços não são estritamente unívocos ou violam monotonicidade.",
                law_code="LAW-091-PRICE-MONOTONICITY",
            )

        # 4. Prioridade Estrita Preço-Tempo (FIFO)
        fifo_ok = True
        for side_levels in (self._bids.values(), self._asks.values()):
            for level in side_levels:
                orders_list = list(level.orders.values())
                for i in range(len(orders_list) - 1):
                    o1 = orders_list[i]
                    o2 = orders_list[i + 1]
                    # Sequência temporal ou número sequencial não pode decrescer na fila
                    if o1.priority_timestamp > o2.priority_timestamp:
                        fifo_ok = False
                        break
                if not fifo_ok:
                    break
            if not fifo_ok:
                break
        results["strict_fifo_priority"] = fifo_ok
        if not fifo_ok and raise_on_error:
            raise MboInvariantViolationError(
                "Fila FIFO violada: ordem posterior possui timestamp de prioridade inferior à anterior.",
                law_code="LAW-091-FIFO-PRIORITY",
            )

        # 5. Consistência de Volume dos Níveis
        qty_consistent = True
        for side_levels in (self._bids.values(), self._asks.values()):
            for level in side_levels:
                sum_orders = sum(o.quantity for o in level.orders.values())
                if sum_orders != level.total_quantity:
                    qty_consistent = False
                    break
            if not qty_consistent:
                break
        results["level_quantity_consistency"] = qty_consistent
        if not qty_consistent and raise_on_error:
            raise MboInvariantViolationError(
                "Volume total registrado no PriceLevel difere da soma das ordens ativas.",
                law_code="LAW-091-LEVEL-QUANTITY-CONSISTENCY",
            )

        # 6. Integridade do Índice Global
        total_orders_in_levels = sum(
            len(lvl.orders) for lvl in list(self._bids.values()) + list(self._asks.values())
        )
        index_ok = (total_orders_in_levels == len(self._orders))
        results["index_integrity"] = index_ok
        if not index_ok and raise_on_error:
            raise MboInvariantViolationError(
                f"Índice global de ordens ({len(self._orders)}) diverge da soma nos níveis ({total_orders_in_levels}).",
                law_code="LAW-091-INDEX-INTEGRITY",
            )

        return results

    # -----------------------------------------------------------------------
    # Serialização e Snapshots
    # -----------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Gera um snapshot completo do estado do livro L3 e L2."""
        return {
            "security_id": self.security_id,
            "symbol": self.symbol,
            "trading_mode": self.trading_mode.value,
            "last_sequence_number": self.last_sequence_number,
            "last_event_timestamp": self.last_event_timestamp.isoformat() if self.last_event_timestamp else None,
            "total_orders_count": self.total_orders_count,
            "total_current_lots": self.total_current_lots,
            "bbo": self.get_bbo().to_dict(),
            "l2_depth": self.get_l2_depth(depth=20),
            "ledger": self._ledger.to_dict(),
            "trades_count": len(self._trades),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado integral do motor L3 para dicionário."""
        return {
            "security_id": self.security_id,
            "symbol": self.symbol,
            "trading_mode": self.trading_mode.value,
            "last_sequence_number": self.last_sequence_number,
            "last_event_timestamp": self.last_event_timestamp.isoformat() if self.last_event_timestamp else None,
            "total_orders_count": self.total_orders_count,
            "total_current_lots": self.total_current_lots,
            "events_processed": self.events_processed,
            "bbo": self.get_bbo().to_dict(),
            "invariants": self.verify_invariants(raise_on_error=False),
            "ledger": self._ledger.to_dict(),
            "bids_levels_count": len(self._bids),
            "asks_levels_count": len(self._asks),
            "trades": [trade.to_dict() for trade in self._trades],
        }


# ---------------------------------------------------------------------------
# Codec Binário Bidirecional (ITCH / SBE B3 UMDF Market Data)
# ---------------------------------------------------------------------------

class MboBinaryCodec:
    """Codec Binário Little-Endian de Alta Performance para Mensagens MBO.

    Estrutura do Cabeçalho Padronizado (20 bytes):
    - magic (uint16): 0xB34D ("B3M")
    - version (uint8): 1
    - msg_type (char / uint8): 'A', 'M', 'X', 'E', 'D'
    - sequence_number (uint64): Sequencial global determinístico
    - timestamp_ns (uint64): Nanosegundos UTC Unix Epoch

    Formatos de Payload:
    - OrderAdd ('A' / 41 bytes payload):
      order_id (uint64), security_id (uint64), side (char 'B'/'S'), price_scaled (int64), quantity (uint64)
    - OrderModify ('M' / 24 bytes payload):
      order_id (uint64), price_scaled (int64), new_quantity (uint64)
    - OrderCancel ('X' / 16 bytes payload):
      order_id (uint64), canceled_quantity (uint64)
    - OrderExecute ('E' / 32 bytes payload):
      order_id (uint64), executed_quantity (uint64), execution_price_scaled (int64), trade_id_num (uint64)
    - OrderDelete ('D' / 10 bytes payload):
      order_id (uint64), reason_code (uint16)
    """

    HEADER_FORMAT: str = "<HBBQQ"
    HEADER_SIZE: int = struct.calcsize(HEADER_FORMAT)  # 20 bytes

    PAYLOAD_FORMAT_ADD: str = "<QQcqq"
    PAYLOAD_FORMAT_MODIFY: str = "<QqQ"
    PAYLOAD_FORMAT_CANCEL: str = "<QQ"
    PAYLOAD_FORMAT_EXECUTE: str = "<QQqQ"
    PAYLOAD_FORMAT_DELETE: str = "<QH"

    PAYLOAD_SIZE_ADD: int = struct.calcsize(PAYLOAD_FORMAT_ADD)        # 33 bytes
    PAYLOAD_SIZE_MODIFY: int = struct.calcsize(PAYLOAD_FORMAT_MODIFY)    # 24 bytes
    PAYLOAD_SIZE_CANCEL: int = struct.calcsize(PAYLOAD_FORMAT_CANCEL)    # 16 bytes
    PAYLOAD_SIZE_EXECUTE: int = struct.calcsize(PAYLOAD_FORMAT_EXECUTE)  # 32 bytes
    PAYLOAD_SIZE_DELETE: int = struct.calcsize(PAYLOAD_FORMAT_DELETE)    # 10 bytes

    @classmethod
    def datetime_to_nanos(cls, dt: datetime) -> int:
        """Converte datetime UTC para nanosegundos desde Unix Epoch."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1_000_000_000)

    @classmethod
    def nanos_to_datetime(cls, nanos: int) -> datetime:
        """Converte nanosegundos Unix Epoch para datetime UTC."""
        return datetime.fromtimestamp(nanos / 1_000_000_000, tz=timezone.utc)

    @classmethod
    def encode_order_add(cls, event: OrderAddEvent) -> bytes:
        """Serializa OrderAddEvent em bytes binários Little-Endian."""
        header = struct.pack(
            cls.HEADER_FORMAT,
            MAGIC_HEADER_B3_MBO,
            PROTOCOL_VERSION_MBO,
            ord(b"A"),
            event.sequence_number,
            cls.datetime_to_nanos(event.timestamp),
        )
        payload = struct.pack(
            cls.PAYLOAD_FORMAT_ADD,
            event.order_id,
            event.security_id,
            event.side.byte_code,
            scale_price_to_int(event.price),
            event.quantity,
        )
        return header + payload

    @classmethod
    def encode_order_modify(cls, event: OrderModifyEvent) -> bytes:
        """Serializa OrderModifyEvent em bytes binários Little-Endian."""
        ts = event.timestamp or datetime.now(timezone.utc)
        header = struct.pack(
            cls.HEADER_FORMAT,
            MAGIC_HEADER_B3_MBO,
            PROTOCOL_VERSION_MBO,
            ord(b"M"),
            event.sequence_number,
            cls.datetime_to_nanos(ts),
        )
        price_scaled = scale_price_to_int(event.new_price) if event.new_price is not None else 0
        payload = struct.pack(
            cls.PAYLOAD_FORMAT_MODIFY,
            event.order_id,
            price_scaled,
            event.new_quantity,
        )
        return header + payload

    @classmethod
    def encode_order_cancel(cls, event: OrderCancelEvent) -> bytes:
        """Serializa OrderCancelEvent em bytes binários Little-Endian."""
        ts = event.timestamp or datetime.now(timezone.utc)
        header = struct.pack(
            cls.HEADER_FORMAT,
            MAGIC_HEADER_B3_MBO,
            PROTOCOL_VERSION_MBO,
            ord(b"X"),
            event.sequence_number,
            cls.datetime_to_nanos(ts),
        )
        qty = event.canceled_quantity if event.canceled_quantity is not None else 0
        payload = struct.pack(
            cls.PAYLOAD_FORMAT_CANCEL,
            event.order_id,
            qty,
        )
        return header + payload

    @classmethod
    def encode_order_execute(cls, event: OrderExecuteEvent) -> bytes:
        """Serializa OrderExecuteEvent em bytes binários Little-Endian."""
        ts = event.timestamp or datetime.now(timezone.utc)
        header = struct.pack(
            cls.HEADER_FORMAT,
            MAGIC_HEADER_B3_MBO,
            PROTOCOL_VERSION_MBO,
            ord(b"E"),
            event.sequence_number,
            cls.datetime_to_nanos(ts),
        )
        # Converte trade_id para hash numérico ou int se possível
        try:
            trade_num = int(event.trade_id.replace("TRD_", "").replace("_", ""))
        except (ValueError, AttributeError):
            trade_num = abs(hash(event.trade_id)) % (2**63 - 1)

        payload = struct.pack(
            cls.PAYLOAD_FORMAT_EXECUTE,
            event.order_id,
            event.executed_quantity,
            scale_price_to_int(event.execution_price),
            trade_num,
        )
        return header + payload

    @classmethod
    def encode_order_delete(cls, event: OrderDeleteEvent) -> bytes:
        """Serializa OrderDeleteEvent em bytes binários Little-Endian."""
        ts = event.timestamp or datetime.now(timezone.utc)
        header = struct.pack(
            cls.HEADER_FORMAT,
            MAGIC_HEADER_B3_MBO,
            PROTOCOL_VERSION_MBO,
            ord(b"D"),
            event.sequence_number,
            cls.datetime_to_nanos(ts),
        )
        payload = struct.pack(
            cls.PAYLOAD_FORMAT_DELETE,
            event.order_id,
            1,  # Reason code
        )
        return header + payload

    @classmethod
    def encode_event(cls, event: MboEvent) -> bytes:
        """Encapsula e codifica qualquer evento MBO tipado."""
        if isinstance(event, OrderAddEvent):
            return cls.encode_order_add(event)
        if isinstance(event, OrderModifyEvent):
            return cls.encode_order_modify(event)
        if isinstance(event, OrderCancelEvent):
            return cls.encode_order_cancel(event)
        if isinstance(event, OrderExecuteEvent):
            return cls.encode_order_execute(event)
        if isinstance(event, OrderDeleteEvent):
            return cls.encode_order_delete(event)
        raise MboCodecError(f"Tipo de evento não suportado para codificação: {type(event)}")

    @classmethod
    def decode_message(
        cls,
        data: bytes,
        offset: int = 0,
        default_security_id: int = 1,
        default_symbol: str = "PETR4",
    ) -> Tuple[MboEvent, int]:
        """Decodifica uma mensagem MBO binária a partir do buffer na posição offset.

        Retorna:
            Tupla (evento decodificado, novo offset após a mensagem).
        """
        remaining = len(data) - offset
        if remaining < cls.HEADER_SIZE:
            raise MboCodecError(
                f"Buffer truncado para cabeçalho MBO: esperado={cls.HEADER_SIZE}, disponível={remaining}"
            )

        magic, version, msg_char_code, seq_num, ts_nanos = struct.unpack_from(
            cls.HEADER_FORMAT, data, offset
        )
        if magic != MAGIC_HEADER_B3_MBO:
            raise MboCodecError(f"Magic header inválido: 0x{magic:04X} != 0x{MAGIC_HEADER_B3_MBO:04X}")

        timestamp = cls.nanos_to_datetime(ts_nanos)
        msg_char = chr(msg_char_code)
        payload_offset = offset + cls.HEADER_SIZE

        if msg_char == "A":
            if len(data) - payload_offset < cls.PAYLOAD_SIZE_ADD:
                raise MboCodecError("Buffer insuficiente para payload OrderAdd")
            order_id, sec_id, side_byte, price_scaled, qty = struct.unpack_from(
                cls.PAYLOAD_FORMAT_ADD, data, payload_offset
            )
            event = OrderAddEvent(
                order_id=order_id,
                security_id=sec_id,
                symbol=default_symbol,
                side=OrderSide.from_code(side_byte),
                price=unscale_int_to_price(price_scaled),
                quantity=qty,
                sequence_number=seq_num,
                timestamp=timestamp,
            )
            next_offset = payload_offset + cls.PAYLOAD_SIZE_ADD

        elif msg_char == "M":
            if len(data) - payload_offset < cls.PAYLOAD_SIZE_MODIFY:
                raise MboCodecError("Buffer insuficiente para payload OrderModify")
            order_id, price_scaled, qty = struct.unpack_from(
                cls.PAYLOAD_FORMAT_MODIFY, data, payload_offset
            )
            new_price = unscale_int_to_price(price_scaled) if price_scaled > 0 else None
            event = OrderModifyEvent(
                order_id=order_id,
                security_id=default_security_id,
                symbol=default_symbol,
                new_quantity=qty,
                new_price=new_price,
                sequence_number=seq_num,
                timestamp=timestamp,
            )
            next_offset = payload_offset + cls.PAYLOAD_SIZE_MODIFY

        elif msg_char == "X":
            if len(data) - payload_offset < cls.PAYLOAD_SIZE_CANCEL:
                raise MboCodecError("Buffer insuficiente para payload OrderCancel")
            order_id, canceled_qty = struct.unpack_from(
                cls.PAYLOAD_FORMAT_CANCEL, data, payload_offset
            )
            event = OrderCancelEvent(
                order_id=order_id,
                security_id=default_security_id,
                symbol=default_symbol,
                canceled_quantity=canceled_qty if canceled_qty > 0 else None,
                sequence_number=seq_num,
                timestamp=timestamp,
            )
            next_offset = payload_offset + cls.PAYLOAD_SIZE_CANCEL

        elif msg_char == "E":
            if len(data) - payload_offset < cls.PAYLOAD_SIZE_EXECUTE:
                raise MboCodecError("Buffer insuficiente para payload OrderExecute")
            order_id, exec_qty, price_scaled, trade_num = struct.unpack_from(
                cls.PAYLOAD_FORMAT_EXECUTE, data, payload_offset
            )
            event = OrderExecuteEvent(
                order_id=order_id,
                security_id=default_security_id,
                symbol=default_symbol,
                executed_quantity=exec_qty,
                execution_price=unscale_int_to_price(price_scaled),
                trade_id=f"TRD_{trade_num}",
                sequence_number=seq_num,
                timestamp=timestamp,
            )
            next_offset = payload_offset + cls.PAYLOAD_SIZE_EXECUTE

        elif msg_char == "D":
            if len(data) - payload_offset < cls.PAYLOAD_SIZE_DELETE:
                raise MboCodecError("Buffer insuficiente para payload OrderDelete")
            order_id, reason_code = struct.unpack_from(
                cls.PAYLOAD_FORMAT_DELETE, data, payload_offset
            )
            event = OrderDeleteEvent(
                order_id=order_id,
                security_id=default_security_id,
                symbol=default_symbol,
                sequence_number=seq_num,
                timestamp=timestamp,
                reason=f"REASON_{reason_code}",
            )
            next_offset = payload_offset + cls.PAYLOAD_SIZE_DELETE

        else:
            raise MboCodecError(f"Caractere de tipo de mensagem MBO não reconhecido: {msg_char}")

        return event, next_offset

    @classmethod
    def decode_stream(
        cls,
        data: bytes,
        default_security_id: int = 1,
        default_symbol: str = "PETR4",
    ) -> List[MboEvent]:
        """Decodifica stream contígua contendo múltiplos pacotes binários sequenciais."""
        events: List[MboEvent] = []
        offset = 0
        total_len = len(data)
        while offset < total_len:
            event, next_offset = cls.decode_message(
                data,
                offset=offset,
                default_security_id=default_security_id,
                default_symbol=default_symbol,
            )
            events.append(event)
            offset = next_offset
        return events


# ---------------------------------------------------------------------------
# Motor Orquestrador Multi-Ativo (MarketByOrderEngine)
# ---------------------------------------------------------------------------

class MarketByOrderEngine:
    """Motor de Orquestração e Processamento de Feeds MBO para Múltiplos Ativos."""

    def __init__(self) -> None:
        self.books: Dict[int, MarketByOrderL3Book] = {}
        self.symbol_to_id: Dict[str, int] = {}
        self.packets_processed: int = 0
        self.events_processed: int = 0

    def get_or_create_book(
        self,
        security_id: int,
        symbol: str = "",
        trading_mode: TradingMode = TradingMode.CONTINUOUS,
    ) -> MarketByOrderL3Book:
        """Recupera ou instancia o livro L3 para o instrumento especificado."""
        if security_id not in self.books:
            sym = symbol or f"SEC_{security_id}"
            book = MarketByOrderL3Book(security_id=security_id, symbol=sym, trading_mode=trading_mode)
            self.books[security_id] = book
            self.symbol_to_id[sym] = security_id
        return self.books[security_id]

    def get_book(self, security_id_or_symbol: Union[int, str]) -> Optional[MarketByOrderL3Book]:
        """Obtém o livro L3 por SecurityID ou Ticker."""
        if isinstance(security_id_or_symbol, int):
            return self.books.get(security_id_or_symbol)
        sec_id = self.symbol_to_id.get(str(security_id_or_symbol))
        return self.books.get(sec_id) if sec_id is not None else None

    def process_event(self, event: MboEvent) -> Any:
        """Encaminha o evento para o livro do ativo correspondente."""
        book = self.get_or_create_book(event.security_id, event.symbol)
        result = book.apply_event(event)
        self.events_processed += 1
        return result

    def process_binary_packet(
        self,
        data: bytes,
        default_security_id: int = 1,
        default_symbol: str = "PETR4",
    ) -> List[Any]:
        """Decodifica stream binária e processa cada mensagem no motor."""
        events = MboBinaryCodec.decode_stream(
            data,
            default_security_id=default_security_id,
            default_symbol=default_symbol,
        )
        results = []
        for ev in events:
            res = self.process_event(ev)
            results.append(res)
        self.packets_processed += 1
        return results

    def verify_all_invariants(self, raise_on_error: bool = False) -> Dict[int, Dict[str, bool]]:
        """Verifica as leis invariantes formais em todos os livros de ofertas gerenciados."""
        all_results: Dict[int, Dict[str, bool]] = {}
        for sec_id, book in self.books.items():
            all_results[sec_id] = book.verify_invariants(raise_on_error=raise_on_error)
        return all_results

    def to_dict(self) -> Dict[str, Any]:
        """Retorna o estado operacional e métricas de todos os livros gerenciados."""
        return {
            "total_books": len(self.books),
            "packets_processed": self.packets_processed,
            "events_processed": self.events_processed,
            "books": {sec_id: book.to_dict() for sec_id, book in self.books.items()},
        }
