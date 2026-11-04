"""Motor de Salvaguardas e Cascata de Inadimplência da Câmara B3 (FL-055).

Implementa a hierarquia determinística de absorção de perdas por default de Membro
de Compensação (MC) conforme o Regulamento da Câmara B3 (Capítulo VII), o Manual
de Risco CORE e os Princípios CPMI-IOSCO para Infraestruturas do Mercado Financeiro
(PFMI Princípios 4 e 7: Risco de Crédito Cover 2 e Risco de Liquidez D0).

Estrutura dos 5 Níveis da Cascata (Default Waterfall):
1. LEVEL_1_DEFAULTER_MARGINS: Margens e Garantias Próprias do Inadimplente.
2. LEVEL_2_DEFAULTER_FUND_CONTRIBUTION: Contribuição do Inadimplente ao Fundo de Liquidação.
3. LEVEL_3_B3_SKIN_IN_THE_GAME: Capital Próprio da B3 dedicado ao Risco (SITG Pré-Mutualizado).
4. LEVEL_4_MUTUALIZED_DEFAULT_FUND: Fundo de Liquidação Mútuo dos Membros Adimplentes (Pro-Rata).
5. LEVEL_5_ADDITIONAL_B3_ASSESSMENT_CALLS: Capital Adicional B3 e Chamada Extraordinária de Aval.

Zero dependências externas (Python Standard Library pura).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


# =====================================================================
# ENUMS E CONSTANTES DA CÂMARA B3 / CPMI-IOSCO
# =====================================================================

class SafeguardLayerType(str, Enum):
    """Níveis de absorção de perdas da Cascata de Inadimplência (Default Waterfall)."""

    LEVEL_1_DEFAULTER_MARGINS = "LEVEL_1_DEFAULTER_MARGINS"
    """Nível 1: Margem de garantia inicial e colaterais próprios do membro inadimplente."""

    LEVEL_2_DEFAULTER_FUND_CONTRIBUTION = "LEVEL_2_DEFAULTER_FUND_CONTRIBUTION"
    """Nível 2: Cota ordinária depositada pelo membro inadimplente no Fundo de Liquidação."""

    LEVEL_3_B3_SKIN_IN_THE_GAME = "LEVEL_3_B3_SKIN_IN_THE_GAME"
    """Nível 3: Capital próprio da B3 dedicado ao risco (Skin-in-the-Game / SITG pré-mutualizado)."""

    LEVEL_4_MUTUALIZED_DEFAULT_FUND = "LEVEL_4_MUTUALIZED_DEFAULT_FUND"
    """Nível 4: Fundo de Liquidação Mútuo — Cotas dos Membros de Compensação Adimplentes."""

    LEVEL_5_ADDITIONAL_B3_ASSESSMENT_CALLS = "LEVEL_5_ADDITIONAL_B3_ASSESSMENT_CALLS"
    """Nível 5: Capital adicional da B3 e Chamadas Extraordinárias de Aval (Assessment Calls)."""


LAYER_NAMES_PTBR: Dict[SafeguardLayerType, str] = {
    SafeguardLayerType.LEVEL_1_DEFAULTER_MARGINS: "Margens Próprias do Inadimplente",
    SafeguardLayerType.LEVEL_2_DEFAULTER_FUND_CONTRIBUTION: "Cota do Inadimplente no Fundo",
    SafeguardLayerType.LEVEL_3_B3_SKIN_IN_THE_GAME: "Skin-in-the-Game B3 (SITG)",
    SafeguardLayerType.LEVEL_4_MUTUALIZED_DEFAULT_FUND: "Fundo Mútuo de Membros Adimplentes",
    SafeguardLayerType.LEVEL_5_ADDITIONAL_B3_ASSESSMENT_CALLS: "SITG Adicional B3 e Chamada Extraordinária de Aval",
}


# =====================================================================
# EXCEÇÕES DO MÓDULO
# =====================================================================

class DefaultWaterfallError(Exception):
    """Exceção base para operações de salvaguardas e cascata de default."""
    pass


class ClearingMemberNotFoundError(DefaultWaterfallError):
    """Membro de Compensação especificado no default não está cadastrado."""
    pass


class InvalidLossAmountError(DefaultWaterfallError):
    """Valor de perda bruta de default inválido ou não positivo."""
    pass


class WaterfallInvariantViolationError(DefaultWaterfallError):
    """Violação formal da invariante matemática de conservação de recursos."""
    pass


# =====================================================================
# FUNÇÕES UTILITÁRIAS
# =====================================================================

def parse_iso_datetime(value: Union[str, datetime]) -> datetime:
    """Converte string ISO 8601 (incluindo sufixo Z) em datetime ciente de fuso (UTC)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    norm = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(norm)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_decimal_currency(val: Union[str, int, float, Decimal]) -> Decimal:
    """Padroniza valores monetários para Decimal com precisão de 2 casas decimais."""
    return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# =====================================================================
# DATA CLASSES DE ENTRADA E MODELO
# =====================================================================

@dataclass
class ClearingMemberSafeguards:
    """Cadastro de salvaguardas financeiras e limites de um Membro de Compensação (MC)."""

    member_id: str
    member_name: str
    deposited_margins: Decimal
    default_fund_contribution: Decimal
    assessment_call_cap: Decimal
    risk_weight: Decimal = Decimal("0.00")
    is_defaulter: bool = False

    def __post_init__(self) -> None:
        self.deposited_margins = to_decimal_currency(self.deposited_margins)
        self.default_fund_contribution = to_decimal_currency(self.default_fund_contribution)
        self.assessment_call_cap = to_decimal_currency(self.assessment_call_cap)
        self.risk_weight = Decimal(str(self.risk_weight))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "member_id": self.member_id,
            "member_name": self.member_name,
            "deposited_margins": str(self.deposited_margins),
            "default_fund_contribution": str(self.default_fund_contribution),
            "assessment_call_cap": str(self.assessment_call_cap),
            "risk_weight": str(self.risk_weight),
            "is_defaulter": self.is_defaulter,
        }


@dataclass
class DefaultLossEvent:
    """Evento formal de inadimplência financeira de um Membro de Compensação."""

    event_id: str
    defaulter_member_id: str
    gross_loss_amount: Decimal
    timestamp: datetime
    stress_scenario_name: str
    description: str

    def __post_init__(self) -> None:
        self.gross_loss_amount = to_decimal_currency(self.gross_loss_amount)
        if isinstance(self.timestamp, str):
            self.timestamp = parse_iso_datetime(self.timestamp)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "defaulter_member_id": self.defaulter_member_id,
            "gross_loss_amount": str(self.gross_loss_amount),
            "timestamp": self.timestamp.isoformat(),
            "stress_scenario_name": self.stress_scenario_name,
            "description": self.description,
        }


@dataclass
class WaterfallExecutionStage:
    """Registro determinístico de execução de um nível da cascata de salvaguardas."""

    layer_type: SafeguardLayerType
    layer_name: str
    available_amount: Decimal
    consumed_amount: Decimal
    surplus_amount: Decimal
    loss_before_stage: Decimal
    loss_after_stage: Decimal
    is_exhausted: bool
    member_allocations: Dict[str, Decimal] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer_type": self.layer_type.value,
            "layer_name": self.layer_name,
            "available_amount": str(self.available_amount),
            "consumed_amount": str(self.consumed_amount),
            "surplus_amount": str(self.surplus_amount),
            "loss_before_stage": str(self.loss_before_stage),
            "loss_after_stage": str(self.loss_after_stage),
            "is_exhausted": self.is_exhausted,
            "member_allocations": {k: str(v) for k, v in self.member_allocations.items()},
        }


@dataclass
class DefaultWaterfallResult:
    """Resultado auditável consolidado da execução da cascata de inadimplência."""

    event: DefaultLossEvent
    stages: List[WaterfallExecutionStage]
    total_loss: Decimal
    total_absorbed: Decimal
    residual_deficit: Decimal
    is_fully_absorbed: bool
    settlement_continuity_maintained: bool
    max_layer_reached: Optional[SafeguardLayerType]
    absorption_by_layer: Dict[str, Decimal]
    non_defaulting_mutualized_impact: Dict[str, Decimal]
    non_defaulting_assessment_impact: Dict[str, Decimal]
    b3_own_capital_consumed: Decimal
    invariant_verified: bool
    execution_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event.to_dict(),
            "stages": [s.to_dict() for s in self.stages],
            "total_loss": str(self.total_loss),
            "total_absorbed": str(self.total_absorbed),
            "residual_deficit": str(self.residual_deficit),
            "is_fully_absorbed": self.is_fully_absorbed,
            "settlement_continuity_maintained": self.settlement_continuity_maintained,
            "max_layer_reached": self.max_layer_reached.value if self.max_layer_reached else None,
            "absorption_by_layer": {k: str(v) for k, v in self.absorption_by_layer.items()},
            "non_defaulting_mutualized_impact": {
                k: str(v) for k, v in self.non_defaulting_mutualized_impact.items()
            },
            "non_defaulting_assessment_impact": {
                k: str(v) for k, v in self.non_defaulting_assessment_impact.items()
            },
            "b3_own_capital_consumed": str(self.b3_own_capital_consumed),
            "invariant_verified": self.invariant_verified,
            "execution_timestamp": self.execution_timestamp.isoformat(),
        }


# =====================================================================
# MOTOR DETERMINÍSTICO DA CASCATA DE INADIMPLÊNCIA (CORE / B3)
# =====================================================================

class DefaultWaterfallEngine:
    """Motor de cálculo e liquidação da Cascata de Inadimplência da Câmara B3.
    
    Garante deterministicamente a absorção progressiva de perdas, o rateio pro-rata
    estrito sem quebra de centavos e a verificação matemática formal da invariante
    de conservação de recursos CPMI-IOSCO.
    """

    def __init__(
        self,
        b3_skin_in_the_game: Union[str, int, float, Decimal] = Decimal("500000000.00"),
        b3_additional_capital: Union[str, int, float, Decimal] = Decimal("250000000.00"),
    ) -> None:
        self.b3_skin_in_the_game: Decimal = to_decimal_currency(b3_skin_in_the_game)
        self.b3_additional_capital: Decimal = to_decimal_currency(b3_additional_capital)
        self.members: Dict[str, ClearingMemberSafeguards] = {}

    def register_member(self, member: ClearingMemberSafeguards) -> None:
        """Cadastra ou atualiza um Membro de Compensação na câmara."""
        self.members[member.member_id] = member

    def get_member(self, member_id: str) -> Optional[ClearingMemberSafeguards]:
        """Obtém as salvaguardas registradas de um Membro de Compensação."""
        return self.members.get(member_id)

    @classmethod
    def load_from_json(
        cls, json_path: Union[str, Path]
    ) -> Tuple[DefaultWaterfallEngine, List[DefaultLossEvent]]:
        """Inicializa o motor e carrega os eventos de perda a partir de arquivo JSON."""
        path = Path(json_path)
        if not path.is_file():
            raise FileNotFoundError(f"Arquivo de cenários de waterfall não encontrado: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        b3_cfg = data.get("b3_safeguards", {})
        engine = cls(
            b3_skin_in_the_game=b3_cfg.get("skin_in_the_game_brl", "500000000.00"),
            b3_additional_capital=b3_cfg.get("additional_b3_capital_brl", "250000000.00"),
        )

        for m_data in data.get("clearing_members", []):
            member = ClearingMemberSafeguards(
                member_id=m_data["member_id"],
                member_name=m_data["member_name"],
                deposited_margins=Decimal(m_data["deposited_margins"]),
                default_fund_contribution=Decimal(m_data["default_fund_contribution"]),
                assessment_call_cap=Decimal(m_data["assessment_call_cap"]),
                risk_weight=Decimal(m_data.get("risk_weight", "0.0")),
                is_defaulter=False,
            )
            engine.register_member(member)

        events: List[DefaultLossEvent] = []
        for s_data in data.get("scenarios", []):
            event = DefaultLossEvent(
                event_id=s_data["event_id"],
                defaulter_member_id=s_data["defaulter_member_id"],
                gross_loss_amount=Decimal(s_data["gross_loss_amount"]),
                timestamp=parse_iso_datetime(s_data["timestamp"]),
                stress_scenario_name=s_data["stress_scenario_name"],
                description=s_data["description"],
            )
            events.append(event)

        return engine, events

    @classmethod
    def from_fixture(
        cls, fixture_path: Union[str, Path]
    ) -> Tuple[DefaultWaterfallEngine, List[Dict[str, Any]]]:
        """Carrega a engine e a lista bruta de cenários para testes automatizados."""
        path = Path(fixture_path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        b3_cfg = data.get("b3_safeguards", {})
        engine = cls(
            b3_skin_in_the_game=b3_cfg.get("skin_in_the_game_brl", "500000000.00"),
            b3_additional_capital=b3_cfg.get("additional_b3_capital_brl", "250000000.00"),
        )

        for m_data in data.get("clearing_members", []):
            member = ClearingMemberSafeguards(
                member_id=m_data["member_id"],
                member_name=m_data["member_name"],
                deposited_margins=Decimal(m_data["deposited_margins"]),
                default_fund_contribution=Decimal(m_data["default_fund_contribution"]),
                assessment_call_cap=Decimal(m_data["assessment_call_cap"]),
                risk_weight=Decimal(m_data.get("risk_weight", "0.0")),
            )
            engine.register_member(member)

        return engine, data.get("scenarios", [])

    def calculate_pro_rata_mutualized_losses(
        self,
        non_defaulting_members: Sequence[ClearingMemberSafeguards],
        loss_to_absorb: Decimal,
    ) -> Dict[str, Decimal]:
        """Calcula o rateio pro-rata no Fundo Mútuo (Nível 4) com conservação exata de centavos."""
        loss = to_decimal_currency(loss_to_absorb)
        if loss <= Decimal("0.00") or not non_defaulting_members:
            return {m.member_id: Decimal("0.00") for m in non_defaulting_members}

        total_contributions = sum(m.default_fund_contribution for m in non_defaulting_members)
        if total_contributions <= Decimal("0.00"):
            return {m.member_id: Decimal("0.00") for m in non_defaulting_members}

        # Se a perda for igual ou maior que a soma das cotas, cada membro perde 100% de sua cota
        if loss >= total_contributions:
            return {m.member_id: m.default_fund_contribution for m in non_defaulting_members}

        # Rateio proporcional ponderado pela cota ordinária
        allocations: Dict[str, Decimal] = {}
        allocated_sum = Decimal("0.00")

        # Ordena membros por maior cota e por ID para ordenação determinística
        sorted_members = sorted(
            non_defaulting_members,
            key=lambda m: (m.default_fund_contribution, m.member_id),
            reverse=True,
        )

        for m in sorted_members:
            raw_share = (m.default_fund_contribution / total_contributions) * loss
            share = raw_share.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            # Garantir que não exceda a cota
            share = min(share, m.default_fund_contribution)
            allocations[m.member_id] = share
            allocated_sum += share

        # Ajuste determinístico de resíduo de centavos
        cent_diff = loss - allocated_sum
        if cent_diff != Decimal("0.00"):
            # Aloca a diferença de centavos ao maior membro sem ultrapassar a cota
            for m in sorted_members:
                current = allocations[m.member_id]
                candidate = current + cent_diff
                if Decimal("0.00") <= candidate <= m.default_fund_contribution:
                    allocations[m.member_id] = candidate
                    allocated_sum += cent_diff
                    break

        return allocations

    def calculate_pro_rata_assessment_calls(
        self,
        non_defaulting_members: Sequence[ClearingMemberSafeguards],
        loss_to_absorb: Decimal,
    ) -> Dict[str, Decimal]:
        """Calcula o rateio pro-rata de Chamada Extraordinária de Aval (Nível 5) limitada ao teto."""
        loss = to_decimal_currency(loss_to_absorb)
        if loss <= Decimal("0.00") or not non_defaulting_members:
            return {m.member_id: Decimal("0.00") for m in non_defaulting_members}

        total_caps = sum(m.assessment_call_cap for m in non_defaulting_members)
        if total_caps <= Decimal("0.00"):
            return {m.member_id: Decimal("0.00") for m in non_defaulting_members}

        if loss >= total_caps:
            return {m.member_id: m.assessment_call_cap for m in non_defaulting_members}

        allocations: Dict[str, Decimal] = {}
        allocated_sum = Decimal("0.00")

        sorted_members = sorted(
            non_defaulting_members,
            key=lambda m: (m.assessment_call_cap, m.member_id),
            reverse=True,
        )

        for m in sorted_members:
            raw_share = (m.assessment_call_cap / total_caps) * loss
            share = raw_share.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            share = min(share, m.assessment_call_cap)
            allocations[m.member_id] = share
            allocated_sum += share

        cent_diff = loss - allocated_sum
        if cent_diff != Decimal("0.00"):
            for m in sorted_members:
                current = allocations[m.member_id]
                candidate = current + cent_diff
                if Decimal("0.00") <= candidate <= m.assessment_call_cap:
                    allocations[m.member_id] = candidate
                    allocated_sum += cent_diff
                    break

        return allocations

    def process_default(self, event: DefaultLossEvent) -> DefaultWaterfallResult:
        """Processa a cascata de default nível a nível de modo estritamente determinístico.
        
        Nível 1 -> Nível 2 -> Nível 3 -> Nível 4 -> Nível 5
        """
        defaulter = self.get_member(event.defaulter_member_id)
        if not defaulter:
            raise ClearingMemberNotFoundError(
                f"Membro de compensação inadimplente '{event.defaulter_member_id}' não encontrado."
            )

        gross_loss = to_decimal_currency(event.gross_loss_amount)
        if gross_loss <= Decimal("0.00"):
            raise InvalidLossAmountError(
                f"Valor de perda bruta de default deve ser estritamente positivo: {gross_loss}"
            )

        remaining_loss = gross_loss
        stages: List[WaterfallExecutionStage] = []
        absorption_by_layer: Dict[str, Decimal] = {}
        max_layer_reached: Optional[SafeguardLayerType] = None

        # Identifica membros adimplentes
        non_defaulting_members = [
            m for m in self.members.values() if m.member_id != defaulter.member_id
        ]

        # -------------------------------------------------------------
        # NÍVEL 1: MARGENS E GARANTIAS PRÓPRIAS DO INADIMPLENTE
        # -------------------------------------------------------------
        l1_type = SafeguardLayerType.LEVEL_1_DEFAULTER_MARGINS
        l1_available = defaulter.deposited_margins
        l1_loss_before = remaining_loss
        l1_consumed = min(l1_available, remaining_loss)
        remaining_loss -= l1_consumed
        l1_surplus = l1_available - l1_consumed
        l1_exhausted = (l1_surplus == Decimal("0.00") and l1_available > Decimal("0.00"))
        l1_allocations = {defaulter.member_id: l1_consumed} if l1_consumed > Decimal("0.00") else {}

        if l1_consumed > Decimal("0.00"):
            max_layer_reached = l1_type

        stages.append(
            WaterfallExecutionStage(
                layer_type=l1_type,
                layer_name=LAYER_NAMES_PTBR[l1_type],
                available_amount=l1_available,
                consumed_amount=l1_consumed,
                surplus_amount=l1_surplus,
                loss_before_stage=l1_loss_before,
                loss_after_stage=remaining_loss,
                is_exhausted=l1_exhausted,
                member_allocations=l1_allocations,
            )
        )
        absorption_by_layer[l1_type.value] = l1_consumed

        # -------------------------------------------------------------
        # NÍVEL 2: CONTRIBUIÇÃO DO INADIMPLENTE AO FUNDO DE LIQUIDAÇÃO
        # -------------------------------------------------------------
        l2_type = SafeguardLayerType.LEVEL_2_DEFAULTER_FUND_CONTRIBUTION
        l2_available = defaulter.default_fund_contribution
        l2_loss_before = remaining_loss
        l2_consumed = min(l2_available, remaining_loss) if remaining_loss > Decimal("0.00") else Decimal("0.00")
        remaining_loss -= l2_consumed
        l2_surplus = l2_available - l2_consumed
        l2_exhausted = (l2_surplus == Decimal("0.00") and l2_available > Decimal("0.00"))
        l2_allocations = {defaulter.member_id: l2_consumed} if l2_consumed > Decimal("0.00") else {}

        if l2_consumed > Decimal("0.00"):
            max_layer_reached = l2_type

        stages.append(
            WaterfallExecutionStage(
                layer_type=l2_type,
                layer_name=LAYER_NAMES_PTBR[l2_type],
                available_amount=l2_available,
                consumed_amount=l2_consumed,
                surplus_amount=l2_surplus,
                loss_before_stage=l2_loss_before,
                loss_after_stage=remaining_loss,
                is_exhausted=l2_exhausted,
                member_allocations=l2_allocations,
            )
        )
        absorption_by_layer[l2_type.value] = l2_consumed

        # -------------------------------------------------------------
        # NÍVEL 3: CAPITAL PRÓPRIO DA B3 (SKIN-IN-THE-GAME / SITG)
        # -------------------------------------------------------------
        l3_type = SafeguardLayerType.LEVEL_3_B3_SKIN_IN_THE_GAME
        l3_available = self.b3_skin_in_the_game
        l3_loss_before = remaining_loss
        l3_consumed = min(l3_available, remaining_loss) if remaining_loss > Decimal("0.00") else Decimal("0.00")
        remaining_loss -= l3_consumed
        l3_surplus = l3_available - l3_consumed
        l3_exhausted = (l3_surplus == Decimal("0.00") and l3_available > Decimal("0.00"))
        l3_allocations = {"B3_SITG_ORDINARY": l3_consumed} if l3_consumed > Decimal("0.00") else {}

        if l3_consumed > Decimal("0.00"):
            max_layer_reached = l3_type

        stages.append(
            WaterfallExecutionStage(
                layer_type=l3_type,
                layer_name=LAYER_NAMES_PTBR[l3_type],
                available_amount=l3_available,
                consumed_amount=l3_consumed,
                surplus_amount=l3_surplus,
                loss_before_stage=l3_loss_before,
                loss_after_stage=remaining_loss,
                is_exhausted=l3_exhausted,
                member_allocations=l3_allocations,
            )
        )
        absorption_by_layer[l3_type.value] = l3_consumed

        # -------------------------------------------------------------
        # NÍVEL 4: FUNDO DE LIQUIDAÇÃO MÚTUO (MEMBROS ADIMPLENTES)
        # -------------------------------------------------------------
        l4_type = SafeguardLayerType.LEVEL_4_MUTUALIZED_DEFAULT_FUND
        l4_available = sum(m.default_fund_contribution for m in non_defaulting_members)
        l4_loss_before = remaining_loss
        l4_consumed = min(l4_available, remaining_loss) if remaining_loss > Decimal("0.00") else Decimal("0.00")
        remaining_loss -= l4_consumed
        l4_surplus = l4_available - l4_consumed
        l4_exhausted = (l4_surplus == Decimal("0.00") and l4_available > Decimal("0.00"))

        l4_allocations = self.calculate_pro_rata_mutualized_losses(
            non_defaulting_members, l4_consumed
        )

        if l4_consumed > Decimal("0.00"):
            max_layer_reached = l4_type

        stages.append(
            WaterfallExecutionStage(
                layer_type=l4_type,
                layer_name=LAYER_NAMES_PTBR[l4_type],
                available_amount=l4_available,
                consumed_amount=l4_consumed,
                surplus_amount=l4_surplus,
                loss_before_stage=l4_loss_before,
                loss_after_stage=remaining_loss,
                is_exhausted=l4_exhausted,
                member_allocations=l4_allocations,
            )
        )
        absorption_by_layer[l4_type.value] = l4_consumed

        # -------------------------------------------------------------
        # NÍVEL 5: CAPITAL ADICIONAL B3 E CHAMADA EXTRAORDINÁRIA DE AVAL
        # -------------------------------------------------------------
        l5_type = SafeguardLayerType.LEVEL_5_ADDITIONAL_B3_ASSESSMENT_CALLS
        l5_assessment_cap_total = sum(m.assessment_call_cap for m in non_defaulting_members)
        l5_available = self.b3_additional_capital + l5_assessment_cap_total
        l5_loss_before = remaining_loss
        l5_consumed = min(l5_available, remaining_loss) if remaining_loss > Decimal("0.00") else Decimal("0.00")
        remaining_loss -= l5_consumed
        l5_surplus = l5_available - l5_consumed
        l5_exhausted = (l5_surplus == Decimal("0.00") and l5_available > Decimal("0.00"))

        # Absorção no N5: Primeiro Capital Adicional B3, depois Assessment Calls pro-rata
        b3_add_consumed = min(self.b3_additional_capital, l5_consumed)
        assessment_consumed = l5_consumed - b3_add_consumed

        l5_allocations = self.calculate_pro_rata_assessment_calls(
            non_defaulting_members, assessment_consumed
        )
        if b3_add_consumed > Decimal("0.00"):
            l5_allocations["B3_ADDITIONAL_SITG"] = b3_add_consumed

        if l5_consumed > Decimal("0.00"):
            max_layer_reached = l5_type

        stages.append(
            WaterfallExecutionStage(
                layer_type=l5_type,
                layer_name=LAYER_NAMES_PTBR[l5_type],
                available_amount=l5_available,
                consumed_amount=l5_consumed,
                surplus_amount=l5_surplus,
                loss_before_stage=l5_loss_before,
                loss_after_stage=remaining_loss,
                is_exhausted=l5_exhausted,
                member_allocations=l5_allocations,
            )
        )
        absorption_by_layer[l5_type.value] = l5_consumed

        # -------------------------------------------------------------
        # CONSOLIDAÇÃO E VERIFICAÇÃO FORMAL DA INVARIANTE
        # -------------------------------------------------------------
        total_absorbed = sum(s.consumed_amount for s in stages)
        residual_deficit = remaining_loss
        is_fully_absorbed = (residual_deficit == Decimal("0.00"))
        settlement_continuity = is_fully_absorbed

        # Capital próprio da B3 consumido (N3 SITG + N5 Additional SITG)
        b3_own_consumed = l3_consumed + b3_add_consumed

        # Separação limpa dos impactos de membros adimplentes
        non_defaulting_mutualized = {
            m_id: amt for m_id, amt in l4_allocations.items() if amt > Decimal("0.00")
        }
        non_defaulting_assessment = {
            m_id: amt
            for m_id, amt in l5_allocations.items()
            if m_id != "B3_ADDITIONAL_SITG" and amt > Decimal("0.00")
        }

        # Verificação da Invariante: Perda_Total == Total_Absorvido + Deficit_Residual
        invariant_verified = (gross_loss == (total_absorbed + residual_deficit))
        if not invariant_verified:
            raise WaterfallInvariantViolationError(
                f"Violação da invariante da cascata: Perda={gross_loss} != "
                f"Absorvido({total_absorbed}) + Déficit({residual_deficit})"
            )

        return DefaultWaterfallResult(
            event=event,
            stages=stages,
            total_loss=gross_loss,
            total_absorbed=total_absorbed,
            residual_deficit=residual_deficit,
            is_fully_absorbed=is_fully_absorbed,
            settlement_continuity_maintained=settlement_continuity,
            max_layer_reached=max_layer_reached,
            absorption_by_layer=absorption_by_layer,
            non_defaulting_mutualized_impact=non_defaulting_mutualized,
            non_defaulting_assessment_impact=non_defaulting_assessment,
            b3_own_capital_consumed=b3_own_consumed,
            invariant_verified=invariant_verified,
        )

    def verify_waterfall_invariants(self, result: DefaultWaterfallResult) -> bool:
        """Executa validação formal de 5 propriedades matemáticas da cascata."""
        # 1. Lei da Conservação Global: Perda == Absorvido + Déficit
        if result.total_loss != (result.total_absorbed + result.residual_deficit):
            return False

        # 2. Soma das Camadas == Total Absorvido
        sum_stages = sum(s.consumed_amount for s in result.stages)
        if sum_stages != result.total_absorbed:
            return False

        # 3. Equilíbrio de Balanço em cada Camada
        for s in result.stages:
            if s.available_amount != (s.consumed_amount + s.surplus_amount):
                return False
            if s.loss_before_stage != (s.consumed_amount + s.loss_after_stage):
                return False
            if s.consumed_amount < Decimal("0.00") or s.surplus_amount < Decimal("0.00"):
                return False

        # 4. Conservação do Rateio Mútuo no Nível 4
        l4_stage = next(s for s in result.stages if s.layer_type == SafeguardLayerType.LEVEL_4_MUTUALIZED_DEFAULT_FUND)
        if sum(l4_stage.member_allocations.values()) != l4_stage.consumed_amount:
            return False

        # 5. Conservação no Nível 5 (B3 Adicional + Assessment Calls)
        l5_stage = next(s for s in result.stages if s.layer_type == SafeguardLayerType.LEVEL_5_ADDITIONAL_B3_ASSESSMENT_CALLS)
        if sum(l5_stage.member_allocations.values()) != l5_stage.consumed_amount:
            return False

        return True
