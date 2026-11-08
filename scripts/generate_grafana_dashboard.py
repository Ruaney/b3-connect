#!/usr/bin/env python3
"""
Gerador e Validador do Dashboard Oficial Grafana B3 Enterprise Overview.
Produz o dashboard de produção para o ecossistema de observabilidade da B3
em `observability/grafana_b3_enterprise_overview.json`.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "observability" / "grafana_b3_enterprise_overview.json"


def build_dashboard() -> dict:
    dashboard = {
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                    "enable": True,
                    "hide": True,
                    "name": "Annotations & Alerts",
                    "type": "dashboard",
                },
                {
                    "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
                    "enable": True,
                    "hide": False,
                    "iconColor": "#F2495C",
                    "name": "B3 Circuit Breakers & Default Triggers",
                    "expr": "changes(b3_puma_circuit_breaker_active[1m]) > 0 or changes(b3_clearing_default_events_total[1m]) > 0",
                    "titleFormat": "Alerta B3: {{instance}}",
                    "textFormat": "Evento crítico de mercado ou inadimplência detectado",
                },
            ]
        },
        "description": "Painel de controle unificado de produção da B3 — Trading PUMA, Custódia CSD IMBARQ, Cascata de Salvaguardas CCP, SNG Detrans, SRO SUSEP e Barramento Central de Liquidação (LAW-106A/B/C).",
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [
            {
                "asDropdown": False,
                "icon": "doc",
                "includeVars": True,
                "keepTime": True,
                "tags": ["b3", "documentation"],
                "targetBlank": True,
                "title": "Compêndio Técnico B3",
                "tooltip": "Acessar Compêndio de Invariantes e Leis Formais",
                "type": "link",
                "url": "https://github.com/b3-integration-atlas",
            }
        ],
        "liveNow": False,
        "panels": [],
        "refresh": "5s",
        "schemaVersion": 38,
        "style": "dark",
        "tags": [
            "b3",
            "enterprise",
            "puma",
            "imbarq",
            "csd",
            "waterfall",
            "safeguards",
            "sng",
            "detran",
            "sro",
            "susep",
            "central-bus",
            "law-106",
            "observability",
            "production",
        ],
        "templating": {
            "list": [
                {
                    "current": {"selected": True, "text": "Prometheus", "value": "Prometheus"},
                    "hide": 0,
                    "includeAll": False,
                    "label": "Data Source",
                    "multi": False,
                    "name": "DS_PROMETHEUS",
                    "options": [],
                    "query": "prometheus",
                    "refresh": 1,
                    "type": "datasource",
                },
                {
                    "current": {"selected": True, "text": "PRD", "value": "prd"},
                    "description": "Ambiente de execução",
                    "hide": 0,
                    "includeAll": False,
                    "label": "Ambiente",
                    "multi": False,
                    "name": "env",
                    "options": [
                        {"selected": True, "text": "Produção (PRD)", "value": "prd"},
                        {"selected": False, "text": "Homologação (STG)", "value": "stg"},
                        {"selected": False, "text": "Certificação B3 (CERT)", "value": "cert"},
                    ],
                    "query": "prd,stg,cert",
                    "type": "custom",
                },
                {
                    "allValue": ".*",
                    "current": {"selected": True, "text": "Todos", "value": "$__all"},
                    "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
                    "definition": "label_values(b3_central_bus_subsystem_volume_brl, subsystem)",
                    "description": "Filtrar por subsistema de liquidação",
                    "hide": 0,
                    "includeAll": True,
                    "label": "Subsistema",
                    "multi": True,
                    "name": "subsystem",
                    "query": {"query": "label_values(b3_central_bus_subsystem_volume_brl, subsystem)", "refId": "PrometheusVariableQueryRef"},
                    "refresh": 2,
                    "sort": 1,
                    "type": "query",
                },
                {
                    "allValue": ".*",
                    "current": {"selected": True, "text": "Todos", "value": "$__all"},
                    "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
                    "definition": "label_values(b3_sng_gravames_inclusions_total, uf)",
                    "description": "Filtrar por Detran Estadual",
                    "hide": 0,
                    "includeAll": True,
                    "label": "Detran UF",
                    "multi": True,
                    "name": "uf",
                    "query": {"query": "label_values(b3_sng_gravames_inclusions_total, uf)", "refId": "PrometheusVariableQueryRef"},
                    "refresh": 2,
                    "sort": 1,
                    "type": "query",
                },
            ]
        },
        "time": {"from": "now-1h", "to": "now"},
        "timepicker": {
            "refresh_intervals": ["1s", "5s", "10s", "30s", "1m", "5m"],
            "time_options": ["5m", "15m", "1h", "6h", "12h", "24h", "2d", "7d", "30d"],
        },
        "timezone": "America/Sao_Paulo",
        "title": "B3 Enterprise Architecture & Invariants Overview — Mission Control",
        "uid": "b3-enterprise-overview",
        "version": 1,
    }

    panels = []
    panel_id = 1

    def next_id():
        nonlocal panel_id
        res = panel_id
        panel_id += 1
        return res

    # =========================================================================
    # ROW 0: VISÃO GERAL & EXECUTIVE HEALTH SCOREBOARD
    # =========================================================================
    panels.append({
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0},
        "id": next_id(),
        "panels": [],
        "title": "⚡ B3 EXECUTIVE HEALTH & CRITICAL INVARIANTS STATUS",
        "type": "row",
    })

    # KPI 1: PUMA Throughput
    panels.append({
        "id": next_id(),
        "title": "PUMA Trading Throughput",
        "type": "stat",
        "gridPos": {"h": 4, "w": 4, "x": 0, "y": 1},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "sum(rate(b3_puma_messages_total{env=~\"$env\"}[1m]))",
            "legendFormat": "Throughput Global",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "mps",
                "color": {"mode": "palette-classic"},
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "orange", "value": None},
                        {"color": "green", "value": 1000},
                        {"color": "purple", "value": 100000},
                    ],
                },
                "mappings": [],
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "auto",
            "textMode": "auto",
            "colorMode": "background",
            "graphMode": "area",
            "justifyMode": "auto",
        },
    })

    # KPI 2: PUMA Latency p99
    panels.append({
        "id": next_id(),
        "title": "PUMA Latência p99 (Matching)",
        "type": "stat",
        "gridPos": {"h": 4, "w": 4, "x": 4, "y": 1},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "histogram_quantile(0.99, sum(rate(b3_puma_matching_latency_microseconds_bucket{env=~\"$env\"}[1m])) by (le))",
            "legendFormat": "p99 Latency",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "µs",
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "yellow", "value": 150},
                        {"color": "red", "value": 500},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "auto",
            "textMode": "auto",
            "colorMode": "background",
            "graphMode": "area",
            "justifyMode": "auto",
        },
    })

    # KPI 3: IMBARQ Custody Breaks
    panels.append({
        "id": next_id(),
        "title": "IMBARQ Custódia Breaks",
        "description": "Quebras de conciliação de custódia na Central Depositária. O valor estrito deve ser ZERO (Invariante CSD).",
        "type": "stat",
        "gridPos": {"h": 4, "w": 4, "x": 8, "y": 1},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "sum(b3_imbarq_custody_reconciliation_breaks_total{env=~\"$env\"})",
            "legendFormat": "Breaks Ativos",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "short",
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "red", "value": 1},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "auto",
            "textMode": "value_and_name",
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "auto",
        },
    })

    # KPI 4: Default Waterfall Active Tier
    panels.append({
        "id": next_id(),
        "title": "Cascata de Salvaguardas (Tier Ativo)",
        "description": "Nível de acionamento do Default Waterfall da Câmara B3 (L1=Garantias a L5=Extraordinário).",
        "type": "stat",
        "gridPos": {"h": 4, "w": 4, "x": 12, "y": 1},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "max(b3_clearing_default_waterfall_active_tier_level{env=~\"$env\"})",
            "legendFormat": "Nível Tier",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "blue", "value": 1},
                        {"color": "yellow", "value": 2},
                        {"color": "orange", "value": 3},
                        {"color": "red", "value": 4},
                        {"color": "dark-red", "value": 5},
                    ],
                },
                "mappings": [
                    {
                        "type": "value",
                        "options": {
                            "0": {"text": "NORMAL (0)", "color": "green"},
                            "1": {"text": "TIER 1 (Margens)", "color": "blue"},
                            "2": {"text": "TIER 2 (Fundo Próprio)", "color": "yellow"},
                            "3": {"text": "TIER 3 (B3 SITG)", "color": "orange"},
                            "4": {"text": "TIER 4 (Fundo Mútuo)", "color": "red"},
                            "5": {"text": "TIER 5 (Chamada Extra)", "color": "dark-red"},
                        },
                    }
                ],
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "auto",
            "textMode": "auto",
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "auto",
        },
    })

    # KPI 5: SNG Ativos & Baixas
    panels.append({
        "id": next_id(),
        "title": "SNG Gravames Ativos",
        "description": "Gravames veiculares vigentes no Sistema Nacional de Gravames junto aos Detrans.",
        "type": "stat",
        "gridPos": {"h": 4, "w": 4, "x": 16, "y": 1},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "sum(b3_sng_active_liens_gauge{env=~\"$env\", uf=~\"$uf\"})",
            "legendFormat": "Gravames Vigentes",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "short",
                "color": {"mode": "palette-classic"},
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "blue", "value": None}],
                },
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "auto",
            "textMode": "auto",
            "colorMode": "background",
            "graphMode": "area",
            "justifyMode": "auto",
        },
    })

    # KPI 6: Barramento Central LAW-106 Status
    panels.append({
        "id": next_id(),
        "title": "Barramento Central (LAW-106)",
        "description": "Conformidade estrita das leis LAW-106A (Equilíbrio Contábil), LAW-106B (Não-propagação) e LAW-106C (Atomicidade).",
        "type": "stat",
        "gridPos": {"h": 4, "w": 4, "x": 20, "y": 1},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "b3_central_bus_invariants_status{env=~\"$env\", law=\"LAW-106_ALL\"}",
            "legendFormat": "LAW-106 Invariants",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "red", "value": None},
                        {"color": "green", "value": 1},
                    ],
                },
                "mappings": [
                    {
                        "type": "value",
                        "options": {
                            "0": {"text": "VIOLAÇÃO", "color": "red"},
                            "1": {"text": "100% INTACTO", "color": "green"},
                        },
                    }
                ],
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "auto",
            "textMode": "auto",
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "auto",
        },
    })

    # =========================================================================
    # ROW 1: PUMA TRADING SYSTEM (THROUGHPUT & LATÊNCIA EM MICROSSEGUNDOS)
    # =========================================================================
    panels.append({
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": 5},
        "id": next_id(),
        "panels": [],
        "title": "1. PUMA TRADING SYSTEM — MOTOR DE NEGOCIAÇÃO, THROUGHPUT & LATÊNCIA ULTRA-LOW (µs)",
        "type": "row",
    })

    # Panel: PUMA Throughput por Venue (PUMA_LIT, PUMA_DARK_MIDPOINT, PUMA_RLP, PUMA_AUCTION)
    panels.append({
        "id": next_id(),
        "title": "PUMA Order & Trade Throughput por Venue (msg/s)",
        "description": "Taxa instantânea de mensagens processadas pelo PUMA Trading System divididas por livro (PUMA_LIT, PUMA_DARK_MIDPOINT, PUMA_RLP, PUMA_AUCTION).",
        "type": "timeseries",
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 6},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [
            {
                "expr": "sum(rate(b3_puma_messages_total{env=~\"$env\"}[1m])) by (venue)",
                "legendFormat": "{{venue}} Throughput",
                "refId": "A",
            },
            {
                "expr": "sum(rate(b3_puma_orders_matched_total{env=~\"$env\"}[1m]))",
                "legendFormat": "Matches Executados (Total)",
                "refId": "B",
            },
        ],
        "fieldConfig": {
            "defaults": {
                "unit": "mps",
                "custom": {
                    "drawStyle": "line",
                    "lineInterpolation": "smooth",
                    "lineWidth": 2,
                    "fillOpacity": 15,
                    "gradientMode": "opacity",
                    "stacking": {"mode": "normal", "group": "A"},
                },
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "Matches Executados (Total)"},
                    "properties": [
                        {"id": "custom.stacking", "value": {"mode": "none"}},
                        {"id": "custom.lineWidth", "value": 3},
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": "yellow"}},
                    ],
                }
            ],
        },
        "options": {
            "tooltip": {"mode": "multi", "sort": "desc"},
            "legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max", "lastNotNull"]},
        },
    })

    # Panel: PUMA Latency Quantiles (p50, p95, p99, p99.9) in microseconds
    panels.append({
        "id": next_id(),
        "title": "PUMA Matching Engine Latency — p50, p95, p99 (µs)",
        "description": "Latência determinística de execução de ordens no matching engine PUMA em microssegundos.",
        "type": "timeseries",
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": 6},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [
            {
                "expr": "histogram_quantile(0.50, sum(rate(b3_puma_matching_latency_microseconds_bucket{env=~\"$env\"}[1m])) by (le))",
                "legendFormat": "p50 Median Latency",
                "refId": "A",
            },
            {
                "expr": "histogram_quantile(0.95, sum(rate(b3_puma_matching_latency_microseconds_bucket{env=~\"$env\"}[1m])) by (le))",
                "legendFormat": "p95 Latency",
                "refId": "B",
            },
            {
                "expr": "histogram_quantile(0.99, sum(rate(b3_puma_matching_latency_microseconds_bucket{env=~\"$env\"}[1m])) by (le))",
                "legendFormat": "p99 Tail Latency",
                "refId": "C",
            },
            {
                "expr": "histogram_quantile(0.999, sum(rate(b3_puma_matching_latency_microseconds_bucket{env=~\"$env\"}[1m])) by (le))",
                "legendFormat": "p99.9 Extreme Tail Latency",
                "refId": "D",
            },
        ],
        "fieldConfig": {
            "defaults": {
                "unit": "µs",
                "custom": {
                    "drawStyle": "line",
                    "lineInterpolation": "smooth",
                    "lineWidth": 2,
                    "fillOpacity": 5,
                    "thresholdsStyle": {"mode": "line"},
                },
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "yellow", "value": 150},
                        {"color": "red", "value": 400},
                    ],
                },
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "p50 Median Latency"},
                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "super-light-green"}}],
                },
                {
                    "matcher": {"id": "byName", "options": "p95 Latency"},
                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "yellow"}}],
                },
                {
                    "matcher": {"id": "byName", "options": "p99 Tail Latency"},
                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}],
                },
                {
                    "matcher": {"id": "byName", "options": "p99.9 Extreme Tail Latency"},
                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}],
                },
            ],
        },
        "options": {
            "tooltip": {"mode": "multi", "sort": "desc"},
            "legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max", "lastNotNull"]},
        },
    })

    # Sub-panels PUMA: Túneis de Negociação (LAW-110) & Rejeições
    panels.append({
        "id": next_id(),
        "title": "PUMA Rejeições de Preço por Violação de Túnel (LAW-110)",
        "description": "Ordens rejeitadas imediatamente por estouro dos túneis de oscilação operacional e de rejeição.",
        "type": "timeseries",
        "gridPos": {"h": 6, "w": 12, "x": 0, "y": 14},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "sum(rate(b3_puma_price_tunnel_violations_total{env=~\"$env\"}[1m])) by (tunnel_type)",
            "legendFormat": "{{tunnel_type}} Rejeições/s",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "cps",
                "custom": {"drawStyle": "bars", "fillOpacity": 60},
                "color": {"mode": "palette-classic"},
            },
            "overrides": [],
        },
        "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["sum", "max"]}},
    })

    panels.append({
        "id": next_id(),
        "title": "PUMA Distribuição de Latência (Buckets µs)",
        "description": "Histograma de latência de matching em microssegundos categorizado por faixa operacional.",
        "type": "bargauge",
        "gridPos": {"h": 6, "w": 12, "x": 12, "y": 14},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "sum(rate(b3_puma_matching_latency_microseconds_bucket{env=~\"$env\"}[5m])) by (le)",
            "legendFormat": "≤ {{le}} µs",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "short",
                "color": {"mode": "palette-classic"},
            },
            "overrides": [],
        },
        "options": {
            "displayMode": "gradient",
            "orientation": "horizontal",
            "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
        },
    })

    # =========================================================================
    # ROW 2: IMBARQ CUSTÓDIA RECONCILIATION & CSD RECONCILIATION
    # =========================================================================
    panels.append({
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": 20},
        "id": next_id(),
        "panels": [],
        "title": "2. CENTRAL DEPOSITÁRIA CSD & RECONCILIAÇÃO DE CUSTÓDIA IMBARQ (BREAKS = 0)",
        "type": "row",
    })

    # Panel: Taxa de Reconciliação de Custódia IMBARQ
    panels.append({
        "id": next_id(),
        "title": "Taxa de Reconciliação de Custódia IMBARQ (%)",
        "description": "Taxa percentual de reconciliação de contas individualizadas CSD B3 vs. IMBARQ012/IMBARQ006. Requisito regulatório: 100.0000% (Breaks = 0).",
        "type": "gauge",
        "gridPos": {"h": 8, "w": 8, "x": 0, "y": 21},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "clamp_max((1 - (sum(b3_imbarq_custody_reconciliation_breaks_total{env=~\"$env\"}) / sum(b3_imbarq_custody_positions_total{env=~\"$env\"} > 0 or vector(1)))) * 100, 100)",
            "legendFormat": "Taxa de Conciliação CSD",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "percent",
                "min": 99.0,
                "max": 100,
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "red", "value": None},
                        {"color": "yellow", "value": 99.9},
                        {"color": "green", "value": 100.0},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
            "showThresholdLabels": True,
            "showThresholdMarkers": True,
        },
    })

    # Panel: Histórico de Quebras de Custódia (Breaks Counter)
    panels.append({
        "id": next_id(),
        "title": "IMBARQ Custody Breaks Breakdown (Target = 0)",
        "description": "Contagem de inconsistências de custódia por tipo de evento e código ISIN. Qualquer quebra gera alarme nível 1.",
        "type": "timeseries",
        "gridPos": {"h": 8, "w": 8, "x": 8, "y": 21},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [
            {
                "expr": "sum(b3_imbarq_custody_reconciliation_breaks_total{env=~\"$env\"}) by (break_type)",
                "legendFormat": "{{break_type}} Breaks",
                "refId": "A",
            },
            {
                "expr": "sum(b3_csd_unallocated_positions_gauge{env=~\"$env\"})",
                "legendFormat": "Posições em Conta Transitória",
                "refId": "B",
            },
        ],
        "fieldConfig": {
            "defaults": {
                "unit": "short",
                "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10},
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "red", "value": 1},
                    ],
                },
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "Posições em Conta Transitória"},
                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}],
                }
            ],
        },
        "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "max"]}},
    })

    # Panel: Emissão e Processamento de Arquivos IMBARQ (004, 006, 011, 012, 014, 018)
    panels.append({
        "id": next_id(),
        "title": "Arquivos IMBARQ Processados e Registros Conciliados",
        "description": "Volume de registros processados nos arquivos posicionais regulatórios IMBARQ ao final do ciclo D0.",
        "type": "barchart",
        "gridPos": {"h": 8, "w": 8, "x": 16, "y": 21},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "sum(b3_imbarq_file_records_processed_total{env=~\"$env\"}) by (file_code)",
            "legendFormat": "{{file_code}} Registros",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "short",
                "color": {"mode": "palette-classic"},
            },
            "overrides": [],
        },
        "options": {
            "orientation": "horizontal",
            "legend": {"displayMode": "list", "placement": "bottom"},
        },
    })

    # =========================================================================
    # ROW 3: DEFAULT WATERFALL CASCATA DE SALVAGUARDAS TIERS 1-5
    # =========================================================================
    panels.append({
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": 29},
        "id": next_id(),
        "panels": [],
        "title": "3. CÂMARA B3 CLEARING — CONSUMO DA CASCATA DE SALVAGUARDAS (DEFAULT WATERFALL TIERS 1-5)",
        "type": "row",
    })

    # Panel: Consumo % por Tier da Cascata de Salvaguardas
    panels.append({
        "id": next_id(),
        "title": "Taxa de Consumo por Tier da Cascata (Tiers 1 a 5)",
        "description": "Percentual consumido de cada camada do Default Waterfall da Câmara B3 (CPMI-IOSCO PFMI 4/7 e LAW-106B).",
        "type": "bargauge",
        "gridPos": {"h": 7, "w": 12, "x": 0, "y": 30},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "b3_clearing_default_waterfall_tier_consumption_ratio{env=~\"$env\"} * 100",
            "legendFormat": "{{tier_name}} ({{tier_level}})",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "percent",
                "min": 0,
                "max": 100,
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "yellow", "value": 25},
                        {"color": "orange", "value": 50},
                        {"color": "red", "value": 75},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "displayMode": "gradient",
            "orientation": "horizontal",
            "showUnfilled": True,
            "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
        },
    })

    # Panel: Capital Disponível vs. Consumido nas Salvaguardas (R$ Bilhões)
    panels.append({
        "id": next_id(),
        "title": "Capacidade Financeira da Cascata de Salvaguardas (R$ MM)",
        "description": "Colchão financeiro disponível e perdas absorvidas por tier de salvaguarda.",
        "type": "timeseries",
        "gridPos": {"h": 7, "w": 12, "x": 12, "y": 30},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [
            {
                "expr": "sum(b3_clearing_safeguards_available_capital_brl{env=~\"$env\"}) by (tier)",
                "legendFormat": "{{tier}} Disponível",
                "refId": "A",
            },
            {
                "expr": "sum(b3_clearing_safeguards_absorbed_losses_brl_total{env=~\"$env\"}) by (tier)",
                "legendFormat": "{{tier}} Absorvido",
                "refId": "B",
            },
        ],
        "fieldConfig": {
            "defaults": {
                "unit": "currencyBRL",
                "custom": {
                    "drawStyle": "line",
                    "lineWidth": 2,
                    "fillOpacity": 10,
                },
            },
            "overrides": [],
        },
        "options": {
            "tooltip": {"mode": "multi", "sort": "desc"},
            "legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "max"]},
        },
    })

    # Detalhamento dos Tiers e Inadimplência
    panels.append({
        "id": next_id(),
        "title": "Integridade do Capital Próprio B3 Skin-In-The-Game (Tier 3 SITG)",
        "description": "Monitoramento da preservação do capital dedicado próprio da B3 na compensação multilateral.",
        "type": "gauge",
        "gridPos": {"h": 6, "w": 6, "x": 0, "y": 37},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "b3_clearing_sitg_capital_intact_ratio{env=~\"$env\"} * 100",
            "legendFormat": "B3 SITG Intacto %",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "percent",
                "min": 0,
                "max": 100,
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "red", "value": None},
                        {"color": "orange", "value": 80},
                        {"color": "green", "value": 100},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "values": False}},
    })

    panels.append({
        "id": next_id(),
        "title": "Chamadas de Margem de Variação Intradiária (Pix & SPI)",
        "description": "Taxa de liquidação instantânea de chamadas de recomposição de margem via SPI.",
        "type": "timeseries",
        "gridPos": {"h": 6, "w": 18, "x": 6, "y": 37},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [
            {
                "expr": "sum(rate(b3_pix_margin_calls_issued_total{env=~\"$env\"}[5m]))",
                "legendFormat": "Chamadas Emitidas/s",
                "refId": "A",
            },
            {
                "expr": "sum(rate(b3_pix_margin_calls_settled_total{env=~\"$env\"}[5m]))",
                "legendFormat": "Chamadas Liquidadas Instantâneas/s",
                "refId": "B",
            },
        ],
        "fieldConfig": {
            "defaults": {
                "unit": "cps",
                "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 15},
            },
            "overrides": [],
        },
        "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max"]}},
    })

    # =========================================================================
    # ROW 4: SNG — SISTEMA NACIONAL DE GRAVAMES (DETRANS)
    # =========================================================================
    panels.append({
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": 43},
        "id": next_id(),
        "panels": [],
        "title": "4. SNG — SISTEMA NACIONAL DE GRAVAMES (DETRANS & CRÉDITO VEICULAR / LAW-112)",
        "type": "row",
    })

    # Panel: Volume de Inclusões e Baixas de Gravames
    panels.append({
        "id": next_id(),
        "title": "Volume de Inclusões vs. Baixas de Gravames (op/s)",
        "description": "Fluxo contínuo de registros de novas restrições financeiras e baixas após quitação junto aos Detrans (Res. CONTRAN 807/2020).",
        "type": "timeseries",
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 44},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [
            {
                "expr": "sum(rate(b3_sng_gravames_inclusions_total{env=~\"$env\", uf=~\"$uf\"}[5m]))",
                "legendFormat": "Inclusões de Gravame",
                "refId": "A",
            },
            {
                "expr": "sum(rate(b3_sng_gravames_discharges_total{env=~\"$env\", uf=~\"$uf\"}[5m]))",
                "legendFormat": "Baixas de Gravame (Quitações)",
                "refId": "B",
            },
        ],
        "fieldConfig": {
            "defaults": {
                "unit": "ops",
                "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 20},
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "Inclusões de Gravame"},
                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}],
                },
                {
                    "matcher": {"id": "byName", "options": "Baixas de Gravame (Quitações)"},
                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}],
                },
            ],
        },
        "options": {
            "tooltip": {"mode": "multi", "sort": "desc"},
            "legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max", "sum"]},
        },
    })

    # Panel: Distribuição por Detran Estadual (UF)
    panels.append({
        "id": next_id(),
        "title": "Volume de Gravames por Detran Estadual (UF)",
        "description": "Concentração de registros e baixas de gravames por unidade federativa.",
        "type": "barchart",
        "gridPos": {"h": 8, "w": 8, "x": 12, "y": 44},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "topk(10, sum(rate(b3_sng_gravames_inclusions_total{env=~\"$env\"}[1h])) by (uf))",
            "legendFormat": "Detran-{{uf}}",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "ops",
                "color": {"mode": "palette-classic"},
            },
            "overrides": [],
        },
        "options": {
            "orientation": "horizontal",
            "legend": {"displayMode": "list", "placement": "bottom"},
        },
    })

    # Panel: Rejeições de Unicidade LAW-112 (Bloqueio de Duplo Gravame)
    panels.append({
        "id": next_id(),
        "title": "SNG Tentativas de Duplo Gravame Bloqueadas (LAW-112)",
        "description": "Inviolabilidade de gravame ativo por Chassi/Renavam. Tentativas fraudulentas ou concorrentes bloqueadas pelo SNG.",
        "type": "stat",
        "gridPos": {"h": 8, "w": 4, "x": 20, "y": 44},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "sum(increase(b3_sng_law112_duplicate_lien_rejections_total{env=~\"$env\"}[1h]))",
            "legendFormat": "Rejeições LAW-112 (1h)",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "short",
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "yellow", "value": 5},
                        {"color": "red", "value": 20},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
            "colorMode": "background",
            "graphMode": "area",
        },
    })

    # =========================================================================
    # ROW 5: SRO — SISTEMA DE REGISTRO DE OPERAÇÕES SUSEP (SEGUROS)
    # =========================================================================
    panels.append({
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": 52},
        "id": next_id(),
        "panels": [],
        "title": "5. SRO — SISTEMA DE REGISTRO DE OPERAÇÕES SUSEP (SEGUROS & SINISTRALIDADE / LAW-113)",
        "type": "row",
    })

    # Panel: Registro de Apólices por Ramo de Seguro
    panels.append({
        "id": next_id(),
        "title": "Registro de Apólices e Endossos por Ramo SUSEP (op/s)",
        "description": "Taxa de emissão e registro de apólices no ambiente SRO V3 homologado SUSEP.",
        "type": "timeseries",
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 53},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "sum(rate(b3_sro_policy_registrations_total{env=~\"$env\"}[5m])) by (ramo)",
            "legendFormat": "{{ramo}}",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "ops",
                "custom": {
                    "drawStyle": "line",
                    "lineWidth": 2,
                    "fillOpacity": 15,
                    "stacking": {"mode": "normal", "group": "A"},
                },
            },
            "overrides": [],
        },
        "options": {
            "tooltip": {"mode": "multi", "sort": "desc"},
            "legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max", "lastNotNull"]},
        },
    })

    # Panel: Índice de Sinistralidade (Loss Ratio %)
    panels.append({
        "id": next_id(),
        "title": "Índice de Sinistralidade Consolidado (Loss Ratio %)",
        "description": "Razão entre Sinistros Ocorridos e Prêmios Ganhos no ecossistema SRO. Metas prudenciais SUSEP.",
        "type": "gauge",
        "gridPos": {"h": 8, "w": 6, "x": 12, "y": 53},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "(sum(b3_sro_incurred_claims_brl{env=~\"$env\"}) / sum(b3_sro_earned_premiums_brl{env=~\"$env\"} > 0 or vector(1))) * 100",
            "legendFormat": "Sinistralidade %",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "percent",
                "min": 0,
                "max": 120,
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "yellow", "value": 65},
                        {"color": "orange", "value": 80},
                        {"color": "red", "value": 100},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
            "showThresholdLabels": True,
            "showThresholdMarkers": True,
        },
    })

    # Panel: Sinistros vs. Prêmios em Moeda (R$ MM) e Validação LAW-113
    panels.append({
        "id": next_id(),
        "title": "Prêmios Arrecadados vs. Sinistros Liquidados (R$ MM)",
        "description": "Volume financeiro total sob tutela de custódia e registro regulatório no SRO B3.",
        "type": "timeseries",
        "gridPos": {"h": 8, "w": 6, "x": 18, "y": 53},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [
            {
                "expr": "sum(rate(b3_sro_earned_premiums_brl{env=~\"$env\"}[1h])) / 1e6",
                "legendFormat": "Prêmios (R$ MM/h)",
                "refId": "A",
            },
            {
                "expr": "sum(rate(b3_sro_incurred_claims_brl{env=~\"$env\"}[1h])) / 1e6",
                "legendFormat": "Sinistros (R$ MM/h)",
                "refId": "B",
            },
        ],
        "fieldConfig": {
            "defaults": {
                "unit": "currencyBRL",
                "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 15},
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "Sinistros (R$ MM/h)"},
                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}],
                }
            ],
        },
        "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "mean"]}},
    })

    # =========================================================================
    # ROW 6: BARRAMENTO CENTRAL DE LIQUIDAÇÃO UNIFICADA (LAW-106A/B/C)
    # =========================================================================
    panels.append({
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": 61},
        "id": next_id(),
        "panels": [],
        "title": "6. BARRAMENTO CENTRAL DE LIQUIDAÇÃO UNIFICADA & CONSERVAÇÃO FORMAL (LAW-106A / LAW-106B / LAW-106C)",
        "type": "row",
    })

    # Tripla de Invariantes LAW-106A, LAW-106B, LAW-106C
    panels.append({
        "id": next_id(),
        "title": "LAW-106A: Equilíbrio Contábil Global",
        "description": "Soma de Ativos == Soma de Passivos dos 10 subsistemas (Desbalanceamento estrito = R$ 0,00).",
        "type": "stat",
        "gridPos": {"h": 5, "w": 8, "x": 0, "y": 62},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "b3_central_bus_accounting_imbalance_brl{env=~\"$env\"}",
            "legendFormat": "Imbalance (R$)",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "currencyBRL",
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "red", "value": 0.01},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
            "colorMode": "background",
            "graphMode": "none",
        },
    })

    panels.append({
        "id": next_id(),
        "title": "LAW-106B: Não-Propagação de Default",
        "description": "Contenção de contágio de insolvência. Déficits residuais não absorvidos devem ser rigorosamente ZERO.",
        "type": "stat",
        "gridPos": {"h": 5, "w": 8, "x": 8, "y": 62},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "b3_central_bus_unabsorbed_deficit_brl{env=~\"$env\"}",
            "legendFormat": "Déficit Não-Absorvido",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "currencyBRL",
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "red", "value": 0.01},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
            "colorMode": "background",
            "graphMode": "none",
        },
    })

    panels.append({
        "id": next_id(),
        "title": "LAW-106C: Atomicidade Inter-Sistemas",
        "description": "Taxa de liquidação All-or-Nothing inter-subsistemas (DvP/PvP). Violação de perna parcial = 0.",
        "type": "stat",
        "gridPos": {"h": 5, "w": 8, "x": 16, "y": 62},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "b3_central_bus_atomic_settlement_success_ratio{env=~\"$env\"} * 100",
            "legendFormat": "Atomicidade All-or-None %",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "percent",
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "red", "value": None},
                        {"color": "green", "value": 100},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
            "colorMode": "background",
            "graphMode": "none",
        },
    })

    # Volume Multilateral Liquidado nos 10 Subsistemas
    panels.append({
        "id": next_id(),
        "title": "Liquidação Multilateral Unificada nos 10 Subsistemas B3 (R$ Bilhões)",
        "description": "Volume bruto e líquido compensado multilateralmente pelo Barramento Central (Equities, Derivatives, Balcão NoMe, Renda Fixa, Agro, CVM 175, Cripto/Drex, Pix Margem, CCEE Energia, Carbono CBIO).",
        "type": "timeseries",
        "gridPos": {"h": 9, "w": 16, "x": 0, "y": 67},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "sum(rate(b3_central_bus_subsystem_volume_brl{env=~\"$env\", subsystem=~\"$subsystem\"}[5m])) by (subsystem) / 1e9",
            "legendFormat": "{{subsystem}}",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "currencyBRL",
                "custom": {
                    "drawStyle": "line",
                    "lineWidth": 2,
                    "fillOpacity": 20,
                    "stacking": {"mode": "normal", "group": "A"},
                },
            },
            "overrides": [],
        },
        "options": {
            "tooltip": {"mode": "multi", "sort": "desc"},
            "legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "mean", "sum"]},
        },
    })

    # Netting Efficiency Ratio Gauge
    panels.append({
        "id": next_id(),
        "title": "Eficiência de Compensação Multilateral (Netting Factor %)",
        "description": "Redução percentual de liquidez necessária através do Barramento Central Multilateral contínuo.",
        "type": "gauge",
        "gridPos": {"h": 9, "w": 8, "x": 16, "y": 67},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "targets": [{
            "expr": "(1 - (sum(b3_central_bus_net_liquidity_settled_brl{env=~\"$env\"}) / sum(b3_central_bus_gross_liquidity_settled_brl{env=~\"$env\"} > 0 or vector(1)))) * 100",
            "legendFormat": "Eficiência Netting %",
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "percent",
                "min": 50,
                "max": 99,
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "yellow", "value": None},
                        {"color": "blue", "value": 70},
                        {"color": "green", "value": 85},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
            "showThresholdLabels": True,
            "showThresholdMarkers": True,
        },
    })

    dashboard["panels"] = panels
    return dashboard


def main():
    dashboard = build_dashboard()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, indent=2, ensure_ascii=False)

    print(f"✓ Dashboard oficial gerado com sucesso em: {OUTPUT_PATH}")
    print(f"✓ Total de painéis e seções criados: {len(dashboard['panels'])}")


if __name__ == "__main__":
    main()
