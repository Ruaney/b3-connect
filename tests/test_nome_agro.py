"""Suíte de Testes Unitários de Títulos do Agronegócio NoMe Agro B3 (FL-026).

Valida os requisitos de negócio e operacionais da Cédula de Produto Rural (CPR Física e Financeira):
- Registro escritural conforme Lei nº 8.929/1994 e Lei nº 13.986/2020 (Lei do Agro).
- Validação determinística do Cadastro Ambiental Rural (CAR - SICAR).
- Verificação de cronograma de safra e bloqueio de safras retroativas.
- Validação estrita de valores, quantidades, locais de entrega e documentos oficiais.
- Ciclo de anuência eletrônica do credor (aceite/recusa).
- Liquidação física com romaneio e liquidação financeira indexada (CEPEA).
"""

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.nome_agro import (
    AgroError,
    AgroGuarantee,
    AgroGuaranteeType,
    AgroParsingError,
    AgroRegistrationEngine,
    AgroSettlementError,
    AgroValidationError,
    ApprovalStatus,
    CommodityType,
    CprRegistration,
    CprType,
    DeliveryLocation,
    ElectronicApproval,
    MeasurementUnit,
    Party,
    PriceIndexType,
    SettlementRecord,
    validate_car_code,
    validate_crop_year_string,
    validate_document,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestNomeAgroCpr(unittest.TestCase):
    """Testes para o motor de registro e escrituração NoMe Agro."""

    def setUp(self) -> None:
        self.engine = AgroRegistrationEngine()
        self.financial_fixture_path = FIXTURES_DIR / "cpr_financial_registration.json"
        self.physical_fixture_path = FIXTURES_DIR / "cpr_physical_registration.json"

        # Documentos válidos para testes
        self.valid_cpf = "12345678909"
        self.valid_cnpj_1 = "12345678000195"
        self.valid_cnpj_2 = "98765432000198"
        self.valid_car = "MT-5107909-E8B1A2C3D4E5F6789012345678901234"

    def test_cpr_financial_registration_and_settlement_lifecycle(self) -> None:
        """Teste de registro de CPR Financeira com liquidação em moeda corrente indexada ao CEPEA."""
        # 1. Carrega fixture oficial
        cpr = CprRegistration.from_file(self.financial_fixture_path)
        self.assertEqual(cpr.cpr_id, "CPR-2026-SOJA-001")
        self.assertEqual(cpr.cpr_type, CprType.FINANCEIRA)
        self.assertEqual(cpr.commodity, CommodityType.SOJA)
        self.assertEqual(cpr.quantity, Decimal("10000"))
        self.assertEqual(cpr.unit, MeasurementUnit.SACAS_60KG)
        self.assertEqual(cpr.crop_year, "2026/2027")
        self.assertEqual(cpr.price_index, PriceIndexType.CEPEA_PARANAGUA)

        # 2. Registra no motor
        registered = self.engine.register_cpr(cpr)
        self.assertEqual(registered.status, ApprovalStatus.PENDING_APPROVAL)
        self.assertIsNone(registered.approval)
        self.assertIsNone(registered.settlement)

        # 3. Tenta liquidar antes da anuência do credor (deve ser rejeitado)
        with self.assertRaises(AgroSettlementError) as ctx:
            self.engine.settle_cpr("CPR-2026-SOJA-001", settlement_price=Decimal("138.50"))
        self.assertIn("A CPR deve estar previamente com anuência", str(ctx.exception))

        # 4. Credor manifesta anuência eletrônica formal
        approved = self.engine.grant_creditor_approval(
            cpr_id="CPR-2026-SOJA-001",
            creditor_document="98.765.432/0001-98",
            approved_by="Gerente de Risco Agro",
            notes="Aceite eletrônico aprovado com sucesso via NoMe Agro B3.",
        )
        self.assertEqual(approved.status, ApprovalStatus.APPROVED)
        self.assertIsNotNone(approved.approval)
        self.assertTrue(approved.approval.approved)
        self.assertEqual(approved.approval.approved_by, "Gerente de Risco Agro")
        self.assertTrue(len(approved.approval.signature_hash) == 64)

        # 5. Executa a liquidação financeira no vencimento
        # 10.000 sacas * R$ 138,50 = R$ 1.385.000,00
        settlement = self.engine.settle_cpr(
            cpr_id="CPR-2026-SOJA-001",
            settlement_price=Decimal("138.50"),
            notes="Liquidação financeira apurada pelo índice CEPEA Paranaguá.",
        )
        self.assertEqual(settlement.settlement_type, CprType.FINANCEIRA)
        self.assertEqual(settlement.settlement_price, Decimal("138.50"))
        self.assertEqual(settlement.total_financial_value, Decimal("1385000.00"))
        self.assertEqual(approved.status, ApprovalStatus.SETTLED)
        self.assertEqual(approved.settlement.total_financial_value, Decimal("1385000.00"))

    def test_cpr_physical_registration_and_settlement_lifecycle(self) -> None:
        """Teste de registro de CPR Física com local de entrega e cronograma de safra."""
        cpr = CprRegistration.from_file(self.physical_fixture_path)
        self.assertEqual(cpr.cpr_id, "CPR-2026-MILHO-002")
        self.assertEqual(cpr.cpr_type, CprType.FISICA)
        self.assertEqual(cpr.commodity, CommodityType.MILHO)
        self.assertEqual(cpr.quantity, Decimal("20000"))
        self.assertIsNotNone(cpr.delivery_location)
        self.assertEqual(cpr.delivery_location.city, "Rio Verde")
        self.assertEqual(cpr.delivery_location.state, "GO")
        self.assertEqual(cpr.delivery_location.warehouse_name, "Terminal Graneleiro Rio Verde - Armazéns Gerais")

        # Registra
        registered = self.engine.register_cpr(cpr)
        self.assertEqual(registered.status, ApprovalStatus.PENDING_APPROVAL)

        # Credor concede anuência
        approved = self.engine.grant_creditor_approval(
            cpr_id="CPR-2026-MILHO-002",
            creditor_document="98765432000198",
            approved_by="Mesa de Commodities Trading",
        )
        self.assertEqual(approved.status, ApprovalStatus.APPROVED)

        # Liquidação física mediante entrega de grãos
        settlement = self.engine.settle_cpr(
            cpr_id="CPR-2026-MILHO-002",
            quantity_delivered=Decimal("20000"),
            receipt_id="ROMANEIO-CARGILL-987123",
            notes="Entrega total efetuada com classificação padrão comercial.",
        )
        self.assertEqual(settlement.settlement_type, CprType.FISICA)
        self.assertEqual(settlement.quantity_delivered, Decimal("20000"))
        self.assertEqual(settlement.receipt_identifier, "ROMANEIO-CARGILL-987123")
        self.assertEqual(approved.status, ApprovalStatus.SETTLED)

    def test_block_retroactive_crop_year(self) -> None:
        """Teste de bloqueio de emissões com safra retroativa ou inconsistente."""
        cpr = CprRegistration.from_file(self.financial_fixture_path)
        cpr.cpr_id = "CPR-TEST-RETRO"

        # Safra passada em relação à emissão de 2026 (ex: safra 2023/2024)
        cpr.crop_year = "2023/2024"
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("Safra inválida ou retroativa", str(ctx.exception))

        # Safra anual retroativa (ex: 2022)
        cpr.crop_year = "2022"
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("Safra inválida ou retroativa", str(ctx.exception))

        # Formato de safra inválido
        cpr.crop_year = "safra_antiga"
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("Safra inválida ou retroativa", str(ctx.exception))

    def test_block_invalid_car_code_in_rural_real_estate_guarantee(self) -> None:
        """Teste de validação estrita do Código CAR em garantias com alienação fiduciária de imóvel."""
        cpr = CprRegistration.from_file(self.financial_fixture_path)
        cpr.cpr_id = "CPR-TEST-CAR"

        # 1. CAR com UF inválida (XX não é estado brasileiro)
        cpr.guarantees[0].car_code = "XX-5107909-E8B1A2C3D4E5F6789012345678901234"
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("Código CAR inválido", str(ctx.exception))

        # 2. CAR com código IBGE de tamanho incorreto (não possui 7 dígitos)
        cpr.guarantees[0].car_code = "MT-510-E8B1A2C3D4E5F6789012345678901234"
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("Código CAR inválido", str(ctx.exception))

        # 3. CAR com hash não hexadecimal (contém caracteres inválidos)
        cpr.guarantees[0].car_code = "MT-5107909-ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("Código CAR inválido", str(ctx.exception))

        # 4. CAR ausente em alienação fiduciária de imóvel rural
        cpr.guarantees[0].car_code = None
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("exige obrigatoriamente código CAR", str(ctx.exception))

    def test_block_inconsistent_guarantee_values(self) -> None:
        """Teste de rejeição de garantia com valor negativo ou cobertura abusiva."""
        cpr = CprRegistration.from_file(self.financial_fixture_path)
        cpr.cpr_id = "CPR-TEST-GUARANTEE-VAL"

        # Valor da garantia negativo
        cpr.guarantees[0].guarantee_value = Decimal("-50000.00")
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("valor negativo", str(ctx.exception))

        # Percentual de cobertura negativo
        cpr.guarantees[0].guarantee_value = Decimal("50000.00")
        cpr.guarantees[0].coverage_percentage = Decimal("-10.00")
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("percentual de cobertura inválido", str(ctx.exception))

    def test_block_negative_or_zero_quantity(self) -> None:
        """Teste de rejeição de emissões com quantidade nula ou negativa."""
        cpr = CprRegistration.from_file(self.financial_fixture_path)
        cpr.cpr_id = "CPR-TEST-QTY-ZERO"
        cpr.quantity = Decimal("0")

        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("Quantidade da CPR deve ser estritamente positiva", str(ctx.exception))

        cpr.quantity = Decimal("-100")
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("Quantidade da CPR deve ser estritamente positiva", str(ctx.exception))

    def test_block_negative_unit_price(self) -> None:
        """Teste de rejeição de preço unitário negativo."""
        cpr = CprRegistration.from_file(self.financial_fixture_path)
        cpr.cpr_id = "CPR-TEST-PRICE"
        cpr.unit_price = Decimal("-15.00")

        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("Preço unitário deve ser estritamente positivo", str(ctx.exception))

    def test_block_maturity_prior_to_issue_date(self) -> None:
        """Teste de rejeição quando vencimento é anterior à data de emissão."""
        cpr = CprRegistration.from_file(self.financial_fixture_path)
        cpr.cpr_id = "CPR-TEST-DATES"
        cpr.issue_date = date(2026, 6, 1)
        cpr.maturity_date = date(2026, 5, 1)

        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("Data de vencimento", str(ctx.exception))
        self.assertIn("não pode ser anterior à data de emissão", str(ctx.exception))

    def test_block_delivery_location_inconsistencies_physical_cpr(self) -> None:
        """Teste de rejeição de inconsistências no local e cronograma de entrega da CPR Física."""
        cpr = CprRegistration.from_file(self.physical_fixture_path)
        cpr.cpr_id = "CPR-TEST-PHYS-LOC"

        # 1. CPR Física sem delivery_location
        cpr.delivery_location = None
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("CPR Física exige especificação de local de entrega", str(ctx.exception))

        # 2. Reestabelece local mas com UF inexistente
        cpr.delivery_location = DeliveryLocation(
            warehouse_name="Silo Central",
            address="Rodovia Km 10",
            city="Cidade Teste",
            state="XX",
        )
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("UF do local de entrega inválida", str(ctx.exception))

        # 3. Data de início de entrega anterior à emissão
        cpr.delivery_location.state = "MT"
        cpr.issue_date = date(2026, 6, 1)
        cpr.delivery_location.delivery_start_date = date(2026, 5, 1)
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("Data inicial de entrega", str(ctx.exception))

        # 4. Data final de entrega anterior à data inicial
        cpr.delivery_location.delivery_start_date = date(2026, 7, 1)
        cpr.delivery_location.delivery_end_date = date(2026, 6, 15)
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("Data final de entrega", str(ctx.exception))

    def test_creditor_electronic_approval_divergent_document(self) -> None:
        """Teste de rejeição de anuência caso o credor possua documento divergente."""
        cpr = CprRegistration.from_file(self.financial_fixture_path)
        cpr.cpr_id = "CPR-TEST-APPROVAL-MISMATCH"
        self.engine.register_cpr(cpr)

        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.grant_creditor_approval(
                cpr_id="CPR-TEST-APPROVAL-MISMATCH",
                creditor_document="11122233344",  # Documento não coincide com o credor registrado
                approved_by="Auditor",
            )
        self.assertIn("não confere com o credor registrado", str(ctx.exception))

    def test_creditor_rejection_workflow(self) -> None:
        """Teste de recusa expressa da CPR pelo credor."""
        cpr = CprRegistration.from_file(self.financial_fixture_path)
        cpr.cpr_id = "CPR-TEST-REJECTION"
        self.engine.register_cpr(cpr)

        rejected = self.engine.reject_cpr(
            cpr_id="CPR-TEST-REJECTION",
            creditor_document="98765432000198",
            reason="Preço de referência divergente da cotação acordada na pré-mesa.",
        )
        self.assertEqual(rejected.status, ApprovalStatus.REJECTED)
        self.assertEqual(
            rejected.metadata.get("rejection_reason"),
            "Preço de referência divergente da cotação acordada na pré-mesa.",
        )

        # Impossível liquidar CPR recusada
        with self.assertRaises(AgroSettlementError):
            self.engine.settle_cpr("CPR-TEST-REJECTION", settlement_price=Decimal("130.00"))

    def test_financial_calculation_with_spread_and_interest(self) -> None:
        """Teste de cálculo financeiro completo com ágio/deságio e taxa de juros."""
        cpr = CprRegistration.from_file(self.financial_fixture_path)
        cpr.cpr_id = "CPR-TEST-FIN-CALC"
        cpr.quantity = Decimal("1000")
        cpr.unit_price = Decimal("100.00")
        cpr.spread_percentage = Decimal("2.00")  # +2% de spread
        cpr.interest_rate_annual = Decimal("10.00")  # 10% a.a.
        cpr.issue_date = date(2026, 1, 1)
        cpr.maturity_date = date(2026, 7, 2)  # 182 dias de prazo

        # Base = 1000 * 100 = 100.000,00
        # Spread = 100.000 * 1,02 = 102.000,00
        # Juros = 102.000 * (1 + 0,10 * 182 / 365) = 102.000 * (1 + 0,049863...) = 107.086,03
        calculated = cpr.calculate_financial_value()
        self.assertEqual(calculated, Decimal("107086.03"))

    def test_document_validation_cpf_and_cnpj(self) -> None:
        """Validação de documentos brasileiros com algoritmo oficial módulo 11."""
        self.assertTrue(validate_document(self.valid_cpf))
        self.assertTrue(validate_document(self.valid_cnpj_1))
        self.assertTrue(validate_document(self.valid_cnpj_2))

        # CPFs e CNPJs com dígitos verificadores corrompidos
        self.assertFalse(validate_document("12345678900"))  # DV errado
        self.assertFalse(validate_document("11111111111"))  # Sequência idêntica
        self.assertFalse(validate_document("12345678000100"))  # DV de CNPJ errado
        self.assertFalse(validate_document("123"))  # Comprimento incorreto

    def test_parties_with_invalid_documents_rejected(self) -> None:
        """Teste de rejeição de emissor ou credor com CPF/CNPJ inválido."""
        cpr = CprRegistration.from_file(self.financial_fixture_path)
        cpr.cpr_id = "CPR-TEST-DOC-CORRUPT"

        cpr.issuer.document = "00000000000"
        with self.assertRaises(AgroValidationError) as ctx:
            self.engine.register_cpr(cpr)
        self.assertIn("Documento do emissor inválido", str(ctx.exception))

    def test_json_serialization_and_deserialization_roundtrip(self) -> None:
        """Teste de serialização e desserialização JSON idempotente e sem perdas."""
        cpr1 = CprRegistration.from_file(self.financial_fixture_path)
        json_output = cpr1.to_json()

        cpr2 = CprRegistration.from_json(json_output)
        self.assertEqual(cpr1.cpr_id, cpr2.cpr_id)
        self.assertEqual(cpr1.cpr_type, cpr2.cpr_type)
        self.assertEqual(cpr1.commodity, cpr2.commodity)
        self.assertEqual(cpr1.quantity, cpr2.quantity)
        self.assertEqual(cpr1.guarantees[0].car_code, cpr2.guarantees[0].car_code)
        self.assertEqual(cpr1.to_dict(), cpr2.to_dict())

    def test_filter_and_list_cprs(self) -> None:
        """Teste de listagem e filtragem de CPRs registradas."""
        cpr_fin = CprRegistration.from_file(self.financial_fixture_path)
        cpr_phys = CprRegistration.from_file(self.physical_fixture_path)

        self.engine.register_cpr(cpr_fin)
        self.engine.register_cpr(cpr_phys)

        # Filtro por commodity
        soja_list = self.engine.list_cprs(commodity=CommodityType.SOJA)
        self.assertEqual(len(soja_list), 1)
        self.assertEqual(soja_list[0].cpr_id, "CPR-2026-SOJA-001")

        # Filtro por status
        pending_list = self.engine.list_cprs(status=ApprovalStatus.PENDING_APPROVAL)
        self.assertEqual(len(pending_list), 2)


if __name__ == "__main__":
    unittest.main()
