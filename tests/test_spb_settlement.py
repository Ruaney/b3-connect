"""Testes unitários e de integração para o módulo SPB e Liquidação Multilateral (FL-027)."""

from datetime import date
from decimal import Decimal
import io
from pathlib import Path
import sys
import unittest

# Garante inclusão da raiz do projeto no sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.spb_settlement import (
    ISPB_B3_DEFAULT,
    InvalidIspbError,
    LdlSettlementRecord,
    SettlementReconciliationResult,
    SpbError,
    SpbMessageHeader,
    SpbSettlementEngine,
    SpbSettlementMessage,
    SpbXmlValidationError,
    StrTransferRecord,
    validate_ispb,
)
from src.imbarq004 import parse_imbarq004

FIXTURES_DIR = ROOT_DIR / "fixtures"


class TestSpbSettlement(unittest.TestCase):
    """Bateria de testes para mensageria SPB, parsing XML, geração e conciliação determinística."""

    def setUp(self) -> None:
        self.engine = SpbSettlementEngine(ispb_camara="00997185")
        self.canonical_fixture_path = FIXTURES_DIR / "spb_ldl0001_message.xml"
        self.imbarq004_valid_path = FIXTURES_DIR / "imbarq004-valid.txt"

    # -----------------------------------------------------------------------
    # 1. Validação de ISPB
    # -----------------------------------------------------------------------

    def test_validate_ispb_valid(self) -> None:
        """Valida que ISPBs de 8 dígitos numéricos válidos são aceitos."""
        self.assertEqual(validate_ispb("00997185"), "00997185")
        self.assertEqual(validate_ispb("12345678"), "12345678")
        self.assertEqual(validate_ispb("00000000"), "00000000")
        self.assertEqual(validate_ispb("99999999"), "99999999")
        # Aceita inteiro dentro do intervalo e formata com 8 dígitos
        self.assertEqual(validate_ispb(997185), "00997185")
        self.assertEqual(validate_ispb(0), "00000000")
        self.assertEqual(validate_ispb(12345678), "12345678")

    def test_validate_ispb_invalid(self) -> None:
        """Rejeita rigorosamente ISPBs fora da especificação de 8 dígitos numéricos."""
        invalid_cases = [
            "1234567",       # 7 dígitos
            "123456789",     # 9 dígitos
            "1234567a",      # caractere não numérico
            "abcdefgh",      # somente letras
            " 12345678 ",    # espaços em branco
            "12 34567",      # espaço interno
            "",              # string vazia
            None,            # nulo
            -1,              # inteiro negativo
            100000000,       # inteiro > 8 dígitos
            12345678.0,      # tipo float não suportado
        ]
        for val in invalid_cases:
            with self.subTest(val=val):
                with self.assertRaises(InvalidIspbError):
                    validate_ispb(val)

    # -----------------------------------------------------------------------
    # 2. Parsing de Mensagem Canônica LDL0001
    # -----------------------------------------------------------------------

    def test_parse_canonical_ldl0001_fixture(self) -> None:
        """Valida parsing e integridade da mensagem oficial spb_ldl0001_message.xml."""
        msg = self.engine.parse_ldl0001(self.canonical_fixture_path)

        # Header BCMSG
        self.assertEqual(msg.header.emissor_ispb, "00997185")
        self.assertEqual(msg.header.destinatario_ispb, "12345678")
        self.assertEqual(msg.header.dominio_sistema, "SPB01")
        self.assertEqual(msg.header.nu_op, "20261104000000001234")
        self.assertEqual(msg.header.identd_contg, "N")
        self.assertEqual(msg.header.identd_operad, "OPERATLAS01")
        self.assertEqual(msg.header.num_seq, 1)
        self.assertEqual(msg.header.indr_cont, "N")

        # Record LDL0001
        rec = msg.record
        self.assertIsInstance(rec, LdlSettlementRecord)
        self.assertEqual(rec.cod_msg, "LDL0001")
        self.assertEqual(rec.num_ctrl_ldl, "2026110400000001")
        self.assertEqual(rec.num_ctrl_str, "20261104STR000012345")
        self.assertEqual(rec.ispb_camara, "00997185")
        self.assertEqual(rec.ispb_participante, "12345678")
        self.assertEqual(rec.tipo_debito_credito, "C")
        self.assertEqual(rec.valor_liquidacao, Decimal("23500.00"))
        self.assertEqual(rec.data_liquidacao, date(2026, 11, 4))
        self.assertEqual(rec.tipo_conta, "CC")
        self.assertEqual(rec.numero_conta, "0000123456")
        self.assertEqual(rec.agencia, "0001")
        self.assertEqual(rec.codigo_camara, "B3")

    # -----------------------------------------------------------------------
    # 3. Geração e Roundtrip de XML LDL0001
    # -----------------------------------------------------------------------

    def test_generate_and_parse_roundtrip_ldl0001(self) -> None:
        """Valida que a geração de XML canônico LDL0001 preserva 100% da informação após parsing."""
        header = SpbMessageHeader(
            emissor_ispb="00997185",
            destinatario_ispb="87654321",
            dominio_sistema="SPB01",
            nu_op="20261104999900001111",
            identd_operad="ROBOT_LIQ",
        )
        record = LdlSettlementRecord(
            num_ctrl_ldl="CTRL-LDL-2026-001",
            num_ctrl_str="STR-BACEN-998877",
            ispb_camara="00997185",
            ispb_participante="87654321",
            tipo_debito_credito="D",
            valor_liquidacao=Decimal("158420.50"),
            data_liquidacao=date(2026, 11, 4),
            tipo_conta="RESERVAS",
            numero_conta="999888111",
            agencia="0001",
            codigo_camara="B3",
        )

        xml_generated = self.engine.generate_ldl0001(header, record)
        self.assertIn("<?xml version=", xml_generated)
        self.assertIn("<CodMsg>LDL0001</CodMsg>", xml_generated)
        self.assertIn("<TpDeb_Cred>D</TpDeb_Cred>", xml_generated)
        self.assertIn("<VlrLiqd>158420.50</VlrLiqd>", xml_generated)

        # Reparsing
        msg_roundtrip = self.engine.parse_ldl0001(xml_generated)
        self.assertEqual(msg_roundtrip.header.emissor_ispb, "00997185")
        self.assertEqual(msg_roundtrip.header.destinatario_ispb, "87654321")
        self.assertEqual(msg_roundtrip.record.tipo_debito_credito, "D")
        self.assertEqual(msg_roundtrip.record.valor_liquidacao, Decimal("158420.50"))
        self.assertEqual(msg_roundtrip.record.data_liquidacao, date(2026, 11, 4))
        self.assertEqual(msg_roundtrip.record.numero_conta, "999888111")

    # -----------------------------------------------------------------------
    # 4. Geração e Parsing de STR0010 (Aporte de Recursos)
    # -----------------------------------------------------------------------

    def test_str0010_generation_and_parsing(self) -> None:
        """Valida o ciclo completo da mensagem STR0010 para cobertura de débitos."""
        header = SpbMessageHeader(
            emissor_ispb="12345678",
            destinatario_ispb="00997185",
            dominio_sistema="SPB01",
            nu_op="20261104STR0010ABC",
        )
        record = StrTransferRecord(
            num_ctrl_if="IF-DEBT-COVER-01",
            num_ctrl_str="BACEN-STR-554433",
            ispb_if_debitada="12345678",
            ispb_if_creditada="00997185",
            valor_transferencia=Decimal("50000.00"),
            data_agendamento=date(2026, 11, 4),
            tipo_conta_debitada="RESERVAS",
            numero_conta_debitada="000112233",
            tipo_conta_creditada="LIQUIDACAO",
            numero_conta_creditada="000999888",
            finalidade="COBERTURA DE DEBITO MULTILATERAL",
        )

        xml_str = self.engine.generate_str0010(header, record)
        self.assertIn("<CodMsg>STR0010</CodMsg>", xml_str)
        self.assertIn("<VlrLanc>50000.00</VlrLanc>", xml_str)

        parsed = self.engine.parse_str0010(xml_str)
        str_rec = parsed.record
        self.assertIsInstance(str_rec, StrTransferRecord)
        self.assertEqual(str_rec.ispb_if_debitada, "12345678")
        self.assertEqual(str_rec.ispb_if_creditada, "00997185")
        self.assertEqual(str_rec.valor_transferencia, Decimal("50000.00"))
        self.assertEqual(str_rec.numero_conta_debitada, "000112233")
        self.assertEqual(str_rec.numero_conta_creditada, "000999888")

    # -----------------------------------------------------------------------
    # 5. Rejeição de Mensagens XML com Erro de Esquema ou Dados Inválidos
    # -----------------------------------------------------------------------

    def test_xml_syntax_error(self) -> None:
        """Rejeita XML malformado sintaticamente."""
        bad_xml = "<DOC><BCMSG>Nao fechou"
        with self.assertRaises(SpbXmlValidationError):
            self.engine.parse_ldl0001(bad_xml)

    def test_invalid_root_element(self) -> None:
        """Rejeita XML cujo elemento raiz não seja <DOC>."""
        bad_xml = "<INVALIDO><BCMSG></BCMSG></INVALIDO>"
        with self.assertRaises(SpbXmlValidationError):
            self.engine.parse_ldl0001(bad_xml)

    def test_missing_bcmsg_or_sismsg(self) -> None:
        """Rejeita XML com blocos obrigatórios ausentes."""
        no_bcmsg = """<DOC><SISMSG><LDL0001></LDL0001></SISMSG></DOC>"""
        with self.assertRaises(SpbXmlValidationError):
            self.engine.parse_ldl0001(no_bcmsg)

        no_sismsg = """<DOC><BCMSG><IdentdEmissor>00997185</IdentdEmissor><IdentdDestinatario>12345678</IdentdDestinatario><DomSist>SPB01</DomSist><NUOp>1</NUOp></BCMSG></DOC>"""
        with self.assertRaises(SpbXmlValidationError):
            self.engine.parse_ldl0001(no_sismsg)

    def test_negative_or_zero_amount_rejected(self) -> None:
        """Rejeita mensagens com valores de liquidação nulos ou negativos."""
        xml_zero = """<DOC>
          <BCMSG>
            <IdentdEmissor>00997185</IdentdEmissor>
            <IdentdDestinatario>12345678</IdentdDestinatario>
            <DomSist>SPB01</DomSist>
            <NUOp>1</NUOp>
          </BCMSG>
          <SISMSG>
            <LDL0001>
              <CodMsg>LDL0001</CodMsg>
              <NumCtrlLDL>1</NumCtrlLDL>
              <NumCtrlSTR>1</NumCtrlSTR>
              <ISPBCamara>00997185</ISPBCamara>
              <ISPBIF>12345678</ISPBIF>
              <TpDeb_Cred>C</TpDeb_Cred>
              <VlrLiqd>0.00</VlrLiqd>
              <DtLiqd>2026-11-04</DtLiqd>
              <NumCt>123</NumCt>
            </LDL0001>
          </SISMSG>
        </DOC>"""
        with self.assertRaises(SpbXmlValidationError):
            self.engine.parse_ldl0001(xml_zero)

    def test_invalid_date_format(self) -> None:
        """Rejeita formato de data incompatível com YYYY-MM-DD."""
        xml_bad_date = """<DOC>
          <BCMSG>
            <IdentdEmissor>00997185</IdentdEmissor>
            <IdentdDestinatario>12345678</IdentdDestinatario>
            <DomSist>SPB01</DomSist>
            <NUOp>1</NUOp>
          </BCMSG>
          <SISMSG>
            <LDL0001>
              <CodMsg>LDL0001</CodMsg>
              <NumCtrlLDL>1</NumCtrlLDL>
              <NumCtrlSTR>1</NumCtrlSTR>
              <ISPBCamara>00997185</ISPBCamara>
              <ISPBIF>12345678</ISPBIF>
              <TpDeb_Cred>C</TpDeb_Cred>
              <VlrLiqd>100.00</VlrLiqd>
              <DtLiqd>04/11/2026</DtLiqd>
              <NumCt>123</NumCt>
            </LDL0001>
          </SISMSG>
        </DOC>"""
        with self.assertRaises(SpbXmlValidationError):
            self.engine.parse_ldl0001(xml_bad_date)

    def test_invalid_direction_indicator(self) -> None:
        """Rejeita indicador de direção diferente de 'C' ou 'D'."""
        xml_bad_dir = """<DOC>
          <BCMSG>
            <IdentdEmissor>00997185</IdentdEmissor>
            <IdentdDestinatario>12345678</IdentdDestinatario>
            <DomSist>SPB01</DomSist>
            <NUOp>1</NUOp>
          </BCMSG>
          <SISMSG>
            <LDL0001>
              <CodMsg>LDL0001</CodMsg>
              <NumCtrlLDL>1</NumCtrlLDL>
              <NumCtrlSTR>1</NumCtrlSTR>
              <ISPBCamara>00997185</ISPBCamara>
              <ISPBIF>12345678</ISPBIF>
              <TpDeb_Cred>X</TpDeb_Cred>
              <VlrLiqd>100.00</VlrLiqd>
              <DtLiqd>2026-11-04</DtLiqd>
              <NumCt>123</NumCt>
            </LDL0001>
          </SISMSG>
        </DOC>"""
        with self.assertRaises(SpbXmlValidationError):
            self.engine.parse_ldl0001(xml_bad_dir)

    # -----------------------------------------------------------------------
    # 6. Conciliação Determinística entre IMBARQ004 e LDL0001
    # -----------------------------------------------------------------------

    def test_reconciliation_exact_trailer_match(self) -> None:
        """Valida reconciliação exata quando o valor da LDL0001 coincide com o totalizador do trailer do IMBARQ004."""
        # imbarq004-valid.txt possui saldo líquido do trailer = R$ 51.104,70 Credor ('C')
        imbarq = parse_imbarq004(self.imbarq004_valid_path)
        self.assertEqual(imbarq.trailer.total_saldo_liquido, Decimal("51104.70"))
        self.assertEqual(imbarq.trailer.sinal_saldo_liquido, "C")

        ldl_rec = LdlSettlementRecord(
            num_ctrl_ldl="LDL-TRAILER-MATCH-01",
            num_ctrl_str="STR-20261104-100",
            ispb_camara="00997185",
            ispb_participante="12345678",
            tipo_debito_credito="C",
            valor_liquidacao=Decimal("51104.70"),
            data_liquidacao=date(2026, 11, 4),
            numero_conta="0000123456",
        )

        res = self.engine.reconcile_with_imbarq004(ldl_rec, imbarq, scope="trailer")
        self.assertTrue(res.is_reconciled)
        self.assertEqual(res.status, "MATCHED")
        self.assertEqual(res.diff_valor, Decimal("0.00"))
        self.assertEqual(res.imbarq004_saldo_liquido, Decimal("51104.70"))
        self.assertEqual(res.spb_valor, Decimal("51104.70"))

    def test_reconciliation_date_specific_match(self) -> None:
        """Valida reconciliação determinística filtrada pela data de liquidação específica."""
        # No imbarq004-valid.txt:
        # Lançamentos para 2026-11-04:
        # - Crédito 21.250,00
        # - Débito 8.500,00
        # - Débito 145,30
        # Saldo líquido em 2026-11-04: 21.250,00 - 8.645,30 = R$ 12.604,70 Credor ('C')
        imbarq = parse_imbarq004(self.imbarq004_valid_path)

        ldl_rec = LdlSettlementRecord(
            num_ctrl_ldl="LDL-DATE-MATCH-01",
            num_ctrl_str="STR-20261104-200",
            ispb_camara="00997185",
            ispb_participante="12345678",
            tipo_debito_credito="C",
            valor_liquidacao=Decimal("12604.70"),
            data_liquidacao=date(2026, 11, 4),
            numero_conta="0000123456",
        )

        res = self.engine.reconcile_with_imbarq004(ldl_rec, imbarq, scope="settlement_date")
        self.assertTrue(res.is_reconciled)
        self.assertEqual(res.status, "MATCHED")
        self.assertEqual(res.diff_valor, Decimal("0.00"))
        self.assertEqual(res.imbarq004_saldo_liquido, Decimal("12604.70"))
        self.assertEqual(res.spb_valor, Decimal("12604.70"))

    def test_reconciliation_mismatch_amount(self) -> None:
        """Detecta divergência de valor financeiro entre o IMBARQ004 e a mensagem SPB."""
        imbarq = parse_imbarq004(self.imbarq004_valid_path)

        # Informa valor com 1 centavo de diferença
        ldl_rec = LdlSettlementRecord(
            num_ctrl_ldl="LDL-DIFF-01",
            num_ctrl_str="STR-20261104-300",
            ispb_camara="00997185",
            ispb_participante="12345678",
            tipo_debito_credito="C",
            valor_liquidacao=Decimal("51104.71"),  # 1 centavo a mais
            data_liquidacao=date(2026, 11, 4),
            numero_conta="0000123456",
        )

        res = self.engine.reconcile_with_imbarq004(ldl_rec, imbarq, scope="trailer")
        self.assertFalse(res.is_reconciled)
        self.assertEqual(res.status, "MISMATCH_AMOUNT")
        self.assertEqual(res.diff_valor, Decimal("0.01"))
        self.assertIn("Divergência de montante", res.detalhes[0])

    def test_reconciliation_mismatch_direction(self) -> None:
        """Detecta divergência de natureza contábil (Crédito vs Débito)."""
        imbarq = parse_imbarq004(self.imbarq004_valid_path)

        # Valor coincide mas natureza está como Débito 'D' em vez de 'C'
        ldl_rec = LdlSettlementRecord(
            num_ctrl_ldl="LDL-DIR-DIFF-01",
            num_ctrl_str="STR-20261104-400",
            ispb_camara="00997185",
            ispb_participante="12345678",
            tipo_debito_credito="D",
            valor_liquidacao=Decimal("51104.70"),
            data_liquidacao=date(2026, 11, 4),
            numero_conta="0000123456",
        )

        res = self.engine.reconcile_with_imbarq004(ldl_rec, imbarq, scope="trailer")
        self.assertFalse(res.is_reconciled)
        self.assertEqual(res.status, "MISMATCH_DIRECTION")
        self.assertIn("Divergência de natureza financeira", res.detalhes[0])

    def test_reconciliation_mismatch_date(self) -> None:
        """Detecta divergência explícita na data de liquidação acordada."""
        imbarq = parse_imbarq004(self.imbarq004_valid_path)

        ldl_rec = LdlSettlementRecord(
            num_ctrl_ldl="LDL-DATE-DIFF-01",
            num_ctrl_str="STR-20261104-500",
            ispb_camara="00997185",
            ispb_participante="12345678",
            tipo_debito_credito="C",
            valor_liquidacao=Decimal("51104.70"),
            data_liquidacao=date(2026, 11, 4),
            numero_conta="0000123456",
        )

        # Exige data alvo 2026-11-05
        res = self.engine.reconcile_with_imbarq004(
            ldl_rec,
            imbarq,
            target_date=date(2026, 11, 5),
            scope="trailer",
        )
        self.assertFalse(res.is_reconciled)
        self.assertEqual(res.status, "MISMATCH_DATE")
        self.assertIn("Divergência de data de liquidação", res.detalhes[0])


if __name__ == "__main__":
    unittest.main()
