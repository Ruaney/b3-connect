#!/usr/bin/env python3
"""
Validação de integridade estrita do catálogo de integrações B3.

Espelha e expande as regras de conformidade de Test-Catalog.ps1:
1. Unicidade de IDs entre fontes e entre fluxos.
2. Campos obrigatórios de fontes (id, title, limitation).
3. Origem oficial de fontes (HTTPS e domínio b3.com.br ou subdomínio .b3.com.br).
4. Campos obrigatórios de fluxos (id, name, pending, fontes não-vazias).
5. Integridade referencial: todas as fontes de um fluxo devem existir em sources.json.
6. Tipagem estrita de estados ('mapped', 'documented', 'implemented', 'tested_locally', 'validated_b3').
7. Integridade de evidências: se estado != 'mapped' e for True, exige apontamento no dicionário de evidências.
8. (Opcional) Verificação de existência física dos arquivos de evidência no disco (--check-files).
"""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, List, Any, Optional, Tuple


VALID_STATES = [
    "mapped",
    "documented",
    "implemented",
    "tested_locally",
    "validated_b3",
]


class CatalogValidationError(Exception):
    """Exceção levantada quando uma regra de conformidade do catálogo é violada."""
    pass


def resolve_default_paths() -> Tuple[Path, Path]:
    """
    Localiza os arquivos sources.json e flows.json procurando em relação ao script
    ou diretório de trabalho atual.
    """
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent

    # Tenta ../catalog/ a partir do script
    sources_candidate = repo_root / "catalog" / "sources.json"
    flows_candidate = repo_root / "catalog" / "flows.json"

    if sources_candidate.is_file() and flows_candidate.is_file():
        return sources_candidate, flows_candidate

    # Tenta ./catalog a partir do diretório atual de trabalho
    cwd = Path.cwd()
    sources_cwd = cwd / "catalog" / "sources.json"
    flows_cwd = cwd / "catalog" / "flows.json"

    if sources_cwd.is_file() and flows_cwd.is_file():
        return sources_cwd, flows_cwd

    return sources_candidate, flows_candidate


def load_json_file(file_path: Path, entity_name: str) -> Dict[str, Any]:
    """Carrega e decodifica arquivo JSON com mensagem de erro amigável."""
    if not file_path.exists():
        raise CatalogValidationError(f"Arquivo de {entity_name} não encontrado: {file_path}")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as err:
        raise CatalogValidationError(
            f"Erro de sintaxe JSON no arquivo de {entity_name} ({file_path}): {err}"
        )
    except Exception as err:
        raise CatalogValidationError(
            f"Erro ao ler arquivo de {entity_name} ({file_path}): {err}"
        )


def validate_catalog(
    sources_path: Path,
    flows_path: Path,
    check_files: bool = False,
    repo_root: Optional[Path] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Executa a validação de integridade completa sobre os catálogos.

    Retorna um dicionário com estatísticas e detalhes da validação.
    Levanta CatalogValidationError em caso de violação de regra.
    """
    sources_data = load_json_file(sources_path, "fontes")
    flows_data = load_json_file(flows_path, "fluxos")

    sources: List[Dict[str, Any]] = sources_data.get("sources", [])
    flows: List[Dict[str, Any]] = flows_data.get("flows", [])

    if not isinstance(sources, list):
        raise CatalogValidationError("Formato inválido: campo 'sources' deve ser uma lista.")
    if not isinstance(flows, list):
        raise CatalogValidationError("Formato inválido: campo 'flows' deve ser uma lista.")

    # 1. Unicidade de IDs em fontes e fluxos
    for group_name, items in [("fontes", sources), ("fluxos", flows)]:
        seen_ids = set()
        duplicates = set()
        for item in items:
            item_id = item.get("id")
            if item_id in seen_ids:
                duplicates.add(item_id)
            seen_ids.add(item_id)
        if duplicates:
            raise CatalogValidationError(
                f"IDs duplicados em {group_name}: {', '.join(sorted(str(d) for d in duplicates))}"
            )

    # 2. Validação de fontes
    source_ids = set()
    for source in sources:
        sid = source.get("id")
        title = source.get("title")
        limitation = source.get("limitation")
        url = source.get("url")

        if not sid or not title or not limitation or not url:
            raise CatalogValidationError(f"Fonte incompleta: {sid or '(sem ID)'}")

        source_ids.add(sid)

        # Validação de URL oficial B3
        try:
            parsed = urlparse(url)
        except Exception as err:
            raise CatalogValidationError(f"URL inválida na fonte {sid}: {url} ({err})")

        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (host == "b3.com.br" or host.endswith(".b3.com.br")):
            raise CatalogValidationError(f"Fonte não oficial: {sid} (host={host}, url={url})")

        if verbose:
            print(f"[OK] Fonte verificada: {sid} - {title}")

    # 3. Validação de fluxos
    if repo_root is None:
        repo_root = sources_path.parent.parent

    for flow in flows:
        fid = flow.get("id")
        name = flow.get("name")
        pending = flow.get("pending")
        flow_sources = flow.get("sources")

        if not fid or not name or not pending or not isinstance(flow_sources, list) or len(flow_sources) == 0:
            raise CatalogValidationError(f"Fluxo incompleto: {fid or '(sem ID)'}")

        # Integridade referencial com fontes
        for sid in flow_sources:
            if sid not in source_ids:
                raise CatalogValidationError(f"Fonte inexistente: {sid} (referenciada pelo fluxo {fid})")

        # Validação de estados
        evidence_dict = flow.get("evidence", {}) or {}
        if not isinstance(evidence_dict, dict):
            raise CatalogValidationError(f"Campo 'evidence' deve ser um dicionário no fluxo {fid}")

        for state in VALID_STATES:
            if state not in flow:
                raise CatalogValidationError(f"Estado ausente: {fid}/{state}")

            val = flow[state]
            # Em Python bool é subclasse de int, verificar estritamente isinstance(val, bool)
            if not isinstance(val, bool):
                raise CatalogValidationError(f"Estado inválido: {fid}/{state} (valor={repr(val)})")

            if state != "mapped" and val is True:
                evidence_val = evidence_dict.get(state)
                if not evidence_val or not isinstance(evidence_val, str) or not evidence_val.strip():
                    raise CatalogValidationError(f"Falta evidência: {fid}/{state}")

                # Checagem opcional de arquivos físicos
                if check_files:
                    # Evidência pode conter múltiplos arquivos separados por vírgula
                    file_tokens = [t.strip() for t in evidence_val.split(",") if t.strip()]
                    for token in file_tokens:
                        file_candidate = repo_root / token
                        if not file_candidate.exists():
                            raise CatalogValidationError(
                                f"Arquivo de evidência não encontrado no disco para {fid}/{state}: "
                                f"{token} (procurado em {file_candidate})"
                            )

        if verbose:
            print(f"[OK] Fluxo verificado: {fid} - {name}")

    return {
        "status": "valid",
        "sources_count": len(sources),
        "flows_count": len(flows),
        "sources_version": sources_data.get("version"),
        "flows_version": flows_data.get("version"),
    }


def parse_arguments() -> argparse.Namespace:
    default_sources, default_flows = resolve_default_paths()
    parser = argparse.ArgumentParser(
        description="Validador de integridade estrita do catálogo de integrações B3 (sources.json e flows.json)."
    )
    parser.add_argument(
        "-s",
        "--sources",
        type=Path,
        default=default_sources,
        help=f"Caminho para sources.json (padrão: {default_sources})",
    )
    parser.add_argument(
        "-f",
        "--flows",
        type=Path,
        default=default_flows,
        help=f"Caminho para flows.json (padrão: {default_flows})",
    )
    parser.add_argument(
        "--check-files",
        action="store_true",
        help="Verifica se os caminhos apontados nas evidências existem fisicamente no repositório.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Exibe detalhes de cada elemento validado.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Modo silencioso; suprime saídas normais e exibe apenas erros se houver falha.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Imprime resultado em formato JSON estruturado.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    try:
        results = validate_catalog(
            sources_path=args.sources,
            flows_path=args.flows,
            check_files=args.check_files,
            verbose=args.verbose,
        )

        if args.output_json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        elif not args.quiet:
            print(
                f"Integridade verificada: {results['sources_count']} fontes; {results['flows_count']} fluxos."
            )
        return 0

    except CatalogValidationError as err:
        if args.output_json:
            print(json.dumps({"status": "error", "message": str(err)}, indent=2, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"ERRO DE INTEGRIDADE: {err}", file=sys.stderr)
        return 1
    except Exception as err:
        if args.output_json:
            print(json.dumps({"status": "error", "message": f"Erro inesperado: {err}"}, indent=2, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"ERRO INESPERADO: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
