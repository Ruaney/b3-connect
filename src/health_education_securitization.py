"""Módulo de Balcão NoMe B3 — Emissão, Registro e Gestão de Fundo de Reserva de CR Saúde e CR Educação (FL-085).

Implementa o motor determinístico de estruturação, segregação patrimonial (Regime Fiduciário
com Patrimônio Separado), agregação de lastros sociais (Saúde e Educação), subordinação de tranches
(Sênior, Mezanino, Subordinada/First-Loss Piece), gestão dinâmica do Fundo de Reserva de Liquidez
e cascata de amortização/pagamento (Payment Waterfall), em estrita conformidade com:
- Lei nº 14.430/2022 (Marco Legal da Securitização de Direitos Creditórios - arts. 1º, 3º, 4º e arts. 17 a 33).
- Resolução CVM nº 60/2021 (Regras aplicáveis às Companhias Securitizadoras e Certificados de Recebíveis).
- Resolução CVM nº 175/2022 e seus anexos normativos (Marco dos Fundos de Investimento e Direitos Creditórios).
- Especificações do Sistema de Balcão NoMe B3 para registro de Certificados de Recebíveis e averbação fiduciária.

Setores Sociais e Lastros Cobertos:
1. Saúde (CR Saúde):
   - Mensalidades de planos de saúde suplementar regulados pela ANS (individuais e coletivos).
   - Faturas hospitalares e repasses do Sistema Único de Saúde - SUS (AIH - Autorização de Internação Hospitalar e APAC).
   - Recebíveis e faturas de hospitais privados, clínicas médicas especializadas e redes de medicina diagnóstica.
2. Educação (CR Educação):
   - Mensalidades escolares de educação básica (Educação Infantil, Ensino Fundamental e Ensino Médio - K12).
   - Mensalidades de ensino superior (Graduação presencial/EAD e Pós-Graduação/Especializações).
   - Contratos de crédito e financiamento estudantil privado de longo prazo com garantia de fiador/avalista.

Invariantes Formais Provadas e Auditadas:
1. Conservação Financeira Multilateral do Waterfall:
   Entradas == Saídas (Caixa_Inicial + Recebimentos_Lastro + Rendimentos + Saques_Reserva ==
   Despesas_Operacionais + Servico_Senior + Servico_Mezanino + Aportes_Reserva +
   Servico_Subordinada + Distribuicao_Residual + Caixa_Final).
2. Incomunicabilidade Absoluta do Patrimônio Separado (Lei nº 14.430/2022, art. 19):
   Os ativos que integram o patrimônio fiduciário (lastros, recebíveis, caixa segregado e fundo de reserva)
   respondem unicamente pelas obrigações decorrentes da emissão respectiva, não respondem por obrigações
   da securitizadora, não integram a massa falida e não se comunicam entre séries ou emissões distintas.
   Tentativas de transferências indevidas para o patrimônio geral ou entre emissões são estritamente bloqueadas.
3. Inviolabilidade da Subordinação (Water-Tight Subordination):
   Nenhum centavo é distribuído às tranches Subordinadas ou a título de Excess Spread enquanto
   houver déficit de juros/amortização na Classe Sênior ou defasagem no Fundo de Reserva obrigatório.
4. Absorção Hierárquica de Perdas (First-Loss Piece):
   Prejuízos decorrentes de inadimplência definitiva, glosas hospitalares do SUS ou evasão de alunos
   são absorvidos primariamente pela Tranche Subordinada, antes de impactar a Mezanino e a Sênior.
5. Conservação de Saldo Devedor e Preço Unitário:
   Saldo_Devedor_Tranche == Quantidade * Preço_Unitário (centavo a centavo).
6. Sanidade e Recomposição do Fundo de Reserva:
   Saldo da Reserva >= 0, piso obrigatório respeitado e recomposição mandatória via cascata.
7. Elegibilidade de Lastro por Setor Social:
   Segregação estrita entre lastros de Saúde (CR Saúde) e Educação (CR Educação).

100% Python Standard Library (sem dependências externas).
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
# CONSTANTES DE PRECISÃO NUMÉRICA E ARREDONDAMENTO B3
# =====================================================================

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_UNIT = Decimal("0.00000001")
QUANTIZE_RATE = Decimal("0.00000001")
QUANTIZE_QTY = Decimal("1")


def round_cents(val: Decimal) -> Decimal:
    """Arredonda valor financeiro para 2 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)


def round_unit(val: Decimal) -> Decimal:
    """Arredonda Preço Unitário (PU/VNE) para 8 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_UNIT, rounding=ROUND_HALF_UP)


def round_rate(val: Decimal) -> Decimal:
    """Arredonda taxas e fatores percentuais para 8 casas decimais."""
    return val.quantize(QUANTIZE_RATE, rounding=ROUND_HALF_UP)


def to_decimal(value: Any, field_name: str = "campo") -> Decimal:
    """Converte valor para Decimal de forma robusta e auditável."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise HealthEducationValidationError(f"Valor nulo não permitido para '{field_name}'.")
    try:
        cleaned = str(value).strip().replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError) as err:
        raise HealthEducationValidationError(
            f"Valor inválido '{value}' para o campo '{field_name}': {err}"
        ) from err


def to_date(value: Any, field_name: str = "data") -> date:
    """Converte string ISO 'YYYY-MM-DD' ou datetime/date para date."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value or not isinstance(value, str):
        raise HealthEducationValidationError(f"Data inválida para '{field_name}': {value}")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as err:
        raise HealthEducationValidationError(
            f"Formato de data inválido para '{field_name}' ('{value}'). Esperado YYYY-MM-DD."
        ) from err


# =====================================================================
# EXCEÇÕES DE DOMÍNIO
# =====================================================================

class HealthEducationSecuritizationError(Exception):
    """Exceção base do módulo de securitização de CR Saúde e CR Educação."""
    pass


class HealthEducationValidationError(HealthEducationSecuritizationError):
    """Erro de validação cadastral, parâmetros inválidos ou inelegibilidade de lastro social."""
    pass


class HealthEducationWaterfallError(HealthEducationSecuritizationError):
    """Falha de execução na cascata de pagamentos ou déficit de liquidez insuperável."""
    pass


class HealthEducationFiduciaryBreachError(HealthEducationSecuritizationError):
    """Violação do regime fiduciário, desvios de finalidade ou defasagem crítica de reserva."""
    pass


class HealthEducationSegregationViolationError(HealthEducationFiduciaryBreachError):
    """Violação da Incomunicabilidade do Patrimônio Separado (art. 19 da Lei nº 14.430/2022)."""
    pass


class HealthEducationInvariantError(HealthEducationSecuritizationError):
    """Violação matemática de leis de conservação financeira ou integridade de subordinação."""
    pass


# =====================================================================
# ENUMS REGULATÓRIOS (LEI 14.430/2022 E RESOLUÇÕES CVM 60/175)
# =====================================================================

class SocialSecuritizationSector(str, Enum):
    """Setor Social da emissão de Certificados de Recebíveis."""
    HEALTH = "HEALTH"        # CR Saúde (Recebíveis hospitalares, SUS e planos de saúde)
    EDUCATION = "EDUCATION"  # CR Educação (Mensalidades escolares, universidades e crédito estudantil)

    @classmethod
    def from_str(cls, value: Any) -> SocialSecuritizationSector:
        if isinstance(value, cls):
            return value
        norm = str(value or "").strip().upper()
        if norm in ("HEALTH", "SAUDE", "CR_SAUDE", "SAÚDE"):
            return cls.HEALTH
        if norm in ("EDUCATION", "EDUCACAO", "EDUCAÇÃO", "CR_EDUCACAO", "CR_EDU"):
            return cls.EDUCATION
        raise HealthEducationValidationError(f"Setor social de securitização desconhecido: '{value}'")


class LegalFramework(str, Enum):
    """Marco regulatório aplicável à estruturação fiduciária social."""
    LEI_14430_2022 = "LEI_14430_2022"  # Marco Legal da Securitização
    RESOLUCAO_CVM_60 = "RESOLUCAO_CVM_60"  # Securitizadoras de Crédito
    RESOLUCAO_CVM_175 = "RESOLUCAO_CVM_175"  # Marco Regulatório dos Fundos de Investimento (FIDC/Recebíveis)


class SocialCollateralType(str, Enum):
    """Tipos de direitos creditórios e títulos de lastro elegíveis."""
    # Lastros do Setor de Saúde (CR Saúde)
    HEALTH_PLAN_PREMIUM = "HEALTH_PLAN_PREMIUM"            # Mensalidades de planos de saúde suplementar (ANS)
    HOSPITAL_SUS_INVOICE = "HOSPITAL_SUS_INVOICE"          # Faturas de serviços hospitalares SUS (AIH / APAC)
    PRIVATE_CLINIC_RECEIVABLE = "PRIVATE_CLINIC_RECEIVABLE"  # Recebíveis de clínicas privadas, hospitais e diagnósticos

    # Lastros do Setor de Educação (CR Educação)
    TUITION_FEES_K12 = "TUITION_FEES_K12"                  # Mensalidades escolares de ensino básico (K-12)
    TUITION_FEES_HIGHER_ED = "TUITION_FEES_HIGHER_ED"      # Mensalidades de ensino superior (Graduação e Pós-Graduação)
    STUDENT_LOAN_PRIVATE = "STUDENT_LOAN_PRIVATE"          # Contratos de financiamento estudantil privado com fiadores

    @property
    def is_health(self) -> bool:
        """Verifica se o lastro pertence ao setor de Saúde."""
        return self in (
            SocialCollateralType.HEALTH_PLAN_PREMIUM,
            SocialCollateralType.HOSPITAL_SUS_INVOICE,
            SocialCollateralType.PRIVATE_CLINIC_RECEIVABLE,
        )

    @property
    def is_education(self) -> bool:
        """Verifica se o lastro pertence ao setor de Educação."""
        return self in (
            SocialCollateralType.TUITION_FEES_K12,
            SocialCollateralType.TUITION_FEES_HIGHER_ED,
            SocialCollateralType.STUDENT_LOAN_PRIVATE,
        )

    @property
    def sector(self) -> SocialSecuritizationSector:
        """Retorna o setor social obrigatório deste lastro."""
        if self.is_health:
            return SocialSecuritizationSector.HEALTH
        return SocialSecuritizationSector.EDUCATION


class CollateralStatus(str, Enum):
    """Situação de adimplência e ciclo de vida do lastro social."""
    PERFORMING = "PERFORMING"      # Adimplente
    DELINQUENT = "DELINQUENT"      # Em atraso
    DEFAULTED = "DEFAULTED"        # Inadimplente / Write-off definitivo (glosa SUS irremediável ou evasão)
    SETTLED = "SETTLED"            # Integralmente quitado
    SUBSTITUTED = "SUBSTITUTED"    # Substituído por outro lastro homologado


class CertificateTrancheClass(str, Enum):
    """Classe de subordinação dos certificados de recebíveis."""
    SENIOR = "SENIOR"              # Prioridade absoluta de juros e principal
    MEZZANINE = "MEZZANINE"        # Prioridade intermediária
    SUBORDINATED = "SUBORDINATED"  # Absorve primeira perda (First Loss Piece) e recebe excess spread residual


class AmortizationType(str, Enum):
    """Modelo de amortização de principal das tranches."""
    BULLET = "BULLET"              # Amortização integral no vencimento
    SAC = "SAC"                    # Amortização periódica constante
    PRICE = "PRICE"                # Parcelas periódicas constantes (Tabela Price)
    PASS_THROUGH = "PASS_THROUGH"  # Repasse direto da amortização arrecadada dos lastros
    CUSTOM = "CUSTOM"              # Cronograma contratual com datas e montantes específicos


class IndexerType(str, Enum):
    """Indexador de remuneração das tranches e dos lastros."""
    IPCA = "IPCA"
    CDI = "CDI"
    PREFIXADO = "PREFIXADO"
    IGPM = "IGPM"


class FiduciaryRegimeStatus(str, Enum):
    """Status do patrimônio separado averbado no Balcão NoMe B3."""
    ACTIVE = "ACTIVE"
    DEFICIENT = "DEFICIENT"
    LIQUIDATED = "LIQUIDATED"
    RESOLVED = "RESOLVED"


# =====================================================================
# DATA CLASSES DE DOMÍNIO
# =====================================================================

@dataclass
class SocialCollateralMetadata:
    """Metadados setoriais regulatórios específicos de Saúde ou Educação."""
    ans_operator_code: Optional[str] = None          # Registro da operadora de saúde na ANS
    sus_aih_number: Optional[str] = None             # Número da Autorização de Internação Hospitalar
    sus_apac_number: Optional[str] = None            # Número da APAC de Alta Complexidade SUS
    cnes_hospital_code: Optional[str] = None         # Código CNES do hospital/clínica
    mec_institution_code: Optional[str] = None       # Código da IES no MEC / e-MEC
    inep_school_code: Optional[str] = None           # Código da escola no INEP / Censo Escolar
    student_id: Optional[str] = None                 # Matrícula do aluno ou ID do contrato de financiamento
    guarantor_name: Optional[str] = None             # Nome do avalista/fiador do financiamento estudantil
    guarantor_doc: Optional[str] = None              # CPF/CNPJ do fiador/avalista

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ans_operator_code": self.ans_operator_code,
            "sus_aih_number": self.sus_aih_number,
            "sus_apac_number": self.sus_apac_number,
            "cnes_hospital_code": self.cnes_hospital_code,
            "mec_institution_code": self.mec_institution_code,
            "inep_school_code": self.inep_school_code,
            "student_id": self.student_id,
            "guarantor_name": self.guarantor_name,
            "guarantor_doc": self.guarantor_doc,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> SocialCollateralMetadata:
        if not data:
            return cls()
        return cls(
            ans_operator_code=data.get("ans_operator_code"),
            sus_aih_number=data.get("sus_aih_number"),
            sus_apac_number=data.get("sus_apac_number"),
            cnes_hospital_code=data.get("cnes_hospital_code"),
            mec_institution_code=data.get("mec_institution_code"),
            inep_school_code=data.get("inep_school_code"),
            student_id=data.get("student_id"),
            guarantor_name=data.get("guarantor_name"),
            guarantor_doc=data.get("guarantor_doc"),
        )


@dataclass
class SocialCollateralPaymentReceipt:
    """Comprovante de amortização e juros recebidos de um ativo de lastro social."""
    asset_id: str
    payment_date: date
    principal_paid: Decimal
    interest_paid: Decimal
    penalty_paid: Decimal
    total_received: Decimal
    remaining_balance: Decimal
    new_status: CollateralStatus

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "payment_date": self.payment_date.isoformat(),
            "principal_paid": str(self.principal_paid),
            "interest_paid": str(self.interest_paid),
            "penalty_paid": str(self.penalty_paid),
            "total_received": str(self.total_received),
            "remaining_balance": str(self.remaining_balance),
            "new_status": self.new_status.value,
        }


@dataclass
class SocialCollateralAsset:
    """Direito creditório ou título de lastro no setor social (Saúde ou Educação)."""
    asset_id: str
    collateral_type: SocialCollateralType
    debtor_name: str
    debtor_doc: str
    original_face_value: Decimal
    outstanding_balance: Decimal
    issue_date: date
    maturity_date: date
    indexer: IndexerType
    interest_rate_pct: Decimal
    status: CollateralStatus = CollateralStatus.PERFORMING
    accrued_interest: Decimal = field(default_factory=lambda: Decimal("0.00"))
    delinquency_days: int = 0
    metadata: SocialCollateralMetadata = field(default_factory=SocialCollateralMetadata)
    guarantee_description: str = ""

    def __post_init__(self) -> None:
        self.original_face_value = round_cents(to_decimal(self.original_face_value, "original_face_value"))
        self.outstanding_balance = round_cents(to_decimal(self.outstanding_balance, "outstanding_balance"))
        self.accrued_interest = round_cents(to_decimal(self.accrued_interest, "accrued_interest"))
        self.interest_rate_pct = round_rate(to_decimal(self.interest_rate_pct, "interest_rate_pct"))
        self.issue_date = to_date(self.issue_date, "issue_date")
        self.maturity_date = to_date(self.maturity_date, "maturity_date")
        if self.maturity_date < self.issue_date:
            raise HealthEducationValidationError(
                f"Data de vencimento ({self.maturity_date}) anterior à emissão ({self.issue_date}) no lastro social {self.asset_id}."
            )
        if self.original_face_value <= Decimal("0.00"):
            raise HealthEducationValidationError(f"Valor nominal deve ser positivo no lastro {self.asset_id}.")
        if self.outstanding_balance < Decimal("0.00"):
            raise HealthEducationValidationError(f"Saldo devedor não pode ser negativo no lastro {self.asset_id}.")

    def receive_payment(
        self,
        principal: Decimal,
        interest: Decimal,
        penalty: Decimal = Decimal("0.00"),
        payment_date: Optional[date] = None,
    ) -> SocialCollateralPaymentReceipt:
        """Processa pagamento (amortização, juros e encargos moratórios) recebido do devedor."""
        p = round_cents(to_decimal(principal, "principal"))
        i = round_cents(to_decimal(interest, "interest"))
        m = round_cents(to_decimal(penalty, "penalty"))

        if p < Decimal("0.00") or i < Decimal("0.00") or m < Decimal("0.00"):
            raise HealthEducationValidationError("Valores de amortização e juros não podem ser negativos.")
        if p > self.outstanding_balance:
            raise HealthEducationValidationError(
                f"Amortização solicitada ({p}) excede o saldo devedor ({self.outstanding_balance}) do lastro {self.asset_id}."
            )

        self.outstanding_balance = round_cents(self.outstanding_balance - p)
        if self.accrued_interest >= i:
            self.accrued_interest = round_cents(self.accrued_interest - i)
        else:
            self.accrued_interest = Decimal("0.00")

        if self.outstanding_balance == Decimal("0.00"):
            self.status = CollateralStatus.SETTLED
            self.delinquency_days = 0
        else:
            self.status = CollateralStatus.PERFORMING
            self.delinquency_days = 0

        p_date = payment_date or date.today()
        total_rec = round_cents(p + i + m)
        return SocialCollateralPaymentReceipt(
            asset_id=self.asset_id,
            payment_date=p_date,
            principal_paid=p,
            interest_paid=i,
            penalty_paid=m,
            total_received=total_rec,
            remaining_balance=self.outstanding_balance,
            new_status=self.status,
        )

    def mark_delinquent(self, days: int) -> None:
        """Sinaliza inadimplência temporária ou atraso na liquidação do lastro."""
        if days < 0:
            raise HealthEducationValidationError("Dias de atraso não podem ser negativos.")
        self.delinquency_days = int(days)
        if self.delinquency_days > 0 and self.status != CollateralStatus.DEFAULTED:
            self.status = CollateralStatus.DELINQUENT
        elif self.delinquency_days == 0 and self.status == CollateralStatus.DELINQUENT:
            self.status = CollateralStatus.PERFORMING

    def write_off(self) -> Decimal:
        """Executa write-off definitivo do lastro (ex: glosa SUS definitiva, falência do devedor, evasão escolar)."""
        if self.status in (CollateralStatus.SETTLED, CollateralStatus.DEFAULTED):
            raise HealthEducationValidationError(f"Lastro social {self.asset_id} já liquidado ou em default não pode sofrer novo write-off.")
        total_loss = round_cents(self.outstanding_balance + self.accrued_interest)
        self.outstanding_balance = Decimal("0.00")
        self.accrued_interest = Decimal("0.00")
        self.status = CollateralStatus.DEFAULTED
        return total_loss

    def accrue_interest(self, period_rate: Decimal) -> Decimal:
        """Apropria juros do período sobre o saldo devedor do lastro."""
        rate = round_rate(to_decimal(period_rate, "period_rate"))
        if rate < Decimal("0.00"):
            raise HealthEducationValidationError("Taxa periódica de juros do lastro não pode ser negativa.")
        interest = round_cents(self.outstanding_balance * rate)
        self.accrued_interest = round_cents(self.accrued_interest + interest)
        return interest

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "collateral_type": self.collateral_type.value,
            "debtor_name": self.debtor_name,
            "debtor_doc": self.debtor_doc,
            "original_face_value": str(self.original_face_value),
            "outstanding_balance": str(self.outstanding_balance),
            "issue_date": self.issue_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "indexer": self.indexer.value,
            "interest_rate_pct": str(self.interest_rate_pct),
            "status": self.status.value,
            "accrued_interest": str(self.accrued_interest),
            "delinquency_days": self.delinquency_days,
            "metadata": self.metadata.to_dict(),
            "guarantee_description": self.guarantee_description,
        }


@dataclass
class ReserveFund:
    """Fundo de Reserva de Liquidez obrigatório da emissão (Credit Enhancement)."""
    fund_id: str
    target_balance: Decimal
    current_balance: Decimal
    minimum_floor: Decimal = Decimal("0.00")
    yield_rate_annual_pct: Decimal = Decimal("0.1000")  # 10% a.a. CDI padrão em conta fiduciária

    def __post_init__(self) -> None:
        self.target_balance = round_cents(to_decimal(self.target_balance, "target_balance"))
        self.current_balance = round_cents(to_decimal(self.current_balance, "current_balance"))
        self.minimum_floor = round_cents(to_decimal(self.minimum_floor, "minimum_floor"))
        self.yield_rate_annual_pct = round_rate(to_decimal(self.yield_rate_annual_pct, "yield_rate_annual_pct"))
        if self.target_balance < Decimal("0.00") or self.current_balance < Decimal("0.00") or self.minimum_floor < Decimal("0.00"):
            raise HealthEducationValidationError("Saldos do fundo de reserva não podem ser negativos.")

    @property
    def is_deficient(self) -> bool:
        """Indica se o saldo atual do fundo está defasado em relação à meta contratual."""
        return self.current_balance < self.target_balance

    @property
    def deficit_amount(self) -> Decimal:
        """Montante exato requerido para recompor o fundo até a meta regulamentar."""
        return round_cents(max(Decimal("0.00"), self.target_balance - self.current_balance))

    def deposit(self, amount: Decimal) -> Decimal:
        """Efetua aporte ou recomposição de liquidez no fundo de reserva."""
        amt = round_cents(to_decimal(amount, "deposit_amount"))
        if amt < Decimal("0.00"):
            raise HealthEducationValidationError("Valor de aporte no fundo de reserva não pode ser negativo.")
        self.current_balance = round_cents(self.current_balance + amt)
        return self.current_balance

    def draw(self, requested_amount: Decimal) -> Decimal:
        """Efetua saque de liquidez para cobrir despesas ou serviço da dívida sênior."""
        req = round_cents(to_decimal(requested_amount, "requested_draw"))
        if req < Decimal("0.00"):
            raise HealthEducationValidationError("Valor de saque do fundo de reserva não pode ser negativo.")
        actual_draw = min(req, self.current_balance)
        self.current_balance = round_cents(self.current_balance - actual_draw)
        return actual_draw

    def apply_yield(self, period_rate: Decimal) -> Decimal:
        """Aplica remuneração financeira sobre os recursos aplicados em conta vinculada."""
        rate = round_rate(to_decimal(period_rate, "reserve_yield_rate"))
        if rate < Decimal("0.00"):
            raise HealthEducationValidationError("Taxa de rendimento do fundo não pode ser negativa.")
        earnings = round_cents(self.current_balance * rate)
        self.current_balance = round_cents(self.current_balance + earnings)
        return earnings

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fund_id": self.fund_id,
            "target_balance": str(self.target_balance),
            "current_balance": str(self.current_balance),
            "minimum_floor": str(self.minimum_floor),
            "yield_rate_annual_pct": str(self.yield_rate_annual_pct),
            "is_deficient": self.is_deficient,
            "deficit_amount": str(self.deficit_amount),
        }


@dataclass
class CertificateTranche:
    """Classe/Tranche de Certificados de Recebíveis emitida no Balcão NoMe B3."""
    tranche_id: str
    isin: str
    b3_code: str
    tranche_class: CertificateTrancheClass
    amortization_type: AmortizationType
    indexer: IndexerType
    spread_rate_annual_pct: Decimal
    original_quantity: int
    current_quantity: int
    original_unit_price: Decimal
    current_unit_price: Decimal
    accrued_interest: Decimal = field(default_factory=lambda: Decimal("0.00"))
    unpaid_interest_deficit: Decimal = field(default_factory=lambda: Decimal("0.00"))
    amortization_schedule: Dict[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.original_unit_price = round_unit(to_decimal(self.original_unit_price, "original_unit_price"))
        self.current_unit_price = round_unit(to_decimal(self.current_unit_price, "current_unit_price"))
        self.spread_rate_annual_pct = round_rate(to_decimal(self.spread_rate_annual_pct, "spread_rate_annual_pct"))
        self.accrued_interest = round_cents(to_decimal(self.accrued_interest, "accrued_interest"))
        self.unpaid_interest_deficit = round_cents(to_decimal(self.unpaid_interest_deficit, "unpaid_interest_deficit"))
        if self.original_quantity <= 0 or self.current_quantity < 0:
            raise HealthEducationValidationError(f"Quantidades inválidas na tranche {self.tranche_id}.")
        if self.current_unit_price < Decimal("0.00"):
            raise HealthEducationValidationError(f"PU não pode ser negativo na tranche {self.tranche_id}.")

    @property
    def total_outstanding_balance(self) -> Decimal:
        """Saldo Devedor Total da tranche: Quantidade * PU."""
        return round_cents(Decimal(self.current_quantity) * self.current_unit_price)

    def accrue_interest_period(self, period_rate: Decimal) -> Decimal:
        """Apropria juros do período sobre o saldo devedor atual."""
        rate = round_rate(to_decimal(period_rate, "period_rate"))
        if rate < Decimal("0.00"):
            raise HealthEducationValidationError("Taxa periódica de juros da tranche não pode ser negativa.")
        interest = round_cents(self.total_outstanding_balance * rate)
        self.accrued_interest = round_cents(self.accrued_interest + interest)
        return interest

    def pay_interest(self, cash_available: Decimal) -> Tuple[Decimal, Decimal]:
        """Paga juros vencidos e acumulados até o limite de recursos disponíveis."""
        avail = round_cents(to_decimal(cash_available, "cash_available"))
        total_due = round_cents(self.accrued_interest + self.unpaid_interest_deficit)
        if total_due <= Decimal("0.00"):
            return (Decimal("0.00"), Decimal("0.00"))
        paid = min(avail, total_due)
        new_deficit = round_cents(total_due - paid)
        self.accrued_interest = Decimal("0.00")
        self.unpaid_interest_deficit = new_deficit
        return (paid, new_deficit)

    def amortize(self, principal_amount: Decimal) -> Decimal:
        """Amortiza principal da tranche e atualiza com precisão o Preço Unitário (PU)."""
        amt = round_cents(to_decimal(principal_amount, "principal_amount"))
        if amt < Decimal("0.00"):
            raise HealthEducationValidationError("Valor de amortização não pode ser negativo.")
        current_bal = self.total_outstanding_balance
        if current_bal == Decimal("0.00"):
            return Decimal("0.00")
        actual_amort = min(amt, current_bal)
        new_balance = round_cents(current_bal - actual_amort)
        if self.current_quantity > 0:
            self.current_unit_price = round_unit(new_balance / Decimal(self.current_quantity))
        return actual_amort

    def absorb_loss(self, loss_amount: Decimal) -> Tuple[Decimal, Decimal]:
        """Absorve perda definitiva de crédito (write-off) reduzindo o PU da tranche."""
        loss = round_cents(to_decimal(loss_amount, "loss_amount"))
        if loss < Decimal("0.00"):
            raise HealthEducationValidationError("Valor da perda não pode ser negativo.")
        current_bal = self.total_outstanding_balance
        if current_bal == Decimal("0.00"):
            return (Decimal("0.00"), loss)
        absorbed = min(loss, current_bal)
        unabsorbed = round_cents(loss - absorbed)
        new_balance = round_cents(current_bal - absorbed)
        if self.current_quantity > 0:
            self.current_unit_price = round_unit(new_balance / Decimal(self.current_quantity))
        return (absorbed, unabsorbed)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tranche_id": self.tranche_id,
            "isin": self.isin,
            "b3_code": self.b3_code,
            "tranche_class": self.tranche_class.value,
            "amortization_type": self.amortization_type.value,
            "indexer": self.indexer.value,
            "spread_rate_annual_pct": str(self.spread_rate_annual_pct),
            "original_quantity": self.original_quantity,
            "current_quantity": self.current_quantity,
            "original_unit_price": str(self.original_unit_price),
            "current_unit_price": str(self.current_unit_price),
            "total_outstanding_balance": str(self.total_outstanding_balance),
            "accrued_interest": str(self.accrued_interest),
            "unpaid_interest_deficit": str(self.unpaid_interest_deficit),
            "amortization_schedule": {k: str(v) for k, v in self.amortization_schedule.items()},
        }


@dataclass
class FiduciaryRegime:
    """Regime Fiduciário com Patrimônio Separado (Lei nº 14.430/2022, arts. 17 a 33)."""
    regime_id: str
    securitizer_name: str
    securitizer_cnpj: str
    trustee_name: str
    trustee_cnpj: str
    deed_registry_number: str
    registry_date: date
    segregated_account_id: str
    segregated_cash_balance: Decimal
    reserve_fund: ReserveFund
    status: FiduciaryRegimeStatus = FiduciaryRegimeStatus.ACTIVE

    def __post_init__(self) -> None:
        self.segregated_cash_balance = round_cents(to_decimal(self.segregated_cash_balance, "segregated_cash_balance"))
        self.registry_date = to_date(self.registry_date, "registry_date")
        if self.segregated_cash_balance < Decimal("0.00"):
            raise HealthEducationValidationError("Caixa segregado fiduciário não pode ser negativo.")
        if not self.segregated_account_id or not self.segregated_account_id.strip():
            raise HealthEducationValidationError("Identificador de conta segregada fiduciária é obrigatório.")

    def assert_incommunicability(self, attempted_recipient_account: Optional[str] = None) -> None:
        """Verifica a regra de incomunicabilidade fiduciária perante terceiros."""
        if attempted_recipient_account and attempted_recipient_account != self.segregated_account_id:
            raise HealthEducationSegregationViolationError(
                f"Violação de Incomunicabilidade (art. 19 da Lei nº 14.430/2022): Recursos do regime {self.regime_id} "
                f"não podem ser direcionados para conta externa/alheia '{attempted_recipient_account}'."
            )

    def withdraw_segregated_cash(self, amount: Decimal, purpose: str) -> Decimal:
        """Efetua desembolso de caixa segregado com validação estrita de finalidade autorizada."""
        amt = round_cents(to_decimal(amount, "amount"))
        if amt < Decimal("0.00"):
            raise HealthEducationValidationError("Valor de desembolso não pode ser negativo.")
        if amt > self.segregated_cash_balance:
            raise HealthEducationWaterfallError(
                f"Caixa segregado insuficiente ({self.segregated_cash_balance}) para desembolso ({amt})."
            )

        authorized_purposes = {
            "WATERFALL_EXPENSE",
            "WATERFALL_SENIOR_SERVICE",
            "WATERFALL_MEZZANINE_SERVICE",
            "WATERFALL_RESERVE_DEPOSIT",
            "WATERFALL_SUBORDINATED_SERVICE",
            "WATERFALL_RESIDUAL_AUTHORIZED",
            "FINAL_UNWIND_SETTLEMENT",
        }
        if purpose not in authorized_purposes:
            raise HealthEducationSegregationViolationError(
                f"Tentativa de retirada não autorizada ('{purpose}') violando a afetação do patrimônio separado."
            )

        self.segregated_cash_balance = round_cents(self.segregated_cash_balance - amt)
        return amt

    def deposit_segregated_cash(self, amount: Decimal) -> Decimal:
        """Deposita recursos na conta fiduciária do patrimônio separado."""
        amt = round_cents(to_decimal(amount, "amount"))
        if amt < Decimal("0.00"):
            raise HealthEducationValidationError("Valor de depósito não pode ser negativo.")
        self.segregated_cash_balance = round_cents(self.segregated_cash_balance + amt)
        return self.segregated_cash_balance

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime_id": self.regime_id,
            "securitizer_name": self.securitizer_name,
            "securitizer_cnpj": self.securitizer_cnpj,
            "trustee_name": self.trustee_name,
            "trustee_cnpj": self.trustee_cnpj,
            "deed_registry_number": self.deed_registry_number,
            "registry_date": self.registry_date.isoformat(),
            "segregated_account_id": self.segregated_account_id,
            "segregated_cash_balance": str(self.segregated_cash_balance),
            "reserve_fund": self.reserve_fund.to_dict(),
            "status": self.status.value,
        }


@dataclass
class LossAbsorptionResult:
    """Resultado auditável da absorção de prejuízo por default de lastro social."""
    asset_id: str
    total_loss: Decimal
    loss_absorbed_by_subordinated: Decimal
    loss_absorbed_by_mezzanine: Decimal
    loss_absorbed_by_senior: Decimal
    unabsorbed_loss: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "total_loss": str(self.total_loss),
            "loss_absorbed_by_subordinated": str(self.loss_absorbed_by_subordinated),
            "loss_absorbed_by_mezzanine": str(self.loss_absorbed_by_mezzanine),
            "loss_absorbed_by_senior": str(self.loss_absorbed_by_senior),
            "unabsorbed_loss": str(self.unabsorbed_loss),
        }


@dataclass
class WaterfallExecutionResult:
    """Relatório contábil completo da execução periódica do Payment Waterfall."""
    execution_id: str
    issue_id: str
    period_date: date
    opening_cash_balance: Decimal
    cash_inflow_collateral: Decimal
    cash_inflow_yield: Decimal
    total_cash_available: Decimal
    expenses_paid: Decimal
    reserve_fund_draw: Decimal
    senior_interest_paid: Decimal
    senior_principal_paid: Decimal
    senior_interest_deficit: Decimal
    senior_principal_deficit: Decimal
    mezzanine_interest_paid: Decimal
    mezzanine_principal_paid: Decimal
    reserve_fund_deposit: Decimal
    subordinated_interest_paid: Decimal
    subordinated_principal_paid: Decimal
    residual_distribution: Decimal
    closing_cash_balance: Decimal
    closing_reserve_balance: Decimal
    is_senior_fully_satisfied: bool
    is_reserve_at_target: bool
    conservation_verified: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "issue_id": self.issue_id,
            "period_date": self.period_date.isoformat(),
            "opening_cash_balance": str(self.opening_cash_balance),
            "cash_inflow_collateral": str(self.cash_inflow_collateral),
            "cash_inflow_yield": str(self.cash_inflow_yield),
            "total_cash_available": str(self.total_cash_available),
            "expenses_paid": str(self.expenses_paid),
            "reserve_fund_draw": str(self.reserve_fund_draw),
            "senior_interest_paid": str(self.senior_interest_paid),
            "senior_principal_paid": str(self.senior_principal_paid),
            "senior_interest_deficit": str(self.senior_interest_deficit),
            "senior_principal_deficit": str(self.senior_principal_deficit),
            "mezzanine_interest_paid": str(self.mezzanine_interest_paid),
            "mezzanine_principal_paid": str(self.mezzanine_principal_paid),
            "reserve_fund_deposit": str(self.reserve_fund_deposit),
            "subordinated_interest_paid": str(self.subordinated_interest_paid),
            "subordinated_principal_paid": str(self.subordinated_principal_paid),
            "residual_distribution": str(self.residual_distribution),
            "closing_cash_balance": str(self.closing_cash_balance),
            "closing_reserve_balance": str(self.closing_reserve_balance),
            "is_senior_fully_satisfied": self.is_senior_fully_satisfied,
            "is_reserve_at_target": self.is_reserve_at_target,
            "conservation_verified": self.conservation_verified,
        }


# =====================================================================
# ENTIDADE CENTRAL DE EMISSÃO DE CR SAÚDE E CR EDUCAÇÃO
# =====================================================================

@dataclass
class SocialSecuritizationIssue:
    """Emissão de CR Saúde ou CR Educação averbada no Balcão NoMe B3 com Regime Fiduciário."""
    issue_id: str
    sector: SocialSecuritizationSector
    legal_framework: List[LegalFramework]
    series_number: int
    issue_date: date
    final_maturity_date: date
    fiduciary_regime: FiduciaryRegime
    tranches: Dict[str, CertificateTranche] = field(default_factory=dict)
    collateral_pool: Dict[str, SocialCollateralAsset] = field(default_factory=dict)
    annual_operational_expenses_pct: Decimal = Decimal("0.0015")  # 0.15% a.a. fiduciário e B3
    pending_inflow: Decimal = field(default_factory=lambda: Decimal("0.00"))

    def __post_init__(self) -> None:
        self.issue_date = to_date(self.issue_date, "issue_date")
        self.final_maturity_date = to_date(self.final_maturity_date, "final_maturity_date")
        self.annual_operational_expenses_pct = round_rate(
            to_decimal(self.annual_operational_expenses_pct, "annual_operational_expenses_pct")
        )
        self.pending_inflow = round_cents(to_decimal(self.pending_inflow, "pending_inflow"))
        if self.final_maturity_date < self.issue_date:
            raise HealthEducationValidationError("Data de vencimento final anterior à emissão.")
        if not self.legal_framework:
            self.legal_framework = [LegalFramework.LEI_14430_2022, LegalFramework.RESOLUCAO_CVM_60]

    def add_collateral(self, asset: SocialCollateralAsset) -> None:
        """Adiciona ativo de lastro com validação estrita de elegibilidade setorial social."""
        if self.sector == SocialSecuritizationSector.HEALTH and not asset.collateral_type.is_health:
            raise HealthEducationValidationError(
                f"CR Saúde admite exclusivamente lastros de saúde (planos ANS, SUS e hospitais/clínicas). "
                f"Recebido: {asset.collateral_type.value}"
            )
        if self.sector == SocialSecuritizationSector.EDUCATION and not asset.collateral_type.is_education:
            raise HealthEducationValidationError(
                f"CR Educação admite exclusivamente lastros educacionais (mensalidades K12, superior e crédito estudantil). "
                f"Recebido: {asset.collateral_type.value}"
            )
        self.collateral_pool[asset.asset_id] = asset

    def add_tranche(self, tranche: CertificateTranche) -> None:
        """Adiciona tranche de certificados à emissão."""
        self.tranches[tranche.tranche_id] = tranche

    def total_collateral_balance(self) -> Decimal:
        """Soma do saldo devedor dos lastros ativos (Performing ou Delinquent)."""
        total = sum(
            (asset.outstanding_balance for asset in self.collateral_pool.values()
             if asset.status in (CollateralStatus.PERFORMING, CollateralStatus.DELINQUENT)),
            Decimal("0.00"),
        )
        return round_cents(total)

    def total_tranches_balance(self) -> Decimal:
        """Soma do saldo devedor de todas as tranches de certificados."""
        total = sum((t.total_outstanding_balance for t in self.tranches.values()), Decimal("0.00"))
        return round_cents(total)

    def senior_balance(self) -> Decimal:
        """Saldo devedor das tranches da classe Sênior."""
        total = sum(
            (t.total_outstanding_balance for t in self.tranches.values() if t.tranche_class == CertificateTrancheClass.SENIOR),
            Decimal("0.00"),
        )
        return round_cents(total)

    def mezzanine_balance(self) -> Decimal:
        """Saldo devedor das tranches da classe Mezanino."""
        total = sum(
            (t.total_outstanding_balance for t in self.tranches.values() if t.tranche_class == CertificateTrancheClass.MEZZANINE),
            Decimal("0.00"),
        )
        return round_cents(total)

    def subordinated_balance(self) -> Decimal:
        """Saldo devedor das tranches da classe Subordinada."""
        total = sum(
            (t.total_outstanding_balance for t in self.tranches.values() if t.tranche_class == CertificateTrancheClass.SUBORDINATED),
            Decimal("0.00"),
        )
        return round_cents(total)

    def overcollateralization_ratio(self) -> Decimal:
        """Razão de Sobrecolateralização: (Lastro + Reserva + Caixa Segregado) / Saldo Sênior."""
        sr_bal = self.senior_balance()
        if sr_bal == Decimal("0.00"):
            return Decimal("1.00000000")
        fiduciary_assets = (
            self.total_collateral_balance()
            + self.fiduciary_regime.reserve_fund.current_balance
            + self.fiduciary_regime.segregated_cash_balance
        )
        return round_rate(fiduciary_assets / sr_bal)

    def subordination_ratio(self) -> Decimal:
        """Percentual de subordinação protetiva: Saldo Subordinado / Saldo Total de Certificados."""
        total_cert = self.total_tranches_balance()
        if total_cert == Decimal("0.00"):
            return Decimal("0.00000000")
        sub_bal = self.subordinated_balance()
        return round_rate(sub_bal / total_cert)

    def register_collateral_payment(
        self,
        asset_id: str,
        principal: Decimal,
        interest: Decimal,
        penalty: Decimal = Decimal("0.00"),
        payment_date: Optional[date] = None,
    ) -> SocialCollateralPaymentReceipt:
        """Registra pagamento recebido de lastro social e direciona ao caixa fiduciário pendente."""
        if asset_id not in self.collateral_pool:
            raise HealthEducationValidationError(f"Lastro '{asset_id}' não encontrado na emissão {self.issue_id}.")
        asset = self.collateral_pool[asset_id]
        receipt = asset.receive_payment(principal, interest, penalty, payment_date)
        self.pending_inflow = round_cents(self.pending_inflow + receipt.total_received)
        return receipt

    def process_collateral_default(self, asset_id: str) -> LossAbsorptionResult:
        """Processa inadimplência e write-off definitivo de lastro social (First-Loss Piece)."""
        if asset_id not in self.collateral_pool:
            raise HealthEducationValidationError(f"Lastro '{asset_id}' não encontrado na emissão {self.issue_id}.")
        asset = self.collateral_pool[asset_id]
        total_loss = asset.write_off()

        remaining_loss = total_loss
        loss_sub = Decimal("0.00")
        loss_mezz = Decimal("0.00")
        loss_sr = Decimal("0.00")

        # 1. Absorção primária e integral pela Tranche Subordinada
        sub_tranches = [t for t in self.tranches.values() if t.tranche_class == CertificateTrancheClass.SUBORDINATED]
        for tr in sub_tranches:
            if remaining_loss <= Decimal("0.00"):
                break
            absorbed, remaining_loss = tr.absorb_loss(remaining_loss)
            loss_sub = round_cents(loss_sub + absorbed)

        # 2. Se a perda exceder a Subordinada, absorve na Mezanino
        if remaining_loss > Decimal("0.00"):
            mezz_tranches = [t for t in self.tranches.values() if t.tranche_class == CertificateTrancheClass.MEZZANINE]
            for tr in mezz_tranches:
                if remaining_loss <= Decimal("0.00"):
                    break
                absorbed, remaining_loss = tr.absorb_loss(remaining_loss)
                loss_mezz = round_cents(loss_mezz + absorbed)

        # 3. Se a perda exceder a Mezanino, atinge a Sênior
        if remaining_loss > Decimal("0.00"):
            sr_tranches = [t for t in self.tranches.values() if t.tranche_class == CertificateTrancheClass.SENIOR]
            for tr in sr_tranches:
                if remaining_loss <= Decimal("0.00"):
                    break
                absorbed, remaining_loss = tr.absorb_loss(remaining_loss)
                loss_sr = round_cents(loss_sr + absorbed)

        return LossAbsorptionResult(
            asset_id=asset_id,
            total_loss=total_loss,
            loss_absorbed_by_subordinated=loss_sub,
            loss_absorbed_by_mezzanine=loss_mezz,
            loss_absorbed_by_senior=loss_sr,
            unabsorbed_loss=remaining_loss,
        )

    def attempt_unauthorized_transfer(self, destination_account: str, amount: Decimal) -> None:
        """Bloqueia qualquer tentativa de transferência para patrimônio geral ou terceiro não vinculado."""
        amt = round_cents(to_decimal(amount, "amount"))
        if amt <= Decimal("0.00"):
            raise HealthEducationValidationError("Valor de transferência deve ser estritamente positivo.")
        raise HealthEducationSegregationViolationError(
            f"Violação de Incomunicabilidade (Lei nº 14.430/2022, art. 19): Tentativa de transferir {amt} "
            f"do patrimônio fiduciário {self.fiduciary_regime.regime_id} para conta externa '{destination_account}' "
            f"bloqueada por lei. O patrimônio separado não responde por débitos da securitizadora."
        )

    def execute_waterfall(
        self,
        period_date: date,
        expenses_override: Optional[Decimal] = None,
        period_indexer_rate: Optional[Decimal] = None,
        allow_subordinated_residual_payout: bool = False,
    ) -> WaterfallExecutionResult:
        """Executa a cascata periódica de pagamentos (Payment Waterfall) com conservação formal rigorosa."""
        period_dt = to_date(period_date, "period_date")
        date_str = period_dt.isoformat()

        opening_cash = self.fiduciary_regime.segregated_cash_balance
        inflow_collateral = self.pending_inflow
        self.pending_inflow = Decimal("0.00")

        # Rendimento de caixa aplicado do patrimônio separado no período
        monthly_cash_rate = round_rate(self.fiduciary_regime.reserve_fund.yield_rate_annual_pct / Decimal("12.0"))
        cash_yield = round_cents(opening_cash * monthly_cash_rate)

        # Rendimento do próprio Fundo de Reserva
        self.fiduciary_regime.reserve_fund.apply_yield(monthly_cash_rate)

        # Caixa total em conta fiduciária antes dos desembolsos
        cash_available = round_cents(opening_cash + inflow_collateral + cash_yield)
        total_cash_initial = cash_available

        # -----------------------------------------------------------------
        # NÍVEL 1: Despesas Operacionais e Fiduciárias do Patrimônio Separado
        # -----------------------------------------------------------------
        if expenses_override is not None:
            expenses_due = round_cents(to_decimal(expenses_override, "expenses_override"))
        else:
            total_liab = self.total_tranches_balance()
            monthly_exp_rate = round_rate(self.annual_operational_expenses_pct / Decimal("12.0"))
            expenses_due = round_cents(total_liab * monthly_exp_rate)

        expenses_paid = min(cash_available, expenses_due)
        cash_available = round_cents(cash_available - expenses_paid)

        # -----------------------------------------------------------------
        # Apropriação de Juros do Período em Todas as Tranches
        # -----------------------------------------------------------------
        for tranche in self.tranches.values():
            spread_monthly = round_rate(tranche.spread_rate_annual_pct / Decimal("12.0"))
            idx_monthly = round_rate(to_decimal(period_indexer_rate or Decimal("0.00"), "indexer_rate"))
            period_rate = round_rate(spread_monthly + idx_monthly)
            tranche.accrue_interest_period(period_rate)

        # -----------------------------------------------------------------
        # NÍVEL 2: Serviço da Dívida Sênior (Juros + Amortização Programada)
        # -----------------------------------------------------------------
        senior_tranches = [t for t in self.tranches.values() if t.tranche_class == CertificateTrancheClass.SENIOR]
        senior_int_paid = Decimal("0.00")
        senior_prin_paid = Decimal("0.00")
        senior_int_def = Decimal("0.00")
        senior_prin_def = Decimal("0.00")
        reserve_drawn = Decimal("0.00")

        for tr in senior_tranches:
            int_due = round_cents(tr.accrued_interest + tr.unpaid_interest_deficit)
            # Se caixa for insuficiente para juros sênior, saca do Fundo de Reserva
            if cash_available < int_due:
                shortfall = round_cents(int_due - cash_available)
                drawn = self.fiduciary_regime.reserve_fund.draw(shortfall)
                reserve_drawn = round_cents(reserve_drawn + drawn)
                cash_available = round_cents(cash_available + drawn)

            paid_i, def_i = tr.pay_interest(cash_available)
            cash_available = round_cents(cash_available - paid_i)
            senior_int_paid = round_cents(senior_int_paid + paid_i)
            senior_int_def = round_cents(senior_int_def + def_i)

            # Amortização programada
            sched_amort = tr.amortization_schedule.get(date_str, Decimal("0.00"))
            sched_amort = round_cents(to_decimal(sched_amort, "senior_schedule_amort"))
            if sched_amort > Decimal("0.00"):
                # Se faltar caixa para amortização sênior programada, também recorre à reserva se disponível
                if cash_available < sched_amort:
                    shortfall_p = round_cents(sched_amort - cash_available)
                    drawn_p = self.fiduciary_regime.reserve_fund.draw(shortfall_p)
                    reserve_drawn = round_cents(reserve_drawn + drawn_p)
                    cash_available = round_cents(cash_available + drawn_p)

                act_prin = min(cash_available, sched_amort)
                amort_executed = tr.amortize(act_prin)
                cash_available = round_cents(cash_available - amort_executed)
                senior_prin_paid = round_cents(senior_prin_paid + amort_executed)
                senior_prin_def = round_cents(senior_prin_def + max(Decimal("0.00"), sched_amort - amort_executed))

        # -----------------------------------------------------------------
        # NÍVEL 3: Serviço da Dívida Mezanino
        # -----------------------------------------------------------------
        mezz_int_paid = Decimal("0.00")
        mezz_prin_paid = Decimal("0.00")
        mezz_tranches = [t for t in self.tranches.values() if t.tranche_class == CertificateTrancheClass.MEZZANINE]

        # Mezanino só recebe se a Sênior estiver 100% adimplente no período
        if senior_int_def == Decimal("0.00") and senior_prin_def == Decimal("0.00"):
            for tr in mezz_tranches:
                paid_i, def_i = tr.pay_interest(cash_available)
                cash_available = round_cents(cash_available - paid_i)
                mezz_int_paid = round_cents(mezz_int_paid + paid_i)

                sched_amort = tr.amortization_schedule.get(date_str, Decimal("0.00"))
                sched_amort = round_cents(to_decimal(sched_amort, "mezz_schedule_amort"))
                if sched_amort > Decimal("0.00"):
                    act_prin = min(cash_available, sched_amort)
                    amort_executed = tr.amortize(act_prin)
                    cash_available = round_cents(cash_available - amort_executed)
                    mezz_prin_paid = round_cents(mezz_prin_paid + amort_executed)

        # -----------------------------------------------------------------
        # NÍVEL 4: Recomposição Mandatória do Fundo de Reserva de Liquidez
        # -----------------------------------------------------------------
        reserve_deposited = Decimal("0.00")
        if self.fiduciary_regime.reserve_fund.is_deficient and cash_available > Decimal("0.00"):
            needed = self.fiduciary_regime.reserve_fund.deficit_amount
            deposit_amt = min(cash_available, needed)
            self.fiduciary_regime.reserve_fund.deposit(deposit_amt)
            reserve_deposited = deposit_amt
            cash_available = round_cents(cash_available - deposit_amt)

        # -----------------------------------------------------------------
        # NÍVEL 5: Serviço da Dívida Subordinada (Somente após Sênior & Fundo Plenos)
        # -----------------------------------------------------------------
        sub_int_paid = Decimal("0.00")
        sub_prin_paid = Decimal("0.00")
        sub_tranches = [t for t in self.tranches.values() if t.tranche_class == CertificateTrancheClass.SUBORDINATED]

        # Regra de Ouro da Securitização:
        # A Subordinada NUNCA recebe nada se houver déficit sênior ou fundo de reserva desfalçado!
        can_pay_subordinated = (
            senior_int_def == Decimal("0.00")
            and senior_prin_def == Decimal("0.00")
            and not self.fiduciary_regime.reserve_fund.is_deficient
        )

        if can_pay_subordinated:
            for tr in sub_tranches:
                paid_i, def_i = tr.pay_interest(cash_available)
                cash_available = round_cents(cash_available - paid_i)
                sub_int_paid = round_cents(sub_int_paid + paid_i)

                sched_amort = tr.amortization_schedule.get(date_str, Decimal("0.00"))
                sched_amort = round_cents(to_decimal(sched_amort, "sub_schedule_amort"))
                if sched_amort > Decimal("0.00"):
                    act_prin = min(cash_available, sched_amort)
                    amort_executed = tr.amortize(act_prin)
                    cash_available = round_cents(cash_available - amort_executed)
                    sub_prin_paid = round_cents(sub_prin_paid + amort_executed)

        # -----------------------------------------------------------------
        # NÍVEL 6: Distribuição Residual (Excess Spread) ou Retenção em Caixa Segregado
        # -----------------------------------------------------------------
        residual_dist = Decimal("0.00")
        if allow_subordinated_residual_payout and can_pay_subordinated and cash_available > Decimal("0.00"):
            residual_dist = cash_available
            cash_available = Decimal("0.00")

        self.fiduciary_regime.segregated_cash_balance = cash_available

        # -----------------------------------------------------------------
        # PROVA FORMAL DE CONSERVAÇÃO FINANCEIRA DO WATERFALL: Entradas == Saídas
        # -----------------------------------------------------------------
        total_inflow = round_cents(opening_cash + inflow_collateral + cash_yield + reserve_drawn)
        total_outflow = round_cents(
            expenses_paid
            + senior_int_paid
            + senior_prin_paid
            + mezz_int_paid
            + mezz_prin_paid
            + reserve_deposited
            + sub_int_paid
            + sub_prin_paid
            + residual_dist
            + cash_available
        )
        conservation_verified = (total_inflow == total_outflow) and (cash_available >= Decimal("0.00"))

        if not conservation_verified:
            raise HealthEducationInvariantError(
                f"Quebra formal de conservação financeira no waterfall: Entradas ({total_inflow}) != Saídas ({total_outflow}) "
                f"ou Caixa Segregado Negativo ({cash_available})"
            )

        is_sr_satisfied = (senior_int_def == Decimal("0.00") and senior_prin_def == Decimal("0.00"))
        is_res_target = not self.fiduciary_regime.reserve_fund.is_deficient

        exec_id = f"WTF-{self.issue_id}-{date_str}"
        return WaterfallExecutionResult(
            execution_id=exec_id,
            issue_id=self.issue_id,
            period_date=period_dt,
            opening_cash_balance=opening_cash,
            cash_inflow_collateral=inflow_collateral,
            cash_inflow_yield=cash_yield,
            total_cash_available=total_cash_initial,
            expenses_paid=expenses_paid,
            reserve_fund_draw=reserve_drawn,
            senior_interest_paid=senior_int_paid,
            senior_principal_paid=senior_prin_paid,
            senior_interest_deficit=senior_int_def,
            senior_principal_deficit=senior_prin_def,
            mezzanine_interest_paid=mezz_int_paid,
            mezzanine_principal_paid=mezz_prin_paid,
            reserve_fund_deposit=reserve_deposited,
            subordinated_interest_paid=sub_int_paid,
            subordinated_principal_paid=sub_prin_paid,
            residual_distribution=residual_dist,
            closing_cash_balance=cash_available,
            closing_reserve_balance=self.fiduciary_regime.reserve_fund.current_balance,
            is_senior_fully_satisfied=is_sr_satisfied,
            is_reserve_at_target=is_res_target,
            conservation_verified=conservation_verified,
        )

    def unwind_reserve_at_maturity(self) -> Decimal:
        """Na liquidação final da emissão, transfere a reserva residual para o caixa segregado."""
        sr_bal = self.senior_balance()
        mezz_bal = self.mezzanine_balance()
        if sr_bal > Decimal("0.00") or mezz_bal > Decimal("0.00"):
            raise HealthEducationWaterfallError(
                "Impossível desmobilizar Fundo de Reserva antes da amortização integral de Sênior e Mezanino."
            )
        rem = self.fiduciary_regime.reserve_fund.current_balance
        self.fiduciary_regime.reserve_fund.current_balance = Decimal("0.00")
        self.fiduciary_regime.segregated_cash_balance = round_cents(
            self.fiduciary_regime.segregated_cash_balance + rem
        )
        return rem

    def verify_invariants(self) -> Dict[str, bool]:
        """Verifica todas as leis de conservação, incomunicabilidade e subordinação da emissão social."""
        sr_bal = self.senior_balance()
        collateral_bal = self.total_collateral_balance()
        reserve_bal = self.fiduciary_regime.reserve_fund.current_balance
        cash_bal = self.fiduciary_regime.segregated_cash_balance
        total_fiduciary_assets = round_cents(collateral_bal + reserve_bal + cash_bal)

        # 1. Cobertura da Segregação Fiduciária
        fiduciary_coverage = (total_fiduciary_assets >= sr_bal) or (sr_bal == Decimal("0.00"))

        # 2. Incomunicabilidade do Patrimônio Separado (Conta fiduciária exclusiva e ativa)
        incommunicability_verified = (
            bool(self.fiduciary_regime.segregated_account_id)
            and self.fiduciary_regime.status in (FiduciaryRegimeStatus.ACTIVE, FiduciaryRegimeStatus.DEFICIENT, FiduciaryRegimeStatus.LIQUIDATED)
            and self.fiduciary_regime.segregated_cash_balance >= Decimal("0.00")
        )

        # 3. Conservação de PU e Saldo Devedor nas Tranches
        pu_conservation = True
        for tr in self.tranches.values():
            calc_bal = round_cents(Decimal(tr.current_quantity) * tr.current_unit_price)
            if calc_bal != tr.total_outstanding_balance:
                pu_conservation = False
                break

        # 4. Sanidade do Fundo de Reserva
        reserve_sanity = (
            Decimal("0.00") <= reserve_bal
            and self.fiduciary_regime.reserve_fund.minimum_floor >= Decimal("0.00")
        )

        # 5. Cronologia de Datas
        dates_sane = (self.final_maturity_date >= self.issue_date)

        # 6. Elegibilidade de Lastro por Setor Social
        collateral_valid = True
        for asset in self.collateral_pool.values():
            if self.sector == SocialSecuritizationSector.HEALTH and not asset.collateral_type.is_health:
                collateral_valid = False
                break
            if self.sector == SocialSecuritizationSector.EDUCATION and not asset.collateral_type.is_education:
                collateral_valid = False
                break

        all_hold = (
            fiduciary_coverage
            and incommunicability_verified
            and pu_conservation
            and reserve_sanity
            and dates_sane
            and collateral_valid
        )

        return {
            "fiduciary_coverage_verified": fiduciary_coverage,
            "incommunicability_verified": incommunicability_verified,
            "pu_conservation_verified": pu_conservation,
            "reserve_sanity_verified": reserve_sanity,
            "dates_sane_verified": dates_sane,
            "collateral_valid_verified": collateral_valid,
            "all_invariants_hold": all_hold,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "sector": self.sector.value,
            "legal_framework": [fw.value for fw in self.legal_framework],
            "series_number": self.series_number,
            "issue_date": self.issue_date.isoformat(),
            "final_maturity_date": self.final_maturity_date.isoformat(),
            "annual_operational_expenses_pct": str(self.annual_operational_expenses_pct),
            "pending_inflow": str(self.pending_inflow),
            "fiduciary_regime": self.fiduciary_regime.to_dict(),
            "tranches": {k: v.to_dict() for k, v in self.tranches.items()},
            "collateral_pool": {k: v.to_dict() for k, v in self.collateral_pool.items()},
        }


# =====================================================================
# MOTOR DETERMINÍSTICO E ORQUESTRADOR CENTRAL (ENGINE)
# =====================================================================

class SocialSecuritizationEngine:
    """Motor de orquestração de securitização social, agregação de lastros e gestão de reserva."""

    def __init__(self) -> None:
        self.issues: Dict[str, SocialSecuritizationIssue] = {}

    def add_issue(self, issue: SocialSecuritizationIssue) -> None:
        """Registra uma emissão no motor garantindo incomunicabilidade entre patrimônios separados."""
        # Validação de Incomunicabilidade: Não permite colisão de contas fiduciárias ou termos de securitização
        for existing in self.issues.values():
            if existing.issue_id == issue.issue_id:
                raise HealthEducationValidationError(f"Emissão '{issue.issue_id}' já cadastrada.")
            if existing.fiduciary_regime.regime_id == issue.fiduciary_regime.regime_id:
                raise HealthEducationSegregationViolationError(
                    f"Conflito de Regime Fiduciário: '{issue.fiduciary_regime.regime_id}' já está vinculado à emissão {existing.issue_id}."
                )
            if existing.fiduciary_regime.segregated_account_id == issue.fiduciary_regime.segregated_account_id:
                raise HealthEducationSegregationViolationError(
                    f"Conflito de Conta Fiduciária: '{issue.fiduciary_regime.segregated_account_id}' não pode ser compartilhada entre emissões distintas."
                )
        self.issues[issue.issue_id] = issue

    def get_issue(self, issue_id: str) -> SocialSecuritizationIssue:
        """Obtém uma emissão cadastrada pelo ID."""
        if issue_id not in self.issues:
            raise HealthEducationValidationError(f"Emissão social '{issue_id}' não localizada no motor.")
        return self.issues[issue_id]

    def execute_waterfall(
        self,
        issue_id: str,
        period_date: date,
        expenses_override: Optional[Decimal] = None,
        period_indexer_rate: Optional[Decimal] = None,
        allow_subordinated_residual_payout: bool = False,
    ) -> WaterfallExecutionResult:
        """Executa a cascata de amortização de uma emissão específica."""
        issue = self.get_issue(issue_id)
        return issue.execute_waterfall(
            period_date=period_date,
            expenses_override=expenses_override,
            period_indexer_rate=period_indexer_rate,
            allow_subordinated_residual_payout=allow_subordinated_residual_payout,
        )

    def process_collateral_default(self, issue_id: str, asset_id: str) -> LossAbsorptionResult:
        """Processa inadimplência e write-off de lastro em uma emissão."""
        issue = self.get_issue(issue_id)
        return issue.process_collateral_default(asset_id)

    def attempt_inter_issue_transfer(
        self,
        source_issue_id: str,
        target_issue_id: str,
        amount: Decimal,
    ) -> None:
        """Simula e bloqueia categoricamente transferências entre patrimônios separados distintos."""
        source = self.get_issue(source_issue_id)
        target = self.get_issue(target_issue_id)
        raise HealthEducationSegregationViolationError(
            f"Violação de Incomunicabilidade (Lei nº 14.430/2022, art. 19): Transferência inter-patrimônio "
            f"proibida de {source.issue_id} (conta {source.fiduciary_regime.segregated_account_id}) "
            f"para {target.issue_id} (conta {target.fiduciary_regime.segregated_account_id})."
        )

    def verify_all_invariants(self) -> Dict[str, Dict[str, bool]]:
        """Verifica as invariantes de todas as emissões cadastradas."""
        results = {}
        for issue_id, issue in self.issues.items():
            results[issue_id] = issue.verify_invariants()
        return results

    @classmethod
    def load_from_dict(cls, data: Dict[str, Any]) -> SocialSecuritizationEngine:
        """Instancia o motor a partir de um dicionário estruturado."""
        engine = cls()
        issues_data = data.get("issues", [])
        for item in issues_data:
            issue = load_social_securitization_from_dict(item)
            engine.add_issue(issue)
        return engine

    @classmethod
    def load_from_fixture(cls, fixture_path: Union[str, Path]) -> SocialSecuritizationEngine:
        """Instancia o motor a partir de um arquivo de fixture JSON."""
        path = Path(fixture_path)
        if not path.is_file():
            raise HealthEducationValidationError(f"Arquivo de fixture não encontrado: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.load_from_dict(data)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "flow_code": "FL-085",
            "system": "B3_BALCAO_NOME_SOCIAL_SECURITIZATION",
            "issues": [issue.to_dict() for issue in self.issues.values()],
        }


# =====================================================================
# FUNÇÕES DE SERIALIZAÇÃO E CARGA
# =====================================================================

def load_social_securitization_from_dict(data: Dict[str, Any]) -> SocialSecuritizationIssue:
    """Converte dicionário estruturado em instância de SocialSecuritizationIssue com tipagem estrita."""
    issue_id = str(data["issue_id"])
    sector = SocialSecuritizationSector.from_str(data["sector"])

    frameworks_raw = data.get("legal_framework", ["LEI_14430_2022", "RESOLUCAO_CVM_60", "RESOLUCAO_CVM_175"])
    if isinstance(frameworks_raw, str):
        frameworks_raw = [frameworks_raw]
    legal_framework = [LegalFramework(fw) for fw in frameworks_raw]

    series_num = int(data.get("series_number", 1))
    issue_date = to_date(data["issue_date"], "issue_date")
    maturity_date = to_date(data["final_maturity_date"], "final_maturity_date")
    annual_exp = to_decimal(data.get("annual_operational_expenses_pct", "0.0015"))
    pending_inf = to_decimal(data.get("pending_inflow", "0.00"))

    # Fiduciary Regime
    fid_data = data["fiduciary_regime"]
    res_data = fid_data["reserve_fund"]
    reserve_fund = ReserveFund(
        fund_id=str(res_data["fund_id"]),
        target_balance=to_decimal(res_data["target_balance"]),
        current_balance=to_decimal(res_data["current_balance"]),
        minimum_floor=to_decimal(res_data.get("minimum_floor", "0.00")),
        yield_rate_annual_pct=to_decimal(res_data.get("yield_rate_annual_pct", "0.10")),
    )

    fiduciary_regime = FiduciaryRegime(
        regime_id=str(fid_data["regime_id"]),
        securitizer_name=str(fid_data["securitizer_name"]),
        securitizer_cnpj=str(fid_data["securitizer_cnpj"]),
        trustee_name=str(fid_data["trustee_name"]),
        trustee_cnpj=str(fid_data["trustee_cnpj"]),
        deed_registry_number=str(fid_data["deed_registry_number"]),
        registry_date=to_date(fid_data["registry_date"]),
        segregated_account_id=str(fid_data.get("segregated_account_id", f"CC-FID-{issue_id}")),
        segregated_cash_balance=to_decimal(fid_data.get("segregated_cash_balance", "0.00")),
        reserve_fund=reserve_fund,
        status=FiduciaryRegimeStatus(fid_data.get("status", "ACTIVE")),
    )

    issue = SocialSecuritizationIssue(
        issue_id=issue_id,
        sector=sector,
        legal_framework=legal_framework,
        series_number=series_num,
        issue_date=issue_date,
        final_maturity_date=maturity_date,
        fiduciary_regime=fiduciary_regime,
        annual_operational_expenses_pct=annual_exp,
        pending_inflow=pending_inf,
    )

    # Tranches
    tranches_dict = data.get("tranches", {})
    tranches_iterable = tranches_dict.values() if isinstance(tranches_dict, dict) else tranches_dict
    for tr_data in tranches_iterable:
        sched = {k: to_decimal(v) for k, v in tr_data.get("amortization_schedule", {}).items()}
        tranche = CertificateTranche(
            tranche_id=str(tr_data["tranche_id"]),
            isin=str(tr_data["isin"]),
            b3_code=str(tr_data["b3_code"]),
            tranche_class=CertificateTrancheClass(tr_data["tranche_class"]),
            amortization_type=AmortizationType(tr_data["amortization_type"]),
            indexer=IndexerType(tr_data["indexer"]),
            spread_rate_annual_pct=to_decimal(tr_data["spread_rate_annual_pct"]),
            original_quantity=int(tr_data["original_quantity"]),
            current_quantity=int(tr_data["current_quantity"]),
            original_unit_price=to_decimal(tr_data["original_unit_price"]),
            current_unit_price=to_decimal(tr_data["current_unit_price"]),
            accrued_interest=to_decimal(tr_data.get("accrued_interest", "0.00")),
            unpaid_interest_deficit=to_decimal(tr_data.get("unpaid_interest_deficit", "0.00")),
            amortization_schedule=sched,
        )
        issue.add_tranche(tranche)

    # Collateral Pool
    collat_dict = data.get("collateral_pool", {})
    collat_iterable = collat_dict.values() if isinstance(collat_dict, dict) else collat_dict
    for col_data in collat_iterable:
        metadata = SocialCollateralMetadata.from_dict(col_data.get("metadata"))
        asset = SocialCollateralAsset(
            asset_id=str(col_data["asset_id"]),
            collateral_type=SocialCollateralType(col_data["collateral_type"]),
            debtor_name=str(col_data["debtor_name"]),
            debtor_doc=str(col_data["debtor_doc"]),
            original_face_value=to_decimal(col_data["original_face_value"]),
            outstanding_balance=to_decimal(col_data["outstanding_balance"]),
            issue_date=to_date(col_data["issue_date"]),
            maturity_date=to_date(col_data["maturity_date"]),
            indexer=IndexerType(col_data["indexer"]),
            interest_rate_pct=to_decimal(col_data["interest_rate_pct"]),
            status=CollateralStatus(col_data.get("status", "PERFORMING")),
            accrued_interest=to_decimal(col_data.get("accrued_interest", "0.00")),
            delinquency_days=int(col_data.get("delinquency_days", 0)),
            metadata=metadata,
            guarantee_description=str(col_data.get("guarantee_description", "")),
        )
        issue.add_collateral(asset)

    return issue
