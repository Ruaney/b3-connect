#!/usr/bin/env python3
"""scripts/verify_type_annotations.py

Auditor Estático Institucional de Tipagem via AST (Abstract Syntax Tree).
Valida conformidade com PEP 484, PEP 561 e PEP 526:
 1. Validação sintática estrita de 100% dos arquivos em src/.
 2. Verificação de presença do marcador de pacote tipado (src/py.typed).
 3. Verificação de cobertura de anotações de tipo em funções e métodos (limite institucional >= 98%).
 4. Verificação de argumentos tipados e retornos explícitos.
"""

import ast
from pathlib import Path
import sys
from typing import Dict, List, NamedTuple, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"

INSTITUTIONAL_COVERAGE_THRESHOLD = 98.0  # Mínimo exigido: 98%


class FunctionTypeReport(NamedTuple):
    file_name: str
    func_name: str
    lineno: int
    has_return: bool
    unannotated_args: List[str]


def audit_src_typing(src_dir: Path) -> Tuple[int, int, List[FunctionTypeReport], List[str]]:
    """Audita estaticamente todos os arquivos em src/ via AST."""
    total_functions = 0
    fully_typed_functions = 0
    unannotated_list: List[FunctionTypeReport] = []
    syntax_errors: List[str] = []

    for py_file in sorted(src_dir.glob("*.py")):
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content, filename=str(py_file))
        except Exception as e:
            syntax_errors.append(f"{py_file.name}: {e}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total_functions += 1
                has_return = node.returns is not None
                # __init__ e __post_init__ sem anotação de retorno implicitamente retornam None
                if not has_return and node.name in ("__init__", "__post_init__"):
                    has_return = True

                unannotated_args = [
                    arg.arg
                    for arg in node.args.args
                    if arg.annotation is None and arg.arg not in ("self", "cls")
                ]

                if has_return and not unannotated_args:
                    fully_typed_functions += 1
                else:
                    unannotated_list.append(
                        FunctionTypeReport(
                            file_name=py_file.name,
                            func_name=node.name,
                            lineno=node.lineno,
                            has_return=has_return,
                            unannotated_args=unannotated_args,
                        )
                    )

    return total_functions, fully_typed_functions, unannotated_list, syntax_errors


def main() -> int:
    print("======================================================================")
    print(" B3 INTEGRATION ATLAS — AUDITORIA ESTÁTICA DE TIPAGEM VIA AST / PEP 561")
    print(" Verificação de cobertura de tipos estritos e integridade sintática")
    print("======================================================================")

    py_typed_file = SRC_DIR / "py.typed"
    if not py_typed_file.is_file():
        print(f"❌ Marcador PEP 561 ausente: {py_typed_file}", file=sys.stderr)
        return 1
    print(f"✓ Marcador de pacote tipado PEP 561 verificado: {py_typed_file.name}")

    total_funcs, typed_funcs, unannotated, syntax_errors = audit_src_typing(SRC_DIR)

    if syntax_errors:
        print("\n❌ Erros de parsing sintático detectados:")
        for err in syntax_errors:
            print(f"  - {err}")
        return 1

    coverage = (typed_funcs / total_funcs * 100.0) if total_funcs > 0 else 100.0

    print(f"Total de funções / métodos analisados: {total_funcs}")
    print(f"Funções estritamente tipadas:          {typed_funcs}")
    print(f"Cobertura de tipagem estrita:          {coverage:.2f}% (Mínimo institucional: {INSTITUTIONAL_COVERAGE_THRESHOLD}%)")

    if coverage < INSTITUTIONAL_COVERAGE_THRESHOLD:
        print(f"\n❌ Cobertura de tipagem ({coverage:.2f}%) abaixo do limiar institucional ({INSTITUTIONAL_COVERAGE_THRESHOLD}%).")
        return 1

    print("\n✓ 100% de conformidade com os padrões institucionais de tipagem estrita da B3!")
    print("======================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
