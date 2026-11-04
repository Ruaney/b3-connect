"""Módulo de Registro, Monitoramento e Liquidação de LIGs / Covered Bonds — B3 NoMe (FL-049).

Implementa os requisitos regulatórios, contábeis e de modelagem financeira para:
- Letras Imobiliárias Garantidas (LIG) conforme a Lei Federal nº 13.097/2015 e Marco Legal das Garantias (Lei 14.711/2023).
- Resolução CMN nº 4.598/2017 e regulamentação consolidada do Banco Central do Brasil (BACEN).
- Segregação de Patrimônio de Afetação (Cover Pool) averbado no Balcão NoMe B3 (Conta 3901-LIG-AFETACAO).
- Requisito de Sobrecolateralização Mínima (Overcollateralization - OC >= 5% / Cover Ratio >= 105%).
- Teste de Estresse de Liquidez de 180 Dias com choques de fluxo de caixa e inadimplência.
- Atribuições e relatórios de auditoria periódica do Agente Fiduciário de LIG.

Zero dependências externas (100% Python Standard Library: dataclasses, Decimal, datetime, enum, json).
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
# ENUMS REGULATÓRIOS E OPERACIONAIS (LEI 13.097/15 / RES. CMN 4.598/17)
# =====================================================================

class LigIndexer(str, Enum):
    """Indexadores financeiros admitidos na emissão de LIG no NoMe B3."""

    PREFIXADO = "PREFIXADO"
    IPCA = "IPCA"
    CDI = "CDI"
    IGPM = "IGPM"
    TAXA_CAMBIAL = "TAXA_CAMBIAL"


class CoverPoolAssetType(str, Enum):
    """Tipos de ativos elegíveis para a carteira de afetação (Cover Pool)."""

    REAL_ESTATE_CREDIT = "REAL_ESTATE_CREDIT"  # Crédito imobiliário (SFH / SFI)
    TREASURY_BOND = "TREASURY_BOND"            # Títulos Públicos Federais (TPF / Selic)
    HEDGE_DERIVATIVE = "HEDGE_DERIVATIVE"      # Derivativos financeiros de hedge (Swap / NDF)
    CASH_EQUIVALENT = "CASH_EQUIVALENT"        # Disponibilidades em moeda nacional (BACEN / B3)


class LigBondStatus(str, Enum):
    """Estado do ciclo de vida da LIG registrada no NoMe."""

    ACTIVE = "ACTIVE"
    MATURED = "MATURED"
    DEFAULTED = "DEFAULTED"
    CANCELLED = "CANCELLED"


class CoverPoolComplianceStatus(str, Enum):
    """Status de enquadramento regulatório da razão de cobertura (Cover Ratio)."""

    COMPLIANT = "COMPLIANT"                               # Cover Ratio >= 105%
    DEFICIENT_UNDER_CURE_PERIOD = "DEFICIENT_UNDER_CURE_PERIOD" # < 105%, prazo de cura de 15 dias úteis
    BREACH_UNRESOLVED = "BREACH_UNRESOLVED"               # Cura expirada sem aporte


# Constantes Regulatórias (Resolução CMN nº 4.598/2017)
REGULATORY_MINIMUM_COVER_RATIO = Decimal("1.0500")  # 105% (OC mínimo de 5%)
REGULATORY_MINIMUM_OC_PCT = Decimal("0.0500")       # 5.00%
REGULATORY_CURE_DEADLINE_DAYS = 15                  # 15 dias úteis para recomposição
MAX_RESIDENTIAL_LTV = Decimal("0.8000")             # Teto LTV 80% crédito habitacional
MAX_COMMERCIAL_LTV = Decimal("0.6000")              # Teto LTV 60% crédito comercial
MAX_DELINQUENCY_DAYS_ELIGIBLE = 60                  # Atraso máximo tolerado para inclusão no OC


# =====================================================================
# EXCEÇÕES DE DOMÍNIO
# =====================================================================

class LigError(Exception):
    """Exceção base para operações de LIG e Cover Pool."""
    pass


class LigValidationError(LigError):
    """Erro de validação cadastral ou inconsistência de dados de LIG."""
    pass


class LigComplianceError(LigError):
    """Violação de regras de sobrecolateralização ou solvência regulatória."""
    pass


# =====================================================================
# DATA CLASSES DE DOMÍNIO
# =====================================================================

@dataclass
class LigBond:
    """Representação de uma Letra Imobiliária Garantida (LIG) emitida."""

    bond_id: str
    isin: str
    nome_code: str
    indexer: LigIndexer
    annual_rate: Decimal
    spread_rate: Decimal
    issue_date: str
    maturity_date: str
    nominal_unit_value: Decimal
    quantity: Decimal
    outstanding_nominal_value: Decimal
    present_value: Decimal
    status: LigBondStatus = LigBondStatus.ACTIVE
    currency: str = "BRL"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LigBond:
        """Instancia a classe a partir de dicionário serializado."""
        indexer_val = data.get("indexer", LigIndexer.PREFIXADO)
        if isinstance(indexer_val, str):
            indexer_val = LigIndexer(indexer_val)

        status_val = data.get("status", LigBondStatus.ACTIVE)
        if isinstance(status_val, str):
            status_val = LigBondStatus(status_val)

        return cls(
            bond_id=str(data["bond_id"]),
            isin=str(data["isin"]),
            nome_code=str(data["nome_code"]),
            indexer=indexer_val,
            annual_rate=Decimal(str(data.get("annual_rate", "0.0000"))),
            spread_rate=Decimal(str(data.get("spread_rate", "0.0000"))),
            issue_date=str(data["issue_date"]),
            maturity_date=str(data["maturity_date"]),
            nominal_unit_value=Decimal(str(data.get("nominal_unit_value", "1000.00"))),
            quantity=Decimal(str(data.get("quantity", "1.0000"))),
            outstanding_nominal_value=Decimal(str(data["outstanding_nominal_value"])),
            present_value=Decimal(str(data["present_value"])),
            status=status_val,
            currency=str(data.get("currency", "BRL")),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a LIG para formato JSON-compatível."""
        res = asdict(self)
        res["indexer"] = self.indexer.value
        res["status"] = self.status.value
        for k in ["annual_rate", "spread_rate", "nominal_unit_value", "quantity",
                  "outstanding_nominal_value", "present_value"]:
            res[k] = str(res[k])
        return res


@dataclass
class CoverPoolAsset:
    """Ativo vinculado à carteira segregada de afetação da LIG."""

    asset_id: str
    asset_type: CoverPoolAssetType
    description: str
    contract_or_cetip_code: str
    outstanding_balance: Decimal
    present_value: Decimal
    property_valuation: Optional[Decimal] = None
    ltv_ratio: Optional[Decimal] = None
    max_eligible_ltv: Optional[Decimal] = None
    days_past_due: int = 0
    is_eligible: bool = True
    currency: str = "BRL"
    counterparty_or_borrower: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CoverPoolAsset:
        """Instancia a partir de dicionário serializado."""
        atype_val = data["asset_type"]
        if isinstance(atype_val, str):
            atype_val = CoverPoolAssetType(atype_val)

        prop_val = (
            Decimal(str(data["property_valuation"]))
            if data.get("property_valuation") is not None
            else None
        )
        ltv = (
            Decimal(str(data["ltv_ratio"]))
            if data.get("ltv_ratio") is not None
            else None
        )
        max_ltv = (
            Decimal(str(data["max_eligible_ltv"]))
            if data.get("max_eligible_ltv") is not None
            else None
        )

        return cls(
            asset_id=str(data["asset_id"]),
            asset_type=atype_val,
            description=str(data.get("description", "")),
            contract_or_cetip_code=str(data.get("contract_or_cetip_code", "")),
            outstanding_balance=Decimal(str(data["outstanding_balance"])),
            present_value=Decimal(str(data["present_value"])),
            property_valuation=prop_val,
            ltv_ratio=ltv,
            max_eligible_ltv=max_ltv,
            days_past_due=int(data.get("days_past_due", 0)),
            is_eligible=bool(data.get("is_eligible", True)),
            currency=str(data.get("currency", "BRL")),
            counterparty_or_borrower=str(data.get("counterparty_or_borrower", "")),
        )

    def compute_eligible_present_value(self) -> Decimal:
        """Calcula o Valor Presente elegível conforme regras da Resolução CMN 4.598/2017.

        Regras:
        1. Se marcado como inelegível: VP elegível = 0.
        2. Inadimplência > 60 dias: glosa total (VP elegível = 0).
        3. Atraso entre 31 e 60 dias: haircut de 20%.
        4. Crédito imobiliário com LTV excedente: parcela acima do teto regulamentar é expurgada.
        5. Derivativo com MtM negativo: é considerado passivo (valor negativo reduz o pool).
        """
        if not self.is_eligible:
            return Decimal("0.00")

        # 1. Glosa por inadimplência (> 60 dias)
        if self.days_past_due > MAX_DELINQUENCY_DAYS_ELIGIBLE:
            return Decimal("0.00")

        base_vp = self.present_value

        # 2. Haircut para atrasos de 31 a 60 dias
        haircut = Decimal("1.0000")
        if self.days_past_due > 30:
            haircut = Decimal("0.8000")  # 20% haircut preventivo

        # 3. Limitação por LTV para crédito imobiliário
        if self.asset_type == CoverPoolAssetType.REAL_ESTATE_CREDIT:
            if self.ltv_ratio is not None and self.max_eligible_ltv is not None:
                if self.ltv_ratio > self.max_eligible_ltv and self.ltv_ratio > Decimal("0"):
                    # O valor elegível fica limitado proporcionalmente ao teto de LTV
                    ltv_factor = (self.max_eligible_ltv / self.ltv_ratio).quantize(
                        Decimal("0.00000001"), rounding=ROUND_HALF_UP
                    )
                    base_vp = (base_vp * ltv_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        eligible = (base_vp * haircut).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return eligible

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o ativo para formato JSON."""
        res = asdict(self)
        res["asset_type"] = self.asset_type.value
        res["outstanding_balance"] = str(self.outstanding_balance)
        res["present_value"] = str(self.present_value)
        if self.property_valuation is not None:
            res["property_valuation"] = str(self.property_valuation)
        if self.ltv_ratio is not None:
            res["ltv_ratio"] = str(self.ltv_ratio)
        if self.max_eligible_ltv is not None:
            res["max_eligible_ltv"] = str(self.max_eligible_ltv)
        res["eligible_present_value"] = str(self.compute_eligible_present_value())
        return res


@dataclass
class LiquidityCashFlowPeriod:
    """Fluxos de caixa projetados para um período de projeção."""

    period_id: str
    start_day: int
    end_day: int
    real_estate_inflows: Decimal
    treasury_inflows: Decimal
    derivative_inflows: Decimal
    cash_inflows: Decimal
    lig_coupons_outflows: Decimal
    lig_principal_outflows: Decimal
    derivative_outflows: Decimal
    fiduciary_fee_outflows: Decimal

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LiquidityCashFlowPeriod:
        return cls(
            period_id=str(data["period_id"]),
            start_day=int(data["start_day"]),
            end_day=int(data["end_day"]),
            real_estate_inflows=Decimal(str(data.get("real_estate_inflows", "0.00"))),
            treasury_inflows=Decimal(str(data.get("treasury_inflows", "0.00"))),
            derivative_inflows=Decimal(str(data.get("derivative_inflows", "0.00"))),
            cash_inflows=Decimal(str(data.get("cash_inflows", "0.00"))),
            lig_coupons_outflows=Decimal(str(data.get("lig_coupons_outflows", "0.00"))),
            lig_principal_outflows=Decimal(str(data.get("lig_principal_outflows", "0.00"))),
            derivative_outflows=Decimal(str(data.get("derivative_outflows", "0.00"))),
            fiduciary_fee_outflows=Decimal(str(data.get("fiduciary_fee_outflows", "0.00"))),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "period_id": self.period_id,
            "start_day": self.start_day,
            "end_day": self.end_day,
            "real_estate_inflows": str(self.real_estate_inflows),
            "treasury_inflows": str(self.treasury_inflows),
            "derivative_inflows": str(self.derivative_inflows),
            "cash_inflows": str(self.cash_inflows),
            "lig_coupons_outflows": str(self.lig_coupons_outflows),
            "lig_principal_outflows": str(self.lig_principal_outflows),
            "derivative_outflows": str(self.derivative_outflows),
            "fiduciary_fee_outflows": str(self.fiduciary_fee_outflows),
        }


@dataclass
class PeriodStressProjection:
    """Projeção de estresse em um período do horizonte de 180 dias."""

    period_id: str
    start_day: int
    end_day: int
    base_inflows: Decimal
    stressed_inflows: Decimal
    projected_outflows: Decimal
    net_period_cashflow: Decimal
    cumulative_liquidity_balance: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "period_id": self.period_id,
            "start_day": self.start_day,
            "end_day": self.end_day,
            "base_inflows": str(self.base_inflows),
            "stressed_inflows": str(self.stressed_inflows),
            "projected_outflows": str(self.projected_outflows),
            "net_period_cashflow": str(self.net_period_cashflow),
            "cumulative_liquidity_balance": str(self.cumulative_liquidity_balance),
        }


@dataclass
class LiquidityStressTestResult:
    """Resultado formal da auditoria de teste de estresse de liquidez de 180 dias."""

    cover_pool_id: str
    test_date: str
    projection_days: int
    is_compliant: bool
    minimum_cumulative_liquidity: Decimal
    stressed_default_rate_applied: Decimal
    stress_scenario_name: str
    periods: List[PeriodStressProjection]
    shortfall_period_id: Optional[str] = None
    shortfall_amount: Decimal = Decimal("0.00")
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cover_pool_id": self.cover_pool_id,
            "test_date": self.test_date,
            "projection_days": self.projection_days,
            "is_compliant": self.is_compliant,
            "minimum_cumulative_liquidity": str(self.minimum_cumulative_liquidity),
            "stressed_default_rate_applied": str(self.stressed_default_rate_applied),
            "stress_scenario_name": self.stress_scenario_name,
            "periods": [p.to_dict() for p in self.periods],
            "shortfall_period_id": self.shortfall_period_id,
            "shortfall_amount": str(self.shortfall_amount),
            "notes": self.notes,
        }


@dataclass
class CoverPoolSummary:
    """Relatório sintético de solvência e sobrecolateralização (OC) do Cover Pool."""

    cover_pool_id: str
    reference_date: str
    issuer_name: str
    issuer_cnpj: str
    fiduciary_agent_name: str
    fiduciary_agent_cnpj: str
    nome_segregated_account: str
    total_lig_nominal: Decimal
    total_lig_present_value: Decimal
    total_cover_pool_nominal: Decimal
    total_cover_pool_present_value: Decimal
    total_cover_pool_eligible_vp: Decimal
    overcollateralization_pct: Decimal
    cover_ratio: Decimal
    regulatory_minimum_cover_ratio: Decimal
    regulatory_minimum_oc_pct: Decimal
    is_overcollateralization_compliant: bool
    compliance_status: CoverPoolComplianceStatus
    shortfall_amount: Decimal
    cure_deadline_days: int
    asset_breakdown_by_type: Dict[str, Decimal]
    ineligible_assets_count: int
    ineligible_present_value_glosa: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cover_pool_id": self.cover_pool_id,
            "reference_date": self.reference_date,
            "issuer_name": self.issuer_name,
            "issuer_cnpj": self.issuer_cnpj,
            "fiduciary_agent_name": self.fiduciary_agent_name,
            "fiduciary_agent_cnpj": self.fiduciary_agent_cnpj,
            "nome_segregated_account": self.nome_segregated_account,
            "total_lig_nominal": str(self.total_lig_nominal),
            "total_lig_present_value": str(self.total_lig_present_value),
            "total_cover_pool_nominal": str(self.total_cover_pool_nominal),
            "total_cover_pool_present_value": str(self.total_cover_pool_present_value),
            "total_cover_pool_eligible_vp": str(self.total_cover_pool_eligible_vp),
            "overcollateralization_pct": str(self.overcollateralization_pct),
            "cover_ratio": str(self.cover_ratio),
            "regulatory_minimum_cover_ratio": str(self.regulatory_minimum_cover_ratio),
            "regulatory_minimum_oc_pct": str(self.regulatory_minimum_oc_pct),
            "is_overcollateralization_compliant": self.is_overcollateralization_compliant,
            "compliance_status": self.compliance_status.value,
            "shortfall_amount": str(self.shortfall_amount),
            "cure_deadline_days": self.cure_deadline_days,
            "asset_breakdown_by_type": {k: str(v) for k, v in self.asset_breakdown_by_type.items()},
            "ineligible_assets_count": self.ineligible_assets_count,
            "ineligible_present_value_glosa": str(self.ineligible_present_value_glosa),
        }


# =====================================================================
# MOTOR DE GESTÃO E CONFORMIDADE DE LIG / COVER POOL
# =====================================================================

class LigCoveredBondsEngine:
    """Motor regulatório e matemático de gestão de LIGs e Cover Pool na B3."""

    def __init__(self) -> None:
        self._pools: Dict[str, Dict[str, Any]] = {}

    def register_cover_pool(
        self,
        cover_pool_id: str,
        issuer_name: str,
        issuer_cnpj: str,
        fiduciary_agent_name: str,
        fiduciary_agent_cnpj: str,
        nome_segregated_account: str = "3901-LIG-AFETACAO",
        issuer_participant_code: str = "00000",
        minimum_overcollateralization_pct: Decimal = REGULATORY_MINIMUM_OC_PCT,
        minimum_cover_ratio: Decimal = REGULATORY_MINIMUM_COVER_RATIO,
    ) -> None:
        """Registra uma nova carteira de afetação de LIG no motor."""
        if not cover_pool_id:
            raise LigValidationError("O identificador do Cover Pool não pode ser vazio.")

        self._pools[cover_pool_id] = {
            "cover_pool_id": cover_pool_id,
            "issuer_name": issuer_name,
            "issuer_cnpj": issuer_cnpj,
            "issuer_participant_code": issuer_participant_code,
            "fiduciary_agent_name": fiduciary_agent_name,
            "fiduciary_agent_cnpj": fiduciary_agent_cnpj,
            "nome_segregated_account": nome_segregated_account,
            "minimum_overcollateralization_pct": minimum_overcollateralization_pct,
            "minimum_cover_ratio": minimum_cover_ratio,
            "bonds": {},
            "assets": {},
            "cash_flows": [],
        }

    def add_lig_bond(self, cover_pool_id: str, bond: LigBond) -> None:
        """Vincula uma LIG emitida ao Cover Pool especificado."""
        if cover_pool_id not in self._pools:
            raise LigValidationError(f"Cover Pool '{cover_pool_id}' não encontrado.")
        if bond.bond_id in self._pools[cover_pool_id]["bonds"]:
            raise LigValidationError(f"LIG '{bond.bond_id}' já vinculada ao pool '{cover_pool_id}'.")
        self._pools[cover_pool_id]["bonds"][bond.bond_id] = bond

    def add_cover_pool_asset(self, cover_pool_id: str, asset: CoverPoolAsset) -> None:
        """Adiciona um ativo elegível ou derivativo ao Cover Pool."""
        if cover_pool_id not in self._pools:
            raise LigValidationError(f"Cover Pool '{cover_pool_id}' não encontrado.")
        if asset.asset_id in self._pools[cover_pool_id]["assets"]:
            raise LigValidationError(f"Ativo '{asset.asset_id}' já existe no pool '{cover_pool_id}'.")
        self._pools[cover_pool_id]["assets"][asset.asset_id] = asset

    def add_liquidity_cash_flow(self, cover_pool_id: str, cash_flow: LiquidityCashFlowPeriod) -> None:
        """Registra uma projeção de fluxo de caixa periódico para o teste de liquidez."""
        if cover_pool_id not in self._pools:
            raise LigValidationError(f"Cover Pool '{cover_pool_id}' não encontrado.")
        self._pools[cover_pool_id]["cash_flows"].append(cash_flow)

    def get_bonds(self, cover_pool_id: str) -> List[LigBond]:
        """Retorna lista de LIGs vinculadas ao pool."""
        if cover_pool_id not in self._pools:
            raise LigValidationError(f"Cover Pool '{cover_pool_id}' não encontrado.")
        return list(self._pools[cover_pool_id]["bonds"].values())

    def get_assets(self, cover_pool_id: str) -> List[CoverPoolAsset]:
        """Retorna lista de ativos vinculados ao pool."""
        if cover_pool_id not in self._pools:
            raise LigValidationError(f"Cover Pool '{cover_pool_id}' não encontrado.")
        return list(self._pools[cover_pool_id]["assets"].values())

    def calculate_cover_ratio(self, cover_pool_id: str) -> Decimal:
        """Calcula a razão de cobertura estrita (Cover Ratio = VP_Cover_Pool / VP_LIG)."""
        summary = self.evaluate_cover_pool(cover_pool_id)
        return summary.cover_ratio

    def evaluate_cover_pool(
        self,
        cover_pool_id: str,
        reference_date: Optional[str] = None,
    ) -> CoverPoolSummary:
        """Avalia de forma determinística a conformidade de sobrecolateralização (OC) da carteira."""
        if cover_pool_id not in self._pools:
            raise LigValidationError(f"Cover Pool '{cover_pool_id}' não encontrado.")

        pool_meta = self._pools[cover_pool_id]
        ref_date = reference_date or datetime.now().strftime("%Y-%m-%d")

        bonds: List[LigBond] = list(pool_meta["bonds"].values())
        assets: List[CoverPoolAsset] = list(pool_meta["assets"].values())

        # 1. Totalização do Passivo de LIGs (e derivativos passivos com MtM negativo)
        total_lig_nominal = Decimal("0.00")
        total_lig_present_value = Decimal("0.00")

        for b in bonds:
            if b.status == LigBondStatus.ACTIVE:
                total_lig_nominal += b.outstanding_nominal_value
                total_lig_present_value += b.present_value

        # 2. Totalização dos Ativos do Cover Pool
        total_pool_nominal = Decimal("0.00")
        total_pool_present_value = Decimal("0.00")
        total_eligible_vp = Decimal("0.00")
        ineligible_count = 0
        ineligible_glosa = Decimal("0.00")

        breakdown: Dict[str, Decimal] = {
            t.value: Decimal("0.00") for t in CoverPoolAssetType
        }

        for a in assets:
            total_pool_nominal += a.outstanding_balance
            total_pool_present_value += a.present_value

            eligible_vp = a.compute_eligible_present_value()
            total_eligible_vp += eligible_vp

            breakdown[a.asset_type.value] += eligible_vp

            if eligible_vp < a.present_value:
                ineligible_count += 1
                ineligible_glosa += (a.present_value - eligible_vp)

        # 3. Cálculo da Razão de Cobertura e Overcollateralization (OC)
        min_cover_ratio = pool_meta.get("minimum_cover_ratio", REGULATORY_MINIMUM_COVER_RATIO)
        min_oc_pct = pool_meta.get("minimum_overcollateralization_pct", REGULATORY_MINIMUM_OC_PCT)

        if total_lig_present_value <= Decimal("0.00"):
            # Sem obrigações ativas de LIG emitidas
            cover_ratio = Decimal("1.0000")
            oc_pct = Decimal("0.0000")
            is_compliant = True
            status = CoverPoolComplianceStatus.COMPLIANT
            shortfall = Decimal("0.00")
        else:
            cover_ratio = (total_eligible_vp / total_lig_present_value).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
            oc_pct = ((total_eligible_vp - total_lig_present_value) / total_lig_present_value).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )

            is_compliant = cover_ratio >= min_cover_ratio

            required_eligible_vp = (total_lig_present_value * min_cover_ratio).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if total_eligible_vp >= required_eligible_vp:
                shortfall = Decimal("0.00")
                status = CoverPoolComplianceStatus.COMPLIANT
            else:
                shortfall = (required_eligible_vp - total_eligible_vp).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                status = CoverPoolComplianceStatus.DEFICIENT_UNDER_CURE_PERIOD

        return CoverPoolSummary(
            cover_pool_id=cover_pool_id,
            reference_date=ref_date,
            issuer_name=pool_meta["issuer_name"],
            issuer_cnpj=pool_meta["issuer_cnpj"],
            fiduciary_agent_name=pool_meta["fiduciary_agent_name"],
            fiduciary_agent_cnpj=pool_meta["fiduciary_agent_cnpj"],
            nome_segregated_account=pool_meta["nome_segregated_account"],
            total_lig_nominal=total_lig_nominal.quantize(Decimal("0.01")),
            total_lig_present_value=total_lig_present_value.quantize(Decimal("0.01")),
            total_cover_pool_nominal=total_pool_nominal.quantize(Decimal("0.01")),
            total_cover_pool_present_value=total_pool_present_value.quantize(Decimal("0.01")),
            total_cover_pool_eligible_vp=total_eligible_vp.quantize(Decimal("0.01")),
            overcollateralization_pct=oc_pct,
            cover_ratio=cover_ratio,
            regulatory_minimum_cover_ratio=min_cover_ratio,
            regulatory_minimum_oc_pct=min_oc_pct,
            is_overcollateralization_compliant=is_compliant,
            compliance_status=status,
            shortfall_amount=shortfall,
            cure_deadline_days=REGULATORY_CURE_DEADLINE_DAYS,
            asset_breakdown_by_type=breakdown,
            ineligible_assets_count=ineligible_count,
            ineligible_present_value_glosa=ineligible_glosa.quantize(Decimal("0.01")),
        )

    def run_liquidity_stress_test(
        self,
        cover_pool_id: str,
        projection_days: int = 180,
        stress_default_pct: Decimal = Decimal("0.1500"),
        interest_rate_shock_bps: int = 0,
        scenario_name: str = "ESTRESSE_REGULATORIO_CMN_4598_180D",
    ) -> LiquidityStressTestResult:
        """Executa simulação do Teste de Estresse de Liquidez de 180 dias.

        Metodologia (Art. 11 Resolução CMN nº 4.598/2017):
        1. Entradas de crédito imobiliário sofrem corte de estresse (`stress_default_pct`).
        2. Títulos públicos federais e derivativos entram integralmente pelo fluxo contratado.
        3. Disponibilidade de caixa é adicionada no primeiro período.
        4. Todas as saídas de LIG (cupons + amortizações) e honorários fiduciários são computadas em 100%.
        5. O saldo de liquidez acumulado projetado não pode ser negativo em nenhum período.
        """
        if cover_pool_id not in self._pools:
            raise LigValidationError(f"Cover Pool '{cover_pool_id}' não encontrado.")

        pool_meta = self._pools[cover_pool_id]
        cash_flows: List[LiquidityCashFlowPeriod] = pool_meta.get("cash_flows", [])

        if not cash_flows:
            # Sem fluxos configurados no pool
            return LiquidityStressTestResult(
                cover_pool_id=cover_pool_id,
                test_date=datetime.now().strftime("%Y-%m-%d"),
                projection_days=projection_days,
                is_compliant=True,
                minimum_cumulative_liquidity=Decimal("0.00"),
                stressed_default_rate_applied=stress_default_pct,
                stress_scenario_name=scenario_name,
                periods=[],
                notes=["Nenhum fluxo de caixa projetado no período de 180 dias."],
            )

        running_cumulative = Decimal("0.00")
        min_cumulative = Decimal("Infinity")
        projections: List[PeriodStressProjection] = []
        shortfall_period: Optional[str] = None
        shortfall_amount = Decimal("0.00")
        notes: List[str] = []

        for p in cash_flows:
            # Entradas base
            base_inflow = (
                p.real_estate_inflows
                + p.treasury_inflows
                + p.derivative_inflows
                + p.cash_inflows
            )

            # Estresse sobre crédito imobiliário: (1 - default_pct)
            recovery_factor = Decimal("1.0000") - stress_default_pct
            stressed_re_inflow = (p.real_estate_inflows * recovery_factor).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            stressed_inflow = (
                stressed_re_inflow
                + p.treasury_inflows
                + p.derivative_inflows
                + p.cash_inflows
            )

            # Saídas integrais (100%)
            outflow = (
                p.lig_coupons_outflows
                + p.lig_principal_outflows
                + p.derivative_outflows
                + p.fiduciary_fee_outflows
            )

            net_cash_flow = stressed_inflow - outflow
            running_cumulative += net_cash_flow

            if running_cumulative < min_cumulative:
                min_cumulative = running_cumulative

            if running_cumulative < Decimal("0.00") and shortfall_period is None:
                shortfall_period = p.period_id
                shortfall_amount = (-running_cumulative).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

            projections.append(
                PeriodStressProjection(
                    period_id=p.period_id,
                    start_day=p.start_day,
                    end_day=p.end_day,
                    base_inflows=base_inflow.quantize(Decimal("0.01")),
                    stressed_inflows=stressed_inflow.quantize(Decimal("0.01")),
                    projected_outflows=outflow.quantize(Decimal("0.01")),
                    net_period_cashflow=net_cash_flow.quantize(Decimal("0.01")),
                    cumulative_liquidity_balance=running_cumulative.quantize(Decimal("0.01")),
                )
            )

        is_compliant = min_cumulative >= Decimal("0.00")

        if is_compliant:
            notes.append(
                f"Aprovado no Teste de Estresse de 180 dias com colchão mínimo positivo de R$ {min_cumulative:,.2f}."
            )
        else:
            notes.append(
                f"REPROVADO no Teste de Estresse de Liquidez. Quebra de liquidez em {shortfall_period} "
                f"com déficit de R$ {shortfall_amount:,.2f}. Exige recomposição emergencial em TPF/Caixa."
            )

        return LiquidityStressTestResult(
            cover_pool_id=cover_pool_id,
            test_date=datetime.now().strftime("%Y-%m-%d"),
            projection_days=projection_days,
            is_compliant=is_compliant,
            minimum_cumulative_liquidity=min_cumulative.quantize(Decimal("0.01")),
            stressed_default_rate_applied=stress_default_pct,
            stress_scenario_name=scenario_name,
            periods=projections,
            shortfall_period_id=shortfall_period,
            shortfall_amount=shortfall_amount,
            notes=notes,
        )

    def generate_fiduciary_agent_report(self, cover_pool_id: str) -> Dict[str, Any]:
        """Gera o parecer oficial mensal do Agente Fiduciário para entrega à B3 e ao BACEN."""
        summary = self.evaluate_cover_pool(cover_pool_id)
        stress_test = self.run_liquidity_stress_test(cover_pool_id)

        report = {
            "report_metadata": {
                "report_type": "PARECER_MENSAL_AGENTE_FIDUCIARIO_LIG",
                "regulatory_framework": "LEI_13097_2015_RES_CMN_4598_2017",
                "generation_timestamp": datetime.now().isoformat(),
                "fiduciary_agent": summary.fiduciary_agent_name,
                "fiduciary_agent_cnpj": summary.fiduciary_agent_cnpj,
            },
            "cover_pool_summary": summary.to_dict(),
            "liquidity_stress_test_180d": stress_test.to_dict(),
            "regulatory_conclusion": {
                "overcollateralization_compliant": summary.is_overcollateralization_compliant,
                "liquidity_compliant": stress_test.is_compliant,
                "overall_verdict": (
                    "APROVADO_CONFORME"
                    if summary.is_overcollateralization_compliant and stress_test.is_compliant
                    else "NOTIFICACAO_DE_DEFICIT_CURA_15_DIAS"
                ),
            },
        }
        return report

    def export_nome_registration_payload(self, cover_pool_id: str) -> Dict[str, Any]:
        """Exporta o pacote de dados para integração com o NoMe Balcão B3."""
        if cover_pool_id not in self._pools:
            raise LigValidationError(f"Cover Pool '{cover_pool_id}' não encontrado.")

        pool_meta = self._pools[cover_pool_id]
        summary = self.evaluate_cover_pool(cover_pool_id)

        bonds_payload = [b.to_dict() for b in pool_meta["bonds"].values()]
        assets_payload = [a.to_dict() for a in pool_meta["assets"].values()]

        return {
            "nome_header": {
                "system": "B3_BALCAO_NOME",
                "message_type": "LIG_COVERED_BONDS_PORTFOLIO_MAINTENANCE",
                "protocol_id": f"NOME-LIG-{cover_pool_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "participant_code": pool_meta["issuer_participant_code"],
            },
            "cover_pool_id": cover_pool_id,
            "issuer": {
                "name": pool_meta["issuer_name"],
                "cnpj": pool_meta["issuer_cnpj"],
            },
            "fiduciary_agent": {
                "name": pool_meta["fiduciary_agent_name"],
                "cnpj": pool_meta["fiduciary_agent_cnpj"],
            },
            "custody_account_segregated": pool_meta["nome_segregated_account"],
            "bonds": bonds_payload,
            "assets": assets_payload,
            "solvency_metrics": {
                "cover_ratio": str(summary.cover_ratio),
                "overcollateralization_pct": str(summary.overcollateralization_pct),
                "total_eligible_vp": str(summary.total_cover_pool_eligible_vp),
                "total_lig_vp": str(summary.total_lig_present_value),
                "is_compliant": summary.is_overcollateralization_compliant,
            },
        }

    def load_from_fixture(self, fixture_data: Dict[str, Any]) -> None:
        """Carrega instâncias completas a partir de uma fixture JSON."""
        pools_data = fixture_data.get("cover_pools", [])
        for pdata in pools_data:
            pool_id = str(pdata["cover_pool_id"])
            self.register_cover_pool(
                cover_pool_id=pool_id,
                issuer_name=str(pdata["issuer_name"]),
                issuer_cnpj=str(pdata["issuer_cnpj"]),
                fiduciary_agent_name=str(pdata["fiduciary_agent_name"]),
                fiduciary_agent_cnpj=str(pdata["fiduciary_agent_cnpj"]),
                nome_segregated_account=str(pdata.get("nome_segregated_account", "3901-LIG-AFETACAO")),
                issuer_participant_code=str(pdata.get("issuer_participant_code", "00000")),
                minimum_overcollateralization_pct=Decimal(
                    str(pdata.get("minimum_overcollateralization_pct", "0.0500"))
                ),
                minimum_cover_ratio=Decimal(
                    str(pdata.get("minimum_cover_ratio", "1.0500"))
                ),
            )

            # Carrega LIGs
            for bdata in pdata.get("bonds", []):
                self.add_lig_bond(pool_id, LigBond.from_dict(bdata))

            # Carrega Ativos
            for adata in pdata.get("assets", []):
                self.add_cover_pool_asset(pool_id, CoverPoolAsset.from_dict(adata))

            # Carrega Projeções de Liquidez
            for cdata in pdata.get("liquidity_cash_flows", []):
                self.add_liquidity_cash_flow(pool_id, LiquidityCashFlowPeriod.from_dict(cdata))

    def load_from_json_file(self, file_path: Union[str, Path]) -> None:
        """Lê e processa arquivo JSON contendo fixtures de LIG."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Arquivo de fixture '{path}' não encontrado.")
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self.load_from_fixture(data)
