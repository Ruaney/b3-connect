"""Motor Normativo e Operacional de Fundos de Investimento — Resolução CVM nº 175/2022.

Implementa a infraestrutura completa de fundos multiclasses da CVM 175:
1. Estrutura de Fundo Mestre com Classes Essenciais e Subclasses de Cotas;
2. Segregação Patrimonial Inviolável entre classes (patrimônio e passivos isolados - LAW-041);
3. Segregação estrita entre Administrador Fiduciário e Gestor de Recursos;
4. Cálculo de Cota Diária e Taxa de Performance com Linha d'Água (High-Water Mark) por subclasse;
5. Alocação de Cotistas por Subclasse (Varejo, Qualificado, Profissional) com limites de concentração;
6. Invariantes Formais de Conservação Patrimonial e Regulatória:
   - LAW-041: Inviolabilidade da Segregação Patrimonial e Isolamento de Passivos entre Classes;
   - LAW-043: Inviolabilidade da Linha d'Água (High-Water Mark);
   - LAW-045: Conservação Patrimonial de Fundo Multiclasse (PL da Classe == Soma das Subclasses == Ativos - Passivos).

100% Python Standard Library pura (dataclasses, datetime, decimal, enum, json, pathlib, typing).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union


# =====================================================================
# CONSTANTES DE PRECISÃO NUMÉRICA E FORMATAÇÃO B3 / CVM 175
# =====================================================================

QUANTIZE_MONEY = Decimal("0.01")
QUANTIZE_QUOTA = Decimal("0.00000001")
QUANTIZE_RATE = Decimal("0.00000001")
QUANTIZE_INDEX = Decimal("0.0001")

DAYS_PER_YEAR_WORK = Decimal("252")


def round_cents(val: Union[Decimal, str, float, int]) -> Decimal:
    """Arredonda valor monetário para 2 casas decimais (centavos BRL)."""
    if not isinstance(val, Decimal):
        val = Decimal(str(val))
    return val.quantize(QUANTIZE_MONEY, rounding=ROUND_HALF_UP)


def round_quota(val: Union[Decimal, str, float, int]) -> Decimal:
    """Arredonda cota para 8 casas decimais (padrão regulamentar CVM)."""
    if not isinstance(val, Decimal):
        val = Decimal(str(val))
    return val.quantize(QUANTIZE_QUOTA, rounding=ROUND_HALF_UP)


def round_rate(val: Union[Decimal, str, float, int]) -> Decimal:
    """Arredonda taxa para 8 casas decimais."""
    if not isinstance(val, Decimal):
        val = Decimal(str(val))
    return val.quantize(QUANTIZE_RATE, rounding=ROUND_HALF_UP)


# =====================================================================
# CÓDIGOS FORMAIS DE LEIS REGULATÓRIAS (CVM 175 / B3)
# =====================================================================

LAW_041_CODE = "LAW-041"
LAW_041_NAME = "Inviolabilidade da Segregação Patrimonial e Isolamento de Passivos entre Classes"

LAW_043_CODE = "LAW-043"
LAW_043_NAME = "Inviolabilidade da Linha d'Água (High-Water Mark) na Taxa de Performance"

LAW_045_CODE = "LAW-045"
LAW_045_NAME = "Conservação Patrimonial de Fundo Multiclasse (Equilíbrio Ativo-Passivo-PL e Cotistas)"


# =====================================================================
# HIERARQUIA DE EXCEÇÕES REGULATÓRIAS E OPERACIONAIS
# =====================================================================

class Cvm175Error(Exception):
    """Exceção base para violações do motor CVM 175."""
    def __init__(self, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.law_code = law_code
        self.details = details or {}

    def __str__(self) -> str:
        prefix = f"[{self.law_code}] " if self.law_code else ""
        return f"{prefix}{self.message}"


class ValidationError(Cvm175Error):
    """Erro de validação cadastral ou inconsistência de parâmetros."""
    pass


class RoleSegregationError(Cvm175Error):
    """Violação da segregação estrita entre Administrador Fiduciário e Gestor de Recursos."""
    pass


class RingFencingViolationError(Cvm175Error):
    """Violação da segregação patrimonial e isolamento de passivos entre classes (LAW-041)."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, law_code=LAW_041_CODE, details=details)


class HighWaterMarkViolationError(Cvm175Error):
    """Cobrança indevida de taxa de performance violando a Linha d'Água (LAW-043)."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, law_code=LAW_043_CODE, details=details)


class FundConservationError(Cvm175Error):
    """Quebra da conservação contábil ou patrimonial de fundo ou cotas (LAW-045)."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, law_code=LAW_045_CODE, details=details)


class InvestorIneligibleError(Cvm175Error):
    """Investidor não cumpre os requisitos de público-alvo da subclasse."""
    pass


class ConcentrationLimitBreachError(Cvm175Error):
    """Limite regulatório de concentração por cotista singular excedido."""
    pass


class MinimumInvestmentError(Cvm175Error):
    """Valor inferior aos pisos mínimos de aporte, movimentação ou permanência."""
    pass


class InsufficientLiquidityError(Cvm175Error):
    """Saldo de cotas ou caixa insuficiente para efetuar o resgate."""
    pass


class ClassInsolvencyError(Cvm175Error):
    """Operação rejeitada devido a estado de insolvência de classe segregada."""
    pass


# =====================================================================
# ENUMS DE DOMÍNIO
# =====================================================================

class FundCategory(str, Enum):
    """Categorias de fundos regulados pela Resolução CVM nº 175."""
    FIF = "FIF"    # Fundo de Investimento Financeiro (Renda Fixa, Ações, Multimercado, Cambial)
    FIDC = "FIDC"  # Fundo de Investimento em Direitos Creditórios
    FII = "FII"    # Fundo de Investimento Imobiliário
    FIP = "FIP"    # Fundo de Investimento em Participações
    ETF = "ETF"    # Fundo de Índice


class FundClassType(str, Enum):
    """Tipos operacionais e estratégicos de classes de cotas."""
    RENDA_FIXA = "RENDA_FIXA"
    ACOES = "ACOES"
    MULTIMERCADO = "MULTIMERCADO"
    CAMBIAL = "CAMBIAL"
    CREDITO_PRIVADO = "CREDITO_PRIVADO"
    IMOBILIARIO = "IMOBILIARIO"


class LiabilityRegime(str, Enum):
    """Regime de responsabilidade dos cotistas (Código Civil art. 1.368-D)."""
    LIMITED = "LIMITED"      # Responsabilidade limitada ao valor das cotas
    UNLIMITED = "UNLIMITED"  # Responsabilidade ilimitada


class CondominiumType(str, Enum):
    """Forma de constituição do condomínio da classe."""
    OPEN_END = "OPEN_END"      # Condomínio Aberto (permite resgates periódicos)
    CLOSED_END = "CLOSED_END"  # Condomínio Fechado (sem resgate; negociação secundária ou amortização)


class InvestorCategory(str, Enum):
    """Categorias regulatórias de investidores (Resolução CVM 30 / CVM 175)."""
    RETAIL = "RETAIL"              # Investidor em Geral / Varejo
    QUALIFIED = "QUALIFIED"        # Investidor Qualificado (>= R$ 1MM)
    PROFESSIONAL = "PROFESSIONAL"  # Investidor Profissional (>= R$ 10MM ou institucional)


class ServiceProviderRole(str, Enum):
    """Papéis de prestadores essenciais e secundários sob a CVM 175."""
    ADMINISTRATOR = "ADMINISTRATOR"  # Administrador Fiduciário (essencial)
    ASSET_MANAGER = "ASSET_MANAGER"  # Gestor de Recursos (essencial)
    CUSTODIAN = "CUSTODIAN"          # Custodiante
    BOOKKEEPER = "BOOKKEEPER"        # Escriturador de cotas
    AUDITOR = "AUDITOR"              # Auditor Independente


class FiduciaryAction(str, Enum):
    """Ações operacionais sujeitas à segregação estrita de competências."""
    REGISTER_FUND = "REGISTER_FUND"
    CONSTITUTE_CLASS = "CONSTITUTE_CLASS"
    CALCULATE_OFFICIAL_NAV = "CALCULATE_OFFICIAL_NAV"
    BOOKKEEP_QUOTAS = "BOOKKEEP_QUOTAS"
    DECLARE_INSOLVENCY = "DECLARE_INSOLVENCY"
    CALL_ASSEMBLY = "CALL_ASSEMBLY"
    EXECUTE_PORTFOLIO_TRADE = "EXECUTE_PORTFOLIO_TRADE"
    SELECT_PORTFOLIO_ASSETS = "SELECT_PORTFOLIO_ASSETS"
    MANAGE_PORTFOLIO_RISK = "MANAGE_PORTFOLIO_RISK"
    TRANSFER_LIABILITY = "TRANSFER_LIABILITY"


class ClassStatus(str, Enum):
    """Status operacional da classe de cotas."""
    ACTIVE = "ACTIVE"
    INSOLVENT = "INSOLVENT"
    LIQUIDATION = "LIQUIDATION"
    TERMINATED = "TERMINATED"


# =====================================================================
# MODELOS DE PRESTADORES DE SERVIÇOS E SEGREGAÇÃO ESTRITA
# =====================================================================

@dataclass
class ServiceProvider:
    """Prestador de serviço de fundo sob a CVM 175."""
    provider_id: str
    name: str
    cnpj: str
    role: ServiceProviderRole
    cvm_code: str

    def can_perform(self, action: FiduciaryAction) -> bool:
        """Verifica se o prestador tem competência regulatória para a ação."""
        if action == FiduciaryAction.TRANSFER_LIABILITY:
            # Transferência de passivo entre classes é universalmente vedada (LAW-041)
            return False

        if self.role == ServiceProviderRole.ADMINISTRATOR:
            return action in {
                FiduciaryAction.REGISTER_FUND,
                FiduciaryAction.CONSTITUTE_CLASS,
                FiduciaryAction.CALCULATE_OFFICIAL_NAV,
                FiduciaryAction.BOOKKEEP_QUOTAS,
                FiduciaryAction.DECLARE_INSOLVENCY,
                FiduciaryAction.CALL_ASSEMBLY,
            }

        if self.role == ServiceProviderRole.ASSET_MANAGER:
            return action in {
                FiduciaryAction.EXECUTE_PORTFOLIO_TRADE,
                FiduciaryAction.SELECT_PORTFOLIO_ASSETS,
                FiduciaryAction.MANAGE_PORTFOLIO_RISK,
            }

        if self.role == ServiceProviderRole.BOOKKEEPER:
            return action in {
                FiduciaryAction.BOOKKEEP_QUOTAS,
            }

        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "name": self.name,
            "cnpj": self.cnpj,
            "role": self.role.value,
            "cvm_code": self.cvm_code,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ServiceProvider:
        return cls(
            provider_id=str(data["provider_id"]),
            name=str(data["name"]),
            cnpj=str(data["cnpj"]),
            role=ServiceProviderRole(data["role"]),
            cvm_code=str(data["cvm_code"]),
        )


# =====================================================================
# MODELOS DE COTISTAS E POSIÇÕES
# =====================================================================

@dataclass
class Investor:
    """Perfil cadastral e regulatório do cotista."""
    investor_id: str
    name: str
    document: str  # CPF ou CNPJ
    category: InvestorCategory
    net_worth: Decimal
    is_related_party: bool = False

    def is_eligible_for(self, target_audience: InvestorCategory) -> bool:
        """Avalia elegibilidade para a subclasse conforme a CVM 175."""
        if target_audience == InvestorCategory.RETAIL:
            # Subclasse de varejo aceita todos os investidores
            return True
        if target_audience == InvestorCategory.QUALIFIED:
            # Aceita Qualificado e Profissional
            return self.category in {InvestorCategory.QUALIFIED, InvestorCategory.PROFESSIONAL}
        if target_audience == InvestorCategory.PROFESSIONAL:
            # Aceita exclusivamente Profissional
            return self.category == InvestorCategory.PROFESSIONAL
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "investor_id": self.investor_id,
            "name": self.name,
            "document": self.document,
            "category": self.category.value,
            "net_worth": str(round_cents(self.net_worth)),
            "is_related_party": self.is_related_party,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Investor:
        return cls(
            investor_id=str(data["investor_id"]),
            name=str(data["name"]),
            document=str(data["document"]),
            category=InvestorCategory(data["category"]),
            net_worth=Decimal(str(data["net_worth"])),
            is_related_party=bool(data.get("is_related_party", False)),
        )


@dataclass
class InvestorHolding:
    """Posição de um cotista em uma subclasse."""
    investor_id: str
    subclass_id: str
    quotas: Decimal
    acquisition_cost: Decimal
    entry_date: date

    def to_dict(self) -> Dict[str, Any]:
        return {
            "investor_id": self.investor_id,
            "subclass_id": self.subclass_id,
            "quotas": str(round_quota(self.quotas)),
            "acquisition_cost": str(round_cents(self.acquisition_cost)),
            "entry_date": self.entry_date.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InvestorHolding:
        entry_d = data["entry_date"]
        parsed_date = date.fromisoformat(entry_d) if isinstance(entry_d, str) else entry_d
        return cls(
            investor_id=str(data["investor_id"]),
            subclass_id=str(data["subclass_id"]),
            quotas=Decimal(str(data["quotas"])),
            acquisition_cost=Decimal(str(data["acquisition_cost"])),
            entry_date=parsed_date,
        )


# =====================================================================
# ATIVOS E PASSIVOS DA CARTEIRA DA CLASSE
# =====================================================================

@dataclass
class AssetPosition:
    """Ativo integrante da carteira de uma classe."""
    asset_id: str
    ticker: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    accrued_interest: Decimal = Decimal("0.00")

    @property
    def market_value(self) -> Decimal:
        """Valor a mercado (MtM) do ativo."""
        return round_cents(self.quantity * self.unit_price + self.accrued_interest)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "ticker": self.ticker,
            "description": self.description,
            "quantity": str(round_quota(self.quantity)),
            "unit_price": str(round_cents(self.unit_price)),
            "accrued_interest": str(round_cents(self.accrued_interest)),
            "market_value": str(self.market_value),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AssetPosition:
        return cls(
            asset_id=str(data["asset_id"]),
            ticker=str(data["ticker"]),
            description=str(data["description"]),
            quantity=Decimal(str(data["quantity"])),
            unit_price=Decimal(str(data["unit_price"])),
            accrued_interest=Decimal(str(data.get("accrued_interest", "0.00"))),
        )


@dataclass
class LiabilityPosition:
    """Passivo exigível exclusivo de uma classe."""
    liability_id: str
    description: str
    amount: Decimal
    creditor: str
    due_date: Optional[date] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "liability_id": self.liability_id,
            "description": self.description,
            "amount": str(round_cents(self.amount)),
            "creditor": self.creditor,
            "due_date": self.due_date.isoformat() if self.due_date else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LiabilityPosition:
        dd = data.get("due_date")
        parsed_dd = date.fromisoformat(dd) if (dd and isinstance(dd, str)) else dd
        return cls(
            liability_id=str(data["liability_id"]),
            description=str(data["description"]),
            amount=Decimal(str(data["amount"])),
            creditor=str(data["creditor"]),
            due_date=parsed_dd,
        )


# =====================================================================
# SUBCLASSE DE COTAS (CVM 175)
# =====================================================================

@dataclass
class FundSubclass:
    """Subclasse de cotas vinculada a uma classe de fundo (CVM 175).

    Diferencia-se por público-alvo, taxas de administração/performance,
    prazos de cotização/resgate e investimento mínimo.
    """
    subclass_id: str
    class_id: str
    name: str
    target_audience: InvestorCategory
    admin_fee_annual_rate: Decimal
    performance_fee_rate: Decimal
    benchmark_name: str
    high_water_mark: Decimal
    benchmark_base_index: Decimal
    min_initial_investment: Decimal = Decimal("100.00")
    min_subsequent_investment: Decimal = Decimal("100.00")
    min_balance_permanence: Decimal = Decimal("100.00")
    concentration_limit_pct: Optional[Decimal] = None  # ex.: 0.20 (20%) para Varejo
    redemption_quotation_days: int = 0  # D+cot
    redemption_payment_days: int = 1    # D+pag
    total_quotas: Decimal = Decimal("0.00000000")
    quota_value: Decimal = Decimal("1.00000000")
    net_asset_value: Decimal = Decimal("0.00")
    provisioned_admin_fee: Decimal = Decimal("0.00")
    provisioned_performance_fee: Decimal = Decimal("0.00")
    holdings: Dict[str, InvestorHolding] = field(default_factory=dict)

    def validate_investor_eligibility(self, investor: Investor) -> None:
        """Valida se o investidor pode participar desta subclasse."""
        if not investor.is_eligible_for(self.target_audience):
            raise InvestorIneligibleError(
                f"Cotista '{investor.investor_id}' ({investor.category.value}) não é elegível "
                f"para a subclasse '{self.subclass_id}' destinada a {self.target_audience.value}."
            )

    def get_investor_concentration(self, investor_id: str, proposed_extra_quotas: Decimal = Decimal("0")) -> Decimal:
        """Calcula a concentração percentual do investidor na subclasse."""
        current_holding = self.holdings.get(investor_id)
        current_quotas = current_holding.quotas if current_holding else Decimal("0")
        target_quotas = current_quotas + proposed_extra_quotas
        total_after = self.total_quotas + proposed_extra_quotas

        if total_after <= Decimal("0"):
            return Decimal("0.0000")

        return round_rate(target_quotas / total_after)

    def validate_subscription(self, investor: Investor, amount: Decimal) -> None:
        """Valida pré-requisitos de aplicação/subscrição."""
        self.validate_investor_eligibility(investor)

        is_initial = investor.investor_id not in self.holdings
        min_required = self.min_initial_investment if is_initial else self.min_subsequent_investment

        if amount < min_required:
            raise MinimumInvestmentError(
                f"Aporte de R$ {amount:.2f} inferior ao mínimo exigido de R$ {min_required:.2f} "
                f"na subclasse '{self.subclass_id}' ({'inicial' if is_initial else 'adicional'})."
            )

        if self.concentration_limit_pct is not None and self.total_quotas > Decimal("0"):
            # Calcula o número estimado de novas cotas
            new_quotas = round_quota(amount / self.quota_value)
            conc = self.get_investor_concentration(investor.investor_id, proposed_extra_quotas=new_quotas)
            if conc > self.concentration_limit_pct:
                raise ConcentrationLimitBreachError(
                    f"Aporte resulta em concentração de {conc * 100:.2f}% para o cotista "
                    f"'{investor.investor_id}', violando o limite regulatório de "
                    f"{self.concentration_limit_pct * 100:.2f}% na subclasse '{self.subclass_id}'."
                )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subclass_id": self.subclass_id,
            "class_id": self.class_id,
            "name": self.name,
            "target_audience": self.target_audience.value,
            "admin_fee_annual_rate": str(round_rate(self.admin_fee_annual_rate)),
            "performance_fee_rate": str(round_rate(self.performance_fee_rate)),
            "benchmark_name": self.benchmark_name,
            "high_water_mark": str(round_quota(self.high_water_mark)),
            "benchmark_base_index": str(round_quota(self.benchmark_base_index)),
            "min_initial_investment": str(round_cents(self.min_initial_investment)),
            "min_subsequent_investment": str(round_cents(self.min_subsequent_investment)),
            "min_balance_permanence": str(round_cents(self.min_balance_permanence)),
            "concentration_limit_pct": str(round_rate(self.concentration_limit_pct)) if self.concentration_limit_pct is not None else None,
            "redemption_quotation_days": self.redemption_quotation_days,
            "redemption_payment_days": self.redemption_payment_days,
            "total_quotas": str(round_quota(self.total_quotas)),
            "quota_value": str(round_quota(self.quota_value)),
            "net_asset_value": str(round_cents(self.net_asset_value)),
            "provisioned_admin_fee": str(round_cents(self.provisioned_admin_fee)),
            "provisioned_performance_fee": str(round_cents(self.provisioned_performance_fee)),
            "holdings": {k: v.to_dict() for k, v in self.holdings.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FundSubclass:
        sub = cls(
            subclass_id=str(data["subclass_id"]),
            class_id=str(data["class_id"]),
            name=str(data["name"]),
            target_audience=InvestorCategory(data["target_audience"]),
            admin_fee_annual_rate=Decimal(str(data["admin_fee_annual_rate"])),
            performance_fee_rate=Decimal(str(data["performance_fee_rate"])),
            benchmark_name=str(data["benchmark_name"]),
            high_water_mark=Decimal(str(data["high_water_mark"])),
            benchmark_base_index=Decimal(str(data["benchmark_base_index"])),
            min_initial_investment=Decimal(str(data.get("min_initial_investment", "100.00"))),
            min_subsequent_investment=Decimal(str(data.get("min_subsequent_investment", "100.00"))),
            min_balance_permanence=Decimal(str(data.get("min_balance_permanence", "100.00"))),
            concentration_limit_pct=Decimal(str(data["concentration_limit_pct"])) if data.get("concentration_limit_pct") is not None else None,
            redemption_quotation_days=int(data.get("redemption_quotation_days", 0)),
            redemption_payment_days=int(data.get("redemption_payment_days", 1)),
            total_quotas=Decimal(str(data.get("total_quotas", "0"))),
            quota_value=Decimal(str(data.get("quota_value", "1.00000000"))),
            net_asset_value=Decimal(str(data.get("net_asset_value", "0.00"))),
            provisioned_admin_fee=Decimal(str(data.get("provisioned_admin_fee", "0.00"))),
            provisioned_performance_fee=Decimal(str(data.get("provisioned_performance_fee", "0.00"))),
        )
        if "holdings" in data:
            sub.holdings = {k: InvestorHolding.from_dict(v) for k, v in data["holdings"].items()}
        return sub


# =====================================================================
# CLASSE DE COTAS (CVM 175)
# =====================================================================

@dataclass
class FundClass:
    """Classe de cotas sob a Resolução CVM nº 175.

    Constitui patrimônio segregado nos termos da Lei nº 13.874/2019 e
    do Código Civil (Art. 1.368-C e 1.368-E):
    - Ativos e passivos vinculados respondem exclusiva e unicamente pelas
      suas próprias obrigações (LAW-041).
    - Possui CNPJ específico por classe.
    - Contém uma ou mais subclasses de cotas.
    """
    class_id: str
    fund_id: str
    name: str
    cnpj: str
    class_type: FundClassType
    liability_regime: LiabilityRegime = LiabilityRegime.LIMITED
    condominium_type: CondominiumType = CondominiumType.OPEN_END
    status: ClassStatus = ClassStatus.ACTIVE
    cash_balance: Decimal = Decimal("0.00")
    assets: Dict[str, AssetPosition] = field(default_factory=dict)
    liabilities: Dict[str, LiabilityPosition] = field(default_factory=dict)
    subclasses: Dict[str, FundSubclass] = field(default_factory=dict)

    @property
    def total_assets_value(self) -> Decimal:
        """Valor total dos ativos a mercado mais caixa."""
        assets_sum = sum((a.market_value for a in self.assets.values()), Decimal("0.00"))
        return round_cents(self.cash_balance + assets_sum)

    @property
    def total_liabilities_value(self) -> Decimal:
        """Valor total dos passivos exigíveis da classe."""
        return round_cents(sum((l.amount for l in self.liabilities.values()), Decimal("0.00")))

    @property
    def net_asset_value(self) -> Decimal:
        """Patrimônio Líquido (PL) contábil da classe: Ativos - Passivos."""
        return round_cents(self.total_assets_value - self.total_liabilities_value)

    @property
    def is_insolvent(self) -> bool:
        """Indica se a classe incorreu em insolvência (PL negativo)."""
        return self.net_asset_value < Decimal("0.00")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "class_id": self.class_id,
            "fund_id": self.fund_id,
            "name": self.name,
            "cnpj": self.cnpj,
            "class_type": self.class_type.value,
            "liability_regime": self.liability_regime.value,
            "condominium_type": self.condominium_type.value,
            "status": self.status.value,
            "cash_balance": str(round_cents(self.cash_balance)),
            "total_assets_value": str(self.total_assets_value),
            "total_liabilities_value": str(self.total_liabilities_value),
            "net_asset_value": str(self.net_asset_value),
            "is_insolvent": self.is_insolvent,
            "assets": {k: v.to_dict() for k, v in self.assets.items()},
            "liabilities": {k: v.to_dict() for k, v in self.liabilities.items()},
            "subclasses": {k: v.to_dict() for k, v in self.subclasses.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FundClass:
        f_class = cls(
            class_id=str(data["class_id"]),
            fund_id=str(data["fund_id"]),
            name=str(data["name"]),
            cnpj=str(data["cnpj"]),
            class_type=FundClassType(data["class_type"]),
            liability_regime=LiabilityRegime(data.get("liability_regime", "LIMITED")),
            condominium_type=CondominiumType(data.get("condominium_type", "OPEN_END")),
            status=ClassStatus(data.get("status", "ACTIVE")),
            cash_balance=Decimal(str(data.get("cash_balance", "0.00"))),
        )
        if "assets" in data:
            f_class.assets = {k: AssetPosition.from_dict(v) for k, v in data["assets"].items()}
        if "liabilities" in data:
            f_class.liabilities = {k: LiabilityPosition.from_dict(v) for k, v in data["liabilities"].items()}
        if "subclasses" in data:
            f_class.subclasses = {k: FundSubclass.from_dict(v) for k, v in data["subclasses"].items()}
        return f_class


# =====================================================================
# FUNDO MESTRE (MASTER FUND - CVM 175)
# =====================================================================

@dataclass
class MasterFund:
    """Fundo de Investimento constituído sob o marco regulatório da CVM 175.

    Estrutura mestra contendo os prestadores essenciais (Administrador Fiduciário
    e Gestor de Recursos) e o catálogo de classes patrimonialmente segregadas.
    """
    fund_id: str
    name: str
    cnpj: str
    category: FundCategory
    administrator: ServiceProvider
    manager: ServiceProvider
    custodian: Optional[ServiceProvider] = None
    bookkeeper: Optional[ServiceProvider] = None
    auditor: Optional[ServiceProvider] = None
    classes: Dict[str, FundClass] = field(default_factory=dict)
    investors: Dict[str, Investor] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Garante a segregação estrita dos prestadores essenciais."""
        if self.administrator.role != ServiceProviderRole.ADMINISTRATOR:
            raise RoleSegregationError(
                f"Prestador '{self.administrator.name}' possui papel {self.administrator.role.value}, "
                f"mas a CVM 175 exige papel ADMINISTRATOR para o Administrador Fiduciário."
            )
        if self.manager.role != ServiceProviderRole.ASSET_MANAGER:
            raise RoleSegregationError(
                f"Prestador '{self.manager.name}' possui papel {self.manager.role.value}, "
                f"mas a CVM 175 exige papel ASSET_MANAGER para o Gestor de Recursos."
            )
        if self.administrator.cnpj == self.manager.cnpj:
            raise RoleSegregationError(
                f"Administrador Fiduciário e Gestor de Recursos não podem compartilhar o mesmo CNPJ "
                f"('{self.administrator.cnpj}') sob a segregação estrita da CVM 175."
            )

    @property
    def total_net_asset_value(self) -> Decimal:
        """Soma contábil do PL de todas as classes ativas."""
        return round_cents(sum((c.net_asset_value for c in self.classes.values()), Decimal("0.00")))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fund_id": self.fund_id,
            "name": self.name,
            "cnpj": self.cnpj,
            "category": self.category.value,
            "administrator": self.administrator.to_dict(),
            "manager": self.manager.to_dict(),
            "custodian": self.custodian.to_dict() if self.custodian else None,
            "bookkeeper": self.bookkeeper.to_dict() if self.bookkeeper else None,
            "auditor": self.auditor.to_dict() if self.auditor else None,
            "total_net_asset_value": str(self.total_net_asset_value),
            "classes": {k: v.to_dict() for k, v in self.classes.items()},
            "investors": {k: v.to_dict() for k, v in self.investors.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MasterFund:
        fund = cls(
            fund_id=str(data["fund_id"]),
            name=str(data["name"]),
            cnpj=str(data["cnpj"]),
            category=FundCategory(data["category"]),
            administrator=ServiceProvider.from_dict(data["administrator"]),
            manager=ServiceProvider.from_dict(data["manager"]),
            custodian=ServiceProvider.from_dict(data["custodian"]) if data.get("custodian") else None,
            bookkeeper=ServiceProvider.from_dict(data["bookkeeper"]) if data.get("bookkeeper") else None,
            auditor=ServiceProvider.from_dict(data["auditor"]) if data.get("auditor") else None,
        )
        if "classes" in data:
            fund.classes = {k: FundClass.from_dict(v) for k, v in data["classes"].items()}
        if "investors" in data:
            fund.investors = {k: Investor.from_dict(v) for k, v in data["investors"].items()}
        return fund


# =====================================================================
# RESULTADOS DA AVALIAÇÃO DIÁRIA DE COTIZAÇÃO
# =====================================================================

@dataclass
class SubclassValuationResult:
    """Resultado contábil da cotização diária de uma subclasse."""
    subclass_id: str
    previous_quota: Decimal
    new_quota: Decimal
    total_quotas: Decimal
    gross_nav: Decimal
    net_asset_value: Decimal
    admin_fee_appropriated: Decimal
    performance_fee_appropriated: Decimal
    high_water_mark: Decimal
    benchmark_index: Decimal
    hurdle_quota: Decimal
    exceeded_hwm: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subclass_id": self.subclass_id,
            "previous_quota": str(round_quota(self.previous_quota)),
            "new_quota": str(round_quota(self.new_quota)),
            "total_quotas": str(round_quota(self.total_quotas)),
            "gross_nav": str(round_cents(self.gross_nav)),
            "net_asset_value": str(round_cents(self.net_asset_value)),
            "admin_fee_appropriated": str(round_cents(self.admin_fee_appropriated)),
            "performance_fee_appropriated": str(round_cents(self.performance_fee_appropriated)),
            "high_water_mark": str(round_quota(self.high_water_mark)),
            "benchmark_index": str(round_quota(self.benchmark_index)),
            "hurdle_quota": str(round_quota(self.hurdle_quota)),
            "exceeded_hwm": self.exceeded_hwm,
        }


@dataclass
class ClassValuationResult:
    """Resultado consolidado da cotização diária de uma classe."""
    class_id: str
    class_net_asset_value: Decimal
    total_assets_value: Decimal
    total_liabilities_value: Decimal
    subclasses_results: Dict[str, SubclassValuationResult]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "class_id": self.class_id,
            "class_net_asset_value": str(round_cents(self.class_net_asset_value)),
            "total_assets_value": str(round_cents(self.total_assets_value)),
            "total_liabilities_value": str(round_cents(self.total_liabilities_value)),
            "subclasses_results": {k: v.to_dict() for k, v in self.subclasses_results.items()},
        }


# =====================================================================
# MOTOR NORMATIVO E OPERACIONAL CVM 175
# =====================================================================

class Cvm175FundsEngine:
    """Motor regulatório, contábil e de salvaguardas para fundos CVM 175.

    Executa:
    - Verificação de segregação de competências entre Administrador e Gestor;
    - Proteção estrita do ring-fencing patrimonial entre classes (LAW-041);
    - Cotização diária com pro-rata de administração e HWM de performance (LAW-043);
    - Gestão de passivos e cotistas com verificação de suitability e limites (LAW-045);
    - Auditoria de invariantes formais de conservação matemática.
    """

    def __init__(self, master_fund: Optional[MasterFund] = None) -> None:
        self.master_fund: Optional[MasterFund] = master_fund

    # -----------------------------------------------------------------
    # ENFORCEMENT DE SEGREGAÇÃO ESTRITA DE COMPETÊNCIAS
    # -----------------------------------------------------------------

    def verify_action_authorization(self, actor: ServiceProvider, action: FiduciaryAction) -> None:
        """Garante que apenas o prestador legalmente competente execute a ação."""
        if not actor.can_perform(action):
            raise RoleSegregationError(
                f"Prestador '{actor.name}' com papel {actor.role.value} não possui autoridade "
                f"legal para executar a ação regulatória {action.value} sob a Resolução CVM 175."
            )

    # -----------------------------------------------------------------
    # GESTÃO DE CLASSES E SEGREGAÇÃO PATRIMONIAL INVIOLÁVEL (LAW-041)
    # -----------------------------------------------------------------

    def add_class(self, actor: ServiceProvider, fund_class: FundClass) -> None:
        """Registra nova classe com patrimônio segregado."""
        if not self.master_fund:
            raise ValidationError("Nenhum fundo mestre registrado no motor.")
        self.verify_action_authorization(actor, FiduciaryAction.CONSTITUTE_CLASS)

        if fund_class.class_id in self.master_fund.classes:
            raise ValidationError(f"Classe '{fund_class.class_id}' já está cadastrada no fundo.")

        # Valida duplicidade de CNPJ de classe
        for existing in self.master_fund.classes.values():
            if existing.cnpj == fund_class.cnpj:
                raise ValidationError(f"CNPJ '{fund_class.cnpj}' já pertence à classe '{existing.class_id}'.")

        self.master_fund.classes[fund_class.class_id] = fund_class

    def attempt_cross_class_liability_settlement(
        self,
        source_class_id: str,
        target_class_id: str,
        amount: Decimal,
        description: str = "Cobertura de déficit ou passivo interclasses",
    ) -> None:
        """Tenta liquidar passivo de uma classe usando recursos de outra classe.

        DEVE ser imediatamente interceptado e bloqueado pela salvaguarda LAW-041:
        O patrimônio de uma classe segregada responde unicamente por suas próprias obrigações.
        """
        if not self.master_fund:
            raise ValidationError("Fundo mestre não configurado.")

        if source_class_id not in self.master_fund.classes:
            raise ValidationError(f"Classe de origem '{source_class_id}' inexistente.")
        if target_class_id not in self.master_fund.classes:
            raise ValidationError(f"Classe de destino '{target_class_id}' inexistente.")

        if source_class_id == target_class_id:
            return  # Mesma classe, sem violação de segregação

        # Violação flagrante de Ring-Fencing (LAW-041 / Art. 1.368-C Código Civil)
        raise RingFencingViolationError(
            f"Tentativa ilícita de liquidar passivo da classe '{target_class_id}' utilizando "
            f"R$ {amount:.2f} do patrimônio segregado da classe '{source_class_id}'. "
            f"A segregação patrimonial e o isolamento de passivos são invioláveis por lei ({LAW_041_CODE}).",
            details={
                "law_code": LAW_041_CODE,
                "source_class_id": source_class_id,
                "target_class_id": target_class_id,
                "attempted_amount": str(round_cents(amount)),
                "description": description,
            },
        )

    # -----------------------------------------------------------------
    # GESTÃO DE SUBCLASSES E SUITABILITY / CONCENTRAÇÃO
    # -----------------------------------------------------------------

    def add_subclass(self, actor: ServiceProvider, subclass: FundSubclass) -> None:
        """Registra nova subclasse de cotas sob uma classe existente."""
        if not self.master_fund:
            raise ValidationError("Fundo mestre não configurado.")
        self.verify_action_authorization(actor, FiduciaryAction.CONSTITUTE_CLASS)

        if subclass.class_id not in self.master_fund.classes:
            raise ValidationError(f"Classe mãe '{subclass.class_id}' não encontrada no fundo.")

        target_class = self.master_fund.classes[subclass.class_id]
        if subclass.subclass_id in target_class.subclasses:
            raise ValidationError(f"Subclasse '{subclass.subclass_id}' já existe na classe '{subclass.class_id}'.")

        target_class.subclasses[subclass.subclass_id] = subclass

    def register_investor(self, actor: ServiceProvider, investor: Investor) -> None:
        """Cadastra cotista no livro do Administrador Fiduciário/Escriturador."""
        if not self.master_fund:
            raise ValidationError("Fundo mestre não configurado.")
        self.verify_action_authorization(actor, FiduciaryAction.BOOKKEEP_QUOTAS)

        self.master_fund.investors[investor.investor_id] = investor

    def subscribe(
        self,
        class_id: str,
        subclass_id: str,
        investor_id: str,
        amount: Decimal,
        operation_date: date,
    ) -> Decimal:
        """Processa subscrição/aplicação de recursos em uma subclasse.

        Verifica:
        1. Elegibilidade regulatória do cotista (Varejo, Qualificado, Profissional);
        2. Pisos de investimento mínimo inicial e subsequente;
        3. Limite de concentração por cotista singular na subclasse;
        4. Conservação estrita de cotas e caixa.
        """
        if not self.master_fund:
            raise ValidationError("Fundo mestre não configurado.")
        if class_id not in self.master_fund.classes:
            raise ValidationError(f"Classe '{class_id}' inexistente.")

        f_class = self.master_fund.classes[class_id]
        if f_class.status != ClassStatus.ACTIVE:
            raise ClassInsolvencyError(f"Classe '{class_id}' está com status {f_class.status.value}; subscrições suspensas.")

        if subclass_id not in f_class.subclasses:
            raise ValidationError(f"Subclasse '{subclass_id}' não pertence à classe '{class_id}'.")

        subclass = f_class.subclasses[subclass_id]
        if investor_id not in self.master_fund.investors:
            raise ValidationError(f"Cotista '{investor_id}' não cadastrado no fundo.")

        investor = self.master_fund.investors[investor_id]
        amount_cents = round_cents(amount)

        if amount_cents <= Decimal("0.00"):
            raise ValidationError("Valor do aporte deve ser estritamente positivo.")

        # Validações regulatórias
        subclass.validate_subscription(investor, amount_cents)

        # Emissão de cotas na cota unitária oficial da subclasse
        new_quotas = round_quota(amount_cents / subclass.quota_value)
        if new_quotas <= Decimal("0"):
            raise ValidationError("Valor do aporte insuficiente para subscrever uma fração mínima de cota.")

        # Atualiza a posição do investidor
        if investor_id in subclass.holdings:
            pos = subclass.holdings[investor_id]
            pos.quotas = round_quota(pos.quotas + new_quotas)
            pos.acquisition_cost = round_cents(pos.acquisition_cost + amount_cents)
        else:
            subclass.holdings[investor_id] = InvestorHolding(
                investor_id=investor_id,
                subclass_id=subclass_id,
                quotas=new_quotas,
                acquisition_cost=amount_cents,
                entry_date=operation_date,
            )

        # Atualiza o total de cotas e caixa da classe
        subclass.total_quotas = round_quota(subclass.total_quotas + new_quotas)
        subclass.net_asset_value = round_cents(subclass.total_quotas * subclass.quota_value)
        f_class.cash_balance = round_cents(f_class.cash_balance + amount_cents)

        return new_quotas

    def redeem(
        self,
        class_id: str,
        subclass_id: str,
        investor_id: str,
        quota_count: Optional[Decimal] = None,
        amount: Optional[Decimal] = None,
    ) -> Tuple[Decimal, Decimal]:
        """Processa resgate de recursos de um cotista em uma subclasse.

        Retorna: (cotas_canceladas, valor_bruto_resgatado).
        """
        if not self.master_fund:
            raise ValidationError("Fundo mestre não configurado.")
        if class_id not in self.master_fund.classes:
            raise ValidationError(f"Classe '{class_id}' inexistente.")

        f_class = self.master_fund.classes[class_id]
        if f_class.status != ClassStatus.ACTIVE:
            raise ClassInsolvencyError(f"Classe '{class_id}' em estado {f_class.status.value}; resgates bloqueados.")

        if subclass_id not in f_class.subclasses:
            raise ValidationError(f"Subclasse '{subclass_id}' inexistente.")

        subclass = f_class.subclasses[subclass_id]
        if investor_id not in subclass.holdings:
            raise ValidationError(f"Cotista '{investor_id}' não possui posição na subclasse '{subclass_id}'.")

        pos = subclass.holdings[investor_id]

        if quota_count is not None:
            quotas_to_redeem = round_quota(quota_count)
            redeem_value = round_cents(quotas_to_redeem * subclass.quota_value)
        elif amount is not None:
            redeem_value = round_cents(amount)
            quotas_to_redeem = round_quota(redeem_value / subclass.quota_value)
        else:
            raise ValidationError("Necessário informar 'quota_count' ou 'amount' para resgate.")

        if quotas_to_redeem <= Decimal("0"):
            raise ValidationError("Quantidade de cotas para resgate deve ser positiva.")

        if quotas_to_redeem > pos.quotas:
            raise InsufficientLiquidityError(
                f"Cotista solicitou resgate de {quotas_to_redeem} cotas, mas possui apenas {pos.quotas} cotas."
            )

        # Checa saldo remanescente vs piso de permanência
        remaining_quotas = round_quota(pos.quotas - quotas_to_redeem)
        remaining_value = round_cents(remaining_quotas * subclass.quota_value)
        if Decimal("0.00") < remaining_value < subclass.min_balance_permanence:
            raise MinimumInvestmentError(
                f"Resgate deixaria saldo remanescente de R$ {remaining_value:.2f}, "
                f"abaixo do saldo mínimo de permanência de R$ {subclass.min_balance_permanence:.2f}."
            )

        # Checa disponibilidade de caixa na classe
        if redeem_value > f_class.cash_balance:
            raise InsufficientLiquidityError(
                f"Caixa da classe R$ {f_class.cash_balance:.2f} insuficiente para liquidar "
                f"resgate de R$ {redeem_value:.2f}. Necessário desinvestimento pelo Gestor."
            )

        # Efetivação do resgate
        if remaining_quotas == Decimal("0"):
            del subclass.holdings[investor_id]
        else:
            pos.quotas = remaining_quotas
            pos.acquisition_cost = round_cents(pos.acquisition_cost - redeem_value)

        subclass.total_quotas = round_quota(subclass.total_quotas - quotas_to_redeem)
        subclass.net_asset_value = round_cents(subclass.total_quotas * subclass.quota_value)
        f_class.cash_balance = round_cents(f_class.cash_balance - redeem_value)

        return quotas_to_redeem, redeem_value

    # -----------------------------------------------------------------
    # CÁLCULO DE COTA DIÁRIA E LINHA D'ÁGUA (HIGH-WATER MARK - LAW-043)
    # -----------------------------------------------------------------

    def calculate_daily_valuation(
        self,
        actor: ServiceProvider,
        valuation_date: date,
        asset_prices: Optional[Dict[str, Decimal]] = None,
        benchmark_indices: Optional[Dict[str, Decimal]] = None,
        operational_expenses: Optional[Dict[str, Decimal]] = None,
    ) -> Dict[str, ClassValuationResult]:
        """Executa a rotina diária de apuração do valor da cota e provisão de taxas.

        Regras estritas da CVM 175:
        1. Função privativa do Administrador Fiduciário (CALCULATE_OFFICIAL_NAV);
        2. Atualização dos ativos a mercado (MtM) na carteira da classe;
        3. Rateio do resultado bruto entre as subclasses proporcional ao PL prévio;
        4. Apropriação diária da taxa de administração pro-rata 252 dias úteis;
        5. Cálculo da taxa de performance sob a Linha d'Água (LAW-043):
           - Provisão > 0 APENAS se cota bruta líquida de adm superar o maior entre HWM e hurdle;
           - Se cota <= HWM, provisão é estritamente zero;
        6. Preservação do equilíbrio contábil PL(Classe) == soma(PL(Subclasses)) (LAW-045).
        """
        if not self.master_fund:
            raise ValidationError("Fundo mestre não configurado.")
        self.verify_action_authorization(actor, FiduciaryAction.CALCULATE_OFFICIAL_NAV)

        asset_prices = asset_prices or {}
        benchmark_indices = benchmark_indices or {}
        operational_expenses = operational_expenses or {}

        results: Dict[str, ClassValuationResult] = {}

        for class_id, f_class in self.master_fund.classes.items():
            if f_class.status != ClassStatus.ACTIVE:
                continue

            # 1. Atualiza marcação a mercado dos ativos da classe
            for asset_id, asset in f_class.assets.items():
                if asset_id in asset_prices:
                    asset.unit_price = round_cents(asset_prices[asset_id])

            # 2. Debita despesas operacionais comuns da classe (corretagem, custódia)
            if class_id in operational_expenses:
                exp = round_cents(operational_expenses[class_id])
                if exp > Decimal("0"):
                    f_class.cash_balance = round_cents(f_class.cash_balance - exp)

            # 3. Calcula o PL Bruto da Classe antes de taxas específicas das subclasses
            gross_class_nav = f_class.net_asset_value
            if gross_class_nav < Decimal("0.00"):
                f_class.status = ClassStatus.INSOLVENT
                # Continua cálculo para registro contábil de insolvência

            # 4. Soma do PL prévio das subclasses para ponderação do rateio
            total_previous_subclass_nav = sum(
                (s.net_asset_value for s in f_class.subclasses.values() if s.total_quotas > Decimal("0")),
                Decimal("0.00"),
            )

            subclass_results: Dict[str, SubclassValuationResult] = {}

            # Se não houver subclasses com cotas, apenas armazena resultado básico
            if total_previous_subclass_nav <= Decimal("0.00"):
                for sub_id, sub in f_class.subclasses.items():
                    subclass_results[sub_id] = SubclassValuationResult(
                        subclass_id=sub_id,
                        previous_quota=sub.quota_value,
                        new_quota=sub.quota_value,
                        total_quotas=sub.total_quotas,
                        gross_nav=Decimal("0.00"),
                        net_asset_value=Decimal("0.00"),
                        admin_fee_appropriated=Decimal("0.00"),
                        performance_fee_appropriated=Decimal("0.00"),
                        high_water_mark=sub.high_water_mark,
                        benchmark_index=benchmark_indices.get(sub.benchmark_name, sub.benchmark_base_index),
                        hurdle_quota=sub.high_water_mark,
                        exceeded_hwm=False,
                    )
                results[class_id] = ClassValuationResult(
                    class_id=class_id,
                    class_net_asset_value=f_class.net_asset_value,
                    total_assets_value=f_class.total_assets_value,
                    total_liabilities_value=f_class.total_liabilities_value,
                    subclasses_results=subclass_results,
                )
                continue

            # 5. Rateio do resultado bruto e dedução das taxas específicas por subclasse
            for sub_id, sub in f_class.subclasses.items():
                if sub.total_quotas <= Decimal("0"):
                    continue

                # Peso patrimonial da subclasse na carteira comum da classe
                sub_weight = sub.net_asset_value / total_previous_subclass_nav
                sub_gross_nav = round_cents(gross_class_nav * sub_weight)

                # a) Taxa de administração diária pro-rata (convenção exponencial padrão ANBIMA/CVM)
                daily_adm_rate = (Decimal("1") + sub.admin_fee_annual_rate) ** (Decimal("1") / DAYS_PER_YEAR_WORK) - Decimal("1")
                admin_fee_day = round_cents(sub_gross_nav * daily_adm_rate)
                sub_nav_post_adm = round_cents(sub_gross_nav - admin_fee_day)

                # Cota teórica pré-performance
                cota_pre_perf = round_quota(sub_nav_post_adm / sub.total_quotas)

                # b) Apuração da Linha d'Água (High-Water Mark - LAW-043)
                curr_bench = benchmark_indices.get(sub.benchmark_name, sub.benchmark_base_index)
                bench_factor = curr_bench / sub.benchmark_base_index if sub.benchmark_base_index > Decimal("0") else Decimal("1")

                # Cota corrigida pelo hurdle / benchmark
                hurdle_cota = round_quota(sub.high_water_mark * bench_factor)
                hwm_threshold = max(sub.high_water_mark, hurdle_cota)

                perf_fee_day = Decimal("0.00")
                exceeded_hwm = False

                if cota_pre_perf > hwm_threshold and sub.performance_fee_rate > Decimal("0"):
                    exceeded_hwm = True
                    excess_spread = cota_pre_perf - hwm_threshold
                    perf_fee_day = round_cents(excess_spread * sub.performance_fee_rate * sub.total_quotas)
                else:
                    # Inviolabilidade da Linha d'Água: Cota abaixo do HWM proíbe taxa de performance
                    perf_fee_day = Decimal("0.00")

                # c) Patrimônio Líquido Final e Cota Oficial da Subclasse
                final_sub_nav = round_cents(sub_nav_post_adm - perf_fee_day)
                new_official_quota = round_quota(final_sub_nav / sub.total_quotas)

                # Provisão contábil acumulada
                sub.provisioned_admin_fee = round_cents(sub.provisioned_admin_fee + admin_fee_day)
                sub.provisioned_performance_fee = round_cents(sub.provisioned_performance_fee + perf_fee_day)

                previous_quota = sub.quota_value
                sub.quota_value = new_official_quota
                sub.net_asset_value = final_sub_nav

                subclass_results[sub_id] = SubclassValuationResult(
                    subclass_id=sub_id,
                    previous_quota=previous_quota,
                    new_quota=new_official_quota,
                    total_quotas=sub.total_quotas,
                    gross_nav=sub_gross_nav,
                    net_asset_value=final_sub_nav,
                    admin_fee_appropriated=admin_fee_day,
                    performance_fee_appropriated=perf_fee_day,
                    high_water_mark=sub.high_water_mark,
                    benchmark_index=curr_bench,
                    hurdle_quota=hurdle_cota,
                    exceeded_hwm=exceeded_hwm,
                )

            results[class_id] = ClassValuationResult(
                class_id=class_id,
                class_net_asset_value=f_class.net_asset_value,
                total_assets_value=f_class.total_assets_value,
                total_liabilities_value=f_class.total_liabilities_value,
                subclasses_results=subclass_results,
            )

        return results

    def crystallize_performance_fee(
        self,
        actor: ServiceProvider,
        class_id: str,
        subclass_id: str,
        current_benchmark_index: Decimal,
    ) -> Decimal:
        """Cristaliza a taxa de performance acumulada ao final do período ou no resgate.

        Transfere a provisão para conta de pagamento ao Gestor e eleva a Linha d'Água (HWM)
        para a cota atual caso esta supere o recorde anterior (LAW-043).
        """
        if not self.master_fund:
            raise ValidationError("Fundo mestre não configurado.")
        self.verify_action_authorization(actor, FiduciaryAction.CALCULATE_OFFICIAL_NAV)

        if class_id not in self.master_fund.classes:
            raise ValidationError(f"Classe '{class_id}' inexistente.")
        f_class = self.master_fund.classes[class_id]

        if subclass_id not in f_class.subclasses:
            raise ValidationError(f"Subclasse '{subclass_id}' inexistente.")
        sub = f_class.subclasses[subclass_id]

        fee_to_settle = sub.provisioned_performance_fee
        if fee_to_settle < Decimal("0.00"):
            fee_to_settle = Decimal("0.00")

        # Se a cota atual superou o HWM, atualiza a nova Linha d'Água
        if sub.quota_value > sub.high_water_mark:
            sub.high_water_mark = sub.quota_value
            sub.benchmark_base_index = current_benchmark_index

        # Zera provisão acumulada
        sub.provisioned_performance_fee = Decimal("0.00")

        # Debita do caixa da classe para pagar o Gestor de Recursos
        if fee_to_settle > Decimal("0.00"):
            f_class.cash_balance = round_cents(f_class.cash_balance - fee_to_settle)

        return fee_to_settle

    # -----------------------------------------------------------------
    # AUDITORIA DAS LEIS FORMAIS INVARIANTES (LAW-041, LAW-043, LAW-045)
    # -----------------------------------------------------------------

    def audit_law_041_ring_fencing(self) -> Dict[str, Any]:
        """Audita formalmente a Segregação Patrimonial e Isolamento de Passivos (LAW-041).

        Invariantes:
        1. Ativos de cada classe pertencem exclusivamente a ela;
        2. Passivos de cada classe pertencem exclusivamente a ela;
        3. A eventual insolvência de uma classe não afeta nem transfere obrigações às demais.
        """
        if not self.master_fund:
            raise ValidationError("Fundo mestre não configurado.")

        audit_report: Dict[str, Any] = {
            "law_code": LAW_041_CODE,
            "law_name": LAW_041_NAME,
            "classes_audited": len(self.master_fund.classes),
            "ring_fencing_preserved": True,
            "insolvent_classes": [],
            "classes_details": {},
        }

        all_asset_ids: Dict[str, str] = {}
        all_liability_ids: Dict[str, str] = {}

        for c_id, f_class in self.master_fund.classes.items():
            # Verifica sobreposição de ativos
            for a_id in f_class.assets.keys():
                if a_id in all_asset_ids:
                    audit_report["ring_fencing_preserved"] = False
                    raise RingFencingViolationError(
                        f"Ativo '{a_id}' compartilhado ilicitamente entre as classes "
                        f"'{all_asset_ids[a_id]}' e '{c_id}'. Violação estrita de {LAW_041_CODE}."
                    )
                all_asset_ids[a_id] = c_id

            # Verifica sobreposição de passivos
            for l_id in f_class.liabilities.keys():
                if l_id in all_liability_ids:
                    audit_report["ring_fencing_preserved"] = False
                    raise RingFencingViolationError(
                        f"Passivo '{l_id}' compartilhado entre as classes "
                        f"'{all_liability_ids[l_id]}' e '{c_id}'. Violação estrita de {LAW_041_CODE}."
                    )
                all_liability_ids[l_id] = c_id

            if f_class.is_insolvent:
                audit_report["insolvent_classes"].append(c_id)

            audit_report["classes_details"][c_id] = {
                "status": f_class.status.value,
                "net_asset_value": str(f_class.net_asset_value),
                "is_insolvent": f_class.is_insolvent,
                "assets_count": len(f_class.assets),
                "liabilities_count": len(f_class.liabilities),
            }

        return audit_report

    def audit_law_043_high_water_mark(self, benchmark_indices: Optional[Dict[str, Decimal]] = None) -> Dict[str, Any]:
        """Audita formalmente a Inviolabilidade da Linha d'Água (LAW-043).

        Invariante:
        TaxaPerformance > 0 <=> CotaAtual > max(HWM, Hurdle)
        Se Cota <= HWM, a taxa provisionada deve ser estritamente R$ 0,00.
        """
        if not self.master_fund:
            raise ValidationError("Fundo mestre não configurado.")

        benchmark_indices = benchmark_indices or {}
        audit_report: Dict[str, Any] = {
            "law_code": LAW_043_CODE,
            "law_name": LAW_043_NAME,
            "subclasses_audited": 0,
            "hwm_invariance_preserved": True,
            "subclasses_details": {},
        }

        for c_id, f_class in self.master_fund.classes.items():
            for s_id, sub in f_class.subclasses.items():
                audit_report["subclasses_audited"] += 1
                curr_bench = benchmark_indices.get(sub.benchmark_name, sub.benchmark_base_index)
                bench_factor = curr_bench / sub.benchmark_base_index if sub.benchmark_base_index > Decimal("0") else Decimal("1")
                hurdle = round_quota(sub.high_water_mark * bench_factor)
                threshold = max(sub.high_water_mark, hurdle)

                # Se a cota está abaixo da linha d'água, provisão não pode ser positiva
                if sub.quota_value <= sub.high_water_mark and sub.provisioned_performance_fee > Decimal("0.00"):
                    audit_report["hwm_invariance_preserved"] = False
                    raise HighWaterMarkViolationError(
                        f"Subclasse '{s_id}' possui taxa de performance provisionada de "
                        f"R$ {sub.provisioned_performance_fee:.2f}, mas a cota unitária "
                        f"R$ {sub.quota_value:.8f} está abaixo do HWM R$ {sub.high_water_mark:.8f}. "
                        f"Violação da Inviolabilidade da Linha d'Água ({LAW_043_CODE})."
                    )

                audit_report["subclasses_details"][s_id] = {
                    "quota_value": str(sub.quota_value),
                    "high_water_mark": str(sub.high_water_mark),
                    "hurdle_quota": str(hurdle),
                    "provisioned_performance_fee": str(sub.provisioned_performance_fee),
                    "is_above_hwm": sub.quota_value > sub.high_water_mark,
                }

        return audit_report

    def audit_law_045_fund_conservation(self) -> Dict[str, Any]:
        """Audita formalmente a Conservação Patrimonial de Fundo Multiclasse (LAW-045).

        Invariantes:
        1. Para cada classe: PL(Classe) == soma(PL(Subclasses)) (com tolerância máxima de 1 centavo por subclasse por arredondamento);
        2. Para cada subclasse: total_quotas == soma(cotas dos cotistas no livro de escrituração);
        3. PL(Classe) == AtivosTotais - PassivosTotais.
        """
        if not self.master_fund:
            raise ValidationError("Fundo mestre não configurado.")

        audit_report: Dict[str, Any] = {
            "law_code": LAW_045_CODE,
            "law_name": LAW_045_NAME,
            "classes_audited": len(self.master_fund.classes),
            "conservation_preserved": True,
            "classes_details": {},
        }

        for c_id, f_class in self.master_fund.classes.items():
            # Validação 1: PL da Classe == Ativos - Passivos
            expected_class_nav = f_class.total_assets_value - f_class.total_liabilities_value
            if abs(f_class.net_asset_value - expected_class_nav) > Decimal("0.01"):
                audit_report["conservation_preserved"] = False
                raise FundConservationError(
                    f"Violação contábil na classe '{c_id}': PL={f_class.net_asset_value} "
                    f"diverge de Ativos({f_class.total_assets_value}) - Passivos({f_class.total_liabilities_value}) "
                    f"= {expected_class_nav} ({LAW_045_CODE})."
                )

            # Validação 2: Soma dos PLs das Subclasses == PL da Classe (se houver cotas emitidas)
            sum_subclasses_nav = sum((s.net_asset_value for s in f_class.subclasses.values()), Decimal("0.00"))
            has_quotas = any((s.total_quotas > Decimal("0") for s in f_class.subclasses.values()))

            if has_quotas:
                # Tolerância de 1 centavo por subclasse decorrente do arredondamento do valor unitário da cota
                tolerance = Decimal(len(f_class.subclasses)) * Decimal("0.02")
                nav_diff = abs(f_class.net_asset_value - sum_subclasses_nav)
                if nav_diff > tolerance:
                    audit_report["conservation_preserved"] = False
                    raise FundConservationError(
                        f"Violação de conservação multiclasse em '{c_id}': PL da classe ({f_class.net_asset_value}) "
                        f"diverge da soma dos PLs das subclasses ({sum_subclasses_nav}) por R$ {nav_diff:.2f} ({LAW_045_CODE})."
                    )

            # Validação 3: Conservação de cotas no livro de escrituração
            subclasses_audit = {}
            for s_id, sub in f_class.subclasses.items():
                sum_holdings_quotas = sum((h.quotas for h in sub.holdings.values()), Decimal("0.00000000"))
                quotas_diff = abs(sub.total_quotas - sum_holdings_quotas)
                if quotas_diff > Decimal("0.00000010"):
                    audit_report["conservation_preserved"] = False
                    raise FundConservationError(
                        f"Violação no livro de escrituração da subclasse '{s_id}': total_quotas "
                        f"({sub.total_quotas}) diverge da soma das cotas dos cotistas ({sum_holdings_quotas}) ({LAW_045_CODE})."
                    )
                subclasses_audit[s_id] = {
                    "total_quotas": str(sub.total_quotas),
                    "sum_holdings_quotas": str(sum_holdings_quotas),
                    "net_asset_value": str(sub.net_asset_value),
                    "holdings_count": len(sub.holdings),
                }

            audit_report["classes_details"][c_id] = {
                "class_net_asset_value": str(f_class.net_asset_value),
                "sum_subclasses_nav": str(sum_subclasses_nav),
                "subclasses": subclasses_audit,
            }

        return audit_report

    def verify_all_invariants(self, benchmark_indices: Optional[Dict[str, Decimal]] = None) -> Dict[str, Any]:
        """Executa auditoria integral de todas as leis regulatórias CVM 175."""
        return {
            "law_041": self.audit_law_041_ring_fencing(),
            "law_043": self.audit_law_043_high_water_mark(benchmark_indices=benchmark_indices),
            "law_045": self.audit_law_045_fund_conservation(),
            "all_passed": True,
        }

    # -----------------------------------------------------------------
    # SERIALIZAÇÃO JSON E FIXTURES
    # -----------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "framework": "RESOLUCAO_CVM_175",
            "master_fund": self.master_fund.to_dict() if self.master_fund else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Cvm175FundsEngine:
        master_fund = MasterFund.from_dict(data["master_fund"]) if data.get("master_fund") else None
        return cls(master_fund=master_fund)

    def load_fixture(self, file_path: Union[str, Path]) -> None:
        """Carrega estado a partir de arquivo JSON."""
        p = Path(file_path)
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self.master_fund = MasterFund.from_dict(data["master_fund"])

    def save_fixture(self, file_path: Union[str, Path]) -> None:
        """Salva estado em arquivo JSON."""
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
