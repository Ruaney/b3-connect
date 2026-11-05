"""B3 Integration Atlas CLI.

Interface de linha de comando institucional para consulta e inspeção do catálogo
de integrações B3, fontes normativas e execução do verificador formal de invariantes.

Zero dependências externas: 100% biblioteca padrão do Python.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Constantes do Catálogo
VALID_STATES = [
    "mapped",
    "documented",
    "implemented",
    "tested_locally",
    "validated_b3",
]


def resolve_catalog_dir(custom_path: Optional[str | Path] = None) -> Path:
    """Localiza o diretório catalog contendo flows.json e sources.json."""
    if custom_path:
        p = Path(custom_path).resolve()
        if p.is_dir():
            return p

    env_dir = os.environ.get("B3_CATALOG_DIR")
    if env_dir:
        p = Path(env_dir).resolve()
        if p.is_dir():
            return p

    # Diretório relativo ao pacote instalado / repositório
    pkg_root = Path(__file__).resolve().parent.parent
    candidate_repo = pkg_root / "catalog"
    if candidate_repo.is_dir():
        return candidate_repo

    # Diretório de trabalho atual
    cwd_candidate = Path.cwd() / "catalog"
    if cwd_candidate.is_dir():
        return cwd_candidate

    return candidate_repo


def resolve_fixtures_dir(custom_path: Optional[str | Path] = None) -> Path:
    """Localiza o diretório fixtures para testes de invariantes."""
    if custom_path:
        p = Path(custom_path).resolve()
        if p.is_dir():
            return p

    env_dir = os.environ.get("B3_FIXTURES_DIR")
    if env_dir:
        p = Path(env_dir).resolve()
        if p.is_dir():
            return p

    pkg_root = Path(__file__).resolve().parent.parent
    candidate_repo = pkg_root / "fixtures"
    if candidate_repo.is_dir():
        return candidate_repo

    cwd_candidate = Path.cwd() / "fixtures"
    if cwd_candidate.is_dir():
        return cwd_candidate

    return candidate_repo


def load_catalog(catalog_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Carrega flows.json e sources.json a partir do diretório fornecido."""
    flows_path = catalog_dir / "flows.json"
    sources_path = catalog_dir / "sources.json"

    if not flows_path.is_file():
        raise FileNotFoundError(f"Arquivo flows.json não encontrado em: {flows_path}")
    if not sources_path.is_file():
        raise FileNotFoundError(f"Arquivo sources.json não encontrado em: {sources_path}")

    with open(flows_path, "r", encoding="utf-8") as f:
        flows_data = json.load(f)
    with open(sources_path, "r", encoding="utf-8") as f:
        sources_data = json.load(f)

    return flows_data, sources_data


# ==============================================================================
# Subcomandos da CLI Atlas
# ==============================================================================


def cmd_list_flows(args: argparse.Namespace) -> int:
    """Lista fluxos cadastrados com opções de filtragem e formatação."""
    catalog_dir = resolve_catalog_dir(args.catalog_dir)
    flows_data, _ = load_catalog(catalog_dir)
    flows: List[Dict[str, Any]] = flows_data.get("flows", [])

    filtered: List[Dict[str, Any]] = []
    system_filter = (args.system or "").lower().strip()
    search_query = (args.query or "").lower().strip()
    status_filter = (args.status or "").lower().strip()

    for f in flows:
        fid = str(f.get("id", "")).lower()
        fname = str(f.get("name", "")).lower()
        fsystem = str(f.get("system", "")).lower()

        if system_filter and system_filter not in fsystem:
            continue
        if search_query and (search_query not in fid and search_query not in fname):
            continue
        if status_filter:
            # Se filtrou por um estado específico, exige que ele seja True
            if not f.get(status_filter, False):
                continue
        filtered.append(f)

    if args.json:
        print(json.dumps(filtered, indent=2, ensure_ascii=False))
        return 0

    print(f"\nB3 Integration Atlas — {len(filtered)} fluxos encontrados (de {len(flows)} totais):")
    print("-" * 88)
    print(f"{'ID':<8} {'Sistema':<12} {'Interface':<12} {'Nome':<42} {'Status'}")
    print("-" * 88)

    for f in filtered:
        fid = f.get("id", "")
        fsys = f.get("system", "")
        fiface = f.get("interface", "")
        fname = f.get("name", "")
        if len(fname) > 40:
            fname = fname[:37] + "..."

        status_flags = []
        if f.get("implemented"):
            status_flags.append("IMPL")
        if f.get("tested_locally"):
            status_flags.append("TEST")
        if f.get("validated_b3"):
            status_flags.append("CERT")
        status_str = f"[{'|'.join(status_flags)}]" if status_flags else "[MAP]"

        print(f"{fid:<8} {fsys:<12} {fiface:<12} {fname:<42} {status_str}")

    print("-" * 88)
    return 0


def cmd_flow_info(args: argparse.Namespace) -> int:
    """Exibe detalhes aprofundados sobre um fluxo específico."""
    catalog_dir = resolve_catalog_dir(args.catalog_dir)
    flows_data, sources_data = load_catalog(catalog_dir)
    flows: List[Dict[str, Any]] = flows_data.get("flows", [])
    sources_map = {s.get("id"): s for s in sources_data.get("sources", [])}

    target_id = args.flow_id.strip().upper()
    found = None
    for f in flows:
        if f.get("id", "").upper() == target_id:
            found = f
            break

    if not found:
        sys.stderr.write(f"Erro: Fluxo '{target_id}' não encontrado no catálogo B3.\n")
        return 1

    if args.json:
        # Enriquece com dados das fontes
        enriched = dict(found)
        enriched["sources_detail"] = [
            sources_map.get(sid, {"id": sid}) for sid in found.get("sources", [])
        ]
        print(json.dumps(enriched, indent=2, ensure_ascii=False))
        return 0

    print("\n" + "=" * 80)
    print(f" DETALHES DO FLUXO: {found.get('id')} — {found.get('name')}")
    print("=" * 80)
    print(f"  Sistema:       {found.get('system')}")
    print(f"  Interface:     {found.get('interface')}")
    print(f"  Confiança:     {found.get('confidence')}")
    print(f"  Pendente:      {'Sim' if found.get('pending') else 'Não (Concluído)'}")
    print("\n  Estágios de Homologação:")
    for st in VALID_STATES:
        ok = found.get(st, False)
        icon = "✓" if ok else "✗"
        print(f"    [{icon}] {st:<18}: {ok}")

    evidence = found.get("evidence", {})
    if evidence:
        print("\n  Arquivos de Evidência e Rastreabilidade:")
        for k, v in evidence.items():
            print(f"    - {k}: {v}")

    src_ids = found.get("sources", [])
    if src_ids:
        print("\n  Fontes Oficiais B3 Referenciadas:")
        for sid in src_ids:
            src_info = sources_map.get(sid, {})
            title = src_info.get("title", "Desconhecido")
            url = src_info.get("url", "")
            print(f"    - [{sid}] {title}")
            if url:
                print(f"      URL: {url}")

    print("=" * 80 + "\n")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """Exibe estatísticas gerais e métricas de cobertura de governança B3."""
    catalog_dir = resolve_catalog_dir(args.catalog_dir)
    flows_data, sources_data = load_catalog(catalog_dir)
    flows: List[Dict[str, Any]] = flows_data.get("flows", [])
    sources: List[Dict[str, Any]] = sources_data.get("sources", [])

    total_flows = len(flows)
    total_sources = len(sources)

    stage_counts = {st: sum(1 for f in flows if f.get(st)) for st in VALID_STATES}
    pending_count = sum(1 for f in flows if f.get("pending"))

    systems: Dict[str, int] = {}
    for f in flows:
        sys_name = f.get("system", "Outros") or "Outros"
        systems[sys_name] = systems.get(sys_name, 0) + 1

    source_kinds: Dict[str, int] = {}
    for s in sources:
        kind = s.get("kind", "outro") or "outro"
        source_kinds[kind] = source_kinds.get(kind, 0) + 1

    stats_obj = {
        "catalog_version": flows_data.get("version"),
        "total_flows": total_flows,
        "total_sources": total_sources,
        "pending_flows": pending_count,
        "stages": stage_counts,
        "flows_by_system": systems,
        "sources_by_kind": source_kinds,
    }

    if args.json:
        print(json.dumps(stats_obj, indent=2, ensure_ascii=False))
        return 0

    print("\n" + "=" * 70)
    print(" B3 INTEGRATION ATLAS — ESTATÍSTICAS E MÉTRICAS DE COBERTURA")
    print("=" * 70)
    print(f"  Versão do Catálogo:      {flows_data.get('version', 'N/A')}")
    print(f"  Total de Fluxos Mapeados: {total_flows}")
    print(f"  Total de Fontes Oficiais: {total_sources}")
    print(f"  Fluxos com Pendências:   {pending_count}")
    print("\n  Progresso por Estágio:")
    for st, count in stage_counts.items():
        pct = (count / total_flows * 100) if total_flows else 0
        bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
        print(f"    {st:<16} : {count:3d}/{total_flows} ({pct:5.1f}%) [{bar}]")

    print("\n  Distribuição por Sistema:")
    for sname, scount in sorted(systems.items(), key=lambda x: x[1], reverse=True):
        print(f"    - {sname:<20}: {scount:2d} fluxos")

    print("\n  Tipos de Fontes B3:")
    for kname, kcount in sorted(source_kinds.items(), key=lambda x: x[1], reverse=True):
        print(f"    - {kname:<20}: {kcount:2d} documentos")

    print("=" * 70 + "\n")
    return 0


def cmd_list_sources(args: argparse.Namespace) -> int:
    """Lista fontes oficiais catalogadas com suporte a busca."""
    catalog_dir = resolve_catalog_dir(args.catalog_dir)
    _, sources_data = load_catalog(catalog_dir)
    sources: List[Dict[str, Any]] = sources_data.get("sources", [])

    search_query = (args.query or "").lower().strip()
    kind_filter = (args.kind or "").lower().strip()

    filtered: List[Dict[str, Any]] = []
    for s in sources:
        sid = str(s.get("id", "")).lower()
        title = str(s.get("title", "")).lower()
        kind = str(s.get("kind", "")).lower()

        if kind_filter and kind_filter != kind:
            continue
        if search_query and (search_query not in sid and search_query not in title):
            continue
        filtered.append(s)

    if args.json:
        print(json.dumps(filtered, indent=2, ensure_ascii=False))
        return 0

    print(f"\nB3 Integration Atlas — {len(filtered)} fontes encontradas:")
    print("-" * 88)
    print(f"{'ID':<8} {'Tipo':<12} {'Título':<64}")
    print("-" * 88)
    for s in filtered:
        sid = s.get("id", "")
        kind = s.get("kind", "")
        title = s.get("title", "")
        if len(title) > 62:
            title = title[:59] + "..."
        print(f"{sid:<8} {kind:<12} {title:<64}")
    print("-" * 88)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Executa a verificação formal de invariantes da B3."""
    fixtures_dir = resolve_fixtures_dir(getattr(args, "fixtures_dir", None))
    if not fixtures_dir.is_dir():
        sys.stderr.write(f"Erro: Diretório de fixtures não encontrado: {fixtures_dir}\n")
        return 1

    # Tenta importar dinamicamente o verificador de invariantes oficial
    repo_root = fixtures_dir.parent
    scripts_dir = repo_root / "scripts"
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    try:
        import verify_invariants  # type: ignore

        # Chama a execução principal do script de verificação
        verify_invariants.main()
        return 0
    except SystemExit as se:
        return int(se.code) if se.code is not None else 0
    except Exception as exc:
        sys.stderr.write(f"Falha na execução da verificação de invariantes: {exc}\n")
        return 1


# ==============================================================================
# Entrypoints dos CLI Scripts
# ==============================================================================


def build_atlas_parser() -> argparse.ArgumentParser:
    """Constrói o analisador de argumentos da CLI b3-atlas."""
    parser = argparse.ArgumentParser(
        prog="b3-atlas",
        description="B3 Integration Atlas — CLI Interativa de Catálogo e Fontes B3",
    )
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=None,
        help="Caminho personalizado para a pasta catalog (contendo flows.json e sources.json)",
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Comandos disponíveis")

    # b3-atlas list-flows
    p_flows = subparsers.add_parser("list-flows", help="Lista fluxos da plataforma B3")
    p_flows.add_argument("-s", "--system", help="Filtrar por sistema (ex: PUMA, NoMe, RTC, IMBARQ)")
    p_flows.add_argument("-q", "--query", help="Buscar termo em ID ou Nome do fluxo")
    p_flows.add_argument(
        "--status",
        choices=VALID_STATES,
        help="Filtrar por estágio concluído (ex: implemented, tested_locally)",
    )
    p_flows.add_argument("--json", action="store_true", help="Saída em formato JSON")
    p_flows.set_defaults(func=cmd_list_flows)

    # b3-atlas info <flow_id>
    p_info = subparsers.add_parser("info", help="Exibe detalhes completos de um fluxo")
    p_info.add_argument("flow_id", help="Identificador do fluxo (ex: FL-001)")
    p_info.add_argument("--json", action="store_true", help="Saída em formato JSON")
    p_info.set_defaults(func=cmd_flow_info)

    # b3-atlas stats
    p_stats = subparsers.add_parser("stats", help="Exibe métricas e cobertura do atlas")
    p_stats.add_argument("--json", action="store_true", help="Saída em formato JSON")
    p_stats.set_defaults(func=cmd_stats)

    # b3-atlas list-sources
    p_src = subparsers.add_parser("list-sources", help="Lista fontes normativas B3")
    p_src.add_argument("-q", "--query", help="Buscar termo em ID ou Título da fonte")
    p_src.add_argument("-k", "--kind", help="Filtrar por tipo (manual, regulamento, catalogo, etc.)")
    p_src.add_argument("--json", action="store_true", help="Saída em formato JSON")
    p_src.set_defaults(func=cmd_list_sources)

    # b3-atlas verify
    p_verify = subparsers.add_parser("verify", help="Executa a suíte de verificação formal de invariantes")
    p_verify.add_argument("--fixtures-dir", type=Path, default=None, help="Caminho para fixtures")
    p_verify.set_defaults(func=cmd_verify)

    return parser


def build_verify_parser() -> argparse.ArgumentParser:
    """Constrói o analisador para o executável direto b3-verify."""
    parser = argparse.ArgumentParser(
        prog="b3-verify",
        description="B3 Verification Suite — Execução Formal de Invariantes e Leis de Conservação",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=None,
        help="Caminho personalizado para o diretório de fixtures",
    )
    return parser


def main_atlas(argv: Optional[Sequence[str]] = None) -> int:
    """Entrypoint do comando b3-atlas."""
    parser = build_atlas_parser()
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        return 0

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0

    try:
        return args.func(args)
    except FileNotFoundError as err:
        sys.stderr.write(f"Erro: {err}\n")
        return 1
    except Exception as err:
        sys.stderr.write(f"Erro inesperado: {err}\n")
        return 1


def main_verify(argv: Optional[Sequence[str]] = None) -> int:
    """Entrypoint do comando direto b3-verify."""
    parser = build_verify_parser()
    if argv is None:
        argv = sys.argv[1:]
    args = parser.parse_args(argv)
    return cmd_verify(args)


if __name__ == "__main__":
    if Path(sys.argv[0]).stem == "b3-verify":
        sys.exit(main_verify())
    else:
        sys.exit(main_atlas())
