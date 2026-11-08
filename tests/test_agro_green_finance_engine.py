"""Suíte de Testes Unitários e Invariantes Formais do Motor de Agronegócio e Finanças Verdes (FL-026, FL-036, FL-087).

Testa exaustivamente:
1. Cédula de Produto Rural (CPR Digital / Lei nº 13.986/2020 - Nova Lei do Agro):
   - Registro de CPR Física com armazém geral e CPR Financeira com índice CEPEA;
   - Fracionamento eletrônico determinístico (desdobramento em cotas/frações sem resíduo);
   - Averbação de garantias (Penhor Rural, Alienação Fiduciária de Safra e Patrimônio Rural em Afetação);
   - Invariantes LAW-044 (conservação de split), LAW-048 (congelamento) e LAW-065 (teto de safra).
2. Créditos de Descarbonização (CBIO / RenovaBio - Lei nº 13.576/2017):
   - Escrituração primária com NF-e e ANP;
   - Mercado secundário livre de dupla contagem;
   - Aposentadoria compulsória para distribuidores e conformidade de metas ANP (carry-over e multas);
   - Invariante LAW-048 (irrevogabilidade da aposentadoria) e conservação emitida vs aposentada.
3. Contratos Bilaterais de Energia Elétrica (CCEE / Aneel - ACL):
   - Registro de contratos no ACL e trava fiduciária de recebíveis (LAW-065);
   - Balanço de potência e energia por agente e submercado SIN;
   - Liquidação de diferenças no MCP CCEE ao PLD e conservação multilateral soma-zero (LAW-066).
4. Persistência, serialização e auditoria global de integridade com fixtures JSON.

100% Python Standard Library pura.
"""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from src.agro_green_finance_engine import (
    AgroGreenFinanceEngine,
    AgroGreenFinanceError,
    AnpComplianceReport,
    AverbaçãoGarantia,
    BilateralPowerContract,
    BiofuelType,
    CbioBatch,
    CbioBatchStatus,
    CbioRetirementCertificate,
    CbioRetirementError,
    CbioTradeRecord,
    CbioTransferError,
    CbioValidationError,
    CommodityType,
    CprFraction,
    CprFractionationError,
    CprGuarantee,
    CprStatus,
    CprType,
    CprValidationError,
    DeliveryLocation,
    DigitalCpr,
    EnergySource,
    GuaranteeType,
    GuaranteeValidationError,
    MeasurementUnit,
    Party,
    PowerBalanceReport,
    PowerContractValidationError,
    PowerSettlementError,
    SinSubmarket,
    round_cents,
    round_energy,
    round_qty,
)
from src.invariants import InvariantViolationError


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "agro_green_finance_engine.json"
)


class TestAgroGreenFinanceEngine(unittest.TestCase):
    """Suíte principal de testes do AgroGreenFinanceEngine."""

    def setUp(self) -> None:
        self.engine = AgroGreenFinanceEngine()

        self.producer_pj = Party(
            name="Cooperativa Agrícola do Cerrado",
            document="12345678000195",
            party_type="PJ",
            city="Rio Verde",
            state="GO",
        )
        self.bank_creditor = Party(
            name="Banco do Agro S.A.",
            document="98765432000198",
            party_type="PJ",
            city="São Paulo",
            state="SP",
        )
        self.valid_car = "MT-5107909-E8B1A2C3D4E5F6789012345678901234"
        self.valid_nfe = "35261051466860000156550010001234561001234567"

    # =========================================================================
    # 1. TESTES: CÉDULA DE PRODUTO RURAL (CPR DIGITAL)
    # =========================================================================

    def test_register_cpr_physical_success(self) -> None:
        """Registra CPR Física com entrega em armazém geral e dados completos."""
        deliv = DeliveryLocation(
            warehouse_name="Armazéns Gerais Rio Verde",
            address="Rod. GO-174 Km 5",
            city="Rio Verde",
            state="GO",
            ibge_code="5218805",
            warehouse_cnpj="98765432000198",
            delivery_start_date="2026-09-01",
            delivery_end_date="2026-10-15",
        )
        cpr = self.engine.register_cpr(
            cpr_id="CPR-MILHO-001",
            cpr_type=CprType.FISICA,
            issuer=self.producer_pj,
            creditor=self.bank_creditor,
            commodity=CommodityType.MILHO,
            quantity=15000,
            unit=MeasurementUnit.SACAS_60KG,
            crop_year="2026/2027",
            issue_date="2026-05-01",
            maturity_date="2026-10-15",
            estimated_harvest_value="1200000.00",
            unit_price="70.00",
            delivery_location=deliv,
        )

        self.assertEqual(cpr.cpr_id, "CPR-MILHO-001")
        self.assertEqual(cpr.cpr_type, CprType.FISICA)
        self.assertEqual(cpr.commodity, CommodityType.MILHO)
        self.assertEqual(cpr.quantity, Decimal("15000.0000"))
        self.assertEqual(cpr.notional_amount, Decimal("1050000.00"))
        self.assertEqual(cpr.status, CprStatus.ACTIVE)
        self.assertTrue(len(cpr.electronic_signature_hash) == 64)

    def test_register_cpr_physical_requires_delivery_location(self) -> None:
        """CPR Física sem local de entrega deve ser sumariamente rejeitada."""
        with self.assertRaises(CprValidationError):
            self.engine.register_cpr(
                cpr_id="CPR-FAIL-001",
                cpr_type="FISICA",
                issuer=self.producer_pj,
                creditor=self.bank_creditor,
                commodity="MILHO",
                quantity=1000,
                unit="SACAS_60KG",
                crop_year="2026",
                issue_date="2026-05-01",
                maturity_date="2026-10-15",
                estimated_harvest_value="100000.00",
                delivery_location=None,
            )

    def test_register_cpr_financial_success(self) -> None:
        """Registra CPR Financeira referenciada em índice CEPEA com sucesso."""
        cpr = self.engine.register_cpr(
            cpr_id="CPR-SOJA-FIN-001",
            cpr_type=CprType.FINANCEIRA,
            issuer=self.producer_pj,
            creditor=self.bank_creditor,
            commodity=CommodityType.SOJA,
            quantity=10000,
            unit=MeasurementUnit.SACAS_60KG,
            crop_year="2026/2027",
            issue_date="2026-05-10",
            maturity_date="2026-11-20",
            estimated_harvest_value="1600000.00",
            unit_price="140.00",
            price_index="CEPEA_PARANAGUA",
        )
        self.assertEqual(cpr.cpr_type, CprType.FINANCEIRA)
        self.assertEqual(cpr.notional_amount, Decimal("1400000.00"))
        self.assertEqual(cpr.price_index, "CEPEA_PARANAGUA")

    def test_cpr_invalid_crop_year_and_negative_quantity(self) -> None:
        """Valida que anos-safra incorretos e quantidades negativas são bloqueados."""
        with self.assertRaises(CprValidationError):
            self.engine.register_cpr(
                cpr_id="CPR-ERR-YEAR",
                cpr_type="FINANCEIRA",
                issuer=self.producer_pj,
                creditor=self.bank_creditor,
                commodity="SOJA",
                quantity=500,
                unit="SACAS_60KG",
                crop_year="SAFRA_INVALIDA_2026",
                issue_date="2026-05-01",
                maturity_date="2026-11-01",
                estimated_harvest_value="100000.00",
            )

        with self.assertRaises(CprValidationError):
            self.engine.register_cpr(
                cpr_id="CPR-ERR-QTY",
                cpr_type="FINANCEIRA",
                issuer=self.producer_pj,
                creditor=self.bank_creditor,
                commodity="SOJA",
                quantity=-50,
                unit="SACAS_60KG",
                crop_year="2026/2027",
                issue_date="2026-05-01",
                maturity_date="2026-11-01",
                estimated_harvest_value="100000.00",
            )

    # =========================================================================
    # 2. TESTES: AVERBAÇÃO DE GARANTIAS E INVARIANTE LAW-065
    # =========================================================================

    def test_averbar_garantias_multiplas(self) -> None:
        """Averba Penhor Rural, Alienação Fiduciária e Patrimônio de Afetação na CPR."""
        cpr = self.engine.register_cpr(
            cpr_id="CPR-GARANTIAS-01",
            cpr_type=CprType.FINANCEIRA,
            issuer=self.producer_pj,
            creditor=self.bank_creditor,
            commodity=CommodityType.SOJA,
            quantity=20000,
            unit=MeasurementUnit.SACAS_60KG,
            crop_year="2026/2027",
            issue_date="2026-05-01",
            maturity_date="2026-11-01",
            estimated_harvest_value="3000000.00",
            unit_price="130.00",
        )

        # 1. Penhor Rural
        g1 = self.engine.averbar_garantia(
            cpr_id="CPR-GARANTIAS-01",
            guarantee_id="GAR-001",
            guarantee_type=GuaranteeType.PENHOR_RURAL,
            description="Penhor agrícola de 1º grau sobre a lavoura",
            guarantee_value="1000000.00",
            registry_office="Cartório de Registro de Imóveis de Sorriso/MT",
            registration_number="R-1-45120",
            car_code=self.valid_car,
        )
        self.assertEqual(g1.guarantee_value, Decimal("1000000.00"))

        # 2. Alienação Fiduciária de Safra
        g2 = self.engine.averbar_garantia(
            cpr_id="CPR-GARANTIAS-01",
            guarantee_id="GAR-002",
            guarantee_type=GuaranteeType.ALIENACAO_FIDUCIARIA_SAFRA,
            description="Alienação fiduciária da safra futura",
            guarantee_value="800000.00",
            registry_office="Cartório de Registro de Imóveis de Sorriso/MT",
            registration_number="AV-2-45120",
        )
        self.assertEqual(g2.guarantee_value, Decimal("800000.00"))

        # 3. Patrimônio Rural em Afetação (Lei nº 13.986/2020)
        g3 = self.engine.averbar_garantia(
            cpr_id="CPR-GARANTIAS-01",
            guarantee_id="GAR-003",
            guarantee_type=GuaranteeType.PATRIMONIO_RURAL_AFETACAO,
            description="Fração de imóvel rural em afetação",
            guarantee_value="700000.00",
            registry_office="Cartório de Registro de Imóveis de Sorriso/MT",
            registration_number="R-3-45120",
            car_code=self.valid_car,
            sigef_parcel_code="SIGEF-MT-2026-1234",
            affected_area_ha="300.0000",
        )
        self.assertEqual(g3.affected_area_ha, Decimal("300.0000"))

        self.assertEqual(cpr.total_guarantees_value, Decimal("2500000.00"))
        self.assertLessEqual(cpr.total_guarantees_value, cpr.estimated_harvest_value)

    def test_law_065_guarantee_cannot_exceed_harvest_value(self) -> None:
        """LAW-065: Bloqueia averbação de garantia cujo valor exceda o lastro avaliado."""
        self.engine.register_cpr(
            cpr_id="CPR-LAW065-TEST",
            cpr_type=CprType.FINANCEIRA,
            issuer=self.producer_pj,
            creditor=self.bank_creditor,
            commodity=CommodityType.SOJA,
            quantity=5000,
            unit=MeasurementUnit.SACAS_60KG,
            crop_year="2026/2027",
            issue_date="2026-05-01",
            maturity_date="2026-11-01",
            estimated_harvest_value="500000.00",
            notional_amount="450000.00",
        )

        with self.assertRaises(InvariantViolationError) as ctx:
            self.engine.averbar_garantia(
                cpr_id="CPR-LAW065-TEST",
                guarantee_id="GAR-OVERFLOW",
                guarantee_type=GuaranteeType.PENHOR_RURAL,
                description="Tentativa de sobre-averbação de penhor",
                guarantee_value="550000.00",
                registry_office="Cartório de Sorriso/MT",
                registration_number="R-1-999",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-065")

    def test_patrimonio_afetacao_requires_car_and_sigef(self) -> None:
        """Patrimônio de Afetação exige estritamente código SIGEF e cadastro CAR válidos."""
        self.engine.register_cpr(
            cpr_id="CPR-AFETACAO-CHECK",
            cpr_type=CprType.FINANCEIRA,
            issuer=self.producer_pj,
            creditor=self.bank_creditor,
            commodity=CommodityType.MILHO,
            quantity=10000,
            unit=MeasurementUnit.SACAS_60KG,
            crop_year="2026/2027",
            issue_date="2026-05-01",
            maturity_date="2026-11-01",
            estimated_harvest_value="1000000.00",
        )

        # Sem SIGEF
        with self.assertRaises(GuaranteeValidationError):
            self.engine.averbar_garantia(
                cpr_id="CPR-AFETACAO-CHECK",
                guarantee_id="GAR-FAIL-SIGEF",
                guarantee_type=GuaranteeType.PATRIMONIO_RURAL_AFETACAO,
                description="Sem SIGEF",
                guarantee_value="200000.00",
                registry_office="Cartório Sorriso",
                registration_number="R-1",
                car_code=self.valid_car,
                sigef_parcel_code=None,
            )

        # Sem CAR
        with self.assertRaises(GuaranteeValidationError):
            self.engine.averbar_garantia(
                cpr_id="CPR-AFETACAO-CHECK",
                guarantee_id="GAR-FAIL-CAR",
                guarantee_type=GuaranteeType.PATRIMONIO_RURAL_AFETACAO,
                description="Sem CAR",
                guarantee_value="200000.00",
                registry_office="Cartório Sorriso",
                registration_number="R-1",
                car_code=None,
                sigef_parcel_code="SIGEF-12345",
            )

    # =========================================================================
    # 3. TESTES: FRACIONAMENTO ELETRÔNICO (LAW-044, LAW-048, LAW-066)
    # =========================================================================

    def test_cpr_fractionation_success_and_law_044_law_066(self) -> None:
        """Fracionamento divide CPR em 3 frações conservando quantidades e garantias."""
        cpr = self.engine.register_cpr(
            cpr_id="CPR-SPLIT-01",
            cpr_type=CprType.FINANCEIRA,
            issuer=self.producer_pj,
            creditor=self.bank_creditor,
            commodity=CommodityType.CAFE,
            quantity=10000,
            unit=MeasurementUnit.SACAS_60KG,
            crop_year="2026/2027",
            issue_date="2026-04-01",
            maturity_date="2026-12-01",
            estimated_harvest_value="12000000.00",
            notional_amount="10000000.00",
        )
        self.engine.averbar_garantia(
            cpr_id="CPR-SPLIT-01",
            guarantee_id="GAR-SPLIT-01",
            guarantee_type=GuaranteeType.PENHOR_RURAL,
            description="Penhor agrícola",
            guarantee_value="6000000.00",
            registry_office="Cartório Varginha",
            registration_number="R-1",
        )

        fractions_spec = [
            {
                "fraction_id": "CPR-SPLIT-01-F01",
                "quantity": "5000.0000",
                "notional_amount": "5000000.00",
                "creditor": {
                    "name": "Fiagro Alpha",
                    "document": "33453598000123",
                    "party_type": "PJ",
                    "city": "São Paulo",
                    "state": "SP",
                },
            },
            {
                "fraction_id": "CPR-SPLIT-01-F02",
                "quantity": "3000.0000",
                "notional_amount": "3000000.00",
                "creditor": {
                    "name": "Fiagro Beta",
                    "document": "51466860000156",
                    "party_type": "PJ",
                    "city": "Campinas",
                    "state": "SP",
                },
            },
            {
                "fraction_id": "CPR-SPLIT-01-F03",
                "quantity": "2000.0000",
                "notional_amount": "2000000.00",
                "creditor": {
                    "name": "Securitizadora Gama",
                    "document": "07388609000161",
                    "party_type": "PJ",
                    "city": "Curitiba",
                    "state": "PR",
                },
            },
        ]

        fractions = self.engine.fracionar_cpr("CPR-SPLIT-01", fractions_spec)

        self.assertEqual(len(fractions), 3)
        self.assertEqual(cpr.status, CprStatus.FRACTIONATED)

        # Checagem LAW-044 e LAW-066
        self.engine.assert_law_044_cpr_fractionation_conservation("CPR-SPLIT-01")

        # Rateio de garantias
        total_guar_allocated = sum((f.allocated_guarantee_value for f in fractions), Decimal("0.00"))
        self.assertEqual(total_guar_allocated, Decimal("6000000.00"))
        self.assertEqual(fractions[0].allocated_guarantee_value, Decimal("3000000.00"))
        self.assertEqual(fractions[1].allocated_guarantee_value, Decimal("1800000.00"))
        self.assertEqual(fractions[2].allocated_guarantee_value, Decimal("1200000.00"))

    def test_law_048_cpr_fractionated_is_frozen(self) -> None:
        """LAW-048: CPR fracionada não pode ser re-fracionada."""
        cpr = self.engine.register_cpr(
            cpr_id="CPR-FREEZE-TEST",
            cpr_type=CprType.FINANCEIRA,
            issuer=self.producer_pj,
            creditor=self.bank_creditor,
            commodity=CommodityType.SOJA,
            quantity=2000,
            unit=MeasurementUnit.SACAS_60KG,
            crop_year="2026/2027",
            issue_date="2026-05-01",
            maturity_date="2026-11-01",
            estimated_harvest_value="300000.00",
            notional_amount="260000.00",
        )
        spec = [
            {
                "quantity": "1000.0000",
                "notional_amount": "130000.00",
                "creditor": {"name": "C1", "document": "12345678000195"},
            },
            {
                "quantity": "1000.0000",
                "notional_amount": "130000.00",
                "creditor": {"name": "C2", "document": "98765432000198"},
            },
        ]
        self.engine.fracionar_cpr("CPR-FREEZE-TEST", spec)

        # Tentativa de re-fracionar deve falhar por violação de congelamento (LAW-048)
        with self.assertRaises(CprFractionationError):
            self.engine.fracionar_cpr("CPR-FREEZE-TEST", spec)

    def test_law_044_fraction_quantity_mismatch_rejected(self) -> None:
        """LAW-044: Se a soma das quantidades fracionadas divergir da CPR matriz, rejeita."""
        self.engine.register_cpr(
            cpr_id="CPR-QTY-DIVERGENCE",
            cpr_type=CprType.FINANCEIRA,
            issuer=self.producer_pj,
            creditor=self.bank_creditor,
            commodity=CommodityType.MILHO,
            quantity=1000,
            unit=MeasurementUnit.SACAS_60KG,
            crop_year="2026/2027",
            issue_date="2026-05-01",
            maturity_date="2026-11-01",
            estimated_harvest_value="80000.00",
            notional_amount="75000.00",
        )
        invalid_spec = [
            {
                "quantity": "600.0000",
                "notional_amount": "37500.00",
                "creditor": {"name": "C1", "document": "12345678000195"},
            },
            {
                "quantity": "399.0000",  # Soma dá 999 != 1000
                "notional_amount": "37500.00",
                "creditor": {"name": "C2", "document": "98765432000198"},
            },
        ]
        with self.assertRaises(InvariantViolationError) as ctx:
            self.engine.fracionar_cpr("CPR-QTY-DIVERGENCE", invalid_spec)
        self.assertEqual(ctx.exception.law_code, "LAW-044")

    # =========================================================================
    # 4. TESTES: CRÉDITOS DE DESCARBONIZAÇÃO (CBIO / RENOVABIO)
    # =========================================================================

    def test_cbio_primary_issuance_and_secondary_transfer(self) -> None:
        """Escrituração primária de CBIO e transferência no mercado secundário sem dupla contagem."""
        batch = self.engine.issue_cbio_batch(
            batch_id="CBIO-USINA-01",
            issuer_id="USINA-01",
            issuer_name="Usina Bioenergia S.A.",
            issuer_cnpj="51466860000156",
            bookkeeper_id="BANCO-ESC-01",
            bookkeeper_name="Banco Escriturador S.A.",
            bookkeeper_cnpj="61194353000164",
            anp_certificate_code="ANP-CERT-2026-001",
            nfe_key=self.valid_nfe,
            biofuel_type=BiofuelType.ETANOL_HIDRATADO,
            energy_efficiency_score="62.50",
            quantity=10000,
            unit_price_brl="95.00",
        )
        self.assertEqual(batch.circulating_quantity, 10000)
        self.assertEqual(batch.retired_quantity, 0)

        # Transferência parcial de 4.000 CBIOs para Distribuidora
        trade = self.engine.transfer_cbio(
            trade_id="TRD-001",
            batch_id="CBIO-USINA-01",
            seller_cnpj="51466860000156",
            buyer_cnpj="33000167000101",
            buyer_name="Distribuidora de Petróleo S.A.",
            quantity=4000,
            unit_price_brl="96.00",
        )

        self.assertEqual(trade.quantity, 4000)
        self.assertEqual(trade.total_amount_brl, Decimal("384000.00"))

        # O lote original agora tem 6.000 e foi criado um sub-lote de 4.000 para o comprador
        self.assertEqual(batch.circulating_quantity, 6000)
        sub_batches = [b for b in self.engine.cbio_batches.values() if b.current_owner_cnpj == "33000167000101"]
        self.assertEqual(len(sub_batches), 1)
        self.assertEqual(sub_batches[0].circulating_quantity, 4000)

        # Invariante de conservação total: 6.000 + 4.000 = 10.000
        total_circ = sum(b.circulating_quantity for b in self.engine.cbio_batches.values())
        self.assertEqual(total_circ, 10000)

    def test_cbio_transfer_insufficient_balance_rejected(self) -> None:
        """Tentativa de transferir quantidade superior ao saldo em circulação deve falhar."""
        self.engine.issue_cbio_batch(
            batch_id="CBIO-SALDO-TEST",
            issuer_id="USINA-02",
            issuer_name="Usina Teste",
            issuer_cnpj="51466860000156",
            bookkeeper_id="BANCO-01",
            bookkeeper_name="Banco 01",
            bookkeeper_cnpj="61194353000164",
            anp_certificate_code="ANP-CERT-002",
            nfe_key=self.valid_nfe,
            biofuel_type=BiofuelType.BIODIESEL,
            energy_efficiency_score="70.00",
            quantity=1000,
            unit_price_brl="90.00",
        )

        with self.assertRaises(CbioTransferError):
            self.engine.transfer_cbio(
                trade_id="TRD-FAIL",
                batch_id="CBIO-SALDO-TEST",
                seller_cnpj="51466860000156",
                buyer_cnpj="33000167000101",
                buyer_name="Distribuidora",
                quantity=1001,
                unit_price_brl="90.00",
            )

    def test_cbio_compulsory_retirement_and_law_048(self) -> None:
        """Aposentadoria compulsória definitiva de CBIO para atendimento de meta ANP."""
        self.engine.issue_cbio_batch(
            batch_id="CBIO-RETIRE-01",
            issuer_id="USINA-01",
            issuer_name="Usina 01",
            issuer_cnpj="33000167000101",  # Já na distribuidora
            bookkeeper_id="BANCO-01",
            bookkeeper_name="Banco 01",
            bookkeeper_cnpj="61194353000164",
            anp_certificate_code="ANP-CERT-003",
            nfe_key=self.valid_nfe,
            biofuel_type=BiofuelType.ETANOL_ANIDRO,
            energy_efficiency_score="65.00",
            quantity=5000,
            unit_price_brl="95.00",
        )

        cert = self.engine.retire_cbios_compulsory(
            certificate_id="CERT-APOS-001",
            obligated_party_cnpj="33000167000101",
            obligated_party_name="Distribuidora Combustíveis",
            compliance_year=2026,
            batch_retirements=[{"batch_id": "CBIO-RETIRE-01", "quantity": 5000}],
        )

        self.assertEqual(cert.quantity_retired, 5000)
        self.assertEqual(cert.tco2e_avoided, Decimal("5000.00"))

        batch = self.engine.cbio_batches["CBIO-RETIRE-01"]
        self.assertEqual(batch.status, CbioBatchStatus.APOSENTADO)
        self.assertEqual(batch.circulating_quantity, 0)
        self.assertEqual(batch.retired_quantity, 5000)

        # LAW-048: Bloqueio estrito de re-aposentadoria ou negociação de lote aposentado
        with self.assertRaises(CbioRetirementError):
            self.engine.retire_cbios_compulsory(
                certificate_id="CERT-REAPOS-FAIL",
                obligated_party_cnpj="33000167000101",
                obligated_party_name="Distribuidora Combustíveis",
                compliance_year=2026,
                batch_retirements=[{"batch_id": "CBIO-RETIRE-01", "quantity": 100}],
            )

        with self.assertRaises(CbioTransferError):
            self.engine.transfer_cbio(
                trade_id="TRD-RETIRED-FAIL",
                batch_id="CBIO-RETIRE-01",
                seller_cnpj="33000167000101",
                buyer_cnpj="51466860000156",
                buyer_name="Usina",
                quantity=100,
                unit_price_brl="95.00",
            )

    def test_anp_compliance_and_carry_over(self) -> None:
        """Testa apuração de conformidade da meta ANP, carry-over legal e aplicação de multas."""
        distrib_cnpj = "33000167000101"
        self.engine.issue_cbio_batch(
            batch_id="CBIO-ANP-01",
            issuer_id="USINA-01",
            issuer_name="Usina",
            issuer_cnpj=distrib_cnpj,
            bookkeeper_id="BANCO-01",
            bookkeeper_name="Banco",
            bookkeeper_cnpj="61194353000164",
            anp_certificate_code="ANP-CERT-004",
            nfe_key=self.valid_nfe,
            biofuel_type=BiofuelType.ETANOL_HIDRATADO,
            energy_efficiency_score="60.00",
            quantity=10000,
            unit_price_brl="100.00",
        )

        # Caso 1: Aposenta 9.000 de uma meta de 10.000 (90% de cumprimento >= 85%)
        self.engine.retire_cbios_compulsory(
            certificate_id="CERT-ANP-90",
            obligated_party_cnpj=distrib_cnpj,
            obligated_party_name="Distribuidora",
            compliance_year=2026,
            batch_retirements=[{"batch_id": "CBIO-ANP-01", "quantity": 9000}],
        )

        rep1 = self.engine.calculate_anp_compliance(
            obligated_party_cnpj=distrib_cnpj,
            obligated_party_name="Distribuidora",
            compliance_year=2026,
            annual_target_cbios=10000,
        )
        self.assertEqual(rep1.compliance_percentage, Decimal("90.00"))
        self.assertFalse(rep1.is_fully_compliant)
        self.assertTrue(rep1.carry_over_eligible)
        self.assertEqual(rep1.carry_over_quantity, 1000)  # Todo o déficit de 1.000 é carregado (<= 15%)
        self.assertEqual(rep1.total_penalty_brl, Decimal("0.00"))

        # Caso 2: Atingiu menos de 85% (ex: 80% em 2027) gera multa integral
        rep2 = self.engine.calculate_anp_compliance(
            obligated_party_cnpj=distrib_cnpj,
            obligated_party_name="Distribuidora",
            compliance_year=2027,  # Ano sem aposentadorias (0%)
            annual_target_cbios=5000,
            penalty_per_cbio="100.00",
        )
        self.assertEqual(rep2.compliance_percentage, Decimal("0.00"))
        self.assertFalse(rep2.carry_over_eligible)
        self.assertEqual(rep2.total_penalty_brl, Decimal("500000.00"))

    # =========================================================================
    # 5. TESTES: CONTRATOS BILATERAIS DE ENERGIA ELÉTRICA (CCEE / ACL)
    # =========================================================================

    def test_register_power_contract_and_fiduciary_lock(self) -> None:
        """Registra contrato de energia no ACL e valida trava fiduciária (LAW-065)."""
        contract = self.engine.register_power_contract(
            contract_id="CTR-TEST-001",
            ccee_contract_code="CCEE-1001",
            buyer_id="COM-01",
            buyer_name="Comercializadora Alpha",
            seller_id="GER-01",
            seller_name="Geradora Hidro Beta",
            energy_source=EnergySource.CONV,
            submarket=SinSubmarket.SE_CO,
            start_date="2026-08-01",
            end_date="2026-08-31",
            mw_average="10.0000",
            total_hours=744,
            price_per_mwh="150.00",
            fiduciary_lock_amount="500000.00",
            fiduciary_creditor_id="BANCO-CRED-01",
        )

        self.assertEqual(contract.volume_mwh, Decimal("7440.0000"))
        self.assertEqual(contract.notional_amount, Decimal("1116000.00"))
        self.assertEqual(contract.fiduciary_lock_amount, Decimal("500000.00"))

    def test_power_fiduciary_lock_overflow_rejected_law_065(self) -> None:
        """LAW-065: Bloqueia trava fiduciária superior ao valor nocional do contrato de energia."""
        with self.assertRaises(InvariantViolationError) as ctx:
            self.engine.register_power_contract(
                contract_id="CTR-FAIL-LOCK",
                ccee_contract_code="CCEE-FAIL",
                buyer_id="COM-01",
                buyer_name="Comercializadora",
                seller_id="GER-01",
                seller_name="Geradora",
                energy_source="CONV",
                submarket="SE_CO",
                start_date="2026-08-01",
                end_date="2026-08-31",
                mw_average="1.0000",
                total_hours=100,
                price_per_mwh="100.00",  # Notional = 10,000.00
                fiduciary_lock_amount="12000.00",  # Excede nocional
            )
        self.assertEqual(ctx.exception.law_code, "LAW-065")

    def test_power_balance_and_ccee_mcp_settlement_zero_sum_law_066(self) -> None:
        """Apura balanço energético e executa liquidação multilateral MCP ao PLD (LAW-066)."""
        # Contrato de 10 MWmédio (7.440 MWh) de GER-01 para COM-01 em SE_CO
        self.engine.register_power_contract(
            contract_id="CTR-MCP-001",
            ccee_contract_code="CCEE-MCP-1",
            buyer_id="COM-01",
            buyer_name="Comercializadora",
            seller_id="GER-01",
            seller_name="Geradora",
            energy_source="CONV",
            submarket="SE_CO",
            start_date="2026-08-01",
            end_date="2026-08-31",
            mw_average="10.0000",
            total_hours=744,
            price_per_mwh="180.00",
        )

        # Medição:
        # GER-01 gerou 7.940 MWh (500 MWh a mais que sua venda contratual de 7.440) -> Sobra de 500 MWh
        # COM-01 consumiu 7.940 MWh (500 MWh a mais que sua compra contratual de 7.440) -> Déficit de 500 MWh
        measurements = {
            "GER-01": {"measured_generation_mwh": "7940.0000", "measured_consumption_mwh": "0.0000"},
            "COM-01": {"measured_generation_mwh": "0.0000", "measured_consumption_mwh": "7940.0000"},
        }

        # Liquidação ao PLD de R$ 150.00/MWh
        batch = self.engine.settle_ccee_mcp_batch(
            batch_id="BATCH-TEST-MCP-01",
            period="2026-08",
            submarket=SinSubmarket.SE_CO,
            pld_price="150.00",
            participants_measurements=measurements,
        )

        self.assertEqual(batch.total_credits_brl, Decimal("75000.00"))
        self.assertEqual(batch.total_debits_brl, Decimal("75000.00"))
        self.assertEqual(batch.net_clearing_discrepancy_brl, Decimal("0.00"))

        # Invariante LAW-066
        self.engine.assert_law_066_multilateral_clearing_and_value_conservation()

    def test_pld_outside_regulatory_bounds_rejected(self) -> None:
        """PLD fora dos limites estruturais ANEEL (mínimo R$ 69.04, máximo R$ 679.29) é rejeitado."""
        with self.assertRaises(PowerSettlementError):
            self.engine.settle_ccee_mcp_batch(
                batch_id="BATCH-ERR-MIN",
                period="2026-08",
                submarket="SE_CO",
                pld_price="50.00",  # Abaixo do mínimo de 69.04
                participants_measurements={},
            )

        with self.assertRaises(PowerSettlementError):
            self.engine.settle_ccee_mcp_batch(
                batch_id="BATCH-ERR-MAX",
                period="2026-08",
                submarket="SE_CO",
                pld_price="800.00",  # Acima do máximo de 679.29
                participants_measurements={},
            )

    # =========================================================================
    # 6. TESTES: AUDITORIA GLOBAL DE INVARIANTES E FIXTURES JSON
    # =========================================================================

    def test_verify_all_invariants_on_fixture(self) -> None:
        """Carrega fixtures/agro_green_finance_engine.json e valida 100% dos invariantes formais."""
        self.assertTrue(FIXTURE_PATH.exists(), f"Fixture não encontrada em: {FIXTURE_PATH}")
        loaded_engine = AgroGreenFinanceEngine.load_from_fixture_file(FIXTURE_PATH)

        report = loaded_engine.verify_all_invariants()
        self.assertTrue(report["all_invariants_satisfied"])
        self.assertEqual(report["audited_cprs_count"], 3)
        self.assertEqual(report["audited_cbio_batches_count"], 4)
        self.assertEqual(report["audited_power_contracts_count"], 2)
        self.assertEqual(report["audited_ccee_batches_count"], 1)
        self.assertEqual(report["total_issued_cbios"], 35000)
        self.assertEqual(report["total_circulating_cbios"], 23000)
        self.assertEqual(report["total_retired_cbios"], 12000)

    def test_roundtrip_dump_and_load_fixtures(self) -> None:
        """Testa serialização completa (to_dict / dump) e deserialização (from_dict / load)."""
        loaded_engine = AgroGreenFinanceEngine.load_from_fixture_file(FIXTURE_PATH)

        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            loaded_engine.dump_to_fixture_file(tmp_path)
            reloaded_engine = AgroGreenFinanceEngine.load_from_fixture_file(tmp_path)

            self.assertEqual(len(reloaded_engine.cprs), len(loaded_engine.cprs))
            self.assertEqual(len(reloaded_engine.cbio_batches), len(loaded_engine.cbio_batches))
            self.assertEqual(len(reloaded_engine.power_contracts), len(loaded_engine.power_contracts))
            self.assertEqual(len(reloaded_engine.ccee_settlement_history), len(loaded_engine.ccee_settlement_history))

            report = reloaded_engine.verify_all_invariants()
            self.assertTrue(report["all_invariants_satisfied"])
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
