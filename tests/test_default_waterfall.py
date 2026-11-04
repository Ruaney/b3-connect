"""Testes unitários e de integração da Cascata de Inadimplência da Câmara B3 (FL-055).

Valida deterministicamente os 5 níveis de salvaguardas (CPMI-IOSCO PFMI 4 e 7),
a absorção progressiva de perdas, o rateio pro-rata no Fundo Mútuo e Chamadas de Aval,
a conservação matemática de centavos e a invariante fundamental de solvência.
"""

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.default_waterfall import (
    ClearingMemberNotFoundError,
    ClearingMemberSafeguards,
    DefaultLossEvent,
    DefaultWaterfallEngine,
    DefaultWaterfallError,
    DefaultWaterfallResult,
    InvalidLossAmountError,
    SafeguardLayerType,
    WaterfallExecutionStage,
    WaterfallInvariantViolationError,
    to_decimal_currency,
)


class TestDefaultWaterfall(unittest.TestCase):
    """Bateria de testes das salvaguardas e cascata de default da Câmara B3."""

    def setUp(self) -> None:
        self.fixtures_path = ROOT_DIR / "fixtures" / "default_waterfall_scenarios.json"
        self.engine, self.scenarios_data = DefaultWaterfallEngine.from_fixture(self.fixtures_path)

    def test_fixture_loading_and_member_registration(self) -> None:
        """Verifica se a fixture carregou os 5 membros de compensação e parâmetros da B3."""
        self.assertEqual(self.engine.b3_skin_in_the_game, Decimal("500000000.00"))
        self.assertEqual(self.engine.b3_additional_capital, Decimal("250000000.00"))
        self.assertEqual(len(self.engine.members), 5)

        xp = self.engine.get_member("MC-00105")
        self.assertIsNotNone(xp)
        self.assertEqual(xp.member_name, "XP Investimentos CCTVM")
        self.assertEqual(xp.deposited_margins, Decimal("300000000.00"))
        self.assertEqual(xp.default_fund_contribution, Decimal("150000000.00"))

    def test_scenario_1_small_default_level1_only(self) -> None:
        """Cenário 1: Pequeno default (R$ 120M) absorvido 100% no Nível 1 pelas margens próprias."""
        s1 = next(s for s in self.scenarios_data if s["scenario_id"] == "SCENARIO_SMALL_DEFAULT")
        event = DefaultLossEvent(
            event_id=s1["event_id"],
            defaulter_member_id=s1["defaulter_member_id"],
            gross_loss_amount=Decimal(s1["gross_loss_amount"]),
            timestamp=s1["timestamp"],
            stress_scenario_name=s1["stress_scenario_name"],
            description=s1["description"],
        )

        result = self.engine.process_default(event)

        self.assertTrue(result.is_fully_absorbed)
        self.assertTrue(result.settlement_continuity_maintained)
        self.assertEqual(result.residual_deficit, Decimal("0.00"))
        self.assertEqual(result.total_absorbed, Decimal("120000000.00"))
        self.assertEqual(result.max_layer_reached, SafeguardLayerType.LEVEL_1_DEFAULTER_MARGINS)

        # Nível 1 consumiu R$ 120M e sobrou R$ 180M de margens
        l1_stage = result.stages[0]
        self.assertEqual(l1_stage.consumed_amount, Decimal("120000000.00"))
        self.assertEqual(l1_stage.surplus_amount, Decimal("180000000.00"))
        self.assertFalse(l1_stage.is_exhausted)

        # Níveis 2 a 5 intocados (consumo zero)
        for stage in result.stages[1:]:
            self.assertEqual(stage.consumed_amount, Decimal("0.00"))

        # Membros adimplentes e capital da B3 completamente preservados
        self.assertEqual(len(result.non_defaulting_mutualized_impact), 0)
        self.assertEqual(len(result.non_defaulting_assessment_impact), 0)
        self.assertEqual(result.b3_own_capital_consumed, Decimal("0.00"))

        self.assertTrue(self.engine.verify_waterfall_invariants(result))

    def test_scenario_2_medium_default_absorbed_by_b3_sitg(self) -> None:
        """Cenário 2: Perda de R$ 510M exaure N1 e N2 e consome R$ 160M do SITG B3 (Nível 3)."""
        s2 = next(s for s in self.scenarios_data if s["scenario_id"] == "SCENARIO_MEDIUM_DEFAULT")
        event = DefaultLossEvent(
            event_id=s2["event_id"],
            defaulter_member_id=s2["defaulter_member_id"],
            gross_loss_amount=Decimal(s2["gross_loss_amount"]),
            timestamp=s2["timestamp"],
            stress_scenario_name=s2["stress_scenario_name"],
            description=s2["description"],
        )

        result = self.engine.process_default(event)

        self.assertTrue(result.is_fully_absorbed)
        self.assertEqual(result.residual_deficit, Decimal("0.00"))
        self.assertEqual(result.total_absorbed, Decimal("510000000.00"))
        self.assertEqual(result.max_layer_reached, SafeguardLayerType.LEVEL_3_B3_SKIN_IN_THE_GAME)

        # N1: R$ 250M esgotado
        self.assertEqual(result.stages[0].consumed_amount, Decimal("250000000.00"))
        self.assertTrue(result.stages[0].is_exhausted)

        # N2: R$ 100M esgotado
        self.assertEqual(result.stages[1].consumed_amount, Decimal("100000000.00"))
        self.assertTrue(result.stages[1].is_exhausted)

        # N3: R$ 160M consumido do SITG B3, sobram R$ 340M
        self.assertEqual(result.stages[2].consumed_amount, Decimal("160000000.00"))
        self.assertEqual(result.stages[2].surplus_amount, Decimal("340000000.00"))
        self.assertFalse(result.stages[2].is_exhausted)

        # Membros adimplentes continuam 100% blindados
        self.assertEqual(len(result.non_defaulting_mutualized_impact), 0)
        self.assertEqual(result.stages[3].consumed_amount, Decimal("0.00"))
        self.assertEqual(result.stages[4].consumed_amount, Decimal("0.00"))

        self.assertTrue(self.engine.verify_waterfall_invariants(result))

    def test_scenario_3_severe_systemic_mutualized_fund_pro_rata(self) -> None:
        """Cenário 3: Perda de R$ 1.6 Bi atinge Nível 4 e consome R$ 600M do Fundo Mútuo pro-rata."""
        s3 = next(s for s in self.scenarios_data if s["scenario_id"] == "SCENARIO_SEVERE_SYSTEMIC_DEFAULT")
        event = DefaultLossEvent(
            event_id=s3["event_id"],
            defaulter_member_id=s3["defaulter_member_id"],
            gross_loss_amount=Decimal(s3["gross_loss_amount"]),
            timestamp=s3["timestamp"],
            stress_scenario_name=s3["stress_scenario_name"],
            description=s3["description"],
        )

        result = self.engine.process_default(event)

        self.assertTrue(result.is_fully_absorbed)
        self.assertEqual(result.residual_deficit, Decimal("0.00"))
        self.assertEqual(result.total_absorbed, Decimal("1600000000.00"))
        self.assertEqual(result.max_layer_reached, SafeguardLayerType.LEVEL_4_MUTUALIZED_DEFAULT_FUND)

        # N1: Margens do faltoso BTG (R$ 400M)
        self.assertEqual(result.stages[0].consumed_amount, Decimal("400000000.00"))
        # N2: Cota do faltoso BTG (R$ 100M)
        self.assertEqual(result.stages[1].consumed_amount, Decimal("100000000.00"))
        # N3: SITG B3 (R$ 500M) integralmente consumido
        self.assertEqual(result.stages[2].consumed_amount, Decimal("500000000.00"))
        self.assertTrue(result.stages[2].is_exhausted)

        # N4: R$ 600M consumidos do fundo mútuo dos adimplentes (de um total de R$ 800M)
        l4 = result.stages[3]
        self.assertEqual(l4.consumed_amount, Decimal("600000000.00"))
        self.assertEqual(l4.surplus_amount, Decimal("200000000.00"))

        # Validação do rateio pro-rata:
        # MC-00105 (XP): 150/800 * 600 = 112.5M
        # MC-00033 (Santander): 100/800 * 600 = 75.0M
        # MC-00237 (Bradesco): 250/800 * 600 = 187.5M
        # MC-00341 (Itau): 300/800 * 600 = 225.0M
        alloc = l4.member_allocations
        self.assertEqual(alloc["MC-00105"], Decimal("112500000.00"))
        self.assertEqual(alloc["MC-00033"], Decimal("75000000.00"))
        self.assertEqual(alloc["MC-00237"], Decimal("187500000.00"))
        self.assertEqual(alloc["MC-00341"], Decimal("225000000.00"))
        self.assertEqual(sum(alloc.values()), Decimal("600000000.00"))

        # Nível 5 não foi tocado
        self.assertEqual(result.stages[4].consumed_amount, Decimal("0.00"))

        self.assertTrue(self.engine.verify_waterfall_invariants(result))

    def test_scenario_4_extreme_catastrophic_assessment(self) -> None:
        """Cenário 4: Perda extrema de R$ 2.3 Bi aciona Nível 5 com SITG adicional e Assessment Calls."""
        s4 = next(s for s in self.scenarios_data if s["scenario_id"] == "SCENARIO_EXTREME_CATASTROPHIC")
        event = DefaultLossEvent(
            event_id=s4["event_id"],
            defaulter_member_id=s4["defaulter_member_id"],
            gross_loss_amount=Decimal(s4["gross_loss_amount"]),
            timestamp=s4["timestamp"],
            stress_scenario_name=s4["stress_scenario_name"],
            description=s4["description"],
        )

        result = self.engine.process_default(event)

        self.assertTrue(result.is_fully_absorbed)
        self.assertEqual(result.residual_deficit, Decimal("0.00"))
        self.assertEqual(result.total_absorbed, Decimal("2300000000.00"))
        self.assertEqual(result.max_layer_reached, SafeguardLayerType.LEVEL_5_ADDITIONAL_B3_ASSESSMENT_CALLS)

        # Nível 5 acionado para absorver R$ 400M (de R$ 900M disponíveis)
        l5 = result.stages[4]
        self.assertEqual(l5.consumed_amount, Decimal("400000000.00"))
        self.assertEqual(l5.surplus_amount, Decimal("500000000.00"))

        # B3 Additional SITG consumido = R$ 250M
        self.assertEqual(result.stages[4].member_allocations.get("B3_ADDITIONAL_SITG"), Decimal("250000000.00"))
        # Assessment calls consumiram os outros R$ 150M
        assessment_sum = sum(
            amt for m_id, amt in result.stages[4].member_allocations.items() if m_id != "B3_ADDITIONAL_SITG"
        )
        self.assertEqual(assessment_sum, Decimal("150000000.00"))

        self.assertTrue(self.engine.verify_waterfall_invariants(result))

    def test_scenario_5_uncovered_deficit_exhausts_all_layers(self) -> None:
        """Cenário 5: Perda de R$ 3.0 Bi ultrapassa a totalidade das salvaguardas (R$ 2.8 Bi)."""
        s5 = next(s for s in self.scenarios_data if s["scenario_id"] == "SCENARIO_EXHAUSTED_UNCOVERED_DEFICIT")
        event = DefaultLossEvent(
            event_id=s5["event_id"],
            defaulter_member_id=s5["defaulter_member_id"],
            gross_loss_amount=Decimal(s5["gross_loss_amount"]),
            timestamp=s5["timestamp"],
            stress_scenario_name=s5["stress_scenario_name"],
            description=s5["description"],
        )

        result = self.engine.process_default(event)

        # Não absorvido integralmente
        self.assertFalse(result.is_fully_absorbed)
        self.assertFalse(result.settlement_continuity_maintained)
        self.assertEqual(result.total_absorbed, Decimal("2800000000.00"))
        self.assertEqual(result.residual_deficit, Decimal("200000000.00"))

        # Todos os 5 níveis devem estar exauridos
        for stage in result.stages:
            self.assertTrue(stage.is_exhausted)
            self.assertEqual(stage.surplus_amount, Decimal("0.00"))

        # Invariante fundamental continua 100% válida: 3.0B == 2.8B + 200M
        self.assertTrue(result.invariant_verified)
        self.assertTrue(self.engine.verify_waterfall_invariants(result))

    def test_pro_rata_conservation_exactness(self) -> None:
        """Garante que a distribuição pro-rata não perde frações de centavos."""
        members = [
            ClearingMemberSafeguards("M1", "Membro 1", Decimal("100"), Decimal("12345.67"), Decimal("1000")),
            ClearingMemberSafeguards("M2", "Membro 2", Decimal("100"), Decimal("23456.78"), Decimal("1000")),
            ClearingMemberSafeguards("M3", "Membro 3", Decimal("100"), Decimal("34567.89"), Decimal("1000")),
        ]
        loss_odd = Decimal("31415.93")
        allocations = self.engine.calculate_pro_rata_mutualized_losses(members, loss_odd)

        # A soma de todas as parcelas atribuídas DEVE ser rigorosamente idêntica a loss_odd
        total_allocated = sum(allocations.values())
        self.assertEqual(total_allocated, loss_odd)

        # Nenhuma parcela individual excede a cota do participante
        for m in members:
            self.assertLessEqual(allocations[m.member_id], m.default_fund_contribution)

    def test_unregistered_member_raises_error(self) -> None:
        """Tentativa de processar default para membro inexistente deve falhar com ClearingMemberNotFoundError."""
        event = DefaultLossEvent(
            event_id="DEF-ERR-001",
            defaulter_member_id="MC-INEXISTENTE",
            gross_loss_amount=Decimal("1000000.00"),
            timestamp=datetime.now(timezone.utc),
            stress_scenario_name="Erro",
            description="Membro invalido",
        )
        with self.assertRaises(ClearingMemberNotFoundError):
            self.engine.process_default(event)

    def test_invalid_loss_amount_raises_error(self) -> None:
        """Perda nula ou negativa deve disparar InvalidLossAmountError."""
        event_zero = DefaultLossEvent(
            event_id="DEF-ERR-002",
            defaulter_member_id="MC-00105",
            gross_loss_amount=Decimal("0.00"),
            timestamp=datetime.now(timezone.utc),
            stress_scenario_name="Zero",
            description="Perda zero",
        )
        with self.assertRaises(InvalidLossAmountError):
            self.engine.process_default(event_zero)

        event_neg = DefaultLossEvent(
            event_id="DEF-ERR-003",
            defaulter_member_id="MC-00105",
            gross_loss_amount=Decimal("-500.00"),
            timestamp=datetime.now(timezone.utc),
            stress_scenario_name="Negativa",
            description="Perda negativa",
        )
        with self.assertRaises(InvalidLossAmountError):
            self.engine.process_default(event_neg)

    def test_to_dict_serialization(self) -> None:
        """Garante serialização completa e fidelidade dos tipos exportados."""
        s1 = self.scenarios_data[0]
        event = DefaultLossEvent(
            event_id=s1["event_id"],
            defaulter_member_id=s1["defaulter_member_id"],
            gross_loss_amount=Decimal(s1["gross_loss_amount"]),
            timestamp=s1["timestamp"],
            stress_scenario_name=s1["stress_scenario_name"],
            description=s1["description"],
        )
        result = self.engine.process_default(event)
        res_dict = result.to_dict()

        json_str = json.dumps(res_dict, indent=2)
        self.assertIn("DEF-20261104-001", json_str)
        self.assertIn("LEVEL_1_DEFAULTER_MARGINS", json_str)
        self.assertIn("120000000.00", json_str)
        self.assertTrue(res_dict["invariant_verified"])

    def test_load_from_json_direct(self) -> None:
        """Testa o método de conveniência DefaultWaterfallEngine.load_from_json."""
        engine, events = DefaultWaterfallEngine.load_from_json(self.fixtures_path)
        self.assertEqual(len(engine.members), 5)
        self.assertEqual(len(events), 5)
        for ev in events:
            res = engine.process_default(ev)
            self.assertTrue(res.invariant_verified)
            self.assertTrue(engine.verify_waterfall_invariants(res))


if __name__ == "__main__":
    unittest.main()
