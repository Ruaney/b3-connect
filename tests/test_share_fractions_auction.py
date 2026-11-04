"""Testes unitários para o fluxo de Tratamento de Frações de Ações e Leilão de Sobras (FL-052)."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
import unittest

# Garante que a raiz do repositório esteja no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.share_fractions_auction import (
    AuctionValidationError,
    CustodyAccountError,
    FinancialConservationError,
    FractionsAuctionOutcome,
    FractionsPoolSummary,
    GroupingEvent,
    GroupingEventType,
    GroupingValidationError,
    ShareFractionHolder,
    ShareFractionsAuctionEngine,
)


class TestShareFractionsAuction(unittest.TestCase):
    """Bateria de testes para apuração de frações pós-grupamento e leilão de sobras."""

    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_path = self.fixtures_dir / "share_fractions_auction.json"

    def test_fixture_loading_and_execution(self) -> None:
        """Valida a execução ponta a ponta a partir da fixture oficial do cenário 10:1."""
        engine, event, holders, auction = ShareFractionsAuctionEngine.from_json_fixture(self.fixture_path)

        self.assertEqual(event.event_id, "EVT-GRUP-2026-MGLU3")
        self.assertEqual(event.ticker, "MGLU3")
        self.assertEqual(event.ratio_from, 10)
        self.assertEqual(event.ratio_to, 1)
        self.assertEqual(event.ratio_str, "10:1")
        self.assertEqual(event.grouping_factor, Decimal("0.1"))
        self.assertEqual(len(holders), 5)

        # 1. Registro e apuração de custódia
        engine.register_event(event)
        summary = engine.process_custody_accounts(event.event_id, holders)

        self.assertEqual(summary.total_original_shares, 940)
        self.assertEqual(summary.total_whole_shares_allocated, 91)
        self.assertEqual(summary.total_fractional_shares, Decimal("3.0"))
        self.assertEqual(summary.auction_whole_shares, 3)
        self.assertEqual(summary.residual_unauctioned_fraction, Decimal("0.0"))
        self.assertEqual(summary.holders_with_fractions, 5)

        # Verificar segregação individual
        expected_holdings = [
            ("1001-1", 13, Decimal("0.7")),
            ("1002-2", 25, Decimal("0.4")),
            ("2003-3", 8, Decimal("0.8")),
            ("2004-4", 31, Decimal("0.6")),
            ("3005-5", 14, Decimal("0.5")),
        ]
        for h, (exp_acc, exp_whole, exp_frac) in zip(holders, expected_holdings):
            self.assertEqual(h.investor_account, exp_acc)
            self.assertEqual(h.whole_shares, exp_whole)
            self.assertEqual(h.fraction_share, exp_frac)

        # 2. Registro do leilão
        engine.register_auction_outcome(auction)
        self.assertEqual(auction.shares_auctioned, 3)
        self.assertEqual(auction.gross_total_amount, Decimal("135.00"))
        self.assertEqual(auction.total_fees, Decimal("0.20"))
        self.assertEqual(auction.net_total_amount, Decimal("134.80"))

        # 3. Rateio financeiro pro-rata
        distributed = engine.calculate_pro_rata_distribution(event.event_id)
        self.assertEqual(len(distributed), 5)

        expected_finances = [
            ("1001-1", Decimal("31.50"), Decimal("0.05"), Decimal("31.45")),
            ("1002-2", Decimal("18.00"), Decimal("0.03"), Decimal("17.97")),
            ("2003-3", Decimal("36.00"), Decimal("0.05"), Decimal("35.95")),
            ("2004-4", Decimal("27.00"), Decimal("0.04"), Decimal("26.96")),
            ("3005-5", Decimal("22.50"), Decimal("0.03"), Decimal("22.47")),
        ]
        for h, (exp_acc, exp_gross, exp_fee, exp_net) in zip(distributed, expected_finances):
            self.assertEqual(h.investor_account, exp_acc)
            self.assertEqual(h.cash_credit_gross, exp_gross)
            self.assertEqual(h.fee_deduction, exp_fee)
            self.assertEqual(h.cash_credit_net, exp_net)
            self.assertEqual(h.cash_credit_net, h.cash_credit_gross - h.fee_deduction)

        # 4. Invariantes formais
        invariants = engine.validate_conservation_invariants(event.event_id)
        self.assertTrue(invariants["share_conservation_satisfied"])
        self.assertTrue(invariants["pool_conservation_satisfied"])
        self.assertTrue(invariants["gross_cash_conservation_satisfied"])
        self.assertTrue(invariants["fee_conservation_satisfied"])
        self.assertTrue(invariants["net_cash_conservation_satisfied"])
        self.assertTrue(invariants["row_conservation_satisfied"])
        self.assertTrue(invariants["all_invariants_satisfied"])
        self.assertEqual(invariants["metrics"]["distributed_net_credit"], "134.80")

    def test_run_full_cycle_method(self) -> None:
        """Valida execução simplificada por run_full_cycle e exportação de extrato."""
        engine, event, holders, auction = ShareFractionsAuctionEngine.from_json_fixture(self.fixture_path)
        report = engine.run_full_cycle(event, holders, auction)

        self.assertTrue(report["all_invariants_satisfied"])
        statements = engine.export_liquidation_statement(event.event_id)
        self.assertEqual(len(statements), 5)
        self.assertEqual(statements[0]["investor_account"], "1001-1")
        self.assertEqual(statements[0]["cash_credit_net"], "31.45")

    def test_custom_ratio_five_to_two(self) -> None:
        """Valida grupamento não unitário 5:2 (para cada 5 ações antigas, 2 novas)."""
        engine = ShareFractionsAuctionEngine()
        event = GroupingEvent(
            event_id="EVT-5TO2",
            ticker="B3SA3",
            isin="BRB3SAACNOR6",
            company_name="B3 S.A.",
            ratio_from=5,
            ratio_to=2,
            approval_date=date(2026, 11, 1),
            cutoff_date=date(2026, 11, 2),
        )
        self.assertEqual(event.grouping_factor, Decimal("0.4"))

        # Titulares:
        # H1: 12 ações -> 12 * 2 / 5 = 4.8 -> 4 inteiras, 0.8 fração
        # H2: 13 ações -> 13 * 2 / 5 = 5.2 -> 5 inteiras, 0.2 fração
        # Total frações = 0.8 + 0.2 = 1.0 -> 1 lote inteiro
        holders = [
            ShareFractionHolder("ACC-1", "0008", "INVESTIDOR A", "111", 12),
            ShareFractionHolder("ACC-2", "0072", "INVESTIDOR B", "222", 13),
        ]
        engine.register_event(event)
        summary = engine.process_custody_accounts(event.event_id, holders)

        self.assertEqual(summary.total_original_shares, 25)
        self.assertEqual(summary.total_whole_shares_allocated, 9)
        self.assertEqual(summary.total_fractional_shares, Decimal("1.0"))
        self.assertEqual(summary.auction_whole_shares, 1)

        auction = FractionsAuctionOutcome(
            auction_id="AUC-5TO2",
            event_id=event.event_id,
            ticker="B3SA3",
            shares_auctioned=1,
            gross_price_per_share=Decimal("50.00"),
            gross_total_amount=Decimal("50.00"),
            total_fees=Decimal("1.00"),
            net_total_amount=Decimal("49.00"),
        )
        engine.register_auction_outcome(auction)
        distributed = engine.calculate_pro_rata_distribution(event.event_id)

        # Proporção 0.8 / 1.0 (80%) e 0.2 / 1.0 (20%)
        # H1: 40.00 bruto, 0.80 taxas, 39.20 líquido
        # H2: 10.00 bruto, 0.20 taxas, 9.80 líquido
        h1 = distributed[0]
        h2 = distributed[1]
        self.assertEqual(h1.cash_credit_gross, Decimal("40.00"))
        self.assertEqual(h1.fee_deduction, Decimal("0.80"))
        self.assertEqual(h1.cash_credit_net, Decimal("39.20"))

        self.assertEqual(h2.cash_credit_gross, Decimal("10.00"))
        self.assertEqual(h2.fee_deduction, Decimal("0.20"))
        self.assertEqual(h2.cash_credit_net, Decimal("9.80"))

        invariants = engine.validate_conservation_invariants(event.event_id)
        self.assertTrue(invariants["all_invariants_satisfied"])

    def test_exact_multiples_no_fractions(self) -> None:
        """Valida que acionistas com múltiplos exatos não geram frações e recebem R$ 0,00 no leilão."""
        engine = ShareFractionsAuctionEngine()
        event = GroupingEvent(
            event_id="EVT-EXACT",
            ticker="VALE3",
            isin="BRVALEACNOR0",
            company_name="VALE S.A.",
            ratio_from=10,
            ratio_to=1,
        )
        holders = [
            ShareFractionHolder("ACC-1", "0008", "ALFA", "111", 500),
            ShareFractionHolder("ACC-2", "0008", "BETA", "222", 1000),
        ]
        engine.register_event(event)
        summary = engine.process_custody_accounts(event.event_id, holders)

        self.assertEqual(summary.total_original_shares, 1500)
        self.assertEqual(summary.total_whole_shares_allocated, 150)
        self.assertEqual(summary.total_fractional_shares, Decimal("0.0"))
        self.assertEqual(summary.auction_whole_shares, 0)
        self.assertEqual(summary.holders_with_fractions, 0)

        # Sem sobras, rateio não distribui créditos
        distributed = engine.calculate_pro_rata_distribution(event.event_id)
        for h in distributed:
            self.assertEqual(h.cash_credit_net, Decimal("0.00"))

    def test_zero_fees_auction(self) -> None:
        """Valida leilão com isenção de taxas (taxas = 0), onde Net == Gross perfeitamente."""
        engine = ShareFractionsAuctionEngine()
        event = GroupingEvent(
            event_id="EVT-NO-FEE",
            ticker="PETR4",
            isin="BRPETRACNPR6",
            company_name="PETROLEO BRASILEIRO S.A.",
            ratio_from=10,
            ratio_to=1,
        )
        holders = [
            ShareFractionHolder("ACC-1", "0008", "A", "1", 15),  # 1 inteira, 0.5 fração
            ShareFractionHolder("ACC-2", "0008", "B", "2", 25),  # 2 inteiras, 0.5 fração
        ]
        engine.register_event(event)
        engine.process_custody_accounts(event.event_id, holders)

        auction = FractionsAuctionOutcome(
            auction_id="AUC-NO-FEE",
            event_id=event.event_id,
            ticker="PETR4",
            shares_auctioned=1,
            gross_price_per_share=Decimal("38.50"),
            gross_total_amount=Decimal("38.50"),
            total_fees=Decimal("0.00"),
            net_total_amount=Decimal("38.50"),
        )
        engine.register_auction_outcome(auction)
        distributed = engine.calculate_pro_rata_distribution(event.event_id)

        # 38.50 / 2 = 19.25 cada
        self.assertEqual(distributed[0].cash_credit_net, Decimal("19.25"))
        self.assertEqual(distributed[1].cash_credit_net, Decimal("19.25"))
        self.assertEqual(distributed[0].fee_deduction, Decimal("0.00"))
        self.assertEqual(distributed[1].fee_deduction, Decimal("0.00"))

        invariants = engine.validate_conservation_invariants(event.event_id)
        self.assertTrue(invariants["all_invariants_satisfied"])

    def test_largest_remainder_tie_break(self) -> None:
        """Valida distribuição de 1 centavo indivisível entre frações perfeitamente simétricas."""
        engine = ShareFractionsAuctionEngine()
        event = GroupingEvent(
            event_id="EVT-TIE",
            ticker="ITUB4",
            isin="BRITUBACNPR1",
            company_name="ITAU UNIBANCO HOLDING S.A.",
            ratio_from=10,
            ratio_to=1,
        )
        # 3 titulares idênticos com 0.33333333... fração cada (ex: soma = 1 lote)
        # Para forçar resíduo: R$ 10.00 dividido por 3 = 3.3333... cada
        # Base: 3.33 cada (total 9.99). Falta 1 centavo. O primeiro deve receber 3.34.
        holders = [
            ShareFractionHolder("ACC-A", "0008", "A", "1", 3),
            ShareFractionHolder("ACC-B", "0008", "B", "2", 3),
            ShareFractionHolder("ACC-C", "0008", "C", "3", 4),
        ]
        engine.register_event(event)
        # 3/10 + 3/10 + 4/10 = 10/10 = 1.0
        engine.process_custody_accounts(event.event_id, holders)

        auction = FractionsAuctionOutcome(
            auction_id="AUC-TIE",
            event_id=event.event_id,
            ticker="ITUB4",
            shares_auctioned=1,
            gross_price_per_share=Decimal("10.00"),
            gross_total_amount=Decimal("10.00"),
            total_fees=Decimal("0.00"),
            net_total_amount=Decimal("10.00"),
        )
        engine.register_auction_outcome(auction)
        distributed = engine.calculate_pro_rata_distribution(event.event_id)

        # Pesos: C tem 0.4 (4.00), A tem 0.3 (3.00), B tem 0.3 (3.00)
        # 10.00 * 0.4 = 4.00, 10.00 * 0.3 = 3.00, 10.00 * 0.3 = 3.00 -> exato!
        self.assertEqual(distributed[0].cash_credit_net, Decimal("3.00"))
        self.assertEqual(distributed[1].cash_credit_net, Decimal("3.00"))
        self.assertEqual(distributed[2].cash_credit_net, Decimal("4.00"))

        sum_net = sum(h.cash_credit_net for h in distributed)
        self.assertEqual(sum_net, Decimal("10.00"))

    def test_validation_errors(self) -> None:
        """Valida que entradas inconsistentes disparam exceções tipadas de validação."""
        # Fator inválido
        with self.assertRaises(GroupingValidationError):
            GroupingEvent("E1", "T", "ISIN", "C", ratio_from=0, ratio_to=1)

        with self.assertRaises(GroupingValidationError):
            GroupingEvent("E2", "T", "ISIN", "C", ratio_from=10, ratio_to=0)

        # Saldo negativo
        with self.assertRaises(CustodyAccountError):
            ShareFractionHolder("ACC", "0008", "N", "1", original_shares=-5)

        engine = ShareFractionsAuctionEngine()
        # Evento não cadastrado
        with self.assertRaises(GroupingValidationError):
            engine.process_custody_accounts("EVENT-INEXISTENTE", [])

        # Lista vazia
        event = GroupingEvent("EVT-1", "T", "ISIN", "C", ratio_from=10, ratio_to=1)
        engine.register_event(event)
        with self.assertRaises(CustodyAccountError):
            engine.process_custody_accounts(event.event_id, [])

        # Mismatch de quantidade no leilão
        holders = [ShareFractionHolder("ACC", "0008", "N", "1", 17)]  # 1 inteira, 0.7 fração
        engine.process_custody_accounts(event.event_id, holders)  # auction_whole_shares = 0
        bad_auction = FractionsAuctionOutcome(
            auction_id="A1",
            event_id=event.event_id,
            ticker="T",
            shares_auctioned=10,  # Pool tem 0
            gross_price_per_share=Decimal("10.00"),
            gross_total_amount=Decimal("100.00"),
        )
        with self.assertRaises(AuctionValidationError):
            engine.register_auction_outcome(bad_auction)

        # Mismatch bruto != preço * quantidade
        bad_price_auction = FractionsAuctionOutcome(
            auction_id="A2",
            event_id=event.event_id,
            ticker="T",
            shares_auctioned=1,
            gross_price_per_share=Decimal("10.00"),
            gross_total_amount=Decimal("999.00"),  # Deveria ser 10.00
        )
        # Ajustar pool para 1
        holders_for_one = [ShareFractionHolder("ACC-1", "0008", "N", "1", 20)]
        engine.process_custody_accounts(event.event_id, holders_for_one)
        # Agora pool tem 0 ações de sobra porque 20 // 10 = 2 e frac = 0.
        holders_for_one = [ShareFractionHolder("ACC-1", "0008", "N", "1", 25), ShareFractionHolder("ACC-2", "0008", "N", "2", 25)]
        engine.process_custody_accounts(event.event_id, holders_for_one) # 0.5 + 0.5 = 1.0 -> 1 ação
        with self.assertRaises(AuctionValidationError):
            engine.register_auction_outcome(bad_price_auction)

        # Evento duplicado
        with self.assertRaises(GroupingValidationError):
            engine.register_event(event)

        # Extrato para evento inexistente
        with self.assertRaises(CustodyAccountError):
            engine.export_liquidation_statement("EVENT-INEXISTENTE")

    def test_reverse_split_one_hundred_to_one(self) -> None:
        """Valida cenário de grupamento agressivo 100:1 (ex: OIBR3)."""
        engine = ShareFractionsAuctionEngine()
        event = GroupingEvent(
            event_id="EVT-100TO1",
            ticker="OIBR3",
            isin="BROIBRACNOR1",
            company_name="OI S.A. - EM RECUPERACAO JUDICIAL",
            ratio_from=100,
            ratio_to=1,
            approval_date=date(2026, 10, 1),
            cutoff_date=date(2026, 11, 1),
        )
        holders = [
            ShareFractionHolder("ACC-01", "0008", "H1", "1", 175),  # 1 inteira, 0.75 fração
            ShareFractionHolder("ACC-02", "0072", "H2", "2", 25),   # 0 inteira, 0.25 fração
        ]
        engine.register_event(event)
        summary = engine.process_custody_accounts(event.event_id, holders)

        self.assertEqual(summary.total_original_shares, 200)
        self.assertEqual(summary.total_whole_shares_allocated, 1)
        self.assertEqual(summary.total_fractional_shares, Decimal("1.0"))
        self.assertEqual(summary.auction_whole_shares, 1)

        auction = FractionsAuctionOutcome(
            auction_id="AUC-100TO1",
            event_id=event.event_id,
            ticker="OIBR3",
            shares_auctioned=1,
            gross_price_per_share=Decimal("12.00"),
            gross_total_amount=Decimal("12.00"),
            trading_fees=Decimal("0.05"),
            clearing_fees=Decimal("0.02"),
            brokerage_fees=Decimal("0.10"),
            total_fees=Decimal("0.17"),
            net_total_amount=Decimal("11.83"),
        )
        engine.register_auction_outcome(auction)
        distributed = engine.calculate_pro_rata_distribution(event.event_id)

        # H1 tem 75% e H2 tem 25%
        # Gross: 12.00 * 0.75 = 9.00, 12.00 * 0.25 = 3.00
        # Fees: 0.17 * 0.75 = 0.1275 -> 13 cents, 0.17 * 0.25 = 0.0425 -> 4 cents. Total = 0.17
        # Net: 9.00 - 0.13 = 8.87; 3.00 - 0.04 = 2.96. Total = 11.83.
        self.assertEqual(distributed[0].cash_credit_gross, Decimal("9.00"))
        self.assertEqual(distributed[0].fee_deduction, Decimal("0.13"))
        self.assertEqual(distributed[0].cash_credit_net, Decimal("8.87"))

        self.assertEqual(distributed[1].cash_credit_gross, Decimal("3.00"))
        self.assertEqual(distributed[1].fee_deduction, Decimal("0.04"))
        self.assertEqual(distributed[1].cash_credit_net, Decimal("2.96"))

        invariants = engine.validate_conservation_invariants(event.event_id)
        self.assertTrue(invariants["all_invariants_satisfied"])

    def test_serialization_to_dict(self) -> None:
        """Valida os métodos to_dict das estruturas de dados."""
        holder = ShareFractionHolder("ACC-1", "0008", "TESTE", "111", 137, 13, Decimal("0.7"))
        h_dict = holder.to_dict()
        self.assertEqual(h_dict["investor_account"], "ACC-1")
        self.assertEqual(h_dict["whole_shares"], 13)
        self.assertEqual(h_dict["fraction_share"], "0.7")

        event = GroupingEvent(
            "EVT-TEST", "TEST3", "BRTEST3", "TEST S.A.", 10, 1,
            approval_date=date(2026, 1, 1), cutoff_date=date(2026, 1, 2)
        )
        e_dict = event.to_dict()
        self.assertEqual(e_dict["event_id"], "EVT-TEST")
        self.assertEqual(e_dict["ratio_str"], "10:1")

        summary = FractionsPoolSummary(
            "EVT-TEST", "TEST3", 1, 1, 137, 13, Decimal("0.7"), 0, Decimal("0.7")
        )
        s_dict = summary.to_dict()
        self.assertEqual(s_dict["total_original_shares"], 137)

        outcome = FractionsAuctionOutcome(
            "AUC-1", "EVT-TEST", "TEST3", 1, Decimal("10.00"), Decimal("10.00")
        )
        o_dict = outcome.to_dict()
        self.assertEqual(o_dict["auction_id"], "AUC-1")


if __name__ == "__main__":
    unittest.main()
