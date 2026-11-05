"""Bateria de Testes Unitários e Verificação Formal de Invariantes para o Módulo de
Operações Compromissadas com Títulos Privados no Balcão NoMe B3 (FL-079).

Valida:
1. Modelagem rigorosa de títulos privados elegíveis (Debêntures, LF, CRI, CRA).
2. Matriz de Haircuts Regulatórios B3 (por rating de crédito, prazo residual e tipo de ativo).
3. Monotonicidade estrita da matriz de haircuts regulatórios.
4. Matemática financeira DU/252 (PRE, CDI e CDI+Spread) e Invariante de Paridade de Recompra.
5. Liquidação Delivery versus Payment (DvP) simultânea na perna de ida (D0).
6. Marcação a mercado diária (MtM) e emissão de Chamadas de Margem por desvalorização.
7. Satisfação de Chamadas de Margem via Caixa e via Títulos Adicionais.
8. Liquidação DvP na perna de volta (vencimento) com desoneração fiduciária.
9. Eventos de Default e Excussão de Garantia no Balcão NoMe (Waterfall de Liquidação com superávit e déficit).
10. Verificação formal de invariantes de conservação e não-negatividade.
11. Carregamento e integridade da fixture JSON padronizada (private_repo_nome.json).
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.private_repo_nome import (
    ContractStatus,
    CreditRating,
    DefaultReason,
    ExecutionWaterfallResult,
    MarginCallEvent,
    MarginDepositType,
    ParticipantAccount,
    PrivateAsset,
    PrivateAssetType,
    PrivateRepoContract,
    PrivateRepoDefaultError,
    PrivateRepoHaircutError,
    PrivateRepoMarginCallError,
    PrivateRepoNomeEngine,
    PrivateRepoSettlementError,
    PrivateRepoValidationError,
    RateType,
    RegulatoryHaircutCalculator,
    SettlementDvPRecord,
    SettlementLeg,
    calculate_remuneration_factor_cdi,
    calculate_remuneration_factor_cdi_spread,
    calculate_remuneration_factor_pre,
    count_workdays,
    round_cents,
    round_factor,
    round_pu,
    round_qty,
    round_rate,
)


class TestPrivateRepoNome(unittest.TestCase):
    """Bateria de testes completa do fluxo FL-079."""

    def setUp(self) -> None:
        self.fixtures_dir = ROOT_DIR / "fixtures"
        self.fixture_path = self.fixtures_dir / "private_repo_nome.json"
        self.engine = PrivateRepoNomeEngine.load_from_fixture(self.fixture_path)

    # -----------------------------------------------------------------
    # 1. CARREGAMENTO DA FIXTURE E INVARIANTES INICIAIS
    # -----------------------------------------------------------------

    def test_fixture_loading_and_invariants(self) -> None:
        """Verifica se a fixture oficial carrega com integridade e satisfaz todas as invariantes."""
        self.assertEqual(len(self.engine.participants), 3)
        self.assertEqual(len(self.engine.assets), 5)
        self.assertEqual(len(self.engine.contracts), 4)

        report = self.engine.verify_invariants()
        self.assertTrue(report["all_invariants_hold"])
        self.assertEqual(len(report["violations"]), 0)
        self.assertEqual(report["total_contracts"], 4)

    # -----------------------------------------------------------------
    # 2. MODELAGEM DE ATIVOS PRIVADOS E VALIDAÇÕES
    # -----------------------------------------------------------------

    def test_private_asset_validations(self) -> None:
        """Valida inicialização e restrições de integridade dos títulos privados."""
        # Sucesso
        deb = PrivateAsset(
            asset_id="DEB-TEST-01",
            isin="BRTESTDBS001",
            asset_type=PrivateAssetType.DEBENTURE,
            issuer_cnpj="12345678000195",
            issuer_name="EMPRESA TESTE S.A.",
            rating=CreditRating.AA,
            issue_date=date(2025, 1, 10),
            maturity_date=date(2030, 1, 10),
            nominal_unit_price=Decimal("1000.00"),
            current_market_pu=Decimal("1025.50"),
        )
        self.assertEqual(deb.asset_type, PrivateAssetType.DEBENTURE)
        self.assertEqual(deb.rating, CreditRating.AA)
        self.assertTrue(deb.rating.is_investment_grade)

        # Falha: Vencimento anterior à emissão
        with self.assertRaises(PrivateRepoValidationError):
            PrivateAsset(
                asset_id="DEB-INV-01",
                isin="BRTESTDBS002",
                asset_type=PrivateAssetType.DEBENTURE,
                issuer_cnpj="12345678000195",
                issuer_name="EMPRESA TESTE",
                rating=CreditRating.A,
                issue_date=date(2025, 1, 10),
                maturity_date=date(2024, 1, 10),
                nominal_unit_price=Decimal("1000.00"),
                current_market_pu=Decimal("1000.00"),
            )

        # Falha: Preço negativo
        with self.assertRaises(PrivateRepoValidationError):
            PrivateAsset(
                asset_id="DEB-INV-02",
                isin="BRTESTDBS003",
                asset_type=PrivateAssetType.DEBENTURE,
                issuer_cnpj="12345678000195",
                issuer_name="EMPRESA TESTE",
                rating=CreditRating.A,
                issue_date=date(2025, 1, 10),
                maturity_date=date(2030, 1, 10),
                nominal_unit_price=Decimal("-1000.00"),
                current_market_pu=Decimal("1000.00"),
            )

    # -----------------------------------------------------------------
    # 3. MATRIZ REGULATÓRIA DE HAIRCUTS B3
    # -----------------------------------------------------------------

    def test_regulatory_haircut_matrix_calculations(self) -> None:
        """Valida cálculos pontuais e monotonicidade da matriz de haircuts."""
        # 1. LF AAA prazo curto (180 dias): Base 5% + Tenor 0% + LF 0% = 5%
        hc_lf_aaa = RegulatoryHaircutCalculator.calculate_haircut(
            PrivateAssetType.LF, CreditRating.AAA, 180
        )
        self.assertEqual(hc_lf_aaa, Decimal("0.05000000"))

        # 2. Debênture AA prazo médio (800 dias ~ 2.2 anos): Base 7.5% + Tenor 2% + Debênture 1.5% = 11%
        hc_deb_aa = RegulatoryHaircutCalculator.calculate_haircut(
            PrivateAssetType.DEBENTURE, CreditRating.AA, 800
        )
        self.assertEqual(hc_deb_aa, Decimal("0.11000000"))

        # 3. CRI BBB prazo longo (1500 dias ~ 4.1 anos): Base 18% + Tenor 4% + CRI 3% = 25%
        hc_cri_bbb = RegulatoryHaircutCalculator.calculate_haircut(
            PrivateAssetType.CRI, CreditRating.BBB, 1500
        )
        self.assertEqual(hc_cri_bbb, Decimal("0.25000000"))

        # 4. CRA SUB_INVESTMENT prazo muito longo (2000 dias > 5 anos): Base 30% + Tenor 7% + CRA 3% = 40%
        hc_cra_sub = RegulatoryHaircutCalculator.calculate_haircut(
            PrivateAssetType.CRA, CreditRating.SUB_INVESTMENT, 2000
        )
        self.assertEqual(hc_cra_sub, Decimal("0.40000000"))

        # 5. Sobretaxa de emissor em monitoramento especial (+5%)
        hc_watchlist = RegulatoryHaircutCalculator.calculate_haircut(
            PrivateAssetType.LF, CreditRating.AAA, 180, issuer_watchlist=True
        )
        self.assertEqual(hc_watchlist, Decimal("0.10000000"))

    def test_haircut_monotonicity_and_bounds(self) -> None:
        """Garante formalmente a monotonicidade por rating e por prazo, e os limites regulatórios."""
        ratings = [
            CreditRating.AAA,
            CreditRating.AA_PLUS,
            CreditRating.A,
            CreditRating.BBB,
            CreditRating.SUB_INVESTMENT,
            CreditRating.UNRATED,
        ]
        tenors = [100, 500, 1200, 2500]

        for atype in PrivateAssetType:
            for t in tenors:
                hcs = [RegulatoryHaircutCalculator.calculate_haircut(atype, r, t) for r in ratings]
                for i in range(len(hcs) - 1):
                    # Monotonicidade por rating: rating pior => haircut maior ou igual
                    self.assertLessEqual(
                        hcs[i],
                        hcs[i + 1],
                        f"Monotonicidade de rating violada para {atype} em {t} dias",
                    )
                    # Limites
                    self.assertGreaterEqual(hcs[i], Decimal("0.02000000"))
                    self.assertLessEqual(hcs[i], Decimal("0.50000000"))

            for r in ratings:
                hcs_tenor = [RegulatoryHaircutCalculator.calculate_haircut(atype, r, t) for t in tenors]
                for j in range(len(hcs_tenor) - 1):
                    # Monotonicidade por prazo: prazo maior => haircut maior ou igual
                    self.assertLessEqual(
                        hcs_tenor[j],
                        hcs_tenor[j + 1],
                        f"Monotonicidade de prazo violada para {atype} com {r}",
                    )

    # -----------------------------------------------------------------
    # 4. MATEMÁTICA FINANCEIRA DU/252 E INVARIANTE DE PARIDADE
    # -----------------------------------------------------------------

    def test_remuneration_factor_pre_exact(self) -> None:
        """Valida cálculo do fator de remuneração pré-fixado DU/252."""
        # 12.50% a.a. por 44 dias úteis
        fator = calculate_remuneration_factor_pre(Decimal("12.50"), 44)
        # (1 + 0.1250)^(44/252) = 1.02077821
        self.assertEqual(fator, Decimal("1.02077821"))

        # Invariante de paridade: Preço_Recompra == round_cents(Preço_Venda * Fator)
        start_price = Decimal("9300000.00")
        expected_end = round_cents(start_price * fator)
        self.assertEqual(expected_end, Decimal("9493237.35"))

    def test_remuneration_factor_cdi_exact(self) -> None:
        """Valida cálculo do fator de remuneração indexado ao CDI padrão Cetip/B3."""
        # 100% do CDI, CDI projetado 11.25% a.a., 22 dias úteis
        fator = calculate_remuneration_factor_cdi(Decimal("100.00"), Decimal("11.25"), 22)
        self.assertEqual(fator, Decimal("1.00935056"))

        start_price = Decimal("15000000.00")
        expected_end = round_cents(start_price * fator)
        self.assertEqual(expected_end, Decimal("15140258.40"))

    def test_remuneration_factor_cdi_spread_exact(self) -> None:
        """Valida cálculo do fator de compromissada CDI + spread a.a."""
        # 100% do CDI (11.25%) + Spread 1.50% a.a., 63 dias úteis
        fator = calculate_remuneration_factor_cdi_spread(
            Decimal("100.00"), Decimal("11.25"), Decimal("1.50"), 63
        )
        self.assertEqual(fator, Decimal("1.03084034"))

        start_price = Decimal("17600000.00")
        expected_end = round_cents(start_price * fator)
        self.assertEqual(expected_end, Decimal("18142789.98"))

    # -----------------------------------------------------------------
    # 5. REGISTRO DE CONTRATOS E REGRAS DE COBERTURA
    # -----------------------------------------------------------------

    def test_reject_contract_with_insufficient_haircut(self) -> None:
        """Rejeita contrato com haircut inicial inferior ao regulatório B3."""
        # DEB-VALE-31 exige haircut de 10.5% (0.105)
        invalid_contract = PrivateRepoContract(
            contract_id="REPO-INV-001",
            seller_id="BANCO_ALFA_S_A",
            buyer_id="FUNDO_BETA_ASSET",
            asset_id="DEB-VALE-31",
            quantity=Decimal("1000.0000"),
            trade_date=date(2026, 11, 4),
            start_date=date(2026, 11, 4),
            maturity_date=date(2026, 12, 4),
            workdays=22,
            rate_type=RateType.PRE,
            annual_rate=Decimal("12.00"),
            start_pu=Decimal("900.00000000"),
            start_price=Decimal("900000.00"),
            end_pu=Decimal("908.97650000"),
            end_price=Decimal("908976.50"),
            initial_haircut=Decimal("0.05000000"),  # Inválido: 5% < 10.5%
            status=ContractStatus.REGISTERED,
        )
        with self.assertRaises(PrivateRepoHaircutError):
            self.engine.register_repo_contract(invalid_contract)

    def test_reject_contract_with_loan_exceeding_haircut_limit(self) -> None:
        """Rejeita contrato se Preço de Venda exceder o teto permitido pelo haircut."""
        # 1000 títulos a PU mercado 1045.25 = 1.045.250,00. Haircut 10.5% => Teto = 935.498,75
        invalid_loan = PrivateRepoContract(
            contract_id="REPO-INV-002",
            seller_id="BANCO_ALFA_S_A",
            buyer_id="FUNDO_BETA_ASSET",
            asset_id="DEB-VALE-31",
            quantity=Decimal("1000.0000"),
            trade_date=date(2026, 11, 4),
            start_date=date(2026, 11, 4),
            maturity_date=date(2026, 12, 4),
            workdays=22,
            rate_type=RateType.PRE,
            annual_rate=Decimal("12.00"),
            start_pu=Decimal("980.00000000"),
            start_price=Decimal("980000.00"),  # Excede 935.498,75
            end_pu=Decimal("989.77440000"),
            end_price=Decimal("989774.40"),
            initial_haircut=Decimal("0.10500000"),
            status=ContractStatus.REGISTERED,
        )
        with self.assertRaises(PrivateRepoHaircutError):
            self.engine.register_repo_contract(invalid_loan)

    def test_reject_contract_violating_repurchase_parity(self) -> None:
        """Rejeita contrato com Preço de Recompra divergente da paridade matemática."""
        invalid_parity = PrivateRepoContract(
            contract_id="REPO-INV-003",
            seller_id="BANCO_ALFA_S_A",
            buyer_id="FUNDO_BETA_ASSET",
            asset_id="DEB-VALE-31",
            quantity=Decimal("1000.0000"),
            trade_date=date(2026, 11, 4),
            start_date=date(2026, 11, 4),
            maturity_date=date(2026, 12, 4),
            workdays=22,
            rate_type=RateType.PRE,
            annual_rate=Decimal("12.00"),
            start_pu=Decimal("900.00000000"),
            start_price=Decimal("900000.00"),
            end_pu=Decimal("950.00000000"),
            end_price=Decimal("950000.00"),  # Totalmente errado
            initial_haircut=Decimal("0.10500000"),
            status=ContractStatus.REGISTERED,
        )
        with self.assertRaises(PrivateRepoValidationError):
            self.engine.register_repo_contract(invalid_parity)

    # -----------------------------------------------------------------
    # 6. LIQUIDAÇÃO DVP DA PERNA DE IDA (D0)
    # -----------------------------------------------------------------

    def test_execute_start_leg_dvp_success(self) -> None:
        """Verifica a transferência simultânea de custódia e caixa na liquidação D0."""
        # REPO-PRV-004 está REGISTERED na fixture: CRA-KLAB-2031, 15000 títulos, R$ 12.750.000,00
        seller = self.engine.participants["BANCO_ALFA_S_A"]
        buyer = self.engine.participants["BANCO_GAMA_INVEST"]

        seller_cash_before = seller.cash_balance
        buyer_cash_before = buyer.cash_balance
        seller_free_before = seller.get_free_quantity("CRA-KLAB-2031")
        seller_blocked_before = seller.get_blocked_quantity("CRA-KLAB-2031")

        rec = self.engine.execute_start_leg_dvp("REPO-PRV-004", date(2026, 11, 4))
        self.assertIsNotNone(rec)
        self.assertEqual(rec.leg, SettlementLeg.START_LEG)

        # Invariante de Conservação DvP:
        # 1. Caixa transferido estritamente igual
        self.assertEqual(seller.cash_balance, seller_cash_before + Decimal("12750000.00"))
        self.assertEqual(buyer.cash_balance, buyer_cash_before - Decimal("12750000.00"))
        # 2. Custódia bloqueada fiduciariamente
        self.assertEqual(seller.get_free_quantity("CRA-KLAB-2031"), seller_free_before - Decimal("15000.0000"))
        self.assertEqual(seller.get_blocked_quantity("CRA-KLAB-2031"), seller_blocked_before + Decimal("15000.0000"))

        contract = self.engine.contracts["REPO-PRV-004"]
        self.assertEqual(contract.status, ContractStatus.ACTIVE)

    def test_execute_start_leg_dvp_insufficient_collateral(self) -> None:
        """Garante rejeição se o vendedor não tiver custódia livre suficiente."""
        seller = self.engine.participants["BANCO_ALFA_S_A"]
        # Zera a custódia livre temporariamente
        seller.free_collateral["CRA-KLAB-2031"] = Decimal("0.0000")

        with self.assertRaises(PrivateRepoSettlementError):
            self.engine.execute_start_leg_dvp("REPO-PRV-004", date(2026, 11, 4))

    def test_execute_start_leg_dvp_insufficient_cash(self) -> None:
        """Garante rejeição se o comprador não tiver saldo em caixa suficiente."""
        buyer = self.engine.participants["BANCO_GAMA_INVEST"]
        buyer.cash_balance = Decimal("100.00")

        with self.assertRaises(PrivateRepoSettlementError):
            self.engine.execute_start_leg_dvp("REPO-PRV-004", date(2026, 11, 4))

    # -----------------------------------------------------------------
    # 7. MARCAÇÃO A MERCADO (MtM) E CHAMADA DE MARGEM DIÁRIA
    # -----------------------------------------------------------------

    def test_daily_mark_to_market_triggers_margin_call(self) -> None:
        """Desvalorização do lastro privado gera emissão automática de chamada de margem."""
        contract = self.engine.contracts["REPO-PRV-001"]
        # DEB-VALE-31 cai de 1045.25 para 800.00
        new_prices = {"DEB-VALE-31": Decimal("800.00000000")}

        calls = self.engine.mark_to_market(date(2026, 11, 10), market_prices=new_prices)
        self.assertEqual(len(calls), 1)

        call = calls[0]
        self.assertEqual(call.contract_id, "REPO-PRV-001")
        self.assertEqual(call.status, "PENDING")
        self.assertGreater(call.required_amount, Decimal("0.00"))
        self.assertEqual(contract.status, ContractStatus.MARGIN_CALL)

    def test_satisfy_margin_call_with_cash(self) -> None:
        """Tomador aporta caixa para recompor a margem regulatória e reativa o contrato."""
        new_prices = {"DEB-VALE-31": Decimal("800.00000000")}
        calls = self.engine.mark_to_market(date(2026, 11, 10), market_prices=new_prices)
        call = calls[0]

        seller = self.engine.participants["BANCO_ALFA_S_A"]
        cash_before = seller.cash_balance

        # Aporte em dinheiro
        sat_call = self.engine.satisfy_margin_call(
            event_id=call.event_id,
            deposit_type=MarginDepositType.CASH,
            cash_amount=call.required_amount,
            satisfied_date=date(2026, 11, 11),
        )

        self.assertEqual(sat_call.status, "SATISFIED")
        self.assertEqual(seller.cash_balance, cash_before - call.required_amount)
        contract = self.engine.contracts["REPO-PRV-001"]
        self.assertEqual(contract.additional_cash_margin, call.required_amount)
        self.assertEqual(contract.status, ContractStatus.ACTIVE)

    def test_satisfy_margin_call_with_additional_asset(self) -> None:
        """Tomador aporta títulos privados adicionais para recompor a garantia."""
        new_prices = {"DEB-VALE-31": Decimal("800.00000000")}
        calls = self.engine.mark_to_market(date(2026, 11, 10), market_prices=new_prices)
        call = calls[0]

        seller = self.engine.participants["BANCO_ALFA_S_A"]
        # Vendedor usa LF-ITAU-2027 como margem adicional (50 títulos x ~320.150 = ~16 milhões de lastro)
        free_lf_before = seller.get_free_quantity("LF-ITAU-2027")
        blocked_lf_before = seller.get_blocked_quantity("LF-ITAU-2027")

        sat_call = self.engine.satisfy_margin_call(
            event_id=call.event_id,
            deposit_type=MarginDepositType.ADDITIONAL_ASSET,
            asset_id="LF-ITAU-2027",
            asset_quantity=Decimal("30.0000"),
            satisfied_date=date(2026, 11, 11),
        )

        self.assertEqual(sat_call.status, "SATISFIED")
        self.assertEqual(seller.get_free_quantity("LF-ITAU-2027"), free_lf_before - Decimal("30.0000"))
        self.assertEqual(seller.get_blocked_quantity("LF-ITAU-2027"), blocked_lf_before + Decimal("30.0000"))

        contract = self.engine.contracts["REPO-PRV-001"]
        self.assertEqual(contract.additional_collateral["LF-ITAU-2027"], Decimal("30.0000"))
        self.assertEqual(contract.status, ContractStatus.ACTIVE)

    # -----------------------------------------------------------------
    # 8. LIQUIDAÇÃO DVP DA PERNA DE VOLTA (VENCIMENTO)
    # -----------------------------------------------------------------

    def test_execute_return_leg_dvp_success(self) -> None:
        """Executa liquidação DvP na data de recompra com desoneração fiduciária integral."""
        contract = self.engine.contracts["REPO-PRV-002"]  # LF ITAU, end_price R$ 15.140.258,40
        seller = self.engine.participants["BANCO_ALFA_S_A"]
        buyer = self.engine.participants["FUNDO_BETA_ASSET"]

        seller_cash_before = seller.cash_balance
        buyer_cash_before = buyer.cash_balance
        seller_free_lf = seller.get_free_quantity("LF-ITAU-2027")
        seller_blocked_lf = seller.get_blocked_quantity("LF-ITAU-2027")

        rec = self.engine.execute_return_leg_dvp("REPO-PRV-002", date(2026, 12, 4))
        self.assertEqual(rec.leg, SettlementLeg.RETURN_LEG)

        # Invariante DvP na volta:
        # Vendedor paga end_price, Comprador recebe end_price
        self.assertEqual(seller.cash_balance, seller_cash_before - contract.end_price)
        self.assertEqual(buyer.cash_balance, buyer_cash_before + contract.end_price)

        # Garantia primária desbloqueada de volta para a custódia livre do vendedor
        self.assertEqual(seller.get_blocked_quantity("LF-ITAU-2027"), seller_blocked_lf - contract.quantity)
        self.assertEqual(seller.get_free_quantity("LF-ITAU-2027"), seller_free_lf + contract.quantity)
        self.assertEqual(contract.status, ContractStatus.SETTLED)

    def test_execute_return_leg_dvp_insufficient_cash_triggers_default(self) -> None:
        """Vendedor sem caixa na data de vencimento é impedido de liquidar a volta e aciona Default."""
        seller = self.engine.participants["BANCO_ALFA_S_A"]
        seller.cash_balance = Decimal("100.00")

        with self.assertRaises(PrivateRepoDefaultError):
            self.engine.execute_return_leg_dvp("REPO-PRV-002", date(2026, 12, 4))

    # -----------------------------------------------------------------
    # 9. EVENTOS DE DEFAULT E EXCUSSÃO DE GARANTIA (NOME WATERFALL)
    # -----------------------------------------------------------------

    def test_default_waterfall_execution_with_surplus(self) -> None:
        """Default com valor de alienação superior à dívida: taxas pagas, comprador pago e excedente ao vendedor."""
        # REPO-PRV-003: CRI-CYRE-2029, 20.000 títulos. end_price = R$ 18.142.789,98
        # Simula alienação no NoMe a R$ 1.050,00 por título => Bruto = R$ 21.000.000,00
        seller = self.engine.participants["BANCO_GAMA_INVEST"]
        buyer = self.engine.participants["FUNDO_BETA_ASSET"]

        seller_cash_before = seller.cash_balance
        buyer_cash_before = buyer.cash_balance

        result = self.engine.trigger_default_and_liquidate(
            contract_id="REPO-PRV-003",
            execution_date=date(2027, 2, 6),
            reason=DefaultReason.MATURITY_REPURCHASE_FAILED,
            liquidation_pu_override=Decimal("1050.00000000"),
            b3_fee_rate=Decimal("0.00100000"),  # 0.10%
        )

        self.assertEqual(result.gross_proceeds, Decimal("21000000.00"))
        # Taxa B3: 0.10% de 21M = R$ 21.000,00
        self.assertEqual(result.b3_execution_fees, Decimal("21000.00"))
        self.assertEqual(result.net_proceeds_after_fees, Decimal("20979000.00"))

        # Dívida do comprador R$ 18.142.789,98 é paga integralmente
        self.assertEqual(result.buyer_satisfaction, Decimal("18142789.98"))
        self.assertTrue(result.is_fully_satisfied)
        self.assertEqual(result.uncovered_debt, Decimal("0.00"))

        # Excedente devolvido ao vendedor: 20.979.000,00 - 18.142.789,98 = 2.836.210,02
        self.assertEqual(result.seller_refund, Decimal("2836210.02"))

        # Invariante Formal do Waterfall: Bruto == Taxas + Comprador + Vendedor
        self.assertTrue(result.conservation_verified)
        self.assertEqual(
            result.gross_proceeds,
            result.b3_execution_fees + result.buyer_satisfaction + result.seller_refund,
        )

        # Saldos atualizados
        self.assertEqual(buyer.cash_balance, buyer_cash_before + result.buyer_satisfaction)
        self.assertEqual(seller.cash_balance, seller_cash_before + result.seller_refund)

    def test_default_waterfall_execution_with_shortfall(self) -> None:
        """Cenário de estresse extremo com shortfall: recursos insuficientes deixam saldo a descoberto."""
        # REPO-PRV-003: end_price = R$ 18.142.789,98
        # Alienação a apenas R$ 500,00 por título (crash de 50%) => Bruto = 20.000 * 500 = R$ 10.000.000,00
        result = self.engine.trigger_default_and_liquidate(
            contract_id="REPO-PRV-003",
            execution_date=date(2027, 2, 6),
            reason=DefaultReason.VOLUNTARY_EARLY_DEFAULT,
            liquidation_pu_override=Decimal("500.00000000"),
            b3_fee_rate=Decimal("0.00100000"),
        )

        self.assertEqual(result.gross_proceeds, Decimal("10000000.00"))
        # Taxa B3: R$ 10.000,00
        self.assertEqual(result.b3_execution_fees, Decimal("10000.00"))
        self.assertEqual(result.net_proceeds_after_fees, Decimal("9990000.00"))

        # Comprador recebe o valor líquido disponível
        self.assertEqual(result.buyer_satisfaction, Decimal("9990000.00"))
        self.assertFalse(result.is_fully_satisfied)
        self.assertEqual(result.seller_refund, Decimal("0.00"))

        # Shortfall apurado: 18.142.789,98 - 9.990.000,00 = 8.152.789,98
        self.assertEqual(result.uncovered_debt, Decimal("8152789.98"))

        # Invariante Formal: Comprador + Shortfall == Dívida Total
        self.assertEqual(
            result.buyer_satisfaction + result.uncovered_debt,
            result.debt_to_buyer,
        )
        self.assertTrue(result.conservation_verified)

    # -----------------------------------------------------------------
    # 10. DETECÇÃO DE VIOLAÇÃO DE INVARIANTES E SERIALIZAÇÃO
    # -----------------------------------------------------------------

    def test_invariants_violation_detection(self) -> None:
        """Verifica se verify_invariants detecta quebras deliberadas de integridade."""
        # 1. Caixa negativo
        seller = self.engine.participants["BANCO_ALFA_S_A"]
        seller.cash_balance = Decimal("-500.00")
        report = self.engine.verify_invariants()
        self.assertFalse(report["all_invariants_hold"])
        self.assertGreater(report["violations_count"], 0)
        seller.cash_balance = Decimal("500000000.00")  # Restaura

    def test_roundtrip_to_dict_and_from_dict(self) -> None:
        """Garante fidelidade total na serialização to_dict e desserialização from_dict."""
        data = self.engine.to_dict()
        rebuilt = PrivateRepoNomeEngine.from_dict(data)

        self.assertEqual(len(rebuilt.participants), len(self.engine.participants))
        self.assertEqual(len(rebuilt.assets), len(self.engine.assets))
        self.assertEqual(len(rebuilt.contracts), len(self.engine.contracts))

        rep = rebuilt.verify_invariants()
        self.assertTrue(rep["all_invariants_hold"])


if __name__ == "__main__":
    unittest.main()
