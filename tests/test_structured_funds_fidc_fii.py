"""Bateria de Testes Unitários — Fundos Estruturados: FIDC e FII no Balcão NoMe B3 (FL-077).

Testa exaustivamente com 100% de conformidade com a Resolução CVM nº 175 (Anexos II e III):
1. Modelagem, tipos e precisão Decimal:
   - Validações cadastrais, conversão de datas e números, valores não-negativos.
2. Estrutura de Classes e Subclasses de Cotas FIDC:
   - Cotas Seniores, Mezanino e Subordinada Júnior.
   - Subordination Ratio (Razão de Subordinação) e gatilhos de desenquadramento.
3. Invariante de Subordinação Estrita (Water-Tight Priority):
   - First-loss piece: Subordinada Júnior absorve 100% do prejuízo até seu PL se esgotar.
   - Segunda perda: Mezanino absorve perdas apenas após zeramento da Júnior.
   - Sênior intocada enquanto houver patrimônio em Júnior ou Mezanino.
   - Perda catastrófica atingindo a Sênior somente após exaustão das subclasses subordinadas.
4. Cascata de Amortização e Pagamentos de FIDC (Payment Waterfall):
   - Adimplência plena com remuneração residual da Júnior e conservação de caixa.
   - Estresse de liquidez com corte total para Mezanino e Júnior e prioridade absoluta da Sênior.
   - Conservação estrita: Caixa_Inicial == Despesas + Serviço_Sênior + Serviço_Mezanino + Júnior + Caixa_Final.
5. Fundos de Investimento Imobiliário (FII) e Ativos Imobiliários:
   - Imóveis físicos (tijolo), CRIs (papel), avaliação a valor de mercado e cálculo de NAV.
6. Invariante de Rendimentos Obrigatórios FII (Lei nº 8.668/1993, Art. 10 § único):
   - Distribuição mandatória mínima de 95% do lucro líquido apurado em regime de caixa semestral.
   - Rejeição regulatória com FiiRegulatoryDistributionError para distribuições abaixo de 95%.
   - Tratamento de regime de caixa negativo (sem obrigação de distribuição) e reserva acumulada.
7. Conservação Patrimonial de Cotas:
   - FIDC: PL_Total == PL_Senior + PL_Mezanino + PL_Junior == Ativos_Totais - Passivo_Exigivel.
   - FII: PL_FII == Quantidade_Cotas * Valor_Cota == Ativos_Totais - Passivo_Exigivel.
8. Fixture Oficial e Serialização:
   - Carga do fixture sintético fixtures/structured_funds_fidc_fii.json.
   - Auditoria e aprovação de verify_all_invariants() em todos os fundos.
   - Teste de idempotência to_dict() / from_dict().
"""

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.structured_funds_fidc_fii import (
    CreditReceivable,
    DistributionFrequency,
    FidcFund,
    FidcLossAbsorptionResult,
    FidcQuotaClass,
    FidcQuotaSubclass,
    FidcSubordinationBreachError,
    FidcWaterfallResult,
    FiiAsset,
    FiiAssetType,
    FiiCashFlowStatement,
    FiiDistributionEvent,
    FiiFund,
    FiiQuotaClass,
    FiiRegulatoryDistributionError,
    FundCategory,
    ReceivableStatus,
    ReceivableType,
    SettlementCycle,
    StructuredFundError,
    StructuredFundsEngine,
    StructuredFundValidationError,
    round_cents,
    round_rate,
    round_unit,
    to_date,
    to_decimal,
)


class TestStructuredFundsFidcFiiModel(unittest.TestCase):
    """Testes unitários de validação de modelos e precisão numérica."""

    def test_precision_helpers(self) -> None:
        """Verifica precisão e quantização financeira oficial B3."""
        self.assertEqual(round_cents(Decimal("123.456")), Decimal("123.46"))
        self.assertEqual(round_cents(Decimal("123.454")), Decimal("123.45"))
        self.assertEqual(round_unit(Decimal("100.123456789")), Decimal("100.12345679"))
        self.assertEqual(round_rate(Decimal("0.123456789")), Decimal("0.12345679"))
        self.assertEqual(to_date("2026-11-04"), date(2026, 11, 4))
        self.assertEqual(to_decimal("42.50"), Decimal("42.50"))

    def test_invalid_date_and_decimal_conversions(self) -> None:
        """Verifica tratamento de dados inválidos em conversões."""
        with self.assertRaises(StructuredFundValidationError):
            to_date("data-invalida")
        with self.assertRaises(StructuredFundValidationError):
            to_date(12345)
        with self.assertRaises(StructuredFundValidationError):
            to_decimal(None)
        with self.assertRaises(StructuredFundValidationError):
            to_decimal("abc")

    def test_credit_receivable_validation(self) -> None:
        """Verifica criação e invariantes de um direito creditório."""
        rec = CreditReceivable(
            receivable_id="REC-001",
            debtor_name="Devedor Teste",
            debtor_cnpj_cpf="11.222.333/0001-44",
            receivable_type=ReceivableType.TRADE_DUPLICATA,
            original_face_value=Decimal("100000.00"),
            current_outstanding_balance=Decimal("100000.00"),
            acquisition_discount_rate_annual=Decimal("0.12000000"),
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 6, 1),
        )
        self.assertEqual(rec.net_book_value, Decimal("100000.00"))
        self.assertEqual(rec.status, ReceivableStatus.PERFORMING)

        # Provisão por atraso
        rec.mark_past_due(days=35, pld_pct=Decimal("0.20"))
        self.assertEqual(rec.status, ReceivableStatus.PAST_DUE)
        self.assertEqual(rec.pld_provision_amount, Decimal("20000.00"))
        self.assertEqual(rec.net_book_value, Decimal("80000.00"))

        # Default com 95 dias de atraso
        rec.mark_past_due(days=95, pld_pct=Decimal("0.50"))
        self.assertEqual(rec.status, ReceivableStatus.DEFAULTED)
        self.assertEqual(rec.pld_provision_amount, Decimal("50000.00"))
        self.assertEqual(rec.net_book_value, Decimal("50000.00"))

        # Baixa a prejuízo (write-off)
        loss = rec.write_off()
        self.assertEqual(loss, Decimal("50000.00"))
        self.assertEqual(rec.status, ReceivableStatus.WRITTEN_OFF)
        self.assertEqual(rec.net_book_value, Decimal("0.00"))

    def test_credit_receivable_invalid_constraints(self) -> None:
        """Verifica validações de integridade no recebível."""
        with self.assertRaises(StructuredFundValidationError):
            CreditReceivable(
                receivable_id="REC-INV-1",
                debtor_name="Devedor",
                debtor_cnpj_cpf="123",
                receivable_type=ReceivableType.TRADE_DUPLICATA,
                original_face_value=Decimal("-10.00"),  # negativo
                current_outstanding_balance=Decimal("10.00"),
                acquisition_discount_rate_annual=Decimal("0.10"),
                issue_date=date(2026, 1, 1),
                due_date=date(2026, 6, 1),
            )
        with self.assertRaises(StructuredFundValidationError):
            CreditReceivable(
                receivable_id="REC-INV-2",
                debtor_name="Devedor",
                debtor_cnpj_cpf="123",
                receivable_type=ReceivableType.TRADE_DUPLICATA,
                original_face_value=Decimal("1000.00"),
                current_outstanding_balance=Decimal("1000.00"),
                acquisition_discount_rate_annual=Decimal("0.10"),
                issue_date=date(2026, 6, 1),
                due_date=date(2026, 1, 1),  # vencimento anterior
            )


class TestFidcSubordinationAndWaterfall(unittest.TestCase):
    """Testes da dinâmica de subordinação estrita, absorção de perdas e waterfall de FIDC."""

    def setUp(self) -> None:
        """Cria estrutura padrão de FIDC tranches Sênior, Mezanino e Júnior."""
        self.sr_quota = FidcQuotaSubclass(
            subclass_id="SR-01",
            isin="BRFIDCCT0015",
            b3_ticker="FIDC11SR",
            quota_class=FidcQuotaClass.SENIOR,
            benchmark_indexer="CDI",
            target_spread_annual=Decimal("0.015"),
            quantity=Decimal("10000"),
            unit_quota_value=Decimal("100.00000000"),  # 1.000.000,00
            minimum_subordination_ratio=Decimal("0.20"),  # 20%
        )
        self.mz_quota = FidcQuotaSubclass(
            subclass_id="MZ-01",
            isin="BRFIDCCT0023",
            b3_ticker="FIDC11MZ",
            quota_class=FidcQuotaClass.MEZZANINE,
            benchmark_indexer="CDI",
            target_spread_annual=Decimal("0.035"),
            quantity=Decimal("2000"),
            unit_quota_value=Decimal("100.00000000"),  # 200.000,00
            minimum_subordination_ratio=Decimal("0.10"),
        )
        self.jr_quota = FidcQuotaSubclass(
            subclass_id="JR-01",
            isin="BRFIDCCT0031",
            b3_ticker="FIDC11JR",
            quota_class=FidcQuotaClass.SUBORDINATED_JUNIOR,
            benchmark_indexer="RESIDUAL",
            target_spread_annual=Decimal("0.00"),
            quantity=Decimal("1000"),
            unit_quota_value=Decimal("100.00000000"),  # 100.000,00
        )
        self.fund = FidcFund(
            fund_id="FIDC-TESTE-01",
            fund_name="FIDC Teste Subordinação",
            cnpj="01.234.567/0001-89",
            administrator_cnpj="00.000.000/0001-91",
            manager_cnpj="11.111.111/0001-22",
            cvm_registration_number="CVM-175-TEST",
            creation_date=date(2026, 1, 1),
            senior_quota=self.sr_quota,
            mezzanine_quota=self.mz_quota,
            junior_quota=self.jr_quota,
            cash_balance=Decimal("100000.00"),
        )
        # Lastro de 1.200.000 em recebíveis + 100.000 caixa = 1.300.000 Ativos = PL (1M + 200k + 100k)
        rec1 = CreditReceivable(
            receivable_id="REC-A",
            debtor_name="Devedor A",
            debtor_cnpj_cpf="11.111.111/0001-11",
            receivable_type=ReceivableType.TRADE_DUPLICATA,
            original_face_value=Decimal("800000.00"),
            current_outstanding_balance=Decimal("800000.00"),
            acquisition_discount_rate_annual=Decimal("0.12"),
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 12, 31),
        )
        rec2 = CreditReceivable(
            receivable_id="REC-B",
            debtor_name="Devedor B",
            debtor_cnpj_cpf="22.222.222/0001-22",
            receivable_type=ReceivableType.BANK_CREDIT_NOTE_CCB,
            original_face_value=Decimal("400000.00"),
            current_outstanding_balance=Decimal("400000.00"),
            acquisition_discount_rate_annual=Decimal("0.14"),
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 12, 31),
        )
        self.fund.add_receivable(rec1)
        self.fund.add_receivable(rec2)

    def test_initial_subordination_ratio(self) -> None:
        """Verifica razão de subordinação inicial: (200k + 100k) / 1.300k = ~23.08% > 20%."""
        ratio = self.fund.calculate_subordination_ratio()
        self.assertAlmostEqual(float(ratio), 300000.0 / 1300000.0, places=4)
        self.assertFalse(self.fund.is_subordination_breached())

    def test_first_loss_absorption_junior_only(self) -> None:
        """Perda moderada (60.000) absorvida 100% pela Subordinada Júnior (first-loss piece)."""
        res = self.fund.absorb_credit_default(receivable_id="REC-B", loss_amount=Decimal("60000.00"))

        self.assertEqual(res.total_loss_amount, Decimal("60000.00"))
        self.assertEqual(res.absorbed_by_junior, Decimal("60000.00"))
        self.assertEqual(res.absorbed_by_mezzanine, Decimal("0.00"))
        self.assertEqual(res.absorbed_by_senior, Decimal("0.00"))
        self.assertEqual(res.unabsorbed_loss, Decimal("0.00"))
        self.assertTrue(res.is_water_tight_satisfied)

        # Júnior tinha 100k, passa a ter 40k -> PU cai de 100 para 40
        self.assertEqual(self.fund.junior_quota.total_equity, Decimal("40000.00"))
        self.assertEqual(self.fund.junior_quota.unit_quota_value, Decimal("40.00000000"))
        # Mezanino e Sênior permanecem 100% intactas
        self.assertEqual(self.fund.mezzanine_quota.total_equity, Decimal("200000.00"))
        self.assertEqual(self.fund.senior_quota.total_equity, Decimal("1000000.00"))

    def test_water_tight_loss_absorption_mezzanine(self) -> None:
        """Perda (150.000) supera Júnior (100k) e atinge Mezanino (50k), sem tocar Sênior."""
        res = self.fund.absorb_credit_default(receivable_id="REC-B", loss_amount=Decimal("150000.00"))

        self.assertEqual(res.absorbed_by_junior, Decimal("100000.00"))
        self.assertEqual(res.absorbed_by_mezzanine, Decimal("50000.00"))
        self.assertEqual(res.absorbed_by_senior, Decimal("0.00"))
        self.assertEqual(res.unabsorbed_loss, Decimal("0.00"))
        self.assertTrue(res.is_water_tight_satisfied)

        # Júnior zerada
        self.assertEqual(self.fund.junior_quota.total_equity, Decimal("0.00"))
        self.assertEqual(self.fund.junior_quota.unit_quota_value, Decimal("0.00000000"))
        # Mezanino tinha 200k, passa a 150k -> PU cai de 100 para 75
        self.assertEqual(self.fund.mezzanine_quota.total_equity, Decimal("150000.00"))
        self.assertEqual(self.fund.mezzanine_quota.unit_quota_value, Decimal("75.00000000"))
        # Sênior 100% intacta
        self.assertEqual(self.fund.senior_quota.total_equity, Decimal("1000000.00"))

        # Agora a subordinação remanescente é apenas 150k / 1.150k = 13.04% < 20% (desenquadrada!)
        self.assertTrue(self.fund.is_subordination_breached())

    def test_catastrophic_loss_senior_impact(self) -> None:
        """Perda catastrófica (350.000) zera Júnior (100k), zera Mezanino (200k) e atinge Sênior (50k)."""
        res = self.fund.absorb_credit_default(receivable_id="REC-A", loss_amount=Decimal("350000.00"))

        self.assertEqual(res.absorbed_by_junior, Decimal("100000.00"))
        self.assertEqual(res.absorbed_by_mezzanine, Decimal("200000.00"))
        self.assertEqual(res.absorbed_by_senior, Decimal("50000.00"))
        self.assertEqual(res.unabsorbed_loss, Decimal("0.00"))
        self.assertTrue(res.is_water_tight_satisfied)

        self.assertEqual(self.fund.junior_quota.total_equity, Decimal("0.00"))
        self.assertEqual(self.fund.mezzanine_quota.total_equity, Decimal("0.00"))
        # Sênior tinha 1M, passa a 950k -> PU cai para 95
        self.assertEqual(self.fund.senior_quota.total_equity, Decimal("950000.00"))
        self.assertEqual(self.fund.senior_quota.unit_quota_value, Decimal("95.00000000"))

    def test_receivable_collection(self) -> None:
        """Liquidação de recebível e entrada de caixa no FIDC."""
        inflow = self.fund.process_receivable_collection("REC-B", Decimal("200000.00"), Decimal("10000.00"))
        self.assertEqual(inflow, Decimal("210000.00"))
        self.assertEqual(self.fund.cash_balance, Decimal("310000.00"))  # 100k + 210k
        rec = self.fund.receivables["REC-B"]
        self.assertEqual(rec.current_outstanding_balance, Decimal("200000.00"))

    def test_waterfall_full_liquidity_distribution(self) -> None:
        """Execução periódica do Waterfall com caixa abundante: todas as tranches atendidas."""
        # Coloca caixa suficiente para juros da sênior, mezanino e residual da júnior
        self.fund.cash_balance = Decimal("80000.00")
        res = self.fund.execute_waterfall(
            period_date=date(2026, 6, 30),
            operational_expenses=Decimal("5000.00"),
            senior_target_interest=Decimal("15000.00"),
            senior_target_amortization=Decimal("0.00"),
            mezzanine_target_interest=Decimal("8000.00"),
            mezzanine_target_amortization=Decimal("0.00"),
        )
        self.assertEqual(res.operational_expenses_paid, Decimal("5000.00"))
        self.assertEqual(res.senior_interest_paid, Decimal("15000.00"))
        self.assertEqual(res.senior_deficit, Decimal("0.00"))
        self.assertEqual(res.mezzanine_interest_paid, Decimal("8000.00"))
        self.assertEqual(res.mezzanine_deficit, Decimal("0.00"))
        # Júnior recebe o excesso: 80k - 5k - 15k - 8k = 52k
        self.assertEqual(res.junior_remuneration_paid, Decimal("52000.00"))
        self.assertEqual(res.closing_cash, Decimal("0.00"))
        self.assertTrue(res.water_tight_priority_verified)
        self.assertTrue(res.cash_conservation_verified)

    def test_waterfall_stressed_liquidity_shortage(self) -> None:
        """Estresse de liquidez no Waterfall: Caixa só paga despesas e parte da Sênior."""
        self.fund.cash_balance = Decimal("12000.00")
        res = self.fund.execute_waterfall(
            period_date=date(2026, 6, 30),
            operational_expenses=Decimal("5000.00"),
            senior_target_interest=Decimal("15000.00"),
            mezzanine_target_interest=Decimal("8000.00"),
        )
        self.assertEqual(res.operational_expenses_paid, Decimal("5000.00"))
        self.assertEqual(res.senior_interest_paid, Decimal("7000.00"))
        self.assertEqual(res.senior_deficit, Decimal("8000.00"))
        # Mezanino e Júnior cortadas a zero absoluto
        self.assertEqual(res.mezzanine_interest_paid, Decimal("0.00"))
        self.assertEqual(res.mezzanine_deficit, Decimal("8000.00"))
        self.assertEqual(res.junior_remuneration_paid, Decimal("0.00"))
        self.assertTrue(res.water_tight_priority_verified)
        self.assertTrue(res.cash_conservation_verified)


class TestFiiOperationsAndRegulation(unittest.TestCase):
    """Testes de Fundos de Investimento Imobiliário (FII - CVM 175 Anexo III e Lei 8.668/93)."""

    def setUp(self) -> None:
        """Cria estrutura padrão de FII."""
        self.fii = FiiFund(
            fund_id="FII-ALPHA-01",
            fund_name="FII Edifícios Comerciais Alpha",
            cnpj="12.345.678/0001-99",
            b3_ticker="ALPH11",
            isin="BRALPHCTF001",
            administrator_cnpj="00.000.000/0001-91",
            manager_cnpj="22.222.222/0001-33",
            creation_date=date(2025, 1, 1),
            total_quotas=Decimal("100000"),
            unit_quota_value=Decimal("100.00000000"),  # 10.000.000,00
            cash_balance=Decimal("500000.00"),
        )
        prop = FiiAsset(
            asset_id="PROP-01",
            asset_name="Torre Alpha",
            asset_type=FiiAssetType.PHYSICAL_PROPERTY,
            acquisition_cost=Decimal("9500000.00"),
            current_valuation=Decimal("9500000.00"),
            monthly_gross_revenue=Decimal("80000.00"),
            monthly_operating_expense=Decimal("5000.00"),
        )
        self.fii.add_asset(prop)

    def test_fii_nav_and_conservation(self) -> None:
        """Verifica conservação patrimonial: PL == 100.000 * 100 = 10.000.000 == 9.5M + 500k."""
        self.assertEqual(self.fii.net_asset_value, Decimal("10000000.00"))
        invariants = self.fii.verify_invariants()
        self.assertTrue(invariants["equity_conservation_verified"])
        self.assertTrue(invariants["all_invariants_hold"])

    def test_fii_semiannual_distribution_compliant_95_percent(self) -> None:
        """Distribuição de 95% do lucro caixa semestral cumpre estritamente o Art. 10 da Lei 8.668/93."""
        # Lucro Caixa: Receitas 600k - Despesas 100k = 500k
        stmt = self.fii.record_cash_flow_period(
            statement_id="STMT-2026-S1",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 6, 30),
            is_semiannual_closing=True,
            gross_rental_revenue=Decimal("600000.00"),
            property_maintenance_expenses=Decimal("40000.00"),
            administration_fee_paid=Decimal("20000.00"),
            management_fee_paid=Decimal("40000.00"),
        )
        self.assertEqual(stmt.net_cash_profit, Decimal("500000.00"))
        self.assertEqual(stmt.mandatory_minimum_distribution_95, Decimal("475000.00"))  # 95% de 500k

        # Declara e liquida distribuição de exatamente 475.000 (95%)
        ev = self.fii.declare_and_settle_distribution(
            statement_id="STMT-2026-S1",
            record_date=date(2026, 6, 30),
            payment_date=date(2026, 7, 15),
            declared_amount=Decimal("475000.00"),
        )
        self.assertEqual(ev.declared_total_distribution, Decimal("475000.00"))
        self.assertEqual(ev.distribution_per_quota, Decimal("4.75000000"))
        self.assertTrue(ev.is_cvm175_and_lei8668_compliant)
        self.assertEqual(ev.retained_undistributed_earnings, Decimal("25000.00"))
        self.assertEqual(self.fii.accumulated_retained_earnings, Decimal("25000.00"))

        # Invariantes do FII após distribuição
        inv = self.fii.verify_invariants()
        self.assertTrue(inv["minimum_distribution_95_verified"])
        self.assertTrue(inv["all_invariants_hold"])

    def test_fii_illegal_distribution_below_95_percent_fails(self) -> None:
        """Tentativa de distribuição abaixo de 95% (ex: 90%) lança FiiRegulatoryDistributionError."""
        self.fii.record_cash_flow_period(
            statement_id="STMT-2026-S1",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 6, 30),
            is_semiannual_closing=True,
            gross_rental_revenue=Decimal("200000.00"),
        )
        # Lucro Caixa = 200k -> 95% mínimo = 190k. Tenta distribuir 180k (90%)
        with self.assertRaises(FiiRegulatoryDistributionError) as ctx:
            self.fii.declare_and_settle_distribution(
                statement_id="STMT-2026-S1",
                record_date=date(2026, 6, 30),
                payment_date=date(2026, 7, 15),
                declared_amount=Decimal("180000.00"),
            )
        self.assertIn("inferiores ao piso regulatório de 95%", str(ctx.exception))

    def test_fii_negative_cash_flow_period(self) -> None:
        """Período com resultado de caixa negativo não possui obrigação de distribuição."""
        stmt = self.fii.record_cash_flow_period(
            statement_id="STMT-NEG-01",
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            is_semiannual_closing=False,
            gross_rental_revenue=Decimal("10000.00"),
            property_maintenance_expenses=Decimal("50000.00"),
        )
        self.assertEqual(stmt.net_cash_profit, Decimal("-40000.00"))
        self.assertEqual(stmt.mandatory_minimum_distribution_95, Decimal("0.00"))


class TestStructuredFundsEngineAndFixtures(unittest.TestCase):
    """Testes de integração com a engine completa e validação do fixture oficial."""

    def test_fixture_loading_and_invariants(self) -> None:
        """Carrega fixtures/structured_funds_fidc_fii.json e audita todas as invariantes."""
        fixture_path = ROOT_DIR / "fixtures" / "structured_funds_fidc_fii.json"
        self.assertTrue(fixture_path.exists(), f"Fixture não encontrada: {fixture_path}")

        engine = StructuredFundsEngine.load_from_fixture(fixture_path)
        self.assertIn("FIDC-AGRO-INDUSTRIA-01", engine.fidc_funds)
        self.assertIn("FIDC-STRESSED-WRITE-OFF-02", engine.fidc_funds)
        self.assertIn("FII-PRIME-OFFICES-01", engine.fii_funds)
        self.assertIn("FII-CREDITO-CRI-02", engine.fii_funds)

        # Validação formal das invariantes iniciais de todos os fundos
        report = engine.verify_all_invariants()
        for fund_key, invs in report.items():
            self.assertTrue(invs["all_invariants_hold"], f"Falha de invariante no fundo {fund_key}: {invs}")

    def test_fixture_fidc_write_off_scenario(self) -> None:
        """Executa cenário de estresse no FIDC-STRESSED-WRITE-OFF-02 da fixture."""
        fixture_path = ROOT_DIR / "fixtures" / "structured_funds_fidc_fii.json"
        engine = StructuredFundsEngine.load_from_fixture(fixture_path)
        stressed_fund = engine.get_fidc("FIDC-STRESSED-WRITE-OFF-02")

        # REC-DEF-2026-101 tem saldo de 1.200.000
        # Júnior tem 1.000.000 -> absorve 1.000.000 e zera
        # Mezanino tem 1.500.000 -> absorve 200.000 e fica com 1.300.000
        # Sênior tem 5.000.000 -> absorve 0 e fica com 5.000.000
        res = stressed_fund.absorb_credit_default("REC-DEF-2026-101")
        self.assertEqual(res.total_loss_amount, Decimal("1200000.00"))
        self.assertEqual(res.absorbed_by_junior, Decimal("1000000.00"))
        self.assertEqual(res.absorbed_by_mezzanine, Decimal("200000.00"))
        self.assertEqual(res.absorbed_by_senior, Decimal("0.00"))
        self.assertTrue(res.is_water_tight_satisfied)

        self.assertEqual(stressed_fund.junior_quota.total_equity, Decimal("0.00"))
        self.assertEqual(stressed_fund.mezzanine_quota.total_equity, Decimal("1300000.00"))
        self.assertEqual(stressed_fund.senior_quota.total_equity, Decimal("5000000.00"))

        inv = stressed_fund.verify_invariants()
        self.assertTrue(inv["all_invariants_hold"])

    def test_fixture_fii_distribution_scenario(self) -> None:
        """Executa distribuição de 95% no FII-PRIME-OFFICES-01 da fixture."""
        fixture_path = ROOT_DIR / "fixtures" / "structured_funds_fidc_fii.json"
        engine = StructuredFundsEngine.load_from_fixture(fixture_path)
        fii = engine.get_fii("FII-PRIME-OFFICES-01")

        ev = fii.declare_and_settle_distribution(
            statement_id="STMT-2026-S1",
            record_date=date(2026, 6, 30),
            payment_date=date(2026, 7, 15),
        )
        self.assertEqual(ev.declared_total_distribution, Decimal("3895000.00"))
        self.assertTrue(ev.is_cvm175_and_lei8668_compliant)

        inv = fii.verify_invariants()
        self.assertTrue(inv["all_invariants_hold"])

    def test_engine_to_dict_and_from_dict_roundtrip(self) -> None:
        """Verifica serialização determinística e reconstrução sem perdas."""
        fixture_path = ROOT_DIR / "fixtures" / "structured_funds_fidc_fii.json"
        engine1 = StructuredFundsEngine.load_from_fixture(fixture_path)

        data = engine1.to_dict()
        engine2 = StructuredFundsEngine.from_dict(data)

        self.assertEqual(len(engine1.fidc_funds), len(engine2.fidc_funds))
        self.assertEqual(len(engine1.fii_funds), len(engine2.fii_funds))
        self.assertEqual(engine1.to_dict(), engine2.to_dict())


if __name__ == "__main__":
    unittest.main()
