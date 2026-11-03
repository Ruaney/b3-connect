"""Testes automatizados para o Pipeline Unificado de Fechamento Diário da B3."""

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.imbarq004 import parse_imbarq004
from src.imbarq006 import parse_imbarq006
from src.imbarq011 import parse_imbarq011
from src.imbarq012 import parse_imbarq012
from src.imbarq014 import parse_imbarq014
from src.pipeline import (
    DailyClosingSummary,
    PipelineConfigurationError,
    PipelineDateMismatchError,
    PipelineParticipantMismatchError,
    PostTradePipeline,
)
from src.reconciliation import BreakType, InternalPosition

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestPostTradePipeline(unittest.TestCase):
    """Suíte de testes para validação e orquestração do pipeline de fechamento diário."""

    def setUp(self) -> None:
        self.fixtures_dir = FIXTURES_DIR
        self.p004 = FIXTURES_DIR / "imbarq004-valid.txt"
        self.p006 = FIXTURES_DIR / "imbarq006-valid.txt"
        self.p011 = FIXTURES_DIR / "imbarq011-valid.txt"
        self.p012 = FIXTURES_DIR / "imbarq012-valid.txt"
        self.p014 = FIXTURES_DIR / "imbarq014-valid.txt"

        # Livro interno com match perfeito em relação ao IMBARQ006:
        # 123456 | PETR4    | 1000 | C
        # 123456 | PETRL350 | 5000 | C
        # 789012 | WDOZ26   | 10   | V
        self.matching_internal_book = [
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

    def test_end_to_end_pipeline_success(self) -> None:
        """Executa ponta a ponta o pipeline com todas as fixtures válidas e valida os totalizadores."""
        pipeline = PostTradePipeline(
            imbarq004=self.p004,
            imbarq006=self.p006,
            imbarq011=self.p011,
            imbarq012=self.p012,
            imbarq014=self.p014,
            internal_positions=self.matching_internal_book,
        )
        summary = pipeline.execute()

        # Validação de Metadados e Cabeçalho Consolidado
        self.assertEqual(summary.data_movimento, date(2026, 11, 3))
        self.assertEqual(summary.codigo_participante, 105)
        self.assertEqual(summary.nome_participante, "CORRETORA ATLAS EXEMPLO S/A")

        # 1. Posições na Câmara (IMBARQ006) & Conciliação
        self.assertEqual(summary.total_posicoes_camara, 3)
        self.assertEqual(summary.total_quantidade_liquida_camara, 6010)
        self.assertEqual(summary.total_valor_camara, Decimal("118250.00"))
        self.assertEqual(summary.status_conciliacao, "CLEAN")
        self.assertTrue(summary.is_clean)
        self.assertTrue(summary.is_conciliado_clean)
        self.assertEqual(summary.total_matches, 3)
        self.assertEqual(summary.total_quebras, 0)
        self.assertEqual(summary.total_posicoes_internas, 3)

        # 2. Saldo Financeiro de Liquidação (IMBARQ004)
        self.assertEqual(summary.total_lancamentos, 4)
        self.assertEqual(summary.total_debitos, Decimal("8645.30"))
        self.assertEqual(summary.total_creditos, Decimal("59750.00"))
        self.assertEqual(summary.saldo_liquido, Decimal("51104.70"))
        self.assertEqual(summary.sinal_saldo_liquido, "C")

        # 3. Empréstimo BTC (IMBARQ011)
        self.assertEqual(summary.total_contratos_btc, 3)
        self.assertEqual(summary.quantidade_total_btc, 40000)
        self.assertEqual(summary.volume_total_btc, Decimal("1112500.00"))

        # 4. Custódia CSD (IMBARQ012)
        self.assertEqual(summary.total_posicoes_csd, 3)
        self.assertEqual(summary.quantidade_total_csd, 6200)
        self.assertEqual(summary.quantidade_livre_csd, 5400)
        self.assertEqual(summary.quantidade_bloqueada_csd, 800)
        self.assertEqual(summary.valor_carteira_csd, Decimal("373900.00"))

        # 5. Colateral e Chamadas de Margem (IMBARQ014)
        self.assertEqual(summary.total_colaterais, 3)
        self.assertEqual(summary.margem_requerida_total, Decimal("440000.00"))
        self.assertEqual(summary.valor_avaliacao_total, Decimal("510000.00"))
        self.assertEqual(summary.margem_livre_total, Decimal("90000.00"))
        self.assertEqual(summary.defasagem_total, Decimal("20000.00"))
        self.assertEqual(summary.total_chamadas_margem, 1)
        self.assertTrue(summary.tem_chamada_margem)

    def test_reconciliation_breaks_detected(self) -> None:
        """Valida que quebras no livro interno são refletidas no sumário e nos relatórios."""
        # Livro interno com divergência: PETR4 com 800 (B3 tem 1000) e sem WDOZ26
        broken_internal_book = [
            InternalPosition(123456, "PETR4", 800, "C"),
            InternalPosition(123456, "PETRL350", 5000, "C"),
        ]

        pipeline = PostTradePipeline(
            imbarq004=self.p004,
            imbarq006=self.p006,
            imbarq011=self.p011,
            imbarq012=self.p012,
            imbarq014=self.p014,
            internal_positions=broken_internal_book,
        )
        summary = pipeline.run()

        self.assertEqual(summary.status_conciliacao, "QUEBRAS")
        self.assertFalse(summary.is_clean)
        self.assertEqual(summary.total_matches, 1)  # PETRL350
        self.assertEqual(summary.total_quebras, 2)  # PETR4 (divergência) + WDOZ26 (fantasma na B3)

        # Verifica detalhamento de quebras no relatório de conciliação
        self.assertIsNotNone(summary.reconciliation_report)
        breaks = summary.reconciliation_report.breaks
        break_types = {b.break_type for b in breaks}
        self.assertIn(BreakType.QUANTITY_MISMATCH, break_types)
        self.assertIn(BreakType.GHOST_IN_B3, break_types)

        # Verifica exportação dict
        summary_dict = summary.to_dict()
        self.assertEqual(summary_dict["status_geral"], "QUEBRAS_CONCILIACAO")
        self.assertEqual(len(summary_dict["posicoes_camara"]["quebras"]), 2)

        # Verifica menção das quebras no Markdown
        report_md = summary.to_markdown_report()
        self.assertIn("QUEBRAS", report_md)
        self.assertIn("QUANTITY_MISMATCH", report_md)
        self.assertIn("GHOST_IN_B3", report_md)

    def test_pipeline_without_internal_positions(self) -> None:
        """Valida fechamento quando não é fornecido livro de custódia interno."""
        pipeline = PostTradePipeline(
            imbarq004=self.p004,
            imbarq006=self.p006,
            imbarq011=self.p011,
            imbarq012=self.p012,
            imbarq014=self.p014,
            internal_positions=None,
        )
        summary = pipeline.execute()

        self.assertEqual(summary.status_conciliacao, "NAO_CONCILIADO")
        self.assertFalse(summary.is_clean)
        self.assertIsNone(summary.reconciliation_report)
        self.assertEqual(summary.total_matches, 0)
        self.assertEqual(summary.total_quebras, 0)

    def test_pipeline_load_from_directory(self) -> None:
        """Testa o carregamento automático dos 5 relatórios diretamente de um diretório."""
        pipeline = PostTradePipeline.from_directory(
            self.fixtures_dir,
            internal_positions=self.matching_internal_book,
        )
        summary = pipeline.execute()

        self.assertEqual(summary.total_posicoes_camara, 3)
        self.assertEqual(summary.total_lancamentos, 4)
        self.assertEqual(summary.total_contratos_btc, 3)
        self.assertEqual(summary.total_posicoes_csd, 3)
        self.assertEqual(summary.total_colaterais, 3)
        self.assertTrue(summary.is_clean)

    def test_structured_export_to_dict(self) -> None:
        """Valida que .to_dict() gera a estrutura exata e serializável."""
        pipeline = PostTradePipeline(
            imbarq004=self.p004,
            imbarq006=self.p006,
            imbarq011=self.p011,
            imbarq012=self.p012,
            imbarq014=self.p014,
            internal_positions=self.matching_internal_book,
        )
        summary = pipeline.execute()
        data = summary.to_dict()

        # Metadados
        self.assertEqual(data["data_movimento"], "2026-11-03")
        self.assertEqual(data["codigo_participante"], 105)
        self.assertEqual(data["nome_participante"], "CORRETORA ATLAS EXEMPLO S/A")

        # Top-level direct shortcuts
        self.assertEqual(data["total_posicoes_camara"], 3)
        self.assertEqual(data["status_conciliacao"], "CLEAN")
        self.assertTrue(data["is_clean"])
        self.assertEqual(data["total_debitos"], 8645.30)
        self.assertEqual(data["total_creditos"], 59750.00)
        self.assertEqual(data["saldo_liquido"], 51104.70)
        self.assertEqual(data["total_contratos_btc"], 3)
        self.assertEqual(data["volume_total_btc"], 1112500.00)
        self.assertEqual(data["quantidade_livre_csd"], 5400)
        self.assertEqual(data["quantidade_bloqueada_csd"], 800)
        self.assertEqual(data["margem_livre_total"], 90000.00)
        self.assertEqual(data["defasagem_total"], 20000.00)

        # Seções estruturadas
        self.assertEqual(data["posicoes_camara"]["layout"], "IMBARQ006")
        self.assertEqual(data["posicoes_camara"]["total_posicoes"], 3)

        self.assertEqual(data["liquidacao_financeira"]["layout"], "IMBARQ004")
        self.assertEqual(data["liquidacao_financeira"]["total_lancamentos"], 4)
        self.assertEqual(data["liquidacao_financeira"]["saldo_liquido"], 51104.70)

        self.assertEqual(data["emprestimo_btc"]["layout"], "IMBARQ011")
        self.assertEqual(data["emprestimo_btc"]["total_contratos"], 3)
        self.assertEqual(data["emprestimo_btc"]["volume_total"], 1112500.00)

        self.assertEqual(data["custodia_depositaria_csd"]["layout"], "IMBARQ012")
        self.assertEqual(data["custodia_depositaria_csd"]["quantidade_livre"], 5400)
        self.assertEqual(data["custodia_depositaria_csd"]["quantidade_bloqueada"], 800)

        self.assertEqual(data["colateral_margem"]["layout"], "IMBARQ014")
        self.assertEqual(data["colateral_margem"]["margem_livre_total"], 90000.00)
        self.assertEqual(data["colateral_margem"]["defasagem_total"], 20000.00)

    def test_structured_export_to_json(self) -> None:
        """Valida que .to_json() exporta string JSON válida e suporta gravação em arquivo."""
        pipeline = PostTradePipeline(
            imbarq004=self.p004,
            imbarq006=self.p006,
            imbarq011=self.p011,
            imbarq012=self.p012,
            imbarq014=self.p014,
            internal_positions=self.matching_internal_book,
        )
        summary = pipeline.execute()

        json_str = summary.to_json(indent=2)
        parsed_json = json.loads(json_str)

        self.assertEqual(parsed_json["data_movimento"], "2026-11-03")
        self.assertEqual(parsed_json["saldo_liquido"], 51104.70)
        self.assertEqual(parsed_json["volume_total_btc"], 1112500.00)

        # Testa salvamento em arquivo
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "closing_summary.json"
            exported = summary.to_json(output_path=out_file)
            self.assertTrue(out_file.exists())
            file_content = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertEqual(file_content["codigo_participante"], 105)

    def test_structured_export_to_markdown_report(self) -> None:
        """Valida que .to_markdown_report() formata o relatório executivo completo."""
        pipeline = PostTradePipeline(
            imbarq004=self.p004,
            imbarq006=self.p006,
            imbarq011=self.p011,
            imbarq012=self.p012,
            imbarq014=self.p014,
            internal_positions=self.matching_internal_book,
        )
        summary = pipeline.execute()

        md = summary.to_markdown_report()

        # Valida seções principais
        self.assertIn("# Fechamento Diário Pós-Negociação B3 — Sumário Executivo", md)
        self.assertIn("CORRETORA ATLAS EXEMPLO S/A", md)
        self.assertIn("1. Posições na Câmara & Conciliação de Custódia (IMBARQ006)", md)
        self.assertIn("2. Liquidação Financeira Multilateral (IMBARQ004)", md)
        self.assertIn("3. Empréstimo de Ativos / BTC (IMBARQ011)", md)
        self.assertIn("4. Custódia na Depositária Central CSD (IMBARQ012)", md)
        self.assertIn("5. Garantias e Chamadas de Margem (IMBARQ014)", md)

        # Valida formatação monetária brasileira
        self.assertIn("R$ 51.104,70", md)
        self.assertIn("R$ 1.112.500,00", md)
        self.assertIn("R$ 90.000,00", md)
        self.assertIn("R$ 20.000,00", md)

        # Valida chamada de margem no relatório
        self.assertIn("Detalhamento das Chamadas de Margem / Defasagens", md)

        # Testa gravação em arquivo
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "closing_report.md"
            summary.to_markdown_report(output_path=out_file)
            self.assertTrue(out_file.exists())
            self.assertIn("IMBARQ006", out_file.read_text(encoding="utf-8"))

    def test_delegated_exports_on_pipeline_instance(self) -> None:
        """Testa que os métodos to_dict, to_json e to_markdown_report funcionam diretamente no pipeline."""
        pipeline = PostTradePipeline(
            imbarq004=self.p004,
            imbarq006=self.p006,
            imbarq011=self.p011,
            imbarq012=self.p012,
            imbarq014=self.p014,
            internal_positions=self.matching_internal_book,
        )

        # Antes de executar deve lançar RuntimeError
        with self.assertRaises(RuntimeError):
            pipeline.to_dict()
        with self.assertRaises(RuntimeError):
            pipeline.to_json()
        with self.assertRaises(RuntimeError):
            pipeline.to_markdown_report()

        # Após execução, delega perfeitamente
        pipeline.execute()
        self.assertIsInstance(pipeline.to_dict(), dict)
        self.assertIn("2026-11-03", pipeline.to_json())
        self.assertIn("Sumário Executivo", pipeline.to_markdown_report())

    def test_date_mismatch_validation(self) -> None:
        """Garante que divergência de data de movimento levanta PipelineDateMismatchError."""
        pipeline = PostTradePipeline(
            imbarq004=self.p004,
            imbarq006=self.p006,
            imbarq011=self.p011,
            imbarq012=self.p012,
            imbarq014=self.p014,
            strict_date_validation=True,
        )
        with self.assertRaises(PipelineDateMismatchError):
            pipeline.execute(data_movimento=date(2026, 11, 4))

    def test_missing_required_reports(self) -> None:
        """Garante que omitir um dos 5 relatórios obrigatórios levanta PipelineConfigurationError."""
        # Omitindo IMBARQ014
        pipeline = PostTradePipeline(
            imbarq004=self.p004,
            imbarq006=self.p006,
            imbarq011=self.p011,
            imbarq012=self.p012,
            imbarq014=None,
        )
        with self.assertRaises(PipelineConfigurationError) as ctx:
            pipeline.execute(require_all_files=True)
        self.assertIn("IMBARQ014", str(ctx.exception))

    def test_partial_execution_when_require_all_false(self) -> None:
        """Permite fechamento parcial quando require_all_files=False."""
        pipeline = PostTradePipeline(
            imbarq004=self.p004,
            imbarq006=self.p006,
            imbarq011=None,
            imbarq012=None,
            imbarq014=None,
        )
        summary = pipeline.execute(require_all_files=False)
        self.assertEqual(summary.total_posicoes_camara, 3)
        self.assertEqual(summary.total_lancamentos, 4)
        self.assertEqual(summary.total_contratos_btc, 0)
        self.assertEqual(summary.total_posicoes_csd, 0)
        self.assertEqual(summary.total_colaterais, 0)

    def test_incremental_modular_loading(self) -> None:
        """Testa o carregamento modular passo a passo através das APIs do pipeline."""
        pipeline = PostTradePipeline()
        pipeline.load_imbarq004(self.p004)
        pipeline.load_imbarq006(self.p006)
        pipeline.load_imbarq011(self.p011)
        pipeline.load_imbarq012(self.p012)
        pipeline.load_imbarq014(self.p014)
        pipeline.load_internal_positions(
            [
                {"conta": 123456, "ticker": "PETR4", "qtd": 1000, "side": "C"},
                {"conta": 123456, "ticker": "PETRL350", "qtd": 5000, "side": "C"},
                {"conta": 789012, "ticker": "WDOZ26", "qtd": 10, "side": "V"},
            ]
        )

        summary = pipeline.run()
        self.assertTrue(summary.is_clean)
        self.assertEqual(summary.total_matches, 3)


if __name__ == "__main__":
    unittest.main()
