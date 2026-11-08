"""Camada de Interoperabilidade C-ABI / Foreign Function Interface (FFI) de Alta Performance.

Provê definições de estruturas ctypes, conversões zero-copy com memoryview,
alinhamento de bytes e layouts C99 estritos para:
- Mensageria PUMA SBE v2 (NewOrderSingle, OrderCancel, ExecutionReport)
- Buffer Circular de Ordens HFT (Order Ring Buffer) alinhado em cache-line (64 bytes)
- Posições Escriturais de Custódia Depositária
- Transferências e Posições Líquidas Multilaterais do Barramento Central
"""

from __future__ import annotations

import ctypes
from enum import IntEnum
from typing import Any, Dict, Iterator, Optional, Tuple, Type, TypeVar, Union

# Constantes de Protocolo e Escalas
PRICE_SCALE: int = 100_000_000  # 10^8 (8 decimais padrão B3 PUMA)
CENT_SCALE: int = 100           # 10^2 (centavos para montantes financeiros R$)
CACHE_LINE_SIZE: int = 64       # Tamanho canônico de L1 Cache Line (x86_64 / ARM64)
B3_CACHE_LINE_SIZE: int = 64    # Alias explícito B3
B3_PRICE_SCALE: int = 100_000_000
B3_CENT_SCALE: int = 100

TEMPLATE_NEW_ORDER: int = 101
TEMPLATE_ORDER_CANCEL: int = 102
TEMPLATE_EXECUTION_REPORT: int = 103

DEFAULT_SCHEMA_ID: int = 1
DEFAULT_SCHEMA_VERSION: int = 1


class Side(IntEnum):
    """Lado da Ordem no PUMA Trading System."""
    BUY = 1
    SELL = 2


class TimeInForce(IntEnum):
    """Validade temporal da Ordem."""
    DAY = 0  # Válida para o dia
    GTC = 1  # Good 'Til Cancel
    IOC = 3  # Immediate or Cancel
    FOK = 4  # Fill or Kill


class ExecType(IntEnum):
    """Tipo do Evento de Execução PUMA."""
    NEW = 0
    PARTIAL_FILL = 1
    FILL = 2
    CANCELLED = 4
    REJECTED = 8


class OrdStatus(IntEnum):
    """Estado da Ordem no motor PUMA."""
    NEW = 0
    PARTIALLY_FILLED = 1
    FILLED = 2
    CANCELLED = 4
    REJECTED = 8


class SettlementCycle(IntEnum):
    """Ciclos de Liquidação do Barramento Central de Liquidação."""
    INTRADAY_1 = 1
    INTRADAY_2 = 2
    MULTILATERAL_CLOSE = 3
    BILATERAL_EVENING = 4


class ClearingSubsystem(IntEnum):
    """Subsistemas da Câmara B3."""
    DERIVATIVES = 1
    EQUITIES = 2
    FIXED_INCOME = 3
    FX = 4


class InstructionStatus(IntEnum):
    """Estado da instrução multilateral de liquidação."""
    PENDING = 0
    SETTLED = 1
    CANCELLED = 2
    REJECTED = 3


T = TypeVar("T", bound="CAbiStructure")


class CAbiStructure(ctypes.Structure):
    """Classe base ctypes com primitivas zero-copy de alta performance."""

    def as_memoryview(self) -> memoryview:
        """Exporta a memória da estrutura como memoryview de bytes (1D) sem alocação de cópia heap."""
        return memoryview(self).cast("B")

    def as_bytes(self) -> bytes:
        """Serializa a estrutura diretamente em bytes."""
        return bytes(self)

    @classmethod
    def from_buffer_zero_copy(
        cls: Type[T],
        buf: Union[bytearray, memoryview, ctypes.Array],
        offset: int = 0,
    ) -> T:
        """Instancia a estrutura diretamente sobre o buffer sem realizar memcpy.

        O buffer deve suportar protocolo de buffer gravável (ex: bytearray ou memoryview gravável).
        """
        return cls.from_buffer(buf, offset)

    @classmethod
    def from_buffer_copy_safe(
        cls: Type[T],
        buf: Union[bytes, bytearray, memoryview],
        offset: int = 0,
    ) -> T:
        """Instancia a estrutura copiando os bytes necessários do buffer (seguro para bytes imutáveis)."""
        return cls.from_buffer_copy(buf, offset)

    @classmethod
    def from_pointer_address(cls: Type[T], address: int) -> T:
        """Mapeia uma instância diretamente de um ponteiro numérico de memória."""
        return cls.from_address(address)

    def get_pointer_address(self) -> int:
        """Retorna o endereço de memória base da estrutura."""
        return ctypes.addressof(self)


# ==============================================================================
# PUMA SBE v2 - Wire Message Layouts (Packed 1-byte alignment)
# ==============================================================================

class SbeHeaderStruct(CAbiStructure):
    """Cabeçalho canônico SBE (Message Header / Framing Header) de 8 bytes.

    Layout em memória:
    - block_length: uint16 (2 bytes, offset 0)
    - template_id:  uint16 (2 bytes, offset 2)
    - schema_id:    uint16 (2 bytes, offset 4)
    - version:      uint16 (2 bytes, offset 6)
    Tamanho total: 8 bytes.
    """
    _pack_ = 1
    _fields_ = [
        ("block_length", ctypes.c_uint16),
        ("template_id", ctypes.c_uint16),
        ("schema_id", ctypes.c_uint16),
        ("version", ctypes.c_uint16),
    ]

    def populate(
        self,
        block_length: int,
        template_id: int,
        schema_id: int = DEFAULT_SCHEMA_ID,
        version: int = DEFAULT_SCHEMA_VERSION,
    ) -> None:
        self.block_length = block_length
        self.template_id = template_id
        self.schema_id = schema_id
        self.version = version


class NewOrderSingleStruct(CAbiStructure):
    """Mensagem de Inserção de Nova Ordem PUMA SBE v2 (TemplateId = 101).

    Layout em memória (Packed 1-byte):
    - header:        SbeHeaderStruct (8 bytes, offset 0)
    - cl_ord_id:     uint64 (8 bytes, offset 8)
    - security_id:   uint64 (8 bytes, offset 16)
    - side:          uint8  (1 byte,  offset 24)
    - order_qty:     uint32 (4 bytes, offset 25)
    - price_scaled:  int64  (8 bytes, offset 29)
    - time_in_force: uint8  (1 byte,  offset 37)
    - account:       uint32 (4 bytes, offset 38)
    Tamanho total: 42 bytes (Header 8 + Payload 34).
    """
    _pack_ = 1
    _fields_ = [
        ("header", SbeHeaderStruct),
        ("cl_ord_id", ctypes.c_uint64),
        ("security_id", ctypes.c_uint64),
        ("side", ctypes.c_uint8),
        ("order_qty", ctypes.c_uint32),
        ("price_scaled", ctypes.c_int64),
        ("time_in_force", ctypes.c_uint8),
        ("account", ctypes.c_uint32),
    ]

    TEMPLATE_ID = TEMPLATE_NEW_ORDER
    BLOCK_LENGTH = 34
    TOTAL_SIZE = 42

    @property
    def price(self) -> float:
        """Preço em reais em ponto flutuante."""
        return self.price_scaled / PRICE_SCALE

    @price.setter
    def price(self, val: float) -> None:
        self.price_scaled = int(round(val * PRICE_SCALE))

    def populate(
        self,
        cl_ord_id: int,
        security_id: int,
        side: int,
        order_qty: int,
        price: float,
        time_in_force: int = int(TimeInForce.DAY),
        account: int = 0,
        schema_id: int = DEFAULT_SCHEMA_ID,
        version: int = DEFAULT_SCHEMA_VERSION,
    ) -> None:
        """Inicializa todos os campos da ordem."""
        self.header.populate(
            block_length=self.BLOCK_LENGTH,
            template_id=self.TEMPLATE_ID,
            schema_id=schema_id,
            version=version,
        )
        self.cl_ord_id = cl_ord_id
        self.security_id = security_id
        self.side = side
        self.order_qty = order_qty
        self.price = price
        self.time_in_force = time_in_force
        self.account = account


class OrderCancelStruct(CAbiStructure):
    """Mensagem de Cancelamento de Ordem PUMA SBE v2 (TemplateId = 102).

    Layout em memória (Packed 1-byte):
    - header:         SbeHeaderStruct (8 bytes, offset 0)
    - cl_ord_id:      uint64 (8 bytes, offset 8)
    - orig_cl_ord_id: uint64 (8 bytes, offset 16)
    - security_id:    uint64 (8 bytes, offset 24)
    - account:        uint32 (4 bytes, offset 32)
    Tamanho total: 36 bytes (Header 8 + Payload 28).
    """
    _pack_ = 1
    _fields_ = [
        ("header", SbeHeaderStruct),
        ("cl_ord_id", ctypes.c_uint64),
        ("orig_cl_ord_id", ctypes.c_uint64),
        ("security_id", ctypes.c_uint64),
        ("account", ctypes.c_uint32),
    ]

    TEMPLATE_ID = TEMPLATE_ORDER_CANCEL
    BLOCK_LENGTH = 28
    TOTAL_SIZE = 36

    def populate(
        self,
        cl_ord_id: int,
        orig_cl_ord_id: int,
        security_id: int,
        account: int,
        schema_id: int = DEFAULT_SCHEMA_ID,
        version: int = DEFAULT_SCHEMA_VERSION,
    ) -> None:
        self.header.populate(
            block_length=self.BLOCK_LENGTH,
            template_id=self.TEMPLATE_ID,
            schema_id=schema_id,
            version=version,
        )
        self.cl_ord_id = cl_ord_id
        self.orig_cl_ord_id = orig_cl_ord_id
        self.security_id = security_id
        self.account = account


class ExecutionReportStruct(CAbiStructure):
    """Mensagem de Relatório de Execução PUMA SBE v2 (TemplateId = 103).

    Layout em memória (Packed 1-byte):
    - header:         SbeHeaderStruct (8 bytes, offset 0)
    - order_id:       uint64 (8 bytes, offset 8)
    - cl_ord_id:      uint64 (8 bytes, offset 16)
    - exec_id:        uint64 (8 bytes, offset 24)
    - exec_type:      uint8  (1 byte,  offset 32)
    - ord_status:     uint8  (1 byte,  offset 33)
    - security_id:    uint64 (8 bytes, offset 34)
    - cum_qty:        uint32 (4 bytes, offset 42)
    - leaves_qty:     uint32 (4 bytes, offset 46)
    - last_px_scaled: int64  (8 bytes, offset 50)
    - last_qty:       uint32 (4 bytes, offset 58)
    Tamanho total: 62 bytes (Header 8 + Payload 54).
    """
    _pack_ = 1
    _fields_ = [
        ("header", SbeHeaderStruct),
        ("order_id", ctypes.c_uint64),
        ("cl_ord_id", ctypes.c_uint64),
        ("exec_id", ctypes.c_uint64),
        ("exec_type", ctypes.c_uint8),
        ("ord_status", ctypes.c_uint8),
        ("security_id", ctypes.c_uint64),
        ("cum_qty", ctypes.c_uint32),
        ("leaves_qty", ctypes.c_uint32),
        ("last_px_scaled", ctypes.c_int64),
        ("last_qty", ctypes.c_uint32),
    ]

    TEMPLATE_ID = TEMPLATE_EXECUTION_REPORT
    BLOCK_LENGTH = 54
    TOTAL_SIZE = 62

    @property
    def last_px(self) -> float:
        """Preço da última execução em reais."""
        return self.last_px_scaled / PRICE_SCALE

    @last_px.setter
    def last_px(self, val: float) -> None:
        self.last_px_scaled = int(round(val * PRICE_SCALE))

    def populate(
        self,
        order_id: int,
        cl_ord_id: int,
        exec_id: int,
        exec_type: int,
        ord_status: int,
        security_id: int,
        cum_qty: int,
        leaves_qty: int,
        last_px: float,
        last_qty: int,
        schema_id: int = DEFAULT_SCHEMA_ID,
        version: int = DEFAULT_SCHEMA_VERSION,
    ) -> None:
        self.header.populate(
            block_length=self.BLOCK_LENGTH,
            template_id=self.TEMPLATE_ID,
            schema_id=schema_id,
            version=version,
        )
        self.order_id = order_id
        self.cl_ord_id = cl_ord_id
        self.exec_id = exec_id
        self.exec_type = exec_type
        self.ord_status = ord_status
        self.security_id = security_id
        self.cum_qty = cum_qty
        self.leaves_qty = leaves_qty
        self.last_px = last_px
        self.last_qty = last_qty


# ==============================================================================
# HFT Order Buffer (Cache-Line Aligned: 64 bytes)
# ==============================================================================

class OrderBufferEntryStruct(CAbiStructure):
    """Entrada em Buffer de Ordens otimizada para Cache-Line L1 (64 bytes exatos).

    Layout:
    - entry_id:      uint64 (8 bytes, offset 0)
    - timestamp_ns:  uint64 (8 bytes, offset 8)
    - cl_ord_id:     uint64 (8 bytes, offset 16)
    - security_id:   uint64 (8 bytes, offset 24)
    - price_scaled:  int64  (8 bytes, offset 32)
    - quantity:      uint32 (4 bytes, offset 40)
    - account:       uint32 (4 bytes, offset 44)
    - side:          uint8  (1 byte,  offset 48)
    - time_in_force: uint8  (1 byte,  offset 49)
    - order_status:  uint8  (1 byte,  offset 50)
    - _pad:          uint8[5] (5 bytes, offset 51)
    - symbol:        char[8]  (8 bytes, offset 56)
    Tamanho total: exatamente 64 bytes (1 cache-line).
    """
    _pack_ = 8
    _fields_ = [
        ("entry_id", ctypes.c_uint64),
        ("timestamp_ns", ctypes.c_uint64),
        ("cl_ord_id", ctypes.c_uint64),
        ("security_id", ctypes.c_uint64),
        ("price_scaled", ctypes.c_int64),
        ("quantity", ctypes.c_uint32),
        ("account", ctypes.c_uint32),
        ("side", ctypes.c_uint8),
        ("time_in_force", ctypes.c_uint8),
        ("order_status", ctypes.c_uint8),
        ("_pad", ctypes.c_uint8 * 5),
        ("symbol", ctypes.c_char * 8),
    ]

    @property
    def price(self) -> float:
        return self.price_scaled / PRICE_SCALE

    @price.setter
    def price(self, val: float) -> None:
        self.price_scaled = int(round(val * PRICE_SCALE))

    @property
    def symbol_str(self) -> str:
        return self.symbol.decode("ascii", errors="replace").rstrip("\x00")

    @symbol_str.setter
    def symbol_str(self, val: str) -> None:
        self.symbol = val.encode("ascii")[:8]


class OrderRingBufferHeaderStruct(CAbiStructure):
    """Cabeçalho de Buffer Circular (Ring Buffer) de Ordens Lockless (32 bytes).

    Layout:
    - capacity:   uint32 (4 bytes) - Potência de 2
    - mask:       uint32 (4 bytes) - capacity - 1
    - head:       uint64 (8 bytes) - Posição de escrita
    - tail:       uint64 (8 bytes) - Posição de leitura
    - entry_size: uint32 (4 bytes) - 64 bytes
    - flags:      uint32 (4 bytes)
    """
    _fields_ = [
        ("capacity", ctypes.c_uint32),
        ("mask", ctypes.c_uint32),
        ("head", ctypes.c_uint64),
        ("tail", ctypes.c_uint64),
        ("entry_size", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


class ZeroCopyOrderRingBuffer:
    """Implementação zero-copy de Buffer Circular de ordens HFT em memória compartilhada / bytearray."""

    def __init__(self, capacity: int = 1024, backing_buffer: Optional[bytearray] = None) -> None:
        if capacity <= 0 or (capacity & (capacity - 1)) != 0:
            raise ValueError(f"Capacidade do ring buffer deve ser potência de 2: {capacity}")
        self.capacity = capacity
        self.entry_size = ctypes.sizeof(OrderBufferEntryStruct)
        self.header_size = ctypes.sizeof(OrderRingBufferHeaderStruct)
        self.total_size = self.header_size + (self.capacity * self.entry_size)

        if backing_buffer is None:
            self._buffer = bytearray(self.total_size)
        else:
            if len(backing_buffer) < self.total_size:
                raise ValueError(
                    f"Buffer fornecido tem {len(backing_buffer)} bytes; esperado ao menos {self.total_size}"
                )
            self._buffer = backing_buffer

        self.header = OrderRingBufferHeaderStruct.from_buffer(self._buffer, 0)
        self.header.capacity = self.capacity
        self.header.mask = self.capacity - 1
        self.header.head = 0
        self.header.tail = 0
        self.header.entry_size = self.entry_size
        self.header.flags = 0

    @property
    def buffer(self) -> bytearray:
        return self._buffer

    def get_entry(self, index: int) -> OrderBufferEntryStruct:
        """Obtém referência ctypes zero-copy para a entrada no índice slot especificado."""
        slot = index & self.header.mask
        offset = self.header_size + (slot * self.entry_size)
        return OrderBufferEntryStruct.from_buffer(self._buffer, offset)

    def write_entry(
        self,
        entry_id: int,
        timestamp_ns: int,
        cl_ord_id: int,
        security_id: int,
        price: float,
        quantity: int,
        account: int,
        side: int,
        symbol: str,
        time_in_force: int = 0,
        order_status: int = 0,
    ) -> int:
        """Escreve uma nova entrada no topo (head) do ring buffer de forma zero-copy."""
        head = self.header.head
        entry = self.get_entry(head)
        entry.entry_id = entry_id
        entry.timestamp_ns = timestamp_ns
        entry.cl_ord_id = cl_ord_id
        entry.security_id = security_id
        entry.price = price
        entry.quantity = quantity
        entry.account = account
        entry.side = side
        entry.symbol_str = symbol
        entry.time_in_force = time_in_force
        entry.order_status = order_status
        self.header.head = head + 1
        return head


# ==============================================================================
# Custódia Depositária (Posições em Memória)
# ==============================================================================

class CustodyPositionStruct(CAbiStructure):
    """Estrutura em C para posição escritural de custódia depositária B3.

    Layout (alinhamento natural C 8-byte):
    - participant_id:        uint32 (4 bytes, offset 0)
    - _pad0:                 uint32 (4 bytes, offset 4)
    - account_id:            uint64 (8 bytes, offset 8)
    - security_id:           uint64 (8 bytes, offset 16)
    - available_qty:         int64  (8 bytes, offset 24)
    - blocked_margin_qty:    int64  (8 bytes, offset 32)
    - blocked_loan_qty:      int64  (8 bytes, offset 40)
    - blocked_judicial_qty:  int64  (8 bytes, offset 48)
    - total_qty:             int64  (8 bytes, offset 56)
    - updated_at_ns:         uint64 (8 bytes, offset 64)
    - symbol:                char[16] (16 bytes, offset 72)
    - isin:                  char[12] (12 bytes, offset 88)
    - _pad1:                 uint8[4] (4 bytes, offset 100)
    Tamanho total: 104 bytes (múltiplo de 8).
    """
    _fields_ = [
        ("participant_id", ctypes.c_uint32),
        ("_pad0", ctypes.c_uint32),
        ("account_id", ctypes.c_uint64),
        ("security_id", ctypes.c_uint64),
        ("available_qty", ctypes.c_int64),
        ("blocked_margin_qty", ctypes.c_int64),
        ("blocked_loan_qty", ctypes.c_int64),
        ("blocked_judicial_qty", ctypes.c_int64),
        ("total_qty", ctypes.c_int64),
        ("updated_at_ns", ctypes.c_uint64),
        ("symbol", ctypes.c_char * 16),
        ("isin", ctypes.c_char * 12),
        ("_pad1", ctypes.c_uint8 * 4),
    ]

    @property
    def symbol_str(self) -> str:
        return self.symbol.decode("ascii", errors="replace").rstrip("\x00")

    @symbol_str.setter
    def symbol_str(self, val: str) -> None:
        self.symbol = val.encode("ascii")[:16]

    @property
    def isin_str(self) -> str:
        return self.isin.decode("ascii", errors="replace").rstrip("\x00")

    @isin_str.setter
    def isin_str(self, val: str) -> None:
        self.isin = val.encode("ascii")[:12]

    def is_consistent(self) -> bool:
        """Verifica a Lei Canônica de Custódia B3: Total == Livre + Bloqueios."""
        expected_total = (
            self.available_qty
            + self.blocked_margin_qty
            + self.blocked_loan_qty
            + self.blocked_judicial_qty
        )
        return self.total_qty == expected_total

    def recalculate_total(self) -> int:
        """Recalcula e atribui total_qty com base nos saldos livres e bloqueios."""
        self.total_qty = (
            self.available_qty
            + self.blocked_margin_qty
            + self.blocked_loan_qty
            + self.blocked_judicial_qty
        )
        return self.total_qty


# ==============================================================================
# Barramento Central de Liquidação - Transferências e Saldos Multilaterais
# ==============================================================================

class MultilateralTransferStruct(CAbiStructure):
    """Instrução de Transferência Multilateral do Barramento Central.

    Layout (alinhamento C 8-byte):
    - transfer_id:         uint64 (8 bytes, offset 0)
    - instruction_id:      uint64 (8 bytes, offset 8)
    - cycle_id:            uint32 (4 bytes, offset 16)
    - subsystem:           uint32 (4 bytes, offset 20)
    - debtor_member_id:    uint32 (4 bytes, offset 24)
    - creditor_member_id:  uint32 (4 bytes, offset 28)
    - gross_amount_cents:  int64  (8 bytes, offset 32)
    - asset_id:            uint64 (8 bytes, offset 40)
    - asset_quantity:      int64  (8 bytes, offset 48)
    - status:              uint32 (4 bytes, offset 56)
    - _pad0:               uint32 (4 bytes, offset 60)
    - timestamp_ns:        uint64 (8 bytes, offset 64)
    Tamanho total: 72 bytes.
    """
    _fields_ = [
        ("transfer_id", ctypes.c_uint64),
        ("instruction_id", ctypes.c_uint64),
        ("cycle_id", ctypes.c_uint32),
        ("subsystem", ctypes.c_uint32),
        ("debtor_member_id", ctypes.c_uint32),
        ("creditor_member_id", ctypes.c_uint32),
        ("gross_amount_cents", ctypes.c_int64),
        ("asset_id", ctypes.c_uint64),
        ("asset_quantity", ctypes.c_int64),
        ("status", ctypes.c_uint32),
        ("_pad0", ctypes.c_uint32),
        ("timestamp_ns", ctypes.c_uint64),
    ]

    @property
    def gross_amount(self) -> float:
        """Montante em reais (R$)."""
        return self.gross_amount_cents / CENT_SCALE

    @gross_amount.setter
    def gross_amount(self, val: float) -> None:
        self.gross_amount_cents = int(round(val * CENT_SCALE))


class MultilateralNetPositionStruct(CAbiStructure):
    """Posição Líquida Multilateral Apurada para um Membro de Compensação.

    Layout (alinhamento C 8-byte):
    - member_id:               uint32 (4 bytes, offset 0)
    - cycle_id:                uint32 (4 bytes, offset 4)
    - gross_receivables_cents: int64  (8 bytes, offset 8)
    - gross_payables_cents:    int64  (8 bytes, offset 16)
    - net_balance_cents:       int64  (8 bytes, offset 24)
    - status:                  uint32 (4 bytes, offset 32)
    - _pad0:                   uint32 (4 bytes, offset 36)
    - updated_at_ns:           uint64 (8 bytes, offset 40)
    Tamanho total: 48 bytes.
    """
    _fields_ = [
        ("member_id", ctypes.c_uint32),
        ("cycle_id", ctypes.c_uint32),
        ("gross_receivables_cents", ctypes.c_int64),
        ("gross_payables_cents", ctypes.c_int64),
        ("net_balance_cents", ctypes.c_int64),
        ("status", ctypes.c_uint32),
        ("_pad0", ctypes.c_uint32),
        ("updated_at_ns", ctypes.c_uint64),
    ]

    @property
    def gross_receivables(self) -> float:
        return self.gross_receivables_cents / CENT_SCALE

    @property
    def gross_payables(self) -> float:
        return self.gross_payables_cents / CENT_SCALE

    @property
    def net_balance(self) -> float:
        return self.net_balance_cents / CENT_SCALE

    def compute_net_balance(self) -> int:
        """Calcula a posição líquida: Recebíveis - Pagáveis."""
        self.net_balance_cents = self.gross_receivables_cents - self.gross_payables_cents
        return self.net_balance_cents

    def is_net_balanced(self) -> bool:
        """Verifica se o net_balance_cents reflete estritamente a diferença."""
        return self.net_balance_cents == (self.gross_receivables_cents - self.gross_payables_cents)


# ==============================================================================
# Funções Utilitárias Zero-Copy e Interop de Alto Desempenho
# ==============================================================================

def zero_copy_view(c_struct: ctypes.Structure) -> memoryview:
    """Retorna um memoryview zero-copy de bytes mapeado sobre a memória da estrutura ctypes."""
    return memoryview(c_struct).cast("B")


def zero_copy_from_buffer(
    struct_cls: Type[T],
    buf: Union[bytearray, memoryview],
    offset: int = 0,
) -> T:
    """Mapeia uma estrutura ctypes sobre uma região gravável de memória sem efetuar cópia."""
    return struct_cls.from_buffer(buf, offset)


def batch_decode_sbe_stream(
    data: Union[bytearray, memoryview, bytes],
) -> Iterator[Tuple[int, CAbiStructure]]:
    """Iterador zero-copy para decodificar um fluxo contínuo de mensagens PUMA SBE v2."""
    offset = 0
    total_len = len(data)
    header_struct = SbeHeaderStruct

    while offset + ctypes.sizeof(header_struct) <= total_len:
        # Se for bytes imutável usamos from_buffer_copy, se for bytearray/memoryview usamos from_buffer
        if isinstance(data, bytes):
            hdr = header_struct.from_buffer_copy(data, offset)
        else:
            hdr = header_struct.from_buffer(data, offset)

        template_id = hdr.template_id

        if template_id == TEMPLATE_NEW_ORDER:
            target_cls = NewOrderSingleStruct
        elif template_id == TEMPLATE_ORDER_CANCEL:
            target_cls = OrderCancelStruct
        elif template_id == TEMPLATE_EXECUTION_REPORT:
            target_cls = ExecutionReportStruct
        else:
            # Template não suportado ou fim de stream alinhado
            break

        msg_size = ctypes.sizeof(target_cls)
        if offset + msg_size > total_len:
            break

        if isinstance(data, bytes):
            msg = target_cls.from_buffer_copy(data, offset)
        else:
            msg = target_cls.from_buffer(data, offset)

        yield (template_id, msg)
        offset += msg_size
