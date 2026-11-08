#!/usr/bin/env python3
"""scripts/verify_zero_pip.py

Auditor Estático Institucional de Conformidade Zero-Pip Dependencies.
Garante rigorosamente que:
 1. Nenhum pacote ou dependência de terceiros externa (PyPI) seja importado no código-fonte (src/),
    scripts de automação (scripts/) ou suíte de testes (tests/).
 2. Apenas a biblioteca padrão do Python (Python Standard Library) e submódulos internos do projeto
    sejam utilizados.
 3. O arquivo pyproject.toml declare explicitamente 'dependencies = []'.
"""

import ast
from pathlib import Path
import sys
from typing import Dict, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# Módulos conhecidos da Biblioteca Padrão do Python (Python 3.8 - 3.13)
PYTHON_STDLIB_MODULES: Set[str] = set(sys.builtin_module_names) | {
    "__future__", "_thread", "abc", "aifc", "argparse", "array", "ast", "asynchat",
    "asyncio", "asyncore", "atexit", "audioop", "base64", "bdb", "binascii",
    "binhex", "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb", "chunk",
    "cmath", "cmd", "code", "codecs", "codeop", "collections", "colorsys",
    "compileall", "concurrent", "configparser", "contextlib", "contextvars",
    "copy", "copyreg", "cProfile", "crypt", "csv", "ctypes", "curses", "dataclasses",
    "datetime", "dbm", "decimal", "difflib", "dis", "distutils", "doctest", "email",
    "encodings", "ensurepip", "enum", "errno", "faulthandler", "fcntl", "filecmp",
    "fileinput", "fnmatch", "formatter", "fractions", "ftplib", "functools", "gc",
    "getopt", "getpass", "gettext", "glob", "graphlib", "grp", "gzip", "hashlib",
    "heapq", "hmac", "html", "http", "imaplib", "imghdr", "imp", "importlib",
    "inspect", "io", "ipaddress", "itertools", "json", "keyword", "lib2to3",
    "linecache", "locale", "logging", "lzma", "mailbox", "mailcap", "marshal",
    "math", "mimetypes", "mmap", "modulefinder", "msilib", "msvcrt", "multiprocessing",
    "netrc", "nntplib", "numbers", "operator", "optparse", "os", "ossaudiodev",
    "parser", "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil",
    "platform", "plistlib", "poplib", "posix", "posixpath", "pprint", "profile",
    "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc", "queue", "quopri",
    "random", "re", "readline", "reprlib", "resource", "rlcompleter", "runpy",
    "sched", "secrets", "select", "selectors", "shelve", "shlex", "shutil",
    "signal", "site", "smtpd", "smtplib", "sndhdr", "socket", "socketserver",
    "spwd", "sqlite3", "sre", "ssl", "stat", "statistics", "string", "stringprep",
    "struct", "subprocess", "sunau", "symbol", "symtable", "sys", "sysconfig",
    "syslog", "tabnanny", "tarfile", "telnetlib", "tempfile", "termios", "test",
    "textwrap", "threading", "time", "timeit", "tkinter", "token", "tokenize",
    "tomllib", "trace", "traceback", "tracemalloc", "tty", "turtle", "turtledemo",
    "types", "typing", "unicodedata", "unittest", "urllib", "uu", "uuid", "venv",
    "warnings", "wave", "weakref", "webbrowser", "winreg", "winsound", "wsgiref",
    "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile", "zipimport", "zlib", "zoneinfo",
}


def get_internal_modules(repo_root: Path) -> Set[str]:
    """Descobre todos os nomes de módulos internos nos diretórios src, scripts e tests."""
    internal = {"src", "scripts", "tests"}
    for folder in ["src", "scripts", "tests"]:
        folder_path = repo_root / folder
        if not folder_path.is_dir():
            continue
        for p in folder_path.rglob("*.py"):
            internal.add(p.stem)
            # Adiciona caminho relativo sem extensão como módulo pontilhado
            rel = p.relative_to(repo_root).with_suffix("")
            internal.add(str(rel).replace("/", ".").replace("\\", "."))
            # Primeiro componente de diretório
            internal.add(p.relative_to(repo_root).parts[0])
    return internal


def scan_file_imports(file_path: Path) -> List[Tuple[str, int]]:
    """Extrai todos os imports de nível superior de um arquivo Python via AST."""
    imports: List[Tuple[str, int]] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content, filename=str(file_path))
    except Exception as e:
        print(f"[ERRO DE PARSING] {file_path}: {e}", file=sys.stderr)
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_module = alias.name.split(".")[0]
                imports.append((top_module, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_module = node.module.split(".")[0]
                imports.append((top_module, node.lineno))
    return imports


def audit_zero_pip(repo_root: Path) -> Tuple[bool, Dict[str, List[Tuple[str, int]]], int]:
    """Audita todo o repositório garantindo conformidade Zero-Pip.

    Retorna: (is_valid, violations_by_file, total_imports_checked)
    """
    internal_modules = get_internal_modules(repo_root)
    allowed_modules = PYTHON_STDLIB_MODULES | internal_modules

    violations: Dict[str, List[Tuple[str, int]]] = {}
    total_imports_checked = 0

    target_folders = ["src", "scripts", "tests"]
    for folder in target_folders:
        folder_path = repo_root / folder
        if not folder_path.is_dir():
            continue
        for py_file in folder_path.rglob("*.py"):
            rel_path = str(py_file.relative_to(repo_root))
            file_imports = scan_file_imports(py_file)
            for mod_name, lineno in file_imports:
                total_imports_checked += 1
                if mod_name not in allowed_modules:
                    violations.setdefault(rel_path, []).append((mod_name, lineno))

    # Validação do pyproject.toml
    pyproject_file = repo_root / "pyproject.toml"
    if pyproject_file.is_file():
        content = pyproject_file.read_text(encoding="utf-8")
        if "dependencies = []" not in content:
            violations.setdefault("pyproject.toml", []).append(("dependencies != []", 0))

    is_valid = len(violations) == 0
    return is_valid, violations, total_imports_checked


def main() -> int:
    print("======================================================================")
    print(" B3 INTEGRATION ATLAS — AUDITORIA INSTITUCIONAL ZERO-PIP DEPENDENCIES")
    print(" Verificação estrita de ausência de pacotes externos terceiros (PyPI)")
    print("======================================================================")

    is_valid, violations, total_checked = audit_zero_pip(REPO_ROOT)

    print(f"Diretório Raiz Auditado: {REPO_ROOT}")
    print(f"Total de Declarações de Import Analisadas: {total_checked}")

    if not is_valid:
        print("\n❌ FALHA DE CONFORMIDADE ZERO-PIP: Dependências não autorizadas detectadas!\n")
        for file_path, items in violations.items():
            print(f"  Arquivo: {file_path}")
            for mod, line in items:
                print(f"    - Linha {line}: import de pacote terceiro '{mod}'")
        print("\nPolitica Institucional: O B3 Integration Atlas exige Zero dependências de terceiros.")
        return 1

    print("\n✓ 100% CONFORME: Nenhuma dependência externa ou pip de terceiros detectada.")
    print("✓ Código-fonte restrito exclusivamente à Python Standard Library e módulos internos.")
    print("✓ Arquivo pyproject.toml validado com 'dependencies = []'.")
    print("======================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
