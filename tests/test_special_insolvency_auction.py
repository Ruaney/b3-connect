"""Testes unitários e verificação formal de Leilões Especiais de Insolvência e Desinvestimento (FL-081).

Valida integralmente o motor de leilões judiciais conforme a Lei nº 11.101/2005 e os regulamentos B3:
1. Habilitação prévia e bloqueio de caução (Bid Bond);
2. Sessão de propostas fechadas (Stalking Horse) e pregão eletrônico de overbidding com degrau mínimo;
3. Respeito ao preço de reserva mínimo homologado pelo juízo recuperacional;
4. Exercício de preferência / equiparação (Right to Match) pelo Stalking Horse;
5. Invariante formal de conservação de ativos leiloados: Lote_Total == Lote_Arrematado + Lote_Deserto;
6. Invariante de Restituição Integral de Caução a participantes não vencedores: Total_Caução_Retida == Caução_Vencedor;
7. Invariante de Liquidação Financeira Integral (Cascata Judicial de Pagamento - Art. 84 e 83 LF-RJ);
8. Rateio proporcional exato em centavos pelo método dos maiores restos (Hamilton / Hare-Niemeyer).
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.special_insolvency_auction import (
    AuctionBid,
    AuctionPhase,
    BidBelowReservePriceError,
    BidBondRestitutionLine,
    BidBondRestitutionReport,
    BidRoundType,
    CREDITOR_CLASS_HIERARCHY,
    CreditorClaim,
    CreditorClass,
    CreditorPaymentDetail,
    ExtracurricularExpense,
    ExtracurricularExpenseType,
    InsolvencyAuction,
    InsolvencyAuctionInvariantError,
    InsolvencyAuctionSummary,
    InsolvencyLot,
    InsolvencyLotType,
    InsufficientBidBondError,
    InvalidOverbidIncrementError,
    LotAuctionStatus,
    LotNotFoundError,
    Proponent,
    ProponentNotFoundError,
    ProponentNotQualifiedError,
    SpecialInsolvencyAuctionEngine,
    SpecialInsolvencyAuctionError,
    WaterfallSettlementResult,
    _distribute_pro_rata_cents,
    _quantize_money,
    _quantize_rate,
    _to_date,
    _to_datetime,
)


class TestSpecialInsolvencyAuction(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_path = self.fixtures_dir / "special_insolvency_auction.json"

    def test_fixture_loading_and_portfolio_summary(self) -> None:
        """Valida o carregamento da fixture oficial JSON e o resumo do portfólio de leilões."""
        engine = SpecialInsolvencyAuctionEngine.from_json_fixture(self.fixture_path)
        self.assertEqual(len(engine.auctions), 4)

        summary = engine.summarize()
        self.assertEqual(summary["total_auctions"], 4)
        self.assertEqual(summary["total_lots"], 6)
        self.assertEqual(summary["total_awarded_lots"], 3)  # UPI-001 (1) + SIDERURGIA (1) + BIOENERGIA (1)
        self.assertEqual(summary["total_desert_lots"], 3)   # SIDERURGIA (1) + TOTAL-DESERTO (2)
        self.assertTrue(summary["all_invariants_satisfied"])

        # Total arrecadado: 1.6B + 280M + 120M = 2.000.000.000,00 BRL
        expected_total = Decimal("2000000000.00")
        self.assertEqual(Decimal(summary["total_proceeds_brl"]), expected_total)

    def test_scenario_01_telecom_upi_stalking_horse_and_waterfall(self) -> None:
        """Cenário 1: Alienação de UPI com Stalking Horse, Overbidding, Right to Match e Waterfall Completo."""
        engine = SpecialInsolvencyAuctionEngine.from_json_fixture(self.fixture_path)
        rep = engine.run_auction("AUCTION-RJ-UPI-001")

        self.assertEqual(rep.auction_id, "AUCTION-RJ-UPI-001")
        self.assertEqual(rep.total_lots, 1)
        self.assertEqual(rep.awarded_lots, 1)
        self.assertEqual(rep.desert_lots, 0)
        self.assertTrue(rep.is_asset_conserved)
        self.assertTrue(rep.is_bid_bond_conserved)
        self.assertTrue(rep.is_waterfall_conserved)
        self.assertTrue(rep.all_invariants_satisfied)

        auction = engine.get_auction("AUCTION-RJ-UPI-001")
        lot = auction.lots["LOT-UPI-FIBRA-01"]
        self.assertEqual(lot.status, LotAuctionStatus.AWARDED)
        self.assertEqual(lot.winning_proponent_id, "PROP-SH-01")
        self.assertEqual(lot.winning_bid_amount, Decimal("1600000000.00"))

        # Validação da Invariante 2: Restituição de Caução
        # Caução depositada: 3 * 75M = 225M
        # Retida (vencedor PROP-SH-01): 75M
        # Devolvida aos demais (PROP-CONC-02 e PROP-PATRIA-03): 150M
        bb_rep = auction.bid_bond_report
        self.assertIsNotNone(bb_rep)
        self.assertEqual(bb_rep.total_deposited, Decimal("225000000.00"))
        self.assertEqual(bb_rep.total_retained, Decimal("75000000.00"))
        self.assertEqual(bb_rep.total_refunded, Decimal("150000000.00"))
        self.assertEqual(bb_rep.total_retained, auction.proponents["PROP-SH-01"].bid_bond_deposited)
        self.assertTrue(bb_rep.is_conserved)

        # Validação da Invariante 3: Liquidação Financeira Integral
        wf = auction.waterfall_result
        self.assertIsNotNone(wf)
        self.assertEqual(wf.gross_auction_proceeds, Decimal("1600000000.00"))
        self.assertEqual(wf.retained_bid_bond_applied, Decimal("75000000.00"))
        self.assertEqual(wf.cash_settlement_collected, Decimal("1525000000.00"))
        self.assertEqual(wf.total_extracurricular_expenses, Decimal("45000000.00"))
        self.assertEqual(wf.net_proceeds_for_creditors, Decimal("1555000000.00"))

        # Pagamentos das classes
        # Classe I (Trabalhista): 150M (100%)
        # Classe II (Garantia Real): 800M (100%)
        # Classe III (Tributária): 500M (100%)
        # Classe VI (Quirografário): demanda de 300M, recebem 105M (35% pro-rata exato)
        # 150M + 800M + 500M + 105M = 1.555.000.000,00
        self.assertEqual(wf.payments_by_class[CreditorClass.CLASSE_I_TRABALHISTA.value], Decimal("150000000.00"))
        self.assertEqual(wf.payments_by_class[CreditorClass.CLASSE_II_GARANTIA_REAL.value], Decimal("800000000.00"))
        self.assertEqual(wf.payments_by_class[CreditorClass.CLASSE_III_TRIBUTARIA.value], Decimal("500000000.00"))
        self.assertEqual(wf.payments_by_class[CreditorClass.CLASSE_VI_QUIROGRAFARIO.value], Decimal("105000000.00"))
        self.assertEqual(wf.total_paid_to_creditors, Decimal("1555000000.00"))
        self.assertEqual(wf.residual_surplus_to_debtor, Decimal("0.00"))

        # Conferência do rateio de quirografários
        p_nokia = next(p for p in wf.individual_payments if p.creditor_id == "CR-QUIR-01")
        p_huawei = next(p for p in wf.individual_payments if p.creditor_id == "CR-QUIR-02")
        p_cisco = next(p for p in wf.individual_payments if p.creditor_id == "CR-QUIR-03")
        self.assertEqual(p_nokia.paid_amount, Decimal("52500000.00"))
        self.assertEqual(p_huawei.paid_amount, Decimal("35000000.00"))
        self.assertEqual(p_cisco.paid_amount, Decimal("17500000.00"))

        # Fechamento centavo a centavo
        self.assertEqual(
            wf.gross_auction_proceeds,
            wf.total_extracurricular_expenses + wf.total_paid_to_creditors + wf.residual_surplus_to_debtor,
        )

    def test_scenario_02_multi_lot_awarded_and_desert(self) -> None:
        """Cenário 2: Leilão de Múltiplos Lotes com 1 Arrematado e 1 Deserto."""
        engine = SpecialInsolvencyAuctionEngine.from_json_fixture(self.fixture_path)
        rep = engine.run_auction("AUCTION-RJ-SIDERURGIA-MULTI-02")

        # Invariante 1: Lote_Total == Lote_Arrematado + Lote_Deserto (2 == 1 + 1)
        self.assertEqual(rep.total_lots, 2)
        self.assertEqual(rep.awarded_lots, 1)
        self.assertEqual(rep.desert_lots, 1)
        self.assertEqual(rep.total_lots, rep.awarded_lots + rep.desert_lots)
        self.assertTrue(rep.is_asset_conserved)

        auction = engine.get_auction("AUCTION-RJ-SIDERURGIA-MULTI-02")
        lot1 = auction.lots["LOT-SID-UPI-01"]
        lot2 = auction.lots["LOT-SID-TERRENO-02"]

        self.assertEqual(lot1.status, LotAuctionStatus.AWARDED)
        self.assertEqual(lot1.winning_proponent_id, "PROP-CSN-01")
        self.assertEqual(lot1.winning_bid_amount, Decimal("280000000.00"))

        self.assertEqual(lot2.status, LotAuctionStatus.DESERTO)
        self.assertIsNone(lot2.winning_proponent_id)
        self.assertIsNone(lot2.winning_bid_amount)

        # Invariante 2: Restituição de Caução
        # PROP-CSN-01 venceu: retido R$ 15.500.000,00
        # PROP-GERDAU-02 e PROP-IMOB-03 perderam: 100% devolvido
        bb = auction.bid_bond_report
        self.assertIsNotNone(bb)
        self.assertEqual(bb.total_retained, Decimal("15500000.00"))
        self.assertEqual(bb.total_refunded, Decimal("15500000.00") + Decimal("3000000.00"))
        self.assertEqual(bb.total_deposited, bb.total_retained + bb.total_refunded)
        self.assertTrue(bb.is_conserved)

        # Invariante 3: Waterfall
        wf = auction.waterfall_result
        self.assertIsNotNone(wf)
        self.assertEqual(wf.gross_auction_proceeds, Decimal("280000000.00"))
        self.assertEqual(wf.total_extracurricular_expenses, Decimal("10000000.00"))
        self.assertEqual(wf.net_proceeds_for_creditors, Decimal("270000000.00"))
        # Trabalhista (70M) + Garantia Real (200M) = 270M (100% quitados)
        self.assertEqual(wf.total_paid_to_creditors, Decimal("270000000.00"))
        self.assertEqual(wf.residual_surplus_to_debtor, Decimal("0.00"))

    def test_scenario_03_equity_surplus_returned_to_debtor(self) -> None:
        """Cenário 3: Alienação Judicial com Superávit Remanescente devolvido à Sociedade Recuperanda."""
        engine = SpecialInsolvencyAuctionEngine.from_json_fixture(self.fixture_path)
        rep = engine.run_auction("AUCTION-RJ-SUPERAVIT-EQUITY-03")

        self.assertTrue(rep.all_invariants_satisfied)
        self.assertEqual(rep.total_proceeds, Decimal("120000000.00"))

        auction = engine.get_auction("AUCTION-RJ-SUPERAVIT-EQUITY-03")
        wf = auction.waterfall_result
        self.assertIsNotNone(wf)

        # Custas: 1.5M + 3.5M = 5.0M
        self.assertEqual(wf.total_extracurricular_expenses, Decimal("5000000.00"))
        # Credores: 15M (trab) + 40M (gar) + 10M (trib) + 20M (quir) = 85.0M
        self.assertEqual(wf.total_paid_to_creditors, Decimal("85000000.00"))
        # Superávit remanescente para a devedora: 120M - 5M - 85M = 30M
        self.assertEqual(wf.residual_surplus_to_debtor, Decimal("30000000.00"))

        # Confirma conservação absoluta
        self.assertEqual(
            wf.gross_auction_proceeds,
            wf.total_extracurricular_expenses + wf.total_paid_to_creditors + wf.residual_surplus_to_debtor,
        )

    def test_scenario_04_total_deserto_auction(self) -> None:
        """Cenário 4: Leilão 100% Deserto com Restituição Integral de Todas as Cauções."""
        engine = SpecialInsolvencyAuctionEngine.from_json_fixture(self.fixture_path)
        rep = engine.run_auction("AUCTION-RJ-TOTAL-DESERTO-04")

        self.assertEqual(rep.total_lots, 2)
        self.assertEqual(rep.awarded_lots, 0)
        self.assertEqual(rep.desert_lots, 2)
        self.assertEqual(rep.total_proceeds, Decimal("0.00"))
        self.assertTrue(rep.is_asset_conserved)
        self.assertTrue(rep.is_bid_bond_conserved)
        self.assertTrue(rep.is_waterfall_conserved)
        self.assertTrue(rep.all_invariants_satisfied)

        auction = engine.get_auction("AUCTION-RJ-TOTAL-DESERTO-04")
        bb = auction.bid_bond_report
        self.assertIsNotNone(bb)
        # Total retido deve ser rigorosamente zero
        self.assertEqual(bb.total_retained, Decimal("0.00"))
        self.assertEqual(bb.total_refunded, bb.total_deposited)
        self.assertEqual(bb.winning_proponents, [])

    def test_proponent_not_qualified_error(self) -> None:
        """Valida que participante sem habilitação prévia é rejeitado."""
        auction = InsolvencyAuction(
            auction_id="ERR-QUALIF",
            debtor_name="EMPRESA TESTE",
            judicial_process_number="0001",
            court_name="1ª Vara",
            judicial_administrator="AJ Teste",
            auction_date="2026-11-04",
            settlement_date="2026-11-06",
        )
        lot = InsolvencyLot(
            lot_id="LOT-01",
            asset_name="Ativo Teste",
            lot_type=InsolvencyLotType.UPI,
            quantity=1,
            judicial_reserve_price=Decimal("1000000.00"),
            minimum_overbid_step=Decimal("50000.00"),
            required_bid_bond_amount=Decimal("50000.00"),
            judicial_process_number="0001",
            court_name="1ª Vara",
            debtor_name="EMPRESA TESTE",
            judicial_administrator="AJ Teste",
        )
        auction.register_lot(lot)
        p = Proponent(
            proponent_id="P-UNQUALIFIED",
            name="Não Habilitado",
            document_id="000",
            participant_code=1,
            custody_account="ACC1",
            is_qualified=False,  # Não habilitado
            bid_bond_deposited=Decimal("100000.00"),
            bid_bond_blocked=True,
        )
        auction.register_proponent(p)

        bid = AuctionBid(
            bid_id="BID-ERR-1",
            lot_id="LOT-01",
            proponent_id="P-UNQUALIFIED",
            round_type=BidRoundType.SEALED_BID,
            bid_amount=Decimal("1100000.00"),
            timestamp="2026-11-04T10:00:00Z",
        )
        with self.assertRaises(ProponentNotQualifiedError):
            auction.submit_bid(bid)

    def test_insufficient_or_unblocked_bid_bond_error(self) -> None:
        """Valida rejeição de lances sem caução bloqueada ou com caução insuficiente."""
        auction = InsolvencyAuction(
            auction_id="ERR-BOND",
            debtor_name="EMPRESA TESTE",
            judicial_process_number="0001",
            court_name="1ª Vara",
            judicial_administrator="AJ Teste",
            auction_date="2026-11-04",
            settlement_date="2026-11-06",
        )
        lot = InsolvencyLot(
            lot_id="LOT-01",
            asset_name="Ativo Teste",
            lot_type=InsolvencyLotType.UPI,
            quantity=1,
            judicial_reserve_price=Decimal("1000000.00"),
            minimum_overbid_step=Decimal("50000.00"),
            required_bid_bond_amount=Decimal("50000.00"),
            judicial_process_number="0001",
            court_name="1ª Vara",
            debtor_name="EMPRESA TESTE",
            judicial_administrator="AJ Teste",
        )
        auction.register_lot(lot)

        # Caso 1: Caução não bloqueada
        p1 = Proponent(
            proponent_id="P-UNBLOCKED",
            name="Caução Não Bloqueada",
            document_id="001",
            participant_code=1,
            custody_account="ACC1",
            is_qualified=True,
            bid_bond_deposited=Decimal("100000.00"),
            bid_bond_blocked=False,  # Não bloqueada
        )
        auction.register_proponent(p1)
        bid1 = AuctionBid(
            bid_id="B1",
            lot_id="LOT-01",
            proponent_id="P-UNBLOCKED",
            round_type=BidRoundType.SEALED_BID,
            bid_amount=Decimal("1100000.00"),
            timestamp="2026-11-04T10:00:00Z",
        )
        with self.assertRaises(InsufficientBidBondError):
            auction.submit_bid(bid1)

        # Caso 2: Caução depositada menor que o exigido
        p2 = Proponent(
            proponent_id="P-SHORT",
            name="Caução Insuficiente",
            document_id="002",
            participant_code=1,
            custody_account="ACC2",
            is_qualified=True,
            bid_bond_deposited=Decimal("20000.00"),  # Menor que 50.000,00 exigido
            bid_bond_blocked=True,
        )
        auction.register_proponent(p2)
        bid2 = AuctionBid(
            bid_id="B2",
            lot_id="LOT-01",
            proponent_id="P-SHORT",
            round_type=BidRoundType.SEALED_BID,
            bid_amount=Decimal("1100000.00"),
            timestamp="2026-11-04T10:00:00Z",
        )
        with self.assertRaises(InsufficientBidBondError):
            auction.submit_bid(bid2)

    def test_bid_below_reserve_price_error(self) -> None:
        """Valida rejeição de lances inferiores ao preço de reserva mínimo homologado."""
        auction = InsolvencyAuction(
            auction_id="ERR-RESERVE",
            debtor_name="EMPRESA TESTE",
            judicial_process_number="0001",
            court_name="1ª Vara",
            judicial_administrator="AJ Teste",
            auction_date="2026-11-04",
            settlement_date="2026-11-06",
        )
        lot = InsolvencyLot(
            lot_id="LOT-01",
            asset_name="Ativo Teste",
            lot_type=InsolvencyLotType.UPI,
            quantity=1,
            judicial_reserve_price=Decimal("1000000.00"),
            minimum_overbid_step=Decimal("50000.00"),
            required_bid_bond_amount=Decimal("50000.00"),
            judicial_process_number="0001",
            court_name="1ª Vara",
            debtor_name="EMPRESA TESTE",
            judicial_administrator="AJ Teste",
        )
        auction.register_lot(lot)
        p = Proponent(
            proponent_id="P-OK",
            name="Habilitado",
            document_id="001",
            participant_code=1,
            custody_account="ACC1",
            is_qualified=True,
            bid_bond_deposited=Decimal("50000.00"),
            bid_bond_blocked=True,
        )
        auction.register_proponent(p)

        bid = AuctionBid(
            bid_id="B-LOW",
            lot_id="LOT-01",
            proponent_id="P-OK",
            round_type=BidRoundType.SEALED_BID,
            bid_amount=Decimal("950000.00"),  # Inferior a 1.000.000,00
            timestamp="2026-11-04T10:00:00Z",
        )
        with self.assertRaises(BidBelowReservePriceError):
            auction.submit_bid(bid)

    def test_invalid_overbid_step_error(self) -> None:
        """Valida que lance no pregão que não respeita o incremento mínimo é rejeitado."""
        auction = InsolvencyAuction(
            auction_id="ERR-OVERBID",
            debtor_name="EMPRESA TESTE",
            judicial_process_number="0001",
            court_name="1ª Vara",
            judicial_administrator="AJ Teste",
            auction_date="2026-11-04",
            settlement_date="2026-11-06",
        )
        lot = InsolvencyLot(
            lot_id="LOT-01",
            asset_name="Ativo Teste",
            lot_type=InsolvencyLotType.UPI,
            quantity=1,
            judicial_reserve_price=Decimal("1000000.00"),
            minimum_overbid_step=Decimal("50000.00"),  # Step de 50.000,00
            required_bid_bond_amount=Decimal("50000.00"),
            judicial_process_number="0001",
            court_name="1ª Vara",
            debtor_name="EMPRESA TESTE",
            judicial_administrator="AJ Teste",
        )
        auction.register_lot(lot)

        p1 = Proponent(
            proponent_id="P1",
            name="Proponente 1",
            document_id="001",
            participant_code=1,
            custody_account="ACC1",
            is_qualified=True,
            bid_bond_deposited=Decimal("50000.00"),
            bid_bond_blocked=True,
        )
        p2 = Proponent(
            proponent_id="P2",
            name="Proponente 2",
            document_id="002",
            participant_code=2,
            custody_account="ACC2",
            is_qualified=True,
            bid_bond_deposited=Decimal("50000.00"),
            bid_bond_blocked=True,
        )
        auction.register_proponent(p1)
        auction.register_proponent(p2)

        # Lance 1 válido: 1.000.000,00
        bid1 = AuctionBid(
            bid_id="B1",
            lot_id="LOT-01",
            proponent_id="P1",
            round_type=BidRoundType.SEALED_BID,
            bid_amount=Decimal("1000000.00"),
            timestamp="2026-11-04T10:00:00Z",
        )
        auction.submit_bid(bid1)

        # Lance 2 de overbid com incremento de apenas 10.000,00 (mínimo exigido: 50.000,00 -> total 1.050.000,00)
        bid2 = AuctionBid(
            bid_id="B2",
            lot_id="LOT-01",
            proponent_id="P2",
            round_type=BidRoundType.OPEN_OVERBID,
            bid_amount=Decimal("1010000.00"),
            timestamp="2026-11-04T10:30:00Z",
        )
        with self.assertRaises(InvalidOverbidIncrementError):
            auction.submit_bid(bid2)

    def test_unauthorized_right_to_match_error(self) -> None:
        """Valida que proponente sem prerrogativa de Stalking Horse não pode dar MATCHING_BID."""
        auction = InsolvencyAuction(
            auction_id="ERR-MATCH",
            debtor_name="EMPRESA TESTE",
            judicial_process_number="0001",
            court_name="1ª Vara",
            judicial_administrator="AJ Teste",
            auction_date="2026-11-04",
            settlement_date="2026-11-06",
        )
        lot = InsolvencyLot(
            lot_id="LOT-01",
            asset_name="Ativo Teste",
            lot_type=InsolvencyLotType.UPI,
            quantity=1,
            judicial_reserve_price=Decimal("1000000.00"),
            minimum_overbid_step=Decimal("50000.00"),
            required_bid_bond_amount=Decimal("50000.00"),
            judicial_process_number="0001",
            court_name="1ª Vara",
            debtor_name="EMPRESA TESTE",
            judicial_administrator="AJ Teste",
        )
        auction.register_lot(lot)
        p = Proponent(
            proponent_id="P-COMMON",
            name="Participante Comum",
            document_id="001",
            participant_code=1,
            custody_account="ACC1",
            is_qualified=True,
            bid_bond_deposited=Decimal("50000.00"),
            bid_bond_blocked=True,
            is_stalking_horse=False,
            has_right_to_match=False,
        )
        auction.register_proponent(p)

        bid = AuctionBid(
            bid_id="B-MATCH",
            lot_id="LOT-01",
            proponent_id="P-COMMON",
            round_type=BidRoundType.MATCHING_BID,
            bid_amount=Decimal("1000000.00"),
            timestamp="2026-11-04T10:00:00Z",
        )
        with self.assertRaises(SpecialInsolvencyAuctionError):
            auction.submit_bid(bid)

    def test_lot_or_proponent_not_found(self) -> None:
        """Valida exceções de busca de lote ou proponente inexistente."""
        auction = InsolvencyAuction(
            auction_id="NOT-FOUND",
            debtor_name="EMPRESA TESTE",
            judicial_process_number="0001",
            court_name="1ª Vara",
            judicial_administrator="AJ Teste",
            auction_date="2026-11-04",
            settlement_date="2026-11-06",
        )
        engine = SpecialInsolvencyAuctionEngine()
        engine.register_auction(auction)

        with self.assertRaises(LotNotFoundError):
            engine.get_auction("AUCTION-DOES-NOT-EXIST")

        with self.assertRaises(ProponentNotFoundError):
            auction.qualify_proponent("P-UNKNOWN", Decimal("1000.00"))

        bid_unknown_lot = AuctionBid(
            bid_id="B-ERR",
            lot_id="NON-EXISTENT-LOT",
            proponent_id="P1",
            round_type=BidRoundType.SEALED_BID,
            bid_amount=Decimal("1000000.00"),
            timestamp="2026-11-04T10:00:00Z",
        )
        with self.assertRaises(LotNotFoundError):
            auction.submit_bid(bid_unknown_lot)

    def test_hamilton_pro_rata_exact_cents_conservation(self) -> None:
        """Valida que o rateio pro-rata pelo método dos maiores restos não perde centavos."""
        # 3 credores com créditos ímpares e valor a ratear que gera dízima periódica
        claims = [
            ("C1", Decimal("100.00")),
            ("C2", Decimal("200.00")),
            ("C3", Decimal("300.00")),
        ]
        # Total claims = 600.00. Ratear 100.00:
        # C1 proporção = 1/6 = 16.6666...
        # C2 proporção = 2/6 = 33.3333...
        # C3 proporção = 3/6 = 50.0000...
        dist = _distribute_pro_rata_cents(Decimal("100.00"), claims)
        self.assertEqual(sum(dist.values()), Decimal("100.00"))
        self.assertEqual(dist["C1"], Decimal("16.67"))
        self.assertEqual(dist["C2"], Decimal("33.33"))
        self.assertEqual(dist["C3"], Decimal("50.00"))

        # Caso em que o montante é suficiente para 100%
        dist_full = _distribute_pro_rata_cents(Decimal("700.00"), claims)
        self.assertEqual(dist_full["C1"], Decimal("100.00"))
        self.assertEqual(dist_full["C2"], Decimal("200.00"))
        self.assertEqual(dist_full["C3"], Decimal("300.00"))

        # Caso com montante zero ou créditos vazios
        self.assertEqual(_distribute_pro_rata_cents(Decimal("0.00"), claims)["C1"], Decimal("0.00"))
        self.assertEqual(_distribute_pro_rata_cents(Decimal("50.00"), []), {})

    def test_helper_functions_and_quantization(self) -> None:
        """Valida funções utilitárias de data, datetime e quantização monetária."""
        self.assertEqual(_quantize_money(100.555), Decimal("100.56"))
        self.assertEqual(_quantize_rate("0.123456789"), Decimal("0.12345679"))
        self.assertEqual(_to_date(date(2026, 11, 4), "dt"), date(2026, 11, 4))
        self.assertEqual(_to_date("2026-11-04", "dt"), date(2026, 11, 4))
        self.assertEqual(_to_datetime(datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc), "ts").hour, 10)
        self.assertEqual(_to_datetime("2026-11-04T10:00:00Z", "ts").tzinfo, timezone.utc)

        with self.assertRaises(ValueError):
            _to_date(12345, "invalid")
        with self.assertRaises(ValueError):
            _to_datetime(99999, "invalid")

    def test_serialization_and_to_dict_roundtrip(self) -> None:
        """Valida serialização em dicionário e round-trip completo."""
        engine = SpecialInsolvencyAuctionEngine.from_json_fixture(self.fixture_path)
        auction = engine.get_auction("AUCTION-RJ-UPI-001")
        auction.run()

        data = auction.to_dict()
        self.assertEqual(data["auction_id"], "AUCTION-RJ-UPI-001")
        self.assertIn("lots", data)
        self.assertIn("proponents", data)
        self.assertIn("bids", data)
        self.assertIn("waterfall_result", data)
        self.assertIn("bid_bond_report", data)

        reloaded = InsolvencyAuction.from_dict(data)
        self.assertEqual(reloaded.auction_id, auction.auction_id)
        self.assertEqual(len(reloaded.lots), len(auction.lots))
        self.assertEqual(len(reloaded.proponents), len(auction.proponents))

    def test_verify_invariants_failure_detection(self) -> None:
        """Valida que verify_invariants detecta inconsistências forçadas e lança exceção em modo estrito."""
        engine = SpecialInsolvencyAuctionEngine.from_json_fixture(self.fixture_path)
        auction = engine.get_auction("AUCTION-RJ-UPI-001")
        auction.run()

        # Força adulteração no waterfall (quebra a conservação de recursos)
        assert auction.waterfall_result is not None
        auction.waterfall_result.gross_auction_proceeds = Decimal("9999999999.00")

        inv = auction.verify_invariants(raise_on_error=False)
        self.assertFalse(inv["financial_waterfall_conservation"])
        self.assertFalse(inv["all_invariants_satisfied"])

        with self.assertRaises(InsolvencyAuctionInvariantError):
            auction.verify_invariants(raise_on_error=True)


if __name__ == "__main__":
    unittest.main()
