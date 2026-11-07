"""Testes unitários e invariantes de compensação multilateral NDF FX (FL-095)."""

from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.ndf_fx_multilateral_clearing import (
    ContractSide,
    CurrencyPair,
    NdfContract,
    NdfStatus,
    NdfFxMultilateralClearingEngine,
)


class TestNdfFxMultilateralClearing(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = NdfFxMultilateralClearingEngine()
        # Banco A comprou USD 1M a 5.4500
        self.c1 = NdfContract(
            contract_id="NDF-USD-001",
            participant_id="PART-BANK-A",
            currency_pair=CurrencyPair.USDBRL,
            side=ContractSide.BUY,
            notional_foreign=Decimal("1000000.00"),
            forward_rate=Decimal("5.4500"),
            trade_date="2026-11-01",
            maturity_date="2026-12-01",
        )
        # Banco B vendeu USD 1M a 5.4500
        self.c2 = NdfContract(
            contract_id="NDF-USD-002",
            participant_id="PART-BANK-B",
            currency_pair=CurrencyPair.USDBRL,
            side=ContractSide.SELL,
            notional_foreign=Decimal("1000000.00"),
            forward_rate=Decimal("5.4500"),
            trade_date="2026-11-01",
            maturity_date="2026-12-01",
        )
        self.engine.register_contract(self.c1)
        self.engine.register_contract(self.c2)

    def test_daily_variation_margin_zero_sum(self) -> None:
        # PTAX sobe para 5.5000 (+R$ 0,0500 por USD)
        curves = {CurrencyPair.USDBRL: Decimal("5.5000")}
        adjustments = self.engine.calculate_daily_variation_margin("2026-11-02", curves)

        # Banco A ganha R$ 50.000,00; Banco B paga R$ 50.000,00
        self.assertEqual(adjustments["PART-BANK-A"], Decimal("50000.00"))
        self.assertEqual(adjustments["PART-BANK-B"], Decimal("-50000.00"))
        # Soma multilateral deve ser rigorosamente 0.00
        self.assertEqual(sum(adjustments.values()), Decimal("0.00"))

    def test_settlement_at_maturity_with_tax(self) -> None:
        # Liquida Banco A contra PTAX final 5.6000 (Ganho bruto: 1M * (5.60 - 5.45) = 150.000)
        receipt = self.engine.settle_at_maturity("NDF-USD-001", fixing_ptax=Decimal("5.6000"))
        self.assertEqual(Decimal(receipt["gross_amount"]), Decimal("150000.00"))
        self.assertEqual(Decimal(receipt["tax_withheld"]), Decimal("22500.00")) # 15% IRRF
        self.assertEqual(Decimal(receipt["net_amount"]), Decimal("127500.00"))
        self.assertEqual(self.c1.status, NdfStatus.SETTLED)

    def test_invariants_and_serialization(self) -> None:
        curves = {CurrencyPair.USDBRL: Decimal("5.5200")}
        self.engine.calculate_daily_variation_margin("2026-11-02", curves)
        self.engine.settle_at_maturity("NDF-USD-001", fixing_ptax=Decimal("5.5200"))
        self.engine.settle_at_maturity("NDF-USD-002", fixing_ptax=Decimal("5.5200"))

        audit = self.engine.verify_invariants()
        self.assertTrue(audit["all_invariants_hold"])
        self.assertTrue(audit["multilateral_zero_sum_verified"])
        self.assertTrue(audit["settlement_financial_conservation_verified"])

        d = self.engine.to_dict()
        restored = NdfFxMultilateralClearingEngine.from_dict(d)
        self.assertEqual(len(restored.contracts), 2)


if __name__ == "__main__":
    unittest.main()
