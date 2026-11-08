"""Suíte de Testes de Validação da Camada C-ABI / FFI (ctypes & layouts nativos).

Valida:
1. Layout de memória, alinhamento, padding e sizeof estrito.
2. Offset exato de cada campo em conformidade com C99 e especificação SBE v2.
3. Paridade bidirecional com os codecs de `src.puma_sbe`.
4. Semântica zero-copy com memoryview, bytearray e ponteiros ctypes.
5. Invariantes de custódia depositária e reconciliação multilateral.
6. HFT Order Ring Buffer com alinhamento de 64 bytes (L1 Cache Line).
"""

import ctypes
from pathlib import Path
import struct
import sys
import unittest

# Garante que a raiz do repositório esteja no sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.c_abi_compat import (
    B3_CACHE_LINE_SIZE,
    CENT_SCALE,
    ClearingSubsystem,
    CustodyPositionStruct,
    DEFAULT_SCHEMA_ID,
    DEFAULT_SCHEMA_VERSION,
    ExecType,
    ExecutionReportStruct,
    InstructionStatus,
    MultilateralNetPositionStruct,
    MultilateralTransferStruct,
    NewOrderSingleStruct,
    OrderBufferEntryStruct,
    OrderCancelStruct,
    OrderRingBufferHeaderStruct,
    OrdStatus,
    PRICE_SCALE,
    SbeHeaderStruct,
    SettlementCycle,
    Side,
    TEMPLATE_EXECUTION_REPORT,
    TEMPLATE_NEW_ORDER,
    TEMPLATE_ORDER_CANCEL,
    TimeInForce,
    ZeroCopyOrderRingBuffer,
    batch_decode_sbe_stream,
    zero_copy_from_buffer,
    zero_copy_view,
)
from src.puma_sbe import (
    ExecutionReportBinary,
    NewOrderSingleBinary,
    OrderCancelBinary,
    SbeHeader,
)


class TestCAbiMemoryLayoutAndSizes(unittest.TestCase):
    """Valida tamanhos de estruturas em memória (sizeof) e alinhamentos de bytes."""

    def test_sbe_wire_sizes(self):
        """As estruturas de mensageria de rede SBE devem ser estritamente empacotadas (_pack_ = 1)."""
        self.assertEqual(ctypes.sizeof(SbeHeaderStruct), 8)
        self.assertEqual(ctypes.sizeof(NewOrderSingleStruct), 42)
        self.assertEqual(ctypes.sizeof(OrderCancelStruct), 36)
        self.assertEqual(ctypes.sizeof(ExecutionReportStruct), 62)

    def test_in_memory_and_cache_line_sizes(self):
        """Estruturas em memória devem ter alinhamento natural de 64-bit / cache-line."""
        self.assertEqual(ctypes.sizeof(OrderBufferEntryStruct), 64)
        self.assertEqual(ctypes.sizeof(OrderBufferEntryStruct), B3_CACHE_LINE_SIZE)
        self.assertEqual(ctypes.sizeof(OrderRingBufferHeaderStruct), 32)
        self.assertEqual(ctypes.sizeof(CustodyPositionStruct), 104)
        self.assertEqual(ctypes.sizeof(MultilateralTransferStruct), 72)
        self.assertEqual(ctypes.sizeof(MultilateralNetPositionStruct), 48)


class TestCAbiFieldOffsets(unittest.TestCase):
    """Valida offsets exatos em bytes para garantir compatibilidade binária perfeita com C/Rust/Go."""

    def test_sbe_header_offsets(self):
        self.assertEqual(SbeHeaderStruct.block_length.offset, 0)
        self.assertEqual(SbeHeaderStruct.template_id.offset, 2)
        self.assertEqual(SbeHeaderStruct.schema_id.offset, 4)
        self.assertEqual(SbeHeaderStruct.version.offset, 6)

    def test_new_order_single_offsets(self):
        self.assertEqual(NewOrderSingleStruct.header.offset, 0)
        self.assertEqual(NewOrderSingleStruct.cl_ord_id.offset, 8)
        self.assertEqual(NewOrderSingleStruct.security_id.offset, 16)
        self.assertEqual(NewOrderSingleStruct.side.offset, 24)
        self.assertEqual(NewOrderSingleStruct.order_qty.offset, 25)
        self.assertEqual(NewOrderSingleStruct.price_scaled.offset, 29)
        self.assertEqual(NewOrderSingleStruct.time_in_force.offset, 37)
        self.assertEqual(NewOrderSingleStruct.account.offset, 38)

    def test_order_cancel_offsets(self):
        self.assertEqual(OrderCancelStruct.header.offset, 0)
        self.assertEqual(OrderCancelStruct.cl_ord_id.offset, 8)
        self.assertEqual(OrderCancelStruct.orig_cl_ord_id.offset, 16)
        self.assertEqual(OrderCancelStruct.security_id.offset, 24)
        self.assertEqual(OrderCancelStruct.account.offset, 32)

    def test_execution_report_offsets(self):
        self.assertEqual(ExecutionReportStruct.header.offset, 0)
        self.assertEqual(ExecutionReportStruct.order_id.offset, 8)
        self.assertEqual(ExecutionReportStruct.cl_ord_id.offset, 16)
        self.assertEqual(ExecutionReportStruct.exec_id.offset, 24)
        self.assertEqual(ExecutionReportStruct.exec_type.offset, 32)
        self.assertEqual(ExecutionReportStruct.ord_status.offset, 33)
        self.assertEqual(ExecutionReportStruct.security_id.offset, 34)
        self.assertEqual(ExecutionReportStruct.cum_qty.offset, 42)
        self.assertEqual(ExecutionReportStruct.leaves_qty.offset, 46)
        self.assertEqual(ExecutionReportStruct.last_px_scaled.offset, 50)
        self.assertEqual(ExecutionReportStruct.last_qty.offset, 58)

    def test_order_buffer_entry_offsets(self):
        self.assertEqual(OrderBufferEntryStruct.entry_id.offset, 0)
        self.assertEqual(OrderBufferEntryStruct.timestamp_ns.offset, 8)
        self.assertEqual(OrderBufferEntryStruct.cl_ord_id.offset, 16)
        self.assertEqual(OrderBufferEntryStruct.security_id.offset, 24)
        self.assertEqual(OrderBufferEntryStruct.price_scaled.offset, 32)
        self.assertEqual(OrderBufferEntryStruct.quantity.offset, 40)
        self.assertEqual(OrderBufferEntryStruct.account.offset, 44)
        self.assertEqual(OrderBufferEntryStruct.side.offset, 48)
        self.assertEqual(OrderBufferEntryStruct.time_in_force.offset, 49)
        self.assertEqual(OrderBufferEntryStruct.order_status.offset, 50)
        self.assertEqual(OrderBufferEntryStruct._pad.offset, 51)
        self.assertEqual(OrderBufferEntryStruct.symbol.offset, 56)

    def test_custody_position_offsets(self):
        self.assertEqual(CustodyPositionStruct.participant_id.offset, 0)
        self.assertEqual(CustodyPositionStruct._pad0.offset, 4)
        self.assertEqual(CustodyPositionStruct.account_id.offset, 8)
        self.assertEqual(CustodyPositionStruct.security_id.offset, 16)
        self.assertEqual(CustodyPositionStruct.available_qty.offset, 24)
        self.assertEqual(CustodyPositionStruct.blocked_margin_qty.offset, 32)
        self.assertEqual(CustodyPositionStruct.blocked_loan_qty.offset, 40)
        self.assertEqual(CustodyPositionStruct.blocked_judicial_qty.offset, 48)
        self.assertEqual(CustodyPositionStruct.total_qty.offset, 56)
        self.assertEqual(CustodyPositionStruct.updated_at_ns.offset, 64)
        self.assertEqual(CustodyPositionStruct.symbol.offset, 72)
        self.assertEqual(CustodyPositionStruct.isin.offset, 88)
        self.assertEqual(CustodyPositionStruct._pad1.offset, 100)

    def test_multilateral_transfer_offsets(self):
        self.assertEqual(MultilateralTransferStruct.transfer_id.offset, 0)
        self.assertEqual(MultilateralTransferStruct.instruction_id.offset, 8)
        self.assertEqual(MultilateralTransferStruct.cycle_id.offset, 16)
        self.assertEqual(MultilateralTransferStruct.subsystem.offset, 20)
        self.assertEqual(MultilateralTransferStruct.debtor_member_id.offset, 24)
        self.assertEqual(MultilateralTransferStruct.creditor_member_id.offset, 28)
        self.assertEqual(MultilateralTransferStruct.gross_amount_cents.offset, 32)
        self.assertEqual(MultilateralTransferStruct.asset_id.offset, 40)
        self.assertEqual(MultilateralTransferStruct.asset_quantity.offset, 48)
        self.assertEqual(MultilateralTransferStruct.status.offset, 56)
        self.assertEqual(MultilateralTransferStruct._pad0.offset, 60)
        self.assertEqual(MultilateralTransferStruct.timestamp_ns.offset, 64)

    def test_multilateral_net_position_offsets(self):
        self.assertEqual(MultilateralNetPositionStruct.member_id.offset, 0)
        self.assertEqual(MultilateralNetPositionStruct.cycle_id.offset, 4)
        self.assertEqual(MultilateralNetPositionStruct.gross_receivables_cents.offset, 8)
        self.assertEqual(MultilateralNetPositionStruct.gross_payables_cents.offset, 16)
        self.assertEqual(MultilateralNetPositionStruct.net_balance_cents.offset, 24)
        self.assertEqual(MultilateralNetPositionStruct.status.offset, 32)
        self.assertEqual(MultilateralNetPositionStruct._pad0.offset, 36)
        self.assertEqual(MultilateralNetPositionStruct.updated_at_ns.offset, 40)


class TestPumaSbeParity(unittest.TestCase):
    """Valida paridade e interoperabilidade total entre ctypes C-ABI e python dataclasses."""

    def test_new_order_single_roundtrip_parity(self):
        # 1. Cria com dataclass puma_sbe
        sbe_order = NewOrderSingleBinary(
            cl_ord_id=987654321,
            security_id=102030,
            side=Side.BUY,
            order_qty=5000,
            price=38.75,
            time_in_force=TimeInForce.DAY,
            account=12345,
        )
        wire_bytes = sbe_order.encode()
        self.assertEqual(len(wire_bytes), 42)

        # 2. Decodifica via ctypes struct zero-copy / buffer
        c_order = NewOrderSingleStruct.from_buffer_copy_safe(wire_bytes)
        self.assertEqual(c_order.header.block_length, 34)
        self.assertEqual(c_order.header.template_id, TEMPLATE_NEW_ORDER)
        self.assertEqual(c_order.header.schema_id, DEFAULT_SCHEMA_ID)
        self.assertEqual(c_order.header.version, DEFAULT_SCHEMA_VERSION)
        self.assertEqual(c_order.cl_ord_id, 987654321)
        self.assertEqual(c_order.security_id, 102030)
        self.assertEqual(c_order.side, 1)
        self.assertEqual(c_order.order_qty, 5000)
        self.assertAlmostEqual(c_order.price, 38.75, places=6)
        self.assertEqual(c_order.price_scaled, 3_875_000_000)
        self.assertEqual(c_order.time_in_force, 0)
        self.assertEqual(c_order.account, 12345)

        # 3. Serializa via ctypes e decodifica de volta via puma_sbe
        c_bytes = c_order.as_bytes()
        self.assertEqual(c_bytes, wire_bytes)

        redecoded = NewOrderSingleBinary.decode(c_bytes)
        self.assertEqual(redecoded.cl_ord_id, sbe_order.cl_ord_id)
        self.assertEqual(redecoded.security_id, sbe_order.security_id)
        self.assertEqual(redecoded.side, sbe_order.side)
        self.assertEqual(redecoded.order_qty, sbe_order.order_qty)
        self.assertAlmostEqual(redecoded.price, sbe_order.price, places=6)
        self.assertEqual(redecoded.account, sbe_order.account)

    def test_order_cancel_parity(self):
        sbe_cancel = OrderCancelBinary(
            cl_ord_id=8888,
            orig_cl_ord_id=7777,
            security_id=9999,
            account=54321,
        )
        wire_bytes = sbe_cancel.encode()
        self.assertEqual(len(wire_bytes), 36)

        c_cancel = OrderCancelStruct.from_buffer_copy_safe(wire_bytes)
        self.assertEqual(c_cancel.header.template_id, TEMPLATE_ORDER_CANCEL)
        self.assertEqual(c_cancel.cl_ord_id, 8888)
        self.assertEqual(c_cancel.orig_cl_ord_id, 7777)
        self.assertEqual(c_cancel.security_id, 9999)
        self.assertEqual(c_cancel.account, 54321)

        c_bytes = c_cancel.as_bytes()
        self.assertEqual(c_bytes, wire_bytes)

    def test_execution_report_parity(self):
        sbe_exec = ExecutionReportBinary(
            order_id=1001,
            cl_ord_id=1002,
            exec_id=1003,
            exec_type=ExecType.PARTIAL_FILL,
            ord_status=OrdStatus.PARTIALLY_FILLED,
            security_id=2001,
            cum_qty=400,
            leaves_qty=600,
            last_px=115.50,
            last_qty=400,
        )
        wire_bytes = sbe_exec.encode()
        self.assertEqual(len(wire_bytes), 62)

        c_exec = ExecutionReportStruct.from_buffer_copy_safe(wire_bytes)
        self.assertEqual(c_exec.header.template_id, TEMPLATE_EXECUTION_REPORT)
        self.assertEqual(c_exec.order_id, 1001)
        self.assertEqual(c_exec.cl_ord_id, 1002)
        self.assertEqual(c_exec.exec_id, 1003)
        self.assertEqual(c_exec.exec_type, 1)
        self.assertEqual(c_exec.ord_status, 1)
        self.assertEqual(c_exec.security_id, 2001)
        self.assertEqual(c_exec.cum_qty, 400)
        self.assertEqual(c_exec.leaves_qty, 600)
        self.assertAlmostEqual(c_exec.last_px, 115.50, places=6)
        self.assertEqual(c_exec.last_qty, 400)

        c_bytes = c_exec.as_bytes()
        self.assertEqual(c_bytes, wire_bytes)


class TestZeroCopyMechanics(unittest.TestCase):
    """Valida semântica de mutabilidade zero-copy através de ponteiros, bytearrays e memoryviews."""

    def test_in_place_bytearray_mutation(self):
        """Modificar o struct ctypes deve alterar imediatamente os bytes subjacentes sem cópia."""
        buf = bytearray(42)
        c_order = NewOrderSingleStruct.from_buffer(buf)

        c_order.populate(
            cl_ord_id=123456789,
            security_id=98765,
            side=Side.SELL,
            order_qty=2500,
            price=45.20,
            account=1001,
        )

        # Checa que os bytes no bytearray foram alterados diretamente
        block_len, tpl_id, schema, ver = struct.unpack_from("<HHHH", buf, 0)
        self.assertEqual(tpl_id, TEMPLATE_NEW_ORDER)
        self.assertEqual(block_len, 34)

        cl_ord_id, sec_id, side, qty, px_scaled = struct.unpack_from("<QQBIq", buf, 8)
        self.assertEqual(cl_ord_id, 123456789)
        self.assertEqual(sec_id, 98765)
        self.assertEqual(side, 2)
        self.assertEqual(qty, 2500)
        self.assertEqual(px_scaled, 4_520_000_000)

        # Agora muta o bytearray externamente e checa reflexão instantânea no struct
        struct.pack_into("<I", buf, 25, 9999)  # Altera order_qty no offset 25
        self.assertEqual(c_order.order_qty, 9999)

    def test_memoryview_export_and_slice(self):
        """Exportar memoryview deve permitir fatiamento e leitura direta de memória C."""
        order = NewOrderSingleStruct()
        order.populate(
            cl_ord_id=1,
            security_id=2,
            side=1,
            order_qty=100,
            price=10.0,
            account=5,
        )

        mv = order.as_memoryview()
        self.assertEqual(len(mv), 42)
        self.assertEqual(mv.readonly, False)

        # Modifica via memoryview slice
        mv[24] = 2  # Muda side de 1 para 2
        self.assertEqual(order.side, 2)

    def test_pointer_address_mapping(self):
        """Mapeamento a partir de endereço de memória puro (from_pointer_address)."""
        buf = bytearray(42)
        c_order1 = NewOrderSingleStruct.from_buffer(buf)
        c_order1.cl_ord_id = 424242

        addr = ctypes.addressof(c_order1)
        self.assertGreater(addr, 0)

        c_order2 = NewOrderSingleStruct.from_pointer_address(addr)
        self.assertEqual(c_order2.cl_ord_id, 424242)

        c_order2.cl_ord_id = 999
        self.assertEqual(c_order1.cl_ord_id, 999)

    def test_batch_stream_zero_copy_decoder(self):
        """Decodificação em fluxo contínuo de múltiplos templates sem alocação intermediária."""
        o1 = NewOrderSingleStruct()
        o1.populate(cl_ord_id=1, security_id=10, side=1, order_qty=100, price=20.0, account=1)

        c1 = OrderCancelStruct()
        c1.populate(cl_ord_id=2, orig_cl_ord_id=1, security_id=10, account=1)

        e1 = ExecutionReportStruct()
        e1.populate(
            order_id=1,
            cl_ord_id=1,
            exec_id=101,
            exec_type=ExecType.FILL,
            ord_status=OrdStatus.FILLED,
            security_id=10,
            cum_qty=100,
            leaves_qty=0,
            last_px=20.0,
            last_qty=100,
        )

        stream = bytearray()
        stream.extend(o1.as_bytes())
        stream.extend(c1.as_bytes())
        stream.extend(e1.as_bytes())
        self.assertEqual(len(stream), 42 + 36 + 62)

        decoded = list(batch_decode_sbe_stream(stream))
        self.assertEqual(len(decoded), 3)

        tpl1, msg1 = decoded[0]
        self.assertEqual(tpl1, TEMPLATE_NEW_ORDER)
        self.assertEqual(msg1.cl_ord_id, 1)

        tpl2, msg2 = decoded[1]
        self.assertEqual(tpl2, TEMPLATE_ORDER_CANCEL)
        self.assertEqual(msg2.orig_cl_ord_id, 1)

        tpl3, msg3 = decoded[2]
        self.assertEqual(tpl3, TEMPLATE_EXECUTION_REPORT)
        self.assertEqual(msg3.exec_id, 101)


class TestOrderRingBuffer(unittest.TestCase):
    """Testa buffer circular de ordens HFT alinhado em cache-line (64 bytes)."""

    def test_ring_buffer_operations(self):
        ring = ZeroCopyOrderRingBuffer(capacity=16)
        self.assertEqual(ring.capacity, 16)
        self.assertEqual(ring.entry_size, 64)

        # Escreve primeira ordem
        idx0 = ring.write_entry(
            entry_id=1,
            timestamp_ns=1000000,
            cl_ord_id=101,
            security_id=999,
            price=25.50,
            quantity=500,
            account=1234,
            side=Side.BUY,
            symbol="PETR4",
        )
        self.assertEqual(idx0, 0)
        self.assertEqual(ring.header.head, 1)

        entry0 = ring.get_entry(0)
        self.assertEqual(entry0.entry_id, 1)
        self.assertEqual(entry0.cl_ord_id, 101)
        self.assertAlmostEqual(entry0.price, 25.50, places=6)
        self.assertEqual(entry0.symbol_str, "PETR4")

        # Escreve segunda ordem
        idx1 = ring.write_entry(
            entry_id=2,
            timestamp_ns=2000000,
            cl_ord_id=102,
            security_id=888,
            price=42.10,
            quantity=1000,
            account=5678,
            side=Side.SELL,
            symbol="VALE3",
        )
        self.assertEqual(idx1, 1)
        entry1 = ring.get_entry(1)
        self.assertEqual(entry1.symbol_str, "VALE3")
        self.assertEqual(entry1.side, 2)


class TestCustodyAndClearingStructures(unittest.TestCase):
    """Testa estruturas de custódia e transferências multilaterais do Barramento Central."""

    def test_custody_position_consistency_invariant(self):
        """Verifica a equação canônica da custódia B3: Total == Livre + Bloqueios."""
        pos = CustodyPositionStruct()
        pos.participant_id = 88
        pos.account_id = 998877
        pos.security_id = 100200
        pos.symbol_str = "B3SA3"
        pos.isin_str = "BRB3SAACNOR6"
        pos.available_qty = 10000
        pos.blocked_margin_qty = 2000
        pos.blocked_loan_qty = 3000
        pos.blocked_judicial_qty = 500
        pos.recalculate_total()

        self.assertEqual(pos.total_qty, 15500)
        self.assertTrue(pos.is_consistent())
        self.assertEqual(pos.symbol_str, "B3SA3")
        self.assertEqual(pos.isin_str, "BRB3SAACNOR6")

        # Violação do invariante
        pos.total_qty = 99999
        self.assertFalse(pos.is_consistent())

    def test_multilateral_transfer_and_netting(self):
        """Testa montantes em centavos e posições líquidas multilaterais."""
        transfer = MultilateralTransferStruct()
        transfer.transfer_id = 1
        transfer.instruction_id = 100
        transfer.cycle_id = SettlementCycle.MULTILATERAL_CLOSE
        transfer.subsystem = ClearingSubsystem.EQUITIES
        transfer.debtor_member_id = 3
        transfer.creditor_member_id = 88
        transfer.gross_amount = 1_500_250.75  # R$ 1.500.250,75
        transfer.asset_id = 102030
        transfer.asset_quantity = 50000
        transfer.status = InstructionStatus.SETTLED

        self.assertEqual(transfer.gross_amount_cents, 150_025_075)
        self.assertAlmostEqual(transfer.gross_amount, 1_500_250.75, places=2)

        # Teste de net position multilateral
        net_pos = MultilateralNetPositionStruct()
        net_pos.member_id = 88
        net_pos.cycle_id = SettlementCycle.MULTILATERAL_CLOSE
        net_pos.gross_receivables_cents = 500_000_00
        net_pos.gross_payables_cents = 350_000_00
        net_balance = net_pos.compute_net_balance()

        self.assertEqual(net_balance, 150_000_00)
        self.assertTrue(net_pos.is_net_balanced())
        self.assertEqual(net_pos.net_balance, 150_000.0)

    def test_native_c11_shared_library_execution(self):
        """Executa funções nativas C11 compiladas em ffi/libb3_atlas_c_abi.so via ctypes.CDLL."""
        from src.c_abi_compat import load_native_c_abi_library
        lib = load_native_c_abi_library()
        if lib is None:
            self.skipTest("Shared library libb3_atlas_c_abi.so not present on this platform")

        order = NewOrderSingleStruct()
        order.populate(cl_ord_id=777888, security_id=1001, side=Side.BUY, order_qty=500, price=38.75, account=42)
        raw_buf = ctypes.create_string_buffer(42)
        written = lib.b3_sbe_encode_new_order(ctypes.byref(order), raw_buf, 42)
        self.assertEqual(written, 42)

        decoded = NewOrderSingleStruct()
        rc = lib.b3_sbe_decode_new_order(raw_buf, 42, ctypes.byref(decoded))
        self.assertEqual(rc, 0)
        self.assertEqual(decoded.cl_ord_id, 777888)
        self.assertAlmostEqual(decoded.price, 38.75, places=6)

        pos = CustodyPositionStruct()
        pos.available_qty = 1000
        pos.blocked_margin_qty = 500
        pos.blocked_loan_qty = 200
        pos.blocked_judicial_qty = 0
        pos.recalculate_total()
        self.assertTrue(lib.b3_custody_position_is_consistent(ctypes.byref(pos)))

        net = lib.b3_multilateral_compute_net(900_000_00, 400_000_00)
        self.assertEqual(net, 500_000_00)

    def test_wasm_binary_compilation_and_execution(self):
        """Valida o módulo binário WebAssembly ffi/b3_atlas_c_abi.wasm."""
        from pathlib import Path
        import shutil
        import subprocess

        wasm_path = Path(__file__).resolve().parent.parent / "ffi" / "b3_atlas_c_abi.wasm"
        self.assertTrue(wasm_path.is_file(), "ffi/b3_atlas_c_abi.wasm deve existir")
        raw = wasm_path.read_bytes()
        self.assertEqual(raw[:8], b"\x00asm\x01\x00\x00\x00", "Cabeçalho W3C WASM v1 inválido")

        if shutil.which("node"):
            res = subprocess.run(
                [
                    "node",
                    "-e",
                    (
                        'const fs = require("fs");'
                        'const bytes = fs.readFileSync("ffi/b3_atlas_c_abi.wasm");'
                        'WebAssembly.instantiate(bytes).then(({ instance }) => {'
                        '  const e = instance.exports;'
                        '  if (e.b3_multilateral_compute_net(900n, 400n) !== 500n) process.exit(1);'
                        '  const v = new DataView(e.memory.buffer);'
                        '  v.setUint16(0, 34, true); v.setUint16(2, 101, true);'
                        '  if (e.b3_sbe_validate_new_order(0, 42) !== 1) process.exit(2);'
                        '});'
                    ),
                ],
                cwd=str(wasm_path.parent.parent),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, f"Falha na execução WASM: {res.stderr}")


if __name__ == "__main__":
    unittest.main()
