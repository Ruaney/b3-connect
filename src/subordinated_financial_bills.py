"""Módulo de Escrituração, Liquidação e Absorção de Perdas de Letras Financeiras Subordinadas (FL-073).

Implementa as regras de negócio, modelagem matemática e invariantes formais para:
- Letras Financeiras (LF) Subordinadas emitidas por instituições financeiras autorizadas pelo BACEN,
  registradas e escrituradas no Balcão NoMe B3.
- Instrumentos elegíveis a compor o Capital Nível 1 (Capital Complementar / Additional Tier 1 - AT1 - Perpétuas)
  e Capital Nível 2 (Tier 2 - Prazo mínimo de 5 anos / 60 meses), em estrita conformidade com:
  * Resolução CMN nº 4.958/2021 (Metodologia de Apuração do Patrimônio de Referência - PR).
  * Resolução CMN nº 5.036/2022 (Condições e Características de Emissão de Letras Financeiras).
  * Lei nº 12.249/2010 (Marco Legal da Letra Financeira).
  * Padrões Prudenciais de Basileia III (Basel Committee on Banking Supervision - BCBS).

Invariantes Formais Provadas:
1. Identidade Escritural de PU e Saldo Devedor:
   Saldo_Devedor == Quantidade * PU_Atual (centavo a centavo).
2. Conservação Financeira de Absorção de Perdas no Write-Down:
   Perda_Absorvida == Quantidade * (PU_Antes - PU_Depois).
3. Inviolabilidade da Hierarquia de Capital (Loss Absorption Priority):
   LFs Nível 1 (AT1) absorvem perdas prioritária e integralmente antes de qualquer
   impacto sobre LFs Nível 2 (Tier 2).
4. Conservação Econômica em Conversão Mandatória em Ações:
   Perda_Alocada == (Ações_Emitidas * Preço_Conversão) + Troco_Residual.
5. Teto Estrito de Write-Up (Recomposição Temporária):
   PU_Pós_Write_Up <= PU_Referência_Original e condicionado a Lucro Líquido Auditado e BACEN.
6. Não-Inadimplência na Suspensão Discricionária de Cupom:
   Suspensão discricionária de cupom Nível 1 não aciona Default, não gera juros moratórios
   e ativa obrigatoriamente o Dividend Stopper.
7. Sanidade Temporal e Amortização Prudencial de Capital Nível 2:
   Prazo de emissão >= 5 anos e redutor linear de 20% ao ano nos últimos 5 anos de vida útil.

100% Python Standard Library (dataclasses, Decimal, datetime, enum, json, pathlib).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union


# =====================================================================
# CONSTANTES DE PRECISÃO NUMÉRICA E ARREDONDAMENTO B3 / CETIP
# =====================================================================

QUANTIZE_MONEY = Decimal("0.01")
QUANTIZE_PU = Decimal("0.00000001")
QUANTIZE_RATE = Decimal("0.00000001")
QUANTIZE_QTY = Decimal("1")

REGULATORY_MIN_CET1_TRIGGER = Decimal("0.05125")  # Gatilho regulatório padrão de 5,125%
MINIMUM_TIER2_TERM_DAYS = 1825                    # 5 anos (5 * 365 dias)
MINIMUM_CALL_OPTION_DAYS = 1825                   # Prazo mínimo para Call Option (5 anos)
MINIMUM_NOMINAL_VALUE_CAPITAL = Decimal("50000.00")  # Valor unitário mínimo LF


def round_cents(val: Decimal) -> Decimal:
    """Arredonda valor financeiro para 2 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_MONEY, rounding=ROUND_HALF_UP)


def round_unit(val: Decimal) -> Decimal:
    """Arredonda Preço Unitário (PU) para 8 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_PU, rounding=ROUND_HALF_UP)


def round_rate(val: Decimal) -> Decimal:
    """Arredonda taxas e fatores para 8 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_RATE, rounding=ROUND_HALF_UP)


def to_decimal(value: Any, field_name: str = "campo") -> Decimal:
    """Converte valor para Decimal de forma robusta e auditável."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise SubordinatedBillValidationError(f"Valor nulo não permitido para '{field_name}'.")
    try:
        cleaned = str(value).strip().replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError) as err:
        raise SubordinatedBillValidationError(
            f"Valor inválido '{value}' para o campo '{field_name}': {err}"
        )


def parse_date(value: Any, field_name: str = "data") -> Optional[date]:
    """Converte representação ISO-8601 em date de forma tolerante a None."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        return date.fromisoformat(cleaned)
    raise SubordinatedBillValidationError(
        f"Tipo inválido '{type(value).__name__}' para campo de data '{field_name}'."
    )


def parse_datetime(value: Any, field_name: str = "data_hora") -> datetime:
    """Converte representação ISO-8601 em datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        cleaned = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    raise SubordinatedBillValidationError(
        f"Tipo inválido '{type(value).__name__}' para campo de timestamp '{field_name}'."
    )


# =====================================================================
# ENUMS REGULATÓRIOS E OPERACIONAIS (RESOLUÇÕES CMN 4.958 E 5.036)
# =====================================================================

class CapitalTier(str, Enum):
    """Nível de elegibilidade de capital regulatório bancário (Resolução CMN nº 4.958)."""

    LEVEL_1_ADDITIONAL = "NIVEL_1"  # Capital Complementar / Additional Tier 1 (AT1) - Perpétua
    LEVEL_2 = "NIVEL_2"             # Capital Nível 2 / Tier 2 - Prazo mínimo de 5 anos


class LossAbsorptionMechanism(str, Enum):
    """Mecanismo contratual de absorção de perdas em caso de disparo do gatilho prudencial."""

    PERMANENT_WRITE_DOWN = "WRITE_DOWN_PERMANENTE"       # Extinção definitiva e irreversível de saldo/PU
    TEMPORARY_WRITE_DOWN = "WRITE_DOWN_TEMPORARIO"       # Baixa contábil temporária com recomposição futura (Write-Up)
    MANDATORY_EQUITY_CONVERSION = "CONVERSAO_ACOES"      # Conversão compulsória em ações ordinárias/patrimoniais


class SubordinatedBillStatus(str, Enum):
    """Status do ciclo de vida da Letra Financeira Subordinada no Balcão NoMe B3."""

    ACTIVE = "ATIVO"                                     # Ativo em circulação com pagamentos normais
    INTEREST_SUSPENDED = "JUROS_SUSPENSOS"               # Cupom suspenso discricionariamente (sem default)
    PARTIALLY_WRITTEN_DOWN = "ABSORVIDO_PARCIAL"         # Saldo reduzido parcialmente por gatilho de perda
    FULLY_WRITTEN_DOWN = "EXTINTO_PERDA_TOTAL"           # Saldo 100% extinto por absorção de perdas
    CONVERTED_TO_EQUITY = "CONVERTIDO_ACOES"             # Saldo 100% convertido em ações do emissor
    MATURED = "VENCIDO"                                  # Título Nível 2 chegado à data de vencimento
    REDEEMED = "RESGATADO"                               # Resgatado antecipadamente via Call Option com aprovação BACEN


class CouponStatus(str, Enum):
    """Estado de liquidação de um cupom de juros agendado."""

    SCHEDULED = "AGENDADO"
    PAID = "PAGO"
    SUSPENDED_DISCRETIONARY = "SUSPENSO_DISCRICIONARIO"
    CANCELLED = "CANCELADO"


class SubordinatedBillIndexer(str, Enum):
    """Indexadores financeiros admitidos em Letras Financeiras no Balcão NoMe."""

    CDI = "CDI"
    PREFIXADO = "PREFIXADO"
    IPCA = "IPCA"


# =====================================================================
# HIERARQUIA DE EXCEÇÕES DE NEGÓCIO E CONFORMIDADE
# =====================================================================

class SubordinatedBillError(Exception):
    """Exceção base para operações com Letras Financeiras Subordinadas."""
    pass


class SubordinatedBillValidationError(SubordinatedBillError):
    """Exceção levantada quando regras cadastrais ou parâmetros de emissão são violados."""
    pass


class LossAbsorptionError(SubordinatedBillError):
    """Exceção levantada quando ocorre inconsistência no mecanismo de absorção de perdas."""
    pass


class WriteUpError(SubordinatedBillError):
    """Exceção levantada quando regras de recomposição de write-down temporário são violadas."""
    pass


class RegulatoryHierarchyBreachError(SubordinatedBillError):
    """Exceção levantada quando a ordem legal de absorção de perdas de capital é desrespeitada."""
    pass


# =====================================================================
# MODELOS DE DADOS E REGISTROS ESCRITURAIS
# =====================================================================

@dataclass
class SubordinatedBillCoupon:
    """Cupom de juros periódico de Letra Financeira Subordinada."""

    coupon_id: str
    due_date: date
    rate_annual_pct: Decimal
    amount: Decimal
    status: CouponStatus = CouponStatus.SCHEDULED
    cancellation_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "coupon_id": self.coupon_id,
            "due_date": self.due_date.isoformat(),
            "rate_annual_pct": str(self.rate_annual_pct),
            "amount": str(self.amount),
            "status": self.status.value,
            "cancellation_reason": self.cancellation_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SubordinatedBillCoupon:
        return cls(
            coupon_id=str(data["coupon_id"]),
            due_date=date.fromisoformat(str(data["due_date"])),
            rate_annual_pct=to_decimal(data["rate_annual_pct"], "rate_annual_pct"),
            amount=round_cents(to_decimal(data["amount"], "amount")),
            status=CouponStatus(str(data.get("status", CouponStatus.SCHEDULED.value))),
            cancellation_reason=data.get("cancellation_reason"),
        )


@dataclass
class LossAbsorptionEvent:
    """Registro de auditoria de absorção de perdas via gatilho prudencial de capital."""

    event_id: str
    timestamp: datetime
    bill_id: str
    issuer_cnpj: str
    mechanism: LossAbsorptionMechanism
    cet1_ratio_trigger: Decimal
    cet1_ratio_detected: Decimal
    pu_before: Decimal
    pu_after: Decimal
    quantity: Decimal
    nominal_loss_absorbed: Decimal
    shares_issued: Decimal = Decimal("0")
    conversion_price: Decimal = Decimal("0.00")
    cash_fraction_refund: Decimal = Decimal("0.00")
    reconstitution_eligible: bool = False
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "bill_id": self.bill_id,
            "issuer_cnpj": self.issuer_cnpj,
            "mechanism": self.mechanism.value,
            "cet1_ratio_trigger": str(self.cet1_ratio_trigger),
            "cet1_ratio_detected": str(self.cet1_ratio_detected),
            "pu_before": str(self.pu_before),
            "pu_after": str(self.pu_after),
            "quantity": str(self.quantity),
            "nominal_loss_absorbed": str(self.nominal_loss_absorbed),
            "shares_issued": str(self.shares_issued),
            "conversion_price": str(self.conversion_price),
            "cash_fraction_refund": str(self.cash_fraction_refund),
            "reconstitution_eligible": self.reconstitution_eligible,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LossAbsorptionEvent:
        return cls(
            event_id=str(data["event_id"]),
            timestamp=parse_datetime(data["timestamp"], "timestamp"),
            bill_id=str(data["bill_id"]),
            issuer_cnpj=str(data["issuer_cnpj"]),
            mechanism=LossAbsorptionMechanism(str(data["mechanism"])),
            cet1_ratio_trigger=to_decimal(data["cet1_ratio_trigger"], "cet1_ratio_trigger"),
            cet1_ratio_detected=to_decimal(data["cet1_ratio_detected"], "cet1_ratio_detected"),
            pu_before=round_unit(to_decimal(data["pu_before"], "pu_before")),
            pu_after=round_unit(to_decimal(data["pu_after"], "pu_after")),
            quantity=to_decimal(data["quantity"], "quantity"),
            nominal_loss_absorbed=round_cents(to_decimal(data["nominal_loss_absorbed"], "nominal_loss_absorbed")),
            shares_issued=to_decimal(data.get("shares_issued", "0"), "shares_issued"),
            conversion_price=round_cents(to_decimal(data.get("conversion_price", "0.00"), "conversion_price")),
            cash_fraction_refund=round_cents(to_decimal(data.get("cash_fraction_refund", "0.00"), "cash_fraction_refund")),
            reconstitution_eligible=bool(data.get("reconstitution_eligible", False)),
            notes=str(data.get("notes", "")),
        )


@dataclass
class WriteUpEvent:
    """Registro de auditoria de recomposição (Write-Up) de Letra Financeira com Write-Down Temporário."""

    event_id: str
    timestamp: datetime
    bill_id: str
    issuer_cnpj: str
    pu_before: Decimal
    pu_after: Decimal
    write_up_value_total: Decimal
    bacen_authorization_protocol: str
    audit_net_profit_used: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "bill_id": self.bill_id,
            "issuer_cnpj": self.issuer_cnpj,
            "pu_before": str(self.pu_before),
            "pu_after": str(self.pu_after),
            "write_up_value_total": str(self.write_up_value_total),
            "bacen_authorization_protocol": self.bacen_authorization_protocol,
            "audit_net_profit_used": str(self.audit_net_profit_used),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WriteUpEvent:
        return cls(
            event_id=str(data["event_id"]),
            timestamp=parse_datetime(data["timestamp"], "timestamp"),
            bill_id=str(data["bill_id"]),
            issuer_cnpj=str(data["issuer_cnpj"]),
            pu_before=round_unit(to_decimal(data["pu_before"], "pu_before")),
            pu_after=round_unit(to_decimal(data["pu_after"], "pu_after")),
            write_up_value_total=round_cents(to_decimal(data["write_up_value_total"], "write_up_value_total")),
            bacen_authorization_protocol=str(data["bacen_authorization_protocol"]),
            audit_net_profit_used=round_cents(to_decimal(data["audit_net_profit_used"], "audit_net_profit_used")),
        )


@dataclass
class SubordinatedFinancialBill:
    """Letra Financeira Subordinada escriturada no Balcão NoMe B3."""

    bill_id: str
    isin: str
    issuer_cnpj: str
    issuer_name: str
    capital_tier: CapitalTier
    absorption_mechanism: LossAbsorptionMechanism
    indexer: SubordinatedBillIndexer
    spread_annual: Decimal
    interest_description: str
    issue_date: date
    nominal_unit_value: Decimal
    quantity: Decimal
    maturity_date: Optional[date] = None
    call_option_date: Optional[date] = None
    regulatory_trigger_ratio: Decimal = REGULATORY_MIN_CET1_TRIGGER
    current_unit_value: Decimal = field(default=Decimal("0.00"))
    original_reference_pu: Decimal = field(default=Decimal("0.00"))
    status: SubordinatedBillStatus = SubordinatedBillStatus.ACTIVE
    dividend_stopper_active: bool = False
    total_loss_absorbed: Decimal = Decimal("0.00")
    equity_shares_converted: Decimal = Decimal("0")
    conversion_price: Optional[Decimal] = None
    interest_cancellable_discretionary: bool = True
    coupons: List[SubordinatedBillCoupon] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Inicializa e assegura coerência inicial dos campos."""
        self.spread_annual = round_rate(to_decimal(self.spread_annual, "spread_annual"))
        self.nominal_unit_value = round_cents(to_decimal(self.nominal_unit_value, "nominal_unit_value"))
        self.quantity = to_decimal(self.quantity, "quantity")
        self.regulatory_trigger_ratio = round_rate(to_decimal(self.regulatory_trigger_ratio, "regulatory_trigger_ratio"))

        if self.current_unit_value == Decimal("0.00") or self.current_unit_value is None:
            self.current_unit_value = round_unit(self.nominal_unit_value)
        else:
            self.current_unit_value = round_unit(to_decimal(self.current_unit_value, "current_unit_value"))

        if self.original_reference_pu == Decimal("0.00") or self.original_reference_pu is None:
            self.original_reference_pu = round_unit(self.nominal_unit_value)
        else:
            self.original_reference_pu = round_unit(to_decimal(self.original_reference_pu, "original_reference_pu"))

        if self.conversion_price is not None:
            self.conversion_price = round_cents(to_decimal(self.conversion_price, "conversion_price"))

    @property
    def is_perpetual(self) -> bool:
        """Indica se o título é de prazo indeterminado (perpétuo)."""
        return self.maturity_date is None

    @property
    def outstanding_balance(self) -> Decimal:
        """Calcula o saldo devedor contábil total: Saldo = Quantidade * PU_Atual."""
        return round_cents(self.quantity * self.current_unit_value)

    def tier2_regulatory_capital_factor(self, as_of_date: date) -> Decimal:
        """
        Calcula o fator redutor de elegibilidade para Capital Nível 2 (Resolução CMN nº 4.958, Art. 20).

        Nos últimos 5 anos de vida útil do instrumento, aplica-se redutor linear de 20% ao ano:
        - Prazo remanescente > 4 anos e <= 5 anos: 80% (fator 0.80)
        - Prazo remanescente > 3 anos e <= 4 anos: 60% (fator 0.60)
        - Prazo remanescente > 2 anos e <= 3 anos: 40% (fator 0.40)
        - Prazo remanescente > 1 ano e <= 2 anos: 20% (fator 0.20)
        - Prazo remanescente <= 1 ano: 0% (fator 0.00)
        - Prazo remanescente > 5 anos: 100% (fator 1.00)

        Para LFs Nível 1 (Perpétuas), o fator é sempre 1.00 (sem amortização).
        """
        if self.capital_tier == CapitalTier.LEVEL_1_ADDITIONAL:
            return Decimal("1.00")

        if self.maturity_date is None:
            raise SubordinatedBillValidationError(
                f"LF Nível 2 {self.bill_id} deve possuir data de vencimento."
            )

        days_remaining = (self.maturity_date - as_of_date).days
        if days_remaining <= 0:
            return Decimal("0.00")

        # Conversão em anos considerando ano médio de 365.25 dias
        years_remaining = Decimal(days_remaining) / Decimal("365.25")

        if years_remaining > Decimal("5.0"):
            return Decimal("1.00")
        elif years_remaining > Decimal("4.0"):
            return Decimal("0.80")
        elif years_remaining > Decimal("3.0"):
            return Decimal("0.60")
        elif years_remaining > Decimal("2.0"):
            return Decimal("0.40")
        elif years_remaining > Decimal("1.0"):
            return Decimal("0.20")
        else:
            return Decimal("0.00")

    def tier2_eligible_capital_amount(self, as_of_date: date) -> Decimal:
        """Retorna o valor efetivo computável no Capital Nível 2 após redutor prudencial."""
        factor = self.tier2_regulatory_capital_factor(as_of_date)
        return round_cents(self.outstanding_balance * factor)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bill_id": self.bill_id,
            "isin": self.isin,
            "issuer_cnpj": self.issuer_cnpj,
            "issuer_name": self.issuer_name,
            "capital_tier": self.capital_tier.value,
            "absorption_mechanism": self.absorption_mechanism.value,
            "indexer": self.indexer.value,
            "spread_annual": str(self.spread_annual),
            "interest_description": self.interest_description,
            "issue_date": self.issue_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat() if self.maturity_date else None,
            "call_option_date": self.call_option_date.isoformat() if self.call_option_date else None,
            "regulatory_trigger_ratio": str(self.regulatory_trigger_ratio),
            "nominal_unit_value": str(self.nominal_unit_value),
            "current_unit_value": str(self.current_unit_value),
            "original_reference_pu": str(self.original_reference_pu),
            "quantity": str(self.quantity),
            "status": self.status.value,
            "dividend_stopper_active": self.dividend_stopper_active,
            "total_loss_absorbed": str(self.total_loss_absorbed),
            "equity_shares_converted": str(self.equity_shares_converted),
            "conversion_price": str(self.conversion_price) if self.conversion_price is not None else None,
            "interest_cancellable_discretionary": self.interest_cancellable_discretionary,
            "coupons": [c.to_dict() for c in self.coupons],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SubordinatedFinancialBill:
        coupons = [SubordinatedBillCoupon.from_dict(c) for c in data.get("coupons", [])]
        return cls(
            bill_id=str(data["bill_id"]),
            isin=str(data["isin"]),
            issuer_cnpj=str(data["issuer_cnpj"]),
            issuer_name=str(data["issuer_name"]),
            capital_tier=CapitalTier(str(data["capital_tier"])),
            absorption_mechanism=LossAbsorptionMechanism(str(data["absorption_mechanism"])),
            indexer=SubordinatedBillIndexer(str(data["indexer"])),
            spread_annual=to_decimal(data["spread_annual"], "spread_annual"),
            interest_description=str(data["interest_description"]),
            issue_date=parse_date(data["issue_date"], "issue_date"),  # type: ignore[arg-type]
            maturity_date=parse_date(data.get("maturity_date"), "maturity_date"),
            call_option_date=parse_date(data.get("call_option_date"), "call_option_date"),
            regulatory_trigger_ratio=to_decimal(data.get("regulatory_trigger_ratio", REGULATORY_MIN_CET1_TRIGGER), "regulatory_trigger_ratio"),
            nominal_unit_value=to_decimal(data["nominal_unit_value"], "nominal_unit_value"),
            current_unit_value=to_decimal(data.get("current_unit_value", data["nominal_unit_value"]), "current_unit_value"),
            original_reference_pu=to_decimal(data.get("original_reference_pu", data["nominal_unit_value"]), "original_reference_pu"),
            quantity=to_decimal(data["quantity"], "quantity"),
            status=SubordinatedBillStatus(str(data.get("status", SubordinatedBillStatus.ACTIVE.value))),
            dividend_stopper_active=bool(data.get("dividend_stopper_active", False)),
            total_loss_absorbed=to_decimal(data.get("total_loss_absorbed", "0.00"), "total_loss_absorbed"),
            equity_shares_converted=to_decimal(data.get("equity_shares_converted", "0"), "equity_shares_converted"),
            conversion_price=to_decimal(data["conversion_price"], "conversion_price") if data.get("conversion_price") is not None else None,
            interest_cancellable_discretionary=bool(data.get("interest_cancellable_discretionary", True)),
            coupons=coupons,
        )


# =====================================================================
# REGISTRO CENTRAL E MOTOR DE ESCRITURAÇÃO E ABSORÇÃO DE PERDAS
# =====================================================================

class SubordinatedFinancialBillsRegistry:
    """
    Motor central de escrituração, liquidação e absorção de perdas de LFs Subordinadas.

    Gerencia o ciclo de vida de ativos no Balcão NoMe B3, garantindo o cumprimento
    das regras prudenciais do CMN e conservação contábil rigorosa.
    """

    def __init__(self) -> None:
        self.bills: Dict[str, SubordinatedFinancialBill] = {}
        self.loss_events: List[LossAbsorptionEvent] = []
        self.write_up_events: List[WriteUpEvent] = []

    def register_bill(self, bill: SubordinatedFinancialBill) -> SubordinatedFinancialBill:
        """
        Registra e valida uma Letra Financeira Subordinada no Balcão NoMe.

        Aplica validações de elegibilidade prudencial:
        - Nível 1: DEVE ser perpétua (sem data de vencimento) e permitir suspensão discricionária de juros.
        - Nível 2: DEVE ter prazo mínimo de 5 anos (1825 dias) entre emissão e vencimento.
        - Call Option (se houver): Mínimo 5 anos da data de emissão.
        - VNU Mínimo: R$ 50.000,00.
        - Gatilho CET1: Não pode ser inferior ao piso regulatório de 5,125%.
        """
        if bill.bill_id in self.bills:
            raise SubordinatedBillValidationError(
                f"Letra Financeira com ID '{bill.bill_id}' já se encontra registrada no NoMe."
            )

        if not bill.isin.startswith("BR") or len(bill.isin) != 12:
            raise SubordinatedBillValidationError(
                f"Código ISIN '{bill.isin}' inválido para instrumento nacional B3."
            )

        if len(bill.issuer_cnpj) != 14 or not bill.issuer_cnpj.isdigit():
            raise SubordinatedBillValidationError(
                f"CNPJ do emissor '{bill.issuer_cnpj}' inválido (deve conter 14 dígitos)."
            )

        if bill.nominal_unit_value < MINIMUM_NOMINAL_VALUE_CAPITAL:
            raise SubordinatedBillValidationError(
                f"Valor Nominal Unitário R$ {bill.nominal_unit_value} inferior ao mínimo legal de R$ {MINIMUM_NOMINAL_VALUE_CAPITAL}."
            )

        if bill.quantity <= Decimal("0"):
            raise SubordinatedBillValidationError(
                f"Quantidade escriturada deve ser estritamente positiva: {bill.quantity}"
            )

        if bill.regulatory_trigger_ratio < REGULATORY_MIN_CET1_TRIGGER:
            raise SubordinatedBillValidationError(
                f"Gatilho regulatório de Capital Principal {bill.regulatory_trigger_ratio} inferior ao piso legal de {REGULATORY_MIN_CET1_TRIGGER} (5.125%)."
            )

        # Validações específicas por nível de capital
        if bill.capital_tier == CapitalTier.LEVEL_1_ADDITIONAL:
            if not bill.is_perpetual:
                raise SubordinatedBillValidationError(
                    f"LF Nível 1 {bill.bill_id} deve ser perpétua (maturity_date deve ser None)."
                )
            if not bill.interest_cancellable_discretionary:
                raise SubordinatedBillValidationError(
                    f"LF Nível 1 {bill.bill_id} deve permitir cancelamento discricionário de remuneração (Res. CMN 4.958, Art. 16)."
                )

        elif bill.capital_tier == CapitalTier.LEVEL_2:
            if bill.maturity_date is None:
                raise SubordinatedBillValidationError(
                    f"LF Nível 2 {bill.bill_id} deve possuir data de vencimento expressa."
                )
            tenor_days = (bill.maturity_date - bill.issue_date).days
            if tenor_days < MINIMUM_TIER2_TERM_DAYS:
                raise SubordinatedBillValidationError(
                    f"LF Nível 2 {bill.bill_id} com prazo de {tenor_days} dias inferior ao mínimo legal de {MINIMUM_TIER2_TERM_DAYS} dias (5 anos)."
                )

        # Validação de Call Option
        if bill.call_option_date is not None:
            call_days = (bill.call_option_date - bill.issue_date).days
            if call_days < MINIMUM_CALL_OPTION_DAYS:
                raise SubordinatedBillValidationError(
                    f"Opção de compra (Call) da LF {bill.bill_id} em {call_days} dias é inferior ao interstício mínimo de 5 anos ({MINIMUM_CALL_OPTION_DAYS} dias)."
                )

        # Validação de mecanismo de conversão
        if bill.absorption_mechanism == LossAbsorptionMechanism.MANDATORY_EQUITY_CONVERSION:
            if bill.conversion_price is None or bill.conversion_price <= Decimal("0"):
                raise SubordinatedBillValidationError(
                    f"LF com conversão em ações {bill.bill_id} exige preço de conversão positivo."
                )

        self.bills[bill.bill_id] = bill
        return bill

    def get_bill(self, bill_id: str) -> SubordinatedFinancialBill:
        """Recupera LF pelo ID ou levanta SubordinatedBillValidationError."""
        if bill_id not in self.bills:
            raise SubordinatedBillValidationError(f"LF '{bill_id}' não localizada no Balcão NoMe.")
        return self.bills[bill_id]

    def get_bills_by_issuer(self, issuer_cnpj: str) -> List[SubordinatedFinancialBill]:
        """Retorna todas as LFs emitidas por uma instituição financeira."""
        return [b for b in self.bills.values() if b.issuer_cnpj == issuer_cnpj]

    def suspend_coupon_discretionary(
        self,
        bill_id: str,
        coupon_id: Optional[str] = None,
        reason: str = "Preservação prudencial de liquidez e capital",
    ) -> SubordinatedFinancialBill:
        """
        Executa a suspensão discricionária de pagamento de juros/cupom da LF.

        Regra de Negócio Crucial (Resoluções CMN nº 4.958 e 5.036):
        - A suspensão de cupom em LF Nível 1 é uma faculdade discricionária do emissor.
        - NÃO caracteriza evento de inadimplência (default). O status do ativo passa a
          `INTEREST_SUSPENDED`, e NÃO a qualquer estado de default.
        - Não gera juros de mora ou encargos compensatórios (remuneração não cumulativa).
        - Ativa obrigatoriamente a trava de dividendos (Dividend Stopper): a instituição
          fica impedida de remunerar acionistas ordinários enquanto perdurar a suspensão.
        """
        bill = self.get_bill(bill_id)

        if not bill.interest_cancellable_discretionary:
            raise SubordinatedBillValidationError(
                f"LF {bill_id} não possui cláusula contratual de suspensão discricionária de cupom."
            )

        bill.dividend_stopper_active = True
        bill.status = SubordinatedBillStatus.INTEREST_SUSPENDED

        if coupon_id is not None:
            found = False
            for c in bill.coupons:
                if c.coupon_id == coupon_id:
                    c.status = CouponStatus.SUSPENDED_DISCRETIONARY
                    c.cancellation_reason = reason
                    found = True
                    break
            if not found:
                raise SubordinatedBillValidationError(
                    f"Cupom '{coupon_id}' não encontrado na LF {bill_id}."
                )
        else:
            # Suspende todos os cupons agendados
            for c in bill.coupons:
                if c.status == CouponStatus.SCHEDULED:
                    c.status = CouponStatus.SUSPENDED_DISCRETIONARY
                    c.cancellation_reason = reason

        return bill

    def resume_coupon_payments(self, bill_id: str) -> SubordinatedFinancialBill:
        """
        Retoma o fluxo regular de pagamento de juros da LF e desativa o Dividend Stopper.
        """
        bill = self.get_bill(bill_id)
        if bill.status == SubordinatedBillStatus.INTEREST_SUSPENDED:
            # Se já sofreu write-down parcial, restaura para ABSORVIDO_PARCIAL, senão ATIVO
            if bill.total_loss_absorbed > Decimal("0") and bill.current_unit_value > Decimal("0"):
                bill.status = SubordinatedBillStatus.PARTIALLY_WRITTEN_DOWN
            else:
                bill.status = SubordinatedBillStatus.ACTIVE

        bill.dividend_stopper_active = False
        return bill

    def evaluate_and_trigger_loss_absorption(
        self,
        issuer_cnpj: str,
        current_cet1_ratio: Decimal,
        required_cet1_target: Decimal = REGULATORY_MIN_CET1_TRIGGER,
        rwa: Optional[Decimal] = None,
        loss_amount_to_absorb: Optional[Decimal] = None,
        as_of_date: Optional[date] = None,
        is_ponv: bool = False,
    ) -> List[LossAbsorptionEvent]:
        """
        Avalia o Capital Principal da instituição emissora e dispara determinísticamente
        a absorção de perdas em LFs Subordinadas.

        Hierarquia Obrigatória de Absorção (Resolução CMN nº 4.958):
        1. As LFs Nível 1 (AT1) absorvem as perdas PRIMEIRO.
           - Rateio pro-rata entre instrumentos de Nível 1 caso haja múltiplos títulos.
        2. As LFs Nível 2 (Tier 2) SOMENTE absorvem perdas se:
           - O saldo de Nível 1 estiver TOTALMENTE exaurido (saldo == 0), E
           - Houver decretação formal de Não Viabilidade (Point of Non-Viability - PonV)
             ou intervenção pelo Banco Central (`is_ponv=True`).
           - Se for tentado impor perdas a Nível 2 havendo capacidade remanescente em Nível 1,
             lança `RegulatoryHierarchyBreachError`.
        """
        current_cet1 = round_rate(to_decimal(current_cet1_ratio, "current_cet1_ratio"))
        trigger_floor = round_rate(to_decimal(required_cet1_target, "required_cet1_target"))

        # Determina se houve disparo do gatilho
        trigger_fired = (current_cet1 < trigger_floor) or is_ponv
        if not trigger_fired:
            return []

        # Determina o montante de perda que precisa ser absorvido
        if loss_amount_to_absorb is not None:
            needed_loss = round_cents(to_decimal(loss_amount_to_absorb, "loss_amount_to_absorb"))
        elif rwa is not None:
            rwa_val = round_cents(to_decimal(rwa, "rwa"))
            deficit_pct = max(Decimal("0.00"), trigger_floor - current_cet1)
            needed_loss = round_cents(rwa_val * deficit_pct)
        else:
            raise LossAbsorptionError(
                "Para disparar absorção de perdas, informe 'loss_amount_to_absorb' ou 'rwa'."
            )

        if needed_loss <= Decimal("0.00"):
            return []

        # Localiza instrumentos ativos do emissor agrupados por nível de capital
        issuer_bills = self.get_bills_by_issuer(issuer_cnpj)
        tier1_bills = [
            b for b in issuer_bills
            if b.capital_tier == CapitalTier.LEVEL_1_ADDITIONAL
            and b.status in (SubordinatedBillStatus.ACTIVE, SubordinatedBillStatus.INTEREST_SUSPENDED, SubordinatedBillStatus.PARTIALLY_WRITTEN_DOWN)
            and b.outstanding_balance > Decimal("0.00")
        ]
        tier2_bills = [
            b for b in issuer_bills
            if b.capital_tier == CapitalTier.LEVEL_2
            and b.status in (SubordinatedBillStatus.ACTIVE, SubordinatedBillStatus.PARTIALLY_WRITTEN_DOWN)
            and b.outstanding_balance > Decimal("0.00")
        ]

        loss_events: List[LossAbsorptionEvent] = []
        remaining_loss = needed_loss

        # FASE 1: Absorção pelo Capital Nível 1 (AT1)
        if tier1_bills and remaining_loss > Decimal("0.00"):
            total_tier1_balance = sum((b.outstanding_balance for b in tier1_bills), Decimal("0.00"))

            # Se a perda necessária supera todo o saldo de Nível 1, consome 100% de Nível 1
            if remaining_loss >= total_tier1_balance:
                for b in tier1_bills:
                    alloc_loss = b.outstanding_balance
                    event = self._apply_loss_to_bill(
                        bill=b,
                        loss_to_apply=alloc_loss,
                        current_cet1=current_cet1,
                        trigger_floor=trigger_floor,
                    )
                    loss_events.append(event)
                remaining_loss = round_cents(remaining_loss - total_tier1_balance)
            else:
                # Rateio proporcional (pro-rata) centavo a centavo com ajuste residual no último
                allocated_so_far = Decimal("0.00")
                for idx, b in enumerate(tier1_bills):
                    if idx == len(tier1_bills) - 1:
                        alloc_loss = round_cents(remaining_loss - allocated_so_far)
                    else:
                        weight = b.outstanding_balance / total_tier1_balance
                        alloc_loss = round_cents(remaining_loss * weight)
                        allocated_so_far += alloc_loss

                    # Garante que não ultrapasse o saldo do título
                    alloc_loss = min(alloc_loss, b.outstanding_balance)

                    event = self._apply_loss_to_bill(
                        bill=b,
                        loss_to_apply=alloc_loss,
                        current_cet1=current_cet1,
                        trigger_floor=trigger_floor,
                    )
                    loss_events.append(event)
                remaining_loss = Decimal("0.00")

        # FASE 2: Avaliação de absorção pelo Capital Nível 2 (Tier 2)
        if remaining_loss > Decimal("0.00"):
            # Verifica se ainda resta saldo em Nível 1: se restar, VIOLAÇÃO DA HIERARQUIA
            remaining_tier1_balance = sum(
                (b.outstanding_balance for b in issuer_bills if b.capital_tier == CapitalTier.LEVEL_1_ADDITIONAL),
                Decimal("0.00")
            )
            if remaining_tier1_balance > Decimal("0.00"):
                raise RegulatoryHierarchyBreachError(
                    f"Violação da hierarquia de capital: LFs Nível 1 possuem saldo remanescente de R$ {remaining_tier1_balance}. "
                    f"Instrumentos Nível 2 não podem absorver perdas antes da exaustão completa de Nível 1."
                )

            # LFs Nível 2 só absorvem perda em caso de PonV / Intervenção
            if not is_ponv:
                # Sem PonV formal, Nível 2 não é atingido por gatilho exclusivo de CET1 < 5.125%
                return loss_events

            if tier2_bills:
                total_tier2_balance = sum((b.outstanding_balance for b in tier2_bills), Decimal("0.00"))
                allocated_so_far = Decimal("0.00")
                for idx, b in enumerate(tier2_bills):
                    if remaining_loss >= total_tier2_balance:
                        alloc_loss = b.outstanding_balance
                    else:
                        if idx == len(tier2_bills) - 1:
                            alloc_loss = round_cents(remaining_loss - allocated_so_far)
                        else:
                            weight = b.outstanding_balance / total_tier2_balance
                            alloc_loss = round_cents(remaining_loss * weight)
                            allocated_so_far += alloc_loss

                    alloc_loss = min(alloc_loss, b.outstanding_balance)
                    event = self._apply_loss_to_bill(
                        bill=b,
                        loss_to_apply=alloc_loss,
                        current_cet1=current_cet1,
                        trigger_floor=trigger_floor,
                    )
                    loss_events.append(event)

        self.loss_events.extend(loss_events)
        return loss_events

    def _apply_loss_to_bill(
        self,
        bill: SubordinatedFinancialBill,
        loss_to_apply: Decimal,
        current_cet1: Decimal,
        trigger_floor: Decimal,
    ) -> LossAbsorptionEvent:
        """Aplica a perda alocada ao instrumento conforme seu mecanismo contratual."""
        pu_before = bill.current_unit_value
        loss_allocated = round_cents(loss_to_apply)
        shares_issued = Decimal("0")
        conv_price = Decimal("0.00")
        cash_refund = Decimal("0.00")

        if bill.absorption_mechanism in (
            LossAbsorptionMechanism.PERMANENT_WRITE_DOWN,
            LossAbsorptionMechanism.TEMPORARY_WRITE_DOWN,
        ):
            # Redução de PU proporcional à perda
            if loss_allocated >= bill.outstanding_balance:
                pu_after = Decimal("0.00000000")
                actual_loss = bill.outstanding_balance
                bill.status = SubordinatedBillStatus.FULLY_WRITTEN_DOWN
            else:
                pu_loss_per_unit = round_unit(loss_allocated / bill.quantity)
                pu_after = max(Decimal("0.00000000"), round_unit(pu_before - pu_loss_per_unit))
                actual_loss = round_cents(bill.quantity * (pu_before - pu_after))
                bill.status = SubordinatedBillStatus.PARTIALLY_WRITTEN_DOWN

            bill.current_unit_value = pu_after
            bill.total_loss_absorbed = round_cents(bill.total_loss_absorbed + actual_loss)

            event = LossAbsorptionEvent(
                event_id=f"ABS-EVT-{bill.bill_id}-{len(self.loss_events) + 1:04d}",
                timestamp=datetime.now(timezone.utc),
                bill_id=bill.bill_id,
                issuer_cnpj=bill.issuer_cnpj,
                mechanism=bill.absorption_mechanism,
                cet1_ratio_trigger=trigger_floor,
                cet1_ratio_detected=current_cet1,
                pu_before=pu_before,
                pu_after=pu_after,
                quantity=bill.quantity,
                nominal_loss_absorbed=actual_loss,
                reconstitution_eligible=(bill.absorption_mechanism == LossAbsorptionMechanism.TEMPORARY_WRITE_DOWN),
                notes=f"Write-Down executado. Saldo anterior: R$ {pu_before * bill.quantity:.2f}, Saldo novo: R$ {pu_after * bill.quantity:.2f}.",
            )
            return event

        elif bill.absorption_mechanism == LossAbsorptionMechanism.MANDATORY_EQUITY_CONVERSION:
            if bill.conversion_price is None or bill.conversion_price <= Decimal("0"):
                raise LossAbsorptionError(
                    f"LF {bill.bill_id} exige preço de conversão positivo para conversão mandatória em ações."
                )

            conv_price = bill.conversion_price
            # Quantidade de ações: divisão inteira do valor convertido pelo preço de conversão
            shares_issued = Decimal(int(loss_allocated // conv_price))
            value_in_shares = round_cents(shares_issued * conv_price)
            cash_refund = round_cents(loss_allocated - value_in_shares)

            # Reduz o PU pela perda total alocada
            if loss_allocated >= bill.outstanding_balance:
                pu_after = Decimal("0.00000000")
                actual_loss = bill.outstanding_balance
                bill.status = SubordinatedBillStatus.CONVERTED_TO_EQUITY
            else:
                pu_loss_per_unit = round_unit(loss_allocated / bill.quantity)
                pu_after = max(Decimal("0.00000000"), round_unit(pu_before - pu_loss_per_unit))
                actual_loss = round_cents(bill.quantity * (pu_before - pu_after))
                bill.status = SubordinatedBillStatus.PARTIALLY_WRITTEN_DOWN

            bill.current_unit_value = pu_after
            bill.total_loss_absorbed = round_cents(bill.total_loss_absorbed + actual_loss)
            bill.equity_shares_converted = bill.equity_shares_converted + shares_issued

            event = LossAbsorptionEvent(
                event_id=f"ABS-EVT-{bill.bill_id}-{len(self.loss_events) + 1:04d}",
                timestamp=datetime.now(timezone.utc),
                bill_id=bill.bill_id,
                issuer_cnpj=bill.issuer_cnpj,
                mechanism=bill.absorption_mechanism,
                cet1_ratio_trigger=trigger_floor,
                cet1_ratio_detected=current_cet1,
                pu_before=pu_before,
                pu_after=pu_after,
                quantity=bill.quantity,
                nominal_loss_absorbed=actual_loss,
                shares_issued=shares_issued,
                conversion_price=conv_price,
                cash_fraction_refund=cash_refund,
                reconstitution_eligible=False,
                notes=f"Conversão Mandatória em Ações. {shares_issued} ações emitidas a R$ {conv_price} cada. Sobra em caixa: R$ {cash_refund}.",
            )
            return event

        else:
            raise LossAbsorptionError(f"Mecanismo desconhecido: {bill.absorption_mechanism}")

    def execute_temporary_write_up(
        self,
        bill_id: str,
        write_up_amount_per_unit: Decimal,
        bacen_authorization_protocol: str,
        audit_net_profit_available: Decimal,
    ) -> WriteUpEvent:
        """
        Executa a recomposição contábil (Write-Up) de uma LF com Write-Down Temporário.

        Critérios de Elegibilidade (Resolução CMN nº 4.958, Art. 17):
        1. O instrumento DEVE possuir mecanismo `TEMPORARY_WRITE_DOWN`.
        2. É necessária anuência / autorização prévia expressa do Banco Central do Brasil.
        3. A instituição deve possuir lucros líquidos auditados suficientes para absorver o write-up.
        4. Teto de Recomposição: O PU resultante não pode ultrapassar o valor nominal de referência original
           (`original_reference_pu`).
        """
        bill = self.get_bill(bill_id)

        if bill.absorption_mechanism != LossAbsorptionMechanism.TEMPORARY_WRITE_DOWN:
            raise WriteUpError(
                f"LF {bill_id} possui mecanismo '{bill.absorption_mechanism.value}' que veda recomposição contábil (Write-Up permitido apenas para WRITE_DOWN_TEMPORARIO)."
            )

        if not bacen_authorization_protocol or not bacen_authorization_protocol.strip():
            raise WriteUpError(
                "Write-up exige protocolo formal de autorização do Banco Central do Brasil (BACEN)."
            )

        delta_pu = round_unit(to_decimal(write_up_amount_per_unit, "write_up_amount_per_unit"))
        if delta_pu <= Decimal("0.00000000"):
            raise WriteUpError(f"Valor unitário de recomposição deve ser positivo: {delta_pu}")

        pu_before = bill.current_unit_value
        target_pu = round_unit(pu_before + delta_pu)

        # Invariante de Teto: PU nunca excede o PU de referência original
        if target_pu > bill.original_reference_pu:
            raise WriteUpError(
                f"Recomposição violaria o teto nominal original: PU resultante R$ {target_pu} > R$ {bill.original_reference_pu}."
            )

        total_write_up_val = round_cents(delta_pu * bill.quantity)
        profit_available = round_cents(to_decimal(audit_net_profit_available, "audit_net_profit_available"))

        if total_write_up_val > profit_available:
            raise WriteUpError(
                f"Lucro líquido auditado disponível de R$ {profit_available} insuficiente para cobrir o write-up de R$ {total_write_up_val}."
            )

        pu_after = target_pu
        bill.current_unit_value = pu_after

        # Atualiza status
        if pu_after >= bill.original_reference_pu:
            bill.status = SubordinatedBillStatus.ACTIVE
            bill.total_loss_absorbed = Decimal("0.00")
        else:
            bill.status = SubordinatedBillStatus.PARTIALLY_WRITTEN_DOWN
            loss_rem = round_cents((bill.original_reference_pu - pu_after) * bill.quantity)
            bill.total_loss_absorbed = loss_rem

        event = WriteUpEvent(
            event_id=f"WUP-EVT-{bill.bill_id}-{len(self.write_up_events) + 1:04d}",
            timestamp=datetime.now(timezone.utc),
            bill_id=bill.bill_id,
            issuer_cnpj=bill.issuer_cnpj,
            pu_before=pu_before,
            pu_after=pu_after,
            write_up_value_total=total_write_up_val,
            bacen_authorization_protocol=bacen_authorization_protocol.strip(),
            audit_net_profit_used=total_write_up_val,
        )
        self.write_up_events.append(event)
        return event

    def verify_all_invariants(self) -> Dict[str, Any]:
        """
        Executa verificação formal de todas as invariantes matemáticas e regulatórias do sistema.

        Retorna relatório de conformidade auditável.
        """
        violations: List[str] = []

        for bill_id, bill in self.bills.items():
            # 1. Identidade de Saldo Devedor
            expected_bal = round_cents(bill.quantity * bill.current_unit_value)
            if bill.outstanding_balance != expected_bal:
                violations.append(
                    f"LF {bill_id}: Quebra de identidade de saldo: {bill.outstanding_balance} != {expected_bal}"
                )

            # 2. Teto de PU
            if bill.current_unit_value > bill.original_reference_pu:
                violations.append(
                    f"LF {bill_id}: PU atual R$ {bill.current_unit_value} superior ao PU original R$ {bill.original_reference_pu}"
                )

            # 3. Sanidade de Perpétua em Nível 1
            if bill.capital_tier == CapitalTier.LEVEL_1_ADDITIONAL:
                if not bill.is_perpetual:
                    violations.append(f"LF Nível 1 {bill_id} não é perpétua.")

            # 4. Sanidade de Prazo em Nível 2
            if bill.capital_tier == CapitalTier.LEVEL_2:
                if bill.maturity_date is None:
                    violations.append(f"LF Nível 2 {bill_id} sem data de vencimento.")
                else:
                    days = (bill.maturity_date - bill.issue_date).days
                    if days < MINIMUM_TIER2_TERM_DAYS:
                        violations.append(f"LF Nível 2 {bill_id} com prazo {days} dias < 1825 dias.")

            # 5. Suspensão de cupom não é inadimplência
            if bill.status == SubordinatedBillStatus.INTEREST_SUSPENDED:
                if not bill.dividend_stopper_active:
                    violations.append(f"LF {bill_id}: Cupom suspenso sem Dividend Stopper ativado.")

        # 6. Verificação de Conservação nos Eventos de Perda
        for evt in self.loss_events:
            calc_loss = round_cents(evt.quantity * (evt.pu_before - evt.pu_after))
            if evt.nominal_loss_absorbed != calc_loss:
                violations.append(
                    f"Evento {evt.event_id}: Perda registrada {evt.nominal_loss_absorbed} != calculada {calc_loss}"
                )
            if evt.mechanism == LossAbsorptionMechanism.MANDATORY_EQUITY_CONVERSION:
                calc_total = round_cents((evt.shares_issued * evt.conversion_price) + evt.cash_fraction_refund)
                if evt.nominal_loss_absorbed != calc_total:
                    violations.append(
                        f"Evento {evt.event_id}: Conservação de conversão violada: {evt.nominal_loss_absorbed} != {calc_total}"
                    )

        # 7. Verificação dos Eventos de Write-Up
        for wup in self.write_up_events:
            bill = self.bills.get(wup.bill_id)
            if bill:
                calc_val = round_cents((wup.pu_after - wup.pu_before) * bill.quantity)
                if wup.write_up_value_total != calc_val:
                    violations.append(
                        f"WriteUp {wup.event_id}: Valor registrado {wup.write_up_value_total} != calculado {calc_val}"
                    )

        return {
            "all_invariants_hold": len(violations) == 0,
            "violations_count": len(violations),
            "violations": violations,
            "total_bills": len(self.bills),
            "total_loss_events": len(self.loss_events),
            "total_write_up_events": len(self.write_up_events),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "flow_code": "FL-073",
            "system": "B3_BALCAO_NOME_SUBORDINATED_FINANCIAL_BILLS",
            "regulatory_framework": [
                "RESOLUCAO_CMN_4958_2021",
                "RESOLUCAO_CMN_5036_2022",
                "LEI_12249_2010",
                "BASEL_III_STANDARDS",
            ],
            "bills": [b.to_dict() for b in self.bills.values()],
            "loss_events": [e.to_dict() for e in self.loss_events],
            "write_up_events": [w.to_dict() for w in self.write_up_events],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SubordinatedFinancialBillsRegistry:
        registry = cls()
        for b_data in data.get("bills", []):
            bill = SubordinatedFinancialBill.from_dict(b_data)
            registry.bills[bill.bill_id] = bill

        for e_data in data.get("loss_events", []):
            evt = LossAbsorptionEvent.from_dict(e_data)
            registry.loss_events.append(evt)

        for w_data in data.get("write_up_events", []):
            wup = WriteUpEvent.from_dict(w_data)
            registry.write_up_events.append(wup)

        return registry

    @classmethod
    def load_from_fixture(cls, path: Union[str, Path]) -> SubordinatedFinancialBillsRegistry:
        """Carrega e instancia o registro a partir de fixture JSON."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Arquivo de fixture não encontrado: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
