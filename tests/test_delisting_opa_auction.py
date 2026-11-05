"""Testes unitários e verificação formal do Leilão Especial de Deslistagem / OPA (FL-070).

Cobre 100% dos fluxos e regras de negócio:
- Quórum legal estrito de mais de 2/3 das ações habilitadas (Art. 4º-A da Lei 6.404/76).
- Respeito ao piso regulatório de Tag Along (Art. 254-A / Regulamentos B3).
- Resgate compulsório / Squeeze-Out determinístico quando controle >= 95% (Art. 4º, § 5º).
- Rateio proporcional pelo Método dos Maiores Restos (Hamilton / Hare-Niemeyer).
- Prevenção de sobras e quantização de centavos em Decimal.
"""

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.delisting_opa_auction import (
    CorporateGovernanceSegment,
    CustodyNotBlockedError,
    DelistingAllocationLine,
    DelistingAuctionConfig,
    DelistingAuctionNotFoundError,
    DelistingAuctionStatus,
    DelistingAuctionSummary,
    DelistingConservationError,
    DelistingOpaAuctionEngine,
    DelistingOpaError,
    DelistingOpaModality,
    DelistingShareholderOrder,
    DelistingValidationError,
    ShareholderDelistingStance,
    SqueezeOutAction,
    TagAlongFloorViolationError,
    UnregisteredFreeFloatSqueezeOutDeposit,
    _quantize_money,
    _quantize_rate,
    _to_date,
)


class TestDelistingOpaAuction(unittest.TestCase):
    def setUp(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_path = self.fixtures_dir / "delisting_opa_auction.json"

    def test_fixture_loading_and_portfolio_summary(self):
        """Valida a carga da fixture oficial com os 5 cenários e resumo executivo."""
        engine = DelistingOpaAuctionEngine.from_json_fixture(self.fixture_path)
        self.assertEqual(len(engine.auctions), 5)

        summary = engine.summarize()
        self.assertEqual(summary["total_auctions"], 5)
        self.assertTrue(summary["all_conservations_satisfied"])
        self.assertFalse(summary["all_delistings_approved"])  # OPA-002 foi rejeitada por quórum
        self.assertGreater(Decimal(summary["total_financial_disbursed_brl"]), Decimal("0.00"))

        # Confirma serialização em dict
        for s in summary["summaries"]:
            self.assertIn("auction_id", s)
            self.assertIn("allocations", s)

    def test_scenario_01_successful_delisting_without_squeeze_out(self):
        """Cenário 1: Fechamento com sucesso (quórum > 2/3) e free float remanescente > 5%."""
        engine = DelistingOpaAuctionEngine.from_json_fixture(self.fixture_path)
        rep = engine.run_auction("OPA-DELIST-2026-001")

        self.assertEqual(rep.status, DelistingAuctionStatus.HOMOLOGATED_SUCCESS)
        self.assertTrue(rep.delisting_approved)
        self.assertTrue(rep.quorum_achieved)
        self.assertFalse(rep.squeeze_out_eligible)
        self.assertEqual(rep.squeeze_out_status, SqueezeOutAction.NOT_ELIGIBLE)

        # Total habilitado = 1.8M sell + 400k agree + 300k dissent = 2.5M
        self.assertEqual(rep.total_enabled_shares, 2500000)
        self.assertEqual(rep.total_sell_shares, 1800000)
        self.assertEqual(rep.total_agree_shares, 400000)
        self.assertEqual(rep.total_dissent_shares, 300000)
        self.assertEqual(rep.total_concordant_shares, 2200000)

        # 3 * 2.2M (6.6M) > 2 * 2.5M (5.0M) -> Quórum = 88%
        self.assertTrue(rep.concordance_condition_satisfied)

        # Ações aceitas e devolvidas
        self.assertEqual(rep.total_accepted_shares, 1800000)
        self.assertEqual(rep.total_returned_shares, 700000)
        self.assertEqual(rep.post_auction_controller_shares, 8800000)
        self.assertEqual(rep.post_auction_free_float_shares, 1200000)  # 12% > 5%

        # Financeiro: 1.8M * 35.50 = 63,900,000.00
        self.assertEqual(rep.total_financial_tender_brl, Decimal("63900000.00"))
        self.assertEqual(rep.total_financial_disbursed_brl, Decimal("63900000.00"))
        self.assertEqual(rep.total_financial_squeeze_out_brl, Decimal("0.00"))
        self.assertIsNone(rep.unregistered_squeeze_out)

    def test_scenario_02_insufficient_quorum_rejection(self):
        """Cenário 2: Quórum de 2/3 não atingido. Deslistagem rejeitada e 100% devolvido."""
        engine = DelistingOpaAuctionEngine.from_json_fixture(self.fixture_path)
        rep = engine.run_auction("OPA-DELIST-2026-002")

        self.assertEqual(rep.status, DelistingAuctionStatus.REJECTED_INSUFFICIENT_QUORUM)
        self.assertFalse(rep.delisting_approved)
        self.assertFalse(rep.quorum_achieved)
        self.assertFalse(rep.concordance_condition_satisfied)

        # Total habilitado = 5M. Concordantes = 3M (60% <= 66.67%)
        self.assertEqual(rep.total_enabled_shares, 5000000)
        self.assertEqual(rep.total_concordant_shares, 3000000)
        self.assertEqual(rep.total_accepted_shares, 0)
        self.assertEqual(rep.total_returned_shares, 5000000)
        self.assertEqual(rep.total_financial_disbursed_brl, Decimal("0.00"))

        # Controlador permanece com posição inicial
        self.assertEqual(rep.post_auction_controller_shares, 14000000)
        self.assertEqual(rep.post_auction_free_float_shares, 6000000)
        self.assertEqual(rep.squeeze_out_status, SqueezeOutAction.NOT_ELIGIBLE)

    def test_scenario_03_successful_delisting_with_squeeze_out(self):
        """Cenário 3: Aceitação supera 95%, controlador atinge >= 95%, resgate compulsório total."""
        engine = DelistingOpaAuctionEngine.from_json_fixture(self.fixture_path)
        rep = engine.run_auction("OPA-DELIST-2026-003")

        self.assertEqual(rep.status, DelistingAuctionStatus.HOMOLOGATED_SUCCESS)
        self.assertTrue(rep.delisting_approved)
        self.assertTrue(rep.quorum_achieved)
        self.assertTrue(rep.squeeze_out_eligible)
        self.assertEqual(rep.squeeze_out_status, SqueezeOutAction.EXECUTED)

        # Venda inicial aceita: 1.200.000 ações
        self.assertEqual(rep.total_accepted_shares, 1200000)
        # Resgate compulsório de ordens (150k agree + 50k dissent = 200k)
        self.assertEqual(rep.squeeze_out_shares_from_orders, 200000)
        # Devolução final das ordens é zero (todas foram resgatadas)
        self.assertEqual(rep.total_returned_shares, 0)

        # Depósito escrow para ações não habilitadas: 1.5M free float - 1.4M habilitadas = 100k
        self.assertEqual(rep.squeeze_out_unregistered_shares, 100000)
        self.assertIsNotNone(rep.unregistered_squeeze_out)
        self.assertEqual(rep.unregistered_squeeze_out.unregistered_quantity, 100000)
        self.assertEqual(rep.unregistered_squeeze_out.deposit_financial_brl, Decimal("5000000.00"))

        # Financeiro:
        # Tender: 1.2M * 50 = 60,000,000.00
        # Squeeze ordens: 200k * 50 = 10,000,000.00
        # Squeeze escrow: 100k * 50 = 5,000,000.00
        # Total desembolsado = 75,000,000.00
        self.assertEqual(rep.total_financial_tender_brl, Decimal("60000000.00"))
        self.assertEqual(rep.total_financial_squeeze_out_brl, Decimal("15000000.00"))
        self.assertEqual(rep.total_financial_disbursed_brl, Decimal("75000000.00"))

        # Linhas individuais de alocação conservadas
        for a in rep.allocations:
            self.assertTrue(a.is_conserved)
            if a.stance == ShareholderDelistingStance.TENDER_SELL:
                self.assertEqual(a.tender_accepted_quantity, a.enabled_quantity)
                self.assertEqual(a.returned_quantity, 0)
                self.assertEqual(a.squeeze_out_redeemed_quantity, 0)
            else:
                self.assertEqual(a.tender_accepted_quantity, 0)
                self.assertEqual(a.returned_quantity, 0)
                self.assertEqual(a.squeeze_out_redeemed_quantity, a.enabled_quantity)
                self.assertEqual(a.squeeze_out_financial_brl, Decimal(a.enabled_quantity) * Decimal("50.00"))

    def test_scenario_04_delisting_with_tag_along_compliance(self):
        """Cenário 4: OPA unificada com Alienação de Controle e cumprimento do piso de 80%."""
        engine = DelistingOpaAuctionEngine.from_json_fixture(self.fixture_path)
        rep = engine.run_auction("OPA-DELIST-2026-004")

        self.assertEqual(rep.status, DelistingAuctionStatus.HOMOLOGATED_SUCCESS)
        self.assertTrue(rep.delisting_approved)
        self.assertTrue(rep.tag_along_respected)
        self.assertEqual(rep.tag_along_floor_price, Decimal("32.00"))
        self.assertGreaterEqual(rep.offer_price_per_share, rep.tag_along_floor_price)
        self.assertEqual(rep.total_accepted_shares, 3200000)
        self.assertEqual(rep.total_returned_shares, 1000000)

    def test_scenario_05_concurrent_delisting_with_pro_rata_allocation(self):
        """Cenário 5: Rateio determinístico de sobras pelo Método dos Maiores Restos (Hamilton)."""
        engine = DelistingOpaAuctionEngine.from_json_fixture(self.fixture_path)
        rep = engine.run_auction("OPA-DELIST-2026-005")

        self.assertEqual(rep.status, DelistingAuctionStatus.HOMOLOGATED_SUCCESS)
        self.assertTrue(rep.delisting_approved)
        self.assertEqual(rep.total_sell_shares, 3000000)
        self.assertEqual(rep.total_accepted_shares, 2000000)  # Exatamente o target
        self.assertEqual(rep.total_returned_shares, 1400000)  # 1M sobras + 400k agree
        self.assertEqual(rep.total_enabled_shares, 3400000)
        self.assertTrue(rep.is_conservation_satisfied)

        # Alocações específicas do rateio
        alloc_map = {a.order_id: a for a in rep.allocations}
        # ORD-01 (1.5M enabled): piso 1.000.000, resto 0 -> 1.000.000 aceitas, 500k devolvidas
        self.assertEqual(alloc_map["ORD-DELIST-005-01"].tender_accepted_quantity, 1000000)
        self.assertEqual(alloc_map["ORD-DELIST-005-01"].returned_quantity, 500000)
        # ORD-02 (1.0M enabled): piso 666.666, resto 2M (maior resto ganha +1) -> 666.667 aceitas
        self.assertEqual(alloc_map["ORD-DELIST-005-02"].tender_accepted_quantity, 666667)
        self.assertEqual(alloc_map["ORD-DELIST-005-02"].returned_quantity, 333333)
        # ORD-03 (500k enabled): piso 333.333, resto 1M -> 333.333 aceitas, 166.667 devolvidas
        self.assertEqual(alloc_map["ORD-DELIST-005-03"].tender_accepted_quantity, 333333)
        self.assertEqual(alloc_map["ORD-DELIST-005-03"].returned_quantity, 166667)
        # ORD-04 (AGREE_NO_SALE 400k): 0 aceitas, 400k devolvidas
        self.assertEqual(alloc_map["ORD-DELIST-005-04"].tender_accepted_quantity, 0)
        self.assertEqual(alloc_map["ORD-DELIST-005-04"].returned_quantity, 400000)

        # Soma das aceitas deve bater rigorosamente com o target de 2.000.000
        self.assertEqual(
            alloc_map["ORD-DELIST-005-01"].tender_accepted_quantity
            + alloc_map["ORD-DELIST-005-02"].tender_accepted_quantity
            + alloc_map["ORD-DELIST-005-03"].tender_accepted_quantity,
            2000000,
        )

    def test_tag_along_floor_violations(self):
        """Valida que oferta abaixo de 80% (Tradicional) ou 100% (Novo Mercado) lança exceção."""
        engine = DelistingOpaAuctionEngine.from_json_fixture(self.fixture_path)
        cfg = engine.get_auction("OPA-DELIST-2026-004")

        # No cenário 4, preço de controle é 40.00, piso 80% é 32.00.
        # Se alterarmos o preço ofertado para 31.99, deve lançar TagAlongFloorViolationError:
        cfg.offer_price_per_share = Decimal("31.99")
        with self.assertRaises(TagAlongFloorViolationError):
            engine.run_auction("OPA-DELIST-2026-004")

        # Teste de Novo Mercado requerendo 100%
        cfg.governance_segment = CorporateGovernanceSegment.NOVO_MERCADO
        cfg.offer_price_per_share = Decimal("39.99")
        with self.assertRaises(TagAlongFloorViolationError):
            engine.run_auction("OPA-DELIST-2026-004")

        # Se oferta for 40.00 no Novo Mercado, deve ser válida
        cfg.offer_price_per_share = Decimal("40.00")
        engine.validate_auction_config(cfg)

    def test_missing_control_block_price_in_change_of_control(self):
        """Valida que OPA unificada de controle sem preço do bloco de controle lança erro."""
        engine = DelistingOpaAuctionEngine.from_json_fixture(self.fixture_path)
        cfg = engine.get_auction("OPA-DELIST-2026-004")
        cfg.control_block_price_per_share = None

        with self.assertRaises(DelistingValidationError):
            engine.run_auction("OPA-DELIST-2026-004")

    def test_unblocked_custody_rejection(self):
        """Valida que ordem com ações desimpedidas (sem bloqueio) é rejeitada."""
        engine = DelistingOpaAuctionEngine.from_json_fixture(self.fixture_path)
        cfg = engine.get_auction("OPA-DELIST-2026-001")
        # Cria ordem sem bloqueio de custódia
        unblocked_order = DelistingShareholderOrder(
            order_id="ORD-UNBLOCKED-01",
            shareholder_id="CPF-999",
            participant_code=105,
            custody_account="999-9",
            stance=ShareholderDelistingStance.TENDER_SELL,
            enabled_quantity=1000,
            blocked_in_custody=False,
        )
        cfg.orders.append(unblocked_order)

        with self.assertRaises(CustodyNotBlockedError):
            engine.run_auction("OPA-DELIST-2026-001")

    def test_exact_two_thirds_boundary_condition(self):
        """Valida o limite exato de 2/3: empate (3*Q_conc == 2*Q_hab) rejeita; mais de 2/3 aprova."""
        # Caso A: Exatamente 2/3 (66.666...%) -> 20.000 concordantes de 30.000 habilitadas.
        # 3 * 20.000 = 60.000; 2 * 30.000 = 60.000. Condição 3*Q > 2*H é FALSA -> REJEITA!
        cfg_tie = DelistingAuctionConfig(
            auction_id="OPA-TIE-BOUNDARY",
            company_name="BOUNDARY S.A.",
            ticker="BDY3",
            isin="BRBDY3ACNOR0",
            offeror_name="BDY HOLDING",
            modality=DelistingOpaModality.FECHAMENTO_CAPITAL,
            governance_segment=CorporateGovernanceSegment.NOVO_MERCADO,
            offer_price_per_share=Decimal("10.00"),
            control_block_price_per_share=None,
            statutory_tag_along_ratio=None,
            total_issued_shares=100000,
            initial_controller_shares=70000,
            initial_free_float_shares=30000,
            execute_squeeze_out=False,
            max_target_quantity=None,
            min_acceptance_quantity=0,
            auction_date=date(2026, 11, 4),
            settlement_date=date(2026, 11, 6),
            orders=[
                DelistingShareholderOrder(
                    order_id="ORD-TIE-01",
                    shareholder_id="CPF-01",
                    participant_code=105,
                    custody_account="CUST-01",
                    stance=ShareholderDelistingStance.TENDER_SELL,
                    enabled_quantity=20000,
                ),
                DelistingShareholderOrder(
                    order_id="ORD-TIE-02",
                    shareholder_id="CPF-02",
                    participant_code=105,
                    custody_account="CUST-02",
                    stance=ShareholderDelistingStance.DISSENT_HOLD,
                    enabled_quantity=10000,
                ),
            ],
        )
        engine = DelistingOpaAuctionEngine()
        engine.register_auction(cfg_tie)
        rep_tie = engine.run_auction("OPA-TIE-BOUNDARY")
        self.assertEqual(rep_tie.status, DelistingAuctionStatus.REJECTED_INSUFFICIENT_QUORUM)
        self.assertFalse(rep_tie.delisting_approved)
        self.assertFalse(rep_tie.quorum_achieved)
        self.assertEqual(rep_tie.total_accepted_shares, 0)
        self.assertEqual(rep_tie.total_returned_shares, 30000)

        # Caso B: Mais de 2/3 pelo menor incremento inteiro possível (+1 ação concordante):
        # 20.001 concordantes de 30.000 habilitadas.
        # 3 * 20.001 = 60.003 > 2 * 30.000 = 60.000. Condição VERDADEIRA -> APROVA!
        cfg_win = DelistingAuctionConfig(
            auction_id="OPA-WIN-BOUNDARY",
            company_name="BOUNDARY S.A.",
            ticker="BDY3",
            isin="BRBDY3ACNOR0",
            offeror_name="BDY HOLDING",
            modality=DelistingOpaModality.FECHAMENTO_CAPITAL,
            governance_segment=CorporateGovernanceSegment.NOVO_MERCADO,
            offer_price_per_share=Decimal("10.00"),
            control_block_price_per_share=None,
            statutory_tag_along_ratio=None,
            total_issued_shares=100000,
            initial_controller_shares=70000,
            initial_free_float_shares=30000,
            execute_squeeze_out=False,
            max_target_quantity=None,
            min_acceptance_quantity=0,
            auction_date=date(2026, 11, 4),
            settlement_date=date(2026, 11, 6),
            orders=[
                DelistingShareholderOrder(
                    order_id="ORD-WIN-01",
                    shareholder_id="CPF-01",
                    participant_code=105,
                    custody_account="CUST-01",
                    stance=ShareholderDelistingStance.TENDER_SELL,
                    enabled_quantity=20001,
                ),
                DelistingShareholderOrder(
                    order_id="ORD-WIN-02",
                    shareholder_id="CPF-02",
                    participant_code=105,
                    custody_account="CUST-02",
                    stance=ShareholderDelistingStance.DISSENT_HOLD,
                    enabled_quantity=9999,
                ),
            ],
        )
        engine.register_auction(cfg_win)
        rep_win = engine.run_auction("OPA-WIN-BOUNDARY")
        self.assertEqual(rep_win.status, DelistingAuctionStatus.HOMOLOGATED_SUCCESS)
        self.assertTrue(rep_win.delisting_approved)
        self.assertTrue(rep_win.quorum_achieved)
        self.assertEqual(rep_win.total_accepted_shares, 20001)
        self.assertEqual(rep_win.total_returned_shares, 9999)

    def test_minimum_acceptance_condition_failure(self):
        """Valida que aceitação mínima inferior ao estipulado pelo ofertante frustra a OPA."""
        engine = DelistingOpaAuctionEngine.from_json_fixture(self.fixture_path)
        cfg = engine.get_auction("OPA-DELIST-2026-005")
        # Define aceitação mínima superior à soma de ordens de venda (ex: 3.500.000)
        cfg.min_acceptance_quantity = 3500000

        rep = engine.run_auction("OPA-DELIST-2026-005")
        self.assertEqual(rep.status, DelistingAuctionStatus.REJECTED_MIN_ACCEPTANCE_NOT_MET)
        self.assertFalse(rep.delisting_approved)
        self.assertEqual(rep.total_accepted_shares, 0)
        self.assertEqual(rep.total_returned_shares, rep.total_enabled_shares)

    def test_squeeze_out_eligible_but_not_requested(self):
        """Valida que se controle >= 95% mas execute_squeeze_out=False, fica ELIGIBLE_NOT_REQUESTED."""
        engine = DelistingOpaAuctionEngine.from_json_fixture(self.fixture_path)
        cfg = engine.get_auction("OPA-DELIST-2026-003")
        cfg.execute_squeeze_out = False

        rep = engine.run_auction("OPA-DELIST-2026-003")
        self.assertEqual(rep.status, DelistingAuctionStatus.HOMOLOGATED_SUCCESS)
        self.assertTrue(rep.squeeze_out_eligible)
        self.assertEqual(rep.squeeze_out_status, SqueezeOutAction.ELIGIBLE_NOT_REQUESTED)
        self.assertEqual(rep.squeeze_out_shares_from_orders, 0)
        self.assertEqual(rep.total_returned_shares, 200000)

    def test_validation_errors(self):
        """Valida todas as rejeições cadastrais e numéricas."""
        engine = DelistingOpaAuctionEngine()

        # Total emitido <= 0
        with self.assertRaises(DelistingValidationError):
            cfg_zero = DelistingAuctionConfig(
                auction_id="ERR-01",
                company_name="ERR",
                ticker="ERR3",
                isin="BRERR3ACNOR0",
                offeror_name="OFF",
                modality=DelistingOpaModality.FECHAMENTO_CAPITAL,
                governance_segment=CorporateGovernanceSegment.NOVO_MERCADO,
                offer_price_per_share=Decimal("10.00"),
                control_block_price_per_share=None,
                statutory_tag_along_ratio=None,
                total_issued_shares=0,
                initial_controller_shares=0,
                initial_free_float_shares=0,
                execute_squeeze_out=False,
                max_target_quantity=None,
                min_acceptance_quantity=0,
                auction_date=date(2026, 11, 4),
                settlement_date=date(2026, 11, 6),
            )
            engine.register_auction(cfg_zero)

        # Inconsistência de capital (controlador + free_float != total_issued)
        with self.assertRaises(DelistingValidationError):
            cfg_bad_capital = DelistingAuctionConfig(
                auction_id="ERR-02",
                company_name="ERR",
                ticker="ERR3",
                isin="BRERR3ACNOR0",
                offeror_name="OFF",
                modality=DelistingOpaModality.FECHAMENTO_CAPITAL,
                governance_segment=CorporateGovernanceSegment.NOVO_MERCADO,
                offer_price_per_share=Decimal("10.00"),
                control_block_price_per_share=None,
                statutory_tag_along_ratio=None,
                total_issued_shares=10000,
                initial_controller_shares=6000,
                initial_free_float_shares=3000,  # 6k + 3k = 9k != 10k
                execute_squeeze_out=False,
                max_target_quantity=None,
                min_acceptance_quantity=0,
                auction_date=date(2026, 11, 4),
                settlement_date=date(2026, 11, 6),
            )
            engine.register_auction(cfg_bad_capital)

        # Preço ofertado <= 0
        with self.assertRaises(DelistingValidationError):
            cfg_zero_price = DelistingAuctionConfig(
                auction_id="ERR-03",
                company_name="ERR",
                ticker="ERR3",
                isin="BRERR3ACNOR0",
                offeror_name="OFF",
                modality=DelistingOpaModality.FECHAMENTO_CAPITAL,
                governance_segment=CorporateGovernanceSegment.NOVO_MERCADO,
                offer_price_per_share=Decimal("0.00"),
                control_block_price_per_share=None,
                statutory_tag_along_ratio=None,
                total_issued_shares=10000,
                initial_controller_shares=7000,
                initial_free_float_shares=3000,
                execute_squeeze_out=False,
                max_target_quantity=None,
                min_acceptance_quantity=0,
                auction_date=date(2026, 11, 4),
                settlement_date=date(2026, 11, 6),
            )
            engine.register_auction(cfg_zero_price)

        # Ordem com quantidade habilitada <= 0
        with self.assertRaises(DelistingValidationError):
            cfg_zero_order = DelistingAuctionConfig(
                auction_id="ERR-04",
                company_name="ERR",
                ticker="ERR3",
                isin="BRERR3ACNOR0",
                offeror_name="OFF",
                modality=DelistingOpaModality.FECHAMENTO_CAPITAL,
                governance_segment=CorporateGovernanceSegment.NOVO_MERCADO,
                offer_price_per_share=Decimal("10.00"),
                control_block_price_per_share=None,
                statutory_tag_along_ratio=None,
                total_issued_shares=10000,
                initial_controller_shares=7000,
                initial_free_float_shares=3000,
                execute_squeeze_out=False,
                max_target_quantity=None,
                min_acceptance_quantity=0,
                auction_date=date(2026, 11, 4),
                settlement_date=date(2026, 11, 6),
                orders=[
                    DelistingShareholderOrder(
                        order_id="ORD-0",
                        shareholder_id="0",
                        participant_code=1,
                        custody_account="1",
                        stance=ShareholderDelistingStance.TENDER_SELL,
                        enabled_quantity=0,
                    )
                ],
            )
            engine.register_auction(cfg_zero_order)

        # Leilão não encontrado
        with self.assertRaises(DelistingAuctionNotFoundError):
            engine.get_auction("AUCTION-DOES-NOT-EXIST")

    def test_helper_functions_and_data_classes(self):
        """Valida helpers de quantização, datas e representações."""
        self.assertEqual(_quantize_money(12.345), Decimal("12.35"))
        self.assertEqual(_quantize_rate("0.123456789"), Decimal("0.12345679"))
        self.assertEqual(_to_date(date(2026, 11, 4), "test"), date(2026, 11, 4))
        self.assertEqual(_to_date("2026-11-04T00:00:00Z", "test"), date(2026, 11, 4))

        with self.assertRaises(ValueError):
            _to_date(12345, "invalid")

        alloc = DelistingAllocationLine(
            order_id="ORD-T",
            shareholder_id="SH-01",
            participant_code=105,
            custody_account="ACC-1",
            stance=ShareholderDelistingStance.TENDER_SELL,
            enabled_quantity=100,
            tender_accepted_quantity=60,
            returned_quantity=30,
            squeeze_out_redeemed_quantity=10,
            tender_financial_brl=Decimal("600.00"),
            squeeze_out_financial_brl=Decimal("100.00"),
            total_financial_brl=Decimal("700.00"),
        )
        self.assertEqual(alloc.total_disposed_quantity, 70)
        self.assertTrue(alloc.is_conserved)


if __name__ == "__main__":
    unittest.main()
