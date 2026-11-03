"""Bateria de Testes Automatizados para Mensageria Binária PUMA SBE (FL-013).

Testa:
- Roundtrip de codificação e decodificação (NewOrderSingle, OrderCancel, ExecutionReport).
- Carregamento e validação de fixtures binárias reais (.bin).
- Rejeição de payloads truncados (cabeçalho ou bloco de dados).
- Rejeição de templates desconhecidos.
- Validações semânticas de campos (quantidades, preços, lados, contas).
- Despacho automático via PumaSbeCodec.
"""

from pathlib import Path
import struct
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH independentemente do diretório de execução
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.puma_sbe import (
    ExecType,
    ExecutionReportBinary,
    NewOrderSingleBinary,
    OrderCancelBinary,
    OrdStatus,
    PumaSbeCodec,
    PumaSbeError,
    SbeHeader,
    SbeTruncatedPayloadError,
    SbeUnknownTemplateError,
    SbeValidationError,
    Side,
    TimeInForce,
)

FIXTURES_DIR = REPO_ROOT / "fixtures"


class TestPumaSbeHeader(unittest.TestCase):
    """Testa codificação e decodificação do cabeçalho padrão SBE de 8 bytes."""

    def test_header_encode_decode_roundtrip(self):
        """Cabeçalho deve ser codificado e decodificado perfeitamente em 8 bytes Little-Endian."""
        header = SbeHeader(
            block_length=34,
            template_id=101,
            schema_id=1,
            version=1,
        )
        raw = header.encode()
        self.assertEqual(len(raw), 8)

        # Testa decodificação manual dos campos
        block_len, tpl_id, schema, ver = struct.unpack("<HHHH", raw)
        self.assertEqual(block_len, 34)
        self.assertEqual(tpl_id, 101)
        self.assertEqual(schema, 1)
        self.assertEqual(ver, 1)

        # Testa decodificação pela classe
        decoded = SbeHeader.decode(raw)
        self.assertEqual(decoded.block_length, 34)
        self.assertEqual(decoded.template_id, 101)
        self.assertEqual(decoded.schema_id, 1)
        self.assertEqual(decoded.version, 1)

    def test_header_truncated_raises_error(self):
        """Payload com menos de 8 bytes deve disparar SbeTruncatedPayloadError."""
        for short_data in [b"", b"\x01\x02", b"\x00" * 7]:
            with self.subTest(length=len(short_data)):
                with self.assertRaises(SbeTruncatedPayloadError):
                    SbeHeader.decode(short_data)


class TestPumaSbeNewOrderSingle(unittest.TestCase):
    """Testa a mensagem NewOrderSingleBinary (TemplateId = 101)."""

    def setUp(self):
        self.order = NewOrderSingleBinary(
            cl_ord_id=2026110300000001,
            security_id=105829,
            side=Side.BUY,
            order_qty=500,
            price=34.50,
            time_in_force=TimeInForce.DAY,
            account=987654,
        )

    def test_new_order_roundtrip(self):
        """Codificação e decodificação de NewOrderSingle deve preservar todos os campos."""
        encoded = self.order.encode()
        self.assertEqual(len(encoded), 42)  # 8 header + 34 payload

        decoded = NewOrderSingleBinary.decode(encoded)
        self.assertEqual(decoded.cl_ord_id, self.order.cl_ord_id)
        self.assertEqual(decoded.security_id, self.order.security_id)
        self.assertEqual(decoded.side, Side.BUY)
        self.assertEqual(decoded.order_qty, 500)
        self.assertAlmostEqual(decoded.price, 34.50, places=4)
        self.assertEqual(decoded.price_scaled, 3450000000)
        self.assertEqual(decoded.time_in_force, TimeInForce.DAY)
        self.assertEqual(decoded.account, 987654)
        self.assertEqual(decoded.header.template_id, 101)
        self.assertEqual(decoded.header.block_length, 34)

    def test_decode_from_fixture_file(self):
        """Valida que o arquivo binário fixture puma_sbe_new_order.bin decodifica com sucesso."""
        fixture_path = FIXTURES_DIR / "puma_sbe_new_order.bin"
        self.assertTrue(fixture_path.exists(), f"Fixture não encontrada: {fixture_path}")

        raw_data = fixture_path.read_bytes()
        self.assertEqual(len(raw_data), 42)

        # Decodifica usando a classe específica e o codec genérico
        order_direct = NewOrderSingleBinary.decode(raw_data)
        order_codec = PumaSbeCodec.decode(raw_data)

        self.assertIsInstance(order_codec, NewOrderSingleBinary)
        self.assertEqual(order_direct.cl_ord_id, 2026110300000001)
        self.assertEqual(order_direct.security_id, 105829)
        self.assertEqual(order_direct.side, Side.BUY)
        self.assertEqual(order_direct.order_qty, 1000)
        self.assertAlmostEqual(order_direct.price, 34.50, places=4)
        self.assertEqual(order_direct.account, 987654)

    def test_semantic_validations(self):
        """Validações semânticas de campos devem impedir emissão de ordens inválidas."""
        # Quantidade zerada ou negativa
        with self.assertRaises(SbeValidationError):
            NewOrderSingleBinary(
                cl_ord_id=1, security_id=1, side=Side.BUY, order_qty=0, price=10.0, account=123
            ).encode()

        with self.assertRaises(SbeValidationError):
            NewOrderSingleBinary(
                cl_ord_id=1, security_id=1, side=Side.BUY, order_qty=-5, price=10.0, account=123
            ).encode()

        # Preço zerado ou negativo
        with self.assertRaises(SbeValidationError):
            NewOrderSingleBinary(
                cl_ord_id=1, security_id=1, side=Side.BUY, order_qty=100, price=0.0, account=123
            ).encode()

        with self.assertRaises(SbeValidationError):
            NewOrderSingleBinary(
                cl_ord_id=1, security_id=1, side=Side.BUY, order_qty=100, price=-12.5, account=123
            ).encode()

        # Lado inválido (diferente de 1 ou 2)
        with self.assertRaises(SbeValidationError):
            NewOrderSingleBinary(
                cl_ord_id=1, security_id=1, side=99, order_qty=100, price=10.0, account=123
            ).encode()

        # TimeInForce inválido
        with self.assertRaises(SbeValidationError):
            NewOrderSingleBinary(
                cl_ord_id=1, security_id=1, side=Side.SELL, order_qty=100, price=10.0, time_in_force=99, account=123
            ).encode()

        # Conta zerada
        with self.assertRaises(SbeValidationError):
            NewOrderSingleBinary(
                cl_ord_id=1, security_id=1, side=Side.BUY, order_qty=100, price=10.0, account=0
            ).encode()

        # ClOrdID zerado
        with self.assertRaises(SbeValidationError):
            NewOrderSingleBinary(
                cl_ord_id=0, security_id=1, side=Side.BUY, order_qty=100, price=10.0, account=123
            ).encode()


class TestPumaSbeOrderCancel(unittest.TestCase):
    """Testa a mensagem OrderCancelBinary (TemplateId = 102)."""

    def test_cancel_roundtrip(self):
        """Codificação e decodificação de OrderCancelBinary deve preservar integridade."""
        cancel = OrderCancelBinary(
            cl_ord_id=2026110300000002,
            orig_cl_ord_id=2026110300000001,
            security_id=105829,
            account=987654,
        )
        encoded = cancel.encode()
        self.assertEqual(len(encoded), 36)  # 8 header + 28 payload

        decoded = OrderCancelBinary.decode(encoded)
        self.assertEqual(decoded.cl_ord_id, 2026110300000002)
        self.assertEqual(decoded.orig_cl_ord_id, 2026110300000001)
        self.assertEqual(decoded.security_id, 105829)
        self.assertEqual(decoded.account, 987654)
        self.assertEqual(decoded.header.template_id, 102)
        self.assertEqual(decoded.header.block_length, 28)

        # Testa decodificação pelo Codec genérico
        decoded_codec = PumaSbeCodec.decode(encoded)
        self.assertIsInstance(decoded_codec, OrderCancelBinary)
        self.assertEqual(decoded_codec.orig_cl_ord_id, 2026110300000001)

    def test_cancel_semantic_validations(self):
        """Campos zerados devem disparar SbeValidationError."""
        with self.assertRaises(SbeValidationError):
            OrderCancelBinary(cl_ord_id=0, orig_cl_ord_id=1, security_id=1, account=1).encode()

        with self.assertRaises(SbeValidationError):
            OrderCancelBinary(cl_ord_id=1, orig_cl_ord_id=0, security_id=1, account=1).encode()

        with self.assertRaises(SbeValidationError):
            OrderCancelBinary(cl_ord_id=1, orig_cl_ord_id=1, security_id=0, account=1).encode()

        with self.assertRaises(SbeValidationError):
            OrderCancelBinary(cl_ord_id=1, orig_cl_ord_id=1, security_id=1, account=0).encode()


class TestPumaSbeExecutionReport(unittest.TestCase):
    """Testa a mensagem ExecutionReportBinary (TemplateId = 103)."""

    def setUp(self):
        self.exec_rep = ExecutionReportBinary(
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

    def test_execution_report_roundtrip(self):
        """Codificação e decodificação de ExecutionReport deve preservar todos os campos."""
        encoded = self.exec_rep.encode()
        self.assertEqual(len(encoded), 62)  # 8 header + 54 payload

        decoded = ExecutionReportBinary.decode(encoded)
        self.assertEqual(decoded.order_id, 9001234567890)
        self.assertEqual(decoded.cl_ord_id, 2026110300000001)
        self.assertEqual(decoded.exec_id, 777000111222)
        self.assertEqual(decoded.exec_type, ExecType.FILL)
        self.assertEqual(decoded.ord_status, OrdStatus.FILLED)
        self.assertEqual(decoded.security_id, 105829)
        self.assertEqual(decoded.cum_qty, 1000)
        self.assertEqual(decoded.leaves_qty, 0)
        self.assertAlmostEqual(decoded.last_px, 34.50, places=4)
        self.assertEqual(decoded.last_px_scaled, 3450000000)
        self.assertEqual(decoded.last_qty, 1000)
        self.assertEqual(decoded.header.template_id, 103)
        self.assertEqual(decoded.header.block_length, 54)

    def test_decode_from_fixture_file(self):
        """Valida que o arquivo binário fixture puma_sbe_execution.bin decodifica com sucesso."""
        fixture_path = FIXTURES_DIR / "puma_sbe_execution.bin"
        self.assertTrue(fixture_path.exists(), f"Fixture não encontrada: {fixture_path}")

        raw_data = fixture_path.read_bytes()
        self.assertEqual(len(raw_data), 62)

        report_direct = ExecutionReportBinary.decode(raw_data)
        report_codec = PumaSbeCodec.decode(raw_data)

        self.assertIsInstance(report_codec, ExecutionReportBinary)
        self.assertEqual(report_direct.order_id, 9001234567890)
        self.assertEqual(report_direct.exec_type, ExecType.FILL)
        self.assertEqual(report_direct.ord_status, OrdStatus.FILLED)
        self.assertEqual(report_direct.cum_qty, 1000)
        self.assertEqual(report_direct.leaves_qty, 0)
        self.assertAlmostEqual(report_direct.last_px, 34.50, places=4)
        self.assertEqual(report_direct.last_qty, 1000)

    def test_execution_report_validations(self):
        """Validações de integridade de valores de execução."""
        # ExecType inválido
        with self.assertRaises(SbeValidationError):
            ExecutionReportBinary(
                order_id=1, cl_ord_id=1, exec_id=1, exec_type=99, ord_status=OrdStatus.FILLED,
                security_id=1, cum_qty=100, leaves_qty=0, last_px=10.0, last_qty=100
            ).encode()

        # OrdStatus inválido
        with self.assertRaises(SbeValidationError):
            ExecutionReportBinary(
                order_id=1, cl_ord_id=1, exec_id=1, exec_type=ExecType.FILL, ord_status=99,
                security_id=1, cum_qty=100, leaves_qty=0, last_px=10.0, last_qty=100
            ).encode()

        # Quantidades negativas
        with self.assertRaises(SbeValidationError):
            ExecutionReportBinary(
                order_id=1, cl_ord_id=1, exec_id=1, exec_type=ExecType.FILL, ord_status=OrdStatus.FILLED,
                security_id=1, cum_qty=-1, leaves_qty=0, last_px=10.0, last_qty=100
            ).encode()

        with self.assertRaises(SbeValidationError):
            ExecutionReportBinary(
                order_id=1, cl_ord_id=1, exec_id=1, exec_type=ExecType.FILL, ord_status=OrdStatus.FILLED,
                security_id=1, cum_qty=100, leaves_qty=-1, last_px=10.0, last_qty=100
            ).encode()

        # Preço negativo
        with self.assertRaises(SbeValidationError):
            ExecutionReportBinary(
                order_id=1, cl_ord_id=1, exec_id=1, exec_type=ExecType.FILL, ord_status=OrdStatus.FILLED,
                security_id=1, cum_qty=100, leaves_qty=0, last_px=-5.0, last_qty=100
            ).encode()


class TestPumaSbeCodecAndErrorHandling(unittest.TestCase):
    """Testa PumaSbeCodec, tratamento de erros, payloads truncados e templates desconhecidos."""

    def test_truncated_header(self):
        """Payload com menos de 8 bytes deve disparar SbeTruncatedPayloadError."""
        for length in range(8):
            with self.subTest(length=length):
                with self.assertRaises(SbeTruncatedPayloadError):
                    PumaSbeCodec.decode(b"\x00" * length)

    def test_truncated_body_for_template_101(self):
        """Header válido mas body incompleto para NewOrderSingle (ex: 20 bytes ao invés de 42)."""
        header = SbeHeader(block_length=34, template_id=101).encode()
        truncated = header + (b"\x00" * 10)  # Total 18 bytes ao invés de 42
        with self.assertRaises(SbeTruncatedPayloadError):
            PumaSbeCodec.decode(truncated)

    def test_truncated_body_for_template_103(self):
        """Header válido mas body incompleto para ExecutionReport (ex: 40 bytes ao invés de 62)."""
        header = SbeHeader(block_length=54, template_id=103).encode()
        truncated = header + (b"\x00" * 30)  # Total 38 bytes ao invés de 62
        with self.assertRaises(SbeTruncatedPayloadError):
            PumaSbeCodec.decode(truncated)

    def test_unknown_template_id(self):
        """Template ID não cadastrado no catálogo deve disparar SbeUnknownTemplateError."""
        header = SbeHeader(block_length=10, template_id=999).encode()
        payload = header + (b"\x00" * 10)
        with self.assertRaises(SbeUnknownTemplateError):
            PumaSbeCodec.decode(payload)

    def test_divergent_template_id_in_specific_decoder(self):
        """Tentar decodificar pacote de Template 103 com NewOrderSingleBinary deve falhar."""
        exec_fixture = (FIXTURES_DIR / "puma_sbe_execution.bin").read_bytes()
        with self.assertRaises(SbeValidationError) as ctx:
            NewOrderSingleBinary.decode(exec_fixture)
        self.assertIn("TemplateId divergente", str(ctx.exception))

    def test_codec_encode_dispatch(self):
        """PumaSbeCodec.encode deve despachar corretamente cada tipo de mensagem."""
        order = NewOrderSingleBinary(
            cl_ord_id=1, security_id=105829, side=Side.BUY, order_qty=100, price=30.0, account=123
        )
        cancel = OrderCancelBinary(
            cl_ord_id=2, orig_cl_ord_id=1, security_id=105829, account=123
        )
        report = ExecutionReportBinary(
            order_id=10, cl_ord_id=1, exec_id=20, exec_type=ExecType.NEW, ord_status=OrdStatus.NEW,
            security_id=105829, cum_qty=0, leaves_qty=100, last_px=0.0, last_qty=0
        )

        self.assertEqual(len(PumaSbeCodec.encode(order)), 42)
        self.assertEqual(len(PumaSbeCodec.encode(cancel)), 36)
        self.assertEqual(len(PumaSbeCodec.encode(report)), 62)

        with self.assertRaises(TypeError):
            PumaSbeCodec.encode("not_a_message")  # type: ignore

    def test_price_scale_precision(self):
        """Preços fracionários devem ter precisão exata de 8 casas decimais."""
        prices = [0.01, 1.23456789, 34.50, 102.15, 999.99]
        for p in prices:
            with self.subTest(price=p):
                order = NewOrderSingleBinary(
                    cl_ord_id=1, security_id=105829, side=Side.BUY, order_qty=100, price=p, account=123
                )
                encoded = order.encode()
                decoded = NewOrderSingleBinary.decode(encoded)
                self.assertAlmostEqual(decoded.price, p, places=6)


if __name__ == "__main__":
    unittest.main()
