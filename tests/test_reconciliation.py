"""Testes automatizados para o motor de conciliação de custódia (IMBARQ006 vs Livro Interno)."""

from datetime import date
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.imbarq006 import parse_imbarq006
from src.reconciliation import (
    BreakType,
    InternalPosition,
    PositionReconciler,
    ReconciliationBreak,
    ReconciliationReport,
    reconcile_positions,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestPositionReconciliation(unittest.TestCase):
    """Bateria de testes unitários do motor de conciliação."""

    def setUp(self) -> None:
        self.valid_fixture_path = FIXTURES_DIR / "imbarq006-valid.txt"
        self.imbarq_file = parse_imbarq006(self.valid_fixture_path)

        # Base idêntica à fixture sintética imbarq006-valid.txt:
        # 1: PETR4, conta 123456, qtd 1000, sentido C
        # 2: PETRL350, conta 123456, qtd 5000, sentido C
        # 3: WDOZ26, conta 789012, qtd 10, sentido V
        self.identical_internal_positions = [
            InternalPosition(
                codigo_conta_custodia=123456,
                codigo_negociacao="PETR4",
                quantidade_liquida=1000,
                sentido_posicao="C",
            ),
            InternalPosition(
                codigo_conta_custodia=123456,
                codigo_negociacao="PETRL350",
                quantidade_liquida=5000,
                sentido_posicao="C",
            ),
            InternalPosition(
                codigo_conta_custodia=789012,
                codigo_negociacao="WDOZ26",
                quantidade_liquida=10,
                sentido_posicao="V",
            ),
        ]

    def test_reconciliation_perfect_match(self) -> None:
        """1. Valida cenário de conciliação 100% batida sem nenhuma quebra."""
        report = reconcile_positions(
            b3_source=self.imbarq_file,
            internal_source=self.identical_internal_positions,
        )

        self.assertTrue(report.is_clean)
        self.assertEqual(report.total_posicoes_b3, 3)
        self.assertEqual(report.total_posicoes_internas, 3)
        self.assertEqual(report.total_matches, 3)
        self.assertEqual(report.total_breaks, 0)
        self.assertEqual(len(report.quantity_mismatches), 0)
        self.assertEqual(len(report.ghost_positions), 0)
        self.assertEqual(len(report.missing_positions), 0)
        self.assertEqual(report.data_movimento, date(2026, 11, 3))

        # Confere detalhes dos matches
        matched_tickers = {m.codigo_negociacao: m.quantidade_liquida for m in report.matches}
        self.assertEqual(matched_tickers["PETR4"], 1000)
        self.assertEqual(matched_tickers["PETRL350"], 5000)
        self.assertEqual(matched_tickers["WDOZ26"], 10)

    def test_reconciliation_quantity_mismatch(self) -> None:
        """2. Valida detecção de quebra por divergência de quantidade."""
        # Altera quantidade de PETR4 no livro interno de 1000 para 850
        divergent_internal = [
            InternalPosition(
                codigo_conta_custodia=123456,
                codigo_negociacao="PETR4",
                quantidade_liquida=850,
                sentido_posicao="C",
            ),
            self.identical_internal_positions[1],  # PETRL350 - 5000
            self.identical_internal_positions[2],  # WDOZ26 - 10
        ]

        report = reconcile_positions(
            b3_source=self.valid_fixture_path,  # Testa passando caminho do arquivo diretamente
            internal_source=divergent_internal,
        )

        self.assertFalse(report.is_clean)
        self.assertEqual(report.total_matches, 2)
        self.assertEqual(report.total_breaks, 1)

        breaks = report.quantity_mismatches
        self.assertEqual(len(breaks), 1)

        b = breaks[0]
        self.assertEqual(b.break_type, BreakType.QUANTITY_MISMATCH)
        self.assertEqual(b.codigo_conta_custodia, 123456)
        self.assertEqual(b.codigo_negociacao, "PETR4")
        self.assertEqual(b.quantidade_b3, 1000)
        self.assertEqual(b.quantidade_interna, 850)
        self.assertEqual(b.diferenca, 150)
        self.assertIn("Quebra quantitativa", b.detalhes)

    def test_reconciliation_ghost_and_missing_assets(self) -> None:
        """3. Valida detecção de ativos fantasmas na B3 e ativos faltantes na câmara."""
        # Cenário:
        # - PETR4: Presente em ambos (match de 1000)
        # - WDOZ26: Presente em ambos (match de 10)
        # - PETRL350: Presente na B3 mas omitido do livro interno -> GHOST_IN_B3
        # - VALE3: Presente no livro interno (2500) mas inexistente no IMBARQ006 -> MISSING_IN_B3
        internal_positions = [
            InternalPosition(
                codigo_conta_custodia=123456,
                codigo_negociacao="PETR4",
                quantidade_liquida=1000,
                sentido_posicao="C",
            ),
            InternalPosition(
                codigo_conta_custodia=789012,
                codigo_negociacao="WDOZ26",
                quantidade_liquida=10,
                sentido_posicao="V",
            ),
            InternalPosition(
                codigo_conta_custodia=123456,
                codigo_negociacao="VALE3",
                quantidade_liquida=2500,
                sentido_posicao="C",
            ),
        ]

        report = reconcile_positions(
            b3_source=self.imbarq_file,
            internal_source=internal_positions,
        )

        self.assertFalse(report.is_clean)
        self.assertEqual(report.total_matches, 2)
        self.assertEqual(report.total_breaks, 2)

        # 1 Fantasma (PETRL350)
        ghosts = report.ghost_positions
        self.assertEqual(len(ghosts), 1)
        g = ghosts[0]
        self.assertEqual(g.break_type, BreakType.GHOST_IN_B3)
        self.assertEqual(g.codigo_conta_custodia, 123456)
        self.assertEqual(g.codigo_negociacao, "PETRL350")
        self.assertEqual(g.quantidade_b3, 5000)
        self.assertIsNone(g.quantidade_interna)
        self.assertEqual(g.diferenca, 5000)
        self.assertIn("não encontrada no livro interno", g.detalhes)

        # 1 Faltante (VALE3)
        missings = report.missing_positions
        self.assertEqual(len(missings), 1)
        m = missings[0]
        self.assertEqual(m.break_type, BreakType.MISSING_IN_B3)
        self.assertEqual(m.codigo_conta_custodia, 123456)
        self.assertEqual(m.codigo_negociacao, "VALE3")
        self.assertIsNone(m.quantidade_b3)
        self.assertEqual(m.quantidade_interna, 2500)
        self.assertEqual(m.diferenca, -2500)
        self.assertIn("não reportada pelo IMBARQ006", m.detalhes)

    def test_reconciliation_with_dict_inputs(self) -> None:
        """4. Valida suporte a coleções de dicionários flexíveis no livro interno."""
        dict_payloads = [
            {"conta": 123456, "ticker": "PETR4", "quantidade": 1000, "sentido": "C"},
            {"account": 123456, "symbol": "PETRL350", "qty": 5000, "side": "C"},
            {"codigo_conta_custodia": 789012, "codigo_negociacao": "WDOZ26", "quantidade_liquida": 10, "sentido_posicao": "V"},
        ]

        report = reconcile_positions(
            b3_source=self.imbarq_file,
            internal_source=dict_payloads,
        )

        self.assertTrue(report.is_clean)
        self.assertEqual(report.total_matches, 3)

    def test_reconciliation_signed_short_position(self) -> None:
        """5. Valida que quantidade interna negativa (-10) para posição vendida casa com a B3."""
        signed_internal = [
            InternalPosition(123456, "PETR4", 1000),
            InternalPosition(123456, "PETRL350", 5000),
            InternalPosition(789012, "WDOZ26", -10),  # short position com sinal negativo
        ]

        report = reconcile_positions(
            b3_source=self.imbarq_file,
            internal_source=signed_internal,
            allow_signed_quantities=True,
        )

        self.assertTrue(report.is_clean)
        self.assertEqual(report.total_matches, 3)

    def test_reconciliation_direction_conflict(self) -> None:
        """6. Valida que divergência de sentido (C vs V) com mesma quantidade é reportada como quebra."""
        divergent_side = [
            # Na B3 PETR4 está C (Comprada). Aqui no livro interno forçamos V (Vendida)
            InternalPosition(123456, "PETR4", 1000, sentido_posicao="V"),
            self.identical_internal_positions[1],
            self.identical_internal_positions[2],
        ]

        report = reconcile_positions(
            b3_source=self.imbarq_file,
            internal_source=divergent_side,
        )

        self.assertFalse(report.is_clean)
        self.assertEqual(len(report.quantity_mismatches), 1)
        brk = report.quantity_mismatches[0]
        self.assertIn("Divergência de sentido de posição", brk.detalhes)

    def test_reconciliation_empty_sources(self) -> None:
        """7. Valida casos de borda: fontes vazias."""
        # B3 vazia e livro vazio
        report_empty = reconcile_positions(b3_source=[], internal_source=[])
        self.assertTrue(report_empty.is_clean)
        self.assertEqual(report_empty.total_matches, 0)
        self.assertEqual(report_empty.total_breaks, 0)

        # B3 com posições e livro interno vazio -> todos fantasmas
        report_all_ghosts = reconcile_positions(b3_source=self.imbarq_file, internal_source=[])
        self.assertFalse(report_all_ghosts.is_clean)
        self.assertEqual(len(report_all_ghosts.ghost_positions), 3)

        # B3 vazia e livro interno preenchido -> todos faltantes
        report_all_missing = reconcile_positions(b3_source=[], internal_source=self.identical_internal_positions)
        self.assertFalse(report_all_missing.is_clean)
        self.assertEqual(len(report_all_missing.missing_positions), 3)

    def test_summary_output(self) -> None:
        """8. Valida geração de resumo textual para relatório operacional."""
        # Testa relatório com quebras
        report_breaks = reconcile_positions(
            b3_source=self.imbarq_file,
            internal_source=[InternalPosition(123456, "PETR4", 900)],
        )
        summary_text = report_breaks.summary()

        self.assertIn("RELATÓRIO DE CONCILIAÇÃO DE CUSTÓDIA B3", summary_text)
        self.assertIn("STATUS: COM QUEBRAS DE CONCILIAÇÃO", summary_text)
        self.assertIn("DETALHAMENTO DAS QUEBRAS", summary_text)
        self.assertIn("QUANTITY_MISMATCH", summary_text)
        self.assertIn("GHOST_IN_B3", summary_text)


if __name__ == "__main__":
    unittest.main()
