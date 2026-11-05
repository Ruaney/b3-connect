"""
Suíte de Testes Unitários — B3 CSD & BACEN DREX Piloto Fase 2 (FL-089)
======================================================================
Validação da Liquidação Atômica DvP e PvP em Rede DLT EVM / Hyperledger Besu
com CBDC Atacado (Real Digital), Moeda Escritural de Varejo (Real Tokenizado),
Títulos Públicos Federais Tokenizados (TPFt: LTN, LFT, NTN-B), HTLC e
garantias formais de conservação matemática e paridade soberana 1:1.
"""

from decimal import Decimal
import hashlib
from pathlib import Path
import unittest

from src.drex_phase2_atomic_dvp import (
    AtomicSwapStatus,
    ClientWallet,
    DrexAccountFrozenError,
    DrexAssetNotFoundError,
    DrexAssetType,
    DrexHtlcAlreadySettledError,
    DrexHtlcError,
    DrexHtlcExpiredError,
    DrexHtlcNotExpiredError,
    DrexHtlcPreimageMismatchError,
    DrexInsufficientBalanceError,
    DrexInvalidOperationError,
    DrexPhase2AtomicDvpEngine,
    DrexPhase2Error,
    DrexSecurityType,
    DrexSettlementMode,
    HtlcStatus,
    InstitutionAccount,
    TpftInstrument,
)
from src.invariants import InvariantViolationError


class TestDrexPhase2AtomicDvp(unittest.TestCase):
    """Bateria de testes formais para o fluxo FL-089 do Piloto Drex Fase 2."""

    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "drex_phase2_atomic_dvp.json"
        self.engine = DrexPhase2AtomicDvpEngine.load_from_fixture(self.fixture_file)

    # --------------------------------------------------------------------------
    # 1. CARREGAMENTO DE FIXTURE E INVARIANTES INICIAIS
    # --------------------------------------------------------------------------

    def test_fixture_loading_and_baseline_invariants(self) -> None:
        """Verifica a integridade inicial da rede Drex após carga da fixture oficial."""
        self.assertEqual(len(self.engine.institutions), 3)
        self.assertEqual(len(self.engine.clients), 4)
        self.assertEqual(len(self.engine.tpft_instruments), 3)

        # Invariantes devem passar 100%
        inv = self.engine.verify_invariants()
        self.assertTrue(inv["LAW-089C_non_negativity"])
        self.assertTrue(inv["LAW-089B_sovereign_parity"])
        self.assertTrue(inv["LAW-089A_custody_conservation"])

        # Confere dados da IF Itaú
        itau = self.engine.institutions["IF-ITAU-001"]
        self.assertEqual(itau.real_digital_balance, Decimal("150000000.00"))
        self.assertEqual(itau.tpft_balances["LTN2028"], 15000)

        # Confere cliente Alice
        alice = self.engine.clients["0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
        self.assertEqual(alice.real_tokenizado_balances["RT_ITAU"], Decimal("2500000.00"))
        self.assertEqual(alice.tpft_balances["LTN2028"], 500)

    # --------------------------------------------------------------------------
    # 2. OPERAÇÕES DE MOEDA BACEN / IF: MINT & BURN
    # --------------------------------------------------------------------------

    def test_mint_and_burn_real_digital_wholesale(self) -> None:
        """Testa emissão e queima de Real Digital Atacado pelo BACEN."""
        initial_balance = self.engine.institutions["IF-ITAU-001"].real_digital_balance

        # BACEN injeta 25.000.000,00 de reserva bancária
        new_balance = self.engine.mint_real_digital("IF-ITAU-001", Decimal("25000000.00"))
        self.assertEqual(new_balance, initial_balance + Decimal("25000000.00"))
        self.engine.verify_invariants()

        # BACEN recolhe 10.000.000,00
        burned_balance = self.engine.burn_real_digital("IF-ITAU-001", Decimal("10000000.00"))
        self.assertEqual(burned_balance, initial_balance + Decimal("15000000.00"))
        self.engine.verify_invariants()

        # Tentativa de queimar mais do que o saldo
        with self.assertRaises(DrexInsufficientBalanceError):
            self.engine.burn_real_digital("IF-ITAU-001", Decimal("999999999.00"))

        # Montante zero ou negativo rejeitado
        with self.assertRaises(DrexInvalidOperationError):
            self.engine.mint_real_digital("IF-ITAU-001", Decimal("0.00"))
        with self.assertRaises(DrexInvalidOperationError):
            self.engine.burn_real_digital("IF-ITAU-001", Decimal("-100.00"))

    def test_issue_and_redeem_real_tokenizado_retail(self) -> None:
        """Testa emissão e resgate de Real Tokenizado (depósitos de clientes)."""
        alice_addr = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        alice = self.engine.clients[alice_addr]
        initial_rt = alice.real_tokenizado_balances["RT_ITAU"]

        # Alice deposita R$ 500.000,00 na sua conta Itaú e recebe RT_ITAU
        new_rt = self.engine.issue_real_tokenizado(alice_addr, Decimal("500000.00"))
        self.assertEqual(new_rt, initial_rt + Decimal("500000.00"))
        self.engine.verify_invariants()

        # Alice saca/resgata R$ 200.000,00
        redeemed_rt = self.engine.redeem_real_tokenizado(alice_addr, Decimal("200000.00"))
        self.assertEqual(redeemed_rt, initial_rt + Decimal("300000.00"))
        self.engine.verify_invariants()

        # Tentativa de resgate com saldo insuficiente
        with self.assertRaises(DrexInsufficientBalanceError):
            self.engine.redeem_real_tokenizado(alice_addr, Decimal("99999999.00"))

    # --------------------------------------------------------------------------
    # 3. LIQUIDAÇÃO ATÔMICA DVP ATACADO (IF vs IF)
    # --------------------------------------------------------------------------

    def test_wholesale_dvp_success(self) -> None:
        """Valida DvP Atacado entre Itaú (vendedor) e Bradesco (comprador) de LTN2028."""
        itau = self.engine.institutions["IF-ITAU-001"]
        bradesco = self.engine.institutions["IF-BRADESCO-002"]

        itau_ltn_before = itau.tpft_balances["LTN2028"]
        bradesco_ltn_before = bradesco.tpft_balances["LTN2028"]
        itau_rd_before = itau.real_digital_balance
        bradesco_rd_before = bradesco.real_digital_balance

        qty = 1000
        pu = Decimal("985.45")
        total_fin = Decimal("985450.00")

        receipt = self.engine.settle_wholesale_dvp(
            seller_institution_id="IF-ITAU-001",
            buyer_institution_id="IF-BRADESCO-002",
            tpft_symbol="LTN2028",
            quantity=qty,
            unit_price_brl=pu,
        )

        self.assertEqual(receipt.status, AtomicSwapStatus.COMMITTED)
        self.assertEqual(receipt.settlement_mode, DrexSettlementMode.WHOLESALE_DVP)
        self.assertEqual(receipt.financial_amount_brl, total_fin)
        self.assertTrue(receipt.atomic_conservation_verified)

        # Saldos atualizados
        self.assertEqual(itau.tpft_balances["LTN2028"], itau_ltn_before - qty)
        self.assertEqual(bradesco.tpft_balances["LTN2028"], bradesco_ltn_before + qty)
        self.assertEqual(itau.real_digital_balance, itau_rd_before + total_fin)
        self.assertEqual(bradesco.real_digital_balance, bradesco_rd_before - total_fin)

        # Invariantes após DvP Atacado
        self.engine.verify_invariants()

    def test_wholesale_dvp_insufficient_tpft(self) -> None:
        """Garante All-or-None quando vendedor não tem títulos suficientes."""
        itau = self.engine.institutions["IF-ITAU-001"]
        bradesco = self.engine.institutions["IF-BRADESCO-002"]
        itau_rd_before = itau.real_digital_balance
        bradesco_rd_before = bradesco.real_digital_balance

        with self.assertRaises(DrexInsufficientBalanceError):
            self.engine.settle_wholesale_dvp(
                seller_institution_id="IF-ITAU-001",
                buyer_institution_id="IF-BRADESCO-002",
                tpft_symbol="LTN2028",
                quantity=999999,  # Vendedor não possui
                unit_price_brl=Decimal("985.45"),
            )

        # Nenhuma alteração financeira ou de títulos deve ter ocorrido
        self.assertEqual(itau.real_digital_balance, itau_rd_before)
        self.assertEqual(bradesco.real_digital_balance, bradesco_rd_before)

    def test_wholesale_dvp_insufficient_real_digital(self) -> None:
        """Garante All-or-None quando comprador não tem Real Digital suficiente."""
        itau = self.engine.institutions["IF-ITAU-001"]
        bradesco = self.engine.institutions["IF-BRADESCO-002"]
        itau_ltn_before = itau.tpft_balances["LTN2028"]

        with self.assertRaises(DrexInsufficientBalanceError):
            self.engine.settle_wholesale_dvp(
                seller_institution_id="IF-ITAU-001",
                buyer_institution_id="IF-BRADESCO-002",
                tpft_symbol="LTN2028",
                quantity=1000,
                unit_price_brl=Decimal("500000.00"),  # Excede reserva do Bradesco
            )

        self.assertEqual(itau.tpft_balances["LTN2028"], itau_ltn_before)

    # --------------------------------------------------------------------------
    # 4. LIQUIDAÇÃO ATÔMICA DVP VAREJO INTRA E INTERBANCÁRIO
    # --------------------------------------------------------------------------

    def test_retail_intrabank_dvp(self) -> None:
        """DvP de varejo entre clientes da mesma instituição financeira (Itaú)."""
        alice_addr = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        diana_addr = "0xdddddddddddddddddddddddddddddddddddddddd"

        alice = self.engine.clients[alice_addr]
        diana = self.engine.clients[diana_addr]

        alice_ltn_before = alice.tpft_balances["LTN2028"]
        diana_ltn_before = diana.tpft_balances.get("LTN2028", 0)
        alice_rt_before = alice.real_tokenizado_balances["RT_ITAU"]
        diana_rt_before = diana.real_tokenizado_balances["RT_ITAU"]

        qty = 50
        pu = Decimal("985.45")
        total_fin = Decimal("49272.50")

        # Alice vende 50 LTN para Diana
        receipt = self.engine.settle_interbank_retail_dvp(
            seller_wallet_address=alice_addr,
            buyer_wallet_address=diana_addr,
            tpft_symbol="LTN2028",
            quantity=qty,
            unit_price_brl=pu,
        )

        self.assertEqual(receipt.status, AtomicSwapStatus.COMMITTED)
        self.assertEqual(receipt.interbank_rd_cleared, Decimal("0.00"))  # Mesmo banco!

        # Saldos atualizados
        self.assertEqual(alice.tpft_balances["LTN2028"], alice_ltn_before - qty)
        self.assertEqual(diana.tpft_balances["LTN2028"], diana_ltn_before + qty)
        self.assertEqual(alice.real_tokenizado_balances["RT_ITAU"], alice_rt_before + total_fin)
        self.assertEqual(diana.real_tokenizado_balances["RT_ITAU"], diana_rt_before - total_fin)

        self.engine.verify_invariants()

    def test_retail_interbank_dvp_full_cycle(self) -> None:
        """
        DvP Interbancário completo:
        Alice (Itaú) vende 100 LTN2028 para Bob (Bradesco) a R$ 985,45.
        Verifica:
        1. Entrega de TPFt Alice -> Bob;
        2. Débito de RT_BRADESCO de Bob e crédito de RT_ITAU para Alice;
        3. Compensação atômica interbancária de Real Digital: Bradesco -> Itaú;
        4. Conservação de paridade 1:1 e All-or-None.
        """
        alice_addr = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        bob_addr = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        alice = self.engine.clients[alice_addr]
        bob = self.engine.clients[bob_addr]
        itau = self.engine.institutions["IF-ITAU-001"]
        bradesco = self.engine.institutions["IF-BRADESCO-002"]

        alice_ltn_before = alice.tpft_balances["LTN2028"]
        bob_ltn_before = bob.tpft_balances.get("LTN2028", 0)
        alice_rt_before = alice.real_tokenizado_balances["RT_ITAU"]
        bob_rt_before = bob.real_tokenizado_balances["RT_BRADESCO"]

        itau_rd_before = itau.real_digital_balance
        bradesco_rd_before = bradesco.real_digital_balance

        qty = 100
        pu = Decimal("985.45")
        total_fin = Decimal("98545.00")

        receipt = self.engine.settle_interbank_retail_dvp(
            seller_wallet_address=alice_addr,
            buyer_wallet_address=bob_addr,
            tpft_symbol="LTN2028",
            quantity=qty,
            unit_price_brl=pu,
        )

        self.assertEqual(receipt.status, AtomicSwapStatus.COMMITTED)
        self.assertEqual(receipt.settlement_mode, DrexSettlementMode.RETAIL_INTERBANK_DVP)
        self.assertEqual(receipt.interbank_rd_cleared, total_fin)
        self.assertTrue(receipt.atomic_conservation_verified)

        # 1. Perna de TPFt
        self.assertEqual(alice.tpft_balances["LTN2028"], alice_ltn_before - qty)
        self.assertEqual(bob.tpft_balances["LTN2028"], bob_ltn_before + qty)

        # 2. Perna de Moeda de Varejo
        self.assertEqual(bob.real_tokenizado_balances["RT_BRADESCO"], bob_rt_before - total_fin)
        self.assertEqual(alice.real_tokenizado_balances["RT_ITAU"], alice_rt_before + total_fin)

        # 3. Perna de Reserva Interbancária Atacado (Real Digital)
        self.assertEqual(bradesco.real_digital_balance, bradesco_rd_before - total_fin)
        self.assertEqual(itau.real_digital_balance, itau_rd_before + total_fin)

        self.engine.verify_invariants()

    def test_retail_interbank_dvp_rejection_insufficient_buyer_rt(self) -> None:
        """Garante rejeição All-or-None se o comprador não possui Real Tokenizado suficiente."""
        alice_addr = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        bob_addr = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        alice = self.engine.clients[alice_addr]
        bob = self.engine.clients[bob_addr]
        alice_ltn_before = alice.tpft_balances["LTN2028"]
        bob_rt_before = bob.real_tokenizado_balances["RT_BRADESCO"]

        with self.assertRaises(DrexInsufficientBalanceError):
            self.engine.settle_interbank_retail_dvp(
                seller_wallet_address=alice_addr,
                buyer_wallet_address=bob_addr,
                tpft_symbol="LTN2028",
                quantity=100,
                unit_price_brl=Decimal("50000.00"),  # Total = R$ 5.000.000 (Bob tem 3.8M)
            )

        # Saldos rigorosamente inalterados
        self.assertEqual(alice.tpft_balances["LTN2028"], alice_ltn_before)
        self.assertEqual(bob.real_tokenizado_balances["RT_BRADESCO"], bob_rt_before)

    # --------------------------------------------------------------------------
    # 5. LIQUIDAÇÃO ATÔMICA PVP INTERBANCÁRIA
    # --------------------------------------------------------------------------

    def test_interbank_pvp_transfer(self) -> None:
        """Valida transferência de Real Tokenizado entre Alice (Itaú) e Carlos (BB) via Real Digital."""
        alice_addr = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        carlos_addr = "0xcccccccccccccccccccccccccccccccccccccccc"

        alice = self.engine.clients[alice_addr]
        carlos = self.engine.clients[carlos_addr]
        itau = self.engine.institutions["IF-ITAU-001"]
        bb = self.engine.institutions["IF-BB-003"]

        alice_rt_before = alice.real_tokenizado_balances["RT_ITAU"]
        carlos_rt_before = carlos.real_tokenizado_balances["RT_BB"]
        itau_rd_before = itau.real_digital_balance
        bb_rd_before = bb.real_digital_balance

        amount = Decimal("300000.00")

        receipt = self.engine.settle_interbank_pvp(
            sender_wallet_address=alice_addr,
            recipient_wallet_address=carlos_addr,
            amount_brl=amount,
        )

        self.assertEqual(receipt.status, AtomicSwapStatus.COMMITTED)
        self.assertEqual(receipt.settlement_mode, DrexSettlementMode.INTERBANK_PVP)
        self.assertEqual(receipt.financial_amount_brl, amount)
        self.assertEqual(receipt.interbank_rd_cleared, amount)

        # Alice debita RT_ITAU
        self.assertEqual(alice.real_tokenizado_balances["RT_ITAU"], alice_rt_before - amount)
        # Carlos credita RT_BB
        self.assertEqual(carlos.real_tokenizado_balances["RT_BB"], carlos_rt_before + amount)
        # Reserva DLT: Itaú debita RD, BB credita RD
        self.assertEqual(itau.real_digital_balance, itau_rd_before - amount)
        self.assertEqual(bb.real_digital_balance, bb_rd_before + amount)

        self.engine.verify_invariants()

    # --------------------------------------------------------------------------
    # 6. HASH TIME-LOCKED CONTRACTS (HTLC) & TIMEOUT REVERSIONS
    # --------------------------------------------------------------------------

    def test_htlc_claim_success_with_valid_preimage(self) -> None:
        """Testa criação e resgate com sucesso de HTLC travando TPFt."""
        alice_addr = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        bob_addr = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        alice = self.engine.clients[alice_addr]
        bob = self.engine.clients[bob_addr]
        alice_ntnb_before = alice.tpft_balances["NTNB2035"]
        bob_ntnb_before = bob.tpft_balances.get("NTNB2035", 0)

        secret = "chave-secreta-regulamentar-bacen-drex-fase-2"
        hash_lock = hashlib.sha256(secret.encode("utf-8")).hexdigest()

        # Alice trava 20 NTNB2035 no HTLC com 3600 segundos de prazo
        htlc = self.engine.create_htlc(
            sender_address=alice_addr,
            recipient_address=bob_addr,
            asset_type=DrexAssetType.TPFT,
            asset_id="NTNB2035",
            amount_or_quantity=20,
            hash_lock=hash_lock,
            timelock_seconds=3600.0,
        )

        self.assertEqual(htlc.status, HtlcStatus.PENDING)
        self.assertEqual(alice.tpft_balances["NTNB2035"], alice_ntnb_before - 20)

        # Bob apresenta a preimage correta antes do prazo
        claimed_htlc = self.engine.claim_htlc(htlc.contract_id, secret)
        self.assertEqual(claimed_htlc.status, HtlcStatus.CLAIMED)
        self.assertEqual(claimed_htlc.secret_preimage, secret)
        self.assertEqual(bob.tpft_balances["NTNB2035"], bob_ntnb_before + 20)

        self.engine.verify_invariants()

    def test_htlc_rejection_on_invalid_preimage(self) -> None:
        """Garante que tentativa de resgate com preimage inválida falha e mantém fundos em custódia."""
        alice_addr = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        bob_addr = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        secret = "segredo-genuino-12345"
        hash_lock = hashlib.sha256(secret.encode("utf-8")).hexdigest()

        htlc = self.engine.create_htlc(
            sender_address=alice_addr,
            recipient_address=bob_addr,
            asset_type=DrexAssetType.TPFT,
            asset_id="NTNB2035",
            amount_or_quantity=10,
            hash_lock=hash_lock,
            timelock_seconds=1800.0,
        )

        with self.assertRaises(DrexHtlcPreimageMismatchError):
            self.engine.claim_htlc(htlc.contract_id, "segredo-errado-hacker")

        # Contrato continua pendente
        self.assertEqual(self.engine.htlc_contracts[htlc.contract_id].status, HtlcStatus.PENDING)

    def test_htlc_timeout_expiration_and_automatic_refund(self) -> None:
        """
        Valida que se o prazo expirar sem revelação da preimage:
        1. O resgate via claim_htlc é bloqueado por expiração;
        2. O remetente pode acionar o reembolso total sem perda de ativos.
        """
        bob_addr = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        alice_addr = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        bob = self.engine.clients[bob_addr]

        bob_lft_before = bob.tpft_balances["LFT2029"]

        secret = "segredo-que-nao-sera-revelado"
        hash_lock = hashlib.sha256(secret.encode("utf-8")).hexdigest()

        # Bob trava 50 LFT2029 com timelock de 300 segundos
        htlc = self.engine.create_htlc(
            sender_address=bob_addr,
            recipient_address=alice_addr,
            asset_type=DrexAssetType.TPFT,
            asset_id="LFT2029",
            amount_or_quantity=50,
            hash_lock=hash_lock,
            timelock_seconds=300.0,
        )
        self.assertEqual(bob.tpft_balances["LFT2029"], bob_lft_before - 50)

        # Tentativa de reembolso ANTES do timelock deve falhar
        with self.assertRaises(DrexHtlcNotExpiredError):
            self.engine.refund_htlc(htlc.contract_id)

        # Avança o tempo do motor DLT em 301 segundos
        self.engine.advance_time(301.0)

        # Tentativa de resgate pós-timeout deve ser rejeitada
        with self.assertRaises(DrexHtlcExpiredError):
            self.engine.claim_htlc(htlc.contract_id, secret)

        # Reembolso bem-sucedido
        refunded_htlc = self.engine.refund_htlc(htlc.contract_id)
        self.assertEqual(refunded_htlc.status, HtlcStatus.REFUNDED)
        self.assertEqual(bob.tpft_balances["LFT2029"], bob_lft_before)

        # Tentativa de reutilizar contrato reembolsado
        with self.assertRaises(DrexHtlcAlreadySettledError):
            self.engine.refund_htlc(htlc.contract_id)

        self.engine.verify_invariants()

    def test_execute_htlc_atomic_dvp_bilateral_swap(self) -> None:
        """
        Testa o método orquestrador execute_htlc_atomic_dvp:
        Executa troca atômica simultânea entre TPFt e Real Tokenizado usando HTLCs cruzados.
        """
        alice_addr = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        carlos_addr = "0xcccccccccccccccccccccccccccccccccccccccc"

        alice = self.engine.clients[alice_addr]
        carlos = self.engine.clients[carlos_addr]

        alice_ltn_before = alice.tpft_balances["LTN2028"]
        carlos_ltn_before = carlos.tpft_balances["LTN2028"]
        carlos_rt_before = carlos.real_tokenizado_balances["RT_BB"]

        preimage = "drex-fase-2-atomic-swap-preimage-secret"
        qty = 200
        payment = Decimal("197090.00")

        h_tpft, h_pay, receipt = self.engine.execute_htlc_atomic_dvp(
            seller_address=alice_addr,
            buyer_address=carlos_addr,
            tpft_symbol="LTN2028",
            tpft_quantity=qty,
            payment_asset_type=DrexAssetType.REAL_TOKENIZADO,
            payment_asset_id="RT_BB",
            payment_amount_brl=payment,
            secret_preimage=preimage,
            timelock_seconds=1800.0,
        )

        self.assertEqual(h_tpft.status, HtlcStatus.CLAIMED)
        self.assertEqual(h_pay.status, HtlcStatus.CLAIMED)
        self.assertEqual(receipt.status, AtomicSwapStatus.COMMITTED)
        self.assertTrue(receipt.atomic_conservation_verified)

        # Saldos conferidos
        self.assertEqual(alice.tpft_balances["LTN2028"], alice_ltn_before - qty)
        self.assertEqual(carlos.tpft_balances["LTN2028"], carlos_ltn_before + qty)
        self.assertEqual(carlos.real_tokenizado_balances["RT_BB"], carlos_rt_before - payment)

        self.engine.verify_invariants()

    def test_htlc_cooperative_cancellation(self) -> None:
        """Testa o cancelamento cooperativo de HTLC pendente devolvendo fundos."""
        alice_addr = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        bob_addr = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        alice = self.engine.clients[alice_addr]
        alice_ltn_before = alice.tpft_balances["LTN2028"]

        hash_lock = hashlib.sha256(b"cooperative-cancel").hexdigest()
        htlc = self.engine.create_htlc(
            sender_address=alice_addr,
            recipient_address=bob_addr,
            asset_type=DrexAssetType.TPFT,
            asset_id="LTN2028",
            amount_or_quantity=30,
            hash_lock=hash_lock,
            timelock_seconds=3600.0,
        )

        self.assertEqual(alice.tpft_balances["LTN2028"], alice_ltn_before - 30)

        cancelled = self.engine.cancel_htlc(htlc.contract_id)
        self.assertEqual(cancelled.status, HtlcStatus.CANCELLED)
        self.assertEqual(alice.tpft_balances["LTN2028"], alice_ltn_before)

        self.engine.verify_invariants()

    # --------------------------------------------------------------------------
    # 7. RESILIÊNCIA, VIOLAÇÃO DE INVARIANTES E SERIALIZAÇÃO
    # --------------------------------------------------------------------------

    def test_adversarial_negative_quantity_or_invalid_address(self) -> None:
        """Valida proteção contra valores adversariais e endereços desconhecidos."""
        with self.assertRaises(DrexInvalidOperationError):
            self.engine.settle_wholesale_dvp(
                seller_institution_id="IF-ITAU-001",
                buyer_institution_id="IF-BRADESCO-002",
                tpft_symbol="LTN2028",
                quantity=-5,
                unit_price_brl=Decimal("985.45"),
            )

        with self.assertRaises(DrexAssetNotFoundError):
            self.engine.settle_wholesale_dvp(
                seller_institution_id="IF-FANTASMA-999",
                buyer_institution_id="IF-BRADESCO-002",
                tpft_symbol="LTN2028",
                quantity=10,
                unit_price_brl=Decimal("985.45"),
            )

    def test_invariant_violation_detection(self) -> None:
        """Garante que qualquer alteração fraudulenta direta no estado é detectada por verify_invariants."""
        itau = self.engine.institutions["IF-ITAU-001"]
        # Força saldo negativo
        itau.real_digital_balance = Decimal("-500.00")
        with self.assertRaises(InvariantViolationError):
            self.engine.verify_invariants()

    def test_to_dict_serialization(self) -> None:
        """Testa serialização completa to_dict() para integração e auditoria externa."""
        state_dict = self.engine.to_dict()
        self.assertEqual(state_dict["version"], "2.0.0")
        self.assertIn("institutions", state_dict)
        self.assertIn("clients", state_dict)
        self.assertIn("tpft_instruments", state_dict)
        self.assertIn("invariants_summary", state_dict)
        self.assertTrue(state_dict["invariants_summary"]["LAW-089A_custody_conservation"])


if __name__ == "__main__":
    unittest.main()
