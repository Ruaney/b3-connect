"""Testes unitários para o módulo de Balcão e Renda Fixa NoMe B3 (FL-014 e FL-019).

Valida:
- Parsing e validação de registro de emissão de CDB (JSON) e Debênture (XML).
- Regras de negócio, conservação do valor financeiro e validações de integridade.
- Batimento bilateral de ordens secundárias (Matching Engine).
- Conciliação determinística de extrato NoMe com 100% de match.
- Detecção e categorização de quebras de conciliação: QUANTITY_MISMATCH,
  PU_MISMATCH, GHOST_IN_NOME e MISSING_IN_NOME.
"""

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH independentemente do diretório de execução
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.nome_balcao import (
    FixedIncomeAsset,
    IndexerType,
    InstrumentType,
    InternalBalcaoPosition,
    NoMeBreakType,
    NoMeError,
    NoMeParsingError,
    NoMeReconciler,
    NoMeStatementFile,
    NoMeStatementPosition,
    NoMeTradeRegistration,
    NoMeValidationError,
    PartyParticipant,
    SettlementType,
    TradeMatchStatus,
    match_bilateral_trades,
    parse_nome_statement_json,
    parse_nome_trade_json,
    parse_nome_trade_xml,
    reconcile_nome_positions,
)


class TestNoMeModelsAndValidation(unittest.TestCase):
    """Testes de validação dos modelos de dados do NoMe."""

    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"

    def test_cdb_registration_from_json_fixture(self) -> None:
        """Valida o registro de CDB pós-fixado a partir da fixture JSON oficial."""
        json_path = self.fixtures_dir / "nome_cdb_registration.json"
        trade = parse_nome_trade_json(json_path)

        self.assertEqual(trade.trade_id, "REG-CDB-20261103-0001")
        self.assertEqual(trade.trade_date, date(2026, 11, 3))
        self.assertEqual(trade.settlement_date, date(2026, 11, 3))
        self.assertEqual(trade.settlement_type, SettlementType.GROSS_STR)
        self.assertEqual(trade.match_status, TradeMatchStatus.MATCHED)

        # Ativo
        asset = trade.asset
        self.assertEqual(asset.asset_code, "CDB-ITAU-202811")
        self.assertEqual(asset.instrument_type, InstrumentType.CDB)
        self.assertEqual(asset.issuer_cnpj, "60701190000104")
        self.assertEqual(asset.issuer_name, "BANCO ITAU UNIBANCO S.A.")
        self.assertEqual(asset.issue_date, date(2026, 11, 3))
        self.assertEqual(asset.maturity_date, date(2028, 11, 3))
        self.assertEqual(asset.indexer, IndexerType.DI)
        self.assertEqual(asset.indexer_percentage, Decimal("105.00"))
        self.assertEqual(asset.emission_unit_price, Decimal("1000.00000000"))

        # Negociação e Financeiro
        self.assertEqual(trade.quantity, Decimal("5000"))
        self.assertEqual(trade.unit_price, Decimal("1000.00000000"))
        self.assertEqual(trade.financial_amount, Decimal("5000000.00"))

        # Participantes
        self.assertEqual(trade.buyer.participant_id, "00001050")
        self.assertEqual(trade.buyer.custody_account, 105001)
        self.assertEqual(trade.seller.participant_id, "00000033")
        self.assertEqual(trade.seller.custody_account, 330001)

        # Serialização para dict
        d = trade.to_dict()
        self.assertEqual(d["trade_id"], "REG-CDB-20261103-0001")
        self.assertEqual(d["quantity"], 5000.0)

    def test_debenture_trade_from_xml_fixture(self) -> None:
        """Valida o registro de negociação de Debênture via XML com spread de taxa e PU fracionário."""
        xml_path = self.fixtures_dir / "nome_debenture_trade.xml"
        trade = parse_nome_trade_xml(xml_path)

        self.assertEqual(trade.trade_id, "TRD-DEB-20261103-8891")
        self.assertEqual(trade.trade_date, date(2026, 11, 3))
        self.assertEqual(trade.settlement_date, date(2026, 11, 4))
        self.assertEqual(trade.settlement_type, SettlementType.GROSS_STR)

        asset = trade.asset
        self.assertEqual(asset.asset_code, "DEB-VALE-203012")
        self.assertEqual(asset.instrument_type, InstrumentType.DEBENTURE)
        self.assertEqual(asset.issuer_cnpj, "33592510000154")
        self.assertEqual(asset.issuer_name, "VALE S.A.")
        self.assertEqual(asset.indexer, IndexerType.IPCA)
        self.assertEqual(asset.rate_spread, Decimal("5.85"))

        self.assertEqual(trade.quantity, Decimal("2500"))
        self.assertEqual(trade.unit_price, Decimal("1045.50210000"))
        self.assertEqual(trade.financial_amount, Decimal("2613755.25"))

        # Verificação do produto exato: 2500 * 1045.5021 == 2613755.25
        calc = trade.quantity * trade.unit_price
        self.assertEqual(calc, Decimal("2613755.250000"))

    def test_asset_maturity_before_issue_fails(self) -> None:
        """Garante rejeição de ativo com vencimento anterior ou igual à emissão."""
        with self.assertRaises(NoMeValidationError) as ctx:
            FixedIncomeAsset(
                asset_code="CDB-FAIL",
                instrument_type=InstrumentType.CDB,
                issuer_cnpj="60701190000104",
                issuer_name="BANCO TESTE",
                issue_date=date(2026, 11, 3),
                maturity_date=date(2025, 11, 3),  # Passado!
                indexer=IndexerType.DI,
            )
        self.assertIn("deve ser posterior à data de emissão", str(ctx.exception))

    def test_financial_conservation_invariance_violation(self) -> None:
        """Garante que divergência entre Financeiro e Qtd * PU levanta NoMeValidationError."""
        buyer = PartyParticipant(participant_id="00001050", custody_account=1001)
        seller = PartyParticipant(participant_id="00002050", custody_account=2001)
        asset = FixedIncomeAsset(
            asset_code="CDB-TEST",
            instrument_type=InstrumentType.CDB,
            issuer_cnpj="60701190000104",
            issuer_name="BANCO TESTE",
            issue_date=date(2026, 11, 1),
            maturity_date=date(2027, 11, 1),
            indexer=IndexerType.DI,
        )

        with self.assertRaises(NoMeValidationError) as ctx:
            NoMeTradeRegistration(
                trade_id="TRD-BAD-FIN",
                trade_date=date(2026, 11, 3),
                settlement_date=date(2026, 11, 3),
                settlement_type=SettlementType.GROSS_STR,
                match_status=TradeMatchStatus.PENDING_MATCH,
                asset=asset,
                quantity=Decimal("100"),
                unit_price=Decimal("1000.00"),
                financial_amount=Decimal("150000.00"),  # Deveria ser 100.000,00!
                buyer=buyer,
                seller=seller,
            )
        self.assertIn("Violação da lei de conservação financeira", str(ctx.exception))

    def test_self_trade_same_account_fails(self) -> None:
        """Garante rejeição de trade entre a mesma instituição e mesma conta de custódia."""
        buyer = PartyParticipant(participant_id="00001050", custody_account=1001)
        seller = PartyParticipant(participant_id="00001050", custody_account=1001)
        asset = FixedIncomeAsset(
            asset_code="CDB-TEST",
            instrument_type=InstrumentType.CDB,
            issuer_cnpj="60701190000104",
            issuer_name="BANCO TESTE",
            issue_date=date(2026, 11, 1),
            maturity_date=date(2027, 11, 1),
            indexer=IndexerType.DI,
        )

        with self.assertRaises(NoMeValidationError) as ctx:
            NoMeTradeRegistration(
                trade_id="TRD-SELF",
                trade_date=date(2026, 11, 3),
                settlement_date=date(2026, 11, 3),
                settlement_type=SettlementType.GROSS_STR,
                match_status=TradeMatchStatus.PENDING_MATCH,
                asset=asset,
                quantity=Decimal("10"),
                unit_price=Decimal("1000.00"),
                financial_amount=Decimal("10000.00"),
                buyer=buyer,
                seller=seller,
            )
        self.assertIn("conta compradora e vendedora são idênticas", str(ctx.exception))


class TestBilateralTradeMatching(unittest.TestCase):
    """Testes do motor de Batimento Bilateral (Matching Engine) NoMe."""

    def setUp(self) -> None:
        self.asset = FixedIncomeAsset(
            asset_code="DEB-SUZA-203105",
            instrument_type=InstrumentType.DEBENTURE,
            issuer_cnpj="16404287000155",
            issuer_name="SUZANO S.A.",
            issue_date=date(2025, 5, 10),
            maturity_date=date(2031, 5, 10),
            indexer=IndexerType.IPCA,
            rate_spread=Decimal("6.10"),
        )
        self.buyer_party = PartyParticipant(participant_id="00001050", custody_account=105001)
        self.seller_party = PartyParticipant(participant_id="00000888", custody_account=888001)

    def test_bilateral_matching_success(self) -> None:
        """Verifica casamento bem-sucedido de duas intenções com parâmetros idênticos."""
        buyer_trade = NoMeTradeRegistration(
            trade_id="BUY-001",
            trade_date=date(2026, 11, 3),
            settlement_date=date(2026, 11, 4),
            settlement_type=SettlementType.GROSS_STR,
            match_status=TradeMatchStatus.PENDING_MATCH,
            asset=self.asset,
            quantity=Decimal("1000"),
            unit_price=Decimal("1020.50"),
            financial_amount=Decimal("1020500.00"),
            buyer=self.buyer_party,
            seller=self.seller_party,
        )

        seller_trade = NoMeTradeRegistration(
            trade_id="SELL-001",
            trade_date=date(2026, 11, 3),
            settlement_date=date(2026, 11, 4),
            settlement_type=SettlementType.GROSS_STR,
            match_status=TradeMatchStatus.PENDING_MATCH,
            asset=self.asset,
            quantity=Decimal("1000"),
            unit_price=Decimal("1020.50"),
            financial_amount=Decimal("1020500.00"),
            buyer=self.buyer_party,
            seller=self.seller_party,
        )

        matched, reasons = match_bilateral_trades(buyer_trade, seller_trade)
        self.assertTrue(matched)
        self.assertEqual(len(reasons), 0)
        self.assertEqual(buyer_trade.match_status, TradeMatchStatus.MATCHED)
        self.assertEqual(seller_trade.match_status, TradeMatchStatus.MATCHED)

    def test_bilateral_matching_price_mismatch(self) -> None:
        """Verifica rejeição quando a ponta vendedora exige PU superior à compradora."""
        buyer_trade = NoMeTradeRegistration(
            trade_id="BUY-002",
            trade_date=date(2026, 11, 3),
            settlement_date=date(2026, 11, 4),
            settlement_type=SettlementType.GROSS_STR,
            match_status=TradeMatchStatus.PENDING_MATCH,
            asset=self.asset,
            quantity=Decimal("1000"),
            unit_price=Decimal("1020.00"),
            financial_amount=Decimal("1020000.00"),
            buyer=self.buyer_party,
            seller=self.seller_party,
        )

        seller_trade = NoMeTradeRegistration(
            trade_id="SELL-002",
            trade_date=date(2026, 11, 3),
            settlement_date=date(2026, 11, 4),
            settlement_type=SettlementType.GROSS_STR,
            match_status=TradeMatchStatus.PENDING_MATCH,
            asset=self.asset,
            quantity=Decimal("1000"),
            unit_price=Decimal("1025.00"),  # PU divergente!
            financial_amount=Decimal("1025000.00"),
            buyer=self.buyer_party,
            seller=self.seller_party,
        )

        matched, reasons = match_bilateral_trades(buyer_trade, seller_trade)
        self.assertFalse(matched)
        self.assertTrue(any("Divergência de Preço Unitário" in r for r in reasons))
        self.assertEqual(buyer_trade.match_status, TradeMatchStatus.REJECTED)
        self.assertEqual(seller_trade.match_status, TradeMatchStatus.REJECTED)

    def test_bilateral_matching_quantity_and_date_mismatch(self) -> None:
        """Verifica detecção de múltiplas causas de divergência simultaneamente."""
        buyer_trade = NoMeTradeRegistration(
            trade_id="BUY-003",
            trade_date=date(2026, 11, 3),
            settlement_date=date(2026, 11, 4),
            settlement_type=SettlementType.GROSS_STR,
            match_status=TradeMatchStatus.PENDING_MATCH,
            asset=self.asset,
            quantity=Decimal("1000"),
            unit_price=Decimal("1020.00"),
            financial_amount=Decimal("1020000.00"),
            buyer=self.buyer_party,
            seller=self.seller_party,
        )

        seller_trade = NoMeTradeRegistration(
            trade_id="SELL-003",
            trade_date=date(2026, 11, 3),
            settlement_date=date(2026, 11, 5),  # Data liquidação diferente!
            settlement_type=SettlementType.GROSS_STR,
            match_status=TradeMatchStatus.PENDING_MATCH,
            asset=self.asset,
            quantity=Decimal("800"),  # Quantidade diferente!
            unit_price=Decimal("1020.00"),
            financial_amount=Decimal("816000.00"),
            buyer=self.buyer_party,
            seller=self.seller_party,
        )

        matched, reasons = match_bilateral_trades(buyer_trade, seller_trade)
        self.assertFalse(matched)
        self.assertEqual(len(reasons), 2)
        self.assertTrue(any("Divergência de Quantidade" in r for r in reasons))
        self.assertTrue(any("Divergência de Data de Liquidação" in r for r in reasons))


class TestNoMeReconciliation(unittest.TestCase):
    """Testes do motor de conciliação de balcão (FL-019)."""

    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.statement_path = self.fixtures_dir / "nome_statement_valid.json"
        self.statement = parse_nome_statement_json(self.statement_path)

    def test_statement_parsing_and_integrity(self) -> None:
        """Valida parsing estrutural do extrato NoMe e totalizadores."""
        self.assertEqual(self.statement.statement_id, "EXT-NOME-20261103-00001050")
        self.assertEqual(self.statement.participant_id, "00001050")
        self.assertEqual(self.statement.reference_date, date(2026, 11, 3))
        self.assertEqual(self.statement.total_records, 4)
        self.assertEqual(self.statement.total_financial_amount, Decimal("9922365.25"))
        self.assertEqual(len(self.statement.positions), 4)

    def test_reconciliation_perfect_match_100_percent(self) -> None:
        """Verifica conciliação com 100% de casamento entre livro interno e NoMe."""
        # Cria exatamente o livro interno espelhado
        internal_positions = [
            InternalBalcaoPosition(
                custody_account=105001,
                asset_code="CDB-ITAU-202811",
                total_quantity=Decimal("5000"),
                unit_price=Decimal("1000.00000000"),
                financial_amount=Decimal("5000000.00"),
            ),
            InternalBalcaoPosition(
                custody_account=105002,
                asset_code="DEB-VALE-203012",
                total_quantity=Decimal("2500"),
                unit_price=Decimal("1045.50210000"),
                financial_amount=Decimal("2613755.25"),
            ),
            InternalBalcaoPosition(
                custody_account=105001,
                asset_code="LCI-BBDC-202705",
                total_quantity=Decimal("1400"),
                unit_price=Decimal("1020.15000000"),
                financial_amount=Decimal("1428210.00"),
            ),
            InternalBalcaoPosition(
                custody_account=105003,
                asset_code="CRI-KINE-203208",
                total_quantity=Decimal("800"),
                unit_price=Decimal("1100.50000000"),
                financial_amount=Decimal("880400.00"),
            ),
        ]

        report = reconcile_nome_positions(
            internal_positions=internal_positions,
            statement=self.statement,
        )

        self.assertTrue(report.is_clean)
        self.assertEqual(report.total_breaks, 0)
        self.assertEqual(report.total_matched, 4)
        self.assertEqual(report.summary["breaks_count"], 0)
        self.assertEqual(report.summary["matched_count"], 4)

    def test_reconciliation_quantity_mismatch_break(self) -> None:
        """Verifica detecção determinística de QUANTITY_MISMATCH."""
        internal_positions = [
            # Posição com quantidade menor no livro interno (4800 vs 5000)
            {
                "custody_account": 105001,
                "asset_code": "CDB-ITAU-202811",
                "quantity": 4800,
                "unit_price": "1000.00000000",
            },
            {
                "custody_account": 105002,
                "asset_code": "DEB-VALE-203012",
                "quantity": 2500,
                "unit_price": "1045.50210000",
            },
            {
                "custody_account": 105001,
                "asset_code": "LCI-BBDC-202705",
                "quantity": 1400,
                "unit_price": "1020.15000000",
            },
            {
                "custody_account": 105003,
                "asset_code": "CRI-KINE-203208",
                "quantity": 800,
                "unit_price": "1100.50000000",
            },
        ]

        report = reconcile_nome_positions(internal_positions, self.statement)

        self.assertFalse(report.is_clean)
        self.assertEqual(report.total_matched, 3)
        self.assertEqual(report.total_breaks, 1)

        brk = report.breaks[0]
        self.assertEqual(brk.break_type, NoMeBreakType.QUANTITY_MISMATCH)
        self.assertEqual(brk.asset_code, "CDB-ITAU-202811")
        self.assertEqual(brk.internal_quantity, Decimal("4800"))
        self.assertEqual(brk.nome_quantity, Decimal("5000"))
        self.assertEqual(brk.quantity_difference, Decimal("200"))

    def test_reconciliation_pu_mismatch_break(self) -> None:
        """Verifica detecção determinística de PU_MISMATCH quando ultrapassa a tolerância."""
        internal_positions = [
            {
                "custody_account": 105001,
                "asset_code": "CDB-ITAU-202811",
                "quantity": 5000,
                "unit_price": "1000.00000000",
            },
            # PU divergente no livro interno (1040.00 vs 1045.50210000)
            {
                "custody_account": 105002,
                "asset_code": "DEB-VALE-203012",
                "quantity": 2500,
                "unit_price": "1040.00000000",
            },
            {
                "custody_account": 105001,
                "asset_code": "LCI-BBDC-202705",
                "quantity": 1400,
                "unit_price": "1020.15000000",
            },
            {
                "custody_account": 105003,
                "asset_code": "CRI-KINE-203208",
                "quantity": 800,
                "unit_price": "1100.50000000",
            },
        ]

        reconciler = NoMeReconciler(pu_tolerance=Decimal("0.0001"))
        report = reconciler.reconcile(internal_positions, self.statement)

        self.assertFalse(report.is_clean)
        self.assertEqual(report.total_matched, 3)
        self.assertEqual(report.total_breaks, 1)

        brk = report.breaks[0]
        self.assertEqual(brk.break_type, NoMeBreakType.PU_MISMATCH)
        self.assertEqual(brk.asset_code, "DEB-VALE-203012")
        self.assertEqual(brk.internal_unit_price, Decimal("1040.00000000"))
        self.assertEqual(brk.nome_unit_price, Decimal("1045.50210000"))
        self.assertEqual(brk.price_difference, Decimal("5.50210000"))

    def test_reconciliation_ghost_and_missing_assets(self) -> None:
        """Verifica simultaneamente quebras do tipo GHOST_IN_NOME e MISSING_IN_NOME."""
        # Livro interno omitindo CRI-KINE-203208 (será GHOST no NoMe)
        # e incluindo ativo que não está no extrato NoMe (será MISSING no NoMe)
        internal_positions = [
            {
                "custody_account": 105001,
                "asset_code": "CDB-ITAU-202811",
                "quantity": 5000,
                "unit_price": "1000.00000000",
            },
            {
                "custody_account": 105002,
                "asset_code": "DEB-VALE-203012",
                "quantity": 2500,
                "unit_price": "1045.50210000",
            },
            {
                "custody_account": 105001,
                "asset_code": "LCI-BBDC-202705",
                "quantity": 1400,
                "unit_price": "1020.15000000",
            },
            # Ativo exclusivo interno:
            {
                "custody_account": 105004,
                "asset_code": "LCA-BBAS-202801",
                "quantity": 600,
                "unit_price": "1005.00000000",
            },
        ]

        report = reconcile_nome_positions(internal_positions, self.statement)

        self.assertFalse(report.is_clean)
        self.assertEqual(report.total_matched, 3)
        self.assertEqual(report.total_breaks, 2)

        break_types = {b.break_type for b in report.breaks}
        self.assertIn(NoMeBreakType.GHOST_IN_NOME, break_types)
        self.assertIn(NoMeBreakType.MISSING_IN_NOME, break_types)

        ghost = next(b for b in report.breaks if b.break_type == NoMeBreakType.GHOST_IN_NOME)
        self.assertEqual(ghost.asset_code, "CRI-KINE-203208")
        self.assertEqual(ghost.custody_account, 105003)

        missing = next(b for b in report.breaks if b.break_type == NoMeBreakType.MISSING_IN_NOME)
        self.assertEqual(missing.asset_code, "LCA-BBAS-202801")
        self.assertEqual(missing.custody_account, 105004)


if __name__ == "__main__":
    unittest.main()
