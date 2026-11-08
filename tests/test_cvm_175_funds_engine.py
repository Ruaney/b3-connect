"""Bateria de Testes Unitários — Motor Normativo e Operacional CVM 175 (Multiclasses).

Valida integralmente todas as exigências da Resolução CVM nº 175:
1. Modelagem multiclasses de fundos de investimento (MasterFund, FundClass, FundSubclass);
2. Segregação estrita entre Administrador Fiduciário e Gestor de Recursos;
3. Segregação patrimonial inviolável e isolamento de passivos entre classes (LAW-041);
4. Cálculo diário de cota e taxa de performance com Linha d'Água (High-Water Mark - LAW-043);
5. Alocação de cotistas por público-alvo (Varejo, Qualificado, Profissional) e limites de concentração;
6. Invariantes formais de conservação patrimonial e de cotas (LAW-045);
7. Carregamento, validação e idempotência do fixture oficial fixtures/cvm_175_funds_engine.json.

100% Python Standard Library pura (unittest, decimal, datetime, json, pathlib).
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

from src.cvm_175_funds_engine import (
    AssetPosition,
    ClassInsolvencyError,
    ClassStatus,
    ClassValuationResult,
    ConcentrationLimitBreachError,
    CondominiumType,
    Cvm175Error,
    Cvm175FundsEngine,
    FiduciaryAction,
    FundCategory,
    FundClass,
    FundClassType,
    FundConservationError,
    FundSubclass,
    HighWaterMarkViolationError,
    InsufficientLiquidityError,
    Investor,
    InvestorCategory,
    InvestorHolding,
    InvestorIneligibleError,
    LAW_041_CODE,
    LAW_043_CODE,
    LAW_045_CODE,
    LiabilityPosition,
    LiabilityRegime,
    MasterFund,
    MinimumInvestmentError,
    RingFencingViolationError,
    RoleSegregationError,
    ServiceProvider,
    ServiceProviderRole,
    SubclassValuationResult,
    ValidationError,
    round_cents,
    round_quota,
    round_rate,
)


class TestCvm175FundsEngine(unittest.TestCase):
    """Suíte abrangente de testes normativos, operacionais e formais sob a CVM 175."""

    def setUp(self) -> None:
        """Cria ambiente base para os testes."""
        self.admin = ServiceProvider(
            provider_id="ADM-001",
            name="B3 Banco Fiduciário S.A.",
            cnpj="00.111.222/0001-33",
            role=ServiceProviderRole.ADMINISTRATOR,
            cvm_code="CVM-ADM-9021",
        )
        self.manager = ServiceProvider(
            provider_id="MGR-001",
            name="Alpha Gestão de Recursos Ltda.",
            cnpj="33.444.555/0001-66",
            role=ServiceProviderRole.ASSET_MANAGER,
            cvm_code="CVM-MGR-4512",
        )
        self.custodian = ServiceProvider(
            provider_id="CUST-001",
            name="B3 Central Depositária S.A.",
            cnpj="00.111.222/0002-14",
            role=ServiceProviderRole.CUSTODIAN,
            cvm_code="CVM-CUST-1001",
        )

        self.master = MasterFund(
            fund_id="FUNDO-TESTE-01",
            name="Alpha Multimercado Fundo Multiclasses",
            cnpj="12.345.678/0001-90",
            category=FundCategory.FIF,
            administrator=self.admin,
            manager=self.manager,
            custodian=self.custodian,
        )

        self.engine = Cvm175FundsEngine(master_fund=self.master)

        # Cadastro de investidores modelo
        self.inv_retail = Investor("INV-R01", "João Varejo", "111.222.333-44", InvestorCategory.RETAIL, Decimal("50000.00"))
        self.inv_qualif = Investor("INV-Q01", "Pedro Qualificado", "222.333.444-55", InvestorCategory.QUALIFIED, Decimal("2500000.00"))
        self.inv_prof = Investor("INV-P01", "Alpha Institucional", "33.444.555/0001-00", InvestorCategory.PROFESSIONAL, Decimal("50000000.00"))

        for inv in [self.inv_retail, self.inv_qualif, self.inv_prof]:
            self.engine.register_investor(self.admin, inv)

        # Criação de Classe 1
        self.class1 = FundClass(
            class_id="CLASS-01",
            fund_id=self.master.fund_id,
            name="Classe Multimercado Macro",
            cnpj="12.345.678/0002-71",
            class_type=FundClassType.MULTIMERCADO,
            liability_regime=LiabilityRegime.LIMITED,
            condominium_type=CondominiumType.OPEN_END,
            cash_balance=Decimal("0.00"),
        )
        self.engine.add_class(self.admin, self.class1)

        # Criação de Subclasse Varejo
        self.sub_retail = FundSubclass(
            subclass_id="SUB-VAREJO-01",
            class_id="CLASS-01",
            name="Subclasse Varejo Macro",
            target_audience=InvestorCategory.RETAIL,
            admin_fee_annual_rate=Decimal("0.02000000"),
            performance_fee_rate=Decimal("0.20000000"),
            benchmark_name="CDI",
            high_water_mark=Decimal("100.00000000"),
            benchmark_base_index=Decimal("100.0000"),
            min_initial_investment=Decimal("100.00"),
            min_subsequent_investment=Decimal("100.00"),
            min_balance_permanence=Decimal("100.00"),
            concentration_limit_pct=Decimal("0.30000000"),
            quota_value=Decimal("100.00000000"),
        )
        self.engine.add_subclass(self.admin, self.sub_retail)

        # Criação de Subclasse Profissional
        self.sub_prof = FundSubclass(
            subclass_id="SUB-PROF-01",
            class_id="CLASS-01",
            name="Subclasse Profissional Macro",
            target_audience=InvestorCategory.PROFESSIONAL,
            admin_fee_annual_rate=Decimal("0.00800000"),
            performance_fee_rate=Decimal("0.15000000"),
            benchmark_name="CDI",
            high_water_mark=Decimal("100.00000000"),
            benchmark_base_index=Decimal("100.0000"),
            min_initial_investment=Decimal("1000000.00"),
            min_subsequent_investment=Decimal("100000.00"),
            min_balance_permanence=Decimal("500000.00"),
            concentration_limit_pct=None,
            quota_value=Decimal("100.00000000"),
        )
        self.engine.add_subclass(self.admin, self.sub_prof)

    # =================================================================
    # 1. TESTES DE SEGREGAÇÃO ESTRITA (ADMINISTRADOR VS GESTOR)
    # =================================================================

    def test_strict_role_segregation_cnpj_identity(self) -> None:
        """Administrador e Gestor não podem compartilhar o mesmo CNPJ."""
        with self.assertRaises(RoleSegregationError):
            MasterFund(
                fund_id="FUNDO-INVALID-01",
                name="Fundo Sem Segregação",
                cnpj="99.999.999/0001-99",
                category=FundCategory.FIF,
                administrator=ServiceProvider(
                    provider_id="P1", name="Empresa Unificada", cnpj="11.222.333/0001-44",
                    role=ServiceProviderRole.ADMINISTRATOR, cvm_code="ADM1"
                ),
                manager=ServiceProvider(
                    provider_id="P2", name="Empresa Unificada Gestão", cnpj="11.222.333/0001-44",
                    role=ServiceProviderRole.ASSET_MANAGER, cvm_code="MGR1"
                ),
            )

    def test_strict_role_segregation_role_mismatch(self) -> None:
        """Papel atribuído deve coincidir com o papel fiduciário exigido."""
        with self.assertRaises(RoleSegregationError):
            MasterFund(
                fund_id="FUNDO-INVALID-02",
                name="Fundo Papel Invertido",
                cnpj="99.999.999/0001-99",
                category=FundCategory.FIF,
                administrator=self.manager,  # Gestor tentando ser Administrador
                manager=self.admin,
            )

    def test_fiduciary_actions_authorization_matrix(self) -> None:
        """Apenas o prestador competente pode executar ações fiduciárias."""
        # Administrador pode calcular cota oficial
        self.engine.verify_action_authorization(self.admin, FiduciaryAction.CALCULATE_OFFICIAL_NAV)

        # Gestor não pode calcular cota oficial
        with self.assertRaises(RoleSegregationError):
            self.engine.verify_action_authorization(self.manager, FiduciaryAction.CALCULATE_OFFICIAL_NAV)

        # Gestor pode executar trades
        self.engine.verify_action_authorization(self.manager, FiduciaryAction.EXECUTE_PORTFOLIO_TRADE)

        # Administrador não pode executar trades discricionários de carteira
        with self.assertRaises(RoleSegregationError):
            self.engine.verify_action_authorization(self.admin, FiduciaryAction.EXECUTE_PORTFOLIO_TRADE)

        # Transferência de passivos é universalmente vedada (LAW-041)
        with self.assertRaises(RoleSegregationError):
            self.engine.verify_action_authorization(self.admin, FiduciaryAction.TRANSFER_LIABILITY)

    # =================================================================
    # 2. TESTES DE SEGREGAÇÃO PATRIMONIAL INVIOLÁVEL (LAW-041)
    # =================================================================

    def test_law_041_ring_fencing_cross_class_transfer_blocked(self) -> None:
        """Tentativa de transferir recursos para cobrir passivo de outra classe é bloqueada."""
        # Cria Classe 2
        class2 = FundClass(
            class_id="CLASS-02",
            fund_id=self.master.fund_id,
            name="Classe Renda Fixa Soberana",
            cnpj="12.345.678/0003-52",
            class_type=FundClassType.RENDA_FIXA,
            cash_balance=Decimal("50000.00"),
        )
        self.engine.add_class(self.admin, class2)

        # Tentativa de transferir R$ 20.000 da CLASS-01 para pagar dívida da CLASS-02
        with self.assertRaises(RingFencingViolationError) as ctx:
            self.engine.attempt_cross_class_liability_settlement(
                source_class_id="CLASS-01",
                target_class_id="CLASS-02",
                amount=Decimal("20000.00"),
            )
        self.assertEqual(ctx.exception.law_code, LAW_041_CODE)
        self.assertIn("segregação patrimonial", str(ctx.exception).lower())

    def test_law_041_audit_isolated_assets_and_liabilities(self) -> None:
        """Auditoria formal detecta e proíbe colisão de ativos entre classes."""
        class2 = FundClass(
            class_id="CLASS-02",
            fund_id=self.master.fund_id,
            name="Classe Crédito",
            cnpj="12.345.678/0003-52",
            class_type=FundClassType.CREDITO_PRIVADO,
        )
        self.engine.add_class(self.admin, class2)

        # Ativo na Classe 1
        self.class1.assets["AST-01"] = AssetPosition("AST-01", "VALE3", "Ações Vale", Decimal("100"), Decimal("60.00"))

        # Auditoria com classes segregadas passa
        report = self.engine.audit_law_041_ring_fencing()
        self.assertTrue(report["ring_fencing_preserved"])

        # Ilegalmente insere o mesmo ativo na Classe 2
        class2.assets["AST-01"] = AssetPosition("AST-01", "VALE3", "Ações Vale", Decimal("100"), Decimal("60.00"))

        with self.assertRaises(RingFencingViolationError) as ctx:
            self.engine.audit_law_041_ring_fencing()
        self.assertEqual(ctx.exception.law_code, LAW_041_CODE)

    def test_law_041_class_insolvency_does_not_affect_other_classes(self) -> None:
        """Insolvência em uma classe não contamina outras classes do mesmo fundo."""
        class_distressed = FundClass(
            class_id="CLASS-DISTRESSED",
            fund_id=self.master.fund_id,
            name="Classe Problemática",
            cnpj="12.345.678/0004-33",
            class_type=FundClassType.CREDITO_PRIVADO,
            cash_balance=Decimal("1000.00"),
        )
        # Passivo de R$ 50.000 gerando PL negativo de -R$ 49.000 (Insolvência)
        class_distressed.liabilities["DEBT-01"] = LiabilityPosition("DEBT-01", "Processo Judicial Perda", Decimal("50000.00"), "Credor X")
        self.engine.add_class(self.admin, class_distressed)

        self.assertTrue(class_distressed.is_insolvent)
        self.assertFalse(self.class1.is_insolvent)

        # Relatório de auditoria aponta a classe insolvente isoladamente
        report = self.engine.audit_law_041_ring_fencing()
        self.assertIn("CLASS-DISTRESSED", report["insolvent_classes"])
        self.assertTrue(report["ring_fencing_preserved"])

    # =================================================================
    # 3. TESTES DE ALOCAÇÃO DE COTISTAS E LIMITES REGULATÓRIOS
    # =================================================================

    def test_investor_eligibility_by_audience(self) -> None:
        """Validação rigorosa de público-alvo por subclasse."""
        # Subclasse Profissional rejeita Varejo
        with self.assertRaises(InvestorIneligibleError):
            self.sub_prof.validate_investor_eligibility(self.inv_retail)

        # Subclasse Profissional rejeita Qualificado
        with self.assertRaises(InvestorIneligibleError):
            self.sub_prof.validate_investor_eligibility(self.inv_qualif)

        # Subclasse Profissional aceita Profissional
        self.sub_prof.validate_investor_eligibility(self.inv_prof)

        # Subclasse Varejo aceita todos
        self.sub_retail.validate_investor_eligibility(self.inv_retail)
        self.sub_retail.validate_investor_eligibility(self.inv_qualif)
        self.sub_retail.validate_investor_eligibility(self.inv_prof)

    def test_subscription_minimum_investment_enforcement(self) -> None:
        """Aporte abaixo do piso mínimo é rejeitado."""
        # Subclasse Varejo mínimo = R$ 100
        with self.assertRaises(MinimumInvestmentError):
            self.engine.subscribe("CLASS-01", "SUB-VAREJO-01", "INV-R01", Decimal("50.00"), date(2024, 1, 10))

        # Subclasse Profissional mínimo = R$ 1.000.000
        with self.assertRaises(MinimumInvestmentError):
            self.engine.subscribe("CLASS-01", "SUB-PROF-01", "INV-P01", Decimal("500000.00"), date(2024, 1, 10))

    def test_subscription_and_concentration_limit(self) -> None:
        """Limite de concentração por cotista singular é estritamente monitorado."""
        op_date = date(2024, 1, 15)

        # Primeiro aporte na subclasse varejo: R$ 10.000 (100 cotas a R$ 100)
        q1 = self.engine.subscribe("CLASS-01", "SUB-VAREJO-01", "INV-R01", Decimal("10000.00"), op_date)
        self.assertEqual(q1, Decimal("100.00000000"))

        # Segundo investidor tenta aportar R$ 20.000
        # Total ficaria R$ 30.000, e a participação do segundo cotista seria 20.000 / 30.000 = 66.67%
        # Mas o limite da subclasse varejo é 30% -> Deve rejeitar!
        with self.assertRaises(ConcentrationLimitBreachError):
            self.engine.subscribe("CLASS-01", "SUB-VAREJO-01", "INV-Q01", Decimal("20000.00"), op_date)

    def test_subscription_and_redemption_lifecycle(self) -> None:
        """Ciclo completo de subscrição e resgate com conservação de cotas e caixa."""
        op_date = date(2024, 2, 1)

        initial_cash = self.class1.cash_balance
        # Subscrição na subclasse profissional: R$ 2.000.000 (20.000 cotas a R$ 100)
        q = self.engine.subscribe("CLASS-01", "SUB-PROF-01", "INV-P01", Decimal("2000000.00"), op_date)
        self.assertEqual(q, Decimal("20000.00000000"))
        self.assertEqual(self.class1.cash_balance, initial_cash + Decimal("2000000.00"))

        # Resgate parcial de 5.000 cotas
        canceled_q, amount_redeemed = self.engine.redeem("CLASS-01", "SUB-PROF-01", "INV-P01", quota_count=Decimal("5000.00000000"))
        self.assertEqual(canceled_q, Decimal("5000.00000000"))
        self.assertEqual(amount_redeemed, Decimal("500000.00"))
        self.assertEqual(self.sub_prof.total_quotas, Decimal("15000.00000000"))
        self.assertEqual(self.sub_prof.holdings["INV-P01"].quotas, Decimal("15000.00000000"))

        # Resgate excedente gera erro de liquidez
        with self.assertRaises(InsufficientLiquidityError):
            self.engine.redeem("CLASS-01", "SUB-PROF-01", "INV-P01", quota_count=Decimal("20000.00000000"))

        # Resgate que deixaria saldo abaixo do saldo mínimo de permanência (R$ 500k = 5.000 cotas)
        # Saldo atual = 15.000 cotas. Tentar resgatar 12.000 cotas deixaria 3.000 cotas = R$ 300k (< 500k)
        with self.assertRaises(MinimumInvestmentError):
            self.engine.redeem("CLASS-01", "SUB-PROF-01", "INV-P01", quota_count=Decimal("12000.00000000"))

    # =================================================================
    # 4. TESTES DE CÁLCULO DE COTA E LINHA D'ÁGUA (LAW-043)
    # =================================================================

    def test_daily_valuation_hwm_with_performance_fee(self) -> None:
        """Cota acima do HWM e Benchmark gera apropriação exata de performance fee."""
        # Setup: 10.000 cotas na Subclasse Varejo a R$ 100 -> PL = R$ 1.000.000
        self.engine.subscribe("CLASS-01", "SUB-VAREJO-01", "INV-R01", Decimal("1000000.00"), date(2024, 3, 1))

        # Ativo valoriza gerando valorização na carteira
        self.class1.assets["AST-01"] = AssetPosition("AST-01", "VALE3", "Ações", Decimal("10000"), Decimal("110.00"))  # 1.1M
        self.class1.cash_balance = Decimal("0.00")  # PL total = 1.1M (+10% de ganho bruto)

        # Benchmark CDI subiu 1% (índice 101.0000 vs base 100.0000)
        # Hurdle Cota = 100.00 * (101 / 100) = 101.00000000
        val_date = date(2024, 3, 2)
        results = self.engine.calculate_daily_valuation(
            actor=self.admin,
            valuation_date=val_date,
            benchmark_indices={"CDI": Decimal("101.0000")},
        )

        sub_res = results["CLASS-01"].subclasses_results["SUB-VAREJO-01"]
        self.assertTrue(sub_res.exceeded_hwm)
        self.assertGreater(sub_res.performance_fee_appropriated, Decimal("0.00"))
        self.assertGreater(sub_res.admin_fee_appropriated, Decimal("0.00"))
        self.assertGreater(sub_res.new_quota, Decimal("100.00000000"))

        # Auditoria formal LAW-043 passa
        audit_hwm = self.engine.audit_law_043_high_water_mark(benchmark_indices={"CDI": Decimal("101.0000")})
        self.assertTrue(audit_hwm["hwm_invariance_preserved"])

    def test_daily_valuation_hwm_below_watermark_zero_performance(self) -> None:
        """Cota abaixo do HWM proíbe terminantemente cobrança de performance (LAW-043)."""
        # Setup: 10.000 cotas na Subclasse Varejo a R$ 100 -> PL = R$ 1.000.000
        self.engine.subscribe("CLASS-01", "SUB-VAREJO-01", "INV-R01", Decimal("1000000.00"), date(2024, 3, 1))

        # Carteira sofre desvalorização: Ativo cai para R$ 900.000 (-10%)
        self.class1.assets["AST-01"] = AssetPosition("AST-01", "VALE3", "Ações", Decimal("10000"), Decimal("90.00"))
        self.class1.cash_balance = Decimal("0.00")

        results = self.engine.calculate_daily_valuation(
            actor=self.admin,
            valuation_date=date(2024, 3, 2),
            benchmark_indices={"CDI": Decimal("101.0000")},
        )

        sub_res = results["CLASS-01"].subclasses_results["SUB-VAREJO-01"]
        self.assertFalse(sub_res.exceeded_hwm)
        # Taxa de performance DEVE ser exatamente R$ 0.00
        self.assertEqual(sub_res.performance_fee_appropriated, Decimal("0.00"))
        self.assertLess(sub_res.new_quota, Decimal("100.00000000"))

        # Auditoria confirma preservação da Linha d'Água
        audit_hwm = self.engine.audit_law_043_high_water_mark(benchmark_indices={"CDI": Decimal("101.0000")})
        self.assertTrue(audit_hwm["hwm_invariance_preserved"])

    def test_law_043_audit_raises_on_unlawful_performance_provision(self) -> None:
        """Tentativa ilícita de provisionar performance com cota abaixo de HWM é capturada."""
        self.sub_retail.quota_value = Decimal("95.00000000")
        self.sub_retail.high_water_mark = Decimal("100.00000000")
        self.sub_retail.provisioned_performance_fee = Decimal("1500.00")  # Cobrança ilegal

        with self.assertRaises(HighWaterMarkViolationError) as ctx:
            self.engine.audit_law_043_high_water_mark()
        self.assertEqual(ctx.exception.law_code, LAW_043_CODE)

    def test_crystallize_performance_fee_updates_hwm(self) -> None:
        """Cristalização de performance atualiza o HWM para a cota recorde."""
        self.sub_retail.quota_value = Decimal("108.50000000")
        self.sub_retail.high_water_mark = Decimal("100.00000000")
        self.sub_retail.provisioned_performance_fee = Decimal("2500.00")
        self.class1.cash_balance = Decimal("10000.00")

        settled = self.engine.crystallize_performance_fee(
            actor=self.admin,
            class_id="CLASS-01",
            subclass_id="SUB-VAREJO-01",
            current_benchmark_index=Decimal("105.0000"),
        )

        self.assertEqual(settled, Decimal("2500.00"))
        self.assertEqual(self.sub_retail.high_water_mark, Decimal("108.50000000"))
        self.assertEqual(self.sub_retail.benchmark_base_index, Decimal("105.0000"))
        self.assertEqual(self.sub_retail.provisioned_performance_fee, Decimal("0.00"))
        self.assertEqual(self.class1.cash_balance, Decimal("7500.00"))

    # =================================================================
    # 5. TESTES DE CONSERVAÇÃO PATRIMONIAL MULTICLASSE (LAW-045)
    # =================================================================

    def test_law_045_fund_conservation_audit(self) -> None:
        """Auditoria confirma conservação entre PL da classe e subclasses."""
        # 2 cotistas aportam
        self.engine.subscribe("CLASS-01", "SUB-VAREJO-01", "INV-R01", Decimal("50000.00"), date(2024, 1, 10))
        self.engine.subscribe("CLASS-01", "SUB-PROF-01", "INV-P01", Decimal("1000000.00"), date(2024, 1, 10))

        report = self.engine.audit_law_045_fund_conservation()
        self.assertTrue(report["conservation_preserved"])
        self.assertEqual(report["law_code"], LAW_045_CODE)

    def test_law_045_detects_holding_bookkeeping_discrepancy(self) -> None:
        """Divergência entre total_quotas e soma dos cotistas aciona LAW-045."""
        self.engine.subscribe("CLASS-01", "SUB-VAREJO-01", "INV-R01", Decimal("10000.00"), date(2024, 1, 10))
        # Frauda o total de cotas da subclasse
        self.sub_retail.total_quotas = Decimal("9999.00000000")

        with self.assertRaises(FundConservationError) as ctx:
            self.engine.audit_law_045_fund_conservation()
        self.assertEqual(ctx.exception.law_code, LAW_045_CODE)

    # =================================================================
    # 6. TESTES COM FIXTURE OFICIAL JSON E ROUNDTRIP IDEMPOTENTE
    # =================================================================

    def test_load_and_validate_official_fixture(self) -> None:
        """Carrega fixtures/cvm_175_funds_engine.json e audita todos os invariantes."""
        fixture_path = ROOT_DIR / "fixtures" / "cvm_175_funds_engine.json"
        self.assertTrue(fixture_path.exists(), f"Arquivo de fixture {fixture_path} não encontrado.")

        engine = Cvm175FundsEngine()
        engine.load_fixture(fixture_path)
        self.assertIsNotNone(engine.master_fund)

        # Verifica consistência das classes carregadas
        self.assertEqual(len(engine.master_fund.classes), 2)
        self.assertIn("CLASS-MACRO-01", engine.master_fund.classes)
        self.assertIn("CLASS-CREDITO-02", engine.master_fund.classes)

        # Executa auditoria integral de todas as leis
        audit = engine.verify_all_invariants()
        self.assertTrue(audit["all_passed"])
        self.assertTrue(audit["law_041"]["ring_fencing_preserved"])
        self.assertTrue(audit["law_043"]["hwm_invariance_preserved"])
        self.assertTrue(audit["law_045"]["conservation_preserved"])

    def test_roundtrip_serialization_idempotency(self) -> None:
        """Serialização to_dict() e from_dict() preserva centavos e cotas exatos."""
        fixture_path = ROOT_DIR / "fixtures" / "cvm_175_funds_engine.json"
        engine_original = Cvm175FundsEngine()
        engine_original.load_fixture(fixture_path)

        dict_1 = engine_original.to_dict()
        engine_deserialized = Cvm175FundsEngine.from_dict(dict_1)
        dict_2 = engine_deserialized.to_dict()

        self.assertEqual(dict_1, dict_2)
        self.assertEqual(
            engine_original.master_fund.total_net_asset_value,
            engine_deserialized.master_fund.total_net_asset_value,
        )

    def test_subsequent_subscription_minimum_threshold(self) -> None:
        """Aporte subsequente deve respeitar min_subsequent_investment."""
        self.sub_retail.concentration_limit_pct = None
        self.sub_retail.min_initial_investment = Decimal("500.00")
        self.sub_retail.min_subsequent_investment = Decimal("200.00")

        # Aporte inicial válido
        self.engine.subscribe("CLASS-01", "SUB-VAREJO-01", "INV-R01", Decimal("500.00"), date(2024, 1, 10))

        # Aporte subsequente abaixo do piso de R$ 200 (ex: R$ 150)
        with self.assertRaises(MinimumInvestmentError):
            self.engine.subscribe("CLASS-01", "SUB-VAREJO-01", "INV-R01", Decimal("150.00"), date(2024, 1, 15))

        # Aporte subsequente válido
        q2 = self.engine.subscribe("CLASS-01", "SUB-VAREJO-01", "INV-R01", Decimal("250.00"), date(2024, 1, 20))
        self.assertGreater(q2, Decimal("0"))

    def test_cross_class_same_class_is_allowed(self) -> None:
        """Operação dentro da mesma classe não viola segregação patrimonial."""
        # Não levanta exceção
        self.engine.attempt_cross_class_liability_settlement(
            source_class_id="CLASS-01",
            target_class_id="CLASS-01",
            amount=Decimal("1000.00"),
        )

    def test_class_insolvent_blocks_new_operations(self) -> None:
        """Classe em status INSOLVENT bloqueia novas aplicações e resgates."""
        self.class1.status = ClassStatus.INSOLVENT

        with self.assertRaises(ClassInsolvencyError):
            self.engine.subscribe("CLASS-01", "SUB-VAREJO-01", "INV-R01", Decimal("500.00"), date(2024, 1, 10))

        with self.assertRaises(ClassInsolvencyError):
            self.engine.redeem("CLASS-01", "SUB-VAREJO-01", "INV-R01", quota_count=Decimal("10.00000000"))

    def test_multi_day_valuation_sequence(self) -> None:
        """Sequência de 3 dias de cotização com subida, queda e recuperação de cota."""
        # Investimento inicial de R$ 1.000.000 (10.000 cotas a R$ 100)
        self.engine.subscribe("CLASS-01", "SUB-VAREJO-01", "INV-R01", Decimal("1000000.00"), date(2024, 1, 1))

        # Dia 1: Ativo valoriza para R$ 1.050.000 (+5%), CDI estável (100.00)
        self.class1.assets["AST-01"] = AssetPosition("AST-01", "LFT", "Tesouro Selic", Decimal("1000"), Decimal("1050.00"))
        self.class1.cash_balance = Decimal("0.00")

        res_d1 = self.engine.calculate_daily_valuation(
            actor=self.admin,
            valuation_date=date(2024, 1, 2),
            benchmark_indices={"CDI": Decimal("100.0000")},
        )
        cota_d1 = res_d1["CLASS-01"].subclasses_results["SUB-VAREJO-01"].new_quota
        self.assertGreater(cota_d1, Decimal("100.00000000"))

        # Cristaliza performance do Dia 1
        self.engine.crystallize_performance_fee(
            actor=self.admin,
            class_id="CLASS-01",
            subclass_id="SUB-VAREJO-01",
            current_benchmark_index=Decimal("100.0000"),
        )
        new_hwm = self.sub_retail.high_water_mark
        self.assertEqual(new_hwm, cota_d1)

        # Dia 2: Mercado cai (-3%)
        self.class1.assets["AST-01"].unit_price = Decimal("1010.00")
        res_d2 = self.engine.calculate_daily_valuation(
            actor=self.admin,
            valuation_date=date(2024, 1, 3),
            benchmark_indices={"CDI": Decimal("100.1000")},
        )
        sub_d2 = res_d2["CLASS-01"].subclasses_results["SUB-VAREJO-01"]
        self.assertFalse(sub_d2.exceeded_hwm)
        self.assertEqual(sub_d2.performance_fee_appropriated, Decimal("0.00"))

        # Dia 3: Recuperação parcial mas ainda abaixo da Linha d'Água anterior (HWM)
        self.class1.assets["AST-01"].unit_price = Decimal("1030.00")
        res_d3 = self.engine.calculate_daily_valuation(
            actor=self.admin,
            valuation_date=date(2024, 1, 4),
            benchmark_indices={"CDI": Decimal("100.2000")},
        )
        sub_d3 = res_d3["CLASS-01"].subclasses_results["SUB-VAREJO-01"]
        self.assertFalse(sub_d3.exceeded_hwm)
        self.assertEqual(sub_d3.performance_fee_appropriated, Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
