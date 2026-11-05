"""
Testes unitários e de integração para o motor de Gestão e Liquidação de Margem via Pix Automático (FL-090).
==========================================================================================================
Cobre:
- Validação estrita de CPF, CNPJ, ISPB, EndToEndId (32 chars) e chaves Pix DICT;
- Ciclo de vida e governança de Mandatos Pix Automático (Resolução BCB nº 341/2023);
- Emissão de Chamada de Margem Intradiária pelo motor CORE da B3;
- Geração e parsing de mensagens ISO 20022 pacs.008 e pacs.002;
- Execução de liquidação instantânea D0 com débito na Conta PI pagadora e crédito na Conta PI B3;
- Idempotência absoluta e prevenção de colisão por EndToEndId;
- Tratamento de rejeições (saldo insuficiente AM04, mandato inválido RC07, limite excedido ED05,
  chave inativa BE05, timeout de SLA AB03) com transição compulsória para DEFAULT e bloqueio pré-trade;
- Verificação rigorosa das 6 Invariantes Formais do sistema (soma-zero, E2EID, não-repúdio, etc.);
- Carga de fixture JSON e serialização integral to_dict().
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.pix_automatico_margin_calls import (
    AuthorizationChannel,
    B3ParticipantCollateralAccount,
    DictKeyRecord,
    IntradayMarginCall,
    InvalidEndToEndIdError,
    InvalidIspbError,
    InvalidPixKeyError,
    InvalidTaxIdError,
    MandateError,
    MandateInactiveError,
    MandateLimitExceededError,
    MandateNotFoundError,
    MandateStatus,
    MarginCallDefaultError,
    MarginCallReason,
    MarginCallStatus,
    Pacs002Message,
    Pacs008Message,
    PixAutomaticoMandate,
    PixAutomaticoMarginEngine,
    PixAutomaticoMarginError,
    PixAutomaticoValidationError,
    PixAutomaticoInvariantViolationError,
    PixAutomaticoSettlementReceipt,
    PixKeyType,
    RecurrencePurpose,
    SpiPaymentStatus,
    SpiPiAccount,
    SpiRejectReasonCode,
    generate_end_to_end_id,
    validate_cnpj,
    validate_cpf,
    validate_end_to_end_id,
    validate_ispb,
    validate_pix_key,
    validate_tax_id,
)

FIXTURES_DIR = ROOT_DIR / "fixtures"


class TestPixAutomaticoMarginCalls(unittest.TestCase):
    """Bateria exaustiva de testes do fluxo FL-090."""

    def setUp(self) -> None:
        self.engine = PixAutomaticoMarginEngine(
            ispb_camara="00997185",
            camara_account_number="PI-B3-CLEARING-MARGIN",
            camara_initial_balance=Decimal("500000000.00"),
        )
        self.fixture_file = FIXTURES_DIR / "pix_automatico_margin_calls.json"

        # Conta PI Itaú
        self.pi_itau = SpiPiAccount(
            ispb="60701190",
            account_number="PI-ITAU-001",
            participant_name="Banco Itaú Unibanco S.A.",
            balance=Decimal("100000000.00"),
            available_limit=Decimal("10000000.00"),
        )
        self.engine.register_pi_account(self.pi_itau)

        # Chave DICT
        self.engine.register_dict_key(
            pix_key="52998224725",
            key_type=PixKeyType.CPF,
            tax_id="52998224725",
            name="Investidor Fundo Alpha Qualificado",
            ispb="60701190",
            branch="0001",
            account_number="12345-6",
            is_active=True,
        )

        # Mandato Pix Automático Ativo
        self.mandate = PixAutomaticoMandate(
            mandate_id="MNDT-TEST-ALPHA-01",
            payer_tax_id="52998224725",
            payer_name="Investidor Fundo Alpha Qualificado",
            payer_ispb="60701190",
            payer_branch="0001",
            payer_account_number="12345-6",
            payer_pix_key="52998224725",
            max_limit_per_call=Decimal("10000000.00"),
            total_monthly_limit=Decimal("40000000.00"),
            status=MandateStatus.ACTIVE,
            authorization_channel=AuthorizationChannel.OPEN_FINANCE,
            recurrence_purpose=RecurrencePurpose.INTRADAY_MARGIN_CALL,
        )
        self.engine.register_mandate(self.mandate)

        # Conta de Colateral B3
        self.collateral_account = B3ParticipantCollateralAccount(
            participant_id="PL-ITAU-01",
            subaccount_id="SUB-ALPHA-101",
            tax_id="52998224725",
            cash_collateral_balance=Decimal("5000000.00"),
            required_margin=Decimal("5000000.00"),
            pretrade_risk_limit=Decimal("50000000.00"),
            pretrade_capacity_utilized=Decimal("15000000.00"),
        )
        self.engine.register_collateral_account(self.collateral_account)

    # -----------------------------------------------------------------------
    # 1. Testes de Validação Sintática e Algorítmica
    # -----------------------------------------------------------------------

    def test_tax_id_validation_cpf_cnpj(self) -> None:
        """Testa validação de CPF e CNPJ com algoritmos de módulo 11."""
        valid_cpf = "52998224725"
        self.assertEqual(validate_cpf(valid_cpf), valid_cpf)
        self.assertEqual(validate_tax_id(valid_cpf), valid_cpf)

        valid_cnpj = "00997185000150"
        self.assertEqual(validate_cnpj(valid_cnpj), valid_cnpj)
        self.assertEqual(validate_tax_id(valid_cnpj), valid_cnpj)

        # CPF com dígito inválido
        with self.assertRaises(InvalidTaxIdError):
            validate_cpf("52998224726")

        # Sequência repetida
        with self.assertRaises(InvalidTaxIdError):
            validate_cpf("11111111111")

        # CNPJ inválido
        with self.assertRaises(InvalidTaxIdError):
            validate_cnpj("00997185000199")

        # Tamanho inválido
        with self.assertRaises(InvalidTaxIdError):
            validate_tax_id("1234567")

    def test_ispb_and_e2e_id_validation(self) -> None:
        """Testa formato de ISPB (8 dígitos) e EndToEndId (32 caracteres)."""
        self.assertEqual(validate_ispb("00997185"), "00997185")
        with self.assertRaises(InvalidIspbError):
            validate_ispb("1234")
        with self.assertRaises(InvalidIspbError):
            validate_ispb("0099718A")

        now = datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)
        e2e = generate_end_to_end_id("60701190", timestamp=now, suffix="ALPHA123456")
        self.assertEqual(len(e2e), 32)
        self.assertTrue(e2e.startswith("E60701190202611051430"))
        self.assertEqual(validate_end_to_end_id(e2e), e2e)

        # Formatos inválidos
        with self.assertRaises(InvalidEndToEndIdError):
            validate_end_to_end_id("D60701190202611051430ALPHA123456")  # Não começa com E
        with self.assertRaises(InvalidEndToEndIdError):
            validate_end_to_end_id(e2e + "X")  # 33 caracteres
        with self.assertRaises(InvalidEndToEndIdError):
            validate_end_to_end_id(e2e[:31])  # 31 caracteres

    def test_pix_key_validation(self) -> None:
        """Testa validação de chaves Pix por tipologia no DICT."""
        self.assertTrue(validate_pix_key("52998224725", PixKeyType.CPF))
        self.assertFalse(validate_pix_key("00000000000", PixKeyType.CPF))

        self.assertTrue(validate_pix_key("00997185000150", PixKeyType.CNPJ))
        self.assertFalse(validate_pix_key("00997185000199", PixKeyType.CNPJ))

        self.assertTrue(validate_pix_key("investidor@fundo.com.br", PixKeyType.EMAIL))
        self.assertFalse(validate_pix_key("invalido@", PixKeyType.EMAIL))

        self.assertTrue(validate_pix_key("+5511999998888", PixKeyType.PHONE))
        self.assertFalse(validate_pix_key("11999998888", PixKeyType.PHONE))  # falta +

        self.assertTrue(validate_pix_key("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", PixKeyType.EVP))
        self.assertFalse(validate_pix_key("chave-aleatoria-invalida", PixKeyType.EVP))

    # -----------------------------------------------------------------------
    # 2. Testes de Mandato Pix Automático (Resolução BCB 341/2023)
    # -----------------------------------------------------------------------

    def test_mandate_lifecycle_and_limits(self) -> None:
        """Testa o ciclo de vida do mandato: vigência, limites, suspensão e revogação."""
        m = self.mandate
        now = datetime.now(timezone.utc)

        # Chamada dentro do limite
        valid, code, msg = m.is_valid_for_call(Decimal("5000000.00"), now)
        self.assertTrue(valid)
        self.assertIsNone(code)

        # Chamada excedendo teto por transação
        valid, code, msg = m.is_valid_for_call(Decimal("15000000.00"), now)
        self.assertFalse(valid)
        self.assertEqual(code, SpiRejectReasonCode.ED05)

        # Suspensão temporária
        self.engine.suspend_mandate(m.mandate_id, reason="Bloqueio preventivo pelo investidor")
        self.assertEqual(m.status, MandateStatus.SUSPENDED)
        valid, code, msg = m.is_valid_for_call(Decimal("1000000.00"), now)
        self.assertFalse(valid)
        self.assertEqual(code, SpiRejectReasonCode.RC07)

        # Reativação
        self.engine.reactivate_mandate(m.mandate_id)
        self.assertEqual(m.status, MandateStatus.ACTIVE)
        valid, code, msg = m.is_valid_for_call(Decimal("1000000.00"), now)
        self.assertTrue(valid)

        # Revogação permanente
        self.engine.revoke_mandate(m.mandate_id, reason="Cancelamento solicitado")
        self.assertEqual(m.status, MandateStatus.REVOKED)
        valid, code, msg = m.is_valid_for_call(Decimal("1000000.00"), now)
        self.assertFalse(valid)
        self.assertEqual(code, SpiRejectReasonCode.RC07)

    def test_mandate_monthly_accumulation(self) -> None:
        """Testa consumo acumulado do teto mensal do mandato."""
        m = self.mandate
        now = datetime.now(timezone.utc)

        # Consome R$ 35.000.000,00 de R$ 40.000.000,00
        m.apply_utilization(Decimal("35000000.00"))
        self.assertEqual(m.current_monthly_utilized, Decimal("35000000.00"))

        # Chamada de R$ 4.000.000,00 ainda cabe (total 39M <= 40M)
        valid, code, msg = m.is_valid_for_call(Decimal("4000000.00"), now)
        self.assertTrue(valid)

        # Chamada de R$ 6.000.000,00 ultrapassa o teto de 40M (35M + 6M = 41M)
        valid, code, msg = m.is_valid_for_call(Decimal("6000000.00"), now)
        self.assertFalse(valid)
        self.assertEqual(code, SpiRejectReasonCode.ED05)

    # -----------------------------------------------------------------------
    # 3. Emissão de Chamada de Margem CORE e Execução com Sucesso (ACSC)
    # -----------------------------------------------------------------------

    def test_successful_margin_call_settlement(self) -> None:
        """Testa o fluxo completo feliz: CORE dispara -> Pix Automático -> SPI ACSC -> D0 Settled."""
        call_amount = Decimal("3500000.00")
        mc = self.engine.issue_margin_call(
            participant_id="PL-ITAU-01",
            subaccount_id="SUB-ALPHA-101",
            payer_tax_id="52998224725",
            deficit_amount=call_amount,
            reason=MarginCallReason.INTRADAY_CORE_DEFICIT,
            deadline_seconds=10,
        )

        self.assertEqual(mc.status, MarginCallStatus.ISSUED)
        self.assertEqual(mc.deficit_amount, call_amount)
        self.assertEqual(self.collateral_account.margin_deficit, call_amount)

        # Saldo inicial das contas PI
        itau_initial = self.pi_itau.balance
        b3_initial = self.engine.pi_b3.balance
        collateral_initial = self.collateral_account.cash_collateral_balance

        # Processa a liquidação instantânea (SLA 1.5s < 10s)
        receipt = self.engine.process_margin_call_settlement(
            margin_call_id=mc.margin_call_id,
            simulated_latency_seconds=1.5,
        )

        # Verificações do Recibo
        self.assertEqual(receipt.status, SpiPaymentStatus.ACSC)
        self.assertTrue(receipt.sla_met)
        self.assertFalse(receipt.idempotent_replay)
        self.assertEqual(receipt.amount, call_amount)
        self.assertEqual(receipt.pretrade_capacity_restored, call_amount)

        # Status da Chamada
        self.assertEqual(mc.status, MarginCallStatus.SETTLED)
        self.assertEqual(mc.settled_amount, call_amount)
        self.assertIsNotNone(mc.settled_timestamp)

        # Débito no Pagador e Crédito na B3 (SOMA-ZERO EXATA)
        self.assertEqual(self.pi_itau.balance, itau_initial - call_amount)
        self.assertEqual(self.engine.pi_b3.balance, b3_initial + call_amount)

        # Colateral aportado na B3 e déficit zerado
        self.assertEqual(self.collateral_account.cash_collateral_balance, collateral_initial + call_amount)
        self.assertEqual(self.collateral_account.margin_deficit, Decimal("0.00"))

        # Mandato atualizado
        self.assertEqual(self.mandate.current_monthly_utilized, call_amount)

        # Mensagens ISO 20022 geradas
        pacs008 = list(self.engine.pacs008_messages.values())[0]
        pacs002 = list(self.engine.pacs002_messages.values())[0]
        self.assertEqual(pacs008.amount, call_amount)
        self.assertEqual(pacs002.status, SpiPaymentStatus.ACSC)
        self.assertIn("<Document", pacs008.to_xml())
        self.assertIn("<Document", pacs002.to_xml())

    # -----------------------------------------------------------------------
    # 4. Testes de Idempotência e Unicidade
    # -----------------------------------------------------------------------

    def test_idempotent_replay_protection(self) -> None:
        """Testa que reprocessar a mesma chamada não duplica débito nem crédito."""
        call_amount = Decimal("2000000.00")
        mc = self.engine.issue_margin_call(
            participant_id="PL-ITAU-01",
            subaccount_id="SUB-ALPHA-101",
            payer_tax_id="52998224725",
            deficit_amount=call_amount,
        )

        receipt1 = self.engine.process_margin_call_settlement(mc.margin_call_id)
        self.assertEqual(receipt1.status, SpiPaymentStatus.ACSC)
        self.assertFalse(receipt1.idempotent_replay)

        itau_balance_after_first = self.pi_itau.balance
        b3_balance_after_first = self.engine.pi_b3.balance

        # Segunda chamada para a mesma margin_call_id
        receipt2 = self.engine.process_margin_call_settlement(mc.margin_call_id)
        self.assertEqual(receipt2.status, SpiPaymentStatus.ACSC)
        self.assertTrue(receipt2.idempotent_replay)
        self.assertEqual(receipt2.e2e_id, receipt1.e2e_id)

        # Saldos PI e colaterais devem permanecer rigorosamente inalterados
        self.assertEqual(self.pi_itau.balance, itau_balance_after_first)
        self.assertEqual(self.engine.pi_b3.balance, b3_balance_after_first)
        self.assertEqual(self.mandate.current_monthly_utilized, call_amount)

    # -----------------------------------------------------------------------
    # 5. Testes de Rejeição e Default Imediato
    # -----------------------------------------------------------------------

    def test_rejection_insufficient_funds_triggers_default(self) -> None:
        """Testa saldo insuficiente na Conta PI (AM04) gerando default compulsório imediato."""
        # Configura Conta PI com saldo baixo
        self.pi_itau.balance = Decimal("100000.00")
        self.pi_itau.available_limit = Decimal("0.00")
        self.pi_itau.initial_balance = Decimal("100000.00")

        call_amount = Decimal("1000000.00")
        mc = self.engine.issue_margin_call(
            participant_id="PL-ITAU-01",
            subaccount_id="SUB-ALPHA-101",
            payer_tax_id="52998224725",
            deficit_amount=call_amount,
        )

        receipt = self.engine.process_margin_call_settlement(mc.margin_call_id)

        # Validações do Default
        self.assertEqual(receipt.status, SpiPaymentStatus.RJCT)
        self.assertEqual(receipt.reject_code, SpiRejectReasonCode.AM04.value)
        self.assertEqual(receipt.risk_status_after, "DEFAULTED")

        self.assertEqual(mc.status, MarginCallStatus.DEFAULTED)
        self.assertTrue(mc.default_triggered)
        self.assertIn("AM04", mc.failure_reason or "")

        # Conta de Colateral bloqueada para trading e capacidade pré-trade zerada
        self.assertTrue(self.collateral_account.is_in_default)
        self.assertTrue(self.collateral_account.is_blocked_for_trading)
        self.assertEqual(self.collateral_account.available_pretrade_capacity, Decimal("0.00"))

        # Nenhum centavo transferido
        self.assertEqual(self.pi_itau.balance, Decimal("100000.00"))
        self.assertEqual(self.engine.pi_b3.balance, Decimal("500000000.00"))

    def test_rejection_revoked_mandate_triggers_default(self) -> None:
        """Testa rejeição por mandato revogado (RC07) com default imediato."""
        self.mandate.revoke(reason="Ordem judicial")

        mc = self.engine.issue_margin_call(
            participant_id="PL-ITAU-01",
            subaccount_id="SUB-ALPHA-101",
            payer_tax_id="52998224725",
            deficit_amount=Decimal("500000.00"),
        )

        receipt = self.engine.process_margin_call_settlement(mc.margin_call_id)
        self.assertEqual(receipt.status, SpiPaymentStatus.RJCT)
        self.assertEqual(receipt.reject_code, SpiRejectReasonCode.RC07.value)
        self.assertEqual(mc.status, MarginCallStatus.DEFAULTED)
        self.assertTrue(self.collateral_account.is_in_default)

    def test_rejection_mandate_limit_exceeded_triggers_default(self) -> None:
        """Testa rejeição quando chamada excede teto por operação do mandato (ED05)."""
        call_amount = Decimal("15000000.00")  # Mandato autoriza até 10M
        mc = self.engine.issue_margin_call(
            participant_id="PL-ITAU-01",
            subaccount_id="SUB-ALPHA-101",
            payer_tax_id="52998224725",
            deficit_amount=call_amount,
        )

        receipt = self.engine.process_margin_call_settlement(mc.margin_call_id)
        self.assertEqual(receipt.status, SpiPaymentStatus.RJCT)
        self.assertEqual(receipt.reject_code, SpiRejectReasonCode.ED05.value)
        self.assertEqual(mc.status, MarginCallStatus.DEFAULTED)

    def test_rejection_spi_sla_timeout_triggers_default(self) -> None:
        """Testa timeout operacional do SPI (> 10s) rejeitando com código AB03."""
        call_amount = Decimal("1000000.00")
        mc = self.engine.issue_margin_call(
            participant_id="PL-ITAU-01",
            subaccount_id="SUB-ALPHA-101",
            payer_tax_id="52998224725",
            deficit_amount=call_amount,
        )

        # Simula latência de 12 segundos (> SLA de 10s)
        receipt = self.engine.process_margin_call_settlement(
            margin_call_id=mc.margin_call_id,
            simulated_latency_seconds=12.0,
        )
        self.assertEqual(receipt.status, SpiPaymentStatus.RJCT)
        self.assertEqual(receipt.reject_code, SpiRejectReasonCode.AB03.value)
        self.assertEqual(mc.status, MarginCallStatus.DEFAULTED)

    # -----------------------------------------------------------------------
    # 6. Testes das Invariantes Formais (verify_invariants)
    # -----------------------------------------------------------------------

    def test_verify_invariants_full_compliance(self) -> None:
        """Testa que todas as 6 invariantes formais são estritamente satisfeitas após operações válidas."""
        # Executa uma liquidação válida
        mc = self.engine.issue_margin_call(
            participant_id="PL-ITAU-01",
            subaccount_id="SUB-ALPHA-101",
            payer_tax_id="52998224725",
            deficit_amount=Decimal("4000000.00"),
        )
        self.engine.process_margin_call_settlement(mc.margin_call_id, simulated_latency_seconds=2.0)

        invariants = self.engine.verify_invariants(raise_on_error=True)
        self.assertTrue(invariants["spi_financial_conservation"])
        self.assertTrue(invariants["e2e_id_uniqueness_and_format"])
        self.assertTrue(invariants["irrevocability_and_non_repudiation"])
        self.assertTrue(invariants["risk_capacity_consistency"])
        self.assertTrue(invariants["mandate_compliance_integrity"])
        self.assertTrue(invariants["sla_latency_compliance"])
        self.assertTrue(invariants["all_satisfied"])

    def test_invariants_failure_detection_on_balance_manipulation(self) -> None:
        """Testa detecção e levantamento de PixAutomaticoInvariantViolationError se houver adulteração contábil."""
        mc = self.engine.issue_margin_call(
            participant_id="PL-ITAU-01",
            subaccount_id="SUB-ALPHA-101",
            payer_tax_id="52998224725",
            deficit_amount=Decimal("1000000.00"),
        )
        self.engine.process_margin_call_settlement(mc.margin_call_id)

        # Adulteração arbitrária no saldo da B3 (criando R$ 50,00 do nada)
        self.engine.pi_b3.balance += Decimal("50.00")

        with self.assertRaises(PixAutomaticoInvariantViolationError):
            self.engine.verify_invariants(raise_on_error=True)

    # -----------------------------------------------------------------------
    # 7. Testes de Carga de Fixture e Serialização to_dict()
    # -----------------------------------------------------------------------

    def test_load_fixture_and_execute_scenario(self) -> None:
        """Testa a carga do arquivo JSON de fixture e execução de múltiplos participantes."""
        fixture_engine = PixAutomaticoMarginEngine(ispb_camara="00997185")
        fixture_engine.load_fixture(self.fixture_file)

        # Verifica contas carregadas
        self.assertIn("60701190", fixture_engine.pi_accounts)
        self.assertIn("30306294", fixture_engine.pi_accounts)
        self.assertIn("02332886", fixture_engine.pi_accounts)
        self.assertIn("60746948", fixture_engine.pi_accounts)

        # BTG Pactual - Execução de chamada de margem com sucesso
        btg_mc = fixture_engine.issue_margin_call(
            participant_id="PL-BTG-01",
            subaccount_id="SUB-BTG-202",
            payer_tax_id="30306294000145",
            deficit_amount=Decimal("8500000.00"),
        )
        btg_receipt = fixture_engine.process_margin_call_settlement(btg_mc.margin_call_id)
        self.assertEqual(btg_receipt.status, SpiPaymentStatus.ACSC)
        self.assertEqual(btg_mc.status, MarginCallStatus.SETTLED)

        # Bradesco - Mandato cadastrado como REVOKED no fixture -> deve falhar em DEFAULT
        brad_mc = fixture_engine.issue_margin_call(
            participant_id="PL-BRAD-01",
            subaccount_id="SUB-BRAD-404",
            payer_tax_id="60746948000112",
            deficit_amount=Decimal("1500000.00"),
        )
        brad_receipt = fixture_engine.process_margin_call_settlement(brad_mc.margin_call_id)
        self.assertEqual(brad_receipt.status, SpiPaymentStatus.RJCT)
        self.assertEqual(brad_receipt.reject_code, SpiRejectReasonCode.RC07.value)
        self.assertEqual(brad_mc.status, MarginCallStatus.DEFAULTED)

        # Auditoria de invariantes da base carregada
        inv = fixture_engine.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["all_satisfied"])

        # Teste to_dict
        d = fixture_engine.to_dict()
        self.assertEqual(d["ispb_camara"], "00997185")
        self.assertEqual(len(d["pi_accounts"]), 5)
        self.assertEqual(len(d["margin_calls"]), 2)
        self.assertTrue(d["invariants"]["all_satisfied"])

    # -----------------------------------------------------------------------
    # 8. Testes Adicionais de Casos de Borda e Resiliência
    # -----------------------------------------------------------------------

    def test_mandate_dates_past_and_future(self) -> None:
        """Testa validações de data de início futura e data de expiração passada no mandato."""
        now = datetime.now(timezone.utc)
        
        # Mandato no futuro
        future_mandate = PixAutomaticoMandate(
            mandate_id="MNDT-FUTURE",
            payer_tax_id="52998224725",
            payer_name="Investidor",
            payer_ispb="60701190",
            payer_branch="0001",
            payer_account_number="12345-6",
            start_date=now + timedelta(days=10),
            max_limit_per_call=Decimal("1000000.00"),
            total_monthly_limit=Decimal("5000000.00"),
        )
        valid, code, msg = future_mandate.is_valid_for_call(Decimal("500000.00"), now)
        self.assertFalse(valid)
        self.assertEqual(code, SpiRejectReasonCode.RC07)
        self.assertIn("vigência", msg or "")

        # Mandato expirado no passado
        expired_mandate = PixAutomaticoMandate(
            mandate_id="MNDT-EXPIRED",
            payer_tax_id="52998224725",
            payer_name="Investidor",
            payer_ispb="60701190",
            payer_branch="0001",
            payer_account_number="12345-6",
            start_date=now - timedelta(days=60),
            expiration_date=now - timedelta(days=1),
            max_limit_per_call=Decimal("1000000.00"),
            total_monthly_limit=Decimal("5000000.00"),
        )
        valid, code, msg = expired_mandate.is_valid_for_call(Decimal("500000.00"), now)
        self.assertFalse(valid)
        self.assertEqual(code, SpiRejectReasonCode.RC07)
        self.assertIn("expirado", msg or "")

    def test_inactive_or_missing_dict_key_triggers_default(self) -> None:
        """Testa chave DICT inativa gerando rejeição BE05 e default da conta."""
        # Cadastra chave inativa
        self.engine.register_dict_key(
            pix_key="inativo@fundo.com.br",
            key_type=PixKeyType.EMAIL,
            tax_id="52998224725",
            name="Investidor Alpha",
            ispb="60701190",
            branch="0001",
            account_number="12345-6",
            is_active=False,
        )
        self.mandate.payer_pix_key = "inativo@fundo.com.br"

        mc = self.engine.issue_margin_call(
            participant_id="PL-ITAU-01",
            subaccount_id="SUB-ALPHA-101",
            payer_tax_id="52998224725",
            deficit_amount=Decimal("1000000.00"),
        )

        receipt = self.engine.process_margin_call_settlement(mc.margin_call_id)
        self.assertEqual(receipt.status, SpiPaymentStatus.RJCT)
        self.assertEqual(receipt.reject_code, SpiRejectReasonCode.BE05.value)
        self.assertEqual(mc.status, MarginCallStatus.DEFAULTED)
        self.assertTrue(self.collateral_account.is_in_default)

    def test_multiple_interbank_settlements_soma_zero(self) -> None:
        """Testa liquidação concorrente/múltipla de chamadas de margem em múltiplos bancos."""
        # Cadastra BTG e XP
        pi_btg = SpiPiAccount(
            ispb="30306294",
            account_number="PI-BTG-001",
            participant_name="BTG Pactual",
            balance=Decimal("200000000.00"),
            available_limit=Decimal("50000000.00"),
        )
        self.engine.register_pi_account(pi_btg)

        mandate_btg = PixAutomaticoMandate(
            mandate_id="MNDT-BTG-99",
            payer_tax_id="30306294000145",
            payer_name="BTG Pactual",
            payer_ispb="30306294",
            payer_branch="0001",
            payer_account_number="98765-4",
            max_limit_per_call=Decimal("30000000.00"),
            total_monthly_limit=Decimal("100000000.00"),
        )
        self.engine.register_mandate(mandate_btg)

        collateral_btg = B3ParticipantCollateralAccount(
            participant_id="PL-BTG-01",
            subaccount_id="SUB-BTG-202",
            tax_id="30306294000145",
            cash_collateral_balance=Decimal("10000000.00"),
            required_margin=Decimal("10000000.00"),
        )
        self.engine.register_collateral_account(collateral_btg)

        # Executa chamadas para Itaú e BTG
        mc1 = self.engine.issue_margin_call(
            participant_id="PL-ITAU-01",
            subaccount_id="SUB-ALPHA-101",
            payer_tax_id="52998224725",
            deficit_amount=Decimal("2500000.00"),
        )
        mc2 = self.engine.issue_margin_call(
            participant_id="PL-BTG-01",
            subaccount_id="SUB-BTG-202",
            payer_tax_id="30306294000145",
            deficit_amount=Decimal("17500000.00"),
        )

        r1 = self.engine.process_margin_call_settlement(mc1.margin_call_id)
        r2 = self.engine.process_margin_call_settlement(mc2.margin_call_id)

        self.assertEqual(r1.status, SpiPaymentStatus.ACSC)
        self.assertEqual(r2.status, SpiPaymentStatus.ACSC)

        # Total debitado Itaú + BTG deve ser exatamente 20.000.000,00
        self.assertEqual(self.pi_itau.total_debited, Decimal("2500000.00"))
        self.assertEqual(pi_btg.total_debited, Decimal("17500000.00"))
        self.assertEqual(self.engine.pi_b3.total_credited, Decimal("20000000.00"))

        # Conservação formal comprovada
        inv = self.engine.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["spi_financial_conservation"])
        self.assertTrue(inv["all_satisfied"])

    def test_xml_iso20022_conformance(self) -> None:
        """Testa geração e validação estrutural de XML ISO 20022 para pacs.008 e pacs.002."""
        now = datetime.now(timezone.utc)
        e2e = generate_end_to_end_id("60701190", now, "TESTXML1234")
        pacs008 = Pacs008Message(
            msg_id="MSG-TEST-008",
            e2e_id=e2e,
            mandate_id="MNDT-001",
            amount=Decimal("1250000.50"),
            debtor_tax_id="52998224725",
            debtor_name="Investidor Alpha",
            debtor_ispb="60701190",
            debtor_account="12345-6",
            creditor_tax_id="00997185000150",
            creditor_name="B3 S.A.",
            creditor_ispb="00997185",
            creditor_account="PI-B3-CLEARING",
        )
        xml008 = pacs008.to_xml()
        self.assertIn("urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08", xml008)
        self.assertIn(e2e, xml008)
        self.assertIn("1250000.50", xml008)

        pacs002 = Pacs002Message(
            msg_id="MSG-TEST-002",
            original_msg_id="MSG-TEST-008",
            original_e2e_id=e2e,
            status=SpiPaymentStatus.ACSC,
            settlement_timestamp=now,
            latency_ms=850.0,
        )
        xml002 = pacs002.to_xml()
        self.assertIn("urn:iso:std:iso:20022:tech:xsd:pacs.002.001.10", xml002)
        self.assertIn("ACSC", xml002)
        self.assertIn(e2e, xml002)

    def test_invalid_parameters_raise_validation_errors(self) -> None:
        """Testa validações de parâmetros negativos, limites inconsistentes e erros cadastrais."""
        # Déficit negativo
        with self.assertRaises(PixAutomaticoValidationError):
            self.engine.issue_margin_call(
                participant_id="PL-ITAU-01",
                subaccount_id="SUB-ALPHA-101",
                payer_tax_id="52998224725",
                deficit_amount=Decimal("-100.00"),
            )

        # Limite máximo por chamada negativo
        with self.assertRaises(PixAutomaticoValidationError):
            PixAutomaticoMandate(
                mandate_id="MNDT-INV",
                payer_tax_id="52998224725",
                payer_name="Investidor",
                payer_ispb="60701190",
                payer_branch="0001",
                payer_account_number="12345-6",
                max_limit_per_call=Decimal("-1000.00"),
                total_monthly_limit=Decimal("5000.00"),
            )

        # Limite mensal menor que o limite por chamada
        with self.assertRaises(PixAutomaticoValidationError):
            PixAutomaticoMandate(
                mandate_id="MNDT-INV2",
                payer_tax_id="52998224725",
                payer_name="Investidor",
                payer_ispb="60701190",
                payer_branch="0001",
                payer_account_number="12345-6",
                max_limit_per_call=Decimal("10000.00"),
                total_monthly_limit=Decimal("5000.00"),
            )

    def test_unknown_identifiers_raise_not_found(self) -> None:
        """Testa que buscar ou revogar mandatos e chamadas inexistentes levanta erro apropriado."""
        with self.assertRaises(MandateNotFoundError):
            self.engine.revoke_mandate("MNDT-NAO-EXISTE")

        with self.assertRaises(MandateNotFoundError):
            self.engine.suspend_mandate("MNDT-NAO-EXISTE")

        with self.assertRaises(MandateNotFoundError):
            self.engine.reactivate_mandate("MNDT-NAO-EXISTE")

        with self.assertRaises(PixAutomaticoValidationError):
            self.engine.process_margin_call_settlement("MC-NAO-EXISTE")


if __name__ == "__main__":
    unittest.main()
