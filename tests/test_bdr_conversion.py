"""Testes automatizados para o módulo de BDRs e Custódia Internacional (FL-034)."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from src.bdr_conversion import (
    BackingConservationError,
    BdrConversionEngine,
    BdrConversionRequest,
    BdrDividendDistribution,
    BdrOperationType,
    BdrProgram,
    BdrSponsorLevel,
    BdrValidationError,
    InsufficientBdrBalanceError,
    ParityMultipleError,
    ProgramNotFoundError,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "bdr_conversion_requests.json"
)


class TestBdrConversionEngine(unittest.TestCase):
    """Suite de testes para Emissão, Cancelamento e Proventos de BDRs na B3 (FL-034)."""

    def _make_aapl_program(
        self, locked_shares: int = 4500, issued_bdrs: int = 90000
    ) -> BdrProgram:
        return BdrProgram(
            ticker="AAPL34",
            bdr_isin="BRAAPLBDR004",
            underlying_ticker="AAPL",
            underlying_isin="US0378331005",
            issuer_name="APPLE INC.",
            sponsor_level=BdrSponsorLevel.UNSPONSORED_LEVEL_1,
            home_exchange="NASDAQ",
            overseas_custodian="DTC",
            custodian_account="DTC-B3-0942-AAPL",
            depositary_institution="BANCO B3 S.A.",
            underlying_shares_per_block=1,
            bdr_per_share=20,
            currency="USD",
            locked_underlying_shares=locked_shares,
            issued_bdr_quantity=issued_bdrs,
        )

    def test_load_and_process_fixture_end_to_end(self) -> None:
        """Valida o processamento completo do lote JSON de emissões, cancelamentos e dividendos."""
        engine, conversions, dividends = BdrConversionEngine.load_and_process_fixture(
            FIXTURE_PATH
        )

        self.assertEqual(len(conversions), 5)
        self.assertEqual(len(dividends), 2)
        self.assertTrue(engine.verify_global_conservation())

        # AAPL34: inicial 4.500 shares (90.000 BDRs) + 800 shares (16.000 BDRs) - 300 shares (6.000 BDRs)
        aapl = engine.get_program("AAPL34")
        self.assertEqual(aapl.locked_underlying_shares, 5000)
        self.assertEqual(aapl.issued_bdr_quantity, 100000)

        # MSFT34: inicial 2.000 shares (48.000 BDRs) + 500 shares (12.000 BDRs)
        msft = engine.get_program("MSFT34")
        self.assertEqual(msft.locked_underlying_shares, 2500)
        self.assertEqual(msft.issued_bdr_quantity, 60000)

        # NVDC34: inicial 3.000 shares (30.000 BDRs) - 500 shares (5.000 BDRs)
        nvdc = engine.get_program("NVDC34")
        self.assertEqual(nvdc.locked_underlying_shares, 2500)
        self.assertEqual(nvdc.issued_bdr_quantity, 25000)

        # XPBR31: inicial 10.000 shares (10.000 BDRs) + 2.500 shares (2.500 BDRs)
        xpbr = engine.get_program("XPBR31")
        self.assertEqual(xpbr.locked_underlying_shares, 12500)
        self.assertEqual(xpbr.issued_bdr_quantity, 12500)

        # Verifica cálculo determinístico do dividendo AAPL34
        div_aapl = dividends[0]
        self.assertEqual(div_aapl.event_id, "DIV-AAPL34-2026Q4")
        self.assertEqual(div_aapl.gross_total_usd, Decimal("1250.00"))
        self.assertEqual(div_aapl.withholding_tax_usd, Decimal("375.00"))
        self.assertEqual(div_aapl.net_foreign_usd, Decimal("875.00"))
        self.assertEqual(div_aapl.converted_gross_brl, Decimal("5075.00"))
        self.assertEqual(div_aapl.iof_tax_brl, Decimal("19.29"))
        self.assertEqual(div_aapl.after_iof_brl, Decimal("5055.71"))
        self.assertEqual(div_aapl.depositary_fee_brl, Decimal("151.67"))
        self.assertEqual(div_aapl.net_total_brl, Decimal("4904.04"))
        self.assertEqual(div_aapl.net_dividend_per_bdr_brl, Decimal("0.049040"))

        # Verifica cálculo determinístico do dividendo MSFT34
        div_msft = dividends[1]
        self.assertEqual(div_msft.event_id, "DIV-MSFT34-2026Q4")
        self.assertEqual(div_msft.gross_total_usd, Decimal("1875.00"))
        self.assertEqual(div_msft.withholding_tax_usd, Decimal("562.50"))
        self.assertEqual(div_msft.net_foreign_usd, Decimal("1312.50"))
        self.assertEqual(div_msft.converted_gross_brl, Decimal("7638.75"))
        self.assertEqual(div_msft.iof_tax_brl, Decimal("29.03"))
        self.assertEqual(div_msft.after_iof_brl, Decimal("7609.72"))
        self.assertEqual(div_msft.depositary_fee_brl, Decimal("304.39"))
        self.assertEqual(div_msft.net_total_brl, Decimal("7305.33"))
        self.assertEqual(div_msft.net_dividend_per_bdr_brl, Decimal("0.121756"))

    def test_sponsor_levels_and_regulatory_classifications(self) -> None:
        """Valida regras da Resolução CVM 182 para Níveis I, II, III Patrocinados e Nível I Não Patrocinado."""
        self.assertTrue(BdrSponsorLevel.SPONSORED_LEVEL_1.is_sponsored)
        self.assertFalse(BdrSponsorLevel.SPONSORED_LEVEL_1.requires_cvm_issuer_registration)
        self.assertEqual(BdrSponsorLevel.SPONSORED_LEVEL_1.expected_b3_suffixes, ("31",))

        self.assertTrue(BdrSponsorLevel.SPONSORED_LEVEL_2.is_sponsored)
        self.assertTrue(BdrSponsorLevel.SPONSORED_LEVEL_2.requires_cvm_issuer_registration)
        self.assertEqual(BdrSponsorLevel.SPONSORED_LEVEL_2.expected_b3_suffixes, ("32",))

        self.assertTrue(BdrSponsorLevel.SPONSORED_LEVEL_3.is_sponsored)
        self.assertTrue(BdrSponsorLevel.SPONSORED_LEVEL_3.requires_cvm_issuer_registration)
        self.assertEqual(BdrSponsorLevel.SPONSORED_LEVEL_3.expected_b3_suffixes, ("33",))

        self.assertFalse(BdrSponsorLevel.UNSPONSORED_LEVEL_1.is_sponsored)
        self.assertFalse(BdrSponsorLevel.UNSPONSORED_LEVEL_1.requires_cvm_issuer_registration)
        self.assertEqual(
            BdrSponsorLevel.UNSPONSORED_LEVEL_1.expected_b3_suffixes, ("34", "35", "39")
        )

        # Sufixo incompatível com o nível de patrocínio deve falhar
        with self.assertRaises(BdrValidationError):
            BdrProgram(
                ticker="AAPL33",
                bdr_isin="BRAAPLBDR004",
                underlying_ticker="AAPL",
                underlying_isin="US0378331005",
                issuer_name="APPLE INC.",
                sponsor_level=BdrSponsorLevel.UNSPONSORED_LEVEL_1,
                home_exchange="NASDAQ",
                overseas_custodian="DTC",
                custodian_account="DTC-B3-0942-AAPL",
                depositary_institution="BANCO B3 S.A.",
                bdr_per_share=20,
            )

    def test_rejects_fractional_underlying_shares_on_cancellation(self) -> None:
        """Garante rejeição quando o cancelamento de BDRs não é múltiplo exato da paridade (fração no exterior)."""
        program = self._make_aapl_program()
        engine = BdrConversionEngine([program])

        # 250 BDRs em paridade 1:20 resultariam em 12,5 ações AAPL no DTC -> proibido
        invalid_cancel = BdrConversionRequest(
            request_id="REQ-ERR-FRAC-01",
            ticker="AAPL34",
            operation_type=BdrOperationType.CANCELLATION,
            participant_code="0114",
            investor_account="990011-2",
            overseas_broker_dtc_code="0005",
            bdr_quantity=250,
            request_date=date(2026, 11, 3),
            swift_reference="MT540-ERR-001",
        )
        with self.assertRaises(ParityMultipleError):
            engine.process_conversion(invalid_cancel)

        # Verifica que o saldo do programa permaneceu intacto após a rejeição
        self.assertEqual(program.locked_underlying_shares, 4500)
        self.assertEqual(program.issued_bdr_quantity, 90000)

        # Fração explícita em Decimal também deve lançar ParityMultipleError
        with self.assertRaises(ParityMultipleError):
            BdrConversionRequest(
                request_id="REQ-ERR-FRAC-02",
                ticker="AAPL34",
                operation_type=BdrOperationType.ISSUANCE,
                participant_code="0114",
                investor_account="990011-2",
                overseas_broker_dtc_code="0005",
                underlying_shares_quantity=Decimal("10.5"),  # type: ignore[arg-type]
                request_date=date(2026, 11, 3),
                swift_reference="MT542-ERR-002",
            )

    def test_rejects_mismatched_shares_and_bdr_quantities(self) -> None:
        """Garante rejeição quando tanto ações quanto BDRs são informados mas divergem da paridade."""
        program = self._make_aapl_program()
        engine = BdrConversionEngine([program])

        mismatch_req = BdrConversionRequest(
            request_id="REQ-ERR-MISMATCH",
            ticker="AAPL34",
            operation_type=BdrOperationType.ISSUANCE,
            participant_code="0114",
            investor_account="884010-9",
            overseas_broker_dtc_code="0005",
            underlying_shares_quantity=100,
            bdr_quantity=2500,  # 100 * 20 deveria ser 2000
            request_date=date(2026, 11, 3),
            swift_reference="MT542-ERR-003",
        )
        with self.assertRaises(ParityMultipleError):
            engine.process_conversion(mismatch_req)

    def test_backing_conservation_invariant_and_insufficient_balance(self) -> None:
        """Valida a invariante estrita de conservação de lastro e bloqueio de saldo negativo."""
        # Tentativa de criar programa com lastro desbalanceado (100 ações != 1900 BDRs na razão 1:20)
        with self.assertRaises(BackingConservationError):
            self._make_aapl_program(locked_shares=100, issued_bdrs=1900)

        program = self._make_aapl_program(locked_shares=100, issued_bdrs=2000)
        engine = BdrConversionEngine([program])

        # Tentativa de cancelar 2.200 BDRs (110 ações) quando só existem 2.000 BDRs (100 ações)
        excess_cancel = BdrConversionRequest(
            request_id="REQ-ERR-BAL",
            ticker="AAPL34",
            operation_type=BdrOperationType.CANCELLATION,
            participant_code="0072",
            investor_account="441200-3",
            overseas_broker_dtc_code="0352",
            bdr_quantity=2200,
            request_date=date(2026, 11, 3),
            swift_reference="MT540-ERR-004",
        )
        with self.assertRaises(InsufficientBdrBalanceError):
            engine.process_conversion(excess_cancel)

        # Cancelamento total de 2.000 BDRs zera lastro e BDRs preservando conservação
        full_cancel = BdrConversionRequest(
            request_id="REQ-FULL-CANCEL",
            ticker="AAPL34",
            operation_type=BdrOperationType.CANCELLATION,
            participant_code="0072",
            investor_account="441200-3",
            overseas_broker_dtc_code="0352",
            bdr_quantity=2000,
            request_date=date(2026, 11, 3),
            swift_reference="MT540-OK-005",
        )
        res = engine.process_conversion(full_cancel)
        self.assertEqual(res.new_locked_shares, 0)
        self.assertEqual(res.new_issued_bdrs, 0)
        self.assertTrue(engine.verify_global_conservation())

    def test_dividend_distribution_investor_payout_and_validations(self) -> None:
        """Valida o rateio de dividendos por investidor e rejeição de parâmetros inválidos."""
        program = self._make_aapl_program(locked_shares=5000, issued_bdrs=100000)
        engine = BdrConversionEngine([program])

        dist = BdrDividendDistribution(
            event_id="DIV-AAPL34-TEST",
            ticker="AAPL34",
            record_date=date(2026, 11, 3),
            payment_date_abroad=date(2026, 11, 10),
            payment_date_b3=date(2026, 11, 17),
            gross_dividend_per_share_usd=Decimal("0.25"),
            eligible_underlying_shares=5000,
            eligible_bdr_quantity=100000,
            withholding_tax_rate=Decimal("0.30"),
            ptax_exchange_rate=Decimal("5.8000"),
            iof_fx_rate=Decimal("0.0038"),
            depositary_fee_rate=Decimal("0.0300"),
        )
        engine.process_dividend_distribution(dist)

        # Comitente com 10.000 BDRs (10% da emissão) recebe R$ 490,40
        investor_payout = dist.calculate_investor_payout_brl(10000)
        self.assertEqual(investor_payout, Decimal("490.40"))

        summary = dist.to_summary_dict()
        self.assertEqual(summary["net_total_brl"], "4904.04")
        self.assertEqual(summary["net_dividend_per_bdr_brl"], "0.049040")

        # Evento com posição elegível divergente do lastro custodiado deve lançar BackingConservationError
        bad_dist = BdrDividendDistribution(
            event_id="DIV-AAPL34-BAD",
            ticker="AAPL34",
            record_date=date(2026, 11, 3),
            payment_date_abroad=date(2026, 11, 10),
            payment_date_b3=date(2026, 11, 17),
            gross_dividend_per_share_usd=Decimal("0.25"),
            eligible_underlying_shares=4000,
            eligible_bdr_quantity=80000,
            ptax_exchange_rate=Decimal("5.8000"),
        )
        with self.assertRaises(BackingConservationError):
            engine.process_dividend_distribution(bad_dist)

        # Programa inexistente lança ProgramNotFoundError
        with self.assertRaises(ProgramNotFoundError):
            engine.get_program("GOOGL34")


if __name__ == "__main__":
    unittest.main()
