"""B3 Integration Atlas — Suíte de Testes do Mercado Secundário de Renda Fixa (FL-071).

Testa:
1. Precificação determinística de PU Par, PU Curva e PU Mercado (MtM ANBIMA).
2. Validação de Túnel de Preços em Cross-Trades (prevenção de preços fora da banda).
3. Roteamento de ordens de compra e venda com reserva de custódia e poder de compra.
4. Matching contínuo e liquidação atômica DvP D+1.
5. Invariantes financeiras: conservação de saldo de custódia e fechamento financeiro bilateral.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from src.secondary_market_fixed_income import (
    AssetType,
    CustodyAccount,
    FixedIncomeAsset,
    IndexerType,
    InsufficientCashLimitError,
    InsufficientCustodyBalanceError,
    OrderSide,
    OrderStatus,
    PriceQuoteType,
    SecondaryMarketFixedIncomeEngine,
    SecondaryMarketTunnelViolationError,
    SecondaryMarketValidationError,
    SettlementStatus,
    TradingMode,
)

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "secondary_fixed_income.json"


class TestSecondaryMarketFixedIncome(unittest.TestCase):
    """Bateria de testes unitários do motor de negociação de renda fixa privada."""

    def setUp(self) -> None:
        self.engine = SecondaryMarketFixedIncomeEngine.load_from_fixture(FIXTURE_PATH)

    def test_load_from_fixture_integrity(self) -> None:
        """Verifica se os ativos, market data e contas foram carregados corretamente."""
        self.assertIn("CRA022001", self.engine.assets)
        self.assertIn("CRI023002", self.engine.assets)
        self.assertIn("ACC-SELLER-01", self.engine.accounts)
        self.assertIn("ACC-BUYER-01", self.engine.accounts)
        self.assertEqual(self.engine.assets["CRA022001"].asset_type, AssetType.CRA)

    def test_pricing_pu_par_and_curva(self) -> None:
        """Valida apuração de PU Par e PU Curva para CRA indexado a IPCA."""
        pricing = self.engine.price_asset("CRA022001", calc_date=date(2024, 11, 4))
        self.assertIsInstance(pricing.pu_par, Decimal)
        self.assertGreater(pricing.pu_par, Decimal("1000.00"))
        self.assertGreater(pricing.pu_curva_sujo, pricing.pu_curva_limpo)
        self.assertGreater(pricing.juros_acruados, Decimal("0.00"))
        self.assertGreater(pricing.duration_macaulay_years, Decimal("0.00"))

    def test_pricing_pu_mercado_anbima_spread(self) -> None:
        """Valida PU Mercado com taxa de mercado ANBIMA exponencial: (1 + r_bench) * (1 + spread) - 1."""
        pricing = self.engine.price_asset("CRA022001", calc_date=date(2024, 11, 4))
        # YTM de mercado = (1 + 0.0580) * (1 + 0.0120) - 1 = 0.07069600
        expected_ytm = Decimal("0.07069600")
        self.assertEqual(pricing.market_ytm_annual, expected_ytm)
        self.assertGreater(pricing.pu_mercado_sujo, Decimal("0.00"))
        self.assertGreater(pricing.dv01, Decimal("0.00"))

    def test_mark_to_market_portfolio(self) -> None:
        """Testa reavaliação a mercado da carteira de uma conta de custódia."""
        mtm = self.engine.mark_to_market_portfolio("ACC-SELLER-01", calc_date=date(2024, 11, 4))
        self.assertIn("positions", mtm)
        self.assertGreater(mtm["total_market_value"], Decimal("0.00"))
        self.assertEqual(len(mtm["positions"]), 2)

    def test_submit_valid_order_and_custody_blocking(self) -> None:
        """Verifica que o envio de ordem de venda bloqueia saldo em custódia."""
        seller = self.engine.accounts["ACC-SELLER-01"]
        initial_free = seller.free_positions["CRA022001"]

        order = self.engine.submit_order(
            asset_code="CRA022001",
            side=OrderSide.SELL,
            mode=TradingMode.SPOT,
            quantity=Decimal("100"),
            quote_type=PriceQuoteType.DIRTY_PRICE,
            quote_value=Decimal("940.00"),
            account_id="ACC-SELLER-01",
            participant_code="105",
            trade_date=date(2024, 11, 4),
        )

        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertEqual(seller.free_positions["CRA022001"], initial_free - Decimal("100"))
        self.assertEqual(seller.blocked_positions["CRA022001"], Decimal("100"))

    def test_submit_sell_order_insufficient_custody_rejected(self) -> None:
        """Verifica rejeição mandatória de venda sem saldo suficiente em custódia."""
        with self.assertRaises(InsufficientCustodyBalanceError):
            self.engine.submit_order(
                asset_code="CRA022001",
                side=OrderSide.SELL,
                mode=TradingMode.SPOT,
                quantity=Decimal("10000"),  # Só possui 500
                quote_type=PriceQuoteType.DIRTY_PRICE,
                quote_value=Decimal("940.00"),
                account_id="ACC-SELLER-01",
                participant_code="105",
            )

    def test_submit_buy_order_insufficient_cash_rejected(self) -> None:
        """Verifica rejeição de compra que exceda saldo em conta + limite de crédito."""
        with self.assertRaises(InsufficientCashLimitError):
            self.engine.submit_order(
                asset_code="CRA022001",
                side=OrderSide.BUY,
                mode=TradingMode.SPOT,
                quantity=Decimal("10000"),  # 10.000 * 940 = 9.4M > 2.5M
                quote_type=PriceQuoteType.DIRTY_PRICE,
                quote_value=Decimal("940.00"),
                account_id="ACC-BUYER-01",
                participant_code="205",
            )

    def test_execute_cross_trade_within_tunnel(self) -> None:
        """Valida execução de Cross-Trade bilateral com preço respeitando o túnel ANBIMA."""
        trade = self.engine.execute_cross_trade(
            asset_code="CRA022001",
            quantity=Decimal("50"),
            agreed_dirty_price=Decimal("940.00"),
            buyer_account_id="ACC-BUYER-01",
            seller_account_id="ACC-SELLER-01",
            buyer_participant="205",
            seller_participant="105",
            trade_date=date(2024, 11, 4),
        )

        self.assertIsNotNone(trade.trade_id)
        self.assertEqual(trade.status, SettlementStatus.PENDING)
        self.assertIsNotNone(trade.pricing_tunnel_deviation_pct)
        asset = self.engine.assets["CRA022001"]
        self.assertLessEqual(trade.pricing_tunnel_deviation_pct, asset.tunnel_band_max_pct)

    def test_execute_cross_trade_violating_tunnel_rejected(self) -> None:
        """Verifica que cross-trade com preço discrepante fora da banda do túnel é rejeitado."""
        with self.assertRaises(SecondaryMarketTunnelViolationError):
            self.engine.execute_cross_trade(
                asset_code="CRA022001",
                quantity=Decimal("50"),
                agreed_dirty_price=Decimal("1500.00"),  # Mais de 50% acima do PU Mercado (banda max é 2.5%)
                buyer_account_id="ACC-BUYER-01",
                seller_account_id="ACC-SELLER-01",
                buyer_participant="205",
                seller_participant="105",
                trade_date=date(2024, 11, 4),
            )

    def test_settle_dvp_atomic_conservation(self) -> None:
        """Valida liquidação financeira e física DvP D+1 com conservação atômica."""
        seller = self.engine.accounts["ACC-SELLER-01"]
        buyer = self.engine.accounts["ACC-BUYER-01"]

        seller_initial_cash = seller.cash_balance
        buyer_initial_cash = buyer.cash_balance

        trade = self.engine.execute_cross_trade(
            asset_code="CRA022001",
            quantity=Decimal("50"),
            agreed_dirty_price=Decimal("940.00"),
            buyer_account_id="ACC-BUYER-01",
            seller_account_id="ACC-SELLER-01",
            buyer_participant="205",
            seller_participant="105",
            trade_date=date(2024, 11, 4),
        )

        receipt = self.engine.settle_dvp(trade.trade_id, settlement_date=date(2024, 11, 5))

        self.assertEqual(receipt.status, SettlementStatus.SETTLED)
        total_fin = Decimal("50") * Decimal("940.00")
        self.assertEqual(receipt.gross_financial_amount, total_fin)

        # Checa transferência de dinheiro
        self.assertEqual(seller.cash_balance, seller_initial_cash + total_fin)
        self.assertEqual(buyer.cash_balance, buyer_initial_cash - total_fin)

        # Checa transferência de custódia
        self.assertEqual(buyer.free_positions.get("CRA022001"), Decimal("50"))

    def test_verify_all_invariants(self) -> None:
        """Valida que todas as leis de conservação formal se sustentam no motor."""
        trade = self.engine.execute_cross_trade(
            asset_code="CRA022001",
            quantity=Decimal("10"),
            agreed_dirty_price=Decimal("940.00"),
            buyer_account_id="ACC-BUYER-01",
            seller_account_id="ACC-SELLER-01",
            buyer_participant="205",
            seller_participant="105",
            trade_date=date(2024, 11, 4),
        )
        self.engine.settle_dvp(trade.trade_id, settlement_date=date(2024, 11, 5))

        report = self.engine.verify_all_invariants()
        self.assertTrue(report["all_invariants_hold"])
        self.assertTrue(report["dvp_physical_conservation_verified"])
        self.assertTrue(report["dvp_financial_conservation_verified"])
        self.assertTrue(report["pu_conservation_verified"])
        self.assertTrue(report["tunnel_integrity_verified"])


if __name__ == "__main__":
    unittest.main()
