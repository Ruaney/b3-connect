"""Central de Garantias, Gravames e Ônus Reais na Registradora B3 (FL-037).

Implementa o motor determinístico de Constituição, Averbação, Bloqueio de Negociação,
Excussão em Cascata (Waterfall) e Baixa de Gravames e Ônus Reais:
- Modalidades: Penhor, Alienação Fiduciária, Cessão Fiduciária e Usufruto.
- Conformidade regulatória: Lei nº 14.711/2023 (Marco Legal das Garantias) e Resolução BCB nº 264/2022.
- Múltiplos graus de prioridade (1º Grau / Sênior, 2º Grau / Subordinado, 3º Grau / Júnior)
  sobre o mesmo ativo garantidor até o limite do valor líquido de avaliação.
- Cascata de excussão por ordem estrita de prelazia com conservação e devolução do saldo
  remanescente ao devedor garantidor.

Zero dependências externas (100% biblioteca padrão Python).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")


def _quantize_money(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda valores monetários em BRL para 2 casas decimais (ROUND_HALF_UP)."""
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _quantize_qty(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda quantidades de ativos para 4 casas decimais (ROUND_HALF_UP)."""
    return Decimal(str(value)).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def _parse_iso_timestamp(value: Union[str, datetime]) -> datetime:
    """Converte string ISO-8601 (com ou sem sufixo Z) para objeto datetime UTC."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------


class LienRegistryError(Exception):
    """Exceção base da Central de Garantias e Gravames B3 (FL-037)."""


class CollateralPoolNotFoundError(LienRegistryError):
    """Lançada quando o pool de ativos garantidores não está registrado."""


class DuplicatePoolError(LienRegistryError):
    """Lançada ao tentar registrar um pool com identificador duplicado."""


class DuplicateLienError(LienRegistryError):
    """Lançada ao tentar averbar um gravame com protocolo duplicado."""


class LienNotFoundError(LienRegistryError):
    """Lançada quando o gravame informado não existe no registro."""


class InsufficientCollateralCapacityError(LienRegistryError):
    """Lançada quando o valor garantido excede a capacidade líquida de avaliação disponível."""


class FreeTradingBlockedError(LienRegistryError):
    """Lançada quando há tentativa de negociação ou transferência livre de ativo gravado."""


class InvalidLienOperationError(LienRegistryError):
    """Lançada em transições de estado inválidas (ex.: excutir pool sem gravames ativos)."""


# ---------------------------------------------------------------------------
# Enumerações Tipadas
# ---------------------------------------------------------------------------


class LienModality(str, Enum):
    """Modalidades jurídicas de gravames e ônus reais na Registradora e Depositária B3."""

    PENHOR = "PENHOR"
    ALIENACAO_FIDUCIARIA = "ALIENACAO_FIDUCIARIA"
    CESSAO_FIDUCIARIA = "CESSAO_FIDUCIARIA"
    USUFRUTO = "USUFRUTO"

    @property
    def transfers_fiduciary_ownership(self) -> bool:
        """Indica se a modalidade transfere propriedade resolúvel/titularidade fiduciária."""
        return self in (
            LienModality.ALIENACAO_FIDUCIARIA,
            LienModality.CESSAO_FIDUCIARIA,
        )


class LienPriorityRank(str, Enum):
    """Graus de prelazia (prioridade) conforme o Marco Legal das Garantias (Lei 14.711/2023)."""

    PRIMEIRO_GRAU = "PRIMEIRO_GRAU"
    SEGUNDO_GRAU = "SEGUNDO_GRAU"
    TERCEIRO_GRAU = "TERCEIRO_GRAU"

    @property
    def order(self) -> int:
        """Precedência numérica estrita (menor número = maior prioridade na cascata)."""
        mapping = {
            LienPriorityRank.PRIMEIRO_GRAU: 1,
            LienPriorityRank.SEGUNDO_GRAU: 2,
            LienPriorityRank.TERCEIRO_GRAU: 3,
        }
        return mapping[self]

    @property
    def is_senior(self) -> bool:
        """Indica se o gravame é de 1º Grau (Sênior)."""
        return self == LienPriorityRank.PRIMEIRO_GRAU


class LienStatus(str, Enum):
    """Ciclo de vida do gravame na Registradora e Depositária Central da B3."""

    PENDENTE_ANUENCIA = "PENDENTE_ANUENCIA"
    ATIVO_BLOQUEADO = "ATIVO_BLOQUEADO"
    EM_EXCUSSAO = "EM_EXCUSSAO"
    EXCUTIDO_LIQUIDADO = "EXCUTIDO_LIQUIDADO"
    BAIXADO_LIBERADO = "BAIXADO_LIBERADO"
    REJEITADO = "REJEITADO"

    @property
    def encumbers_collateral(self) -> bool:
        """Indica se o status atual consome capacidade de garantia e bloqueia negociação livre."""
        return self in (LienStatus.ATIVO_BLOQUEADO, LienStatus.EM_EXCUSSAO)


# ---------------------------------------------------------------------------
# Estruturas de Dados (Dataclasses)
# ---------------------------------------------------------------------------


@dataclass
class CollateralAssetPool:
    """Lote de ativos financeiros ou valores mobiliários depositados como lastro de garantia."""

    pool_id: str
    asset_type: str
    ticker_or_cetip_code: str
    isin: str
    grantor_document: str
    grantor_name: str
    custodian_participant_code: str
    total_quantity: Decimal
    unit_market_price: Decimal
    haircut_pct: Decimal = field(default=Decimal("0.0000"))
    currency: str = "BRL"
    custody_account_free: str = "2101-6"
    custody_account_encumbered: str = "2901-8"

    def __post_init__(self) -> None:
        self.total_quantity = _quantize_qty(self.total_quantity)
        self.unit_market_price = _quantize_money(self.unit_market_price)
        self.haircut_pct = _quantize_qty(self.haircut_pct)

        if not self.pool_id or not self.pool_id.strip():
            raise InvalidLienOperationError("pool_id não pode ser vazio.")
        if len(self.isin.strip()) != 12:
            raise InvalidLienOperationError(
                f"ISIN inválido '{self.isin}': deve conter exatamente 12 caracteres."
            )
        if self.total_quantity <= Decimal("0"):
            raise InvalidLienOperationError(
                f"Quantidade total do pool '{self.pool_id}' deve ser positiva."
            )
        if self.unit_market_price <= Decimal("0"):
            raise InvalidLienOperationError(
                f"Preço unitário de mercado do pool '{self.pool_id}' deve ser positivo."
            )
        if self.haircut_pct < Decimal("0") or self.haircut_pct >= Decimal("1.0000"):
            raise InvalidLienOperationError(
                f"Haircut do pool '{self.pool_id}' deve estar em [0.0000, 1.0000): {self.haircut_pct}."
            )

    @property
    def gross_market_value_brl(self) -> Decimal:
        """Valor bruto de mercado do ativo garantidor em BRL."""
        return _quantize_money(self.total_quantity * self.unit_market_price)

    @property
    def haircut_deduction_brl(self) -> Decimal:
        """Deságio em BRL calculado a partir do haircut regulatório/contratual."""
        return _quantize_money(self.gross_market_value_brl * self.haircut_pct)

    @property
    def net_valuation_amount_brl(self) -> Decimal:
        """Valor líquido de avaliação (teto máximo para soma dos graus de garantia)."""
        return _quantize_money(self.gross_market_value_brl - self.haircut_deduction_brl)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CollateralAssetPool:
        return cls(
            pool_id=str(data["pool_id"]),
            asset_type=str(data["asset_type"]),
            ticker_or_cetip_code=str(data["ticker_or_cetip_code"]),
            isin=str(data["isin"]),
            grantor_document=str(data["grantor_document"]),
            grantor_name=str(data["grantor_name"]),
            custodian_participant_code=str(data["custodian_participant_code"]),
            total_quantity=Decimal(str(data["total_quantity"])),
            unit_market_price=Decimal(str(data["unit_market_price"])),
            haircut_pct=Decimal(str(data.get("haircut_pct", "0.0000"))),
            currency=str(data.get("currency", "BRL")),
            custody_account_free=str(data.get("custody_account_free", "2101-6")),
            custody_account_encumbered=str(data.get("custody_account_encumbered", "2901-8")),
        )


@dataclass
class LienRegistration:
    """Registro individual de Gravame ou Ônus Real averbado sobre um CollateralAssetPool."""

    lien_id: str
    pool_id: str
    modality: LienModality
    priority_rank: LienPriorityRank
    status: LienStatus
    creditor_document: str
    creditor_name: str
    contract_reference: str
    secured_amount_brl: Decimal
    earmarked_quantity: Decimal
    registration_timestamp: datetime
    legal_framework: str = "LEI_14711_2023_BCB_264"
    notes: str = ""
    released_timestamp: Optional[datetime] = None
    release_authorization_token: Optional[str] = None
    paid_amount_on_excussion_brl: Decimal = field(default=Decimal("0.00"))
    shortfall_on_excussion_brl: Decimal = field(default=Decimal("0.00"))

    def __post_init__(self) -> None:
        if isinstance(self.modality, str):
            self.modality = LienModality(self.modality)
        if isinstance(self.priority_rank, str):
            self.priority_rank = LienPriorityRank(self.priority_rank)
        if isinstance(self.status, str):
            self.status = LienStatus(self.status)
        self.secured_amount_brl = _quantize_money(self.secured_amount_brl)
        self.earmarked_quantity = _quantize_qty(self.earmarked_quantity)
        self.registration_timestamp = _parse_iso_timestamp(self.registration_timestamp)
        if self.released_timestamp is not None:
            self.released_timestamp = _parse_iso_timestamp(self.released_timestamp)
        self.paid_amount_on_excussion_brl = _quantize_money(self.paid_amount_on_excussion_brl)
        self.shortfall_on_excussion_brl = _quantize_money(self.shortfall_on_excussion_brl)

        if not self.lien_id or not self.lien_id.strip():
            raise InvalidLienOperationError("lien_id não pode ser vazio.")
        if self.secured_amount_brl <= Decimal("0"):
            raise InvalidLienOperationError(
                f"Valor garantido do gravame '{self.lien_id}' deve ser maior que zero."
            )
        if self.earmarked_quantity <= Decimal("0"):
            raise InvalidLienOperationError(
                f"Quantidade vinculada do gravame '{self.lien_id}' deve ser maior que zero."
            )

    @property
    def waterfall_sort_key(self) -> Tuple[int, datetime, str]:
        """Chave determinística de prelazia: (grau de prioridade, anterioridade do registro, ID)."""
        return (self.priority_rank.order, self.registration_timestamp, self.lien_id)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LienRegistration:
        return cls(
            lien_id=str(data["lien_id"]),
            pool_id=str(data["pool_id"]),
            modality=LienModality(str(data["modality"])),
            priority_rank=LienPriorityRank(str(data["priority_rank"])),
            status=LienStatus(str(data.get("status", LienStatus.ATIVO_BLOQUEADO.value))),
            creditor_document=str(data["creditor_document"]),
            creditor_name=str(data["creditor_name"]),
            contract_reference=str(data["contract_reference"]),
            secured_amount_brl=Decimal(str(data["secured_amount_brl"])),
            earmarked_quantity=Decimal(str(data["earmarked_quantity"])),
            registration_timestamp=_parse_iso_timestamp(data["registration_timestamp"]),
            legal_framework=str(data.get("legal_framework", "LEI_14711_2023_BCB_264")),
            notes=str(data.get("notes", "")),
        )


@dataclass(frozen=True)
class LienExcussionAllocation:
    """Resultado individual de pagamento de um grau na cascata de excussão."""

    lien_id: str
    creditor_document: str
    creditor_name: str
    modality: LienModality
    priority_rank: LienPriorityRank
    claimed_amount_brl: Decimal
    paid_amount_brl: Decimal
    shortfall_brl: Decimal
    fully_satisfied: bool


@dataclass(frozen=True)
class ExcussionWaterfallResult:
    """Resultado auditável da liquidação em cascata (Waterfall) por ordem de prelazia."""

    event_id: str
    pool_id: str
    execution_timestamp: datetime
    gross_proceeds_brl: Decimal
    execution_costs_brl: Decimal
    net_proceeds_brl: Decimal
    allocations: Tuple[LienExcussionAllocation, ...]
    total_claimed_brl: Decimal
    total_creditors_paid_brl: Decimal
    total_shortfall_brl: Decimal
    grantor_surplus_returned_brl: Decimal
    senior_fully_satisfied: bool
    prelacy_order_respected: bool

    def PayoutByRank(self, rank: LienPriorityRank) -> Decimal:
        """Retorna o total pago em BRL para um determinado grau de prioridade."""
        total = sum(
            (a.paid_amount_brl for a in self.allocations if a.priority_rank == rank),
            Decimal("0.00"),
        )
        return _quantize_money(total)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o resultado da excussão para dicionário compatível com JSON."""
        return {
            "event_id": self.event_id,
            "pool_id": self.pool_id,
            "execution_timestamp": self.execution_timestamp.isoformat(),
            "gross_proceeds_brl": str(self.gross_proceeds_brl),
            "execution_costs_brl": str(self.execution_costs_brl),
            "net_proceeds_brl": str(self.net_proceeds_brl),
            "total_claimed_brl": str(self.total_claimed_brl),
            "total_creditors_paid_brl": str(self.total_creditors_paid_brl),
            "total_shortfall_brl": str(self.total_shortfall_brl),
            "grantor_surplus_returned_brl": str(self.grantor_surplus_returned_brl),
            "senior_fully_satisfied": self.senior_fully_satisfied,
            "prelacy_order_respected": self.prelacy_order_respected,
            "allocations": [
                {
                    "lien_id": a.lien_id,
                    "creditor_document": a.creditor_document,
                    "creditor_name": a.creditor_name,
                    "modality": a.modality.value,
                    "priority_rank": a.priority_rank.value,
                    "claimed_amount_brl": str(a.claimed_amount_brl),
                    "paid_amount_brl": str(a.paid_amount_brl),
                    "shortfall_brl": str(a.shortfall_brl),
                    "fully_satisfied": a.fully_satisfied,
                }
                for a in self.allocations
            ],
        }


# ---------------------------------------------------------------------------
# Motor da Central de Garantias e Gravames (LienCollateralRegistryEngine)
# ---------------------------------------------------------------------------


class LienCollateralRegistryEngine:
    """Motor determinístico da Central de Garantias, Gravames e Ônus Reais B3 (FL-037)."""

    def __init__(self) -> None:
        self._pools: Dict[str, CollateralAssetPool] = {}
        self._liens: Dict[str, LienRegistration] = {}
        self._pool_liens: Dict[str, List[str]] = {}
        self._excussion_history: List[ExcussionWaterfallResult] = []

    @property
    def pools(self) -> Dict[str, CollateralAssetPool]:
        return dict(self._pools)

    @property
    def liens(self) -> Dict[str, LienRegistration]:
        return dict(self._liens)

    @property
    def excussion_history(self) -> List[ExcussionWaterfallResult]:
        return list(self._excussion_history)

    # ---- Gestão de Pools de Colateral ----

    def register_pool(self, pool: CollateralAssetPool) -> CollateralAssetPool:
        """Registra um novo lote de ativos garantidores na depositária/registradora."""
        if pool.pool_id in self._pools:
            raise DuplicatePoolError(f"Pool '{pool.pool_id}' já está registrado.")
        self._pools[pool.pool_id] = pool
        self._pool_liens[pool.pool_id] = []
        return pool

    def get_pool(self, pool_id: str) -> CollateralAssetPool:
        if pool_id not in self._pools:
            raise CollateralPoolNotFoundError(
                f"Pool garantidor '{pool_id}' não encontrado na Registradora B3."
            )
        return self._pools[pool_id]

    def get_active_liens_for_pool(self, pool_id: str) -> List[LienRegistration]:
        """Retorna os gravames ativos (bloqueados/em excussão) do pool ordenados por prelazia."""
        self.get_pool(pool_id)
        active = [
            self._liens[lid]
            for lid in self._pool_liens.get(pool_id, [])
            if self._liens[lid].status.encumbers_collateral
        ]
        return sorted(active, key=lambda l: l.waterfall_sort_key)

    def get_all_liens_for_pool(self, pool_id: str) -> List[LienRegistration]:
        """Retorna todos os gravames (ativos, baixados ou liquidados) vinculados ao pool."""
        self.get_pool(pool_id)
        all_liens = [self._liens[lid] for lid in self._pool_liens.get(pool_id, [])]
        return sorted(all_liens, key=lambda l: l.waterfall_sort_key)

    # ---- Cálculos de Capacidade de Cobertura e Bloqueio de Negociação ----

    def committed_valuation_brl(self, pool_id: str) -> Decimal:
        """Soma dos créditos garantidos por gravames ativos sobre o pool."""
        active = self.get_active_liens_for_pool(pool_id)
        total = sum((l.secured_amount_brl for l in active), Decimal("0.00"))
        return _quantize_money(total)

    def available_valuation_capacity_brl(self, pool_id: str) -> Decimal:
        """Capacidade líquida de avaliação disponível para novos graus de garantia."""
        pool = self.get_pool(pool_id)
        committed = self.committed_valuation_brl(pool_id)
        available = pool.net_valuation_amount_brl - committed
        return _quantize_money(max(available, Decimal("0.00")))

    def is_free_trading_blocked(self, pool_id: str) -> bool:
        """Verifica se o ativo está bloqueado para negociação livre na conta 2901-X."""
        return len(self.get_active_liens_for_pool(pool_id)) > 0

    def encumbered_quantity(self, pool_id: str) -> Decimal:
        """Retorna a quantidade bloqueada em custódia (limitada ao total do pool)."""
        pool = self.get_pool(pool_id)
        active = self.get_active_liens_for_pool(pool_id)
        if not active:
            return Decimal("0.0000")
        summed_qty = sum((l.earmarked_quantity for l in active), Decimal("0.0000"))
        return _quantize_qty(min(pool.total_quantity, summed_qty))

    def free_tradable_quantity(self, pool_id: str) -> Decimal:
        """Retorna a quantidade livre para negociação na conta 2101-6.

        Em conformidade com a Lei 14.711/2023 e Resolução BCB 264, enquanto houver
        gravame ativo sobre o pool, a parcela onerada permanece bloqueada e qualquer
        tentativa de alienação livre do lote gravado é impedida.
        """
        pool = self.get_pool(pool_id)
        if self.is_free_trading_blocked(pool_id):
            return Decimal("0.0000")
        return pool.total_quantity

    def validate_free_trade_order(self, pool_id: str, requested_quantity: Decimal) -> None:
        """Valida se uma ordem de venda ou transferência livre pode ser executada."""
        req_qty = _quantize_qty(requested_quantity)
        if req_qty <= Decimal("0"):
            raise InvalidLienOperationError("Quantidade solicitada para negociação deve ser positiva.")
        if self.is_free_trading_blocked(pool_id):
            active_ids = [l.lien_id for l in self.get_active_liens_for_pool(pool_id)]
            raise FreeTradingBlockedError(
                f"Negociação livre bloqueada para o pool '{pool_id}' na conta 2901-8. "
                f"Gravames ativos incidentes: {', '.join(active_ids)}."
            )
        pool = self.get_pool(pool_id)
        if req_qty > pool.total_quantity:
            raise InvalidLienOperationError(
                f"Quantidade solicitada ({req_qty}) excede o saldo total do pool ({pool.total_quantity})."
            )

    # ---- Constituição e Averbação de Gravame ----

    def constitute_lien(self, lien: LienRegistration) -> LienRegistration:
        """Constitui e averba um gravame de 1º, 2º ou 3º grau validando a capacidade do ativo."""
        if lien.lien_id in self._liens:
            raise DuplicateLienError(f"Gravame '{lien.lien_id}' já averbado na Registradora B3.")

        pool = self.get_pool(lien.pool_id)

        if lien.earmarked_quantity > pool.total_quantity:
            raise InsufficientCollateralCapacityError(
                f"Quantidade vinculada ({lien.earmarked_quantity}) no gravame '{lien.lien_id}' "
                f"excede a quantidade total custodiada no pool '{pool.pool_id}' ({pool.total_quantity})."
            )

        if lien.status.encumbers_collateral:
            available_brl = self.available_valuation_capacity_brl(pool.pool_id)
            if lien.secured_amount_brl > available_brl:
                raise InsufficientCollateralCapacityError(
                    f"Capacidade de avaliação insuficiente no pool '{pool.pool_id}' para averbar "
                    f"o gravame '{lien.lien_id}' ({lien.priority_rank.value}). "
                    f"Solicitado: R$ {lien.secured_amount_brl} | "
                    f"Disponível: R$ {available_brl} | "
                    f"Teto líquido de avaliação: R$ {pool.net_valuation_amount_brl}."
                )

            # Se estiver constituindo 2º ou 3º grau, verifica coerência da cadeia de prelazia
            active_ranks = {l.priority_rank for l in self.get_active_liens_for_pool(pool.pool_id)}
            if (
                lien.priority_rank == LienPriorityRank.TERCEIRO_GRAU
                and LienPriorityRank.SEGUNDO_GRAU not in active_ranks
                and LienPriorityRank.PRIMEIRO_GRAU not in active_ranks
            ):
                raise InvalidLienOperationError(
                    f"Não é permitido averbar gravame de 3º grau ('{lien.lien_id}') "
                    f"sem gravame anterior ativo no pool '{pool.pool_id}'."
                )

        self._liens[lien.lien_id] = lien
        self._pool_liens[pool.pool_id].append(lien.lien_id)
        return lien

    # ---- Baixa / Liberação de Gravame ----

    def release_lien(
        self,
        lien_id: str,
        release_timestamp: Union[str, datetime],
        creditor_authorization_token: str,
    ) -> LienRegistration:
        """Efetua a baixa de um gravame mediante comando/token de quitação do credor."""
        if lien_id not in self._liens:
            raise LienNotFoundError(f"Gravame '{lien_id}' não encontrado.")
        if not creditor_authorization_token or not creditor_authorization_token.strip():
            raise InvalidLienOperationError(
                "Token de autorização do credor é obrigatório para baixa de gravame."
            )

        lien = self._liens[lien_id]
        if lien.status != LienStatus.ATIVO_BLOQUEADO:
            raise InvalidLienOperationError(
                f"Gravame '{lien_id}' não pode ser baixado no status '{lien.status.value}'."
            )

        lien.status = LienStatus.BAIXADO_LIBERADO
        lien.released_timestamp = _parse_iso_timestamp(release_timestamp)
        lien.release_authorization_token = creditor_authorization_token.strip()
        return lien

    # ---- Cascata de Excussão e Liquidação por Ordem de Prelazia (Waterfall) ----

    def execute_excussion_waterfall(
        self,
        event_id: str,
        pool_id: str,
        gross_proceeds_brl: Union[Decimal, str, int, float],
        execution_costs_brl: Union[Decimal, str, int, float] = Decimal("0.00"),
        execution_timestamp: Union[str, datetime] = "2026-11-03T16:00:00Z",
    ) -> ExcussionWaterfallResult:
        """Executa a liquidação em cascata do ativo garantidor respeitando a prelazia estrita.

        Regras (Lei 14.711/2023):
        1. Deduzem-se os custos operacionais/emolumentos de excussão do produto bruto da venda.
        2. Os gravames ativos são ordenados por `(priority_rank.order, registration_timestamp, lien_id)`.
        3. O 1º Grau (Sênior) recebe 100% do seu crédito garantido antes que o 2º Grau receba
           qualquer valor excedente; o 2º Grau recebe 100% antes do 3º Grau.
        4. O saldo remanescente após a satisfação de todos os graus é integralmente restituído
           ao devedor garantidor (`grantor_surplus_returned_brl`).
        """
        self.get_pool(pool_id)
        active_liens = self.get_active_liens_for_pool(pool_id)
        if not active_liens:
            raise InvalidLienOperationError(
                f"Pool '{pool_id}' não possui gravames ativos para excussão."
            )

        gross = _quantize_money(gross_proceeds_brl)
        costs = _quantize_money(execution_costs_brl)
        if gross <= Decimal("0"):
            raise InvalidLienOperationError("Valor bruto da excussão deve ser positivo.")
        if costs < Decimal("0"):
            raise InvalidLienOperationError("Custos de excussão não podem ser negativos.")
        if costs > gross:
            raise InvalidLienOperationError(
                f"Custos de excussão (R$ {costs}) não podem exceder o valor bruto apurado (R$ {gross})."
            )

        net_proceeds = _quantize_money(gross - costs)
        remaining_balance = net_proceeds

        allocations: List[LienExcussionAllocation] = []
        total_claimed = Decimal("0.00")
        total_paid = Decimal("0.00")
        total_shortfall = Decimal("0.00")

        # Transita gravames para EM_EXCUSSAO e liquida em ordem estrita de prelazia
        for lien in active_liens:
            lien.status = LienStatus.EM_EXCUSSAO
            claimed = lien.secured_amount_brl
            paid = _quantize_money(min(remaining_balance, claimed))
            shortfall = _quantize_money(claimed - paid)
            remaining_balance = _quantize_money(remaining_balance - paid)

            lien.paid_amount_on_excussion_brl = paid
            lien.shortfall_on_excussion_brl = shortfall
            lien.status = LienStatus.EXCUTIDO_LIQUIDADO

            total_claimed = _quantize_money(total_claimed + claimed)
            total_paid = _quantize_money(total_paid + paid)
            total_shortfall = _quantize_money(total_shortfall + shortfall)

            allocations.append(
                LienExcussionAllocation(
                    lien_id=lien.lien_id,
                    creditor_document=lien.creditor_document,
                    creditor_name=lien.creditor_name,
                    modality=lien.modality,
                    priority_rank=lien.priority_rank,
                    claimed_amount_brl=claimed,
                    paid_amount_brl=paid,
                    shortfall_brl=shortfall,
                    fully_satisfied=(shortfall == Decimal("0.00")),
                )
            )

        grantor_surplus = remaining_balance

        # Verificação determinística de respeito à prelazia
        senior_allocs = [
            a for a in allocations if a.priority_rank == LienPriorityRank.PRIMEIRO_GRAU
        ]
        senior_fully_satisfied = all(a.fully_satisfied for a in senior_allocs)

        subordinated_paid = sum(
            (
                a.paid_amount_brl
                for a in allocations
                if a.priority_rank != LienPriorityRank.PRIMEIRO_GRAU
            ),
            Decimal("0.00"),
        )
        prelacy_respected = True
        if not senior_fully_satisfied and subordinated_paid > Decimal("0.00"):
            prelacy_respected = False

        # Invariante de conservação financeira: gross == costs + total_paid + grantor_surplus
        if gross != _quantize_money(costs + total_paid + grantor_surplus):
            raise LienRegistryError(
                "Quebra de invariante contábil na cascata de excussão."
            )

        result = ExcussionWaterfallResult(
            event_id=event_id,
            pool_id=pool_id,
            execution_timestamp=_parse_iso_timestamp(execution_timestamp),
            gross_proceeds_brl=gross,
            execution_costs_brl=costs,
            net_proceeds_brl=net_proceeds,
            allocations=tuple(allocations),
            total_claimed_brl=total_claimed,
            total_creditors_paid_brl=total_paid,
            total_shortfall_brl=total_shortfall,
            grantor_surplus_returned_brl=grantor_surplus,
            senior_fully_satisfied=senior_fully_satisfied,
            prelacy_order_respected=prelacy_respected,
        )
        self._excussion_history.append(result)
        return result

    # ---- Carregamento e Processamento de Fixtures JSON ----

    @classmethod
    def from_fixture(
        cls,
        fixture_path: Union[str, Path],
        execute_events: bool = False,
    ) -> LienCollateralRegistryEngine:
        """Instancia o motor a partir do arquivo JSON de fixture.

        Se `execute_events=True`, executa também os eventos de excussão e baixa
        declarados nas seções `excussion_events` e `release_events` da fixture.
        """
        path = Path(fixture_path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        engine = cls()

        for pool_data in raw.get("collateral_pools", []):
            engine.register_pool(CollateralAssetPool.from_dict(pool_data))

        for lien_data in raw.get("lien_registrations", []):
            engine.constitute_lien(LienRegistration.from_dict(lien_data))

        if execute_events:
            for exc in raw.get("excussion_events", []):
                engine.execute_excussion_waterfall(
                    event_id=str(exc["event_id"]),
                    pool_id=str(exc["pool_id"]),
                    gross_proceeds_brl=Decimal(str(exc["gross_liquidation_proceeds_brl"])),
                    execution_costs_brl=Decimal(str(exc.get("execution_costs_brl", "0.00"))),
                    execution_timestamp=str(exc["execution_timestamp"]),
                )
            for rel in raw.get("release_events", []):
                engine.release_lien(
                    lien_id=str(rel["lien_id"]),
                    release_timestamp=str(rel["release_timestamp"]),
                    creditor_authorization_token=str(rel["creditor_authorization_token"]),
                )

        return engine

    def summary_report(self) -> Dict[str, Any]:
        """Gera um relatório executivo consolidado dos pools, gravames e excussões."""
        pools_summary = {}
        for pool_id, pool in self._pools.items():
            active_liens = self.get_active_liens_for_pool(pool_id)
            pools_summary[pool_id] = {
                "asset_type": pool.asset_type,
                "ticker_or_cetip_code": pool.ticker_or_cetip_code,
                "gross_market_value_brl": str(pool.gross_market_value_brl),
                "net_valuation_amount_brl": str(pool.net_valuation_amount_brl),
                "committed_valuation_brl": str(self.committed_valuation_brl(pool_id)),
                "available_valuation_capacity_brl": str(
                    self.available_valuation_capacity_brl(pool_id)
                ),
                "is_free_trading_blocked": self.is_free_trading_blocked(pool_id),
                "active_liens_count": len(active_liens),
                "active_ranks": [l.priority_rank.value for l in active_liens],
            }
        return {
            "total_pools": len(self._pools),
            "total_registered_liens": len(self._liens),
            "total_excussions_executed": len(self._excussion_history),
            "pools": pools_summary,
        }
