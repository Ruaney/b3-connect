"""Testes automatizados para o módulo de Captura de Operações em Bloco RTC (FL-007)."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
import sys
import unittest

# Garante que a raiz do repositório esteja no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.rtc_capture import (
    AllocationStatus,
    BatchIntegrityError,
    InvalidValueError,
    MarketType,
    MissingRequiredFieldError,
    NegativeOrZeroValueError,
    RTCError,
    RTCValidationError,
    TradeCaptureBatch,
    TradeCaptureParser,
    TradeRecord,
    TradeSide,
    parse_rtc_capture,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestRTCTradeCapture(unittest.TestCase):
    """Bateria de testes unitários e de integração do validador e parser RTC."""

    def setUp(self) -> None:
        self.parser = TradeCaptureParser()
        self.valid_fixture_path = FIXTURES_DIR / "rtc_trade_capture_valid.json"
        self.invalid_fixture_path = FIXTURES_DIR / "rtc_trade_capture_invalid.json"

    # -----------------------------------------------------------------------
    # 1. Testes de Ingestão de Lote Válido
    # -----------------------------------------------------------------------

    def test_parse_valid_batch_from_file(self) -> None:
        """Valida a leitura e parsing do arquivo JSON sintético válido com 3 operações."""
        batch = self.parser.parse_file(self.valid_fixture_path)

        # Metadados do lote
        self.assertEqual(batch.batch_id, "RTC-BATCH-20261103-001")
        self.assertEqual(batch.source, "RTC_PUMA")
        self.assertEqual(batch.timestamp, datetime(2026, 11, 3, 17, 30, 0, tzinfo=timezone.utc))
        self.assertEqual(batch.total_records, 3)
        self.assertEqual(len(batch.records), 3)

        # Trade 1: Ação à vista VALE3 (Compra em bloco, Não Alocado)
        t1 = batch.records[0]
        self.assertEqual(t1.trade_id, "RTC20261103-00000001")
        self.assertEqual(t1.trade_date, date(2026, 11, 3))
        self.assertEqual(t1.trade_time, time(10, 15, 22))
        self.assertEqual(t1.symbol, "VALE3")
        self.assertEqual(t1.isin, "BRVALEACNOR0")
        self.assertEqual(t1.market_type, MarketType.EQUITY)
        self.assertEqual(t1.side, TradeSide.BUY)
        self.assertEqual(t1.quantity, Decimal("50000"))
        self.assertEqual(t1.price, Decimal("62.50"))
        self.assertEqual(t1.financial_volume, Decimal("3125000.00"))
        self.assertEqual(t1.counterparty, "00000088")
        self.assertEqual(t1.executing_broker, "105")
        self.assertEqual(t1.clearing_broker, "105")
        self.assertTrue(t1.block_trade_flag)
        self.assertTrue(t1.is_block_trade)
        self.assertEqual(t1.allocation_status, AllocationStatus.UNALLOCATED)
        self.assertEqual(t1.exec_id, "PUMA-EXEC-VALE3-001")

        # Trade 2: Ação à vista PETR4 (Venda em bloco, Pendente de Alocação)
        t2 = batch.records[1]
        self.assertEqual(t2.trade_id, "RTC20261103-00000002")
        self.assertEqual(t2.trade_date, date(2026, 11, 3))
        self.assertEqual(t2.trade_time, time(11, 42, 5))
        self.assertEqual(t2.symbol, "PETR4")
        self.assertEqual(t2.isin, "BRPETRACNPR6")
        self.assertEqual(t2.market_type, MarketType.EQUITY)
        self.assertEqual(t2.side, TradeSide.SELL)
        self.assertEqual(t2.quantity, Decimal("100000"))
        self.assertEqual(t2.price, Decimal("38.40"))
        self.assertEqual(t2.financial_volume, Decimal("3840000.00"))
        self.assertEqual(t2.counterparty, "00000072")
        self.assertEqual(t2.allocation_status, AllocationStatus.PENDING)

        # Trade 3: Opção de Ação PETRL350 (Compra de Opção em bloco, Alocado)
        t3 = batch.records[2]
        self.assertEqual(t3.trade_id, "RTC20261103-00000003")
        self.assertEqual(t3.trade_date, date(2026, 11, 3))
        self.assertEqual(t3.trade_time, time(14, 8, 49))
        self.assertEqual(t3.symbol, "PETRL350")
        self.assertEqual(t3.isin, "BRPETR350001")
        self.assertEqual(t3.market_type, MarketType.OPTIONS)
        self.assertEqual(t3.side, TradeSide.BUY)
        self.assertEqual(t3.quantity, Decimal("200000"))
        self.assertEqual(t3.price, Decimal("1.85"))
        self.assertEqual(t3.financial_volume, Decimal("370000.00"))
        self.assertEqual(t3.counterparty, "00000015")
        self.assertEqual(t3.allocation_status, AllocationStatus.ALLOCATED)

        # Agregados do lote
        self.assertEqual(batch.total_quantity, Decimal("350000"))
        self.assertEqual(batch.total_financial_volume, Decimal("7335000.00"))

        # Métodos de consulta e filtragem
        self.assertEqual(len(batch.unallocated_trades()), 1)
        self.assertEqual(batch.unallocated_trades()[0].symbol, "VALE3")

        self.assertEqual(len(batch.pending_trades()), 1)
        self.assertEqual(batch.pending_trades()[0].symbol, "PETR4")

        self.assertEqual(len(batch.allocated_trades()), 1)
        self.assertEqual(batch.allocated_trades()[0].symbol, "PETRL350")

        self.assertEqual(len(batch.filter_by_symbol("VALE3")), 1)
        self.assertEqual(len(batch.filter_by_market(MarketType.OPTIONS)), 1)

    def test_parse_via_high_level_function(self) -> None:
        """Testa a função de conveniência parse_rtc_capture com diferentes tipos de entrada."""
        # 1. Por caminho de arquivo
        b1 = parse_rtc_capture(self.valid_fixture_path)
        self.assertEqual(b1.total_records, 3)

        # 2. Por string de caminho
        b2 = parse_rtc_capture(str(self.valid_fixture_path))
        self.assertEqual(b2.total_records, 3)

        # 3. Por conteúdo JSON string
        with open(self.valid_fixture_path, "r", encoding="utf-8") as f:
            content = f.read()
        b3 = parse_rtc_capture(content)
        self.assertEqual(b3.total_records, 3)

    def test_parse_snake_case_record(self) -> None:
        """Verifica compatibilidade com chaves no padrão snake_case."""
        data = {
            "trade_id": "RTC-SNAKE-01",
            "trade_date": "2026-11-03",
            "trade_time": "15:00:00",
            "symbol": "B3SA3",
            "isin": "BRB3SAACNOR6",
            "market_type": "EQUITY",
            "side": "BUY",
            "quantity": 1000,
            "price": "12.50",
            "counterparty": "00000099",
            "executing_broker": "105",
            "clearing_broker": "105",
            "block_trade_flag": True,
            "allocation_status": "Unallocated",
        }
        rec = self.parser.parse_record_dict(data)
        self.assertEqual(rec.trade_id, "RTC-SNAKE-01")
        self.assertEqual(rec.symbol, "B3SA3")
        self.assertEqual(rec.financial_volume, Decimal("12500.00"))

    def test_serialization_roundtrip(self) -> None:
        """Testa serialização to_dict() do lote e de registros."""
        batch = self.parser.parse_file(self.valid_fixture_path)

        # PascalCase
        dict_pascal = batch.to_dict(pascal_case=True)
        self.assertIn("BatchID", dict_pascal)
        self.assertIn("TotalFinancialVolume", dict_pascal)
        self.assertEqual(len(dict_pascal["Records"]), 3)

        # snake_case
        dict_snake = batch.to_dict(pascal_case=False)
        self.assertIn("batch_id", dict_snake)
        self.assertIn("total_financial_volume", dict_snake)
        self.assertEqual(len(dict_snake["records"]), 3)

    # -----------------------------------------------------------------------
    # 2. Testes de Rejeição de Lote Inválido (Fixtures e Regras)
    # -----------------------------------------------------------------------

    def test_parse_invalid_fixture_file_raises_error(self) -> None:
        """Testa que o arquivo de fixture inválido é imediatamente rejeitado pelo parser."""
        with self.assertRaises(RTCValidationError):
            self.parser.parse_file(self.invalid_fixture_path)

    def test_individual_records_in_invalid_fixture(self) -> None:
        """Verifica cada cenário de erro presente na fixture sintética rtc_trade_capture_invalid.json."""
        import json
        with open(self.invalid_fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        records = data["records"]

        # Registro 1: Falta TradeID
        with self.assertRaises(MissingRequiredFieldError) as cm1:
            self.parser.parse_record_dict(records[0], record_index=1)
        self.assertIn("TradeID", str(cm1.exception))

        # Registro 2: Quantidade negativa (-1000)
        with self.assertRaises(NegativeOrZeroValueError) as cm2:
            self.parser.parse_record_dict(records[1], record_index=2)
        self.assertEqual(cm2.exception.field_name, "Quantity")

        # Registro 3: Preço negativo (-25.80)
        with self.assertRaises(NegativeOrZeroValueError) as cm3:
            self.parser.parse_record_dict(records[2], record_index=3)
        self.assertEqual(cm3.exception.field_name, "Price")

        # Registro 4: Preço zerado (0.0)
        with self.assertRaises(NegativeOrZeroValueError) as cm4:
            self.parser.parse_record_dict(records[3], record_index=4)
        self.assertEqual(cm4.exception.field_name, "Price")

        # Registro 5: Side inválido ('HOLD')
        with self.assertRaises(InvalidValueError) as cm5:
            self.parser.parse_record_dict(records[4], record_index=5)
        self.assertIn("Lado da operação inválido", str(cm5.exception))

    # -----------------------------------------------------------------------
    # 3. Testes Granulares de Regras de Negócio e Casos de Borda
    # -----------------------------------------------------------------------

    def _base_valid_record_dict(self) -> dict:
        return {
            "TradeID": "T-TEST-001",
            "TradeDate": "2026-11-03",
            "TradeTime": "10:00:00",
            "Symbol": "VALE3",
            "ISIN": "BRVALEACNOR0",
            "MarketType": "EQUITY",
            "Side": "BUY",
            "Quantity": 1000,
            "Price": "50.00",
            "Counterparty": "00000088",
            "ExecutingBroker": "105",
            "ClearingBroker": "105",
            "BlockTradeFlag": True,
            "AllocationStatus": "Unallocated",
        }

    def test_rejection_missing_required_fields(self) -> None:
        """Verifica que a ausência de cada campo obrigatório gera MissingRequiredFieldError."""
        required_fields = [
            "TradeID",
            "TradeDate",
            "TradeTime",
            "Symbol",
            "ISIN",
            "MarketType",
            "Side",
            "Quantity",
            "Price",
            "Counterparty",
            "ExecutingBroker",
            "ClearingBroker",
            "BlockTradeFlag",
            "AllocationStatus",
        ]
        for field_name in required_fields:
            d = self._base_valid_record_dict()
            del d[field_name]
            with self.assertRaises(MissingRequiredFieldError, msg=f"Deveria falhar ao omitir {field_name}"):
                self.parser.parse_record_dict(d)

    def test_rejection_zero_and_negative_prices(self) -> None:
        """Verifica rejeição estrita de preços <= 0."""
        for invalid_price in ["0", "0.0", "-0.01", "-100.50"]:
            d = self._base_valid_record_dict()
            d["Price"] = invalid_price
            with self.assertRaises(NegativeOrZeroValueError):
                self.parser.parse_record_dict(d)

    def test_rejection_zero_and_negative_quantities(self) -> None:
        """Verifica rejeição estrita de quantidades <= 0."""
        for invalid_qty in [0, -1, -500]:
            d = self._base_valid_record_dict()
            d["Quantity"] = invalid_qty
            with self.assertRaises(NegativeOrZeroValueError):
                self.parser.parse_record_dict(d)

    def test_side_parsing_and_normalization(self) -> None:
        """Verifica normalização de variações válidas de Side e rejeição de inválidas."""
        valid_buy = ["BUY", "buy", "B", "b", "1", "COMPRA"]
        for val in valid_buy:
            d = self._base_valid_record_dict()
            d["Side"] = val
            rec = self.parser.parse_record_dict(d)
            self.assertEqual(rec.side, TradeSide.BUY)

        valid_sell = ["SELL", "sell", "S", "s", "2", "VENDA"]
        for val in valid_sell:
            d = self._base_valid_record_dict()
            d["Side"] = val
            rec = self.parser.parse_record_dict(d)
            self.assertEqual(rec.side, TradeSide.SELL)

        with self.assertRaises(InvalidValueError):
            d = self._base_valid_record_dict()
            d["Side"] = "UNKNOWN_SIDE"
            self.parser.parse_record_dict(d)

    def test_isin_validation(self) -> None:
        """Verifica validação de comprimento (12 caracteres) e formato de ISIN."""
        # ISIN curto
        d1 = self._base_valid_record_dict()
        d1["ISIN"] = "BRVALE"
        with self.assertRaises(InvalidValueError):
            self.parser.parse_record_dict(d1)

        # ISIN longo
        d2 = self._base_valid_record_dict()
        d2["ISIN"] = "BRVALEACNOR099"
        with self.assertRaises(InvalidValueError):
            self.parser.parse_record_dict(d2)

        # ISIN com caracteres inválidos
        d3 = self._base_valid_record_dict()
        d3["ISIN"] = "BRVALE*CNOR0"
        with self.assertRaises(InvalidValueError):
            self.parser.parse_record_dict(d3)

    def test_allocation_status_parsing(self) -> None:
        """Verifica parsing de status de alocação."""
        d = self._base_valid_record_dict()
        d["AllocationStatus"] = "Pending"
        rec = self.parser.parse_record_dict(d)
        self.assertEqual(rec.allocation_status, AllocationStatus.PENDING)

        d["AllocationStatus"] = "Allocated"
        rec = self.parser.parse_record_dict(d)
        self.assertEqual(rec.allocation_status, AllocationStatus.ALLOCATED)

        d["AllocationStatus"] = "Cancelled"
        with self.assertRaises(InvalidValueError):
            self.parser.parse_record_dict(d)

    def test_batch_integrity_error(self) -> None:
        """Verifica lançamento de BatchIntegrityError quando total_records diverge da quantidade real."""
        batch_dict = {
            "batch_id": "BATCH-DIV-01",
            "source": "RTC_PUMA",
            "timestamp": "2026-11-03T17:00:00Z",
            "total_records": 10,  # Declara 10, mas só envia 1
            "records": [self._base_valid_record_dict()],
        }
        with self.assertRaises(BatchIntegrityError) as cm:
            self.parser.parse_dict(batch_dict)
        self.assertEqual(cm.exception.declared_total, 10)
        self.assertEqual(cm.exception.actual_total, 1)

    def test_malformed_json_raises_validation_error(self) -> None:
        """Verifica que string JSON malformada dispara RTCValidationError."""
        with self.assertRaises(RTCValidationError):
            self.parser.parse_json("{invalid json payload")


if __name__ == "__main__":
    unittest.main()
