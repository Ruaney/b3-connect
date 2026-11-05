"""Testes unitários da interface de linha de comando (CLI) do B3 Integration Atlas."""

from io import StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from src.cli import (
    build_atlas_parser,
    build_verify_parser,
    cmd_flow_info,
    cmd_list_flows,
    cmd_list_sources,
    cmd_stats,
    load_catalog,
    main_atlas,
    main_verify,
    resolve_catalog_dir,
    resolve_fixtures_dir,
)


class TestAtlasCLI(unittest.TestCase):
    """Bateria de testes unitários para a CLI b3-atlas e b3-verify."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.catalog_dir = cls.repo_root / "catalog"
        cls.fixtures_dir = cls.repo_root / "fixtures"

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
        """Verifica carregamento estruturado de flows.json e sources.json."""
        flows_data, sources_data = load_catalog(self.catalog_dir)
        self.assertIn("flows", flows_data)
        self.assertIn("sources", sources_data)
        self.assertGreater(len(flows_data["flows"]), 50)
        self.assertGreater(len(sources_data["sources"]), 30)

    def test_cmd_list_flows_text(self):
        """Testa list-flows em formato tabular texto."""
        parser = build_atlas_parser()
        args = parser.parse_args(["list-flows"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_list_flows(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("FL-001", output)
            self.assertIn("B3 Integration Atlas", output)

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
        """Testa list-flows com saída JSON válida."""
        parser = build_atlas_parser()
        args = parser.parse_args(["list-flows", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_list_flows(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            parsed = json.loads(output)
            self.assertIsInstance(parsed, list)
            self.assertGreater(len(parsed), 50)
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

    def test_cmd_flow_info_json(self):
        """Testa consulta detalhada de fluxo com saída JSON."""
        parser = build_atlas_parser()
        args = parser.parse_args(["info", "FL-001", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_flow_info(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            parsed = json.loads(output)
            self.assertEqual(parsed.get("id"), "FL-001")
            self.assertIn("sources_detail", parsed)

    def test_cmd_flow_info_not_found(self):
        """Testa consulta de fluxo inexistente gerando código 1."""
        parser = build_atlas_parser()
        args = parser.parse_args(["info", "FL-INEXISTENTE-999"])
        with patch("sys.stderr", new=StringIO()) as fake_err:
            code = cmd_flow_info(args)
            self.assertEqual(code, 1)
            self.assertIn("não encontrado", fake_err.getvalue())

    def test_cmd_stats(self):
        """Testa exibição de estatísticas gerais."""
        parser = build_atlas_parser()
        args = parser.parse_args(["stats"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_stats(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("ESTATÍSTICAS E MÉTRICAS", output)
            self.assertIn("Total de Fluxos Mapeados", output)

    def test_cmd_stats_json(self):
        """Testa exibição de estatísticas em JSON."""
        parser = build_atlas_parser()
        args = parser.parse_args(["stats", "--json"])
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = cmd_stats(args)
            output = fake_out.getvalue()
            self.assertEqual(code, 0)
            parsed = json.loads(output)
            self.assertIn("total_flows", parsed)
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

    def test_main_verify_execution(self):
        """Testa execução do verificador formal de invariantes via main_verify."""
        # Redireciona stdout para suprimir o log volumoso das 70 leis
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main_verify([])
            self.assertEqual(code, 0)
            self.assertIn("LEIS DE CONSERVAÇÃO E INVARIANTES DA B3 FORAM PROVADAS", fake_out.getvalue())


if __name__ == "__main__":
    unittest.main()
