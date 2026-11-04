"""Testes unitários e de integração do Motor de Tarifação B3 (FL-053).

Valida deterministicamente as regras de tarifação oficial da B3 no PUMA Trading
System e Câmara Clearing:
- Escadinha Regressiva de ADTV (Tiers 1 a 7)
- Desconto Especial de Day-Trade e Casamento Intradiário (Netting)
- Regimes Especiais para Não Residentes (CMN 4.373), Fundos (CVM 175) e Formadores de Mercado (PMM)
- Invariantes de conservação financeira e integridade do extrato diário
- Execução da fixture oficial fixtures/b3_tariff_calculations.json
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.b3_tariff_engine import (
    AdtvTier,
    B3TariffEngine,
    B3TariffError,
    DailyTariffSummary,
    InvestorCategory,
    TariffIntegrityError,
    TariffValidationError,
    TradeTariffExecution,
    TradingModality,
)


class TestB3TariffEngine(unittest.TestCase):
    """Bateria de testes do Motor de Tarifação Integrada B3."""

    def setUp(self) -> None:
        self.engine = B3TariffEngine()
        self.fixtures_path = ROOT_DIR / "fixtures" / "b3_tariff_calculations.json"

    # --------------------------------------------------------------------------
    # 1. Testes de Enumerações e Faixas de ADTV
    # --------------------------------------------------------------------------

    def test_adtv_tier_from_volume(self) -> None:
        """Verifica o correto enquadramento nas 7 faixas de ADTV."""
        # Tier 1: até R$ 1M
        self.assertEqual(AdtvTier.from_volume("0.00"), AdtvTier.TIER_1)
        self.assertEqual(AdtvTier.from_volume("500000.00"), AdtvTier.TIER_1)
        self.assertEqual(AdtvTier.from_volume("1000000.00"), AdtvTier.TIER_1)

        # Tier 2: > 1M a 5M
        self.assertEqual(AdtvTier.from_volume("1000000.01"), AdtvTier.TIER_2)
        self.assertEqual(AdtvTier.from_volume("5000000.00"), AdtvTier.TIER_2)

        # Tier 3: > 5M a 20M
        self.assertEqual(AdtvTier.from_volume("5000000.01"), AdtvTier.TIER_3)
        self.assertEqual(AdtvTier.from_volume("20000000.00"), AdtvTier.TIER_3)

        # Tier 4: > 20M a 50M
        self.assertEqual(AdtvTier.from_volume("20000000.01"), AdtvTier.TIER_4)
        self.assertEqual(AdtvTier.from_volume("50000000.00"), AdtvTier.TIER_4)

        # Tier 5: > 50M a 100M
        self.assertEqual(AdtvTier.from_volume("50000000.01"), AdtvTier.TIER_5)
        self.assertEqual(AdtvTier.from_volume("100000000.00"), AdtvTier.TIER_5)

        # Tier 6: > 100M a 300M
        self.assertEqual(AdtvTier.from_volume("100000000.01"), AdtvTier.TIER_6)
        self.assertEqual(AdtvTier.from_volume("300000000.00"), AdtvTier.TIER_6)

        # Tier 7: > 300M
        self.assertEqual(AdtvTier.from_volume("300000000.01"), AdtvTier.TIER_7)
        self.assertEqual(AdtvTier.from_volume("1000000000.00"), AdtvTier.TIER_7)

    def test_adtv_tier_negative_volume_raises(self) -> None:
        """Volume ADTV negativo deve disparar TariffValidationError."""
        with self.assertRaises(TariffValidationError):
            AdtvTier.from_volume("-10.00")

    def test_adtv_volume_limits(self) -> None:
        """Verifica a consistência dos limites das faixas de ADTV."""
        min_v, max_v = AdtvTier.TIER_1.volume_limits
        self.assertEqual(min_v, Decimal("0.00"))
        self.assertEqual(max_v, Decimal("1000000.00"))

        min_v7, max_v7 = AdtvTier.TIER_7.volume_limits
        self.assertEqual(min_v7, Decimal("300000000.01"))
        self.assertIsNone(max_v7)

    # --------------------------------------------------------------------------
    # 2. Testes de Cálculo Individual e Escadinha Regressiva
    # --------------------------------------------------------------------------

    def test_regular_trade_tier1_individual(self) -> None:
        """Verifica cálculo regular Tier 1 (0,0050% negociação + 0,0250% liquidação = 0,0300%)."""
        trade = self.engine.calculate_trade_tariff(
            trade_id="T-01",
            symbol="PETR4",
            side="BUY",
            quantity=1000,
            price="30.00",
            investor_id="INV-01",
            broker_id="008",
            modality=TradingModality.REGULAR,
            adtv_tier=AdtvTier.TIER_1,
            investor_category=InvestorCategory.INDIVIDUAL,
        )

        self.assertEqual(trade.financial_volume, Decimal("30000.00"))
        self.assertEqual(trade.trading_fee, Decimal("1.50"))  # 30.000 * 0,000050 = 1.50
        self.assertEqual(trade.settlement_fee, Decimal("7.50"))  # 30.000 * 0,000250 = 7.50
        self.assertEqual(trade.registration_fee, Decimal("0.00"))
        self.assertEqual(trade.total_tariff, Decimal("9.00"))  # 1.50 + 7.50 = 9.00
        self.assertEqual(trade.day_trade_savings, Decimal("0.00"))

    def test_day_trade_tier1_discount(self) -> None:
        """Verifica cálculo Day-Trade Tier 1 (0,0030% negociação + 0,0170% liquidação = 0,0200%)."""
        trade = self.engine.calculate_trade_tariff(
            trade_id="T-02",
            symbol="PETR4",
            side="BUY",
            quantity=1000,
            price="30.00",
            investor_id="INV-01",
            broker_id="008",
            modality=TradingModality.DAY_TRADE,
            adtv_tier=AdtvTier.TIER_1,
            investor_category=InvestorCategory.INDIVIDUAL,
        )

        self.assertEqual(trade.financial_volume, Decimal("30000.00"))
        self.assertEqual(trade.trading_fee, Decimal("0.90"))  # 30.000 * 0,000030 = 0.90
        self.assertEqual(trade.settlement_fee, Decimal("5.10"))  # 30.000 * 0,000170 = 5.10
        self.assertEqual(trade.total_tariff, Decimal("6.00"))  # 0.90 + 5.10 = 6.00
        # Economia em relação ao Regular (9.00 - 6.00 = 3.00)
        self.assertEqual(trade.day_trade_savings, Decimal("3.00"))

    def test_regressive_ladder_tier1_to_tier7(self) -> None:
        """Comprova que alíquotas caem monotonicamente do Tier 1 ao Tier 7."""
        volume = Decimal("1000000.00")
        prev_regular_total = Decimal("999999.99")
        prev_dt_total = Decimal("999999.99")

        for tier in AdtvTier:
            trade_reg = self.engine.calculate_trade_tariff(
                trade_id=f"T-REG-{tier.value}",
                symbol="VALE3",
                side="BUY",
                quantity=10000,
                price="100.00",
                investor_id="INV-TEST",
                broker_id="008",
                modality=TradingModality.REGULAR,
                adtv_tier=tier,
            )
            trade_dt = self.engine.calculate_trade_tariff(
                trade_id=f"T-DT-{tier.value}",
                symbol="VALE3",
                side="BUY",
                quantity=10000,
                price="100.00",
                investor_id="INV-TEST",
                broker_id="008",
                modality=TradingModality.DAY_TRADE,
                adtv_tier=tier,
            )

            self.assertLess(trade_reg.total_tariff, prev_regular_total)
            self.assertLess(trade_dt.total_tariff, prev_dt_total)
            self.assertLess(trade_dt.total_tariff, trade_reg.total_tariff)

            prev_regular_total = trade_reg.total_tariff
            prev_dt_total = trade_dt.total_tariff

    # --------------------------------------------------------------------------
    # 3. Testes de Regimes Especiais (Não Residente, Institucional, Formador)
    # --------------------------------------------------------------------------

    def test_non_resident_cmn_4373_discount(self) -> None:
        """Verifica desconto de 20% na taxa de liquidação para Não Residente (CMN 4.373)."""
        trade_regular_ind = self.engine.calculate_trade_tariff(
            trade_id="T-IND",
            symbol="PETR4",
            side="BUY",
            quantity=10000,
            price="40.00",
            investor_id="INV-IND",
            broker_id="008",
            modality=TradingModality.REGULAR,
            adtv_tier=AdtvTier.TIER_6,
            investor_category=InvestorCategory.INDIVIDUAL,
        )

        trade_non_resident = self.engine.calculate_trade_tariff(
            trade_id="T-NR",
            symbol="PETR4",
            side="BUY",
            quantity=10000,
            price="40.00",
            investor_id="INV-NR",
            broker_id="008",
            modality=TradingModality.REGULAR,
            adtv_tier=AdtvTier.TIER_6,
            investor_category=InvestorCategory.NON_RESIDENT,
        )

        # Emolumentos iguais (negociação não muda)
        self.assertEqual(trade_regular_ind.trading_fee, trade_non_resident.trading_fee)
        # Taxa de liquidação: NR deve pagar 80% da taxa regular
        # Tier 6 base settlement = 0.000150 * 400.000 = 60.00
        # NR settlement = 60.00 * 0.80 = 48.00
        self.assertEqual(trade_regular_ind.settlement_fee, Decimal("60.00"))
        self.assertEqual(trade_non_resident.settlement_fee, Decimal("48.00"))
        self.assertLess(trade_non_resident.total_tariff, trade_regular_ind.total_tariff)

    def test_market_maker_pmm_full_exemption(self) -> None:
        """Verifica isenção integral (100% de desconto) para Formador de Mercado (PMM)."""
        trade_pmm = self.engine.calculate_trade_tariff(
            trade_id="T-PMM",
            symbol="PETR4",
            side="BUY",
            quantity=50000,
            price="38.50",
            investor_id="INV-PMM",
            broker_id="008",
            modality=TradingModality.DAY_TRADE,
            adtv_tier=AdtvTier.TIER_1,
            investor_category=InvestorCategory.MARKET_MAKER,
        )

        self.assertEqual(trade_pmm.trading_fee, Decimal("0.00"))
        self.assertEqual(trade_pmm.settlement_fee, Decimal("0.00"))
        self.assertEqual(trade_pmm.registration_fee, Decimal("0.00"))
        self.assertEqual(trade_pmm.total_tariff, Decimal("0.00"))
        self.assertEqual(trade_pmm.day_trade_savings, Decimal("0.00"))

    # --------------------------------------------------------------------------
    # 4. Testes de Validação e Erros de Entrada
    # --------------------------------------------------------------------------

    def test_invalid_quantity_or_price_raises(self) -> None:
        """Quantidade ou preço não positivos devem disparar TariffValidationError."""
        with self.assertRaises(TariffValidationError):
            self.engine.calculate_trade_tariff(
                trade_id="T-ERR-1",
                symbol="PETR4",
                side="BUY",
                quantity=0,
                price="30.00",
                investor_id="INV-01",
                broker_id="008",
            )

        with self.assertRaises(TariffValidationError):
            self.engine.calculate_trade_tariff(
                trade_id="T-ERR-2",
                symbol="PETR4",
                side="BUY",
                quantity=100,
                price="-30.00",
                investor_id="INV-01",
                broker_id="008",
            )

    def test_invalid_side_raises(self) -> None:
        """Lado diferente de BUY/SELL/C/V dispara TariffValidationError."""
        with self.assertRaises(TariffValidationError):
            self.engine.calculate_trade_tariff(
                trade_id="T-ERR-3",
                symbol="PETR4",
                side="HOLD",
                quantity=100,
                price="30.00",
                investor_id="INV-01",
                broker_id="008",
            )

    # --------------------------------------------------------------------------
    # 5. Testes de Casamento Intradiário (Day-Trade Netting)
    # --------------------------------------------------------------------------

    def test_match_and_classify_day_trades_perfect_match(self) -> None:
        """Compra de 1.000 e venda de 1.000 no mesmo dia/corretora/ativo vira 100% Day-Trade."""
        trades = [
            {
                "trade_id": "T-BUY",
                "symbol": "PETR4",
                "side": "BUY",
                "quantity": 1000,
                "price": "38.00",
                "investor_id": "INV-NET",
                "broker_id": "008",
                "timestamp": "2026-11-03T10:00:00Z",
            },
            {
                "trade_id": "T-SELL",
                "symbol": "PETR4",
                "side": "SELL",
                "quantity": 1000,
                "price": "38.50",
                "investor_id": "INV-NET",
                "broker_id": "008",
                "timestamp": "2026-11-03T15:00:00Z",
            },
        ]

        classified = self.engine.match_and_classify_day_trades(trades)
        self.assertEqual(len(classified), 2)
        self.assertTrue(all(t.modality == TradingModality.DAY_TRADE for t in classified))

    def test_match_and_classify_day_trades_partial_split(self) -> None:
        """Compra de 3.000 e venda de 1.000 resulta em 1.000 DT e 2.000 REG."""
        trades = [
            {
                "trade_id": "T-BUY-3K",
                "symbol": "VALE3",
                "side": "BUY",
                "quantity": 3000,
                "price": "65.00",
                "investor_id": "INV-NET-2",
                "broker_id": "085",
                "timestamp": "2026-11-03T10:00:00Z",
            },
            {
                "trade_id": "T-SELL-1K",
                "symbol": "VALE3",
                "side": "SELL",
                "quantity": 1000,
                "price": "66.00",
                "investor_id": "INV-NET-2",
                "broker_id": "085",
                "timestamp": "2026-11-03T14:00:00Z",
            },
        ]

        classified = self.engine.match_and_classify_day_trades(trades)
        # O buy deve ter sido splitado em DT (1000) e REG (2000), sell é 1000 DT
        dt_trades = [t for t in classified if t.modality == TradingModality.DAY_TRADE]
        reg_trades = [t for t in classified if t.modality == TradingModality.REGULAR]

        self.assertEqual(sum(t.quantity for t in dt_trades if t.side == "BUY"), Decimal("1000"))
        self.assertEqual(sum(t.quantity for t in dt_trades if t.side == "SELL"), Decimal("1000"))
        self.assertEqual(sum(t.quantity for t in reg_trades), Decimal("2000"))

    def test_match_different_brokers_remain_regular(self) -> None:
        """Operações em corretoras distintas não configuram Day-Trade."""
        trades = [
            {
                "trade_id": "T-BRK-1",
                "symbol": "PETR4",
                "side": "BUY",
                "quantity": 1000,
                "price": "38.00",
                "investor_id": "INV-DIFF-BRK",
                "broker_id": "008",
            },
            {
                "trade_id": "T-BRK-2",
                "symbol": "PETR4",
                "side": "SELL",
                "quantity": 1000,
                "price": "38.50",
                "investor_id": "INV-DIFF-BRK",
                "broker_id": "085",
            },
        ]

        classified = self.engine.match_and_classify_day_trades(trades)
        self.assertEqual(len(classified), 2)
        self.assertTrue(all(t.modality == TradingModality.REGULAR for t in classified))

    # --------------------------------------------------------------------------
    # 6. Testes de Extrato Diário e Integridade de Fechamento
    # --------------------------------------------------------------------------

    def test_daily_summary_integrity_and_manipulation_detection(self) -> None:
        """Gera extrato diário e comprova que manipulações violam as leis de conservação."""
        t1 = self.engine.calculate_trade_tariff(
            trade_id="TR-1",
            symbol="PETR4",
            side="BUY",
            quantity=1000,
            price="35.00",
            investor_id="INV-01",
            broker_id="008",
            modality=TradingModality.REGULAR,
        )
        t2 = self.engine.calculate_trade_tariff(
            trade_id="TR-2",
            symbol="PETR4",
            side="SELL",
            quantity=1000,
            price="36.00",
            investor_id="INV-01",
            broker_id="008",
            modality=TradingModality.DAY_TRADE,
        )

        summary = self.engine.generate_daily_summary([t1, t2])
        self.assertTrue(summary.verify_integrity())

        # Teste de violação 1: alterar volume declarado
        corrupted_summary = DailyTariffSummary(
            trading_date=summary.trading_date,
            total_trades=summary.total_trades,
            total_financial_volume=summary.total_financial_volume + Decimal("100.00"),
            regular_financial_volume=summary.regular_financial_volume,
            day_trade_financial_volume=summary.day_trade_financial_volume,
            total_trading_fee=summary.total_trading_fee,
            total_settlement_fee=summary.total_settlement_fee,
            total_registration_fee=summary.total_registration_fee,
            total_tariff=summary.total_tariff,
            total_day_trade_savings=summary.total_day_trade_savings,
            trades_by_tier=summary.trades_by_tier,
            trades_by_modality=summary.trades_by_modality,
            volume_by_symbol=summary.volume_by_symbol,
            executions=summary.executions,
        )
        with self.assertRaises(TariffIntegrityError):
            corrupted_summary.verify_integrity()

        # Teste de violação 2: alterar tarifa total declarada
        corrupted_fee_summary = DailyTariffSummary(
            trading_date=summary.trading_date,
            total_trades=summary.total_trades,
            total_financial_volume=summary.total_financial_volume,
            regular_financial_volume=summary.regular_financial_volume,
            day_trade_financial_volume=summary.day_trade_financial_volume,
            total_trading_fee=summary.total_trading_fee,
            total_settlement_fee=summary.total_settlement_fee,
            total_registration_fee=summary.total_registration_fee,
            total_tariff=summary.total_tariff + Decimal("0.05"),
            total_day_trade_savings=summary.total_day_trade_savings,
            trades_by_tier=summary.trades_by_tier,
            trades_by_modality=summary.trades_by_modality,
            volume_by_symbol=summary.volume_by_symbol,
            executions=summary.executions,
        )
        with self.assertRaises(TariffIntegrityError):
            corrupted_fee_summary.verify_integrity()

    # --------------------------------------------------------------------------
    # 7. Teste de Integração com a Fixture Oficial
    # --------------------------------------------------------------------------

    def test_fixture_execution_and_closure_verification(self) -> None:
        """Executa a fixture b3_tariff_calculations.json e valida os totais esperados."""
        self.assertTrue(self.fixtures_path.exists(), f"Fixture não encontrada em {self.fixtures_path}")

        with open(self.fixtures_path, "r", encoding="utf-8") as f:
            raw_fixture = json.load(f)

        summary = self.engine.load_fixture_and_process(self.fixtures_path)

        # 1. Conferência de fechamento e integridade
        self.assertTrue(summary.verify_integrity())

        # 2. Conferência de negócios individuais contra expectativas da fixture
        expected_trades = raw_fixture["trades"]
        self.assertEqual(len(summary.executions), len(expected_trades))

        for actual, expected in zip(summary.executions, expected_trades):
            self.assertEqual(actual.trade_id, expected["trade_id"])
            self.assertEqual(str(actual.financial_volume), expected["expected_financial_volume"])
            self.assertEqual(str(actual.trading_fee), expected["expected_trading_fee"])
            self.assertEqual(str(actual.settlement_fee), expected["expected_settlement_fee"])
            self.assertEqual(str(actual.registration_fee), expected["expected_registration_fee"])
            self.assertEqual(str(actual.total_tariff), expected["expected_total_tariff"])
            self.assertEqual(str(actual.day_trade_savings), expected["expected_day_trade_savings"])

        # 3. Conferência dos totais diários consolidados
        expected_totals = raw_fixture["expected_daily_totals"]
        self.assertEqual(summary.total_trades, expected_totals["total_trades"])
        self.assertEqual(str(summary.total_financial_volume), expected_totals["total_financial_volume"])
        self.assertEqual(str(summary.regular_financial_volume), expected_totals["regular_financial_volume"])
        self.assertEqual(str(summary.day_trade_financial_volume), expected_totals["day_trade_financial_volume"])
        self.assertEqual(str(summary.total_trading_fee), expected_totals["total_trading_fee"])
        self.assertEqual(str(summary.total_settlement_fee), expected_totals["total_settlement_fee"])
        self.assertEqual(str(summary.total_registration_fee), expected_totals["total_registration_fee"])
        self.assertEqual(str(summary.total_tariff), expected_totals["total_tariff"])
        self.assertEqual(str(summary.total_day_trade_savings), expected_totals["total_day_trade_savings"])

    def test_json_serialization(self) -> None:
        """Verifica se os objetos geram dicionários e JSONs válidos."""
        summary = self.engine.load_fixture_and_process(self.fixtures_path)
        summary_dict = summary.to_dict()
        json_output = json.dumps(summary_dict, indent=2)
        self.assertIn("TRD-20261103-001", json_output)
        self.assertIn("total_financial_volume", json_output)


if __name__ == "__main__":
    unittest.main()
