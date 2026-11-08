#!/usr/bin/env python3
"""Lab 04: Metodologia CORE de Margem B3, Haircuts de TPF e Default Waterfall.

Demonstra na prática:
1. Cálculo de risco intradiário da Câmara B3 (VaR 99,5%, Expected Shortfall Cornish-Fisher,
   penalidade de iliquidez LAM e Haircuts de Títulos Públicos Federais `LAW-105A`).
2. Simulação da Cascata de Salvaguardas (`Default Waterfall` / `LAW-106B`) em cenário de
   inadimplência de membro de compensação.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core_margin_analytics import (
    AssetClass,
    CoreMarginAnalytics,
    CoreRiskAssessmentEngine,
    MaturityTenor,
    SafeguardWaterfallLayer,
    TPFBondType,
    TPFCollateral,
)


def run_lab() -> dict[str, object]:
    """Executa o Lab 04 (CORE Margin Analytics + Default Waterfall) e retorna telemetria."""
    engine = CoreRiskAssessmentEngine()

    es_equities = CoreMarginAnalytics.calculate_parametric_expected_shortfall(
        exposure_brl=Decimal("12000000.00"),
        daily_volatility=Decimal("0.024"),
        horizon_days=2,
        confidence_level=Decimal("0.995"),
        kurtosis=Decimal("4.80"),
        skewness=Decimal("-0.35"),
    )
    es_rates = CoreMarginAnalytics.calculate_parametric_expected_shortfall(
        exposure_brl=Decimal("8500000.00"),
        daily_volatility=Decimal("0.011"),
        horizon_days=2,
        confidence_level=Decimal("0.995"),
    )

    collaterals = [
        TPFCollateral(
            asset_id="SELIC-LFT-2028",
            bond_type=TPFBondType.LFT,
            tenor=MaturityTenor.FROM_2Y_TO_5Y,
            face_value_brl=Decimal("1500000.00"),
        ),
        TPFCollateral(
            asset_id="SELIC-NTNB-2032",
            bond_type=TPFBondType.NTN_B,
            tenor=MaturityTenor.FROM_5Y_TO_10Y,
            face_value_brl=Decimal("1000000.00"),
        ),
    ]

    assessment = engine.evaluate_participant_portfolio(
        participant_id="MC-0088",
        asset_margins={
            AssetClass.EQUITIES: es_equities.expected_shortfall_brl,
            AssetClass.INTEREST_RATES: es_rates.expected_shortfall_brl,
        },
        lam_margin_brl=Decimal("85000.00"),
        collateral_basket=collaterals,
    )

    # Simulação do Default Waterfall da Câmara B3 (LAW-106B)
    waterfall = CoreMarginAnalytics.execute_safeguard_waterfall(
        gross_default_loss_brl=Decimal("85000000.00"),
        defaulter_margin_brl=Decimal("50000000.00"),
        defaulter_fund_contrib_brl=Decimal("15000000.00"),
        b3_sitg_brl=Decimal("10000000.00"),
        mutualized_fund_brl=Decimal("120000000.00"),
        extraordinary_assessment_brl=Decimal("60000000.00"),
    )
    mutualized_layer = next(
        l for l in waterfall.layers if l.layer == SafeguardWaterfallLayer.LAYER_4_MUTUALIZED_DEFAULT_FUND
    )

    return {
        "lab": "LAB-04-CORE-MARGIN-AND-WATERFALL",
        "total_required_margin_brl": assessment.total_required_margin_brl,
        "collateral_post_haircut_brl": assessment.net_eligible_collateral_brl,
        "margin_balance_brl": assessment.margin_balance_brl,
        "law003_verified": "LAW-003" in assessment.invariants_verified,
        "law105a_verified": "LAW-105A" in assessment.invariants_verified,
        "waterfall_fully_absorbed": waterfall.is_fully_absorbed,
        "waterfall_mutualized_used": mutualized_layer.loss_absorbed_brl,
    }


if __name__ == "__main__":
    res = run_lab()
    print("=" * 72)
    print("🧪 LAB 04: METODOLOGIA CORE DE MARGEM B3 & DEFAULT WATERFALL CCP")
    print("=" * 72)
    print(f"• Margem Requerida CORE      : R$ {res['total_required_margin_brl']:,.2f} (LAW-003 OK={res['law003_verified']})")
    print(f"• Garantias TPF Pós-Haircut  : R$ {res['collateral_post_haircut_brl']:,.2f} (LAW-105A OK={res['law105a_verified']})")
    print(f"• Saldo Líquido de Margem    : R$ {res['margin_balance_brl']:,.2f}")
    print(f"• Default Waterfall Absorvido: {res['waterfall_fully_absorbed']} (Fundo Mutualizado usado: R$ {res['waterfall_mutualized_used']:,.2f})")
    print("=" * 72)
