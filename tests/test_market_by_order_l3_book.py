"""Bateria de Testes Unitários e de Integração — Reconstituição Livro L3 MBO B3 (FL-091).

Valida:
1. Modelagem de eventos granulares MBO (OrderAdd, OrderModify, OrderCancel, OrderExecute, OrderDelete).
2. Prioridade Estrita Preço-Tempo (FIFO) em cada nível de preço:
   - Redução de volume mantém fila temporal.
   - Aumento de volume perde prioridade e move para o final da fila de mesmo preço.
   - Alteração de preço realoca a ordem ao final da fila do novo nível.
3. Top of Book (BBO), Spread bid-ask, Preço Médio e Micro-preço ponderado por volume.
4. Profundidade L2 agregada por nível de preço (Market By Price).
5. Detecção de Crossed/Locked book e proteção fail-safe em modo de Negociação Contínua.
6. Leis Invariantes Formais:
   - Conservação de Lotes: Lotes_Atuais == Lotes_Adicionados - Lotes_Executados - Lotes_Cancelados.
   - Não-Cruzamento em Mercado Contínuo.
   - Monotonicidade de Preços e Consistência de Índices Globais.
7. Codec Binário Bidirecional Little-Endian (ITCH / SBE B3 UMDF) e decodificação de streams.
8. Replay determinístico de cenários a partir de fixtures JSON oficiais.
"""

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import struct
import sys
import unittest

# Garante a raiz do projeto no PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.market_by_order_l3_book import (
    BBO,
    CrossedBookError,
    DuplicateOrderError,
    InvalidOrderError,
    L2LevelView,
    L3Order,
    MarketByOrderEngine,
    MarketByOrderL3Book,
    MboBinaryCodec,
    MboCodecError,
    MboEventType,
    MboInvariantViolationError,
    OrderAddEvent,
    OrderCancelEvent,
    OrderDeleteEvent,
    OrderExecuteEvent,
    OrderModifyEvent,
    OrderNotFoundError,
    OrderSide,
    PriceLevel,
    TradeRecord,
    TradingMode,
    scale_price_to_int,
    to_decimal_price,
    unscale_int_to_price,
)

FIXTURE_PATH = REPO_ROOT / "fixtures" / "market_by_order_l3_book.json"


class TestMboDataModels(unittest.TestCase):
    """Testa os enums, eventos e modelos fundamentais do livro MBO."""

    def test_order_side_conversion(self) -> None:
        self.assertEqual(OrderSide.from_code("B"), OrderSide.BID)
        self.assertEqual(OrderSide.from_code("BUY"), OrderSide.BID)
        self.assertEqual(OrderSide.from_code(0), OrderSide.BID)
        self.assertEqual(OrderSide.from_code(b"B"), OrderSide.BID)

        self.assertEqual(OrderSide.from_code("S"), OrderSide.ASK)
        self.assertEqual(OrderSide.from_code("SELL"), OrderSide.ASK)
        self.assertEqual(OrderSide.from_code(1), OrderSide.ASK)
        self.assertEqual(OrderSide.from_code(b"S"), OrderSide.ASK)

        self.assertEqual(OrderSide.BID.opposite, OrderSide.ASK)
        self.assertEqual(OrderSide.ASK.opposite, OrderSide.BID)

        with self.assertRaises(ValueError):
            OrderSide.from_code("INVALID")

    def test_event_validations(self) -> None:
        # Preço <= 0 deve falhar
        with self.assertRaises(InvalidOrderError):
            OrderAddEvent(
                order_id=1,
                security_id=10,
                symbol="VALE3",
                side=OrderSide.BID,
                price=Decimal("-1.00"),
                quantity=100,
                sequence_number=1,
                timestamp=datetime.now(timezone.utc),
            )

        # Quantidade <= 0 deve falhar
        with self.assertRaises(InvalidOrderError):
            OrderAddEvent(
                order_id=1,
                security_id=10,
                symbol="VALE3",
                side=OrderSide.BID,
                price=Decimal("10.00"),
                quantity=0,
                sequence_number=1,
                timestamp=datetime.now(timezone.utc),
            )

        # OrderID <= 0 deve falhar
        with self.assertRaises(InvalidOrderError):
            OrderModifyEvent(order_id=0, security_id=10, symbol="VALE3", new_quantity=100)

        with self.assertRaises(InvalidOrderError):
            OrderCancelEvent(order_id=1, security_id=10, symbol="VALE3", canceled_quantity=-5)

        with self.assertRaises(InvalidOrderError):
            OrderExecuteEvent(
                order_id=1,
                security_id=10,
                symbol="VALE3",
                executed_quantity=0,
                execution_price=Decimal("10.00"),
                trade_id="TRD_1",
            )


class TestPriceLevelAndFifo(unittest.TestCase):
    """Testa o comportamento de fila temporal FIFO em um nível de preço individual."""

    def setUp(self) -> None:
        self.level = PriceLevel(price=Decimal("50.00"), side=OrderSide.BID)

    def test_fifo_order_insertion(self) -> None:
        ts1 = datetime(2026, 11, 5, 10, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 11, 5, 10, 0, 1, tzinfo=timezone.utc)

        o1 = L3Order(101, 1, "PETR4", OrderSide.BID, Decimal("50.00"), 100, 100, ts1, 1)
        o2 = L3Order(102, 1, "PETR4", OrderSide.BID, Decimal("50.00"), 200, 200, ts2, 2)

        self.level.add_order(o1)
        self.level.add_order(o2)

        self.assertEqual(self.level.order_count, 2)
        self.assertEqual(self.level.total_quantity, 300)
        self.assertEqual(self.level.get_front_order().order_id, 101)

    def test_reduction_maintains_queue_priority(self) -> None:
        ts = datetime.now(timezone.utc)
        o1 = L3Order(101, 1, "PETR4", OrderSide.BID, Decimal("50.00"), 100, 100, ts, 1)
        o2 = L3Order(102, 1, "PETR4", OrderSide.BID, Decimal("50.00"), 200, 200, ts, 2)
        self.level.add_order(o1)
        self.level.add_order(o2)

        # Redução da ordem 1: volume cai de 100 para 60 (redução de 40)
        self.level.reduce_order_quantity(101, 40)
        self.assertEqual(o1.quantity, 60)
        self.assertEqual(self.level.total_quantity, 260)
        # Ordem 101 AINDA é a primeira da fila!
        self.assertEqual(self.level.get_front_order().order_id, 101)

    def test_increase_moves_to_end_of_queue(self) -> None:
        ts1 = datetime(2026, 11, 5, 10, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 11, 5, 10, 0, 1, tzinfo=timezone.utc)
        ts3 = datetime(2026, 11, 5, 10, 0, 2, tzinfo=timezone.utc)

        o1 = L3Order(101, 1, "PETR4", OrderSide.BID, Decimal("50.00"), 100, 100, ts1, 1)
        o2 = L3Order(102, 1, "PETR4", OrderSide.BID, Decimal("50.00"), 200, 200, ts2, 2)
        self.level.add_order(o1)
        self.level.add_order(o2)

        # Aumento da ordem 1: volume sobe de 100 para 150 (aumento de 50)
        self.level.increase_order_quantity(101, 50, new_timestamp=ts3, new_sequence=3)
        self.assertEqual(o1.quantity, 150)
        self.assertEqual(self.level.total_quantity, 350)
        # Ordem 101 PERDEU prioridade: ordem 102 agora é a primeira da fila!
        self.assertEqual(self.level.get_front_order().order_id, 102)
        # Ordem 101 agora é a última da fila
        orders_keys = list(self.level.orders.keys())
        self.assertEqual(orders_keys, [102, 101])


class TestMarketByOrderL3BookOperations(unittest.TestCase):
    """Testa todas as operações do motor de livro L3."""

    def setUp(self) -> None:
        self.book = MarketByOrderL3Book(security_id=105234, symbol="PETR4")

    def test_order_add_and_duplicates(self) -> None:
        self.book.apply_order_add(1, "BID", "35.00", 100, sequence_number=1)
        self.assertEqual(self.book.total_orders_count, 1)
        self.assertEqual(self.book.total_current_lots, 100)

        # Duplicata deve falhar
        with self.assertRaises(DuplicateOrderError):
            self.book.apply_order_add(1, "BID", "35.00", 50, sequence_number=2)

    def test_order_modify_reduction_and_increase(self) -> None:
        # Cria três ordens no mesmo nível
        self.book.apply_order_add(10, "BID", "30.00", 100, sequence_number=1)
        self.book.apply_order_add(11, "BID", "30.00", 200, sequence_number=2)
        self.book.apply_order_add(12, "BID", "30.00", 300, sequence_number=3)

        # Redução na ordem 10: de 100 para 80
        self.book.apply_order_modify(10, new_quantity=80, sequence_number=4)
        lvl = self.book.get_price_level("BID", "30.00")
        self.assertEqual(list(lvl.orders.keys()), [10, 11, 12])
        self.assertEqual(lvl.total_quantity, 580)

        # Aumento na ordem 10: de 80 para 150 -> perde prioridade para o final
        self.book.apply_order_modify(10, new_quantity=150, sequence_number=5)
        self.assertEqual(list(lvl.orders.keys()), [11, 12, 10])
        self.assertEqual(lvl.total_quantity, 650)

    def test_order_modify_price_relocation(self) -> None:
        self.book.apply_order_add(20, "BID", "30.00", 100, sequence_number=1)
        self.book.apply_order_add(21, "BID", "31.00", 200, sequence_number=2)

        # Move ordem 20 de 30.00 para 31.00 com 150 lotes
        self.book.apply_order_modify(20, new_quantity=150, new_price="31.00", sequence_number=3)

        # Nível 30.00 deve ser excluído por ter ficado vazio
        self.assertIsNone(self.book.get_price_level("BID", "30.00"))

        # Nível 31.00 agora tem ordem 21 seguida de 20
        lvl31 = self.book.get_price_level("BID", "31.00")
        self.assertIsNotNone(lvl31)
        self.assertEqual(list(lvl31.orders.keys()), [21, 20])
        self.assertEqual(lvl31.total_quantity, 350)

    def test_order_cancel_partial_and_full(self) -> None:
        self.book.apply_order_add(30, "ASK", "45.00", 500, sequence_number=1)
        self.book.apply_order_add(31, "ASK", "45.00", 300, sequence_number=2)

        # Cancelamento parcial de 200 da ordem 30
        res = self.book.apply_order_cancel(30, canceled_quantity=200, sequence_number=3)
        self.assertIsNotNone(res)
        self.assertEqual(res.quantity, 300)
        self.assertEqual(self.book.total_current_lots, 600)

        # Ordem 30 ainda é a primeira da fila
        lvl = self.book.get_price_level("ASK", "45.00")
        self.assertEqual(lvl.get_front_order().order_id, 30)

        # Cancelamento total da ordem 30
        res_full = self.book.apply_order_cancel(30, sequence_number=4)
        self.assertIsNone(res_full)
        self.assertIsNone(self.book.get_order(30))
        self.assertEqual(self.book.total_orders_count, 1)

    def test_order_execute_partial_and_full(self) -> None:
        self.book.apply_order_add(40, "BID", "25.00", 1000, sequence_number=1)

        # Execução parcial de 400
        trd1 = self.book.apply_order_execute(40, executed_quantity=400, trade_id="T1", sequence_number=2)
        self.assertEqual(trd1.quantity, 400)
        self.assertEqual(self.book.get_order(40).quantity, 600)
        self.assertEqual(self.book.total_current_lots, 600)

        # Execução restante de 600
        trd2 = self.book.apply_order_execute(40, executed_quantity=600, trade_id="T2", sequence_number=3)
        self.assertEqual(trd2.quantity, 600)
        self.assertIsNone(self.book.get_order(40))
        self.assertIsNone(self.book.get_price_level("BID", "25.00"))
        self.assertEqual(self.book.total_current_lots, 0)

    def test_order_delete(self) -> None:
        self.book.apply_order_add(50, "ASK", "60.00", 500, sequence_number=1)
        del_order = self.book.apply_order_delete(50, sequence_number=2)
        self.assertEqual(del_order.order_id, 50)
        self.assertIsNone(self.book.get_order(50))
        self.assertEqual(self.book.total_current_lots, 0)


class TestBboAndMicrostructureMetrics(unittest.TestCase):
    """Testa cálculo de BBO, spread, mid price, micro-preço e profundidade L2."""

    def setUp(self) -> None:
        self.book = MarketByOrderL3Book(security_id=1, symbol="PETR4")

    def test_empty_and_one_sided_book(self) -> None:
        bbo = self.book.get_bbo()
        self.assertIsNone(bbo.bid_price)
        self.assertIsNone(bbo.ask_price)
        self.assertIsNone(bbo.spread)

        # Apenas BID
        self.book.apply_order_add(1, "BID", "30.00", 100, sequence_number=1)
        bbo = self.book.get_bbo()
        self.assertEqual(bbo.bid_price, Decimal("30.00"))
        self.assertIsNone(bbo.ask_price)
        self.assertIsNone(bbo.spread)

    def test_bbo_spread_and_micro_price(self) -> None:
        self.book.apply_order_add(1, "BID", "30.00", 1000, sequence_number=1)
        self.book.apply_order_add(2, "ASK", "30.10", 3000, sequence_number=2)

        bbo = self.book.get_bbo()
        self.assertEqual(bbo.bid_price, Decimal("30.00"))
        self.assertEqual(bbo.ask_price, Decimal("30.10"))
        self.assertEqual(bbo.spread, Decimal("0.10"))
        self.assertEqual(bbo.mid_price, Decimal("30.05"))

        # Micro-preço ponderado: (30.00 * 3000 + 30.10 * 1000) / 4000 = (90000 + 30100) / 4000 = 120100 / 4000 = 30.025
        expected_micro = (Decimal("30.00") * 3000 + Decimal("30.10") * 1000) / Decimal("4000")
        self.assertEqual(bbo.micro_price, expected_micro)

    def test_l2_depth_sorting(self) -> None:
        # Bids em 29.90, 30.00, 29.80
        self.book.apply_order_add(1, "BID", "29.90", 500, sequence_number=1)
        self.book.apply_order_add(2, "BID", "30.00", 1000, sequence_number=2)
        self.book.apply_order_add(3, "BID", "29.80", 1500, sequence_number=3)

        # Asks em 30.20, 30.10, 30.30
        self.book.apply_order_add(4, "ASK", "30.20", 400, sequence_number=4)
        self.book.apply_order_add(5, "ASK", "30.10", 800, sequence_number=5)
        self.book.apply_order_add(6, "ASK", "30.30", 1200, sequence_number=6)

        depth = self.book.get_l2_depth(depth=10)

        # Bids devem ser decrescentes: 30.00, 29.90, 29.80
        bid_prices = [Decimal(lvl["price"]) for lvl in depth["bids"]]
        self.assertEqual(bid_prices, [Decimal("30.00"), Decimal("29.90"), Decimal("29.80")])

        # Asks devem ser crescentes: 30.10, 30.20, 30.30
        ask_prices = [Decimal(lvl["price"]) for lvl in depth["asks"]]
        self.assertEqual(ask_prices, [Decimal("30.10"), Decimal("30.20"), Decimal("30.30")])


class TestCrossedAndLockedBook(unittest.TestCase):
    """Testa detecção e validação de Crossed e Locked Book sob diferentes modos de mercado."""

    def test_continuous_mode_rejects_crossed_book(self) -> None:
        book = MarketByOrderL3Book(security_id=1, symbol="PETR4", trading_mode=TradingMode.CONTINUOUS)
        book.apply_order_add(1, "ASK", "30.00", 100, sequence_number=1)

        # Inserção de BID >= ASK em modo contínuo DEVE levantar CrossedBookError
        with self.assertRaises(CrossedBookError):
            book.apply_order_add(2, "BID", "30.50", 100, sequence_number=2)

    def test_auction_mode_allows_crossed_book(self) -> None:
        book = MarketByOrderL3Book(security_id=1, symbol="PETR4", trading_mode=TradingMode.AUCTION)
        book.apply_order_add(1, "ASK", "30.00", 100, sequence_number=1)
        # Em leilão, não levanta exceção
        book.apply_order_add(2, "BID", "30.50", 100, sequence_number=2)

        self.assertTrue(book.is_crossed())
        invariants = book.verify_invariants(raise_on_error=False)
        self.assertTrue(invariants["order_conservation"])
        self.assertTrue(invariants["no_crossed_book_continuous"])  # Válido em AUCTION


class TestConservationInvariants(unittest.TestCase):
    """Auditoria estrita das Leis de Conservação de Ordens no Book (FL-091)."""

    def test_complete_lifecycle_conservation(self) -> None:
        book = MarketByOrderL3Book(security_id=10, symbol="VALE3")

        # 1. Adds
        book.apply_order_add(1, "BID", "60.00", 1000, sequence_number=1)
        book.apply_order_add(2, "BID", "59.90", 2000, sequence_number=2)
        book.apply_order_add(3, "ASK", "60.10", 3000, sequence_number=3)
        book.verify_invariants(raise_on_error=True)

        # 2. Modify: Redução (mantém prioridade)
        book.apply_order_modify(2, new_quantity=1500, sequence_number=4)
        book.verify_invariants(raise_on_error=True)

        # 3. Modify: Aumento (perde prioridade)
        book.apply_order_modify(1, new_quantity=1200, sequence_number=5)
        book.verify_invariants(raise_on_error=True)

        # 4. Execute parcial
        book.apply_order_execute(3, executed_quantity=1000, sequence_number=6)
        book.verify_invariants(raise_on_error=True)

        # 5. Cancel parcial
        book.apply_order_cancel(2, canceled_quantity=500, sequence_number=7)
        book.verify_invariants(raise_on_error=True)

        # 6. Delete
        book.apply_order_delete(1, sequence_number=8)
        book.verify_invariants(raise_on_error=True)

        # 7. Execute restante do ASK 3 (2000)
        book.apply_order_execute(3, executed_quantity=2000, sequence_number=9)
        book.verify_invariants(raise_on_error=True)

        # Sobrou apenas a ordem 2 com 1000 lotes (2000 - 500 red - 500 cancel = 1000)
        self.assertEqual(book.total_current_lots, 1000)
        ledger = book.ledger
        self.assertEqual(book.total_current_lots, ledger.expected_current_lots)


class TestBinaryCodec(unittest.TestCase):
    """Testa a codificação e decodificação binária de alta performance (ITCH/SBE)."""

    def test_roundtrip_order_add(self) -> None:
        now = datetime(2026, 11, 5, 12, 30, 45, 123000, tzinfo=timezone.utc)
        event = OrderAddEvent(
            order_id=987654321,
            security_id=105234,
            symbol="PETR4",
            side=OrderSide.BID,
            price=Decimal("35.50000000"),
            quantity=2500,
            sequence_number=101,
            timestamp=now,
        )

        encoded = MboBinaryCodec.encode_order_add(event)
        decoded, next_off = MboBinaryCodec.decode_message(encoded, default_symbol="PETR4")

        self.assertEqual(next_off, len(encoded))
        self.assertIsInstance(decoded, OrderAddEvent)
        self.assertEqual(decoded.order_id, event.order_id)
        self.assertEqual(decoded.security_id, event.security_id)
        self.assertEqual(decoded.side, event.side)
        self.assertEqual(decoded.price, event.price)
        self.assertEqual(decoded.quantity, event.quantity)
        self.assertEqual(decoded.sequence_number, event.sequence_number)

    def test_multi_message_stream_decoding(self) -> None:
        e1 = OrderAddEvent(1, 10, "PETR4", OrderSide.BID, Decimal("30.00"), 100, 1, datetime.now(timezone.utc))
        e2 = OrderModifyEvent(1, 10, "PETR4", 80, Decimal("30.00"), 2, datetime.now(timezone.utc))
        e3 = OrderExecuteEvent(1, 10, "PETR4", 50, Decimal("30.00"), "TRD_1", sequence_number=3)
        e4 = OrderCancelEvent(1, 10, "PETR4", 30, sequence_number=4)
        e5 = OrderDeleteEvent(1, 10, "PETR4", sequence_number=5)

        stream = (
            MboBinaryCodec.encode_event(e1)
            + MboBinaryCodec.encode_event(e2)
            + MboBinaryCodec.encode_event(e3)
            + MboBinaryCodec.encode_event(e4)
            + MboBinaryCodec.encode_event(e5)
        )

        decoded_events = MboBinaryCodec.decode_stream(stream, default_security_id=10, default_symbol="PETR4")
        self.assertEqual(len(decoded_events), 5)
        self.assertIsInstance(decoded_events[0], OrderAddEvent)
        self.assertIsInstance(decoded_events[1], OrderModifyEvent)
        self.assertIsInstance(decoded_events[2], OrderExecuteEvent)
        self.assertIsInstance(decoded_events[3], OrderCancelEvent)
        self.assertIsInstance(decoded_events[4], OrderDeleteEvent)


class TestMarketByOrderEngine(unittest.TestCase):
    """Testa o motor multi-ativo e despachador de eventos."""

    def test_engine_multi_asset_processing(self) -> None:
        engine = MarketByOrderEngine()
        ev1 = OrderAddEvent(1, 100, "PETR4", OrderSide.BID, Decimal("35.00"), 500, 1, datetime.now(timezone.utc))
        ev2 = OrderAddEvent(2, 200, "VALE3", OrderSide.ASK, Decimal("60.00"), 800, 2, datetime.now(timezone.utc))

        engine.process_event(ev1)
        engine.process_event(ev2)

        self.assertEqual(len(engine.books), 2)
        book_petr = engine.get_book("PETR4")
        book_vale = engine.get_book(200)

        self.assertIsNotNone(book_petr)
        self.assertIsNotNone(book_vale)
        self.assertEqual(book_petr.total_current_lots, 500)
        self.assertEqual(book_vale.total_current_lots, 800)

        all_inv = engine.verify_all_invariants(raise_on_error=True)
        self.assertTrue(all_inv[100]["order_conservation"])
        self.assertTrue(all_inv[200]["order_conservation"])


class TestFixtureScenarios(unittest.TestCase):
    """Testa os cenários canônicos declarados na fixture JSON oficial."""

    def setUp(self) -> None:
        self.assertTrue(FIXTURE_PATH.exists(), f"Fixture não encontrada: {FIXTURE_PATH}")
        with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
            self.fixture_data = json.load(f)

    def test_replay_scenario_1_multilevel_book(self) -> None:
        sc1 = self.fixture_data["scenarios"]["scenario_1_multilevel_book_construction"]
        book = MarketByOrderL3Book(security_id=105234, symbol="PETR4")

        for ev_data in sc1["events"]:
            book.apply_order_add(
                order_id=ev_data["order_id"],
                side=ev_data["side"],
                price=ev_data["price"],
                quantity=ev_data["quantity"],
                sequence_number=ev_data["sequence_number"],
                timestamp=datetime.fromisoformat(ev_data["timestamp"].replace("Z", "+00:00")),
                participant_id=ev_data["participant_id"],
            )

        exp = sc1["expected_state"]
        self.assertEqual(book.total_orders_count, exp["total_orders"])
        self.assertEqual(book.total_current_lots, exp["total_current_lots"])

        bbo = book.get_bbo()
        self.assertEqual(bbo.bid_price, Decimal(exp["bbo"]["bid_price"]))
        self.assertEqual(bbo.bid_quantity, exp["bbo"]["bid_quantity"])
        self.assertEqual(bbo.ask_price, Decimal(exp["bbo"]["ask_price"]))
        self.assertEqual(bbo.ask_quantity, exp["bbo"]["ask_quantity"])
        self.assertEqual(bbo.spread, Decimal(exp["bbo"]["spread"]))

        depth = book.get_l2_depth(depth=10)
        self.assertEqual(len(depth["bids"]), len(exp["l2_depth"]["bids"]))
        self.assertEqual(len(depth["asks"]), len(exp["l2_depth"]["asks"]))

        # Verifica invariantes formais
        invariants = book.verify_invariants(raise_on_error=True)
        self.assertTrue(all(invariants.values()))

    def test_replay_scenario_2_fifo_priority(self) -> None:
        sc2 = self.fixture_data["scenarios"]["scenario_2_fifo_queue_priority_test"]
        book = MarketByOrderL3Book(security_id=105234, symbol="PETR4")

        seq = 1
        for ord_data in sc2["initial_orders"]:
            book.apply_order_add(
                order_id=ord_data["order_id"],
                side="BID",
                price=ord_data["price"],
                quantity=ord_data["quantity"],
                sequence_number=seq,
                timestamp=datetime.fromisoformat(ord_data["timestamp"].replace("Z", "+00:00")),
            )
            seq += 1

        lvl = book.get_price_level("BID", "40.00")
        self.assertEqual(list(lvl.orders.keys()), [3001, 3002, 3003])

        # Passo 1: Redução mantém prioridade
        mod1 = sc2["modifications"][0]
        book.apply_order_modify(mod1["order_id"], new_quantity=mod1["new_quantity"], sequence_number=seq)
        seq += 1
        self.assertEqual(list(lvl.orders.keys()), mod1["expected_queue_order"])
        self.assertEqual(lvl.total_quantity, mod1["expected_level_quantity"])

        # Passo 2: Aumento perde prioridade para o final da fila
        mod2 = sc2["modifications"][1]
        book.apply_order_modify(mod2["order_id"], new_quantity=mod2["new_quantity"], sequence_number=seq)
        seq += 1
        self.assertEqual(list(lvl.orders.keys()), mod2["expected_queue_order"])
        self.assertEqual(lvl.total_quantity, mod2["expected_level_quantity"])


if __name__ == "__main__":
    unittest.main()
