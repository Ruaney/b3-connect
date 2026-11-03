"""Suíte de testes automatizados para Ofertas Públicas CVM 160 e Bookbuilding B3 (FL-033)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import unittest

from src.public_offerings_cvm160 import (
    AllocationOutcome,
    AllocationStatus,
    BookbuildingBid,
    BookbuildingExecutionError,
    BookbuildingSummary,
    CvmRite,
    IneligibleInvestorError,
    InvestorCategory,
    OfferingType,
    OfferingValidationError,
    PublicOfferingEngine,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_ROOT / "fixtures" / "bookbuilding_cvm160_bids.json"


class TestPublicOfferingsCvm160Fixture(unittest.TestCase):
    """Testa o carregamento e apuração determinística da fixture oficial de IPO CVM 160."""

    def test_load_and_execute_fixture_ipo(self) -> None:
        engine = PublicOfferingEngine.from_json_file(FIXTURE_PATH)
        self.assertEqual(engine.offering_id, "OFR-CVM160-2026-001")
        self.assertEqual(engine.offering_type, OfferingType.IPO)
        self.assertEqual(engine.cvm_rite, CvmRite.ORDINARY)
        self.assertEqual(engine.total_offered_quantity, 10000)
        self.assertEqual(len(engine.bids), 11)

        summary = engine.execute_bookbuilding()
        self.assertIsInstance(summary, BookbuildingSummary)
        self.assertTrue(summary.is_fully_subscribed)
        self.assertEqual(summary.total_offered_quantity, 10000)
        self.assertEqual(summary.total_allocated_quantity, 10000)
        self.assertEqual(summary.unallocated_offered_quantity, 0)
        self.assertEqual(summary.clearing_price_or_rate, Decimal("21.00"))
        self.assertEqual(summary.retail_allocated_quantity, 2000)
        self.assertEqual(summary.institutional_allocated_quantity, 8000)
        self.assertEqual(summary.total_settlement_amount, Decimal("210000.00"))

        # Verifica resultados individuais de Varejo (Tranche 20% = 2.000 ações)
        ret_001 = summary.get_outcome("BID-RET-001")
        ret_002 = summary.get_outcome("BID-RET-002")
        ret_003 = summary.get_outcome("BID-RET-003")
        ret_004 = summary.get_outcome("BID-RET-004")
        ret_005 = summary.get_outcome("BID-RET-005")

        self.assertIsNotNone(ret_001)
        self.assertIsNotNone(ret_002)
        self.assertIsNotNone(ret_003)
        self.assertIsNotNone(ret_004)
        self.assertIsNotNone(ret_005)

        assert ret_001 and ret_002 and ret_003 and ret_004 and ret_005
        self.assertEqual(ret_001.allocated_quantity, 800)
        self.assertEqual(ret_001.status, AllocationStatus.FULL)
        self.assertEqual(ret_002.allocated_quantity, 700)
        self.assertEqual(ret_002.status, AllocationStatus.FULL)

        # No corte de R$ 21,00 do Varejo sobram 500 ações para ratear entre 600 e 400 (total 1.000 -> 50%)
        self.assertEqual(ret_003.allocated_quantity, 300)
        self.assertEqual(ret_003.status, AllocationStatus.PARTIAL_PRORATA)
        self.assertEqual(ret_003.allocation_ratio, Decimal("0.500000"))

        self.assertEqual(ret_004.allocated_quantity, 200)
        self.assertEqual(ret_004.status, AllocationStatus.PARTIAL_PRORATA)
        self.assertEqual(ret_004.allocation_ratio, Decimal("0.500000"))

        # BID-RET-005 ofertou R$ 20,00 < R$ 21,00 -> excluída pelo corte
        self.assertEqual(ret_005.allocated_quantity, 0)
        self.assertEqual(ret_005.status, AllocationStatus.CUTOFF_EXCLUDED)

        # Verifica resultados Institucionais (Tranche 80% = 8.000 ações)
        inst_001 = summary.get_outcome("BID-INST-001")
        inst_002 = summary.get_outcome("BID-INST-002")
        qual_001 = summary.get_outcome("BID-QUAL-001")
        inst_003 = summary.get_outcome("BID-INST-003")
        inst_004 = summary.get_outcome("BID-INST-004")
        inst_005 = summary.get_outcome("BID-INST-005")

        assert inst_001 and inst_002 and qual_001 and inst_003 and inst_004 and inst_005
        self.assertEqual(inst_001.allocated_quantity, 3500)
        self.assertEqual(inst_002.allocated_quantity, 2500)
        # BID-QUAL-001 está no preço de corte (R$ 21,00), mas possui lockup_days=90 -> prioridade 100%!
        self.assertEqual(qual_001.allocated_quantity, 1500)
        self.assertEqual(qual_001.status, AllocationStatus.FULL)

        # Sobram 500 ações institucionais para ratear igualmente entre BID-INST-003 (2000) e BID-INST-004 (2000) sem lock-up
        self.assertEqual(inst_003.allocated_quantity, 250)
        self.assertEqual(inst_003.status, AllocationStatus.PARTIAL_PRORATA)
        self.assertEqual(inst_004.allocated_quantity, 250)
        self.assertEqual(inst_004.status, AllocationStatus.PARTIAL_PRORATA)

        # BID-INST-005 ofertou R$ 19,50 < R$ 21,00 -> excluída no corte
        self.assertEqual(inst_005.allocated_quantity, 0)
        self.assertEqual(inst_005.status, AllocationStatus.CUTOFF_EXCLUDED)


class TestRateBasedBookbuildingCvm160(unittest.TestCase):
    """Testa ofertas cotadas por Taxa/Spread (Debêntures Incentivadas, CRI, CRA)."""

    def test_debenture_incentivada_dutch_auction_by_rate(self) -> None:
        """Em ofertas por taxa, menor taxa exigida é mais competitiva e vence o corte."""
        engine = PublicOfferingEngine(
            offering_id="OFR-DEB-2026-002",
            issuer_name="RODOVIAS DO BRASIL S.A.",
            ticker="RODO11",
            isin="BRRODO01DBS8",
            offering_type=OfferingType.DEBENTURE_INCENTIVADA,
            total_offered_quantity=1000,
            min_price_or_rate=Decimal("5.50"),  # IPCA + 5,50% a.a.
            max_price_or_rate=Decimal("6.80"),  # Taxa teto: IPCA + 6,80% a.a.
            unit_nominal_price=Decimal("1000.00"),
            lockup_priority_enabled=False,
        )

        # Investidor 1 aceita IPCA + 6,00% (muito competitivo)
        engine.submit_bid(
            BookbuildingBid(
                bid_id="DEB-01",
                investor_id="FUND-INFRA-01",
                participant_id=10,
                investor_category=InvestorCategory.PROFESSIONAL,
                quantity=400,
                limit_price_or_rate=Decimal("6.00"),
                timestamp=datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc),
            )
        )
        # Investidor 2 aceita IPCA + 6,25%
        engine.submit_bid(
            BookbuildingBid(
                bid_id="DEB-02",
                investor_id="FUND-INFRA-02",
                participant_id=11,
                investor_category=InvestorCategory.PROFESSIONAL,
                quantity=400,
                limit_price_or_rate=Decimal("6.25"),
                timestamp=datetime(2026, 11, 3, 10, 5, tzinfo=timezone.utc),
            )
        )
        # Investidor 3 e 4 exigem IPCA + 6,50% (faixa marginal: precisam de 200 entre 600 demandados)
        engine.submit_bid(
            BookbuildingBid(
                bid_id="DEB-03",
                investor_id="QUAL-01",
                participant_id=12,
                investor_category=InvestorCategory.QUALIFIED,
                quantity=300,
                limit_price_or_rate=Decimal("6.50"),
                timestamp=datetime(2026, 11, 3, 10, 10, tzinfo=timezone.utc),
            )
        )
        engine.submit_bid(
            BookbuildingBid(
                bid_id="DEB-04",
                investor_id="QUAL-02",
                participant_id=12,
                investor_category=InvestorCategory.QUALIFIED,
                quantity=300,
                limit_price_or_rate=Decimal("6.50"),
                timestamp=datetime(2026, 11, 3, 10, 12, tzinfo=timezone.utc),
            )
        )
        # Investidor 5 exige IPCA + 6,75% (acima do corte de 6,50% -> excluído!)
        engine.submit_bid(
            BookbuildingBid(
                bid_id="DEB-05",
                investor_id="PROF-HIGH-RATE",
                participant_id=15,
                investor_category=InvestorCategory.PROFESSIONAL,
                quantity=500,
                limit_price_or_rate=Decimal("6.75"),
                timestamp=datetime(2026, 11, 3, 10, 15, tzinfo=timezone.utc),
            )
        )
        # Investidor 6 exige IPCA + 7,10% (acima da taxa teto 6,80% -> rejeitado de plano!)
        engine.submit_bid(
            BookbuildingBid(
                bid_id="DEB-06",
                investor_id="PROF-ABOVE-CEILING",
                participant_id=15,
                investor_category=InvestorCategory.PROFESSIONAL,
                quantity=200,
                limit_price_or_rate=Decimal("7.10"),
                timestamp=datetime(2026, 11, 3, 10, 20, tzinfo=timezone.utc),
            )
        )

        summary = engine.execute_bookbuilding()
        self.assertEqual(summary.clearing_price_or_rate, Decimal("6.50"))
        self.assertEqual(summary.total_allocated_quantity, 1000)
        self.assertEqual(summary.total_settlement_amount, Decimal("1000000.00"))

        self.assertEqual(summary.get_outcome("DEB-01").allocated_quantity, 400)  # type: ignore[union-attr]
        self.assertEqual(summary.get_outcome("DEB-02").allocated_quantity, 400)  # type: ignore[union-attr]
        self.assertEqual(summary.get_outcome("DEB-03").allocated_quantity, 100)  # type: ignore[union-attr]
        self.assertEqual(summary.get_outcome("DEB-04").allocated_quantity, 100)  # type: ignore[union-attr]
        self.assertEqual(summary.get_outcome("DEB-05").status, AllocationStatus.CUTOFF_EXCLUDED)  # type: ignore[union-attr]
        self.assertEqual(summary.get_outcome("DEB-06").status, AllocationStatus.REJECTED)  # type: ignore[union-attr]


class TestProRataConservationAndEdgeCases(unittest.TestCase):
    """Testa conservação de quantidades sob dízimas periódicas, sub-subscrição e validações."""

    def test_exact_integer_conservation_on_prime_fractions(self) -> None:
        """Três propostas idênticas disputando 100 unidades (33,333...% cada) devem somar exatamente 100."""
        engine = PublicOfferingEngine(
            offering_id="OFR-PRORATA-100",
            issuer_name="EMISSOR TESTE S.A.",
            ticker="TEST3",
            isin="BRTESTACNOR9",
            offering_type=OfferingType.FOLLOW_ON,
            total_offered_quantity=100,
            min_price_or_rate=Decimal("10.00"),
            max_price_or_rate=Decimal("15.00"),
        )

        for i in range(1, 4):
            engine.submit_bid(
                BookbuildingBid(
                    bid_id=f"BID-0{i}",
                    investor_id=f"INV-0{i}",
                    participant_id=100 + i,
                    investor_category=InvestorCategory.PROFESSIONAL,
                    quantity=100,
                    limit_price_or_rate=Decimal("12.00"),
                    timestamp=datetime(2026, 11, 3, 12, i, tzinfo=timezone.utc),
                )
            )

        summary = engine.execute_bookbuilding()
        self.assertEqual(summary.total_allocated_quantity, 100)
        allocs = [o.allocated_quantity for o in summary.outcomes]
        # A primeira por FIFO leva 34 e as outras duas 33 (34 + 33 + 33 = 100)
        self.assertEqual(allocs, [34, 33, 33])
        self.assertEqual(sum(allocs), 100)

    def test_undersubscribed_offering_allocates_all_valid_bids(self) -> None:
        """Se a demanda total válida for menor que o lote ofertado, todos recebem 100% no piso."""
        engine = PublicOfferingEngine(
            offering_id="OFR-UNDER-001",
            issuer_name="FII LOGISTICA",
            ticker="LOGG11",
            isin="BRLOGGCTF001",
            offering_type=OfferingType.FII_QUOTA,
            total_offered_quantity=5000,
            min_price_or_rate=Decimal("100.00"),
            max_price_or_rate=Decimal("110.00"),
        )
        engine.submit_bid(
            BookbuildingBid(
                bid_id="B1",
                investor_id="I1",
                participant_id=1,
                investor_category=InvestorCategory.RETAIL,
                quantity=1200,
                limit_price_or_rate=Decimal("105.00"),
            )
        )
        engine.submit_bid(
            BookbuildingBid(
                bid_id="B2",
                investor_id="I2",
                participant_id=2,
                investor_category=InvestorCategory.QUALIFIED,
                quantity=1800,
                limit_price_or_rate=Decimal("102.00"),
            )
        )

        summary = engine.execute_bookbuilding()
        self.assertFalse(summary.is_fully_subscribed)
        self.assertEqual(summary.total_allocated_quantity, 3000)
        self.assertEqual(summary.unallocated_offered_quantity, 2000)
        self.assertEqual(summary.clearing_price_or_rate, Decimal("100.00"))

    def test_tranche_spillover_when_retail_is_undersubscribed(self) -> None:
        """Se o varejo não preencher sua tranche reservada, o saldo migra para o institucional."""
        engine = PublicOfferingEngine(
            offering_id="OFR-SPILL-001",
            issuer_name="COMPANHIA ABERTA S.A.",
            ticker="OPEN3",
            isin="BROPENACNOR1",
            offering_type=OfferingType.IPO,
            total_offered_quantity=1000,
            min_price_or_rate=Decimal("10.00"),
            max_price_or_rate=Decimal("20.00"),
            retail_tranche_pct=Decimal("0.30"),  # 300 reservadas ao varejo
        )
        # Varejo pede apenas 100
        engine.submit_bid(
            BookbuildingBid(
                bid_id="RET-1",
                investor_id="R1",
                participant_id=1,
                investor_category=InvestorCategory.RETAIL,
                quantity=100,
                limit_price_or_rate=Decimal("18.00"),
            )
        )
        # Institucional pede 1200
        engine.submit_bid(
            BookbuildingBid(
                bid_id="INST-1",
                investor_id="I1",
                participant_id=2,
                investor_category=InvestorCategory.PROFESSIONAL,
                quantity=1200,
                limit_price_or_rate=Decimal("15.00"),
            )
        )

        summary = engine.execute_bookbuilding()
        self.assertEqual(summary.total_allocated_quantity, 1000)
        self.assertEqual(summary.retail_allocated_quantity, 100)
        self.assertEqual(summary.institutional_allocated_quantity, 900)

    def test_validation_and_ineligible_investor_errors(self) -> None:
        """Garante rejeição imediata de investidores inelegíveis ou parâmetros inválidos."""
        engine = PublicOfferingEngine(
            offering_id="OFR-PROF-ONLY",
            issuer_name="SECURITIZADORA S.A.",
            ticker="CRI01",
            isin="BRCRI001CRI9",
            offering_type=OfferingType.CRI,
            total_offered_quantity=500,
            min_price_or_rate=Decimal("7.00"),
            max_price_or_rate=Decimal("8.50"),
            cvm_rite=CvmRite.AUTOMATIC,
            allowed_categories=[InvestorCategory.PROFESSIONAL],
        )

        with self.assertRaises(IneligibleInvestorError):
            engine.submit_bid(
                BookbuildingBid(
                    bid_id="RET-FORBIDDEN",
                    investor_id="CPF-01",
                    participant_id=10,
                    investor_category=InvestorCategory.RETAIL,
                    quantity=50,
                    limit_price_or_rate=Decimal("7.50"),
                )
            )

        with self.assertRaises(OfferingValidationError):
            BookbuildingBid(
                bid_id="BAD-QTY",
                investor_id="INV-1",
                participant_id=10,
                investor_category=InvestorCategory.PROFESSIONAL,
                quantity=0,
                limit_price_or_rate=Decimal("7.50"),
            )

        with self.assertRaises(BookbuildingExecutionError):
            engine.execute_bookbuilding()


if __name__ == "__main__":
    unittest.main()
