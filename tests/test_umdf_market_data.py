"""Bateria de Testes Automatizados para Market Data Feed UMDF / SBE Multicast (FL-021).

Testa:
- Decodificação e codificação do cabeçalho canônico SBE (UmdfMessageHeader).
- Decodificação de mensagens SecurityStatus (Template 201).
- Carregamento de fixture binária e decodificação de Snapshot (Template 202).
- Reconstrução de Livro de Ofertas L2 (Market by Price) e L3 (Market by Order).
- Cálculo de métricas: Best Bid, Best Ask, Spread, Mid Price, Micro-preço e Profundidade.
- Carregamento de fixture binária e aplicação de atualizações incrementais (Template 203).
- Registro de trades tick-by-tick no livro.
- Descarte determinístico de pacotes duplicados entre canais redundantes Feed A e Feed B.
- Detecção e contagem de gaps de sequência no consumidor de stream.
- Rejeição de payloads truncados e templates desconhecidos.
"""

from pathlib import Path
import struct
import sys
import unittest

# Garante a raiz do projeto no PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.umdf_market_data import (
    GROUP_HEADER_SIZE,
    HEADER_SIZE,
    IncrementalEntry,
    MDEntryType,
    MDUpdateAction,
    MarketDataIncremental,
    MarketDataSnapshot,
    OrderBook,
    OrderBookEntry,
    SecurityStatusMessage,
    SecurityTradingStatus,
    TradingSessionSubID,
    UmdfCodec,
    UmdfError,
    UmdfMessageHeader,
    UmdfStreamConsumer,
    UmdfTruncatedPayloadError,
    UmdfUnknownTemplateError,
    UmdfValidationError,
)

FIXTURES_DIR = REPO_ROOT / "fixtures"


class TestUmdfHeader(unittest.TestCase):
    """Testes para o cabeçalho canônico SBE (UmdfMessageHeader)."""

    def test_header_encode_decode(self):
        """Cabeçalho SBE de 8 bytes deve codificar e decodificar perfeitamente."""
        header = UmdfMessageHeader(
            block_length=34,
            template_id=201,
            schema_id=1,
            version=1,
        )
        raw = header.encode()
        self.assertEqual(len(raw), HEADER_SIZE)

        # Unpack direto via struct para validação de layout de baixo nível
        block_len, tpl_id, schema_id, version = struct.unpack("<HHHH", raw)
        self.assertEqual(block_len, 34)
        self.assertEqual(tpl_id, 201)
        self.assertEqual(schema_id, 1)
        self.assertEqual(version, 1)

        decoded = UmdfMessageHeader.decode(raw)
        self.assertEqual(decoded.block_length, 34)
        self.assertEqual(decoded.template_id, 201)
        self.assertEqual(decoded.schema_id, 1)
        self.assertEqual(decoded.version, 1)

    def test_header_truncated_raises_error(self):
        """Payload com menos de 8 bytes deve disparar UmdfTruncatedPayloadError."""
        with self.assertRaises(UmdfTruncatedPayloadError):
            UmdfMessageHeader.decode(b"\x00\x01\x02")


class TestSecurityStatusMessage(unittest.TestCase):
    """Testes para mensagens de status de instrumento (Template 201)."""

    def test_security_status_roundtrip(self):
        """Codificação e decodificação completa de SecurityStatusMessage."""
        msg = SecurityStatusMessage(
            sequence_number=50,
            security_id=2001,
            security_group="EQUITIES",
            security_trading_status=SecurityTradingStatus.OPEN,
            trading_session_sub_id=TradingSessionSubID.CONTINUOUS_TRADING,
            timestamp_ns=1793750400000000000,
        )
        raw = msg.encode()
        self.assertEqual(len(raw), 42)  # 8 bytes header + 34 bytes payload

        decoded = SecurityStatusMessage.decode(raw)
        self.assertEqual(decoded.sequence_number, 50)
        self.assertEqual(decoded.security_id, 2001)
        self.assertEqual(decoded.security_group, "EQUITIES")
        self.assertEqual(decoded.security_trading_status, SecurityTradingStatus.OPEN)
        self.assertEqual(decoded.trading_session_sub_id, TradingSessionSubID.CONTINUOUS_TRADING)
        self.assertEqual(decoded.timestamp_ns, 1793750400000000000)

    def test_security_status_validation_errors(self):
        """Validações de regras de negócio em SecurityStatusMessage."""
        # SecurityID <= 0
        with self.assertRaises(UmdfValidationError):
            SecurityStatusMessage(
                sequence_number=1,
                security_id=0,
                security_group="EQUITIES",
                security_trading_status=SecurityTradingStatus.OPEN,
                trading_session_sub_id=2,
                timestamp_ns=100,
            ).encode()

        # Status inválido
        with self.assertRaises(UmdfValidationError):
            SecurityStatusMessage(
                sequence_number=1,
                security_id=2001,
                security_group="EQUITIES",
                security_trading_status=99,
                trading_session_sub_id=2,
                timestamp_ns=100,
            ).encode()


class TestMarketDataSnapshot(unittest.TestCase):
    """Testes para o snapshot completo do livro de ofertas (Template 202)."""

    def setUp(self):
        self.snapshot_fixture_path = FIXTURES_DIR / "umdf_market_data_snapshot.bin"

    def test_load_snapshot_fixture(self):
        """Carrega e valida a fixture binária real do snapshot de PETR4."""
        self.assertTrue(self.snapshot_fixture_path.exists())
        with open(self.snapshot_fixture_path, "rb") as f:
            raw_data = f.read()

        snapshot = MarketDataSnapshot.decode(raw_data)
        self.assertEqual(snapshot.sequence_number, 100)
        self.assertEqual(snapshot.security_id, 2001)
        self.assertEqual(snapshot.tot_num_reports, 1)
        self.assertEqual(snapshot.last_msg_seq_num_processed, 1000)
        self.assertEqual(len(snapshot.entries), 10)

        # 5 Bids e 5 Asks
        bids = [e for e in snapshot.entries if e.md_entry_type == MDEntryType.BID]
        asks = [e for e in snapshot.entries if e.md_entry_type == MDEntryType.OFFER]
        self.assertEqual(len(bids), 5)
        self.assertEqual(len(asks), 5)

        # Melhores ofertas (Top of Book)
        top_bid = bids[0]
        self.assertAlmostEqual(top_bid.price, 38.50)
        self.assertEqual(top_bid.size, 10000)
        self.assertEqual(top_bid.number_of_orders, 15)
        self.assertEqual(top_bid.order_id, 10001)

        top_ask = asks[0]
        self.assertAlmostEqual(top_ask.price, 38.51)
        self.assertEqual(top_ask.size, 8000)
        self.assertEqual(top_ask.number_of_orders, 12)
        self.assertEqual(top_ask.order_id, 20001)

    def test_snapshot_encode_decode_roundtrip(self):
        """Roundtrip determinístico de encode e decode do snapshot."""
        entries = [
            OrderBookEntry(MDEntryType.BID, 30.00, 500, price_level=1, number_of_orders=2, order_id=1),
            OrderBookEntry(MDEntryType.OFFER, 30.10, 800, price_level=1, number_of_orders=3, order_id=2),
        ]
        snap = MarketDataSnapshot(
            sequence_number=200,
            security_id=1001,
            tot_num_reports=1,
            last_msg_seq_num_processed=5000,
            timestamp_ns=1700000000000000000,
            entries=entries,
        )
        raw = snap.encode()
        decoded = MarketDataSnapshot.decode(raw)

        self.assertEqual(decoded.sequence_number, 200)
        self.assertEqual(decoded.security_id, 1001)
        self.assertEqual(decoded.last_msg_seq_num_processed, 5000)
        self.assertEqual(len(decoded.entries), 2)
        self.assertAlmostEqual(decoded.entries[0].price, 30.00)
        self.assertAlmostEqual(decoded.entries[1].price, 30.10)

    def test_snapshot_truncated_payload(self):
        """Tentativa de decodificar snapshot truncado deve disparar erro."""
        with open(self.snapshot_fixture_path, "rb") as f:
            raw_data = f.read()

        # Corta os últimos 10 bytes
        with self.assertRaises(UmdfTruncatedPayloadError):
            MarketDataSnapshot.decode(raw_data[:-10])


class TestOrderBookReconstruction(unittest.TestCase):
    """Testes para reconstrução e métricas do livro de ofertas L2 e L3."""

    def test_book_initialization_from_snapshot(self):
        """Inicializa livro de ofertas a partir de snapshot e valida integridade."""
        fixture_path = FIXTURES_DIR / "umdf_market_data_snapshot.bin"
        with open(fixture_path, "rb") as f:
            raw_data = f.read()
        snapshot = MarketDataSnapshot.decode(raw_data)

        book = OrderBook(security_id=2001)
        book.apply_snapshot(snapshot)

        self.assertEqual(book.last_seq_num, 1000)
        self.assertEqual(len(book.bids), 5)
        self.assertEqual(len(book.asks), 5)

        # Validação do BBO (Best Bid & Offer)
        best_bid = book.best_bid()
        self.assertIsNotNone(best_bid)
        self.assertAlmostEqual(best_bid[0], 38.50)
        self.assertEqual(best_bid[1], 10000)

        best_ask = book.best_ask()
        self.assertIsNotNone(best_ask)
        self.assertAlmostEqual(best_ask[0], 38.51)
        self.assertEqual(best_ask[1], 8000)

        # Spread: 38.51 - 38.50 = 0.01
        self.assertAlmostEqual(book.spread(), 0.01)

        # Mid-price: (38.50 + 38.51) / 2 = 38.505
        self.assertAlmostEqual(book.mid_price(), 38.505)

        # Micro-preço ponderado: (10000 * 38.51 + 8000 * 38.50) / 18000 = (385100 + 308000) / 18000 = 693100 / 18000 = 38.5056
        self.assertAlmostEqual(book.micro_price(), 38.5056, places=4)

        # Validação de profundidade L2 (Top 3)
        depth = book.get_depth(levels=3)
        self.assertEqual(len(depth["bids"]), 3)
        self.assertEqual(len(depth["asks"]), 3)

        self.assertAlmostEqual(depth["bids"][0]["price"], 38.50)
        self.assertAlmostEqual(depth["bids"][1]["price"], 38.49)
        self.assertAlmostEqual(depth["bids"][2]["price"], 38.48)

        self.assertAlmostEqual(depth["asks"][0]["price"], 38.51)
        self.assertAlmostEqual(depth["asks"][1]["price"], 38.52)
        self.assertAlmostEqual(depth["asks"][2]["price"], 38.53)


class TestIncrementalRefresh(unittest.TestCase):
    """Testes para o fluxo incremental e aplicação de deltas (Template 203)."""

    def setUp(self):
        self.inc_fixture_path = FIXTURES_DIR / "umdf_market_data_incremental.bin"
        self.snap_fixture_path = FIXTURES_DIR / "umdf_market_data_snapshot.bin"

    def test_load_incremental_fixture(self):
        """Carrega e valida a fixture de atualização incremental."""
        self.assertTrue(self.inc_fixture_path.exists())
        with open(self.inc_fixture_path, "rb") as f:
            raw_data = f.read()

        incremental = MarketDataIncremental.decode(raw_data)
        self.assertEqual(incremental.sequence_number, 1001)
        self.assertEqual(len(incremental.entries), 3)

        # Trade
        trade_entry = incremental.entries[0]
        self.assertEqual(trade_entry.md_entry_type, MDEntryType.TRADE)
        self.assertEqual(trade_entry.trade_id, 99887701)
        self.assertAlmostEqual(trade_entry.price, 38.51)
        self.assertEqual(trade_entry.size, 2000)

        # Change no Ask 38.51
        change_ask = incremental.entries[1]
        self.assertEqual(change_ask.md_update_action, MDUpdateAction.CHANGE)
        self.assertEqual(change_ask.md_entry_type, MDEntryType.OFFER)
        self.assertAlmostEqual(change_ask.price, 38.51)
        self.assertEqual(change_ask.size, 6000)

        # Change no Bid 38.50
        change_bid = incremental.entries[2]
        self.assertEqual(change_bid.md_update_action, MDUpdateAction.CHANGE)
        self.assertEqual(change_bid.md_entry_type, MDEntryType.BID)
        self.assertAlmostEqual(change_bid.price, 38.50)
        self.assertEqual(change_bid.size, 15000)

    def test_apply_incremental_updates_to_order_book(self):
        """Aplica incremental sobre o livro de ofertas reconstruído via snapshot."""
        # 1. Carrega snapshot
        with open(self.snap_fixture_path, "rb") as f:
            snap = MarketDataSnapshot.decode(f.read())
        book = OrderBook(security_id=2001)
        book.apply_snapshot(snap)

        # Estado pré-incremental
        self.assertEqual(book.best_ask(), (38.51, 8000))
        self.assertEqual(book.best_bid(), (38.50, 10000))
        self.assertEqual(len(book.trades), 0)

        # 2. Carrega e aplica incremental
        with open(self.inc_fixture_path, "rb") as f:
            incremental = MarketDataIncremental.decode(f.read())
        book.apply_incremental(incremental)

        # Estado pós-incremental
        self.assertEqual(book.last_seq_num, 1001)
        # Ask 38.51 deve ter sido reduzido de 8000 para 6000
        self.assertEqual(book.best_ask(), (38.51, 6000))
        # Bid 38.50 deve ter subido de 10000 para 15000
        self.assertEqual(book.best_bid(), (38.50, 15000))
        # Um trade registrado
        self.assertEqual(len(book.trades), 1)
        self.assertEqual(book.trades[0]["trade_id"], 99887701)
        self.assertEqual(book.trades[0]["size"], 2000)
        self.assertAlmostEqual(book.trades[0]["price"], 38.51)

    def test_delete_and_overlay_actions(self):
        """Testa ações DELETE e OVERLAY no livro de ofertas."""
        book = OrderBook(security_id=2001)
        # Inserção manual de nível
        inc_new = MarketDataIncremental(
            sequence_number=1,
            timestamp_ns=1000,
            entries=[
                IncrementalEntry(
                    md_update_action=MDUpdateAction.NEW,
                    md_entry_type=MDEntryType.BID,
                    security_id=2001,
                    price=40.00,
                    size=100,
                )
            ],
        )
        book.apply_incremental(inc_new)
        self.assertEqual(book.best_bid(), (40.00, 100))

        # OVERLAY substituindo
        inc_overlay = MarketDataIncremental(
            sequence_number=2,
            timestamp_ns=1001,
            entries=[
                IncrementalEntry(
                    md_update_action=MDUpdateAction.OVERLAY,
                    md_entry_type=MDEntryType.BID,
                    security_id=2001,
                    price=40.00,
                    size=250,
                )
            ],
        )
        book.apply_incremental(inc_overlay)
        self.assertEqual(book.best_bid(), (40.00, 250))

        # DELETE removendo
        inc_del = MarketDataIncremental(
            sequence_number=3,
            timestamp_ns=1002,
            entries=[
                IncrementalEntry(
                    md_update_action=MDUpdateAction.DELETE,
                    md_entry_type=MDEntryType.BID,
                    security_id=2001,
                    price=40.00,
                    size=250,
                )
            ],
        )
        book.apply_incremental(inc_del)
        self.assertIsNone(book.best_bid())


class TestUmdfStreamConsumer(unittest.TestCase):
    """Testes de redundância Active-Active (Feeds A/B) e stream consumer."""

    def setUp(self):
        with open(FIXTURES_DIR / "umdf_market_data_snapshot.bin", "rb") as f:
            self.snap_bytes = f.read()
        with open(FIXTURES_DIR / "umdf_market_data_incremental.bin", "rb") as f:
            self.inc_bytes = f.read()

    def test_ab_feed_deduplication(self):
        """Descarta deterministicamente pacotes duplicados recebidos de feeds A e B."""
        consumer = UmdfStreamConsumer()

        # Pacote chega primeiro via Feed A
        msgs_a = consumer.on_packet(self.snap_bytes, feed_id="A")
        self.assertEqual(len(msgs_a), 1)
        self.assertEqual(consumer.packets_received_a, 1)
        self.assertEqual(consumer.packets_received_b, 0)
        self.assertEqual(consumer.duplicates_discarded, 0)

        # Mesmo pacote (seq=100) chega milissegundos depois via Feed B
        msgs_b = consumer.on_packet(self.snap_bytes, feed_id="B")
        self.assertEqual(len(msgs_b), 0)  # Descartado como duplicata
        self.assertEqual(consumer.packets_received_a, 1)
        self.assertEqual(consumer.packets_received_b, 1)
        self.assertEqual(consumer.duplicates_discarded, 1)

        # Livro foi populado corretamente
        book = consumer.get_book(2001)
        self.assertIsNotNone(book)
        self.assertEqual(len(book.bids), 5)

    def test_reverse_feed_deduplication(self):
        """Pacote chegando primeiro pelo Feed B e depois pelo Feed A é deduplicado."""
        consumer = UmdfStreamConsumer()

        # Chega pelo Feed B primeiro
        msgs_b = consumer.on_packet(self.inc_bytes, feed_id="B")
        self.assertEqual(len(msgs_b), 1)
        self.assertEqual(consumer.packets_received_b, 1)

        # Chega pelo Feed A depois
        msgs_a = consumer.on_packet(self.inc_bytes, feed_id="A")
        self.assertEqual(len(msgs_a), 0)
        self.assertEqual(consumer.duplicates_discarded, 1)

    def test_gap_detection_metric(self):
        """Detecta salto anormal de números de sequência (salto > 1)."""
        consumer = UmdfStreamConsumer()

        # Pacote seq 100
        consumer.on_packet(self.snap_bytes, feed_id="A")
        self.assertEqual(consumer.gaps_detected, 0)

        # Pacote seq 1001 (salto de 100 para 1001 -> gap de 900 pacotes)
        consumer.on_packet(self.inc_bytes, feed_id="A")
        self.assertEqual(consumer.gaps_detected, 900)

        stats = consumer.stats()
        self.assertEqual(stats["highest_sequence"], 1001)
        self.assertEqual(stats["gaps_detected"], 900)

    def test_consumer_security_status_dispatch(self):
        """Verifica atualização de status do ativo ao consumir SecurityStatusMessage."""
        consumer = UmdfStreamConsumer()
        status_msg = SecurityStatusMessage(
            sequence_number=1,
            security_id=2001,
            security_group="EQUITIES",
            security_trading_status=SecurityTradingStatus.AUCTION,
            trading_session_sub_id=TradingSessionSubID.PRE_OPEN,
            timestamp_ns=1793750400000000000,
        )
        raw = status_msg.encode()
        consumer.on_packet(raw, feed_id="A")

        st = consumer.get_security_status(2001)
        self.assertIsNotNone(st)
        self.assertEqual(st.security_trading_status, SecurityTradingStatus.AUCTION)
        self.assertEqual(st.trading_session_sub_id, TradingSessionSubID.PRE_OPEN)


class TestUmdfCodec(unittest.TestCase):
    """Testes gerais do codec e tratamento de erros."""

    def test_unknown_template_raises_error(self):
        """Template ID desconhecido deve disparar UmdfUnknownTemplateError."""
        header = UmdfMessageHeader(block_length=10, template_id=999)
        raw = header.encode() + b"\x00" * 10
        with self.assertRaises(UmdfUnknownTemplateError):
            UmdfCodec.decode(raw)

    def test_truncated_buffer_raises_error(self):
        """Buffer menor que o cabeçalho deve disparar UmdfTruncatedPayloadError."""
        with self.assertRaises(UmdfTruncatedPayloadError):
            UmdfCodec.decode(b"\x00\x01\x02")


if __name__ == "__main__":
    unittest.main()
