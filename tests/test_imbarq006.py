"""Testes automatizados para o parser do layout IMBARQ006 (Posições da Câmara B3)."""

from datetime import date, time
from decimal import Decimal
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH independentemente da forma de execução
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.imbarq006 import (
    Imbarq006Parser,
    InvalidRecordLengthError,
    InvalidRecordStructureError,
    RecordParsingError,
    TrailerValidationError,
    parse_imbarq006,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestImbarq006Parser(unittest.TestCase):
    """Bateria de testes unitários e de integração do parser IMBARQ006."""

    def setUp(self) -> None:
        self.parser = Imbarq006Parser()
        self.valid_fixture_path = FIXTURES_DIR / "imbarq006-valid.txt"
        self.bad_trailer_path = FIXTURES_DIR / "imbarq006-bad-trailer.txt"
        self.bad_length_path = FIXTURES_DIR / "imbarq006-bad-length.txt"

    def test_parse_valid_fixture(self) -> None:
        """Valida parsing completo do arquivo de posições sintético válido."""
        res = parse_imbarq006(self.valid_fixture_path)

        # 1. Header
        self.assertEqual(res.header.tipo_registro, "00")
        self.assertEqual(res.header.identificador_layout, "IMBARQ006")
        self.assertEqual(res.header.versao_layout, 1)
        self.assertEqual(res.header.data_geracao, date(2026, 11, 3))
        self.assertEqual(res.header.hora_geracao, time(23, 0, 0))
        self.assertEqual(res.header.data_movimento, date(2026, 11, 3))
        self.assertEqual(res.header.codigo_participante, 105)
        self.assertEqual(res.header.nome_participante, "CORRETORA ATLAS EXEMPLO S/A")
        self.assertEqual(res.header.sequencial_remessa, 1)

        # 2. Detalhes (3 posições)
        self.assertEqual(res.total_posicoes, 3)

        # Posição 1: PETR4 (À Vista)
        p1 = res.detalhes[0]
        self.assertEqual(p1.codigo_negociacao, "PETR4")
        self.assertEqual(p1.codigo_isin, "BRPETRACNPR6")
        self.assertEqual(p1.tipo_mercado, "VIS")
        self.assertEqual(p1.sentido_posicao, "C")
        self.assertEqual(p1.quantidade_bruta, 1000)
        self.assertEqual(p1.quantidade_bloqueada, 0)
        self.assertEqual(p1.quantidade_liquida, 1000)
        self.assertEqual(p1.preco_fechamento_ajuste, Decimal("38.50"))
        self.assertEqual(p1.valor_financeiro, Decimal("38500.00"))
        self.assertIsNone(p1.data_vencimento)
        self.assertIsNone(p1.codigo_serie)

        # Posição 2: PETRL350 (Opção)
        p2 = res.detalhes[1]
        self.assertEqual(p2.codigo_negociacao, "PETRL350")
        self.assertEqual(p2.codigo_isin, "BRPETRL35001")
        self.assertEqual(p2.tipo_mercado, "OPC")
        self.assertEqual(p2.sentido_posicao, "C")
        self.assertEqual(p2.quantidade_liquida, 5000)
        self.assertEqual(p2.preco_fechamento_ajuste, Decimal("4.25"))
        self.assertEqual(p2.valor_financeiro, Decimal("21250.00"))
        self.assertEqual(p2.data_vencimento, date(2026, 12, 18))
        self.assertEqual(p2.codigo_serie, "PETRL350")

        # Posição 3: WDOZ26 (Futuros)
        p3 = res.detalhes[2]
        self.assertEqual(p3.codigo_negociacao, "WDOZ26")
        self.assertEqual(p3.codigo_isin, "BRBMEFWDO001")
        self.assertEqual(p3.tipo_mercado, "FUT")
        self.assertEqual(p3.sentido_posicao, "V")
        self.assertEqual(p3.quantidade_liquida, 10)
        self.assertEqual(p3.preco_fechamento_ajuste, Decimal("5850.00"))
        self.assertEqual(p3.valor_financeiro, Decimal("58500.00"))
        self.assertEqual(p3.data_vencimento, date(2026, 12, 1))
        self.assertEqual(p3.codigo_serie, "WDOZ26")

        # 3. Trailler
        self.assertEqual(res.trailer.tipo_registro, "99")
        self.assertEqual(res.trailer.identificador_layout, "IMBARQ006")
        self.assertEqual(res.trailer.total_registros_arquivo, 5)
        self.assertEqual(res.trailer.total_registros_detalhe, 3)
        self.assertEqual(res.trailer.total_quantidade_liquida, 6010)
        self.assertEqual(res.trailer.total_valor_financeiro, Decimal("118250.00"))

    def test_rejection_bad_trailer(self) -> None:
        """Valida que arquivo com trailler adulterado ou corrompido é rejeitado estritamente."""
        with self.assertRaises(TrailerValidationError) as ctx:
            parse_imbarq006(self.bad_trailer_path)

        err_msg = str(ctx.exception)
        self.assertIn("Total de registros de detalhe diverge", err_msg)
        self.assertIn("Somatório de quantidade líquida diverge", err_msg)
        self.assertIn("Somatório de valor financeiro diverge", err_msg)

    def test_rejection_bad_length(self) -> None:
        """Valida que linha com tamanho diferente de 200 colunas causa InvalidRecordLengthError."""
        with self.assertRaises(InvalidRecordLengthError) as ctx:
            parse_imbarq006(self.bad_length_path)

        self.assertEqual(ctx.exception.line_number, 3)
        self.assertEqual(ctx.exception.expected_len, 200)
        self.assertEqual(ctx.exception.actual_len, 150)

    def test_empty_file_rejection(self) -> None:
        """Valida que arquivo vazio ou com linhas insuficientes é rejeitado."""
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([])

        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines(["00" + " " * 198])

    def test_empty_positions_file_valid(self) -> None:
        """Valida caso de borda: arquivo sem posições (Header + Trailler zerado)."""
        h = "00" + "IMBARQ006" + "001" + "20261103" + "230000" + "20261103" + "00000105" + "CORRETORA ATLAS EXEMPLO S/A   " + "000001" + (" " * 120)
        # 2 registros no arquivo, 0 detalhes, 0 quantidade, 0.00 valor
        t = "99" + "IMBARQ006" + "00000002" + "00000000" + "000000000000000000" + "000000000000000000" + (" " * 137)
        self.assertEqual(len(h), 200)
        self.assertEqual(len(t), 200)

        res = self.parser.parse_lines([h, t])
        self.assertEqual(res.total_posicoes, 0)
        self.assertEqual(res.trailer.total_registros_arquivo, 2)
        self.assertEqual(res.trailer.total_quantidade_liquida, 0)
        self.assertEqual(res.trailer.total_valor_financeiro, Decimal("0.00"))

    def test_invalid_layout_id(self) -> None:
        """Valida que Header com identificador de layout diferente é rejeitado."""
        h_bad = "00" + "OUTRO0000" + "001" + "20261103" + "230000" + "20261103" + "00000105" + "CORRETORA ATLAS EXEMPLO S/A   " + "000001" + (" " * 120)
        t = "99" + "IMBARQ006" + "00000002" + "00000000" + "000000000000000000" + "000000000000000000" + (" " * 137)
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([h_bad, t])

    def test_invalid_number_in_detail(self) -> None:
        """Valida que caracteres não numéricos em campos numéricos disparam RecordParsingError."""
        # Altera quantidade de dígitos válidos para letras na quantidade bruta
        with open(self.valid_fixture_path, "r", encoding="ascii") as f:
            lines = [l.rstrip("\r\n") for l in f]

        bad_d1 = lines[1][:50] + "ABCDEFGHIJKLMNO" + lines[1][65:]
        lines[1] = bad_d1
        with self.assertRaises(RecordParsingError):
            self.parser.parse_lines(lines)


if __name__ == "__main__":
    unittest.main()
