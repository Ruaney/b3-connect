"""Suíte de Testes Unitários — Motor Regulatório de Formador de Mercado PMM B3 (FL-103).

Testa exaustivamente todas as obrigações regulatórias, parâmetros oficiais de liquidez,
spread máximo, lotes mínimos bilaterais, janela de recomposição pós-execução, exclusão
de leilões, auditoria de descredenciamento e perda de incentivos fiscais/emolumentares,
além das leis invariantes formais LAW-109 (LAW-109A a LAW-109D).

100% Python Standard Library (unittest, decimal, json, pathlib, datetime).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.invariants import InvariantViolationError, assert_market_maker_pmm_liquidity_law_109
from src.market_maker_pmm_engine import (
    AssetClass,
    CRITICAL_DEACCREDITATION_PCT,
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_MAX_DAILY_SPREAD_VIOLATIONS,
    DEFAULT_MAX_REGENERATION_SECONDS,
    DEFAULT_MIN_PRESENCE_PCT,
    DeaccreditationReason,
    DeaccreditationStatus,
    LiquidityTier,
    MarketMakerPmmEngine,
    MarketMakerQuoteSnapshot,
    MarketPhase,
    PmmContract,
    PmmContractNotFoundError,
    PmmDeaccreditationAudit,
    PmmError,
    PmmInvariantViolationError,
    PmmMonthlySettlement,
    PmmObligationStatus,
    PmmSessionAudit,
    PmmValidationError,
    SnapshotEvaluationResult,
    parse_datetime,
    round_cents,
    round_pct,
    round_qty,
)


class TestMarketMakerPmmEngine(unittest.TestCase):
    """Testes unitários completos do subsistema regulatório de Formador de Mercado (FL-103)."""

    def setUp(self) -> None:
        self.engine = MarketMakerPmmEngine()
        self.sample_contract = PmmContract(
            contract_id="PMM-2026-PRIO3-BTG",
            participant_id="016-BTG_PACTUAL",
            symbol="PRIO3",
            asset_class=AssetClass.EQUITY_CASH,
            liquidity_tier=LiquidityTier.TIER_2_MEDIUM,
            min_presence_pct=Decimal("80.00"),
            max_spread_pct=Decimal("1.50"),
            min_bid_qty=Decimal("500"),
            min_ask_qty=Decimal("500"),
            max_regeneration_seconds=60,
            max_daily_spread_violations=5,
            base_discount_pct=Decimal("100.00"),
            min_monthly_compliant_ratio=Decimal("80.00"),
            deaccreditation_threshold_pct=Decimal("70.00"),
            max_consecutive_non_compliant_sessions=3,
        )
        self.engine.register_contract(self.sample_contract)

    # ==========================================================================
    # 1. TESTES DE CONTRATO E VALIDAÇÃO PARAMÉTRICA
    # ==========================================================================

    def test_contract_registration_and_retrieval(self) -> None:
        """Verifica registro e busca de contrato por participante e ticker."""
        c = self.engine.get_contract("016-BTG_PACTUAL", "PRIO3")
        self.assertIsNotNone(c)
        assert c is not None
        self.assertEqual(c.symbol, "PRIO3")
        self.assertEqual(c.asset_class, AssetClass.EQUITY_CASH)
        self.assertEqual(c.min_presence_pct, Decimal("80.00"))
        self.assertEqual(c.max_spread_pct, Decimal("1.50"))
        self.assertEqual(c.min_bid_qty, Decimal("500"))
        self.assertEqual(c.min_ask_qty, Decimal("500"))

        # Busca inexistente
        self.assertIsNone(self.engine.get_contract("999-DESCONHECIDO", "PETR4"))

    def test_contract_validation_errors(self) -> None:
        """Testa rejeição estrita de contratos com parâmetros regulatórios inválidos."""
        # Identificador vazio
        with self.assertRaises(PmmValidationError):
            PmmContract("", "016-BTG", "PRIO3", AssetClass.EQUITY_CASH, LiquidityTier.TIER_1_HIGH)

        # Participante vazio
        with self.assertRaises(PmmValidationError):
            PmmContract("PMM-01", "   ", "PRIO3", AssetClass.EQUITY_CASH, LiquidityTier.TIER_1_HIGH)

        # Símbolo vazio
        with self.assertRaises(PmmValidationError):
            PmmContract("PMM-01", "016-BTG", "", AssetClass.EQUITY_CASH, LiquidityTier.TIER_1_HIGH)

        # Presença mínima fora de [50, 100]
        with self.assertRaises(PmmValidationError):
            PmmContract(
                "PMM-01", "016-BTG", "PRIO3", AssetClass.EQUITY_CASH, LiquidityTier.TIER_1_HIGH,
                min_presence_pct=Decimal("45.00")
            )
        with self.assertRaises(PmmValidationError):
            PmmContract(
                "PMM-01", "016-BTG", "PRIO3", AssetClass.EQUITY_CASH, LiquidityTier.TIER_1_HIGH,
                min_presence_pct=Decimal("105.00")
            )

        # Spread máximo negativo ou zero
        with self.assertRaises(PmmValidationError):
            PmmContract(
                "PMM-01", "016-BTG", "PRIO3", AssetClass.EQUITY_CASH, LiquidityTier.TIER_1_HIGH,
                max_spread_pct=Decimal("-0.50")
            )
        with self.assertRaises(PmmValidationError):
            PmmContract(
                "PMM-01", "016-BTG", "PRIO3", AssetClass.EQUITY_CASH, LiquidityTier.TIER_1_HIGH,
                max_spread_pct=Decimal("0.00")
            )

        # Lotes mínimos não-positivos
        with self.assertRaises(PmmValidationError):
            PmmContract(
                "PMM-01", "016-BTG", "PRIO3", AssetClass.EQUITY_CASH, LiquidityTier.TIER_1_HIGH,
                min_bid_qty=Decimal("0")
            )
        with self.assertRaises(PmmValidationError):
            PmmContract(
                "PMM-01", "016-BTG", "PRIO3", AssetClass.EQUITY_CASH, LiquidityTier.TIER_1_HIGH,
                min_ask_qty=Decimal("-10")
            )

        # Carência de recomposição fora de [0, 300]
        with self.assertRaises(PmmValidationError):
            PmmContract(
                "PMM-01", "016-BTG", "PRIO3", AssetClass.EQUITY_CASH, LiquidityTier.TIER_1_HIGH,
                max_regeneration_seconds=350
            )

        # Limiar de descredenciamento maior que a presença mínima
        with self.assertRaises(PmmValidationError):
            PmmContract(
                "PMM-01", "016-BTG", "PRIO3", AssetClass.EQUITY_CASH, LiquidityTier.TIER_1_HIGH,
                min_presence_pct=Decimal("80.00"),
                deaccreditation_threshold_pct=Decimal("85.00")
            )

    # ==========================================================================
    # 2. TESTES DE AVALIAÇÃO DE SNAPSHOTS E FASES DO MERCADO
    # ==========================================================================

    def test_snapshot_continuous_compliant(self) -> None:
        """Snapshot com ambas as pontas dentro do spread e lote mínimo é COMPLIANT."""
        snap = MarketMakerQuoteSnapshot(
            timestamp=datetime(2026, 11, 3, 14, 0, 0, tzinfo=timezone.utc),
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=Decimal("45.00"),
            bid_qty=Decimal("600"),
            ask_price=Decimal("45.45"),
            ask_qty=Decimal("600"),
            phase=MarketPhase.CONTINUOUS,
        )
        res = self.engine.evaluate_snapshot(snap)
        self.assertEqual(res.status, PmmObligationStatus.COMPLIANT)
        self.assertTrue(res.is_valid_presence)
        self.assertTrue(res.is_eligible_phase)
        self.assertEqual(res.spread_pct, Decimal("1.0000"))  # ((45.45 - 45.00) / 45.00) * 100 = 1.00% <= 1.50%

    def test_snapshot_auction_exclusion(self) -> None:
        """Leilões de abertura, fechamento e volatilidade são excluídos da presença contínua."""
        phases = [
            MarketPhase.OPENING_AUCTION,
            MarketPhase.CLOSING_AUCTION,
            MarketPhase.VOLATILITY_AUCTION,
            MarketPhase.TRADING_HALT,
            MarketPhase.CLOSED,
        ]
        for p in phases:
            snap = MarketMakerQuoteSnapshot(
                timestamp=datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc),
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                bid_price=Decimal("45.00"),
                bid_qty=Decimal("1000"),
                ask_price=Decimal("45.20"),
                ask_qty=Decimal("1000"),
                phase=p,
            )
            res = self.engine.evaluate_snapshot(snap)
            self.assertEqual(res.status, PmmObligationStatus.AUCTION_EXCLUDED)
            self.assertFalse(res.is_valid_presence)
            self.assertFalse(res.is_eligible_phase)

    def test_snapshot_spread_violation(self) -> None:
        """Spread acima do teto contratual (1.50%) é classificado como SPREAD_VIOLATION."""
        snap = MarketMakerQuoteSnapshot(
            timestamp=datetime(2026, 11, 3, 14, 10, 0, tzinfo=timezone.utc),
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=Decimal("45.00"),
            bid_qty=Decimal("600"),
            ask_price=Decimal("46.00"),  # Spread: ((46 - 45)/45)*100 = 2.2222% > 1.50%
            ask_qty=Decimal("600"),
            phase=MarketPhase.CONTINUOUS,
        )
        res = self.engine.evaluate_snapshot(snap)
        self.assertEqual(res.status, PmmObligationStatus.SPREAD_VIOLATION)
        self.assertFalse(res.is_valid_presence)
        self.assertTrue(res.is_eligible_phase)

    def test_snapshot_depth_violation(self) -> None:
        """Lote abaixo do mínimo contratual (500) é classificado como DEPTH_VIOLATION."""
        # Lote de compra insuficiente
        snap_bid = MarketMakerQuoteSnapshot(
            timestamp=datetime(2026, 11, 3, 14, 20, 0, tzinfo=timezone.utc),
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=Decimal("45.00"),
            bid_qty=Decimal("300"),  # < 500
            ask_price=Decimal("45.40"),
            ask_qty=Decimal("600"),
            phase=MarketPhase.CONTINUOUS,
        )
        res_bid = self.engine.evaluate_snapshot(snap_bid)
        self.assertEqual(res_bid.status, PmmObligationStatus.DEPTH_VIOLATION)
        self.assertFalse(res_bid.is_valid_presence)

        # Lote de venda insuficiente
        snap_ask = MarketMakerQuoteSnapshot(
            timestamp=datetime(2026, 11, 3, 14, 21, 0, tzinfo=timezone.utc),
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=Decimal("45.00"),
            bid_qty=Decimal("600"),
            ask_price=Decimal("45.40"),
            ask_qty=Decimal("200"),  # < 500
            phase=MarketPhase.CONTINUOUS,
        )
        res_ask = self.engine.evaluate_snapshot(snap_ask)
        self.assertEqual(res_ask.status, PmmObligationStatus.DEPTH_VIOLATION)
        self.assertFalse(res_ask.is_valid_presence)

    def test_snapshot_spread_and_depth_violation(self) -> None:
        """Violação simultânea de spread e profundidade de lote."""
        snap = MarketMakerQuoteSnapshot(
            timestamp=datetime(2026, 11, 3, 14, 25, 0, tzinfo=timezone.utc),
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=Decimal("45.00"),
            bid_qty=Decimal("200"),  # < 500
            ask_price=Decimal("46.50"),  # Spread 3.33% > 1.50%
            ask_qty=Decimal("200"),  # < 500
            phase=MarketPhase.CONTINUOUS,
        )
        res = self.engine.evaluate_snapshot(snap)
        self.assertEqual(res.status, PmmObligationStatus.SPREAD_AND_DEPTH_VIOLATION)
        self.assertFalse(res.is_valid_presence)

    def test_snapshot_inverted_or_crossed_book(self) -> None:
        """Livro invertido (Ask <= Bid) deve ser marcado como SPREAD_VIOLATION e presença inválida."""
        snap = MarketMakerQuoteSnapshot(
            timestamp=datetime(2026, 11, 3, 14, 30, 0, tzinfo=timezone.utc),
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=Decimal("45.50"),
            bid_qty=Decimal("600"),
            ask_price=Decimal("45.00"),  # Ask < Bid
            ask_qty=Decimal("600"),
            phase=MarketPhase.CONTINUOUS,
        )
        res = self.engine.evaluate_snapshot(snap)
        self.assertEqual(res.status, PmmObligationStatus.SPREAD_VIOLATION)
        self.assertFalse(res.is_valid_presence)
        self.assertIn("cruzado", res.notes)

    def test_snapshot_missing_bid_or_ask(self) -> None:
        """Ausência unilateral de oferta gera MISSING_BID ou MISSING_ASK."""
        # Sem compra
        snap_no_bid = MarketMakerQuoteSnapshot(
            timestamp=datetime(2026, 11, 3, 14, 35, 0, tzinfo=timezone.utc),
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=None,
            bid_qty=None,
            ask_price=Decimal("45.40"),
            ask_qty=Decimal("600"),
            phase=MarketPhase.CONTINUOUS,
        )
        self.assertEqual(self.engine.evaluate_snapshot(snap_no_bid).status, PmmObligationStatus.MISSING_BID)

        # Sem venda
        snap_no_ask = MarketMakerQuoteSnapshot(
            timestamp=datetime(2026, 11, 3, 14, 36, 0, tzinfo=timezone.utc),
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=Decimal("45.00"),
            bid_qty=Decimal("600"),
            ask_price=None,
            ask_qty=None,
            phase=MarketPhase.CONTINUOUS,
        )
        self.assertEqual(self.engine.evaluate_snapshot(snap_no_ask).status, PmmObligationStatus.MISSING_ASK)

        # Ausência total
        snap_absent = MarketMakerQuoteSnapshot(
            timestamp=datetime(2026, 11, 3, 14, 37, 0, tzinfo=timezone.utc),
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=None,
            bid_qty=None,
            ask_price=None,
            ask_qty=None,
            phase=MarketPhase.CONTINUOUS,
        )
        self.assertEqual(self.engine.evaluate_snapshot(snap_absent).status, PmmObligationStatus.ABSENT)

    # ==========================================================================
    # 3. TESTES DA JANELA DE CARÊNCIA DE RECOMPOSIÇÃO (REGENERATION WINDOW)
    # ==========================================================================

    def test_regeneration_window_legitimate_carência(self) -> None:
        """Ofertas ausentes ou com lote reduzido logo após execução contam como REGENERATING (presença válida)."""
        trade_time = datetime(2026, 11, 3, 14, 40, 0, tzinfo=timezone.utc)
        snap_time = datetime(2026, 11, 3, 14, 40, 25, tzinfo=timezone.utc)  # 25s após trade <= 60s

        # Ausência total em recomposição legítima
        snap_recomp = MarketMakerQuoteSnapshot(
            timestamp=snap_time,
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=None,
            bid_qty=None,
            ask_price=None,
            ask_qty=None,
            phase=MarketPhase.CONTINUOUS,
            last_trade_timestamp=trade_time,
        )
        res = self.engine.evaluate_snapshot(snap_recomp)
        self.assertEqual(res.status, PmmObligationStatus.REGENERATING)
        self.assertTrue(res.is_valid_presence)
        self.assertEqual(res.regeneration_elapsed_seconds, Decimal("25"))

        # Recomposição de lote parcial (bid_qty abaixo do mínimo durante carência)
        snap_lote = MarketMakerQuoteSnapshot(
            timestamp=snap_time,
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=Decimal("45.00"),
            bid_qty=Decimal("100"),  # < 500
            ask_price=Decimal("45.40"),
            ask_qty=Decimal("600"),
            phase=MarketPhase.CONTINUOUS,
            last_trade_timestamp=trade_time,
        )
        res_lote = self.engine.evaluate_snapshot(snap_lote)
        self.assertEqual(res_lote.status, PmmObligationStatus.REGENERATING)
        self.assertTrue(res_lote.is_valid_presence)

    def test_regeneration_window_expired(self) -> None:
        """Após o término da janela de carência (>60s), ausência torna-se ABSENT e violação."""
        trade_time = datetime(2026, 11, 3, 14, 40, 0, tzinfo=timezone.utc)
        snap_time = datetime(2026, 11, 3, 14, 41, 15, tzinfo=timezone.utc)  # 75s após trade > 60s

        snap_expired = MarketMakerQuoteSnapshot(
            timestamp=snap_time,
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=None,
            bid_qty=None,
            ask_price=None,
            ask_qty=None,
            phase=MarketPhase.CONTINUOUS,
            last_trade_timestamp=trade_time,
        )
        res = self.engine.evaluate_snapshot(snap_expired)
        self.assertEqual(res.status, PmmObligationStatus.ABSENT)
        self.assertFalse(res.is_valid_presence)

    # ==========================================================================
    # 4. TESTES DE AUDITORIA DE SESSÃO ORDINÁRIA
    # ==========================================================================

    def test_audit_session_compliant(self) -> None:
        """Sessão com 90% de presença e zero violações é aprovada como conforme."""
        snapshots = [
            # 900s de leilão de abertura (excluído)
            MarketMakerQuoteSnapshot(
                timestamp=datetime(2026, 11, 3, 13, 0, tzinfo=timezone.utc),
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                bid_price=Decimal("45.00"),
                bid_qty=Decimal("1000"),
                ask_price=Decimal("45.30"),
                ask_qty=Decimal("1000"),
                phase=MarketPhase.OPENING_AUCTION,
                duration_seconds=Decimal("900.0"),
            ),
            # 9.000s contínuos válidos (COMPLIANT)
            MarketMakerQuoteSnapshot(
                timestamp=datetime(2026, 11, 3, 13, 15, tzinfo=timezone.utc),
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                bid_price=Decimal("45.10"),
                bid_qty=Decimal("600"),
                ask_price=Decimal("45.50"),
                ask_qty=Decimal("600"),
                phase=MarketPhase.CONTINUOUS,
                duration_seconds=Decimal("9000.0"),
            ),
            # 1.000s contínuos ausentes (ABSENT)
            MarketMakerQuoteSnapshot(
                timestamp=datetime(2026, 11, 3, 15, 45, tzinfo=timezone.utc),
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                bid_price=None,
                bid_qty=None,
                ask_price=None,
                ask_qty=None,
                phase=MarketPhase.CONTINUOUS,
                duration_seconds=Decimal("1000.0"),
            ),
        ]
        # Total contínuo elegível = 10.000s, Válido = 9.000s -> Presença = 90.00% >= 80.00%
        audit = self.engine.audit_session(snapshots, session_date="2026-11-03")
        self.assertTrue(audit.is_compliant)
        self.assertEqual(audit.presence_pct, Decimal("90.0000"))
        self.assertEqual(audit.eligible_continuous_seconds, Decimal("10000.0"))
        self.assertEqual(audit.valid_presence_seconds, Decimal("9000.0"))
        self.assertEqual(audit.spread_violation_count, 0)
        self.assertEqual(audit.missing_quote_count, 1)

    def test_audit_session_failed_presence(self) -> None:
        """Sessão com 75% de presença (<80%) é reprovada como não-conforme."""
        snapshots = [
            # 7.500s válidos
            MarketMakerQuoteSnapshot(
                timestamp=datetime(2026, 11, 3, 13, 15, tzinfo=timezone.utc),
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                bid_price=Decimal("45.10"),
                bid_qty=Decimal("600"),
                ask_price=Decimal("45.50"),
                ask_qty=Decimal("600"),
                phase=MarketPhase.CONTINUOUS,
                duration_seconds=Decimal("7500.0"),
            ),
            # 2.500s ausentes
            MarketMakerQuoteSnapshot(
                timestamp=datetime(2026, 11, 3, 15, 20, tzinfo=timezone.utc),
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                bid_price=None,
                bid_qty=None,
                ask_price=None,
                ask_qty=None,
                phase=MarketPhase.CONTINUOUS,
                duration_seconds=Decimal("2500.0"),
            ),
        ]
        # Presença = 75.00% < 80.00%
        audit = self.engine.audit_session(snapshots, session_date="2026-11-03")
        self.assertFalse(audit.is_compliant)
        self.assertEqual(audit.presence_pct, Decimal("75.0000"))
        self.assertIn("abaixo da meta contratual", audit.failure_reasons[0])

    def test_audit_session_excessive_spread_violations(self) -> None:
        """Sessão com violações de spread acima do teto diário tolerado (5) é reprovada."""
        snapshots = []
        for i in range(6):  # 6 violações de spread
            snapshots.append(
                MarketMakerQuoteSnapshot(
                    timestamp=datetime(2026, 11, 3, 13, i, tzinfo=timezone.utc),
                    symbol="PRIO3",
                    participant_id="016-BTG_PACTUAL",
                    bid_price=Decimal("45.00"),
                    bid_qty=Decimal("600"),
                    ask_price=Decimal("47.00"),  # Spread 4.44% > 1.50%
                    ask_qty=Decimal("600"),
                    phase=MarketPhase.CONTINUOUS,
                    duration_seconds=Decimal("100.0"),
                )
            )
        # Snapshot com presença normal para atingir tempo
        snapshots.append(
            MarketMakerQuoteSnapshot(
                timestamp=datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc),
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                bid_price=Decimal("45.00"),
                bid_qty=Decimal("600"),
                ask_price=Decimal("45.40"),
                ask_qty=Decimal("600"),
                phase=MarketPhase.CONTINUOUS,
                duration_seconds=Decimal("9400.0"),
            )
        )
        audit = self.engine.audit_session(snapshots, session_date="2026-11-03")
        self.assertFalse(audit.is_compliant)
        self.assertEqual(audit.spread_violation_count, 6)
        self.assertTrue(any("excederam o teto" in r for r in audit.failure_reasons))

    # ==========================================================================
    # 5. TESTES DE LIQUIDAÇÃO MENSAL E INCENTIVOS TARIFÁRIOS
    # ==========================================================================

    def test_monthly_settlement_compliant_full_discount(self) -> None:
        """Participante adimplente no mês recebe 100% de desconto contratual em tarifas."""
        # 4 sessões com 100% de conformidade e presença média de 92%
        sessions = [
            PmmSessionAudit(
                session_date=f"2026-11-0{i+1}",
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                total_session_seconds=Decimal("10000.0"),
                eligible_continuous_seconds=Decimal("10000.0"),
                valid_presence_seconds=Decimal("9200.0"),
                presence_pct=Decimal("92.0000"),
                required_presence_pct=Decimal("80.00"),
                spread_violation_count=0,
                depth_violation_count=0,
                missing_quote_count=0,
                regeneration_count=0,
                is_compliant=True,
                snapshots_count=10,
            )
            for i in range(4)
        ]
        settle = self.engine.calculate_monthly_settlement(
            session_results=sessions,
            gross_trading_fees=Decimal("60000.00"),
            gross_clearing_fees=Decimal("40000.00"),
            month_reference="2026-11",
            use_tiered_discount=False,
        )
        self.assertTrue(settle.is_reward_eligible)
        self.assertEqual(settle.discount_pct_applied, Decimal("100.00"))
        self.assertEqual(settle.total_gross_fees, Decimal("100000.00"))
        self.assertEqual(settle.discount_amount, Decimal("100000.00"))
        self.assertEqual(settle.net_fees_payable, Decimal("0.00"))

    def test_monthly_settlement_tiered_platinum(self) -> None:
        """No modelo escalonado (tiered), presença >= 95% concede Platinum (100% isenção)."""
        sessions = [
            PmmSessionAudit(
                session_date="2026-11-01",
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                total_session_seconds=Decimal("10000.0"),
                eligible_continuous_seconds=Decimal("10000.0"),
                valid_presence_seconds=Decimal("9700.0"),
                presence_pct=Decimal("97.0000"),
                required_presence_pct=Decimal("80.00"),
                spread_violation_count=0,
                depth_violation_count=0,
                missing_quote_count=0,
                regeneration_count=0,
                is_compliant=True,
                snapshots_count=5,
            )
        ]
        settle = self.engine.calculate_monthly_settlement(
            session_results=sessions,
            gross_trading_fees=Decimal("50000.00"),
            gross_clearing_fees=Decimal("30000.00"),
            use_tiered_discount=True,
        )
        self.assertEqual(settle.discount_pct_applied, Decimal("100.00"))
        self.assertIn("TIER_PLATINUM_100", settle.tier_description)

    def test_monthly_settlement_ineligible_zero_discount(self) -> None:
        """Participante inadimplente não tem direito a desconto (tarifa cheia)."""
        # 2 sessões conformes e 2 reprovadas -> ratio 50% < 80%
        sessions = [
            PmmSessionAudit(
                session_date=f"2026-11-0{i+1}",
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                total_session_seconds=Decimal("10000.0"),
                eligible_continuous_seconds=Decimal("10000.0"),
                valid_presence_seconds=Decimal("8500.0" if i < 2 else "6000.0"),
                presence_pct=Decimal("85.0000" if i < 2 else "60.0000"),
                required_presence_pct=Decimal("80.00"),
                spread_violation_count=0,
                depth_violation_count=0,
                missing_quote_count=0,
                regeneration_count=0,
                is_compliant=(i < 2),
                snapshots_count=5,
            )
            for i in range(4)
        ]
        settle = self.engine.calculate_monthly_settlement(
            session_results=sessions,
            gross_trading_fees=Decimal("50000.00"),
            gross_clearing_fees=Decimal("30000.00"),
        )
        self.assertFalse(settle.is_reward_eligible)
        self.assertEqual(settle.discount_pct_applied, Decimal("0.00"))
        self.assertEqual(settle.discount_amount, Decimal("0.00"))
        self.assertEqual(settle.net_fees_payable, Decimal("80000.00"))

    # ==========================================================================
    # 6. TESTES DE AUDITORIA DE DESCREDENCIAMENTO E SANÇÕES BSM
    # ==========================================================================

    def test_audit_deaccreditation_consecutive_failures(self) -> None:
        """3 sessões consecutivas com inadimplemento geram descredenciamento sumário (DEACCREDITED)."""
        sessions = [
            PmmSessionAudit(
                session_date=f"2026-11-0{i+1}",
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                total_session_seconds=Decimal("10000.0"),
                eligible_continuous_seconds=Decimal("10000.0"),
                valid_presence_seconds=Decimal("6500.0"),
                presence_pct=Decimal("65.0000"),
                required_presence_pct=Decimal("80.00"),
                spread_violation_count=1,
                depth_violation_count=1,
                missing_quote_count=0,
                regeneration_count=0,
                is_compliant=False,
                snapshots_count=5,
            )
            for i in range(3)
        ]
        deacc = self.engine.audit_deaccreditation(sessions)
        self.assertEqual(deacc.status, DeaccreditationStatus.DEACCREDITED)
        self.assertTrue(deacc.incentives_revoked)
        self.assertEqual(deacc.consecutive_non_compliant_count, 3)
        self.assertEqual(deacc.fine_or_penalty_amount, Decimal("10000.00"))
        self.assertIn("DESCREDENCIAMENTO DETERMINADO PELA BSM", deacc.audit_message)

    def test_audit_deaccreditation_warning(self) -> None:
        """Apenas uma falha isolada gera advertência (WARNING) sem revogação de incentivo."""
        sessions = [
            PmmSessionAudit(
                session_date="2026-11-01",
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                total_session_seconds=Decimal("10000.0"),
                eligible_continuous_seconds=Decimal("10000.0"),
                valid_presence_seconds=Decimal("7800.0"),
                presence_pct=Decimal("78.0000"),
                required_presence_pct=Decimal("80.00"),
                spread_violation_count=0,
                depth_violation_count=0,
                missing_quote_count=0,
                regeneration_count=0,
                is_compliant=False,
                snapshots_count=5,
            ),
            PmmSessionAudit(
                session_date="2026-11-02",
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                total_session_seconds=Decimal("10000.0"),
                eligible_continuous_seconds=Decimal("10000.0"),
                valid_presence_seconds=Decimal("9500.0"),
                presence_pct=Decimal("95.0000"),
                required_presence_pct=Decimal("80.00"),
                spread_violation_count=0,
                depth_violation_count=0,
                missing_quote_count=0,
                regeneration_count=0,
                is_compliant=True,
                snapshots_count=5,
            ),
            PmmSessionAudit(
                session_date="2026-11-03",
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                total_session_seconds=Decimal("10000.0"),
                eligible_continuous_seconds=Decimal("10000.0"),
                valid_presence_seconds=Decimal("9600.0"),
                presence_pct=Decimal("96.0000"),
                required_presence_pct=Decimal("80.00"),
                spread_violation_count=0,
                depth_violation_count=0,
                missing_quote_count=0,
                regeneration_count=0,
                is_compliant=True,
                snapshots_count=5,
            ),
            PmmSessionAudit(
                session_date="2026-11-04",
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                total_session_seconds=Decimal("10000.0"),
                eligible_continuous_seconds=Decimal("10000.0"),
                valid_presence_seconds=Decimal("9400.0"),
                presence_pct=Decimal("94.0000"),
                required_presence_pct=Decimal("80.00"),
                spread_violation_count=0,
                depth_violation_count=0,
                missing_quote_count=0,
                regeneration_count=0,
                is_compliant=True,
                snapshots_count=5,
            ),
            PmmSessionAudit(
                session_date="2026-11-05",
                symbol="PRIO3",
                participant_id="016-BTG_PACTUAL",
                total_session_seconds=Decimal("10000.0"),
                eligible_continuous_seconds=Decimal("10000.0"),
                valid_presence_seconds=Decimal("9500.0"),
                presence_pct=Decimal("95.0000"),
                required_presence_pct=Decimal("80.00"),
                spread_violation_count=0,
                depth_violation_count=0,
                missing_quote_count=0,
                regeneration_count=0,
                is_compliant=True,
                snapshots_count=5,
            ),
        ]
        # 4 de 5 conformes -> 80.00% compliance ratio, média > 90%
        deacc = self.engine.audit_deaccreditation(sessions)
        self.assertEqual(deacc.status, DeaccreditationStatus.WARNING)
        self.assertFalse(deacc.incentives_revoked)
        self.assertIn("NOTIFICAÇÃO DE ADVERTÊNCIA", deacc.audit_message)

    # ==========================================================================
    # 7. TESTE DE CARREGAMENTO DA FIXTURE JSON OFICIAL
    # ==========================================================================

    def test_load_official_fixture(self) -> None:
        """Carrega e audita a fixture JSON oficial do motor PMM."""
        fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "market_maker_pmm_engine.json"
        summary = self.engine.load_from_fixture(fixture_path)
        self.assertEqual(summary["contracts_loaded"], 3)
        self.assertEqual(summary["sessions_audited"], 4)
        self.assertEqual(summary["monthly_settlements"], 3)
        self.assertEqual(summary["deaccreditations_audited"], 3)

        # Verifica conformidade da auditoria de invariantes com os dados da fixture
        invariants = self.engine.verify_invariants()
        self.assertTrue(invariants["LAW-109A"])
        self.assertTrue(invariants["LAW-109B"])
        self.assertTrue(invariants["LAW-109C"])
        self.assertTrue(invariants["LAW-109D"])

    # ==========================================================================
    # 8. TESTES DE INVARIANTES REGULATÓRIAS (LAW-109)
    # ==========================================================================

    def test_verify_invariants_law_109a_violation(self) -> None:
        """LAW-109A: Sessão aprovada como conforme com presença < 80% dispara InvariantViolationError."""
        invalid_session = PmmSessionAudit(
            session_date="2026-11-03",
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            total_session_seconds=Decimal("10000.0"),
            eligible_continuous_seconds=Decimal("10000.0"),
            valid_presence_seconds=Decimal("7000.0"),
            presence_pct=Decimal("70.0000"),
            required_presence_pct=Decimal("80.00"),
            spread_violation_count=0,
            depth_violation_count=0,
            missing_quote_count=0,
            regeneration_count=0,
            is_compliant=True,  # Violação proposital da invariante
            snapshots_count=1,
        )
        self.engine.session_audits.append(invalid_session)
        with self.assertRaises(PmmInvariantViolationError) as ctx:
            self.engine.verify_invariants()
        self.assertEqual(ctx.exception.law_code, "LAW-109A")

    def test_verify_invariants_law_109b_violation(self) -> None:
        """LAW-109B: Snapshot marcado como COMPLIANT com spread acima do limite dispara erro."""
        snap = MarketMakerQuoteSnapshot(
            timestamp=datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc),
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=Decimal("45.00"),
            bid_qty=Decimal("600"),
            ask_price=Decimal("46.50"),
            ask_qty=Decimal("600"),
            phase=MarketPhase.CONTINUOUS,
        )
        invalid_eval = SnapshotEvaluationResult(
            snapshot=snap,
            status=PmmObligationStatus.COMPLIANT,  # Violação proposital
            is_valid_presence=True,
            is_eligible_phase=True,
            spread_pct=Decimal("3.3333"),  # > 1.50%
            spread_ticks=Decimal("1.50"),
        )
        session = PmmSessionAudit(
            session_date="2026-11-03",
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            total_session_seconds=Decimal("1.0"),
            eligible_continuous_seconds=Decimal("1.0"),
            valid_presence_seconds=Decimal("1.0"),
            presence_pct=Decimal("100.0000"),
            required_presence_pct=Decimal("80.00"),
            spread_violation_count=0,
            depth_violation_count=0,
            missing_quote_count=0,
            regeneration_count=0,
            is_compliant=True,
            snapshots_count=1,
            snapshot_results=[invalid_eval],
        )
        self.engine.session_audits.append(session)
        with self.assertRaises(PmmInvariantViolationError) as ctx:
            self.engine.verify_invariants()
        self.assertEqual(ctx.exception.law_code, "LAW-109B")

    def test_verify_invariants_law_109c_violation(self) -> None:
        """LAW-109C: Snapshot COMPLIANT com quantidade insuficiente dispara erro."""
        snap = MarketMakerQuoteSnapshot(
            timestamp=datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc),
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            bid_price=Decimal("45.00"),
            bid_qty=Decimal("200"),  # < 500
            ask_price=Decimal("45.30"),
            ask_qty=Decimal("600"),
            phase=MarketPhase.CONTINUOUS,
        )
        invalid_eval = SnapshotEvaluationResult(
            snapshot=snap,
            status=PmmObligationStatus.COMPLIANT,  # Violação proposital
            is_valid_presence=True,
            is_eligible_phase=True,
            spread_pct=Decimal("0.6667"),
            spread_ticks=Decimal("0.30"),
        )
        session = PmmSessionAudit(
            session_date="2026-11-03",
            symbol="PRIO3",
            participant_id="016-BTG_PACTUAL",
            total_session_seconds=Decimal("1.0"),
            eligible_continuous_seconds=Decimal("1.0"),
            valid_presence_seconds=Decimal("1.0"),
            presence_pct=Decimal("100.0000"),
            required_presence_pct=Decimal("80.00"),
            spread_violation_count=0,
            depth_violation_count=0,
            missing_quote_count=0,
            regeneration_count=0,
            is_compliant=True,
            snapshots_count=1,
            snapshot_results=[invalid_eval],
        )
        self.engine.session_audits.append(session)
        with self.assertRaises(PmmInvariantViolationError) as ctx:
            self.engine.verify_invariants()
        self.assertEqual(ctx.exception.law_code, "LAW-109C")

    def test_verify_invariants_law_109d_violation(self) -> None:
        """LAW-109D: Concessão de desconto a participante inelegível dispara erro."""
        invalid_settle = PmmMonthlySettlement(
            month_reference="2026-11",
            participant_id="016-BTG_PACTUAL",
            symbol="PRIO3",
            total_sessions=10,
            compliant_sessions=4,
            compliance_ratio_pct=Decimal("40.0000"),
            average_presence_pct=Decimal("60.0000"),
            min_required_presence_pct=Decimal("80.00"),
            is_reward_eligible=False,  # Inelegível
            discount_pct_applied=Decimal("50.00"),  # Violação proposital: concedeu desconto
            gross_trading_fees=Decimal("10000.00"),
            gross_clearing_fees=Decimal("10000.00"),
            total_gross_fees=Decimal("20000.00"),
            discount_amount=Decimal("10000.00"),  # Violação
            net_fees_payable=Decimal("10000.00"),
        )
        self.engine.monthly_settlements.append(invalid_settle)
        with self.assertRaises(PmmInvariantViolationError) as ctx:
            self.engine.verify_invariants()
        self.assertEqual(ctx.exception.law_code, "LAW-109D")

    # ==========================================================================
    # 9. TESTES DO HELPER CENTRAL DE INVARIANTES (src.invariants)
    # ==========================================================================

    def test_central_invariant_helper_law_109(self) -> None:
        """Testa o assert central em src.invariants assert_market_maker_pmm_liquidity_law_109."""
        # Sucesso
        assert_market_maker_pmm_liquidity_law_109(
            presence_pct=Decimal("85.50"),
            min_presence_pct=Decimal("80.00"),
            is_compliant=True,
            spread_pct=Decimal("1.20"),
            max_spread_pct=Decimal("1.50"),
            bid_qty=Decimal("500"),
            min_bid_qty=Decimal("500"),
            ask_qty=Decimal("600"),
            min_ask_qty=Decimal("500"),
            discount_amount=Decimal("0.00"),
            is_incentive_eligible=False,
            participant_id="016-BTG",
            symbol="PRIO3",
        )

        # Falha de presença
        with self.assertRaises(InvariantViolationError):
            assert_market_maker_pmm_liquidity_law_109(
                presence_pct=Decimal("75.00"),
                min_presence_pct=Decimal("80.00"),
                is_compliant=True,
            )

        # Falha de spread
        with self.assertRaises(InvariantViolationError):
            assert_market_maker_pmm_liquidity_law_109(
                presence_pct=Decimal("90.00"),
                min_presence_pct=Decimal("80.00"),
                is_compliant=True,
                spread_pct=Decimal("2.50"),
                max_spread_pct=Decimal("1.50"),
            )

        # Falha de lote
        with self.assertRaises(InvariantViolationError):
            assert_market_maker_pmm_liquidity_law_109(
                presence_pct=Decimal("90.00"),
                min_presence_pct=Decimal("80.00"),
                is_compliant=True,
                bid_qty=Decimal("300"),
                min_bid_qty=Decimal("500"),
            )

        # Falha de desconto indevido
        with self.assertRaises(InvariantViolationError):
            assert_market_maker_pmm_liquidity_law_109(
                presence_pct=Decimal("50.00"),
                min_presence_pct=Decimal("80.00"),
                is_compliant=False,
                is_incentive_eligible=False,
                discount_amount=Decimal("5000.00"),
            )

    # ==========================================================================
    # 10. TESTES DE SERIALIZAÇÃO TO_DICT / FROM_DICT
    # ==========================================================================

    def test_roundtrip_to_dict_from_dict(self) -> None:
        """Testa idempotência da serialização to_dict() e restauração from_dict()."""
        # Carrega fixture
        fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "market_maker_pmm_engine.json"
        self.engine.load_from_fixture(fixture_path)

        data = self.engine.to_dict()
        engine_restored = MarketMakerPmmEngine.from_dict(data)

        self.assertEqual(len(engine_restored.contracts), len(self.engine.contracts))
        self.assertEqual(len(engine_restored.session_audits), len(self.engine.session_audits))
        self.assertEqual(len(engine_restored.monthly_settlements), len(self.engine.monthly_settlements))
        self.assertEqual(len(engine_restored.deaccreditation_audits), len(self.engine.deaccreditation_audits))

        # Re-verifica invariantes no motor restaurado
        inv = engine_restored.verify_invariants()
        self.assertTrue(all(inv.values()))


if __name__ == "__main__":
    unittest.main()
