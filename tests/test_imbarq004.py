"""Testes automatizados para o parser do layout IMBARQ004 (Lançamentos Financeiros de Liquidação B3)."""

from datetime import date, time
from decimal import Decimal
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH independentemente da forma de execução
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.imbarq004 import (
    Imbarq004Parser,
    InvalidRecordLengthError,
    InvalidRecordStructureError,
    RecordParsingError,
    TrailerValidationError,
    parse_imbarq004,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestImbarq004Parser(unittest.TestCase):
    """Bateria de testes unitários e de integração do parser IMBARQ004."""

    def setUp(self) -> None:
        self.parser = Imbarq004Parser()
        self.valid_fixture_path = FIXTURES_DIR / "imbarq004-valid.txt"
        self.bad_trailer_path = FIXTURES_DIR / "imbarq004-bad-trailer.txt"
        self.bad_length_path = FIXTURES_DIR / "imbarq004-bad-length.txt"

    def test_parse_valid_fixture(self) -> None:
        """Valida parsing completo do arquivo de lançamentos financeiros sintético válido."""
        res = parse_imbarq004(self.valid_fixture_path)

        # 1. Header (00)
        self.assertEqual(res.header.tipo_registro, "00")
        self.assertEqual(res.header.identificador_layout, "IMBARQ004")
        self.assertEqual(res.header.versao_layout, 1)
        self.assertEqual(res.header.data_geracao, date(2026, 11, 3))
        self.assertEqual(res.header.hora_geracao, time(23, 0, 0))
        self.assertEqual(res.header.data_movimento, date(2026, 11, 3))
        self.assertEqual(res.header.codigo_participante, 105)
        self.assertEqual(res.header.nome_participante, "CORRETORA ATLAS EXEMPLO S/A")
        self.assertEqual(res.header.sequencial_remessa, 1)

        # 2. Detalhes (4 lançamentos: 2 créditos e 2 débitos)
        self.assertEqual(res.total_lancamentos, 4)

        # Lançamento 1: Liquidação de Prêmio de Opções (Crédito)
        d1 = res.detalhes[0]
        self.assertEqual(d1.tipo_registro, "01")
        self.assertEqual(d1.codigo_participante, 105)
        self.assertEqual(d1.codigo_conta_custodia, 123456)
        self.assertEqual(d1.numero_lancamento, 1)
        self.assertEqual(d1.data_liquidacao, date(2026, 11, 4))
        self.assertEqual(d1.codigo_historico, 101)
        self.assertEqual(d1.descricao_historico, "LIQ PREMIO OPCOES CREDITO")
        self.assertEqual(d1.natureza_lancamento, "C")
        self.assertEqual(d1.valor_lancamento, Decimal("21250.00"))
        self.assertEqual(d1.codigo_negociacao, "PETRL350")
        self.assertEqual(d1.codigo_isin, "BRPETRL35001")
        self.assertEqual(d1.tipo_mercado, "OPC")
        self.assertEqual(d1.participante_contraparte, 1)
        self.assertEqual(d1.numero_documento, "NOTA-OPC-0001001")
        self.assertEqual(d1.canal_liquidacao, "CAM")
        self.assertEqual(d1.status_liquidacao, "L")

        # Lançamento 2: Liquidação de Prêmio de Opções (Débito)
        d2 = res.detalhes[1]
        self.assertEqual(d2.tipo_registro, "01")
        self.assertEqual(d2.codigo_participante, 105)
        self.assertEqual(d2.codigo_conta_custodia, 123456)
        self.assertEqual(d2.numero_lancamento, 2)
        self.assertEqual(d2.data_liquidacao, date(2026, 11, 4))
        self.assertEqual(d2.codigo_historico, 101)
        self.assertEqual(d2.descricao_historico, "LIQ PREMIO OPCOES DEBITO")
        self.assertEqual(d2.natureza_lancamento, "D")
        self.assertEqual(d2.valor_lancamento, Decimal("8500.00"))
        self.assertEqual(d2.codigo_negociacao, "VALEA60")
        self.assertEqual(d2.codigo_isin, "BRVALEACNPA3")
        self.assertEqual(d2.tipo_mercado, "OPC")
        self.assertEqual(d2.participante_contraparte, 1)
        self.assertEqual(d2.numero_documento, "NOTA-OPC-0001002")
        self.assertEqual(d2.canal_liquidacao, "CAM")
        self.assertEqual(d2.status_liquidacao, "L")

        # Lançamento 3: Taxa de Registro e Emolumentos (Débito)
        d3 = res.detalhes[2]
        self.assertEqual(d3.tipo_registro, "01")
        self.assertEqual(d3.codigo_participante, 105)
        self.assertEqual(d3.codigo_conta_custodia, 123456)
        self.assertEqual(d3.numero_lancamento, 3)
        self.assertEqual(d3.data_liquidacao, date(2026, 11, 4))
        self.assertEqual(d3.codigo_historico, 201)
        self.assertEqual(d3.descricao_historico, "TAXA DE REGISTRO B3")
        self.assertEqual(d3.natureza_lancamento, "D")
        self.assertEqual(d3.valor_lancamento, Decimal("145.30"))
        self.assertIsNone(d3.codigo_negociacao)
        self.assertIsNone(d3.codigo_isin)
        self.assertIsNone(d3.tipo_mercado)
        self.assertEqual(d3.participante_contraparte, 1)
        self.assertEqual(d3.numero_documento, "TAXA-REG-0005432")
        self.assertEqual(d3.canal_liquidacao, "CAM")
        self.assertEqual(d3.status_liquidacao, "L")

        # Lançamento 4: Liquidação Financeira Mercado à Vista (Crédito)
        d4 = res.detalhes[3]
        self.assertEqual(d4.tipo_registro, "01")
        self.assertEqual(d4.codigo_participante, 105)
        self.assertEqual(d4.codigo_conta_custodia, 789012)
        self.assertEqual(d4.numero_lancamento, 4)
        self.assertEqual(d4.data_liquidacao, date(2026, 11, 5))
        self.assertEqual(d4.codigo_historico, 401)
        self.assertEqual(d4.descricao_historico, "LIQ FINANC MERCADO A VISTA")
        self.assertEqual(d4.natureza_lancamento, "C")
        self.assertEqual(d4.valor_lancamento, Decimal("38500.00"))
        self.assertEqual(d4.codigo_negociacao, "PETR4")
        self.assertEqual(d4.codigo_isin, "BRPETRACNPR6")
        self.assertEqual(d4.tipo_mercado, "VIS")
        self.assertEqual(d4.participante_contraparte, 1)
        self.assertEqual(d4.numero_documento, "NOTA-VIS-0009876")
        self.assertEqual(d4.canal_liquidacao, "CAM")
        self.assertEqual(d4.status_liquidacao, "L")

        # 3. Propriedades Agregadas do Arquivo
        self.assertEqual(res.total_debitos, Decimal("8645.30"))
        self.assertEqual(res.total_creditos, Decimal("59750.00"))
        self.assertEqual(res.saldo_liquido, Decimal("51104.70"))

        # 4. Trailler (99)
        self.assertEqual(res.trailer.tipo_registro, "99")
        self.assertEqual(res.trailer.identificador_layout, "IMBARQ004")
        self.assertEqual(res.trailer.total_registros_arquivo, 6)
        self.assertEqual(res.trailer.total_registros_detalhe, 4)
        self.assertEqual(res.trailer.total_valor_debito, Decimal("8645.30"))
        self.assertEqual(res.trailer.total_valor_credito, Decimal("59750.00"))
        self.assertEqual(res.trailer.sinal_saldo_liquido, "C")
        self.assertEqual(res.trailer.total_saldo_liquido, Decimal("51104.70"))

    def test_rejection_bad_trailer(self) -> None:
        """Valida que arquivo com trailler adulterado ou corrompido é rejeitado estritamente."""
        with self.assertRaises(TrailerValidationError) as ctx:
            parse_imbarq004(self.bad_trailer_path)

        err_msg = str(ctx.exception)
        self.assertIn("Total de registros de detalhe diverge", err_msg)
        self.assertIn("Somatório de valor a débito diverge", err_msg)
        self.assertIn("Somatório de valor a crédito diverge", err_msg)
        self.assertIn("Sinal do saldo líquido diverge", err_msg)
        self.assertIn("Somatório do saldo líquido absoluto diverge", err_msg)

    def test_rejection_bad_length(self) -> None:
        """Valida que linha com tamanho diferente de 200 colunas causa InvalidRecordLengthError."""
        with self.assertRaises(InvalidRecordLengthError) as ctx:
            parse_imbarq004(self.bad_length_path)

        self.assertEqual(ctx.exception.line_number, 3)
        self.assertEqual(ctx.exception.expected_len, 200)
        self.assertEqual(ctx.exception.actual_len, 150)

    def test_empty_file_rejection(self) -> None:
        """Valida que arquivo vazio ou com linhas insuficientes é rejeitado."""
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([])

        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines(["00" + " " * 198])

    def test_empty_financial_file_valid(self) -> None:
        """Valida caso de borda: arquivo sem lançamentos no dia (Header + Trailler zerado)."""
        h = "00" + "IMBARQ004" + "001" + "20261103" + "230000" + "20261103" + "00000105" + "CORRETORA ATLAS EXEMPLO S/A   " + "000001" + (" " * 120)
        # 2 registros no arquivo, 0 detalhes, 0.00 debito, 0.00 credito, C, 0.00 saldo liquido
        t = "99" + "IMBARQ004" + "00000002" + "00000000" + "000000000000000000" + "000000000000000000" + "C" + "000000000000000000" + (" " * 118)
        self.assertEqual(len(h), 200)
        self.assertEqual(len(t), 200)

        res = self.parser.parse_lines([h, t])
        self.assertEqual(res.total_lancamentos, 0)
        self.assertEqual(res.total_debitos, Decimal("0.00"))
        self.assertEqual(res.total_creditos, Decimal("0.00"))
        self.assertEqual(res.saldo_liquido, Decimal("0.00"))
        self.assertEqual(res.trailer.total_registros_arquivo, 2)
        self.assertEqual(res.trailer.sinal_saldo_liquido, "C")

    def test_debit_balance_trailer(self) -> None:
        """Valida arquivo cujo saldo líquido é Devedor ('D')."""
        h = "00" + "IMBARQ004" + "001" + "20261103" + "230000" + "20261103" + "00000105" + "CORRETORA ATLAS EXEMPLO S/A   " + "000001" + (" " * 120)
        # Lançamento a débito de R$ 10.000,00
        d = "01000001050000001234560000000001202611040101LIQ PREMIO OPCOES DEBITO      D00000000001000000VALEA60     BRVALEACNPA3OPC00000001NOTA-OPC-0001002CAM L" + (" " * 52)
        # 3 registros no arquivo, 1 detalhe, 10000.00 débito, 0.00 crédito, sinal D, 10000.00 líquido
        t = "99" + "IMBARQ004" + "00000003" + "00000001" + "000000000001000000" + "000000000000000000" + "D" + "000000000001000000" + (" " * 118)

        self.assertEqual(len(h), 200)
        self.assertEqual(len(d), 200)
        self.assertEqual(len(t), 200)

        res = self.parser.parse_lines([h, d, t])
        self.assertEqual(res.total_lancamentos, 1)
        self.assertEqual(res.total_debitos, Decimal("10000.00"))
        self.assertEqual(res.total_creditos, Decimal("0.00"))
        self.assertEqual(res.saldo_liquido, Decimal("-10000.00"))
        self.assertEqual(res.trailer.sinal_saldo_liquido, "D")
        self.assertEqual(res.trailer.total_saldo_liquido, Decimal("10000.00"))

    def test_invalid_layout_id(self) -> None:
        """Valida que Header com identificador de layout diferente é rejeitado."""
        h_bad = "00" + "OUTRO0000" + "001" + "20261103" + "230000" + "20261103" + "00000105" + "CORRETORA ATLAS EXEMPLO S/A   " + "000001" + (" " * 120)
        t = "99" + "IMBARQ004" + "00000002" + "00000000" + "000000000000000000" + "000000000000000000" + "C" + "000000000000000000" + (" " * 118)
        with self.assertRaises(InvalidRecordStructureError):
            self.parser.parse_lines([h_bad, t])

    def test_invalid_fields_in_detail(self) -> None:
        """Valida detecção de dados mal formatados ou inválidos em registros de detalhe."""
        h = "00" + "IMBARQ004" + "001" + "20261103" + "230000" + "20261103" + "00000105" + "CORRETORA ATLAS EXEMPLO S/A   " + "000001" + (" " * 120)

        # Natureza inválida ('X' ao invés de 'C' ou 'D')
        d_bad_nat = "01000001050000001234560000000001202611040101LIQ PREMIO OPCOES CREDITO     X00000000002125000PETRL350    BRPETRL35001OPC00000001NOTA-OPC-0001001CAM L" + (" " * 52)
        with self.assertRaises(RecordParsingError) as ctx:
            self.parser.parse_line_detail(d_bad_nat, line_no=2)
        self.assertIn("natureza_lancamento", str(ctx.exception))

        # Status inválido ('Z' ao invés de 'L', 'P' ou 'C')
        d_bad_st = "01000001050000001234560000000001202611040101LIQ PREMIO OPCOES CREDITO     C00000000002125000PETRL350    BRPETRL35001OPC00000001NOTA-OPC-0001001CAM Z" + (" " * 52)
        with self.assertRaises(RecordParsingError) as ctx:
            self.parser.parse_line_detail(d_bad_st, line_no=2)
        self.assertIn("status_liquidacao", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
