"""Testes unitários do Servidor do Dashboard Local (b3_ui_server.py)."""

from http.server import HTTPServer
import json
import threading
import time
import unittest
import urllib.request

from src.b3_ui_server import B3DashboardHandler, run_dashboard_server


class TestB3UiServer(unittest.TestCase):
    """Bateria de testes para as rotas e respostas da API do Dashboard Local."""

    @classmethod
    def setUpClass(cls) -> None:
        # Encontra uma porta livre para o teste
        cls.port = 18080
        cls.server = HTTPServer(("127.0.0.1", cls.port), B3DashboardHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str) -> tuple[int, bytes, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            body = resp.read()
            headers = dict(resp.headers)
            return status, body, headers

    def test_serve_html_dashboard_root(self) -> None:
        """Verifica se a rota raiz '/' retorna o HTML do dashboard."""
        status, body, headers = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn(b"B3 ATLAS", body)
        self.assertIn(b"7 M\xc3\xb3dulos B3", body)

    def test_api_status(self) -> None:
        """Verifica endpoint /api/status."""
        status, body, headers = self._get("/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["version"], "2.2.0")
        self.assertEqual(data["homologated_flows"], 100)
        self.assertEqual(data["formal_conservation_laws"], 84)
        self.assertEqual(data["architecture_modules"], 7)

    def test_api_modules(self) -> None:
        """Verifica endpoint /api/modules com os 7 módulos."""
        status, body, headers = self._get("/api/modules")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(len(data), 7)
        ids = [m["id"] for m in data]
        self.assertIn("PUMA_TRADING", ids)
        self.assertIn("NOME_BALCAO", ids)
        self.assertIn("CLEARING_RISK", ids)

    def test_api_docs_list_and_single(self) -> None:
        """Verifica /api/docs e /api/docs/<doc_id>."""
        status, body, headers = self._get("/api/docs")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(len(data), 80)

        # Detalhes de um documento específico
        status, body, headers = self._get("/api/docs/SRC-006")
        self.assertEqual(status, 200)
        doc_data = json.loads(body.decode("utf-8"))
        self.assertEqual(doc_data["doc"]["doc_id"], "SRC-006")
        self.assertIn("raw_content", doc_data)

    def test_api_simulate_day(self) -> None:
        """Verifica execução de simulação via /api/simulate-day."""
        status, body, headers = self._get("/api/simulate-day?shares=50000")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("simulation_id", data)
        self.assertIn("metrics", data)
        self.assertGreater(len(data["events"]), 0)

    def test_api_chaos_scenario(self) -> None:
        """Verifica execução de caos via /api/chaos/<scenario>."""
        status, body, headers = self._get("/api/chaos/CIRCUIT_BREAKER")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["is_resilient"])
        self.assertEqual(data["scenario"], "CIRCUIT_BREAKER")

    def test_api_health(self) -> None:
        """Verifica endpoint /api/health."""
        status, body, headers = self._get("/api/health")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["status"], "UP")
        self.assertTrue(data["offline"])


if __name__ == "__main__":
    unittest.main()
