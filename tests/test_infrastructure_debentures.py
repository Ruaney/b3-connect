"""Testes unitários para o módulo de Balcão NoMe & Central Depositária B3 (FL-057).

Debêntures Incentivadas e de Infraestrutura (Leis 12.431/2011 e 14.801/2024).

Valida:
1. Cadastro, escrituração e integridade de debêntures a partir da fixture oficial.
2. Marco Legal da Lei 12.431/2011: isenção de IRRF para PF (0%) e INR (0%), e tributação exclusiva de 15% para PJ.
3. Marco Legal da Lei 14.801/2024: tributação regressiva para investidores e dedução tributária de 130% para a emissora (100% despesa regular + 30% bônus).
4. Modalidades de amortização de principal: Bullet (única no vencimento), Constante e Variável.
5. Dinâmica de correção monetária por IPCA e cálculo de juros/cupom sobre VNE atualizado.
6. Invariantes matemáticos de conservação:
   - Conservação do Saldo Devedor Global: VNE_Global == Quantidade * VNE_Unitario.
   - Conservação Financeira Multilateral: Débito Emissora == Créditos Líquidos + IRRF.
   - Conservação de Custódia Depositária: Soma das contas == Quantidade em circulação.
7. Tratamento de exceções e proteção contra duplo processamento ou dados inválidos.
"""

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.infrastructure_debentures import (
    AmortizationType,
    CustodyPosition,
    DebentureEmission,
    DebentureError,
    DebentureInvariantError,
    DebentureLegalFramework,
    DebentureSettlementError,
    DebentureSettlementReceipt,
    DebentureValidationError,
    InfrastructureDebenturesEngine,
    InvestorTaxCategory,
    PaymentScheduleEvent,
    ScheduleEventType,
    round_cents,
    round_unit,
)

FIXTURES_DIR = ROOT_DIR / "fixtures"


class TestInfrastructureDebentures(unittest.TestCase):
    """Suíte de testes de escrituração e liquidação de Debêntures B3."""

    def setUp(self) -> None:
        """Inicializa o motor com as fixtures oficiais antes de cada teste."""
        self.fixture_path = FIXTURES_DIR / "infrastructure_debentures.json"
        self.engine = InfrastructureDebenturesEngine.load_from_json(self.fixture_path)

    def test_fixture_loading_and_emissions_catalog(self) -> None:
        """Verifica se todas as emissões da fixture foram carregadas e indexadas corretamente."""
        self.assertIn("TRAN11", self.engine.emissions)
        self.assertIn("RODO21", self.engine.emissions)
        self.assertIn("SANE31", self.engine.emissions)

        tran11 = self.engine.get_emission("TRAN11")
        self.assertEqual(tran11.sector, "ENERGIA")
        self.assertEqual(tran11.legal_framework, DebentureLegalFramework.LEI_12431_2011)
        self.assertEqual(tran11.amortization_type, AmortizationType.CONSTANT)
        self.assertEqual(tran11.circulating_quantity, Decimal("50000"))
        self.assertEqual(tran11.nominal_original_value, Decimal("1000.00"))
        self.assertEqual(tran11.global_nominal_escalonado_value, Decimal("50000000.00"))

        rodo21 = self.engine.get_emission("RODO21")
        self.assertEqual(rodo21.sector, "TRANSPORTE")
        self.assertEqual(rodo21.legal_framework, DebentureLegalFramework.LEI_14801_2024)
        self.assertEqual(rodo21.amortization_type, AmortizationType.BULLET)
        self.assertEqual(rodo21.circulating_quantity, Decimal("10000"))
        self.assertEqual(rodo21.nominal_original_value, Decimal("10000.00"))
        self.assertEqual(rodo21.global_nominal_escalonado_value, Decimal("100000000.00"))

        sane31 = self.engine.get_emission("SANE31")
        self.assertEqual(sane31.sector, "SANEAMENTO")
        self.assertEqual(sane31.amortization_type, AmortizationType.VARIABLE)

    def test_custody_conservation_invariant(self) -> None:
        """Verifica a Lei de Conservação de Custódia (Soma das Contas == Quantidade em Circulação)."""
        for code, emission in self.engine.emissions.items():
            self.assertEqual(
                emission.total_custody_quantity,
                emission.circulating_quantity,
                f"Custódia inconsistente na debênture {code}",
            )

    def test_tax_rates_lei_12431_vs_lei_14801(self) -> None:
        """Valida a apuração das alíquotas tributárias segundo o marco legal e perfil do investidor."""
        # Lei 12.431/2011: PF e INR são isentos (0%), PJ paga 15% definitivo
        rate_pf_12431 = self.engine.calculate_tax_rate(
            DebentureLegalFramework.LEI_12431_2011, InvestorTaxCategory.INDIVIDUAL_PF
        )
        rate_inr_12431 = self.engine.calculate_tax_rate(
            DebentureLegalFramework.LEI_12431_2011, InvestorTaxCategory.NON_RESIDENT_INR
        )
        rate_pj_12431 = self.engine.calculate_tax_rate(
            DebentureLegalFramework.LEI_12431_2011, InvestorTaxCategory.CORPORATE_PJ
        )
        self.assertEqual(rate_pf_12431, Decimal("0.00"))
        self.assertEqual(rate_inr_12431, Decimal("0.00"))
        self.assertEqual(rate_pj_12431, Decimal("0.15"))

        # Lei 14.801/2024: Tabela regressiva para todos os investidores
        rate_180d = self.engine.calculate_tax_rate(
            DebentureLegalFramework.LEI_14801_2024, InvestorTaxCategory.INDIVIDUAL_PF, holding_days=100
        )
        rate_360d = self.engine.calculate_tax_rate(
            DebentureLegalFramework.LEI_14801_2024, InvestorTaxCategory.CORPORATE_PJ, holding_days=250
        )
        rate_720d = self.engine.calculate_tax_rate(
            DebentureLegalFramework.LEI_14801_2024, InvestorTaxCategory.INDIVIDUAL_PF, holding_days=500
        )
        rate_over_720d = self.engine.calculate_tax_rate(
            DebentureLegalFramework.LEI_14801_2024, InvestorTaxCategory.CORPORATE_PJ, holding_days=750
        )
        self.assertEqual(rate_180d, Decimal("0.225"))
        self.assertEqual(rate_360d, Decimal("0.200"))
        self.assertEqual(rate_720d, Decimal("0.175"))
        self.assertEqual(rate_over_720d, Decimal("0.150"))

    def test_lei_12431_energy_constant_amortization_flow(self) -> None:
        """Testa a debênture TRAN11 (Energia, Lei 12.431/2011) com amortização constante e juros IPCA+."""
        # Evento 1: Somente Cupom (sem amortização)
        receipts_ev1 = self.engine.process_schedule_event("TRAN11", "EVT-TRAN11-01")
        self.assertEqual(len(receipts_ev1), 3)

        tran11 = self.engine.get_emission("TRAN11")
        # VNE atualizado pelo IPCA de 2.15%: 1000 * 1.0215 = 1021.50000000
        # Amortização: 0% -> VNE pós evento = 1021.50000000
        self.assertEqual(tran11.nominal_escalonado_value, Decimal("1021.50000000"))
        self.assertEqual(
            tran11.global_nominal_escalonado_value,
            Decimal("50000") * Decimal("1021.50000000"),
        )

        # Checa recibos por conta de custódia
        rec_pf = next(r for r in receipts_ev1 if r.investor_tax_category == InvestorTaxCategory.INDIVIDUAL_PF)
        rec_inr = next(r for r in receipts_ev1 if r.investor_tax_category == InvestorTaxCategory.NON_RESIDENT_INR)
        rec_pj = next(r for r in receipts_ev1 if r.investor_tax_category == InvestorTaxCategory.CORPORATE_PJ)

        # PF e INR não têm retenção de IRRF
        self.assertEqual(rec_pf.tax_rate, Decimal("0.00"))
        self.assertEqual(rec_pf.withheld_tax_amount, Decimal("0.00"))
        self.assertEqual(rec_pf.net_settlement_amount, rec_pf.total_gross_amount)

        self.assertEqual(rec_inr.tax_rate, Decimal("0.00"))
        self.assertEqual(rec_inr.withheld_tax_amount, Decimal("0.00"))
        self.assertEqual(rec_inr.net_settlement_amount, rec_inr.total_gross_amount)

        # PJ tem retenção de 15%
        self.assertEqual(rec_pj.tax_rate, Decimal("0.15"))
        self.assertGreater(rec_pj.withheld_tax_amount, Decimal("0.00"))
        self.assertEqual(rec_pj.net_settlement_amount, rec_pj.total_gross_amount - rec_pj.withheld_tax_amount)

        # Evento 2: Cupom + Amortização Constante de 25%
        receipts_ev2 = self.engine.process_schedule_event("TRAN11", "EVT-TRAN11-02")
        self.assertEqual(len(receipts_ev2), 3)

        # VNE atualizado pelo IPCA de 2.40%: 1021.50 * 1.024 = 1046.01600000
        # Amortização de 25%: 1046.016 * 0.25 = 261.50400000
        # VNE pós evento: 1046.016 - 261.504 = 784.51200000
        self.assertEqual(tran11.nominal_escalonado_value, Decimal("784.51200000"))
        self.assertEqual(
            tran11.global_nominal_escalonado_value,
            round_cents(Decimal("50000") * Decimal("784.51200000")),
        )

        # Verifica formalmente todas as invariantes da debênture
        audit = self.engine.verify_invariants("TRAN11")
        self.assertTrue(audit["custody_conservation_verified"])
        self.assertTrue(audit["debtor_balance_conservation_verified"])
        self.assertTrue(audit["multilateral_financial_conservation_verified"])

    def test_lei_14801_transport_bullet_amortization_and_issuer_bonus(self) -> None:
        """Testa a debênture RODO21 (Transporte, Lei 14.801/2024) com bônus fiscal ao emissor e Bullet."""
        rodo21 = self.engine.get_emission("RODO21")

        # Processa os primeiros 3 cupons semestrais
        self.engine.process_schedule_event("RODO21", "EVT-RODO21-01")
        self.engine.process_schedule_event("RODO21", "EVT-RODO21-02")
        self.engine.process_schedule_event("RODO21", "EVT-RODO21-03")

        # Como é Bullet, não houve amortização e o VNE apenas foi corrigido pelo IPCA
        self.assertGreater(rodo21.nominal_escalonado_value, Decimal("10000.00"))
        self.assertEqual(rodo21.status, "ACTIVE")

        # Evento 4: Vencimento final Bullet com 100% de amortização
        receipts_ev4 = self.engine.process_schedule_event("RODO21", "EVT-RODO21-04")
        self.assertEqual(len(receipts_ev4), 2)

        # Após o resgate final Bullet, VNE unitário e global devem ser rigorosamente ZERO
        self.assertEqual(rodo21.nominal_escalonado_value, Decimal("0.00000000"))
        self.assertEqual(rodo21.global_nominal_escalonado_value, Decimal("0.00"))
        self.assertEqual(rodo21.status, "REDEEMED")

        # Verifica bônus de exclusão fiscal adicional de 30% para a emissora
        for r in receipts_ev4:
            expected_bonus = round_cents(r.gross_interest_amount * Decimal("0.30"))
            self.assertEqual(r.issuer_tax_deduction_bonus, expected_bonus)

        # Auditoria formal completa
        audit = self.engine.verify_invariants("RODO21")
        self.assertTrue(audit["debtor_balance_conservation_verified"])
        self.assertTrue(audit["multilateral_financial_conservation_verified"])
        self.assertGreater(audit["total_issuer_tax_bonus"], Decimal("0.00"))

    def test_variable_amortization_sanitation_flow(self) -> None:
        """Testa a debênture SANE31 com amortização variável até extinção completa."""
        sane31 = self.engine.get_emission("SANE31")

        # Processa todos os 5 eventos do cronograma
        all_receipts = self.engine.process_all_events("SANE31")
        self.assertEqual(len(all_receipts), 10)  # 5 eventos * 2 contas de custódia

        # Ao final do último evento (100% amortização restante), debênture deve estar resgatada
        self.assertEqual(sane31.nominal_escalonado_value, Decimal("0.00000000"))
        self.assertEqual(sane31.global_nominal_escalonado_value, Decimal("0.00"))
        self.assertEqual(sane31.status, "REDEEMED")

        audit = self.engine.verify_invariants("SANE31")
        self.assertEqual(audit["status"], "REDEEMED")
        self.assertTrue(audit["debtor_balance_conservation_verified"])
        self.assertTrue(audit["multilateral_financial_conservation_verified"])

    def test_double_event_processing_protection(self) -> None:
        """Garante que um mesmo evento não possa ser liquidado duas vezes."""
        self.engine.process_schedule_event("TRAN11", "EVT-TRAN11-01")
        with self.assertRaises(DebentureSettlementError):
            self.engine.process_schedule_event("TRAN11", "EVT-TRAN11-01")

    def test_custody_quantity_mismatch_detection(self) -> None:
        """Garante que discrepância entre contas e emissão levanta DebentureInvariantError."""
        invalid_emission = DebentureEmission(
            asset_code="FAIL11",
            isin="BRFAIL4DBS11",
            issuer_name="Empresa Teste Invariante S.A.",
            issuer_cnpj="00.000.000/0001-00",
            sector="ENERGIA",
            legal_framework=DebentureLegalFramework.LEI_12431_2011,
            amortization_type=AmortizationType.BULLET,
            emission_date=date(2025, 1, 1),
            maturity_date=date(2030, 1, 1),
            issued_quantity=Decimal("1000"),
            circulating_quantity=Decimal("1000"),
            nominal_original_value=Decimal("1000.00"),
            nominal_escalonado_value=Decimal("1000.00"),
            annual_coupon_rate=Decimal("0.06"),
            custody_positions=[
                CustodyPosition(
                    account_id="CUST-999",
                    investor_name="Investidor X",
                    investor_document="000.000.000-00",
                    investor_tax_category=InvestorTaxCategory.INDIVIDUAL_PF,
                    quantity=Decimal("950"),  # 950 != 1000 -> viola conservação
                )
            ],
        )
        with self.assertRaises(DebentureInvariantError):
            self.engine.add_emission(invalid_emission)

    def test_enum_parsing_helpers(self) -> None:
        """Testa robustez dos helpers de normalização de enums."""
        self.assertEqual(
            DebentureLegalFramework.from_str("Lei 12.431/2011"),
            DebentureLegalFramework.LEI_12431_2011,
        )
        self.assertEqual(
            DebentureLegalFramework.from_str("14801"),
            DebentureLegalFramework.LEI_14801_2024,
        )
        self.assertEqual(AmortizationType.from_str("BULLET"), AmortizationType.BULLET)
        self.assertEqual(AmortizationType.from_str("SAC"), AmortizationType.CONSTANT)
        self.assertEqual(AmortizationType.from_str("CUSTOM"), AmortizationType.VARIABLE)
        self.assertEqual(InvestorTaxCategory.from_str("PF"), InvestorTaxCategory.INDIVIDUAL_PF)
        self.assertEqual(InvestorTaxCategory.from_str("PJ"), InvestorTaxCategory.CORPORATE_PJ)
        self.assertEqual(InvestorTaxCategory.from_str("INR"), InvestorTaxCategory.NON_RESIDENT_INR)


if __name__ == "__main__":
    unittest.main()
