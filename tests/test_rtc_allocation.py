"""Testes automatizados para o motor de Alocação e Repasse RTC B3 (FL-008)."""

from decimal import Decimal
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH independentemente da forma de execução
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.rtc_allocation import (
    AllocationBatch,
    AllocationError,
    AllocationInstruction,
    AllocationRole,
    AllocationStatus,
    AllocationSumMismatchError,
    AllocationValidationError,
    AllocationValidator,
    ParentTrade,
    ReasonCode,
    ValidationResult,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestRtcAllocation(unittest.TestCase):
    """Bateria de testes unitários do motor de alocação e repasse RTC."""

    def setUp(self) -> None:
        self.validator = AllocationValidator(strict_price=True)
        self.valid_fixture_path = FIXTURES_DIR / "rtc_allocation_valid.json"
        self.invalid_sum_fixture_path = FIXTURES_DIR / "rtc_allocation_invalid_sum.json"

    def test_valid_allocation_from_fixture(self) -> None:
        """Valida o desdobramento de 10.000 ações em 3 fundos a partir da fixture oficial."""
        batch = AllocationBatch.from_file(self.valid_fixture_path)

        # 1. Verificações do trade pai
        self.assertEqual(batch.parent_trade.trade_ref_id, "TRD-20261103-008891")
        self.assertEqual(batch.parent_trade.symbol, "PETR4")
        self.assertEqual(batch.parent_trade.executing_broker, 88)
        self.assertEqual(batch.parent_trade.parent_trade_quantity, Decimal("10000"))
        self.assertEqual(batch.parent_trade.parent_trade_price, Decimal("38.50"))

        # 2. Verificações das instruções no lote
        self.assertEqual(len(batch.instructions), 3)
        self.assertEqual(batch.total_allocated_quantity(), Decimal("10000"))
        self.assertTrue(batch.is_balanced())

        # 3. Execução da validação
        res: ValidationResult = self.validator.validate_batch(batch)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.reason_code, ReasonCode.OK)
        self.assertEqual(len(res.errors), 0)

        # 4. Processamento da transição de estados
        processed = self.validator.process_batch(batch)
        self.assertEqual(processed.status, AllocationStatus.ACCEPTED)
        self.assertEqual(processed.reason_code, ReasonCode.OK)

        for instr in processed.instructions:
            self.assertEqual(instr.status, AllocationStatus.ACCEPTED)
            self.assertEqual(instr.reason_code, ReasonCode.OK)
            self.assertEqual(instr.allocation_price, Decimal("38.50"))

    def test_invalid_sum_exceeds_parent_from_fixture(self) -> None:
        """Valida que sobre-alocação (11.000 > 10.000) na fixture é rejeitada estritamente."""
        batch = AllocationBatch.from_file(self.invalid_sum_fixture_path)

        self.assertEqual(batch.parent_trade.parent_trade_quantity, Decimal("10000"))
        self.assertEqual(batch.total_allocated_quantity(), Decimal("11000"))
        self.assertFalse(batch.is_balanced())

        # Validação sem disparar erro
        res = self.validator.validate_batch(batch)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.reason_code, ReasonCode.ERR_OVER_ALLOCATION)
        self.assertTrue(any("Sobre-alocação detectada" in e for e in res.errors))

        # Processamento e conferência de transição para REJECTED
        processed = self.validator.process_batch(batch, raise_on_error=False)
        self.assertEqual(processed.status, AllocationStatus.REJECTED)
        self.assertEqual(processed.reason_code, ReasonCode.ERR_OVER_ALLOCATION)

        for instr in processed.instructions:
            self.assertEqual(instr.status, AllocationStatus.REJECTED)
            self.assertEqual(instr.reason_code, ReasonCode.ERR_OVER_ALLOCATION)

        # Verificação do disparo de exceção especializada
        with self.assertRaises(AllocationSumMismatchError) as ctx:
            self.validator.process_batch(batch, raise_on_error=True)

        self.assertEqual(ctx.exception.parent_quantity, Decimal("10000"))
        self.assertEqual(ctx.exception.allocated_quantity, Decimal("11000"))
        self.assertEqual(ctx.exception.reason_code, ReasonCode.ERR_OVER_ALLOCATION)

    def test_under_allocation_rejection(self) -> None:
        """Valida que sub-alocação (soma menor que o trade pai) é categorizada como erro."""
        parent = ParentTrade(
            trade_ref_id="TRD-UNDER-01",
            symbol="VALE3",
            executing_broker=105,
            parent_trade_quantity=Decimal("5000"),
            parent_trade_price=Decimal("62.30"),
        )
        instructions = [
            AllocationInstruction(
                allocation_id="AL-01",
                trade_ref_id="TRD-UNDER-01",
                target_account=2001,
                target_clearing_member=105,
                allocated_quantity=Decimal("2000"),
                allocation_price=Decimal("62.30"),
            ),
            AllocationInstruction(
                allocation_id="AL-02",
                trade_ref_id="TRD-UNDER-01",
                target_account=2002,
                target_clearing_member=105,
                allocated_quantity=Decimal("2500"),
                allocation_price=Decimal("62.30"),
            ),
        ]
        # Total alocado: 4.500 vs 5.000 pai
        batch = AllocationBatch(
            batch_id="BATCH-UNDER-01",
            parent_trade=parent,
            instructions=instructions,
        )

        self.assertFalse(batch.is_balanced())
        res = self.validator.validate_batch(batch)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.reason_code, ReasonCode.ERR_UNDER_ALLOCATION)
        self.assertTrue(any("Sub-alocação detectada" in e for e in res.errors))

        with self.assertRaises(AllocationSumMismatchError):
            self.validator.process_batch(batch, raise_on_error=True)

    def test_non_positive_quantity_rejection(self) -> None:
        """Valida que parcelas com quantidade zero ou negativa são rejeitadas."""
        parent = ParentTrade(
            trade_ref_id="TRD-ZERO-01",
            symbol="ITUB4",
            executing_broker=88,
            parent_trade_quantity=Decimal("1000"),
            parent_trade_price=Decimal("35.00"),
        )
        instructions = [
            AllocationInstruction(
                allocation_id="AL-Z1",
                trade_ref_id="TRD-ZERO-01",
                target_account=3001,
                target_clearing_member=88,
                allocated_quantity=Decimal("1000"),
                allocation_price=Decimal("35.00"),
            ),
            AllocationInstruction(
                allocation_id="AL-Z2",
                trade_ref_id="TRD-ZERO-01",
                target_account=3002,
                target_clearing_member=88,
                allocated_quantity=Decimal("0"),
                allocation_price=Decimal("35.00"),
            ),
        ]
        batch = AllocationBatch(
            batch_id="BATCH-ZERO-01",
            parent_trade=parent,
            instructions=instructions,
        )

        res = self.validator.validate_batch(batch)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.reason_code, ReasonCode.ERR_QTY_NON_POSITIVE)

    def test_price_mismatch_rejection(self) -> None:
        """Valida que divergência de preço da parcela em relação ao pai é rejeitada em modo estrito."""
        parent = ParentTrade(
            trade_ref_id="TRD-PX-01",
            symbol="B3SA3",
            executing_broker=88,
            parent_trade_quantity=Decimal("1000"),
            parent_trade_price=Decimal("12.50"),
        )
        instructions = [
            AllocationInstruction(
                allocation_id="AL-PX1",
                trade_ref_id="TRD-PX-01",
                target_account=4001,
                target_clearing_member=88,
                allocated_quantity=Decimal("1000"),
                allocation_price=Decimal("12.55"),  # Divergente de 12.50
            ),
        ]
        batch = AllocationBatch(
            batch_id="BATCH-PX-01",
            parent_trade=parent,
            instructions=instructions,
        )

        res = self.validator.validate_batch(batch)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.reason_code, ReasonCode.ERR_PRICE_MISMATCH)

        with self.assertRaises(AllocationValidationError) as ctx:
            self.validator.process_batch(batch, raise_on_error=True)
        self.assertEqual(ctx.exception.reason_code, ReasonCode.ERR_PRICE_MISMATCH)

    def test_trade_ref_mismatch_rejection(self) -> None:
        """Valida que instrução apontando para trade_ref diferente é rejeitada."""
        parent = ParentTrade(
            trade_ref_id="TRD-REF-CORRECT",
            symbol="BBAS3",
            executing_broker=105,
            parent_trade_quantity=Decimal("500"),
            parent_trade_price=Decimal("28.00"),
        )
        instructions = [
            AllocationInstruction(
                allocation_id="AL-REF-1",
                trade_ref_id="TRD-REF-WRONG",
                target_account=5001,
                target_clearing_member=105,
                allocated_quantity=Decimal("500"),
                allocation_price=Decimal("28.00"),
            ),
        ]
        batch = AllocationBatch(batch_id="BATCH-REF-01", parent_trade=parent, instructions=instructions)

        res = self.validator.validate_batch(batch)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.reason_code, ReasonCode.ERR_TRADE_REF_MISMATCH)

    def test_duplicate_allocation_id_rejection(self) -> None:
        """Valida que IDs de alocação duplicados no mesmo lote são rejeitados."""
        parent = ParentTrade(
            trade_ref_id="TRD-DUP-01",
            symbol="WEGE3",
            executing_broker=88,
            parent_trade_quantity=Decimal("2000"),
            parent_trade_price=Decimal("54.00"),
        )
        instructions = [
            AllocationInstruction(
                allocation_id="ALLOC-DUP",
                trade_ref_id="TRD-DUP-01",
                target_account=6001,
                target_clearing_member=88,
                allocated_quantity=Decimal("1000"),
                allocation_price=Decimal("54.00"),
            ),
            AllocationInstruction(
                allocation_id="ALLOC-DUP",  # ID duplicado
                trade_ref_id="TRD-DUP-01",
                target_account=6002,
                target_clearing_member=88,
                allocated_quantity=Decimal("1000"),
                allocation_price=Decimal("54.00"),
            ),
        ]
        batch = AllocationBatch(batch_id="BATCH-DUP-01", parent_trade=parent, instructions=instructions)

        res = self.validator.validate_batch(batch)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.reason_code, ReasonCode.ERR_DUPLICATE_ALLOCATION_ID)

    def test_empty_batch_rejection(self) -> None:
        """Valida que lote vazio (sem instruções) é rejeitado."""
        parent = ParentTrade(
            trade_ref_id="TRD-EMPTY-01",
            symbol="VALE3",
            executing_broker=88,
            parent_trade_quantity=Decimal("1000"),
            parent_trade_price=Decimal("60.00"),
        )
        batch = AllocationBatch(batch_id="BATCH-EMPTY-01", parent_trade=parent, instructions=[])

        res = self.validator.validate_batch(batch)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.reason_code, ReasonCode.ERR_EMPTY_BATCH)

    def test_decimal_precision_preservation(self) -> None:
        """Valida que frações decimais não sofrem erros de ponto flutuante binário."""
        parent = ParentTrade(
            trade_ref_id="TRD-DEC-01",
            symbol="DI1F27",
            executing_broker=88,
            parent_trade_quantity=Decimal("100.000"),
            parent_trade_price=Decimal("105432.18"),
        )
        instructions = [
            AllocationInstruction(
                allocation_id="DEC-01",
                trade_ref_id="TRD-DEC-01",
                target_account=7001,
                target_clearing_member=88,
                allocated_quantity=Decimal("33.333"),
                allocation_price=Decimal("105432.18"),
            ),
            AllocationInstruction(
                allocation_id="DEC-02",
                trade_ref_id="TRD-DEC-01",
                target_account=7002,
                target_clearing_member=88,
                allocated_quantity=Decimal("33.333"),
                allocation_price=Decimal("105432.18"),
            ),
            AllocationInstruction(
                allocation_id="DEC-03",
                trade_ref_id="TRD-DEC-01",
                target_account=7003,
                target_clearing_member=88,
                allocated_quantity=Decimal("33.334"),
                allocation_price=Decimal("105432.18"),
            ),
        ]
        batch = AllocationBatch(batch_id="BATCH-DEC-01", parent_trade=parent, instructions=instructions)

        self.assertEqual(batch.total_allocated_quantity(), Decimal("100.000"))
        self.assertTrue(batch.is_balanced())
        res = self.validator.validate_batch(batch)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.reason_code, ReasonCode.OK)

    def test_serialization_roundtrip(self) -> None:
        """Valida ciclo completo de serialização e desserialização JSON."""
        batch_orig = AllocationBatch.from_file(self.valid_fixture_path)
        json_repr = batch_orig.to_json()

        batch_restored = AllocationBatch.from_json(json_repr)
        self.assertEqual(batch_orig.batch_id, batch_restored.batch_id)
        self.assertEqual(
            batch_orig.parent_trade.parent_trade_quantity,
            batch_restored.parent_trade.parent_trade_quantity,
        )
        self.assertEqual(len(batch_orig.instructions), len(batch_restored.instructions))

        for orig_i, rest_i in zip(batch_orig.instructions, batch_restored.instructions):
            self.assertEqual(orig_i.allocation_id, rest_i.allocation_id)
            self.assertEqual(orig_i.allocated_quantity, rest_i.allocated_quantity)
            self.assertEqual(orig_i.allocation_price, rest_i.allocation_price)
            self.assertEqual(orig_i.target_account, rest_i.target_account)

    def test_manual_accept_reject_helpers(self) -> None:
        """Valida os métodos auxiliares de aceite e rejeição direta de instrução."""
        instr = AllocationInstruction(
            allocation_id="MANUAL-01",
            trade_ref_id="TRD-MAN-01",
            target_account=8001,
            target_clearing_member=105,
            allocated_quantity=Decimal("100"),
            allocation_price=Decimal("25.00"),
        )
        self.assertEqual(instr.status, AllocationStatus.SUBMITTED)
        self.assertIsNone(instr.reason_code)

        instr.accept()
        self.assertEqual(instr.status, AllocationStatus.ACCEPTED)
        self.assertEqual(instr.reason_code, ReasonCode.OK)

        instr.reject(ReasonCode.ERR_INVALID_ACCOUNT)
        self.assertEqual(instr.status, AllocationStatus.REJECTED)
        self.assertEqual(instr.reason_code, ReasonCode.ERR_INVALID_ACCOUNT)


if __name__ == "__main__":
    unittest.main()
