"""
Suíte de Testes Unitários — B3 CSD & BACEN DREX Fase 2 (FL-105)
================================================================
Validação da Emissão Primária de Ativos de Renda Fixa Privada Tokenizados (RWA),
Smart Contracts Simulados ERC-1155 e ERC-3643, Liquidação Atômica Bilateral DvP
(Direct EVM e HTLC), Gestão de Proventos On-Chain e Invariante LAW-111.
"""

from decimal import Decimal
import hashlib
from pathlib import Path
import time
import unittest

from src.drex_rwa_primary_issuance import (
    AllocationRecord,
    BidStatus,
    BookbuildingBid,
    CorporateActionEvent,
    CorporateActionStatus,
    CorporateActionType,
    DrexRwaBookbuildingError,
    DrexRwaComplianceError,
    DrexRwaCorporateActionError,
    DrexRwaError,
    DrexRwaHtlcError,
    DrexRwaInsufficientBalanceError,
    DrexRwaInvalidStateError,
    DrexRwaPrimaryIssuanceEngine,
    DvpSettlementReceipt,
    Erc1155MultiTokenContract,
    Erc1155TokenMetadata,
    Erc3643ComplianceManager,
    Erc3643ComplianceRule,
    HtlcDvpContract,
    HtlcStatus,
    IdentityClaim,
    IdentityClaimType,
    InvestorProfile,
    IssuanceSettlementMode,
    OfferingStatus,
    OnChainIdentity,
    PaymentDistributionRecord,
    RealDigitalTokenContract,
    RwaAssetType,
    RwaPrimaryIssuanceOffering,
    RwaTranche,
)
from src.invariants import (
    assert_drex_rwa_primary_issuance_invariants,
    InvariantViolationError,
)


class TestDrexRwaPrimaryIssuance(unittest.TestCase):
    """Testes formais de conformidade, bookbuilding, liquidação DvP e proventos RWA no Drex."""

    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "drex_rwa_primary_issuance.json"
        self.engine = DrexRwaPrimaryIssuanceEngine.load_from_fixture(self.fixture_file)

    # --------------------------------------------------------------------------
    # 1. CARGA DE FIXTURE E INVARIANTES INICIAIS
    # --------------------------------------------------------------------------

    def test_fixture_loading_and_invariants(self) -> None:
        """Verifica a carga íntegra da fixture e a validação das invariantes iniciais."""
        self.assertGreaterEqual(len(self.engine.offerings), 2)
        self.assertIn("OFF-DEB-2026-001", self.engine.offerings)
        self.assertIn("OFF-CRI-2027-002", self.engine.offerings)

        # Saldos CBDC e ERC-1155
        deb_meta = self.engine.rwa_tokens.metadata[1001]
        self.assertEqual(deb_meta.circulating_units, 50000)
        self.assertEqual(deb_meta.current_vnu, Decimal("950.00"))

        # Validação estrita das leis
        inv_results = self.engine.verify_invariants()
        self.assertTrue(inv_results["LAW-111_primary_dvp_atomic_quantity"])
        self.assertTrue(inv_results["LAW-111_primary_dvp_financial_parity"])
        self.assertTrue(inv_results["LAW-111_coupon_conservation"])
        self.assertTrue(inv_results["LAW-089C_token_non_negativity"])
        self.assertTrue(inv_results["LAW-3643_regulatory_compliance"])

    # --------------------------------------------------------------------------
    # 2. SERIALIZAÇÃO COMPLETA TO_DICT / FROM_DICT
    # --------------------------------------------------------------------------

    def test_serialization_roundtrip(self) -> None:
        """Testa serialização bidirecional do motor sem perda de estado."""
        data = self.engine.to_dict()
        rebuilt = DrexRwaPrimaryIssuanceEngine.from_dict(data)

        self.assertEqual(len(rebuilt.offerings), len(self.engine.offerings))
        self.assertEqual(len(rebuilt.dvp_receipts), len(self.engine.dvp_receipts))
        self.assertEqual(len(rebuilt.corporate_actions), len(self.engine.corporate_actions))

        # Re-validação de invariantes no motor reconstruído
        inv_results = rebuilt.verify_invariants()
        self.assertTrue(inv_results["LAW-111_primary_dvp_atomic_quantity"])
        self.assertTrue(inv_results["LAW-111_coupon_conservation"])

    # --------------------------------------------------------------------------
    # 3. COMPLIANCE ON-CHAIN ERC-3643 / T-REX
    # --------------------------------------------------------------------------

    def test_erc3643_investor_qualification_and_claims(self) -> None:
        """Testa validação de perfil de investidor e exigência de credenciais KYC/AML."""
        # Registra investidor de varejo sem claim profissional
        wallet_retail = "0x9999999999999999999999999999999999999999"
        ident = self.engine.register_investor(
            wallet_address=wallet_retail,
            tax_id="000.111.222-33",
            investor_profile=InvestorProfile.RETAIL,
            country_code="BR",
        )
        self.assertEqual(ident.investor_profile, InvestorProfile.RETAIL)

        # Regra do token 1001 exige investidor QUALIFIED
        can_part, reason = self.engine.compliance.can_participate(wallet_retail, 1001)
        self.assertFalse(can_part)
        self.assertIn("below required", reason)

        # Atualiza perfil para QUALIFIED
        ident.investor_profile = InvestorProfile.QUALIFIED
        can_part, reason = self.engine.compliance.can_participate(wallet_retail, 1001)
        self.assertTrue(can_part)

    def test_erc3643_frozen_wallet_and_country_exclusion(self) -> None:
        """Testa congelamento preventivo de carteira e exclusão geográfica."""
        wallet = "0x8888888888888888888888888888888888888888"
        ident = self.engine.register_investor(
            wallet_address=wallet,
            tax_id="99.888.777/0001-66",
            investor_profile=InvestorProfile.PROFESSIONAL,
            country_code="US",  # Não permitido na regra padrão "BR"
        )

        can_part, reason = self.engine.compliance.can_participate(wallet, 1001)
        self.assertFalse(can_part)
        self.assertIn("not allowed", reason)

        # Altera país para BR mas congela a conta
        ident.country_code = "BR"
        ident.is_frozen = True
        can_part, reason = self.engine.compliance.can_participate(wallet, 1001)
        self.assertFalse(can_part)
        self.assertIn("frozen", reason)

    # --------------------------------------------------------------------------
    # 4. BOOKBUILDING E RATEIO PRO-RATA (RESOLUÇÃO CVM 160)
    # --------------------------------------------------------------------------

    def test_bookbuilding_and_pro_rata_allocation_oversubscribed(self) -> None:
        """Valida o fechamento do bookbuilding com taxa de corte e rateio proporcional."""
        offering_id = "OFF-CRA-2028-003"
        token_id = 1003
        issuer_wallet = "0x1111111111111111111111111111111111111111"
        underwriter_wallet = "0x2222222222222222222222222222222222222222"
        escrow_wallet = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"

        # Registra oferta CRA
        self.engine.create_offering(
            offering_id=offering_id,
            asset_symbol="CRA-SLC-2028",
            isin="BRCRASLC0039",
            asset_type=RwaAssetType.CRA,
            tranche=RwaTranche.MEZZANINE,
            token_id=token_id,
            target_units=20000,
            vnu_nominal=Decimal("1000.00"),
            issuer_wallet=issuer_wallet,
            underwriter_wallet=underwriter_wallet,
            escrow_wallet=escrow_wallet,
            benchmark_description="IPCA + 7.00%",
            min_rate=Decimal("6.50"),
            max_rate=Decimal("7.50"),
            subscription_price=Decimal("1000.00"),
            min_investor_profile=InvestorProfile.QUALIFIED,
        )

        self.engine.open_bookbuilding(offering_id)

        # Investidores submetem propostas
        # Investidor A (25.000 unidades a 6.80% - estritamente abaixo da taxa de corte) -> cabe 10.000 se corte for 7.00
        # Vamos montar: Target = 20.000 unidades
        # Bid 1: 10.000 @ 6.70% (prioritária, alocação 100%)
        # Bid 2: 15.000 @ 7.00% (taxa de corte)
        # Bid 3: 15.000 @ 7.00% (taxa de corte)
        # Bid 4: 10.000 @ 7.30% (acima da taxa de corte -> rejeitada)
        # Demanda na taxa de corte = 30.000, Unidades restantes = 20.000 - 10.000 = 10.000
        # Rateio: Bid 2 e Bid 3 recebem 5.000 unidades cada (15.000/30.000 * 10.000 = 5.000)

        w_a = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        w_b = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        w_c = "0xcccccccccccccccccccccccccccccccccccccccc"
        w_d = "0xdddddddddddddddddddddddddddddddddddddddd"

        self.engine.submit_bid(offering_id, "BID-CRA-1", w_a, 10000, Decimal("6.70"))
        self.engine.submit_bid(offering_id, "BID-CRA-2", w_b, 15000, Decimal("7.00"))
        self.engine.submit_bid(offering_id, "BID-CRA-3", w_c, 15000, Decimal("7.00"))
        self.engine.submit_bid(offering_id, "BID-CRA-4", w_d, 10000, Decimal("7.30"))

        allocations = self.engine.close_and_price_bookbuilding(offering_id, fixed_cut_off_rate=Decimal("7.00"))

        offering = self.engine.offerings[offering_id]
        self.assertEqual(offering.status, OfferingStatus.ALLOCATED)
        self.assertEqual(offering.total_allocated_units, 20000)

        # Verificação dos rateios
        bid_map = offering.bids
        self.assertEqual(bid_map["BID-CRA-1"].allocated_units, 10000)
        self.assertEqual(bid_map["BID-CRA-2"].allocated_units, 5000)
        self.assertEqual(bid_map["BID-CRA-3"].allocated_units, 5000)
        self.assertEqual(bid_map["BID-CRA-4"].allocated_units, 0)
        self.assertEqual(bid_map["BID-CRA-4"].status, BidStatus.REJECTED)

        # Soma das alocações deve ser exatamente target_units
        self.assertEqual(sum(a.allocated_units for a in allocations), 20000)

    # --------------------------------------------------------------------------
    # 5. LIQUIDAÇÃO ATÔMICA DVP DIRETA (EVM SAME-BLOCK)
    # --------------------------------------------------------------------------

    def test_direct_atomic_dvp_settlement_success(self) -> None:
        """Executa a liquidação atômica bilateral direta DvP com débito RD e crédito RWA."""
        offering_id = "OFF-CRA-2028-003"
        # Cria e aloca pequena oferta
        offering = self.engine.create_offering(
            offering_id=offering_id,
            asset_symbol="CRA-TEST-001",
            isin="BRCRATEST001",
            asset_type=RwaAssetType.CRA,
            tranche=RwaTranche.SENIOR,
            token_id=2001,
            target_units=1000,
            vnu_nominal=Decimal("1000.00"),
            issuer_wallet="0x1111111111111111111111111111111111111111",
            underwriter_wallet="0x2222222222222222222222222222222222222222",
            escrow_wallet="0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            benchmark_description="CDI + 1.00%",
            min_rate=Decimal("1.00"),
            max_rate=Decimal("1.50"),
            subscription_price=Decimal("1000.00"),
        )
        self.engine.open_bookbuilding(offering_id)

        w_investor = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self.engine.submit_bid(offering_id, "BID-T1", w_investor, 1000, Decimal("1.20"))
        allocs = self.engine.close_and_price_bookbuilding(offering_id)
        alloc_id = allocs[0].allocation_id

        # Saldo inicial RD do investidor e emissor
        init_investor_rd = self.engine.cbdc.balance_of(w_investor)
        init_issuer_rd = self.engine.cbdc.balance_of(offering.issuer_wallet)
        init_investor_tokens = self.engine.rwa_tokens.balance_of(w_investor, 2001)

        # Executa DvP direto
        receipt = self.engine.settle_direct_atomic_dvp(offering_id, alloc_id)

        self.assertEqual(receipt.delivered_units, 1000)
        self.assertEqual(receipt.settled_financial_brl, Decimal("1000000.00"))
        self.assertEqual(receipt.status, "COMMITTED")

        # Verifica balanços
        self.assertEqual(
            self.engine.cbdc.balance_of(w_investor),
            init_investor_rd - Decimal("1000000.00"),
        )
        self.assertEqual(
            self.engine.cbdc.balance_of(offering.issuer_wallet),
            init_issuer_rd + Decimal("1000000.00"),
        )
        self.assertEqual(
            self.engine.rwa_tokens.balance_of(w_investor, 2001),
            init_investor_tokens + 1000,
        )

        # Lei 111 comprovada
        inv_results = self.engine.verify_invariants()
        self.assertTrue(inv_results["LAW-111_primary_dvp_atomic_quantity"])

    def test_direct_atomic_dvp_insufficient_funds(self) -> None:
        """Garante a rejeição atômica se o investidor não tiver saldo em Real Digital."""
        offering_id = "OFF-CRA-2028-004"
        self.engine.create_offering(
            offering_id=offering_id,
            asset_symbol="CRA-TEST-002",
            isin="BRCRATEST002",
            asset_type=RwaAssetType.CRA,
            tranche=RwaTranche.SENIOR,
            token_id=2002,
            target_units=1000,
            vnu_nominal=Decimal("1000.00"),
            issuer_wallet="0x1111111111111111111111111111111111111111",
            underwriter_wallet="0x2222222222222222222222222222222222222222",
            escrow_wallet="0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            benchmark_description="CDI + 1.00%",
            min_rate=Decimal("1.00"),
            max_rate=Decimal("1.50"),
            subscription_price=Decimal("1000.00"),
        )
        self.engine.open_bookbuilding(offering_id)

        # Investidor sem saldo
        broke_wallet = "0x7777777777777777777777777777777777777777"
        self.engine.register_investor(broke_wallet, "000.000.000-00", InvestorProfile.QUALIFIED)
        self.engine.submit_bid(offering_id, "BID-BROKE", broke_wallet, 500, Decimal("1.10"))
        allocs = self.engine.close_and_price_bookbuilding(offering_id)
        alloc_id = allocs[0].allocation_id

        with self.assertRaises(DrexRwaInsufficientBalanceError):
            self.engine.settle_direct_atomic_dvp(offering_id, alloc_id)

    # --------------------------------------------------------------------------
    # 6. LIQUIDAÇÃO ATÔMICA CONDICIONAL VIA HTLC
    # --------------------------------------------------------------------------

    def test_htlc_atomic_swap_success(self) -> None:
        """Testa o ciclo completo de troca atômica via HTLC com revelação de preimage."""
        offering_id = "OFF-HTLC-TEST"
        token_id = 3001
        self.engine.create_offering(
            offering_id=offering_id,
            asset_symbol="DEB-HTLC-3001",
            isin="BRDEBHTLC001",
            asset_type=RwaAssetType.DEBENTURE,
            tranche=RwaTranche.SENIOR,
            token_id=token_id,
            target_units=500,
            vnu_nominal=Decimal("1000.00"),
            issuer_wallet="0x1111111111111111111111111111111111111111",
            underwriter_wallet="0x2222222222222222222222222222222222222222",
            escrow_wallet="0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            benchmark_description="IPCA + 5.50%",
            min_rate=Decimal("5.00"),
            max_rate=Decimal("6.00"),
            subscription_price=Decimal("1000.00"),
        )
        self.engine.open_bookbuilding(offering_id)

        w_investor = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        self.engine.submit_bid(offering_id, "BID-H1", w_investor, 500, Decimal("5.50"))
        allocs = self.engine.close_and_price_bookbuilding(offering_id)
        alloc_id = allocs[0].allocation_id

        # Segredo criptográfico
        preimage = "super_secret_drex_salt_2026"
        hash_lock = hashlib.sha256(preimage.encode()).hexdigest()

        # Inicia HTLC (trava RD e tokens no escrow)
        htlc = self.engine.initiate_htlc_dvp(offering_id, alloc_id, hash_lock, timeout_seconds=3600)
        self.assertEqual(htlc.status, HtlcStatus.PENDING)

        # Claim com preimage
        receipt = self.engine.claim_htlc_dvp(htlc.contract_id, preimage)
        self.assertEqual(receipt.delivered_units, 500)
        self.assertEqual(receipt.settled_financial_brl, Decimal("500000.00"))
        self.assertEqual(htlc.status, HtlcStatus.CLAIMED)
        self.assertEqual(self.engine.rwa_tokens.balance_of(w_investor, token_id), 500)

    def test_htlc_invalid_preimage_and_timeout_refund(self) -> None:
        """Testa falha de hashlock na revelação e estorno integral pós-timeout."""
        offering_id = "OFF-HTLC-FAIL"
        token_id = 3002
        self.engine.create_offering(
            offering_id=offering_id,
            asset_symbol="DEB-HTLC-3002",
            isin="BRDEBHTLC002",
            asset_type=RwaAssetType.DEBENTURE,
            tranche=RwaTranche.SENIOR,
            token_id=token_id,
            target_units=200,
            vnu_nominal=Decimal("1000.00"),
            issuer_wallet="0x1111111111111111111111111111111111111111",
            underwriter_wallet="0x2222222222222222222222222222222222222222",
            escrow_wallet="0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            benchmark_description="IPCA + 5.50%",
            min_rate=Decimal("5.00"),
            max_rate=Decimal("6.00"),
            subscription_price=Decimal("1000.00"),
        )
        self.engine.open_bookbuilding(offering_id)

        w_investor = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self.engine.submit_bid(offering_id, "BID-H2", w_investor, 200, Decimal("5.50"))
        allocs = self.engine.close_and_price_bookbuilding(offering_id)
        alloc_id = allocs[0].allocation_id

        preimage = "drex_secret_preimage"
        hash_lock = hashlib.sha256(preimage.encode()).hexdigest()

        htlc = self.engine.initiate_htlc_dvp(offering_id, alloc_id, hash_lock, timeout_seconds=10)

        # Tentativa com preimage errada
        with self.assertRaises(DrexRwaHtlcError):
            self.engine.claim_htlc_dvp(htlc.contract_id, "wrong_secret")

        # Estorno forçado simulando timeout
        bal_before_refund = self.engine.cbdc.balance_of(w_investor)
        self.engine.refund_htlc_dvp(htlc.contract_id, force_expired=True)

        self.assertEqual(htlc.status, HtlcStatus.REFUNDED)
        self.assertEqual(
            self.engine.cbdc.balance_of(w_investor),
            bal_before_refund + Decimal("200000.00"),
        )

    # --------------------------------------------------------------------------
    # 7. PROVENTOS ON-CHAIN: CUPONS E AMORTIZAÇÕES
    # --------------------------------------------------------------------------

    def test_coupon_payment_and_law111_conservation(self) -> None:
        """Executa evento corporativo de juros/cupom e audita a conservação de proventos."""
        offering_id = "OFF-DEB-2026-001"
        event_id = "EVT-COUPON-TEST-002"
        funder = "0x1111111111111111111111111111111111111111"

        # Debênture tem 50.000 tokens circulantes
        action = self.engine.create_corporate_action(
            offering_id=offering_id,
            event_id=event_id,
            event_type=CorporateActionType.COUPON_PAYMENT,
            record_date="2026-11-08",
            payment_date="2026-11-09",
            unit_amount_brl=Decimal("15.50"),
            rate_or_percentage=Decimal("1.55"),
        )

        required_funds = self.engine.fund_corporate_action(event_id, funder)
        self.assertEqual(required_funds, Decimal("775000.00"))  # 50.000 * 15.50

        executed_action = self.engine.execute_corporate_action(event_id)
        self.assertEqual(executed_action.status, CorporateActionStatus.EXECUTED)
        self.assertEqual(executed_action.total_distributed_brl, Decimal("775000.00"))
        self.assertEqual(len(executed_action.distributions), 2)  # Alice e Bob detêm 25.000 cada

        # Integridade LAW-111 de proventos
        inv = self.engine.verify_invariants()
        self.assertTrue(inv["LAW-111_coupon_conservation"])

    def test_principal_amortization_vnu_reduction(self) -> None:
        """Testa amortização de principal e atualização do VNU remanescente."""
        offering_id = "OFF-DEB-2026-001"
        event_id = "EVT-AMORT-TEST-003"
        funder = "0x1111111111111111111111111111111111111111"

        vnu_before = self.engine.offerings[offering_id].current_vnu
        amort_amount = Decimal("50.00")

        self.engine.create_corporate_action(
            offering_id=offering_id,
            event_id=event_id,
            event_type=CorporateActionType.PRINCIPAL_AMORTIZATION,
            record_date="2026-11-10",
            payment_date="2026-11-12",
            unit_amount_brl=amort_amount,
            rate_or_percentage=Decimal("5.00"),
        )
        self.engine.fund_corporate_action(event_id, funder)
        self.engine.execute_corporate_action(event_id)

        vnu_after = self.engine.offerings[offering_id].current_vnu
        self.assertEqual(vnu_after, vnu_before - amort_amount)

    def test_total_redemption_and_burn(self) -> None:
        """Testa resgate total com queima de todos os tokens e encerramento da oferta."""
        offering_id = "OFF-CRI-2027-002"
        token_id = 1002
        event_id = "EVT-REDEMPTION-CRI"
        funder = "0x1111111111111111111111111111111111111111"

        circulating = self.engine.rwa_tokens.metadata[token_id].circulating_units
        vnu = self.engine.offerings[offering_id].current_vnu

        self.engine.create_corporate_action(
            offering_id=offering_id,
            event_id=event_id,
            event_type=CorporateActionType.TOTAL_REDEMPTION,
            record_date="2027-10-30",
            payment_date="2027-11-01",
            unit_amount_brl=vnu,
        )
        self.engine.fund_corporate_action(event_id, funder)
        self.engine.execute_corporate_action(event_id)

        self.assertEqual(self.engine.rwa_tokens.metadata[token_id].circulating_units, 0)
        self.assertEqual(self.engine.offerings[offering_id].current_vnu, Decimal("0.00"))
        self.assertEqual(self.engine.offerings[offering_id].status, OfferingStatus.MATURED)

    # --------------------------------------------------------------------------
    # 8. TESTES FORMAIS DE VIOLAÇÃO DE INVARIANTES (LAW-111 & LAW-089C)
    # --------------------------------------------------------------------------

    def test_law111_violation_asymmetric_quantity(self) -> None:
        """Assegura disparo de InvariantViolationError se quantidade alocada != entregue."""
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_drex_rwa_primary_issuance_invariants(
                allocated_units=1000,
                delivered_units=999,  # Violação proposital
                received_financial=Decimal("1000000.00"),
                expected_financial=Decimal("1000000.00"),
                operation_id="OP-TEST-FAIL-QTY",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-111")

    def test_law111_violation_asymmetric_financial(self) -> None:
        """Assegura disparo de InvariantViolationError se valor recebido != esperado."""
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_drex_rwa_primary_issuance_invariants(
                allocated_units=1000,
                delivered_units=1000,
                received_financial=Decimal("999999.00"),  # Violação financeira
                expected_financial=Decimal("1000000.00"),
                operation_id="OP-TEST-FAIL-FIN",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-111")

    def test_law111_violation_coupon_leakage(self) -> None:
        """Assegura disparo se montante de cupom provisionado != montante distribuído."""
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_drex_rwa_primary_issuance_invariants(
                allocated_units=100,
                delivered_units=100,
                received_financial=Decimal("100000.00"),
                expected_financial=Decimal("100000.00"),
                operation_id="OP-TEST-FAIL-COUPON",
                funded_coupon_amount=Decimal("5000.00"),
                distributed_coupon_amount=Decimal("4990.00"),  # Vazamento
            )
        self.assertEqual(ctx.exception.law_code, "LAW-111")


if __name__ == "__main__":
    unittest.main()
