"""Testes de validação da Coleção Postman v2.1 (docs/b3_atlas_postman_collection.json)."""

import json
from pathlib import Path
import unittest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS_DIR = _REPO_ROOT / "docs"


class TestPostmanCollection(unittest.TestCase):
    """Bateria de validação estrutural da Coleção Postman v2.1."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.collection_file = _DOCS_DIR / "b3_atlas_postman_collection.json"
        with open(cls.collection_file, "r", encoding="utf-8") as f:
            cls.collection = json.load(f)

    def test_file_exists_and_is_valid_json(self) -> None:
        """Verifica se o arquivo existe e possui JSON válido."""
        self.assertTrue(self.collection_file.is_file())
        self.assertIn("info", self.collection)
        self.assertIn("item", self.collection)
        self.assertIn("variable", self.collection)

    def test_postman_schema_v21(self) -> None:
        """Valida se o schema da coleção é Postman v2.1.0."""
        info = self.collection["info"]
        self.assertEqual(info["schema"], "https://schema.getpostman.com/json/collection/v2.1.0/collection.json")
        self.assertIn("B3 Integration Atlas", info["name"])

    def test_environment_variables(self) -> None:
        """Verifica se as variáveis base_url, api_key e hmac_secret estão configuradas."""
        variables = {v["key"]: v["value"] for v in self.collection.get("variable", [])}
        self.assertIn("base_url", variables)
        self.assertIn("api_key", variables)
        self.assertIn("hmac_secret", variables)
        self.assertEqual(variables["base_url"], "http://127.0.0.1:8080")

    def test_folders_structure_exact_match(self) -> None:
        """Verifica se as 9 pastas obrigatórias estão presentes exatamente como solicitado."""
        expected_folders = [
            "01 - PUMA Trading & Ordem MOC/LOC",
            "02 - Câmara B3 & Default Waterfall",
            "03 - Balcão NoMe & Recebíveis",
            "04 - Central Depositária & STVM",
            "05 - SPB, STR & Drex Fase 2",
            "06 - SNG Financiamentos (Veículos)",
            "07 - SRO Seguros (SUSEP V3)",
            "08 - Laboratório de Conceitos Complexos (/api/concepts)",
            "09 - Simulação & Chaos Testing",
        ]
        actual_folders = [item["name"] for item in self.collection["item"]]
        self.assertEqual(actual_folders, expected_folders)

    def test_requests_test_scripts_and_assertions(self) -> None:
        """Verifica se todas as requisições têm scripts de teste com asserção 200 e validações."""
        total_requests = 0
        for folder in self.collection["item"]:
            for req_item in folder["item"]:
                total_requests += 1
                self.assertIn("request", req_item)
                self.assertIn("url", req_item["request"])
                self.assertIn("event", req_item)

                # Verifica se há evento de teste
                test_events = [ev for ev in req_item["event"] if ev.get("listen") == "test"]
                self.assertTrue(len(test_events) > 0, f"Request {req_item['name']} sem evento de teste")

                exec_script = "\n".join(test_events[0]["script"]["exec"])
                self.assertIn("pm.response.to.have.status(200)", exec_script)

        self.assertEqual(total_requests, 67)


if __name__ == "__main__":
    unittest.main()
