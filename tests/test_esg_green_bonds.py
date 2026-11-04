"""Suíte de Testes Unitários — Registro e Certificação de Green Bonds e SLBs no Balcão NoMe B3 (FL-065).

Valida integralmente:
1. Carga, integridade e consistência cadastral a partir da fixture oficial JSON.
2. Regras de Taxonomia Verde e Use of Proceeds (ICMA Green Bond Principles):
   - Alocação em setores elegíveis (Energia Renovável, Transporte Limpo, Agro Sustentável).
   - Conservação de recursos: Net_Proceeds == Total_Allocated + Total_Unallocated.
   - Bloqueio estrito de sobrealocação (limite da captação líquida e do custo do projeto).
3. Metas de Sustentabilidade de SLBs (ICMA Sustainability-Linked Bond Principles):
   - Cadastro de KPIs, linhas de base (baseline) e direção (DECREASE vs INCREASE).
   - Metas SPT com datas de observação e prazos regulamentares de auditoria.
4. Monitoramento e Disparo de Gatilho de Step-Up de Cupom:
   - Meta atingida (ACHIEVED): manutenção da taxa base do cupom.
   - Meta não atingida (MISSED): disparo imediato do acréscimo de Step-Up (+bps).
   - Inadimplemento de laudo de auditoria (DEFAULTED_REPORTING): disparo automático de penalidade.
5. Apuração e Projeção Financeira de Fluxo de Caixa:
   - Cálculo de juros base e parcela punitiva de Step-Up centavo a centavo.
6. Relatórios Oficiais de Conformidade e Selo ESG B3:
   - Relatório de Alocação (Allocation Report).
   - Relatório de Impacto Ambiental (Impact Report).
   - Relatório de Progresso de Metas SLB (SlbProgressReport).
   - Dossiê de Certificação NoMe com hash criptográfico SHA-256.
7. Verificação Formal de Invariantes Matemáticos de Mercado e Custódia ESG.
"""

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.esg_green_bonds import (
    AllocationReport,
    B3EsgCertificationDossier,
    CouponBenchmark,
    CouponCashflowItem,
    EligibleGreenProject,
    EsgBondEmission,
    EsgBondError,
    EsgCertificationStatus,
    EsgConservationError,
    EsgGreenBondsEngine,
    EsgInstrumentType,
    EsgValidationError,
    GreenTaxonomySector,
    ImpactReport,
    KpiDefinition,
    KpiDirection,
    PaymentFrequency,
    SPOStatus,
    SecondPartyOpinion,
    SlbProgressReport,
    SptStatus,
    SptTarget,
    StepUpTriggerError,
    TaxonomyNonComplianceError,
    UnallocatedProceedsPolicy,
    round_cents,
    round_rate,
)

FIXTURES_DIR = ROOT_DIR / "fixtures"


class TestEsgGreenBonds(unittest.TestCase):
    """Testes unitários para o subsistema de Green Bonds e SLBs no Balcão NoMe B3."""

    def setUp(self) -> None:
        """Inicializa o motor ESG a partir da fixture oficial."""
        self.fixture_path = FIXTURES_DIR / "esg_green_bonds.json"
        self.engine = EsgGreenBondsEngine.load_from_json(self.fixture_path)

    # -----------------------------------------------------------------------
    # 1. Carga de Fixtures e Integridade Cadastral
    # -----------------------------------------------------------------------

    def test_fixture_loading_and_catalog_indexing(self) -> None:
        """Verifica se todas as emissões da fixture foram carregadas e indexadas corretamente."""
        self.assertIn("DEB-SOLAR-01", self.engine.emissions)
        self.assertIn("SLB-PULP-01", self.engine.emissions)
        self.assertIn("SLB-STEEL-02", self.engine.emissions)
        self.assertIn("CRA-AGRO-VERDE-03", self.engine.emissions)

        solar = self.engine.get_emission("DEB-SOLAR-01")
        self.assertEqual(solar.instrument_type, EsgInstrumentType.GREEN_BOND)
        self.assertEqual(solar.isin, "BROMG4DBS012")
        self.assertEqual(solar.gross_proceeds, Decimal("150000000.00"))
        self.assertEqual(solar.issuance_costs, Decimal("1500000.00"))
        self.assertEqual(solar.net_proceeds, Decimal("148500000.00"))
        self.assertEqual(solar.base_coupon_rate, Decimal("0.06500000"))
        self.assertEqual(solar.coupon_benchmark, CouponBenchmark.IPCA)
        self.assertEqual(solar.payment_frequency, PaymentFrequency.SEMIANNUAL)
        self.assertTrue(solar.is_fully_allocated)
        self.assertIsNotNone(solar.spo)
        self.assertEqual(solar.spo.status, SPOStatus.ALIGNED)

    # -----------------------------------------------------------------------
    # 2. Taxonomia Verde e Conservação de Recursos (Use of Proceeds)
    # -----------------------------------------------------------------------

    def test_green_bond_proceeds_conservation(self) -> None:
        """Valida que Net_Proceeds == Total_Allocated + Total_Unallocated."""
        for code, emission in self.engine.emissions.items():
            if emission.instrument_type != EsgInstrumentType.SUSTAINABILITY_LINKED_BOND:
                conservation = emission.total_allocated_proceeds + emission.total_unallocated_proceeds
                self.assertEqual(
                    conservation,
                    emission.net_proceeds,
                    f"Conservação violada no ativo {code}: {conservation} != {emission.net_proceeds}"
                )
                self.assertLessEqual(
                    emission.total_allocated_proceeds,
                    emission.net_proceeds,
                    f"Recursos alocados excedem recursos líquidos no ativo {code}"
                )

    def test_partial_allocation_cra_agro(self) -> None:
        """Valida emissão com alocação parcial e gestão de caixa transitório."""
        cra = self.engine.get_emission("CRA-AGRO-VERDE-03")
        self.assertFalse(cra.is_fully_allocated)
        self.assertEqual(cra.total_allocated_proceeds, Decimal("45000000.00"))
        self.assertEqual(cra.total_unallocated_proceeds, Decimal("34200000.00"))
        self.assertEqual(cra.net_proceeds, Decimal("79200000.00"))
        self.assertEqual(cra.allocation_ratio_pct, Decimal("56.8182"))

    def test_allocate_new_eligible_project(self) -> None:
        """Valida alocação adicional em projeto elegível respeitando a capacidade remanescente."""
        new_project = EligibleGreenProject(
            project_id="PRJ-BIO-AGRO-02",
            name="Usina de Biometano a partir de Dejetos Bovinos",
            sector=GreenTaxonomySector.CIRCULAR_ECONOMY_WASTE,
            description="Produção de biometano para substituição de óleo diesel na frota agrícola.",
            total_project_cost=Decimal("25000000.00"),
            allocated_amount=Decimal("20000000.00"),
            allocation_date=date(2025, 2, 10),
            environmental_impact_metric="tco2e_avoided_per_year",
            environmental_impact_value=Decimal("18000.000"),
            location_uf="MS",
            verification_doc_ref="DOC-CETESB-LIC-2025-019"
        )

        self.engine.allocate_project("CRA-AGRO-VERDE-03", new_project)
        cra = self.engine.get_emission("CRA-AGRO-VERDE-03")
        self.assertEqual(cra.total_allocated_proceeds, Decimal("65000000.00"))
        self.assertEqual(cra.total_unallocated_proceeds, Decimal("14200000.00"))
        self.assertEqual(len(cra.projects), 2)

    def test_prevent_over_allocation_beyond_net_proceeds(self) -> None:
        """Verifica que o motor bloqueia alocação cujo valor exceda o saldo não alocado."""
        excessive_project = EligibleGreenProject(
            project_id="PRJ-OVER-01",
            name="Projeto Megawatt Excessivo",
            sector=GreenTaxonomySector.RENEWABLE_ENERGY,
            description="Tentativa de alocar montante superior ao limite.",
            total_project_cost=Decimal("100000000.00"),
            allocated_amount=Decimal("50000000.00"),  # Disponível é apenas 34.2M
            allocation_date=date(2025, 3, 1),
            environmental_impact_metric="tco2e_avoided_per_year",
            environmental_impact_value=Decimal("10000.000"),
            location_uf="GO",
            verification_doc_ref="DOC-TEST"
        )

        with self.assertRaises(TaxonomyNonComplianceError):
            self.engine.allocate_project("CRA-AGRO-VERDE-03", excessive_project)

    def test_prevent_project_cost_over_allocation(self) -> None:
        """Verifica que o valor alocado não pode exceder o custo total do projeto."""
        with self.assertRaises(TaxonomyNonComplianceError):
            EligibleGreenProject(
                project_id="PRJ-INVALID-COST",
                name="Projeto Sobrealocado",
                sector=GreenTaxonomySector.GREEN_BUILDINGS,
                description="Alocação maior que o custo da obra.",
                total_project_cost=Decimal("1000000.00"),
                allocated_amount=Decimal("1500000.00"),  # Maior que 1M
                allocation_date=date(2025, 1, 1),
                environmental_impact_metric="kwh_saved",
                environmental_impact_value=Decimal("500.000"),
                location_uf="SP",
                verification_doc_ref="DOC-LEED"
            )

    def test_prevent_allocation_in_slb(self) -> None:
        """Verifica que títulos SLB não aceitam alocação Use-of-Proceeds."""
        project = EligibleGreenProject(
            project_id="PRJ-SLB-TEST",
            name="Tentativa Alocação SLB",
            sector=GreenTaxonomySector.RENEWABLE_ENERGY,
            description="SLBs financiam fins corporativos gerais.",
            total_project_cost=Decimal("1000000.00"),
            allocated_amount=Decimal("500000.00"),
            allocation_date=date(2025, 1, 1),
            environmental_impact_metric="tco2e",
            environmental_impact_value=Decimal("100.000"),
            location_uf="SP",
            verification_doc_ref="DOC-TEST"
        )
        with self.assertRaises(TaxonomyNonComplianceError):
            self.engine.allocate_project("SLB-PULP-01", project)

    def test_deallocate_project(self) -> None:
        """Valida que remover um projeto desfaz a alocação e devolve os recursos ao saldo não alocado."""
        cra = self.engine.get_emission("CRA-AGRO-VERDE-03")
        initial_allocated = cra.total_allocated_proceeds
        removed = self.engine.deallocate_project("CRA-AGRO-VERDE-03", "PRJ-ABC-CERRADO-01")
        self.assertEqual(removed.project_id, "PRJ-ABC-CERRADO-01")
        self.assertEqual(cra.total_allocated_proceeds, Decimal("0.00"))
        self.assertEqual(cra.total_unallocated_proceeds, cra.net_proceeds)
        self.assertEqual(cra.allocation_ratio_pct, Decimal("0.0000"))

    # -----------------------------------------------------------------------
    # 3. Metas de Sustentabilidade (KPI / SPT) e Disparo de Step-Up de Cupom
    # -----------------------------------------------------------------------

    def test_slb_target_achieved_no_step_up(self) -> None:
        """Valida que o cumprimento da meta SPT (ACHIEVED) não ativa o Step-Up e mantém o cupom base."""
        slb_pulp = self.engine.get_emission("SLB-PULP-01")
        self.assertFalse(slb_pulp.step_up_applied)
        self.assertEqual(slb_pulp.base_coupon_rate, Decimal("0.05750000"))
        self.assertEqual(slb_pulp.current_coupon_rate, Decimal("0.05750000"))

        spt = slb_pulp.spts[0]
        self.assertEqual(spt.status, SptStatus.ACHIEVED)
        self.assertLessEqual(spt.measured_value, spt.target_value)  # DECREASE: 0.2850 <= 0.3000

    def test_slb_target_missed_triggers_step_up(self) -> None:
        """Valida que o não atingimento da meta SPT (MISSED) dispara o Step-Up aumentando o cupom."""
        slb_steel = self.engine.get_emission("SLB-STEEL-02")
        self.assertTrue(slb_steel.step_up_applied)
        self.assertEqual(slb_steel.base_coupon_rate, Decimal("0.06000000"))

        # Step-up de 50.0 bps = +0.00500000 -> taxa ativa = 0.06500000 (6.50% a.a.)
        self.assertEqual(slb_steel.current_coupon_rate, Decimal("0.06500000"))
        self.assertEqual(slb_steel.step_up_effective_date, date(2025, 12, 31))
        self.assertIn("Meta SPT-STEEL-2025 não atingida", slb_steel.step_up_reason)

    def test_evaluate_spt_dynamically_decrease(self) -> None:
        """Testa avaliação dinâmica de meta SPT com direção DECREASE."""
        # Cadastra nova emissão SLB
        self.engine.register_slb(
            asset_code="SLB-TEST-DYN",
            isin="BRDYNSLB0014",
            issuer_name="Empresa Teste Dinâmico S.A.",
            issuer_cnpj="55.666.777/0001-88",
            issue_date=date(2024, 1, 1),
            maturity_date=date(2029, 1, 1),
            nominal_unit_value=Decimal("1000.00"),
            issued_quantity=Decimal("50000"),
            base_coupon_rate=Decimal("0.07000000"),  # 7.00% a.a.
            coupon_benchmark=CouponBenchmark.PRE,
            payment_frequency=PaymentFrequency.ANNUAL,
            issuance_costs=Decimal("500000.00")
        )

        kpi = KpiDefinition(
            kpi_id="KPI-WATER-01",
            name="Consumo Específico de Água Industrial",
            unit_of_measure="m3/ton",
            baseline_year=2023,
            baseline_value=Decimal("4.50"),
            methodology="ISO 14046 Water Footprint",
            direction=KpiDirection.DECREASE
        )
        self.engine.add_kpi_to_slb("SLB-TEST-DYN", kpi)

        spt = SptTarget(
            spt_id="SPT-WATER-2026",
            kpi_id="KPI-WATER-01",
            target_year=2026,
            observation_date=date(2026, 12, 31),
            verification_deadline=date(2027, 3, 31),
            target_value=Decimal("3.20"),
            step_up_spread_bps=Decimal("30.0")  # +0.30%
        )
        self.engine.add_spt_target("SLB-TEST-DYN", spt)

        dyn = self.engine.get_emission("SLB-TEST-DYN")
        self.assertFalse(dyn.step_up_applied)
        self.assertEqual(dyn.current_coupon_rate, Decimal("0.07000000"))

        # Cenário 1: Meta descumprida (medido 3.80 > meta 3.20)
        status = self.engine.evaluate_spt(
            asset_code="SLB-TEST-DYN",
            spt_id="SPT-WATER-2026",
            measured_value=Decimal("3.80"),
            evaluation_date=date(2027, 2, 15),
            auditor_name="KPMG Auditores",
            auditor_report_ref="LAUDO-KPMG-2027"
        )
        self.assertEqual(status, SptStatus.MISSED)
        self.assertTrue(dyn.step_up_applied)
        # Taxa ativa = 7.00% + 0.30% = 7.30% a.a.
        self.assertEqual(dyn.current_coupon_rate, Decimal("0.07300000"))

        # Cenário 2: Reavaliação corretiva com cumprimento (medido 3.10 <= meta 3.20)
        status2 = self.engine.evaluate_spt(
            asset_code="SLB-TEST-DYN",
            spt_id="SPT-WATER-2026",
            measured_value=Decimal("3.10"),
            evaluation_date=date(2027, 2, 20),
            auditor_name="KPMG Auditores",
            auditor_report_ref="LAUDO-KPMG-2027-REV1"
        )
        self.assertEqual(status2, SptStatus.ACHIEVED)
        self.assertFalse(dyn.step_up_applied)
        self.assertEqual(dyn.current_coupon_rate, Decimal("0.07000000"))

    def test_overdue_reporting_deadline_triggers_automatic_step_up(self) -> None:
        """Verifica que expirar o prazo de auditoria sem laudo aciona automaticamente o Step-Up."""
        self.engine.register_slb(
            asset_code="SLB-AUDIT-TEST",
            isin="BRAUDIT00019",
            issuer_name="Auditoria Pendente S.A.",
            issuer_cnpj="66.777.888/0001-99",
            issue_date=date(2024, 1, 1),
            maturity_date=date(2029, 1, 1),
            nominal_unit_value=Decimal("1000.00"),
            issued_quantity=Decimal("10000"),
            base_coupon_rate=Decimal("0.06000000"),
            coupon_benchmark=CouponBenchmark.IPCA,
            payment_frequency=PaymentFrequency.ANNUAL
        )

        kpi = KpiDefinition(
            kpi_id="KPI-DIV-01",
            name="% Mulheres em Cargos de Liderança",
            unit_of_measure="%",
            baseline_year=2023,
            baseline_value=Decimal("20.00"),
            methodology="Índice Teva Mulheres na Liderança",
            direction=KpiDirection.INCREASE
        )
        self.engine.add_kpi_to_slb("SLB-AUDIT-TEST", kpi)

        spt = SptTarget(
            spt_id="SPT-DIV-2025",
            kpi_id="KPI-DIV-01",
            target_year=2025,
            observation_date=date(2025, 12, 31),
            verification_deadline=date(2026, 4, 30),
            target_value=Decimal("40.00"),
            step_up_spread_bps=Decimal("25.0")
        )
        self.engine.add_spt_target("SLB-AUDIT-TEST", spt)

        test_em = self.engine.get_emission("SLB-AUDIT-TEST")
        self.assertFalse(test_em.step_up_applied)

        # Avaliação na data 2026-05-01 (posterior a 2026-04-30 sem entrega do laudo)
        triggered = self.engine.evaluate_reporting_deadline("SLB-AUDIT-TEST", date(2026, 5, 1))
        self.assertIn("SPT-DIV-2025", triggered)
        self.assertTrue(test_em.step_up_applied)
        self.assertEqual(spt.status, SptStatus.DEFAULTED_REPORTING)
        self.assertEqual(test_em.current_coupon_rate, Decimal("0.06250000"))

    # -----------------------------------------------------------------------
    # 4. Cálculo Financeiro de Fluxo de Cupom e Step-Up
    # -----------------------------------------------------------------------

    def test_coupon_cashflow_without_step_up(self) -> None:
        """Calcula o fluxo de cupom para título sem acionamento de Step-Up."""
        cashflow = self.engine.calculate_coupon_cashflow(
            asset_code="SLB-PULP-01",
            start_date=date(2026, 3, 15),
            end_date=date(2026, 9, 15),
            outstanding_principal=Decimal("200000000.00")
        )

        self.assertEqual(cashflow.days, 184)
        self.assertEqual(cashflow.base_rate_pct, Decimal("5.7500"))
        self.assertEqual(cashflow.step_up_spread_pct, Decimal("0.0000"))
        self.assertEqual(cashflow.effective_rate_pct, Decimal("5.7500"))
        self.assertEqual(cashflow.step_up_penalty_amount, Decimal("0.00"))
        self.assertGreater(cashflow.base_interest_amount, Decimal("0.00"))
        self.assertEqual(cashflow.total_interest_payable, cashflow.base_interest_amount)

    def test_coupon_cashflow_with_step_up_penalty(self) -> None:
        """Calcula o fluxo de cupom comprovando a sobretaxa gerada pelo Step-Up."""
        cashflow = self.engine.calculate_coupon_cashflow(
            asset_code="SLB-STEEL-02",
            start_date=date(2026, 1, 10),
            end_date=date(2026, 7, 10),
            outstanding_principal=Decimal("300000000.00")
        )

        self.assertEqual(cashflow.days, 181)
        self.assertEqual(cashflow.base_rate_pct, Decimal("6.0000"))
        self.assertEqual(cashflow.step_up_spread_pct, Decimal("0.5000"))
        self.assertEqual(cashflow.effective_rate_pct, Decimal("6.5000"))

        # Base: 300M * 6.00% * (181/360) = R$ 9.050.000,00
        # Step-up penalty: 300M * 0.50% * (181/360) = R$ 754.166,67
        self.assertGreater(cashflow.step_up_penalty_amount, Decimal("0.00"))
        self.assertEqual(
            cashflow.total_interest_payable,
            cashflow.base_interest_amount + cashflow.step_up_penalty_amount
        )

    # -----------------------------------------------------------------------
    # 5. Relatórios de Conformidade ESG
    # -----------------------------------------------------------------------

    def test_allocation_report_generation(self) -> None:
        """Gera e valida a integridade do Relatório de Alocação de Green Bond."""
        report = self.engine.generate_allocation_report("DEB-SOLAR-01", date(2026, 11, 4))
        self.assertIsInstance(report, AllocationReport)
        self.assertEqual(report.asset_code, "DEB-SOLAR-01")
        self.assertEqual(report.net_proceeds, Decimal("148500000.00"))
        self.assertEqual(report.allocated_proceeds, Decimal("148500000.00"))
        self.assertEqual(report.unallocated_proceeds, Decimal("0.00"))
        self.assertEqual(report.allocation_ratio_pct, Decimal("100.0000"))
        self.assertTrue(report.is_fully_allocated)
        self.assertEqual(report.status, "TOTALMENTE_ALOCADO")

        # Verifica discriminação por setor
        self.assertIn("RENEWABLE_ENERGY", report.sector_breakdown)
        self.assertIn("CLEAN_TRANSPORTATION", report.sector_breakdown)
        self.assertEqual(len(report.projects_summary), 3)

    def test_impact_report_generation(self) -> None:
        """Gera e valida o Relatório de Impacto Ambiental com métricas consolidadas."""
        report = self.engine.generate_impact_report("DEB-SOLAR-01", date(2026, 11, 4))
        self.assertIsInstance(report, ImpactReport)
        self.assertEqual(report.asset_code, "DEB-SOLAR-01")

        # Total de CO2 evitado = 54.000 + 42.000 = 96.000 tCO2e/ano
        total_co2 = report.metrics_aggregated.get("tCO2e_avoided_per_year")
        self.assertEqual(total_co2, Decimal("96000.000"))

        # Eficiência de carbono: 96.000 / 148.5 M = ~646.465 tCO2e/M BRL
        self.assertGreater(report.carbon_efficiency_tco2e_per_m_brl, Decimal("600.000"))

    def test_slb_progress_report_generation(self) -> None:
        """Gera e valida o Relatório de Progresso de Metas de SLB."""
        report = self.engine.generate_slb_progress_report("SLB-PULP-01", date(2026, 11, 4))
        self.assertIsInstance(report, SlbProgressReport)
        self.assertEqual(report.overall_compliance, "ALL_TARGETS_ACHIEVED")
        self.assertFalse(report.step_up_status["step_up_applied"])

        report_steel = self.engine.generate_slb_progress_report("SLB-STEEL-02", date(2026, 11, 4))
        self.assertEqual(report_steel.overall_compliance, "TARGET_MISSED_STEP_UP_ACTIVE")
        self.assertTrue(report_steel.step_up_status["step_up_applied"])

    def test_b3_esg_certification_dossier(self) -> None:
        """Emite o Dossiê de Certificação NoMe com hash SHA-256 e selo ativo."""
        dossier = self.engine.generate_b3_esg_certification_dossier("DEB-SOLAR-01")
        self.assertIsInstance(dossier, B3EsgCertificationDossier)
        self.assertEqual(dossier.asset_code, "DEB-SOLAR-01")
        self.assertTrue(dossier.seal_active)
        self.assertEqual(dossier.certification_status, EsgCertificationStatus.CERTIFIED_ACTIVE)
        self.assertIsNotNone(dossier.spo_summary)
        self.assertEqual(len(dossier.audit_trail_hash), 64)  # SHA-256 hex length

    # -----------------------------------------------------------------------
    # 6. Validação e Verificação Formal de Invariantes (Proof Laws)
    # -----------------------------------------------------------------------

    def test_all_formal_invariants_pass_on_fixture(self) -> None:
        """Verifica formalmente todas as leis de conservação em todas as emissões cadastradas."""
        for code in self.engine.emissions:
            passed = self.engine.verify_invariants(code)
            self.assertTrue(passed, f"Falha formal de invariante na emissão {code}")

    def test_invariant_detects_broken_proceeds_conservation(self) -> None:
        """Verifica que o validador formal detecta quebra de conservação de recursos."""
        solar = self.engine.get_emission("DEB-SOLAR-01")
        # Simula adulteração ilegal de recursos não alocados
        original_net = solar.net_proceeds
        solar.issuance_costs += Decimal("5000000.00")  # Altera Net_Proceeds sem ajustar projetos
        with self.assertRaises(EsgConservationError):
            self.engine.verify_invariants("DEB-SOLAR-01")
        solar.issuance_costs -= Decimal("5000000.00")  # Restaura

    def test_invariant_detects_inconsistent_step_up_state(self) -> None:
        """Verifica que o validador formal detecta inconsistência entre metas e taxa de cupom."""
        steel = self.engine.get_emission("SLB-STEEL-02")
        # Força desativação ilegal do flag de Step-Up mesmo com meta MISSED
        steel.step_up_applied = False
        with self.assertRaises(EsgConservationError):
            self.engine.verify_invariants("SLB-STEEL-02")
        steel.step_up_applied = True  # Restaura

    # -----------------------------------------------------------------------
    # 7. Serialização e Persistência JSON Determinística
    # -----------------------------------------------------------------------

    def test_save_and_reload_json_roundtrip(self) -> None:
        """Gera arquivo temporário JSON, salva e recarrega verificando identidade total."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            self.engine.save_to_json(tmp_path)
            reloaded_engine = EsgGreenBondsEngine.load_from_json(tmp_path)

            self.assertEqual(len(reloaded_engine.emissions), len(self.engine.emissions))
            for code, em in self.engine.emissions.items():
                self.assertIn(code, reloaded_engine.emissions)
                reloaded_em = reloaded_engine.get_emission(code)
                self.assertEqual(reloaded_em.net_proceeds, em.net_proceeds)
                self.assertEqual(reloaded_em.current_coupon_rate, em.current_coupon_rate)
                self.assertEqual(reloaded_em.step_up_applied, em.step_up_applied)
                self.assertEqual(len(reloaded_em.projects), len(em.projects))
                self.assertEqual(len(reloaded_em.spts), len(em.spts))
                # Verifica invariantes no reloaded
                self.assertTrue(reloaded_engine.verify_invariants(code))
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
