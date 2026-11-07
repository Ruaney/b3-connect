"""Testes unitários do Coletor e Extrator de Documentação B3 (b3_doc_harvester.py)."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.b3_doc_harvester import B3DocHarvester, B3DocMetadata, B3LinkExtractor


class TestB3DocHarvester(unittest.TestCase):
    """Bateria de testes para o motor de raspagem e catalogação documental da B3."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target_dir = Path(self.temp_dir.name)
        self.harvester = B3DocHarvester(target_dir=self.target_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_classify_url_categories(self) -> None:
        """Verifica se a classificação temática por URL e texto funciona corretamente."""
        # PUMA
        cat, ext = self.harvester._classify_url("https://b3.com.br/puma/sbe-specs.pdf", "PUMA SBE")
        self.assertEqual(cat, "PUMA_TRADING")
        self.assertEqual(ext, "pdf")

        # Câmara e Risco
        cat, ext = self.harvester._classify_url("https://b3.com.br/clearing/core-margin-rules.pdf", "Manual CORE")
        self.assertEqual(cat, "CLEARING_RISK")
        self.assertEqual(ext, "pdf")

        # NoMe / Balcão
        cat, ext = self.harvester._classify_url("https://b3.com.br/cetip/registro-cdb.xsd", "Schema CDB")
        self.assertEqual(cat, "NOME_BALCAO")
        self.assertEqual(ext, "xsd")

        # CSD
        cat, ext = self.harvester._classify_url("https://b3.com.br/imercado/layout-imbarq012.zip", "IMBARQ012")
        self.assertEqual(cat, "CSD_DEPOSITARY")
        self.assertEqual(ext, "zip")

        # SPB / Liquidação Bacen / Pix / Drex
        cat, ext = self.harvester._classify_url("https://b3.com.br/spb/mensageria-str.pdf", "Catálogo SPB")
        self.assertEqual(cat, "SPB_SETTLEMENT")
        self.assertEqual(ext, "pdf")

        # BSM Supervisão de Mercados
        cat, ext = self.harvester._classify_url("https://bsm.com.br/auditoria/roteiro-rpa.pdf", "Roteiro BSM")
        self.assertEqual(cat, "BSM_SURVEILLANCE")
        self.assertEqual(ext, "pdf")

        # Tarifas e Emolumentos
        cat, ext = self.harvester._classify_url("https://b3.com.br/tarifas/tabela-emolumentos.xlsx", "Tarifas e Custos")
        self.assertEqual(cat, "TARIFF_CATALOG")
        self.assertEqual(ext, "xlsx")

        # Circulares Regulatórias
        cat, ext = self.harvester._classify_url("https://b3.com.br/regulacao/oficio-circular-001.pdf", "Ofício Circular CVM")
        self.assertEqual(cat, "REGULATORY_CIRCULARS")
        self.assertEqual(ext, "pdf")

    def test_import_from_sources_catalog(self) -> None:
        """Testa importação de fontes JSON para o manifesto do coletor."""
        mock_sources = {
            "sources": [
                {
                    "id": "SRC-MOCK-01",
                    "title": "Manual PUMA SBE Entry",
                    "url": "https://www.b3.com.br/puma-entry.pdf",
                },
                {
                    "id": "SRC-MOCK-02",
                    "title": "Manual Câmara B3 CORE Margem",
                    "url": "https://www.b3.com.br/clearing-core.pdf",
                },
            ]
        }
        sources_file = self.target_dir / "test_sources.json"
        with open(sources_file, "w", encoding="utf-8") as f:
            json.dump(mock_sources, f)

        imported = self.harvester.import_from_sources_catalog(sources_file)
        self.assertEqual(imported, 2)
        self.assertEqual(len(self.harvester.documents), 2)
        self.assertIn("SRC-MOCK-01", self.harvester.documents)
        self.assertTrue(self.harvester.manifest_path.is_file())

    def test_link_extractor_html(self) -> None:
        """Testa o extrator HTML isolando links e textos das âncoras."""
        html_sample = """
        <html>
            <body>
                <a href="/docs/manual-puma.pdf">Manual PUMA 2026</a>
                <a href="https://external.com/schema.xsd">Schema Externo</a>
            </body>
        </html>
        """
        parser = B3LinkExtractor(base_url="https://www.b3.com.br/pt_br/")
        parser.feed(html_sample)

        self.assertEqual(len(parser.extracted_links), 2)
        self.assertEqual(parser.extracted_links[0][0], "https://www.b3.com.br/docs/manual-puma.pdf")
        self.assertEqual(parser.extracted_links[0][1], "Manual PUMA 2026")

    def test_download_document_dry_run(self) -> None:
        """Verifica que o dry_run simula sem efetuar chamadas de rede."""
        self.harvester.documents["DOC-TEST"] = B3DocMetadata(
            doc_id="DOC-TEST",
            title="Doc Teste",
            url="https://www.b3.com.br/test.pdf",
            category="PUMA_TRADING",
            file_type="pdf",
        )
        success = self.harvester.download_document("DOC-TEST", dry_run=True)
        self.assertTrue(success)

    @patch.object(B3DocHarvester, "fetch_url")
    def test_download_document_mock_success(self, mock_fetch) -> None:
        """Testa download simulado gravando em disco e calculando SHA-256."""
        mock_fetch.return_value = (b"CONTEUDO_PDF_SIMULADO_B3", "application/pdf", None)

        self.harvester.documents["SRC-001"] = B3DocMetadata(
            doc_id="SRC-001",
            title="Manual Teste",
            url="https://www.b3.com.br/doc.pdf",
            category="CLEARING_RISK",
            file_type="pdf",
        )

        success = self.harvester.download_document("SRC-001", dry_run=False)
        self.assertTrue(success)

        doc = self.harvester.documents["SRC-001"]
        self.assertEqual(doc.download_status, "DOWNLOADED")
        self.assertEqual(doc.size_bytes, len(b"CONTEUDO_PDF_SIMULADO_B3"))
        self.assertIsNotNone(doc.sha256)

    def test_populate_local_archive_and_summary(self) -> None:
        """Testa a geração local autoritativa e o cálculo estatístico de cobertura."""
        mock_sources = {
            "sources": [
                {
                    "id": "SRC-POP-01",
                    "title": "Especificação FIX Entrada",
                    "url": "https://www.b3.com.br/puma-fix.html",
                },
                {
                    "id": "SRC-POP-02",
                    "title": "Manual de Conciliação IMBARQ",
                    "url": "https://www.b3.com.br/imercado-doc.pdf",
                },
            ]
        }
        sources_file = self.target_dir / "test_pop_sources.json"
        with open(sources_file, "w", encoding="utf-8") as f:
            json.dump(mock_sources, f)

        count = self.harvester.populate_local_archive(sources_path=sources_file, force=True)
        self.assertEqual(count, 2)

        summary = self.harvester.get_summary()
        self.assertEqual(summary["total_cataloged"], 2)
        self.assertEqual(summary["downloaded_count"], 2)
        self.assertEqual(summary["percent_downloaded"], 100.0)
        self.assertEqual(summary["by_status"]["DOWNLOADED"], 2)

        # Verifica existência física dos arquivos e hashes
        for doc in self.harvester.documents.values():
            self.assertEqual(doc.download_status, "DOWNLOADED")
            self.assertGreater(doc.size_bytes, 0)
            self.assertIsNotNone(doc.sha256)
            self.assertEqual(len(doc.sha256), 64)
            disk_path = self.target_dir.parent / doc.local_path
            self.assertTrue(disk_path.is_file())


if __name__ == "__main__":
    unittest.main()
