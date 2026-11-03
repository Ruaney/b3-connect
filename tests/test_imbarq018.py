"""Testes automatizados para o parser do layout IMBARQ018 (Eventos Corporativos Data Ex B3)."""

from datetime import date, time
from decimal import Decimal
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH independentemente da forma de execução
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.imbarq018 import (
    CorporateEventDetailRecord,
    Imbarq018File,
    Imbarq018Parser,
    InvalidRecordLengthError,
    InvalidRecordStructureError,
    RecordParsingError,
    TrailerValidationError,
    parse_imbarq018,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestImbarq018Parser(unittest.TestCase):
    """Bateria de testes unitários e de integração do parser IMBARQ018."""

    def setUp(self) -> None:
        self.parser = Imbarq018Parser()
        self.valid_fixture_path = FIXTURES_DIR / "imbarq018-valid.txt"
        self.bad_trailer_path = FIXTURES_DIR / "imbarq018-bad-trailer.txt"
        self.bad_length_path = FIXTURES_DIR / "imbarq018-bad-length.txt"

    def test_parse_valid_fixture(self) -> None:
        """Valida parsing completo do arquivo de eventos corporativos válido."""
        res = parse_imbarq018(self.valid_fixture_path)

        # 1. Header (00)
        self.assertEqual(res.header.tipo_registro, "00")
        self.assertEqual(res.header.identificador_layout, "IMBARQ018")
        self.assertEqual(res.header.versao_layout, 1)
        self.assertEqual(res.header.data_geracao, date(2026, 11, 3))
        self.assertEqual(res.header.hora_geracao, time(23, 0, 0))
        self.assertEqual(res.header.data_movimento, date(2026, 11, 3))
        self.assertEqual(res.header.codigo_participante, 105)
        self.assertEqual(res.header.nome_participante, "CORRETORA ATLAS EXEMPLO S/A")
        self.assertEqual(res.header.sequencial_remessa, 1)

        # 2. Detalhes (3 eventos societários: DIVID PETR4, JCP B3SA3, BONIF VALE3)
        self.assertEqual(res.total_eventos, 3)

        # Evento 1: Dividendos PETR4
        d1 = res.detalhes[0]
        self.assertEqual(d1.tipo_registro, "01")
        self.assertEqual(d1.codigo_participante, 105)
        self.assertEqual(d1.codigo_conta_custodia, 123456)
        self.assertEqual(d1.conta_custodia, 123456)
        self.assertEqual(d1.codigo_negociacao, "PETR4")
        self.assertEqual(d1.ticker, "PETR4")
        self.assertEqual(d1.codigo_isin, "BRPETRACNPR6")
        self.assertEqual(d1.isin, "BRPETRACNPR6")
        self.assertEqual(d1.codigo_evento, "DIVID")
        self.assertEqual(d1.data_com, date(2026, 10, 30))
        self.assertEqual(d1.data_ex, date(2026, 11, 3))
        self.assertEqual(d1.data_pagamento, date(2026, 11, 20))
        self.assertEqual(d1.valor_provento, Decimal("1.250000"))
        self.assertEqual(d1.percentual_evento, Decimal("0.0000"))
        self.assertEqual(d1.indicador_tributacao, "N")
        self.assertFalse(d1.is_tributado)
        self.assertEqual(d1.status_evento, "APROV")
        self.assertTrue(d1.is_aprovado)
        self.assertFalse(d1.is_cancelado)
        self.assertFalse(d1.is_pendente)
        self.assertTrue(d1.is_provento_em_dinheiro)
        self.assertFalse(d1.is_evento_acionario)

        # Evento 2: JCP B3SA3
        d2 = res.detalhes[1]
        self.assertEqual(d2.tipo_registro, "01")
        self.assertEqual(d2.codigo_participante, 105)
        self.assertEqual(d2.codigo_conta_custodia, 123456)
        self.assertEqual(d2.ticker, "B3SA3")
        self.assertEqual(d2.isin, "BRB3SAACNOR6")
        self.assertEqual(d2.codigo_evento, "JCP")
        self.assertEqual(d2.data_com, date(2026, 10, 25))
        self.assertEqual(d2.data_ex, date(2026, 10, 26))
        self.assertEqual(d2.data_pagamento, date(2026, 11, 15))
        self.assertEqual(d2.valor_provento, Decimal("0.085000"))
        self.assertEqual(d2.percentual_evento, Decimal("0.0000"))
        self.assertEqual(d2.indicador_tributacao, "S")
        self.assertTrue(d2.is_tributado)
        self.assertEqual(d2.status_evento, "APROV")
        self.assertTrue(d2.is_aprovado)
        self.assertTrue(d2.is_provento_em_dinheiro)
        self.assertFalse(d2.is_evento_acionario)

        # Evento 3: Bonificação VALE3
        d3 = res.detalhes[2]
        self.assertEqual(d3.tipo_registro, "01")
        self.assertEqual(d3.codigo_participante, 105)
        self.assertEqual(d3.codigo_conta_custodia, 789012)
        self.assertEqual(d3.ticker, "VALE3")
        self.assertEqual(d3.isin, "BRVALEACNOR0")
        self.assertEqual(d3.codigo_evento, "BONIF")
        self.assertEqual(d3.data_com, date(2026, 10, 15))
        self.assertEqual(d3.data_ex, date(2026, 10, 16))
        self.assertEqual(d3.data_pagamento, date(2026, 10, 31))
        self.assertEqual(d3.valor_provento, Decimal("0.000000"))
        self.assertEqual(d3.percentual_evento, Decimal("5.0000"))
        self.assertEqual(d3.indicador_tributacao, "N")
        self.assertFalse(d3.is_tributado)
        self.assertEqual(d3.status_evento, "APROV")
        self.assertTrue(d3.is_aprovado)
        self.assertFalse(d3.is_provento_em_dinheiro)
        self.assertTrue(d3.is_evento_acionario)

        # 3. Trailler (99)
        self.assertEqual(res.trailer.tipo_registro, "99")
        self.assertEqual(res.trailer.identificador_layout, "IMBARQ018")
        self.assertEqual(res.trailer.total_registros_arquivo, 5)
        self.assertEqual(res.trailer.total_registros_detalhe, 3)
        self.assertEqual(res.trailer.total_valor_proventos, Decimal("1.335000"))

        # 4. Métodos de agregação e filtros de Imbarq018File
        self.assertEqual(len(res.get_eventos_por_tipo("DIVID")), 1)
        self.assertEqual(len(res.get_eventos_por_tipo("JCP")), 1)
        self.assertEqual(len(res.get_eventos_por_tipo("BONIF")), 1)
        self.assertEqual(len(res.get_eventos_por_tipo("SUBSC")), 0)

        self.assertEqual(len(res.get_eventos_por_ticker("PETR4")), 1)
        self.assertEqual(len(res.get_eventos_por_ticker("VALE3")), 1)
        self.assertEqual(len(res.get_eventos_por_ticker("ITUB4")), 0)

        self.assertEqual(len(res.get_eventos_por_conta(123456)), 2)
        self.assertEqual(len(res.get_eventos_por_conta(789012)), 1)
        self.assertEqual(len(res.get_eventos_por_conta(999999)), 0)

        self.assertEqual(len(res.get_eventos_aprovados()), 3)
        self.assertEqual(len(res.get_eventos_pendentes()), 0)
        self.assertEqual(len(res.get_eventos_cancelados()), 0)

        self.assertEqual(res.total_proventos(), Decimal("1.335000"))
        totais_tipo = res.total_proventos_por_tipo()
        self.assertEqual(totais_tipo["DIVID"], Decimal("1.250000"))
        self.assertEqual(totais_tipo["JCP"], Decimal("0.085000"))
        self.assertEqual(totais_tipo["BONIF"], Decimal("0.000000"))

    def test_rejection_bad_trailer(self) -> None:
        """Valida que somatório de proventos corrompido no Trailler é estritamente rejeitado."""
        with self.assertRaises(TrailerValidationError) as ctx:
            parse_imbarq018(self.bad_trailer_path)

        msg = str(ctx.exception)
        self.assertIn("Falha de validação de integridade no Trailler", msg)
        self.assertIn("Somatório de valor de proventos diverge", msg)

    def test_rejection_bad_length(self) -> None:
        """Valida que linha com tamanho diferente de 200 caracteres dispara InvalidRecordLengthError."""
        with self.assertRaises(InvalidRecordLengthError) as ctx:
            parse_imbarq018(self.bad_length_path)

        err = ctx.exception
        self.assertEqual(err.line_number, 2)
        self.assertEqual(err.expected_len, 200)
        self.assertEqual(err.actual_len, 150)

    def test_rejection_bad_header_layout_id(self) -> None:
        """Valida rejeição de Header com identificador de layout divergente."""
        raw_header = "00IMBARQ999001202611032300002026110300000105CORRETORA ATLAS EXEMPLO S/A   000001" + " " * 120
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_line_header(raw_header, line_no=1)

    def test_rejection_bad_header_record_type(self) -> None:
        """Valida rejeição de Header cujo tipo não é '00'."""
        raw_header = "01IMBARQ018001202611032300002026110300000105CORRETORA ATLAS EXEMPLO S/A   000001" + " " * 120
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_line_header(raw_header, line_no=1)

    def test_rejection_bad_detail_event_type(self) -> None:
        """Valida rejeição de código de evento societário não reconhecido."""
        line = (
            "0100000105000000123456PETR4       BRPETRACNPR6INVALID202610302026110320261120"
            "000000000000125000000000000000000000NAPROV" + " " * 82
        )
        with self.assertRaises(RecordParsingError) as ctx:
            self.parser.parse_line_detail(line, line_no=2)
        self.assertIn("codigo_evento", str(ctx.exception))

    def test_rejection_bad_detail_tax_indicator(self) -> None:
        """Valida rejeição de indicador de tributação fora de ('S', 'N')."""
        line = (
            "0100000105000000123456PETR4       BRPETRACNPR6DIVID 202610302026110320261120"
            "000000000000125000000000000000000000XAPROV" + " " * 82
        )
        with self.assertRaises(RecordParsingError) as ctx:
            self.parser.parse_line_detail(line, line_no=2)
        self.assertIn("indicador_tributacao", str(ctx.exception))

    def test_rejection_bad_detail_status(self) -> None:
        """Valida rejeição de status de evento corporativo fora de ('APROV', 'CANC', 'PEND')."""
        line = (
            "0100000105000000123456PETR4       BRPETRACNPR6DIVID 202610302026110320261120"
            "000000000000125000000000000000000000NXYZ  " + " " * 82
        )
        with self.assertRaises(RecordParsingError) as ctx:
            self.parser.parse_line_detail(line, line_no=2)
        self.assertIn("status_evento", str(ctx.exception))

    def test_parse_detail_with_zero_payment_date(self) -> None:
        """Valida que data de pagamento 00000000 é convertida para None (data a definir)."""
        line = (
            "0100000105000000123456PETR4       BRPETRACNPR6DIVID 202610302026110300000000"
            "000000000000125000000000000000000000NAPROV" + " " * 82
        )
        d = self.parser.parse_line_detail(line, line_no=2)
        self.assertIsNone(d.data_pagamento)

    def test_rejection_trailer_mismatch_counts(self) -> None:
        """Valida rejeição quando trailler contém contagem divergente de registros ou detalhes."""
        h = "00IMBARQ018001202611032300002026110300000105CORRETORA ATLAS EXEMPLO S/A   000001" + " " * 120
        d = (
            "0100000105000000123456PETR4       BRPETRACNPR6DIVID 202610302026110320261120"
            "000000000000125000000000000000000000NAPROV" + " " * 82
        )
        # Trailler alegando 10 registros de arquivo e 5 detalhes
        t = "99IMBARQ01800000010000000050000000000001250000" + " " * 154

        with self.assertRaises(TrailerValidationError) as ctx:
            self.parser.parse_lines([h, d, t])
        self.assertIn("Total de registros do arquivo diverge", str(ctx.exception))
        self.assertIn("Total de registros de detalhe diverge", str(ctx.exception))

    def test_rejection_insufficient_lines(self) -> None:
        """Valida que arquivo com apenas 1 linha é rejeitado por estrutura inválida."""
        h = "00IMBARQ018001202611032300002026110300000105CORRETORA ATLAS EXEMPLO S/A   000001" + " " * 120
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([h])


if __name__ == "__main__":
    unittest.main()
