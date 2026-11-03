"""Testes automatizados para o parser do layout IMBARQ014 (Garantias e Margens B3)."""

from datetime import date, time
from decimal import Decimal
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH independentemente da forma de execução
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.imbarq014 import (
    Imbarq014Parser,
    InvalidRecordLengthError,
    InvalidRecordStructureError,
    RecordParsingError,
    TrailerValidationError,
    parse_imbarq014,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestImbarq014Parser(unittest.TestCase):
    """Bateria de testes unitários e de integração do parser IMBARQ014."""

    def setUp(self) -> None:
        self.parser = Imbarq014Parser()
        self.valid_fixture_path = FIXTURES_DIR / "imbarq014-valid.txt"
        self.bad_trailer_path = FIXTURES_DIR / "imbarq014-bad-trailer.txt"
        self.bad_length_path = FIXTURES_DIR / "imbarq014-bad-length.txt"

    def test_parse_valid_fixture(self) -> None:
        """Valida parsing completo do arquivo de garantias e chamadas de margem válido."""
        res = parse_imbarq014(self.valid_fixture_path)

        # 1. Header (00)
        self.assertEqual(res.header.tipo_registro, "00")
        self.assertEqual(res.header.identificador_layout, "IMBARQ014")
        self.assertEqual(res.header.versao_layout, 1)
        self.assertEqual(res.header.data_geracao, date(2026, 11, 3))
        self.assertEqual(res.header.hora_geracao, time(23, 0, 0))
        self.assertEqual(res.header.data_movimento, date(2026, 11, 3))
        self.assertEqual(res.header.codigo_participante, 105)
        self.assertEqual(res.header.nome_participante, "CORRETORA ATLAS EXEMPLO S/A")
        self.assertEqual(res.header.sequencial_remessa, 1)

        # 2. Detalhes (3 colaterais: Moeda, Título Público, Ações)
        self.assertEqual(res.total_colaterais, 3)

        # Detalhe 1: Moeda (MOE / BRL)
        d1 = res.detalhes[0]
        self.assertEqual(d1.tipo_registro, "01")
        self.assertEqual(d1.codigo_participante, 105)
        self.assertEqual(d1.codigo_conta_custodia, 123456)
        self.assertEqual(d1.tipo_colateral, "MOE")
        self.assertEqual(d1.identificador_ativo, "BRL")
        self.assertEqual(d1.codigo_isin, "BRBRLCTF0001")
        self.assertEqual(d1.finalidade_garantia, "AMP")
        self.assertEqual(d1.quantidade_depositada, 150000)
        self.assertEqual(d1.preco_avaliacao_unitario, Decimal("1.00"))
        self.assertEqual(d1.valor_avaliacao, Decimal("150000.00"))
        self.assertEqual(d1.margem_requerida, Decimal("100000.00"))
        self.assertEqual(d1.situacao_margem, "L")
        self.assertTrue(d1.is_margem_livre)
        self.assertFalse(d1.is_chamada_margem)
        self.assertEqual(d1.valor_margem_livre_defasagem, Decimal("50000.00"))
        self.assertEqual(d1.participante_liquidacao, 105)

        # Detalhe 2: Título Público Federal (TPU / LFT2027)
        d2 = res.detalhes[1]
        self.assertEqual(d2.tipo_registro, "01")
        self.assertEqual(d2.codigo_participante, 105)
        self.assertEqual(d2.codigo_conta_custodia, 123456)
        self.assertEqual(d2.tipo_colateral, "TPU")
        self.assertEqual(d2.identificador_ativo, "LFT2027")
        self.assertEqual(d2.codigo_isin, "BRSTNCLF1001")
        self.assertEqual(d2.finalidade_garantia, "AMP")
        self.assertEqual(d2.quantidade_depositada, 20)
        self.assertEqual(d2.preco_avaliacao_unitario, Decimal("14500.00"))
        self.assertEqual(d2.valor_avaliacao, Decimal("290000.00"))
        self.assertEqual(d2.margem_requerida, Decimal("250000.00"))
        self.assertEqual(d2.situacao_margem, "L")
        self.assertTrue(d2.is_margem_livre)
        self.assertFalse(d2.is_chamada_margem)
        self.assertEqual(d2.valor_margem_livre_defasagem, Decimal("40000.00"))
        self.assertEqual(d2.participante_liquidacao, 105)

        # Detalhe 3: Ações (ACO / PETR4) com chamada de margem
        d3 = res.detalhes[2]
        self.assertEqual(d3.tipo_registro, "01")
        self.assertEqual(d3.codigo_participante, 105)
        self.assertEqual(d3.codigo_conta_custodia, 789012)
        self.assertEqual(d3.tipo_colateral, "ACO")
        self.assertEqual(d3.identificador_ativo, "PETR4")
        self.assertEqual(d3.codigo_isin, "BRPETRACNPR6")
        self.assertEqual(d3.finalidade_garantia, "AMP")
        self.assertEqual(d3.quantidade_depositada, 2000)
        self.assertEqual(d3.preco_avaliacao_unitario, Decimal("35.00"))
        self.assertEqual(d3.valor_avaliacao, Decimal("70000.00"))
        self.assertEqual(d3.margem_requerida, Decimal("90000.00"))
        self.assertEqual(d3.situacao_margem, "D")
        self.assertFalse(d3.is_margem_livre)
        self.assertTrue(d3.is_chamada_margem)
        self.assertEqual(d3.valor_margem_livre_defasagem, Decimal("20000.00"))
        self.assertEqual(d3.participante_liquidacao, 105)

        # 3. Trailler (99)
        self.assertEqual(res.trailer.tipo_registro, "99")
        self.assertEqual(res.trailer.identificador_layout, "IMBARQ014")
        self.assertEqual(res.trailer.total_registros_arquivo, 5)
        self.assertEqual(res.trailer.total_registros_detalhe, 3)
        self.assertEqual(res.trailer.total_quantidade_depositada, 152020)
        self.assertEqual(res.trailer.total_valor_avaliacao, Decimal("510000.00"))
        self.assertEqual(res.trailer.total_margem_requerida, Decimal("440000.00"))
        self.assertEqual(res.trailer.total_margem_livre_defasagem, Decimal("110000.00"))

        # 4. Métodos Auxiliares de Consulta e Negócio
        chamadas = res.get_chamadas_margem()
        self.assertEqual(len(chamadas), 1)
        self.assertEqual(chamadas[0].identificador_ativo, "PETR4")
        self.assertEqual(chamadas[0].valor_margem_livre_defasagem, Decimal("20000.00"))

        livres = res.get_margens_livres()
        self.assertEqual(len(livres), 2)

        garantias_moe = res.get_garantias_por_tipo("MOE")
        self.assertEqual(len(garantias_moe), 1)
        self.assertEqual(garantias_moe[0].identificador_ativo, "BRL")

        totais_tipo = res.total_avaliacao_por_tipo()
        self.assertEqual(totais_tipo["MOE"], Decimal("150000.00"))
        self.assertEqual(totais_tipo["TPU"], Decimal("290000.00"))
        self.assertEqual(totais_tipo["ACO"], Decimal("70000.00"))

        self.assertEqual(res.total_defasagem(), Decimal("20000.00"))

    def test_rejection_bad_trailer(self) -> None:
        """Valida que arquivo com trailler adulterado ou corrompido é rejeitado estritamente."""
        with self.assertRaises(TrailerValidationError) as ctx:
            parse_imbarq014(self.bad_trailer_path)

        err_msg = str(ctx.exception)
        self.assertIn("Total de registros de detalhe diverge", err_msg)
        self.assertIn("Somatório de quantidade depositada diverge", err_msg)
        self.assertIn("Somatório de valor de avaliação diverge", err_msg)
        self.assertIn("Somatório de margem requerida diverge", err_msg)
        self.assertIn("Somatório de margem livre/defasagem diverge", err_msg)

    def test_rejection_bad_length(self) -> None:
        """Valida que linha com tamanho diferente de 200 colunas causa InvalidRecordLengthError."""
        with self.assertRaises(InvalidRecordLengthError) as ctx:
            parse_imbarq014(self.bad_length_path)

        self.assertEqual(ctx.exception.line_number, 3)
        self.assertEqual(ctx.exception.expected_len, 200)
        self.assertEqual(ctx.exception.actual_len, 150)

    def test_empty_file_rejection(self) -> None:
        """Valida que arquivo vazio ou com linhas insuficientes é rejeitado."""
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([])

        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines(["00" + " " * 198])

    def test_empty_collaterals_file_valid(self) -> None:
        """Valida caso de borda: arquivo sem colaterais (Header + Trailler zerado)."""
        h = (
            "00"
            + "IMBARQ014"
            + "001"
            + "20261103"
            + "230000"
            + "20261103"
            + "00000105"
            + "CORRETORA ATLAS EXEMPLO S/A   "
            + "000001"
            + (" " * 120)
        )
        # 2 registros no arquivo, 0 detalhes, 0 quantidade, 0.00 valores
        t = (
            "99"
            + "IMBARQ014"
            + "00000002"
            + "00000000"
            + "000000000000000000"
            + "000000000000000000"
            + "000000000000000000"
            + "000000000000000000"
            + (" " * 101)
        )
        self.assertEqual(len(h), 200)
        self.assertEqual(len(t), 200)

        res = self.parser.parse_lines([h, t])
        self.assertEqual(res.total_colaterais, 0)
        self.assertEqual(res.trailer.total_registros_arquivo, 2)
        self.assertEqual(res.trailer.total_quantidade_depositada, 0)
        self.assertEqual(res.trailer.total_valor_avaliacao, Decimal("0.00"))
        self.assertEqual(res.trailer.total_margem_requerida, Decimal("0.00"))
        self.assertEqual(res.trailer.total_margem_livre_defasagem, Decimal("0.00"))

    def test_invalid_layout_id(self) -> None:
        """Valida que Header com identificador de layout diferente é rejeitado."""
        h_bad = (
            "00"
            + "OUTRO0000"
            + "001"
            + "20261103"
            + "230000"
            + "20261103"
            + "00000105"
            + "CORRETORA ATLAS EXEMPLO S/A   "
            + "000001"
            + (" " * 120)
        )
        t = (
            "99"
            + "IMBARQ014"
            + "00000002"
            + "00000000"
            + "000000000000000000"
            + "000000000000000000"
            + "000000000000000000"
            + "000000000000000000"
            + (" " * 101)
        )
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([h_bad, t])

    def test_invalid_number_in_detail(self) -> None:
        """Valida que caracteres não numéricos em campos numéricos disparam RecordParsingError."""
        with open(self.valid_fixture_path, "r", encoding="ascii") as f:
            lines = [line.rstrip("\r\n") for line in f]

        # Altera quantidade depositada para letras
        bad_d1 = lines[1][:52] + "ABCDEFGHIJKLMNO" + lines[1][67:]
        lines[1] = bad_d1
        with self.assertRaises(RecordParsingError):
            self.parser.parse_lines(lines)

    def test_invalid_collateral_type(self) -> None:
        """Valida que tipo de colateral não reconhecido dispara RecordParsingError."""
        with open(self.valid_fixture_path, "r", encoding="ascii") as f:
            lines = [line.rstrip("\r\n") for line in f]

        bad_d1 = lines[1][:22] + "XYZ" + lines[1][25:]
        lines[1] = bad_d1
        with self.assertRaises(RecordParsingError) as ctx:
            self.parser.parse_lines(lines)
        self.assertIn("tipo_colateral", str(ctx.exception))

    def test_invalid_margin_status(self) -> None:
        """Valida que situação de margem inválida dispara RecordParsingError."""
        with open(self.valid_fixture_path, "r", encoding="ascii") as f:
            lines = [line.rstrip("\r\n") for line in f]

        bad_d1 = lines[1][:116] + "X" + lines[1][117:]
        lines[1] = bad_d1
        with self.assertRaises(RecordParsingError) as ctx:
            self.parser.parse_lines(lines)
        self.assertIn("situacao_margem", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
