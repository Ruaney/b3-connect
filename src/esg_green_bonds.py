"""Módulo de Balcão NoMe B3 — Registro e Certificação de Green Bonds e Sustainability-Linked Bonds (FL-065).

Implementa o motor determinístico e o arcabouço regulatório da B3 para instrumentos de dívida sustentável
(Renda Fixa ESG no Balcão NoMe), integrando:
1. Green Bonds / Títulos Verdes (Use of Proceeds):
   - Alocação e rastreabilidade estrita de recursos (Net Proceeds = Allocated + Unallocated).
   - Validação da Taxonomia Verde (Taxonomia Sustentável Brasileira / FEBRABAN / CEBDS / ICMA GBP).
   - Prevenção a Greenwashing, sobrealocação de projetos e controle de recursos não alocados.
2. Sustainability-Linked Bonds (SLB / Metas Corporativas):
   - Governança de Indicadores-Chave de Desempenho (KPI) e Metas de Desempenho de Sustentabilidade (SPT).
   - Linhas de base (Baseline), trajetórias e direção de metas (Redução de GEE vs Aumento de Renovável).
   - Auditoria independente e prazos regulamentares de verificação (Limited/Reasonable Assurance).
3. Monitoramento de Gatilho de Step-Up de Cupom:
   - Verificação determinística de cumprimento de SPT na Data de Observação.
   - Aplicação de penalidade de Step-Up (+bps no cupom) por meta não atingida ou atraso de laudo auditado.
   - Projeção de fluxo de caixa financeiro e apuração da sobretaxa centavo a centavo.
4. Relatórios de Conformidade e Selo ESG B3:
   - Relatório de Alocação (Allocation Report) e Relatório de Impacto (Impact Report).
   - Relatório de Progresso de SLB e Dossiê de Certificação NoMe com hash criptográfico SHA-256.
5. Invariantes Formais de Conservação e Integridade Financeira.

Normas e Padrões de Referência:
- ICMA Green Bond Principles (GBP 2021) e Sustainability-Linked Bond Principles (SLBP 2023).
- Resoluções CVM nº 59/2021, nº 175/2022 e nº 193/2023 (Divulgações Financeiras e Relatórios ESG).
- Taxonomia Sustentável Brasileira (Ministério da Fazenda / FEBRABAN).
- Climate Bonds Standard v4.0 (Climate Bonds Initiative - CBI).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Constantes Numéricas e de Precisão B3
# ---------------------------------------------------------------------------

CENTS_PLACES = Decimal("0.01")
RATE_PLACES = Decimal("0.00000001")
PERCENT_PLACES = Decimal("0.0001")
IMPACT_PLACES = Decimal("0.001")
BPS_TO_DECIMAL = Decimal("0.0001")  # 1 bps = 0.01% = 0.0001
DAYS_PER_YEAR_CONVENTION = Decimal("360")  # Convenção 30/360 comercial ou 252 para dias úteis


def round_cents(val: Decimal) -> Decimal:
    """Arredonda valor financeiro para 2 casas decimais (critério ROUND_HALF_UP)."""
    return val.quantize(CENTS_PLACES, rounding=ROUND_HALF_UP)


def round_rate(val: Decimal) -> Decimal:
    """Arredonda taxas e fatores para 8 casas decimais."""
    return val.quantize(RATE_PLACES, rounding=ROUND_HALF_UP)


def round_percent(val: Decimal) -> Decimal:
    """Arredonda porcentagens para 4 casas decimais."""
    return val.quantize(PERCENT_PLACES, rounding=ROUND_HALF_UP)


def round_impact(val: Decimal) -> Decimal:
    """Arredonda métricas de impacto ambiental para 3 casas decimais."""
    return val.quantize(IMPACT_PLACES, rounding=ROUND_HALF_UP)


def to_decimal(val: Any, field_name: str = "campo") -> Decimal:
    """Converte valor para Decimal garantindo integridade e finitude."""
    if isinstance(val, Decimal):
        return val
    if val is None or isinstance(val, bool):
        raise EsgValidationError(f"Valor inválido ou nulo para o campo numérico '{field_name}'.")
    try:
        cleaned = str(val).strip().replace(",", ".")
        dec = Decimal(cleaned)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise EsgValidationError(f"Não foi possível converter '{val}' em Decimal para '{field_name}': {exc}") from exc
    if not dec.is_finite():
        raise EsgValidationError(f"Valor numérico não finito detectado em '{field_name}': {val}")
    return dec


def to_date(val: Any, field_name: str = "data") -> date:
    """Converte string ISO (YYYY-MM-DD) ou datetime para date."""
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if not val or not isinstance(val, str):
        raise EsgValidationError(f"Data inválida ou vazia no campo '{field_name}': {val}")
    try:
        return date.fromisoformat(val.strip())
    except ValueError as exc:
        raise EsgValidationError(f"Formato de data inválido para '{field_name}' ('{val}'). Use YYYY-MM-DD.") from exc


def validate_cnpj(cnpj: str, field_name: str = "cnpj") -> str:
    """Normaliza e valida CNPJ (14 dígitos)."""
    if not cnpj or not isinstance(cnpj, str):
        raise EsgValidationError(f"CNPJ obrigatório ausente em '{field_name}'.")
    digits = "".join(ch for ch in cnpj if ch.isdigit())
    if len(digits) != 14:
        raise EsgValidationError(f"CNPJ inválido em '{field_name}': '{cnpj}'. Esperado 14 dígitos numéricos.")
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def validate_isin(isin: str, field_name: str = "isin") -> str:
    """Valida código ISIN de 12 caracteres (ex: BRBMEIDBS001)."""
    if not isin or not isinstance(isin, str):
        raise EsgValidationError(f"Código ISIN ausente em '{field_name}'.")
    cleaned = isin.strip().upper()
    if len(cleaned) != 12 or not cleaned[:2].isalpha() or not cleaned[2:].isalnum():
        raise EsgValidationError(f"Código ISIN inválido em '{field_name}': '{isin}'. Esperado 12 caracteres alfanuméricos.")
    return cleaned


# ---------------------------------------------------------------------------
# Exceções Customizadas do Subsistema ESG Balcão NoMe B3
# ---------------------------------------------------------------------------

class EsgBondError(Exception):
    """Exceção base para o subsistema de Green Bonds e SLBs no Balcão NoMe B3."""


class EsgValidationError(EsgBondError):
    """Falha de validação cadastral, formatação de parâmetros ou atributos obrigatórios."""


class TaxonomyNonComplianceError(EsgBondError):
    """Violação das regras de elegibilidade da Taxonomia Verde ou superalocação de recursos."""


class StepUpTriggerError(EsgBondError):
    """Erro no processo de observação de metas SPT ou cálculo do gatilho de Step-Up."""


class EsgConservationError(EsgBondError):
    """Violação de leis matemáticas de conservação de recursos ESG, custódia ou financeiro."""


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------

class EsgInstrumentType(str, Enum):
    """Tipo do instrumento financeiro de dívida sustentável."""
    GREEN_BOND = "GREEN_BOND"
    SOCIAL_BOND = "SOCIAL_BOND"
    SUSTAINABLE_BOND = "SUSTAINABLE_BOND"
    TRANSITION_BOND = "TRANSITION_BOND"
    SUSTAINABILITY_LINKED_BOND = "SUSTAINABILITY_LINKED_BOND"

    @classmethod
    def from_str(cls, value: Any) -> EsgInstrumentType:
        if isinstance(value, cls):
            return value
        if not value:
            raise EsgValidationError("Tipo de instrumento ESG não pode ser vazio.")
        norm = str(value).strip().upper().replace(" ", "_").replace("-", "_")
        try:
            return cls(norm)
        except ValueError as exc:
            raise EsgValidationError(f"Tipo de instrumento ESG inválido: '{value}'. Esperado: {[e.value for e in cls]}") from exc


class GreenTaxonomySector(str, Enum):
    """Setores e categorias de projetos elegíveis conforme Taxonomia Verde Nacional e ICMA GBP."""
    RENEWABLE_ENERGY = "RENEWABLE_ENERGY"
    ENERGY_EFFICIENCY = "ENERGY_EFFICIENCY"
    CLEAN_TRANSPORTATION = "CLEAN_TRANSPORTATION"
    SUSTAINABLE_WATER_WASTEWATER = "SUSTAINABLE_WATER_WASTEWATER"
    CIRCULAR_ECONOMY_WASTE = "CIRCULAR_ECONOMY_WASTE"
    GREEN_BUILDINGS = "GREEN_BUILDINGS"
    SUSTAINABLE_AGRO_FORESTRY = "SUSTAINABLE_AGRO_FORESTRY"
    POLLUTION_PREVENTION_CONTROL = "POLLUTION_PREVENTION_CONTROL"
    CLIMATE_CHANGE_ADAPTATION = "CLIMATE_CHANGE_ADAPTATION"

    @classmethod
    def from_str(cls, value: Any) -> GreenTaxonomySector:
        if isinstance(value, cls):
            return value
        if not value:
            raise EsgValidationError("Setor da Taxonomia Verde não pode ser vazio.")
        norm = str(value).strip().upper().replace(" ", "_").replace("-", "_")
        try:
            return cls(norm)
        except ValueError as exc:
            raise EsgValidationError(f"Setor da Taxonomia Verde inválido: '{value}'. Esperado: {[e.value for e in cls]}") from exc


class KpiDirection(str, Enum):
    """Direção da meta de sustentabilidade (SPT): redução ou aumento."""
    DECREASE = "DECREASE"
    INCREASE = "INCREASE"

    @classmethod
    def from_str(cls, value: Any) -> KpiDirection:
        if isinstance(value, cls):
            return value
        if not value:
            raise EsgValidationError("Direção do KPI não pode ser vazia.")
        norm = str(value).strip().upper()
        try:
            return cls(norm)
        except ValueError as exc:
            raise EsgValidationError(f"Direção de KPI inválida: '{value}'. Esperado: DECREASE ou INCREASE") from exc


class SptStatus(str, Enum):
    """Status de cumprimento da Meta de Desempenho de Sustentabilidade (SPT)."""
    PENDING = "PENDING"
    ACHIEVED = "ACHIEVED"
    MISSED = "MISSED"
    DEFAULTED_REPORTING = "DEFAULTED_REPORTING"

    @classmethod
    def from_str(cls, value: Any) -> SptStatus:
        if isinstance(value, cls):
            return value
        if not value:
            raise EsgValidationError("Status de SPT não pode ser vazio.")
        norm = str(value).strip().upper()
        try:
            return cls(norm)
        except ValueError as exc:
            raise EsgValidationError(f"Status de SPT inválido: '{value}'.") from exc


class EsgCertificationStatus(str, Enum):
    """Status da certificação e selo ESG no Balcão NoMe B3."""
    CERTIFIED_ACTIVE = "CERTIFIED_ACTIVE"
    UNDER_REVIEW = "UNDER_REVIEW"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"

    @classmethod
    def from_str(cls, value: Any) -> EsgCertificationStatus:
        if isinstance(value, cls):
            return value
        if not value:
            raise EsgValidationError("Status de certificação não pode ser vazio.")
        norm = str(value).strip().upper()
        try:
            return cls(norm)
        except ValueError as exc:
            raise EsgValidationError(f"Status de certificação ESG inválido: '{value}'.") from exc


class SPOStatus(str, Enum):
    """Status do Parecer de Segunda Opinião (SPO) / Certificação Externa."""
    ALIGNED = "ALIGNED"
    CONDITIONALLY_ALIGNED = "CONDITIONALLY_ALIGNED"
    NON_ALIGNED = "NON_ALIGNED"
    EXPIRED = "EXPIRED"

    @classmethod
    def from_str(cls, value: Any) -> SPOStatus:
        if isinstance(value, cls):
            return value
        if not value:
            raise EsgValidationError("Status de SPO não pode ser vazio.")
        norm = str(value).strip().upper()
        try:
            return cls(norm)
        except ValueError as exc:
            raise EsgValidationError(f"Status de SPO inválido: '{value}'.") from exc


class CouponBenchmark(str, Enum):
    """Indexador de referência do cupom de juros."""
    IPCA = "IPCA"
    CDI = "CDI"
    PRE = "PRE"
    SELIC = "SELIC"

    @classmethod
    def from_str(cls, value: Any) -> CouponBenchmark:
        if isinstance(value, cls):
            return value
        norm = str(value).strip().upper()
        try:
            return cls(norm)
        except ValueError as exc:
            raise EsgValidationError(f"Indexador de cupom inválido: '{value}'.") from exc


class PaymentFrequency(str, Enum):
    """Periodicidade de pagamento de juros."""
    ANNUAL = "ANNUAL"
    SEMIANNUAL = "SEMIANNUAL"
    QUARTERLY = "QUARTERLY"
    MONTHLY = "MONTHLY"

    @property
    def periods_per_year(self) -> int:
        if self == PaymentFrequency.ANNUAL:
            return 1
        if self == PaymentFrequency.SEMIANNUAL:
            return 2
        if self == PaymentFrequency.QUARTERLY:
            return 4
        if self == PaymentFrequency.MONTHLY:
            return 12
        return 1

    @classmethod
    def from_str(cls, value: Any) -> PaymentFrequency:
        if isinstance(value, cls):
            return value
        norm = str(value).strip().upper()
        try:
            return cls(norm)
        except ValueError as exc:
            raise EsgValidationError(f"Periodicidade inválida: '{value}'.") from exc


class UnallocatedProceedsPolicy(str, Enum):
    """Política de gestão transitória de recursos ainda não alocados."""
    TREASURY_BONDS = "TREASURY_BONDS"  # Títulos Públicos Federais (LFT/LTN)
    CDB_VERDE = "CDB_VERDE"            # Depósito a Prazo com Destinação ESG
    CASH_EQUIVALENT = "CASH_EQUIVALENT"  # Caixa remunerado de curto prazo

    @classmethod
    def from_str(cls, value: Any) -> UnallocatedProceedsPolicy:
        if isinstance(value, cls):
            return value
        if not value:
            return cls.TREASURY_BONDS
        norm = str(value).strip().upper().replace(" ", "_").replace("-", "_")
        try:
            return cls(norm)
        except ValueError as exc:
            raise EsgValidationError(f"Política de recursos não alocados inválida: '{value}'.") from exc


# ---------------------------------------------------------------------------
# Dataclasses de Dados e Componentes Estruturados
# ---------------------------------------------------------------------------

@dataclass
class EligibleGreenProject:
    """Projeto elegível financiado com recursos de Green Bond (Use of Proceeds)."""
    project_id: str
    name: str
    sector: GreenTaxonomySector
    description: str
    total_project_cost: Decimal
    allocated_amount: Decimal
    allocation_date: date
    environmental_impact_metric: str  # ex: "tCO2e_avoided_per_year", "mwh_generated_per_year", "m3_water_treated"
    environmental_impact_value: Decimal
    location_uf: str
    verification_doc_ref: str

    def __post_init__(self) -> None:
        self.sector = GreenTaxonomySector.from_str(self.sector)
        self.total_project_cost = to_decimal(self.total_project_cost, "total_project_cost")
        self.allocated_amount = to_decimal(self.allocated_amount, "allocated_amount")
        self.environmental_impact_value = to_decimal(self.environmental_impact_value, "environmental_impact_value")
        self.allocation_date = to_date(self.allocation_date, "allocation_date")

        if self.allocated_amount <= Decimal("0"):
            raise TaxonomyNonComplianceError(f"Valor alocado no projeto '{self.project_id}' deve ser estritamente positivo.")
        if self.allocated_amount > self.total_project_cost:
            raise TaxonomyNonComplianceError(
                f"Valor alocado (R$ {self.allocated_amount}) excede o custo total do projeto '{self.project_id}' "
                f"(R$ {self.total_project_cost}). Risco de sobrealocação detectado."
            )


@dataclass
class SecondPartyOpinion:
    """Parecer de Segunda Opinião (SPO) emitido por avaliador externo independente credenciado."""
    spo_id: str
    verifier_name: str
    verifier_cnpj_or_id: str
    standard_framework: str  # ex: "ICMA_GBP_2021", "ICMA_SLBP_2023", "TAXONOMIA_VERDE_BR"
    opinion_date: date
    expiry_date: Optional[date]
    status: SPOStatus
    report_hash: str

    def __post_init__(self) -> None:
        self.opinion_date = to_date(self.opinion_date, "opinion_date")
        if self.expiry_date:
            self.expiry_date = to_date(self.expiry_date, "expiry_date")
        self.status = SPOStatus.from_str(self.status)
        if not self.report_hash:
            # Gera hash determinístico se não informado
            raw = f"{self.spo_id}:{self.verifier_name}:{self.standard_framework}:{self.opinion_date}"
            self.report_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def is_currently_valid(self, as_of: date) -> bool:
        """Verifica se o SPO está em vigência e alinhado."""
        if self.status != SPOStatus.ALIGNED:
            return False
        if self.expiry_date and as_of > self.expiry_date:
            return False
        return True


@dataclass
class KpiDefinition:
    """Indicador-Chave de Desempenho ESG para Sustainability-Linked Bonds."""
    kpi_id: str
    name: str
    unit_of_measure: str  # ex: "tCO2e/ton_celulose", "%", "m3/ton"
    baseline_year: int
    baseline_value: Decimal
    methodology: str  # ex: "GHG Protocol Corporate Standard", "ISO 14064"
    direction: KpiDirection

    def __post_init__(self) -> None:
        self.baseline_value = to_decimal(self.baseline_value, "baseline_value")
        self.direction = KpiDirection.from_str(self.direction)
        if self.baseline_year < 1900 or self.baseline_year > 2100:
            raise EsgValidationError(f"Ano de baseline inválido: {self.baseline_year}")


@dataclass
class SptTarget:
    """Meta de Desempenho de Sustentabilidade (SPT) atrelada ao gatilho de Step-Up."""
    spt_id: str
    kpi_id: str
    target_year: int
    observation_date: date
    verification_deadline: date
    target_value: Decimal
    step_up_spread_bps: Decimal  # ex: 25.0 bps = 0.25% p.a.
    step_down_spread_bps: Decimal = Decimal("0")
    measured_value: Optional[Decimal] = None
    verification_date: Optional[date] = None
    auditor_name: Optional[str] = None
    auditor_report_ref: Optional[str] = None
    status: SptStatus = SptStatus.PENDING

    def __post_init__(self) -> None:
        self.observation_date = to_date(self.observation_date, "observation_date")
        self.verification_deadline = to_date(self.verification_deadline, "verification_deadline")
        self.target_value = to_decimal(self.target_value, "target_value")
        self.step_up_spread_bps = to_decimal(self.step_up_spread_bps, "step_up_spread_bps")
        self.step_down_spread_bps = to_decimal(self.step_down_spread_bps, "step_down_spread_bps")
        self.status = SptStatus.from_str(self.status)

        if self.measured_value is not None:
            self.measured_value = to_decimal(self.measured_value, "measured_value")
        if self.verification_date is not None:
            self.verification_date = to_date(self.verification_date, "verification_date")

        if self.verification_deadline < self.observation_date:
            raise EsgValidationError(
                f"Prazo de verificação ({self.verification_deadline}) não pode ser anterior à data de observação ({self.observation_date})."
            )
        if self.step_up_spread_bps <= Decimal("0"):
            raise EsgValidationError(f"Step-Up spread deve ser estritamente positivo ({self.step_up_spread_bps} bps fornecido).")


@dataclass
class CouponCashflowItem:
    """Fluxo financeiro de cupom detalhado por período de amortização ou juros."""
    period_index: int
    start_date: date
    end_date: date
    days: int
    outstanding_principal: Decimal
    base_rate_pct: Decimal
    step_up_spread_pct: Decimal
    effective_rate_pct: Decimal
    base_interest_amount: Decimal
    step_up_penalty_amount: Decimal
    total_interest_payable: Decimal


@dataclass
class AllocationReport:
    """Relatório oficial de alocação de recursos de Green Bond (ICMA Use of Proceeds Pillar 4)."""
    asset_code: str
    report_date: date
    net_proceeds: Decimal
    allocated_proceeds: Decimal
    unallocated_proceeds: Decimal
    allocation_ratio_pct: Decimal
    sector_breakdown: Dict[str, Dict[str, Any]]
    projects_summary: List[Dict[str, Any]]
    unallocated_management: Dict[str, Any]
    is_fully_allocated: bool
    status: str


@dataclass
class ImpactReport:
    """Relatório oficial de impacto ambiental gerado pelos recursos do Green Bond."""
    asset_code: str
    report_date: date
    total_allocated_brl: Decimal
    metrics_aggregated: Dict[str, Decimal]
    carbon_efficiency_tco2e_per_m_brl: Decimal
    impact_by_sector: Dict[str, Dict[str, Any]]
    projects_detail: List[Dict[str, Any]]


@dataclass
class SlbProgressReport:
    """Relatório oficial de progresso de metas de sustentabilidade (SLB)."""
    asset_code: str
    report_date: date
    kpi_summaries: List[Dict[str, Any]]
    spt_summaries: List[Dict[str, Any]]
    step_up_status: Dict[str, Any]
    overall_compliance: str


@dataclass
class B3EsgCertificationDossier:
    """Dossiê de certificação e selo ESG emitido pelo Balcão NoMe B3."""
    asset_code: str
    isin: str
    issuer_name: str
    issuer_cnpj: str
    instrument_type: str
    certification_status: EsgCertificationStatus
    seal_active: bool
    spo_summary: Optional[Dict[str, Any]]
    audit_trail_hash: str
    issue_timestamp: datetime


@dataclass
class EsgBondEmission:
    """Representação central da emissão de título de dívida ESG no Balcão NoMe B3."""
    asset_code: str
    isin: str
    issuer_name: str
    issuer_cnpj: str
    instrument_type: EsgInstrumentType
    issue_date: date
    maturity_date: date
    nominal_unit_value: Decimal
    issued_quantity: Decimal
    issuance_costs: Decimal
    base_coupon_rate: Decimal  # ex: 0.06500000 para 6.50% a.a.
    coupon_benchmark: CouponBenchmark
    payment_frequency: PaymentFrequency
    unallocated_policy: UnallocatedProceedsPolicy = UnallocatedProceedsPolicy.TREASURY_BONDS
    certification_status: EsgCertificationStatus = EsgCertificationStatus.CERTIFIED_ACTIVE
    spo: Optional[SecondPartyOpinion] = None
    projects: List[EligibleGreenProject] = field(default_factory=list)
    kpis: Dict[str, KpiDefinition] = field(default_factory=dict)
    spts: List[SptTarget] = field(default_factory=list)
    step_up_applied: bool = False
    step_up_effective_date: Optional[date] = None
    step_up_reason: Optional[str] = None

    def __post_init__(self) -> None:
        self.isin = validate_isin(self.isin, "isin")
        self.issuer_cnpj = validate_cnpj(self.issuer_cnpj, "issuer_cnpj")
        self.instrument_type = EsgInstrumentType.from_str(self.instrument_type)
        self.issue_date = to_date(self.issue_date, "issue_date")
        self.maturity_date = to_date(self.maturity_date, "maturity_date")
        self.nominal_unit_value = to_decimal(self.nominal_unit_value, "nominal_unit_value")
        self.issued_quantity = to_decimal(self.issued_quantity, "issued_quantity")
        self.issuance_costs = to_decimal(self.issuance_costs, "issuance_costs")
        self.base_coupon_rate = to_decimal(self.base_coupon_rate, "base_coupon_rate")
        self.coupon_benchmark = CouponBenchmark.from_str(self.coupon_benchmark)
        self.payment_frequency = PaymentFrequency.from_str(self.payment_frequency)
        self.certification_status = EsgCertificationStatus.from_str(self.certification_status)

        if self.maturity_date <= self.issue_date:
            raise EsgValidationError(f"Data de vencimento ({self.maturity_date}) deve ser posterior à emissão ({self.issue_date}).")
        if self.nominal_unit_value <= Decimal("0"):
            raise EsgValidationError("Valor nominal unitário deve ser estritamente positivo.")
        if self.issued_quantity <= Decimal("0"):
            raise EsgValidationError("Quantidade emitida deve ser estritamente positiva.")
        if self.issuance_costs < Decimal("0"):
            raise EsgValidationError("Custos de emissão não podem ser negativos.")
        if self.issuance_costs >= self.gross_proceeds:
            raise EsgValidationError("Custos de emissão não podem exceder ou igualar a captação bruta.")

    @property
    def gross_proceeds(self) -> Decimal:
        """Volume bruto captado = Quantidade * VNE."""
        return round_cents(self.nominal_unit_value * self.issued_quantity)

    @property
    def net_proceeds(self) -> Decimal:
        """Volume líquido disponível para projetos = Volume Bruto - Custos de Emissão."""
        return round_cents(self.gross_proceeds - self.issuance_costs)

    @property
    def total_allocated_proceeds(self) -> Decimal:
        """Total de recursos já alocados em projetos elegíveis."""
        total = sum((p.allocated_amount for p in self.projects), Decimal("0"))
        return round_cents(total)

    @property
    def total_unallocated_proceeds(self) -> Decimal:
        """Recursos temporariamente não alocados (sob custódia de liquidez)."""
        unallocated = self.net_proceeds - self.total_allocated_proceeds
        return round_cents(unallocated)

    @property
    def allocation_ratio_pct(self) -> Decimal:
        """Percentual de recursos já alocados em relação aos recursos líquidos."""
        if self.net_proceeds == Decimal("0"):
            return Decimal("0.0000")
        pct = (self.total_allocated_proceeds / self.net_proceeds) * Decimal("100")
        return round_percent(pct)

    @property
    def is_fully_allocated(self) -> bool:
        """Indica se 100% dos recursos líquidos foram alocados."""
        return self.total_allocated_proceeds >= self.net_proceeds

    @property
    def current_coupon_rate(self) -> Decimal:
        """Taxa anual ativa do cupom, considerando penalidade de Step-Up caso acionada."""
        if not self.step_up_applied:
            return round_rate(self.base_coupon_rate)

        # Encontra o maior spread de step-up dentre as metas descumpridas
        missed_spts = [
            s for s in self.spts
            if s.status in (SptStatus.MISSED, SptStatus.DEFAULTED_REPORTING)
        ]
        if not missed_spts:
            return round_rate(self.base_coupon_rate)

        # O maior spread de penalidade aplicável (em bps convertido para decimal)
        max_bps = max((s.step_up_spread_bps for s in missed_spts), default=Decimal("0"))
        additional_rate = max_bps * BPS_TO_DECIMAL
        return round_rate(self.base_coupon_rate + additional_rate)


# ---------------------------------------------------------------------------
# Motor Central: EsgGreenBondsEngine
# ---------------------------------------------------------------------------

class EsgGreenBondsEngine:
    """Motor de Registro, Certificação de Taxonomia e Monitoramento ESG no Balcão NoMe B3 (FL-065)."""

    def __init__(self) -> None:
        self.emissions: Dict[str, EsgBondEmission] = {}

    # -----------------------------------------------------------------------
    # Carga e Serialização JSON
    # -----------------------------------------------------------------------

    @classmethod
    def load_from_json(cls, file_path: Union[str, Path]) -> EsgGreenBondsEngine:
        """Carrega e instancia o motor a partir da fixture oficial JSON."""
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Arquivo de fixture ESG não encontrado em: {path}")

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        engine = cls()
        emissions_data = data.get("emissions", [])

        for item in emissions_data:
            # Parse SPO if present
            spo_obj: Optional[SecondPartyOpinion] = None
            if item.get("spo"):
                s_data = item["spo"]
                spo_obj = SecondPartyOpinion(
                    spo_id=s_data["spo_id"],
                    verifier_name=s_data["verifier_name"],
                    verifier_cnpj_or_id=s_data["verifier_cnpj_or_id"],
                    standard_framework=s_data["standard_framework"],
                    opinion_date=to_date(s_data["opinion_date"]),
                    expiry_date=to_date(s_data["expiry_date"]) if s_data.get("expiry_date") else None,
                    status=SPOStatus.from_str(s_data["status"]),
                    report_hash=s_data.get("report_hash", "")
                )

            # Instancia emissão
            emission = EsgBondEmission(
                asset_code=item["asset_code"],
                isin=item["isin"],
                issuer_name=item["issuer_name"],
                issuer_cnpj=item["issuer_cnpj"],
                instrument_type=EsgInstrumentType.from_str(item["instrument_type"]),
                issue_date=to_date(item["issue_date"]),
                maturity_date=to_date(item["maturity_date"]),
                nominal_unit_value=to_decimal(item["nominal_unit_value"]),
                issued_quantity=to_decimal(item["issued_quantity"]),
                issuance_costs=to_decimal(item.get("issuance_costs", "0")),
                base_coupon_rate=to_decimal(item["base_coupon_rate"]),
                coupon_benchmark=CouponBenchmark.from_str(item["coupon_benchmark"]),
                payment_frequency=PaymentFrequency.from_str(item["payment_frequency"]),
                unallocated_policy=UnallocatedProceedsPolicy.from_str(item.get("unallocated_policy", "TREASURY_BONDS")),
                certification_status=EsgCertificationStatus.from_str(item.get("certification_status", "CERTIFIED_ACTIVE")),
                spo=spo_obj,
                step_up_applied=bool(item.get("step_up_applied", False)),
                step_up_effective_date=to_date(item["step_up_effective_date"]) if item.get("step_up_effective_date") else None,
                step_up_reason=item.get("step_up_reason")
            )

            # Carrega projetos alocados
            for p_data in item.get("projects", []):
                project = EligibleGreenProject(
                    project_id=p_data["project_id"],
                    name=p_data["name"],
                    sector=GreenTaxonomySector.from_str(p_data["sector"]),
                    description=p_data["description"],
                    total_project_cost=to_decimal(p_data["total_project_cost"]),
                    allocated_amount=to_decimal(p_data["allocated_amount"]),
                    allocation_date=to_date(p_data["allocation_date"]),
                    environmental_impact_metric=p_data["environmental_impact_metric"],
                    environmental_impact_value=to_decimal(p_data["environmental_impact_value"]),
                    location_uf=p_data["location_uf"],
                    verification_doc_ref=p_data["verification_doc_ref"]
                )
                emission.projects.append(project)

            # Carrega KPIs
            for k_data in item.get("kpis", []):
                kpi = KpiDefinition(
                    kpi_id=k_data["kpi_id"],
                    name=k_data["name"],
                    unit_of_measure=k_data["unit_of_measure"],
                    baseline_year=int(k_data["baseline_year"]),
                    baseline_value=to_decimal(k_data["baseline_value"]),
                    methodology=k_data["methodology"],
                    direction=KpiDirection.from_str(k_data["direction"])
                )
                emission.kpis[kpi.kpi_id] = kpi

            # Carrega SPTs
            for s_data in item.get("spts", []):
                spt = SptTarget(
                    spt_id=s_data["spt_id"],
                    kpi_id=s_data["kpi_id"],
                    target_year=int(s_data["target_year"]),
                    observation_date=to_date(s_data["observation_date"]),
                    verification_deadline=to_date(s_data["verification_deadline"]),
                    target_value=to_decimal(s_data["target_value"]),
                    step_up_spread_bps=to_decimal(s_data["step_up_spread_bps"]),
                    step_down_spread_bps=to_decimal(s_data.get("step_down_spread_bps", "0")),
                    measured_value=to_decimal(s_data["measured_value"]) if s_data.get("measured_value") is not None else None,
                    verification_date=to_date(s_data["verification_date"]) if s_data.get("verification_date") else None,
                    auditor_name=s_data.get("auditor_name"),
                    auditor_report_ref=s_data.get("auditor_report_ref"),
                    status=SptStatus.from_str(s_data.get("status", "PENDING"))
                )
                emission.spts.append(spt)

            engine.emissions[emission.asset_code] = emission

        return engine

    def save_to_json(self, file_path: Union[str, Path]) -> None:
        """Serializa o estado atual do catálogo de emissões ESG em formato JSON determinístico."""
        path = Path(file_path)
        payload: Dict[str, Any] = {
            "description": "B3 Balcão NoMe - Catálogo de Registro e Certificação de Green Bonds e SLBs (FL-065)",
            "version": "1.0.0",
            "as_of": date.today().isoformat(),
            "emissions": []
        }

        for em in self.emissions.values():
            em_dict: Dict[str, Any] = {
                "asset_code": em.asset_code,
                "isin": em.isin,
                "issuer_name": em.issuer_name,
                "issuer_cnpj": em.issuer_cnpj,
                "instrument_type": em.instrument_type.value,
                "issue_date": em.issue_date.isoformat(),
                "maturity_date": em.maturity_date.isoformat(),
                "nominal_unit_value": str(em.nominal_unit_value),
                "issued_quantity": str(em.issued_quantity),
                "issuance_costs": str(em.issuance_costs),
                "gross_proceeds": str(em.gross_proceeds),
                "net_proceeds": str(em.net_proceeds),
                "base_coupon_rate": str(em.base_coupon_rate),
                "current_coupon_rate": str(em.current_coupon_rate),
                "coupon_benchmark": em.coupon_benchmark.value,
                "payment_frequency": em.payment_frequency.value,
                "unallocated_policy": em.unallocated_policy.value,
                "certification_status": em.certification_status.value,
                "step_up_applied": em.step_up_applied,
                "step_up_effective_date": em.step_up_effective_date.isoformat() if em.step_up_effective_date else None,
                "step_up_reason": em.step_up_reason,
                "spo": asdict(em.spo) if em.spo else None,
                "projects": [
                    {
                        "project_id": p.project_id,
                        "name": p.name,
                        "sector": p.sector.value,
                        "description": p.description,
                        "total_project_cost": str(p.total_project_cost),
                        "allocated_amount": str(p.allocated_amount),
                        "allocation_date": p.allocation_date.isoformat(),
                        "environmental_impact_metric": p.environmental_impact_metric,
                        "environmental_impact_value": str(p.environmental_impact_value),
                        "location_uf": p.location_uf,
                        "verification_doc_ref": p.verification_doc_ref
                    }
                    for p in em.projects
                ],
                "kpis": [
                    {
                        "kpi_id": k.kpi_id,
                        "name": k.name,
                        "unit_of_measure": k.unit_of_measure,
                        "baseline_year": k.baseline_year,
                        "baseline_value": str(k.baseline_value),
                        "methodology": k.methodology,
                        "direction": k.direction.value
                    }
                    for k in em.kpis.values()
                ],
                "spts": [
                    {
                        "spt_id": s.spt_id,
                        "kpi_id": s.kpi_id,
                        "target_year": s.target_year,
                        "observation_date": s.observation_date.isoformat(),
                        "verification_deadline": s.verification_deadline.isoformat(),
                        "target_value": str(s.target_value),
                        "step_up_spread_bps": str(s.step_up_spread_bps),
                        "step_down_spread_bps": str(s.step_down_spread_bps),
                        "measured_value": str(s.measured_value) if s.measured_value is not None else None,
                        "verification_date": s.verification_date.isoformat() if s.verification_date else None,
                        "auditor_name": s.auditor_name,
                        "auditor_report_ref": s.auditor_report_ref,
                        "status": s.status.value
                    }
                    for s in em.spts
                ]
            }
            # Remove campos de data nulos na serialização de SPO se houver
            if em_dict["spo"] and em_dict["spo"].get("opinion_date"):
                em_dict["spo"]["opinion_date"] = em.spo.opinion_date.isoformat()
            if em_dict["spo"] and em_dict["spo"].get("expiry_date"):
                em_dict["spo"]["expiry_date"] = em.spo.expiry_date.isoformat()
            if em_dict["spo"] and isinstance(em_dict["spo"].get("status"), SPOStatus):
                em_dict["spo"]["status"] = em.spo.status.value

            payload["emissions"].append(em_dict)

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    # -----------------------------------------------------------------------
    # Operações de Registro e Governança de Emissões
    # -----------------------------------------------------------------------

    def get_emission(self, asset_code: str) -> EsgBondEmission:
        """Obtém a emissão cadastrada pelo código de ativo."""
        code = asset_code.strip().upper()
        if code not in self.emissions:
            raise EsgValidationError(f"Ativo ESG '{asset_code}' não encontrado no Balcão NoMe B3.")
        return self.emissions[code]

    def register_green_bond(
        self,
        asset_code: str,
        isin: str,
        issuer_name: str,
        issuer_cnpj: str,
        issue_date: date,
        maturity_date: date,
        nominal_unit_value: Decimal,
        issued_quantity: Decimal,
        base_coupon_rate: Decimal,
        coupon_benchmark: CouponBenchmark,
        payment_frequency: PaymentFrequency,
        issuance_costs: Decimal = Decimal("0"),
        spo: Optional[SecondPartyOpinion] = None,
        unallocated_policy: UnallocatedProceedsPolicy = UnallocatedProceedsPolicy.TREASURY_BONDS,
    ) -> EsgBondEmission:
        """Registra um Green Bond (Use of Proceeds) com verificação de elegibilidade."""
        code = asset_code.strip().upper()
        if code in self.emissions:
            raise EsgValidationError(f"Código de ativo ESG '{code}' já registrado no sistema.")

        emission = EsgBondEmission(
            asset_code=code,
            isin=isin,
            issuer_name=issuer_name,
            issuer_cnpj=issuer_cnpj,
            instrument_type=EsgInstrumentType.GREEN_BOND,
            issue_date=issue_date,
            maturity_date=maturity_date,
            nominal_unit_value=nominal_unit_value,
            issued_quantity=issued_quantity,
            issuance_costs=issuance_costs,
            base_coupon_rate=base_coupon_rate,
            coupon_benchmark=coupon_benchmark,
            payment_frequency=payment_frequency,
            unallocated_policy=unallocated_policy,
            spo=spo
        )

        self.emissions[code] = emission
        return emission

    def register_slb(
        self,
        asset_code: str,
        isin: str,
        issuer_name: str,
        issuer_cnpj: str,
        issue_date: date,
        maturity_date: date,
        nominal_unit_value: Decimal,
        issued_quantity: Decimal,
        base_coupon_rate: Decimal,
        coupon_benchmark: CouponBenchmark,
        payment_frequency: PaymentFrequency,
        issuance_costs: Decimal = Decimal("0"),
        spo: Optional[SecondPartyOpinion] = None,
    ) -> EsgBondEmission:
        """Registra um Sustainability-Linked Bond (SLB) com metas atreladas ao cupom."""
        code = asset_code.strip().upper()
        if code in self.emissions:
            raise EsgValidationError(f"Código de ativo ESG '{code}' já registrado no sistema.")

        emission = EsgBondEmission(
            asset_code=code,
            isin=isin,
            issuer_name=issuer_name,
            issuer_cnpj=issuer_cnpj,
            instrument_type=EsgInstrumentType.SUSTAINABILITY_LINKED_BOND,
            issue_date=issue_date,
            maturity_date=maturity_date,
            nominal_unit_value=nominal_unit_value,
            issued_quantity=issued_quantity,
            issuance_costs=issuance_costs,
            base_coupon_rate=base_coupon_rate,
            coupon_benchmark=coupon_benchmark,
            payment_frequency=payment_frequency,
            spo=spo
        )

        self.emissions[code] = emission
        return emission

    # -----------------------------------------------------------------------
    # Taxonomia Verde: Alocação de Recursos (Green Bond Use of Proceeds)
    # -----------------------------------------------------------------------

    def allocate_project(self, asset_code: str, project: EligibleGreenProject) -> None:
        """Aloca recursos de um Green Bond em um projeto elegível conforme a Taxonomia Verde.

        Aplica as regras de conservação:
        1. O ativo deve ser do tipo Use-of-Proceeds (GREEN_BOND, SOCIAL_BOND, etc.).
        2. A alocação no projeto não pode exceder o saldo não alocado da captação líquida.
        3. A alocação não pode exceder o custo total orçado do projeto.
        """
        emission = self.get_emission(asset_code)
        if emission.instrument_type == EsgInstrumentType.SUSTAINABILITY_LINKED_BOND:
            raise TaxonomyNonComplianceError(
                "Sustainability-Linked Bonds (SLB) financiam fins corporativos gerais; "
                "não utilizam mecanismo de alocação de projetos (Use of Proceeds)."
            )

        # Previne duplicação de ID de projeto dentro da mesma emissão
        if any(p.project_id == project.project_id for p in emission.projects):
            raise TaxonomyNonComplianceError(f"Projeto com ID '{project.project_id}' já alocado nesta emissão.")

        # Validação de capacidade da captação líquida
        available_unallocated = emission.total_unallocated_proceeds
        if project.allocated_amount > available_unallocated:
            raise TaxonomyNonComplianceError(
                f"Valor alocado no projeto (R$ {project.allocated_amount}) excede o saldo não alocado disponível "
                f"(R$ {available_unallocated}) da captação líquida de R$ {emission.net_proceeds}."
            )

        emission.projects.append(project)

    def deallocate_project(self, asset_code: str, project_id: str) -> EligibleGreenProject:
        """Remove a alocação de um projeto, retornando os recursos ao montante não alocado."""
        emission = self.get_emission(asset_code)
        target_idx: Optional[int] = None
        for i, p in enumerate(emission.projects):
            if p.project_id == project_id:
                target_idx = i
                break

        if target_idx is None:
            raise EsgValidationError(f"Projeto '{project_id}' não encontrado na emissão '{asset_code}'.")

        removed = emission.projects.pop(target_idx)
        return removed

    # -----------------------------------------------------------------------
    # Sustainability-Linked Bonds: Configuração de KPI e SPT
    # -----------------------------------------------------------------------

    def add_kpi_to_slb(self, asset_code: str, kpi: KpiDefinition) -> None:
        """Associa um indicador-chave de desempenho a uma emissão SLB."""
        emission = self.get_emission(asset_code)
        if emission.instrument_type != EsgInstrumentType.SUSTAINABILITY_LINKED_BOND:
            raise EsgValidationError("Indicadores de meta (KPI/SPT) são exclusivos de Sustainability-Linked Bonds (SLB).")

        if kpi.kpi_id in emission.kpis:
            raise EsgValidationError(f"KPI com ID '{kpi.kpi_id}' já associado a este título.")

        emission.kpis[kpi.kpi_id] = kpi

    def add_spt_target(self, asset_code: str, spt: SptTarget) -> None:
        """Cadastra uma meta SPT com gatilho de Step-Up associada a um KPI."""
        emission = self.get_emission(asset_code)
        if emission.instrument_type != EsgInstrumentType.SUSTAINABILITY_LINKED_BOND:
            raise EsgValidationError("Metas SPT com gatilho de Step-Up são exclusivas de SLBs.")

        if spt.kpi_id not in emission.kpis:
            raise EsgValidationError(f"KPI '{spt.kpi_id}' associado ao SPT não encontrado na emissão.")

        kpi = emission.kpis[spt.kpi_id]
        if spt.target_year <= kpi.baseline_year:
            raise EsgValidationError(
                f"Ano-alvo da meta SPT ({spt.target_year}) deve ser estritamente posterior ao ano-base do KPI ({kpi.baseline_year})."
            )

        if any(s.spt_id == spt.spt_id for s in emission.spts):
            raise EsgValidationError(f"Meta SPT com ID '{spt.spt_id}' já existe nesta emissão.")

        emission.spts.append(spt)

    # -----------------------------------------------------------------------
    # Monitoramento de Gatilho de Step-Up de Cupom
    # -----------------------------------------------------------------------

    def evaluate_spt(
        self,
        asset_code: str,
        spt_id: str,
        measured_value: Decimal,
        evaluation_date: date,
        auditor_name: str,
        auditor_report_ref: str,
    ) -> SptStatus:
        """Avalia formalmente a medição de uma meta SPT auditada e dispara ou não o Step-Up.

        Regras de Negócio B3:
        1. Se a direção for DECREASE (ex: emissões tCO2e): meta cumprida se measured_value <= target_value.
        2. Se a direção for INCREASE (ex: % renovável): meta cumprida se measured_value >= target_value.
        3. Se não cumprida: status torna-se MISSED e o Step-Up é disparado imediatamente a partir da data de observação.
        4. Se cumprida: status torna-se ACHIEVED, e o cupom segue em base_coupon_rate.
        """
        emission = self.get_emission(asset_code)
        spt = next((s for s in emission.spts if s.spt_id == spt_id), None)
        if not spt:
            raise StepUpTriggerError(f"Meta SPT '{spt_id}' não encontrada na emissão '{asset_code}'.")

        kpi = emission.kpis[spt.kpi_id]
        eval_dt = to_date(evaluation_date)
        measured = to_decimal(measured_value, "measured_value")

        spt.measured_value = measured
        spt.verification_date = eval_dt
        spt.auditor_name = auditor_name.strip()
        spt.auditor_report_ref = auditor_report_ref.strip()

        # Determina cumprimento
        achieved = False
        if kpi.direction == KpiDirection.DECREASE:
            achieved = (measured <= spt.target_value)
        elif kpi.direction == KpiDirection.INCREASE:
            achieved = (measured >= spt.target_value)

        if achieved:
            spt.status = SptStatus.ACHIEVED
            # Se todas as metas avaliadas estiverem ACHIEVED, desativa Step-Up
            missed_remaining = [
                s for s in emission.spts
                if s.status in (SptStatus.MISSED, SptStatus.DEFAULTED_REPORTING)
            ]
            if not missed_remaining:
                emission.step_up_applied = False
                emission.step_up_effective_date = None
                emission.step_up_reason = None
        else:
            spt.status = SptStatus.MISSED
            emission.step_up_applied = True
            emission.step_up_effective_date = spt.observation_date
            emission.step_up_reason = (
                f"Meta SPT '{spt.spt_id}' não atingida. Medido: {measured} {kpi.unit_of_measure} "
                f"vs Alvo: {spt.target_value} {kpi.unit_of_measure}. Acréscimo de +{spt.step_up_spread_bps} bps."
            )

        return spt.status

    def evaluate_reporting_deadline(self, asset_code: str, as_of_date: date) -> List[str]:
        """Avalia se prazos regulamentares de envio de laudo expiraram sem comprovação.

        Conforme ICMA SLBP e Regras NoMe B3: A inobservância do prazo máximo de entrega do laudo
        audita equivale a inadimplemento de sustentabilidade (DEFAULTED_REPORTING), acionando
        o gatilho de Step-Up automaticamente para proteção dos debenturistas.
        """
        emission = self.get_emission(asset_code)
        as_of = to_date(as_of_date)
        triggered_spts: List[str] = []

        for spt in emission.spts:
            if spt.status == SptStatus.PENDING and as_of > spt.verification_deadline:
                spt.status = SptStatus.DEFAULTED_REPORTING
                emission.step_up_applied = True
                emission.step_up_effective_date = spt.observation_date
                emission.step_up_reason = (
                    f"Prazo de auditoria expirado em {spt.verification_deadline} sem laudo independente entregue à B3. "
                    f"Step-Up penalizador acionado (+{spt.step_up_spread_bps} bps)."
                )
                triggered_spts.append(spt.spt_id)

        return triggered_spts

    # -----------------------------------------------------------------------
    # Cálculo Financeiro e Projeção de Fluxo de Cupom
    # -----------------------------------------------------------------------

    def calculate_coupon_cashflow(
        self,
        asset_code: str,
        start_date: date,
        end_date: date,
        outstanding_principal: Decimal
    ) -> CouponCashflowItem:
        """Calcula o montante de cupom devido em um período, separando a base e a sobretaxa do Step-Up."""
        emission = self.get_emission(asset_code)
        s_date = to_date(start_date)
        e_date = to_date(end_date)
        principal = to_decimal(outstanding_principal, "outstanding_principal")

        if e_date <= s_date:
            raise EsgValidationError("Data de término deve ser posterior à data de início do período de cupom.")
        if principal <= Decimal("0"):
            raise EsgValidationError("Saldo devedor principal deve ser estritamente positivo.")

        days = (e_date - s_date).days
        year_frac = Decimal(days) / DAYS_PER_YEAR_CONVENTION

        base_rate = emission.base_coupon_rate
        base_interest = round_cents(principal * base_rate * year_frac)

        step_up_spread = Decimal("0")
        step_up_penalty = Decimal("0")

        if emission.step_up_applied and emission.step_up_effective_date and e_date >= emission.step_up_effective_date:
            step_up_spread = (emission.current_coupon_rate - base_rate)
            step_up_penalty = round_cents(principal * step_up_spread * year_frac)

        total_interest = base_interest + step_up_penalty
        effective_rate = base_rate + step_up_spread

        return CouponCashflowItem(
            period_index=1,
            start_date=s_date,
            end_date=e_date,
            days=days,
            outstanding_principal=principal,
            base_rate_pct=round_percent(base_rate * Decimal("100")),
            step_up_spread_pct=round_percent(step_up_spread * Decimal("100")),
            effective_rate_pct=round_percent(effective_rate * Decimal("100")),
            base_interest_amount=base_interest,
            step_up_penalty_amount=step_up_penalty,
            total_interest_payable=total_interest
        )

    # -----------------------------------------------------------------------
    # Relatórios Oficiais de Conformidade ESG
    # -----------------------------------------------------------------------

    def generate_allocation_report(self, asset_code: str, as_of_date: Optional[date] = None) -> AllocationReport:
        """Gera o Relatório de Alocação de Recursos (ICMA Green Bond Principles Pillar 4)."""
        emission = self.get_emission(asset_code)
        report_dt = to_date(as_of_date) if as_of_date else date.today()

        sector_breakdown: Dict[str, Dict[str, Any]] = {}
        projects_summary: List[Dict[str, Any]] = []

        total_allocated = emission.total_allocated_proceeds
        for p in emission.projects:
            sec_name = p.sector.value
            if sec_name not in sector_breakdown:
                sector_breakdown[sec_name] = {
                    "total_allocated": Decimal("0.00"),
                    "project_count": 0,
                    "share_of_allocated_pct": Decimal("0.0000")
                }
            sector_breakdown[sec_name]["total_allocated"] += p.allocated_amount
            sector_breakdown[sec_name]["project_count"] += 1

            projects_summary.append({
                "project_id": p.project_id,
                "name": p.name,
                "sector": p.sector.value,
                "allocated_amount": str(p.allocated_amount),
                "total_project_cost": str(p.total_project_cost),
                "environmental_metric": p.environmental_impact_metric,
                "environmental_value": str(p.environmental_impact_value),
                "location_uf": p.location_uf
            })

        for sec in sector_breakdown.values():
            if total_allocated > Decimal("0"):
                sec["share_of_allocated_pct"] = round_percent((sec["total_allocated"] / total_allocated) * Decimal("100"))
            sec["total_allocated"] = str(round_cents(sec["total_allocated"]))

        status_str = "TOTALMENTE_ALOCADO" if emission.is_fully_allocated else "PARCIALMENTE_ALOCADO"

        return AllocationReport(
            asset_code=emission.asset_code,
            report_date=report_dt,
            net_proceeds=emission.net_proceeds,
            allocated_proceeds=emission.total_allocated_proceeds,
            unallocated_proceeds=emission.total_unallocated_proceeds,
            allocation_ratio_pct=emission.allocation_ratio_pct,
            sector_breakdown=sector_breakdown,
            projects_summary=projects_summary,
            unallocated_management={
                "policy": emission.unallocated_policy.value,
                "temporary_vehicle": "TÍTULOS PÚBLICOS FEDERAIS / CDB VERDE SELIC D0",
                "liquidity": "IMEDIATA"
            },
            is_fully_allocated=emission.is_fully_allocated,
            status=status_str
        )

    def generate_impact_report(self, asset_code: str, as_of_date: Optional[date] = None) -> ImpactReport:
        """Gera o Relatório de Impacto Ambiental consolidado (ICMA GBP Pillar 4)."""
        emission = self.get_emission(asset_code)
        report_dt = to_date(as_of_date) if as_of_date else date.today()

        metrics_aggregated: Dict[str, Decimal] = {}
        impact_by_sector: Dict[str, Dict[str, Any]] = {}
        projects_detail: List[Dict[str, Any]] = []

        for p in emission.projects:
            metric = p.environmental_impact_metric
            metrics_aggregated[metric] = metrics_aggregated.get(metric, Decimal("0")) + p.environmental_impact_value

            sec = p.sector.value
            if sec not in impact_by_sector:
                impact_by_sector[sec] = {
                    "allocated_amount": Decimal("0.00"),
                    "metrics": {}
                }
            impact_by_sector[sec]["allocated_amount"] += p.allocated_amount
            impact_by_sector[sec]["metrics"][metric] = (
                impact_by_sector[sec]["metrics"].get(metric, Decimal("0")) + p.environmental_impact_value
            )

            projects_detail.append({
                "project_id": p.project_id,
                "sector": p.sector.value,
                "metric": p.environmental_impact_metric,
                "value": str(round_impact(p.environmental_impact_value)),
                "allocated_amount": str(p.allocated_amount)
            })

        # Calcula eficiência de carbono (tCO2e evitadas por milhão de reais alocados)
        total_co2 = metrics_aggregated.get("tCO2e_avoided_per_year", Decimal("0"))
        total_m_brl = emission.total_allocated_proceeds / Decimal("1000000")
        carbon_efficiency = Decimal("0.000")
        if total_m_brl > Decimal("0"):
            carbon_efficiency = round_impact(total_co2 / total_m_brl)

        # Converte valores numéricos para serialização
        for sec in impact_by_sector.values():
            sec["allocated_amount"] = str(round_cents(sec["allocated_amount"]))
            sec["metrics"] = {k: str(round_impact(v)) for k, v in sec["metrics"].items()}

        formatted_metrics = {k: round_impact(v) for k, v in metrics_aggregated.items()}

        return ImpactReport(
            asset_code=emission.asset_code,
            report_date=report_dt,
            total_allocated_brl=emission.total_allocated_proceeds,
            metrics_aggregated=formatted_metrics,
            carbon_efficiency_tco2e_per_m_brl=carbon_efficiency,
            impact_by_sector=impact_by_sector,
            projects_detail=projects_detail
        )

    def generate_slb_progress_report(self, asset_code: str, as_of_date: Optional[date] = None) -> SlbProgressReport:
        """Gera o Relatório de Progresso e Desempenho de Metas ESG para Sustainability-Linked Bonds."""
        emission = self.get_emission(asset_code)
        report_dt = to_date(as_of_date) if as_of_date else date.today()

        kpi_summaries: List[Dict[str, Any]] = [
            {
                "kpi_id": k.kpi_id,
                "name": k.name,
                "unit": k.unit_of_measure,
                "baseline_year": k.baseline_year,
                "baseline_value": str(k.baseline_value),
                "direction": k.direction.value,
                "methodology": k.methodology
            }
            for k in emission.kpis.values()
        ]

        spt_summaries: List[Dict[str, Any]] = []
        for s in emission.spts:
            kpi = emission.kpis.get(s.kpi_id)
            progress_pct = None
            if s.measured_value is not None and kpi is not None:
                # Calcula % de avanço rumo à meta
                delta_total = abs(kpi.baseline_value - s.target_value)
                if delta_total > Decimal("0"):
                    delta_achieved = (
                        (kpi.baseline_value - s.measured_value)
                        if kpi.direction == KpiDirection.DECREASE
                        else (s.measured_value - kpi.baseline_value)
                    )
                    progress_pct = round_percent((delta_achieved / delta_total) * Decimal("100"))

            spt_summaries.append({
                "spt_id": s.spt_id,
                "kpi_id": s.kpi_id,
                "target_year": s.target_year,
                "observation_date": s.observation_date.isoformat(),
                "verification_deadline": s.verification_deadline.isoformat(),
                "target_value": str(s.target_value),
                "measured_value": str(s.measured_value) if s.measured_value is not None else None,
                "progress_percentage": str(progress_pct) if progress_pct is not None else "N/A",
                "step_up_spread_bps": str(s.step_up_spread_bps),
                "status": s.status.value,
                "auditor": s.auditor_name
            })

        step_up_info = {
            "step_up_applied": emission.step_up_applied,
            "effective_annual_coupon_rate": str(round_percent(emission.current_coupon_rate * Decimal("100"))) + "%",
            "base_annual_coupon_rate": str(round_percent(emission.base_coupon_rate * Decimal("100"))) + "%",
            "effective_date": emission.step_up_effective_date.isoformat() if emission.step_up_effective_date else None,
            "reason": emission.step_up_reason
        }

        overall = "COMPLIANT"
        if any(s.status == SptStatus.MISSED for s in emission.spts):
            overall = "TARGET_MISSED_STEP_UP_ACTIVE"
        elif any(s.status == SptStatus.DEFAULTED_REPORTING for s in emission.spts):
            overall = "AUDIT_DEFAULT_STEP_UP_ACTIVE"
        elif all(s.status == SptStatus.ACHIEVED for s in emission.spts) and emission.spts:
            overall = "ALL_TARGETS_ACHIEVED"

        return SlbProgressReport(
            asset_code=emission.asset_code,
            report_date=report_dt,
            kpi_summaries=kpi_summaries,
            spt_summaries=spt_summaries,
            step_up_status=step_up_info,
            overall_compliance=overall
        )

    def generate_b3_esg_certification_dossier(self, asset_code: str) -> B3EsgCertificationDossier:
        """Emite o Dossiê Oficial de Certificação ESG com hash criptográfico SHA-256."""
        emission = self.get_emission(asset_code)

        # Monta payload para hash de autenticidade da trilha de auditoria
        raw_hash_data = (
            f"{emission.asset_code}:{emission.isin}:{emission.issuer_cnpj}:"
            f"{emission.instrument_type.value}:{emission.gross_proceeds}:{emission.net_proceeds}:"
            f"{emission.certification_status.value}:{len(emission.projects)}:{len(emission.spts)}"
        )
        audit_hash = hashlib.sha256(raw_hash_data.encode("utf-8")).hexdigest()

        seal_active = (emission.certification_status == EsgCertificationStatus.CERTIFIED_ACTIVE)

        spo_sum: Optional[Dict[str, Any]] = None
        if emission.spo:
            spo_sum = {
                "spo_id": emission.spo.spo_id,
                "verifier": emission.spo.verifier_name,
                "framework": emission.spo.standard_framework,
                "status": emission.spo.status.value,
                "opinion_date": emission.spo.opinion_date.isoformat()
            }

        return B3EsgCertificationDossier(
            asset_code=emission.asset_code,
            isin=emission.isin,
            issuer_name=emission.issuer_name,
            issuer_cnpj=emission.issuer_cnpj,
            instrument_type=emission.instrument_type.value,
            certification_status=emission.certification_status,
            seal_active=seal_active,
            spo_summary=spo_sum,
            audit_trail_hash=audit_hash,
            issue_timestamp=datetime.now(timezone.utc)
        )

    # -----------------------------------------------------------------------
    # Verificação Formal de Invariantes de Mercado e Custódia ESG (Proof Laws)
    # -----------------------------------------------------------------------

    def verify_invariants(self, asset_code: str) -> bool:
        """Verifica formalmente o conjunto de Leis Invariantes de Conservação ESG no Balcão NoMe B3.

        Leis Invariantes Verificadas:
        1. Lei da Conservação de Recursos (Use of Proceeds):
           `Net_Proceeds == Total_Allocated + Total_Unallocated` (centavo a centavo).
        2. Lei da Não-Sobrealocação:
           `Total_Allocated <= Net_Proceeds` (não permite alocação excedendo os recursos líquidos captados).
        3. Lei da Finitude de Custo de Projeto:
           Para cada projeto `p`, `p.allocated_amount <= p.total_project_cost`.
        4. Lei do Determinismo de Gatilho e Cupom (SLB):
           Se houver meta `MISSED` ou `DEFAULTED_REPORTING`, `Current_Coupon == Base_Coupon + Step_Up_Spread`.
           Caso contrário, `Current_Coupon == Base_Coupon`.
        5. Lei da Não-Negatividade de Sobretaxa:
           `Step_Up_Spread >= 0` e `Step_Up_Penalty_Amount >= 0`.
        """
        emission = self.get_emission(asset_code)

        # Lei 1 e Lei 2: Use of Proceeds (apenas para Green/Social/Sustainable Bonds)
        if emission.instrument_type != EsgInstrumentType.SUSTAINABILITY_LINKED_BOND:
            conservation_sum = emission.total_allocated_proceeds + emission.total_unallocated_proceeds
            if conservation_sum != emission.net_proceeds:
                raise EsgConservationError(
                    f"Violação da Lei 1: Soma de Alocados (R$ {emission.total_allocated_proceeds}) + "
                    f"Não Alocados (R$ {emission.total_unallocated_proceeds}) = R$ {conservation_sum} "
                    f"difere dos Recursos Líquidos (R$ {emission.net_proceeds})."
                )

            if emission.total_allocated_proceeds > emission.net_proceeds:
                raise EsgConservationError(
                    f"Violação da Lei 2: Recursos alocados (R$ {emission.total_allocated_proceeds}) "
                    f"excedem a captação líquida total (R$ {emission.net_proceeds})."
                )

            # Lei 3: Custo de Projeto
            for p in emission.projects:
                if p.allocated_amount > p.total_project_cost:
                    raise EsgConservationError(
                        f"Violação da Lei 3: Projeto '{p.project_id}' tem alocação (R$ {p.allocated_amount}) "
                        f"maior que seu custo total (R$ {p.total_project_cost})."
                    )

        # Lei 4 e Lei 5: Regras de Cupom e Step-Up (SLB)
        if emission.instrument_type == EsgInstrumentType.SUSTAINABILITY_LINKED_BOND:
            has_missed = any(
                s.status in (SptStatus.MISSED, SptStatus.DEFAULTED_REPORTING)
                for s in emission.spts
            )
            if has_missed:
                if not emission.step_up_applied:
                    raise EsgConservationError("Violação da Lei 4: Meta SLB descumprida porém Step-Up não está ativado.")
                if emission.current_coupon_rate <= emission.base_coupon_rate:
                    raise EsgConservationError(
                        f"Violação da Lei 4: Cupom ativo ({emission.current_coupon_rate}) não reflete o "
                        f"acréscimo de Step-Up sobre a taxa base ({emission.base_coupon_rate})."
                    )
            else:
                if emission.step_up_applied:
                    raise EsgConservationError("Violação da Lei 4: Step-Up ativado indevidamente sem metas descumpridas.")
                if emission.current_coupon_rate != emission.base_coupon_rate:
                    raise EsgConservationError(
                        f"Violação da Lei 4: Cupom ativo ({emission.current_coupon_rate}) diverge da "
                        f"taxa base ({emission.base_coupon_rate}) quando não há penalidade."
                    )

        return True
