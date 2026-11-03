"""Testes automatizados para o parser do layout IMBARQ012 (Posições da Depositária B3)."""

from datetime import date, time
from decimal import Decimal
import io
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH independentemente da forma de execução
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.imbarq012 import (
    Imbarq012Parser,
    InvalidRecordLengthError,
    InvalidRecordStructureError,
    PositionConsistencyError,
    RecordParsingError,
    TrailerValidationError,
    parse_imbarq012,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestImbarq012Parser(unittest.TestCase):
    """Bateria de testes unitários e de integração do parser IMBARQ012."""

    def setUp(self) -> None:
        self.parser = Imbarq012Parser()
        self.valid_fixture_path = FIXTURES_DIR / "imbarq012-valid.txt"
        self.bad_trailer_path = FIXTURES_DIR / "imbarq012-bad-trailer.txt"

    def test_parse_valid_fixture(self) -> None:
        """Valida parsing completo do arquivo de posições da depositária válido."""
        res = parse_imbarq012(self.valid_fixture_path)

        # 1. Header
        self.assertEqual(res.header.tipo_registro, "00")
        self.assertEqual(res.header.identificador_layout, "IMBARQ012")
        self.assertEqual(res.header.versao_layout, 1)
        self.assertEqual(res.header.data_geracao, date(2026, 11, 3))
        self.assertEqual(res.header.hora_geracao, time(23, 0, 0))
        self.assertEqual(res.header.data_movimento, date(2026, 11, 3))
        self.assertEqual(res.header.codigo_participante, 105)
        self.assertEqual(res.header.nome_participante, "CORRETORA ATLAS EXEMPLO S/A")
        self.assertEqual(res.header.sequencial_remessa, 1)

        # 2. Detalhes (3 posições)
        self.assertEqual(res.total_posicoes, 3)

        # Posição 1: PETR4 (À Vista com bloqueios judiciais e de garantia)
        p1 = res.detalhes[0]
        self.assertEqual(p1.tipo_registro, "01")
        self.assertEqual(p1.codigo_participante, 105)
        self.assertEqual(p1.codigo_conta_custodia, 123456)
        self.assertEqual(p1.codigo_negociacao, "PETR4")
        self.assertEqual(p1.codigo_isin, "BRPETRACNPR6")
        self.assertEqual(p1.tipo_mercado, "VIS")
        self.assertEqual(p1.quantidade_total, 1000)
        self.assertEqual(p1.quantidade_disponivel, 700)
        self.assertEqual(p1.quantidade_bloqueada_garantia, 200)
        self.assertEqual(p1.quantidade_bloqueada_judicial, 100)
        self.assertEqual(p1.quantidade_bloqueada_total, 300)
        self.assertTrue(p1.is_bloqueado)
        self.assertEqual(p1.quantidade_eventos_pendentes, 0)
        self.assertFalse(p1.has_eventos_pendentes)
        self.assertEqual(p1.preco_fechamento, Decimal("38.50"))
        self.assertEqual(p1.valor_financeiro, Decimal("38500.00"))
        self.assertIsNone(p1.data_liquidacao_evento)
        self.assertIsNone(p1.tipo_evento)

        # Posição 2: VALE3 (À Vista com bloqueio de garantia e evento de bonificação pendente)
        p2 = res.detalhes[1]
        self.assertEqual(p2.codigo_participante, 105)
        self.assertEqual(p2.codigo_conta_custodia, 123456)
        self.assertEqual(p2.codigo_negociacao, "VALE3")
        self.assertEqual(p2.codigo_isin, "BRVALEACNOR0")
        self.assertEqual(p2.tipo_mercado, "VIS")
        self.assertEqual(p2.quantidade_total, 5000)
        self.assertEqual(p2.quantidade_disponivel, 4500)
        self.assertEqual(p2.quantidade_bloqueada_garantia, 500)
        self.assertEqual(p2.quantidade_bloqueada_judicial, 0)
        self.assertEqual(p2.quantidade_bloqueada_total, 500)
        self.assertTrue(p2.is_bloqueado)
        self.assertEqual(p2.quantidade_eventos_pendentes, 100)
        self.assertTrue(p2.has_eventos_pendentes)
        self.assertEqual(p2.preco_fechamento, Decimal("62.10"))
        self.assertEqual(p2.valor_financeiro, Decimal("310500.00"))
        self.assertEqual(p2.data_liquidacao_evento, date(2026, 11, 20))
        self.assertEqual(p2.tipo_evento, "BONIF")

        # Posição 3: BOVA11 (ETF 100% disponível sem bloqueios ou eventos)
        p3 = res.detalhes[2]
        self.assertEqual(p3.codigo_participante, 105)
        self.assertEqual(p3.codigo_conta_custodia, 789012)
        self.assertEqual(p3.codigo_negociacao, "BOVA11")
        self.assertEqual(p3.codigo_isin, "BRBOVACTF001")
        self.assertEqual(p3.tipo_mercado, "ETF")
        self.assertEqual(p3.quantidade_total, 200)
        self.assertEqual(p3.quantidade_disponivel, 200)
        self.assertEqual(p3.quantidade_bloqueada_garantia, 0)
        self.assertEqual(p3.quantidade_bloqueada_judicial, 0)
        self.assertEqual(p3.quantidade_bloqueada_total, 0)
        self.assertFalse(p3.is_bloqueado)
        self.assertEqual(p3.quantidade_eventos_pendentes, 0)
        self.assertFalse(p3.has_eventos_pendentes)
        self.assertEqual(p3.preco_fechamento, Decimal("124.50"))
        self.assertEqual(p3.valor_financeiro, Decimal("24900.00"))
        self.assertIsNone(p3.data_liquidacao_evento)
        self.assertIsNone(p3.tipo_evento)

        # 3. Trailler
        self.assertEqual(res.trailer.tipo_registro, "99")
        self.assertEqual(res.trailer.identificador_layout, "IMBARQ012")
        self.assertEqual(res.trailer.total_registros_arquivo, 5)
        self.assertEqual(res.trailer.total_registros_detalhe, 3)
        self.assertEqual(res.trailer.total_quantidade_total, 6200)
        self.assertEqual(res.trailer.total_quantidade_disponivel, 5400)
        self.assertEqual(res.trailer.total_quantidade_bloqueada, 800)
        self.assertEqual(res.trailer.total_eventos_pendentes, 100)
        self.assertEqual(res.trailer.total_valor_financeiro, Decimal("373900.00"))

        # 4. Métodos Utilitários e Agregações
        self.assertEqual(res.valor_total_carteira, Decimal("373900.00"))
        pos_conta_1 = res.get_posicoes_por_conta(123456)
        self.assertEqual(len(pos_conta_1), 2)
        self.assertEqual([p.codigo_negociacao for p in pos_conta_1], ["PETR4", "VALE3"])

        pos_conta_2 = res.get_posicoes_por_conta(789012)
        self.assertEqual(len(pos_conta_2), 1)
        self.assertEqual(pos_conta_2[0].codigo_negociacao, "BOVA11")

        pos_petr = res.get_posicoes_por_ticker("PETR4")
        self.assertEqual(len(pos_petr), 1)
        self.assertEqual(pos_petr[0].codigo_isin, "BRPETRACNPR6")

        pos_isin = res.get_posicoes_por_isin("BRVALEACNOR0")
        self.assertEqual(len(pos_isin), 1)
        self.assertEqual(pos_isin[0].codigo_negociacao, "VALE3")

    def test_rejection_bad_trailer(self) -> None:
        """Valida que arquivo com trailler divergente é rejeitado com TrailerValidationError."""
        with self.assertRaises(TrailerValidationError) as ctx:
            parse_imbarq012(self.bad_trailer_path)

        err_msg = str(ctx.exception)
        self.assertIn("Total de registros de detalhe diverge", err_msg)
        self.assertIn("Somatório de quantidade total diverge", err_msg)
        self.assertIn("Somatório de quantidade disponível diverge", err_msg)
        self.assertIn("Somatório de quantidade bloqueada diverge", err_msg)
        self.assertIn("Somatório de eventos pendentes diverge", err_msg)
        self.assertIn("Somatório de valor financeiro diverge", err_msg)

    def test_rejection_bad_length(self) -> None:
        """Valida que linha com tamanho diferente de 200 colunas causa InvalidRecordLengthError."""
        with open(self.valid_fixture_path, "r", encoding="ascii") as f:
            lines = [line.rstrip("\r\n") for line in f]

        lines[2] = lines[2][:160]  # trunca para 160 colunas

        with self.assertRaises(InvalidRecordLengthError) as ctx:
            self.parser.parse_lines(lines)

        self.assertEqual(ctx.exception.line_number, 3)
        self.assertEqual(ctx.exception.expected_len, 200)
        self.assertEqual(ctx.exception.actual_len, 160)

    def test_position_consistency_rejection(self) -> None:
        """Valida que registro com total != disponível + garantia + judicial falha."""
        with open(self.valid_fixture_path, "r", encoding="ascii") as f:
            lines = [line.rstrip("\r\n") for line in f]

        # Altera quantidade total de PETR4 para 9999 (divergente da soma das parcelas 700 + 200 + 100 = 1000)
        bad_line = lines[1][:49] + "000000000009999" + lines[1][64:]
        lines[1] = bad_line

        with self.assertRaises(PositionConsistencyError) as ctx:
            self.parser.parse_lines(lines)

        self.assertIn("PETR4", str(ctx.exception))
        self.assertEqual(ctx.exception.quantidade_total, 9999)
        self.assertEqual(ctx.exception.quantidade_disponivel, 700)

    def test_empty_file_and_short_file_rejection(self) -> None:
        """Valida que arquivo vazio ou com menos de 2 linhas é rejeitado."""
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([])

        header_only = "00" + "IMBARQ012" + "001" + "20261103" + "230000" + "20261103" + "00000105" + "CORRETORA ATLAS EXEMPLO S/A   " + "000001" + (" " * 120)
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([header_only])

    def test_empty_positions_file_valid(self) -> None:
        """Valida caso de borda: arquivo sem posições (Header + Trailler zerado)."""
        h = "00" + "IMBARQ012" + "001" + "20261103" + "230000" + "20261103" + "00000105" + "CORRETORA ATLAS EXEMPLO S/A   " + "000001" + (" " * 120)
        # 2 linhas no arquivo, 0 detalhes, 0 em todas as quantidades e valores
        t = "99" + "IMBARQ012" + "00000002" + "00000000" + ("000000000000000000" * 4) + "000000000000000000" + (" " * 83)
        self.assertEqual(len(h), 200)
        self.assertEqual(len(t), 200)

        res = self.parser.parse_lines([h, t])
        self.assertEqual(res.total_posicoes, 0)
        self.assertEqual(res.trailer.total_registros_arquivo, 2)
        self.assertEqual(res.trailer.total_quantidade_total, 0)
        self.assertEqual(res.trailer.total_quantidade_disponivel, 0)
        self.assertEqual(res.trailer.total_quantidade_bloqueada, 0)
        self.assertEqual(res.trailer.total_eventos_pendentes, 0)
        self.assertEqual(res.trailer.total_valor_financeiro, Decimal("0.00"))

    def test_invalid_layout_id(self) -> None:
        """Valida que Header ou Trailler com identificador diferente de IMBARQ012 é rejeitado."""
        h_bad = "00" + "IMBARQ999" + "001" + "20261103" + "230000" + "20261103" + "00000105" + "CORRETORA ATLAS EXEMPLO S/A   " + "000001" + (" " * 120)
        t = "99" + "IMBARQ012" + "00000002" + "00000000" + ("000000000000000000" * 4) + "000000000000000000" + (" " * 83)

        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([h_bad, t])

        h_ok = "00" + "IMBARQ012" + "001" + "20261103" + "230000" + "20261103" + "00000105" + "CORRETORA ATLAS EXEMPLO S/A   " + "000001" + (" " * 120)
        t_bad = "99" + "IMBARQ999" + "00000002" + "00000000" + ("000000000000000000" * 4) + "000000000000000000" + (" " * 83)
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([h_ok, t_bad])

    def test_invalid_number_in_detail(self) -> None:
        """Valida que caracteres não numéricos em campos numéricos disparam RecordParsingError."""
        with open(self.valid_fixture_path, "r", encoding="ascii") as f:
            lines = [line.rstrip("\r\n") for line in f]

        bad_p1 = lines[1][:49] + "ABCDEFGHIJKLMNO" + lines[1][64:]
        lines[1] = bad_p1

        with self.assertRaises(RecordParsingError):
            self.parser.parse_lines(lines)

    def test_missing_ticker_or_isin(self) -> None:
        """Valida que ticker ou ISIN ausentes disparam RecordParsingError."""
        with open(self.valid_fixture_path, "r", encoding="ascii") as f:
            lines = [line.rstrip("\r\n") for line in f]

        # Ticker vazio
        bad_ticker = lines[1][:22] + (" " * 12) + lines[1][34:]
        lines_copy = list(lines)
        lines_copy[1] = bad_ticker
        with self.assertRaises(RecordParsingError):
            self.parser.parse_lines(lines_copy)

        # ISIN vazio
        bad_isin = lines[1][:34] + (" " * 12) + lines[1][46:]
        lines_copy = list(lines)
        lines_copy[1] = bad_isin
        with self.assertRaises(RecordParsingError):
            self.parser.parse_lines(lines_copy)

    def test_parse_with_string_and_io_stream(self) -> None:
        """Valida chamada utilitária parse_imbarq012 com string raw e io.StringIO."""
        with open(self.valid_fixture_path, "r", encoding="ascii") as f:
            content = f.read()

        # Com string raw
        res_str = parse_imbarq012(content)
        self.assertEqual(res_str.total_posicoes, 3)

        # Com io.StringIO
        stream = io.StringIO(content)
        res_io = parse_imbarq012(stream)
        self.assertEqual(res_io.total_posicoes, 3)


if __name__ == "__main__":
    unittest.main()
