"""Testes unitários para Leilões Especiais de OPA na B3 (FL-044)."""

from decimal import Decimal
from pathlib import Path
import unittest

from src.opa_tender_offer import (
    CorporateGovernanceSegment,
    OpaAuctionConfig,
    OpaAuctionStatus,
    OpaModality,
    OpaTenderOfferEngine,
    TagAlongPriceFloorViolationError,
)


class TestOpaTenderOffer(unittest.TestCase):
    def setUp(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_path = self.fixtures_dir / "opa_tender_offer_auction.json"

    def test_fixture_loading_and_execution(self):
        """Valida a execução de todas as modalidades da fixture oficial."""
        engine = OpaTenderOfferEngine.from_json_fixture(self.fixture_path)
        self.assertEqual(len(engine.auctions), 3)

        res = engine.summarize()
        self.assertEqual(res["total_auctions"], 3)
        self.assertTrue(res["all_conservations_satisfied"])

    def test_fechamento_capital_quorum_and_squeeze_out(self):
        """Valida que quórum > 2/3 aprova o fechamento de capital e confere Squeeze-Out se FF < 5%."""
        engine = OpaTenderOfferEngine.from_json_fixture(self.fixture_path)
        summary = engine.run_auction("OPA-B3-2026-001")

        self.assertEqual(summary.status, OpaAuctionStatus.HOMOLOGATED)
        self.assertTrue(summary.quorum_achieved)
        self.assertTrue(summary.squeeze_out_eligible)
        self.assertEqual(summary.total_accepted_shares, 2000000)
        self.assertEqual(summary.total_returned_shares, 400000)  # 300k agree + 100k dissent
        self.assertTrue(summary.is_conservation_satisfied)

    def test_tag_along_price_floor_enforcement(self):
        """Valida que oferta de Tag-Along abaixo de 80% (ou 100% no Novo Mercado) é rejeitada."""
        engine = OpaTenderOfferEngine.from_json_fixture(self.fixture_path)
        cfg = engine.get_auction("OPA-B3-2026-002")
        # Preço do controle é 40.00, segmento tradicional requer 80% (32.00).
        # Se alterarmos o preço ofertado para 31.99, deve falhar:
        cfg.offer_price_per_share = Decimal("31.99")

        with self.assertRaises(TagAlongPriceFloorViolationError):
            engine.run_auction("OPA-B3-2026-002")

    def test_voluntaria_parcial_pro_rata_allocation(self):
        """Valida que rateio pro-rata pelo método dos maiores restos entrega exatamente o target."""
        engine = OpaTenderOfferEngine.from_json_fixture(self.fixture_path)
        summary = engine.run_auction("OPA-B3-2026-003")

        self.assertEqual(summary.status, OpaAuctionStatus.HOMOLOGATED)
        # Target era 1.000.000 ações, total ofertado era 2.000.000 (800k + 600k + 600k)
        self.assertEqual(summary.total_accepted_shares, 1000000)
        self.assertEqual(summary.total_returned_shares, 1000000)
        self.assertTrue(summary.is_conservation_satisfied)
        # Alocações individuais: 400k para ordem 1, 300k para ordem 2, 300k para ordem 3
        acc_quantities = [a.accepted_quantity for a in summary.allocations]
        self.assertEqual(acc_quantities, [400000, 300000, 300000])


if __name__ == "__main__":
    unittest.main()
