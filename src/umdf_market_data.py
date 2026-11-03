"""Módulo de Market Data Feed em Tempo Real — B3 UMDF / SBE Multicast (FL-021).

Implementa o protocolo Unified Market Data Feed (UMDF) da B3 baseado em Simple Binary
Encoding (SBE) Little-Endian sobre Multicast UDP redundante (Feeds A e B).

Suporta:
- Decodificação zero-copy via struct (Little-Endian).
- SecurityStatus (TemplateId = 201).
- MarketDataSnapshotFullRefresh (TemplateId = 202) com livro L2 e L3.
- MarketDataIncrementalRefresh (TemplateId = 203) com inserções, alterações, cancelamentos e trades.
- Reconstrução de Livro de Ofertas L2 (Market by Price) e L3 (Market by Order).
- Consumidor resiliente UmdfStreamConsumer com descarte determinístico de duplicatas A/B e detecção de gaps.
- Zero dependências externas (Python stdlib).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import struct
from typing import Any, ClassVar, Optional, Union


# Constantes do Protocolo UMDF SBE
PRICE_SCALE: int = 100_000_000  # 10^8 (8 casas decimais)
DEFAULT_SCHEMA_ID: int = 1
DEFAULT_SCHEMA_VERSION: int = 1

# Template IDs Oficiais
TEMPLATE_SECURITY_STATUS: int = 201
TEMPLATE_MARKET_DATA_SNAPSHOT: int = 202
TEMPLATE_MARKET_DATA_INCREMENTAL: int = 203

# Formato e Tamanho do Message Header Canônico SBE (8 bytes)
HEADER_FORMAT: str = "<HHHH"
HEADER_SIZE: int = struct.calcsize(HEADER_FORMAT)  # 8 bytes

# Formato do GroupSizeEncoding (4 bytes)
GROUP_HEADER_FORMAT: str = "<HH"
GROUP_HEADER_SIZE: int = struct.calcsize(GROUP_HEADER_FORMAT)  # 4 bytes


class MDEntryType(IntEnum):
    """Tipo de Entrada no Livro de Market Data (FIX Tag 269)."""

    BID = 0  # Oferta de Compra
    OFFER = 1  # Oferta de Venda
    TRADE = 2  # Execução de Negócio
    INDEX_VALUE = 3  # Valor de Índice
    OPENING_PRICE = 4  # Preço de Abertura
    CLOSING_PRICE = 5  # Preço de Fechamento
    SETTLEMENT_PRICE = 6  # Preço de Ajuste


class MDUpdateAction(IntEnum):
    """Ação de Atualização Incremental (FIX Tag 279)."""

    NEW = 0  # Inserção de nova oferta / nível
    CHANGE = 1  # Alteração de volume / quantidade
    DELETE = 2  # Cancelamento de oferta / nível
    OVERLAY = 3  # Substituição do nível existente


class SecurityTradingStatus(IntEnum):
    """Estado Operacional de Negociação do Ativo (FIX Tag 326)."""

    PAUSE = 2  # Negociação Suspensa / Circuit Breaker
    OPEN = 17  # Negociação Contínua Aberta
    CLOSE = 18  # Mercado Fechado
    AUCTION = 21  # Leilão / Pré-Abertura


class TradingSessionSubID(IntEnum):
    """Sub-fase da Sessão de Negociação (FIX Tag 625)."""

    PRE_OPEN = 1  # Pré-Abertura
    CONTINUOUS_TRADING = 2  # Pregão Contínuo
    CLOSING_AUCTION = 3  # Leilão de Fechamento
    POST_TRADING = 4  # Pós-Negociação / After-Market


# Exceções Específicas do Protocolo UMDF SBE
class UmdfError(Exception):
    """Exceção base para erros do protocolo B3 UMDF."""


class UmdfTruncatedPayloadError(UmdfError):
    """Disparado quando o buffer de bytes é menor que o esperado pelo layout."""


class UmdfUnknownTemplateError(UmdfError):
    """Disparado quando o TemplateId no cabeçalho não é suportado pelo catálogo."""


class UmdfValidationError(UmdfError):
    """Disparado quando um campo viola regras semânticas de integridade da B3."""


@dataclass
class UmdfMessageHeader:
    """Cabeçalho canônico SBE (8 bytes, Little-Endian).

    Campos:
        block_length (uint16): Comprimento do bloco raiz da mensagem.
        template_id (uint16): Identificador unívoco do template (201, 202, 203).
        schema_id (uint16): Identificador do schema B3 (padrão 1).
        version (uint16): Versão do schema (padrão 1).
    """

    block_length: int
    template_id: int
    schema_id: int = DEFAULT_SCHEMA_ID
    version: int = DEFAULT_SCHEMA_VERSION

    FORMAT: ClassVar[str] = HEADER_FORMAT
    SIZE: ClassVar[int] = HEADER_SIZE

    def encode(self) -> bytes:
        """Serializa o cabeçalho em 8 bytes Little-Endian."""
        return struct.pack(
            self.FORMAT,
            self.block_length,
            self.template_id,
            self.schema_id,
            self.version,
        )

    @classmethod
    def decode(cls, data: Union[bytes, bytearray, memoryview], offset: int = 0) -> UmdfMessageHeader:
        """Desserializa o cabeçalho a partir de um buffer de memória."""
        if len(data) - offset < cls.SIZE:
            raise UmdfTruncatedPayloadError(
                f"Payload insuficiente para UmdfMessageHeader: esperado ao menos {cls.SIZE} bytes, "
                f"recebido {len(data) - offset} bytes."
            )
        block_length, template_id, schema_id, version = struct.unpack_from(cls.FORMAT, data, offset)
        return cls(
            block_length=block_length,
            template_id=template_id,
            schema_id=schema_id,
            version=version,
        )


@dataclass
class SecurityStatusMessage:
    """Mensagem de Status de Instrumento (TemplateId = 201).

    Informa alterações no estado de negociação (aberto, leilão, pausado, fechado).
    BlockLength = 34 bytes.
    Tamanho Total com Header = 42 bytes.
    """

    sequence_number: int
    security_id: int
    security_group: str
    security_trading_status: int
    trading_session_sub_id: int
    timestamp_ns: int
    header: UmdfMessageHeader = field(
        default_factory=lambda: UmdfMessageHeader(
            block_length=34,
            template_id=TEMPLATE_SECURITY_STATUS,
        )
    )

    TEMPLATE_ID: ClassVar[int] = TEMPLATE_SECURITY_STATUS
    BLOCK_LENGTH: ClassVar[int] = 34
    PAYLOAD_FORMAT: ClassVar[str] = "<QQ8sBBQ"
    TOTAL_SIZE: ClassVar[int] = HEADER_SIZE + BLOCK_LENGTH  # 42 bytes

    def validate(self) -> None:
        """Valida integridade semântica dos dados."""
        if self.sequence_number < 0:
            raise UmdfValidationError(f"SequenceNumber não pode ser negativo: {self.sequence_number}")
        if self.security_id <= 0:
            raise UmdfValidationError(f"SecurityID deve ser estritamente positivo: {self.security_id}")
        if self.security_trading_status not in (
            SecurityTradingStatus.PAUSE,
            SecurityTradingStatus.OPEN,
            SecurityTradingStatus.CLOSE,
            SecurityTradingStatus.AUCTION,
        ):
            raise UmdfValidationError(f"SecurityTradingStatus inválido: {self.security_trading_status}")

    def encode(self) -> bytes:
        """Codifica a mensagem completa em 42 bytes binários."""
        self.validate()
        encoded_group = self.security_group.encode("ascii")[:8].ljust(8, b" ")
        payload = struct.pack(
            self.PAYLOAD_FORMAT,
            self.sequence_number,
            self.security_id,
            encoded_group,
            int(self.security_trading_status),
            int(self.trading_session_sub_id),
            self.timestamp_ns,
        )
        return self.header.encode() + payload

    @classmethod
    def decode(cls, data: Union[bytes, bytearray, memoryview], offset: int = 0) -> SecurityStatusMessage:
        """Decodifica a mensagem a partir de buffer sem alocação desnecessária."""
        if len(data) - offset < cls.TOTAL_SIZE:
            raise UmdfTruncatedPayloadError(
                f"Buffer truncado para SecurityStatusMessage: esperado {cls.TOTAL_SIZE} bytes, "
                f"disponível {len(data) - offset} bytes."
            )
        header = UmdfMessageHeader.decode(data, offset)
        if header.template_id != cls.TEMPLATE_ID:
            raise UmdfUnknownTemplateError(
                f"TemplateId inválido para SecurityStatusMessage: esperado {cls.TEMPLATE_ID}, "
                f"recebido {header.template_id}."
            )

        payload_offset = offset + HEADER_SIZE
        (
            seq_num,
            sec_id,
            raw_group,
            sec_status,
            session_sub,
            ts_ns,
        ) = struct.unpack_from(cls.PAYLOAD_FORMAT, data, payload_offset)

        group_str = raw_group.decode("ascii", errors="replace").rstrip(" ")
        msg = cls(
            sequence_number=seq_num,
            security_id=sec_id,
            security_group=group_str,
            security_trading_status=sec_status,
            trading_session_sub_id=session_sub,
            timestamp_ns=ts_ns,
            header=header,
        )
        msg.validate()
        return msg


@dataclass
class OrderBookEntry:
    """Entrada individual de nível de livro de ofertas ou ordem individual.

    Compatível com livros L2 (Market by Price) e L3 (Market by Order).
    Formato: "<BqQHIQ" (31 bytes).
    """

    md_entry_type: int
    price: float
    size: int
    price_level: int = 1
    number_of_orders: int = 1
    order_id: int = 0

    ENTRY_FORMAT: ClassVar[str] = "<BqQHIQ"
    ENTRY_SIZE: ClassVar[int] = struct.calcsize(ENTRY_FORMAT)  # 31 bytes

    @property
    def price_scaled(self) -> int:
        """Preço codificado na escala fixa de 8 casas decimais (10^8)."""
        return int(round(self.price * PRICE_SCALE))

    def validate(self) -> None:
        """Valida integridade da entrada do livro."""
        if self.md_entry_type not in (MDEntryType.BID, MDEntryType.OFFER, MDEntryType.TRADE):
            raise UmdfValidationError(f"MDEntryType inválido: {self.md_entry_type}")
        if self.price <= 0:
            raise UmdfValidationError(f"Price deve ser estritamente positivo: {self.price}")
        if self.size <= 0:
            raise UmdfValidationError(f"Size deve ser estritamente positivo: {self.size}")

    def encode(self) -> bytes:
        """Serializa a entrada em 31 bytes Little-Endian."""
        self.validate()
        return struct.pack(
            self.ENTRY_FORMAT,
            int(self.md_entry_type),
            self.price_scaled,
            self.size,
            self.price_level,
            self.number_of_orders,
            self.order_id,
        )

    @classmethod
    def decode(cls, data: Union[bytes, bytearray, memoryview], offset: int = 0) -> OrderBookEntry:
        """Decodifica a entrada a partir do buffer."""
        if len(data) - offset < cls.ENTRY_SIZE:
            raise UmdfTruncatedPayloadError(
                f"Buffer truncado para OrderBookEntry: esperado ao menos {cls.ENTRY_SIZE} bytes, "
                f"disponível {len(data) - offset} bytes."
            )
        entry_type, px_scaled, size, level, num_orders, order_id = struct.unpack_from(
            cls.ENTRY_FORMAT, data, offset
        )
        entry = cls(
            md_entry_type=entry_type,
            price=px_scaled / PRICE_SCALE,
            size=size,
            price_level=level,
            number_of_orders=num_orders,
            order_id=order_id,
        )
        entry.validate()
        return entry


@dataclass
class MarketDataSnapshot:
    """Snapshot Completo do Livro de Ofertas (TemplateId = 202).

    Permite reconstrução determinística do livro (bootstrap ou pós-queda).
    Root Block = 36 bytes ("<QQIQQ").
    Group Header = 4 bytes ("<HH").
    Entries = N * 31 bytes ("<BqQHIQ").
    """

    sequence_number: int
    security_id: int
    tot_num_reports: int
    last_msg_seq_num_processed: int
    timestamp_ns: int
    entries: list[OrderBookEntry] = field(default_factory=list)
    header: UmdfMessageHeader = field(
        default_factory=lambda: UmdfMessageHeader(
            block_length=36,
            template_id=TEMPLATE_MARKET_DATA_SNAPSHOT,
        )
    )

    TEMPLATE_ID: ClassVar[int] = TEMPLATE_MARKET_DATA_SNAPSHOT
    ROOT_BLOCK_LENGTH: ClassVar[int] = 36
    ROOT_FORMAT: ClassVar[str] = "<QQIQQ"

    def validate(self) -> None:
        """Valida integridade do snapshot."""
        if self.sequence_number < 0:
            raise UmdfValidationError(f"SequenceNumber negativo: {self.sequence_number}")
        if self.security_id <= 0:
            raise UmdfValidationError(f"SecurityID deve ser positivo: {self.security_id}")
        for entry in self.entries:
            entry.validate()

    def encode(self) -> bytes:
        """Codifica o snapshot binário completo com repeating group."""
        self.validate()
        header_bytes = self.header.encode()
        root_bytes = struct.pack(
            self.ROOT_FORMAT,
            self.sequence_number,
            self.security_id,
            self.tot_num_reports,
            self.last_msg_seq_num_processed,
            self.timestamp_ns,
        )
        group_header = struct.pack(
            GROUP_HEADER_FORMAT,
            OrderBookEntry.ENTRY_SIZE,
            len(self.entries),
        )
        entries_bytes = b"".join(e.encode() for e in self.entries)
        return header_bytes + root_bytes + group_header + entries_bytes

    @classmethod
    def decode(cls, data: Union[bytes, bytearray, memoryview], offset: int = 0) -> MarketDataSnapshot:
        """Decodifica snapshot completo a partir do buffer."""
        min_size = HEADER_SIZE + cls.ROOT_BLOCK_LENGTH + GROUP_HEADER_SIZE
        if len(data) - offset < min_size:
            raise UmdfTruncatedPayloadError(
                f"Buffer insuficiente para MarketDataSnapshot: esperado ao menos {min_size} bytes, "
                f"disponível {len(data) - offset} bytes."
            )

        header = UmdfMessageHeader.decode(data, offset)
        if header.template_id != cls.TEMPLATE_ID:
            raise UmdfUnknownTemplateError(
                f"TemplateId inválido para MarketDataSnapshot: esperado {cls.TEMPLATE_ID}, "
                f"recebido {header.template_id}."
            )

        root_offset = offset + HEADER_SIZE
        (
            seq_num,
            sec_id,
            tot_reports,
            last_seq_processed,
            ts_ns,
        ) = struct.unpack_from(cls.ROOT_FORMAT, data, root_offset)

        group_offset = root_offset + cls.ROOT_BLOCK_LENGTH
        entry_block_len, num_in_group = struct.unpack_from(GROUP_HEADER_FORMAT, data, group_offset)

        expected_total = group_offset + GROUP_HEADER_SIZE + (num_in_group * entry_block_len)
        if len(data) < expected_total:
            raise UmdfTruncatedPayloadError(
                f"Payload truncado para entries do snapshot: esperado {expected_total} bytes, "
                f"disponível {len(data)} bytes."
            )

        entries: list[OrderBookEntry] = []
        cur_offset = group_offset + GROUP_HEADER_SIZE
        for _ in range(num_in_group):
            entry = OrderBookEntry.decode(data, cur_offset)
            entries.append(entry)
            cur_offset += entry_block_len

        snapshot = cls(
            sequence_number=seq_num,
            security_id=sec_id,
            tot_num_reports=tot_reports,
            last_msg_seq_num_processed=last_seq_processed,
            timestamp_ns=ts_ns,
            entries=entries,
            header=header,
        )
        snapshot.validate()
        return snapshot


@dataclass
class IncrementalEntry:
    """Entrada individual de atualização incremental de market data (48 bytes).

    Formato: "<BBQqQHIQQ" (48 bytes).
    """

    md_update_action: int
    md_entry_type: int
    security_id: int
    price: float
    size: int
    price_level: int = 0
    number_of_orders: int = 0
    order_id: int = 0
    trade_id: int = 0

    ENTRY_FORMAT: ClassVar[str] = "<BBQqQHIQQ"
    ENTRY_SIZE: ClassVar[int] = struct.calcsize(ENTRY_FORMAT)  # 48 bytes

    @property
    def price_scaled(self) -> int:
        """Preço codificado na escala fixa de 8 casas decimais (10^8)."""
        return int(round(self.price * PRICE_SCALE))

    def validate(self) -> None:
        """Valida integridade semântica da entrada incremental."""
        if self.md_update_action not in (
            MDUpdateAction.NEW,
            MDUpdateAction.CHANGE,
            MDUpdateAction.DELETE,
            MDUpdateAction.OVERLAY,
        ):
            raise UmdfValidationError(f"MDUpdateAction inválido: {self.md_update_action}")
        if self.md_entry_type not in (MDEntryType.BID, MDEntryType.OFFER, MDEntryType.TRADE):
            raise UmdfValidationError(f"MDEntryType inválido: {self.md_entry_type}")
        if self.security_id <= 0:
            raise UmdfValidationError(f"SecurityID deve ser estritamente positivo: {self.security_id}")
        if self.price <= 0:
            raise UmdfValidationError(f"Price deve ser estritamente positivo: {self.price}")
        if self.size <= 0:
            raise UmdfValidationError(f"Size deve ser estritamente positivo: {self.size}")

    def encode(self) -> bytes:
        """Serializa a entrada incremental em 48 bytes Little-Endian."""
        self.validate()
        return struct.pack(
            self.ENTRY_FORMAT,
            int(self.md_update_action),
            int(self.md_entry_type),
            self.security_id,
            self.price_scaled,
            self.size,
            self.price_level,
            self.number_of_orders,
            self.order_id,
            self.trade_id,
        )

    @classmethod
    def decode(cls, data: Union[bytes, bytearray, memoryview], offset: int = 0) -> IncrementalEntry:
        """Decodifica a entrada a partir do buffer."""
        if len(data) - offset < cls.ENTRY_SIZE:
            raise UmdfTruncatedPayloadError(
                f"Buffer insuficiente para IncrementalEntry: esperado ao menos {cls.ENTRY_SIZE} bytes, "
                f"disponível {len(data) - offset} bytes."
            )
        (
            action,
            entry_type,
            sec_id,
            px_scaled,
            size,
            level,
            num_orders,
            order_id,
            trade_id,
        ) = struct.unpack_from(cls.ENTRY_FORMAT, data, offset)

        entry = cls(
            md_update_action=action,
            md_entry_type=entry_type,
            security_id=sec_id,
            price=px_scaled / PRICE_SCALE,
            size=size,
            price_level=level,
            number_of_orders=num_orders,
            order_id=order_id,
            trade_id=trade_id,
        )
        entry.validate()
        return entry


@dataclass
class MarketDataIncremental:
    """Mensagem de Atualização Incremental (TemplateId = 203).

    Root Block = 16 bytes ("<QQ").
    Group Header = 4 bytes ("<HH").
    Entries = N * 48 bytes ("<BBQqQHIQQ").
    """

    sequence_number: int
    timestamp_ns: int
    entries: list[IncrementalEntry] = field(default_factory=list)
    header: UmdfMessageHeader = field(
        default_factory=lambda: UmdfMessageHeader(
            block_length=16,
            template_id=TEMPLATE_MARKET_DATA_INCREMENTAL,
        )
    )

    TEMPLATE_ID: ClassVar[int] = TEMPLATE_MARKET_DATA_INCREMENTAL
    ROOT_BLOCK_LENGTH: ClassVar[int] = 16
    ROOT_FORMAT: ClassVar[str] = "<QQ"

    def validate(self) -> None:
        """Valida integridade da mensagem incremental."""
        if self.sequence_number < 0:
            raise UmdfValidationError(f"SequenceNumber negativo: {self.sequence_number}")
        for entry in self.entries:
            entry.validate()

    def encode(self) -> bytes:
        """Serializa a mensagem incremental completa com repeating group."""
        self.validate()
        header_bytes = self.header.encode()
        root_bytes = struct.pack(
            self.ROOT_FORMAT,
            self.sequence_number,
            self.timestamp_ns,
        )
        group_header = struct.pack(
            GROUP_HEADER_FORMAT,
            IncrementalEntry.ENTRY_SIZE,
            len(self.entries),
        )
        entries_bytes = b"".join(e.encode() for e in self.entries)
        return header_bytes + root_bytes + group_header + entries_bytes

    @classmethod
    def decode(cls, data: Union[bytes, bytearray, memoryview], offset: int = 0) -> MarketDataIncremental:
        """Decodifica a mensagem incremental a partir do buffer."""
        min_size = HEADER_SIZE + cls.ROOT_BLOCK_LENGTH + GROUP_HEADER_SIZE
        if len(data) - offset < min_size:
            raise UmdfTruncatedPayloadError(
                f"Buffer insuficiente para MarketDataIncremental: esperado ao menos {min_size} bytes, "
                f"disponível {len(data) - offset} bytes."
            )

        header = UmdfMessageHeader.decode(data, offset)
        if header.template_id != cls.TEMPLATE_ID:
            raise UmdfUnknownTemplateError(
                f"TemplateId inválido para MarketDataIncremental: esperado {cls.TEMPLATE_ID}, "
                f"recebido {header.template_id}."
            )

        root_offset = offset + HEADER_SIZE
        seq_num, ts_ns = struct.unpack_from(cls.ROOT_FORMAT, data, root_offset)

        group_offset = root_offset + cls.ROOT_BLOCK_LENGTH
        entry_block_len, num_in_group = struct.unpack_from(GROUP_HEADER_FORMAT, data, group_offset)

        expected_total = group_offset + GROUP_HEADER_SIZE + (num_in_group * entry_block_len)
        if len(data) < expected_total:
            raise UmdfTruncatedPayloadError(
                f"Payload truncado para entries incrementais: esperado {expected_total} bytes, "
                f"disponível {len(data)} bytes."
            )

        entries: list[IncrementalEntry] = []
        cur_offset = group_offset + GROUP_HEADER_SIZE
        for _ in range(num_in_group):
            entry = IncrementalEntry.decode(data, cur_offset)
            entries.append(entry)
            cur_offset += entry_block_len

        msg = cls(
            sequence_number=seq_num,
            timestamp_ns=ts_ns,
            entries=entries,
            header=header,
        )
        msg.validate()
        return msg


class OrderBook:
    """Motor de Manutenção e Reconstrução do Livro de Ofertas (L2 e L3).

    Mantém o estado sincronizado das ofertas de compra (bids) e venda (asks),
    ordens individuais (L3) e histórico recente de trades executados.
    """

    def __init__(self, security_id: int):
        self.security_id: int = security_id
        # Livro L2: preço -> OrderBookEntry
        self.bids: dict[float, OrderBookEntry] = {}
        self.asks: dict[float, OrderBookEntry] = {}
        # Livro L3: order_id -> OrderBookEntry
        self.orders: dict[int, OrderBookEntry] = {}
        # Histórico de negócios executados
        self.trades: list[dict[str, Any]] = []
        # Sequencial de sincronização
        self.last_seq_num: int = 0

    def clear(self) -> None:
        """Limpa todo o estado do livro."""
        self.bids.clear()
        self.asks.clear()
        self.orders.clear()
        self.trades.clear()
        self.last_seq_num = 0

    def apply_snapshot(self, snapshot: MarketDataSnapshot) -> None:
        """Aplica um snapshot completo, redefinindo o estado do livro."""
        if snapshot.security_id != self.security_id:
            raise UmdfValidationError(
                f"SecurityID do snapshot ({snapshot.security_id}) difere do livro ({self.security_id})"
            )

        self.clear()
        self.last_seq_num = snapshot.last_msg_seq_num_processed

        for entry in snapshot.entries:
            if entry.md_entry_type == MDEntryType.BID:
                self.bids[entry.price] = entry
            elif entry.md_entry_type == MDEntryType.OFFER:
                self.asks[entry.price] = entry

            if entry.order_id > 0:
                self.orders[entry.order_id] = entry

    def apply_incremental(self, incremental: MarketDataIncremental) -> None:
        """Aplica deltas incrementais e registra execuções de negócios."""
        for entry in incremental.entries:
            if entry.security_id != self.security_id:
                continue

            # Registro de Negócio Executado (Trade)
            if entry.md_entry_type == MDEntryType.TRADE:
                self.trades.append(
                    {
                        "trade_id": entry.trade_id,
                        "security_id": entry.security_id,
                        "price": entry.price,
                        "size": entry.size,
                        "order_id": entry.order_id,
                        "timestamp_ns": incremental.timestamp_ns,
                    }
                )
                continue

            target_book = self.bids if entry.md_entry_type == MDEntryType.BID else self.asks

            if entry.md_update_action == MDUpdateAction.NEW:
                book_entry = OrderBookEntry(
                    md_entry_type=entry.md_entry_type,
                    price=entry.price,
                    size=entry.size,
                    price_level=entry.price_level or 1,
                    number_of_orders=entry.number_of_orders or 1,
                    order_id=entry.order_id,
                )
                target_book[entry.price] = book_entry
                if entry.order_id > 0:
                    self.orders[entry.order_id] = book_entry

            elif entry.md_update_action == MDUpdateAction.CHANGE:
                if entry.price in target_book:
                    target_book[entry.price].size = entry.size
                    if entry.number_of_orders > 0:
                        target_book[entry.price].number_of_orders = entry.number_of_orders
                else:
                    target_book[entry.price] = OrderBookEntry(
                        md_entry_type=entry.md_entry_type,
                        price=entry.price,
                        size=entry.size,
                        price_level=entry.price_level or 1,
                        number_of_orders=entry.number_of_orders or 1,
                        order_id=entry.order_id,
                    )
                if entry.order_id > 0 and entry.order_id in self.orders:
                    self.orders[entry.order_id].size = entry.size

            elif entry.md_update_action == MDUpdateAction.DELETE:
                if entry.price in target_book:
                    del target_book[entry.price]
                if entry.order_id > 0 and entry.order_id in self.orders:
                    del self.orders[entry.order_id]

            elif entry.md_update_action == MDUpdateAction.OVERLAY:
                target_book[entry.price] = OrderBookEntry(
                    md_entry_type=entry.md_entry_type,
                    price=entry.price,
                    size=entry.size,
                    price_level=entry.price_level or 1,
                    number_of_orders=entry.number_of_orders or 1,
                    order_id=entry.order_id,
                )

        self.last_seq_num = incremental.sequence_number

    def best_bid(self) -> Optional[tuple[float, int]]:
        """Retorna a melhor oferta de compra (preço máximo, quantidade total)."""
        if not self.bids:
            return None
        best_px = max(self.bids.keys())
        return best_px, self.bids[best_px].size

    def best_ask(self) -> Optional[tuple[float, int]]:
        """Retorna a melhor oferta de venda (preço mínimo, quantidade total)."""
        if not self.asks:
            return None
        best_px = min(self.asks.keys())
        return best_px, self.asks[best_px].size

    def spread(self) -> Optional[float]:
        """Calcula o spread do Top of Book (Best Ask - Best Bid)."""
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return None
        return round(ask[0] - bid[0], 4)

    def mid_price(self) -> Optional[float]:
        """Calcula o preço médio entre a melhor compra e a melhor venda."""
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return None
        return round((bid[0] + ask[0]) / 2.0, 4)

    def micro_price(self) -> Optional[float]:
        """Calcula o micro-preço ponderado por volume no Top of Book."""
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return None
        bid_px, bid_qty = bid
        ask_px, ask_qty = ask
        total_qty = bid_qty + ask_qty
        if total_qty == 0:
            return None
        return round((bid_qty * ask_px + ask_qty * bid_px) / total_qty, 4)

    def get_depth(self, levels: int = 5) -> dict[str, list[dict[str, Any]]]:
        """Retorna a profundidade L2 de até N níveis ordenados.

        Bids ordenados de forma decrescente (maior preço primeiro).
        Asks ordenados de forma crescente (menor preço primeiro).
        """
        sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:levels]
        sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:levels]

        bids_out = [
            {
                "level": i + 1,
                "price": px,
                "size": entry.size,
                "orders": entry.number_of_orders,
            }
            for i, (px, entry) in enumerate(sorted_bids)
        ]

        asks_out = [
            {
                "level": i + 1,
                "price": px,
                "size": entry.size,
                "orders": entry.number_of_orders,
            }
            for i, (px, entry) in enumerate(sorted_asks)
        ]

        return {"bids": bids_out, "asks": asks_out}


UmdfMessage = Union[SecurityStatusMessage, MarketDataSnapshot, MarketDataIncremental]


class UmdfCodec:
    """Codec determinístico de alto desempenho para mensagens UMDF SBE."""

    TEMPLATES: ClassVar[dict[int, type[UmdfMessage]]] = {
        TEMPLATE_SECURITY_STATUS: SecurityStatusMessage,
        TEMPLATE_MARKET_DATA_SNAPSHOT: MarketDataSnapshot,
        TEMPLATE_MARKET_DATA_INCREMENTAL: MarketDataIncremental,
    }

    @classmethod
    def decode(cls, data: Union[bytes, bytearray, memoryview], offset: int = 0) -> UmdfMessage:
        """Decodifica dinamicamente a mensagem UMDF baseando-se no TemplateId."""
        if len(data) - offset < HEADER_SIZE:
            raise UmdfTruncatedPayloadError(
                f"Buffer insuficiente para cabeçalho SBE: {len(data) - offset} bytes disponíveis."
            )
        header = UmdfMessageHeader.decode(data, offset)
        msg_cls = cls.TEMPLATES.get(header.template_id)
        if msg_cls is None:
            raise UmdfUnknownTemplateError(
                f"TemplateId {header.template_id} desconhecido no catálogo UMDF SBE. "
                f"Suportados: {list(cls.TEMPLATES.keys())}"
            )
        return msg_cls.decode(data, offset)

    @classmethod
    def encode(cls, message: UmdfMessage) -> bytes:
        """Serializa mensagem UMDF para buffer binário."""
        return message.encode()


class UmdfStreamConsumer:
    """Consumidor de Fluxo Multicast UMDF com Redundância Active-Active (Feeds A/B).

    Responsabilidades:
    1. Recepção concorrente de pacotes dos canais Multicast Feed A e Feed B.
    2. Deduplicação determinística em nível de pacote/sequência (descarte instantâneo do feed mais lento).
    3. Detecção de lacunas de sequência (gap detection) para acionamento de recuperação.
    4. Roteamento automático de mensagens e manutenção do estado do livro de ofertas e status de ativos.
    """

    def __init__(self):
        self.books: dict[int, OrderBook] = {}
        self.security_statuses: dict[int, SecurityStatusMessage] = {}
        self.processed_sequences: set[int] = set()
        self.highest_sequence: int = 0

        # Métricas de Telemetria e Desempenho
        self.packets_received_a: int = 0
        self.packets_received_b: int = 0
        self.duplicates_discarded: int = 0
        self.gaps_detected: int = 0
        self.messages_processed: int = 0

    def get_or_create_book(self, security_id: int) -> OrderBook:
        """Recupera ou inicializa o livro de ofertas para o ativo."""
        if security_id not in self.books:
            self.books[security_id] = OrderBook(security_id)
        return self.books[security_id]

    def on_packet(
        self,
        data: Union[bytes, bytearray, memoryview],
        feed_id: str = "A",
    ) -> list[UmdfMessage]:
        """Processa um pacote UDP recebido no canal especificado ('A' ou 'B').

        Retorna a lista de mensagens decodificadas, ou lista vazia caso seja duplicata descartada.
        """
        # Contabiliza recebimento por canal físico
        if feed_id.upper() == "A":
            self.packets_received_a += 1
        else:
            self.packets_received_b += 1

        if len(data) < HEADER_SIZE:
            raise UmdfTruncatedPayloadError(
                f"Pacote UDP recebido menor que o cabeçalho SBE ({len(data)} < {HEADER_SIZE})"
            )

        # Inspeciona o TemplateId e SequenceNumber do primeiro bloco (offset 8 é sempre sequence_number em uint64)
        header = UmdfMessageHeader.decode(data, 0)
        if len(data) < HEADER_SIZE + 8:
            raise UmdfTruncatedPayloadError(
                f"Pacote truncado para leitura de sequence_number: {len(data)} bytes"
            )

        seq_num = struct.unpack_from("<Q", data, HEADER_SIZE)[0]

        # Arbitragem e Deduplicação Active-Active
        if seq_num in self.processed_sequences:
            self.duplicates_discarded += 1
            return []

        # Detecção de Gap de Sequência
        if self.highest_sequence > 0 and seq_num > self.highest_sequence + 1:
            self.gaps_detected += (seq_num - self.highest_sequence - 1)

        self.processed_sequences.add(seq_num)
        if seq_num > self.highest_sequence:
            self.highest_sequence = seq_num

        # Decodifica a mensagem (ou mensagens empacotadas sequencialmente no payload)
        decoded_messages: list[UmdfMessage] = []
        offset = 0

        while offset < len(data):
            msg = UmdfCodec.decode(data, offset)
            decoded_messages.append(msg)
            self.messages_processed += 1

            # Despacho interno para gerenciamento de estado
            if isinstance(msg, SecurityStatusMessage):
                self.security_statuses[msg.security_id] = msg
            elif isinstance(msg, MarketDataSnapshot):
                book = self.get_or_create_book(msg.security_id)
                book.apply_snapshot(msg)
            elif isinstance(msg, MarketDataIncremental):
                for entry in msg.entries:
                    book = self.get_or_create_book(entry.security_id)
                    # Cria mensagem individualizada ou aplica via livro
                # Agrupa por ativo se houver múltiplos
                sec_ids = {entry.security_id for entry in msg.entries}
                for s_id in sec_ids:
                    book = self.get_or_create_book(s_id)
                    book.apply_incremental(msg)

            # Avança o offset
            if isinstance(msg, SecurityStatusMessage):
                offset += msg.TOTAL_SIZE
            elif isinstance(msg, MarketDataSnapshot):
                offset += (
                    HEADER_SIZE
                    + msg.ROOT_BLOCK_LENGTH
                    + GROUP_HEADER_SIZE
                    + (len(msg.entries) * OrderBookEntry.ENTRY_SIZE)
                )
            elif isinstance(msg, MarketDataIncremental):
                offset += (
                    HEADER_SIZE
                    + msg.ROOT_BLOCK_LENGTH
                    + GROUP_HEADER_SIZE
                    + (len(msg.entries) * IncrementalEntry.ENTRY_SIZE)
                )

        return decoded_messages

    def get_book(self, security_id: int) -> Optional[OrderBook]:
        """Retorna o livro de ofertas do ativo, se existente."""
        return self.books.get(security_id)

    def get_security_status(self, security_id: int) -> Optional[SecurityStatusMessage]:
        """Retorna o status atual do ativo, se existente."""
        return self.security_statuses.get(security_id)

    def stats(self) -> dict[str, int]:
        """Retorna dicionário com métricas operacionais consolidadas."""
        return {
            "packets_received_a": self.packets_received_a,
            "packets_received_b": self.packets_received_b,
            "duplicates_discarded": self.duplicates_discarded,
            "gaps_detected": self.gaps_detected,
            "messages_processed": self.messages_processed,
            "highest_sequence": self.highest_sequence,
        }
