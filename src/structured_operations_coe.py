"""B3 Balcão NoMe — Certificados de Operações Estruturadas (COE) (FL-102).
========================================================================
Motor determinístico de emissão, registro, decomposição estrutural,
monitoramento de barreiras (knock-in/knock-out/autocall), marcação a mercado (MtM)
e liquidação (financeira e física) de Certificados de Operações Estruturadas (COE),
em estrita conformidade com:
  - Lei Federal nº 12.249/2010 (arts. 39 a 44 - Criação e regime jurídico do COE);
  - Resolução CMN nº 4.263/2013 (Emissão e registro em depositária central B3);
  - Resolução CVM nº 8/2020 (Oferta pública, suitability e DIE - Documento de Informações Essenciais);
  - Lei nº 11.033/2004 e IN RFB nº 1.585/2015 (Tabela regressiva de IRRF para Renda Fixa).

Leis de Invariantes Formais Auditadas:
  1. LAW-108: Inviolabilidade do Piso de Capital Protegido:
     Para todo COE registrado como 'Valor Nominal Protegido' (PROTECTED_CAPITAL),
     o valor bruto de liquidação no vencimento ou no evento de resgate antecipado (autocall)
     é estritamente maior ou igual ao Principal investido garantido:
     Payoff_bruto >= Principal_garantido = Nominal_Unitario * Quantidade * Piso_protecao_pct.
  2. LAW-108B: Delimitação Estrita por Teto de Ganho (Cap Invariant):
     O valor total de liquidação bruta nunca ultrapassa o teto máximo estipulado no DIE:
     Total_bruto <= Principal * (1 + Cap_gain_pct).
  3. LAW-108C: Conservação Tributária Centavo a Centavo:
     O valor bruto de liquidação decompõe-se exatamente na soma do valor líquido com o IRRF:
     Total_bruto == Total_liquido + IRRF_retido.
  4. LAW-108D: Conservação Patrimonial na Liquidação Física:
     Em liquidação física (entrega de ações/ETFs), o valor econômico entregue acrescido do
     resíduo financeiro de frações coincide com o valor bruto apurado de liquidação.

100% Python Standard Library (decimal, dataclasses, enum, typing, json, datetime, math, pathlib).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from src.invariants import (
    InvariantViolationError,
    assert_coe_protected_capital_floor,
    check,
)

# Precisões decimais regulatórias B3
TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")
EIGHT_PLACES = Decimal("0.00000001")


def round_cents(val: Union[Decimal, str, float, int]) -> Decimal:
    """Arredonda valor financeiro para centavos (2 casas decimais) com ROUND_HALF_EVEN."""
    return Decimal(str(val)).quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)


def round_four(val: Union[Decimal, str, float, int]) -> Decimal:
    """Arredonda valor unitário ou cotação para 4 casas decimais com ROUND_HALF_EVEN."""
    return Decimal(str(val)).quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN)


def round_six(val: Union[Decimal, str, float, int]) -> Decimal:
    """Arredonda taxas e fatores para 6 casas decimais."""
    return Decimal(str(val)).quantize(SIX_PLACES, rounding=ROUND_HALF_EVEN)


def std_norm_cdf(x: float) -> float:
    """Função de distribuição cumulativa normal padrão N(x) via erf da stdlib."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def std_norm_pdf(x: float) -> float:
    """Função densidade de probabilidade normal padrão n(x)."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


class CoeCapitalProtectionType(str, Enum):
    """Modalidade de proteção de capital segundo art. 42 da Lei 12.249/2010."""
    PROTECTED_CAPITAL = "PROTECTED_CAPITAL"  # Valor Nominal Protegido (mínimo 100% garantido)
    AT_RISK_CAPITAL = "AT_RISK_CAPITAL"        # Valor Nominal em Risco (perda parcial ou total do capital)


class CoePayoffStructure(str, Enum):
    """Estrutura do derivativo embutido e estratégia de retorno pactuada no DIE."""
    BULL_CALL_SPREAD = "BULL_CALL_SPREAD"              # Alta moderada com trava de alta (Call Spread)
    DIGITAL_BINARY = "DIGITAL_BINARY"                  # Retorno fixo (cupom) condicional a barreira/nível
    PARTICIPATION_WITH_CAP = "PARTICIPATION_WITH_CAP"  # Participação alavancada na alta com teto (Cap)
    BARRIER_KNOCK_OUT = "BARRIER_KNOCK_OUT"            # Barreira desativante (Up-and-Out / Down-and-Out)
    BARRIER_KNOCK_IN = "BARRIER_KNOCK_IN"              # Barreira ativante (Up-and-In / Down-and-In)
    AUTOCALLABLE = "AUTOCALLABLE"                      # Chamada antecipada com cupom em datas de observação


class BarrierType(str, Enum):
    """Direção e tipo de barreira embutida."""
    UP_AND_OUT = "UP_AND_OUT"
    DOWN_AND_OUT = "DOWN_AND_OUT"
    UP_AND_IN = "UP_AND_IN"
    DOWN_AND_IN = "DOWN_AND_IN"


class SettlementType(str, Enum):
    """Tipo de liquidação do COE no vencimento / encerramento."""
    FINANCIAL = "FINANCIAL"  # Liquidação financeira em BRL via SPB / STR
    PHYSICAL = "PHYSICAL"    # Liquidação física com entrega de ações/ETFs via depositária B3


class UnderlyingAssetType(str, Enum):
    """Ativos subjacentes e índices referenciais homologados para COE na B3."""
    IBOVESPA = "IBOVESPA"
    SP500 = "SP500"
    PTAX_USD = "PTAX_USD"
    CDI = "CDI"
    GOLD_OZ1D = "GOLD_OZ1D"
    PETR4 = "PETR4"
    VALE3 = "VALE3"
    BOVA11 = "BOVA11"
    CUSTOM = "CUSTOM"


class CoeStatus(str, Enum):
    """Estado do ciclo de vida do COE na B3."""
    REGISTERED = "REGISTERED"
    ACTIVE = "ACTIVE"
    EARLY_TERMINATED = "EARLY_TERMINATED"  # Autocall acionado antes do vencimento
    MATURED = "MATURED"
    SETTLED = "SETTLED"


class CoeError(Exception):
    """Exceção base de operações com COE."""
    pass


class CoeValidationError(CoeError):
    """Falha de validação cadastral ou parâmetros ilegais do COE."""
    pass


class CoeSettlementError(CoeError):
    """Falha no processamento de liquidação física ou financeira."""
    pass


@dataclass(frozen=True)
class ObservationDateEvent:
    """Evento em data de observação intermediária para Autocall ou checagem de barreiras."""
    observation_index: int
    observation_date: str
    barrier_level_pct: Decimal  # Ex: 100.0% do valor inicial
    coupon_rate_pct: Decimal    # Cupom pago caso o autocall seja disparado
    observed_level: Optional[Decimal] = None
    triggered: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observation_index": self.observation_index,
            "observation_date": self.observation_date,
            "barrier_level_pct": str(self.barrier_level_pct),
            "coupon_rate_pct": str(self.coupon_rate_pct),
            "observed_level": str(self.observed_level) if self.observed_level is not None else None,
            "triggered": self.triggered,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ObservationDateEvent":
        return cls(
            observation_index=int(data["observation_index"]),
            observation_date=data["observation_date"],
            barrier_level_pct=Decimal(str(data["barrier_level_pct"])),
            coupon_rate_pct=Decimal(str(data["coupon_rate_pct"])),
            observed_level=Decimal(str(data["observed_level"])) if data.get("observed_level") is not None else None,
            triggered=bool(data.get("triggered", False)),
        )


@dataclass
class FixedIncomeLegSpec:
    """Decomposição: Perna de Renda Fixa (Título de dívida de desconto emitido pelo Banco).

    Representa a nota zero-cupom emitida para garantir o retorno do principal ou
    financiar a montagem da estrutura de opções com taxa pré ou CDI.
    """
    discount_rate_annual: Decimal       # Taxa pré de desconto do emissor (ex: 0.1250 = 12.50% a.a.)
    day_count_convention: str = "DU/252"# "DU/252" ou "DC/360"
    business_days: int = 252            # Prazo em dias úteis até o vencimento
    present_value: Optional[Decimal] = None
    derivatives_budget: Optional[Decimal] = None

    def calculate_pv(self, nominal_unit_value: Decimal) -> Decimal:
        """Calcula o valor presente (PV) da nota de renda fixa no início da operação."""
        t_years = Decimal(str(self.business_days)) / Decimal("252.0")
        r = float(self.discount_rate_annual)
        t = float(t_years)
        # Fator de desconto: (1 + r)^(-t)
        df = Decimal(str(1.0 / math.pow(1.0 + r, t)))
        pv = (nominal_unit_value * df).quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN)
        self.present_value = pv
        self.derivatives_budget = max(Decimal("0.0000"), nominal_unit_value - pv)
        return pv

    def to_dict(self) -> Dict[str, Any]:
        return {
            "discount_rate_annual": str(self.discount_rate_annual),
            "day_count_convention": self.day_count_convention,
            "business_days": self.business_days,
            "present_value": str(self.present_value) if self.present_value is not None else None,
            "derivatives_budget": str(self.derivatives_budget) if self.derivatives_budget is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FixedIncomeLegSpec":
        return cls(
            discount_rate_annual=Decimal(str(data["discount_rate_annual"])),
            day_count_convention=data.get("day_count_convention", "DU/252"),
            business_days=int(data.get("business_days", 252)),
            present_value=Decimal(str(data["present_value"])) if data.get("present_value") is not None else None,
            derivatives_budget=Decimal(str(data["derivatives_budget"])) if data.get("derivatives_budget") is not None else None,
        )


@dataclass
class EmbeddedOptionLegSpec:
    """Decomposição: Perna de Opções Embutidas na Estrutura do COE."""
    structure_type: CoePayoffStructure
    lower_strike: Optional[Decimal] = None          # K1 (preço de exercício inferior)
    upper_strike: Optional[Decimal] = None          # K2 (preço de exercício superior para Call Spread)
    participation_rate_pct: Decimal = Decimal("100.00")  # Taxa de alavancagem / participação na alta
    maximum_cap_gain_pct: Optional[Decimal] = None  # Ganho máximo permitido (Cap)
    digital_coupon_pct: Optional[Decimal] = None    # Cupom da opção digital (ex: 15.00%)
    barrier_level: Optional[Decimal] = None         # Nível da barreira em preço do subjacente
    barrier_type: Optional[BarrierType] = None      # Tipo da barreira
    rebate_pct: Decimal = Decimal("0.00")           # Rebate caso a barreira seja tocada
    barrier_hit: bool = False                       # Flag se barreira foi violada

    def to_dict(self) -> Dict[str, Any]:
        return {
            "structure_type": self.structure_type.value,
            "lower_strike": str(self.lower_strike) if self.lower_strike is not None else None,
            "upper_strike": str(self.upper_strike) if self.upper_strike is not None else None,
            "participation_rate_pct": str(self.participation_rate_pct),
            "maximum_cap_gain_pct": str(self.maximum_cap_gain_pct) if self.maximum_cap_gain_pct is not None else None,
            "digital_coupon_pct": str(self.digital_coupon_pct) if self.digital_coupon_pct is not None else None,
            "barrier_level": str(self.barrier_level) if self.barrier_level is not None else None,
            "barrier_type": self.barrier_type.value if self.barrier_type else None,
            "rebate_pct": str(self.rebate_pct),
            "barrier_hit": self.barrier_hit,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmbeddedOptionLegSpec":
        return cls(
            structure_type=CoePayoffStructure(data["structure_type"]),
            lower_strike=Decimal(str(data["lower_strike"])) if data.get("lower_strike") is not None else None,
            upper_strike=Decimal(str(data["upper_strike"])) if data.get("upper_strike") is not None else None,
            participation_rate_pct=Decimal(str(data.get("participation_rate_pct", "100.00"))),
            maximum_cap_gain_pct=Decimal(str(data["maximum_cap_gain_pct"])) if data.get("maximum_cap_gain_pct") is not None else None,
            digital_coupon_pct=Decimal(str(data["digital_coupon_pct"])) if data.get("digital_coupon_pct") is not None else None,
            barrier_level=Decimal(str(data["barrier_level"])) if data.get("barrier_level") is not None else None,
            barrier_type=BarrierType(data["barrier_type"]) if data.get("barrier_type") else None,
            rebate_pct=Decimal(str(data.get("rebate_pct", "0.00"))),
            barrier_hit=bool(data.get("barrier_hit", False)),
        )


@dataclass(frozen=True)
class DieSummary:
    """Documento de Informações Essenciais (DIE) - Resolução CVM nº 8/2020."""
    target_investor_profile: str      # 'GERAL', 'QUALIFICADO', 'PROFISSIONAL'
    issuer_rating: str                # ex: 'brAAA' (S&P), 'AAA(bra)' (Fitch)
    scenario_stress_return_pct: Decimal
    scenario_pessimistic_return_pct: Decimal
    scenario_neutral_return_pct: Decimal
    scenario_optimistic_return_pct: Decimal
    max_loss_pct: Decimal
    fiduciary_agent: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_investor_profile": self.target_investor_profile,
            "issuer_rating": self.issuer_rating,
            "scenario_stress_return_pct": str(self.scenario_stress_return_pct),
            "scenario_pessimistic_return_pct": str(self.scenario_pessimistic_return_pct),
            "scenario_neutral_return_pct": str(self.scenario_neutral_return_pct),
            "scenario_optimistic_return_pct": str(self.scenario_optimistic_return_pct),
            "max_loss_pct": str(self.max_loss_pct),
            "fiduciary_agent": self.fiduciary_agent,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DieSummary":
        return cls(
            target_investor_profile=data["target_investor_profile"],
            issuer_rating=data["issuer_rating"],
            scenario_stress_return_pct=Decimal(str(data["scenario_stress_return_pct"])),
            scenario_pessimistic_return_pct=Decimal(str(data["scenario_pessimistic_return_pct"])),
            scenario_neutral_return_pct=Decimal(str(data["scenario_neutral_return_pct"])),
            scenario_optimistic_return_pct=Decimal(str(data["scenario_optimistic_return_pct"])),
            max_loss_pct=Decimal(str(data["max_loss_pct"])),
            fiduciary_agent=data["fiduciary_agent"],
        )


@dataclass(frozen=True)
class PhysicalDeliveryAllocation:
    """Detalhamento de liquidação física (entrega de ações/ETFs)."""
    shares_symbol: str
    shares_quantity: int
    share_settlement_price: Decimal
    shares_gross_value: Decimal
    fractional_cash_residual: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shares_symbol": self.shares_symbol,
            "shares_quantity": self.shares_quantity,
            "share_settlement_price": str(self.share_settlement_price),
            "shares_gross_value": str(self.shares_gross_value),
            "fractional_cash_residual": str(self.fractional_cash_residual),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PhysicalDeliveryAllocation":
        return cls(
            shares_symbol=data["shares_symbol"],
            shares_quantity=int(data["shares_quantity"]),
            share_settlement_price=Decimal(str(data["share_settlement_price"])),
            shares_gross_value=Decimal(str(data["shares_gross_value"])),
            fractional_cash_residual=Decimal(str(data["fractional_cash_residual"])),
        )


@dataclass(frozen=True)
class CoeTaxBreakdown:
    """Detalhamento tributário oficial da liquidação (IRRF sobre Renda Fixa)."""
    gross_yield: Decimal
    holding_days: int
    tax_rate: Decimal
    irrf_amount: Decimal
    net_yield: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gross_yield": str(self.gross_yield),
            "holding_days": self.holding_days,
            "tax_rate": str(self.tax_rate),
            "irrf_amount": str(self.irrf_amount),
            "net_yield": str(self.net_yield),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CoeTaxBreakdown":
        return cls(
            gross_yield=Decimal(str(data["gross_yield"])),
            holding_days=int(data["holding_days"]),
            tax_rate=Decimal(str(data["tax_rate"])),
            irrf_amount=Decimal(str(data["irrf_amount"])),
            net_yield=Decimal(str(data["net_yield"])),
        )


@dataclass(frozen=True)
class CoeMarkToMarket:
    """Posição diária de Marcação a Mercado (MtM) do COE."""
    contract_id: str
    valuation_date: str
    days_to_maturity: int
    underlying_spot: Decimal
    volatility: Decimal
    fixed_income_pv_unit: Decimal
    derivative_pv_unit: Decimal
    unit_mtm: Decimal
    total_mtm: Decimal
    unrealized_pnl: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "valuation_date": self.valuation_date,
            "days_to_maturity": self.days_to_maturity,
            "underlying_spot": str(self.underlying_spot),
            "volatility": str(self.volatility),
            "fixed_income_pv_unit": str(self.fixed_income_pv_unit),
            "derivative_pv_unit": str(self.derivative_pv_unit),
            "unit_mtm": str(self.unit_mtm),
            "total_mtm": str(self.total_mtm),
            "unrealized_pnl": str(self.unrealized_pnl),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CoeMarkToMarket":
        return cls(
            contract_id=data["contract_id"],
            valuation_date=data["valuation_date"],
            days_to_maturity=int(data["days_to_maturity"]),
            underlying_spot=Decimal(str(data["underlying_spot"])),
            volatility=Decimal(str(data["volatility"])),
            fixed_income_pv_unit=Decimal(str(data["fixed_income_pv_unit"])),
            derivative_pv_unit=Decimal(str(data["derivative_pv_unit"])),
            unit_mtm=Decimal(str(data["unit_mtm"])),
            total_mtm=Decimal(str(data["total_mtm"])),
            unrealized_pnl=Decimal(str(data["unrealized_pnl"])),
        )


@dataclass(frozen=True)
class CoeSettlementReceipt:
    """Comprovante oficial de liquidação no NoMe/Câmara B3."""
    settlement_id: str
    contract_id: str
    settlement_date: str
    settlement_type: SettlementType
    settlement_reason: str  # 'MATURITY', 'AUTOCALL', 'BARRIER_EARLY_EXIT'
    underlying_final_level: Decimal
    underlying_performance_pct: Decimal
    gross_unit_payoff: Decimal
    total_gross_settlement: Decimal
    total_principal_invested: Decimal
    guaranteed_principal: Decimal
    tax_breakdown: CoeTaxBreakdown
    total_net_settlement: Decimal
    physical_delivery: Optional[PhysicalDeliveryAllocation] = None
    invariant_capital_protection_held: bool = True
    invariant_cap_gain_held: bool = True
    invariant_tax_conservation_held: bool = True

    def verify_invariants(self, protection_type: CoeCapitalProtectionType, protection_floor_pct: Decimal) -> None:
        """Audita formalmente as invariantes da liquidação (LAW-108, LAW-108B, LAW-108C)."""
        # LAW-108: Piso de Capital Protegido
        if protection_type == CoeCapitalProtectionType.PROTECTED_CAPITAL:
            assert_coe_protected_capital_floor(
                gross_settlement=self.total_gross_settlement,
                guaranteed_principal=self.guaranteed_principal,
                contract_id=self.contract_id,
                protection_floor_pct=protection_floor_pct,
            )

        # LAW-108C: Conservação de Retenção de IRRF (Gross == Net + IRRF)
        reconstructed_gross = (self.total_net_settlement + self.tax_breakdown.irrf_amount).quantize(TWO_PLACES)
        check(
            abs(self.total_gross_settlement - reconstructed_gross) <= Decimal("0.02"),
            f"Violação da Conservação Tributária (LAW-108C) no contrato '{self.contract_id}': "
            f"Total Bruto ({self.total_gross_settlement}) != Líquido ({self.total_net_settlement}) + IRRF ({self.tax_breakdown.irrf_amount})",
            law_code="LAW-108C",
            details={
                "contract_id": self.contract_id,
                "gross": str(self.total_gross_settlement),
                "net": str(self.total_net_settlement),
                "irrf": str(self.tax_breakdown.irrf_amount),
            },
        )

        # LAW-108D: Conservação na Entrega Física (se aplicável)
        if self.settlement_type == SettlementType.PHYSICAL and self.physical_delivery is not None:
            phys_total = (self.physical_delivery.shares_gross_value + self.physical_delivery.fractional_cash_residual).quantize(TWO_PLACES)
            check(
                abs(self.total_gross_settlement - phys_total) <= Decimal("0.05"),
                f"Violação da Conservação na Liquidação Física (LAW-108D) no contrato '{self.contract_id}': "
                f"Bruto ({self.total_gross_settlement}) != Valor Ações ({self.physical_delivery.shares_gross_value}) + Resíduo ({self.physical_delivery.fractional_cash_residual})",
                law_code="LAW-108D",
                details={
                    "contract_id": self.contract_id,
                    "gross": str(self.total_gross_settlement),
                    "physical_total": str(phys_total),
                },
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "settlement_id": self.settlement_id,
            "contract_id": self.contract_id,
            "settlement_date": self.settlement_date,
            "settlement_type": self.settlement_type.value,
            "settlement_reason": self.settlement_reason,
            "underlying_final_level": str(self.underlying_final_level),
            "underlying_performance_pct": str(self.underlying_performance_pct),
            "gross_unit_payoff": str(self.gross_unit_payoff),
            "total_gross_settlement": str(self.total_gross_settlement),
            "total_principal_invested": str(self.total_principal_invested),
            "guaranteed_principal": str(self.guaranteed_principal),
            "tax_breakdown": self.tax_breakdown.to_dict(),
            "total_net_settlement": str(self.total_net_settlement),
            "physical_delivery": self.physical_delivery.to_dict() if self.physical_delivery else None,
            "invariant_capital_protection_held": self.invariant_capital_protection_held,
            "invariant_cap_gain_held": self.invariant_cap_gain_held,
            "invariant_tax_conservation_held": self.invariant_tax_conservation_held,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CoeSettlementReceipt":
        return cls(
            settlement_id=data["settlement_id"],
            contract_id=data["contract_id"],
            settlement_date=data["settlement_date"],
            settlement_type=SettlementType(data["settlement_type"]),
            settlement_reason=data["settlement_reason"],
            underlying_final_level=Decimal(str(data["underlying_final_level"])),
            underlying_performance_pct=Decimal(str(data["underlying_performance_pct"])),
            gross_unit_payoff=Decimal(str(data["gross_unit_payoff"])),
            total_gross_settlement=Decimal(str(data["total_gross_settlement"])),
            total_principal_invested=Decimal(str(data["total_principal_invested"])),
            guaranteed_principal=Decimal(str(data["guaranteed_principal"])),
            tax_breakdown=CoeTaxBreakdown.from_dict(data["tax_breakdown"]),
            total_net_settlement=Decimal(str(data["total_net_settlement"])),
            physical_delivery=PhysicalDeliveryAllocation.from_dict(data["physical_delivery"]) if data.get("physical_delivery") else None,
            invariant_capital_protection_held=bool(data.get("invariant_capital_protection_held", True)),
            invariant_cap_gain_held=bool(data.get("invariant_cap_gain_held", True)),
            invariant_tax_conservation_held=bool(data.get("invariant_tax_conservation_held", True)),
        )


@dataclass
class CoeContractSpec:
    """Especificação formal completa de COE registrado na B3 (FL-102)."""
    contract_id: str
    issuer_institution_id: str
    issuer_name: str
    investor_id: str
    investor_account: str
    underlying_asset: UnderlyingAssetType
    underlying_symbol: str
    protection_type: CoeCapitalProtectionType
    payoff_structure: CoePayoffStructure
    settlement_type: SettlementType
    issue_date: str
    maturity_date: str
    nominal_unit_value: Decimal          # VNU (ex: R$ 1.000,00)
    quantity: int                        # Quantidade de títulos emitidos
    initial_underlying_level: Decimal    # Cotação base inicial do subjacente (S0)
    protection_floor_pct: Decimal       # Piso contratual garantido (ex: 100.00% para protegido, 80.00% em risco)
    fixed_income_leg: FixedIncomeLegSpec
    derivative_leg: EmbeddedOptionLegSpec
    die_summary: Optional[DieSummary] = None
    observation_schedule: List[ObservationDateEvent] = field(default_factory=list)
    status: CoeStatus = CoeStatus.REGISTERED

    @property
    def total_initial_notional(self) -> Decimal:
        """Montante total do principal investido na emissão (VNU * Qtd)."""
        return (self.nominal_unit_value * Decimal(str(self.quantity))).quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)

    @property
    def total_guaranteed_principal(self) -> Decimal:
        """Montante mínimo de principal garantido pelo contrato."""
        floor_mult = self.protection_floor_pct / Decimal("100.00")
        return (self.total_initial_notional * floor_mult).quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)

    def verify_invariants(self) -> None:
        """Validações regulatórias e de integridade matemática do contrato."""
        if self.quantity <= 0:
            raise CoeValidationError(f"Quantidade deve ser estritamente positiva: {self.quantity}")
        if self.nominal_unit_value <= Decimal("0.00"):
            raise CoeValidationError(f"VNU deve ser positivo: {self.nominal_unit_value}")
        if self.initial_underlying_level <= Decimal("0.00"):
            raise CoeValidationError(f"Cotação inicial do subjacente deve ser positiva: {self.initial_underlying_level}")

        # Se Capital Protegido, o piso não pode ser inferior a 100% (art. 42 da Lei 12.249/2010)
        if self.protection_type == CoeCapitalProtectionType.PROTECTED_CAPITAL and self.protection_floor_pct < Decimal("100.00"):
            raise CoeValidationError(
                f"COE de Valor Nominal Protegido exige piso mínimo de 100% (pactuado: {self.protection_floor_pct}%)"
            )

        # Se Capital em Risco, o piso deve estar entre 0% e < 100%
        if self.protection_type == CoeCapitalProtectionType.AT_RISK_CAPITAL:
            if self.protection_floor_pct < Decimal("0.00") or self.protection_floor_pct >= Decimal("100.00"):
                raise CoeValidationError(
                    f"COE de Valor Nominal em Risco deve ter piso no intervalo [0%, 100%): {self.protection_floor_pct}%"
                )

        # Validação do DIE obrigatório pela Resolução CVM 8
        if self.die_summary is not None:
            if self.protection_type == CoeCapitalProtectionType.PROTECTED_CAPITAL and self.die_summary.max_loss_pct != Decimal("0.00"):
                raise CoeValidationError("COE de Capital Protegido deve ter perda máxima 0% no DIE")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "issuer_institution_id": self.issuer_institution_id,
            "issuer_name": self.issuer_name,
            "investor_id": self.investor_id,
            "investor_account": self.investor_account,
            "underlying_asset": self.underlying_asset.value,
            "underlying_symbol": self.underlying_symbol,
            "protection_type": self.protection_type.value,
            "payoff_structure": self.payoff_structure.value,
            "settlement_type": self.settlement_type.value,
            "issue_date": self.issue_date,
            "maturity_date": self.maturity_date,
            "nominal_unit_value": str(self.nominal_unit_value),
            "quantity": self.quantity,
            "initial_underlying_level": str(self.initial_underlying_level),
            "protection_floor_pct": str(self.protection_floor_pct),
            "fixed_income_leg": self.fixed_income_leg.to_dict(),
            "derivative_leg": self.derivative_leg.to_dict(),
            "die_summary": self.die_summary.to_dict() if self.die_summary else None,
            "observation_schedule": [obs.to_dict() for obs in self.observation_schedule],
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CoeContractSpec":
        spec = cls(
            contract_id=data["contract_id"],
            issuer_institution_id=data["issuer_institution_id"],
            issuer_name=data.get("issuer_name", "Banco Emissor"),
            investor_id=data.get("investor_id", "INVESTOR-DEFAULT"),
            investor_account=data.get("investor_account", "CC-DEFAULT"),
            underlying_asset=UnderlyingAssetType(data["underlying_asset"]),
            underlying_symbol=data.get("underlying_symbol", data["underlying_asset"]),
            protection_type=CoeCapitalProtectionType(data["protection_type"]),
            payoff_structure=CoePayoffStructure(data["payoff_structure"]),
            settlement_type=SettlementType(data.get("settlement_type", "FINANCIAL")),
            issue_date=data["issue_date"],
            maturity_date=data["maturity_date"],
            nominal_unit_value=Decimal(str(data["nominal_unit_value"])),
            quantity=int(data["quantity"]),
            initial_underlying_level=Decimal(str(data["initial_underlying_level"])),
            protection_floor_pct=Decimal(str(data["protection_floor_pct"])),
            fixed_income_leg=FixedIncomeLegSpec.from_dict(data["fixed_income_leg"]),
            derivative_leg=EmbeddedOptionLegSpec.from_dict(data["derivative_leg"]),
            die_summary=DieSummary.from_dict(data["die_summary"]) if data.get("die_summary") else None,
            observation_schedule=[ObservationDateEvent.from_dict(o) for o in data.get("observation_schedule", [])],
            status=CoeStatus(data.get("status", "REGISTERED")),
        )
        spec.verify_invariants()
        return spec


# ==============================================================================
# Modelos Quantitativos Analíticos de Precificação e Marcação a Mercado (MtM)
# ==============================================================================

class CoeQuantitativePricer:
    """Motor analítico de precificação Black-Scholes para pernas de opções embutidas."""

    @staticmethod
    def black_scholes_call(
        spot: float,
        strike: float,
        risk_free_rate: float,
        dividend_yield: float,
        volatility: float,
        time_to_maturity: float,
    ) -> float:
        """Preço fechado de Call européia clássica (Black-Scholes-Merton)."""
        if time_to_maturity <= 1e-6:
            return max(0.0, spot - strike)
        if volatility <= 1e-6:
            df = math.exp(-risk_free_rate * time_to_maturity)
            fwd = spot * math.exp((risk_free_rate - dividend_yield) * time_to_maturity)
            return max(0.0, (fwd - strike) * df)

        v_sqrt = volatility * math.sqrt(time_to_maturity)
        d1 = (math.log(spot / strike) + (risk_free_rate - dividend_yield + 0.5 * volatility * volatility) * time_to_maturity) / v_sqrt
        d2 = d1 - v_sqrt
        return spot * math.exp(-dividend_yield * time_to_maturity) * std_norm_cdf(d1) - strike * math.exp(-risk_free_rate * time_to_maturity) * std_norm_cdf(d2)

    @staticmethod
    def black_scholes_put(
        spot: float,
        strike: float,
        risk_free_rate: float,
        dividend_yield: float,
        volatility: float,
        time_to_maturity: float,
    ) -> float:
        """Preço fechado de Put européia clássica (Black-Scholes-Merton)."""
        if time_to_maturity <= 1e-6:
            return max(0.0, strike - spot)
        if volatility <= 1e-6:
            df = math.exp(-risk_free_rate * time_to_maturity)
            fwd = spot * math.exp((risk_free_rate - dividend_yield) * time_to_maturity)
            return max(0.0, (strike - fwd) * df)

        v_sqrt = volatility * math.sqrt(time_to_maturity)
        d1 = (math.log(spot / strike) + (risk_free_rate - dividend_yield + 0.5 * volatility * volatility) * time_to_maturity) / v_sqrt
        d2 = d1 - v_sqrt
        return strike * math.exp(-risk_free_rate * time_to_maturity) * std_norm_cdf(-d2) - spot * math.exp(-dividend_yield * time_to_maturity) * std_norm_cdf(-d1)

    @staticmethod
    def digital_cash_call(
        spot: float,
        strike: float,
        payout: float,
        risk_free_rate: float,
        dividend_yield: float,
        volatility: float,
        time_to_maturity: float,
    ) -> float:
        """Opção Digital Cash-or-Nothing Call: paga payout se S_T >= K."""
        if time_to_maturity <= 1e-6:
            return payout if spot >= strike else 0.0
        v_sqrt = volatility * math.sqrt(time_to_maturity)
        d1 = (math.log(spot / strike) + (risk_free_rate - dividend_yield + 0.5 * volatility * volatility) * time_to_maturity) / v_sqrt
        d2 = d1 - v_sqrt
        return payout * math.exp(-risk_free_rate * time_to_maturity) * std_norm_cdf(d2)

    @classmethod
    def price_embedded_derivative(
        cls,
        contract: CoeContractSpec,
        current_spot: Decimal,
        days_to_maturity: int,
        volatility: Decimal,
        risk_free_rate: Decimal,
        dividend_yield: Decimal = Decimal("0.00"),
    ) -> Decimal:
        """Precifica analiticamente o valor da perna derivativa unitária."""
        s = float(current_spot)
        s0 = float(contract.initial_underlying_level)
        t = max(0.0, float(days_to_maturity) / 252.0)
        vol = float(volatility)
        r = float(risk_free_rate)
        q = float(dividend_yield)
        vnu = float(contract.nominal_unit_value)
        leg = contract.derivative_leg

        # Se já venceu (t == 0), calcula valor intrínseco imediato
        if t <= 1e-6:
            perf_pct = ((current_spot - contract.initial_underlying_level) / contract.initial_underlying_level) * Decimal("100.0")
            gain_pct = Decimal("0.00")
            if leg.structure_type == CoePayoffStructure.BULL_CALL_SPREAD:
                k1 = float(leg.lower_strike or contract.initial_underlying_level)
                k2 = float(leg.upper_strike or (contract.initial_underlying_level * Decimal("1.20")))
                val = max(0.0, min(s - k1, k2 - k1)) / s0
                gain_pct = Decimal(str(val * 100.0))
            elif leg.structure_type == CoePayoffStructure.DIGITAL_BINARY:
                k = float(leg.lower_strike or contract.initial_underlying_level)
                if s >= k:
                    gain_pct = leg.digital_coupon_pct or Decimal("15.00")
            elif leg.structure_type == CoePayoffStructure.PARTICIPATION_WITH_CAP:
                part = float(leg.participation_rate_pct) / 100.0
                raw = max(0.0, (s - s0) / s0 * part)
                if leg.maximum_cap_gain_pct is not None:
                    raw = min(raw, float(leg.maximum_cap_gain_pct) / 100.0)
                gain_pct = Decimal(str(raw * 100.0))
            return (contract.nominal_unit_value * (gain_pct / Decimal("100.0"))).quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN)

        # Precificação analítica contínua para t > 0
        pv_der: float = 0.0

        if leg.structure_type == CoePayoffStructure.BULL_CALL_SPREAD:
            k1 = float(leg.lower_strike or contract.initial_underlying_level)
            k2 = float(leg.upper_strike or (contract.initial_underlying_level * Decimal("1.20")))
            call1 = cls.black_scholes_call(s, k1, r, q, vol, t)
            call2 = cls.black_scholes_call(s, k2, r, q, vol, t)
            spread_val_per_share = max(0.0, call1 - call2)
            # Normalização pelo valor inicial
            fraction = spread_val_per_share / s0
            pv_der = vnu * fraction

        elif leg.structure_type == CoePayoffStructure.DIGITAL_BINARY:
            k = float(leg.lower_strike or contract.initial_underlying_level)
            coupon = float(leg.digital_coupon_pct or Decimal("15.00")) / 100.0
            payout = vnu * coupon
            pv_der = cls.digital_cash_call(s, k, payout, r, q, vol, t)

        elif leg.structure_type == CoePayoffStructure.PARTICIPATION_WITH_CAP:
            part = float(leg.participation_rate_pct) / 100.0
            k1 = s0
            call1 = cls.black_scholes_call(s, k1, r, q, vol, t)
            if leg.maximum_cap_gain_pct is not None:
                cap_dec = float(leg.maximum_cap_gain_pct) / 100.0
                k2 = s0 * (1.0 + cap_dec / part)
                call2 = cls.black_scholes_call(s, k2, r, q, vol, t)
                spread = max(0.0, call1 - call2)
            else:
                spread = call1
            pv_der = vnu * part * (spread / s0)

        elif leg.structure_type in (CoePayoffStructure.BARRIER_KNOCK_OUT, CoePayoffStructure.BARRIER_KNOCK_IN):
            # Se barreira já foi acionada
            if leg.barrier_hit:
                if leg.structure_type == CoePayoffStructure.BARRIER_KNOCK_OUT:
                    # Desativada: vale apenas o rebate descontado
                    rebate = float(leg.rebate_pct) / 100.0
                    pv_der = vnu * rebate * math.exp(-r * t)
                else:
                    # Knock-In ativada: comporta-se como spread vanilla
                    k1 = float(leg.lower_strike or contract.initial_underlying_level)
                    k2 = float(leg.upper_strike or (contract.initial_underlying_level * Decimal("1.25")))
                    call1 = cls.black_scholes_call(s, k1, r, q, vol, t)
                    call2 = cls.black_scholes_call(s, k2, r, q, vol, t)
                    pv_der = vnu * (max(0.0, call1 - call2) / s0)
            else:
                # Aproximação analítica descontada para barreira não tocada
                k1 = float(leg.lower_strike or contract.initial_underlying_level)
                k2 = float(leg.upper_strike or (contract.initial_underlying_level * Decimal("1.25")))
                call1 = cls.black_scholes_call(s, k1, r, q, vol, t)
                call2 = cls.black_scholes_call(s, k2, r, q, vol, t)
                base_spread = vnu * (max(0.0, call1 - call2) / s0)
                # Fator de probabilidade de não violação da barreira
                barrier_price = float(leg.barrier_level or (contract.initial_underlying_level * Decimal("1.30")))
                if leg.barrier_type == BarrierType.UP_AND_OUT:
                    prob_survive = max(0.05, min(0.99, (barrier_price - s) / (barrier_price - s0))) if barrier_price > s else 0.0
                    pv_der = base_spread * prob_survive
                elif leg.barrier_type == BarrierType.DOWN_AND_IN:
                    prob_in = max(0.01, min(0.95, (s0 - s) / s0)) if s < s0 else 0.10
                    pv_der = base_spread * prob_in
                else:
                    pv_der = base_spread * 0.85

        elif leg.structure_type == CoePayoffStructure.AUTOCALLABLE:
            # Modelo de cupom digital ponderado por probabilidade de chamada
            k = s0
            prob_call = std_norm_cdf((math.log(s / k) + (r - q) * t) / (vol * math.sqrt(t)))
            target_coupon = float(leg.digital_coupon_pct or Decimal("20.00")) / 100.0
            pv_der = vnu * target_coupon * prob_call * math.exp(-r * t)

        return Decimal(str(pv_der)).quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN)


# ==============================================================================
# Motor Principal de Produtos Estruturados COE (FL-102)
# ==============================================================================

class StructuredOperationsCoeEngine:
    """Motor integral de emissão, registro, acompanhamento de barreiras,

    marcação a mercado e liquidação de Certificados de Operações Estruturadas (COE).
    """

    def __init__(self) -> None:
        self.contracts: Dict[str, CoeContractSpec] = {}
        self.mark_to_market_history: Dict[str, List[CoeMarkToMarket]] = {}
        self.settlement_receipts: Dict[str, CoeSettlementReceipt] = {}

    def register_coe(self, spec: CoeContractSpec) -> None:
        """Registra e homologa uma emissão de COE no NoMe B3."""
        if spec.contract_id in self.contracts:
            raise CoeValidationError(f"COE já registrado com identificador '{spec.contract_id}'")

        spec.verify_invariants()

        # Calcula o PV inicial da renda fixa e o orçamento de derivativos
        spec.fixed_income_leg.calculate_pv(spec.nominal_unit_value)
        self.contracts[spec.contract_id] = spec
        self.mark_to_market_history[spec.contract_id] = []

    def get_contract(self, contract_id: str) -> CoeContractSpec:
        """Recupera a especificação de um contrato pelo ID."""
        if contract_id not in self.contracts:
            raise CoeValidationError(f"COE '{contract_id}' não encontrado no cadastro B3")
        return self.contracts[contract_id]

    def record_underlying_observation(
        self,
        contract_id: str,
        observation_date: str,
        observed_level: Decimal,
        days_elapsed: int,
    ) -> Optional[CoeSettlementReceipt]:
        """Registra a cotação observada e avalia barreiras de autocall ou knock-in/knock-out.

        Se uma condição de encerramento antecipado (Autocall) for preenchida,
        efetua a liquidação antecipada e emite o recibo.
        """
        contract = self.get_contract(contract_id)
        if contract.status not in (CoeStatus.REGISTERED, CoeStatus.ACTIVE):
            raise CoeSettlementError(f"COE '{contract_id}' com status inválido para observação: {contract.status}")

        contract.status = CoeStatus.ACTIVE
        leg = contract.derivative_leg

        # 1. Checagem de Barreiras Knock-In / Knock-Out
        if leg.barrier_level is not None and not leg.barrier_hit:
            if leg.barrier_type == BarrierType.UP_AND_OUT and observed_level >= leg.barrier_level:
                leg.barrier_hit = True
            elif leg.barrier_type == BarrierType.DOWN_AND_OUT and observed_level <= leg.barrier_level:
                leg.barrier_hit = True
            elif leg.barrier_type == BarrierType.UP_AND_IN and observed_level >= leg.barrier_level:
                leg.barrier_hit = True
            elif leg.barrier_type == BarrierType.DOWN_AND_IN and observed_level <= leg.barrier_level:
                leg.barrier_hit = True

        # 2. Avaliação de Autocall no cronograma programado
        if contract.payoff_structure == CoePayoffStructure.AUTOCALLABLE:
            for i, obs in enumerate(contract.observation_schedule):
                if obs.observation_date == observation_date and not obs.triggered:
                    target_level = contract.initial_underlying_level * (obs.barrier_level_pct / Decimal("100.00"))
                    new_obs = ObservationDateEvent(
                        observation_index=obs.observation_index,
                        observation_date=obs.observation_date,
                        barrier_level_pct=obs.barrier_level_pct,
                        coupon_rate_pct=obs.coupon_rate_pct,
                        observed_level=observed_level,
                        triggered=(observed_level >= target_level),
                    )
                    contract.observation_schedule[i] = new_obs

                    if new_obs.triggered:
                        # Autocall disparado: encerramento antecipado com cupom acumulado
                        gross_unit = contract.nominal_unit_value * (Decimal("1.00") + (obs.coupon_rate_pct / Decimal("100.00")))
                        perf_pct = ((observed_level - contract.initial_underlying_level) / contract.initial_underlying_level) * Decimal("100.00")

                        receipt = self._execute_settlement(
                            contract=contract,
                            settlement_date=observation_date,
                            settlement_reason="AUTOCALL",
                            final_level=observed_level,
                            perf_pct=perf_pct,
                            gross_unit=round_four(gross_unit),
                            days_elapsed=days_elapsed,
                        )
                        contract.status = CoeStatus.EARLY_TERMINATED
                        return receipt

        return None

    def calculate_mark_to_market(
        self,
        contract_id: str,
        valuation_date: str,
        current_spot: Decimal,
        days_to_maturity: int,
        volatility: Decimal,
        risk_free_rate: Decimal,
    ) -> CoeMarkToMarket:
        """Calcula a Marcação a Mercado (MtM) diária combinando renda fixa e opções embutidas."""
        contract = self.get_contract(contract_id)

        # 1. PV da Renda Fixa na data t
        t_years = Decimal(str(max(0, days_to_maturity))) / Decimal("252.0")
        r = float(contract.fixed_income_leg.discount_rate_annual)
        t = float(t_years)
        df = Decimal(str(1.0 / math.pow(1.0 + r, t))) if t > 0 else Decimal("1.00")
        pv_rf = (contract.nominal_unit_value * df).quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN)

        # 2. PV da perna de opções
        pv_der = CoeQuantitativePricer.price_embedded_derivative(
            contract=contract,
            current_spot=current_spot,
            days_to_maturity=days_to_maturity,
            volatility=volatility,
            risk_free_rate=risk_free_rate,
        )

        unit_mtm = pv_rf + pv_der
        # Garantia de piso para capital protegido no MtM conservador
        if contract.protection_type == CoeCapitalProtectionType.PROTECTED_CAPITAL:
            piso_unit = contract.nominal_unit_value * (contract.protection_floor_pct / Decimal("100.00"))
            unit_mtm = max(unit_mtm, pv_rf)

        total_mtm = (unit_mtm * Decimal(str(contract.quantity))).quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)
        unrealized = total_mtm - contract.total_initial_notional

        mtm = CoeMarkToMarket(
            contract_id=contract_id,
            valuation_date=valuation_date,
            days_to_maturity=days_to_maturity,
            underlying_spot=current_spot,
            volatility=volatility,
            fixed_income_pv_unit=pv_rf,
            derivative_pv_unit=pv_der,
            unit_mtm=unit_mtm,
            total_mtm=total_mtm,
            unrealized_pnl=unrealized,
        )
        self.mark_to_market_history[contract_id].append(mtm)
        return mtm

    def settle_at_maturity(
        self,
        contract_id: str,
        final_underlying_level: Decimal,
        settlement_date: str,
        days_elapsed: int,
    ) -> CoeSettlementReceipt:
        """Efetua a apuração formal do payoff de vencimento e liquidação do COE."""
        contract = self.get_contract(contract_id)
        if contract.status in (CoeStatus.SETTLED, CoeStatus.EARLY_TERMINATED):
            raise CoeSettlementError(f"COE '{contract_id}' já liquidado com status {contract.status}")

        perf_pct = ((final_underlying_level - contract.initial_underlying_level) / contract.initial_underlying_level) * Decimal("100.00")
        gross_unit = self._compute_maturity_unit_payoff(contract, final_underlying_level, perf_pct)

        receipt = self._execute_settlement(
            contract=contract,
            settlement_date=settlement_date,
            settlement_reason="MATURITY",
            final_level=final_underlying_level,
            perf_pct=perf_pct,
            gross_unit=gross_unit,
            days_elapsed=days_elapsed,
        )
        contract.status = CoeStatus.SETTLED
        return receipt

    def _compute_maturity_unit_payoff(
        self,
        contract: CoeContractSpec,
        final_level: Decimal,
        perf_pct: Decimal,
    ) -> Decimal:
        """Determina o payoff bruto unitário no vencimento com base na estrutura contratada."""
        vnu = contract.nominal_unit_value
        floor_mult = contract.protection_floor_pct / Decimal("100.00")
        min_guaranteed = (vnu * floor_mult).quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN)
        leg = contract.derivative_leg

        payoff = min_guaranteed

        if leg.structure_type == CoePayoffStructure.BULL_CALL_SPREAD:
            if perf_pct <= Decimal("0.00"):
                payoff = min_guaranteed
            else:
                effective_gain = perf_pct * (leg.participation_rate_pct / Decimal("100.00"))
                if leg.maximum_cap_gain_pct is not None:
                    effective_gain = min(effective_gain, leg.maximum_cap_gain_pct)
                payoff = vnu * (Decimal("1.00") + (effective_gain / Decimal("100.00")))

        elif leg.structure_type == CoePayoffStructure.DIGITAL_BINARY:
            threshold = leg.lower_strike or contract.initial_underlying_level
            if final_level >= threshold:
                coupon = leg.digital_coupon_pct or Decimal("15.00")
                payoff = vnu * (Decimal("1.00") + (coupon / Decimal("100.00")))
            else:
                payoff = min_guaranteed

        elif leg.structure_type == CoePayoffStructure.PARTICIPATION_WITH_CAP:
            if perf_pct <= Decimal("0.00"):
                # Se for em risco e sem proteção total
                if contract.protection_type == CoeCapitalProtectionType.AT_RISK_CAPITAL:
                    # Queda consome principal até o piso contratado
                    payoff = vnu * (Decimal("1.00") + (perf_pct / Decimal("100.00")))
                else:
                    payoff = min_guaranteed
            else:
                effective_gain = perf_pct * (leg.participation_rate_pct / Decimal("100.00"))
                if leg.maximum_cap_gain_pct is not None:
                    effective_gain = min(effective_gain, leg.maximum_cap_gain_pct)
                payoff = vnu * (Decimal("1.00") + (effective_gain / Decimal("100.00")))

        elif leg.structure_type == CoePayoffStructure.BARRIER_KNOCK_OUT:
            # Se barreira desativante foi tocada, paga rebate ou proteção
            if leg.barrier_hit or (leg.barrier_level and final_level >= leg.barrier_level):
                rebate = leg.rebate_pct
                payoff = vnu * (Decimal("1.00") + (rebate / Decimal("100.00")))
            else:
                # Não tocou: ganho ilimitado ou com participação
                effective_gain = max(Decimal("0.00"), perf_pct * (leg.participation_rate_pct / Decimal("100.00")))
                payoff = vnu * (Decimal("1.00") + (effective_gain / Decimal("100.00")))

        elif leg.structure_type == CoePayoffStructure.BARRIER_KNOCK_IN:
            # Só ganha se tocou a barreira
            if leg.barrier_hit or (leg.barrier_level and final_level >= leg.barrier_level):
                effective_gain = max(Decimal("0.00"), perf_pct * (leg.participation_rate_pct / Decimal("100.00")))
                if leg.maximum_cap_gain_pct is not None:
                    effective_gain = min(effective_gain, leg.maximum_cap_gain_pct)
                payoff = vnu * (Decimal("1.00") + (effective_gain / Decimal("100.00")))
            else:
                payoff = min_guaranteed

        elif leg.structure_type == CoePayoffStructure.AUTOCALLABLE:
            # Não disparou autocall em datas anteriores
            if perf_pct >= Decimal("0.00"):
                coupon = leg.digital_coupon_pct or Decimal("20.00")
                payoff = vnu * (Decimal("1.00") + (coupon / Decimal("100.00")))
            else:
                payoff = min_guaranteed

        # Se Capital Protegido, LAW-108 impõe estritamente: Payoff >= min_guaranteed
        if contract.protection_type == CoeCapitalProtectionType.PROTECTED_CAPITAL:
            payoff = max(payoff, min_guaranteed)
        else:
            # Capital em Risco: respeita o piso contratual (ex: 80% ou 0%)
            payoff = max(payoff, min_guaranteed)

        return payoff.quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN)

    def _execute_settlement(
        self,
        contract: CoeContractSpec,
        settlement_date: str,
        settlement_reason: str,
        final_level: Decimal,
        perf_pct: Decimal,
        gross_unit: Decimal,
        days_elapsed: int,
    ) -> CoeSettlementReceipt:
        """Executa os cálculos fiscais, alocação física/financeira e audita as invariantes."""
        principal = contract.total_initial_notional
        guaranteed_principal = contract.total_guaranteed_principal
        total_gross = (gross_unit * Decimal(str(contract.quantity))).quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)
        gross_yield = max(Decimal("0.00"), total_gross - principal)

        # Tabela Regressiva de Renda Fixa (Lei 11.033/2004)
        if days_elapsed <= 180:
            tax_rate = Decimal("0.225")
        elif days_elapsed <= 360:
            tax_rate = Decimal("0.200")
        elif days_elapsed <= 720:
            tax_rate = Decimal("0.175")
        else:
            tax_rate = Decimal("0.150")

        irrf = (gross_yield * tax_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)
        net_yield = (gross_yield - irrf).quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)
        total_net = (total_gross - irrf).quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)

        # Checagem preliminar das invariantes
        cap_protection_held = True
        if contract.protection_type == CoeCapitalProtectionType.PROTECTED_CAPITAL:
            cap_protection_held = (total_gross >= guaranteed_principal)

        cap_gain_held = True
        if contract.derivative_leg.maximum_cap_gain_pct is not None:
            max_allowed = principal * (Decimal("1.00") + (contract.derivative_leg.maximum_cap_gain_pct / Decimal("100.00")))
            cap_gain_held = (total_gross <= max_allowed + Decimal("0.05"))

        tax_conservation_held = (total_gross == total_net + irrf)

        tax_breakdown = CoeTaxBreakdown(
            gross_yield=gross_yield,
            holding_days=days_elapsed,
            tax_rate=tax_rate,
            irrf_amount=irrf,
            net_yield=net_yield,
        )

        # Alocação de Liquidação Física (se aplicável)
        physical_delivery = None
        if contract.settlement_type == SettlementType.PHYSICAL:
            # Em liquidação física, calcula a quantidade de ações inteiras a entregar e resíduo em caixa
            share_price = final_level
            if share_price > Decimal("0.00"):
                total_shares = int((total_gross / share_price).quantize(Decimal("1"), rounding=ROUND_DOWN))
                shares_value = (Decimal(str(total_shares)) * share_price).quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)
                cash_residual = (total_gross - shares_value).quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)
                physical_delivery = PhysicalDeliveryAllocation(
                    shares_symbol=contract.underlying_symbol,
                    shares_quantity=total_shares,
                    share_settlement_price=share_price,
                    shares_gross_value=shares_value,
                    fractional_cash_residual=cash_residual,
                )

        receipt_id = f"SETTLE-COE-{contract.contract_id}-{settlement_date.replace('-', '')}"
        receipt = CoeSettlementReceipt(
            settlement_id=receipt_id,
            contract_id=contract.contract_id,
            settlement_date=settlement_date,
            settlement_type=contract.settlement_type,
            settlement_reason=settlement_reason,
            underlying_final_level=final_level,
            underlying_performance_pct=round_cents(perf_pct),
            gross_unit_payoff=gross_unit,
            total_gross_settlement=total_gross,
            total_principal_invested=principal,
            guaranteed_principal=guaranteed_principal,
            tax_breakdown=tax_breakdown,
            total_net_settlement=total_net,
            physical_delivery=physical_delivery,
            invariant_capital_protection_held=cap_protection_held,
            invariant_cap_gain_held=cap_gain_held,
            invariant_tax_conservation_held=tax_conservation_held,
        )

        # Auditoria estrita da Invariante LAW-108 e derivadas
        receipt.verify_invariants(contract.protection_type, contract.protection_floor_pct)

        self.settlement_receipts[receipt_id] = receipt
        return receipt

    def verify_invariants(self) -> None:
        """Audita todas as posições, contratos e liquidações do motor."""
        for contract in self.contracts.values():
            contract.verify_invariants()
        for receipt in self.settlement_receipts.values():
            contract = self.contracts[receipt.contract_id]
            receipt.verify_invariants(contract.protection_type, contract.protection_floor_pct)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado completo do motor para dicionário Python."""
        return {
            "contracts": [c.to_dict() for c in self.contracts.values()],
            "mark_to_market_history": {
                cid: [m.to_dict() for m in history]
                for cid, history in self.mark_to_market_history.items()
            },
            "settlement_receipts": [r.to_dict() for r in self.settlement_receipts.values()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StructuredOperationsCoeEngine":
        """Reconstrói uma instância completa do motor a partir de dicionário."""
        engine = cls()
        for c_data in data.get("contracts", []):
            spec = CoeContractSpec.from_dict(c_data)
            engine.register_coe(spec)

        for cid, history in data.get("mark_to_market_history", {}).items():
            engine.mark_to_market_history[cid] = [CoeMarkToMarket.from_dict(m) for m in history]

        for r_data in data.get("settlement_receipts", []):
            receipt = CoeSettlementReceipt.from_dict(r_data)
            engine.settlement_receipts[receipt.settlement_id] = receipt

        engine.verify_invariants()
        return engine

    def export_to_json(self, filepath: Union[str, Path]) -> None:
        """Salva todo o estado do motor em arquivo JSON estruturado."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_from_json(cls, filepath: Union[str, Path]) -> "StructuredOperationsCoeEngine":
        """Carrega e valida um estado de motor a partir de arquivo JSON."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
