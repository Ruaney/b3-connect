"""Suíte de Testes Unitários e Invariantes Formais — Opções Asiáticas de Balcão NoMe B3 (FL-092).

Valida integralmente o motor quantitativo de precificação analítica, gregas de 1ª e 2ª ordem,
modelos Kemna-Vorst, Turnbull-Wakeman e Curran, ciclo de observações, liquidação financeira
e a auditoria estrita das leis de invariantes:
- LAW-095: Paridade Put-Call Asiática e Não-Arbitragem (Fixed e Floating Strike).
- LAW-096: Menor Volatilidade Efetiva e Dominância de Preço sobre Vanilla (Vol_Asian <= Vol_Vanilla).
- LAW-097: Conservação Financeira Bilateral Soma-Zero em Liquidação (Centavo a Centavo).

100% Python Standard Library (unittest, math, decimal, datetime, json, pathlib).
"""

from datetime import date, timedelta
from decimal import Decimal
import json
import math
from pathlib import Path
import unittest

from src.asian_exotic_options import (
    AsianAssetSpecification,
    AsianAverageType,
    AsianContractStatus,
    AsianExoticOptionsEngine,
    AsianGreeks,
    AsianInvariantViolationError,
    AsianOptionContract,
    AsianOptionError,
    AsianOptionType,
    AsianPortfolioSummary,
    AsianPricer,
    AsianPricingError,
    AsianPricingModel,
    AsianPricingResult,
    AsianSamplingFrequency,
    AsianSettlementError,
    AsianSettlementRecord,
    AsianStrikeType,
    AsianUnderlyingType,
    AsianValidationError,
    ObservationPoint,
    PARITY_TOLERANCE,
    round_four,
    round_greek,
    round_money,
)
from src.invariants import (
    assert_asian_effective_volatility_bound,
    assert_asian_put_call_parity,
    assert_asian_zero_sum_settlement,
)


class TestAsianSpecificationsAndContracts(unittest.TestCase):
    """Testes para especificações de ativos e entidades de contrato."""

    def test_default_asset_specifications(self) -> None:
        """Garante que as especificações contratuais padronizadas B3 estejam estritas."""
        ptax = AsianAssetSpecification.get_default(AsianUnderlyingType.USD_PTAX)
        self.assertEqual(ptax.currency, "BRL")
        self.assertEqual(ptax.tick_size, Decimal("0.0001"))
        self.assertEqual(ptax.contract_multiplier, Decimal("1.00"))

        soja = AsianAssetSpecification.get_default(AsianUnderlyingType.SOYBEAN)
        self.assertEqual(soja.currency, "USD")
        self.assertEqual(soja.contract_multiplier, Decimal("450.00"))
        self.assertEqual(soja.tick_size, Decimal("0.01"))

        milho = AsianAssetSpecification.get_default(AsianUnderlyingType.CORN)
        self.assertEqual(milho.currency, "BRL")
        self.assertEqual(milho.contract_multiplier, Decimal("450.00"))
        self.assertEqual(milho.tick_size, Decimal("0.01"))

        boi = AsianAssetSpecification.get_default(AsianUnderlyingType.CATTLE)
        self.assertEqual(boi.currency, "BRL")
        self.assertEqual(boi.contract_multiplier, Decimal("330.00"))
        self.assertEqual(boi.tick_size, Decimal("0.01"))

        cafe = AsianAssetSpecification.get_default(AsianUnderlyingType.COFFEE)
        self.assertEqual(cafe.currency, "USD")
        self.assertEqual(cafe.contract_multiplier, Decimal("100.00"))
        self.assertEqual(cafe.tick_size, Decimal("0.05"))

    def test_contract_validation_rejects_invalid_parameters(self) -> None:
        """Verifica se contratos com dados inconsistentes são estritamente rejeitados."""
        # Quantidade <= 0
        c_bad = AsianOptionContract(
            contract_id="BAD-001",
            nome_protocol="PROTO-001",
            underlying_type=AsianUnderlyingType.USD_PTAX,
            option_type=AsianOptionType.CALL,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            average_type=AsianAverageType.ARITHMETIC,
            sampling_frequency=AsianSamplingFrequency.DAILY,
            trade_date=date(2026, 10, 1),
            start_averaging_date=date(2026, 10, 1),
            expiration_date=date(2026, 10, 30),
            settlement_date=date(2026, 11, 3),
            strike_price=Decimal("5.40"),
            quantity=Decimal("0.00"),  # inválido
            contract_multiplier=Decimal("1.0"),
            spot_initial=Decimal("5.40"),
            risk_free_rate=Decimal("0.1075"),
            volatility=Decimal("0.15"),
            observations=[ObservationPoint(observation_date=date(2026, 10, 30))],
        )
        with self.assertRaises(AssertionError):
            c_bad.verify_invariants()

        # Data de trade posterior à expiração
        c_bad_date = AsianOptionContract(
            contract_id="BAD-002",
            nome_protocol="PROTO-002",
            underlying_type=AsianUnderlyingType.USD_PTAX,
            option_type=AsianOptionType.CALL,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            average_type=AsianAverageType.ARITHMETIC,
            sampling_frequency=AsianSamplingFrequency.DAILY,
            trade_date=date(2026, 11, 1),
            start_averaging_date=date(2026, 10, 1),  # anterior ao trade
            expiration_date=date(2026, 10, 30),
            settlement_date=date(2026, 11, 3),
            strike_price=Decimal("5.40"),
            quantity=Decimal("1000.00"),
            contract_multiplier=Decimal("1.0"),
            spot_initial=Decimal("5.40"),
            risk_free_rate=Decimal("0.1075"),
            volatility=Decimal("0.15"),
            observations=[ObservationPoint(observation_date=date(2026, 10, 30))],
        )
        with self.assertRaises(AssertionError):
            c_bad_date.verify_invariants()

    def test_contract_serialization_roundtrip(self) -> None:
        """Garante a serialização to_dict() e desserialização from_dict() perfeita."""
        contract = AsianOptionContract(
            contract_id="OPT-ROUNDTRIP-01",
            nome_protocol="PROTO-RT-01",
            underlying_type=AsianUnderlyingType.CORN,
            option_type=AsianOptionType.PUT,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            average_type=AsianAverageType.ARITHMETIC,
            sampling_frequency=AsianSamplingFrequency.WEEKLY,
            trade_date=date(2026, 10, 1),
            start_averaging_date=date(2026, 10, 5),
            expiration_date=date(2026, 10, 30),
            settlement_date=date(2026, 11, 3),
            strike_price=Decimal("65.00"),
            quantity=Decimal("10.00"),
            contract_multiplier=Decimal("450.00"),
            spot_initial=Decimal("64.50"),
            risk_free_rate=Decimal("0.1075"),
            dividend_yield=Decimal("0.02"),
            volatility=Decimal("0.22"),
            observations=[
                ObservationPoint(observation_date=date(2026, 10, 9), observed_price=Decimal("64.80")),
                ObservationPoint(observation_date=date(2026, 10, 16), observed_price=Decimal("65.10")),
            ],
            holder_account="CC-101",
            writer_account="CC-202",
        )
        c_dict = contract.to_dict()
        reconstructed = AsianOptionContract.from_dict(c_dict)

        self.assertEqual(contract.contract_id, reconstructed.contract_id)
        self.assertEqual(contract.strike_price, reconstructed.strike_price)
        self.assertEqual(contract.quantity, reconstructed.quantity)
        self.assertEqual(len(contract.observations), len(reconstructed.observations))
        self.assertEqual(reconstructed.observations[0].observed_price, Decimal("64.80"))


class TestObservationSchedulesAndAverageCalculation(unittest.TestCase):
    """Testes para geração de cronogramas de observação e cálculo de médias."""

    def test_daily_schedule_skips_weekends(self) -> None:
        """Garante que amostragem diária ignore sábados e domingos."""
        # 2026-10-01 (Qui) a 2026-10-09 (Sex): 7 dias úteis (1, 2, 5, 6, 7, 8, 9)
        sched = AsianExoticOptionsEngine.generate_observation_schedule(
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 9),
            frequency=AsianSamplingFrequency.DAILY,
        )
        self.assertEqual(len(sched), 7)
        for pt in sched:
            self.assertLess(pt.observation_date.weekday(), 5)

    def test_weekly_schedule_generation(self) -> None:
        """Verifica geração semanal para sextas-feiras."""
        sched = AsianExoticOptionsEngine.generate_observation_schedule(
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 30),
            frequency=AsianSamplingFrequency.WEEKLY,
        )
        # Sextas de Out/2026: 2, 9, 16, 23, 30 -> 5 pontos
        self.assertEqual(len(sched), 5)
        for pt in sched:
            self.assertEqual(pt.observation_date.weekday(), 4)

    def test_arithmetic_and_geometric_average_computation(self) -> None:
        """Verifica o cálculo exato de média aritmética e média geométrica."""
        prices = [Decimal("100.00"), Decimal("105.00"), Decimal("110.25")]
        obs = [
            ObservationPoint(observation_date=date(2026, 10, 1), observed_price=prices[0]),
            ObservationPoint(observation_date=date(2026, 10, 2), observed_price=prices[1]),
            ObservationPoint(observation_date=date(2026, 10, 3), observed_price=prices[2]),
        ]

        contract_arith = AsianOptionContract(
            contract_id="ARITH-01",
            nome_protocol="P-01",
            underlying_type=AsianUnderlyingType.USD_PTAX,
            option_type=AsianOptionType.CALL,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            average_type=AsianAverageType.ARITHMETIC,
            sampling_frequency=AsianSamplingFrequency.DAILY,
            trade_date=date(2026, 10, 1),
            start_averaging_date=date(2026, 10, 1),
            expiration_date=date(2026, 10, 3),
            settlement_date=date(2026, 10, 4),
            strike_price=Decimal("100.00"),
            quantity=Decimal("100.0"),
            contract_multiplier=Decimal("1.0"),
            spot_initial=Decimal("100.00"),
            risk_free_rate=Decimal("0.10"),
            volatility=Decimal("0.20"),
            observations=obs,
        )

        contract_geom = AsianOptionContract(
            contract_id="GEOM-01",
            nome_protocol="P-02",
            underlying_type=AsianUnderlyingType.USD_PTAX,
            option_type=AsianOptionType.CALL,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            average_type=AsianAverageType.GEOMETRIC,
            sampling_frequency=AsianSamplingFrequency.DAILY,
            trade_date=date(2026, 10, 1),
            start_averaging_date=date(2026, 10, 1),
            expiration_date=date(2026, 10, 3),
            settlement_date=date(2026, 10, 4),
            strike_price=Decimal("100.00"),
            quantity=Decimal("100.0"),
            contract_multiplier=Decimal("1.0"),
            spot_initial=Decimal("100.00"),
            risk_free_rate=Decimal("0.10"),
            volatility=Decimal("0.20"),
            observations=obs,
        )

        avg_arith = contract_arith.compute_current_average()
        # Média Aritmética: (100 + 105 + 110.25) / 3 = 105.0833
        self.assertAlmostEqual(float(avg_arith), 105.0833, places=3)

        avg_geom = contract_geom.compute_current_average()
        # Média Geométrica: (100 * 105 * 110.25)^(1/3) = (1157625)^(1/3) = 105.0000
        self.assertAlmostEqual(float(avg_geom), 105.0000, places=3)

        # Invariante clássica da média: Geométrica <= Aritmética (AM-GM Inequality)
        self.assertLessEqual(avg_geom, avg_arith)


class TestAnalyticalPricingModelsAndInvariants(unittest.TestCase):
    """Testes detalhados para os modelos Kemna-Vorst, Turnbull-Wakeman e Curran."""

    def test_n_equals_one_collapses_to_vanilla_black_scholes(self) -> None:
        """Garante que quando N=1, a Opção Asiática colapsa analiticamente para a Opção Vanilla Europeia."""
        spot = 100.0
        strike = 100.0
        r = 0.10
        q = 0.02
        sigma = 0.20
        tau = 1.0

        # Preço Vanilla Europeu
        vanilla_call = AsianPricer._vanilla_european_price(
            spot, strike, r, q, sigma, tau, AsianOptionType.CALL
        )
        vanilla_put = AsianPricer._vanilla_european_price(
            spot, strike, r, q, sigma, tau, AsianOptionType.PUT
        )

        # Média Geométrica N=1
        call_geom, _, eff_vol_g, _, _, _, _ = AsianPricer.price_geometric_kemna_vorst(
            spot, strike, r, q, sigma, tau, [tau], AsianOptionType.CALL
        )
        put_geom, _, _, _, _, _, _ = AsianPricer.price_geometric_kemna_vorst(
            spot, strike, r, q, sigma, tau, [tau], AsianOptionType.PUT
        )

        # Média Aritmética N=1
        call_arith, _, eff_vol_a, _, _, _, _ = AsianPricer.price_arithmetic_turnbull_wakeman(
            spot, strike, r, q, sigma, tau, [tau], AsianOptionType.CALL
        )
        put_arith, _, _, _, _, _, _ = AsianPricer.price_arithmetic_turnbull_wakeman(
            spot, strike, r, q, sigma, tau, [tau], AsianOptionType.PUT
        )

        # Confronto de precisão estrita (10 casas decimais)
        self.assertAlmostEqual(call_geom, vanilla_call, places=8)
        self.assertAlmostEqual(put_geom, vanilla_put, places=8)
        self.assertAlmostEqual(call_arith, vanilla_call, places=8)
        self.assertAlmostEqual(put_arith, vanilla_put, places=8)
        self.assertAlmostEqual(eff_vol_g, sigma, places=6)
        self.assertAlmostEqual(eff_vol_a, sigma, places=6)

    def test_law_095_asian_put_call_parity_fixed_strike(self) -> None:
        """Audita rigorosamente a Paridade Put-Call Asiática (LAW-095) para Fixed Strike."""
        spot = 5.45
        strike = 5.50
        r = 0.1075
        q = 0.045
        sigma = 0.16
        tau = 0.5
        N = 20
        dt = tau / N
        times = [i * dt for i in range(1, N + 1)]

        # 1. Média Geométrica (Kemna-Vorst)
        call_g, exp_g, _, _, _, _, p_diff_g = AsianPricer.price_geometric_kemna_vorst(
            spot, strike, r, q, sigma, tau, times, AsianOptionType.CALL, AsianStrikeType.FIXED_STRIKE
        )
        put_g, _, _, _, _, _, _ = AsianPricer.price_geometric_kemna_vorst(
            spot, strike, r, q, sigma, tau, times, AsianOptionType.PUT, AsianStrikeType.FIXED_STRIKE
        )
        disc = math.exp(-r * tau)
        parity_g = disc * (exp_g - strike)
        self.assertAlmostEqual(call_g - put_g, parity_g, places=6)
        self.assertLess(p_diff_g, 1e-5)

        # Função de invariante de módulo formal
        assert_asian_put_call_parity(
            call_price=Decimal(str(call_g)),
            put_price=Decimal(str(put_g)),
            discount_factor=Decimal(str(disc)),
            expected_underlying_avg=Decimal(str(exp_g)),
            strike_price=Decimal(str(strike)),
            tolerance=PARITY_TOLERANCE,
            contract_id="TEST-PARITY-GEOM",
        )

        # 2. Média Aritmética (Turnbull-Wakeman)
        call_a, exp_a, _, _, _, _, p_diff_a = AsianPricer.price_arithmetic_turnbull_wakeman(
            spot, strike, r, q, sigma, tau, times, AsianOptionType.CALL, AsianStrikeType.FIXED_STRIKE
        )
        put_a, _, _, _, _, _, _ = AsianPricer.price_arithmetic_turnbull_wakeman(
            spot, strike, r, q, sigma, tau, times, AsianOptionType.PUT, AsianStrikeType.FIXED_STRIKE
        )
        parity_a = disc * (exp_a - strike)
        self.assertAlmostEqual(call_a - put_a, parity_a, places=6)
        self.assertLess(p_diff_a, 1e-5)

        assert_asian_put_call_parity(
            call_price=Decimal(str(call_a)),
            put_price=Decimal(str(put_a)),
            discount_factor=Decimal(str(disc)),
            expected_underlying_avg=Decimal(str(exp_a)),
            strike_price=Decimal(str(strike)),
            tolerance=PARITY_TOLERANCE,
            contract_id="TEST-PARITY-ARITH",
        )

        # 3. Média Aritmética (Curran)
        call_c, exp_c, _, _, _, _, p_diff_c = AsianPricer.price_arithmetic_curran(
            spot, strike, r, q, sigma, tau, times, AsianOptionType.CALL, AsianStrikeType.FIXED_STRIKE
        )
        put_c, _, _, _, _, _, _ = AsianPricer.price_arithmetic_curran(
            spot, strike, r, q, sigma, tau, times, AsianOptionType.PUT, AsianStrikeType.FIXED_STRIKE
        )
        parity_c = disc * (exp_c - strike)
        self.assertAlmostEqual(call_c - put_c, parity_c, places=6)
        self.assertLess(p_diff_c, 1e-5)

    def test_law_095_asian_put_call_parity_floating_strike(self) -> None:
        """Audita a Paridade Put-Call Asiática para Strike Flutuante (Average Strike Option)."""
        spot = 65.0
        r = 0.1075
        q = 0.02
        sigma = 0.22
        tau = 0.25
        N = 12
        dt = tau / N
        times = [i * dt for i in range(1, N + 1)]

        # Floating Strike Geometric
        call_fg, exp_g, _, _, _, fwd_g, p_diff_fg = AsianPricer.price_geometric_kemna_vorst(
            spot, 0.0, r, q, sigma, tau, times, AsianOptionType.CALL, AsianStrikeType.FLOATING_STRIKE
        )
        put_fg, _, _, _, _, _, _ = AsianPricer.price_geometric_kemna_vorst(
            spot, 0.0, r, q, sigma, tau, times, AsianOptionType.PUT, AsianStrikeType.FLOATING_STRIKE
        )
        disc = math.exp(-r * tau)
        E_ST = spot * math.exp((r - q) * tau)
        parity_fg = disc * (E_ST - exp_g)
        self.assertAlmostEqual(call_fg - put_fg, parity_fg, places=6)
        self.assertLess(p_diff_fg, 1e-5)

        # Floating Strike Arithmetic
        call_fa, exp_a, _, _, _, fwd_a, p_diff_fa = AsianPricer.price_arithmetic_turnbull_wakeman(
            spot, 0.0, r, q, sigma, tau, times, AsianOptionType.CALL, AsianStrikeType.FLOATING_STRIKE
        )
        put_fa, _, _, _, _, _, _ = AsianPricer.price_arithmetic_turnbull_wakeman(
            spot, 0.0, r, q, sigma, tau, times, AsianOptionType.PUT, AsianStrikeType.FLOATING_STRIKE
        )
        parity_fa = disc * (E_ST - exp_a)
        self.assertAlmostEqual(call_fa - put_fa, parity_fa, places=6)
        self.assertLess(p_diff_fa, 1e-5)

    def test_law_096_lower_effective_volatility_and_price_dominance(self) -> None:
        """Audita a Invariante de Menor Volatilidade Efetiva e Menor Prêmio Relativo (LAW-096)."""
        spot = 100.0
        strike = 100.0  # At-the-money
        r = 0.10
        q = 0.00
        sigma = 0.25
        tau = 1.0
        N = 52  # Amostragem semanal (52 observações)
        dt = tau / N
        times = [i * dt for i in range(1, N + 1)]

        # Opção Vanilla Europeia ATM
        call_vanilla = AsianPricer._vanilla_european_price(
            spot, strike, r, q, sigma, tau, AsianOptionType.CALL
        )
        put_vanilla = AsianPricer._vanilla_european_price(
            spot, strike, r, q, sigma, tau, AsianOptionType.PUT
        )

        # Opção Asiática Geométrica
        call_g, _, eff_vol_g, _, _, _, _ = AsianPricer.price_geometric_kemna_vorst(
            spot, strike, r, q, sigma, tau, times, AsianOptionType.CALL
        )
        put_g, _, _, _, _, _, _ = AsianPricer.price_geometric_kemna_vorst(
            spot, strike, r, q, sigma, tau, times, AsianOptionType.PUT
        )

        # Opção Asiática Aritmética
        call_a, _, eff_vol_a, _, _, _, _ = AsianPricer.price_arithmetic_turnbull_wakeman(
            spot, strike, r, q, sigma, tau, times, AsianOptionType.CALL
        )
        put_a, _, _, _, _, _, _ = AsianPricer.price_arithmetic_turnbull_wakeman(
            spot, strike, r, q, sigma, tau, times, AsianOptionType.PUT
        )

        # 1. Menor Volatilidade Efetiva: Vol_Asian <= Vol_Vanilla
        # Teoricamente: sigma_eff aprox sigma / sqrt(3) aprox 0.577 * 0.25 = 0.144
        self.assertLess(eff_vol_g, sigma)
        self.assertLess(eff_vol_a, sigma)
        self.assertAlmostEqual(eff_vol_g / sigma, 1.0 / math.sqrt(3.0), delta=0.03)

        assert_asian_effective_volatility_bound(
            effective_volatility=Decimal(str(eff_vol_a)),
            vanilla_volatility=Decimal(str(sigma)),
            contract_id="TEST-LAW-096-VOL",
        )

        # 2. Dominância de Preço ATM: Prêmio Asiático <= Prêmio Vanilla
        self.assertLess(call_g, call_vanilla)
        self.assertLess(put_g, put_vanilla)
        self.assertLess(call_a, call_vanilla)
        self.assertLess(put_a, put_vanilla)

    def test_in_progress_asian_option_with_past_observations(self) -> None:
        """Garante a precificação correta de opções em andamento (in-progress) com observações passadas."""
        spot = 105.0
        strike = 100.0
        r = 0.10
        q = 0.00
        sigma = 0.20
        tau_total = 1.0
        N = 12
        m = 6  # 6 observações já realizadas
        past_prices = [98.0, 99.0, 100.0, 102.0, 104.0, 105.0]
        future_times = [(i - m) * (tau_total / N) for i in range(m + 1, N + 1)]
        all_times = [0.0] * m + future_times

        # Precificação Aritmética in-progress
        call_p, exp_avg, eff_vol, d1, d2, _, p_diff = AsianPricer.price_arithmetic_turnbull_wakeman(
            spot=spot,
            strike=strike,
            r=r,
            q=q,
            sigma=sigma,
            tau_to_expiration=future_times[-1],
            observation_times=all_times,
            option_type=AsianOptionType.CALL,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            past_prices=past_prices,
        )
        put_p, _, _, _, _, _, _ = AsianPricer.price_arithmetic_turnbull_wakeman(
            spot=spot,
            strike=strike,
            r=r,
            q=q,
            sigma=sigma,
            tau_to_expiration=future_times[-1],
            observation_times=all_times,
            option_type=AsianOptionType.PUT,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            past_prices=past_prices,
        )

        # Paridade in-progress deve ser exata
        disc = math.exp(-r * future_times[-1])
        expected_parity = disc * (exp_avg - strike)
        self.assertAlmostEqual(call_p - put_p, expected_parity, places=6)
        self.assertLess(p_diff, 1e-5)

        # Call deve ser positiva e maior que Put pois spot subiu
        self.assertGreater(call_p, put_p)

    def test_deeply_itm_in_progress_call_boundary_condition(self) -> None:
        """Garante tratamento quando K* <= 0 (call deterministicamente exercível)."""
        spot = 120.0
        strike = 50.0  # Strike muito baixo
        r = 0.05
        q = 0.0
        sigma = 0.15
        times = [0.0, 0.0, 0.5]
        past = [110.0, 115.0]

        call_itm, exp_avg, _, _, _, _, p_diff = AsianPricer.price_arithmetic_turnbull_wakeman(
            spot=spot,
            strike=strike,
            r=r,
            q=q,
            sigma=sigma,
            tau_to_expiration=0.5,
            observation_times=times,
            option_type=AsianOptionType.CALL,
            past_prices=past,
        )
        put_itm, _, _, _, _, _, _ = AsianPricer.price_arithmetic_turnbull_wakeman(
            spot=spot,
            strike=strike,
            r=r,
            q=q,
            sigma=sigma,
            tau_to_expiration=0.5,
            observation_times=times,
            option_type=AsianOptionType.PUT,
            past_prices=past,
        )
        self.assertEqual(put_itm, 0.0)
        self.assertAlmostEqual(call_itm, math.exp(-r * 0.5) * (exp_avg - strike), places=6)


class TestGreeksCalculations(unittest.TestCase):
    """Testes para o cálculo e consistência matemática das Gregas Analíticas."""

    def test_analytical_and_numerical_greeks_coherence(self) -> None:
        """Garante que Delta e Gamma concordem com aproximação numérica estrita."""
        spot = 5.45
        strike = 5.45
        r = 0.1075
        q = 0.045
        sigma = 0.16
        tau = 0.25
        times = [i * (tau / 10) for i in range(1, 11)]

        greeks_call = AsianPricer.compute_analytical_greeks(
            spot=spot,
            strike=strike,
            r=r,
            q=q,
            sigma=sigma,
            tau_to_expiration=tau,
            observation_times=times,
            option_type=AsianOptionType.CALL,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            average_type=AsianAverageType.ARITHMETIC,
        )
        greeks_put = AsianPricer.compute_analytical_greeks(
            spot=spot,
            strike=strike,
            r=r,
            q=q,
            sigma=sigma,
            tau_to_expiration=tau,
            observation_times=times,
            option_type=AsianOptionType.PUT,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            average_type=AsianAverageType.ARITHMETIC,
        )

        # Delta de Call deve estar em [0, 1] e de Put em [-1, 0]
        self.assertGreater(greeks_call.delta, Decimal("0.00"))
        self.assertLess(greeks_call.delta, Decimal("1.00"))
        self.assertLess(greeks_put.delta, Decimal("0.00"))
        self.assertGreater(greeks_put.delta, Decimal("-1.00"))

        # Convexidade (Gamma) deve ser estritamente positiva para opções vanilla/asiáticas regulares
        self.assertGreater(greeks_call.gamma, Decimal("0.00"))
        self.assertGreater(greeks_put.gamma, Decimal("0.00"))
        self.assertAlmostEqual(float(greeks_call.gamma), float(greeks_put.gamma), places=4)

        # Vega deve ser estritamente positivo
        self.assertGreater(greeks_call.vega, Decimal("0.00"))
        self.assertEqual(greeks_call.vega_1pct, round_greek(greeks_call.vega * Decimal("0.01")))

        # Decaimento temporal: Theta diário = Theta anual / 252
        self.assertEqual(
            greeks_call.theta_daily,
            round_greek(greeks_call.theta_annual / Decimal("252")),
        )


class TestSettlementAndZeroSumConservation(unittest.TestCase):
    """Testes para liquidação exclusivamente financeira e invariante soma-zero (LAW-097)."""

    def setUp(self) -> None:
        self.engine = AsianExoticOptionsEngine()

    def test_itm_call_financial_settlement_and_law_097(self) -> None:
        """Verifica liquidação de Call ITM com conservação soma-zero bilateral e retenção de IRRF."""
        obs = [
            ObservationPoint(observation_date=date(2026, 10, 1), observed_price=Decimal("5.40")),
            ObservationPoint(observation_date=date(2026, 10, 2), observed_price=Decimal("5.50")),
            ObservationPoint(observation_date=date(2026, 10, 3), observed_price=Decimal("5.60")),
        ]
        # Média = 5.50. Strike = 5.40. Intrinsic = 0.10.
        contract = AsianOptionContract(
            contract_id="SETTLE-ITM-CALL-01",
            nome_protocol="NOME-SETTLE-001",
            underlying_type=AsianUnderlyingType.USD_PTAX,
            option_type=AsianOptionType.CALL,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            average_type=AsianAverageType.ARITHMETIC,
            sampling_frequency=AsianSamplingFrequency.DAILY,
            trade_date=date(2026, 10, 1),
            start_averaging_date=date(2026, 10, 1),
            expiration_date=date(2026, 10, 3),
            settlement_date=date(2026, 10, 4),
            strike_price=Decimal("5.4000"),
            quantity=Decimal("100000.00"),  # USD 100.000
            contract_multiplier=Decimal("1.00"),
            spot_initial=Decimal("5.4000"),
            risk_free_rate=Decimal("0.1075"),
            volatility=Decimal("0.15"),
            observations=obs,
            holder_irrf_rate=Decimal("0.1500"),
            holder_irrf_exempt=False,
            premium_agreed=Decimal("0.0200"),  # Prêmio pago de R$ 2.000,00
        )
        self.engine.register_contract(contract)

        record = self.engine.settle_contract(contract.contract_id)

        # Ganho Bruto: 100.000 * 0.10 = R$ 10.000,00
        self.assertEqual(record.gross_holder_amount, Decimal("10000.00"))
        # Lançador deve ser debitado no valor exato com sinal invertido
        self.assertEqual(record.gross_writer_amount, Decimal("-10000.00"))

        # Base IRRF = 10.000 - (100.000 * 0.02) = 10.000 - 2.000 = 8.000.
        # IRRF (15%) = 8.000 * 0.15 = R$ 1.200,00
        self.assertEqual(record.irrf_amount, Decimal("1200.00"))
        self.assertEqual(record.net_holder_amount, Decimal("8800.00"))
        self.assertEqual(record.net_writer_amount, Decimal("-10000.00"))

        # Auditoria formal LAW-097
        self.assertTrue(record.is_zero_sum)
        assert_asian_zero_sum_settlement(
            gross_holder_amount=record.gross_holder_amount,
            gross_writer_amount=record.gross_writer_amount,
            contract_id=record.contract_id,
        )

    def test_otm_option_zero_payoffs(self) -> None:
        """Garante que opção OTM encerre com payoff 0,00 e conservação soma-zero intacta."""
        obs = [
            ObservationPoint(observation_date=date(2026, 10, 1), observed_price=Decimal("5.30")),
            ObservationPoint(observation_date=date(2026, 10, 2), observed_price=Decimal("5.32")),
        ]
        # Média = 5.31. Strike Call = 5.40 -> OTM
        contract = AsianOptionContract(
            contract_id="SETTLE-OTM-01",
            nome_protocol="NOME-SETTLE-002",
            underlying_type=AsianUnderlyingType.USD_PTAX,
            option_type=AsianOptionType.CALL,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            average_type=AsianAverageType.ARITHMETIC,
            sampling_frequency=AsianSamplingFrequency.DAILY,
            trade_date=date(2026, 10, 1),
            start_averaging_date=date(2026, 10, 1),
            expiration_date=date(2026, 10, 2),
            settlement_date=date(2026, 10, 3),
            strike_price=Decimal("5.4000"),
            quantity=Decimal("50000.00"),
            contract_multiplier=Decimal("1.00"),
            spot_initial=Decimal("5.3500"),
            risk_free_rate=Decimal("0.1075"),
            volatility=Decimal("0.15"),
            observations=obs,
        )
        self.engine.register_contract(contract)
        record = self.engine.settle_contract(contract.contract_id)

        self.assertFalse(record.is_itm)
        self.assertEqual(record.gross_holder_amount, Decimal("0.00"))
        self.assertEqual(record.gross_writer_amount, Decimal("0.00"))
        self.assertEqual(record.irrf_amount, Decimal("0.00"))
        self.assertTrue(record.is_zero_sum)

    def test_floating_strike_commodity_settlement(self) -> None:
        """Verifica liquidação de Floating Strike em contrato de Milho (CCM)."""
        obs = [
            ObservationPoint(observation_date=date(2026, 10, 9), observed_price=Decimal("65.00")),
            ObservationPoint(observation_date=date(2026, 10, 16), observed_price=Decimal("66.00")),
            ObservationPoint(observation_date=date(2026, 10, 23), observed_price=Decimal("67.00")),
            ObservationPoint(observation_date=date(2026, 10, 30), observed_price=Decimal("68.00")),
        ]
        # Média Aritmética = 66.50. Spot Final (S_T) = 68.00.
        # Call Flutuante: Payoff = max(S_T - A, 0) = max(68.00 - 66.50, 0) = 1.50 R$/saca
        contract = AsianOptionContract(
            contract_id="SETTLE-FLOAT-CCM",
            nome_protocol="NOME-CCM-001",
            underlying_type=AsianUnderlyingType.CORN,
            option_type=AsianOptionType.CALL,
            strike_type=AsianStrikeType.FLOATING_STRIKE,
            average_type=AsianAverageType.ARITHMETIC,
            sampling_frequency=AsianSamplingFrequency.WEEKLY,
            trade_date=date(2026, 10, 2),
            start_averaging_date=date(2026, 10, 9),
            expiration_date=date(2026, 10, 30),
            settlement_date=date(2026, 11, 3),
            strike_price=Decimal("0.00"),
            quantity=Decimal("10.00"),
            contract_multiplier=Decimal("450.00"),  # 4.500 sacas
            spot_initial=Decimal("65.00"),
            risk_free_rate=Decimal("0.1075"),
            volatility=Decimal("0.22"),
            observations=obs,
            holder_irrf_exempt=True,
        )
        self.engine.register_contract(contract)
        record = self.engine.settle_contract(contract.contract_id)

        self.assertTrue(record.is_itm)
        self.assertEqual(record.intrinsic_per_unit, Decimal("1.5000"))
        # Volume = 10 contratos * 450 sacas * R$ 1,50 = R$ 6.750,00
        self.assertEqual(record.gross_holder_amount, Decimal("6750.00"))
        self.assertEqual(record.gross_writer_amount, Decimal("-6750.00"))
        self.assertEqual(record.irrf_amount, Decimal("0.00"))  # isento
        self.assertEqual(record.net_holder_amount, Decimal("6750.00"))
        self.assertTrue(record.is_zero_sum)


class TestFixtureIntegration(unittest.TestCase):
    """Testes de integração ponta a ponta com o arquivo de fixture oficial."""

    def setUp(self) -> None:
        self.fixture_path = (
            Path(__file__).parent.parent / "fixtures" / "asian_exotic_options.json"
        )
        with open(self.fixture_path, "r", encoding="utf-8") as f:
            self.fixture_data = json.load(f)
        self.engine = AsianExoticOptionsEngine()

    def test_fixture_loading_and_pricing(self) -> None:
        """Carrega todos os contratos da fixture, precifica e valida invariantes."""
        contracts_data = self.fixture_data["contracts"]
        self.assertGreaterEqual(len(contracts_data), 6)

        for c_data in contracts_data:
            contract = AsianOptionContract.from_dict(c_data)
            self.engine.register_contract(contract)

            # Precifica como na data de início do contrato
            pricing_res = self.engine.price_contract(
                contract.contract_id,
                valuation_date=contract.trade_date,
            )

            # Invariante LAW-095: Paridade Asiática
            self.assertTrue(pricing_res.parity_holds)
            # Invariante LAW-096: Menor Volatilidade Efetiva
            self.assertTrue(pricing_res.effective_vol_bound_holds)
            self.assertLessEqual(
                pricing_res.effective_volatility,
                pricing_res.vanilla_volatility + Decimal("0.0001"),
            )
            # Gregas consistentes
            self.assertIsNotNone(pricing_res.greeks.delta)
            self.assertGreaterEqual(pricing_res.greeks.gamma, Decimal("0.00"))

    def test_fixture_settlement_and_portfolio_zero_sum(self) -> None:
        """Liquida todos os contratos da fixture e valida o balanço multilateral soma-zero."""
        for c_data in self.fixture_data["contracts"]:
            contract = AsianOptionContract.from_dict(c_data)
            self.engine.register_contract(contract)

        settlements = self.engine.settle_all()
        self.assertEqual(len(settlements), len(self.fixture_data["contracts"]))

        # Cada contrato deve ser soma-zero
        for s in settlements:
            self.assertTrue(s.is_zero_sum)
            s.verify_invariants()

        # Sumário consolidado do portfólio NoMe
        summary = self.engine.summarize_portfolio()
        summary.verify_invariants()
        self.assertTrue(summary.all_zero_sum)
        self.assertEqual(
            summary.total_holder_gross + summary.total_writer_gross,
            Decimal("0.00"),
        )


class TestEngineEdgeCasesAndErrorHandling(unittest.TestCase):
    """Testes de robustez, tratamento de erros e casos extremos."""

    def setUp(self) -> None:
        self.engine = AsianExoticOptionsEngine()

    def test_duplicate_contract_registration_raises_error(self) -> None:
        """Garante que tentativa de registrar contrato com mesmo ID levante erro."""
        c = AsianOptionContract(
            contract_id="DUP-01",
            nome_protocol="PROTO-DUP",
            underlying_type=AsianUnderlyingType.USD_PTAX,
            option_type=AsianOptionType.CALL,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            average_type=AsianAverageType.ARITHMETIC,
            sampling_frequency=AsianSamplingFrequency.DAILY,
            trade_date=date(2026, 10, 1),
            start_averaging_date=date(2026, 10, 1),
            expiration_date=date(2026, 10, 30),
            settlement_date=date(2026, 11, 3),
            strike_price=Decimal("5.40"),
            quantity=Decimal("1000.0"),
            contract_multiplier=Decimal("1.0"),
            spot_initial=Decimal("5.40"),
            risk_free_rate=Decimal("0.10"),
            volatility=Decimal("0.15"),
            observations=[ObservationPoint(observation_date=date(2026, 10, 30))],
        )
        self.engine.register_contract(c)
        with self.assertRaises(AsianValidationError):
            self.engine.register_contract(c)

    def test_contract_not_found_raises_error(self) -> None:
        """Verifica exceção ao buscar contrato inexistente."""
        with self.assertRaises(AsianValidationError):
            self.engine.get_contract("NON-EXISTENT")

    def test_settle_with_unrealized_observations_raises_error(self) -> None:
        """Garante que liquidação falhe se houver observações pendentes sem cotação."""
        c = AsianOptionContract(
            contract_id="PENDING-01",
            nome_protocol="PROTO-PEND",
            underlying_type=AsianUnderlyingType.USD_PTAX,
            option_type=AsianOptionType.CALL,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            average_type=AsianAverageType.ARITHMETIC,
            sampling_frequency=AsianSamplingFrequency.DAILY,
            trade_date=date(2026, 10, 1),
            start_averaging_date=date(2026, 10, 1),
            expiration_date=date(2026, 10, 30),
            settlement_date=date(2026, 11, 3),
            strike_price=Decimal("5.40"),
            quantity=Decimal("1000.0"),
            contract_multiplier=Decimal("1.0"),
            spot_initial=Decimal("5.40"),
            risk_free_rate=Decimal("0.10"),
            volatility=Decimal("0.15"),
            observations=[
                ObservationPoint(observation_date=date(2026, 10, 15), observed_price=Decimal("5.42")),
                ObservationPoint(observation_date=date(2026, 10, 30), observed_price=None),  # pendente!
            ],
        )
        self.engine.register_contract(c)
        with self.assertRaises(AsianSettlementError):
            self.engine.settle_contract(c.contract_id)

    def test_batch_add_observations_updates_status(self) -> None:
        """Garante que inserção em lote atualize o status para MATURED."""
        c = AsianOptionContract(
            contract_id="BATCH-01",
            nome_protocol="PROTO-BATCH",
            underlying_type=AsianUnderlyingType.USD_PTAX,
            option_type=AsianOptionType.CALL,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            average_type=AsianAverageType.ARITHMETIC,
            sampling_frequency=AsianSamplingFrequency.DAILY,
            trade_date=date(2026, 10, 1),
            start_averaging_date=date(2026, 10, 1),
            expiration_date=date(2026, 10, 30),
            settlement_date=date(2026, 11, 3),
            strike_price=Decimal("5.40"),
            quantity=Decimal("1000.0"),
            contract_multiplier=Decimal("1.0"),
            spot_initial=Decimal("5.40"),
            risk_free_rate=Decimal("0.10"),
            volatility=Decimal("0.15"),
            observations=[
                ObservationPoint(observation_date=date(2026, 10, 15)),
                ObservationPoint(observation_date=date(2026, 10, 30)),
            ],
        )
        self.engine.register_contract(c)
        self.assertEqual(c.status, AsianContractStatus.ACTIVE)

        # Adiciona 1 observação -> IN_AVERAGING
        self.engine.add_market_observation(c.contract_id, date(2026, 10, 15), Decimal("5.43"))
        self.assertEqual(c.status, AsianContractStatus.IN_AVERAGING)

        # Adiciona a última -> MATURED
        self.engine.add_market_observation(c.contract_id, date(2026, 10, 30), Decimal("5.47"))
        self.assertEqual(c.status, AsianContractStatus.MATURED)

    def test_curran_vs_turnbull_wakeman_convergence(self) -> None:
        """Verifica a proximidade entre o modelo Turnbull-Wakeman e Curran para opções aritméticas."""
        spot = 100.0
        strike = 100.0
        r = 0.10
        q = 0.02
        sigma = 0.20
        tau = 0.5
        times = [i * (tau / 6) for i in range(1, 7)]

        call_tw, _, _, _, _, _, _ = AsianPricer.price_arithmetic_turnbull_wakeman(
            spot, strike, r, q, sigma, tau, times, AsianOptionType.CALL
        )
        call_curran, _, _, _, _, _, _ = AsianPricer.price_arithmetic_curran(
            spot, strike, r, q, sigma, tau, times, AsianOptionType.CALL
        )

        # Curran e Turnbull-Wakeman devem diferir por menos de 1% para ATM
        rel_diff = abs(call_tw - call_curran) / call_tw
        self.assertLess(rel_diff, 0.01)

    def test_schedule_frequencies_biweekly_monthly_custom(self) -> None:
        """Verifica a geração de cronogramas quinzenais, mensais e customizados."""
        # Biweekly
        sched_bi = AsianExoticOptionsEngine.generate_observation_schedule(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 1),
            frequency=AsianSamplingFrequency.BIWEEKLY,
        )
        self.assertGreater(len(sched_bi), 3)

        # Monthly
        sched_mo = AsianExoticOptionsEngine.generate_observation_schedule(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 6, 1),
            frequency=AsianSamplingFrequency.MONTHLY,
        )
        self.assertGreaterEqual(len(sched_mo), 5)

        # Custom
        sched_cu = AsianExoticOptionsEngine.generate_observation_schedule(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            frequency=AsianSamplingFrequency.CUSTOM,
        )
        self.assertEqual(len(sched_cu), 1)

    def test_greeks_for_commodities(self) -> None:
        """Garante a avaliação de gregas para Café e Soja."""
        times = [0.1, 0.2, 0.3, 0.4, 0.5]
        greeks_coffee = AsianPricer.compute_analytical_greeks(
            spot=250.0,
            strike=245.0,
            r=0.0525,
            q=0.01,
            sigma=0.24,
            tau_to_expiration=0.5,
            observation_times=times,
            option_type=AsianOptionType.CALL,
            strike_type=AsianStrikeType.FIXED_STRIKE,
            average_type=AsianAverageType.ARITHMETIC,
        )
        self.assertGreater(greeks_coffee.delta, Decimal("0.50"))  # ITM Call
        self.assertGreater(greeks_coffee.vega, Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
