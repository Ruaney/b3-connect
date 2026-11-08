"""Suíte de Testes Unitários e Invariantes Formais — Certificados de Operações Estruturadas (COE) (FL-102).
==================================================================================================
Validação formal e quantitativa do motor de emissão, registro, decomposição estrutural,
marcação a mercado (MtM), avaliação de barreiras (autocall/knock-in/knock-out) e liquidação
de Certificados de Operações Estruturadas (COE) na B3:
  - Lei nº 12.249/2010 (arts. 39 a 44);
  - Resolução CMN nº 4.263/2013;
  - Resolução CVM nº 8/2020;
  - Tabela Regressiva de IRRF (Lei nº 11.033/2004).

Invariantes Formais Testadas:
  - LAW-108: Inviolabilidade do Piso de Capital Protegido (Payoff >= Principal).
  - LAW-108B: Delimitação Estrita pelo Teto de Ganho (Cap Invariant).
  - LAW-108C: Conservação Tributária Centavo a Centavo (Bruto == Líquido + IRRF).
  - LAW-108D: Conservação Patrimonial na Liquidação Física (Bruto == Ações + Resíduo).

100% Python Standard Library (unittest, decimal, json, pathlib, tempfile).
"""

from decimal import Decimal
import json
import math
from pathlib import Path
import tempfile
import unittest

from src.invariants import InvariantViolationError, assert_coe_protected_capital_floor
from src.structured_operations_coe import (
    BarrierType,
    CoeCapitalProtectionType,
    CoeContractSpec,
    CoeMarkToMarket,
    CoePayoffStructure,
    CoeQuantitativePricer,
    CoeSettlementError,
    CoeSettlementReceipt,
    CoeStatus,
    CoeTaxBreakdown,
    CoeValidationError,
    DieSummary,
    EmbeddedOptionLegSpec,
    FixedIncomeLegSpec,
    ObservationDateEvent,
    PhysicalDeliveryAllocation,
    SettlementType,
    StructuredOperationsCoeEngine,
    UnderlyingAssetType,
    round_cents,
    round_four,
)


class TestCoeRegistrationAndValidation(unittest.TestCase):
    """Testes de emissão, registro e validação de conformidade legal (Lei 12.249/2010 e CVM 8)."""

    def setUp(self) -> None:
        self.engine = StructuredOperationsCoeEngine()
        self.rf_leg = FixedIncomeLegSpec(
            discount_rate_annual=Decimal("0.1250"),
            day_count_convention="DU/252",
            business_days=252,
        )
        self.opt_leg = EmbeddedOptionLegSpec(
            structure_type=CoePayoffStructure.BULL_CALL_SPREAD,
            lower_strike=Decimal("130000.00"),
            upper_strike=Decimal("156000.00"),
            participation_rate_pct=Decimal("100.00"),
            maximum_cap_gain_pct=Decimal("20.00"),
        )
        self.die = DieSummary(
            target_investor_profile="GERAL",
            issuer_rating="brAAA",
            scenario_stress_return_pct=Decimal("0.00"),
            scenario_pessimistic_return_pct=Decimal("0.00"),
            scenario_neutral_return_pct=Decimal("8.50"),
            scenario_optimistic_return_pct=Decimal("20.00"),
            max_loss_pct=Decimal("0.00"),
            fiduciary_agent="Pentágono DTVM",
        )

    def test_successful_protected_capital_registration(self) -> None:
        """Garante registro com sucesso de COE com Capital 100% Protegido."""
        spec = CoeContractSpec(
            contract_id="COE-TEST-001",
            issuer_institution_id="BANCO-001",
            issuer_name="Banco Safra",
            investor_id="INV-001",
            investor_account="CC-1010",
            underlying_asset=UnderlyingAssetType.IBOVESPA,
            underlying_symbol="IBOV",
            protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
            payoff_structure=CoePayoffStructure.BULL_CALL_SPREAD,
            settlement_type=SettlementType.FINANCIAL,
            issue_date="2026-01-10",
            maturity_date="2027-01-10",
            nominal_unit_value=Decimal("1000.00"),
            quantity=100,
            initial_underlying_level=Decimal("130000.00"),
            protection_floor_pct=Decimal("100.00"),
            fixed_income_leg=self.rf_leg,
            derivative_leg=self.opt_leg,
            die_summary=self.die,
        )
        self.engine.register_coe(spec)
        retrieved = self.engine.get_contract("COE-TEST-001")
        self.assertEqual(retrieved.contract_id, "COE-TEST-001")
        self.assertEqual(retrieved.total_initial_notional, Decimal("100000.00"))
        self.assertEqual(retrieved.total_guaranteed_principal, Decimal("100000.00"))
        self.assertIsNotNone(retrieved.fixed_income_leg.present_value)
        self.assertIsNotNone(retrieved.fixed_income_leg.derivatives_budget)

    def test_rejection_protected_capital_with_floor_below_100_percent(self) -> None:
        """Rejeita COE classificado como Capital Protegido com piso inferior a 100%."""
        spec = CoeContractSpec(
            contract_id="COE-INVALID-FLOOR",
            issuer_institution_id="BANCO-001",
            issuer_name="Banco Safra",
            investor_id="INV-001",
            investor_account="CC-1010",
            underlying_asset=UnderlyingAssetType.IBOVESPA,
            underlying_symbol="IBOV",
            protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
            payoff_structure=CoePayoffStructure.BULL_CALL_SPREAD,
            settlement_type=SettlementType.FINANCIAL,
            issue_date="2026-01-10",
            maturity_date="2027-01-10",
            nominal_unit_value=Decimal("1000.00"),
            quantity=100,
            initial_underlying_level=Decimal("130000.00"),
            protection_floor_pct=Decimal("95.00"),  # Ilegal para Capital Protegido!
            fixed_income_leg=self.rf_leg,
            derivative_leg=self.opt_leg,
            die_summary=self.die,
        )
        with self.assertRaises(CoeValidationError):
            self.engine.register_coe(spec)

    def test_at_risk_capital_registration(self) -> None:
        """Permite registro de COE com Valor Nominal em Risco (piso entre 0% e < 100%)."""
        die_risk = DieSummary(
            target_investor_profile="QUALIFICADO",
            issuer_rating="brAAA",
            scenario_stress_return_pct=Decimal("-20.00"),
            scenario_pessimistic_return_pct=Decimal("-10.00"),
            scenario_neutral_return_pct=Decimal("10.00"),
            scenario_optimistic_return_pct=Decimal("30.00"),
            max_loss_pct=Decimal("20.00"),
            fiduciary_agent="Pentágono DTVM",
        )
        spec = CoeContractSpec(
            contract_id="COE-RISK-001",
            issuer_institution_id="BANCO-001",
            issuer_name="Banco Safra",
            investor_id="INV-001",
            investor_account="CC-1010",
            underlying_asset=UnderlyingAssetType.VALE3,
            underlying_symbol="VALE3",
            protection_type=CoeCapitalProtectionType.AT_RISK_CAPITAL,
            payoff_structure=CoePayoffStructure.PARTICIPATION_WITH_CAP,
            settlement_type=SettlementType.FINANCIAL,
            issue_date="2026-01-10",
            maturity_date="2027-01-10",
            nominal_unit_value=Decimal("1000.00"),
            quantity=50,
            initial_underlying_level=Decimal("60.00"),
            protection_floor_pct=Decimal("80.00"),  # Perda máxima de 20%
            fixed_income_leg=self.rf_leg,
            derivative_leg=self.opt_leg,
            die_summary=die_risk,
        )
        self.engine.register_coe(spec)
        self.assertEqual(spec.total_guaranteed_principal, Decimal("40000.00"))  # 80% de 50.000

    def test_rejection_duplicate_registration(self) -> None:
        """Rejeita duplicidade de registro de contrato com mesmo ID."""
        spec = CoeContractSpec(
            contract_id="COE-DUP-001",
            issuer_institution_id="BANCO-001",
            issuer_name="Banco Safra",
            investor_id="INV-001",
            investor_account="CC-1010",
            underlying_asset=UnderlyingAssetType.IBOVESPA,
            underlying_symbol="IBOV",
            protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
            payoff_structure=CoePayoffStructure.BULL_CALL_SPREAD,
            settlement_type=SettlementType.FINANCIAL,
            issue_date="2026-01-10",
            maturity_date="2027-01-10",
            nominal_unit_value=Decimal("1000.00"),
            quantity=10,
            initial_underlying_level=Decimal("130000.00"),
            protection_floor_pct=Decimal("100.00"),
            fixed_income_leg=self.rf_leg,
            derivative_leg=self.opt_leg,
            die_summary=self.die,
        )
        self.engine.register_coe(spec)
        with self.assertRaises(CoeValidationError):
            self.engine.register_coe(spec)


class TestEmbeddedOptionStructuresAndPayoffs(unittest.TestCase):
    """Testes de apuração de payoff para todas as estruturas de derivativos embutidos."""

    def setUp(self) -> None:
        self.engine = StructuredOperationsCoeEngine()
        self.rf_leg = FixedIncomeLegSpec(
            discount_rate_annual=Decimal("0.1200"),
            day_count_convention="DU/252",
            business_days=252,
        )

    def test_bull_call_spread_payoff_moderate_gain(self) -> None:
        """Call Spread com ganho moderado dentro do intervalo [K1, K2]."""
        spec = CoeContractSpec(
            contract_id="COE-BULL-01",
            issuer_institution_id="BANCO-001",
            issuer_name="Banco Itaú",
            investor_id="INV-001",
            investor_account="CC-1010",
            underlying_asset=UnderlyingAssetType.IBOVESPA,
            underlying_symbol="IBOV",
            protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
            payoff_structure=CoePayoffStructure.BULL_CALL_SPREAD,
            settlement_type=SettlementType.FINANCIAL,
            issue_date="2026-01-01",
            maturity_date="2027-01-01",
            nominal_unit_value=Decimal("1000.00"),
            quantity=100,
            initial_underlying_level=Decimal("100.00"),
            protection_floor_pct=Decimal("100.00"),
            fixed_income_leg=self.rf_leg,
            derivative_leg=EmbeddedOptionLegSpec(
                structure_type=CoePayoffStructure.BULL_CALL_SPREAD,
                lower_strike=Decimal("100.00"),
                upper_strike=Decimal("120.00"),
                participation_rate_pct=Decimal("100.00"),
                maximum_cap_gain_pct=Decimal("20.00"),
            ),
        )
        self.engine.register_coe(spec)
        # Alta de 10% (final = 110.00)
        receipt = self.engine.settle_at_maturity("COE-BULL-01", Decimal("110.00"), "2027-01-01", 365)
        self.assertEqual(receipt.underlying_performance_pct, Decimal("10.00"))
        self.assertEqual(receipt.gross_unit_payoff, Decimal("1100.0000"))
        self.assertEqual(receipt.total_gross_settlement, Decimal("110000.00"))
        # IRRF: 365 dias corridos -> alíquota 17.5%
        self.assertEqual(receipt.tax_breakdown.tax_rate, Decimal("0.175"))
        self.assertEqual(receipt.tax_breakdown.gross_yield, Decimal("10000.00"))
        self.assertEqual(receipt.tax_breakdown.irrf_amount, Decimal("1750.00"))
        self.assertEqual(receipt.total_net_settlement, Decimal("108250.00"))

    def test_bull_call_spread_payoff_above_cap(self) -> None:
        """Call Spread com alta extrema além de K2 respeitando o teto (Cap)."""
        spec = CoeContractSpec(
            contract_id="COE-BULL-CAP",
            issuer_institution_id="BANCO-001",
            issuer_name="Banco Itaú",
            investor_id="INV-001",
            investor_account="CC-1010",
            underlying_asset=UnderlyingAssetType.IBOVESPA,
            underlying_symbol="IBOV",
            protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
            payoff_structure=CoePayoffStructure.BULL_CALL_SPREAD,
            settlement_type=SettlementType.FINANCIAL,
            issue_date="2026-01-01",
            maturity_date="2027-01-01",
            nominal_unit_value=Decimal("1000.00"),
            quantity=100,
            initial_underlying_level=Decimal("100.00"),
            protection_floor_pct=Decimal("100.00"),
            fixed_income_leg=self.rf_leg,
            derivative_leg=EmbeddedOptionLegSpec(
                structure_type=CoePayoffStructure.BULL_CALL_SPREAD,
                lower_strike=Decimal("100.00"),
                upper_strike=Decimal("120.00"),
                participation_rate_pct=Decimal("100.00"),
                maximum_cap_gain_pct=Decimal("20.00"),
            ),
        )
        self.engine.register_coe(spec)
        # Alta de 50% (final = 150.00), mas teto é 20%
        receipt = self.engine.settle_at_maturity("COE-BULL-CAP", Decimal("150.00"), "2027-01-01", 365)
        self.assertEqual(receipt.gross_unit_payoff, Decimal("1200.0000"))
        self.assertEqual(receipt.total_gross_settlement, Decimal("120000.00"))

    def test_bull_call_spread_payoff_negative_underlying(self) -> None:
        """Call Spread com queda do subjacente garante 100% do capital (LAW-108)."""
        spec = CoeContractSpec(
            contract_id="COE-BULL-DOWN",
            issuer_institution_id="BANCO-001",
            issuer_name="Banco Itaú",
            investor_id="INV-001",
            investor_account="CC-1010",
            underlying_asset=UnderlyingAssetType.IBOVESPA,
            underlying_symbol="IBOV",
            protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
            payoff_structure=CoePayoffStructure.BULL_CALL_SPREAD,
            settlement_type=SettlementType.FINANCIAL,
            issue_date="2026-01-01",
            maturity_date="2027-01-01",
            nominal_unit_value=Decimal("1000.00"),
            quantity=100,
            initial_underlying_level=Decimal("100.00"),
            protection_floor_pct=Decimal("100.00"),
            fixed_income_leg=self.rf_leg,
            derivative_leg=EmbeddedOptionLegSpec(
                structure_type=CoePayoffStructure.BULL_CALL_SPREAD,
                lower_strike=Decimal("100.00"),
                upper_strike=Decimal("120.00"),
            ),
        )
        self.engine.register_coe(spec)
        # Queda de 30% (final = 70.00)
        receipt = self.engine.settle_at_maturity("COE-BULL-DOWN", Decimal("70.00"), "2027-01-01", 365)
        self.assertEqual(receipt.gross_unit_payoff, Decimal("1000.0000"))
        self.assertEqual(receipt.total_gross_settlement, Decimal("100000.00"))
        self.assertEqual(receipt.tax_breakdown.gross_yield, Decimal("0.00"))
        self.assertEqual(receipt.tax_breakdown.irrf_amount, Decimal("0.00"))
        self.assertEqual(receipt.total_net_settlement, Decimal("100000.00"))

    def test_digital_binary_payoff(self) -> None:
        """Opção digital binária: paga cupom fixo se subjacente >= strike, ou piso se menor."""
        spec = CoeContractSpec(
            contract_id="COE-DIG-01",
            issuer_institution_id="BANCO-002",
            issuer_name="Banco Bradesco",
            investor_id="INV-002",
            investor_account="CC-2020",
            underlying_asset=UnderlyingAssetType.SP500,
            underlying_symbol="SPX",
            protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
            payoff_structure=CoePayoffStructure.DIGITAL_BINARY,
            settlement_type=SettlementType.FINANCIAL,
            issue_date="2026-01-01",
            maturity_date="2027-01-01",
            nominal_unit_value=Decimal("1000.00"),
            quantity=50,
            initial_underlying_level=Decimal("5000.00"),
            protection_floor_pct=Decimal("100.00"),
            fixed_income_leg=self.rf_leg,
            derivative_leg=EmbeddedOptionLegSpec(
                structure_type=CoePayoffStructure.DIGITAL_BINARY,
                lower_strike=Decimal("5000.00"),
                digital_coupon_pct=Decimal("15.00"),
            ),
        )
        self.engine.register_coe(spec)
        # Cenário 1: acima do strike (5200.00) -> paga 15%
        receipt = self.engine.settle_at_maturity("COE-DIG-01", Decimal("5200.00"), "2027-01-01", 365)
        self.assertEqual(receipt.gross_unit_payoff, Decimal("1150.0000"))
        self.assertEqual(receipt.total_gross_settlement, Decimal("57500.00"))

    def test_participation_with_cap(self) -> None:
        """Participação alavancada com teto: 150% da alta limitada a 30%."""
        spec = CoeContractSpec(
            contract_id="COE-PART-01",
            issuer_institution_id="BANCO-003",
            issuer_name="Banco Santander",
            investor_id="INV-003",
            investor_account="CC-3030",
            underlying_asset=UnderlyingAssetType.PTAX_USD,
            underlying_symbol="USDBRL",
            protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
            payoff_structure=CoePayoffStructure.PARTICIPATION_WITH_CAP,
            settlement_type=SettlementType.FINANCIAL,
            issue_date="2026-01-01",
            maturity_date="2027-01-01",
            nominal_unit_value=Decimal("1000.00"),
            quantity=100,
            initial_underlying_level=Decimal("5.00"),
            protection_floor_pct=Decimal("100.00"),
            fixed_income_leg=self.rf_leg,
            derivative_leg=EmbeddedOptionLegSpec(
                structure_type=CoePayoffStructure.PARTICIPATION_WITH_CAP,
                participation_rate_pct=Decimal("150.00"),
                maximum_cap_gain_pct=Decimal("30.00"),
            ),
        )
        self.engine.register_coe(spec)
        # Alta de 10% no dólar -> ganho = 10% * 150% = 15%
        receipt = self.engine.settle_at_maturity("COE-PART-01", Decimal("5.50"), "2027-01-01", 365)
        self.assertEqual(receipt.gross_unit_payoff, Decimal("1150.0000"))

    def test_barrier_knock_out_activated(self) -> None:
        """Barreira Up-and-Out acionada durante a vida do produto paga rebate de 5%."""
        spec = CoeContractSpec(
            contract_id="COE-KO-01",
            issuer_institution_id="BANCO-004",
            issuer_name="Banco Safra",
            investor_id="INV-004",
            investor_account="CC-4040",
            underlying_asset=UnderlyingAssetType.BOVA11,
            underlying_symbol="BOVA11",
            protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
            payoff_structure=CoePayoffStructure.BARRIER_KNOCK_OUT,
            settlement_type=SettlementType.FINANCIAL,
            issue_date="2026-01-01",
            maturity_date="2027-01-01",
            nominal_unit_value=Decimal("1000.00"),
            quantity=100,
            initial_underlying_level=Decimal("100.00"),
            protection_floor_pct=Decimal("100.00"),
            fixed_income_leg=self.rf_leg,
            derivative_leg=EmbeddedOptionLegSpec(
                structure_type=CoePayoffStructure.BARRIER_KNOCK_OUT,
                barrier_level=Decimal("130.00"),
                barrier_type=BarrierType.UP_AND_OUT,
                rebate_pct=Decimal("5.00"),
            ),
        )
        self.engine.register_coe(spec)
        # Observação intermediária atinge a barreira (135.00)
        self.engine.record_underlying_observation("COE-KO-01", "2026-06-01", Decimal("135.00"), 150)
        self.assertTrue(spec.derivative_leg.barrier_hit)

        # No vencimento, como a barreira foi violada, paga capital + rebate de 5%
        receipt = self.engine.settle_at_maturity("COE-KO-01", Decimal("120.00"), "2027-01-01", 365)
        self.assertEqual(receipt.gross_unit_payoff, Decimal("1050.0000"))
        self.assertEqual(receipt.total_gross_settlement, Decimal("105000.00"))


class TestAutocallLifecycleAndObservations(unittest.TestCase):
    """Testes de ciclo de vida de COE Autocallable com encerramento antecipado."""

    def test_autocall_triggered_on_first_observation_date(self) -> None:
        """Dispara encerramento antecipado com cupom na 1ª data de observação."""
        engine = StructuredOperationsCoeEngine()
        obs_schedule = [
            ObservationDateEvent(1, "2026-06-15", Decimal("100.00"), Decimal("10.00")),
            ObservationDateEvent(2, "2026-12-15", Decimal("100.00"), Decimal("20.00")),
        ]
        spec = CoeContractSpec(
            contract_id="COE-AUTO-01",
            issuer_institution_id="BANCO-001",
            issuer_name="Banco BTG Pactual",
            investor_id="INV-001",
            investor_account="CC-1010",
            underlying_asset=UnderlyingAssetType.PETR4,
            underlying_symbol="PETR4",
            protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
            payoff_structure=CoePayoffStructure.AUTOCALLABLE,
            settlement_type=SettlementType.FINANCIAL,
            issue_date="2026-01-15",
            maturity_date="2026-12-15",
            nominal_unit_value=Decimal("1000.00"),
            quantity=100,
            initial_underlying_level=Decimal("40.00"),
            protection_floor_pct=Decimal("100.00"),
            fixed_income_leg=FixedIncomeLegSpec(Decimal("0.12")),
            derivative_leg=EmbeddedOptionLegSpec(CoePayoffStructure.AUTOCALLABLE),
            observation_schedule=obs_schedule,
        )
        engine.register_coe(spec)

        # Na 1ª data de observação, PETR4 sobe para 42.00 (>= 40.00) -> Autocall acionado
        receipt = engine.record_underlying_observation("COE-AUTO-01", "2026-06-15", Decimal("42.00"), 151)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.settlement_reason, "AUTOCALL")
        self.assertEqual(receipt.gross_unit_payoff, Decimal("1100.0000"))  # Principal + 10%
        self.assertEqual(receipt.total_gross_settlement, Decimal("110000.00"))
        self.assertEqual(spec.status, CoeStatus.EARLY_TERMINATED)
        # IRRF para 151 dias -> 22.5%
        self.assertEqual(receipt.tax_breakdown.tax_rate, Decimal("0.225"))
        self.assertEqual(receipt.tax_breakdown.irrf_amount, Decimal("2250.00"))
        self.assertEqual(receipt.total_net_settlement, Decimal("107750.00"))


class TestPhysicalSettlementAndTaxConservation(unittest.TestCase):
    """Testes de liquidação física e conservação tributária."""

    def test_physical_settlement_shares_delivery(self) -> None:
        """Liquidação física de COE com entrega de ações e fração financeira em caixa."""
        engine = StructuredOperationsCoeEngine()
        spec = CoeContractSpec(
            contract_id="COE-PHYS-01",
            issuer_institution_id="BANCO-001",
            issuer_name="Banco Itaú BBA",
            investor_id="INV-001",
            investor_account="CC-1010",
            underlying_asset=UnderlyingAssetType.VALE3,
            underlying_symbol="VALE3",
            protection_type=CoeCapitalProtectionType.AT_RISK_CAPITAL,
            payoff_structure=CoePayoffStructure.PARTICIPATION_WITH_CAP,
            settlement_type=SettlementType.PHYSICAL,
            issue_date="2026-01-01",
            maturity_date="2027-01-01",
            nominal_unit_value=Decimal("1000.00"),
            quantity=10,  # Notional: R$ 10.000,00
            initial_underlying_level=Decimal("50.00"),
            protection_floor_pct=Decimal("0.00"),
            fixed_income_leg=FixedIncomeLegSpec(Decimal("0.12")),
            derivative_leg=EmbeddedOptionLegSpec(CoePayoffStructure.PARTICIPATION_WITH_CAP),
        )
        engine.register_coe(spec)

        # Cenário adverso: VALE3 cai 20% para 40.00.
        # Payoff unitário = 800.00, total bruto = 8.000,00
        receipt = engine.settle_at_maturity("COE-PHYS-01", Decimal("40.00"), "2027-01-01", 365)
        self.assertEqual(receipt.total_gross_settlement, Decimal("8000.00"))
        self.assertIsNotNone(receipt.physical_delivery)
        # 8000.00 / 40.00 = 200 ações exatas
        self.assertEqual(receipt.physical_delivery.shares_quantity, 200)
        self.assertEqual(receipt.physical_delivery.shares_gross_value, Decimal("8000.00"))
        self.assertEqual(receipt.physical_delivery.fractional_cash_residual, Decimal("0.00"))

    def test_tax_schedule_tiers(self) -> None:
        """Validação estrita das 4 faixas de IRRF da Lei 11.033/2004."""
        engine = StructuredOperationsCoeEngine()
        for days, expected_rate in [
            (100, Decimal("0.225")),
            (200, Decimal("0.200")),
            (400, Decimal("0.175")),
            (800, Decimal("0.150")),
        ]:
            cid = f"COE-TAX-{days}"
            spec = CoeContractSpec(
                contract_id=cid,
                issuer_institution_id="BANCO-001",
                issuer_name="Banco Safra",
                investor_id="INV-001",
                investor_account="CC-1010",
                underlying_asset=UnderlyingAssetType.IBOVESPA,
                underlying_symbol="IBOV",
                protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
                payoff_structure=CoePayoffStructure.DIGITAL_BINARY,
                settlement_type=SettlementType.FINANCIAL,
                issue_date="2026-01-01",
                maturity_date="2027-01-01",
                nominal_unit_value=Decimal("1000.00"),
                quantity=10,
                initial_underlying_level=Decimal("100.00"),
                protection_floor_pct=Decimal("100.00"),
                fixed_income_leg=FixedIncomeLegSpec(Decimal("0.12")),
                derivative_leg=EmbeddedOptionLegSpec(
                    CoePayoffStructure.DIGITAL_BINARY,
                    lower_strike=Decimal("100.00"),
                    digital_coupon_pct=Decimal("10.00"),
                ),
            )
            engine.register_coe(spec)
            receipt = engine.settle_at_maturity(cid, Decimal("110.00"), "2027-01-01", days)
            self.assertEqual(receipt.tax_breakdown.tax_rate, expected_rate)
            self.assertEqual(receipt.total_gross_settlement, receipt.total_net_settlement + receipt.tax_breakdown.irrf_amount)


class TestMarkToMarketAndQuantitativePricing(unittest.TestCase):
    """Testes de precificação analítica Black-Scholes e Marcação a Mercado (MtM)."""

    def test_bs_call_and_put_closed_form(self) -> None:
        """Garante convergência e propriedades fundamentais da precificação Black-Scholes."""
        # Spot = 100, Strike = 100, r = 0.10, q = 0, vol = 0.20, T = 1.0
        call = CoeQuantitativePricer.black_scholes_call(100.0, 100.0, 0.10, 0.0, 0.20, 1.0)
        put = CoeQuantitativePricer.black_scholes_put(100.0, 100.0, 0.10, 0.0, 0.20, 1.0)
        # Paridade Put-Call: C - P = S - K * exp(-r*T) = 100 - 100 * exp(-0.10) = 100 * (1 - 0.904837) = 9.516
        self.assertAlmostEqual(call - put, 100.0 - 100.0 * math.exp(-0.10), places=3)
        self.assertGreater(call, 0.0)
        self.assertGreater(put, 0.0)

    def test_daily_mark_to_market_calculation(self) -> None:
        """Calcula MtM periódico do COE decompondo renda fixa e perna de opções."""
        engine = StructuredOperationsCoeEngine()
        spec = CoeContractSpec(
            contract_id="COE-MTM-01",
            issuer_institution_id="BANCO-001",
            issuer_name="Banco Bradesco",
            investor_id="INV-001",
            investor_account="CC-1010",
            underlying_asset=UnderlyingAssetType.IBOVESPA,
            underlying_symbol="IBOV",
            protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
            payoff_structure=CoePayoffStructure.BULL_CALL_SPREAD,
            settlement_type=SettlementType.FINANCIAL,
            issue_date="2026-01-01",
            maturity_date="2027-01-01",
            nominal_unit_value=Decimal("1000.00"),
            quantity=100,
            initial_underlying_level=Decimal("100.00"),
            protection_floor_pct=Decimal("100.00"),
            fixed_income_leg=FixedIncomeLegSpec(Decimal("0.1250")),
            derivative_leg=EmbeddedOptionLegSpec(
                structure_type=CoePayoffStructure.BULL_CALL_SPREAD,
                lower_strike=Decimal("100.00"),
                upper_strike=Decimal("120.00"),
            ),
        )
        engine.register_coe(spec)

        # MtM aos 126 dias úteis restantes
        mtm = engine.calculate_mark_to_market(
            contract_id="COE-MTM-01",
            valuation_date="2026-07-01",
            current_spot=Decimal("108.00"),
            days_to_maturity=126,
            volatility=Decimal("0.20"),
            risk_free_rate=Decimal("0.1250"),
        )
        self.assertGreater(mtm.fixed_income_pv_unit, Decimal("888.00"))
        self.assertGreater(mtm.derivative_pv_unit, Decimal("0.00"))
        self.assertGreater(mtm.unit_mtm, Decimal("900.00"))
        self.assertEqual(len(engine.mark_to_market_history["COE-MTM-01"]), 1)


class TestInvariantsAuditing(unittest.TestCase):
    """Testes rigorosos das leis de invariantes: LAW-108, LAW-108B, LAW-108C, LAW-108D."""

    def test_law_108_protected_capital_floor_assertion_success(self) -> None:
        """LAW-108 passa com sucesso quando Payoff >= Principal garantido."""
        assert_coe_protected_capital_floor(
            gross_settlement=Decimal("100000.00"),
            guaranteed_principal=Decimal("100000.00"),
            contract_id="COE-VALID-FLOOR",
            protection_floor_pct=Decimal("100.00"),
        )

    def test_law_108_protected_capital_floor_assertion_failure(self) -> None:
        """LAW-108 dispara InvariantViolationError se Payoff < Principal garantido."""
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_coe_protected_capital_floor(
                gross_settlement=Decimal("99999.99"),  # 1 centavo abaixo do piso
                guaranteed_principal=Decimal("100000.00"),
                contract_id="COE-BREACH-FLOOR",
                protection_floor_pct=Decimal("100.00"),
            )
        self.assertEqual(ctx.exception.law_code, "LAW-108")
        self.assertIn("Violação da Inviolabilidade do Piso de Capital Protegido", str(ctx.exception))

    def test_law_108c_tax_conservation_failure(self) -> None:
        """LAW-108C dispara InvariantViolationError se houver inconsistência fiscal."""
        tax = CoeTaxBreakdown(
            gross_yield=Decimal("1000.00"),
            holding_days=365,
            tax_rate=Decimal("0.175"),
            irrf_amount=Decimal("175.00"),
            net_yield=Decimal("825.00"),
        )
        corrupted_receipt = CoeSettlementReceipt(
            settlement_id="SETTLE-ERR-01",
            contract_id="COE-ERR-01",
            settlement_date="2027-01-01",
            settlement_type=SettlementType.FINANCIAL,
            settlement_reason="MATURITY",
            underlying_final_level=Decimal("110.00"),
            underlying_performance_pct=Decimal("10.00"),
            gross_unit_payoff=Decimal("1100.00"),
            total_gross_settlement=Decimal("11000.00"),
            total_principal_invested=Decimal("10000.00"),
            guaranteed_principal=Decimal("10000.00"),
            tax_breakdown=tax,
            total_net_settlement=Decimal("10820.00"),  # Deveria ser 10825.00! Desvio de R$ 5,00
        )
        with self.assertRaises(InvariantViolationError) as ctx:
            corrupted_receipt.verify_invariants(CoeCapitalProtectionType.PROTECTED_CAPITAL, Decimal("100.00"))
        self.assertEqual(ctx.exception.law_code, "LAW-108C")


class TestSerializationAndFixtures(unittest.TestCase):
    """Testes de serialização, carga de fixture JSON e verificação global do motor."""

    def test_roundtrip_to_dict_and_from_dict(self) -> None:
        """Garante roundtrip idempotente to_dict / from_dict em todas as entidades."""
        engine = StructuredOperationsCoeEngine()
        spec = CoeContractSpec(
            contract_id="COE-ROUNDTRIP-01",
            issuer_institution_id="BANCO-001",
            issuer_name="Banco Safra",
            investor_id="INV-001",
            investor_account="CC-1010",
            underlying_asset=UnderlyingAssetType.IBOVESPA,
            underlying_symbol="IBOV",
            protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
            payoff_structure=CoePayoffStructure.BULL_CALL_SPREAD,
            settlement_type=SettlementType.FINANCIAL,
            issue_date="2026-01-01",
            maturity_date="2027-01-01",
            nominal_unit_value=Decimal("1000.00"),
            quantity=10,
            initial_underlying_level=Decimal("100.00"),
            protection_floor_pct=Decimal("100.00"),
            fixed_income_leg=FixedIncomeLegSpec(Decimal("0.12")),
            derivative_leg=EmbeddedOptionLegSpec(
                structure_type=CoePayoffStructure.BULL_CALL_SPREAD,
                lower_strike=Decimal("100.00"),
                upper_strike=Decimal("120.00"),
            ),
        )
        engine.register_coe(spec)
        engine.calculate_mark_to_market("COE-ROUNDTRIP-01", "2026-06-01", Decimal("105.00"), 126, Decimal("0.20"), Decimal("0.12"))
        engine.settle_at_maturity("COE-ROUNDTRIP-01", Decimal("115.00"), "2027-01-01", 365)

        data = engine.to_dict()
        reconstructed = StructuredOperationsCoeEngine.from_dict(data)
        reconstructed.verify_invariants()
        self.assertEqual(len(reconstructed.contracts), 1)
        self.assertEqual(len(reconstructed.settlement_receipts), 1)

    def test_load_official_fixture(self) -> None:
        """Carrega e valida integralmente a fixture oficial fixtures/structured_operations_coe.json."""
        fixture_path = Path("fixtures/structured_operations_coe.json")
        self.assertTrue(fixture_path.exists(), f"Arquivo de fixture '{fixture_path}' deve existir")

        engine = StructuredOperationsCoeEngine.load_from_json(fixture_path)
        self.assertEqual(len(engine.contracts), 6)
        engine.verify_invariants()

        # Testa liquidação do primeiro contrato da fixture (COE-BULL-IBOV-001)
        receipt = engine.settle_at_maturity("COE-BULL-IBOV-001", Decimal("143000.00"), "2027-01-15", 365)
        self.assertEqual(receipt.contract_id, "COE-BULL-IBOV-001")
        self.assertGreater(receipt.total_gross_settlement, Decimal("500000.00"))
        self.assertTrue(receipt.invariant_capital_protection_held)


if __name__ == "__main__":
    unittest.main()
