"""Bateria de testes unitários de validação da especificação OpenAPI 3.1.

Arquivo auditado: docs/openapi_b3_atlas_complete.json
Valida conformidade rigorosa com a especificação OpenAPI 3.1 e integridade referencial.
"""

import json
from pathlib import Path
import re
import unittest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC_FILE = _REPO_ROOT / "docs" / "openapi_b3_atlas_complete.json"

class TestOpenApiSpec(unittest.TestCase):
    """Suíte de testes de integridade da especificação OpenAPI 3.1."""

    @classmethod
    def setUpClass(cls) -> None:
        assert _SPEC_FILE.is_file(), f"Arquivo não encontrado: {_SPEC_FILE}"
        with open(_SPEC_FILE, "r", encoding="utf-8") as f:
            cls.spec = json.load(f)

    def test_openapi_version_and_metadata(self) -> None:
        self.assertEqual(self.spec.get("openapi"), "3.1.0")
        info = self.spec.get("info", {})
        self.assertEqual(info.get("version"), "2.3.0")
        self.assertIn("B3 Integration Atlas", info.get("title", ""))
        self.assertIn("Lei Federal", info.get("description", ""))
        self.assertIn("contact", info)
        self.assertIn("license", info)

    def test_servers_configuration(self) -> None:
        servers = self.spec.get("servers", [])
        self.assertGreaterEqual(len(servers), 3)
        urls = [s["url"] for s in servers]
        self.assertIn("http://localhost:8080", urls)
        self.assertIn("https://api.b3.com.br", urls)
        self.assertIn("https://cert.api.b3.com.br", urls)

    def test_all_required_endpoints_present(self) -> None:
        paths = self.spec.get("paths", {})
        required_endpoints = [
            "/api/status",
            "/api/health",
            "/api/modules",
            "/api/docs",
            "/api/docs/{doc_id}",
            "/api/simulate-day",
            "/api/chaos/{scenario}",
            "/api/phase7/{flow_id}",
            "/api/tla-check",
            "/api/concepts",
            "/api/concepts/{concept_id}",
            "/api/concepts/{concept_id}/simulate",
            "/api/v1/orders",
            "/api/v1/auction/moc",
            "/api/v1/auction/theoretical-price",
            "/api/v1/sng/gravames",
            "/api/v1/sro/apolices",
        ]
        for ep in required_endpoints:
            self.assertIn(ep, paths, f"Endpoint obrigatório ausente: {ep}")

    def test_detailed_gateway_routes_present(self) -> None:
        paths = self.spec.get("paths", {})
        additional_routes = [
            "/api/v1/orders/{order_id}",
            "/api/v1/sng/gravames/{gravame_id}",
            "/api/v1/sng/gravames/{gravame_id}/baixa",
            "/api/v1/sng/gravames/{gravame_id}/cancelamento",
            "/api/v1/sro/apolices/{apolice_id}",
            "/api/v1/sro/apolices/{apolice_id}/endosso",
            "/api/v1/sro/apolices/{apolice_id}/sinistros",
            "/api/v1/sro/apolices/{apolice_id}/sinistros/{sinistro_id}/liquidar",
        ]
        for r in additional_routes:
            self.assertIn(r, paths, f"Rota detalhada ausente: {r}")

    def test_http_methods_and_status_codes(self) -> None:
        paths = self.spec.get("paths", {})
        for path_name, path_item in paths.items():
            for method, op in path_item.items():
                if method in ("get", "post", "delete", "put", "patch"):
                    self.assertIn("summary", op)
                    self.assertIn("operationId", op)
                    self.assertIn("responses", op)
                    success_codes = [c for c in op["responses"] if c.startswith("2")]
                    self.assertTrue(len(success_codes) > 0)

    def test_referential_integrity(self) -> None:
        spec_str = json.dumps(self.spec, indent=2, ensure_ascii=False)
        ref_pattern = re.compile(r"\"\$ref\":\s*\"([^\"]+)\"")
        refs = ref_pattern.findall(spec_str)
        self.assertGreater(len(refs), 40)
        schemas = self.spec["components"]["schemas"]
        parameters = self.spec["components"]["parameters"]
        responses = self.spec["components"]["responses"]
        for r in refs:
            if r.startswith("#/components/schemas/"):
                s = r.replace("#/components/schemas/", "")
                self.assertIn(s, schemas)
            elif r.startswith("#/components/parameters/"):
                p = r.replace("#/components/parameters/", "")
                self.assertIn(p, parameters)
            elif r.startswith("#/components/responses/"):
                resp = r.replace("#/components/responses/", "")
                self.assertIn(resp, responses)

    def test_security_schemes(self) -> None:
        sec = self.spec["components"]["securitySchemes"]
        self.assertIn("OAuth2MutualTLS", sec)
        self.assertIn("InstitutionalApiKey", sec)

if __name__ == "__main__":
    unittest.main()
