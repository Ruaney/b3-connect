"""Bateria de Testes Unitários e Validação Formal do Motor CORE Margin Analytics (FL-050).

Cobre:
1. Funções matemáticas analíticas (Acklam PPF, PDF, CDF, Cornish-Fisher).
2. Value-at-Risk (VaR) Paramétrico e Histórico com 99,5% de confiança (CPMI-IOSCO PFMI 5).
3. Expected Shortfall (CVaR) com cauda gorda, curtose de estresse e multiplicador sistêmico.
4. Liquidity-Adjusted Margins (LAM) com Closeout Horizon dinâmico de 2 a 5 dias.
5. Matriz de Haircuts Conservadores para TPFs (LFT, LTN, NTN-B, NTN-F) e Buffer Transfronteiriço (LAW-105A).
6. Fator de Diversificação Intercommodities e teto regulatório de 30% de benefício.
7. Cascata de Salvaguardas da Câmara B3 em 5 camadas sequenciais (LAW-006).
8. Preservação formal estrita das Leis Invariantes: LAW-003, LAW-006 e LAW-105A.
9. Motor integrado de avaliação de risco do participante (CoreRiskAssessmentEngine).
"""

from decimal import Decimal
import math
from pathlib import Path
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.core_margin_analytics import (
    AssetClass,
    CoreMarginAnalytics,
    CoreRiskAssessmentEngine,
    DefaultWaterfallResult,
    DiversificationResult,
    ExpectedShortfallResult,
    HistoricalVaRResult,
    LiquidityAdjustedMarginResult,
    LiquidityParameters,
    MarginCallStatus,
    MaturityTenor,
    ParametricVaRResult,
    ParticipantRiskAssessment,
    SafeguardWaterfallLayer,
    TENOR_ORDER,
    TPFBondType,
    TPFCollateral,
    TPF_HAIRCUT_SCHEDULE,
    WaterfallLayerAbsorption,
    assert_law_003_margin_covers_potential_risk,
    assert_law_006_collateral_safeguards_coverage,
    assert_law_105a_tpf_collateral_haircuts,
    cornish_fisher_quantile,
    std_norm_cdf,
    std_norm_pdf,
    std_norm_ppf,
)
from src.invariants import InvariantViolationError


class TestCoreMarginAnalytics(unittest.TestCase):
    """Suíte de testes de precisão quantitativa e invariantes formais do modelo CORE."""

    def setUp(self) -> None:
        self.engine = CoreMarginAnalytics()
        self.assessment_engine = CoreRiskAssessmentEngine()

    # ==========================================================================
    # 1. Testes de Funções Matemáticas Analíticas
    # ==========================================================================

    def test_std_norm_ppf_accuracy_at_regulatory_quantiles(self) -> None:
        """Verifica a precisão de Acklam para o quantil regulatório de 99,5% (PFMI 5)."""
        z_995 = std_norm_ppf(0.995)
        # O valor canônico de Z a 99.5% é ~ 2.5758293035...
        self.assertAlmostEqual(z_995, 2.5758293, places=6)

        # Teste de simetria e extremos
        z_005 = std_norm_ppf(0.005)
        self.assertAlmostEqual(z_995, -z_005, places=6)

        z_50 = std_norm_ppf(0.50)
        self.assertAlmostEqual(z_50, 0.0, places=6)

        with self.assertRaises(ValueError):
            std_norm_ppf(0.0)
        with self.assertRaises(ValueError):
            std_norm_ppf(1.0)

    def test_std_norm_pdf_and_cdf(self) -> None:
        """Verifica as propriedades básicas da Normal padrão."""
        self.assertAlmostEqual(std_norm_pdf(0.0), 0.39894228, places=6)
        self.assertAlmostEqual(std_norm_cdf(0.0), 0.50, places=6)
        self.assertAlmostEqual(std_norm_cdf(2.5758293), 0.995, places=3)

    def test_cornish_fisher_expansion_fat_tails(self) -> None:
        """Verifica a expansão de Cornish-Fisher sob caudas pesadas e assimetria."""
        alpha = 0.995
        z_normal = std_norm_ppf(alpha)

        # Sem excesso de curtose ou assimetria, quantil coincide com a Normal
        z_neutral = cornish_fisher_quantile(alpha, skewness=0.0, kurtosis=3.0)
        self.assertAlmostEqual(z_neutral, z_normal, places=6)

        # Com excesso de curtose (kurtosis = 6.0), quantil se expande significativamente
        z_fat = cornish_fisher_quantile(alpha, skewness=0.0, kurtosis=6.0)
        self.assertGreater(z_fat, z_normal)
        self.assertAlmostEqual(z_fat, 3.7462, places=3)

        # Com curtose extrema e assimetria positiva
        z_extreme = cornish_fisher_quantile(alpha, skewness=0.8, kurtosis=8.0)
        self.assertGreater(z_extreme, z_fat)

    # ==========================================================================
    # 2. Value-at-Risk (Paramétrico e Histórico) a 99,5%
    # ==========================================================================

    def test_parametric_var_calculation(self) -> None:
        """Testa o cálculo do VaR paramétrico com horizonte de 2 dias e 99,5% de confiança."""
        exposure = Decimal("10000000.00")  # R$ 10 milhões
        daily_vol = Decimal("0.02")        # 2% a.d.
        horizon = 2

        var_res = self.engine.calculate_parametric_var(
            exposure_brl=exposure,
            daily_volatility=daily_vol,
            horizon_days=horizon,
            confidence_level=Decimal("0.995"),
        )

        # VaR = 10.000.000 * 2.57582931 * 0.02 * sqrt(2) ~ 728.554,49
        expected_approx = float(exposure) * float(var_res.z_score) * float(daily_vol) * math.sqrt(horizon)
        self.assertAlmostEqual(float(var_res.var_brl), expected_approx, delta=5.00)
        self.assertEqual(var_res.confidence_level, Decimal("0.995"))
        self.assertEqual(var_res.horizon_days, 2)
        self.assertGreater(var_res.var_brl, Decimal("0.00"))

    def test_parametric_var_monotonicity_and_horizon_scaling(self) -> None:
        """Verifica a monotonicidade com volatilidade e a regra da raiz do tempo sqrt(h)."""
        exposure = Decimal("5000000.00")
        vol1 = Decimal("0.015")
        vol2 = Decimal("0.030")

        var1 = self.engine.calculate_parametric_var(exposure, vol1, horizon_days=2).var_brl
        var2 = self.engine.calculate_parametric_var(exposure, vol2, horizon_days=2).var_brl
        self.assertGreater(var2, var1)

        # Escalonamento por raiz do tempo: h=2 vs h=5
        var_h2 = self.engine.calculate_parametric_var(exposure, vol1, horizon_days=2).var_brl
        var_h5 = self.engine.calculate_parametric_var(exposure, vol1, horizon_days=5).var_brl
        ratio = float(var_h5 / var_h2)
        expected_ratio = math.sqrt(5.0 / 2.0)
        self.assertAlmostEqual(ratio, expected_ratio, places=2)

    def test_historical_var_calculation(self) -> None:
        """Testa o VaR histórico com série de 1000 perdas diárias sintéticas."""
        # Cria uma série ordenada de 1000 perdas de 1.000 a 1.000.000
        losses = [Decimal(str(i * 1000)) for i in range(1, 1001)]

        var_hist = self.engine.calculate_historical_var(
            historical_losses_brl=losses,
            horizon_days=2,
            confidence_level=Decimal("0.995"),
        )

        self.assertEqual(var_hist.sample_size, 1000)
        # No percentil 99.5% de 1000 observações, o índice é 999 * 0.995 = 994.005
        # Corresponde a perdas em torno de R$ 995.000 * sqrt(2)
        self.assertGreater(var_hist.var_brl, Decimal("1000000.00"))
        self.assertLessEqual(var_hist.var_brl, Decimal("1000000.00") * Decimal(str(math.sqrt(2))))
        self.assertEqual(var_hist.worst_historical_loss_brl, Decimal("1000000.00"))

    # ==========================================================================
    # 3. Expected Shortfall (CVaR) com Cauda Gorda para Estresse
    # ==========================================================================

    def test_expected_shortfall_greater_than_var(self) -> None:
        """Invariante Coerente de Medida de Risco: ES >= VaR sempre."""
        exposure = Decimal("8000000.00")
        vol = Decimal("0.025")

        es_res = self.engine.calculate_parametric_expected_shortfall(
            exposure_brl=exposure,
            daily_volatility=vol,
            horizon_days=2,
            confidence_level=Decimal("0.995"),
            kurtosis=Decimal("3.00"),
            skewness=Decimal("0.00"),
        )

        self.assertGreaterEqual(es_res.expected_shortfall_brl, es_res.var_threshold_brl)
        self.assertFalse(es_res.is_fat_tail_adjusted)

    def test_expected_shortfall_fat_tail_stress_amplification(self) -> None:
        """Verifica a amplificação de cauda gorda com curtose elevada e estresse sistêmico."""
        exposure = Decimal("10000000.00")
        vol = Decimal("0.02")

        es_gaussian = self.engine.calculate_parametric_expected_shortfall(
            exposure_brl=exposure,
            daily_volatility=vol,
            horizon_days=2,
            kurtosis=Decimal("3.00"),
            stress_tail_factor=Decimal("0.00"),
        )

        # Estresse sistêmico com curtose leptocúrtica (kurtosis = 7.0) e tail boost de 20%
        es_stressed = self.engine.calculate_parametric_expected_shortfall(
            exposure_brl=exposure,
            daily_volatility=vol,
            horizon_days=2,
            kurtosis=Decimal("7.00"),
            stress_tail_factor=Decimal("0.20"),
        )

        self.assertTrue(es_stressed.is_fat_tail_adjusted)
        self.assertGreater(es_stressed.fat_tail_multiplier, Decimal("1.20"))
        self.assertGreater(es_stressed.expected_shortfall_brl, es_gaussian.expected_shortfall_brl * Decimal("1.30"))

    def test_historical_expected_shortfall(self) -> None:
        """Testa o Expected Shortfall Histórico sobre a cauda das piores perdas."""
        losses = [Decimal(str(i * 100)) for i in range(1, 1001)]
        es_hist = self.engine.calculate_historical_expected_shortfall(
            historical_losses_brl=losses,
            horizon_days=2,
            confidence_level=Decimal("0.995"),
            stress_tail_factor=Decimal("0.10"),
        )

        self.assertGreater(es_hist.expected_shortfall_brl, es_hist.var_threshold_brl)

    # ==========================================================================
    # 4. Liquidity-Adjusted Margins (LAM) e Closeout Horizon
    # ==========================================================================

    def test_closeout_horizon_determination(self) -> None:
        """Verifica a determinação do prazo de liquidação forçada entre 2 e 5 dias."""
        adv = Decimal("1000000")  # ADV de 1 milhão de ações
        part_limit = Decimal("0.15")  # 15% ADV = 150.000 ações/dia

        # Posição pequena (100.000 ações): consome menos de 1 dia -> mínimo 2 dias
        h_small = self.engine.calculate_closeout_horizon(
            position_quantity=Decimal("100000"),
            adv_shares=adv,
            participation_limit=part_limit,
        )
        self.assertEqual(h_small, 2)

        # Posição média (450.000 ações): 450k / 150k = 3 dias
        h_mid = self.engine.calculate_closeout_horizon(
            position_quantity=Decimal("450000"),
            adv_shares=adv,
            participation_limit=part_limit,
        )
        self.assertEqual(h_mid, 3)

        # Posição grande (600.000 ações): 600k / 150k = 4 dias
        h_large = self.engine.calculate_closeout_horizon(
            position_quantity=Decimal("600000"),
            adv_shares=adv,
            participation_limit=part_limit,
        )
        self.assertEqual(h_large, 4)

        # Posição gigantesca (2.000.000 ações): excederia 10 dias -> teto de 5 dias
        h_mega = self.engine.calculate_closeout_horizon(
            position_quantity=Decimal("2000000"),
            adv_shares=adv,
            participation_limit=part_limit,
        )
        self.assertEqual(h_mega, 5)

    def test_liquidity_adjusted_margin_components(self) -> None:
        """Verifica os componentes aditivos da Margem LAM: tempo, spread e impacto."""
        base_risk = Decimal("500000.00")
        position_q = Decimal("500000")
        price = Decimal("40.00")
        vol = Decimal("0.02")

        params = LiquidityParameters(
            adv_shares=Decimal("1000000"),
            bid_ask_spread_pct=Decimal("0.0020"),  # 20 bps
            participation_rate_limit=Decimal("0.15"),
            impact_coefficient=Decimal("0.10"),
        )

        lam_res = self.engine.calculate_liquidity_adjusted_margin(
            base_risk_brl=base_risk,
            position_quantity=position_q,
            unit_price_brl=price,
            daily_volatility=vol,
            liquidity_params=params,
        )

        self.assertIn(lam_res.closeout_horizon_days, [3, 4])
        self.assertGreater(lam_res.time_scaling_factor, Decimal("1.00"))
        self.assertGreater(lam_res.spread_cost_brl, Decimal("0.00"))
        self.assertGreater(lam_res.market_impact_penalty_brl, Decimal("0.00"))
        # LAM total deve superar o risco base estático
        self.assertGreater(lam_res.total_lam_margin_brl, base_risk)

    # ==========================================================================
    # 5. Matriz de Haircuts Conservadores de TPFs (LAW-105A)
    # ==========================================================================

    def test_tpf_haircut_matrix_tenor_monotonicity(self) -> None:
        """Verifica que para todos os tipos de TPF o haircut é estritamente não-decrescente com o prazo."""
        for b_type in TPFBondType:
            previous_h = Decimal("0.00")
            for tenor in TENOR_ORDER:
                h = TPF_HAIRCUT_SCHEDULE[(b_type, tenor)]
                self.assertGreaterEqual(
                    h,
                    previous_h,
                    f"Haircut decrescente detectado para {b_type} no tenor {tenor}",
                )
                self.assertLess(h, Decimal("1.00"))
                previous_h = h

    def test_tpf_duration_hierarchy(self) -> None:
        """Verifica a hierarquia de risco: LFT (pós) < LTN (pré zero) <= NTN-F (pré cupom) < NTN-B (IPCA real)."""
        tenor = MaturityTenor.FROM_2Y_TO_5Y
        h_lft = TPF_HAIRCUT_SCHEDULE[(TPFBondType.LFT, tenor)]
        h_ltn = TPF_HAIRCUT_SCHEDULE[(TPFBondType.LTN, tenor)]
        h_ntnf = TPF_HAIRCUT_SCHEDULE[(TPFBondType.NTN_F, tenor)]
        h_ntnb = TPF_HAIRCUT_SCHEDULE[(TPFBondType.NTN_B, tenor)]

        self.assertLess(h_lft, h_ltn)
        self.assertLessEqual(h_ltn, h_ntnf)
        self.assertLess(h_ntnf, h_ntnb)

    def test_cross_border_collateral_fx_buffer(self) -> None:
        """Verifica a aplicação de buffer cambial para colaterais offshore (LAW-105A)."""
        domestic_bond = TPFCollateral(
            asset_id="NTN-B-2035-DOMESTIC",
            bond_type=TPFBondType.NTN_B,
            tenor=MaturityTenor.FROM_5Y_TO_10Y,
            face_value_brl=Decimal("1000000.00"),
            is_cross_border=False,
        )

        offshore_bond = TPFCollateral(
            asset_id="NTN-B-2035-OFFSHORE",
            bond_type=TPFBondType.NTN_B,
            tenor=MaturityTenor.FROM_5Y_TO_10Y,
            face_value_brl=Decimal("1000000.00"),
            is_cross_border=True,
            fx_buffer=Decimal("0.08"),
        )

        self.assertEqual(domestic_bond.total_haircut(), Decimal("0.1350"))
        # Offshore: 1 - (1 - 0.1350)*(1 - 0.08) = 1 - 0.865 * 0.92 = 1 - 0.7958 = 0.2042
        self.assertGreater(offshore_bond.total_haircut(), domestic_bond.total_haircut())
        self.assertLess(offshore_bond.eligible_collateral_value(), domestic_bond.eligible_collateral_value())

    # ==========================================================================
    # 6. Fator de Diversificação Intercommodities e Teto Prudencial
    # ==========================================================================

    def test_intercommodity_diversification_with_regulatory_cap(self) -> None:
        """Verifica que o crédito de diversificação não excede o teto prudencial de 30%."""
        margins = {
            AssetClass.EQUITIES: Decimal("1000000.00"),
            AssetClass.INTEREST_RATES: Decimal("1000000.00"),  # Correlação negativa (-0.25)
            AssetClass.FOREIGN_EXCHANGE: Decimal("500000.00"),
        }

        div_res = self.engine.calculate_intercommodity_diversification(
            standalone_margins=margins,
            max_credit_pct=Decimal("0.30"),
        )

        undiv_sum = Decimal("2500000.00")
        self.assertEqual(div_res.undiversified_sum_brl, undiv_sum)
        # O benefício nunca pode deixar a margem menor que 70% da soma (R$ 1.750.000,00)
        min_allowed = undiv_sum * Decimal("0.70")
        self.assertGreaterEqual(div_res.diversified_margin_brl, min_allowed)
        self.assertLessEqual(div_res.diversification_credit_pct, Decimal("0.3000"))

    def test_single_asset_diversification_neutrality(self) -> None:
        """Verifica que portfólio mono-classe tem fator de diversificação idêntico a 1.0."""
        margins = {
            AssetClass.EQUITIES: Decimal("2000000.00"),
        }
        div_res = self.engine.calculate_intercommodity_diversification(margins)
        self.assertEqual(div_res.diversified_margin_brl, Decimal("2000000.00"))
        self.assertEqual(div_res.diversification_factor, Decimal("1.0000"))
        self.assertEqual(div_res.diversification_credit_pct, Decimal("0.0000"))

    # ==========================================================================
    # 7. Cascata de Salvaguardas da Câmara B3 (LAW-006)
    # ==========================================================================

    def test_safeguard_waterfall_sequential_absorption(self) -> None:
        """Verifica a ordem rígida de absorção da cascata: Defaulter Margins -> SITG -> Mutualized."""
        loss = Decimal("3000000.00")  # Perda de R$ 3 milhões

        waterfall = self.engine.execute_safeguard_waterfall(
            gross_default_loss_brl=loss,
            defaulter_margin_brl=Decimal("1000000.00"),
            defaulter_fund_contrib_brl=Decimal("500000.00"),
            b3_sitg_brl=Decimal("500000.00"),
            mutualized_fund_brl=Decimal("2000000.00"),
            extraordinary_assessment_brl=Decimal("5000000.00"),
        )

        self.assertTrue(waterfall.is_fully_absorbed)
        self.assertEqual(waterfall.residual_uncovered_deficit_brl, Decimal("0.00"))
        self.assertEqual(waterfall.total_absorbed_brl, loss)

        # Camadas 1, 2 e 3 devem ter sido 100% consumidas (1.0M + 0.5M + 0.5M = 2.0M)
        self.assertEqual(waterfall.layers[0].loss_absorbed_brl, Decimal("1000000.00"))
        self.assertEqual(waterfall.layers[0].remaining_balance_brl, Decimal("0.00"))

        self.assertEqual(waterfall.layers[1].loss_absorbed_brl, Decimal("500000.00"))
        self.assertEqual(waterfall.layers[1].remaining_balance_brl, Decimal("0.00"))

        self.assertEqual(waterfall.layers[2].loss_absorbed_brl, Decimal("500000.00"))
        self.assertEqual(waterfall.layers[2].remaining_balance_brl, Decimal("0.00"))

        # Camada 4 (Mutualized Fund) absorve o restante (1.0M de 2.0M disponíveis)
        self.assertEqual(waterfall.layers[3].loss_absorbed_brl, Decimal("1000000.00"))
        self.assertEqual(waterfall.layers[3].remaining_balance_brl, Decimal("1000000.00"))

        # Camada 5 (Extraordinary Assessment) fica intocada
        self.assertEqual(waterfall.layers[4].loss_absorbed_brl, Decimal("0.00"))
        self.assertEqual(waterfall.layers[4].remaining_balance_brl, Decimal("5000000.00"))

    def test_safeguard_waterfall_catastrophic_deficit(self) -> None:
        """Verifica a identificação de déficit não coberto em caso de default extremo."""
        gross_loss = Decimal("15000000.00")
        waterfall = self.engine.execute_safeguard_waterfall(
            gross_default_loss_brl=gross_loss,
            defaulter_margin_brl=Decimal("2000000.00"),
            defaulter_fund_contrib_brl=Decimal("1000000.00"),
            b3_sitg_brl=Decimal("1000000.00"),
            mutualized_fund_brl=Decimal("3000000.00"),
            extraordinary_assessment_brl=Decimal("3000000.00"),
        )

        total_cap = Decimal("10000000.00")
        self.assertFalse(waterfall.is_fully_absorbed)
        self.assertEqual(waterfall.total_absorbed_brl, total_cap)
        self.assertEqual(waterfall.residual_uncovered_deficit_brl, Decimal("5000000.00"))
        # Conservação contábil
        self.assertEqual(
            gross_loss,
            waterfall.total_absorbed_brl + waterfall.residual_uncovered_deficit_brl,
        )

    # ==========================================================================
    # 8. Validações e Provas Formais de Invariantes (LAW-003, LAW-006, LAW-105A)
    # ==========================================================================

    def test_law_003_margin_covers_potential_risk(self) -> None:
        """Verifica LAW-003: Margem Requerida >= Risco Potencial."""
        # Caso conforme: Margem >= Risco
        assert_law_003_margin_covers_potential_risk(
            required_margin_brl=Decimal("1500000.00"),
            potential_risk_brl=Decimal("1400000.00"),
        )

        # Caso limite exato
        assert_law_003_margin_covers_potential_risk(
            required_margin_brl=Decimal("1500000.00"),
            potential_risk_brl=Decimal("1500000.00"),
        )

        # Violação de LAW-003: Margem < Risco Potencial
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_law_003_margin_covers_potential_risk(
                required_margin_brl=Decimal("1000000.00"),
                potential_risk_brl=Decimal("1200000.00"),
            )
        self.assertEqual(ctx.exception.law_code, "LAW-003")

    def test_law_006_collateral_coverage(self) -> None:
        """Verifica LAW-006: Colateral Líquido Elegível >= Margem Requerida."""
        # Conforme
        assert_law_006_collateral_safeguards_coverage(
            total_eligible_collateral_net_brl=Decimal("2000000.00"),
            required_margin_brl=Decimal("1800000.00"),
        )

        # Violação de LAW-006: Déficit de Garantia
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_law_006_collateral_safeguards_coverage(
                total_eligible_collateral_net_brl=Decimal("1500000.00"),
                required_margin_brl=Decimal("1800000.00"),
            )
        self.assertEqual(ctx.exception.law_code, "LAW-006")

    def test_law_105a_tpf_haircut_matrix_verification(self) -> None:
        """Verifica a integridade formal da matriz de haircuts e cobertura de TPFs (LAW-105A)."""
        collaterals = [
            TPFCollateral(
                asset_id="LFT-2026",
                bond_type=TPFBondType.LFT,
                tenor=MaturityTenor.UP_TO_1Y,
                face_value_brl=Decimal("1000000.00"),
            ),
            TPFCollateral(
                asset_id="LTN-2027",
                bond_type=TPFBondType.LTN,
                tenor=MaturityTenor.FROM_1Y_TO_2Y,
                face_value_brl=Decimal("1000000.00"),
            ),
        ]

        # Valor líquido = 1M * (1 - 0.008) + 1M * (1 - 0.045) = 992.000 + 955.000 = 1.947.000
        assert_law_105a_tpf_collateral_haircuts(
            collateral_basket=collaterals,
            required_margin_brl=Decimal("1900000.00"),
            participant_id="MC-001",
        )

        # Falha de cobertura no colateral TPF
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_law_105a_tpf_collateral_haircuts(
                collateral_basket=collaterals,
                required_margin_brl=Decimal("2100000.00"),
                participant_id="MC-001",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-006")

    # ==========================================================================
    # 9. Teste do Motor Integrado de Risco (CoreRiskAssessmentEngine)
    # ==========================================================================

    def test_integrated_risk_assessment_healthy_participant(self) -> None:
        """Cenário de participante adimplente com colateral superior à margem requerida."""
        margins = {
            AssetClass.EQUITIES: Decimal("600000.00"),
            AssetClass.INTEREST_RATES: Decimal("500000.00"),
        }
        lam_margin = Decimal("700000.00")

        collaterals = [
            TPFCollateral(
                asset_id="LFT-BR-01",
                bond_type=TPFBondType.LFT,
                tenor=MaturityTenor.UP_TO_1Y,
                face_value_brl=Decimal("2000000.00"),
            )
        ]

        assessment = self.assessment_engine.evaluate_participant_portfolio(
            participant_id="PART-BR-01",
            asset_margins=margins,
            lam_margin_brl=lam_margin,
            collateral_basket=collaterals,
        )

        self.assertEqual(assessment.status, MarginCallStatus.SUFFICIENT)
        self.assertGreater(assessment.margin_balance_brl, Decimal("0.00"))
        self.assertIn("LAW-003", assessment.invariants_verified)
        self.assertIn("LAW-006", assessment.invariants_verified)
        self.assertIn("LAW-105A", assessment.invariants_verified)

    def test_integrated_risk_assessment_margin_call_trigger(self) -> None:
        """Cenário de participante com déficit de colateral disparando chamada imediata."""
        margins = {
            AssetClass.EQUITIES: Decimal("1500000.00"),
            AssetClass.INTEREST_RATES: Decimal("1200000.00"),
        }
        lam_margin = Decimal("2200000.00")

        collaterals = [
            TPFCollateral(
                asset_id="NTN-B-LONG",
                bond_type=TPFBondType.NTN_B,
                tenor=MaturityTenor.OVER_10Y,  # Haircut de 18.50%
                face_value_brl=Decimal("1500000.00"),
            )
        ]

        assessment = self.assessment_engine.evaluate_participant_portfolio(
            participant_id="PART-DEFICIT-02",
            asset_margins=margins,
            lam_margin_brl=lam_margin,
            collateral_basket=collaterals,
        )

        self.assertEqual(assessment.status, MarginCallStatus.MARGIN_CALL_REQUIRED)
        self.assertLess(assessment.margin_balance_brl, Decimal("0.00"))
        self.assertIn("LAW-003", assessment.invariants_verified)
        # LAW-006 não pôde ser atestada como satisfeita devido ao déficit
        self.assertNotIn("LAW-006", assessment.invariants_verified)


if __name__ == "__main__":
    unittest.main()
