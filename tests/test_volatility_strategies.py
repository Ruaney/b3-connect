"""
Suíte de Testes Unitários — B3 PUMA Volatility Strategies & Multi-Leg Execution (FL-063)
========================================================================================
Testes exaustivos com 100% de cobertura dos componentes:
1. Carregamento e integridade das fixtures canônicas da B3.
2. Validador estrutural de pernas para Straddle, Strangle, Iron Condor, Collar e Butterfly.
3. Tratamento e rejeição determinística de combinações inválidas.
4. Motor de Margem de Risco Combinada (CORE margin offset e haircut de spread de volatilidade).
5. Motor de Execução Atômica Multi-Leg no PUMA (All-or-None, Zero Legging Risk, Conservação de Razão).
6. Lei de Conservação de Razão de Pernas (Leg Ratio Invariant).
7. Motor Quantitativo de Gregas e Perfil de Payoff/Breakeven.
8. Serialização e Desserialização (to_dict / from_dict).
"""

from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.volatility_strategies import (
    AtomicExecutionError,
    CombinedMarginRequirement,
    CombinedRiskMarginEngine,
    ExecutionStatus,
    InvalidLegCombinationError,
    LegExecutionFill,
    LegSide,
    MarginCategory,
    MultiLegExecutionReceipt,
    MultiLegOrder,
    OptionType,
    PumaAtomicMultiLegEngine,
    RatioConservationError,
    StrategyGreeks,
    StrategyLeg,
    StrategyPayoff,
    StrategyType,
    VolatilityAnalytics,
    VolatilityStrategyError,
    VolatilityStrategyValidator,
    black_scholes_greeks,
    load_from_fixture,
)


class TestVolatilityStrategiesFixture(unittest.TestCase):
    """Testes de carga e integridade das fixtures de estratégias de volatilidade."""

    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "volatility_strategies.json"

    def test_load_fixture_success(self) -> None:
        data = load_from_fixture(self.fixture_file)
        self.assertEqual(data["version"], "1.0.0")
        self.assertIn("strategies", data)
        self.assertEqual(len(data["strategies"]), 5)

        strat_types = {s["strategy_type"] for s in data["strategies"]}
        expected_types = {"STRADDLE", "STRANGLE", "IRON_CONDOR", "COLLAR", "BUTTERFLY"}
        self.assertEqual(strat_types, expected_types)

    def test_load_fixture_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_from_fixture("fixtures/non_existent_fixture.json")


class TestVolatilityStrategyValidator(unittest.TestCase):
    """Testes para o validador de pernas combinadas (VolatilityStrategyValidator)."""

    def test_straddle_valid_long_and_short(self) -> None:
        # Long Straddle
        legs_long = [
            StrategyLeg("L1", "PETRA380", OptionType.CALL, LegSide.BUY, Decimal("38.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "PETRM380", OptionType.PUT, LegSide.BUY, Decimal("38.00"), 1, "2026-11-20"),
        ]
        VolatilityStrategyValidator.validate_straddle(legs_long)

        # Short Straddle
        legs_short = [
            StrategyLeg("L1", "PETRA380", OptionType.CALL, LegSide.SELL, Decimal("38.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "PETRM380", OptionType.PUT, LegSide.SELL, Decimal("38.00"), 1, "2026-11-20"),
        ]
        VolatilityStrategyValidator.validate_straddle(legs_short)

    def test_straddle_invalid_combinations(self) -> None:
        # Strikes divergentes
        legs_strike_diff = [
            StrategyLeg("L1", "PETRA380", OptionType.CALL, LegSide.BUY, Decimal("38.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "PETRM370", OptionType.PUT, LegSide.BUY, Decimal("37.00"), 1, "2026-11-20"),
        ]
        with self.assertRaises(InvalidLegCombinationError):
            VolatilityStrategyValidator.validate_straddle(legs_strike_diff)

        # Lados divergentes (Buy Call + Sell Put)
        legs_side_diff = [
            StrategyLeg("L1", "PETRA380", OptionType.CALL, LegSide.BUY, Decimal("38.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "PETRM380", OptionType.PUT, LegSide.SELL, Decimal("38.00"), 1, "2026-11-20"),
        ]
        with self.assertRaises(InvalidLegCombinationError):
            VolatilityStrategyValidator.validate_straddle(legs_side_diff)

        # Ratio divergente
        legs_ratio_invalid = [
            StrategyLeg("L1", "PETRA380", OptionType.CALL, LegSide.BUY, Decimal("38.00"), 2, "2026-11-20"),
            StrategyLeg("L2", "PETRM380", OptionType.PUT, LegSide.BUY, Decimal("38.00"), 1, "2026-11-20"),
        ]
        with self.assertRaises(InvalidLegCombinationError):
            VolatilityStrategyValidator.validate_straddle(legs_ratio_invalid)

        # Vencimentos distintos
        legs_exp_diff = [
            StrategyLeg("L1", "PETRA380", OptionType.CALL, LegSide.BUY, Decimal("38.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "PETRM380", OptionType.PUT, LegSide.BUY, Decimal("38.00"), 1, "2026-12-18"),
        ]
        with self.assertRaises(InvalidLegCombinationError):
            VolatilityStrategyValidator.validate_straddle(legs_exp_diff)

        # 3 pernas
        with self.assertRaises(InvalidLegCombinationError):
            VolatilityStrategyValidator.validate_straddle(legs_long := [
                StrategyLeg("L1", "PETRA380", OptionType.CALL, LegSide.BUY, Decimal("38.00"), 1),
                StrategyLeg("L2", "PETRM380", OptionType.PUT, LegSide.BUY, Decimal("38.00"), 1),
                StrategyLeg("L3", "PETRA400", OptionType.CALL, LegSide.BUY, Decimal("40.00"), 1),
            ])

    def test_strangle_valid_and_invalid(self) -> None:
        # Válido: Put 62 < Call 68
        legs_valid = [
            StrategyLeg("L1", "VALEM620", OptionType.PUT, LegSide.SELL, Decimal("62.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "VALEA680", OptionType.CALL, LegSide.SELL, Decimal("68.00"), 1, "2026-11-20"),
        ]
        VolatilityStrategyValidator.validate_strangle(legs_valid)

        # Inválido: Put strike >= Call strike
        legs_inverted = [
            StrategyLeg("L1", "VALEM680", OptionType.PUT, LegSide.SELL, Decimal("68.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "VALEA620", OptionType.CALL, LegSide.SELL, Decimal("62.00"), 1, "2026-11-20"),
        ]
        with self.assertRaises(InvalidLegCombinationError):
            VolatilityStrategyValidator.validate_strangle(legs_inverted)

        # Inválido: 2 Calls
        legs_two_calls = [
            StrategyLeg("L1", "VALEA620", OptionType.CALL, LegSide.SELL, Decimal("62.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "VALEA680", OptionType.CALL, LegSide.SELL, Decimal("68.00"), 1, "2026-11-20"),
        ]
        with self.assertRaises(InvalidLegCombinationError):
            VolatilityStrategyValidator.validate_strangle(legs_two_calls)

    def test_iron_condor_valid_and_invalid(self) -> None:
        # Válido: Credit Iron Condor
        # K_p1=112 (BUY), K_p2=116 (SELL), K_c1=124 (SELL), K_c2=128 (BUY)
        legs_valid = [
            StrategyLeg("L1", "BOVAM112", OptionType.PUT, LegSide.BUY, Decimal("112.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "BOVAM116", OptionType.PUT, LegSide.SELL, Decimal("116.00"), 1, "2026-11-20"),
            StrategyLeg("L3", "BOVAA124", OptionType.CALL, LegSide.SELL, Decimal("124.00"), 1, "2026-11-20"),
            StrategyLeg("L4", "BOVAA128", OptionType.CALL, LegSide.BUY, Decimal("128.00"), 1, "2026-11-20"),
        ]
        VolatilityStrategyValidator.validate_iron_condor(legs_valid)

        # Inválido: Strikes desordenados (Put2 > Call1)
        legs_unordered = [
            StrategyLeg("L1", "BOVAM112", OptionType.PUT, LegSide.BUY, Decimal("112.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "BOVAM125", OptionType.PUT, LegSide.SELL, Decimal("125.00"), 1, "2026-11-20"),
            StrategyLeg("L3", "BOVAA120", OptionType.CALL, LegSide.SELL, Decimal("120.00"), 1, "2026-11-20"),
            StrategyLeg("L4", "BOVAA128", OptionType.CALL, LegSide.BUY, Decimal("128.00"), 1, "2026-11-20"),
        ]
        with self.assertRaises(InvalidLegCombinationError):
            VolatilityStrategyValidator.validate_iron_condor(legs_unordered)

        # Inválido: Apenas 3 pernas
        with self.assertRaises(InvalidLegCombinationError):
            VolatilityStrategyValidator.validate_iron_condor(legs_valid[:3])

    def test_collar_valid_and_invalid(self) -> None:
        # Válido: 3 pernas com Spot
        legs_3_valid = [
            StrategyLeg("L1", "PETR4", OptionType.SPOT, LegSide.BUY, Decimal("0.00"), 1, None, Decimal("38.00")),
            StrategyLeg("L2", "PETRM360", OptionType.PUT, LegSide.BUY, Decimal("36.00"), 1, "2026-11-20"),
            StrategyLeg("L3", "PETRA400", OptionType.CALL, LegSide.SELL, Decimal("40.00"), 1, "2026-11-20"),
        ]
        VolatilityStrategyValidator.validate_collar(legs_3_valid)

        # Válido: 2 pernas sintético
        legs_2_valid = [
            StrategyLeg("L2", "PETRM360", OptionType.PUT, LegSide.BUY, Decimal("36.00"), 1, "2026-11-20"),
            StrategyLeg("L3", "PETRA400", OptionType.CALL, LegSide.SELL, Decimal("40.00"), 1, "2026-11-20"),
        ]
        VolatilityStrategyValidator.validate_collar(legs_2_valid)

        # Inválido: Put strike >= Call strike
        legs_collar_inverted = [
            StrategyLeg("L2", "PETRM400", OptionType.PUT, LegSide.BUY, Decimal("40.00"), 1, "2026-11-20"),
            StrategyLeg("L3", "PETRA360", OptionType.CALL, LegSide.SELL, Decimal("36.00"), 1, "2026-11-20"),
        ]
        with self.assertRaises(InvalidLegCombinationError):
            VolatilityStrategyValidator.validate_collar(legs_collar_inverted)

        # Inválido: Put vendida
        legs_collar_wrong_side = [
            StrategyLeg("L2", "PETRM360", OptionType.PUT, LegSide.SELL, Decimal("36.00"), 1, "2026-11-20"),
            StrategyLeg("L3", "PETRA400", OptionType.CALL, LegSide.SELL, Decimal("40.00"), 1, "2026-11-20"),
        ]
        with self.assertRaises(InvalidLegCombinationError):
            VolatilityStrategyValidator.validate_collar(legs_collar_wrong_side)

    def test_butterfly_valid_and_invalid(self) -> None:
        # Válido: Borboleta de 3 strikes simétrica 1:2:1
        legs_3_valid = [
            StrategyLeg("L1", "PETRA360", OptionType.CALL, LegSide.BUY, Decimal("36.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "PETRA380", OptionType.CALL, LegSide.SELL, Decimal("38.00"), 2, "2026-11-20"),
            StrategyLeg("L3", "PETRA400", OptionType.CALL, LegSide.BUY, Decimal("40.00"), 1, "2026-11-20"),
        ]
        VolatilityStrategyValidator.validate_butterfly(legs_3_valid)

        # Válido: Iron Butterfly 4 pernas
        legs_iron_bf = [
            StrategyLeg("L1", "PETRM360", OptionType.PUT, LegSide.BUY, Decimal("36.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "PETRM380", OptionType.PUT, LegSide.SELL, Decimal("38.00"), 1, "2026-11-20"),
            StrategyLeg("L3", "PETRA380", OptionType.CALL, LegSide.SELL, Decimal("38.00"), 1, "2026-11-20"),
            StrategyLeg("L4", "PETRA400", OptionType.CALL, LegSide.BUY, Decimal("40.00"), 1, "2026-11-20"),
        ]
        VolatilityStrategyValidator.validate_butterfly(legs_iron_bf)

        # Inválido: Asas assimétricas (38 - 36 = 2 != 41 - 38 = 3)
        legs_asym = [
            StrategyLeg("L1", "PETRA360", OptionType.CALL, LegSide.BUY, Decimal("36.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "PETRA380", OptionType.CALL, LegSide.SELL, Decimal("38.00"), 2, "2026-11-20"),
            StrategyLeg("L3", "PETRA410", OptionType.CALL, LegSide.BUY, Decimal("41.00"), 1, "2026-11-20"),
        ]
        with self.assertRaises(InvalidLegCombinationError):
            VolatilityStrategyValidator.validate_butterfly(legs_asym)

        # Inválido: Ratios errados (1:1:1 em vez de 1:2:1)
        legs_wrong_ratio = [
            StrategyLeg("L1", "PETRA360", OptionType.CALL, LegSide.BUY, Decimal("36.00"), 1, "2026-11-20"),
            StrategyLeg("L2", "PETRA380", OptionType.CALL, LegSide.SELL, Decimal("38.00"), 1, "2026-11-20"),
            StrategyLeg("L3", "PETRA400", OptionType.CALL, LegSide.BUY, Decimal("40.00"), 1, "2026-11-20"),
        ]
        with self.assertRaises(InvalidLegCombinationError):
            VolatilityStrategyValidator.validate_butterfly(legs_wrong_ratio)


class TestCombinedRiskMarginEngine(unittest.TestCase):
    """Testes para o motor de margem de risco combinada (haircut e offsets CORE)."""

    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "volatility_strategies.json"
        self.fixture_data = load_from_fixture(self.fixture_file)
        self.engine = CombinedRiskMarginEngine(volatility_spread_haircut=Decimal("0.15"))

    def test_combined_margin_straddle_long(self) -> None:
        strat_data = self.fixture_data["strategies"][0]
        order = MultiLegOrder.from_dict({
            "order_id": strat_data["strategy_id"],
            "strategy_type": strat_data["strategy_type"],
            "underlying_symbol": strat_data["underlying_symbol"],
            "underlying_spot": strat_data["underlying_spot"],
            "packages_requested": strat_data["packages_requested"],
            "limit_net_price": strat_data["limit_net_price"],
            "legs": strat_data["legs"],
        })

        margin_req = self.engine.calculate_combined_margin(order)
        self.assertEqual(margin_req.margin_category, MarginCategory.DEBIT_PAID)
        # Débito unitário: 1.65 + 1.45 = 3.10. Para 1000 pacotes: 3100.00
        self.assertEqual(margin_req.combined_margin, Decimal("3100.00"))
        self.assertEqual(margin_req.worst_case_loss, Decimal("3100.00"))

    def test_combined_margin_strangle_short_with_haircut(self) -> None:
        strat_data = self.fixture_data["strategies"][1]
        order = MultiLegOrder.from_dict({
            "order_id": strat_data["strategy_id"],
            "strategy_type": strat_data["strategy_type"],
            "underlying_symbol": strat_data["underlying_symbol"],
            "underlying_spot": strat_data["underlying_spot"],
            "packages_requested": strat_data["packages_requested"],
            "limit_net_price": strat_data["limit_net_price"],
            "legs": strat_data["legs"],
        })

        margin_req = self.engine.calculate_combined_margin(order)
        self.assertEqual(margin_req.margin_category, MarginCategory.WORST_CASE_OFFSET)
        # Margem combinada deve ser substancialmente menor que a soma isolada das pontas a descoberto!
        self.assertLess(margin_req.combined_margin, margin_req.naked_isolated_margin)
        self.assertGreater(margin_req.margin_benefit, Decimal("0.00"))
        self.assertGreater(margin_req.haircut_applied_pct, Decimal("0.00"))

    def test_combined_margin_iron_condor(self) -> None:
        strat_data = self.fixture_data["strategies"][2]
        order = MultiLegOrder.from_dict({
            "order_id": strat_data["strategy_id"],
            "strategy_type": strat_data["strategy_type"],
            "underlying_symbol": strat_data["underlying_symbol"],
            "underlying_spot": strat_data["underlying_spot"],
            "packages_requested": strat_data["packages_requested"],
            "limit_net_price": strat_data["limit_net_price"],
            "legs": strat_data["legs"],
        })

        margin_req = self.engine.calculate_combined_margin(order)
        self.assertEqual(margin_req.margin_category, MarginCategory.SPREAD_CAPPED)
        # Largura máxima do spread: 4.00. Crédito unitário: 1.75.
        # Risco unitário máximo = 4.00 - 1.75 = 2.25.
        # Para 2000 pacotes: 2000 * 2.25 = 4500.00
        self.assertEqual(margin_req.combined_margin, Decimal("4500.00"))
        self.assertEqual(margin_req.worst_case_loss, Decimal("4500.00"))
        # Margem isolada na abordagem ingênua exigiria dezenas de milhares de reais!
        self.assertLess(margin_req.combined_margin, margin_req.naked_isolated_margin)

    def test_combined_margin_collar(self) -> None:
        strat_data = self.fixture_data["strategies"][3]
        order = MultiLegOrder.from_dict({
            "order_id": strat_data["strategy_id"],
            "strategy_type": strat_data["strategy_type"],
            "underlying_symbol": strat_data["underlying_symbol"],
            "underlying_spot": strat_data["underlying_spot"],
            "packages_requested": strat_data["packages_requested"],
            "limit_net_price": strat_data["limit_net_price"],
            "legs": strat_data["legs"],
        })

        margin_req = self.engine.calculate_combined_margin(order)
        self.assertEqual(margin_req.margin_category, MarginCategory.COVERED_COLLAR)
        # Risco máximo: Queda até Strike Put (38 - 36 = 2.00) + custo líquido de opções (0.85 - 0.90 = -0.05)
        # Risco unitário = 1.95. Para 1000 pacotes: 1950.00
        self.assertEqual(margin_req.combined_margin, Decimal("1950.00"))

    def test_combined_margin_butterfly(self) -> None:
        strat_data = self.fixture_data["strategies"][4]
        order = MultiLegOrder.from_dict({
            "order_id": strat_data["strategy_id"],
            "strategy_type": strat_data["strategy_type"],
            "underlying_symbol": strat_data["underlying_symbol"],
            "underlying_spot": strat_data["underlying_spot"],
            "packages_requested": strat_data["packages_requested"],
            "limit_net_price": strat_data["limit_net_price"],
            "legs": strat_data["legs"],
        })

        margin_req = self.engine.calculate_combined_margin(order)
        self.assertEqual(margin_req.margin_category, MarginCategory.DEBIT_PAID)
        # Débito líquido unitário: 2.80 - 2*1.65 + 0.90 = 0.40.
        # Para 1000 pacotes: 400.00
        self.assertEqual(margin_req.combined_margin, Decimal("400.00"))


class TestPumaAtomicMultiLegEngine(unittest.TestCase):
    """Testes para o motor de execução atômica multi-leg no PUMA Trading System."""

    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "volatility_strategies.json"
        self.fixture_data = load_from_fixture(self.fixture_file)
        self.engine = PumaAtomicMultiLegEngine()

    def test_execute_straddle_atomic_success(self) -> None:
        strat_data = self.fixture_data["strategies"][0]
        order = MultiLegOrder.from_dict({
            "order_id": strat_data["strategy_id"],
            "strategy_type": strat_data["strategy_type"],
            "underlying_symbol": strat_data["underlying_symbol"],
            "underlying_spot": strat_data["underlying_spot"],
            "packages_requested": strat_data["packages_requested"],
            "limit_net_price": strat_data["limit_net_price"],
            "legs": strat_data["legs"],
        })

        receipt = self.engine.execute_order(order, strat_data["simulated_book"])
        self.assertTrue(receipt.atomic)
        self.assertEqual(receipt.packages_filled, 1000)
        self.assertEqual(receipt.net_strategy_price, Decimal("3.10"))
        self.assertEqual(receipt.total_net_cash_flow, Decimal("-3100.00"))
        self.assertEqual(len(receipt.leg_fills), 2)

        # Verificação da Lei de Conservação de Razão entre Pernas
        self.assertTrue(self.engine.verify_ratio_conservation(receipt, order))
        self.assertEqual(order.status, ExecutionStatus.FILLED)

    def test_execute_iron_condor_atomic_success(self) -> None:
        strat_data = self.fixture_data["strategies"][2]
        order = MultiLegOrder.from_dict({
            "order_id": strat_data["strategy_id"],
            "strategy_type": strat_data["strategy_type"],
            "underlying_symbol": strat_data["underlying_symbol"],
            "underlying_spot": strat_data["underlying_spot"],
            "packages_requested": strat_data["packages_requested"],
            "limit_net_price": strat_data["limit_net_price"],
            "legs": strat_data["legs"],
        })

        receipt = self.engine.execute_order(order, strat_data["simulated_book"])
        self.assertEqual(receipt.packages_filled, 2000)
        self.assertEqual(receipt.net_strategy_price, Decimal("-1.75"))
        self.assertEqual(receipt.total_net_cash_flow, Decimal("3500.00"))
        self.assertEqual(len(receipt.leg_fills), 4)

        # Checagem das quantidades individuais de cada perna (1:1:1:1)
        for fill in receipt.leg_fills:
            self.assertEqual(fill.executed_qty, 2000)

        self.assertTrue(self.engine.verify_ratio_conservation(receipt, order))

    def test_execute_butterfly_ratio_conservation(self) -> None:
        strat_data = self.fixture_data["strategies"][4]
        order = MultiLegOrder.from_dict({
            "order_id": strat_data["strategy_id"],
            "strategy_type": strat_data["strategy_type"],
            "underlying_symbol": strat_data["underlying_symbol"],
            "underlying_spot": strat_data["underlying_spot"],
            "packages_requested": strat_data["packages_requested"],
            "limit_net_price": strat_data["limit_net_price"],
            "legs": strat_data["legs"],
        })

        receipt = self.engine.execute_order(order, strat_data["simulated_book"])
        self.assertEqual(receipt.packages_filled, 1000)
        self.assertEqual(receipt.net_strategy_price, Decimal("0.40"))
        self.assertEqual(receipt.total_net_cash_flow, Decimal("-400.00"))

        # Invariante estrita: Miolo deve ter executado exatamente 2000 lotes e asas 1000 lotes (1:2:1)
        fills_by_symbol = {f.symbol: f.executed_qty for f in receipt.leg_fills}
        self.assertEqual(fills_by_symbol["PETRA360"], 1000)
        self.assertEqual(fills_by_symbol["PETRA380"], 2000)
        self.assertEqual(fills_by_symbol["PETRA400"], 1000)

        self.assertTrue(self.engine.verify_ratio_conservation(receipt, order))

    def test_insufficient_liquidity_all_or_none_rejection(self) -> None:
        strat_data = self.fixture_data["strategies"][0]
        order = MultiLegOrder.from_dict({
            "order_id": "ORD-STRADDLE-OVERSIZED",
            "strategy_type": strat_data["strategy_type"],
            "underlying_symbol": strat_data["underlying_symbol"],
            "underlying_spot": strat_data["underlying_spot"],
            "packages_requested": 10000,  # Excede liquidez do book (max 3000)
            "legs": strat_data["legs"],
        })

        # Deve rejeitar integralmente sob All-or-None sem executar nenhuma perna
        with self.assertRaises(AtomicExecutionError):
            self.engine.execute_order(order, strat_data["simulated_book"], allow_partial_packages=False)

        self.assertEqual(order.status, ExecutionStatus.REJECTED)
        self.assertEqual(order.packages_executed, 0)

    def test_partial_package_execution_conserves_ratios(self) -> None:
        strat_data = self.fixture_data["strategies"][0]
        order = MultiLegOrder.from_dict({
            "order_id": "ORD-STRADDLE-PARTIAL",
            "strategy_type": strat_data["strategy_type"],
            "underlying_symbol": strat_data["underlying_symbol"],
            "underlying_spot": strat_data["underlying_spot"],
            "packages_requested": 5000,
            "legs": strat_data["legs"],
        })

        # Permite execução parcial de pacotes: liquidez do book é min(5000, 3000) = 3000
        receipt = self.engine.execute_order(order, strat_data["simulated_book"], allow_partial_packages=True)
        self.assertEqual(receipt.packages_filled, 3000)
        self.assertEqual(order.status, ExecutionStatus.PARTIALLY_FILLED)
        self.assertTrue(self.engine.verify_ratio_conservation(receipt, order))

    def test_limit_price_violation_rejection(self) -> None:
        strat_data = self.fixture_data["strategies"][0]
        order = MultiLegOrder.from_dict({
            "order_id": "ORD-STRADDLE-TIGHT-LIMIT",
            "strategy_type": strat_data["strategy_type"],
            "underlying_symbol": strat_data["underlying_symbol"],
            "underlying_spot": strat_data["underlying_spot"],
            "packages_requested": 500,
            "limit_net_price": Decimal("2.50"),  # Mercado está cotando a 3.10
            "legs": strat_data["legs"],
        })

        with self.assertRaises(AtomicExecutionError):
            self.engine.execute_order(order, strat_data["simulated_book"])

        self.assertEqual(order.status, ExecutionStatus.REJECTED)


class TestVolatilityAnalytics(unittest.TestCase):
    """Testes para o motor quantitativo de gregas e perfis de payoff."""

    def test_black_scholes_analytical_greeks(self) -> None:
        # Call ATM (Spot=100, Strike=100, T=0.5, r=0.10, vol=0.20)
        delta_c, gamma_c, vega_c, theta_c = black_scholes_greeks(100.0, 100.0, 0.5, 0.10, 0.20, True)
        self.assertGreater(delta_c, 0.5)
        self.assertLess(delta_c, 0.7)
        self.assertGreater(gamma_c, 0.0)
        self.assertGreater(vega_c, 0.0)
        self.assertLess(theta_c, 0.0)  # Decaimento temporal é negativo para titular

        # Put ATM
        delta_p, gamma_p, vega_p, theta_p = black_scholes_greeks(100.0, 100.0, 0.5, 0.10, 0.20, False)
        self.assertAlmostEqual(delta_c - delta_p, 1.0, places=4)
        self.assertAlmostEqual(gamma_c, gamma_p, places=4)
        self.assertAlmostEqual(vega_c, vega_p, places=4)

    def test_strategy_greeks_straddle_delta_neutrality(self) -> None:
        legs = [
            StrategyLeg("L1", "PETRA380", OptionType.CALL, LegSide.BUY, Decimal("38.00"), 1, "2026-11-20", implied_vol=Decimal("0.28")),
            StrategyLeg("L2", "PETRM380", OptionType.PUT, LegSide.BUY, Decimal("38.00"), 1, "2026-11-20", implied_vol=Decimal("0.28")),
        ]
        order = MultiLegOrder("ORD-GREEKS", StrategyType.STRADDLE, "PETR4", Decimal("38.00"), 1000, legs)
        greeks = VolatilityAnalytics.calculate_strategy_greeks(order, interest_rate_annual=0.0)

        # Com taxa de juros zero e spot == strike, Delta de Call (~0.50) e Put (~-0.50) somam ~0.00 (Delta Neutro)
        self.assertAlmostEqual(float(greeks.delta), 0.0, delta=0.05)
        # Gamma e Vega agregados são positivos para Long Straddle
        self.assertGreater(greeks.gamma, Decimal("0.00"))
        self.assertGreater(greeks.vega, Decimal("0.00"))
        self.assertLess(greeks.theta, Decimal("0.00"))

    def test_payoff_straddle_breakevens(self) -> None:
        legs = [
            StrategyLeg("L1", "PETRA380", OptionType.CALL, LegSide.BUY, Decimal("38.00"), 1, market_price=Decimal("1.65")),
            StrategyLeg("L2", "PETRM380", OptionType.PUT, LegSide.BUY, Decimal("38.00"), 1, market_price=Decimal("1.45")),
        ]
        order = MultiLegOrder("ORD-PAYOFF", StrategyType.STRADDLE, "PETR4", Decimal("38.00"), 1000, legs)
        payoff = VolatilityAnalytics.calculate_payoff(order, min_spot=Decimal("30.00"), max_spot=Decimal("46.00"), steps=80)

        self.assertEqual(payoff.max_loss, Decimal("3100.00"))
        self.assertIsNone(payoff.max_profit)
        # Pontos de breakeven teóricos: 38.00 - 3.10 = 34.90 e 38.00 + 3.10 = 41.10
        self.assertEqual(len(payoff.breakeven_points), 2)
        self.assertAlmostEqual(float(payoff.breakeven_points[0]), 34.90, delta=0.25)
        self.assertAlmostEqual(float(payoff.breakeven_points[1]), 41.10, delta=0.25)


class TestDataClassSerialization(unittest.TestCase):
    """Testes para garantir integridade de serialização e desserialização de modelos."""

    def test_leg_serialization_roundtrip(self) -> None:
        leg = StrategyLeg(
            leg_id="LEG-01",
            symbol="PETRA380",
            option_type=OptionType.CALL,
            side=LegSide.BUY,
            strike=Decimal("38.00"),
            ratio=1,
            expiration_date="2026-11-20",
            market_price=Decimal("1.65"),
            implied_vol=Decimal("0.28"),
        )
        data = leg.to_dict()
        reconstructed = StrategyLeg.from_dict(data)
        self.assertEqual(leg.symbol, reconstructed.symbol)
        self.assertEqual(leg.strike, reconstructed.strike)
        self.assertEqual(leg.ratio, reconstructed.ratio)

    def test_order_serialization_roundtrip(self) -> None:
        legs = [
            StrategyLeg("L1", "PETRA380", OptionType.CALL, LegSide.BUY, Decimal("38.00"), 1),
            StrategyLeg("L2", "PETRM380", OptionType.PUT, LegSide.BUY, Decimal("38.00"), 1),
        ]
        order = MultiLegOrder("ORD-001", StrategyType.STRADDLE, "PETR4", Decimal("38.00"), 500, legs, Decimal("3.20"))
        data = order.to_dict()
        reconstructed = MultiLegOrder.from_dict(data)
        self.assertEqual(order.order_id, reconstructed.order_id)
        self.assertEqual(order.packages_requested, reconstructed.packages_requested)
        self.assertEqual(len(reconstructed.legs), 2)


if __name__ == "__main__":
    unittest.main()
