"""
Suíte de Testes Unitários — B3 PUMA Closing Auction (FL-059)
===========================================================
Validação de formação de Preço Teórico de Fechamento (PTF),
execução de uncross, prioridade de ordens MOC/LOC e conservação de book.
"""

import unittest
from decimal import Decimal
from pathlib import Path
from src.puma_closing_auction import (
    PumaClosingAuctionEngine,
    AuctionOrder,
    AuctionOrderSide,
    AuctionOrderType,
    AuctionPhase,
    PumaClosingAuctionError
)


class TestPumaClosingAuction(unittest.TestCase):

    def setUp(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "puma_closing_auction.json"

    def test_load_from_fixture(self):
        engine = PumaClosingAuctionEngine.load_from_fixture(self.fixture_file)
        self.assertEqual(engine.symbol, "PETR4")
        self.assertEqual(engine.reference_price, Decimal("38.50"))
        self.assertEqual(len(engine.orders), 6)

    def test_calculate_theoretical_price(self):
        engine = PumaClosingAuctionEngine.load_from_fixture(self.fixture_file)
        summary = engine.calculate_theoretical_price()
        self.assertIsNotNone(summary)
        # No preço 38.50:
        # Compras disponíveis: MOC (15k) + LIM 38.60 (25k) + LOC 38.50 (10k) = 50.000 lotes
        # Vendas disponíveis: MOC (20k) + LIM 38.50 (20k) = 40.000 lotes (LOC 38.55 não entra)
        # Volume executável = min(50k, 40k) = 40.000
        self.assertEqual(summary.uncross_price, Decimal("38.50"))
        self.assertEqual(summary.executable_volume, 40000)
        self.assertEqual(summary.imbalance_quantity, 10000) # Excesso de 10k na compra

    def test_execute_uncross_and_invariants(self):
        engine = PumaClosingAuctionEngine.load_from_fixture(self.fixture_file)
        ptf = engine.execute_uncross()
        self.assertEqual(ptf.uncross_price, Decimal("38.50"))
        self.assertEqual(engine.phase, AuctionPhase.CLOSED)
        self.assertTrue(len(engine.executions) > 0)

        # Invariantes formais
        inv = engine.verify_invariants()
        self.assertTrue(inv["conservation_verified"])
        self.assertTrue(inv["unique_price_verified"])
        self.assertTrue(inv["has_executions"])

        # Soma dos volumes executados deve ser exatamente 40.000
        total_exec = sum(fill.executed_quantity for fill in engine.executions)
        self.assertEqual(total_exec, 40000)

        # MOC Sell de 20k deve estar 100% executado
        moc_sell = engine.orders["ORD-MOC-SELL-01"]
        self.assertEqual(moc_sell.remaining_quantity, 0)
        self.assertEqual(moc_sell.executed_quantity, 20000)

    def test_error_handling(self):
        engine = PumaClosingAuctionEngine("VALE3", Decimal("60.00"))
        with self.assertRaises(PumaClosingAuctionError):
            engine.add_order(
                AuctionOrder(
                    order_id="ERR-1",
                    symbol="PETR4", # Símbolo divergente
                    side=AuctionOrderSide.BUY,
                    order_type=AuctionOrderType.MOC,
                    quantity=100
                )
            )


if __name__ == "__main__":
    unittest.main()
