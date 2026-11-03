"""Testes unitários para o módulo de Derivativos Listados BM&F e Ajuste Diário (FL-032)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from src.bmf_futures_settlement import (
    BmfFuturesCalculationError,
    BmfFuturesSettlementEngine,
    BmfFuturesValidationError,
    DailySettlementResult,
    FutureContractFamily,
    FuturePosition,
    PortfolioSettlementSummary,
    PositionSide,
    PositionType,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "bmf_futures_positions.json"
)


class TestBmfFuturesSettlement(unittest.TestCase):
    """Suíte de testes determinísticos para PU e Ajuste Diário de Futuros B3."""

    def setUp(self) -> None:
        self.engine = BmfFuturesSettlementEngine()
        self.trade_date = date(2026, 11, 3)
        self.settlement_date = date(2026, 11, 4)

    def test_contract_family_multipliers_and_inversion(self) -> None:
        """Verifica os multiplicadores financeiros por ponto e sentido de taxa/preço."""
        self.assertEqual(
            FutureContractFamily.DI1.point_multiplier, Decimal("1.00")
        )
        self.assertTrue(FutureContractFamily.DI1.is_rate_inverted)

        self.assertEqual(
            FutureContractFamily.DOL.point_multiplier, Decimal("50.00")
        )
        self.assertFalse(FutureContractFamily.DOL.is_rate_inverted)

        self.assertEqual(
            FutureContractFamily.WDO.point_multiplier, Decimal("10.00")
        )
        self.assertEqual(
            FutureContractFamily.IND.point_multiplier, Decimal("1.00")
        )
        self.assertEqual(
            FutureContractFamily.WIN.point_multiplier, Decimal("0.20")
        )

    def test_ticker_parsing_and_validation(self) -> None:
        """Valida o reconhecimento de tickers B3 (DI1F27, DOLZ26, WDOZ26, INDZ26, WINZ26)."""
        self.assertEqual(
            FutureContractFamily.from_ticker("DI1F27"), FutureContractFamily.DI1
        )
        self.assertEqual(
            FutureContractFamily.from_ticker("DOLZ26"), FutureContractFamily.DOL
        )
        self.assertEqual(
            FutureContractFamily.from_ticker("WDOZ26"), FutureContractFamily.WDO
        )
        self.assertEqual(
            FutureContractFamily.from_ticker("INDZ26"), FutureContractFamily.IND
        )
        self.assertEqual(
            FutureContractFamily.from_ticker("WINZ26"), FutureContractFamily.WIN
        )

        with self.assertRaises(BmfFuturesValidationError):
            FutureContractFamily.from_ticker("INVALID99")

        with self.assertRaises(BmfFuturesValidationError):
            FutureContractFamily.from_ticker("DI1A27")  # Mês 'A' não existe no padrão B3

    def test_calculate_di1_pu_deterministic(self) -> None:
        """Testa o cálculo de PU = 100.000 / (1 + taxa/100)^(DU/252) em 2 e 6 casas."""
        # No vencimento (DU = 0), PU = 100.000,00 independente da taxa
        pu_maturity = self.engine.calculate_di1_pu("12.50", business_days=0)
        self.assertEqual(pu_maturity, Decimal("100000.00"))

        # Em 1 ano exatamente (DU = 252) a 25,00% a.a.: PU = 100.000 / 1,25 = 80.000,00
        pu_one_year = self.engine.calculate_di1_pu("25.00", business_days=252)
        self.assertEqual(pu_one_year, Decimal("80000.00"))

        # Para DI1F27 com DU = 42 e taxa 11,50% a.a.:
        # 100000 / (1.1150 ** (42/252)) = 98202.118178... -> 98202.12
        pu_1150 = self.engine.calculate_di1_pu("11.50", business_days=42, precision=2)
        self.assertEqual(pu_1150, Decimal("98202.12"))

        pu_1150_hp = self.engine.calculate_di1_pu(
            "11.50", business_days=42, precision=6
        )
        self.assertEqual(pu_1150_hp, Decimal("98202.118178"))

        # Para DI1F27 com DU = 42 e taxa 11,65% a.a.:
        # 100000 / (1.1165 ** (42/252)) = 98180.117028... -> 98180.12
        pu_1165 = self.engine.calculate_di1_pu("11.65", business_days=42, precision=2)
        self.assertEqual(pu_1165, Decimal("98180.12"))
        self.assertLess(pu_1165, pu_1150)  # Taxa maior -> PU menor

    def test_di1_carried_position_rate_inversion(self) -> None:
        """Verifica que Comprado em Taxa (Vendido em PU) ganha quando a taxa sobe (PU cai)."""
        pos_long_rate = FuturePosition(
            position_id="TEST-DI1-LONG-RATE",
            ticker="DI1F27",
            contract_family=FutureContractFamily.DI1,
            position_type=PositionType.CARRIED,
            side=PositionSide.BUY,  # Comprado em Taxa = Vendido em PU
            quantity=100,
            business_days_to_maturity=42,
            previous_settlement_rate_pct=Decimal("11.50"),
            current_settlement_rate_pct=Decimal("11.65"),
        )
        res_long = self.engine.calculate_position_settlement(
            pos_long_rate,
            trade_date=self.trade_date,
            settlement_date=self.settlement_date,
        )
        self.assertEqual(res_long.pu_direction, "SHORT_PU")
        self.assertEqual(res_long.reference_value, Decimal("98202.12"))
        self.assertEqual(res_long.settlement_value, Decimal("98180.12"))
        # Ganho de (98202.12 - 98180.12) = +22.00 pontos de PU * 100 contratos = +R$ 2.200,00
        self.assertEqual(res_long.variation_points, Decimal("22.00"))
        self.assertEqual(res_long.settlement_amount_brl, Decimal("2200.00"))
        self.assertTrue(res_long.is_credit)

        # Posição simétrica Vendida em Taxa (Comprada em PU) perde com a alta de juros
        pos_short_rate = FuturePosition(
            position_id="TEST-DI1-SHORT-RATE",
            ticker="DI1F27",
            contract_family=FutureContractFamily.DI1,
            position_type=PositionType.CARRIED,
            side=PositionSide.SELL,  # Vendido em Taxa = Comprado em PU
            quantity=100,
            business_days_to_maturity=42,
            previous_settlement_rate_pct=Decimal("11.50"),
            current_settlement_rate_pct=Decimal("11.65"),
        )
        res_short = self.engine.calculate_position_settlement(
            pos_short_rate,
            trade_date=self.trade_date,
            settlement_date=self.settlement_date,
        )
        self.assertEqual(res_short.pu_direction, "LONG_PU")
        self.assertEqual(res_short.settlement_amount_brl, Decimal("-2200.00"))
        self.assertFalse(res_short.is_credit)

    def test_di1_cdi_overnight_correction(self) -> None:
        """Verifica a correção do PU de D-1 pelo fator diário do CDI over."""
        # PU D-1 = 98.000,00 corrigido por 1 dia útil a CDI = 25,00% a.a.
        corrected = self.engine.correct_di1_previous_pu(
            Decimal("98000.00"), cdi_overnight_rate_pct=Decimal("25.00"), precision=2
        )
        self.assertGreater(corrected, Decimal("98000.00"))

        pos = FuturePosition(
            position_id="TEST-DI1-CDI-CORR",
            ticker="DI1F27",
            contract_family=FutureContractFamily.DI1,
            position_type=PositionType.CARRIED,
            side=PositionSide.BUY,
            quantity=10,
            business_days_to_maturity=42,
            previous_settlement_pu=Decimal("98000.00"),
            current_settlement_pu=Decimal("98050.00"),
            apply_cdi_correction=True,
        )
        res = self.engine.calculate_position_settlement(
            pos,
            trade_date=self.trade_date,
            settlement_date=self.settlement_date,
            cdi_overnight_rate_pct=Decimal("25.00"),
        )
        self.assertEqual(res.reference_value, corrected)

    def test_dol_and_wdo_settlement(self) -> None:
        """Testa o ajuste diário de Dólar Cheio (DOL = R$ 50/pt) e Mini Dólar (WDO = R$ 10/pt)."""
        # DOLZ26: Comprado 10 contratos, PA_d-1 = 5780.00, PA_d = 5805.50 (+25.50 pts)
        # Ajuste = 25.50 * 50.00 * 10 = +R$ 12.750,00
        pos_dol = FuturePosition(
            position_id="TEST-DOL-001",
            ticker="DOLZ26",
            contract_family=FutureContractFamily.DOL,
            position_type=PositionType.CARRIED,
            side=PositionSide.BUY,
            quantity=10,
            previous_settlement_price=Decimal("5780.00"),
            current_settlement_price=Decimal("5805.50"),
        )
        res_dol = self.engine.calculate_position_settlement(
            pos_dol, trade_date=self.trade_date
        )
        self.assertEqual(res_dol.variation_points, Decimal("25.50"))
        self.assertEqual(res_dol.unit_settlement_brl, Decimal("1275.00"))
        self.assertEqual(res_dol.settlement_amount_brl, Decimal("12750.00"))
        self.assertEqual(res_dol.settlement_date, date(2026, 11, 4))

        # WDOZ26: Vendido 25 contratos, PA_d-1 = 5780.00, PA_d = 5805.50 (-25.50 pts para vendido)
        # Ajuste = -25.50 * 10.00 * 25 = -R$ 6.375,00
        pos_wdo = FuturePosition(
            position_id="TEST-WDO-001",
            ticker="WDOZ26",
            contract_family=FutureContractFamily.WDO,
            position_type=PositionType.CARRIED,
            side=PositionSide.SELL,
            quantity=25,
            previous_settlement_price=Decimal("5780.00"),
            current_settlement_price=Decimal("5805.50"),
        )
        res_wdo = self.engine.calculate_position_settlement(
            pos_wdo, trade_date=self.trade_date
        )
        self.assertEqual(res_wdo.variation_points, Decimal("-25.50"))
        self.assertEqual(res_wdo.unit_settlement_brl, Decimal("-255.00"))
        self.assertEqual(res_wdo.settlement_amount_brl, Decimal("-6375.00"))

    def test_ind_and_win_settlement_and_day_trade(self) -> None:
        """Testa o ajuste diário de Índice Cheio (IND = R$ 1,00/pt) e Mini Índice (WIN = R$ 0,20/pt)."""
        # INDZ26: Comprado 5 contratos, PA_d-1 = 129500, PA_d = 130250 (+750 pts)
        # Ajuste = 750 * 1.00 * 5 = +R$ 3.750,00
        pos_ind = FuturePosition(
            position_id="TEST-IND-001",
            ticker="INDZ26",
            contract_family=FutureContractFamily.IND,
            position_type=PositionType.CARRIED,
            side=PositionSide.BUY,
            quantity=5,
            previous_settlement_price=Decimal("129500.00"),
            current_settlement_price=Decimal("130250.00"),
        )
        res_ind = self.engine.calculate_position_settlement(
            pos_ind, trade_date=self.trade_date
        )
        self.assertEqual(res_ind.settlement_amount_brl, Decimal("3750.00"))

        # WINZ26: Day-trade Vendido na abertura a 130400 e recomprado a 129900 (+500 pts)
        # Quantidade = 40 contratos -> 500 * 0.20 * 40 = +R$ 4.000,00
        pos_win_dt = FuturePosition(
            position_id="TEST-WIN-DT",
            ticker="WINZ26",
            contract_family=FutureContractFamily.WIN,
            position_type=PositionType.DAY_TRADE,
            side=PositionSide.SELL,
            quantity=40,
            entry_price=Decimal("130400.00"),
            exit_price=Decimal("129900.00"),
        )
        res_win = self.engine.calculate_position_settlement(
            pos_win_dt, trade_date=self.trade_date
        )
        self.assertEqual(res_win.variation_points, Decimal("500.00"))
        self.assertEqual(res_win.unit_settlement_brl, Decimal("100.00"))
        self.assertEqual(res_win.settlement_amount_brl, Decimal("4000.00"))

    def test_new_position_same_day_settlement(self) -> None:
        """Testa ajuste de nova posição aberta no pregão D contra o ajuste de fechamento D."""
        pos_new = FuturePosition(
            position_id="TEST-WDO-NEW",
            ticker="WDOZ26",
            contract_family=FutureContractFamily.WDO,
            position_type=PositionType.NEW_POSITION,
            side=PositionSide.BUY,
            quantity=15,
            entry_price=Decimal("5795.00"),
            current_settlement_price=Decimal("5805.50"),
        )
        res = self.engine.calculate_position_settlement(
            pos_new, trade_date=self.trade_date
        )
        # (5805.50 - 5795.00) * 10 * 15 = 10.50 * 150 = R$ 1.575,00
        self.assertEqual(res.settlement_amount_brl, Decimal("1575.00"))

    def test_settle_from_fixture_json(self) -> None:
        """Carrega fixtures/bmf_futures_positions.json e valida a consolidação D+1."""
        self.assertTrue(FIXTURE_PATH.exists(), f"Fixture não encontrada: {FIXTURE_PATH}")
        summary: PortfolioSettlementSummary = self.engine.settle_from_json(FIXTURE_PATH)

        self.assertEqual(summary.portfolio_id, "BMF-PORT-20261103-001")
        self.assertEqual(summary.participant_code, "00114")
        self.assertEqual(summary.trade_date, date(2026, 11, 3))
        self.assertEqual(summary.settlement_date, date(2026, 11, 4))
        self.assertEqual(len(summary.results), 7)

        # Verifica cada posição da fixture:
        # 1. POS-DI1-001 (CARRIED BUY 100 DI1F27: 98202.12 -> 98180.12) = +2200.00
        # 2. POS-DI1-002 (DAY_TRADE BUY 50 DI1F27: 11.40% [98216.80] -> 11.60% [98187.45])
        #    (98216.80 - 98187.45) * 50 = 29.35 * 50 = +1467.50
        # 3. POS-DOL-001 (CARRIED BUY 10 DOLZ26: 5780 -> 5805.50) = +12750.00
        # 4. POS-DOL-002 (DAY_TRADE BUY 5 DOLZ26: 5790 -> 5802) = 12 * 50 * 5 = +3000.00
        # 5. POS-WDO-001 (CARRIED SELL 25 WDOZ26: 5780 -> 5805.50) = -6375.00
        # 6. POS-IND-001 (CARRIED BUY 5 INDZ26: 129500 -> 130250) = +3750.00
        # 7. POS-WIN-001 (DAY_TRADE SELL 40 WINZ26: 130400 -> 129900) = +4000.00
        by_id = {r.position_id: r for r in summary.results}
        self.assertEqual(by_id["POS-DI1-001"].settlement_amount_brl, Decimal("2200.00"))
        self.assertEqual(by_id["POS-DI1-002"].settlement_amount_brl, Decimal("1467.50"))
        self.assertEqual(by_id["POS-DOL-001"].settlement_amount_brl, Decimal("12750.00"))
        self.assertEqual(by_id["POS-DOL-002"].settlement_amount_brl, Decimal("3000.00"))
        self.assertEqual(by_id["POS-WDO-001"].settlement_amount_brl, Decimal("-6375.00"))
        self.assertEqual(by_id["POS-IND-001"].settlement_amount_brl, Decimal("3750.00"))
        self.assertEqual(by_id["POS-WIN-001"].settlement_amount_brl, Decimal("4000.00"))

        self.assertEqual(summary.total_credits_brl, Decimal("27167.50"))
        self.assertEqual(summary.total_debits_brl, Decimal("-6375.00"))
        self.assertEqual(summary.net_settlement_brl, Decimal("20792.50"))
        self.assertEqual(summary.net_financial_status, "CREDIT")

        # Verifica serialização to_dict
        serialized = summary.to_dict()
        self.assertEqual(serialized["net_settlement_brl"], "20792.50")
        self.assertEqual(len(serialized["results"]), 7)

    def test_validation_and_calculation_errors(self) -> None:
        """Verifica disparo de exceções em entradas inválidas."""
        with self.assertRaises(BmfFuturesValidationError):
            FuturePosition(
                position_id="BAD-FAMILY",
                ticker="DOLZ26",
                contract_family=FutureContractFamily.DI1,
                side=PositionSide.BUY,
                quantity=5,
            )

        with self.assertRaises(BmfFuturesValidationError):
            FuturePosition(
                position_id="BAD-QTY",
                ticker="DOLZ26",
                contract_family=FutureContractFamily.DOL,
                side=PositionSide.BUY,
                quantity=0,
                previous_settlement_price=Decimal("5800.00"),
                current_settlement_price=Decimal("5810.00"),
            )

        with self.assertRaises(BmfFuturesCalculationError):
            self.engine.calculate_di1_pu("-105.00", business_days=20)

        with self.assertRaises(BmfFuturesCalculationError):
            self.engine.calculate_di1_pu("11.50", business_days=-1)

    def test_next_business_day_weekend_rollover(self) -> None:
        """Testa que operações na sexta-feira liquidam o ajuste na segunda-feira (D+1 útil)."""
        friday = date(2026, 11, 6)  # Sexta-feira
        monday = self.engine.calculate_next_business_day(friday)
        self.assertEqual(monday, date(2026, 11, 9))


if __name__ == "__main__":
    unittest.main()
