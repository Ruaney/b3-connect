"""Testes unitários e de invariantes do split de resgate de fundos CVM 175 (FL-096)."""

from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.funds_redemption_split_settlement import (
    FundClassState,
    FundClassType,
    FundsRedemptionSplitEngine,
    RedemptionStatus,
)


class TestFundsRedemptionSplitSettlement(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FundsRedemptionSplitEngine()
        self.fund = FundClassState(
            fund_id="FND-ALPHA-01",
            class_name="Alpha Macro FIC FIM",
            fund_type=FundClassType.MULTIMERCADO,
            total_quotas=Decimal("100000.00000000"),
            quota_value=Decimal("15.00000000"),
            high_water_mark=Decimal("14.50000000"),
            admin_fee_annual_rate=Decimal("0.0200"),
            performance_fee_rate=Decimal("0.2000"),
        )
        self.engine.register_fund(self.fund)

    def test_request_and_cotizacao_with_hwm(self) -> None:
        # Resgate de 1.000 cotas a cota 15.00 (excede HWM de 14.50 em 0.50 por cota)
        order = self.engine.request_redemption("RED-001", "FND-ALPHA-01", "INV-001", Decimal("1000.00000000"))
        self.assertEqual(order.status, RedemptionStatus.REQUESTED)

        quoted = self.engine.execute_cotizacao_and_split("RED-001", current_quota_value=Decimal("15.00000000"))
        self.assertEqual(quoted.status, RedemptionStatus.QUOTED)
        self.assertEqual(quoted.gross_amount, Decimal("15000.00"))
        # Performance: 1.000 * 0.50 * 20% = R$ 100,00
        self.assertEqual(quoted.performance_fee_amount, Decimal("100.00"))
        # Verifica cotas abatidas
        self.assertEqual(self.fund.total_quotas, Decimal("99000.00000000"))
        # HWM atualizada para 15.00
        self.assertEqual(self.fund.high_water_mark, Decimal("15.00000000"))

    def test_performance_fee_zero_below_hwm(self) -> None:
        # Se cota cai para 14.00 (abaixo de HWM 14.50), taxa de performance deve ser zero
        order = self.engine.request_redemption("RED-002", "FND-ALPHA-01", "INV-002", Decimal("500.00000000"))
        quoted = self.engine.execute_cotizacao_and_split("RED-002", current_quota_value=Decimal("14.00000000"))
        self.assertEqual(quoted.performance_fee_amount, Decimal("0.00"))

    def test_settlement_split_zero_sum(self) -> None:
        self.engine.request_redemption("RED-001", "FND-ALPHA-01", "INV-001", Decimal("1000.00000000"))
        quoted = self.engine.execute_cotizacao_and_split("RED-001", current_quota_value=Decimal("15.00000000"))
        log = self.engine.settle_redemption("RED-001")

        # Soma das partes == Bruto debitado do fundo
        total_parts = quoted.net_credit_amount + quoted.admin_fee_amount + quoted.performance_fee_amount + quoted.tax_retention_irrf
        self.assertEqual(quoted.gross_amount, total_parts)

        # Invariantes
        audit = self.engine.verify_invariants()
        self.assertTrue(audit["all_invariants_hold"])
        self.assertTrue(audit["split_settlement_zero_sum_verified"])
        self.assertTrue(audit["high_water_mark_inviolability_verified"])

        # Serialização
        d = self.engine.to_dict()
        restored = FundsRedemptionSplitEngine.from_dict(d)
        self.assertIn("FND-ALPHA-01", restored.funds)


if __name__ == "__main__":
    unittest.main()
