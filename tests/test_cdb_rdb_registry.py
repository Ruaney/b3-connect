"""Testes unitários para o módulo de Registro e Liquidação de CDB e RDB (FL-054).

Valida:
1. Validação de modelos cadastrais e aderência à Resolução CMN nº 5.036/2022.
2. Inalienabilidade e intransferibilidade do RDB versus transferibilidade do CDB.
3. Precisão da tabela regressiva de IOF (Decreto 6.306/2007, dias 1 a 29 e isenção dia 30+).
4. Precisão da tabela regressiva de IRRF (Lei 11.033/2004, faixas até 180, 360, 720 e >720 dias).
5. Curva de precificação e atualização diária de PU para PRE, %CDI, CDI + Spread e IPCA.
6. Liquidação de vencimento e resgates antecipados parciais e totais.
7. Invariantes formais de conservação financeira:
   - Bruto == Principal + Rendimento Bruto
   - Liquido == Bruto - IOF - IRRF
   - Liquido == Principal + Rendimento Líquido
8. Replay e integridade das fixtures oficiais em `fixtures/cdb_rdb_registry.json`.
"""

from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

# Garante a raiz do projeto no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.cdb_rdb_registry import (
    BankingSecurityType,
    CdbCalculationError,
    CdbContract,
    CdbIndexerType,
    CdbRdbError,
    CdbRdbRegistryEngine,
    CdbSettlementError,
    CdbSettlementReceipt,
    CdbValidationError,
    ContractStatus,
    InalienableRdbError,
    SettlementType,
    TaxCalculationBreakdown,
    get_iof_regressive_rate,
    get_irrf_regressive_rate,
)


class TestCdbRdbTaxRules(unittest.TestCase):
    """Testes unitários das tabelas regressivas de IOF e IRRF."""

    def test_iof_regressive_table(self) -> None:
        """Valida alíquotas de IOF regressivo em dias-chave conforme Decreto 6.306/2007."""
        self.assertEqual(get_iof_regressive_rate(1), Decimal("96.00"))
        self.assertEqual(get_iof_regressive_rate(5), Decimal("83.00"))
        self.assertEqual(get_iof_regressive_rate(10), Decimal("66.00"))
        self.assertEqual(get_iof_regressive_rate(15), Decimal("50.00"))
        self.assertEqual(get_iof_regressive_rate(20), Decimal("33.00"))
        self.assertEqual(get_iof_regressive_rate(25), Decimal("16.00"))
        self.assertEqual(get_iof_regressive_rate(29), Decimal("3.00"))
        self.assertEqual(get_iof_regressive_rate(30), Decimal("0.00"))
        self.assertEqual(get_iof_regressive_rate(45), Decimal("0.00"))
        self.assertEqual(get_iof_regressive_rate(750), Decimal("0.00"))

    def test_irrf_regressive_table(self) -> None:
        """Valida alíquotas regressivas de IRRF conforme Lei 11.033/2004."""
        # Até 180 dias: 22.5%
        self.assertEqual(get_irrf_regressive_rate(1), Decimal("22.50"))
        self.assertEqual(get_irrf_regressive_rate(15), Decimal("22.50"))
        self.assertEqual(get_irrf_regressive_rate(180), Decimal("22.50"))

        # De 181 a 360 dias: 20.0%
        self.assertEqual(get_irrf_regressive_rate(181), Decimal("20.00"))
        self.assertEqual(get_irrf_regressive_rate(360), Decimal("20.00"))

        # De 361 a 720 dias: 17.5%
        self.assertEqual(get_irrf_regressive_rate(361), Decimal("17.50"))
        self.assertEqual(get_irrf_regressive_rate(720), Decimal("17.50"))

        # Acima de 720 dias: 15.0%
        self.assertEqual(get_irrf_regressive_rate(721), Decimal("15.00"))
        self.assertEqual(get_irrf_regressive_rate(750), Decimal("15.00"))
        self.assertEqual(get_irrf_regressive_rate(1000), Decimal("15.00"))

    def test_tax_calculation_breakdown_conservation(self) -> None:
        """Valida a consistência formal e conservação na apuração de tributos."""
        engine = CdbRdbRegistryEngine()

        # Resgate no 15º dia com rendimento de R$ 500,00
        tax = engine.calculate_taxes(Decimal("500.00"), 15)
        self.assertEqual(tax.calendar_days, 15)
        self.assertEqual(tax.iof_rate, Decimal("50.00"))
        self.assertEqual(tax.iof_amount, Decimal("250.00"))
        self.assertEqual(tax.irrf_base, Decimal("250.00"))
        self.assertEqual(tax.irrf_rate, Decimal("22.50"))
        self.assertEqual(tax.irrf_amount, Decimal("56.25"))
        self.assertEqual(tax.total_tax, Decimal("306.25"))
        self.assertEqual(tax.net_yield, Decimal("193.75"))

        # Invariantes
        self.assertEqual(tax.net_yield, tax.gross_yield - tax.total_tax)
        self.assertEqual(tax.total_tax, tax.iof_amount + tax.irrf_amount)

        # Rendimento zero ou negativo
        zero_tax = engine.calculate_taxes(Decimal("0.00"), 15)
        self.assertEqual(zero_tax.total_tax, Decimal("0.00"))
        self.assertEqual(zero_tax.net_yield, Decimal("0.00"))


class TestCdbRdbModelsAndValidation(unittest.TestCase):
    """Testes de integridade cadastral e regras regulatórias CMN 5.036."""

    def test_valid_cdb_creation(self) -> None:
        """Criação bem-sucedida de contrato de CDB Pré."""
        contract = CdbContract(
            contract_id="CDB-TEST-001",
            security_type=BankingSecurityType.CDB,
            issuer_name="BANCO DO BRASIL S.A.",
            issuer_ispb="00000000",
            custody_account="998877-1",
            investor_id="12.345.678/0001-90",
            issue_date=date(2024, 1, 2),
            maturity_date=date(2025, 1, 2),
            initial_quantity=50,
            current_quantity=50,
            initial_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
            indexer_type=CdbIndexerType.PRE,
            annual_rate=Decimal("11.5000"),
        )
        self.assertTrue(contract.is_alienable)
        self.assertEqual(contract.principal_amount(), Decimal("50000.00"))
        self.assertEqual(contract.gross_financial_value(), Decimal("50000.00"))
        self.assertEqual(contract.gross_yield(), Decimal("0.00"))

    def test_rdb_inalienability_enforcement(self) -> None:
        """Valida que RDB não pode ser transferido ou alienado a terceiros (CMN 5.036)."""
        engine = CdbRdbRegistryEngine()
        rdb = CdbContract(
            contract_id="RDB-TEST-001",
            security_type=BankingSecurityType.RDB,
            issuer_name="BANCO SANTANDER BRASIL S.A.",
            issuer_ispb="90400888",
            custody_account="400123-9",
            investor_id="CLIENTE-ORIGINAL-CPF-111",
            issue_date=date(2024, 1, 2),
            maturity_date=date(2025, 1, 2),
            initial_quantity=20,
            current_quantity=20,
            initial_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
            indexer_type=CdbIndexerType.CDI_PERCENT,
            cdi_percent=Decimal("105.00"),
        )
        engine.register_contract(rdb)
        self.assertFalse(rdb.is_alienable)

        # Mesma titularidade (permitida para liquidação)
        engine.validate_rdb_transfer_attempt("RDB-TEST-001", "CLIENTE-ORIGINAL-CPF-111")

        # Tentativa de cessão a terceiro (bloqueada)
        with self.assertRaises(InalienableRdbError):
            engine.validate_rdb_transfer_attempt("RDB-TEST-001", "TERCEIRO-INVESTIDOR-CPF-222")

    def test_invalid_contract_validations(self) -> None:
        """Valida rejeição de parâmetros inconsistentes."""
        # ISPB inválido
        with self.assertRaises(CdbValidationError):
            CdbContract(
                contract_id="CDB-INV-001",
                security_type=BankingSecurityType.CDB,
                issuer_name="BANCO TESTE",
                issuer_ispb="123",  # não tem 8 dígitos
                custody_account="123",
                investor_id="INV-1",
                issue_date=date(2024, 1, 2),
                maturity_date=date(2025, 1, 2),
                initial_quantity=10,
                current_quantity=10,
                initial_unit_price=Decimal("1000.00000000"),
                current_unit_price=Decimal("1000.00000000"),
                indexer_type=CdbIndexerType.PRE,
                annual_rate=Decimal("10.00"),
            )

        # Vencimento anterior ou igual à emissão
        with self.assertRaises(CdbValidationError):
            CdbContract(
                contract_id="CDB-INV-002",
                security_type=BankingSecurityType.CDB,
                issuer_name="BANCO TESTE",
                issuer_ispb="12345678",
                custody_account="123",
                investor_id="INV-1",
                issue_date=date(2024, 1, 2),
                maturity_date=date(2024, 1, 2),  # mesma data
                initial_quantity=10,
                current_quantity=10,
                initial_unit_price=Decimal("1000.00000000"),
                current_unit_price=Decimal("1000.00000000"),
                indexer_type=CdbIndexerType.PRE,
                annual_rate=Decimal("10.00"),
            )

        # Quantidade inicial nula ou negativa
        with self.assertRaises(CdbValidationError):
            CdbContract(
                contract_id="CDB-INV-003",
                security_type=BankingSecurityType.CDB,
                issuer_name="BANCO TESTE",
                issuer_ispb="12345678",
                custody_account="123",
                investor_id="INV-1",
                issue_date=date(2024, 1, 2),
                maturity_date=date(2025, 1, 2),
                initial_quantity=0,
                current_quantity=0,
                initial_unit_price=Decimal("1000.00000000"),
                current_unit_price=Decimal("1000.00000000"),
                indexer_type=CdbIndexerType.PRE,
                annual_rate=Decimal("10.00"),
            )


class TestCdbRdbPricingAndDailyPU(unittest.TestCase):
    """Testes da curva de precificação e atualização diária de PU."""

    def setUp(self) -> None:
        self.engine = CdbRdbRegistryEngine()
        self.start_date = date(2024, 1, 2)
        self.day_15 = date(2024, 1, 17)

        # Popula taxas DI
        curr = self.start_date + timedelta(days=1)
        while curr <= self.day_15:
            if self.engine.is_business_day(curr):
                self.engine.register_di_rate(curr, Decimal("11.6500"))
            curr += timedelta(days=1)

    def test_pu_calculation_pre(self) -> None:
        """Calcula PU na curva para título pré-fixado."""
        contract = CdbContract(
            contract_id="CDB-PRE-TEST",
            security_type=BankingSecurityType.CDB,
            issuer_name="BANCO TESTE",
            issuer_ispb="12345678",
            custody_account="123",
            investor_id="INV-1",
            issue_date=self.start_date,
            maturity_date=date(2025, 1, 2),
            initial_quantity=10,
            current_quantity=10,
            initial_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
            indexer_type=CdbIndexerType.PRE,
            annual_rate=Decimal("12.0000"),
        )
        self.engine.register_contract(contract)

        # Data de emissão: PU deve ser igual ao PU inicial
        pu_d0 = self.engine.calculate_pu(contract, self.start_date)
        self.assertEqual(pu_d0, Decimal("1000.00000000"))

        # Data D+15 (11 dias úteis)
        # (1 + 0.12)^(11/252) = 1.0049591431...
        pu_d15 = self.engine.calculate_pu(contract, self.day_15)
        self.assertEqual(pu_d15, Decimal("1004.95914310"))

    def test_pu_calculation_cdi_percent(self) -> None:
        """Calcula PU na curva para título %CDI."""
        contract = CdbContract(
            contract_id="CDB-CDI-TEST",
            security_type=BankingSecurityType.CDB,
            issuer_name="BANCO TESTE",
            issuer_ispb="12345678",
            custody_account="123",
            investor_id="INV-1",
            issue_date=self.start_date,
            maturity_date=date(2025, 1, 2),
            initial_quantity=10,
            current_quantity=10,
            initial_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
            indexer_type=CdbIndexerType.CDI_PERCENT,
            cdi_percent=Decimal("108.00"),
        )
        self.engine.register_contract(contract)

        pu_d15 = self.engine.calculate_pu(contract, self.day_15)
        self.assertTrue(pu_d15 > Decimal("1000.00000000"))
        self.assertEqual(pu_d15, Decimal("1005.20847000"))

    def test_update_daily_pu_batch(self) -> None:
        """Atualização em lote do PU diário dos contratos ativos."""
        c1 = CdbContract(
            contract_id="CDB-1",
            security_type=BankingSecurityType.CDB,
            issuer_name="BANCO 1",
            issuer_ispb="11111111",
            custody_account="1",
            investor_id="INV-1",
            issue_date=self.start_date,
            maturity_date=date(2025, 1, 2),
            initial_quantity=10,
            current_quantity=10,
            initial_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
            indexer_type=CdbIndexerType.PRE,
            annual_rate=Decimal("10.0000"),
        )
        self.engine.register_contract(c1)

        updated = self.engine.update_daily_pu(self.day_15)
        self.assertIn("CDB-1", updated)
        self.assertEqual(c1.current_unit_price, updated["CDB-1"])
        self.assertTrue(c1.current_unit_price > Decimal("1000.00000000"))


class TestCdbRdbSettlementAndConservation(unittest.TestCase):
    """Testes rigorosos de liquidação financeira e leis de conservação."""

    def setUp(self) -> None:
        self.engine = CdbRdbRegistryEngine()
        self.start_date = date(2024, 1, 2)
        self.day_15 = date(2024, 1, 17)
        self.day_750 = date(2026, 1, 21)
        self.maturity_date = date(2026, 1, 22)

        # DI rates
        curr = self.start_date + timedelta(days=1)
        while curr <= self.maturity_date:
            if self.engine.is_business_day(curr):
                rate = Decimal("11.6500") if curr.year == 2024 else Decimal("11.1500")
                self.engine.register_di_rate(curr, rate)
            curr += timedelta(days=1)

    def test_settlement_day_15_iof_and_irrf_conservation(self) -> None:
        """Resgate no 15º dia corrido com IOF 50% e IRRF 22.5%."""
        contract = CdbContract(
            contract_id="CDB-ITAU-TEST",
            security_type=BankingSecurityType.CDB,
            issuer_name="BANCO ITAU UNIBANCO S.A.",
            issuer_ispb="60701190",
            custody_account="100234-5",
            investor_id="32.145.890/0001-44",
            issue_date=self.start_date,
            maturity_date=self.maturity_date,
            initial_quantity=100,
            current_quantity=100,
            initial_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
            indexer_type=CdbIndexerType.PRE,
            annual_rate=Decimal("12.0000"),
        )
        self.engine.register_contract(contract)

        # Resgate parcial de 50 títulos no dia 15
        receipt = self.engine.settle_contract("CDB-ITAU-TEST", self.day_15, redeemed_quantity=50)

        self.assertEqual(receipt.settlement_type, SettlementType.EARLY_PARTIAL)
        self.assertEqual(receipt.calendar_days, 15)
        self.assertEqual(receipt.business_days, 11)
        self.assertEqual(receipt.redeemed_quantity, 50)
        self.assertEqual(receipt.remaining_quantity, 50)
        self.assertEqual(contract.status, ContractStatus.PARTIALLY_REDEEMED)

        # Validação tributária
        self.assertEqual(receipt.tax_breakdown.iof_rate, Decimal("50.00"))
        self.assertEqual(receipt.tax_breakdown.irrf_rate, Decimal("22.50"))

        # Leis Formais de Conservação Contábil
        self.assertEqual(
            receipt.gross_amount,
            receipt.principal_amount + receipt.tax_breakdown.gross_yield,
            "Violação da Lei de Conservação: Bruto != Principal + Rendimento Bruto",
        )
        self.assertEqual(
            receipt.net_amount,
            receipt.gross_amount - receipt.tax_breakdown.iof_amount - receipt.tax_breakdown.irrf_amount,
            "Violação da Lei de Conservação: Líquido != Bruto - IOF - IRRF",
        )
        self.assertEqual(
            receipt.net_amount,
            receipt.principal_amount + receipt.tax_breakdown.net_yield,
            "Violação da Lei de Conservação: Líquido != Principal + Rendimento Líquido",
        )

    def test_settlement_after_750_days_irrf_15(self) -> None:
        """Resgate após 750 dias com IOF 0% e IRRF 15%."""
        contract = CdbContract(
            contract_id="CDB-LONG-TERM",
            security_type=BankingSecurityType.CDB,
            issuer_name="BANCO SAFRA S.A.",
            issuer_ispb="58160789",
            custody_account="500876-3",
            investor_id="11.222.333/0001-44",
            issue_date=self.start_date,
            maturity_date=self.maturity_date,
            initial_quantity=50,
            current_quantity=50,
            initial_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
            indexer_type=CdbIndexerType.PRE,
            annual_rate=Decimal("12.0000"),
        )
        self.engine.register_contract(contract)

        receipt = self.engine.settle_contract("CDB-LONG-TERM", self.day_750)

        self.assertEqual(receipt.settlement_type, SettlementType.EARLY_TOTAL)
        self.assertEqual(receipt.calendar_days, 750)
        self.assertEqual(receipt.redeemed_quantity, 50)
        self.assertEqual(receipt.remaining_quantity, 0)
        self.assertEqual(contract.status, ContractStatus.SETTLED)

        # IOF zerado e IRRF na alíquota mínima de 15%
        self.assertEqual(receipt.tax_breakdown.iof_rate, Decimal("0.00"))
        self.assertEqual(receipt.tax_breakdown.iof_amount, Decimal("0.00"))
        self.assertEqual(receipt.tax_breakdown.irrf_rate, Decimal("15.00"))
        self.assertTrue(receipt.tax_breakdown.irrf_amount > Decimal("0.00"))

        # Leis de Conservação
        self.assertEqual(
            receipt.gross_amount,
            receipt.principal_amount + receipt.tax_breakdown.gross_yield,
        )
        self.assertEqual(
            receipt.net_amount,
            receipt.gross_amount - receipt.tax_breakdown.total_tax,
        )
        self.assertEqual(
            receipt.net_amount,
            receipt.principal_amount + receipt.tax_breakdown.net_yield,
        )

    def test_total_maturity_settlement(self) -> None:
        """Liquidação por vencimento na data final estipulada."""
        contract = CdbContract(
            contract_id="CDB-MATURITY",
            security_type=BankingSecurityType.CDB,
            issuer_name="BANCO BRADESCO S.A.",
            issuer_ispb="60746948",
            custody_account="200543-1",
            investor_id="14.285.714/0001-99",
            issue_date=self.start_date,
            maturity_date=self.maturity_date,
            initial_quantity=20,
            current_quantity=20,
            initial_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
            indexer_type=CdbIndexerType.PRE,
            annual_rate=Decimal("11.0000"),
        )
        self.engine.register_contract(contract)

        receipt = self.engine.settle_contract("CDB-MATURITY", self.maturity_date)
        self.assertEqual(receipt.settlement_type, SettlementType.TOTAL_MATURITY)
        self.assertEqual(receipt.remaining_quantity, 0)
        self.assertEqual(contract.status, ContractStatus.SETTLED)

    def test_settlement_rejections(self) -> None:
        """Valida que tentativas de resgate com quantidade ou status inválidos são rejeitadas."""
        contract = CdbContract(
            contract_id="CDB-FAIL-TEST",
            security_type=BankingSecurityType.CDB,
            issuer_name="BANCO TESTE",
            issuer_ispb="12345678",
            custody_account="123",
            investor_id="INV-1",
            issue_date=self.start_date,
            maturity_date=self.maturity_date,
            initial_quantity=10,
            current_quantity=10,
            initial_unit_price=Decimal("1000.00000000"),
            current_unit_price=Decimal("1000.00000000"),
            indexer_type=CdbIndexerType.PRE,
            annual_rate=Decimal("10.0000"),
        )
        self.engine.register_contract(contract)

        # Quantidade zero ou negativa
        with self.assertRaises(CdbSettlementError):
            self.engine.settle_contract("CDB-FAIL-TEST", self.day_15, redeemed_quantity=0)

        # Quantidade maior que a disponível
        with self.assertRaises(CdbSettlementError):
            self.engine.settle_contract("CDB-FAIL-TEST", self.day_15, redeemed_quantity=11)

        # Liquidação total
        self.engine.settle_contract("CDB-FAIL-TEST", self.day_15, redeemed_quantity=10)

        # Tentar liquidar contrato já liquidado (SETTLED)
        with self.assertRaises(CdbSettlementError):
            self.engine.settle_contract("CDB-FAIL-TEST", self.day_15, redeemed_quantity=1)


class TestCdbRdbOfficialFixtures(unittest.TestCase):
    """Valida a leitura e integridade da fixture oficial `fixtures/cdb_rdb_registry.json`."""

    def setUp(self) -> None:
        self.fixtures_dir = ROOT_DIR / "fixtures"
        self.fixture_path = self.fixtures_dir / "cdb_rdb_registry.json"

    def test_fixture_loading_and_scenarios(self) -> None:
        """Carrega e valida todos os contratos e cenários da fixture oficial."""
        self.assertTrue(self.fixture_path.exists(), f"Fixture não encontrada: {self.fixture_path}")

        with open(self.fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        engine = CdbRdbRegistryEngine()

        # 1. Carrega dados de mercado
        di_history = data["market_data"]["di_over_history"]
        engine.register_di_rates_batch(di_history)

        ipca_indices = data["market_data"]["ipca_indices"]
        for dt_str, val in ipca_indices.items():
            engine.register_ipca_index(dt_str, val)

        # 2. Carrega e valida contratos
        for c_dict in data["contracts"]:
            contract = CdbContract.from_dict(c_dict)
            engine.register_contract(contract)

        self.assertIn("CDB-ITAU-PRE-2024", engine.contracts)
        self.assertIn("CDB-BRADESCO-CDI-2024", engine.contracts)
        self.assertIn("CDB-SANTANDER-CDI-SPREAD-2024", engine.contracts)
        self.assertIn("CDB-BTG-IPCA-2024", engine.contracts)
        self.assertIn("RDB-SAFRA-CDI-2024", engine.contracts)

        # 3. Executa Cenário 1: Resgate no 15º dia (IOF 50% + IRRF 22.5%)
        sc_15 = data["settlement_scenarios"]["scenario_pre_day_15_early_partial"]
        receipt_15 = engine.settle_contract(
            contract_id=sc_15["contract_id"],
            settlement_date=sc_15["settlement_date"],
            redeemed_quantity=sc_15["redeemed_quantity"],
        )
        expected_15 = sc_15["expected_receipt"]
        self.assertEqual(receipt_15.calendar_days, 15)
        self.assertEqual(receipt_15.tax_breakdown.iof_rate, Decimal("50.00"))
        self.assertEqual(receipt_15.tax_breakdown.irrf_rate, Decimal("22.50"))
        self.assertEqual(str(receipt_15.gross_amount), expected_15["gross_amount"])
        self.assertEqual(str(receipt_15.net_amount), expected_15["net_amount"])

        # 4. Executa Cenário 2: Resgate após 750 dias (IOF 0% + IRRF 15%)
        sc_750 = data["settlement_scenarios"]["scenario_pre_day_750_early_total"]
        receipt_750 = engine.settle_contract(
            contract_id=sc_750["contract_id"],
            settlement_date=sc_750["settlement_date"],
            redeemed_quantity=sc_750["redeemed_quantity"],
        )
        expected_750 = sc_750["expected_receipt"]
        self.assertEqual(receipt_750.calendar_days, 750)
        self.assertEqual(receipt_750.tax_breakdown.iof_rate, Decimal("0.00"))
        self.assertEqual(receipt_750.tax_breakdown.irrf_rate, Decimal("15.00"))
        self.assertEqual(str(receipt_750.gross_amount), expected_750["gross_amount"])
        self.assertEqual(str(receipt_750.net_amount), expected_750["net_amount"])

        # 5. Executa Cenário 3: CDB %CDI Resgate dia 15
        sc_cdi_15 = data["settlement_scenarios"]["scenario_cdi_day_15_early_partial"]
        receipt_cdi = engine.settle_contract(
            contract_id=sc_cdi_15["contract_id"],
            settlement_date=sc_cdi_15["settlement_date"],
            redeemed_quantity=sc_cdi_15["redeemed_quantity"],
        )
        expected_cdi = sc_cdi_15["expected_receipt"]
        self.assertEqual(str(receipt_cdi.net_amount), expected_cdi["net_amount"])

        # 6. Executa Cenário 4: CDB IPCA Resgate dia 750
        sc_ipca_750 = data["settlement_scenarios"]["scenario_ipca_day_750_early_partial"]
        receipt_ipca = engine.settle_contract(
            contract_id=sc_ipca_750["contract_id"],
            settlement_date=sc_ipca_750["settlement_date"],
            redeemed_quantity=sc_ipca_750["redeemed_quantity"],
        )
        expected_ipca = sc_ipca_750["expected_receipt"]
        self.assertEqual(str(receipt_ipca.net_amount), expected_ipca["net_amount"])


if __name__ == "__main__":
    unittest.main()
