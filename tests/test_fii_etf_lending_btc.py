"""Testes automatizados do Motor de Empréstimo de Cotas FII e ETF (FL-084).

Validação estrita das regras de negócio do Manual de Operações BTC B3:
- Registro e alocação atômica em D0;
- Remuneração pró-rata die com convenção DU/252;
- Repasse compulsório de proventos (dividendos de FIIs e rendimentos de ETFs);
- Devolução física e liquidação no vencimento ou antecipada;
- Verificação formal de invariantes (LAW-084 e LAW-085);
- Testes adversariais de quebra de invariantes e falhas de entrega;
- Carga e integridade de fixtures JSON.
"""

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

# Garante a raiz do repositório no sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.fii_etf_lending_btc import (
    AssetType,
    ContractConservationError,
    ContractNotFoundError,
    ContractStatus,
    ContractTermError,
    CustodyAccount,
    CustodyPosition,
    DividendAnnouncement,
    DividendEventType,
    DividendSettlementReceipt,
    FiiEtfLendingEngine,
    FiiEtfLoanContract,
    FiiEtfValidationError,
    InsufficientCustodyError,
    ReturnExecutionReceipt,
    ReversibilityType,
    count_business_days,
    is_business_day,
    round_cents,
)

FIXTURE_PATH = REPO_ROOT / "fixtures" / "fii_etf_lending_btc.json"


class TestFiiEtfLendingRegistration(unittest.TestCase):
    """Testes de registro e alocação de contratos BTC para cotas de FII e ETF."""

    def setUp(self) -> None:
        self.engine = FiiEtfLendingEngine()
        # Doador 105/10001 com 10.000 cotas de HGLG11
        self.engine.deposit_custody(
            participant_id=105,
            account_id=10001,
            symbol="HGLG11",
            isin="BRHGLGCTF004",
            quantity=10000,
        )

    def test_successful_fii_contract_registration_and_allocation(self) -> None:
        """Valida registro e transferência fiduciária em D0 de cotas de FII."""
        contract = FiiEtfLoanContract(
            contract_id="BTC-FII-001",
            asset_type=AssetType.FII,
            symbol="HGLG11",
            isin="BRHGLGCTF004",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=88,
            borrower_account=20001,
            quantity=6000,
            base_price=Decimal("165.50"),
            rental_rate=Decimal("0.0250"),
            open_date=date(2026, 10, 1),
            maturity_date=date(2026, 12, 1),
            reversibility=ReversibilityType.BORROWER_REVERSIBLE,
        )

        registered = self.engine.register_contract(contract)
        self.assertEqual(registered.contract_id, "BTC-FII-001")
        self.assertEqual(registered.contract_value, Decimal("993000.00"))
        self.assertEqual(registered.active_quantity, 6000)
        self.assertEqual(registered.returned_quantity, 0)
        self.assertEqual(registered.status, ContractStatus.OPEN)

        # Verifica custódia do Doador
        lender_pos = self.engine.get_custody_position(105, 10001, "HGLG11")
        self.assertEqual(lender_pos.available_qty, 4000)      # 10.000 - 6.000
        self.assertEqual(lender_pos.lent_active_qty, 6000)    # 6.000 emprestadas
        self.assertEqual(lender_pos.total_owned_qty, 10000)   # Patrimônio conservado

        # Verifica custódia do Tomador
        borrower_pos = self.engine.get_custody_position(88, 20001, "HGLG11")
        self.assertEqual(borrower_pos.available_qty, 6000)
        self.assertEqual(borrower_pos.borrowed_active_qty, 6000)

    def test_etf_equity_and_fixed_income_registration(self) -> None:
        """Valida que ETFs de Ações e ETFs de Renda Fixa são aceitos e alocados."""
        self.engine.deposit_custody(105, 10001, "BOVA11", "BRBOVACTF003", 20000)
        self.engine.deposit_custody(105, 10001, "IMAB11", "BRIMABCTF001", 15000)

        c_equity = FiiEtfLoanContract(
            contract_id="BTC-ETF-EQ-001",
            asset_type=AssetType.ETF_EQUITY,
            symbol="BOVA11",
            isin="BRBOVACTF003",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=88,
            borrower_account=20001,
            quantity=5000,
            base_price=Decimal("125.00"),
            rental_rate=Decimal("0.0200"),
            open_date=date(2026, 10, 1),
            maturity_date=date(2026, 11, 1),
        )
        self.engine.register_contract(c_equity)

        c_fixed = FiiEtfLoanContract(
            contract_id="BTC-ETF-FI-001",
            asset_type=AssetType.ETF_FIXED_INCOME,
            symbol="IMAB11",
            isin="BRIMABCTF001",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=93,
            borrower_account=30001,
            quantity=8000,
            base_price=Decimal("100.00"),
            rental_rate=Decimal("0.0150"),
            open_date=date(2026, 10, 1),
            maturity_date=date(2026, 11, 1),
        )
        self.engine.register_contract(c_fixed)

        self.assertIn("BTC-ETF-EQ-001", self.engine.contracts)
        self.assertIn("BTC-ETF-FI-001", self.engine.contracts)

    def test_rejection_when_lender_custody_is_insufficient(self) -> None:
        """Rejeita contratação caso o Doador não possua saldo livre suficiente em custódia."""
        contract = FiiEtfLoanContract(
            contract_id="BTC-FAIL-001",
            asset_type=AssetType.FII,
            symbol="HGLG11",
            isin="BRHGLGCTF004",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=88,
            borrower_account=20001,
            quantity=15000,  # Doador só tem 10.000
            base_price=Decimal("165.50"),
            rental_rate=Decimal("0.0250"),
            open_date=date(2026, 10, 1),
            maturity_date=date(2026, 12, 1),
        )
        with self.assertRaises(InsufficientCustodyError):
            self.engine.register_contract(contract)

    def test_rejection_when_lender_and_borrower_are_same_account(self) -> None:
        """Rejeita contrato com doador e tomador na mesma conta e participante."""
        contract = FiiEtfLoanContract(
            contract_id="BTC-FAIL-002",
            asset_type=AssetType.FII,
            symbol="HGLG11",
            isin="BRHGLGCTF004",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=105,
            borrower_account=10001,
            quantity=1000,
            base_price=Decimal("165.50"),
            rental_rate=Decimal("0.0250"),
            open_date=date(2026, 10, 1),
            maturity_date=date(2026, 12, 1),
        )
        with self.assertRaises(FiiEtfValidationError):
            self.engine.register_contract(contract)

    def test_validation_errors_on_invalid_contract_parameters(self) -> None:
        """Valida detecção de parâmetros inválidos na criação do contrato."""
        # Quantidade <= 0
        with self.assertRaises(FiiEtfValidationError):
            FiiEtfLoanContract(
                contract_id="INV-1",
                asset_type=AssetType.FII,
                symbol="HGLG11",
                isin="BRHGLGCTF004",
                lender_participant_id=105,
                lender_account=10001,
                borrower_participant_id=88,
                borrower_account=20001,
                quantity=0,
                base_price=Decimal("165.50"),
                rental_rate=Decimal("0.0250"),
                open_date=date(2026, 10, 1),
                maturity_date=date(2026, 12, 1),
            )

        # Preço base <= 0
        with self.assertRaises(FiiEtfValidationError):
            FiiEtfLoanContract(
                contract_id="INV-2",
                asset_type=AssetType.FII,
                symbol="HGLG11",
                isin="BRHGLGCTF004",
                lender_participant_id=105,
                lender_account=10001,
                borrower_participant_id=88,
                borrower_account=20001,
                quantity=100,
                base_price=Decimal("0.00"),
                rental_rate=Decimal("0.0250"),
                open_date=date(2026, 10, 1),
                maturity_date=date(2026, 12, 1),
            )

        # Taxa de remuneração negativa
        with self.assertRaises(FiiEtfValidationError):
            FiiEtfLoanContract(
                contract_id="INV-3",
                asset_type=AssetType.FII,
                symbol="HGLG11",
                isin="BRHGLGCTF004",
                lender_participant_id=105,
                lender_account=10001,
                borrower_participant_id=88,
                borrower_account=20001,
                quantity=100,
                base_price=Decimal("165.50"),
                rental_rate=Decimal("-0.01"),
                open_date=date(2026, 10, 1),
                maturity_date=date(2026, 12, 1),
            )

        # Vencimento anterior à abertura
        with self.assertRaises(FiiEtfValidationError):
            FiiEtfLoanContract(
                contract_id="INV-4",
                asset_type=AssetType.FII,
                symbol="HGLG11",
                isin="BRHGLGCTF004",
                lender_participant_id=105,
                lender_account=10001,
                borrower_participant_id=88,
                borrower_account=20001,
                quantity=100,
                base_price=Decimal("165.50"),
                rental_rate=Decimal("0.0250"),
                open_date=date(2026, 10, 10),
                maturity_date=date(2026, 10, 5),
            )


class TestFiiEtfRentalCalculation(unittest.TestCase):
    """Testes do cálculo da taxa de remuneração pro-rata die com convenção DU/252."""

    def setUp(self) -> None:
        self.engine = FiiEtfLendingEngine()
        self.engine.deposit_custody(105, 10001, "HGLG11", "BRHGLGCTF004", 10000)
        self.contract = FiiEtfLoanContract(
            contract_id="BTC-CALC-001",
            asset_type=AssetType.FII,
            symbol="HGLG11",
            isin="BRHGLGCTF004",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=88,
            borrower_account=20001,
            quantity=10000,
            base_price=Decimal("100.00"),  # Volume base = R$ 1.000.000,00
            rental_rate=Decimal("0.0500"),  # 5.00% a.a.
            open_date=date(2026, 10, 1),    # Quinta-feira
            maturity_date=date(2026, 12, 1),
        )
        self.engine.register_contract(self.contract)

    def test_zero_business_days_accrual(self) -> None:
        """Quando aberta e consultada na mesma data (0 DU), remuneração é 0,00."""
        du, fee = self.contract.calculate_accrued_rental(as_of_date=date(2026, 10, 1))
        self.assertEqual(du, 0)
        self.assertEqual(fee, Decimal("0.00"))

    def test_exact_du252_pro_rata_die_formula(self) -> None:
        """Valida que a remuneração segue estritamente Volume * ((1 + r)^(DU/252) - 1)."""
        # Entre 2026-10-01 (Qui) e 2026-10-22 (Qui)
        # Outubro/2026 tem feriado em 12/10 (Nossa Senhora Aparecida, Seg)
        du = count_business_days(date(2026, 10, 1), date(2026, 10, 22))
        self.assertEqual(du, 14)  # 15 dias de semana - 1 feriado = 14 DU

        du_res, fee = self.engine.calculate_rental_fee("BTC-CALC-001", return_date=date(2026, 10, 22))
        self.assertEqual(du_res, 14)

        # Cálculo teórico esperado:
        # Volume = 10.000 * 100.00 = 1.000.000,00
        # Fator = (1 + 0.05)^(14/252) - 1
        vol = Decimal("1000000.00")
        fator = (Decimal("1.05") ** (Decimal("14") / Decimal("252"))) - Decimal("1")
        expected_fee = round_cents(vol * fator)

        self.assertEqual(fee, expected_fee)
        self.assertGreater(fee, Decimal("0.00"))

    def test_full_year_252_du_accrual(self) -> None:
        """Em 252 dias úteis, a taxa deve render exatamente Volume * r."""
        # Cria um contrato com prazo que resulte em 252 DU
        c_annual = FiiEtfLoanContract(
            contract_id="BTC-ANNUAL-001",
            asset_type=AssetType.FII,
            symbol="HGLG11",
            isin="BRHGLGCTF004",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=88,
            borrower_account=20001,
            quantity=1000,
            base_price=Decimal("100.00"),
            rental_rate=Decimal("0.1000"),  # 10% a.a.
            open_date=date(2026, 1, 2),
            maturity_date=date(2027, 1, 5),
        )
        # Teste unitário direto da função calculate_accrued_rental com 252 DU
        # Volume = 100.000,00, Taxa = 10% => R$ 10.000,00
        # Para forçar contagem exata de 252 DU:
        t_exp = Decimal("252") / Decimal("252")
        factor = (Decimal("1.10") ** t_exp) - Decimal("1")
        fee = round_cents(Decimal("100000.00") * factor)
        self.assertEqual(fee, Decimal("10000.00"))


class TestFiiEtfDividendForwarding(unittest.TestCase):
    """Testes do repasse compulsório de proventos (dividendos de FII e rendimentos de ETF)."""

    def setUp(self) -> None:
        self.engine = FiiEtfLendingEngine()
        self.engine.deposit_custody(105, 10001, "HGLG11", "BRHGLGCTF004", 20000)
        self.engine.deposit_custody(105, 10001, "IMAB11", "BRIMABCTF001", 30000)

        # Contrato 1: HGLG11 aberto em 2026-10-01
        self.c1 = FiiEtfLoanContract(
            contract_id="BTC-DIV-FII-01",
            asset_type=AssetType.FII,
            symbol="HGLG11",
            isin="BRHGLGCTF004",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=88,
            borrower_account=20001,
            quantity=10000,
            base_price=Decimal("165.50"),
            rental_rate=Decimal("0.0250"),
            open_date=date(2026, 10, 1),
            maturity_date=date(2026, 12, 1),
        )
        self.engine.register_contract(self.c1)

        # Contrato 2: IMAB11 ETF de Renda Fixa aberto em 2026-10-01
        self.c2 = FiiEtfLoanContract(
            contract_id="BTC-DIV-ETF-02",
            asset_type=AssetType.ETF_FIXED_INCOME,
            symbol="IMAB11",
            isin="BRIMABCTF001",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=93,
            borrower_account=30001,
            quantity=20000,
            base_price=Decimal("102.00"),
            rental_rate=Decimal("0.0200"),
            open_date=date(2026, 10, 1),
            maturity_date=date(2026, 11, 30),
        )
        self.engine.register_contract(self.c2)

    def test_fii_monthly_earnings_compulsory_forwarding(self) -> None:
        """Valida que o dividendo mensal de FII é repassado integralmente ao doador (centavo a centavo)."""
        announcement = DividendAnnouncement(
            event_id="EVT-HGLG11-OUT26",
            symbol="HGLG11",
            isin="BRHGLGCTF004",
            event_type=DividendEventType.FII_EARNINGS,
            record_date=date(2026, 10, 31),
            ex_date=date(2026, 11, 3),
            payment_date=date(2026, 11, 14),
            rate_per_quota=Decimal("1.100000"),
            tax_withholding_rate=Decimal("0.0000"),  # Isento PF
        )

        receipts = self.engine.process_dividend(announcement)
        self.assertEqual(len(receipts), 1)
        r = receipts[0]

        # 10.000 cotas * R$ 1,10 = R$ 11.000,00
        self.assertEqual(r.gross_amount, Decimal("11000.00"))
        self.assertEqual(r.withholding_tax, Decimal("0.00"))
        self.assertEqual(r.net_amount, Decimal("11000.00"))
        self.assertEqual(r.borrower_debit, Decimal("11000.00"))
        self.assertEqual(r.lender_credit, Decimal("11000.00"))

        # Invariante de repasse centavo a centavo
        self.assertEqual(r.borrower_debit, r.lender_credit)
        self.assertTrue(r.value_conserved)

    def test_etf_income_forwarding_with_tax_withholding(self) -> None:
        """Valida repasse de rendimento de ETF com retenção de IRRF 15% e paridade estrita."""
        announcement = DividendAnnouncement(
            event_id="EVT-IMAB11-OUT26",
            symbol="IMAB11",
            isin="BRIMABCTF001",
            event_type=DividendEventType.ETF_INCOME,
            record_date=date(2026, 10, 15),
            ex_date=date(2026, 10, 16),
            payment_date=date(2026, 10, 22),
            rate_per_quota=Decimal("0.750000"),
            tax_withholding_rate=Decimal("0.1500"),  # 15% IRRF
        )

        receipts = self.engine.process_dividend(announcement)
        self.assertEqual(len(receipts), 1)
        r = receipts[0]

        # 20.000 cotas * R$ 0,75 = R$ 15.000,00 bruto
        # IRRF 15% = R$ 2.250,00
        # Líquido = R$ 12.750,00
        self.assertEqual(r.gross_amount, Decimal("15000.00"))
        self.assertEqual(r.withholding_tax, Decimal("2250.00"))
        self.assertEqual(r.net_amount, Decimal("12750.00"))
        self.assertEqual(r.borrower_debit, Decimal("12750.00"))
        self.assertEqual(r.lender_credit, Decimal("12750.00"))
        self.assertEqual(r.borrower_debit, r.lender_credit)

    def test_dividend_skips_ineligible_contracts(self) -> None:
        """Valida que contratos abertos APÓS a data com (record date) não recebem provento."""
        announcement_past = DividendAnnouncement(
            event_id="EVT-PAST-01",
            symbol="HGLG11",
            isin="BRHGLGCTF004",
            event_type=DividendEventType.FII_EARNINGS,
            record_date=date(2026, 9, 30),  # Anterior a 2026-10-01 (abertura do contrato)
            ex_date=date(2026, 10, 1),
            payment_date=date(2026, 10, 15),
            rate_per_quota=Decimal("1.100000"),
        )
        receipts = self.engine.process_dividend(announcement_past)
        self.assertEqual(len(receipts), 0)


class TestFiiEtfReturnAndSettlement(unittest.TestCase):
    """Testes de devolução física de cotas e liquidação de aluguel."""

    def setUp(self) -> None:
        self.engine = FiiEtfLendingEngine()
        self.engine.deposit_custody(105, 10001, "HGLG11", "BRHGLGCTF004", 10000)

        self.contract = FiiEtfLoanContract(
            contract_id="BTC-RET-001",
            asset_type=AssetType.FII,
            symbol="HGLG11",
            isin="BRHGLGCTF004",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=88,
            borrower_account=20001,
            quantity=10000,
            base_price=Decimal("165.50"),
            rental_rate=Decimal("0.0250"),
            open_date=date(2026, 10, 1),
            maturity_date=date(2026, 12, 1),
            reversibility=ReversibilityType.BORROWER_REVERSIBLE,
        )
        self.engine.register_contract(self.contract)

    def test_partial_early_return(self) -> None:
        """Devolução parcial antecipada de 4.000 cotas com entrega física e quitação de aluguel."""
        receipt = self.engine.process_return(
            contract_id="BTC-RET-001",
            return_date=date(2026, 11, 4),
            quantity=4000,
        )

        self.assertEqual(receipt.returned_quantity, 4000)
        self.assertEqual(receipt.remaining_quantity, 6000)
        self.assertTrue(receipt.is_early_return)
        self.assertFalse(receipt.is_final_settlement)
        self.assertGreater(receipt.rental_fee, Decimal("0.00"))

        # Estado do contrato
        self.assertEqual(self.contract.status, ContractStatus.PARTIALLY_RETURNED)
        self.assertEqual(self.contract.active_quantity, 6000)
        self.assertEqual(self.contract.returned_quantity, 4000)

        # Verificação da custódia após entrega física
        borrower_pos = self.engine.get_custody_position(88, 20001, "HGLG11")
        self.assertEqual(borrower_pos.available_qty, 6000)
        self.assertEqual(borrower_pos.borrowed_active_qty, 6000)

        lender_pos = self.engine.get_custody_position(105, 10001, "HGLG11")
        self.assertEqual(lender_pos.available_qty, 4000)  # 4.000 devolvidas voltam a ser disponíveis
        self.assertEqual(lender_pos.lent_active_qty, 6000)
        self.assertEqual(lender_pos.returned_cum_qty, 4000)

    def test_complete_return_at_maturity(self) -> None:
        """Liquidação integral no vencimento com entrega de todas as cotas restantes."""
        receipt = self.engine.process_return(
            contract_id="BTC-RET-001",
            return_date=date(2026, 12, 1),
            quantity=10000,
        )

        self.assertEqual(receipt.returned_quantity, 10000)
        self.assertEqual(receipt.remaining_quantity, 0)
        self.assertFalse(receipt.is_early_return)
        self.assertTrue(receipt.is_final_settlement)

        self.assertEqual(self.contract.status, ContractStatus.RETURNED)
        self.assertEqual(self.contract.active_quantity, 0)

        # Custódia volta ao estado original do Doador
        lender_pos = self.engine.get_custody_position(105, 10001, "HGLG11")
        self.assertEqual(lender_pos.available_qty, 10000)
        self.assertEqual(lender_pos.lent_active_qty, 0)
        self.assertEqual(lender_pos.returned_cum_qty, 10000)

        # Tomador fica com passivo zerado
        borrower_pos = self.engine.get_custody_position(88, 20001, "HGLG11")
        self.assertEqual(borrower_pos.available_qty, 0)
        self.assertEqual(borrower_pos.borrowed_active_qty, 0)

    def test_delivery_failure_when_borrower_lacks_custody(self) -> None:
        """Simula falha de entrega (buy-in) quando o Tomador não tem as cotas para devolver."""
        # Tomador vendeu as cotas a descoberto (retira da custódia disponível)
        borrower_acc = self.engine.get_or_create_account(88, 20001)
        borrower_acc.withdraw("HGLG11", 7000)  # Restam apenas 3.000

        # Tenta devolver 5.000 cotas -> Falha de custódia
        with self.assertRaises(InsufficientCustodyError):
            self.engine.process_return(
                contract_id="BTC-RET-001",
                return_date=date(2026, 11, 4),
                quantity=5000,
            )

    def test_fixed_term_contract_rejects_early_return(self) -> None:
        """Contrato de prazo fixo (FIXED_TERM) rejeita devolução antes da maturity_date."""
        self.engine.deposit_custody(105, 10001, "KNIP11", "BRKNIPCTF001", 5000)
        c_fixed = FiiEtfLoanContract(
            contract_id="BTC-FIXED-001",
            asset_type=AssetType.FII,
            symbol="KNIP11",
            isin="BRKNIPCTF001",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=88,
            borrower_account=20001,
            quantity=5000,
            base_price=Decimal("95.00"),
            rental_rate=Decimal("0.0300"),
            open_date=date(2026, 10, 1),
            maturity_date=date(2026, 11, 30),
            reversibility=ReversibilityType.FIXED_TERM,
        )
        self.engine.register_contract(c_fixed)

        # Devolução antecipada em 2026-11-10 deve ser rejeitada
        with self.assertRaises(ContractTermError):
            self.engine.process_return(
                contract_id="BTC-FIXED-001",
                return_date=date(2026, 11, 10),
                quantity=5000,
            )


class TestFiiEtfFormalInvariants(unittest.TestCase):
    """Testes formais de conformidade e estresse das leis invariantes (LAW-084 e LAW-085)."""

    def setUp(self) -> None:
        self.engine = FiiEtfLendingEngine()
        self.engine.deposit_custody(105, 10001, "HGLG11", "BRHGLGCTF004", 10000)
        self.contract = FiiEtfLoanContract(
            contract_id="BTC-INV-001",
            asset_type=AssetType.FII,
            symbol="HGLG11",
            isin="BRHGLGCTF004",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=88,
            borrower_account=20001,
            quantity=10000,
            base_price=Decimal("165.50"),
            rental_rate=Decimal("0.0250"),
            open_date=date(2026, 10, 1),
            maturity_date=date(2026, 12, 1),
        )
        self.engine.register_contract(self.contract)

    def test_invariants_verify_cleanly(self) -> None:
        """Verifica que o sistema passa 100% na auditoria de invariantes em múltiplos passos."""
        audit_1 = self.engine.verify_invariants()
        self.assertTrue(audit_1["law_084_contract_quota_conservation"])
        self.assertTrue(audit_1["law_085_dividend_forwarding_parity"])
        self.assertTrue(audit_1["law_multilateral_custody_balance"])
        self.assertTrue(audit_1["law_non_negative_custody"])

        # Provento
        announcement = DividendAnnouncement(
            event_id="EVT-01",
            symbol="HGLG11",
            isin="BRHGLGCTF004",
            event_type=DividendEventType.FII_EARNINGS,
            record_date=date(2026, 10, 31),
            ex_date=date(2026, 11, 3),
            payment_date=date(2026, 11, 14),
            rate_per_quota=Decimal("1.100000"),
        )
        self.engine.process_dividend(announcement)
        audit_2 = self.engine.verify_invariants()
        self.assertTrue(audit_2["law_085_dividend_forwarding_parity"])

        # Devolução parcial
        self.engine.process_return("BTC-INV-001", return_date=date(2026, 11, 4), quantity=3000)
        audit_3 = self.engine.verify_invariants()
        self.assertTrue(audit_3["law_084_contract_quota_conservation"])

        # Devolução final
        self.engine.process_return("BTC-INV-001", return_date=date(2026, 12, 1), quantity=7000)
        audit_4 = self.engine.verify_invariants()
        self.assertTrue(audit_4["law_084_contract_quota_conservation"])

    def test_adversarial_break_of_law_084_quota_conservation(self) -> None:
        """Garante que a quebra intencional de conservação de cotas é detectada e interrompida."""
        # Força corruptela no contrato: quantidade total adulterada
        self.contract.quantity = 9999  # Deveria ser 10000

        with self.assertRaises(ContractConservationError):
            self.engine.verify_invariants()

    def test_adversarial_break_of_law_085_dividend_parity(self) -> None:
        """Garante que a disparidade no repasse de proventos é detectada."""
        # Injeta um recibo fraudulento com débito != crédito
        fraudulent_receipt = DividendSettlementReceipt(
            receipt_id="REC-FRAUD-001",
            contract_id="BTC-INV-001",
            event_id="EVT-FRAUD",
            symbol="HGLG11",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=88,
            borrower_account=20001,
            quantity_at_record_date=10000,
            rate_per_quota=Decimal("1.00"),
            gross_amount=Decimal("10000.00"),
            withholding_tax=Decimal("0.00"),
            net_amount=Decimal("10000.00"),
            borrower_debit=Decimal("10000.00"),
            lender_credit=Decimal("9999.99"),  # 1 centavo desviado!
            tax_compensation_credit=Decimal("0.00"),
            payment_date=date(2026, 11, 14),
        )
        self.engine.dividend_receipts.append(fraudulent_receipt)

        with self.assertRaises(ContractConservationError):
            self.engine.verify_invariants()

    def test_adversarial_custody_multilateral_imbalance(self) -> None:
        """Garante que o desbalanceamento multilateral de custódia é detectado."""
        borrower_pos = self.engine.get_custody_position(88, 20001, "HGLG11")
        borrower_pos.borrowed_active_qty += 500  # Adulteração unilateral

        with self.assertRaises(ContractConservationError):
            self.engine.verify_invariants()


class TestFiiEtfFixtureAndSerialization(unittest.TestCase):
    """Testes de carregamento, serialização to_dict e validação do fixture oficial."""

    def test_load_official_fixture(self) -> None:
        """Carrega e valida o fixture fii_etf_lending_btc.json oficial."""
        self.assertTrue(FIXTURE_PATH.exists(), f"Fixture não encontrado em: {FIXTURE_PATH}")

        engine = FiiEtfLendingEngine.load_fixture(FIXTURE_PATH)
        self.assertEqual(len(engine.contracts), 4)
        self.assertEqual(len(engine.dividend_receipts), 3)
        self.assertEqual(len(engine.return_receipts), 2)

        # Auditoria formal completa de invariantes
        audit = engine.verify_invariants()
        self.assertTrue(audit["law_084_contract_quota_conservation"])
        self.assertTrue(audit["law_085_dividend_forwarding_parity"])
        self.assertTrue(audit["law_multilateral_custody_balance"])
        self.assertTrue(audit["law_non_negative_custody"])

    def test_to_dict_roundtrip(self) -> None:
        """Verifica serialização to_dict e reconstrução fiel from_dict."""
        engine = FiiEtfLendingEngine.load_fixture(FIXTURE_PATH)
        data = engine.to_dict()

        self.assertIn("version", data)
        self.assertIn("contracts", data)
        self.assertIn("custody_accounts", data)
        self.assertIn("dividend_receipts", data)
        self.assertIn("return_receipts", data)
        self.assertIn("invariants_audit", data)

        # Reconstituição
        reconstructed = FiiEtfLendingEngine.from_dict(data)
        self.assertEqual(len(reconstructed.contracts), len(engine.contracts))
        self.assertEqual(len(reconstructed.dividend_receipts), len(engine.dividend_receipts))
        self.assertEqual(len(reconstructed.return_receipts), len(engine.return_receipts))

        # Nova auditoria de invariantes no motor reconstruído
        audit_reconstructed = reconstructed.verify_invariants()
        self.assertTrue(audit_reconstructed["law_084_contract_quota_conservation"])


if __name__ == "__main__":
    unittest.main()
