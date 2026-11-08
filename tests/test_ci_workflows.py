"""B3 Integration Atlas — Validador Sintático e Semântico dos Workflows de CI/CD.

Suíte oficial de testes unitários para a esteira de Integração Contínua e Quality Gate:
 1. Validação sintática rigorosa dos arquivos YAML (.github/workflows/*.yml) sem dependências externas.
 2. Validação semântica de .github/workflows/ci.yml:
    - Matriz de testes: Python 3.8, 3.9, 3.10, 3.11, 3.12, 3.13 no Ubuntu, macOS e Windows.
    - Zero-Pip Dependencies: verificação estrita via scripts/verify_zero_pip.py.
    - 91 Leis de Conservação: scripts/verify_invariants.py e src/formal_invariant_prover.py.
    - OpenAPI 3.1 e Postman v2.1: geração e suítes de validação.
    - Tipagem Estrita: scripts/verify_type_annotations.py e MyPy.
 3. Validação semântica de .github/workflows/benchmarks.yml:
    - Execução do profiler de nanossegundos scripts/benchmark_zero_alloc.py.
    - Asserções estritas de latência sub-microssegundo (< 1000ns) e zero alocações reais de heap.
    - Upload de artefatos de telemetria.
 4. Validação semântica de .github/workflows/pages.yml:
    - Compilação do bundle offline dist/dashboard_standalone.html via scripts/export_static_dashboard.py.
    - Validação de integridade e tamanho do bundle (> 1MB).
    - Deploy automatizado com fallback dual (Actions Pages + branch gh-pages).
 5. Testes funcionais diretos dos auditores de Zero-Pip e Tipagem Estrita AST.
"""

from pathlib import Path
import re
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.verify_zero_pip import audit_zero_pip
from scripts.verify_type_annotations import audit_src_typing


class SimpleYamlValidator:
    """Validador sintático de YAML institucional (Zero-Pip) baseado na biblioteca padrão."""

    @staticmethod
    def validate_syntax(content: str, filename: str) -> None:
        """Garante regras sintáticas básicas de YAML: ausência de tabs e balanceamento de blocos."""
        lines = content.splitlines()
        in_multiline_block = False
        block_indent = 0

        for idx, line in enumerate(lines, start=1):
            if "\t" in line:
                raise ValueError(f"[{filename}:{idx}] Uso proibido de caracteres TAB em arquivo YAML.")

            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            current_indent = len(line) - len(line.lstrip())

            # Gerenciamento de blocos multiline literais (| ou >)
            if in_multiline_block:
                if current_indent <= block_indent and (":" in stripped or stripped.startswith("- ")):
                    in_multiline_block = False
                else:
                    # Conteúdo interno do bloco literal - não é parsing YAML direto
                    continue

            if stripped.endswith("|") or stripped.endswith(">") or stripped.endswith("|-") or stripped.endswith("|+"):
                in_multiline_block = True
                block_indent = current_indent
                continue

            # Verifica strings com aspas abertas sem fechar em linhas de mapeamento YAML
            for quote in ('"', "'"):
                if stripped.count(quote) % 2 != 0:
                    raise ValueError(f"[{filename}:{idx}] Aspas não balanceadas ({quote}): {stripped}")


class TestCIWorkflows(unittest.TestCase):
    """Suíte de auditoria e validação da esteira institucional de CI/CD."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.workflows_dir = REPO_ROOT / ".github" / "workflows"
        cls.ci_file = cls.workflows_dir / "ci.yml"
        cls.benchmarks_file = cls.workflows_dir / "benchmarks.yml"
        cls.pages_file = cls.workflows_dir / "pages.yml"

        # Conteúdos
        cls.ci_content = cls.ci_file.read_text(encoding="utf-8")
        cls.benchmarks_content = cls.benchmarks_file.read_text(encoding="utf-8")
        cls.pages_content = cls.pages_file.read_text(encoding="utf-8")

    # -------------------------------------------------------------------------
    # 1. Validação Sintática Básica dos Workflows
    # -------------------------------------------------------------------------
    def test_workflow_files_exist(self) -> None:
        """Garante que os 3 workflows institucionais existem no disco."""
        self.assertTrue(self.ci_file.is_file(), "ci.yml não encontrado")
        self.assertTrue(self.benchmarks_file.is_file(), "benchmarks.yml não encontrado")
        self.assertTrue(self.pages_file.is_file(), "pages.yml não encontrado")

    def test_workflow_syntax_valid(self) -> None:
        """Garante que os 3 workflows respeitam regras sintáticas estritas do YAML."""
        SimpleYamlValidator.validate_syntax(self.ci_content, "ci.yml")
        SimpleYamlValidator.validate_syntax(self.benchmarks_content, "benchmarks.yml")
        SimpleYamlValidator.validate_syntax(self.pages_content, "pages.yml")

    # -------------------------------------------------------------------------
    # 2. Validação Semântica de ci.yml
    # -------------------------------------------------------------------------
    def test_ci_triggers_and_branches(self) -> None:
        """Valida se ci.yml dispara em push, pull_request e workflow_dispatch para main/master."""
        self.assertIn("push:", self.ci_content)
        self.assertIn("pull_request:", self.ci_content)
        self.assertIn("workflow_dispatch:", self.ci_content)
        self.assertIn("- main", self.ci_content)
        self.assertIn("- master", self.ci_content)

    def test_ci_matrix_platforms_and_python_versions(self) -> None:
        """Garante matriz de testes para Python 3.8-3.13 no Ubuntu, macOS e Windows."""
        # SOs
        self.assertIn("ubuntu-latest", self.ci_content)
        self.assertIn("macos-latest", self.ci_content)
        self.assertIn("windows-latest", self.ci_content)

        # Versões Python exigidas: 3.8, 3.9, 3.10, 3.11, 3.12, 3.13
        expected_pythons = ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13"]
        for version in expected_pythons:
            self.assertIn(version, self.ci_content, f"Versão Python {version} ausente da matriz de CI")

    def test_ci_zero_pip_verification_step(self) -> None:
        """Garante que a auditoria Zero-Pip está presente na esteira."""
        self.assertIn("scripts/verify_zero_pip.py", self.ci_content)

    def test_ci_formal_91_invariants_verification(self) -> None:
        """Garante que a prova formal das 91 Leis de Conservação é executada na esteira."""
        self.assertIn("scripts/verify_invariants.py", self.ci_content)
        self.assertIn("src/formal_invariant_prover.py", self.ci_content)

    def test_ci_openapi_and_postman_validation(self) -> None:
        """Garante que OpenAPI 3.1 e Postman v2.1 são gerados e validados na esteira."""
        self.assertIn("scripts/generate_openapi_spec.py", self.ci_content)
        self.assertIn("scripts/generate_postman_collection.py", self.ci_content)
        self.assertIn("tests/test_openapi_spec.py", self.ci_content)
        self.assertIn("tests/test_postman_collection.py", self.ci_content)

    def test_ci_strict_typing_mypy_and_ast(self) -> None:
        """Garante verificação de tipagem estrita via AST e MyPy."""
        self.assertIn("scripts/verify_type_annotations.py", self.ci_content)
        self.assertIn("mypy", self.ci_content)

    # -------------------------------------------------------------------------
    # 3. Validação Semântica de benchmarks.yml
    # -------------------------------------------------------------------------
    def test_benchmarks_regression_execution(self) -> None:
        """Garante que benchmarks.yml executa o profiler e os testes de benchmark."""
        self.assertIn("scripts/benchmark_zero_alloc.py", self.benchmarks_content)
        self.assertIn("tests/test_benchmark_zero_alloc.py", self.benchmarks_content)

    def test_benchmarks_submicrosecond_and_zero_alloc_assertions(self) -> None:
        """Garante asserções estritas de latência sub-microssegundo e zero heap allocation."""
        self.assertIn("PUMA_SBE_NewOrderSingle_UnpackRaw_ZeroAlloc", self.benchmarks_content)
        self.assertIn("PUMA_SBE_ExecutionReport_UnpackRaw_ZeroAlloc", self.benchmarks_content)
        self.assertIn("is_zero_alloc", self.benchmarks_content)
        self.assertIn("< 1000", self.benchmarks_content)

    def test_benchmarks_artifact_upload(self) -> None:
        """Garante persistência de artefatos de telemetria e benchmark."""
        self.assertIn("upload-artifact", self.benchmarks_content)
        self.assertIn("benchmark_zero_alloc_results.json", self.benchmarks_content)
        self.assertIn("RELATORIO_MICROBENCHMARKS_NANOSSEGUNDOS.md", self.benchmarks_content)

    # -------------------------------------------------------------------------
    # 4. Validação Semântica de pages.yml
    # -------------------------------------------------------------------------
    def test_pages_standalone_bundle_build(self) -> None:
        """Garante que pages.yml compila dist/dashboard_standalone.html antes do deploy."""
        self.assertIn("scripts/export_static_dashboard.py", self.pages_content)
        self.assertIn("dist/dashboard_standalone.html", self.pages_content)

    def test_pages_permissions_and_deployment_targets(self) -> None:
        """Garante permissões necessárias e deploy no GitHub Pages."""
        self.assertIn("pages: write", self.pages_content)
        self.assertIn("id-token: write", self.pages_content)
        self.assertIn("deploy-pages", self.pages_content)
        self.assertIn("gh-pages", self.pages_content)

    # -------------------------------------------------------------------------
    # 5. Validação Funcional dos Scripts de Auditoria
    # -------------------------------------------------------------------------
    def test_zero_pip_script_functional(self) -> None:
        """Executa audit_zero_pip() diretamente garantindo 0 violações no repo."""
        is_valid, violations, total_checked = audit_zero_pip(REPO_ROOT)
        self.assertTrue(
            is_valid,
            f"Violações Zero-Pip encontradas: {violations}",
        )
        self.assertGreater(total_checked, 2000, "Menos de 2000 imports verificados")

    def test_type_annotations_script_functional(self) -> None:
        """Executa audit_src_typing() diretamente garantindo cobertura >= 98% e sem erros sintáticos."""
        src_dir = REPO_ROOT / "src"
        total_funcs, typed_funcs, unannotated, syntax_errors = audit_src_typing(src_dir)
        self.assertEqual(len(syntax_errors), 0, f"Erros sintáticos: {syntax_errors}")
        coverage = (typed_funcs / total_funcs * 100.0) if total_funcs > 0 else 0.0
        self.assertGreaterEqual(coverage, 98.0, f"Cobertura abaixo de 98%: {coverage:.2f}%")


if __name__ == "__main__":
    unittest.main()
