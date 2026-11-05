"""Testes Unitários e de Invariantes Formais para Comercialização de Energia Elétrica (FL-087).

Cobre:
- Registro de contratos bilaterais no ACL (Balcão NoMe B3 e CCEE);
- Modelagem de fontes de energia: Convencional (CONV), Incentivada 50% (I50) e 100% (I100);
- Submercados elétricos do SIN: SE/CO, Sul, Nordeste e Norte;
- Cálculo e conversão física: MWmédio para MWh no período/mês;
- Preço de Liquidação das Diferenças (PLD CCEE) com limites ANEEL;
- Marcação a Mercado (Mark-to-Market) bilateral com conservação soma-zero;
- Garantias financeiras, haircuts regulatórios e chamadas de margem;
- Trava fiduciária de recebíveis de energia no NoMe B3 e liquidação em conta escrow;
- Invariantes formais LAW-089, LAW-090, LAW-091 e LAW-092.
"""

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.electric_power_trading_ccee import (
    BilateralPowerContract,
    BilateralSettlementReceipt,
    CceeMcpBatchSettlement,
    CceeMcpSettlementPosition,
    CollateralDeposit,
    CollateralType,
    ContractNotFoundError,
    ContractStatus,
    DEFAULT_PLD_MAX_HOURLY,
    DEFAULT_PLD_MIN,
    ElectricPowerTradingEngine,
    ElectricSubmarket,
    EnergyType,
    FiduciaryAssignment,
    FiduciaryLockStatus,
    InvalidContractStateError,
    MarginCall,
    MarkToMarketResult,
    ModulationType,
    ParticipantCollateralProfile,
    PldLimitViolationError,
    PldRecord,
    PowerTradingError,
    PowerTradingValidationError,
    calculate_month_hours,
    calculate_period_hours,
    round_cents,
    round_energy,
)
from src.invariants import (
    InvariantViolationError,
    assert_fiduciary_assignment_limit,
    assert_power_energy_conservation,
    assert_power_non_negativity,
    assert_power_settlement_zero_sum,
)


class TestElectricPowerTradingCCEE(unittest.TestCase):
    """Bateria de testes completos do motor de comercialização de energia ACL / NoMe B3 / CCEE."""

    def setUp(self) -> None:
        self.fixture_path = (
            Path(__file__).resolve().parent.parent / "fixtures" / "electric_power_trading_ccee.json"
        )
        self.engine = ElectricPowerTradingEngine.load_from_fixture(self.fixture_path)

    # -----------------------------------------------------------------------
    # 1. Carregamento de Fixture e Invariantes
    # -----------------------------------------------------------------------

    def test_fixture_loading_and_invariants(self) -> None:
        """Garante que a fixture carregue com integridade e todas as invariantes formais passem."""
        report = self.engine.verify_invariants()
        self.assertTrue(report["all_invariants_hold"])
        self.assertTrue(report["energy_conservation_verified"])
        self.assertTrue(report["bilateral_settlement_zero_sum_verified"])
        self.assertTrue(report["non_negativity_verified"])
        self.assertTrue(report["fiduciary_limits_verified"])
        self.assertTrue(report["mtm_zero_sum_verified"])
        self.assertTrue(report["ccee_mcp_zero_sum_verified"])
        self.assertTrue(report["collateral_coverage_verified"])
        self.assertEqual(report["contracts_count"], 4)
        self.assertEqual(report["total_energy_bought_mwh"], "40920.0000")
        self.assertEqual(report["total_energy_sold_mwh"], "40920.0000")

    def test_serialization_roundtrip(self) -> None:
        """Valida que to_dict() e from_dict() preservem fielmente todo o estado operacional."""
        engine_dict = self.engine.to_dict()
        reconstituted = ElectricPowerTradingEngine.from_dict(engine_dict)
        report = reconstituted.verify_invariants()
        self.assertTrue(report["all_invariants_hold"])
        self.assertEqual(len(reconstituted.contracts), len(self.engine.contracts))
        self.assertEqual(len(reconstituted.collateral_profiles), len(self.engine.collateral_profiles))
        self.assertEqual(len(reconstituted.pld_records), len(self.engine.pld_records))
        self.assertEqual(len(reconstituted.settlement_receipts), len(self.engine.settlement_receipts))

    # -----------------------------------------------------------------------
    # 2. Conversão de Montante: MWmédio para MWh e Cálculo de Horas
    # -----------------------------------------------------------------------

    def test_calculate_month_hours_regular_and_leap(self) -> None:
        """Testa o cálculo exato de horas mensais para meses de 31, 30, 28 e 29 dias (bissexto)."""
        self.assertEqual(calculate_month_hours(2024, 1), 31 * 24)   # Jan: 744h
        self.assertEqual(calculate_month_hours(2024, 2), 29 * 24)   # Fev 2024 (bissexto): 696h
        self.assertEqual(calculate_month_hours(2023, 2), 28 * 24)   # Fev 2023 (normal): 672h
        self.assertEqual(calculate_month_hours(2024, 4), 30 * 24)   # Abr: 720h

        with self.assertRaises(PowerTradingValidationError):
            calculate_month_hours(2024, 13)

    def test_calculate_period_hours_and_volume_mwh(self) -> None:
        """Verifica a conversão de MWmédio para MWh baseada nas horas do período."""
        start = date(2024, 9, 1)
        end = date(2024, 9, 30)  # 30 dias = 720h
        hours = calculate_period_hours(start, end)
        self.assertEqual(hours, 720)

        mw_avg = Decimal("12.5000")
        expected_mwh = round_energy(mw_avg * Decimal(str(hours)))
        self.assertEqual(expected_mwh, Decimal("9000.0000"))

        with self.assertRaises(PowerTradingValidationError):
            calculate_period_hours(date(2024, 9, 30), date(2024, 9, 1))

    # -----------------------------------------------------------------------
    # 3. Modelagem de Contratos, Fontes e Submercados
    # -----------------------------------------------------------------------

    def test_energy_types_and_tusd_discounts(self) -> None:
        """Verifica os descontos na TUSD/TUST para CONV, I50 e I100."""
        self.assertEqual(EnergyType.CONV.tusd_discount_pct, Decimal("0.00"))
        self.assertEqual(EnergyType.I50.tusd_discount_pct, Decimal("50.00"))
        self.assertEqual(EnergyType.I100.tusd_discount_pct, Decimal("100.00"))

    def test_contract_registration_and_notional(self) -> None:
        """Valida o registro de novo contrato bilateral com cálculo automático de notional."""
        contract = self.engine.register_contract(
            contract_id="CTR-TEST-001",
            nome_registration_id="NOME-ENE-TEST-01",
            ccee_contract_code="CCEE-REG-TEST-01",
            buyer_id="COM-ENERGIA-SE-02",
            buyer_name="DELTA COMERCIALIZADORA DE ENERGIA S.A.",
            seller_id="GER-SOLAR-NE-01",
            seller_name="SOL DO SERTAO S.A.",
            energy_type=EnergyType.I50,
            submarket=ElectricSubmarket.NE,
            start_date=date(2024, 10, 1),
            end_date=date(2024, 10, 31),  # 31 dias = 744h
            mw_average=Decimal("10.0000"),
            contract_price_mwh=Decimal("175.50"),
        )
        self.assertEqual(contract.total_hours, 744)
        self.assertEqual(contract.volume_mwh, Decimal("7440.0000"))
        # Notional = 7440.0000 * 175.50 = 1.305.720,00
        self.assertEqual(contract.notional_amount, Decimal("1305720.00"))
        self.assertIn("CTR-TEST-001", self.engine.contracts)

    def test_contract_registration_duplicate_or_same_participant(self) -> None:
        """Rejeita duplicidade de identificador ou mesmo agente como comprador e vendedor."""
        with self.assertRaises(PowerTradingValidationError):
            self.engine.register_contract(
                contract_id="CTR-ACL-2024-001",  # Já existente na fixture
                nome_registration_id="NOME-NEW",
                ccee_contract_code="CCEE-NEW",
                buyer_id="AG1",
                buyer_name="Agente 1",
                seller_id="AG2",
                seller_name="Agente 2",
                energy_type=EnergyType.CONV,
                submarket=ElectricSubmarket.SE_CO,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                mw_average=Decimal("1.0000"),
                contract_price_mwh=Decimal("100.00"),
            )

        with self.assertRaises(PowerTradingValidationError):
            self.engine.register_contract(
                contract_id="CTR-SELF-TRADE",
                nome_registration_id="NOME-SELF",
                ccee_contract_code="CCEE-SELF",
                buyer_id="AG_SAME",
                buyer_name="Mesmo Agente",
                seller_id="AG_SAME",
                seller_name="Mesmo Agente",
                energy_type=EnergyType.CONV,
                submarket=ElectricSubmarket.SE_CO,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                mw_average=Decimal("1.0000"),
                contract_price_mwh=Decimal("100.00"),
            )

    # -----------------------------------------------------------------------
    # 4. Invariante de Não-Negatividade (LAW-091)
    # -----------------------------------------------------------------------

    def test_non_negativity_invariant_enforcement(self) -> None:
        """Garante que potências, horas, volumes e preços não sejam negativos."""
        # Sucesso
        assert_power_non_negativity(
            mw_average=Decimal("10.0000"),
            total_hours=720,
            volume_mwh=Decimal("7200.0000"),
            price_mwh=Decimal("150.00"),
            contract_id="VALID-001",
        )

        # Falha em MW médio negativo
        with self.assertRaises(InvariantViolationError):
            assert_power_non_negativity(
                mw_average=Decimal("-5.0000"),
                total_hours=720,
                volume_mwh=Decimal("3600.0000"),
                price_mwh=Decimal("150.00"),
                contract_id="INVALID-MW",
            )

        # Falha em preço negativo
        with self.assertRaises(InvariantViolationError):
            assert_power_non_negativity(
                mw_average=Decimal("5.0000"),
                total_hours=720,
                volume_mwh=Decimal("3600.0000"),
                price_mwh=Decimal("-10.00"),
                contract_id="INVALID-PRICE",
            )

    # -----------------------------------------------------------------------
    # 5. Preço de Liquidação das Diferenças (PLD CCEE)
    # -----------------------------------------------------------------------

    def test_pld_validation_and_regulatory_limits(self) -> None:
        """Testa o registro de PLD e conformidade com pisos e tetos da ANEEL."""
        # Válido
        pld = self.engine.register_pld(
            submarket=ElectricSubmarket.SE_CO,
            reference_date=date(2024, 9, 1),
            average_price=Decimal("140.00"),
            min_limit=DEFAULT_PLD_MIN,
            max_limit=DEFAULT_PLD_MAX_HOURLY,
        )
        self.assertEqual(pld.average_price, Decimal("140.00"))

        # Violação do piso regulatório (abaixo do PLD Min)
        with self.assertRaises(PldLimitViolationError):
            self.engine.register_pld(
                submarket=ElectricSubmarket.SE_CO,
                reference_date=date(2024, 9, 2),
                average_price=Decimal("45.00"),  # < 61.07
                min_limit=DEFAULT_PLD_MIN,
            )

        # Violação do teto regulatório (acima do PLD Max)
        with self.assertRaises(PldLimitViolationError):
            self.engine.register_pld(
                submarket=ElectricSubmarket.SE_CO,
                reference_date=date(2024, 9, 3),
                average_price=Decimal("1800.00"),  # > 1470.32
                max_limit=DEFAULT_PLD_MAX_HOURLY,
            )

    # -----------------------------------------------------------------------
    # 6. Marcação a Mercado (Mark-to-Market - MtM) e Soma-Zero
    # -----------------------------------------------------------------------

    def test_mark_to_market_calculation_and_zero_sum(self) -> None:
        """Verifica o cálculo de MtM com simetria e conservação exata de soma-zero."""
        # CTR-ACL-2024-001: 7440 MWh, preço contratado R$ 180,00, curva forward SE_CO/CONV R$ 192,50
        # Dif = +12.50 R$/MWh
        # Buyer MtM = 7440 * 12.50 = +93.000,00 R$
        # Seller MtM = 7440 * (-12.50) = -93.000,00 R$
        mtm = self.engine.calculate_mtm("CTR-ACL-2024-001", valuation_date=date(2024, 8, 15))
        self.assertEqual(mtm.price_differential_mwh, Decimal("12.50"))
        self.assertEqual(mtm.buyer_mtm, Decimal("93000.00"))
        self.assertEqual(mtm.seller_mtm, Decimal("-93000.00"))
        self.assertTrue(mtm.is_zero_sum)
        self.assertEqual(mtm.buyer_mtm + mtm.seller_mtm, Decimal("0.00"))

    def test_portfolio_mtm_calculation(self) -> None:
        """Verifica o cálculo de MtM para todo o portfólio de contratos ativos."""
        results = self.engine.calculate_portfolio_mtm(valuation_date=date(2024, 8, 15))
        self.assertGreater(len(results), 0)
        for res in results:
            self.assertTrue(res.is_zero_sum)
            self.assertEqual(res.buyer_mtm + res.seller_mtm, Decimal("0.00"))

    # -----------------------------------------------------------------------
    # 7. Garantias Financeiras e Gestão de Margem
    # -----------------------------------------------------------------------

    def test_collateral_deposits_and_evaluated_haircuts(self) -> None:
        """Testa depósitos de garantias e aplicação correta de haircuts regulatórios."""
        profile = self.engine.register_participant("NOVO-PARTICIPANTE", "Novo Agente S.A.", "AG-NOVO")
        
        # Cash BRL (0% haircut)
        dep_cash = self.engine.deposit_collateral(
            deposit_id="DEP-CASH-01",
            participant_id="NOVO-PARTICIPANTE",
            collateral_type=CollateralType.CASH_BRL,
            nominal_amount=Decimal("100000.00"),
        )
        self.assertEqual(dep_cash.evaluated_amount, Decimal("100000.00"))

        # TPF (5% haircut)
        dep_tpf = self.engine.deposit_collateral(
            deposit_id="DEP-TPF-01",
            participant_id="NOVO-PARTICIPANTE",
            collateral_type=CollateralType.SOVEREIGN_BOND,
            nominal_amount=Decimal("100000.00"),
            haircut_pct=Decimal("5.00"),
        )
        self.assertEqual(dep_tpf.evaluated_amount, Decimal("95000.00"))

        self.assertEqual(profile.total_collateral_nominal, Decimal("200000.00"))
        self.assertEqual(profile.total_collateral_evaluated, Decimal("195000.00"))

    def test_margin_requirement_and_margin_call(self) -> None:
        """Verifica emissão automática de Margin Call em caso de insuficiência patrimonial."""
        self.engine.register_participant("PARTICIPANTE-DEFICIT", "Agente em Déficit S.A.", "AG-DEF")
        self.engine.deposit_collateral(
            deposit_id="DEP-DEF-01",
            participant_id="PARTICIPANTE-DEFICIT",
            collateral_type=CollateralType.CASH_BRL,
            nominal_amount=Decimal("195000.00"),
        )
        # Exigência de R$ 250.000,00 quando o participante avaliado tem apenas R$ 195.000,00
        mc = self.engine.set_margin_requirement(
            participant_id="PARTICIPANTE-DEFICIT",
            required_margin=Decimal("250000.00"),
            evaluation_date=date(2024, 8, 20),
        )
        self.assertIsNotNone(mc)
        self.assertEqual(mc.deficit_amount, Decimal("55000.00"))
        self.assertEqual(mc.status, "PENDING")

    # -----------------------------------------------------------------------
    # 8. Trava Fiduciária de Recebíveis no NoMe B3 (LAW-092)
    # -----------------------------------------------------------------------

    def test_fiduciary_assignment_registration_and_limit(self) -> None:
        """Valida constituição de trava fiduciária e limite máximo sobre o valor do contrato."""
        contract = self.engine.contracts["CTR-ACL-2024-001"]
        # Nocional = 1.339.200,00

        # Trava válida de R$ 1.000.000,00 <= R$ 1.339.200,00
        assignment = self.engine.register_fiduciary_assignment(
            assignment_id="TRV-TEST-001",
            contract_id="CTR-ACL-2024-001",
            beneficiary_creditor_id="BCO-SANTANDER",
            beneficiary_creditor_name="BANCO SANTANDER BRASIL S.A.",
            escrow_account_id="ESCROW-SANT-991",
            secured_debt_amount=Decimal("1000000.00"),
            retention_percentage=Decimal("100.00"),
        )
        self.assertEqual(assignment.secured_debt_amount, Decimal("1000000.00"))

        # Violação de Limite (LAW-092): tentar travar valor superior ao contrato
        with self.assertRaises(InvariantViolationError):
            assert_fiduciary_assignment_limit(
                assigned_amount=Decimal("2000000.00"),
                total_contract_value=contract.notional_amount,
                contract_id="CTR-ACL-2024-001",
            )

    # -----------------------------------------------------------------------
    # 9. Liquidação Financeira Bilateral e Destinação Fiduciária (LAW-090)
    # -----------------------------------------------------------------------

    def test_bilateral_settlement_with_fiduciary_retention(self) -> None:
        """Testa a liquidação financeira com direcionamento fiduciário para conta escrow."""
        # CTR-ACL-2024-003: 3720 MWh * 210,00 = R$ 781.200,00
        # Trava fiduciária: dívida de R$ 500.000,00, retenção de 60%
        # Retenção calculada = 781.200,00 * 60% = R$ 468.720,00 <= R$ 500.000,00
        # Seller net credit = 781.200,00 - 468.720,00 = R$ 312.480,00
        # Buyer debit = R$ 781.200,00
        # Verificação: 781.200,00 == 468.720,00 + 312.480,00 (Soma-Zero centavo a centavo!)
        receipt = self.engine.process_bilateral_settlement(
            contract_id="CTR-ACL-2024-003",
            settlement_date=date(2024, 8, 31),
        )
        self.assertEqual(receipt.gross_amount, Decimal("781200.00"))
        self.assertEqual(receipt.buyer_debit, Decimal("781200.00"))
        self.assertEqual(receipt.fiduciary_retained_amount, Decimal("468720.00"))
        self.assertEqual(receipt.seller_net_credit, Decimal("312480.00"))
        self.assertTrue(receipt.is_zero_sum)
        self.assertEqual(receipt.buyer_debit, receipt.fiduciary_retained_amount + receipt.seller_net_credit)

        contract = self.engine.contracts["CTR-ACL-2024-003"]
        self.assertEqual(contract.status, ContractStatus.SETTLED)

        # Re-liquidação de contrato já liquidado deve falhar
        with self.assertRaises(InvalidContractStateError):
            self.engine.process_bilateral_settlement("CTR-ACL-2024-003")

    def test_bilateral_settlement_without_fiduciary_lock(self) -> None:
        """Testa liquidação de contrato sem trava fiduciária (crédito integral ao vendedor)."""
        # CTR-ACL-2024-002: 18600 MWh * 165,00 = R$ 3.069.000,00
        receipt = self.engine.process_bilateral_settlement(
            contract_id="CTR-ACL-2024-002",
            settlement_date=date(2024, 8, 31),
        )
        self.assertEqual(receipt.fiduciary_retained_amount, Decimal("0.00"))
        self.assertEqual(receipt.seller_net_credit, Decimal("3069000.00"))
        self.assertEqual(receipt.buyer_debit, Decimal("3069000.00"))
        self.assertTrue(receipt.is_zero_sum)

    # -----------------------------------------------------------------------
    # 10. Conservação de Energia (LAW-089) e CCEE MCP Settlement
    # -----------------------------------------------------------------------

    def test_energy_conservation_invariant(self) -> None:
        """Verifica que total de energia vendida é rigorosamente igual à comprada."""
        assert_power_energy_conservation(
            total_mwh_sold=Decimal("15000.0000"),
            total_mwh_bought=Decimal("15000.0000"),
            contract_id="CTR-BALANCED-01",
        )

        with self.assertRaises(InvariantViolationError):
            assert_power_energy_conservation(
                total_mwh_sold=Decimal("15000.0000"),
                total_mwh_bought=Decimal("14999.0000"),
                contract_id="CTR-UNBALANCED-02",
            )

    def test_ccee_mcp_settlement_conservation(self) -> None:
        """Valida que o lote de liquidação multilateral do MCP na CCEE conserva energia e finanças."""
        batch = self.engine.ccee_mcp_settlements[0]
        self.assertEqual(batch.net_energy_balance_mwh, Decimal("0.0000"))
        self.assertEqual(batch.total_credits, Decimal("135540.00"))
        self.assertEqual(batch.total_debits, Decimal("135540.00"))
        self.assertEqual(batch.net_financial_imbalance, Decimal("0.00"))

    def test_fiduciary_assignment_full_amortization_and_release(self) -> None:
        """Valida a amortização integral da dívida garantida e alteração de status para RELEASED."""
        # CTR-ACL-2024-001: R$ 1.339.200,00, dívida fiduciária R$ 800.000,00, retenção 100%
        contract = self.engine.contracts["CTR-ACL-2024-001"]
        self.assertEqual(contract.fiduciary_assignment.status, FiduciaryLockStatus.ACTIVE)
        self.assertEqual(contract.fiduciary_assignment.secured_debt_amount, Decimal("800000.00"))

        receipt = self.engine.process_bilateral_settlement(
            contract_id="CTR-ACL-2024-001",
            settlement_date=date(2024, 8, 31),
        )
        # Fatura de 1.339.200,00 retém 800.000,00 para quitar a dívida
        self.assertEqual(receipt.fiduciary_retained_amount, Decimal("800000.00"))
        self.assertEqual(receipt.seller_net_credit, Decimal("539200.00"))
        self.assertEqual(contract.fiduciary_assignment.secured_debt_amount, Decimal("0.00"))
        self.assertEqual(contract.fiduciary_assignment.status, FiduciaryLockStatus.RELEASED)
        self.assertTrue(receipt.is_zero_sum)

    def test_fiduciary_assignment_fixed_retention_amount(self) -> None:
        """Valida cálculo de retenção fiduciária com montante fixo estipulado."""
        fa = FiduciaryAssignment(
            assignment_id="TRV-FIXED-01",
            contract_id="CTR-TEST",
            beneficiary_creditor_id="BCO-01",
            beneficiary_creditor_name="Banco 01",
            escrow_account_id="ESCROW-01",
            secured_debt_amount=Decimal("100000.00"),
            fixed_retention_amount=Decimal("30000.00"),
        )
        # Se fatura for 50.000,00, retém 30.000,00
        self.assertEqual(fa.calculate_retention(Decimal("50000.00")), Decimal("30000.00"))
        # Se fatura for 20.000,00, retém no máximo a fatura (20.000,00)
        self.assertEqual(fa.calculate_retention(Decimal("20000.00")), Decimal("20000.00"))

    def test_tampered_invariants_failure_detection(self) -> None:
        """Garante que qualquer inconsistência induzida quebre verify_invariants."""
        # Criar engine cópia
        engine_dict = self.engine.to_dict()
        engine_corrupt = ElectricPowerTradingEngine.from_dict(engine_dict)

        # Corromper um recebimento de liquidação tornando-o não soma-zero
        corrupt_receipt = engine_corrupt.settlement_receipts[0]
        corrupt_receipt.buyer_debit += Decimal("1000.00")

        with self.assertRaises(InvariantViolationError):
            engine_corrupt.verify_invariants()


if __name__ == "__main__":
    unittest.main()
