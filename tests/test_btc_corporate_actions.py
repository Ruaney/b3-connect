"""Testes automatizados para Eventos Corporativos em Empréstimo de Ativos BTC (FL-048)."""

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.btc_corporate_actions import (
    BtcAdjustmentReceipt,
    BtcCorporateActionError,
    BtcCorporateActionsEngine,
    BtcEventType,
    BtcLoanContract,
    CorporateEventAnnouncement,
)

FIXTURES_PATH = REPO_ROOT / "fixtures" / "btc_corporate_actions.json"


class TestBtcCorporateActions(unittest.TestCase):
    """Bateria de testes unitários do motor de eventos corporativos em contratos BTC."""

    def setUp(self) -> None:
        self.engine = BtcCorporateActionsEngine()

    def test_cash_dividend_settlement(self) -> None:
        """Valida que o Doador recebe 100% dos dividendos e o Tomador é debitado integralmente (isento de IRRF)."""
        contract = BtcLoanContract(
            contract_number=202600101,
            symbol="PETR4",
            isin="BRPETRACNPR6",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=88,
            borrower_account=20001,
            quantity=10000,
            base_price=Decimal("38.50"),
            open_date=date(2026, 10, 1),
            maturity_date=date(2026, 12, 1),
        )
        self.engine.register_contract(contract)

        event = CorporateEventAnnouncement(
            event_id="EVT-DIV-001",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            event_type=BtcEventType.DIVIDEND,
            record_date=date(2026, 11, 4),
            ex_date=date(2026, 11, 5),
            payment_date=date(2026, 11, 20),
            gross_rate_per_share=Decimal("1.250000"),
            tax_exemption=True,
        )

        receipts = self.engine.process_event(event)
        self.assertEqual(len(receipts), 1)
        r = receipts[0]

        # 10.000 * 1,25 = R$ 12.500,00
        self.assertEqual(r.gross_cash_amount, Decimal("12500.00"))
        self.assertEqual(r.withholding_tax_amount, Decimal("0.00"))
        self.assertEqual(r.net_cash_amount, Decimal("12500.00"))
        self.assertEqual(r.borrower_debit_amount, Decimal("12500.00"))
        self.assertEqual(r.lender_credit_amount, Decimal("12500.00"))
        self.assertEqual(r.tax_compensation_credit, Decimal("0.00"))
        self.assertEqual(r.payment_date, date(2026, 11, 20))
        self.assertTrue(r.value_conserved)

    def test_jcp_withholding_and_tax_compensation(self) -> None:
        """Valida retenção de 15% de IRRF sobre JCP e geração do crédito tributário compensável do Tomador."""
        contract = BtcLoanContract(
            contract_number=202600102,
            symbol="VALE3",
            isin="BRVALEACNOR0",
            lender_participant_id=105,
            lender_account=10002,
            borrower_participant_id=93,
            borrower_account=20002,
            quantity=5000,
            base_price=Decimal("62.00"),
            open_date=date(2026, 10, 15),
            maturity_date=date(2026, 11, 25),
        )
        self.engine.register_contract(contract)

        event = CorporateEventAnnouncement(
            event_id="EVT-JCP-002",
            symbol="VALE3",
            isin="BRVALEACNOR0",
            event_type=BtcEventType.JCP,
            record_date=date(2026, 11, 4),
            ex_date=date(2026, 11, 5),
            payment_date=date(2026, 11, 25),
            gross_rate_per_share=Decimal("2.000000"),
            withholding_tax_rate=Decimal("0.1500"),
        )

        receipts = self.engine.process_event(event)
        self.assertEqual(len(receipts), 1)
        r = receipts[0]

        # 5.000 * 2,00 = R$ 10.000,00 bruto
        self.assertEqual(r.gross_cash_amount, Decimal("10000.00"))
        # IRRF de 15% = R$ 1.500,00
        self.assertEqual(r.withholding_tax_amount, Decimal("1500.00"))
        # Líquido para o Doador = R$ 8.500,00
        self.assertEqual(r.net_cash_amount, Decimal("8500.00"))
        self.assertEqual(r.borrower_debit_amount, Decimal("10000.00"))
        self.assertEqual(r.lender_credit_amount, Decimal("8500.00"))
        # Tomador tem direito ao crédito tributário compensável no valor de R$ 1.500,00
        self.assertEqual(r.tax_compensation_credit, Decimal("1500.00"))

    def test_jcp_tax_exempt_lender(self) -> None:
        """Garante que Doador imune/isento recebe o valor integral do JCP sem desconto de IRRF."""
        contract = BtcLoanContract(
            contract_number=202600108,
            symbol="VALE3",
            isin="BRVALEACNOR0",
            lender_participant_id=105,
            lender_account=99999,
            borrower_participant_id=93,
            borrower_account=20002,
            quantity=1000,
            base_price=Decimal("62.00"),
            open_date=date(2026, 10, 15),
            maturity_date=date(2026, 11, 25),
            is_tax_exempt=True,
        )
        self.engine.register_contract(contract)

        event = CorporateEventAnnouncement(
            event_id="EVT-JCP-002",
            symbol="VALE3",
            isin="BRVALEACNOR0",
            event_type=BtcEventType.JCP,
            record_date=date(2026, 11, 4),
            ex_date=date(2026, 11, 5),
            payment_date=date(2026, 11, 25),
            gross_rate_per_share=Decimal("2.000000"),
            withholding_tax_rate=Decimal("0.1500"),
        )

        receipts = self.engine.process_event(event)
        self.assertEqual(len(receipts), 1)
        r = receipts[0]

        self.assertEqual(r.gross_cash_amount, Decimal("2000.00"))
        self.assertEqual(r.withholding_tax_amount, Decimal("0.00"))
        self.assertEqual(r.net_cash_amount, Decimal("2000.00"))
        self.assertEqual(r.lender_credit_amount, Decimal("2000.00"))
        self.assertEqual(r.tax_compensation_credit, Decimal("0.00"))

    def test_stock_bonus_10_percent_value_conservation(self) -> None:
        """Valida que bonificação de 10% aumenta quantidade e reduz preço conservando o nocional."""
        contract = BtcLoanContract(
            contract_number=202600103,
            symbol="B3SA3",
            isin="BRB3SAACNOR6",
            lender_participant_id=114,
            lender_account=10003,
            borrower_participant_id=88,
            borrower_account=20003,
            quantity=20000,
            base_price=Decimal("11.00"),
            contract_value=Decimal("220000.00"),
            open_date=date(2026, 10, 20),
            maturity_date=date(2026, 11, 30),
        )
        self.engine.register_contract(contract)

        event = CorporateEventAnnouncement(
            event_id="EVT-BON-003",
            symbol="B3SA3",
            isin="BRB3SAACNOR6",
            event_type=BtcEventType.BONUS,
            record_date=date(2026, 11, 4),
            ex_date=date(2026, 11, 5),
            ratio_factor=Decimal("0.1000"),
            percentage=Decimal("10.0000"),
        )

        receipts = self.engine.process_event(event)
        self.assertEqual(len(receipts), 1)
        r = receipts[0]

        # 20.000 * 1,10 = 22.000 ações
        self.assertEqual(r.original_quantity, 20000)
        self.assertEqual(r.adjusted_quantity, 22000)
        self.assertEqual(r.delta_quantity, 2000)
        # Preço ajustado: 11,00 / 1,10 = R$ 10,00
        self.assertEqual(r.original_base_price, Decimal("11.00"))
        self.assertEqual(r.adjusted_base_price, Decimal("10.00"))
        # Valor do contrato rigorosamente conservado
        self.assertEqual(r.original_contract_value, Decimal("220000.00"))
        self.assertEqual(r.adjusted_contract_value, Decimal("220000.00"))
        self.assertTrue(r.value_conserved)

        # Verifica atualização do próprio objeto de contrato
        self.assertEqual(contract.quantity, 22000)
        self.assertEqual(contract.base_price, Decimal("10.00"))
        self.assertEqual(contract.contract_value, Decimal("220000.00"))

    def test_stock_split_1_to_3_value_conservation(self) -> None:
        """Valida que desdobramento 1:3 triplica a quantidade e divide o preço por 3 conservando o nocional."""
        contract = BtcLoanContract(
            contract_number=202600104,
            symbol="WEGE3",
            isin="BRWEGEACNOR0",
            lender_participant_id=114,
            lender_account=10004,
            borrower_participant_id=105,
            borrower_account=20004,
            quantity=15000,
            base_price=Decimal("45.00"),
            contract_value=Decimal("675000.00"),
            open_date=date(2026, 9, 10),
            maturity_date=date(2026, 12, 10),
        )
        self.engine.register_contract(contract)

        event = CorporateEventAnnouncement(
            event_id="EVT-SPL-004",
            symbol="WEGE3",
            isin="BRWEGEACNOR0",
            event_type=BtcEventType.SPLIT,
            record_date=date(2026, 11, 4),
            ex_date=date(2026, 11, 5),
            ratio_from=1,
            ratio_to=3,
            ratio_factor=Decimal("3.0000"),
        )

        receipts = self.engine.process_event(event)
        self.assertEqual(len(receipts), 1)
        r = receipts[0]

        # 15.000 * 3 = 45.000 ações
        self.assertEqual(r.original_quantity, 15000)
        self.assertEqual(r.adjusted_quantity, 45000)
        self.assertEqual(r.delta_quantity, 30000)
        # Preço ajustado: 45,00 / 3 = R$ 15,00
        self.assertEqual(r.original_base_price, Decimal("45.00"))
        self.assertEqual(r.adjusted_base_price, Decimal("15.00"))
        self.assertEqual(r.original_contract_value, Decimal("675000.00"))
        self.assertEqual(r.adjusted_contract_value, Decimal("675000.00"))
        self.assertTrue(r.value_conserved)

        # Verifica atualização no contrato
        self.assertEqual(contract.quantity, 45000)
        self.assertEqual(contract.base_price, Decimal("15.00"))
        self.assertEqual(contract.contract_value, Decimal("675000.00"))

    def test_reverse_split_10_to_1_exact(self) -> None:
        """Valida grupamento de 10:1 exato sem frações residuais."""
        contract = BtcLoanContract(
            contract_number=202600105,
            symbol="MGLU3",
            isin="BRMGLUACNOR2",
            lender_participant_id=88,
            lender_account=10005,
            borrower_participant_id=93,
            borrower_account=20005,
            quantity=100000,
            base_price=Decimal("1.50"),
            contract_value=Decimal("150000.00"),
            open_date=date(2026, 10, 5),
            maturity_date=date(2026, 11, 20),
        )
        self.engine.register_contract(contract)

        event = CorporateEventAnnouncement(
            event_id="EVT-REV-005",
            symbol="MGLU3",
            isin="BRMGLUACNOR2",
            event_type=BtcEventType.REVERSE_SPLIT,
            record_date=date(2026, 11, 4),
            ex_date=date(2026, 11, 5),
            ratio_from=10,
            ratio_to=1,
            ratio_factor=Decimal("0.1000"),
        )

        receipts = self.engine.process_event(event)
        self.assertEqual(len(receipts), 1)
        r = receipts[0]

        # 100.000 / 10 = 10.000 ações
        self.assertEqual(r.original_quantity, 100000)
        self.assertEqual(r.adjusted_quantity, 10000)
        self.assertEqual(r.delta_quantity, -90000)
        # Preço ajustado: 1,50 * 10 = R$ 15,00
        self.assertEqual(r.original_base_price, Decimal("1.50"))
        self.assertEqual(r.adjusted_base_price, Decimal("15.00"))
        self.assertEqual(r.original_contract_value, Decimal("150000.00"))
        self.assertEqual(r.adjusted_contract_value, Decimal("150000.00"))
        self.assertEqual(r.fractional_shares, 0)
        self.assertEqual(r.fraction_cash_settlement, Decimal("0.00"))
        self.assertTrue(r.value_conserved)

        self.assertEqual(contract.quantity, 10000)
        self.assertEqual(contract.base_price, Decimal("15.00"))

    def test_reverse_split_with_fractional_shares_settlement(self) -> None:
        """Valida que grupamento com sobra de frações liquida financeiramente a fração a favor do Doador."""
        contract = BtcLoanContract(
            contract_number=202600106,
            symbol="COGN3",
            isin="BRCOGNACNOR2",
            lender_participant_id=93,
            lender_account=10006,
            borrower_participant_id=114,
            borrower_account=20006,
            quantity=10055,
            base_price=Decimal("2.20"),
            contract_value=Decimal("22121.00"),
            open_date=date(2026, 10, 12),
            maturity_date=date(2026, 11, 28),
        )
        self.engine.register_contract(contract)

        event = CorporateEventAnnouncement(
            event_id="EVT-REV-006",
            symbol="COGN3",
            isin="BRCOGNACNOR2",
            event_type=BtcEventType.REVERSE_SPLIT,
            record_date=date(2026, 11, 4),
            ex_date=date(2026, 11, 5),
            ratio_from=10,
            ratio_to=1,
            ratio_factor=Decimal("0.1000"),
        )

        receipts = self.engine.process_event(event)
        self.assertEqual(len(receipts), 1)
        r = receipts[0]

        # 10.055 // 10 = 1.005 ações inteiras, sobra de 5 frações
        self.assertEqual(r.original_quantity, 10055)
        self.assertEqual(r.adjusted_quantity, 1005)
        self.assertEqual(r.fractional_shares, 5)
        self.assertEqual(r.adjusted_base_price, Decimal("22.00"))
        self.assertEqual(r.adjusted_contract_value, Decimal("22110.00"))
        # 5 frações * 2,20 = R$ 11,00
        self.assertEqual(r.fraction_cash_settlement, Decimal("11.00"))
        self.assertEqual(r.borrower_debit_amount, Decimal("11.00"))
        self.assertEqual(r.lender_credit_amount, Decimal("11.00"))
        # Conservação do valor total: 22.110,00 + 11,00 = 22.121,00
        self.assertEqual(r.adjusted_contract_value + r.fraction_cash_settlement, r.original_contract_value)
        self.assertTrue(r.value_conserved)

    def test_subscription_rights_exercise_vs_market_liquidation(self) -> None:
        """Testa o tratamento de direitos de subscrição nos cenários de exercício e liquidação a mercado."""
        contract = BtcLoanContract(
            contract_number=202600107,
            symbol="ITUB4",
            isin="BRITUBACNPR1",
            lender_participant_id=105,
            lender_account=10007,
            borrower_participant_id=88,
            borrower_account=20007,
            quantity=8000,
            base_price=Decimal("35.00"),
            open_date=date(2026, 10, 1),
            maturity_date=date(2026, 12, 15),
        )
        self.engine.register_contract(contract)

        event = CorporateEventAnnouncement(
            event_id="EVT-SUB-007",
            symbol="ITUB4",
            isin="BRITUBACNPR1",
            event_type=BtcEventType.SUBSCRIPTION_RIGHT,
            record_date=date(2026, 11, 4),
            ex_date=date(2026, 11, 5),
            subscription_ratio=Decimal("0.2000"),
            subscription_price=Decimal("25.00"),
            right_market_value=Decimal("2.40"),
            right_symbol="ITUB1",
        )

        # Cenário 1: Doador NÃO exerce -> Liquidação financeira do valor de mercado
        rcpt_no_exercise = self.engine.calculate_subscription_rights(contract, event, exercise=False)
        self.assertEqual(rcpt_no_exercise.rights_allocated, 1600)  # 8.000 * 0,20
        self.assertFalse(rcpt_no_exercise.rights_exercised)
        # 1.600 * 2,40 = R$ 3.840,00
        self.assertEqual(rcpt_no_exercise.rights_cash_settlement, Decimal("3840.00"))
        self.assertEqual(rcpt_no_exercise.borrower_debit_amount, Decimal("3840.00"))
        self.assertEqual(rcpt_no_exercise.lender_credit_amount, Decimal("3840.00"))

        # Cenário 2: Doador SOLICITA exercício -> Doador aporta preço de emissão
        rcpt_exercise = self.engine.calculate_subscription_rights(contract, event, exercise=True)
        self.assertEqual(rcpt_exercise.rights_allocated, 1600)
        self.assertTrue(rcpt_exercise.rights_exercised)
        # 1.600 * 25,00 = R$ 40.000,00
        self.assertEqual(rcpt_exercise.rights_exercise_cost, Decimal("40000.00"))
        self.assertEqual(rcpt_exercise.rights_cash_settlement, Decimal("0.00"))

    def test_fixture_loading_and_batch_processing(self) -> None:
        """Carrega e valida o processamento completo da fixture oficial btc_corporate_actions.json."""
        self.assertTrue(FIXTURES_PATH.exists(), f"Fixture não encontrada: {FIXTURES_PATH}")

        engine, events = BtcCorporateActionsEngine.from_json_fixture(FIXTURES_PATH)
        self.assertEqual(len(engine.contracts), 7)
        self.assertEqual(len(events), 7)

        # Processamento em lote
        receipts = engine.process_all_events(events)
        self.assertEqual(len(receipts), 7)

        # Resumo do motor
        summary = engine.summarize()
        self.assertTrue(summary["all_conserved"])
        self.assertEqual(summary["total_receipts"], 7)
        self.assertEqual(summary["total_contracts"], 7)
        self.assertEqual(summary["total_events"], 7)

        # Verificação contra os resultados esperados no JSON
        with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
            fixture_data = json.load(f)

        expected = fixture_data["expected_results"]
        receipts_by_contract = {r.contract_number: r for r in receipts}

        # PETR4 Dividendo
        r_petr = receipts_by_contract[202600101]
        self.assertEqual(str(r_petr.gross_cash_amount), expected["202600101"]["gross_cash_amount"])
        self.assertEqual(str(r_petr.net_cash_amount), expected["202600101"]["net_cash_amount"])

        # VALE3 JCP
        r_vale = receipts_by_contract[202600102]
        self.assertEqual(str(r_vale.gross_cash_amount), expected["202600102"]["gross_cash_amount"])
        self.assertEqual(str(r_vale.withholding_tax_amount), expected["202600102"]["withholding_tax_amount"])
        self.assertEqual(str(r_vale.tax_compensation_credit), expected["202600102"]["tax_compensation_credit"])

        # B3SA3 Bonificação
        r_b3sa = receipts_by_contract[202600103]
        self.assertEqual(r_b3sa.adjusted_quantity, expected["202600103"]["adjusted_quantity"])
        self.assertEqual(str(r_b3sa.adjusted_base_price), expected["202600103"]["adjusted_base_price"])

        # WEGE3 Desdobramento
        r_wege = receipts_by_contract[202600104]
        self.assertEqual(r_wege.adjusted_quantity, expected["202600104"]["adjusted_quantity"])
        self.assertEqual(str(r_wege.adjusted_base_price), expected["202600104"]["adjusted_base_price"])

        # MGLU3 Grupamento
        r_mglu = receipts_by_contract[202600105]
        self.assertEqual(r_mglu.adjusted_quantity, expected["202600105"]["adjusted_quantity"])
        self.assertEqual(str(r_mglu.adjusted_base_price), expected["202600105"]["adjusted_base_price"])

        # COGN3 Grupamento com frações
        r_cogn = receipts_by_contract[202600106]
        self.assertEqual(r_cogn.adjusted_quantity, expected["202600106"]["adjusted_quantity"])
        self.assertEqual(r_cogn.fractional_shares, expected["202600106"]["fractional_shares"])
        self.assertEqual(str(r_cogn.fraction_cash_settlement), expected["202600106"]["fraction_cash_settlement"])

        # ITUB4 Subscrição não exercida por padrão
        r_itub = receipts_by_contract[202600107]
        self.assertEqual(r_itub.rights_allocated, expected["202600107"]["rights_allocated"])
        self.assertEqual(str(r_itub.rights_cash_settlement), expected["202600107"]["market_value_settlement"])

    def test_ineligible_contract_handling(self) -> None:
        """Garante que contratos abertos após a data com ou vencidos antes da data ex não são ajustados."""
        # Contrato aberto após a Data Com (Data Com = 2026-11-04, Contrato = 2026-11-05)
        late_contract = BtcLoanContract(
            contract_number=202600999,
            symbol="PETR4",
            isin="BRPETRACNPR6",
            lender_participant_id=105,
            lender_account=10001,
            borrower_participant_id=88,
            borrower_account=20001,
            quantity=5000,
            base_price=Decimal("38.50"),
            open_date=date(2026, 11, 5),  # Posterior à Data Com
            maturity_date=date(2026, 12, 1),
        )
        self.engine.register_contract(late_contract)

        event = CorporateEventAnnouncement(
            event_id="EVT-DIV-001",
            symbol="PETR4",
            isin="BRPETRACNPR6",
            event_type=BtcEventType.DIVIDEND,
            record_date=date(2026, 11, 4),
            ex_date=date(2026, 11, 5),
            gross_rate_per_share=Decimal("1.250000"),
        )

        receipts = self.engine.process_event(event)
        self.assertEqual(len(receipts), 0)

    def test_serialization_roundtrip(self) -> None:
        """Verifica serialização e desserialização completa de modelos para JSON/dict."""
        contract = BtcLoanContract(
            contract_number=202600001,
            symbol="VALE3",
            isin="BRVALEACNOR0",
            lender_participant_id=105,
            lender_account=10002,
            borrower_participant_id=93,
            borrower_account=20002,
            quantity=5000,
            base_price=Decimal("62.00"),
            contract_value=Decimal("310000.00"),
            open_date=date(2026, 10, 15),
            maturity_date=date(2026, 11, 25),
        )
        c_dict = contract.to_dict()
        reconstructed_contract = BtcLoanContract.from_dict(c_dict)
        self.assertEqual(contract.contract_number, reconstructed_contract.contract_number)
        self.assertEqual(contract.quantity, reconstructed_contract.quantity)
        self.assertEqual(contract.base_price, reconstructed_contract.base_price)

        event = CorporateEventAnnouncement(
            event_id="EVT-TEST-001",
            symbol="VALE3",
            isin="BRVALEACNOR0",
            event_type=BtcEventType.JCP,
            record_date=date(2026, 11, 4),
            ex_date=date(2026, 11, 5),
            gross_rate_per_share=Decimal("2.000000"),
        )
        e_dict = event.to_dict()
        reconstructed_event = CorporateEventAnnouncement.from_dict(e_dict)
        self.assertEqual(event.event_id, reconstructed_event.event_id)
        self.assertEqual(event.gross_rate_per_share, reconstructed_event.gross_rate_per_share)


if __name__ == "__main__":
    unittest.main()
