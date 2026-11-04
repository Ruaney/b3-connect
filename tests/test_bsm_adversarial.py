"""Suíte Especializada de Testes Adversários e Red Team de Supervisão BSM (CVM 505 / CVM 35).

Cenários de teste concebidos pela ótica do Red Team de Vigilância de Mercado para validar
a robustez e a precisão do motor de auditoria e vigilância BSM (src/bsm_surveillance.py):

1. Wash Trading coordenado entre matriz e filiais (mesma raiz de CNPJ).
2. Wash Trading triangulado/coordenado através de intermediários (pass-through / carrossel).
3. Spoofing com cancelamento relâmpago (< 50ms) e execução no lado oposto (Spoof-and-Execute).
4. Spoofing evasivo por desvio de OTR e cancelamento em latência ultrabaixa.
5. Layering em múltiplos níveis de book (estratificação profunda de 4 a 5 camadas).
6. Layering em múltiplos episódios ao longo do pregão (sliding windows).
7. Validação estrita de 0% de Falsos Positivos em fluxo legítimo de Formadores de Mercado (PMM)
   e negociações entre partes independentes.
8. Teste de estresse integrado e geração de dossiê regulatório da BSM.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.bsm_surveillance import (
    AbusePatternType,
    ActionType,
    AuditRecord,
    BeneficiaryType,
    BsmAuditReport,
    BsmSurveillanceEngine,
    BsmValidationError,
    ComplianceStatus,
    SeverityLevel,
    Side,
    SurveillanceAlert,
    format_document,
    get_document_root,
    is_same_economic_entity,
    normalize_document,
    parse_timestamp,
)


class TestAdversarialWashTrading(unittest.TestCase):
    """Bateria de testes adversários para Wash Trading (CVM 35 / CVM 505 / CVM 8)."""

    def setUp(self) -> None:
        self.engine = BsmSurveillanceEngine()
        self.base_time = datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc)

    def test_wash_trading_same_cnpj_root_matriz_filial(self) -> None:
        """Cenário 1.1: Wash Trading direto entre Matriz e Filial (mesma raiz de CNPJ).

        Adversário utiliza contas distintas em corretoras diferentes:
        - Matriz: 12.345.678/0001-90 (BROKER105, Operador OPER-01)
        - Filial: 12.345.678/0002-71 (BROKER200, Operador OPER-02)
        Ambas possuem a mesma raiz cadastral '12345678' (capital único e mesmo comitente final).
        """
        cnpj_matriz = "12.345.678/0001-90"
        cnpj_filial = "12.345.678/0002-71"

        # 1. Matriz emite ordem de compra de 10.000 PETR4 @ 38.50
        rec_buy = AuditRecord(
            record_id="ADV-WASH-ROOT-BUY",
            timestamp=self.base_time,
            participant_id="BROKER105",
            clearing_member_id="CLEARING001",
            account_id="ACC_MATRIZ_SP",
            beneficiary_id=cnpj_matriz,
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-ALPHA-01",
            symbol="PETR4",
            side=Side.BUY,
            price=Decimal("38.50"),
            quantity=10000,
            order_id="ORD-MATRIZ-101",
            cl_ord_id="CL-MAT-101",
            action_type=ActionType.NEW,
        )

        # 2. Filial emite ordem de venda simultânea (1.2s depois) @ 38.50 como trade
        rec_sell = AuditRecord(
            record_id="ADV-WASH-ROOT-SELL",
            timestamp=self.base_time + timedelta(milliseconds=1200),
            participant_id="BROKER200",
            clearing_member_id="CLEARING002",
            account_id="ACC_FILIAL_RJ",
            beneficiary_id=cnpj_filial,
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-BETA-02",
            symbol="PETR4",
            side=Side.SELL,
            price=Decimal("38.50"),
            quantity=10000,
            order_id="ORD-FILIAL-201",
            cl_ord_id="CL-FIL-201",
            action_type=ActionType.TRADE,
        )

        self.engine.ingest_record(rec_buy)
        self.engine.ingest_record(rec_sell)

        alerts = self.engine.detect_wash_trading(time_window_seconds=10.0)
        self.assertEqual(len(alerts), 1, "Motor deve identificar Wash Trading entre matriz e filial")

        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.WASH_TRADING)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.symbol, "PETR4")
        self.assertEqual(alert.metrics["match_reason"], "SAME_CNPJ_ROOT")
        self.assertEqual(alert.metrics["cnpj_root"], "12345678")
        self.assertIn("ADV-WASH-ROOT-BUY", alert.evidence_records)
        self.assertIn("ADV-WASH-ROOT-SELL", alert.evidence_records)
        self.assertIn("mesma raiz de CNPJ 12345678", alert.description)

    def test_coordinated_wash_trading_via_intermediary_triangulation(self) -> None:
        """Cenário 1.2: Wash Trading coordenado via intermediário (conduit / pass-through).

        Adversário busca ocultar a auto-negociação intercalando um intermediário financeiro:
        - Perna 1: Entidade A (Matriz 23.456.789/0001-10) vende 15.000 VALE3 @ 65.00 para Intermediário M.
        - Perna 2: Intermediário M (99.999.999/0001-99) revende 15.000 VALE3 @ 65.00 para Entidade B (Filial 23.456.789/0002-00) 400ms depois.
        Resultado líquido: Posição do intermediário é zero; 15.000 ações transferidas entre a mesma raiz com volume artificial.
        """
        cnpj_a = "23.456.789/0001-10"
        cnpj_b = "23.456.789/0002-00"
        cnpj_intermediary = "99.999.999/0001-99"

        t0 = self.base_time

        # Perna 1: A vende para M
        rec_leg1_seller_a = AuditRecord(
            record_id="COORD-LEG1-SELL-A",
            timestamp=t0,
            participant_id="BROKER105",
            clearing_member_id="CLEARING001",
            account_id="ACC_A",
            beneficiary_id=cnpj_a,
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-A",
            symbol="VALE3",
            side=Side.SELL,
            price=Decimal("65.00"),
            quantity=15000,
            order_id="ORD-A-01",
            cl_ord_id="CL-A-01",
            action_type=ActionType.TRADE,
            execution_id="EXEC-TRG-001",
            metadata={"counterparty_beneficiary_id": cnpj_intermediary},
        )
        rec_leg1_buyer_m = AuditRecord(
            record_id="COORD-LEG1-BUY-M",
            timestamp=t0,
            participant_id="BROKER999",
            clearing_member_id="CLEARING001",
            account_id="ACC_M_CONDUIT",
            beneficiary_id=cnpj_intermediary,
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-M",
            symbol="VALE3",
            side=Side.BUY,
            price=Decimal("65.00"),
            quantity=15000,
            order_id="ORD-M-01",
            cl_ord_id="CL-M-01",
            action_type=ActionType.TRADE,
            execution_id="EXEC-TRG-001",
            metadata={"counterparty_beneficiary_id": cnpj_a},
        )

        # Perna 2: M revende para B (400ms depois)
        rec_leg2_seller_m = AuditRecord(
            record_id="COORD-LEG2-SELL-M",
            timestamp=t0 + timedelta(milliseconds=400),
            participant_id="BROKER999",
            clearing_member_id="CLEARING001",
            account_id="ACC_M_CONDUIT",
            beneficiary_id=cnpj_intermediary,
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-M",
            symbol="VALE3",
            side=Side.SELL,
            price=Decimal("65.00"),
            quantity=15000,
            order_id="ORD-M-02",
            cl_ord_id="CL-M-02",
            action_type=ActionType.TRADE,
            execution_id="EXEC-TRG-002",
            metadata={"counterparty_beneficiary_id": cnpj_b},
        )
        rec_leg2_buyer_b = AuditRecord(
            record_id="COORD-LEG2-BUY-B",
            timestamp=t0 + timedelta(milliseconds=400),
            participant_id="BROKER200",
            clearing_member_id="CLEARING002",
            account_id="ACC_B",
            beneficiary_id=cnpj_b,
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-B",
            symbol="VALE3",
            side=Side.BUY,
            price=Decimal("65.00"),
            quantity=15000,
            order_id="ORD-B-02",
            cl_ord_id="CL-B-02",
            action_type=ActionType.TRADE,
            execution_id="EXEC-TRG-002",
            metadata={"counterparty_beneficiary_id": cnpj_intermediary},
        )

        self.engine.ingest_records([
            rec_leg1_seller_a,
            rec_leg1_buyer_m,
            rec_leg2_seller_m,
            rec_leg2_buyer_b,
        ])

        alerts = self.engine.detect_wash_trading(time_window_seconds=10.0, detect_coordinated=True)
        # Deve identificar o esquema coordenado
        coord_alerts = [a for a in alerts if a.metrics.get("is_coordinated") is True]
        self.assertGreaterEqual(len(coord_alerts), 1, "Deve detectar Wash Trading coordenado via intermediário")

        al = coord_alerts[0]
        self.assertEqual(al.pattern_type, AbusePatternType.WASH_TRADING)
        self.assertEqual(al.severity, SeverityLevel.CRITICAL)
        self.assertEqual(al.metrics["cnpj_root"], "23456789")
        self.assertEqual(al.metrics["intermediary_id"], normalize_document(cnpj_intermediary))
        self.assertIn("COORD-LEG1-SELL-A", al.evidence_records)
        self.assertIn("COORD-LEG2-BUY-B", al.evidence_records)
        self.assertIn("intermediário", al.description)

    def test_wash_trading_carousel_multi_filial_circulation(self) -> None:
        """Cenário 1.3: Carrossel circular entre 3 filiais da mesma raiz.

        Filial 1 -> Filial 2 -> Filial 3 em sequência rápida a preço constante.
        """
        root = "45678901"
        cnpj_f1 = f"{root}000101"
        cnpj_f2 = f"{root}000202"
        cnpj_f3 = f"{root}000303"

        records = [
            # F1 vende 5000, F2 compra 5000
            AuditRecord(
                record_id="CAR-01-F1-SELL",
                timestamp=self.base_time,
                participant_id="BRK1",
                clearing_member_id="CLR1",
                account_id="ACC_F1",
                beneficiary_id=cnpj_f1,
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OP1",
                symbol="B3SA3",
                side=Side.SELL,
                price=Decimal("12.80"),
                quantity=5000,
                order_id="ORD-C-1",
                cl_ord_id="CL-C-1",
                action_type=ActionType.TRADE,
            ),
            AuditRecord(
                record_id="CAR-01-F2-BUY",
                timestamp=self.base_time,
                participant_id="BRK1",
                clearing_member_id="CLR1",
                account_id="ACC_F2",
                beneficiary_id=cnpj_f2,
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OP2",
                symbol="B3SA3",
                side=Side.BUY,
                price=Decimal("12.80"),
                quantity=5000,
                order_id="ORD-C-2",
                cl_ord_id="CL-C-2",
                action_type=ActionType.TRADE,
            ),
            # F2 vende 5000, F3 compra 5000 (2 segundos depois)
            AuditRecord(
                record_id="CAR-02-F2-SELL",
                timestamp=self.base_time + timedelta(seconds=2),
                participant_id="BRK1",
                clearing_member_id="CLR1",
                account_id="ACC_F2",
                beneficiary_id=cnpj_f2,
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OP2",
                symbol="B3SA3",
                side=Side.SELL,
                price=Decimal("12.80"),
                quantity=5000,
                order_id="ORD-C-3",
                cl_ord_id="CL-C-3",
                action_type=ActionType.TRADE,
            ),
            AuditRecord(
                record_id="CAR-02-F3-BUY",
                timestamp=self.base_time + timedelta(seconds=2),
                participant_id="BRK2",
                clearing_member_id="CLR2",
                account_id="ACC_F3",
                beneficiary_id=cnpj_f3,
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OP3",
                symbol="B3SA3",
                side=Side.BUY,
                price=Decimal("12.80"),
                quantity=5000,
                order_id="ORD-C-4",
                cl_ord_id="CL-C-4",
                action_type=ActionType.TRADE,
            ),
        ]

        self.engine.ingest_records(records)
        alerts = self.engine.detect_wash_trading(time_window_seconds=10.0)
        self.assertGreaterEqual(len(alerts), 2, "Deve detectar ambos os giros do carrossel entre filiais")
        for al in alerts:
            self.assertEqual(al.metrics["cnpj_root"], root)
            self.assertEqual(al.severity, SeverityLevel.CRITICAL)


class TestAdversarialSpoofingFlashCancel(unittest.TestCase):
    """Bateria de testes adversários para Spoofing com cancelamento relâmpago (< 50ms)."""

    def setUp(self) -> None:
        self.engine = BsmSurveillanceEngine()
        self.base_time = datetime(2026, 11, 4, 11, 0, 0, tzinfo=timezone.utc)

    def test_spoofing_flash_cancel_under_50ms_with_opposite_execution(self) -> None:
        """Cenário 2.1: Spoofing HFT com cancelamento em 18ms e execução no lado oposto.

        Mecanismo do ataque:
        1. Robô HFT insere ordem massiva de COMPRA (100.000 ações BOVA11 @ 120.00) em T0.
        2. A falsa liquidez pressiona o book para cima. Em T0 + 12ms, o robô executa
           uma ordem de VENDA real no ask (10.000 ações @ 120.05).
        3. Em T0 + 18ms (apenas 6ms após o preenchimento da venda), o robô cancela
           a ordem de COMPRA original (latência total da ordem = 18ms < 50ms).
        """
        t0 = self.base_time
        ord_id = "SPOOF-FLASH-ORD-100"

        # 1. Ordem de compra grande inserida (Bid)
        rec_new = AuditRecord(
            record_id="REC-SPOOF-NEW",
            timestamp=t0,
            participant_id="BROKER_HFT",
            clearing_member_id="CLEARING001",
            account_id="ACC_PROP_DESK",
            beneficiary_id="88.777.666/0001-55",
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-ROBOT-01",
            algo_id="ALGO-SPOOFER-X",
            symbol="BOVA11",
            side=Side.BUY,
            price=Decimal("120.00"),
            quantity=100000,
            order_id=ord_id,
            cl_ord_id="CL-SPOOF-100",
            action_type=ActionType.NEW,
        )

        # 2. Execução real de venda no Ask em T0 + 12ms
        rec_trade = AuditRecord(
            record_id="REC-SPOOF-TRADE-OPP",
            timestamp=t0 + timedelta(milliseconds=12),
            participant_id="BROKER_HFT",
            clearing_member_id="CLEARING001",
            account_id="ACC_PROP_DESK",
            beneficiary_id="88.777.666/0001-55",
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-ROBOT-01",
            algo_id="ALGO-SPOOFER-X",
            symbol="BOVA11",
            side=Side.SELL,
            price=Decimal("120.05"),
            quantity=10000,
            order_id="REAL-FILL-ORD-200",
            cl_ord_id="CL-FILL-200",
            action_type=ActionType.TRADE,
            execution_id="EXEC-BOVA-771",
        )

        # 3. Cancelamento relâmpago da ordem de compra em T0 + 18ms (< 50ms)
        rec_cancel = AuditRecord(
            record_id="REC-SPOOF-CANC",
            timestamp=t0 + timedelta(milliseconds=18),
            participant_id="BROKER_HFT",
            clearing_member_id="CLEARING001",
            account_id="ACC_PROP_DESK",
            beneficiary_id="88.777.666/0001-55",
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-ROBOT-01",
            algo_id="ALGO-SPOOFER-X",
            symbol="BOVA11",
            side=Side.BUY,
            price=Decimal("120.00"),
            quantity=100000,
            order_id=ord_id,
            cl_ord_id="CL-SPOOF-100",
            action_type=ActionType.CANCEL,
        )

        self.engine.ingest_records([rec_new, rec_trade, rec_cancel])

        flash_alerts = self.engine.detect_flash_cancellations(flash_threshold_ms=50.0)
        self.assertEqual(len(flash_alerts), 1, "Deve detectar spoofing com cancelamento relâmpago de 18ms")

        alert = flash_alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.SPOOFING)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.symbol, "BOVA11")
        self.assertEqual(alert.metrics["flash_cancel_duration_ms"], 18.0)
        self.assertTrue(alert.metrics["has_opposite_execution"])
        self.assertEqual(alert.metrics["opposite_trade_record_id"], "REC-SPOOF-TRADE-OPP")
        self.assertEqual(alert.metrics["opposite_trade_side"], "SELL")
        self.assertIn("REC-SPOOF-NEW", alert.evidence_records)
        self.assertIn("REC-SPOOF-CANC", alert.evidence_records)
        self.assertIn("REC-SPOOF-TRADE-OPP", alert.evidence_records)

    def test_spoofing_ultra_fast_flash_cancel_probing_without_trade(self) -> None:
        """Cenário 2.2: Spoofing por sondagem relâmpago (Quote Probing / Phantom Liquidity) em 25ms.

        Ordem inserida e cancelada em 25ms repetidamente para assustar market makers.
        """
        t0 = self.base_time
        records = [
            AuditRecord(
                record_id="PROBE-NEW-01",
                timestamp=t0,
                participant_id="BRK_HFT",
                clearing_member_id="CLR01",
                account_id="ACC_PROP",
                beneficiary_id="77.666.555/0001-44",
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OP_HFT",
                symbol="VALE3",
                side=Side.SELL,
                price=Decimal("63.00"),
                quantity=40000,
                order_id="ORD-PROBE-01",
                cl_ord_id="CL-PROBE-01",
                action_type=ActionType.NEW,
            ),
            AuditRecord(
                record_id="PROBE-CANC-01",
                timestamp=t0 + timedelta(milliseconds=25),
                participant_id="BRK_HFT",
                clearing_member_id="CLR01",
                account_id="ACC_PROP",
                beneficiary_id="77.666.555/0001-44",
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OP_HFT",
                symbol="VALE3",
                side=Side.SELL,
                price=Decimal("63.00"),
                quantity=40000,
                order_id="ORD-PROBE-01",
                cl_ord_id="CL-PROBE-01",
                action_type=ActionType.CANCEL,
            ),
        ]

        self.engine.ingest_records(records)
        alerts = self.engine.detect_flash_cancellations(flash_threshold_ms=50.0, min_flash_cancels=1)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].pattern_type, AbusePatternType.SPOOFING)
        self.assertEqual(alerts[0].severity, SeverityLevel.HIGH)
        self.assertEqual(alerts[0].metrics["flash_cancel_duration_ms"], 25.0)

    def test_spoofing_evasion_low_otr_caught_by_flash_detection(self) -> None:
        """Cenário 2.3: Tentativa de evasão de OTR através de trades artificiais.

        Adversário emite 8 ordens, cancela 6 em 30ms (<50ms) e executa 2 trades legítimos.
        OTR tradicional seria (8 + 6) / 2 = 7.0 (bem abaixo do threshold de 20.0).
        O detector de cancelamento relâmpago captura a infração de latência com 100% de precisão.
        """
        t0 = self.base_time
        records = []
        for i in range(6):
            oid = f"EVADE-ORD-{i}"
            records.append(
                AuditRecord(
                    record_id=f"EVADE-NEW-{i}",
                    timestamp=t0 + timedelta(milliseconds=i * 200),
                    participant_id="BRK_EVADER",
                    clearing_member_id="CLR01",
                    account_id="ACC_EVADER",
                    beneficiary_id="66.555.444/0001-33",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OP_EVADER",
                    symbol="PETR4",
                    side=Side.BUY,
                    price=Decimal("38.40"),
                    quantity=10000,
                    order_id=oid,
                    cl_ord_id=f"CL-{i}",
                    action_type=ActionType.NEW,
                )
            )
            records.append(
                AuditRecord(
                    record_id=f"EVADE-CANC-{i}",
                    timestamp=t0 + timedelta(milliseconds=i * 200 + 30),  # 30ms de vida
                    participant_id="BRK_EVADER",
                    clearing_member_id="CLR01",
                    account_id="ACC_EVADER",
                    beneficiary_id="66.555.444/0001-33",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OP_EVADER",
                    symbol="PETR4",
                    side=Side.BUY,
                    price=Decimal("38.40"),
                    quantity=10000,
                    order_id=oid,
                    cl_ord_id=f"CL-{i}",
                    action_type=ActionType.CANCEL,
                )
            )

        # 2 trades para mascarar OTR
        for j in range(2):
            records.append(
                AuditRecord(
                    record_id=f"EVADE-TRD-{j}",
                    timestamp=t0 + timedelta(seconds=2 + j),
                    participant_id="BRK_EVADER",
                    clearing_member_id="CLR01",
                    account_id="ACC_EVADER",
                    beneficiary_id="66.555.444/0001-33",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OP_EVADER",
                    symbol="PETR4",
                    side=Side.BUY,
                    price=Decimal("38.40"),
                    quantity=100,
                    order_id=f"DUMMY-TRD-{j}",
                    cl_ord_id=f"CL-DUMMY-{j}",
                    action_type=ActionType.TRADE,
                )
            )

        self.engine.ingest_records(records)

        # OTR tradicional com threshold 20 não dispararia
        otr_alerts = self.engine.detect_spoofing_and_excessive_otr(otr_threshold=20.0)
        self.assertEqual(len(otr_alerts), 0, "OTR tradicional foi contornado pela injeção de trades")

        # Detector especializado de cancelamentos relâmpago captura 100% das 6 ordens canceladas em 30ms
        flash_alerts = self.engine.detect_flash_cancellations(flash_threshold_ms=50.0)
        self.assertEqual(len(flash_alerts), 6, "Detector relâmpago deve identificar todas as 6 ordens predatórias")


class TestAdversarialLayeringMultiLevelBook(unittest.TestCase):
    """Bateria de testes adversários para Layering em múltiplos níveis de book."""

    def setUp(self) -> None:
        self.engine = BsmSurveillanceEngine()
        self.base_time = datetime(2026, 11, 4, 14, 0, 0, tzinfo=timezone.utc)

    def test_layering_5_levels_deep_book_with_opposite_execution(self) -> None:
        """Cenário 3.1: Layering profundo em 5 níveis de preço no Bid com execução no Ask.

        Estratégia:
        1. Inserção de 5 camadas de ordens de compra: R$ 50.10, 50.08, 50.06, 50.04, 50.02.
        2. Simulação de profundidade massiva de compra atrai vendedores passivos a subir cotações.
        3. Manipulador executa venda a mercado (Ask @ 50.12) de 25.000 ações.
        4. Imediatamente após a execução, as 5 camadas são canceladas em bloco.
        """
        t0 = self.base_time
        prices = [Decimal("50.10"), Decimal("50.08"), Decimal("50.06"), Decimal("50.04"), Decimal("50.02")]
        records = []

        # 5 camadas de New Order
        for idx, px in enumerate(prices):
            records.append(
                AuditRecord(
                    record_id=f"L5-NEW-{idx}",
                    timestamp=t0 + timedelta(milliseconds=idx * 60),
                    participant_id="BRK_MANIPULATOR",
                    clearing_member_id="CLR01",
                    account_id="ACC_DESK_ALPHA",
                    beneficiary_id="55.444.333/0001-22",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OP_LAYER_01",
                    symbol="BBAS3",
                    side=Side.BUY,
                    price=px,
                    quantity=8000,
                    order_id=f"ORD-L5-{idx}",
                    cl_ord_id=f"CL-L5-{idx}",
                    action_type=ActionType.NEW,
                )
            )

        # Execução oportuna de venda no Ask em T0 + 800ms
        records.append(
            AuditRecord(
                record_id="L5-TRADE-ASK",
                timestamp=t0 + timedelta(milliseconds=800),
                participant_id="BRK_MANIPULATOR",
                clearing_member_id="CLR01",
                account_id="ACC_DESK_ALPHA",
                beneficiary_id="55.444.333/0001-22",
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OP_LAYER_01",
                symbol="BBAS3",
                side=Side.SELL,
                price=Decimal("50.12"),
                quantity=25000,
                order_id="REAL-SELL-5012",
                cl_ord_id="CL-SELL-5012",
                action_type=ActionType.TRADE,
                execution_id="EXEC-BBAS-999",
            )
        )

        # Cancelamento em bloco das 5 ordens em T0 + 1200ms
        for idx, px in enumerate(prices):
            records.append(
                AuditRecord(
                    record_id=f"L5-CANC-{idx}",
                    timestamp=t0 + timedelta(milliseconds=1200 + idx * 20),
                    participant_id="BRK_MANIPULATOR",
                    clearing_member_id="CLR01",
                    account_id="ACC_DESK_ALPHA",
                    beneficiary_id="55.444.333/0001-22",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OP_LAYER_01",
                    symbol="BBAS3",
                    side=Side.BUY,
                    price=px,
                    quantity=8000,
                    order_id=f"ORD-L5-{idx}",
                    cl_ord_id=f"CL-L5-{idx}",
                    action_type=ActionType.CANCEL,
                )
            )

        self.engine.ingest_records(records)

        alerts = self.engine.detect_layering(min_layers=3, time_window_seconds=15.0)
        self.assertEqual(len(alerts), 1, "Deve identificar o episódio de Layering de 5 níveis")

        al = alerts[0]
        self.assertEqual(al.pattern_type, AbusePatternType.LAYERING)
        self.assertEqual(al.severity, SeverityLevel.CRITICAL)
        self.assertEqual(al.metrics["layer_count"], 5)
        self.assertTrue(al.metrics["has_opposite_execution"])
        self.assertEqual(al.metrics["opposite_trade_record_id"], "L5-TRADE-ASK")
        self.assertIn("L5-TRADE-ASK", al.evidence_records)

    def test_layering_multiple_episodes_throughout_day_sliding_window(self) -> None:
        """Cenário 3.2: Múltiplos episódios de Layering espaçados ao longo do dia.

        Testa a robustez de sliding window:
        - Episódio 1: 10:00:00 (3 camadas de compra canceladas em 2s).
        - Episódio 2: 15:30:00 (4 camadas de compra canceladas em 2s).
        Ambos os episódios devem ser detectados sem que o tempo entre eles anule o alerta.
        """
        records = []
        # Episódio 1 (Manhã: 10h)
        t_m = self.base_time
        for i, p in enumerate([Decimal("30.00"), Decimal("29.95"), Decimal("29.90")]):
            records.append(
                AuditRecord(
                    record_id=f"EP1-NEW-{i}",
                    timestamp=t_m + timedelta(milliseconds=i * 50),
                    participant_id="BRK105",
                    clearing_member_id="CLR01",
                    account_id="ACC_SPEC",
                    beneficiary_id="44.333.222/0001-11",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OP1",
                    symbol="ITUB4",
                    side=Side.BUY,
                    price=p,
                    quantity=2000,
                    order_id=f"ORD-EP1-{i}",
                    cl_ord_id=f"CL-EP1-{i}",
                    action_type=ActionType.NEW,
                )
            )
            records.append(
                AuditRecord(
                    record_id=f"EP1-CANC-{i}",
                    timestamp=t_m + timedelta(seconds=2, milliseconds=i * 50),
                    participant_id="BRK105",
                    clearing_member_id="CLR01",
                    account_id="ACC_SPEC",
                    beneficiary_id="44.333.222/0001-11",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OP1",
                    symbol="ITUB4",
                    side=Side.BUY,
                    price=p,
                    quantity=2000,
                    order_id=f"ORD-EP1-{i}",
                    cl_ord_id=f"CL-EP1-{i}",
                    action_type=ActionType.CANCEL,
                )
            )

        # Episódio 2 (Tarde: 15h30 - 5 horas e meia depois)
        t_t = self.base_time + timedelta(hours=5, minutes=30)
        for i, p in enumerate([Decimal("30.50"), Decimal("30.45"), Decimal("30.40"), Decimal("30.35")]):
            records.append(
                AuditRecord(
                    record_id=f"EP2-NEW-{i}",
                    timestamp=t_t + timedelta(milliseconds=i * 50),
                    participant_id="BRK105",
                    clearing_member_id="CLR01",
                    account_id="ACC_SPEC",
                    beneficiary_id="44.333.222/0001-11",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OP1",
                    symbol="ITUB4",
                    side=Side.BUY,
                    price=p,
                    quantity=3000,
                    order_id=f"ORD-EP2-{i}",
                    cl_ord_id=f"CL-EP2-{i}",
                    action_type=ActionType.NEW,
                )
            )
            records.append(
                AuditRecord(
                    record_id=f"EP2-CANC-{i}",
                    timestamp=t_t + timedelta(seconds=2, milliseconds=i * 50),
                    participant_id="BRK105",
                    clearing_member_id="CLR01",
                    account_id="ACC_SPEC",
                    beneficiary_id="44.333.222/0001-11",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OP1",
                    symbol="ITUB4",
                    side=Side.BUY,
                    price=p,
                    quantity=3000,
                    order_id=f"ORD-EP2-{i}",
                    cl_ord_id=f"CL-EP2-{i}",
                    action_type=ActionType.CANCEL,
                )
            )

        self.engine.ingest_records(records)
        alerts = self.engine.detect_layering(min_layers=3, time_window_seconds=15.0)
        self.assertEqual(len(alerts), 2, "Motor deve identificar ambos os episódios de Layering do pregão")


class TestZeroFalsePositivesLegitimateFlow(unittest.TestCase):
    """Garantia de 0% de Falsos Positivos em condições normais de mercado."""

    def setUp(self) -> None:
        self.engine = BsmSurveillanceEngine()
        self.base_time = datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)

    def test_clean_market_maker_pmm_quoting_and_spread_adjustment(self) -> None:
        """Cenário 4.1: Formador de Mercado (PMM) oficial com cotações contínuas e ajustes de spread.

        O PMM envia simultaneamente ordens de compra e venda dentro dos parâmetros do programa,
        atualizando suas cotações rapidamente conforme o livro se move.
        Não deve gerar falso positivo de spoofing nem de layering.
        """
        t0 = self.base_time
        records = []

        # PMM cota Bid e Ask
        for step in range(5):
            t_step = t0 + timedelta(seconds=step * 2)
            # Bid
            bid_oid = f"MM-BID-{step}"
            records.append(
                AuditRecord(
                    record_id=f"REC-MM-BID-N-{step}",
                    timestamp=t_step,
                    participant_id="BROKER_PMM",
                    clearing_member_id="CLR001",
                    account_id="ACC_MM_REGULATED",
                    beneficiary_id="11.222.333/0001-99",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OPER-PMM-CERT",
                    algo_id="ALGO-PMM-EQUITY-01",
                    symbol="B3SA3",
                    side=Side.BUY,
                    price=Decimal("12.50") + Decimal(step * 0.01),
                    quantity=1000,
                    order_id=bid_oid,
                    cl_ord_id=f"CL-MM-B-{step}",
                    action_type=ActionType.NEW,
                    metadata={"pqr_certified": True, "cancel_reason": "SPREAD_ADJUSTMENT"},
                )
            )
            # Ask
            ask_oid = f"MM-ASK-{step}"
            records.append(
                AuditRecord(
                    record_id=f"REC-MM-ASK-N-{step}",
                    timestamp=t_step,
                    participant_id="BROKER_PMM",
                    clearing_member_id="CLR001",
                    account_id="ACC_MM_REGULATED",
                    beneficiary_id="11.222.333/0001-99",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OPER-PMM-CERT",
                    algo_id="ALGO-PMM-EQUITY-01",
                    symbol="B3SA3",
                    side=Side.SELL,
                    price=Decimal("12.55") + Decimal(step * 0.01),
                    quantity=1000,
                    order_id=ask_oid,
                    cl_ord_id=f"CL-MM-A-{step}",
                    action_type=ActionType.NEW,
                    metadata={"pqr_certified": True, "cancel_reason": "SPREAD_ADJUSTMENT"},
                )
            )
            # Atualização de cotação / cancelamento legítimo do bid anterior
            if step > 0:
                records.append(
                    AuditRecord(
                        record_id=f"REC-MM-BID-C-{step}",
                        timestamp=t_step + timedelta(milliseconds=45),  # 45ms mas PMM certificado
                        participant_id="BROKER_PMM",
                        clearing_member_id="CLR001",
                        account_id="ACC_MM_REGULATED",
                        beneficiary_id="11.222.333/0001-99",
                        beneficiary_type=BeneficiaryType.PJ,
                        operator_id="OPER-PMM-CERT",
                        algo_id="ALGO-PMM-EQUITY-01",
                        symbol="B3SA3",
                        side=Side.BUY,
                        price=Decimal("12.50") + Decimal((step - 1) * 0.01),
                        quantity=1000,
                        order_id=f"MM-BID-{step - 1}",
                        cl_ord_id=f"CL-MM-B-{step - 1}",
                        action_type=ActionType.CANCEL,
                        metadata={"pqr_certified": True, "cancel_reason": "SPREAD_ADJUSTMENT"},
                    )
                )

        self.engine.ingest_records(records)

        # Executa todos os detectores
        alerts = self.engine.run_all_detectors()
        self.assertEqual(len(alerts), 0, f"Atividade legítima de Formador de Mercado gerou alertas indevidos: {alerts}")

    def test_independent_entities_fair_market_trading(self) -> None:
        """Cenário 4.2: Negociação legítima entre comitentes e CNPJs totalmente independentes.

        Empresa X (CNPJ 11.111.111/0001-11) vende e Empresa Y (CNPJ 22.222.222/0001-22) compra.
        Mesmo instrumento, mesmo preço de mercado, mesmo instante de tempo.
        Deve produzir rigorosamente zero alertas de wash trading.
        """
        rec_sell_indep = AuditRecord(
            record_id="INDEP-SELL-01",
            timestamp=self.base_time,
            participant_id="BRK105",
            clearing_member_id="CLR001",
            account_id="ACC_CORP_X",
            beneficiary_id="11.111.111/0001-11",
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OP_X",
            symbol="VALE3",
            side=Side.SELL,
            price=Decimal("62.50"),
            quantity=5000,
            order_id="ORD-X-01",
            cl_ord_id="CL-X-01",
            action_type=ActionType.TRADE,
        )
        rec_buy_indep = AuditRecord(
            record_id="INDEP-BUY-01",
            timestamp=self.base_time,
            participant_id="BRK200",
            clearing_member_id="CLR002",
            account_id="ACC_CORP_Y",
            beneficiary_id="22.222.222/0001-22",
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OP_Y",
            symbol="VALE3",
            side=Side.BUY,
            price=Decimal("62.50"),
            quantity=5000,
            order_id="ORD-Y-01",
            cl_ord_id="CL-Y-01",
            action_type=ActionType.TRADE,
        )

        self.engine.ingest_record(rec_sell_indep)
        self.engine.ingest_record(rec_buy_indep)

        alerts = self.engine.detect_wash_trading(time_window_seconds=10.0)
        self.assertEqual(len(alerts), 0, "Partes independentes não devem disparar Wash Trading")

    def test_normal_order_cancellation_human_and_algo_latency(self) -> None:
        """Cenário 4.3: Cancelamentos normais com latência humana e institucional (> 500ms).

        Ordens normais que descansaram no book por 2.5s e 10s antes de serem canceladas
        por rebalanceamento ou expiração não devem ser confundidas com spoofing.
        """
        t0 = self.base_time
        records = [
            AuditRecord(
                record_id="NORM-NEW-01",
                timestamp=t0,
                participant_id="BRK105",
                clearing_member_id="CLR001",
                account_id="ACC_RETAIL_1",
                beneficiary_id="123.456.789-99",
                beneficiary_type=BeneficiaryType.PF,
                operator_id="OP_RETAIL",
                symbol="PETR4",
                side=Side.BUY,
                price=Decimal("38.10"),
                quantity=200,
                order_id="ORD-RETAIL-01",
                cl_ord_id="CL-R-01",
                action_type=ActionType.NEW,
            ),
            AuditRecord(
                record_id="NORM-CANC-01",
                timestamp=t0 + timedelta(seconds=2, milliseconds=500),  # 2500ms
                participant_id="BRK105",
                clearing_member_id="CLR001",
                account_id="ACC_RETAIL_1",
                beneficiary_id="123.456.789-99",
                beneficiary_type=BeneficiaryType.PF,
                operator_id="OP_RETAIL",
                symbol="PETR4",
                side=Side.BUY,
                price=Decimal("38.10"),
                quantity=200,
                order_id="ORD-RETAIL-01",
                cl_ord_id="CL-R-01",
                action_type=ActionType.CANCEL,
            ),
        ]

        self.engine.ingest_records(records)
        flash_alerts = self.engine.detect_flash_cancellations(flash_threshold_ms=50.0)
        self.assertEqual(len(flash_alerts), 0, "Cancelamento após 2.5s não deve disparar alerta relâmpago")


class TestRedTeamIntegratedStressAndAuditDossier(unittest.TestCase):
    """Teste integrado de estresse com geração de Dossiê de Auditoria Regulatória BSM."""

    def test_integrated_market_session_with_injected_attacks(self) -> None:
        """Cenário 5: Pregão completo com fluxo misto (benigno + 3 ataques injetados).

        Valida que o motor:
        1. Detecta 100% dos ataques injetados (Wash Trading de Raiz, Spoofing Relâmpago e Layering).
        2. Mantém 0% de falsos positivos no fluxo limpo de fundo.
        3. Gera Dossiê oficial da BSM com integridade probatória de evidências.
        """
        engine = BsmSurveillanceEngine()
        t_base = datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc)
        records = []

        # --- Fluxo Benigno de Fundo (50 negócios entre participantes independentes) ---
        for i in range(25):
            records.append(
                AuditRecord(
                    record_id=f"CLEAN-BUY-{i:03d}",
                    timestamp=t_base + timedelta(seconds=i * 20),
                    participant_id="BROKER_INSTITUTIONAL",
                    clearing_member_id="CLEARING001",
                    account_id=f"ACC_FUND_{i}",
                    beneficiary_id=f"33.000.{i:03d}/0001-10",
                    beneficiary_type=BeneficiaryType.INVESTMENT_FUND,
                    operator_id="OPER-PORTFOLIO",
                    symbol="PETR4",
                    side=Side.BUY,
                    price=Decimal("38.50"),
                    quantity=1000,
                    order_id=f"ORD-CLEAN-B-{i}",
                    cl_ord_id=f"CL-CB-{i}",
                    action_type=ActionType.TRADE,
                )
            )
            records.append(
                AuditRecord(
                    record_id=f"CLEAN-SELL-{i:03d}",
                    timestamp=t_base + timedelta(seconds=i * 20),
                    participant_id="BROKER_RETAIL",
                    clearing_member_id="CLEARING002",
                    account_id=f"ACC_RETAIL_{i}",
                    beneficiary_id=f"44.000.{i:03d}/0001-20",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OPER-RETAIL",
                    symbol="PETR4",
                    side=Side.SELL,
                    price=Decimal("38.50"),
                    quantity=1000,
                    order_id=f"ORD-CLEAN-S-{i}",
                    cl_ord_id=f"CL-CS-{i}",
                    action_type=ActionType.TRADE,
                )
            )

        # --- Injeção de Ataque 1: Wash Trading por Raiz de CNPJ (Matriz e Filial) em VALE3 ---
        t_atk1 = t_base + timedelta(minutes=10)
        records.append(
            AuditRecord(
                record_id="ATK1-WASH-MATRIZ-BUY",
                timestamp=t_atk1,
                participant_id="BROKER105",
                clearing_member_id="CLEARING001",
                account_id="ACC_MATRIZ",
                beneficiary_id="99.888.777/0001-10",
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OPER-WASH-01",
                symbol="VALE3",
                side=Side.BUY,
                price=Decimal("63.00"),
                quantity=8000,
                order_id="ORD-ATK1-BUY",
                cl_ord_id="CL-ATK1-B",
                action_type=ActionType.TRADE,
            )
        )
        records.append(
            AuditRecord(
                record_id="ATK1-WASH-FILIAL-SELL",
                timestamp=t_atk1 + timedelta(seconds=1),
                participant_id="BROKER200",
                clearing_member_id="CLEARING002",
                account_id="ACC_FILIAL",
                beneficiary_id="99.888.777/0002-99",  # Mesma raiz 99888777
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OPER-WASH-02",
                symbol="VALE3",
                side=Side.SELL,
                price=Decimal("63.00"),
                quantity=8000,
                order_id="ORD-ATK1-SELL",
                cl_ord_id="CL-ATK1-S",
                action_type=ActionType.TRADE,
            )
        )

        # --- Injeção de Ataque 2: Spoofing Flash Cancel em 15ms em WINZ26 ---
        t_atk2 = t_base + timedelta(minutes=20)
        records.append(
            AuditRecord(
                record_id="ATK2-SPOOF-NEW",
                timestamp=t_atk2,
                participant_id="BROKER_HFT",
                clearing_member_id="CLEARING001",
                account_id="ACC_HFT_PROP",
                beneficiary_id="55.666.777/0001-88",
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OPER-HFT-PUMA",
                algo_id="ALGO-PREDATOR",
                symbol="WINZ26",
                side=Side.BUY,
                price=Decimal("131000.0"),
                quantity=100,
                order_id="ORD-SPOOF-FLASH-99",
                cl_ord_id="CL-SPOOF-99",
                action_type=ActionType.NEW,
            )
        )
        records.append(
            AuditRecord(
                record_id="ATK2-SPOOF-FILL-ASK",
                timestamp=t_atk2 + timedelta(milliseconds=10),
                participant_id="BROKER_HFT",
                clearing_member_id="CLEARING001",
                account_id="ACC_HFT_PROP",
                beneficiary_id="55.666.777/0001-88",
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OPER-HFT-PUMA",
                algo_id="ALGO-PREDATOR",
                symbol="WINZ26",
                side=Side.SELL,
                price=Decimal("131050.0"),
                quantity=20,
                order_id="ORD-REAL-FILL-99",
                cl_ord_id="CL-FILL-99",
                action_type=ActionType.TRADE,
                execution_id="EXEC-WIN-777",
            )
        )
        records.append(
            AuditRecord(
                record_id="ATK2-SPOOF-CANC",
                timestamp=t_atk2 + timedelta(milliseconds=15),  # 15ms
                participant_id="BROKER_HFT",
                clearing_member_id="CLEARING001",
                account_id="ACC_HFT_PROP",
                beneficiary_id="55.666.777/0001-88",
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OPER-HFT-PUMA",
                algo_id="ALGO-PREDATOR",
                symbol="WINZ26",
                side=Side.BUY,
                price=Decimal("131000.0"),
                quantity=100,
                order_id="ORD-SPOOF-FLASH-99",
                cl_ord_id="CL-SPOOF-99",
                action_type=ActionType.CANCEL,
            )
        )

        # --- Injeção de Ataque 3: Layering em 4 níveis em BBDC4 ---
        t_atk3 = t_base + timedelta(minutes=30)
        layer_pxs = [Decimal("15.20"), Decimal("15.18"), Decimal("15.16"), Decimal("15.14")]
        for idx, px in enumerate(layer_pxs):
            records.append(
                AuditRecord(
                    record_id=f"ATK3-LAYER-N-{idx}",
                    timestamp=t_atk3 + timedelta(milliseconds=idx * 50),
                    participant_id="BROKER300",
                    clearing_member_id="CLEARING001",
                    account_id="ACC_SPEC_01",
                    beneficiary_id="77.888.999/0001-33",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OPER-LAYER-01",
                    symbol="BBDC4",
                    side=Side.BUY,
                    price=px,
                    quantity=5000,
                    order_id=f"ORD-LAYER-4P-{idx}",
                    cl_ord_id=f"CL-L4-{idx}",
                    action_type=ActionType.NEW,
                )
            )
            records.append(
                AuditRecord(
                    record_id=f"ATK3-LAYER-C-{idx}",
                    timestamp=t_atk3 + timedelta(seconds=2, milliseconds=idx * 20),
                    participant_id="BROKER300",
                    clearing_member_id="CLEARING001",
                    account_id="ACC_SPEC_01",
                    beneficiary_id="77.888.999/0001-33",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OPER-LAYER-01",
                    symbol="BBDC4",
                    side=Side.BUY,
                    price=px,
                    quantity=5000,
                    order_id=f"ORD-LAYER-4P-{idx}",
                    cl_ord_id=f"CL-L4-{idx}",
                    action_type=ActionType.CANCEL,
                )
            )

        engine.ingest_records(records)

        # Geração do Relatório Executivo
        report = engine.generate_audit_report("BSM-STRESS-REP-2026")
        self.assertIsInstance(report, BsmAuditReport)
        self.assertEqual(report.total_records, len(records))

        # Deve capturar com 100% de recall exatamente os 3 ataques injetados
        self.assertEqual(report.alerts_count, 3, f"Esperava exatamente 3 alertas de ataques injetados, obteve {report.alerts_count}")

        pattern_types = {a.pattern_type for a in report.alerts}
        self.assertIn(AbusePatternType.WASH_TRADING, pattern_types)
        self.assertIn(AbusePatternType.SPOOFING, pattern_types)
        self.assertIn(AbusePatternType.LAYERING, pattern_types)

        # Testa exportação de dossiê BSM para o ataque de spoofing relâmpago
        spoof_alert = [a for a in report.alerts if a.pattern_type == AbusePatternType.SPOOFING][0]
        dossier = engine.export_bsm_dossier(spoof_alert)
        self.assertEqual(dossier["entity"], "BSM Supervisão de Mercados - B3 S.A.")
        self.assertEqual(dossier["alert"]["pattern_type"], "SPOOFING")
        self.assertEqual(dossier["alert"]["severity"], "CRITICAL")
        self.assertGreaterEqual(dossier["evidence_count"], 3)
        self.assertEqual(len(dossier["evidence_chain"]), 3)


if __name__ == "__main__":
    unittest.main()
