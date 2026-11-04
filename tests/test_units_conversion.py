"""Testes unitários para Composição e Desmembramento de Units na Depositária B3 (FL-043)."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from src.units_conversion import (
    BookSuspensionError,
    InsufficientFreeCustodyError,
    UnitConversionInstruction,
    UnitOperationType,
    UnitRatioMismatchError,
    UnitsConversionEngine,
)


class TestUnitsConversion(unittest.TestCase):
    def setUp(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_path = self.fixtures_dir / "units_conversion_requests.json"

    def test_fixture_loading_and_execution(self):
        """Valida execução em lote de todas as instruções da fixture oficial."""
        engine, instructions = UnitsConversionEngine.from_json_fixture(self.fixture_path)
        self.assertEqual(len(engine.programs), 5)
        self.assertEqual(len(engine.accounts), 5)
        self.assertEqual(len(instructions), 6)

        receipts = engine.process_all(instructions)
        self.assertEqual(len(receipts), 6)

        summary = engine.summarize()
        self.assertTrue(summary.all_conservations_satisfied)
        self.assertEqual(summary.total_instructions_processed, 6)

    def test_ratio_mismatch_rejected(self):
        """Valida que proporção incorreta de ON/PN é rejeitada deterministicamente."""
        engine, _ = UnitsConversionEngine.from_json_fixture(self.fixture_path)
        # SANB11 requer ratio 1:1, passar 3000 Units com 2000 ON deve falhar
        bad_inst = UnitConversionInstruction(
            instruction_id="INST-FAIL-RATIO",
            unit_ticker="SANB11",
            operation_type=UnitOperationType.COMPOSITION,
            participant_code="0114",
            investor_account="660010-5",
            unit_quantity=3000,
            on_quantity=2000,  # Deveria ser 3000
            pn_quantity=3000,
            request_date=date(2026, 11, 3),
        )
        with self.assertRaises(UnitRatioMismatchError):
            engine.process_instruction(bad_inst)

    def test_book_suspension_blocked(self):
        """Valida que conversão durante suspensão de livros (AGE/AGO) é bloqueada."""
        engine, _ = UnitsConversionEngine.from_json_fixture(self.fixture_path)
        prog = engine.get_program("SANB11")
        prog.book_suspended = True
        prog.suspension_reason = "AGO_DIVIDENDOS"

        inst = UnitConversionInstruction(
            instruction_id="INST-FAIL-SUSP",
            unit_ticker="SANB11",
            operation_type=UnitOperationType.COMPOSITION,
            participant_code="0114",
            investor_account="660010-5",
            unit_quantity=100,
            on_quantity=100,
            pn_quantity=100,
            request_date=date(2026, 11, 3),
        )
        with self.assertRaises(BookSuspensionError):
            engine.process_instruction(inst)

    def test_insufficient_free_balance_blocked(self):
        """Valida que tentar compor com saldo livre insuficiente é bloqueado."""
        engine, _ = UnitsConversionEngine.from_json_fixture(self.fixture_path)
        # A conta 660010-5 tem 10.000 ON livre, solicitar 20.000 Units
        inst = UnitConversionInstruction(
            instruction_id="INST-FAIL-BALANCE",
            unit_ticker="SANB11",
            operation_type=UnitOperationType.COMPOSITION,
            participant_code="0114",
            investor_account="660010-5",
            unit_quantity=20000,
            on_quantity=20000,
            pn_quantity=20000,
            request_date=date(2026, 11, 3),
        )
        with self.assertRaises(InsufficientFreeCustodyError):
            engine.process_instruction(inst)


if __name__ == "__main__":
    unittest.main()
