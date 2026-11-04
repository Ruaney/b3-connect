"""
Suíte de Testes Unitários — Smart Order Router (SOR) & Agregação de Liquidez (FL-068)
===================================================================================
Validação da fragmentação determinística de ordens (Split SOR), agregação multi-venue,
minimização de market impact/slippage e Lei Fundamental de Conservação de Lote Original:
Soma(Q_roteada) + Q_residual == Q_ordem_mae.
"""

import json
import unittest
from decimal import Decimal
from pathlib import Path

from src.smart_order_router import (
    ChildOrderSlice,
    OrderSide,
    ParentOrder,
    ParentOrderType,
    PriceLevel,
    RouterExecutionReport,
    RouterExecutionStatus,
    RoutingStrategy,
    SliceStatus,
    SmartOrderRouterEngine,
    SmartOrderRouterError,
    VenueBook,
    VenueType,
)


class TestSmartOrderRouter(unittest.TestCase):

    def setUp(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "smart_order_router.json"

    def test_load_from_fixture(self):
        """Verifica o carregamento dos 4 venues e dos livros a partir da fixture oficial."""
        engine = SmartOrderRouterEngine.load_from_fixture(self.fixture_file)
        self.assertEqual(engine.symbol, "PETR4")
        self.assertEqual(len(engine.venues), 4)

        best_bid, best_ask = engine.get_national_best_bid_offer()
        self.assertIsNotNone(best_bid)
        self.assertIsNotNone(best_ask)
        # BBO consolidado
        self.assertEqual(best_bid, Decimal("38.49"))
        self.assertEqual(best_ask, Decimal("38.49"))

    def test_aggregated_depth(self):
        """Valida a consolidação de profundidade multi-mercado."""
        engine = SmartOrderRouterEngine.load_from_fixture(self.fixture_file)
        ask_depth = engine.get_aggregated_depth(OrderSide.BUY)
        self.assertTrue(len(ask_depth) >= 4)
        # Menor preço de ask é 38.49 (Dark Pool)
        self.assertEqual(ask_depth[0]["price"], Decimal("38.49"))
        self.assertEqual(ask_depth[0]["total_quantity"], 15000)

    def test_tc01_best_price_depth_buy_market(self):
        """
        TC-01: Ordem-mãe de Compra a Mercado (25.000 ações).
        Estratégia: BEST_PRICE_DEPTH.
        Varre: 15.000 @ 38.49 (Dark) + 8.000 @ 38.495 (RLP) + 2.000 @ 38.50 (Lit).
        Prova formal da conservação: 15k + 8k + 2k == 25k (Q_mãe).
        """
        engine = SmartOrderRouterEngine.load_from_fixture(self.fixture_file)
        order = ParentOrder(
            order_id="ORD-TC01-BUY-MKT",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=ParentOrderType.MARKET,
            quantity=25000,
            strategy=RoutingStrategy.BEST_PRICE_DEPTH
        )
        report = engine.route_order(order)

        self.assertEqual(report.status, RouterExecutionStatus.FULLY_ROUTED)
        self.assertEqual(report.original_quantity, 25000)
        self.assertEqual(report.total_routed_quantity, 25000)
        self.assertEqual(report.residual_unrouted_quantity, 0)
        self.assertTrue(report.conservation_verified)

        # Soma das fatias
        self.assertEqual(sum(s.quantity for s in report.slices), 25000)
        self.assertEqual(len(report.slices), 3)

        # Preço Médio Ponderado (VWAP) deve refletir melhora sobre o book lit (38.50)
        self.assertLess(report.volume_weighted_average_price, Decimal("38.50"))

        # Invariantes formais
        inv = engine.verify_invariants(report)
        self.assertTrue(inv["all_invariants_hold"])
        self.assertTrue(inv["conservation_law"])
        self.assertTrue(inv["no_trade_through"])

    def test_tc02_minimal_market_impact_large_buy(self):
        """
        TC-02: Ordem-mãe de Compra Limitada (45.000 ações a 38.50).
        Estratégia: MINIMAL_MARKET_IMPACT.
        Prioriza absorção em Dark Pools (Dark Pool + Block Crossing) antes de atingir o livro lit.
        Os ambientes dark possuem 75.000 ações disponíveis a <= 38.50, portanto todas as 45.000
        ações são absorvidas com impacto zero no livro aberto.
        """
        engine = SmartOrderRouterEngine.load_from_fixture(self.fixture_file)
        order = ParentOrder(
            order_id="ORD-TC02-BUY-DARK-IMPACT",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=ParentOrderType.LIMIT,
            quantity=45000,
            price=Decimal("38.50"),
            strategy=RoutingStrategy.MINIMAL_MARKET_IMPACT
        )
        report = engine.route_order(order)

        self.assertEqual(report.status, RouterExecutionStatus.FULLY_ROUTED)
        self.assertEqual(report.total_routed_quantity, 45000)
        self.assertEqual(report.residual_unrouted_quantity, 0)
        self.assertTrue(report.conservation_verified)

        # 100% das 45.000 ações foram absorvidas em venues dark
        dark_volume = sum(s.quantity for s in report.slices if s.is_dark)
        self.assertEqual(dark_volume, 45000)
        self.assertEqual(report.dark_pool_fill_ratio, Decimal("1.0000"))
        self.assertEqual(report.market_impact_score, Decimal("0.0000"))

        # Lei de conservação
        self.assertEqual(sum(s.quantity for s in report.slices), 45000)
        inv = engine.verify_invariants(report)
        self.assertTrue(inv["all_invariants_hold"])

    def test_minimal_market_impact_hybrid_dark_and_lit(self):
        """
        Testa ordem maior que a capacidade dos pools dark (90.000 ações).
        Estratégia: MINIMAL_MARKET_IMPACT.
        Fase 1: 75.000 ações absorvidas em Dark Pools (15k + 20k Dark + 40k Block).
        Fase 2: 15.000 ações restantes divididas entre Balcão RLP (8k @ 38.495) e PUMA Lit (7k @ 38.50).
        Conservação total: 75k + 15k == 90.000.
        """
        engine = SmartOrderRouterEngine.load_from_fixture(self.fixture_file)
        order = ParentOrder(
            order_id="ORD-HYBRID-90K",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=ParentOrderType.LIMIT,
            quantity=90000,
            price=Decimal("38.50"),
            strategy=RoutingStrategy.MINIMAL_MARKET_IMPACT
        )
        report = engine.route_order(order)

        self.assertEqual(report.status, RouterExecutionStatus.FULLY_ROUTED)
        self.assertEqual(report.total_routed_quantity, 90000)
        self.assertEqual(report.residual_unrouted_quantity, 0)
        self.assertTrue(report.conservation_verified)

        dark_qty = sum(s.quantity for s in report.slices if s.is_dark)
        lit_qty = sum(s.quantity for s in report.slices if not s.is_dark)
        self.assertEqual(dark_qty, 75000)
        self.assertEqual(lit_qty, 15000)
        self.assertEqual(dark_qty + lit_qty, 90000)

        inv = engine.verify_invariants(report)
        self.assertTrue(inv["all_invariants_hold"])

    def test_filter_disallow_dark_and_otc(self):
        """
        Testa roteamento quando a ordem proíbe dark pools ou balcão (allow_dark=False, allow_otc=False).
        Deve interagir unicamente com o livro lit PUMA.
        """
        engine = SmartOrderRouterEngine.load_from_fixture(self.fixture_file)
        order = ParentOrder(
            order_id="ORD-LIT-ONLY",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=ParentOrderType.LIMIT,
            quantity=15000,
            price=Decimal("38.51"),
            allow_dark=False,
            allow_otc=False,
            strategy=RoutingStrategy.BEST_PRICE_DEPTH
        )
        report = engine.route_order(order)

        self.assertEqual(report.status, RouterExecutionStatus.FULLY_ROUTED)
        self.assertEqual(report.total_routed_quantity, 15000)
        # 100% das fatias no PUMA LIT
        for s in report.slices:
            self.assertEqual(s.venue_id, "VENUE-PUMA-LIT")
            self.assertFalse(s.is_dark)

        self.assertEqual(report.dark_pool_fill_ratio, Decimal("0.0000"))
        self.assertTrue(report.conservation_verified)

    def test_report_serialization_to_dict(self):
        """Testa serialização JSON compatível do relatório."""
        engine = SmartOrderRouterEngine.load_from_fixture(self.fixture_file)
        order = ParentOrder(
            order_id="ORD-SER-TEST",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=ParentOrderType.MARKET,
            quantity=1000
        )
        report = engine.route_order(order)
        d = report.to_dict()
        self.assertEqual(d["parent_order_id"], "ORD-SER-TEST")
        self.assertEqual(d["symbol"], "PETR4")
        self.assertEqual(d["total_routed_quantity"], 1000)
        # Garante que pode ser serializado para JSON sem erro de Decimal
        serialized = json.dumps(d)
        self.assertIn("ORD-SER-TEST", serialized)

    def test_tc03_cost_fee_optimized_sell(self):
        """
        TC-03: Ordem-mãe de Venda a Mercado (30.000 ações).
        Estratégia: COST_FEE_OPTIMIZED.
        Prioriza os melhores preços líquidos de taxa/emolumento.
        """
        engine = SmartOrderRouterEngine.load_from_fixture(self.fixture_file)
        order = ParentOrder(
            order_id="ORD-TC03-SELL-FEE-OPT",
            symbol="PETR4",
            side=OrderSide.SELL,
            order_type=ParentOrderType.MARKET,
            quantity=30000,
            strategy=RoutingStrategy.COST_FEE_OPTIMIZED
        )
        report = engine.route_order(order)

        self.assertEqual(report.status, RouterExecutionStatus.FULLY_ROUTED)
        self.assertEqual(report.total_routed_quantity, 30000)
        self.assertEqual(report.residual_unrouted_quantity, 0)
        self.assertTrue(report.conservation_verified)
        self.assertEqual(sum(s.quantity for s in report.slices), 30000)

        inv = engine.verify_invariants(report)
        self.assertTrue(inv["all_invariants_hold"])

    def test_tc04_pro_rata_liquidity_split(self):
        """
        TC-04: Ordem-mãe de Compra Limitada (20.000 ações a 38.50).
        Estratégia: PRO_RATA_LIQUIDITY.
        Garante conservação exata de inteiros (Hare-Niemeyer) sem perda de nenhuma ação.
        """
        engine = SmartOrderRouterEngine.load_from_fixture(self.fixture_file)
        order = ParentOrder(
            order_id="ORD-TC04-BUY-PRO-RATA",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=ParentOrderType.LIMIT,
            quantity=20000,
            price=Decimal("38.50"),
            strategy=RoutingStrategy.PRO_RATA_LIQUIDITY
        )
        report = engine.route_order(order)

        self.assertEqual(report.status, RouterExecutionStatus.FULLY_ROUTED)
        self.assertEqual(report.total_routed_quantity, 20000)
        self.assertEqual(report.residual_unrouted_quantity, 0)
        self.assertTrue(report.conservation_verified)
        self.assertEqual(sum(s.quantity for s in report.slices), 20000)

        inv = engine.verify_invariants(report)
        self.assertTrue(inv["all_invariants_hold"])

    def test_tc05_partial_fill_residual_conservation(self):
        """
        TC-05: Ordem-mãe de Compra Limitada de 150.000 ações a 38.50.
        Liquidez disponível total até 38.50 é menor que 150.000.
        Prova formal da conservação:
        Soma(Q_roteada) + Q_residual == Q_ordem_mae (150.000).
        """
        engine = SmartOrderRouterEngine.load_from_fixture(self.fixture_file)
        order = ParentOrder(
            order_id="ORD-TC05-BUY-PARTIAL",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=ParentOrderType.LIMIT,
            quantity=150000,
            price=Decimal("38.50"),
            strategy=RoutingStrategy.BEST_PRICE_DEPTH
        )
        report = engine.route_order(order)

        self.assertEqual(report.status, RouterExecutionStatus.PARTIALLY_ROUTED)
        self.assertGreater(report.total_routed_quantity, 0)
        self.assertGreater(report.residual_unrouted_quantity, 0)
        self.assertEqual(
            report.total_routed_quantity + report.residual_unrouted_quantity,
            150000
        )
        self.assertTrue(report.conservation_verified)

        # Invariantes formais
        inv = engine.verify_invariants(report)
        self.assertTrue(inv["conservation_law"])
        self.assertTrue(inv["non_negative_slices"])

    def test_tc06_fok_rejected_conservation(self):
        """
        TC-06: Ordem FOK não preenchível na íntegra é cancelada imediatamente.
        Conservação: 0 roteado + 200.000 residual == 200.000 (Q_mãe).
        """
        engine = SmartOrderRouterEngine.load_from_fixture(self.fixture_file)
        order = ParentOrder(
            order_id="ORD-TC06-BUY-FOK-FAIL",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=ParentOrderType.FOK,
            quantity=200000,
            price=Decimal("38.495"),
            strategy=RoutingStrategy.BEST_PRICE_DEPTH
        )
        report = engine.route_order(order)

        self.assertEqual(report.status, RouterExecutionStatus.REJECTED_FOK_UNFILLABLE)
        self.assertEqual(report.total_routed_quantity, 0)
        self.assertEqual(report.residual_unrouted_quantity, 200000)
        self.assertTrue(report.conservation_verified)
        self.assertEqual(len(report.slices), 0)

    def test_tc07_fok_success_conservation(self):
        """
        TC-07: Ordem FOK que possui liquidez suficiente no preço limite é atendida 100%.
        """
        engine = SmartOrderRouterEngine.load_from_fixture(self.fixture_file)
        order = ParentOrder(
            order_id="ORD-TC07-BUY-FOK-SUCCESS",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=ParentOrderType.FOK,
            quantity=15000,
            price=Decimal("38.495"),
            strategy=RoutingStrategy.BEST_PRICE_DEPTH
        )
        report = engine.route_order(order)

        self.assertEqual(report.status, RouterExecutionStatus.FULLY_ROUTED)
        self.assertEqual(report.total_routed_quantity, 15000)
        self.assertEqual(report.residual_unrouted_quantity, 0)
        self.assertTrue(report.conservation_verified)

    def test_price_limit_protection_no_slices_above_limit(self):
        """
        Garante que nenhuma fatia de compra é executada acima do preço limite especificado.
        """
        engine = SmartOrderRouterEngine.load_from_fixture(self.fixture_file)
        limit_p = Decimal("38.49")
        order = ParentOrder(
            order_id="ORD-LIMIT-STRICT",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=ParentOrderType.LIMIT,
            quantity=50000,
            price=limit_p,
            strategy=RoutingStrategy.BEST_PRICE_DEPTH
        )
        report = engine.route_order(order)

        for s in report.slices:
            self.assertLessEqual(s.price, limit_p)

        self.assertEqual(
            report.total_routed_quantity + report.residual_unrouted_quantity,
            order.quantity
        )
        self.assertTrue(report.conservation_verified)

    def test_error_handling_and_validation(self):
        """Testa validações defensivas do motor e da ordem-mãe."""
        # Quantidade negativa ou zero
        with self.assertRaises(SmartOrderRouterError):
            ParentOrder(
                order_id="ERR-QTY",
                symbol="PETR4",
                side=OrderSide.BUY,
                order_type=ParentOrderType.MARKET,
                quantity=0
            )

        # Ordem limitada sem preço
        with self.assertRaises(SmartOrderRouterError):
            ParentOrder(
                order_id="ERR-PRICE",
                symbol="PETR4",
                side=OrderSide.BUY,
                order_type=ParentOrderType.LIMIT,
                quantity=1000,
                price=None
            )

        # Divergência de símbolo
        engine = SmartOrderRouterEngine("VALE3")
        with self.assertRaises(SmartOrderRouterError):
            engine.register_venue(
                VenueBook(
                    venue_id="V-1",
                    venue_name="Test",
                    venue_type=VenueType.PUMA_LIT,
                    symbol="PETR4"
                )
            )


if __name__ == "__main__":
    unittest.main()
