"""Suíte Avançada de Chaos Testing & Resiliência a Partições de Rede na B3.

Valida a resiliência operacional e determinística em 3 cenários catastróficos:
1. Partição de rede com Detrans Estaduais durante inclusão de gravame SNG (fila de contingência assíncrona).
   - Invariante LAW-112: Unicidade e Inviolabilidade de Gravame Ativo por Chassi/Renavam.
   - Garantia de não-geração de estados fantasmas nem perda de solicitações.
2. Timeout do sistema SRO da SUSEP durante registro de apólice com retry idempotente e deduplicação de hash.
   - Invariante LAW-113: Conservação de Cosseguro (100.00%) e Inviolabilidade do Limite Máximo de Garantia (LMG).
   - Garantia de que retries concorrentes ou sucessivos não duplicam apólices nem inflam capitais segurados.
3. Partição do STR/BACEN durante liquidação bruta com reversão automática no Barramento Central.
   - Invariante LAW-106C: Atomicidade estrita de liquidação inter-sistemas (All-or-Nothing / Herstatt Risk Zero).
   - Invariante LAW-106A: Equilíbrio contábil global estrito (Soma Ativos == Soma Passivos com tolerância R$ 0,00).

100% Python Standard Library (unittest, decimal, hashlib, datetime, typing).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.central_settlement_liquidity_bus import (
    AtomicSettlementViolationError,
    B3CentralSettlementBus,
    ClearingMember,
    ClearingSubsystem,
    InstructionStatus,
    SettlementCycle,
    SettlementInstruction,
    StrBacenPartitionError,
    to_cents,
)
from src.sng_gravames_engine import (
    ContingenciaGravameItem,
    DetranNetworkPartitionError,
    DetranUF,
    GravameRecord,
    ModalidadeGravame,
    SngGravamesEngine,
    StatusContingencia,
    StatusGravame,
    VeiculoIdentificacao,
)
from src.sro_seguros_engine import (
    ApoliceRecord,
    CosseguradorShare,
    IdempotencyConflictError,
    RamoSeguro,
    SroSegurosEngine,
    StatusApolice,
    SusepTimeoutError,
)


class TestChaosSngDetranPartitionResilience(unittest.TestCase):
    """Cenário 1: Partição de rede com Detrans Estaduais durante inclusão de gravame SNG.
    
    Verifica a contenção via fila de contingência assíncrona e a invariante LAW-112.
    """

    def setUp(self) -> None:
        self.engine = SngGravamesEngine()
        self.veiculo_sp = VeiculoIdentificacao(
            chassi="9BWCA41V01P123456",
            renavam="12345678901",
            placa="ABC1D23",
            uf_emplacamento="SP",
            marca_modelo="VOLKSWAGEN POLO 1.0 TSI",
            ano_fabricacao=2024,
            ano_modelo=2025,
        )
        self.veiculo_rj = VeiculoIdentificacao(
            chassi="9BWCA41V01P654321",
            renavam="98765432101",
            placa="XYZ9K87",
            uf_emplacamento="RJ",
            marca_modelo="CHEVROLET TRACKER 1.2 TURBO",
            ano_fabricacao=2023,
            ano_modelo=2024,
        )
        self.veiculo_mg = VeiculoIdentificacao(
            chassi="9BWCA41V01P777888",
            renavam="55566677701",
            placa="MGM3E44",
            uf_emplacamento="MG",
            marca_modelo="FIAT STRADA FREEDOM 1.3",
            ano_fabricacao=2024,
            ano_modelo=2024,
        )

    def test_partition_routes_to_asynchronous_contingency_queue(self) -> None:
        """Simula partição com Detran-SP e valida que a inclusão entra na fila sem estado fantasma."""
        # Ativa partição catastrófica com Detran-SP
        self.engine.simular_particao_detran(DetranUF.SP, particionado=True)

        # Tentativa com raise_on_partition=True
        with self.assertRaises(DetranNetworkPartitionError) as ctx:
            self.engine.registrar_com_contingencia(
                contrato_numero="CTR-SP-2026-001",
                modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
                credor_cnpj="60701190000104",
                credor_nome="Banco Itaú Veículos S/A",
                devedor_documento="12345678909",
                devedor_nome="Carlos Eduardo Silva",
                veiculo=self.veiculo_sp,
                valor_financiamento=Decimal("75000.00"),
                quantidade_parcelas=48,
                taxa_juros_anual_pct=Decimal("17.9"),
                raise_on_partition=True,
            )
        self.assertIn("Detran-SP inacessível (Partição de rede)", str(ctx.exception))

        # Valida que NÃO gerou gravame ativo ("estado fantasma")
        self.assertEqual(len(self.engine.gravames), 0)
        self.assertNotIn(self.veiculo_sp.chassi, self.engine.chassi_ativo_index)
        self.assertIsNone(self.engine.consultar_por_chassi(self.veiculo_sp.chassi))

        # Valida que está seguramente retido na fila de contingência
        self.assertEqual(len(self.engine.fila_contingencia), 1)
        item = self.engine.fila_contingencia[0]
        self.assertEqual(item.status, StatusContingencia.ENFILEIRADO)
        self.assertEqual(item.uf_detran, "SP")
        self.assertEqual(item.veiculo.chassi, self.veiculo_sp.chassi)
        self.assertEqual(item.credor_cnpj, "60701190000104")
        self.assertTrue(item.idempotency_hash)

        # Invariantes LAW-112 auditadas formalmente
        inv_check = self.engine.verify_invariants(raise_on_error=True)
        self.assertTrue(inv_check["verified"])
        self.assertEqual(inv_check["active_gravames"], 0)

    def test_contingency_queue_deduplication_under_repeated_retries(self) -> None:
        """Garante que sucessivos retries do credor durante partição não geram duplicidades na fila."""
        self.engine.simular_particao_detran(DetranUF.SP, particionado=True)

        # Simula 5 retries automáticos disparados pelo sistema do banco credor
        for i in range(5):
            with self.assertRaises(DetranNetworkPartitionError):
                self.engine.registrar_com_contingencia(
                    contrato_numero="CTR-SP-2026-RETRY",
                    modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
                    credor_cnpj="60701190000104",
                    credor_nome="Banco Itaú Veículos S/A",
                    devedor_documento="12345678909",
                    devedor_nome="Carlos Eduardo Silva",
                    veiculo=self.veiculo_sp,
                    valor_financiamento=Decimal("75000.00"),
                    quantidade_parcelas=48,
                    taxa_juros_anual_pct=Decimal("17.9"),
                    raise_on_partition=True,
                )

        # Fila deve conter estritamente 1 item com retry_count = 4
        self.assertEqual(len(self.engine.fila_contingencia), 1)
        item = self.engine.fila_contingencia[0]
        self.assertEqual(item.retry_count, 4)
        self.assertEqual(len(self.engine.contingencia_chassi_map), 1)

        # Nenhum estado fantasma no índice ativo
        self.assertEqual(len(self.engine.gravames), 0)
        self.assertEqual(len(self.engine.chassi_ativo_index), 0)

    def test_partition_recovery_and_contingency_flush_integrity(self) -> None:
        """Simula a recuperação do enlace do Detran e o esvaziamento determinístico da contingência."""
        self.engine.simular_particao_detran(DetranUF.SP, particionado=True)

        # Enfileira gravame sob contingência
        item = self.engine.registrar_com_contingencia(
            contrato_numero="CTR-SP-2026-FLUSH",
            modalidade=ModalidadeGravame.ARRENDAMENTO_MERCANTIL,
            credor_cnpj="60701190000104",
            credor_nome="Banco Itaú Veículos S/A",
            devedor_documento="12345678909",
            devedor_nome="Carlos Eduardo Silva",
            veiculo=self.veiculo_sp,
            valor_financiamento=Decimal("89000.00"),
            quantidade_parcelas=60,
            taxa_juros_anual_pct=Decimal("16.5"),
            raise_on_partition=False,
        )
        self.assertIsInstance(item, ContingenciaGravameItem)
        self.assertEqual(len(self.engine.fila_contingencia), 1)

        # Enlace restabelecido
        self.engine.simular_particao_detran(DetranUF.SP, particionado=False)

        # Processamento da fila
        processados = self.engine.processar_fila_contingencia(uf=DetranUF.SP)
        self.assertEqual(len(processados), 1)

        gravame = processados[0]
        self.assertEqual(gravame.status, StatusGravame.ATIVO)
        self.assertEqual(gravame.veiculo.chassi, self.veiculo_sp.chassi)
        self.assertTrue(gravame.detran_protocolo.startswith("DETRAN-SP-"))

        # Fila deve estar limpa e índice ativo com 1 gravame
        self.assertEqual(len(self.engine.fila_contingencia), 0)
        self.assertEqual(len(self.engine.contingencia_chassi_map), 0)
        self.assertEqual(len(self.engine.chassi_ativo_index), 1)

        # Tentativa de inclusão de gravame concorrente após ativação DEVE falhar (LAW-112)
        with self.assertRaises(ValueError) as ctx:
            self.engine.registrar_gravame(
                contrato_numero="CTR-CONCORRENTE",
                modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
                credor_cnpj="00000000000191",
                credor_nome="Banco Concorrente S/A",
                devedor_documento="99988877766",
                devedor_nome="Outro Devedor",
                veiculo=self.veiculo_sp,
                valor_financiamento=Decimal("50000.00"),
                quantidade_parcelas=36,
                taxa_juros_anual_pct=Decimal("20.0"),
            )
        self.assertIn("Violação da LAW-112", str(ctx.exception))

        # Auditoria formal LAW-112
        inv = self.engine.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["verified"])
        self.assertEqual(inv["active_gravames"], 1)

    def test_selective_state_partition_isolation(self) -> None:
        """Garante que a partição de um Detran (ex: RJ) não afeta outros estados operacionais (MG)."""
        self.engine.simular_particao_detran(DetranUF.RJ, particionado=True)

        # Inclusão em MG (operacional) deve ter sucesso imediato
        rec_mg = self.engine.registrar_com_contingencia(
            contrato_numero="CTR-MG-001",
            modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
            credor_cnpj="60701190000104",
            credor_nome="Banco Itaú",
            devedor_documento="11122233344",
            devedor_nome="Produtor Rural MG",
            veiculo=self.veiculo_mg,
            valor_financiamento=Decimal("95000.00"),
            quantidade_parcelas=48,
            taxa_juros_anual_pct=Decimal("15.5"),
        )
        self.assertIsInstance(rec_mg, GravameRecord)
        self.assertEqual(rec_mg.status, StatusGravame.ATIVO)

        # Inclusão no RJ (particionado) entra na fila assíncrona
        with self.assertRaises(DetranNetworkPartitionError):
            self.engine.registrar_com_contingencia(
                contrato_numero="CTR-RJ-001",
                modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
                credor_cnpj="60701190000104",
                credor_nome="Banco Itaú",
                devedor_documento="55566677788",
                devedor_nome="Devedor RJ",
                veiculo=self.veiculo_rj,
                valor_financiamento=Decimal("110000.00"),
                quantidade_parcelas=48,
                taxa_juros_anual_pct=Decimal("18.0"),
            )

        # MG ativo, RJ em contingência
        self.assertEqual(len(self.engine.gravames), 1)
        self.assertEqual(len(self.engine.fila_contingencia), 1)
        self.assertIn(self.veiculo_mg.chassi, self.engine.chassi_ativo_index)
        self.assertNotIn(self.veiculo_rj.chassi, self.engine.chassi_ativo_index)

        # Invariantes formais LAW-112 preservadas
        self.assertTrue(self.engine.verify_invariants(raise_on_error=True)["verified"])


class TestChaosSroSusepTimeoutResilience(unittest.TestCase):
    """Cenário 2: Timeout do sistema SRO da SUSEP durante registro de apólice.
    
    Verifica retry idempotente com deduplicação de hash e integridade da LAW-113.
    """

    def setUp(self) -> None:
        self.engine = SroSegurosEngine()
        self.proposta_id = "PROP-SUSEP-AUTO-2026-99"
        self.seguradora_cnpj = "61550141000172"
        self.seguradora_nome = "Porto Seguro Companhia de Seguros Gerais"
        self.lmg = Decimal("2500000.00")
        self.premio = Decimal("14500.00")

    def test_susep_timeout_prevents_partial_commit_and_phantom_states(self) -> None:
        """Simula timeout no gateway SRO da SUSEP e valida que nenhuma apólice incompleta é criada."""
        self.engine.simular_timeout_susep(ativo=True, count=1)

        with self.assertRaises(SusepTimeoutError) as ctx:
            self.engine.registrar_apolice(
                numero_proposta=self.proposta_id,
                ramo=RamoSeguro.PATRIMONIAL_COMPREENSIVO,
                seguradora_lider_cnpj=self.seguradora_cnpj,
                seguradora_lider_nome=self.seguradora_nome,
                segurado_documento="33444555000199",
                segurado_nome="Industrias Mecanicas Brasil Ltda",
                data_emissao="2026-11-03",
                inicio_vigencia="2026-11-04T00:00:00Z",
                fim_vigencia="2027-11-04T00:00:00Z",
                limite_maximo_garantia=self.lmg,
                premio_liquido=self.premio,
            )
        self.assertIn("504 Gateway Timeout", str(ctx.exception))

        # Confirma que zero apólices ou estados fantasmas foram persistidos
        self.assertEqual(len(self.engine.apolices), 0)
        self.assertEqual(len(self.engine.proposta_deduplicacao), 0)
        self.assertEqual(len(self.engine.hash_deduplicacao), 0)

    def test_idempotent_retry_with_hash_deduplication(self) -> None:
        """Verifica que múltiplos retries pós-timeout geram exatamente 1 apólice sem duplicar LMG."""
        # Configura 2 timeouts transitórios seguidos de restabelecimento
        self.engine.simular_timeout_susep(ativo=True, count=2)

        # Executa com cliente de retry resiliente
        apolice = self.engine.registrar_apolice_com_retry(
            numero_proposta=self.proposta_id,
            ramo=RamoSeguro.PATRIMONIAL_COMPREENSIVO,
            seguradora_lider_cnpj=self.seguradora_cnpj,
            seguradora_lider_nome=self.seguradora_nome,
            segurado_documento="33444555000199",
            segurado_nome="Industrias Mecanicas Brasil Ltda",
            data_emissao="2026-11-03",
            inicio_vigencia="2026-11-04T00:00:00Z",
            fim_vigencia="2027-11-04T00:00:00Z",
            limite_maximo_garantia=self.lmg,
            premio_liquido=self.premio,
            max_retries=3,
        )

        self.assertIsInstance(apolice, ApoliceRecord)
        self.assertEqual(apolice.status, StatusApolice.VIGENTE)
        self.assertEqual(len(self.engine.apolices), 1)

        # Agora simula 5 retries adicionais com o mesmo hash/proposta (ex: reenvio por desespero da seguradora)
        for _ in range(5):
            replay = self.engine.registrar_apolice(
                numero_proposta=self.proposta_id,
                ramo=RamoSeguro.PATRIMONIAL_COMPREENSIVO,
                seguradora_lider_cnpj=self.seguradora_cnpj,
                seguradora_lider_nome=self.seguradora_nome,
                segurado_documento="33444555000199",
                segurado_nome="Industrias Mecanicas Brasil Ltda",
                data_emissao="2026-11-03",
                inicio_vigencia="2026-11-04T00:00:00Z",
                fim_vigencia="2027-11-04T00:00:00Z",
                limite_maximo_garantia=self.lmg,
                premio_liquido=self.premio,
            )
            # Deve retornar exatamente a mesma instância com o mesmo apolice_id
            self.assertEqual(replay.apolice_id, apolice.apolice_id)
            self.assertEqual(replay.susep_hash, apolice.susep_hash)

        # O repositório SRO deve ter estritamente 1 registro (duplicação zero)
        self.assertEqual(len(self.engine.apolices), 1)

        # A exposição de capital segurado (LMG) deve ser exatamente R$ 2.500.000,00 e não 6x
        total_lmg = sum(a.limite_maximo_garantia for a in self.engine.apolices.values())
        self.assertEqual(total_lmg, self.lmg)

        # Auditoria formal LAW-113
        inv = self.engine.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["verified"])
        self.assertEqual(inv["total_apolices"], 1)

    def test_idempotency_conflict_rejection(self) -> None:
        """Garante que tentativa de reutilizar mesmo número de proposta com parâmetros divergentes é rejeitada."""
        # 1. Registro inicial bem-sucedido
        self.engine.registrar_apolice(
            numero_proposta=self.proposta_id,
            ramo=RamoSeguro.AUTOMOVEL,
            seguradora_lider_cnpj=self.seguradora_cnpj,
            seguradora_lider_nome=self.seguradora_nome,
            segurado_documento="33444555000199",
            segurado_nome="Industrias Mecanicas Brasil Ltda",
            data_emissao="2026-11-03",
            inicio_vigencia="2026-11-04T00:00:00Z",
            fim_vigencia="2027-11-04T00:00:00Z",
            limite_maximo_garantia=Decimal("100000.00"),
            premio_liquido=Decimal("3500.00"),
        )

        # 2. Tentativa com mesmo número de proposta mas LMG e Ramo alterados
        with self.assertRaises(IdempotencyConflictError) as ctx:
            self.engine.registrar_apolice(
                numero_proposta=self.proposta_id,
                ramo=RamoSeguro.PATRIMONIAL_COMPREENSIVO,
                seguradora_lider_cnpj=self.seguradora_cnpj,
                seguradora_lider_nome=self.seguradora_nome,
                segurado_documento="33444555000199",
                segurado_nome="Industrias Mecanicas Brasil Ltda",
                data_emissao="2026-11-03",
                inicio_vigencia="2026-11-04T00:00:00Z",
                fim_vigencia="2027-11-04T00:00:00Z",
                limite_maximo_garantia=Decimal("500000.00"),
                premio_liquido=Decimal("8000.00"),
            )
        self.assertIn("Conflito de Idempotência", str(ctx.exception))

        # Mantém exatamente o registro original íntegro
        self.assertEqual(len(self.engine.apolices), 1)

    def test_cosseguro_inviolability_across_timeouts_and_retries(self) -> None:
        """Valida que o particionamento de cosseguro em 3 seguradoras respeita LAW-113 sob retry."""
        cosseguradores = [
            CosseguradorShare(
                cnpj="61550141000172",
                razao_social="Porto Seguro",
                percentual_quota=Decimal("50.00"),
                is_lider=True,
            ),
            CosseguradorShare(
                cnpj="33000118000179",
                razao_social="Bradesco Seguros S/A",
                percentual_quota=Decimal("30.00"),
                is_lider=False,
            ),
            CosseguradorShare(
                cnpj="08816202000100",
                razao_social="Mapfre Seguros Gerais S/A",
                percentual_quota=Decimal("20.00"),
                is_lider=False,
            ),
        ]

        self.engine.simular_timeout_susep(ativo=True, count=1)
        apolice = self.engine.registrar_apolice_com_retry(
            numero_proposta="PROP-COSSEGURO-MULTI",
            ramo=RamoSeguro.GARANTIA_SEGURADORA,
            seguradora_lider_cnpj="61550141000172",
            seguradora_lider_nome="Porto Seguro",
            segurado_documento="11222333000144",
            segurado_nome="Consórcio Linha 6 Metrô SP",
            data_emissao="2026-11-03",
            inicio_vigencia="2026-11-04T00:00:00Z",
            fim_vigencia="2029-11-04T00:00:00Z",
            limite_maximo_garantia=Decimal("150000000.00"),
            premio_liquido=Decimal("4500000.00"),
            cosseguradores=cosseguradores,
            max_retries=2,
        )

        self.assertEqual(len(apolice.cosseguradores), 3)
        self.assertEqual(sum(c.percentual_quota for c in apolice.cosseguradores), Decimal("100.00"))
        self.assertTrue(self.engine.verify_invariants(raise_on_error=True)["verified"])


class TestChaosStrBacenGrossSettlementReversal(unittest.TestCase):
    """Cenário 3: Partição do STR/BACEN durante liquidação bruta com reversão automática no Barramento Central (LAW-106C).
    
    Verifica atomicidade All-or-Nothing, eliminação de Risco de Principal (Herstatt Risk) e equilíbrio contábil (LAW-106A).
    """

    def setUp(self) -> None:
        self.fixture_path = ROOT_DIR / "fixtures" / "central_settlement_liquidity_bus.json"
        self.bus, _ = B3CentralSettlementBus.from_fixture(self.fixture_path)
        self.initial_imbalance = self.bus.verify_invariants(raise_on_error=True)

    def test_str_partition_triggers_immediate_automatic_reversal(self) -> None:
        """Simula partição do STR/BACEN durante a execução de grupo atômico e audita reversão."""
        atomic_group = "ATOMIC-CHAOS-STR-001"
        m1 = "MC-00341"  # Itaú Unibanco
        m2 = "MC-00237"  # Bradesco BBI

        # Saldos pré-liquidação
        m1_initial_cash = self.bus.members[m1].cash_settlement_balance
        m2_initial_cash = self.bus.members[m2].cash_settlement_balance

        # Perda de R$ 50.000.000,00 dividida em duas pernas atômicas inter-sistemas
        leg1 = SettlementInstruction(
            instruction_id="INST-ATOMIC-LEG1-OTC",
            subsystem=ClearingSubsystem.NOME_BALCAO,
            cycle=SettlementCycle.D0,
            debtor_member_id=m1,
            creditor_member_id=m2,
            gross_amount=Decimal("50000000.00"),
            atomic_group_id=atomic_group,
            description="Perna B3 NoMe Derivativos OTC",
        )
        leg2 = SettlementInstruction(
            instruction_id="INST-ATOMIC-LEG2-STR",
            subsystem=ClearingSubsystem.FIXED_INCOME,
            cycle=SettlementCycle.D0,
            debtor_member_id=m2,
            creditor_member_id=m1,
            gross_amount=Decimal("50000000.00"),
            atomic_group_id=atomic_group,
            description="Perna STR/BACEN Reserva Bancária LDL0001",
        )
        self.bus.add_instruction(leg1)
        self.bus.add_instruction(leg2)

        # Executa com partição simulada no STR/BACEN
        success = self.bus.execute_atomic_group(
            atomic_group_id=atomic_group,
            simulate_str_partition=True,
            raise_on_partition=False,
        )
        self.assertFalse(success)

        # Validação da Invariante LAW-106C: Ambas as pernas DEVEM estar em ROLLED_BACK
        self.assertEqual(leg1.status, InstructionStatus.ROLLED_BACK)
        self.assertEqual(leg2.status, InstructionStatus.ROLLED_BACK)

        # Validação de Ausência de Estados Fantasmas ou débitos unilaterais
        self.assertEqual(self.bus.members[m1].cash_settlement_balance, m1_initial_cash)
        self.assertEqual(self.bus.members[m2].cash_settlement_balance, m2_initial_cash)

        # Validação da Invariante LAW-106A: Equilíbrio Global Estrito (Ativos == Passivos)
        invariants = self.bus.verify_invariants(raise_on_error=True)
        self.assertTrue(invariants["LAW-106A_global_accounting_equilibrium"])
        self.assertTrue(invariants["LAW-106C_intersystem_settlement_atomicity"])
        self.assertTrue(invariants["all_invariants_hold"])

    def test_str_partition_with_exception_triggers_rollback(self) -> None:
        """Simula exceção de partição de rede lançada pelo gateway STR durante validação de lote."""
        atomic_group = "ATOMIC-CHAOS-STR-EXC"
        m1 = "MC-00341"  # Itaú
        m3 = "MC-00088"  # BTG

        m1_cash_before = self.bus.members[m1].cash_settlement_balance
        m3_cash_before = self.bus.members[m3].cash_settlement_balance

        inst_a = SettlementInstruction(
            instruction_id="INST-CHAOS-LEG-A",
            subsystem=ClearingSubsystem.EQUITIES,
            cycle=SettlementCycle.D0,
            debtor_member_id=m1,
            creditor_member_id=m3,
            gross_amount=Decimal("30000000.00"),
            atomic_group_id=atomic_group,
        )
        inst_b = SettlementInstruction(
            instruction_id="INST-CHAOS-LEG-B-BACEN",
            subsystem=ClearingSubsystem.FIXED_INCOME,
            cycle=SettlementCycle.D0,
            debtor_member_id=m3,
            creditor_member_id=m1,
            gross_amount=Decimal("30000000.00"),
            atomic_group_id=atomic_group,
            description="STR/BACEN Reserva Bancária",
        )
        self.bus.add_instruction(inst_a)
        self.bus.add_instruction(inst_b)

        # Validador de gateway que falha catastroficamente ao conectar no enlace STR BACEN
        def failing_str_gateway(inst: SettlementInstruction) -> bool:
            if "BACEN" in inst.instruction_id or "STR" in inst.description:
                raise StrBacenPartitionError("Enlace STR MQSeries indisponível: ECONNRESET pelo peer BACEN")
            return True

        with self.assertRaises(StrBacenPartitionError):
            self.bus.execute_atomic_group(
                atomic_group_id=atomic_group,
                str_gateway_validator=failing_str_gateway,
                raise_on_partition=True,
            )

        # LAW-106C: All-or-Nothing comprovado
        self.assertEqual(inst_a.status, InstructionStatus.ROLLED_BACK)
        self.assertEqual(inst_b.status, InstructionStatus.ROLLED_BACK)

        # Saldos financeiros inalterados (Zero perdas contábeis)
        self.assertEqual(self.bus.members[m1].cash_settlement_balance, m1_cash_before)
        self.assertEqual(self.bus.members[m3].cash_settlement_balance, m3_cash_before)

        # Balanço consolidado 100% equilibrado
        balance = self.bus.get_consolidated_balance_sheet()
        self.assertEqual(balance.accounting_imbalance, Decimal("0.00"))
        self.assertTrue(self.bus.verify_invariants(raise_on_error=True)["all_invariants_hold"])

    def test_str_link_recovery_and_successful_atomic_settlement(self) -> None:
        """Verifica que após reversão e restabelecimento do enlace STR, nova instrução liquida com sucesso."""
        atomic_group_fail = "ATOMIC-FAIL"
        atomic_group_ok = "ATOMIC-OK"
        m1 = "MC-00341"  # Itaú
        m4 = "MC-00033"  # Santander

        # 1. Tentativa com partição do STR
        i1 = SettlementInstruction(
            instruction_id="I1-FAIL",
            subsystem=ClearingSubsystem.DERIVATIVES,
            cycle=SettlementCycle.D0,
            debtor_member_id=m1,
            creditor_member_id=m4,
            gross_amount=Decimal("10000000.00"),
            atomic_group_id=atomic_group_fail,
        )
        self.bus.add_instruction(i1)
        res_fail = self.bus.execute_atomic_group(atomic_group_fail, simulate_str_partition=True)
        self.assertFalse(res_fail)
        self.assertEqual(i1.status, InstructionStatus.ROLLED_BACK)

        # 2. Restabelecimento do canal: novo grupo atômico com enlace saudável
        m1_cash_prior = self.bus.members[m1].cash_settlement_balance
        m4_cash_prior = self.bus.members[m4].cash_settlement_balance

        i2 = SettlementInstruction(
            instruction_id="I2-OK-LEG1",
            subsystem=ClearingSubsystem.DERIVATIVES,
            cycle=SettlementCycle.D0,
            debtor_member_id=m1,
            creditor_member_id=m4,
            gross_amount=Decimal("10000000.00"),
            atomic_group_id=atomic_group_ok,
        )
        i3 = SettlementInstruction(
            instruction_id="I3-OK-LEG2",
            subsystem=ClearingSubsystem.FIXED_INCOME,
            cycle=SettlementCycle.D0,
            debtor_member_id=m4,
            creditor_member_id=m1,
            gross_amount=Decimal("10000000.00"),
            atomic_group_id=atomic_group_ok,
            description="STR/BACEN Restabelecido",
        )
        self.bus.add_instruction(i2)
        self.bus.add_instruction(i3)

        res_ok = self.bus.execute_atomic_group(atomic_group_ok, simulate_str_partition=False)
        self.assertTrue(res_ok)
        self.assertEqual(i2.status, InstructionStatus.SETTLED)
        self.assertEqual(i3.status, InstructionStatus.SETTLED)

        # Posições conferidas
        self.assertEqual(self.bus.members[m1].cash_settlement_balance, m1_cash_prior)
        self.assertEqual(self.bus.members[m4].cash_settlement_balance, m4_cash_prior)

        # Todas as invariantes confirmadas
        self.assertTrue(self.bus.verify_invariants(raise_on_error=True)["all_invariants_hold"])


class TestChaosHolisticMultiSystemStress(unittest.TestCase):
    """Teste Holístico de Caos: Simulação Simultânea de Falhas Catastróficas Múltiplas.
    
    Submete simultaneamente o SNG, o SRO e o Barramento Central a partições de rede,
    timeouts e quedas de enlaces, auditando a ausência completa de estados fantasmas.
    """

    def test_combined_catastrophic_stress_and_invariants(self) -> None:
        """Executa estresse cruzado nos 3 módulos e audita todas as leis de conservação."""
        sng = SngGravamesEngine()
        sro = SroSegurosEngine()
        bus, _ = B3CentralSettlementBus.from_fixture(ROOT_DIR / "fixtures" / "central_settlement_liquidity_bus.json")

        # 1. Partição Detran-SP e Detran-RJ
        sng.simular_particao_detran(DetranUF.SP, particionado=True)
        sng.simular_particao_detran(DetranUF.RJ, particionado=True)

        for i in range(10):
            veic = VeiculoIdentificacao(
                chassi=f"9BWCA41V01P9999{i:02d}",
                renavam=f"123456789{i:02d}",
                placa=f"CHA{i:02d}00",
                uf_emplacamento="SP" if i % 2 == 0 else "RJ",
                marca_modelo="VEICULO TESTE CAOS",
                ano_fabricacao=2024,
                ano_modelo=2025,
            )
            # Todos devem ir para fila de contingência assíncrona
            item = sng.registrar_com_contingencia(
                contrato_numero=f"CTR-CHAOS-{i}",
                modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
                credor_cnpj="60701190000104",
                credor_nome="Banco Itaú",
                devedor_documento=f"000111222{i:02d}",
                devedor_nome=f"Devedor {i}",
                veiculo=veic,
                valor_financiamento=Decimal("50000.00"),
                quantidade_parcelas=36,
                taxa_juros_anual_pct=Decimal("15.0"),
                raise_on_partition=False,
            )
            self.assertIsInstance(item, ContingenciaGravameItem)

        # 0 gravames ativos, 10 na contingência
        self.assertEqual(len(sng.gravames), 0)
        self.assertEqual(len(sng.fila_contingencia), 10)
        self.assertTrue(sng.verify_invariants(raise_on_error=True)["verified"])

        # 2. Timeout massivo na SUSEP SRO e recuperação por retry idempotente
        sro.simular_timeout_susep(ativo=True, count=3)
        apolice_resiliente = sro.registrar_apolice_com_retry(
            numero_proposta="PROP-COMBINED-CHAOS-01",
            ramo=RamoSeguro.GARANTIA_SEGURADORA,
            seguradora_lider_cnpj="61550141000172",
            seguradora_lider_nome="Porto Seguro",
            segurado_documento="55666777000188",
            segurado_nome="Grandes Projetos B3 S/A",
            data_emissao="2026-11-03",
            inicio_vigencia="2026-11-04T00:00:00Z",
            fim_vigencia="2028-11-04T00:00:00Z",
            limite_maximo_garantia=Decimal("50000000.00"),
            premio_liquido=Decimal("1200000.00"),
            max_retries=5,
        )
        self.assertIsInstance(apolice_resiliente, ApoliceRecord)
        self.assertEqual(len(sro.apolices), 1)
        self.assertTrue(sro.verify_invariants(raise_on_error=True)["verified"])

        # 3. Partição simultânea do STR BACEN no Barramento Central
        grp = "ATOMIC-COMBINED-CHAOS"
        inst_otc = SettlementInstruction(
            instruction_id="INST-COMBINED-01",
            subsystem=ClearingSubsystem.NOME_BALCAO,
            cycle=SettlementCycle.D0,
            debtor_member_id="MC-00341",  # Itaú
            creditor_member_id="MC-00237",  # Bradesco
            gross_amount=Decimal("100000000.00"),
            atomic_group_id=grp,
        )
        inst_str = SettlementInstruction(
            instruction_id="INST-COMBINED-02",
            subsystem=ClearingSubsystem.FIXED_INCOME,
            cycle=SettlementCycle.D0,
            debtor_member_id="MC-00237",
            creditor_member_id="MC-00341",
            gross_amount=Decimal("100000000.00"),
            atomic_group_id=grp,
            description="STR/BACEN Reserva Bancária LDL0001",
        )
        bus.add_instruction(inst_otc)
        bus.add_instruction(inst_str)

        # Falha do canal STR reverte o grupo atômico integralmente
        res = bus.execute_atomic_group(grp, simulate_str_partition=True)
        self.assertFalse(res)
        self.assertEqual(inst_otc.status, InstructionStatus.ROLLED_BACK)
        self.assertEqual(inst_str.status, InstructionStatus.ROLLED_BACK)

        # Equilíbrio contábil global estrito auditado (LAW-106A e LAW-106C)
        bus_inv = bus.verify_invariants(raise_on_error=True)
        self.assertTrue(bus_inv["all_invariants_hold"])

        # 4. Restabelecimento do SNG: processa contingência
        sng.simular_particao_detran(DetranUF.SP, particionado=False)
        sng.simular_particao_detran(DetranUF.RJ, particionado=False)
        ativados = sng.processar_fila_contingencia()
        self.assertEqual(len(ativados), 10)
        self.assertEqual(len(sng.fila_contingencia), 0)
        self.assertEqual(len(sng.chassi_ativo_index), 10)
        self.assertTrue(sng.verify_invariants(raise_on_error=True)["verified"])


if __name__ == "__main__":
    unittest.main()
