"""
Suíte de Testes Unitários — B3 Balcão NoMe COE (FL-058)
======================================================
Validação completa de registro, precificação, barreiras de Autocall,
liquidação no vencimento e verificação das leis de conservação de capital.
"""

import unittest
from decimal import Decimal
from pathlib import Path
from src.coe_structured_notes import (
    CoeEngine,
    CoeContractSpec,
    CoeCapitalProtectionType,
    CoePayoffStructure,
    UnderlyingAssetType,
    CoeStatus,
    ObservationDateEvent,
    CoeValidationError,
    CoeSettlementError
)


class TestCoeStructuredNotes(unittest.TestCase):

    def setUp(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "coe_structured_notes.json"

    def test_load_from_fixture(self):
        engine = CoeEngine.load_from_json(self.fixture_file)
        self.assertEqual(len(engine.contracts), 4)
        c1 = engine.contracts["COE-IBOV-BULL-001"]
        self.assertEqual(c1.underlying_asset, UnderlyingAssetType.IBOVESPA)
        self.assertEqual(c1.protection_type, CoeCapitalProtectionType.PROTECTED_CAPITAL)
        self.assertEqual(c1.payoff_structure, CoePayoffStructure.BULL_SPREAD)
        self.assertEqual(c1.total_initial_notional, Decimal("500000.00"))

    def test_bull_spread_above_cap_maturity(self):
        engine = CoeEngine.load_from_json(self.fixture_file)
        # IBOV subiu de 130k para 180k (+38.46%), cap é 35%
        receipt = engine.settle_at_maturity(
            contract_id="COE-IBOV-BULL-001",
            final_underlying_level=Decimal("180000.00"),
            settlement_date="2026-11-04",
            days_elapsed=730
        )
        self.assertTrue(receipt.invariant_cap_gain_held)
        self.assertTrue(receipt.invariant_capital_protection_held)
        # Ganho limitado a 35% -> VNU bruto = 1000 * 1.35 = 1350.00
        self.assertEqual(receipt.gross_unit_payoff, Decimal("1350.0000"))
        self.assertEqual(receipt.total_gross_settlement, Decimal("675000.00"))
        self.assertEqual(receipt.tax_breakdown.tax_rate, Decimal("0.150")) # >720 dias
        self.assertEqual(receipt.tax_breakdown.irrf_amount, Decimal("26250.00")) # 175k * 15%
        self.assertEqual(receipt.total_net_settlement, Decimal("648750.00"))

    def test_bull_spread_negative_performance_protected_capital(self):
        engine = CoeEngine.load_from_json(self.fixture_file)
        # IBOV caiu de 130k para 115k (-11.54%) -> Garante 100% do capital
        receipt = engine.settle_at_maturity(
            contract_id="COE-IBOV-BULL-001",
            final_underlying_level=Decimal("115000.00"),
            settlement_date="2026-11-04",
            days_elapsed=730
        )
        self.assertTrue(receipt.invariant_capital_protection_held)
        self.assertEqual(receipt.gross_unit_payoff, Decimal("1000.0000"))
        self.assertEqual(receipt.total_gross_settlement, Decimal("500000.00"))
        self.assertEqual(receipt.tax_breakdown.gross_yield, Decimal("0.00"))
        self.assertEqual(receipt.tax_breakdown.irrf_amount, Decimal("0.00"))
        self.assertEqual(receipt.total_net_settlement, Decimal("500000.00"))

    def test_autocall_triggered_on_observation_date(self):
        engine = CoeEngine.load_from_json(self.fixture_file)
        # Ouro em 495 vs 450 inicial (+10%). Barreira é 100%. Cupom obs 2 é 17%
        receipt = engine.check_autocall(
            contract_id="COE-GOLD-AUTOCALL-003",
            observation_date="2026-11-04",
            current_underlying_level=Decimal("495.00"),
            days_elapsed=365
        )
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.settlement_reason, "AUTOCALL")
        self.assertEqual(receipt.gross_unit_payoff, Decimal("11700.0000")) # 10000 * 1.17
        self.assertEqual(receipt.total_gross_settlement, Decimal("585000.00"))
        self.assertEqual(receipt.tax_breakdown.tax_rate, Decimal("0.175")) # 365 dias (361 a 720d = 17.5%)
        c = engine.contracts["COE-GOLD-AUTOCALL-003"]
        self.assertEqual(c.status, CoeStatus.EARLY_TERMINATED)

    def test_validation_errors(self):
        engine = CoeEngine()
        spec = CoeContractSpec(
            contract_id="COE-INV",
            issuer_institution_id="INST",
            underlying_asset=UnderlyingAssetType.CDI,
            protection_type=CoeCapitalProtectionType.PROTECTED_CAPITAL,
            payoff_structure=CoePayoffStructure.DIGITAL_BINARY,
            issue_date="2026-01-01",
            maturity_date="2026-12-31",
            nominal_unit_value=Decimal("1000.00"),
            quantity=0, # Quantidade inválida
            initial_underlying_level=Decimal("100.00"),
            protection_floor_pct=Decimal("100.00")
        )
        with self.assertRaises(CoeValidationError):
            engine.register_coe(spec)


if __name__ == "__main__":
    unittest.main()
