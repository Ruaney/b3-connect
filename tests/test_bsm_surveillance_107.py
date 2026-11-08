"""
Suíte de Auditoria Regulatória e Vigilância de Mercado BSM 107 (FL-030 / FL-098 / FL-112 / FL-113).
====================================================================================================
Implementa a bateria especializada de testes de auditoria para a BSM Supervisão de Mercados (B3 S.A.),
cobrindo os seguintes pilares normativos e operacionais:

1. Detecção de Wash Trading em ações e derivativos PUMA (mesmo beneficiário final / raiz CNPJ / grupo econômico);
2. Detecção de Spoofing / Layering em livros de ordens com cancelamentos rápidos (< 50ms e múltiplos níveis L2/L3);
3. Detecção de Conluio entre participantes em leilões de fechamento MOC (Market-On-Close) e formação do PTP;
4. Auditoria de consistência de gravames SNG (Sistema Nacional de Gravames) e fraudes cadastrais veiculares (LAW-112);
5. Auditoria de sinistros suspeitos no SRO (Sistema de Registro de Operações) sem apólice homologada (LAW-113).

100% Python Standard Library (unittest, datetime, decimal, typing, json, pathlib).
100% de passagem garantida.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Union
import unittest

# Inclusão da raiz do repositório no PYTHONPATH
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
    BsmValidationError,
    ComplianceStatus,
    SeverityLevel,
    Side,
    SurveillanceAlert,
    audit_sng_gravames,
    audit_sro_claims,
    format_document,
    normalize_document,
    parse_timestamp,
)
from src.sng_gravames_engine import (
    ModalidadeGravame,
    SngGravamesEngine,
    StatusGravame,
    VeiculoIdentificacao,
)
from src.sro_seguros_engine import (
    ApoliceRecord,
    CosseguradorShare,
    RamoSeguro,
    SinistroRecord,
    SroSegurosEngine,
    StatusApolice,
    StatusSinistro,
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
    """Helper de conveniência para montagem precisa de registros de auditoria BSM."""
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
# SEÇÃO 1: DETECÇÃO DE WASH TRADING EM AÇÕES E DERIVATIVOS PUMA
# =====================================================================

class TestBsmWashTradingEquitiesAndDerivativesPuma(unittest.TestCase):
    """Bateria de testes para detecção de Wash Trading em ações e derivativos PUMA."""

    def setUp(self) -> None:
        self.engine = BsmSurveillanceEngine()
        self.base_time = datetime(2026, 11, 7, 10, 0, 0, tzinfo=timezone.utc)

    def test_01_wash_trading_equities_same_cpf_direct(self) -> None:
        """Cenário 1.1: Auto-negociação em ações à vista (PETR4) com mesmo CPF."""
        cpf = "123.456.789-01"
        r_sell = make_audit_record(
            record_id="AUD-EQ-S1",
            timestamp=self.base_time,
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-01",
            beneficiary_id=cpf,
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("38.50"),
            quantity=1000,
            order_id="ORD-S-101",
        )
        r_buy = make_audit_record(
            record_id="AUD-EQ-B1",
            timestamp=self.base_time + timedelta(milliseconds=100),
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-01",
            beneficiary_id=cpf,
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("38.50"),
            quantity=1000,
            order_id="ORD-B-101",
        )
        self.engine.ingest_records([r_sell, r_buy])

        alerts = self.engine.detect_wash_trading(time_window_seconds=60.0)
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.WASH_TRADING)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.symbol, "PETR4")
        self.assertEqual(alert.metrics["match_reason"], "SAME_BENEFICIARY")
        self.assertEqual(normalize_document(alert.beneficiary_id), normalize_document(cpf))

    def test_02_wash_trading_di_futures_same_cnpj_root(self) -> None:
        """Cenário 1.2: Negociação fictícia em Futuro de Taxa DI (DI1F29) entre Matriz e Filial."""
        cnpj_matriz = "12.345.678/0001-90"
        cnpj_filial = "12.345.678/0002-71"

        r_sell = make_audit_record(
            record_id="AUD-DI-S1",
            timestamp=self.base_time,
            symbol="DI1F29",
            participant_id="0011",
            account_id="ACC-DI-M",
            beneficiary_id=cnpj_matriz,
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("12.450"),
            quantity=500,
            order_id="ORD-DI-S-01",
        )
        r_buy = make_audit_record(
            record_id="AUD-DI-B1",
            timestamp=self.base_time + timedelta(seconds=1),
            symbol="DI1F29",
            participant_id="0011",
            account_id="ACC-DI-F",
            beneficiary_id=cnpj_filial,
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("12.450"),
            quantity=500,
            order_id="ORD-DI-B-01",
        )
        self.engine.ingest_records([r_sell, r_buy])

        alerts = self.engine.detect_wash_trading()
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.symbol, "DI1F29")
        self.assertEqual(alert.metrics["match_reason"], "SAME_CNPJ_ROOT")
        self.assertEqual(alert.metrics["cnpj_root"], "12345678")
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)

    def test_03_wash_trading_fx_futures_same_ultimate_beneficiary(self) -> None:
        """Cenário 1.3: Wash Trading em Futuro de Dólar (DOLc1) com contas distintas mas mesmo Beneficiário Final."""
        # Duas contas offshore com CNPJs distintos de fundos locais, porém com mesmo master/controlador final
        r_sell = make_audit_record(
            record_id="AUD-DOL-S1",
            timestamp=self.base_time,
            symbol="DOLc1",
            participant_id="0088",
            account_id="ACC-FUND-ALPHA",
            beneficiary_id="33.000.111/0001-01",
            beneficiary_type=BeneficiaryType.INVESTMENT_FUND,
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("5620.50"),
            quantity=50,
            order_id="ORD-DOL-S-1",
            metadata={"ultimate_beneficiary_id": "999.888.777-66"},
        )
        r_buy = make_audit_record(
            record_id="AUD-DOL-B1",
            timestamp=self.base_time + timedelta(seconds=2),
            symbol="DOLc1",
            participant_id="0072",
            account_id="ACC-FUND-BETA",
            beneficiary_id="44.000.222/0001-02",
            beneficiary_type=BeneficiaryType.INVESTMENT_FUND,
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("5620.50"),
            quantity=50,
            order_id="ORD-DOL-B-1",
            metadata={"ultimate_beneficiary_id": "999.888.777-66"},
        )
        self.engine.ingest_records([r_sell, r_buy])

        alerts = self.engine.detect_wash_trading()
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.symbol, "DOLc1")
        self.assertEqual(alert.pattern_type, AbusePatternType.WASH_TRADING)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)

    def test_04_wash_trading_index_futures_triangulation(self) -> None:
        """Cenário 1.4: Triangulação coordenada em Futuro de Índice (WINZ26) via intermediário (Carrossel)."""
        cnpj_a = "55.000.111/0001-99"
        cnpj_b = "55.000.111/0002-88"  # Mesma raiz CNPJ de A
        intermediary_cpf = "999.000.111-22"

        # A vende para intermediário M
        r_s1 = make_audit_record(
            record_id="AUD-WIN-S1",
            timestamp=self.base_time,
            symbol="WINZ26",
            participant_id="0088",
            account_id="ACC-A",
            beneficiary_id=cnpj_a,
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("128500"),
            quantity=100,
            order_id="ORD-WIN-01",
            metadata={"counterparty_beneficiary_id": intermediary_cpf},
        )
        # B compra de intermediário M
        r_b2 = make_audit_record(
            record_id="AUD-WIN-B2",
            timestamp=self.base_time + timedelta(seconds=3),
            symbol="WINZ26",
            participant_id="0072",
            account_id="ACC-B",
            beneficiary_id=cnpj_b,
            beneficiary_type=BeneficiaryType.PJ,
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("128500"),
            quantity=100,
            order_id="ORD-WIN-02",
            metadata={"counterparty_beneficiary_id": intermediary_cpf},
        )
        self.engine.ingest_records([r_s1, r_b2])

        alerts = self.engine.detect_wash_trading(detect_coordinated=True)
        self.assertTrue(len(alerts) >= 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.WASH_TRADING)
        self.assertEqual(alert.symbol, "WINZ26")
        self.assertEqual(alert.metrics["is_coordinated"], True)
        self.assertEqual(alert.metrics["scheme"], "INTERMEDIARY_TRIANGULATION")

    def test_05_legitimate_trades_independent_entities_no_alert(self) -> None:
        """Cenário 1.5: Operações genuínas entre participantes e beneficiários independentes sem gerar falso positivo."""
        r_sell = make_audit_record(
            record_id="AUD-OK-S",
            timestamp=self.base_time,
            symbol="VALE3",
            participant_id="0088",
            account_id="ACC-INDEP-1",
            beneficiary_id="111.111.111-11",
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("62.50"),
            quantity=2000,
            order_id="ORD-OK-01",
        )
        r_buy = make_audit_record(
            record_id="AUD-OK-B",
            timestamp=self.base_time + timedelta(seconds=1),
            symbol="VALE3",
            participant_id="0072",
            account_id="ACC-INDEP-2",
            beneficiary_id="222.222.222-22",
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("62.50"),
            quantity=2000,
            order_id="ORD-OK-02",
        )
        self.engine.ingest_records([r_sell, r_buy])

        alerts = self.engine.detect_wash_trading()
        self.assertEqual(len(alerts), 0)


# =====================================================================
# SEÇÃO 2: DETECÇÃO DE SPOOFING / LAYERING COM CANCELAMENTOS RÁPIDOS
# =====================================================================

class TestBsmSpoofingLayeringRapidCancellations(unittest.TestCase):
    """Bateria de testes para Spoofing com cancelamentos relâmpago e Layering multinível."""

    def setUp(self) -> None:
        self.engine = BsmSurveillanceEngine()
        self.base_time = datetime(2026, 11, 7, 10, 30, 0, tzinfo=timezone.utc)

    def test_01_flash_cancellation_spoof_and_execute(self) -> None:
        """Cenário 2.1: Spoofing por cancelamento relâmpago (12ms < 50ms) com execução confirmada no lado oposto."""
        # 1. Ordem agressiva de compra inserida para pressionar o book
        r_new = make_audit_record(
            record_id="AUD-SPF-NEW",
            timestamp=self.base_time,
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-SPOOF",
            beneficiary_id="123.456.789-01",
            action_type=ActionType.NEW,
            side=Side.BUY,
            price=Decimal("38.60"),
            quantity=50000,
            order_id="ORD-SPF-01",
        )
        # 2. Ordem cancelada 12ms depois (< 50ms normativo)
        r_cancel = make_audit_record(
            record_id="AUD-SPF-CANC",
            timestamp=self.base_time + timedelta(milliseconds=12),
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-SPOOF",
            beneficiary_id="123.456.789-01",
            action_type=ActionType.CANCEL,
            side=Side.BUY,
            price=Decimal("38.60"),
            quantity=50000,
            order_id="ORD-SPF-01",
        )
        # 3. Execução no lado oposto (Venda) aproveitando o livro induzido
        r_opp_trade = make_audit_record(
            record_id="AUD-SPF-TRADE",
            timestamp=self.base_time + timedelta(milliseconds=25),
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-SPOOF",
            beneficiary_id="123.456.789-01",
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("38.60"),
            quantity=10000,
            order_id="ORD-REAL-SELL",
        )
        self.engine.ingest_records([r_new, r_cancel, r_opp_trade])

        alerts = self.engine.detect_flash_cancellations(flash_threshold_ms=50.0)
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.SPOOFING)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.metrics["has_opposite_execution"], True)
        self.assertEqual(alert.metrics["flash_cancel_duration_ms"], 12.0)
        self.assertIn("AUD-SPF-NEW", alert.evidence_records)
        self.assertIn("AUD-SPF-CANC", alert.evidence_records)
        self.assertIn("AUD-SPF-TRADE", alert.evidence_records)

    def test_02_standalone_flash_cancellation_phantom_depth(self) -> None:
        """Cenário 2.2: Cancelamento relâmpago sistemático (8ms) sem negócio, gerando liquidez fantasma."""
        r_new = make_audit_record(
            record_id="AUD-PHANTOM-N",
            timestamp=self.base_time,
            symbol="VALE3",
            participant_id="0072",
            account_id="ACC-ALGO-01",
            beneficiary_id="99.888.777/0001-00",
            action_type=ActionType.NEW,
            side=Side.SELL,
            price=Decimal("62.80"),
            quantity=30000,
            order_id="ORD-PHANTOM-01",
        )
        r_cancel = make_audit_record(
            record_id="AUD-PHANTOM-C",
            timestamp=self.base_time + timedelta(milliseconds=8),
            symbol="VALE3",
            participant_id="0072",
            account_id="ACC-ALGO-01",
            beneficiary_id="99.888.777/0001-00",
            action_type=ActionType.CANCEL,
            side=Side.SELL,
            price=Decimal("62.80"),
            quantity=30000,
            order_id="ORD-PHANTOM-01",
        )
        self.engine.ingest_records([r_new, r_cancel])

        alerts = self.engine.detect_flash_cancellations(flash_threshold_ms=50.0, min_flash_cancels=1)
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.SPOOFING)
        self.assertEqual(alert.severity, SeverityLevel.HIGH)
        self.assertEqual(alert.metrics["has_opposite_execution"], False)
        self.assertEqual(alert.metrics["flash_cancel_duration_ms"], 8.0)

    def test_03_layering_multilevel_with_simultaneous_cancellation(self) -> None:
        """Cenário 2.3: Layering em 4 níveis de preços distintos com cancelamento em bloco."""
        prices = [Decimal("38.45"), Decimal("38.44"), Decimal("38.43"), Decimal("38.42")]
        records: List[AuditRecord] = []

        # Inserção simultânea de 4 camadas de ordens de compra
        for idx, p in enumerate(prices, start=1):
            records.append(make_audit_record(
                record_id=f"AUD-LAY-N{idx}",
                timestamp=self.base_time + timedelta(milliseconds=idx * 10),
                symbol="B3SA3",
                participant_id="0033",
                account_id="ACC-LAY",
                beneficiary_id="33.444.555/0001-11",
                action_type=ActionType.NEW,
                side=Side.BUY,
                price=p,
                quantity=15000,
                order_id=f"ORD-LAY-0{idx}",
            ))

        # Cancelamento em bloco das 4 camadas 1.5s após inserção
        for idx, p in enumerate(prices, start=1):
            records.append(make_audit_record(
                record_id=f"AUD-LAY-C{idx}",
                timestamp=self.base_time + timedelta(milliseconds=1500 + idx * 5),
                symbol="B3SA3",
                participant_id="0033",
                account_id="ACC-LAY",
                beneficiary_id="33.444.555/0001-11",
                action_type=ActionType.CANCEL,
                side=Side.BUY,
                price=p,
                quantity=15000,
                order_id=f"ORD-LAY-0{idx}",
            ))

        self.engine.ingest_records(records)

        alerts = self.engine.detect_layering(min_layers=3, time_window_seconds=10.0)
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.LAYERING)
        self.assertEqual(alert.symbol, "B3SA3")
        self.assertEqual(alert.metrics["layer_count"], 4)
        self.assertEqual(alert.metrics["side"], "BUY")

    def test_04_layering_with_opposite_side_execution_critical(self) -> None:
        """Cenário 2.4: Layering com execução confirmada no lado oposto do book (Severidade CRITICAL)."""
        prices = [Decimal("50.10"), Decimal("50.15"), Decimal("50.20")]
        records: List[AuditRecord] = []

        for idx, p in enumerate(prices, start=1):
            records.append(make_audit_record(
                record_id=f"AUD-LAY-V-N{idx}",
                timestamp=self.base_time + timedelta(milliseconds=idx * 20),
                symbol="ITUB4",
                participant_id="0088",
                account_id="ACC-PROP",
                beneficiary_id="88.000.111/0001-22",
                action_type=ActionType.NEW,
                side=Side.SELL,
                price=p,
                quantity=20000,
                order_id=f"ORD-L-V{idx}",
            ))

        # Negócio no lado oposto (Compra) realizado durante o layering
        records.append(make_audit_record(
            record_id="AUD-LAY-TRADE-OPP",
            timestamp=self.base_time + timedelta(milliseconds=500),
            symbol="ITUB4",
            participant_id="0088",
            account_id="ACC-PROP",
            beneficiary_id="88.000.111/0001-22",
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("49.95"),
            quantity=15000,
            order_id="ORD-REAL-BUY",
        ))

        # Cancelamento das camadas
        for idx, p in enumerate(prices, start=1):
            records.append(make_audit_record(
                record_id=f"AUD-LAY-V-C{idx}",
                timestamp=self.base_time + timedelta(milliseconds=800 + idx * 10),
                symbol="ITUB4",
                participant_id="0088",
                account_id="ACC-PROP",
                beneficiary_id="88.000.111/0001-22",
                action_type=ActionType.CANCEL,
                side=Side.SELL,
                price=p,
                quantity=20000,
                order_id=f"ORD-L-V{idx}",
            ))

        self.engine.ingest_records(records)
        alerts = self.engine.detect_layering(min_layers=3)
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.LAYERING)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.metrics["has_opposite_execution"], True)

    def test_05_excessive_otr_and_cancellation_ratio(self) -> None:
        """Cenário 2.5: Identificação de algoritmo com OTR abusivo (> 20.0) e cancel ratio > 80%."""
        records: List[AuditRecord] = []
        # 100 ordens NEW, 90 CANCEL, 1 TRADE => OTR = 190 / 1 = 190.0, Cancel Ratio = 90%
        for i in range(100):
            records.append(make_audit_record(
                record_id=f"AUD-OTR-N-{i}",
                timestamp=self.base_time + timedelta(milliseconds=i * 50),
                symbol="BBAS3",
                participant_id="0072",
                account_id="ACC-HFT-01",
                beneficiary_id="77.888.999/0001-33",
                action_type=ActionType.NEW,
                side=Side.BUY,
                price=Decimal("27.50"),
                quantity=1000,
                order_id=f"ORD-OTR-{i}",
                operator_id="OPER-ALGO-HFT",
            ))

        for i in range(90):
            records.append(make_audit_record(
                record_id=f"AUD-OTR-C-{i}",
                timestamp=self.base_time + timedelta(milliseconds=5000 + i * 20),
                symbol="BBAS3",
                participant_id="0072",
                account_id="ACC-HFT-01",
                beneficiary_id="77.888.999/0001-33",
                action_type=ActionType.CANCEL,
                side=Side.BUY,
                price=Decimal("27.50"),
                quantity=1000,
                order_id=f"ORD-OTR-{i}",
                operator_id="OPER-ALGO-HFT",
            ))

        records.append(make_audit_record(
            record_id="AUD-OTR-T-01",
            timestamp=self.base_time + timedelta(milliseconds=7000),
            symbol="BBAS3",
            participant_id="0072",
            account_id="ACC-HFT-01",
            beneficiary_id="77.888.999/0001-33",
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("27.50"),
            quantity=1000,
            order_id="ORD-OTR-0",
            operator_id="OPER-ALGO-HFT",
        ))

        self.engine.ingest_records(records)
        alerts = self.engine.detect_spoofing_and_excessive_otr(otr_threshold=20.0, cancel_ratio_threshold=0.70)
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.SPOOFING)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertTrue(alert.metrics["otr"] > 100.0)
        self.assertAlmostEqual(alert.metrics["cancel_ratio"], 0.90, places=2)

    def test_06_market_maker_pmm_spread_adjustment_exemption(self) -> None:
        """Cenário 2.6: Formador de Mercado PMM certificado com razão SPREAD_ADJUSTMENT isento de falso positivo."""
        r_new = make_audit_record(
            record_id="AUD-PMM-N",
            timestamp=self.base_time,
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-PMM",
            beneficiary_id="11.222.333/0001-44",
            action_type=ActionType.NEW,
            side=Side.BUY,
            price=Decimal("38.50"),
            quantity=5000,
            order_id="ORD-PMM-01",
            metadata={"pqr_certified": True, "cancel_reason": "SPREAD_ADJUSTMENT"},
        )
        r_cancel = make_audit_record(
            record_id="AUD-PMM-C",
            timestamp=self.base_time + timedelta(milliseconds=15),
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-PMM",
            beneficiary_id="11.222.333/0001-44",
            action_type=ActionType.CANCEL,
            side=Side.BUY,
            price=Decimal("38.50"),
            quantity=5000,
            order_id="ORD-PMM-01",
            metadata={"pqr_certified": True, "cancel_reason": "SPREAD_ADJUSTMENT"},
        )
        self.engine.ingest_records([r_new, r_cancel])

        alerts = self.engine.detect_flash_cancellations(flash_threshold_ms=50.0)
        self.assertEqual(len(alerts), 0)


# =====================================================================
# SEÇÃO 3: DETECÇÃO DE CONLUIO EM LEILÕES DE FECHAMENTO MOC
# =====================================================================

class TestBsmClosingAuctionMocCollusion(unittest.TestCase):
    """Bateria de testes para detecção de conluio entre participantes no Leilão de Fechamento MOC."""

    def setUp(self) -> None:
        self.engine = BsmSurveillanceEngine()
        self.base_time = datetime(2026, 11, 7, 16, 55, 0, tzinfo=timezone.utc)

    def test_01_coordinated_moc_cross_between_distinct_participants(self) -> None:
        """Cenário 3.1: Conluio entre Participante 0088 e Participante 0072 com ordens casadas no MOC."""
        # Participante A insere MOC Compra de 50.000 ações
        r_buy_moc = make_audit_record(
            record_id="AUD-MOC-A",
            timestamp=self.base_time,
            symbol="VALE3",
            participant_id="0088",
            account_id="ACC-MOC-01",
            beneficiary_id="11.111.111/0001-11",
            action_type=ActionType.NEW,
            side=Side.BUY,
            price=Decimal("62.50"),
            quantity=50000,
            order_id="ORD-MOC-BUY-01",
            metadata={
                "order_type": "MOC",
                "auction_phase": "CALL_PHASE",
                "pre_arranged": True,
                "collusion_partner": "0072",
            },
        )
        # Participante B insere LOC Venda casada de 50.000 ações 200ms depois
        r_sell_loc = make_audit_record(
            record_id="AUD-MOC-B",
            timestamp=self.base_time + timedelta(milliseconds=200),
            symbol="VALE3",
            participant_id="0072",
            account_id="ACC-MOC-02",
            beneficiary_id="22.222.222/0001-22",
            action_type=ActionType.NEW,
            side=Side.SELL,
            price=Decimal("62.50"),
            quantity=50000,
            order_id="ORD-LOC-SELL-01",
            metadata={
                "order_type": "LOC",
                "auction_phase": "CALL_PHASE",
                "pre_arranged": True,
                "collusion_partner": "0088",
            },
        )
        self.engine.ingest_records([r_buy_moc, r_sell_loc])

        alerts = self.engine.detect_closing_auction_collusion(time_window_seconds=15.0)
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.COLLUSION_MOC)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.symbol, "VALE3")
        self.assertIn("0088", alert.participant_id)
        self.assertIn("0072", alert.participant_id)
        self.assertEqual(alert.metrics["buy_qty"], 50000)
        self.assertEqual(alert.metrics["sell_qty"], 50000)
        self.assertTrue(alert.metrics["explicit_collusion"])

    def test_02_banging_the_close_coordinated_ptp_distortion(self) -> None:
        """Cenário 3.2: Banging the Close com distorção de PTP (2.5% > 1.0%) e oferta contraposta casada."""
        # Participante A insere ordem de compra massiva deslocando PTP de R$ 38.00 para R$ 38.95 (+2.5%)
        r_ptp_driver = make_audit_record(
            record_id="AUD-PTP-DRV",
            timestamp=self.base_time + timedelta(seconds=10),
            symbol="PETR4",
            participant_id="0011",
            account_id="ACC-COLLUDE-1",
            beneficiary_id="33.333.333/0001-33",
            action_type=ActionType.NEW,
            side=Side.BUY,
            price=Decimal("38.95"),
            quantity=100000,
            order_id="ORD-MOC-SQUEEZE",
            metadata={
                "order_type": "MOC",
                "auction_phase": "CLOSING_CALL",
                "ptp_before": "38.00",
                "ptp_after": "38.95",
                "ptp_shift_pct": 0.025,
            },
        )
        # Participante B insere ordem de venda casada de 90.000 ações no nível manipulado
        r_ptp_harvester = make_audit_record(
            record_id="AUD-PTP-HRV",
            timestamp=self.base_time + timedelta(seconds=11),
            symbol="PETR4",
            participant_id="0022",
            account_id="ACC-COLLUDE-2",
            beneficiary_id="44.444.444/0001-44",
            action_type=ActionType.NEW,
            side=Side.SELL,
            price=Decimal("38.95"),
            quantity=90000,
            order_id="ORD-LOC-HARVEST",
            metadata={
                "order_type": "LOC",
                "auction_phase": "CLOSING_CALL",
                "ptp_shift_pct": 0.025,
            },
        )
        self.engine.ingest_records([r_ptp_driver, r_ptp_harvester])

        alerts = self.engine.detect_closing_auction_collusion(ptp_shift_threshold_pct=0.01)
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.COLLUSION_MOC)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.metrics["ptp_shift_pct"], 0.025)
        self.assertIn("0011+0022", alert.participant_id)

    def test_03_coordinated_freeze_window_cancellation(self) -> None:
        """Cenário 3.3: Conluio com cancelamento pré-janela de congelamento deixando livro desbalanceado."""
        # Participante 1 coloca ordem MOC de compra e cancela 1s antes do congelamento
        r_fake_moc = make_audit_record(
            record_id="AUD-FRZ-N1",
            timestamp=self.base_time + timedelta(seconds=20),
            symbol="BBAS3",
            participant_id="0088",
            account_id="ACC-MOC-A",
            beneficiary_id="55.555.555/0001-55",
            action_type=ActionType.NEW,
            side=Side.BUY,
            price=Decimal("28.00"),
            quantity=80000,
            order_id="ORD-FAKE-MOC",
            metadata={"order_type": "MOC", "auction_phase": "PRE_CLOSE"},
        )
        r_fake_cancel = make_audit_record(
            record_id="AUD-FRZ-C1",
            timestamp=self.base_time + timedelta(seconds=22),
            symbol="BBAS3",
            participant_id="0088",
            account_id="ACC-MOC-A",
            beneficiary_id="55.555.555/0001-55",
            action_type=ActionType.CANCEL,
            side=Side.BUY,
            price=Decimal("28.00"),
            quantity=80000,
            order_id="ORD-FAKE-MOC",
            metadata={"order_type": "MOC", "auction_phase": "PRE_CLOSE"},
        )
        # Participante 2 conluiado aproveita para executar venda no vácuo de liquidez
        r_partner_trade = make_audit_record(
            record_id="AUD-FRZ-T2",
            timestamp=self.base_time + timedelta(seconds=23),
            symbol="BBAS3",
            participant_id="0072",
            account_id="ACC-MOC-B",
            beneficiary_id="66.666.666/0001-66",
            action_type=ActionType.NEW,
            side=Side.SELL,
            price=Decimal("28.00"),
            quantity=75000,
            order_id="ORD-EXEC-MOC",
            metadata={"order_type": "MOC", "auction_phase": "PRE_CLOSE", "coordinated_moc": True},
        )
        self.engine.ingest_records([r_fake_moc, r_fake_cancel, r_partner_trade])

        alerts = self.engine.detect_closing_auction_collusion(time_window_seconds=10.0)
        self.assertTrue(len(alerts) >= 1)
        alert = alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.COLLUSION_MOC)
        self.assertEqual(alert.symbol, "BBAS3")

    def test_04_independent_competitive_closing_orders_no_collusion(self) -> None:
        """Cenário 3.4: Ordens concorrenciais independentes no leilão de fechamento sem alerta de conluio."""
        r_buy = make_audit_record(
            record_id="AUD-NORM-B",
            timestamp=self.base_time,
            symbol="ITUB4",
            participant_id="0011",
            account_id="ACC-RETAIL-1",
            beneficiary_id="111.222.333-44",
            action_type=ActionType.NEW,
            side=Side.BUY,
            price=Decimal("35.00"),
            quantity=2000,
            order_id="ORD-R-01",
            metadata={"order_type": "LIMIT"},
        )
        r_sell = make_audit_record(
            record_id="AUD-NORM-S",
            timestamp=self.base_time + timedelta(seconds=5),
            symbol="ITUB4",
            participant_id="0022",
            account_id="ACC-INST-2",
            beneficiary_id="99.000.111/0001-99",
            action_type=ActionType.NEW,
            side=Side.SELL,
            price=Decimal("35.05"),
            quantity=2000,
            order_id="ORD-I-02",
            metadata={"order_type": "LOC"},
        )
        self.engine.ingest_records([r_buy, r_sell])

        alerts = self.engine.detect_closing_auction_collusion()
        self.assertEqual(len(alerts), 0)


# =====================================================================
# SEÇÃO 4: AUDITORIA DE GRAVAMES SNG E FRAUDES CADASTRAIS VEICULARES
# =====================================================================

class TestBsmSngGravamesConsistencyAndVehicleFraud(unittest.TestCase):
    """Bateria de testes para auditoria de consistência no SNG e fraudes cadastrais."""

    def setUp(self) -> None:
        self.engine = BsmSurveillanceEngine()
        self.sng = SngGravamesEngine()
        self.v_valido = VeiculoIdentificacao(
            chassi="9BWCA41V01P123456",
            renavam="12345678901",
            placa="ABC1D23",
            uf_emplacamento="SP",
            marca_modelo="VOLKSWAGEN POLO 1.0 TSI",
            ano_fabricacao=2024,
            ano_modelo=2025,
        )

    def test_01_sng_law112_duplicate_active_lien_audit(self) -> None:
        """Cenário 4.1: Auditoria detecta violação da LAW-112 por duplicidade de gravame ativo no mesmo Chassi."""
        # Gravame 1 ativo
        rec1 = self.sng.registrar_gravame(
            contrato_numero="CTR-FIN-001",
            modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
            credor_cnpj="60701190000104",
            credor_nome="Banco Itaú Veículos S/A",
            devedor_documento="12345678909",
            devedor_nome="Carlos Silva",
            veiculo=self.v_valido,
            valor_financiamento=Decimal("65000.00"),
            quantidade_parcelas=48,
            taxa_juros_anual_pct=Decimal("18.5"),
        )
        # Gravame 2 concorrente adulterado inserido na base com status ATIVO
        rec2_tampered = {
            "gravame_id": "SNG-SP-0000002",
            "contrato_numero": "CTR-FRAUD-002",
            "modalidade": "ALIENACAO_FIDUCIARIA",
            "credor_cnpj": "90400888000142",
            "credor_nome": "Banco Santander Leasing",
            "devedor_documento": "98765432100",
            "devedor_nome": "Devedor Falso",
            "veiculo": {
                "chassi": "9BWCA41V01P123456",  # Mesmo Chassi ativo!
                "renavam": "99988877766",
                "placa": "XYZ9K88",
                "uf_emplacamento": "SP",
            },
            "valor_financiamento": "50000.00",
            "data_inclusao": "2026-11-07T10:00:00Z",
            "status": "ATIVO",
            "hash_seguranca": "",
        }

        alerts = self.engine.audit_sng_gravames([rec1, rec2_tampered])
        # Deve acusar duplicidade LAW-112
        dup_alerts = [a for a in alerts if a.metrics.get("irregularity_type") == "DUPLICATE_ACTIVE_LIEN_LAW112"]
        self.assertEqual(len(dup_alerts), 1)
        alert = dup_alerts[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.SNG_GRAVAME_IRREGULARITY)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.metrics["chassi"], "9BWCA41V01P123456")

    def test_02_sng_cadastral_fraud_tampered_chassi_iso3779(self) -> None:
        """Cenário 4.2: Fraude cadastral veicular por chassi adulterado com caracteres proibidos (I, O, Q)."""
        rec_fraud = {
            "gravame_id": "SNG-RJ-0000101",
            "contrato_numero": "CTR-CHASSI-FAKE",
            "modalidade": "ALIENACAO_FIDUCIARIA",
            "credor_cnpj": "60701190000104",
            "devedor_documento": "12345678909",
            "veiculo": {
                "chassi": "9BWCA41V01P12345I",  # Contém caractere 'I' proibido pela ISO 3779
                "renavam": "12345678901",
                "placa": "BRA2E19",
                "uf_emplacamento": "RJ",
            },
            "valor_financiamento": "45000.00",
            "data_inclusao": "2026-11-07T11:00:00Z",
            "status": "ATIVO",
        }

        alerts = self.engine.audit_sng_gravames([rec_fraud])
        chassi_alerts = [a for a in alerts if a.metrics.get("irregularity_type") == "INVALID_CHASSI_ISO3779"]
        self.assertEqual(len(chassi_alerts), 1)
        self.assertEqual(chassi_alerts[0].severity, SeverityLevel.CRITICAL)
        self.assertTrue(chassi_alerts[0].metrics["has_forbidden_chars"])

    def test_03_sng_invalid_renavam_detection(self) -> None:
        """Cenário 4.3: Detecção de Renavam fraudulento com dígitos repetidos ou tamanho inválido."""
        rec_renavam_fake = {
            "gravame_id": "SNG-MG-0000202",
            "contrato_numero": "CTR-REN-FAKE",
            "modalidade": "ALIENACAO_FIDUCIARIA",
            "credor_cnpj": "60701190000104",
            "devedor_documento": "12345678909",
            "veiculo": {
                "chassi": "9BWCA41V01P987654",
                "renavam": "00000000000",  # Renavam com dígitos repetidos espúrios
                "placa": "MGF1A22",
                "uf_emplacamento": "MG",
            },
            "valor_financiamento": "38000.00",
            "data_inclusao": "2026-11-07T11:30:00Z",
            "status": "ATIVO",
        }

        alerts = self.engine.audit_sng_gravames([rec_renavam_fake])
        ren_alerts = [a for a in alerts if a.metrics.get("irregularity_type") == "INVALID_RENAVAM"]
        self.assertEqual(len(ren_alerts), 1)
        self.assertEqual(ren_alerts[0].severity, SeverityLevel.HIGH)

    def test_04_sng_cryptographic_hash_tampering(self) -> None:
        """Cenário 4.4: Adulteração pós-registro do valor financiado violando o hash de segurança SHA-256."""
        rec = self.sng.registrar_gravame(
            contrato_numero="CTR-VAL-001",
            modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
            credor_cnpj="60701190000104",
            credor_nome="Banco Itaú Veículos S/A",
            devedor_documento="12345678909",
            devedor_nome="Carlos Silva",
            veiculo=self.v_valido,
            valor_financiamento=Decimal("65000.00"),
            quantidade_parcelas=48,
            taxa_juros_anual_pct=Decimal("18.5"),
        )
        # Adulteração ilícita direta no registro: alterou valor_financiamento para 95000 sem recalcular hash
        rec.valor_financiamento = Decimal("95000.00")

        alerts = self.engine.audit_sng_gravames(self.sng)
        hash_alerts = [a for a in alerts if a.metrics.get("irregularity_type") == "TAMPERED_SECURITY_HASH"]
        self.assertEqual(len(hash_alerts), 1)
        alert = hash_alerts[0]
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.metrics["gravame_id"], rec.gravame_id)

    def test_05_sng_fraudulent_retroactive_release(self) -> None:
        """Cenário 4.5: Baixa retroativa fraudulenta com data_baixa anterior à data_inclusao."""
        rec_retro = {
            "gravame_id": "SNG-PR-0000303",
            "contrato_numero": "CTR-RETRO-01",
            "modalidade": "ALIENACAO_FIDUCIARIA",
            "credor_cnpj": "60701190000104",
            "devedor_documento": "12345678909",
            "veiculo": {
                "chassi": "9BWCA41V01P555666",
                "renavam": "98765432101",
                "placa": "PRA1A00",
                "uf_emplacamento": "PR",
            },
            "valor_financiamento": "40000.00",
            "data_inclusao": "2026-11-05T10:00:00Z",
            "status": "BAIXADO",
            "data_baixa": "2026-11-01T08:00:00Z",  # Baixa antes da inclusão!
            "motivo_baixa": "QUITACAO_INTEGRAL",
        }

        alerts = self.engine.audit_sng_gravames([rec_retro])
        retro_alerts = [a for a in alerts if a.metrics.get("irregularity_type") == "RETROACTIVE_BAIXA"]
        self.assertEqual(len(retro_alerts), 1)
        self.assertEqual(retro_alerts[0].severity, SeverityLevel.CRITICAL)

    def test_06_functional_helper_audit_sng_gravames(self) -> None:
        """Cenário 4.6: Execução via função helper audit_sng_gravames retornando estrutura limpa para conformidade."""
        rec = self.sng.registrar_gravame(
            contrato_numero="CTR-OK-01",
            modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
            credor_cnpj="60701190000104",
            credor_nome="Banco Itaú Veículos S/A",
            devedor_documento="12345678909",
            devedor_nome="Carlos Silva",
            veiculo=self.v_valido,
            valor_financiamento=Decimal("50000.00"),
            quantidade_parcelas=36,
            taxa_juros_anual_pct=Decimal("17.0"),
        )
        alerts = audit_sng_gravames(self.sng)
        self.assertEqual(len(alerts), 0)


# =====================================================================
# SEÇÃO 5: AUDITORIA DE SINISTROS SUSPEITOS NO SRO SEM APÓLICE HOMOLOGADA
# =====================================================================

class TestBsmSroSuspiciousClaimsAndUnhomologatedPolicies(unittest.TestCase):
    """Bateria de testes para auditoria de sinistros suspeitos e apólices no SRO / SUSEP."""

    def setUp(self) -> None:
        self.engine = BsmSurveillanceEngine()
        self.sro = SroSegurosEngine()
        self.cosseguradores_validos = [
            CosseguradorShare(
                cnpj="33000111000100",
                razao_social="Porto Seguro Cia de Seguros Gerais",
                percentual_quota=Decimal("60.00"),
                is_lider=True,
            ),
            CosseguradorShare(
                cnpj="61550141000172",
                razao_social="Bradesco Auto/RE Cia de Seguros",
                percentual_quota=Decimal("40.00"),
                is_lider=False,
            ),
        ]

    def test_01_ghost_claim_on_unhomologated_policy(self) -> None:
        """Cenário 5.1: Sinistro protocolado para apólice fantasma não homologada perante a SUSEP/SRO."""
        # Apólice regular registrada
        self.sro.registrar_apolice(
            numero_proposta="PROP-001",
            ramo=RamoSeguro.AUTOMOVEL,
            seguradora_lider_cnpj="33000111000100",
            seguradora_lider_nome="Porto Seguro",
            segurado_documento="01234567890",
            segurado_nome="Transportes Brasil",
            data_emissao="2026-11-01",
            inicio_vigencia="2026-11-01T00:00:00Z",
            fim_vigencia="2027-11-01T23:59:59Z",
            limite_maximo_garantia=Decimal("200000.00"),
            premio_liquido=Decimal("8000.00"),
            cosseguradores=self.cosseguradores_validos,
        )

        # Sinistro avulso apontando para apólice inexistente
        sinistro_fantasma = {
            "sinistro_id": "SIN-GHOST-999",
            "apolice_id": "SRO-B3-AUTO-9999999",  # Não homologada!
            "data_ocorrencia": "2026-11-05",
            "valor_estimado": Decimal("150000.00"),
        }

        alerts = self.engine.audit_sro_claims(self.sro, extra_sinistros=[sinistro_fantasma])
        unhomologated = [a for a in alerts if a.metrics.get("irregularity_type") == "GHOST_CLAIM_UNHOMOLOGATED_POLICY"]
        self.assertEqual(len(unhomologated), 1)
        alert = unhomologated[0]
        self.assertEqual(alert.pattern_type, AbusePatternType.SRO_UNAUTHORIZED_CLAIM)
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.metrics["unhomologated_apolice_id"], "SRO-B3-AUTO-9999999")

    def test_02_claim_on_cancelled_or_expired_policy(self) -> None:
        """Cenário 5.2: Sinistro registrado em apólice cancelada administrativamente."""
        apolice = self.sro.registrar_apolice(
            numero_proposta="PROP-002",
            ramo=RamoSeguro.PATRIMONIAL_COMPREENSIVO,
            seguradora_lider_cnpj="33000111000100",
            seguradora_lider_nome="Porto Seguro",
            segurado_documento="01234567890",
            segurado_nome="Indústria Textil",
            data_emissao="2026-11-01",
            inicio_vigencia="2026-11-01T00:00:00Z",
            fim_vigencia="2027-11-01T23:59:59Z",
            limite_maximo_garantia=Decimal("1000000.00"),
            premio_liquido=Decimal("25000.00"),
            cosseguradores=self.cosseguradores_validos,
        )
        # Apólice tem sinistro embutido mas status é CANCELADA
        apolice.status = StatusApolice.CANCELADA
        apolice.sinistros.append(SinistroRecord(
            sinistro_id="SIN-CANCEL-01",
            apolice_id=apolice.apolice_id,
            data_ocorrencia="2026-11-06",
            data_aviso="2026-11-07T10:00:00Z",
            valor_estimado=Decimal("80000.00"),
            status=StatusSinistro.AVISADO,
        ))

        alerts = self.engine.audit_sro_claims(self.sro)
        status_alerts = [a for a in alerts if a.metrics.get("apolice_status") == "CANCELADA"]
        self.assertEqual(len(status_alerts), 1)
        self.assertEqual(status_alerts[0].severity, SeverityLevel.CRITICAL)

    def test_03_temporal_fraud_retroactive_pre_existing_claim(self) -> None:
        """Cenário 5.3: Fraude de sinistro retroativo (ocorrência antes do início de vigência da apólice)."""
        apolice = self.sro.registrar_apolice(
            numero_proposta="PROP-003",
            ramo=RamoSeguro.RURAL_AGRICOLA,
            seguradora_lider_cnpj="33000111000100",
            seguradora_lider_nome="Porto Seguro",
            segurado_documento="01234567890",
            segurado_nome="Cooperativa Agro",
            data_emissao="2026-11-05",
            inicio_vigencia="2026-11-05T00:00:00Z",
            fim_vigencia="2027-11-05T23:59:59Z",
            limite_maximo_garantia=Decimal("800000.00"),
            premio_liquido=Decimal("30000.00"),
            cosseguradores=self.cosseguradores_validos,
        )
        # Sinistro retroativo: evento ocorreu em 2026-10-20 (antes da vigência em novembro)
        apolice.sinistros.append(SinistroRecord(
            sinistro_id="SIN-RETRO-01",
            apolice_id=apolice.apolice_id,
            data_ocorrencia="2026-10-20",  # Sinistro pré-existente à apólice
            data_aviso="2026-11-06T10:00:00Z",
            valor_estimado=Decimal("120000.00"),
            status=StatusSinistro.AVISADO,
        ))

        alerts = self.engine.audit_sro_claims(self.sro)
        retro_alerts = [a for a in alerts if "SIN-RETRO-01" in a.evidence_records]
        self.assertEqual(len(retro_alerts), 1)
        alert = retro_alerts[0]
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.metrics["dt_ocorrencia"], "2026-10-20")

    def test_04_law113_claims_exceeding_lmg_violation(self) -> None:
        """Cenário 5.4: Violação da LAW-113 por liquidação cumulativa de sinistros superior ao LMG."""
        apolice = self.sro.registrar_apolice(
            numero_proposta="PROP-004",
            ramo=RamoSeguro.TRANSPORTE_CARGAS,
            seguradora_lider_cnpj="33000111000100",
            seguradora_lider_nome="Porto Seguro",
            segurado_documento="01234567890",
            segurado_nome="Logística Express",
            data_emissao="2026-11-01",
            inicio_vigencia="2026-11-01T00:00:00Z",
            fim_vigencia="2027-11-01T23:59:59Z",
            limite_maximo_garantia=Decimal("100000.00"),  # LMG = 100k
            premio_liquido=Decimal("5000.00"),
            cosseguradores=self.cosseguradores_validos,
        )
        # Força inserção de sinistro liquidado de 150k (superior ao LMG de 100k)
        apolice.sinistros.append(SinistroRecord(
            sinistro_id="SIN-EXCESS-01",
            apolice_id=apolice.apolice_id,
            data_ocorrencia="2026-11-03",
            data_aviso="2026-11-04T10:00:00Z",
            valor_estimado=Decimal("150000.00"),
            valor_liquidado=Decimal("150000.00"),  # Excede LMG!
            status=StatusSinistro.LIQUIDADO,
        ))

        alerts = self.engine.audit_sro_claims(self.sro)
        lmg_alerts = [a for a in alerts if "LAW113" in a.alert_id or "LMG" in a.alert_id]
        self.assertEqual(len(lmg_alerts), 1)
        alert = lmg_alerts[0]
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.metrics["total_liquidado"], 150000.0)
        self.assertEqual(alert.metrics["lmg"], 100000.0)

    def test_05_law113_cosseguro_partition_inequality_audit(self) -> None:
        """Cenário 5.5: Auditoria detecta violação da partição de cosseguro da LAW-113 (soma != 100.00%)."""
        # Monta apólice com quotas somando 90.00%
        apolice_invalid_cosseg = {
            "apolice_id": "SRO-B3-AUTO-0000888",
            "numero_proposta": "PROP-INVALID-COSSEG",
            "ramo": "0531_AUTOMOVEL",
            "seguradora_lider_cnpj": "33000111000100",
            "seguradora_lider_nome": "Porto Seguro",
            "segurado_documento": "01234567890",
            "inicio_vigencia": "2026-11-01T00:00:00Z",
            "fim_vigencia": "2027-11-01T23:59:59Z",
            "limite_maximo_garantia": "300000.00",
            "premio_liquido": "10000.00",
            "status": "VIGENTE",
            "cosseguradores": [
                {"cnpj": "33000111000100", "percentual_quota": "50.00"},
                {"cnpj": "61550141000172", "percentual_quota": "40.00"},  # Soma = 90.00% != 100.00%
            ],
            "sinistros": [],
        }

        alerts = self.engine.audit_sro_claims([apolice_invalid_cosseg])
        cosseg_alerts = [a for a in alerts if "COSSEGURO" in a.alert_id]
        self.assertEqual(len(cosseg_alerts), 1)
        alert = cosseg_alerts[0]
        self.assertEqual(alert.severity, SeverityLevel.CRITICAL)
        self.assertEqual(alert.metrics["total_quota"], 90.0)

    def test_06_sro_cryptographic_hash_tampering(self) -> None:
        """Cenário 5.6: Adulteração maliciosa pós-emissão de apólice no SRO detectada por divergência de hash SUSEP."""
        apolice = self.sro.registrar_apolice(
            numero_proposta="PROP-HASH-01",
            ramo=RamoSeguro.AUTOMOVEL,
            seguradora_lider_cnpj="33000111000100",
            seguradora_lider_nome="Porto Seguro",
            segurado_documento="01234567890",
            segurado_nome="Transportadora",
            data_emissao="2026-11-01",
            inicio_vigencia="2026-11-01T00:00:00Z",
            fim_vigencia="2027-11-01T23:59:59Z",
            limite_maximo_garantia=Decimal("200000.00"),
            premio_liquido=Decimal("8000.00"),
            cosseguradores=self.cosseguradores_validos,
        )
        # Adulteração ilícita de LMG de 200k para 500k sem rehash
        apolice.limite_maximo_garantia = Decimal("500000.00")

        alerts = self.engine.audit_sro_claims(self.sro)
        hash_alerts = [a for a in alerts if "HASH" in a.alert_id]
        self.assertEqual(len(hash_alerts), 1)
        self.assertEqual(hash_alerts[0].severity, SeverityLevel.CRITICAL)

    def test_07_functional_helper_audit_sro_claims(self) -> None:
        """Cenário 5.7: Execução via função helper audit_sro_claims garantindo conformidade operacional."""
        self.sro.registrar_apolice(
            numero_proposta="PROP-CLEAN-01",
            ramo=RamoSeguro.AUTOMOVEL,
            seguradora_lider_cnpj="33000111000100",
            seguradora_lider_nome="Porto Seguro",
            segurado_documento="01234567890",
            segurado_nome="Empresa Limpa",
            data_emissao="2026-11-01",
            inicio_vigencia="2026-11-01T00:00:00Z",
            fim_vigencia="2027-11-01T23:59:59Z",
            limite_maximo_garantia=Decimal("150000.00"),
            premio_liquido=Decimal("6000.00"),
            cosseguradores=self.cosseguradores_validos,
        )
        alerts = audit_sro_claims(self.sro)
        self.assertEqual(len(alerts), 0)


# =====================================================================
# SEÇÃO 6: RELATÓRIO CONSOLIDADO E DOSSIÊ DE AUDITORIA BSM
# =====================================================================

class TestBsmSurveillanceConsolidatedAuditReport(unittest.TestCase):
    """Testa geração integrada de relatório de auditoria e dossiê fiscalizatório da BSM."""

    def setUp(self) -> None:
        self.engine = BsmSurveillanceEngine()
        self.base_time = datetime(2026, 11, 7, 14, 0, 0, tzinfo=timezone.utc)

    def test_consolidated_report_and_dossier_export(self) -> None:
        """Gera relatório executivo BSM contendo alertas, resumos por participante e exportação de dossiê."""
        # 1. Alimenta com registros de Wash Trading
        cpf = "123.456.789-01"
        r1 = make_audit_record(
            record_id="AUD-REP-W1",
            timestamp=self.base_time,
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-01",
            beneficiary_id=cpf,
            action_type=ActionType.TRADE,
            side=Side.SELL,
            price=Decimal("38.50"),
            quantity=1000,
            order_id="ORD-W-01",
        )
        r2 = make_audit_record(
            record_id="AUD-REP-W2",
            timestamp=self.base_time + timedelta(milliseconds=50),
            symbol="PETR4",
            participant_id="0088",
            account_id="ACC-01",
            beneficiary_id=cpf,
            action_type=ActionType.TRADE,
            side=Side.BUY,
            price=Decimal("38.50"),
            quantity=1000,
            order_id="ORD-W-02",
        )
        self.engine.ingest_records([r1, r2])

        # Gera relatório consolidado
        report = self.engine.generate_audit_report(report_id="BSM-AUDIT-107")
        self.assertIsInstance(report, BsmAuditReport)
        self.assertEqual(report.report_id, "BSM-AUDIT-107")
        self.assertEqual(report.total_records, 2)
        self.assertEqual(report.total_trades, 2)
        self.assertTrue(report.alerts_count >= 1)

        # Exporta dossiê técnico de fiscalização para o primeiro alerta
        alert = report.alerts[0]
        dossier = self.engine.export_bsm_dossier(alert)
        self.assertEqual(dossier["entity"], "BSM Supervisão de Mercados - B3 S.A.")
        self.assertIn("BSM-DOSSIER-", dossier["protocol_id"])
        self.assertEqual(dossier["evidence_count"], len(alert.evidence_records))
        self.assertEqual(dossier["participant"]["participant_id"], alert.participant_id)

        # Valida serialização JSON
        report_json = report.to_json()
        self.assertIn("BSM-AUDIT-107", report_json)
        self.assertIn("alerts", report_json)


if __name__ == "__main__":
    unittest.main(verbosity=2)
