"""Bateria de testes automatizados para Derivativos de Balcão e Opções Flexíveis (FL-016 e FL-017) — B3 NoMe."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.nome_derivatives import (
    BarrierType,
    BarrierValidationError,
    ChronologicalDateError,
    DayCountConvention,
    DerivativesRegistrationEngine,
    DerivativesValidationError,
    ExerciseStyle,
    FlexibleOptionContract,
    GuaranteeType,
    GuaranteeValidationError,
    IncompatibleCurrencyError,
    IndexerType,
    InvalidLegConfigurationError,
    LegType,
    MissingRequiredFieldError,
    NegativeOrZeroValueError,
    OptionPayoffResult,
    OptionType,
    ReasonCode,
    RegistrationStatus,
    SettlementType,
    SettlementValidationError,
    SwapContract,
    SwapLeg,
    SwapPayoffResult,
    UnderlyingType,
    ValidationResult,
)

FIXTURES_DIR = ROOT_DIR / "fixtures"


class TestNomeDerivatives(unittest.TestCase):
    """Testes unitários e de integração do motor de derivativos NoMe."""

    def setUp(self) -> None:
        self.engine_strict = DerivativesRegistrationEngine(raise_on_validation_failure=True)
        self.engine_lenient = DerivativesRegistrationEngine(raise_on_validation_failure=False)
        self.swap_fixture_path = FIXTURES_DIR / "nome_swap_di_pre.json"
        self.option_fixture_path = FIXTURES_DIR / "nome_flex_option.json"

    # =======================================================================
    # Testes de Swaps de Balcão (FL-016)
    # =======================================================================

    def test_swap_di_pre_from_fixture(self) -> None:
        """Valida o carregamento, estrutura e registro do Swap DI x Pré oficial."""
        self.assertTrue(self.swap_fixture_path.exists(), "Fixture nome_swap_di_pre.json deve existir.")
        contract = SwapContract.from_file(self.swap_fixture_path)

        # 1. Validações de atributos do contrato
        self.assertEqual(contract.contract_id, "SWP-20261103-0001")
        self.assertEqual(contract.trade_date, date(2026, 11, 3))
        self.assertEqual(contract.start_date, date(2026, 11, 4))
        self.assertEqual(contract.maturity_date, date(2027, 11, 4))
        self.assertEqual(contract.business_days, 252)
        self.assertEqual(contract.calendar_days, 365)
        self.assertEqual(contract.notional_amount, Decimal("10000000.00"))
        self.assertEqual(contract.quantity, Decimal("100"))
        self.assertEqual(contract.pu_initial, Decimal("100000.00"))
        self.assertEqual(contract.currency, "BRL")
        self.assertIsNone(contract.foreign_currency)
        self.assertIsNone(contract.initial_fx_rate)
        self.assertEqual(contract.guarantee_type, GuaranteeType.WITH_CCP_GUARANTEE)
        self.assertEqual(contract.clearing_member_active, 105)
        self.assertEqual(contract.clearing_member_passive, 88)
        self.assertEqual(contract.party_active, "PART-BR-00105")
        self.assertEqual(contract.party_passive, "PART-BR-00088")
        self.assertFalse(contract.periodic_adjustments)

        # 2. Validações das pontas
        self.assertEqual(contract.active_leg.leg_type, LegType.ACTIVE)
        self.assertEqual(contract.active_leg.indexer, IndexerType.CDI)
        self.assertEqual(contract.active_leg.day_count_convention, DayCountConvention.DU_252)
        self.assertEqual(contract.active_leg.rate_or_spread, Decimal("1.0000"))
        self.assertEqual(contract.active_leg.pu_initial, Decimal("100000.00"))
        self.assertEqual(contract.active_leg.currency, "BRL")

        self.assertEqual(contract.passive_leg.leg_type, LegType.PASSIVE)
        self.assertEqual(contract.passive_leg.indexer, IndexerType.PRE)
        self.assertEqual(contract.passive_leg.day_count_convention, DayCountConvention.DU_252)
        self.assertEqual(contract.passive_leg.rate_or_spread, Decimal("0.1250"))
        self.assertEqual(contract.passive_leg.pu_initial, Decimal("100000.00"))
        self.assertEqual(contract.passive_leg.currency, "BRL")

        # 3. Registro e transição de estado
        val_res = self.engine_strict.validate_swap(contract)
        self.assertTrue(val_res.is_valid)
        self.assertEqual(val_res.reason_code, ReasonCode.OK)

        registered = self.engine_strict.register_swap(contract)
        self.assertEqual(registered.status, RegistrationStatus.REGISTERED)
        self.assertEqual(registered.reason_code, ReasonCode.OK)

        # 4. Serialização e roundtrip
        contract_dict = registered.to_dict()
        self.assertEqual(contract_dict["contract_id"], "SWP-20261103-0001")
        self.assertEqual(contract_dict["status"], "REGISTERED")
        rebuilt = SwapContract.from_dict(contract_dict)
        self.assertEqual(rebuilt.contract_id, contract.contract_id)
        self.assertEqual(rebuilt.notional_amount, contract.notional_amount)

    def test_swap_deterministic_payoff_and_cashflow(self) -> None:
        """Valida o cálculo determinístico do nocional e do fluxo de caixa no vencimento."""
        contract = SwapContract.from_file(self.swap_fixture_path)

        # 1. Cálculo de Nocional
        notional_calc = self.engine_strict.calculate_swap_notional(contract)
        self.assertEqual(notional_calc, Decimal("10000000.00"))

        # 2. Fator da ponta pré-fixada (12.50% em 252 DU)
        # (1 + 0.1250) ** (252 / 252) = 1.12500000
        factor_pre = self.engine_strict.calculate_fixed_leg_factor(
            rate=Decimal("0.1250"),
            business_days=252,
            convention=DayCountConvention.DU_252,
        )
        self.assertEqual(factor_pre, Decimal("1.12500000"))

        # 3. Cenário A: CDI realizado acumulado superior à taxa pré (13.50% acumulado)
        factor_cdi_scenario_a = Decimal("1.13500000")
        payoff_a = self.engine_strict.calculate_swap_payoff(
            contract=contract,
            active_realized_factor=factor_cdi_scenario_a,
            passive_realized_factor=factor_pre,
        )

        self.assertEqual(payoff_a.active_final_value, Decimal("11350000.00"))
        self.assertEqual(payoff_a.passive_final_value, Decimal("11250000.00"))
        self.assertEqual(payoff_a.net_cashflow_active, Decimal("100000.00"))
        self.assertEqual(payoff_a.receiving_party, "PART-BR-00105")
        self.assertEqual(payoff_a.paying_party, "PART-BR-00088")
        self.assertTrue(payoff_a.is_bullet_settled)

        # 4. Cenário B: CDI realizado inferior à taxa pré (11.00% acumulado)
        factor_cdi_scenario_b = Decimal("1.11000000")
        payoff_b = self.engine_strict.calculate_swap_payoff(
            contract=contract,
            active_realized_factor=factor_cdi_scenario_b,
            passive_realized_factor=factor_pre,
        )

        self.assertEqual(payoff_b.active_final_value, Decimal("11100000.00"))
        self.assertEqual(payoff_b.passive_final_value, Decimal("11250000.00"))
        self.assertEqual(payoff_b.net_cashflow_active, Decimal("-150000.00"))
        self.assertEqual(payoff_b.receiving_party, "PART-BR-00088")
        self.assertEqual(payoff_b.paying_party, "PART-BR-00105")

        # 5. Cenário C: Empate perfeito (Fator ativo == Fator passivo)
        payoff_c = self.engine_strict.calculate_swap_payoff(
            contract=contract,
            active_realized_factor=factor_pre,
            passive_realized_factor=factor_pre,
        )
        self.assertEqual(payoff_c.net_cashflow_active, Decimal("0.00"))
        self.assertEqual(payoff_c.receiving_party, "NONE")
        self.assertEqual(payoff_c.paying_party, "NONE")
        self.assertFalse(payoff_c.is_bullet_settled)

    def test_swap_rejection_identical_legs(self) -> None:
        """Rejeita swap cujas pontas ativa e passiva são economicamente idênticas."""
        contract = SwapContract.from_file(self.swap_fixture_path)

        # Altera a ponta passiva para ser idêntica à ponta ativa
        contract.passive_leg.indexer = IndexerType.CDI
        contract.passive_leg.rate_or_spread = Decimal("1.0000")
        contract.passive_leg.currency = "BRL"

        with self.assertRaises(InvalidLegConfigurationError) as ctx:
            self.engine_strict.validate_swap(contract)
        self.assertEqual(ctx.exception.reason_code, ReasonCode.ERR_IDENTICAL_LEGS)

        # Com motor não estrito, retorna ValidationResult com falha
        res = self.engine_lenient.validate_swap(contract)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.reason_code, ReasonCode.ERR_IDENTICAL_LEGS)

    def test_swap_rejection_incompatible_currencies(self) -> None:
        """Rejeita swap com moedas distintas entre as pontas sem definição de foreign_currency ou FX rate."""
        contract = SwapContract.from_file(self.swap_fixture_path)

        # Ponta passiva em USD sem foreign_currency declarada
        contract.passive_leg.currency = "USD"
        contract.foreign_currency = None
        contract.initial_fx_rate = None

        with self.assertRaises(IncompatibleCurrencyError) as ctx:
            self.engine_strict.validate_swap(contract)
        self.assertEqual(ctx.exception.reason_code, ReasonCode.ERR_INCOMPATIBLE_CURRENCY)

        # Informa foreign_currency mas deixa initial_fx_rate nulo ou zero
        contract.foreign_currency = "USD"
        contract.initial_fx_rate = Decimal("0")

        with self.assertRaises(IncompatibleCurrencyError) as ctx:
            self.engine_strict.validate_swap(contract)
        self.assertEqual(ctx.exception.reason_code, ReasonCode.ERR_INCOMPATIBLE_CURRENCY)

        # Com initial_fx_rate válido, deve ser aceito
        contract.initial_fx_rate = Decimal("5.6540")
        val_res = self.engine_strict.validate_swap(contract)
        self.assertTrue(val_res.is_valid)

    def test_swap_rejection_negative_and_zero_values(self) -> None:
        """Rejeita montantes nocionais, quantidades ou PUs nulos ou negativos."""
        # Nocional zero
        contract = SwapContract.from_file(self.swap_fixture_path)
        contract.notional_amount = Decimal("0")
        with self.assertRaises(NegativeOrZeroValueError):
            self.engine_strict.validate_swap(contract)

        # Quantidade negativa
        contract = SwapContract.from_file(self.swap_fixture_path)
        contract.quantity = Decimal("-10")
        with self.assertRaises(NegativeOrZeroValueError):
            self.engine_strict.validate_swap(contract)

        # PU inicial da ponta ativa zero
        contract = SwapContract.from_file(self.swap_fixture_path)
        contract.active_leg.pu_initial = Decimal("0")
        with self.assertRaises(NegativeOrZeroValueError):
            self.engine_strict.validate_swap(contract)

    def test_swap_rejection_chronological_date_errors(self) -> None:
        """Rejeita contratos com violações da ordem cronológica de datas."""
        # start_date anterior a trade_date
        contract = SwapContract.from_file(self.swap_fixture_path)
        contract.start_date = date(2026, 11, 2)
        contract.trade_date = date(2026, 11, 3)
        with self.assertRaises(ChronologicalDateError):
            self.engine_strict.validate_swap(contract)

        # maturity_date anterior a start_date
        contract = SwapContract.from_file(self.swap_fixture_path)
        contract.maturity_date = date(2026, 11, 1)
        with self.assertRaises(ChronologicalDateError):
            self.engine_strict.validate_swap(contract)

        # Dias úteis menores que 1
        contract = SwapContract.from_file(self.swap_fixture_path)
        contract.business_days = 0
        with self.assertRaises(ChronologicalDateError):
            self.engine_strict.validate_swap(contract)

        # Dias corridos menores que dias úteis
        contract = SwapContract.from_file(self.swap_fixture_path)
        contract.calendar_days = 200
        contract.business_days = 252
        with self.assertRaises(ChronologicalDateError):
            self.engine_strict.validate_swap(contract)

    def test_swap_ccp_guarantee_validations(self) -> None:
        """Valida que registro com garantia central CCP B3 exige clearing members válidos."""
        contract = SwapContract.from_file(self.swap_fixture_path)
        contract.guarantee_type = GuaranteeType.WITH_CCP_GUARANTEE

        # Falta clearing_member_active
        contract.clearing_member_active = None
        with self.assertRaises(GuaranteeValidationError) as ctx:
            self.engine_strict.validate_swap(contract)
        self.assertEqual(ctx.exception.reason_code, ReasonCode.ERR_MISSING_CLEARING_MEMBER)

        # Clearing member passivo inválido (<= 0)
        contract.clearing_member_active = 105
        contract.clearing_member_passive = 0
        with self.assertRaises(GuaranteeValidationError) as ctx:
            self.engine_strict.validate_swap(contract)
        self.assertEqual(ctx.exception.reason_code, ReasonCode.ERR_INVALID_CLEARING_MEMBER)

        # Registro bilateral (sem garantia) não exige clearing members
        contract.guarantee_type = GuaranteeType.WITHOUT_GUARANTEE
        contract.clearing_member_active = None
        contract.clearing_member_passive = None
        val_res = self.engine_strict.validate_swap(contract)
        self.assertTrue(val_res.is_valid)

    # =======================================================================
    # Testes de Opções Flexíveis de Balcão (FL-017)
    # =======================================================================

    def test_flexible_option_from_fixture(self) -> None:
        """Valida o carregamento, estrutura e registro da Opção Flexível oficial."""
        self.assertTrue(self.option_fixture_path.exists(), "Fixture nome_flex_option.json deve existir.")
        contract = FlexibleOptionContract.from_file(self.option_fixture_path)

        # 1. Atributos da opção
        self.assertEqual(contract.contract_id, "OPT-20261103-0002")
        self.assertEqual(contract.trade_date, date(2026, 11, 3))
        self.assertEqual(contract.premium_payment_date, date(2026, 11, 4))
        self.assertEqual(contract.expiration_date, date(2027, 2, 18))
        self.assertEqual(contract.underlying_asset, "PETR4")
        self.assertEqual(contract.underlying_type, UnderlyingType.EQUITY)
        self.assertEqual(contract.option_type, OptionType.CALL)
        self.assertEqual(contract.exercise_style, ExerciseStyle.EUROPEAN)
        self.assertEqual(contract.strike_price, Decimal("42.00"))
        self.assertEqual(contract.premium, Decimal("2.15"))
        self.assertEqual(contract.quantity, Decimal("50000"))
        self.assertEqual(contract.settlement_type, SettlementType.FINANCIAL)
        self.assertEqual(contract.barrier_type, BarrierType.UP_AND_OUT)
        self.assertEqual(contract.barrier_level, Decimal("52.00"))
        self.assertEqual(contract.guarantee_type, GuaranteeType.WITH_CCP_GUARANTEE)
        self.assertEqual(contract.clearing_member_holder, 105)
        self.assertEqual(contract.clearing_member_writer, 72)
        self.assertEqual(contract.party_holder, "FUNDO-ALPHA-FIA")
        self.assertEqual(contract.party_writer, "TESOURARIA-BETA-BANCO")
        self.assertEqual(contract.currency, "BRL")

        # 2. Cálculos derivados
        # Total premium = 2.15 * 50,000 = 107,500.00
        self.assertEqual(contract.total_premium(), Decimal("107500.00"))
        # Notional = 42.00 * 50,000 = 2,100,000.00
        self.assertEqual(contract.notional_underlying(), Decimal("2100000.00"))

        # 3. Validação e Registro
        val_res = self.engine_strict.validate_flexible_option(contract)
        self.assertTrue(val_res.is_valid)

        registered = self.engine_strict.register_flexible_option(contract)
        self.assertEqual(registered.status, RegistrationStatus.REGISTERED)
        self.assertEqual(registered.reason_code, ReasonCode.OK)

        # 4. Serialização e roundtrip
        opt_dict = registered.to_dict()
        self.assertEqual(opt_dict["contract_id"], "OPT-20261103-0002")
        self.assertEqual(opt_dict["status"], "REGISTERED")
        rebuilt = FlexibleOptionContract.from_dict(opt_dict)
        self.assertEqual(rebuilt.contract_id, contract.contract_id)
        self.assertEqual(rebuilt.total_premium(), Decimal("107500.00"))

    def test_flexible_option_payoff_scenarios(self) -> None:
        """Testa múltiplos cenários de apuração de payoff e disparo de barreira."""
        contract = FlexibleOptionContract.from_file(self.option_fixture_path)

        # Cenário 1: No dinheiro (In-The-Money) sem violar barreira
        # Strike: 42.00, Spot no vencimento: 45.00, Barreira Up-and-Out: 52.00, Máxima: 46.00
        payoff_itm = self.engine_strict.calculate_option_payoff(
            contract=contract,
            spot_price_at_expiry=Decimal("45.00"),
            highest_price_during_life=Decimal("46.00"),
        )
        self.assertTrue(payoff_itm.is_exercised)
        self.assertFalse(payoff_itm.is_barrier_breached)
        self.assertEqual(payoff_itm.unit_payoff, Decimal("3.00"))
        # 3.00 * 50,000 = 150,000.00
        self.assertEqual(payoff_itm.total_payoff, Decimal("150000.00"))

        # Cenário 2: Fora do dinheiro (Out-of-The-Money)
        # Spot no vencimento: 39.00 < 42.00
        payoff_otm = self.engine_strict.calculate_option_payoff(
            contract=contract,
            spot_price_at_expiry=Decimal("39.00"),
            highest_price_during_life=Decimal("41.00"),
        )
        self.assertFalse(payoff_otm.is_exercised)
        self.assertFalse(payoff_otm.is_barrier_breached)
        self.assertEqual(payoff_otm.unit_payoff, Decimal("0.00"))
        self.assertEqual(payoff_otm.total_payoff, Decimal("0.00"))

        # Cenário 3: Violação de Barreira Up-and-Out (Knock-out)
        # Spot no vencimento: 48.00 (teria R$ 6.00 de intrinsic), mas bateu 53.00 durante a vida (> 52.00)
        payoff_knockout = self.engine_strict.calculate_option_payoff(
            contract=contract,
            spot_price_at_expiry=Decimal("48.00"),
            highest_price_during_life=Decimal("53.00"),
        )
        self.assertFalse(payoff_knockout.is_exercised)
        self.assertTrue(payoff_knockout.is_barrier_breached)
        self.assertEqual(payoff_knockout.unit_payoff, Decimal("0.00"))
        self.assertEqual(payoff_knockout.total_payoff, Decimal("0.00"))

    def test_flexible_put_option_down_and_in_barrier(self) -> None:
        """Testa opção flexível de venda (PUT) com barreira ativadora Down-and-In."""
        put_contract = FlexibleOptionContract(
            contract_id="OPT-PUT-001",
            trade_date=date(2026, 11, 3),
            expiration_date=date(2027, 5, 20),
            underlying_asset="VALE3",
            underlying_type=UnderlyingType.EQUITY,
            option_type=OptionType.PUT,
            exercise_style=ExerciseStyle.EUROPEAN,
            strike_price=Decimal("60.00"),
            premium=Decimal("3.50"),
            quantity=Decimal("10000"),
            settlement_type=SettlementType.FINANCIAL,
            barrier_type=BarrierType.DOWN_AND_IN,
            barrier_level=Decimal("50.00"),
            guarantee_type=GuaranteeType.WITHOUT_GUARANTEE,
            party_holder="INVESTOR-PUT-HOLDER",
            party_writer="BANK-PUT-WRITER",
        )

        # 1. Barreira NÃO atingida (mínima foi 52.00 > 50.00) -> Opção NÃO ativada
        # Spot no vencimento: 52.00 (< Strike 60.00), mas como barreira 50.00 não foi tocada, não ativa
        payoff_inactive = self.engine_strict.calculate_option_payoff(
            contract=put_contract,
            spot_price_at_expiry=Decimal("52.00"),
            lowest_price_during_life=Decimal("52.00"),
        )
        self.assertFalse(payoff_inactive.is_exercised)
        self.assertFalse(payoff_inactive.is_barrier_breached)
        self.assertEqual(payoff_inactive.total_payoff, Decimal("0.00"))

        # 2. Barreira ATINGIDA (mínima foi 49.00 <= 50.00) -> Opção ativada e exercida
        # Spot no vencimento: 48.00. Strike: 60.00. Intrinsic: 60.00 - 48.00 = 12.00
        payoff_active = self.engine_strict.calculate_option_payoff(
            contract=put_contract,
            spot_price_at_expiry=Decimal("48.00"),
            lowest_price_during_life=Decimal("49.00"),
        )
        self.assertTrue(payoff_active.is_exercised)
        self.assertTrue(payoff_active.is_barrier_breached)
        self.assertEqual(payoff_active.unit_payoff, Decimal("12.00"))
        # 12.00 * 10,000 = 120,000.00
        self.assertEqual(payoff_active.total_payoff, Decimal("120000.00"))

    def test_flexible_option_validation_rejections(self) -> None:
        """Valida as rejeições de integridade para opções flexíveis."""
        contract = FlexibleOptionContract.from_file(self.option_fixture_path)

        # Strike <= 0
        contract.strike_price = Decimal("0")
        with self.assertRaises(NegativeOrZeroValueError):
            self.engine_strict.validate_flexible_option(contract)

        # Quantidade <= 0
        contract = FlexibleOptionContract.from_file(self.option_fixture_path)
        contract.quantity = Decimal("-50")
        with self.assertRaises(NegativeOrZeroValueError):
            self.engine_strict.validate_flexible_option(contract)

        # Vencimento anterior à data de negociação
        contract = FlexibleOptionContract.from_file(self.option_fixture_path)
        contract.expiration_date = date(2026, 11, 2)
        with self.assertRaises(ChronologicalDateError):
            self.engine_strict.validate_flexible_option(contract)

        # Data de prêmio posterior ao vencimento
        contract = FlexibleOptionContract.from_file(self.option_fixture_path)
        contract.premium_payment_date = date(2027, 3, 1)
        with self.assertRaises(ChronologicalDateError):
            self.engine_strict.validate_flexible_option(contract)

        # Barreira informada sem nível (level)
        contract = FlexibleOptionContract.from_file(self.option_fixture_path)
        contract.barrier_type = BarrierType.UP_AND_OUT
        contract.barrier_level = None
        with self.assertRaises(BarrierValidationError):
            self.engine_strict.validate_flexible_option(contract)

        # Liquidação física para ativo do tipo moeda (CURRENCY)
        contract = FlexibleOptionContract.from_file(self.option_fixture_path)
        contract.underlying_asset = "USD"
        contract.underlying_type = UnderlyingType.CURRENCY
        contract.settlement_type = SettlementType.PHYSICAL
        with self.assertRaises(SettlementValidationError):
            self.engine_strict.validate_flexible_option(contract)

        # Garantia central CCP sem clearing member
        contract = FlexibleOptionContract.from_file(self.option_fixture_path)
        contract.guarantee_type = GuaranteeType.WITH_CCP_GUARANTEE
        contract.clearing_member_holder = None
        with self.assertRaises(GuaranteeValidationError):
            self.engine_strict.validate_flexible_option(contract)


if __name__ == "__main__":
    unittest.main()
