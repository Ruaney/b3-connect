"""
Suíte de Testes Unitários — Simulador e Rebalanceamento de Índices B3 (FL-066)
=============================================================================
Validação determinística do cálculo do Índice de Negociabilidade (IN),
filtros regulatórios de elegibilidade, corte dos 85% de representatividade,
ponderação por free float com teto de 20%, conservação estrita de pesos
(Soma(W_i) == 100%) e ciclo quadrimestral de prévias.
"""

import unittest
from decimal import Decimal
from pathlib import Path

from src.index_rebalancing import (
    AssetNegotiabilityScore,
    AssetTradingData,
    IndexConstituent,
    IndexRebalancingEngine,
    IndexRebalancingError,
    IndexType,
    PreviewStage,
    RebalancingResult
)


class TestIndexRebalancing(unittest.TestCase):

    def setUp(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "index_rebalancing.json"
        self.engine, self.assets, self.data = IndexRebalancingEngine.load_from_fixture(self.fixture_file)

    def test_load_from_fixture(self):
        """Verifica o carregamento íntegro da fixture e parâmetros do motor."""
        self.assertEqual(len(self.assets), 28)
        self.assertEqual(self.engine.min_presence, Decimal("0.95"))
        self.assertEqual(self.engine.min_volume_share, Decimal("0.001"))
        self.assertEqual(self.engine.penny_stock_min_price, Decimal("1.00"))
        self.assertEqual(self.engine.in_cutoff_ratio, Decimal("0.85"))
        self.assertEqual(self.engine.default_cap_limit, Decimal("0.20"))

        # Testa ativo Petrobras
        petr4 = next(a for a in self.assets if a.ticker == "PETR4")
        self.assertEqual(petr4.trades_count, 520000)
        self.assertEqual(petr4.closing_price, Decimal("38.50"))
        self.assertFalse(petr4.is_penny_stock)
        self.assertFalse(petr4.is_in_judicial_recovery)

    def test_calculate_single_in(self):
        """Verifica a precisão matemática da fórmula individual do IN."""
        # trades: 100 de 1000 (10%), volume: 50M de 200M (25%) -> IN = sqrt(0.10 * 0.25) = sqrt(0.025) = 0.15811388...
        in_val = IndexRebalancingEngine.calculate_single_in(
            trades_count=100,
            total_trades=1000,
            financial_volume=Decimal("50000000.00"),
            total_volume=Decimal("200000000.00")
        )
        expected = Decimal("0.025").sqrt()
        self.assertEqual(in_val, expected)

        # Erros com valores negativos ou zerados
        with self.assertRaises(IndexRebalancingError):
            IndexRebalancingEngine.calculate_single_in(10, 0, Decimal("100"), Decimal("1000"))
        with self.assertRaises(IndexRebalancingError):
            IndexRebalancingEngine.calculate_single_in(10, 100, Decimal("100"), Decimal("0"))
        with self.assertRaises(IndexRebalancingError):
            IndexRebalancingEngine.calculate_single_in(-5, 100, Decimal("100"), Decimal("1000"))

    def test_score_universe_and_eligibility_filters(self):
        """Verifica pontuação de IN e aplicação estrita dos 4 filtros de elegibilidade."""
        scores = self.engine.score_universe(self.assets)
        self.assertEqual(len(scores), 28)

        score_map = {s.ticker: s for s in scores}

        # 1. Recuperação Judicial (OIBR3, AMER3 devem falhar no filtro)
        oibr3 = score_map["OIBR3"]
        self.assertFalse(oibr3.meets_recovery_filter)
        self.assertFalse(oibr3.is_eligible)

        amer3 = score_map["AMER3"]
        self.assertFalse(amer3.meets_recovery_filter)
        self.assertFalse(amer3.is_eligible)

        # 2. Penny Stock (PENN3 com preço R$ 0,68 deve falhar)
        penn3 = score_map["PENN3"]
        self.assertFalse(penn3.meets_penny_stock_filter)
        self.assertFalse(penn3.is_eligible)

        # 3. Presença em Pregões (INAC3 com presença 78% < 95% deve falhar)
        inac3 = score_map["INAC3"]
        self.assertFalse(inac3.meets_presence)
        self.assertFalse(inac3.is_eligible)

        # 4. Volume mínimo (LOWV3 com volume < 0.1% do mercado deve falhar)
        lowv3 = score_map["LOWV3"]
        self.assertFalse(lowv3.meets_volume_share)
        self.assertFalse(lowv3.is_eligible)

        # Ativos líderes devem estar elegíveis e no top ranking
        self.assertTrue(score_map["PETR4"].is_eligible)
        self.assertTrue(score_map["VALE3"].is_eligible)
        self.assertEqual(score_map["PETR4"].in_rank, 1)
        self.assertEqual(score_map["VALE3"].in_rank, 2)

        # Verifica corte dos 85% de representatividade
        in_85_tickers = [s.ticker for s in scores if s.in_85_percent_cutoff]
        self.assertIn("PETR4", in_85_tickers)
        self.assertIn("VALE3", in_85_tickers)
        self.assertIn("JBSS3", in_85_tickers) # Ativo que atinge o limiar dos 85%
        # Ativos menores fora dos 85%
        self.assertFalse(score_map["TUPY3"].in_85_percent_cutoff)
        self.assertFalse(score_map["LEVE3"].in_85_percent_cutoff)

    def test_capping_algorithm_and_weight_conservation(self):
        """
        Verifica formalmente a Lei de Conservação de Pesos:
        Soma(W_i) == Decimal('1.00000000') (100%)
        e aplicação do teto de 20% com redistribuição proporcional.
        """
        scores = self.engine.score_universe(self.assets)
        selected = self.engine.filter_constituents(self.assets, scores, IndexType.IBOV)
        constituents = self.engine.calculate_capped_weights(selected, IndexType.IBOV, cap_limit=Decimal("0.20"))

        # 1. Lei de Conservação: Soma estrita == 1.00000000
        weight_sum = sum(c.weight for c in constituents)
        self.assertEqual(weight_sum, Decimal("1.00000000"))

        # 2. Teto regulatório: Nenhum ativo pode ultrapassar 20.00%
        for c in constituents:
            self.assertLessEqual(c.weight, Decimal("0.20000000"))

        # 3. VALE3 tinha peso uncapped ~22.13% -> deve estar capped exatamente em 20.00%
        vale3 = next(c for c in constituents if c.ticker == "VALE3")
        self.assertEqual(vale3.weight, Decimal("0.20000000"))
        self.assertTrue(vale3.is_capped)
        self.assertGreater(vale3.uncapped_weight, Decimal("0.20000000"))

        # 4. PETR4 e ITUB4 tiveram seus pesos aumentados proporcionalmente pela sobra de VALE3
        itub4 = next(c for c in constituents if c.ticker == "ITUB4")
        self.assertFalse(itub4.is_capped)
        self.assertLess(itub4.weight, Decimal("0.20000000"))

    def test_ibovespa_rebalancing_and_invariants(self):
        """Executa rebalanceamento completo do Ibovespa e valida invariantes formais."""
        prev_portfolio = self.data.get("previous_portfolio_ibov")
        res = self.engine.rebalance(
            self.assets,
            index_type=IndexType.IBOV,
            previous_portfolio=prev_portfolio,
            portfolio_notional=Decimal("1000000000.00")
        )

        self.assertEqual(res.index_type, IndexType.IBOV)
        self.assertEqual(res.weight_sum, Decimal("1.00000000"))
        self.assertTrue(res.total_constituents > 0)
        self.assertTrue(res.divisor > Decimal("0"))
        self.assertTrue(res.index_level > Decimal("0"))

        # Verifica turnover em relação à carteira anterior
        self.assertGreater(res.turnover_ratio, Decimal("0"))

        # Inclusões e exclusões
        self.assertIsInstance(res.additions, list)
        self.assertIsInstance(res.deletions, list)

        # Invariantes formais
        inv = self.engine.verify_invariants(res, self.assets)
        self.assertTrue(inv["weight_conservation"], "Soma dos pesos divergente de 100%")
        self.assertTrue(inv["cap_limit_respected"], "Teto de 20% violado")
        self.assertTrue(inv["no_ineligible_constituents"], "Constituinte inelegível presente na carteira")
        self.assertTrue(inv["theoretical_quantities_positive"], "Quantidade teórica não positiva")
        self.assertTrue(inv["representativity_satisfied"], "Representatividade não satisfeita")
        self.assertTrue(inv["all_passed"], "Falha nas invariantes formais da B3")

    def test_ibrx100_rebalancing(self):
        """Verifica rebalanceamento do IBrX-100 (top ativos elegíveis ordenados por IN)."""
        res = self.engine.rebalance(self.assets, index_type=IndexType.IBRX100)
        self.assertEqual(res.index_type, IndexType.IBRX100)
        self.assertEqual(res.weight_sum, Decimal("1.00000000"))
        self.assertEqual(res.total_constituents, 23) # 23 ativos 100% elegíveis no universo

        # Nenhum inelegível (OIBR3, AMER3, PENN3, INAC3, LOWV3)
        tickers = [c.ticker for c in res.constituents]
        self.assertNotIn("OIBR3", tickers)
        self.assertNotIn("AMER3", tickers)
        self.assertNotIn("PENN3", tickers)
        self.assertNotIn("INAC3", tickers)
        self.assertNotIn("LOWV3", tickers)

        inv = self.engine.verify_invariants(res, self.assets)
        self.assertTrue(inv["all_passed"])

    def test_smll_rebalancing(self):
        """Verifica rebalanceamento do Índice Small Cap (SMLL)."""
        res = self.engine.rebalance(self.assets, index_type=IndexType.SMLL)
        self.assertEqual(res.index_type, IndexType.SMLL)
        self.assertEqual(res.weight_sum, Decimal("1.00000000"))

        # Deve conter small caps como POMO4, TUPY3, LEVE3, SMTO3
        tickers = [c.ticker for c in res.constituents]
        self.assertIn("POMO4", tickers)
        self.assertIn("TUPY3", tickers)
        self.assertIn("LEVE3", tickers)

        # Não deve conter as mega caps (VALE3, PETR4, ITUB4)
        self.assertNotIn("VALE3", tickers)
        self.assertNotIn("PETR4", tickers)
        self.assertNotIn("ITUB4", tickers)

        inv = self.engine.verify_invariants(res, self.assets)
        self.assertTrue(inv["all_passed"])

    def test_idiv_rebalancing(self):
        """Verifica rebalanceamento do Índice Dividendos (IDIV)."""
        res = self.engine.rebalance(self.assets, index_type=IndexType.IDIV)
        self.assertEqual(res.index_type, IndexType.IDIV)
        self.assertEqual(res.weight_sum, Decimal("1.00000000"))

        # Deve conter campeãs de proventos
        tickers = [c.ticker for c in res.constituents]
        self.assertIn("TAEE11", tickers)
        self.assertIn("CPFE3", tickers)
        self.assertIn("BBAS3", tickers)
        self.assertIn("LEVE3", tickers)

        inv = self.engine.verify_invariants(res, self.assets)
        self.assertTrue(inv["all_passed"])

    def test_simulate_quadrimestral_cycle(self):
        """Simula a sequência temporal de prévias: 1ª Prévia -> 2ª Prévia -> Carteira Definitiva."""
        cycle = self.engine.simulate_quadrimestral_cycle(
            self.assets, self.assets, self.assets,
            index_type=IndexType.IBOV,
            period="2027-Q1"
        )

        self.assertIn(PreviewStage.PREVIEW_1, cycle)
        self.assertIn(PreviewStage.PREVIEW_2, cycle)
        self.assertIn(PreviewStage.PREVIEW_3_DEFINITIVE, cycle)

        p1 = cycle[PreviewStage.PREVIEW_1]
        p2 = cycle[PreviewStage.PREVIEW_2]
        p3 = cycle[PreviewStage.PREVIEW_3_DEFINITIVE]

        # Todos devem conservar estritamente 100% dos pesos
        self.assertEqual(p1.weight_sum, Decimal("1.00000000"))
        self.assertEqual(p2.weight_sum, Decimal("1.00000000"))
        self.assertEqual(p3.weight_sum, Decimal("1.00000000"))

        # Continuidade de nível do índice entre prévias com mesmos preços
        self.assertEqual(p2.index_level, p1.index_level)
        self.assertEqual(p3.index_level, p2.index_level)

    def test_error_conditions(self):
        """Testa validações defensivas e tratamento de exceções."""
        # Universo vazio
        with self.assertRaises(IndexRebalancingError):
            self.engine.score_universe([])

        # Cap limit impossível (ex: 2 ativos com cap 0.20 -> max 0.40 < 1.0)
        two_assets = self.assets[:2]
        with self.assertRaises(IndexRebalancingError):
            self.engine.calculate_capped_weights(two_assets, IndexType.IBOV, cap_limit=Decimal("0.20"))

        # Cap limit nulo ou negativo
        with self.assertRaises(IndexRebalancingError):
            self.engine.calculate_capped_weights(two_assets, IndexType.IBOV, cap_limit=Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
