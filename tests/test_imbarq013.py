"""Testes automatizados para o parser do layout IMBARQ013 (Movimentações da Depositária B3)."""

from datetime import date, time
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.imbarq013 import (
    Imbarq013Parser,
    InvalidRecordLengthError,
    InvalidRecordStructureError,
    RecordParsingError,
    TrailerValidationError,
    parse_imbarq013,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestImbarq013Parser(unittest.TestCase):
    """Bateria de testes unitários e de integração do parser IMBARQ013."""

    def setUp(self) -> None:
        self.parser = Imbarq013Parser()
        self.valid_fixture_path = FIXTURES_DIR / "imbarq013-valid.txt"
        self.bad_trailer_path = FIXTURES_DIR / "imbarq013-bad-trailer.txt"
        self.bad_length_path = FIXTURES_DIR / "imbarq013-bad-length.txt"

    def test_parse_valid_fixture(self) -> None:
        """Valida parsing completo do arquivo de movimentações de custódia válido."""
        res = parse_imbarq013(self.valid_fixture_path)

        # 1. Header (00)
        self.assertEqual(res.header.tipo_registro, "00")
        self.assertEqual(res.header.identificador_layout, "IMBARQ013")
        self.assertEqual(res.header.versao_layout, 1)
        self.assertEqual(res.header.data_geracao, date(2026, 11, 3))
        self.assertEqual(res.header.hora_geracao, time(23, 0, 0))
        self.assertEqual(res.header.data_movimento, date(2026, 11, 3))
        self.assertEqual(res.header.codigo_participante, 105)
        self.assertEqual(res.header.nome_participante, "CORRETORA ATLAS EXEMPLO S/A")
        self.assertEqual(res.header.sequencial_remessa, 1)

        # 2. Detalhes (3 movimentações: PETR4, VALE3, BOVA11)
        self.assertEqual(res.total_movimentacoes, 3)

        # Detalhe 1: PETR4 Compra Pregão (Crédito de 5000)
        m1 = res.movimentacoes[0]
        self.assertEqual(m1.tipo_registro, "01")
        self.assertEqual(m1.numero_controle, 20260001)
        self.assertEqual(m1.conta_custodia_origem, 1)
        self.assertEqual(m1.conta_custodia_destino, 123456)
        self.assertEqual(m1.codigo_negociacao, "PETR4")
        self.assertEqual(m1.codigo_isin, "BRPETRACNPR6")
        self.assertEqual(m1.tipo_movimentacao, "LIQ_PREGAO")
        self.assertEqual(m1.data_movimento, date(2026, 11, 3))
        self.assertEqual(m1.data_liquidacao_fisica, date(2026, 11, 5))
        self.assertEqual(m1.indicador_debito_credito, "C")
        self.assertTrue(m1.is_credito)
        self.assertFalse(m1.is_debito)
        self.assertEqual(m1.quantidade_movimentada, 5000)
        self.assertEqual(m1.motivo_historico, "LIQUIDACAO FISICA PREGAO COMPRA")

        # Detalhe 2: VALE3 Bloqueio Judicial (Débito de 2000)
        m2 = res.movimentacoes[1]
        self.assertEqual(m2.codigo_negociacao, "VALE3")
        self.assertEqual(m2.tipo_movimentacao, "BLOQ_JUD")
        self.assertEqual(m2.indicador_debito_credito, "D")
        self.assertTrue(m2.is_debito)
        self.assertFalse(m2.is_credito)
        self.assertEqual(m2.quantidade_movimentada, 2000)

        # Detalhe 3: BOVA11 Transferência de Custódia STVM (Débito de 1000)
        m3 = res.movimentacoes[2]
        self.assertEqual(m3.codigo_negociacao, "BOVA11")
        self.assertEqual(m3.tipo_movimentacao, "TRANSF_CUST")
        self.assertEqual(m3.quantidade_movimentada, 1000)

        # 3. Trailler (99)
        self.assertEqual(res.trailer.tipo_registro, "99")
        self.assertEqual(res.trailer.identificador_layout, "IMBARQ013")
        self.assertEqual(res.trailer.total_registros_arquivo, 5)
        self.assertEqual(res.trailer.total_registros_detalhe, 3)
        self.assertEqual(res.trailer.total_quantidade_debito, 3000)
        self.assertEqual(res.trailer.total_quantidade_credito, 5000)
        self.assertEqual(res.trailer.sinal_saldo_liquido, "C")
        self.assertEqual(res.trailer.saldo_liquido_movimentacao, 2000)

    def test_filtros_imbarq013_file(self) -> None:
        """Testa utilitários de filtro de ativos, tipos e contas."""
        res = parse_imbarq013(self.valid_fixture_path)

        petr4_movs = res.filtrar_por_ticker("PETR4")
        self.assertEqual(len(petr4_movs), 1)
        self.assertEqual(petr4_movs[0].codigo_negociacao, "PETR4")

        liq_movs = res.filtrar_por_tipo("LIQ_PREGAO")
        self.assertEqual(len(liq_movs), 1)

        conta_movs = res.filtrar_por_conta(123456)
        self.assertEqual(len(conta_movs), 3)

    def test_bad_trailer_detection(self) -> None:
        """Garante rejeição quando os somatórios do trailler não batem."""
        with self.assertRaises(TrailerValidationError):
            parse_imbarq013(self.bad_trailer_path)

    def test_bad_record_length(self) -> None:
        """Garante rejeição estrita de linhas com comprimento diferente de 200 colunas."""
        with self.assertRaises(InvalidRecordLengthError):
            parse_imbarq013(self.bad_length_path)

    def test_empty_input_raises_structure_error(self) -> None:
        """Garante rejeição de arquivo vazio sem Header."""
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([])

    def test_invalid_movement_type_raises_parsing_error(self) -> None:
        """Garante rejeição quando código de tipo de movimentação é inválido."""
        raw_header = (
            "00IMBARQ013001202611032300002026110300000105CORRETORA ATLAS EXEMPLO S/A   000001" + " " * 120
        )
        raw_detail_bad_type = (
            "01000000020260001000000000001000000123456PETR4       BRPETRACNPR6INVALID_TYPE2026110320261105C"
            "000000000005000LIQUIDACAO FISICA PREGAO COMPRA         " + " " * 51
        )
        raw_trailer = (
            "99IMBARQ0130000000300000001000000000000000000000000000000005000C000000000000005000" + " " * 118
        )
        with self.assertRaises(RecordParsingError) as ctx:
            self.parser.parse_lines([raw_header, raw_detail_bad_type, raw_trailer])
        self.assertIn("tipo_movimentacao", str(ctx.exception))

    def test_invalid_dc_indicator_raises_parsing_error(self) -> None:
        """Garante rejeição quando indicador de débito/crédito é diferente de 'D' ou 'C'."""
        raw_header = (
            "00IMBARQ013001202611032300002026110300000105CORRETORA ATLAS EXEMPLO S/A   000001" + " " * 120
        )
        raw_detail_bad_dc = (
            "01000000020260001000000000001000000123456PETR4       BRPETRACNPR6LIQ_PREGAO  2026110320261105X"
            "000000000005000LIQUIDACAO FISICA PREGAO COMPRA         " + " " * 51
        )
        raw_trailer = (
            "99IMBARQ0130000000300000001000000000000000000000000000000005000C000000000000005000" + " " * 118
        )
        with self.assertRaises(RecordParsingError) as ctx:
            self.parser.parse_lines([raw_header, raw_detail_bad_dc, raw_trailer])
        self.assertIn("indicador_debito_credito", str(ctx.exception))

    def test_settlement_before_movement_date_raises_error(self) -> None:
        """Garante rejeição quando data de liquidação física é anterior à data do movimento."""
        raw_header = (
            "00IMBARQ013001202611032300002026110300000105CORRETORA ATLAS EXEMPLO S/A   000001" + " " * 120
        )
        raw_detail_past_date = (
            "01000000020260001000000000001000000123456PETR4       BRPETRACNPR6LIQ_PREGAO  2026110320261101C"
            "000000000005000LIQUIDACAO FISICA PREGAO COMPRA         " + " " * 51
        )
        raw_trailer = (
            "99IMBARQ0130000000300000001000000000000000000000000000000005000C000000000000005000" + " " * 118
        )
        with self.assertRaises(RecordParsingError) as ctx:
            self.parser.parse_lines([raw_header, raw_detail_past_date, raw_trailer])
        self.assertIn("data_liquidacao_fisica", str(ctx.exception))

    def test_parse_from_io_stream(self) -> None:
        """Testa parsing a partir de buffer StringIO em memória."""
        import io
        content = self.valid_fixture_path.read_text(encoding="ascii")
        res = parse_imbarq013(io.StringIO(content))
        self.assertEqual(res.total_movimentacoes, 3)
        self.assertEqual(res.header.identificador_layout, "IMBARQ013")


if __name__ == "__main__":
    unittest.main()
