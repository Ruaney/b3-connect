"""Bateria de Testes Unitários para Cálculo e Monitoramento de Margem Intradiária CORE B3 (FL-050).

Testa:
- Criação e serialização de cenários de risco primitivos e posições multi-ativo.
- Reavaliação integral de perdas sob choques de ações à vista, volatilidade, juros e câmbio.
- Convexidade de opções sobre ações via modelo analítico Black-Scholes.
- Reavaliação de PU de contratos futuros de DI1 e precificação de futuros de Dólar DOL.
- Efeito Portfólio e compensação multilateral (netting) intragrupo e cross-asset.
- Apuração do pior caso de liquidação (Worst-Case Closeout Loss).
- Adicionais regulatórios de liquidez e concentração setorial.
- Cesta de colaterais com haircuts regulatórios B3 (CASH_BRL, LFT, LTN, USD, Ações).
- Gatilho e estados de chamada de margem intradiária: NORMAL, WARNING, MARGIN_CALL_REQUIRED e CRITICAL.
- Carregamento completo da fixture oficial fixtures/core_intraday_margin.json e confronto de benchmarks.
- Desempenho e latência determinística em microssegundos (< 1 ms / sub-millisecond).
"""

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

# Garante a raiz do repositório no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.core_intraday_margin import (
    AssetCategory,
    CollateralItem,
    CollateralType,
    CoreRiskEngine,
    CoreRiskError,
    IntradayMarginCalculation,
    InvalidPositionError,
    MarginStatus,
    PositionItem,
    RiskScenario,
    ScenarioEvaluationError,
    StressScenarioResult,
    _black_scholes_price,
    _std_norm_cdf,
)

FIXTURES_DIR = ROOT_DIR / "fixtures"
CORE_FIXTURE_PATH = FIXTURES_DIR / "core_intraday_margin.json"


class TestCoreIntradayMargin(unittest.TestCase):
    """Suíte de testes do motor de margem intradiária CORE da Câmara B3."""

    def setUp(self) -> None:
        self.engine = CoreRiskEngine(
            regulatory_tolerance_pct=Decimal("0.05"),
            regulatory_tolerance_min_brl=Decimal("5000.00"),
            liquidity_addon_rate=Decimal("0.005"),
            concentration_threshold_pct=Decimal("0.40"),
            concentration_addon_rate=Decimal("0.015"),
        )

    # --------------------------------------------------------------------------
    # 1. Cenários Primitivos de Risco
    # --------------------------------------------------------------------------

    def test_risk_scenario_creation_and_serialization(self) -> None:
        """Cenário de risco deve ser criado, serializado e desserializado com precisão."""
        scn = RiskScenario(
            scenario_id="SCN_TEST_01",
            name="Cenário de Teste de Estresse",
            description="Choque combinado em bolsa, vol e curva",
            equity_shocks={"PETR4": Decimal("-0.20"), "VALE3": Decimal("-0.15")},
            volatility_shocks={"PETR4": Decimal("0.30")},
            interest_rate_shocks={"PRE_CURVE": Decimal("0.0150")},
            fx_shocks={"USD_BRL": Decimal("0.10")},
            probability_weight=Decimal("1.0"),
        )

        d = scn.to_dict()
        self.assertEqual(d["scenario_id"], "SCN_TEST_01")
        self.assertEqual(d["equity_shocks"]["PETR4"], "-0.20")
        self.assertEqual(d["volatility_shocks"]["PETR4"], "0.30")
        self.assertEqual(d["interest_rate_shocks"]["PRE_CURVE"], "0.0150")
        self.assertEqual(d["fx_shocks"]["USD_BRL"], "0.10")

        rebuilt = RiskScenario.from_dict(d)
        self.assertEqual(rebuilt.scenario_id, scn.scenario_id)
        self.assertEqual(rebuilt.equity_shocks["PETR4"], Decimal("-0.20"))
        self.assertEqual(rebuilt.volatility_shocks["PETR4"], Decimal("0.30"))

    # --------------------------------------------------------------------------
    # 2. Reavaliação Integral de Posições (Equities, Options, DI1, DOL)
    # --------------------------------------------------------------------------

    def test_equity_spot_shocks_pnl(self) -> None:
        """Ações à vista devem sofrer perdas e ganhos simétricos sob choques de preço."""
        # Long 1.000 ações PETR4 @ R$ 35,00
        pos_long = PositionItem(
            position_id="POS_PETR4_L",
            symbol="PETR4",
            asset_category=AssetCategory.EQUITY,
            underlying_symbol="PETR4",
            quantity=Decimal("1000"),
            current_price=Decimal("35.00"),
        )
        # Short 1.000 ações PETR4 @ R$ 35,00
        pos_short = PositionItem(
            position_id="POS_PETR4_S",
            symbol="PETR4",
            asset_category=AssetCategory.EQUITY,
            underlying_symbol="PETR4",
            quantity=Decimal("-1000"),
            current_price=Decimal("35.00"),
        )

        # Choque de -10%
        scn_down10 = RiskScenario(
            scenario_id="DOWN_10",
            name="Queda 10%",
            equity_shocks={"PETR4": Decimal("-0.10")},
        )
        pnl_long = pos_long.calculate_scenario_pnl(scn_down10)
        pnl_short = pos_short.calculate_scenario_pnl(scn_down10)

        # 1.000 * 35.00 * (-0.10) = -3.500,00
        self.assertEqual(pnl_long, Decimal("-3500.00"))
        # -1.000 * 35.00 * (-0.10) = +3.500,00
        self.assertEqual(pnl_short, Decimal("3500.00"))

        # Choque de +20%
        scn_up20 = RiskScenario(
            scenario_id="UP_20",
            name="Alta 20%",
            equity_shocks={"PETR4": Decimal("0.20")},
        )
        self.assertEqual(pos_long.calculate_scenario_pnl(scn_up20), Decimal("7000.00"))
        self.assertEqual(pos_short.calculate_scenario_pnl(scn_up20), Decimal("-7000.00"))

    def test_options_black_scholes_convexity(self) -> None:
        """Opções de compra e venda devem exibir curvatura não-linear (gama) e sensibilidade a vol."""
        # Call ATM strike 35, spot 35, vol 30%, rate 11.75%, mat 0.25
        call = PositionItem(
            position_id="POS_CALL",
            symbol="PETRL350",
            asset_category=AssetCategory.OPTION_CALL,
            underlying_symbol="PETR4",
            quantity=Decimal("1000"),
            current_price=Decimal("35.00"),
            strike=Decimal("35.00"),
            maturity_years=Decimal("0.25"),
            implied_vol=Decimal("0.30"),
            risk_free_rate=Decimal("0.1175"),
        )
        # Put ATM strike 35
        put = PositionItem(
            position_id="POS_PUT",
            symbol="PETRX350",
            asset_category=AssetCategory.OPTION_PUT,
            underlying_symbol="PETR4",
            quantity=Decimal("1000"),
            current_price=Decimal("35.00"),
            strike=Decimal("35.00"),
            maturity_years=Decimal("0.25"),
            implied_vol=Decimal("0.30"),
            risk_free_rate=Decimal("0.1175"),
        )

        # Queda de 10% no ativo objeto: Call perde valor, Put ganha valor
        scn_down = RiskScenario(
            scenario_id="SCN_DOWN",
            name="Queda Spot",
            equity_shocks={"PETR4": Decimal("-0.10")},
        )
        call_pnl_down = call.calculate_scenario_pnl(scn_down)
        put_pnl_down = put.calculate_scenario_pnl(scn_down)
        self.assertLess(call_pnl_down, Decimal("0.00"))
        self.assertGreater(put_pnl_down, Decimal("0.00"))

        # Alta de 10% no ativo objeto: Call ganha valor, Put perde valor
        scn_up = RiskScenario(
            scenario_id="SCN_UP",
            name="Alta Spot",
            equity_shocks={"PETR4": Decimal("0.10")},
        )
        call_pnl_up = call.calculate_scenario_pnl(scn_up)
        put_pnl_up = put.calculate_scenario_pnl(scn_up)
        self.assertGreater(call_pnl_up, Decimal("0.00"))
        self.assertLess(put_pnl_up, Decimal("0.00"))

        # Choque puro de volatilidade (+30% relativo): ambas as opções (compradas) ganham valor
        scn_vol = RiskScenario(
            scenario_id="SCN_VOL",
            name="Spike Vol",
            volatility_shocks={"PETR4": Decimal("0.30")},
        )
        self.assertGreater(call.calculate_scenario_pnl(scn_vol), Decimal("0.00"))
        self.assertGreater(put.calculate_scenario_pnl(scn_vol), Decimal("0.00"))

    def test_di1_futures_pu_revaluation(self) -> None:
        """Futuro DI1 deve valorizar quando a taxa de juros cai e desvalorizar quando a taxa sobe."""
        # Long 10 contratos DI1 (comprador de PU = ganha com queda de taxa)
        pos_di = PositionItem(
            position_id="POS_DI1",
            symbol="DI1F27",
            asset_category=AssetCategory.FUTURE_DI,
            underlying_symbol="PRE_CURVE",
            quantity=Decimal("10"),
            current_price=Decimal("11.75"),
            annual_rate=Decimal("0.1175"),
            business_days=252,
            contract_multiplier=Decimal("1.0"),
        )

        # Choque de alta na curva pré (+150 bps)
        scn_rate_up = RiskScenario(
            scenario_id="RATE_UP",
            name="Alta de Juros +150 bps",
            interest_rate_shocks={"PRE_CURVE": Decimal("0.0150")},
        )
        pnl_up = pos_di.calculate_scenario_pnl(scn_rate_up)
        self.assertLess(pnl_up, Decimal("0.00"))

        # Choque de queda na curva pré (-150 bps)
        scn_rate_down = RiskScenario(
            scenario_id="RATE_DOWN",
            name="Queda de Juros -150 bps",
            interest_rate_shocks={"PRE_CURVE": Decimal("-0.0150")},
        )
        pnl_down = pos_di.calculate_scenario_pnl(scn_rate_down)
        self.assertGreater(pnl_down, Decimal("0.00"))

    def test_dol_futures_fx_shocks(self) -> None:
        """Futuro DOL deve usar multiplicador de 50 e responder diretamente a choques cambiais."""
        # Short 5 contratos DOL @ 5.000 pontos (USD 250.000 nocional)
        pos_dol = PositionItem(
            position_id="POS_DOL",
            symbol="DOLZ26",
            asset_category=AssetCategory.FUTURE_DOL,
            underlying_symbol="USD_BRL",
            quantity=Decimal("-5"),
            current_price=Decimal("5000.00"),
            contract_multiplier=Decimal("50.0"),
        )

        # Desvalorização cambial de +10% (dólar sobe 500 pontos)
        scn_fx_up = RiskScenario(
            scenario_id="FX_UP",
            name="Alta do Dólar +10%",
            fx_shocks={"USD_BRL": Decimal("0.10")},
        )
        pnl = pos_dol.calculate_scenario_pnl(scn_fx_up)
        # -5 * 500.00 * 50 = -125.000,00
        self.assertEqual(pnl, Decimal("-125000.00"))

        # Apreciação cambial de -5% (dólar cai 250 pontos)
        scn_fx_down = RiskScenario(
            scenario_id="FX_DOWN",
            name="Queda do Dólar -5%",
            fx_shocks={"USD_BRL": Decimal("-0.05")},
        )
        pnl_gain = pos_dol.calculate_scenario_pnl(scn_fx_down)
        # -5 * (-250.00) * 50 = +62.500,00
        self.assertEqual(pnl_gain, Decimal("62500.00"))

    # --------------------------------------------------------------------------
    # 3. Efeito Portfólio e Compensação Multilateral (Netting)
    # --------------------------------------------------------------------------

    def test_portfolio_offsetting_equity_and_options(self) -> None:
        """Opção de venda (PUT) comprada deve amortecer a perda da ação à vista sob crash de mercado."""
        # Long 10.000 PETR4 @ R$ 36,00
        stock = PositionItem(
            position_id="STOCK",
            symbol="PETR4",
            asset_category=AssetCategory.EQUITY,
            underlying_symbol="PETR4",
            quantity=Decimal("10000"),
            current_price=Decimal("36.00"),
        )
        # Long 10.000 PUTs PETRX360 @ strike 36.00
        put_hedge = PositionItem(
            position_id="PUT_HEDGE",
            symbol="PETRX360",
            asset_category=AssetCategory.OPTION_PUT,
            underlying_symbol="PETR4",
            quantity=Decimal("10000"),
            current_price=Decimal("36.00"),
            strike=Decimal("36.00"),
            maturity_years=Decimal("0.25"),
            implied_vol=Decimal("0.30"),
            risk_free_rate=Decimal("0.1175"),
        )

        scn_crash = RiskScenario(
            scenario_id="CRASH_20",
            name="Crash -20%",
            equity_shocks={"PETR4": Decimal("-0.20")},
            volatility_shocks={"PETR4": Decimal("0.30")},
        )

        stock_loss = stock.calculate_scenario_pnl(scn_crash)
        put_gain = put_hedge.calculate_scenario_pnl(scn_crash)

        self.assertEqual(stock_loss, Decimal("-72000.00"))
        self.assertGreater(put_gain, Decimal("40000.00"))

        net_pnl = stock_loss + put_gain
        # A perda líquida hedged é substancialmente menor que a perda isolada da ação
        self.assertGreater(net_pnl, stock_loss)
        self.assertLess(abs(net_pnl), abs(stock_loss))

    # --------------------------------------------------------------------------
    # 4. Cesta de Colaterais e Haircuts
    # --------------------------------------------------------------------------

    def test_collateral_haircuts_calculation(self) -> None:
        """Itens de colateral devem aplicar haircuts regulatórios exatos da B3."""
        c_brl = CollateralItem("C1", CollateralType.CASH_BRL, "Dinheiro", Decimal("100000.00"), Decimal("0.00"))
        c_lft = CollateralItem("C2", CollateralType.FEDERAL_BONDS_LFT, "LFT", Decimal("100000.00"), Decimal("0.02"))
        c_ltn = CollateralItem("C3", CollateralType.FEDERAL_BONDS_LTN, "LTN", Decimal("100000.00"), Decimal("0.035"))
        c_usd = CollateralItem("C4", CollateralType.CASH_USD, "USD", Decimal("100000.00"), Decimal("0.10"))
        c_eq = CollateralItem("C5", CollateralType.EQUITY_IBRX, "ITUB4", Decimal("100000.00"), Decimal("0.20"))

        self.assertEqual(c_brl.effective_value, Decimal("100000.00"))
        self.assertEqual(c_lft.effective_value, Decimal("98000.00"))
        self.assertEqual(c_ltn.effective_value, Decimal("96500.00"))
        self.assertEqual(c_usd.effective_value, Decimal("90000.00"))
        self.assertEqual(c_eq.effective_value, Decimal("80000.00"))

    # --------------------------------------------------------------------------
    # 5. Gatilhos de Chamada de Margem Intradiária e Estados
    # --------------------------------------------------------------------------

    def test_margin_status_normal_when_covered(self) -> None:
        """Quando garantias cobrem integralmente o risco, status deve ser NORMAL."""
        engine = CoreRiskEngine()
        engine.add_position(PositionItem("P1", "PETR4", AssetCategory.EQUITY, "PETR4", Decimal("1000"), Decimal("30.00")))
        engine.add_scenario(RiskScenario("SCN1", "Crash", equity_shocks={"PETR4": Decimal("-0.20")}))
        # Margem requerida ~ R$ 6.000 + add-on. Depósito de R$ 50.000 cobre com folga.
        engine.add_collateral(CollateralItem("COL1", CollateralType.CASH_BRL, "Dinheiro", Decimal("50000.00"), Decimal("0.00")))

        calc = engine.calculate_intraday_margin("ACCT_NORMAL")
        self.assertEqual(calc.status, MarginStatus.NORMAL)
        self.assertFalse(calc.margin_call_triggered)
        self.assertEqual(calc.risk_deficit, Decimal("0.00"))
        self.assertEqual(calc.margin_call_amount, Decimal("0.00"))

    def test_margin_status_warning_within_tolerance(self) -> None:
        """Quando déficit de risco é inferior ou igual à tolerância regulamentar, status deve ser WARNING."""
        engine = CoreRiskEngine(regulatory_tolerance_pct=Decimal("0.10"), regulatory_tolerance_min_brl=Decimal("2000.00"))
        engine.add_position(PositionItem("P1", "PETR4", AssetCategory.EQUITY, "PETR4", Decimal("1000"), Decimal("30.00")))
        # Perda de 6.000,00
        engine.add_scenario(RiskScenario("SCN1", "Crash", equity_shocks={"PETR4": Decimal("-0.20")}))
        # Depósito de R$ 5.500,00 -> déficit de ~ R$ 650,00 (abaixo de tolerância mínima de 2.000)
        engine.add_collateral(CollateralItem("COL1", CollateralType.CASH_BRL, "Dinheiro", Decimal("5500.00"), Decimal("0.00")))

        calc = engine.calculate_intraday_margin("ACCT_WARNING")
        self.assertEqual(calc.status, MarginStatus.WARNING)
        self.assertFalse(calc.margin_call_triggered)
        self.assertGreater(calc.risk_deficit, Decimal("0.00"))

    def test_margin_call_required_triggered(self) -> None:
        """Quando déficit excede a tolerância regulamentar, dispara MARGIN_CALL_REQUIRED."""
        engine = CoreRiskEngine(regulatory_tolerance_pct=Decimal("0.05"), regulatory_tolerance_min_brl=Decimal("1000.00"))
        engine.add_position(PositionItem("P1", "PETR4", AssetCategory.EQUITY, "PETR4", Decimal("10000"), Decimal("30.00")))
        # Perda de R$ 60.000,00 + Addon liquidez R$ 1.500,00 = Margem R$ 61.500,00
        engine.add_scenario(RiskScenario("SCN1", "Crash", equity_shocks={"PETR4": Decimal("-0.20")}))
        # Colateral de R$ 45.000,00 -> Déficit de R$ 16.500,00 (teto crítico = 45.000 * 0.5 = 22.500)
        engine.add_collateral(CollateralItem("COL1", CollateralType.CASH_BRL, "Dinheiro", Decimal("45000.00"), Decimal("0.00")))

        calc = engine.calculate_intraday_margin("ACCT_CALL")
        self.assertEqual(calc.status, MarginStatus.MARGIN_CALL_REQUIRED)
        self.assertTrue(calc.margin_call_triggered)
        self.assertEqual(calc.margin_call_amount, calc.risk_deficit)

    def test_critical_margin_status_on_severe_deficit(self) -> None:
        """Quando o déficit excede 50% do colateral disponível, o status deve ser CRITICAL."""
        engine = CoreRiskEngine()
        engine.add_position(PositionItem("P1", "PETR4", AssetCategory.EQUITY, "PETR4", Decimal("100000"), Decimal("30.00")))
        # Perda de R$ 600.000,00
        engine.add_scenario(RiskScenario("SCN1", "Crash", equity_shocks={"PETR4": Decimal("-0.20")}))
        # Colateral irrisório de R$ 10.000,00
        engine.add_collateral(CollateralItem("COL1", CollateralType.CASH_BRL, "Dinheiro", Decimal("10000.00"), Decimal("0.00")))

        calc = engine.calculate_intraday_margin("ACCT_CRITICAL")
        self.assertEqual(calc.status, MarginStatus.CRITICAL)
        self.assertTrue(calc.margin_call_triggered)

    # --------------------------------------------------------------------------
    # 6. Carregamento da Fixture Oficial e Confronto de Benchmarks
    # --------------------------------------------------------------------------

    def test_load_official_fixture_and_verify_benchmarks(self) -> None:
        """Deve carregar fixtures/core_intraday_margin.json e reproduzir exatamente os benchmarks esperados."""
        self.assertTrue(CORE_FIXTURE_PATH.exists(), f"Fixture não encontrada: {CORE_FIXTURE_PATH}")

        with open(CORE_FIXTURE_PATH, "r", encoding="utf-8") as f:
            fixture_data = json.load(f)

        engine = CoreRiskEngine()
        engine.load_from_json(fixture_data)

        # Verifica quantidade de itens carregados
        self.assertEqual(len(engine.get_positions()), 6)
        self.assertEqual(len(engine.get_scenarios()), 12)
        self.assertEqual(len(engine.get_collateral()), 3)

        # Executa cálculo oficial
        calc = engine.calculate_intraday_margin(fixture_data["account_id"])
        expected = fixture_data["expected_benchmark"]

        self.assertEqual(calc.worst_case_scenario_id, expected["worst_case_scenario_id"])
        self.assertEqual(str(calc.worst_case_loss), expected["worst_case_loss"])
        self.assertEqual(str(calc.liquidity_addon), expected["liquidity_addon"])
        self.assertEqual(str(calc.concentration_addon), expected["concentration_addon"])
        self.assertEqual(str(calc.total_margin_required), expected["total_margin_required"])
        self.assertEqual(str(calc.total_collateral_deposited), expected["total_collateral_deposited"])
        self.assertEqual(str(calc.total_collateral_available), expected["total_collateral_available"])
        self.assertEqual(str(calc.risk_deficit), expected["risk_deficit"])
        self.assertEqual(str(calc.regulatory_tolerance), expected["regulatory_tolerance"])
        self.assertEqual(calc.margin_call_triggered, expected["margin_call_triggered"])
        self.assertEqual(str(calc.margin_call_amount), expected["margin_call_amount"])
        self.assertEqual(calc.status.value, expected["status"])

    # --------------------------------------------------------------------------
    # 7. Benchmark de Latência Ultrabaixa (< 1.000 µs / Sub-Milissegundo)
    # --------------------------------------------------------------------------

    def test_sub_millisecond_latency_benchmark(self) -> None:
        """Motor deve atingir latência P99 estritamente inferior a 1 ms (1.000 µs)."""
        engine = CoreRiskEngine()
        engine.load_from_json(CORE_FIXTURE_PATH)

        stats = engine.benchmark_latency(iterations=1000)
        self.assertTrue(stats["sub_millisecond"])
        self.assertLess(stats["p99_micros"], 1000.0, f"P99 de {stats['p99_micros']} µs excedeu 1.000 µs")
        self.assertLess(stats["mean_micros"], 600.0, f"Média de {stats['mean_micros']} µs excedeu 600 µs")

    # --------------------------------------------------------------------------
    # 8. Casos Extremos e Serialização de Resultado Consolidado
    # --------------------------------------------------------------------------

    def test_empty_portfolio_and_edge_cases(self) -> None:
        """Motor deve tratar carteiras vazias ou cenários nulos sem falhas."""
        fresh_engine = CoreRiskEngine()
        calc = fresh_engine.calculate_intraday_margin("EMPTY_ACCT")
        self.assertEqual(calc.worst_case_loss, Decimal("0.00"))
        self.assertEqual(calc.total_margin_required, Decimal("0.00"))
        self.assertEqual(calc.status, MarginStatus.NORMAL)
        self.assertFalse(calc.margin_call_triggered)

        # Testa serialização do resultado consolidado
        d_calc = calc.to_dict()
        self.assertIn("calculation_id", d_calc)
        self.assertEqual(d_calc["worst_case_loss"], "0.00")
        self.assertEqual(d_calc["status"], "NORMAL")


if __name__ == "__main__":
    unittest.main()
