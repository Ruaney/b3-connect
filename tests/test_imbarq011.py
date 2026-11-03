"""Testes automatizados para o parser do layout IMBARQ011 (Empréstimo de Ativos / BTC B3)."""

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

from src.imbarq011 import (
    Imbarq011Parser,
    InvalidRecordLengthError,
    InvalidRecordStructureError,
    RecordParsingError,
    TrailerValidationError,
    parse_imbarq011,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestImbarq011Parser(unittest.TestCase):
    """Bateria de testes unitários e de integração do parser IMBARQ011."""

    def setUp(self) -> None:
        self.parser = Imbarq011Parser()
        self.valid_fixture_path = FIXTURES_DIR / "imbarq011-valid.txt"
        self.bad_trailer_path = FIXTURES_DIR / "imbarq011-bad-trailer.txt"

    def test_parse_valid_fixture(self) -> None:
        """Valida parsing completo do arquivo de empréstimo de ativos sintético válido."""
        res = parse_imbarq011(self.valid_fixture_path)

        # 1. Header
        self.assertEqual(res.header.tipo_registro, "00")
        self.assertEqual(res.header.identificador_layout, "IMBARQ011")
        self.assertEqual(res.header.versao_layout, 1)
        self.assertEqual(res.header.data_geracao, date(2026, 11, 3))
        self.assertEqual(res.header.hora_geracao, time(23, 0, 0))
        self.assertEqual(res.header.data_movimento, date(2026, 11, 3))
        self.assertEqual(res.header.codigo_participante, 105)
        self.assertEqual(res.header.nome_participante, "CORRETORA ATLAS EXEMPLO S/A")
        self.assertEqual(res.header.sequencial_remessa, 1)

        # 2. Contratos (3 contratos)
        self.assertEqual(res.total_contratos, 3)

        # Contrato 1: VALE3
        c1 = res.contratos[0]
        self.assertEqual(c1.numero_contrato, 20260001)
        self.assertEqual(c1.codigo_participante_doador, 105)
        self.assertEqual(c1.conta_doador, 123456)
        self.assertEqual(c1.codigo_participante_tomador, 210)
        self.assertEqual(c1.conta_tomador, 987654)
        self.assertEqual(c1.codigo_negociacao, "VALE3")
        self.assertEqual(c1.codigo_isin, "BRVALEACNOR0")
        self.assertEqual(c1.quantidade_alugada, 10000)
        self.assertEqual(c1.taxa_aluguel, Decimal("2.5000"))
        self.assertEqual(c1.data_abertura, date(2026, 10, 15))
        self.assertEqual(c1.data_vencimento, date(2026, 11, 15))
        self.assertEqual(c1.tipo_reversibilidade, "T")
        self.assertEqual(c1.preco_base, Decimal("62.50"))
        self.assertEqual(c1.valor_contrato, Decimal("625000.00"))

        # Contrato 2: B3SA3
        c2 = res.contratos[1]
        self.assertEqual(c2.numero_contrato, 20260002)
        self.assertEqual(c2.codigo_participante_doador, 105)
        self.assertEqual(c2.conta_doador, 123456)
        self.assertEqual(c2.codigo_participante_tomador, 88)
        self.assertEqual(c2.conta_tomador, 555123)
        self.assertEqual(c2.codigo_negociacao, "B3SA3")
        self.assertEqual(c2.codigo_isin, "BRB3SAACNOR6")
        self.assertEqual(c2.quantidade_alugada, 25000)
        self.assertEqual(c2.taxa_aluguel, Decimal("1.7500"))
        self.assertEqual(c2.data_abertura, date(2026, 10, 20))
        self.assertEqual(c2.data_vencimento, date(2026, 12, 20))
        self.assertEqual(c2.tipo_reversibilidade, "D")
        self.assertEqual(c2.preco_base, Decimal("11.80"))
        self.assertEqual(c2.valor_contrato, Decimal("295000.00"))

        # Contrato 3: PETR4
        c3 = res.contratos[2]
        self.assertEqual(c3.numero_contrato, 20260003)
        self.assertEqual(c3.codigo_participante_doador, 300)
        self.assertEqual(c3.conta_doador, 777888)
        self.assertEqual(c3.codigo_participante_tomador, 105)
        self.assertEqual(c3.conta_tomador, 333222)
        self.assertEqual(c3.codigo_negociacao, "PETR4")
        self.assertEqual(c3.codigo_isin, "BRPETRACNPR6")
        self.assertEqual(c3.quantidade_alugada, 5000)
        self.assertEqual(c3.taxa_aluguel, Decimal("4.1200"))
        self.assertEqual(c3.data_abertura, date(2026, 11, 1))
        self.assertEqual(c3.data_vencimento, date(2027, 1, 15))
        self.assertEqual(c3.tipo_reversibilidade, "A")
        self.assertEqual(c3.preco_base, Decimal("38.50"))
        self.assertEqual(c3.valor_contrato, Decimal("192500.00"))

        # 3. Trailler
        self.assertEqual(res.trailer.tipo_registro, "99")
        self.assertEqual(res.trailer.identificador_layout, "IMBARQ011")
        self.assertEqual(res.trailer.total_registros_arquivo, 5)
        self.assertEqual(res.trailer.total_registros_detalhe, 3)
        self.assertEqual(res.trailer.total_quantidade_alugada, 40000)
        self.assertEqual(res.trailer.total_valor_contratos, Decimal("1112500.00"))

    def test_rejection_bad_trailer(self) -> None:
        """Valida que arquivo com trailler adulterado ou corrompido é rejeitado estritamente."""
        with self.assertRaises(TrailerValidationError) as ctx:
            parse_imbarq011(self.bad_trailer_path)

        err_msg = str(ctx.exception)
        self.assertIn("Somatório de quantidade alugada diverge", err_msg)
        self.assertIn("99999", err_msg)
        self.assertIn("40000", err_msg)

    def test_rejection_bad_record_length(self) -> None:
        """Valida que registros com tamanho diferente de 200 colunas disparam erro imediato."""
        short_line = "00IMBARQ011001" + " " * 50
        with self.assertRaises(InvalidRecordLengthError) as ctx:
            self.parser.parse_lines([short_line])
        self.assertEqual(ctx.exception.expected_len, 200)
        self.assertEqual(ctx.exception.line_number, 1)

    def test_rejection_invalid_structure(self) -> None:
        """Valida detecção de arquivos truncados ou fora da ordem hierárquica."""
        # Arquivo com apenas 1 linha
        valid_header = self.valid_fixture_path.read_text().splitlines()[0]
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([valid_header])

        # Header incorreto
        lines = self.valid_fixture_path.read_text().splitlines()
        corrupted_header = "01" + lines[0][2:]
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([corrupted_header] + lines[1:])

        # Layout ID errado no Header
        wrong_id_header = lines[0][:2] + "IMBARQ999" + lines[0][11:]
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([wrong_id_header] + lines[1:])

    def test_rejection_invalid_field_values(self) -> None:
        """Valida rejeição de campos com violações de domínio de negócio."""
        lines = self.valid_fixture_path.read_text().splitlines()

        # Data de vencimento anterior à data de abertura
        # Data abertura: 20261015, vencimento alterado para 20261010
        corrupted_detail = lines[1][:121] + "20261010" + lines[1][129:]
        with self.assertRaises(RecordParsingError) as ctx:
            self.parser.parse_lines([lines[0], corrupted_detail, lines[4]])
        self.assertIn("anterior à data de abertura", str(ctx.exception))

        # Tipo de reversibilidade inválido
        corrupted_rev = lines[1][:129] + "X" + lines[1][130:]
        with self.assertRaises(RecordParsingError) as ctx:
            self.parser.parse_lines([lines[0], corrupted_rev, lines[4]])
        self.assertIn("Tipo de reversibilidade inválido", str(ctx.exception))

    def test_in_memory_and_buffer_parsing(self) -> None:
        """Valida parsing a partir de buffer StringIO e texto em memória."""
        raw_text = self.valid_fixture_path.read_text()

        # StringIO
        buf = io.StringIO(raw_text)
        res_buf = parse_imbarq011(buf)
        self.assertEqual(res_buf.total_contratos, 3)

        # String pura
        res_str = parse_imbarq011(raw_text)
        self.assertEqual(res_str.total_contratos, 3)


if __name__ == "__main__":
    unittest.main()
