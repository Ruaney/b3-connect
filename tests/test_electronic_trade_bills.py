"""Suíte de Testes Automatizados para Registro, Endosso e Liquidação de Duplicatas (FL-051).

Testa:
- Validação formal de chave de acesso de NF-e (44 dígitos, cUF, cDV via Módulo 11, CNPJ emitente).
- Validação matemática de CNPJ (pesos e dígitos verificadores).
- Escrituração no Balcão NoMe B3 de duplicatas mercantis vinculadas a NF-e.
- Mecanismo anti-fraude de unicidade de lastro (bloqueio por chave NF-e + parcela).
- Bloqueio de duplicidade em interoperabilidade com registradoras externas.
- Endosso Translativo com transferência integral de titularidade para FIDC.
- Endosso Mandato para cobrança bancária simples sem alteração de titularidade.
- Integridade e legitimidade da cadeia sequencial de endossos cambiais.
- Rejeição de endossos por terceiros desautorizados ou carimbos retroativos.
- Liquidação financeira via PIX (com E2E ID) e SPB/STR (com conciliação contábil).
- Conservação financeira contábil: Principal + Juros + Multa - Descontos == Liquidado.
- Bloqueio de dupla liquidação e rejeição de pagadores/recebedores desautorizados.
- Baixa operacional (desconstituição) de duplicata.
- Carga completa e validação de invariantes a partir da fixture oficial JSON.
- Serialização to_dict e desserialização from_dict de todos os modelos de dados.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

# Garante que a raiz do repositório esteja no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.electronic_trade_bills import (
    DuplicateTradeBillError,
    ElectronicTradeBill,
    ElectronicTradeBillEngine,
    EndorsementRecord,
    EndorsementType,
    InvalidEndorsementError,
    NfeValidationError,
    SettlementMethod,
    SettlementRecord,
    TradeBillError,
    TradeBillInvalidStatusError,
    TradeBillNotFoundError,
    TradeBillSettlementError,
    TradeBillStatus,
    calculate_nfe_check_digit,
    clean_document,
    validate_cnpj,
    validate_nfe_access_key,
)

FIXTURES_DIR = ROOT_DIR / "fixtures"
TRADE_BILLS_FIXTURE_PATH = FIXTURES_DIR / "electronic_trade_bills.json"


class TestElectronicTradeBills(unittest.TestCase):
    """Bateria de testes unitários do motor NoMe de duplicatas escriturais eletrônicas."""

    def setUp(self) -> None:
        self.engine = ElectronicTradeBillEngine(registrar_code="B3_NOME")
        # CNPJs válidos de teste
        self.issuer_cnpj = "12345678000195"  # INDÚSTRIA DE ALIMENTOS PAULISTANA LTDA
        self.drawee_cnpj = "98765432000198"  # REDE DE SUPERMERCADOS BRASIL S.A.
        self.fidc_cnpj = "11223344000186"    # FIDC ALPHA RECEBÍVEIS
        self.bank_cnpj = "60701190000104"    # BANCO COBRADOR S.A.
        
        # Chave NF-e válida 44 dígitos gerada com cDV correto (Módulo 11)
        # cUF=35 (SP), AAMM=2611, CNPJ=12345678000195, Mod=55, Serie=001, nNF=000123456, tpEmis=1, cNF=12345678 -> cDV=9
        self.valid_nfe_key = "35261112345678000195550010001234561123456789"

    # -----------------------------------------------------------------------
    # 1. Validações da Chave NF-e e CNPJ
    # -----------------------------------------------------------------------

    def test_nfe_access_key_valid(self) -> None:
        """Verifica aceitação de chave de 44 dígitos com cDV correto e emitente correspondente."""
        self.assertTrue(validate_nfe_access_key(self.valid_nfe_key, expected_issuer_cnpj=self.issuer_cnpj))

    def test_nfe_access_key_invalid_length(self) -> None:
        """Chave com tamanho diferente de 44 dígitos deve levantar NfeValidationError."""
        with self.assertRaises(NfeValidationError):
            validate_nfe_access_key("3526111234567800019555001000123456112345678")  # 43 dígitos
        with self.assertRaises(NfeValidationError):
            validate_nfe_access_key(self.valid_nfe_key + "0")  # 45 dígitos

    def test_nfe_access_key_invalid_uf(self) -> None:
        """Chave com cUF IBGE inexistente deve ser rejeitada."""
        invalid_uf_key = "99" + self.valid_nfe_key[2:]
        with self.assertRaises(NfeValidationError):
            validate_nfe_access_key(invalid_uf_key)

    def test_nfe_access_key_invalid_month(self) -> None:
        """Chave com mês de emissão inválido (ex: 13) deve ser rejeitada."""
        invalid_month_key = self.valid_nfe_key[:4] + "13" + self.valid_nfe_key[6:]
        with self.assertRaises(NfeValidationError):
            validate_nfe_access_key(invalid_month_key)

    def test_nfe_access_key_check_digit_mismatch(self) -> None:
        """Chave com cDV alterado deve falhar no Módulo 11."""
        # Se cDV original é 9, substitui por 8
        bad_dv_key = self.valid_nfe_key[:43] + ("8" if self.valid_nfe_key[43] != "8" else "7")
        with self.assertRaises(NfeValidationError):
            validate_nfe_access_key(bad_dv_key)

    def test_nfe_access_key_issuer_mismatch(self) -> None:
        """Chave cujo CNPJ codificado não bate com o emitente declarado deve ser rejeitada."""
        other_cnpj = "55667788000186"
        with self.assertRaises(NfeValidationError):
            validate_nfe_access_key(self.valid_nfe_key, expected_issuer_cnpj=other_cnpj)

    def test_cnpj_validator(self) -> None:
        """Valida a precisão da checagem matemática de CNPJs."""
        self.assertTrue(validate_cnpj("12.345.678/0001-95"))
        self.assertTrue(validate_cnpj("98765432000198"))
        self.assertFalse(validate_cnpj("11111111111111"))
        self.assertFalse(validate_cnpj("12345678000190"))  # DV errado

    # -----------------------------------------------------------------------
    # 2. Registro de Duplicatas e Unicidade de Lastro (Anti-Fraude)
    # -----------------------------------------------------------------------

    def test_register_trade_bill_success(self) -> None:
        """Registra duplicata mercantil válida e verifica estado inicial."""
        bill = self.engine.register_trade_bill(
            trade_bill_id="TBILL-TEST-001",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=3,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="50000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
        )
        self.assertEqual(bill.trade_bill_id, "TBILL-TEST-001")
        self.assertEqual(bill.status, TradeBillStatus.REGISTRADA)
        self.assertEqual(bill.current_owner_document, self.issuer_cnpj)
        self.assertEqual(bill.balance_due, Decimal("50000.00"))
        self.assertIsNone(bill.mandatary_document)
        self.assertEqual(len(bill.endorsements), 0)
        self.assertTrue(bill.is_active)
        self.assertFalse(bill.is_settled)

    def test_ballast_uniqueness_blocks_duplicate_installment(self) -> None:
        """Mecanismo anti-fraude: Rejeita tentativa de cadastrar a mesma parcela de NF-e."""
        self.engine.register_trade_bill(
            trade_bill_id="TBILL-001",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=2,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="10000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
        )
        # Tentativa de escrituração da MESMA parcela da mesma NF-e com outro ID
        with self.assertRaises(DuplicateTradeBillError):
            self.engine.register_trade_bill(
                trade_bill_id="TBILL-DUPLICATE-ATTEMPT",
                nfe_access_key=self.valid_nfe_key,
                installment_number=1,
                total_installments=2,
                issuer_document=self.issuer_cnpj,
                issuer_name="Indústria Paulistana",
                drawee_document=self.drawee_cnpj,
                drawee_name="Supermercados Brasil",
                face_value="10000.00",
                issue_date="2026-11-01",
                due_date="2026-12-01",
            )

    def test_ballast_uniqueness_allows_different_installments(self) -> None:
        """Parcelas distintas da mesma NF-e devem ser aceitas normalmente."""
        b1 = self.engine.register_trade_bill(
            trade_bill_id="TBILL-001-P1",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=2,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="15000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
        )
        b2 = self.engine.register_trade_bill(
            trade_bill_id="TBILL-001-P2",
            nfe_access_key=self.valid_nfe_key,
            installment_number=2,
            total_installments=2,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="15000.00",
            issue_date="2026-11-01",
            due_date="2027-01-01",
        )
        self.assertEqual(b1.installment_number, 1)
        self.assertEqual(b2.installment_number, 2)
        self.assertTrue(self.engine.verify_ballast_uniqueness())

    def test_interoperability_blocks_external_ballast(self) -> None:
        """Trava anti-fraude: Rejeita duplicata cujo lastro já está em registradora externa."""
        # Registra lastro na CERC externa
        self.engine.register_external_ballast(
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            registrar_name="REGISTRADORA_CERC_EXTERNA",
        )
        with self.assertRaises(DuplicateTradeBillError):
            self.engine.register_trade_bill(
                trade_bill_id="TBILL-INT-001",
                nfe_access_key=self.valid_nfe_key,
                installment_number=1,
                total_installments=1,
                issuer_document=self.issuer_cnpj,
                issuer_name="Indústria Paulistana",
                drawee_document=self.drawee_cnpj,
                drawee_name="Supermercados Brasil",
                face_value="25000.00",
                issue_date="2026-11-01",
                due_date="2026-12-01",
            )

    def test_invalid_parameters_rejection(self) -> None:
        """Verifica rejeição preventiva para valores de face e datas incoerentes."""
        # Valor de face negativo ou zero
        with self.assertRaises(TradeBillError):
            self.engine.register_trade_bill(
                trade_bill_id="TBILL-BAD-VAL",
                nfe_access_key=self.valid_nfe_key,
                installment_number=1,
                total_installments=1,
                issuer_document=self.issuer_cnpj,
                issuer_name="Paulistana",
                drawee_document=self.drawee_cnpj,
                drawee_name="Supermercados",
                face_value="-100.00",
                issue_date="2026-11-01",
                due_date="2026-12-01",
            )
        # Vencimento anterior à emissão
        with self.assertRaises(TradeBillError):
            self.engine.register_trade_bill(
                trade_bill_id="TBILL-BAD-DATES",
                nfe_access_key=self.valid_nfe_key,
                installment_number=1,
                total_installments=1,
                issuer_document=self.issuer_cnpj,
                issuer_name="Paulistana",
                drawee_document=self.drawee_cnpj,
                drawee_name="Supermercados",
                face_value="5000.00",
                issue_date="2026-11-10",
                due_date="2026-11-01",
            )

    # -----------------------------------------------------------------------
    # 3. Endosso Eletrônico (Translativo vs Mandato) e Cadeia de Titularidade
    # -----------------------------------------------------------------------

    def test_translativo_endorsement_to_fidc(self) -> None:
        """Endosso translativo transfere a propriedade e atualiza o titular para o FIDC."""
        bill = self.engine.register_trade_bill(
            trade_bill_id="TBILL-END-001",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=1,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="30000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
            registration_timestamp="2026-11-01T10:00:00Z",
        )
        record = self.engine.endorse_trade_bill(
            trade_bill_id="TBILL-END-001",
            endorsement_type=EndorsementType.TRANSLATIVO,
            endorser_document=self.issuer_cnpj,
            endorsee_document=self.fidc_cnpj,
            endorsee_name="FIDC Alpha Recebíveis",
            timestamp="2026-11-02T14:00:00Z",
            notes="Cessão fiduciária de recebível comercial",
        )
        self.assertEqual(bill.status, TradeBillStatus.ENDOSSADA)
        self.assertEqual(bill.current_owner_document, self.fidc_cnpj)
        self.assertEqual(bill.current_owner_name, "FIDC Alpha Recebíveis")
        self.assertIsNotNone(record.digital_signature_hash)
        self.assertTrue(self.engine.verify_endorsement_chain("TBILL-END-001"))

    def test_mandato_endorsement_retains_ownership(self) -> None:
        """Endosso mandato institui banco cobrador sem alterar a titularidade creditícia."""
        bill = self.engine.register_trade_bill(
            trade_bill_id="TBILL-MAN-001",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=1,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="45000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
            registration_timestamp="2026-11-01T10:00:00Z",
        )
        self.engine.endorse_trade_bill(
            trade_bill_id="TBILL-MAN-001",
            endorsement_type=EndorsementType.MANDATO,
            endorser_document=self.issuer_cnpj,
            endorsee_document=self.bank_cnpj,
            endorsee_name="Banco Cobrador S.A.",
            timestamp="2026-11-02T10:00:00Z",
            notes="Constituição de mandato para cobrança simples via boleto",
        )
        self.assertEqual(bill.status, TradeBillStatus.EM_COBRANCA)
        # Titular continua sendo o emitente!
        self.assertEqual(bill.current_owner_document, self.issuer_cnpj)
        # Mandatário registrado
        self.assertEqual(bill.mandatary_document, self.bank_cnpj)
        self.assertEqual(bill.mandatary_name, "Banco Cobrador S.A.")
        self.assertTrue(self.engine.verify_endorsement_chain("TBILL-MAN-001"))

    def test_multi_tier_endorsement_chain(self) -> None:
        """Cadeia complexa: Emitente -> FIDC (Translativo) -> Banco (Mandato)."""
        bill = self.engine.register_trade_bill(
            trade_bill_id="TBILL-CHAIN-001",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=1,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="60000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
            registration_timestamp="2026-11-01T10:00:00Z",
        )
        # 1. Emitente cede para FIDC (Translativo)
        self.engine.endorse_trade_bill(
            trade_bill_id="TBILL-CHAIN-001",
            endorsement_type=EndorsementType.TRANSLATIVO,
            endorser_document=self.issuer_cnpj,
            endorsee_document=self.fidc_cnpj,
            endorsee_name="FIDC Alpha",
            timestamp="2026-11-02T10:00:00Z",
        )
        # 2. FIDC outorga mandato para o Banco (Mandato)
        self.engine.endorse_trade_bill(
            trade_bill_id="TBILL-CHAIN-001",
            endorsement_type=EndorsementType.MANDATO,
            endorser_document=self.fidc_cnpj,
            endorsee_document=self.bank_cnpj,
            endorsee_name="Banco Cobrador",
            timestamp="2026-11-03T11:00:00Z",
        )
        self.assertEqual(bill.status, TradeBillStatus.EM_COBRANCA)
        self.assertEqual(bill.current_owner_document, self.fidc_cnpj)
        self.assertEqual(bill.mandatary_document, self.bank_cnpj)
        self.assertTrue(self.engine.verify_endorsement_chain("TBILL-CHAIN-001"))

    def test_unauthorized_endorser_rejected(self) -> None:
        """Terceiro desautorizado tentando endossar deve ser rejeitado."""
        self.engine.register_trade_bill(
            trade_bill_id="TBILL-UNAUTH-001",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=1,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="20000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
        )
        # O FIDC ainda não é titular, logo não pode endossar
        with self.assertRaises(InvalidEndorsementError):
            self.engine.endorse_trade_bill(
                trade_bill_id="TBILL-UNAUTH-001",
                endorsement_type=EndorsementType.TRANSLATIVO,
                endorser_document=self.fidc_cnpj,
                endorsee_document=self.bank_cnpj,
                endorsee_name="Banco Cobrador",
            )

    def test_retroactive_endorsement_timestamp_rejected(self) -> None:
        """Endosso com timestamp anterior à emissão deve ser rejeitado."""
        self.engine.register_trade_bill(
            trade_bill_id="TBILL-TIME-001",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=1,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="20000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
            registration_timestamp="2026-11-01T10:00:00Z",
        )
        with self.assertRaises(InvalidEndorsementError):
            self.engine.endorse_trade_bill(
                trade_bill_id="TBILL-TIME-001",
                endorsement_type=EndorsementType.TRANSLATIVO,
                endorser_document=self.issuer_cnpj,
                endorsee_document=self.fidc_cnpj,
                endorsee_name="FIDC Alpha",
                timestamp="2026-10-31T23:59:59Z",  # Anterior ao registro!
            )

    # -----------------------------------------------------------------------
    # 4. Liquidação Financeira via PIX e SPB com Conservação Contábil
    # -----------------------------------------------------------------------

    def test_settlement_via_pix_with_discount(self) -> None:
        """Liquidação via Pix com desconto comercial e baixa registral automática."""
        self.engine.register_trade_bill(
            trade_bill_id="TBILL-PIX-001",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=1,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="50000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
        )
        # Endossa para FIDC
        self.engine.endorse_trade_bill(
            trade_bill_id="TBILL-PIX-001",
            endorsement_type=EndorsementType.TRANSLATIVO,
            endorser_document=self.issuer_cnpj,
            endorsee_document=self.fidc_cnpj,
            endorsee_name="FIDC Alpha",
        )
        # Sacado paga FIDC via Pix com R$ 500,00 de desconto
        settl = self.engine.settle_trade_bill(
            trade_bill_id="TBILL-PIX-001",
            method=SettlementMethod.PIX,
            payer_document=self.drawee_cnpj,
            receiver_document=self.fidc_cnpj,
            principal_amount="50000.00",
            discount_amount="500.00",
            settled_amount="49500.00",
            channel_transaction_id="E12345678202611281415A9876543210",
        )
        bill = self.engine.get_bill("TBILL-PIX-001")
        self.assertEqual(bill.status, TradeBillStatus.LIQUIDADA)
        self.assertEqual(bill.balance_due, Decimal("0.00"))
        self.assertTrue(bill.is_settled)
        self.assertEqual(settl.settled_amount, Decimal("49500.00"))

    def test_settlement_via_spb_with_interest(self) -> None:
        """Liquidação via SPB/STR com juros de mora e conservação contábil."""
        # Cria outra chave NF-e válida
        bill = self.engine.register_trade_bill(
            trade_bill_id="TBILL-SPB-001",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=1,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="100000.00",
            issue_date="2026-11-01",
            due_date="2026-11-20",
        )
        settl = self.engine.settle_trade_bill(
            trade_bill_id="TBILL-SPB-001",
            method=SettlementMethod.SPB,
            payer_document=self.drawee_cnpj,
            receiver_document=self.issuer_cnpj,
            principal_amount="100000.00",
            interest_amount="1500.00",
            fine_amount="500.00",
            discount_amount="0.00",
            settled_amount="102000.00",
            channel_transaction_id="STR202611200000491823",
        )
        self.assertEqual(bill.status, TradeBillStatus.LIQUIDADA)
        self.assertEqual(bill.balance_due, Decimal("0.00"))
        self.assertEqual(settl.settled_amount, Decimal("102000.00"))

    def test_settlement_payer_mismatch_rejected(self) -> None:
        """Pagador diferente do sacado deve ser rejeitado."""
        self.engine.register_trade_bill(
            trade_bill_id="TBILL-BAD-PAYER",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=1,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="20000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
        )
        other_cnpj = "55667788000186"
        with self.assertRaises(TradeBillSettlementError):
            self.engine.settle_trade_bill(
                trade_bill_id="TBILL-BAD-PAYER",
                method=SettlementMethod.PIX,
                payer_document=other_cnpj,
                receiver_document=self.issuer_cnpj,
                principal_amount="20000.00",
            )

    def test_double_settlement_blocked(self) -> None:
        """Tentativa de liquidar duas vezes a mesma duplicata é bloqueada."""
        self.engine.register_trade_bill(
            trade_bill_id="TBILL-DOUBLE-001",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=1,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="10000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
        )
        self.engine.settle_trade_bill(
            trade_bill_id="TBILL-DOUBLE-001",
            method=SettlementMethod.PIX,
            payer_document=self.drawee_cnpj,
            receiver_document=self.issuer_cnpj,
            principal_amount="10000.00",
        )
        with self.assertRaises(TradeBillSettlementError):
            self.engine.settle_trade_bill(
                trade_bill_id="TBILL-DOUBLE-001",
                method=SettlementMethod.PIX,
                payer_document=self.drawee_cnpj,
                receiver_document=self.issuer_cnpj,
                principal_amount="10000.00",
            )

    def test_accounting_break_in_settlement_rejected(self) -> None:
        """Quebra na equação Principal + Juros + Multa - Desconto == Liquidado deve ser rejeitada."""
        self.engine.register_trade_bill(
            trade_bill_id="TBILL-MATH-ERR",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=1,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="10000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
        )
        with self.assertRaises(TradeBillSettlementError):
            self.engine.settle_trade_bill(
                trade_bill_id="TBILL-MATH-ERR",
                method=SettlementMethod.PIX,
                payer_document=self.drawee_cnpj,
                receiver_document=self.issuer_cnpj,
                principal_amount="10000.00",
                interest_amount="100.00",
                settled_amount="9999.00",  # Divergente de 10100.00
            )

    def test_endorsement_on_settled_bill_rejected(self) -> None:
        """Duplicata já quitada não admite endosso posterior."""
        self.engine.register_trade_bill(
            trade_bill_id="TBILL-SETTLED-END",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=1,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="10000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
        )
        self.engine.settle_trade_bill(
            trade_bill_id="TBILL-SETTLED-END",
            method=SettlementMethod.PIX,
            payer_document=self.drawee_cnpj,
            receiver_document=self.issuer_cnpj,
            principal_amount="10000.00",
        )
        with self.assertRaises(TradeBillInvalidStatusError):
            self.engine.endorse_trade_bill(
                trade_bill_id="TBILL-SETTLED-END",
                endorsement_type=EndorsementType.TRANSLATIVO,
                endorser_document=self.issuer_cnpj,
                endorsee_document=self.fidc_cnpj,
                endorsee_name="FIDC Alpha",
            )

    def test_deregister_trade_bill(self) -> None:
        """Baixa operacional manual de duplicata ativa."""
        bill = self.engine.register_trade_bill(
            trade_bill_id="TBILL-BAIXA-001",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=1,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="15000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
        )
        self.engine.deregister_trade_bill("TBILL-BAIXA-001", reason="DESACORDO_COMERCIAL")
        self.assertEqual(bill.status, TradeBillStatus.BAIXADA)
        self.assertFalse(bill.is_active)
        # Não permite liquidação de título cancelado
        with self.assertRaises(TradeBillInvalidStatusError):
            self.engine.settle_trade_bill(
                trade_bill_id="TBILL-BAIXA-001",
                method=SettlementMethod.PIX,
                payer_document=self.drawee_cnpj,
                receiver_document=self.issuer_cnpj,
                principal_amount="15000.00",
            )

    # -----------------------------------------------------------------------
    # 5. Carga de Fixture Oficial e Auditoria Global de Invariantes
    # -----------------------------------------------------------------------

    def test_fixture_loading_and_audit(self) -> None:
        """Carrega a fixture oficial JSON e verifica todas as leis e invariantes."""
        self.assertTrue(TRADE_BILLS_FIXTURE_PATH.exists(), f"Fixture não encontrada: {TRADE_BILLS_FIXTURE_PATH}")
        summary = self.engine.load_from_fixture(TRADE_BILLS_FIXTURE_PATH)
        self.assertEqual(summary["loaded_count"], 5)
        self.assertEqual(summary["blocked_attempts_count"], 2)

        # Checa unicidade global de lastro
        self.assertTrue(self.engine.verify_ballast_uniqueness())

        # Checa conservação contábil
        audit = self.engine.verify_accounting_conservation()
        self.assertTrue(audit["is_conserved"])
        self.assertTrue(audit["identity_conserved"])
        self.assertTrue(audit["settlements_arithmetic_valid"])

        # Tenta re-escriturar uma das parcelas da fixture -> deve falhar
        with self.assertRaises(DuplicateTradeBillError):
            self.engine.register_trade_bill(
                trade_bill_id="TBILL-FRAUD-DUPLICATE",
                nfe_access_key="35261112345678000195550010001234561123456789",
                installment_number=1,
                total_installments=3,
                issuer_document="12345678000195",
                issuer_name="Tentativa Fraude",
                drawee_document="98765432000198",
                drawee_name="Supermercados Brasil",
                face_value="50000.00",
                issue_date="2026-11-01",
                due_date="2026-12-01",
            )

        # Tenta re-escriturar tentativa bloqueada externa (CERC)
        with self.assertRaises(DuplicateTradeBillError):
            self.engine.register_trade_bill(
                trade_bill_id="TBILL-FRAUD-EXTERNA",
                nfe_access_key="35261112345678000195550010001234561123456789",
                installment_number=2,
                total_installments=3,
                issuer_document="12345678000195",
                issuer_name="Tentativa Fraude 2",
                drawee_document="98765432000198",
                drawee_name="Supermercados Brasil",
                face_value="50000.00",
                issue_date="2026-11-01",
                due_date="2027-01-01",
            )

    def test_export_state(self) -> None:
        """Verifica a exportação de estado auditável do motor."""
        self.engine.register_trade_bill(
            trade_bill_id="TBILL-EXP-001",
            nfe_access_key=self.valid_nfe_key,
            installment_number=1,
            total_installments=1,
            issuer_document=self.issuer_cnpj,
            issuer_name="Indústria Paulistana",
            drawee_document=self.drawee_cnpj,
            drawee_name="Supermercados Brasil",
            face_value="12000.00",
            issue_date="2026-11-01",
            due_date="2026-12-01",
        )
        state = self.engine.export_state()
        self.assertEqual(state["registrar_code"], "B3_NOME")
        self.assertEqual(state["total_bills"], 1)
        self.assertTrue(state["audit"]["is_conserved"])


if __name__ == "__main__":
    unittest.main()
