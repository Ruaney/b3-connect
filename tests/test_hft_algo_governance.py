"""Bateria de testes unitários de Governança de Negociação Algorítmica e HFT (FL-047).

Valida:
1. Conformidade de tags regulatórias FIX CVM 505 (Tags 1031, 57, 1724, 8015).
2. Sincronização temporal de relógios RTS 25 (< 100 µs para HFT, < 1 ms para algo, < 1 s manual).
3. Cálculo determinístico de Order-to-Trade Ratio (OTR) por volume e por quantidade.
4. Enquadramento de tetos de OTR por classe de ativo (Ibovespa, Small Caps, Futuros, Opções).
5. Apuração progressiva de tarifas punitivas e multas por sobrecarga de mensageria da B3.
6. Carga de fixtures e geração de dossiê de fiscalização BSM.
"""

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

# Garante que a raiz do repositório esteja no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.hft_algo_governance import (
    ActionType,
    AlgoOrderTag,
    AssetClass,
    ClockSynchronizationError,
    CustOrderHandlingType,
    HftAlgoGovernanceEngine,
    HftGovernanceError,
    HftGovernanceReport,
    MAX_CLOCK_DRIFT_MICROS,
    OrderOriginationRole,
    OtrAssetLimit,
    OtrCalculationResult,
    OtrTariffPenalty,
    OtrViolationError,
    PenaltyTier,
    RegulatoryTagValidationError,
    TradingAlgorithmClass,
)


class TestHftAlgoGovernance(unittest.TestCase):
    """Testes de conformidade regulatória CVM 505 / RTS 25 e OTR B3."""

    def setUp(self) -> None:
        self.engine = HftAlgoGovernanceEngine()
        self.fixtures_path = ROOT_DIR / "fixtures" / "hft_algo_governance.json"

    # =================================================================
    # 1. TESTES DE CLASSIFICAÇÃO DE ATIVOS
    # =================================================================

    def test_asset_classification(self) -> None:
        """Verifica se os instrumentos são roteados para as classes corretas de OTR."""
        # Large Caps (Ibovespa)
        self.assertEqual(self.engine.classify_asset("PETR4"), AssetClass.EQUITIES_LARGE_CAP)
        self.assertEqual(self.engine.classify_asset("VALE3"), AssetClass.EQUITIES_LARGE_CAP)
        self.assertEqual(self.engine.classify_asset("ITUB4"), AssetClass.EQUITIES_LARGE_CAP)

        # Small Caps
        self.assertEqual(self.engine.classify_asset("CASH3"), AssetClass.EQUITIES_SMALL_CAP)
        self.assertEqual(self.engine.classify_asset("MGLU3"), AssetClass.EQUITIES_SMALL_CAP)

        # Futuros Financeiros
        self.assertEqual(self.engine.classify_asset("DI1F29"), AssetClass.FUTURES_FINANCIAL)
        self.assertEqual(self.engine.classify_asset("DOLG27"), AssetClass.FUTURES_FINANCIAL)
        self.assertEqual(self.engine.classify_asset("WINF27"), AssetClass.FUTURES_FINANCIAL)

        # Opções Listadas
        self.assertEqual(self.engine.classify_asset("PETRB300"), AssetClass.OPTIONS)
        self.assertEqual(self.engine.classify_asset("VALEA650"), AssetClass.OPTIONS)

    # =================================================================
    # 2. TESTES DE VALIDAÇÃO DE TAGS REGULATÓRIAS (CVM 505 / FIX)
    # =================================================================

    def test_validate_regulatory_tags_compliant_hft(self) -> None:
        """Verifica que uma ordem HFT com todas as tags corretas é aprovada sem erros."""
        tag = AlgoOrderTag(
            cl_ord_id="ORD-OK-01",
            symbol="PETR4",
            action_type=ActionType.NEW,
            quantity=1000,
            price=Decimal("38.50"),
            timestamp=datetime(2026, 11, 3, 10, 0, 0, 100000, tzinfo=timezone.utc),
            cust_order_handling_type=CustOrderHandlingType.ALGORITHMIC,
            trading_algorithm_id="ALGO-MM-01",
            order_origination_role=OrderOriginationRole.ALGORITHM_DEA,
            executing_trader="PQO-HFT-881",
            algo_class=TradingAlgorithmClass.HIGH_FREQUENCY_TRADING,
            clock_drift_micros=15,
        )
        errors = self.engine.validate_regulatory_tags(tag)
        self.assertEqual(len(errors), 0)

    def test_validate_regulatory_tags_missing_executing_trader(self) -> None:
        """Detecta ausência mandatória do operador PQO (Tag 8015)."""
        tag = AlgoOrderTag(
            cl_ord_id="ORD-FAIL-8015",
            symbol="VALE3",
            action_type=ActionType.NEW,
            quantity=500,
            price=Decimal("62.00"),
            timestamp=datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc),
            cust_order_handling_type=CustOrderHandlingType.ALGORITHMIC,
            trading_algorithm_id="ALGO-MM-01",
            order_origination_role=OrderOriginationRole.ALGORITHM_DEA,
            executing_trader="",  # VAZIO!
            algo_class=TradingAlgorithmClass.HIGH_FREQUENCY_TRADING,
        )
        errors = self.engine.validate_regulatory_tags(tag)
        self.assertTrue(any("Tag 8015" in err for err in errors))

    def test_validate_regulatory_tags_missing_algo_id(self) -> None:
        """Detecta ausência mandatória do ID de algoritmo (Tag 57) em ordem algorítmica."""
        tag = AlgoOrderTag(
            cl_ord_id="ORD-FAIL-57",
            symbol="VALE3",
            action_type=ActionType.NEW,
            quantity=500,
            price=Decimal("62.00"),
            timestamp=datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc),
            cust_order_handling_type=CustOrderHandlingType.ALGORITHMIC,
            trading_algorithm_id=None,  # AUSENTE!
            order_origination_role=OrderOriginationRole.ALGORITHM_DEA,
            executing_trader="PQO-HFT-881",
            algo_class=TradingAlgorithmClass.HIGH_FREQUENCY_TRADING,
        )
        errors = self.engine.validate_regulatory_tags(tag)
        self.assertTrue(any("Tag 57" in err for err in errors))

    def test_validate_regulatory_tags_manual_order_with_algo_id(self) -> None:
        """Rejeita ordem manual com Tag 57 preenchida indevidamente."""
        tag = AlgoOrderTag(
            cl_ord_id="ORD-FAIL-MANUAL",
            symbol="VALE3",
            action_type=ActionType.NEW,
            quantity=100,
            price=Decimal("62.00"),
            timestamp=datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc),
            cust_order_handling_type=CustOrderHandlingType.DESK_MANUAL,
            trading_algorithm_id="ALGO-INTRUSO",  # Não pode ter em manual!
            order_origination_role=OrderOriginationRole.AGENCY,
            executing_trader="PQO-TRADER-10",
            algo_class=TradingAlgorithmClass.MANUAL_DISCRETIONARY,
        )
        errors = self.engine.validate_regulatory_tags(tag)
        self.assertTrue(any("Tag 57" in err for err in errors))

    def test_validate_regulatory_tags_hft_invalid_handling_type(self) -> None:
        """Detecta rotulagem falsa de ordem HFT como manual de mesa (Tag 1031)."""
        tag = AlgoOrderTag(
            cl_ord_id="ORD-FAIL-1031",
            symbol="DOLG27",
            action_type=ActionType.NEW,
            quantity=50,
            price=Decimal("5400.00"),
            timestamp=datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc),
            cust_order_handling_type=CustOrderHandlingType.DESK_MANUAL,  # Incompatível com HFT!
            trading_algorithm_id="ALGO-HFT-01",
            order_origination_role=OrderOriginationRole.ALGORITHM_DEA,
            executing_trader="PQO-HFT-881",
            algo_class=TradingAlgorithmClass.HIGH_FREQUENCY_TRADING,
        )
        errors = self.engine.validate_regulatory_tags(tag)
        self.assertTrue(any("Tag 1031" in err for err in errors))

    # =================================================================
    # 3. TESTES DE SINCRONIZAÇÃO DE RELÓGIOS RTS 25
    # =================================================================

    def test_clock_synchronization_hft_limits(self) -> None:
        """Valida que ordens HFT exigem desvio estritamente inferior a 100 µs."""
        # Conforme: 75 µs
        tag_ok = AlgoOrderTag(
            cl_ord_id="ORD-SYNC-OK",
            symbol="PETR4",
            action_type=ActionType.NEW,
            quantity=100,
            price=Decimal("38.00"),
            timestamp=datetime.now(timezone.utc),
            cust_order_handling_type=CustOrderHandlingType.ALGORITHMIC,
            trading_algorithm_id="ALGO-01",
            executing_trader="PQO-01",
            algo_class=TradingAlgorithmClass.HIGH_FREQUENCY_TRADING,
            clock_drift_micros=75,
        )
        is_sync, _ = self.engine.validate_clock_synchronization(tag_ok)
        self.assertTrue(is_sync)

        # Não Conforme: 120 µs (> 100 µs)
        tag_fail = AlgoOrderTag(
            cl_ord_id="ORD-SYNC-FAIL",
            symbol="PETR4",
            action_type=ActionType.NEW,
            quantity=100,
            price=Decimal("38.00"),
            timestamp=datetime.now(timezone.utc),
            cust_order_handling_type=CustOrderHandlingType.ALGORITHMIC,
            trading_algorithm_id="ALGO-01",
            executing_trader="PQO-01",
            algo_class=TradingAlgorithmClass.HIGH_FREQUENCY_TRADING,
            clock_drift_micros=120,
        )
        is_sync, msg = self.engine.validate_clock_synchronization(tag_fail)
        self.assertFalse(is_sync)
        self.assertIn("RTS 25 Non-Compliance", msg)

    def test_clock_synchronization_standard_algo_limits(self) -> None:
        """Valida tolerância de 1.000 µs (1 ms) para algoritmos convencionais."""
        tag = AlgoOrderTag(
            cl_ord_id="ORD-ALGO-SYNC",
            symbol="CASH3",
            action_type=ActionType.NEW,
            quantity=500,
            price=Decimal("8.50"),
            timestamp=datetime.now(timezone.utc),
            cust_order_handling_type=CustOrderHandlingType.ALGORITHMIC,
            trading_algorithm_id="ALGO-VWAP-01",
            executing_trader="PQO-01",
            algo_class=TradingAlgorithmClass.ALGORITHMIC_STANDARD,
            clock_drift_micros=850,  # < 1000 µs
        )
        is_sync, _ = self.engine.validate_clock_synchronization(tag)
        self.assertTrue(is_sync)

        tag.clock_drift_micros = 1250  # > 1000 µs
        is_sync, _ = self.engine.validate_clock_synchronization(tag)
        self.assertFalse(is_sync)

    def test_clock_synchronization_manual_limits(self) -> None:
        """Valida tolerância de 1.000.000 µs (1 s) para ordens manuais."""
        tag = AlgoOrderTag(
            cl_ord_id="ORD-MANUAL-SYNC",
            symbol="PETR4",
            action_type=ActionType.NEW,
            quantity=100,
            price=Decimal("38.00"),
            timestamp=datetime.now(timezone.utc),
            cust_order_handling_type=CustOrderHandlingType.DESK_MANUAL,
            executing_trader="PQO-01",
            algo_class=TradingAlgorithmClass.MANUAL_DISCRETIONARY,
            clock_drift_micros=450_000,  # 450 ms < 1 s
        )
        is_sync, _ = self.engine.validate_clock_synchronization(tag)
        self.assertTrue(is_sync)

        tag.clock_drift_micros = 1_500_000  # 1.5 s > 1 s
        is_sync, _ = self.engine.validate_clock_synchronization(tag)
        self.assertFalse(is_sync)

    # =================================================================
    # 4. TESTES DE CÁLCULO DE OTR (VOLUME E QUANTIDADE)
    # =================================================================

    def test_otr_calculation_deterministic_formulas(self) -> None:
        """Verifica a exatidão das fórmulas OTR_vol e OTR_qty."""
        # Cria um cenário com 10 New, 5 Replace, 2 Cancel, 3 Trades
        # Total New + Replace = 15
        # Total Trades = 3
        # OTR_vol esperado = 15 / 3 = 5.00
        # Qty New (10 * 100 = 1000) + Qty Replace (5 * 100 = 500) = 1500
        # Qty Executada = 3 * 100 = 300
        # OTR_qty esperado = 1500 / 300 = 5.00

        for i in range(10):
            self.engine.add_order_event(
                AlgoOrderTag(
                    cl_ord_id=f"NEW-{i}",
                    symbol="PETR4",
                    action_type=ActionType.NEW,
                    quantity=100,
                    price=Decimal("38.50"),
                    timestamp=datetime.now(timezone.utc),
                    cust_order_handling_type=CustOrderHandlingType.ALGORITHMIC,
                    trading_algorithm_id="TEST-ALGO",
                    executing_trader="PQO-01",
                )
            )

        for i in range(5):
            self.engine.add_order_event(
                AlgoOrderTag(
                    cl_ord_id=f"REPLACE-{i}",
                    symbol="PETR4",
                    action_type=ActionType.REPLACE,
                    quantity=100,
                    price=Decimal("38.51"),
                    timestamp=datetime.now(timezone.utc),
                    cust_order_handling_type=CustOrderHandlingType.ALGORITHMIC,
                    trading_algorithm_id="TEST-ALGO",
                    executing_trader="PQO-01",
                )
            )

        for i in range(2):
            self.engine.add_order_event(
                AlgoOrderTag(
                    cl_ord_id=f"CANCEL-{i}",
                    symbol="PETR4",
                    action_type=ActionType.CANCEL,
                    quantity=100,
                    price=Decimal("38.51"),
                    timestamp=datetime.now(timezone.utc),
                    cust_order_handling_type=CustOrderHandlingType.ALGORITHMIC,
                    trading_algorithm_id="TEST-ALGO",
                    executing_trader="PQO-01",
                )
            )

        for i in range(3):
            self.engine.add_order_event(
                AlgoOrderTag(
                    cl_ord_id=f"TRADE-{i}",
                    symbol="PETR4",
                    action_type=ActionType.TRADE,
                    quantity=100,
                    price=Decimal("38.51"),
                    timestamp=datetime.now(timezone.utc),
                    cust_order_handling_type=CustOrderHandlingType.ALGORITHMIC,
                    trading_algorithm_id="TEST-ALGO",
                    executing_trader="PQO-01",
                )
            )

        results = self.engine.calculate_otr(algorithm_id="TEST-ALGO", symbol="PETR4")
        self.assertEqual(len(results), 1)
        res = results[0]

        self.assertEqual(res.total_new_orders, 10)
        self.assertEqual(res.total_replace_orders, 5)
        self.assertEqual(res.total_cancel_orders, 2)
        self.assertEqual(res.total_orders_inserted_amended, 15)
        self.assertEqual(res.executed_trades, 3)
        self.assertEqual(res.total_qty_offered, 1500)
        self.assertEqual(res.executed_qty, 300)
        self.assertEqual(res.otr_volume, Decimal("5.00"))
        self.assertEqual(res.otr_quantity, Decimal("5.00"))
        self.assertFalse(res.is_volume_violation)
        self.assertFalse(res.is_quantity_violation)
        self.assertEqual(res.excess_orders, 0)

    def test_otr_zero_trades_division_guard(self) -> None:
        """Garante que ausência de execuções (trades=0) utiliza denominador 1 com segurança."""
        self.engine.add_order_event(
            AlgoOrderTag(
                cl_ord_id="NEW-ZERO-TRADE",
                symbol="VALE3",
                action_type=ActionType.NEW,
                quantity=1000,
                price=Decimal("60.00"),
                timestamp=datetime.now(timezone.utc),
                cust_order_handling_type=CustOrderHandlingType.ALGORITHMIC,
                trading_algorithm_id="ZERO-TRADE-ALGO",
                executing_trader="PQO-01",
            )
        )
        results = self.engine.calculate_otr(algorithm_id="ZERO-TRADE-ALGO", symbol="VALE3")
        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.executed_trades, 0)
        self.assertEqual(res.executed_qty, 0)
        self.assertEqual(res.otr_volume, Decimal("1.00"))
        self.assertEqual(res.otr_quantity, Decimal("1000.00"))

    # =================================================================
    # 5. TESTES DE APURAÇÃO DE TARIFAS E PENALIDADES DA B3
    # =================================================================

    def test_penalty_calculation_tier1(self) -> None:
        """Testa apuração na faixa Tier 1 (1.0x < Er <= 1.5x) a R$ 0,05 por ordem excedente."""
        # Large Cap limite = 150
        # 1 Trade executado. Permite 150 ordens.
        # Envia 180 ordens inseridas.
        # Er = 180 / 150 = 1.20x (Tier 1)
        # Excesso = 180 - 150 = 30 ordens.
        # Taxa esperada: 30 * 0.05 = R$ 1,50
        calc_result = OtrCalculationResult(
            algorithm_id="TIER1-ALGO",
            symbol="PETR4",
            asset_class=AssetClass.EQUITIES_LARGE_CAP,
            algo_class=TradingAlgorithmClass.HIGH_FREQUENCY_TRADING,
            total_new_orders=180,
            total_replace_orders=0,
            total_cancel_orders=0,
            total_orders_inserted_amended=180,
            executed_trades=1,
            total_qty_offered=18000,
            executed_qty=1000,
            otr_volume=Decimal("180.00"),
            otr_quantity=Decimal("18.00"),
            max_otr_volume=Decimal("150"),
            max_otr_quantity=Decimal("250"),
            is_volume_violation=True,
            is_quantity_violation=False,
            excess_orders=30,
            excess_quantity=0,
        )

        penalties = self.engine.calculate_penalties([calc_result])
        self.assertEqual(len(penalties), 1)
        p = penalties[0]

        self.assertEqual(p.penalty_tier, PenaltyTier.TIER_1)
        self.assertEqual(p.excess_orders, 30)
        self.assertEqual(p.volume_penalty_fee, Decimal("1.50"))
        self.assertEqual(p.fixed_penalty_fee, Decimal("0.00"))
        self.assertEqual(p.total_penalty_brl, Decimal("1.50"))

    def test_penalty_calculation_tier2(self) -> None:
        """Testa apuração na faixa Tier 2 (1.5x < Er <= 2.5x) a R$ 0,15 por ordem excedente."""
        # Futuros limite = 100
        # 1 Trade. Permite 100 ordens.
        # Envia 200 ordens.
        # Er = 200 / 100 = 2.0x (Tier 2)
        # Excesso = 200 - 100 = 100 ordens.
        # Taxa esperada: 100 * 0.15 = R$ 15,00
        calc_result = OtrCalculationResult(
            algorithm_id="TIER2-ALGO",
            symbol="DOLG27",
            asset_class=AssetClass.FUTURES_FINANCIAL,
            algo_class=TradingAlgorithmClass.HIGH_FREQUENCY_TRADING,
            total_new_orders=200,
            total_replace_orders=0,
            total_cancel_orders=0,
            total_orders_inserted_amended=200,
            executed_trades=1,
            total_qty_offered=2000,
            executed_qty=50,
            otr_volume=Decimal("200.00"),
            otr_quantity=Decimal("40.00"),
            max_otr_volume=Decimal("100"),
            max_otr_quantity=Decimal("150"),
            is_volume_violation=True,
            is_quantity_violation=False,
            excess_orders=100,
            excess_quantity=0,
        )

        penalties = self.engine.calculate_penalties([calc_result])
        self.assertEqual(len(penalties), 1)
        p = penalties[0]

        self.assertEqual(p.penalty_tier, PenaltyTier.TIER_2)
        self.assertEqual(p.excess_orders, 100)
        self.assertEqual(p.volume_penalty_fee, Decimal("15.00"))
        self.assertEqual(p.fixed_penalty_fee, Decimal("0.00"))
        self.assertEqual(p.total_penalty_brl, Decimal("15.00"))

    def test_penalty_calculation_tier3_with_fixed_fine_and_qty_surcharge(self) -> None:
        """Testa apuração crítica Tier 3 (Er > 2.5x) com multa fixa e sobretaxa de quantidade."""
        # Large Cap limite = 150 vol, 250 qty
        # 1 Trade com 100 ações executadas.
        # Ordens: 600 ordens (Er = 600 / 150 = 4.0x > 2.5x -> Tier 3)
        # Ordens excedentes = 600 - 150 = 450 ordens.
        # Taxa de ordens: 450 * 0.35 = R$ 157,50
        # Multa fixa Tier 3: R$ 1.000,00
        # Qty Ofertada: 50.000 cotas. Qty Máxima permitida: 250 * 100 = 25.000 cotas.
        # Qty excedente: 50.000 - 25.000 = 25.000 cotas.
        # Surcharge de Qty: 25.000 * 0.001 = R$ 25,00
        # Total Esperado: 157.50 + 1000.00 + 25.00 = R$ 1.182,50
        calc_result = OtrCalculationResult(
            algorithm_id="TIER3-ALGO",
            symbol="VALE3",
            asset_class=AssetClass.EQUITIES_LARGE_CAP,
            algo_class=TradingAlgorithmClass.HIGH_FREQUENCY_TRADING,
            total_new_orders=600,
            total_replace_orders=0,
            total_cancel_orders=0,
            total_orders_inserted_amended=600,
            executed_trades=1,
            total_qty_offered=50000,
            executed_qty=100,
            otr_volume=Decimal("600.00"),
            otr_quantity=Decimal("500.00"),
            max_otr_volume=Decimal("150"),
            max_otr_quantity=Decimal("250"),
            is_volume_violation=True,
            is_quantity_violation=True,
            excess_orders=450,
            excess_quantity=25000,
        )

        penalties = self.engine.calculate_penalties([calc_result])
        self.assertEqual(len(penalties), 1)
        p = penalties[0]

        self.assertEqual(p.penalty_tier, PenaltyTier.TIER_3)
        self.assertEqual(p.excess_orders, 450)
        self.assertEqual(p.excess_quantity, 25000)
        self.assertEqual(p.volume_penalty_fee, Decimal("157.50"))
        self.assertEqual(p.quantity_surcharge_fee, Decimal("25.00"))
        self.assertEqual(p.fixed_penalty_fee, Decimal("1000.00"))
        self.assertEqual(p.total_penalty_brl, Decimal("1182.50"))

    # =================================================================
    # 6. TESTES DE CARGA DE FIXTURES E RELATÓRIO CONSOLIDADO
    # =================================================================

    def test_load_from_fixture_file_and_generate_report(self) -> None:
        """Carrega a fixture oficial 'fixtures/hft_algo_governance.json' e valida o dossiê."""
        self.assertTrue(self.fixtures_path.exists(), f"Fixture não encontrada: {self.fixtures_path}")

        engine = HftAlgoGovernanceEngine.load_from_json(self.fixtures_path)
        self.assertGreater(len(engine.events), 10)

        report = engine.generate_governance_report("TEST-BSM-REP-01")
        self.assertIsInstance(report, HftGovernanceReport)
        self.assertEqual(report.report_id, "TEST-BSM-REP-01")
        self.assertGreater(report.total_hft_events, 0)
        self.assertGreater(report.total_standard_algo_events, 0)
        self.assertGreater(report.total_manual_events, 0)

        # Verifica detecção da ordem não conforme (CL-NONCOMPLIANT-001)
        self.assertTrue(
            any("Tag 8015" in err for err in report.tag_validation_errors),
            "Deveria ter detectado falta de ExecutingTrader na ordem não conforme",
        )
        self.assertTrue(
            any("Tag 1031" in err for err in report.tag_validation_errors),
            "Deveria ter detectado inconsistência de CustOrderHandlingType",
        )

        # Verifica detecção de violação de relógio RTS 25 (> 100 µs em HFT)
        self.assertTrue(
            any(item["cl_ord_id"] == "CL-NONCOMPLIANT-001" for item in report.clock_sync_violations),
            "Deveria ter registrado violação de sincronização temporal para CL-NONCOMPLIANT-001",
        )

        # Verifica que o algoritmo abusivo em VALE3 foi penalizado
        abusive_penalty = next(
            (p for p in report.penalties if p.algorithm_id == "ALGO-HFT-ABUSIVE-02"),
            None,
        )
        self.assertIsNotNone(abusive_penalty, "ALGO-HFT-ABUSIVE-02 deveria ter sido avaliado")
        # Deve ter violado OTR de quantidade (150.000 cotas ofertadas para 100 executadas = ratio 1500 > 250)
        self.assertTrue(abusive_penalty.penalty_tier != PenaltyTier.NONE)
        self.assertGreater(abusive_penalty.total_penalty_brl, Decimal("0.00"))

        # Verifica serialização do relatório
        rep_dict = report.to_dict()
        self.assertIn("report_id", rep_dict)
        self.assertIn("penalties", rep_dict)
        self.assertIn("clock_sync_violations", rep_dict)

        # Verifica serialização do motor
        engine_dict = engine.to_dict()
        self.assertIn("limits", engine_dict)
        self.assertEqual(engine_dict["events_count"], len(engine.events))


if __name__ == "__main__":
    unittest.main()
