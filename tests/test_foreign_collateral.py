"""Bateria de testes automatizados para Garantias em Moeda Estrangeira e Ouro (FL-022) — Câmara B3."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.foreign_collateral import (
    BasketEvaluationSummary,
    BlockedCurrencyError,
    CollateralEvaluationResult,
    CollateralHaircut,
    ForeignAssetType,
    ForeignCollateralDeposit,
    ForeignCollateralEngine,
    HaircutConfigurationError,
    IneligibleAssetError,
    InvalidDepositDataError,
    UnsupportedCurrencyError,
)


class TestForeignCollateralValuation(unittest.TestCase):
    """Testes de avaliação financeira, conversão PTAX e cobertura CORE para cesta de garantias."""

    def setUp(self) -> None:
        self.engine = ForeignCollateralEngine()
        self.fixtures_dir = ROOT_DIR / "fixtures"
        self.fixture_path = self.fixtures_dir / "foreign_collateral_deposit.json"

    def test_fixture_loading_and_mixed_basket_valuation(self) -> None:
        """Avalia o lote de garantias mistas do fixture (USD, US Treasuries e Ouro OZ1D)."""
        self.assertTrue(self.fixture_path.exists(), f"Fixture não encontrado em {self.fixture_path}")

        metadata, deposits = self.engine.load_deposit_batch_from_file(self.fixture_path)
        self.assertEqual(len(deposits), 3)
        self.assertEqual(metadata["codigo_participante"], 105)
        self.assertEqual(metadata["codigo_conta_custodia"], 987654)
        self.assertEqual(metadata["margem_requerida_brl"], Decimal("1500000.00"))

        # PTAX USD = 5.6500
        fx_rates = {"USD": Decimal("5.6500")}
        summary = self.engine.evaluate_basket(
            deposits=deposits,
            fx_rates=fx_rates,
            required_margin_brl=metadata["margem_requerida_brl"],
            lote_id=metadata["lote_id"],
            participant_id=metadata["codigo_participante"],
            account_id=metadata["codigo_conta_custodia"],
            valuation_date=date(2026, 11, 3),
        )

        # 1. Item USD Cash
        usd_item = summary.itens[0]
        self.assertEqual(usd_item.tipo_ativo, ForeignAssetType.USD_CASH)
        self.assertEqual(usd_item.valor_bruto_moeda, Decimal("100000.00"))
        self.assertEqual(usd_item.taxa_cambio, Decimal("5.6500"))
        self.assertEqual(usd_item.valor_bruto_brl, Decimal("565000.00"))
        self.assertEqual(usd_item.haircut_total, Decimal("0.1000"))
        self.assertEqual(usd_item.deducao_haircut_brl, Decimal("56500.00"))
        self.assertEqual(usd_item.valor_liquido_brl, Decimal("508500.00"))

        # 2. Item US Treasury Bill
        tbill_item = summary.itens[1]
        self.assertEqual(tbill_item.tipo_ativo, ForeignAssetType.US_TREASURY_BILL)
        self.assertEqual(tbill_item.valor_bruto_moeda, Decimal("197000.00"))  # 2000 * 98.50
        self.assertEqual(tbill_item.taxa_cambio, Decimal("5.6500"))
        self.assertEqual(tbill_item.valor_bruto_brl, Decimal("1113050.00"))  # 197000 * 5.65
        self.assertEqual(tbill_item.haircut_total, Decimal("0.0500"))
        self.assertEqual(tbill_item.deducao_haircut_brl, Decimal("55652.50"))
        self.assertEqual(tbill_item.valor_liquido_brl, Decimal("1057397.50"))

        # 3. Item Ouro OZ1D
        gold_item = summary.itens[2]
        self.assertEqual(gold_item.tipo_ativo, ForeignAssetType.GOLD_OZ1D)
        self.assertEqual(gold_item.valor_bruto_moeda, Decimal("337500.00"))  # 3 * 112500
        self.assertEqual(gold_item.taxa_cambio, Decimal("1.0000"))
        self.assertEqual(gold_item.valor_bruto_brl, Decimal("337500.00"))
        self.assertEqual(gold_item.haircut_total, Decimal("0.1500"))
        self.assertEqual(gold_item.deducao_haircut_brl, Decimal("50625.00"))
        self.assertEqual(gold_item.valor_liquido_brl, Decimal("286875.00"))

        # Invariante Formal: Valor Bruto BRL == Dedução Haircut + Valor Líquido BRL para cada item
        for item in summary.itens:
            self.assertEqual(item.valor_bruto_brl, item.deducao_haircut_brl + item.valor_liquido_brl)

        # Totais da Cesta
        self.assertEqual(summary.total_bruto_brl, Decimal("2015550.00"))
        self.assertEqual(summary.total_haircut_brl, Decimal("162777.50"))
        self.assertEqual(summary.total_liquido_brl, Decimal("1852772.50"))
        self.assertEqual(summary.total_bruto_brl, summary.total_haircut_brl + summary.total_liquido_brl)

        # Cobertura de Margem (1.852.772,50 > 1.500.000,00 -> Margem Livre)
        self.assertEqual(summary.saldo_margem_brl, Decimal("352772.50"))
        self.assertEqual(summary.situacao_margem, "L")
        self.assertTrue(summary.is_covered)
        self.assertFalse(summary.is_margin_call)

    def test_margin_deficit_triggers_situation_d(self) -> None:
        """Verifica se a insuficiência de colateral frente à margem requerida dispara defasagem (D)."""
        _, deposits = self.engine.load_deposit_batch_from_file(self.fixture_path)
        fx_rates = {"USD": Decimal("5.6500")}

        # Exige 2.000.000,00 de margem (colateral líquido é 1.852.772,50)
        summary = self.engine.evaluate_basket(
            deposits=deposits,
            fx_rates=fx_rates,
            required_margin_brl=Decimal("2000000.00"),
        )
        self.assertEqual(summary.saldo_margem_brl, Decimal("-147227.50"))
        self.assertEqual(summary.situacao_margem, "D")
        self.assertFalse(summary.is_covered)
        self.assertTrue(summary.is_margin_call)

    def test_debt_bdr_evaluation(self) -> None:
        """Avalia BDRs de dívida corporativa com haircut de 18%."""
        dep = ForeignCollateralDeposit(
            item_id="BDR-01",
            tipo_ativo=ForeignAssetType.DEBT_BDR,
            ticker="BDRUSDBOND",
            isin="BRBDRBOND001",
            quantidade=Decimal("500"),
            preco_unitario=Decimal("1000.00"),
            moeda="BRL",
            local_custodia="B3_CSD",
        )
        res = self.engine.evaluate_deposit(dep, fx_rates={})
        self.assertEqual(res.valor_bruto_brl, Decimal("500000.00"))
        self.assertEqual(res.haircut_total, Decimal("0.1800"))
        self.assertEqual(res.deducao_haircut_brl, Decimal("90000.00"))
        self.assertEqual(res.valor_liquido_brl, Decimal("410000.00"))

    def test_eur_cash_evaluation(self) -> None:
        """Avalia Euro em espécie com haircut regulatório de 12%."""
        dep = ForeignCollateralDeposit(
            item_id="EUR-01",
            tipo_ativo=ForeignAssetType.EUR_CASH,
            ticker="EUR_CASH",
            isin="EU000000EUR0",
            quantidade=Decimal("50000.00"),
            preco_unitario=Decimal("1.00"),
            moeda="EUR",
            local_custodia="BNP_PARIS",
        )
        res = self.engine.evaluate_deposit(dep, fx_rates={"EUR": Decimal("6.1000")})
        self.assertEqual(res.valor_bruto_brl, Decimal("305000.00"))
        self.assertEqual(res.haircut_total, Decimal("0.1200"))
        self.assertEqual(res.deducao_haircut_brl, Decimal("36600.00"))
        self.assertEqual(res.valor_liquido_brl, Decimal("268400.00"))


class TestForeignCollateralRejectionAndEligibility(unittest.TestCase):
    """Testes de rejeição de ativos não elegíveis, moedas bloqueadas e dados inválidos."""

    def setUp(self) -> None:
        self.engine = ForeignCollateralEngine()

    def test_blocked_currencies_rejection(self) -> None:
        """Rejeita categoricamente depósitos em moedas sancionadas ou sob controle cambial severo."""
        for blocked_curr in ["RUB", "IRR", "KPW", "SYR", "VEF", "CUP", "BYN"]:
            dep = ForeignCollateralDeposit(
                item_id=f"BLK-{blocked_curr}",
                tipo_ativo=ForeignAssetType.USD_CASH,  # tipo válido, mas moeda bloqueada
                ticker=f"CASH_{blocked_curr}",
                isin="US0000000000",
                quantidade=Decimal("1000.00"),
                preco_unitario=Decimal("1.00"),
                moeda=blocked_curr,
                local_custodia="MOSCOW_BANK",
            )
            with self.assertRaises(BlockedCurrencyError) as ctx:
                self.engine.validate_deposit(dep)
            self.assertIn("bloqueada por sanções regulatórias", str(ctx.exception))

    def test_unsupported_currencies_rejection(self) -> None:
        """Rejeita moedas que não constam na lista autorizada da câmara (ex.: GBP, JPY)."""
        for unsupp_curr in ["GBP", "JPY", "CHF", "CNY", "AUD"]:
            dep = ForeignCollateralDeposit(
                item_id=f"UNSUP-{unsupp_curr}",
                tipo_ativo=ForeignAssetType.USD_CASH,
                ticker=f"CASH_{unsupp_curr}",
                isin="US0000000000",
                quantidade=Decimal("1000.00"),
                preco_unitario=Decimal("1.00"),
                moeda=unsupp_curr,
                local_custodia="GLOBAL_BANK",
            )
            with self.assertRaises(UnsupportedCurrencyError) as ctx:
                self.engine.validate_deposit(dep)
            self.assertIn("não autorizada", str(ctx.exception))

    def test_incompatible_asset_and_currency(self) -> None:
        """Rejeita combinação inconsistente entre tipo de ativo e moeda."""
        # Ouro B3 com denominação USD
        dep_gold_usd = ForeignCollateralDeposit(
            item_id="GOLD-USD-ERR",
            tipo_ativo=ForeignAssetType.GOLD_OZ1D,
            ticker="OZ1D",
            isin="BROZ1DMULT01",
            quantidade=Decimal("1"),
            preco_unitario=Decimal("2000.00"),
            moeda="USD",
            local_custodia="B3_VAULT_SP",
        )
        with self.assertRaises(InvalidDepositDataError):
            self.engine.validate_deposit(dep_gold_usd)

        # USD_CASH cotado em BRL
        dep_cash_brl = ForeignCollateralDeposit(
            item_id="USD-BRL-ERR",
            tipo_ativo=ForeignAssetType.USD_CASH,
            ticker="USD_CASH",
            isin="US000000USD0",
            quantidade=Decimal("1000"),
            preco_unitario=Decimal("1.00"),
            moeda="BRL",
            local_custodia="CITIBANK_NY",
        )
        with self.assertRaises(InvalidDepositDataError):
            self.engine.validate_deposit(dep_cash_brl)

    def test_invalid_deposit_numerical_parameters(self) -> None:
        """Rejeita quantidade nula/negativa, preço unitário nulo/negativo ou campos vazios."""
        # Quantidade <= 0
        with self.assertRaises(InvalidDepositDataError):
            ForeignCollateralDeposit(
                item_id="ERR-QTY",
                tipo_ativo=ForeignAssetType.USD_CASH,
                ticker="USD_CASH",
                isin="US000000USD0",
                quantidade=Decimal("0.00"),
                preco_unitario=Decimal("1.00"),
                moeda="USD",
                local_custodia="CITIBANK_NY",
            )

        # Preço unitário <= 0
        with self.assertRaises(InvalidDepositDataError):
            ForeignCollateralDeposit(
                item_id="ERR-PRICE",
                tipo_ativo=ForeignAssetType.USD_CASH,
                ticker="USD_CASH",
                isin="US000000USD0",
                quantidade=Decimal("100.00"),
                preco_unitario=Decimal("-1.00"),
                moeda="USD",
                local_custodia="CITIBANK_NY",
            )

        # Ticker vazio
        with self.assertRaises(InvalidDepositDataError):
            ForeignCollateralDeposit(
                item_id="ERR-TICKER",
                tipo_ativo=ForeignAssetType.USD_CASH,
                ticker="",
                isin="US000000USD0",
                quantidade=Decimal("100.00"),
                preco_unitario=Decimal("1.00"),
                moeda="USD",
                local_custodia="CITIBANK_NY",
            )

        # ISIN vazio
        with self.assertRaises(InvalidDepositDataError):
            ForeignCollateralDeposit(
                item_id="ERR-ISIN",
                tipo_ativo=ForeignAssetType.USD_CASH,
                ticker="USD_CASH",
                isin="   ",
                quantidade=Decimal("100.00"),
                preco_unitario=Decimal("1.00"),
                moeda="USD",
                local_custodia="CITIBANK_NY",
            )

    def test_haircut_configuration_bounds(self) -> None:
        """Verifica restrições de limite para componentes de haircut [0.00, 1.00]."""
        with self.assertRaises(HaircutConfigurationError):
            CollateralHaircut(
                tipo_ativo=ForeignAssetType.USD_CASH,
                fx_haircut=Decimal("-0.05"),
            )

        with self.assertRaises(HaircutConfigurationError):
            CollateralHaircut(
                tipo_ativo=ForeignAssetType.USD_CASH,
                total_haircut=Decimal("1.25"),
            )

    def test_missing_or_negative_fx_rate(self) -> None:
        """Gera erro se a taxa cambial necessária para conversão for omitida ou negativa."""
        dep = ForeignCollateralDeposit(
            item_id="DEP-NO-FX",
            tipo_ativo=ForeignAssetType.USD_CASH,
            ticker="USD_CASH",
            isin="US000000USD0",
            quantidade=Decimal("1000.00"),
            preco_unitario=Decimal("1.00"),
            moeda="USD",
            local_custodia="CITIBANK_NY",
        )
        # Sem fornecer taxa para USD
        with self.assertRaises(InvalidDepositDataError):
            self.engine.evaluate_deposit(dep, fx_rates={})

        # Com taxa negativa
        with self.assertRaises(InvalidDepositDataError):
            self.engine.evaluate_deposit(dep, fx_rates={"USD": Decimal("-5.5000")})


class TestForeignCollateralFXVariationAndIntraday(unittest.TestCase):
    """Testes de variação cambial intradiária (alta e queda de PTAX) e sensibilidade de margem."""

    def setUp(self) -> None:
        self.engine = ForeignCollateralEngine()
        self.fixtures_dir = ROOT_DIR / "fixtures"
        self.fixture_path = self.fixtures_dir / "foreign_collateral_deposit.json"
        _, self.deposits = self.engine.load_deposit_batch_from_file(self.fixture_path)
        self.required_margin = Decimal("1700000.00")

    def test_ptax_increase_expands_free_margin(self) -> None:
        """Aumento da PTAX (desvalorização do BRL) valoriza colateral internacional e eleva margem livre."""
        # PTAX Base = 5.0000
        summary_base = self.engine.evaluate_basket(
            deposits=self.deposits,
            fx_rates={"USD": Decimal("5.0000")},
            required_margin_brl=self.required_margin,
        )

        # PTAX Sobe para 5.8000
        summary_higher_fx = self.engine.revalue_intraday(
            original_deposits=self.deposits,
            updated_fx_rates={"USD": Decimal("5.8000")},
            required_margin_brl=self.required_margin,
        )

        self.assertGreater(summary_higher_fx.total_liquido_brl, summary_base.total_liquido_brl)
        self.assertGreater(summary_higher_fx.saldo_margem_brl, summary_base.saldo_margem_brl)
        # O valor do ouro em BRL deve se manter idêntico entre as rodadas
        gold_base = next(i for i in summary_base.itens if i.tipo_ativo == ForeignAssetType.GOLD_OZ1D)
        gold_higher = next(i for i in summary_higher_fx.itens if i.tipo_ativo == ForeignAssetType.GOLD_OZ1D)
        self.assertEqual(gold_base.valor_liquido_brl, gold_higher.valor_liquido_brl)

    def test_ptax_drop_triggers_margin_call(self) -> None:
        """Queda acentuada da PTAX deprecia garantias em USD e gera chamada de margem (D)."""
        # Com PTAX = 5.5000:
        # USD Cash net = 100.000 * 5.50 * 0.90 = 495.000,00
        # T-Bill net = 197.000 * 5.50 * 0.95 = 1.029.325,00
        # Ouro net = 286.875,00
        # Total = 1.811.200,00 > 1.700.000,00 -> Situação "L"
        summary_good = self.engine.evaluate_basket(
            deposits=self.deposits,
            fx_rates={"USD": Decimal("5.5000")},
            required_margin_brl=self.required_margin,
        )
        self.assertEqual(summary_good.situacao_margem, "L")
        self.assertTrue(summary_good.is_covered)

        # PTAX despenca para 4.5000:
        # USD Cash net = 100.000 * 4.50 * 0.90 = 405.000,00
        # T-Bill net = 197.000 * 4.50 * 0.95 = 842.175,00
        # Ouro net = 286.875,00
        # Total = 1.534.050,00 < 1.700.000,00 -> Situação "D" (Defasagem de 165.950,00)
        summary_bad = self.engine.revalue_intraday(
            original_deposits=self.deposits,
            updated_fx_rates={"USD": Decimal("4.5000")},
            required_margin_brl=self.required_margin,
        )
        self.assertEqual(summary_bad.situacao_margem, "D")
        self.assertFalse(summary_bad.is_covered)
        self.assertTrue(summary_bad.is_margin_call)
        self.assertEqual(summary_bad.saldo_margem_brl, Decimal("-165950.00"))

    def test_intraday_price_revaluation(self) -> None:
        """Marcação a mercado de preços de T-Bills e Ouro intradiários."""
        updated_prices = {
            "TBILL_2027": Decimal("99.00"),     # T-Bill sobe de 98.50 para 99.00
            "OZ1D": Decimal("115000.00"),       # Ouro sobe de 112.500 para 115.000
        }
        revalued = self.engine.revalue_intraday(
            original_deposits=self.deposits,
            updated_fx_rates={"USD": Decimal("5.6500")},
            required_margin_brl=self.required_margin,
            updated_prices=updated_prices,
        )
        # O item T-Bill deve refletir o preço de 99.00
        tbill_item = next(i for i in revalued.itens if i.ticker == "TBILL_2027")
        self.assertEqual(tbill_item.preco_unitario, Decimal("99.00"))
        # O item OZ1D deve refletir o preço de 115000.00
        gold_item = next(i for i in revalued.itens if i.ticker == "OZ1D")
        self.assertEqual(gold_item.preco_unitario, Decimal("115000.00"))


class TestForeignAssetTypeAndHaircutProperties(unittest.TestCase):
    """Testes dos métodos utilitários do Enum e decomposição do Haircut."""

    def test_foreign_asset_type_properties(self) -> None:
        """Valida properties de classificação de tipo de ativo."""
        self.assertTrue(ForeignAssetType.USD_CASH.is_cash)
        self.assertTrue(ForeignAssetType.EUR_CASH.is_cash)
        self.assertFalse(ForeignAssetType.US_TREASURY_BILL.is_cash)

        self.assertTrue(ForeignAssetType.US_TREASURY_BILL.is_us_treasury)
        self.assertTrue(ForeignAssetType.US_TREASURY_NOTE.is_us_treasury)
        self.assertTrue(ForeignAssetType.US_TREASURY_BOND.is_us_treasury)
        self.assertFalse(ForeignAssetType.GOLD_OZ1D.is_us_treasury)

        self.assertTrue(ForeignAssetType.GOLD_OZ1D.is_gold)
        self.assertTrue(ForeignAssetType.GOLD_OZ2D.is_gold)
        self.assertTrue(ForeignAssetType.GOLD_OZ3D.is_gold)
        self.assertFalse(ForeignAssetType.DEBT_BDR.is_gold)

        self.assertEqual(ForeignAssetType.USD_CASH.default_currency, "USD")
        self.assertEqual(ForeignAssetType.EUR_CASH.default_currency, "EUR")
        self.assertEqual(ForeignAssetType.GOLD_OZ1D.default_currency, "BRL")
        self.assertEqual(ForeignAssetType.DEBT_BDR.default_currency, "BRL")

    def test_haircut_automatic_sum(self) -> None:
        """Valida que o haircut total é calculado automaticamente a partir dos componentes."""
        h = CollateralHaircut(
            tipo_ativo=ForeignAssetType.US_TREASURY_NOTE,
            fx_haircut=Decimal("0.0200"),
            volatility_haircut=Decimal("0.0500"),
            liquidity_haircut=Decimal("0.0100"),
        )
        self.assertEqual(h.total_haircut, Decimal("0.0800"))

    def test_custom_haircut_registration(self) -> None:
        """Permite cadastrar ou sobrescrever regra de haircut no motor."""
        engine = ForeignCollateralEngine()
        custom_rule = CollateralHaircut(
            tipo_ativo=ForeignAssetType.USD_CASH,
            fx_haircut=Decimal("0.0800"),
            volatility_haircut=Decimal("0.0000"),
            liquidity_haircut=Decimal("0.0000"),
            total_haircut=Decimal("0.0800"),
            descricao="Regra especial para participante formador de mercado",
        )
        engine.register_haircut_rule(custom_rule)
        retrieved = engine.get_haircut_rule(ForeignAssetType.USD_CASH)
        self.assertEqual(retrieved.total_haircut, Decimal("0.0800"))


if __name__ == "__main__":
    unittest.main()
