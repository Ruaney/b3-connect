"""Testes unitários e verificação formal de invariantes da CPR Verde e PSA B3 (FL-082).

Valida conformidade estrita com:
- Lei nº 8.929/1994 (Cédula de Produto Rural).
- Lei nº 13.986/2020 (Nova Lei do Agro — Escrituração B3).
- Decreto nº 10.828/2021 (Regulamentação da CPR Verde).
- Lei nº 14.119/2021 (Política Nacional de Pagamento por Serviços Ambientais).
"""

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.green_cpr_environmental_services import (
    Biome,
    CARComplianceError,
    CARProperty,
    CARStatus,
    CPRVerdeCategory,
    CPRVerdeFinancialType,
    CPRVerdeStatus,
    CertificationInvalidError,
    EnvironmentalMetrics,
    EnvironmentalServiceType,
    GreenCPR,
    GreenCPREnvironmentalServicesEngine,
    GreenCPRError,
    GreenCPRInvariantViolationError,
    GreenCPRValidationError,
    PreservedAreaExceededError,
    SettlementConservationError,
    SettlementExecutionResult,
    ThirdPartyCertification,
    round_cents,
    round_ha,
    round_metric,
    round_rate,
    to_decimal,
)


class TestGreenCPREnvironmentalServices(unittest.TestCase):
    """Bateria de testes unitários e de integridade matemática do motor de CPR Verde e PSA B3."""

    def setUp(self) -> None:
        self.fixture_path = (
            Path(__file__).resolve().parent.parent / "fixtures" / "green_cpr_environmental_services.json"
        )
        self.engine = GreenCPREnvironmentalServicesEngine.load_from_fixture(self.fixture_path)
        self.ref_date = date(2026, 11, 5)

    def test_fixture_loading_and_invariants(self) -> None:
        """Garante que a fixture carregue com integridade e todas as invariantes formais passem."""
        self.assertEqual(len(self.engine.car_registry), 5)
        self.assertEqual(len(self.engine.cprs), 3)
        self.assertEqual(len(self.engine.settlements), 1)

        inv_report = self.engine.verify_invariants()
        self.assertTrue(inv_report["INV-CPR-001_PRESERVED_AREA_UNICITY"])
        self.assertTrue(inv_report["INV-CPR-002_FINANCIAL_CONSERVATION"])
        self.assertTrue(inv_report["INV-CPR-003_CAR_COMPLIANCE"])
        self.assertTrue(inv_report["INV-CPR-004_METRIC_INTEGRITY"])
        self.assertTrue(inv_report["INV-CPR-005_CERTIFICATION_VALIDITY"])

        # Deve executar assert_invariants sem levantar exceção
        self.engine.assert_invariants()

        # Testa serialização bidirecional to_dict / from_dict
        state_dict = self.engine.to_dict()
        rebuilt_engine = GreenCPREnvironmentalServicesEngine.from_dict(state_dict)
        self.assertEqual(len(rebuilt_engine.car_registry), 5)
        self.assertEqual(len(rebuilt_engine.cprs), 3)
        self.assertEqual(len(rebuilt_engine.settlements), 1)
        rebuilt_engine.assert_invariants()

    def test_car_property_validation(self) -> None:
        """Valida regras cadastrais, formatação SICAR e limites territoriais do imóvel."""
        car = CARProperty(
            car_number="GO-5208707-1234567890ABCDEF1234567890ABCDEF",
            property_name="FAZENDA BOA ESPERANÇA",
            municipality="Goiânia",
            state="GO",
            biome=Biome.CERRADO,
            total_property_area_ha=Decimal("1000.0000"),
            legal_reserve_area_ha=Decimal("200.0000"),
            app_area_ha=Decimal("80.0000"),
            surplus_native_veg_ha=Decimal("120.0000"),
        )
        self.assertEqual(car.total_preserved_area_ha, Decimal("400.0000"))
        compliant, issues = car.validate_compliance()
        self.assertTrue(compliant)
        self.assertEqual(len(issues), 0)

        # Código SICAR inválido
        with self.assertRaises(GreenCPRValidationError):
            CARProperty(
                car_number="INVALID-CAR-CODE",
                property_name="TESTE",
                municipality="Sinop",
                state="MT",
                biome=Biome.AMAZONIA,
                total_property_area_ha=Decimal("500.0000"),
                legal_reserve_area_ha=Decimal("100.0000"),
                app_area_ha=Decimal("50.0000"),
            )

        # UF divergente entre código CAR e estado
        with self.assertRaises(GreenCPRValidationError):
            CARProperty(
                car_number="SP-3550308-1234567890ABCDEF1234567890ABCDEF",
                property_name="TESTE",
                municipality="Cuiabá",
                state="MT",
                biome=Biome.CERRADO,
                total_property_area_ha=Decimal("500.0000"),
                legal_reserve_area_ha=Decimal("100.0000"),
                app_area_ha=Decimal("50.0000"),
            )

        # Área preservada excedendo área total da fazenda
        with self.assertRaises(GreenCPRValidationError):
            CARProperty(
                car_number="MT-5107909-1234567890ABCDEF1234567890ABCDEF",
                property_name="TESTE",
                municipality="Sinop",
                state="MT",
                biome=Biome.CERRADO,
                total_property_area_ha=Decimal("100.0000"),
                legal_reserve_area_ha=Decimal("80.0000"),
                app_area_ha=Decimal("50.0000"),
            )

    def test_car_compliance_rejections(self) -> None:
        """Verifica que imóveis irregulares (embargos, suspensões ou desmatamento) sejam rejeitados."""
        # Imóvel com embargo IBAMA
        car_embargado = self.engine.get_car("MT-5107909-00112233445566778899AABBCCDDEEFF")
        comp, issues = car_embargado.validate_compliance()
        self.assertFalse(comp)
        self.assertTrue(any("embargo" in i.lower() for i in issues))

        # Imóvel com CAR suspenso
        car_suspenso = self.engine.get_car("GO-5208707-AABBCCDDEEFF00112233445566778899")
        comp, issues = car_suspenso.validate_compliance()
        self.assertFalse(comp)
        self.assertTrue(any("status" in i.lower() for i in issues))

        # Tentar registrar CPR Verde em imóvel embargado deve levantar CARComplianceError
        cpr_invalid = GreenCPR(
            cpr_id="CPRV-FAIL-001",
            issuer_tax_id="12345678000199",
            issuer_name="PRODUTOR EMBARGADO",
            buyer_tax_id="98765432000110",
            buyer_name="INVESTIDOR",
            car_number=car_embargado.car_number,
            category=CPRVerdeCategory.CONSERVATION,
            service_type=EnvironmentalServiceType.CARBON_STOCK,
            committed_area_ha=Decimal("500.0000"),
            contracted_units=Decimal("10000.0000"),
            unit_price_brl=Decimal("50.00"),
            issue_date=date(2026, 5, 1),
            maturity_date=date(2027, 5, 1),
            metrics=EnvironmentalMetrics.calculate_default_for_biome(
                Biome.AMAZONIA, EnvironmentalServiceType.CARBON_STOCK
            ),
            certification=ThirdPartyCertification(
                certifier_cnpj="33271505000140",
                certifier_name="AUDITORIA INDEPENDENTE",
                auditor_registration="ART-12345",
                technical_report_hash="a" * 64,
                methodology="IPCC",
                certification_date=date(2026, 4, 1),
                expiration_date=date(2028, 4, 1),
            ),
        )

        with self.assertRaises(CARComplianceError):
            self.engine.register_green_cpr(cpr_invalid)

    def test_environmental_metrics_and_biomes(self) -> None:
        """Testa geração de métricas científicas por bioma e cálculos estequiométricos de carbono."""
        for biome in Biome:
            metrics = EnvironmentalMetrics.calculate_default_for_biome(
                biome, EnvironmentalServiceType.CARBON_STOCK
            )
            self.assertGreater(metrics.biomass_density_ton_per_ha, Decimal("0.0000"))
            self.assertGreater(metrics.carbon_stock_tco2e_per_ha, Decimal("0.0000"))
            self.assertGreater(metrics.water_yield_m3_per_ha_year, Decimal("0.0000"))

            # Relação estequiométrica IPCC: Biomassa * 0.47 * (44/12)
            expected_c = metrics.biomass_density_ton_per_ha * Decimal("0.47")
            expected_co2e = round_metric(expected_c * (Decimal("44") / Decimal("12")))
            self.assertEqual(metrics.carbon_stock_tco2e_per_ha, expected_co2e)

            # Teste de cálculos totais para área de 100 ha
            area = Decimal("100.0000")
            self.assertEqual(metrics.total_biomass_tons(area), round_metric(metrics.biomass_density_ton_per_ha * area))
            self.assertEqual(metrics.total_carbon_stock_tco2e(area), round_metric(metrics.carbon_stock_tco2e_per_ha * area))
            self.assertEqual(metrics.total_water_yield_m3(area), round_metric(metrics.water_yield_m3_per_ha_year * area))

    def test_third_party_certification_validation(self) -> None:
        """Valida integridade do laudo de auditoria independente e hash SHA-256."""
        # CNPJ inválido
        with self.assertRaises(GreenCPRValidationError):
            ThirdPartyCertification(
                certifier_cnpj="123",
                certifier_name="CERTIFICADORA",
                auditor_registration="ART-001",
                technical_report_hash="a" * 64,
                methodology="ISO 14064",
                certification_date=date(2026, 1, 1),
                expiration_date=date(2027, 1, 1),
            )

        # Hash inválido (tamanho diferente de 64 hexadecimais)
        with self.assertRaises(GreenCPRValidationError):
            ThirdPartyCertification(
                certifier_cnpj="33271505000140",
                certifier_name="CERTIFICADORA",
                auditor_registration="ART-001",
                technical_report_hash="hash_invalido_curto",
                methodology="ISO 14064",
                certification_date=date(2026, 1, 1),
                expiration_date=date(2027, 1, 1),
            )

        # Data de expiração anterior à emissão
        with self.assertRaises(GreenCPRValidationError):
            ThirdPartyCertification(
                certifier_cnpj="33271505000140",
                certifier_name="CERTIFICADORA",
                auditor_registration="ART-001",
                technical_report_hash="f" * 64,
                methodology="ISO 14064",
                certification_date=date(2026, 5, 1),
                expiration_date=date(2026, 4, 1),
            )

        # Checagem de vigência
        cert = ThirdPartyCertification(
            certifier_cnpj="33271505000140",
            certifier_name="CERTIFICADORA",
            auditor_registration="ART-001",
            technical_report_hash="f" * 64,
            methodology="ISO 14064",
            certification_date=date(2026, 1, 1),
            expiration_date=date(2027, 1, 1),
        )
        self.assertTrue(cert.is_valid_as_of(date(2026, 6, 1)))
        self.assertFalse(cert.is_valid_as_of(date(2025, 12, 31)))
        self.assertFalse(cert.is_valid_as_of(date(2027, 1, 2)))

    def test_area_unicity_invariant_and_double_counting_prevention(self) -> None:
        """Testa rigorosamente a invariante: Área_Comprometida <= Área_Preservada_Total_CAR."""
        car_sp = self.engine.get_car("SP-3550308-F1E2D3C4B5A697887766554433221100")
        # car_sp possui 600 ha preservados. CPRV-2026-0002 já comprometeu 250 ha.
        self.assertEqual(car_sp.total_preserved_area_ha, Decimal("600.0000"))
        committed = self.engine.get_committed_area_for_car(car_sp.car_number)
        self.assertEqual(committed, Decimal("250.0000"))
        available = self.engine.get_available_preserved_area_for_car(car_sp.car_number)
        self.assertEqual(available, Decimal("350.0000"))

        # Tentativa de emitir nova CPR comprometendo 400 ha (250 + 400 = 650 > 600) -> DEVE FALHAR
        excess_cpr = GreenCPR(
            cpr_id="CPRV-EXCESS-001",
            issuer_tax_id="11223344000155",
            issuer_name="ASSOCIAÇÃO MANTIQUEIRA",
            buyer_tax_id="99887766000144",
            buyer_name="FUNDO DE CRÉDITO ESG",
            car_number=car_sp.car_number,
            category=CPRVerdeCategory.CONSERVATION,
            service_type=EnvironmentalServiceType.HYDROLOGICAL_PRESERVATION,
            committed_area_ha=Decimal("400.0000"),
            contracted_units=Decimal("1000.0000"),
            unit_price_brl=Decimal("100.00"),
            issue_date=date(2026, 5, 1),
            maturity_date=date(2027, 5, 1),
            metrics=EnvironmentalMetrics.calculate_default_for_biome(
                Biome.MATA_ATLANTICA, EnvironmentalServiceType.HYDROLOGICAL_PRESERVATION
            ),
            certification=ThirdPartyCertification(
                certifier_cnpj="02989617000109",
                certifier_name="IMAFLORA",
                auditor_registration="ART-SP-9988",
                technical_report_hash="b" * 64,
                methodology="Protocolo PSA",
                certification_date=date(2026, 4, 1),
                expiration_date=date(2028, 4, 1),
            ),
        )

        with self.assertRaises(PreservedAreaExceededError):
            self.engine.register_green_cpr(excess_cpr)

        # Emissão de nova CPR dentro do limite (ex.: 300 ha; total = 250 + 300 = 550 <= 600) -> DEVE PASSAR
        valid_second_cpr = GreenCPR(
            cpr_id="CPRV-VALID-002",
            issuer_tax_id="11223344000155",
            issuer_name="ASSOCIAÇÃO MANTIQUEIRA",
            buyer_tax_id="99887766000144",
            buyer_name="FUNDO DE CRÉDITO ESG",
            car_number=car_sp.car_number,
            category=CPRVerdeCategory.CONSERVATION,
            service_type=EnvironmentalServiceType.HYDROLOGICAL_PRESERVATION,
            committed_area_ha=Decimal("300.0000"),
            contracted_units=Decimal("1000.0000"),
            unit_price_brl=Decimal("100.00"),
            issue_date=date(2026, 5, 1),
            maturity_date=date(2027, 5, 1),
            metrics=EnvironmentalMetrics.calculate_default_for_biome(
                Biome.MATA_ATLANTICA, EnvironmentalServiceType.HYDROLOGICAL_PRESERVATION
            ),
            certification=ThirdPartyCertification(
                certifier_cnpj="02989617000109",
                certifier_name="IMAFLORA",
                auditor_registration="ART-SP-9988",
                technical_report_hash="b" * 64,
                methodology="Protocolo PSA",
                certification_date=date(2026, 4, 1),
                expiration_date=date(2028, 4, 1),
            ),
        )
        registered = self.engine.register_green_cpr(valid_second_cpr)
        self.assertEqual(registered.status, CPRVerdeStatus.REGISTERED)
        self.assertTrue(registered.b3_registration_id.startswith("B3-NOME-CPRV-"))
        self.assertEqual(self.engine.get_committed_area_for_car(car_sp.car_number), Decimal("550.0000"))
        self.assertEqual(self.engine.get_available_preserved_area_for_car(car_sp.car_number), Decimal("50.0000"))

        # Invariantes formais devem continuar 100% satisfeitas
        self.engine.assert_invariants()

    def test_financial_settlement_and_conservation_invariant(self) -> None:
        """Testa liquidação financeira determinística e invariante de conservação centavo a centavo."""
        # Liquida CPRV-2026-0002
        cpr2 = self.engine.get_cpr("CPRV-2026-0002")
        self.assertEqual(cpr2.status, CPRVerdeStatus.REGISTERED)
        self.assertEqual(cpr2.total_notional_brl, Decimal("1700000.00"))

        # Regra da certificação: rate = 2.0%, fixed = R$ 5.000,00
        # Taxa de certificação: 1.700.000 * 0,02 + 5.000 = 34.000 + 5.000 = 39.000,00
        # Remuneração do Produtor: 1.700.000 - 39.000 = 1.661.000,00
        settle_result = self.engine.execute_settlement(
            cpr_id="CPRV-2026-0002",
            settlement_date=date(2026, 8, 15),
        )

        self.assertEqual(settle_result.gross_buyer_disbursement, Decimal("1700000.00"))
        self.assertEqual(settle_result.certification_fee, Decimal("39000.00"))
        self.assertEqual(settle_result.producer_remuneration, Decimal("1661000.00"))

        # Lei de Conservação: Desembolso == Remuneração + Taxa
        self.assertEqual(
            settle_result.gross_buyer_disbursement,
            settle_result.producer_remuneration + settle_result.certification_fee,
        )
        self.assertEqual(cpr2.status, CPRVerdeStatus.SETTLED)
        self.engine.assert_invariants()

    def test_settlement_conservation_violation_error(self) -> None:
        """Valida que desbalanceamentos entre desembolso e alocação levantem SettlementConservationError."""
        # Tentativa de instanciar SettlementExecutionResult onde desembolso != produtor + certificadora
        with self.assertRaises(SettlementConservationError):
            SettlementExecutionResult(
                settlement_id="FAIL-SETTLE-001",
                cpr_id="CPRV-2026-0001",
                settlement_date=date(2026, 7, 1),
                gross_buyer_disbursement=Decimal("100000.00"),
                producer_remuneration=Decimal("95000.00"),
                certification_fee=Decimal("4999.99"),  # Falta 0.01 centavo!
            )

    def test_settlement_supervenient_embargo_blocking(self) -> None:
        """Garante que se uma propriedade for embargada antes da liquidação, a liquidação seja barrada."""
        cpr3 = self.engine.get_cpr("CPRV-2026-0003")
        car3 = self.engine.get_car(cpr3.car_number)

        # Simula fiscalização do IBAMA que descobre desmatamento e embarga o imóvel supervenientemente
        car3.has_ibama_embargo = True

        with self.assertRaises(CARComplianceError):
            self.engine.execute_settlement(
                cpr_id="CPRV-2026-0003",
                settlement_date=date(2026, 9, 1),
            )

        # CPR foi automaticamente bloqueada
        self.assertEqual(cpr3.status, CPRVerdeStatus.BLOCKED)

    def test_cpr_lifecycle_block_and_cancel(self) -> None:
        """Valida operações de bloqueio cautelar e cancelamento no balcão."""
        cpr = self.engine.get_cpr("CPRV-2026-0003")
        self.engine.block_cpr(cpr.cpr_id, "Ordem judicial da Vara Agrária")
        self.assertEqual(cpr.status, CPRVerdeStatus.BLOCKED)

        # Não é permitido liquidar CPR bloqueada
        with self.assertRaises(GreenCPRValidationError):
            self.engine.execute_settlement(cpr.cpr_id, date(2026, 9, 1))

        # Teste de cancelamento
        car = self.engine.get_car(cpr.car_number)
        new_cpr = GreenCPR(
            cpr_id="CPRV-TO-CANCEL",
            issuer_tax_id="77889900000122",
            issuer_name="COOPERATIVA",
            buyer_tax_id="44332211000188",
            buyer_name="INVESTIDOR",
            car_number=car.car_number,
            category=CPRVerdeCategory.CONSERVATION,
            service_type=EnvironmentalServiceType.CARBON_STOCK,
            committed_area_ha=Decimal("100.0000"),
            contracted_units=Decimal("100.0000"),
            unit_price_brl=Decimal("100.00"),
            issue_date=date(2026, 5, 1),
            maturity_date=date(2027, 5, 1),
            metrics=EnvironmentalMetrics.calculate_default_for_biome(
                Biome.AMAZONIA, EnvironmentalServiceType.CARBON_STOCK
            ),
            certification=ThirdPartyCertification(
                certifier_cnpj="43236712000190",
                certifier_name="BUREAU VERITAS",
                auditor_registration="ART-1122",
                technical_report_hash="c" * 64,
                methodology="VCS",
                certification_date=date(2026, 4, 1),
                expiration_date=date(2028, 4, 1),
            ),
        )
        self.engine.register_green_cpr(new_cpr)
        self.assertEqual(new_cpr.status, CPRVerdeStatus.REGISTERED)

        self.engine.cancel_cpr(new_cpr.cpr_id, "Distrato amigável entre as partes")
        self.assertEqual(new_cpr.status, CPRVerdeStatus.CANCELLED)

        # CPR cancelada libera a área preservada do imóvel
        area_committed = self.engine.get_committed_area_for_car(car.car_number)
        # new_cpr não deve constar na soma da área comprometida
        self.assertEqual(area_committed, Decimal("0.0000"))  # cpr3 está BLOCKED, new_cpr está CANCELLED

    def test_formal_invariants_failure_detection(self) -> None:
        """Garante que verify_invariants e assert_invariants detectem qualquer violação injetada."""
        # 1. Violação forçada de área preservada
        cpr1 = self.engine.get_cpr("CPRV-2026-0001")
        original_ha = cpr1.committed_area_ha
        try:
            cpr1.committed_area_ha = Decimal("999999.0000")
            inv = self.engine.verify_invariants()
            self.assertFalse(inv["INV-CPR-001_PRESERVED_AREA_UNICITY"])
            with self.assertRaises(GreenCPRInvariantViolationError):
                self.engine.assert_invariants()
        finally:
            cpr1.committed_area_ha = original_ha

        # 2. Violação forçada de métrica ambiental não-positiva
        try:
            cpr1.metrics.biomass_density_ton_per_ha = Decimal("-10.0000")
            inv = self.engine.verify_invariants()
            self.assertFalse(inv["INV-CPR-004_METRIC_INTEGRITY"])
            with self.assertRaises(GreenCPRInvariantViolationError):
                self.engine.assert_invariants()
        finally:
            cpr1.metrics.biomass_density_ton_per_ha = Decimal("110.0000")


if __name__ == "__main__":
    unittest.main()
