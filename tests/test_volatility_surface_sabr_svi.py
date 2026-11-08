"""B3 Integration Atlas — Suíte de Testes Formais para Superfícies de Volatilidade SVI e SABR (FL-101).

Validação unitária, matemática e estocástica exaustiva:
1. Modelo SVI de Gatheral (2004) e restrições de Lee.
2. Modelo SABR de Hagan et al. (2002), continuidade ATM e casos CEV.
3. Precificação Black-76, Paridade Put-Call e Inversão de Volatilidade.
4. Lei 85 / LAW-107A: Invariante de Não-Arbitragem de Calendário.
5. Lei 86 / LAW-107B: Invariante de Não-Arbitragem Butterfly e Convexidade.
6. Calibração Nelder-Mead Simplex em Standard Library.
7. Superfícies Oficiais B3 DOL e IBOV e carga da fixture JSON.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import unittest

from src.invariants import (
    InvariantViolationError,
    assert_butterfly_arbitrage_free,
    assert_calendar_arbitrage_free,
)
from src.volatility_surface_sabr_svi import (
    B3AssetType,
    B3VolatilitySurface,
    B3VolatilitySurfaceEngine,
    ButterflyArbitrageError,
    CalendarArbitrageError,
    DayCountConvention,
    OptionType,
    ParameterValidationError,
    SABRParameters,
    SVIParameters,
    VolatilityModelError,
    VolatilityModelType,
    VolatilitySlice,
    black76_call_price,
    black76_implied_vol,
    black76_price,
    black76_put_price,
    nelder_mead_optimize,
    norm_cdf,
    norm_pdf,
)


class TestSVIParametricModel(unittest.TestCase):
    """Testes unitários e matemáticos para o modelo Raw SVI de Jim Gatheral."""

    def setUp(self) -> None:
        # Parâmetros canônicos realistas para índice de ações (IBOV) com skew negativo
        self.svi_valid = SVIParameters(
            a=0.015,
            b=0.08,
            rho=-0.65,
            m=0.005,
            sigma=0.10,
        )

    def test_svi_total_variance_and_derivatives(self) -> None:
        """Testa o cálculo da variância total e suas derivadas analíticas w'(k) e w''(k)."""
        w_atm = self.svi_valid.total_variance(0.0)
        self.assertGreater(w_atm, 0.0)

        # Teste de convexidade estrita: w''(k) > 0 para qualquer k
        for k in [-0.5, -0.2, 0.0, 0.2, 0.5]:
            w = self.svi_valid.total_variance(k)
            dw = self.svi_valid.dw_dk(k)
            d2w = self.svi_valid.d2w_dk2(k)
            self.assertGreater(w, 0.0)
            self.assertGreater(d2w, 0.0, f"w''(k) deve ser estritamente positiva em k={k}")

            # Validação numérica de derivadas finitas centrais
            h = 1e-5
            dw_num = (self.svi_valid.total_variance(k + h) - self.svi_valid.total_variance(k - h)) / (2.0 * h)
            self.assertAlmostEqual(dw, dw_num, places=5)

            d2w_num = (
                self.svi_valid.total_variance(k + h)
                - 2.0 * self.svi_valid.total_variance(k)
                + self.svi_valid.total_variance(k - h)
            ) / (h * h)
            self.assertAlmostEqual(d2w, d2w_num, places=4)

    def test_svi_gatheral_density_positive(self) -> None:
        """Verifica que a função g(k) de Gatheral é estritamente não-negativa."""
        for k in [-0.4, -0.2, 0.0, 0.2, 0.4]:
            g = self.svi_valid.g_density(k)
            self.assertGreater(g, 0.0)

    def test_svi_parameter_validation_failures(self) -> None:
        """Verifica a rejeição fail-safe de parâmetros SVI que violam limites teóricos."""
        # 1. b negativo
        with self.assertRaises(ParameterValidationError):
            SVIParameters(a=0.01, b=-0.05, rho=0.0, m=0.0, sigma=0.1)

        # 2. |rho| >= 1
        with self.assertRaises(ParameterValidationError):
            SVIParameters(a=0.01, b=0.05, rho=1.0, m=0.0, sigma=0.1)
        with self.assertRaises(ParameterValidationError):
            SVIParameters(a=0.01, b=0.05, rho=-1.05, m=0.0, sigma=0.1)

        # 3. sigma <= 0
        with self.assertRaises(ParameterValidationError):
            SVIParameters(a=0.01, b=0.05, rho=0.0, m=0.0, sigma=0.0)

        # 4. Violação do limite de momento finito de Lee: b*(1 + |rho|) >= 2
        with self.assertRaises(ParameterValidationError):
            SVIParameters(a=0.01, b=1.5, rho=0.5, m=0.0, sigma=0.1)

        # 5. Violação da não-negatividade de variância total mínima
        with self.assertRaises(ParameterValidationError):
            SVIParameters(a=-0.20, b=0.05, rho=0.0, m=0.0, sigma=0.1)

    def test_svi_serialization_roundtrip(self) -> None:
        """Testa serialização to_dict() e from_dict()."""
        d = self.svi_valid.to_dict()
        reconstructed = SVIParameters.from_dict(d)
        self.assertEqual(self.svi_valid.a, reconstructed.a)
        self.assertEqual(self.svi_valid.b, reconstructed.b)
        self.assertEqual(self.svi_valid.rho, reconstructed.rho)
        self.assertEqual(self.svi_valid.m, reconstructed.m)
        self.assertEqual(self.svi_valid.sigma, reconstructed.sigma)


class TestSABRParametricModel(unittest.TestCase):
    """Testes unitários e matemáticos para a aproximação assintótica SABR de Hagan et al."""

    def setUp(self) -> None:
        self.F = 5.50
        self.T = 0.25
        # Parâmetros SABR típicos para opções de câmbio DOL (beta=1.0, lognormal)
        self.sabr_fx = SABRParameters(alpha=0.15, beta=1.0, rho=0.20, nu=0.40)
        # Parâmetros SABR típicos com beta CEV (beta=0.5)
        self.sabr_cev = SABRParameters(alpha=0.35, beta=0.5, rho=-0.30, nu=0.50)

    def test_sabr_atm_continuity(self) -> None:
        """Verifica a continuidade analítica suave na transição K -> F (ATM)."""
        vol_atm = self.sabr_fx.implied_vol(self.F, self.F, self.T)
        vol_near_up = self.sabr_fx.implied_vol(self.F, self.F + 1e-6, self.T)
        vol_near_dn = self.sabr_fx.implied_vol(self.F, self.F - 1e-6, self.T)

        self.assertAlmostEqual(vol_atm, vol_near_up, places=6)
        self.assertAlmostEqual(vol_atm, vol_near_dn, places=6)

    def test_sabr_smile_curvature(self) -> None:
        """Testa se o modelo gera volatilidades plausíveis para strikes ITM e OTM."""
        strikes = [4.8, 5.2, 5.5, 5.8, 6.2]
        vols = [self.sabr_fx.implied_vol(self.F, K, self.T) for K in strikes]

        for v in vols:
            self.assertGreater(v, 0.05)
            self.assertLess(v, 0.50)

        # Com rho > 0, vol de K alto (Call OTM) deve ser maior que vol de K baixo
        vol_low = self.sabr_fx.implied_vol(self.F, 4.8, self.T)
        vol_high = self.sabr_fx.implied_vol(self.F, 6.2, self.T)
        self.assertGreater(vol_high, vol_low)

    def test_sabr_cev_beta_half(self) -> None:
        """Testa SABR com elasticidade beta = 0.5."""
        vol_atm = self.sabr_cev.implied_vol(self.F, self.F, self.T)
        self.assertGreater(vol_atm, 0.0)

        # Com rho < 0, vol deve ser decrescente com o strike (equity skew)
        v_low = self.sabr_cev.implied_vol(self.F, 4.8, self.T)
        v_high = self.sabr_cev.implied_vol(self.F, 6.2, self.T)
        self.assertGreater(v_low, v_high)

    def test_sabr_parameter_validation_failures(self) -> None:
        """Verifica a rejeição fail-safe de parâmetros SABR inválidos."""
        # alpha <= 0
        with self.assertRaises(ParameterValidationError):
            SABRParameters(alpha=0.0, beta=1.0, rho=0.0, nu=0.2)

        # beta fora de [0, 1]
        with self.assertRaises(ParameterValidationError):
            SABRParameters(alpha=0.1, beta=-0.1, rho=0.0, nu=0.2)
        with self.assertRaises(ParameterValidationError):
            SABRParameters(alpha=0.1, beta=1.2, rho=0.0, nu=0.2)

        # |rho| >= 1
        with self.assertRaises(ParameterValidationError):
            SABRParameters(alpha=0.1, beta=1.0, rho=1.0, nu=0.2)

        # nu < 0
        with self.assertRaises(ParameterValidationError):
            SABRParameters(alpha=0.1, beta=1.0, rho=0.0, nu=-0.1)

    def test_sabr_serialization_roundtrip(self) -> None:
        """Testa serialização to_dict() e from_dict()."""
        d = self.sabr_fx.to_dict()
        reconstructed = SABRParameters.from_dict(d)
        self.assertEqual(self.sabr_fx.alpha, reconstructed.alpha)
        self.assertEqual(self.sabr_fx.beta, reconstructed.beta)
        self.assertEqual(self.sabr_fx.rho, reconstructed.rho)
        self.assertEqual(self.sabr_fx.nu, reconstructed.nu)


class TestBlack76PricingAndHelpers(unittest.TestCase):
    """Testes para o motor analítico Black-76 e funções de distribuição."""

    def test_normal_distribution_properties(self) -> None:
        """Testa simetria e limites da normal padrão."""
        self.assertAlmostEqual(norm_cdf(0.0), 0.5, places=9)
        self.assertAlmostEqual(norm_cdf(-5.0), 0.0, places=6)
        self.assertAlmostEqual(norm_cdf(5.0), 1.0, places=6)
        self.assertAlmostEqual(norm_pdf(0.0), 1.0 / math.sqrt(2.0 * math.pi), places=9)

    def test_put_call_parity_exact(self) -> None:
        """Testa rigorosamente a Paridade Put-Call Black-76: C - P == exp(-r*T) * (F - K)."""
        F = 130000.0
        r = 0.1075
        T = 0.5
        sigma = 0.22

        for K in [110000.0, 120000.0, 130000.0, 140000.0, 150000.0]:
            c = black76_call_price(F, K, T, sigma, r)
            p = black76_put_price(F, K, T, sigma, r)
            expected_diff = math.exp(-r * T) * (F - K)
            self.assertAlmostEqual(c - p, expected_diff, places=4)

    def test_implied_vol_inverter(self) -> None:
        """Testa a recuperação exata da volatilidade implícita a partir do preço de mercado."""
        F = 5.50
        K = 5.60
        T = 0.25
        r = 0.1075
        true_vol = 0.1650

        c_price = black76_call_price(F, K, T, true_vol, r)
        recovered_vol = black76_implied_vol(c_price, F, K, T, r, OptionType.CALL)
        self.assertAlmostEqual(recovered_vol, true_vol, places=5)

        p_price = black76_put_price(F, K, T, true_vol, r)
        recovered_put_vol = black76_implied_vol(p_price, F, K, T, r, OptionType.PUT)
        self.assertAlmostEqual(recovered_put_vol, true_vol, places=5)


class TestVolatilitySliceAndSurfaceInvariants(unittest.TestCase):
    """Testes formais de conformidade e violação das Leis LAW-107A e LAW-107B."""

    def setUp(self) -> None:
        self.engine = B3VolatilitySurfaceEngine()
        self.dol_surface = self.engine.create_b3_dol_surface()
        self.ibov_surface = self.engine.create_b3_ibov_surface()

    def test_dol_surface_invariants_pass(self) -> None:
        """Verifica que a superfície oficial de Dólar B3 passa em todas as invariantes."""
        results = self.dol_surface.verify_invariants(raise_on_error=True)
        for law, passed in results.items():
            self.assertTrue(passed, f"Lei {law} falhou na superfície DOL")

    def test_ibov_surface_invariants_pass(self) -> None:
        """Verifica que a superfície oficial de Ibovespa B3 passa em todas as invariantes."""
        results = self.ibov_surface.verify_invariants(raise_on_error=True)
        for law, passed in results.items():
            self.assertTrue(passed, f"Lei {law} falhou na superfície IBOV")

    def test_total_variance_interpolation_monotonicity(self) -> None:
        """Verifica que a interpolação em T garante variância total não-decrescente em T contínuo."""
        # Avalia a superfície IBOV em uma grade fina de T entre 21 DU e 252 DU
        t_values = [0.10, 0.15, 0.20, 0.30, 0.40, 0.60, 0.80, 1.00]
        k_test = -0.10

        prev_w = -1.0
        for T in t_values:
            w = self.ibov_surface.total_variance(k_test, T)
            if prev_w >= 0.0:
                self.assertGreaterEqual(
                    w,
                    prev_w - 1e-9,
                    f"Variância decrescente detectada: w(T={T})={w} < w(prev)={prev_w}",
                )
            prev_w = w

    # -----------------------------------------------------------------------
    # Violação Adversarial da Lei 85 / LAW-107A: Arbitragem de Calendário
    # -----------------------------------------------------------------------

    def test_law_107a_calendar_arbitrage_violation_detected(self) -> None:
        """Testa a detecção rigorosa de arbitragem de calendário quando w(k, T2) < w(k, T1)."""
        # Criamos duas fatias artificiais onde a segunda tem menor variância total
        s1 = VolatilitySlice(
            slice_id="SLICE-T1",
            maturity_t=0.25,
            days_to_expiry=63,
            forward=5.50,
            risk_free_rate=0.10,
            model_type=VolatilityModelType.SABR,
            sabr_params=SABRParameters(alpha=0.20, beta=1.0, rho=0.0, nu=0.1),
        )
        s2 = VolatilitySlice(
            slice_id="SLICE-T2",
            maturity_t=0.50,
            days_to_expiry=126,
            forward=5.60,
            risk_free_rate=0.10,
            model_type=VolatilityModelType.SABR,
            # alpha drasticamente reduzido gerando w2 < w1
            sabr_params=SABRParameters(alpha=0.10, beta=1.0, rho=0.0, nu=0.1),
        )

        bad_surface = B3VolatilitySurface(
            surface_id="BAD-CALENDAR-SURFACE",
            asset_type=B3AssetType.DOL,
            model_type=VolatilityModelType.SABR,
            as_of_date="2026-11-04",
            slices=[s1, s2],
        )

        # 1. Auditoria formal sem lançar erro deve sinalizar is_arbitrage_free=False
        cal_rep = bad_surface.verify_calendar_arbitrage(raise_on_error=False)
        self.assertFalse(cal_rep["is_arbitrage_free"])
        self.assertGreater(cal_rep["violations_count"], 0)

        # 2. Com raise_on_error=True deve levantar InvariantViolationError (LAW-107A)
        with self.assertRaises(InvariantViolationError) as ctx:
            bad_surface.verify_calendar_arbitrage(raise_on_error=True)
        self.assertIn("LAW-107A", str(ctx.exception))

        # 3. Teste direto da asserção de invariante
        with self.assertRaises(InvariantViolationError) as ctx_assert:
            assert_calendar_arbitrage_free(
                w_t1=0.02,
                w_t2=0.01,
                t1=0.25,
                t2=0.50,
                moneyness_k=0.0,
                surface_id="TEST-DIRECT",
            )
        self.assertIn("LAW-107A", str(ctx_assert.exception))

    # -----------------------------------------------------------------------
    # Violação Adversarial da Lei 86 / LAW-107B: Arbitragem Butterfly
    # -----------------------------------------------------------------------

    def test_law_107b_butterfly_arbitrage_violation_detected(self) -> None:
        """Testa a detecção rigorosa de arbitragem de borboleta (densidade negativa g(k) < 0)."""
        # Parâmetro de curvatura violando a suavidade de Gatheral
        # Um b excessivo com rho muito inclinado gera g(k) < 0
        s_bad = VolatilitySlice(
            slice_id="SLICE-BAD-BUTTERFLY",
            maturity_t=0.25,
            days_to_expiry=63,
            forward=130000.0,
            risk_free_rate=0.10,
            model_type=VolatilityModelType.SVI,
            svi_params=SVIParameters(
                a=0.005,
                b=0.70,
                rho=-0.75,
                m=0.0,
                sigma=0.015,
            ),
        )

        rep = s_bad.check_butterfly_arbitrage(raise_on_error=False)
        self.assertFalse(rep["is_arbitrage_free"])
        self.assertLess(rep["min_g"], 0.0)

        # Teste com raise_on_error
        with self.assertRaises(InvariantViolationError) as ctx:
            s_bad.check_butterfly_arbitrage(raise_on_error=True)
        self.assertIn("LAW-107B", str(ctx.exception))

        # Teste direto da asserção com densidade negativa
        with self.assertRaises(InvariantViolationError) as ctx_assert:
            assert_butterfly_arbitrage_free(
                g_k=-0.05,
                moneyness_k=0.10,
                maturity_t=0.25,
                surface_id="TEST-BUTTERFLY",
            )
        self.assertIn("LAW-107B", str(ctx_assert.exception))

        # Teste direto com preços de call violando convexidade
        with self.assertRaises(InvariantViolationError) as ctx_convex:
            assert_butterfly_arbitrage_free(
                g_k=0.5,
                surface_id="TEST-CONVEXITY",
                call_k1=10.0,
                call_k2=12.0,  # C1 - 2*C2 + C3 = 10 - 24 + 11 = -3 < 0
                call_k3=11.0,
            )
        self.assertIn("LAW-107B", str(ctx_convex.exception))


class TestNelderMeadCalibration(unittest.TestCase):
    """Testes para o otimizador Nelder-Mead e funções de calibração de mercado."""

    def test_nelder_mead_quadratic_minimum(self) -> None:
        """Testa se o otimizador converge para o mínimo global de função quadrática 3D."""
        def quadratic(x: List[float]) -> float:
            return (x[0] - 1.5) ** 2 + (x[1] + 2.0) ** 2 + (x[2] - 0.75) ** 2

        best_x, best_val = nelder_mead_optimize(quadratic, [0.0, 0.0, 0.0], max_iter=300)
        self.assertAlmostEqual(best_x[0], 1.5, places=3)
        self.assertAlmostEqual(best_x[1], -2.0, places=3)
        self.assertAlmostEqual(best_x[2], 0.75, places=3)
        self.assertAlmostEqual(best_val, 0.0, places=5)

    def test_svi_slice_calibration_from_market_quotes(self) -> None:
        """Testa calibração de fatia SVI a partir de cotações geradas."""
        true_svi = SVIParameters(a=0.015, b=0.08, rho=-0.60, m=0.0, sigma=0.10)
        F = 130000.0
        T = 0.25
        strikes = [115000.0, 120000.0, 125000.0, 130000.0, 135000.0, 140000.0]
        market_vols = [true_svi.implied_vol(math.log(K / F), T) for K in strikes]

        calibrated_svi = B3VolatilitySurfaceEngine.calibrate_svi_slice(
            strikes=strikes,
            market_vols=market_vols,
            forward=F,
            maturity_t=T,
        )

        # O modelo calibrado deve ter volatilidades muito próximas das de mercado
        for K, v_tgt in zip(strikes, market_vols):
            v_fit = calibrated_svi.implied_vol(math.log(K / F), T)
            self.assertAlmostEqual(v_fit, v_tgt, delta=0.015)

    def test_sabr_slice_calibration_from_market_quotes(self) -> None:
        """Testa calibração de fatia SABR a partir de cotações geradas."""
        true_sabr = SABRParameters(alpha=0.14, beta=1.0, rho=0.18, nu=0.35)
        F = 5.50
        T = 0.50
        strikes = [5.0, 5.2, 5.5, 5.8, 6.0]
        market_vols = [true_sabr.implied_vol(F, K, T) for K in strikes]

        calibrated_sabr = B3VolatilitySurfaceEngine.calibrate_sabr_slice(
            strikes=strikes,
            market_vols=market_vols,
            forward=F,
            maturity_t=T,
            beta=1.0,
        )

        for K, v_tgt in zip(strikes, market_vols):
            v_fit = calibrated_sabr.implied_vol(F, K, T)
            self.assertAlmostEqual(v_fit, v_tgt, delta=0.015)


class TestFixtureIntegration(unittest.TestCase):
    """Testes de validação da fixture institucional JSON."""

    def test_load_and_validate_fixture(self) -> None:
        """Carrega fixtures/volatility_surface_sabr_svi.json e valida as superfícies."""
        fixture_path = Path("/mnt/j/Documents/github/b3-integration-atlas/fixtures/volatility_surface_sabr_svi.json")
        self.assertTrue(fixture_path.exists(), f"Fixture não encontrada: {fixture_path}")

        with open(fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["version"], "1.0.0")
        self.assertIn("surfaces", data)

        # Reconstrói superfícies a partir da fixture
        dol_dict = data["surfaces"]["SURFACE-B3-DOL-SABR"]
        dol_surf = B3VolatilitySurface.from_dict(dol_dict)
        self.assertEqual(dol_surf.asset_type, B3AssetType.DOL)
        self.assertEqual(len(dol_surf.slices), 5)
        self.assertTrue(all(dol_surf.verify_invariants(raise_on_error=True).values()))

        ibov_dict = data["surfaces"]["SURFACE-B3-IBOV-SVI"]
        ibov_surf = B3VolatilitySurface.from_dict(ibov_dict)
        self.assertEqual(ibov_surf.asset_type, B3AssetType.IBOV)
        self.assertEqual(len(ibov_surf.slices), 5)
        self.assertTrue(all(ibov_surf.verify_invariants(raise_on_error=True).values()))


if __name__ == "__main__":
    unittest.main()
