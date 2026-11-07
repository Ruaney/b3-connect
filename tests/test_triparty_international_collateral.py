"""Suíte de Testes Unitários — Custódia Internacional e Triparty Repo B3 (FL-099).

Testa exaustivamente todas as funcionalidades de gestão de garantias transfronteiriças,
matriz de haircuts por rating/tenor, buffers cambiais, mensageria SWIFT MT540/MT542,
trava Safe-Release guardrail, liquidação bilateral soma-zero e auditoria formal
das leis de invariantes LAW-105A, LAW-105B e LAW-105C.

100% Python Standard Library (unittest, decimal, json, pathlib).
"""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.triparty_international_collateral import (
    AccountCategory,
    CollateralAllocation,
    CustodyAccount,
    CustodyConservationError,
    CustodyPosition,
    IneligibleCollateralError,
    InsufficientCollateralError,
    InternationalAssetType,
    InternationalCustodian,
    MarginReleaseViolationError,
    PtaxRate,
    RepoStatus,
    SovereignRating,
    SwiftMessage,
    SwiftMessageStatus,
    SwiftMessageType,
    SwiftMessagingError,
    TripartyCollateralError,
    TripartyInternationalCollateralEngine,
    TripartyRepoContract,
    round_cents,
    round_quantity,
    round_rate,
)


class TestTripartyInternationalCollateral(unittest.TestCase):
    """Testes unitários para o motor de Custódia Internacional e Triparty Repo (FL-099)."""

    def setUp(self) -> None:
        self.engine = TripartyInternationalCollateralEngine()

        # Configura taxas PTAX BACEN
        self.engine.set_ptax_rate(
            PtaxRate(currency="USD", ptax_bid=Decimal("5.450000"), ptax_ask=Decimal("5.452000"), rate_date="2026-11-06")
        )
        self.engine.set_ptax_rate(
            PtaxRate(currency="EUR", ptax_bid=Decimal("5.900000"), ptax_ask=Decimal("5.903000"), rate_date="2026-11-06")
        )
        self.engine.set_ptax_rate(
            PtaxRate(currency="GBP", ptax_bid=Decimal("7.100000"), ptax_ask=Decimal("7.105000"), rate_date="2026-11-06")
        )

        # Cadastra contas de custódia
        self.house_acc = CustodyAccount(
            account_id="EC-HOUSE-001",
            custodian=InternationalCustodian.EUROCLEAR,
            category=AccountCategory.HOUSE,
            participant_id="BANK-ALPHA",
            is_fiduciary_segregated=True,
        )
        self.client_acc = CustodyAccount(
            account_id="EC-CLIENT-001",
            custodian=InternationalCustodian.EUROCLEAR,
            category=AccountCategory.CLIENT,
            participant_id="BANK-ALPHA",
            is_fiduciary_segregated=True,
        )
        self.dtc_acc = CustodyAccount(
            account_id="DTC-HOUSE-001",
            custodian=InternationalCustodian.DTC,
            category=AccountCategory.HOUSE,
            participant_id="BANK-ALPHA",
            is_fiduciary_segregated=True,
        )

        self.engine.register_account(self.house_acc)
        self.engine.register_account(self.client_acc)
        self.engine.register_account(self.dtc_acc)

        # Depósito de posições
        self.engine.deposit_collateral(
            account_id="EC-HOUSE-001",
            isin="DE0001102580",
            symbol="BUND-10Y",
            currency="EUR",
            quantity=Decimal("10000.0000"),
        )
        self.engine.deposit_collateral(
            account_id="DTC-HOUSE-001",
            isin="US912810TL44",
            symbol="UST-10Y",
            currency="USD",
            quantity=Decimal("20000.0000"),
        )

    def test_ptax_rate_validation(self) -> None:
        """Testa validação de cotações PTAX."""
        # Bid <= 0 inválido
        with self.assertRaises(ValueError):
            PtaxRate(currency="USD", ptax_bid=Decimal("0.000000"), ptax_ask=Decimal("5.500000"), rate_date="2026-11-06")

        # Bid > Ask inválido
        with self.assertRaises(ValueError):
            PtaxRate(currency="USD", ptax_bid=Decimal("5.600000"), ptax_ask=Decimal("5.500000"), rate_date="2026-11-06")

        # Moeda inexistente
        with self.assertRaises(ValueError):
            self.engine.get_ptax_rate("JPY")

    def test_haircut_matrix_and_fx_buffer(self) -> None:
        """Testa escalonamento de haircuts por maturidade, rating e buffer cambial."""
        # Tenor <= 1 ano AAA: 2% base + 8% USD buffer = 10%
        base, fx, total = self.engine.calculate_total_haircut(
            SovereignRating.AAA, Decimal("0.8"), "USD"
        )
        self.assertEqual(base, Decimal("0.020000"))
        self.assertEqual(fx, Decimal("0.080000"))
        self.assertEqual(total, Decimal("0.100000"))

        # Tenor > 10 anos BBB-: 10% base + 8% spread + 10% EUR buffer = 28%
        base_bbb, fx_eur, total_bbb = self.engine.calculate_total_haircut(
            SovereignRating.BBB_MINUS, Decimal("15.0"), "EUR"
        )
        self.assertEqual(base_bbb, Decimal("0.180000"))
        self.assertEqual(fx_eur, Decimal("0.100000"))
        self.assertEqual(total_bbb, Decimal("0.280000"))

        # Sub-investment grade deve ser rejeitado imediatamente
        with self.assertRaises(IneligibleCollateralError):
            self.engine.calculate_total_haircut(
                SovereignRating.SUB_INVESTMENT, Decimal("5.0"), "USD"
            )

    def test_custody_account_deposit_and_conservation(self) -> None:
        """Testa conservação física de custódia na conta."""
        acc = self.engine.accounts["EC-HOUSE-001"]
        pos = acc.positions["DE0001102580"]

        self.assertEqual(pos.total_quantity, Decimal("10000.0000"))
        self.assertEqual(pos.available_quantity, Decimal("10000.0000"))
        self.assertEqual(pos.locked_quantity, Decimal("0.0000"))

        # Depósito adicional
        self.engine.deposit_collateral(
            account_id="EC-HOUSE-001",
            isin="DE0001102580",
            symbol="BUND-10Y",
            currency="EUR",
            quantity=Decimal("5000.0000"),
        )
        self.assertEqual(pos.total_quantity, Decimal("15000.0000"))
        self.assertEqual(pos.available_quantity, Decimal("15000.0000"))

        # Depósito inválido (<= 0)
        with self.assertRaises(ValueError):
            self.engine.deposit_collateral("EC-HOUSE-001", "DE0001102580", "BUND-10Y", "EUR", Decimal("0.0000"))

    def test_triparty_repo_lifecycle_and_swift_mt540(self) -> None:
        """Testa criação de repo, alocação de colateral e geração de MT540 SWIFT."""
        repo = TripartyRepoContract(
            repo_id="REPO-TEST-001",
            borrower_participant_id="BANK-ALPHA",
            lender_participant_id="B3_CLEARING_HOUSE",
            triparty_custodian=InternationalCustodian.EUROCLEAR,
            nominal_cash_brl=Decimal("10000000.00"),
            repo_rate_annual=Decimal("0.130000"),
            start_date="2026-11-06",
            end_date="2026-11-20",
            business_days=10,
            required_margin_brl=Decimal("11000000.00"),
        )
        self.engine.create_repo_contract(repo)

        # Aloca 3.000 títulos BUND (preço 100 EUR, PTAX bid 5.90, Haircut 17.5%)
        # Gross = 3000 * 100 * 5.90 = 1.770.000 BRL
        # Eligible = 1.770.000 * (1 - 0.175) = 1.460.250 BRL
        alloc1 = self.engine.allocate_collateral(
            repo_id="REPO-TEST-001",
            account_id="EC-HOUSE-001",
            isin="DE0001102580",
            quantity=Decimal("3000.0000"),
            unit_price_foreign=Decimal("100.000000"),
            rating=SovereignRating.AAA,
            tenor_years=Decimal("6.0"),
        )

        self.assertEqual(alloc1.gross_value_brl, Decimal("1770000.00"))
        self.assertEqual(alloc1.eligible_value_brl, Decimal("1460250.00"))

        # Custódia da conta deve estar conservada e bloqueada
        pos = self.engine.accounts["EC-HOUSE-001"].positions["DE0001102580"]
        self.assertEqual(pos.available_quantity, Decimal("7000.0000"))
        self.assertEqual(pos.locked_quantity, Decimal("3000.0000"))

        # Verifica mensagem SWIFT MT540 emitida
        self.assertEqual(len(self.engine.swift_messages), 1)
        swift_msg = self.engine.swift_messages[0]
        self.assertEqual(swift_msg.message_type, SwiftMessageType.MT540)
        self.assertEqual(swift_msg.sender_bic, "EACLBBEB")
        self.assertEqual(swift_msg.isin, "DE0001102580")
        self.assertIn("INCOMUNICABILIDADE PATRIMONIAL", swift_msg.raw_mt_text)

        # Tentar travar repo sem colateral suficiente deve lançar InsufficientCollateralError
        with self.assertRaises(InsufficientCollateralError):
            self.engine.lock_repo("REPO-TEST-001")

        # Aloca colateral adicional em USD via DTC: 18.000 UST (preço 100 USD, PTAX 5.45, Haircut 16.5%)
        # Gross = 18000 * 100 * 5.45 = 9.810.000 BRL
        # Eligible = 9.810.000 * (1 - 0.165) = 8.191.350 BRL
        # Total colateral = 1.460.250 + 8.191.350 = 9.651.600 BRL (ainda < 11.000.000)
        # Vamos alocar quantidade que cubra: 22.000 títulos UST (precisamos depositar mais 5.000)
        self.engine.deposit_collateral("DTC-HOUSE-001", "US912810TL44", "UST-10Y", "USD", Decimal("5000.0000"))
        alloc2 = self.engine.allocate_collateral(
            repo_id="REPO-TEST-001",
            account_id="DTC-HOUSE-001",
            isin="US912810TL44",
            quantity=Decimal("22000.0000"),
            unit_price_foreign=Decimal("100.000000"),
            rating=SovereignRating.AA_PLUS,
            tenor_years=Decimal("8.0"),
        )
        # Eligible UST = 22000 * 100 * 5.45 * 0.835 = 10.011.650 BRL
        # Total = 1.460.250 + 10.011.650 = 11.471.900 BRL >= 11.000.000 BRL
        self.assertGreaterEqual(repo.total_eligible_collateral_brl(), repo.required_margin_brl)

        # Agora a trava é aceita com sucesso
        self.engine.lock_repo("REPO-TEST-001")
        self.assertEqual(repo.status, RepoStatus.LOCKED_FIDUCIARY)

    def test_safe_release_guardrail_prevents_margin_deficit(self) -> None:
        """Testa se o Safe-Release Guardrail impede liberação que resulte em déficit de margem."""
        repo = TripartyRepoContract(
            repo_id="REPO-GUARDRAIL-001",
            borrower_participant_id="BANK-ALPHA",
            lender_participant_id="B3_CLEARING_HOUSE",
            triparty_custodian=InternationalCustodian.EUROCLEAR,
            nominal_cash_brl=Decimal("1000000.00"),
            repo_rate_annual=Decimal("0.130000"),
            start_date="2026-11-06",
            end_date="2026-11-20",
            business_days=10,
            required_margin_brl=Decimal("1000000.00"),
        )
        self.engine.create_repo_contract(repo)

        # Aloca 3000 BUND -> 1.460.250 BRL elegível (margem requerida 1.000.000 BRL)
        alloc = self.engine.allocate_collateral(
            repo_id="REPO-GUARDRAIL-001",
            account_id="EC-HOUSE-001",
            isin="DE0001102580",
            quantity=Decimal("3000.0000"),
            unit_price_foreign=Decimal("100.000000"),
            rating=SovereignRating.AAA,
            tenor_years=Decimal("5.0"),
        )
        self.engine.lock_repo("REPO-GUARDRAIL-001")

        # Tentativa de liberar 2.000 títulos deixaria apenas 1000 títulos (~486k BRL) < 1.000.000 margem
        with self.assertRaises(MarginReleaseViolationError):
            self.engine.request_safe_release(
                repo_id="REPO-GUARDRAIL-001",
                allocation_id=alloc.allocation_id,
                quantity_to_release=Decimal("2000.0000"),
            )

        # Liberação prudente de 500 títulos (remanescente ~1.216.875 BRL >= 1.000.000) deve passar
        msg = self.engine.request_safe_release(
            repo_id="REPO-GUARDRAIL-001",
            allocation_id=alloc.allocation_id,
            quantity_to_release=Decimal("500.0000"),
        )
        self.assertEqual(msg.message_type, SwiftMessageType.MT542)
        self.assertEqual(alloc.quantity, Decimal("2500.0000"))

        # Custódia liberada na conta
        pos = self.engine.accounts["EC-HOUSE-001"].positions["DE0001102580"]
        self.assertEqual(pos.available_quantity, Decimal("7500.0000"))
        self.assertEqual(pos.locked_quantity, Decimal("2500.0000"))

    def test_regular_dvp_settlement_zero_sum(self) -> None:
        """Testa liquidação regular DvP com recompra financeira e liberação total soma-zero (LAW-105C)."""
        repo = TripartyRepoContract(
            repo_id="REPO-SETTLE-001",
            borrower_participant_id="BANK-ALPHA",
            lender_participant_id="B3_CLEARING_HOUSE",
            triparty_custodian=InternationalCustodian.EUROCLEAR,
            nominal_cash_brl=Decimal("1000000.00"),
            repo_rate_annual=Decimal("0.125000"),
            start_date="2026-11-01",
            end_date="2026-11-15",
            business_days=10,
            required_margin_brl=Decimal("1000000.00"),
        )
        self.engine.create_repo_contract(repo)

        alloc = self.engine.allocate_collateral(
            repo_id="REPO-SETTLE-001",
            account_id="EC-HOUSE-001",
            isin="DE0001102580",
            quantity=Decimal("3000.0000"),
            unit_price_foreign=Decimal("100.000000"),
            rating=SovereignRating.AAA,
            tenor_years=Decimal("5.0"),
        )
        self.engine.lock_repo("REPO-SETTLE-001")

        # Liquida operação
        record = self.engine.settle_repo("REPO-SETTLE-001")

        self.assertEqual(repo.status, RepoStatus.SETTLED)
        self.assertEqual(record["borrower_paid_brl"], record["lender_received_brl"])
        self.assertEqual(record["net_financial_delta_brl"], "0.00")
        self.assertEqual(record["collateral_released_qty"]["DE0001102580"], "3000.0000")

        # Custódia totalmente restaurada como disponível
        pos = self.engine.accounts["EC-HOUSE-001"].positions["DE0001102580"]
        self.assertEqual(pos.locked_quantity, Decimal("0.0000"))
        self.assertEqual(pos.available_quantity, Decimal("10000.0000"))

    def test_default_execution_waterfall_zero_sum(self) -> None:
        """Testa excussão fiduciária internacional em caso de default com conservação soma-zero (LAW-105C)."""
        repo = TripartyRepoContract(
            repo_id="REPO-DEFAULT-001",
            borrower_participant_id="BANK-ALPHA",
            lender_participant_id="B3_CLEARING_HOUSE",
            triparty_custodian=InternationalCustodian.EUROCLEAR,
            nominal_cash_brl=Decimal("2000000.00"),
            repo_rate_annual=Decimal("0.120000"),
            start_date="2026-11-01",
            end_date="2026-11-20",
            business_days=14,
            required_margin_brl=Decimal("2000000.00"),
        )
        self.engine.create_repo_contract(repo)

        self.engine.allocate_collateral(
            repo_id="REPO-DEFAULT-001",
            account_id="EC-HOUSE-001",
            isin="DE0001102580",
            quantity=Decimal("5000.0000"),
            unit_price_foreign=Decimal("100.000000"),
            rating=SovereignRating.AAA,
            tenor_years=Decimal("5.0"),
        )
        self.engine.lock_repo("REPO-DEFAULT-001")

        # Leilão internacional gera 520.000 EUR
        # Convertido a PTAX bid 5.90 -> 3.068.000 BRL
        # Dívida é aproximadamente 2.012.871,83 BRL
        auction_proceeds = {"DE0001102580": Decimal("520000.00")}
        record = self.engine.execute_default("REPO-DEFAULT-001", auction_proceeds)

        self.assertEqual(repo.status, RepoStatus.DEFAULT_EXECUTED)
        paid = Decimal(record["borrower_paid_brl"])
        lender_recv = Decimal(record["lender_received_brl"])
        surplus = Decimal(record["surplus_returned_to_borrower_brl"])

        # Soma-zero rigorosa do produto do leilão
        self.assertEqual(paid, lender_recv + surplus)
        self.assertEqual(record["net_financial_delta_brl"], "0.00")

    def test_formal_invariants_verification(self) -> None:
        """Testa verificação formal das três invariantes LAW-105A, LAW-105B e LAW-105C."""
        audit = self.engine.verify_invariants()
        self.assertTrue(audit["all_invariants_hold"])
        self.assertTrue(audit["law_105a_collateral_haircut_coverage_verified"])
        self.assertTrue(audit["law_105b_international_lock_inviolability_verified"])
        self.assertTrue(audit["law_105c_settlement_zero_sum_verified"])

    def test_fixture_loading_and_roundtrip(self) -> None:
        """Testa integridade e invariantes da fixture oficial triparty_international_collateral.json."""
        fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "triparty_international_collateral.json"
        self.assertTrue(fixture_path.exists(), f"Fixture não encontrada em {fixture_path}")

        with open(fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        engine = TripartyInternationalCollateralEngine.from_dict(data)
        audit = engine.verify_invariants()

        self.assertTrue(audit["all_invariants_hold"])
        self.assertTrue(audit["law_105a_collateral_haircut_coverage_verified"])
        self.assertTrue(audit["law_105b_international_lock_inviolability_verified"])
        self.assertTrue(audit["law_105c_settlement_zero_sum_verified"])

        # Roundtrip to_dict -> from_dict
        serialized = engine.to_dict()
        engine_roundtrip = TripartyInternationalCollateralEngine.from_dict(serialized)
        audit_roundtrip = engine_roundtrip.verify_invariants()
        self.assertTrue(audit_roundtrip["all_invariants_hold"])


if __name__ == "__main__":
    unittest.main()
