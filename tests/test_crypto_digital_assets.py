"""
Suíte de Testes Unitários — B3 Digital Assets & Marco Legal dos Criptoativos (FL-075)
=====================================================================================
Validação de:
  1. Motor de custódia híbrida (Onboard/Lock CSD -> Mint On-Chain e Burn On-Chain -> Unlock CSD).
  2. Liquidação atômica DvP híbrida com trilhos STR e Pix e estorno (Two-Phase Commit).
  3. Prova de Reserva contínua (Proof-of-Reserve 1:1) com Árvore de Merkle e reconciliação matemática.
  4. Segregação patrimonial fiduciária (Art. 13-A da Lei nº 14.478/2022).
  5. Padrão ERC-3643 (ONCHAINID, KYC/AML e qualificação de investidores).
  6. Invariantes formais de conservação matemática.
"""

from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.crypto_digital_assets import (
    CryptoDigitalAssetsEngine,
    CsdEscrituralAccount,
    Erc3643Wallet,
    FiduciaryReserveAccount,
    DvpAtomicOrder,
    RwaAssetType,
    ComplianceStatus,
    DvpSettlementRail,
    DvpOrderStatus,
    BridgeActionType,
    CryptoDigitalAssetsError,
    ComplianceViolationError,
    ProofOfReserveBreachError,
    DvpSettlementError,
    AssetSegregationError,
    InsufficientBalanceError,
)
from src.invariants import assert_crypto_digital_assets_por_parity, InvariantViolationError


class TestCryptoDigitalAssets(unittest.TestCase):
    """Testes unitários e de integridade matemática do módulo crypto_digital_assets."""

    def setUp(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "crypto_digital_assets.json"
        self.engine = CryptoDigitalAssetsEngine.load_from_fixture(self.fixture_file)

    # --------------------------------------------------------------------------
    # 1. Carregamento e Estado Inicial
    # --------------------------------------------------------------------------

    def test_load_from_fixture(self):
        """Valida integridade estrutural e conservação da fixture oficial."""
        self.assertEqual(len(self.engine.csd_accounts), 3)
        self.assertEqual(len(self.engine.fiduciary_reserves), 3)
        self.assertEqual(len(self.engine.wallets), 4)

        # Invariantes formais globais
        inv = self.engine.verify_formal_invariants()
        self.assertTrue(inv["all_invariants_hold"])
        self.assertTrue(inv["proof_of_reserve_parity_1to1"])
        self.assertTrue(inv["csd_custody_conservation"])
        self.assertTrue(inv["fiduciary_segregation_inviolability"])
        self.assertTrue(inv["dvp_atomic_conservation"])

    def test_initial_proof_of_reserve_reconciliation(self):
        """Valida que todos os ativos iniciam com paridade 1:1 estrita e Merkle Root consistente."""
        # 1. Debênture Vale
        por_vale = self.engine.generate_proof_of_reserve("RWA-DEB-VALE-01")
        self.assertTrue(por_vale.is_fully_backed)
        self.assertEqual(por_vale.total_circulating_supply, Decimal("35000.00000000"))
        self.assertEqual(por_vale.total_segregated_backing, Decimal("35000.00000000"))
        self.assertEqual(por_vale.reserve_ratio, Decimal("1.00000000"))
        self.assertEqual(por_vale.discrepancy_delta, Decimal("0.00000000"))
        self.assertEqual(len(por_vale.merkle_root), 64)
        self.assertEqual(por_vale.leaves_count, 2)
        self.assertTrue(por_vale.segregation_audit_passed)

        # 2. CRI Agro
        por_agro = self.engine.generate_proof_of_reserve("RWA-CRI-AGRO-02")
        self.assertTrue(por_agro.is_fully_backed)
        self.assertEqual(por_agro.total_circulating_supply, Decimal("10000.00000000"))
        self.assertEqual(por_agro.total_segregated_backing, Decimal("10000.00000000"))
        self.assertEqual(por_agro.reserve_ratio, Decimal("1.00000000"))
        self.assertEqual(por_agro.discrepancy_delta, Decimal("0.00000000"))
        self.assertEqual(por_agro.leaves_count, 1)

    # --------------------------------------------------------------------------
    # 2. Ponte de Custódia Híbrida: Onboard (Lock CSD -> Mint On-Chain)
    # --------------------------------------------------------------------------

    def test_bridge_onboard_mint_success(self):
        """Valida o fluxo regular de tokenização com bloqueio fiduciário e mint ERC-3643."""
        initial_por = self.engine.generate_proof_of_reserve("RWA-DEB-VALE-01")
        self.assertEqual(initial_por.total_circulating_supply, Decimal("35000.00000000"))

        receipt = self.engine.bridge_onboard_mint(
            csd_account_id="CSD-ACC-ITAU-DEB01",
            wallet_address="0x1111111111111111111111111111111111111111",
            asset_code="RWA-DEB-VALE-01",
            units=Decimal("5000.00000000")
        )

        self.assertEqual(receipt["action"], BridgeActionType.ONBOARD_MINT.value)
        self.assertTrue(receipt["parity_1to1_preserved"])

        csd_acc = self.engine.csd_accounts["CSD-ACC-ITAU-DEB01:RWA-DEB-VALE-01"]
        self.assertEqual(csd_acc.available_units, Decimal("25000.00000000"))
        self.assertEqual(csd_acc.fiduciary_locked_units, Decimal("25000.00000000"))
        self.assertEqual(csd_acc.total_units, Decimal("50000.00000000"))

        wallet = self.engine.wallets["0x1111111111111111111111111111111111111111"]
        self.assertEqual(wallet.available_balance("RWA-DEB-VALE-01"), Decimal("25000.00000000"))

        # Nova verificação de PoR após mint: 40.000 tokens e 40.000 lastro
        updated_por = self.engine.generate_proof_of_reserve("RWA-DEB-VALE-01")
        self.assertTrue(updated_por.is_fully_backed)
        self.assertEqual(updated_por.total_circulating_supply, Decimal("40000.00000000"))
        self.assertEqual(updated_por.total_segregated_backing, Decimal("40000.00000000"))
        self.assertEqual(updated_por.discrepancy_delta, Decimal("0.00000000"))

    def test_bridge_onboard_mint_insufficient_available_balance(self):
        """Tentativa de tokenizar mais ativos do que o disponível na CSD deve falhar."""
        with self.assertRaises(InsufficientBalanceError):
            self.engine.bridge_onboard_mint(
                csd_account_id="CSD-ACC-ITAU-DEB01",
                wallet_address="0x1111111111111111111111111111111111111111",
                asset_code="RWA-DEB-VALE-01",
                units=Decimal("50000.00000000")  # Disponível é apenas 30.000
            )

    def test_bridge_onboard_mint_tax_id_mismatch(self):
        """Tentativa de mintar para carteira de investidor com Tax ID diferente deve falhar."""
        with self.assertRaises(ComplianceViolationError):
            self.engine.bridge_onboard_mint(
                csd_account_id="CSD-ACC-ITAU-DEB01",  # Tax ID Itaú
                wallet_address="0x2222222222222222222222222222222222222222",  # Tax ID Bradesco
                asset_code="RWA-DEB-VALE-01",
                units=Decimal("1000.00000000")
            )

    def test_bridge_onboard_mint_suspended_wallet(self):
        """Tentativa de mintar para carteira suspensa/revogada deve ser bloqueada."""
        wallet = self.engine.wallets["0x1111111111111111111111111111111111111111"]
        wallet.compliance_status = ComplianceStatus.SUSPENDED

        with self.assertRaises(ComplianceViolationError):
            self.engine.bridge_onboard_mint(
                csd_account_id="CSD-ACC-ITAU-DEB01",
                wallet_address="0x1111111111111111111111111111111111111111",
                asset_code="RWA-DEB-VALE-01",
                units=Decimal("1000.00000000")
            )

    # --------------------------------------------------------------------------
    # 3. Ponte de Custódia Híbrida: Redeem (Burn On-Chain -> Unlock CSD)
    # --------------------------------------------------------------------------

    def test_bridge_redeem_burn_success(self):
        """Valida a des-tokenização (queima na EVM e desbloqueio livre na CSD B3)."""
        receipt = self.engine.bridge_redeem_burn(
            wallet_address="0x2222222222222222222222222222222222222222",
            csd_account_id="CSD-ACC-BRAD-DEB01",
            asset_code="RWA-DEB-VALE-01",
            units=Decimal("5000.00000000")
        )

        self.assertEqual(receipt["action"], BridgeActionType.REDEEM_BURN.value)
        self.assertTrue(receipt["parity_1to1_preserved"])

        wallet = self.engine.wallets["0x2222222222222222222222222222222222222222"]
        self.assertEqual(wallet.available_balance("RWA-DEB-VALE-01"), Decimal("10000.00000000"))

        csd_acc = self.engine.csd_accounts["CSD-ACC-BRAD-DEB01:RWA-DEB-VALE-01"]
        self.assertEqual(csd_acc.available_units, Decimal("30000.00000000"))
        self.assertEqual(csd_acc.fiduciary_locked_units, Decimal("10000.00000000"))
        self.assertEqual(csd_acc.total_units, Decimal("40000.00000000"))

        # PoR após resgate: 30.000 tokens e 30.000 lastro
        updated_por = self.engine.generate_proof_of_reserve("RWA-DEB-VALE-01")
        self.assertTrue(updated_por.is_fully_backed)
        self.assertEqual(updated_por.total_circulating_supply, Decimal("30000.00000000"))
        self.assertEqual(updated_por.total_segregated_backing, Decimal("30000.00000000"))

    def test_bridge_redeem_burn_insufficient_onchain_balance(self):
        """Tentativa de queimar mais tokens do que o saldo on-chain disponível deve falhar."""
        with self.assertRaises(InsufficientBalanceError):
            self.engine.bridge_redeem_burn(
                wallet_address="0x2222222222222222222222222222222222222222",
                csd_account_id="CSD-ACC-BRAD-DEB01",
                asset_code="RWA-DEB-VALE-01",
                units=Decimal("20000.00000000")  # Possui apenas 15.000
            )

    # --------------------------------------------------------------------------
    # 4. Liquidação Atômica DvP Híbrida (STR e Pix)
    # --------------------------------------------------------------------------

    def test_hybrid_dvp_settlement_pix(self):
        """
        Executa DvP híbrido completo com liquidação financeira via Pix/SPI:
          1. Fase 1: Seller trava tokens em escrow on-chain.
          2. Fase 2: Pix quitado com End-to-End ID homologado.
          3. Liberação atômica dos tokens para o comprador.
        """
        seller_addr = "0x1111111111111111111111111111111111111111"
        buyer_addr = "0x4444444444444444444444444444444444444444"
        asset_code = "RWA-DEB-VALE-01"
        units = Decimal("5000.00000000")
        unit_price = Decimal("1020.50")

        # 1. Iniciação e Escrow On-Chain
        order = self.engine.initiate_hybrid_dvp(
            order_id="DVP-PIX-20261104-001",
            asset_code=asset_code,
            seller_wallet=seller_addr,
            buyer_wallet=buyer_addr,
            token_units=units,
            unit_price_brl=unit_price,
            settlement_rail=DvpSettlementRail.PIX
        )

        self.assertEqual(order.status, DvpOrderStatus.ESCROW_LOCKED)
        self.assertEqual(order.total_financial_brl, Decimal("5102500.00"))

        seller = self.engine.wallets[seller_addr]
        self.assertEqual(seller.available_balance(asset_code), Decimal("15000.00000000"))
        self.assertEqual(seller.escrowed_balance(asset_code), Decimal("5000.00000000"))
        self.assertEqual(seller.total_balance(asset_code), Decimal("20000.00000000"))

        # PoR durante o escrow deve permanecer exatamente 1:1 (tokens em escrow contam no passivo)
        por_during_escrow = self.engine.generate_proof_of_reserve(asset_code)
        self.assertTrue(por_during_escrow.is_fully_backed)
        self.assertEqual(por_during_escrow.total_circulating_supply, Decimal("35000.00000000"))

        # 2. Confirmação de Quitação no Pix (SPI / BACEN)
        pix_e2e_id = "E00038166202611041200abc12345678"
        settled_order = self.engine.confirm_and_settle_dvp(
            order_id="DVP-PIX-20261104-001",
            payment_end_to_end_id=pix_e2e_id,
            amount_paid_brl=Decimal("5102500.00")
        )

        self.assertEqual(settled_order.status, DvpOrderStatus.SETTLED)
        self.assertEqual(settled_order.payment_end_to_end_id, pix_e2e_id)
        self.assertIsNotNone(settled_order.settlement_tx_hash)

        # Saldos pós-liquidação
        buyer = self.engine.wallets[buyer_addr]
        self.assertEqual(seller.escrowed_balance(asset_code), Decimal("0.00000000"))
        self.assertEqual(seller.available_balance(asset_code), Decimal("15000.00000000"))
        self.assertEqual(buyer.available_balance(asset_code), Decimal("5000.00000000"))

        # PoR final permanece 1:1
        por_final = self.engine.generate_proof_of_reserve(asset_code)
        self.assertTrue(por_final.is_fully_backed)
        self.assertEqual(por_final.discrepancy_delta, Decimal("0.00000000"))

    def test_hybrid_dvp_settlement_str(self):
        """Executa DvP híbrido com liquidação financeira via STR (LBTR / TED)."""
        seller_addr = "0x2222222222222222222222222222222222222222"
        buyer_addr = "0x4444444444444444444444444444444444444444"
        asset_code = "RWA-DEB-VALE-01"
        units = Decimal("2000.00000000")
        unit_price = Decimal("1000.00")

        order = self.engine.initiate_hybrid_dvp(
            order_id="DVP-STR-20261104-002",
            asset_code=asset_code,
            seller_wallet=seller_addr,
            buyer_wallet=buyer_addr,
            token_units=units,
            unit_price_brl=unit_price,
            settlement_rail=DvpSettlementRail.STR
        )

        str_control_num = "STR20261104-09876543-TED"
        settled_order = self.engine.confirm_and_settle_dvp(
            order_id="DVP-STR-20261104-002",
            payment_end_to_end_id=str_control_num,
            amount_paid_brl=Decimal("2000000.00")
        )

        self.assertEqual(settled_order.status, DvpOrderStatus.SETTLED)
        buyer = self.engine.wallets[buyer_addr]
        self.assertEqual(buyer.available_balance(asset_code), Decimal("2000.00000000"))

    def test_hybrid_dvp_mismatched_payment_amount_rejection(self):
        """Tentativa de liquidar ordem DvP com valor financeiro diferente deve falhar."""
        order = self.engine.initiate_hybrid_dvp(
            order_id="DVP-MISMATCH-003",
            asset_code="RWA-DEB-VALE-01",
            seller_wallet="0x1111111111111111111111111111111111111111",
            buyer_wallet="0x4444444444444444444444444444444444444444",
            token_units=Decimal("1000.00000000"),
            unit_price_brl=Decimal("1000.00"),
            settlement_rail=DvpSettlementRail.PIX
        )

        with self.assertRaises(DvpSettlementError):
            self.engine.confirm_and_settle_dvp(
                order_id="DVP-MISMATCH-003",
                payment_end_to_end_id="E00038166202611041200abc12345678",
                amount_paid_brl=Decimal("999999.00")  # Menor que R$ 1.000.000,00
            )

        # Ordem permanece em escrow seguro
        self.assertEqual(order.status, DvpOrderStatus.ESCROW_LOCKED)

    def test_hybrid_dvp_cancellation_and_rollback(self):
        """Valida que o cancelamento por timeout reverte os tokens do escrow para o vendedor."""
        seller_addr = "0x1111111111111111111111111111111111111111"
        asset_code = "RWA-DEB-VALE-01"

        order = self.engine.initiate_hybrid_dvp(
            order_id="DVP-CANCEL-004",
            asset_code=asset_code,
            seller_wallet=seller_addr,
            buyer_wallet="0x4444444444444444444444444444444444444444",
            token_units=Decimal("3000.00000000"),
            unit_price_brl=Decimal("1000.00"),
            settlement_rail=DvpSettlementRail.PIX
        )

        seller = self.engine.wallets[seller_addr]
        self.assertEqual(seller.available_balance(asset_code), Decimal("17000.00000000"))
        self.assertEqual(seller.escrowed_balance(asset_code), Decimal("3000.00000000"))

        # Cancelamento
        cancelled = self.engine.cancel_or_expire_dvp("DVP-CANCEL-004", reason="SPI_TIMEOUT")
        self.assertEqual(cancelled.status, DvpOrderStatus.CANCELLED_TIMEOUT)

        # Tokens restaurados integralmente no saldo disponível
        self.assertEqual(seller.available_balance(asset_code), Decimal("20000.00000000"))
        self.assertEqual(seller.escrowed_balance(asset_code), Decimal("0.00000000"))

    def test_dvp_anti_double_spending(self):
        """Vendedor não pode comprometer em nova ordem tokens já travados em escrow."""
        seller_addr = "0x3333333333333333333333333333333333333333"
        asset_code = "RWA-CRI-AGRO-02"  # Saldo total: 10.000

        # Ordem 1: Trava 8.000
        self.engine.initiate_hybrid_dvp(
            order_id="DVP-LOCK-01",
            asset_code=asset_code,
            seller_wallet=seller_addr,
            buyer_wallet="0x4444444444444444444444444444444444444444",
            token_units=Decimal("8000.00000000"),
            unit_price_brl=Decimal("500.00"),
            settlement_rail=DvpSettlementRail.PIX
        )

        # Ordem 2: Tentativa de travar mais 3.000 (saldo livre restante é apenas 2.000)
        with self.assertRaises(InsufficientBalanceError):
            self.engine.initiate_hybrid_dvp(
                order_id="DVP-LOCK-02",
                asset_code=asset_code,
                seller_wallet=seller_addr,
                buyer_wallet="0x4444444444444444444444444444444444444444",
                token_units=Decimal("3000.00000000"),
                unit_price_brl=Decimal("500.00"),
                settlement_rail=DvpSettlementRail.PIX
            )

    # --------------------------------------------------------------------------
    # 5. Red Teaming, Casos Limítrofes e Invariantes Formais
    # --------------------------------------------------------------------------

    def test_adversarial_unbacked_supply_detection(self):
        """Injeção forçada de supply on-chain sem lastro CSD deve ser capturada pelo Proof of Reserve."""
        wallet = self.engine.wallets["0x1111111111111111111111111111111111111111"]
        # Injeta 10.000 tokens a descoberto
        wallet.balances["RWA-DEB-VALE-01"] += Decimal("10000.00000000")

        por = self.engine.generate_proof_of_reserve("RWA-DEB-VALE-01")
        self.assertFalse(por.is_fully_backed)
        self.assertEqual(por.discrepancy_delta, Decimal("-10000.00000000"))
        self.assertNotEqual(por.reserve_ratio, Decimal("1.00000000"))

        inv = self.engine.verify_formal_invariants("RWA-DEB-VALE-01")
        self.assertFalse(inv["all_invariants_hold"])
        self.assertFalse(inv["proof_of_reserve_parity_1to1"])

        with self.assertRaises(InvariantViolationError):
            assert_crypto_digital_assets_por_parity(
                por.total_segregated_backing,
                por.total_circulating_supply,
                "RWA-DEB-VALE-01"
            )

    def test_merkle_tree_tamper_evidence(self):
        """Qualquer alteração infinitesimal em saldos altera deterministicamente o Merkle Root."""
        root_before, _ = self.engine.calculate_merkle_root("RWA-DEB-VALE-01")

        # Modifica 0.00000001 de unidade em uma carteira
        wallet = self.engine.wallets["0x1111111111111111111111111111111111111111"]
        wallet.balances["RWA-DEB-VALE-01"] += Decimal("0.00000001")

        root_after, _ = self.engine.calculate_merkle_root("RWA-DEB-VALE-01")
        self.assertNotEqual(root_before, root_after)

    def test_fiduciary_segregation_inviolability(self):
        """Reserva fiduciária não-segregada (comunicável) causa violação do Art. 13-A da Lei 14.478/2022."""
        with self.assertRaises(AssetSegregationError):
            FiduciaryReserveAccount(
                reserve_id="RES-INVALID",
                custodian_participant_id="PART-TEST",
                asset_code="RWA-DEB-VALE-01",
                segregated_backing_units=Decimal("1000.00000000"),
                fiduciary_deposit_account="CC-001",
                is_bankruptcy_remote=False  # Violação legal
            )


if __name__ == "__main__":
    unittest.main()
