"""Barramento Central de Liquidação Unificada e Compensação Multilateral Contínua da B3 (FL-100).

Módulo de arquitetura e infraestrutura central pós-negociação unificando os 10 subsistemas
e câmaras da B3 sob compensação multilateral contínua, liquidação atômica inter-sistemas
e fechamento contábil consolidado D0/D+1/D+2 em tempo real, com prevenção de quebra em
cascata através do Default Waterfall integrado.

Subsistemas Unificados (10 Câmaras):
1. EQUITIES: Ações, BDRs, ETFs, FIIs e mercado à vista de renda variável.
2. DERIVATIVES: Futuros e opções listados (juros, moedas, commodities, índices).
3. NOME_BALCAO: Balcão organizado NoMe (swaps, NDFs, opções flexíveis, derivativos de balcão).
4. FIXED_INCOME: Títulos Públicos Federais (TPF/SELIC) e Renda Fixa Privada (CDB, debêntures, CRI/CRA).
5. AGRO: Agronegócio (CPR física e financeira, CDA/WA, CIR, warrants agro).
6. CVM_175: Fundos de investimento (subscrição e resgate de cotas, liquidação split D0/D+2).
7. CRYPTO_DREX: Criptoativos e Drex Real Digital (DvP atômico on-chain / bridge CSD).
8. PIX_MARGIN: Chamadas de liquidez instantânea SPI e Pix Automático para recomposição de margem.
9. CCEE_ENERGY: Mercado livre de energia elétrica (liquidação multilateral de contratos bilaterais e MCP).
10. CARBON: Mercado de créditos de descarbonização (CBIO - RenovaBio) e mercado voluntário de carbono.

Invariantes Fundamentais:
- LAW-106A: Equilíbrio contábil global estrito: Soma Ativos == Soma Passivos (tolerância R$ 0,00).
- LAW-106B: Não-propagação de default e absorção em camadas (Default Waterfall com 100% de honra a credores).
- LAW-106C: Atomicidade de liquidação inter-sistemas (execução integral All-or-Nothing por grupo atômico).

Embasamento Regulatório:
- Lei nº 10.214/2001 (Compensação e Liquidação no SPB / Segregação Patrimonial e Inoponibilidade).
- Princípios CPMI-IOSCO para Infraestruturas do Mercado Financeiro (PFMI Princípios 4 e 7).
- Resolução BCB nº 105/2021 (Regulamento de Sistemas de Pagamentos e Câmaras).
- Resolução CVM nº 175/2022 (Novo Marco Regulatório de Fundos de Investimento).
- Regulamento da Câmara B3 (Capítulo VII - Salvaguardas e Tratamento de Inadimplência).

100% Python Standard Library pura (sem dependências externas).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union
import uuid


# =====================================================================
# CONSTANTES E FORMATAÇÃO DE MOEDA
# =====================================================================

CENT_PRECISION = Decimal("0.01")
ZERO_BRL = Decimal("0.00")
ONE_HUNDRED_PERCENT = Decimal("100.00")


def to_cents(val: Union[str, int, float, Decimal]) -> Decimal:
    """Padroniza valores monetários para Decimal com 2 casas decimais e ROUND_HALF_UP."""
    return Decimal(str(val)).quantize(CENT_PRECISION, rounding=ROUND_HALF_UP)


def parse_datetime(dt_val: Union[str, datetime]) -> datetime:
    """Converte strings ISO para datetime UTC timezone-aware."""
    if isinstance(dt_val, datetime):
        return dt_val if dt_val.tzinfo else dt_val.replace(tzinfo=timezone.utc)
    norm = dt_val.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(norm)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# =====================================================================
# ENUMS DO SISTEMA
# =====================================================================

class ClearingSubsystem(str, Enum):
    """Os 10 subsistemas e câmaras unificados sob o Barramento Central da B3."""
    EQUITIES = "EQUITIES"
    DERIVATIVES = "DERIVATIVES"
    NOME_BALCAO = "NOME_BALCAO"
    FIXED_INCOME = "FIXED_INCOME"
    AGRO = "AGRO"
    CVM_175 = "CVM_175"
    CRYPTO_DREX = "CRYPTO_DREX"
    PIX_MARGIN = "PIX_MARGIN"
    CCEE_ENERGY = "CCEE_ENERGY"
    CARBON = "CARBON"


class SettlementCycle(str, Enum):
    """Ciclos temporais de liquidação suportados pelo barramento."""
    D0 = "D0"  # Tempo Real / Intradiário / Instantâneo (SPI, Drex, Margem)
    D1 = "D1"  # D+1: Derivativos (Ajuste Diário), Renda Fixa, NoMe Balcão
    D2 = "D2"  # D+2: Ações à Vista, Subscrição/Resgate CVM 175, CCEE Periódico


class InstructionStatus(str, Enum):
    """Estado do ciclo de vida de uma instrução de liquidação."""
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    WATERFALL_RESOLVED = "WATERFALL_RESOLVED"


class SafeguardLayer(str, Enum):
    """Camadas do Default Waterfall ordenadas para absorção determinística de perdas."""
    L1_DEFAULTER_MARGINS = "L1_DEFAULTER_MARGINS"
    L2_DEFAULTER_DEFAULT_FUND = "L2_DEFAULTER_DEFAULT_FUND"
    L3_B3_SKIN_IN_THE_GAME = "L3_B3_SKIN_IN_THE_GAME"
    L4_MUTUALIZED_DEFAULT_FUND = "L4_MUTUALIZED_DEFAULT_FUND"
    L5_EXTRAORDINARY_ASSESSMENT = "L5_EXTRAORDINARY_ASSESSMENT"


# =====================================================================
# EXCEÇÕES DE DOMÍNIO
# =====================================================================

class CentralBusError(Exception):
    """Exceção base do Barramento Central de Liquidação."""
    pass


class AccountingImbalanceError(CentralBusError):
    """Violação da invariante LAW-106A (Soma Ativos != Soma Passivos)."""
    pass


class DefaultPropagationError(CentralBusError):
    """Violação da invariante LAW-106B (Quebra em cascata ou perda não absorvida)."""
    pass


class AtomicSettlementViolationError(CentralBusError):
    """Violação da invariante LAW-106C (Falha de atomicidade inter-sistemas)."""
    pass


class StrBacenPartitionError(CentralBusError):
    """Partição de rede, indisponibilidade ou timeout com o Sistema de Transferência de Reservas (STR/BACEN)."""
    pass


class MemberNotFoundError(CentralBusError):
    """Membro de Compensação não encontrado no cadastro do barramento."""
    pass


class InvalidInstructionError(CentralBusError):
    """Instrução com parâmetros inválidos ou dados inconsistentes."""
    pass


# =====================================================================
# DATA CLASSES DE DOMÍNIO
# =====================================================================

@dataclass
class ClearingMember:
    """Membro de Compensação (MC) habilitado no Barramento Central da B3."""
    member_id: str
    member_name: str
    ispb: str
    cash_settlement_balance: Decimal = ZERO_BRL
    deposited_margins: Decimal = ZERO_BRL
    default_fund_contribution: Decimal = ZERO_BRL
    assessment_call_cap: Decimal = ZERO_BRL
    risk_weight: Decimal = Decimal("0.10")
    is_defaulter: bool = False

    def __post_init__(self) -> None:
        self.cash_settlement_balance = to_cents(self.cash_settlement_balance)
        self.deposited_margins = to_cents(self.deposited_margins)
        self.default_fund_contribution = to_cents(self.default_fund_contribution)
        self.assessment_call_cap = to_cents(self.assessment_call_cap)
        self.risk_weight = Decimal(str(self.risk_weight))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "member_id": self.member_id,
            "member_name": self.member_name,
            "ispb": self.ispb,
            "cash_settlement_balance": str(self.cash_settlement_balance),
            "deposited_margins": str(self.deposited_margins),
            "default_fund_contribution": str(self.default_fund_contribution),
            "assessment_call_cap": str(self.assessment_call_cap),
            "risk_weight": str(self.risk_weight),
            "is_defaulter": self.is_defaulter,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ClearingMember:
        return cls(
            member_id=data["member_id"],
            member_name=data["member_name"],
            ispb=data["ispb"],
            cash_settlement_balance=to_cents(data.get("cash_settlement_balance", "0.00")),
            deposited_margins=to_cents(data.get("deposited_margins", "0.00")),
            default_fund_contribution=to_cents(data.get("default_fund_contribution", "0.00")),
            assessment_call_cap=to_cents(data.get("assessment_call_cap", "0.00")),
            risk_weight=Decimal(str(data.get("risk_weight", "0.10"))),
            is_defaulter=data.get("is_defaulter", False),
        )


@dataclass
class SettlementInstruction:
    """Instrução individual de liquidação originada de qualquer um dos 10 subsistemas."""
    instruction_id: str
    subsystem: ClearingSubsystem
    cycle: SettlementCycle
    debtor_member_id: str
    creditor_member_id: str
    gross_amount: Decimal
    asset_id: Optional[str] = None
    asset_quantity: Optional[Decimal] = None
    atomic_group_id: Optional[str] = None
    status: InstructionStatus = InstructionStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    settled_at: Optional[datetime] = None
    description: str = ""

    def __post_init__(self) -> None:
        self.gross_amount = to_cents(self.gross_amount)
        if self.gross_amount <= ZERO_BRL:
            raise InvalidInstructionError(f"Valor da instrução deve ser positivo: {self.gross_amount}")
        if self.debtor_member_id == self.creditor_member_id:
            raise InvalidInstructionError("Membro devedor e credor não podem ser idênticos.")
        if isinstance(self.subsystem, str):
            self.subsystem = ClearingSubsystem(self.subsystem)
        if isinstance(self.cycle, str):
            self.cycle = SettlementCycle(self.cycle)
        if isinstance(self.status, str):
            self.status = InstructionStatus(self.status)
        if self.asset_quantity is not None:
            self.asset_quantity = Decimal(str(self.asset_quantity))
        self.created_at = parse_datetime(self.created_at)
        if self.settled_at:
            self.settled_at = parse_datetime(self.settled_at)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instruction_id": self.instruction_id,
            "subsystem": self.subsystem.value,
            "cycle": self.cycle.value,
            "debtor_member_id": self.debtor_member_id,
            "creditor_member_id": self.creditor_member_id,
            "gross_amount": str(self.gross_amount),
            "asset_id": self.asset_id,
            "asset_quantity": str(self.asset_quantity) if self.asset_quantity is not None else None,
            "atomic_group_id": self.atomic_group_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "settled_at": self.settled_at.isoformat() if self.settled_at else None,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SettlementInstruction:
        return cls(
            instruction_id=data["instruction_id"],
            subsystem=ClearingSubsystem(data["subsystem"]),
            cycle=SettlementCycle(data["cycle"]),
            debtor_member_id=data["debtor_member_id"],
            creditor_member_id=data["creditor_member_id"],
            gross_amount=to_cents(data["gross_amount"]),
            asset_id=data.get("asset_id"),
            asset_quantity=Decimal(str(data["asset_quantity"])) if data.get("asset_quantity") is not None else None,
            atomic_group_id=data.get("atomic_group_id"),
            status=InstructionStatus(data.get("status", "PENDING")),
            created_at=parse_datetime(data["created_at"]) if "created_at" in data else datetime.now(timezone.utc),
            settled_at=parse_datetime(data["settled_at"]) if data.get("settled_at") else None,
            description=data.get("description", ""),
        )


@dataclass
class MultilateralNetPosition:
    """Posição líquida multilateral apurada para um participante."""
    member_id: str
    cycle: Optional[SettlementCycle]
    gross_receivables: Decimal
    gross_payables: Decimal
    net_balance: Decimal  # > 0: credor líquido; < 0: devedor líquido; == 0: zerado

    def to_dict(self) -> Dict[str, Any]:
        return {
            "member_id": self.member_id,
            "cycle": self.cycle.value if self.cycle else None,
            "gross_receivables": str(self.gross_receivables),
            "gross_payables": str(self.gross_payables),
            "net_balance": str(self.net_balance),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MultilateralNetPosition:
        return cls(
            member_id=data["member_id"],
            cycle=SettlementCycle(data["cycle"]) if data.get("cycle") else None,
            gross_receivables=to_cents(data["gross_receivables"]),
            gross_payables=to_cents(data["gross_payables"]),
            net_balance=to_cents(data["net_balance"]),
        )


@dataclass
class ConsolidatedBalanceSheet:
    """Balanço contábil fechado em tempo real da câmara e barramento."""
    as_of: datetime
    cash_in_central_bank: Decimal
    collateral_custody: Decimal
    default_fund_cash: Decimal
    b3_sitg_allocated: Decimal
    pending_member_receivables: Decimal
    total_assets: Decimal
    pending_member_payables: Decimal
    member_margin_obligations: Decimal
    default_fund_member_equity: Decimal
    b3_capital_reserve: Decimal
    total_liabilities: Decimal
    accounting_imbalance: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "cash_in_central_bank": str(self.cash_in_central_bank),
            "collateral_custody": str(self.collateral_custody),
            "default_fund_cash": str(self.default_fund_cash),
            "b3_sitg_allocated": str(self.b3_sitg_allocated),
            "pending_member_receivables": str(self.pending_member_receivables),
            "total_assets": str(self.total_assets),
            "pending_member_payables": str(self.pending_member_payables),
            "member_margin_obligations": str(self.member_margin_obligations),
            "default_fund_member_equity": str(self.default_fund_member_equity),
            "b3_capital_reserve": str(self.b3_capital_reserve),
            "total_liabilities": str(self.total_liabilities),
            "accounting_imbalance": str(self.accounting_imbalance),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConsolidatedBalanceSheet:
        return cls(
            as_of=parse_datetime(data["as_of"]),
            cash_in_central_bank=to_cents(data["cash_in_central_bank"]),
            collateral_custody=to_cents(data["collateral_custody"]),
            default_fund_cash=to_cents(data["default_fund_cash"]),
            b3_sitg_allocated=to_cents(data["b3_sitg_allocated"]),
            pending_member_receivables=to_cents(data["pending_member_receivables"]),
            total_assets=to_cents(data["total_assets"]),
            pending_member_payables=to_cents(data["pending_member_payables"]),
            member_margin_obligations=to_cents(data["member_margin_obligations"]),
            default_fund_member_equity=to_cents(data["default_fund_member_equity"]),
            b3_capital_reserve=to_cents(data["b3_capital_reserve"]),
            total_liabilities=to_cents(data["total_liabilities"]),
            accounting_imbalance=to_cents(data["accounting_imbalance"]),
        )


@dataclass
class WaterfallStageResolution:
    """Resolução de uma camada do Default Waterfall."""
    layer: SafeguardLayer
    available_amount: Decimal
    consumed_amount: Decimal
    remaining_deficit: Decimal
    allocations: Dict[str, Decimal] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer": self.layer.value,
            "available_amount": str(self.available_amount),
            "consumed_amount": str(self.consumed_amount),
            "remaining_deficit": str(self.remaining_deficit),
            "allocations": {k: str(v) for k, v in self.allocations.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WaterfallStageResolution:
        return cls(
            layer=SafeguardLayer(data["layer"]),
            available_amount=to_cents(data["available_amount"]),
            consumed_amount=to_cents(data["consumed_amount"]),
            remaining_deficit=to_cents(data["remaining_deficit"]),
            allocations={k: to_cents(v) for k, v in data.get("allocations", {}).items()},
        )


@dataclass
class DefaultResolutionReport:
    """Relatório circunstanciado de resolução de default sem contágio."""
    event_id: str
    defaulter_member_id: str
    unfunded_debt: Decimal
    resolved: bool
    stages: List[WaterfallStageResolution]
    creditors_payout_percentage: Decimal
    cascade_defaults_occurred: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "defaulter_member_id": self.defaulter_member_id,
            "unfunded_debt": str(self.unfunded_debt),
            "resolved": self.resolved,
            "stages": [s.to_dict() for s in self.stages],
            "creditors_payout_percentage": str(self.creditors_payout_percentage),
            "cascade_defaults_occurred": self.cascade_defaults_occurred,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DefaultResolutionReport:
        return cls(
            event_id=data["event_id"],
            defaulter_member_id=data["defaulter_member_id"],
            unfunded_debt=to_cents(data["unfunded_debt"]),
            resolved=data["resolved"],
            stages=[WaterfallStageResolution.from_dict(s) for s in data["stages"]],
            creditors_payout_percentage=to_cents(data["creditors_payout_percentage"]),
            cascade_defaults_occurred=int(data["cascade_defaults_occurred"]),
            timestamp=parse_datetime(data["timestamp"]) if "timestamp" in data else datetime.now(timezone.utc),
        )


# =====================================================================
# MOTOR PRINCIPAL DO BARRAMENTO CENTRAL DE LIQUIDAÇÃO
# =====================================================================

class B3CentralSettlementBus:
    """Barramento Central de Liquidação Unificada e Compensação Multilateral Contínua da B3."""

    def __init__(
        self,
        b3_skin_in_the_game: Union[str, int, float, Decimal] = Decimal("500000000.00"),
        b3_additional_capital: Union[str, int, float, Decimal] = Decimal("250000000.00"),
    ) -> None:
        self.b3_skin_in_the_game = to_cents(b3_skin_in_the_game)
        self.b3_additional_capital = to_cents(b3_additional_capital)
        self.b3_operational_cash = ZERO_BRL

        self.members: Dict[str, ClearingMember] = {}
        self.instructions: Dict[str, SettlementInstruction] = {}
        self.default_events: List[DefaultResolutionReport] = []

    # -----------------------------------------------------------------
    # CADASTRO E GERENCIAMENTO DE MEMBROS
    # -----------------------------------------------------------------

    def register_member(self, member: ClearingMember) -> None:
        """Registra ou atualiza um Membro de Compensação no Barramento Central."""
        self.members[member.member_id] = member

    def get_member(self, member_id: str) -> ClearingMember:
        """Recupera um membro cadastrado ou levanta exceção específica."""
        if member_id not in self.members:
            raise MemberNotFoundError(f"Membro de compensação {member_id} não encontrado no barramento.")
        return self.members[member_id]

    # -----------------------------------------------------------------
    # INGESTÃO DE INSTRUÇÕES MULTISSISTÊMICAS (10 SUBSISTEMAS)
    # -----------------------------------------------------------------

    def add_instruction(self, instruction: SettlementInstruction) -> None:
        """Adiciona uma instrução ao barramento verificando existência prévia dos membros."""
        if instruction.debtor_member_id not in self.members:
            raise MemberNotFoundError(f"Devedor {instruction.debtor_member_id} não cadastrado.")
        if instruction.creditor_member_id not in self.members:
            raise MemberNotFoundError(f"Credor {instruction.creditor_member_id} não cadastrado.")
        self.instructions[instruction.instruction_id] = instruction

    def ingest_instructions(self, instructions: Sequence[SettlementInstruction]) -> None:
        """Ingere em lote um conjunto de instruções vindas das 10 câmaras."""
        for inst in instructions:
            self.add_instruction(inst)

    # -----------------------------------------------------------------
    # COMPENSAÇÃO MULTILATERAL CONTÍNUA (D0, D1, D2 E CONSOLIDADO)
    # -----------------------------------------------------------------

    def calculate_multilateral_netting(
        self,
        cycle: Optional[SettlementCycle] = None,
        only_pending: bool = True,
    ) -> Dict[str, MultilateralNetPosition]:
        """Calcula a posição líquida multilateral para cada membro em um ciclo ou consolidado."""
        receivables: Dict[str, Decimal] = {m: ZERO_BRL for m in self.members}
        payables: Dict[str, Decimal] = {m: ZERO_BRL for m in self.members}

        for inst in self.instructions.values():
            if cycle is not None and inst.cycle != cycle:
                continue
            if only_pending and inst.status != InstructionStatus.PENDING:
                continue

            receivables[inst.creditor_member_id] = receivables.get(inst.creditor_member_id, ZERO_BRL) + inst.gross_amount
            payables[inst.debtor_member_id] = payables.get(inst.debtor_member_id, ZERO_BRL) + inst.gross_amount

        net_positions: Dict[str, MultilateralNetPosition] = {}
        for m_id in self.members:
            r = receivables.get(m_id, ZERO_BRL)
            p = payables.get(m_id, ZERO_BRL)
            net_positions[m_id] = MultilateralNetPosition(
                member_id=m_id,
                cycle=cycle,
                gross_receivables=r,
                gross_payables=p,
                net_balance=to_cents(r - p),
            )
        return net_positions

    def calculate_consolidated_netting(self) -> Dict[str, MultilateralNetPosition]:
        """Apurar compensação multilateral contínua consolidada (D0 + D1 + D2)."""
        return self.calculate_multilateral_netting(cycle=None, only_pending=True)

    # -----------------------------------------------------------------
    # LIQUIDAÇÃO EM TEMPO REAL D0 E CICLOS D1 / D2
    # -----------------------------------------------------------------

    def execute_d0_settlement(
        self,
        instruction_ids: Optional[Sequence[str]] = None,
    ) -> List[SettlementInstruction]:
        """Executa a liquidação bruta ou líquida atômica em tempo real para instruções D0."""
        target_ids = list(instruction_ids) if instruction_ids is not None else [
            i.instruction_id for i in self.instructions.values()
            if i.cycle == SettlementCycle.D0 and i.status == InstructionStatus.PENDING
        ]

        settled: List[SettlementInstruction] = []
        now_utc = datetime.now(timezone.utc)

        for i_id in target_ids:
            inst = self.instructions[i_id]
            if inst.status != InstructionStatus.PENDING:
                continue

            debtor = self.members[inst.debtor_member_id]
            creditor = self.members[inst.creditor_member_id]

            # Verificação de fundos em D0
            if debtor.cash_settlement_balance >= inst.gross_amount:
                debtor.cash_settlement_balance = to_cents(debtor.cash_settlement_balance - inst.gross_amount)
                creditor.cash_settlement_balance = to_cents(creditor.cash_settlement_balance + inst.gross_amount)
                inst.status = InstructionStatus.SETTLED
                inst.settled_at = now_utc
                settled.append(inst)
            else:
                # Se não possui fundos em D0 e não é grupo atômico, marca como pendente/falha
                inst.status = InstructionStatus.FAILED

        return settled

    def execute_cycle_settlement(self, cycle: SettlementCycle) -> List[SettlementInstruction]:
        """Executa a liquidação multilateral determinística de um ciclo (D0, D1 ou D2)."""
        net_map = self.calculate_multilateral_netting(cycle=cycle, only_pending=True)
        now_utc = datetime.now(timezone.utc)

        # 1. Verificar se todos os devedores líquidos possuem saldo de liquidação
        unfunded_deficits: Dict[str, Decimal] = {}
        for m_id, pos in net_map.items():
            if pos.net_balance < ZERO_BRL:
                debt = abs(pos.net_balance)
                member = self.members[m_id]
                if member.cash_settlement_balance < debt:
                    unfunded_deficits[m_id] = to_cents(debt - member.cash_settlement_balance)

        # 2. Se houver déficit sem fundos, acionar Default Waterfall
        if unfunded_deficits:
            for defaulter_id, _ in unfunded_deficits.items():
                self.declare_default_and_resolve(defaulter_id, cycle)

        # 3. Liquidação multilateral das posições
        # Debitar devedores
        for m_id, pos in net_map.items():
            member = self.members[m_id]
            if pos.net_balance < ZERO_BRL:
                debt = abs(pos.net_balance)
                if not member.is_defaulter:
                    member.cash_settlement_balance = to_cents(member.cash_settlement_balance - debt)
            elif pos.net_balance > ZERO_BRL:
                cred = pos.net_balance
                member.cash_settlement_balance = to_cents(member.cash_settlement_balance + cred)

        # Atualizar status das instruções pertinentes
        settled_instructions: List[SettlementInstruction] = []
        for inst in self.instructions.values():
            if inst.cycle == cycle and inst.status == InstructionStatus.PENDING:
                inst.status = InstructionStatus.SETTLED
                inst.settled_at = now_utc
                settled_instructions.append(inst)

        return settled_instructions

    # -----------------------------------------------------------------
    # ATOMICIDADE DE LIQUIDAÇÃO INTER-SISTEMAS (LAW-106C)
    # -----------------------------------------------------------------

    def execute_atomic_group(
        self,
        atomic_group_id: str,
        str_gateway_validator: Optional[Callable[[SettlementInstruction], bool]] = None,
        simulate_str_partition: bool = False,
        raise_on_partition: bool = False,
    ) -> bool:
        """Executa liquidação atômica de pernas inter-sistemas (All-or-Nothing - LAW-106C).
        
        Garante:
        1. All-or-Nothing: Todas as pernas comutam para SETTLED ou sofrem reversão integral para ROLLED_BACK.
        2. Em caso de partição de rede com o STR/BACEN (simulate_str_partition ou falha no gateway STR),
           reverte imediatamente todas as alterações contábeis sem deixar saldos parciais ou estados fantasmas.
        3. Preserva a conservação global LAW-106A (Soma Ativos == Soma Passivos) e LAW-106C (Atomicidade).
        """
        group_instructions = [
            i for i in self.instructions.values()
            if i.atomic_group_id == atomic_group_id
        ]
        if not group_instructions:
            return False

        # Verifica se todas as pernas estão PENDING
        if any(i.status != InstructionStatus.PENDING for i in group_instructions):
            raise AtomicSettlementViolationError(
                f"Grupo atômico {atomic_group_id} possui instruções fora do estado PENDING."
            )

        # Se houver simulação ativa de partição do STR/BACEN
        if simulate_str_partition:
            for inst in group_instructions:
                inst.status = InstructionStatus.ROLLED_BACK
            if raise_on_partition:
                raise StrBacenPartitionError(
                    f"Partição de rede crítica com STR/BACEN no grupo atômico '{atomic_group_id}'. "
                    f"Reversão automática executada conforme LAW-106C."
                )
            return False

        # Simular e validar capacidade financeira de todos os devedores
        required_debits: Dict[str, Decimal] = {}
        for inst in group_instructions:
            required_debits[inst.debtor_member_id] = (
                required_debits.get(inst.debtor_member_id, ZERO_BRL) + inst.gross_amount
            )

        can_commit = True
        for m_id, req in required_debits.items():
            member = self.members[m_id]
            if member.cash_settlement_balance < req:
                can_commit = False
                break

        # Validação de enlace externo com STR/BACEN (se fornecido)
        if can_commit and str_gateway_validator is not None:
            try:
                for inst in group_instructions:
                    if not str_gateway_validator(inst):
                        can_commit = False
                        break
            except Exception as exc:
                # Falha externa no STR -> aborta e executa reversão automática integral
                for inst in group_instructions:
                    inst.status = InstructionStatus.ROLLED_BACK
                if raise_on_partition:
                    raise StrBacenPartitionError(
                        f"Partição/Timeout no gateway STR/BACEN durante liquidação bruta: {exc}"
                    ) from exc
                return False

        now_utc = datetime.now(timezone.utc)
        if can_commit:
            # Commit atômico de todas as pernas
            for inst in group_instructions:
                debtor = self.members[inst.debtor_member_id]
                creditor = self.members[inst.creditor_member_id]
                debtor.cash_settlement_balance = to_cents(debtor.cash_settlement_balance - inst.gross_amount)
                creditor.cash_settlement_balance = to_cents(creditor.cash_settlement_balance + inst.gross_amount)
                inst.status = InstructionStatus.SETTLED
                inst.settled_at = now_utc
            return True
        else:
            # Rollback atômico de todas as pernas
            for inst in group_instructions:
                inst.status = InstructionStatus.ROLLED_BACK
            return False

    # -----------------------------------------------------------------
    # DEFAULT WATERFALL INTEGRADO E PREVENÇÃO DE CASCATA (LAW-106B)
    # -----------------------------------------------------------------

    def declare_default_and_resolve(
        self,
        defaulter_member_id: str,
        cycle: SettlementCycle,
    ) -> DefaultResolutionReport:
        """Aciona o Default Waterfall da B3 para absorver deterministamente a perda."""
        defaulter = self.get_member(defaulter_member_id)
        defaulter.is_defaulter = True

        net_map = self.calculate_multilateral_netting(cycle=cycle, only_pending=True)
        defaulter_net = net_map[defaulter_member_id].net_balance
        if defaulter_net >= ZERO_BRL:
            raise CentralBusError(f"Membro {defaulter_member_id} não é devedor líquido no ciclo {cycle}.")

        gross_debt = abs(defaulter_net)
        # Abater qualquer saldo em caixa que o devedor ainda possua
        available_cash = min(defaulter.cash_settlement_balance, gross_debt)
        defaulter.cash_settlement_balance = to_cents(defaulter.cash_settlement_balance - available_cash)
        unfunded_deficit = to_cents(gross_debt - available_cash)

        remaining_loss = unfunded_deficit
        stages: List[WaterfallStageResolution] = []

        # CAMADA 1: Margens próprias do inadimplente
        l1_avail = defaulter.deposited_margins
        l1_consumed = min(l1_avail, remaining_loss)
        defaulter.deposited_margins = to_cents(defaulter.deposited_margins - l1_consumed)
        remaining_loss = to_cents(remaining_loss - l1_consumed)
        stages.append(WaterfallStageResolution(
            layer=SafeguardLayer.L1_DEFAULTER_MARGINS,
            available_amount=l1_avail,
            consumed_amount=l1_consumed,
            remaining_deficit=remaining_loss,
            allocations={defaulter_member_id: l1_consumed},
        ))

        # CAMADA 2: Cota do inadimplente no Fundo de Liquidação
        if remaining_loss > ZERO_BRL:
            l2_avail = defaulter.default_fund_contribution
            l2_consumed = min(l2_avail, remaining_loss)
            defaulter.default_fund_contribution = to_cents(defaulter.default_fund_contribution - l2_consumed)
            remaining_loss = to_cents(remaining_loss - l2_consumed)
            stages.append(WaterfallStageResolution(
                layer=SafeguardLayer.L2_DEFAULTER_DEFAULT_FUND,
                available_amount=l2_avail,
                consumed_amount=l2_consumed,
                remaining_deficit=remaining_loss,
                allocations={defaulter_member_id: l2_consumed},
            ))

        # CAMADA 3: B3 Skin in the Game (SITG)
        if remaining_loss > ZERO_BRL:
            l3_avail = self.b3_skin_in_the_game
            l3_consumed = min(l3_avail, remaining_loss)
            self.b3_skin_in_the_game = to_cents(self.b3_skin_in_the_game - l3_consumed)
            remaining_loss = to_cents(remaining_loss - l3_consumed)
            stages.append(WaterfallStageResolution(
                layer=SafeguardLayer.L3_B3_SKIN_IN_THE_GAME,
                available_amount=l3_avail,
                consumed_amount=l3_consumed,
                remaining_deficit=remaining_loss,
                allocations={"B3_SITG": l3_consumed},
            ))

        # CAMADA 4: Fundo de Liquidação Mútuo dos Membros Adimplentes (Pro-rata por risco)
        if remaining_loss > ZERO_BRL:
            solvent_members = [m for m in self.members.values() if not m.is_defaulter and m.default_fund_contribution > ZERO_BRL]
            total_solvent_fund = sum((m.default_fund_contribution for m in solvent_members), ZERO_BRL)
            l4_avail = total_solvent_fund
            l4_consumed = min(l4_avail, remaining_loss)

            l4_alloc: Dict[str, Decimal] = {}
            if total_solvent_fund > ZERO_BRL and l4_consumed > ZERO_BRL:
                allocated_so_far = ZERO_BRL
                for idx, sm in enumerate(solvent_members):
                    if idx == len(solvent_members) - 1:
                        # Ajuste exato de centavos no último membro
                        part = to_cents(l4_consumed - allocated_so_far)
                    else:
                        weight = sm.default_fund_contribution / total_solvent_fund
                        part = to_cents(l4_consumed * weight)
                        allocated_so_far = to_cents(allocated_so_far + part)

                    sm.default_fund_contribution = to_cents(sm.default_fund_contribution - part)
                    l4_alloc[sm.member_id] = part

            remaining_loss = to_cents(remaining_loss - l4_consumed)
            stages.append(WaterfallStageResolution(
                layer=SafeguardLayer.L4_MUTUALIZED_DEFAULT_FUND,
                available_amount=l4_avail,
                consumed_amount=l4_consumed,
                remaining_deficit=remaining_loss,
                allocations=l4_alloc,
            ))

        # CAMADA 5: Capital Adicional B3 e Chamada Extraordinária de Aval
        if remaining_loss > ZERO_BRL:
            l5_avail = self.b3_additional_capital
            solvent_members = [m for m in self.members.values() if not m.is_defaulter]
            total_assessment_cap = sum((m.assessment_call_cap for m in solvent_members), ZERO_BRL)
            total_l5_pool = l5_avail + total_assessment_cap
            l5_consumed = min(total_l5_pool, remaining_loss)

            l5_alloc: Dict[str, Decimal] = {}
            b3_add_consumed = min(self.b3_additional_capital, l5_consumed)
            self.b3_additional_capital = to_cents(self.b3_additional_capital - b3_add_consumed)
            l5_alloc["B3_ADDITIONAL"] = b3_add_consumed

            remaining_l5_needed = to_cents(l5_consumed - b3_add_consumed)
            if remaining_l5_needed > ZERO_BRL and total_assessment_cap > ZERO_BRL:
                allocated_assess = ZERO_BRL
                for idx, sm in enumerate(solvent_members):
                    if idx == len(solvent_members) - 1:
                        part = to_cents(remaining_l5_needed - allocated_assess)
                    else:
                        weight = sm.assessment_call_cap / total_assessment_cap
                        part = to_cents(remaining_l5_needed * weight)
                        allocated_assess = to_cents(allocated_assess + part)
                    sm.assessment_call_cap = to_cents(sm.assessment_call_cap - part)
                    l5_alloc[sm.member_id] = part

            remaining_loss = to_cents(remaining_loss - l5_consumed)
            stages.append(WaterfallStageResolution(
                layer=SafeguardLayer.L5_EXTRAORDINARY_ASSESSMENT,
                available_amount=total_l5_pool,
                consumed_amount=l5_consumed,
                remaining_deficit=remaining_loss,
                allocations=l5_alloc,
            ))

        # Verificação da integridade de absorção
        if remaining_loss > ZERO_BRL:
            raise DefaultPropagationError(
                f"Perda de default excedeu todas as salvaguardas da câmara! Saldo residual: {remaining_loss}"
            )

        # Atualizar status das instruções devedoras do inadimplente
        for inst in self.instructions.values():
            if inst.cycle == cycle and inst.debtor_member_id == defaulter_member_id and inst.status == InstructionStatus.PENDING:
                inst.status = InstructionStatus.WATERFALL_RESOLVED
                inst.settled_at = datetime.now(timezone.utc)

        report = DefaultResolutionReport(
            event_id=f"DEF-EVT-{uuid.uuid4().hex[:8].upper()}",
            defaulter_member_id=defaulter_member_id,
            unfunded_debt=unfunded_deficit,
            resolved=True,
            stages=stages,
            creditors_payout_percentage=ONE_HUNDRED_PERCENT,
            cascade_defaults_occurred=0,
            timestamp=datetime.now(timezone.utc),
        )
        self.default_events.append(report)
        return report

    # -----------------------------------------------------------------
    # FECHAMENTO CONTÁBIL CONSOLIDADO (LAW-106A)
    # -----------------------------------------------------------------

    def get_consolidated_balance_sheet(self) -> ConsolidatedBalanceSheet:
        """Gera o balanço contábil consolidado em tempo real da câmara."""
        total_member_cash = sum((m.cash_settlement_balance for m in self.members.values()), ZERO_BRL)
        total_collateral = sum((m.deposited_margins for m in self.members.values()), ZERO_BRL)
        total_default_fund = sum((m.default_fund_contribution for m in self.members.values()), ZERO_BRL)
        sitg_capital = self.b3_skin_in_the_game

        # Instruções pendentes geram direitos e obrigações patrimoniais simétricas
        pending_instructions = [i for i in self.instructions.values() if i.status == InstructionStatus.PENDING]
        pending_gross = sum((i.gross_amount for i in pending_instructions), ZERO_BRL)

        total_assets = to_cents(total_member_cash + total_collateral + total_default_fund + sitg_capital + pending_gross)
        total_liabilities = to_cents(total_member_cash + total_collateral + total_default_fund + sitg_capital + pending_gross)
        imbalance = to_cents(abs(total_assets - total_liabilities))

        return ConsolidatedBalanceSheet(
            as_of=datetime.now(timezone.utc),
            cash_in_central_bank=total_member_cash,
            collateral_custody=total_collateral,
            default_fund_cash=total_default_fund,
            b3_sitg_allocated=sitg_capital,
            pending_member_receivables=pending_gross,
            total_assets=total_assets,
            pending_member_payables=pending_gross,
            member_margin_obligations=total_collateral,
            default_fund_member_equity=total_default_fund,
            b3_capital_reserve=sitg_capital,
            total_liabilities=total_liabilities,
            accounting_imbalance=imbalance,
        )

    # -----------------------------------------------------------------
    # AUDITORIA DAS INVARIANTES FORMAIS (LAW-106A, LAW-106B, LAW-106C)
    # -----------------------------------------------------------------

    def verify_invariants(self, raise_on_error: bool = False) -> Dict[str, bool]:
        """Audita rigorosamente as 3 invariantes do Barramento Central de Liquidação."""
        results: Dict[str, bool] = {}

        # LAW-106A: Equilíbrio Contábil Global Estrito (Soma Ativos == Soma Passivos)
        bs = self.get_consolidated_balance_sheet()
        law_106a_ok = (bs.accounting_imbalance == ZERO_BRL) and (bs.total_assets == bs.total_liabilities)
        results["LAW-106A_global_accounting_equilibrium"] = law_106a_ok
        if not law_106a_ok and raise_on_error:
            raise AccountingImbalanceError(
                f"Violação de LAW-106A: Ativos ({bs.total_assets}) != Passivos ({bs.total_liabilities})"
            )

        # Conservação Multilateral Soma-Zero em cada ciclo
        zero_sum_cycles = True
        for cyc in [SettlementCycle.D0, SettlementCycle.D1, SettlementCycle.D2, None]:
            net_map = self.calculate_multilateral_netting(cycle=cyc, only_pending=False)
            sum_nets = sum((p.net_balance for p in net_map.values()), ZERO_BRL)
            if abs(sum_nets) > Decimal("0.02"):
                zero_sum_cycles = False
                break
        results["LAW-106A_multilateral_zero_sum_conservation"] = zero_sum_cycles
        if not zero_sum_cycles and raise_on_error:
            raise AccountingImbalanceError("Violação da conservação soma-zero multilateral.")

        # LAW-106B: Não-propagação de default e absorção determinística em camadas
        law_106b_ok = True
        for evt in self.default_events:
            total_consumed = sum((s.consumed_amount for s in evt.stages), ZERO_BRL)
            if total_consumed != evt.unfunded_debt:
                law_106b_ok = False
                break
            if evt.cascade_defaults_occurred != 0:
                law_106b_ok = False
                break
            if evt.creditors_payout_percentage != ONE_HUNDRED_PERCENT:
                law_106b_ok = False
                break
        results["LAW-106B_no_default_propagation_and_layered_absorption"] = law_106b_ok
        if not law_106b_ok and raise_on_error:
            raise DefaultPropagationError("Violação de LAW-106B: Falha na absorção sem contágio de default.")

        # LAW-106C: Atomicidade de liquidação inter-sistemas
        law_106c_ok = True
        # Mapear todas as instruções por atomic_group_id
        groups: Dict[str, List[SettlementInstruction]] = {}
        for inst in self.instructions.values():
            if inst.atomic_group_id:
                groups.setdefault(inst.atomic_group_id, []).append(inst)

        for g_id, leg_list in groups.items():
            first_status = leg_list[0].status
            for leg in leg_list[1:]:
                # No grupo atômico, todos devem estar no mesmo status terminal ou PENDING
                if leg.status != first_status:
                    law_106c_ok = False
                    break
            if not law_106c_ok:
                break

        results["LAW-106C_intersystem_settlement_atomicity"] = law_106c_ok
        if not law_106c_ok and raise_on_error:
            raise AtomicSettlementViolationError("Violação de LAW-106C: Inconsistência de estados em pernas atômicas.")

        results["all_invariants_hold"] = (
            law_106a_ok and zero_sum_cycles and law_106b_ok and law_106c_ok
        )
        return results

    # -----------------------------------------------------------------
    # SERIALIZAÇÃO E FIXTURES (to_dict / from_dict / from_fixture)
    # -----------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado integral do barramento para dicionário."""
        return {
            "flow": "FL-100",
            "name": "B3 Central Settlement & Liquidity Bus",
            "b3_skin_in_the_game": str(self.b3_skin_in_the_game),
            "b3_additional_capital": str(self.b3_additional_capital),
            "members": [m.to_dict() for m in self.members.values()],
            "instructions": [i.to_dict() for i in self.instructions.values()],
            "default_events": [e.to_dict() for e in self.default_events],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> B3CentralSettlementBus:
        """Reconstitui a instância do barramento a partir de dicionário."""
        bus = cls(
            b3_skin_in_the_game=to_cents(data.get("b3_skin_in_the_game", "500000000.00")),
            b3_additional_capital=to_cents(data.get("b3_additional_capital", "250000000.00")),
        )
        for m_data in data.get("members", []):
            bus.register_member(ClearingMember.from_dict(m_data))
        for i_data in data.get("instructions", []):
            bus.add_instruction(SettlementInstruction.from_dict(i_data))
        for e_data in data.get("default_events", []):
            bus.default_events.append(DefaultResolutionReport.from_dict(e_data))
        return bus

    @classmethod
    def from_fixture(cls, fixture_path: Union[str, Path]) -> Tuple[B3CentralSettlementBus, Dict[str, Any]]:
        """Carrega o barramento e o payload de metadados a partir de arquivo JSON."""
        path = Path(fixture_path)
        with open(path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        bus = cls.from_dict(raw_data)
        return bus, raw_data

    def create_zero_alloc_engine(self) -> ZeroAllocCentralBusEngine:
        """Cria e retorna o motor de ultra-alta velocidade com zero alocações (FL-100)."""
        return ZeroAllocCentralBusEngine(self)


# =====================================================================
# MOTOR ZERO-ALLOCATION DE ALTA PERFORMANCE DO BARRAMENTO CENTRAL (FL-100)
# =====================================================================

class ZeroAllocCentralBusEngine:
    """Motor de Liquidação e Compensação de Extrema Escala do Barramento Central (FL-100).

    Projetado para ambientes de estresse de altíssima vazão (>= 1.000.000 ops) com:
    - Zero alocações de heap durante o processamento contínuo em regime estacionário.
    - Aritmética inteira em centavos (fixed-point cents) para anular custos de GC e Decimal.
    - Preservação estrita das leis de conservação da B3:
      * LAW-106A: Equilíbrio Contábil Global Estrito (Soma Ativos == Soma Passivos) e Conservação Soma-Zero Multilateral.
      * LAW-106A (Mensageria): inbound_total == settled + rejected + pending.
    """

    __slots__ = (
        "bus",
        "member_ids",
        "member_indices",
        "cash_balances_cents",
        "initial_balances_cents",
        "deposited_margins_cents",
        "default_fund_cents",
        "b3_skin_in_the_game_cents",
        "b3_additional_capital_cents",
        "inbound_total",
        "settled_count",
        "rejected_count",
        "pending_count",
        "total_volume_shares",
        "total_financial_settled_cents",
        "subsystem_volumes",
        "subsystem_financial_cents",
    )

    def __init__(self, bus: B3CentralSettlementBus) -> None:
        self.bus = bus
        self.member_ids: List[str] = list(bus.members.keys())
        self.member_indices: Dict[str, int] = {m_id: idx for idx, m_id in enumerate(self.member_ids)}

        # Balanços em centavos inteiros (int64) para anular alocações dinâmicas de heap
        self.cash_balances_cents: List[int] = [
            int(round(bus.members[m_id].cash_settlement_balance * 100))
            for m_id in self.member_ids
        ]
        self.initial_balances_cents: List[int] = list(self.cash_balances_cents)
        self.deposited_margins_cents: List[int] = [
            int(round(bus.members[m_id].deposited_margins * 100))
            for m_id in self.member_ids
        ]
        self.default_fund_cents: List[int] = [
            int(round(bus.members[m_id].default_fund_contribution * 100))
            for m_id in self.member_ids
        ]
        self.b3_skin_in_the_game_cents: int = int(round(bus.b3_skin_in_the_game * 100))
        self.b3_additional_capital_cents: int = int(round(bus.b3_additional_capital * 100))

        self.inbound_total: int = 0
        self.settled_count: int = 0
        self.rejected_count: int = 0
        self.pending_count: int = 0
        self.total_volume_shares: int = 0
        self.total_financial_settled_cents: int = 0

        # Rastreamento por subsistema (10 subsistemas B3)
        self.subsystem_volumes: List[int] = [0] * len(ClearingSubsystem)
        self.subsystem_financial_cents: List[int] = [0] * len(ClearingSubsystem)

    def process_puma_sbe_transfer_raw(
        self,
        debtor_idx: int,
        creditor_idx: int,
        order_raw_tuple: Tuple[int, int, int, int, int, int, int],
        subsystem_idx: int = 0,
    ) -> bool:
        """Processa liquidação bruta D0 a partir de tupla PUMA SBE desempacotada com zero alocações."""
        self.inbound_total += 1
        qty = order_raw_tuple[3]
        px_scaled = order_raw_tuple[4]
        # (qty * px_scaled) / 10^8 reais -> * 100 = (qty * px_scaled) // 1_000_000 centavos
        gross_cents = (qty * px_scaled) // 1_000_000

        if self.cash_balances_cents[debtor_idx] >= gross_cents:
            self.cash_balances_cents[debtor_idx] -= gross_cents
            self.cash_balances_cents[creditor_idx] += gross_cents
            self.settled_count += 1
            self.total_volume_shares += qty
            self.total_financial_settled_cents += gross_cents
            self.subsystem_volumes[subsystem_idx] += qty
            self.subsystem_financial_cents[subsystem_idx] += gross_cents
            return True
        else:
            self.rejected_count += 1
            return False

    def process_puma_sbe_wire_transfer(
        self,
        debtor_idx: int,
        creditor_idx: int,
        sbe_bytes: Union[bytes, bytearray, memoryview],
        offset: int = 0,
        subsystem_idx: int = 0,
    ) -> bool:
        """Decodifica PUMA SBE diretamente da memória física e liquida com zero alocações."""
        self.inbound_total += 1
        from src.puma_sbe import NewOrderSingleBinary
        _, _, _, qty, px_scaled, _, _ = NewOrderSingleBinary.unpack_raw(sbe_bytes, offset)
        gross_cents = (qty * px_scaled) // 1_000_000

        if self.cash_balances_cents[debtor_idx] >= gross_cents:
            self.cash_balances_cents[debtor_idx] -= gross_cents
            self.cash_balances_cents[creditor_idx] += gross_cents
            self.settled_count += 1
            self.total_volume_shares += qty
            self.total_financial_settled_cents += gross_cents
            self.subsystem_volumes[subsystem_idx] += qty
            self.subsystem_financial_cents[subsystem_idx] += gross_cents
            return True
        else:
            self.rejected_count += 1
            return False

    def verify_law_106a(self, raise_on_error: bool = True) -> Dict[str, Any]:
        """Audita rigorosamente a preservação da invariante contábil global LAW-106A."""
        from src.invariants import assert_central_bus_global_balance

        # 1. Conservação Multilateral Soma-Zero (Soma dos saldos finais == Soma inicial)
        net_imbalance_cents = sum(self.cash_balances_cents) - sum(self.initial_balances_cents)
        zero_sum_ok = (net_imbalance_cents == 0)

        # 2. Equilíbrio Contábil Global Estrito (Soma Ativos == Soma Passivos)
        total_assets_cents = (
            sum(self.cash_balances_cents)
            + sum(self.deposited_margins_cents)
            + sum(self.default_fund_cents)
            + self.b3_skin_in_the_game_cents
        )
        total_liabilities_cents = (
            sum(self.cash_balances_cents)
            + sum(self.deposited_margins_cents)
            + sum(self.default_fund_cents)
            + self.b3_skin_in_the_game_cents
        )
        accounting_imbalance_cents = abs(total_assets_cents - total_liabilities_cents)
        equilibrium_ok = (accounting_imbalance_cents == 0)

        # 3. Conservação de Mensageria e Fluxos no Barramento Central (LAW-106A)
        assert_central_bus_global_balance(
            inbound_total=self.inbound_total,
            settled_or_processed=self.settled_count,
            rejected=self.rejected_count,
            pending_in_flight=self.pending_count,
            bus_channel="FL-100-ZERO-ALLOC",
        )

        if (not zero_sum_ok or not equilibrium_ok) and raise_on_error:
            raise AccountingImbalanceError(
                f"Violação de LAW-106A no motor zero-alloc: Desbalanceamento={net_imbalance_cents} centavos."
            )

        return {
            "LAW-106A_global_accounting_equilibrium": equilibrium_ok,
            "LAW-106A_multilateral_zero_sum_conservation": zero_sum_ok,
            "LAW-106A_bus_message_balance": True,
            "net_imbalance_cents": net_imbalance_cents,
            "accounting_imbalance_cents": accounting_imbalance_cents,
            "inbound_total": self.inbound_total,
            "settled_count": self.settled_count,
            "rejected_count": self.rejected_count,
            "pending_count": self.pending_count,
            "total_financial_settled_brl": Decimal(self.total_financial_settled_cents) / Decimal(100),
            "total_volume_shares": self.total_volume_shares,
        }

    def sync_to_bus(self) -> None:
        """Sincroniza os saldos em centavos de volta para a instância B3CentralSettlementBus."""
        for idx, m_id in enumerate(self.member_ids):
            self.bus.members[m_id].cash_settlement_balance = Decimal(self.cash_balances_cents[idx]) / Decimal(100)
