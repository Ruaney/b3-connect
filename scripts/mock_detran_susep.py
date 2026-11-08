#!/usr/bin/env python3
"""Mock DETRAN & SUSEP Gateway Simulator.

Simulação de alta fidelidade e ultraleve de gateways externos para testes
integrados de ponta a ponta:
- DETRAN: Registro e baixa de gravames veiculares (SNG).
- SUSEP: Registro e auditoria de apólices e sinistros (SRO).
- Observabilidade: Métricas compatíveis com Prometheus em /metrics.

100% Python Standard Library (Sem dependências externas).
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional
import urllib.parse


class MockGatewayState:
    """Estado em memória do simulador dos gateways DETRAN e SUSEP."""

    def __init__(self) -> None:
        self.gravames: Dict[str, Dict[str, Any]] = {}
        self.baixas: Dict[str, Dict[str, Any]] = {}
        self.apolices: Dict[str, Dict[str, Any]] = {}
        self.sinistros: Dict[str, Dict[str, Any]] = {}
        self.detran_requests_total: int = 0
        self.susep_requests_total: int = 0
        self.total_requests: int = 0
        self._seed_initial_data()

    def _seed_initial_data(self) -> None:
        """Inicializa alguns registros de teste para homologação imediata."""
        # Gravame inicial SP
        gid = "GRV-SP-2026-0001"
        self.gravames[gid] = {
            "gravame_id": gid,
            "chassi": "9BWZZZ377VT004211",
            "placa": "BRA2E19",
            "uf": "SP",
            "credor_cnpj": "00000000000191",
            "credor_nome": "BANCO TESTE B3 S.A.",
            "devedor_documento": "12345678901",
            "status": "AVERBADO",
            "detran_protocolo": "DETRAN-SP-AVERB-2026-9901",
            "data_inclusao": "2026-11-08T00:00:00Z",
        }
        # Apólice inicial SRO
        aid = "AP-AUTO-2026-0001"
        h = hashlib.sha256(f"APOLICE:{aid}:SUSEP".encode("utf-8")).hexdigest()
        self.apolices[aid] = {
            "apolice_id": aid,
            "ramo": "AUTO",
            "seguradora_cnpj": "33000111000199",
            "limite_maximo_garantia": 150000.0,
            "premio_liquido": 3200.0,
            "susep_hash": h,
            "susep_hash_protocolo": f"SUSEP-SRO-V3-{h[:12].upper()}",
            "status": "REGISTRADA",
            "data_emissao": "2026-11-08T00:00:00Z",
        }


STATE = MockGatewayState()


class MockGatewayHandler(BaseHTTPRequestHandler):
    """Handler HTTP para simulação dos gateways DETRAN e SUSEP."""

    server_version = "B3MockGateway/1.0.0"

    def log_message(self, format: str, *args: Any) -> None:
        """Redireciona logs para stdout de forma estruturada."""
        sys.stdout.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ')}] [MOCK-GATEWAY] {format % args}\n")
        sys.stdout.flush()

    def _send_json(self, data: Any, status_code: int = 200) -> None:
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length).decode("utf-8")
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self) -> None:
        STATE.total_requests += 1
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("", "/", "/health", "/api/health"):
            self._send_json({
                "status": "UP",
                "service": "mock-detran-susep",
                "version": "1.0.0",
                "gateways": ["DETRAN-SNG", "SUSEP-SRO"],
                "records": {
                    "gravames_ativos": len(STATE.gravames),
                    "baixas_registradas": len(STATE.baixas),
                    "apolices_ativas": len(STATE.apolices),
                    "sinistros_notificados": len(STATE.sinistros),
                },
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
        elif path == "/metrics":
            self._serve_metrics()
        elif path == "/detran/gravames":
            STATE.detran_requests_total += 1
            uf = query.get("uf", [None])[0]
            items = list(STATE.gravames.values())
            if uf:
                items = [g for g in items if g.get("uf") == uf.upper()]
            self._send_json({"total": len(items), "gravames": items})
        elif path.startswith("/detran/gravames/"):
            STATE.detran_requests_total += 1
            gid = path.split("/")[-1]
            rec = STATE.gravames.get(gid)
            if rec:
                self._send_json(rec)
            else:
                self._send_json({"error": "Gravame não encontrado", "gravame_id": gid}, status_code=404)
        elif path == "/detran/health":
            STATE.detran_requests_total += 1
            self._send_json({"status": "UP", "gateway": "DETRAN-SNG", "uf_coverage": "ALL_27_UFS"})
        elif path == "/susep/apolices":
            STATE.susep_requests_total += 1
            ramo = query.get("ramo", [None])[0]
            items = list(STATE.apolices.values())
            if ramo:
                items = [a for a in items if a.get("ramo") == ramo.upper()]
            self._send_json({"total": len(items), "apolices": items})
        elif path.startswith("/susep/apolices/"):
            STATE.susep_requests_total += 1
            aid = path.split("/")[-1]
            rec = STATE.apolices.get(aid)
            if rec:
                self._send_json(rec)
            else:
                self._send_json({"error": "Apólice não encontrada", "apolice_id": aid}, status_code=404)
        elif path == "/susep/health":
            STATE.susep_requests_total += 1
            self._send_json({"status": "UP", "gateway": "SUSEP-SRO", "protocol_version": "V3.0"})
        else:
            self.send_error(404, "Rota mock não encontrada")

    def do_POST(self) -> None:
        STATE.total_requests += 1
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        payload = self._read_json_body()

        if path in ("/detran/gravames", "/detran/gravame"):
            STATE.detran_requests_total += 1
            gid = payload.get("gravame_id") or f"GRV-{payload.get('uf', 'SP')}-{int(time.time()*1000)}"
            uf = payload.get("uf", "SP").upper()
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            proto_hash = hashlib.md5(f"{gid}:{ts}".encode("utf-8")).hexdigest()[:8].upper()
            proto = f"DETRAN-{uf}-AVERB-{time.strftime('%Y')}-{proto_hash}"
            rec = {
                "gravame_id": gid,
                "chassi": payload.get("chassi", "9BWZZZ00000000000"),
                "placa": payload.get("placa", "PAD0000"),
                "uf": uf,
                "credor_cnpj": payload.get("credor_cnpj", "00000000000191"),
                "credor_nome": payload.get("credor_nome", "INSTITUICAO FINANCEIRA"),
                "devedor_documento": payload.get("devedor_documento", "00000000000"),
                "status": "AVERBADO",
                "detran_protocolo": proto,
                "data_inclusao": ts,
            }
            STATE.gravames[gid] = rec
            self._send_json({
                "status": "AVERBADO",
                "gravame_id": gid,
                "detran_protocolo": proto,
                "uf": uf,
                "timestamp": ts,
            }, status_code=201)

        elif path.endswith("/baixa") or path in ("/detran/baixas", "/detran/baixa"):
            STATE.detran_requests_total += 1
            gid = payload.get("gravame_id")
            if not gid and "/gravames/" in path:
                gid = path.split("/gravames/")[1].split("/")[0]

            uf = payload.get("uf", "SP").upper()
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            proto_hash = hashlib.md5(f"BAIXA:{gid}:{ts}".encode("utf-8")).hexdigest()[:8].upper()
            proto = f"DETRAN-{uf}-BAIXA-{time.strftime('%Y')}-{proto_hash}"

            if gid and gid in STATE.gravames:
                STATE.gravames[gid]["status"] = "BAIXADO"
                STATE.gravames[gid]["data_baixa"] = ts
                STATE.gravames[gid]["desalienacao_protocolo"] = proto

            baixa_rec = {
                "gravame_id": gid,
                "status": "BAIXADO",
                "desalienacao_protocolo": proto,
                "detran_status": "LIBERADO_PARA_TRANSFERENCIA",
                "timestamp": ts,
            }
            if gid:
                STATE.baixas[gid] = baixa_rec
            self._send_json(baixa_rec, status_code=200)

        elif path in ("/susep/apolices", "/susep/apolice"):
            STATE.susep_requests_total += 1
            aid = payload.get("apolice_id") or f"AP-{payload.get('ramo', 'AUTO')}-{int(time.time()*1000)}"
            ramo = payload.get("ramo", "AUTO").upper()
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            raw_hash = hashlib.sha256(f"{aid}:{ramo}:{ts}".encode("utf-8")).hexdigest()
            proto = f"SUSEP-SRO-V3-REC-{raw_hash[:12].upper()}"
            rec = {
                "apolice_id": aid,
                "ramo": ramo,
                "seguradora_cnpj": payload.get("seguradora_cnpj", "00000000000199"),
                "limite_maximo_garantia": float(payload.get("limite_maximo_garantia", 100000.0)),
                "premio_liquido": float(payload.get("premio_liquido", 2000.0)),
                "susep_hash": raw_hash,
                "susep_hash_protocolo": proto,
                "status": "REGISTRADA",
                "data_emissao": ts,
            }
            STATE.apolices[aid] = rec
            self._send_json({
                "status": "REGISTRADA",
                "apolice_id": aid,
                "susep_hash": raw_hash,
                "susep_hash_protocolo": proto,
                "timestamp": ts,
            }, status_code=201)

        elif path in ("/susep/sinistros", "/susep/sinistro"):
            STATE.susep_requests_total += 1
            sid = payload.get("sinistro_id") or f"SIN-{int(time.time()*1000)}"
            aid = payload.get("apolice_id", "AP-UNKNOWN")
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            proto_hash = hashlib.sha256(f"{sid}:{aid}:{ts}".encode("utf-8")).hexdigest()[:10].upper()
            proto = f"SUSEP-SINISTRO-{proto_hash}"
            rec = {
                "sinistro_id": sid,
                "apolice_id": aid,
                "valor_sinistro": float(payload.get("valor_sinistro", 15000.0)),
                "status": "NOTIFICADO",
                "sinistro_protocolo": proto,
                "timestamp": ts,
            }
            STATE.sinistros[sid] = rec
            self._send_json(rec, status_code=201)
        else:
            self.send_error(404, "Endpoint mock não reconhecido")

    def _serve_metrics(self) -> None:
        """Retorna métricas em formato Prometheus."""
        lines = [
            "# HELP b3_mock_gateway_up Status do servico mock gateway externo",
            "# TYPE b3_mock_gateway_up gauge",
            'b3_mock_gateway_up{env="prd",service="mock-detran-susep"} 1',
            "",
            "# HELP b3_mock_detran_requests_total Total de requisicoes tratadas pelo mock DETRAN",
            "# TYPE b3_mock_detran_requests_total counter",
            f'b3_mock_detran_requests_total{{env="prd",service="mock-detran-susep"}} {STATE.detran_requests_total}',
            "",
            "# HELP b3_mock_susep_requests_total Total de requisicoes tratadas pelo mock SUSEP",
            "# TYPE b3_mock_susep_requests_total counter",
            f'b3_mock_susep_requests_total{{env="prd",service="mock-detran-susep"}} {STATE.susep_requests_total}',
            "",
            "# HELP b3_mock_gravames_active Total de gravames em memoria no mock DETRAN",
            "# TYPE b3_mock_gravames_active gauge",
            f'b3_mock_gravames_active{{env="prd"}} {len(STATE.gravames)}',
            "",
            "# HELP b3_mock_apolices_active Total de apolices em memoria no mock SUSEP",
            "# TYPE b3_mock_apolices_active gauge",
            f'b3_mock_apolices_active{{env="prd"}} {len(STATE.apolices)}',
            "",
            "# HELP b3_mock_requests_total Total geral de chamadas ao mock gateway",
            "# TYPE b3_mock_requests_total counter",
            f'b3_mock_requests_total{{env="prd"}} {STATE.total_requests}',
            "",
        ]
        body = "\n".join(lines).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def run_mock_server(host: str = "0.0.0.0", port: int = 8089) -> None:
    """Inicia o servidor de mock."""
    server = HTTPServer((host, port), MockGatewayHandler)
    print("=" * 80)
    print(f" B3 Mock DETRAN & SUSEP Gateway rodando em http://{host}:{port}")
    print(" 100% Python Standard Library — Endpoints:")
    print(f"  • Healthcheck:  http://{host}:{port}/health")
    print(f"  • DETRAN SNG:   http://{host}:{port}/detran/gravames")
    print(f"  • SUSEP SRO:    http://{host}:{port}/susep/apolices")
    print(f"  • Prometheus:   http://{host}:{port}/metrics")
    print("=" * 80)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado pelo usuário.")
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock Gateway DETRAN & SUSEP B3")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"), help="Host IP")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8089)), help="Porta HTTP")
    args = parser.parse_args()
    run_mock_server(host=args.host, port=args.port)
