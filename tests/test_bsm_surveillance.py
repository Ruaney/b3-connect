"""Testes automatizados da frente de Auditoria Regulatória e Supervisão BSM (FL-030).

Valida conformidade de trilha de auditoria (CVM 35 / CVM 505), identificação de
beneficiário final e operador PQO, detecção analítica de Wash Trading, Spoofing,
Layering, Churning e cálculo oficial de Order-to-Trade Ratio (OTR).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH independentemente da forma de execução
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.bsm_surveillance import (
    AbusePatternType,
    ActionType,
    AuditRecord,
    BeneficiaryType,
    BsmAuditReport,
    BsmAuditTrailError,
    BsmSurveillanceEngine,
    BsmSurveillanceError,
    BsmValidationError,
    ComplianceStatus,
    SeverityLevel,
    Side,
    SurveillanceAlert,
    format_document,
    normalize_document,
    parse_timestamp,
)


class TestBsmSurveillance(unittest.TestCase):
    """Bateria de testes unitários do motor de vigilância de mercado BSM."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.fixtures_dir = self.repo_root / "fixtures"
        self.fixture_audit_path = self.fixtures_dir / "bsm_audit_trail.json"

    def test_document_normalization_and_formatting(self) -> None:
        """Verifica a higienização e formatação de CPF e CNPJ."""
        # CPF
        cpf_raw = "111.222.333-44"
        self.assertEqual(normalize_document(cpf_raw), "11122233344")
        self.assertEqual(format_document("11122233344"), "111.222.333-44")

        # CNPJ
        cnpj_raw = "12.345.678/0001-90"
        self.assertEqual(normalize_document(cnpj_raw), "12345678000190")
        self.assertEqual(format_document("12345678000190"), "12.345.678/0001-90")

    def test_parse_timestamps_iso_and_fix(self) -> None:
        """Verifica conversão robusta de datas em ISO-8601 (com microssegundos) e formato FIX."""
        # ISO com Z
        dt1 = parse_timestamp("2026-11-03T14:15:30.123456Z")
        self.assertEqual(dt1.tzinfo, timezone.utc)
        self.assertEqual(dt1.microsecond, 123456)

        # Formato FIX standard Tag 60
        dt2 = parse_timestamp("20261103-14:15:30.654321")
        self.assertEqual(dt2.tzinfo, timezone.utc)
        self.assertEqual(dt2.microsecond, 654321)

        # Erro em formato inválido
        with self.assertRaises(BsmValidationError):
            parse_timestamp("data-invalida-b3")

    def test_audit_record_validation(self) -> None:
        """Testa validações obrigatórias da CVM e B3 no AuditRecord."""
        # Registro válido
        rec = AuditRecord(
            record_id="REC-001",
            timestamp=datetime(2026, 11, 3, 14, 0, 0, 1000, tzinfo=timezone.utc),
            participant_id="BROKER105",
            clearing_member_id="CLEARING001",
            account_id="ACC_01",
            beneficiary_id="111.222.333-44",
            beneficiary_type=BeneficiaryType.PF,
            operator_id="OPER-B3-8841",
            symbol="PETR4",
            side=Side.BUY,
            price=Decimal("38.50"),
            quantity=1000,
            order_id="ORD-01",
            cl_ord_id="CL-01",
            action_type=ActionType.NEW,
        )
        self.assertEqual(rec.beneficiary_id, "11122233344")
        self.assertEqual(rec.price, Decimal("38.50"))

        # Falha: Beneficiário ausente (Resolução CVM 35)
        with self.assertRaises(BsmValidationError):
            AuditRecord(
                record_id="REC-ERR-01",
                timestamp=datetime.now(timezone.utc),
                participant_id="BROKER105",
                clearing_member_id="CLEARING001",
                account_id="ACC_01",
                beneficiary_id="",  # Vazio
                beneficiary_type=BeneficiaryType.PF,
                operator_id="OPER-B3-8841",
                symbol="PETR4",
                side=Side.BUY,
                price=Decimal("38.50"),
                quantity=1000,
                order_id="ORD-01",
                cl_ord_id="CL-01",
                action_type=ActionType.NEW,
            )

        # Falha: Operador PQO ausente
        with self.assertRaises(BsmValidationError):
            AuditRecord(
                record_id="REC-ERR-02",
                timestamp=datetime.now(timezone.utc),
                participant_id="BROKER105",
                clearing_member_id="CLEARING001",
                account_id="ACC_01",
                beneficiary_id="11122233344",
                beneficiary_type=BeneficiaryType.PF,
                operator_id="",  # Vazio
                symbol="PETR4",
                side=Side.BUY,
                price=Decimal("38.50"),
                quantity=1000,
                order_id="ORD-01",
                cl_ord_id="CL-01",
                action_type=ActionType.NEW,
            )

        # Falha: Quantidade negativa ou zero
        with self.assertRaises(BsmValidationError):
            AuditRecord(
                record_id="REC-ERR-03",
                timestamp=datetime.now(timezone.utc),
                participant_id="BROKER105",
                clearing_member_id="CLEARING001",
                account_id="ACC_01",
                beneficiary_id="11122233344",
                beneficiary_type=BeneficiaryType.PF,
                operator_id="OPER-B3-8841",
                symbol="PETR4",
                side=Side.BUY,
                price=Decimal("38.50"),
                quantity=0,
                order_id="ORD-01",
                cl_ord_id="CL-01",
                action_type=ActionType.NEW,
            )

    def test_bsm_audit_trail_fixture_loading(self) -> None:
        """Carrega e valida o arquivo de fixture oficial bsm_audit_trail.json."""
        self.assertTrue(
            self.fixture_audit_path.exists(),
            f"Fixture não encontrada em {self.fixture_audit_path}",
        )
        engine = BsmSurveillanceEngine()
        loaded = engine.load_from_json(self.fixture_audit_path)

        self.assertGreaterEqual(len(loaded), 5)
        self.assertEqual(len(engine.records), len(loaded))

        # Confere integridade do primeiro registro
        first = engine.records[0]
        self.assertEqual(first.record_id, "BSM-AUD-001")
        self.assertEqual(first.symbol, "PETR4")
        self.assertEqual(first.side, Side.BUY)
        self.assertEqual(first.operator_id, "OPER-B3-8841")
        self.assertEqual(first.algo_id, "ALGO-VWAP-01")
        self.assertEqual(first.beneficiary_id, "11122233344")

        # Gera relatório padrão
        report = engine.generate_audit_report("REP-FIXTURE-01")
        self.assertIsInstance(report, BsmAuditReport)
        self.assertEqual(report.total_records, len(loaded))
        self.assertGreaterEqual(report.total_orders, 2)
        self.assertGreaterEqual(report.total_trades, 2)
        self.assertGreaterEqual(report.total_cancels, 1)

    def test_wash_trading_detection_same_cpf(self) -> None:
        """Testa disparo de alerta de Wash Trading em ordens cruzadas com mesmo CPF."""
        engine = BsmSurveillanceEngine()
        base_time = datetime(2026, 11, 3, 15, 0, 0, tzinfo=timezone.utc)
        cpf_investidor = "123.456.789-00"

        # 1. Ordem de compra do CPF 12345678900
        rec_buy = AuditRecord(
            record_id="WASH-BUY-001",
            timestamp=base_time,
            participant_id="BROKER105",
            clearing_member_id="CLEARING001",
            account_id="ACC_ALPHA",
            beneficiary_id=cpf_investidor,
            beneficiary_type=BeneficiaryType.PF,
            operator_id="OPER-101",
            symbol="VALE3",
            side=Side.BUY,
            price=Decimal("62.50"),
            quantity=1000,
            order_id="ORD-BUY-101",
            cl_ord_id="CL-BUY-101",
            action_type=ActionType.NEW,
        )

        # 2. Ordem de venda simultânea (3 segundos depois) do mesmo CPF a preço compatível
        rec_sell = AuditRecord(
            record_id="WASH-SELL-001",
            timestamp=base_time + timedelta(seconds=3),
            participant_id="BROKER105",
            clearing_member_id="CLEARING001",
            account_id="ACC_BETA_MESMO_TITULAR",
            beneficiary_id=cpf_investidor,
            beneficiary_type=BeneficiaryType.PF,
            operator_id="OPER-102",
            symbol="VALE3",
            side=Side.SELL,
            price=Decimal("62.50"),
            quantity=1000,
            order_id="ORD-SELL-101",
            cl_ord_id="CL-SELL-101",
            action_type=ActionType.TRADE,  # Executado como trade
        )

        engine.ingest_record(rec_buy)
        engine.ingest_record(rec_sell)

        alerts = engine.detect_wash_trading(time_window_seconds=10.0)
        self.assertEqual(len(alerts), 1)

        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.WASH_TRADING)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.symbol, "VALE3")
        self.assertEqual(alert.beneficiary_id, "12345678900")
        self.assertIn("WASH-BUY-001", alert.evidence_records)
        self.assertIn("WASH-SELL-001", alert.evidence_records)

        # Testa exportação de dossiê para a BSM
        dossier = engine.export_bsm_dossier(alert)
        self.assertEqual(dossier["entity"], "BSM Supervisão de Mercados - B3 S.A.")
        self.assertEqual(dossier["evidence_count"], 2)
        self.assertEqual(dossier["alert"]["pattern_type"], "WASH_TRADING")

    def test_wash_trading_negative_different_beneficiaries(self) -> None:
        """Verifica que negócios entre beneficiários distintos não geram falso positivo."""
        engine = BsmSurveillanceEngine()
        base_time = datetime(2026, 11, 3, 15, 0, 0, tzinfo=timezone.utc)

        # Comprador: CPF A
        rec_buy = AuditRecord(
            record_id="TRADE-BUY-001",
            timestamp=base_time,
            participant_id="BROKER105",
            clearing_member_id="CLEARING001",
            account_id="ACC_CLIENTE_A",
            beneficiary_id="111.111.111-11",
            beneficiary_type=BeneficiaryType.PF,
            operator_id="OPER-101",
            symbol="PETR4",
            side=Side.BUY,
            price=Decimal("38.00"),
            quantity=500,
            order_id="ORD-B-1",
            cl_ord_id="CL-B-1",
            action_type=ActionType.TRADE,
        )

        # Vendedor: CPF B (comitente distinto)
        rec_sell = AuditRecord(
            record_id="TRADE-SELL-001",
            timestamp=base_time + timedelta(seconds=1),
            participant_id="BROKER200",
            clearing_member_id="CLEARING002",
            account_id="ACC_CLIENTE_B",
            beneficiary_id="222.222.222-22",
            beneficiary_type=BeneficiaryType.PF,
            operator_id="OPER-202",
            symbol="PETR4",
            side=Side.SELL,
            price=Decimal("38.00"),
            quantity=500,
            order_id="ORD-S-1",
            cl_ord_id="CL-S-1",
            action_type=ActionType.TRADE,
        )

        engine.ingest_record(rec_buy)
        engine.ingest_record(rec_sell)

        alerts = engine.detect_wash_trading(time_window_seconds=10.0)
        self.assertEqual(len(alerts), 0, "Negociação entre beneficiários distintos não deve gerar alerta.")

    def test_spoofing_and_excessive_otr_alert(self) -> None:
        """Testa detecção de Spoofing e Order-to-Trade Ratio (OTR) abusivo por cancelamentos massivos."""
        engine = BsmSurveillanceEngine()
        base_time = datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc)

        # Simula robô inserindo 20 ordens e cancelando 19 delas, com apenas 1 trade
        # Mensagens = 20 novas + 19 canceladas = 39 mensagens
        # Trades = 1
        # OTR = 39 / 1 = 39 (acima do threshold de 20)
        # Cancel Ratio = 19 / 20 = 95% (acima do threshold de 70%)
        records = []
        for i in range(20):
            ord_id = f"SPOOF-ORD-{i:03d}"
            # Nova ordem
            records.append(
                AuditRecord(
                    record_id=f"AUD-NEW-{i:03d}",
                    timestamp=base_time + timedelta(milliseconds=i * 50),
                    participant_id="BROKER_HFT",
                    clearing_member_id="CLEARING001",
                    account_id="ACC_PROP_TRADING",
                    beneficiary_id="99.888.777/0001-66",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OPER-HFT-01",
                    algo_id="ALGO-SPOOFER-01",
                    symbol="WINZ26",
                    side=Side.BUY,
                    price=Decimal("130500.0"),
                    quantity=50,
                    order_id=ord_id,
                    cl_ord_id=f"CL-{i:03d}",
                    action_type=ActionType.NEW,
                )
            )
            # Cancela 19 delas
            if i < 19:
                records.append(
                    AuditRecord(
                        record_id=f"AUD-CANC-{i:03d}",
                        timestamp=base_time + timedelta(milliseconds=i * 50 + 20),
                        participant_id="BROKER_HFT",
                        clearing_member_id="CLEARING001",
                        account_id="ACC_PROP_TRADING",
                        beneficiary_id="99.888.777/0001-66",
                        beneficiary_type=BeneficiaryType.PJ,
                        operator_id="OPER-HFT-01",
                        algo_id="ALGO-SPOOFER-01",
                        symbol="WINZ26",
                        side=Side.BUY,
                        price=Decimal("130500.0"),
                        quantity=50,
                        order_id=ord_id,
                        cl_ord_id=f"CL-{i:03d}-CANC",
                        action_type=ActionType.CANCEL,
                    )
                )

        # Adiciona 1 trade executado
        records.append(
            AuditRecord(
                record_id="AUD-TRADE-001",
                timestamp=base_time + timedelta(milliseconds=1500),
                participant_id="BROKER_HFT",
                clearing_member_id="CLEARING001",
                account_id="ACC_PROP_TRADING",
                beneficiary_id="99.888.777/0001-66",
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OPER-HFT-01",
                algo_id="ALGO-SPOOFER-01",
                symbol="WINZ26",
                side=Side.SELL,
                price=Decimal("130550.0"),
                quantity=10,
                order_id="SPOOF-ORD-019",
                cl_ord_id="CL-019",
                action_type=ActionType.TRADE,
                execution_id="EXEC-WIN-001",
            )
        )

        engine.ingest_records(records)

        alerts = engine.detect_spoofing_and_excessive_otr(
            otr_threshold=20.0,
            min_messages=5,
            cancel_ratio_threshold=0.70,
        )
        self.assertEqual(len(alerts), 1)

        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.SPOOFING)
        self.assertEqual(alert.symbol, "WINZ26")
        self.assertGreater(alert.metrics["otr"], 20.0)
        self.assertGreater(alert.metrics["cancel_ratio"], 0.90)
        self.assertEqual(alert.metrics["n_trades"], 1)

    def test_layering_detection(self) -> None:
        """Testa detecção de conduta vedada de Layering (múltiplas camadas de preços canceladas)."""
        engine = BsmSurveillanceEngine()
        base_time = datetime(2026, 11, 3, 17, 0, 0, tzinfo=timezone.utc)

        # Inserção de 4 camadas de ordens no book com preços sucessivos
        prices = [Decimal("50.10"), Decimal("50.05"), Decimal("50.00"), Decimal("49.95")]
        records = []
        for idx, px in enumerate(prices):
            records.append(
                AuditRecord(
                    record_id=f"LAYER-NEW-{idx}",
                    timestamp=base_time + timedelta(milliseconds=idx * 100),
                    participant_id="BROKER105",
                    clearing_member_id="CLEARING001",
                    account_id="ACC_SPECULATOR",
                    beneficiary_id="555.666.777-88",
                    beneficiary_type=BeneficiaryType.PF,
                    operator_id="OPER-99",
                    symbol="BBDC4",
                    side=Side.BUY,
                    price=px,
                    quantity=5000,
                    order_id=f"ORD-LAYER-{idx}",
                    cl_ord_id=f"CL-LAYER-{idx}",
                    action_type=ActionType.NEW,
                )
            )

        # Cancelamento em bloco de todas as 4 ordens 2 segundos depois
        for idx, px in enumerate(prices):
            records.append(
                AuditRecord(
                    record_id=f"LAYER-CANC-{idx}",
                    timestamp=base_time + timedelta(seconds=2, milliseconds=idx * 50),
                    participant_id="BROKER105",
                    clearing_member_id="CLEARING001",
                    account_id="ACC_SPECULATOR",
                    beneficiary_id="555.666.777-88",
                    beneficiary_type=BeneficiaryType.PF,
                    operator_id="OPER-99",
                    symbol="BBDC4",
                    side=Side.BUY,
                    price=px,
                    quantity=5000,
                    order_id=f"ORD-LAYER-{idx}",
                    cl_ord_id=f"CL-LAYER-{idx}-C",
                    action_type=ActionType.CANCEL,
                )
            )

        engine.ingest_records(records)
        alerts = engine.detect_layering(min_layers=3, time_window_seconds=10.0)
        self.assertEqual(len(alerts), 1)

        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.LAYERING)
        self.assertEqual(alert.symbol, "BBDC4")
        self.assertEqual(alert.metrics["layer_count"], 4)

    def test_churning_detection(self) -> None:
        """Testa detecção de Churning (giro excessivo de carteira sem alteração patrimonial líquida)."""
        engine = BsmSurveillanceEngine()
        base_time = datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc)
        account = "ACC_DISCRETIONARY_01"
        cpf = "777.888.999-11"

        # Simula 8 trades rápidos de compra e venda com 100 ações cada
        # Total Comprado: 400 | Total Vendido: 400 | Líquido: 0 | Giro: 800
        records = []
        for i in range(8):
            side = Side.BUY if i % 2 == 0 else Side.SELL
            records.append(
                AuditRecord(
                    record_id=f"CHURN-TRD-{i:02d}",
                    timestamp=base_time + timedelta(minutes=i * 15),
                    participant_id="BROKER_MESA",
                    clearing_member_id="CLEARING001",
                    account_id=account,
                    beneficiary_id=cpf,
                    beneficiary_type=BeneficiaryType.PF,
                    operator_id="OPER-PORTFOLIO-MGR",
                    symbol="ITUB4",
                    side=side,
                    price=Decimal("35.00"),
                    quantity=100,
                    order_id=f"ORD-CHURN-{i}",
                    cl_ord_id=f"CL-CHURN-{i}",
                    action_type=ActionType.TRADE,
                )
            )

        engine.ingest_records(records)
        alerts = engine.detect_churning(min_trades=6)
        self.assertEqual(len(alerts), 1)

        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.CHURNING)
        self.assertEqual(alert.symbol, "ITUB4")
        self.assertEqual(alert.metrics["total_trades"], 8)
        self.assertEqual(alert.metrics["net_qty"], 0)
        self.assertEqual(alert.metrics["gross_qty"], 800)

    def test_clean_trading_produces_no_alerts(self) -> None:
        """Verifica que atividade de negociação legítima e equilibrada produz zero alertas."""
        engine = BsmSurveillanceEngine()
        base_time = datetime(2026, 11, 3, 11, 0, 0, tzinfo=timezone.utc)

        # 5 ordens normais e 5 execuções de comitentes distintos
        for i in range(5):
            engine.ingest_record(
                AuditRecord(
                    record_id=f"CLEAN-ORD-{i}",
                    timestamp=base_time + timedelta(seconds=i * 10),
                    participant_id="BROKER105",
                    clearing_member_id="CLEARING001",
                    account_id=f"ACC_{i}",
                    beneficiary_id=f"1234567890{i}",
                    beneficiary_type=BeneficiaryType.PF,
                    operator_id=f"OPER-{i}",
                    symbol="PETR4",
                    side=Side.BUY,
                    price=Decimal("38.50"),
                    quantity=100,
                    order_id=f"ORD-CL-{i}",
                    cl_ord_id=f"CL-{i}",
                    action_type=ActionType.NEW,
                )
            )
            engine.ingest_record(
                AuditRecord(
                    record_id=f"CLEAN-TRD-{i}",
                    timestamp=base_time + timedelta(seconds=i * 10 + 2),
                    participant_id="BROKER105",
                    clearing_member_id="CLEARING001",
                    account_id=f"ACC_{i}",
                    beneficiary_id=f"1234567890{i}",
                    beneficiary_type=BeneficiaryType.PF,
                    operator_id=f"OPER-{i}",
                    symbol="PETR4",
                    side=Side.BUY,
                    price=Decimal("38.50"),
                    quantity=100,
                    order_id=f"ORD-CL-{i}",
                    cl_ord_id=f"CL-{i}",
                    action_type=ActionType.TRADE,
                )
            )

        alerts = engine.run_all_detectors()
        self.assertEqual(len(alerts), 0)

        report = engine.generate_audit_report("REP-CLEAN")
        self.assertEqual(report.alerts_count, 0)
        self.assertLess(report.global_otr, 2.0)


if __name__ == "__main__":
    unittest.main()
