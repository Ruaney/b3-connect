"""Testes unitários para o exportador estático standalone (scripts/export_static_dashboard.py)."""

import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.export_static_dashboard import (
    collect_static_dataset,
    extract_html_template_from_server,
    generate_standalone_bundle,
    validate_standalone_bundle,
)


class TestExportStaticDashboard(unittest.TestCase):
    """Bateria de testes para validação da geração do bundle standalone."""

    def test_collect_static_dataset(self) -> None:
        """Verifica se o dataset coletado possui 107 fluxos, 87 documentos e 12 conceitos."""
        data = collect_static_dataset(_REPO_ROOT)
        self.assertEqual(len(data["flows"]), 107)
        self.assertEqual(len(data["docs"]), 87)
        self.assertEqual(len(data["concepts"]), 12)
        self.assertEqual(len(data["modules"]), 7)
        self.assertEqual(len(data["chaos"]), 4)
        self.assertEqual(len(data["phase7"]), 7)
        self.assertIn("FL-101", data["phase7"])
        self.assertIn("FL-107", data["phase7"])
        self.assertTrue(data["tlaCheck"]["passed"])

    def test_extract_html_template_from_server(self) -> None:
        """Verifica extração correta do template HTML de b3_ui_server.py."""
        html = extract_html_template_from_server(_REPO_ROOT)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("B3 ATLAS", html)
        self.assertIn("tab-overview", html)
        self.assertIn("tab-flows", html)

    def test_generate_and_validate_bundle_temp(self) -> None:
        """Verifica a geração de um bundle completo em diretório temporário."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "dist" / "dashboard_standalone.html"
            file_size = generate_standalone_bundle(_REPO_ROOT, out_path)
            self.assertTrue(out_path.is_file())
            self.assertGreater(file_size, 500_000)

            # Validação
            validate_standalone_bundle(out_path)


if __name__ == "__main__":
    unittest.main()
