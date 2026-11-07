"""Testes unitários e invariantes da Cédula Imobiliária Rural (CIR) com Afetação — NoMe Agro B3 (FL-097)."""

from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.rural_real_estate_cir import (
    CedulaImobiliariaRural,
    CirStatus,
    RuralPropertyLandParcel,
    RuralRealEstateCirEngine,
    round_cents,
    round_area,
)


class TestRuralRealEstateCir(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RuralRealEstateCirEngine()
        self.parcel = RuralPropertyLandParcel(
            parcel_id="PARCEL-MT-001",
            property_sncr_code="950.123.456.789-0",
            sigef_certification_code="SIGEF-MT-2026-99881",
            registry_office_code="CRI-SORRISO-MT",
            property_deed_number="MAT-44120",
            total_area_ha=Decimal("1250.5000"),
            affected_fraction_ha=Decimal("450.0000"),
            appraisal_value_brl=Decimal("18000000.00"),
        )
        self.engine.register_land_parcel(self.parcel)

        self.cir = CedulaImobiliariaRural(
            cir_id="CIR-2026-0001",
            issuer_tax_id="12.345.678/0001-90",
            creditor_tax_id="98.765.432/0001-10",
            parcel_id="PARCEL-MT-001",
            nominal_amount=Decimal("12000000.00"),
            interest_rate_annual=Decimal("0.1250"),
            issue_date="2026-11-01",
            due_date="2027-11-01",
        )

    def test_parcel_validation_and_bounds(self) -> None:
        # Fração afetada maior que área total deve levantar ValueError
        with self.assertRaises(ValueError):
            RuralPropertyLandParcel(
                parcel_id="PARCEL-INVALID",
                property_sncr_code="950.000",
                sigef_certification_code="SIGEF-FAIL",
                registry_office_code="CRI-01",
                property_deed_number="MAT-01",
                total_area_ha=Decimal("100.0000"),
                affected_fraction_ha=Decimal("150.0000"),
                appraisal_value_brl=Decimal("1000000.00"),
            )

        # Cadastro de mesma certificação SIGEF duplicada deve falhar
        dup_parcel = RuralPropertyLandParcel(
            parcel_id="PARCEL-DUP",
            property_sncr_code="950.999",
            sigef_certification_code="SIGEF-MT-2026-99881",
            registry_office_code="CRI-02",
            property_deed_number="MAT-02",
            total_area_ha=Decimal("500.0000"),
            affected_fraction_ha=Decimal("100.0000"),
            appraisal_value_brl=Decimal("2000000.00"),
        )
        with self.assertRaises(ValueError):
            self.engine.register_land_parcel(dup_parcel)

    def test_cir_issuance_prudential_limit(self) -> None:
        # Emissão regular dentro do limite (12M <= 18M)
        self.engine.issue_cir(self.cir)
        self.assertEqual(self.cir.status, CirStatus.ACTIVE)
        self.assertIn("CIR-2026-0001", self.engine.cirs)

        # Tentativa de emitir CIR com Notional > Valor de Avaliação (ex: 20M > 18M)
        parcel2 = RuralPropertyLandParcel(
            parcel_id="PARCEL-MT-002",
            property_sncr_code="950.555",
            sigef_certification_code="SIGEF-MT-555",
            registry_office_code="CRI-01",
            property_deed_number="MAT-02",
            total_area_ha=Decimal("200.0000"),
            affected_fraction_ha=Decimal("100.0000"),
            appraisal_value_brl=Decimal("5000000.00"),
        )
        self.engine.register_land_parcel(parcel2)
        excessive_cir = CedulaImobiliariaRural(
            cir_id="CIR-EXCESS",
            issuer_tax_id="12.345.678/0001-90",
            creditor_tax_id="98.765.432/0001-10",
            parcel_id="PARCEL-MT-002",
            nominal_amount=Decimal("6000000.00"),
            interest_rate_annual=Decimal("0.10"),
            issue_date="2026-11-01",
            due_date="2027-11-01",
        )
        with self.assertRaises(ValueError):
            self.engine.issue_cir(excessive_cir)

    def test_anti_double_affection_inviolability(self) -> None:
        self.engine.issue_cir(self.cir)
        # Segunda CIR tentando afetar a mesma parcela já vinculada deve ser rejeitada
        second_cir = CedulaImobiliariaRural(
            cir_id="CIR-2026-0002",
            issuer_tax_id="12.345.678/0001-90",
            creditor_tax_id="98.765.432/0001-10",
            parcel_id="PARCEL-MT-001",
            nominal_amount=Decimal("2000000.00"),
            interest_rate_annual=Decimal("0.1250"),
            issue_date="2026-11-01",
            due_date="2027-11-01",
        )
        with self.assertRaises(ValueError):
            self.engine.issue_cir(second_cir)

    def test_interest_accrual_and_regular_settlement(self) -> None:
        self.engine.issue_cir(self.cir)
        # Acumula juros de 126 dias úteis (meio ano útil)
        accrued = self.engine.accrue_interest("CIR-2026-0001", business_days=126)
        self.assertGreater(accrued, Decimal("12000000.00"))

        # Liquidação regular DvP com valor correto
        record = self.engine.settle_cir("CIR-2026-0001", settlement_amount=accrued)
        self.assertEqual(record["status"], "SETTLED")
        self.assertEqual(self.cir.status, CirStatus.SETTLED)
        self.assertEqual(Decimal(record["issuer_paid"]), Decimal(record["creditor_received"]))

    def test_fiduciary_execution_default(self) -> None:
        self.engine.issue_cir(self.cir)
        self.engine.accrue_interest("CIR-2026-0001", business_days=252) # 1 ano: ~13.5M
        expected_debt = self.cir.accrued_amount

        # Leilão da terra arrecada R$ 15.000.000,00
        exec_record = self.engine.execute_default("CIR-2026-0001", auction_proceeds=Decimal("15000000.00"))
        self.assertEqual(exec_record["status"], "EXECUTED_IN_DEFAULT")
        self.assertEqual(Decimal(exec_record["creditor_recovered"]), expected_debt)
        self.assertEqual(
            Decimal(exec_record["surplus_returned_to_issuer"]),
            Decimal("15000000.00") - expected_debt,
        )

        audit = self.engine.verify_invariants()
        self.assertTrue(audit["all_invariants_hold"])
        self.assertTrue(audit["settlement_zero_sum_verified"])

    def test_invariants_and_serialization(self) -> None:
        self.engine.issue_cir(self.cir)
        self.engine.settle_cir("CIR-2026-0001", settlement_amount=Decimal("12000000.00"))

        audit = self.engine.verify_invariants()
        self.assertTrue(audit["all_invariants_hold"])
        self.assertTrue(audit["affected_patrimony_limit_verified"])
        self.assertTrue(audit["anti_double_affection_verified"])
        self.assertTrue(audit["settlement_zero_sum_verified"])

        d = self.engine.to_dict()
        restored = RuralRealEstateCirEngine.from_dict(d)
        self.assertIn("CIR-2026-0001", restored.cirs)
        self.assertIn("PARCEL-MT-001", restored.parcels)
        self.assertEqual(len(restored.settlement_records), 1)

    def test_fixture_loading(self) -> None:
        fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "rural_real_estate_cir.json"
        self.assertTrue(fixture_path.exists())
        with open(fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        engine = RuralRealEstateCirEngine.from_dict(data)
        audit = engine.verify_invariants()
        self.assertTrue(audit["all_invariants_hold"])
        self.assertEqual(len(engine.parcels), 2)
        self.assertEqual(len(engine.cirs), 2)


if __name__ == "__main__":
    unittest.main()
