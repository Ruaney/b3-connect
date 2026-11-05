"""Módulo de Fundos Estruturados — FIDC e FII no Balcão NoMe B3 (FL-077).

Implementa as regras de negócio, modelagem matemática e invariantes formais para:
- Fundos de Investimento em Direitos Creditórios (FIDC - Resolução CVM nº 175, Anexo Normativo II).
- Fundos de Investimento Imobiliário (FII - Resolução CVM nº 175, Anexo Normativo III e Lei nº 8.668/1993).
- Escrituração, custódia e liquidação de cotas no Balcão NoMe B3 / Central Depositária B3 (CSD).

Regras de Negócio e Invariantes Formais:
1. Estrutura de Classes e Subclasses de Cotas FIDC:
   - Cotas Seniores: Prioridade absoluta de amortização, resgate e remuneração;
   - Cotas Mezanino (Subordinadas Mezanino): Subordinadas à Sênior, mas seniores à Júnior;
   - Cotas Subordinadas Júnior: Primeira perda (first-loss piece) e remuneração residual.
2. Invariante de Subordinação Estrita (Water-Tight Priority):
   - A cota Subordinada Júnior absorve 100% das perdas e inadimplência da carteira até
     zerar seu patrimônio líquido antes que a cota Mezanino sofra qualquer baixa;
   - A cota Mezanino absorve perdas subsequentes antes que a cota Sênior seja atingida;
   - No Waterfall de pagamentos, o serviço da Sênior é satisfeito integralmente antes
     de qualquer distribuição à Mezanino, e esta antes da Júnior.
3. Invariante de Rendimentos Obrigatórios FII (Lei nº 8.668/1993, Art. 10 § único):
   - Distribuição mínima mandatória de 95% do lucro líquido apurado em regime de caixa
     no balancete semestral (com antecipações mensais praticadas no mercado B3).
4. Invariante de Conservação Patrimonial de Cotas:
   - FIDC: PL_Total == PL_Senior + PL_Mezanino + PL_Junior == Ativos_Totais - Passivo_Exigivel;
   - FII: PL_FII == Quantidade_Cotas * Valor_Cota == Ativos_Totais - Passivo_Exigivel.
5. Invariante de Conservação do Fluxo de Caixa (Payment Waterfall):
   - Caixa_Inicial + Recebimentos == Despesas_Operacionais + Servico_Senior +
     Servico_Mezanino + Distribuicao_Junior + Caixa_Final.

100% Python Standard Library (dataclasses, Decimal, datetime, enum, json, pathlib).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


# =====================================================================
# CONSTANTES DE PRECISÃO NUMÉRICA E ARREDONDAMENTO B3 / CETIP
# =====================================================================

QUANTIZE_MONEY = Decimal("0.01")
QUANTIZE_UNIT = Decimal("0.00000001")
QUANTIZE_RATE = Decimal("0.00000001")
QUANTIZE_QTY = Decimal("0.00000001")

LEI_8668_MIN_PROFIT_DISTRIBUTION_PCT = Decimal("0.95")


def round_cents(val: Decimal) -> Decimal:
    """Arredonda valor financeiro para 2 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_MONEY, rounding=ROUND_HALF_UP)


def round_unit(val: Decimal) -> Decimal:
    """Arredonda Valor Patrimonial Unitário da cota para 8 casas decimais."""
    return val.quantize(QUANTIZE_UNIT, rounding=ROUND_HALF_UP)


def round_rate(val: Decimal) -> Decimal:
    """Arredonda taxas percentuais para 8 casas decimais."""
    return val.quantize(QUANTIZE_RATE, rounding=ROUND_HALF_UP)


def to_decimal(value: Any, field_name: str = "campo") -> Decimal:
    """Converte valor para Decimal de forma auditável e estrita."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise StructuredFundValidationError(f"Valor nulo não permitido para '{field_name}'.")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as err:
        raise StructuredFundValidationError(
            f"Valor inválido '{value}' para '{field_name}': {err}"
        ) from err


def to_date(value: Any, field_name: str = "data") -> date:
    """Converte string ISO (YYYY-MM-DD) ou objeto date de forma auditável."""
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as err:
            raise StructuredFundValidationError(
                f"Formato de data inválido para '{field_name}': {value}. Esperado YYYY-MM-DD."
            ) from err
    raise StructuredFundValidationError(f"Tipo inválido para '{field_name}': {type(value)}")


# =====================================================================
# EXCEÇÕES DE DOMÍNIO
# =====================================================================

class StructuredFundError(Exception):
    """Exceção base para operações de fundos estruturados (FIDC e FII)."""
    pass


class StructuredFundValidationError(StructuredFundError):
    """Violação de regras de validação ou consistência cadastral/estrutural."""
    pass


class FidcSubordinationBreachError(StructuredFundError):
    """Violação da prioridade estrita de subordinação (Water-Tight Priority) em FIDC."""
    pass


class FiiRegulatoryDistributionError(StructuredFundError):
    """Violação da obrigação legal de distribuição mínima de 95% do lucro caixa (Lei 8.668/93)."""
    pass


class StructuredFundInvariantError(StructuredFundError):
    """Falha em uma das invariantes formais de conservação matemática da B3."""
    pass


# =====================================================================
# ENUMS DE DOMÍNIO (RESOLUÇÃO CVM 175)
# =====================================================================

class FundCategory(str, Enum):
    """Categoria do fundo estruturado no Balcão NoMe B3."""
    FIDC = "FIDC"
    FII = "FII"


class FidcQuotaClass(str, Enum):
    """Classe de cota de FIDC segundo a hierarquia de subordinação (CVM 175 Anexo II)."""
    SENIOR = "SENIOR"
    MEZZANINE = "MEZANINO"
    SUBORDINATED_JUNIOR = "SUBORDINADA_JUNIOR"

    @property
    def seniority_level(self) -> int:
        """Nível numérico de senioridade: 1=Sênior, 2=Mezanino, 3=Subordinada Júnior."""
        if self == FidcQuotaClass.SENIOR:
            return 1
        if self == FidcQuotaClass.MEZZANINE:
            return 2
        return 3


class FiiQuotaClass(str, Enum):
    """Classes de cotas admitidas para FII (CVM 175 Anexo III)."""
    UNICA = "UNICA"
    ORDINARIA = "ORDINARIA"
    PREFERENCIAL = "PREFERENCIAL"


class ReceivableType(str, Enum):
    """Natureza dos direitos creditórios elegíveis para carteiras de FIDC."""
    TRADE_DUPLICATA = "DUPLICATA_MERCANTIL"
    SERVICE_DUPLICATA = "DUPLICATA_SERVICO"
    BANK_CREDIT_NOTE_CCB = "CCB_FINANCEIRA"
    CARD_RECEIVABLE = "RECEBIVEL_CARTAO"
    AGRO_COMMODITY_NOTE = "RECEBIVEL_AGRONEGOCIO"


class ReceivableStatus(str, Enum):
    """Estado operacional e de adimplência do recebível no lastro do FIDC."""
    PERFORMING = "ADIMPLENTE"
    PAST_DUE = "EM_ATRASO"
    DEFAULTED = "INADIMPLENTE"
    WRITTEN_OFF = "BAIXADO_PREJUIZO"
    SETTLED = "LIQUIDADO"


class FiiAssetType(str, Enum):
    """Classificação dos ativos imobiliários componentes da carteira do FII."""
    PHYSICAL_PROPERTY = "IMOVEL_FISICO"
    CRI_RECEIVABLE = "CRI"
    LCI_BILL = "LCI"
    FII_QUOTAS = "COTAS_FII"
    LIQUIDITY_FIXED_INCOME = "RENDA_FIXA_LIQUIDEZ"


class DistributionFrequency(str, Enum):
    """Periodicidade da apuração e distribuição de proventos do FII."""
    MONTHLY = "MENSAL"
    SEMIANNUAL = "SEMESTRAL"


class SettlementCycle(str, Enum):
    """Ciclo de liquidação financeira no Balcão NoMe B3."""
    D_PLUS_0 = "D+0"
    D_PLUS_1 = "D+1"
    D_PLUS_2 = "D+2"


# =====================================================================
# MODELOS DE DADOS - FIDC (ANEXO NORMATIVO II - CVM 175)
# =====================================================================

@dataclass
class CreditReceivable:
    """Direito creditório integrante do lastro de FIDC escriturado na B3."""
    receivable_id: str
    debtor_name: str
    debtor_cnpj_cpf: str
    receivable_type: ReceivableType
    original_face_value: Decimal
    current_outstanding_balance: Decimal
    acquisition_discount_rate_annual: Decimal
    issue_date: date
    due_date: date
    status: ReceivableStatus = ReceivableStatus.PERFORMING
    days_past_due: int = 0
    pld_provision_amount: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.original_face_value = round_cents(to_decimal(self.original_face_value, "original_face_value"))
        self.current_outstanding_balance = round_cents(to_decimal(self.current_outstanding_balance, "current_outstanding_balance"))
        self.acquisition_discount_rate_annual = round_rate(to_decimal(self.acquisition_discount_rate_annual, "acquisition_discount_rate_annual"))
        self.issue_date = to_date(self.issue_date, "issue_date")
        self.due_date = to_date(self.due_date, "due_date")
        self.pld_provision_amount = round_cents(to_decimal(self.pld_provision_amount, "pld_provision_amount"))

        if self.original_face_value <= Decimal("0.00"):
            raise StructuredFundValidationError(f"Valor nominal deve ser positivo: {self.original_face_value}")
        if self.current_outstanding_balance < Decimal("0.00"):
            raise StructuredFundValidationError(f"Saldo devedor não pode ser negativo: {self.current_outstanding_balance}")
        if self.due_date < self.issue_date:
            raise StructuredFundValidationError(f"Data de vencimento ({self.due_date}) anterior à emissão ({self.issue_date})")

    @property
    def net_book_value(self) -> Decimal:
        """Valor contábil líquido após dedução da provisão para perdas (PCLD)."""
        return max(Decimal("0.00"), round_cents(self.current_outstanding_balance - self.pld_provision_amount))

    def mark_past_due(self, days: int, pld_pct: Decimal = Decimal("0.00")) -> None:
        """Registra inadimplência/atraso e constitui provisão PCLD."""
        if days < 0:
            raise StructuredFundValidationError("Dias de atraso não podem ser negativos.")
        self.days_past_due = days
        self.status = ReceivableStatus.PAST_DUE if days < 90 else ReceivableStatus.DEFAULTED
        pld_rate = min(Decimal("1.00"), max(Decimal("0.00"), pld_pct))
        self.pld_provision_amount = round_cents(self.current_outstanding_balance * pld_rate)

    def write_off(self) -> Decimal:
        """Baixa definitiva a prejuízo do saldo devedor remanescente."""
        loss = self.net_book_value
        self.pld_provision_amount = self.current_outstanding_balance
        self.status = ReceivableStatus.WRITTEN_OFF
        return loss

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receivable_id": self.receivable_id,
            "debtor_name": self.debtor_name,
            "debtor_cnpj_cpf": self.debtor_cnpj_cpf,
            "receivable_type": self.receivable_type.value,
            "original_face_value": str(self.original_face_value),
            "current_outstanding_balance": str(self.current_outstanding_balance),
            "acquisition_discount_rate_annual": str(self.acquisition_discount_rate_annual),
            "issue_date": self.issue_date.isoformat(),
            "due_date": self.due_date.isoformat(),
            "status": self.status.value,
            "days_past_due": self.days_past_due,
            "pld_provision_amount": str(self.pld_provision_amount),
            "net_book_value": str(self.net_book_value),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CreditReceivable:
        return cls(
            receivable_id=str(data["receivable_id"]),
            debtor_name=str(data["debtor_name"]),
            debtor_cnpj_cpf=str(data["debtor_cnpj_cpf"]),
            receivable_type=ReceivableType(data["receivable_type"]),
            original_face_value=to_decimal(data["original_face_value"]),
            current_outstanding_balance=to_decimal(data["current_outstanding_balance"]),
            acquisition_discount_rate_annual=to_decimal(data["acquisition_discount_rate_annual"]),
            issue_date=to_date(data["issue_date"]),
            due_date=to_date(data["due_date"]),
            status=ReceivableStatus(data.get("status", "ADIMPLENTE")),
            days_past_due=int(data.get("days_past_due", 0)),
            pld_provision_amount=to_decimal(data.get("pld_provision_amount", "0.00")),
        )


@dataclass
class FidcQuotaSubclass:
    """Classe/Subclasse de cotas de FIDC registrada no Balcão NoMe B3."""
    subclass_id: str
    isin: str
    b3_ticker: str
    quota_class: FidcQuotaClass
    benchmark_indexer: str
    target_spread_annual: Decimal
    quantity: Decimal
    unit_quota_value: Decimal
    minimum_subordination_ratio: Decimal = Decimal("0.00")
    accrued_unpaid_yield: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.quantity = to_decimal(self.quantity, "quantity")
        self.unit_quota_value = round_unit(to_decimal(self.unit_quota_value, "unit_quota_value"))
        self.target_spread_annual = round_rate(to_decimal(self.target_spread_annual, "target_spread_annual"))
        self.minimum_subordination_ratio = round_rate(to_decimal(self.minimum_subordination_ratio, "minimum_subordination_ratio"))
        self.accrued_unpaid_yield = round_cents(to_decimal(self.accrued_unpaid_yield, "accrued_unpaid_yield"))

        if self.quantity <= Decimal("0"):
            raise StructuredFundValidationError(f"Quantidade de cotas deve ser estritamente positiva: {self.quantity}")
        if self.unit_quota_value < Decimal("0.00"):
            raise StructuredFundValidationError(f"Valor unitário de cota não pode ser negativo: {self.unit_quota_value}")

    @property
    def total_equity(self) -> Decimal:
        """Patrimônio líquido contábil total da classe (Quantidade * PU)."""
        return round_cents(self.quantity * self.unit_quota_value)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subclass_id": self.subclass_id,
            "isin": self.isin,
            "b3_ticker": self.b3_ticker,
            "quota_class": self.quota_class.value,
            "benchmark_indexer": self.benchmark_indexer,
            "target_spread_annual": str(self.target_spread_annual),
            "quantity": str(self.quantity),
            "unit_quota_value": str(self.unit_quota_value),
            "total_equity": str(self.total_equity),
            "minimum_subordination_ratio": str(self.minimum_subordination_ratio),
            "accrued_unpaid_yield": str(self.accrued_unpaid_yield),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FidcQuotaSubclass:
        return cls(
            subclass_id=str(data["subclass_id"]),
            isin=str(data["isin"]),
            b3_ticker=str(data["b3_ticker"]),
            quota_class=FidcQuotaClass(data["quota_class"]),
            benchmark_indexer=str(data["benchmark_indexer"]),
            target_spread_annual=to_decimal(data["target_spread_annual"]),
            quantity=to_decimal(data["quantity"]),
            unit_quota_value=to_decimal(data["unit_quota_value"]),
            minimum_subordination_ratio=to_decimal(data.get("minimum_subordination_ratio", "0.00")),
            accrued_unpaid_yield=to_decimal(data.get("accrued_unpaid_yield", "0.00")),
        )


@dataclass
class FidcLossAbsorptionResult:
    """Resultado auditável da absorção de prejuízo por default de recebíveis."""
    event_id: str
    receivable_id: str
    total_loss_amount: Decimal
    absorbed_by_junior: Decimal
    absorbed_by_mezzanine: Decimal
    absorbed_by_senior: Decimal
    unabsorbed_loss: Decimal
    junior_equity_post: Decimal
    mezzanine_equity_post: Decimal
    senior_equity_post: Decimal
    is_water_tight_satisfied: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "receivable_id": self.receivable_id,
            "total_loss_amount": str(self.total_loss_amount),
            "absorbed_by_junior": str(self.absorbed_by_junior),
            "absorbed_by_mezzanine": str(self.absorbed_by_mezzanine),
            "absorbed_by_senior": str(self.absorbed_by_senior),
            "unabsorbed_loss": str(self.unabsorbed_loss),
            "junior_equity_post": str(self.junior_equity_post),
            "mezzanine_equity_post": str(self.mezzanine_equity_post),
            "senior_equity_post": str(self.senior_equity_post),
            "is_water_tight_satisfied": self.is_water_tight_satisfied,
        }


@dataclass
class FidcWaterfallResult:
    """Demonstrativo de execução do Payment Waterfall periódico no FIDC."""
    distribution_id: str
    period_date: date
    opening_cash: Decimal
    collections_inflow: Decimal
    total_available_cash: Decimal
    operational_expenses_paid: Decimal
    senior_interest_paid: Decimal
    senior_amortization_paid: Decimal
    senior_deficit: Decimal
    mezzanine_interest_paid: Decimal
    mezzanine_amortization_paid: Decimal
    mezzanine_deficit: Decimal
    junior_remuneration_paid: Decimal
    closing_cash: Decimal
    subordination_ratio_post: Decimal
    water_tight_priority_verified: bool
    cash_conservation_verified: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "distribution_id": self.distribution_id,
            "period_date": self.period_date.isoformat(),
            "opening_cash": str(self.opening_cash),
            "collections_inflow": str(self.collections_inflow),
            "total_available_cash": str(self.total_available_cash),
            "operational_expenses_paid": str(self.operational_expenses_paid),
            "senior_interest_paid": str(self.senior_interest_paid),
            "senior_amortization_paid": str(self.senior_amortization_paid),
            "senior_deficit": str(self.senior_deficit),
            "mezzanine_interest_paid": str(self.mezzanine_interest_paid),
            "mezzanine_amortization_paid": str(self.mezzanine_amortization_paid),
            "mezzanine_deficit": str(self.mezzanine_deficit),
            "junior_remuneration_paid": str(self.junior_remuneration_paid),
            "closing_cash": str(self.closing_cash),
            "subordination_ratio_post": str(self.subordination_ratio_post),
            "water_tight_priority_verified": self.water_tight_priority_verified,
            "cash_conservation_verified": self.cash_conservation_verified,
        }


@dataclass
class FidcFund:
    """Entidade de Fundo de Investimento em Direitos Creditórios (FIDC - CVM 175 Anexo II)."""
    fund_id: str
    fund_name: str
    cnpj: str
    administrator_cnpj: str
    manager_cnpj: str
    cvm_registration_number: str
    creation_date: date
    senior_quota: FidcQuotaSubclass
    junior_quota: FidcQuotaSubclass
    mezzanine_quota: Optional[FidcQuotaSubclass] = None
    receivables: Dict[str, CreditReceivable] = field(default_factory=dict)
    cash_balance: Decimal = Decimal("0.00")
    accrued_expenses: Decimal = Decimal("0.00")
    loss_events: List[FidcLossAbsorptionResult] = field(default_factory=list)
    waterfall_history: List[FidcWaterfallResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.creation_date = to_date(self.creation_date, "creation_date")
        self.cash_balance = round_cents(to_decimal(self.cash_balance, "cash_balance"))
        self.accrued_expenses = round_cents(to_decimal(self.accrued_expenses, "accrued_expenses"))

        if self.senior_quota.quota_class != FidcQuotaClass.SENIOR:
            raise StructuredFundValidationError(f"Classe incorreta para cota sênior: {self.senior_quota.quota_class}")
        if self.junior_quota.quota_class != FidcQuotaClass.SUBORDINATED_JUNIOR:
            raise StructuredFundValidationError(f"Classe incorreta para cota júnior: {self.junior_quota.quota_class}")
        if self.mezzanine_quota and self.mezzanine_quota.quota_class != FidcQuotaClass.MEZZANINE:
            raise StructuredFundValidationError(f"Classe incorreta para cota mezanino: {self.mezzanine_quota.quota_class}")

    @property
    def total_receivables_net_balance(self) -> Decimal:
        """Soma contábil líquida dos recebíveis ativos (excluindo baixados)."""
        tot = Decimal("0.00")
        for rec in self.receivables.values():
            if rec.status != ReceivableStatus.WRITTEN_OFF and rec.status != ReceivableStatus.SETTLED:
                tot += rec.net_book_value
        return round_cents(tot)

    @property
    def total_assets(self) -> Decimal:
        """Ativo Total do FIDC: Recebíveis Líquidos + Disponibilidades de Caixa."""
        return round_cents(self.total_receivables_net_balance + self.cash_balance)

    @property
    def total_liabilities(self) -> Decimal:
        """Passivo Exigível do FIDC (despesas, taxas e obrigações a pagar)."""
        return round_cents(self.accrued_expenses)

    @property
    def net_asset_value(self) -> Decimal:
        """Patrimônio Líquido do FIDC (Ativo Total - Passivo Exigível)."""
        return max(Decimal("0.00"), round_cents(self.total_assets - self.total_liabilities))

    def add_receivable(self, receivable: CreditReceivable) -> None:
        """Registra novo direito creditório cedido ao FIDC."""
        if receivable.receivable_id in self.receivables:
            raise StructuredFundValidationError(f"Recebível '{receivable.receivable_id}' já cadastrado.")
        self.receivables[receivable.receivable_id] = receivable

    def process_receivable_collection(
        self, receivable_id: str, principal_collected: Decimal, interest_collected: Decimal = Decimal("0.00")
    ) -> Decimal:
        """Liquida recebível e aporta recursos líquidos na conta caixa do fundo."""
        if receivable_id not in self.receivables:
            raise StructuredFundValidationError(f"Recebível '{receivable_id}' não localizado.")
        rec = self.receivables[receivable_id]
        princ = round_cents(to_decimal(principal_collected, "principal_collected"))
        interest = round_cents(to_decimal(interest_collected, "interest_collected"))

        if princ > rec.current_outstanding_balance:
            raise StructuredFundValidationError(
                f"Amortização informada ({princ}) excede saldo devedor ({rec.current_outstanding_balance})."
            )

        rec.current_outstanding_balance = round_cents(rec.current_outstanding_balance - princ)
        if rec.current_outstanding_balance == Decimal("0.00"):
            rec.status = ReceivableStatus.SETTLED
            rec.pld_provision_amount = Decimal("0.00")

        total_inflow = round_cents(princ + interest)
        self.cash_balance = round_cents(self.cash_balance + total_inflow)
        return total_inflow

    def calculate_subordination_ratio(self) -> Decimal:
        """Calcula o índice de subordinação (Razão de Subordinação das Cotas Subordinadas).

        Índice = (PL_Junior + PL_Mezanino) / PL_Total (para proteção da Sênior).
        """
        pl_total = self.net_asset_value
        if pl_total == Decimal("0.00"):
            return Decimal("0.00")
        mezz_eq = self.mezzanine_quota.total_equity if self.mezzanine_quota else Decimal("0.00")
        jr_eq = self.junior_quota.total_equity
        sub_equity = round_cents(jr_eq + mezz_eq)
        return round_rate(sub_equity / pl_total)

    def is_subordination_breached(self) -> bool:
        """Verifica se o índice de subordinação está abaixo do mínimo contratual da classe Sênior."""
        current_ratio = self.calculate_subordination_ratio()
        return current_ratio < self.senior_quota.minimum_subordination_ratio

    def absorb_credit_default(
        self, receivable_id: str, loss_amount: Optional[Decimal] = None
    ) -> FidcLossAbsorptionResult:
        """Executa a absorção hierárquica estrita (Water-Tight Priority) de perda por default.

        Regra Inviolável CVM 175:
        1. Subordinada Júnior absorve 100% da perda até seu PL se esgotar.
        2. Mezanino absorve qualquer perda remanescente até seu PL se esgotar.
        3. Sênior absorve perdas apenas e exclusivamente se Júnior e Mezanino estiverem zeradas.
        """
        if receivable_id not in self.receivables:
            raise StructuredFundValidationError(f"Recebível '{receivable_id}' não existe no fundo.")
        rec = self.receivables[receivable_id]

        if loss_amount is not None:
            effective_loss = round_cents(to_decimal(loss_amount, "loss_amount"))
        else:
            effective_loss = rec.write_off()

        rem_loss = effective_loss
        junior_eq = self.junior_quota.total_equity
        mezz_eq = self.mezzanine_quota.total_equity if self.mezzanine_quota else Decimal("0.00")
        senior_eq = self.senior_quota.total_equity

        # 1. Absorção pela Subordinada Júnior
        abs_junior = min(rem_loss, junior_eq)
        rem_loss = round_cents(rem_loss - abs_junior)

        # 2. Absorção pela Mezanino
        abs_mezz = Decimal("0.00")
        if rem_loss > Decimal("0.00") and self.mezzanine_quota:
            abs_mezz = min(rem_loss, mezz_eq)
            rem_loss = round_cents(rem_loss - abs_mezz)

        # 3. Absorção pela Sênior
        abs_senior = Decimal("0.00")
        if rem_loss > Decimal("0.00"):
            abs_senior = min(rem_loss, senior_eq)
            rem_loss = round_cents(rem_loss - abs_senior)

        unabsorbed = rem_loss

        # Atualização contábil dos PUs das cotas
        new_jr_eq = round_cents(junior_eq - abs_junior)
        self.junior_quota.unit_quota_value = round_unit(new_jr_eq / self.junior_quota.quantity)

        if self.mezzanine_quota:
            new_mezz_eq = round_cents(mezz_eq - abs_mezz)
            self.mezzanine_quota.unit_quota_value = round_unit(new_mezz_eq / self.mezzanine_quota.quantity)
        else:
            new_mezz_eq = Decimal("0.00")

        new_sr_eq = round_cents(senior_eq - abs_senior)
        self.senior_quota.unit_quota_value = round_unit(new_sr_eq / self.senior_quota.quantity)

        # Verificação formal da prioridade estrita
        water_tight = True
        if abs_mezz > Decimal("0.00") and new_jr_eq > Decimal("0.00"):
            water_tight = False
        if abs_senior > Decimal("0.00") and (new_jr_eq > Decimal("0.00") or new_mezz_eq > Decimal("0.00")):
            water_tight = False

        if not water_tight:
            raise FidcSubordinationBreachError(
                f"Violação da subordinação estrita: Júnior={new_jr_eq}, Mezanino_Abs={abs_mezz}, Sênior_Abs={abs_senior}"
            )

        event = FidcLossAbsorptionResult(
            event_id=f"ABS-{self.fund_id}-{len(self.loss_events) + 1:04d}",
            receivable_id=receivable_id,
            total_loss_amount=effective_loss,
            absorbed_by_junior=abs_junior,
            absorbed_by_mezzanine=abs_mezz,
            absorbed_by_senior=abs_senior,
            unabsorbed_loss=unabsorbed,
            junior_equity_post=new_jr_eq,
            mezzanine_equity_post=new_mezz_eq,
            senior_equity_post=new_sr_eq,
            is_water_tight_satisfied=water_tight,
        )
        self.loss_events.append(event)
        return event

    def execute_waterfall(
        self,
        period_date: date,
        operational_expenses: Decimal,
        senior_target_interest: Decimal,
        senior_target_amortization: Decimal = Decimal("0.00"),
        mezzanine_target_interest: Decimal = Decimal("0.00"),
        mezzanine_target_amortization: Decimal = Decimal("0.00"),
    ) -> FidcWaterfallResult:
        """Executa a cascata de amortização e pagamentos (Payment Waterfall) periódica no Balcão NoMe B3.

        Ordem Estrita de Prioridade:
        1. Despesas operacionais do fundo (administração, custódia, escrituração);
        2. Remuneração / Juros da cota Sênior;
        3. Amortização programada da cota Sênior;
        4. Remuneração / Juros da cota Mezanino (apenas se Sênior 100% adimplente);
        5. Amortização programada da cota Mezanino (apenas se Sênior 100% adimplente);
        6. Remuneração residual / distribuição à cota Subordinada Júnior;
        7. Retenção de caixa final.
        """
        p_date = to_date(period_date, "period_date")
        op_exp = round_cents(to_decimal(operational_expenses, "operational_expenses"))
        sr_int_req = round_cents(to_decimal(senior_target_interest, "senior_target_interest"))
        sr_amort_req = round_cents(to_decimal(senior_target_amortization, "senior_target_amortization"))
        mezz_int_req = round_cents(to_decimal(mezzanine_target_interest, "mezzanine_target_interest"))
        mezz_amort_req = round_cents(to_decimal(mezzanine_target_amortization, "mezzanine_target_amortization"))

        opening_cash = self.cash_balance
        available = opening_cash

        # 1. Despesas operacionais
        paid_exp = min(available, op_exp)
        available = round_cents(available - paid_exp)

        # 2. Juros Sênior
        paid_sr_int = min(available, sr_int_req)
        available = round_cents(available - paid_sr_int)
        sr_int_deficit = round_cents(sr_int_req - paid_sr_int)

        # 3. Amortização Sênior
        paid_sr_amort = min(available, sr_amort_req)
        available = round_cents(available - paid_sr_amort)
        sr_amort_deficit = round_cents(sr_amort_req - paid_sr_amort)
        total_sr_deficit = round_cents(sr_int_deficit + sr_amort_deficit)

        # 4 & 5. Mezanino (apenas se Sênior 100% atendida)
        paid_mezz_int = Decimal("0.00")
        paid_mezz_amort = Decimal("0.00")
        mezz_deficit = round_cents(mezz_int_req + mezz_amort_req)

        if total_sr_deficit == Decimal("0.00"):
            paid_mezz_int = min(available, mezz_int_req)
            available = round_cents(available - paid_mezz_int)
            paid_mezz_amort = min(available, mezz_amort_req)
            available = round_cents(available - paid_mezz_amort)
            mezz_deficit = round_cents((mezz_int_req - paid_mezz_int) + (mezz_amort_req - paid_mezz_amort))

        # 6. Júnior (apenas se Sênior e Mezanino 100% atendidas)
        paid_jr = Decimal("0.00")
        if total_sr_deficit == Decimal("0.00") and mezz_deficit == Decimal("0.00"):
            # Subordinada júnior recebe o excesso de caixa disponível
            paid_jr = available
            available = Decimal("0.00")

        closing_cash = available
        self.cash_balance = closing_cash

        # Atualização de amortizações de principal das cotas
        if paid_sr_amort > Decimal("0.00"):
            sr_eq = round_cents(self.senior_quota.total_equity - paid_sr_amort)
            self.senior_quota.unit_quota_value = round_unit(sr_eq / self.senior_quota.quantity)
        if paid_mezz_amort > Decimal("0.00") and self.mezzanine_quota:
            mezz_eq = round_cents(self.mezzanine_quota.total_equity - paid_mezz_amort)
            self.mezzanine_quota.unit_quota_value = round_unit(mezz_eq / self.mezzanine_quota.quantity)

        # Verificação da conservação estrita de caixa
        total_disbursed = round_cents(
            paid_exp + paid_sr_int + paid_sr_amort + paid_mezz_int + paid_mezz_amort + paid_jr + closing_cash
        )
        cash_conservation = (total_disbursed == opening_cash)

        # Prioridade estrita do waterfall
        wt_verified = True
        if (paid_mezz_int > Decimal("0.00") or paid_mezz_amort > Decimal("0.00")) and total_sr_deficit > Decimal("0.00"):
            wt_verified = False
        if paid_jr > Decimal("0.00") and (total_sr_deficit > Decimal("0.00") or mezz_deficit > Decimal("0.00")):
            wt_verified = False

        res = FidcWaterfallResult(
            distribution_id=f"WTF-{self.fund_id}-{p_date.isoformat()}",
            period_date=p_date,
            opening_cash=opening_cash,
            collections_inflow=Decimal("0.00"),
            total_available_cash=opening_cash,
            operational_expenses_paid=paid_exp,
            senior_interest_paid=paid_sr_int,
            senior_amortization_paid=paid_sr_amort,
            senior_deficit=total_sr_deficit,
            mezzanine_interest_paid=paid_mezz_int,
            mezzanine_amortization_paid=paid_mezz_amort,
            mezzanine_deficit=mezz_deficit,
            junior_remuneration_paid=paid_jr,
            closing_cash=closing_cash,
            subordination_ratio_post=self.calculate_subordination_ratio(),
            water_tight_priority_verified=wt_verified,
            cash_conservation_verified=cash_conservation,
        )
        self.waterfall_history.append(res)
        return res

    def recalculate_quotas_nav(self) -> None:
        """Reconcilia e recalcula o valor da cota júnior como resíduo do PL do fundo."""
        total_pl = self.net_asset_value
        sr_eq = self.senior_quota.total_equity
        mezz_eq = self.mezzanine_quota.total_equity if self.mezzanine_quota else Decimal("0.00")
        prior_eq = round_cents(sr_eq + mezz_eq)

        if total_pl >= prior_eq:
            jr_equity = round_cents(total_pl - prior_eq)
            self.junior_quota.unit_quota_value = round_unit(jr_equity / self.junior_quota.quantity)
        else:
            self.junior_quota.unit_quota_value = Decimal("0.00000000")
            deficit = round_cents(prior_eq - total_pl)
            if self.mezzanine_quota:
                if mezz_eq >= deficit:
                    new_mezz_eq = round_cents(mezz_eq - deficit)
                    self.mezzanine_quota.unit_quota_value = round_unit(new_mezz_eq / self.mezzanine_quota.quantity)
                else:
                    self.mezzanine_quota.unit_quota_value = Decimal("0.00000000")
                    sr_deficit = round_cents(deficit - mezz_eq)
                    new_sr_eq = max(Decimal("0.00"), round_cents(sr_eq - sr_deficit))
                    self.senior_quota.unit_quota_value = round_unit(new_sr_eq / self.senior_quota.quantity)
            else:
                new_sr_eq = max(Decimal("0.00"), round_cents(sr_eq - deficit))
                self.senior_quota.unit_quota_value = round_unit(new_sr_eq / self.senior_quota.quantity)

    def verify_invariants(self) -> Dict[str, bool]:
        """Verifica todas as leis formais de conservação e subordinação para o FIDC."""
        # 1. Conservação Patrimonial: PL_Total == Soma(PL_Classes)
        pl_total = self.net_asset_value
        sr_eq = self.senior_quota.total_equity
        mezz_eq = self.mezzanine_quota.total_equity if self.mezzanine_quota else Decimal("0.00")
        jr_eq = self.junior_quota.total_equity
        classes_sum = round_cents(sr_eq + mezz_eq + jr_eq)
        equity_conservation = (pl_total == classes_sum)

        # 2. Inviolabilidade da Subordinação Estrita: Mezanino só absorve perda com Júnior zerada
        subordination_sanely_distributed = True
        for ev in self.loss_events:
            if not ev.is_water_tight_satisfied:
                subordination_sanely_distributed = False
                break

        # 3. Não-Negatividade de cotas
        quotas_non_negative = (
            self.senior_quota.unit_quota_value >= Decimal("0.00")
            and (self.mezzanine_quota is None or self.mezzanine_quota.unit_quota_value >= Decimal("0.00"))
            and self.junior_quota.unit_quota_value >= Decimal("0.00")
            and self.senior_quota.quantity > Decimal("0")
            and self.junior_quota.quantity > Decimal("0")
        )

        # 4. Sanidade do Balanço: Ativos == Recebíveis + Caixa
        balance_sheet_sane = (self.total_assets == round_cents(self.total_receivables_net_balance + self.cash_balance))

        # 5. Waterfall cash conservation em todos os eventos
        waterfall_sane = all(w.cash_conservation_verified and w.water_tight_priority_verified for w in self.waterfall_history)

        return {
            "equity_conservation_verified": equity_conservation,
            "subordination_water_tight_verified": subordination_sanely_distributed,
            "quotas_non_negative_verified": quotas_non_negative,
            "balance_sheet_sane_verified": balance_sheet_sane,
            "waterfall_history_verified": waterfall_sane,
            "all_invariants_hold": (
                equity_conservation
                and subordination_sanely_distributed
                and quotas_non_negative
                and balance_sheet_sane
                and waterfall_sane
            ),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fund_id": self.fund_id,
            "fund_name": self.fund_name,
            "cnpj": self.cnpj,
            "administrator_cnpj": self.administrator_cnpj,
            "manager_cnpj": self.manager_cnpj,
            "cvm_registration_number": self.cvm_registration_number,
            "creation_date": self.creation_date.isoformat(),
            "senior_quota": self.senior_quota.to_dict(),
            "mezzanine_quota": self.mezzanine_quota.to_dict() if self.mezzanine_quota else None,
            "junior_quota": self.junior_quota.to_dict(),
            "cash_balance": str(self.cash_balance),
            "accrued_expenses": str(self.accrued_expenses),
            "total_receivables_net_balance": str(self.total_receivables_net_balance),
            "total_assets": str(self.total_assets),
            "total_liabilities": str(self.total_liabilities),
            "net_asset_value": str(self.net_asset_value),
            "subordination_ratio": str(self.calculate_subordination_ratio()),
            "subordination_breached": self.is_subordination_breached(),
            "receivables": [r.to_dict() for r in self.receivables.values()],
            "loss_events": [ev.to_dict() for ev in self.loss_events],
            "waterfall_history": [w.to_dict() for w in self.waterfall_history],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FidcFund:
        sr = FidcQuotaSubclass.from_dict(data["senior_quota"])
        jr = FidcQuotaSubclass.from_dict(data["junior_quota"])
        mezz = FidcQuotaSubclass.from_dict(data["mezzanine_quota"]) if data.get("mezzanine_quota") else None
        fund = cls(
            fund_id=str(data["fund_id"]),
            fund_name=str(data["fund_name"]),
            cnpj=str(data["cnpj"]),
            administrator_cnpj=str(data["administrator_cnpj"]),
            manager_cnpj=str(data["manager_cnpj"]),
            cvm_registration_number=str(data["cvm_registration_number"]),
            creation_date=to_date(data["creation_date"]),
            senior_quota=sr,
            junior_quota=jr,
            mezzanine_quota=mezz,
            cash_balance=to_decimal(data.get("cash_balance", "0.00")),
            accrued_expenses=to_decimal(data.get("accrued_expenses", "0.00")),
        )
        for rec_d in data.get("receivables", []):
            fund.add_receivable(CreditReceivable.from_dict(rec_d))
        return fund


# =====================================================================
# MODELOS DE DADOS - FII (ANEXO NORMATIVO III - CVM 175 & LEI 8.668/93)
# =====================================================================

@dataclass
class FiiAsset:
    """Ativo imobiliário componente do patrimônio do FII."""
    asset_id: str
    asset_name: str
    asset_type: FiiAssetType
    acquisition_cost: Decimal
    current_valuation: Decimal
    monthly_gross_revenue: Decimal = Decimal("0.00")
    monthly_operating_expense: Decimal = Decimal("0.00")
    address_or_description: str = ""

    def __post_init__(self) -> None:
        self.acquisition_cost = round_cents(to_decimal(self.acquisition_cost, "acquisition_cost"))
        self.current_valuation = round_cents(to_decimal(self.current_valuation, "current_valuation"))
        self.monthly_gross_revenue = round_cents(to_decimal(self.monthly_gross_revenue, "monthly_gross_revenue"))
        self.monthly_operating_expense = round_cents(to_decimal(self.monthly_operating_expense, "monthly_operating_expense"))

        if self.acquisition_cost <= Decimal("0.00"):
            raise StructuredFundValidationError(f"Custo de aquisição deve ser positivo: {self.acquisition_cost}")
        if self.current_valuation < Decimal("0.00"):
            raise StructuredFundValidationError(f"Avaliação do ativo não pode ser negativa: {self.current_valuation}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_name": self.asset_name,
            "asset_type": self.asset_type.value,
            "acquisition_cost": str(self.acquisition_cost),
            "current_valuation": str(self.current_valuation),
            "monthly_gross_revenue": str(self.monthly_gross_revenue),
            "monthly_operating_expense": str(self.monthly_operating_expense),
            "address_or_description": self.address_or_description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FiiAsset:
        return cls(
            asset_id=str(data["asset_id"]),
            asset_name=str(data["asset_name"]),
            asset_type=FiiAssetType(data["asset_type"]),
            acquisition_cost=to_decimal(data["acquisition_cost"]),
            current_valuation=to_decimal(data["current_valuation"]),
            monthly_gross_revenue=to_decimal(data.get("monthly_gross_revenue", "0.00")),
            monthly_operating_expense=to_decimal(data.get("monthly_operating_expense", "0.00")),
            address_or_description=str(data.get("address_or_description", "")),
        )


@dataclass
class FiiCashFlowStatement:
    """Demonstrativo de apuração do resultado financeiro em Regime de Caixa de FII."""
    statement_id: str
    period_start: date
    period_end: date
    is_semiannual_closing: bool
    gross_rental_revenue: Decimal
    financial_yield_revenue: Decimal
    realized_capital_gains: Decimal
    other_operating_revenues: Decimal
    property_maintenance_expenses: Decimal
    administration_fee_paid: Decimal
    management_fee_paid: Decimal
    custody_and_audit_expenses: Decimal
    other_operating_expenses: Decimal

    def __post_init__(self) -> None:
        self.period_start = to_date(self.period_start, "period_start")
        self.period_end = to_date(self.period_end, "period_end")
        self.gross_rental_revenue = round_cents(to_decimal(self.gross_rental_revenue, "gross_rental_revenue"))
        self.financial_yield_revenue = round_cents(to_decimal(self.financial_yield_revenue, "financial_yield_revenue"))
        self.realized_capital_gains = round_cents(to_decimal(self.realized_capital_gains, "realized_capital_gains"))
        self.other_operating_revenues = round_cents(to_decimal(self.other_operating_revenues, "other_operating_revenues"))
        self.property_maintenance_expenses = round_cents(to_decimal(self.property_maintenance_expenses, "property_maintenance_expenses"))
        self.administration_fee_paid = round_cents(to_decimal(self.administration_fee_paid, "administration_fee_paid"))
        self.management_fee_paid = round_cents(to_decimal(self.management_fee_paid, "management_fee_paid"))
        self.custody_and_audit_expenses = round_cents(to_decimal(self.custody_and_audit_expenses, "custody_and_audit_expenses"))
        self.other_operating_expenses = round_cents(to_decimal(self.other_operating_expenses, "other_operating_expenses"))

        if self.period_end < self.period_start:
            raise StructuredFundValidationError(f"Fim de período ({self.period_end}) anterior ao início ({self.period_start})")

    @property
    def total_cash_revenues(self) -> Decimal:
        """Total de ingressos brutos recebidos em caixa no período."""
        return round_cents(
            self.gross_rental_revenue + self.financial_yield_revenue + self.realized_capital_gains + self.other_operating_revenues
        )

    @property
    def total_cash_expenses(self) -> Decimal:
        """Total de despesas operacionais efetivamente pagas em caixa no período."""
        return round_cents(
            self.property_maintenance_expenses
            + self.administration_fee_paid
            + self.management_fee_paid
            + self.custody_and_audit_expenses
            + self.other_operating_expenses
        )

    @property
    def net_cash_profit(self) -> Decimal:
        """Lucro Líquido em Regime de Caixa auferido no período."""
        return round_cents(self.total_cash_revenues - self.total_cash_expenses)

    @property
    def mandatory_minimum_distribution_95(self) -> Decimal:
        """Proventos mínimos obrigatórios de 95% do lucro caixa (Art. 10 Lei 8.668/93)."""
        if self.net_cash_profit <= Decimal("0.00"):
            return Decimal("0.00")
        return round_cents(self.net_cash_profit * LEI_8668_MIN_PROFIT_DISTRIBUTION_PCT)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statement_id": self.statement_id,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "is_semiannual_closing": self.is_semiannual_closing,
            "gross_rental_revenue": str(self.gross_rental_revenue),
            "financial_yield_revenue": str(self.financial_yield_revenue),
            "realized_capital_gains": str(self.realized_capital_gains),
            "other_operating_revenues": str(self.other_operating_revenues),
            "total_cash_revenues": str(self.total_cash_revenues),
            "property_maintenance_expenses": str(self.property_maintenance_expenses),
            "administration_fee_paid": str(self.administration_fee_paid),
            "management_fee_paid": str(self.management_fee_paid),
            "custody_and_audit_expenses": str(self.custody_and_audit_expenses),
            "other_operating_expenses": str(self.other_operating_expenses),
            "total_cash_expenses": str(self.total_cash_expenses),
            "net_cash_profit": str(self.net_cash_profit),
            "mandatory_minimum_distribution_95": str(self.mandatory_minimum_distribution_95),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FiiCashFlowStatement:
        return cls(
            statement_id=str(data["statement_id"]),
            period_start=to_date(data["period_start"]),
            period_end=to_date(data["period_end"]),
            is_semiannual_closing=bool(data.get("is_semiannual_closing", False)),
            gross_rental_revenue=to_decimal(data.get("gross_rental_revenue", "0.00")),
            financial_yield_revenue=to_decimal(data.get("financial_yield_revenue", "0.00")),
            realized_capital_gains=to_decimal(data.get("realized_capital_gains", "0.00")),
            other_operating_revenues=to_decimal(data.get("other_operating_revenues", "0.00")),
            property_maintenance_expenses=to_decimal(data.get("property_maintenance_expenses", "0.00")),
            administration_fee_paid=to_decimal(data.get("administration_fee_paid", "0.00")),
            management_fee_paid=to_decimal(data.get("management_fee_paid", "0.00")),
            custody_and_audit_expenses=to_decimal(data.get("custody_and_audit_expenses", "0.00")),
            other_operating_expenses=to_decimal(data.get("other_operating_expenses", "0.00")),
        )


@dataclass
class FiiDistributionEvent:
    """Evento corporativo de declaração e liquidação de proventos de FII na B3."""
    distribution_id: str
    statement_id: str
    record_date: date
    payment_date: date
    net_cash_profit: Decimal
    mandatory_minimum_distribution: Decimal
    declared_total_distribution: Decimal
    distribution_per_quota: Decimal
    payout_ratio_pct: Decimal
    retained_undistributed_earnings: Decimal
    is_cvm175_and_lei8668_compliant: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "distribution_id": self.distribution_id,
            "statement_id": self.statement_id,
            "record_date": self.record_date.isoformat(),
            "payment_date": self.payment_date.isoformat(),
            "net_cash_profit": str(self.net_cash_profit),
            "mandatory_minimum_distribution": str(self.mandatory_minimum_distribution),
            "declared_total_distribution": str(self.declared_total_distribution),
            "distribution_per_quota": str(self.distribution_per_quota),
            "payout_ratio_pct": str(self.payout_ratio_pct),
            "retained_undistributed_earnings": str(self.retained_undistributed_earnings),
            "is_cvm175_and_lei8668_compliant": self.is_cvm175_and_lei8668_compliant,
        }


@dataclass
class FiiFund:
    """Entidade de Fundo de Investimento Imobiliário (FII - CVM 175 Anexo III / Lei 8.668)."""
    fund_id: str
    fund_name: str
    cnpj: str
    b3_ticker: str
    isin: str
    administrator_cnpj: str
    manager_cnpj: str
    creation_date: date
    total_quotas: Decimal
    unit_quota_value: Decimal
    cash_balance: Decimal = Decimal("0.00")
    accrued_obligations: Decimal = Decimal("0.00")
    accumulated_retained_earnings: Decimal = Decimal("0.00")
    assets: Dict[str, FiiAsset] = field(default_factory=dict)
    cash_flow_history: List[FiiCashFlowStatement] = field(default_factory=list)
    distributions: List[FiiDistributionEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.creation_date = to_date(self.creation_date, "creation_date")
        self.total_quotas = to_decimal(self.total_quotas, "total_quotas")
        self.unit_quota_value = round_unit(to_decimal(self.unit_quota_value, "unit_quota_value"))
        self.cash_balance = round_cents(to_decimal(self.cash_balance, "cash_balance"))
        self.accrued_obligations = round_cents(to_decimal(self.accrued_obligations, "accrued_obligations"))
        self.accumulated_retained_earnings = round_cents(to_decimal(self.accumulated_retained_earnings, "accumulated_retained_earnings"))

        if self.total_quotas <= Decimal("0"):
            raise StructuredFundValidationError(f"Quantidade total de cotas de FII deve ser positiva: {self.total_quotas}")
        if self.unit_quota_value <= Decimal("0.00"):
            raise StructuredFundValidationError(f"Valor patrimonial unitário inicial deve ser positivo: {self.unit_quota_value}")

    @property
    def total_assets_valuation(self) -> Decimal:
        """Soma contábil das avaliações dos ativos imobiliários na carteira."""
        tot = Decimal("0.00")
        for asset in self.assets.values():
            tot += asset.current_valuation
        return round_cents(tot)

    @property
    def total_assets(self) -> Decimal:
        """Ativo Total do FII (Imóveis/Títulos Imobiliários + Disponibilidades de Caixa)."""
        return round_cents(self.total_assets_valuation + self.cash_balance)

    @property
    def total_liabilities(self) -> Decimal:
        """Passivo Exigível do FII (alavancagem, aquisições a prazo e provisões)."""
        return round_cents(self.accrued_obligations)

    @property
    def net_asset_value(self) -> Decimal:
        """Patrimônio Líquido contábil do FII (Ativo Total - Passivo Exigível)."""
        return max(Decimal("0.00"), round_cents(self.total_assets - self.total_liabilities))

    def add_asset(self, asset: FiiAsset) -> None:
        """Incorpora imóvel ou ativo financeiro imobiliário ao patrimônio do FII."""
        if asset.asset_id in self.assets:
            raise StructuredFundValidationError(f"Ativo '{asset.asset_id}' já registrado no fundo.")
        self.assets[asset.asset_id] = asset

    def record_cash_flow_period(
        self,
        statement_id: str,
        period_start: date,
        period_end: date,
        is_semiannual_closing: bool,
        gross_rental_revenue: Decimal = Decimal("0.00"),
        financial_yield_revenue: Decimal = Decimal("0.00"),
        realized_capital_gains: Decimal = Decimal("0.00"),
        other_operating_revenues: Decimal = Decimal("0.00"),
        property_maintenance_expenses: Decimal = Decimal("0.00"),
        administration_fee_paid: Decimal = Decimal("0.00"),
        management_fee_paid: Decimal = Decimal("0.00"),
        custody_and_audit_expenses: Decimal = Decimal("0.00"),
        other_operating_expenses: Decimal = Decimal("0.00"),
    ) -> FiiCashFlowStatement:
        """Registra movimentação e apuração de resultado de caixa do período."""
        stmt = FiiCashFlowStatement(
            statement_id=statement_id,
            period_start=period_start,
            period_end=period_end,
            is_semiannual_closing=is_semiannual_closing,
            gross_rental_revenue=gross_rental_revenue,
            financial_yield_revenue=financial_yield_revenue,
            realized_capital_gains=realized_capital_gains,
            other_operating_revenues=other_operating_revenues,
            property_maintenance_expenses=property_maintenance_expenses,
            administration_fee_paid=administration_fee_paid,
            management_fee_paid=management_fee_paid,
            custody_and_audit_expenses=custody_and_audit_expenses,
            other_operating_expenses=other_operating_expenses,
        )
        self.cash_flow_history.append(stmt)
        # Atualiza o saldo de caixa do fundo
        net_cash = stmt.net_cash_profit
        self.cash_balance = round_cents(self.cash_balance + net_cash)
        return stmt

    def declare_and_settle_distribution(
        self,
        statement_id: str,
        record_date: date,
        payment_date: date,
        declared_amount: Optional[Decimal] = None,
        force_payout_ratio: Optional[Decimal] = None,
    ) -> FiiDistributionEvent:
        """Declara e liquida distribuição de proventos de FII no Balcão NoMe B3.

        Garante o piso legal mandatória de 95% do lucro caixa (Lei nº 8.668/93).
        """
        stmt = next((s for s in self.cash_flow_history if s.statement_id == statement_id), None)
        if not stmt:
            raise StructuredFundValidationError(f"Demonstrativo '{statement_id}' não localizado no FII.")

        profit = stmt.net_cash_profit
        min_required = stmt.mandatory_minimum_distribution_95

        if declared_amount is not None:
            distribution_total = round_cents(to_decimal(declared_amount, "declared_amount"))
        elif force_payout_ratio is not None:
            ratio = min(Decimal("1.50"), max(Decimal("0.00"), force_payout_ratio))
            distribution_total = round_cents(profit * ratio) if profit > Decimal("0.00") else Decimal("0.00")
        else:
            # Padrão: 95% exatos do lucro caixa semestral / mensal
            distribution_total = min_required

        # Validação estrita da Lei nº 8.668/1993
        is_compliant = True
        if profit > Decimal("0.00") and distribution_total < min_required:
            is_compliant = False
            raise FiiRegulatoryDistributionError(
                f"Violação da Lei nº 8.668/93: Proventos declarados ({distribution_total}) "
                f"inferiores ao piso regulatório de 95% ({min_required}) do lucro caixa ({profit})."
            )

        # Verificação de solvência de caixa
        available_liquidity = round_cents(self.cash_balance + self.accumulated_retained_earnings)
        if distribution_total > available_liquidity:
            raise StructuredFundValidationError(
                f"Distribuição ({distribution_total}) excede liquidez disponível ({available_liquidity})."
            )

        # Débito em caixa
        if distribution_total <= self.cash_balance:
            self.cash_balance = round_cents(self.cash_balance - distribution_total)
            residual_profit = round_cents(profit - distribution_total) if profit > distribution_total else Decimal("0.00")
            self.accumulated_retained_earnings = round_cents(self.accumulated_retained_earnings + residual_profit)
        else:
            # Utiliza reserva de lucros acumulados anteriores
            surplus_needed = round_cents(distribution_total - self.cash_balance)
            self.cash_balance = Decimal("0.00")
            self.accumulated_retained_earnings = round_cents(self.accumulated_retained_earnings - surplus_needed)

        dist_per_quota = round_unit(distribution_total / self.total_quotas)
        payout_ratio = round_rate(distribution_total / profit) if profit > Decimal("0.00") else Decimal("0.00")
        retained = round_cents(profit - distribution_total) if profit > distribution_total else Decimal("0.00")

        event = FiiDistributionEvent(
            distribution_id=f"DIST-{self.b3_ticker}-{len(self.distributions) + 1:04d}",
            statement_id=statement_id,
            record_date=to_date(record_date, "record_date"),
            payment_date=to_date(payment_date, "payment_date"),
            net_cash_profit=profit,
            mandatory_minimum_distribution=min_required,
            declared_total_distribution=distribution_total,
            distribution_per_quota=dist_per_quota,
            payout_ratio_pct=round_rate(payout_ratio * Decimal("100.00")),
            retained_undistributed_earnings=retained,
            is_cvm175_and_lei8668_compliant=is_compliant,
        )
        self.distributions.append(event)
        self.recalculate_unit_quota()
        return event

    def recalculate_unit_quota(self) -> None:
        """Recalcula o Valor Patrimonial da Cota (NAV por cota = PL / Quantidade_Cotas)."""
        pl = self.net_asset_value
        self.unit_quota_value = round_unit(pl / self.total_quotas)

    def verify_invariants(self) -> Dict[str, bool]:
        """Verifica todas as leis de conservação patrimonial e conformidade legal do FII."""
        # 1. Conservação Patrimonial de Cotas: PL == Quantidade * Valor_Cota
        pl_total = self.net_asset_value
        calc_pl = round_cents(self.total_quotas * self.unit_quota_value)
        # Tolerância máxima de 1 centavo por truncamento de 8 casas
        equity_conservation = abs(pl_total - calc_pl) <= Decimal("0.02")

        # 2. Piso Legal de Distribuição de Proventos (Lei 8.668/93): >= 95% do lucro caixa positivo
        dist_compliant = True
        for ev in self.distributions:
            if not ev.is_cvm175_and_lei8668_compliant:
                dist_compliant = False
                break
            if ev.net_cash_profit > Decimal("0.00") and ev.declared_total_distribution < ev.mandatory_minimum_distribution:
                dist_compliant = False
                break

        # 3. Não-Negatividade de Caixa e Cotas
        solvency_ok = (self.cash_balance >= Decimal("0.00")) and (self.total_quotas > Decimal("0"))

        # 4. Integridade de Ativos
        assets_ok = all(a.current_valuation >= Decimal("0.00") for a in self.assets.values())

        return {
            "equity_conservation_verified": equity_conservation,
            "minimum_distribution_95_verified": dist_compliant,
            "solvency_non_negative_verified": solvency_ok,
            "assets_integrity_verified": assets_ok,
            "all_invariants_hold": equity_conservation and dist_compliant and solvency_ok and assets_ok,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fund_id": self.fund_id,
            "fund_name": self.fund_name,
            "cnpj": self.cnpj,
            "b3_ticker": self.b3_ticker,
            "isin": self.isin,
            "administrator_cnpj": self.administrator_cnpj,
            "manager_cnpj": self.manager_cnpj,
            "creation_date": self.creation_date.isoformat(),
            "total_quotas": str(self.total_quotas),
            "unit_quota_value": str(self.unit_quota_value),
            "total_equity": str(round_cents(self.total_quotas * self.unit_quota_value)),
            "cash_balance": str(self.cash_balance),
            "accrued_obligations": str(self.accrued_obligations),
            "accumulated_retained_earnings": str(self.accumulated_retained_earnings),
            "total_assets_valuation": str(self.total_assets_valuation),
            "total_assets": str(self.total_assets),
            "total_liabilities": str(self.total_liabilities),
            "net_asset_value": str(self.net_asset_value),
            "assets": [a.to_dict() for a in self.assets.values()],
            "cash_flow_history": [cf.to_dict() for cf in self.cash_flow_history],
            "distributions": [d.to_dict() for d in self.distributions],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FiiFund:
        fund = cls(
            fund_id=str(data["fund_id"]),
            fund_name=str(data["fund_name"]),
            cnpj=str(data["cnpj"]),
            b3_ticker=str(data["b3_ticker"]),
            isin=str(data["isin"]),
            administrator_cnpj=str(data["administrator_cnpj"]),
            manager_cnpj=str(data["manager_cnpj"]),
            creation_date=to_date(data["creation_date"]),
            total_quotas=to_decimal(data["total_quotas"]),
            unit_quota_value=to_decimal(data["unit_quota_value"]),
            cash_balance=to_decimal(data.get("cash_balance", "0.00")),
            accrued_obligations=to_decimal(data.get("accrued_obligations", "0.00")),
            accumulated_retained_earnings=to_decimal(data.get("accumulated_retained_earnings", "0.00")),
        )
        for asset_d in data.get("assets", []):
            fund.add_asset(FiiAsset.from_dict(asset_d))
        for stmt_d in data.get("cash_flow_history", []):
            fund.cash_flow_history.append(FiiCashFlowStatement.from_dict(stmt_d))
        return fund


# =====================================================================
# MOTOR CENTRAL / ORQUESTRADOR DE FUNDOS ESTRUTURADOS B3
# =====================================================================

class StructuredFundsEngine:
    """Motor unificado de gestão, liquidação e custódia escritural de FIDC e FII no Balcão NoMe B3."""

    def __init__(self) -> None:
        self.fidc_funds: Dict[str, FidcFund] = {}
        self.fii_funds: Dict[str, FiiFund] = {}

    def register_fidc(self, fund: FidcFund) -> None:
        """Registra fundo FIDC sob Anexo II da Resolução CVM 175."""
        if fund.fund_id in self.fidc_funds:
            raise StructuredFundValidationError(f"FIDC '{fund.fund_id}' já registrado no motor.")
        self.fidc_funds[fund.fund_id] = fund

    def register_fii(self, fund: FiiFund) -> None:
        """Registra fundo FII sob Anexo III da Resolução CVM 175 e Lei 8.668/93."""
        if fund.fund_id in self.fii_funds:
            raise StructuredFundValidationError(f"FII '{fund.fund_id}' já registrado no motor.")
        self.fii_funds[fund.fund_id] = fund

    def get_fidc(self, fund_id: str) -> FidcFund:
        """Recupera instância de FIDC por identificador."""
        if fund_id not in self.fidc_funds:
            raise StructuredFundValidationError(f"FIDC '{fund_id}' não cadastrado.")
        return self.fidc_funds[fund_id]

    def get_fii(self, fund_id: str) -> FiiFund:
        """Recupera instância de FII por identificador."""
        if fund_id not in self.fii_funds:
            raise StructuredFundValidationError(f"FII '{fund_id}' não cadastrado.")
        return self.fii_funds[fund_id]

    def process_fidc_default(
        self, fund_id: str, receivable_id: str, loss_amount: Optional[Decimal] = None
    ) -> FidcLossAbsorptionResult:
        """Processa inadimplência e absorção estrita de prejuízo na cascata do FIDC."""
        fund = self.get_fidc(fund_id)
        return fund.absorb_credit_default(receivable_id=receivable_id, loss_amount=loss_amount)

    def execute_fidc_waterfall(
        self,
        fund_id: str,
        period_date: date,
        operational_expenses: Decimal,
        senior_target_interest: Decimal,
        senior_target_amortization: Decimal = Decimal("0.00"),
        mezzanine_target_interest: Decimal = Decimal("0.00"),
        mezzanine_target_amortization: Decimal = Decimal("0.00"),
    ) -> FidcWaterfallResult:
        """Executa cascata de pagamentos periódica de FIDC."""
        fund = self.get_fidc(fund_id)
        return fund.execute_waterfall(
            period_date=period_date,
            operational_expenses=operational_expenses,
            senior_target_interest=senior_target_interest,
            senior_target_amortization=senior_target_amortization,
            mezzanine_target_interest=mezzanine_target_interest,
            mezzanine_target_amortization=mezzanine_target_amortization,
        )

    def record_fii_cash_flow(
        self,
        fund_id: str,
        statement_id: str,
        period_start: date,
        period_end: date,
        is_semiannual_closing: bool,
        gross_rental_revenue: Decimal = Decimal("0.00"),
        financial_yield_revenue: Decimal = Decimal("0.00"),
        realized_capital_gains: Decimal = Decimal("0.00"),
        other_operating_revenues: Decimal = Decimal("0.00"),
        property_maintenance_expenses: Decimal = Decimal("0.00"),
        administration_fee_paid: Decimal = Decimal("0.00"),
        management_fee_paid: Decimal = Decimal("0.00"),
        custody_and_audit_expenses: Decimal = Decimal("0.00"),
        other_operating_expenses: Decimal = Decimal("0.00"),
    ) -> FiiCashFlowStatement:
        """Lança demonstrativo de resultado em regime de caixa de FII."""
        fund = self.get_fii(fund_id)
        return fund.record_cash_flow_period(
            statement_id=statement_id,
            period_start=period_start,
            period_end=period_end,
            is_semiannual_closing=is_semiannual_closing,
            gross_rental_revenue=gross_rental_revenue,
            financial_yield_revenue=financial_yield_revenue,
            realized_capital_gains=realized_capital_gains,
            other_operating_revenues=other_operating_revenues,
            property_maintenance_expenses=property_maintenance_expenses,
            administration_fee_paid=administration_fee_paid,
            management_fee_paid=management_fee_paid,
            custody_and_audit_expenses=custody_and_audit_expenses,
            other_operating_expenses=other_operating_expenses,
        )

    def distribute_fii_earnings(
        self,
        fund_id: str,
        statement_id: str,
        record_date: date,
        payment_date: date,
        declared_amount: Optional[Decimal] = None,
        force_payout_ratio: Optional[Decimal] = None,
    ) -> FiiDistributionEvent:
        """Declara e liquida distribuição de proventos de FII no Balcão NoMe."""
        fund = self.get_fii(fund_id)
        return fund.declare_and_settle_distribution(
            statement_id=statement_id,
            record_date=record_date,
            payment_date=payment_date,
            declared_amount=declared_amount,
            force_payout_ratio=force_payout_ratio,
        )

    def verify_all_invariants(self) -> Dict[str, Dict[str, bool]]:
        """Audita formalmente todas as leis de conservação de todos os FIDCs e FIIs."""
        report: Dict[str, Dict[str, bool]] = {}
        for fidc_id, fund in self.fidc_funds.items():
            report[f"FIDC:{fidc_id}"] = fund.verify_invariants()
        for fii_id, fund in self.fii_funds.items():
            report[f"FII:{fii_id}"] = fund.verify_invariants()
        return report

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "flow_code": "FL-077",
            "system": "B3_BALCAO_NOME_STRUCTURED_FUNDS",
            "fidc_funds": [f.to_dict() for f in self.fidc_funds.values()],
            "fii_funds": [f.to_dict() for f in self.fii_funds.values()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> StructuredFundsEngine:
        engine = cls()
        for fidc_d in data.get("fidc_funds", []):
            engine.register_fidc(FidcFund.from_dict(fidc_d))
        for fii_d in data.get("fii_funds", []):
            engine.register_fii(FiiFund.from_dict(fii_d))
        return engine

    @classmethod
    def load_from_fixture(cls, path: Union[str, Path]) -> StructuredFundsEngine:
        """Carrega e instancia o motor a partir de arquivo JSON de fixture."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Arquivo de fixture '{file_path}' não encontrado.")
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
