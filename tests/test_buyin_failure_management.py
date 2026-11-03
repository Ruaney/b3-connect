"""Testes unitários para Gestão de Falha de Entrega D+2 e Execução de Buy-In (FL-038)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.buyin_failure_management import (
    BuyInExecutionOrder,
    BuyInExecutionQuantityError,
    BuyInFailureEngine,
    BuyInValidationError,
    DeliveryFailureRecord,
    ExcessRegularizationError,
    FailureStatus,
    PenaltyAndDifferentialSummary,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "buyin_settlement_failures.json"
)


class TestBuyInFailureManagement(unittest.TestCase):
    """Suíte de testes determinísticos para o fluxo FL-038 (Falha D+2 e Buy-In D+3)."""

    def setUp(self) -> None:
        self.engine = BuyInFailureEngine()
        self.trade_date = date(2026, 10, 30)
        self.settlement_date = date(2026, 11, 3)
        self.buyin_date = date(2026, 11, 4)

    def test_shortfall_deterministic_identity(self) -> None:
        """Verifica a identidade Shortfall == Obrigação - Entregue - Regularizado_BTC."""
        shortfall = self.engine.calculate_shortfall(
            obligated_quantity=10000,
            delivered_quantity=4000,
            btc_regularized_quantity=2000,
        )
        self.assertEqual(shortfall, 4000)

        shortfall_zero = self.engine.calculate_shortfall(
            obligated_quantity=5000,
            delivered_quantity=1500,
            btc_regularized_quantity=3500,
        )
        self.assertEqual(shortfall_zero, 0)

    def test_penalty_calculation_and_minimum_floor(self) -> None:
        """Verifica o cálculo da multa operacional de falha D+2 e aplicação de piso."""
        # 6.000 ações em falha a R$ 36,00 = R$ 216.000,00 * 0,50% = R$ 1.080,00
        penalty = self.engine.calculate_penalty(
            failed_quantity=6000,
            original_sale_price=Decimal("36.00"),
            penalty_rate_pct=Decimal("0.50"),
        )
        self.assertEqual(penalty, Decimal("1080.00"))

        # Teste com piso mínimo regulamentar de R$ 250,00 quando a multa percentual é menor
        penalty_with_floor = self.engine.calculate_penalty(
            failed_quantity=100,
            original_sale_price=Decimal("10.00"),
            penalty_rate_pct=Decimal("0.50"),
            min_penalty_brl=Decimal("250.00"),
        )
        self.assertEqual(penalty_with_floor, Decimal("250.00"))

    def test_asymmetric_price_differential_allocation(self) -> None:
        """Valida que o vendedor arca com alta de preço, mas não recebe ganho na queda."""
        # Cenário 1: Preço de recompra (38,50) > Preço original (36,00) -> Débito de R$ 2,50/ação
        unit_diff, total_diff, favorable_ignored = (
            self.engine.calculate_price_differential(
                executed_quantity=4000,
                original_sale_price=Decimal("36.00"),
                buyin_execution_price=Decimal("38.50"),
            )
        )
        self.assertEqual(unit_diff, Decimal("2.50"))
        self.assertEqual(total_diff, Decimal("10000.00"))
        self.assertEqual(favorable_ignored, Decimal("0.00"))

        # Cenário 2: Preço de recompra (32,80) < Preço original (34,00) -> Diferencial zero
        unit_diff_low, total_diff_low, favorable_ignored_low = (
            self.engine.calculate_price_differential(
                executed_quantity=5000,
                original_sale_price=Decimal("34.00"),
                buyin_execution_price=Decimal("32.80"),
            )
        )
        self.assertEqual(unit_diff_low, Decimal("0.00"))
        self.assertEqual(total_diff_low, Decimal("0.00"))
        self.assertEqual(favorable_ignored_low, Decimal("6000.00"))

        # Cenário 3: Preço de recompra igual ao preço original (34,00 == 34,00)
        unit_eq, total_eq, fav_eq = self.engine.calculate_price_differential(
            executed_quantity=5000,
            original_sale_price=Decimal("34.00"),
            buyin_execution_price=Decimal("34.00"),
        )
        self.assertEqual(unit_eq, Decimal("0.00"))
        self.assertEqual(total_eq, Decimal("0.00"))
        self.assertEqual(fav_eq, Decimal("0.00"))

    def test_btc_regularization_partial_and_full_reversion(self) -> None:
        """Testa a reversão parcial e total da ordem de Buy-In mediante empréstimo BTC."""
        record = DeliveryFailureRecord(
            failure_id="FAIL-BTC-001",
            trade_id="TRD-BTC-001",
            participant_code="00105",
            seller_account="123456-7",
            ticker="WEGE3",
            isin="BRWEGEACNOR0",
            trade_date=self.trade_date,
            settlement_date=self.settlement_date,
            buyin_date=self.buyin_date,
            obligated_quantity=3000,
            delivered_quantity=1000,
            btc_regularized_quantity=0,
            original_sale_price=Decimal("40.00"),
        )
        self.assertEqual(record.initial_failure_quantity, 2000)
        self.assertEqual(record.shortfall_quantity, 2000)

        # Regularização parcial via BTC de 800 ações -> Shortfall cai para 1.200
        partial_rec = self.engine.apply_btc_regularization(
            record, additional_btc_quantity=800, btc_contract_id="BTC-WEGE-01"
        )
        self.assertEqual(partial_rec.btc_regularized_quantity, 800)
        self.assertEqual(partial_rec.shortfall_quantity, 1200)

        order_partial = self.engine.issue_buyin_order(partial_rec)
        self.assertEqual(order_partial.buyin_quantity, 1200)
        self.assertEqual(order_partial.status, FailureStatus.BUYIN_ISSUED_D3)

        # Regularização complementar via BTC das 1.200 restantes -> Reversão Total (Shortfall = 0)
        full_rec = self.engine.apply_btc_regularization(
            partial_rec, additional_btc_quantity=1200, btc_contract_id="BTC-WEGE-02"
        )
        self.assertEqual(full_rec.shortfall_quantity, 0)

        order_reverted, summary_reverted = self.engine.evaluate_failure_case(full_rec)
        self.assertTrue(order_reverted.is_reverted_by_btc)
        self.assertEqual(order_reverted.status, FailureStatus.REVERTED_BY_BTC)
        self.assertEqual(summary_reverted.shortfall_quantity, 0)
        self.assertEqual(summary_reverted.price_differential_amount_brl, Decimal("0.00"))
        # Multa sobre a falha original de D+2 (2.000 * 40,00 * 0,50% = R$ 400,00) permanece devida
        self.assertEqual(summary_reverted.penalty_amount_brl, Decimal("400.00"))
        self.assertEqual(
            summary_reverted.total_debited_from_seller_brl, Decimal("400.00")
        )

    def test_step_by_step_order_issuance_and_execution(self) -> None:
        """Testa a emissão da ordem de Buy-In em D+3 seguida de execução no mercado."""
        record = DeliveryFailureRecord(
            failure_id="FAIL-STEP-001",
            trade_id="TRD-STEP-001",
            participant_code="00114",
            seller_account="998811-2",
            ticker="BBAS3",
            isin="BRBBASACNOR3",
            trade_date=self.trade_date,
            settlement_date=self.settlement_date,
            buyin_date=self.buyin_date,
            obligated_quantity=4000,
            delivered_quantity=1000,
            btc_regularized_quantity=500,
            original_sale_price=Decimal("28.00"),
        )
        issued_order = self.engine.issue_buyin_order(record, buyin_order_id="BUYIN-BBAS-001")
        self.assertEqual(issued_order.buyin_quantity, 2500)
        self.assertEqual(issued_order.executed_quantity, 0)
        self.assertEqual(issued_order.status, FailureStatus.BUYIN_ISSUED_D3)

        executed_order = self.engine.execute_buyin_order(
            issued_order,
            executed_quantity=2500,
            execution_price=Decimal("29.40"),
        )
        self.assertEqual(executed_order.status, FailureStatus.BUYIN_EXECUTED)
        self.assertEqual(executed_order.remaining_quantity, 0)
        self.assertEqual(executed_order.unit_price_differential_brl, Decimal("1.40"))
        self.assertEqual(
            executed_order.price_differential_amount_brl, Decimal("3500.00")
        )

    def test_fixture_cases_end_to_end(self) -> None:
        """Valida todos os casos de falha de entrega do arquivo fixtures/buyin_settlement_failures.json."""
        self.assertTrue(FIXTURE_PATH.exists(), f"Fixture não encontrada: {FIXTURE_PATH}")
        raw_fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        expected_items = {
            item["failure_id"]: item for item in raw_fixture["failures"]
        }

        batch_result = self.engine.evaluate_fixture(FIXTURE_PATH)
        self.assertEqual(batch_result["total_cases"], 4)
        self.assertEqual(batch_result["total_obligated_quantity"], 35000)
        self.assertEqual(batch_result["total_delivered_quantity"], 9000)
        self.assertEqual(batch_result["total_initial_failure_quantity"], 26000)
        self.assertEqual(batch_result["total_btc_regularized_quantity"], 10000)
        self.assertEqual(batch_result["total_shortfall_quantity"], 16000)
        self.assertEqual(batch_result["total_buyin_executed_quantity"], 13000)
        self.assertEqual(batch_result["total_penalties_brl"], Decimal("4030.00"))
        self.assertEqual(
            batch_result["total_price_differentials_brl"], Decimal("15000.00")
        )
        self.assertEqual(
            batch_result["total_debited_from_sellers_brl"], Decimal("19030.00")
        )

        for summary in batch_result["summaries"]:
            self.assertIsInstance(summary, PenaltyAndDifferentialSummary)
            exp = expected_items[summary.failure_id]
            self.assertEqual(
                summary.shortfall_quantity, exp["expected_shortfall_quantity"]
            )
            self.assertEqual(
                summary.penalty_amount_brl,
                Decimal(exp["expected_penalty_amount_brl"]),
            )
            self.assertEqual(
                summary.price_differential_amount_brl,
                Decimal(exp["expected_price_differential_brl"]),
            )
            self.assertEqual(
                summary.total_debited_from_seller_brl,
                Decimal(exp["expected_total_debited_brl"]),
            )
            self.assertEqual(summary.status.value, exp["expected_status"])

            serialized = summary.to_dict()
            self.assertEqual(serialized["failure_id"], summary.failure_id)
            self.assertEqual(serialized["status"], exp["expected_status"])

    def test_validation_errors_and_invariants(self) -> None:
        """Verifica o disparo de exceções determinísticas para violações de domínio."""
        # Excesso de regularização BTC (Entregue 4.000 + BTC 7.000 > Obrigação 10.000)
        with self.assertRaises(ExcessRegularizationError):
            DeliveryFailureRecord(
                failure_id="FAIL-ERR-001",
                trade_id="TRD-ERR-001",
                participant_code="00105",
                seller_account="111111-1",
                ticker="PETR4",
                isin="BRPETRACNPR6",
                trade_date=self.trade_date,
                settlement_date=self.settlement_date,
                obligated_quantity=10000,
                delivered_quantity=4000,
                btc_regularized_quantity=7000,
                original_sale_price=Decimal("36.00"),
            )

        # Quantidade executada no Buy-In maior que o Shortfall pendente
        with self.assertRaises(BuyInExecutionQuantityError):
            DeliveryFailureRecord(
                failure_id="FAIL-ERR-002",
                trade_id="TRD-ERR-002",
                participant_code="00105",
                seller_account="111111-1",
                ticker="PETR4",
                isin="BRPETRACNPR6",
                trade_date=self.trade_date,
                settlement_date=self.settlement_date,
                obligated_quantity=10000,
                delivered_quantity=4000,
                btc_regularized_quantity=2000,
                original_sale_price=Decimal("36.00"),
                buyin_executed_quantity=5000,  # Shortfall é 4.000
                buyin_execution_price=Decimal("38.00"),
            )

        # Registro onde não houve falha (entregue == obrigação)
        with self.assertRaises(BuyInValidationError):
            DeliveryFailureRecord(
                failure_id="FAIL-ERR-003",
                trade_id="TRD-ERR-003",
                participant_code="00105",
                seller_account="111111-1",
                ticker="PETR4",
                isin="BRPETRACNPR6",
                trade_date=self.trade_date,
                settlement_date=self.settlement_date,
                obligated_quantity=1000,
                delivered_quantity=1000,
                original_sale_price=Decimal("36.00"),
            )


if __name__ == "__main__":
    unittest.main()
