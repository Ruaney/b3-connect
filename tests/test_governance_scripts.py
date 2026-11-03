"""
Testes automatizados dos scripts de governança multiplataforma:
- scripts/test_catalog.py
- scripts/get_coverage.py
"""

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Adiciona o diretório scripts ao sys.path para importação direta
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from test_catalog import validate_catalog, CatalogValidationError, resolve_default_paths
from get_coverage import calculate_coverage, format_percent


class TestCatalogValidation(unittest.TestCase):
    """Testa o validador de integridade estrita do catálogo."""

    def setUp(self):
        self.sources_path = REPO_ROOT / "catalog" / "sources.json"
        self.flows_path = REPO_ROOT / "catalog" / "flows.json"

        with open(self.sources_path, "r", encoding="utf-8") as f:
            self.valid_sources_data = json.load(f)

        with open(self.flows_path, "r", encoding="utf-8") as f:
            self.valid_flows_data = json.load(f)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_catalogs(self, sources_data, flows_data):
        s_path = self.temp_path / "sources.json"
        f_path = self.temp_path / "flows.json"
        with open(s_path, "w", encoding="utf-8") as f:
            json.dump(sources_data, f)
        with open(f_path, "w", encoding="utf-8") as f:
            json.dump(flows_data, f)
        return s_path, f_path

    def test_official_catalog_passes_validation(self):
        """O catálogo oficial do repositório deve passar sem erros."""
        result = validate_catalog(
            self.sources_path,
            self.flows_path,
            check_files=True,
            repo_root=REPO_ROOT,
        )
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["sources_count"], len(self.valid_sources_data["sources"]))
        self.assertEqual(result["flows_count"], len(self.valid_flows_data["flows"]))

    def test_duplicate_source_id_raises_error(self):
        """IDs duplicados em fontes devem ser rejeitados."""
        s_data = copy.deepcopy(self.valid_sources_data)
        dup = copy.deepcopy(s_data["sources"][0])
        s_data["sources"].append(dup)

        s_path, f_path = self._write_catalogs(s_data, self.valid_flows_data)
        with self.assertRaises(CatalogValidationError) as ctx:
            validate_catalog(s_path, f_path)
        self.assertIn("IDs duplicados em fontes", str(ctx.exception))

    def test_duplicate_flow_id_raises_error(self):
        """IDs duplicados em fluxos devem ser rejeitados."""
        f_data = copy.deepcopy(self.valid_flows_data)
        dup = copy.deepcopy(f_data["flows"][0])
        f_data["flows"].append(dup)

        s_path, f_path = self._write_catalogs(self.valid_sources_data, f_data)
        with self.assertRaises(CatalogValidationError) as ctx:
            validate_catalog(s_path, f_path)
        self.assertIn("IDs duplicados em fluxos", str(ctx.exception))

    def test_incomplete_source_missing_fields(self):
        """Fonte sem limitação ou título obrigatório deve falhar."""
        s_data = copy.deepcopy(self.valid_sources_data)
        del s_data["sources"][0]["limitation"]

        s_path, f_path = self._write_catalogs(s_data, self.valid_flows_data)
        with self.assertRaises(CatalogValidationError) as ctx:
            validate_catalog(s_path, f_path)
        self.assertIn("Fonte incompleta", str(ctx.exception))

    def test_unofficial_source_url(self):
        """URLs fora do domínio oficial b3.com.br devem falhar."""
        s_data = copy.deepcopy(self.valid_sources_data)
        s_data["sources"][0]["url"] = "https://www.google.com/search?q=b3"

        s_path, f_path = self._write_catalogs(s_data, self.valid_flows_data)
        with self.assertRaises(CatalogValidationError) as ctx:
            validate_catalog(s_path, f_path)
        self.assertIn("Fonte não oficial", str(ctx.exception))

    def test_http_insecure_url_fails(self):
        """URLs em HTTP simples (inseguro) devem falhar."""
        s_data = copy.deepcopy(self.valid_sources_data)
        s_data["sources"][0]["url"] = "http://www.b3.com.br/pt_br/"

        s_path, f_path = self._write_catalogs(s_data, self.valid_flows_data)
        with self.assertRaises(CatalogValidationError) as ctx:
            validate_catalog(s_path, f_path)
        self.assertIn("Fonte não oficial", str(ctx.exception))

    def test_flow_missing_pending_fails(self):
        """Fluxo sem o campo pending deve falhar."""
        f_data = copy.deepcopy(self.valid_flows_data)
        del f_data["flows"][0]["pending"]

        s_path, f_path = self._write_catalogs(self.valid_sources_data, f_data)
        with self.assertRaises(CatalogValidationError) as ctx:
            validate_catalog(s_path, f_path)
        self.assertIn("Fluxo incompleto", str(ctx.exception))

    def test_flow_empty_sources_fails(self):
        """Fluxo sem lista de fontes deve falhar."""
        f_data = copy.deepcopy(self.valid_flows_data)
        f_data["flows"][0]["sources"] = []

        s_path, f_path = self._write_catalogs(self.valid_sources_data, f_data)
        with self.assertRaises(CatalogValidationError) as ctx:
            validate_catalog(s_path, f_path)
        self.assertIn("Fluxo incompleto", str(ctx.exception))

    def test_flow_referencing_nonexistent_source_fails(self):
        """Fluxo referenciando fonte inexistente deve falhar."""
        f_data = copy.deepcopy(self.valid_flows_data)
        f_data["flows"][0]["sources"].append("SRC-999")

        s_path, f_path = self._write_catalogs(self.valid_sources_data, f_data)
        with self.assertRaises(CatalogValidationError) as ctx:
            validate_catalog(s_path, f_path)
        self.assertIn("Fonte inexistente: SRC-999", str(ctx.exception))

    def test_invalid_state_type_fails(self):
        """Estado com tipo que não seja booleano deve falhar."""
        f_data = copy.deepcopy(self.valid_flows_data)
        f_data["flows"][0]["implemented"] = "sim"  # string em vez de bool

        s_path, f_path = self._write_catalogs(self.valid_sources_data, f_data)
        with self.assertRaises(CatalogValidationError) as ctx:
            validate_catalog(s_path, f_path)
        self.assertIn("Estado inválido", str(ctx.exception))

    def test_missing_evidence_when_state_is_true_fails(self):
        """Se estado for True (e != 'mapped'), evidência correspondente é obrigatória."""
        f_data = copy.deepcopy(self.valid_flows_data)
        # FL-001 tem implemented=True, vamos remover a evidência
        del f_data["flows"][0]["evidence"]["implemented"]

        s_path, f_path = self._write_catalogs(self.valid_sources_data, f_data)
        with self.assertRaises(CatalogValidationError) as ctx:
            validate_catalog(s_path, f_path)
        self.assertIn("Falta evidência: FL-001/implemented", str(ctx.exception))

    def test_check_files_fails_when_file_does_not_exist(self):
        """Verificação de arquivos deve falhar se o arquivo apontado na evidência não existir."""
        f_data = copy.deepcopy(self.valid_flows_data)
        f_data["flows"][0]["evidence"]["implemented"] = "src/arquivo_que_nao_existe.py"

        s_path, f_path = self._write_catalogs(self.valid_sources_data, f_data)
        with self.assertRaises(CatalogValidationError) as ctx:
            validate_catalog(s_path, f_path, check_files=True, repo_root=REPO_ROOT)
        self.assertIn("Arquivo de evidência não encontrado no disco", str(ctx.exception))


class TestCoverageCalculation(unittest.TestCase):
    """Testa o cálculo de cobertura dos fluxos."""

    def setUp(self):
        self.flows_path = REPO_ROOT / "catalog" / "flows.json"

    def test_calculate_coverage_official(self):
        data = calculate_coverage(self.flows_path)
        with open(self.flows_path, "r", encoding="utf-8") as f:
            flows_json = json.load(f)
        total = len(flows_json.get("flows", []))
        self.assertEqual(data["total_flows"], total)
        for state in ["mapped", "documented", "implemented", "tested_locally", "validated_b3"]:
            expected_count = sum(1 for flow in flows_json.get("flows", []) if flow.get(state) is True)
            self.assertEqual(data["coverage"][state]["count"], expected_count)
            expected_pct = round(100.0 * expected_count / total, 1) if total else 0.0
            self.assertEqual(data["coverage"][state]["percent"], expected_pct)

    def test_format_percent(self):
        self.assertEqual(format_percent(100.0), "100%")
        self.assertEqual(format_percent(60.0), "60%")
        self.assertEqual(format_percent(0.0), "0%")
        self.assertEqual(format_percent(33.3), "33.3%")
        self.assertEqual(format_percent(12.5), "12.5%")


if __name__ == "__main__":
    unittest.main()
