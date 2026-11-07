"""Testes unitários da interface de linha de comando (CLI) do B3 Integration Atlas."""

from io import StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock

from src.cli import (
    VERSION,
    build_atlas_parser,
    build_verify_parser,
    cmd_explain,
    cmd_flow_info,
    cmd_list_flows,
    cmd_list_sources,
    cmd_simulate_central_bus,
    cmd_simulate_flow,
    cmd_simulate_moc,
    cmd_simulate_triparty,
    cmd_stats,
    cmd_trace_balcao,
    cmd_trace_trade,
    load_catalog,
    main_atlas,
    main_verify,
    resolve_catalog_dir,
    resolve_fixtures_dir,
)


class TestAtlasCLI(unittest.TestCase):
    """Bateria de testes unitários para a CLI b3-atlas e b3-verify (Marco de 100 Fluxos)."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.catalog_dir = cls.repo_root / "catalog"
        cls.fixtures_dir = cls.repo_root / "fixtures"

    def test_version_constant(self):
        """Verifica a versão 2.2.0 celebrando o marco de 100 fluxos B3."""
        self.assertEqual(VERSION, "2.2.0")

    def test_resolve_catalog_dir(self):
        """Verifica se resolve_catalog_dir encontra a pasta catalog válida."""
        cat_dir = resolve_catalog_dir()
        self.assertTrue(cat_dir.is_dir(), f"Pasta catalog não encontrada: {cat_dir}")
        self.assertTrue((cat_dir / "flows.json").is_file())
        self.assertTrue((cat_dir / "sources.json").is_file())

    def test_resolve_fixtures_dir(self):
        """Verifica se resolve_fixtures_dir encontra a pasta fixtures válida."""
        fix_dir = resolve_fixtures_dir()
        self.assertTrue(fix_dir.is_dir(), f"Pasta fixtures não encontrada: {fix_dir}")

    def test_load_catalog(self):
        """Verifica carregamento estruturado e integridade total dos 100 fluxos B3."""
        flows_data, sources_data = load_catalog(self.catalog_dir)
        self.assertIn("flows", flows_data)
        self.assertIn("sources", sources_data)
        self.assertEqual(len(flows_data["flows"]), 100)
        self.assertGreaterEqual(len(sources_data["sources"]), 30)

        flow_ids = {f["id"] for f in flows_data["flows"]}
        self.assertIn("FL-098", flow_ids)
        self.assertIn("FL-099", flow_ids)
        self.assertIn("FL-100", flow_ids)

    def test_cmd_list_flows_text(self):
        """Testa list-flows em formato tabular texto."""
        parser = build_atlas_parser()
        args = parser.parse_args(["list-flows"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_list_flows(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("FL-001", output)
            self.assertIn("FL-100", output)
            self.assertIn("100 fluxos encontrados", output)

    def test_cmd_list_flows_filter_system(self):
        """Testa list-flows com filtro por sistema."""
        parser = build_atlas_parser()
        args = parser.parse_args(["list-flows", "-s", "PUMA"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_list_flows(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("Puma", output)

    def test_cmd_list_flows_json(self):
        """Testa list-flows com saída JSON válida dos 100 fluxos."""
        parser = build_atlas_parser()
        args = parser.parse_args(["list-flows", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_list_flows(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            parsed = json.loads(output)
            self.assertIsInstance(parsed, list)
            self.assertEqual(len(parsed), 100)
            self.assertIn("id", parsed[0])

    def test_cmd_flow_info_found(self):
        """Testa consulta detalhada de fluxo existente."""
        parser = build_atlas_parser()
        args = parser.parse_args(["info", "FL-001"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_flow_info(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("FL-001", output)
            self.assertIn("Estágios de Homologação", output)

    def test_cmd_flow_info_fl100(self):
        """Testa consulta detalhada do fluxo FL-100 (Barramento Central)."""
        parser = build_atlas_parser()
        args = parser.parse_args(["info", "FL-100"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_flow_info(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("FL-100", output)
            self.assertIn("Barramento Central", output)

    def test_cmd_flow_info_json(self):
        """Testa consulta detalhada de fluxo com saída JSON."""
        parser = build_atlas_parser()
        args = parser.parse_args(["info", "FL-098", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_flow_info(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            parsed = json.loads(output)
            self.assertEqual(parsed.get("id"), "FL-098")
            self.assertIn("MOC/LOC", parsed.get("name", ""))

    def test_cmd_flow_info_not_found(self):
        """Testa consulta de fluxo inexistente gerando código 1."""
        parser = build_atlas_parser()
        args = parser.parse_args(["info", "FL-INEXISTENTE-999"])
        with patch("sys.stderr", new=StringIO()) as fake_err:
            code = cmd_flow_info(args)
            self.assertEqual(code, 1)
            self.assertIn("não encontrado", fake_err.getvalue())

    def test_cmd_stats(self):
        """Testa exibição de estatísticas gerais dos 100 fluxos."""
        parser = build_atlas_parser()
        args = parser.parse_args(["stats"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_stats(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("ESTATÍSTICAS E MÉTRICAS", output)
            self.assertIn("Total de Fluxos Mapeados: 100", output)

    def test_cmd_stats_json(self):
        """Testa exibição de estatísticas em JSON."""
        parser = build_atlas_parser()
        args = parser.parse_args(["stats", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_stats(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            parsed = json.loads(output)
            self.assertEqual(parsed.get("total_flows"), 100)
            self.assertIn("total_sources", parsed)
            self.assertIn("stages", parsed)

    def test_cmd_list_sources(self):
        """Testa listagem de fontes normativas."""
        parser = build_atlas_parser()
        args = parser.parse_args(["list-sources"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_list_sources(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("fontes encontradas", output)

    def test_cmd_list_sources_json(self):
        """Testa listagem de fontes em JSON."""
        parser = build_atlas_parser()
        args = parser.parse_args(["list-sources", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_list_sources(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            parsed = json.loads(output)
            self.assertIsInstance(parsed, list)
            self.assertGreater(len(parsed), 30)

    # --------------------------------------------------------------------------
    # Testes dos Novos Comandos de Simulação: MOC/LOC, Triparty e Barramento Central
    # --------------------------------------------------------------------------

    def test_cmd_simulate_moc_text_and_json(self):
        """Testa simulação do Leilão de Fechamento MOC/LOC (FL-098)."""
        parser = build_atlas_parser()

        # Modo texto
        args_text = parser.parse_args(["simulate-moc"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_simulate_moc(args_text)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("SIMULAÇÃO: LEILÃO DE FECHAMENTO PUMA (MOC/LOC)", output)
            self.assertIn("FL-098", output)
            self.assertIn("Prioridade MOC Cumprida:      Sim (LAW-104B)", output)

        # Modo JSON
        args_json = parser.parse_args(["simulate-moc", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_simulate_moc(args_json)
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertEqual(data["flow"], "FL-098")
            self.assertEqual(data["status"], "SUCCESS")
            self.assertIn("LAW-104A", data["invariants_verified"])
            self.assertIn("LAW-104B", data["invariants_verified"])
            self.assertIn("LAW-104C", data["invariants_verified"])

    def test_cmd_simulate_moc_freeze_window(self):
        """Testa leilão de fechamento com auditoria da Janela de Congelamento."""
        parser = build_atlas_parser()
        args = parser.parse_args(["simulate-moc", "--test-freeze", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_simulate_moc(args)
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertTrue(data["freeze_protection_tested"])
            self.assertTrue(data["freeze_inviolability_verified"])

    def test_cmd_simulate_moc_custom_orders(self):
        """Testa leilão de fechamento com ordens customizadas."""
        parser = build_atlas_parser()
        args = parser.parse_args(["simulate-moc", "--custom", "--symbol", "VALE3", "--ref-price", "60.00", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_simulate_moc(args)
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertEqual(data["symbol"], "VALE3")
            self.assertEqual(data["status"], "SUCCESS")

    def test_cmd_simulate_triparty_text_and_json(self):
        """Testa simulação de Garantias Triparty Repo Internacional (FL-099)."""
        parser = build_atlas_parser()

        # Modo texto
        args_text = parser.parse_args(["simulate-triparty"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_simulate_triparty(args_text)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("SIMULAÇÃO: TRIPARTY REPO & GARANTIAS INTERNACIONAIS", output)
            self.assertIn("FL-099", output)
            self.assertIn("Segregação Fiduciária", output)

        # Modo JSON
        args_json = parser.parse_args(["simulate-triparty", "--custodian", "CLEARSTREAM", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_simulate_triparty(args_json)
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertEqual(data["flow"], "FL-099")
            self.assertEqual(data["custodian"], "CLEARSTREAM")
            self.assertEqual(data["status"], "SUCCESS")
            self.assertIn("LAW-105A", data["invariants_verified"])
            self.assertIn("LAW-105B", data["invariants_verified"])
            self.assertIn("LAW-105C", data["invariants_verified"])

    def test_cmd_simulate_central_bus_text_and_json(self):
        """Testa simulação do Barramento Central de Liquidação Unificada (FL-100)."""
        parser = build_atlas_parser()

        # Modo texto
        args_text = parser.parse_args(["simulate-central-bus"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_simulate_central_bus(args_text)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("SIMULAÇÃO: BARRAMENTO CENTRAL DE LIQUIDAÇÃO MULTILATERAL", output)
            self.assertIn("FL-100", output)
            self.assertIn("10 (Equities, Deriv, Balcão, RF, Agro, Fundos, Drex, Pix, CCEE, Carbono)", output)

        # Modo JSON
        args_json = parser.parse_args(["simulate-central-bus", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_simulate_central_bus(args_json)
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertEqual(data["flow"], "FL-100")
            self.assertEqual(data["subsystems_count"], 10)
            self.assertEqual(data["accounting_imbalance_brl"], "0.00")
            self.assertIn("LAW-106A", data["invariants_verified"])
            self.assertIn("LAW-106B", data["invariants_verified"])
            self.assertIn("LAW-106C", data["invariants_verified"])

    def test_cmd_simulate_central_bus_stress_default(self):
        """Testa simulação do barramento central com cenário de Default Waterfall."""
        parser = build_atlas_parser()
        args = parser.parse_args(["simulate-central-bus", "--stress-default", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_simulate_central_bus(args)
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertTrue(data["stress_default_tested"])
            self.assertTrue(data["default_absorbed"])
            self.assertGreater(data["stages_activated"], 0)

    def test_cmd_simulate_flow_dispatcher(self):
        """Testa comando unificado simulate-flow para os 100 fluxos B3."""
        parser = build_atlas_parser()

        # FL-098 (MOC/LOC)
        args_fl98 = parser.parse_args(["simulate-flow", "FL-098", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_simulate_flow(args_fl98)
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertEqual(data["flow"], "FL-098")

        # FL-099 (Triparty)
        args_fl99 = parser.parse_args(["simulate-flow", "FL-099", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_simulate_flow(args_fl99)
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertEqual(data["flow"], "FL-099")

        # FL-100 (Barramento Central)
        args_fl100 = parser.parse_args(["simulate-flow", "FL-100", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_simulate_flow(args_fl100)
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertEqual(data["flow"], "FL-100")

        # FL-001 (Fluxo genérico dos 100)
        args_fl01 = parser.parse_args(["simulate-flow", "FL-001", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_simulate_flow(args_fl01)
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertEqual(data["flow_id"], "FL-001")
            self.assertEqual(data["simulation_status"], "VERIFIED_FORMALLY")

    def test_cmd_simulate_flow_not_found(self):
        """Testa simulate-flow com ID inválido."""
        parser = build_atlas_parser()
        args = parser.parse_args(["simulate-flow", "FL-999"])
        with patch("sys.stderr", new=StringIO()) as fake_err:
            code = cmd_simulate_flow(args)
            self.assertEqual(code, 1)
            self.assertIn("não encontrado", fake_err.getvalue())

    def test_main_atlas_no_args_prints_help(self):
        """Testa chamada sem argumentos imprimindo help e retornando 0."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas([])
            self.assertEqual(code, 0)
            self.assertIn("usage:", fake_out.getvalue().lower())

    def test_main_atlas_dispatch(self):
        """Testa despacho completo via main_atlas."""
        with patch("sys.stdout", new=StringIO()):
            code = main_atlas(["stats", "--json"])
            self.assertEqual(code, 0)

    def test_main_atlas_dispatch_simulations_and_aliases(self):
        """Testa execução dos novos subcomandos e aliases via entrypoint principal."""
        with patch("sys.stdout", new=StringIO()):
            # simulate-moc e alias moc
            self.assertEqual(main_atlas(["simulate-moc", "--json"]), 0)
            self.assertEqual(main_atlas(["moc", "--json"]), 0)

            # simulate-triparty e alias triparty
            self.assertEqual(main_atlas(["simulate-triparty", "--json"]), 0)
            self.assertEqual(main_atlas(["triparty", "--json"]), 0)

            # simulate-central-bus e aliases central-bus / barramento
            self.assertEqual(main_atlas(["simulate-central-bus", "--json"]), 0)
            self.assertEqual(main_atlas(["central-bus", "--json"]), 0)
            self.assertEqual(main_atlas(["barramento", "--json"]), 0)

            # simulate-flow e alias run-flow
            self.assertEqual(main_atlas(["simulate-flow", "FL-100", "--json"]), 0)
            self.assertEqual(main_atlas(["run-flow", "FL-098", "--json"]), 0)

    def test_explain_command_all_modules(self):
        """Testa o comando b3-atlas explain para todos os 7 módulos e overview."""
        modules = ["overview", "pre-trade", "puma", "balcao", "ccp", "spb", "csd", "bsm"]
        for mod in modules:
            with patch("sys.stdout", new=StringIO()) as fake_out:
                code = main_atlas(["explain", mod])
                self.assertEqual(code, 0, f"Falha no explain para {mod}")
                self.assertIn("MÓDULO", fake_out.getvalue().upper() if mod != "overview" else fake_out.getvalue())

    def test_explain_json_output(self):
        """Testa explain com formato JSON."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["explain", "puma", "--json"])
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertIn("title", data)
            self.assertIn("PUMA", data["title"])

    def test_trace_trade_normal_and_flags(self):
        """Testa simulação interativa de negociação listada percorrendo os 7 módulos."""
        # Caso normal
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["trace-trade"])
            self.assertEqual(code, 0)
            self.assertIn("LIQUIDAÇÃO CONCLUÍDA", fake_out.getvalue())
            self.assertIn("TOTALMENTE CONFORME", fake_out.getvalue())

        # Caso Fat-Finger
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["trace-trade", "--fat-finger"])
            self.assertEqual(code, 0)
            self.assertIn("FAT_FINGER_REJECTION", fake_out.getvalue())

        # Caso Wash Trading
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["trace-trade", "--wash-trade"])
            self.assertEqual(code, 0)
            self.assertIn("WASH_TRADING_DETECTED", fake_out.getvalue())

        # JSON output
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["trace-trade", "--json"])
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertTrue(data["is_settled"])
            self.assertEqual(len(data["steps"]), 7)

    def test_trace_balcao_lifecycle(self):
        """Testa simulação de registro em balcão NoMe com gravame de recebível."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["trace-balcao", "--asset", "CDB", "--notional", "300000"])
            self.assertEqual(code, 0)
            self.assertIn("Trava de domicílio", fake_out.getvalue())
            self.assertIn("LIQUIDAÇÃO CONCLUÍDA", fake_out.getvalue())

        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["trace-balcao", "--json"])
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertEqual(data["operation_type"], "OTC_REGISTRATION")
            self.assertTrue(data["is_settled"])

    def test_harvest_docs_cli(self):
        """Testa o comando b3-atlas harvest-docs para status e json."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["harvest-docs", "--json"])
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertIn("total_cataloged", data)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["harvest-docs", "--sync"])
            self.assertEqual(code, 0)
            self.assertIn("B3 DOCUMENTATION HARVESTER", fake_out.getvalue())

    def test_simulate_day_cli(self):
        """Testa o comando b3-atlas simulate-day e formato JSON."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["simulate-day"])
            self.assertEqual(code, 0)
            self.assertIn("B3 SIMULADOR MULTI-MÓDULO", fake_out.getvalue())

        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["simulate-day", "--json"])
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertIn("metrics", data)
            self.assertIn("events", data)

    def test_doc_read_cli(self):
        """Testa a leitura de documentos locais via b3-atlas doc-read."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["doc-read", "SRC-006"])
            self.assertEqual(code, 0)
            self.assertIn("DOCUMENTO OFICIAL B3: SRC-006", fake_out.getvalue())

        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["doc-read", "SRC-004"])
            self.assertEqual(code, 0)
            self.assertIn("Arquivo Binário PDF 1.4", fake_out.getvalue())

    def test_modules_cli(self):
        """Testa o mapa arquitetural holístico dos 7 módulos via b3-atlas modules."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["modules"])
            self.assertEqual(code, 0)
            self.assertIn("ARQUITETURA HOLÍSTICA DOS 7 MÓDULOS", fake_out.getvalue())
            self.assertIn("PUMA Trading System", fake_out.getvalue())
            self.assertIn("Balcão NoMe / Cetip", fake_out.getvalue())

    def test_chaos_cli_scenarios(self):
        """Testa o simulador de cenários de crise e engenharia do caos via b3-atlas chaos."""
        # Execução padrão (ALL)
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["chaos"])
            self.assertEqual(code, 0)
            output = fake_out.getvalue()
            self.assertIn("SIMULADOR DE CRISES", output)
            self.assertIn("CIRCUIT_BREAKER", output)
            self.assertIn("CLEARING_DEFAULT", output)
            self.assertIn("RECEIVABLES_LIEN_CONFLICT", output)
            self.assertIn("SPB_NETWORK_DISRUPTION", output)

        # Execução específica com saída JSON
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_atlas(["chaos", "--scenario", "CLEARING_DEFAULT", "--json"])
            self.assertEqual(code, 0)
            data = json.loads(fake_out.getvalue())
            self.assertEqual(data["scenario"], "CLEARING_DEFAULT")
            self.assertTrue(data["is_resilient"])
            self.assertIn("LAW-106B", data["laws_verified"])

    def test_ui_cli_invocation(self):
        """Testa o comando b3-atlas ui com mock do servidor HTTP."""
        with patch("src.b3_ui_server.run_dashboard_server") as mock_server_builder:
            mock_server = MagicMock()
            mock_server_builder.return_value = mock_server
            mock_server.serve_forever.side_effect = KeyboardInterrupt

            with patch("sys.stdout", new=StringIO()) as fake_out:
                code = main_atlas(["ui", "--port", "19090", "--no-browser"])
                self.assertEqual(code, 0)
                self.assertIn("DASHBOARD INTERATIVO LOCAL", fake_out.getvalue())
                self.assertIn("19090", fake_out.getvalue())
                mock_server_builder.assert_called_once_with(host="127.0.0.1", port=19090)
                mock_server.serve_forever.assert_called_once()
                mock_server.server_close.assert_called_once()

    def test_main_verify_execution(self):
        """Testa execução do verificador formal de invariantes via main_verify."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_verify([])
            self.assertEqual(code, 0)
            self.assertIn("LEIS DE CONSERVAÇÃO E INVARIANTES DA B3 FORAM PROVADAS", fake_out.getvalue())


if __name__ == "__main__":
    unittest.main()
