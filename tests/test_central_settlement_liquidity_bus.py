"""Testes unitários e auditoria de invariantes do Barramento Central de Liquidação da B3 (FL-100).

Valida deterministicamente:
- A unificação dos 10 subsistemas e câmaras (Equities, Derivativos, NoMe Balcão,
  Renda Fixa, Agro, CVM 175, Cripto/Drex, Pix Margem, CCEE Energia, Carbono).
- Fechamento contábil consolidado D0/D+1/D+2 em tempo real.
- Invariante LAW-106A: Equilíbrio contábil estrito (Ativos == Passivos).
- Invariante LAW-106B: Não-propagação de default e absorção determinística em camadas (Default Waterfall).
- Invariante LAW-106C: Atomicidade de liquidação inter-sistemas (All-or-Nothing).
- Serialização to_dict / from_dict / from_fixture e tratamento de exceções.
"""

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.central_settlement_liquidity_bus import (
    AccountingImbalanceError,
    AtomicSettlementViolationError,
    B3CentralSettlementBus,
    CentralBusError,
    ClearingMember,
    ClearingSubsystem,
    ConsolidatedBalanceSheet,
    DefaultPropagationError,
    DefaultResolutionReport,
    InstructionStatus,
    InvalidInstructionError,
    MemberNotFoundError,
    MultilateralNetPosition,
    SafeguardLayer,
    SettlementCycle,
    SettlementInstruction,
    WaterfallStageResolution,
    to_cents,
)


class TestCentralSettlementLiquidityBus(unittest.TestCase):
    """Bateria de testes e conformidade regulatória do Barramento Central FL-100."""

    def setUp(self) -> None:
        self.fixture_path = ROOT_DIR / "fixtures" / "central_settlement_liquidity_bus.json"
        self.bus, self.raw_data = B3CentralSettlementBus.from_fixture(self.fixture_path)

    # -----------------------------------------------------------------
    # 1. CARREGAMENTO DA FIXTURE E CADASTRO DOS SUBSISTEMAS
    # -----------------------------------------------------------------

    def test_fixture_loading_and_subsystems_presence(self) -> None:
        """Verifica se o barramento inicializou corretamente com 6 membros e 10 câmaras."""
        self.assertEqual(len(self.bus.members), 6)
        self.assertEqual(self.bus.b3_skin_in_the_game, Decimal("500000000.00"))
        self.assertEqual(self.bus.b3_additional_capital, Decimal("250000000.00"))

        # Checagem de membros chave do SFN
        itau = self.bus.get_member("MC-00341")
        self.assertEqual(itau.member_name, "Itau Unibanco S.A.")
        self.assertEqual(itau.ispb, "60701190")
        self.assertEqual(itau.cash_settlement_balance, Decimal("850000000.00"))

        xp = self.bus.get_member("MC-00105")
        self.assertEqual(xp.member_name, "XP Investimentos CCTVM")

        # Verificar presença dos subsistemas mapeados nas instruções
        present_subsystems = {i.subsystem for i in self.bus.instructions.values()}
        self.assertIn(ClearingSubsystem.EQUITIES, present_subsystems)
        self.assertIn(ClearingSubsystem.DERIVATIVES, present_subsystems)
        self.assertIn(ClearingSubsystem.NOME_BALCAO, present_subsystems)
        self.assertIn(ClearingSubsystem.FIXED_INCOME, present_subsystems)
        self.assertIn(ClearingSubsystem.AGRO, present_subsystems)
        self.assertIn(ClearingSubsystem.CVM_175, present_subsystems)
        self.assertIn(ClearingSubsystem.CRYPTO_DREX, present_subsystems)
        self.assertIn(ClearingSubsystem.PIX_MARGIN, present_subsystems)
        self.assertIn(ClearingSubsystem.CCEE_ENERGY, present_subsystems)
        self.assertIn(ClearingSubsystem.CARBON, present_subsystems)

    # -----------------------------------------------------------------
    # 2. INVARIANTE LAW-106A: EQUILÍBRIO CONTÁBIL GLOBAL E SOMA-ZERO
    # -----------------------------------------------------------------

    def test_law_106a_strict_accounting_equilibrium(self) -> None:
        """Valida que Ativos == Passivos com tolerância estrita de R$ 0,00 no balanço consolidado."""
        bs = self.bus.get_consolidated_balance_sheet()
        self.assertEqual(bs.accounting_imbalance, Decimal("0.00"))
        self.assertEqual(bs.total_assets, bs.total_liabilities)
        self.assertGreater(bs.total_assets, Decimal("0.00"))

        inv = self.bus.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["LAW-106A_global_accounting_equilibrium"])
        self.assertTrue(inv["LAW-106A_multilateral_zero_sum_conservation"])
        self.assertTrue(inv["all_invariants_hold"])

    def test_multilateral_netting_zero_sum_across_cycles(self) -> None:
        """Verifica a conservação matemática soma-zero da compensação multilateral nos ciclos D0, D1, D2."""
        for cycle in [SettlementCycle.D0, SettlementCycle.D1, SettlementCycle.D2, None]:
            netting = self.bus.calculate_multilateral_netting(cycle=cycle, only_pending=False)
            sum_nets = sum((p.net_balance for p in netting.values()), Decimal("0.00"))
            self.assertEqual(sum_nets, Decimal("0.00"), f"Divergência na soma zero do ciclo {cycle}")

    def test_consolidated_netting_aggregation(self) -> None:
        """Testa o cálculo do netting consolidado (D0 + D1 + D2)."""
        cons_netting = self.bus.calculate_consolidated_netting()
        self.assertEqual(len(cons_netting), 6)
        sum_nets = sum((p.net_balance for p in cons_netting.values()), Decimal("0.00"))
        self.assertEqual(sum_nets, Decimal("0.00"))

    # -----------------------------------------------------------------
    # 3. LIQUIDAÇÃO EM TEMPO REAL D0 E CICLOS D1 / D2
    # -----------------------------------------------------------------

    def test_d0_real_time_settlement(self) -> None:
        """Verifica liquidação intradiária direta com movimentação de contas no BACEN/SPI."""
        # Instrução INST-PIX-MARGIN-007: XP (MC-00105) paga Bradesco (MC-00237) R$ 12.000.000,00
        xp = self.bus.get_member("MC-00105")
        bradesco = self.bus.get_member("MC-00237")
        xp_cash_before = xp.cash_settlement_balance
        bradesco_cash_before = bradesco.cash_settlement_balance

        settled = self.bus.execute_d0_settlement(instruction_ids=["INST-PIX-MARGIN-007"])
        self.assertEqual(len(settled), 1)
        self.assertEqual(settled[0].status, InstructionStatus.SETTLED)
        self.assertIsNotNone(settled[0].settled_at)

        self.assertEqual(xp.cash_settlement_balance, xp_cash_before - Decimal("12000000.00"))
        self.assertEqual(bradesco.cash_settlement_balance, bradesco_cash_before + Decimal("12000000.00"))

        # Re-auditoria do balanço
        inv = self.bus.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["all_invariants_hold"])

    def test_cycle_settlement_d2_multilateral(self) -> None:
        """Executa a liquidação multilateral completa do ciclo D2."""
        settled_d2 = self.bus.execute_cycle_settlement(SettlementCycle.D2)
        self.assertGreater(len(settled_d2), 0)
        for inst in settled_d2:
            self.assertEqual(inst.status, InstructionStatus.SETTLED)
            self.assertEqual(inst.cycle, SettlementCycle.D2)

        # Invariantes devem se manter 100% íntegras
        inv = self.bus.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["all_invariants_hold"])

    # -----------------------------------------------------------------
    # 4. INVARIANTE LAW-106C: ATOMICIDADE INTER-SISTEMAS (ALL-OR-NOTHING)
    # -----------------------------------------------------------------

    def test_law_106c_atomic_settlement_success(self) -> None:
        """Executa com sucesso transação atômica multi-pernas (Equities + Drex Real Digital DvP)."""
        group_id = "ATOMIC-DREX-EQUITY-001"
        res = self.bus.execute_atomic_group(group_id)
        self.assertTrue(res)

        leg_a = self.bus.instructions["INST-ATOMIC-LEG-A-009"]
        leg_b = self.bus.instructions["INST-ATOMIC-LEG-B-010"]

        self.assertEqual(leg_a.status, InstructionStatus.SETTLED)
        self.assertEqual(leg_b.status, InstructionStatus.SETTLED)

        # Ambas pernas devem ter mesmo status terminal (LAW-106C)
        inv = self.bus.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["LAW-106C_intersystem_settlement_atomicity"])

    def test_law_106c_atomic_settlement_rollback_on_liquidity_failure(self) -> None:
        """Valida rollback integral de todas as pernas se um participante não tiver saldo suficiente."""
        # Criar novo grupo atômico onde devedor não possui saldo
        new_inst_1 = SettlementInstruction(
            instruction_id="INST-ATOMIC-FAIL-A",
            subsystem=ClearingSubsystem.CCEE_ENERGY,
            cycle=SettlementCycle.D0,
            debtor_member_id="MC-00422",  # Safra tem R$ 200 Mi
            creditor_member_id="MC-00341",
            gross_amount=Decimal("500000000.00"),  # Excede o caixa de Safra
            atomic_group_id="ATOMIC-FAIL-GRP-99",
            description="Perna A: Contrato bilateral de energia CCEE de grande porte",
        )
        new_inst_2 = SettlementInstruction(
            instruction_id="INST-ATOMIC-FAIL-B",
            subsystem=ClearingSubsystem.DERIVATIVES,
            cycle=SettlementCycle.D0,
            debtor_member_id="MC-00341",  # Itaú possui saldo
            creditor_member_id="MC-00422",
            gross_amount=Decimal("500000000.00"),
            atomic_group_id="ATOMIC-FAIL-GRP-99",
            description="Perna B: Hedge DI1 atrelado ao contrato CCEE",
        )
        self.bus.add_instruction(new_inst_1)
        self.bus.add_instruction(new_inst_2)

        res = self.bus.execute_atomic_group("ATOMIC-FAIL-GRP-99")
        self.assertFalse(res)

        # Nenhuma perna pode estar liquidada; ambas devem estar ROLLED_BACK
        self.assertEqual(new_inst_1.status, InstructionStatus.ROLLED_BACK)
        self.assertEqual(new_inst_2.status, InstructionStatus.ROLLED_BACK)

        # Invariante LAW-106C preservada
        inv = self.bus.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["LAW-106C_intersystem_settlement_atomicity"])

    def test_atomic_group_already_settled_rejection(self) -> None:
        """Tentar executar grupo atômico que já foi liquidado deve levantar AtomicSettlementViolationError."""
        group_id = "ATOMIC-DREX-EQUITY-001"
        self.assertTrue(self.bus.execute_atomic_group(group_id))
        with self.assertRaises(AtomicSettlementViolationError):
            self.bus.execute_atomic_group(group_id)

    # -----------------------------------------------------------------
    # 5. INVARIANTE LAW-106B: DEFAULT WATERFALL INTEGRADO E ZERO CONTÁGIO
    # -----------------------------------------------------------------

    def test_law_106b_default_waterfall_and_cascade_prevention(self) -> None:
        """Valida absorção escalonada em 5 camadas com 100% de honra aos credores e zero quebra em cascata."""
        # Forçar cenário de inadimplência em D1 zerando o caixa de BTG (MC-00088)
        btg = self.bus.get_member("MC-00088")
        btg.cash_settlement_balance = Decimal("0.00")

        # Criar obrigação expressiva para BTG em D1 para colocá-lo em saldo devedor
        stress_inst = SettlementInstruction(
            instruction_id="INST-STRESS-DERIVATIVES-BTG",
            subsystem=ClearingSubsystem.DERIVATIVES,
            cycle=SettlementCycle.D1,
            debtor_member_id="MC-00088",
            creditor_member_id="MC-00341",
            gross_amount=Decimal("450000000.00"),
            description="Chamada maciça de margem e ajuste de derivativos em estresse",
        )
        self.bus.add_instruction(stress_inst)

        # Executar a declaração de default e resolução
        report = self.bus.declare_default_and_resolve(defaulter_member_id="MC-00088", cycle=SettlementCycle.D1)

        self.assertTrue(report.resolved)
        self.assertEqual(report.cascade_defaults_occurred, 0)
        self.assertEqual(report.creditors_payout_percentage, Decimal("100.00"))
        self.assertGreater(len(report.stages), 0)

        # Checagem das camadas consumidas
        total_consumed = sum((s.consumed_amount for s in report.stages), Decimal("0.00"))
        self.assertEqual(total_consumed, report.unfunded_debt)

        # Status das instruções devedoras do inadimplente foi atualizado
        self.assertEqual(stress_inst.status, InstructionStatus.WATERFALL_RESOLVED)

        # Auditoria formal da invariante LAW-106B
        inv = self.bus.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["LAW-106B_no_default_propagation_and_layered_absorption"])

    def test_default_deep_layers_l4_and_l5(self) -> None:
        """Testa cenário extremo que atinge mutualização (L4) e aval adicional (L5)."""
        # Criar membro com garantias baixas e default gigantesco
        weak_member = ClearingMember(
            member_id="MC-WEAK-99",
            member_name="Corretora Ficticia S.A.",
            ispb="99999999",
            cash_settlement_balance=Decimal("0.00"),
            deposited_margins=Decimal("10000000.00"),  # L1: R$ 10 Mi
            default_fund_contribution=Decimal("5000000.00"),  # L2: R$ 5 Mi
            assessment_call_cap=Decimal("5000000.00"),
        )
        self.bus.register_member(weak_member)

        # Definir SITG pequeno para forçar Nível 4 e Nível 5
        self.bus.b3_skin_in_the_game = Decimal("20000000.00")  # L3: R$ 20 Mi
        self.bus.b3_additional_capital = Decimal("10000000.00")  # L5: R$ 10 Mi

        # Dívida de R$ 80.000.000,00:
        # L1 consome 10 Mi (resta 70 Mi)
        # L2 consome 5 Mi (resta 65 Mi)
        # L3 consome 20 Mi (resta 45 Mi)
        # L4 consome 45 Mi pro-rata entre os outros membros adimplentes
        huge_debt_inst = SettlementInstruction(
            instruction_id="INST-WEAK-HUGE-DEBT",
            subsystem=ClearingSubsystem.NOME_BALCAO,
            cycle=SettlementCycle.D1,
            debtor_member_id="MC-WEAK-99",
            creditor_member_id="MC-00341",
            gross_amount=Decimal("80000000.00"),
            description="Exposição extrema em swap sem colateral",
        )
        self.bus.add_instruction(huge_debt_inst)

        report = self.bus.declare_default_and_resolve("MC-WEAK-99", SettlementCycle.D1)
        self.assertTrue(report.resolved)
        self.assertEqual(report.unfunded_debt, Decimal("80000000.00"))

        layer_names = [s.layer for s in report.stages]
        self.assertIn(SafeguardLayer.L1_DEFAULTER_MARGINS, layer_names)
        self.assertIn(SafeguardLayer.L2_DEFAULTER_DEFAULT_FUND, layer_names)
        self.assertIn(SafeguardLayer.L3_B3_SKIN_IN_THE_GAME, layer_names)
        self.assertIn(SafeguardLayer.L4_MUTUALIZED_DEFAULT_FUND, layer_names)

        inv = self.bus.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["LAW-106B_no_default_propagation_and_layered_absorption"])

    def test_default_exceeding_all_safeguards_raises_error(self) -> None:
        """Default que supere o valor agregado de todas as 5 camadas deve levantar DefaultPropagationError."""
        bankrupt_member = ClearingMember(
            member_id="MC-TITANIC",
            member_name="Mega Defaulter S.A.",
            ispb="00000001",
            cash_settlement_balance=Decimal("0.00"),
            deposited_margins=Decimal("1000.00"),
            default_fund_contribution=Decimal("1000.00"),
            assessment_call_cap=Decimal("1000.00"),
        )
        self.bus.register_member(bankrupt_member)

        # Instrução de R$ 100 Bilhões
        apocalyptic_inst = SettlementInstruction(
            instruction_id="INST-APOCALYPSE",
            subsystem=ClearingSubsystem.DERIVATIVES,
            cycle=SettlementCycle.D1,
            debtor_member_id="MC-TITANIC",
            creditor_member_id="MC-00341",
            gross_amount=Decimal("100000000000.00"),
            description="Default catastrófico além de todas as salvaguardas",
        )
        self.bus.add_instruction(apocalyptic_inst)

        with self.assertRaises(DefaultPropagationError):
            self.bus.declare_default_and_resolve("MC-TITANIC", SettlementCycle.D1)

    # -----------------------------------------------------------------
    # 6. SERIALIZAÇÃO E CARREGAMENTO (ROUND-TRIP to_dict / from_dict)
    # -----------------------------------------------------------------

    def test_roundtrip_serialization_to_dict_and_from_dict(self) -> None:
        """Verifica fidelidade total de serialização e deserialização do barramento."""
        bus_dict = self.bus.to_dict()
        self.assertEqual(bus_dict["flow"], "FL-100")
        self.assertEqual(bus_dict["name"], "B3 Central Settlement & Liquidity Bus")
        self.assertEqual(len(bus_dict["members"]), 6)
        self.assertEqual(len(bus_dict["instructions"]), 11)

        reconstructed_bus = B3CentralSettlementBus.from_dict(bus_dict)
        self.assertEqual(len(reconstructed_bus.members), 6)
        self.assertEqual(len(reconstructed_bus.instructions), 11)
        self.assertEqual(reconstructed_bus.b3_skin_in_the_game, self.bus.b3_skin_in_the_game)

        # Invariantes preservadas no objeto reconstituído
        inv = reconstructed_bus.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["all_invariants_hold"])

    def test_domain_dataclasses_serialization(self) -> None:
        """Testa to_dict e from_dict das dataclasses isoladas."""
        # MultilateralNetPosition
        net = MultilateralNetPosition(
            member_id="MC-00105",
            cycle=SettlementCycle.D1,
            gross_receivables=Decimal("100.00"),
            gross_payables=Decimal("40.00"),
            net_balance=Decimal("60.00"),
        )
        d_net = net.to_dict()
        net_rec = MultilateralNetPosition.from_dict(d_net)
        self.assertEqual(net.net_balance, net_rec.net_balance)

        # ConsolidatedBalanceSheet
        bs = self.bus.get_consolidated_balance_sheet()
        d_bs = bs.to_dict()
        bs_rec = ConsolidatedBalanceSheet.from_dict(d_bs)
        self.assertEqual(bs.total_assets, bs_rec.total_assets)

        # WaterfallStageResolution
        stage = WaterfallStageResolution(
            layer=SafeguardLayer.L1_DEFAULTER_MARGINS,
            available_amount=Decimal("500.00"),
            consumed_amount=Decimal("200.00"),
            remaining_deficit=Decimal("0.00"),
            allocations={"MC-01": Decimal("200.00")},
        )
        d_stage = stage.to_dict()
        stage_rec = WaterfallStageResolution.from_dict(d_stage)
        self.assertEqual(stage.consumed_amount, stage_rec.consumed_amount)

    # -----------------------------------------------------------------
    # 7. TRATAMENTO DE EXCEÇÕES E LIMITES
    # -----------------------------------------------------------------

    def test_validation_and_error_handling(self) -> None:
        """Testa regras de integridade cadastral e parâmetros inválidos."""
        # Membro inexistente
        with self.assertRaises(MemberNotFoundError):
            self.bus.get_member("MC-NAO-EXISTE")

        with self.assertRaises(MemberNotFoundError):
            self.bus.add_instruction(
                SettlementInstruction(
                    instruction_id="INST-BAD-MEMBER",
                    subsystem=ClearingSubsystem.EQUITIES,
                    cycle=SettlementCycle.D2,
                    debtor_member_id="MC-GHOST",
                    creditor_member_id="MC-00105",
                    gross_amount=Decimal("100.00"),
                )
            )

        # Devedor e credor idênticos
        with self.assertRaises(InvalidInstructionError):
            SettlementInstruction(
                instruction_id="INST-BAD-SAME",
                subsystem=ClearingSubsystem.EQUITIES,
                cycle=SettlementCycle.D2,
                debtor_member_id="MC-00105",
                creditor_member_id="MC-00105",
                gross_amount=Decimal("100.00"),
            )

        # Valor nulo ou negativo
        with self.assertRaises(InvalidInstructionError):
            SettlementInstruction(
                instruction_id="INST-BAD-AMOUNT",
                subsystem=ClearingSubsystem.EQUITIES,
                cycle=SettlementCycle.D2,
                debtor_member_id="MC-00105",
                creditor_member_id="MC-00341",
                gross_amount=Decimal("-500.00"),
            )

        # Declarar default de membro com saldo credor ou nulo
        with self.assertRaises(CentralBusError):
            self.bus.declare_default_and_resolve("MC-00341", SettlementCycle.D2)


if __name__ == "__main__":
    unittest.main()
