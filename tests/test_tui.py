"""Suíte de Testes da Interface Terminal de Alta Fidelidade (TUI) e Monitor PUMA.

Valida:
- Renderização sem crash de todos os 5 painéis da TUI
- Validação dos 107 fluxos, 91 leis e 87 manuais
- Mini-livro de ofertas PUMA, spread e leilão MOC
- Barramento Central (LAW-106A Ativos vs Passivos e LAW-106B Waterfall)
- Consulta SNG e SRO
- Simulação de todos os 12 conceitos complexos
- Modo headless / não-interativo e execução CLI (`b3 tui` e `b3 monitor`)
- 100% Python Standard Library.
"""

from __future__ import annotations

import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

# Garante path do repositório
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.cli import build_atlas_parser, main_b3
from src.tui import ANSI_ESCAPE_RE, ANSIFormatter, B3TUIEngine, strip_ansi
from src.b3_concepts_catalog import CONCEPTS


class TestB3TUIEngineRendering(unittest.TestCase):
    """Testes unitários dos painéis de renderização do motor B3TUIEngine."""

    def setUp(self) -> None:
        self.engine = B3TUIEngine(use_color=False)

    def test_engine_initialization(self) -> None:
        """Verifica se o motor carrega corretamente os 107 fluxos, 91 leis e 87 manuais."""
        self.assertEqual(len(self.engine.flows), 107, "Deve conter exatamente 107 fluxos")
        self.assertEqual(len(self.engine.laws_dict), 91, "Deve conter exatamente 91 leis canônicas")
        self.assertEqual(len(self.engine.sources), 87, "Deve conter exatamente 87 fontes regulatórias")
        self.assertEqual(len(CONCEPTS), 12, "Deve conter exatamente 12 conceitos estruturantes")

    def test_render_overview_no_crash(self) -> None:
        """Verifica a renderização do Painel de Visão Geral."""
        output = self.engine.render_overview(width=96)
        self.assertIn("B3 INTEGRATION ATLAS", output)
        self.assertIn("FLUXOS TOTAIS: 107", output)
        self.assertIn("LEIS FORMAIS: 91", output)
        self.assertIn("MANUAIS B3: 87", output)
        self.assertIn("Clearing, CCP & Liquidação Multilateral", output)
        self.assertIn("LAW-001", output)
        self.assertIn("LAW-106A", output)
        self.assertIn("LAW-106B", output)
        self.assertGreater(len(output.splitlines()), 20)

    def test_render_puma_no_crash(self) -> None:
        """Verifica o Monitor PUMA em tempo real com mini-livro e leilão MOC."""
        output = self.engine.render_puma(width=96)
        self.assertIn("MONITOR DE NEGOCIAÇÃO PUMA", output)
        self.assertIn("ATIVO: PETR4", output)
        self.assertIn("BEST BID", output)
        self.assertIn("BEST ASK", output)
        self.assertIn("SPREAD", output)
        self.assertIn("COMPRADOR (BIDS)", output)
        self.assertIn("VENDEDOR (ASKS)", output)
        self.assertIn("LEILÃO DE FECHAMENTO PUMA", output)
        self.assertIn("FREEZE WINDOW", output)
        self.assertIn("Preço Teórico (P_teo)", output)
        self.assertGreater(len(output.splitlines()), 20)

    def test_render_central_bus_no_crash(self) -> None:
        """Verifica o Scoreboard do Barramento Central (LAW-106A e LAW-106B)."""
        output = self.engine.render_central_bus(width=96)
        self.assertIn("BARRAMENTO CENTRAL DE LIQUIDAÇÃO B3", output)
        self.assertIn("EQUILÍBRIO ESTRITO CONFIRMADO", output)
        self.assertIn("LAW-106A", output)
        self.assertIn("ATIVOS DE LIQUIDAÇÃO CCP", output)
        self.assertIn("PASSIVOS E OBRIGAÇÕES", output)
        self.assertIn("TOTAL ATIVOS:", output)
        self.assertIn("TOTAL PASSIVOS:", output)
        self.assertIn("WATERFALL DE SALVAGUARDAS (LAW-106B)", output)
        self.assertIn("1ª CAMADA", output)
        self.assertIn("5ª CAMADA", output)
        self.assertGreater(len(output.splitlines()), 20)

    def test_render_sng_sro_no_crash(self) -> None:
        """Verifica o painel de consulta rápida SNG (Gravames) e SRO (Seguros SUSEP)."""
        output = self.engine.render_sng_sro(width=96)
        self.assertIn("REGISTRADORES NACIONAIS — SNG & SRO", output)
        self.assertIn("SNG — SISTEMA NACIONAL DE GRAVAMES", output)
        self.assertIn("9BWZZZ377VT004251", output)
        self.assertIn("B3X2026", output)
        self.assertIn("ALIENACAO_FIDUCIARIA", output)
        self.assertIn("SRO — SISTEMA DE REGISTRO DE OPERAÇÕES", output)
        self.assertIn("SUSEP", output)
        self.assertIn("LIMITE MÁXIMO GARANTIA", output)
        self.assertGreater(len(output.splitlines()), 15)

    def test_render_concepts_no_crash(self) -> None:
        """Verifica o Menu dos 12 conceitos estruturantes e simulação analítica."""
        output = self.engine.render_concepts(width=96)
        self.assertIn("OS 12 CONCEITOS COMPLEXOS B3", output)
        self.assertIn("Novação Legal e Contraparte Central", output)
        self.assertIn("Cascata de Salvaguardas da Câmara", output)
        self.assertIn("Leilão de Fechamento MOC/LOC", output)
        self.assertIn("Drex Fase 2", output)
        self.assertIn("Sistema Nacional de Gravames SNG", output)
        self.assertIn("Sistema de Registro de Operações SRO", output)

    def test_all_12_concepts_simulation_in_tui(self) -> None:
        """Executa a simulação de todos os 12 conceitos e valida o veredito formal."""
        for idx, concept in enumerate(CONCEPTS):
            self.engine.selected_concept_idx = idx
            result = self.engine.run_concept_simulation()
            self.assertIsNotNone(result)
            self.assertEqual(result.get("concept_id"), concept.id)
            rendered = self.engine.render_concepts(width=96)
            self.assertIn(concept.id, rendered)
            self.assertIn("RESULTADO DA SIMULAÇÃO", rendered)
            self.assertIn("INVARIANTE MATEMÁTICA CONSERVADA", rendered)

    def test_screen_aliases(self) -> None:
        """Verifica se os aliases de telas são mapeados adequadamente."""
        test_cases = [
            ("overview", "VISÃO GERAL"),
            ("puma", "MONITOR DE NEGOCIAÇÃO PUMA"),
            ("moc", "MONITOR DE NEGOCIAÇÃO PUMA"),
            ("bus", "BARRAMENTO CENTRAL"),
            ("central_bus", "BARRAMENTO CENTRAL"),
            ("sng", "REGISTRADORES NACIONAIS"),
            ("sro", "REGISTRADORES NACIONAIS"),
            ("sng_sro", "REGISTRADORES NACIONAIS"),
            ("concepts", "12 CONCEITOS COMPLEXOS"),
        ]
        for alias, expected_content in test_cases:
            rendered = self.engine.render_screen(alias, width=96)
            self.assertIn(expected_content, rendered, f"Alias '{alias}' falhou ao renderizar.")

    def test_no_color_strips_ansi(self) -> None:
        """Verifica que quando use_color=False, nenhuma sequência ANSI é gerada."""
        engine_nocolor = B3TUIEngine(use_color=False)
        for screen in ["overview", "puma", "central_bus", "sng_sro", "concepts"]:
            out = engine_nocolor.render_screen(screen)
            matches = ANSI_ESCAPE_RE.findall(out)
            self.assertEqual(len(matches), 0, f"Sequências ANSI encontradas na tela {screen} com use_color=False")

    def test_tick_advances_market_pulse(self) -> None:
        """Verifica o avanço de ticks e atualização de preços no monitor PUMA."""
        initial_ticks = self.engine.tick_count
        self.engine.tick()
        self.assertEqual(self.engine.tick_count, initial_ticks + 1)
        self.assertGreater(self.engine.ref_price, 0)

    def test_headless_run(self) -> None:
        """Verifica que run_headless gera saída válida para multi-ticks."""
        out = self.engine.run_headless(ticks=2, width=96)
        self.assertIn("B3 INTEGRATION ATLAS", out)
        self.assertEqual(self.engine.tick_count, 2)


class TestB3TUICLIParsingAndExecution(unittest.TestCase):
    """Testes de integração CLI para `./b3 tui` e `./b3 monitor`."""

    def test_parser_contains_tui_and_monitor(self) -> None:
        """Verifica se os comandos 'tui' e 'monitor' estão registrados no parser."""
        parser = build_atlas_parser()
        args_tui = parser.parse_args(["tui", "--screen", "puma", "--headless"])
        self.assertEqual(args_tui.screen, "puma")
        self.assertTrue(args_tui.headless)

        args_mon = parser.parse_args(["monitor", "--symbol", "VALE3", "--headless"])
        self.assertEqual(args_mon.symbol, "VALE3")
        self.assertTrue(args_mon.headless)

    def test_cli_tui_overview_headless(self) -> None:
        """Testa `./b3 tui --headless --no-color`."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            exit_code = main_b3(["tui", "--headless", "--no-color"])
        self.assertEqual(exit_code, 0)
        output = captured.getvalue()
        self.assertIn("FLUXOS TOTAIS: 107", output)
        self.assertIn("LEIS FORMAIS: 91", output)

    def test_cli_tui_screen_puma(self) -> None:
        """Testa `./b3 tui --screen puma --headless --no-color`."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            exit_code = main_b3(["tui", "--screen", "puma", "--headless", "--no-color"])
        self.assertEqual(exit_code, 0)
        output = captured.getvalue()
        self.assertIn("MONITOR DE NEGOCIAÇÃO PUMA", output)
        self.assertIn("BEST BID", output)

    def test_cli_tui_screen_bus(self) -> None:
        """Testa `./b3 tui --screen bus --headless --no-color`."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            exit_code = main_b3(["tui", "--screen", "bus", "--headless", "--no-color"])
        self.assertEqual(exit_code, 0)
        output = captured.getvalue()
        self.assertIn("BARRAMENTO CENTRAL DE LIQUIDAÇÃO B3", output)
        self.assertIn("LAW-106A", output)

    def test_cli_tui_screen_sng_sro(self) -> None:
        """Testa `./b3 tui --screen sng_sro --headless --no-color`."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            exit_code = main_b3(["tui", "--screen", "sng_sro", "--headless", "--no-color"])
        self.assertEqual(exit_code, 0)
        output = captured.getvalue()
        self.assertIn("REGISTRADORES NACIONAIS", output)

    def test_cli_tui_screen_concepts_with_arg(self) -> None:
        """Testa `./b3 tui --screen concepts --concept drex-dvp-atomico --headless --no-color`."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            exit_code = main_b3(
                ["tui", "--screen", "concepts", "--concept", "drex-dvp-atomico", "--headless", "--no-color"]
            )
        self.assertEqual(exit_code, 0)
        output = captured.getvalue()
        self.assertIn("drex-dvp-atomico", output)
        self.assertIn("ATOMICIDADE_CONFIRMADA", output)

    def test_cli_monitor_command(self) -> None:
        """Testa comando direto `./b3 monitor --headless --no-color`."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            exit_code = main_b3(["monitor", "--headless", "--no-color", "--symbol", "PETR4"])
        self.assertEqual(exit_code, 0)
        output = captured.getvalue()
        self.assertIn("MONITOR DE NEGOCIAÇÃO PUMA", output)
        self.assertIn("PETR4", output)

    def test_interactive_non_tty_fallback(self) -> None:
        """Verifica que run_interactive cai com segurança para renderização única quando sys.stdin.isatty() for False."""
        captured = io.StringIO()
        engine = B3TUIEngine(use_color=False)
        with patch("sys.stdin.isatty", return_value=False), patch("sys.stdout", captured):
            ret = engine.run_interactive()
        self.assertEqual(ret, 0)
        self.assertIn("B3 INTEGRATION ATLAS", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
