#!/usr/bin/env python3
"""
Cálculo de cobertura do inventário de fluxos de integração B3.

Espelha e expande as métricas de Get-Coverage.ps1:
1. Carrega catalog/flows.json.
2. Contabiliza os estados ('mapped', 'documented', 'implemented', 'tested_locally', 'validated_b3').
3. Calcula os percentuais em relação ao total de fluxos do inventário conhecido.
4. Suporta modos de saída: texto padrão (idêntico ao PowerShell), JSON estruturado, detalhado e tabela Markdown.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple


TRACKED_STATES = [
    ("mapped", "Mapeado no inventário"),
    ("documented", "Especificado tecnicamente"),
    ("implemented", "Código implementado"),
    ("tested_locally", "Testado localmente com fixtures"),
    ("validated_b3", "Homologado em ambiente B3"),
]


def resolve_default_flows_path() -> Path:
    """Localiza o arquivo flows.json a partir do script ou diretório de trabalho."""
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    candidate = repo_root / "catalog" / "flows.json"
    if candidate.is_file():
        return candidate

    cwd_candidate = Path.cwd() / "catalog" / "flows.json"
    if cwd_candidate.is_file():
        return cwd_candidate

    return candidate


def calculate_coverage(flows_path: Path) -> Dict[str, Any]:
    """Lê o arquivo de fluxos e calcula as métricas de cobertura por estado."""
    if not flows_path.is_file():
        raise FileNotFoundError(f"Arquivo de catálogo não encontrado: {flows_path}")

    with open(flows_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    flows: List[Dict[str, Any]] = catalog.get("flows", [])
    version = catalog.get("version", "desconhecida")
    total_flows = len(flows)

    coverage_by_state = {}
    for state_key, label in TRACKED_STATES:
        matching_flows = [flow for flow in flows if flow.get(state_key) is True]
        count = len(matching_flows)
        raw_pct = (100.0 * count / total_flows) if total_flows > 0 else 0.0
        rounded_pct = round(raw_pct, 1)

        coverage_by_state[state_key] = {
            "label": label,
            "count": count,
            "total": total_flows,
            "percent": rounded_pct,
            "flow_ids": [f.get("id") for f in matching_flows],
        }

    return {
        "version": version,
        "as_of": catalog.get("as_of"),
        "scope": catalog.get("scope"),
        "total_flows": total_flows,
        "coverage": coverage_by_state,
        "flows": flows,
    }


def format_percent(pct: float) -> str:
    """Formata percentual: número inteiro se não tiver casas decimais, ou 1 casa decimal."""
    if pct.is_integer():
        return f"{int(pct)}%"
    return f"{pct:.1f}%"


def print_standard_output(data: Dict[str, Any]) -> None:
    """Imprime saída padrão idêntica ao formato do Get-Coverage.ps1."""
    version = data["version"]
    total = data["total_flows"]
    print(f"Inventário {version}: {total} fluxos conhecidos; descoberta incompleta.")

    for state_key, _ in TRACKED_STATES:
        info = data["coverage"][state_key]
        count = info["count"]
        pct_str = format_percent(info["percent"])
        print(f"{state_key}: {count}/{total} ({pct_str})")

    print("Percentuais relativos ao inventário, não à totalidade da B3.")


def print_detailed_output(data: Dict[str, Any]) -> None:
    """Imprime análise aprofundada com discriminação por fluxo e pendências."""
    print_standard_output(data)
    print("\n" + "=" * 60)
    print("DETALHAMENTO POR FLUXO:")
    print("=" * 60)

    for flow in data["flows"]:
        fid = flow.get("id", "???")
        name = flow.get("name", "Sem nome")
        system = flow.get("system", "N/A")
        interface = flow.get("interface", "N/A")

        status_indicators = []
        for state_key, _ in TRACKED_STATES:
            is_active = flow.get(state_key) is True
            status_indicators.append(f"{state_key}: {'✓' if is_active else '✗'}")

        print(f"\n[{fid}] {name}")
        print(f"  Sistema: {system} | Interface: {interface}")
        print(f"  Status:  {' | '.join(status_indicators)}")
        pending = flow.get("pending")
        if pending:
            print(f"  Pendente: {pending}")


def print_markdown_output(data: Dict[str, Any]) -> None:
    """Gera tabela em Markdown para documentação."""
    total = data["total_flows"]
    print(f"### Cobertura do Inventário B3 (v{data['version']})\n")
    print("| Estado | Descrição | Concluídos | Total | Cobertura |")
    print("|---|---|:---:|:---:|:---:|")
    for state_key, label in TRACKED_STATES:
        info = data["coverage"][state_key]
        pct_str = format_percent(info["percent"])
        print(f"| `{state_key}` | {label} | {info['count']} | {total} | **{pct_str}** |")
    print("\n*Nota: Percentuais relativos ao inventário conhecido, não à totalidade da B3.*")


def parse_arguments() -> argparse.Namespace:
    default_flows = resolve_default_flows_path()
    parser = argparse.ArgumentParser(
        description="Calcula a cobertura do inventário de fluxos B3 a partir de flows.json."
    )
    parser.add_argument(
        "-f",
        "--flows",
        type=Path,
        default=default_flows,
        help=f"Caminho para o arquivo flows.json (padrão: {default_flows})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Formata a saída como JSON estruturado para pipelines CI/CD.",
    )
    parser.add_argument(
        "-d",
        "--detailed",
        action="store_true",
        help="Exibe discriminação detalhada por fluxo e pendências.",
    )
    parser.add_argument(
        "-m",
        "--markdown",
        action="store_true",
        help="Exibe o resultado em formato de tabela Markdown.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    try:
        data = calculate_coverage(args.flows)

        if args.output_json:
            # Serializa sem a chave 'flows' se não for necessário detalhe excessivo
            export_data = {
                "version": data["version"],
                "as_of": data["as_of"],
                "scope": data["scope"],
                "total_flows": data["total_flows"],
                "coverage": {
                    k: {
                        "count": v["count"],
                        "total": v["total"],
                        "percent": v["percent"],
                        "flow_ids": v["flow_ids"],
                    }
                    for k, v in data["coverage"].items()
                },
            }
            print(json.dumps(export_data, indent=2, ensure_ascii=False))
        elif args.markdown:
            print_markdown_output(data)
        elif args.detailed:
            print_detailed_output(data)
        else:
            print_standard_output(data)

        return 0

    except FileNotFoundError as err:
        print(f"ERRO: {err}", file=sys.stderr)
        return 1
    except Exception as err:
        print(f"ERRO INESPERADO: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
