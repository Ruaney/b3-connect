"""Testes unitários do Simulador do Ciclo Diário dos 7 Módulos da B3 (b3_market_simulator.py)."""

from decimal import Decimal
import json
import unittest

from src.b3_market_simulator import B3MarketSimulator, DailySimulationResult, SimulationPhase


class TestB3MarketSimulator(unittest.TestCase):
    """Bateria de testes para o simulador do dia de mercado completo da B3."""

    def setUp(self) -> None:
        self.simulator = B3MarketSimulator(trading_date="2026-11-03")

    def test_run_full_market_day_default(self) -> None:
        """Verifica a execução ponta a ponta com parâmetros institucionais padrão."""
        result = self.simulator.run_full_market_day()

        self.assertIsInstance(result, DailySimulationResult)
        self.assertTrue(result.simulation_id.startswith("SIM-B3-"))
        self.assertEqual(result.trading_date, "2026-11-03")

        # Verifica volumes financeiros apurados
        self.assertGreater(result.total_equities_volume, Decimal("0.00"))
        self.assertGreater(result.total_balcao_volume, Decimal("0.00"))
        self.assertGreater(result.total_spb_settled, Decimal("0.00"))
        self.assertGreater(result.total_drex_atomic_settled, Decimal("0.00"))
        self.assertGreater(result.total_tariffs_collected, Decimal("0.00"))

        # Verifica métricas de salvaguarda e BSM
        self.assertEqual(result.bsm_alerts_prevented, 1)
        self.assertEqual(len(result.imbarq_files_created), 6)
        self.assertIn("IMBARQ004_Financeiro", result.imbarq_files_created)
        self.assertIn("IMBARQ012_CustodiaFisica", result.imbarq_files_created)

        # Verifica que todas as leis formais dos 7 módulos foram invocadas
        expected_laws = [
            "LAW-001", "LAW-002", "LAW-006", "LAW-007", "LAW-008",
            "LAW-011", "LAW-018", "LAW-041", "LAW-047", "LAW-090",
            "LAW-104A", "LAW-104B", "LAW-105A", "LAW-106A",
        ]
        for law in expected_laws:
            self.assertIn(law, result.laws_verified)

        # Verifica fases temporais cronológicas
        phases = [e.phase for e in result.events]
        self.assertIn(SimulationPhase.PRE_OPENING, phases)
        self.assertIn(SimulationPhase.MARKET_OPEN, phases)
        self.assertIn(SimulationPhase.INTRADAY_RISK, phases)
        self.assertIn(SimulationPhase.CLOSING_CROSS, phases)
        self.assertIn(SimulationPhase.MULTILATERAL_NETTING, phases)
        self.assertIn(SimulationPhase.SPB_DREX_SETTLEMENT, phases)
        self.assertIn(SimulationPhase.CSD_RECONCILIATION, phases)

        # Performance do motor (deve rodar em milissegundos)
        self.assertLess(result.execution_duration_ms, 150.0)

    def test_to_dict_and_summary_text(self) -> None:
        """Verifica a serialização JSON e a formatação textual do relatório."""
        result = self.simulator.run_full_market_day(
            base_shares=50_000,
            base_price=Decimal("40.00"),
            notional_balcao=Decimal("5_000_000.00"),
        )
        d = result.to_dict()
        self.assertIn("simulation_id", d)
        self.assertIn("metrics", d)
        self.assertIn("events", d)

        # Verifica validade estrita do JSON
        json_str = json.dumps(d)
        self.assertTrue(len(json_str) > 500)

        summary = result.summary_text()
        self.assertIn("B3 SIMULADOR MULTI-MÓDULO", summary)
        self.assertIn("Negociação Bolsa (PUMA)", summary)
        self.assertIn("Registro Balcão (NoMe)", summary)
        self.assertIn("Liquidação Bruta SPB (STR)", summary)
        self.assertIn("LAW-106A", summary)

    def test_custom_parameters_scaling(self) -> None:
        """Verifica proporcionalidade dos cálculos sob diferentes volumes."""
        result_small = self.simulator.run_full_market_day(
            base_shares=10_000,
            base_price=Decimal("10.00"),
            notional_balcao=Decimal("1_000_000.00"),
        )
        result_large = self.simulator.run_full_market_day(
            base_shares=100_000,
            base_price=Decimal("10.00"),
            notional_balcao=Decimal("10_000_000.00"),
        )

        self.assertLess(result_small.total_equities_volume, result_large.total_equities_volume)
        self.assertLess(result_small.total_balcao_volume, result_large.total_balcao_volume)
        self.assertLess(result_small.total_tariffs_collected, result_large.total_tariffs_collected)


if __name__ == "__main__":
    unittest.main()
