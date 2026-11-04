"""Testes unitários e de integração do Programa de Formador de Mercado (PMM) B3 (FL-046).

Valida deterministicamente as obrigações de presença contínua, spread máximo,
profundidade mínima de lote, tempo de recomposição (regeneration time), apuração
diária de sessões e cálculo de descontos e isenções tarifárias da B3.
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

from src.market_maker_pmm import (
    AssetClass,
    BookQuoteSnapshot,
    MarketMakerPmmEngine,
    MarketPhase,
    PmmAssetContract,
    PmmAuditCalculationError,
    PmmContractNotFoundError,
    PmmMonthlyTariffReward,
    PmmObligationStatus,
    PmmSessionAuditResult,
    PmmValidationError,
    SnapshotAuditResult,
    parse_datetime,
)


class TestMarketMakerPmm(unittest.TestCase):
    """Bateria de testes das regras do Programa de Formador de Mercado B3."""

    def setUp(self) -> None:
        self.engine = MarketMakerPmmEngine()
        self.contract_prio = PmmAssetContract(
            contract_id="PMM-PRIO3-01",
            participant_id="008-UBS",
            symbol="PRIO3",
            asset_class=AssetClass.EQUITY_SMALL_CAP,
            min_presence_pct=Decimal("80.00"),
            max_spread_pct=Decimal("1.50"),
            min_bid_qty=Decimal("500"),
            min_ask_qty=Decimal("500"),
            max_regeneration_seconds=60,
            max_daily_spread_violations=5,
            base_discount_pct=Decimal("100.00"),
            min_monthly_compliant_ratio=Decimal("80.00"),
        )
        self.engine.register_contract(self.contract_prio)

    # --------------------------------------------------------------------------
    # 1. Testes de Contrato e Validações de Entrada
    # --------------------------------------------------------------------------

    def test_contract_validation_success_and_retrieval(self) -> None:
        """Verifica o registro e recuperação de contrato de PMM."""
        retrieved = self.engine.get_contract("008-UBS", "PRIO3")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.contract_id, "PMM-PRIO3-01")
        self.assertEqual(retrieved.min_presence_pct, Decimal("80.00"))

    def test_contract_validation_errors(self) -> None:
        """Verifica que parâmetros inválidos no contrato disparam PmmValidationError."""
        # Presença > 100%
        with self.assertRaises(PmmValidationError):
            PmmAssetContract(
                contract_id="C1",
                participant_id="P1",
                symbol="PRIO3",
                asset_class=AssetClass.EQUITY_SMALL_CAP,
                min_presence_pct=Decimal("105.00"),
                max_spread_pct=Decimal("1.50"),
                min_bid_qty=Decimal("100"),
                min_ask_qty=Decimal("100"),
            )

        # Spread negativo ou zero
        with self.assertRaises(PmmValidationError):
            PmmAssetContract(
                contract_id="C2",
                participant_id="P1",
                symbol="PRIO3",
                asset_class=AssetClass.EQUITY_SMALL_CAP,
                min_presence_pct=Decimal("80.00"),
                max_spread_pct=Decimal("0.00"),
                min_bid_qty=Decimal("100"),
                min_ask_qty=Decimal("100"),
            )

        # Lotes negativos
        with self.assertRaises(PmmValidationError):
            PmmAssetContract(
                contract_id="C3",
                participant_id="P1",
                symbol="PRIO3",
                asset_class=AssetClass.EQUITY_SMALL_CAP,
                min_presence_pct=Decimal("80.00"),
                max_spread_pct=Decimal("1.50"),
                min_bid_qty=Decimal("-50"),
                min_ask_qty=Decimal("100"),
            )

    def test_snapshot_duration_validation(self) -> None:
        """Verifica que snapshot com duração zero ou negativa dispara erro."""
        with self.assertRaises(PmmValidationError):
            BookQuoteSnapshot(
                timestamp=datetime.now(timezone.utc),
                symbol="PRIO3",
                participant_id="008-UBS",
                duration_seconds=Decimal("0.0"),
            )

    # --------------------------------------------------------------------------
    # 2. Avaliação Determinística de Snapshots de Livro
    # --------------------------------------------------------------------------

    def test_snapshot_full_compliance(self) -> None:
        """Snapshot com ambas as pontas ativas, spread <= max e lote >= min é COMPLIANT."""
        now = datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc)
        snp = BookQuoteSnapshot(
            timestamp=now,
            symbol="PRIO3",
            participant_id="008-UBS",
            bid_price=Decimal("40.00"),
            bid_qty=Decimal("500"),
            ask_price=Decimal("40.50"),  # Spread = (0.50 / 40.00) * 100 = 1.25% <= 1.50%
            ask_qty=Decimal("600"),
            phase=MarketPhase.CONTINUOUS,
        )
        res = self.engine.evaluate_snapshot(snp, self.contract_prio)
        self.assertEqual(res.status, PmmObligationStatus.COMPLIANT)
        self.assertTrue(res.is_valid_presence)
        self.assertTrue(res.is_eligible_phase)
        self.assertEqual(res.spread_pct, Decimal("1.2500"))

    def test_snapshot_spread_violation(self) -> None:
        """Snapshot com spread > max_spread_pct é SPREAD_VIOLATION."""
        now = datetime(2026, 11, 4, 10, 5, 0, tzinfo=timezone.utc)
        snp = BookQuoteSnapshot(
            timestamp=now,
            symbol="PRIO3",
            participant_id="008-UBS",
            bid_price=Decimal("40.00"),
            bid_qty=Decimal("500"),
            ask_price=Decimal("40.80"),  # Spread = (0.80 / 40.00) * 100 = 2.00% > 1.50%
            ask_qty=Decimal("500"),
            phase=MarketPhase.CONTINUOUS,
        )
        res = self.engine.evaluate_snapshot(snp, self.contract_prio)
        self.assertEqual(res.status, PmmObligationStatus.SPREAD_VIOLATION)
        self.assertFalse(res.is_valid_presence)
        self.assertTrue(res.is_eligible_phase)
        self.assertEqual(res.spread_pct, Decimal("2.0000"))

    def test_snapshot_depth_violation(self) -> None:
        """Snapshot com lote abaixo do mínimo é DEPTH_VIOLATION."""
        now = datetime(2026, 11, 4, 10, 10, 0, tzinfo=timezone.utc)
        snp = BookQuoteSnapshot(
            timestamp=now,
            symbol="PRIO3",
            participant_id="008-UBS",
            bid_price=Decimal("40.00"),
            bid_qty=Decimal("400"),  # min é 500
            ask_price=Decimal("40.40"),  # spread = 1.00% ok
            ask_qty=Decimal("500"),
            phase=MarketPhase.CONTINUOUS,
        )
        res = self.engine.evaluate_snapshot(snp, self.contract_prio)
        self.assertEqual(res.status, PmmObligationStatus.DEPTH_VIOLATION)
        self.assertFalse(res.is_valid_presence)

    def test_snapshot_spread_and_depth_violation(self) -> None:
        """Snapshot com spread > teto e profundidade < piso simultaneamente."""
        now = datetime(2026, 11, 4, 10, 15, 0, tzinfo=timezone.utc)
        snp = BookQuoteSnapshot(
            timestamp=now,
            symbol="PRIO3",
            participant_id="008-UBS",
            bid_price=Decimal("40.00"),
            bid_qty=Decimal("200"),
            ask_price=Decimal("41.00"),  # spread = 2.50% > 1.50%
            ask_qty=Decimal("200"),
            phase=MarketPhase.CONTINUOUS,
        )
        res = self.engine.evaluate_snapshot(snp, self.contract_prio)
        self.assertEqual(res.status, PmmObligationStatus.SPREAD_AND_DEPTH_VIOLATION)
        self.assertFalse(res.is_valid_presence)

    def test_snapshot_crossed_book_violation(self) -> None:
        """Livro com Ask <= Bid (cruzado ou nulo) é classificado como violação crítica."""
        now = datetime(2026, 11, 4, 10, 20, 0, tzinfo=timezone.utc)
        snp = BookQuoteSnapshot(
            timestamp=now,
            symbol="PRIO3",
            participant_id="008-UBS",
            bid_price=Decimal("40.50"),
            bid_qty=Decimal("500"),
            ask_price=Decimal("40.00"),
            ask_qty=Decimal("500"),
            phase=MarketPhase.CONTINUOUS,
        )
        res = self.engine.evaluate_snapshot(snp, self.contract_prio)
        self.assertEqual(res.status, PmmObligationStatus.SPREAD_VIOLATION)
        self.assertFalse(res.is_valid_presence)

    def test_snapshot_missing_bid_or_ask_without_regeneration(self) -> None:
        """Ausência de ponta sem justificativa de trade recente é violação."""
        now = datetime(2026, 11, 4, 10, 25, 0, tzinfo=timezone.utc)
        # Falta Bid
        snp1 = BookQuoteSnapshot(
            timestamp=now,
            symbol="PRIO3",
            participant_id="008-UBS",
            bid_price=None,
            ask_price=Decimal("40.50"),
            ask_qty=Decimal("500"),
            phase=MarketPhase.CONTINUOUS,
        )
        res1 = self.engine.evaluate_snapshot(snp1, self.contract_prio)
        self.assertEqual(res1.status, PmmObligationStatus.MISSING_BID)
        self.assertFalse(res1.is_valid_presence)

        # Falta Ask
        snp2 = BookQuoteSnapshot(
            timestamp=now,
            symbol="PRIO3",
            participant_id="008-UBS",
            bid_price=Decimal("40.50"),
            bid_qty=Decimal("500"),
            ask_price=None,
            phase=MarketPhase.CONTINUOUS,
        )
        res2 = self.engine.evaluate_snapshot(snp2, self.contract_prio)
        self.assertEqual(res2.status, PmmObligationStatus.MISSING_ASK)
        self.assertFalse(res2.is_valid_presence)

        # Ausência total
        snp3 = BookQuoteSnapshot(
            timestamp=now,
            symbol="PRIO3",
            participant_id="008-UBS",
            phase=MarketPhase.CONTINUOUS,
        )
        res3 = self.engine.evaluate_snapshot(snp3, self.contract_prio)
        self.assertEqual(res3.status, PmmObligationStatus.ABSENT)
        self.assertFalse(res3.is_valid_presence)

    def test_regeneration_time_grace_period(self) -> None:
        """Ausência ou lote reduzido dentro de 60s pós-execução é REGENERATING e conta como presença válida."""
        trade_time = datetime(2026, 11, 4, 10, 30, 0, tzinfo=timezone.utc)
        snapshot_time = trade_time + timedelta(seconds=25)  # 25s decorridos <= 60s

        # Ausência total de ofertas durante recomposição
        snp = BookQuoteSnapshot(
            timestamp=snapshot_time,
            symbol="PRIO3",
            participant_id="008-UBS",
            last_trade_timestamp=trade_time,
            phase=MarketPhase.CONTINUOUS,
        )
        res = self.engine.evaluate_snapshot(snp, self.contract_prio)
        self.assertEqual(res.status, PmmObligationStatus.REGENERATING)
        self.assertTrue(res.is_valid_presence)
        self.assertTrue(res.is_eligible_phase)
        self.assertEqual(res.regeneration_elapsed_seconds, Decimal("25"))

        # Recomposição expirada (> 60s)
        expired_time = trade_time + timedelta(seconds=61)
        snp_exp = BookQuoteSnapshot(
            timestamp=expired_time,
            symbol="PRIO3",
            participant_id="008-UBS",
            last_trade_timestamp=trade_time,
            phase=MarketPhase.CONTINUOUS,
        )
        res_exp = self.engine.evaluate_snapshot(snp_exp, self.contract_prio)
        self.assertEqual(res_exp.status, PmmObligationStatus.ABSENT)
        self.assertFalse(res_exp.is_valid_presence)

    def test_market_phase_auction_exclusion(self) -> None:
        """Leilões (abertura, fechamento, volatilidade) são excluídos da contagem de presença contínua."""
        now = datetime(2026, 11, 4, 9, 50, 0, tzinfo=timezone.utc)
        for phase in (
            MarketPhase.OPENING_AUCTION,
            MarketPhase.CLOSING_AUCTION,
            MarketPhase.VOLATILITY_AUCTION,
            MarketPhase.TRADING_HALT,
            MarketPhase.CLOSED,
        ):
            snp = BookQuoteSnapshot(
                timestamp=now,
                symbol="PRIO3",
                participant_id="008-UBS",
                phase=phase,
            )
            res = self.engine.evaluate_snapshot(snp, self.contract_prio)
            self.assertEqual(res.status, PmmObligationStatus.AUCTION_EXCLUDED)
            self.assertFalse(res.is_eligible_phase)
            self.assertFalse(res.is_valid_presence)

    def test_max_spread_ticks_enforcement(self) -> None:
        """Verifica a regra opcional de spread máximo em ticks absolutos."""
        contract_ticks = PmmAssetContract(
            contract_id="PMM-CCM-01",
            participant_id="099-XP",
            symbol="CCMF26",
            asset_class=AssetClass.COMMODITY_FUTURE,
            min_presence_pct=Decimal("85.00"),
            max_spread_pct=Decimal("1.00"),  # 1%
            min_bid_qty=Decimal("10"),
            min_ask_qty=Decimal("10"),
            max_spread_ticks=Decimal("0.10"),  # Teto absoluto de R$ 0,10
        )
        self.engine.register_contract(contract_ticks)

        now = datetime(2026, 11, 4, 11, 0, 0, tzinfo=timezone.utc)
        # Bid = 60.00, Ask = 60.15 -> Spread% = (0.15/60)*100 = 0.25% (ok no %), mas ticks = 0.15 > 0.10 (violação)
        snp = BookQuoteSnapshot(
            timestamp=now,
            symbol="CCMF26",
            participant_id="099-XP",
            bid_price=Decimal("60.00"),
            bid_qty=Decimal("10"),
            ask_price=Decimal("60.15"),
            ask_qty=Decimal("10"),
            phase=MarketPhase.CONTINUOUS,
        )
        res = self.engine.evaluate_snapshot(snp, contract_ticks)
        self.assertEqual(res.status, PmmObligationStatus.SPREAD_VIOLATION)
        self.assertFalse(res.is_valid_presence)

    # --------------------------------------------------------------------------
    # 3. Auditoria Diária de Pregão (PmmSessionAuditResult)
    # --------------------------------------------------------------------------

    def test_audit_session_compliant(self) -> None:
        """Sessão com 90% de presença e poucas violações é considerada is_compliant = True."""
        base_time = datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc)
        snapshots = [
            # 1. Leilão de abertura (300s) -> excluído
            BookQuoteSnapshot(
                timestamp=base_time,
                symbol="PRIO3",
                participant_id="008-UBS",
                phase=MarketPhase.OPENING_AUCTION,
                duration_seconds=Decimal("300.0"),
            ),
            # 2. Negociação contínua válida (900s)
            BookQuoteSnapshot(
                timestamp=base_time + timedelta(seconds=300),
                symbol="PRIO3",
                participant_id="008-UBS",
                bid_price=Decimal("40.00"),
                bid_qty=Decimal("500"),
                ask_price=Decimal("40.50"),
                ask_qty=Decimal("500"),
                phase=MarketPhase.CONTINUOUS,
                duration_seconds=Decimal("900.0"),
            ),
            # 3. Violação pontual de spread (100s)
            BookQuoteSnapshot(
                timestamp=base_time + timedelta(seconds=1200),
                symbol="PRIO3",
                participant_id="008-UBS",
                bid_price=Decimal("40.00"),
                bid_qty=Decimal("500"),
                ask_price=Decimal("41.00"),
                ask_qty=Decimal("500"),
                phase=MarketPhase.CONTINUOUS,
                duration_seconds=Decimal("100.0"),
            ),
        ]
        # Total contínuo = 900 + 100 = 1000s
        # Válido = 900s -> Presença = 900 / 1000 = 90.0000% >= 80% meta
        result = self.engine.audit_session(snapshots, self.contract_prio)
        self.assertTrue(result.is_compliant)
        self.assertEqual(result.eligible_continuous_seconds, Decimal("1000.0"))
        self.assertEqual(result.valid_presence_seconds, Decimal("900.0"))
        self.assertEqual(result.presence_pct, Decimal("90.0000"))
        self.assertEqual(result.spread_violation_count, 1)

    def test_audit_session_failed_due_to_low_presence(self) -> None:
        """Sessão com presença abaixo de 80% é reprovada (is_compliant = False)."""
        base_time = datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc)
        snapshots = [
            # 700s válido
            BookQuoteSnapshot(
                timestamp=base_time,
                symbol="PRIO3",
                participant_id="008-UBS",
                bid_price=Decimal("40.00"),
                bid_qty=Decimal("500"),
                ask_price=Decimal("40.50"),
                ask_qty=Decimal("500"),
                phase=MarketPhase.CONTINUOUS,
                duration_seconds=Decimal("700.0"),
            ),
            # 300s ausente
            BookQuoteSnapshot(
                timestamp=base_time + timedelta(seconds=700),
                symbol="PRIO3",
                participant_id="008-UBS",
                phase=MarketPhase.CONTINUOUS,
                duration_seconds=Decimal("300.0"),
            ),
        ]
        # Presença = 700 / 1000 = 70.0000% < 80%
        result = self.engine.audit_session(snapshots, self.contract_prio)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.presence_pct, Decimal("70.0000"))
        self.assertTrue(any("abaixo da meta" in r for r in result.failure_reasons))

    def test_audit_session_failed_due_to_excessive_spread_violations(self) -> None:
        """Sessão que cumpre presença mas excede o teto de violações de spread é reprovada."""
        base_time = datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc)
        snapshots = []
        # 10 violações de 5 segundos (50s) -> excede teto de 5 violações do contrato PRIO3
        for i in range(8):
            snapshots.append(
                BookQuoteSnapshot(
                    timestamp=base_time + timedelta(seconds=i * 10),
                    symbol="PRIO3",
                    participant_id="008-UBS",
                    bid_price=Decimal("40.00"),
                    bid_qty=Decimal("500"),
                    ask_price=Decimal("41.50"),  # spread largo
                    ask_qty=Decimal("500"),
                    phase=MarketPhase.CONTINUOUS,
                    duration_seconds=Decimal("5.0"),
                )
            )
        # 960 segundos válidos
        snapshots.append(
            BookQuoteSnapshot(
                timestamp=base_time + timedelta(seconds=100),
                symbol="PRIO3",
                participant_id="008-UBS",
                bid_price=Decimal("40.00"),
                bid_qty=Decimal("500"),
                ask_price=Decimal("40.40"),
                ask_qty=Decimal("500"),
                phase=MarketPhase.CONTINUOUS,
                duration_seconds=Decimal("960.0"),
            )
        )
        # Total contínuo = 40 + 960 = 1000s
        # Presença = 960 / 1000 = 96.0000% (alta!), porém com 8 violações de spread > 5 permitidas
        result = self.engine.audit_session(snapshots, self.contract_prio)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.spread_violation_count, 8)
        self.assertTrue(any("Violações de spread" in r for r in result.failure_reasons))

    # --------------------------------------------------------------------------
    # 4. Apuração Mensal e Descontos Tarifários (PmmMonthlyTariffReward)
    # --------------------------------------------------------------------------

    def test_monthly_tariff_reward_full_discount_invariant(self) -> None:
        """Verifica a elegibilidade mensal e a conservação financeira das tarifas com 100% de desconto."""
        # 5 sessões conformes com presença média de 92%
        sessions = [
            PmmSessionAuditResult(
                session_date=f"2026-11-0{i}",
                symbol="PRIO3",
                participant_id="008-UBS",
                total_session_seconds=Decimal("1000.0"),
                eligible_continuous_seconds=Decimal("1000.0"),
                valid_presence_seconds=Decimal("920.0"),
                presence_pct=Decimal("92.0000"),
                required_presence_pct=Decimal("80.00"),
                spread_violation_count=0,
                depth_violation_count=0,
                missing_quote_count=0,
                regeneration_count=0,
                is_compliant=True,
            )
            for i in range(1, 6)
        ]

        gross_trading = Decimal("30000.00")
        gross_clearing = Decimal("15000.00")

        reward = self.engine.calculate_monthly_tariffs(
            session_results=sessions,
            gross_trading_fees=gross_trading,
            gross_clearing_fees=gross_clearing,
            contract=self.contract_prio,
            month_reference="2026-11",
            use_tiered_discount=False,
        )

        self.assertTrue(reward.is_reward_eligible)
        self.assertEqual(reward.compliance_ratio_pct, Decimal("100.0000"))
        self.assertEqual(reward.average_presence_pct, Decimal("92.0000"))
        self.assertEqual(reward.discount_pct_applied, Decimal("100.00"))

        # Invariante financeira exata
        self.assertEqual(reward.total_gross_fees, Decimal("45000.00"))
        self.assertEqual(reward.discount_amount, Decimal("45000.00"))
        self.assertEqual(reward.net_fees_payable, Decimal("0.00"))
        self.assertEqual(reward.total_gross_fees, reward.discount_amount + reward.net_fees_payable)

    def test_monthly_tariff_reward_tiered_discount(self) -> None:
        """Verifica os tiers de desconto progressivo (Ouro 100%, Prata 90%, Bronze 75%)."""
        gross_total = Decimal("10000.00")

        # Caso Silver (Presença média de 91%)
        sessions_silver = [
            PmmSessionAuditResult(
                session_date="2026-11-01",
                symbol="PRIO3",
                participant_id="008-UBS",
                total_session_seconds=Decimal("1000.0"),
                eligible_continuous_seconds=Decimal("1000.0"),
                valid_presence_seconds=Decimal("910.0"),
                presence_pct=Decimal("91.0000"),
                required_presence_pct=Decimal("80.00"),
                spread_violation_count=0,
                depth_violation_count=0,
                missing_quote_count=0,
                regeneration_count=0,
                is_compliant=True,
            )
        ]
        reward_silver = self.engine.calculate_monthly_tariffs(
            session_results=sessions_silver,
            gross_trading_fees=Decimal("7000.00"),
            gross_clearing_fees=Decimal("3000.00"),
            contract=self.contract_prio,
            use_tiered_discount=True,
        )
        self.assertEqual(reward_silver.discount_pct_applied, Decimal("90.00"))
        self.assertEqual(reward_silver.discount_amount, Decimal("9000.00"))
        self.assertEqual(reward_silver.net_fees_payable, Decimal("1000.00"))

        # Caso Bronze (Presença média de 82%)
        sessions_bronze = [
            PmmSessionAuditResult(
                session_date="2026-11-01",
                symbol="PRIO3",
                participant_id="008-UBS",
                total_session_seconds=Decimal("1000.0"),
                eligible_continuous_seconds=Decimal("1000.0"),
                valid_presence_seconds=Decimal("820.0"),
                presence_pct=Decimal("82.0000"),
                required_presence_pct=Decimal("80.00"),
                spread_violation_count=0,
                depth_violation_count=0,
                missing_quote_count=0,
                regeneration_count=0,
                is_compliant=True,
            )
        ]
        reward_bronze = self.engine.calculate_monthly_tariffs(
            session_results=sessions_bronze,
            gross_trading_fees=Decimal("7000.00"),
            gross_clearing_fees=Decimal("3000.00"),
            contract=self.contract_prio,
            use_tiered_discount=True,
        )
        self.assertEqual(reward_bronze.discount_pct_applied, Decimal("75.00"))
        self.assertEqual(reward_bronze.discount_amount, Decimal("7500.00"))
        self.assertEqual(reward_bronze.net_fees_payable, Decimal("2500.00"))

    def test_monthly_tariff_ineligible_no_discount(self) -> None:
        """Formador inadimplente não faz jus a nenhum desconto (0%)."""
        sessions_failed = [
            PmmSessionAuditResult(
                session_date="2026-11-01",
                symbol="PRIO3",
                participant_id="008-UBS",
                total_session_seconds=Decimal("1000.0"),
                eligible_continuous_seconds=Decimal("1000.0"),
                valid_presence_seconds=Decimal("600.0"),
                presence_pct=Decimal("60.0000"),
                required_presence_pct=Decimal("80.00"),
                spread_violation_count=0,
                depth_violation_count=0,
                missing_quote_count=0,
                regeneration_count=0,
                is_compliant=False,
            )
        ]
        reward = self.engine.calculate_monthly_tariffs(
            session_results=sessions_failed,
            gross_trading_fees=Decimal("5000.00"),
            gross_clearing_fees=Decimal("2000.00"),
            contract=self.contract_prio,
        )
        self.assertFalse(reward.is_reward_eligible)
        self.assertEqual(reward.discount_pct_applied, Decimal("0.00"))
        self.assertEqual(reward.discount_amount, Decimal("0.00"))
        self.assertEqual(reward.net_fees_payable, Decimal("7000.00"))

    # --------------------------------------------------------------------------
    # 5. Carregamento e Auditoria Completa da Fixture Oficial
    # --------------------------------------------------------------------------

    def test_load_official_fixture_and_report_export(self) -> None:
        """Carrega fixtures/market_maker_pmm_audit.json e valida a apuração ponta a ponta."""
        fixture_path = ROOT_DIR / "fixtures" / "market_maker_pmm_audit.json"
        self.assertTrue(fixture_path.exists(), f"Arquivo não encontrado: {fixture_path}")

        session_results, monthly_rewards = self.engine.load_fixtures_and_audit(fixture_path)

        self.assertGreaterEqual(len(session_results), 3)
        self.assertGreaterEqual(len(monthly_rewards), 2)

        # Valida que todos os cálculos de tarifas mantêm a invariante de conservação
        for rew in monthly_rewards:
            self.assertEqual(
                rew.total_gross_fees,
                rew.discount_amount + rew.net_fees_payable,
                f"Quebra da invariante tarifária para {rew.participant_id} / {rew.symbol}",
            )

        # Exportação de relatório
        report = self.engine.export_audit_report(session_results, monthly_rewards)
        self.assertIn("report_generated_at", report)
        self.assertEqual(report["sessions_count"], len(session_results))
        self.assertEqual(report["monthly_rewards_count"], len(monthly_rewards))


if __name__ == "__main__":
    unittest.main()
