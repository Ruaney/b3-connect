"""Testes unitários e invariantes formais do Motor de Circuit Breaker e Túneis PUMA (FL-093)."""

from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.puma_circuit_breaker_tunnels import (
    AssetSegment,
    MarketState,
    Order,
    OrderSide,
    OrderType,
    PumaCircuitBreakerEngine,
)


class TestPumaCircuitBreakerTunnels(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PumaCircuitBreakerEngine(
            symbol="PETR4",
            segment=AssetSegment.IBOV_EQUITY,
            opening_price=Decimal("30.00"),
        )

    def test_tunnel_bounds_calculation(self) -> None:
        bounds = self.engine.compute_bounds()
        # IBOV: Rejection +/-15% -> [25.50, 34.50]
        self.assertEqual(bounds.rejection_lower, Decimal("25.50"))
        self.assertEqual(bounds.rejection_upper, Decimal("34.50"))
        # Static +/-8% -> [27.60, 32.40]
        self.assertEqual(bounds.static_lower, Decimal("27.60"))
        self.assertEqual(bounds.static_upper, Decimal("32.40"))
        # Dynamic +/-3% -> [29.10, 30.90]
        self.assertEqual(bounds.dynamic_lower, Decimal("29.10"))
        self.assertEqual(bounds.dynamic_upper, Decimal("30.90"))

    def test_rejection_tunnel_rejects_out_of_bounds_order(self) -> None:
        # Preço 40.00 está acima de 34.50
        order_bad = Order(
            order_id="ORD-BAD-01",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("40.00"),
            quantity=100,
        )
        ok, reason = self.engine.submit_order(order_bad)
        self.assertFalse(ok)
        self.assertIn("Túnel de Rejeição", reason)
        self.assertEqual(len(self.engine.rejected_orders), 1)

    def test_continuous_matching_within_tunnels(self) -> None:
        # Ordem passiva de venda a 30.05
        sell = Order("ORD-S-1", "PETR4", OrderSide.SELL, OrderType.LIMIT, Decimal("30.05"), 500)
        self.engine.submit_order(sell)

        # Ordem agressora de compra a 30.05
        buy = Order("ORD-B-1", "PETR4", OrderSide.BUY, OrderType.LIMIT, Decimal("30.05"), 200)
        self.engine.submit_order(buy)

        self.assertEqual(len(self.engine.trades), 1)
        self.assertEqual(self.engine.trades[0].quantity, 200)
        self.assertEqual(self.engine.trades[0].price, Decimal("30.05"))
        self.assertEqual(self.engine.last_traded_price, Decimal("30.05"))
        self.assertEqual(sell.remaining_qty, 300)
        self.assertEqual(buy.remaining_qty, 0)
        self.assertEqual(self.engine.market_state, MarketState.CONTINUOUS)

    def test_circuit_breaker_triggered_by_dynamic_tunnel_breach(self) -> None:
        # Ordem passiva distante (31.50 viola túnel dinâmico de 3% a partir de 30.00 -> máx 30.90)
        sell_high = Order("ORD-S-HIGH", "PETR4", OrderSide.SELL, OrderType.LIMIT, Decimal("31.50"), 1000)
        self.engine.submit_order(sell_high)

        # Agressor tenta comprar a 31.50
        buy_aggr = Order("ORD-B-AGGR", "PETR4", OrderSide.BUY, OrderType.LIMIT, Decimal("31.50"), 500)
        self.engine.submit_order(buy_aggr)

        # Deve ter engatilhado Circuit Breaker ao invés de executar
        self.assertEqual(self.engine.market_state, MarketState.AUCTION_CIRCUIT_BREAKER)
        self.assertEqual(len(self.engine.trades), 0)
        self.assertEqual(len(self.engine.auction_history), 1)

        # No leilão, calcula PTP e executa uncross
        ptp = self.engine.uncross_auction()
        self.assertIsNotNone(ptp)
        self.assertEqual(ptp.ptp_price, Decimal("31.50"))
        self.assertEqual(ptp.executable_volume, 500)
        self.assertEqual(len(self.engine.trades), 1)
        self.assertTrue(self.engine.trades[0].executed_in_auction)
        self.assertEqual(self.engine.market_state, MarketState.CONTINUOUS)

    def test_invariants_and_serialization(self) -> None:
        # Executa cenário e testa invariantes
        audit = self.engine.verify_invariants()
        self.assertTrue(audit["all_invariants_hold"])
        self.assertTrue(audit["continuous_tunnel_compliance_verified"])
        self.assertTrue(audit["order_conservation_verified"])

        # Teste from_dict / to_dict
        d = self.engine.to_dict()
        restored = PumaCircuitBreakerEngine.from_dict(d)
        self.assertEqual(restored.symbol, self.engine.symbol)
        self.assertEqual(restored.last_traded_price, self.engine.last_traded_price)


if __name__ == "__main__":
    unittest.main()
