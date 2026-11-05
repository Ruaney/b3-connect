"""Testes unitários e de invariantes do Módulo de Warrants Agrícolas e Armazéns Credenciados B3 (FL-078).

Valida conformidade estrita com:
- Lei nº 11.076/2004 (CDA e WA) e Decreto nº 1.102/1903.
- Lei nº 14.711/2023 (Marco Legal das Garantias).
- Manual de Risco e Garantias da Câmara B3 (CORE).
"""

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.agricultural_warrant_collateral import (
    AgriculturalCommodity,
    AgriculturalInvariantViolationError,
    AgriculturalWarrant,
    AgriculturalWarrantCollateralEngine,
    AgriculturalWarrantError,
    AgriculturalWarrantValidationError,
    AuctionExecutionError,
    ExecutionResult,
    HaircutBreakdown,
    MarginCheckResult,
    MarginReleaseViolationError,
    QualityMetrics,
    UnitOfMeasure,
    ValuationResult,
    Warehouse,
    WarehouseAccreditationStatus,
    WarehouseNotAccreditedError,
    WarrantProtestError,
    WarrantStateTransitionError,
    WarrantStatus,
)


class TestAgriculturalWarrantCollateral(unittest.TestCase):
    """Bateria de testes unitários do motor de warrants e garantias em commodities agrícolas B3."""

    def setUp(self) -> None:
        self.fixture_path = (
            Path(__file__).resolve().parent.parent / "fixtures" / "agricultural_warrant_collateral.json"
        )
        self.engine = AgriculturalWarrantCollateralEngine.load_from_fixture(self.fixture_path)
        self.ref_date = date(2026, 11, 4)

    def test_fixture_loading_and_invariants(self) -> None:
        """Garante que a fixture carregue com integridade e todas as invariantes formais passem."""
        self.assertEqual(len(self.engine.warehouses), 2)
        self.assertEqual(len(self.engine.warrants), 3)

        inv_report = self.engine.verify_formal_invariants(self.ref_date)
        for name, holds in inv_report.items():
            self.assertTrue(holds, f"Invariante formal quebrada: {name}")

    def test_warehouse_validation_and_unaccredited_error(self) -> None:
        """Valida que armazéns suspensos ou com seguro vencido sejam barrados."""
        wh_expired = Warehouse(
            code="WH-EXP-01",
            name="ARMAZEM EXPIRADO LTDA",
            cnpj="11222333000144",
            state="MT",
            city="Sinop",
            accreditation_number="MAPA-AG-2020-001",
            accreditation_status=WarehouseAccreditationStatus.ACCREDITED,
            static_capacity_tons=Decimal("50000"),
            insurance_policy_number="POL-EXP-001",
            insurance_company="SEGURADORA X",
            insurance_coverage_amount=Decimal("10000000.00"),
            insurance_expiration_date=date(2025, 12, 31),  # Vencido em relação a ref_date (2026-11-04)
            storage_monthly_fee_per_unit=Decimal("0.50"),
        )
        with self.assertRaises(WarehouseNotAccreditedError):
            wh_expired.validate(as_of_date=self.ref_date)

        wh_suspended = Warehouse(
            code="WH-SUSP-01",
            name="ARMAZEM SUSPENSO LTDA",
            cnpj="11222333000155",
            state="MT",
            city="Sinop",
            accreditation_number="MAPA-AG-2020-002",
            accreditation_status=WarehouseAccreditationStatus.SUSPENDED,
            static_capacity_tons=Decimal("50000"),
            insurance_policy_number="POL-SUSP-001",
            insurance_company="SEGURADORA Y",
            insurance_coverage_amount=Decimal("10000000.00"),
            insurance_expiration_date=date(2027, 12, 31),
            storage_monthly_fee_per_unit=Decimal("0.50"),
        )
        with self.assertRaises(WarehouseNotAccreditedError):
            wh_suspended.validate()

    def test_physical_shrinkage_conservation(self) -> None:
        """Garante a conservação física da quebra técnica de armazenagem."""
        warrant_id = "CDA-WA-SOY-001"
        val = self.engine.calculate_valuation(warrant_id, as_of_date=self.ref_date)

        # Invariante: initial == effective + loss
        warrant = self.engine.get_warrant(warrant_id)
        self.assertEqual(
            warrant.initial_quantity,
            val.effective_physical_quantity + val.technical_loss_quantity,
        )
        self.assertGreater(val.technical_loss_quantity, Decimal("0.0000"))
        self.assertGreater(val.effective_physical_quantity, Decimal("0.0000"))

    def test_bundle_split_and_reunite(self) -> None:
        """Valida o desmembramento (split) do CDA/WA e posterior unificação mediante quitação."""
        warrant_id = "CDA-WA-SOY-001"
        warrant = self.engine.get_warrant(warrant_id)
        self.assertTrue(warrant.is_bundled)

        # Desmembra WA para constituir penhor de crédito agrícola
        self.engine.split_bundle(
            warrant_id=warrant_id,
            wa_creditor_name="BANCO SANTANDER BRASIL S.A.",
            wa_creditor_document="90400888000142",
            debt_principal=Decimal("1500000.00"),
            annual_interest_rate=Decimal("0.1350"),
        )
        self.assertFalse(warrant.is_bundled)
        self.assertEqual(warrant.wa_debt_principal, Decimal("1500000.00"))

        # Re-unifica comprovando quitação do WA
        self.engine.reunite_bundle(
            warrant_id=warrant_id,
            settlement_proof_ref="RECIBO-QUITACAO-SANTANDER-2026-9912",
        )
        self.assertTrue(warrant.is_bundled)
        self.assertEqual(warrant.wa_debt_principal, Decimal("0.00"))

    def test_b3_clearing_pledge_and_margin_evaluation(self) -> None:
        """Valida a vinculação do warrant à câmara e o cálculo da margem de garantia B3."""
        warrant_id = "CDA-WA-SOY-001"
        clearing_acc = "CLEARING-CONTA-0089-9"
        future_code = "SJCZ26"

        # Vincula como colateral
        self.engine.pledge_to_b3_clearing(
            warrant_id=warrant_id,
            clearing_account=clearing_acc,
            associated_future=future_code,
        )
        warrant = self.engine.get_warrant(warrant_id)
        self.assertEqual(warrant.status, WarrantStatus.PLEDGED_TO_B3)
        self.assertEqual(warrant.b3_pledged_account, clearing_acc)

        # Avaliação de margem com margem requerida inferior ao colateral -> Compliant
        margin_res = self.engine.evaluate_clearing_margin(
            clearing_account=clearing_acc,
            required_margin=Decimal("2000000.00"),
            as_of_date=self.ref_date,
        )
        self.assertTrue(margin_res.is_margin_compliant)
        self.assertEqual(margin_res.margin_call_amount, Decimal("0.00"))
        self.assertGreater(margin_res.allocated_collateral_value, Decimal("2000000.00"))

        # Avaliação com margem requerida que excede o colateral -> Margin Call
        margin_res_deficit = self.engine.evaluate_clearing_margin(
            clearing_account=clearing_acc,
            required_margin=Decimal("15000000.00"),
            as_of_date=self.ref_date,
        )
        self.assertFalse(margin_res_deficit.is_margin_compliant)
        self.assertGreater(margin_res_deficit.margin_call_amount, Decimal("0.00"))
        self.assertEqual(warrant.status, WarrantStatus.IN_MARGIN_CALL)

    def test_safe_release_guardrail_prevents_margin_deficit(self) -> None:
        """Garante que a desoneração de garantias seja barrada se o colateral remanescente for insuficiente."""
        warrant_id = "CDA-WA-SOY-001"
        clearing_acc = "CLEARING-CONTA-0089-9"

        self.engine.pledge_to_b3_clearing(
            warrant_id=warrant_id,
            clearing_account=clearing_acc,
            associated_future="SJCZ26",
        )

        # Tentar liberar com exigência residual de R$ 500.000,00 sem outro colateral -> Rejeição
        with self.assertRaises(MarginReleaseViolationError):
            self.engine.release_from_b3_clearing(
                warrant_id=warrant_id,
                required_margin_after_release=Decimal("500000.00"),
                as_of_date=self.ref_date,
            )

        # Liberar com exigência residual de R$ 0,00 -> Sucesso
        released = self.engine.release_from_b3_clearing(
            warrant_id=warrant_id,
            required_margin_after_release=Decimal("0.00"),
            as_of_date=self.ref_date,
        )
        self.assertEqual(released.status, WarrantStatus.RELEASED)
        self.assertIsNone(released.b3_pledged_account)

    def test_protest_and_auction_execution_waterfall(self) -> None:
        """Valida o waterfall financeiro estrito da excussão física em leilão público."""
        warrant_id = "CDA-WA-COF-003"  # Já está protestado na fixture
        warrant = self.engine.get_warrant(warrant_id)
        self.assertEqual(warrant.status, WarrantStatus.PROTESTED)

        auction_date = date(2026, 8, 20)
        auction_price = Decimal("1200.0000")  # Preço obtido no pregão de leilão

        exec_res = self.engine.execute_physical_auction(
            warrant_id=warrant_id,
            auction_date=auction_date,
            auction_unit_price=auction_price,
            warehouse_expenses_override=Decimal("25000.00"),
        )

        # Verificação do Waterfall: Bruto == Despesas + WA + Remanescente CDA
        self.assertTrue(exec_res.conservation_verified)
        self.assertEqual(
            exec_res.gross_auction_proceeds,
            exec_res.warehouse_expenses + exec_res.wa_debt_settled + exec_res.cda_surplus_refunded,
        )
        self.assertEqual(warrant.status, WarrantStatus.EXECUTED)
        self.assertTrue(exec_res.is_debt_fully_satisfied)
        self.assertGreater(exec_res.cda_surplus_refunded, Decimal("0.00"))

    def test_premature_protest_rejected(self) -> None:
        """Garante que o protesto antes do vencimento do título seja rejeitado."""
        warrant = self.engine.get_warrant("CDA-WA-SOY-001")
        # Vencimento é 2027-03-15; tentativa de protesto em 2026-11-04 deve falhar
        with self.assertRaises(WarrantProtestError):
            self.engine.record_protest(
                warrant_id=warrant.id,
                protest_date=date(2026, 11, 4),
                notary_name="Cartório de Teste",
            )


if __name__ == "__main__":
    unittest.main()
