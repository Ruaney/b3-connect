#!/usr/bin/env bash
# ==============================================================================
# B3 Integration Atlas — Docker Configuration & Syntax Validation Suite
# Validates Dockerfile, docker-compose.yml, Prometheus & Grafana configs
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "================================================================================"
echo " B3 INTEGRATION ATLAS — VALIDAÇÃO DA INFRAESTRUTURA DOCKER E OBSERVABILIDADE"
echo "================================================================================"

PASSED=0
FAILED=0

check_step() {
    local desc="$1"
    local cmd="$2"
    echo -n "→ ${desc}... "
    if eval "${cmd}" >/dev/null 2>&1; then
        echo -e "\033[32m[OK]\033[0m"
        PASSED=$((PASSED + 1))
    else
        echo -e "\033[31m[FALHOU]\033[0m"
        echo "  Comando que falhou: ${cmd}"
        FAILED=$((FAILED + 1))
    fi
}

echo ""
echo "[1/5] Validação do Dockerfile (Multi-stage, Zero-pip, b3user, Healthcheck)"
echo "--------------------------------------------------------------------------------"
check_step "Dockerfile existe" "[ -f '${REPO_ROOT}/Dockerfile' ]"
check_step "Base Python 3.11 presente" "grep -q 'FROM python:3.11' '${REPO_ROOT}/Dockerfile'"
check_step "Multi-stage build configurado (AS builder e AS runtime)" "grep -q 'AS builder' '${REPO_ROOT}/Dockerfile' && grep -q 'AS runtime' '${REPO_ROOT}/Dockerfile'"
check_step "Sem invocação de pip (Zero Pip)" "! grep -iE 'pip install|pip3 install' '${REPO_ROOT}/Dockerfile'"
check_step "Usuário não-root b3user configurado" "grep -q 'USER b3user' '${REPO_ROOT}/Dockerfile' && grep -q 'b3user' '${REPO_ROOT}/Dockerfile'"
check_step "Healthcheck nativo com urllib configurado" "grep -q 'HEALTHCHECK' '${REPO_ROOT}/Dockerfile' && grep -q 'urllib.request' '${REPO_ROOT}/Dockerfile'"
check_step "Porta 8080 exposta" "grep -q 'EXPOSE 8080' '${REPO_ROOT}/Dockerfile'"

echo ""
echo "[2/5] Validação do docker-compose.yml e Topologia dos 4 Serviços"
echo "--------------------------------------------------------------------------------"
check_step "docker-compose.yml existe" "[ -f '${REPO_ROOT}/docker-compose.yml' ]"
check_step "Sintaxe YAML válida de docker-compose.yml" "python3 -c \"import yaml; yaml.safe_load(open('${REPO_ROOT}/docker-compose.yml'))\""
check_step "Serviço b3-atlas configurado (porta 8080)" "python3 -c \"import yaml; doc=yaml.safe_load(open('${REPO_ROOT}/docker-compose.yml')); assert 'b3-atlas' in doc['services']; assert '8080:8080' in doc['services']['b3-atlas']['ports']\""
check_step "Serviço prometheus configurado (porta 9090)" "python3 -c \"import yaml; doc=yaml.safe_load(open('${REPO_ROOT}/docker-compose.yml')); assert 'prometheus' in doc['services']; assert '9090:9090' in doc['services']['prometheus']['ports']\""
check_step "Serviço grafana configurado (porta 3000)" "python3 -c \"import yaml; doc=yaml.safe_load(open('${REPO_ROOT}/docker-compose.yml')); assert 'grafana' in doc['services']; assert '3000:3000' in doc['services']['grafana']['ports']\""
check_step "Serviço mock-detran-susep configurado (porta 8089)" "python3 -c \"import yaml; doc=yaml.safe_load(open('${REPO_ROOT}/docker-compose.yml')); assert 'mock-detran-susep' in doc['services']; assert '8089:8089' in doc['services']['mock-detran-susep']['ports']\""
check_step "Dependência Prometheus -> b3-atlas" "python3 -c \"import yaml; doc=yaml.safe_load(open('${REPO_ROOT}/docker-compose.yml')); assert 'b3-atlas' in doc['services']['prometheus']['depends_on']\""
check_step "Dependência Grafana -> Prometheus" "python3 -c \"import yaml; doc=yaml.safe_load(open('${REPO_ROOT}/docker-compose.yml')); assert 'prometheus' in doc['services']['grafana']['depends_on']\""

echo ""
echo "[3/5] Validação da Configuração do Prometheus (Scraping PUMA, IMBARQ, Waterfall, SNG, SRO)"
echo "--------------------------------------------------------------------------------"
check_step "observability/prometheus.yml existe" "[ -f '${REPO_ROOT}/observability/prometheus.yml' ]"
check_step "Sintaxe YAML válida de prometheus.yml" "python3 -c \"import yaml; yaml.safe_load(open('${REPO_ROOT}/observability/prometheus.yml'))\""
check_step "Scrape job b3-atlas definido para porta 8080" "python3 -c \"import yaml; doc=yaml.safe_load(open('${REPO_ROOT}/observability/prometheus.yml')); jobs=[j['job_name'] for j in doc['scrape_configs']]; assert 'b3-atlas' in jobs\""
check_step "Scrape job mock-detran-susep definido" "python3 -c \"import yaml; doc=yaml.safe_load(open('${REPO_ROOT}/observability/prometheus.yml')); jobs=[j['job_name'] for j in doc['scrape_configs']]; assert 'mock-detran-susep' in jobs\""
check_step "Cobertura de métricas (PUMA, IMBARQ, Waterfall, SNG, SRO)" "grep -q 'subsystems.*puma.*imbarq.*waterfall.*sng.*sro' '${REPO_ROOT}/observability/prometheus.yml'"

echo ""
echo "[4/5] Validação do Provisionamento do Grafana (Datasource e Dashboards)"
echo "--------------------------------------------------------------------------------"
check_step "observability/grafana_datasources.yml existe" "[ -f '${REPO_ROOT}/observability/grafana_datasources.yml' ]"
check_step "Sintaxe YAML válida de grafana_datasources.yml" "python3 -c \"import yaml; yaml.safe_load(open('${REPO_ROOT}/observability/grafana_datasources.yml'))\""
check_step "Datasource Prometheus apontando para http://prometheus:9090" "python3 -c \"import yaml; doc=yaml.safe_load(open('${REPO_ROOT}/observability/grafana_datasources.yml')); ds=doc['datasources'][0]; assert ds['url'] == 'http://prometheus:9090'\""
check_step "observability/grafana_dashboards.yml existe" "[ -f '${REPO_ROOT}/observability/grafana_dashboards.yml' ]"
check_step "Sintaxe YAML válida de grafana_dashboards.yml" "python3 -c \"import yaml; yaml.safe_load(open('${REPO_ROOT}/observability/grafana_dashboards.yml'))\""
check_step "observability/grafana_b3_enterprise_overview.json existe e é JSON válido" "python3 -c \"import json; json.load(open('${REPO_ROOT}/observability/grafana_b3_enterprise_overview.json'))\""

echo ""
echo "[5/5] Validação do Script Mock DETRAN/SUSEP"
echo "--------------------------------------------------------------------------------"
check_step "scripts/mock_detran_susep.py existe" "[ -f '${REPO_ROOT}/scripts/mock_detran_susep.py' ]"
check_step "Compilação de bytecode sem erros" "python3 -m py_compile '${REPO_ROOT}/scripts/mock_detran_susep.py'"
check_step "Zero dependências externas (100% Python Standard Library)" "python3 -c \"import ast; tree=ast.parse(open('${REPO_ROOT}/scripts/mock_detran_susep.py').read()); imports=[n.names[0].name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import)] + [n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]; stdlib={'argparse', 'http', 'hashlib', 'json', 'os', 'sys', 'time', 'typing', 'urllib', '__future__'}; assert set(imports).issubset(stdlib), f'Dependências não permitidas: {set(imports) - stdlib}'\""

echo ""
echo "================================================================================"
if [ "${FAILED}" -eq 0 ]; then
    echo -e "\033[32m ✓ TODOS OS ${PASSED} TESTES DE CONFIGURAÇÃO DOCKER PASSARAM COM SUCESSO!\033[0m"
    echo "================================================================================"
    exit 0
else
    echo -e "\033[31m ✗ ${FAILED} TESTES FALHARAM! Verifique os erros acima.\033[0m"
    echo "================================================================================"
    exit 1
fi
