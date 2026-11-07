"""Testes unitários do Simulador de Caos e Estresse da B3 (b3_chaos_simulator.py)."""

from decimal import Decimal
import json
import unittest

from src.b3_chaos_simulator import B3ChaosSimulator, ChaosScenarioType, ChaosSimulationResult


class TestB3ChaosSimulator(unittest.TestCase):
    """Bateria de testes para os 4 cenários extremos de crise do B3 Chaos Simulator."""

    def setUp(self) -> None:
        self.simulator = B3ChaosSimulator(trading_date="2026-11-03")

    def test_circuit_breaker_scenario(self) -> None:
        """Verifica a simulação de Circuit Breaker Nível 1 no PUMA."""
        result = self.simulator.run_scenario(ChaosScenarioType.CIRCUIT_BREAKER)
        self.assertTrue(result.is_resilient)
        self.assertTrue(result.system_recovered)
        self.assertEqual(result.scenario, ChaosScenarioType.CIRCUIT_BREAKER)
        self.assertIn("LAW-093", result.laws_verified)
        self.assertIn("LAW-104C", result.laws_verified)
        self.assertEqual(len(result.steps), 5)
        self.assertIn("CIRCUIT_BREAKER_RESILIENT", result.verdict)

    def test_clearing_default_scenario(self) -> None:
        """Verifica a simulação de default e absorção pela Default Waterfall."""
        result = self.simulator.run_scenario(ChaosScenarioType.CLEARING_DEFAULT)
        self.assertTrue(result.is_resilient)
        self.assertTrue(result.system_recovered)
        self.assertEqual(result.total_financial_contained, Decimal("500_000_000.00"))
        self.assertIn("LAW-106B", result.laws_verified)
        self.assertEqual(len(result.steps), 6)
        self.assertIn("DEFAULT_CONTAINED_CPMI_IOSCO", result.verdict)

    def test_receivables_conflict_scenario(self) -> None:
        """Verifica a resolução de conflito de gravames por prelazia legal."""
        result = self.simulator.run_scenario(ChaosScenarioType.RECEIVABLES_LIEN_CONFLICT)
        self.assertTrue(result.is_resilient)
        self.assertTrue(result.system_recovered)
        self.assertIn("LAW-018", result.laws_verified)
        self.assertIn("LAW-031", result.laws_verified)
        self.assertEqual(len(result.steps), 4)

    def test_spb_network_disruption_scenario(self) -> None:
        """Verifica o failover e liquidação assíncrona do SPB."""
        result = self.simulator.run_scenario(ChaosScenarioType.SPB_NETWORK_DISRUPTION)
        self.assertTrue(result.is_resilient)
        self.assertTrue(result.system_recovered)
        self.assertIn("LAW-007", result.laws_verified)
        self.assertIn("LAW-106C", result.laws_verified)
        self.assertEqual(len(result.steps), 4)

    def test_json_and_summary_text_all_scenarios(self) -> None:
        """Verifica que todos os cenários geram JSON válido e relatórios em texto limpos."""
        for scenario in ChaosScenarioType:
            res = self.simulator.run_scenario(scenario)
            d = res.to_dict()
            self.assertIn("scenario", d)
            self.assertIn("steps", d)
            json_str = json.dumps(d)
            self.assertTrue(len(json_str) > 200)

            summary = res.summary_text()
            self.assertIn("B3 CHAOS SIMULATOR", summary)
            self.assertIn("Veredito de Resiliência", summary)


if __name__ == "__main__":
    unittest.main()
