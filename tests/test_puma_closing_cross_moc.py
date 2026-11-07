"""Suíte de Testes Unitários e Invariantes Formais — Leilão de Fechamento PUMA MOC/LOC (FL-098).
=============================================================================================

Testes exaustivos cobrindo:
1. Formação do Preço Teórico de Fechamento (PTP/PTF) e Regras Oficiais de Desempate B3.
2. Inviolabilidade da Janela de Congelamento (Freeze Window / No-Cancel — LAW-104A).
3. Prioridade Absoluta de Alocação de Ordens MOC sobre LOC (LAW-104B).
4. Conservação de Volume no Uncross e Preço Único de Liquidação (LAW-104C).
5. Casos de Borda: Apenas MOC, Ausência de Liquidez, Empates Equidistantes e Tratamento de Erros.
6. Serialização e Desserialização Roundtrip (`to_dict` / `from_dict` / `load_from_fixture`).
"""

from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.puma_closing_cross_moc import (
    AuctionPhase,
    ClosingOrder,
    ClosingOrderSide,
    ClosingOrderStatus,
    ClosingOrderType,
    PumaClosingCrossEngine,
    PumaClosingCrossError,
    PumaClosingExecutionFill,
    PumaFreezeWindowViolationError,
    PumaInvariantViolationError,
    TheoreticalPriceSummary,
    round_price,
)


class TestPumaClosingCrossMoc(unittest.TestCase):
    """Bateria de testes unitários do motor de leilão de fechamento PUMA B3."""

    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "puma_closing_cross_moc.json"
        self.engine = PumaClosingCrossEngine(
            symbol="PETR4",
            reference_price=Decimal("35.00"),
            tick_size=Decimal("0.01"),
        )

    # --------------------------------------------------------------------------
    # 1. Testes de Carregamento de Fixture e Execução Padrão
    # --------------------------------------------------------------------------

    def test_load_from_fixture(self) -> None:
        """Valida carregamento completo da fixture canônica JSON."""
        engine = PumaClosingCrossEngine.load_from_fixture(self.fixture_file)
        self.assertEqual(engine.symbol, "PETR4")
        self.assertEqual(engine.reference_price, Decimal("35.00"))
        self.assertEqual(len(engine.orders), 8)

        # Checa tipos de ordens na fixture
        moc_buys = [o for o in engine.orders.values() if o.order_type == ClosingOrderType.MOC and o.side == ClosingOrderSide.BUY]
        moc_sells = [o for o in engine.orders.values() if o.order_type == ClosingOrderType.MOC and o.side == ClosingOrderSide.SELL]
        self.assertEqual(len(moc_buys), 1)
        self.assertEqual(len(moc_sells), 1)
        self.assertIsNone(moc_buys[0].price)
        self.assertIsNone(moc_sells[0].price)

    def test_calculate_theoretical_price_fixture(self) -> None:
        """Valida a apuração determinística do PTP a partir da fixture."""
        engine = PumaClosingCrossEngine.load_from_fixture(self.fixture_file)
        summary = engine.calculate_theoretical_price()
        self.assertIsNotNone(summary)
        assert summary is not None

        # Preço ótimo apurado deve ser 35.00
        # No preço 35.00:
        # Compras elegíveis: MOC (30k) + LOC 35.10 (20k) + LOC 35.00 (15k) = 65.000 lotes
        # Vendas elegíveis: MOC (25k) + LOC 34.95 (20k) + LOC 35.00 (15k) = 60.000 lotes
        # Volume executável = min(65k, 60k) = 60.000 lotes
        # Desequilíbrio = +5.000 (excesso comprador)
        self.assertEqual(summary.uncross_price, Decimal("35.00"))
        self.assertEqual(summary.executable_volume, 60000)
        self.assertEqual(summary.imbalance_quantity, 5000)
        self.assertEqual(summary.rule_applied, "MAX_VOLUME")

    def test_execute_uncross_fixture_and_invariants(self) -> None:
        """Executa uncross da fixture e audita integralmente as invariantes LAW-104A/B/C."""
        engine = PumaClosingCrossEngine.load_from_fixture(self.fixture_file)
        ptp = engine.execute_uncross()

        self.assertEqual(ptp.uncross_price, Decimal("35.00"))
        self.assertEqual(engine.phase, AuctionPhase.CLOSED)
        self.assertTrue(len(engine.executions) > 0)

        # Volume total executado nas execuções deve ser exatamente 60.000
        total_fill_qty = sum(f.executed_quantity for f in engine.executions)
        self.assertEqual(total_fill_qty, 60000)

        # MOC Sell (25k) deve estar 100% executado
        moc_sell = engine.orders["ORD-MOC-SELL-001"]
        self.assertEqual(moc_sell.remaining_quantity, 0)
        self.assertEqual(moc_sell.status, ClosingOrderStatus.FILLED)

        # MOC Buy (30k) deve estar 100% executado
        moc_buy = engine.orders["ORD-MOC-BUY-001"]
        self.assertEqual(moc_buy.remaining_quantity, 0)
        self.assertEqual(moc_buy.status, ClosingOrderStatus.FILLED)

        # Auditoria formal de invariantes
        inv = engine.verify_invariants()
        self.assertTrue(inv["all_invariants_hold"])
        self.assertTrue(inv["law_104a_freeze_inviolability"])
        self.assertTrue(inv["law_104b_moc_priority"])
        self.assertTrue(inv["law_104c_volume_conservation"])
        self.assertTrue(inv["ptp_unique_price_verified"])
        self.assertEqual(inv["total_executed_lots"], 60000)

    # --------------------------------------------------------------------------
    # 2. Invariante LAW-104A: Inviolabilidade da Janela de Congelamento
    # --------------------------------------------------------------------------

    def test_law_104a_cancellation_lifecycle_and_freeze_rejection(self) -> None:
        """Garante que cancelamentos funcionam na CALL_PHASE mas são sumariamente rejeitados no FREEZE."""
        engine = PumaClosingCrossEngine("PETR4", Decimal("35.00"))
        order1 = ClosingOrder(
            order_id="ORD-01",
            symbol="PETR4",
            side=ClosingOrderSide.BUY,
            order_type=ClosingOrderType.LOC,
            quantity=1000,
            price=Decimal("35.00"),
        )
        order2 = ClosingOrder(
            order_id="ORD-02",
            symbol="PETR4",
            side=ClosingOrderSide.BUY,
            order_type=ClosingOrderType.LOC,
            quantity=2000,
            price=Decimal("35.10"),
        )
        engine.add_order(order1)
        engine.add_order(order2)

        # 1. Em CALL_PHASE, cancelamento é permitido
        engine.cancel_order("ORD-01")
        self.assertEqual(order1.status, ClosingOrderStatus.CANCELLED)
        self.assertEqual(order1.cancelled_quantity, 1000)
        self.assertEqual(order1.remaining_quantity, 0)

        # 2. Entrada no Freeze Period
        engine.enter_freeze_period(timestamp_ms=1731000000000)
        self.assertEqual(engine.phase, AuctionPhase.FREEZE_WINDOW)

        # 3. Tentativa de cancelar ORD-02 durante o freeze DEVE ser sumariamente rejeitada
        with self.assertRaises(PumaFreezeWindowViolationError) as ctx:
            engine.cancel_order("ORD-02", timestamp_ms=1731000001000)
        self.assertIn("LAW-104A", str(ctx.exception))
        self.assertIn("FREEZE_WINDOW", str(ctx.exception))

        # Ordem 2 deve permanecer intacta e ativa
        self.assertEqual(order2.status, ClosingOrderStatus.ACTIVE)
        self.assertEqual(order2.remaining_quantity, 2000)

        # Auditoria deve conter o registro formal do cancelamento rejeitado
        self.assertEqual(len(engine.rejected_cancellations), 1)
        audit = engine.rejected_cancellations[0]
        self.assertEqual(audit["order_id"], "ORD-02")
        self.assertEqual(audit["rejection_reason"], "REJECTED_FREEZE_WINDOW_ACTIVE")
        self.assertEqual(audit["law_reference"], "LAW-104A")

        # 4. Transição para Random Extension continua bloqueando cancelamentos
        engine.enter_random_extension()
        self.assertEqual(engine.phase, AuctionPhase.RANDOM_EXTENSION)
        with self.assertRaises(PumaFreezeWindowViolationError):
            engine.cancel_order("ORD-02", timestamp_ms=1731000002000)
        self.assertEqual(len(engine.rejected_cancellations), 2)

    def test_law_104a_modifications_during_freeze(self) -> None:
        """Valida que reduções de quantidade ou piora de preço são proibidas no Freeze Window."""
        engine = PumaClosingCrossEngine("PETR4", Decimal("35.00"))
        buy_order = ClosingOrder(
            order_id="BUY-01",
            symbol="PETR4",
            side=ClosingOrderSide.BUY,
            order_type=ClosingOrderType.LOC,
            quantity=5000,
            price=Decimal("35.20"),
        )
        sell_order = ClosingOrder(
            order_id="SELL-01",
            symbol="PETR4",
            side=ClosingOrderSide.SELL,
            order_type=ClosingOrderType.LOC,
            quantity=5000,
            price=Decimal("34.80"),
        )
        engine.add_order(buy_order)
        engine.add_order(sell_order)

        engine.enter_freeze_period(timestamp_ms=1731000000000)

        # Redução de quantidade deve falhar
        with self.assertRaises(PumaFreezeWindowViolationError):
            engine.modify_order("BUY-01", new_quantity=3000)

        # Redução de preço de compra (piora) deve falhar
        with self.assertRaises(PumaFreezeWindowViolationError):
            engine.modify_order("BUY-01", new_price=Decimal("35.10"))

        # Aumento de preço de venda (piora) deve falhar
        with self.assertRaises(PumaFreezeWindowViolationError):
            engine.modify_order("SELL-01", new_price=Decimal("34.90"))

        # Aumento de quantidade durante o freeze é permitido
        engine.modify_order("BUY-01", new_quantity=7000)
        self.assertEqual(buy_order.quantity, 7000)

    # --------------------------------------------------------------------------
    # 3. Invariante LAW-104B: Prioridade Absoluta MOC sobre LOC
    # --------------------------------------------------------------------------

    def test_law_104b_moc_absolute_priority_over_earlier_aggressive_loc(self) -> None:
        """Garante que MOC tem prioridade absoluta sobre ordem LOC, mesmo que a LOC tenha
        chegado antes e com preço ultra-agressivo."""
        engine = PumaClosingCrossEngine("VALE3", Decimal("60.00"))

        # Oferta vendedora com liquidez restrita (5.000 lotes a 60.00)
        sell_moc = ClosingOrder(
            order_id="SELL-MOC",
            symbol="VALE3",
            side=ClosingOrderSide.SELL,
            order_type=ClosingOrderType.MOC,
            quantity=5000,
            timestamp_ms=100,
        )
        engine.add_order(sell_moc)

        # LOC Compra: chegou cedo (t=50), preço super agressivo 65.00, volume 5.000
        loc_buy = ClosingOrder(
            order_id="BUY-LOC-EARLY-AGGRESSIVE",
            symbol="VALE3",
            side=ClosingOrderSide.BUY,
            order_type=ClosingOrderType.LOC,
            quantity=5000,
            price=Decimal("65.00"),
            timestamp_ms=50,
        )
        # MOC Compra: chegou mais tarde (t=200), volume 5.000
        moc_buy = ClosingOrder(
            order_id="BUY-MOC-LATE",
            symbol="VALE3",
            side=ClosingOrderSide.BUY,
            order_type=ClosingOrderType.MOC,
            quantity=5000,
            timestamp_ms=200,
        )

        engine.add_order(loc_buy)
        engine.add_order(moc_buy)

        # Executa uncross
        ptp = engine.execute_uncross()
        # Ambos os preços candidatos 60.00 e 65.00 têm volume 5.000 e excesso comprador (+5.000)
        # Regra 3 (pressão compradora) seleciona o maior preço (65.00)
        self.assertEqual(ptp.uncross_price, Decimal("65.00"))

        # Como a ponta vendedora só tinha 5.000 ações,
        # a ordem BUY-MOC DEVE consumir integralmente os 5.000 lotes!
        # A ordem BUY-LOC não pode receber fill nenhum (executed_quantity == 0)
        self.assertEqual(moc_buy.executed_quantity, 5000)
        self.assertEqual(moc_buy.remaining_quantity, 0)
        self.assertEqual(moc_buy.status, ClosingOrderStatus.FILLED)

        self.assertEqual(loc_buy.executed_quantity, 0)
        self.assertEqual(loc_buy.remaining_quantity, 5000)
        self.assertEqual(loc_buy.status, ClosingOrderStatus.ACTIVE)

        # Valida invariante LAW-104B
        inv = engine.verify_invariants()
        self.assertTrue(inv["law_104b_moc_priority"])

    def test_law_104b_fifo_within_same_moc_tier(self) -> None:
        """Garante ordenamento FIFO estrito entre múltiplas ordens MOC do mesmo lado."""
        engine = PumaClosingCrossEngine("BBAS3", Decimal("28.00"))

        # Vendedor de 15.000
        engine.add_order(ClosingOrder("SELL-MOC", "BBAS3", ClosingOrderSide.SELL, ClosingOrderType.MOC, 15000, timestamp_ms=500))

        # Compradores MOC em tempos diferentes
        moc1 = ClosingOrder("BUY-MOC-1", "BBAS3", ClosingOrderSide.BUY, ClosingOrderType.MOC, 10000, timestamp_ms=100)
        moc2 = ClosingOrder("BUY-MOC-2", "BBAS3", ClosingOrderSide.BUY, ClosingOrderType.MOC, 10000, timestamp_ms=200)
        engine.add_order(moc1)
        engine.add_order(moc2)

        engine.execute_uncross()

        # moc1 chegou antes (t=100): 10.000 preenchidos
        self.assertEqual(moc1.executed_quantity, 10000)
        self.assertEqual(moc1.status, ClosingOrderStatus.FILLED)

        # moc2 chegou depois (t=200): recebe os 5.000 restantes
        self.assertEqual(moc2.executed_quantity, 5000)
        self.assertEqual(moc2.remaining_quantity, 5000)
        self.assertEqual(moc2.status, ClosingOrderStatus.PARTIALLY_FILLED)

        inv = engine.verify_invariants()
        self.assertTrue(inv["law_104b_moc_priority"])
        self.assertTrue(inv["law_104c_volume_conservation"])

    # --------------------------------------------------------------------------
    # 4. Invariante LAW-104C: Conservação de Volume e Preço Único
    # --------------------------------------------------------------------------

    def test_law_104c_strict_volume_and_price_conservation(self) -> None:
        """Testa conservação rigorosa com combinações de MOC, LOC, cancelamentos e sobras."""
        engine = PumaClosingCrossEngine("ITUB4", Decimal("32.50"))

        # Inserção de ordens
        engine.add_order(ClosingOrder("B1", "ITUB4", ClosingOrderSide.BUY, ClosingOrderType.MOC, 10000, timestamp_ms=10))
        engine.add_order(ClosingOrder("B2", "ITUB4", ClosingOrderSide.BUY, ClosingOrderType.LOC, 15000, Decimal("32.60"), timestamp_ms=20))
        engine.add_order(ClosingOrder("B3", "ITUB4", ClosingOrderSide.BUY, ClosingOrderType.LOC, 20000, Decimal("32.40"), timestamp_ms=30))
        engine.add_order(ClosingOrder("B-CANCEL", "ITUB4", ClosingOrderSide.BUY, ClosingOrderType.LOC, 5000, Decimal("32.55"), timestamp_ms=40))

        engine.add_order(ClosingOrder("S1", "ITUB4", ClosingOrderSide.SELL, ClosingOrderType.MOC, 12000, timestamp_ms=15))
        engine.add_order(ClosingOrder("S2", "ITUB4", ClosingOrderSide.SELL, ClosingOrderType.LOC, 8000, Decimal("32.50"), timestamp_ms=25))
        engine.add_order(ClosingOrder("S3", "ITUB4", ClosingOrderSide.SELL, ClosingOrderType.LOC, 30000, Decimal("32.70"), timestamp_ms=35))

        # Cancelamento antes do freeze
        engine.cancel_order("B-CANCEL")

        # Entrada no freeze
        engine.enter_freeze_period(timestamp_ms=100)

        # Execução
        # Entre 32.50 e 32.60 há empate de volume (20k) e de desbalanceamento (+5k compra)
        # Regra 3 (pressão compradora) seleciona o maior preço: 32.60
        ptp = engine.execute_uncross()
        self.assertEqual(ptp.uncross_price, Decimal("32.60"))

        inv = engine.verify_invariants()
        self.assertTrue(inv["all_invariants_hold"])
        self.assertTrue(inv["law_104c_volume_conservation"])
        self.assertTrue(inv["ptp_unique_price_verified"])

        # Todas as execuções devem ter preço exatamente igual a 32.60
        for f in engine.executions:
            self.assertEqual(f.executed_price, Decimal("32.60"))

    # --------------------------------------------------------------------------
    # 5. Regras Oficiais de Desempate B3 para Formação do Preço Teórico
    # --------------------------------------------------------------------------

    def test_b3_tie_break_rule1_max_volume(self) -> None:
        """Regra 1: Preço que maximiza estritamente o volume executável."""
        engine = PumaClosingCrossEngine("ELET3", Decimal("40.00"))
        # Compra limitada a 40.20 (volume 100)
        engine.add_order(ClosingOrder("B1", "ELET3", ClosingOrderSide.BUY, ClosingOrderType.LOC, 100, Decimal("40.20")))
        # Venda a 40.10 (volume 100) e a 40.20 (volume 50)
        engine.add_order(ClosingOrder("S1", "ELET3", ClosingOrderSide.SELL, ClosingOrderType.LOC, 100, Decimal("40.10")))
        engine.add_order(ClosingOrder("S2", "ELET3", ClosingOrderSide.SELL, ClosingOrderType.LOC, 50, Decimal("40.20")))

        # A 40.10: Buy=100, Sell=100 -> Vol=100
        # A 40.20: Buy=100, Sell=150 -> Vol=100
        # Empate de volume (100) entre 40.10 e 40.20.
        # Desequilíbrio a 40.10: |100 - 100| = 0
        # Desequilíbrio a 40.20: |100 - 150| = 50
        # Menor desequilíbrio escolhe 40.10 (Regra 2)
        summary = engine.calculate_theoretical_price()
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary.uncross_price, Decimal("40.10"))
        self.assertEqual(summary.rule_applied, "MIN_IMBALANCE")

    def test_b3_tie_break_rule3_pure_buy_pressure(self) -> None:
        """Regra 3: Empate de volume e desbalanceamento com excesso puro de compra escolhe maior preço."""
        engine = PumaClosingCrossEngine("TEST1", Decimal("50.00"))
        # Compras com excesso
        engine.add_order(ClosingOrder("B1", "TEST1", ClosingOrderSide.BUY, ClosingOrderType.LOC, 150, Decimal("52.00")))
        # Vendas a 51.00 (100)
        engine.add_order(ClosingOrder("S1", "TEST1", ClosingOrderSide.SELL, ClosingOrderType.LOC, 100, Decimal("51.00")))

        # Preços candidatos: 50.00 (ref), 51.00, 52.00
        # A 51.00: Buy=150, Sell=100 -> Vol=100, Imbalance=+50 (Compra)
        # A 52.00: Buy=150, Sell=100 -> Vol=100, Imbalance=+50 (Compra)
        # Ambos com volume 100 e imbalance 50 (ambos compra).
        # Pressão compradora escolhe o maior preço: 52.00!
        summary = engine.calculate_theoretical_price()
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary.uncross_price, Decimal("52.00"))
        self.assertEqual(summary.rule_applied, "MARKET_PRESSURE")

    def test_b3_tie_break_rule3_pure_sell_pressure(self) -> None:
        """Regra 3: Empate de volume e desbalanceamento com excesso puro de venda escolhe menor preço."""
        engine = PumaClosingCrossEngine("TEST2", Decimal("50.00"))
        # Compra a 51.00 (100)
        engine.add_order(ClosingOrder("B1", "TEST2", ClosingOrderSide.BUY, ClosingOrderType.LOC, 100, Decimal("51.00")))
        # Venda a 49.00 (150)
        engine.add_order(ClosingOrder("S1", "TEST2", ClosingOrderSide.SELL, ClosingOrderType.LOC, 150, Decimal("49.00")))

        # A 49.00: Buy=100, Sell=150 -> Vol=100, Imbalance=-50 (Venda)
        # A 50.00: Buy=100, Sell=150 -> Vol=100, Imbalance=-50 (Venda)
        # A 51.00: Buy=100, Sell=150 -> Vol=100, Imbalance=-50 (Venda)
        # Pressão vendedora pura: escolhe menor preço (49.00)!
        summary = engine.calculate_theoretical_price()
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary.uncross_price, Decimal("49.00"))
        self.assertEqual(summary.rule_applied, "MARKET_PRESSURE")

    def test_b3_tie_break_rule3_proximity_to_reference(self) -> None:
        """Regra 3: Desequilíbrio zero escolhe o preço mais próximo da referência."""
        engine = PumaClosingCrossEngine("TEST3", Decimal("50.00"))
        # Compra a 52.00 (100)
        engine.add_order(ClosingOrder("B1", "TEST3", ClosingOrderSide.BUY, ClosingOrderType.LOC, 100, Decimal("52.00")))
        # Venda a 48.00 (100)
        engine.add_order(ClosingOrder("S1", "TEST3", ClosingOrderSide.SELL, ClosingOrderType.LOC, 100, Decimal("48.00")))

        # No intervalo 48.00 a 52.00, volume é sempre 100 e desequilíbrio é sempre 0.
        # Os preços candidatos são 48.00, 50.00 (ref), 52.00.
        # Mais próximo de 50.00 é o próprio 50.00!
        summary = engine.calculate_theoretical_price()
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary.uncross_price, Decimal("50.00"))
        self.assertEqual(summary.rule_applied, "REF_PRICE_PROXIMITY")

    # --------------------------------------------------------------------------
    # 6. Casos Especiais e Tratamento de Erros
    # --------------------------------------------------------------------------

    def test_only_moc_orders_cross_at_reference_price(self) -> None:
        """Quando existem apenas ordens MOC em ambas as pontas, o leilão uncrossa ao preço de referência."""
        engine = PumaClosingCrossEngine("MOC_ONLY", Decimal("100.00"))
        engine.add_order(ClosingOrder("B-MOC", "MOC_ONLY", ClosingOrderSide.BUY, ClosingOrderType.MOC, 5000))
        engine.add_order(ClosingOrder("S-MOC", "MOC_ONLY", ClosingOrderSide.SELL, ClosingOrderType.MOC, 4000))

        summary = engine.calculate_theoretical_price()
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary.uncross_price, Decimal("100.00"))
        self.assertEqual(summary.executable_volume, 4000)

        ptp = engine.execute_uncross()
        self.assertEqual(ptp.uncross_price, Decimal("100.00"))
        self.assertEqual(len(engine.executions), 1)
        self.assertEqual(engine.executions[0].executed_quantity, 4000)
        self.assertEqual(engine.executions[0].executed_price, Decimal("100.00"))

        inv = engine.verify_invariants()
        self.assertTrue(inv["all_invariants_hold"])

    def test_no_liquidity_or_no_crossing_price(self) -> None:
        """Sem cruzamento possível (compra máxima menor que venda mínima)."""
        engine = PumaClosingCrossEngine("NOCROSS", Decimal("10.00"))
        engine.add_order(ClosingOrder("B1", "NOCROSS", ClosingOrderSide.BUY, ClosingOrderType.LOC, 100, Decimal("9.00")))
        engine.add_order(ClosingOrder("S1", "NOCROSS", ClosingOrderSide.SELL, ClosingOrderType.LOC, 100, Decimal("11.00")))

        summary = engine.calculate_theoretical_price()
        self.assertIsNone(summary)

        with self.assertRaises(PumaClosingCrossError):
            engine.execute_uncross()

    def test_validation_errors(self) -> None:
        """Validação de parâmetros de ordens e estados inválidos."""
        engine = PumaClosingCrossEngine("PETR4", Decimal("35.00"))

        # Quantidade <= 0
        with self.assertRaises(PumaClosingCrossError):
            ClosingOrder("O1", "PETR4", ClosingOrderSide.BUY, ClosingOrderType.MOC, 0)

        # Preço nulo em LOC
        with self.assertRaises(PumaClosingCrossError):
            ClosingOrder("O2", "PETR4", ClosingOrderSide.BUY, ClosingOrderType.LOC, 100, None)

        # Símbolo incompatível
        with self.assertRaises(PumaClosingCrossError):
            engine.add_order(ClosingOrder("O3", "VALE3", ClosingOrderSide.BUY, ClosingOrderType.MOC, 100))

        # Ordem duplicada
        order = ClosingOrder("O4", "PETR4", ClosingOrderSide.BUY, ClosingOrderType.MOC, 100)
        engine.add_order(order)
        with self.assertRaises(PumaClosingCrossError):
            engine.add_order(order)

    # --------------------------------------------------------------------------
    # 7. Serialização Roundtrip (to_dict / from_dict)
    # --------------------------------------------------------------------------

    def test_serialization_roundtrip(self) -> None:
        """Testa serialização completa e reconstrução de estado do motor."""
        engine = PumaClosingCrossEngine.load_from_fixture(self.fixture_file)
        engine.enter_freeze_period(timestamp_ms=1731000000000)

        # Tentativa de cancelamento rejeitada
        try:
            engine.cancel_order("ORD-LOC-BUY-002", timestamp_ms=1731000001000)
        except PumaFreezeWindowViolationError:
            pass

        # Executa uncross
        engine.execute_uncross()

        # Serializa para dict e JSON
        dict_data = engine.to_dict()
        json_str = json.dumps(dict_data)
        loaded_data = json.loads(json_str)

        # Reconstrói a partir do dict
        reconstructed = PumaClosingCrossEngine.from_dict(loaded_data)
        self.assertEqual(reconstructed.symbol, engine.symbol)
        self.assertEqual(reconstructed.phase, AuctionPhase.CLOSED)
        self.assertEqual(len(reconstructed.orders), len(engine.orders))
        self.assertEqual(len(reconstructed.executions), len(engine.executions))
        self.assertEqual(len(reconstructed.rejected_cancellations), 1)

        # Invariantes no motor reconstruído
        inv = reconstructed.verify_invariants()
        self.assertTrue(inv["all_invariants_hold"])
        self.assertTrue(inv["law_104a_freeze_inviolability"])
        self.assertTrue(inv["law_104b_moc_priority"])
        self.assertTrue(inv["law_104c_volume_conservation"])

    def test_assert_invariants_formal_bridge(self) -> None:
        """Testa método assert_invariants() e integração formal com src.invariants."""
        engine = PumaClosingCrossEngine.load_from_fixture(self.fixture_file)
        engine.enter_freeze_period(timestamp_ms=1000)
        engine.execute_uncross()
        # Não deve lançar InvariantViolationError
        engine.assert_invariants()


    # --------------------------------------------------------------------------
    # 8. Testes Negativos de Invariantes (Detecção Ativa de Violações)
    # --------------------------------------------------------------------------

    def test_law_104a_violation_detection(self) -> None:
        """Garante que se uma ordem fosse indevidamente cancelada durante o freeze,
        verify_invariants reporta falsidade para LAW-104A."""
        engine = PumaClosingCrossEngine("PETR4", Decimal("35.00"))
        ord1 = ClosingOrder("O1", "PETR4", ClosingOrderSide.BUY, ClosingOrderType.LOC, 1000, Decimal("35.00"))
        engine.add_order(ord1)
        engine.enter_freeze_period(timestamp_ms=1000)

        # Simula violação forçada (ex: bypass interno ou bug de concorrência)
        engine._cancelled_during_freeze_count = 1
        inv = engine.verify_invariants()
        self.assertFalse(inv["all_invariants_hold"])
        self.assertFalse(inv["law_104a_freeze_inviolability"])

    def test_law_104b_violation_detection(self) -> None:
        """Garante que se uma ordem LOC recebesse fill com MOC não atendida,
        verify_invariants detecta a quebra da prioridade LAW-104B."""
        engine = PumaClosingCrossEngine("PETR4", Decimal("35.00"))
        moc_buy = ClosingOrder("MOC-B", "PETR4", ClosingOrderSide.BUY, ClosingOrderType.MOC, 1000)
        loc_buy = ClosingOrder("LOC-B", "PETR4", ClosingOrderSide.BUY, ClosingOrderType.LOC, 1000, Decimal("35.00"))
        engine.add_order(moc_buy)
        engine.add_order(loc_buy)

        # Simula violação da prioridade: LOC executou 500, mas MOC ainda tem 1000 residual
        loc_buy.executed_quantity = 500
        inv = engine.verify_invariants()
        self.assertFalse(inv["all_invariants_hold"])
        self.assertFalse(inv["law_104b_moc_priority"])

    def test_law_104c_volume_and_price_tampering_detection(self) -> None:
        """Garante que violação de conservação de volume ou preço divergente é detectada."""
        engine = PumaClosingCrossEngine.load_from_fixture(self.fixture_file)
        engine.execute_uncross()

        # Simula adulteração de volume (criação de lotes fantasma)
        engine.orders["ORD-MOC-BUY-001"].executed_quantity += 100
        inv = engine.verify_invariants()
        self.assertFalse(inv["all_invariants_hold"])
        self.assertFalse(inv["law_104c_volume_conservation"])

        # Restaura e simula preço de fill divergente
        engine.orders["ORD-MOC-BUY-001"].executed_quantity -= 100
        bad_fill = PumaClosingExecutionFill(
            fill_id="FILL-BAD",
            symbol="PETR4",
            buy_order_id="B",
            sell_order_id="S",
            buy_order_type=ClosingOrderType.LOC,
            sell_order_type=ClosingOrderType.LOC,
            executed_price=Decimal("35.50"), # Diferente do PTP 35.00
            executed_quantity=10,
            timestamp_ms=1000,
        )
        engine.executions.append(bad_fill)
        inv2 = engine.verify_invariants()
        self.assertFalse(inv2["all_invariants_hold"])
        self.assertFalse(inv2["ptp_unique_price_verified"])

    def test_b3_tie_break_equidistant_reference_prices(self) -> None:
        """Quando dois preços são perfeitamente equidistantes da referência com desequilíbrio nulo,
        a convenção oficial B3 adota o preço superior."""
        engine = PumaClosingCrossEngine("EQUI", Decimal("50.00"))
        # Compra a 51.00 (100 lotes)
        engine.add_order(ClosingOrder("B1", "EQUI", ClosingOrderSide.BUY, ClosingOrderType.LOC, 100, Decimal("51.00")))
        # Venda a 49.00 (100 lotes)
        engine.add_order(ClosingOrder("S1", "EQUI", ClosingOrderSide.SELL, ClosingOrderType.LOC, 100, Decimal("49.00")))

        # Suponha que apenas 49.00 e 51.00 estejam no livro como candidatos:
        # A 49.00: Vol=100, Imb=0
        # A 51.00: Vol=100, Imb=0
        # Ambos equidistantes de 50.00 (|49-50|=1, |51-50|=1).
        # Porém 50.00 também é candidato (preço de referência).
        # A 50.00: Vol=100, Imb=0, dist=0. Escolhe 50.00!
        summary = engine.calculate_theoretical_price()
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary.uncross_price, Decimal("50.00"))


if __name__ == "__main__":
    unittest.main()
