"""Testes unitários e de invariantes para Empréstimo de Crédito Privado NoMe B3 (FL-094)."""

from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.structured_debt_lending import (
    CreditRating,
    LoanStatus,
    StructuredAssetType,
    StructuredDebtLoanContract,
    StructuredDebtLendingEngine,
)


class TestStructuredDebtLending(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = StructuredDebtLendingEngine()
        self.engine.set_initial_custody("ACC-LENDER-01", "DEB-VALE-2030", 1000)
        self.contract = StructuredDebtLoanContract(
            contract_id="LOAN-DEB-001",
            asset_code="DEB-VALE-2030",
            asset_type=StructuredAssetType.DEBENTURE,
            rating=CreditRating.AAA,
            unit_price=Decimal("1000.00"),
            quantity=500,
            lender_account="ACC-LENDER-01",
            borrower_account="ACC-BORROWER-01",
            annual_rate=Decimal("0.0250"),
            start_date="2026-11-01",
            due_date="2026-12-01",
        )

    def test_required_collateral_calculation(self) -> None:
        # Notional = 500 * 1000 = 500.000. Rating AAA = 10% Haircut -> Margem = 550.000
        self.assertEqual(self.contract.notional_value, Decimal("500000.00"))
        self.assertEqual(self.contract.required_collateral, Decimal("550000.00"))

    def test_loan_registration_and_physical_transfer(self) -> None:
        self.engine.register_loan(self.contract, initial_collateral=Decimal("550000.00"))
        self.assertEqual(self.engine.get_custody("ACC-LENDER-01", "DEB-VALE-2030"), 500)
        self.assertEqual(self.engine.get_custody("ACC-BORROWER-01", "DEB-VALE-2030"), 500)
        self.assertEqual(self.contract.status, LoanStatus.OPEN)

    def test_insufficient_collateral_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.engine.register_loan(self.contract, initial_collateral=Decimal("400000.00"))

    def test_interest_accrual_and_pass_through_events(self) -> None:
        self.engine.register_loan(self.contract, initial_collateral=Decimal("550000.00"))
        # Acumula juros de 21 dias úteis
        fee = self.engine.accrue_interest("LOAN-DEB-001", business_days=21)
        self.assertGreater(fee, Decimal("0.00"))
        self.assertEqual(self.contract.accrued_interest_remuneration, fee)

        # Repasse de cupom de juros de R$ 20,00 por debênture
        evts = self.engine.process_pass_through_event("DEB-VALE-2030", yield_amount_per_unit=Decimal("20.00"))
        self.assertEqual(len(evts), 1)
        self.assertEqual(Decimal(evts[0]["yield_passed"]), Decimal("10000.00"))
        self.assertEqual(self.contract.total_yield_passed_through, Decimal("10000.00"))

    def test_return_loan_and_invariants(self) -> None:
        self.engine.register_loan(self.contract, initial_collateral=Decimal("550000.00"))
        self.engine.accrue_interest("LOAN-DEB-001", business_days=10)
        receipt = self.engine.return_loan("LOAN-DEB-001")

        self.assertEqual(receipt["status"], "TERMINATED_NORMAL")
        self.assertEqual(receipt["returned_quantity"], 500)
        self.assertEqual(self.engine.get_custody("ACC-LENDER-01", "DEB-VALE-2030"), 1000)
        self.assertEqual(self.engine.get_custody("ACC-BORROWER-01", "DEB-VALE-2030"), 0)

        # Invariantes
        audit = self.engine.verify_invariants()
        self.assertTrue(audit["all_invariants_hold"])
        self.assertTrue(audit["physical_custody_conservation_verified"])
        self.assertTrue(audit["non_negativity_verified"])

        # Serialização
        d = self.engine.to_dict()
        restored = StructuredDebtLendingEngine.from_dict(d)
        self.assertIn("LOAN-DEB-001", restored.contracts)


if __name__ == "__main__":
    unittest.main()
