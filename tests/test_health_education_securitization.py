"""Bateria de Testes Unitários — Securitização Social de CR Saúde e CR Educação no Balcão NoMe B3 (FL-085).

Testa exaustivamente com 100% de conformidade formal:
1. Modelagem, arredondamentos e precisão numérica B3 (Decimal, quantização de centavos e taxas).
2. Agregação e elegibilidade estrita de lastros sociais:
   - Saúde (planos ANS, faturas e repasses SUS/AIH/APAC, clínicas privadas).
   - Educação (mensalidades K12, superior e financiamento estudantil privado com fiadores).
   - Rejeição de lastros cruzados (educação em CR Saúde ou saúde em CR Educação).
3. Regime Fiduciário e Incomunicabilidade Absoluta do Patrimônio Separado (Lei nº 14.430/2022, art. 19):
   - Bloqueio de transferências para contas alheias e patrimônio geral da securitizadora.
   - Isolamento total entre patrimônios separados de séries distintas.
4. Gestão dinâmica do Fundo de Reserva obrigatório (Credit Enhancement):
   - Metas de saldo, depósitos, saques de liquidez, remuneração em conta fiduciária e recomposição prioritária.
5. Cascata de pagamentos (Payment Waterfall) em múltiplos cenários:
   - Adimplência perfeita com remuneração de Sênior, Mezanino e Subordinada.
   - Estresse de liquidez com saque do Fundo de Reserva para socorrer Sênior e bloqueio da Subordinada.
   - Recomposição mandatória do Fundo de Reserva antes de qualquer liberação para Subordinada.
   - Desmobilização do Fundo de Reserva na liquidação final.
6. Absorção hierárquica de perdas (First-Loss Piece):
   - Glosas SUS definitivas e inadimplência de mensalidades consumindo Subordinada > Mezanino > Sênior.
7. Conservação formal das Invariantes Matemáticas de Securitização Social.
8. Carga, auditoria e execução a partir do fixture oficial (fixtures/health_education_securitization.json).
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

from src.health_education_securitization import (
    AmortizationType,
    CertificateTranche,
    CertificateTrancheClass,
    CollateralStatus,
    FiduciaryRegime,
    FiduciaryRegimeStatus,
    HealthEducationFiduciaryBreachError,
    HealthEducationInvariantError,
    HealthEducationSecuritizationError,
    HealthEducationSegregationViolationError,
    HealthEducationValidationError,
    HealthEducationWaterfallError,
    IndexerType,
    LegalFramework,
    LossAbsorptionResult,
    ReserveFund,
    SocialCollateralAsset,
    SocialCollateralMetadata,
    SocialCollateralPaymentReceipt,
    SocialCollateralType,
    SocialSecuritizationEngine,
    SocialSecuritizationIssue,
    SocialSecuritizationSector,
    WaterfallExecutionResult,
    load_social_securitization_from_dict,
    round_cents,
    round_rate,
    round_unit,
    to_date,
    to_decimal,
)


class TestSocialSecuritizationUtilsAndEnums(unittest.TestCase):
    """Testes de funções utilitárias de precisão numérica e Enums regulatórios."""

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

        with self.assertRaises(HealthEducationValidationError):
            to_decimal(None, "campo_nulo")

        with self.assertRaises(HealthEducationValidationError):
            to_decimal("invalido", "campo_texto")

    def test_to_date_conversions(self) -> None:
        d = date(2026, 1, 15)
        dt = datetime(2026, 1, 15, 10, 30, 0)
        self.assertEqual(to_date(d), d)
        self.assertEqual(to_date(dt), d)
        self.assertEqual(to_date("2026-01-15"), d)

        with self.assertRaises(HealthEducationValidationError):
            to_date(None)
        with self.assertRaises(HealthEducationValidationError):
            to_date("data-errada")

    def test_social_securitization_sector_enum(self) -> None:
        self.assertEqual(SocialSecuritizationSector.from_str("HEALTH"), SocialSecuritizationSector.HEALTH)
        self.assertEqual(SocialSecuritizationSector.from_str("saude"), SocialSecuritizationSector.HEALTH)
        self.assertEqual(SocialSecuritizationSector.from_str("CR_SAUDE"), SocialSecuritizationSector.HEALTH)
        self.assertEqual(SocialSecuritizationSector.from_str("EDUCATION"), SocialSecuritizationSector.EDUCATION)
        self.assertEqual(SocialSecuritizationSector.from_str("educacao"), SocialSecuritizationSector.EDUCATION)
        self.assertEqual(SocialSecuritizationSector.from_str("CR_EDU"), SocialSecuritizationSector.EDUCATION)

        with self.assertRaises(HealthEducationValidationError):
            SocialSecuritizationSector.from_str("AGRO")

    def test_social_collateral_type_properties(self) -> None:
        # Health types
        for t in (
            SocialCollateralType.HEALTH_PLAN_PREMIUM,
            SocialCollateralType.HOSPITAL_SUS_INVOICE,
            SocialCollateralType.PRIVATE_CLINIC_RECEIVABLE,
        ):
            self.assertTrue(t.is_health)
            self.assertFalse(t.is_education)
            self.assertEqual(t.sector, SocialSecuritizationSector.HEALTH)

        # Education types
        for t in (
            SocialCollateralType.TUITION_FEES_K12,
            SocialCollateralType.TUITION_FEES_HIGHER_ED,
            SocialCollateralType.STUDENT_LOAN_PRIVATE,
        ):
            self.assertTrue(t.is_education)
            self.assertFalse(t.is_health)
            self.assertEqual(t.sector, SocialSecuritizationSector.EDUCATION)


class TestSocialCollateralAsset(unittest.TestCase):
    """Testes unitários da gestão individual de recebíveis de saúde e educação."""

    def setUp(self) -> None:
        self.meta_health = SocialCollateralMetadata(
            ans_operator_code="ANS-12345",
            cnes_hospital_code="CNES-9876",
            sus_aih_number="AIH-112233",
        )
        self.asset_health = SocialCollateralAsset(
            asset_id="SUS-HOSP-01",
            collateral_type=SocialCollateralType.HOSPITAL_SUS_INVOICE,
            debtor_name="HOSPITAL SANTA CASA DE MISERICORDIA",
            debtor_doc="12345678000199",
            original_face_value=Decimal("100000.00"),
            outstanding_balance=Decimal("100000.00"),
            issue_date=date(2026, 1, 1),
            maturity_date=date(2027, 1, 1),
            indexer=IndexerType.CDI,
            interest_rate_pct=Decimal("0.12"),
            metadata=self.meta_health,
        )

    def test_asset_initialization_validation(self) -> None:
        with self.assertRaises(HealthEducationValidationError):
            # Vencimento anterior à emissão
            SocialCollateralAsset(
                asset_id="INV-01",
                collateral_type=SocialCollateralType.TUITION_FEES_K12,
                debtor_name="Escola",
                debtor_doc="111",
                original_face_value=Decimal("100.00"),
                outstanding_balance=Decimal("100.00"),
                issue_date=date(2026, 1, 1),
                maturity_date=date(2025, 1, 1),
                indexer=IndexerType.IPCA,
                interest_rate_pct=Decimal("0.05"),
            )

        with self.assertRaises(HealthEducationValidationError):
            # Valor nominal <= 0
            SocialCollateralAsset(
                asset_id="INV-02",
                collateral_type=SocialCollateralType.TUITION_FEES_K12,
                debtor_name="Escola",
                debtor_doc="111",
                original_face_value=Decimal("0.00"),
                outstanding_balance=Decimal("0.00"),
                issue_date=date(2026, 1, 1),
                maturity_date=date(2027, 1, 1),
                indexer=IndexerType.IPCA,
                interest_rate_pct=Decimal("0.05"),
            )

    def test_asset_receive_payment(self) -> None:
        receipt = self.asset_health.receive_payment(
            principal=Decimal("40000.00"),
            interest=Decimal("5000.00"),
            penalty=Decimal("500.00"),
            payment_date=date(2026, 2, 1),
        )
        self.assertEqual(receipt.principal_paid, Decimal("40000.00"))
        self.assertEqual(receipt.interest_paid, Decimal("5000.00"))
        self.assertEqual(receipt.penalty_paid, Decimal("500.00"))
        self.assertEqual(receipt.total_received, Decimal("45500.00"))
        self.assertEqual(self.asset_health.outstanding_balance, Decimal("60000.00"))
        self.assertEqual(self.asset_health.status, CollateralStatus.PERFORMING)

        # Quitação total
        receipt_final = self.asset_health.receive_payment(
            principal=Decimal("60000.00"),
            interest=Decimal("0.00"),
        )
        self.assertEqual(self.asset_health.outstanding_balance, Decimal("0.00"))
        self.assertEqual(self.asset_health.status, CollateralStatus.SETTLED)

        # Erro ao amortizar além do saldo
        with self.assertRaises(HealthEducationValidationError):
            self.asset_health.receive_payment(principal=Decimal("100.00"), interest=Decimal("0.00"))

    def test_asset_delinquency_and_write_off(self) -> None:
        self.asset_health.mark_delinquent(30)
        self.assertEqual(self.asset_health.delinquency_days, 30)
        self.assertEqual(self.asset_health.status, CollateralStatus.DELINQUENT)

        # Volta a adimplente se regularizado
        self.asset_health.mark_delinquent(0)
        self.assertEqual(self.asset_health.status, CollateralStatus.PERFORMING)

        # Write-off definitivo de glosa SUS
        self.asset_health.accrue_interest(Decimal("0.01"))
        self.assertEqual(self.asset_health.accrued_interest, Decimal("1000.00"))

        loss = self.asset_health.write_off()
        self.assertEqual(loss, Decimal("101000.00"))
        self.assertEqual(self.asset_health.outstanding_balance, Decimal("0.00"))
        self.assertEqual(self.asset_health.accrued_interest, Decimal("0.00"))
        self.assertEqual(self.asset_health.status, CollateralStatus.DEFAULTED)

        # Write-off de lastro já baixado não é permitido
        with self.assertRaises(HealthEducationValidationError):
            self.asset_health.write_off()


class TestReserveFundManagement(unittest.TestCase):
    """Testes unitários da gestão do Fundo de Reserva obrigatório."""

    def setUp(self) -> None:
        self.rf = ReserveFund(
            fund_id="RF-SAUDE-01",
            target_balance=Decimal("2000000.00"),
            current_balance=Decimal("2000000.00"),
            minimum_floor=Decimal("1000000.00"),
            yield_rate_annual_pct=Decimal("0.12"),
        )

    def test_initial_state_and_properties(self) -> None:
        self.assertFalse(self.rf.is_deficient)
        self.assertEqual(self.rf.deficit_amount, Decimal("0.00"))

    def test_draw_and_deficiency(self) -> None:
        drawn = self.rf.draw(Decimal("500000.00"))
        self.assertEqual(drawn, Decimal("500000.00"))
        self.assertEqual(self.rf.current_balance, Decimal("1500000.00"))
        self.assertTrue(self.rf.is_deficient)
        self.assertEqual(self.rf.deficit_amount, Decimal("500000.00"))

        # Saque limitado ao saldo disponível
        drawn_excess = self.rf.draw(Decimal("2500000.00"))
        self.assertEqual(drawn_excess, Decimal("1500000.00"))
        self.assertEqual(self.rf.current_balance, Decimal("0.00"))
        self.assertEqual(self.rf.deficit_amount, Decimal("2000000.00"))

    def test_deposit_and_yield(self) -> None:
        self.rf.draw(Decimal("1000000.00"))
        self.assertEqual(self.rf.current_balance, Decimal("1000000.00"))

        # Depósito
        self.rf.deposit(Decimal("400000.00"))
        self.assertEqual(self.rf.current_balance, Decimal("1400000.00"))

        # Rendimento mensal (1% sobre 1.400.000,00 = 14.000,00)
        earned = self.rf.apply_yield(Decimal("0.01"))
        self.assertEqual(earned, Decimal("14000.00"))
        self.assertEqual(self.rf.current_balance, Decimal("1414000.00"))


class TestCertificateTranches(unittest.TestCase):
    """Testes unitários de tranches, PU e subordinação."""

    def setUp(self) -> None:
        self.tranche = CertificateTranche(
            tranche_id="TR-SR-01",
            isin="BRCRSAUSEN01",
            b3_code="CRSAU01",
            tranche_class=CertificateTrancheClass.SENIOR,
            amortization_type=AmortizationType.SAC,
            indexer=IndexerType.CDI,
            spread_rate_annual_pct=Decimal("0.02"),
            original_quantity=10000,
            current_quantity=10000,
            original_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
        )

    def test_balance_and_interest(self) -> None:
        self.assertEqual(self.tranche.total_outstanding_balance, Decimal("10000000.00"))
        interest = self.tranche.accrue_interest_period(Decimal("0.01"))
        self.assertEqual(interest, Decimal("100000.00"))
        self.assertEqual(self.tranche.accrued_interest, Decimal("100000.00"))

        # Pagamento parcial de juros
        paid, deficit = self.tranche.pay_interest(Decimal("60000.00"))
        self.assertEqual(paid, Decimal("60000.00"))
        self.assertEqual(deficit, Decimal("40000.00"))
        self.assertEqual(self.tranche.unpaid_interest_deficit, Decimal("40000.00"))
        self.assertEqual(self.tranche.accrued_interest, Decimal("0.00"))

    def test_amortization_and_pu(self) -> None:
        amortized = self.tranche.amortize(Decimal("2000000.00"))
        self.assertEqual(amortized, Decimal("2000000.00"))
        self.assertEqual(self.tranche.total_outstanding_balance, Decimal("8000000.00"))
        self.assertEqual(self.tranche.current_unit_price, Decimal("800.00000000"))

    def test_loss_absorption(self) -> None:
        sub_tranche = CertificateTranche(
            tranche_id="TR-SUB-01",
            isin="BRCRSAUSUB01",
            b3_code="CRSAU01S",
            tranche_class=CertificateTrancheClass.SUBORDINATED,
            amortization_type=AmortizationType.BULLET,
            indexer=IndexerType.CDI,
            spread_rate_annual_pct=Decimal("0.05"),
            original_quantity=1000,
            current_quantity=1000,
            original_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
        )
        self.assertEqual(sub_tranche.total_outstanding_balance, Decimal("1000000.00"))

        absorbed, unabsorbed = sub_tranche.absorb_loss(Decimal("400000.00"))
        self.assertEqual(absorbed, Decimal("400000.00"))
        self.assertEqual(unabsorbed, Decimal("0.00"))
        self.assertEqual(sub_tranche.total_outstanding_balance, Decimal("600000.00"))
        self.assertEqual(sub_tranche.current_unit_price, Decimal("600.00000000"))

        # Absorção excedendo o saldo restante
        absorbed2, unabsorbed2 = sub_tranche.absorb_loss(Decimal("800000.00"))
        self.assertEqual(absorbed2, Decimal("600000.00"))
        self.assertEqual(unabsorbed2, Decimal("200000.00"))
        self.assertEqual(sub_tranche.total_outstanding_balance, Decimal("0.00"))
        self.assertEqual(sub_tranche.current_unit_price, Decimal("0.00000000"))


class TestFiduciaryRegimeAndIncommunicability(unittest.TestCase):
    """Testes da incomunicabilidade do patrimônio separado e blindagem legal (Lei 14.430/2022)."""

    def setUp(self) -> None:
        self.rf = ReserveFund(
            fund_id="RF-01",
            target_balance=Decimal("1000000.00"),
            current_balance=Decimal("1000000.00"),
        )
        self.regime = FiduciaryRegime(
            regime_id="REG-SAUDE-01",
            securitizer_name="SECURITIZADORA SOCIAL S.A.",
            securitizer_cnpj="11222333000144",
            trustee_name="TRUSTEE DTVM S.A.",
            trustee_cnpj="55666777000188",
            deed_registry_number="TERMO-SEC-01",
            registry_date=date(2026, 1, 10),
            segregated_account_id="CC-FID-SAUDE-001",
            segregated_cash_balance=Decimal("500000.00"),
            reserve_fund=self.rf,
        )

    def test_assert_incommunicability_violation(self) -> None:
        # Mesma conta: permitido
        self.regime.assert_incommunicability("CC-FID-SAUDE-001")

        # Conta de outra emissão ou patrimônio geral: violação
        with self.assertRaises(HealthEducationSegregationViolationError):
            self.regime.assert_incommunicability("CC-SECURITIZADORA-GERAL")

    def test_unauthorized_withdrawal_protection(self) -> None:
        # Desembolso com finalidade autorizada
        withdrawn = self.regime.withdraw_segregated_cash(Decimal("50000.00"), "WATERFALL_EXPENSE")
        self.assertEqual(withdrawn, Decimal("50000.00"))
        self.assertEqual(self.regime.segregated_cash_balance, Decimal("450000.00"))

        # Tentativa de desvio para conta da securitizadora fora do waterfall
        with self.assertRaises(HealthEducationSegregationViolationError):
            self.regime.withdraw_segregated_cash(Decimal("10000.00"), "SECURITIZER_GENERAL_FUND")

        with self.assertRaises(HealthEducationSegregationViolationError):
            self.regime.withdraw_segregated_cash(Decimal("10000.00"), "UNAUTHORIZED_TRANSFER")


class TestSocialSecuritizationEligibilityAndStructure(unittest.TestCase):
    """Testes de elegibilidade de lastro, subordinação e invariantes em CR Saúde e CR Educação."""

    def setUp(self) -> None:
        self.rf_saude = ReserveFund("RF-SAUDE", Decimal("1000000.00"), Decimal("1000000.00"))
        self.fid_saude = FiduciaryRegime(
            regime_id="REG-S-01",
            securitizer_name="SEC SAUDE",
            securitizer_cnpj="11111111000111",
            trustee_name="TRUSTEE",
            trustee_cnpj="22222222000122",
            deed_registry_number="TERMO-S-01",
            registry_date=date(2026, 1, 1),
            segregated_account_id="CC-FID-SAUDE-01",
            segregated_cash_balance=Decimal("200000.00"),
            reserve_fund=self.rf_saude,
        )
        self.issue_saude = SocialSecuritizationIssue(
            issue_id="CR-SAUDE-01",
            sector=SocialSecuritizationSector.HEALTH,
            legal_framework=[LegalFramework.LEI_14430_2022, LegalFramework.RESOLUCAO_CVM_60],
            series_number=1,
            issue_date=date(2026, 1, 1),
            final_maturity_date=date(2031, 1, 1),
            fiduciary_regime=self.fid_saude,
        )

        self.rf_edu = ReserveFund("RF-EDU", Decimal("800000.00"), Decimal("800000.00"))
        self.fid_edu = FiduciaryRegime(
            regime_id="REG-E-01",
            securitizer_name="SEC EDU",
            securitizer_cnpj="33333333000133",
            trustee_name="TRUSTEE",
            trustee_cnpj="22222222000122",
            deed_registry_number="TERMO-E-01",
            registry_date=date(2026, 1, 1),
            segregated_account_id="CC-FID-EDU-01",
            segregated_cash_balance=Decimal("150000.00"),
            reserve_fund=self.rf_edu,
        )
        self.issue_edu = SocialSecuritizationIssue(
            issue_id="CR-EDU-01",
            sector=SocialSecuritizationSector.EDUCATION,
            legal_framework=[LegalFramework.LEI_14430_2022],
            series_number=1,
            issue_date=date(2026, 1, 1),
            final_maturity_date=date(2031, 1, 1),
            fiduciary_regime=self.fid_edu,
        )

    def test_strict_sector_eligibility(self) -> None:
        asset_health = SocialCollateralAsset(
            asset_id="SUS-01",
            collateral_type=SocialCollateralType.HOSPITAL_SUS_INVOICE,
            debtor_name="SUS",
            debtor_doc="123",
            original_face_value=Decimal("500000.00"),
            outstanding_balance=Decimal("500000.00"),
            issue_date=date(2026, 1, 1),
            maturity_date=date(2028, 1, 1),
            indexer=IndexerType.CDI,
            interest_rate_pct=Decimal("0.10"),
        )
        asset_edu = SocialCollateralAsset(
            asset_id="EDU-01",
            collateral_type=SocialCollateralType.TUITION_FEES_HIGHER_ED,
            debtor_name="Universidade",
            debtor_doc="456",
            original_face_value=Decimal("500000.00"),
            outstanding_balance=Decimal("500000.00"),
            issue_date=date(2026, 1, 1),
            maturity_date=date(2028, 1, 1),
            indexer=IndexerType.IPCA,
            interest_rate_pct=Decimal("0.08"),
        )

        # Saúde aceita saúde, rejeita educação
        self.issue_saude.add_collateral(asset_health)
        with self.assertRaises(HealthEducationValidationError):
            self.issue_saude.add_collateral(asset_edu)

        # Educação aceita educação, rejeita saúde
        self.issue_edu.add_collateral(asset_edu)
        with self.assertRaises(HealthEducationValidationError):
            self.issue_edu.add_collateral(asset_health)

    def test_incommunicability_attempted_transfer(self) -> None:
        with self.assertRaises(HealthEducationSegregationViolationError):
            self.issue_saude.attempt_unauthorized_transfer("CONTA-SECURITIZADORA-MATRIZ", Decimal("100000.00"))


class TestWaterfallAndHierarchicalLossAbsorption(unittest.TestCase):
    """Testes completos da execução do Waterfall, conservação matemática e First-Loss Piece."""

    def setUp(self) -> None:
        self.rf = ReserveFund(
            fund_id="RF-01",
            target_balance=Decimal("1000000.00"),
            current_balance=Decimal("1000000.00"),
            minimum_floor=Decimal("500000.00"),
            yield_rate_annual_pct=Decimal("0.12"),  # 1% ao mês
        )
        self.fid = FiduciaryRegime(
            regime_id="REG-SAUDE-01",
            securitizer_name="VERT SEC",
            securitizer_cnpj="111",
            trustee_name="VORTX",
            trustee_cnpj="222",
            deed_registry_number="TERMO-01",
            registry_date=date(2026, 1, 1),
            segregated_account_id="CC-FID-01",
            segregated_cash_balance=Decimal("200000.00"),
            reserve_fund=self.rf,
        )
        self.issue = SocialSecuritizationIssue(
            issue_id="CR-SAUDE-MULTI",
            sector=SocialSecuritizationSector.HEALTH,
            legal_framework=[LegalFramework.LEI_14430_2022],
            series_number=1,
            issue_date=date(2026, 1, 1),
            final_maturity_date=date(2031, 1, 1),
            fiduciary_regime=self.fid,
        )

        # Tranches: Senior (20M), Mezzanine (5M), Subordinated (3M)
        self.tranche_sr = CertificateTranche(
            tranche_id="TR-SR",
            isin="BRCRSSEN001",
            b3_code="CRSSEN",
            tranche_class=CertificateTrancheClass.SENIOR,
            amortization_type=AmortizationType.SAC,
            indexer=IndexerType.CDI,
            spread_rate_annual_pct=Decimal("0.0120"),  # 0.10% ao mês
            original_quantity=20000,
            current_quantity=20000,
            original_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
            amortization_schedule={"2026-02-01": Decimal("1000000.00")},
        )
        self.tranche_mez = CertificateTranche(
            tranche_id="TR-MEZ",
            isin="BRCRSMEZ002",
            b3_code="CRSMEZ",
            tranche_class=CertificateTrancheClass.MEZZANINE,
            amortization_type=AmortizationType.SAC,
            indexer=IndexerType.CDI,
            spread_rate_annual_pct=Decimal("0.0240"),  # 0.20% ao mês
            original_quantity=5000,
            current_quantity=5000,
            original_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
            amortization_schedule={"2026-02-01": Decimal("250000.00")},
        )
        self.tranche_sub = CertificateTranche(
            tranche_id="TR-SUB",
            isin="BRCRSSUB003",
            b3_code="CRSSUB",
            tranche_class=CertificateTrancheClass.SUBORDINATED,
            amortization_type=AmortizationType.BULLET,
            indexer=IndexerType.CDI,
            spread_rate_annual_pct=Decimal("0.0480"),  # 0.40% ao mês
            original_quantity=3000,
            current_quantity=3000,
            original_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
        )
        self.issue.add_tranche(self.tranche_sr)
        self.issue.add_tranche(self.tranche_mez)
        self.issue.add_tranche(self.tranche_sub)

        # Lastro hospitalar SUS e clínicas
        self.asset_sus = SocialCollateralAsset(
            asset_id="SUS-AST-01",
            collateral_type=SocialCollateralType.HOSPITAL_SUS_INVOICE,
            debtor_name="MINISTERIO DA SAUDE",
            debtor_doc="0001",
            original_face_value=Decimal("20000000.00"),
            outstanding_balance=Decimal("20000000.00"),
            issue_date=date(2026, 1, 1),
            maturity_date=date(2028, 1, 1),
            indexer=IndexerType.CDI,
            interest_rate_pct=Decimal("0.12"),
        )
        self.asset_plan = SocialCollateralAsset(
            asset_id="PLAN-AST-02",
            collateral_type=SocialCollateralType.HEALTH_PLAN_PREMIUM,
            debtor_name="UNIMED BRASIL",
            debtor_doc="0002",
            original_face_value=Decimal("10000000.00"),
            outstanding_balance=Decimal("10000000.00"),
            issue_date=date(2026, 1, 1),
            maturity_date=date(2028, 1, 1),
            indexer=IndexerType.CDI,
            interest_rate_pct=Decimal("0.12"),
        )
        self.issue.add_collateral(self.asset_sus)
        self.issue.add_collateral(self.asset_plan)

    def test_waterfall_performing_scenario(self) -> None:
        # Recebimento de R$ 2.000.000,00 de amortização + juros
        receipt = self.issue.register_collateral_payment(
            asset_id="SUS-AST-01",
            principal=Decimal("1800000.00"),
            interest=Decimal("200000.00"),
            payment_date=date(2026, 2, 1),
        )
        self.assertEqual(receipt.total_received, Decimal("2000000.00"))
        self.assertEqual(self.issue.pending_inflow, Decimal("2000000.00"))

        result = self.issue.execute_waterfall(
            period_date=date(2026, 2, 1),
            expenses_override=Decimal("20000.00"),
            period_indexer_rate=Decimal("0.0090"),  # CDI 0.90% no mês
            allow_subordinated_residual_payout=False,
        )

        self.assertTrue(result.conservation_verified)
        self.assertTrue(result.is_senior_fully_satisfied)
        self.assertTrue(result.is_reserve_at_target)
        self.assertEqual(result.expenses_paid, Decimal("20000.00"))
        self.assertGreater(result.senior_interest_paid, Decimal("0.00"))
        self.assertEqual(result.senior_principal_paid, Decimal("1000000.00"))
        self.assertGreater(result.mezzanine_interest_paid, Decimal("0.00"))
        self.assertEqual(result.mezzanine_principal_paid, Decimal("250000.00"))
        self.assertGreater(result.subordinated_interest_paid, Decimal("0.00"))
        self.assertGreaterEqual(result.closing_cash_balance, Decimal("0.00"))

        # Invariantes após waterfall
        invs = self.issue.verify_invariants()
        self.assertTrue(invs["all_invariants_hold"])

    def test_waterfall_liquidity_stress_and_reserve_draw(self) -> None:
        # Nenhum recebimento de lastro (inadimplência temporária)
        # Caixa segregado é de apenas R$ 200.000,00
        # Serviço sênior demanda juros (aprox 200k) + amortização (1M) = 1.2M
        # Fundo de reserva deve ser sacado para cobrir o déficit sênior!

        result = self.issue.execute_waterfall(
            period_date=date(2026, 2, 1),
            expenses_override=Decimal("10000.00"),
            period_indexer_rate=Decimal("0.0090"),
        )

        self.assertTrue(result.conservation_verified)
        self.assertGreater(result.reserve_fund_draw, Decimal("0.00"))
        self.assertTrue(self.issue.fiduciary_regime.reserve_fund.is_deficient)

        # Regra de Ouro: Como a reserva está defasada, a Subordinada recebe ZERO!
        self.assertEqual(result.subordinated_interest_paid, Decimal("0.00"))
        self.assertEqual(result.subordinated_principal_paid, Decimal("0.00"))

    def test_waterfall_reserve_replenishment_priority(self) -> None:
        # 1. Primeiro período em estresse que consome a reserva
        self.issue.execute_waterfall(
            period_date=date(2026, 2, 1),
            expenses_override=Decimal("10000.00"),
            period_indexer_rate=Decimal("0.0090"),
        )
        self.assertTrue(self.issue.fiduciary_regime.reserve_fund.is_deficient)
        deficit_before = self.issue.fiduciary_regime.reserve_fund.deficit_amount

        # 2. Segundo período: forte entrada de caixa de R$ 3.000.000,00
        self.issue.register_collateral_payment(
            asset_id="SUS-AST-01",
            principal=Decimal("2500000.00"),
            interest=Decimal("500000.00"),
            payment_date=date(2026, 3, 1),
        )

        result2 = self.issue.execute_waterfall(
            period_date=date(2026, 3, 1),
            expenses_override=Decimal("10000.00"),
            period_indexer_rate=Decimal("0.0090"),
        )

        self.assertTrue(result2.conservation_verified)
        # O Fundo de Reserva foi recomposto prioritariamente até a meta
        self.assertGreater(result2.reserve_fund_deposit, Decimal("0.00"))
        # A defasagem antes do aporte no período 2 (após rendimento mensal de R$ 20 sobre o saldo remanescente de R$ 2.000)
        self.assertEqual(result2.closing_reserve_balance, self.issue.fiduciary_regime.reserve_fund.target_balance)
        self.assertTrue(result2.is_reserve_at_target)

    def test_first_loss_piece_hierarchical_absorption(self) -> None:
        # Caso 1: Perda de R$ 1.500.000,00 (absorvida 100% pela Subordinada de R$ 3M)
        res1 = self.issue.process_collateral_default("PLAN-AST-02")
        # PLAN-AST-02 tem R$ 10.000.000,00 de saldo. Isso excede Subordinada (3M) e Mezanino (5M)!
        # Esperado: Subordinada absorve 3M, Mezanino absorve 5M, Sênior absorve 2M!
        self.assertEqual(res1.total_loss, Decimal("10000000.00"))
        self.assertEqual(res1.loss_absorbed_by_subordinated, Decimal("3000000.00"))
        self.assertEqual(res1.loss_absorbed_by_mezzanine, Decimal("5000000.00"))
        self.assertEqual(res1.loss_absorbed_by_senior, Decimal("2000000.00"))
        self.assertEqual(res1.unabsorbed_loss, Decimal("0.00"))

        self.assertEqual(self.tranche_sub.total_outstanding_balance, Decimal("0.00"))
        self.assertEqual(self.tranche_mez.total_outstanding_balance, Decimal("0.00"))
        self.assertEqual(self.tranche_sr.total_outstanding_balance, Decimal("18000000.00"))

    def test_unwind_reserve_at_maturity_rules(self) -> None:
        # Não pode desmobilizar se houver saldo sênior
        with self.assertRaises(HealthEducationWaterfallError):
            self.issue.unwind_reserve_at_maturity()

        # Zerando tranches sênior e mezanino
        self.tranche_sr.current_quantity = 0
        self.tranche_mez.current_quantity = 0
        self.assertEqual(self.issue.senior_balance(), Decimal("0.00"))
        self.assertEqual(self.issue.mezzanine_balance(), Decimal("0.00"))

        cash_before = self.issue.fiduciary_regime.segregated_cash_balance
        res_before = self.issue.fiduciary_regime.reserve_fund.current_balance
        unwound = self.issue.unwind_reserve_at_maturity()

        self.assertEqual(unwound, res_before)
        self.assertEqual(self.issue.fiduciary_regime.reserve_fund.current_balance, Decimal("0.00"))
        self.assertEqual(self.issue.fiduciary_regime.segregated_cash_balance, cash_before + res_before)


class TestSocialSecuritizationEngineAndFixture(unittest.TestCase):
    """Testes de ponta a ponta do motor orquestrador com a fixture sintética oficial."""

    def setUp(self) -> None:
        self.fixture_path = ROOT_DIR / "fixtures" / "health_education_securitization.json"
        self.assertTrue(self.fixture_path.is_file(), f"Fixture não encontrada em {self.fixture_path}")
        self.engine = SocialSecuritizationEngine.load_from_fixture(self.fixture_path)

    def test_fixture_loading_and_structure(self) -> None:
        self.assertIn("CR-SAUDE-2026-001", self.engine.issues)
        self.assertIn("CR-EDU-2026-001", self.engine.issues)

        saude = self.engine.get_issue("CR-SAUDE-2026-001")
        self.assertEqual(saude.sector, SocialSecuritizationSector.HEALTH)
        self.assertEqual(len(saude.collateral_pool), 3)
        self.assertEqual(len(saude.tranches), 3)

        edu = self.engine.get_issue("CR-EDU-2026-001")
        self.assertEqual(edu.sector, SocialSecuritizationSector.EDUCATION)
        self.assertEqual(len(edu.collateral_pool), 3)
        self.assertEqual(len(edu.tranches), 2)

    def test_verify_all_invariants_hold(self) -> None:
        invariants = self.engine.verify_all_invariants()
        self.assertIn("CR-SAUDE-2026-001", invariants)
        self.assertIn("CR-EDU-2026-001", invariants)

        for issue_id, report in invariants.items():
            for key, val in report.items():
                self.assertTrue(val, f"Invariante {key} violada na emissão {issue_id}")

    def test_engine_waterfall_execution(self) -> None:
        # Executa waterfall para Saúde
        res_saude = self.engine.execute_waterfall(
            issue_id="CR-SAUDE-2026-001",
            period_date=date(2026, 12, 15),
            period_indexer_rate=Decimal("0.0085"),
        )
        self.assertTrue(res_saude.conservation_verified)

        # Executa waterfall para Educação
        res_edu = self.engine.execute_waterfall(
            issue_id="CR-EDU-2026-001",
            period_date=date(2026, 12, 1),
            period_indexer_rate=Decimal("0.0040"),
        )
        self.assertTrue(res_edu.conservation_verified)

    def test_engine_inter_issue_incommunicability(self) -> None:
        with self.assertRaises(HealthEducationSegregationViolationError):
            self.engine.attempt_inter_issue_transfer(
                source_issue_id="CR-SAUDE-2026-001",
                target_issue_id="CR-EDU-2026-001",
                amount=Decimal("100000.00"),
            )

    def test_engine_serialization_roundtrip(self) -> None:
        serialized = self.engine.to_dict()
        self.assertEqual(serialized["flow_code"], "FL-085")
        self.assertEqual(len(serialized["issues"]), 2)

        reloaded = SocialSecuritizationEngine.load_from_dict(serialized)
        self.assertEqual(len(reloaded.issues), 2)
        invs = reloaded.verify_all_invariants()
        for issue_id, report in invs.items():
            self.assertTrue(report["all_invariants_hold"])


if __name__ == "__main__":
    unittest.main()
