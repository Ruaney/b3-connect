"""Testes unitários e verificação formal do Módulo de Empréstimo e Operações Compromissadas com TPFs (FL-076).

Valida conformidade com:
- Leis de Conservação de Custódia Soberana (LAW-059): Custódia_Total == Disponível + Emprestada + Bloqueada_Garantia.
- Lei de Paridade de Recompra em Operações Compromissadas (LAW-060): Preço_Recompra == Preço_Venda * (1 + Taxa_Repo * DU/252).
- Normas da Câmara B3 e SELIC para Empréstimo de TPF (D0 / D+1) com taxas ativa e passiva.
- Repasse integral de 100% de cupons semestrais de NTN-B e NTN-F.
- Margem de garantia multilateral mínima de 100% do PU de mercado.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.sovereign_tpf_lending import (
    CollateralAssetType,
    CollateralMarginAllocation,
    CouponForwardEvent,
    LendingRateType,
    LoanStatus,
    RepoStatus,
    SettlementCycle,
    SovereignBond,
    SovereignCollateralDeficitError,
    SovereignCouponForwardError,
    SovereignCustodyAccount,
    SovereignCustodyConservationError,
    SovereignCustodyPosition,
    SovereignLoanContract,
    SovereignRepoAgreement,
    SovereignRepoParityError,
    SovereignStateTransitionError,
    SovereignTpfError,
    SovereignTpfLendingEngine,
    SovereignTpfValidationError,
    TPFBondType,
    count_business_days,
    is_business_day,
    round_cents,
    round_pu,
)


class TestSovereignTpfLending(unittest.TestCase):
    """Bateria de testes unitários e de invariantes para o fluxo FL-076."""

    def setUp(self) -> None:
        self.fixture_path = (
            Path(__file__).resolve().parent.parent / "fixtures" / "sovereign_tpf_lending.json"
        )
        self.engine = SovereignTpfLendingEngine.load_from_fixture(self.fixture_path)

    # -----------------------------------------------------------------
    # 1. Carregamento da Fixture e Verificação Formal de Invariantes
    # -----------------------------------------------------------------

    def test_fixture_loading_and_formal_invariants(self) -> None:
        """Verifica se a fixture carrega com integridade e todas as invariantes formais se sustentam."""
        self.assertEqual(len(self.engine.bonds), 4)
        self.assertEqual(len(self.engine.accounts), 3)
        self.assertEqual(len(self.engine.loans), 2)
        self.assertEqual(len(self.engine.repos), 2)
        self.assertEqual(len(self.engine.coupon_history), 1)

        inv = self.engine.verify_invariants()
        self.assertTrue(inv["all_invariants_hold"], f"Violações detectadas: {inv['violations']}")
        self.assertTrue(inv["sovereign_custody_conservation"])
        self.assertTrue(inv["repo_repurchase_parity"])
        self.assertTrue(inv["multilateral_margin_coverage"])
        self.assertTrue(inv["coupon_forward_integrity"])
        self.assertTrue(inv["non_negative_balances"])
        self.assertEqual(len(inv["violations"]), 0)

    def test_serialization_round_trip(self) -> None:
        """Verifica a integridade de serialização to_dict() e reconstituição from_dict()."""
        data = self.engine.to_dict()
        reconstituted = SovereignTpfLendingEngine.from_dict(data)

        self.assertEqual(len(reconstituted.bonds), len(self.engine.bonds))
        self.assertEqual(len(reconstituted.accounts), len(self.engine.accounts))
        self.assertEqual(len(reconstituted.loans), len(self.engine.loans))
        self.assertEqual(len(reconstituted.repos), len(self.engine.repos))

        inv = reconstituted.verify_invariants()
        self.assertTrue(inv["all_invariants_hold"])

    # -----------------------------------------------------------------
    # 2. Catálogo de Títulos Públicos Federais (LTN, LFT, NTN-B, NTN-F)
    # -----------------------------------------------------------------

    def test_tpf_types_and_coupon_properties(self) -> None:
        """Valida propriedades e regras de cupom dos 4 tipos de TPFs."""
        ltn = self.engine.get_bond("LTN-2027-01-01")
        lft = self.engine.get_bond("LFT-2029-03-01")
        ntnb = self.engine.get_bond("NTN-B-2035-05-15")
        ntnf = self.engine.get_bond("NTN-F-2029-01-01")

        # LTN: Prefixado, sem cupom
        self.assertTrue(ltn.bond_type.is_prefixed)
        self.assertFalse(ltn.bond_type.has_semiannual_coupon)
        self.assertEqual(ltn.calculate_semiannual_coupon_pu(), Decimal("0.000000"))

        # LFT: Pós-fixado SELIC, sem cupom
        self.assertTrue(lft.bond_type.is_selic_postfixed)
        self.assertFalse(lft.bond_type.has_semiannual_coupon)
        self.assertEqual(lft.calculate_semiannual_coupon_pu(), Decimal("0.000000"))

        # NTN-B: Indexado ao IPCA, cupom semestral de 6% a.a.
        self.assertTrue(ntnb.bond_type.is_inflation_linked)
        self.assertTrue(ntnb.bond_type.has_semiannual_coupon)
        coupon_ntnb = ntnb.calculate_semiannual_coupon_pu(reference_nominal_value=Decimal("4000.000000"))
        # 4000 * ((1.06)^0.5 - 1) = 4000 * 0.02956301 = 118.25204
        self.assertAlmostEqual(float(coupon_ntnb), 118.25204, places=2)

        # NTN-F: Prefixado, cupom semestral de 10% a.a.
        self.assertTrue(ntnf.bond_type.is_prefixed)
        self.assertTrue(ntnf.bond_type.has_semiannual_coupon)
        coupon_ntnf = ntnf.calculate_semiannual_coupon_pu(reference_nominal_value=Decimal("1000.000000"))
        # 1000 * ((1.10)^0.5 - 1) = 48.808848
        self.assertEqual(coupon_ntnf, Decimal("48.808848"))

    def test_bond_validation_guardrails(self) -> None:
        """Garante validação estrita no cadastro de títulos públicos."""
        with self.assertRaises(SovereignTpfValidationError):
            SovereignBond(
                bond_id="",
                bond_type=TPFBondType.LTN,
                isin="BRSTNCLTN001",
                issue_date=date(2024, 1, 1),
                maturity_date=date(2026, 1, 1),
                market_unit_price=Decimal("900.00"),
            )

        with self.assertRaises(SovereignTpfValidationError):
            # Vencimento anterior à emissão
            SovereignBond(
                bond_id="LTN-INV",
                bond_type=TPFBondType.LTN,
                isin="BRSTNCLTN002",
                issue_date=date(2025, 1, 1),
                maturity_date=date(2024, 1, 1),
                market_unit_price=Decimal("900.00"),
            )

        with self.assertRaises(SovereignTpfValidationError):
            # PU negativo
            SovereignBond(
                bond_id="LTN-INV2",
                bond_type=TPFBondType.LTN,
                isin="BRSTNCLTN003",
                issue_date=date(2024, 1, 1),
                maturity_date=date(2027, 1, 1),
                market_unit_price=Decimal("-10.00"),
            )

    # -----------------------------------------------------------------
    # 3. Lei de Conservação de Custódia Soberana (LAW-059)
    # -----------------------------------------------------------------

    def test_custody_conservation_law_strict(self) -> None:
        """Verifica a equação: Custódia_Total == Disponível + Emprestada + Bloqueada_Garantia."""
        acc = SovereignCustodyAccount(
            account_id="ACC-TEST-01",
            participant_id="PART-01",
            participant_name="Test Bank",
            cash_balance=Decimal("1000000.00"),
        )
        pos = acc.get_position("LTN-2027-01-01")
        pos.total_quantity = Decimal("1000.0000")
        pos.available_quantity = Decimal("1000.0000")
        self.assertTrue(pos.verify_conservation("ACC-TEST-01"))

        # Bloqueio de margem
        pos.lock_collateral(Decimal("300.0000"), "ACC-TEST-01")
        self.assertEqual(pos.available_quantity, Decimal("700.0000"))
        self.assertEqual(pos.collateral_blocked_quantity, Decimal("300.0000"))
        self.assertEqual(pos.total_quantity, Decimal("1000.0000"))

        # Empréstimo
        pos.lend(Decimal("400.0000"), "ACC-TEST-01")
        self.assertEqual(pos.available_quantity, Decimal("300.0000"))
        self.assertEqual(pos.lent_quantity, Decimal("400.0000"))
        self.assertEqual(pos.collateral_blocked_quantity, Decimal("300.0000"))
        self.assertEqual(pos.total_quantity, Decimal("1000.0000"))

        # Retorno de empréstimo
        pos.return_lending(Decimal("200.0000"), "ACC-TEST-01")
        self.assertEqual(pos.available_quantity, Decimal("500.0000"))
        self.assertEqual(pos.lent_quantity, Decimal("200.0000"))

        # Liberação de margem
        pos.release_collateral(Decimal("100.0000"), "ACC-TEST-01")
        self.assertEqual(pos.available_quantity, Decimal("600.0000"))
        self.assertEqual(pos.collateral_blocked_quantity, Decimal("200.0000"))
        self.assertEqual(pos.total_quantity, Decimal("1000.0000"))

    def test_custody_conservation_violations_caught(self) -> None:
        """Garante que qualquer assimetria ou valor negativo levante SovereignCustodyConservationError."""
        pos = SovereignCustodyPosition(
            bond_id="LTN-2027-01-01",
            total_quantity=Decimal("100.0000"),
            available_quantity=Decimal("100.0000"),
        )
        # Tentativa de emprestar mais do que o disponível
        with self.assertRaises(SovereignCustodyConservationError):
            pos.lend(Decimal("150.0000"))

        # Tentativa de bloquear mais garantia do que o disponível
        with self.assertRaises(SovereignCustodyConservationError):
            pos.lock_collateral(Decimal("110.0000"))

        # Alteração artificial de saldo sem conservação
        pos.available_quantity = Decimal("90.0000")
        with self.assertRaises(SovereignCustodyConservationError):
            pos.verify_conservation()

    # -----------------------------------------------------------------
    # 4. Ciclo de Vida do Empréstimo de TPF (D0 e D+1) e Margem Multilateral
    # -----------------------------------------------------------------

    def test_tpf_lending_lifecycle_success(self) -> None:
        """Executa o ciclo completo de empréstimo: registro, margem, início D0, juros e devolução."""
        engine = SovereignTpfLendingEngine()

        bond = SovereignBond(
            bond_id="LFT-2029-03-01",
            bond_type=TPFBondType.LFT,
            isin="BRSTNCLFT6Q2",
            issue_date=date(2023, 3, 10),
            maturity_date=date(2029, 3, 1),
            market_unit_price=Decimal("14800.000000"),
        )
        engine.register_bond(bond)

        lender = SovereignCustodyAccount(
            account_id="ACC-LENDER",
            participant_id="PART-LEND",
            participant_name="Doador S.A.",
            cash_balance=Decimal("10000.00"),
        )
        borrower = SovereignCustodyAccount(
            account_id="ACC-BORROWER",
            participant_id="PART-BORR",
            participant_name="Tomador S.A.",
            cash_balance=Decimal("2000000.00"),
        )
        engine.register_account(lender)
        engine.register_account(borrower)

        # Depósito de 100 LFTs no doador
        engine.deposit_bond_custody("ACC-LENDER", "LFT-2029-03-01", Decimal("100.0000"))

        # Registro do contrato de empréstimo de 50 LFTs
        contract = engine.register_loan(
            contract_id="LOAN-LFT-001",
            bond_id="LFT-2029-03-01",
            lender_account_id="ACC-LENDER",
            borrower_account_id="ACC-BORROWER",
            quantity=Decimal("50.0000"),
            start_date=date(2026, 11, 4),
            end_date=date(2026, 12, 4),
            settlement_cycle=SettlementCycle.D0,
            rate_type=LendingRateType.PASSIVE,
            rental_rate_annual=Decimal("0.0200"),
            collateral_requirement_pct=Decimal("1.0000"),
        )
        # Principal = 50 * 14800 = 740.000,00
        self.assertEqual(contract.loan_principal, Decimal("740000.00"))
        self.assertEqual(contract.required_margin, Decimal("740000.00"))

        # Tentativa de iniciar sem margem -> Erro de subcolateralização
        with self.assertRaises(SovereignCollateralDeficitError):
            engine.settle_loan_start("LOAN-LFT-001")

        # Tomador aporta margem em dinheiro na Câmara B3 (100% do PU)
        engine.post_cash_margin("LOAN-LFT-001", Decimal("740000.00"))
        self.assertEqual(borrower.cash_blocked_collateral, Decimal("740000.00"))
        self.assertEqual(borrower.cash_balance, Decimal("1260000.00"))

        # Inicia empréstimo com sucesso
        engine.settle_loan_start("LOAN-LFT-001")
        self.assertEqual(contract.status, LoanStatus.ACTIVE)

        # Custódia conferida
        lender_pos = lender.get_position("LFT-2029-03-01")
        self.assertEqual(lender_pos.lent_quantity, Decimal("50.0000"))
        self.assertEqual(lender_pos.available_quantity, Decimal("50.0000"))
        self.assertEqual(lender_pos.total_quantity, Decimal("100.0000"))

        borrower_pos = borrower.get_position("LFT-2029-03-01")
        self.assertEqual(borrower_pos.available_quantity, Decimal("50.0000"))
        self.assertEqual(borrower_pos.total_quantity, Decimal("50.0000"))

        # Devolução do empréstimo
        fee_paid = engine.settle_loan_return("LOAN-LFT-001")
        self.assertEqual(contract.status, LoanStatus.RETURNED)
        self.assertGreater(fee_paid, Decimal("0.00"))

        # Custódia original restabelecida no doador
        self.assertEqual(lender_pos.available_quantity, Decimal("100.0000"))
        self.assertEqual(lender_pos.lent_quantity, Decimal("0.0000"))

        # Margem desonerada integralmente ao tomador
        self.assertEqual(borrower.cash_blocked_collateral, Decimal("0.00"))
        self.assertEqual(borrower.cash_balance, Decimal("2000000.00") - fee_paid)

        inv = engine.verify_invariants()
        self.assertTrue(inv["all_invariants_hold"])

    def test_tpf_lending_with_bond_collateral(self) -> None:
        """Valida aporte de colateral em TPF (ex: LTN dada em garantia de empréstimo de LFT)."""
        engine = SovereignTpfLendingEngine()

        ltn = SovereignBond("LTN-2027", TPFBondType.LTN, "BRSTNCLTN01", date(2024, 1, 1), date(2027, 1, 1), Decimal("850.000000"))
        lft = SovereignBond("LFT-2029", TPFBondType.LFT, "BRSTNCLFT01", date(2023, 1, 1), date(2029, 1, 1), Decimal("14000.000000"))
        engine.register_bond(ltn)
        engine.register_bond(lft)

        lender = SovereignCustodyAccount("ACC-L", "PART-L", "Doador")
        borrower = SovereignCustodyAccount("ACC-B", "PART-B", "Tomador", cash_balance=Decimal("50000.00"))
        engine.register_account(lender)
        engine.register_account(borrower)

        # LFT no doador, LTN no tomador
        engine.deposit_bond_custody("ACC-L", "LFT-2029", Decimal("10.0000"))
        engine.deposit_bond_custody("ACC-B", "LTN-2027", Decimal("200.0000"))  # 200 * 850 = 170.000

        # Empréstimo de 10 LFTs = 140.000,00
        engine.register_loan(
            "LOAN-002", "LFT-2029", "ACC-L", "ACC-B",
            quantity=Decimal("10.0000"),
            start_date=date(2026, 11, 4),
            end_date=date(2026, 11, 18),
            settlement_cycle=SettlementCycle.D1,
            rate_type=LendingRateType.ACTIVE,
            rental_rate_annual=Decimal("0.0300"),
            collateral_requirement_pct=Decimal("1.0000"),
        )

        # Tomador aporta 180 LTNs em garantia (180 * 850 = 153.000 >= 140.000)
        engine.post_bond_margin("LOAN-002", "LTN-2027", Decimal("180.0000"))
        b_pos = borrower.get_position("LTN-2027")
        self.assertEqual(b_pos.collateral_blocked_quantity, Decimal("180.0000"))
        self.assertEqual(b_pos.available_quantity, Decimal("20.0000"))

        engine.settle_loan_start("LOAN-002")
        engine.settle_loan_return("LOAN-002")

        # Verificação da desoneração do título em garantia
        self.assertEqual(b_pos.collateral_blocked_quantity, Decimal("0.0000"))
        self.assertEqual(b_pos.available_quantity, Decimal("200.0000"))

    # -----------------------------------------------------------------
    # 5. Repasse Integral de Cupons Semestrais (NTN-B e NTN-F)
    # -----------------------------------------------------------------

    def test_coupon_forward_ntnf_success(self) -> None:
        """Verifica o repasse de 100% dos juros semestrais de NTN-F do tomador ao doador."""
        engine = SovereignTpfLendingEngine()
        ntnf = SovereignBond(
            bond_id="NTN-F-2029",
            bond_type=TPFBondType.NTN_F,
            isin="BRSTNCNTF178",
            issue_date=date(2018, 1, 5),
            maturity_date=date(2029, 1, 1),
            market_unit_price=Decimal("1000.000000"),
            annual_coupon_rate=Decimal("0.1000"),
        )
        engine.register_bond(ntnf)

        lender = SovereignCustodyAccount("LEND-01", "P-1", "Doador", cash_balance=Decimal("0.00"))
        borrower = SovereignCustodyAccount("BORR-01", "P-2", "Tomador", cash_balance=Decimal("1000000.00"))
        engine.register_account(lender)
        engine.register_account(borrower)

        engine.deposit_bond_custody("LEND-01", "NTN-F-2029", Decimal("500.0000"))

        engine.register_loan(
            "LOAN-NTNF-01", "NTN-F-2029", "LEND-01", "BORR-01",
            quantity=Decimal("500.0000"),
            start_date=date(2026, 6, 1),
            end_date=date(2026, 7, 30),
            settlement_cycle=SettlementCycle.D0,
        )
        engine.post_cash_margin("LOAN-NTNF-01", Decimal("500000.00"))
        engine.settle_loan_start("LOAN-NTNF-01")

        # Cupom semestral oficial NTN-F = 48.808848
        # Total = 500 * 48.808848 = 24.404,424 -> R$ 24.404,42
        ev = engine.process_coupon_forward(
            contract_id="LOAN-NTNF-01",
            coupon_per_unit=Decimal("48.808848"),
            record_date=date(2026, 6, 30),
            payment_date=date(2026, 7, 1),
        )
        self.assertEqual(ev.total_coupon_amount, Decimal("24404.42"))
        self.assertEqual(lender.cash_balance, Decimal("24404.42"))
        self.assertEqual(borrower.cash_balance, Decimal("500000.00") - Decimal("24404.42"))

    def test_coupon_forward_rejected_for_ltn(self) -> None:
        """Garante que tentativa de repassar cupom para LTN (sem cupons) seja estritamente barrada."""
        engine = SovereignTpfLendingEngine()
        ltn = SovereignBond("LTN-2027", TPFBondType.LTN, "BRSTNCLTN01", date(2024, 1, 1), date(2027, 1, 1), Decimal("850.000000"))
        engine.register_bond(ltn)

        engine.register_account(SovereignCustodyAccount("L", "P1", "L"))
        engine.register_account(SovereignCustodyAccount("B", "P2", "B", cash_balance=Decimal("200000.00")))
        engine.deposit_bond_custody("L", "LTN-2027", Decimal("100.0000"))

        engine.register_loan("LOAN-LTN-01", "LTN-2027", "L", "B", Decimal("100.0000"), date(2026, 1, 1), date(2026, 2, 1))
        engine.post_cash_margin("LOAN-LTN-01", Decimal("85000.00"))
        engine.settle_loan_start("LOAN-LTN-01")

        with self.assertRaises(SovereignCouponForwardError):
            engine.process_coupon_forward("LOAN-LTN-01", Decimal("20.000000"), date(2026, 1, 15), date(2026, 1, 16))

    # -----------------------------------------------------------------
    # 6. Operações Compromissadas (Repos) e Lei de Paridade de Recompra (LAW-060)
    # -----------------------------------------------------------------

    def test_repo_repurchase_parity_formula(self) -> None:
        """Valida rigorosamente: Preço_Recompra == Preço_Venda * (1 + Taxa_Repo * DU/252)."""
        engine = SovereignTpfLendingEngine()
        bond = SovereignBond("LTN-2027", TPFBondType.LTN, "BRSTNCLTN01", date(2024, 1, 1), date(2027, 1, 1), Decimal("850.000000"))
        engine.register_bond(bond)

        seller = SovereignCustodyAccount("ACC-SELLER", "P-S", "Vendedor", cash_balance=Decimal("0.00"))
        buyer = SovereignCustodyAccount("ACC-BUYER", "P-B", "Comprador", cash_balance=Decimal("10000000.00"))
        engine.register_account(seller)
        engine.register_account(buyer)

        engine.deposit_bond_custody("ACC-SELLER", "LTN-2027", Decimal("1000.0000"))

        # Registro de compromissada com 21 DU a 10.50% a.a.
        # Spot = 1000 * 850 = 850.000,00
        # Recompra = round_cents(850000 * (1 + 0.105 * 21 / 252)) = 850000 * (1 + 0.00875) = 857.437,50
        repo = engine.register_repo(
            repo_id="REPO-001",
            bond_id="LTN-2027",
            seller_account_id="ACC-SELLER",
            buyer_account_id="ACC-BUYER",
            quantity=Decimal("1000.0000"),
            spot_unit_price=Decimal("850.000000"),
            repo_annual_rate=Decimal("0.1050"),
            start_date=date(2026, 11, 4),
            repurchase_date=date(2026, 12, 3),  # 21 DU
        )

        expected_spot = Decimal("850000.00")
        factor = Decimal("1") + (Decimal("0.1050") * Decimal(repo.business_days) / Decimal("252"))
        expected_repurchase = round_cents(expected_spot * factor)

        self.assertEqual(repo.spot_settlement_amount, expected_spot)
        self.assertEqual(repo.repurchase_settlement_amount, expected_repurchase)
        self.assertEqual(repo.interest_accrued, expected_repurchase - expected_spot)

        # Liquidação da Perna de Ida
        engine.settle_repo_leg1("REPO-001")
        self.assertEqual(repo.status, RepoStatus.SETTLED_LEG1)
        self.assertEqual(seller.cash_balance, expected_spot)
        self.assertEqual(seller.get_position("LTN-2027").available_quantity, Decimal("0.0000"))
        self.assertEqual(buyer.get_position("LTN-2027").available_quantity, Decimal("1000.0000"))

        # Simulação de recebimento de caixa pelo vendedor para honrar a recompra
        seller.deposit_cash(Decimal("20000.00"))

        # Liquidação da Perna de Volta
        engine.settle_repo_leg2("REPO-001")
        self.assertEqual(repo.status, RepoStatus.SETTLED_LEG2)
        self.assertEqual(seller.get_position("LTN-2027").available_quantity, Decimal("1000.0000"))
        self.assertEqual(buyer.get_position("LTN-2027").available_quantity, Decimal("0.0000"))

        inv = engine.verify_invariants()
        self.assertTrue(inv["all_invariants_hold"])

    def test_repo_parity_violation_caught(self) -> None:
        """Garante que desvio no preço de recompra levante SovereignRepoParityError."""
        with self.assertRaises(SovereignRepoParityError):
            SovereignRepoAgreement(
                repo_id="REPO-TAMPERED",
                bond_id="LTN-2027",
                seller_account_id="S",
                buyer_account_id="B",
                quantity=Decimal("100.0000"),
                spot_unit_price=Decimal("850.000000"),
                repo_annual_rate=Decimal("0.1000"),
                start_date=date(2026, 11, 4),
                repurchase_date=date(2026, 11, 5),
                business_days=1,
                spot_settlement_amount=Decimal("85000.00"),
                repurchase_settlement_amount=Decimal("99999.99"),  # Preço adulterado incorreto
            )

    # -----------------------------------------------------------------
    # 7. Auditoria de Invariantes e Detecção de Violações
    # -----------------------------------------------------------------

    def test_invariants_auditor_detects_violations(self) -> None:
        """Testa a capacidade do verify_invariants de flagrar adulterações e inconsistências."""
        engine = SovereignTpfLendingEngine.load_from_fixture(self.fixture_path)

        # Violação 1: Saldo de custódia total desbalanceado
        acc = engine.get_account("SELIC-CTA-00101")
        pos = acc.get_position("LTN-2027-01-01")
        pos.total_quantity += Decimal("500.0000")  # Adulteração não conservativa

        inv = engine.verify_invariants()
        self.assertFalse(inv["all_invariants_hold"])
        self.assertFalse(inv["sovereign_custody_conservation"])
        self.assertTrue(len(inv["violations"]) > 0)

    def test_invariants_auditor_detects_repo_parity_violation(self) -> None:
        """Auditor detecta compromissada com preço de volta fora da fórmula BACEN/B3."""
        engine = SovereignTpfLendingEngine.load_from_fixture(self.fixture_path)
        repo = engine.get_repo("REPO-LTN-ON-001")
        repo.repurchase_settlement_amount += Decimal("100.00")  # Adulteração de recompra

        inv = engine.verify_invariants()
        self.assertFalse(inv["all_invariants_hold"])
        self.assertFalse(inv["repo_repurchase_parity"])

    def test_invariants_auditor_detects_margin_deficit(self) -> None:
        """Auditor detecta empréstimo ativo com garantia inferior a 100% de PU."""
        engine = SovereignTpfLendingEngine.load_from_fixture(self.fixture_path)
        loan = engine.get_loan("LOAN-LTN-2026-001")
        loan.margin_posted = Decimal("500.00")  # Subcolateralização severa

        inv = engine.verify_invariants()
        self.assertFalse(inv["all_invariants_hold"])
        self.assertFalse(inv["multilateral_margin_coverage"])

    def test_coupon_forward_on_ntnb_with_vna(self) -> None:
        """Verifica o repasse de 100% do cupom de NTN-B ao doador."""
        engine = SovereignTpfLendingEngine()
        ntnb = SovereignBond(
            "NTN-B-2035", TPFBondType.NTN_B, "BRSTNCNTB431",
            date(2020, 5, 15), date(2035, 5, 15), Decimal("4200.000000"),
            annual_coupon_rate=Decimal("0.0600")
        )
        engine.register_bond(ntnb)

        lender = SovereignCustodyAccount("L-NTNB", "P-L", "Lender", cash_balance=Decimal("0.00"))
        borrower = SovereignCustodyAccount("B-NTNB", "P-B", "Borrower", cash_balance=Decimal("5000000.00"))
        engine.register_account(lender)
        engine.register_account(borrower)

        engine.deposit_bond_custody("L-NTNB", "NTN-B-2035", Decimal("200.0000"))
        loan = engine.register_loan(
            "LOAN-NTNB-01", "NTN-B-2035", "L-NTNB", "B-NTNB",
            quantity=Decimal("200.0000"),
            start_date=date(2026, 5, 1),
            end_date=date(2026, 6, 1),
            settlement_cycle=SettlementCycle.D0,
        )
        # 200 * 4200 = 840.000,00
        engine.post_cash_margin("LOAN-NTNB-01", Decimal("840000.00"))
        engine.settle_loan_start("LOAN-NTNB-01")

        # Cupom unitário IPCA + 6% semestral calculado com base em VNA = 4200
        coupon_unit = ntnb.calculate_semiannual_coupon_pu(reference_nominal_value=Decimal("4200.000000"))
        ev = engine.process_coupon_forward("LOAN-NTNB-01", coupon_unit, date(2026, 5, 15), date(2026, 5, 15))

        expected_total = round_cents(Decimal("200.0000") * coupon_unit)
        self.assertEqual(ev.total_coupon_amount, expected_total)
        self.assertEqual(lender.cash_balance, expected_total)

    def test_coupon_forward_borrower_insolvency_defaults(self) -> None:
        """Garante que tomador sem saldo para repassar cupom entre em DEFAULTED."""
        engine = SovereignTpfLendingEngine()
        ntnf = SovereignBond("NTN-F-2029", TPFBondType.NTN_F, "ISIN-F", date(2020, 1, 1), date(2029, 1, 1), Decimal("1000.00"))
        engine.register_bond(ntnf)

        lender = SovereignCustodyAccount("L1", "P1", "L")
        # Tomador com apenas 100 reais em caixa
        borrower = SovereignCustodyAccount("B1", "P2", "B", cash_balance=Decimal("100.00"))
        engine.register_account(lender)
        engine.register_account(borrower)

        engine.deposit_bond_custody("L1", "NTN-F-2029", Decimal("100.0000"))
        engine.deposit_cash("B1", Decimal("100000.00"))
        engine.register_loan("LOAN-DEF", "NTN-F-2029", "L1", "B1", Decimal("100.0000"), date(2026, 1, 1), date(2026, 2, 1))
        engine.post_cash_margin("LOAN-DEF", Decimal("100000.00"))
        engine.settle_loan_start("LOAN-DEF")

        # Drena o caixa do tomador antes do cupom
        borrower.cash_balance = Decimal("10.00")

        with self.assertRaises(SovereignCouponForwardError):
            engine.process_coupon_forward("LOAN-DEF", Decimal("48.808848"), date(2026, 1, 15), date(2026, 1, 15))

        loan = engine.get_loan("LOAN-DEF")
        self.assertEqual(loan.status, LoanStatus.DEFAULTED)

    def test_repo_settlement_errors_and_defaults(self) -> None:
        """Testa falhas de liquidação de compromissada por saldo insuficiente."""
        engine = SovereignTpfLendingEngine()
        ltn = SovereignBond("LTN-2027", TPFBondType.LTN, "BRSTNCLTN01", date(2024, 1, 1), date(2027, 1, 1), Decimal("900.000000"))
        engine.register_bond(ltn)

        seller = SovereignCustodyAccount("S-ACC", "P-S", "Seller", cash_balance=Decimal("0.00"))
        buyer = SovereignCustodyAccount("B-ACC", "P-B", "Buyer", cash_balance=Decimal("1000.00"))  # Sem caixa suficiente
        engine.register_account(seller)
        engine.register_account(buyer)

        engine.deposit_bond_custody("S-ACC", "LTN-2027", Decimal("100.0000"))

        repo = engine.register_repo(
            "REPO-FAIL-01", "LTN-2027", "S-ACC", "B-ACC",
            quantity=Decimal("100.0000"),
            spot_unit_price=Decimal("900.000000"),
            repo_annual_rate=Decimal("0.1000"),
            start_date=date(2026, 11, 4),
            repurchase_date=date(2026, 11, 5),
        )
        # Tentativa de liquidação da ida com comprador sem caixa (Preço_Venda = 90.000,00)
        with self.assertRaises(SovereignTpfValidationError):
            engine.settle_repo_leg1("REPO-FAIL-01")

        # Comprador recebe caixa suficiente
        buyer.deposit_cash(Decimal("100000.00"))
        engine.settle_repo_leg1("REPO-FAIL-01")

        # Agora na perna de volta, se o vendedor não tiver caixa para recomprar
        with self.assertRaises(SovereignTpfValidationError):
            engine.settle_repo_leg2("REPO-FAIL-01")
        self.assertEqual(repo.status, RepoStatus.DEFAULTED)

    def test_business_days_helper(self) -> None:
        """Valida a contagem de dias úteis com exclusão de finais de semana e feriados nacionais."""
        # 04/11/2026 (Quarta) até 06/11/2026 (Sexta) -> 2 dias úteis
        d1 = date(2026, 11, 4)
        d2 = date(2026, 11, 6)
        self.assertEqual(count_business_days(d1, d2), 2)

        # Fim de semana não conta
        d_fri = date(2026, 11, 6)
        d_mon = date(2026, 11, 9)
        self.assertEqual(count_business_days(d_fri, d_mon), 1)

        # Feriado nacional de 15/11 (Proclamação da República)
        self.assertFalse(is_business_day(date(2026, 11, 15)))

    def test_update_bond_pu_and_validation(self) -> None:
        """Testa atualização de cotação a mercado (Mark-to-Market)."""
        engine = SovereignTpfLendingEngine()
        ltn = SovereignBond("LTN-2027", TPFBondType.LTN, "BRSTNCLTN01", date(2024, 1, 1), date(2027, 1, 1), Decimal("850.000000"))
        engine.register_bond(ltn)

        engine.update_bond_market_price("LTN-2027", Decimal("865.500000"))
        self.assertEqual(engine.get_bond("LTN-2027").market_unit_price, Decimal("865.500000"))

        with self.assertRaises(SovereignTpfValidationError):
            engine.update_bond_market_price("LTN-2027", Decimal("-1.00"))


if __name__ == "__main__":
    unittest.main()
