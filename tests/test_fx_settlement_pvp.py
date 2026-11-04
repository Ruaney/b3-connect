"""
Suíte de Testes Unitários — B3 Câmara de Câmbio & PvP (FL-060)
=============================================================
Validação de liquidação atômica PvP, eliminação de risco Herstatt,
paridade cambial BRL/USD e fechamento multilateral soma-zero.
"""

import unittest
from decimal import Decimal
from pathlib import Path
from src.fx_settlement_pvp import (
    FxSettlementEngine,
    FxContract,
    FxContractType,
    FxLegStatus,
    FxSettlementStatus,
    FxSettlementError
)


class TestFxSettlementPvP(unittest.TestCase):

    def setUp(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "fx_settlement_pvp.json"

    def test_load_from_fixture(self):
        engine = FxSettlementEngine.load_from_fixture(self.fixture_file)
        self.assertEqual(len(engine.contracts), 4)

    def test_pvp_spot_execution(self):
        engine = FxSettlementEngine.load_from_fixture(self.fixture_file)
        receipt = engine.execute_pvp_settlement("FX-SPOT-2026-001")
        self.assertEqual(receipt.amount_usd_settled, Decimal("10000000.00"))
        self.assertEqual(receipt.amount_brl_settled, Decimal("56540000.00")) # 10M * 5.6540
        self.assertTrue(receipt.pvp_atomic_verified)
        self.assertTrue(receipt.parity_conserved)

    def test_pvp_ndf_settlement(self):
        engine = FxSettlementEngine.load_from_fixture(self.fixture_file)
        # NDF: 25M USD contratado a 5.5200, PTAX final 5.6850 (dif = +0.1650)
        # Ajuste = 25M * 0.1650 = 4.125.000,00 BRL
        receipt = engine.execute_pvp_settlement("FX-NDF-2026-004")
        self.assertEqual(receipt.amount_usd_settled, Decimal("0.00"))
        self.assertEqual(receipt.amount_brl_settled, Decimal("4125000.00"))
        self.assertTrue(receipt.pvp_atomic_verified)

    def test_pvp_failure_prevention_herstatt_risk(self):
        engine = FxSettlementEngine()
        contract = FxContract(
            contract_id="FX-UNCONFIRMED",
            contract_type=FxContractType.SPOT_FX,
            trade_date="2026-11-04",
            settlement_date="2026-11-04",
            buyer_participant_id="P1",
            seller_participant_id="P2",
            notional_usd=Decimal("1000000.00"),
            fx_rate=Decimal("5.5000"),
            usd_leg_status=FxLegStatus.CONFIRMED,
            brl_leg_status=FxLegStatus.PENDING # Falha no BRL!
        )
        engine.register_contract(contract)
        with self.assertRaises(FxSettlementError):
            engine.execute_pvp_settlement("FX-UNCONFIRMED")
        
        c = engine.contracts["FX-UNCONFIRMED"]
        self.assertEqual(c.settlement_status, FxSettlementStatus.FAILED)

    def test_multilateral_conservation(self):
        engine = FxSettlementEngine.load_from_fixture(self.fixture_file)
        # Contratos 1 e 2 são operações opostas de mesmo valor entre BRADESCO e JPMORGAN
        balances = engine.calculate_multilateral_net_balances()
        self.assertIn("PART-BRADESCO-01", balances)
        self.assertIn("PART-JPMORGAN-02", balances)
        # Para os participantes 01 e 02, o net é 0.00
        self.assertEqual(balances["PART-BRADESCO-01"].net_usd, Decimal("0.00"))
        self.assertEqual(balances["PART-BRADESCO-01"].net_brl, Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
