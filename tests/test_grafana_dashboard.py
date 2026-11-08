"""Testes unitários e formais para o dashboard de produção do Grafana B3.

Valida a integridade do JSON, schemas de painéis, variáveis de template e
cobertura de todas as métricas críticas exigidas:
- Throughput PUMA (msg/s) e latência em microssegundos (p50, p95, p99).
- Taxa de reconciliação de custódia IMBARQ (Breaks = 0).
- Consumo da Cascata de Salvaguardas (Default Waterfall Tiers 1-5).
- Volume de inclusões e baixas de gravames no SNG (Detrans).
- Registro de apólices e índice de sinistralidade no SRO (SUSEP).
- Estado do Barramento Central de Liquidação Unificada (LAW-106A/B/C).
"""

import json
from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_FILE = REPO_ROOT / "observability" / "grafana_b3_enterprise_overview.json"


class TestGrafanaDashboard(unittest.TestCase):
    """Bateria de validação do dashboard oficial do Grafana B3."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.assertTrue(DASHBOARD_FILE.exists(), f"Arquivo {DASHBOARD_FILE} não encontrado!")
        with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
            cls.dashboard = json.load(f)

    def test_dashboard_json_structure(self) -> None:
        """Valida a estrutura raiz do JSON padrão do Grafana."""
        required_root_keys = [
            "schemaVersion",
            "title",
            "uid",
            "tags",
            "timezone",
            "refresh",
            "time",
            "templating",
            "panels",
        ]
        for key in required_root_keys:
            self.assertIn(key, self.dashboard, f"Chave obrigatória ausente no JSON: {key}")

        self.assertGreaterEqual(self.dashboard["schemaVersion"], 30, "schemaVersion deve ser moderna (>= 30)")
        self.assertEqual(self.dashboard["uid"], "b3-enterprise-overview")
        self.assertIn("b3", self.dashboard["tags"])
        self.assertIn("production", self.dashboard["tags"])
        self.assertGreaterEqual(len(self.dashboard["panels"]), 15, "Dashboard deve possuir painéis suficientes")

    def test_unique_panel_ids_and_grid(self) -> None:
        """Valida que todos os IDs de painel são únicos e que o layout gridPos é válido."""
        panel_ids = set()
        for panel in self.dashboard["panels"]:
            pid = panel.get("id")
            self.assertIsNotNone(pid, "Painel sem ID")
            self.assertNotIn(pid, panel_ids, f"ID duplicado de painel: {pid}")
            panel_ids.add(pid)

            self.assertIn("gridPos", panel, f"Painel {pid} sem gridPos")
            grid = panel["gridPos"]
            for dim in ["h", "w", "x", "y"]:
                self.assertIn(dim, grid)
                self.assertIsInstance(grid[dim], int)
                self.assertGreaterEqual(grid[dim], 0)

    def test_puma_throughput_and_latency_metrics(self) -> None:
        """Valida Throughput PUMA (msg/s) e latência em microssegundos (p50, p95, p99)."""
        all_exprs = []
        for panel in self.dashboard["panels"]:
            for target in panel.get("targets", []):
                all_exprs.append(target.get("expr", ""))

        content = " ".join(all_exprs)

        # Throughput
        self.assertIn("b3_puma_messages_total", content, "Métrica de throughput PUMA ausente")
        self.assertIn("rate(", content)

        # Latência em microssegundos (p50, p95, p99)
        self.assertIn("b3_puma_matching_latency_microseconds_bucket", content, "Histograma de latência ausente")
        self.assertIn("0.5", content, "Percentil p50 ausente")
        self.assertIn("0.95", content, "Percentil p95 ausente")
        self.assertIn("0.99", content, "Percentil p99 ausente")

    def test_imbarq_custody_reconciliation_breaks(self) -> None:
        """Valida taxa de reconciliação de custódia IMBARQ (Breaks = 0)."""
        all_exprs = []
        has_breaks_panel = False
        for panel in self.dashboard["panels"]:
            title = panel.get("title", "")
            if "IMBARQ" in title and "Breaks" in title:
                has_breaks_panel = True
            for target in panel.get("targets", []):
                all_exprs.append(target.get("expr", ""))

        self.assertTrue(has_breaks_panel, "Painel de Breaks IMBARQ não encontrado")
        content = " ".join(all_exprs)
        self.assertIn("b3_imbarq_custody_reconciliation_breaks_total", content)
        self.assertIn("b3_imbarq_file_records_processed_total", content)

    def test_safeguards_default_waterfall(self) -> None:
        """Valida o consumo da Cascata de Salvaguardas (Default Waterfall Tiers 1-5)."""
        all_exprs = []
        has_waterfall_section = False
        for panel in self.dashboard["panels"]:
            title = panel.get("title", "")
            if "Cascata" in title or "Waterfall" in title:
                has_waterfall_section = True
            for target in panel.get("targets", []):
                all_exprs.append(target.get("expr", ""))

        self.assertTrue(has_waterfall_section, "Seção de Cascata de Salvaguardas não encontrada")
        content = " ".join(all_exprs)
        self.assertIn("b3_clearing_default_waterfall_tier_consumption_ratio", content)
        self.assertIn("b3_clearing_safeguards_available_capital_brl", content)

    def test_sng_detrans_metrics(self) -> None:
        """Valida o volume de inclusões e baixas de gravames no SNG (Detrans)."""
        all_exprs = []
        has_sng_section = False
        for panel in self.dashboard["panels"]:
            title = panel.get("title", "")
            if "SNG" in title:
                has_sng_section = True
            for target in panel.get("targets", []):
                all_exprs.append(target.get("expr", ""))

        self.assertTrue(has_sng_section, "Seção de SNG não encontrada")
        content = " ".join(all_exprs)
        self.assertIn("b3_sng_gravames_inclusions_total", content)
        self.assertIn("b3_sng_gravames_discharges_total", content)

    def test_sro_susep_metrics(self) -> None:
        """Valida o registro de apólices e índice de sinistralidade no SRO (SUSEP)."""
        all_exprs = []
        has_sro_section = False
        for panel in self.dashboard["panels"]:
            title = panel.get("title", "")
            if "SRO" in title:
                has_sro_section = True
            for target in panel.get("targets", []):
                all_exprs.append(target.get("expr", ""))

        self.assertTrue(has_sro_section, "Seção de SRO não encontrada")
        content = " ".join(all_exprs)
        self.assertIn("b3_sro_policy_registrations_total", content)
        self.assertIn("b3_sro_incurred_claims_brl", content)
        self.assertIn("b3_sro_earned_premiums_brl", content)

    def test_central_bus_law_106_invariants(self) -> None:
        """Valida o estado do Barramento Central de Liquidação Unificada (LAW-106A/B/C)."""
        all_exprs = []
        has_law106_section = False
        for panel in self.dashboard["panels"]:
            title = panel.get("title", "")
            if "LAW-106" in title or "Barramento Central" in title:
                has_law106_section = True
            for target in panel.get("targets", []):
                all_exprs.append(target.get("expr", ""))

        self.assertTrue(has_law106_section, "Seção de Barramento Central / LAW-106 não encontrada")
        content = " ".join(all_exprs)
        self.assertIn("b3_central_bus_accounting_imbalance_brl", content)  # LAW-106A
        self.assertIn("b3_central_bus_unabsorbed_deficit_brl", content)    # LAW-106B
        self.assertIn("b3_central_bus_atomic_settlement_success_ratio", content)  # LAW-106C
        self.assertIn("b3_central_bus_subsystem_volume_brl", content)     # 10 subsistemas


if __name__ == "__main__":
    unittest.main()
