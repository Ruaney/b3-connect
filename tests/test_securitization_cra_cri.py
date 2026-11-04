"""Bateria de Testes Unitários — Securitização de CRA e CRI no Balcão NoMe B3 (FL-062).

Testa exaustivamente com 100% de cobertura:
1. Modelagem e precisão numérica (Decimal, arredondamentos e quantização B3).
2. Agregação e elegibilidade de lastros (CPRs para CRA sob Lei 11.076/04; CCIs para CRI sob Lei 9.514/97).
3. Gestão dinâmica do Fundo de Reserva de Liquidez (depósitos, saques de liquidez, remuneração e desmobilização final).
4. Cascata de pagamentos (Payment Waterfall) em múltiplos regimes:
   - Adimplência perfeita com amortização e pagamento integral de Sênior e Subordinada.
   - Estresse de liquidez com saque do Fundo de Reserva e bloqueio de pagamentos à Subordinada.
   - Recomposição mandatória do Fundo de Reserva a partir do Excess Spread.
   - Estrutura multi-tranche com classe Mezanino (Sênior > Mezanino > Fundo > Subordinada).
   - Liquidação final com desmobilização do Fundo de Reserva residual.
5. Absorção de perdas definitivas (First-Loss Piece):
   - Inadimplência e write-off consumindo primeiro a Subordinada, depois Mezanino e por último a Sênior.
6. Conservação formal das 5 Invariantes Matemáticas de Securitização.
7. Carga e validação de fixtures sintéticas oficiais (`fixtures/cra_cri_securitization.json`).
"""

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Tuple
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.securitization_cra_cri import (
    AmortizationType,
    CertificateTranche,
    CertificateTrancheClass,
    CollateralAsset,
    CollateralPaymentReceipt,
    CollateralStatus,
    CollateralType,
    CraCriSecuritizationEngine,
    FiduciaryRegime,
    FiduciaryRegimeStatus,
    IndexerType,
    LegalFramework,
    LossAbsorptionResult,
    ReserveFund,
    SecuritizationError,
    SecuritizationFiduciaryBreachError,
    SecuritizationInvariantError,
    SecuritizationIssue,
    SecuritizationType,
    SecuritizationValidationError,
    SecuritizationWaterfallError,
    WaterfallExecutionResult,
    load_securitization_from_dict,
    round_cents,
    round_rate,
    round_unit,
    to_date,
    to_decimal,
)


class TestSecuritizationUtilsAndEnums(unittest.TestCase):
    """Testes de funções utilitárias, conversões e Enums de domínio."""

    def test_round_helpers(self) -> None:
        self.assertEqual(round_cents(Decimal("100.456")), Decimal("100.46"))
        self.assertEqual(round_cents(Decimal("100.454")), Decimal("100.45"))
        self.assertEqual(round_unit(Decimal("1000.123456789")), Decimal("1000.12345679"))
        self.assertEqual(round_rate(Decimal("0.051234567")), Decimal("0.05123457"))

    def test_to_decimal_valid_and_invalid(self) -> None:
        self.assertEqual(to_decimal(Decimal("12.34")), Decimal("12.34"))
        self.assertEqual(to_decimal(100), Decimal("100"))
        self.assertEqual(to_decimal("150,50"), Decimal("150.50"))
        self.assertEqual(to_decimal(" 200.75 "), Decimal("200.75"))

        with self.assertRaises(SecuritizationValidationError):
            to_decimal(None, "campo_nulo")

        with self.assertRaises(SecuritizationValidationError):
            to_decimal("invalido_xyz", "campo_invalido")

    def test_to_date_valid_and_invalid(self) -> None:
        d = date(2026, 11, 4)
        dt = datetime(2026, 11, 4, 15, 30, 0)
        self.assertEqual(to_date(d), d)
        self.assertEqual(to_date(dt), d)
        self.assertEqual(to_date("2026-11-04"), d)

        with self.assertRaises(SecuritizationValidationError):
            to_date(None)

        with self.assertRaises(SecuritizationValidationError):
            to_date("04/11/2026")

        with self.assertRaises(SecuritizationValidationError):
            to_date(12345)

    def test_securitization_type_from_str(self) -> None:
        self.assertEqual(SecuritizationType.from_str("CRA"), SecuritizationType.CRA)
        self.assertEqual(SecuritizationType.from_str("agro"), SecuritizationType.CRA)
        self.assertEqual(SecuritizationType.from_str("AGRONEGOCIO"), SecuritizationType.CRA)
        self.assertEqual(SecuritizationType.from_str("CRI"), SecuritizationType.CRI)
        self.assertEqual(SecuritizationType.from_str("imob"), SecuritizationType.CRI)
        self.assertEqual(SecuritizationType.from_str("IMOBILIARIO"), SecuritizationType.CRI)
        self.assertEqual(SecuritizationType.from_str(SecuritizationType.CRA), SecuritizationType.CRA)

        with self.assertRaises(SecuritizationValidationError):
            SecuritizationType.from_str("DESCONHECIDO")

    def test_collateral_type_properties(self) -> None:
        for ct in (
            CollateralType.CPR_FINANCEIRA,
            CollateralType.CPR_FISICA,
            CollateralType.CDCA,
            CollateralType.DUPLICATA_RURAL,
            CollateralType.CONTRATO_AGRO,
        ):
            self.assertTrue(ct.is_agro)
            self.assertFalse(ct.is_real_estate)

        for ct in (
            CollateralType.CCI,
            CollateralType.FINANCIAMENTO_HABITACIONAL,
            CollateralType.LOCACAO_ATIPICA_BTS,
            CollateralType.CONTRATO_COMPRA_VENDA,
            CollateralType.DEBENTURE_IMOBILIARIA,
        ):
            self.assertTrue(ct.is_real_estate)
            self.assertFalse(ct.is_agro)


class TestCollateralAsset(unittest.TestCase):
    """Testes da classe de ativos de lastro (CollateralAsset)."""

    def setUp(self) -> None:
        self.asset = CollateralAsset(
            asset_id="CPR-TEST-01",
            collateral_type=CollateralType.CPR_FINANCEIRA,
            debtor_name="DEVEDOR AGRO S.A.",
            debtor_doc="12345678000199",
            original_face_value=Decimal("1000000.00"),
            outstanding_balance=Decimal("1000000.00"),
            issue_date=date(2026, 1, 1),
            maturity_date=date(2028, 1, 1),
            indexer=IndexerType.CDI,
            interest_rate_pct=Decimal("0.02"),
        )

    def test_asset_validation(self) -> None:
        with self.assertRaises(SecuritizationValidationError):
            CollateralAsset(
                asset_id="ERR-01",
                collateral_type=CollateralType.CPR_FINANCEIRA,
                debtor_name="X",
                debtor_doc="0",
                original_face_value=Decimal("100.00"),
                outstanding_balance=Decimal("100.00"),
                issue_date=date(2026, 1, 1),
                maturity_date=date(2025, 1, 1),  # Anterior
                indexer=IndexerType.CDI,
                interest_rate_pct=Decimal("0.01"),
            )

        with self.assertRaises(SecuritizationValidationError):
            CollateralAsset(
                asset_id="ERR-02",
                collateral_type=CollateralType.CPR_FINANCEIRA,
                debtor_name="X",
                debtor_doc="0",
                original_face_value=Decimal("-10.00"),
                outstanding_balance=Decimal("0.00"),
                issue_date=date(2026, 1, 1),
                maturity_date=date(2027, 1, 1),
                indexer=IndexerType.CDI,
                interest_rate_pct=Decimal("0.01"),
            )

        with self.assertRaises(SecuritizationValidationError):
            CollateralAsset(
                asset_id="ERR-03",
                collateral_type=CollateralType.CPR_FINANCEIRA,
                debtor_name="X",
                debtor_doc="0",
                original_face_value=Decimal("100.00"),
                outstanding_balance=Decimal("-50.00"),
                issue_date=date(2026, 1, 1),
                maturity_date=date(2027, 1, 1),
                indexer=IndexerType.CDI,
                interest_rate_pct=Decimal("0.01"),
            )

    def test_receive_payment_partial_and_full(self) -> None:
        # Apropria juros prévios
        self.asset.accrue_interest(Decimal("0.01"))
        self.assertEqual(self.asset.accrued_interest, Decimal("10000.00"))

        # Pagamento parcial
        receipt = self.asset.receive_payment(
            principal=Decimal("400000.00"),
            interest=Decimal("10000.00"),
            penalty=Decimal("500.00"),
            payment_date=date(2026, 6, 1),
        )
        self.assertEqual(receipt.principal_paid, Decimal("400000.00"))
        self.assertEqual(receipt.interest_paid, Decimal("10000.00"))
        self.assertEqual(receipt.penalty_paid, Decimal("500.00"))
        self.assertEqual(receipt.total_received, Decimal("410500.00"))
        self.assertEqual(self.asset.outstanding_balance, Decimal("600000.00"))
        self.assertEqual(self.asset.accrued_interest, Decimal("0.00"))
        self.assertEqual(self.asset.status, CollateralStatus.PERFORMING)

        # Quitação integral
        receipt2 = self.asset.receive_payment(
            principal=Decimal("600000.00"),
            interest=Decimal("0.00"),
        )
        self.assertEqual(self.asset.outstanding_balance, Decimal("0.00"))
        self.assertEqual(self.asset.status, CollateralStatus.SETTLED)

    def test_receive_payment_errors(self) -> None:
        with self.assertRaises(SecuritizationValidationError):
            self.asset.receive_payment(principal=Decimal("-10.00"), interest=Decimal("0.00"))

        with self.assertRaises(SecuritizationValidationError):
            self.asset.receive_payment(principal=Decimal("2000000.00"), interest=Decimal("0.00"))

    def test_delinquency_and_write_off(self) -> None:
        self.asset.mark_delinquent(35)
        self.assertEqual(self.asset.delinquency_days, 35)
        self.assertEqual(self.asset.status, CollateralStatus.DELINQUENT)

        with self.assertRaises(SecuritizationValidationError):
            self.asset.mark_delinquent(-5)

        self.asset.mark_delinquent(0)
        self.assertEqual(self.asset.status, CollateralStatus.PERFORMING)

        # Write-off
        self.asset.accrue_interest(Decimal("0.02"))
        loss = self.asset.write_off()
        self.assertEqual(loss, Decimal("1020000.00"))
        self.assertEqual(self.asset.status, CollateralStatus.DEFAULTED)
        self.assertEqual(self.asset.outstanding_balance, Decimal("0.00"))

        # Write off repetido ou em liquidado
        settled_asset = CollateralAsset(
            asset_id="CPR-SETTLED",
            collateral_type=CollateralType.CPR_FINANCEIRA,
            debtor_name="Y",
            debtor_doc="0",
            original_face_value=Decimal("100.00"),
            outstanding_balance=Decimal("0.00"),
            issue_date=date(2026, 1, 1),
            maturity_date=date(2027, 1, 1),
            indexer=IndexerType.CDI,
            interest_rate_pct=Decimal("0.01"),
            status=CollateralStatus.SETTLED,
        )
        with self.assertRaises(SecuritizationValidationError):
            settled_asset.write_off()

    def test_accrue_interest_error(self) -> None:
        with self.assertRaises(SecuritizationValidationError):
            self.asset.accrue_interest(Decimal("-0.01"))

    def test_asset_to_dict(self) -> None:
        d = self.asset.to_dict()
        self.assertEqual(d["asset_id"], "CPR-TEST-01")
        self.assertEqual(d["collateral_type"], "CPR_FINANCEIRA")
        self.assertEqual(d["outstanding_balance"], "1000000.00")


class TestReserveFund(unittest.TestCase):
    """Testes do Fundo de Reserva de Liquidez."""

    def setUp(self) -> None:
        self.fund = ReserveFund(
            fund_id="RF-01",
            target_balance=Decimal("500000.00"),
            current_balance=Decimal("500000.00"),
            minimum_floor=Decimal("200000.00"),
            yield_rate_annual_pct=Decimal("0.12"),
        )

    def test_validation(self) -> None:
        with self.assertRaises(SecuritizationValidationError):
            ReserveFund(fund_id="X", target_balance=Decimal("-10.00"), current_balance=Decimal("0.00"))

    def test_draw_and_deficiency(self) -> None:
        self.assertFalse(self.fund.is_deficient)
        self.assertEqual(self.fund.deficit_amount, Decimal("0.00"))

        # Saque parcial
        drawn = self.fund.draw(Decimal("200000.00"))
        self.assertEqual(drawn, Decimal("200000.00"))
        self.assertEqual(self.fund.current_balance, Decimal("300000.00"))
        self.assertTrue(self.fund.is_deficient)
        self.assertEqual(self.fund.deficit_amount, Decimal("200000.00"))

        # Saque excedente (deve sacar até o limite do saldo)
        drawn2 = self.fund.draw(Decimal("500000.00"))
        self.assertEqual(drawn2, Decimal("300000.00"))
        self.assertEqual(self.fund.current_balance, Decimal("0.00"))
        self.assertEqual(self.fund.deficit_amount, Decimal("500000.00"))

        with self.assertRaises(SecuritizationValidationError):
            self.fund.draw(Decimal("-1.00"))

    def test_deposit(self) -> None:
        self.fund.draw(Decimal("500000.00"))
        self.fund.deposit(Decimal("250000.00"))
        self.assertEqual(self.fund.current_balance, Decimal("250000.00"))
        self.assertEqual(self.fund.deficit_amount, Decimal("250000.00"))

        with self.assertRaises(SecuritizationValidationError):
            self.fund.deposit(Decimal("-100.00"))

    def test_apply_yield(self) -> None:
        earnings = self.fund.apply_yield(Decimal("0.01"))
        self.assertEqual(earnings, Decimal("5000.00"))
        self.assertEqual(self.fund.current_balance, Decimal("505000.00"))

        with self.assertRaises(SecuritizationValidationError):
            self.fund.apply_yield(Decimal("-0.01"))

    def test_to_dict(self) -> None:
        d = self.fund.to_dict()
        self.assertEqual(d["fund_id"], "RF-01")
        self.assertEqual(d["target_balance"], "500000.00")


class TestCertificateTranche(unittest.TestCase):
    """Testes de Tranches de Certificados (Sênior, Mezanino, Subordinada)."""

    def setUp(self) -> None:
        self.tranche = CertificateTranche(
            tranche_id="TR-SR-01",
            isin="BRCRASEN0012",
            b3_code="CRA126B3S",
            tranche_class=CertificateTrancheClass.SENIOR,
            amortization_type=AmortizationType.SAC,
            indexer=IndexerType.CDI,
            spread_rate_annual_pct=Decimal("0.0150"),
            original_quantity=10000,
            current_quantity=10000,
            original_unit_price=Decimal("1000.00"),
            current_unit_price=Decimal("1000.00"),
            amortization_schedule={"2026-12-15": Decimal("1000000.00")},
        )

    def test_validation(self) -> None:
        with self.assertRaises(SecuritizationValidationError):
            CertificateTranche(
                tranche_id="X",
                isin="0",
                b3_code="0",
                tranche_class=CertificateTrancheClass.SENIOR,
                amortization_type=AmortizationType.BULLET,
                indexer=IndexerType.CDI,
                spread_rate_annual_pct=Decimal("0.01"),
                original_quantity=0,
                current_quantity=0,
                original_unit_price=Decimal("100.00"),
                current_unit_price=Decimal("100.00"),
            )

        with self.assertRaises(SecuritizationValidationError):
            CertificateTranche(
                tranche_id="X",
                isin="0",
                b3_code="0",
                tranche_class=CertificateTrancheClass.SENIOR,
                amortization_type=AmortizationType.BULLET,
                indexer=IndexerType.CDI,
                spread_rate_annual_pct=Decimal("0.01"),
                original_quantity=10,
                current_quantity=10,
                original_unit_price=Decimal("100.00"),
                current_unit_price=Decimal("-10.00"),
            )

    def test_interest_and_payment(self) -> None:
        self.assertEqual(self.tranche.total_outstanding_balance, Decimal("10000000.00"))
        interest = self.tranche.accrue_interest_period(Decimal("0.01"))
        self.assertEqual(interest, Decimal("100000.00"))
        self.assertEqual(self.tranche.accrued_interest, Decimal("100000.00"))

        # Pagamento parcial de juros
        paid, deficit = self.tranche.pay_interest(Decimal("60000.00"))
        self.assertEqual(paid, Decimal("60000.00"))
        self.assertEqual(deficit, Decimal("40000.00"))
        self.assertEqual(self.tranche.accrued_interest, Decimal("0.00"))
        self.assertEqual(self.tranche.unpaid_interest_deficit, Decimal("40000.00"))

        # Pagamento do restante do déficit
        paid2, deficit2 = self.tranche.pay_interest(Decimal("50000.00"))
        self.assertEqual(paid2, Decimal("40000.00"))
        self.assertEqual(deficit2, Decimal("0.00"))
        self.assertEqual(self.tranche.unpaid_interest_deficit, Decimal("0.00"))

    def test_amortization(self) -> None:
        amortized = self.tranche.amortize(Decimal("2000000.00"))
        self.assertEqual(amortized, Decimal("2000000.00"))
        self.assertEqual(self.tranche.total_outstanding_balance, Decimal("8000000.00"))
        self.assertEqual(self.tranche.current_unit_price, Decimal("800.00000000"))

        # Amortização total
        amort2 = self.tranche.amortize(Decimal("10000000.00"))
        self.assertEqual(amort2, Decimal("8000000.00"))
        self.assertEqual(self.tranche.total_outstanding_balance, Decimal("0.00"))
        self.assertEqual(self.tranche.current_unit_price, Decimal("0.00000000"))

        # Amortizar em tranche zerada
        amort3 = self.tranche.amortize(Decimal("500.00"))
        self.assertEqual(amort3, Decimal("0.00"))

        with self.assertRaises(SecuritizationValidationError):
            self.tranche.amortize(Decimal("-10.00"))

    def test_loss_absorption(self) -> None:
        absorbed, unabsorbed = self.tranche.absorb_loss(Decimal("3000000.00"))
        self.assertEqual(absorbed, Decimal("3000000.00"))
        self.assertEqual(unabsorbed, Decimal("0.00"))
        self.assertEqual(self.tranche.total_outstanding_balance, Decimal("7000000.00"))
        self.assertEqual(self.tranche.current_unit_price, Decimal("700.00000000"))

        # Absorção que supera o saldo restante
        absorbed2, unabsorbed2 = self.tranche.absorb_loss(Decimal("8000000.00"))
        self.assertEqual(absorbed2, Decimal("7000000.00"))
        self.assertEqual(unabsorbed2, Decimal("1000000.00"))
        self.assertEqual(self.tranche.total_outstanding_balance, Decimal("0.00"))

        # Perda em tranche zerada
        absorbed3, unabsorbed3 = self.tranche.absorb_loss(Decimal("500.00"))
        self.assertEqual(absorbed3, Decimal("0.00"))
        self.assertEqual(unabsorbed3, Decimal("500.00"))

        with self.assertRaises(SecuritizationValidationError):
            self.tranche.absorb_loss(Decimal("-10.00"))

    def test_to_dict(self) -> None:
        d = self.tranche.to_dict()
        self.assertEqual(d["tranche_id"], "TR-SR-01")
        self.assertEqual(d["tranche_class"], "SENIOR")


class TestSecuritizationIssueAndWaterfall(unittest.TestCase):
    """Testes completos da emissão de securitização, agregação de lastros e cascata."""

    def setUp(self) -> None:
        self.engine = CraCriSecuritizationEngine.load_from_fixture(
            ROOT_DIR / "fixtures" / "cra_cri_securitization.json"
        )

    def test_load_all_fixture_issues(self) -> None:
        self.assertEqual(len(self.engine.issues), 5)
        self.assertIn("CRA-AGRO-2026-001", self.engine.issues)
        self.assertIn("CRI-IMOB-2026-002", self.engine.issues)
        self.assertIn("CRI-BTS-MEZZANINE-2026-003", self.engine.issues)
        self.assertIn("CRA-DEFAULT-WRITE-OFF-2026-004", self.engine.issues)
        self.assertIn("CRI-MATURITY-RESERVE-UNWIND-2026-005", self.engine.issues)

    def test_cra_agro_perfect_execution(self) -> None:
        """Cenário 1: Fluxo perfeito de CRA com adimplência e pagamento de Sênior e Subordinada."""
        issue = self.engine.get_issue("CRA-AGRO-2026-001")
        self.assertEqual(issue.securitization_type, SecuritizationType.CRA)
        self.assertEqual(issue.senior_balance(), Decimal("80000000.00"))
        self.assertEqual(issue.subordinated_balance(), Decimal("20000000.00"))
        self.assertEqual(issue.total_collateral_balance(), Decimal("105000000.00"))

        # Recebe pagamentos dos produtores rurais (R$ 7.000.000,00 de principal + R$ 1.500.000,00 juros)
        rcp1 = issue.register_collateral_payment(
            asset_id="CPR-SOJA-MT-001",
            principal=Decimal("4000000.00"),
            interest=Decimal("800000.00"),
        )
        rcp2 = issue.register_collateral_payment(
            asset_id="CPR-MILHO-GO-002",
            principal=Decimal("3000000.00"),
            interest=Decimal("700000.00"),
        )
        self.assertEqual(issue.pending_inflow, Decimal("8500000.00"))

        # Executa waterfall para 2026-12-15
        result = issue.execute_waterfall(
            period_date=date(2026, 12, 15),
            period_indexer_rate=Decimal("0.0080"),  # CDI mensal 0.80%
            allow_subordinated_residual_payout=True,
        )

        self.assertTrue(result.conservation_verified)
        self.assertTrue(result.is_senior_fully_satisfied)
        self.assertTrue(result.is_reserve_at_target)
        self.assertEqual(result.reserve_fund_draw, Decimal("0.00"))
        self.assertEqual(result.senior_principal_paid, Decimal("5000000.00"))
        self.assertEqual(result.subordinated_principal_paid, Decimal("1000000.00"))
        self.assertGreater(result.senior_interest_paid, Decimal("0.00"))
        self.assertGreater(result.subordinated_interest_paid, Decimal("0.00"))

        # Valida invariantes da emissão
        inv = issue.verify_invariants()
        self.assertTrue(inv["all_invariants_hold"])

    def test_cri_liquidity_stress_and_reserve_draw(self) -> None:
        """Cenário 2: Atraso de recebíveis em CRI com acionamento do Fundo de Reserva."""
        issue = self.engine.get_issue("CRI-IMOB-2026-002")
        self.assertEqual(issue.securitization_type, SecuritizationType.CRI)

        # Apenas pagamentos parciais (CCI comercial em atraso não paga nada)
        issue.register_collateral_payment(
            asset_id="CCI-RES-ALPHAVILLE-01",
            principal=Decimal("1000000.00"),
            interest=Decimal("100000.00"),
        )
        # Total arrecadado: R$ 1.100.000,00 (insuficiente para cobrir R$ 2.500.000 de amortização sênior)

        result = issue.execute_waterfall(
            period_date=date(2026, 12, 20),
            period_indexer_rate=Decimal("0.0040"),  # IPCA mensal 0.40%
        )

        self.assertTrue(result.conservation_verified)
        self.assertGreater(result.reserve_fund_draw, Decimal("0.00"))
        self.assertTrue(issue.fiduciary_regime.reserve_fund.is_deficient)

        # Regra de Ouro: Como a reserva foi desfalecada, a Subordinada NÃO PODE receber juros nem principal!
        self.assertEqual(result.subordinated_interest_paid, Decimal("0.00"))
        self.assertEqual(result.subordinated_principal_paid, Decimal("0.00"))
        self.assertEqual(result.residual_distribution, Decimal("0.00"))

    def test_reserve_replenishment_from_excess_spread(self) -> None:
        """Cenário 3: Recomposição mandatória do Fundo de Reserva a partir do Excess Spread."""
        issue = self.engine.get_issue("CRI-IMOB-2026-002")
        # Provoca defasagem inicial na reserva
        issue.fiduciary_regime.reserve_fund.draw(Decimal("1000000.00"))
        self.assertTrue(issue.fiduciary_regime.reserve_fund.is_deficient)

        # Entrada massiva de caixa gerando excess spread (devedor em atraso regularizou com juros e multas)
        issue.register_collateral_payment(
            asset_id="CCI-COM-FARIALIMA-03",
            principal=Decimal("4000000.00"),
            interest=Decimal("1000000.00"),
            penalty=Decimal("100000.00"),
        )

        result = issue.execute_waterfall(
            period_date=date(2026, 12, 20),
            period_indexer_rate=Decimal("0.0040"),
        )

        self.assertTrue(result.conservation_verified)
        self.assertGreater(result.reserve_fund_deposit, Decimal("0.00"))
        self.assertTrue(result.is_reserve_at_target)
        self.assertFalse(issue.fiduciary_regime.reserve_fund.is_deficient)

    def test_mezzanine_waterfall_priority(self) -> None:
        """Cenário 4: Hierarquia estrita em 3 tranches (Sênior > Mezanino > Fundo > Subordinada)."""
        issue = self.engine.get_issue("CRI-BTS-MEZZANINE-2026-003")
        self.assertEqual(issue.senior_balance(), Decimal("60000000.00"))
        self.assertEqual(issue.mezzanine_balance(), Decimal("20000000.00"))
        self.assertEqual(issue.subordinated_balance(), Decimal("20000000.00"))

        # Arrecada caixa suficiente para Sênior e Mezanino, mas com sobra para Subordinada
        issue.register_collateral_payment(
            asset_id="BTS-GALPAO-LOGISTICO-01",
            principal=Decimal("4000000.00"),
            interest=Decimal("1500000.00"),
        )
        issue.register_collateral_payment(
            asset_id="CCI-COM-MODERNA-02",
            principal=Decimal("1000000.00"),
            interest=Decimal("500000.00"),
        )

        result = issue.execute_waterfall(
            period_date=date(2026, 12, 30),
            period_indexer_rate=Decimal("0.0050"),
        )

        self.assertTrue(result.conservation_verified)
        self.assertEqual(result.senior_principal_paid, Decimal("3000000.00"))
        self.assertEqual(result.mezzanine_principal_paid, Decimal("1000000.00"))
        self.assertGreater(result.mezzanine_interest_paid, Decimal("0.00"))
        self.assertEqual(result.senior_interest_deficit, Decimal("0.00"))

    def test_default_write_off_and_first_loss_absorption(self) -> None:
        """Cenário 5: Inadimplência definitiva de lastro consumindo primeiro a tranche Subordinada."""
        issue = self.engine.get_issue("CRA-DEFAULT-WRITE-OFF-2026-004")
        sr_bal_initial = issue.senior_balance()
        sub_bal_initial = issue.subordinated_balance()
        self.assertEqual(sr_bal_initial, Decimal("40000000.00"))
        self.assertEqual(sub_bal_initial, Decimal("10000000.00"))

        # Processa default da CPR de R$ 6.000.000,00
        loss_result = issue.process_collateral_default("CPR-CITROS-SP-003")

        self.assertEqual(loss_result.total_loss, Decimal("6000000.00"))
        self.assertEqual(loss_result.loss_absorbed_by_subordinated, Decimal("6000000.00"))
        self.assertEqual(loss_result.loss_absorbed_by_mezzanine, Decimal("0.00"))
        self.assertEqual(loss_result.loss_absorbed_by_senior, Decimal("0.00"))
        self.assertEqual(loss_result.unabsorbed_loss, Decimal("0.00"))

        # Subordinada absorveu 100% da perda
        self.assertEqual(issue.subordinated_balance(), Decimal("4000000.00"))
        # Sênior permaneceu 100% protegida e intacta!
        self.assertEqual(issue.senior_balance(), sr_bal_initial)

    def test_default_cascading_to_mezzanine_and_senior(self) -> None:
        """Perda catastrófica superando a subordinada, atingindo mezanino e sênior."""
        issue = self.engine.get_issue("CRI-BTS-MEZZANINE-2026-003")
        # Lastro de R$ 70M sofre perda total
        loss_result = issue.process_collateral_default("BTS-GALPAO-LOGISTICO-01")

        self.assertEqual(loss_result.total_loss, Decimal("70000000.00"))
        # Subordinada absorve seus R$ 20M
        self.assertEqual(loss_result.loss_absorbed_by_subordinated, Decimal("20000000.00"))
        # Mezanino absorve seus R$ 20M
        self.assertEqual(loss_result.loss_absorbed_by_mezzanine, Decimal("20000000.00"))
        # Sênior absorve os R$ 30M excedentes
        self.assertEqual(loss_result.loss_absorbed_by_senior, Decimal("30000000.00"))
        self.assertEqual(loss_result.unabsorbed_loss, Decimal("0.00"))

        self.assertEqual(issue.subordinated_balance(), Decimal("0.00"))
        self.assertEqual(issue.mezzanine_balance(), Decimal("0.00"))
        self.assertEqual(issue.senior_balance(), Decimal("30000000.00"))

    def test_unwind_reserve_at_maturity(self) -> None:
        """Cenário 6: Desmobilização do Fundo de Reserva no vencimento final após quitação da Sênior."""
        issue = self.engine.get_issue("CRI-MATURITY-RESERVE-UNWIND-2026-005")

        # Tentativa prematura de desmobilização deve falhar
        with self.assertRaises(SecuritizationWaterfallError):
            issue.unwind_reserve_at_maturity()

        # Quita a Sênior integralmente
        sr_tranche = issue.tranches["CRI-MAT-26-SR"]
        sr_tranche.amortize(sr_tranche.total_outstanding_balance)
        self.assertEqual(issue.senior_balance(), Decimal("0.00"))

        # Agora a desmobilização é autorizada
        released = issue.unwind_reserve_at_maturity()
        self.assertEqual(released, Decimal("1000000.00"))
        self.assertEqual(issue.fiduciary_regime.reserve_fund.current_balance, Decimal("0.00"))
        self.assertEqual(issue.fiduciary_regime.segregated_cash_balance, Decimal("1000000.00"))

    def test_ineligibility_collateral_errors(self) -> None:
        """CRA rejeita lastro imobiliário e CRI rejeita lastro agro."""
        cra_issue = self.engine.get_issue("CRA-AGRO-2026-001")
        cri_asset = CollateralAsset(
            asset_id="CCI-ERR-01",
            collateral_type=CollateralType.CCI,
            debtor_name="X",
            debtor_doc="0",
            original_face_value=Decimal("1000.00"),
            outstanding_balance=Decimal("1000.00"),
            issue_date=date(2026, 1, 1),
            maturity_date=date(2027, 1, 1),
            indexer=IndexerType.IPCA,
            interest_rate_pct=Decimal("0.05"),
        )
        with self.assertRaises(SecuritizationValidationError):
            cra_issue.add_collateral(cri_asset)

        cri_issue = self.engine.get_issue("CRI-IMOB-2026-002")
        cpr_asset = CollateralAsset(
            asset_id="CPR-ERR-01",
            collateral_type=CollateralType.CPR_FINANCEIRA,
            debtor_name="Y",
            debtor_doc="0",
            original_face_value=Decimal("1000.00"),
            outstanding_balance=Decimal("1000.00"),
            issue_date=date(2026, 1, 1),
            maturity_date=date(2027, 1, 1),
            indexer=IndexerType.CDI,
            interest_rate_pct=Decimal("0.02"),
        )
        with self.assertRaises(SecuritizationValidationError):
            cri_issue.add_collateral(cpr_asset)

    def test_engine_operations_and_invariants(self) -> None:
        """Testa métodos de orquestração do CraCriSecuritizationEngine."""
        all_inv = self.engine.verify_all_invariants()
        self.assertEqual(len(all_inv), 5)
        for issue_id, report in all_inv.items():
            self.assertTrue(report["all_invariants_hold"], f"Invariantes falharam em {issue_id}")

        # Testa get_issue inexistente
        with self.assertRaises(SecuritizationValidationError):
            self.engine.get_issue("EMISSAO_INEXISTENTE")

        # Serialização para dict
        edata = self.engine.to_dict()
        self.assertEqual(edata["flow_code"], "FL-062")
        self.assertEqual(len(edata["issues"]), 5)

        # Carga de fixture inexistente
        with self.assertRaises(SecuritizationValidationError):
            CraCriSecuritizationEngine.load_from_fixture(ROOT_DIR / "fixtures" / "nao_existe.json")

    def test_issue_ratios_and_metrics(self) -> None:
        issue = self.engine.get_issue("CRA-AGRO-2026-001")
        oc = issue.overcollateralization_ratio()
        self.assertGreater(oc, Decimal("1.00"))

        sub_ratio = issue.subordination_ratio()
        self.assertEqual(sub_ratio, Decimal("0.20000000"))  # 20M / 100M = 20%


class TestSecuritizationEdgeCasesAndFullCoverage(unittest.TestCase):
    """Testes focados em cobrir 100% de linhas, branches e exceções do motor."""

    def test_additional_validations_and_errors(self) -> None:
        # Erro taxa periódica negativa na tranche
        tr = CertificateTranche(
            tranche_id="TR-ERR",
            isin="0",
            b3_code="0",
            tranche_class=CertificateTrancheClass.SENIOR,
            amortization_type=AmortizationType.BULLET,
            indexer=IndexerType.CDI,
            spread_rate_annual_pct=Decimal("0.01"),
            original_quantity=10,
            current_quantity=10,
            original_unit_price=Decimal("100.00"),
            current_unit_price=Decimal("100.00"),
        )
        with self.assertRaises(SecuritizationValidationError):
            tr.accrue_interest_period(Decimal("-0.01"))

        # Juros pagos quando total_due == 0
        p, d = tr.pay_interest(Decimal("500.00"))
        self.assertEqual(p, Decimal("0.00"))
        self.assertEqual(d, Decimal("0.00"))

        # FiduciaryRegime com caixa negativo
        rf = ReserveFund("RF-X", Decimal("100.00"), Decimal("100.00"))
        with self.assertRaises(SecuritizationValidationError):
            FiduciaryRegime(
                regime_id="REG-ERR",
                securitizer_name="X",
                securitizer_cnpj="0",
                trustee_name="Y",
                trustee_cnpj="0",
                deed_registry_number="0",
                registry_date=date(2026, 1, 1),
                segregated_cash_balance=Decimal("-10.00"),
                reserve_fund=rf,
            )

        # SecuritizationIssue com vencimento anterior
        fid = FiduciaryRegime(
            regime_id="REG-OK",
            securitizer_name="X",
            securitizer_cnpj="0",
            trustee_name="Y",
            trustee_cnpj="0",
            deed_registry_number="0",
            registry_date=date(2026, 1, 1),
            segregated_cash_balance=Decimal("0.00"),
            reserve_fund=rf,
        )
        with self.assertRaises(SecuritizationValidationError):
            SecuritizationIssue(
                issue_id="ISSUE-ERR",
                securitization_type=SecuritizationType.CRA,
                legal_framework=LegalFramework.LEI_11076_2004,
                series_number=1,
                issue_date=date(2026, 1, 1),
                final_maturity_date=date(2025, 1, 1),
                fiduciary_regime=fid,
            )

        # Subordination ratio quando total tranches == 0
        empty_issue = SecuritizationIssue(
            issue_id="ISSUE-EMPTY",
            securitization_type=SecuritizationType.CRA,
            legal_framework=LegalFramework.LEI_11076_2004,
            series_number=1,
            issue_date=date(2026, 1, 1),
            final_maturity_date=date(2027, 1, 1),
            fiduciary_regime=fid,
        )
        self.assertEqual(empty_issue.subordination_ratio(), Decimal("0.00000000"))
        self.assertEqual(empty_issue.overcollateralization_ratio(), Decimal("1.00000000"))

        # Lastro inexistente em register_collateral_payment e process_collateral_default
        with self.assertRaises(SecuritizationValidationError):
            empty_issue.register_collateral_payment("NAO_EXISTE", Decimal("10.00"), Decimal("0.00"))

        with self.assertRaises(SecuritizationValidationError):
            empty_issue.process_collateral_default("NAO_EXISTE")

    def test_to_dict_on_result_dataclasses(self) -> None:
        lar = LossAbsorptionResult(
            asset_id="AST-1",
            total_loss=Decimal("100.00"),
            loss_absorbed_by_subordinated=Decimal("100.00"),
            loss_absorbed_by_mezzanine=Decimal("0.00"),
            loss_absorbed_by_senior=Decimal("0.00"),
            unabsorbed_loss=Decimal("0.00"),
        )
        d_lar = lar.to_dict()
        self.assertEqual(d_lar["asset_id"], "AST-1")
        self.assertEqual(d_lar["total_loss"], "100.00")

        wfr = WaterfallExecutionResult(
            execution_id="EXEC-1",
            issue_id="ISS-1",
            period_date=date(2026, 1, 1),
            opening_cash_balance=Decimal("0.00"),
            cash_inflow_collateral=Decimal("0.00"),
            cash_inflow_yield=Decimal("0.00"),
            total_cash_available=Decimal("0.00"),
            expenses_paid=Decimal("0.00"),
            reserve_fund_draw=Decimal("0.00"),
            senior_interest_paid=Decimal("0.00"),
            senior_principal_paid=Decimal("0.00"),
            senior_interest_deficit=Decimal("0.00"),
            senior_principal_deficit=Decimal("0.00"),
            mezzanine_interest_paid=Decimal("0.00"),
            mezzanine_principal_paid=Decimal("0.00"),
            reserve_fund_deposit=Decimal("0.00"),
            subordinated_interest_paid=Decimal("0.00"),
            subordinated_principal_paid=Decimal("0.00"),
            residual_distribution=Decimal("0.00"),
            closing_cash_balance=Decimal("0.00"),
            closing_reserve_balance=Decimal("0.00"),
            is_senior_fully_satisfied=True,
            is_reserve_at_target=True,
            conservation_verified=True,
        )
        d_wfr = wfr.to_dict()
        self.assertEqual(d_wfr["execution_id"], "EXEC-1")

    def test_senior_interest_reserve_fund_draw_and_expenses_override(self) -> None:
        """Testa acionamento do Fundo de Reserva especificamente para juros sênior."""
        rf = ReserveFund("RF-TEST", Decimal("50000.00"), Decimal("50000.00"))
        fid = FiduciaryRegime(
            regime_id="REG-DRAW",
            securitizer_name="SEC",
            securitizer_cnpj="0",
            trustee_name="TRU",
            trustee_cnpj="0",
            deed_registry_number="0",
            registry_date=date(2026, 1, 1),
            segregated_cash_balance=Decimal("0.00"),
            reserve_fund=rf,
        )
        issue = SecuritizationIssue(
            issue_id="ISS-DRAW",
            securitization_type=SecuritizationType.CRA,
            legal_framework=LegalFramework.LEI_11076_2004,
            series_number=1,
            issue_date=date(2026, 1, 1),
            final_maturity_date=date(2028, 1, 1),
            fiduciary_regime=fid,
        )
        tr = CertificateTranche(
            tranche_id="TR-SR-DRAW",
            isin="BR001",
            b3_code="CRA01",
            tranche_class=CertificateTrancheClass.SENIOR,
            amortization_type=AmortizationType.BULLET,
            indexer=IndexerType.PREFIXADO,
            spread_rate_annual_pct=Decimal("0.12"),  # 1% ao mês
            original_quantity=1000,
            current_quantity=1000,
            original_unit_price=Decimal("1000.00"),
            current_unit_price=Decimal("1000.00"),
        )
        issue.add_tranche(tr)

        # Executa com despesas override de R$ 100,00 e sem entradas de lastro
        res = issue.execute_waterfall(
            period_date=date(2026, 2, 1),
            expenses_override=Decimal("100.00"),
            period_indexer_rate=Decimal("0.00"),
        )

        self.assertTrue(res.conservation_verified)
        self.assertGreater(res.reserve_fund_draw, Decimal("0.00"))
        self.assertGreater(res.senior_interest_paid, Decimal("0.00"))
        self.assertEqual(res.senior_interest_deficit, Decimal("0.00"))

    def test_multi_tranche_break_coverage(self) -> None:
        """Testa breaks nos loops de absorção de perdas com múltiplas tranches de cada classe."""
        rf = ReserveFund("RF-MULTI", Decimal("10000.00"), Decimal("10000.00"))
        fid = FiduciaryRegime(
            regime_id="REG-MULTI",
            securitizer_name="SEC",
            securitizer_cnpj="0",
            trustee_name="TRU",
            trustee_cnpj="0",
            deed_registry_number="0",
            registry_date=date(2026, 1, 1),
            segregated_cash_balance=Decimal("0.00"),
            reserve_fund=rf,
        )
        issue = SecuritizationIssue(
            issue_id="ISS-MULTI",
            securitization_type=SecuritizationType.CRA,
            legal_framework=LegalFramework.LEI_11076_2004,
            series_number=1,
            issue_date=date(2026, 1, 1),
            final_maturity_date=date(2028, 1, 1),
            fiduciary_regime=fid,
        )

        # 2 Subordinadas
        sub1 = CertificateTranche(
            "SUB1", "0", "0", CertificateTrancheClass.SUBORDINATED,
            AmortizationType.BULLET, IndexerType.CDI, Decimal("0.01"),
            100, 100, Decimal("100.00"), Decimal("100.00")
        )
        sub2 = CertificateTranche(
            "SUB2", "0", "0", CertificateTrancheClass.SUBORDINATED,
            AmortizationType.BULLET, IndexerType.CDI, Decimal("0.01"),
            100, 100, Decimal("100.00"), Decimal("100.00")
        )
        # 2 Mezaninos
        mz1 = CertificateTranche(
            "MZ1", "0", "0", CertificateTrancheClass.MEZZANINE,
            AmortizationType.BULLET, IndexerType.CDI, Decimal("0.01"),
            100, 100, Decimal("100.00"), Decimal("100.00")
        )
        mz2 = CertificateTranche(
            "MZ2", "0", "0", CertificateTrancheClass.MEZZANINE,
            AmortizationType.BULLET, IndexerType.CDI, Decimal("0.01"),
            100, 100, Decimal("100.00"), Decimal("100.00")
        )
        # 2 Seniores
        sr1 = CertificateTranche(
            "SR1", "0", "0", CertificateTrancheClass.SENIOR,
            AmortizationType.BULLET, IndexerType.CDI, Decimal("0.01"),
            100, 100, Decimal("100.00"), Decimal("100.00")
        )
        sr2 = CertificateTranche(
            "SR2", "0", "0", CertificateTrancheClass.SENIOR,
            AmortizationType.BULLET, IndexerType.CDI, Decimal("0.01"),
            100, 100, Decimal("100.00"), Decimal("100.00")
        )

        issue.add_tranche(sub1)
        issue.add_tranche(sub2)
        issue.add_tranche(mz1)
        issue.add_tranche(mz2)
        issue.add_tranche(sr1)
        issue.add_tranche(sr2)

        # Lastro pequeno que é absorvido só pela SUB1 (testando break em sub2)
        col1 = CollateralAsset(
            "COL-PEQ", CollateralType.CPR_FINANCEIRA, "D", "0",
            Decimal("5000.00"), Decimal("5000.00"),
            date(2026, 1, 1), date(2027, 1, 1), IndexerType.CDI, Decimal("0.01")
        )
        issue.add_collateral(col1)
        res1 = issue.process_collateral_default("COL-PEQ")
        self.assertEqual(res1.loss_absorbed_by_subordinated, Decimal("5000.00"))
        self.assertEqual(sub1.total_outstanding_balance, Decimal("5000.00"))
        self.assertEqual(sub2.total_outstanding_balance, Decimal("10000.00"))

        # Lastro que consome todo o restante da SUB1 e SUB2 e é absorvido só pela MZ1 (break em mz2)
        col2 = CollateralAsset(
            "COL-MED", CollateralType.CPR_FINANCEIRA, "D", "0",
            Decimal("20000.00"), Decimal("20000.00"),
            date(2026, 1, 1), date(2027, 1, 1), IndexerType.CDI, Decimal("0.01")
        )
        issue.add_collateral(col2)
        res2 = issue.process_collateral_default("COL-MED")
        self.assertEqual(res2.loss_absorbed_by_subordinated, Decimal("15000.00"))
        self.assertEqual(res2.loss_absorbed_by_mezzanine, Decimal("5000.00"))
        self.assertEqual(mz1.total_outstanding_balance, Decimal("5000.00"))
        self.assertEqual(mz2.total_outstanding_balance, Decimal("10000.00"))

        # Lastro que consome restante de MZ1, MZ2 e é absorvido só por SR1 (break em sr2)
        col3 = CollateralAsset(
            "COL-GRD", CollateralType.CPR_FINANCEIRA, "D", "0",
            Decimal("20000.00"), Decimal("20000.00"),
            date(2026, 1, 1), date(2027, 1, 1), IndexerType.CDI, Decimal("0.01")
        )
        issue.add_collateral(col3)
        res3 = issue.process_collateral_default("COL-GRD")
        self.assertEqual(res3.loss_absorbed_by_mezzanine, Decimal("15000.00"))
        self.assertEqual(res3.loss_absorbed_by_senior, Decimal("5000.00"))
        self.assertEqual(sr1.total_outstanding_balance, Decimal("5000.00"))
        self.assertEqual(sr2.total_outstanding_balance, Decimal("10000.00"))

    def test_invariant_break_detection(self) -> None:
        """Verifica a detecção de quebra de invariantes (PU desbalanceado e lastros incompatíveis)."""
        rf = ReserveFund("RF-INV", Decimal("1000.00"), Decimal("1000.00"))
        fid = FiduciaryRegime("REG-INV", "S", "0", "T", "0", "0", date(2026, 1, 1), Decimal("0.00"), rf)
        issue = SecuritizationIssue(
            "ISS-INV", SecuritizationType.CRA, LegalFramework.LEI_11076_2004, 1,
            date(2026, 1, 1), date(2028, 1, 1), fid
        )
        tr = CertificateTranche(
            "TR-INV", "0", "0", CertificateTrancheClass.SENIOR,
            AmortizationType.BULLET, IndexerType.CDI, Decimal("0.01"),
            10, 10, Decimal("100.00"), Decimal("100.00")
        )
        issue.add_tranche(tr)

        # Injeta lastro imobiliário diretamente na pool de CRA (bypassing add_collateral)
        bad_col = CollateralAsset(
            "BAD-CCI", CollateralType.CCI, "X", "0",
            Decimal("1000.00"), Decimal("1000.00"),
            date(2026, 1, 1), date(2027, 1, 1), IndexerType.IPCA, Decimal("0.05")
        )
        issue.collateral_pool["BAD-CCI"] = bad_col
        inv1 = issue.verify_invariants()
        self.assertFalse(inv1["collateral_valid_verified"])
        self.assertFalse(inv1["all_invariants_hold"])

        # Testa para CRI com lastro agro
        cri_issue = SecuritizationIssue(
            "ISS-CRI-INV", SecuritizationType.CRI, LegalFramework.LEI_9514_1997, 1,
            date(2026, 1, 1), date(2028, 1, 1), fid
        )
        bad_cpr = CollateralAsset(
            "BAD-CPR", CollateralType.CPR_FINANCEIRA, "Y", "0",
            Decimal("1000.00"), Decimal("1000.00"),
            date(2026, 1, 1), date(2027, 1, 1), IndexerType.CDI, Decimal("0.02")
        )
        cri_issue.collateral_pool["BAD-CPR"] = bad_cpr
        inv2 = cri_issue.verify_invariants()
        self.assertFalse(inv2["collateral_valid_verified"])

        # Desbalanceamento de PU
        del issue.collateral_pool["BAD-CCI"]
        tr.current_unit_price = Decimal("90.00")
        # Mas quantidade permanece 10 e total_outstanding_balance é calculado como quantity * PU,
        # para desbalancear, manipulamos total_outstanding_balance via classe
        class BrokenTranche(CertificateTranche):
            @property
            def total_outstanding_balance(self) -> Decimal:
                return Decimal("99999.00")

        broken_tr = BrokenTranche(
            "TR-BRK", "0", "0", CertificateTrancheClass.SENIOR,
            AmortizationType.BULLET, IndexerType.CDI, Decimal("0.01"),
            10, 10, Decimal("100.00"), Decimal("100.00")
        )
        issue.tranches["TR-BRK"] = broken_tr
        inv3 = issue.verify_invariants()
        self.assertFalse(inv3["pu_conservation_verified"])

    def test_engine_delegations(self) -> None:
        """Testa os métodos de conveniência do CraCriSecuritizationEngine."""
        engine = CraCriSecuritizationEngine.load_from_fixture(
            ROOT_DIR / "fixtures" / "cra_cri_securitization.json"
        )
        res_wtf = engine.execute_waterfall(
            issue_id="CRA-AGRO-2026-001",
            period_date=date(2026, 12, 15),
            period_indexer_rate=Decimal("0.0080"),
        )
        self.assertTrue(res_wtf.conservation_verified)

        res_def = engine.process_collateral_default("CRA-DEFAULT-WRITE-OFF-2026-004", "CPR-CITROS-SP-003")
        self.assertEqual(res_def.total_loss, Decimal("6000000.00"))

    def test_waterfall_conservation_invariant_error(self) -> None:
        """Garante que qualquer quebra contábil artificial dispara SecuritizationInvariantError."""
        rf = ReserveFund("RF-INV2", Decimal("1000.00"), Decimal("1000.00"))
        fid = FiduciaryRegime("REG-INV2", "S", "0", "T", "0", "0", date(2026, 1, 1), Decimal("0.00"), rf)
        issue = SecuritizationIssue(
            "ISS-INV2", SecuritizationType.CRA, LegalFramework.LEI_11076_2004, 1,
            date(2026, 1, 1), date(2028, 1, 1), fid
        )

        class CorruptTranche(CertificateTranche):
            def pay_interest(self, cash_available: Decimal) -> Tuple[Decimal, Decimal]:
                # Frauda o retorno para violar a conservação financeira
                return (Decimal("500000.00"), Decimal("0.00"))

        corrupt_tr = CorruptTranche(
            "TR-CORRUPT", "0", "0", CertificateTrancheClass.SENIOR,
            AmortizationType.BULLET, IndexerType.CDI, Decimal("0.01"),
            10, 10, Decimal("100.00"), Decimal("100.00")
        )
        issue.add_tranche(corrupt_tr)

        with self.assertRaises(SecuritizationInvariantError):
            issue.execute_waterfall(period_date=date(2026, 2, 1))

    def test_exception_classes(self) -> None:
        """Garante cobertura das classes de exceção."""
        e1 = SecuritizationError("base")
        e2 = SecuritizationWaterfallError("waterfall")
        e3 = SecuritizationFiduciaryBreachError("fiduciary")
        e4 = SecuritizationInvariantError("invariant")
        self.assertIsInstance(e1, Exception)
        self.assertIsInstance(e2, SecuritizationError)
        self.assertIsInstance(e3, SecuritizationError)
        self.assertIsInstance(e4, SecuritizationError)


if __name__ == "__main__":
    unittest.main()
