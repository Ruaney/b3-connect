"""Módulo de Mensageria Binária de Baixa Latência — B3 Puma Binary Entrypoint (SBE).

Implementa o protocolo Simple Binary Encoding (SBE) padrão Little-Endian para algoritmos
HFT (High-Frequency Trading) e Formadores de Mercado (Market Makers) no PUMA Trading System.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import struct
from typing import Any, ClassVar, Iterator, Optional, Union


# Constantes de Protocolo SBE
HEADER_FORMAT: str = "<HHHH"
HEADER_SIZE: int = struct.calcsize(HEADER_FORMAT)  # 8 bytes
HEADER_STRUCT: struct.Struct = struct.Struct(HEADER_FORMAT)
PRICE_SCALE: int = 100_000_000  # 10^8 (8 casas decimais)
DEFAULT_SCHEMA_ID: int = 1
DEFAULT_SCHEMA_VERSION: int = 1

TEMPLATE_NEW_ORDER: int = 101
TEMPLATE_ORDER_CANCEL: int = 102
TEMPLATE_EXECUTION_REPORT: int = 103


class Side(IntEnum):
    """Lado da Ordem no PUMA Trading System."""

    BUY = 1
    SELL = 2


class TimeInForce(IntEnum):
    """Validade da Ordem."""

    DAY = 0  # Válida para o dia
    GTC = 1  # Good 'Til Cancel
    IOC = 3  # Immediate or Cancel
    FOK = 4  # Fill or Kill


class ExecType(IntEnum):
    """Tipo do Evento de Execução."""

    NEW = 0
    PARTIAL_FILL = 1
    FILL = 2
    CANCELLED = 4
    REJECTED = 8


class OrdStatus(IntEnum):
    """Estado da Ordem."""

    NEW = 0
    PARTIALLY_FILLED = 1
    FILLED = 2
    CANCELLED = 4
    REJECTED = 8


# Exceções Específicas do Protocolo SBE
class PumaSbeError(Exception):
    """Exceção base para erros no protocolo binário PUMA SBE."""


class SbeTruncatedPayloadError(PumaSbeError):
    """Disparado quando o buffer binário é truncado ou menor que o comprimento esperado."""


class SbeUnknownTemplateError(PumaSbeError):
    """Disparado quando o TemplateId informado não é reconhecido no catálogo de mensagens."""


class SbeValidationError(PumaSbeError):
    """Disparado quando campos ou valores de negócio violam as regras estritas da B3."""


@dataclass(slots=True)
class SbeHeader:
    """Cabeçalho canônico SBE (Message Header / Framing Header).

    Campos:
        block_length (uint16): Comprimento em bytes do bloco raiz da mensagem.
        template_id (uint16): Identificador unívoco do template de mensagem.
        schema_id (uint16): Identificador do schema B3 SBE (padrão: 1).
        version (uint16): Versão do schema/template (padrão: 1).
    """

    block_length: int
    template_id: int
    schema_id: int = DEFAULT_SCHEMA_ID
    version: int = DEFAULT_SCHEMA_VERSION

    FORMAT: ClassVar[str] = HEADER_FORMAT
    SIZE: ClassVar[int] = HEADER_SIZE
    STRUCT: ClassVar[struct.Struct] = HEADER_STRUCT

    def encode(self) -> bytes:
        """Serializa o cabeçalho em 8 bytes Little-Endian."""
        return self.STRUCT.pack(
            self.block_length,
            self.template_id,
            self.schema_id,
            self.version,
        )

    @classmethod
    def decode(
        cls,
        data: Union[bytes, bytearray, memoryview],
        offset: int = 0,
    ) -> SbeHeader:
        """Desserializa o cabeçalho a partir de um buffer de bytes sem cópias de heap desnecessárias."""
        data_len = len(data) - offset
        if data_len < cls.SIZE:
            raise SbeTruncatedPayloadError(
                f"Payload truncado para cabeçalho SBE: esperado ao menos {cls.SIZE} bytes, "
                f"recebido {data_len} bytes."
            )
        block_length, template_id, schema_id, version = cls.STRUCT.unpack_from(data, offset)
        return cls(
            block_length=block_length,
            template_id=template_id,
            schema_id=schema_id,
            version=version,
        )


@dataclass(slots=True)
class NewOrderSingleBinary:
    """Mensagem de Inserção de Nova Ordem (TemplateId = 101).

    Campos:
        cl_ord_id (uint64): Identificador da ordem pelo cliente.
        security_id (uint64): Identificador numérico do instrumento na B3.
        side (uint8): 1=Buy, 2=Sell.
        order_qty (uint32): Quantidade da ordem.
        price (float): Preço da ordem (em reais, convertido para escala 10^8 internamente).
        time_in_force (uint8): Validade da ordem (padrão: DAY=0).
        account (uint32): Código da conta/comitente.
    """

    cl_ord_id: int
    security_id: int
    side: int
    order_qty: int
    price: float
    time_in_force: int = TimeInForce.DAY
    account: int = 0
    header: SbeHeader = field(
        default_factory=lambda: SbeHeader(
            block_length=34,
            template_id=TEMPLATE_NEW_ORDER,
        )
    )

    TEMPLATE_ID: ClassVar[int] = TEMPLATE_NEW_ORDER
    BLOCK_LENGTH: ClassVar[int] = 34
    PAYLOAD_FORMAT: ClassVar[str] = "<QQBIqBI"
    TOTAL_SIZE: ClassVar[int] = HEADER_SIZE + BLOCK_LENGTH  # 42 bytes
    STRUCT: ClassVar[struct.Struct] = struct.Struct(PAYLOAD_FORMAT)

    @property
    def price_scaled(self) -> int:
        """Retorna o preço codificado como int64 com escala de 8 decimais."""
        return int(round(self.price * PRICE_SCALE))

    def validate(self) -> None:
        """Valida semanticamente os campos da ordem conforme regras da B3."""
        if self.cl_ord_id <= 0:
            raise SbeValidationError(f"ClOrdID deve ser estritamente positivo: {self.cl_ord_id}")
        if self.security_id <= 0:
            raise SbeValidationError(f"SecurityID deve ser estritamente positivo: {self.security_id}")
        if self.side not in (1, 2):
            raise SbeValidationError(f"Side inválido: {self.side}. Deve ser 1 (Buy) ou 2 (Sell).")
        if self.order_qty <= 0:
            raise SbeValidationError(f"OrderQty deve ser estritamente positivo: {self.order_qty}")
        if self.price <= 0:
            raise SbeValidationError(f"Price deve ser estritamente positivo: {self.price}")
        if self.time_in_force not in (0, 1, 3, 4):
            raise SbeValidationError(f"TimeInForce inválido: {self.time_in_force}")
        if self.account <= 0:
            raise SbeValidationError(f"Account deve ser estritamente positivo: {self.account}")

    def encode(self) -> bytes:
        """Serializa a ordem completa (cabeçalho + payload) em 42 bytes binários."""
        self.validate()
        header_bytes = self.header.encode()
        payload_bytes = self.STRUCT.pack(
            self.cl_ord_id,
            self.security_id,
            int(self.side),
            self.order_qty,
            self.price_scaled,
            int(self.time_in_force),
            self.account,
        )
        return header_bytes + payload_bytes

    @classmethod
    def unpack_raw(
        cls,
        data: Union[bytes, bytearray, memoryview],
        offset: int = 0,
    ) -> tuple[int, int, int, int, int, int, int]:
        """Decodificação zero-copy ultrarrápida do payload retornando tupla direta sem alocação de objeto."""
        return cls.STRUCT.unpack_from(data, offset + HEADER_SIZE)

    @classmethod
    def decode(
        cls,
        data: Union[bytes, bytearray, memoryview],
        offset: int = 0,
        header: Optional[SbeHeader] = None,
        validate: bool = True,
    ) -> NewOrderSingleBinary:
        """Desserializa a ordem completa a partir de um buffer de bytes sem cópias de heap desnecessárias."""
        data_len = len(data) - offset
        if data_len < cls.TOTAL_SIZE:
            raise SbeTruncatedPayloadError(
                f"Payload truncado para NewOrderSingle: esperado {cls.TOTAL_SIZE} bytes, "
                f"recebido {data_len} bytes."
            )
        if header is None:
            header = SbeHeader.decode(data, offset=offset)
        if header.template_id != cls.TEMPLATE_ID:
            raise SbeValidationError(
                f"TemplateId divergente para NewOrderSingle: esperado {cls.TEMPLATE_ID}, "
                f"recebido {header.template_id}"
            )
        if header.block_length != cls.BLOCK_LENGTH:
            raise SbeValidationError(
                f"BlockLength divergente para NewOrderSingle: esperado {cls.BLOCK_LENGTH}, "
                f"recebido {header.block_length}"
            )

        (
            cl_ord_id,
            security_id,
            side,
            order_qty,
            price_scaled,
            time_in_force,
            account,
        ) = cls.STRUCT.unpack_from(data, offset + HEADER_SIZE)

        price = price_scaled / PRICE_SCALE

        order = cls(
            cl_ord_id=cl_ord_id,
            security_id=security_id,
            side=side,
            order_qty=order_qty,
            price=price,
            time_in_force=time_in_force,
            account=account,
            header=header,
        )
        if validate:
            order.validate()
        return order


@dataclass(slots=True)
class OrderCancelBinary:
    """Mensagem de Cancelamento de Ordem (TemplateId = 102).

    Campos:
        cl_ord_id (uint64): Identificador do pedido de cancelamento.
        orig_cl_ord_id (uint64): Identificador da ordem original a ser cancelada.
        security_id (uint64): Identificador numérico do instrumento na B3.
        account (uint32): Código da conta/comitente.
    """

    cl_ord_id: int
    orig_cl_ord_id: int
    security_id: int
    account: int
    header: SbeHeader = field(
        default_factory=lambda: SbeHeader(
            block_length=28,
            template_id=TEMPLATE_ORDER_CANCEL,
        )
    )

    TEMPLATE_ID: ClassVar[int] = TEMPLATE_ORDER_CANCEL
    BLOCK_LENGTH: ClassVar[int] = 28
    PAYLOAD_FORMAT: ClassVar[str] = "<QQQI"
    TOTAL_SIZE: ClassVar[int] = HEADER_SIZE + BLOCK_LENGTH  # 36 bytes
    STRUCT: ClassVar[struct.Struct] = struct.Struct(PAYLOAD_FORMAT)

    def validate(self) -> None:
        """Valida os campos de cancelamento de ordem."""
        if self.cl_ord_id <= 0:
            raise SbeValidationError(f"ClOrdID deve ser estritamente positivo: {self.cl_ord_id}")
        if self.orig_cl_ord_id <= 0:
            raise SbeValidationError(f"OrigClOrdID deve ser estritamente positivo: {self.orig_cl_ord_id}")
        if self.security_id <= 0:
            raise SbeValidationError(f"SecurityID deve ser estritamente positivo: {self.security_id}")
        if self.account <= 0:
            raise SbeValidationError(f"Account deve ser estritamente positivo: {self.account}")

    def encode(self) -> bytes:
        """Serializa o cancelamento de ordem em 36 bytes binários."""
        self.validate()
        header_bytes = self.header.encode()
        payload_bytes = self.STRUCT.pack(
            self.cl_ord_id,
            self.orig_cl_ord_id,
            self.security_id,
            self.account,
        )
        return header_bytes + payload_bytes

    @classmethod
    def unpack_raw(
        cls,
        data: Union[bytes, bytearray, memoryview],
        offset: int = 0,
    ) -> tuple[int, int, int, int]:
        """Decodificação zero-copy ultrarrápida do payload retornando tupla direta sem alocação de objeto."""
        return cls.STRUCT.unpack_from(data, offset + HEADER_SIZE)

    @classmethod
    def decode(
        cls,
        data: Union[bytes, bytearray, memoryview],
        offset: int = 0,
        header: Optional[SbeHeader] = None,
        validate: bool = True,
    ) -> OrderCancelBinary:
        """Desserializa o cancelamento de ordem a partir de bytes sem overhead de heap desnecessário."""
        data_len = len(data) - offset
        if data_len < cls.TOTAL_SIZE:
            raise SbeTruncatedPayloadError(
                f"Payload truncado para OrderCancel: esperado {cls.TOTAL_SIZE} bytes, "
                f"recebido {data_len} bytes."
            )
        if header is None:
            header = SbeHeader.decode(data, offset=offset)
        if header.template_id != cls.TEMPLATE_ID:
            raise SbeValidationError(
                f"TemplateId divergente para OrderCancel: esperado {cls.TEMPLATE_ID}, "
                f"recebido {header.template_id}"
            )
        if header.block_length != cls.BLOCK_LENGTH:
            raise SbeValidationError(
                f"BlockLength divergente para OrderCancel: esperado {cls.BLOCK_LENGTH}, "
                f"recebido {header.block_length}"
            )

        cl_ord_id, orig_cl_ord_id, security_id, account = cls.STRUCT.unpack_from(
            data, offset + HEADER_SIZE
        )

        cancel = cls(
            cl_ord_id=cl_ord_id,
            orig_cl_ord_id=orig_cl_ord_id,
            security_id=security_id,
            account=account,
            header=header,
        )
        if validate:
            cancel.validate()
        return cancel


@dataclass(slots=True)
class ExecutionReportBinary:
    """Mensagem de Relatório de Execução (TemplateId = 103).

    Campos:
        order_id (uint64): Identificador unívoco gerado pelo motor PUMA.
        cl_ord_id (uint64): Identificador atribuído pelo cliente.
        exec_id (uint64): Identificador unívoco do evento de execução.
        exec_type (uint8): Tipo do evento (0=New, 1=Partial, 2=Fill, 4=Cancelled, 8=Rejected).
        ord_status (uint8): Estado da ordem (0=New, 1=Partial, 2=Filled, 4=Cancelled, 8=Rejected).
        security_id (uint64): Identificador numérico do instrumento na B3.
        cum_qty (uint32): Quantidade cumulativa executada.
        leaves_qty (uint32): Quantidade remanescente aberta no livro.
        last_px (float): Preço da última execução parcial/total (convertido de 10^8).
        last_qty (uint32): Quantidade da última execução parcial/total.
    """

    order_id: int
    cl_ord_id: int
    exec_id: int
    exec_type: int
    ord_status: int
    security_id: int
    cum_qty: int
    leaves_qty: int
    last_px: float
    last_qty: int
    header: SbeHeader = field(
        default_factory=lambda: SbeHeader(
            block_length=54,
            template_id=TEMPLATE_EXECUTION_REPORT,
        )
    )

    TEMPLATE_ID: ClassVar[int] = TEMPLATE_EXECUTION_REPORT
    BLOCK_LENGTH: ClassVar[int] = 54
    PAYLOAD_FORMAT: ClassVar[str] = "<QQQBBQIIqI"
    TOTAL_SIZE: ClassVar[int] = HEADER_SIZE + BLOCK_LENGTH  # 62 bytes
    STRUCT: ClassVar[struct.Struct] = struct.Struct(PAYLOAD_FORMAT)

    @property
    def last_px_scaled(self) -> int:
        """Retorna o preço LastPx com escala de 8 decimais."""
        return int(round(self.last_px * PRICE_SCALE))

    def validate(self) -> None:
        """Valida os campos do relatório de execução."""
        if self.order_id <= 0:
            raise SbeValidationError(f"OrderID deve ser estritamente positivo: {self.order_id}")
        if self.cl_ord_id <= 0:
            raise SbeValidationError(f"ClOrdID deve ser estritamente positivo: {self.cl_ord_id}")
        if self.exec_id <= 0:
            raise SbeValidationError(f"ExecID deve ser estritamente positivo: {self.exec_id}")
        if self.exec_type not in (0, 1, 2, 4, 8):
            raise SbeValidationError(f"ExecType inválido: {self.exec_type}")
        if self.ord_status not in (0, 1, 2, 4, 8):
            raise SbeValidationError(f"OrdStatus inválido: {self.ord_status}")
        if self.security_id <= 0:
            raise SbeValidationError(f"SecurityID deve ser estritamente positivo: {self.security_id}")
        if self.cum_qty < 0:
            raise SbeValidationError(f"CumQty não pode ser negativo: {self.cum_qty}")
        if self.leaves_qty < 0:
            raise SbeValidationError(f"LeavesQty não pode ser negativo: {self.leaves_qty}")
        if self.last_px < 0:
            raise SbeValidationError(f"LastPx não pode ser negativo: {self.last_px}")
        if self.last_qty < 0:
            raise SbeValidationError(f"LastQty não pode ser negativo: {self.last_qty}")

    def encode(self) -> bytes:
        """Serializa o relatório de execução em 62 bytes binários."""
        self.validate()
        header_bytes = self.header.encode()
        payload_bytes = self.STRUCT.pack(
            self.order_id,
            self.cl_ord_id,
            self.exec_id,
            int(self.exec_type),
            int(self.ord_status),
            self.security_id,
            self.cum_qty,
            self.leaves_qty,
            self.last_px_scaled,
            self.last_qty,
        )
        return header_bytes + payload_bytes

    @classmethod
    def unpack_raw(
        cls,
        data: Union[bytes, bytearray, memoryview],
        offset: int = 0,
    ) -> tuple[int, int, int, int, int, int, int, int, int, int]:
        """Decodificação zero-copy ultrarrápida do payload retornando tupla direta sem alocação de objeto."""
        return cls.STRUCT.unpack_from(data, offset + HEADER_SIZE)

    @classmethod
    def decode(
        cls,
        data: Union[bytes, bytearray, memoryview],
        offset: int = 0,
        header: Optional[SbeHeader] = None,
        validate: bool = True,
    ) -> ExecutionReportBinary:
        """Desserializa o relatório de execução a partir de bytes sem overhead de heap desnecessário."""
        data_len = len(data) - offset
        if data_len < cls.TOTAL_SIZE:
            raise SbeTruncatedPayloadError(
                f"Payload truncado para ExecutionReport: esperado {cls.TOTAL_SIZE} bytes, "
                f"recebido {data_len} bytes."
            )
        if header is None:
            header = SbeHeader.decode(data, offset=offset)
        if header.template_id != cls.TEMPLATE_ID:
            raise SbeValidationError(
                f"TemplateId divergente para ExecutionReport: esperado {cls.TEMPLATE_ID}, "
                f"recebido {header.template_id}"
            )
        if header.block_length != cls.BLOCK_LENGTH:
            raise SbeValidationError(
                f"BlockLength divergente para ExecutionReport: esperado {cls.BLOCK_LENGTH}, "
                f"recebido {header.block_length}"
            )

        (
            order_id,
            cl_ord_id,
            exec_id,
            exec_type,
            ord_status,
            security_id,
            cum_qty,
            leaves_qty,
            last_px_scaled,
            last_qty,
        ) = cls.STRUCT.unpack_from(data, offset + HEADER_SIZE)

        last_px = last_px_scaled / PRICE_SCALE

        report = cls(
            order_id=order_id,
            cl_ord_id=cl_ord_id,
            exec_id=exec_id,
            exec_type=exec_type,
            ord_status=ord_status,
            security_id=security_id,
            cum_qty=cum_qty,
            leaves_qty=leaves_qty,
            last_px=last_px,
            last_qty=last_qty,
            header=header,
        )
        if validate:
            report.validate()
        return report


SbeMessage = Union[NewOrderSingleBinary, OrderCancelBinary, ExecutionReportBinary]


class PumaSbeCodec:
    """Codec e despachante de alto desempenho para mensagens binárias PUMA SBE."""

    TEMPLATES: ClassVar[dict[int, type[SbeMessage]]] = {
        TEMPLATE_NEW_ORDER: NewOrderSingleBinary,
        TEMPLATE_ORDER_CANCEL: OrderCancelBinary,
        TEMPLATE_EXECUTION_REPORT: ExecutionReportBinary,
    }

    @staticmethod
    def decode_header(
        data: Union[bytes, bytearray, memoryview], offset: int = 0
    ) -> SbeHeader:
        """Decodifica e valida o cabeçalho SBE de 8 bytes sem cópias."""
        return SbeHeader.decode(data, offset=offset)

    @classmethod
    def decode(
        cls,
        data: Union[bytes, bytearray, memoryview],
        offset: int = 0,
        validate: bool = True,
    ) -> SbeMessage:
        """Decodifica dinamicamente qualquer mensagem SBE suportada pelo catálogo sem overhead de heap desnecessário.

        Examina o TemplateId no cabeçalho e invoca o parser correspondente diretamente via offset.

        Raises:
            SbeTruncatedPayloadError: Se os dados forem menores que o cabeçalho ou o bloco.
            SbeUnknownTemplateError: Se o TemplateId não pertencer ao catálogo.
            SbeValidationError: Se a mensagem violar integridade de negócio.
        """
        data_len = len(data) - offset
        if data_len < HEADER_SIZE:
            raise SbeTruncatedPayloadError(
                f"Buffer insuficiente para cabeçalho SBE: esperado ao menos {HEADER_SIZE} bytes, "
                f"recebido {data_len} bytes."
            )

        block_length, template_id, schema_id, version = HEADER_STRUCT.unpack_from(data, offset)
        message_cls = cls.TEMPLATES.get(template_id)
        if message_cls is None:
            raise SbeUnknownTemplateError(
                f"TemplateId {template_id} desconhecido no catálogo PUMA SBE. "
                f"Templates válidos: {list(cls.TEMPLATES.keys())}"
            )

        total_expected = HEADER_SIZE + block_length
        if data_len < total_expected:
            raise SbeTruncatedPayloadError(
                f"Buffer truncado para template {template_id}: "
                f"esperado {total_expected} bytes (header={HEADER_SIZE} + block={block_length}), "
                f"recebido {data_len} bytes."
            )

        header = SbeHeader(block_length, template_id, schema_id, version)
        return message_cls.decode(data, offset=offset, header=header, validate=validate)

    @classmethod
    def iter_decode(
        cls,
        data: Union[bytes, bytearray, memoryview],
        validate: bool = True,
    ) -> Iterator[SbeMessage]:
        """Itera e decodifica sequencialmente um fluxo de mensagens SBE contíguas com zero overhead de fatiamento."""
        offset = 0
        total_len = len(data)
        while offset < total_len:
            rem = total_len - offset
            if rem < HEADER_SIZE:
                raise SbeTruncatedPayloadError(
                    f"Buffer insuficiente para cabeçalho SBE no offset {offset}: "
                    f"esperado ao menos {HEADER_SIZE} bytes, recebido {rem} bytes."
                )
            block_length, template_id, schema_id, version = HEADER_STRUCT.unpack_from(data, offset)
            message_cls = cls.TEMPLATES.get(template_id)
            if message_cls is None:
                raise SbeUnknownTemplateError(
                    f"TemplateId {template_id} desconhecido no catálogo PUMA SBE."
                )
            total_expected = HEADER_SIZE + block_length
            if rem < total_expected:
                raise SbeTruncatedPayloadError(
                    f"Buffer truncado para template {template_id} no offset {offset}: "
                    f"esperado {total_expected} bytes, recebido {rem} bytes."
                )
            header = SbeHeader(block_length, template_id, schema_id, version)
            yield message_cls.decode(data, offset=offset, header=header, validate=validate)
            offset += total_expected

    @classmethod
    def decode_stream(
        cls,
        data: Union[bytes, bytearray, memoryview],
        validate: bool = True,
    ) -> Iterator[SbeMessage]:
        """Alias para iter_decode."""
        return cls.iter_decode(data, validate=validate)

    @classmethod
    def encode(cls, message: Any) -> bytes:
        """Serializa a mensagem SBE em bytes binários."""
        if not isinstance(
            message, (NewOrderSingleBinary, OrderCancelBinary, ExecutionReportBinary)
        ):
            raise TypeError(f"Objeto não suportado para codificação SBE: {type(message)}")
        return message.encode()
