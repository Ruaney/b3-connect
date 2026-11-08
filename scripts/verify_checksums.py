#!/usr/bin/env python3
"""B3 Integration Atlas & Enterprise Platform — Validador Criptográfico de Checksums SHA-256.

Audita a integridade estrita de todos os arquivos de código-fonte, especificações,
manuais normativos, relatórios e fixtures do ecossistema institucional v2.3.0.
100% Python Standard Library (Zero External Dependencies).
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Set, Tuple


def compute_sha256(file_path: Path) -> str:
    """Calcula o hash SHA-256 de um arquivo em chunks binários de 64 KB."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def scan_target_files(
    repo_root: Path,
    include_patterns: Optional[List[str]] = None,
    exclude_dirs: Optional[Set[str]] = None,
) -> List[Path]:
    """Descobre deterministicamente todos os arquivos rastreáveis do repositório."""
    if exclude_dirs is None:
        exclude_dirs = {
            ".git",
            "__pycache__",
            ".pytest_cache",
            "node_modules",
            ".idea",
            ".vscode",
            ".cache",
            "dist-tmp",
            "logs",
        }

    candidate_files: List[Path] = []
    manifest_name = "CHECKSUMS_SHA256_v2.3.0.txt"

    for root, dirs, files in os.walk(repo_root):
        # Filtra diretórios excluídos in-place para podar a árvore
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith(".git")]
        rel_root = Path(root).relative_to(repo_root)

        for filename in files:
            if filename == manifest_name or filename.endswith(".pyc") or filename.endswith(".DS_Store"):
                continue

            full_path = Path(root) / filename
            rel_path = full_path.relative_to(repo_root)
            candidate_files.append(rel_path)

    candidate_files.sort(key=lambda p: p.as_posix())
    return [repo_root / p for p in candidate_files]


def generate_manifest(
    repo_root: Path,
    manifest_path: Path,
    repo_name: str = "b3-integration-atlas",
) -> Tuple[int, Path]:
    """Gera o manifesto criptográfico SHA-256 oficial para o repositório."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    files = scan_target_files(repo_root)

    lines: List[str] = [
        f"# B3 Enterprise Platform & Integration Atlas — SHA-256 Cryptographic Release Manifest",
        f"# Release: v2.3.0 | Repository: {repo_name}",
        f"# Algorithm: SHA-256 (FIPS 180-4) | Formato: <sha256>  <relative_path>",
        f"# Total Files Covered: {len(files)}",
        f"#",
    ]

    for fpath in files:
        rel_path = fpath.relative_to(repo_root).as_posix()
        sha = compute_sha256(fpath)
        lines.append(f"{sha}  {rel_path}")

    content = "\n".join(lines) + "\n"
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

    return len(files), manifest_path


def verify_manifest(
    manifest_path: Path,
    base_dir: Optional[Path] = None,
    verbose: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """Valida cada entrada do manifesto contra o sistema de arquivos local."""
    if base_dir is None:
        base_dir = manifest_path.parent.parent

    if not manifest_path.is_file():
        return False, {
            "error": f"Manifesto não encontrado: {manifest_path}",
            "passed": 0,
            "failed": 0,
            "missing": 0,
            "total": 0,
            "corrupted_files": [],
            "missing_files": [],
        }

    total = 0
    passed = 0
    failed = 0
    missing = 0
    corrupted_files: List[Tuple[str, str, str]] = []
    missing_files: List[str] = []

    with open(manifest_path, "r", encoding="utf-8") as f:
        for line_num, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue

            expected_sha, rel_path = parts[0].strip(), parts[1].strip()
            # Limpa eventual marcador binário/texto '*path' ou ' path'
            if rel_path.startswith("*"):
                rel_path = rel_path[1:]

            target_path = base_dir / rel_path
            total += 1

            if not target_path.exists():
                missing += 1
                missing_files.append(rel_path)
                if verbose:
                    print(f"[MISSING] Linha {line_num}: Arquivo ausente: {rel_path}")
                continue

            actual_sha = compute_sha256(target_path)
            if actual_sha.lower() == expected_sha.lower():
                passed += 1
                if verbose:
                    print(f"[OK] {rel_path}")
            else:
                failed += 1
                corrupted_files.append((rel_path, expected_sha, actual_sha))
                if verbose:
                    print(f"[FAIL] Linha {line_num}: {rel_path}")
                    print(f"       Esperado: {expected_sha}")
                    print(f"       Obtido:   {actual_sha}")

    success = (failed == 0 and missing == 0 and total > 0)
    summary = {
        "success": success,
        "total": total,
        "passed": passed,
        "failed": failed,
        "missing": missing,
        "corrupted_files": corrupted_files,
        "missing_files": missing_files,
    }
    return success, summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auditoria e Verificação de Checksums Criptográficos SHA-256 (Release v2.3.0)"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Caminho para o arquivo CHECKSUMS_SHA256_v2.3.0.txt",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Diretório raiz do repositório a auditar",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Gera ou atualiza o manifesto com os hashes atuais",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Exibe logs detalhados de cada arquivo verificado",
    )

    args = parser.parse_args()

    # Detecta automaticamente a raiz do repo a partir da localização do script
    script_dir = Path(__file__).resolve().parent
    repo_root = args.repo_root or script_dir.parent
    manifest_path = args.manifest or (repo_root / "docs" / "CHECKSUMS_SHA256_v2.3.0.txt")

    if args.generate:
        print(f"Gerando manifesto SHA-256 em: {manifest_path} ...")
        repo_name = repo_root.name
        count, path = generate_manifest(repo_root, manifest_path, repo_name)
        print(f"✅ Manifesto gerado com sucesso! Total de arquivos cobertos: {count}")
        return 0

    print(f"================================================================================")
    print(f" B3 RELEASE AUDITOR — VERIFICAÇÃO CRIPTOGRÁFICA SHA-256 v2.3.0")
    print(f" Repositório: {repo_root}")
    print(f" Manifesto:   {manifest_path}")
    print(f"================================================================================")

    success, summary = verify_manifest(manifest_path, repo_root, verbose=args.verbose)

    print(f"\n--- Resultado da Auditoria ---")
    print(f"Total de Arquivos Auditados: {summary['total']}")
    print(f"Verificados com Sucesso:    {summary['passed']}")
    print(f"Corrompidos / Divergentes:  {summary['failed']}")
    print(f"Arquivos Ausentes:          {summary['missing']}")

    if success:
        print(f"\n✨ SUCESSO: Todos os {summary['total']} arquivos conferem 100% com o manifesto criptográfico!")
        return 0
    else:
        print(f"\n❌ FALHA: Foram detectadas divergências de integridade!")
        if summary["missing_files"]:
            print(f"Arquivos ausentes ({len(summary['missing_files'])}):")
            for m in summary["missing_files"][:10]:
                print(f" - {m}")
        if summary["corrupted_files"]:
            print(f"Arquivos corrompidos ({len(summary['corrupted_files'])}):")
            for rel, exp, act in summary["corrupted_files"][:10]:
                print(f" - {rel} (esperado: {exp[:12]}..., atual: {act[:12]}...)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
