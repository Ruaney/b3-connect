"""Testes unitários e verificação formal do Módulo de Letras Financeiras Subordinadas (FL-073).

Valida conformidade estrita com:
- Resoluções CMN nº 4.958/2021 e nº 5.036/2022.
- Lei nº 12.249/2010.
- Padrões de Capital de Basileia III (AT1 e Tier 2).
"""

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.subordinated_financial_bills import (
    CapitalTier,
    CouponStatus,
    LossAbsorptionError,
    LossAbsorptionEvent,
    LossAbsorptionMechanism,
    RegulatoryHierarchyBreachError,
    SubordinatedBillCoupon,
    SubordinatedBillError,
    SubordinatedBillIndexer,
    SubordinatedBillStatus,
    SubordinatedBillValidationError,
    SubordinatedFinancialBill,
    SubordinatedFinancialBillsRegistry,
    WriteUpError,
    WriteUpEvent,
)


class TestSubordinatedFinancialBills(unittest.TestCase):
    """Bateria de testes unitários do motor de Letras Financeiras Subordinadas."""

    def setUp(self) -> None:
        self.registry = SubordinatedFinancialBillsRegistry()
        self.fixture_path = (
            Path(__file__).resolve().parent.parent / "fixtures" / "subordinated_financial_bills.json"
        )
        with open(self.fixture_path, "r", encoding="utf-8") as f:
            self.fixture_data = json.load(f)

    def test_load_and_register_fixture_bills(self) -> None:
        """Verifica o carregamento dos instrumentos da fixture e cadastro com sucesso."""
        for bill_raw in self.fixture_data["bills"]:
            bill = SubordinatedFinancialBill.from_dict(bill_raw)
            self.registry.register_bill(bill)

        self.assertEqual(len(self.registry.bills), 4)
        report = self.registry.verify_all_invariants()
        self.assertTrue(report["all_invariants_hold"])
        self.assertEqual(len(report["violations"]), 0)

    def test_tier1_must_be_perpetual(self) -> None:
        """Garante que LF Nível 1 com maturity_date definida seja rejeitada no cadastro."""
        invalid_at1 = SubordinatedFinancialBill(
            bill_id="LF-INV-001",
            isin="BRLFINV001A1",
            issuer_cnpj="60701190000104",
            issuer_name="BANCO TESTE",
            capital_tier=CapitalTier.LEVEL_1_ADDITIONAL,
            absorption_mechanism=LossAbsorptionMechanism.PERMANENT_WRITE_DOWN,
            indexer=SubordinatedBillIndexer.CDI,
            spread_annual=Decimal("0.02"),
            interest_description="CDI + 2%",
            issue_date=date(2023, 1, 1),
            maturity_date=date(2033, 1, 1),  # Inválido: Nível 1 não pode ter vencimento
            nominal_unit_value=Decimal("100000.00"),
            quantity=Decimal("10"),
            interest_cancellable_discretionary=True,
        )
        with self.assertRaises(SubordinatedBillValidationError):
            self.registry.register_bill(invalid_at1)

    def test_tier2_minimum_tenor_five_years(self) -> None:
        """Garante que LF Nível 2 com prazo inferior a 5 anos (1825 dias) seja rejeitada."""
        invalid_t2 = SubordinatedFinancialBill(
            bill_id="LF-INV-002",
            isin="BRLFINV002A2",
            issuer_cnpj="60701190000104",
            issuer_name="BANCO TESTE",
            capital_tier=CapitalTier.LEVEL_2,
            absorption_mechanism=LossAbsorptionMechanism.PERMANENT_WRITE_DOWN,
            indexer=SubordinatedBillIndexer.CDI,
            spread_annual=Decimal("0.02"),
            interest_description="CDI + 2%",
            issue_date=date(2023, 1, 1),
            maturity_date=date(2026, 1, 1),  # Apenas 3 anos (1095 dias)
            nominal_unit_value=Decimal("100000.00"),
            quantity=Decimal("10"),
            interest_cancellable_discretionary=False,
        )
        with self.assertRaises(SubordinatedBillValidationError):
            self.registry.register_bill(invalid_t2)

    def test_minimum_nominal_unit_value(self) -> None:
        """Garante cumprimento do valor nominal unitário mínimo de R$ 50.000,00."""
        invalid_vnu = SubordinatedFinancialBill(
            bill_id="LF-INV-003",
            isin="BRLFINV003A3",
            issuer_cnpj="60701190000104",
            issuer_name="BANCO TESTE",
            capital_tier=CapitalTier.LEVEL_2,
            absorption_mechanism=LossAbsorptionMechanism.PERMANENT_WRITE_DOWN,
            indexer=SubordinatedBillIndexer.CDI,
            spread_annual=Decimal("0.02"),
            interest_description="CDI + 2%",
            issue_date=date(2023, 1, 1),
            maturity_date=date(2030, 1, 1),
            nominal_unit_value=Decimal("49999.00"),  # Inferior a R$ 50.000,00
            quantity=Decimal("10"),
            interest_cancellable_discretionary=False,
        )
        with self.assertRaises(SubordinatedBillValidationError):
            self.registry.register_bill(invalid_vnu)

    def test_discretionary_coupon_suspension_activates_stopper_without_default(self) -> None:
        """Valida que a suspensão de cupom Nível 1 ativa o Dividend Stopper sem caracterizar default."""
        bill_raw = self.fixture_data["bills"][0]
        bill = SubordinatedFinancialBill.from_dict(bill_raw)
        self.registry.register_bill(bill)

        # Suspende cupom
        updated = self.registry.suspend_coupon_discretionary(
            bill_id=bill.bill_id,
            coupon_id="CPN-AT1-001-01",
            reason="Preservação de solvência sob estresse",
        )

        self.assertEqual(updated.status, SubordinatedBillStatus.INTEREST_SUSPENDED)
        self.assertTrue(updated.dividend_stopper_active)
        self.assertEqual(updated.coupons[0].status, CouponStatus.SUSPENDED_DISCRETIONARY)

        # Retoma cupons
        resumed = self.registry.resume_coupon_payments(bill.bill_id)
        self.assertEqual(resumed.status, SubordinatedBillStatus.ACTIVE)
        self.assertFalse(resumed.dividend_stopper_active)

    def test_permanent_write_down_absorption_and_pro_rata(self) -> None:
        """Valida a absorção de perdas via write-down permanente e cálculo de saldo devedor."""
        # Registra apenas o AT1 com write-down permanente
        bill = SubordinatedFinancialBill.from_dict(self.fixture_data["bills"][0])
        self.registry.register_bill(bill)

        initial_bal = bill.outstanding_balance
        self.assertEqual(initial_bal, Decimal("150000000.00"))  # 500 * 300,000

        # Dispara absorção parcial de R$ 30.000.000,00
        events = self.registry.evaluate_and_trigger_loss_absorption(
            issuer_cnpj=bill.issuer_cnpj,
            current_cet1_ratio=Decimal("0.0450"),  # abaixo de 5.125%
            loss_amount_to_absorb=Decimal("30000000.00"),
        )

        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertEqual(evt.nominal_loss_absorbed, Decimal("30000000.00"))
        self.assertEqual(bill.status, SubordinatedBillStatus.PARTIALLY_WRITTEN_DOWN)
        self.assertEqual(bill.outstanding_balance, Decimal("120000000.00"))
        self.assertEqual(bill.current_unit_value, Decimal("240000.00000000"))

    def test_mandatory_equity_conversion(self) -> None:
        """Valida conversão compulsória de dívida subordinada em ações e apuração de troco."""
        bill = SubordinatedFinancialBill.from_dict(self.fixture_data["bills"][2])  # AT1-003: Conversão
        self.registry.register_bill(bill)

        # Saldo total: 1000 * 100,000 = 100,000,000. Preço da ação: R$ 25.00
        events = self.registry.evaluate_and_trigger_loss_absorption(
            issuer_cnpj=bill.issuer_cnpj,
            current_cet1_ratio=Decimal("0.0400"),
            loss_amount_to_absorb=Decimal("25000010.50"),  # 1.000.000 ações * 25.00 = 25.000.000 + 10.50 troco
        )

        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertEqual(evt.mechanism, LossAbsorptionMechanism.MANDATORY_EQUITY_CONVERSION)
        self.assertEqual(evt.shares_issued, Decimal("1000000"))
        self.assertEqual(evt.cash_fraction_refund, Decimal("10.50"))
        self.assertEqual(
            evt.nominal_loss_absorbed,
            Decimal("1000000") * Decimal("25.00") + Decimal("10.50"),
        )

    def test_temporary_write_down_and_write_up(self) -> None:
        """Valida ciclo completo de write-down temporário e subsequente recomposição (write-up)."""
        bill = SubordinatedFinancialBill.from_dict(self.fixture_data["bills"][1])  # AT1-002: Temp Write-Down
        self.registry.register_bill(bill)

        # 1. Write-down de R$ 20.000.000,00 (200 unidades * R$ 100.000,00 por unidade)
        self.registry.evaluate_and_trigger_loss_absorption(
            issuer_cnpj=bill.issuer_cnpj,
            current_cet1_ratio=Decimal("0.0420"),
            loss_amount_to_absorb=Decimal("20000000.00"),
        )
        self.assertEqual(bill.current_unit_value, Decimal("400000.00000000"))
        self.assertEqual(bill.status, SubordinatedBillStatus.PARTIALLY_WRITTEN_DOWN)

        # 2. Write-up parcial de R$ 50.000,00 por cota com lucro líquido disponível
        up_evt = self.registry.execute_temporary_write_up(
            bill_id=bill.bill_id,
            write_up_amount_per_unit=Decimal("50000.00"),
            bacen_authorization_protocol="BACEN-PROC-2026-99988",
            audit_net_profit_available=Decimal("50000000.00"),
        )
        self.assertEqual(bill.current_unit_value, Decimal("450000.00000000"))
        self.assertEqual(up_evt.write_up_value_total, Decimal("10000000.00"))  # 200 * 50k
        self.assertEqual(bill.status, SubordinatedBillStatus.PARTIALLY_WRITTEN_DOWN)

        # 3. Tentativa de write-up que ultrapassaria o PU de referência original deve ser barrada
        with self.assertRaises(WriteUpError):
            self.registry.execute_temporary_write_up(
                bill_id=bill.bill_id,
                write_up_amount_per_unit=Decimal("60000.00"),  # Resultaria em 510.000 > 500.000
                bacen_authorization_protocol="BACEN-PROC-2026-99989",
                audit_net_profit_available=Decimal("50000000.00"),
            )

        # 4. Tentativa de write-up sem lucro líquido suficiente
        with self.assertRaises(WriteUpError):
            self.registry.execute_temporary_write_up(
                bill_id=bill.bill_id,
                write_up_amount_per_unit=Decimal("50000.00"),
                bacen_authorization_protocol="BACEN-PROC-2026-99989",
                audit_net_profit_available=Decimal("1000.00"),  # Lucro insuficiente
            )

    def test_capital_hierarchy_protection(self) -> None:
        """Garante que instrumentos Tier 2 não podem sofrer perdas enquanto houver saldo em AT1."""
        at1 = SubordinatedFinancialBill.from_dict(self.fixture_data["bills"][0])
        t2 = SubordinatedFinancialBill.from_dict(self.fixture_data["bills"][3])
        self.registry.register_bill(at1)
        self.registry.register_bill(t2)

        # 1. Sem PonV formal, Nível 2 NÃO pode sofrer perdas mesmo que perda exceda Nível 1
        events = self.registry.evaluate_and_trigger_loss_absorption(
            issuer_cnpj=at1.issuer_cnpj,
            current_cet1_ratio=Decimal("0.0300"),
            loss_amount_to_absorb=Decimal("200000000.00"),  # Excede os 150M de AT1
            is_ponv=False,
        )
        self.assertEqual(len(events), 1)  # Apenas evento de AT1
        self.assertEqual(at1.outstanding_balance, Decimal("0.00"))
        self.assertEqual(t2.outstanding_balance, Decimal("200000000.00"))  # Intacto!

        # 2. Com PonV formal e após Nível 1 estar totalmente exaurido, Nível 2 absorve
        events_ponv = self.registry.evaluate_and_trigger_loss_absorption(
            issuer_cnpj=t2.issuer_cnpj,
            current_cet1_ratio=Decimal("0.0200"),
            loss_amount_to_absorb=Decimal("50000000.00"),
            is_ponv=True,
        )
        self.assertEqual(len(events_ponv), 1)
        self.assertEqual(t2.outstanding_balance, Decimal("150000000.00"))

        # 3. Violação de hierarquia: se houver AT1 com saldo remanescente não absorvido
        at1_extra = SubordinatedFinancialBill(
            bill_id="LF-SUB-AT1-EXTRA",
            isin="BRLFSUBAT199",
            issuer_cnpj=t2.issuer_cnpj,
            issuer_name=t2.issuer_name,
            capital_tier=CapitalTier.LEVEL_1_ADDITIONAL,
            absorption_mechanism=LossAbsorptionMechanism.PERMANENT_WRITE_DOWN,
            indexer=SubordinatedBillIndexer.CDI,
            spread_annual=Decimal("0.02"),
            interest_description="CDI + 2%",
            issue_date=date(2023, 1, 1),
            nominal_unit_value=Decimal("100000.00"),
            quantity=Decimal("10"),
            interest_cancellable_discretionary=True,
        )
        # Se for adicionado mas colocado em status onde tier1_bills o ignora mas saldo existe
        at1_extra.status = SubordinatedBillStatus.CONVERTED_TO_EQUITY
        self.registry.bills[at1_extra.bill_id] = at1_extra

        with self.assertRaises(RegulatoryHierarchyBreachError):
            self.registry.evaluate_and_trigger_loss_absorption(
                issuer_cnpj=t2.issuer_cnpj,
                current_cet1_ratio=Decimal("0.0200"),
                loss_amount_to_absorb=Decimal("100000.00"),
                is_ponv=True,
            )

    def test_tier2_regulatory_capital_amortization_factor(self) -> None:
        """Valida o cronograma prudencial de amortização regulatória de capital Nível 2 (20%/ano)."""
        t2 = SubordinatedFinancialBill.from_dict(self.fixture_data["bills"][3])
        # Data de vencimento: 2031-03-01
        self.registry.register_bill(t2)

        # Mais de 5 anos para o vencimento (> 2026-03-01) -> 100% elegível
        f_full = t2.tier2_regulatory_capital_factor(date(2025, 1, 1))
        self.assertEqual(f_full, Decimal("1.00"))

        # Entre 4 e 5 anos (ex: 2026-06-01) -> 80%
        f_80 = t2.tier2_regulatory_capital_factor(date(2026, 6, 1))
        self.assertEqual(f_80, Decimal("0.80"))

        # Entre 3 e 4 anos (ex: 2027-06-01) -> 60%
        f_60 = t2.tier2_regulatory_capital_factor(date(2027, 6, 1))
        self.assertEqual(f_60, Decimal("0.60"))

        # Entre 2 e 3 anos (ex: 2028-06-01) -> 40%
        f_40 = t2.tier2_regulatory_capital_factor(date(2028, 6, 1))
        self.assertEqual(f_40, Decimal("0.40"))

        # Entre 1 e 2 anos (ex: 2029-06-01) -> 20%
        f_20 = t2.tier2_regulatory_capital_factor(date(2029, 6, 1))
        self.assertEqual(f_20, Decimal("0.20"))

        # Menos de 1 ano (ex: 2030-06-01) -> 0%
        f_0 = t2.tier2_regulatory_capital_factor(date(2030, 6, 1))
        self.assertEqual(f_0, Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
