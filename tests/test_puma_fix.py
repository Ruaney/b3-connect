"""Testes unitários para o protocolo PUMA FIX e Drop Copy (FL-011 e FL-012)."""

import json
import os
from pathlib import Path
import sys
import unittest
from decimal import Decimal

# Garante que a raiz do projeto esteja no PYTHONPATH independentemente da forma de execução
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.puma_fix import (
    FixBodyLengthError,
    FixChecksumError,
    FixError,
    FixExecType,
    FixMessage,
    FixMsgType,
    FixOrdStatus,
    FixOrdType,
    FixPartyRole,
    FixProtocolError,
    FixSessionError,
    FixSessionManager,
    FixSide,
    FixTimeInForce,
    FixValidationError,
    PumaOrderEntryClient,
    DropCopyStreamConsumer,
    SessionState,
    TAG_ACCOUNT,
    TAG_AVG_PX,
    TAG_BEGIN_STRING,
    TAG_BODY_LENGTH,
    TAG_CHECK_SUM,
    TAG_CL_ORD_ID,
    TAG_CUM_QTY,
    TAG_EXEC_ID,
    TAG_EXEC_TYPE,
    TAG_LAST_PX,
    TAG_LAST_QTY,
    TAG_LEAVES_QTY,
    TAG_MEMO,
    TAG_MSG_SEQ_NUM,
    TAG_MSG_TYPE,
    TAG_ORDER_ID,
    TAG_ORDER_QTY,
    TAG_ORD_STATUS,
    TAG_ORD_TYPE,
    TAG_ORIG_CL_ORD_ID,
    TAG_PARTY_ID,
    TAG_PARTY_ROLE,
    TAG_PRICE,
    TAG_SENDER_COMP_ID,
    TAG_SIDE,
    TAG_STOP_PX,
    TAG_SYMBOL,
    TAG_TARGET_COMP_ID,
    TAG_TEXT,
    TAG_TIME_IN_FORCE,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")


class TestFixMessageEncodingAndDecoding(unittest.TestCase):
    """Validação da serialização, parsing e integridade de mensagens FIX."""

    def test_encode_and_decode_roundtrip_soh(self):
        """Testa roundtrip de serialização e decodificação com delimitador SOH (\x01)."""
        msg = FixMessage()
        msg.set(TAG_BEGIN_STRING, "FIX.4.4")
        msg.set(TAG_MSG_TYPE, FixMsgType.HEARTBEAT)
        msg.set(TAG_SENDER_COMP_ID, "TEST_SENDER")
        msg.set(TAG_TARGET_COMP_ID, "BVMF")
        msg.set(TAG_MSG_SEQ_NUM, 10)
        msg.set(52, "20261103-12:00:00.000")

        encoded_bytes = msg.encode()
        self.assertIn(b"\x01", encoded_bytes)
        self.assertTrue(encoded_bytes.startswith(b"8=FIX.4.4\x019="))
        self.assertTrue(encoded_bytes.endswith(b"\x01"))

        decoded = FixMessage.decode(encoded_bytes)
        self.assertEqual(decoded.begin_string, "FIX.4.4")
        self.assertEqual(decoded.msg_type, "0")
        self.assertEqual(decoded.sender_comp_id, "TEST_SENDER")
        self.assertEqual(decoded.target_comp_id, "BVMF")
        self.assertEqual(decoded.seq_num, 10)

    def test_encode_and_decode_pipe_delimiter(self):
        """Testa serialização e parsing com delimitador visual pipe ('|')."""
        msg = FixMessage()
        msg.set(TAG_BEGIN_STRING, "FIX.4.4")
        msg.set(TAG_MSG_TYPE, FixMsgType.LOGON)
        msg.set(TAG_SENDER_COMP_ID, "BROKER105")
        msg.set(TAG_TARGET_COMP_ID, "BVMF")
        msg.set(TAG_MSG_SEQ_NUM, 1)
        msg.set(52, "20261103-12:00:00.000")
        msg.set(98, 0)
        msg.set(108, 30)

        pipe_str = msg.to_string(delimiter="|")
        self.assertIn("|", pipe_str)
        self.assertNotIn("\x01", pipe_str)

        decoded = FixMessage.decode(pipe_str, delimiter="|")
        self.assertEqual(decoded.msg_type, "A")
        self.assertEqual(decoded.get_int(108), 30)
        self.assertEqual(decoded.get_int(98), 0)

    def test_checksum_and_bodylength_calculation(self):
        """Testa o cálculo exato e recálculo determinístico de Tag 9 e Tag 10."""
        msg = FixMessage()
        msg.set(TAG_BEGIN_STRING, "FIX.4.4")
        msg.set(TAG_MSG_TYPE, "0")
        msg.set(TAG_SENDER_COMP_ID, "A")
        msg.set(TAG_TARGET_COMP_ID, "B")
        msg.set(TAG_MSG_SEQ_NUM, 1)
        msg.set(52, "20261103-00:00:00.000")

        # Serializa garantindo integridade
        raw = msg.encode()
        decoded = FixMessage.decode(raw)

        expected_body_len = decoded.calculate_body_length(b"\x01")
        self.assertEqual(decoded.get_int(TAG_BODY_LENGTH), expected_body_len)

        expected_chk = decoded.calculate_checksum(b"\x01")
        self.assertEqual(decoded.get(TAG_CHECK_SUM), expected_chk)
        self.assertEqual(len(expected_chk), 3)

    def test_rejection_corrupted_checksum(self):
        """Testa a rejeição estrita de mensagem com Tag 10 corrompida."""
        msg = FixMessage()
        msg.set(TAG_BEGIN_STRING, "FIX.4.4")
        msg.set(TAG_MSG_TYPE, "0")
        msg.set(TAG_SENDER_COMP_ID, "A")
        msg.set(TAG_TARGET_COMP_ID, "B")
        msg.set(TAG_MSG_SEQ_NUM, 1)
        msg.set(52, "20261103-00:00:00.000")
        valid_raw = msg.to_string(delimiter="|")

        # Corrompe apenas o valor do Checksum (Tag 10)
        corrupted_raw = valid_raw[:-4] + "999|"

        with self.assertRaises(FixChecksumError) as ctx:
            FixMessage.decode(corrupted_raw, delimiter="|", validate_integrity=True)
        self.assertIn("Divergência de CheckSum", str(ctx.exception))

    def test_rejection_corrupted_body_length(self):
        """Testa a rejeição estrita de mensagem com Tag 9 (BodyLength) divergente."""
        msg = FixMessage()
        msg.set(TAG_BEGIN_STRING, "FIX.4.4")
        msg.set(TAG_MSG_TYPE, "0")
        msg.set(TAG_SENDER_COMP_ID, "A")
        msg.set(TAG_TARGET_COMP_ID, "B")
        msg.set(TAG_MSG_SEQ_NUM, 1)
        msg.set(52, "20261103-00:00:00.000")
        # Força BodyLength inválido
        msg.set(TAG_BODY_LENGTH, 999)
        msg.set(TAG_CHECK_SUM, "000")
        raw = msg.to_string(delimiter="|", compute_integrity=False)

        with self.assertRaises(FixBodyLengthError) as ctx:
            FixMessage.decode(raw, delimiter="|", validate_integrity=True)
        self.assertIn("Divergência de BodyLength", str(ctx.exception))

    def test_protocol_order_validation(self):
        """Testa validação estrutural de cabeçalho e trailer FIX (Tag 8, 9 e 10)."""
        # Tag 8 ausente ou fora do início
        with self.assertRaises(FixProtocolError):
            FixMessage.decode("35=0|8=FIX.4.4|9=10|10=000|", delimiter="|", validate_integrity=False)

        # Tag 9 não é o segundo campo
        with self.assertRaises(FixProtocolError):
            FixMessage.decode("8=FIX.4.4|35=0|9=10|10=000|", delimiter="|", validate_integrity=False)

        # Tag 10 não é o último campo
        with self.assertRaises(FixProtocolError):
            FixMessage.decode("8=FIX.4.4|9=10|10=000|35=0|", delimiter="|", validate_integrity=False)

    def test_repeating_groups_and_data_conversions(self):
        """Testa suporte a campos repetidos (PartyIDs) e conversores numéricos."""
        msg = FixMessage()
        msg.append(TAG_PARTY_ID, "BROKER_A")
        msg.append(TAG_PARTY_ROLE, FixPartyRole.EXECUTING_BROKER)
        msg.append(TAG_PARTY_ID, "CLEARING_B")
        msg.append(TAG_PARTY_ROLE, FixPartyRole.CLEARING_MEMBER)

        self.assertEqual(msg.get_all(TAG_PARTY_ID), ["BROKER_A", "CLEARING_B"])
        self.assertEqual(msg.get_all(TAG_PARTY_ROLE), ["1", "4"])

        msg.set(TAG_PRICE, "38.50")
        self.assertEqual(msg.get_float(TAG_PRICE), 38.50)
        self.assertEqual(msg.get_decimal(TAG_PRICE), Decimal("38.50"))
        self.assertEqual(msg.get_int(TAG_PARTY_ROLE), 1)


class TestFixSessionManager(unittest.TestCase):
    """Validação da máquina de estados e sequenciamento de sessão FIX."""

    def setUp(self):
        self.session = FixSessionManager(
            sender_comp_id="CLIENT105",
            target_comp_id="BVMF",
            begin_string="FIX.4.4",
            heartbt_int=30,
        )

    def test_logon_generation(self):
        """Testa criação de Logon (35=A) com credenciais e reset de sequência."""
        logon = self.session.create_logon(reset_seq=True, username="OPR_TEST", password="SEC")
        self.assertEqual(logon.msg_type, FixMsgType.LOGON.value)
        self.assertEqual(logon.seq_num, 1)
        self.assertEqual(logon.get(TAG_SENDER_COMP_ID), "CLIENT105")
        self.assertEqual(logon.get(TAG_TARGET_COMP_ID), "BVMF")
        self.assertEqual(logon.get(553), "OPR_TEST")
        self.assertEqual(logon.get(554), "SEC")
        self.assertEqual(logon.get(141), "Y")
        self.assertEqual(self.session.state, SessionState.LOGON_SENT)

    def test_inbound_logon_acceptance(self):
        """Testa aceite de Logon de resposta da B3."""
        self.session.create_logon(reset_seq=True)
        in_logon = FixMessage()
        in_logon.set(TAG_BEGIN_STRING, "FIX.4.4")
        in_logon.set(TAG_MSG_TYPE, FixMsgType.LOGON)
        in_logon.set(TAG_SENDER_COMP_ID, "BVMF")
        in_logon.set(TAG_TARGET_COMP_ID, "CLIENT105")
        in_logon.set(TAG_MSG_SEQ_NUM, 1)
        in_logon.set(52, "20261103-12:00:00.000")

        status, resp = self.session.handle_inbound(in_logon)
        self.assertEqual(status, "LOGON_ACCEPTED")
        self.assertIsNone(resp)
        self.assertEqual(self.session.state, SessionState.ACTIVE)
        self.assertEqual(self.session.in_seq_num, 2)

    def test_heartbeat_response_to_test_request(self):
        """Testa emissão automática de Heartbeat contendo TestReqID ao receber TestRequest."""
        test_req = FixMessage()
        test_req.set(TAG_BEGIN_STRING, "FIX.4.4")
        test_req.set(TAG_MSG_TYPE, FixMsgType.TEST_REQUEST)
        test_req.set(TAG_MSG_SEQ_NUM, 1)
        test_req.set(112, "HEARTBEAT_CHALLENGE_99")

        status, resp = self.session.handle_inbound(test_req)
        self.assertEqual(status, "TEST_REQUEST_ANSWERED")
        self.assertIsNotNone(resp)
        self.assertEqual(resp.msg_type, FixMsgType.HEARTBEAT.value)
        self.assertEqual(resp.get(112), "HEARTBEAT_CHALLENGE_99")

    def test_sequence_gap_detection_and_resend_request(self):
        """Testa detecção de salto de sequência (gap) e geração de ResendRequest (35=2)."""
        # Esperado in_seq_num = 1; recebido in_seq_num = 5
        jumped_msg = FixMessage()
        jumped_msg.set(TAG_BEGIN_STRING, "FIX.4.4")
        jumped_msg.set(TAG_MSG_TYPE, FixMsgType.HEARTBEAT)
        jumped_msg.set(TAG_MSG_SEQ_NUM, 5)

        status, resp = self.session.handle_inbound(jumped_msg)
        self.assertEqual(status, "GAP_DETECTED")
        self.assertIsNotNone(resp)
        self.assertEqual(resp.msg_type, FixMsgType.RESEND_REQUEST.value)
        self.assertEqual(resp.get_int(7), 1)  # BeginSeqNo
        self.assertEqual(resp.get_int(16), 0)  # EndSeqNo (infinity)

    def test_sequence_too_low_rejection(self):
        """Testa rejeição de mensagem com sequência inferior à esperada."""
        self.session.in_seq_num = 10
        old_msg = FixMessage()
        old_msg.set(TAG_BEGIN_STRING, "FIX.4.4")
        old_msg.set(TAG_MSG_TYPE, FixMsgType.HEARTBEAT)
        old_msg.set(TAG_MSG_SEQ_NUM, 4)

        status, resp = self.session.handle_inbound(old_msg)
        self.assertEqual(status, "SEQUENCE_TOO_LOW")
        self.assertIsNotNone(resp)
        self.assertEqual(resp.msg_type, FixMsgType.REJECT.value)
        self.assertEqual(resp.get_int(45), 4)  # RefSeqNum


class TestPumaOrderEntryClient(unittest.TestCase):
    """Validação das regras de envio e gerenciamento de ordens no Entrypoint (FL-011)."""

    def setUp(self):
        self.session = FixSessionManager(sender_comp_id="BROKER105", target_comp_id="BVMF")
        self.client = PumaOrderEntryClient(
            session=self.session,
            account="778899",
            executing_broker="105",
            clearing_member="105",
        )

    def test_create_valid_new_order_single(self):
        """Testa criação e campos mandatórios de NewOrderSingle para PETR4."""
        msg = self.client.new_order_single(
            symbol="PETR4",
            side=FixSide.BUY,
            order_qty=1000,
            price=38.50,
            ord_type=FixOrdType.LIMIT,
            time_in_force=FixTimeInForce.DAY,
            cl_ord_id="ORD-001",
            memo="ALGO_TWAP",
            client_id="INV_01",
        )

        self.assertEqual(msg.msg_type, FixMsgType.NEW_ORDER_SINGLE.value)
        self.assertEqual(msg.get(TAG_CL_ORD_ID), "ORD-001")
        self.assertEqual(msg.get(TAG_ACCOUNT), "778899")
        self.assertEqual(msg.get(TAG_SYMBOL), "PETR4")
        self.assertEqual(msg.get(TAG_SIDE), "1")
        self.assertEqual(msg.get_int(TAG_ORDER_QTY), 1000)
        self.assertEqual(msg.get(TAG_PRICE), "38.50")
        self.assertEqual(msg.get(TAG_TIME_IN_FORCE), "0")
        self.assertEqual(msg.get(TAG_MEMO), "ALGO_TWAP")

        # Verifica PartyIDs (Executing Broker, Clearing Member, Client ID)
        p_ids = msg.get_all(TAG_PARTY_ID)
        p_roles = msg.get_all(TAG_PARTY_ROLE)
        self.assertIn("105", p_ids)
        self.assertIn("INV_01", p_ids)
        self.assertIn("1", p_roles)  # Executing Broker
        self.assertIn("4", p_roles)  # Clearing Member
        self.assertIn("3", p_roles)  # Client ID

        # Verifica registro em ordens ativas
        self.assertIn("ORD-001", self.client.active_orders)
        self.assertEqual(self.client.active_orders["ORD-001"]["leaves_qty"], 1000)

    def test_new_order_single_validation_errors(self):
        """Testa rejeições pré-trading para quantidade negativa, preço ausente e side inválido."""
        # Quantidade <= 0
        with self.assertRaises(FixValidationError):
            self.client.new_order_single(symbol="PETR4", side=FixSide.BUY, order_qty=0, price=30.0)

        # Ordem limitada sem preço
        with self.assertRaises(FixValidationError):
            self.client.new_order_single(symbol="PETR4", side=FixSide.BUY, order_qty=100, price=None, ord_type=FixOrdType.LIMIT)

        # Ordem Stop sem StopPx
        with self.assertRaises(FixValidationError):
            self.client.new_order_single(symbol="PETR4", side=FixSide.BUY, order_qty=100, ord_type=FixOrdType.STOP, stop_px=None)

        # Side inválido
        with self.assertRaises(FixValidationError):
            self.client.new_order_single(symbol="PETR4", side="9", order_qty=100, price=30.0)

    def test_cancel_and_replace_requests(self):
        """Testa criação de OrderCancelRequest (35=F) e OrderCancelReplaceRequest (35=G)."""
        cancel = self.client.cancel_order(
            orig_cl_ord_id="ORD-001",
            symbol="PETR4",
            side=FixSide.BUY,
            order_qty=1000,
            order_id="B3-123",
        )
        self.assertEqual(cancel.msg_type, FixMsgType.ORDER_CANCEL_REQUEST.value)
        self.assertEqual(cancel.get(TAG_ORIG_CL_ORD_ID), "ORD-001")
        self.assertEqual(cancel.get(TAG_ORDER_ID), "B3-123")

        replace = self.client.cancel_replace_order(
            orig_cl_ord_id="ORD-001",
            symbol="PETR4",
            side=FixSide.BUY,
            new_qty=500,
            new_price=39.00,
            order_id="B3-123",
        )
        self.assertEqual(replace.msg_type, FixMsgType.ORDER_CANCEL_REPLACE_REQUEST.value)
        self.assertEqual(replace.get(TAG_ORIG_CL_ORD_ID), "ORD-001")
        self.assertEqual(replace.get_int(TAG_ORDER_QTY), 500)
        self.assertEqual(replace.get(TAG_PRICE), "39.00")

    def test_process_execution_report_updates_order_book(self):
        """Testa atualização de ordens ativas ao receber ExecutionReport de fill parcial e total."""
        self.client.new_order_single(
            symbol="VALE3",
            side=FixSide.SELL,
            order_qty=500,
            price=62.00,
            cl_ord_id="ORD-VALE-1",
        )

        # 1. Partial Fill (200 executadas, 300 restantes)
        er_partial = FixMessage()
        er_partial.set(TAG_BEGIN_STRING, "FIX.4.4")
        er_partial.set(TAG_MSG_TYPE, FixMsgType.EXECUTION_REPORT)
        er_partial.set(TAG_ORDER_ID, "B3-VALE-99")
        er_partial.set(TAG_CL_ORD_ID, "ORD-VALE-1")
        er_partial.set(TAG_EXEC_ID, "EX-01")
        er_partial.set(TAG_EXEC_TYPE, FixExecType.PARTIAL_FILL)
        er_partial.set(TAG_ORD_STATUS, FixOrdStatus.PARTIALLY_FILLED)
        er_partial.set(TAG_SYMBOL, "VALE3")
        er_partial.set(TAG_SIDE, "2")
        er_partial.set(TAG_LEAVES_QTY, 300)
        er_partial.set(TAG_CUM_QTY, 200)
        er_partial.set(TAG_AVG_PX, "62.00")

        self.client.process_execution_report(er_partial)
        ord_info = self.client.active_orders["ORD-VALE-1"]
        self.assertEqual(ord_info["leaves_qty"], 300)
        self.assertEqual(ord_info["cum_qty"], 200)
        self.assertEqual(ord_info["status"], FixOrdStatus.PARTIALLY_FILLED.value)

        # 2. Full Fill (saldo zerado)
        er_full = FixMessage()
        er_full.set(TAG_BEGIN_STRING, "FIX.4.4")
        er_full.set(TAG_MSG_TYPE, FixMsgType.EXECUTION_REPORT)
        er_full.set(TAG_ORDER_ID, "B3-VALE-99")
        er_full.set(TAG_CL_ORD_ID, "ORD-VALE-1")
        er_full.set(TAG_EXEC_ID, "EX-02")
        er_full.set(TAG_EXEC_TYPE, FixExecType.FILL)
        er_full.set(TAG_ORD_STATUS, FixOrdStatus.FILLED)
        er_full.set(TAG_SYMBOL, "VALE3")
        er_full.set(TAG_SIDE, "2")
        er_full.set(TAG_LEAVES_QTY, 0)
        er_full.set(TAG_CUM_QTY, 500)
        er_full.set(TAG_AVG_PX, "62.00")

        self.client.process_execution_report(er_full)
        self.assertEqual(self.client.active_orders["ORD-VALE-1"]["leaves_qty"], 0)
        self.assertEqual(self.client.active_orders["ORD-VALE-1"]["cum_qty"], 500)
        self.assertEqual(self.client.active_orders["ORD-VALE-1"]["status"], FixOrdStatus.FILLED.value)


class TestDropCopyStreamConsumer(unittest.TestCase):
    """Validação do consumo passivo e consolidação de risco do Drop Copy (FL-012)."""

    def setUp(self):
        self.consumer = DropCopyStreamConsumer()

    def test_consume_drop_copy_fixture_stream(self):
        """Testa a ingestão do lote completo de eventos de fixtures/drop_copy_stream.json."""
        stream_path = os.path.join(FIXTURES_DIR, "drop_copy_stream.json")
        with open(stream_path, "r") as f:
            events_data = json.load(f)

        raw_messages = [e["raw_fix"] for e in events_data]
        records = self.consumer.consume_stream(raw_messages)
        self.assertEqual(len(records), len(events_data))

        # 1. Validação de Posições por Ativo
        # PETR4: Compra de 400 + 600 = +1000
        petr_pos = self.consumer.get_symbol_position("PETR4")
        self.assertEqual(petr_pos["net_quantity"], 1000)
        self.assertEqual(petr_pos["buy_volume"], 1000)
        self.assertEqual(petr_pos["sell_volume"], 0)
        self.assertAlmostEqual(petr_pos["gross_bought_value"], 38500.00)
        self.assertAlmostEqual(petr_pos["vwap_buy"], 38.50)

        # VALE3: Venda de 500 @ 62.10 = -500
        vale_pos = self.consumer.get_symbol_position("VALE3")
        self.assertEqual(vale_pos["net_quantity"], -500)
        self.assertEqual(vale_pos["sell_volume"], 500)
        self.assertAlmostEqual(vale_pos["gross_sold_value"], 31050.00)
        self.assertAlmostEqual(vale_pos["vwap_sell"], 62.10)

        # B3SA3: Compra de 1000 @ 11.95 + 1000 @ 12.00 = +2000
        b3sa_pos = self.consumer.get_symbol_position("B3SA3")
        self.assertEqual(b3sa_pos["net_quantity"], 2000)
        self.assertEqual(b3sa_pos["buy_volume"], 2000)
        self.assertAlmostEqual(b3sa_pos["gross_bought_value"], 23950.00)
        self.assertAlmostEqual(b3sa_pos["vwap_buy"], 11.9750)

        # 2. Rejeição capturada (ITUB4)
        self.assertEqual(len(self.consumer.rejections), 1)
        rej = self.consumer.rejections[0]
        self.assertEqual(rej["symbol"], "ITUB4")
        self.assertEqual(rej["ord_status"], FixOrdStatus.REJECTED.value)
        self.assertIn("TUNEL DE OSCILACAO EXCEDIDO", rej["text"])

        # 3. Sumário global de risco
        risk = self.consumer.get_account_risk_summary()
        self.assertEqual(risk["total_accounts"], 3)
        self.assertEqual(risk["rejected_orders"], 1)
        # Nenhuma ordem ficou aberta com saldo ao final do stream
        self.assertEqual(risk["working_orders_count"], 0)
        expected_exposure = 38500.00 + 31050.00 + 23950.00
        self.assertAlmostEqual(risk["gross_exposure"], expected_exposure)

        # 4. Snapshot estruturado
        snapshot = self.consumer.export_risk_snapshot()
        self.assertIn("summary", snapshot)
        self.assertIn("positions", snapshot)
        self.assertIn("PETR4", snapshot["positions"])
        self.assertEqual(snapshot["total_executions"], 8)


class TestCanonicalFixturesIntegrity(unittest.TestCase):
    """Garante que todos os arquivos canônicos em fixtures/ são válidos e cumprem invariantes."""

    def test_fixture_fix_logon(self):
        """Testa fixture fix_logon.txt."""
        path = os.path.join(FIXTURES_DIR, "fix_logon.txt")
        with open(path, "r") as f:
            content = f.read().strip()
        msg = FixMessage.decode(content, delimiter="|", validate_integrity=True)
        self.assertEqual(msg.msg_type, "A")
        self.assertEqual(msg.sender_comp_id, "BROKER105")
        self.assertEqual(msg.target_comp_id, "BVMF")
        self.assertEqual(msg.seq_num, 1)

    def test_fixture_fix_new_order_single(self):
        """Testa fixture fix_new_order_single.txt."""
        path = os.path.join(FIXTURES_DIR, "fix_new_order_single.txt")
        with open(path, "r") as f:
            content = f.read().strip()
        msg = FixMessage.decode(content, delimiter="|", validate_integrity=True)
        self.assertEqual(msg.msg_type, "D")
        self.assertEqual(msg.get(TAG_SYMBOL), "PETR4")
        self.assertEqual(msg.get(TAG_SIDE), "1")
        self.assertEqual(msg.get_int(TAG_ORDER_QTY), 1000)
        self.assertEqual(msg.get(TAG_PRICE), "38.50")
        self.assertEqual(msg.get(TAG_MEMO), "MESA_VAREJO")

    def test_fixture_fix_execution_report_and_invariants(self):
        """Testa fixture fix_execution_report.txt e comprova a conservação LeavesQty + CumQty == OrderQty."""
        path = os.path.join(FIXTURES_DIR, "fix_execution_report.txt")
        with open(path, "r") as f:
            lines = [line.strip() for line in f if line.strip()]

        self.assertEqual(len(lines), 2)
        for line in lines:
            msg = FixMessage.decode(line, delimiter="|", validate_integrity=True)
            self.assertEqual(msg.msg_type, "8")
            oqty = msg.get_int(TAG_ORDER_QTY)
            lqty = msg.get_int(TAG_LEAVES_QTY)
            cqty = msg.get_int(TAG_CUM_QTY)
            # Invariante universal de execução B3
            self.assertEqual(lqty + cqty, oqty, f"Invariante violada: {lqty} + {cqty} != {oqty}")


if __name__ == "__main__":
    unittest.main()
