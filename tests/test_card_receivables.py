"""Suíte de Testes Unitários — Registro e Gravame de Recebíveis de Cartão de Crédito no NoMe B3 (FL-069).

Valida os requisitos regulatórios da Resolução CMN nº 4.734/2019 e Circular BCB nº 3.952/2019:
1. Agrupamento canônico de Unidades de Recebíveis (UR) por quádrupla.
2. Controle de consentimento ativo (opt-in) do lojista.
3. Lei da Unicidade do Gravame e bloqueio ao sobre-oneramento de garantias.
4. Aditamento e desconstituição (baixa) de gravames fiduciários.
5. Vedações regulatórias de antecipação sobre frações oneradas (trava de antecipação).
6. Liquidação na data de vencimento com trava bancária de domicílio fiduciário.
7. Leis de conservação matemática e contábil estrita.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import unittest

from src.card_receivables_registry import (
    AnticipationRecord,
    BankAccount,
    CardReceivablesRegistryEngine,
    EncumberedReceivableAnticipationError,
    InsufficientReceivableCapacityError,
    InvalidReceivableOperationError,
    LienModality,
    LienNotFoundError,
    LienRecord,
    LienStatus,
    PaymentScheme,
    ReceivableConservationError,
    ReceivableUnit,
    ReceivableUnitNotFoundError,
    SettlementChannel,
    UnauthorizedConsentError,
    UrSettlementReceipt,
    UrStatus,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "card_receivables.json"
)


class TestCardReceivablesRegistry(unittest.TestCase):
    """Bateria de testes determinísticos para o fluxo FL-069."""

    def setUp(self) -> None:
        """Inicializa um novo motor a partir da fixture oficial sintética."""
        self.engine = CardReceivablesRegistryEngine.load_from_fixture(FIXTURE_PATH)

    # -----------------------------------------------------------------------
    # 1. Carga de Fixtures e Estrutura de URs
    # -----------------------------------------------------------------------

    def test_load_fixture_and_initial_invariants(self) -> None:
        """Verifica a correta instanciação das URs, credenciadoras e lojistas da fixture."""
        self.assertEqual(len(self.engine.urs), 5)
        self.assertEqual(len(self.engine.acquirers), 4)
        self.assertEqual(len(self.engine.merchants), 4)

        metrics = self.engine.verify_global_conservation()
        self.assertTrue(metrics["global_conservation_held"])
        self.assertEqual(metrics["total_current_volume"], "695000.00")
        self.assertEqual(metrics["total_committed_volume"], "410000.00")
        self.assertEqual(metrics["total_available_volume"], "285000.00")
        self.assertEqual(metrics["total_settled_volume"], "0.00")

    def test_ur_canonical_attributes(self) -> None:
        """Verifica os atributos da UR 1 (Cielo / Mastercard Crédito)."""
        ur1_id = "UR-20261215-CIELO-000190-E801292B"
        ur1 = self.engine.urs[ur1_id]

        self.assertEqual(ur1.merchant_document, "12345678000190")
        self.assertEqual(ur1.acquirer_code, "CIELO")
        self.assertEqual(ur1.payment_scheme, PaymentScheme.MASTERCARD_CREDITO_A_VISTA)
        self.assertEqual(ur1.settlement_date, "2026-12-15")
        self.assertEqual(ur1.current_amount, Decimal("150000.00"))
        self.assertEqual(ur1.committed_amount, Decimal("90000.00"))
        self.assertEqual(ur1.available_amount, Decimal("60000.00"))
        self.assertEqual(ur1.status, UrStatus.ATIVA)
        self.assertEqual(len(ur1.liens), 1)

    def test_ur_fully_encumbered_status(self) -> None:
        """Verifica que a UR 3 com 100% gravado assume o status TOTALMENTE_ONERADA."""
        ur3_id = "UR-20261210-STONE-000122-49258578"
        ur3 = self.engine.urs[ur3_id]

        self.assertEqual(ur3.current_amount, Decimal("80000.00"))
        self.assertEqual(ur3.committed_amount, Decimal("80000.00"))
        self.assertEqual(ur3.available_amount, Decimal("0.00"))
        self.assertEqual(ur3.status, UrStatus.TOTALMENTE_ONERADA)

    # -----------------------------------------------------------------------
    # 2. Acúmulo de Transações e Unicidade Canônica
    # -----------------------------------------------------------------------

    def test_aggregate_sales_in_existing_ur(self) -> None:
        """Valida que novas vendas para a mesma quádrupla acumulam na UR existente."""
        ur1_id = "UR-20261215-CIELO-000190-E801292B"
        ur_before = self.engine.urs[ur1_id]
        initial_curr = ur_before.current_amount
        initial_avail = ur_before.available_amount

        # Registra nova venda de R$ 25.000,00
        updated_ur = self.engine.register_receivable_transaction(
            merchant_document="12.345.678/0001-90",
            acquirer_code="CIELO",
            payment_scheme=PaymentScheme.MASTERCARD_CREDITO_A_VISTA,
            settlement_date="2026-12-15",
            amount=Decimal("25000.00"),
            transaction_id="TX-CIELO-MC-EXTRA-999"
        )

        self.assertEqual(updated_ur.ur_id, ur1_id)
        self.assertEqual(updated_ur.current_amount, initial_curr + Decimal("25000.00"))
        self.assertEqual(updated_ur.available_amount, initial_avail + Decimal("25000.00"))
        # Gravame fiduciário permanece inalterado
        self.assertEqual(updated_ur.committed_amount, Decimal("90000.00"))
        self.assertEqual(updated_ur.current_amount, updated_ur.committed_amount + updated_ur.available_amount)

    def test_new_ur_creation_for_different_scheme_or_date(self) -> None:
        """Gera nova UR caso o arranjo de pagamento ou data de liquidação divirjam."""
        initial_count = len(self.engine.urs)

        new_ur = self.engine.register_receivable_transaction(
            merchant_document="12345678000190",
            acquirer_code="CIELO",
            payment_scheme=PaymentScheme.VISA_DEBITO,
            settlement_date="2026-11-20",
            amount=Decimal("12000.00")
        )

        self.assertEqual(len(self.engine.urs), initial_count + 1)
        self.assertEqual(new_ur.payment_scheme, PaymentScheme.VISA_DEBITO)
        self.assertEqual(new_ur.current_amount, Decimal("12000.00"))
        self.assertEqual(new_ur.available_amount, Decimal("12000.00"))
        self.assertEqual(new_ur.committed_amount, Decimal("0.00"))

    # -----------------------------------------------------------------------
    # 3. Gestão de Consentimento (Opt-in Regulatório)
    # -----------------------------------------------------------------------

    def test_query_receivables_with_valid_consent(self) -> None:
        """Consulta autorizada retorna a lista de URs do lojista."""
        # Magazine Litoral autorizou Itaú (ISPB 60701190)
        urs = self.engine.query_merchant_urs("12345678000190", "60701190")
        self.assertEqual(len(urs), 2)  # UR1 e UR5

    def test_query_receivables_without_consent_fails(self) -> None:
        """Tentativa de consulta por IF sem autorização ativa é rejeitada com erro regulatório."""
        # Magazine Litoral NÃO autorizou Bradesco (60746948)
        with self.assertRaises(UnauthorizedConsentError):
            self.engine.query_merchant_urs("12345678000190", "60746948")

    def test_revoke_consent_blocks_new_liens(self) -> None:
        """Revogação de consentimento impede a constituição de novos gravames."""
        self.engine.revoke_consent("12345678000190", "60701190")

        ur1_id = "UR-20261215-CIELO-000190-E801292B"
        acc = BankAccount("60701190", "ITAU", "0001", "11223-4")

        with self.assertRaises(UnauthorizedConsentError):
            self.engine.constitute_lien(
                ur_id=ur1_id,
                creditor_ispb="60701190",
                creditor_name="ITAU UNIBANCO",
                contract_number="NOVO-CONTRATO-99",
                modality=LienModality.CESSAO_FIDUCIARIA,
                encumbered_amount=Decimal("10000.00"),
                beneficiary_bank_account=acc
            )

    # -----------------------------------------------------------------------
    # 4. Lei de Unicidade e Não-Sobreposição de Gravame
    # -----------------------------------------------------------------------

    def test_constitute_lien_within_available_capacity(self) -> None:
        """Constituição válida de gravame de 2º grau sobre saldo livre restante."""
        ur1_id = "UR-20261215-CIELO-000190-E801292B"
        ur = self.engine.urs[ur1_id]
        self.assertEqual(ur.available_amount, Decimal("60000.00"))

        btg_acc = BankAccount("30306294", "BANCO BTG PACTUAL", "0001", "55667-8")
        lien = self.engine.constitute_lien(
            ur_id=ur1_id,
            creditor_ispb="30306294",
            creditor_name="BANCO BTG PACTUAL S.A.",
            contract_number="CCB-BTG-2026-551",
            modality=LienModality.CESSAO_FIDUCIARIA,
            encumbered_amount=Decimal("40000.00"),
            beneficiary_bank_account=btg_acc,
            priority_rank=2
        )

        self.assertEqual(lien.encumbered_amount, Decimal("40000.00"))
        self.assertEqual(ur.committed_amount, Decimal("130000.00"))  # 90k + 40k
        self.assertEqual(ur.available_amount, Decimal("20000.00"))  # 150k - 130k
        self.assertEqual(len(ur.liens), 2)
        self.assertEqual(ur.status, UrStatus.ATIVA)

    def test_constitute_lien_exceeding_capacity_rejected(self) -> None:
        """Tentativa de gravame com valor superior à capacidade livre é sumariamente rejeitada."""
        ur1_id = "UR-20261215-CIELO-000190-E801292B"
        # Disponível na UR 1 é R$ 60.000,00. Tentativa de onerar R$ 60.000,01:
        btg_acc = BankAccount("30306294", "BANCO BTG PACTUAL", "0001", "55667-8")
        with self.assertRaises(InsufficientReceivableCapacityError):
            self.engine.constitute_lien(
                ur_id=ur1_id,
                creditor_ispb="30306294",
                creditor_name="BANCO BTG PACTUAL S.A.",
                contract_number="CCB-EXCESSO-001",
                modality=LienModality.CESSAO_FIDUCIARIA,
                encumbered_amount=Decimal("60000.01"),
                beneficiary_bank_account=btg_acc
            )

    def test_constitute_lien_on_fully_encumbered_ur_rejected(self) -> None:
        """Não permite constituir gravame em UR com saldo livre esgotado."""
        ur3_id = "UR-20261210-STONE-000122-49258578"
        # UR 3 tem available_amount == Decimal("0.00")
        acc = BankAccount("60701190", "ITAU", "0001", "99887-1")
        with self.assertRaises(InsufficientReceivableCapacityError):
            self.engine.constitute_lien(
                ur_id=ur3_id,
                creditor_ispb="60701190",
                creditor_name="ITAU UNIBANCO",
                contract_number="CCB-BLOQUEADA-001",
                modality=LienModality.PENHOR,
                encumbered_amount=Decimal("500.00"),
                beneficiary_bank_account=acc
            )

    # -----------------------------------------------------------------------
    # 5. Aditamento e Baixa (Desconstituição) de Gravames
    # -----------------------------------------------------------------------

    def test_amend_lien_partial_reduction(self) -> None:
        """Aditamento para redução parcial do gravame desonera a capacidade da UR."""
        ur1_id = "UR-20261215-CIELO-000190-E801292B"
        ur = self.engine.urs[ur1_id]
        lien_id = "GRV-20261215-001-ITAU01"

        # Reduz gravame de R$ 90.000 para R$ 50.000
        updated_lien = self.engine.amend_lien(
            ur_id=ur1_id,
            lien_id=lien_id,
            new_encumbered_amount=Decimal("50000.00")
        )

        self.assertEqual(updated_lien.encumbered_amount, Decimal("50000.00"))
        self.assertEqual(updated_lien.status, LienStatus.ADITADO)
        self.assertEqual(ur.committed_amount, Decimal("50000.00"))
        self.assertEqual(ur.available_amount, Decimal("100000.00"))  # 150k - 50k

    def test_release_lien_full_discharge(self) -> None:
        """Baixa integral de gravame por liquidação da obrigação garantida."""
        ur3_id = "UR-20261210-STONE-000122-49258578"
        ur = self.engine.urs[ur3_id]
        self.assertEqual(ur.status, UrStatus.TOTALMENTE_ONERADA)

        lien_id = "GRV-20261210-001-ITAU02"
        released = self.engine.release_lien(ur_id=ur3_id, lien_id=lien_id)

        self.assertEqual(released.status, LienStatus.BAIXADO)
        self.assertIsNotNone(released.released_at)
        self.assertEqual(ur.committed_amount, Decimal("0.00"))
        self.assertEqual(ur.available_amount, Decimal("80000.00"))
        self.assertEqual(ur.status, UrStatus.ATIVA)

    # -----------------------------------------------------------------------
    # 6. Antecipação de Recebíveis e Trava Regulatória
    # -----------------------------------------------------------------------

    def test_valid_receivable_anticipation(self) -> None:
        """Antecipação válida sobre o saldo livre não onerado."""
        ur1_id = "UR-20261215-CIELO-000190-E801292B"
        ur = self.engine.urs[ur1_id]
        self.assertEqual(ur.available_amount, Decimal("60000.00"))

        # Lojista antecipa R$ 40.000,00 à taxa de 2,5%
        ant = self.engine.anticipate_receivables(
            ur_id=ur1_id,
            requested_amount=Decimal("40000.00"),
            discount_rate=Decimal("0.0250"),
            anticipator_code="CIELO",
            settlement_channel=SettlementChannel.CREDITO_CONTA
        )

        self.assertEqual(ant.requested_face_amount, Decimal("40000.00"))
        self.assertEqual(ant.discount_fee, Decimal("1000.00"))  # 40k * 2.5%
        self.assertEqual(ant.net_disbursed_amount, Decimal("39000.00"))
        self.assertTrue(ant.conservation_held)

        # Saldo corrente da UR decresce pelo valor de face antecipado
        self.assertEqual(ur.current_amount, Decimal("110000.00"))  # 150k - 40k
        self.assertEqual(ur.settled_amount, Decimal("40000.00"))
        self.assertEqual(ur.committed_amount, Decimal("90000.00"))  # Gravame intocado!
        self.assertEqual(ur.available_amount, Decimal("20000.00"))  # 110k - 90k
        self.assertEqual(ur.current_amount, ur.committed_amount + ur.available_amount)

    def test_anticipation_exceeding_free_balance_blocked(self) -> None:
        """Antecipação que tente invadir parcela sob garantia fiduciária é barrada."""
        ur1_id = "UR-20261215-CIELO-000190-E801292B"
        # Disponível é R$ 60.000,00. Tentativa de antecipar R$ 60.000,01:
        with self.assertRaises(EncumberedReceivableAnticipationError):
            self.engine.anticipate_receivables(
                ur_id=ur1_id,
                requested_amount=Decimal("60000.01"),
                discount_rate=Decimal("0.0200"),
                anticipator_code="CIELO"
            )

    # -----------------------------------------------------------------------
    # 7. Liquidação Financeira na Data Prevista (Vencimento)
    # -----------------------------------------------------------------------

    def test_maturity_settlement_with_creditor_fiduciary_lock(self) -> None:
        """Liquidação na data prevista direciona parcelas gravadas à IF e saldo livre ao lojista."""
        ur2_id = "UR-20261220-REDE-000111-F199FFD5"
        ur = self.engine.urs[ur2_id]
        # UR 2 possui:
        # Total: R$ 320.000,00
        # Gravame 1 (Bradesco 1º grau): R$ 180.000,00
        # Gravame 2 (Banco do Brasil 2º grau): R$ 60.000,00
        # Saldo Livre Lojista: R$ 80.000,00

        receipt = self.engine.settle_ur_on_maturity(ur_id=ur2_id)

        self.assertTrue(receipt.invariant_conservation_held)
        self.assertEqual(receipt.total_settled_amount, Decimal("320000.00"))
        self.assertEqual(receipt.total_creditor_payments, Decimal("240000.00"))
        self.assertEqual(receipt.total_merchant_payments, Decimal("80000.00"))
        self.assertEqual(len(receipt.payments), 3)

        # Pagamento 1: Bradesco
        p1 = receipt.payments[0]
        self.assertEqual(p1.recipient_name, "BANCO BRADESCO S.A.")
        self.assertEqual(p1.amount, Decimal("180000.00"))
        self.assertEqual(p1.destination_account.account_number, "77665-2")

        # Pagamento 2: Banco do Brasil
        p2 = receipt.payments[1]
        self.assertEqual(p2.recipient_name, "BANCO DO BRASIL S.A.")
        self.assertEqual(p2.amount, Decimal("60000.00"))
        self.assertEqual(p2.destination_account.account_number, "55443-3")

        # Pagamento 3: Repasse livre Drogaria Nova Esperança
        p3 = receipt.payments[2]
        self.assertEqual(p3.recipient_name, "DROGARIA E FARMACIA NOVA ESPERANCA LTDA.")
        self.assertEqual(p3.amount, Decimal("80000.00"))
        self.assertEqual(p3.destination_account.account_number, "98765-4")

        # UR fica liquidada
        self.assertEqual(ur.status, UrStatus.LIQUIDADA)
        self.assertEqual(ur.current_amount, Decimal("0.00"))
        self.assertEqual(ur.available_amount, Decimal("0.00"))
        self.assertEqual(ur.committed_amount, Decimal("0.00"))
        self.assertEqual(ur.settled_amount, Decimal("320000.00"))

    def test_settle_already_settled_ur_raises_error(self) -> None:
        """Tentar liquidar duas vezes a mesma UR gera erro de operação inválida."""
        ur4_id = "UR-20261110-CIELO-000177-7D24DA84"
        self.engine.settle_ur_on_maturity(ur_id=ur4_id)
        with self.assertRaises(InvalidReceivableOperationError):
            self.engine.settle_ur_on_maturity(ur_id=ur4_id)

    # -----------------------------------------------------------------------
    # 8. Verificação de Integridade Contábil e Invariantes
    # -----------------------------------------------------------------------

    def test_conservation_after_complex_sequence(self) -> None:
        """Executa ciclo completo: venda, gravame, aditamento, antecipação e liquidação."""
        # 1. Registra nova UR de R$ 200.000,00
        ur = self.engine.register_receivable_transaction(
            merchant_document="12345678000190",
            acquirer_code="PAGBANK",
            payment_scheme=PaymentScheme.ELO_CREDITO_A_VISTA,
            settlement_date="2026-12-28",
            amount=Decimal("200000.00")
        )
        ur_id = ur.ur_id

        # 2. Constitui gravame de R$ 120.000,00 para BTG
        btg_acc = BankAccount("30306294", "BTG", "0001", "12345-6")
        lien = self.engine.constitute_lien(
            ur_id=ur_id,
            creditor_ispb="30306294",
            creditor_name="BTG PACTUAL",
            contract_number="CONTRATO-BTG-200K",
            modality=LienModality.CESSAO_FIDUCIARIA,
            encumbered_amount=Decimal("120000.00"),
            beneficiary_bank_account=btg_acc
        )
        self.assertEqual(ur.available_amount, Decimal("80000.00"))

        # 3. Adita gravame para R$ 100.000,00 (desonera R$ 20.000,00)
        self.engine.amend_lien(
            ur_id=ur_id,
            lien_id=lien.lien_id,
            new_encumbered_amount=Decimal("100000.00")
        )
        self.assertEqual(ur.available_amount, Decimal("100000.00"))

        # 4. Antecipa R$ 50.000,00 a 3,0%
        ant = self.engine.anticipate_receivables(
            ur_id=ur_id,
            requested_amount=Decimal("50000.00"),
            discount_rate=Decimal("0.0300"),
            anticipator_code="PAGBANK"
        )
        self.assertEqual(ant.net_disbursed_amount, Decimal("48500.00"))
        self.assertEqual(ant.discount_fee, Decimal("1500.00"))
        self.assertEqual(ur.current_amount, Decimal("150000.00"))
        self.assertEqual(ur.committed_amount, Decimal("100000.00"))
        self.assertEqual(ur.available_amount, Decimal("50000.00"))

        # 5. Liquida no vencimento
        receipt = self.engine.settle_ur_on_maturity(ur_id=ur_id)
        self.assertEqual(receipt.total_settled_amount, Decimal("150000.00"))
        self.assertEqual(receipt.total_creditor_payments, Decimal("100000.00"))
        self.assertEqual(receipt.total_merchant_payments, Decimal("50000.00"))

        # Prova contábil global
        metrics = self.engine.verify_global_conservation()
        self.assertTrue(metrics["global_conservation_held"])


if __name__ == "__main__":
    unittest.main()
