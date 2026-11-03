"""Módulo de Registro e Gerenciamento de Derivativos de Balcão e Opções Flexíveis (FL-016 e FL-017) — B3 NoMe.

Este módulo implementa modelos de dados tipados, validações estritas de regras de
negócio e cálculos determinísticos para contratos de Swap de Balcão, NDFs e
Opções Flexíveis registrados no Novo Mercado de Balcão (NoMe) da B3, utilizando
exclusivamente a biblioteca padrão Python.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------

class RegistrationStatus(str, Enum):
    """Estado do ciclo de vida do contrato no registro NoMe."""

    SUBMITTED = "SUBMITTED"
    REGISTERED = "REGISTERED"
    REJECTED = "REJECTED"
    MATURED = "MATURED"
    CANCELLED = "CANCELLED"

    @classmethod
    def from_str(cls, value: Any) -> RegistrationStatus:
        """Converte e normaliza strings para RegistrationStatus."""
        if isinstance(value, RegistrationStatus):
            return value
        if not value:
            return cls.SUBMITTED
        s = str(value).strip().upper()
        mapping = {
            "SUBMITTED": cls.SUBMITTED,
            "SUBMETIDO": cls.SUBMITTED,
            "PENDING": cls.SUBMITTED,
            "REGISTERED": cls.REGISTERED,
            "REGISTRADO": cls.REGISTERED,
            "ACTIVE": cls.REGISTERED,
            "REJECTED": cls.REJECTED,
            "REJEITADO": cls.REJECTED,
            "MATURED": cls.MATURED,
            "VENCIDO": cls.MATURED,
            "LIQUIDADO": cls.MATURED,
            "CANCELLED": cls.CANCELLED,
            "CANCELADO": cls.CANCELLED,
        }
        if s in mapping:
            return mapping[s]
        raise ValueError(f"Status de registro inválido: {value!r}")


class LegType(str, Enum):
    """Sentido da ponta do swap."""

    ACTIVE = "ACTIVE"
    PASSIVE = "PASSIVE"

    @classmethod
    def from_str(cls, value: Any) -> LegType:
        """Converte e normaliza strings para LegType."""
        if isinstance(value, LegType):
            return value
        if not value:
            raise ValueError("Tipo de ponta (LegType) não pode ser nulo ou vazio.")
        s = str(value).strip().upper()
        mapping = {
            "ACTIVE": cls.ACTIVE,
            "ATIVA": cls.ACTIVE,
            "RECEIVE": cls.ACTIVE,
            "RECEBER": cls.ACTIVE,
            "LONG": cls.ACTIVE,
            "PASSIVE": cls.PASSIVE,
            "PASSIVA": cls.PASSIVE,
            "PAY": cls.PASSIVE,
            "PAGAR": cls.PASSIVE,
            "SHORT": cls.PASSIVE,
        }
        if s in mapping:
            return mapping[s]
        raise ValueError(f"Tipo de ponta inválido: {value!r}. Aceitos: ACTIVE, PASSIVE.")


class IndexerType(str, Enum):
    """Indexador de referência financeira na B3 NoMe."""

    CDI = "CDI"
    PRE = "PRE"
    DOLAR = "DOLAR"
    IPCA = "IPCA"
    IGPM = "IGPM"
    SELIC = "SELIC"

    @classmethod
    def from_str(cls, value: Any) -> IndexerType:
        """Converte e normaliza strings para IndexerType."""
        if isinstance(value, IndexerType):
            return value
        if not value:
            raise ValueError("Indexador não pode ser nulo ou vazio.")
        s = str(value).strip().upper()
        mapping = {
            "CDI": cls.CDI,
            "DI": cls.CDI,
            "TAXA_DI": cls.CDI,
            "PRE": cls.PRE,
            "PRÉ": cls.PRE,
            "PREFIXADO": cls.PRE,
            "PRÉ-FIXADO": cls.PRE,
            "FIXED": cls.PRE,
            "DOLAR": cls.DOLAR,
            "DÓLAR": cls.DOLAR,
            "USD": cls.DOLAR,
            "PTAX": cls.DOLAR,
            "IPCA": cls.IPCA,
            "IGPM": cls.IGPM,
            "IGP-M": cls.IGPM,
            "SELIC": cls.SELIC,
        }
        if s in mapping:
            return mapping[s]
        raise ValueError(
            f"Indexador não reconhecido ou inválido: {value!r}. "
            f"Indexadores aceitos: CDI, PRE, DOLAR, IPCA, IGPM, SELIC."
        )


class DayCountConvention(str, Enum):
    """Convenção de contagem de dias / base de cálculo."""

    DU_252 = "DU/252"
    DC_360 = "DC/360"
    DC_365 = "DC/365"

    @classmethod
    def from_str(cls, value: Any) -> DayCountConvention:
        """Converte e normaliza strings para DayCountConvention."""
        if isinstance(value, DayCountConvention):
            return value
        if not value:
            return cls.DU_252
        s = str(value).strip().upper()
        mapping = {
            "DU/252": cls.DU_252,
            "DU_252": cls.DU_252,
            "252": cls.DU_252,
            "BUS/252": cls.DU_252,
            "DC/360": cls.DC_360,
            "DC_360": cls.DC_360,
            "360": cls.DC_360,
            "ACT/360": cls.DC_360,
            "DC/365": cls.DC_365,
            "DC_365": cls.DC_365,
            "365": cls.DC_365,
            "ACT/365": cls.DC_365,
        }
        if s in mapping:
            return mapping[s]
        raise ValueError(
            f"Convenção de contagem de dias inválida: {value!r}. Aceitas: DU/252, DC/360, DC/365."
        )


class GuaranteeType(str, Enum):
    """Modalidade de garantia e câmara central."""

    WITH_CCP_GUARANTEE = "WITH_CCP_GUARANTEE"
    WITHOUT_GUARANTEE = "WITHOUT_GUARANTEE"

    @classmethod
    def from_str(cls, value: Any) -> GuaranteeType:
        """Converte e normaliza strings para GuaranteeType."""
        if isinstance(value, GuaranteeType):
            return value
        if not value:
            return cls.WITHOUT_GUARANTEE
        s = str(value).strip().upper()
        mapping = {
            "WITH_CCP_GUARANTEE": cls.WITH_CCP_GUARANTEE,
            "CCP": cls.WITH_CCP_GUARANTEE,
            "CENTRAL_GUARANTEE": cls.WITH_CCP_GUARANTEE,
            "COM_GARANTIA": cls.WITH_CCP_GUARANTEE,
            "GARANTIA_CENTRAL": cls.WITH_CCP_GUARANTEE,
            "WITHOUT_GUARANTEE": cls.WITHOUT_GUARANTEE,
            "BILATERAL": cls.WITHOUT_GUARANTEE,
            "SEM_GARANTIA": cls.WITHOUT_GUARANTEE,
            "SEM_GARANTIA_CENTRAL": cls.WITHOUT_GUARANTEE,
        }
        if s in mapping:
            return mapping[s]
        raise ValueError(
            f"Modalidade de garantia inválida: {value!r}. Aceitas: WITH_CCP_GUARANTEE, WITHOUT_GUARANTEE."
        )


class UnderlyingType(str, Enum):
    """Classificação do ativo-objeto da opção flexível."""

    EQUITY = "EQUITY"
    CURRENCY = "CURRENCY"

    @classmethod
    def from_str(cls, value: Any) -> UnderlyingType:
        """Converte e normaliza strings para UnderlyingType."""
        if isinstance(value, UnderlyingType):
            return value
        if not value:
            return cls.EQUITY
        s = str(value).strip().upper()
        mapping = {
            "EQUITY": cls.EQUITY,
            "STOCK": cls.EQUITY,
            "ACAO": cls.EQUITY,
            "AÇÃO": cls.EQUITY,
            "ACOES": cls.EQUITY,
            "AÇÕES": cls.EQUITY,
            "CURRENCY": cls.CURRENCY,
            "FX": cls.CURRENCY,
            "MOEDA": cls.CURRENCY,
            "CAMBIO": cls.CURRENCY,
            "CÂMBIO": cls.CURRENCY,
        }
        if s in mapping:
            return mapping[s]
        raise ValueError(f"Tipo de ativo-objeto inválido: {value!r}. Aceitos: EQUITY, CURRENCY.")


class OptionType(str, Enum):
    """Tipo da opção: Compra (Call) ou Venda (Put)."""

    CALL = "CALL"
    PUT = "PUT"

    @classmethod
    def from_str(cls, value: Any) -> OptionType:
        """Converte e normaliza strings para OptionType."""
        if isinstance(value, OptionType):
            return value
        if not value:
            raise ValueError("Tipo de opção (Call/Put) não pode ser nulo.")
        s = str(value).strip().upper()
        mapping = {
            "CALL": cls.CALL,
            "COMPRA": cls.CALL,
            "C": cls.CALL,
            "PUT": cls.PUT,
            "VENDA": cls.PUT,
            "V": cls.PUT,
            "P": cls.PUT,
        }
        if s in mapping:
            return mapping[s]
        raise ValueError(f"Tipo de opção inválido: {value!r}. Aceitos: CALL, PUT.")


class ExerciseStyle(str, Enum):
    """Estilo de exercício da opção."""

    EUROPEAN = "EUROPEAN"
    AMERICAN = "AMERICAN"

    @classmethod
    def from_str(cls, value: Any) -> ExerciseStyle:
        """Converte e normaliza strings para ExerciseStyle."""
        if isinstance(value, ExerciseStyle):
            return value
        if not value:
            return cls.EUROPEAN
        s = str(value).strip().upper()
        mapping = {
            "EUROPEAN": cls.EUROPEAN,
            "EUROPEU": cls.EUROPEAN,
            "EUR": cls.EUROPEAN,
            "AMERICAN": cls.AMERICAN,
            "AMERICANO": cls.AMERICAN,
            "AME": cls.AMERICAN,
            "USA": cls.AMERICAN,
        }
        if s in mapping:
            return mapping[s]
        raise ValueError(f"Estilo de exercício inválido: {value!r}. Aceitos: EUROPEAN, AMERICAN.")


class SettlementType(str, Enum):
    """Modalidade de liquidação da opção."""

    FINANCIAL = "FINANCIAL"
    PHYSICAL = "PHYSICAL"

    @classmethod
    def from_str(cls, value: Any) -> SettlementType:
        """Converte e normaliza strings para SettlementType."""
        if isinstance(value, SettlementType):
            return value
        if not value:
            return cls.FINANCIAL
        s = str(value).strip().upper()
        mapping = {
            "FINANCIAL": cls.FINANCIAL,
            "FINANCEIRA": cls.FINANCIAL,
            "FIN": cls.FINANCIAL,
            "PHYSICAL": cls.PHYSICAL,
            "FISICA": cls.PHYSICAL,
            "FÍSICA": cls.PHYSICAL,
            "PHY": cls.PHYSICAL,
        }
        if s in mapping:
            return mapping[s]
        raise ValueError(f"Modalidade de liquidação inválida: {value!r}. Aceitas: FINANCIAL, PHYSICAL.")


class BarrierType(str, Enum):
    """Cláusula de barreira da opção flexível."""

    NONE = "NONE"
    UP_AND_IN = "UP_AND_IN"
    UP_AND_OUT = "UP_AND_OUT"
    DOWN_AND_IN = "DOWN_AND_IN"
    DOWN_AND_OUT = "DOWN_AND_OUT"

    @classmethod
    def from_str(cls, value: Any) -> BarrierType:
        """Converte e normaliza strings para BarrierType."""
        if isinstance(value, BarrierType):
            return value
        if not value:
            return cls.NONE
        s = str(value).strip().upper()
        mapping = {
            "NONE": cls.NONE,
            "SEM_BARREIRA": cls.NONE,
            "NAO": cls.NONE,
            "NÃO": cls.NONE,
            "VANILLA": cls.NONE,
            "UP_AND_IN": cls.UP_AND_IN,
            "UP-AND-IN": cls.UP_AND_IN,
            "UI": cls.UP_AND_IN,
            "UP_AND_OUT": cls.UP_AND_OUT,
            "UP-AND-OUT": cls.UP_AND_OUT,
            "UO": cls.UP_AND_OUT,
            "KNOCK_OUT": cls.UP_AND_OUT,
            "DOWN_AND_IN": cls.DOWN_AND_IN,
            "DOWN-AND-IN": cls.DOWN_AND_IN,
            "DI": cls.DOWN_AND_IN,
            "DOWN_AND_OUT": cls.DOWN_AND_OUT,
            "DOWN-AND-OUT": cls.DOWN_AND_OUT,
            "DO": cls.DOWN_AND_OUT,
        }
        if s in mapping:
            return mapping[s]
        raise ValueError(
            f"Tipo de barreira inválido: {value!r}. "
            f"Aceitos: NONE, UP_AND_IN, UP_AND_OUT, DOWN_AND_IN, DOWN_AND_OUT."
        )


class ReasonCode(str, Enum):
    """Códigos de erro e resultado de validação de registro."""

    OK = "OK"
    ERR_NEGATIVE_OR_ZERO_VALUE = "ERR_NEGATIVE_OR_ZERO_VALUE"
    ERR_CHRONOLOGICAL_DATE = "ERR_CHRONOLOGICAL_DATE"
    ERR_IDENTICAL_LEGS = "ERR_IDENTICAL_LEGS"
    ERR_INCOMPATIBLE_CURRENCY = "ERR_INCOMPATIBLE_CURRENCY"
    ERR_MISSING_CLEARING_MEMBER = "ERR_MISSING_CLEARING_MEMBER"
    ERR_INVALID_CLEARING_MEMBER = "ERR_INVALID_CLEARING_MEMBER"
    ERR_INVALID_INDEXER = "ERR_INVALID_INDEXER"
    ERR_INVALID_SETTLEMENT = "ERR_INVALID_SETTLEMENT"
    ERR_INVALID_BARRIER = "ERR_INVALID_BARRIER"
    ERR_MISSING_REQUIRED_FIELD = "ERR_MISSING_REQUIRED_FIELD"
    ERR_INVALID_LEG_CONFIGURATION = "ERR_INVALID_LEG_CONFIGURATION"


# ---------------------------------------------------------------------------
# Hierarquia de Exceções Especializadas
# ---------------------------------------------------------------------------

class DerivativesError(Exception):
    """Exceção base para erros em derivativos de balcão NoMe."""
    pass


class DerivativesValidationError(DerivativesError):
    """Exceção base de validação de regras de negócio."""

    def __init__(self, message: str, reason_code: ReasonCode) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class NegativeOrZeroValueError(DerivativesValidationError):
    """Lançada quando valor, quantidade, taxa ou preço for estritamente nulo ou negativo."""

    def __init__(self, field_name: str, value: Any) -> None:
        super().__init__(
            f"Campo '{field_name}' deve ser estritamente maior que zero (recebido: {value!r}).",
            reason_code=ReasonCode.ERR_NEGATIVE_OR_ZERO_VALUE,
        )
        self.field_name = field_name
        self.value = value


class ChronologicalDateError(DerivativesValidationError):
    """Lançada quando a ordem cronológica de datas for violada."""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code=ReasonCode.ERR_CHRONOLOGICAL_DATE)


class InvalidLegConfigurationError(DerivativesValidationError):
    """Lançada quando as pontas de um swap forem inconsistentes ou idênticas."""

    def __init__(self, message: str, reason_code: ReasonCode = ReasonCode.ERR_IDENTICAL_LEGS) -> None:
        super().__init__(message, reason_code=reason_code)


class IncompatibleCurrencyError(DerivativesValidationError):
    """Lançada quando moedas de pontas ou conversões forem incompatíveis."""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code=ReasonCode.ERR_INCOMPATIBLE_CURRENCY)


class GuaranteeValidationError(DerivativesValidationError):
    """Lançada quando houver inconsistência nos dados de garantia central CCP."""

    def __init__(self, message: str, reason_code: ReasonCode = ReasonCode.ERR_MISSING_CLEARING_MEMBER) -> None:
        super().__init__(message, reason_code=reason_code)


class SettlementValidationError(DerivativesValidationError):
    """Lançada quando a modalidade de liquidação for incompatível com o ativo ou garantia."""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code=ReasonCode.ERR_INVALID_SETTLEMENT)


class BarrierValidationError(DerivativesValidationError):
    """Lançada quando os parâmetros de barreira forem inconsistentes."""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code=ReasonCode.ERR_INVALID_BARRIER)


class MissingRequiredFieldError(DerivativesValidationError):
    """Lançada quando um campo mandatório estiver ausente."""

    def __init__(self, field_name: str) -> None:
        super().__init__(
            f"Campo mandatório ausente: '{field_name}'.",
            reason_code=ReasonCode.ERR_MISSING_REQUIRED_FIELD,
        )
        self.field_name = field_name


# ---------------------------------------------------------------------------
# Funções Auxiliares de Parsing
# ---------------------------------------------------------------------------

def _parse_date(val: Any, field_name: str = "data") -> date:
    """Converte valores variados para date."""
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if not val:
        raise MissingRequiredFieldError(field_name)
    s = str(val).strip()
    # Tenta formatos ISO YYYY-MM-DD ou YYYYMMDD
    try:
        if len(s) == 8 and s.isdigit():
            return date(int(s[:4]), int(s[4:6]), int(s[6:]))
        return date.fromisoformat(s)
    except Exception as err:
        raise ChronologicalDateError(f"Data inválida no campo '{field_name}': {val!r}") from err


def _parse_decimal(val: Any, field_name: str = "valor") -> Decimal:
    """Converte valores para Decimal de forma determinística."""
    if val is None or val == "":
        raise MissingRequiredFieldError(field_name)
    try:
        return Decimal(str(val).strip())
    except InvalidOperation as err:
        raise NegativeOrZeroValueError(field_name, val) from err


# ---------------------------------------------------------------------------
# Classes de Dados (Dataclasses)
# ---------------------------------------------------------------------------

@dataclass
class SwapLeg:
    """Representação de uma ponta financeira de contrato de Swap."""

    leg_type: LegType
    indexer: IndexerType
    day_count_convention: DayCountConvention
    rate_or_spread: Decimal
    pu_initial: Decimal
    currency: str = "BRL"

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a ponta para dicionário."""
        return {
            "leg_type": self.leg_type.value,
            "indexer": self.indexer.value,
            "day_count_convention": self.day_count_convention.value,
            "rate_or_spread": str(self.rate_or_spread),
            "pu_initial": str(self.pu_initial),
            "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SwapLeg:
        """Cria instância a partir de dicionário."""
        leg_type_raw = data.get("leg_type") or data.get("LegType")
        indexer_raw = data.get("indexer") or data.get("Indexer")
        conv_raw = data.get("day_count_convention") or data.get("DayCountConvention") or "DU/252"
        rate_raw = data.get("rate_or_spread") or data.get("RateOrSpread")
        pu_raw = data.get("pu_initial") or data.get("PUInitial") or data.get("initial_unit_price") or "1000.00"
        currency = str(data.get("currency") or data.get("Currency") or "BRL").strip().upper()

        if leg_type_raw is None:
            raise MissingRequiredFieldError("leg_type")
        if indexer_raw is None:
            raise MissingRequiredFieldError("indexer")
        if rate_raw is None:
            raise MissingRequiredFieldError("rate_or_spread")

        return cls(
            leg_type=LegType.from_str(leg_type_raw),
            indexer=IndexerType.from_str(indexer_raw),
            day_count_convention=DayCountConvention.from_str(conv_raw),
            rate_or_spread=_parse_decimal(rate_raw, "rate_or_spread"),
            pu_initial=_parse_decimal(pu_raw, "pu_initial"),
            currency=currency,
        )


@dataclass
class SwapContract:
    """Contrato de Swap de Balcão (FL-016) registrado no B3 NoMe."""

    contract_id: str
    trade_date: date
    start_date: date
    maturity_date: date
    business_days: int
    calendar_days: int
    notional_amount: Decimal
    active_leg: SwapLeg
    passive_leg: SwapLeg
    quantity: Optional[Decimal] = None
    pu_initial: Optional[Decimal] = None
    currency: str = "BRL"
    foreign_currency: Optional[str] = None
    initial_fx_rate: Optional[Decimal] = None
    guarantee_type: GuaranteeType = GuaranteeType.WITHOUT_GUARANTEE
    clearing_member_active: Optional[int] = None
    clearing_member_passive: Optional[int] = None
    party_active: str = ""
    party_passive: str = ""
    periodic_adjustments: bool = False
    status: RegistrationStatus = RegistrationStatus.SUBMITTED
    reason_code: Optional[ReasonCode] = None

    def effective_notional_brl(self) -> Decimal:
        """Retorna o valor nocional efetivo em moeda corrente nacional (BRL)."""
        if self.foreign_currency and self.initial_fx_rate:
            return self.notional_amount * self.initial_fx_rate
        return self.notional_amount

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o contrato para dicionário JSON-compatível."""
        res: Dict[str, Any] = {
            "contract_id": self.contract_id,
            "contract_type": "SWAP",
            "trade_date": self.trade_date.isoformat(),
            "start_date": self.start_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "business_days": self.business_days,
            "calendar_days": self.calendar_days,
            "notional_amount": str(self.notional_amount),
            "currency": self.currency,
            "guarantee_type": self.guarantee_type.value,
            "party_active": self.party_active,
            "party_passive": self.party_passive,
            "periodic_adjustments": self.periodic_adjustments,
            "status": self.status.value,
            "active_leg": self.active_leg.to_dict(),
            "passive_leg": self.passive_leg.to_dict(),
        }
        if self.quantity is not None:
            res["quantity"] = int(self.quantity) if self.quantity % 1 == 0 else float(self.quantity)
        if self.pu_initial is not None:
            res["pu_initial"] = str(self.pu_initial)
        if self.foreign_currency:
            res["foreign_currency"] = self.foreign_currency
        if self.initial_fx_rate is not None:
            res["initial_fx_rate"] = str(self.initial_fx_rate)
        if self.clearing_member_active is not None:
            res["clearing_member_active"] = self.clearing_member_active
        if self.clearing_member_passive is not None:
            res["clearing_member_passive"] = self.clearing_member_passive
        if self.reason_code is not None:
            res["reason_code"] = self.reason_code.value
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SwapContract:
        """Cria instância de SwapContract a partir de dicionário."""
        contract_id = data.get("contract_id") or data.get("ContractID")
        if not contract_id:
            raise MissingRequiredFieldError("contract_id")

        trade_date = _parse_date(data.get("trade_date") or data.get("TradeDate"), "trade_date")
        start_date = _parse_date(data.get("start_date") or data.get("StartDate"), "start_date")
        maturity_date = _parse_date(data.get("maturity_date") or data.get("MaturityDate"), "maturity_date")

        bus_days_raw = data.get("business_days") or data.get("BusinessDays")
        cal_days_raw = data.get("calendar_days") or data.get("CalendarDays")

        if bus_days_raw is None:
            raise MissingRequiredFieldError("business_days")
        if cal_days_raw is None:
            raise MissingRequiredFieldError("calendar_days")

        business_days = int(bus_days_raw)
        calendar_days = int(cal_days_raw)

        notional_raw = data.get("notional_amount") or data.get("NotionalAmount")
        if notional_raw is None:
            raise MissingRequiredFieldError("notional_amount")
        notional_amount = _parse_decimal(notional_raw, "notional_amount")

        # Legs
        active_data = data.get("active_leg") or data.get("ActiveLeg")
        passive_data = data.get("passive_leg") or data.get("PassiveLeg")

        if not active_data or not isinstance(active_data, dict):
            raise MissingRequiredFieldError("active_leg")
        if not passive_data or not isinstance(passive_data, dict):
            raise MissingRequiredFieldError("passive_leg")

        active_leg = SwapLeg.from_dict(active_data)
        passive_leg = SwapLeg.from_dict(passive_data)

        # Opcionais e complementares
        quantity = _parse_decimal(data["quantity"], "quantity") if data.get("quantity") is not None else None
        pu_initial = _parse_decimal(data["pu_initial"], "pu_initial") if data.get("pu_initial") is not None else None

        currency = str(data.get("currency") or data.get("Currency") or "BRL").strip().upper()
        foreign_currency = data.get("foreign_currency") or data.get("ForeignCurrency")
        if foreign_currency:
            foreign_currency = str(foreign_currency).strip().upper()

        initial_fx_rate = (
            _parse_decimal(data["initial_fx_rate"], "initial_fx_rate")
            if data.get("initial_fx_rate") is not None
            else None
        )

        guarantee_raw = data.get("guarantee_type") or data.get("GuaranteeType") or "WITHOUT_GUARANTEE"
        guarantee_type = GuaranteeType.from_str(guarantee_raw)

        clearing_member_active = (
            int(data["clearing_member_active"]) if data.get("clearing_member_active") is not None else None
        )
        clearing_member_passive = (
            int(data["clearing_member_passive"]) if data.get("clearing_member_passive") is not None else None
        )

        party_active = str(data.get("party_active") or data.get("PartyActive") or "")
        party_passive = str(data.get("party_passive") or data.get("PartyPassive") or "")

        periodic_adjustments = bool(data.get("periodic_adjustments") or data.get("PeriodicAdjustments", False))
        status_raw = data.get("status") or data.get("Status") or "SUBMITTED"
        status = RegistrationStatus.from_str(status_raw)

        reason_code_raw = data.get("reason_code") or data.get("ReasonCode")
        reason_code = ReasonCode(reason_code_raw) if reason_code_raw else None

        return cls(
            contract_id=str(contract_id).strip(),
            trade_date=trade_date,
            start_date=start_date,
            maturity_date=maturity_date,
            business_days=business_days,
            calendar_days=calendar_days,
            notional_amount=notional_amount,
            active_leg=active_leg,
            passive_leg=passive_leg,
            quantity=quantity,
            pu_initial=pu_initial,
            currency=currency,
            foreign_currency=foreign_currency,
            initial_fx_rate=initial_fx_rate,
            guarantee_type=guarantee_type,
            clearing_member_active=clearing_member_active,
            clearing_member_passive=clearing_member_passive,
            party_active=party_active,
            party_passive=party_passive,
            periodic_adjustments=periodic_adjustments,
            status=status,
            reason_code=reason_code,
        )

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> SwapContract:
        """Carrega e instancia um SwapContract a partir de arquivo JSON."""
        p = Path(path)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


@dataclass
class FlexibleOptionContract:
    """Contrato de Opção Flexível de Balcão (FL-017) registrado no B3 NoMe."""

    contract_id: str
    trade_date: date
    expiration_date: date
    underlying_asset: str
    underlying_type: UnderlyingType
    option_type: OptionType
    exercise_style: ExerciseStyle
    strike_price: Decimal
    premium: Decimal
    quantity: Decimal
    premium_payment_date: Optional[date] = None
    settlement_type: SettlementType = SettlementType.FINANCIAL
    barrier_type: BarrierType = BarrierType.NONE
    barrier_level: Optional[Decimal] = None
    guarantee_type: GuaranteeType = GuaranteeType.WITHOUT_GUARANTEE
    clearing_member_holder: Optional[int] = None
    clearing_member_writer: Optional[int] = None
    party_holder: str = ""
    party_writer: str = ""
    currency: str = "BRL"
    status: RegistrationStatus = RegistrationStatus.SUBMITTED
    reason_code: Optional[ReasonCode] = None

    def total_premium(self) -> Decimal:
        """Calcula o valor financeiro total do prêmio (Prêmio Unitário * Quantidade)."""
        return self.premium * self.quantity

    def notional_underlying(self) -> Decimal:
        """Calcula o montante nocional subjacente (Strike * Quantidade)."""
        return self.strike_price * self.quantity

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a opção flexível para dicionário JSON-compatível."""
        res: Dict[str, Any] = {
            "contract_id": self.contract_id,
            "contract_type": "FLEXIBLE_OPTION",
            "trade_date": self.trade_date.isoformat(),
            "expiration_date": self.expiration_date.isoformat(),
            "underlying_asset": self.underlying_asset,
            "underlying_type": self.underlying_type.value,
            "option_type": self.option_type.value,
            "exercise_style": self.exercise_style.value,
            "strike_price": str(self.strike_price),
            "premium": str(self.premium),
            "quantity": int(self.quantity) if self.quantity % 1 == 0 else float(self.quantity),
            "total_premium": str(self.total_premium()),
            "notional_underlying": str(self.notional_underlying()),
            "settlement_type": self.settlement_type.value,
            "barrier_type": self.barrier_type.value,
            "guarantee_type": self.guarantee_type.value,
            "party_holder": self.party_holder,
            "party_writer": self.party_writer,
            "currency": self.currency,
            "status": self.status.value,
        }
        if self.premium_payment_date is not None:
            res["premium_payment_date"] = self.premium_payment_date.isoformat()
        if self.barrier_level is not None:
            res["barrier_level"] = str(self.barrier_level)
        if self.clearing_member_holder is not None:
            res["clearing_member_holder"] = self.clearing_member_holder
        if self.clearing_member_writer is not None:
            res["clearing_member_writer"] = self.clearing_member_writer
        if self.reason_code is not None:
            res["reason_code"] = self.reason_code.value
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FlexibleOptionContract:
        """Cria instância de FlexibleOptionContract a partir de dicionário."""
        contract_id = data.get("contract_id") or data.get("ContractID")
        if not contract_id:
            raise MissingRequiredFieldError("contract_id")

        trade_date = _parse_date(data.get("trade_date") or data.get("TradeDate"), "trade_date")
        exp_date = _parse_date(data.get("expiration_date") or data.get("ExpirationDate"), "expiration_date")

        pmt_raw = data.get("premium_payment_date") or data.get("PremiumPaymentDate")
        premium_payment_date = _parse_date(pmt_raw, "premium_payment_date") if pmt_raw else None

        underlying_asset = str(data.get("underlying_asset") or data.get("UnderlyingAsset") or "").strip().upper()
        if not underlying_asset:
            raise MissingRequiredFieldError("underlying_asset")

        underlying_type = UnderlyingType.from_str(data.get("underlying_type") or data.get("UnderlyingType"))
        option_type = OptionType.from_str(data.get("option_type") or data.get("OptionType"))
        exercise_style = ExerciseStyle.from_str(data.get("exercise_style") or data.get("ExerciseStyle"))

        strike_price = _parse_decimal(data.get("strike_price") or data.get("StrikePrice"), "strike_price")
        premium = _parse_decimal(data.get("premium") or data.get("Premium") or "0", "premium")
        quantity = _parse_decimal(data.get("quantity") or data.get("Quantity"), "quantity")

        settlement_type = SettlementType.from_str(data.get("settlement_type") or data.get("SettlementType"))
        barrier_type = BarrierType.from_str(data.get("barrier_type") or data.get("BarrierType"))

        barrier_level_raw = data.get("barrier_level") or data.get("BarrierLevel")
        barrier_level = _parse_decimal(barrier_level_raw, "barrier_level") if barrier_level_raw is not None else None

        guarantee_raw = data.get("guarantee_type") or data.get("GuaranteeType") or "WITHOUT_GUARANTEE"
        guarantee_type = GuaranteeType.from_str(guarantee_raw)

        clearing_member_holder = (
            int(data["clearing_member_holder"]) if data.get("clearing_member_holder") is not None else None
        )
        clearing_member_writer = (
            int(data["clearing_member_writer"]) if data.get("clearing_member_writer") is not None else None
        )

        party_holder = str(data.get("party_holder") or data.get("PartyHolder") or "")
        party_writer = str(data.get("party_writer") or data.get("PartyWriter") or "")

        currency = str(data.get("currency") or data.get("Currency") or "BRL").strip().upper()
        status_raw = data.get("status") or data.get("Status") or "SUBMITTED"
        status = RegistrationStatus.from_str(status_raw)

        reason_code_raw = data.get("reason_code") or data.get("ReasonCode")
        reason_code = ReasonCode(reason_code_raw) if reason_code_raw else None

        return cls(
            contract_id=str(contract_id).strip(),
            trade_date=trade_date,
            expiration_date=exp_date,
            underlying_asset=underlying_asset,
            underlying_type=underlying_type,
            option_type=option_type,
            exercise_style=exercise_style,
            strike_price=strike_price,
            premium=premium,
            quantity=quantity,
            premium_payment_date=premium_payment_date,
            settlement_type=settlement_type,
            barrier_type=barrier_type,
            barrier_level=barrier_level,
            guarantee_type=guarantee_type,
            clearing_member_holder=clearing_member_holder,
            clearing_member_writer=clearing_member_writer,
            party_holder=party_holder,
            party_writer=party_writer,
            currency=currency,
            status=status,
            reason_code=reason_code,
        )

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> FlexibleOptionContract:
        """Carrega e instancia um FlexibleOptionContract a partir de arquivo JSON."""
        p = Path(path)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# Estruturas de Resultado de Cálculo e Validação
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationResult:
    """Resultado formal da validação de negócio do contrato."""

    is_valid: bool
    contract_id: str
    reason_code: ReasonCode
    message: str


@dataclass(frozen=True)
class SwapPayoffResult:
    """Resultado do cálculo determinístico de payoff no vencimento de Swap."""

    contract_id: str
    notional_amount: Decimal
    active_factor: Decimal
    passive_factor: Decimal
    active_final_value: Decimal
    passive_final_value: Decimal
    net_cashflow_active: Decimal
    receiving_party: str
    paying_party: str

    @property
    def is_bullet_settled(self) -> bool:
        """Informa se a liquidação é positiva ou negativa."""
        return self.net_cashflow_active != Decimal(0)


@dataclass(frozen=True)
class OptionPayoffResult:
    """Resultado do cálculo determinístico de payoff de Opção Flexível."""

    contract_id: str
    underlying_asset: str
    strike_price: Decimal
    spot_price_at_expiry: Decimal
    quantity: Decimal
    is_exercised: bool
    is_barrier_breached: bool
    unit_payoff: Decimal
    total_payoff: Decimal
    settlement_type: SettlementType


# ---------------------------------------------------------------------------
# Motor de Registro e Validação (DerivativesRegistrationEngine)
# ---------------------------------------------------------------------------

class DerivativesRegistrationEngine:
    """Motor de registro e validação de derivativos de balcão e opções flexíveis."""

    def __init__(self, raise_on_validation_failure: bool = True) -> None:
        self.raise_on_validation_failure = raise_on_validation_failure

    def validate_swap(self, contract: SwapContract) -> ValidationResult:
        """Aplica todas as regras de negócio estritas a um contrato de Swap."""
        # 1. Notional estritamente positivo
        if contract.notional_amount <= Decimal(0):
            err = NegativeOrZeroValueError("notional_amount", contract.notional_amount)
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        if contract.quantity is not None and contract.quantity <= Decimal(0):
            err = NegativeOrZeroValueError("quantity", contract.quantity)
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        if contract.pu_initial is not None and contract.pu_initial <= Decimal(0):
            err = NegativeOrZeroValueError("pu_initial", contract.pu_initial)
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        # 2. Cronologia de datas
        if contract.start_date < contract.trade_date:
            err = ChronologicalDateError(
                f"Data de início ({contract.start_date}) não pode ser anterior à data de negociação ({contract.trade_date})."
            )
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        if contract.maturity_date <= contract.start_date:
            err = ChronologicalDateError(
                f"Data de vencimento ({contract.maturity_date}) deve ser estritamente posterior à data de início ({contract.start_date})."
            )
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        if contract.business_days < 1:
            err = ChronologicalDateError(
                f"Prazo em dias úteis deve ser de pelo menos 1 dia útil (recebido: {contract.business_days})."
            )
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        if contract.calendar_days < contract.business_days:
            err = ChronologicalDateError(
                f"Dias corridos ({contract.calendar_days}) não podem ser inferiores aos dias úteis ({contract.business_days})."
            )
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        # 3. Validação das pontas (legs)
        if contract.active_leg.leg_type != LegType.ACTIVE:
            err = InvalidLegConfigurationError(
                f"Ponta ativa deve ter leg_type=ACTIVE, recebido: {contract.active_leg.leg_type!r}",
                reason_code=ReasonCode.ERR_INVALID_LEG_CONFIGURATION,
            )
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        if contract.passive_leg.leg_type != LegType.PASSIVE:
            err = InvalidLegConfigurationError(
                f"Ponta passiva deve ter leg_type=PASSIVE, recebido: {contract.passive_leg.leg_type!r}",
                reason_code=ReasonCode.ERR_INVALID_LEG_CONFIGURATION,
            )
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        if contract.active_leg.pu_initial <= Decimal(0):
            err = NegativeOrZeroValueError("active_leg.pu_initial", contract.active_leg.pu_initial)
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        if contract.passive_leg.pu_initial <= Decimal(0):
            err = NegativeOrZeroValueError("passive_leg.pu_initial", contract.passive_leg.pu_initial)
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        # Proibição de pontas idênticas (mesmo indexador e mesma taxa/spread na mesma moeda)
        if (
            contract.active_leg.indexer == contract.passive_leg.indexer
            and contract.active_leg.rate_or_spread == contract.passive_leg.rate_or_spread
            and contract.active_leg.currency == contract.passive_leg.currency
        ):
            err = InvalidLegConfigurationError(
                f"Ponta ativa e ponta passiva não podem ser idênticas "
                f"(ambas indexadas a {contract.active_leg.indexer.value} com taxa/spread {contract.active_leg.rate_or_spread})."
            )
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        # Validação de moedas e taxas de câmbio
        if contract.active_leg.currency != contract.passive_leg.currency:
            if not contract.foreign_currency:
                err = IncompatibleCurrencyError(
                    f"Ponta ativa ({contract.active_leg.currency}) e ponta passiva ({contract.passive_leg.currency}) "
                    f"possuem moedas distintas, mas 'foreign_currency' não foi informado."
                )
                if self.raise_on_validation_failure:
                    raise err
                return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

            if contract.initial_fx_rate is None or contract.initial_fx_rate <= Decimal(0):
                err = IncompatibleCurrencyError(
                    f"Contrato envolvendo moeda estrangeira ({contract.foreign_currency}) requer 'initial_fx_rate' > 0."
                )
                if self.raise_on_validation_failure:
                    raise err
                return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        if contract.foreign_currency and (contract.initial_fx_rate is None or contract.initial_fx_rate <= Decimal(0)):
            err = IncompatibleCurrencyError(
                f"Quando 'foreign_currency' ({contract.foreign_currency}) é declarada, 'initial_fx_rate' deve ser estritamente > 0."
            )
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        # 4. Validação de Garantia Central CCP B3
        if contract.guarantee_type == GuaranteeType.WITH_CCP_GUARANTEE:
            if contract.clearing_member_active is None:
                err = GuaranteeValidationError(
                    "Registro com garantia central (CCP B3) exige 'clearing_member_active'.",
                    reason_code=ReasonCode.ERR_MISSING_CLEARING_MEMBER,
                )
                if self.raise_on_validation_failure:
                    raise err
                return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

            if contract.clearing_member_passive is None:
                err = GuaranteeValidationError(
                    "Registro com garantia central (CCP B3) exige 'clearing_member_passive'.",
                    reason_code=ReasonCode.ERR_MISSING_CLEARING_MEMBER,
                )
                if self.raise_on_validation_failure:
                    raise err
                return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

            if contract.clearing_member_active <= 0:
                err = GuaranteeValidationError(
                    f"Código de clearing member ativo inválido: {contract.clearing_member_active}",
                    reason_code=ReasonCode.ERR_INVALID_CLEARING_MEMBER,
                )
                if self.raise_on_validation_failure:
                    raise err
                return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

            if contract.clearing_member_passive <= 0:
                err = GuaranteeValidationError(
                    f"Código de clearing member passivo inválido: {contract.clearing_member_passive}",
                    reason_code=ReasonCode.ERR_INVALID_CLEARING_MEMBER,
                )
                if self.raise_on_validation_failure:
                    raise err
                return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        return ValidationResult(True, contract.contract_id, ReasonCode.OK, "Contrato de Swap válido.")

    def validate_flexible_option(self, contract: FlexibleOptionContract) -> ValidationResult:
        """Aplica todas as regras de negócio estritas a um contrato de Opção Flexível."""
        # 1. Strike e quantidade estritamente positivos
        if contract.strike_price <= Decimal(0):
            err = NegativeOrZeroValueError("strike_price", contract.strike_price)
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        if contract.quantity <= Decimal(0):
            err = NegativeOrZeroValueError("quantity", contract.quantity)
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        if contract.premium < Decimal(0):
            err = NegativeOrZeroValueError("premium", contract.premium)
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        # 2. Cronologia de datas
        if contract.expiration_date <= contract.trade_date:
            err = ChronologicalDateError(
                f"Data de vencimento ({contract.expiration_date}) deve ser estritamente posterior à data de negociação ({contract.trade_date})."
            )
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        if contract.premium_payment_date is not None:
            if contract.premium_payment_date < contract.trade_date:
                err = ChronologicalDateError(
                    f"Data de liquidação do prêmio ({contract.premium_payment_date}) não pode ser anterior à data de negociação ({contract.trade_date})."
                )
                if self.raise_on_validation_failure:
                    raise err
                return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

            if contract.premium_payment_date > contract.expiration_date:
                err = ChronologicalDateError(
                    f"Data de liquidação do prêmio ({contract.premium_payment_date}) não pode ser posterior ao vencimento da opção ({contract.expiration_date})."
                )
                if self.raise_on_validation_failure:
                    raise err
                return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        # 3. Barreira
        if contract.barrier_type != BarrierType.NONE:
            if contract.barrier_level is None or contract.barrier_level <= Decimal(0):
                err = BarrierValidationError(
                    f"Opção flexível com barreira '{contract.barrier_type.value}' requer 'barrier_level' estritamente > 0."
                )
                if self.raise_on_validation_failure:
                    raise err
                return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        # 4. Modalidade de liquidação
        if contract.settlement_type == SettlementType.PHYSICAL and contract.underlying_type != UnderlyingType.EQUITY:
            err = SettlementValidationError(
                f"Liquidação física é permitida exclusivamente para ativos do tipo EQUITY (ações listadas). "
                f"Ativo '{contract.underlying_asset}' do tipo '{contract.underlying_type.value}' não admite liquidação física."
            )
            if self.raise_on_validation_failure:
                raise err
            return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        # 5. Validação de Garantia Central CCP B3
        if contract.guarantee_type == GuaranteeType.WITH_CCP_GUARANTEE:
            if contract.clearing_member_holder is None:
                err = GuaranteeValidationError(
                    "Registro com garantia central (CCP B3) exige 'clearing_member_holder'.",
                    reason_code=ReasonCode.ERR_MISSING_CLEARING_MEMBER,
                )
                if self.raise_on_validation_failure:
                    raise err
                return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

            if contract.clearing_member_writer is None:
                err = GuaranteeValidationError(
                    "Registro com garantia central (CCP B3) exige 'clearing_member_writer'.",
                    reason_code=ReasonCode.ERR_MISSING_CLEARING_MEMBER,
                )
                if self.raise_on_validation_failure:
                    raise err
                return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

            if contract.clearing_member_holder <= 0:
                err = GuaranteeValidationError(
                    f"Código de clearing member do titular inválido: {contract.clearing_member_holder}",
                    reason_code=ReasonCode.ERR_INVALID_CLEARING_MEMBER,
                )
                if self.raise_on_validation_failure:
                    raise err
                return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

            if contract.clearing_member_writer <= 0:
                err = GuaranteeValidationError(
                    f"Código de clearing member do lançador inválido: {contract.clearing_member_writer}",
                    reason_code=ReasonCode.ERR_INVALID_CLEARING_MEMBER,
                )
                if self.raise_on_validation_failure:
                    raise err
                return ValidationResult(False, contract.contract_id, err.reason_code, str(err))

        return ValidationResult(True, contract.contract_id, ReasonCode.OK, "Contrato de Opção Flexível válido.")

    def register_swap(self, contract: SwapContract) -> SwapContract:
        """Registra formalmente um contrato de swap após aprovação de validação."""
        res = self.validate_swap(contract)
        if not res.is_valid:
            contract.status = RegistrationStatus.REJECTED
            contract.reason_code = res.reason_code
            return contract

        contract.status = RegistrationStatus.REGISTERED
        contract.reason_code = ReasonCode.OK
        return contract

    def register_flexible_option(self, contract: FlexibleOptionContract) -> FlexibleOptionContract:
        """Registra formalmente uma opção flexível após aprovação de validação."""
        res = self.validate_flexible_option(contract)
        if not res.is_valid:
            contract.status = RegistrationStatus.REJECTED
            contract.reason_code = res.reason_code
            return contract

        contract.status = RegistrationStatus.REGISTERED
        contract.reason_code = ReasonCode.OK
        return contract

    # -----------------------------------------------------------------------
    # Cálculos Determinísticos
    # -----------------------------------------------------------------------

    @staticmethod
    def calculate_swap_notional(contract: SwapContract) -> Decimal:
        """Calcula o valor financeiro nocional efetivo do contrato de swap em BRL."""
        return contract.effective_notional_brl()

    @staticmethod
    def calculate_fixed_leg_factor(
        rate: Decimal,
        business_days: int,
        convention: DayCountConvention = DayCountConvention.DU_252,
        calendar_days: Optional[int] = None,
    ) -> Decimal:
        """Calcula o fator de acumulação de uma ponta pré-fixada.

        Convenção DU/252:
            Fator = (1 + taxa) ** (business_days / 252)
        Convenção DC/360:
            Fator = 1 + taxa * (calendar_days / 360)
        """
        if convention == DayCountConvention.DU_252:
            base = float(Decimal("1") + rate)
            exponent = float(business_days) / 252.0
            factor = math.pow(base, exponent)
            return Decimal(str(round(factor, 8)))
        elif convention == DayCountConvention.DC_360:
            c_days = calendar_days if calendar_days is not None else int(round(business_days * (360.0 / 252.0)))
            linear = Decimal("1") + (rate * Decimal(c_days) / Decimal("360"))
            return linear.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        elif convention == DayCountConvention.DC_365:
            c_days = calendar_days if calendar_days is not None else int(round(business_days * (365.0 / 252.0)))
            linear = Decimal("1") + (rate * Decimal(c_days) / Decimal("365"))
            return linear.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        raise ValueError(f"Convenção não suportada para cálculo de fator fixo: {convention}")

    def calculate_swap_payoff(
        self,
        contract: SwapContract,
        active_realized_factor: Decimal,
        passive_realized_factor: Decimal,
    ) -> SwapPayoffResult:
        """Calcula o fluxo financeiro de liquidação do Swap no vencimento.

        Fórmulas determinísticas B3 NoMe:
            Valor Final Ativo = Nocional * Fator Realizado Ativo
            Valor Final Passivo = Nocional * Fator Realizado Passivo
            Fluxo Líquido Ativo = Valor Final Ativo - Valor Final Passivo
        """
        notional = contract.effective_notional_brl()
        active_final_val = (notional * active_realized_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        passive_final_val = (notional * passive_realized_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        net_cashflow_active = active_final_val - passive_final_val

        if net_cashflow_active > Decimal(0):
            receiving_party = contract.party_active or "PARTY_ACTIVE"
            paying_party = contract.party_passive or "PARTY_PASSIVE"
        elif net_cashflow_active < Decimal(0):
            receiving_party = contract.party_passive or "PARTY_PASSIVE"
            paying_party = contract.party_active or "PARTY_ACTIVE"
        else:
            receiving_party = "NONE"
            paying_party = "NONE"

        return SwapPayoffResult(
            contract_id=contract.contract_id,
            notional_amount=notional,
            active_factor=active_realized_factor,
            passive_factor=passive_realized_factor,
            active_final_value=active_final_val,
            passive_final_value=passive_final_val,
            net_cashflow_active=net_cashflow_active,
            receiving_party=receiving_party,
            paying_party=paying_party,
        )

    def calculate_option_payoff(
        self,
        contract: FlexibleOptionContract,
        spot_price_at_expiry: Decimal,
        highest_price_during_life: Optional[Decimal] = None,
        lowest_price_during_life: Optional[Decimal] = None,
    ) -> OptionPayoffResult:
        """Calcula determinísticamente o payoff da opção flexível no vencimento.

        Trata opções Call e Put com liquidação financeira, considerando barreiras
        ativadoras (In) e desativadoras (Out).
        """
        spot = spot_price_at_expiry
        strike = contract.strike_price
        high = max(spot, highest_price_during_life) if highest_price_during_life is not None else spot
        low = min(spot, lowest_price_during_life) if lowest_price_during_life is not None else spot

        is_barrier_breached = False
        is_active = True

        # Avaliação de Barreira
        if contract.barrier_type == BarrierType.UP_AND_OUT:
            assert contract.barrier_level is not None
            if high >= contract.barrier_level or spot >= contract.barrier_level:
                is_barrier_breached = True
                is_active = False
        elif contract.barrier_type == BarrierType.DOWN_AND_OUT:
            assert contract.barrier_level is not None
            if low <= contract.barrier_level or spot <= contract.barrier_level:
                is_barrier_breached = True
                is_active = False
        elif contract.barrier_type == BarrierType.UP_AND_IN:
            assert contract.barrier_level is not None
            if high >= contract.barrier_level or spot >= contract.barrier_level:
                is_barrier_breached = True
                is_active = True
            else:
                is_active = False
        elif contract.barrier_type == BarrierType.DOWN_AND_IN:
            assert contract.barrier_level is not None
            if low <= contract.barrier_level or spot <= contract.barrier_level:
                is_barrier_breached = True
                is_active = True
            else:
                is_active = False

        # Apuração de Intrinsic Value
        unit_payoff = Decimal("0.00")
        is_exercised = False

        if is_active:
            if contract.option_type == OptionType.CALL:
                if spot > strike:
                    unit_payoff = (spot - strike).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    is_exercised = True
            elif contract.option_type == OptionType.PUT:
                if spot < strike:
                    unit_payoff = (strike - spot).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    is_exercised = True

        total_payoff = (unit_payoff * contract.quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return OptionPayoffResult(
            contract_id=contract.contract_id,
            underlying_asset=contract.underlying_asset,
            strike_price=strike,
            spot_price_at_expiry=spot,
            quantity=contract.quantity,
            is_exercised=is_exercised,
            is_barrier_breached=is_barrier_breached,
            unit_payoff=unit_payoff,
            total_payoff=total_payoff,
            settlement_type=contract.settlement_type,
        )
