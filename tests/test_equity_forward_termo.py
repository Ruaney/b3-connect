"""Testes unitários para o Mercado a Termo de Ações B3 (FL-041)."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from src.equity_forward_termo import (
    CUSTODY_WALLET_FORWARD_COVERAGE,
    CUSTODY_WALLET_FREE_SPOT,
    EquityForwardContract,
    EquityForwardEngine,
    EquityForwardValidationError,
    ForwardCoverageStatus,
    ForwardLifecycleStatus,
    ForwardModality,
    ForwardQuantityConservationError,
    ForwardTermRangeError,
    InsufficientSellerCoverageError,
    calculate_forward_unit_price,
)


class TestEquityForwardTermo(unittest.TestCase):
    def setUp(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_path = self.fixtures_dir / "equity_forward_contracts.json"

    def test_forward_pricing_exponential_du252(self):
        """Valida a fórmula exponencial de precificação a termo P_termo = P_vista * (1 + i)^(DU/252)."""
        p_vista = Decimal("36.00")
        taxa_aa = Decimal("12.6825")
        du = 42
        p_termo = calculate_forward_unit_price(p_vista, taxa_aa, du)
        self.assertEqual(p_termo, Decimal("36.72"))

    def test_term_range_limits(self):
        """Valida os limites regulamentares B3 de prazo (16 a 999 dias corridos)."""
        with self.assertRaises(ForwardTermRangeError):
            EquityForwardContract(
                contract_id="TRM-FAIL-01",
                ticker="PETR4T",
                underlying_ticker="PETR4",
                underlying_isin="BRPETRACNPR6",
                modality=ForwardModality.TRADITIONAL_BRL,
                trade_date=date(2026, 10, 5),
                maturity_date=date(2026, 10, 15),  # 10 dias < 16 dias
                calendar_days_dc=10,
                business_days_du=8,
                spot_price_brl=Decimal("36.00"),
                annual_interest_rate_pct=Decimal("12.0000"),
                forward_unit_price_brl=Decimal("36.13"),
                original_quantity=1000,
                early_settled_quantity=0,
                maturity_settled_quantity=0,
                open_quantity=1000,
                seller_participant_code="00105",
                seller_investor_account="100201-9",
                seller_covered_quantity=1000,
                buyer_participant_code="00210",
                buyer_investor_account="200450-1",
                current_market_price_brl=Decimal("36.00"),
                core_haircut_pct=Decimal("15.0000"),
                buyer_deposited_collateral_brl=Decimal("10000.00"),
            )

    def test_seller_100pct_coverage_enforcement(self):
        """Valida que o vendedor deve depositar 100% da quantidade aberta na carteira 2306-6."""
        with self.assertRaises(InsufficientSellerCoverageError):
            EquityForwardContract(
                contract_id="TRM-FAIL-02",
                ticker="PETR4T",
                underlying_ticker="PETR4",
                underlying_isin="BRPETRACNPR6",
                modality=ForwardModality.TRADITIONAL_BRL,
                trade_date=date(2026, 10, 5),
                maturity_date=date(2026, 11, 20),
                calendar_days_dc=46,
                business_days_du=32,
                spot_price_brl=Decimal("36.00"),
                annual_interest_rate_pct=Decimal("12.0000"),
                forward_unit_price_brl=Decimal("36.52"),
                original_quantity=1000,
                early_settled_quantity=0,
                maturity_settled_quantity=0,
                open_quantity=1000,
                seller_participant_code="00105",
                seller_investor_account="100201-9",
                seller_covered_quantity=800,  # Faltam 200 ações na 2306-6
                buyer_participant_code="00210",
                buyer_investor_account="200450-1",
                current_market_price_brl=Decimal("36.00"),
                core_haircut_pct=Decimal("15.0000"),
                buyer_deposited_collateral_brl=Decimal("10000.00"),
            )

    def test_fixture_loading_and_portfolio_conservation(self):
        """Valida o carregamento da fixture oficial e a conservação estrita de quantidades."""
        engine = EquityForwardEngine.from_json_fixture(self.fixture_path)
        summary = engine.summarize()

        self.assertEqual(summary.total_contracts, 4)
        self.assertTrue(summary.all_quantity_conserved)
        self.assertTrue(summary.all_seller_100pct_covered)
        self.assertTrue(summary.all_terms_within_b3_limits)
        self.assertTrue(summary.all_zero_sum_settlements)

        # Checa conservação global: Original == Liquidado Antecipado + Liquidado Vencimento + Aberto
        self.assertEqual(
            summary.total_original_quantity,
            summary.total_early_settled_quantity
            + summary.total_maturity_settled_quantity
            + summary.total_open_quantity,
        )

    def test_early_settlement_execution(self):
        """Valida a execução de liquidação antecipada parcial com redução de posição e conservação."""
        engine = EquityForwardEngine.from_json_fixture(self.fixture_path)
        contract = engine.get_contract("TRM-B3-2026-0001")
        self.assertEqual(contract.open_quantity, 6000)

        # Executa liquidação antecipada adicional de 2.000 ações
        event = engine.settle_early(
            contract_id="TRM-B3-2026-0001",
            event_id="EVT-TEST-EARLY-02",
            settlement_date=date(2026, 11, 3),
            quantity=2000,
            requested_by="BUYER",
        )
        self.assertEqual(contract.open_quantity, 4000)
        self.assertEqual(contract.early_settled_quantity, 6000)
        self.assertEqual(contract.seller_covered_quantity, 4000)
        self.assertTrue(contract.is_quantity_conserved)
        self.assertTrue(event.is_zero_sum_cashflow)


if __name__ == "__main__":
    unittest.main()
