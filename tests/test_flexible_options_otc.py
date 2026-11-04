"""Testes unitários para Opções Flexíveis de Balcão B3 (FL-042)."""

from decimal import Decimal
from pathlib import Path
import unittest

from src.flexible_options_otc import (
    BarrierType,
    FlexibleOptionContract,
    FlexibleOptionsOtcEngine,
    FlexibleUnderlyingType,
    OptionType,
)


class TestFlexibleOptionsOtc(unittest.TestCase):
    def setUp(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_path = self.fixtures_dir / "flexible_options_otc.json"

    def test_fixture_loading_and_settlement(self):
        """Valida carregamento da fixture oficial e invariantes de soma-zero."""
        engine = FlexibleOptionsOtcEngine.from_json_fixture(self.fixture_path)
        summary = engine.summarize()

        self.assertEqual(summary.total_contracts, 5)
        self.assertTrue(summary.all_zero_sum_gross)
        self.assertTrue(summary.all_cashflow_balanced)

    def test_up_and_out_barrier_knockout_with_rebate(self):
        """Valida que uma opção Up-and-Out sofre Knock-Out ao atingir a barreira e recebe Rebate."""
        engine = FlexibleOptionsOtcEngine.from_json_fixture(self.fixture_path)
        # FLX-USD-2026-0002 atingiu 5.7800 em 2026-10-15 (barreira era 5.6500)
        settlement = engine.settle_contract("FLX-USD-2026-0002")

        self.assertTrue(settlement.is_knocked_out)
        self.assertFalse(settlement.is_active)
        self.assertTrue(settlement.rebate_applied)
        # 20 contratos * 50.000 * 0.0300 rebate = 30.000,00
        self.assertEqual(settlement.gross_holder_brl, Decimal("30000.00"))
        self.assertEqual(settlement.gross_writer_brl, Decimal("-30000.00"))
        self.assertTrue(settlement.is_zero_sum_gross)

    def test_up_and_out_cap_application(self):
        """Valida aplicação de Cap unitário em opção ativa."""
        engine = FlexibleOptionsOtcEngine.from_json_fixture(self.fixture_path)
        # FLX-USD-2026-0001: barreira 5.8500 (máxima foi 5.7800 -> ativa)
        # strike 5.2000, liquidação 5.7200 -> ganho bruto 0.5200, mas cap é 0.4000
        settlement = engine.settle_contract("FLX-USD-2026-0001")

        self.assertTrue(settlement.is_active)
        self.assertFalse(settlement.is_knocked_out)
        self.assertEqual(settlement.raw_intrinsic_unit, Decimal("0.5200"))
        self.assertEqual(settlement.capped_intrinsic_unit, Decimal("0.4000"))
        # 10 contratos * 50.000 * 0.4000 = 200.000,00
        self.assertEqual(settlement.gross_holder_brl, Decimal("200000.00"))
        self.assertEqual(settlement.gross_writer_brl, Decimal("-200000.00"))
        self.assertTrue(settlement.is_zero_sum_gross)

    def test_down_and_in_activation(self):
        """Valida ativação de opção Down-and-In quando o índice toca a barreira inferior."""
        engine = FlexibleOptionsOtcEngine.from_json_fixture(self.fixture_path)
        # FLX-IBOV-2026-0003: barreira 122.000, mínima foi 121.450 -> Ativa!
        settlement = engine.settle_contract("FLX-IBOV-2026-0003")

        self.assertTrue(settlement.is_active)
        self.assertEqual(settlement.raw_intrinsic_unit, Decimal("4500.0000"))
        # 50 contratos * 1.00 * 4500 = 225.000,00
        self.assertEqual(settlement.gross_holder_brl, Decimal("225000.00"))


if __name__ == "__main__":
    unittest.main()
