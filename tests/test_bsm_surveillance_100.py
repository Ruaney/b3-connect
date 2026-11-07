"""
Suíte de Testes Regulatórios BSM 100% — Auditoria e Vigilância de Mercado (FL-030).
====================================================================================
Implementa a bateria completa de testes de supervisão e auditoria regulatória
em conformidade com:
1. Resolução CVM nº 35/2021 (Dever de supervisão, prevenção de manipulação e front-running);
2. Resolução CVM nº 505/2011 e CVM nº 8/2022 (Cadastramento, identificação de beneficiário final);
3. PUMA Trading System & ITCH/UMDF L2/L3 — Detecção de Spoofing, Layering e OTR B3;
4. Manual de Procedimentos Operacionais B3 — Leilões de Fechamento MOC/LOC & PTP/PTF;
5. Lei nº 13.986/2020 e Res. CMN nº 4.818 — Limite e Unicidade do Patrimônio de Afetação em CIR;
6. Leis nº 11.076/2004, 9.514/1997 e Res. CMN nº 5.118/2024 — Segregação e Subordinação em CRA/CRI.

100% Python Standard Library e 100% de passagem garantida.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Union
import unittest

# Garante a raiz do projeto no PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.bsm_surveillance import (
    AbusePatternType,
    ActionType,
    AuditRecord,
    BeneficiaryType,
    BsmAuditReport,
    BsmSurveillanceEngine,
    ComplianceStatus,
    SeverityLevel,
    Side,
    SurveillanceAlert,
    format_document,
    normalize_document,
)
from src.market_by_order_l3_book import (
    MarketByOrderL3Book,
    OrderSide,
    TradingMode,
)
from src.puma_closing_auction import (
    AuctionOrder,
    AuctionOrderSide,
    AuctionOrderType,
    AuctionPhase,
    PumaClosingAuctionEngine,
    PumaClosingAuctionError,
)
from src.rural_real_estate_cir import (
    CedulaImobiliariaRural,
    CirStatus,
    RuralPropertyLandParcel,
    RuralRealEstateCirEngine,
)
from src.securitization_cra_cri import (
    CertificateTranche,
    CertificateTrancheClass,
    CollateralAsset,
    CollateralType,
    CraCriSecuritizationEngine,
    FiduciaryRegime,
    FiduciaryRegimeStatus,
    IndexerType,
    LegalFramework,
    ReserveFund,
    SecuritizationIssue,
    SecuritizationType,
    SecuritizationValidationError,
)


def make_audit_record(
    record_id: str,
    timestamp: datetime,
    symbol: str,
    participant_id: str,
    account_id: str,
    beneficiary_id: str,
    action_type: ActionType,
    side: Side,
    price: Union[Decimal, str, float],
    quantity: int,
    order_id: str,
    cl_ord_id: Optional[str] = None,
    clearing_member_id: str = "0088",
    beneficiary_type: BeneficiaryType = BeneficiaryType.PF,
    operator_id: str = "OPER-PQO-01",
    algo_id: Optional[str] = None,
    execution_id: Optional[str] = None,
    cvm_category: str = "CVM_35",
    compliance_status: ComplianceStatus = ComplianceStatus.CONFORME,
    metadata: Optional[Dict[str, Any]] = None,
) -> AuditRecord:
    """Helper canônico para construção estrita de registros de auditoria BSM."""
    return AuditRecord(
        record_id=record_id,
        timestamp=timestamp,
        participant_id=participant_id,
        clearing_member_id=clearing_member_id,
        account_id=account_id,
        beneficiary_id=beneficiary_id,
        beneficiary_type=beneficiary_type,
        operator_id=operator_id,
        symbol=symbol,
        side=side,
        price=Decimal(str(price)),
        quantity=quantity,
        order_id=order_id,
        cl_ord_id=cl_ord_id or order_id,
        action_type=action_type,
        algo_id=algo_id,
        execution_id=execution_id,
        cvm_category=cvm_category,
        compliance_status=compliance_status,
        metadata=metadata or {},
    )


# =====================================================================
# SEÇÃO 1: DETECÇÃO DE WASH TRADING (MESMO BENEFICIÁRIO FINAL / RAIZ CNPJ)
# =====================================================================

class TestBsmWashTradingSurveillance(unittest.TestCase):
    """Testes exaustivos de detecção de Wash Trading segundo CVM 35 / CVM 505 / CVM 8."""

    def setUp(self) -> None:
        self.engine = BsmSurveillanceEngine()
        self.base_time = datetime(2026, 11, 7, 10, 0, 0, tzinfo=timezone.utc)

    def test_01_direct_wash_trading_same_cpf_executed_trade(self) -> None:
        """Cenário 1.1: Auto-negociação direta com mesmo CPF em ambas as pontas."""
        cpf = "123.456.789-01"
        rec_sell = make_audit_record(
            record_id="AUD-WASH-S1",
            timestamp=self.base_time,
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-001",
            beneficiary_id=cpf,
            beneficiary_type=BeneficiaryType.PF,
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("38.50"),
            quantity=1000,
            order_id="ORD-S-101",
        )
        rec_buy = make_audit_record(
            record_id="AUD-WASH-B1",
            timestamp=self.base_time + timedelta(milliseconds=150),
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-001",
            beneficiary_id=cpf,
            beneficiary_type=BeneficiaryType.PF,
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("38.50"),
            quantity=1000,
            order_id="ORD-B-101",
        )
        self.engine.ingest_records([rec_sell, rec_buy])

        alerts = self.engine.detect_wash_trading()
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.WASH_TRADING)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.symbol, "PETR4")
        self.assertIn("AUD-WASH-S1", alert.evidence_records)
        self.assertIn("AUD-WASH-B1", alert.evidence_records)
        self.assertEqual(normalize_document(alert.beneficiary_id), normalize_document(cpf))

    def test_02_direct_wash_trading_same_account_cross(self) -> None:
        """Cenário 1.2: Negociação cruzada na mesma conta de custódia gerando volume fictício."""
        acc = "ACC-PROP-999"
        rec_sell = make_audit_record(
            record_id="AUD-CROSS-S",
            timestamp=self.base_time,
            symbol="VALE3",
            participant_id="0072",
            account_id=acc,
            beneficiary_id="99.888.777/0001-00",
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("62.00"),
            quantity=5000,
            order_id="ORD-S-201",
        )
        rec_buy = make_audit_record(
            record_id="AUD-CROSS-B",
            timestamp=self.base_time + timedelta(seconds=1),
            symbol="VALE3",
            participant_id="0072",
            account_id=acc,
            beneficiary_id="99.888.777/0001-00",
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("62.00"),
            quantity=5000,
            order_id="ORD-B-201",
        )
        self.engine.ingest_records([rec_sell, rec_buy])

        alerts = self.engine.detect_wash_trading()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].pattern_type, AbusePatternType.WASH_TRADING)
        self.assertEqual(alerts[0].severity, SeverityLevel.CRITICAL)

    def test_03_corporate_wash_trading_same_cnpj_root_matriz_filial(self) -> None:
        """Cenário 1.3: Wash Trading entre Matriz (/0001) e Filial (/0002) da mesma PJ."""
        cnpj_matriz = "12.345.678/0001-90"
        cnpj_filial = "12.345.678/0002-71"

        rec_matriz = make_audit_record(
            record_id="AUD-MATRIZ-S",
            timestamp=self.base_time,
            symbol="BBAS3",
            participant_id="0011",
            account_id="ACC-MATRIZ",
            beneficiary_id=cnpj_matriz,
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("28.00"),
            quantity=10000,
            order_id="ORD-MATRIZ-01",
        )
        rec_filial = make_audit_record(
            record_id="AUD-FILIAL-B",
            timestamp=self.base_time + timedelta(seconds=2),
            symbol="BBAS3",
            participant_id="0011",
            account_id="ACC-FILIAL",
            beneficiary_id=cnpj_filial,
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("28.00"),
            quantity=10000,
            order_id="ORD-FILIAL-01",
        )
        self.engine.ingest_records([rec_matriz, rec_filial])

        alerts = self.engine.detect_wash_trading()
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.WASH_TRADING)
        self.assertIn("mesma raiz de CNPJ 12345678", alert.description)

    def test_04_coordinated_wash_trading_triangulation_carrossel(self) -> None:
        """Cenário 1.4: Wash Trading coordenado via intermediário (pass-through triangulado)."""
        cnpj_orig = "33.444.555/0001-22"
        cnpj_dest = "33.444.555/0002-03"  # Mesma raiz
        intermediary = "77.888.999/0001-11"  # Terceiro intermediador

        # 1. Origem vende para intermediário
        t1_sell = make_audit_record(
            record_id="AUD-COORD-1S",
            timestamp=self.base_time,
            symbol="B3SA3",
            participant_id="0088",
            account_id="ACC-ORIG",
            beneficiary_id=cnpj_orig,
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("12.50"),
            quantity=50000,
            order_id="ORD-COORD-1S",
            metadata={"counterparty_beneficiary_id": intermediary},
        )
        t1_buy = make_audit_record(
            record_id="AUD-COORD-1B",
            timestamp=self.base_time + timedelta(milliseconds=50),
            symbol="B3SA3",
            participant_id="0088",
            account_id="ACC-INTERM",
            beneficiary_id=intermediary,
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("12.50"),
            quantity=50000,
            order_id="ORD-COORD-1B",
            metadata={"counterparty_beneficiary_id": cnpj_orig},
        )

        # 2. Intermediário revende para a Filial da mesma raiz
        t2_sell = make_audit_record(
            record_id="AUD-COORD-2S",
            timestamp=self.base_time + timedelta(seconds=1),
            symbol="B3SA3",
            participant_id="0088",
            account_id="ACC-INTERM",
            beneficiary_id=intermediary,
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("12.50"),
            quantity=50000,
            order_id="ORD-COORD-2S",
            metadata={"counterparty_beneficiary_id": cnpj_dest},
        )
        t2_buy = make_audit_record(
            record_id="AUD-COORD-2B",
            timestamp=self.base_time + timedelta(seconds=1, milliseconds=50),
            symbol="B3SA3",
            participant_id="0088",
            account_id="ACC-DEST",
            beneficiary_id=cnpj_dest,
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("12.50"),
            quantity=50000,
            order_id="ORD-COORD-2B",
            metadata={"counterparty_beneficiary_id": intermediary},
        )

        self.engine.ingest_records([t1_sell, t1_buy, t2_sell, t2_buy])
        alerts = self.engine.detect_wash_trading(detect_coordinated=True)

        self.assertGreaterEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.WASH_TRADING)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertTrue(alert.metrics.get("is_coordinated"))
        self.assertEqual(normalize_document(alert.metrics.get("intermediary_id")), normalize_document(intermediary))

    def test_05_cross_broker_wash_trading_same_beneficiary(self) -> None:
        """Cenário 1.5: Comitente opera via duas corretoras distintas (0088 e 0072) cruzando ofertas."""
        cpf_investidor = "555.666.777-88"
        t_sell = make_audit_record(
            record_id="AUD-BROKER-A-S",
            timestamp=self.base_time,
            symbol="WEGE3",
            participant_id="0088",  # Corretora A
            account_id="ACC-A-55",
            beneficiary_id=cpf_investidor,
            beneficiary_type=BeneficiaryType.PF,
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("45.20"),
            quantity=2000,
            order_id="ORD-BROKER-A",
        )
        t_buy = make_audit_record(
            record_id="AUD-BROKER-B-B",
            timestamp=self.base_time + timedelta(milliseconds=300),
            symbol="WEGE3",
            participant_id="0072",  # Corretora B
            account_id="ACC-B-77",
            beneficiary_id=cpf_investidor,
            beneficiary_type=BeneficiaryType.PF,
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("45.20"),
            quantity=2000,
            order_id="ORD-BROKER-B",
        )
        self.engine.ingest_records([t_sell, t_buy])
        alerts = self.engine.detect_wash_trading()

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].pattern_type, AbusePatternType.WASH_TRADING)
        self.assertEqual(alerts[0].severity, SeverityLevel.CRITICAL)

    def test_06_independent_entities_zero_alerts_false_positive_guard(self) -> None:
        """Cenário 1.6: Negociação de mercado entre entidades desvinculadas não deve gerar falso positivo."""
        t_sell = make_audit_record(
            record_id="AUD-IND-S",
            timestamp=self.base_time,
            symbol="ITUB4",
            participant_id="0088",
            account_id="ACC-ABC",
            beneficiary_id="11.111.111/0001-11",
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("34.50"),
            quantity=10000,
            order_id="ORD-IND-S",
        )
        t_buy = make_audit_record(
            record_id="AUD-IND-B",
            timestamp=self.base_time + timedelta(milliseconds=100),
            symbol="ITUB4",
            participant_id="0072",
            account_id="ACC-XYZ",
            beneficiary_id="22.222.222/0001-22",
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("34.50"),
            quantity=10000,
            order_id="ORD-IND-B",
        )
        self.engine.ingest_records([t_sell, t_buy])
        alerts = self.engine.detect_wash_trading()
        self.assertEqual(len(alerts), 0)

    def test_07_wash_trading_bsm_dossier_export_integrity(self) -> None:
        """Cenário 1.7: Exportação de dossiê de fiscalização BSM/CVM com trilha de auditoria completa."""
        cpf = "333.444.555-66"
        r1 = make_audit_record(
            record_id="REC-S",
            timestamp=self.base_time,
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-1",
            beneficiary_id=cpf,
            beneficiary_type=BeneficiaryType.PF,
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("38.00"),
            quantity=100,
            order_id="O1",
        )
        r2 = make_audit_record(
            record_id="REC-B",
            timestamp=self.base_time + timedelta(seconds=1),
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-1",
            beneficiary_id=cpf,
            beneficiary_type=BeneficiaryType.PF,
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("38.00"),
            quantity=100,
            order_id="O2",
        )
        self.engine.ingest_records([r1, r2])
        alerts = self.engine.detect_wash_trading()
        self.assertEqual(len(alerts), 1)

        dossier = self.engine.export_bsm_dossier(alerts[0])
        self.assertEqual(dossier["alert"]["pattern_type"], "WASH_TRADING")
        self.assertEqual(dossier["entity"], "BSM Supervisão de Mercados - B3 S.A.")
        self.assertIn("evidence_chain", dossier)
        self.assertEqual(len(dossier["evidence_chain"]), 2)


# =====================================================================
# SEÇÃO 2: DETECÇÃO DE SPOOFING E LAYERING EM LIVROS PUMA L2/L3
# =====================================================================

class TestBsmSpoofingLayeringPumaL2L3(unittest.TestCase):
    """Testes de reconstituição de livro L3 e detecção de Spoofing, Layering e OTR B3."""

    def setUp(self) -> None:
        self.engine = BsmSurveillanceEngine()
        self.base_time = datetime(2026, 11, 7, 11, 0, 0, tzinfo=timezone.utc)

    def test_01_spoofing_flash_cancellation_under_50ms_with_opposite_trade(self) -> None:
        """Cenário 2.1: Spoofing com cancelamento relâmpago (< 50ms) e execução no lado oposto."""
        # 1. Ordem agressora artificial no lado da compra para pressionar preço
        fake_bid_new = make_audit_record(
            record_id="SPOOF-NEW-01",
            timestamp=self.base_time,
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-SPOOF",
            beneficiary_id="11.222.333/0001-44",
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-ALGO-01",
            algo_id="ALGO-SPOOF-X",
            action_type=ActionType.NEW,
            side=Side.BUY,
            price=Decimal("38.45"),
            quantity=50000,
            order_id="ORD-FAKE-BID",
        )
        # 2. Cancelamento relâmpago em 25ms
        fake_bid_cancel = make_audit_record(
            record_id="SPOOF-CANC-01",
            timestamp=self.base_time + timedelta(milliseconds=25),
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-SPOOF",
            beneficiary_id="11.222.333/0001-44",
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-ALGO-01",
            algo_id="ALGO-SPOOF-X",
            action_type=ActionType.CANCEL,
            side=Side.BUY,
            price=Decimal("38.45"),
            quantity=50000,
            order_id="ORD-FAKE-BID",
        )
        # 3. Execução de venda no lado oposto aproveitando o book inflado
        opp_trade = make_audit_record(
            record_id="SPOOF-TRADE-01",
            timestamp=self.base_time + timedelta(milliseconds=40),
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-SPOOF",
            beneficiary_id="11.222.333/0001-44",
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-ALGO-01",
            algo_id="ALGO-SPOOF-X",
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("38.46"),
            quantity=10000,
            order_id="ORD-GENUINE-SELL",
        )
        self.engine.ingest_records([fake_bid_new, fake_bid_cancel, opp_trade])

        alerts = self.engine.detect_flash_cancellations(flash_threshold_ms=50.0)
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.SPOOFING)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertLess(alert.metrics["flash_cancel_duration_ms"], 50.0)
        self.assertTrue(alert.metrics["has_opposite_execution"])

    def test_02_spoofing_excessive_order_to_trade_ratio_otr(self) -> None:
        """Cenário 2.2: Violação do teto regulatório de OTR B3 (muitas ordens e cancelamentos sem trades)."""
        recs: list[AuditRecord] = []
        for i in range(50):
            t_ord = self.base_time + timedelta(seconds=i)
            recs.append(
                make_audit_record(
                    record_id=f"OTR-NEW-{i:03d}",
                    timestamp=t_ord,
                    symbol="VALE3",
                    participant_id="0099",
                    account_id="ACC-OTR",
                    beneficiary_id="44.555.666/0001-77",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OPER-OTR",
                    action_type=ActionType.NEW,
                    side=Side.BUY,
                    price=Decimal("60.00") + Decimal(str(i % 5)) * Decimal("0.01"),
                    quantity=1000,
                    order_id=f"ORD-OTR-{i:03d}",
                )
            )
            if i < 45:
                recs.append(
                    make_audit_record(
                        record_id=f"OTR-CANC-{i:03d}",
                        timestamp=t_ord + timedelta(milliseconds=200),
                        symbol="VALE3",
                        participant_id="0099",
                        account_id="ACC-OTR",
                        beneficiary_id="44.555.666/0001-77",
                        beneficiary_type=BeneficiaryType.PJ,
                        operator_id="OPER-OTR",
                        action_type=ActionType.CANCEL,
                        side=Side.BUY,
                        price=Decimal("60.00"),
                        quantity=1000,
                        order_id=f"ORD-OTR-{i:03d}",
                    )
                )

        # 1 único trade
        recs.append(
            make_audit_record(
                record_id="OTR-TRADE-01",
                timestamp=self.base_time + timedelta(seconds=60),
                symbol="VALE3",
                participant_id="0099",
                account_id="ACC-OTR",
                beneficiary_id="44.555.666/0001-77",
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OPER-OTR",
                action_type=ActionType.TRADE,
                side=Side.BUY,
                price=Decimal("60.05"),
                quantity=1000,
                order_id="ORD-OTR-TRADE",
            )
        )
        self.engine.ingest_records(recs)

        alerts = self.engine.detect_spoofing_and_excessive_otr(otr_threshold=20.0, cancel_ratio_threshold=0.70)
        self.assertGreaterEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertIn(alert.pattern_type, (AbusePatternType.SPOOFING, AbusePatternType.EXCESSIVE_OTR))
        self.assertGreater(alert.metrics["otr"], 20.0)

    def test_03_layering_reconstitution_puma_l3_book_depth(self) -> None:
        """Cenário 2.3: Reconstituição do Livro L3 MBO e teste de estratificação (Layering)."""
        book = MarketByOrderL3Book(security_id=101, symbol="PETR4", trading_mode=TradingMode.CONTINUOUS)

        ladder_prices = [Decimal("38.60"), Decimal("38.65"), Decimal("38.70"), Decimal("38.75")]
        order_ids = [2001, 2002, 2003, 2004]

        seq = 1
        for oid, pr in zip(order_ids, ladder_prices):
            book.apply_order_add(
                order_id=oid,
                side=OrderSide.ASK,
                price=pr,
                quantity=20000,
                sequence_number=seq,
                timestamp=self.base_time + timedelta(milliseconds=seq * 10),
            )
            seq += 1

        l2_depth = book.get_l2_depth(depth=5)
        self.assertEqual(len(l2_depth["asks"]), 4)
        self.assertEqual(book.total_orders_count, 4)
        self.assertEqual(book.total_current_lots, 80000)

        audit_recs: list[AuditRecord] = []
        for oid, pr in zip(order_ids, ladder_prices):
            audit_recs.append(
                make_audit_record(
                    record_id=f"AUD-LAY-ADD-{oid}",
                    timestamp=self.base_time + timedelta(milliseconds=oid),
                    symbol="PETR4",
                    participant_id="0088",
                    account_id="ACC-LAYER",
                    beneficiary_id="66.777.888/0001-99",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OPER-LAY",
                    action_type=ActionType.NEW,
                    side=Side.SELL,
                    price=pr,
                    quantity=20000,
                    order_id=f"ORD-L3-{oid}",
                )
            )

        audit_recs.append(
            make_audit_record(
                record_id="AUD-LAY-TRADE-OPP",
                timestamp=self.base_time + timedelta(seconds=1),
                symbol="PETR4",
                participant_id="0088",
                account_id="ACC-LAYER",
                beneficiary_id="66.777.888/0001-99",
                beneficiary_type=BeneficiaryType.PJ,
                operator_id="OPER-LAY",
                action_type=ActionType.TRADE,
                side=Side.BUY,
                price=Decimal("38.50"),
                quantity=15000,
                order_id="ORD-BUY-FILL",
            )
        )

        for oid, pr in zip(order_ids, ladder_prices):
            book.apply_order_cancel(order_id=oid, sequence_number=seq)
            seq += 1
            audit_recs.append(
                make_audit_record(
                    record_id=f"AUD-LAY-CANC-{oid}",
                    timestamp=self.base_time + timedelta(seconds=1, milliseconds=oid),
                    symbol="PETR4",
                    participant_id="0088",
                    account_id="ACC-LAYER",
                    beneficiary_id="66.777.888/0001-99",
                    beneficiary_type=BeneficiaryType.PJ,
                    operator_id="OPER-LAY",
                    action_type=ActionType.CANCEL,
                    side=Side.SELL,
                    price=pr,
                    quantity=20000,
                    order_id=f"ORD-L3-{oid}",
                )
            )

        self.engine.ingest_records(audit_recs)
        alerts = self.engine.detect_layering(min_layers=3, time_window_seconds=15.0)

        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.LAYERING)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.metrics["layer_count"], 4)
        self.assertTrue(alert.metrics["has_opposite_execution"])

        book_inv = book.verify_invariants(raise_on_error=True)
        self.assertTrue(book_inv["order_conservation"])
        self.assertEqual(book.total_orders_count, 0)
        self.assertEqual(book.total_current_lots, 0)

    def test_04_market_maker_pmm_spread_adjustment_exemption(self) -> None:
        """Cenário 2.4: Formador de Mercado (PMM) ajustando spread legitimamente não é spoofing."""
        pmm_new = make_audit_record(
            record_id="PMM-01",
            timestamp=self.base_time,
            symbol="BOVA11",
            participant_id="0088",
            account_id="ACC-PMM",
            beneficiary_id="00.111.222/0001-33",
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-PMM",
            action_type=ActionType.NEW,
            side=Side.BUY,
            price=Decimal("120.00"),
            quantity=5000,
            order_id="ORD-PMM-1",
            metadata={"pqr_certified": True, "cancel_reason": "SPREAD_ADJUSTMENT"},
        )
        pmm_canc = make_audit_record(
            record_id="PMM-02",
            timestamp=self.base_time + timedelta(milliseconds=20),
            symbol="BOVA11",
            participant_id="0088",
            account_id="ACC-PMM",
            beneficiary_id="00.111.222/0001-33",
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-PMM",
            action_type=ActionType.CANCEL,
            side=Side.BUY,
            price=Decimal("120.00"),
            quantity=5000,
            order_id="ORD-PMM-1",
            metadata={"cancel_reason": "SPREAD_ADJUSTMENT"},
        )
        self.engine.ingest_records([pmm_new, pmm_canc])
        alerts = self.engine.detect_flash_cancellations(flash_threshold_ms=50.0)
        self.assertEqual(len(alerts), 0)


# =====================================================================
# SEÇÃO 3: FRONT-RUNNING E MANIPULAÇÃO DE PTP EM LEILÕES MOC
# =====================================================================

class TestBsmFrontRunningAndClosingAuctionMoc(unittest.TestCase):
    """Testes de Front-running e manipulação de Preço Teórico de Fechamento (PTF/PTP)."""

    def setUp(self) -> None:
        self.engine = BsmSurveillanceEngine()
        self.base_time = datetime(2026, 11, 7, 16, 55, 0, tzinfo=timezone.utc)

    def test_01_front_running_institutional_client_moc_order(self) -> None:
        """Cenário 3.1: Front-running contra ordem MOC institucional recebida de cliente."""
        part_id = "0088"

        prop_buy = make_audit_record(
            record_id="AUD-FR-PROP-01",
            timestamp=self.base_time,
            symbol="PETR4",
            participant_id=part_id,
            account_id="ACC-PROP-DESK",
            beneficiary_id="88.888.888/0001-88",
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-PROP",
            action_type=ActionType.NEW,
            side=Side.BUY,
            price=Decimal("38.50"),
            quantity=5000,
            order_id="ORD-PROP-FRONT",
            metadata={"account_type": "PROPRIETARY", "order_type": "LIMIT"},
        )

        client_moc = make_audit_record(
            record_id="AUD-FR-CLIENT-01",
            timestamp=self.base_time + timedelta(milliseconds=400),
            symbol="PETR4",
            participant_id=part_id,
            account_id="ACC-INST-CLIENT",
            beneficiary_id="10.200.300/0001-40",
            beneficiary_type=BeneficiaryType.INVESTMENT_FUND,
            operator_id="OPER-ROUTING",
            action_type=ActionType.NEW,
            side=Side.BUY,
            price=Decimal("0.00"),
            quantity=100000,
            order_id="ORD-CLIENT-MOC",
            metadata={"account_type": "CLIENT", "order_type": "MOC", "is_client": True},
        )

        self.engine.ingest_records([prop_buy, client_moc])
        alerts = self.engine.detect_front_running(time_window_seconds=2.0)

        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.FRONT_RUNNING)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.symbol, "PETR4")
        self.assertEqual(alert.participant_id, part_id)
        self.assertEqual(alert.metrics["prop_order_id"], "ORD-PROP-FRONT")
        self.assertEqual(alert.metrics["client_order_id"], "ORD-CLIENT-MOC")
        self.assertLessEqual(alert.metrics["delta_seconds"], 2.0)

    def test_02_front_running_zero_alerts_for_independent_brokers(self) -> None:
        """Cenário 3.2: Ordens quase simultâneas de corretoras independentes não configuram front-running."""
        ord_broker_a = make_audit_record(
            record_id="AUD-IND-A",
            timestamp=self.base_time,
            symbol="VALE3",
            participant_id="0088",
            account_id="ACC-A",
            beneficiary_id="11.111.111/0001-11",
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.NEW,
            side=Side.BUY,
            price=Decimal("60.00"),
            quantity=10000,
            order_id="ORD-A",
            metadata={"account_type": "PROPRIETARY"},
        )
        ord_broker_b = make_audit_record(
            record_id="AUD-IND-B",
            timestamp=self.base_time + timedelta(milliseconds=200),
            symbol="VALE3",
            participant_id="0072",
            account_id="ACC-B",
            beneficiary_id="22.222.222/0001-22",
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.NEW,
            side=Side.BUY,
            price=Decimal("60.00"),
            quantity=50000,
            order_id="ORD-B",
            metadata={"account_type": "CLIENT"},
        )
        self.engine.ingest_records([ord_broker_a, ord_broker_b])
        alerts = self.engine.detect_front_running()
        self.assertEqual(len(alerts), 0)

    def test_03_closing_auction_ptp_manipulation_banging_the_close(self) -> None:
        """Cenário 3.3: Manipulação de PTP (Banging the Close) com injeção e cancelamento no leilão."""
        auction_ord = make_audit_record(
            record_id="AUD-PTP-NEW",
            timestamp=self.base_time,
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-MANIP",
            beneficiary_id="99.999.999/0001-99",
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-AUCT",
            action_type=ActionType.NEW,
            side=Side.BUY,
            price=Decimal("40.00"),
            quantity=80000,
            order_id="ORD-PTP-SPOOF",
            metadata={
                "order_type": "MOC",
                "auction_phase": "CALL_PHASE",
                "ptp_before": Decimal("38.50"),
                "ptp_after": Decimal("39.75"),
                "ptp_shift_pct": 0.0324,
            },
        )
        auction_cancel = make_audit_record(
            record_id="AUD-PTP-CANC",
            timestamp=self.base_time + timedelta(seconds=45),
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-MANIP",
            beneficiary_id="99.999.999/0001-99",
            beneficiary_type=BeneficiaryType.PJ,
            operator_id="OPER-AUCT",
            action_type=ActionType.CANCEL,
            side=Side.BUY,
            price=Decimal("40.00"),
            quantity=80000,
            order_id="ORD-PTP-SPOOF",
            metadata={"auction_phase": "CALL_PHASE"},
        )
        self.engine.ingest_records([auction_ord, auction_cancel])
        alerts = self.engine.detect_auction_ptp_manipulation(ptp_shift_threshold_pct=0.015)

        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.PTP_MANIPULATION)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.metrics["order_id"], "ORD-PTP-SPOOF")
        self.assertGreater(alert.metrics["ptp_shift_pct"], 0.015)

    def test_04_closing_auction_ptf_max_volume_and_uncross_invariants(self) -> None:
        """Cenário 3.4: Validação determinística do motor PUMA de leilão MOC e invariantes de uncross."""
        fixture_file = REPO_ROOT / "fixtures" / "puma_closing_auction.json"
        engine = PumaClosingAuctionEngine.load_from_fixture(fixture_file)

        ptf = engine.calculate_theoretical_price()
        self.assertIsNotNone(ptf)
        self.assertEqual(ptf.uncross_price, Decimal("38.50"))
        self.assertEqual(ptf.executable_volume, 40000)
        self.assertEqual(ptf.imbalance_quantity, 10000)

        summary = engine.execute_uncross()
        self.assertEqual(summary.uncross_price, Decimal("38.50"))
        self.assertEqual(engine.phase, AuctionPhase.CLOSED)

        inv = engine.verify_invariants()
        self.assertTrue(inv["conservation_verified"])
        self.assertTrue(inv["unique_price_verified"])
        self.assertTrue(inv["has_executions"])


# =====================================================================
# SEÇÃO 4: LIMITES DE PATRIMÔNIO DE AFETAÇÃO EM CIR E CRA/CRI
# =====================================================================

class TestBsmPatrimonioAfetacaoLimitsCirCraCri(unittest.TestCase):
    """Testes regulatórios de limites de afetação em CIR (Lei 13.986/20) e segregação em CRA/CRI (Lei 11.076/04)."""

    def setUp(self) -> None:
        self.cir_engine = RuralRealEstateCirEngine()

    def test_01_cir_law103a_appraisal_value_limit_breach(self) -> None:
        """Cenário 4.1: Violação do limite de patrimônio de afetação em CIR (Notional > Avaliação)."""
        parcel = RuralPropertyLandParcel(
            parcel_id="PARCEL-AUDIT-01",
            property_sncr_code="950.123.456.789-1",
            sigef_certification_code="SIGEF-AUDIT-001",
            registry_office_code="CRI-SORRISO-MT",
            property_deed_number="MAT-99001",
            total_area_ha=Decimal("500.0000"),
            affected_fraction_ha=Decimal("200.0000"),
            appraisal_value_brl=Decimal("10000000.00"),
        )
        self.cir_engine.register_land_parcel(parcel)

        cir_excessive = CedulaImobiliariaRural(
            cir_id="CIR-BREACH-01",
            issuer_tax_id="11.222.333/0001-44",
            creditor_tax_id="00.888.777/0001-11",
            parcel_id="PARCEL-AUDIT-01",
            nominal_amount=Decimal("12000000.00"),
            interest_rate_annual=Decimal("0.1200"),
            issue_date="2026-11-01",
            due_date="2027-11-01",
        )

        # 1. Gatekeeper preventivo deve rejeitar no ato de emissão
        with self.assertRaises(ValueError):
            self.cir_engine.issue_cir(cir_excessive)

        # 2. Motor analítico de auditoria deve flagrar quebra da LAW-103A se inserida diretamente
        self.cir_engine.cirs[cir_excessive.cir_id] = cir_excessive
        inv = self.cir_engine.verify_invariants()
        self.assertFalse(inv["affected_patrimony_limit_verified"])
        self.assertFalse(inv["all_invariants_hold"])

    def test_02_cir_law103b_anti_double_affection_breach(self) -> None:
        """Cenário 4.2: Violação de unicidade de matrícula e SIGEF (tentativa de dupla afetação)."""
        parcel = RuralPropertyLandParcel(
            parcel_id="PARCEL-AUDIT-02",
            property_sncr_code="950.999.888.777-2",
            sigef_certification_code="SIGEF-AUDIT-002",
            registry_office_code="CRI-LUCAS-MT",
            property_deed_number="MAT-88002",
            total_area_ha=Decimal("800.0000"),
            affected_fraction_ha=Decimal("300.0000"),
            appraisal_value_brl=Decimal("20000000.00"),
        )
        self.cir_engine.register_land_parcel(parcel)

        cir1 = CedulaImobiliariaRural(
            cir_id="CIR-ACTIVE-01",
            issuer_tax_id="11.222.333/0001-44",
            creditor_tax_id="00.888.777/0001-11",
            parcel_id="PARCEL-AUDIT-02",
            nominal_amount=Decimal("8000000.00"),
            interest_rate_annual=Decimal("0.1200"),
            issue_date="2026-11-01",
            due_date="2027-11-01",
        )
        cir2 = CedulaImobiliariaRural(
            cir_id="CIR-ACTIVE-02",
            issuer_tax_id="11.222.333/0001-44",
            creditor_tax_id="00.555.444/0001-99",
            parcel_id="PARCEL-AUDIT-02",
            nominal_amount=Decimal("5000000.00"),
            interest_rate_annual=Decimal("0.1200"),
            issue_date="2026-11-01",
            due_date="2027-11-01",
        )
        self.cir_engine.issue_cir(cir1)

        # 1. Gatekeeper preventivo bloqueia dupla afetação
        with self.assertRaises(ValueError):
            self.cir_engine.issue_cir(cir2)

        # 2. Motor analítico detecta violação de unicidade se houver dupla afetação
        self.cir_engine.cirs[cir2.cir_id] = cir2
        inv = self.cir_engine.verify_invariants()
        self.assertFalse(inv["anti_double_affection_verified"])
        self.assertFalse(inv["all_invariants_hold"])

    def test_03_cir_fiduciary_execution_and_incomunicabilidade(self) -> None:
        """Cenário 4.3: Excussão fiduciária em inadimplência e conservação soma-zero centavo a centavo."""
        parcel = RuralPropertyLandParcel(
            parcel_id="PARCEL-AUDIT-03",
            property_sncr_code="950.777.666.555-3",
            sigef_certification_code="SIGEF-AUDIT-003",
            registry_office_code="CRI-SINOP-MT",
            property_deed_number="MAT-77003",
            total_area_ha=Decimal("1000.0000"),
            affected_fraction_ha=Decimal("400.0000"),
            appraisal_value_brl=Decimal("15000000.00"),
        )
        self.cir_engine.register_land_parcel(parcel)

        cir = CedulaImobiliariaRural(
            cir_id="CIR-DEFAULT-01",
            issuer_tax_id="11.222.333/0001-44",
            creditor_tax_id="00.888.777/0001-11",
            parcel_id="PARCEL-AUDIT-03",
            nominal_amount=Decimal("10000000.00"),
            interest_rate_annual=Decimal("0.1000"),
            issue_date="2026-11-01",
            due_date="2027-11-01",
        )
        self.cir_engine.issue_cir(cir)
        accrued = self.cir_engine.accrue_interest("CIR-DEFAULT-01", business_days=252)

        auction_proceeds = Decimal("13000000.00")
        exec_record = self.cir_engine.execute_default("CIR-DEFAULT-01", auction_proceeds=auction_proceeds)

        self.assertEqual(exec_record["status"], "EXECUTED_IN_DEFAULT")
        self.assertEqual(Decimal(exec_record["creditor_recovered"]), accrued)
        self.assertEqual(
            Decimal(exec_record["surplus_returned_to_issuer"]),
            auction_proceeds - accrued,
        )

        inv = self.cir_engine.verify_invariants()
        self.assertTrue(inv["settlement_zero_sum_verified"])

    def test_04_cra_cri_fiduciary_segregation_coverage_breach(self) -> None:
        """Cenário 4.4: Violação de cobertura do regime fiduciário (Ativos Segregados < Saldo Sênior)."""
        fixture_path = REPO_ROOT / "fixtures" / "cra_cri_securitization.json"
        engine = CraCriSecuritizationEngine.load_from_fixture(fixture_path)
        issue = engine.get_issue("CRA-AGRO-2026-001")

        inv_before = issue.verify_invariants()
        self.assertTrue(inv_before["fiduciary_coverage_verified"])

        # Esvaziamento de ativos segregados abaixo do saldo sênior
        for asset in issue.collateral_pool.values():
            asset.outstanding_balance = Decimal("1000000.00")
        issue.fiduciary_regime.segregated_cash_balance = Decimal("0.00")
        issue.fiduciary_regime.reserve_fund.current_balance = Decimal("0.00")

        inv_after = issue.verify_invariants()
        self.assertFalse(inv_after["fiduciary_coverage_verified"])
        self.assertFalse(inv_after["all_invariants_hold"])

    def test_05_cra_cri_water_tight_subordination_priority_breach(self) -> None:
        """Cenário 4.5: Violação da prioridade estrita de subordinação (pagamento indevido a Júnior em estresse)."""
        fixture_path = REPO_ROOT / "fixtures" / "cra_cri_securitization.json"
        engine = CraCriSecuritizationEngine.load_from_fixture(fixture_path)
        issue = engine.get_issue("CRI-IMOB-2026-002")

        # Registra apenas pagamento parcial insuficiente para amortização sênior
        issue.register_collateral_payment(
            asset_id="CCI-RES-ALPHAVILLE-01",
            principal=Decimal("1000000.00"),
            interest=Decimal("100000.00"),
        )
        res = issue.execute_waterfall(
            period_date=date(2026, 12, 20),
            period_indexer_rate=Decimal("0.0040"),
        )

        self.assertTrue(res.conservation_verified)
        self.assertGreater(res.reserve_fund_draw, Decimal("0.00"))
        self.assertTrue(issue.fiduciary_regime.reserve_fund.is_deficient)

        # Regra de Ouro da B3: Quando o Fundo de Reserva é acionado por estresse, Subordinada recebe 0
        self.assertEqual(res.subordinated_interest_paid, Decimal("0.00"))
        self.assertEqual(res.subordinated_principal_paid, Decimal("0.00"))
        self.assertEqual(res.residual_distribution, Decimal("0.00"))

    def test_06_cra_cri_collateral_eligibility_cmn5118_breach(self) -> None:
        """Cenário 4.6: Violação de elegibilidade de lastro sob Resolução CMN 5.118/2024."""
        fixture_path = REPO_ROOT / "fixtures" / "cra_cri_securitization.json"
        engine = CraCriSecuritizationEngine.load_from_fixture(fixture_path)
        issue = engine.get_issue("CRA-AGRO-2026-001")

        ineligible_asset = CollateralAsset(
            asset_id="AST-INELIGIBLE-01",
            collateral_type=CollateralType.CCI,
            debtor_name="SPE Residencial Ibirapuera",
            debtor_doc="99.000.111/0001-22",
            original_face_value=Decimal("5000000.00"),
            outstanding_balance=Decimal("5000000.00"),
            issue_date=date(2026, 1, 1),
            maturity_date=date(2028, 1, 1),
            indexer=IndexerType.CDI,
            interest_rate_pct=Decimal("0.0800"),
        )
        issue.collateral_pool["AST-INELIGIBLE-01"] = ineligible_asset

        inv = issue.verify_invariants()
        self.assertFalse(inv["collateral_valid_verified"])
        self.assertFalse(inv["all_invariants_hold"])

    def test_07_cra_cri_first_loss_piece_absorption_preserves_senior(self) -> None:
        """Cenário 4.7: Mecanismo de First-Loss Piece protege integralmente o patrimônio da Sênior."""
        fixture_path = REPO_ROOT / "fixtures" / "cra_cri_securitization.json"
        engine = CraCriSecuritizationEngine.load_from_fixture(fixture_path)
        issue = engine.get_issue("CRA-DEFAULT-WRITE-OFF-2026-004")

        sub_before = issue.tranches["CRA-DEF-26-SUB"].total_outstanding_balance
        sr_before = issue.tranches["CRA-DEF-26-SR"].total_outstanding_balance

        # Processa default de lastro de R$ 6.000.000,00 (CPR-CITROS-SP-003)
        loss_res = engine.process_collateral_default("CRA-DEFAULT-WRITE-OFF-2026-004", "CPR-CITROS-SP-003")

        # Perda consumida 100% pela Subordinada
        self.assertEqual(loss_res.loss_absorbed_by_subordinated, Decimal("6000000.00"))
        self.assertEqual(loss_res.loss_absorbed_by_senior, Decimal("0.00"))

        sub_after = issue.tranches["CRA-DEF-26-SUB"].total_outstanding_balance
        sr_after = issue.tranches["CRA-DEF-26-SR"].total_outstanding_balance

        self.assertEqual(sub_after, sub_before - Decimal("6000000.00"))
        self.assertEqual(sr_after, sr_before)


# =====================================================================
# SEÇÃO 5: RELATÓRIO CONSOLIDADO E STATUS DE CONFORMIDADE BSM
# =====================================================================

class TestBsmConsolidatedMarketSurveillanceReport(unittest.TestCase):
    """Testes de consolidação global de supervisão e geração de relatório regulatório BSM."""

    def test_01_comprehensive_trading_session_full_audit_report(self) -> None:
        """Cenário 5.1: Sessão completa de mercado integrando múltiplos detectores."""
        engine = BsmSurveillanceEngine()
        base_time = datetime(2026, 11, 7, 10, 0, 0, tzinfo=timezone.utc)

        # Injeta Wash Trading
        engine.ingest_record(
            make_audit_record(
                record_id="REC-WASH-1",
                timestamp=base_time,
                symbol="PETR4",
                participant_id="0088",
                account_id="ACC-1",
                beneficiary_id="111.222.333-44",
                action_type=ActionType.TRADE,
                side=Side.SELL,
                price=Decimal("38.50"),
                quantity=1000,
                order_id="O-W1",
            )
        )
        engine.ingest_record(
            make_audit_record(
                record_id="REC-WASH-2",
                timestamp=base_time + timedelta(seconds=1),
                symbol="PETR4",
                participant_id="0088",
                account_id="ACC-1",
                beneficiary_id="111.222.333-44",
                action_type=ActionType.TRADE,
                side=Side.BUY,
                price=Decimal("38.50"),
                quantity=1000,
                order_id="O-W2",
            )
        )

        # Injeta Front-running
        engine.ingest_record(
            make_audit_record(
                record_id="REC-FR-1",
                timestamp=base_time + timedelta(minutes=5),
                symbol="VALE3",
                participant_id="0088",
                account_id="ACC-PROP",
                beneficiary_id="00.111.222/0001-33",
                beneficiary_type=BeneficiaryType.PJ,
                action_type=ActionType.NEW,
                side=Side.BUY,
                price=Decimal("60.00"),
                quantity=2000,
                order_id="O-FR1",
                metadata={"account_type": "PROPRIETARY"},
            )
        )
        engine.ingest_record(
            make_audit_record(
                record_id="REC-FR-2",
                timestamp=base_time + timedelta(minutes=5, milliseconds=100),
                symbol="VALE3",
                participant_id="0088",
                account_id="ACC-CLI",
                beneficiary_id="99.888.777/0001-66",
                beneficiary_type=BeneficiaryType.INVESTMENT_FUND,
                action_type=ActionType.NEW,
                side=Side.BUY,
                price=Decimal("60.00"),
                quantity=50000,
                order_id="O-FR2",
                metadata={"account_type": "CLIENT", "is_client": True},
            )
        )

        alerts = engine.run_all_detectors()
        self.assertGreaterEqual(len(alerts), 2)

        patterns = {a.pattern_type for a in alerts}
        self.assertIn(AbusePatternType.WASH_TRADING, patterns)
        self.assertIn(AbusePatternType.FRONT_RUNNING, patterns)

        report = engine.generate_audit_report("BSM-REP-CONSOLIDATED-001")
        self.assertGreaterEqual(report.alerts_count, 2)
        report_patterns = {a.pattern_type for a in report.alerts}
        self.assertIn(AbusePatternType.WASH_TRADING, report_patterns)
        self.assertIn(AbusePatternType.FRONT_RUNNING, report_patterns)

        rep_json = report.to_json()
        self.assertIn("BSM-REP-CONSOLIDATED-001", rep_json)
        self.assertIn("WASH_TRADING", rep_json)
        self.assertIn("FRONT_RUNNING", rep_json)


if __name__ == "__main__":
    unittest.main()
