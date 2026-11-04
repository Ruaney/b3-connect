"""
Suíte de Testes Unitários — B3 CSD & BACEN DREX (FL-061)
=======================================================
Validação de tokenização (Mint/Lock), des-tokenização (Burn/Unlock),
liquidação atômica DvP On-Chain e conservação absoluta de custódia CSD/DREX.
"""

import unittest
from decimal import Decimal
from pathlib import Path
from src.drex_tokenized_assets import (
    DrexDvpEngine,
    CsdCustodyAccount,
    DrexWallet,
    DrexOperationType,
    DrexTokenizationError
)


class TestDrexTokenizedAssets(unittest.TestCase):

    def setUp(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "drex_tokenized_assets.json"

    def test_load_from_fixture(self):
        engine = DrexDvpEngine.load_from_fixture(self.fixture_file)
        self.assertEqual(len(engine.csd_accounts), 2)
        self.assertEqual(len(engine.drex_wallets), 2)
        self.assertTrue(engine.verify_global_custody_conservation("LTN2028"))

    def test_tokenize_mint_flow(self):
        engine = DrexDvpEngine.load_from_fixture(self.fixture_file)
        receipt = engine.tokenize_tpft(
            csd_account_id="CSD-ACC-ITAU-001",
            symbol="LTN2028",
            wallet_address="0x1111111111111111111111111111111111111111",
            quantity=5000
        )
        self.assertEqual(receipt.operation_type, DrexOperationType.TOKENIZE_MINT)
        self.assertTrue(receipt.atomic_verified)
        self.assertTrue(receipt.custody_conserved)

        acc = engine.csd_accounts["CSD-ACC-ITAU-001:LTN2028"]
        wallet = engine.drex_wallets["0x1111111111111111111111111111111111111111"]
        self.assertEqual(acc.available_quantity, 45000)
        self.assertEqual(acc.encumbered_drex_quantity, 15000)
        self.assertEqual(wallet.tpft_balances["LTN2028"], 15000)
        self.assertTrue(engine.verify_global_custody_conservation("LTN2028"))

    def test_detokenize_burn_flow(self):
        engine = DrexDvpEngine.load_from_fixture(self.fixture_file)
        receipt = engine.detokenize_tpft(
            wallet_address="0x2222222222222222222222222222222222222222",
            symbol="LTN2028",
            csd_account_id="CSD-ACC-BRAD-002",
            quantity=2000
        )
        self.assertEqual(receipt.operation_type, DrexOperationType.DETOKENIZE_BURN)
        self.assertTrue(receipt.atomic_verified)

        acc = engine.csd_accounts["CSD-ACC-BRAD-002:LTN2028"]
        wallet = engine.drex_wallets["0x2222222222222222222222222222222222222222"]
        self.assertEqual(acc.available_quantity, 32000)
        self.assertEqual(acc.encumbered_drex_quantity, 3000)
        self.assertEqual(wallet.tpft_balances["LTN2028"], 3000)
        self.assertTrue(engine.verify_global_custody_conservation("LTN2028"))

    def test_atomic_onchain_dvp_transfer(self):
        engine = DrexDvpEngine.load_from_fixture(self.fixture_file)
        # Carteira 1 (ITAU) vende 1.000 LTN2028 a R$ 950,00 cada para Carteira 2 (BRAD)
        # Total financeiro = 950.000,00 Real Digital
        receipt = engine.execute_dvp_transfer(
            sender_wallet_address="0x1111111111111111111111111111111111111111",
            receiver_wallet_address="0x2222222222222222222222222222222222222222",
            symbol="LTN2028",
            quantity=1000,
            unit_price_brl=Decimal("950.00")
        )
        self.assertEqual(receipt.operation_type, DrexOperationType.DVP_TRANSFER)
        self.assertEqual(receipt.total_financial_brl, Decimal("950000.00"))

        w1 = engine.drex_wallets["0x1111111111111111111111111111111111111111"]
        w2 = engine.drex_wallets["0x2222222222222222222222222222222222222222"]
        self.assertEqual(w1.tpft_balances["LTN2028"], 9000)
        self.assertEqual(w2.tpft_balances["LTN2028"], 6000)
        self.assertEqual(w1.real_digital_balance_brl, Decimal("25950000.00"))
        self.assertEqual(w2.real_digital_balance_brl, Decimal("39050000.00"))
        self.assertTrue(engine.verify_global_custody_conservation("LTN2028"))

    def test_dvp_insufficient_real_digital_rejection(self):
        engine = DrexDvpEngine.load_from_fixture(self.fixture_file)
        # Carteira 2 tentando comprar 100M BRL sem saldo suficiente
        with self.assertRaises(DrexTokenizationError):
            engine.execute_dvp_transfer(
                sender_wallet_address="0x1111111111111111111111111111111111111111",
                receiver_wallet_address="0x2222222222222222222222222222222222222222",
                symbol="LTN2028",
                quantity=1000,
                unit_price_brl=Decimal("100000.00")
            )


if __name__ == "__main__":
    unittest.main()
