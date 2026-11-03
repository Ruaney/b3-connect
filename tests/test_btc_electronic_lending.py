"""Testes automatizados do Motor de Empréstimo Eletrônico de Ativos (PUMA BTC - FL-025)."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.btc_electronic_lending import (
    BtcMatchingEngine,
    LendingMatch,
    LendingOrder,
    LendingSide,
    RenewalType,
)
from src.imbarq011 import parse_imbarq011

FIXTURES_DIR = REPO_ROOT / "fixtures"


class TestBtcElectronicLending(unittest.TestCase):
    """Bateria de testes unitários do motor de aluguel eletrônico de ativos."""

    def setUp(self) -> None:
        self.trade_date = date(2026, 11, 3)
        self.engine = BtcMatchingEngine(trade_date=self.trade_date, contract_seq_start=202600001)

    def test_exact_match_compatible_rates(self) -> None:
        """Teste de matching exato com casamento de taxas compatíveis."""
        # Doador oferta 10.000 PETR4 a 3.50% a.a.
        lender_order = LendingOrder(
            order_id="ORD-LEND-001",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            side=LendingSide.LENDER,
            quantity=10000,
            rate=Decimal("3.5000"),
            participant_id=105,
            account=123456,
            min_term_days=5,
            max_term_days=30,
            renewal_type=RenewalType.AUTOMATIC,
            reversibility="T",
            base_price=Decimal("38.50"),
        )
        matches_lender = self.engine.submit_order(lender_order)
        self.assertEqual(len(matches_lender), 0)
        self.assertEqual(lender_order.leaves_qty, 10000)
        self.assertEqual(lender_order.cum_qty, 0)

        # Tomador envia ordem agressora de exatamente 10.000 PETR4 aceitando pagar até 3.60% a.a.
        borrower_order = LendingOrder(
            order_id="ORD-BORR-001",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            side=LendingSide.BORROWER,
            quantity=10000,
            rate=Decimal("3.6000"),
            participant_id=210,
            account=987654,
            min_term_days=5,
            max_term_days=30,
            renewal_type=RenewalType.AUTOMATIC,
            reversibility="T",
            base_price=Decimal("38.50"),
        )
        matches = self.engine.submit_order(borrower_order)

        # Verificação do matching
        self.assertEqual(len(matches), 1)
        match = matches[0]

        # Verifica dados do match
        self.assertEqual(match.symbol, "PETR4")
        self.assertEqual(match.isin, "BRPETRACNPR6")
        self.assertEqual(match.quantity, 10000)
        self.assertEqual(match.rate, Decimal("3.5000"))  # Regra de taxa passiva (doador descansado)
        self.assertEqual(match.contract_number, 202600001)
        self.assertEqual(match.lender_participant_id, 105)
        self.assertEqual(match.borrower_participant_id, 210)
        self.assertEqual(match.term_days, 5)
        self.assertEqual(match.open_date, self.trade_date)
        self.assertEqual(match.maturity_date, self.trade_date + timedelta(days=5))
        self.assertEqual(match.base_price, Decimal("38.50"))
        self.assertEqual(match.contract_value, Decimal("385000.00"))  # 10000 * 38.50

        # Verifica estado das ordens
        self.assertTrue(lender_order.is_filled)
        self.assertTrue(borrower_order.is_filled)
        self.assertEqual(lender_order.leaves_qty, 0)
        self.assertEqual(borrower_order.leaves_qty, 0)
        self.assertEqual(lender_order.cum_qty, 10000)
        self.assertEqual(borrower_order.cum_qty, 10000)

        # O livro de ofertas deve estar vazio para PETR4
        book = self.engine.get_book("PETR4")
        self.assertEqual(book["total_lender_qty"], 0)
        self.assertEqual(book["total_borrower_qty"], 0)

    def test_partial_fill_lending_lot(self) -> None:
        """Teste de atendimento parcial de lote de aluguel."""
        # Doador oferta 10.000 VALE3 a 2.80% a.a.
        lender = LendingOrder(
            order_id="ORD-VALE-L1",
            symbol="VALE3",
            isin="BRVALEACNOR0",
            side=LendingSide.LENDER,
            quantity=10000,
            rate=Decimal("2.8000"),
            participant_id=105,
            account=1001,
            min_term_days=10,
            max_term_days=45,
            base_price=Decimal("62.50"),
        )
        self.engine.submit_order(lender)

        # Tomador 1 agride demandando 4.000 ações a 2.80% a.a.
        borrower_1 = LendingOrder(
            order_id="ORD-VALE-B1",
            symbol="VALE3",
            isin="BRVALEACNOR0",
            side=LendingSide.BORROWER,
            quantity=4000,
            rate=Decimal("2.8000"),
            participant_id=210,
            account=2001,
            min_term_days=10,
            max_term_days=30,
            base_price=Decimal("62.50"),
        )
        matches_1 = self.engine.submit_order(borrower_1)

        self.assertEqual(len(matches_1), 1)
        self.assertEqual(matches_1[0].quantity, 4000)
        self.assertEqual(matches_1[0].contract_value, Decimal("250000.00"))  # 4000 * 62.50
        self.assertTrue(borrower_1.is_filled)
        self.assertTrue(lender.is_partial)
        self.assertEqual(lender.leaves_qty, 6000)
        self.assertEqual(lender.cum_qty, 4000)

        # O livro de ofertas deve conter 6.000 ações remanescentes do doador
        book_mid = self.engine.get_book("VALE3")
        self.assertEqual(book_mid["total_lender_qty"], 6000)
        self.assertEqual(len(book_mid["lenders"]), 1)

        # Tomador 2 agride demandando as 6.000 ações restantes a 3.00% a.a.
        borrower_2 = LendingOrder(
            order_id="ORD-VALE-B2",
            symbol="VALE3",
            isin="BRVALEACNOR0",
            side=LendingSide.BORROWER,
            quantity=6000,
            rate=Decimal("3.0000"),
            participant_id=300,
            account=3001,
            min_term_days=10,
            max_term_days=30,
            base_price=Decimal("62.50"),
        )
        matches_2 = self.engine.submit_order(borrower_2)

        self.assertEqual(len(matches_2), 1)
        self.assertEqual(matches_2[0].quantity, 6000)
        self.assertEqual(matches_2[0].rate, Decimal("2.8000"))  # Preço passivo do doador
        self.assertEqual(matches_2[0].contract_value, Decimal("375000.00"))  # 6000 * 62.50
        self.assertTrue(lender.is_filled)
        self.assertTrue(borrower_2.is_filled)
        self.assertEqual(lender.leaves_qty, 0)
        self.assertEqual(lender.cum_qty, 10000)

        # Livro de VALE3 agora está zerado
        book_end = self.engine.get_book("VALE3")
        self.assertEqual(book_end["total_lender_qty"], 0)

    def test_rejection_negative_rates_and_fractional_quantities(self) -> None:
        """Teste de rejeição de taxas negativas ou quantidade fracionária inválida."""
        # Rejeição de taxa negativa
        with self.assertRaises(ValueError) as ctx_rate:
            LendingOrder(
                order_id="BAD-RATE-1",
                symbol="PETR4",
                isin="BRPETRACNPR6",
                side=LendingSide.LENDER,
                quantity=1000,
                rate=Decimal("-0.0100"),
                participant_id=105,
                account=123456,
            )
        self.assertIn("negativa", str(ctx_rate.exception).lower())

        with self.assertRaises(ValueError) as ctx_rate_float:
            LendingOrder(
                order_id="BAD-RATE-2",
                symbol="PETR4",
                isin="BRPETRACNPR6",
                side=LendingSide.BORROWER,
                quantity=1000,
                rate=-1.5,
                participant_id=105,
                account=123456,
            )
        self.assertIn("negativa", str(ctx_rate_float.exception).lower())

        # Rejeição de quantidade fracionária
        with self.assertRaises(ValueError) as ctx_frac:
            LendingOrder(
                order_id="BAD-QTY-1",
                symbol="PETR4",
                isin="BRPETRACNPR6",
                side=LendingSide.LENDER,
                quantity=100.5,  # float fracionário
                rate=Decimal("3.5000"),
                participant_id=105,
                account=123456,
            )
        self.assertIn("fracionária", str(ctx_frac.exception).lower())

        # Rejeição de quantidade zero ou negativa
        with self.assertRaises(ValueError) as ctx_zero:
            LendingOrder(
                order_id="BAD-QTY-0",
                symbol="PETR4",
                isin="BRPETRACNPR6",
                side=LendingSide.LENDER,
                quantity=0,
                rate=Decimal("3.5000"),
                participant_id=105,
                account=123456,
            )
        self.assertIn("positiva", str(ctx_zero.exception).lower())

        with self.assertRaises(ValueError) as ctx_neg:
            LendingOrder(
                order_id="BAD-QTY-NEG",
                symbol="PETR4",
                isin="BRPETRACNPR6",
                side=LendingSide.LENDER,
                quantity=-500,
                rate=Decimal("3.5000"),
                participant_id=105,
                account=123456,
            )
        self.assertIn("positiva", str(ctx_neg.exception).lower())

        # Rejeição de prazos inconsistentes
        with self.assertRaises(ValueError) as ctx_term:
            LendingOrder(
                order_id="BAD-TERM",
                symbol="PETR4",
                isin="BRPETRACNPR6",
                side=LendingSide.LENDER,
                quantity=1000,
                rate=Decimal("3.5000"),
                participant_id=105,
                account=123456,
                min_term_days=30,
                max_term_days=10,  # min > max
            )
        self.assertIn("maior que o prazo máximo", str(ctx_term.exception).lower())

    def test_price_time_priority(self) -> None:
        """Valida que o Doador com menor taxa exigida tem prioridade estrita sobre taxas maiores."""
        # Doador 1: 5.000 a 3.50%
        d1 = LendingOrder(
            order_id="D1",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            side=LendingSide.LENDER,
            quantity=5000,
            rate=Decimal("3.5000"),
            participant_id=105,
            account=101,
            timestamp=datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc),
        )
        # Doador 2: 5.000 a 3.20% (mais competitivo, embora tenha chegado depois)
        d2 = LendingOrder(
            order_id="D2",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            side=LendingSide.LENDER,
            quantity=5000,
            rate=Decimal("3.2000"),
            participant_id=210,
            account=202,
            timestamp=datetime(2026, 11, 3, 10, 1, 0, tzinfo=timezone.utc),
        )
        self.engine.submit_order(d1)
        self.engine.submit_order(d2)

        # Tomador envia ordem de 5.000 a 3.60%
        t = LendingOrder(
            order_id="T1",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            side=LendingSide.BORROWER,
            quantity=5000,
            rate=Decimal("3.6000"),
            participant_id=300,
            account=303,
            timestamp=datetime(2026, 11, 3, 10, 5, 0, tzinfo=timezone.utc),
        )
        matches = self.engine.submit_order(t)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].lender_order_id, "D2")  # Casou com D2 (melhor taxa: 3.20%)
        self.assertEqual(matches[0].rate, Decimal("3.2000"))
        self.assertTrue(d2.is_filled)
        self.assertFalse(d1.is_filled)
        self.assertEqual(d1.leaves_qty, 5000)

    def test_passive_price_rule_both_sides(self) -> None:
        """Verifica a regra de preço passivo (ordem descansada determina a taxa)."""
        # Caso 1: Doador passivo a 3.00%, Tomador agressivo a 3.80% -> Match a 3.00%
        engine1 = BtcMatchingEngine(trade_date=self.trade_date)
        d = LendingOrder(
            order_id="D",
            symbol="B3SA3",
            isin="BRB3SAACNOR6",
            side=LendingSide.LENDER,
            quantity=1000,
            rate=Decimal("3.0000"),
            participant_id=105,
            account=1,
        )
        engine1.submit_order(d)
        t = LendingOrder(
            order_id="T",
            symbol="B3SA3",
            isin="BRB3SAACNOR6",
            side=LendingSide.BORROWER,
            quantity=1000,
            rate=Decimal("3.8000"),
            participant_id=210,
            account=2,
        )
        m1 = engine1.submit_order(t)
        self.assertEqual(m1[0].rate, Decimal("3.0000"))

        # Caso 2: Tomador passivo a 4.00%, Doador agressivo a 3.10% -> Match a 4.00%
        engine2 = BtcMatchingEngine(trade_date=self.trade_date)
        t_passive = LendingOrder(
            order_id="TP",
            symbol="B3SA3",
            isin="BRB3SAACNOR6",
            side=LendingSide.BORROWER,
            quantity=1000,
            rate=Decimal("4.0000"),
            participant_id=210,
            account=2,
        )
        engine2.submit_order(t_passive)
        d_aggr = LendingOrder(
            order_id="DA",
            symbol="B3SA3",
            isin="BRB3SAACNOR6",
            side=LendingSide.LENDER,
            quantity=1000,
            rate=Decimal("3.1000"),
            participant_id=105,
            account=1,
        )
        m2 = engine2.submit_order(d_aggr)
        self.assertEqual(m2[0].rate, Decimal("4.0000"))

    def test_ticker_isolation(self) -> None:
        """Garante que ordens de ativos distintos (PETR4 vs VALE3) nunca cruzam."""
        d_petr = LendingOrder(
            order_id="D-PETR",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            side=LendingSide.LENDER,
            quantity=5000,
            rate=Decimal("2.5000"),
            participant_id=105,
            account=1,
        )
        t_vale = LendingOrder(
            order_id="T-VALE",
            symbol="VALE3",
            isin="BRVALEACNOR0",
            side=LendingSide.BORROWER,
            quantity=5000,
            rate=Decimal("5.0000"),  # Taxa superior, mas ativo diferente
            participant_id=210,
            account=2,
        )
        self.engine.submit_order(d_petr)
        matches = self.engine.submit_order(t_vale)
        self.assertEqual(len(matches), 0)
        self.assertEqual(d_petr.leaves_qty, 5000)
        self.assertEqual(t_vale.leaves_qty, 5000)

    def test_incompatible_rate_rests_in_book(self) -> None:
        """Doador pede taxa superior à que o Tomador aceita pagar: nenhuma execução."""
        d = LendingOrder(
            order_id="D-HIGH",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            side=LendingSide.LENDER,
            quantity=5000,
            rate=Decimal("4.5000"),
            participant_id=105,
            account=1,
        )
        t = LendingOrder(
            order_id="T-LOW",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            side=LendingSide.BORROWER,
            quantity=5000,
            rate=Decimal("3.0000"),
            participant_id=210,
            account=2,
        )
        self.engine.submit_order(d)
        matches = self.engine.submit_order(t)
        self.assertEqual(len(matches), 0)
        book = self.engine.get_book("PETR4")
        self.assertEqual(book["total_lender_qty"], 5000)
        self.assertEqual(book["total_borrower_qty"], 5000)

    def test_term_incompatibility_no_match(self) -> None:
        """Prazos sem sobreposição impedem a celebração do contrato de aluguel."""
        d = LendingOrder(
            order_id="D-LONG",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            side=LendingSide.LENDER,
            quantity=1000,
            rate=Decimal("3.0000"),
            participant_id=105,
            account=1,
            min_term_days=60,
            max_term_days=90,
        )
        t = LendingOrder(
            order_id="T-SHORT",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            side=LendingSide.BORROWER,
            quantity=1000,
            rate=Decimal("3.5000"),
            participant_id=210,
            account=2,
            min_term_days=5,
            max_term_days=20,  # [5, 20] vs [60, 90] não têm interseção
        )
        self.engine.submit_order(d)
        matches = self.engine.submit_order(t)
        self.assertEqual(len(matches), 0)

    def test_cancel_order(self) -> None:
        """Testa o cancelamento de uma ordem em aberto no livro."""
        ord_obj = LendingOrder(
            order_id="TO-CANCEL",
            symbol="VALE3",
            isin="BRVALEACNOR0",
            side=LendingSide.LENDER,
            quantity=2000,
            rate=Decimal("3.0000"),
            participant_id=105,
            account=1,
        )
        self.engine.submit_order(ord_obj)
        self.assertEqual(self.engine.get_book("VALE3")["total_lender_qty"], 2000)

        # Cancela ordem
        cancelled = self.engine.cancel_order("TO-CANCEL")
        self.assertTrue(cancelled)
        self.assertEqual(self.engine.get_book("VALE3")["total_lender_qty"], 0)

        # Cancelar novamente retorna False
        self.assertFalse(self.engine.cancel_order("TO-CANCEL"))

    def test_load_orderbook_fixture(self) -> None:
        """Testa o carregamento e cruzamento do livro de ofertas a partir de fixture JSON."""
        fixture_file = FIXTURES_DIR / "btc_lending_orderbook.json"
        self.assertTrue(fixture_file.exists(), f"Fixture não encontrada: {fixture_file}")

        matches = self.engine.load_fixtures_from_json(fixture_file)

        # Devem ocorrer 3 matches no total:
        # PETR4:
        # - ORD-PETR4-B1 (12.000) cruza com ORD-PETR4-L1 (10.000 @ 3.50%)
        # - ORD-PETR4-B1 cruza com ORD-PETR4-L2 (2.000 @ 3.75%)
        # VALE3:
        # - ORD-VALE3-B1 (5.000) cruza com ORD-VALE3-L1 (5.000 @ 2.80%)
        self.assertEqual(len(matches), 3)

        # Verificação do match 1 (PETR4 com L1)
        m1 = matches[0]
        self.assertEqual(m1.symbol, "PETR4")
        self.assertEqual(m1.lender_order_id, "ORD-PETR4-L1")
        self.assertEqual(m1.borrower_order_id, "ORD-PETR4-B1")
        self.assertEqual(m1.quantity, 10000)
        self.assertEqual(m1.rate, Decimal("3.5000"))

        # Verificação do match 2 (PETR4 com L2 parcial)
        m2 = matches[1]
        self.assertEqual(m2.symbol, "PETR4")
        self.assertEqual(m2.lender_order_id, "ORD-PETR4-L2")
        self.assertEqual(m2.borrower_order_id, "ORD-PETR4-B1")
        self.assertEqual(m2.quantity, 2000)
        self.assertEqual(m2.rate, Decimal("3.7500"))

        # Verificação do match 3 (VALE3 com L1)
        m3 = matches[2]
        self.assertEqual(m3.symbol, "VALE3")
        self.assertEqual(m3.lender_order_id, "ORD-VALE3-L1")
        self.assertEqual(m3.borrower_order_id, "ORD-VALE3-B1")
        self.assertEqual(m3.quantity, 5000)
        self.assertEqual(m3.rate, Decimal("2.8000"))

        # Livro de ofertas residual:
        # PETR4: L2 deve restar com 13.000 ações
        book_petr = self.engine.get_book("PETR4")
        self.assertEqual(book_petr["total_lender_qty"], 13000)
        self.assertEqual(book_petr["total_borrower_qty"], 0)

        # VALE3: L2 deve restar com 8.000 ações (pois pediu 3.20% e tomador ofereceu 2.90%)
        book_vale = self.engine.get_book("VALE3")
        self.assertEqual(book_vale["total_lender_qty"], 8000)
        self.assertEqual(book_vale["total_borrower_qty"], 0)

    def test_imbarq011_record_and_file_generation_roundtrip(self) -> None:
        """Garante que os contratos gerados pelo matching geram arquivo IMBARQ011 100% válido."""
        fixture_file = FIXTURES_DIR / "btc_lending_orderbook.json"
        self.engine.load_fixtures_from_json(fixture_file)

        # Exporta o arquivo IMBARQ011
        imbarq_content = self.engine.export_imbarq011(
            participant_id=105,
            participant_name="CORRETORA ATLAS EXEMPLO S/A",
            seq_remessa=1,
        )

        lines = [line for line in imbarq_content.splitlines() if line]
        # 1 Header + 3 Detalhes + 1 Trailler = 5 linhas
        self.assertEqual(len(lines), 5)

        for i, line in enumerate(lines, start=1):
            self.assertEqual(len(line), 200, f"Linha {i} não possui exatamente 200 colunas")

        # Validação estrita passando o conteúdo gerado diretamente pelo parser IMBARQ011 oficial!
        imbarq_file = parse_imbarq011(imbarq_content)
        self.assertEqual(imbarq_file.header.tipo_registro, "00")
        self.assertEqual(imbarq_file.header.identificador_layout, "IMBARQ011")
        self.assertEqual(imbarq_file.total_contratos, 3)
        self.assertEqual(imbarq_file.trailer.total_registros_arquivo, 5)
        self.assertEqual(imbarq_file.trailer.total_registros_detalhe, 3)
        # Quantidade total: 10.000 + 2.000 + 5.000 = 17.000
        self.assertEqual(imbarq_file.trailer.total_quantidade_alugada, 17000)

        # Checagem dos registros individuais via to_imbarq011_record()
        matches = self.engine.get_matches()
        for idx, m in enumerate(matches, start=2):
            rec = m.to_imbarq011_record(line_number=idx)
            self.assertEqual(rec.tipo_registro, "01")
            self.assertEqual(rec.quantidade_alugada, m.quantity)
            self.assertEqual(rec.taxa_aluguel, m.rate)

    def test_fix_messages_formatting(self) -> None:
        """Testa a geração e formatação de mensagens FIX NewOrderSingle e ExecutionReport."""
        order = LendingOrder(
            order_id="ORD-FIX-01",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            side=LendingSide.BORROWER,
            quantity=5000,
            rate=Decimal("3.5000"),
            participant_id=105,
            account=123456,
            min_term_days=5,
            max_term_days=30,
            renewal_type=RenewalType.AUTOMATIC,
            reversibility="T",
            base_price=Decimal("38.50"),
        )
        fix_nos = order.to_fix_new_order_single(delimiter="|")
        self.assertIn("35=D|", fix_nos)
        self.assertIn("55=PETR4|", fix_nos)
        self.assertIn("54=1|", fix_nos)  # 1 = Borrower
        self.assertIn("9001=3.5000|", fix_nos)
        self.assertIn("9004=1|", fix_nos)  # 1 = AUTOMATIC

        matches = self.engine.submit_order(order)
        # Livro estava vazio, ordem inserida sem match imediato
        self.assertEqual(len(matches), 0)

        # Agora entra doador correspondente
        lender = LendingOrder(
            order_id="ORD-FIX-02",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            side=LendingSide.LENDER,
            quantity=5000,
            rate=Decimal("3.5000"),
            participant_id=210,
            account=654321,
            min_term_days=5,
            max_term_days=30,
            base_price=Decimal("38.50"),
        )
        matches2 = self.engine.submit_order(lender)
        self.assertEqual(len(matches2), 1)

        match = matches2[0]
        exec_lender = match.to_fix_execution_report(for_side=LendingSide.LENDER, delimiter="|")
        self.assertIn("35=8|", exec_lender)
        self.assertIn("54=2|", exec_lender)  # 2 = Lender
        self.assertIn("32=5000|", exec_lender)
        self.assertIn("31=3.5000|", exec_lender)
        self.assertIn(f"9007={match.contract_number}|", exec_lender)

        exec_borrower = match.to_fix_execution_report(for_side=LendingSide.BORROWER, delimiter="|")
        self.assertIn("35=8|", exec_borrower)
        self.assertIn("54=1|", exec_borrower)  # 1 = Borrower


if __name__ == "__main__":
    unittest.main()
