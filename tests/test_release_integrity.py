"""B3 Integration Atlas — Suíte de Testes de Integridade da Release v2.3.0.

Audita a presença e validade do Manifesto Criptográfico SHA-256 e das Notas de Release:
1. Existência e integridade do arquivo docs/CHECKSUMS_SHA256_v2.3.0.txt.
2. Verificação estrita de que 100% dos arquivos catalogados possuem SHA-256 idêntico ao disco.
3. Cobertura completa de todas as seções obrigatórias nas Notas de Release v2.3.0:
   - 107 Fluxos operacionais (FL-001 a FL-107) e 91 Leis Formais de Conservação.
   - SNG (B3 Financiamentos / CONTRAN 807) e SRO (B3 Seguros / SUSEP 642).
   - Especificação OpenAPI 3.1 oficial e Coleção Postman v2.1.
   - SDKs oficiais em Python e TypeScript (v2.3.0).
   - Bateria de 1 milhão de operações a 2,63M msg/s com zero alocações de heap.
   - Verificação formal TLA+ e Provador Simbólico de Teoremas.
   - Auditorias de AppSec Clearance e Parecer LGPD.
   - Dashboard Grafana com 35 painéis e bundle offline standalone.
4. Teste de conformidade da biblioteca de validação criptográfica (FIPS 180-4).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest

# Assegura que o REPO_ROOT está no PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.verify_checksums import (
    compute_sha256,
    generate_manifest,
    verify_manifest,
)


class TestReleaseIntegrity(unittest.TestCase):
    """Suíte oficial de auditoria de integridade para o pacote de release v2.3.0."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = REPO_ROOT
        cls.manifest_path = cls.repo_root / "docs" / "CHECKSUMS_SHA256_v2.3.0.txt"
        cls.release_notes_path = cls.repo_root / "docs" / "RELEASE_NOTES_v2.3.0.md"

    def test_01_sha256_known_vector(self):
        """Valida precisão matemática da rotina compute_sha256 com vetor FIPS."""
        # Cria arquivo temporário em memória / scratch para testar vetor nulo
        tmp_empty = self.repo_root / "fixtures" / "__tmp_empty_vector.bin"
        tmp_empty.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp_empty.write_bytes(b"")
            # SHA-256 de string vazia canônica
            expected_empty_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            self.assertEqual(compute_sha256(tmp_empty), expected_empty_hash)
        finally:
            if tmp_empty.exists():
                tmp_empty.unlink()

    def test_02_release_notes_exists_and_complete(self):
        """Verifica a presença e o conteúdo integral de docs/RELEASE_NOTES_v2.3.0.md."""
        self.assertTrue(
            self.release_notes_path.is_file(),
            f"Arquivo de Notas de Release não encontrado em: {self.release_notes_path}",
        )

        content = self.release_notes_path.read_text(encoding="utf-8")
        self.assertGreater(len(content), 1000, "Notas de release devem ser completas e ricas")

        # Verificações obrigatórias especificadas pelo comitê
        required_topics = [
            ("107 Fluxos operacionais", "107 Fluxos"),
            ("91 Leis Formais", "91 Leis"),
            ("SNG (B3 Financiamentos / CONTRAN 807)", "CONTRAN"),
            ("SRO (B3 Seguros / SUSEP 642)", "SUSEP"),
            ("OpenAPI 3.1", "OpenAPI 3.1"),
            ("Postman v2.1", "Postman"),
            ("SDKs oficiais em Python e TypeScript", "SDK"),
            ("1 milhão de operações", "1 Milhão"),
            ("Zero-Alloc", "Zero-Alloc"),
            ("TLA+", "TLA+"),
            ("Provador Simbólico", "Provador Simbólico"),
            ("AppSec Clearance", "AppSec"),
            ("Parecer LGPD", "LGPD"),
            ("Dashboard Grafana com 35 painéis", "35 painéis"),
            ("bundle offline standalone", "standalone"),
        ]

        for desc, needle in required_topics:
            self.assertIn(
                needle.lower(),
                content.lower(),
                f"Tópico obrigatório não encontrado nas Release Notes: '{desc}' (termo: '{needle}')",
            )

    def test_03_checksum_manifest_exists_and_structured(self):
        """Verifica existência e formato canônico de docs/CHECKSUMS_SHA256_v2.3.0.txt."""
        self.assertTrue(
            self.manifest_path.is_file(),
            f"Manifesto de Checksums não encontrado em: {self.manifest_path}",
        )

        lines = self.manifest_path.read_text(encoding="utf-8").splitlines()
        data_lines = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]

        self.assertGreaterEqual(
            len(data_lines),
            50,
            f"Manifesto deve cobrir uma quantidade significativa de arquivos (encontrados: {len(data_lines)})",
        )

        for line in data_lines[:20]:
            parts = line.split(maxsplit=1)
            self.assertEqual(len(parts), 2, f"Linha com formato inválido: {line}")
            sha_part, path_part = parts[0], parts[1]
            self.assertEqual(len(sha_part), 64, f"Hash SHA-256 deve ter 64 caracteres hex: {sha_part}")
            self.assertTrue(
                all(c in "0123456789abcdefABCDEF" for c in sha_part),
                f"Hash SHA-256 com caracteres inválidos: {sha_part}",
            )

    def test_04_verify_all_checksums_match_100_percent(self):
        """Executa auditoria criptográfica completa de 100% dos arquivos do repositório."""
        success, summary = verify_manifest(self.manifest_path, self.repo_root)

        if not success:
            err_msg = (
                f"Auditoria Criptográfica Falhou!\n"
                f"Arquivos Corrompidos: {len(summary['corrupted_files'])}\n"
                f"Arquivos Ausentes: {len(summary['missing_files'])}\n"
                f"Detalhes Corrompidos: {summary['corrupted_files'][:5]}\n"
                f"Detalhes Ausentes: {summary['missing_files'][:5]}"
            )
            self.fail(err_msg)

        self.assertTrue(success, "Validação de checksums deve retornar sucesso")
        self.assertEqual(summary["failed"], 0, "Nenhum arquivo pode estar corrompido")
        self.assertEqual(summary["missing"], 0, "Nenhum arquivo do manifesto pode estar ausente")
        self.assertGreater(summary["passed"], 50, "Deve validar com sucesso todos os arquivos")

    def test_05_critical_files_covered_in_manifest(self):
        """Assegura que arquivos críticos e vitais da release estão no manifesto."""
        content = self.manifest_path.read_text(encoding="utf-8")

        critical_files = [
            "docs/RELEASE_NOTES_v2.3.0.md",
            "scripts/verify_checksums.py",
            "tests/test_release_integrity.py",
        ]

        for cf in critical_files:
            self.assertIn(
                cf,
                content,
                f"Arquivo crítico '{cf}' não está presente no manifesto de checksums!",
            )

    def test_06_sibling_repo_release_sync(self):
        """Se o repositório irmão b3-enterprise-platform existir, valida sincronismo de release."""
        sibling_root = self.repo_root.parent / "b3-enterprise-platform"
        if not sibling_root.is_dir():
            self.skipTest("Repositório irmão b3-enterprise-platform não está no mesmo diretório base")

        sibling_notes = sibling_root / "docs" / "RELEASE_NOTES_v2.3.0.md"
        sibling_manifest = sibling_root / "docs" / "CHECKSUMS_SHA256_v2.3.0.txt"

        self.assertTrue(sibling_notes.is_file(), "b3-enterprise-platform deve conter RELEASE_NOTES_v2.3.0.md")
        self.assertTrue(sibling_manifest.is_file(), "b3-enterprise-platform deve conter CHECKSUMS_SHA256_v2.3.0.txt")

        # Valida também o manifesto do repositório irmão
        ok, sib_summary = verify_manifest(sibling_manifest, sibling_root)
        self.assertTrue(ok, f"Manifesto do repositório irmão deve estar íntegro: {sib_summary}")


if __name__ == "__main__":
    unittest.main()
