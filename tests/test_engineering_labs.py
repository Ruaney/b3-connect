#!/usr/bin/env python3
"""Suíte de Testes dos Laboratórios Práticos de Engenharia (`labs/`)."""

from __future__ import annotations

from decimal import Decimal
import unittest

from labs.lab_01_puma_sbe_and_moc_auction import run_lab as run_lab_01
from labs.lab_02_imbarq_custody_reconciliation import run_lab as run_lab_02
from labs.lab_03_sng_gravames_and_sro_susep import run_lab as run_lab_03
from labs.lab_04_core_margin_and_waterfall import run_lab as run_lab_04


class TestEngineeringLabs(unittest.TestCase):
    """Valida a execução determinística dos 4 Laboratórios Práticos da B3."""

    def test_01_lab_puma_sbe_and_moc_auction(self) -> None:
        res = run_lab_01()
        self.assertGreaterEqual(res["sbe_byte_length"], 28)
        self.assertEqual(res["sbe_decoded_price"], Decimal("38.50"))
        self.assertEqual(res["sbe_decoded_qty"], 1000)
        self.assertEqual(res["moc_equilibrium_price"], Decimal("38.50"))
        self.assertEqual(res["moc_matched_quantity"], 8000)
        self.assertEqual(res["law008_c_abi_check"], 1)

    def test_02_lab_imbarq_custody_reconciliation(self) -> None:
        res = run_lab_02()
        self.assertTrue(res["petr4_law008_conserved"])
        self.assertEqual(res["total_positions_checked"], 2)
        self.assertEqual(res["matched_count"], 1)
        self.assertEqual(res["breaks_detected"], 1)
        self.assertEqual(res["first_break_ticker"], "VALE3")

    def test_03_lab_sng_gravames_and_sro_susep(self) -> None:
        res = run_lab_03()
        self.assertEqual(res["sng_final_status"], "BAIXADO")
        self.assertTrue(res["sng_law112_verified"])
        self.assertEqual(res["sro_paid_claims_total"], Decimal("450000.00"))
        self.assertTrue(res["sro_law113_verified"])

    def test_04_lab_core_margin_and_waterfall(self) -> None:
        res = run_lab_04()
        self.assertTrue(res["law003_verified"])
        self.assertTrue(res["law105a_verified"])
        self.assertGreater(res["total_required_margin_brl"], Decimal("0"))
        self.assertTrue(res["waterfall_fully_absorbed"])
        self.assertEqual(res["waterfall_mutualized_used"], Decimal("10000000.00"))


if __name__ == "__main__":
    unittest.main()
