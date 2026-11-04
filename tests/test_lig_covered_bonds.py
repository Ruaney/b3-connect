"""Bateria de Testes Unitários — Registro, Monitoramento e Liquidação de LIGs (FL-049).

Valida:
1. Carga de dados a partir da fixture JSON oficial (`fixtures/lig_covered_bonds.json`).
2. Cálculo exato da Razão de Cobertura (Cover Ratio = VP_Cover_Pool / VP_LIG).
3. Conformidade regulatória de Sobrecolateralização Mínima (OC >= 5% / Cover Ratio >= 105%).
4. Glosa de créditos imobiliários inadimplentes (> 60 dias de atraso).
5. Ajuste proporcional de elegibilidade para créditos com LTV excedente ao teto legal (Resolução CMN 4.598/17).
6. Teste de Estresse de Liquidez de 180 Dias com choques adversos de inadimplência e projeções mensais.
7. Identificação de períodos deficitários e cálculo de déficit de liquidez em cenários de quebra.
8. Geração de relatórios de auditoria do Agente Fiduciário e payloads de integração com Balcão NoMe B3.
"""

from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

# Garante que a raiz do repositório esteja no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.lig_covered_bonds import (
    CoverPoolAsset,
    CoverPoolAssetType,
    CoverPoolComplianceStatus,
    CoverPoolSummary,
    LigBond,
    LigBondStatus,
    LigCoveredBondsEngine,
    LigIndexer,
    LigValidationError,
    LiquidityCashFlowPeriod,
    LiquidityStressTestResult,
    MAX_COMMERCIAL_LTV,
    MAX_DELINQUENCY_DAYS_ELIGIBLE,
    MAX_RESIDENTIAL_LTV,
    REGULATORY_CURE_DEADLINE_DAYS,
    REGULATORY_MINIMUM_COVER_RATIO,
    REGULATORY_MINIMUM_OC_PCT,
)


class TestLigCoveredBonds(unittest.TestCase):
    """Suíte de testes do motor de Letras Imobiliárias Garantidas (LIG / Covered Bonds)."""

    def setUp(self) -> None:
        self.engine = LigCoveredBondsEngine()
        self.fixtures_path = ROOT_DIR / "fixtures" / "lig_covered_bonds.json"

    def test_load_from_json_fixture(self) -> None:
        """Verifica a integridade do carregamento da fixture lig_covered_bonds.json."""
        self.assertTrue(self.fixtures_path.exists(), f"Fixture não encontrada em {self.fixtures_path}")
        self.engine.load_from_json_file(self.fixtures_path)

        # Deve carregar os 4 pools de teste
        pools = self.engine._pools
        self.assertEqual(len(pools), 4)
        self.assertIn("CP-LIG-2026-001", pools)
        self.assertIn("CP-LIG-DEFICIENT-002", pools)
        self.assertIn("CP-LIG-INADIMPLENTE-003", pools)
        self.assertIn("CP-LIG-STRESS-FAIL-004", pools)

        # Valida bonds e assets do pool de referência
        bonds = self.engine.get_bonds("CP-LIG-2026-001")
        assets = self.engine.get_assets("CP-LIG-2026-001")
        self.assertEqual(len(bonds), 2)
        self.assertEqual(len(assets), 7)

    def test_compliant_pool_overcollateralization_and_cover_ratio(self) -> None:
        """Valida que o pool padrão atinge Cover Ratio >= 105% e status COMPLIANT."""
        self.engine.load_from_json_file(self.fixtures_path)
        summary = self.engine.evaluate_cover_pool("CP-LIG-2026-001")

        self.assertIsInstance(summary, CoverPoolSummary)
        self.assertEqual(summary.cover_pool_id, "CP-LIG-2026-001")
        self.assertEqual(summary.issuer_name, "BANCO BRADESCO S.A.")
        self.assertEqual(
            summary.fiduciary_agent_name,
            "OLIVEIRA TRUST DISTRIBUIDORA DE TITULOS E VALORES MOBILIARIOS S.A.",
        )

        # VP LIGs: 498.500.000,00 (Pre) + 312.000.000,00 (IPCA) = 810.500.000,00
        expected_lig_vp = Decimal("810500000.00")
        self.assertEqual(summary.total_lig_present_value, expected_lig_vp)

        # VP Cover Pool: 355M + 252M + 148M + 105M + 50.5M + 12M + 20M = 942.500.000,00
        expected_pool_vp = Decimal("942500000.00")
        self.assertEqual(summary.total_cover_pool_eligible_vp, expected_pool_vp)

        # Cover Ratio = 942.500.000,00 / 810.500.000,00 = 1.16286... ~ 1.1629 (116.29%)
        self.assertEqual(summary.cover_ratio, Decimal("1.1629"))
        self.assertEqual(summary.overcollateralization_pct, Decimal("0.1629"))

        # Conformidade regulatória
        self.assertTrue(summary.is_overcollateralization_compliant)
        self.assertEqual(summary.compliance_status, CoverPoolComplianceStatus.COMPLIANT)
        self.assertEqual(summary.shortfall_amount, Decimal("0.00"))
        self.assertEqual(summary.cure_deadline_days, REGULATORY_CURE_DEADLINE_DAYS)

        # Composição dos ativos por tipo
        breakdown = summary.asset_breakdown_by_type
        self.assertEqual(breakdown["REAL_ESTATE_CREDIT"], Decimal("755000000.00"))
        self.assertEqual(breakdown["TREASURY_BOND"], Decimal("155500000.00"))
        self.assertEqual(breakdown["HEDGE_DERIVATIVE"], Decimal("12000000.00"))
        self.assertEqual(breakdown["CASH_EQUIVALENT"], Decimal("20000000.00"))

    def test_deficient_pool_shortfall_and_cure_period(self) -> None:
        """Valida detecção de déficit em pool com Cover Ratio inferior a 105%."""
        self.engine.load_from_json_file(self.fixtures_path)
        summary = self.engine.evaluate_cover_pool("CP-LIG-DEFICIENT-002")

        # VP LIG: 400.000.000,00 | VP Ativos: 408.000.000,00
        # Cover Ratio = 408.000.000 / 400.000.000 = 1.0200 (102% < 105%)
        self.assertEqual(summary.cover_ratio, Decimal("1.0200"))
        self.assertEqual(summary.overcollateralization_pct, Decimal("0.0200"))
        self.assertFalse(summary.is_overcollateralization_compliant)
        self.assertEqual(
            summary.compliance_status, CoverPoolComplianceStatus.DEFICIENT_UNDER_CURE_PERIOD
        )

        # Mínimo exigido: 400.000.000 * 1.05 = 420.000.000,00
        # Déficit = 420.000.000 - 408.000.000 = 12.000.000,00
        self.assertEqual(summary.shortfall_amount, Decimal("12000000.00"))
        self.assertEqual(summary.cure_deadline_days, 15)

    def test_ineligible_assets_delinquency_and_excess_ltv(self) -> None:
        """Valida regras de descarte e glosa: inadimplência > 60 dias e teto de LTV."""
        self.engine.load_from_json_file(self.fixtures_path)
        summary = self.engine.evaluate_cover_pool("CP-LIG-INADIMPLENTE-003")

        # Ativo 1: Inadimplente há 75 dias (glosa total de R$ 50M)
        # Ativo 2: LTV 95% sobre teto 80% (glosa de excesso de LTV: R$ 100M * (1 - 0.80/0.95))
        # Ativo 4: Derivativo com MtM negativo de -R$ 5M
        self.assertGreater(summary.ineligible_assets_count, 0)
        self.assertGreater(summary.ineligible_present_value_glosa, Decimal("0.00"))

        # Verifica cálculo individual de ativo com dias de atraso
        asset_inad = self.engine._pools["CP-LIG-INADIMPLENTE-003"]["assets"]["AST-INAD-REC-001"]
        self.assertEqual(asset_inad.compute_eligible_present_value(), Decimal("0.00"))

        # Ativo com atraso de 45 dias deve ter haircut preventivo de 20%
        asset_haircut = CoverPoolAsset(
            asset_id="AST-HAIRCUT-TEST",
            asset_type=CoverPoolAssetType.REAL_ESTATE_CREDIT,
            description="Crédito com 45 dias de atraso",
            contract_or_cetip_code="CTR-TEST-45D",
            outstanding_balance=Decimal("1000000.00"),
            present_value=Decimal("1000000.00"),
            days_past_due=45,
            is_eligible=True,
        )
        self.assertEqual(asset_haircut.compute_eligible_present_value(), Decimal("800000.00"))

    def test_excess_ltv_capping_formula(self) -> None:
        """Valida que o valor elegível é capado pela proporção do teto legal de LTV."""
        asset_ltv = CoverPoolAsset(
            asset_id="AST-LTV-TEST",
            asset_type=CoverPoolAssetType.REAL_ESTATE_CREDIT,
            description="Crédito com LTV de 100% sobre teto de 80%",
            contract_or_cetip_code="CTR-TEST-LTV",
            outstanding_balance=Decimal("100000.00"),
            present_value=Decimal("100000.00"),
            property_valuation=Decimal("100000.00"),
            ltv_ratio=Decimal("1.0000"),
            max_eligible_ltv=Decimal("0.8000"),
            days_past_due=0,
            is_eligible=True,
        )
        # 100.000 * (0.80 / 1.00) = 80.000,00
        self.assertEqual(asset_ltv.compute_eligible_present_value(), Decimal("80000.00"))

    def test_liquidity_stress_test_success_180d(self) -> None:
        """Valida aprovação no teste de liquidez de 180 dias do pool de referência."""
        self.engine.load_from_json_file(self.fixtures_path)
        stress_result = self.engine.run_liquidity_stress_test(
            cover_pool_id="CP-LIG-2026-001",
            projection_days=180,
            stress_default_pct=Decimal("0.1500"),  # 15% de corte por estresse
        )

        self.assertIsInstance(stress_result, LiquidityStressTestResult)
        self.assertTrue(stress_result.is_compliant)
        self.assertGreater(stress_result.minimum_cumulative_liquidity, Decimal("0.00"))
        self.assertIsNone(stress_result.shortfall_period_id)
        self.assertEqual(len(stress_result.periods), 6)

        # Verifica consistência cronológica dos 6 períodos
        for p in stress_result.periods:
            self.assertGreater(p.cumulative_liquidity_balance, Decimal("0.00"))

    def test_liquidity_stress_test_failure_detection(self) -> None:
        """Valida reprovação no teste de liquidez para pool com descasamento de vencimentos."""
        self.engine.load_from_json_file(self.fixtures_path)
        stress_result = self.engine.run_liquidity_stress_test(
            cover_pool_id="CP-LIG-STRESS-FAIL-004",
            projection_days=180,
            stress_default_pct=Decimal("0.1500"),
        )

        self.assertFalse(stress_result.is_compliant)
        self.assertLess(stress_result.minimum_cumulative_liquidity, Decimal("0.00"))
        self.assertEqual(stress_result.shortfall_period_id, "PERIOD-M3")
        self.assertGreater(stress_result.shortfall_amount, Decimal("0.00"))
        self.assertTrue(any("REPROVADO" in n for n in stress_result.notes))

    def test_fiduciary_agent_report_generation(self) -> None:
        """Valida estrutura do parecer formal gerado pelo Agente Fiduciário."""
        self.engine.load_from_json_file(self.fixtures_path)
        report = self.engine.generate_fiduciary_agent_report("CP-LIG-2026-001")

        self.assertIn("report_metadata", report)
        self.assertIn("cover_pool_summary", report)
        self.assertIn("liquidity_stress_test_180d", report)
        self.assertIn("regulatory_conclusion", report)

        conclusion = report["regulatory_conclusion"]
        self.assertTrue(conclusion["overcollateralization_compliant"])
        self.assertTrue(conclusion["liquidity_compliant"])
        self.assertEqual(conclusion["overall_verdict"], "APROVADO_CONFORME")

    def test_nome_registration_payload_export(self) -> None:
        """Valida que o payload exportado para o NoMe Balcão possui todas as tags obrigatórias."""
        self.engine.load_from_json_file(self.fixtures_path)
        payload = self.engine.export_nome_registration_payload("CP-LIG-2026-001")

        self.assertIn("nome_header", payload)
        self.assertEqual(payload["nome_header"]["system"], "B3_BALCAO_NOME")
        self.assertIn("cover_pool_id", payload)
        self.assertEqual(payload["custody_account_segregated"], "3901-LIG-AFETACAO-001")
        self.assertIn("bonds", payload)
        self.assertIn("assets", payload)
        self.assertIn("solvency_metrics", payload)
        self.assertTrue(payload["solvency_metrics"]["is_compliant"])

    def test_error_handling_and_validations(self) -> None:
        """Valida tratamento de exceções de integridade cadastral do motor."""
        # Tentativa de registrar pool com id vazio
        with self.assertRaises(LigValidationError):
            self.engine.register_cover_pool(
                cover_pool_id="",
                issuer_name="Banco X",
                issuer_cnpj="00000000000100",
                fiduciary_agent_name="DTVM Y",
                fiduciary_agent_cnpj="11111111000100",
            )

        # Adicionar LIG a pool inexistente
        fake_bond = LigBond(
            bond_id="LIG-FAKE",
            isin="BRFAKE000001",
            nome_code="LIGFAKE",
            indexer=LigIndexer.PREFIXADO,
            annual_rate=Decimal("0.1000"),
            spread_rate=Decimal("0.0000"),
            issue_date="2026-01-01",
            maturity_date="2028-01-01",
            nominal_unit_value=Decimal("1000.00"),
            quantity=Decimal("100"),
            outstanding_nominal_value=Decimal("100000.00"),
            present_value=Decimal("100000.00"),
        )
        with self.assertRaises(LigValidationError):
            self.engine.add_lig_bond("POOL-INEXISTENTE", fake_bond)

        # Adicionar ativo a pool inexistente
        fake_asset = CoverPoolAsset(
            asset_id="AST-FAKE",
            asset_type=CoverPoolAssetType.REAL_ESTATE_CREDIT,
            description="Fake",
            contract_or_cetip_code="CTR-FAKE",
            outstanding_balance=Decimal("100000.00"),
            present_value=Decimal("100000.00"),
        )
        with self.assertRaises(LigValidationError):
            self.engine.add_cover_pool_asset("POOL-INEXISTENTE", fake_asset)

    def test_empty_pool_evaluation(self) -> None:
        """Valida avaliação de pool recém-criado sem emissões ativas."""
        self.engine.register_cover_pool(
            cover_pool_id="CP-EMPTY-001",
            issuer_name="Banco Novo",
            issuer_cnpj="12345678000199",
            fiduciary_agent_name="Fiduciária Nova",
            fiduciary_agent_cnpj="98765432000188",
        )
        summary = self.engine.evaluate_cover_pool("CP-EMPTY-001")
        self.assertEqual(summary.total_lig_present_value, Decimal("0.00"))
        self.assertTrue(summary.is_overcollateralization_compliant)
        self.assertEqual(summary.cover_ratio, Decimal("1.0000"))


if __name__ == "__main__":
    unittest.main()
