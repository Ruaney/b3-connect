"""Microbenchmark Determinístico e Testes de Integridade de Protocolo PUMA SBE.

Avalia:
1. Decodificação de 100.000 mensagens SBE consecutivas com medição de microssegundos por mensagem
   e throughput em mensagens por segundo (msg/s).
2. Validação rigorosa de tags binárias Little-Endian (<H, <I, <Q, <q) e integridade de campos.
3. Validação de ausência de overhead de heap desnecessário (slotted dataclasses, zero-slicing via offset,
   ausência de __dict__, e verificação de memória com tracemalloc).
4. Decodificação sequencial em fluxo de mensagens contíguas (iter_decode / decode_stream).
"""

from __future__ import annotations

import gc
from pathlib import Path
import struct
import sys
import time
import tracemalloc
import unittest

# Assegura PYTHONPATH apontando para a raiz do repositório
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.puma_sbe import (
    ExecType,
    ExecutionReportBinary,
    NewOrderSingleBinary,
    OrderCancelBinary,
    OrdStatus,
    PRICE_SCALE,
    PumaSbeCodec,
    SbeHeader,
    Side,
    TimeInForce,
)


class TestPumaSbeLittleEndianIntegrity(unittest.TestCase):
    """Valida que todas as tags Little-Endian são decodificadas fielmente sem desvios de bytes."""

    def test_header_little_endian_wire_layout(self):
        """Cabeçalho SBE: valida tags <H (uint16) em ordem Little-Endian exata."""
        # block_length = 0x0022 (34) -> bytes [0x22, 0x00]
        # template_id  = 0x0065 (101) -> bytes [0x65, 0x00]
        # schema_id    = 0x0002 (2)   -> bytes [0x02, 0x00]
        # version      = 0x0003 (3)   -> bytes [0x03, 0x00]
        raw_bytes = bytes([0x22, 0x00, 0x65, 0x00, 0x02, 0x00, 0x03, 0x00])
        header = SbeHeader.decode(raw_bytes)

        self.assertEqual(header.block_length, 34)
        self.assertEqual(header.template_id, 101)
        self.assertEqual(header.schema_id, 2)
        self.assertEqual(header.version, 3)

        # Roundtrip Little-Endian
        encoded = header.encode()
        self.assertEqual(encoded, raw_bytes)

    def test_new_order_single_little_endian_wire_layout(self):
        """Template 101: valida integridade de tags Little-Endian <Q, <I, <q, B."""
        # Valores com padrões assimétricos de bytes para detectar qualquer inversão de endianness
        cl_ord_id = 0x0102030405060708
        security_id = 0x1122334455667788
        side = Side.BUY  # 1
        order_qty = 0x000186A0  # 100,000
        price = 123.456789
        price_scaled = int(round(price * PRICE_SCALE))  # 12345678900
        time_in_force = TimeInForce.IOC  # 3
        account = 0x000F4240  # 1,000,000

        order = NewOrderSingleBinary(
            cl_ord_id=cl_ord_id,
            security_id=security_id,
            side=side,
            order_qty=order_qty,
            price=price,
            time_in_force=time_in_force,
            account=account,
        )

        encoded = order.encode()
        self.assertEqual(len(encoded), 42)

        # Checa cabeçalho (bytes 0..7)
        bl, tid, sid, ver = struct.unpack_from("<HHHH", encoded, 0)
        self.assertEqual(bl, 34)
        self.assertEqual(tid, 101)
        self.assertEqual(sid, 1)
        self.assertEqual(ver, 1)

        # Checa decodificação manual Little-Endian de cada tag no payload
        dec_cl_ord_id = struct.unpack_from("<Q", encoded, 8)[0]
        dec_sec_id = struct.unpack_from("<Q", encoded, 16)[0]
        dec_side = struct.unpack_from("B", encoded, 24)[0]
        dec_qty = struct.unpack_from("<I", encoded, 25)[0]
        dec_px_scaled = struct.unpack_from("<q", encoded, 29)[0]
        dec_tif = struct.unpack_from("B", encoded, 37)[0]
        dec_acc = struct.unpack_from("<I", encoded, 38)[0]

        self.assertEqual(dec_cl_ord_id, cl_ord_id)
        self.assertEqual(dec_sec_id, security_id)
        self.assertEqual(dec_side, side.value)
        self.assertEqual(dec_qty, order_qty)
        self.assertEqual(dec_px_scaled, price_scaled)
        self.assertEqual(dec_tif, time_in_force.value)
        self.assertEqual(dec_acc, account)

        # Decodifica via classe e via Codec
        decoded = NewOrderSingleBinary.decode(encoded)
        self.assertEqual(decoded.cl_ord_id, cl_ord_id)
        self.assertEqual(decoded.security_id, security_id)
        self.assertEqual(decoded.side, side)
        self.assertEqual(decoded.order_qty, order_qty)
        self.assertAlmostEqual(decoded.price, price, places=6)
        self.assertEqual(decoded.price_scaled, price_scaled)
        self.assertEqual(decoded.time_in_force, time_in_force)
        self.assertEqual(decoded.account, account)

    def test_order_cancel_little_endian_wire_layout(self):
        """Template 102: valida integridade de tags Little-Endian <Q, <I."""
        cl_ord_id = 0xA1A2A3A4A5A6A7A8
        orig_cl_ord_id = 0xB1B2B3B4B5B6B7B8
        security_id = 0xC1C2C3C4C5C6C7C8
        account = 0x12345678

        cancel = OrderCancelBinary(
            cl_ord_id=cl_ord_id,
            orig_cl_ord_id=orig_cl_ord_id,
            security_id=security_id,
            account=account,
        )
        encoded = cancel.encode()
        self.assertEqual(len(encoded), 36)

        decoded = PumaSbeCodec.decode(encoded)
        self.assertIsInstance(decoded, OrderCancelBinary)
        self.assertEqual(decoded.cl_ord_id, cl_ord_id)
        self.assertEqual(decoded.orig_cl_ord_id, orig_cl_ord_id)
        self.assertEqual(decoded.security_id, security_id)
        self.assertEqual(decoded.account, account)

    def test_execution_report_little_endian_wire_layout(self):
        """Template 103: valida integridade de tags Little-Endian <Q, B, <I, <q."""
        order_id = 0x1111222233334444
        cl_ord_id = 0x5555666677778888
        exec_id = 0x9999AAAABBBBCCCC
        exec_type = ExecType.FILL
        ord_status = OrdStatus.FILLED
        security_id = 105829
        cum_qty = 50000
        leaves_qty = 0
        last_px = 45.75
        last_qty = 50000

        report = ExecutionReportBinary(
            order_id=order_id,
            cl_ord_id=cl_ord_id,
            exec_id=exec_id,
            exec_type=exec_type,
            ord_status=ord_status,
            security_id=security_id,
            cum_qty=cum_qty,
            leaves_qty=leaves_qty,
            last_px=last_px,
            last_qty=last_qty,
        )
        encoded = report.encode()
        self.assertEqual(len(encoded), 62)

        decoded = PumaSbeCodec.decode(encoded)
        self.assertIsInstance(decoded, ExecutionReportBinary)
        self.assertEqual(decoded.order_id, order_id)
        self.assertEqual(decoded.cl_ord_id, cl_ord_id)
        self.assertEqual(decoded.exec_id, exec_id)
        self.assertEqual(decoded.exec_type, exec_type)
        self.assertEqual(decoded.ord_status, ord_status)
        self.assertEqual(decoded.security_id, security_id)
        self.assertEqual(decoded.cum_qty, cum_qty)
        self.assertEqual(decoded.leaves_qty, leaves_qty)
        self.assertAlmostEqual(decoded.last_px, last_px, places=4)
        self.assertEqual(decoded.last_qty, last_qty)


class TestPumaSbeZeroHeapOverhead(unittest.TestCase):
    """Valida que não há overhead de heap desnecessário (slotted classes, zero-slicing via offset)."""

    def test_slotted_dataclasses_have_no_dict_overhead(self):
        """Classes SBE devem usar __slots__ para eliminar instâncias de __dict__ na heap."""
        header = SbeHeader(34, 101)
        order = NewOrderSingleBinary(1, 100, Side.BUY, 100, 30.0, TimeInForce.DAY, 123)
        cancel = OrderCancelBinary(1, 2, 100, 123)
        report = ExecutionReportBinary(1, 1, 1, ExecType.NEW, OrdStatus.NEW, 100, 0, 100, 0.0, 0)

        for obj in (header, order, cancel, report):
            with self.subTest(cls=obj.__class__.__name__):
                self.assertFalse(
                    hasattr(obj, "__dict__"),
                    f"Classe {obj.__class__.__name__} possui __dict__, gerando overhead desnecessário de heap!",
                )
                self.assertTrue(
                    hasattr(obj, "__slots__"),
                    f"Classe {obj.__class__.__name__} deve definir __slots__ para garantir alta performance.",
                )

    def test_zero_slice_offset_decoding(self):
        """Decodificação com offset deve decodificar sem necessidade de fatiamento data[a:b]."""
        order = NewOrderSingleBinary(10, 20, Side.SELL, 500, 42.50, TimeInForce.GTC, 999)
        order_bytes = order.encode()

        # Posiciona a mensagem no offset 128 dentro de um buffer de 256 bytes
        offset = 128
        large_buffer = bytearray(256)
        large_buffer[offset : offset + len(order_bytes)] = order_bytes

        decoded_direct = NewOrderSingleBinary.decode(large_buffer, offset=offset)
        self.assertEqual(decoded_direct.cl_ord_id, 10)
        self.assertEqual(decoded_direct.security_id, 20)
        self.assertEqual(decoded_direct.side, Side.SELL)

        decoded_codec = PumaSbeCodec.decode(large_buffer, offset=offset)
        self.assertIsInstance(decoded_codec, NewOrderSingleBinary)
        self.assertEqual(decoded_codec.cl_ord_id, 10)

    def test_memory_stability_under_consecutive_decodes(self):
        """tracemalloc monitora ausência de vazamento ou alocações intermediárias de fatias."""
        order = NewOrderSingleBinary(1, 105829, Side.BUY, 100, 34.50, TimeInForce.DAY, 987654)
        payload = order.encode()

        tracemalloc.start()
        # Warmup
        for _ in range(5000):
            _ = PumaSbeCodec.decode(payload)

        gc.collect()
        snap1 = tracemalloc.take_snapshot()

        for _ in range(50000):
            _ = PumaSbeCodec.decode(payload)

        gc.collect()
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        # Mede diferença líquida de memória
        diff = snap2.compare_to(snap1, "lineno")
        total_diff_kb = sum(stat.size_diff for stat in diff) / 1024.0
        # A memória estável após GC não deve ter acumulado vazamentos de heap
        self.assertLess(
            total_diff_kb,
            150.0,
            f"Vazamento ou acumulação de heap detectado: {total_diff_kb:.2f} KB",
        )


class TestPumaSbeBenchmark100k(unittest.TestCase):
    """Bateria de Microbenchmark Determinístico: 100.000 mensagens SBE consecutivas."""

    ITERATIONS: int = 100_000

    @classmethod
    def setUpClass(cls):
        """Prepara fixtures binárias para o benchmark determinístico."""
        cls.order = NewOrderSingleBinary(
            cl_ord_id=2026110300000001,
            security_id=105829,
            side=Side.BUY,
            order_qty=500,
            price=34.50,
            time_in_force=TimeInForce.DAY,
            account=987654,
        )
        cls.order_bytes = cls.order.encode()

        cls.cancel = OrderCancelBinary(
            cl_ord_id=2026110300000002,
            orig_cl_ord_id=2026110300000001,
            security_id=105829,
            account=987654,
        )
        cls.cancel_bytes = cls.cancel.encode()

        cls.exec_report = ExecutionReportBinary(
            order_id=9001234567890,
            cl_ord_id=2026110300000001,
            exec_id=777000111222,
            exec_type=ExecType.FILL,
            ord_status=OrdStatus.FILLED,
            security_id=105829,
            cum_qty=1000,
            leaves_qty=0,
            last_px=34.50,
            last_qty=1000,
        )
        cls.exec_bytes = cls.exec_report.encode()

        # Warm-up do JIT/CPython interpreter
        for _ in range(5_000):
            _ = PumaSbeCodec.decode(cls.order_bytes)
            _ = PumaSbeCodec.decode(cls.cancel_bytes)
            _ = PumaSbeCodec.decode(cls.exec_bytes)

    def test_benchmark_100k_new_order_single_direct(self):
        """Microbenchmark: 100.000 mensagens NewOrderSingleBinary decodificadas diretamente."""
        data = self.order_bytes
        n = self.ITERATIONS

        gc.collect()
        t0 = time.perf_counter()
        for _ in range(n):
            _ = NewOrderSingleBinary.decode(data)
        t1 = time.perf_counter()

        elapsed = t1 - t0
        throughput = n / elapsed
        latency_us = (elapsed / n) * 1_000_000

        print(
            f"\n[BENCHMARK] NewOrderSingleBinary.decode (100k): "
            f"Throughput={throughput:,.0f} msg/s | Latência Média={latency_us:.3f} us/msg | Total={elapsed:.4f}s"
        )

        self.assertGreater(throughput, 100_000, "Throughput deve exceder 100.000 msg/s")
        self.assertLess(latency_us, 10.0, "Latência média deve ser inferior a 10.0 microssegundos")

    def test_benchmark_100k_puma_sbe_codec_dispatcher(self):
        """Microbenchmark: 100.000 mensagens despachadas e decodificadas via PumaSbeCodec."""
        data = self.order_bytes
        n = self.ITERATIONS

        gc.collect()
        t0 = time.perf_counter()
        for _ in range(n):
            _ = PumaSbeCodec.decode(data)
        t1 = time.perf_counter()

        elapsed = t1 - t0
        throughput = n / elapsed
        latency_us = (elapsed / n) * 1_000_000

        print(
            f"\n[BENCHMARK] PumaSbeCodec.decode - NewOrder (100k): "
            f"Throughput={throughput:,.0f} msg/s | Latência Média={latency_us:.3f} us/msg | Total={elapsed:.4f}s"
        )

        self.assertGreater(throughput, 100_000, "Throughput deve exceder 100.000 msg/s")
        self.assertLess(latency_us, 10.0, "Latência média deve ser inferior a 10.0 microssegundos")

    def test_benchmark_100k_order_cancel_codec(self):
        """Microbenchmark: 100.000 mensagens OrderCancelBinary decodificadas via PumaSbeCodec."""
        data = self.cancel_bytes
        n = self.ITERATIONS

        gc.collect()
        t0 = time.perf_counter()
        for _ in range(n):
            _ = PumaSbeCodec.decode(data)
        t1 = time.perf_counter()

        elapsed = t1 - t0
        throughput = n / elapsed
        latency_us = (elapsed / n) * 1_000_000

        print(
            f"\n[BENCHMARK] PumaSbeCodec.decode - Cancel (100k): "
            f"Throughput={throughput:,.0f} msg/s | Latência Média={latency_us:.3f} us/msg | Total={elapsed:.4f}s"
        )

        self.assertGreater(throughput, 100_000, "Throughput deve exceder 100.000 msg/s")
        self.assertLess(latency_us, 10.0, "Latência média deve ser inferior a 10.0 microssegundos")

    def test_benchmark_100k_execution_report_codec(self):
        """Microbenchmark: 100.000 mensagens ExecutionReportBinary decodificadas via PumaSbeCodec."""
        data = self.exec_bytes
        n = self.ITERATIONS

        gc.collect()
        t0 = time.perf_counter()
        for _ in range(n):
            _ = PumaSbeCodec.decode(data)
        t1 = time.perf_counter()

        elapsed = t1 - t0
        throughput = n / elapsed
        latency_us = (elapsed / n) * 1_000_000

        print(
            f"\n[BENCHMARK] PumaSbeCodec.decode - ExecReport (100k): "
            f"Throughput={throughput:,.0f} msg/s | Latência Média={latency_us:.3f} us/msg | Total={elapsed:.4f}s"
        )

        self.assertGreater(throughput, 100_000, "Throughput deve exceder 100.000 msg/s")
        self.assertLess(latency_us, 10.0, "Latência média deve ser inferior a 10.0 microssegundos")

    def test_benchmark_100k_stream_contiguous_iter_decode(self):
        """Microbenchmark: 100.000 mensagens consecutivas em buffer contíguo via iter_decode."""
        # Cria buffer contíguo contendo 100.000 mensagens consecutivas combinadas
        pattern = self.order_bytes + self.cancel_bytes + self.exec_bytes
        repeats = self.ITERATIONS // 3
        stream_buf = pattern * repeats
        expected_msgs = repeats * 3  # 99.999 mensagens consecutivas

        gc.collect()
        t0 = time.perf_counter()
        count = 0
        for _ in PumaSbeCodec.iter_decode(stream_buf):
            count += 1
        t1 = time.perf_counter()

        self.assertEqual(count, expected_msgs)
        elapsed = t1 - t0
        throughput = count / elapsed
        latency_us = (elapsed / count) * 1_000_000

        print(
            f"\n[BENCHMARK] PumaSbeCodec.iter_decode - Contiguous Stream ({count:,} msgs): "
            f"Throughput={throughput:,.0f} msg/s | Latência Média={latency_us:.3f} us/msg | Total={elapsed:.4f}s"
        )

        self.assertGreater(throughput, 100_000, "Throughput do stream contíguo deve exceder 100.000 msg/s")
        self.assertLess(latency_us, 10.0, "Latência média do stream deve ser inferior a 10.0 microssegundos")

    def test_benchmark_100k_raw_tuple_unpack(self):
        """Microbenchmark: 100.000 mensagens desempacotadas via zero-copy unpack_raw (bare-metal wire speed)."""
        data = self.order_bytes
        n = self.ITERATIONS

        gc.collect()
        t0 = time.perf_counter()
        for _ in range(n):
            _ = NewOrderSingleBinary.unpack_raw(data)
        t1 = time.perf_counter()

        elapsed = t1 - t0
        throughput = n / elapsed
        latency_us = (elapsed / n) * 1_000_000

        print(
            f"\n[BENCHMARK] NewOrderSingleBinary.unpack_raw - Zero Allocation ({n:,} msgs): "
            f"Throughput={throughput:,.0f} msg/s | Latência Média={latency_us:.3f} us/msg | Total={elapsed:.4f}s"
        )

        self.assertGreater(throughput, 1_000_000, "Throughput bare-metal deve exceder 1.000.000 msg/s")
        self.assertLess(latency_us, 1.0, "Latência média bare-metal deve ser inferior a 1.0 microssegundo")


if __name__ == "__main__":
    unittest.main()
