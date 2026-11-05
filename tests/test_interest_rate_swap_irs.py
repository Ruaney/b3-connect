"""Suíte de Testes Unitários e Invariantes Formais para Swaps de Taxa de Juros com Ajustes Periódicos (IRS - FL-080).

Valida determinismo, precisão decimal, acumulação de CDI diário, liquidação de ajuste periódico,
conservação bilateral soma-zero (LAW-080) e não-arbitragem na curva Pré/DI (LAW-081) no Balcão NoMe B3.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from src.invariants import InvariantViolationError
from src.interest_rate_swap_irs import (
    ArbitrageViolationError,
    DuplicateIrsContractError,
    GuaranteeMode,
    InterestRateSwapContract,
    InterestRateSwapEngine,
    IrsContractNotFoundError,
    IrsContractSettlementResult,
    IrsIndexerType,
    IrsLegSpec,
    IrsPeriodSettlementResult,
    IrsResetPeriod,
    IrsValidationError,
    PreDiZeroCurve,
    ResetFrequency,
    ZeroCurveVertex,
    regressive_irrf_rate_for_days,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "interest_rate_swap_irs.json"
)


class TestInterestRateSwapIrs(unittest.TestCase):
    """Testes completos do motor FL-080 (IRS com resets periódicos no Balcão B3)."""

    def test_load_fixture_and_settle_all(self) -> None:
        """Carrega a fixture oficial FL-080 e valida a liquidação e conservação de todos os contratos."""
        engine = InterestRateSwapEngine.from_json_fixture(FIXTURE_PATH)
        self.assertEqual(len(engine.contracts), 4)
        self.assertIn("CURVA-PRE-DI-2026", engine.curves)

        results = engine.settle_all()
        self.assertEqual(len(results), 4)

        for res in results:
            self.assertTrue(
                res.is_zero_sum_conserved,
                f"Falha soma-zero cumulativa no contrato {res.contract_id}",
            )
            self.assertTrue(
                res.is_cashflow_balanced,
                f"Falha equilíbrio de caixa no contrato {res.contract_id}",
            )
            self.assertEqual(
                res.cum_party_a_gross_brl + res.cum_party_b_gross_brl,
                Decimal("0.00"),
            )

            # Valida cada período individual
            for pr in res.period_settlements:
                self.assertTrue(
                    pr.is_zero_sum_conserved,
                    f"Falha soma-zero período {pr.period_index} no contrato {res.contract_id}",
                )
                self.assertTrue(
                    pr.is_cashflow_balanced,
                    f"Falha fluxo de caixa período {pr.period_index} no contrato {res.contract_id}",
                )
                self.assertEqual(
                    pr.party_a_gross_brl + pr.party_b_gross_brl,
                    Decimal("0.00"),
                )

        curve = engine.curves["CURVA-PRE-DI-2026"]
        invariants = engine.verify_invariants(curve=curve)
        self.assertTrue(invariants["irs_period_zero_sum"])
        self.assertTrue(invariants["irs_cashflow_balanced"])
        self.assertTrue(invariants["irs_cumulative_zero_sum"])
        self.assertTrue(invariants["no_arbitrage_pre_di"])

        summary = engine.portfolio_summary()
        self.assertEqual(summary["total_contracts"], 4)
        self.assertEqual(summary["total_reset_events"], 12)  # 4 + 4 + 3 + 1
        self.assertTrue(summary["all_zero_sum_conserved"])
        self.assertTrue(summary["all_cashflow_balanced"])

    def test_daily_cdi_accumulation_cetip_formula(self) -> None:
        """Valida a fórmula oficial Cetip/B3 de produto de fatores diários (1 + CDI_k/100)^(1/252)."""
        leg = IrsLegSpec(
            indexer=IrsIndexerType.CDI,
            rate_annual_pct=Decimal("13.6500"),
            cdi_percentage=Decimal("100.0000"),
            spread_annual_pct=Decimal("0.0000"),
        )

        # 21 dias úteis a taxa constante de 13.65% a.a.
        daily_rates = [Decimal("13.6500")] * 21
        factor_daily = leg.calculate_factor(business_days_du=21, daily_cdi_rates=daily_rates)

        # Fator diário individual: round((1 + 0.1365)^(1/252), 8) = 1.00050785
        # 1.00050785^21 acumulado dia a dia
        self.assertIsInstance(factor_daily, Decimal)
        self.assertGreater(factor_daily, Decimal("1.00000000"))

        # Fator equivalente com cálculo direto
        factor_direct = leg.calculate_factor(business_days_du=21)
        # A diferença entre diário composto e taxa efetiva exponencial direta é de no máximo alguns micros devido aos 8 decimais
        diff = abs(factor_daily - factor_direct)
        self.assertLessEqual(diff, Decimal("0.00001000"))

        # Teste com percentual do CDI (ex: 110% do CDI)
        leg_110 = IrsLegSpec(
            indexer=IrsIndexerType.CDI,
            rate_annual_pct=Decimal("13.6500"),
            cdi_percentage=Decimal("110.0000"),
        )
        factor_110 = leg_110.calculate_factor(business_days_du=21, daily_cdi_rates=daily_rates)
        self.assertGreater(factor_110, factor_daily)

        # Teste com spread adicional (ex: CDI + 1.00% a.a.)
        leg_spread = IrsLegSpec(
            indexer=IrsIndexerType.CDI,
            rate_annual_pct=Decimal("13.6500"),
            cdi_percentage=Decimal("100.0000"),
            spread_annual_pct=Decimal("1.0000"),
        )
        factor_spread = leg_spread.calculate_factor(business_days_du=21, daily_cdi_rates=daily_rates)
        self.assertGreater(factor_spread, factor_daily)

    def test_pre_vs_di_quarterly_resets_exact_numbers(self) -> None:
        """Valida cálculos exatos de fluxo em Swap Pré x DI com 4 trimestres (252 DU)."""
        contract = InterestRateSwapContract(
            contract_id="IRS-UNIT-PRE-DI-001",
            nome_protocol="NOME-TEST-001",
            trade_date=date(2026, 1, 2),
            effective_date=date(2026, 1, 5),
            maturity_date=date(2027, 1, 5),
            reset_frequency=ResetFrequency.QUARTERLY,
            notional_brl=Decimal("10000000.00"),
            party_a_name="Empresa A S.A.",
            party_a_document="01.001.001/0001-01",
            party_a_irrf_rate=Decimal("0.1500"),
            party_b_name="Banco B S.A.",
            party_b_document="02.002.002/0001-02",
            party_b_irrf_rate=Decimal("0.1500"),
            active_leg=IrsLegSpec(indexer=IrsIndexerType.PRE, rate_annual_pct=Decimal("14.0000")),
            passive_leg=IrsLegSpec(indexer=IrsIndexerType.CDI, rate_annual_pct=Decimal("13.0000")),
            periods=[
                IrsResetPeriod(
                    period_index=1,
                    start_date=date(2026, 1, 5),
                    end_date=date(2026, 4, 6),
                    settlement_date=date(2026, 4, 7),
                    business_days_du=63,
                    calendar_days_dc=91,
                    notional_brl=Decimal("10000000.00"),
                    passive_rate_override=Decimal("13.0000"),
                ),
                IrsResetPeriod(
                    period_index=2,
                    start_date=date(2026, 4, 6),
                    end_date=date(2026, 7, 6),
                    settlement_date=date(2026, 7, 7),
                    business_days_du=63,
                    calendar_days_dc=91,
                    notional_brl=Decimal("10000000.00"),
                    passive_rate_override=Decimal("15.0000"),  # DI subiu acima do Pré
                ),
            ],
        )

        engine = InterestRateSwapEngine()
        res = engine.settle_contract(contract)

        self.assertEqual(res.total_periods, 2)
        p1 = res.period_settlements[0]
        p2 = res.period_settlements[1]

        # No período 1, Pré 14.0% > DI 13.0% => Parte A ganha
        self.assertGreater(p1.party_a_gross_brl, Decimal("0.00"))
        self.assertLess(p1.party_b_gross_brl, Decimal("0.00"))
        self.assertEqual(p1.party_a_gross_brl + p1.party_b_gross_brl, Decimal("0.00"))
        self.assertEqual(p1.receiver_document, "01.001.001/0001-01")
        self.assertEqual(p1.payer_document, "02.002.002/0001-02")
        self.assertGreater(p1.party_a_irrf_brl, Decimal("0.00"))
        self.assertEqual(p1.party_b_irrf_brl, Decimal("0.00"))  # perdedor não tem IRRF

        # No período 2, Pré 14.0% < DI 15.0% => Parte B ganha
        self.assertLess(p2.party_a_gross_brl, Decimal("0.00"))
        self.assertGreater(p2.party_b_gross_brl, Decimal("0.00"))
        self.assertEqual(p2.party_a_gross_brl + p2.party_b_gross_brl, Decimal("0.00"))
        self.assertEqual(p2.receiver_document, "02.002.002/0001-02")
        self.assertEqual(p2.payer_document, "01.001.001/0001-01")
        self.assertEqual(p2.party_a_irrf_brl, Decimal("0.00"))
        self.assertGreater(p2.party_b_irrf_brl, Decimal("0.00"))

        # Conservação formal
        self.assertEqual(res.cum_party_a_gross_brl + res.cum_party_b_gross_brl, Decimal("0.00"))
        self.assertEqual(
            res.cum_party_a_net_brl + res.cum_party_b_net_brl + res.cum_total_irrf_brl,
            Decimal("0.00"),
        )

    def test_ipca_vs_di_semiannual_resets(self) -> None:
        """Valida contrato IPCA x DI com inflação e cupom real."""
        contract = InterestRateSwapContract(
            contract_id="IRS-IPCA-UNIT-002",
            nome_protocol="NOME-TEST-002",
            trade_date=date(2026, 1, 15),
            effective_date=date(2026, 1, 16),
            maturity_date=date(2026, 7, 16),
            reset_frequency=ResetFrequency.SEMIANNUAL,
            notional_brl=Decimal("50000000.00"),
            party_a_name="Fundo IPCA",
            party_a_document="10.000.000/0001-00",
            party_a_irrf_exempt=True,
            party_b_name="Tesouraria",
            party_b_document="20.000.000/0001-00",
            party_b_irrf_rate=Decimal("0.2000"),
            active_leg=IrsLegSpec(indexer=IrsIndexerType.IPCA, rate_annual_pct=Decimal("6.0000")),
            passive_leg=IrsLegSpec(indexer=IrsIndexerType.CDI, rate_annual_pct=Decimal("13.0000")),
            periods=[
                IrsResetPeriod(
                    period_index=1,
                    start_date=date(2026, 1, 16),
                    end_date=date(2026, 7, 16),
                    settlement_date=date(2026, 7, 17),
                    business_days_du=126,
                    calendar_days_dc=181,
                    notional_brl=Decimal("50000000.00"),
                    ipca_period_pct=Decimal("3.5000"),  # IPCA forte
                    passive_rate_override=Decimal("13.5000"),
                )
            ],
        )

        engine = InterestRateSwapEngine()
        res = engine.settle_contract(contract)
        pr = res.period_settlements[0]

        # Fator IPCA: (1 + 0.035) * (1 + 0.06)^(126/252) = 1.035 * 1.02956301 = 1.06559772
        expected_active_factor = Decimal("1.06559772")
        self.assertEqual(pr.active_factor, expected_active_factor)

        # Fator CDI: (1 + 0.1350)^(126/252) = 1.06536379
        expected_passive_factor = Decimal("1.06536379")
        self.assertEqual(pr.passive_factor, expected_passive_factor)

        # Como active > passive => Party A ganha
        self.assertGreater(pr.party_a_gross_brl, Decimal("0.00"))
        self.assertEqual(pr.party_a_gross_brl + pr.party_b_gross_brl, Decimal("0.00"))
        # Party A é isenta
        self.assertEqual(pr.party_a_irrf_brl, Decimal("0.00"))
        self.assertEqual(pr.party_a_net_brl, pr.party_a_gross_brl)

    def test_amortizing_notional_annual_resets(self) -> None:
        """Valida IRS com nocional amortizado ao longo de 3 períodos anuais."""
        contract = InterestRateSwapContract(
            contract_id="IRS-AMORT-UNIT-003",
            nome_protocol="NOME-TEST-003",
            trade_date=date(2026, 2, 2),
            effective_date=date(2026, 2, 3),
            maturity_date=date(2029, 2, 5),
            reset_frequency=ResetFrequency.ANNUAL,
            notional_brl=Decimal("30000000.00"),
            party_a_name="Cliente A",
            party_a_document="11.111.111/0001-11",
            party_a_irrf_rate=Decimal("0.1500"),
            party_b_name="Cliente B",
            party_b_document="22.222.222/0001-22",
            party_b_irrf_rate=Decimal("0.1500"),
            active_leg=IrsLegSpec(indexer=IrsIndexerType.PRE, rate_annual_pct=Decimal("12.0000")),
            passive_leg=IrsLegSpec(indexer=IrsIndexerType.CDI, rate_annual_pct=Decimal("12.0000")),
            periods=[
                IrsResetPeriod(
                    period_index=1,
                    start_date=date(2026, 2, 3),
                    end_date=date(2027, 2, 3),
                    settlement_date=date(2027, 2, 4),
                    business_days_du=252,
                    calendar_days_dc=365,
                    notional_brl=Decimal("30000000.00"),
                    passive_rate_override=Decimal("11.5000"),
                ),
                IrsResetPeriod(
                    period_index=2,
                    start_date=date(2027, 2, 3),
                    end_date=date(2028, 2, 3),
                    settlement_date=date(2028, 2, 4),
                    business_days_du=252,
                    calendar_days_dc=365,
                    notional_brl=Decimal("20000000.00"),
                    passive_rate_override=Decimal("12.5000"),
                ),
                IrsResetPeriod(
                    period_index=3,
                    start_date=date(2028, 2, 3),
                    end_date=date(2029, 2, 5),
                    settlement_date=date(2029, 2, 6),
                    business_days_du=252,
                    calendar_days_dc=368,
                    notional_brl=Decimal("10000000.00"),
                    passive_rate_override=Decimal("12.0000"),
                ),
            ],
        )

        engine = InterestRateSwapEngine()
        res = engine.settle_contract(contract)

        self.assertEqual(res.period_settlements[0].notional_brl, Decimal("30000000.00"))
        self.assertEqual(res.period_settlements[1].notional_brl, Decimal("20000000.00"))
        self.assertEqual(res.period_settlements[2].notional_brl, Decimal("10000000.00"))

        # No período 3, taxas iguais (12% vs 12%) em 252 DU => ajuste zero!
        self.assertEqual(res.period_settlements[2].party_a_gross_brl, Decimal("0.00"))
        self.assertEqual(res.period_settlements[2].party_b_gross_brl, Decimal("0.00"))
        self.assertEqual(res.period_settlements[2].total_irrf_brl, Decimal("0.00"))

        self.assertTrue(res.is_zero_sum_conserved)
        self.assertTrue(res.is_cashflow_balanced)

    def test_zero_sum_invariant_cent_by_cent_adversarial(self) -> None:
        """Ataque adversarial com frações assimétricas para verificar invariante soma-zero estrito."""
        contract = InterestRateSwapContract(
            contract_id="IRS-ADV-CENT-001",
            nome_protocol="NOME-ADV-001",
            trade_date=date(2026, 1, 2),
            effective_date=date(2026, 1, 5),
            maturity_date=date(2026, 4, 6),
            reset_frequency=ResetFrequency.CUSTOM,
            notional_brl=Decimal("1234567.89"),  # valor não redondo
            party_a_name="A",
            party_a_document="11.111.111/0001-11",
            party_a_irrf_rate=Decimal("0.2250"),
            party_b_name="B",
            party_b_document="22.222.222/0001-22",
            party_b_irrf_rate=Decimal("0.2000"),
            active_leg=IrsLegSpec(indexer=IrsIndexerType.PRE, rate_annual_pct=Decimal("13.7891")),
            passive_leg=IrsLegSpec(indexer=IrsIndexerType.CDI, rate_annual_pct=Decimal("13.4321")),
            periods=[
                IrsResetPeriod(
                    period_index=1,
                    start_date=date(2026, 1, 5),
                    end_date=date(2026, 4, 6),
                    settlement_date=date(2026, 4, 7),
                    business_days_du=63,
                    calendar_days_dc=91,
                    notional_brl=Decimal("1234567.89"),
                )
            ],
        )

        engine = InterestRateSwapEngine()
        res = engine.settle_contract(contract)
        pr = res.period_settlements[0]

        # Centavo a centavo absoluto
        self.assertEqual(pr.party_a_gross_brl + pr.party_b_gross_brl, Decimal("0.00"))
        self.assertEqual(pr.party_a_net_brl + pr.party_b_net_brl + pr.total_irrf_brl, Decimal("0.00"))

    def test_pre_di_zero_curve_no_arbitrage_invariant(self) -> None:
        """Testa o invariante de não-arbitragem LAW-081 e cálculo da Fair Swap Rate."""
        vertices = [
            ZeroCurveVertex(du_days=0, zero_rate_pct=Decimal("0.0000")),
            ZeroCurveVertex(du_days=63, zero_rate_pct=Decimal("13.5000")),
            ZeroCurveVertex(du_days=126, zero_rate_pct=Decimal("13.7000")),
            ZeroCurveVertex(du_days=189, zero_rate_pct=Decimal("13.9000")),
            ZeroCurveVertex(du_days=252, zero_rate_pct=Decimal("14.0000")),
        ]
        curve = PreDiZeroCurve(curve_date=date(2026, 1, 2), vertices=vertices)

        periods_du = [63, 63, 63, 63]
        self.assertTrue(curve.verify_no_arbitrage(periods_du))

        # Testa Fair Swap Rate
        fair_rate = curve.fair_swap_rate(periods_du)
        self.assertIsInstance(fair_rate, Decimal)
        # Fair rate deve ficar próxima da taxa zero de 252 DU (~14.00%)
        self.assertGreater(fair_rate, Decimal("13.4000"))
        self.assertLess(fair_rate, Decimal("14.2000"))

    def test_arbitrage_detection_on_distorted_curve(self) -> None:
        """Verifica se distorções e inversões de curva que permitem arbitragem disparam InvariantViolationError."""
        # Curva com taxa negativa bizarra gerando fator forward < 1
        bad_vertices = [
            ZeroCurveVertex(du_days=0, zero_rate_pct=Decimal("0.0000")),
            ZeroCurveVertex(du_days=63, zero_rate_pct=Decimal("20.0000")),
            ZeroCurveVertex(du_days=126, zero_rate_pct=Decimal("5.0000")),  # queda brutal criando forward negativo
        ]
        bad_curve = PreDiZeroCurve(curve_date=date(2026, 1, 2), vertices=bad_vertices)

        with self.assertRaises(InvariantViolationError):
            bad_curve.verify_no_arbitrage([63, 63])

    def test_serialization_and_roundtrip(self) -> None:
        """Testa serialização to_dict/from_dict e persistência em arquivo JSON."""
        engine = InterestRateSwapEngine.from_json_fixture(FIXTURE_PATH)
        engine.settle_all()

        data = engine.to_dict()
        self.assertIn("contracts", data)
        self.assertIn("curves", data)
        self.assertIn("settlements", data)

        reloaded = InterestRateSwapEngine.from_dict(data)
        self.assertEqual(len(reloaded.contracts), len(engine.contracts))
        self.assertEqual(len(reloaded.settlements), len(engine.settlements))

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            engine.save_json(tmp_path)
            content = tmp_path.read_text(encoding="utf-8")
            parsed = json.loads(content)
            self.assertEqual(len(parsed["contracts"]), 4)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_regressive_irrf_rate_thresholds(self) -> None:
        """Valida os intervalos da tabela regressiva de IRRF (IN RFB 1.585/2015)."""
        self.assertEqual(regressive_irrf_rate_for_days(30), Decimal("0.2250"))
        self.assertEqual(regressive_irrf_rate_for_days(180), Decimal("0.2250"))
        self.assertEqual(regressive_irrf_rate_for_days(181), Decimal("0.2000"))
        self.assertEqual(regressive_irrf_rate_for_days(360), Decimal("0.2000"))
        self.assertEqual(regressive_irrf_rate_for_days(361), Decimal("0.1750"))
        self.assertEqual(regressive_irrf_rate_for_days(720), Decimal("0.1750"))
        self.assertEqual(regressive_irrf_rate_for_days(721), Decimal("0.1500"))
        self.assertEqual(regressive_irrf_rate_for_days(1500), Decimal("0.1500"))

        with self.assertRaises(IrsValidationError):
            regressive_irrf_rate_for_days(0)
        with self.assertRaises(IrsValidationError):
            regressive_irrf_rate_for_days(-10)

    def test_validation_errors(self) -> None:
        """Valida que entradas inválidas geram IrsValidationError."""
        # Vencimento anterior ao início
        with self.assertRaises(IrsValidationError):
            InterestRateSwapContract(
                contract_id="BAD-001",
                nome_protocol="PROTO-001",
                trade_date=date(2026, 1, 2),
                effective_date=date(2026, 1, 5),
                maturity_date=date(2025, 1, 5),
                reset_frequency=ResetFrequency.ANNUAL,
                notional_brl=Decimal("1000000.00"),
                party_a_name="A",
                party_a_document="11.111.111/0001-11",
                party_b_name="B",
                party_b_document="22.222.222/0001-22",
                active_leg=IrsLegSpec(indexer=IrsIndexerType.PRE),
                passive_leg=IrsLegSpec(indexer=IrsIndexerType.CDI),
                periods=[
                    IrsResetPeriod(
                        period_index=1,
                        start_date=date(2026, 1, 5),
                        end_date=date(2025, 1, 5),
                        settlement_date=date(2025, 1, 6),
                        business_days_du=252,
                        calendar_days_dc=365,
                        notional_brl=Decimal("1000000.00"),
                    )
                ],
            )

        # Operação consigo mesmo
        with self.assertRaises(IrsValidationError):
            InterestRateSwapContract(
                contract_id="BAD-002",
                nome_protocol="PROTO-002",
                trade_date=date(2026, 1, 2),
                effective_date=date(2026, 1, 5),
                maturity_date=date(2027, 1, 5),
                reset_frequency=ResetFrequency.ANNUAL,
                notional_brl=Decimal("1000000.00"),
                party_a_name="A",
                party_a_document="11.111.111/0001-11",
                party_b_name="A Mesma",
                party_b_document="11.111.111/0001-11",  # Mesmo CNPJ
                active_leg=IrsLegSpec(indexer=IrsIndexerType.PRE),
                passive_leg=IrsLegSpec(indexer=IrsIndexerType.CDI),
                periods=[
                    IrsResetPeriod(
                        period_index=1,
                        start_date=date(2026, 1, 5),
                        end_date=date(2027, 1, 5),
                        settlement_date=date(2027, 1, 6),
                        business_days_du=252,
                        calendar_days_dc=365,
                        notional_brl=Decimal("1000000.00"),
                    )
                ],
            )

        # Contrato duplicado
        engine = InterestRateSwapEngine()
        c = InterestRateSwapContract(
            contract_id="DUP-001",
            nome_protocol="PROTO-DUP",
            trade_date=date(2026, 1, 2),
            effective_date=date(2026, 1, 5),
            maturity_date=date(2027, 1, 5),
            reset_frequency=ResetFrequency.ANNUAL,
            notional_brl=Decimal("1000000.00"),
            party_a_name="A",
            party_a_document="11.111.111/0001-11",
            party_b_name="B",
            party_b_document="22.222.222/0001-22",
            active_leg=IrsLegSpec(indexer=IrsIndexerType.PRE),
            passive_leg=IrsLegSpec(indexer=IrsIndexerType.CDI),
            periods=[
                IrsResetPeriod(
                    period_index=1,
                    start_date=date(2026, 1, 5),
                    end_date=date(2027, 1, 5),
                    settlement_date=date(2027, 1, 6),
                    business_days_du=252,
                    calendar_days_dc=365,
                    notional_brl=Decimal("1000000.00"),
                )
            ],
        )
        engine.register_contract(c)
        with self.assertRaises(DuplicateIrsContractError):
            engine.register_contract(c)

        # Contrato inexistente
        with self.assertRaises(IrsContractNotFoundError):
            engine.get_contract("NON-EXISTENT")

    def test_direct_invariants_laws_80_and_81(self) -> None:
        """Testa diretamente as funções de asserção formal LAW-080 e LAW-081."""
        from src.invariants import (
            assert_irs_periodic_reset_zero_sum,
            assert_no_arbitrage_pre_di_curve,
        )

        # LAW-080: soma-zero exata passa
        assert_irs_periodic_reset_zero_sum(
            party_a_amount=Decimal("150000.25"),
            party_b_amount=Decimal("-150000.25"),
            period_index=1,
            contract_id="IRS-TEST-LAW80",
        )

        # LAW-080: desbalanceamento de 1 centavo deve disparar InvariantViolationError
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_irs_periodic_reset_zero_sum(
                party_a_amount=Decimal("150000.25"),
                party_b_amount=Decimal("-150000.24"),
                period_index=1,
                contract_id="IRS-TEST-LAW80-FAIL",
            )
        self.assertIn("LAW-080", str(ctx.exception))

        # LAW-081: não-arbitragem telescópica passa dentro da tolerância
        assert_no_arbitrage_pre_di_curve(
            telescoping_forward_sum=Decimal("0.12345"),
            terminal_discount_factor=Decimal("0.87655"),
            initial_discount_factor=Decimal("1.00000"),
            tolerance=Decimal("0.0001"),
        )

        # LAW-081: quebra da soma telescópica deve disparar InvariantViolationError
        with self.assertRaises(InvariantViolationError) as ctx81:
            assert_no_arbitrage_pre_di_curve(
                telescoping_forward_sum=Decimal("0.15000"),  # desviou muito
                terminal_discount_factor=Decimal("0.87655"),
                initial_discount_factor=Decimal("1.00000"),
                tolerance=Decimal("0.0001"),
            )
        self.assertIn("LAW-081", str(ctx81.exception))

    def test_irs_leg_explicit_factor_and_overrides(self) -> None:
        """Testa sobreposição de fator explícito e sobrescritas de taxas na ponta."""
        leg = IrsLegSpec(indexer=IrsIndexerType.PRE, rate_annual_pct=Decimal("10.0000"))
        # Com explicit_factor, ignora a taxa e DU
        f = leg.calculate_factor(business_days_du=63, explicit_factor=Decimal("1.05000000"))
        self.assertEqual(f, Decimal("1.05000000"))

        # Com rate_override, recalcula com a taxa fornecida
        f_override = leg.calculate_factor(business_days_du=252, rate_override=Decimal("15.0000"))
        self.assertEqual(f_override, Decimal("1.15000000"))

    def test_pre_di_zero_curve_interpolation_and_forwards(self) -> None:
        """Testa interpolação linear e taxas forward na curva Pré/DI."""
        vertices = [
            ZeroCurveVertex(du_days=0, zero_rate_pct=Decimal("0.0000")),
            ZeroCurveVertex(du_days=100, zero_rate_pct=Decimal("10.0000")),
            ZeroCurveVertex(du_days=200, zero_rate_pct=Decimal("12.0000")),
        ]
        curve = PreDiZeroCurve(curve_date=date(2026, 1, 2), vertices=vertices)

        # DU = 0 => 0.0000%
        self.assertEqual(curve.zero_rate_at(0), Decimal("0.0000"))
        # DU = 100 => 10.0000%
        self.assertEqual(curve.zero_rate_at(100), Decimal("10.0000"))
        # DU = 150 => 11.0000%
        self.assertEqual(curve.zero_rate_at(150), Decimal("11.0000"))
        # Além do final => taxa do último vértice
        self.assertEqual(curve.zero_rate_at(300), Decimal("12.0000"))

        # Forward entre 100 e 200 DU
        fwd_rate = curve.forward_annual_rate(100, 200)
        self.assertIsInstance(fwd_rate, Decimal)
        self.assertGreater(fwd_rate, Decimal("12.0000"))  # forward de curva inclinada positivamente é superior à zero

    def test_enums_and_validation_corner_cases(self) -> None:
        """Testa robustez de parsing de enums e validações de tipos."""
        self.assertEqual(ResetFrequency.from_str("trimestral"), ResetFrequency.QUARTERLY)
        self.assertEqual(ResetFrequency.from_str("semestral"), ResetFrequency.SEMIANNUAL)
        self.assertEqual(ResetFrequency.from_str("anual"), ResetFrequency.ANNUAL)
        self.assertEqual(ResetFrequency.from_str("mensal"), ResetFrequency.MONTHLY)
        self.assertEqual(ResetFrequency.from_str("custom"), ResetFrequency.CUSTOM)

        with self.assertRaises(IrsValidationError):
            ResetFrequency.from_str("INVALIDO")

        self.assertEqual(IrsIndexerType.from_str("DI"), IrsIndexerType.CDI)
        self.assertEqual(IrsIndexerType.from_str("pré"), IrsIndexerType.PRE)
        self.assertEqual(IrsIndexerType.from_str("inflação"), IrsIndexerType.IPCA)

        with self.assertRaises(IrsValidationError):
            IrsIndexerType.from_str("INVALIDO")

        self.assertEqual(GuaranteeMode.from_str("ccp"), GuaranteeMode.COM_GARANTIA_CCP)
        self.assertEqual(GuaranteeMode.from_str("bilateral"), GuaranteeMode.SEM_GARANTIA_CCP)

        with self.assertRaises(IrsValidationError):
            GuaranteeMode.from_str("INVALIDO")


if __name__ == "__main__":
    unittest.main()

