"""Módulo de Captura de Operações em Bloco (FL-007) — B3 Real-Time Clearing (RTC).

Este módulo implementa o modelo de mensagens, parsing determinístico e validações
estritas de regras de negócio para a captura de operações em bloco e trades
no ambiente do Real-Time Clearing (RTC) da B3, utilizando exclusivamente
a biblioteca padrão Python.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Union

# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------

class TradeSide(str, Enum):
    """Lado da operação de negociação (Trade Side)."""
    BUY = "BUY"
    SELL = "SELL"

    @classmethod
    def from_str(cls, value: Any) -> TradeSide:
        """Converte e normaliza strings ou códigos numéricos para TradeSide."""
        if isinstance(value, TradeSide):
            return value
        if not value:
            raise InvalidValueError("Lado da operação (Side) não pode ser nulo ou vazio.")

        s = str(value).strip().upper()
        if s in ("BUY", "B", "1", "COMPRA", "C"):
            return cls.BUY
        if s in ("SELL", "S", "2", "VENDA", "V"):
            return cls.SELL
        raise InvalidValueError(
            f"Lado da operação inválido: {value!r}. Valores permitidos: BUY, SELL (ou B, S, 1, 2)."
        )


class MarketType(str, Enum):
    """Segmento de mercado da B3."""
    EQUITY = "EQUITY"
    OPTIONS = "OPTIONS"
    FUTURES = "FUTURES"
    FORWARD = "FORWARD"

    @classmethod
    def from_str(cls, value: Any) -> MarketType:
        """Converte e normaliza strings de mercado para MarketType."""
        if isinstance(value, MarketType):
            return value
        if not value:
            raise InvalidValueError("Segmento de mercado (MarketType) não pode ser nulo ou vazio.")

        s = str(value).strip().upper()
        mapping = {
            "EQUITY": cls.EQUITY,
            "VISTA": cls.EQUITY,
            "CASH": cls.EQUITY,
            "A VISTA": cls.EQUITY,
            "OPTIONS": cls.OPTIONS,
            "OPCOES": cls.OPTIONS,
            "OPÇÕES": cls.OPTIONS,
            "FUTURES": cls.FUTURES,
            "FUTUROS": cls.FUTURES,
            "FORWARD": cls.FORWARD,
            "TERMO": cls.FORWARD,
        }
        if s in mapping:
            return mapping[s]
        raise InvalidValueError(
            f"Segmento de mercado inválido: {value!r}. Valores aceitos: EQUITY, OPTIONS, FUTURES, FORWARD."
        )


class AllocationStatus(str, Enum):
    """Status de alocação de um trade no RTC."""
    UNALLOCATED = "Unallocated"
    PENDING = "Pending"
    ALLOCATED = "Allocated"

    @classmethod
    def from_str(cls, value: Any) -> AllocationStatus:
        """Converte e normaliza status de alocação para AllocationStatus."""
        if isinstance(value, AllocationStatus):
            return value
        if not value:
            raise InvalidValueError("Status de alocação (AllocationStatus) não pode ser nulo ou vazio.")

        s = str(value).strip().lower()
        mapping = {
            "unallocated": cls.UNALLOCATED,
            "nao_alocado": cls.UNALLOCATED,
            "nao alocado": cls.UNALLOCATED,
            "pending": cls.PENDING,
            "pendente": cls.PENDING,
            "allocated": cls.ALLOCATED,
            "alocado": cls.ALLOCATED,
        }
        if s in mapping:
            return mapping[s]
        raise InvalidValueError(
            f"Status de alocação inválido: {value!r}. Valores permitidos: Unallocated, Pending, Allocated."
        )


# ---------------------------------------------------------------------------
# Hierarquia de Exceções Especializadas
# ---------------------------------------------------------------------------

class RTCError(Exception):
    """Exceção base para erros do protocolo RTC."""
    pass


class RTCValidationError(RTCError):
    """Exceção para falhas de validação sintática ou de regras de negócio."""
    pass


class MissingRequiredFieldError(RTCValidationError):
    """Lançada quando um campo obrigatório está ausente ou vazio."""
    def __init__(self, field_name: str, record_index: Optional[int] = None) -> None:
        self.field_name = field_name
        self.record_index = record_index
        loc = f" no registro #{record_index}" if record_index is not None else ""
        super().__init__(f"Campo obrigatório ausente ou vazio: '{field_name}'{loc}.")


class NegativeOrZeroValueError(RTCValidationError):
    """Lançada quando preço ou quantidade for nula, zero ou negativa."""
    def __init__(self, field_name: str, value: Any, record_index: Optional[int] = None) -> None:
        self.field_name = field_name
        self.value = value
        self.record_index = record_index
        loc = f" no registro #{record_index}" if record_index is not None else ""
        super().__init__(
            f"Valor inválido para o campo '{field_name}': {value!r}{loc}. "
            f"O valor deve ser estritamente positivo (> 0)."
        )


class InvalidValueError(RTCValidationError):
    """Lançada quando um campo contém valor fora do domínio ou formato esperado."""
    pass


class BatchIntegrityError(RTCValidationError):
    """Lançada quando a contagem física do lote diverge do totalizador declarado."""
    def __init__(self, declared_total: int, actual_total: int) -> None:
        self.declared_total = declared_total
        self.actual_total = actual_total
        super().__init__(
            f"Divergência de integridade no lote RTC: 'total_records' declarado é {declared_total}, "
            f"mas foram encontrados {actual_total} registros."
        )


# ---------------------------------------------------------------------------
# Modelos de Dados (Dataclasses)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TradeRecord:
    """Representação estruturada e validada de uma operação de negociação no RTC."""

    trade_id: str
    trade_date: date
    trade_time: time
    symbol: str
    isin: str
    market_type: MarketType
    side: TradeSide
    quantity: Decimal
    price: Decimal
    counterparty: str
    executing_broker: str
    clearing_broker: str
    block_trade_flag: bool
    allocation_status: AllocationStatus
    exec_id: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = field(default=None, repr=False, compare=False)

    @property
    def financial_volume(self) -> Decimal:
        """Calcula o volume financeiro bruto da operação (Quantidade * Preço)."""
        return self.quantity * self.price

    @property
    def is_block_trade(self) -> bool:
        """Informa se a operação é expressamente classificada como operação em bloco."""
        return bool(self.block_trade_flag)

    def to_dict(self, pascal_case: bool = True) -> Dict[str, Any]:
        """Serializa o registro para dicionário JSON-compatível."""
        if pascal_case:
            res: Dict[str, Any] = {
                "TradeID": self.trade_id,
                "TradeDate": self.trade_date.isoformat(),
                "TradeTime": self.trade_time.isoformat(),
                "Symbol": self.symbol,
                "ISIN": self.isin,
                "MarketType": self.market_type.value,
                "Side": self.side.value,
                "Quantity": int(self.quantity) if self.quantity % 1 == 0 else float(self.quantity),
                "Price": str(self.price),
                "FinancialVolume": str(self.financial_volume),
                "Counterparty": self.counterparty,
                "ExecutingBroker": self.executing_broker,
                "ClearingBroker": self.clearing_broker,
                "BlockTradeFlag": self.block_trade_flag,
                "AllocationStatus": self.allocation_status.value,
            }
            if self.exec_id:
                res["ExecID"] = self.exec_id
            return res
        else:
            res = {
                "trade_id": self.trade_id,
                "trade_date": self.trade_date.isoformat(),
                "trade_time": self.trade_time.isoformat(),
                "symbol": self.symbol,
                "isin": self.isin,
                "market_type": self.market_type.value,
                "side": self.side.value,
                "quantity": int(self.quantity) if self.quantity % 1 == 0 else float(self.quantity),
                "price": str(self.price),
                "financial_volume": str(self.financial_volume),
                "counterparty": self.counterparty,
                "executing_broker": self.executing_broker,
                "clearing_broker": self.clearing_broker,
                "block_trade_flag": self.block_trade_flag,
                "allocation_status": self.allocation_status.value,
            }
            if self.exec_id:
                res["exec_id"] = self.exec_id
            return res


@dataclass
class TradeCaptureBatch:
    """Lote de captura de operações de negociação do RTC."""

    batch_id: str
    source: str
    timestamp: datetime
    total_records: int
    records: List[TradeRecord] = field(default_factory=list)

    @property
    def total_quantity(self) -> Decimal:
        """Soma cumulativa das quantidades de todos os trades do lote."""
        return sum((r.quantity for r in self.records), Decimal(0))

    @property
    def total_financial_volume(self) -> Decimal:
        """Soma cumulativa do volume financeiro bruto de todos os trades do lote."""
        return sum((r.financial_volume for r in self.records), Decimal(0))

    def filter_by_status(self, status: Union[str, AllocationStatus]) -> List[TradeRecord]:
        """Retorna operações filtradas pelo status de alocação."""
        target = AllocationStatus.from_str(status)
        return [r for r in self.records if r.allocation_status == target]

    def filter_by_symbol(self, symbol: str) -> List[TradeRecord]:
        """Retorna operações de um determinado ticker/ativo."""
        sym_clean = symbol.strip().upper()
        return [r for r in self.records if r.symbol.upper() == sym_clean]

    def filter_by_market(self, market: Union[str, MarketType]) -> List[TradeRecord]:
        """Retorna operações de um segmento de mercado específico."""
        target = MarketType.from_str(market)
        return [r for r in self.records if r.market_type == target]

    def unallocated_trades(self) -> List[TradeRecord]:
        """Retorna todos os negócios brutos não alocados."""
        return self.filter_by_status(AllocationStatus.UNALLOCATED)

    def pending_trades(self) -> List[TradeRecord]:
        """Retorna negócios pendentes de aceite ou confirmação de alocação."""
        return self.filter_by_status(AllocationStatus.PENDING)

    def allocated_trades(self) -> List[TradeRecord]:
        """Retorna negócios cuja alocação em conta final já foi concluída."""
        return self.filter_by_status(AllocationStatus.ALLOCATED)

    def to_dict(self, pascal_case: bool = True) -> Dict[str, Any]:
        """Serializa o lote completo para dicionário Python."""
        if pascal_case:
            return {
                "BatchID": self.batch_id,
                "Source": self.source,
                "Timestamp": self.timestamp.isoformat(),
                "TotalRecords": self.total_records,
                "TotalQuantity": int(self.total_quantity) if self.total_quantity % 1 == 0 else float(self.total_quantity),
                "TotalFinancialVolume": str(self.total_financial_volume),
                "Records": [r.to_dict(pascal_case=True) for r in self.records],
            }
        else:
            return {
                "batch_id": self.batch_id,
                "source": self.source,
                "timestamp": self.timestamp.isoformat(),
                "total_records": self.total_records,
                "total_quantity": int(self.total_quantity) if self.total_quantity % 1 == 0 else float(self.total_quantity),
                "total_financial_volume": str(self.total_financial_volume),
                "records": [r.to_dict(pascal_case=False) for r in self.records],
            }


# ---------------------------------------------------------------------------
# Parser e Validador de Negócio
# ---------------------------------------------------------------------------

_ISIN_REGEX = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class TradeCaptureParser:
    """Parser e validador determinístico para mensagens de captura de trades RTC."""

    def __init__(self, check_batch_integrity: bool = True) -> None:
        self.check_batch_integrity = check_batch_integrity

    def parse_file(self, file_path: Union[str, Path]) -> TradeCaptureBatch:
        """Lê um arquivo JSON do disco e realiza o parsing do lote."""
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"Arquivo RTC não encontrado: {p}")
        with p.open("r", encoding="utf-8") as f:
            content = f.read()
        return self.parse_json(content)

    def parse_json(self, json_str: str) -> TradeCaptureBatch:
        """Faz o parsing a partir de uma string JSON."""
        try:
            payload = json.loads(json_str)
        except json.JSONDecodeError as err:
            raise RTCValidationError(f"Payload JSON inválido: {err}") from err

        if not isinstance(payload, dict):
            raise RTCValidationError(
                f"Estrutura raiz do lote RTC deve ser um objeto JSON (dict), recebido: {type(payload).__name__}"
            )
        return self.parse_dict(payload)

    def parse_dict(self, data: Dict[str, Any]) -> TradeCaptureBatch:
        """Processa um dicionário Python contendo os dados do lote RTC."""
        # 1. Metadados do lote (suporta PascalCase e snake_case)
        batch_id = self._get_field(data, ["batch_id", "BatchID", "batchId"])
        if not batch_id:
            raise MissingRequiredFieldError("batch_id")

        source = self._get_field(data, ["source", "Source"])
        if not source:
            raise MissingRequiredFieldError("source")

        ts_raw = self._get_field(data, ["timestamp", "Timestamp", "dateTime", "DateTime"])
        if not ts_raw:
            raise MissingRequiredFieldError("timestamp")
        timestamp = self._parse_datetime(ts_raw)

        declared_total = self._get_field(data, ["total_records", "TotalRecords", "totalRecords", "recordCount"])
        if declared_total is None:
            raise MissingRequiredFieldError("total_records")
        try:
            total_records = int(declared_total)
        except (ValueError, TypeError) as err:
            raise InvalidValueError(f"Campo 'total_records' deve ser um inteiro válido: {declared_total!r}") from err

        # 2. Registros de trade (suporta "records", "Records", "trades", "Trades")
        raw_records = self._get_field(data, ["records", "Records", "trades", "Trades"])
        if raw_records is None:
            raise MissingRequiredFieldError("records")
        if not isinstance(raw_records, list):
            raise InvalidValueError(
                f"Campo 'records' deve ser uma lista de operações, recebido: {type(raw_records).__name__}"
            )

        # 3. Validação de integridade do lote
        if self.check_batch_integrity and len(raw_records) != total_records:
            raise BatchIntegrityError(declared_total=total_records, actual_total=len(raw_records))

        # 4. Parsing e validação de cada registro
        parsed_records: List[TradeRecord] = []
        for idx, rec_data in enumerate(raw_records, start=1):
            if not isinstance(rec_data, dict):
                raise InvalidValueError(
                    f"Registro #{idx} deve ser um objeto JSON (dict), recebido: {type(rec_data).__name__}"
                )
            record = self.parse_record_dict(rec_data, record_index=idx)
            parsed_records.append(record)

        return TradeCaptureBatch(
            batch_id=str(batch_id).strip(),
            source=str(source).strip(),
            timestamp=timestamp,
            total_records=total_records,
            records=parsed_records,
        )

    def parse_record_dict(self, record: Dict[str, Any], record_index: Optional[int] = None) -> TradeRecord:
        """Processa e valida as regras de negócio de um registro de trade individual."""
        # A. TradeID
        trade_id_raw = self._get_field(record, ["TradeID", "trade_id", "tradeId", "id"])
        if not trade_id_raw or not str(trade_id_raw).strip():
            raise MissingRequiredFieldError("TradeID", record_index)
        trade_id = str(trade_id_raw).strip()

        # B. TradeDate
        date_raw = self._get_field(record, ["TradeDate", "trade_date", "tradeDate", "date"])
        if not date_raw:
            raise MissingRequiredFieldError("TradeDate", record_index)
        trade_date = self._parse_date(date_raw, record_index)

        # C. TradeTime
        time_raw = self._get_field(record, ["TradeTime", "trade_time", "tradeTime", "time"])
        if not time_raw:
            raise MissingRequiredFieldError("TradeTime", record_index)
        trade_time = self._parse_time(time_raw, record_index)

        # D. Symbol / Ticker
        symbol_raw = self._get_field(record, ["Symbol", "Ticker", "symbol", "ticker", "instrument"])
        if not symbol_raw or not str(symbol_raw).strip():
            raise MissingRequiredFieldError("Symbol", record_index)
        symbol = str(symbol_raw).strip().upper()

        # E. ISIN
        isin_raw = self._get_field(record, ["ISIN", "isin", "securityId"])
        if not isin_raw or not str(isin_raw).strip():
            raise MissingRequiredFieldError("ISIN", record_index)
        isin = self._validate_isin(str(isin_raw).strip().upper(), record_index)

        # F. MarketType
        market_raw = self._get_field(record, ["MarketType", "market_type", "marketType", "market"])
        if not market_raw:
            raise MissingRequiredFieldError("MarketType", record_index)
        market_type = MarketType.from_str(market_raw)

        # G. Side (Buy/Sell)
        side_raw = self._get_field(record, ["Side", "side"])
        if not side_raw:
            raise MissingRequiredFieldError("Side", record_index)
        side = TradeSide.from_str(side_raw)

        # H. Quantity
        qty_raw = self._get_field(record, ["Quantity", "quantity", "qty", "volume"])
        if qty_raw is None or str(qty_raw).strip() == "":
            raise MissingRequiredFieldError("Quantity", record_index)
        quantity = self._parse_decimal_strictly_positive("Quantity", qty_raw, record_index)

        # I. Price
        price_raw = self._get_field(record, ["Price", "price", "unitPrice"])
        if price_raw is None or str(price_raw).strip() == "":
            raise MissingRequiredFieldError("Price", record_index)
        price = self._parse_decimal_strictly_positive("Price", price_raw, record_index)

        # J. Counterparty
        cpty_raw = self._get_field(record, ["Counterparty", "counterparty", "contraParticipant"])
        if not cpty_raw or not str(cpty_raw).strip():
            raise MissingRequiredFieldError("Counterparty", record_index)
        counterparty = str(cpty_raw).strip()

        # K. ExecutingBroker
        exec_broker_raw = self._get_field(record, ["ExecutingBroker", "executing_broker", "executingBroker"])
        if not exec_broker_raw or not str(exec_broker_raw).strip():
            raise MissingRequiredFieldError("ExecutingBroker", record_index)
        executing_broker = str(exec_broker_raw).strip()

        # L. ClearingBroker
        clr_broker_raw = self._get_field(record, ["ClearingBroker", "clearing_broker", "clearingBroker"])
        if not clr_broker_raw or not str(clr_broker_raw).strip():
            raise MissingRequiredFieldError("ClearingBroker", record_index)
        clearing_broker = str(clr_broker_raw).strip()

        # M. BlockTradeFlag
        block_flag_raw = self._get_field(record, ["BlockTradeFlag", "block_trade_flag", "blockTradeFlag", "isBlockTrade"])
        if block_flag_raw is None:
            raise MissingRequiredFieldError("BlockTradeFlag", record_index)
        block_trade_flag = self._parse_boolean("BlockTradeFlag", block_flag_raw, record_index)

        # N. AllocationStatus
        status_raw = self._get_field(record, ["AllocationStatus", "allocation_status", "allocationStatus", "status"])
        if not status_raw:
            raise MissingRequiredFieldError("AllocationStatus", record_index)
        allocation_status = AllocationStatus.from_str(status_raw)

        # O. Metadados opcionais
        exec_id_raw = self._get_field(record, ["ExecID", "exec_id", "execId", "executionId"])
        exec_id = str(exec_id_raw).strip() if exec_id_raw else None

        return TradeRecord(
            trade_id=trade_id,
            trade_date=trade_date,
            trade_time=trade_time,
            symbol=symbol,
            isin=isin,
            market_type=market_type,
            side=side,
            quantity=quantity,
            price=price,
            counterparty=counterparty,
            executing_broker=executing_broker,
            clearing_broker=clearing_broker,
            block_trade_flag=block_trade_flag,
            allocation_status=allocation_status,
            exec_id=exec_id,
            raw_data=record,
        )

    # -----------------------------------------------------------------------
    # Utilitários Internos de Conversão e Validação
    # -----------------------------------------------------------------------

    @staticmethod
    def _get_field(d: Dict[str, Any], candidate_keys: Sequence[str]) -> Any:
        """Recupera o primeiro valor encontrado para as chaves candidatas."""
        for k in candidate_keys:
            if k in d:
                return d[k]
        return None

    @staticmethod
    def _parse_datetime(val: Any) -> datetime:
        """Converte string ISO-8601 ou objeto datetime."""
        if isinstance(val, datetime):
            return val
        s = str(val).strip()
        # Trata notação UTC com Z
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(s)
        except Exception as err:
            raise InvalidValueError(f"Timestamp ISO-8601 inválido: {val!r}") from err

    @staticmethod
    def _parse_date(val: Any, record_index: Optional[int] = None) -> date:
        """Converte datas em formatos YYYY-MM-DD ou YYYYMMDD."""
        if isinstance(val, date) and not isinstance(val, datetime):
            return val
        if isinstance(val, datetime):
            return val.date()

        s = str(val).strip()
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue

        loc = f" no registro #{record_index}" if record_index is not None else ""
        raise InvalidValueError(
            f"Formato de data inválido para 'TradeDate': {val!r}{loc}. "
            f"Formatos aceitos: YYYY-MM-DD ou YYYYMMDD."
        )

    @staticmethod
    def _parse_time(val: Any, record_index: Optional[int] = None) -> time:
        """Converte horários em formatos HH:MM:SS, HH:MM:SS.fff ou HHMMSS."""
        if isinstance(val, time):
            return val
        s = str(val).strip()

        for fmt in ("%H:%M:%S", "%H:%M:%S.%f", "%H%M%S"):
            try:
                return datetime.strptime(s, fmt).time()
            except ValueError:
                continue

        loc = f" no registro #{record_index}" if record_index is not None else ""
        raise InvalidValueError(
            f"Formato de horário inválido para 'TradeTime': {val!r}{loc}. "
            f"Formatos aceitos: HH:MM:SS ou HH:MM:SS.fff."
        )

    @staticmethod
    def _parse_decimal_strictly_positive(field_name: str, val: Any, record_index: Optional[int] = None) -> Decimal:
        """Converte para Decimal e exige valor estritamente maior que zero (> 0)."""
        try:
            d = Decimal(str(val).strip())
        except (InvalidOperation, ValueError, TypeError) as err:
            loc = f" no registro #{record_index}" if record_index is not None else ""
            raise InvalidValueError(f"Valor numérico inválido para '{field_name}': {val!r}{loc}") from err

        if d.is_nan() or d.is_infinite():
            loc = f" no registro #{record_index}" if record_index is not None else ""
            raise InvalidValueError(f"Valor numérico especial inválido (NaN/Inf) em '{field_name}': {val!r}{loc}")

        if d <= Decimal(0):
            raise NegativeOrZeroValueError(field_name=field_name, value=val, record_index=record_index)

        return d

    @staticmethod
    def _parse_boolean(field_name: str, val: Any, record_index: Optional[int] = None) -> bool:
        """Converte booleano ou representações comuns."""
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            if val == 1:
                return True
            if val == 0:
                return False
        if isinstance(val, str):
            s = val.strip().lower()
            if s in ("true", "1", "y", "yes", "sim", "s"):
                return True
            if s in ("false", "0", "n", "no", "nao", "não"):
                return False

        loc = f" no registro #{record_index}" if record_index is not None else ""
        raise InvalidValueError(f"Valor booleano inválido para '{field_name}': {val!r}{loc}")

    @staticmethod
    def _validate_isin(isin: str, record_index: Optional[int] = None) -> str:
        """Valida que o código ISIN possui exatamente 12 caracteres alfanuméricos."""
        if len(isin) != 12 or not _ISIN_REGEX.match(isin):
            loc = f" no registro #{record_index}" if record_index is not None else ""
            raise InvalidValueError(
                f"Código ISIN inválido: {isin!r}{loc}. "
                f"O código ISIN (ISO 6166) deve possuir exatamente 12 caracteres alfanuméricos (ex: 'BRVALEACNOR0')."
            )
        return isin


# ---------------------------------------------------------------------------
# Função Facilitadora de Alto Nível
# ---------------------------------------------------------------------------

def parse_rtc_capture(source: Union[str, Path, Dict[str, Any]]) -> TradeCaptureBatch:
    """Lê e processa um lote RTC a partir de arquivo, JSON string ou dicionário."""
    parser = TradeCaptureParser()
    if isinstance(source, dict):
        return parser.parse_dict(source)
    if isinstance(source, Path):
        return parser.parse_file(source)
    if isinstance(source, str):
        stripped = source.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return parser.parse_json(stripped)
        try:
            if len(source) < 4096:
                p = Path(source)
                if p.exists() and p.is_file():
                    return parser.parse_file(p)
        except OSError:
            pass
        return parser.parse_json(source)
    raise TypeError(f"Tipo de fonte não suportado para captura RTC: {type(source).__name__}")
