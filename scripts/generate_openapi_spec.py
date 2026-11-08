#!/usr/bin/env python3
"""Script Gerador e Validador da Especificação OpenAPI 3.1 Canônica do B3 Integration Atlas."""

from __future__ import annotations
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spec_meta import get_info_and_servers
from spec_components import get_parameters, get_responses, get_security_schemes
from spec_schemas_system import get_system_schemas
from spec_schemas_trading import get_trading_schemas
from spec_schemas_sng_sro import get_sng_sro_schemas
from spec_paths_system import get_system_paths
from spec_paths_gateway import get_gateway_paths

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILE = REPO_ROOT / "docs" / "openapi_b3_atlas_complete.json"

def build_full_spec() -> Dict[str, Any]:
    spec = get_info_and_servers()
    schemas = {}
    schemas.update(get_system_schemas())
    schemas.update(get_trading_schemas())
    schemas.update(get_sng_sro_schemas())

    spec["components"] = {
        "parameters": get_parameters(),
        "responses": get_responses(),
        "securitySchemes": get_security_schemes(),
        "schemas": schemas,
    }

    paths = {}
    paths.update(get_system_paths())
    paths.update(get_gateway_paths())
    spec["paths"] = paths
    return spec

def validate_spec(spec: Dict[str, Any]) -> None:
    assert spec["openapi"] == "3.1.0"
    assert "paths" in spec and len(spec["paths"]) >= 25
    assert "components" in spec and "schemas" in spec["components"]
    assert len(spec["components"]["schemas"]) >= 30

    spec_str = json.dumps(spec, indent=2, ensure_ascii=False)
    ref_pattern = re.compile(r"\"\$ref\":\s*\"([^\"]+)\"")
    refs = ref_pattern.findall(spec_str)

    missing_refs = []
    for r in refs:
        if r.startswith("#/components/schemas/"):
            s_name = r.replace("#/components/schemas/", "")
            if s_name not in spec["components"]["schemas"]:
                missing_refs.append(f"Schema: {s_name}")
        elif r.startswith("#/components/parameters/"):
            p_name = r.replace("#/components/parameters/", "")
            if p_name not in spec["components"]["parameters"]:
                missing_refs.append(f"Parameter: {p_name}")
        elif r.startswith("#/components/responses/"):
            resp_name = r.replace("#/components/responses/", "")
            if resp_name not in spec["components"]["responses"]:
                missing_refs.append(f"Response: {resp_name}")

    if missing_refs:
        raise ValueError(f"Erros de resolução de referências $ref: {missing_refs}")

    import jsonschema
    json.loads(spec_str)

def main() -> None:
    print("Iniciando geração da especificação formal OpenAPI 3.1...")
    spec = build_full_spec()
    validate_spec(spec)

    TARGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, ensure_ascii=False)

    print(f"\n[OK] Especificação OpenAPI 3.1 gerada e validada com sucesso em:\n     {TARGET_FILE}")
    print("\nEstatísticas:")
    print(f" - Total de Endpoints (Paths): {len(spec["paths"])}")
    total_ops = sum(len(methods) for methods in spec["paths"].values())
    print(f" - Total de Operações HTTP: {total_ops}")
    print(f" - Total de Schemas de Dados: {len(spec["components"]["schemas"])}")
    print(f" - Total de Parâmetros Reutilizáveis: {len(spec["components"]["parameters"])}")
    print(f" - Total de Respostas Reutilizáveis: {len(spec["components"]["responses"])}")
    print(f" - Total de Security Schemes: {len(spec["components"]["securitySchemes"])}")

if __name__ == "__main__":
    main()
