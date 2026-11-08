"""Testes unitários e de integração da configuração Docker e Observabilidade B3.

Valida a integridade formal, sintaxe e funcionamento de:
- Dockerfile (Multi-stage build, Python 3.11, Zero-pip, b3user, healthcheck nativo)
- docker-compose.yml (Topologia dos 4 serviços: b3-atlas, prometheus, grafana, mock-detran-susep)
- observability/prometheus.yml (Scraping PUMA, IMBARQ, Waterfall, SNG, SRO, Central Bus)
- observability/grafana_datasources.yml e grafana_dashboards.yml (Provisionamento automático)
- scripts/mock_detran_susep.py (Simulador leve de gateways externos 100% Python Standard Library)
- scripts/docker_test.sh (Suite executável de validação de configuração)
- src/b3_ui_server.py (/metrics exposition para Prometheus)
"""

from __future__ import annotations

import ast
from http.server import HTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Union
import unittest
import urllib.parse
import urllib.request


def _parse_yaml_val(val: str) -> Any:
    val = val.strip()
    if not val:
        return None
    if val.startswith("[") and val.endswith("]"):
        try:
            return json.loads(val)
        except Exception:
            return [p.strip().strip('"').strip("'") for p in val[1:-1].split(",") if p.strip()]
    if val in ("true", "True"):
        return True
    if val in ("false", "False"):
        return False
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        return val


def _parse_yaml_lines(lines: List[str], indent: int = 0) -> Any:
    res: Any = None
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        cur_indent = len(line) - len(line.lstrip())
        if cur_indent < indent:
            break
        if cur_indent > indent:
            i += 1
            continue

        if stripped.startswith("- "):
            if res is None:
                res = []
            item_text = stripped[2:].strip()
            if not item_text:
                sub_lines: List[str] = []
                j = i + 1
                while j < len(lines):
                    l_str = lines[j].strip()
                    if not l_str or l_str.startswith("#"):
                        j += 1
                        continue
                    if len(lines[j]) - len(lines[j].lstrip()) <= cur_indent:
                        break
                    sub_lines.append(lines[j])
                    j += 1
                sub_val = _parse_yaml_lines(sub_lines, cur_indent + 2)
                res.append(sub_val)
                i = j
                continue
            elif ":" in item_text:
                k, v = item_text.split(":", 1)
                entry_dict: Dict[str, Any] = {k.strip(): _parse_yaml_val(v) if v.strip() else None}
                sub_lines = []
                j = i + 1
                while j < len(lines):
                    l_str = lines[j].strip()
                    if not l_str or l_str.startswith("#"):
                        j += 1
                        continue
                    if len(lines[j]) - len(lines[j].lstrip()) <= cur_indent:
                        break
                    sub_lines.append(lines[j])
                    j += 1
                if sub_lines:
                    sub_val = _parse_yaml_lines(sub_lines, cur_indent + 2)
                    if isinstance(sub_val, dict):
                        entry_dict.update(sub_val)
                res.append(entry_dict)
                i = j
                continue
            else:
                res.append(_parse_yaml_val(item_text))
                i += 1
                continue
        elif ":" in stripped:
            if res is None:
                res = {}
            k, v = stripped.split(":", 1)
            k = k.strip()
            v = v.strip()
            if v:
                res[k] = _parse_yaml_val(v)
                i += 1
            else:
                sub_lines = []
                j = i + 1
                while j < len(lines):
                    l_str = lines[j].strip()
                    if not l_str or l_str.startswith("#"):
                        j += 1
                        continue
                    if len(lines[j]) - len(lines[j].lstrip()) <= cur_indent:
                        break
                    sub_lines.append(lines[j])
                    j += 1
                res[k] = _parse_yaml_lines(sub_lines, cur_indent + 2) if sub_lines else {}
                i = j
        else:
            i += 1
    return res if res is not None else {}


def _safe_load_yaml(stream_or_str: Any) -> Any:
    if hasattr(stream_or_str, "read"):
        content = stream_or_str.read()
    else:
        content = str(stream_or_str)
    return _parse_yaml_lines(content.splitlines())

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
PROMETHEUS_CONFIG_PATH = REPO_ROOT / "observability" / "prometheus.yml"
GRAFANA_DATASOURCES_PATH = REPO_ROOT / "observability" / "grafana_datasources.yml"
GRAFANA_DASHBOARDS_PATH = REPO_ROOT / "observability" / "grafana_dashboards.yml"
GRAFANA_DASHBOARD_JSON_PATH = REPO_ROOT / "observability" / "grafana_b3_enterprise_overview.json"
MOCK_GATEWAY_SCRIPT = REPO_ROOT / "scripts" / "mock_detran_susep.py"
DOCKER_TEST_SCRIPT = REPO_ROOT / "scripts" / "docker_test.sh"


class TestDockerfileConfig(unittest.TestCase):
    """Bateria de validação do Dockerfile de produção."""

    def setUp(self) -> None:
        self.assertTrue(DOCKERFILE_PATH.is_file(), f"Dockerfile não encontrado em {DOCKERFILE_PATH}")
        with open(DOCKERFILE_PATH, "r", encoding="utf-8") as f:
            self.content = f.read()
            self.lines = self.content.splitlines()

    def test_multi_stage_build_structure(self) -> None:
        """Valida se o Dockerfile implementa multi-stage build ultra-leve."""
        from_lines = [line.strip() for line in self.lines if line.strip().startswith("FROM")]
        self.assertGreaterEqual(len(from_lines), 2, "Dockerfile deve conter ao menos 2 estágios FROM (multi-stage)")
        self.assertTrue(any("AS builder" in l for l in from_lines), "Estágio builder ausente")
        self.assertTrue(any("AS runtime" in l for l in from_lines), "Estágio runtime ausente")

    def test_base_image_python_311(self) -> None:
        """Valida se a imagem base é Python 3.11-slim ou alpine."""
        from_lines = [line.strip() for line in self.lines if line.strip().startswith("FROM")]
        for fl in from_lines:
            self.assertRegex(fl, r"python:3\.11-(slim|alpine)", f"Imagem base deve ser Python 3.11: {fl}")

    def test_zero_pip_installations(self) -> None:
        """Valida que o build não utiliza pip (100% Python Standard Library)."""
        pip_matches = [line for line in self.lines if "pip install" in line.lower() or "pip3 install" in line.lower()]
        self.assertEqual(len(pip_matches), 0, f"Dockerfile não deve conter pip install: {pip_matches}")

    def test_non_root_user_b3user(self) -> None:
        """Valida que a execução roda estritamente sob o usuário não-root b3user."""
        self.assertIn("b3user", self.content, "Usuário não-root 'b3user' deve ser criado no Dockerfile")
        user_directives = [line.strip() for line in self.lines if line.strip().startswith("USER")]
        self.assertIn("USER b3user", user_directives, "Diretiva 'USER b3user' ausente no Dockerfile")

    def test_native_healthcheck_urllib(self) -> None:
        """Valida que o healthcheck é nativo em Python urllib sem dependências de curl/wget."""
        self.assertIn("HEALTHCHECK", self.content, "Instrução HEALTHCHECK ausente")
        self.assertIn("urllib.request", self.content, "Healthcheck deve usar urllib.request nativo")
        self.assertIn("/api/health", self.content, "Healthcheck deve verificar o endpoint /api/health")

    def test_exposed_port_8080(self) -> None:
        """Valida exposição da porta oficial 8080."""
        self.assertIn("EXPOSE 8080", self.content, "Porta 8080 não exposta no Dockerfile")

    def test_entrypoint_defined(self) -> None:
        """Valida entrypoint com b3_ui_server / cli ui."""
        self.assertTrue(
            any(line.strip().startswith("ENTRYPOINT") or line.strip().startswith("CMD") for line in self.lines),
            "ENTRYPOINT ou CMD obrigatório",
        )


class TestDockerComposeConfig(unittest.TestCase):
    """Bateria de validação do arquivo docker-compose.yml."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.assertTrue(COMPOSE_PATH.is_file(), f"docker-compose.yml não encontrado em {COMPOSE_PATH}")
        with open(COMPOSE_PATH, "r", encoding="utf-8") as f:
            cls.compose = _safe_load_yaml(f)

    def test_services_presence(self) -> None:
        """Valida presença dos 4 serviços obrigatórios."""
        self.assertIn("services", self.compose, "Chave 'services' ausente no docker-compose.yml")
        services = self.compose["services"]
        expected_services = ["b3-atlas", "prometheus", "grafana", "mock-detran-susep"]
        for svc in expected_services:
            self.assertIn(svc, services, f"Serviço obrigatório ausente: {svc}")

    def test_b3_atlas_service(self) -> None:
        """Valida configuração do serviço b3-atlas (UI Server + API na porta 8080)."""
        svc = self.compose["services"]["b3-atlas"]
        self.assertIn("ports", svc)
        ports = [str(p) for p in svc["ports"]]
        self.assertTrue(any("8080:8080" in p or "8080" in p for p in ports), "b3-atlas deve expor a porta 8080")
        self.assertIn("healthcheck", svc, "b3-atlas deve ter healthcheck configurado")

    def test_prometheus_service(self) -> None:
        """Valida configuração do serviço prometheus (porta 9090)."""
        svc = self.compose["services"]["prometheus"]
        self.assertIn("ports", svc)
        ports = [str(p) for p in svc["ports"]]
        self.assertTrue(any("9090:9090" in p or "9090" in p for p in ports), "Prometheus deve expor a porta 9090")
        self.assertIn("volumes", svc)
        vol_str = " ".join([str(v) for v in svc["volumes"]])
        self.assertIn("prometheus.yml", vol_str, "Prometheus deve montar o arquivo prometheus.yml")
        self.assertIn("depends_on", svc, "Prometheus deve depender de serviços anteriores")
        deps = svc["depends_on"]
        self.assertIn("b3-atlas", deps, "Prometheus deve depender de b3-atlas")

    def test_grafana_service(self) -> None:
        """Valida configuração do serviço grafana (porta 3000 e volumes de provisionamento)."""
        svc = self.compose["services"]["grafana"]
        self.assertIn("ports", svc)
        ports = [str(p) for p in svc["ports"]]
        self.assertTrue(any("3000:3000" in p or "3000" in p for p in ports), "Grafana deve expor a porta 3000")
        self.assertIn("volumes", svc)
        vol_str = " ".join([str(v) for v in svc["volumes"]])
        self.assertIn("grafana_datasources.yml", vol_str, "Grafana deve montar datasources.yml")
        self.assertIn("grafana_dashboards.yml", vol_str, "Grafana deve montar dashboards.yml")
        self.assertIn("grafana_b3_enterprise_overview.json", vol_str, "Grafana deve montar o JSON do dashboard B3")
        self.assertIn("depends_on", svc)
        deps = svc["depends_on"]
        self.assertIn("prometheus", deps, "Grafana deve depender do Prometheus")

    def test_mock_detran_susep_service(self) -> None:
        """Valida configuração do serviço mock-detran-susep para testes integrados."""
        svc = self.compose["services"]["mock-detran-susep"]
        self.assertIn("ports", svc)
        ports = [str(p) for p in svc["ports"]]
        self.assertTrue(any("8089:8089" in p or "8089" in p for p in ports), "mock-detran-susep deve expor porta 8089")
        self.assertIn("healthcheck", svc, "mock-detran-susep deve ter healthcheck")


class TestPrometheusScraperConfig(unittest.TestCase):
    """Bateria de validação do arquivo observability/prometheus.yml."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.assertTrue(PROMETHEUS_CONFIG_PATH.is_file(), f"prometheus.yml não encontrado em {PROMETHEUS_CONFIG_PATH}")
        with open(PROMETHEUS_CONFIG_PATH, "r", encoding="utf-8") as f:
            cls.prom = _safe_load_yaml(f)

    def test_global_settings(self) -> None:
        """Valida intervalo de scraping global."""
        self.assertIn("global", self.prom)
        self.assertIn("scrape_interval", self.prom["global"])

    def test_scrape_configs_presence(self) -> None:
        """Valida se os jobs de scraping b3-atlas e mock-detran-susep existem."""
        self.assertIn("scrape_configs", self.prom)
        jobs = {j["job_name"]: j for j in self.prom["scrape_configs"]}
        self.assertIn("b3-atlas", jobs, "Job 'b3-atlas' ausente no prometheus.yml")
        self.assertIn("mock-detran-susep", jobs, "Job 'mock-detran-susep' ausente no prometheus.yml")

    def test_b3_atlas_job_targets_and_labels(self) -> None:
        """Valida raspagem do Atlas na porta 8080 e rotulagem para PUMA, IMBARQ, Waterfall, SNG, SRO."""
        jobs = {j["job_name"]: j for j in self.prom["scrape_configs"]}
        atlas_job = jobs["b3-atlas"]
        self.assertEqual(atlas_job.get("metrics_path", "/metrics"), "/metrics")
        targets = []
        labels = {}
        for sc in atlas_job.get("static_configs", []):
            targets.extend(sc.get("targets", []))
            labels.update(sc.get("labels", {}))

        self.assertIn("b3-atlas:8080", targets, "Alvo b3-atlas:8080 ausente")
        subsystems = labels.get("subsystems", "")
        for required_subsystem in ["puma", "imbarq", "waterfall", "sng", "sro"]:
            self.assertIn(required_subsystem, subsystems, f"Subsistema {required_subsystem} ausente nos labels")

    def test_mock_job_target(self) -> None:
        """Valida alvo de raspagem do gateway mock na porta 8089."""
        jobs = {j["job_name"]: j for j in self.prom["scrape_configs"]}
        mock_job = jobs["mock-detran-susep"]
        targets = []
        for sc in mock_job.get("static_configs", []):
            targets.extend(sc.get("targets", []))
        self.assertIn("mock-detran-susep:8089", targets, "Alvo mock-detran-susep:8089 ausente")


class TestGrafanaProvisioningConfigs(unittest.TestCase):
    """Bateria de validação do provisionamento do Grafana."""

    def test_grafana_datasources_provisioning(self) -> None:
        """Valida arquivo grafana_datasources.yml."""
        self.assertTrue(GRAFANA_DATASOURCES_PATH.is_file(), f"{GRAFANA_DATASOURCES_PATH} não encontrado")
        with open(GRAFANA_DATASOURCES_PATH, "r", encoding="utf-8") as f:
            data = _safe_load_yaml(f)
        self.assertIn("datasources", data)
        self.assertGreater(len(data["datasources"]), 0)
        ds = data["datasources"][0]
        self.assertEqual(ds.get("name"), "Prometheus")
        self.assertEqual(ds.get("type"), "prometheus")
        self.assertEqual(ds.get("url"), "http://prometheus:9090")
        self.assertTrue(ds.get("isDefault", False))

    def test_grafana_dashboards_provisioning(self) -> None:
        """Valida arquivo grafana_dashboards.yml."""
        self.assertTrue(GRAFANA_DASHBOARDS_PATH.is_file(), f"{GRAFANA_DASHBOARDS_PATH} não encontrado")
        with open(GRAFANA_DASHBOARDS_PATH, "r", encoding="utf-8") as f:
            data = _safe_load_yaml(f)
        self.assertIn("providers", data)
        provider = data["providers"][0]
        self.assertEqual(provider.get("type"), "file")
        options = provider.get("options", {})
        self.assertEqual(options.get("path"), "/var/lib/grafana/dashboards")

    def test_grafana_dashboard_json_validity(self) -> None:
        """Valida se o JSON do dashboard B3 existe e tem schema válido."""
        self.assertTrue(GRAFANA_DASHBOARD_JSON_PATH.is_file(), f"{GRAFANA_DASHBOARD_JSON_PATH} não encontrado")
        with open(GRAFANA_DASHBOARD_JSON_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(d.get("uid"), "b3-enterprise-overview")
        self.assertGreaterEqual(len(d.get("panels", [])), 15)


class TestMockDetranSusep(unittest.TestCase):
    """Bateria de testes funcionais do simulador mock DETRAN e SUSEP."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.assertTrue(MOCK_GATEWAY_SCRIPT.is_file(), f"{MOCK_GATEWAY_SCRIPT} não encontrado")
        # Subir servidor de mock em porta livre de teste
        from scripts.mock_detran_susep import MockGatewayHandler, STATE
        cls.mock_state = STATE
        cls.port = 18089
        cls.server = HTTPServer(("127.0.0.1", cls.port), MockGatewayHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict | str, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(payload).encode("utf-8") if payload else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                headers = dict(resp.headers)
                raw_body = resp.read().decode("utf-8")
                try:
                    body = json.loads(raw_body)
                except Exception:
                    body = raw_body
                return status, body, headers
        except urllib.error.HTTPError as e:
            raw_body = e.read().decode("utf-8")
            try:
                body = json.loads(raw_body)
            except Exception:
                body = raw_body
            return e.code, body, dict(e.headers)

    def test_mock_script_pure_standard_library(self) -> None:
        """Verifica que mock_detran_susep.py não importa nada fora da Standard Library."""
        with open(MOCK_GATEWAY_SCRIPT, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imports = [n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)]
        imports += [n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
        stdlib = {"argparse", "http", "hashlib", "json", "os", "sys", "time", "typing", "urllib", "__future__"}
        self.assertTrue(set(imports).issubset(stdlib), f"Dependências externas detectadas: {set(imports) - stdlib}")

    def test_mock_health(self) -> None:
        """Verifica endpoint de health do mock."""
        status, body, _ = self._request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "UP")
        self.assertIn("DETRAN-SNG", body["gateways"])
        self.assertIn("SUSEP-SRO", body["gateways"])

    def test_mock_detran_inclusion_and_discharge(self) -> None:
        """Verifica fluxo completo de averbação e baixa de gravame no Detran."""
        # 1. Inclusão de gravame
        payload = {
            "uf": "MG",
            "chassi": "9BWZZZ00000012345",
            "placa": "XYZ9876",
            "credor_cnpj": "12345678000195",
            "credor_nome": "BANCO FINANCIADOR B3",
        }
        status, body, _ = self._request("POST", "/detran/gravames", payload)
        self.assertEqual(status, 201)
        self.assertEqual(body["status"], "AVERBADO")
        self.assertIn("DETRAN-MG-AVERB", body["detran_protocolo"])
        gid = body["gravame_id"]

        # 2. Consulta gravame
        status, body, _ = self._request("GET", f"/detran/gravames/{gid}")
        self.assertEqual(status, 200)
        self.assertEqual(body["gravame_id"], gid)

        # 3. Baixa de gravame
        baixa_payload = {"gravame_id": gid, "uf": "MG"}
        status, body, _ = self._request("POST", f"/detran/gravames/{gid}/baixa", baixa_payload)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "BAIXADO")
        self.assertIn("DETRAN-MG-BAIXA", body["desalienacao_protocolo"])
        self.assertEqual(body["detran_status"], "LIBERADO_PARA_TRANSFERENCIA")

    def test_mock_susep_policy_and_claim(self) -> None:
        """Verifica fluxo completo de registro de apólice e sinistro no SUSEP."""
        # 1. Registro de apólice
        payload = {
            "ramo": "AUTO",
            "seguradora_cnpj": "11222333000188",
            "limite_maximo_garantia": 200000.0,
            "premio_liquido": 4500.0,
        }
        status, body, _ = self._request("POST", "/susep/apolices", payload)
        self.assertEqual(status, 201)
        self.assertEqual(body["status"], "REGISTRADA")
        self.assertEqual(len(body["susep_hash"]), 64)
        self.assertIn("SUSEP-SRO-V3-REC", body["susep_hash_protocolo"])
        aid = body["apolice_id"]

        # 2. Notificação de sinistro
        sinistro_payload = {
            "apolice_id": aid,
            "valor_sinistro": 35000.0,
        }
        status, body, _ = self._request("POST", "/susep/sinistros", sinistro_payload)
        self.assertEqual(status, 201)
        self.assertEqual(body["status"], "NOTIFICADO")
        self.assertIn("SUSEP-SINISTRO", body["sinistro_protocolo"])

    def test_mock_metrics_endpoint(self) -> None:
        """Verifica endpoint Prometheus /metrics no mock gateway."""
        status, body, headers = self._request("GET", "/metrics")
        self.assertEqual(status, 200)
        self.assertIn("text/plain", headers.get("Content-Type", ""))
        self.assertIn("b3_mock_gateway_up", body)
        self.assertIn("b3_mock_detran_requests_total", body)
        self.assertIn("b3_mock_susep_requests_total", body)


class TestB3UiServerPrometheusMetrics(unittest.TestCase):
    """Valida exposição de métricas Prometheus em b3_ui_server.py."""

    @classmethod
    def setUpClass(cls) -> None:
        from src.b3_ui_server import B3DashboardHandler
        cls.port = 18081
        cls.server = HTTPServer(("127.0.0.1", cls.port), B3DashboardHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_metrics_endpoint_coverage(self) -> None:
        """Valida se o endpoint /metrics expõe as métricas de PUMA, IMBARQ, Waterfall, SNG, SRO e Central Bus."""
        url = f"http://127.0.0.1:{self.port}/metrics"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/plain", resp.headers.get("Content-Type", ""))
            body = resp.read().decode("utf-8")

        # PUMA
        self.assertIn("b3_puma_messages_total", body)
        self.assertIn("b3_puma_orders_matched_total", body)
        self.assertIn("b3_puma_matching_latency_microseconds_bucket", body)
        self.assertIn("b3_puma_circuit_breaker_active", body)

        # IMBARQ
        self.assertIn("b3_imbarq_custody_reconciliation_breaks_total", body)
        self.assertIn("b3_imbarq_custody_positions_total", body)
        self.assertIn("b3_csd_unallocated_positions_gauge", body)

        # Waterfall
        self.assertIn("b3_clearing_default_waterfall_active_tier_level", body)
        self.assertIn("b3_clearing_default_waterfall_tier_consumption_ratio", body)
        self.assertIn("b3_clearing_safeguards_available_capital_brl", body)
        self.assertIn("b3_clearing_sitg_capital_intact_ratio", body)

        # SNG
        self.assertIn("b3_sng_active_liens_gauge", body)
        self.assertIn("b3_sng_gravames_inclusions_total", body)
        self.assertIn("b3_sng_gravames_discharges_total", body)
        self.assertIn("b3_sng_law112_duplicate_lien_rejections_total", body)

        # SRO
        self.assertIn("b3_sro_policy_registrations_total", body)
        self.assertIn("b3_sro_earned_premiums_brl", body)
        self.assertIn("b3_sro_incurred_claims_brl", body)

        # Central Bus
        self.assertIn("b3_central_bus_invariants_status", body)
        self.assertIn("b3_central_bus_accounting_imbalance_brl", body)
        self.assertIn("b3_central_bus_atomic_settlement_success_ratio", body)
        self.assertIn("b3_central_bus_subsystem_volume_brl", body)


class TestDockerTestValidationScript(unittest.TestCase):
    """Valida a execução do script scripts/docker_test.sh."""

    def test_docker_test_sh_executable_and_success(self) -> None:
        """Verifica se docker_test.sh executa sem erros e retorna status 0."""
        self.assertTrue(DOCKER_TEST_SCRIPT.is_file(), f"{DOCKER_TEST_SCRIPT} não encontrado")
        self.assertTrue(os.access(DOCKER_TEST_SCRIPT, os.X_OK), "docker_test.sh deve ter permissão de execução (+x)")

        res = subprocess.run([str(DOCKER_TEST_SCRIPT)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"docker_test.sh falhou com código {res.returncode}:\n{res.stdout}\n{res.stderr}")
        self.assertIn("TODOS OS", res.stdout)
        self.assertIn("PASSARAM COM SUCESSO", res.stdout)


if __name__ == "__main__":
    unittest.main()
