"""
Suíte de Testes Automatizados — B3 Garantias Transfronteiriças (FL-067)
========================================================================
Testes de conversão cambial multimoeda PTAX, matriz de haircuts de títulos
soberanos (US Treasuries, German Bunds), buffer de volatilidade cambial,
apuração de chamadas de margem e conciliação Euroclear / DTC.
"""

from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.cross_border_collateral import (
    BlockedCurrencyError,
    CollateralPledgeStatus,
    CrossBorderCollateralEngine,
    CrossBorderCollateralError,
    CrossBorderCollateralPosition,
    CrossBorderSecurity,
    Currency,
    DepositorySettlementError,
    FxVolatilityBufferConfig,
    IneligibleSovereignError,
    InternationalDepository,
    InvalidHaircutError,
    InvalidPositionError,
    MarginCallStatus,
    MarginReleaseViolationError,
    PtaxRate,
    SovereignBondType,
    SovereignIssuer,
    TenorBucket,
    UnsupportedCurrencyError,
)


class TestCrossBorderCollateralEngine(unittest.TestCase):
    """Testes funcionais e quantitativos do motor de garantias transfronteiriças."""

    def setUp(self) -> None:
        self.fixtures_dir = ROOT_DIR / "fixtures"
        self.fixture_file = self.fixtures_dir / "cross_border_collateral.json"
        self.engine = CrossBorderCollateralEngine.load_from_fixture(self.fixture_file)

    def test_fixture_loading(self) -> None:
        """Valida se todas as taxas, configurações e posições do fixture foram carregadas com precisão."""
        self.assertIn(Currency.USD, self.engine.ptax_rates)
        self.assertIn(Currency.EUR, self.engine.ptax_rates)
        self.assertIn(Currency.GBP, self.engine.ptax_rates)

        ptax_usd = self.engine.get_ptax_rate(Currency.USD)
        self.assertEqual(ptax_usd.bid_rate, Decimal("5.6520"))
        self.assertEqual(ptax_usd.ask_rate, Decimal("5.6580"))

        self.assertEqual(len(self.engine.positions), 5)
        self.assertIn("POS-ITAU-UST-01", self.engine.positions)
        self.assertIn("POS-ITAU-BUND-02", self.engine.positions)

    def test_ptax_currency_conversions_and_bid_conservatism(self) -> None:
        """Verifica a conversão cambial conservadora via PTAX de compra (bid)."""
        # 100.000,00 USD * 5.6520 (bid) = R$ 565.200,00
        val_bid = self.engine.convert_fx_to_brl(Decimal("100000.00"), Currency.USD, use_bid=True)
        self.assertEqual(val_bid, Decimal("565200.00"))

        # 100.000,00 USD * 5.6580 (ask) = R$ 565.800,00
        val_ask = self.engine.convert_fx_to_brl(Decimal("100000.00"), Currency.USD, use_bid=False)
        self.assertEqual(val_ask, Decimal("565800.00"))

        # Conservadorismo da CCP: Taxa de compra <= Taxa de venda
        self.assertLessEqual(val_bid, val_ask)

        # Conversão de BRL para BRL retorna paridade exata
        brl_val = self.engine.convert_fx_to_brl(Decimal("50000.00"), Currency.BRL)
        self.assertEqual(brl_val, Decimal("50000.00"))

    def test_cross_rate_synthetic_triangulation(self) -> None:
        """Verifica paridade triangular entre cotações cruzadas (EUR/USD sintético)."""
        cross_rate = self.engine.calculate_synthetic_cross_rate(Currency.EUR, Currency.USD)
        # EUR_mid = 6.1070, USD_mid = 5.6550 -> 6.1070 / 5.6550 = 1.0799
        self.assertEqual(cross_rate, Decimal("1.0799"))

    def test_sovereign_haircut_matrix_tenor_monotonicity(self) -> None:
        """
        Invariante 2: Monotonicidade estrita da matriz de haircuts de títulos soberanos.
        Para qualquer emissor soberano, o haircut aumenta monotonicamente com a duration/maturidade.
        """
        maturities = [
            Decimal("0.5"),   # UP_TO_1Y
            Decimal("2.0"),   # FROM_1Y_TO_3Y
            Decimal("4.0"),   # FROM_3Y_TO_5Y
            Decimal("8.0"),   # FROM_5Y_TO_10Y
            Decimal("25.0"),  # OVER_10Y
        ]

        for issuer in (SovereignIssuer.US, SovereignIssuer.DE, SovereignIssuer.UK):
            previous_haircut = Decimal("0.0000")
            for mat in maturities:
                bucket, total_h = self.engine.calculate_sovereign_haircut(
                    issuer=issuer,
                    maturity_years=mat,
                    depository=InternationalDepository.DTC,
                    credit_rating="AAA",
                )
                self.assertGreaterEqual(
                    total_h,
                    previous_haircut,
                    f"Violação de monotonicidade no emissor {issuer.value} para maturidade {mat}y",
                )
                previous_haircut = total_h

    def test_credit_rating_haircut_and_ineligible_rejection(self) -> None:
        """Verifica o acréscimo de deságio por risco de crédito e a rejeição de notas sub-investimento."""
        # AAA / AA+ = 0 bps adicional
        self.assertEqual(self.engine.calculate_credit_rating_haircut("AAA"), Decimal("0.0000"))
        self.assertEqual(self.engine.calculate_credit_rating_haircut("AA+"), Decimal("0.0000"))

        # AA / AA- = +50 bps
        self.assertEqual(self.engine.calculate_credit_rating_haircut("AA-"), Decimal("0.0050"))

        # A+ / A / A- = +150 bps
        self.assertEqual(self.engine.calculate_credit_rating_haircut("A"), Decimal("0.0150"))

        # BBB- = +300 bps (mínimo grau de investimento)
        self.assertEqual(self.engine.calculate_credit_rating_haircut("BBB-"), Decimal("0.0300"))

        # Grau especulativo (BB+ ou inferior) deve lançar IneligibleSovereignError
        with self.assertRaises(IneligibleSovereignError):
            self.engine.calculate_credit_rating_haircut("BB+")

        with self.assertRaises(IneligibleSovereignError):
            self.engine.calculate_credit_rating_haircut("CCC")

    def test_fx_volatility_buffer_formula_and_floor(self) -> None:
        """Testa o cálculo do buffer de volatilidade cambial com piso regulatório."""
        # Para USD: 2.33 * 0.0120 * sqrt(2) = 0.03954. Piso = 0.0500 (Floor ativado!)
        buf_usd = self.engine.get_fx_volatility_buffer(Currency.USD)
        self.assertEqual(buf_usd, Decimal("0.0500"))

        # Simula período de alta volatilidade cambial (estresse cambial diário de 3.0%)
        stress_cfg = FxVolatilityBufferConfig(
            currency=Currency.USD,
            daily_volatility=Decimal("0.0300"),
            confidence_quantile=Decimal("2.33"),
            liquidation_horizon_days=2,
            minimum_floor_buffer=Decimal("0.0500"),
        )
        # 2.33 * 0.0300 * 1.41421356 = 0.09885... -> 0.0989 (9.89% > 5.00%)
        stress_buf = stress_cfg.calculate_buffer()
        self.assertEqual(stress_buf, Decimal("0.0989"))
        self.assertGreater(stress_buf, Decimal("0.0500"))

    def test_single_position_evaluation_math(self) -> None:
        """Valida a precisão matemática da avaliação de uma posição soberana em USD."""
        pos = self.engine.positions["POS-ITAU-UST-01"]
        ev = self.engine.evaluate_position(pos)

        # 1. Valor Bruto FX = 2.000 * 98.50 = 197.000,00 USD
        self.assertEqual(ev.gross_value_fx, Decimal("197000.00"))

        # 2. Haircut do Título:
        # US Treasury Bill <= 1y: Base 1.00% + DTC 0.20% + AAA 0.00% = 1.20% (0.0120)
        self.assertEqual(ev.bond_haircut_rate, Decimal("0.0120"))
        # Deságio FX = 197.000 * 0.0120 = 2.364,00 USD
        self.assertEqual(ev.bond_haircut_amount_fx, Decimal("2364.00"))
        # Valor Líquido FX = 197.000 - 2.364 = 194.636,00 USD
        self.assertEqual(ev.net_value_after_haircut_fx, Decimal("194636.00"))

        # 3. Conversão PTAX Bid:
        # PTAX Bid USD = 5.6520
        # Bruto BRL = 197.000 * 5.6520 = 1.113.444,00 BRL
        self.assertEqual(ev.gross_converted_brl, Decimal("1113444.00"))
        # Valor após haircut de título em BRL = 194.636 * 5.6520 = 1.100.082,67 BRL
        val_after_bond_haircut_brl = Decimal("1100082.67")

        # 4. Buffer de Volatilidade Cambial (USD = 5.00%):
        # Dedução BRL = 1.100.082,67 * 0.05 = 55.004,13 BRL
        self.assertEqual(ev.fx_buffer_deduction_brl, Decimal("55004.13"))

        # 5. Valor Líquido Elegível BRL:
        # 1.100.082,67 - 55.004,13 = 1.045.078,54 BRL
        self.assertEqual(ev.net_eligible_value_brl, Decimal("1045078.54"))

        # Invariante 1: 0 <= Net <= Gross
        self.assertGreater(ev.net_eligible_value_brl, Decimal("0.00"))
        self.assertLessEqual(ev.net_eligible_value_brl, ev.gross_converted_brl)

    def test_margin_call_surplus_scenario(self) -> None:
        """Verifica apuração de margem com saldo superavitário (PART-ITAU-01)."""
        receipt = self.engine.evaluate_margin_call(
            participant_id="PART-ITAU-01",
            required_margin_brl=Decimal("1500000.00"),
        )
        self.assertEqual(receipt.status, MarginCallStatus.SURPLUS)
        self.assertEqual(receipt.margin_deficit_brl, Decimal("0.00"))
        self.assertGreater(receipt.margin_surplus_brl, Decimal("0.00"))
        self.assertIsNone(receipt.call_deadline)
        self.assertGreaterEqual(receipt.total_net_eligible_collateral_brl, Decimal("1500000.00"))

    def test_margin_call_deficit_and_deadline_generation(self) -> None:
        """Verifica a emissão de chamada de margem para participante com déficit (PART-BRADESCO-02)."""
        # Garantia Brad: 1500 UST-NOTE-5Y a 99.20 USD = 148.800 USD bruto
        # Exigência: R$ 1.200.000,00 -> Gera déficit
        receipt = self.engine.evaluate_margin_call(
            participant_id="PART-BRADESCO-02",
            required_margin_brl=Decimal("1200000.00"),
            is_intraday=True,
        )
        self.assertEqual(receipt.status, MarginCallStatus.MARGIN_CALL_ISSUED)
        self.assertGreater(receipt.margin_deficit_brl, Decimal("0.00"))
        self.assertEqual(receipt.margin_surplus_brl, Decimal("0.00"))
        self.assertIn("16:30 BRT", str(receipt.call_deadline))
        self.assertIn("BRL", receipt.currencies_accepted)
        self.assertIn("USD", receipt.currencies_accepted)

        # Chamada de fechamento (End of Day) tem deadline D+1 10:00
        eod_receipt = self.engine.evaluate_margin_call(
            participant_id="PART-BRADESCO-02",
            required_margin_brl=Decimal("1200000.00"),
            is_intraday=False,
        )
        self.assertIn("D+1 10:00 BRT", str(eod_receipt.call_deadline))

    def test_safe_release_guardrail_prevention_of_deficit(self) -> None:
        """
        Invariante 7: Trava estrita anti-déficit na desoneração de garantias.
        A liberação só é permitida se a carteira remanescente continuar cobrindo a margem requerida.
        """
        # Itaú possui ~R$ 1.8M em garantias líquidas e requerimento de R$ 500.000,00
        # 1. Liberação parcial segura de 200 títulos US Treasury: DEVE PASSAR
        pos_id = "POS-ITAU-UST-01"
        success, msg = self.engine.release_collateral(
            position_id=pos_id,
            release_quantity=Decimal("200.00"),
            required_margin_brl=Decimal("500000.00"),
        )
        self.assertTrue(success)
        self.assertEqual(self.engine.positions[pos_id].quantity, Decimal("1800.00"))

        # 2. Tentativa de liberar todo o restante (1800) quando a exigência é R$ 1.500.000,00:
        # Sem o UST, restará apenas o Bund (~R$ 800k), gerando déficit -> DEVE REJEITAR!
        with self.assertRaises(MarginReleaseViolationError):
            self.engine.release_collateral(
                position_id=pos_id,
                release_quantity=Decimal("1800.00"),
                required_margin_brl=Decimal("1500000.00"),
            )

        # Garante que a quantidade foi revertida intacta
        self.assertEqual(self.engine.positions[pos_id].quantity, Decimal("1800.00"))

    def test_custody_reconciliation_euroclear_and_dtc(self) -> None:
        """Invariante 6: Conciliação estrita de custódia com extratos externos da DTC e Euroclear."""
        # Conciliação perfeita da DTC
        dtc_statement = {
            "US912797HR42": Decimal("2000.00"),
            "US91282CDJ71": Decimal("1500.00"),
        }
        rec_dtc = self.engine.reconcile_custody(InternationalDepository.DTC, dtc_statement)
        self.assertTrue(rec_dtc.is_balanced)
        self.assertEqual(rec_dtc.discrepancy_units, Decimal("0.0000"))

        # Conciliação da Euroclear com divergência simulada
        euroclear_broken_statement = {
            "DE0001102580": Decimal("1500.00"),
            "DE0001141877": Decimal("900.00"),  # Faltando 100 unidades!
            "US912810TL44": Decimal("800.00"),
        }
        rec_euro = self.engine.reconcile_custody(InternationalDepository.EUROCLEAR, euroclear_broken_statement)
        self.assertFalse(rec_euro.is_balanced)
        self.assertEqual(rec_euro.discrepancy_units, Decimal("100.0000"))

    def test_blocked_sanctioned_currencies_rejection(self) -> None:
        """Invariante 8: Rejeição absoluta de moedas sancionadas internacionalmente."""
        with self.assertRaises(BlockedCurrencyError):
            self.engine.register_ptax_rate(
                PtaxRate(
                    currency="RUB",  # Moeda russa sancionada
                    bid_rate=Decimal("10.0"),
                    ask_rate=Decimal("10.0"),
                    mid_rate=Decimal("10.0"),
                    bulletin_date="2026-11-04",
                )
            )

        # Cadastro de Posição com rating CCC deve ser rejeitado como IneligibleSovereignError
        sec_rub = CrossBorderSecurity(
            isin="RU000A0JXFM1",
            ticker="OFZ-RUSSIA",
            name="Russian Federation Bond",
            issuer=SovereignIssuer.US,
            bond_type=SovereignBondType.US_TREASURY_BOND,
            currency=Currency.USD,
            depository=InternationalDepository.EUROCLEAR,
            credit_rating="CCC",
            maturity_years=Decimal("5.0"),
            unit_price=Decimal("50.00"),
        )
        pos_bad = CrossBorderCollateralPosition(
            position_id="POS-BAD-RATING",
            participant_id="PART-001",
            account_id="ACC-001",
            security=sec_rub,
            quantity=Decimal("100.00"),
        )
        with self.assertRaises(IneligibleSovereignError):
            self.engine.evaluate_position(pos_bad)

    def test_formal_invariants_verification(self) -> None:
        """Verifica a suíte completa de provas formais e conservações do módulo."""
        inv_results = self.engine.verify_formal_invariants()
        for k, v in inv_results.items():
            self.assertTrue(v, f"Invariante formal falhou na verificação: {k}")

    def test_invalid_position_parameters(self) -> None:
        """Valida proteções contra quantidades, preços ou ISINs inválidos."""
        # ISIN com tamanho inválido (< 12 caracteres)
        with self.assertRaises(InvalidPositionError):
            CrossBorderSecurity(
                isin="SHORT",
                ticker="INVALID",
                name="Invalid ISIN",
                issuer=SovereignIssuer.US,
                bond_type=SovereignBondType.US_TREASURY_BILL,
                currency=Currency.USD,
                depository=InternationalDepository.DTC,
                credit_rating="AAA",
                maturity_years=Decimal("1.0"),
                unit_price=Decimal("100.00"),
            )

        # Preço negativo
        with self.assertRaises(InvalidPositionError):
            CrossBorderSecurity(
                isin="US912797HR42",
                ticker="NEG-PRICE",
                name="Negative Price Bond",
                issuer=SovereignIssuer.US,
                bond_type=SovereignBondType.US_TREASURY_BILL,
                currency=Currency.USD,
                depository=InternationalDepository.DTC,
                credit_rating="AAA",
                maturity_years=Decimal("1.0"),
                unit_price=Decimal("-10.00"),
            )


if __name__ == "__main__":
    unittest.main()
