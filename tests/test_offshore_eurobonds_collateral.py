"""
Testes unitários e de invariantes do Módulo de Ativos Offshore e Eurobonds como Colateral Multilateral B3 (FL-083).

Valida conformidade estrita com:
- Regras de aceitação de garantias internacionais da Câmara B3 (sistema CORE / NoMe).
- Conversão cambial prudencial via PTAX de Compra (bid) com margem de segurança.
- Segregação de custódia internacional (DTC, Euroclear, Clearstream).
- Não-negatividade e limitação superior do valor de colateral elegível.
- Trava de segurança anti-déficit na desoneração (Safe-Release Guardrail).
"""

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.offshore_eurobonds_collateral import (
    AccountSegregationType,
    CreditRating,
    EvaluatedOffshoreCollateral,
    FxSafetyMarginConfig,
    HaircutConfigurationError,
    IneligibleAssetError,
    IneligibleRatingError,
    InternationalCustodyAccount,
    InternationalCustodySegregationError,
    InternationalDepository,
    InvalidDepositError,
    MarginReleaseViolationError,
    OffshoreAssetType,
    OffshoreCollateralError,
    OffshoreCurrency,
    OffshoreDepositPosition,
    OffshoreEurobondsCollateralEngine,
    OffshoreHaircutBreakdown,
    OffshoreInvariantViolationError,
    PledgeStatus,
    PtaxRate,
    TenorBucket,
    UnsupportedCurrencyError,
)


class TestOffshoreEurobondsCollateral(unittest.TestCase):
    """Bateria de testes unitários do motor de garantias offshore e Eurobonds B3."""

    def setUp(self) -> None:
        self.fixture_path = (
            Path(__file__).resolve().parent.parent / "fixtures" / "offshore_eurobonds_collateral.json"
        )
        self.engine = OffshoreEurobondsCollateralEngine.load_from_fixture(self.fixture_path)
        self.ref_date = date(2026, 11, 4)

    def test_fixture_loading_and_invariants(self) -> None:
        """Garante que a fixture carregue com sucesso e todas as invariantes formais passem."""
        self.assertEqual(len(self.engine.ptax_rates), 2)
        self.assertEqual(len(self.engine.securities), 5)
        self.assertEqual(len(self.engine.accounts), 4)
        self.assertEqual(len(self.engine.positions), 5)

        report = self.engine.verify_invariants(self.ref_date)
        for inv_name, holds in report.items():
            self.assertTrue(holds, f"Invariante formal violada: {inv_name}")

    def test_to_dict_and_from_dict_serialization(self) -> None:
        """Testa idempotência de serialização e desserialização."""
        data = self.engine.to_dict()
        self.assertEqual(data["schema_version"], "1.0.0")
        self.assertEqual(data["flow_id"], "FL-083")

        reconstructed = OffshoreEurobondsCollateralEngine.from_dict(data)
        self.assertEqual(len(reconstructed.securities), len(self.engine.securities))
        self.assertEqual(len(reconstructed.positions), len(self.engine.positions))

        rep = reconstructed.verify_invariants(self.ref_date)
        for inv_name, holds in rep.items():
            self.assertTrue(holds, f"Invariante pós-serialização violada: {inv_name}")

    def test_ptax_conversion_and_conservative_bid_rate(self) -> None:
        """Valida que a conversão em BRL utiliza rigorosamente a taxa PTAX de Compra (bid)."""
        eval_pbr = self.engine.evaluate_position("POS-PBR-001", self.ref_date)

        # PBR: 100,000 unidades a 14.50 USD = 1,450,000.00 USD
        expected_foreign = Decimal("1450000.00")
        self.assertEqual(eval_pbr.gross_foreign_value, expected_foreign)

        # PTAX USD bid: 5.7500 -> Bruto BRL = 1,450,000 * 5.75 = 8,337,500.00 BRL
        expected_gross_brl = Decimal("8337500.00")
        self.assertEqual(eval_pbr.gross_brl_value, expected_gross_brl)
        self.assertEqual(eval_pbr.ptax_bid_rate, Decimal("5.7500"))

    def test_haircut_breakdown_adr_and_eurobond(self) -> None:
        """Valida cálculo analítico de haircut para ADR de ações vs Eurobond de renda fixa."""
        # ADR PBR (BB, BR_OFFSHORE, USD):
        # country: 0.0400, rating: 0.1500, equity: 0.2000, fx: 0.0750 -> soma = 0.4650
        eval_pbr = self.engine.evaluate_position("POS-PBR-001", self.ref_date)
        self.assertEqual(eval_pbr.haircuts.country_risk_haircut, Decimal("0.0400"))
        self.assertEqual(eval_pbr.haircuts.credit_rating_haircut, Decimal("0.1500"))
        self.assertEqual(eval_pbr.haircuts.tenor_or_equity_haircut, Decimal("0.2000"))
        self.assertEqual(eval_pbr.haircuts.fx_volatility_haircut, Decimal("0.0750"))
        self.assertEqual(eval_pbr.haircuts.total_haircut, Decimal("0.4650"))

        # Elegível = 8,337,500.00 * (1 - 0.4650) = 8,337,500.00 * 0.5350 = 4,460,562.50 BRL
        self.assertEqual(eval_pbr.eligible_collateral_value_brl, Decimal("4460562.50"))

        # Eurobond VTCIM-2028 em EUR (BBB, BR_OFFSHORE, EUR):
        # maturity: 2028-07-13 (em 2026-11-04, ~1.69 anos -> tenor 1Y-3Y -> haircut 0.0400)
        # rating BBB: 0.0800, country: 0.0400, fx EUR: 0.0900 -> total = 0.2500
        eval_vtc = self.engine.evaluate_position("POS-VTC-004", self.ref_date)
        self.assertEqual(eval_vtc.haircuts.country_risk_haircut, Decimal("0.0400"))
        self.assertEqual(eval_vtc.haircuts.credit_rating_haircut, Decimal("0.0800"))
        self.assertEqual(eval_vtc.haircuts.tenor_or_equity_haircut, Decimal("0.0400"))
        self.assertEqual(eval_vtc.haircuts.fx_volatility_haircut, Decimal("0.0900"))
        self.assertEqual(eval_vtc.haircuts.total_haircut, Decimal("0.2500"))

        # 4,000 * 98.75 EUR = 395,000.00 EUR * 6.25 = 2,468,750.00 BRL
        # Elegível = 2,468,750.00 * 0.75 = 1,851,562.50 BRL
        self.assertEqual(eval_vtc.gross_brl_value, Decimal("2468750.00"))
        self.assertEqual(eval_vtc.eligible_collateral_value_brl, Decimal("1851562.50"))

    def test_invariant_collateral_non_negativity_and_upper_bound(self) -> None:
        """Invariante 1: 0 <= Valor_Elegivel <= Valor_Bruto em todos os cenários."""
        for pos_id in self.engine.positions:
            eval_item = self.engine.evaluate_position(pos_id, self.ref_date)
            self.assertGreaterEqual(eval_item.eligible_collateral_value_brl, Decimal("0.00"))
            self.assertLessEqual(eval_item.eligible_collateral_value_brl, eval_item.gross_brl_value)
            self.assertGreaterEqual(eval_item.net_margin_credit_brl, Decimal("0.00"))

    def test_invariant_international_custody_segregation_balance(self) -> None:
        """Invariante 2: Conservação de saldo Q_total == Q_bloqueado + Q_livre."""
        for pos in self.engine.positions.values():
            self.assertEqual(pos.total_quantity, pos.pledged_quantity + pos.unencumbered_quantity)

        # Teste de bloqueio e desoneração local
        pos = self.engine.positions["POS-VALE-002"]
        orig_pledged = pos.pledged_quantity
        orig_free = pos.unencumbered_quantity

        # Bloqueia mais 10,000
        pos.pledge_more(Decimal("10000.00"))
        self.assertEqual(pos.pledged_quantity, orig_pledged + Decimal("10000.00"))
        self.assertEqual(pos.unencumbered_quantity, orig_free - Decimal("10000.00"))
        self.assertEqual(pos.total_quantity, pos.pledged_quantity + pos.unencumbered_quantity)

        # Desonera 5,000
        pos.unpledge(Decimal("5000.00"))
        self.assertEqual(pos.pledged_quantity, orig_pledged + Decimal("5000.00"))
        self.assertEqual(pos.unencumbered_quantity, orig_free - Decimal("5000.00"))
        self.assertEqual(pos.total_quantity, pos.pledged_quantity + pos.unencumbered_quantity)

    def test_custody_mismatch_depository_rejection(self) -> None:
        """Garante rejeição se a conta do participante estiver em depositária diferente do ativo."""
        # ACC-ITAU-DTC-HOUSE está na DTC. Tentar depositar Eurobond registrado em EUROCLEAR deve falhar.
        with self.assertRaises(InternationalCustodySegregationError):
            self.engine.deposit_collateral(
                position_id="POS-INVALID-DEP",
                account_id="ACC-ITAU-DTC-HOUSE",  # DTC
                isin="US71647NAQ25",              # PGF-2030 (EUROCLEAR)
                quantity=Decimal("1000"),
            )

    def test_safe_release_guardrail_prevents_margin_deficit(self) -> None:
        """Invariante 5: Desoneração é vetada se colateral residual for menor que a margem requerida."""
        participant_id = "PART-ITAU-01"
        # Margem requerida alta: R$ 4,000,000.00
        # Posição POS-PBR-001 tem crédito de margem de R$ 4,460,562.50
        # Tentar desonerar 50% das ações (liberando ~R$ 2.23M de garantia) deixaria apenas ~R$ 2.23M, gerando déficit!
        with self.assertRaises(MarginReleaseViolationError):
            self.engine.unpledge_collateral(
                position_id="POS-PBR-001",
                quantity=Decimal("50000"),  # 50,000 de 100,000
                required_clearing_margin_brl=Decimal("4000000.00"),
                as_of_date=self.ref_date,
            )

        # Desoneração autorizada se o crédito residual for suficiente
        # Margem requerida baixa: R$ 2,000,000.00
        # Desonerar 10,000 ações libera R$ 446,056.25, restando ~R$ 4.01M > R$ 2.0M
        pos_after = self.engine.unpledge_collateral(
            position_id="POS-PBR-001",
            quantity=Decimal("10000"),
            required_clearing_margin_brl=Decimal("2000000.00"),
            as_of_date=self.ref_date,
        )
        self.assertEqual(pos_after.pledged_quantity, Decimal("90000.0000"))
        self.assertEqual(pos_after.unencumbered_quantity, Decimal("10000.0000"))

    def test_ineligible_credit_rating_rejection(self) -> None:
        """Garante rejeição cadastral de títulos com rating especulativo severo (CCC ou D)."""
        from src.offshore_eurobonds_collateral import OffshoreSecurity

        with self.assertRaises(IneligibleRatingError):
            OffshoreSecurity(
                isin="US9999999999",
                ticker="DISTRESS-BOND",
                name="EMPRESA OFFSHORE INSOLVENTE",
                asset_type=OffshoreAssetType.EUROBOND,
                brazilian_issuer_root="DEFAULT_GRP",
                offshore_issuing_entity="Distressed Finance Ltd",
                currency=OffshoreCurrency.USD,
                depository=InternationalDepository.EUROCLEAR,
                credit_rating=CreditRating.CCC,  # Inelegível B3 (abaixo de BB-)
                country_risk_zone="BR_OFFSHORE",
                maturity_date=date(2028, 1, 1),
                unit_price=Decimal("40.00"),
            )

    def test_ptax_bid_ask_validation(self) -> None:
        """Garante validação estrita da cotação PTAX (bid <= ask e bid > 0)."""
        with self.assertRaises(UnsupportedCurrencyError):
            PtaxRate(
                currency=OffshoreCurrency.USD,
                bid_rate=Decimal("5.80"),
                ask_rate=Decimal("5.70"),  # bid > ask!
                mid_rate=Decimal("5.75"),
                bulletin_date=self.ref_date,
            )

        with self.assertRaises(UnsupportedCurrencyError):
            PtaxRate(
                currency=OffshoreCurrency.USD,
                bid_rate=Decimal("-1.00"),  # Negativo!
                ask_rate=Decimal("5.70"),
                mid_rate=Decimal("2.35"),
                bulletin_date=self.ref_date,
            )

    def test_default_liquidation_execution(self) -> None:
        """Testa excussão forçada de colateral offshore em caso de default multilateral."""
        res = self.engine.execute_default_liquidation(
            position_id="POS-PGF-003",
            liquidation_price_foreign=Decimal("95.00"),
            as_of_date=self.ref_date,
        )
        self.assertEqual(res["status"], "EXECUTED_DEFAULT")
        self.assertEqual(res["executed_quantity"], "5000.0000")
        # 5,000 * 95.00 USD = 475,000.00 USD * 5.7500 = 2,731,250.00 BRL
        self.assertEqual(res["gross_foreign_proceeds"], "475000.00")
        self.assertEqual(res["gross_brl_proceeds"], "2731250.00")

        pos = self.engine.positions["POS-PGF-003"]
        self.assertEqual(pos.pledged_quantity, Decimal("0.0"))
        self.assertEqual(pos.status, PledgeStatus.EXECUTED_DEFAULT)

    def test_participant_portfolio_aggregation(self) -> None:
        """Testa agregação do portfólio de garantias offshore de um participante."""
        # PART-BRAD-02 possui 3 posições: VALE (ADR/GDR), PGF-2030 (Eurobond USD), VTCIM-2028 (Eurobond EUR)
        portfolio = self.engine.evaluate_participant_portfolio("PART-BRAD-02", self.ref_date)
        self.assertEqual(portfolio["items_count"], 3)
        self.assertGreater(portfolio["total_gross_brl"], Decimal("0.00"))
        self.assertGreater(portfolio["total_eligible_brl"], Decimal("0.00"))
        self.assertLessEqual(portfolio["total_eligible_brl"], portfolio["total_gross_brl"])


if __name__ == "__main__":
    unittest.main()
