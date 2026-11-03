"""Testes unitários para Opções Listadas sobre Ações e Índices — Prêmio D+1, Cobertura e Exercício (FL-039)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from src.listed_options_exercise import (
    AssignmentRecord,
    CUSTODY_WALLET_COVERED_OPTIONS,
    CUSTODY_WALLET_FREE_SPOT,
    ConservationInvariantError,
    ContraryInstructionError,
    CoverageType,
    EarlyExerciseStyleError,
    ExerciseInstruction,
    ExpirationSettlementSummary,
    InsufficientCoverageError,
    ListedOptionsEngine,
    ListedOptionsValidationError,
    Moneyness,
    OptionPosition,
    OptionSeries,
    OptionStyle,
    OptionType,
    PositionSide,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "listed_options_expiration.json"
)


class TestListedOptionsExercise(unittest.TestCase):
    """Suíte de testes determinísticos para o fluxo FL-039 (Opções Listadas e Exercício B3)."""

    def setUp(self) -> None:
        self.engine = ListedOptionsEngine()
        self.exp_date = date(2026, 11, 20)
        self.d2_date = date(2026, 11, 24)

    def test_b3_series_symbology_and_month_letters(self) -> None:
        """Valida a simbologia B3: A-L para Calls (1-12) e M-X para Puts (1-12)."""
        call_series = OptionSeries(
            series_ticker="PETRA28",
            underlying_ticker="PETR4",
            underlying_isin="BRPETRACNPR6",
            option_type=OptionType.CALL,
            option_style=OptionStyle.AMERICAN,
            strike_price=Decimal("28.00"),
            expiration_date=date(2026, 1, 16),
        )
        self.assertEqual(call_series.expiration_month_letter, "A")
        self.assertEqual(call_series.expiration_month_number, 1)
        self.assertEqual(call_series.spot_exercise_ticker, "PETR4E")

        put_series = OptionSeries(
            series_ticker="VALEM80",
            underlying_ticker="VALE3",
            underlying_isin="BRVALEACNOR0",
            option_type=OptionType.PUT,
            option_style=OptionStyle.EUROPEAN,
            strike_price=Decimal("80.00"),
            expiration_date=date(2026, 1, 16),
        )
        self.assertEqual(put_series.expiration_month_letter, "M")
        self.assertEqual(put_series.expiration_month_number, 1)
        self.assertEqual(put_series.spot_exercise_ticker, "VALE3E")

        dec_call = OptionSeries(
            series_ticker="BOVAL110",
            underlying_ticker="BOVA11",
            underlying_isin="BRBOVACTF003",
            option_type=OptionType.CALL,
            option_style=OptionStyle.EUROPEAN,
            strike_price=Decimal("110.00"),
            expiration_date=date(2026, 12, 18),
        )
        self.assertEqual(dec_call.expiration_month_letter, "L")
        self.assertEqual(dec_call.expiration_month_number, 12)

        # Letra M em uma série declarada como CALL deve lançar erro de validação
        with self.assertRaises(ListedOptionsValidationError):
            OptionSeries(
                series_ticker="PETRM28",
                underlying_ticker="PETR4",
                underlying_isin="BRPETRACNPR6",
                option_type=OptionType.CALL,
                option_style=OptionStyle.AMERICAN,
                strike_price=Decimal("28.00"),
                expiration_date=date(2026, 1, 16),
            )

        # Letra A em uma série declarada como PUT deve lançar erro de validação
        with self.assertRaises(ListedOptionsValidationError):
            OptionSeries(
                series_ticker="VALEA80",
                underlying_ticker="VALE3",
                underlying_isin="BRVALEACNOR0",
                option_type=OptionType.PUT,
                option_style=OptionStyle.EUROPEAN,
                strike_price=Decimal("80.00"),
                expiration_date=date(2026, 1, 16),
            )

    def test_moneyness_and_intrinsic_value_calculation(self) -> None:
        """Testa o cálculo determinístico de Moneyness (ITM, ATM, OTM) para Calls e Puts."""
        # Call ITM (S = 38.50 > K = 36.00 -> Intrínseco = 2.50)
        mon_c_itm, intr_c_itm = self.engine.evaluate_moneyness(
            OptionType.CALL, Decimal("36.00"), Decimal("38.50")
        )
        self.assertEqual(mon_c_itm, Moneyness.ITM)
        self.assertEqual(intr_c_itm, Decimal("2.50"))

        # Call ATM (S = 128.00 == K = 128.00 -> Intrínseco = 0.00)
        mon_c_atm, intr_c_atm = self.engine.evaluate_moneyness(
            OptionType.CALL, Decimal("128.00"), Decimal("128.00")
        )
        self.assertEqual(mon_c_atm, Moneyness.ATM)
        self.assertEqual(intr_c_atm, Decimal("0.00"))

        # Call OTM (S = 35.20 < K = 38.00 -> Intrínseco = 0.00)
        mon_c_otm, intr_c_otm = self.engine.evaluate_moneyness(
            OptionType.CALL, Decimal("38.00"), Decimal("35.20")
        )
        self.assertEqual(mon_c_otm, Moneyness.OTM)
        self.assertEqual(intr_c_otm, Decimal("0.00"))

        # Put ITM (S = 58.40 < K = 62.00 -> Intrínseco = 3.60)
        mon_p_itm, intr_p_itm = self.engine.evaluate_moneyness(
            OptionType.PUT, Decimal("62.00"), Decimal("58.40")
        )
        self.assertEqual(mon_p_itm, Moneyness.ITM)
        self.assertEqual(intr_p_itm, Decimal("3.60"))

        # Put ATM (S = 62.00 == K = 62.00 -> Intrínseco = 0.00)
        mon_p_atm, intr_p_atm = self.engine.evaluate_moneyness(
            OptionType.PUT, Decimal("62.00"), Decimal("62.00")
        )
        self.assertEqual(mon_p_atm, Moneyness.ATM)
        self.assertEqual(intr_p_atm, Decimal("0.00"))

        # Put OTM (S = 38.50 > K = 34.00 -> Intrínseco = 0.00)
        mon_p_otm, intr_p_otm = self.engine.evaluate_moneyness(
            OptionType.PUT, Decimal("34.00"), Decimal("38.50")
        )
        self.assertEqual(mon_p_otm, Moneyness.OTM)
        self.assertEqual(intr_p_otm, Decimal("0.00"))

    def test_d1_premium_settlement_and_coverage_validations(self) -> None:
        """Verifica a apuração de prêmio D+1 e regras de cobertura (2306-6 vs CORE vs Spread)."""
        gross_h, signed_h = self.engine.calculate_d1_premium(
            quantity=10000,
            premium_per_unit=Decimal("1.85"),
            side=PositionSide.HOLDER,
        )
        self.assertEqual(gross_h, Decimal("18500.00"))
        self.assertEqual(signed_h, Decimal("-18500.00"))

        gross_w, signed_w = self.engine.calculate_d1_premium(
            quantity=10000,
            premium_per_unit=Decimal("1.85"),
            side=PositionSide.WRITER,
        )
        self.assertEqual(gross_w, Decimal("18500.00"))
        self.assertEqual(signed_w, Decimal("18500.00"))

        # Posição lançadora coberta válida (Carteira 2306-6)
        covered_writer = OptionPosition(
            position_id="POS-COV-OK",
            participant_code="00315",
            investor_account="300880-4",
            series_ticker="PETRK36",
            side=PositionSide.WRITER,
            quantity=5000,
            trade_date=date(2026, 11, 3),
            premium_per_unit=Decimal("1.85"),
            coverage_type=CoverageType.COVERED,
            covered_quantity=5000,
        )
        self.assertEqual(
            covered_writer.custody_wallet_code, CUSTODY_WALLET_COVERED_OPTIONS
        )
        self.assertFalse(covered_writer.requires_core_margin)

        # Posição lançadora coberta com saldo insuficiente na 2306-6 deve falhar
        with self.assertRaises(InsufficientCoverageError):
            OptionPosition(
                position_id="POS-COV-FAIL",
                participant_code="00315",
                investor_account="300880-4",
                series_ticker="PETRK36",
                side=PositionSide.WRITER,
                quantity=5000,
                trade_date=date(2026, 11, 3),
                premium_per_unit=Decimal("1.85"),
                coverage_type=CoverageType.COVERED,
                covered_quantity=4900,
            )

        # Posição lançadora travada (SPREAD_LOCKED) sem ponta comprada informada deve falhar
        with self.assertRaises(ListedOptionsValidationError):
            OptionPosition(
                position_id="POS-SPREAD-FAIL",
                participant_code="00618",
                investor_account="600771-3",
                series_ticker="VALEW62",
                side=PositionSide.WRITER,
                quantity=3000,
                trade_date=date(2026, 11, 4),
                premium_per_unit=Decimal("2.40"),
                coverage_type=CoverageType.SPREAD_LOCKED,
            )

    def test_american_vs_european_early_exercise_style(self) -> None:
        """Verifica que opções Europeias rejeitam exercício antecipado antes do vencimento."""
        am_series = OptionSeries(
            series_ticker="PETRK36",
            underlying_ticker="PETR4",
            underlying_isin="BRPETRACNPR6",
            option_type=OptionType.CALL,
            option_style=OptionStyle.AMERICAN,
            strike_price=Decimal("36.00"),
            expiration_date=self.exp_date,
        )
        eu_series = OptionSeries(
            series_ticker="VALEW62",
            underlying_ticker="VALE3",
            underlying_isin="BRVALEACNOR0",
            option_type=OptionType.PUT,
            option_style=OptionStyle.EUROPEAN,
            strike_price=Decimal("62.00"),
            expiration_date=self.exp_date,
        )

        # Estilo Americano permite exercício antecipado em D < T_exp
        self.engine.validate_exercise_date_for_style(am_series, date(2026, 11, 10))
        self.engine.validate_exercise_date_for_style(am_series, self.exp_date)

        # Estilo Europeu bloqueia exercício antes de T_exp, mas permite em T_exp
        with self.assertRaises(EarlyExerciseStyleError):
            self.engine.validate_exercise_date_for_style(eu_series, date(2026, 11, 10))
        self.engine.validate_exercise_date_for_style(eu_series, self.exp_date)

    def test_contrary_instruction_and_pro_rata_writer_assignment(self) -> None:
        """Valida barreira parcial pelo titular e atribuição pro-rata exata aos lançadores."""
        series = OptionSeries(
            series_ticker="PETRK36",
            underlying_ticker="PETR4",
            underlying_isin="BRPETRACNPR6",
            option_type=OptionType.CALL,
            option_style=OptionStyle.AMERICAN,
            strike_price=Decimal("36.00"),
            expiration_date=self.exp_date,
        )
        positions = [
            OptionPosition(
                position_id="H1",
                participant_code="00105",
                investor_account="1001",
                series_ticker="PETRK36",
                side=PositionSide.HOLDER,
                quantity=10000,
                trade_date=date(2026, 11, 3),
                premium_per_unit=Decimal("1.85"),
            ),
            OptionPosition(
                position_id="H2",
                participant_code="00210",
                investor_account="2002",
                series_ticker="PETRK36",
                side=PositionSide.HOLDER,
                quantity=5000,
                trade_date=date(2026, 11, 5),
                premium_per_unit=Decimal("2.10"),
                contrary_instruction=True,
                contrary_quantity=2000,
            ),
            OptionPosition(
                position_id="W1",
                participant_code="00315",
                investor_account="3003",
                series_ticker="PETRK36",
                side=PositionSide.WRITER,
                quantity=9000,
                trade_date=date(2026, 11, 3),
                premium_per_unit=Decimal("1.85"),
                coverage_type=CoverageType.COVERED,
                covered_quantity=9000,
            ),
            OptionPosition(
                position_id="W2",
                participant_code="00420",
                investor_account="4004",
                series_ticker="PETRK36",
                side=PositionSide.WRITER,
                quantity=6000,
                trade_date=date(2026, 11, 5),
                premium_per_unit=Decimal("2.04"),
                coverage_type=CoverageType.UNCOVERED,
            ),
        ]

        summary = self.engine.settle_series_expiration(
            series=series,
            positions=positions,
            underlying_closing_price=Decimal("38.50"),
            settlement_date_d2=self.d2_date,
        )
        self.assertTrue(summary.conservation_invariant_satisfied)
        self.assertEqual(summary.moneyness, Moneyness.ITM)
        self.assertEqual(summary.intrinsic_value_per_unit, Decimal("2.50"))
        self.assertEqual(summary.total_holder_open_quantity, 15000)
        self.assertEqual(summary.total_writer_open_quantity, 15000)
        self.assertEqual(summary.total_holder_exercised_quantity, 13000)
        self.assertEqual(summary.total_holder_contrary_blocked_quantity, 2000)
        self.assertEqual(summary.total_writer_assigned_quantity, 13000)
        # W1 tem 60% de 13.000 = 7.800 (coberta na 2306-6); W2 tem 40% de 13.000 = 5.200 (descoberta)
        self.assertEqual(summary.total_writer_covered_assigned_quantity, 7800)
        self.assertEqual(summary.total_writer_uncovered_assigned_quantity, 5200)
        # Notional D+2 = 13.000 * R$ 36,00 = R$ 468.000,00
        self.assertEqual(summary.total_exercise_notional_d2_brl, Decimal("468000.00"))

    def test_conservation_invariant_violation_detection(self) -> None:
        """Verifica que desequilíbrio entre posições titulares e lançadoras dispara ConservationInvariantError."""
        series = OptionSeries(
            series_ticker="PETRK36",
            underlying_ticker="PETR4",
            underlying_isin="BRPETRACNPR6",
            option_type=OptionType.CALL,
            option_style=OptionStyle.AMERICAN,
            strike_price=Decimal("36.00"),
            expiration_date=self.exp_date,
        )
        unbalanced_positions = [
            OptionPosition(
                position_id="H1",
                participant_code="00105",
                investor_account="1001",
                series_ticker="PETRK36",
                side=PositionSide.HOLDER,
                quantity=10000,
                trade_date=date(2026, 11, 3),
                premium_per_unit=Decimal("1.85"),
            ),
            OptionPosition(
                position_id="W1",
                participant_code="00315",
                investor_account="3003",
                series_ticker="PETRK36",
                side=PositionSide.WRITER,
                quantity=8000,
                trade_date=date(2026, 11, 3),
                premium_per_unit=Decimal("1.85"),
                coverage_type=CoverageType.COVERED,
                covered_quantity=8000,
            ),
        ]
        with self.assertRaises(ConservationInvariantError):
            self.engine.settle_series_expiration(
                series=series,
                positions=unbalanced_positions,
                underlying_closing_price=Decimal("38.50"),
                settlement_date_d2=self.d2_date,
            )

        # Também testa ContraryInstructionError quando barreira excede quantidade titular
        with self.assertRaises(ContraryInstructionError):
            OptionPosition(
                position_id="H-BAD-BARRIER",
                participant_code="00105",
                investor_account="1001",
                series_ticker="PETRK36",
                side=PositionSide.HOLDER,
                quantity=5000,
                trade_date=date(2026, 11, 3),
                premium_per_unit=Decimal("1.85"),
                contrary_instruction=True,
                contrary_quantity=6000,
            )

    def test_evaluate_full_expiration_fixture(self) -> None:
        """Carrega e valida integralmente fixtures/listed_options_expiration.json."""
        self.assertTrue(FIXTURE_PATH.exists())
        result = self.engine.evaluate_fixture(FIXTURE_PATH)

        self.assertEqual(result["total_series"], 6)
        self.assertTrue(result["conservation_invariant_satisfied"])
        self.assertEqual(result["total_holder_open_quantity"], 43000)
        self.assertEqual(result["total_writer_open_quantity"], 43000)

        # Exercidas: 13.000 (PETRK36 Call ITM) + 8.000 (VALEW62 Put ITM) = 21.000
        self.assertEqual(result["total_exercised_quantity"], 21000)
        self.assertEqual(result["total_assigned_quantity"], 21000)

        # Bloqueadas por barreira (Contrary Instruction): 2.000 (PETRK36) + 3.000 (WEGEK50) = 5.000
        self.assertEqual(result["total_contrary_blocked_quantity"], 5000)

        # Expiradas sem exercício (Lapsed): 2.000 (PETRK36 barreira) + 4.000 (BOVAK128 ATM)
        # + 6.000 (ITUBK38 OTM) + 7.000 (PETRW34 OTM) + 3.000 (WEGEK50 barreira total) = 22.000
        self.assertEqual(result["total_lapsed_quantity"], 22000)

        # Desdobramento da atribuição por tipo de cobertura:
        # Coberta (2306-6): 7.800 (PETRK36-W1)
        # Descoberta (CORE): 5.200 (PETRK36-W2) + 5.000 (VALEW62-W1) = 10.200
        # Travada (Spread): 3.000 (VALEW62-W2)
        self.assertEqual(result["total_covered_assigned_quantity"], 7800)
        self.assertEqual(result["total_uncovered_assigned_quantity"], 10200)
        self.assertEqual(result["total_spread_assigned_quantity"], 3000)

        # Volume financeiro D+2 pelo Strike:
        # PETRK36: 13.000 * 36.00 = 468.000,00
        # VALEW62: 8.000 * 62.00 = 496.000,00
        # Total = R$ 964.000,00
        self.assertEqual(
            result["total_exercise_notional_d2_brl"], Decimal("964000.00")
        )

        # Verifica os negócios gerados no mercado à vista de exercício (D+2)
        spot_trades = result["spot_exercise_trades"]
        self.assertEqual(len(spot_trades), 5)
        self.assertEqual(
            sum(t["quantity"] for t in spot_trades),
            result["total_exercised_quantity"],
        )
        self.assertEqual(
            sum(t["gross_settlement_amount_brl"] for t in spot_trades),
            Decimal("964000.00"),
        )

        # Verifica carteiras de entrega nos negócios à vista gerados
        petr_trades = [
            t for t in spot_trades if t["series_ticker"] == "PETRK36"
        ]
        self.assertEqual(
            petr_trades[0]["seller_delivery_wallet_code"],
            CUSTODY_WALLET_COVERED_OPTIONS,
        )
        self.assertEqual(
            petr_trades[-1]["seller_delivery_wallet_code"],
            CUSTODY_WALLET_FREE_SPOT,
        )


if __name__ == "__main__":
    unittest.main()
