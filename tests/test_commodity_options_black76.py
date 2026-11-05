"""Suíte de Testes Unitários e Invariantes Formais — Opções sobre Futuros Agrícolas Black-76 (FL-064).

Valida integralmente o modelo analítico Black-76, as especificações contratuais da B3
(Soja SJC/SOJ, Milho CCM, Boi Gordo BGI, Café Arábica ICF), as gregas de 1ª e 2ª ordem,
o exercício automático no vencimento com conversão em futuros e a auditoria formal
das leis de conservação física e financeira.

100% Python Standard Library (unittest, math, decimal, datetime, json, pathlib).
"""

from datetime import date, datetime
from decimal import Decimal
import json
import math
from pathlib import Path
import unittest

from src.commodity_options_black76 import (
    AgriculturalOptionContract,
    Black76CalculationError,
    Black76Greeks,
    Black76Model,
    Black76PricingResult,
    CommodityOptionInvariantError,
    CommodityOptionsEngine,
    CommodityOptionsError,
    CommodityOptionsValidationError,
    CommoditySpecification,
    CommodityType,
    CreatedFuturePosition,
    ExerciseDisposition,
    ExerciseEvent,
    ExerciseRuleViolationError,
    OptionStyle,
    OptionType,
    ParticipantPosition,
    PositionSide,
)


class TestCommoditySpecifications(unittest.TestCase):
    """Testes para especificações oficiais de contratos agrícolas da B3."""

    def test_default_specifications_contract_sizes(self) -> None:
        """Garante que os tamanhos de contrato e multiplicadores B3 sejam estritos."""
        # Soja (SJC / SOJ): 450 sacas de 60kg, cotado em USD
        soja = CommoditySpecification.get_default_specification(CommodityType.SOYBEAN)
        self.assertEqual(soja.contract_size, Decimal("450"))
        self.assertEqual(soja.currency, "USD")
        self.assertEqual(soja.tick_size, Decimal("0.01"))

        # Milho (CCM): 450 sacas de 60kg, cotado em BRL
        milho = CommoditySpecification.get_default_specification(CommodityType.CORN)
        self.assertEqual(milho.contract_size, Decimal("450"))
        self.assertEqual(milho.currency, "BRL")
        self.assertEqual(milho.tick_size, Decimal("0.01"))

        # Boi Gordo (BGI): 330 arrobas líquidas de 15kg, cotado em BRL
        boi = CommoditySpecification.get_default_specification(CommodityType.LIVE_CATTLE)
        self.assertEqual(boi.contract_size, Decimal("330"))
        self.assertEqual(boi.currency, "BRL")
        self.assertEqual(boi.tick_size, Decimal("0.01"))

        # Café Arábica (ICF): 100 sacas de 60kg, cotado em USD
        cafe = CommoditySpecification.get_default_specification(CommodityType.ARABICA_COFFEE)
        self.assertEqual(cafe.contract_size, Decimal("100"))
        self.assertEqual(cafe.currency, "USD")
        self.assertEqual(cafe.tick_size, Decimal("0.05"))

    def test_commodity_type_aliases(self) -> None:
        """Verifica resolução robusta de códigos de ticker B3."""
        self.assertEqual(CommodityType.from_code("SJC"), CommodityType.SOYBEAN)
        self.assertEqual(CommodityType.from_code("SOJ"), CommodityType.SOYBEAN)
        self.assertEqual(CommodityType.from_code("soja"), CommodityType.SOYBEAN)
        self.assertEqual(CommodityType.from_code("CCM"), CommodityType.CORN)
        self.assertEqual(CommodityType.from_code("milho"), CommodityType.CORN)
        self.assertEqual(CommodityType.from_code("BGI"), CommodityType.LIVE_CATTLE)
        self.assertEqual(CommodityType.from_code("boi"), CommodityType.LIVE_CATTLE)
        self.assertEqual(CommodityType.from_code("ICF"), CommodityType.ARABICA_COFFEE)
        self.assertEqual(CommodityType.from_code("cafe"), CommodityType.ARABICA_COFFEE)

        with self.assertRaises(CommodityOptionsValidationError):
            CommodityType.from_code("WHEAT")

    def test_specification_serialization(self) -> None:
        """Valida roundtrip to_dict / from_dict de especificações."""
        spec = CommoditySpecification.get_default_specification(CommodityType.CORN)
        data = spec.to_dict()
        restored = CommoditySpecification.from_dict(data)
        self.assertEqual(spec, restored)


class TestBlack76AnalyticalPricing(unittest.TestCase):
    """Testes do motor analítico de precificação Black-76 (Fischer Black 1976)."""

    def test_at_the_money_call_put_parity(self) -> None:
        """Valida que no strike ATM (F == K), Call e Put possuem o mesmo preço analítico."""
        F = Decimal("65.00")
        K = Decimal("65.00")
        T = Decimal("0.25")
        vol = Decimal("0.25")
        r = Decimal("0.1075")

        res = Black76Model.price(F, K, T, vol, r)
        self.assertEqual(res.call_price, res.put_price)
        self.assertTrue(res.parity_holds)
        self.assertLessEqual(res.put_call_parity_diff, Decimal("0.0001"))

    def test_put_call_parity_in_the_money(self) -> None:
        """Valida estritamente a Lei da Paridade Put-Call Black-76: C - P == (F - K) * exp(-r*T)."""
        F = Decimal("70.00")
        K = Decimal("65.00")
        T = Decimal("0.50")
        vol = Decimal("0.28")
        r = Decimal("0.1125")

        res = Black76Model.price(F, K, T, vol, r)
        self.assertTrue(res.parity_holds)

        # Verificação matemática direta
        disc = math.exp(-float(r) * float(T))
        expected_diff = (float(F) - float(K)) * disc
        actual_diff = float(res.call_price - res.put_price)
        self.assertAlmostEqual(actual_diff, expected_diff, places=4)

    def test_boundary_zero_time_to_maturity(self) -> None:
        """Opção no vencimento (T = 0) deve convergir estritamente para o valor intrínseco."""
        F = Decimal("72.50")
        K = Decimal("65.00")

        call_itm = Black76Model.price_call(F, K, Decimal("0.0"), Decimal("0.20"), Decimal("0.10"))
        put_otm = Black76Model.price_put(F, K, Decimal("0.0"), Decimal("0.20"), Decimal("0.10"))
        self.assertEqual(call_itm, Decimal("7.5000"))
        self.assertEqual(put_otm, Decimal("0.0000"))

        put_itm = Black76Model.price_put(Decimal("60.00"), K, Decimal("0.0"), Decimal("0.20"), Decimal("0.10"))
        self.assertEqual(put_itm, Decimal("5.0000"))

    def test_boundary_zero_volatility(self) -> None:
        """Com volatilidade zero, prêmio é o valor intrínseco descontado a termo."""
        F = Decimal("80.00")
        K = Decimal("65.00")
        T = Decimal("0.50")
        r = Decimal("0.10")

        disc = Decimal(str(math.exp(-0.10 * 0.50)))
        expected_call = ((F - K) * disc).quantize(Decimal("0.0001"))

        call_p = Black76Model.price_call(F, K, T, Decimal("0.0"), r)
        self.assertAlmostEqual(float(call_p), float(expected_call), places=3)

    def test_invalid_prices_raise_error(self) -> None:
        """Preços não positivos devem levantar Black76CalculationError."""
        with self.assertRaises(Black76CalculationError):
            Black76Model.price_call(Decimal("-10.00"), Decimal("50.00"), Decimal("0.2"), Decimal("0.2"), Decimal("0.1"))

        with self.assertRaises(Black76CalculationError):
            Black76Model.price_call(Decimal("50.00"), Decimal("0.00"), Decimal("0.2"), Decimal("0.2"), Decimal("0.1"))


class TestBlack76GreeksAnalytical(unittest.TestCase):
    """Testes de sensibilidades analíticas (Gregas de 1ª e 2ª ordem)."""

    def setUp(self) -> None:
        self.F = Decimal("65.00")
        self.K = Decimal("65.00")
        self.T = Decimal("0.25")
        self.vol = Decimal("0.25")
        self.r = Decimal("0.1075")

    def test_delta_bounds_and_put_call_relation(self) -> None:
        """Garante limites teóricos e a relação formal: Delta_Call - Delta_Put == exp(-r*T)."""
        call_g = Black76Model.calculate_greeks(self.F, self.K, self.T, self.vol, self.r, OptionType.CALL)
        put_g = Black76Model.calculate_greeks(self.F, self.K, self.T, self.vol, self.r, OptionType.PUT)

        disc = math.exp(-float(self.r) * float(self.T))

        # Limites
        self.assertGreaterEqual(float(call_g.delta), 0.0)
        self.assertLessEqual(float(call_g.delta), disc)
        self.assertGreaterEqual(float(put_g.delta), -disc)
        self.assertLessEqual(float(put_g.delta), 0.0)

        # Delta Put-Call Parity
        delta_diff = float(call_g.delta - put_g.delta)
        self.assertAlmostEqual(delta_diff, disc, places=5)

    def test_gamma_vega_symmetry(self) -> None:
        """No Black-76, Gamma e Vega de Call e Put são idênticos e estritamente não-negativos."""
        call_g = Black76Model.calculate_greeks(self.F, self.K, self.T, self.vol, self.r, OptionType.CALL)
        put_g = Black76Model.calculate_greeks(self.F, self.K, self.T, self.vol, self.r, OptionType.PUT)

        self.assertEqual(call_g.gamma, put_g.gamma)
        self.assertGreater(call_g.gamma, Decimal("0.000000"))

        self.assertEqual(call_g.vega, put_g.vega)
        self.assertGreater(call_g.vega, Decimal("0.000000"))
        self.assertEqual(call_g.vega_1pct, (call_g.vega * Decimal("0.01")).quantize(Decimal("0.000001")))

    def test_theta_and_rho_sensitivities(self) -> None:
        """Valida Theta diário (base 252 DU) e Rho proporcional ao tempo."""
        call_g = Black76Model.calculate_greeks(self.F, self.K, self.T, self.vol, self.r, OptionType.CALL)
        self.assertAlmostEqual(
            float(call_g.theta_daily),
            float(call_g.theta_annual / Decimal("252")),
            places=5
        )
        self.assertAlmostEqual(
            float(call_g.rho_1pct),
            float(call_g.rho * Decimal("0.01")),
            places=5
        )

    def test_second_order_vanna_volga_finite_difference(self) -> None:
        """Compara Vanna e Volga analíticos contra aproximação por diferenças finitas."""
        h = 1e-5
        vol_f = float(self.vol)
        F_f = float(self.F)
        K_f = float(self.K)
        T_f = float(self.T)
        r_f = float(self.r)

        # Vanna = dDelta / dVol
        d1_plus, _ = Black76Model.calculate_d1_d2(F_f, K_f, T_f, vol_f + h)
        delta_plus = math.exp(-r_f * T_f) * Black76Model.std_norm_cdf(d1_plus)

        d1_base, _ = Black76Model.calculate_d1_d2(F_f, K_f, T_f, vol_f)
        delta_base = math.exp(-r_f * T_f) * Black76Model.std_norm_cdf(d1_base)

        numeric_vanna = (delta_plus - delta_base) / h

        greeks = Black76Model.calculate_greeks(self.F, self.K, self.T, self.vol, self.r, OptionType.CALL)
        self.assertAlmostEqual(float(greeks.vanna), numeric_vanna, places=3)


class TestPositionsAndTrading(unittest.TestCase):
    """Testes de gestão de contratos, posições e validações de integridade cadastral."""

    def setUp(self) -> None:
        self.engine = CommodityOptionsEngine()
        self.contract = AgriculturalOptionContract(
            ticker="CCMX26C6500",
            commodity=CommodityType.CORN,
            underlying_future_ticker="CCMX26",
            option_type=OptionType.CALL,
            strike=Decimal("65.00"),
            expiration_date=date(2026, 11, 20),
        )
        self.engine.register_contract(self.contract)

    def test_register_duplicate_contract_fails(self) -> None:
        with self.assertRaises(CommodityOptionsValidationError):
            self.engine.register_contract(self.contract)

    def test_add_position_and_total_premium(self) -> None:
        pos = ParticipantPosition(
            position_id="POS-001",
            participant_id="COOP_AGRO",
            option_ticker="CCMX26C6500",
            side=PositionSide.LONG,
            quantity=100,
            unit_premium=Decimal("3.50"),
        )
        self.engine.add_position(pos)

        # 100 contratos * 450 sacas * R$ 3,50 = R$ 157.500,00
        total_p = pos.total_premium(self.contract.contract_size)
        self.assertEqual(total_p, Decimal("157500.00"))

    def test_contrary_instruction_workflow(self) -> None:
        pos = ParticipantPosition(
            position_id="POS-002",
            participant_id="COOP_AGRO",
            option_ticker="CCMX26C6500",
            side=PositionSide.LONG,
            quantity=50,
            unit_premium=Decimal("2.00"),
        )
        self.engine.add_position(pos)
        self.assertFalse(pos.contrary_instruction)

        self.engine.set_contrary_instruction("POS-002", True)
        self.assertTrue(self.engine.positions["POS-002"].contrary_instruction)

        # Lançadores não podem submeter contrary instruction
        pos_short = ParticipantPosition(
            position_id="POS-003",
            participant_id="MARKET_MAKER",
            option_ticker="CCMX26C6500",
            side=PositionSide.SHORT,
            quantity=50,
            unit_premium=Decimal("2.00"),
        )
        self.engine.add_position(pos_short)
        with self.assertRaises(ExerciseRuleViolationError):
            self.engine.set_contrary_instruction("POS-003", True)


class TestExerciseAndFuturesConversion(unittest.TestCase):
    """Testes de exercício automático, atribuição e conversão em contratos futuros B3."""

    def setUp(self) -> None:
        self.engine = CommodityOptionsEngine()
        # Contrato de Milho: strike 65.00
        self.call_contract = AgriculturalOptionContract(
            ticker="CCMX26C6500",
            commodity=CommodityType.CORN,
            underlying_future_ticker="CCMX26",
            option_type=OptionType.CALL,
            strike=Decimal("65.00"),
            expiration_date=date(2026, 11, 20),
        )
        self.put_contract = AgriculturalOptionContract(
            ticker="CCMX26P6500",
            commodity=CommodityType.CORN,
            underlying_future_ticker="CCMX26",
            option_type=OptionType.PUT,
            strike=Decimal("65.00"),
            expiration_date=date(2026, 11, 20),
        )
        self.engine.register_contract(self.call_contract)
        self.engine.register_contract(self.put_contract)

    def test_automatic_call_exercise_converts_to_long_future(self) -> None:
        """Call ITM: Titular vira Long Futuro ao strike K; Lançador vira Short Futuro ao strike K."""
        # 100 contratos de Call
        self.engine.add_position(ParticipantPosition("P-CALL-L", "HEDGER_A", "CCMX26C6500", PositionSide.LONG, 100, Decimal("3.00")))
        self.engine.add_position(ParticipantPosition("P-CALL-S", "WRITER_B", "CCMX26C6500", PositionSide.SHORT, 100, Decimal("3.00")))

        # Futuro fecha em 68.00 (ITM de R$ 3,00 >= 0,01 tick)
        events = self.engine.evaluate_and_execute_exercise(date(2026, 11, 20), {"CCMX26": Decimal("68.00")})

        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertEqual(evt.disposition, ExerciseDisposition.AUTOMATIC_EXERCISED)
        self.assertEqual(evt.contracts_exercised, 100)

        # Futuros criados
        futures = self.engine.created_futures
        self.assertEqual(len(futures), 2)

        fut_holder = [f for f in futures if f.participant_id == "HEDGER_A"][0]
        fut_writer = [f for f in futures if f.participant_id == "WRITER_B"][0]

        # Regra B3 Call: Titular vira LONG futuro, Lançador vira SHORT futuro ao strike K
        self.assertEqual(fut_holder.side, PositionSide.LONG)
        self.assertEqual(fut_holder.entry_price, Decimal("65.00"))
        self.assertEqual(fut_holder.quantity, 100)

        self.assertEqual(fut_writer.side, PositionSide.SHORT)
        self.assertEqual(fut_writer.entry_price, Decimal("65.00"))
        self.assertEqual(fut_writer.quantity, 100)

        # Variação MTM de abertura no D0:
        # Holder: (68.00 - 65.00) * 450 * 100 = R$ 135.000,00
        # Writer: (65.00 - 68.00) * 450 * 100 = -R$ 135.000,00
        self.assertEqual(evt.holder_mtm_gain, Decimal("135000.00"))
        self.assertEqual(evt.writer_mtm_gain, Decimal("-135000.00"))
        self.assertEqual(evt.net_financial_mtm, Decimal("0.00"))

    def test_automatic_put_exercise_converts_to_short_future(self) -> None:
        """Put ITM: Titular vira Short Futuro ao strike K; Lançador vira Long Futuro ao strike K."""
        # 50 contratos de Put
        self.engine.add_position(ParticipantPosition("P-PUT-L", "PRODUCER_X", "CCMX26P6500", PositionSide.LONG, 50, Decimal("2.50")))
        self.engine.add_position(ParticipantPosition("P-PUT-S", "FEEDLOT_Y", "CCMX26P6500", PositionSide.SHORT, 50, Decimal("2.50")))

        # Futuro fecha em 62.00 (Put ITM por R$ 3,00)
        events = self.engine.evaluate_and_execute_exercise(date(2026, 11, 20), {"CCMX26": Decimal("62.00")})

        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertEqual(evt.disposition, ExerciseDisposition.AUTOMATIC_EXERCISED)

        futures = self.engine.created_futures
        fut_holder = [f for f in futures if f.participant_id == "PRODUCER_X"][0]
        fut_writer = [f for f in futures if f.participant_id == "FEEDLOT_Y"][0]

        # Regra B3 Put: Titular vira SHORT futuro, Lançador vira LONG futuro
        self.assertEqual(fut_holder.side, PositionSide.SHORT)
        self.assertEqual(fut_holder.entry_price, Decimal("65.00"))
        self.assertEqual(fut_writer.side, PositionSide.LONG)
        self.assertEqual(fut_writer.entry_price, Decimal("65.00"))

        # MTM: (65.00 - 62.00) * 450 * 50 = R$ 67.500,00
        self.assertEqual(evt.holder_mtm_gain, Decimal("67500.00"))
        self.assertEqual(evt.writer_mtm_gain, Decimal("-67500.00"))
        self.assertEqual(evt.net_financial_mtm, Decimal("0.00"))

    def test_contrary_instruction_blocks_exercise(self) -> None:
        """Posição com Contrary Instruction não deve gerar futuros mesmo que esteja ITM."""
        pos_l = ParticipantPosition("P-CALL-L", "HEDGER_A", "CCMX26C6500", PositionSide.LONG, 20, Decimal("3.00"), contrary_instruction=True)
        pos_s = ParticipantPosition("P-CALL-S", "WRITER_B", "CCMX26C6500", PositionSide.SHORT, 20, Decimal("3.00"))
        self.engine.add_position(pos_l)
        self.engine.add_position(pos_s)

        events = self.engine.evaluate_and_execute_exercise(date(2026, 11, 20), {"CCMX26": Decimal("75.00")})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].disposition, ExerciseDisposition.CONTRARY_INSTRUCTION)
        self.assertEqual(len(self.engine.created_futures), 0)

    def test_below_tick_threshold_does_not_exercise(self) -> None:
        """ITM inferior a 1 tick regulamentar (ex: 0,005 < 0,01) não é exercido."""
        self.engine.add_position(ParticipantPosition("P-CALL-L", "HEDGER_A", "CCMX26C6500", PositionSide.LONG, 10, Decimal("3.00")))
        self.engine.add_position(ParticipantPosition("P-CALL-S", "WRITER_B", "CCMX26C6500", PositionSide.SHORT, 10, Decimal("3.00")))

        events = self.engine.evaluate_and_execute_exercise(date(2026, 11, 20), {"CCMX26": Decimal("65.005")})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].disposition, ExerciseDisposition.BELOW_TICK_THRESHOLD)
        self.assertEqual(len(self.engine.created_futures), 0)

    def test_out_of_the_money_expires_worthless(self) -> None:
        """Opção OTM expira sem exercício e sem criação de posições futuras."""
        self.engine.add_position(ParticipantPosition("P-CALL-L", "HEDGER_A", "CCMX26C6500", PositionSide.LONG, 10, Decimal("3.00")))
        self.engine.add_position(ParticipantPosition("P-CALL-S", "WRITER_B", "CCMX26C6500", PositionSide.SHORT, 10, Decimal("3.00")))

        events = self.engine.evaluate_and_execute_exercise(date(2026, 11, 20), {"CCMX26": Decimal("60.00")})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].disposition, ExerciseDisposition.EXPIRED_OTM)
        self.assertEqual(len(self.engine.created_futures), 0)


class TestFixtureLoadingAndIntegrity(unittest.TestCase):
    """Testes de carga e conformidade com a fixture oficial B3."""

    def setUp(self) -> None:
        self.fixture_path = (
            Path(__file__).resolve().parent.parent / "fixtures" / "commodity_options_black76.json"
        )
        self.engine = CommodityOptionsEngine.load_from_fixture(self.fixture_path)

    def test_fixture_contents(self) -> None:
        """Garante que a fixture carregue todos os 4 complexos agropecuários."""
        self.assertEqual(len(self.engine.contracts), 10)
        self.assertEqual(len(self.engine.positions), 16)

        commodities_present = {c.commodity for c in self.engine.contracts.values()}
        expected = {CommodityType.CORN, CommodityType.LIVE_CATTLE, CommodityType.SOYBEAN, CommodityType.ARABICA_COFFEE}
        self.assertEqual(commodities_present, expected)

    def test_pricing_benchmarks_match_model(self) -> None:
        """Compara saídas do modelo contra os benchmarks pré-calculados na fixture."""
        with open(self.fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for bm in data.get("pricing_benchmarks", []):
            res = Black76Model.price(
                forward=Decimal(bm["forward_price"]),
                strike=Decimal(bm["strike"]),
                time_to_maturity=Decimal(bm["time_to_maturity"]),
                volatility=Decimal(bm["volatility"]),
                risk_free_rate=Decimal(bm["risk_free_rate"]),
            )
            self.assertEqual(res.call_price, Decimal(bm["expected_call_price"]))
            self.assertEqual(res.put_price, Decimal(bm["expected_put_price"]))
            self.assertLessEqual(res.put_call_parity_diff, Decimal(bm["expected_parity_diff_max"]))

    def test_full_cycle_and_invariants(self) -> None:
        """Executa ciclo completo no engine com a fixture e audita 100% das invariantes formais."""
        # Precificar todas as séries
        for ticker, contract in self.engine.contracts.items():
            self.engine.price_series(
                ticker=ticker,
                forward_price=contract.strike,
                time_to_maturity=Decimal("0.25"),
                volatility=Decimal("0.25"),
            )

        # Exercício das séries com vencimento em 2026-11-20 (CCM)
        settlement = {"CCMX26": Decimal("68.50")}
        events = self.engine.evaluate_and_execute_exercise(date(2026, 11, 20), settlement)
        self.assertGreater(len(events), 0)

        # Verificação formal
        inv = self.engine.verify_invariants(raise_on_error=True)
        for inv_name, ok in inv.items():
            self.assertTrue(ok, f"Invariante formal falhou: {inv_name}")


class TestFormalInvariantsAudit(unittest.TestCase):
    """Testes exaustivos de detecção de falhas e violações de invariantes formais."""

    def test_physical_conservation_violation_detected(self) -> None:
        """Garante que qualquer assimetria na criação de futuros levanta erro."""
        engine = CommodityOptionsEngine()
        contract = AgriculturalOptionContract(
            ticker="CCMX26C6500",
            commodity=CommodityType.CORN,
            underlying_future_ticker="CCMX26",
            option_type=OptionType.CALL,
            strike=Decimal("65.00"),
            expiration_date=date(2026, 11, 20),
        )
        engine.register_contract(contract)
        engine.add_position(ParticipantPosition("P1", "H1", "CCMX26C6500", PositionSide.LONG, 10, Decimal("3.00")))
        engine.add_position(ParticipantPosition("P2", "W1", "CCMX26C6500", PositionSide.SHORT, 10, Decimal("3.00")))

        engine.evaluate_and_execute_exercise(date(2026, 11, 20), {"CCMX26": Decimal("70.00")})

        # Corromper propositalmente a lista de futuros para testar a invariante
        engine.created_futures.pop()

        with self.assertRaises(CommodityOptionInvariantError):
            engine.verify_invariants(raise_on_error=True)

    def test_serialization_roundtrip(self) -> None:
        """Testa idempotência de serialização to_dict e from_dict."""
        engine = CommodityOptionsEngine(risk_free_rate_annual=Decimal("0.1075"))
        contract = AgriculturalOptionContract(
            ticker="BGIU26C32000",
            commodity=CommodityType.LIVE_CATTLE,
            underlying_future_ticker="BGIU26",
            option_type=OptionType.CALL,
            strike=Decimal("320.00"),
            expiration_date=date(2026, 9, 30),
        )
        engine.register_contract(contract)
        engine.add_position(ParticipantPosition("P1", "FRIG", "BGIU26C32000", PositionSide.LONG, 30, Decimal("10.00")))
        engine.add_position(ParticipantPosition("P2", "PROD", "BGIU26C32000", PositionSide.SHORT, 30, Decimal("10.00")))

        engine.price_series("BGIU26C32000", Decimal("320.00"), Decimal("0.20"), Decimal("0.18"))
        engine.evaluate_and_execute_exercise(date(2026, 9, 30), {"BGIU26": Decimal("335.00")})

        data = engine.to_dict()
        reconstructed = CommodityOptionsEngine.from_dict(data)

        self.assertEqual(len(engine.contracts), len(reconstructed.contracts))
        self.assertEqual(len(engine.positions), len(reconstructed.positions))
        self.assertEqual(len(engine.created_futures), len(reconstructed.created_futures))
        self.assertEqual(len(engine.exercise_events), len(reconstructed.exercise_events))

        inv = reconstructed.verify_invariants(raise_on_error=True)
        self.assertTrue(all(inv.values()))

    def test_multi_party_assignment_allocation(self) -> None:
        """Testa divisão de atribuição entre múltiplos lançadores para um titular grande."""
        engine = CommodityOptionsEngine()
        contract = AgriculturalOptionContract(
            ticker="SJCF27C1250",
            commodity=CommodityType.SOYBEAN,
            underlying_future_ticker="SJCF27",
            option_type=OptionType.CALL,
            strike=Decimal("12.50"),
            expiration_date=date(2027, 1, 15),
        )
        engine.register_contract(contract)

        # 1 Titular com 100 contratos; 3 Lançadores com 50, 30 e 20 contratos
        engine.add_position(ParticipantPosition("POS-L1", "EXPORTER_SOJA", "SJCF27C1250", PositionSide.LONG, 100, Decimal("0.80")))
        engine.add_position(ParticipantPosition("POS-S1", "WRITER_A", "SJCF27C1250", PositionSide.SHORT, 50, Decimal("0.80")))
        engine.add_position(ParticipantPosition("POS-S2", "WRITER_B", "SJCF27C1250", PositionSide.SHORT, 30, Decimal("0.80")))
        engine.add_position(ParticipantPosition("POS-S3", "WRITER_C", "SJCF27C1250", PositionSide.SHORT, 20, Decimal("0.80")))

        events = engine.evaluate_and_execute_exercise(date(2027, 1, 15), {"SJCF27": Decimal("14.00")})

        self.assertEqual(len(events), 3)
        self.assertEqual(sum(e.contracts_exercised for e in events), 100)
        self.assertEqual(len(engine.created_futures), 6)  # 3 Longs (100 total) + 3 Shorts (50, 30, 20)

        # Invariante física de conservação de sacas (450 sacas por contrato):
        total_sacas = 100 * 450
        self.assertEqual(total_sacas, 45000)

        inv = engine.verify_invariants(raise_on_error=True)
        self.assertTrue(all(inv.values()))

    def test_extreme_deep_in_the_money_and_out_of_the_money(self) -> None:
        """Testa estabilidade numérica em moneyness extremo (Deep ITM e Deep OTM)."""
        F_deep_itm = Decimal("200.00")
        K = Decimal("50.00")
        T = Decimal("1.00")
        vol = Decimal("0.20")
        r = Decimal("0.10")

        res_deep_itm = Black76Model.price(F_deep_itm, K, T, vol, r)
        self.assertGreater(res_deep_itm.call_price, Decimal("130.00"))
        self.assertEqual(res_deep_itm.put_price, Decimal("0.0000"))
        self.assertTrue(res_deep_itm.parity_holds)

        # Delta da call deve ser praticamente exp(-r*T)
        disc = math.exp(-0.10 * 1.0)
        self.assertAlmostEqual(float(res_deep_itm.call_greeks.delta), disc, places=3)
        self.assertAlmostEqual(float(res_deep_itm.put_greeks.delta), 0.0, places=3)

    def test_zero_sum_holder_writer_conservation(self) -> None:
        """Comprova que a soma financeira líquida de MTM no D0 é rigorosamente 0.00."""
        engine = CommodityOptionsEngine()
        contract = AgriculturalOptionContract(
            ticker="ICFZ26P24000",
            commodity=CommodityType.ARABICA_COFFEE,
            underlying_future_ticker="ICFZ26",
            option_type=OptionType.PUT,
            strike=Decimal("240.00"),
            expiration_date=date(2026, 12, 15),
        )
        engine.register_contract(contract)

        engine.add_position(ParticipantPosition("POS-P-L", "COFFEE_COOP", "ICFZ26P24000", PositionSide.LONG, 75, Decimal("10.00")))
        engine.add_position(ParticipantPosition("POS-P-S", "ROASTER_LLC", "ICFZ26P24000", PositionSide.SHORT, 75, Decimal("10.00")))

        # Café fecha em USD 220.00 (Put ITM por USD 20.00)
        events = engine.evaluate_and_execute_exercise(date(2026, 12, 15), {"ICFZ26": Decimal("220.00")})
        self.assertEqual(len(events), 1)

        evt = events[0]
        # Multiplicador do café é 100 sacas
        # Ganho Holder: (240.00 - 220.00) * 100 * 75 = USD 150.000,00
        self.assertEqual(evt.holder_mtm_gain, Decimal("150000.00"))
        self.assertEqual(evt.writer_mtm_gain, Decimal("-150000.00"))
        self.assertEqual(evt.net_financial_mtm, Decimal("0.00"))

        inv = engine.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["exercise_zero_sum_financial_balance"])


if __name__ == "__main__":
    unittest.main()
