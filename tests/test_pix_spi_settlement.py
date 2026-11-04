"""
Testes unitários e de integração para o motor de Liquidação Instantânea via SPI / Pix (FL-064).
=============================================================================================
Cobre:
- Validação estrita de ISPB, EndToEndId e chaves Pix (CPF, CNPJ, Email, Phone, EVP);
- Geração e parsing bidirecional de mensagens XML ISO 20022 (pacs.008 e pacs.002);
- Liquidação imediata D0 de Cotas de Fundos (CVM 175) e Títulos Escriturais com DvP atômico;
- Reversão atômica (rollback seguro) em caso de rejeição no SPI (pacs.002 RJCT);
- Idempotência absoluta da mensageria SPI (reexecução de EndToEndId);
- Reconciliação do canal STR (STR0007 / STR0008) e prova de conservação financeira.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import io
import json
from pathlib import Path
import sys
import unittest

# Garante inclusão da raiz do projeto no sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.pix_spi_settlement import (
    AccountType,
    AssetType,
    BookEntrySecurityAsset,
    CustodyAccount,
    DvpStatus,
    FundShareAsset,
    InsufficientCustodyError,
    InsufficientFundsError,
    InvalidEndToEndIdError,
    InvalidIspbError,
    InvalidPixKeyError,
    Pacs002Message,
    Pacs008Message,
    PiAccount,
    PixKey,
    PixKeyType,
    PixSpiSettlementEngine,
    SpiSettlementError,
    SpiSettlementReceipt,
    SpiTransactionStatus,
    SpiValidationError,
    SpiXmlError,
    StrReconciliationMismatchError,
    StrReconciliationSummary,
    StrTransferRecord,
    StrTransferType,
    generate_end_to_end_id,
    validate_cnpj,
    validate_cpf,
    validate_end_to_end_id,
    validate_ispb,
    validate_pix_key,
)

FIXTURES_DIR = ROOT_DIR / "fixtures"


class TestPixSpiSettlement(unittest.TestCase):
    """Bateria completa de testes do fluxo FL-064."""

    def setUp(self) -> None:
        self.engine = PixSpiSettlementEngine(ispb_camara="00997185")
        self.fixture_path = FIXTURES_DIR / "pix_spi_settlement.json"

        # Cadastro padrão para testes isolados
        self.pi_itau = PiAccount(
            ispb="60701190",
            account_number="PI-ITAU-TEST",
            participant_name="Itaú Unibanco",
            balance=Decimal("10000000.00"),
            available_limit=Decimal("2000000.00"),
        )
        self.pi_btg = PiAccount(
            ispb="30306294",
            account_number="PI-BTG-TEST",
            participant_name="BTG Pactual",
            balance=Decimal("5000000.00"),
            available_limit=Decimal("1000000.00"),
        )
        self.engine.register_pi_account(self.pi_itau)
        self.engine.register_pi_account(self.pi_btg)

        self.custody_buyer = CustodyAccount(
            account_id="CUST-BUYER-01",
            participant_id="PART-ITAU-01",
            participant_name="Investidor Comprador",
            tax_id="52998224725",
            fund_shares={"HGLG11": Decimal("0.00")},
            securities={"BRCDB0012345": Decimal("0")},
        )
        self.custody_seller = CustodyAccount(
            account_id="CUST-SELLER-01",
            participant_id="PART-BTG-01",
            participant_name="Fundo / Emissor Vendedor",
            tax_id="30306294000145",
            fund_shares={"HGLG11": Decimal("1000.00")},
            securities={"BRCDB0012345": Decimal("500")},
        )
        self.engine.register_custody_account(self.custody_buyer)
        self.engine.register_custody_account(self.custody_seller)

    # -----------------------------------------------------------------------
    # 1. Validação de ISPB
    # -----------------------------------------------------------------------

    def test_validate_ispb_valid(self) -> None:
        """Valida que ISPBs numéricos de 8 dígitos são aceitos."""
        self.assertEqual(validate_ispb("60701190"), "60701190")
        self.assertEqual(validate_ispb("00997185"), "00997185")
        self.assertEqual(validate_ispb("00000000"), "00000000")
        self.assertEqual(validate_ispb(997185), "00997185")
        self.assertEqual(validate_ispb(60701190), "60701190")

    def test_validate_ispb_invalid(self) -> None:
        """Rejeita ISPBs malformatados, nulos ou fora do padrão."""
        invalid_cases = ["", None, "1234567", "123456789", "ABCDEFGH", -5, 100000000]
        for val in invalid_cases:
            with self.subTest(val=val):
                with self.assertRaises(InvalidIspbError):
                    validate_ispb(val)

    # -----------------------------------------------------------------------
    # 2. Validação e Geração de EndToEndId
    # -----------------------------------------------------------------------

    def test_generate_and_validate_end_to_end_id(self) -> None:
        """Gera e valida EndToEndId regulamentar de 32 caracteres do BACEN."""
        dt = datetime(2026, 11, 4, 10, 30, tzinfo=timezone.utc)
        e2e = generate_end_to_end_id("60701190", timestamp=dt, sequence="SEQ00000001")
        self.assertEqual(len(e2e), 32)
        self.assertTrue(e2e.startswith("E60701190202611041030"))
        self.assertTrue(validate_end_to_end_id(e2e))

    def test_validate_end_to_end_id_invalid(self) -> None:
        """Rejeita EndToEndIds fora da especificação técnica do SPI."""
        invalid_e2e = [
            "",
            None,
            "E60701190202611041030SEQ0000000",      # 31 caracteres
            "E60701190202611041030SEQ000000001",    # 33 caracteres
            "D60701190202611041030SEQ00000001",     # Não inicia com 'E'
            "EABCDEFGH202611041030SEQ00000001",     # ISPB não numérico
            "E60701190202611041030SEQ!@#$$%&*",     # Caracteres especiais
        ]
        for item in invalid_e2e:
            with self.subTest(item=item):
                self.assertFalse(validate_end_to_end_id(item))

    # -----------------------------------------------------------------------
    # 3. Validação de Chaves Pix (DICT)
    # -----------------------------------------------------------------------

    def test_validate_pix_keys(self) -> None:
        """Testa validação de chaves Pix em todas as suas tipologias."""
        # CPF
        self.assertTrue(validate_pix_key(PixKeyType.CPF, "52998224725"))
        self.assertTrue(validate_pix_key(PixKeyType.CPF, "529.982.247-25"))
        self.assertFalse(validate_pix_key(PixKeyType.CPF, "11111111111"))
        self.assertFalse(validate_pix_key(PixKeyType.CPF, "12345678900"))

        # CNPJ
        self.assertTrue(validate_pix_key(PixKeyType.CNPJ, "60701190000104"))
        self.assertTrue(validate_pix_key(PixKeyType.CNPJ, "60.701.190/0001-04"))
        self.assertFalse(validate_pix_key(PixKeyType.CNPJ, "00000000000000"))
        self.assertFalse(validate_pix_key(PixKeyType.CNPJ, "60701190000199"))

        # EMAIL
        self.assertTrue(validate_pix_key(PixKeyType.EMAIL, "contato@fundo.com.br"))
        self.assertFalse(validate_pix_key(PixKeyType.EMAIL, "contato_invalido"))

        # PHONE
        self.assertTrue(validate_pix_key(PixKeyType.PHONE, "+5511999998888"))
        self.assertFalse(validate_pix_key(PixKeyType.PHONE, "telefone-invalido"))

        # EVP
        self.assertTrue(validate_pix_key(PixKeyType.EVP, "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"))
        self.assertFalse(validate_pix_key(PixKeyType.EVP, "uuid-invalido"))

    # -----------------------------------------------------------------------
    # 4. Geração e Parsing de XML ISO 20022 (pacs.008 e pacs.002)
    # -----------------------------------------------------------------------

    def test_pacs008_xml_roundtrip(self) -> None:
        """Valida geração e parsing bidirecional de mensagem pacs.008."""
        e2e = generate_end_to_end_id("60701190")
        msg = Pacs008Message(
            msg_id="MSG-SPI-001",
            creation_date_time=datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc),
            end_to_end_id=e2e,
            tx_id="TX-PIX-001",
            amount=Decimal("15000.00"),
            settlement_date=date(2026, 11, 4),
            debtor_name="Investidor Itaú",
            debtor_tax_id="52998224725",
            debtor_ispb="60701190",
            debtor_account="CC-100",
            creditor_name="Fundo BTG",
            creditor_tax_id="30306294000145",
            creditor_ispb="30306294",
            creditor_account="CC-200",
            asset_type=AssetType.INVESTMENT_FUND_SHARE,
            asset_identifier="HGLG11",
            asset_quantity=Decimal("100"),
            asset_unit_price=Decimal("150.00"),
            remittance_info="ASSET:HGLG11;QTY:100;PU:150.00",
        )

        xml_str = self.engine.create_pacs008_xml(msg)
        self.assertIn("<Document", xml_str)
        self.assertIn("<EndToEndId>" + e2e + "</EndToEndId>", xml_str)
        self.assertIn("15000.00", xml_str)

        parsed = self.engine.parse_pacs008_xml(xml_str)
        self.assertEqual(parsed.end_to_end_id, e2e)
        self.assertEqual(parsed.amount, Decimal("15000.00"))
        self.assertEqual(parsed.asset_identifier, "HGLG11")
        self.assertEqual(parsed.asset_quantity, Decimal("100"))

    def test_pacs002_xml_roundtrip(self) -> None:
        """Valida geração e parsing bidirecional de mensagem pacs.002 (ACSC e RJCT)."""
        e2e = generate_end_to_end_id("60701190")
        
        # Teste ACSC
        msg_acsc = Pacs002Message(
            msg_id="MSG-STS-001",
            creation_date_time=datetime(2026, 11, 4, 10, 1, tzinfo=timezone.utc),
            original_end_to_end_id=e2e,
            original_tx_id="TX-001",
            transaction_status=SpiTransactionStatus.ACSC,
            settlement_timestamp=datetime(2026, 11, 4, 10, 1, 5, tzinfo=timezone.utc),
            clearing_system_ref="SPI-PROT-9988",
        )
        xml_acsc = self.engine.create_pacs002_xml(msg_acsc)
        self.assertIn("<TxSts>ACSC</TxSts>", xml_acsc)
        parsed_acsc = self.engine.parse_pacs002_xml(xml_acsc)
        self.assertEqual(parsed_acsc.transaction_status, SpiTransactionStatus.ACSC)
        self.assertEqual(parsed_acsc.original_end_to_end_id, e2e)

        # Teste RJCT
        msg_rjct = Pacs002Message(
            msg_id="MSG-STS-002",
            creation_date_time=datetime(2026, 11, 4, 10, 2, tzinfo=timezone.utc),
            original_end_to_end_id=e2e,
            original_tx_id="TX-001",
            transaction_status=SpiTransactionStatus.RJCT,
            status_reason_code="AM04",
            status_reason_detail="Saldo insuficiente na Conta PI",
        )
        xml_rjct = self.engine.create_pacs002_xml(msg_rjct)
        self.assertIn("<TxSts>RJCT</TxSts>", xml_rjct)
        self.assertIn("<Prtry>AM04</Prtry>", xml_rjct)
        parsed_rjct = self.engine.parse_pacs002_xml(xml_rjct)
        self.assertEqual(parsed_rjct.transaction_status, SpiTransactionStatus.RJCT)
        self.assertEqual(parsed_rjct.status_reason_code, "AM04")

    # -----------------------------------------------------------------------
    # 5. Liquidação Imediata D0 de Cotas de Fundos (CVM 175)
    # -----------------------------------------------------------------------

    def test_fund_share_settlement_d0_success(self) -> None:
        """Executa liquidação D0 com sucesso de cotas de fundos e confirma DvP."""
        e2e = generate_end_to_end_id("60701190")
        qty = Decimal("50.00")
        pu = Decimal("160.00")
        total = qty * pu  # 8000.00

        pacs008 = Pacs008Message(
            msg_id="MSG-SPI-FND-01",
            creation_date_time=datetime.now(timezone.utc),
            end_to_end_id=e2e,
            tx_id="TX-FND-01",
            amount=total,
            settlement_date=date.today(),
            debtor_name="Investidor Comprador",
            debtor_tax_id="52998224725",
            debtor_ispb="60701190",
            debtor_account="CC-100",
            creditor_name="Fundo BTG",
            creditor_tax_id="30306294000145",
            creditor_ispb="30306294",
            creditor_account="CC-200",
            asset_type=AssetType.INVESTMENT_FUND_SHARE,
            asset_identifier="HGLG11",
            asset_quantity=qty,
            asset_unit_price=pu,
        )

        # 1. Envio de pacs.008 -> Reserva de custódia e retenção financeira
        rec1 = self.engine.submit_credit_transfer(pacs008, "CUST-SELLER-01", "CUST-BUYER-01")
        self.assertEqual(rec1.dvp_status, DvpStatus.RESERVED)
        self.assertEqual(self.custody_seller.fund_shares["HGLG11"], Decimal("950.00")) # 1000 - 50
        self.assertEqual(self.custody_buyer.fund_shares["HGLG11"], Decimal("0.00"))   # Ainda não entregue
        self.assertEqual(self.pi_itau.in_transit_debits, total)

        # 2. Confirmação pacs.002 ACSC -> Liquidação definitiva
        pacs002 = Pacs002Message(
            msg_id="STS-FND-01",
            creation_date_time=datetime.now(timezone.utc),
            original_end_to_end_id=e2e,
            original_tx_id="TX-FND-01",
            transaction_status=SpiTransactionStatus.ACSC,
            settlement_timestamp=datetime.now(timezone.utc),
        )
        rec2 = self.engine.process_pacs002_status_report(pacs002)
        self.assertEqual(rec2.dvp_status, DvpStatus.SETTLED)
        self.assertEqual(self.custody_buyer.fund_shares["HGLG11"], Decimal("50.00"))
        self.assertEqual(self.pi_itau.balance, Decimal("9992000.00")) # 10M - 8000
        self.assertEqual(self.pi_btg.balance, Decimal("5008000.00"))  # 5M + 8000
        self.assertEqual(self.pi_itau.in_transit_debits, Decimal("0.00"))

    # -----------------------------------------------------------------------
    # 6. Liquidação Imediata D0 de Títulos Escriturais
    # -----------------------------------------------------------------------

    def test_security_settlement_d0_success(self) -> None:
        """Executa liquidação D0 com sucesso de títulos escriturais (CDB)."""
        e2e = generate_end_to_end_id("60701190")
        qty = Decimal("20")
        pu = Decimal("1000.00")
        total = qty * pu  # 20000.00

        pacs008 = Pacs008Message(
            msg_id="MSG-SPI-SEC-01",
            creation_date_time=datetime.now(timezone.utc),
            end_to_end_id=e2e,
            tx_id="TX-SEC-01",
            amount=total,
            settlement_date=date.today(),
            debtor_name="Investidor Itaú",
            debtor_tax_id="52998224725",
            debtor_ispb="60701190",
            debtor_account="CC-100",
            creditor_name="Emissor BTG",
            creditor_tax_id="30306294000145",
            creditor_ispb="30306294",
            creditor_account="CC-200",
            asset_type=AssetType.BOOK_ENTRY_SECURITY,
            asset_identifier="BRCDB0012345",
            asset_quantity=qty,
            asset_unit_price=pu,
        )

        rec1 = self.engine.submit_credit_transfer(pacs008, "CUST-SELLER-01", "CUST-BUYER-01")
        self.assertEqual(rec1.dvp_status, DvpStatus.RESERVED)
        self.assertEqual(self.custody_seller.securities["BRCDB0012345"], Decimal("480"))

        pacs002 = Pacs002Message(
            msg_id="STS-SEC-01",
            creation_date_time=datetime.now(timezone.utc),
            original_end_to_end_id=e2e,
            original_tx_id="TX-SEC-01",
            transaction_status=SpiTransactionStatus.ACSC,
        )
        rec2 = self.engine.process_pacs002_status_report(pacs002)
        self.assertEqual(rec2.dvp_status, DvpStatus.SETTLED)
        self.assertEqual(self.custody_buyer.securities["BRCDB0012345"], Decimal("20"))
        self.assertEqual(self.pi_itau.balance, Decimal("9980000.00"))
        self.assertEqual(self.pi_btg.balance, Decimal("5020000.00"))

    # -----------------------------------------------------------------------
    # 7. Rejeição e Rollback Seguro (Zero Perda de Custódia / Saldo)
    # -----------------------------------------------------------------------

    def test_rejection_and_atomic_rollback(self) -> None:
        """Garante reversão atômica de custódia e fundos retidos após rejeição RJCT."""
        e2e = generate_end_to_end_id("60701190")
        qty = Decimal("100.00")
        pu = Decimal("150.00")
        total = qty * pu

        pacs008 = Pacs008Message(
            msg_id="MSG-SPI-RJCT-01",
            creation_date_time=datetime.now(timezone.utc),
            end_to_end_id=e2e,
            tx_id="TX-RJCT-01",
            amount=total,
            settlement_date=date.today(),
            debtor_name="Investidor Itaú",
            debtor_tax_id="52998224725",
            debtor_ispb="60701190",
            debtor_account="CC-100",
            creditor_name="Fundo BTG",
            creditor_tax_id="30306294000145",
            creditor_ispb="30306294",
            creditor_account="CC-200",
            asset_type=AssetType.INVESTMENT_FUND_SHARE,
            asset_identifier="HGLG11",
            asset_quantity=qty,
            asset_unit_price=pu,
        )

        self.engine.submit_credit_transfer(pacs008, "CUST-SELLER-01", "CUST-BUYER-01")
        self.assertEqual(self.custody_seller.fund_shares["HGLG11"], Decimal("900.00"))
        self.assertEqual(self.pi_itau.in_transit_debits, total)

        pacs002 = Pacs002Message(
            msg_id="STS-RJCT-01",
            creation_date_time=datetime.now(timezone.utc),
            original_end_to_end_id=e2e,
            original_tx_id="TX-RJCT-01",
            transaction_status=SpiTransactionStatus.RJCT,
            status_reason_code="AB03",
            status_reason_detail="Liquidação expirada no SPI",
        )
        rec = self.engine.process_pacs002_status_report(pacs002)
        self.assertEqual(rec.dvp_status, DvpStatus.ROLLED_BACK)

        # Verifica restauração integral da custódia e fundos
        self.assertEqual(self.custody_seller.fund_shares["HGLG11"], Decimal("1000.00"))
        self.assertEqual(self.custody_buyer.fund_shares["HGLG11"], Decimal("0.00"))
        self.assertEqual(self.pi_itau.in_transit_debits, Decimal("0.00"))
        self.assertEqual(self.pi_itau.balance, Decimal("10000000.00"))
        self.assertEqual(self.pi_btg.balance, Decimal("5000000.00"))

    # -----------------------------------------------------------------------
    # 8. Idempotência Absoluta no SPI
    # -----------------------------------------------------------------------

    def test_spi_messaging_idempotency(self) -> None:
        """Reenvio de mensagem com mesmo EndToEndId retorna recibo original sem duplicar saldos."""
        e2e = generate_end_to_end_id("60701190")
        pacs008 = Pacs008Message(
            msg_id="MSG-SPI-IDEMP-01",
            creation_date_time=datetime.now(timezone.utc),
            end_to_end_id=e2e,
            tx_id="TX-IDEMP-01",
            amount=Decimal("1500.00"),
            settlement_date=date.today(),
            debtor_name="Investidor Itaú",
            debtor_tax_id="52998224725",
            debtor_ispb="60701190",
            debtor_account="CC-100",
            creditor_name="Fundo BTG",
            creditor_tax_id="30306294000145",
            creditor_ispb="30306294",
            creditor_account="CC-200",
            asset_type=AssetType.INVESTMENT_FUND_SHARE,
            asset_identifier="HGLG11",
            asset_quantity=Decimal("10"),
            asset_unit_price=Decimal("150.00"),
        )

        rec1 = self.engine.submit_credit_transfer(pacs008, "CUST-SELLER-01", "CUST-BUYER-01")
        self.assertFalse(rec1.idempotent_replay)
        self.assertEqual(self.custody_seller.fund_shares["HGLG11"], Decimal("990.00"))

        # Reenvio idêntico
        rec2 = self.engine.submit_credit_transfer(pacs008, "CUST-SELLER-01", "CUST-BUYER-01")
        self.assertTrue(rec2.idempotent_replay)
        self.assertEqual(rec1.receipt_id, rec2.receipt_id)

        # Saldos e custódia não foram duplicados!
        self.assertEqual(self.custody_seller.fund_shares["HGLG11"], Decimal("990.00"))
        self.assertEqual(self.pi_itau.in_transit_debits, Decimal("1500.00"))

    # -----------------------------------------------------------------------
    # 9. Conciliação de Canal STR e Prova de Conservação Financeira
    # -----------------------------------------------------------------------

    def test_str_channel_reconciliation_and_conservation(self) -> None:
        """Executa conciliação determinística do canal STR x SPI."""
        # Aporte inicial STR0007 no Itaú
        self.engine.record_str_transfer(
            StrTransferRecord(
                nu_op="STR-TEST-01",
                transfer_type=StrTransferType.STR0007_INJECTION,
                ispb="60701190",
                amount=Decimal("2000000.00"),
                timestamp=datetime.now(timezone.utc),
            )
        )
        # Saldo Itaú agora é 12M

        # Executa liquidação SPI de 500k do Itaú para o BTG
        e2e = generate_end_to_end_id("60701190")
        pacs008 = Pacs008Message(
            msg_id="MSG-STR-CONC-01",
            creation_date_time=datetime.now(timezone.utc),
            end_to_end_id=e2e,
            tx_id="TX-STR-CONC",
            amount=Decimal("500000.00"),
            settlement_date=date.today(),
            debtor_name="Investidor Itaú",
            debtor_tax_id="52998224725",
            debtor_ispb="60701190",
            debtor_account="CC-100",
            creditor_name="Fundo BTG",
            creditor_tax_id="30306294000145",
            creditor_ispb="30306294",
            creditor_account="CC-200",
            asset_type=AssetType.INVESTMENT_FUND_SHARE,
            asset_identifier="HGLG11",
            asset_quantity=Decimal("500"),
            asset_unit_price=Decimal("1000.00"),
        )
        self.engine.submit_credit_transfer(pacs008, "CUST-SELLER-01", "CUST-BUYER-01")
        self.engine.process_pacs002_status_report(
            Pacs002Message(
                msg_id="STS-STR-01",
                creation_date_time=datetime.now(timezone.utc),
                original_end_to_end_id=e2e,
                original_tx_id="TX-STR-CONC",
                transaction_status=SpiTransactionStatus.ACSC,
            )
        )

        # Devolução final STR0008
        self.engine.record_str_transfer(
            StrTransferRecord(
                nu_op="STR-TEST-02",
                transfer_type=StrTransferType.STR0008_RETURN,
                ispb="60701190",
                amount=Decimal("1000000.00"),
                timestamp=datetime.now(timezone.utc),
            )
        )

        # Concilia canal STR para o Itaú
        summary = self.engine.reconcile_str_channel("60701190")
        self.assertTrue(summary.is_balanced)
        self.assertEqual(summary.discrepancy, Decimal("0.00"))
        # 10M inicial + 2M STR - 1M STR - 500k SPI = 10.5M
        self.assertEqual(summary.actual_final_balance, Decimal("10500000.00"))
        self.assertEqual(summary.calculated_final_balance, Decimal("10500000.00"))

    # -----------------------------------------------------------------------
    # 10. Carga e Verificação Formal da Fixture Canônica
    # -----------------------------------------------------------------------

    def test_load_and_run_canonical_fixture(self) -> None:
        """Carrega a fixture sintética e executa os cenários prescritos."""
        engine = PixSpiSettlementEngine.load_from_fixture(self.fixture_path)
        self.assertEqual(len(engine.pi_accounts), 5)
        self.assertEqual(len(engine.custody_accounts), 4)

        with open(self.fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Executa cenário de subscrição D0 de cota
        scn_fnd = data["sample_scenarios"]["fund_quota_subscription_d0"]["pacs008"]
        msg_fnd = Pacs008Message(
            msg_id=scn_fnd["msg_id"],
            creation_date_time=datetime.now(timezone.utc),
            end_to_end_id=scn_fnd["end_to_end_id"],
            tx_id=scn_fnd["tx_id"],
            amount=Decimal(scn_fnd["amount"]),
            settlement_date=date.fromisoformat(scn_fnd["settlement_date"]),
            debtor_name="Investidor Itaú",
            debtor_tax_id="52998224725",
            debtor_ispb=scn_fnd["debtor_ispb"],
            debtor_account="CC-ITAU",
            creditor_name="FII CSHG",
            creditor_tax_id="30306294000145",
            creditor_ispb=scn_fnd["creditor_ispb"],
            creditor_account="CC-BTG",
            asset_type=AssetType(scn_fnd["asset_type"]),
            asset_identifier=scn_fnd["asset_identifier"],
            asset_quantity=Decimal(scn_fnd["asset_quantity"]),
            asset_unit_price=Decimal(scn_fnd["asset_unit_price"]),
        )
        rec_fnd = engine.submit_credit_transfer(
            msg_fnd,
            scn_fnd["seller_custody_account_id"],
            scn_fnd["buyer_custody_account_id"],
        )
        self.assertEqual(rec_fnd.dvp_status, DvpStatus.RESERVED)

        # Confirma com pacs.002
        sts_fnd = engine.process_pacs002_status_report(
            Pacs002Message(
                msg_id="STS-FND-SCN",
                creation_date_time=datetime.now(timezone.utc),
                original_end_to_end_id=scn_fnd["end_to_end_id"],
                original_tx_id=scn_fnd["tx_id"],
                transaction_status=SpiTransactionStatus.ACSC,
            )
        )
        self.assertEqual(sts_fnd.dvp_status, DvpStatus.SETTLED)

        # Comprova conservação global financeira e de custódia
        self.assertTrue(engine.verify_financial_conservation())
        self.assertTrue(engine.verify_custody_conservation("HGLG11"))


if __name__ == "__main__":
    unittest.main()
