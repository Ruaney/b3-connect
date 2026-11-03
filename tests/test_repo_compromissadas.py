"""Testes unitários para o módulo de Operações Compromissadas e Repo B3 (FL-023).

Valida:
1. Modelagem rigorosa de compromissadas (públicas e privadas, overnight e a prazo).
2. Cálculo exato de PU de volta no regime de juros compostos DU/252 (PRE e % CDI Cetip).
3. Batimento bilateral de ordens e detecção de divergências contratuais.
4. Validação de suficiência de lastro em custódia e rejeição determinística.
5. Ciclo de vida completo de liquidação D0 (ida) e D+N (volta) com e sem livre movimentação.
6. Inadimplência do cessionário na devolução de lastro com livre movimentação (Default).
7. Leitura e integridade das fixtures oficiais de compromissadas.
"""

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.repo_compromissadas import (
    CollateralAssetType,
    CollateralRight,
    CustodyPosition,
    RateType,
    RepoBilateralDivergenceError,
    RepoCollateral,
    RepoDefaultError,
    RepoError,
    RepoInsufficientCollateralError,
    RepoLifecycleEngine,
    RepoModality,
    RepoOrderProposal,
    RepoParticipant,
    RepoRate,
    RepoSettlementError,
    RepoSide,
    RepoStateError,
    RepoStatus,
    RepoTrade,
    RepoValidationError,
    SettlementLegResult,
    calculate_pu_volta_cdi_effective,
    calculate_pu_volta_cdi_projected,
    calculate_pu_volta_cdi_spread,
    calculate_pu_volta_pre,
)


class TestRepoModelsAndCalculations(unittest.TestCase):
    """Testes de matemática financeira DU/252 e modelos de compromissada."""

    def setUp(self) -> None:
        self.fixtures_dir = ROOT_DIR / "fixtures"

    def test_pu_volta_pre_exact(self) -> None:
        """Valida o cálculo de PU de volta pré-fixado na convenção DU/252."""
        # PU Ida = 1000.00, Taxa = 12.50% a.a., DU = 30
        pu_ida = Decimal("1000.00000000")
        taxa_aa = Decimal("12.50")
        du = 30

        pu_volta = calculate_pu_volta_pre(pu_ida, taxa_aa, du)
        # (1 + 0.1250)^(30/252) = 1.014120556...
        # PU esperado = 1014.12055634
        self.assertEqual(pu_volta, Decimal("1014.12055634"))

    def test_pu_volta_cdi_projected_cetip_precision(self) -> None:
        """Valida o cálculo de PU de volta indexado ao CDI com padrão Cetip/B3."""
        # NTN-B a 100% do CDI, CDI projetado 11.25% a.a., 30 dias úteis
        pu_ida = Decimal("4125.50000000")
        cdi_percent = Decimal("100.00")
        cdi_aa = Decimal("11.25")
        du = 30

        pu_volta = calculate_pu_volta_cdi_projected(pu_ida, cdi_percent, cdi_aa, du)
        # FDI = round((1 + 0.1125)^(1/252), 8) = 1.00042314
        # Fator 30 dias = round(1.00042314^30, 8) = 1.01277239
        # PU Volta = round(4125.50 * 1.01277239, 8) = 4178.19249495
        self.assertEqual(pu_volta, Decimal("4178.19249495"))

    def test_pu_volta_cdi_effective_series(self) -> None:
        """Valida a acumulação com série diária de taxas CDI observadas."""
        pu_ida = Decimal("10000.00000000")
        cdi_percent = Decimal("100.00")
        # 3 dias com taxas diárias anualizadas de 11.25%, 11.25% e 11.50%
        rates = [Decimal("11.25"), Decimal("11.25"), Decimal("11.50")]

        pu_volta = calculate_pu_volta_cdi_effective(pu_ida, cdi_percent, rates)
        self.assertGreater(pu_volta, pu_ida)
        # Verifica 8 casas decimais
        self.assertEqual(pu_volta.as_tuple().exponent, -8)

    def test_pu_volta_cdi_spread(self) -> None:
        """Valida cálculo de compromissada com CDI + spread anual."""
        pu_ida = Decimal("1000.00000000")
        cdi_percent = Decimal("100.00")
        cdi_aa = Decimal("11.25")
        spread_aa = Decimal("1.50")
        du = 21

        pu_volta = calculate_pu_volta_cdi_spread(pu_ida, cdi_percent, cdi_aa, spread_aa, du)
        pu_volta_pure_cdi = calculate_pu_volta_cdi_projected(pu_ida, cdi_percent, cdi_aa, du)
        self.assertGreater(pu_volta, pu_volta_pure_cdi)

    def test_yield_financial_and_percentage(self) -> None:
        """Valida os métodos auxiliares de rendimento financeiro da RepoTrade."""
        trade = RepoTrade(
            trade_id="REPO-TEST-001",
            modality=RepoModality.PUBLIC_TERM,
            collateral_right=CollateralRight.COM_LIVRE_MOVIMENTACAO,
            collateral=RepoCollateral(
                asset_code="NTNB-2035",
                asset_type=CollateralAssetType.NTNB,
                issuer="TESOURO NACIONAL",
                maturity_date=date(2035, 5, 15),
                is_public=True,
            ),
            quantity=1000,
            trade_date=date(2026, 11, 3),
            settlement_date_ida=date(2026, 11, 3),
            settlement_date_volta=date(2026, 12, 16),
            business_days=30,
            seller=RepoParticipant("00000033", "330001"),
            buyer=RepoParticipant("00001050", "105001"),
            rate=RepoRate(RateType.CDI_PERCENT, Decimal("100.00")),
            unit_price_ida=Decimal("4125.50000000"),
            financial_amount_ida=Decimal("4125500.00"),
            unit_price_volta=Decimal("4178.19249495"),
            financial_amount_volta=Decimal("4178192.49"),
            status=RepoStatus.MATCHED,
        )

        expected_yield = Decimal("4178192.49") - Decimal("4125500.00")
        self.assertEqual(trade.yield_financial(), expected_yield)
        self.assertEqual(trade.yield_financial(), Decimal("52692.49"))
        self.assertAlmostEqual(float(trade.yield_percentage()), 1.2772, places=3)

    def test_fixture_repo_trade_valid(self) -> None:
        """Valida o carregamento da fixture oficial repo_trade_valid.json."""
        fixture_file = self.fixtures_dir / "repo_trade_valid.json"
        self.assertTrue(fixture_file.exists(), f"Fixture não encontrada: {fixture_file}")

        trade = RepoTrade.from_file(fixture_file)
        self.assertEqual(trade.trade_id, "REPO-NTNB-20261103-001")
        self.assertEqual(trade.modality, RepoModality.PUBLIC_TERM)
        self.assertEqual(trade.collateral_right, CollateralRight.COM_LIVRE_MOVIMENTACAO)
        self.assertEqual(trade.collateral.asset_code, "NTNB-20350515")
        self.assertEqual(trade.collateral.asset_type, CollateralAssetType.NTNB)
        self.assertTrue(trade.collateral.is_public)
        self.assertEqual(trade.quantity, 1000)
        self.assertEqual(trade.unit_price_ida, Decimal("4125.50000000"))
        self.assertEqual(trade.financial_amount_ida, Decimal("4125500.00"))
        self.assertEqual(trade.unit_price_volta, Decimal("4178.19249495"))
        self.assertEqual(trade.financial_amount_volta, Decimal("4178192.49"))
        self.assertEqual(trade.status, RepoStatus.MATCHED)

    def test_fixture_repo_trade_overnight_valid(self) -> None:
        """Valida o carregamento da fixture oficial repo_trade_overnight_valid.json."""
        fixture_file = self.fixtures_dir / "repo_trade_overnight_valid.json"
        self.assertTrue(fixture_file.exists(), f"Fixture não encontrada: {fixture_file}")

        trade = RepoTrade.from_file(fixture_file)
        self.assertEqual(trade.trade_id, "REPO-LFT-20261103-002")
        self.assertEqual(trade.modality, RepoModality.PUBLIC_OVERNIGHT)
        self.assertEqual(trade.collateral_right, CollateralRight.SEM_LIVRE_MOVIMENTACAO)
        self.assertEqual(trade.business_days, 1)
        self.assertEqual(trade.quantity, 500)
        self.assertEqual(trade.financial_amount_ida, Decimal("7600000.00"))
        self.assertEqual(trade.financial_amount_volta, Decimal("7603215.86"))

    def test_serialization_roundtrip(self) -> None:
        """Garante que to_dict / to_json e from_dict / from_json preservam todos os campos."""
        fixture_file = self.fixtures_dir / "repo_trade_valid.json"
        trade = RepoTrade.from_file(fixture_file)
        json_output = trade.to_json()
        trade_reconstructed = RepoTrade.from_json(json_output)

        self.assertEqual(trade.trade_id, trade_reconstructed.trade_id)
        self.assertEqual(trade.unit_price_ida, trade_reconstructed.unit_price_ida)
        self.assertEqual(trade.unit_price_volta, trade_reconstructed.unit_price_volta)
        self.assertEqual(trade.financial_amount_ida, trade_reconstructed.financial_amount_ida)
        self.assertEqual(trade.financial_amount_volta, trade_reconstructed.financial_amount_volta)
        self.assertEqual(trade.seller.custody_account, trade_reconstructed.seller.custody_account)

    def test_validation_errors(self) -> None:
        """Valida rejeição de entradas com parâmetros inconsistentes."""
        # Quantidade zero ou negativa
        with self.assertRaises(RepoValidationError):
            RepoTrade(
                trade_id="REPO-BAD-QTY",
                modality=RepoModality.PUBLIC_TERM,
                collateral_right=CollateralRight.COM_LIVRE_MOVIMENTACAO,
                collateral=RepoCollateral("NTNB-2035", CollateralAssetType.NTNB, "TN", date(2035, 1, 1), True),
                quantity=0,
                trade_date=date(2026, 11, 3),
                settlement_date_ida=date(2026, 11, 3),
                settlement_date_volta=date(2026, 12, 16),
                business_days=30,
                seller=RepoParticipant("33", "330001"),
                buyer=RepoParticipant("1050", "105001"),
                rate=RepoRate(RateType.PRE, Decimal("12.0")),
                unit_price_ida=Decimal("1000.0"),
                financial_amount_ida=Decimal("0.0"),
                unit_price_volta=Decimal("1010.0"),
                financial_amount_volta=Decimal("0.0"),
            )


class TestRepoBilateralMatching(unittest.TestCase):
    """Testes de batimento bilateral (Matching Engine) e detecção de divergências."""

    def setUp(self) -> None:
        self.engine = RepoLifecycleEngine()
        self.seller_participant = RepoParticipant(
            participant_id="00000033", custody_account="330001", settlement_account="00000339911"
        )
        self.buyer_participant = RepoParticipant(
            participant_id="00001050", custody_account="105001", settlement_account="00010508823"
        )

    def _create_seller_proposal(self, **kwargs) -> RepoOrderProposal:
        defaults = {
            "order_id": "ORD-SELL-001",
            "side": RepoSide.VENDA_RECOMPRA,
            "participant": self.seller_participant,
            "counterparty_id": "00001050",
            "modality": RepoModality.PUBLIC_TERM,
            "collateral_right": CollateralRight.COM_LIVRE_MOVIMENTACAO,
            "asset_code": "NTNB-20350515",
            "asset_type": CollateralAssetType.NTNB,
            "quantity": 1000,
            "trade_date": date(2026, 11, 3),
            "settlement_date_ida": date(2026, 11, 3),
            "settlement_date_volta": date(2026, 12, 16),
            "business_days": 30,
            "unit_price_ida": Decimal("4125.50000000"),
            "rate_type": RateType.CDI_PERCENT,
            "rate_value": Decimal("100.00"),
            "benchmark_annual_rate": Decimal("11.25"),
        }
        defaults.update(kwargs)
        return RepoOrderProposal(**defaults)

    def _create_buyer_proposal(self, **kwargs) -> RepoOrderProposal:
        defaults = {
            "order_id": "ORD-BUY-001",
            "side": RepoSide.COMPRA_REVENDA,
            "participant": self.buyer_participant,
            "counterparty_id": "00000033",
            "modality": RepoModality.PUBLIC_TERM,
            "collateral_right": CollateralRight.COM_LIVRE_MOVIMENTACAO,
            "asset_code": "NTNB-20350515",
            "asset_type": CollateralAssetType.NTNB,
            "quantity": 1000,
            "trade_date": date(2026, 11, 3),
            "settlement_date_ida": date(2026, 11, 3),
            "settlement_date_volta": date(2026, 12, 16),
            "business_days": 30,
            "unit_price_ida": Decimal("4125.50000000"),
            "rate_type": RateType.CDI_PERCENT,
            "rate_value": Decimal("100.00"),
            "benchmark_annual_rate": Decimal("11.25"),
        }
        defaults.update(kwargs)
        return RepoOrderProposal(**defaults)

    def test_matching_success_with_free_movement(self) -> None:
        """Valida matching perfeito de compromissada com livre movimentação."""
        prop_seller = self._create_seller_proposal()
        prop_buyer = self._create_buyer_proposal()

        trade = self.engine.match_proposals(prop_seller, prop_buyer, trade_id="REPO-TEST-MATCHED-01")

        self.assertEqual(trade.status, RepoStatus.MATCHED)
        self.assertEqual(trade.trade_id, "REPO-TEST-MATCHED-01")
        self.assertEqual(trade.collateral_right, CollateralRight.COM_LIVRE_MOVIMENTACAO)
        self.assertEqual(trade.unit_price_volta, Decimal("4178.19249495"))
        self.assertEqual(trade.financial_amount_volta, Decimal("4178192.49"))
        self.assertIn("REPO-TEST-MATCHED-01", self.engine.trades)

    def test_matching_divergence_rate(self) -> None:
        """Rejeita quando comprador e vendedor enviam taxas divergentes."""
        prop_seller = self._create_seller_proposal(rate_value=Decimal("100.00"))
        prop_buyer = self._create_buyer_proposal(rate_value=Decimal("102.00"))

        with self.assertRaises(RepoBilateralDivergenceError) as ctx:
            self.engine.match_proposals(prop_seller, prop_buyer)
        self.assertIn("Divergência na taxa pactuada", str(ctx.exception))

    def test_matching_divergence_quantity(self) -> None:
        """Rejeita quando a quantidade de títulos difere."""
        prop_seller = self._create_seller_proposal(quantity=1000)
        prop_buyer = self._create_buyer_proposal(quantity=500)

        with self.assertRaises(RepoBilateralDivergenceError) as ctx:
            self.engine.match_proposals(prop_seller, prop_buyer)
        self.assertIn("Divergência de quantidade", str(ctx.exception))

    def test_matching_divergence_pu_ida(self) -> None:
        """Rejeita quando o PU de ida diverge."""
        prop_seller = self._create_seller_proposal(unit_price_ida=Decimal("4125.50000000"))
        prop_buyer = self._create_buyer_proposal(unit_price_ida=Decimal("4120.00000000"))

        with self.assertRaises(RepoBilateralDivergenceError) as ctx:
            self.engine.match_proposals(prop_seller, prop_buyer)
        self.assertIn("Divergência no PU de ida", str(ctx.exception))

    def test_matching_divergence_collateral_right(self) -> None:
        """Rejeita quando uma ponta quer com livre movimentação e a outra sem."""
        prop_seller = self._create_seller_proposal(
            collateral_right=CollateralRight.COM_LIVRE_MOVIMENTACAO
        )
        prop_buyer = self._create_buyer_proposal(
            collateral_right=CollateralRight.SEM_LIVRE_MOVIMENTACAO
        )

        with self.assertRaises(RepoBilateralDivergenceError) as ctx:
            self.engine.match_proposals(prop_seller, prop_buyer)
        self.assertIn("Divergência no direito sobre o lastro", str(ctx.exception))

    def test_matching_divergence_asset(self) -> None:
        """Rejeita quando o código do ativo difere."""
        prop_seller = self._create_seller_proposal(asset_code="NTNB-20350515")
        prop_buyer = self._create_buyer_proposal(asset_code="LFT-20290301")

        with self.assertRaises(RepoBilateralDivergenceError) as ctx:
            self.engine.match_proposals(prop_seller, prop_buyer)
        self.assertIn("Divergência no código do ativo", str(ctx.exception))

    def test_matching_same_account_rejection(self) -> None:
        """Rejeita compromissada entre a mesma conta de custódia."""
        same_participant = RepoParticipant(participant_id="00000033", custody_account="330001")
        prop_seller = self._create_seller_proposal(participant=same_participant)
        prop_buyer = self._create_buyer_proposal(
            participant=same_participant, counterparty_id="00000033"
        )

        with self.assertRaises(RepoBilateralDivergenceError) as ctx:
            self.engine.match_proposals(prop_seller, prop_buyer)
        self.assertIn("Contas de custódia idênticas", str(ctx.exception))


class TestRepoSettlementAndLifecycle(unittest.TestCase):
    """Testes de liquidação D0 e D+N e integridade do livro de custódia/caixa."""

    def setUp(self) -> None:
        self.fixtures_dir = ROOT_DIR / "fixtures"
        self.engine = RepoLifecycleEngine()

    def test_settlement_lifecycle_with_free_movement(self) -> None:
        """Valida ciclo completo D0 -> D+N de compromissada com livre movimentação."""
        trade = RepoTrade.from_file(self.fixtures_dir / "repo_trade_valid.json")
        self.engine.trades[trade.trade_id] = trade

        # Configura saldo inicial de custódia e caixa
        # Cedente (vendedor): 1500 NTN-B disponíveis, R$ 0.00
        # Cessionário (comprador): 0 NTN-B, R$ 10.000.000,00
        self.engine.credit_custody("330001", "NTNB-20350515", 1500)
        self.engine.set_cash_balance("00000339911", Decimal("0.00"))
        self.engine.set_cash_balance("00010508823", Decimal("10000000.00"))

        # --- Liquidação Perna de Ida (D0) ---
        res_ida = self.engine.settle_leg1_d0(trade.trade_id)
        self.assertTrue(res_ida.success)
        self.assertEqual(res_ida.status_after, RepoStatus.LEG1_SETTLED)
        self.assertEqual(trade.status, RepoStatus.LEG1_SETTLED)

        # Checagem de Custódia pós-Ida (com livre movimentação: títulos foram transferidos)
        seller_pos_ida = self.engine.get_custody_position("330001", "NTNB-20350515")
        buyer_pos_ida = self.engine.get_custody_position("105001", "NTNB-20350515")
        self.assertEqual(seller_pos_ida.available_qty, 500)     # 1500 - 1000
        self.assertEqual(seller_pos_ida.locked_repo_qty, 0)
        self.assertEqual(buyer_pos_ida.available_qty, 1000)     # Recebeu 1000 com livre movimentação

        # Checagem Financeira pós-Ida
        self.assertEqual(
            self.engine.get_cash_balance("00000339911"), Decimal("4125500.00")
        )
        self.assertEqual(
            self.engine.get_cash_balance("00010508823"),
            Decimal("10000000.00") - Decimal("4125500.00"),
        )

        # --- Liquidação Perna de Volta (D+N) ---
        res_volta = self.engine.settle_leg2_dn(trade.trade_id)
        self.assertTrue(res_volta.success)
        self.assertEqual(res_volta.status_after, RepoStatus.LEG2_SETTLED)
        self.assertEqual(trade.status, RepoStatus.LEG2_SETTLED)

        # Checagem de Custódia pós-Volta (títulos devolvidos ao cedente)
        seller_pos_volta = self.engine.get_custody_position("330001", "NTNB-20350515")
        buyer_pos_volta = self.engine.get_custody_position("105001", "NTNB-20350515")
        self.assertEqual(seller_pos_volta.available_qty, 1500)  # Restaurado para 1500
        self.assertEqual(buyer_pos_volta.available_qty, 0)      # Devolveu os 1000

        # Checagem Financeira pós-Volta (Cedente pagou capital + juros)
        expected_buyer_cash = (
            Decimal("10000000.00") - Decimal("4125500.00") + Decimal("4178192.49")
        )
        self.assertEqual(self.engine.get_cash_balance("00010508823"), expected_buyer_cash)
        expected_seller_cash = Decimal("4125500.00") - Decimal("4178192.49")
        self.assertEqual(self.engine.get_cash_balance("00000339911"), expected_seller_cash)

    def test_settlement_lifecycle_without_free_movement(self) -> None:
        """Valida ciclo D0 -> D+N sem livre movimentação (lastro bloqueado sob penhor)."""
        trade = RepoTrade.from_file(self.fixtures_dir / "repo_trade_overnight_valid.json")
        self.engine.trades[trade.trade_id] = trade

        # Cedente possui 500 LFT
        self.engine.credit_custody("330001", "LFT-20290301", 500)
        self.engine.set_cash_balance("00000339911", Decimal("0.00"))
        self.engine.set_cash_balance("00010508823", Decimal("10000000.00"))

        # Liquidação Ida
        self.engine.settle_leg1_d0(trade.trade_id)
        seller_pos_ida = self.engine.get_custody_position("330001", "LFT-20290301")
        buyer_pos_ida = self.engine.get_custody_position("105001", "LFT-20290301")

        # Títulos permanecem na conta do cedente, mas bloqueados
        self.assertEqual(seller_pos_ida.available_qty, 0)
        self.assertEqual(seller_pos_ida.locked_repo_qty, 500)
        self.assertEqual(buyer_pos_ida.available_qty, 0)

        # Liquidação Volta
        self.engine.settle_leg2_dn(trade.trade_id)
        seller_pos_volta = self.engine.get_custody_position("330001", "LFT-20290301")
        self.assertEqual(seller_pos_volta.available_qty, 500)
        self.assertEqual(seller_pos_volta.locked_repo_qty, 0)
        self.assertEqual(trade.status, RepoStatus.LEG2_SETTLED)

    def test_rejection_insufficient_collateral_d0(self) -> None:
        """Rejeita liquidação de ida quando o cedente não possui títulos suficientes."""
        trade = RepoTrade.from_file(self.fixtures_dir / "repo_trade_valid.json")
        self.engine.trades[trade.trade_id] = trade

        # Cedente possui apenas 400 títulos (necessários 1000)
        self.engine.credit_custody("330001", "NTNB-20350515", 400)

        with self.assertRaises(RepoInsufficientCollateralError) as ctx:
            self.engine.settle_leg1_d0(trade.trade_id)
        self.assertIn("Lastro insuficiente", str(ctx.exception))
        self.assertEqual(trade.status, RepoStatus.REJECTED)

    def test_buyer_default_on_leg2_when_free_movement_collateral_missing(self) -> None:
        """Acusa Default do cessionário caso não possua os títulos para devolver em D+N."""
        trade = RepoTrade.from_file(self.fixtures_dir / "repo_trade_valid.json")
        self.engine.trades[trade.trade_id] = trade

        # Cedente fornece o lastro na ida
        self.engine.credit_custody("330001", "NTNB-20350515", 1000)
        self.engine.settle_leg1_d0(trade.trade_id)

        # Suponha que o cessionário alienou os títulos no mercado e ficou com saldo 0
        buyer_pos = self.engine.get_custody_position("105001", "NTNB-20350515")
        buyer_pos.available_qty = 0  # Despejou o lastro e não recompôs

        with self.assertRaises(RepoDefaultError) as ctx:
            self.engine.settle_leg2_dn(trade.trade_id)
        self.assertIn("Inadimplência do cessionário", str(ctx.exception))
        self.assertEqual(trade.status, RepoStatus.DEFAULTED)

    def test_settlement_invalid_state_transitions(self) -> None:
        """Rejeita liquidação fora da ordem cronológica da máquina de estados."""
        trade = RepoTrade.from_file(self.fixtures_dir / "repo_trade_valid.json")
        self.engine.trades[trade.trade_id] = trade

        # Tentar liquidar perna de volta antes da perna de ida
        with self.assertRaises(RepoStateError):
            self.engine.settle_leg2_dn(trade.trade_id)

        # Liquidar perna de ida
        self.engine.credit_custody("330001", "NTNB-20350515", 1000)
        self.engine.settle_leg1_d0(trade.trade_id)

        # Tentar liquidar perna de ida novamente
        with self.assertRaises(RepoStateError):
            self.engine.settle_leg1_d0(trade.trade_id)


if __name__ == "__main__":
    unittest.main()
