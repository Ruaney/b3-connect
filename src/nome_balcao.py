"""Módulo de Registro e Conciliação de Balcão NoMe B3 (FL-014 e FL-019).

Este módulo implementa o modelo de mensageria, regras de negócio e motor
determinístico de conciliação para ativos de renda fixa privada e balcão
(antiga CETIP / atual NoMe B3), abrangendo CDB, RDB, LCI, LCA, Debêntures,
CRI e CRA.

Utiliza estritamente a biblioteca padrão do Python (sem dependências externas).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union
import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class NoMeError(Exception):
    """Exceção base para operações do NoMe Balcão B3."""
    pass


class NoMeValidationError(NoMeError):
    """Erro de integridade ou violação de regra de negócio do NoMe."""
    pass


class NoMeParsingError(NoMeError):
    """Erro de parsing estrutural em documentos JSON ou XML do NoMe."""
    pass


class NoMeReconciliationError(NoMeError):
    """Erro no processamento do motor de conciliação de balcão."""
    pass


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------

class InstrumentType(str, Enum):
    """Instrumentos de Renda Fixa e Securitização registrados no NoMe."""
    CDB = "CDB"
    RDB = "RDB"
    LCI = "LCI"
    LCA = "LCA"
    DEBENTURE = "DEBENTURE"
    CRI = "CRI"
    CRA = "CRA"

    @classmethod
    def from_str(cls, value: Any) -> InstrumentType:
        """Converte e normaliza variações textuais para InstrumentType."""
        if isinstance(value, cls):
            return value
        if not value:
            raise NoMeValidationError("Tipo de instrumento não pode ser vazio.")

        normalized = str(value).strip().upper()
        # Tratamento de acentuação e sinônimos usuais de mercado
        normalized = normalized.replace("Ê", "E").replace("É", "E")

        synonyms = {
            "CDB": cls.CDB,
            "RDB": cls.RDB,
            "LCI": cls.LCI,
            "LCA": cls.LCA,
            "DEBENTURE": cls.DEBENTURE,
            "DEB": cls.DEBENTURE,
            "DEBENTURES": cls.DEBENTURE,
            "CRI": cls.CRI,
            "CRA": cls.CRA,
        }

        if normalized in synonyms:
            return synonyms[normalized]

        valid = ", ".join(m.value for m in cls)
        raise NoMeValidationError(
            f"Tipo de instrumento inválido: {value!r}. Valores permitidos: {valid}"
        )


class IndexerType(str, Enum):
    """Indexadores financeiros aceitos pelo NoMe."""
    DI = "DI"
    PRE = "PRE"
    IPCA = "IPCA"
    IGPM = "IGPM"

    @classmethod
    def from_str(cls, value: Any) -> IndexerType:
        """Converte e normaliza strings de indexadores."""
        if isinstance(value, cls):
            return value
        if not value:
            raise NoMeValidationError("Indexador não pode ser vazio.")

        normalized = str(value).strip().upper().replace("É", "E").replace("-", "")

        synonyms = {
            "DI": cls.DI,
            "CDI": cls.DI,
            "TAXA DI": cls.DI,
            "PRE": cls.PRE,
            "PREFIXADO": cls.PRE,
            "PRE-FIXADO": cls.PRE,
            "IPCA": cls.IPCA,
            "IGPM": cls.IGPM,
            "IGP-M": cls.IGPM,
        }

        if normalized in synonyms:
            return synonyms[normalized]

        valid = ", ".join(m.value for m in cls)
        raise NoMeValidationError(
            f"Indexador inválido: {value!r}. Valores aceitos: {valid}"
        )


class TradeMatchStatus(str, Enum):
    """Status do Batimento Bilateral e Liquidação da Operação."""
    PENDING_MATCH = "PENDING_MATCH"
    MATCHED = "MATCHED"
    REJECTED = "REJECTED"
    SETTLED = "SETTLED"

    @classmethod
    def from_str(cls, value: Any) -> TradeMatchStatus:
        """Converte e normaliza strings para TradeMatchStatus."""
        if isinstance(value, cls):
            return value
        if not value:
            raise NoMeValidationError("Status de batimento não pode ser vazio.")

        normalized = str(value).strip().upper()
        for member in cls:
            if member.value == normalized:
                return member

        valid = ", ".join(m.value for m in cls)
        raise NoMeValidationError(
            f"Status de batimento inválido: {value!r}. Valores aceitos: {valid}"
        )


class SettlementType(str, Enum):
    """Modalidade de liquidação financeira e de custódia da operação."""
    GROSS_STR = "GROSS_STR"
    BILATERAL = "BILATERAL"
    NET_CLEARING = "NET_CLEARING"

    @classmethod
    def from_str(cls, value: Any) -> SettlementType:
        """Converte e normaliza modalidade de liquidação."""
        if isinstance(value, cls):
            return value
        if not value:
            return cls.GROSS_STR

        normalized = str(value).strip().upper()
        for member in cls:
            if member.value == normalized:
                return member
        return cls.GROSS_STR


class NoMeBreakType(str, Enum):
    """Tipos determinísticos de divergência na conciliação de balcão NoMe."""
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    PU_MISMATCH = "PU_MISMATCH"
    GHOST_IN_NOME = "GHOST_IN_NOME"
    MISSING_IN_NOME = "MISSING_IN_NOME"


# ---------------------------------------------------------------------------
# Funções Auxiliares de Conversão e Validação
# ---------------------------------------------------------------------------

def _to_decimal(value: Any, field_name: str) -> Decimal:
    """Converte valor para Decimal garantindo validação de erro."""
    if value is None:
        raise NoMeValidationError(f"Campo {field_name!r} não pode ser nulo.")
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise NoMeValidationError(
            f"Valor decimal inválido para {field_name!r}: {value!r}"
        )


def _to_date(value: Any, field_name: str) -> date:
    """Converte string ISO YYYY-MM-DD ou date para date."""
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value:
        raise NoMeValidationError(f"Data obrigatória ausente em {field_name!r}.")
    s = str(value).strip()
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise NoMeValidationError(
            f"Formato de data inválido para {field_name!r}: {value!r}. Esperado YYYY-MM-DD."
        )


def _clean_cnpj(cnpj: Any) -> str:
    """Remove caracteres não-numéricos e valida o tamanho do CNPJ."""
    digits = re.sub(r"\D", "", str(cnpj or ""))
    if len(digits) != 14:
        raise NoMeValidationError(f"CNPJ do emissor deve conter 14 dígitos: {cnpj!r}")
    return digits


# ---------------------------------------------------------------------------
# Modelos de Dados (Dataclasses)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PartyParticipant:
    """Identificação de participante e contas envolvidas na operação."""
    participant_id: str
    custody_account: int
    settlement_account: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.participant_id or not str(self.participant_id).strip():
            raise NoMeValidationError("Código do participante não pode ser vazio.")
        if self.custody_account <= 0:
            raise NoMeValidationError(
                f"Conta de custódia deve ser um número inteiro positivo: {self.custody_account}"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PartyParticipant:
        """Cria instância a partir de dicionário."""
        part_id = data.get("participant_id") or data.get("participant") or data.get("broker")
        conta = data.get("custody_account") or data.get("account") or data.get("conta")
        liquidacao = (
            data.get("settlement_account")
            or data.get("conta_liquidacao")
            or data.get("ispb_account")
        )

        if part_id is None or conta is None:
            raise NoMeValidationError(
                f"Participante e conta de custódia são obrigatórios: {data}"
            )

        try:
            conta_int = int(conta)
        except (ValueError, TypeError):
            raise NoMeValidationError(f"Conta de custódia inválida: {conta!r}")

        return cls(
            participant_id=str(part_id).strip(),
            custody_account=conta_int,
            settlement_account=str(liquidacao).strip() if liquidacao else None,
        )


@dataclass(frozen=True)
class FixedIncomeAsset:
    """Especificação e cadastro de um instrumento de renda fixa ou balcão NoMe."""
    asset_code: str
    instrument_type: InstrumentType
    issuer_cnpj: str
    issuer_name: str
    issue_date: date
    maturity_date: date
    indexer: IndexerType
    indexer_percentage: Decimal = Decimal("0.00")
    rate_spread: Decimal = Decimal("0.00")
    emission_unit_price: Decimal = Decimal("1000.00000000")

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Valida regras de integridade cadastral do ativo."""
        if not self.asset_code or not self.asset_code.strip():
            raise NoMeValidationError("Código do ativo (asset_code) não pode ser vazio.")
        if not self.issuer_name or not self.issuer_name.strip():
            raise NoMeValidationError("Nome do emissor não pode ser vazio.")
        _clean_cnpj(self.issuer_cnpj)

        if self.maturity_date <= self.issue_date:
            raise NoMeValidationError(
                f"Data de vencimento ({self.maturity_date}) deve ser posterior à data de emissão ({self.issue_date})."
            )
        if self.emission_unit_price <= Decimal("0"):
            raise NoMeValidationError(
                f"PU de emissão deve ser estritamente positivo: {self.emission_unit_price}"
            )
        if self.indexer_percentage < Decimal("0"):
            raise NoMeValidationError(
                f"Percentual do indexador não pode ser negativo: {self.indexer_percentage}"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FixedIncomeAsset:
        """Instancia FixedIncomeAsset a partir de dicionário."""
        code = data.get("asset_code") or data.get("code") or data.get("ticker")
        inst_type = InstrumentType.from_str(
            data.get("instrument_type") or data.get("instrument") or data.get("type")
        )
        cnpj = _clean_cnpj(data.get("issuer_cnpj") or data.get("cnpj"))
        issuer_name = str(data.get("issuer_name") or data.get("issuer") or "").strip()
        issue_d = _to_date(data.get("issue_date") or data.get("emission_date"), "issue_date")
        mat_d = _to_date(data.get("maturity_date") or data.get("due_date"), "maturity_date")
        idx = IndexerType.from_str(data.get("indexer") or data.get("benchmark"))

        idx_pct = _to_decimal(data.get("indexer_percentage", 0), "indexer_percentage")
        spread = _to_decimal(data.get("rate_spread", 0), "rate_spread")
        emission_pu = _to_decimal(
            data.get("emission_unit_price", "1000.00000000"), "emission_unit_price"
        )

        return cls(
            asset_code=str(code).strip(),
            instrument_type=inst_type,
            issuer_cnpj=cnpj,
            issuer_name=issuer_name,
            issue_date=issue_d,
            maturity_date=mat_d,
            indexer=idx,
            indexer_percentage=idx_pct,
            rate_spread=spread,
            emission_unit_price=emission_pu,
        )


@dataclass
class NoMeTradeRegistration:
    """Registro de operação primária ou secundária de renda fixa na B3 NoMe."""
    trade_id: str
    trade_date: date
    settlement_date: date
    settlement_type: SettlementType
    match_status: TradeMatchStatus
    asset: FixedIncomeAsset
    quantity: Decimal
    unit_price: Decimal
    financial_amount: Decimal
    buyer: PartyParticipant
    seller: PartyParticipant
    rejection_reasons: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Executa validações rigorosas de negócio sobre o registro da operação."""
        if not self.trade_id or not str(self.trade_id).strip():
            raise NoMeValidationError("Identificador da operação (trade_id) não pode ser vazio.")

        if self.quantity <= Decimal("0"):
            raise NoMeValidationError(f"Quantidade deve ser estritamente positiva: {self.quantity}")

        if self.unit_price <= Decimal("0"):
            raise NoMeValidationError(f"PU de negociação deve ser estritamente positivo: {self.unit_price}")

        if self.financial_amount <= Decimal("0"):
            raise NoMeValidationError(f"Valor financeiro deve ser estritamente positivo: {self.financial_amount}")

        if self.settlement_date < self.trade_date:
            raise NoMeValidationError(
                f"Data de liquidação ({self.settlement_date}) não pode ser anterior à data da operação ({self.trade_date})."
            )

        # Validação de auto-negociação na mesma conta
        if (
            self.buyer.participant_id == self.seller.participant_id
            and self.buyer.custody_account == self.seller.custody_account
        ):
            raise NoMeValidationError(
                f"Operação inválida: conta compradora e vendedora são idênticas ({self.buyer.custody_account})."
            )

        # Invariante financeiro: Qtd * PU == Valor Financeiro (tolerância de R$ 0.02)
        expected_finance = (self.quantity * self.unit_price).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        diff = abs(self.financial_amount - expected_finance)
        if diff > Decimal("0.02"):
            raise NoMeValidationError(
                f"Violação da lei de conservação financeira: informado {self.financial_amount}, "
                f"esperado {expected_finance} (Qtd {self.quantity} * PU {self.unit_price}). "
                f"Divergência: {diff} > tolerância de 0.02."
            )

    def to_dict(self) -> Dict[str, Any]:
        """Converte a operação em dicionário serializável."""
        return {
            "trade_id": self.trade_id,
            "trade_date": self.trade_date.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
            "settlement_type": self.settlement_type.value,
            "match_status": self.match_status.value,
            "asset": {
                "asset_code": self.asset.asset_code,
                "instrument_type": self.asset.instrument_type.value,
                "issuer_cnpj": self.asset.issuer_cnpj,
                "issuer_name": self.asset.issuer_name,
                "issue_date": self.asset.issue_date.isoformat(),
                "maturity_date": self.asset.maturity_date.isoformat(),
                "indexer": self.asset.indexer.value,
                "indexer_percentage": float(self.asset.indexer_percentage),
                "rate_spread": float(self.asset.rate_spread),
                "emission_unit_price": float(self.asset.emission_unit_price),
            },
            "quantity": float(self.quantity),
            "unit_price": float(self.unit_price),
            "financial_amount": float(self.financial_amount),
            "buyer": asdict(self.buyer),
            "seller": asdict(self.seller),
            "rejection_reasons": list(self.rejection_reasons),
        }


@dataclass(frozen=True)
class NoMeStatementPosition:
    """Posição individual de balcão reportada no extrato diário NoMe."""
    custody_account: int
    asset_code: str
    instrument_type: InstrumentType
    issuer_cnpj: str
    issuer_name: str
    available_quantity: Decimal
    blocked_quantity: Decimal
    total_quantity: Decimal
    unit_price: Decimal
    financial_amount: Decimal

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Valida integridade da posição de custódia do extrato."""
        if self.custody_account <= 0:
            raise NoMeValidationError(f"Conta de custódia inválida: {self.custody_account}")
        if not self.asset_code or not self.asset_code.strip():
            raise NoMeValidationError("Código do ativo não pode ser vazio.")
        if self.available_quantity < Decimal("0"):
            raise NoMeValidationError(f"Quantidade disponível não pode ser negativa: {self.available_quantity}")
        if self.blocked_quantity < Decimal("0"):
            raise NoMeValidationError(f"Quantidade bloqueada não pode ser negativa: {self.blocked_quantity}")
        if self.total_quantity < Decimal("0"):
            raise NoMeValidationError(f"Quantidade total não pode ser negativa: {self.total_quantity}")

        # Invariante de soma de quantidades: Total == Disponível + Bloqueada
        if self.total_quantity != (self.available_quantity + self.blocked_quantity):
            raise NoMeValidationError(
                f"Inconsistência de saldos: Total ({self.total_quantity}) != "
                f"Disponível ({self.available_quantity}) + Bloqueada ({self.blocked_quantity})."
            )

        if self.unit_price < Decimal("0"):
            raise NoMeValidationError(f"PU não pode ser negativo: {self.unit_price}")

        # Validação financeira
        expected_finance = (self.total_quantity * self.unit_price).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        diff = abs(self.financial_amount - expected_finance)
        if diff > Decimal("0.02"):
            raise NoMeValidationError(
                f"Violação financeira na posição {self.asset_code}: informado {self.financial_amount}, "
                f"calculado {expected_finance} (dif {diff})."
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NoMeStatementPosition:
        """Instancia NoMeStatementPosition a partir de dicionário."""
        conta = int(data.get("custody_account") or data.get("account") or 0)
        code = str(data.get("asset_code") or data.get("ticker") or "").strip()
        inst_type = InstrumentType.from_str(data.get("instrument_type") or data.get("instrument"))
        cnpj = _clean_cnpj(data.get("issuer_cnpj"))
        issuer_name = str(data.get("issuer_name") or "").strip()

        avail = _to_decimal(data.get("available_quantity", 0), "available_quantity")
        blocked = _to_decimal(data.get("blocked_quantity", 0), "blocked_quantity")
        total = _to_decimal(data.get("total_quantity", 0), "total_quantity")
        pu = _to_decimal(data.get("unit_price", 0), "unit_price")
        finance = _to_decimal(data.get("financial_amount", 0), "financial_amount")

        return cls(
            custody_account=conta,
            asset_code=code,
            instrument_type=inst_type,
            issuer_cnpj=cnpj,
            issuer_name=issuer_name,
            available_quantity=avail,
            blocked_quantity=blocked,
            total_quantity=total,
            unit_price=pu,
            financial_amount=finance,
        )


@dataclass(frozen=True)
class NoMeStatementFile:
    """Extrato consolidado diário de posições de balcão NoMe."""
    statement_id: str
    participant_id: str
    reference_date: date
    generation_timestamp: datetime
    total_records: int
    total_financial_amount: Decimal
    positions: List[NoMeStatementPosition]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Valida integridade do extrato consolidado e seus totalizadores."""
        if not self.statement_id or not self.statement_id.strip():
            raise NoMeValidationError("Identificador do extrato (statement_id) não pode ser vazio.")
        if not self.participant_id or not self.participant_id.strip():
            raise NoMeValidationError("Código do participante não pode ser vazio.")
        if len(self.positions) != self.total_records:
            raise NoMeValidationError(
                f"Total de registros diverge do declarado: "
                f"esperado {self.total_records}, presentes {len(self.positions)}."
            )

        sum_finance = sum(
            (pos.financial_amount for pos in self.positions), Decimal("0.00")
        )
        diff = abs(self.total_financial_amount - sum_finance)
        if diff > Decimal("0.05"):
            raise NoMeValidationError(
                f"Valor financeiro total do extrato diverge: declarado {self.total_financial_amount}, "
                f"soma das posições {sum_finance} (diferença: {diff})."
            )


@dataclass(frozen=True)
class InternalBalcaoPosition:
    """Representação padronizada de uma posição no livro interno de custódia da instituição."""
    custody_account: int
    asset_code: str
    total_quantity: Decimal
    unit_price: Decimal
    financial_amount: Optional[Decimal] = None
    instrument_type: Optional[InstrumentType] = None
    issuer_name: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InternalBalcaoPosition:
        """Instancia posição do livro interno a partir de dicionário flexível."""
        conta = data.get("custody_account") or data.get("account") or data.get("conta")
        code = data.get("asset_code") or data.get("ticker") or data.get("codigo")
        qtd = (
            data.get("total_quantity")
            if "total_quantity" in data
            else (
                data.get("quantity")
                if "quantity" in data
                else (data.get("qtd") if "qtd" in data else data.get("qty"))
            )
        )
        pu = data.get("unit_price") or data.get("pu") or data.get("price")

        if conta is None or code is None or qtd is None or pu is None:
            raise NoMeValidationError(
                f"Campos conta, código, quantidade e PU são obrigatórios no livro interno: {data}"
            )

        inst = None
        if data.get("instrument_type"):
            inst = InstrumentType.from_str(data["instrument_type"])

        finance = None
        if data.get("financial_amount") is not None:
            finance = _to_decimal(data["financial_amount"], "financial_amount")

        return cls(
            custody_account=int(conta),
            asset_code=str(code).strip(),
            total_quantity=_to_decimal(qtd, "total_quantity"),
            unit_price=_to_decimal(pu, "unit_price"),
            financial_amount=finance,
            instrument_type=inst,
            issuer_name=str(data["issuer_name"]).strip() if data.get("issuer_name") else None,
        )


@dataclass(frozen=True)
class NoMeMatchedPosition:
    """Posição de balcão perfeitamente conciliada entre livro interno e NoMe."""
    custody_account: int
    asset_code: str
    quantity: Decimal
    unit_price: Decimal
    financial_amount: Decimal
    instrument_type: InstrumentType


@dataclass(frozen=True)
class NoMeReconciliationBreak:
    """Representação de divergência identificada na conciliação de balcão."""
    break_type: NoMeBreakType
    custody_account: int
    asset_code: str
    internal_quantity: Optional[Decimal] = None
    nome_quantity: Optional[Decimal] = None
    quantity_difference: Optional[Decimal] = None
    internal_unit_price: Optional[Decimal] = None
    nome_unit_price: Optional[Decimal] = None
    price_difference: Optional[Decimal] = None
    internal_financial_amount: Optional[Decimal] = None
    nome_financial_amount: Optional[Decimal] = None
    details: str = ""


@dataclass(frozen=True)
class NoMeReconciliationReport:
    """Relatório final de conciliação de custódia NoMe."""
    reference_date: date
    participant_id: str
    matched_positions: List[NoMeMatchedPosition]
    breaks: List[NoMeReconciliationBreak]
    total_matched: int
    total_breaks: int
    is_clean: bool
    summary: Dict[str, Any]


# ---------------------------------------------------------------------------
# Parsers Determinísticos (JSON e XML)
# ---------------------------------------------------------------------------

def parse_nome_trade_json(
    source: Union[str, bytes, Path, Dict[str, Any]]
) -> NoMeTradeRegistration:
    """Realiza o parsing de registro de operação NoMe a partir de JSON ou dict.

    Args:
        source: Caminho para arquivo JSON, string/bytes JSON, ou dicionário.

    Returns:
        Instância tipada de NoMeTradeRegistration.
    """
    if isinstance(source, Path):
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)
    elif isinstance(source, (str, bytes)):
        s = source.decode("utf-8") if isinstance(source, bytes) else source
        # Se for string apontando para arquivo existente
        if len(s) < 1024 and ("\n" not in s) and Path(s).is_file():
            with open(Path(s), "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            try:
                data = json.loads(s)
            except json.JSONDecodeError as err:
                raise NoMeParsingError(f"Falha ao decodificar JSON do NoMe: {err}")
    elif isinstance(source, dict):
        data = source
    else:
        raise NoMeParsingError(f"Tipo de fonte não suportado para JSON NoMe: {type(source)}")

    try:
        asset_data = data["asset"]
        asset = FixedIncomeAsset.from_dict(asset_data)
        buyer = PartyParticipant.from_dict(data["buyer"])
        seller = PartyParticipant.from_dict(data["seller"])

        trade_d = _to_date(data["trade_date"], "trade_date")
        settle_d = _to_date(data["settlement_date"], "settlement_date")
        settle_type = SettlementType.from_str(data.get("settlement_type"))
        status = TradeMatchStatus.from_str(data.get("match_status", "PENDING_MATCH"))
        qtd = _to_decimal(data["quantity"], "quantity")
        pu = _to_decimal(data["unit_price"], "unit_price")
        fin = _to_decimal(data["financial_amount"], "financial_amount")

        return NoMeTradeRegistration(
            trade_id=str(data["trade_id"]).strip(),
            trade_date=trade_d,
            settlement_date=settle_d,
            settlement_type=settle_type,
            match_status=status,
            asset=asset,
            quantity=qtd,
            unit_price=pu,
            financial_amount=fin,
            buyer=buyer,
            seller=seller,
            rejection_reasons=list(data.get("rejection_reasons", [])),
        )
    except KeyError as err:
        raise NoMeParsingError(f"Campo obrigatório ausente no registro NoMe: {err}")
    except Exception as err:
        if isinstance(err, (NoMeValidationError, NoMeParsingError)):
            raise
        raise NoMeParsingError(f"Erro ao instanciar NoMeTradeRegistration: {err}")


def parse_nome_trade_xml(
    source: Union[str, bytes, Path]
) -> NoMeTradeRegistration:
    """Realiza o parsing de registro de operação NoMe a partir de documento XML.

    Args:
        source: Caminho para arquivo XML, ou string/bytes contendo XML.

    Returns:
        Instância tipada de NoMeTradeRegistration.
    """
    if isinstance(source, Path):
        tree = ET.parse(source)
        root = tree.getroot()
    elif isinstance(source, (str, bytes)):
        s = source.decode("utf-8") if isinstance(source, bytes) else source
        if len(s) < 1024 and ("\n" not in s) and Path(s).is_file():
            tree = ET.parse(Path(s))
            root = tree.getroot()
        else:
            try:
                root = ET.fromstring(s)
            except ET.ParseError as err:
                raise NoMeParsingError(f"Falha de sintaxe XML no registro NoMe: {err}")
    else:
        raise NoMeParsingError(f"Tipo de fonte XML não suportado: {type(source)}")

    def _find_text(element: ET.Element, tag: str, required: bool = True) -> str:
        # Busca insensível a maiúsculas/minúsculas para tags diretas
        for child in element:
            if child.tag.lower() == tag.lower():
                text = (child.text or "").strip()
                if required and not text:
                    raise NoMeParsingError(f"Tag XML <{tag}> não pode estar vazia.")
                return text
        if required:
            raise NoMeParsingError(f"Tag obrigatória <{tag}> não encontrada no XML.")
        return ""

    def _find_elem(element: ET.Element, tag: str) -> ET.Element:
        for child in element:
            if child.tag.lower() == tag.lower():
                return child
        raise NoMeParsingError(f"Elemento obrigatório <{tag}> não encontrado no XML.")

    try:
        trade_id = _find_text(root, "TradeId")
        trade_date = _to_date(_find_text(root, "TradeDate"), "TradeDate")
        settlement_date = _to_date(_find_text(root, "SettlementDate"), "SettlementDate")
        settlement_type = SettlementType.from_str(_find_text(root, "SettlementType", required=False))
        match_status = TradeMatchStatus.from_str(_find_text(root, "MatchStatus"))

        asset_elem = _find_elem(root, "Asset")
        asset = FixedIncomeAsset(
            asset_code=_find_text(asset_elem, "AssetCode"),
            instrument_type=InstrumentType.from_str(_find_text(asset_elem, "InstrumentType")),
            issuer_cnpj=_clean_cnpj(_find_text(asset_elem, "IssuerCnpj")),
            issuer_name=_find_text(asset_elem, "IssuerName"),
            issue_date=_to_date(_find_text(asset_elem, "IssueDate"), "IssueDate"),
            maturity_date=_to_date(_find_text(asset_elem, "MaturityDate"), "MaturityDate"),
            indexer=IndexerType.from_str(_find_text(asset_elem, "Indexer")),
            indexer_percentage=_to_decimal(_find_text(asset_elem, "IndexerPercentage", False) or "0", "IndexerPercentage"),
            rate_spread=_to_decimal(_find_text(asset_elem, "RateSpread", False) or "0", "RateSpread"),
            emission_unit_price=_to_decimal(_find_text(asset_elem, "EmissionUnitPrice", False) or "1000.00", "EmissionUnitPrice"),
        )

        quantity = _to_decimal(_find_text(root, "Quantity"), "Quantity")
        unit_price = _to_decimal(_find_text(root, "UnitPrice"), "UnitPrice")
        financial_amount = _to_decimal(_find_text(root, "FinancialAmount"), "FinancialAmount")

        buyer_elem = _find_elem(root, "Buyer")
        buyer = PartyParticipant(
            participant_id=_find_text(buyer_elem, "ParticipantId"),
            custody_account=int(_find_text(buyer_elem, "CustodyAccount")),
            settlement_account=_find_text(buyer_elem, "SettlementAccount", required=False) or None,
        )

        seller_elem = _find_elem(root, "Seller")
        seller = PartyParticipant(
            participant_id=_find_text(seller_elem, "ParticipantId"),
            custody_account=int(_find_text(seller_elem, "CustodyAccount")),
            settlement_account=_find_text(seller_elem, "SettlementAccount", required=False) or None,
        )

        return NoMeTradeRegistration(
            trade_id=trade_id,
            trade_date=trade_date,
            settlement_date=settlement_date,
            settlement_type=settlement_type,
            match_status=match_status,
            asset=asset,
            quantity=quantity,
            unit_price=unit_price,
            financial_amount=financial_amount,
            buyer=buyer,
            seller=seller,
        )
    except Exception as err:
        if isinstance(err, (NoMeValidationError, NoMeParsingError)):
            raise
        raise NoMeParsingError(f"Erro ao processar registro XML do NoMe: {err}")


def parse_nome_statement_json(
    source: Union[str, bytes, Path, Dict[str, Any]]
) -> NoMeStatementFile:
    """Realiza o parsing do arquivo de extrato diário de balcão NoMe (FL-019).

    Args:
        source: Caminho para arquivo JSON, string/bytes, ou dict carregado.

    Returns:
        Instância validada de NoMeStatementFile.
    """
    if isinstance(source, Path):
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)
    elif isinstance(source, (str, bytes)):
        s = source.decode("utf-8") if isinstance(source, bytes) else source
        if len(s) < 1024 and ("\n" not in s) and Path(s).is_file():
            with open(Path(s), "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            try:
                data = json.loads(s)
            except json.JSONDecodeError as err:
                raise NoMeParsingError(f"Falha ao decodificar JSON do extrato NoMe: {err}")
    elif isinstance(source, dict):
        data = source
    else:
        raise NoMeParsingError(f"Tipo de fonte inválido para extrato NoMe: {type(source)}")

    try:
        statement_id = str(data["statement_id"]).strip()
        part_id = str(data["participant_id"]).strip()
        ref_d = _to_date(data["reference_date"], "reference_date")

        gen_ts_raw = data.get("generation_timestamp")
        if isinstance(gen_ts_raw, datetime):
            gen_ts = gen_ts_raw
        elif gen_ts_raw:
            gen_ts = datetime.fromisoformat(str(gen_ts_raw).replace("Z", "+00:00"))
        else:
            gen_ts = datetime.utcnow()

        total_rec = int(data.get("total_records", len(data.get("positions", []))))
        total_fin = _to_decimal(data["total_financial_amount"], "total_financial_amount")

        raw_positions = data.get("positions", [])
        positions = [NoMeStatementPosition.from_dict(p) for p in raw_positions]

        return NoMeStatementFile(
            statement_id=statement_id,
            participant_id=part_id,
            reference_date=ref_d,
            generation_timestamp=gen_ts,
            total_records=total_rec,
            total_financial_amount=total_fin,
            positions=positions,
        )
    except KeyError as err:
        raise NoMeParsingError(f"Campo mandatório ausente no extrato NoMe: {err}")
    except Exception as err:
        if isinstance(err, (NoMeValidationError, NoMeParsingError)):
            raise
        raise NoMeParsingError(f"Erro ao processar extrato NoMe: {err}")


# ---------------------------------------------------------------------------
# Batimento Bilateral de Ordens (Matching Engine)
# ---------------------------------------------------------------------------

def match_bilateral_trades(
    buy_trade: NoMeTradeRegistration,
    sell_trade: NoMeTradeRegistration,
) -> Tuple[bool, List[str]]:
    """Compara duas ordens submetidas bilateralmente e valida o casamento de balcão.

    Verifica a correspondência estrita dos seguintes parâmetros:
    - Ativo (asset_code)
    - Quantidade de títulos
    - Preço Unitário (PU)
    - Data da Negociação
    - Data de Liquidação
    - Participante Comprador vs. Participante Vendedor declarados reciprocamente

    Args:
        buy_trade: Intenção/registro submetido pela ponta compradora.
        sell_trade: Intenção/registro submetido pela ponta vendedora.

    Returns:
        Tupla (is_matched, rejection_reasons).
    """
    reasons: List[str] = []

    # 1. Ativo
    if buy_trade.asset.asset_code != sell_trade.asset.asset_code:
        reasons.append(
            f"Divergência de Ativo: comprador informou {buy_trade.asset.asset_code!r}, "
            f"vendedor informou {sell_trade.asset.asset_code!r}."
        )

    # 2. Quantidade
    if buy_trade.quantity != sell_trade.quantity:
        reasons.append(
            f"Divergência de Quantidade: comprador informou {buy_trade.quantity}, "
            f"vendedor informou {sell_trade.quantity}."
        )

    # 3. Preço Unitário
    if buy_trade.unit_price != sell_trade.unit_price:
        reasons.append(
            f"Divergência de Preço Unitário: comprador informou {buy_trade.unit_price}, "
            f"vendedor informou {sell_trade.unit_price}."
        )

    # 4. Data de Negociação
    if buy_trade.trade_date != sell_trade.trade_date:
        reasons.append(
            f"Divergência de Data de Operação: comprador informou {buy_trade.trade_date}, "
            f"vendedor informou {sell_trade.trade_date}."
        )

    # 5. Data de Liquidação
    if buy_trade.settlement_date != sell_trade.settlement_date:
        reasons.append(
            f"Divergência de Data de Liquidação: comprador informou {buy_trade.settlement_date}, "
            f"vendedor informou {sell_trade.settlement_date}."
        )

    # 6. Reciprocidade de Participantes
    if buy_trade.buyer.participant_id != sell_trade.buyer.participant_id:
        reasons.append(
            f"Divergência no Comprador: ordem de compra apontou {buy_trade.buyer.participant_id!r}, "
            f"ordem de venda apontou {sell_trade.buyer.participant_id!r}."
        )

    if buy_trade.seller.participant_id != sell_trade.seller.participant_id:
        reasons.append(
            f"Divergência no Vendedor: ordem de compra apontou {buy_trade.seller.participant_id!r}, "
            f"ordem de venda apontou {sell_trade.seller.participant_id!r}."
        )

    if reasons:
        buy_trade.match_status = TradeMatchStatus.REJECTED
        buy_trade.rejection_reasons = list(reasons)
        sell_trade.match_status = TradeMatchStatus.REJECTED
        sell_trade.rejection_reasons = list(reasons)
        return False, reasons

    buy_trade.match_status = TradeMatchStatus.MATCHED
    buy_trade.rejection_reasons = []
    sell_trade.match_status = TradeMatchStatus.MATCHED
    sell_trade.rejection_reasons = []
    return True, []


# ---------------------------------------------------------------------------
# Motor de Conciliação de Balcão (NoMeReconciler)
# ---------------------------------------------------------------------------

class NoMeReconciler:
    """Motor determinístico de conciliação de balcão NoMe B3 (FL-019).

    Cruza o livro interno de custódia da instituição participante com o
    extrato oficial de fechamento diário do NoMe B3, identificando matches
    perfeitos e classificando todas as divergências operacionais:
    - QUANTITY_MISMATCH
    - PU_MISMATCH
    - GHOST_IN_NOME
    - MISSING_IN_NOME
    """

    def __init__(self, pu_tolerance: Decimal = Decimal("0.0001")) -> None:
        """Inicializa o reconciliador.

        Args:
            pu_tolerance: Tolerância permitida para divergência de PU (default: 0.0001).
        """
        self.pu_tolerance = pu_tolerance

    def reconcile(
        self,
        internal_positions: Iterable[Union[InternalBalcaoPosition, Dict[str, Any]]],
        statement: Union[NoMeStatementFile, Dict[str, Any], str, Path],
    ) -> NoMeReconciliationReport:
        """Executa a conciliação determinística entre o livro interno e o extrato NoMe.

        Args:
            internal_positions: Coleção de posições internas da instituição.
            statement: Extrato oficial NoMe (objeto, dicionário, string ou caminho).

        Returns:
            NoMeReconciliationReport com posições batidas e divergências categorizadas.
        """
        # Normalização do extrato NoMe
        if isinstance(statement, NoMeStatementFile):
            stmt = statement
        else:
            stmt = parse_nome_statement_json(statement)

        # Mapeamento do extrato NoMe: (custody_account, asset_code) -> NoMeStatementPosition
        nome_map: Dict[Tuple[int, str], NoMeStatementPosition] = {}
        for pos in stmt.positions:
            key = (pos.custody_account, pos.asset_code.upper())
            nome_map[key] = pos

        # Normalização do livro interno: (custody_account, asset_code) -> InternalBalcaoPosition
        internal_map: Dict[Tuple[int, str], InternalBalcaoPosition] = {}
        for item in internal_positions:
            if isinstance(item, InternalBalcaoPosition):
                internal_pos = item
            else:
                internal_pos = InternalBalcaoPosition.from_dict(item)

            key = (internal_pos.custody_account, internal_pos.asset_code.upper())
            # Se já existir posição interna na mesma chave, acumula quantidade
            if key in internal_map:
                existing = internal_map[key]
                new_total = existing.total_quantity + internal_pos.total_quantity
                # Média ponderada de PU se quantidades forem positivas
                if new_total > Decimal("0"):
                    weighted_pu = (
                        (existing.total_quantity * existing.unit_price)
                        + (internal_pos.total_quantity * internal_pos.unit_price)
                    ) / new_total
                else:
                    weighted_pu = internal_pos.unit_price

                internal_map[key] = InternalBalcaoPosition(
                    custody_account=internal_pos.custody_account,
                    asset_code=internal_pos.asset_code,
                    total_quantity=new_total,
                    unit_price=weighted_pu.quantize(Decimal("0.00000001")),
                    financial_amount=(
                        existing.financial_amount + (internal_pos.financial_amount or Decimal("0"))
                        if existing.financial_amount is not None
                        else None
                    ),
                    instrument_type=internal_pos.instrument_type or existing.instrument_type,
                    issuer_name=internal_pos.issuer_name or existing.issuer_name,
                )
            else:
                internal_map[key] = internal_pos

        all_keys = set(nome_map.keys()) | set(internal_map.keys())
        matched_positions: List[NoMeMatchedPosition] = []
        breaks: List[NoMeReconciliationBreak] = []

        # Ordenação determinística por conta e código de ativo
        for key in sorted(all_keys, key=lambda k: (k[0], k[1])):
            conta, code = key

            in_nome = key in nome_map
            in_internal = key in internal_map

            # Caso 1: Ativo no NoMe mas não no livro interno (GHOST_IN_NOME)
            if in_nome and not in_internal:
                nome_pos = nome_map[key]
                breaks.append(
                    NoMeReconciliationBreak(
                        break_type=NoMeBreakType.GHOST_IN_NOME,
                        custody_account=conta,
                        asset_code=code,
                        internal_quantity=None,
                        nome_quantity=nome_pos.total_quantity,
                        quantity_difference=nome_pos.total_quantity,
                        internal_unit_price=None,
                        nome_unit_price=nome_pos.unit_price,
                        price_difference=None,
                        internal_financial_amount=None,
                        nome_financial_amount=nome_pos.financial_amount,
                        details=(
                            f"Ativo {code} na conta {conta} presente no extrato NoMe "
                            f"(Qtd: {nome_pos.total_quantity}, PU: {nome_pos.unit_price}), "
                            f"mas inexistente no livro interno de custódia."
                        ),
                    )
                )
                continue

            # Caso 2: Ativo no livro interno mas ausente no NoMe (MISSING_IN_NOME)
            if in_internal and not in_nome:
                int_pos = internal_map[key]
                int_fin = int_pos.financial_amount or (int_pos.total_quantity * int_pos.unit_price)
                breaks.append(
                    NoMeReconciliationBreak(
                        break_type=NoMeBreakType.MISSING_IN_NOME,
                        custody_account=conta,
                        asset_code=code,
                        internal_quantity=int_pos.total_quantity,
                        nome_quantity=None,
                        quantity_difference=-int_pos.total_quantity,
                        internal_unit_price=int_pos.unit_price,
                        nome_unit_price=None,
                        price_difference=None,
                        internal_financial_amount=int_fin,
                        nome_financial_amount=None,
                        details=(
                            f"Ativo {code} na conta {conta} registrado no livro interno "
                            f"(Qtd: {int_pos.total_quantity}, PU: {int_pos.unit_price}), "
                            f"mas ausente no extrato NoMe da B3."
                        ),
                    )
                )
                continue

            # Caso 3: Presente em ambos -> Verificação de Quantidade e PU
            nome_pos = nome_map[key]
            int_pos = internal_map[key]

            qty_diff = nome_pos.total_quantity - int_pos.total_quantity
            pu_diff = abs(nome_pos.unit_price - int_pos.unit_price)

            has_qty_break = qty_diff != Decimal("0")
            has_pu_break = pu_diff > self.pu_tolerance

            if has_qty_break:
                int_fin = int_pos.financial_amount or (int_pos.total_quantity * int_pos.unit_price)
                breaks.append(
                    NoMeReconciliationBreak(
                        break_type=NoMeBreakType.QUANTITY_MISMATCH,
                        custody_account=conta,
                        asset_code=code,
                        internal_quantity=int_pos.total_quantity,
                        nome_quantity=nome_pos.total_quantity,
                        quantity_difference=qty_diff,
                        internal_unit_price=int_pos.unit_price,
                        nome_unit_price=nome_pos.unit_price,
                        price_difference=pu_diff,
                        internal_financial_amount=int_fin,
                        nome_financial_amount=nome_pos.financial_amount,
                        details=(
                            f"Divergência de saldo para {code} na conta {conta}: "
                            f"livro interno={int_pos.total_quantity}, NoMe={nome_pos.total_quantity} "
                            f"(diferença={qty_diff})."
                        ),
                    )
                )

            if has_pu_break:
                int_fin = int_pos.financial_amount or (int_pos.total_quantity * int_pos.unit_price)
                breaks.append(
                    NoMeReconciliationBreak(
                        break_type=NoMeBreakType.PU_MISMATCH,
                        custody_account=conta,
                        asset_code=code,
                        internal_quantity=int_pos.total_quantity,
                        nome_quantity=nome_pos.total_quantity,
                        quantity_difference=qty_diff,
                        internal_unit_price=int_pos.unit_price,
                        nome_unit_price=nome_pos.unit_price,
                        price_difference=pu_diff,
                        internal_financial_amount=int_fin,
                        nome_financial_amount=nome_pos.financial_amount,
                        details=(
                            f"Divergência de PU para {code} na conta {conta}: "
                            f"livro interno={int_pos.unit_price}, NoMe={nome_pos.unit_price} "
                            f"(diferença={pu_diff} > tolerância={self.pu_tolerance})."
                        ),
                    )
                )

            # Se não houver divergência nem de quantidade nem de PU, é um Match Perfeito
            if not has_qty_break and not has_pu_break:
                matched_positions.append(
                    NoMeMatchedPosition(
                        custody_account=conta,
                        asset_code=code,
                        quantity=nome_pos.total_quantity,
                        unit_price=nome_pos.unit_price,
                        financial_amount=nome_pos.financial_amount,
                        instrument_type=nome_pos.instrument_type,
                    )
                )

        summary = {
            "total_internal_keys": len(internal_map),
            "total_nome_keys": len(nome_map),
            "matched_count": len(matched_positions),
            "breaks_count": len(breaks),
            "quantity_mismatch_count": sum(
                1 for b in breaks if b.break_type == NoMeBreakType.QUANTITY_MISMATCH
            ),
            "pu_mismatch_count": sum(
                1 for b in breaks if b.break_type == NoMeBreakType.PU_MISMATCH
            ),
            "ghost_in_nome_count": sum(
                1 for b in breaks if b.break_type == NoMeBreakType.GHOST_IN_NOME
            ),
            "missing_in_nome_count": sum(
                1 for b in breaks if b.break_type == NoMeBreakType.MISSING_IN_NOME
            ),
        }

        return NoMeReconciliationReport(
            reference_date=stmt.reference_date,
            participant_id=stmt.participant_id,
            matched_positions=matched_positions,
            breaks=breaks,
            total_matched=len(matched_positions),
            total_breaks=len(breaks),
            is_clean=(len(breaks) == 0),
            summary=summary,
        )


def reconcile_nome_positions(
    internal_positions: Iterable[Union[InternalBalcaoPosition, Dict[str, Any]]],
    statement: Union[NoMeStatementFile, Dict[str, Any], str, Path],
    pu_tolerance: Decimal = Decimal("0.0001"),
) -> NoMeReconciliationReport:
    """Função de conveniência para execução direta da conciliação NoMe."""
    reconciler = NoMeReconciler(pu_tolerance=pu_tolerance)
    return reconciler.reconcile(internal_positions=internal_positions, statement=statement)
