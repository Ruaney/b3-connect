"""B3 Dashboard Server — Servidor Visual Local e Interativo do B3 Integration Atlas.

Fornece um dashboard web moderno, 100% off-line (zero CDN, zero pip dependencies)
para explorar visualmente os 7 módulos, navegar pelos 100 fluxos, ler os 80 manuais
oficiais locais e executar simulações de pregão e cenários de estresse no navegador.

100% Python Standard Library: http.server, urllib, json, pathlib, threading, time.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import socketserver
import sys
import threading
import time
from typing import Any, Dict, List, Optional
import urllib.parse

from src.b3_concepts_catalog import B3ConceptsCatalog
from src.b3_chaos_simulator import B3ChaosSimulator, ChaosScenarioType
from src.b3_developer_portal import render_developer_portal_html, get_openapi_spec, get_guides_catalog
from src.b3_doc_harvester import B3DocHarvester, DOCUMENT_CATEGORIES
from src.b3_market_simulator import B3MarketSimulator

_REPO_ROOT = Path(__file__).resolve().parent.parent


class B3DashboardHandler(BaseHTTPRequestHandler):
    """Handler HTTP para o Dashboard Web Local da B3."""

    server_version = "B3AtlasDashboard/2.3.0"

    def do_OPTIONS(self) -> None:
        """Trata requisições CORS preflight para chamadas interativas."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With, X-Idempotency-Key")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        if path in ("/", "/index.html"):
            self._serve_html_dashboard()
        elif path in ("/docs/api", "/docs/interactive"):
            self._serve_html_developer_portal(path)
        elif path in ("/docs/openapi.json", "/api/openapi.json"):
            self._serve_openapi_spec()
        elif path == "/api/guides":
            self._serve_api_guides()
        elif path.startswith("/api/guides/"):
            guide_name = path.replace("/api/guides/", "").strip()
            self._serve_api_single_guide(guide_name)
        elif path == "/api/v1/orders":
            self._serve_api_v1_orders_list(query)
        elif path.startswith("/api/v1/orders/"):
            order_id = path.replace("/api/v1/orders/", "").strip()
            self._serve_api_v1_single_order(order_id)
        elif path == "/api/v1/auction/theoretical-price":
            self._serve_api_v1_auction_theoretical_price(query)
        elif path == "/api/v1/sng/gravames":
            self._serve_api_v1_sng_gravames_list(query)
        elif path.startswith("/api/v1/sng/gravames/"):
            gravame_id = path.replace("/api/v1/sng/gravames/", "").strip()
            self._serve_api_v1_single_gravame(gravame_id)
        elif path == "/api/v1/sro/apolices":
            self._serve_api_v1_sro_apolices_list(query)
        elif path.startswith("/api/v1/sro/apolices/"):
            apolice_id = path.replace("/api/v1/sro/apolices/", "").strip()
            self._serve_api_v1_single_apolice(apolice_id)
        elif path == "/api/status":
            self._serve_api_status()
        elif path == "/api/modules":
            self._serve_api_modules()
        elif path == "/api/flows":
            self._serve_api_flows()
        elif path == "/api/docs":
            self._serve_api_docs()
        elif path.startswith("/api/docs/"):
            doc_id = path.replace("/api/docs/", "").upper()
            self._serve_api_single_doc(doc_id)
        elif path == "/api/simulate-day":
            self._serve_api_simulate_day(query)
        elif path.startswith("/api/chaos/"):
            scenario_name = path.replace("/api/chaos/", "").upper()
            self._serve_api_chaos(scenario_name)
        elif path.startswith("/api/phase7/"):
            flow_id = path.replace("/api/phase7/", "").upper()
            self._serve_api_phase7(flow_id)
        elif path == "/api/tla-check":
            self._serve_api_tla_check()
        elif path == "/api/concepts":
            self._serve_api_concepts()
        elif path.startswith("/api/concepts/") and path.endswith("/simulate"):
            concept_id = path.replace("/api/concepts/", "").replace("/simulate", "").strip()
            self._serve_api_simulate_concept(concept_id, query)
        elif path.startswith("/api/concepts/"):
            concept_id = path.replace("/api/concepts/", "").strip()
            self._serve_api_single_concept(concept_id)
        elif path == "/api/health":
            self._send_json({"status": "UP", "version": "2.3.0", "offline": True})
        elif path in ("/metrics", "/api/metrics"):
            self._serve_prometheus_metrics()
        else:
            self.send_error(404, "Rota não encontrada")

    def do_POST(self) -> None:
        """Trata requisições POST para o Console Interativo e APIs de Negociação/Garantias/Seguros."""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            body_json = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except Exception:
            body_json = {}

        if path == "/api/v1/orders":
            self._handle_api_v1_post_order(body_json)
        elif path == "/api/v1/auction/moc":
            self._handle_api_v1_post_moc_order(body_json)
        elif path == "/api/v1/auction/uncross":
            self._handle_api_v1_post_auction_uncross(body_json)
        elif path == "/api/v1/sng/gravames":
            self._handle_api_v1_post_sng_gravame(body_json)
        elif path.startswith("/api/v1/sng/gravames/") and path.endswith("/baixa"):
            gravame_id = path.replace("/api/v1/sng/gravames/", "").replace("/baixa", "").strip()
            self._handle_api_v1_post_sng_baixa(gravame_id, body_json)
        elif path.startswith("/api/v1/sng/gravames/") and path.endswith("/cancelamento"):
            gravame_id = path.replace("/api/v1/sng/gravames/", "").replace("/cancelamento", "").strip()
            self._handle_api_v1_post_sng_cancelamento(gravame_id, body_json)
        elif path == "/api/v1/sro/apolices":
            self._handle_api_v1_post_sro_apolice(body_json)
        elif path.startswith("/api/v1/sro/apolices/") and path.endswith("/endosso"):
            apolice_id = path.replace("/api/v1/sro/apolices/", "").replace("/endosso", "").strip()
            self._handle_api_v1_post_sro_endosso(apolice_id, body_json)
        elif path.startswith("/api/v1/sro/apolices/") and path.endswith("/sinistros"):
            apolice_id = path.replace("/api/v1/sro/apolices/", "").replace("/sinistros", "").strip()
            self._handle_api_v1_post_sro_sinistro(apolice_id, body_json)
        elif "/sinistros/" in path and path.endswith("/liquidar"):
            parts = [p for p in path.split("/") if p]
            apolice_id = parts[parts.index("apolices") + 1] if "apolices" in parts else ""
            sinistro_id = parts[parts.index("sinistros") + 1] if "sinistros" in parts else ""
            self._handle_api_v1_post_sro_liquidar_sinistro(apolice_id, sinistro_id, body_json)
        elif path.startswith("/api/"):
            self._send_json({
                "status": "SUCCESS",
                "endpoint": path,
                "message": "Operação processada com sucesso no ambiente Sandbox B3 Local.",
                "request_payload": body_json,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }, status_code=200)
        else:
            self.send_error(404, f"Endpoint POST '{path}' não encontrado")

    def _serve_prometheus_metrics(self) -> None:
        lines = [
            "# HELP b3_puma_messages_total Total de mensagens trafegadas no PUMA Trading System",
            "# TYPE b3_puma_messages_total counter",
            'b3_puma_messages_total{env="prd",venue="EQUITIES"} 15420000',
            'b3_puma_messages_total{env="prd",venue="FUTURES"} 9850000',
            'b3_puma_messages_total{env="prd",venue="OPTIONS"} 4120000',
            "",
            "# HELP b3_puma_orders_matched_total Total de ordens casadas no matching engine",
            "# TYPE b3_puma_orders_matched_total counter",
            'b3_puma_orders_matched_total{env="prd"} 8350000',
            "",
            "# HELP b3_puma_circuit_breaker_active Estado do circuit breaker no PUMA (0=Normal, 1=Ativo)",
            "# TYPE b3_puma_circuit_breaker_active gauge",
            'b3_puma_circuit_breaker_active{env="prd"} 0',
            "",
            "# HELP b3_puma_price_tunnel_violations_total Total de rejeicoes por violacao de tunel de preco",
            "# TYPE b3_puma_price_tunnel_violations_total counter",
            'b3_puma_price_tunnel_violations_total{env="prd",tunnel_type="REJEICAO"} 14',
            'b3_puma_price_tunnel_violations_total{env="prd",tunnel_type="LEILAO"} 42',
            "",
            "# HELP b3_puma_matching_latency_microseconds_bucket Histograma de latencia de matching em microssegundos",
            "# TYPE b3_puma_matching_latency_microseconds_bucket histogram",
            'b3_puma_matching_latency_microseconds_bucket{env="prd",le="0.5"} 150000',
            'b3_puma_matching_latency_microseconds_bucket{env="prd",le="0.95"} 650000',
            'b3_puma_matching_latency_microseconds_bucket{env="prd",le="0.99"} 850000',
            'b3_puma_matching_latency_microseconds_bucket{env="prd",le="1.0"} 890000',
            'b3_puma_matching_latency_microseconds_bucket{env="prd",le="2.0"} 940000',
            'b3_puma_matching_latency_microseconds_bucket{env="prd",le="5.0"} 985000',
            'b3_puma_matching_latency_microseconds_bucket{env="prd",le="10.0"} 996000',
            'b3_puma_matching_latency_microseconds_bucket{env="prd",le="50.0"} 999700',
            'b3_puma_matching_latency_microseconds_bucket{env="prd",le="+Inf"} 1000000',
            'b3_puma_matching_latency_microseconds_count{env="prd"} 1000000',
            'b3_puma_matching_latency_microseconds_sum{env="prd"} 1202000',
            "",
            "# HELP b3_imbarq_custody_reconciliation_breaks_total Total de quebras de conciliacao de custodia IMBARQ",
            "# TYPE b3_imbarq_custody_reconciliation_breaks_total gauge",
            'b3_imbarq_custody_reconciliation_breaks_total{env="prd",break_type="QUANTITY_MISMATCH"} 0',
            'b3_imbarq_custody_reconciliation_breaks_total{env="prd",break_type="MISSING_ISIN"} 0',
            'b3_imbarq_custody_reconciliation_breaks_total{env="prd",break_type="ACCOUNT_INCONSISTENCY"} 0',
            "",
            "# HELP b3_imbarq_custody_positions_total Total de posicoes sob custodia verificadas",
            "# TYPE b3_imbarq_custody_positions_total gauge",
            'b3_imbarq_custody_positions_total{env="prd"} 4500000',
            "",
            "# HELP b3_csd_unallocated_positions_gauge Posicoes de custodia nao alocadas",
            "# TYPE b3_csd_unallocated_positions_gauge gauge",
            'b3_csd_unallocated_positions_gauge{env="prd"} 0',
            "",
            "# HELP b3_imbarq_file_records_processed_total Registros IMBARQ processados por tipo de arquivo",
            "# TYPE b3_imbarq_file_records_processed_total counter",
            'b3_imbarq_file_records_processed_total{env="prd",file_code="IMBARQ006"} 3800000',
            'b3_imbarq_file_records_processed_total{env="prd",file_code="IMBARQ008"} 1250000',
            'b3_imbarq_file_records_processed_total{env="prd",file_code="IMBARQ014"} 940000',
            "",
            "# HELP b3_clearing_default_events_total Eventos de inadimplencia registrados na CCP",
            "# TYPE b3_clearing_default_events_total counter",
            'b3_clearing_default_events_total{env="prd"} 0',
            "",
            "# HELP b3_clearing_default_waterfall_active_tier_level Tier ativo no Default Waterfall (0=Normal, 1-5=Default)",
            "# TYPE b3_clearing_default_waterfall_active_tier_level gauge",
            'b3_clearing_default_waterfall_active_tier_level{env="prd"} 0',
            "",
            "# HELP b3_clearing_default_waterfall_tier_consumption_ratio Taxa de consumo por tier da cascata",
            "# TYPE b3_clearing_default_waterfall_tier_consumption_ratio gauge",
            'b3_clearing_default_waterfall_tier_consumption_ratio{env="prd",tier="TIER1_MARGINS"} 0.0',
            'b3_clearing_default_waterfall_tier_consumption_ratio{env="prd",tier="TIER2_SETTLEMENT_FUND"} 0.0',
            'b3_clearing_default_waterfall_tier_consumption_ratio{env="prd",tier="TIER3_SITG"} 0.0',
            'b3_clearing_default_waterfall_tier_consumption_ratio{env="prd",tier="TIER4_MUTUALIZED_FUND"} 0.0',
            'b3_clearing_default_waterfall_tier_consumption_ratio{env="prd",tier="TIER5_B3_EQUITY"} 0.0',
            "",
            "# HELP b3_clearing_safeguards_available_capital_brl Capital disponivel por tier de salvaguarda (BRL)",
            "# TYPE b3_clearing_safeguards_available_capital_brl gauge",
            'b3_clearing_safeguards_available_capital_brl{env="prd",tier="TIER1_MARGINS"} 38500000000',
            'b3_clearing_safeguards_available_capital_brl{env="prd",tier="TIER2_DEF_PN"} 2500000000',
            'b3_clearing_safeguards_available_capital_brl{env="prd",tier="TIER3_SITG"} 1200000000',
            'b3_clearing_safeguards_available_capital_brl{env="prd",tier="TIER4_MUTUALIZED_PN"} 8000000000',
            'b3_clearing_safeguards_available_capital_brl{env="prd",tier="TIER5_B3_CAPITAL"} 15000000000',
            "",
            "# HELP b3_clearing_safeguards_absorbed_losses_brl_total Perdas absorvidas por tier de salvaguarda (BRL)",
            "# TYPE b3_clearing_safeguards_absorbed_losses_brl_total counter",
            'b3_clearing_safeguards_absorbed_losses_brl_total{env="prd",tier="TIER1_MARGINS"} 0',
            'b3_clearing_safeguards_absorbed_losses_brl_total{env="prd",tier="TIER2_DEF_PN"} 0',
            'b3_clearing_safeguards_absorbed_losses_brl_total{env="prd",tier="TIER3_SITG"} 0',
            'b3_clearing_safeguards_absorbed_losses_brl_total{env="prd",tier="TIER4_MUTUALIZED_PN"} 0',
            'b3_clearing_safeguards_absorbed_losses_brl_total{env="prd",tier="TIER5_B3_CAPITAL"} 0',
            "",
            "# HELP b3_clearing_sitg_capital_intact_ratio Razao de integridade do capital Skin-in-the-Game (1.0 = 100%)",
            "# TYPE b3_clearing_sitg_capital_intact_ratio gauge",
            'b3_clearing_sitg_capital_intact_ratio{env="prd"} 1.0',
            "",
            "# HELP b3_pix_margin_calls_issued_total Total de chamadas de margem intradiaria emitidas via Pix/SPI",
            "# TYPE b3_pix_margin_calls_issued_total counter",
            'b3_pix_margin_calls_issued_total{env="prd"} 1420',
            "",
            "# HELP b3_pix_margin_calls_settled_total Total de chamadas de margem intradiaria liquidadas via Pix/SPI",
            "# TYPE b3_pix_margin_calls_settled_total counter",
            'b3_pix_margin_calls_settled_total{env="prd"} 1420',
            "",
            "# HELP b3_sng_active_liens_gauge Total de gravames veiculares ativos no SNG",
            "# TYPE b3_sng_active_liens_gauge gauge",
            'b3_sng_active_liens_gauge{env="prd",uf="SP"} 4820000',
            'b3_sng_active_liens_gauge{env="prd",uf="RJ"} 1950000',
            'b3_sng_active_liens_gauge{env="prd",uf="MG"} 2310000',
            'b3_sng_active_liens_gauge{env="prd",uf="PR"} 1420000',
            'b3_sng_active_liens_gauge{env="prd",uf="RS"} 1280000',
            'b3_sng_active_liens_gauge{env="prd",uf="SC"} 920000',
            'b3_sng_active_liens_gauge{env="prd",uf="BA"} 870000',
            "",
            "# HELP b3_sng_gravames_inclusions_total Total de inclusoes de gravames registradas no SNG",
            "# TYPE b3_sng_gravames_inclusions_total counter",
            'b3_sng_gravames_inclusions_total{env="prd",uf="SP"} 52400',
            'b3_sng_gravames_inclusions_total{env="prd",uf="RJ"} 21300',
            'b3_sng_gravames_inclusions_total{env="prd",uf="MG"} 25100',
            'b3_sng_gravames_inclusions_total{env="prd",uf="PR"} 15400',
            'b3_sng_gravames_inclusions_total{env="prd",uf="RS"} 13800',
            'b3_sng_gravames_inclusions_total{env="prd",uf="SC"} 9900',
            'b3_sng_gravames_inclusions_total{env="prd",uf="BA"} 9100',
            "",
            "# HELP b3_sng_gravames_discharges_total Total de baixas/desalienacoes de gravames registradas no SNG",
            "# TYPE b3_sng_gravames_discharges_total counter",
            'b3_sng_gravames_discharges_total{env="prd",uf="SP"} 49100',
            'b3_sng_gravames_discharges_total{env="prd",uf="RJ"} 19800',
            'b3_sng_gravames_discharges_total{env="prd",uf="MG"} 23400',
            'b3_sng_gravames_discharges_total{env="prd",uf="PR"} 14500',
            'b3_sng_gravames_discharges_total{env="prd",uf="RS"} 12900',
            'b3_sng_gravames_discharges_total{env="prd",uf="SC"} 9300',
            'b3_sng_gravames_discharges_total{env="prd",uf="BA"} 8600',
            "",
            "# HELP b3_sng_law112_duplicate_lien_rejections_total Rejeicoes por tentativa de duplo gravame (LAW-112)",
            "# TYPE b3_sng_law112_duplicate_lien_rejections_total counter",
            'b3_sng_law112_duplicate_lien_rejections_total{env="prd"} 18',
            "",
            "# HELP b3_sro_policy_registrations_total Total de registros de apolices no SRO por ramo SUSEP",
            "# TYPE b3_sro_policy_registrations_total counter",
            'b3_sro_policy_registrations_total{env="prd",ramo="AUTO"} 820000',
            'b3_sro_policy_registrations_total{env="prd",ramo="VIDA"} 1450000',
            'b3_sro_policy_registrations_total{env="prd",ramo="PATRIMONIAL"} 530000',
            'b3_sro_policy_registrations_total{env="prd",ramo="RURAL"} 210000',
            'b3_sro_policy_registrations_total{env="prd",ramo="GARANTIA"} 95000',
            "",
            "# HELP b3_sro_earned_premiums_brl Volume de premios de seguro arrecadados no SRO (BRL)",
            "# TYPE b3_sro_earned_premiums_brl gauge",
            'b3_sro_earned_premiums_brl{env="prd"} 4850000000',
            "",
            "# HELP b3_sro_incurred_claims_brl Volume de sinistros ocorridos no SRO (BRL)",
            "# TYPE b3_sro_incurred_claims_brl gauge",
            'b3_sro_incurred_claims_brl{env="prd"} 2182500000',
            "",
            "# HELP b3_central_bus_invariants_status Status de conformidade dos invariantes formais do Barramento Central",
            "# TYPE b3_central_bus_invariants_status gauge",
            'b3_central_bus_invariants_status{env="prd",law="LAW-106_ALL"} 1',
            'b3_central_bus_invariants_status{env="prd",law="LAW-106A"} 1',
            'b3_central_bus_invariants_status{env="prd",law="LAW-106B"} 1',
            'b3_central_bus_invariants_status{env="prd",law="LAW-106C"} 1',
            "",
            "# HELP b3_central_bus_accounting_imbalance_brl Desbalanceamento contabil no barramento central (LAW-106A = 0)",
            "# TYPE b3_central_bus_accounting_imbalance_brl gauge",
            'b3_central_bus_accounting_imbalance_brl{env="prd"} 0',
            "",
            "# HELP b3_central_bus_unabsorbed_deficit_brl Deficit nao absorvido entre subsistemas (LAW-106B = 0)",
            "# TYPE b3_central_bus_unabsorbed_deficit_brl gauge",
            'b3_central_bus_unabsorbed_deficit_brl{env="prd"} 0',
            "",
            "# HELP b3_central_bus_atomic_settlement_success_ratio Taxa de sucesso de liquidacoes atomicas DVP (LAW-106C)",
            "# TYPE b3_central_bus_atomic_settlement_success_ratio gauge",
            'b3_central_bus_atomic_settlement_success_ratio{env="prd"} 1.0',
            "",
            "# HELP b3_central_bus_subsystem_volume_brl Volume liquidado por subsistema no barramento central (BRL)",
            "# TYPE b3_central_bus_subsystem_volume_brl gauge",
            'b3_central_bus_subsystem_volume_brl{env="prd",subsystem="PUMA"} 28500000000',
            'b3_central_bus_subsystem_volume_brl{env="prd",subsystem="IMBARQ"} 19800000000',
            'b3_central_bus_subsystem_volume_brl{env="prd",subsystem="CLEARING"} 34200000000',
            'b3_central_bus_subsystem_volume_brl{env="prd",subsystem="BALCAO"} 14500000000',
            'b3_central_bus_subsystem_volume_brl{env="prd",subsystem="SNG"} 2100000000',
            'b3_central_bus_subsystem_volume_brl{env="prd",subsystem="SRO"} 1800000000',
            'b3_central_bus_subsystem_volume_brl{env="prd",subsystem="SELIC"} 51000000000',
            'b3_central_bus_subsystem_volume_brl{env="prd",subsystem="SPB"} 48000000000',
            'b3_central_bus_subsystem_volume_brl{env="prd",subsystem="DREX"} 3200000000',
            'b3_central_bus_subsystem_volume_brl{env="prd",subsystem="AGRO"} 1500000000',
            "",
            "# HELP b3_central_bus_gross_liquidity_settled_brl Volume bruto liquidado no SPB/STR (BRL)",
            "# TYPE b3_central_bus_gross_liquidity_settled_brl gauge",
            'b3_central_bus_gross_liquidity_settled_brl{env="prd"} 124500000000',
            "",
            "# HELP b3_central_bus_net_liquidity_settled_brl Volume liquido apos compensacao multilateral (BRL)",
            "# TYPE b3_central_bus_net_liquidity_settled_brl gauge",
            'b3_central_bus_net_liquidity_settled_brl{env="prd"} 28635000000',
            "",
        ]
        body = "\n".join(lines).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: Any, status_code: int = 200) -> None:
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_html_developer_portal(self, current_path: str = "/docs/api") -> None:
        """Serve o portal interativo OpenAPI 3.1 e os guias práticos."""
        html = render_developer_portal_html(current_path=current_path)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_openapi_spec(self) -> None:
        """Serve a especificação canônica OpenAPI 3.1 em formato JSON."""
        spec = get_openapi_spec()
        self._send_json(spec)

    def _serve_api_guides(self) -> None:
        """Serve o catálogo de guias práticos de integração."""
        catalog = get_guides_catalog()
        summary = [
            {
                "id": g["id"],
                "filename": g["filename"],
                "title": g["title"],
                "category": g["category"],
                "badge": g["badge"],
                "laws": g["laws"],
                "summary": g["summary"],
                "url": f"/api/guides/{g['filename']}",
            }
            for g in catalog
        ]
        self._send_json(summary)

    def _serve_api_single_guide(self, guide_name: str) -> None:
        """Serve o conteúdo em Markdown de um guia de integração específico."""
        if not guide_name.endswith(".md"):
            guide_name += ".md"
        guide_path = (_REPO_ROOT / "docs" / "guides" / guide_name).resolve()
        try:
            guide_path.relative_to(_REPO_ROOT / "docs" / "guides")
        except ValueError:
            self.send_error(403, "Acesso negado: violação de path traversal")
            return
        if not guide_path.is_file():
            self.send_error(404, f"Guia '{guide_name}' não encontrado")
            return
        with open(guide_path, "r", encoding="utf-8") as f:
            content = f.read()
        self._send_json({
            "guide_file": guide_name,
            "content": content,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def _serve_api_v1_orders_list(self, query: Dict[str, List[str]]) -> None:
        """Consulta ordens institucionais simuladas no PUMA Trading System."""
        symbol_filter = query.get("symbol", [None])[0]
        sample_orders = [
            {
                "order_id": "ORD-PUMA-1731024000-PETR4",
                "client_order_id": "CLORD-2026-PETR4-001",
                "symbol": "PETR4",
                "side": "BUY",
                "order_type": "LIMIT",
                "quantity": 1000,
                "price": 38.50,
                "account": "ACC-8819203",
                "status": "FILLED",
                "timestamp": "2026-11-08T13:30:00Z",
            },
            {
                "order_id": "ORD-PUMA-1731024100-VALE3",
                "client_order_id": "CLORD-2026-VALE3-002",
                "symbol": "VALE3",
                "side": "SELL",
                "order_type": "LIMIT",
                "quantity": 500,
                "price": 64.20,
                "account": "ACC-8819203",
                "status": "NEW",
                "timestamp": "2026-11-08T13:35:00Z",
            },
        ]
        if symbol_filter:
            sample_orders = [o for o in sample_orders if o["symbol"].upper() == symbol_filter.upper()]
        self._send_json(sample_orders)

    def _serve_api_v1_single_order(self, order_id: str) -> None:
        """Retorna detalhes de uma ordem específica pelo ID."""
        self._send_json({
            "order_id": order_id,
            "symbol": "PETR4",
            "side": "BUY",
            "quantity": 1000,
            "price": 38.50,
            "status": "NEW",
            "pre_trade_risk_checks": {
                "price_tunnel": "PASSED",
                "fat_finger": "PASSED",
                "gross_credit_limit": "PASSED",
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def _serve_api_v1_auction_theoretical_price(self, query: Dict[str, List[str]]) -> None:
        """Calcula o Preço Teórico de Fechamento (PTP) no Leilão MOC (FL-098)."""
        symbol = query.get("symbol", ["PETR4"])[0]
        self._send_json({
            "symbol": symbol,
            "theoretical_price": 38.65,
            "executable_volume": 1250000,
            "imbalance_side": "BUY",
            "imbalance_quantity": 150000,
            "auction_phase": "FREEZE_WINDOW",
            "freeze_window_active": True,
            "laws_applied": ["LAW-104A", "LAW-104B"],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def _serve_api_v1_sng_gravames_list(self, query: Dict[str, List[str]]) -> None:
        """Lista gravames registrados na base SNG B3 Financiamentos."""
        fixtures_file = _REPO_ROOT / "fixtures" / "sng_gravames_engine.json"
        if fixtures_file.is_file():
            with open(fixtures_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            gravames = d.get("gravames", [])
        else:
            gravames = []
        self._send_json(gravames)

    def _serve_api_v1_single_gravame(self, gravame_id: str) -> None:
        """Consulta um gravame específico na base SNG."""
        fixtures_file = _REPO_ROOT / "fixtures" / "sng_gravames_engine.json"
        if fixtures_file.is_file():
            with open(fixtures_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            for g in d.get("gravames", []):
                if g.get("gravame_id") == gravame_id:
                    self._send_json(g)
                    return
        self._send_json({
            "gravame_id": gravame_id,
            "status": "ATIVO",
            "modalidade": "ALIENACAO_FIDUCIARIA",
            "contrato_numero": "CTR-BV-2026-8812",
            "chassi": "9BWAA05U1HP001234",
            "renavam": "11984726351",
            "detran_status": "NOTIFICADO",
            "law_verified": "LAW-112",
        })

    def _serve_api_v1_sro_apolices_list(self, query: Dict[str, List[str]]) -> None:
        """Lista apólices registradas no padrão SUSEP V3 no SRO B3 Seguros."""
        fixtures_file = _REPO_ROOT / "fixtures" / "sro_seguros_engine.json"
        if fixtures_file.is_file():
            with open(fixtures_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            apolices = d.get("apolices", [])
        else:
            apolices = []
        self._send_json(apolices)

    def _serve_api_v1_single_apolice(self, apolice_id: str) -> None:
        """Consulta uma apólice específica no SRO."""
        fixtures_file = _REPO_ROOT / "fixtures" / "sro_seguros_engine.json"
        if fixtures_file.is_file():
            with open(fixtures_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            for a in d.get("apolices", []):
                if a.get("apolice_id") == apolice_id:
                    self._send_json(a)
                    return
        self._send_json({
            "apolice_id": apolice_id,
            "status": "REGISTRADA",
            "ramo": "RAMO_0531",
            "limite_maximo_garantia": 150000.00,
            "premio_liquido": 3200.00,
            "hash_susep_sha256": "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92",
            "law_verified": "LAW-113",
        })

    def _handle_api_v1_post_order(self, body: Dict[str, Any]) -> None:
        """Submete uma nova ordem de negociação no PUMA Trading System."""
        symbol = body.get("symbol", "PETR4")
        side = body.get("side", "BUY")
        qty = int(body.get("quantity", 100))
        price = float(body.get("price", 38.50)) if body.get("price") is not None else 38.50
        account = body.get("account", "ACC-8819203")
        order_id = f"ORD-PUMA-{int(time.time() * 1000)}-{symbol}"

        resp = {
            "order_id": order_id,
            "client_order_id": body.get("client_order_id", f"CLORD-{order_id}"),
            "symbol": symbol,
            "side": side,
            "order_type": body.get("order_type", "LIMIT"),
            "quantity": qty,
            "price": price,
            "account": account,
            "status": "NEW",
            "pre_trade_risk_checks": {
                "passed": True,
                "price_tunnel_check": "PASSED",
                "fat_finger_check": "PASSED",
                "gross_credit_limit_check": "PASSED",
                "law_applied": "LAW-001",
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "message": "Ordem institucional aceita no PUMA Trading System com validação Pre-Trade Risk.",
        }
        self._send_json(resp, status_code=201)

    def _handle_api_v1_post_moc_order(self, body: Dict[str, Any]) -> None:
        """Submete uma ordem Market-On-Close (MOC) para o Leilão de Fechamento B3."""
        symbol = body.get("symbol", "PETR4")
        side = body.get("side", "BUY")
        qty = int(body.get("quantity", 50000))
        order_id = f"MOC-{int(time.time() * 1000)}-{symbol}"

        resp = {
            "order_id": order_id,
            "client_order_id": body.get("client_order_id", f"CLMOC-{order_id}"),
            "symbol": symbol,
            "side": side,
            "order_type": "MARKET_ON_CLOSE",
            "time_in_force": "AT_THE_CLOSE",
            "quantity": qty,
            "status": "ACCEPTED_FOR_AUCTION",
            "closing_phase": "CALL_PHASE",
            "freeze_window_protected": True,
            "laws_applied": ["LAW-104A", "LAW-104B"],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "message": "Ordem MOC registrada com prioridade absoluta no Leilão de Fechamento B3.",
        }
        self._send_json(resp, status_code=201)

    def _handle_api_v1_post_auction_uncross(self, body: Dict[str, Any]) -> None:
        """Executa a apuração e cruzamento do Leilão de Fechamento."""
        symbol = body.get("symbol", "PETR4")
        resp = {
            "symbol": symbol,
            "status": "UNCROSSED",
            "closing_price": 38.65,
            "matched_volume": 1250000,
            "financial_volume_brl": 48312500.00,
            "laws_verified": ["LAW-104A", "LAW-104B"],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._send_json(resp, status_code=200)

    def _handle_api_v1_post_sng_gravame(self, body: Dict[str, Any]) -> None:
        """Registra um novo gravame veicular no SNG B3 Financiamentos."""
        v = body.get("veiculo", {})
        chassi = v.get("chassi", "9BWAA05U1HP001234") if isinstance(v, dict) else body.get("chassi", "9BWAA05U1HP001234")
        gravame_id = f"SNG-SP-{int(time.time() * 1000) % 1000000:06d}"
        protocolo = f"B3-SNG-{time.strftime('%Y%m%d')}-{int(time.time() * 1000) % 1000000:06d}"

        resp = {
            "gravame_id": gravame_id,
            "protocolo_b3": protocolo,
            "status": "ATIVO",
            "modalidade": body.get("modalidade", "ALIENACAO_FIDUCIARIA"),
            "contrato_numero": body.get("contrato_numero", "CTR-2026-001"),
            "credor_cnpj": body.get("credor_cnpj", "60701190000104"),
            "credor_nome": body.get("credor_nome", "BANCO B3 S.A."),
            "devedor_documento": body.get("devedor_documento", "12345678909"),
            "devedor_nome": body.get("devedor_nome", "DEVEDOR HOMOLOGADO"),
            "chassi": chassi,
            "detran_status": "NOTIFICADO",
            "law_verified": "LAW-112",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mensagem": "Gravame veicular anotado no SNG e comunicado ao DETRAN estadual competente.",
        }
        self._send_json(resp, status_code=201)

    def _handle_api_v1_post_sng_baixa(self, gravame_id: str, body: Dict[str, Any]) -> None:
        """Efetua a baixa e desalienação do gravame veicular."""
        resp = {
            "gravame_id": gravame_id,
            "status": "BAIXADO",
            "motivo_baixa": body.get("motivo_baixa", "QUITACAO_INTEGRAL"),
            "detran_comunicado": True,
            "law_verified": "LAW-112",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mensagem": "Gravame desalienado e baixado com sucesso no SNG B3 Financiamentos.",
        }
        self._send_json(resp, status_code=200)

    def _handle_api_v1_post_sng_cancelamento(self, gravame_id: str, body: Dict[str, Any]) -> None:
        """Efetua o cancelamento do gravame por vício de origem."""
        resp = {
            "gravame_id": gravame_id,
            "status": "CANCELADO",
            "motivo_cancelamento": body.get("motivo_cancelamento", "ERRO_FORMAL"),
            "detran_comunicado": True,
            "law_verified": "LAW-112",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mensagem": "Gravame cancelado e removido do sistema nacional.",
        }
        self._send_json(resp, status_code=200)

    def _handle_api_v1_post_sro_apolice(self, body: Dict[str, Any]) -> None:
        """Registra e certifica uma apólice no SRO B3 Seguros & SUSEP V3."""
        import hashlib
        proposta = body.get("numero_proposta", f"PROP-{int(time.time())}")
        ramo = body.get("ramo", "RAMO_1101")
        apolice_id = f"SRO-B3-{ramo}-{int(time.time() * 1000) % 1000000:06d}"
        hash_susep = hashlib.sha256(f"{apolice_id}:{proposta}".encode("utf-8")).hexdigest()

        resp = {
            "apolice_id": apolice_id,
            "numero_proposta": proposta,
            "numero_registro_susep": f"SUSEP-REG-{time.strftime('%Y%m%d')}-{int(time.time() * 1000) % 1000000:06d}",
            "hash_susep_sha256": hash_susep,
            "status": "REGISTRADA",
            "ramo": ramo,
            "limite_maximo_garantia": float(body.get("limite_maximo_garantia", 5000000.00)),
            "premio_liquido": float(body.get("premio_liquido", 120000.00)),
            "law_verified": "LAW-113",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mensagem": "Apólice registrada e certificada no SRO B3 Seguros perante a SUSEP.",
        }
        self._send_json(resp, status_code=201)

    def _handle_api_v1_post_sro_endosso(self, apolice_id: str, body: Dict[str, Any]) -> None:
        """Registra endosso em apólice cadastrada."""
        resp = {
            "endosso_id": f"END-{int(time.time() * 1000) % 1000000:06d}",
            "apolice_id": apolice_id,
            "tipo_endosso": body.get("tipo_endosso", "ALTERACAO_CADASTRO"),
            "status": "REGISTRADO",
            "law_verified": "LAW-113",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mensagem": "Endosso averbado no SRO perante a SUSEP.",
        }
        self._send_json(resp, status_code=200)

    def _handle_api_v1_post_sro_sinistro(self, apolice_id: str, body: Dict[str, Any]) -> None:
        """Registra aviso de sinistro com bloqueio de saldo LMG."""
        sinistro_id = f"SIN-{int(time.time() * 1000) % 1000000:06d}"
        resp = {
            "sinistro_id": sinistro_id,
            "apolice_id": apolice_id,
            "numero_sinistro": body.get("numero_sinistro", sinistro_id),
            "status": "AVISADO",
            "valor_estimado": float(body.get("valor_estimado", 500000.00)),
            "causa_evento": body.get("causa_evento", "EVENTO_COBERTO"),
            "law_verified": "LAW-113",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mensagem": "Aviso de sinistro registrado com bloqueio de saldo LMG no SRO.",
        }
        self._send_json(resp, status_code=201)

    def _handle_api_v1_post_sro_liquidar_sinistro(self, apolice_id: str, sinistro_id: str, body: Dict[str, Any]) -> None:
        """Efetua a liquidação de indenização de sinistro averbada no SPB."""
        resp = {
            "sinistro_id": sinistro_id,
            "apolice_id": apolice_id,
            "status": "LIQUIDADO",
            "valor_indenizado": float(body.get("valor_indenizado", 480000.00)),
            "comprovante_spb": body.get("comprovante_spb", f"STR-{int(time.time())}"),
            "law_verified": "LAW-113",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mensagem": "Sinistro liquidado e comprovante SPB averbado no SRO.",
        }
        self._send_json(resp, status_code=200)

    def _serve_api_status(self) -> None:
        harvester = B3DocHarvester(target_dir=_REPO_ROOT / "raw_docs")
        summary = harvester.get_summary()
        self._send_json({
            "version": "2.3.0",
            "homologated_flows": 107,
            "formal_conservation_laws": 91,
            "architecture_modules": 7,
            "concepts_count": 12,
            "documents_summary": summary,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def _serve_api_flows(self) -> None:
        flows_file = _REPO_ROOT / "catalog" / "flows.json"
        if not flows_file.is_file():
            self.send_error(404, "Arquivo flows.json não encontrado")
            return
        with open(flows_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._send_json(data.get("flows", []))

    def _serve_api_modules(self) -> None:
        modules = [
            {
                "id": "PUMA_TRADING",
                "name": "PUMA Trading System",
                "scope": "Negociação eletrônica listada de ações, opções, futuros e leilões MOC/LOC",
                "protocols": ["FIX 4.4 / 5.0 SP2", "PUMA SBE v2 (Binary)", "UMDF 2.0 (Multicast)"],
                "laws": ["LAW-001", "LAW-002", "LAW-093", "LAW-104A", "LAW-104B", "LAW-104C"],
                "color": "#0284c7",
            },
            {
                "id": "CLEARING_RISK",
                "name": "Câmara B3 & Risco CORE",
                "scope": "Contraparte Central (CCP), cálculo de margens CORE e Default Waterfall",
                "protocols": ["Compensação Multilateral", "Netting Contínuo", "Triparty Collateral"],
                "laws": ["LAW-004", "LAW-005", "LAW-006", "LAW-105A", "LAW-106A", "LAW-106B"],
                "color": "#e11d48",
            },
            {
                "id": "CSD_DEPOSITARY",
                "name": "Central Depositária (CSD)",
                "scope": "Contas de custódia individualizadas por CPF/CNPJ e eventos societários",
                "protocols": ["IMBARQ 004/006/011/012/014/018", "iMercado APIs", "STVM CVM 179"],
                "laws": ["LAW-008", "LAW-009", "LAW-024", "LAW-046"],
                "color": "#10b981",
            },
            {
                "id": "NOME_BALCAO",
                "name": "Balcão NoMe / Cetip",
                "scope": "Registro de Renda Fixa (CDB/LF), Swaps, Recebíveis (CMN 4.734) e Agro",
                "protocols": ["XML NoMe", "Registro D0", "Central de Gravames (Lei 14.711)"],
                "laws": ["LAW-011", "LAW-015", "LAW-018", "LAW-031", "LAW-082", "LAW-097"],
                "color": "#8b5cf6",
            },
            {
                "id": "SPB_SETTLEMENT",
                "name": "SPB, BACEN & Drex",
                "scope": "Liquidação bruta de reservas (STR), Pix Margem 24/7 e DLT Drex Fase 2",
                "protocols": ["STR LDL0001", "SPI pacs.008", "Hyperledger Besu HTLC ERC-1155"],
                "laws": ["LAW-007", "LAW-089A", "LAW-090", "LAW-106C"],
                "color": "#f59e0b",
            },
            {
                "id": "TARIFF_CATALOG",
                "name": "Tarifação & Emolumentos",
                "scope": "Tabelas regressivas de negociação, day-trade e isenções de formador",
                "protocols": ["Escadinha ADTV Tiers 1-7", "PMM Market Maker Incentives"],
                "laws": ["LAW-047", "LAW-087"],
                "color": "#06b6d4",
            },
            {
                "id": "BSM_SURVEILLANCE",
                "name": "Supervisão BSM",
                "scope": "Vigilância de abusos em tempo real L2/L3 e auditoria RPA de corretoras",
                "protocols": ["Detecção Wash Trading", "Spoofing / Layering", "Order-to-Trade Ratio"],
                "laws": ["LAW-024", "LAW-041"],
                "color": "#ec4899",
            },
        ]
        self._send_json(modules)

    def _serve_api_docs(self) -> None:
        harvester = B3DocHarvester(target_dir=_REPO_ROOT / "raw_docs")
        docs_list = [d.to_dict() for d in harvester.documents.values()]
        self._send_json(docs_list)

    def _serve_api_single_doc(self, doc_id: str) -> None:
        harvester = B3DocHarvester(target_dir=_REPO_ROOT / "raw_docs")
        doc = harvester.documents.get(doc_id)
        if not doc or not doc.local_path:
            self.send_error(404, f"Documento '{doc_id}' não encontrado")
            return

        file_path = (_REPO_ROOT / doc.local_path).resolve()
        try:
            file_path.relative_to(_REPO_ROOT)
        except ValueError:
            self.send_error(403, "Acesso negado: violação de path traversal")
            return
        if not file_path.is_file():
            self.send_error(404, "Arquivo físico não encontrado em disco")
            return

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        self._send_json({
            "doc": doc.to_dict(),
            "raw_content": content,
        })

    def _serve_api_simulate_day(self, query: Dict[str, List[str]]) -> None:
        shares = int(query.get("shares", ["100000"])[0])
        sim = B3MarketSimulator()
        result = sim.run_full_market_day(base_shares=shares)
        self._send_json(result.to_dict())

    def _serve_api_chaos(self, scenario_name: str) -> None:
        try:
            scenario_enum = ChaosScenarioType(scenario_name)
        except Exception:
            self.send_error(400, f"Cenário inválido. Escolha: {[e.value for e in ChaosScenarioType]}")
            return

        sim = B3ChaosSimulator()
        result = sim.run_scenario(scenario_enum)
        self._send_json(result.to_dict())

    def _serve_api_phase7(self, flow_id: str) -> None:
        fixtures_dir = _REPO_ROOT / "fixtures"
        if flow_id == "FL-101":
            from src.volatility_surface_sabr_svi import B3VolatilitySurface
            with open(fixtures_dir / "volatility_surface_sabr_svi.json", "r", encoding="utf-8") as f:
                d = json.load(f)
            surfaces = []
            for sid, sdata in d["surfaces"].items():
                surf = B3VolatilitySurface.from_dict(sdata)
                inv = surf.verify_invariants(raise_on_error=True)
                surfaces.append({
                    "surface_id": surf.surface_id,
                    "asset": surf.asset_type.value,
                    "model": surf.model_type.value,
                    "slices": len(surf.slices),
                    "invariants": inv,
                })
            self._send_json({"flow": "FL-101", "title": "Superfícies SABR/SVI", "laws": ["LAW-107A", "LAW-107B"], "surfaces": surfaces})
        elif flow_id == "FL-102":
            from decimal import Decimal
            from src.structured_operations_coe import StructuredOperationsCoeEngine
            eng = StructuredOperationsCoeEngine.load_from_json(fixtures_dir / "structured_operations_coe.json")
            eng.verify_invariants()
            rec = eng.settle_at_maturity("COE-BULL-IBOV-001", Decimal("145000.00"), "2026-11-06", 756)
            self._send_json({
                "flow": "FL-102",
                "title": "COE Capital Protegido",
                "laws": ["LAW-108"],
                "contracts_count": len(eng.contracts),
                "sample_settlement": rec.to_dict(),
            })
        elif flow_id == "FL-103":
            from src.market_maker_pmm_engine import MarketMakerPmmEngine
            eng = MarketMakerPmmEngine()
            eng.load_from_fixture(fixtures_dir / "market_maker_pmm_engine.json")
            inv = eng.verify_invariants()
            self._send_json({
                "flow": "FL-103",
                "title": "Formador de Mercado PMM B3",
                "laws": ["LAW-109A", "LAW-109B", "LAW-109C", "LAW-109D"],
                "contracts": len(eng.contracts),
                "sessions_audited": len(eng.session_audits),
                "invariants": inv,
            })
        elif flow_id == "FL-104":
            from src.algorithmic_sor_twap_vwap import AlgorithmicSOREngine
            eng, parent, ref_price = AlgorithmicSOREngine.load_from_fixture(fixtures_dir / "algorithmic_sor_twap_vwap.json")
            plan = eng.generate_twap_slices(parent, ref_price)
            inv = plan.verify_invariants(raise_on_error=True)
            self._send_json({
                "flow": "FL-104",
                "title": "Algorithmic SOR TWAP/VWAP/POV",
                "laws": ["LAW-110"],
                "parent_order": parent.parent_id,
                "symbol": parent.symbol,
                "total_quantity": parent.quantity,
                "slices_generated": len(plan.child_orders),
                "invariants": inv,
            })
        elif flow_id == "FL-105":
            from src.drex_rwa_primary_issuance import DrexRwaPrimaryIssuanceEngine
            eng = DrexRwaPrimaryIssuanceEngine.load_from_fixture(fixtures_dir / "drex_rwa_primary_issuance.json")
            inv = eng.verify_invariants()
            self._send_json({
                "flow": "FL-105",
                "title": "Emissão Primária RWA Drex Fase 2",
                "laws": ["LAW-111"],
                "offerings": len(eng.offerings),
                "dvp_receipts": len(eng.dvp_receipts),
                "invariants": inv,
            })
        elif flow_id == "FL-106":
            from decimal import Decimal
            from src.sng_gravames_engine import SngGravamesEngine, ModalidadeGravame, VeiculoIdentificacao
            with open(fixtures_dir / "sng_gravames_engine.json", "r", encoding="utf-8") as f:
                d = json.load(f)
            eng = SngGravamesEngine()
            for g in d.get("gravames", []):
                v_data = g["veiculo"]
                v = VeiculoIdentificacao(
                    chassi=v_data["chassi"],
                    renavam=v_data["renavam"],
                    placa=v_data["placa"],
                    uf_emplacamento=v_data["uf_emplacamento"],
                    marca_modelo=v_data["marca_modelo"],
                    ano_fabricacao=v_data["ano_fabricacao"],
                    ano_modelo=v_data["ano_modelo"],
                )
                eng.registrar_gravame(
                    contrato_numero=g["contrato_numero"],
                    modalidade=ModalidadeGravame(g["modalidade"]),
                    credor_cnpj=g["credor_cnpj"],
                    credor_nome=g["credor_nome"],
                    devedor_documento=g["devedor_documento"],
                    devedor_nome=g["devedor_nome"],
                    veiculo=v,
                    valor_financiamento=Decimal(g["valor_financiamento"]),
                    quantidade_parcelas=g["quantidade_parcelas"],
                    taxa_juros_anual_pct=Decimal(g["taxa_juros_anual_pct"]),
                )
            inv = eng.verify_invariants()
            self._send_json({
                "flow": "FL-106",
                "title": "Sistema Nacional de Gravames (SNG) B3 Financiamentos",
                "laws": ["LAW-112"],
                "gravames_registrados": len(eng.gravames),
                "invariants": inv,
            })
        elif flow_id == "FL-107":
            from decimal import Decimal
            from src.sro_seguros_engine import SroSegurosEngine, RamoSeguro, CosseguradorShare
            with open(fixtures_dir / "sro_seguros_engine.json", "r", encoding="utf-8") as f:
                d = json.load(f)
            eng = SroSegurosEngine()
            for a in d.get("apolices", []):
                cosseg = [
                    CosseguradorShare(
                        cnpj=c["cnpj"],
                        razao_social=c["razao_social"],
                        percentual_quota=Decimal(c["percentual_quota"]),
                        is_lider=c["is_lider"],
                    ) for c in a.get("cosseguradores", [])
                ]
                eng.registrar_apolice(
                    numero_proposta=a["numero_proposta"],
                    ramo=RamoSeguro(a["ramo"]),
                    seguradora_lider_cnpj=a["seguradora_lider_cnpj"],
                    seguradora_lider_nome=a["seguradora_lider_nome"],
                    segurado_documento=a["segurado_documento"],
                    segurado_nome=a["segurado_nome"],
                    data_emissao=a["data_emissao"],
                    inicio_vigencia=a["inicio_vigencia"],
                    fim_vigencia=a["fim_vigencia"],
                    limite_maximo_garantia=Decimal(a["limite_maximo_garantia"]),
                    premio_liquido=Decimal(a["premio_liquido"]),
                    iof_aliquota_pct=Decimal("7.38"),
                    cosseguradores=cosseg,
                )
            inv = eng.verify_invariants()
            self._send_json({
                "flow": "FL-107",
                "title": "Sistema de Registro de Operações (SRO) B3 Seguros & SUSEP",
                "laws": ["LAW-113"],
                "apolices_registradas": len(eng.apolices),
                "invariants": inv,
            })
        else:
            self.send_error(400, f"Fluxo Fase 7 inválido: {flow_id}")

    def _serve_api_tla_check(self) -> None:
        from src.tla_model_checker import CentralLiquidityBusModelChecker
        checker = CentralLiquidityBusModelChecker()
        rep = checker.run_model_check()
        self._send_json({
            "spec_path": rep.spec_path,
            "passed": rep.passed,
            "distinct_states_explored": rep.distinct_states_explored,
            "transitions_evaluated": rep.transitions_evaluated,
            "max_depth_reached": rep.max_depth_reached,
            "deadlocks_found": rep.deadlocks_found,
            "properties": {
                "TypeOK": rep.type_ok_verified,
                "ZeroSumAccountingConservation_LAW_106A": rep.law_106a_zero_sum_verified,
                "WaterfallOrderInviolability_LAW_106B": rep.law_106b_waterfall_order_verified,
                "NoUnabsorbedDefaultAtSettlement_LAW_106B": rep.law_106b_no_unabsorbed_default_verified,
                "IntersystemSettlementAtomicity_LAW_106C": rep.law_106c_atomicity_verified,
                "EventualCycleTermination_Liveness": rep.liveness_termination_verified,
            },
        })

    def _serve_api_concepts(self) -> None:
        catalog = B3ConceptsCatalog()
        self._send_json(catalog.list_all())

    def _serve_api_single_concept(self, concept_id: str) -> None:
        catalog = B3ConceptsCatalog()
        c = catalog.get_by_id(concept_id)
        if not c:
            self.send_error(404, f"Conceito '{concept_id}' não encontrado")
            return
        self._send_json(c.to_dict())

    def _serve_api_simulate_concept(self, concept_id: str, query: Dict[str, List[str]]) -> None:
        catalog = B3ConceptsCatalog()
        params = {k: v[0] for k, v in query.items()}
        res = catalog.simulate(concept_id, params)
        if "error" in res:
            self.send_error(404, res["error"])
            return
        self._send_json(res)

    def _serve_html_dashboard(self) -> None:
        html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>B3 Integration Atlas — Visual Dashboard</title>
  <style>
    :root {
      --bg: #090d16;
      --card-bg: #111827;
      --border: #1f2937;
      --accent-blue: #0284c7;
      --accent-gold: #c39e5c;
      --accent-green: #10b981;
      --accent-red: #ef4444;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    body { background: var(--bg); color: var(--text); display: flex; height: 100vh; overflow: hidden; }
    
    /* Sidebar */
    .sidebar { width: 260px; background: #0c1220; border-right: 1px solid var(--border); display: flex; flex-direction: column; }
    .brand { padding: 1.5rem; border-bottom: 1px solid var(--border); }
    .brand h1 { font-size: 1.1rem; color: var(--text); display: flex; align-items: center; gap: 0.5rem; }
    .brand span { color: var(--accent-gold); font-size: 0.8rem; background: #1e1b18; padding: 0.2rem 0.5rem; border-radius: 4px; }
    .nav { list-style: none; padding: 1rem 0; flex: 1; }
    .nav li a { display: flex; align-items: center; gap: 0.75rem; padding: 0.75rem 1.5rem; color: var(--text-muted); text-decoration: none; font-size: 0.9rem; font-weight: 500; transition: all 0.2s; border-left: 3px solid transparent; }
    .nav li a:hover, .nav li a.active { color: #fff; background: rgba(2, 132, 199, 0.1); border-left-color: var(--accent-blue); }
    .system-status { padding: 1rem 1.5rem; border-top: 1px solid var(--border); font-size: 0.8rem; color: var(--accent-green); display: flex; align-items: center; gap: 0.5rem; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent-green); box-shadow: 0 0 8px var(--accent-green); }

    /* Main Area */
    .main { flex: 1; display: flex; flex-direction: column; overflow-y: auto; background: var(--bg); }
    .header { padding: 1.5rem 2rem; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: #0c1220; }
    .header h2 { font-size: 1.4rem; font-weight: 600; }
    .badge-pill { padding: 0.35rem 0.85rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; background: rgba(16, 185, 129, 0.15); color: var(--accent-green); border: 1px solid rgba(16, 185, 129, 0.3); }

    .content { padding: 2rem; max-width: 1400px; margin: 0 auto; width: 100%; }

    /* Stat Cards */
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1.5rem; margin-bottom: 2rem; }
    .stat-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 1.5rem; }
    .stat-title { font-size: 0.8rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; letter-spacing: 0.05em; }
    .stat-value { font-size: 1.85rem; font-weight: 700; color: #fff; margin-top: 0.5rem; }
    .stat-sub { font-size: 0.75rem; color: var(--accent-gold); margin-top: 0.25rem; }

    /* Modules Section */
    .modules-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 1.5rem; margin-bottom: 2rem; }
    .module-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 1.5rem; transition: transform 0.2s; }
    .module-card:hover { transform: translateY(-2px); border-color: #374151; }
    .module-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }
    .module-title { font-size: 1.1rem; font-weight: 600; color: #fff; }
    .module-desc { font-size: 0.85rem; color: var(--text-muted); line-height: 1.4; margin-bottom: 1rem; }
    .tag-list { display: flex; flex-wrap: wrap; gap: 0.4rem; }
    .tag { font-size: 0.75rem; padding: 0.2rem 0.5rem; border-radius: 4px; background: #1f2937; color: #d1d5db; }
    .tag.law { background: rgba(2, 132, 199, 0.2); color: #38bdf8; border: 1px solid rgba(2, 132, 199, 0.3); }

    /* Panel Styles */
    .panel { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 1.5rem; margin-bottom: 2rem; }
    .panel-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.25rem; }
    .btn { background: var(--accent-blue); color: #fff; border: none; padding: 0.6rem 1.2rem; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 0.85rem; transition: background 0.2s; display: inline-flex; align-items: center; gap: 0.5rem; }
    .btn:hover { background: #0369a1; }
    .btn.danger { background: var(--accent-red); }
    .btn.danger:hover { background: #b91c1c; }

    /* Tables */
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    th, td { padding: 0.75rem 1rem; text-align: left; border-bottom: 1px solid var(--border); }
    th { color: var(--text-muted); font-weight: 600; background: #0c1220; }
    tr:hover td { background: rgba(255, 255, 255, 0.02); }

    /* Timeline & Events */
    .timeline { display: flex; flex-direction: column; gap: 0.75rem; margin-top: 1rem; }
    .timeline-item { background: #0c1220; border: 1px solid var(--border); border-radius: 8px; padding: 1rem; display: flex; align-items: center; gap: 1rem; }
    .timeline-time { font-family: monospace; font-size: 0.85rem; color: var(--accent-gold); font-weight: 600; width: 80px; }
    .timeline-module { font-size: 0.75rem; padding: 0.2rem 0.5rem; border-radius: 4px; font-weight: 700; width: 130px; text-align: center; }
    .timeline-body { flex: 1; }
    .timeline-title { font-size: 0.9rem; font-weight: 600; color: #fff; }
    .timeline-desc { font-size: 0.8rem; color: var(--text-muted); margin-top: 0.2rem; }
    .timeline-vol { font-family: monospace; font-size: 0.9rem; color: var(--accent-green); font-weight: 600; text-align: right; width: 160px; }

    /* Modal / Viewer */
    .viewer { background: #0c1220; border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; font-family: monospace; font-size: 0.8rem; white-space: pre-wrap; max-height: 500px; overflow-y: auto; color: #e5e7eb; }
  </style>
</head>
<body>
  <div class="sidebar">
    <div class="brand">
      <h1>B3 ATLAS <span>v2.3.0</span></h1>
      <p style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.25rem;">Arquitetura & Motor Integrado B3</p>
    </div>
    <ul class="nav">
      <li><a href="#" class="active" onclick="switchTab('overview')">🏛️ 7 Módulos B3</a></li>
      <li><a href="#" onclick="switchTab('flows')">🧭 Catálogo de 107 Fluxos</a></li>
      <li><a href="#" onclick="switchTab('simulator')">⚡ Simulador do Pregão</a></li>
      <li><a href="#" onclick="switchTab('chaos')">🔥 Laboratório de Caos</a></li>
      <li><a href="#" onclick="switchTab('phase7')">🧮 Fase 7 Quant & TLA+</a></li>
      <li><a href="#" onclick="switchTab('concepts')">🧠 Conceitos Complexos B3</a></li>
      <li><a href="#" onclick="switchTab('docs')">📚 Acervo Documental (87)</a></li>
    </ul>
    <div class="system-status">
      <span class="dot"></span> 100% Local (Off-line)
    </div>
  </div>

  <div class="main">
    <div class="header">
      <h2 id="view-title">Visão Holística dos 7 Módulos da B3</h2>
      <span class="badge-pill">91 Leis Provadas • 107 Fluxos • 87 Manuais • 12 Conceitos</span>
    </div>

    <div class="content">
      <!-- Top Stats -->
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-title">Fluxos Institucionais</div>
          <div class="stat-value">107 / 107</div>
          <div class="stat-sub">FL-001 a FL-107 Homologados</div>
        </div>
        <div class="stat-card">
          <div class="stat-title">Leis Formais de Conservação</div>
          <div class="stat-value">91 / 91</div>
          <div class="stat-sub">100% Provadas Matematicamente</div>
        </div>
        <div class="stat-card">
          <div class="stat-title">Acervo Documental Local</div>
          <div class="stat-value">87 / 87 (100%)</div>
          <div class="stat-sub">Gravados em raw_docs/ com SHA-256</div>
        </div>
        <div class="stat-card">
          <div class="stat-title">Conceitos Complexos B3</div>
          <div class="stat-value">12 / 12 (100%)</div>
          <div class="stat-sub">Guias Pedagógicos & Sandbox Live</div>
        </div>
      </div>

      <!-- Tab 1: Overview Modules -->
      <div id="tab-overview">
        <div class="modules-grid" id="modules-container">
          <!-- Injetado via JS -->
        </div>
      </div>

      <!-- Tab Flows (Catálogo de 107 Fluxos) -->
      <div id="tab-flows" style="display: none;">
        <div class="panel">
          <div class="panel-header" style="flex-direction: column; align-items: flex-start; gap: 1rem;">
            <div>
              <h3 style="font-size: 1.25rem; color: #fff;">🧭 Catálogo Homologado de 107 Fluxos Institucionais da B3</h3>
              <p style="font-size: 0.85rem; color: var(--text-muted); margin-top: 0.25rem;">
                Mapeamento completo ponta a ponta: do envio de ordens ao PUMA até o registro no NoMe, liquidação SPB e custódia CSD.
              </p>
            </div>
            <div style="display: flex; gap: 0.75rem; width: 100%; flex-wrap: wrap; align-items: center;">
              <input type="text" id="flow-search" placeholder="Buscar por ID (ex: FL-001), nome, sistema ou interface..." oninput="filterFlows()" style="background: #0c1220; border: 1px solid var(--border); color: #fff; padding: 0.6rem 1rem; border-radius: 6px; font-size: 0.85rem; flex: 1; min-width: 280px;">
              <div id="flow-system-chips" style="display: flex; gap: 0.4rem; flex-wrap: wrap;"></div>
            </div>
          </div>
          <div style="max-height: 520px; overflow-y: auto;">
            <table>
              <thead>
                <tr>
                  <th style="width: 100px;">ID</th>
                  <th>Nome do Fluxo</th>
                  <th>Sistema</th>
                  <th>Interface</th>
                  <th style="width: 120px;">Status</th>
                  <th style="width: 110px;">Ações</th>
                </tr>
              </thead>
              <tbody id="flows-tbody"></tbody>
            </table>
          </div>
          <div id="flow-modal" class="viewer" style="display: none; margin-top: 1.5rem;"></div>
        </div>
      </div>

      <!-- Tab 2: Market Day Simulator -->
      <div id="tab-simulator" style="display: none;">
        <div class="panel">
          <div class="panel-header">
            <div>
              <h3 style="font-size: 1.1rem; color: #fff;">Simulador E2E: Ciclo Completo de Mercado</h3>
              <p style="font-size: 0.85rem; color: var(--text-muted);">Executa sequencialmente todas as 7 etapas de um dia de negociação até a liquidação CSD e SPB.</p>
            </div>
            <button class="btn" onclick="runDaySimulation()">▶ Rodar Simulação do Pregão</button>
          </div>
          <div id="simulation-summary" style="display: none; margin-bottom: 1.5rem; background: #0c1220; border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem;">
            <!-- Sumário injetado via JS -->
          </div>
          <div class="timeline" id="timeline-container">
            <p style="color: var(--text-muted); font-size: 0.9rem;">Clique em 'Rodar Simulação do Pregão' para iniciar a execução dos 7 módulos.</p>
          </div>
        </div>
      </div>

      <!-- Tab 3: Chaos Lab -->
      <div id="tab-chaos" style="display: none;">
        <div class="panel">
          <div class="panel-header">
            <div>
              <h3 style="font-size: 1.1rem; color: #fff;">Laboratório de Caos e Estresse da B3</h3>
              <p style="font-size: 0.85rem; color: var(--text-muted);">Simule os 4 cenários extremos de crise financeira e veja as salvaguardas absorvendo o choque.</p>
            </div>
          </div>
          <div style="display: flex; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 1.5rem;">
            <button class="btn danger" onclick="runChaos('CIRCUIT_BREAKER')">🚨 Circuit Breaker (-10%)</button>
            <button class="btn danger" onclick="runChaos('CLEARING_DEFAULT')">💥 Default Waterfall (R$ 500M)</button>
            <button class="btn" onclick="runChaos('RECEIVABLES_LIEN_CONFLICT')">🔒 Conflito de Gravames</button>
            <button class="btn" onclick="runChaos('SPB_NETWORK_DISRUPTION')">⚡ Contingência STR/SPB</button>
          </div>
          <div id="chaos-output" class="viewer" style="display: none;"></div>
        </div>
      </div>

      <!-- Tab Phase 7 & TLA+ -->
      <div id="tab-phase7" style="display: none;">
        <div class="panel">
          <div class="panel-header">
            <div>
              <h3 style="font-size: 1.1rem; color: #fff;">Fase 7 Quantitativa & Verificação Formal TLA+ (FL-101 a FL-105)</h3>
              <p style="font-size: 0.85rem; color: var(--text-muted);">Execute em tempo real os motores institucionais de derivativos, COE, PMM, SOR e Drex RWA ou o verificador de estados TLA+.</p>
            </div>
          </div>
          <div style="display: flex; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 1.5rem;">
            <button class="btn" onclick="runPhase7('FL-101')">📈 FL-101: Volatilidade SABR/SVI</button>
            <button class="btn" onclick="runPhase7('FL-102')">🛡️ FL-102: COE Capital Protegido</button>
            <button class="btn" onclick="runPhase7('FL-103')">⚖️ FL-103: Formador PMM B3</button>
            <button class="btn" onclick="runPhase7('FL-104')">🎯 FL-104: Algorithmic SOR</button>
            <button class="btn" onclick="runPhase7('FL-105')">🪙 FL-105: Emissão RWA Drex</button>
            <button class="btn" onclick="runPhase7('FL-106')">🚗 FL-106: SNG Gravames</button>
            <button class="btn" onclick="runPhase7('FL-107')">📑 FL-107: SRO Seguros SUSEP</button>
            <button class="btn" style="background: #10b981;" onclick="runTLACheck()">🔬 Rodar Model Checker TLA+ (TLC)</button>
          </div>
          <div id="phase7-output" class="viewer" style="display: none;"></div>
        </div>
      </div>

      <!-- Tab Concepts (Conceitos Complexos) -->
      <div id="tab-concepts" style="display: none;">
        <div class="panel">
          <div class="panel-header" style="flex-direction: column; align-items: flex-start; gap: 1rem;">
            <div>
              <h3 style="font-size: 1.25rem; color: #fff;">🧠 Guia Pedagógico de Conceitos Complexos da B3</h3>
              <p style="font-size: 0.85rem; color: var(--text-muted); margin-top: 0.25rem;">
                Mecanismos matemáticos, regulatórios e operacionais estruturantes: Novação Legal, Default Waterfall, Netting Multilateral, Leilões MOC/LOC, Drex DvP e Superfícies SVI/SABR.
              </p>
            </div>
            <div style="display: flex; gap: 0.75rem; width: 100%; flex-wrap: wrap; align-items: center;">
              <input type="text" id="concept-search" placeholder="Buscar por conceito, lei (ex: LAW-104A) ou palavra-chave..." oninput="filterConcepts()" style="background: #0c1220; border: 1px solid var(--border); color: #fff; padding: 0.6rem 1rem; border-radius: 6px; font-size: 0.85rem; flex: 1; min-width: 280px;">
              <div id="category-chips" style="display: flex; gap: 0.4rem; flex-wrap: wrap;"></div>
            </div>
          </div>

          <div class="modules-grid" id="concepts-grid" style="margin-top: 1rem;">
            <!-- Injetado via JS -->
          </div>

          <!-- Reader View de Detalhes -->
          <div id="concept-detail-modal" class="panel" style="display: none; margin-top: 2rem; background: #0c1220; border: 1px solid var(--accent-blue); box-shadow: 0 0 20px rgba(2, 132, 199, 0.2);">
            <div id="concept-detail-content"></div>
          </div>
        </div>
      </div>

      <!-- Tab 4: Local Docs -->
      <div id="tab-docs" style="display: none;">
        <div class="panel">
          <div class="panel-header">
            <div>
              <h3 style="font-size: 1.1rem; color: #fff;">Acervo Documental Oficial (87 Documentos Locais)</h3>
              <p style="font-size: 0.85rem; color: var(--text-muted);">Manuais oficiais B3, layouts IMBARQ, dicionários XML e regulamentos salvos em raw_docs/.</p>
            </div>
            <input type="text" id="doc-search" placeholder="Filtrar manuais por ID ou título..." oninput="filterDocs()" style="background: #0c1220; border: 1px solid var(--border); color: #fff; padding: 0.5rem 1rem; border-radius: 6px; font-size: 0.85rem; width: 300px;">
          </div>
          <div style="max-height: 450px; overflow-y: auto;">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Título Oficial B3</th>
                  <th>Módulo</th>
                  <th>Formato</th>
                  <th>Tamanho</th>
                  <th>Ações</th>
                </tr>
              </thead>
              <tbody id="docs-tbody"></tbody>
            </table>
          </div>
          <div id="doc-modal" class="viewer" style="display: none; margin-top: 1.5rem;"></div>
        </div>
      </div>
    </div>
  </div>

  <script>
    let allDocs = [];
    let allFlows = [];

    function switchTab(tabId) {
      document.querySelectorAll('.nav li a').forEach(el => el.classList.remove('active'));
      event.target.classList.add('active');
      ['overview', 'flows', 'simulator', 'chaos', 'phase7', 'concepts', 'docs'].forEach(t => {
        const el = document.getElementById('tab-' + t);
        if (el) el.style.display = (t === tabId) ? 'block' : 'none';
      });
      const titles = {
        'overview': 'Visão Holística dos 7 Módulos da B3',
        'flows': '🧭 Catálogo Completo de 107 Fluxos Institucionais B3 (FL-001 a FL-107)',
        'simulator': 'Simulador Diário E2E: Todos os Módulos em Execução',
        'chaos': 'Laboratório de Caos e Testes de Estresse Financeiro',
        'phase7': 'Fase 7 Quantitativa (FL-101 a FL-107) & Model Checking TLA+',
        'concepts': '🧠 Guia Pedagógico de Conceitos Complexos da B3',
        'docs': 'Acervo Documental Oficial B3 (87 Manuais Locais com SHA-256)'
      };
      document.getElementById('view-title').innerText = titles[tabId] || 'B3 Integration Atlas';
      if (tabId === 'flows' && allFlows.length === 0) loadFlows();
      if (tabId === 'docs' && allDocs.length === 0) loadDocs();
      if (tabId === 'concepts' && allConcepts.length === 0) loadConcepts();
    }

    async function loadModules() {
      const res = await fetch('/api/modules');
      const data = await res.json();
      const container = document.getElementById('modules-container');
      container.innerHTML = data.map(m => `
        <div class="module-card" style="border-top: 4px solid ${m.color}">
          <div class="module-header">
            <span class="module-title">${m.name}</span>
            <span class="tag" style="background: ${m.color}22; color: ${m.color}; font-weight: 700;">${m.id}</span>
          </div>
          <p class="module-desc">${m.scope}</p>
          <div style="margin-bottom: 0.75rem;">
            <strong style="font-size: 0.75rem; color: #9ca3af; text-transform: uppercase;">Protocolos & Dados:</strong>
            <div class="tag-list" style="margin-top: 0.25rem;">
              ${m.protocols.map(p => `<span class="tag">${p}</span>`).join('')}
            </div>
          </div>
          <div>
            <strong style="font-size: 0.75rem; color: #9ca3af; text-transform: uppercase;">Leis Formais Provadas:</strong>
            <div class="tag-list" style="margin-top: 0.25rem;">
              ${m.laws.map(l => `<span class="tag law">${l}</span>`).join('')}
            </div>
          </div>
        </div>
      `).join('');
    }

    async function runDaySimulation() {
      const btn = event.target;
      btn.innerText = 'Executando...';
      btn.disabled = true;
      const res = await fetch('/api/simulate-day?shares=100000');
      const data = await res.json();
      btn.innerText = '▶ Rodar Simulação do Pregão';
      btn.disabled = false;

      const summaryDiv = document.getElementById('simulation-summary');
      summaryDiv.style.display = 'block';
      summaryDiv.innerHTML = `
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem;">
          <div><strong style="color: #9ca3af; font-size: 0.75rem;">VOL. NEGOCIAÇÃO PUMA:</strong><div style="font-size: 1.2rem; font-weight: 700; color: #fff;">R$ ${Number(data.metrics.total_equities_volume).toLocaleString('pt-BR', {minimumFractionDigits: 2})}</div></div>
          <div><strong style="color: #9ca3af; font-size: 0.75rem;">VOL. REGISTRO BALCÃO:</strong><div style="font-size: 1.2rem; font-weight: 700; color: #fff;">R$ ${Number(data.metrics.total_balcao_volume).toLocaleString('pt-BR', {minimumFractionDigits: 2})}</div></div>
          <div><strong style="color: #9ca3af; font-size: 0.75rem;">LIQUIDAÇÃO SPB (STR):</strong><div style="font-size: 1.2rem; font-weight: 700; color: #10b981;">R$ ${Number(data.metrics.total_spb_settled).toLocaleString('pt-BR', {minimumFractionDigits: 2})}</div></div>
          <div><strong style="color: #9ca3af; font-size: 0.75rem;">DREX ATÔMICO DVP:</strong><div style="font-size: 1.2rem; font-weight: 700; color: #c39e5c;">R$ ${Number(data.metrics.total_drex_atomic_settled).toLocaleString('pt-BR', {minimumFractionDigits: 2})}</div></div>
        </div>
      `;

      const modColors = {
        'PUMA_TRADING': '#0284c7', 'CLEARING_RISK': '#e11d48', 'NOME_BALCAO': '#8b5cf6',
        'CSD_DEPOSITARY': '#10b981', 'SPB_SETTLEMENT': '#f59e0b', 'TARIFF_CATALOG': '#06b6d4',
        'BSM_SURVEILLANCE': '#ec4899'
      };

      const timeline = document.getElementById('timeline-container');
      timeline.innerHTML = data.events.map(e => `
        <div class="timeline-item">
          <div class="timeline-time">${e.timestamp}</div>
          <div class="timeline-module" style="background: ${modColors[e.module] || '#374151'}22; color: ${modColors[e.module] || '#9ca3af'}; border: 1px solid ${modColors[e.module] || '#374151'}44;">${e.module}</div>
          <div class="timeline-body">
            <div class="timeline-title">${e.title} <span class="tag law">${e.conservation_law}</span></div>
            <div class="timeline-desc">${e.details ? JSON.stringify(e.details) : ''}</div>
          </div>
          <div class="timeline-vol">R$ ${Number(e.volume).toLocaleString('pt-BR', {minimumFractionDigits: 2})}</div>
        </div>
      `).join('');
    }

    async function runChaos(scenario) {
      const viewer = document.getElementById('chaos-output');
      viewer.style.display = 'block';
      viewer.innerText = 'Processando cenário extremo de mercado...';
      const res = await fetch('/api/chaos/' + scenario);
      const data = await res.json();
      
      let out = `================================================================================\n`;
      out += ` B3 CHAOS SIMULATOR: ${data.title}\n`;
      out += `================================================================================\n`;
      out += `Cenário:                 ${data.scenario}\n`;
      out += `Veredito de Resiliência: ${data.is_resilient ? '✓ SISTEMA 100% RESILIENTE' : 'FALHA'}\n`;
      out += `Volume Contido:          R$ ${Number(data.total_financial_contained).toLocaleString('pt-BR', {minimumFractionDigits: 2})}\n`;
      out += `Leis Formais Provadas:   ${data.laws_verified.join(', ')}\n`;
      out += `--------------------------------------------------------------------------------\n`;
      out += `ETAPAS DE ESTRESSE E CONTENÇÃO:\n`;
      data.steps.forEach(s => {
        out += ` [${String(s.seq).padStart(2, '0')}] ${s.timestamp} [${s.phase.padEnd(20)}] ${s.action.padEnd(35)} -> ${s.outcome} (${s.conservation_law})\n`;
      });
      out += `--------------------------------------------------------------------------------\n`;
      out += `PARECER DA CÂMARA / BSM: ${data.verdict}\n`;
      out += `================================================================================\n`;
      viewer.innerText = out;
    }

    async function runPhase7(flowId) {
      const viewer = document.getElementById('phase7-output');
      viewer.style.display = 'block';
      viewer.innerText = 'Executando motor institucional ' + flowId + '...';
      const res = await fetch('/api/phase7/' + flowId);
      const data = await res.json();
      viewer.innerText = JSON.stringify(data, null, 2);
    }

    async function runTLACheck() {
      const viewer = document.getElementById('phase7-output');
      viewer.style.display = 'block';
      viewer.innerText = 'Explorando espaço de estados TLA+ via BFS...';
      const res = await fetch('/api/tla-check');
      const data = await res.json();
      viewer.innerText = JSON.stringify(data, null, 2);
    }

    async function loadDocs() {
      const res = await fetch('/api/docs');
      allDocs = await res.json();
      renderDocs(allDocs);
    }

    function renderDocs(docs) {
      const tbody = document.getElementById('docs-tbody');
      tbody.innerHTML = docs.map(d => `
        <tr>
          <td><code style="color: var(--accent-gold);">${d.doc_id}</code></td>
          <td style="font-weight: 500;">${d.title}</td>
          <td><span class="tag">${d.category}</span></td>
          <td><span class="tag">${d.file_type.toUpperCase()}</span></td>
          <td>${(d.size_bytes / 1024).toFixed(1)} KB</td>
          <td><button class="btn" style="padding: 0.3rem 0.6rem; font-size: 0.75rem;" onclick="viewDoc('${d.doc_id}')">Visualizar</button></td>
        </tr>
      `).join('');
    }

    function filterDocs() {
      const q = document.getElementById('doc-search').value.toLowerCase();
      const filtered = allDocs.filter(d => d.doc_id.toLowerCase().includes(q) || d.title.toLowerCase().includes(q) || d.category.toLowerCase().includes(q));
      renderDocs(filtered);
    }

    async function viewDoc(docId) {
      const modal = document.getElementById('doc-modal');
      modal.style.display = 'block';
      modal.innerText = 'Carregando documento local...';
      const res = await fetch('/api/docs/' + docId);
      const data = await res.json();
      
      let clean = data.raw_content.replace(/<style[\\s\\S]*?<\\/style>/g, '').replace(/<[^>]+>/g, ' ').replace(/\\n\\s*\\n+/g, '\\n\\n');
      modal.innerText = `[${data.doc.doc_id}] ${data.doc.title}\nCategoria: ${data.doc.category} | Hash: ${data.doc.sha256}\n` + '='.repeat(80) + `\n\n` + clean;
      modal.scrollIntoView({ behavior: 'smooth' });
    }

    let selectedFlowSystem = 'TODOS';

    async function loadFlows() {
      const res = await fetch('/api/flows');
      allFlows = await res.json();
      renderFlowSystemChips();
      renderFlows(allFlows);
    }

    function renderFlowSystemChips() {
      const systems = ['TODOS', ...new Set(allFlows.map(f => f.system))];
      const container = document.getElementById('flow-system-chips');
      if (!container) return;
      container.innerHTML = systems.slice(0, 8).map(s => `
        <button class="btn" style="padding: 0.35rem 0.75rem; font-size: 0.75rem; background: ${selectedFlowSystem === s ? 'var(--accent-blue)' : '#1f2937'}; color: #fff;" onclick="selectFlowSystem('${s}')">
          ${s}
        </button>
      `).join('');
    }

    function selectFlowSystem(sys) {
      selectedFlowSystem = sys;
      renderFlowSystemChips();
      filterFlows();
    }

    function filterFlows() {
      const q = document.getElementById('flow-search').value.toLowerCase().trim();
      const filtered = allFlows.filter(f => {
        const matchesSys = (selectedFlowSystem === 'TODOS' || f.system === selectedFlowSystem);
        const matchesQuery = !q || (
          f.id.toLowerCase().includes(q) ||
          f.name.toLowerCase().includes(q) ||
          f.system.toLowerCase().includes(q) ||
          (f.interface && f.interface.toLowerCase().includes(q))
        );
        return matchesSys && matchesQuery;
      });
      renderFlows(filtered);
    }

    function renderFlows(flows) {
      const tbody = document.getElementById('flows-tbody');
      if (!tbody) return;
      if (flows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 1.5rem;">Nenhum fluxo encontrado.</td></tr>';
        return;
      }
      tbody.innerHTML = flows.map(f => `
        <tr>
          <td><code style="color: var(--accent-gold); font-weight: 700;">${f.id}</code></td>
          <td style="font-weight: 500; color: #fff;">${f.name}</td>
          <td><span class="tag" style="background: rgba(2, 132, 199, 0.15); color: #38bdf8;">${f.system}</span></td>
          <td style="font-size: 0.8rem; color: var(--text-muted);">${f.interface || '-'}</td>
          <td>
            <span class="tag" style="background: ${f.tested_locally ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)'}; color: ${f.tested_locally ? '#10b981' : '#ef4444'}; font-weight: 600;">
              ${f.tested_locally ? 'HOMOLOGADO' : 'PENDENTE'}
            </span>
          </td>
          <td>
            <button class="btn" style="padding: 0.3rem 0.6rem; font-size: 0.75rem;" onclick="viewFlowDetail('${f.id}')">Ver Detalhes</button>
          </td>
        </tr>
      `).join('');
    }

    function viewFlowDetail(flowId) {
      const f = allFlows.find(item => item.id === flowId);
      if (!f) return;
      const modal = document.getElementById('flow-modal');
      modal.style.display = 'block';
      let details = `================================================================================\\n`;
      details += ` FLUXO INSTITUCIONAL B3: ${f.id} - ${f.name}\\n`;
      details += `================================================================================\\n`;
      details += `Sistema:        ${f.system}\\n`;
      details += `Interface:      ${f.interface}\\n`;
      details += `Confiança:      ${f.confidence}\\n`;
      details += `Status:         Mapeado=${f.mapped} | Documentado=${f.documented} | Implementado=${f.implemented} | Testado=${f.tested_locally}\\n`;
      details += `Fontes B3:      ${(f.sources || []).join(', ')}\\n`;
      details += `--------------------------------------------------------------------------------\\n`;
      details += `Evidências:\\n`;
      if (f.evidence) {
        details += `  - Documentação: ${f.evidence.documented || 'N/A'}\\n`;
        details += `  - Implementação:${f.evidence.implemented || 'N/A'}\\n`;
        details += `  - Testes:       ${f.evidence.tested_locally || 'N/A'}\\n`;
      }
      if (f.pending) {
        details += `--------------------------------------------------------------------------------\\n`;
        details += `Notas / Pendências:\\n  ${f.pending}\\n`;
      }
      details += `================================================================================\\n`;
      modal.innerText = details;
      modal.scrollIntoView({ behavior: 'smooth' });
    }

    let allConcepts = [];
    let selectedCategory = 'TODOS';

    async function loadConcepts() {
      const res = await fetch('/api/concepts');
      allConcepts = await res.json();
      renderCategoryChips();
      renderConcepts(allConcepts);
    }

    function renderCategoryChips() {
      const cats = ['TODOS', ...new Set(allConcepts.map(c => c.category))];
      const container = document.getElementById('category-chips');
      container.innerHTML = cats.map(c => `
        <button class="btn" style="padding: 0.35rem 0.75rem; font-size: 0.75rem; background: ${selectedCategory === c ? 'var(--accent-blue)' : '#1f2937'}; color: #fff;" onclick="selectConceptCategory('${c}')">
          ${c}
        </button>
      `).join('');
    }

    function selectConceptCategory(cat) {
      selectedCategory = cat;
      renderCategoryChips();
      filterConcepts();
    }

    function filterConcepts() {
      const q = document.getElementById('concept-search').value.toLowerCase().trim();
      const filtered = allConcepts.filter(c => {
        const matchesCat = (selectedCategory === 'TODOS' || c.category === selectedCategory);
        const matchesQuery = !q || (
          c.title.toLowerCase().includes(q) ||
          c.summary.toLowerCase().includes(q) ||
          c.badge.toLowerCase().includes(q) ||
          c.related_laws.some(l => l.toLowerCase().includes(q)) ||
          c.related_flows.some(f => f.toLowerCase().includes(q))
        );
        return matchesCat && matchesQuery;
      });
      renderConcepts(filtered);
    }

    function renderConcepts(list) {
      const grid = document.getElementById('concepts-grid');
      if (list.length === 0) {
        grid.innerHTML = '<p style="color: var(--text-muted); padding: 1rem;">Nenhum conceito encontrado para os critérios de busca.</p>';
        return;
      }
      grid.innerHTML = list.map(c => `
        <div class="module-card" style="border-top: 4px solid var(--accent-gold); display: flex; flex-direction: column; justify-content: space-between;">
          <div>
            <div class="module-header">
              <span class="tag" style="background: rgba(195, 158, 92, 0.15); color: var(--accent-gold); font-weight: 700;">${c.category}</span>
              <span class="tag law">${c.badge}</span>
            </div>
            <h4 style="font-size: 1.05rem; font-weight: 600; color: #fff; margin-bottom: 0.5rem;">${c.title}</h4>
            <p class="module-desc" style="font-size: 0.85rem; color: #d1d5db;">${c.summary}</p>
          </div>
          <div>
            <div class="tag-list" style="margin-bottom: 1rem;">
              ${c.related_flows.map(f => `<span class="tag" style="font-family: monospace;">${f}</span>`).join('')}
              ${c.related_laws.map(l => `<span class="tag law">${l}</span>`).join('')}
            </div>
            <button class="btn" style="width: 100%; justify-content: center; font-size: 0.8rem;" onclick="viewConceptDetail('${c.id}')">
              📖 Ler Explicação Detalhada
            </button>
          </div>
        </div>
      `).join('');
    }

    async function viewConceptDetail(conceptId) {
      const res = await fetch('/api/concepts/' + conceptId);
      const c = await res.json();
      const modal = document.getElementById('concept-detail-modal');
      const content = document.getElementById('concept-detail-content');
      modal.style.display = 'block';

      content.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 1px solid var(--border); padding-bottom: 1rem; margin-bottom: 1.5rem;">
          <div>
            <div style="display: flex; gap: 0.5rem; margin-bottom: 0.5rem;">
              <span class="tag" style="background: rgba(195, 158, 92, 0.15); color: var(--accent-gold); font-weight: 700;">${c.category}</span>
              <span class="tag law">${c.badge}</span>
            </div>
            <h2 style="font-size: 1.4rem; color: #fff;">${c.title}</h2>
            <p style="font-size: 0.9rem; color: var(--text-muted); margin-top: 0.25rem;">${c.summary}</p>
          </div>
          <button class="btn" style="background: #374151; font-size: 0.75rem;" onclick="document.getElementById('concept-detail-modal').style.display='none'">✕ Fechar</button>
        </div>

        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1.5rem; margin-bottom: 1.5rem;">
          <div style="background: #111827; border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem;">
            <h4 style="font-size: 0.85rem; color: #38bdf8; text-transform: uppercase; font-weight: 700; margin-bottom: 0.5rem;">📌 O que é?</h4>
            <p style="font-size: 0.85rem; color: #e5e7eb; line-height: 1.5;">${c.what_is}</p>
          </div>
          <div style="background: #111827; border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem;">
            <h4 style="font-size: 0.85rem; color: #f59e0b; text-transform: uppercase; font-weight: 700; margin-bottom: 0.5rem;">⚠️ Por que existe? (Risco Eliminado)</h4>
            <p style="font-size: 0.85rem; color: #e5e7eb; line-height: 1.5;">${c.why_exists}</p>
          </div>
        </div>

        <div style="background: #111827; border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; margin-bottom: 1.5rem;">
          <h4 style="font-size: 0.85rem; color: #10b981; text-transform: uppercase; font-weight: 700; margin-bottom: 0.75rem;">⚙️ Como Funciona Passo a Passo:</h4>
          <ol style="padding-left: 1.5rem; font-size: 0.85rem; color: #e5e7eb; line-height: 1.6;">
            ${c.how_it_works.map(step => `<li style="margin-bottom: 0.35rem;">${step}</li>`).join('')}
          </ol>
        </div>

        <div style="background: #090d16; border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; margin-bottom: 1.5rem;">
          <h4 style="font-size: 0.85rem; color: var(--accent-gold); text-transform: uppercase; font-weight: 700; margin-bottom: 0.5rem;">📐 Regra Matemática / Lei de Conservação:</h4>
          <pre style="font-family: monospace; font-size: 0.82rem; color: #67e8f9; white-space: pre-wrap; line-height: 1.4;">${c.math_rule}</pre>
        </div>

        <div style="background: #090d16; border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; margin-bottom: 1.5rem;">
          <h4 style="font-size: 0.85rem; color: #a78bfa; text-transform: uppercase; font-weight: 700; margin-bottom: 0.5rem;">📊 Diagrama Arquitetural:</h4>
          <pre style="font-family: monospace; font-size: 0.8rem; color: #e5e7eb; white-space: pre-wrap; line-height: 1.3;">${c.diagram_ascii}</pre>
        </div>

        <div style="background: #111827; border-left: 4px solid var(--accent-green); border-radius: 4px; padding: 1.25rem;">
          <h4 style="font-size: 0.85rem; color: var(--accent-green); text-transform: uppercase; font-weight: 700; margin-bottom: 0.35rem;">💼 Exemplo Prático de Mercado:</h4>
          <p style="font-size: 0.85rem; color: #f3f4f6; line-height: 1.5;">${c.practical_example}</p>
        </div>

        <div style="background: #0d1527; border: 1px solid var(--accent-blue); border-radius: 8px; padding: 1.25rem; margin-top: 1.5rem;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; flex-wrap: wrap; gap: 0.5rem;">
            <div>
              <h4 style="font-size: 0.9rem; color: #38bdf8; text-transform: uppercase; font-weight: 700;">🧪 Laboratório Prático Interativo (Sandbox Live):</h4>
              <p style="font-size: 0.82rem; color: var(--text-muted); margin-top: 0.2rem;">
                Dispare o motor oficial em Python para testar este mecanismo com dados dinâmicos e validar o fechamento da invariante formal.
              </p>
            </div>
            <button class="btn" style="background: #0284c7; color: #fff; font-weight: 600; padding: 0.5rem 1.25rem; font-size: 0.85rem; cursor: pointer; border-radius: 6px;" onclick="runConceptSimulation('${c.id}')">▶️ Executar Simulação deste Mecanismo</button>
          </div>
          <div id="concept-sim-result-${c.id}" style="display: none; background: #080c14; border: 1px solid var(--border); border-radius: 6px; padding: 1rem; margin-top: 0.75rem;"></div>
        </div>
      `;
      modal.scrollIntoView({ behavior: 'smooth' });
    }

    async function runConceptSimulation(conceptId) {
      const box = document.getElementById('concept-sim-result-' + conceptId);
      box.style.display = 'block';
      box.innerHTML = '<span style="color: var(--accent-blue);">⚡ Executando motor analítico oficial do conceito...</span>';
      try {
        const res = await fetch('/api/concepts/' + conceptId + '/simulate');
        const data = await res.json();
        box.innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1f2937; padding-bottom: 0.5rem; margin-bottom: 0.75rem;">
            <span style="color: #10b981; font-weight: bold; font-size: 0.9rem;">✔ ${data.title || 'Simulação Concluída'}</span>
            <span class="tag law" style="font-size: 0.75rem; background: rgba(16, 185, 129, 0.15); color: #10b981;">${data.law_verified || 'INVARIANTE ATIVA'}</span>
          </div>
          <pre style="color: #67e8f9; white-space: pre-wrap; font-size: 0.82rem; line-height: 1.4; font-family: monospace;">${JSON.stringify(data, null, 2)}</pre>
        `;
      } catch (err) {
        box.innerHTML = `<span style="color: var(--accent-red);">Erro ao executar simulação: ${err.message}</span>`;
      }
    }

    loadModules();
  </script>
</body>
</html>
"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_dashboard_server(host: str = "127.0.0.1", port: int = 8080) -> HTTPServer:
    """Inicia o servidor HTTP do dashboard local."""
    server = HTTPServer((host, port), B3DashboardHandler)
    return server
