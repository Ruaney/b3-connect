"""Testes automatizados para o motor de Portabilidade de Custódia e STVM (FL-024) — B3 Depositária Central."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH independentemente da forma de execução
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.stvm_portability import (
    CustodyMovement,
    CustodyPosition,
    STVMAssetItem,
    STVMCustodyEligibilityError,
    STVMEngine,
    STVMRejectionCode,
    STVMRequest,
    STVMStatus,
    STVMStateTransitionError,
    STVMTitularityMismatchError,
    STVMValidationError,
    TransferReason,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestSTVMPortability(unittest.TestCase):
    """Bateria de testes unitários do motor de Portabilidade STVM da B3 Depositária."""

    def setUp(self) -> None:
        self.engine = STVMEngine()
        self.fixture_file = FIXTURES_DIR / "stvm_transfer_request.json"

    def test_full_flow_same_holder_equities_and_fii(self) -> None:
        """Valida o ciclo completo de ponta a ponta (SOLICITADA -> VALIDADA -> ACEITA -> EFETIVADA)

        para transferência de Ações (PETR4, VALE3) e cotas de FII (HGLG11) de Mesma Titularidade.
        """
        # 1. Configura a custódia inicial no participante cedente 105
        # PETR4: 1000 total (1000 livres)
        self.engine.set_custody_position(
            CustodyPosition(
                participant=105,
                account="105001",
                symbol="PETR4",
                isin="BRPETRACNPR6",
                total_quantity=Decimal("1000"),
                available_quantity=Decimal("1000"),
            )
        )
        # VALE3: 500 total (500 livres)
        self.engine.set_custody_position(
            CustodyPosition(
                participant=105,
                account="105001",
                symbol="VALE3",
                isin="BRVALEACNOR0",
                total_quantity=Decimal("500"),
                available_quantity=Decimal("500"),
            )
        )
        # HGLG11: 300 total (300 livres)
        self.engine.set_custody_position(
            CustodyPosition(
                participant=105,
                account="105001",
                symbol="HGLG11",
                isin="BRHGLGCTF004",
                total_quantity=Decimal("300"),
                available_quantity=Decimal("300"),
            )
        )

        # 2. Criação da solicitação STVM de mesma titularidade
        request = STVMRequest(
            protocol="STVM-20261103-000105",
            transfer_reason=TransferReason.MESMA_TITULARIDADE,
            investor_id="123.456.789-00",
            investor_name="CARLOS EDUARDO SILVA",
            source_participant=105,
            source_account="105001",
            target_participant=88,
            target_account="88001",
            target_investor_id="12345678900",
            target_investor_name="CARLOS EDUARDO SILVA",
            items=[
                STVMAssetItem(symbol="PETR4", isin="BRPETRACNPR6", quantity=Decimal("500"), asset_type="ACAO"),
                STVMAssetItem(symbol="VALE3", isin="BRVALEACNOR0", quantity=Decimal("200"), asset_type="ACAO"),
                STVMAssetItem(symbol="HGLG11", isin="BRHGLGCTF004", quantity=Decimal("150"), asset_type="FII"),
            ],
            created_at=datetime(2026, 11, 3, 10, 0, 0),
        )

        # Registro no motor
        reg_req = self.engine.register_request(request)
        self.assertEqual(reg_req.status, STVMStatus.SOLICITADA)
        self.assertEqual(reg_req.regulatory_deadline, date(2026, 11, 5))  # D+2 útil (Terça -> Quinta)
        self.assertEqual(len(reg_req.audit_trail), 1)

        # 3. Cedente valida e audita a solicitação
        validated = self.engine.validate_cedente(reg_req.protocol)
        self.assertEqual(validated.status, STVMStatus.VALIDADA_CEDENTE)
        self.assertEqual(len(validated.audit_trail), 2)

        # 4. Cessionário dá o aceite da conta destinatária
        accepted = self.engine.accept_cessionario(reg_req.protocol, confirmed=True, notes="Conta BTG 88001 ativa")
        self.assertEqual(accepted.status, STVMStatus.ACEITA_CESSIONARIO)
        self.assertEqual(len(accepted.audit_trail), 3)

        # 5. Central Depositária da B3 liquida atomicamente a STVM
        exec_time = datetime(2026, 11, 4, 15, 30, 0)
        result = self.engine.execute_transfer(reg_req.protocol, b3_timestamp=exec_time)

        self.assertEqual(result.status, STVMStatus.EFETIVADA_B3)
        self.assertEqual(len(result.movements), 6)  # 3 ativos * 2 (1 débito + 1 crédito)

        # 6. Verificação de integridade e conservação de custódia
        # Cedente 105
        ced_petr4 = self.engine.get_custody_position(105, "105001", "PETR4")
        self.assertIsNotNone(ced_petr4)
        self.assertEqual(ced_petr4.total_quantity, Decimal("500"))
        self.assertEqual(ced_petr4.available_quantity, Decimal("500"))

        ced_vale3 = self.engine.get_custody_position(105, "105001", "VALE3")
        self.assertIsNotNone(ced_vale3)
        self.assertEqual(ced_vale3.total_quantity, Decimal("300"))
        self.assertEqual(ced_vale3.available_quantity, Decimal("300"))

        ced_hglg11 = self.engine.get_custody_position(105, "105001", "HGLG11")
        self.assertIsNotNone(ced_hglg11)
        self.assertEqual(ced_hglg11.total_quantity, Decimal("150"))
        self.assertEqual(ced_hglg11.available_quantity, Decimal("150"))

        # Cessionário 88
        ces_petr4 = self.engine.get_custody_position(88, "88001", "PETR4")
        self.assertIsNotNone(ces_petr4)
        self.assertEqual(ces_petr4.total_quantity, Decimal("500"))
        self.assertEqual(ces_petr4.available_quantity, Decimal("500"))

        ces_vale3 = self.engine.get_custody_position(88, "88001", "VALE3")
        self.assertIsNotNone(ces_vale3)
        self.assertEqual(ces_vale3.total_quantity, Decimal("200"))
        self.assertEqual(ces_vale3.available_quantity, Decimal("200"))

        ces_hglg11 = self.engine.get_custody_position(88, "88001", "HGLG11")
        self.assertIsNotNone(ces_hglg11)
        self.assertEqual(ces_hglg11.total_quantity, Decimal("150"))
        self.assertEqual(ces_hglg11.available_quantity, Decimal("150"))

        # Conservação global: Total Inicial == Total Final
        self.assertEqual(ced_petr4.total_quantity + ces_petr4.total_quantity, Decimal("1000"))
        self.assertEqual(ced_vale3.total_quantity + ces_vale3.total_quantity, Decimal("500"))
        self.assertEqual(ced_hglg11.total_quantity + ces_hglg11.total_quantity, Decimal("300"))

    def test_blocked_by_judicial_order(self) -> None:
        """Valida que o motor bloqueia a STVM e transiciona para RECUSADA quando o ativo

        possui indisponibilidade ou bloqueio judicial SISBAJUD.
        """
        # Saldo total 1000, porém 800 estão bloqueados judicialmente e apenas 200 disponíveis
        self.engine.set_custody_position(
            CustodyPosition(
                participant=105,
                account="105001",
                symbol="PETR4",
                isin="BRPETRACNPR6",
                total_quantity=Decimal("1000"),
                available_quantity=Decimal("200"),
                blocked_judicial_quantity=Decimal("800"),
            )
        )

        request = STVMRequest(
            protocol="STVM-JUD-001",
            transfer_reason=TransferReason.MESMA_TITULARIDADE,
            investor_id="12345678900",
            investor_name="CARLOS EDUARDO SILVA",
            source_participant=105,
            source_account="105001",
            target_participant=88,
            target_account="88001",
            items=[
                STVMAssetItem(symbol="PETR4", isin="BRPETRACNPR6", quantity=Decimal("500")),
            ],
        )
        self.engine.register_request(request)

        # A validação deve falhar e lançar exceção específica
        with self.assertRaises(STVMCustodyEligibilityError) as ctx:
            self.engine.validate_cedente("STVM-JUD-001")

        self.assertEqual(ctx.exception.rejection_code, STVMRejectionCode.REJ_SALDO_BLOQUEADO_JUDICIAL)

        # O estado da requisição deve ser RECUSADA com código correspondente
        updated_req = self.engine.get_request("STVM-JUD-001") if hasattr(self.engine, "get_request") else self.engine.requests["STVM-JUD-001"]
        self.assertEqual(updated_req.status, STVMStatus.RECUSADA)
        self.assertEqual(updated_req.rejection_code, STVMRejectionCode.REJ_SALDO_BLOQUEADO_JUDICIAL.value)
        self.assertIn("SISBAJUD", updated_req.rejection_reason)

    def test_blocked_by_margin_guarantee(self) -> None:
        """Valida que o motor bloqueia a STVM quando o ativo está depositado em garantia/margem

        na Câmara B3 (CORE).
        """
        # Saldo total 1000, 900 alocados em margem de garantia e apenas 100 livres
        self.engine.set_custody_position(
            CustodyPosition(
                participant=105,
                account="105001",
                symbol="VALE3",
                isin="BRVALEACNOR0",
                total_quantity=Decimal("1000"),
                available_quantity=Decimal("100"),
                blocked_margin_quantity=Decimal("900"),
            )
        )

        request = STVMRequest(
            protocol="STVM-MARG-002",
            transfer_reason=TransferReason.MESMA_TITULARIDADE,
            investor_id="12345678900",
            investor_name="CARLOS EDUARDO SILVA",
            source_participant=105,
            source_account="105001",
            target_participant=88,
            target_account="88001",
            items=[
                STVMAssetItem(symbol="VALE3", isin="BRVALEACNOR0", quantity=Decimal("300")),
            ],
        )
        self.engine.register_request(request)

        with self.assertRaises(STVMCustodyEligibilityError) as ctx:
            self.engine.validate_cedente("STVM-MARG-002")

        self.assertEqual(ctx.exception.rejection_code, STVMRejectionCode.REJ_SALDO_BLOQUEADO_MARGEM)
        updated_req = self.engine.requests["STVM-MARG-002"]
        self.assertEqual(updated_req.status, STVMStatus.RECUSADA)
        self.assertEqual(updated_req.rejection_code, STVMRejectionCode.REJ_SALDO_BLOQUEADO_MARGEM.value)

    def test_rejected_titularity_divergence_without_legal_documents(self) -> None:
        """Valida recusa formal imediata quando o pedido declara Mesma Titularidade mas

        apresenta CPF/CNPJ de origem divergente do destino.
        """
        self.engine.set_custody_position(
            CustodyPosition(
                participant=105,
                account="105001",
                symbol="PETR4",
                isin="BRPETRACNPR6",
                total_quantity=Decimal("500"),
                available_quantity=Decimal("500"),
            )
        )

        request = STVMRequest(
            protocol="STVM-DIV-003",
            transfer_reason=TransferReason.MESMA_TITULARIDADE,
            investor_id="12345678900",  # CPF Origem
            investor_name="CARLOS EDUARDO SILVA",
            source_participant=105,
            source_account="105001",
            target_participant=88,
            target_account="88001",
            target_investor_id="98765432100",  # CPF Destino DIVERGENTE!
            target_investor_name="MARIA APARECIDA SILVA",
            items=[
                STVMAssetItem(symbol="PETR4", isin="BRPETRACNPR6", quantity=Decimal("100")),
            ],
        )
        self.engine.register_request(request)

        with self.assertRaises(STVMTitularityMismatchError) as ctx:
            self.engine.validate_cedente("STVM-DIV-003")

        self.assertEqual(ctx.exception.rejection_code, STVMRejectionCode.REJ_TITULARIDADE_DIVERGENTE)
        req = self.engine.requests["STVM-DIV-003"]
        self.assertEqual(req.status, STVMStatus.RECUSADA)
        self.assertEqual(req.rejection_code, STVMRejectionCode.REJ_TITULARIDADE_DIVERGENTE.value)

    def test_rejected_change_of_holder_without_documents(self) -> None:
        """Valida recusa de transferência de Mudança de Titularidade (ex: DOACAO)

        quando submetida sem documentação comprobatória obrigatória.
        """
        self.engine.set_custody_position(
            CustodyPosition(
                participant=105,
                account="105001",
                symbol="PETR4",
                isin="BRPETRACNPR6",
                total_quantity=Decimal("500"),
                available_quantity=Decimal("500"),
            )
        )

        request = STVMRequest(
            protocol="STVM-DOA-SEM-DOC",
            transfer_reason=TransferReason.DOACAO,
            investor_id="12345678900",
            investor_name="DOADOR SILVA",
            source_participant=105,
            source_account="105001",
            target_participant=88,
            target_account="88001",
            target_investor_id="98765432100",
            target_investor_name="DONATARIO SILVA",
            items=[
                STVMAssetItem(symbol="PETR4", isin="BRPETRACNPR6", quantity=Decimal("100")),
            ],
            supporting_documents=[],  # Documentação ausente!
        )
        self.engine.register_request(request)

        with self.assertRaises(STVMValidationError) as ctx:
            self.engine.validate_cedente("STVM-DOA-SEM-DOC")

        self.assertEqual(ctx.exception.rejection_code, STVMRejectionCode.REJ_DOCUMENTACAO_AUSENTE)
        req = self.engine.requests["STVM-DOA-SEM-DOC"]
        self.assertEqual(req.status, STVMStatus.RECUSADA)
        self.assertEqual(req.rejection_code, STVMRejectionCode.REJ_DOCUMENTACAO_AUSENTE.value)

    def test_approved_change_of_holder_with_documents(self) -> None:
        """Valida que uma transferência por Inventário/Partilha com documentação legal

        anexada é aprovada e liquidada com sucesso.
        """
        self.engine.set_custody_position(
            CustodyPosition(
                participant=105,
                account="105001",
                symbol="VALE3",
                isin="BRVALEACNOR0",
                total_quantity=Decimal("1000"),
                available_quantity=Decimal("1000"),
            )
        )

        request = STVMRequest(
            protocol="STVM-INV-004",
            transfer_reason=TransferReason.HERANCA_INVENTARIO,
            investor_id="11122233344",  # Falecido
            investor_name="ESPÓLIO DE JOÃO SILVA",
            source_participant=105,
            source_account="105001",
            target_participant=88,
            target_account="88001",
            target_investor_id="55566677788",  # Herdeiro
            target_investor_name="FILHO HERDEIRO SILVA",
            items=[
                STVMAssetItem(symbol="VALE3", isin="BRVALEACNOR0", quantity=Decimal("400")),
            ],
            supporting_documents=[
                "ESCRITURA_PUBLICA_INVENTARIO_LIVRO_12_FOLHA_45.pdf",
                "CERTIDAO_QUITACAO_ITCMD_SP_2026.pdf",
            ],
        )
        self.engine.register_request(request)

        # Validação do Cedente
        validated = self.engine.validate_cedente("STVM-INV-004")
        self.assertEqual(validated.status, STVMStatus.VALIDADA_CEDENTE)

        # Aceite do Cessionário
        accepted = self.engine.accept_cessionario("STVM-INV-004")
        self.assertEqual(accepted.status, STVMStatus.ACEITA_CESSIONARIO)

        # Efetivação B3
        result = self.engine.execute_transfer("STVM-INV-004")
        self.assertEqual(result.status, STVMStatus.EFETIVADA_B3)

        # Checagem de saldo transferido
        source_pos = self.engine.get_custody_position(105, "105001", "VALE3")
        target_pos = self.engine.get_custody_position(88, "88001", "VALE3")
        self.assertEqual(source_pos.total_quantity, Decimal("600"))
        self.assertEqual(target_pos.total_quantity, Decimal("400"))

    def test_load_from_fixture_json(self) -> None:
        """Valida o carregamento da fixture oficial fixtures/stvm_transfer_request.json

        e execução completa do fluxo (500 PETR4 e 200 VALE3 do participante 105 para 88).
        """
        request = STVMRequest.from_file(self.fixture_file)

        self.assertEqual(request.protocol, "STVM-20261103-000105")
        self.assertEqual(request.transfer_reason, TransferReason.MESMA_TITULARIDADE)
        self.assertEqual(request.source_participant, 105)
        self.assertEqual(request.target_participant, 88)
        self.assertEqual(len(request.items), 2)
        self.assertEqual(request.items[0].symbol, "PETR4")
        self.assertEqual(request.items[0].quantity, Decimal("500"))
        self.assertEqual(request.items[1].symbol, "VALE3")
        self.assertEqual(request.items[1].quantity, Decimal("200"))

        # Cadastra as posições de saldo no participante 105
        self.engine.set_custody_position(
            CustodyPosition(
                participant=105,
                account="105001",
                symbol="PETR4",
                isin="BRPETRACNPR6",
                total_quantity=Decimal("600"),
                available_quantity=Decimal("600"),
            )
        )
        self.engine.set_custody_position(
            CustodyPosition(
                participant=105,
                account="105001",
                symbol="VALE3",
                isin="BRVALEACNOR0",
                total_quantity=Decimal("300"),
                available_quantity=Decimal("300"),
            )
        )

        self.engine.register_request(request)
        self.engine.validate_cedente(request.protocol)
        self.engine.accept_cessionario(request.protocol)
        res = self.engine.execute_transfer(request.protocol)

        self.assertEqual(res.status, STVMStatus.EFETIVADA_B3)
        self.assertEqual(len(res.movements), 4)

    def test_regulatory_deadlines_business_days(self) -> None:
        """Valida o cálculo do prazo regulatório de D+2 útil (CVM 505)

        incluindo transição entre semanas e feriados nacionais.
        """
        # Terça-feira (2026-11-03) -> D+1 Quarta (04), D+2 Quinta (05)
        d1 = self.engine.calculate_regulatory_deadline(date(2026, 11, 3), 2)
        self.assertEqual(d1, date(2026, 11, 5))

        # Quinta-feira (2026-11-05) -> D+1 Sexta (06), pula Sáb/Dom, D+2 Segunda (09)
        d2 = self.engine.calculate_regulatory_deadline(date(2026, 11, 5), 2)
        self.assertEqual(d2, date(2026, 11, 9))

        # Sexta-feira (2026-11-06) -> D+1 Seg (09), D+2 Terça (10)
        d3 = self.engine.calculate_regulatory_deadline(date(2026, 11, 6), 2)
        self.assertEqual(d3, date(2026, 11, 10))

        # Sexta-feira pré-feriado de Finados (2026-10-30):
        # Sáb 31 / Dom 01 (Fim de semana)
        # Seg 02/11 (Feriado Nacional Finados)
        # D+1: Terça 03/11
        # D+2: Quarta 04/11
        d4 = self.engine.calculate_regulatory_deadline(date(2026, 10, 30), 2)
        self.assertEqual(d4, date(2026, 11, 4))

    def test_invalid_state_transitions(self) -> None:
        """Valida que transições irregulares na máquina de estados são bloqueadas."""
        self.engine.set_custody_position(
            CustodyPosition(
                participant=105,
                account="105001",
                symbol="PETR4",
                isin="BRPETRACNPR6",
                total_quantity=Decimal("500"),
                available_quantity=Decimal("500"),
            )
        )
        request = STVMRequest(
            protocol="STVM-INV-STATE",
            transfer_reason=TransferReason.MESMA_TITULARIDADE,
            investor_id="12345678900",
            investor_name="CARLOS EDUARDO SILVA",
            source_participant=105,
            source_account="105001",
            target_participant=88,
            target_account="88001",
            items=[STVMAssetItem(symbol="PETR4", isin="BRPETRACNPR6", quantity=Decimal("100"))],
        )
        self.engine.register_request(request)

        # Não pode executar direto sem aceite do cessionário
        with self.assertRaises(STVMStateTransitionError):
            self.engine.execute_transfer("STVM-INV-STATE")

        # Não pode dar aceite do cessionário antes de validar no cedente
        with self.assertRaises(STVMStateTransitionError):
            self.engine.accept_cessionario("STVM-INV-STATE")

    def test_cessionario_formal_rejection(self) -> None:
        """Valida recusa formal pelo participante Cessionário (ex: conta destino inválida)."""
        self.engine.set_custody_position(
            CustodyPosition(
                participant=105,
                account="105001",
                symbol="PETR4",
                isin="BRPETRACNPR6",
                total_quantity=Decimal("500"),
                available_quantity=Decimal("500"),
            )
        )
        request = STVMRequest(
            protocol="STVM-CES-REJ",
            transfer_reason=TransferReason.MESMA_TITULARIDADE,
            investor_id="12345678900",
            investor_name="CARLOS EDUARDO SILVA",
            source_participant=105,
            source_account="105001",
            target_participant=88,
            target_account="999999",  # Conta inativa
            items=[STVMAssetItem(symbol="PETR4", isin="BRPETRACNPR6", quantity=Decimal("100"))],
        )
        self.engine.register_request(request)
        self.engine.validate_cedente("STVM-CES-REJ")

        # Cessionário recusa formalmente
        rejected_req = self.engine.accept_cessionario(
            "STVM-CES-REJ",
            confirmed=False,
            notes="Conta 999999 inexistente no BTG",
        )
        self.assertEqual(rejected_req.status, STVMStatus.RECUSADA)
        self.assertEqual(rejected_req.rejection_code, STVMRejectionCode.REJ_CONTA_DESTINO_INVALIDA.value)


if __name__ == "__main__":
    unittest.main()
