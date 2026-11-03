"""Testes unitários determinísticos para o módulo de Créditos de Descarbonização CBIO / RenovaBio (FL-036)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unittest

from src.cbio_carbon_energy import (
    AnpTargetCompliance,
    BiofuelType,
    CbioBatch,
    CbioConservationError,
    CbioInsufficientBalanceError,
    CbioParticipantRole,
    CbioPlatformEngine,
    CbioRetiredAssetError,
    CbioRetirementReceipt,
    CbioStatus,
    CbioValidationError,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "cbio_retirement_batch.json"
)


class TestCbioCarbonEnergy(unittest.TestCase):
    """Suíte de testes para Escrituração, Negociação, Aposentadoria e Meta ANP de CBIOs na B3."""

    def setUp(self) -> None:
        self.engine = CbioPlatformEngine()
        self.valid_nfe = "35261051466860000156550010001234561001234567"

    def _issue_sample_batch(
        self,
        batch_id: str = "CBIO-TEST-0001",
        nfe_key: str | None = None,
        quantity: int = 5000,
    ) -> CbioBatch:
        return self.engine.issue_primary_batch(
            batch_id=batch_id,
            issuer_id="USINA-TEST-01",
            issuer_name="Usina Bioenergia Teste S.A.",
            issuer_cnpj="51466860000156",
            issuer_role=CbioParticipantRole.EMISSOR_PRIMARIO,
            bookkeeper_id="BANCO-ESC-01",
            bookkeeper_name="Banco Escriturador S.A.",
            bookkeeper_cnpj="61194353000164",
            anp_certificate_code="ANP-CERT-2026-0001",
            nfe_key=nfe_key or self.valid_nfe,
            biofuel_type=BiofuelType.ETANOL_HIDRATADO,
            energy_efficiency_note_gco2_mj="62.50",
            quantity=quantity,
            reference_unit_price_brl="95.00",
            issuance_timestamp="2026-03-10T10:00:00Z",
        )

    def test_primary_issuance_and_nfe_validation(self) -> None:
        """Valida a escrituração primária, chave NF-e de 44 dígitos e bloqueio de duplicidade."""
        batch = self._issue_sample_batch()
        self.assertEqual(batch.batch_id, "CBIO-TEST-0001")
        self.assertEqual(batch.status, CbioStatus.EM_CIRCULACAO)
        self.assertEqual(batch.initial_quantity, 5000)
        self.assertEqual(batch.circulating_quantity, 5000)
        self.assertEqual(batch.retired_quantity, 0)
        self.assertEqual(batch.equivalent_tco2e, Decimal("5000.00"))
        self.assertTrue(batch.is_tradable)

        # Chave NF-e com menos de 44 dígitos deve falhar
        with self.assertRaises(CbioValidationError):
            self._issue_sample_batch(batch_id="CBIO-BAD-NFE", nfe_key="1234567890")

        # Duplicidade de NF-e em novo lote deve ser bloqueada (prevenção de duplo lastro fiscal)
        with self.assertRaises(CbioValidationError):
            self._issue_sample_batch(batch_id="CBIO-DUP-NFE", nfe_key=self.valid_nfe)

        # Participante que não é EMISSOR_PRIMARIO não pode originar emissão primária
        with self.assertRaises(CbioValidationError):
            self.engine.issue_primary_batch(
                batch_id="CBIO-INVALID-ROLE",
                issuer_id="DIST-01",
                issuer_name="Distribuidora S.A.",
                issuer_cnpj="34274233000102",
                issuer_role=CbioParticipantRole.PARTE_OBRIGADA,
                bookkeeper_id="BANCO-ESC-01",
                bookkeeper_name="Banco Escriturador S.A.",
                bookkeeper_cnpj="61194353000164",
                anp_certificate_code="ANP-CERT-2026-0002",
                nfe_key="35261051466860000156550010009999991009999999",
                biofuel_type=BiofuelType.BIODIESEL,
                energy_efficiency_note_gco2_mj="55.00",
                quantity=1000,
                reference_unit_price_brl="90.00",
                issuance_timestamp="2026-03-10T10:00:00Z",
            )

    def test_secondary_transfer_split_and_conservation(self) -> None:
        """Testa venda parcial com desmembramento de sub-lote e manutenção da conservação."""
        self._issue_sample_batch(quantity=5000)

        trade = self.engine.transfer_secondary(
            trade_id="TRD-001",
            batch_id="CBIO-TEST-0001",
            new_batch_id="CBIO-TEST-0001-SPLIT",
            seller_id="USINA-TEST-01",
            buyer_id="DIST-OBRIGADA-01",
            buyer_name="Distribuidora Combustíveis S.A.",
            buyer_cnpj="34274233000102",
            buyer_role=CbioParticipantRole.PARTE_OBRIGADA,
            quantity=3200,
            unit_price_brl="101.50",
            trade_timestamp="2026-06-15T14:30:00Z",
        )

        self.assertEqual(trade.financial_volume_brl, Decimal("324800.00"))
        self.assertEqual(trade.target_batch_id, "CBIO-TEST-0001-SPLIT")

        origin_batch = self.engine.batches["CBIO-TEST-0001"]
        split_batch = self.engine.batches["CBIO-TEST-0001-SPLIT"]

        self.assertEqual(origin_batch.circulating_quantity, 1800)
        self.assertEqual(origin_batch.current_holder_id, "USINA-TEST-01")
        self.assertEqual(split_batch.circulating_quantity, 3200)
        self.assertEqual(split_batch.current_holder_id, "DIST-OBRIGADA-01")
        self.assertEqual(split_batch.root_batch_id, "CBIO-TEST-0001")
        self.assertEqual(split_batch.nfe_key, self.valid_nfe)

        self.assertEqual(self.engine.total_issued_cbios, 5000)
        self.assertEqual(self.engine.total_in_circulation_cbios, 5000)
        self.assertEqual(self.engine.total_retired_cbios, 0)
        self.assertTrue(self.engine.verify_conservation_invariant())

    def test_golden_rule_retired_cbio_cannot_be_traded_or_reretired(self) -> None:
        """Valida a Regra de Ouro: CBIO aposentado jamais pode ser renegociado ou reutilizado."""
        self._issue_sample_batch(quantity=2000)

        # Transfere lote integral para Distribuidor
        self.engine.transfer_secondary(
            trade_id="TRD-FULL-01",
            batch_id="CBIO-TEST-0001",
            seller_id="USINA-TEST-01",
            buyer_id="DIST-OBRIGADA-01",
            buyer_name="Distribuidora Combustíveis S.A.",
            buyer_cnpj="34274233000102",
            buyer_role=CbioParticipantRole.PARTE_OBRIGADA,
            quantity=2000,
            unit_price_brl="100.00",
            trade_timestamp="2026-07-01T11:00:00Z",
        )

        # Aposenta integralmente os 2.000 CBIOs
        receipt = self.engine.retire_cbios(
            instruction_id="RET-001",
            receipt_id="CERT-B3-0001",
            batch_id="CBIO-TEST-0001",
            holder_id="DIST-OBRIGADA-01",
            quantity=2000,
            compliance_year=2026,
            beneficiary_justification="Cumprimento de meta compulsória ANP 2026.",
            retirement_timestamp="2026-12-10T16:00:00Z",
        )

        self.assertIsInstance(receipt, CbioRetirementReceipt)
        self.assertEqual(receipt.status, CbioStatus.APOSENTADO)
        self.assertEqual(receipt.retired_quantity, 2000)
        self.assertEqual(receipt.equivalent_tco2e, Decimal("2000.00"))

        batch = self.engine.batches["CBIO-TEST-0001"]
        self.assertEqual(batch.status, CbioStatus.APOSENTADO)
        self.assertEqual(batch.circulating_quantity, 0)
        self.assertEqual(batch.retired_quantity, 2000)
        self.assertFalse(batch.is_tradable)

        # Tentativa de renegociar CBIO aposentado no mercado secundário -> CbioRetiredAssetError
        with self.assertRaises(CbioRetiredAssetError):
            self.engine.transfer_secondary(
                trade_id="TRD-ILLEGAL-02",
                batch_id="CBIO-TEST-0001",
                seller_id="DIST-OBRIGADA-01",
                buyer_id="OUTRA-DIST-02",
                buyer_name="Outra Distribuidora S.A.",
                buyer_cnpj="33337122000127",
                buyer_role=CbioParticipantRole.PARTE_OBRIGADA,
                quantity=500,
                unit_price_brl="105.00",
                trade_timestamp="2026-12-15T10:00:00Z",
            )

        # Tentativa de aposentar novamente o mesmo lote para outro ano -> CbioRetiredAssetError
        with self.assertRaises(CbioRetiredAssetError):
            self.engine.retire_cbios(
                instruction_id="RET-ILLEGAL-02",
                batch_id="CBIO-TEST-0001",
                holder_id="DIST-OBRIGADA-01",
                quantity=2000,
                compliance_year=2027,
                beneficiary_justification="Tentativa ilícita de dupla contagem.",
                retirement_timestamp="2027-01-10T10:00:00Z",
            )

        # Invariante de conservação permanece intacta
        self.assertEqual(self.engine.total_issued_cbios, 2000)
        self.assertEqual(self.engine.total_in_circulation_cbios, 0)
        self.assertEqual(self.engine.total_retired_cbios, 2000)
        self.assertTrue(self.engine.verify_conservation_invariant())

    def test_batch_fixture_processing_and_anp_target_compliance(self) -> None:
        """Processa o lote completo fixtures/cbio_retirement_batch.json e valida metas ANP."""
        summary = self.engine.process_batch_file(FIXTURE_PATH)

        # Conservação Global: 28.000 emitidos == 6.800 em circulação + 21.200 aposentados
        self.assertTrue(summary.conservation_invariant_ok)
        self.assertEqual(summary.total_issued_cbios, 28000)
        self.assertEqual(summary.total_in_circulation_cbios, 6800)
        self.assertEqual(summary.total_retired_cbios, 21200)
        self.assertEqual(summary.total_retired_tco2e, Decimal("21200.00"))

        # Volume Financeiro Secundário:
        # 10000*98.50 (985000) + 8000*99.00 (792000) + 4000*100.20 (400800) + 1500*104.00 (156000)
        # = R$ 2.333.800,00
        self.assertEqual(summary.total_secondary_volume_brl, Decimal("2333800.00"))
        self.assertEqual(len(summary.trades), 4)
        self.assertEqual(len(summary.retirement_receipts), 4)
        self.assertEqual(len(summary.anp_compliance_reports), 3)

        reports_by_id: dict[str, AnpTargetCompliance] = {
            r.distributor_id: r for r in summary.anp_compliance_reports
        }

        # 1. DIST-VIBRA-01: Meta 10.000, Aposentados 10.000 -> 100,00% cumprida
        vibra = reports_by_id["DIST-VIBRA-01"]
        self.assertEqual(vibra.mandatory_target_cbios, 10000)
        self.assertEqual(vibra.retired_cbios, 10000)
        self.assertEqual(vibra.circulating_held_cbios, 0)
        self.assertEqual(vibra.deficit_cbios, 0)
        self.assertEqual(vibra.compliance_percentage, Decimal("100.00"))
        self.assertTrue(vibra.is_fully_compliant)
        self.assertTrue(vibra.within_carryover_tolerance)
        self.assertEqual(vibra.carryover_to_next_year_cbios, 0)
        self.assertEqual(vibra.estimated_fine_brl, Decimal("0.00"))

        # 2. DIST-IPIRANGA-02: Meta 8.000, Aposentados 7.200 (800 em circulação) -> 90,00%
        # Dentro da tolerância legal de carry-over (até 15% para o ano seguinte, pois >= 85%)
        ipiranga = reports_by_id["DIST-IPIRANGA-02"]
        self.assertEqual(ipiranga.mandatory_target_cbios, 8000)
        self.assertEqual(ipiranga.retired_cbios, 7200)
        self.assertEqual(ipiranga.circulating_held_cbios, 800)
        self.assertEqual(ipiranga.deficit_cbios, 800)
        self.assertEqual(ipiranga.compliance_percentage, Decimal("90.00"))
        self.assertFalse(ipiranga.is_fully_compliant)
        self.assertTrue(ipiranga.within_carryover_tolerance)
        self.assertEqual(ipiranga.carryover_to_next_year_cbios, 800)
        self.assertEqual(ipiranga.estimated_fine_brl, Decimal("0.00"))

        # 3. DIST-ALESAT-03: Meta 4.000, Aposentados 2.500 (1.500 ainda em circulação não aposentados)
        # Cumprimento = 62,50% (< 85%) -> Inadimplente, multa sobre déficit de 1.500 CBIOs = R$ 150.000,00
        alesat = reports_by_id["DIST-ALESAT-03"]
        self.assertEqual(alesat.mandatory_target_cbios, 4000)
        self.assertEqual(alesat.retired_cbios, 2500)
        self.assertEqual(alesat.circulating_held_cbios, 1500)
        self.assertEqual(alesat.deficit_cbios, 1500)
        self.assertEqual(alesat.compliance_percentage, Decimal("62.50"))
        self.assertFalse(alesat.is_fully_compliant)
        self.assertFalse(alesat.within_carryover_tolerance)
        self.assertEqual(alesat.carryover_to_next_year_cbios, 0)
        self.assertEqual(alesat.estimated_fine_brl, Decimal("150000.00"))

    def test_partial_retirement_then_remaining_retirement(self) -> None:
        """Verifica que um lote parcialmente aposentado permite aposentar o saldo restante depois."""
        self.engine.process_batch_file(FIXTURE_PATH)

        # DIST-ALESAT-03 ainda possui 1.500 CBIOs em circulação no lote CBIO-2026-0003-S1
        batch = self.engine.batches["CBIO-2026-0003-S1"]
        self.assertEqual(batch.status, CbioStatus.EM_CIRCULACAO)
        self.assertEqual(batch.circulating_quantity, 1500)
        self.assertEqual(batch.retired_quantity, 2500)

        # Tentativa de aposentar 1.600 (acima do saldo em circulação 1.500) falha
        with self.assertRaises(CbioInsufficientBalanceError):
            self.engine.retire_cbios(
                instruction_id="RET-OVER-01",
                batch_id="CBIO-2026-0003-S1",
                holder_id="DIST-ALESAT-03",
                quantity=1600,
                compliance_year=2026,
                beneficiary_justification="Tentativa acima do saldo disponível.",
                retirement_timestamp="2026-12-28T10:00:00Z",
            )

        # Aposenta os 1.500 restantes -> atinge 100% da meta ANP (4.000 CBIOs) e lote vira APOSENTADO
        self.engine.retire_cbios(
            instruction_id="RET-COMPLEMENT-01",
            batch_id="CBIO-2026-0003-S1",
            holder_id="DIST-ALESAT-03",
            quantity=1500,
            compliance_year=2026,
            beneficiary_justification="Complementação para cumprimento integral da meta 2026.",
            retirement_timestamp="2026-12-28T11:00:00Z",
        )

        self.assertEqual(batch.status, CbioStatus.APOSENTADO)
        self.assertEqual(batch.circulating_quantity, 0)
        self.assertEqual(batch.retired_quantity, 4000)

        updated_compliance = self.engine.evaluate_anp_compliance("DIST-ALESAT-03", 2026)
        self.assertEqual(updated_compliance.retired_cbios, 4000)
        self.assertEqual(updated_compliance.compliance_percentage, Decimal("100.00"))
        self.assertTrue(updated_compliance.is_fully_compliant)
        self.assertEqual(updated_compliance.estimated_fine_brl, Decimal("0.00"))
        self.assertTrue(self.engine.verify_conservation_invariant())


if __name__ == "__main__":
    unittest.main()
