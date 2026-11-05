"""Testes do FL-072 — Risco de Liquidez Intradiária, LCR e Estresse Cambial PTAX."""

from __future__ import annotations

import copy
import json
import unittest
from decimal import Decimal
from pathlib import Path

from src.intraday_liquidity_risk import (
    FlowDirection,
    FxDerivativeType,
    FxPosition,
    FxStressScenario,
    HQLAAsset,
    HQLALevel,
    IntradayFlow,
    IntradayLiquidityError,
    IntradayLiquidityRiskEngine,
    InvalidFxShockError,
    InvalidHQLAError,
    InvalidTimeWindowError,
    PositionSide,
    SettlementSystem,
    SurvivalStatus,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "intraday_liquidity_risk.json"


def _scenario(shock: str, sid: str = "S") -> FxStressScenario:
    return FxStressScenario(scenario_id=sid, name=f"PTAX +{shock}", shock_pct=Decimal(shock), base_ptax=Decimal("5.5000"))


class TestIntradayLiquidityRisk(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.engine = IntradayLiquidityRiskEngine.load_from_fixture(FIXTURE)

    # ---------------------------------------------------------------- HQLA
    def test_hqla_buffer_haircuts_caps_and_encumbrance(self) -> None:
        rep = self.engine.calculate_hqla_buffer()
        self.assertEqual(rep.total_gross_market_value, Decimal("540000000.00"))
        self.assertEqual(rep.total_encumbered_value, Decimal("50000000.00"))
        self.assertEqual(rep.level_1_adjusted, Decimal("300000000.00"))
        self.assertEqual(rep.level_2a_adjusted, Decimal("93500000.00"))
        self.assertEqual(rep.level_2b_adjusted, Decimal("40000000.00"))
        self.assertEqual(rep.level_2a_cap_allowed, Decimal("200000000.00"))
        self.assertEqual(rep.total_eligible_hqla, Decimal("433500000.00"))
        self.assertEqual(rep.level_2a_excess_deducted, Decimal("0.00"))
        self.assertEqual(rep.level_2b_excess_deducted, Decimal("0.00"))

    def test_level2_cap_deducts_excess_when_level1_is_small(self) -> None:
        data = copy.deepcopy(self.raw)
        data["hqla_assets"][0]["market_value"] = "30000000.00"  # L1 = 30M -> teto L2 = 20M
        eng = IntradayLiquidityRiskEngine.from_dict(data)
        rep = eng.calculate_hqla_buffer()
        self.assertEqual(rep.level_2a_cap_allowed, Decimal("20000000.00"))
        eligible_l2 = rep.total_eligible_hqla - rep.level_1_adjusted
        self.assertLessEqual(eligible_l2, rep.level_2a_cap_allowed)
        self.assertGreater(rep.level_2a_excess_deducted + rep.level_2b_excess_deducted, Decimal("0"))

    # ------------------------------------------------------------ FX stress
    def test_fx_stress_20pct_margin_call_decomposition(self) -> None:
        res = self.engine.simulate_fx_stress(_scenario("0.20"), target_window_id="W1000")
        self.assertEqual(res.shocked_ptax, Decimal("6.6000"))
        self.assertEqual(res.gross_variation_margin_debit, Decimal("110000000.00"))
        self.assertEqual(res.gross_variation_margin_credit, Decimal("11000000.00"))
        self.assertEqual(res.core_initial_margin_addon, Decimal("9900000.00"))
        self.assertEqual(res.foreign_collateral_revaluation_gain, Decimal("18700000.00"))
        self.assertEqual(res.net_additional_margin_call, Decimal("101200000.00"))

    def test_fx_shock_monotonicity(self) -> None:
        scenarios = [_scenario(s, f"S{i}") for i, s in enumerate(["0.05", "0.10", "0.15", "0.20"])]
        self.assertTrue(self.engine.verify_fx_shock_monotonicity(scenarios))
        self.assertFalse(self.engine.verify_fx_shock_monotonicity(list(reversed(scenarios))))

    def test_fx_stress_unknown_window_rejected(self) -> None:
        with self.assertRaises(InvalidTimeWindowError):
            self.engine.simulate_fx_stress(_scenario("0.10"), target_window_id="W9999")

    # ------------------------------------------------------ survival horizon
    def test_baseline_survival_monetizes_temporary_deficit(self) -> None:
        rep = self.engine.calculate_survival_horizon()
        self.assertEqual(rep.survival_status, SurvivalStatus.DEFICIT_MONETIZED)
        self.assertEqual(rep.total_hqla_monetized, Decimal("5000000.00"))
        self.assertEqual(rep.remaining_hqla_buffer, Decimal("428500000.00"))
        self.assertEqual(rep.final_cash_balance, Decimal("10000000.00"))
        self.assertEqual(rep.liquidity_shortfall, Decimal("0.00"))
        self.assertEqual(rep.minimum_projected_cash, Decimal("-5000000.00"))
        self.assertTrue(all(self.engine.verify_formal_invariants(rep).values()))

    def test_stressed_survival_absorbs_margin_call(self) -> None:
        rep = self.engine.calculate_survival_horizon(_scenario("0.20"), target_stress_window="W1000")
        self.assertEqual(rep.total_stress_margin_calls, Decimal("101200000.00"))
        self.assertEqual(rep.total_hqla_monetized, Decimal("106200000.00"))
        self.assertEqual(rep.final_cash_balance, Decimal("10000000.00"))
        self.assertEqual(rep.survival_status, SurvivalStatus.DEFICIT_MONETIZED)
        self.assertTrue(all(self.engine.verify_formal_invariants(rep).values()))

    def test_default_when_hqla_exhausted(self) -> None:
        data = copy.deepcopy(self.raw)
        data["hqla_assets"] = [a for a in data["hqla_assets"] if a["level"] != "LEVEL_1"]
        eng = IntradayLiquidityRiskEngine.from_dict(data)
        rep = eng.calculate_survival_horizon()
        self.assertEqual(rep.initial_hqla, Decimal("0.00"))  # sem L1, teto de L2 zera o colchão
        self.assertEqual(rep.survival_status, SurvivalStatus.DEFAULT)
        self.assertEqual(rep.exhaustion_window, "W1330")
        self.assertEqual(rep.survival_horizon_hours, Decimal("5.50"))
        self.assertEqual(rep.liquidity_shortfall, Decimal("5000000.00"))
        self.assertTrue(all(eng.verify_formal_invariants(rep).values()))
        self.assertTrue(all(p.is_exhausted for p in rep.window_points[2:]))

    def test_stress_suite_runs_all_scenarios(self) -> None:
        scenarios = [_scenario(s, f"S{i}") for i, s in enumerate(["0.05", "0.20"])]
        reports = self.engine.run_stress_testing_suite(scenarios)
        self.assertEqual(set(reports), {"S0", "S1"})
        self.assertLessEqual(reports["S0"].total_hqla_monetized, reports["S1"].total_hqla_monetized)

    # -------------------------------------------------------- serialization
    def test_roundtrip_serialization(self) -> None:
        clone = IntradayLiquidityRiskEngine.from_dict(self.engine.to_dict())
        self.assertEqual(
            clone.calculate_hqla_buffer().total_eligible_hqla,
            self.engine.calculate_hqla_buffer().total_eligible_hqla,
        )
        self.assertEqual(len(clone.flows), len(self.engine.flows))

    # ------------------------------------------------------------ validation
    def test_domain_validations(self) -> None:
        with self.assertRaises(InvalidHQLAError):
            HQLAAsset("X", "X", HQLALevel.LEVEL_1, Decimal("-1"), Decimal("0"))
        with self.assertRaises(InvalidHQLAError):
            HQLAAsset("X", "X", HQLALevel.LEVEL_1, Decimal("1"), Decimal("1.5"))
        with self.assertRaises(InvalidHQLAError):
            HQLAAsset("X", "X", HQLALevel.LEVEL_1, Decimal("1"), Decimal("0"), fx_rate=Decimal("0"))
        with self.assertRaises(IntradayLiquidityError):
            FxPosition("P", FxDerivativeType.FUTURE_DOL, PositionSide.SHORT, 0, Decimal("50000"), Decimal("5.5"))
        with self.assertRaises(InvalidFxShockError):
            FxStressScenario("S", "S", Decimal("0.1"), Decimal("0"))
        with self.assertRaises(InvalidTimeWindowError):
            self.engine.register_flow(
                IntradayFlow("F", "W0000", SettlementSystem.SPB_STR, FlowDirection.INFLOW, Decimal("1"), "x")
            )


if __name__ == "__main__":
    unittest.main()
