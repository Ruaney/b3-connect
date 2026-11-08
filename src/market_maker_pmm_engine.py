"""Motor Regulatório do Programa de Metas de Formador de Mercado (PMM B3 / BSM) (FL-103).

Implementa a infraestrutura regulatória e computacional do Programa de Formador de Mercado
(Market Maker) da B3 S.A. – Brasil, Bolsa, Balcão, supervisionado pela BSM Supervisão de
Mercados, em estrita conformidade com:
1. Resolução CVM nº 135/2022 (e Instrução CVM nº 384/2003 e alterações posteriores);
2. Regulamento de Operações e Manual de Procedimentos Operacionais do PUMA Trading System B3;
3. Roteiro de Auditoria e Supervisão Contínua da BSM (Diretoria de Autorregulação);
4. Ofícios Circulares de Parâmetros de PMM (Presença Mínima, Spread Máximo e Profundidade por Classe).

Pilares Regulatórios e Funcionalidades:
- Monitoramento contínuo das obrigações contratuais de liquidez bilaterais em pregão contínuo.
- Parâmetros Oficiais Paramétricos:
  * Presença Mínima em Tela: Tempo de presença efetiva >= 80.00% da sessão ordinária contínua;
  * Spread Máximo de Cotação: ((Ask - Bid) / Bid) * 100 <= Teto Contratual da Classe de Liquidez;
  * Quantidade Mínima Ofertada: Lotes mínimos simultâneos e ininterruptos em ambas as pontas (bid/ask);
  * Janela de Carência de Recomposição de Ofertas (Regeneration Time, até 60s) após trade parcial/total;
  * Exclusão regulatória estrita de leilões (abertura, fechamento, volatilidade) e suspensões (trading halt).
- Auditoria Contínua de Descredenciamento e Perda de Incentivos Fiscais/Emolumentares:
  * Revogação imediata de descontos em emolumentos e taxas de negociação/liquidação se não atingir metas;
  * Descredenciamento formal e suspensão por violações reiteradas ou presença abaixo do limiar crítico.
- Leis de Invariantes Formais:
  * LAW-109A: Cumprimento estrito da taxa de presença mínima ordinária (Tempo de Presença >= 80.00%);
  * LAW-109B: Não-negatividade e teto estrito de spread de cotação bilateral (0 < Spread <= MaxSpread);
  * LAW-109C: Conservação de profundidade bilateral de lotes mínimos e respeito à janela de carência;
  * LAW-109D: Integridade de incentivos fiscais/tarifários e auditoria punitiva de descredenciamento.

100% Python Standard Library (dataclasses, datetime, decimal, enum, json, pathlib, time, typing).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

# Constantes de Precisão Numérica B3 / PUMA
TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")

DEFAULT_MIN_PRESENCE_PCT = Decimal("80.00")
CRITICAL_DEACCREDITATION_PCT = Decimal("70.00")
DEFAULT_MAX_REGENERATION_SECONDS = 60
DEFAULT_MAX_DAILY_SPREAD_VIOLATIONS = 5
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3


def round_cents(val: Union[Decimal, str, float, int]) -> Decimal:
    """Arredonda valor monetário em BRL para centavos (2 casas decimais) com ROUND_HALF_UP."""
    d = Decimal(str(val))
    return d.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def round_pct(val: Union[Decimal, str, float, int]) -> Decimal:
    """Arredonda percentual regulatório para 4 casas decimais com ROUND_HALF_UP."""
    d = Decimal(str(val))
    return d.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def round_qty(val: Union[Decimal, str, float, int]) -> Decimal:
    """Normaliza quantidade de contratos ou ações."""
    d = Decimal(str(val))
    return d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def parse_datetime(val: Union[str, datetime]) -> datetime:
    """Converte string ISO-8601 ou objeto datetime para datetime timezone-aware (UTC)."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    cleaned = str(val).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ==============================================================================
# 1. EXCEÇÕES CUSTOMIZADAS DO MOTOR PMM REGULATÓRIO
# ==============================================================================

class PmmError(Exception):
    """Exceção base para o subsistema regulatório de Formador de Mercado (PMM B3 / BSM)."""


class PmmValidationError(PmmError):
    """Disparada quando parâmetros contratuais, cotações ou snapshots são inválidos."""


class PmmContractNotFoundError(PmmError):
    """Disparada quando o contrato de PMM para determinado participante e ativo não é encontrado."""


class PmmAuditCalculationError(PmmError):
    """Disparada quando ocorre falha aritmética ou inconsistência na apuração de sessões."""


class PmmDeaccreditationError(PmmError):
    """Disparada quando um participante descredenciado tenta usufruir de incentivos ou operar como PMM."""


class PmmInvariantViolationError(PmmError):
    """Disparada quando uma lei invariante de liquidez (LAW-109) é violada."""

    def __init__(self, message: str, law_code: str = "LAW-109", details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.law_code = law_code
        self.details = details or {}

    def __str__(self) -> str:
        return f"VIOLAÇÃO DE INVARIANTE REGULATÓRIA PMM [{self.law_code}]: {self.message}"


# ==============================================================================
# 2. ENUMERAÇÕES REGULATÓRIAS B3 / BSM
# ==============================================================================

class AssetClass(str, Enum):
    """Classes de ativos negociáveis elegíveis ao Programa de Formador de Mercado B3."""

    EQUITY_CASH = "EQUITY_CASH"  # Ações à vista (Small/Mid/Large Caps)
    EQUITY_OPTION = "EQUITY_OPTION"  # Séries de opções sobre ações
    FINANCIAL_FUTURE = "FINANCIAL_FUTURE"  # Futuros financeiros (DI, Dólar, Ibovespa)
    COMMODITY_FUTURE = "COMMODITY_FUTURE"  # Futuros agropecuários (Boi Gordo, Milho, Café)
    ETF = "ETF"  # Fundos de Índice listados
    FII = "FII"  # Fundos de Investimento Imobiliário
    CORPORATE_BOND = "CORPORATE_BOND"  # Debêntures e títulos corporativos


class LiquidityTier(str, Enum):
    """Classes de liquidez definidas pela B3 para calibração de spreads e profundidade."""

    TIER_1_HIGH = "TIER_1_HIGH"  # Ativos de alta liquidez (Spread máx até 1.00%)
    TIER_2_MEDIUM = "TIER_2_MEDIUM"  # Ativos de média liquidez (Spread máx até 2.50%)
    TIER_3_LOW = "TIER_3_LOW"  # Ativos de baixa liquidez / Small Caps (Spread máx até 5.00%)
    TIER_4_ILLIQUID = "TIER_4_ILLIQUID"  # Ativos em desenvolvimento (Spread máx até 10.00%)


class MarketPhase(str, Enum):
    """Fases operacionais de negociação no PUMA Trading System da B3."""

    PRE_OPEN = "PRE_OPEN"  # Pré-abertura (excluído)
    OPENING_AUCTION = "OPENING_AUCTION"  # Leilão de abertura (excluído)
    CONTINUOUS = "CONTINUOUS"  # Negociação contínua regular (obrigação ativa)
    VOLATILITY_AUCTION = "VOLATILITY_AUCTION"  # Leilão de volatilidade / intraday (excluído)
    CLOSING_AUCTION = "CLOSING_AUCTION"  # Leilão de fechamento (excluído)
    TRADING_HALT = "TRADING_HALT"  # Suspensão temporária de negociação (excluído)
    POST_CLOSE = "POST_CLOSE"  # Pós-fechamento (excluído)
    CLOSED = "CLOSED"  # Mercado fechado (excluído)


class PmmObligationStatus(str, Enum):
    """Classificação de conformidade do formador em determinado instante do livro."""

    COMPLIANT = "COMPLIANT"  # Ambas as pontas presentes, spread <= max e lote >= min
    SPREAD_VIOLATION = "SPREAD_VIOLATION"  # Pontas presentes e com lote, mas spread > max
    DEPTH_VIOLATION = "DEPTH_VIOLATION"  # Spread adequado, mas lote em uma/ambas as pontas < min
    SPREAD_AND_DEPTH_VIOLATION = "SPREAD_AND_DEPTH_VIOLATION"  # Spread > max E quantidade < min
    MISSING_BID = "MISSING_BID"  # Apenas oferta de venda presente
    MISSING_ASK = "MISSING_ASK"  # Apenas oferta de compra presente
    ABSENT = "ABSENT"  # Ausência total de ofertas de compra e venda sem carência
    REGENERATING = "REGENERATING"  # Janela de carência de recomposição após execução (<= max_regeneration)
    AUCTION_EXCLUDED = "AUCTION_EXCLUDED"  # Fase de leilão ou suspensão (não computada na sessão contínua)


class DeaccreditationStatus(str, Enum):
    """Status regulatório de credenciamento do Formador de Mercado junto à B3 e BSM."""

    ACTIVE = "ACTIVE"  # Regularmente credenciado e adimplente
    WARNING = "WARNING"  # Notificado com advertência formal por descumprimento pontual
    INCENTIVE_SUSPENDED = "INCENTIVE_SUSPENDED"  # Benefícios tarifários e fiscais suspensos
    DEACCREDITED = "DEACCREDITED"  # Descredenciado formalmente por descumprimento reiterado


class DeaccreditationReason(str, Enum):
    """Motivo regulatório para instauração de advertência ou descredenciamento."""

    NONE = "NONE"  # Nenhuma infração
    PERSISTENT_PRESENCE_FAILURE = "PERSISTENT_PRESENCE_FAILURE"  # Presença abaixo da meta sistemática (<70% ou <80% consecutivas)
    EXCESSIVE_SPREAD_VIOLATIONS = "EXCESSIVE_SPREAD_VIOLATIONS"  # Violações contínuas de spread máximo
    CONSECUTIVE_UNJUSTIFIED_ABSENCE = "CONSECUTIVE_UNJUSTIFIED_ABSENCE"  # Ausência em dias consecutivos de pregão
    VOLUNTARY_WITHDRAWAL = "VOLUNTARY_WITHDRAWAL"  # Pedido formal voluntário de descredenciamento
    REGULATORY_SANCTION = "REGULATORY_SANCTION"  # Sanção disciplinar da BSM ou CVM


# ==============================================================================
# 3. MODELOS DE DADOS E CONTRATO DE PMM
# ==============================================================================

@dataclass(frozen=True)
class PmmContract:
    """Contrato formal do Programa de Metas de Formador de Mercado na B3."""

    contract_id: str
    participant_id: str
    symbol: str
    asset_class: AssetClass
    liquidity_tier: LiquidityTier
    min_presence_pct: Decimal = DEFAULT_MIN_PRESENCE_PCT  # Meta oficial de presença (>= 80.00%)
    max_spread_pct: Decimal = Decimal("2.00")  # Teto de spread percentual da classe
    min_bid_qty: Decimal = Decimal("100")  # Lote mínimo simultâneo de compra
    min_ask_qty: Decimal = Decimal("100")  # Lote mínimo simultâneo de venda
    max_regeneration_seconds: int = DEFAULT_MAX_REGENERATION_SECONDS  # Carência de recomposição
    max_daily_spread_violations: int = DEFAULT_MAX_DAILY_SPREAD_VIOLATIONS  # Limite diário de violações
    base_discount_pct: Decimal = Decimal("100.00")  # Desconto contratual em emolumentos/tarifas
    min_monthly_compliant_ratio: Decimal = Decimal("80.00")  # % mínimo de sessões conformes no mês
    deaccreditation_threshold_pct: Decimal = CRITICAL_DEACCREDITATION_PCT  # Piso crítico de descredenciamento
    max_consecutive_non_compliant_sessions: int = DEFAULT_MAX_CONSECUTIVE_FAILURES  # Limite consecutivo
    max_spread_ticks: Optional[Decimal] = None  # Teto adicional opcional em ticks ou reais

    def __post_init__(self) -> None:
        """Validação estrita dos parâmetros regulatórios contratuais."""
        if not self.contract_id or not self.contract_id.strip():
            raise PmmValidationError("Identificador do contrato (contract_id) é obrigatório.")
        if not self.participant_id or not self.participant_id.strip():
            raise PmmValidationError("Código do participante (participant_id) é obrigatório.")
        if not self.symbol or not self.symbol.strip():
            raise PmmValidationError("Ticker do ativo (symbol) é obrigatório.")

        min_pres = round_pct(self.min_presence_pct)
        if min_pres < Decimal("50.0000") or min_pres > Decimal("100.0000"):
            raise PmmValidationError(f"Presença mínima contratual fora dos limites regulatórios [50, 100]: {min_pres}%")

        max_spr = Decimal(str(self.max_spread_pct))
        if max_spr <= Decimal("0.0000"):
            raise PmmValidationError(f"Spread máximo percentual deve ser estritamente positivo: {max_spr}%")

        min_bid = Decimal(str(self.min_bid_qty))
        min_ask = Decimal(str(self.min_ask_qty))
        if min_bid <= Decimal("0") or min_ask <= Decimal("0"):
            raise PmmValidationError(f"Lotes mínimos de compra e venda devem ser positivos: ({min_bid}, {min_ask})")

        if self.max_regeneration_seconds < 0 or self.max_regeneration_seconds > 300:
            raise PmmValidationError(f"Janela de carência fora do intervalo regulatório [0, 300s]: {self.max_regeneration_seconds}")

        base_disc = Decimal(str(self.base_discount_pct))
        if base_disc < Decimal("0.00") or base_disc > Decimal("100.00"):
            raise PmmValidationError(f"Desconto base deve estar em [0, 100]: {base_disc}%")

        deacc_thresh = Decimal(str(self.deaccreditation_threshold_pct))
        if deacc_thresh < Decimal("0.00") or deacc_thresh > min_pres:
            raise PmmValidationError(f"Limiar de descredenciamento ({deacc_thresh}%) deve estar em [0, min_presence_pct].")

    def to_dict(self) -> Dict[str, Any]:
        """Serialização determinística do contrato."""
        return {
            "contract_id": self.contract_id,
            "participant_id": self.participant_id,
            "symbol": self.symbol,
            "asset_class": self.asset_class.value,
            "liquidity_tier": self.liquidity_tier.value,
            "min_presence_pct": str(self.min_presence_pct),
            "max_spread_pct": str(self.max_spread_pct),
            "min_bid_qty": str(self.min_bid_qty),
            "min_ask_qty": str(self.min_ask_qty),
            "max_regeneration_seconds": self.max_regeneration_seconds,
            "max_daily_spread_violations": self.max_daily_spread_violations,
            "base_discount_pct": str(self.base_discount_pct),
            "min_monthly_compliant_ratio": str(self.min_monthly_compliant_ratio),
            "deaccreditation_threshold_pct": str(self.deaccreditation_threshold_pct),
            "max_consecutive_non_compliant_sessions": self.max_consecutive_non_compliant_sessions,
            "max_spread_ticks": str(self.max_spread_ticks) if self.max_spread_ticks is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PmmContract:
        """Desserialização determinística do contrato."""
        return cls(
            contract_id=data["contract_id"],
            participant_id=data["participant_id"],
            symbol=data["symbol"],
            asset_class=AssetClass(data["asset_class"]),
            liquidity_tier=LiquidityTier(data["liquidity_tier"]),
            min_presence_pct=Decimal(str(data.get("min_presence_pct", DEFAULT_MIN_PRESENCE_PCT))),
            max_spread_pct=Decimal(str(data["max_spread_pct"])),
            min_bid_qty=Decimal(str(data["min_bid_qty"])),
            min_ask_qty=Decimal(str(data["min_ask_qty"])),
            max_regeneration_seconds=int(data.get("max_regeneration_seconds", DEFAULT_MAX_REGENERATION_SECONDS)),
            max_daily_spread_violations=int(data.get("max_daily_spread_violations", DEFAULT_MAX_DAILY_SPREAD_VIOLATIONS)),
            base_discount_pct=Decimal(str(data.get("base_discount_pct", "100.00"))),
            min_monthly_compliant_ratio=Decimal(str(data.get("min_monthly_compliant_ratio", "80.00"))),
            deaccreditation_threshold_pct=Decimal(str(data.get("deaccreditation_threshold_pct", CRITICAL_DEACCREDITATION_PCT))),
            max_consecutive_non_compliant_sessions=int(data.get("max_consecutive_non_compliant_sessions", DEFAULT_MAX_CONSECUTIVE_FAILURES)),
            max_spread_ticks=Decimal(str(data["max_spread_ticks"])) if data.get("max_spread_ticks") is not None else None,
        )


# ==============================================================================
# 4. SNAPSHOT DE COTAÇÃO E AVALIAÇÃO INSTANTÂNEA
# ==============================================================================

@dataclass
class MarketMakerQuoteSnapshot:
    """Snapshot instantâneo das ofertas vigentes do Formador de Mercado no livro de ofertas."""

    timestamp: datetime
    symbol: str
    participant_id: str
    bid_price: Optional[Decimal] = None
    bid_qty: Optional[Decimal] = None
    ask_price: Optional[Decimal] = None
    ask_qty: Optional[Decimal] = None
    phase: MarketPhase = MarketPhase.CONTINUOUS
    last_trade_timestamp: Optional[datetime] = None
    duration_seconds: Decimal = Decimal("1.0")

    def __post_init__(self) -> None:
        """Normalização de campos de data e decimais."""
        self.timestamp = parse_datetime(self.timestamp)
        if self.last_trade_timestamp is not None:
            self.last_trade_timestamp = parse_datetime(self.last_trade_timestamp)
        if self.bid_price is not None:
            self.bid_price = Decimal(str(self.bid_price))
        if self.bid_qty is not None:
            self.bid_qty = Decimal(str(self.bid_qty))
        if self.ask_price is not None:
            self.ask_price = Decimal(str(self.ask_price))
        if self.ask_qty is not None:
            self.ask_qty = Decimal(str(self.ask_qty))
        self.duration_seconds = Decimal(str(self.duration_seconds))
        if self.duration_seconds <= Decimal("0.0"):
            raise PmmValidationError("duration_seconds do snapshot deve ser estritamente positivo.")

    def to_dict(self) -> Dict[str, Any]:
        """Serialização para dicionário."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "participant_id": self.participant_id,
            "bid_price": str(self.bid_price) if self.bid_price is not None else None,
            "bid_qty": str(self.bid_qty) if self.bid_qty is not None else None,
            "ask_price": str(self.ask_price) if self.ask_price is not None else None,
            "ask_qty": str(self.ask_qty) if self.ask_qty is not None else None,
            "phase": self.phase.value,
            "last_trade_timestamp": self.last_trade_timestamp.isoformat() if self.last_trade_timestamp else None,
            "duration_seconds": str(self.duration_seconds),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MarketMakerQuoteSnapshot:
        """Desserialização a partir de dicionário."""
        return cls(
            timestamp=parse_datetime(data["timestamp"]),
            symbol=data["symbol"],
            participant_id=data["participant_id"],
            bid_price=Decimal(str(data["bid_price"])) if data.get("bid_price") is not None else None,
            bid_qty=Decimal(str(data["bid_qty"])) if data.get("bid_qty") is not None else None,
            ask_price=Decimal(str(data["ask_price"])) if data.get("ask_price") is not None else None,
            ask_qty=Decimal(str(data["ask_qty"])) if data.get("ask_qty") is not None else None,
            phase=MarketPhase(data.get("phase", MarketPhase.CONTINUOUS.value)),
            last_trade_timestamp=parse_datetime(data["last_trade_timestamp"]) if data.get("last_trade_timestamp") else None,
            duration_seconds=Decimal(str(data.get("duration_seconds", "1.0"))),
        )


@dataclass
class SnapshotEvaluationResult:
    """Resultado da avaliação determinística de conformidade de um snapshot."""

    snapshot: MarketMakerQuoteSnapshot
    status: PmmObligationStatus
    is_valid_presence: bool
    is_eligible_phase: bool
    spread_pct: Optional[Decimal] = None
    spread_ticks: Optional[Decimal] = None
    regeneration_elapsed_seconds: Optional[Decimal] = None
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialização para dicionário."""
        return {
            "snapshot": self.snapshot.to_dict(),
            "status": self.status.value,
            "is_valid_presence": self.is_valid_presence,
            "is_eligible_phase": self.is_eligible_phase,
            "spread_pct": str(self.spread_pct) if self.spread_pct is not None else None,
            "spread_ticks": str(self.spread_ticks) if self.spread_ticks is not None else None,
            "regeneration_elapsed_seconds": str(self.regeneration_elapsed_seconds) if self.regeneration_elapsed_seconds is not None else None,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SnapshotEvaluationResult:
        """Desserialização a partir de dicionário."""
        return cls(
            snapshot=MarketMakerQuoteSnapshot.from_dict(data["snapshot"]),
            status=PmmObligationStatus(data["status"]),
            is_valid_presence=bool(data["is_valid_presence"]),
            is_eligible_phase=bool(data["is_eligible_phase"]),
            spread_pct=Decimal(str(data["spread_pct"])) if data.get("spread_pct") is not None else None,
            spread_ticks=Decimal(str(data["spread_ticks"])) if data.get("spread_ticks") is not None else None,
            regeneration_elapsed_seconds=Decimal(str(data["regeneration_elapsed_seconds"])) if data.get("regeneration_elapsed_seconds") is not None else None,
            notes=str(data.get("notes", "")),
        )


# ==============================================================================
# 5. APURAÇÃO DE SESSÃO DIÁRIA, LIQUIDAÇÃO MENSAL E DESCREDENCIAMENTO
# ==============================================================================

@dataclass
class PmmSessionAudit:
    """Resultado consolidado da auditoria de um pregão diário para determinado ativo."""

    session_date: str
    symbol: str
    participant_id: str
    total_session_seconds: Decimal
    eligible_continuous_seconds: Decimal
    valid_presence_seconds: Decimal
    presence_pct: Decimal
    required_presence_pct: Decimal
    spread_violation_count: int
    depth_violation_count: int
    missing_quote_count: int
    regeneration_count: int
    is_compliant: bool
    snapshots_count: int
    failure_reasons: List[str] = field(default_factory=list)
    snapshot_results: List[SnapshotEvaluationResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialização para dicionário."""
        return {
            "session_date": self.session_date,
            "symbol": self.symbol,
            "participant_id": self.participant_id,
            "total_session_seconds": str(self.total_session_seconds),
            "eligible_continuous_seconds": str(self.eligible_continuous_seconds),
            "valid_presence_seconds": str(self.valid_presence_seconds),
            "presence_pct": str(self.presence_pct),
            "required_presence_pct": str(self.required_presence_pct),
            "spread_violation_count": self.spread_violation_count,
            "depth_violation_count": self.depth_violation_count,
            "missing_quote_count": self.missing_quote_count,
            "regeneration_count": self.regeneration_count,
            "is_compliant": self.is_compliant,
            "snapshots_count": self.snapshots_count,
            "failure_reasons": list(self.failure_reasons),
            "snapshot_results": [r.to_dict() for r in self.snapshot_results],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PmmSessionAudit:
        """Desserialização a partir de dicionário."""
        return cls(
            session_date=data["session_date"],
            symbol=data["symbol"],
            participant_id=data["participant_id"],
            total_session_seconds=Decimal(str(data["total_session_seconds"])),
            eligible_continuous_seconds=Decimal(str(data["eligible_continuous_seconds"])),
            valid_presence_seconds=Decimal(str(data["valid_presence_seconds"])),
            presence_pct=Decimal(str(data["presence_pct"])),
            required_presence_pct=Decimal(str(data["required_presence_pct"])),
            spread_violation_count=int(data["spread_violation_count"]),
            depth_violation_count=int(data["depth_violation_count"]),
            missing_quote_count=int(data["missing_quote_count"]),
            regeneration_count=int(data["regeneration_count"]),
            is_compliant=bool(data["is_compliant"]),
            snapshots_count=int(data["snapshots_count"]),
            failure_reasons=list(data.get("failure_reasons", [])),
            snapshot_results=[SnapshotEvaluationResult.from_dict(r) for r in data.get("snapshot_results", [])],
        )


@dataclass
class PmmMonthlySettlement:
    """Apuração mensal dos descontos e incentivos tarifários devidos pela B3."""

    month_reference: str
    participant_id: str
    symbol: str
    total_sessions: int
    compliant_sessions: int
    compliance_ratio_pct: Decimal
    average_presence_pct: Decimal
    min_required_presence_pct: Decimal
    is_reward_eligible: bool
    discount_pct_applied: Decimal
    gross_trading_fees: Decimal
    gross_clearing_fees: Decimal
    total_gross_fees: Decimal
    discount_amount: Decimal
    net_fees_payable: Decimal
    tier_description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialização para dicionário."""
        return {
            "month_reference": self.month_reference,
            "participant_id": self.participant_id,
            "symbol": self.symbol,
            "total_sessions": self.total_sessions,
            "compliant_sessions": self.compliant_sessions,
            "compliance_ratio_pct": str(self.compliance_ratio_pct),
            "average_presence_pct": str(self.average_presence_pct),
            "min_required_presence_pct": str(self.min_required_presence_pct),
            "is_reward_eligible": self.is_reward_eligible,
            "discount_pct_applied": str(self.discount_pct_applied),
            "gross_trading_fees": str(self.gross_trading_fees),
            "gross_clearing_fees": str(self.gross_clearing_fees),
            "total_gross_fees": str(self.total_gross_fees),
            "discount_amount": str(self.discount_amount),
            "net_fees_payable": str(self.net_fees_payable),
            "tier_description": self.tier_description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PmmMonthlySettlement:
        """Desserialização a partir de dicionário."""
        return cls(
            month_reference=data["month_reference"],
            participant_id=data["participant_id"],
            symbol=data["symbol"],
            total_sessions=int(data["total_sessions"]),
            compliant_sessions=int(data["compliant_sessions"]),
            compliance_ratio_pct=Decimal(str(data["compliance_ratio_pct"])),
            average_presence_pct=Decimal(str(data["average_presence_pct"])),
            min_required_presence_pct=Decimal(str(data["min_required_presence_pct"])),
            is_reward_eligible=bool(data["is_reward_eligible"]),
            discount_pct_applied=Decimal(str(data["discount_pct_applied"])),
            gross_trading_fees=Decimal(str(data["gross_trading_fees"])),
            gross_clearing_fees=Decimal(str(data["gross_clearing_fees"])),
            total_gross_fees=Decimal(str(data["total_gross_fees"])),
            discount_amount=Decimal(str(data["discount_amount"])),
            net_fees_payable=Decimal(str(data["net_fees_payable"])),
            tier_description=str(data.get("tier_description", "")),
        )


@dataclass
class PmmDeaccreditationAudit:
    """Auditoria formal de descredenciamento e perda de incentivos por descumprimento de metas."""

    participant_id: str
    symbol: str
    contract_id: str
    audit_date: str
    status: DeaccreditationStatus
    reason: DeaccreditationReason
    consecutive_non_compliant_count: int
    compliance_ratio_pct: Decimal
    average_presence_pct: Decimal
    incentives_revoked: bool
    fine_or_penalty_amount: Decimal
    audit_message: str
    recommendation: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialização para dicionário."""
        return {
            "participant_id": self.participant_id,
            "symbol": self.symbol,
            "contract_id": self.contract_id,
            "audit_date": self.audit_date,
            "status": self.status.value,
            "reason": self.reason.value,
            "consecutive_non_compliant_count": self.consecutive_non_compliant_count,
            "compliance_ratio_pct": str(self.compliance_ratio_pct),
            "average_presence_pct": str(self.average_presence_pct),
            "incentives_revoked": self.incentives_revoked,
            "fine_or_penalty_amount": str(self.fine_or_penalty_amount),
            "audit_message": self.audit_message,
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PmmDeaccreditationAudit:
        """Desserialização a partir de dicionário."""
        return cls(
            participant_id=data["participant_id"],
            symbol=data["symbol"],
            contract_id=data["contract_id"],
            audit_date=data["audit_date"],
            status=DeaccreditationStatus(data["status"]),
            reason=DeaccreditationReason(data["reason"]),
            consecutive_non_compliant_count=int(data["consecutive_non_compliant_count"]),
            compliance_ratio_pct=Decimal(str(data["compliance_ratio_pct"])),
            average_presence_pct=Decimal(str(data["average_presence_pct"])),
            incentives_revoked=bool(data["incentives_revoked"]),
            fine_or_penalty_amount=Decimal(str(data["fine_or_penalty_amount"])),
            audit_message=str(data["audit_message"]),
            recommendation=str(data["recommendation"]),
        )


# ==============================================================================
# 6. MOTOR REGULATÓRIO PRINCIPAL (MarketMakerPmmEngine)
# ==============================================================================

class MarketMakerPmmEngine:
    """Motor regulatório do Programa de Metas de Formador de Mercado (PMM B3 / BSM) (FL-103)."""

    def __init__(self) -> None:
        self.contracts: Dict[Tuple[str, str], PmmContract] = {}
        self.session_audits: List[PmmSessionAudit] = []
        self.monthly_settlements: List[PmmMonthlySettlement] = []
        self.deaccreditation_audits: List[PmmDeaccreditationAudit] = []

    def register_contract(self, contract: PmmContract) -> None:
        """Registra ou atualiza um contrato de PMM para a chave (participant_id, symbol)."""
        key = (contract.participant_id.strip(), contract.symbol.strip().upper())
        self.contracts[key] = contract

    def get_contract(self, participant_id: str, symbol: str) -> Optional[PmmContract]:
        """Recupera o contrato registrado para o participante e ativo informados."""
        key = (participant_id.strip(), symbol.strip().upper())
        return self.contracts.get(key)

    def list_contracts(self) -> List[PmmContract]:
        """Retorna todos os contratos de PMM registrados."""
        return list(self.contracts.values())

    def evaluate_snapshot(
        self,
        snapshot: MarketMakerQuoteSnapshot,
        contract: Optional[PmmContract] = None,
    ) -> SnapshotEvaluationResult:
        """Avalia deterministamente um snapshot de cotação contra as obrigações de PMM.

        Regras:
        1. Fases de Leilão (Abertura, Fechamento, Volatilidade) e Trading Halt são excluídas
           do cômputo da sessão contínua (AUCTION_EXCLUDED).
        2. Carência de Recomposição (Regeneration Window): Se houver execução recente e
           (snapshot.timestamp - last_trade_timestamp) <= max_regeneration_seconds,
           ausências parciais ou totais contam como presença válida (REGENERATING).
        3. Para snapshots em negociação contínua fora de carência:
           - Ausência total: ABSENT (violação).
           - Ausência de ponta: MISSING_BID ou MISSING_ASK (violação).
           - Ambas as pontas presentes:
             * Validação de livro não-invertido (Ask > Bid). Se Ask <= Bid, violação imediata.
             * Cálculo do spread: ((Ask - Bid) / Bid) * 100.
             * Checagem de spread <= max_spread_pct (e ticks se configurado).
             * Checagem de lote: bid_qty >= min_bid_qty e ask_qty >= min_ask_qty.
        """
        if contract is None:
            contract = self.get_contract(snapshot.participant_id, snapshot.symbol)
            if contract is None:
                raise PmmContractNotFoundError(
                    f"Contrato PMM não encontrado para participante '{snapshot.participant_id}' e ativo '{snapshot.symbol}'."
                )

        # 1. Exclusão de fases que não sejam negociação contínua
        if snapshot.phase != MarketPhase.CONTINUOUS:
            return SnapshotEvaluationResult(
                snapshot=snapshot,
                status=PmmObligationStatus.AUCTION_EXCLUDED,
                is_valid_presence=False,
                is_eligible_phase=False,
                notes=f"Fase operacional '{snapshot.phase.value}' excluída da apuração de pregão contínuo.",
            )

        is_eligible_phase = True

        # 2. Janela de carência de recomposição após execução no livro
        regeneration_active = False
        elapsed_regeneration: Optional[Decimal] = None
        if snapshot.last_trade_timestamp is not None:
            delta_sec = (snapshot.timestamp - snapshot.last_trade_timestamp).total_seconds()
            if delta_sec >= 0:
                elapsed_regeneration = Decimal(str(delta_sec))
                if delta_sec <= contract.max_regeneration_seconds:
                    regeneration_active = True

        has_bid = (
            snapshot.bid_price is not None
            and snapshot.bid_qty is not None
            and snapshot.bid_price > Decimal("0.0")
        )
        has_ask = (
            snapshot.ask_price is not None
            and snapshot.ask_qty is not None
            and snapshot.ask_price > Decimal("0.0")
        )

        # Casos de ausência total ou parcial de cotação
        if not has_bid and not has_ask:
            if regeneration_active:
                return SnapshotEvaluationResult(
                    snapshot=snapshot,
                    status=PmmObligationStatus.REGENERATING,
                    is_valid_presence=True,
                    is_eligible_phase=is_eligible_phase,
                    regeneration_elapsed_seconds=elapsed_regeneration,
                    notes=f"Janela de carência de recomposição legítima ({elapsed_regeneration}s <= {contract.max_regeneration_seconds}s).",
                )
            return SnapshotEvaluationResult(
                snapshot=snapshot,
                status=PmmObligationStatus.ABSENT,
                is_valid_presence=False,
                is_eligible_phase=is_eligible_phase,
                regeneration_elapsed_seconds=elapsed_regeneration,
                notes="Ausência simultânea de ofertas de compra e venda sem carência de execução.",
            )

        if not has_bid:
            if regeneration_active:
                return SnapshotEvaluationResult(
                    snapshot=snapshot,
                    status=PmmObligationStatus.REGENERATING,
                    is_valid_presence=True,
                    is_eligible_phase=is_eligible_phase,
                    regeneration_elapsed_seconds=elapsed_regeneration,
                    notes=f"Recomposição de ponta compradora ({elapsed_regeneration}s <= {contract.max_regeneration_seconds}s).",
                )
            return SnapshotEvaluationResult(
                snapshot=snapshot,
                status=PmmObligationStatus.MISSING_BID,
                is_valid_presence=False,
                is_eligible_phase=is_eligible_phase,
                notes="Oferta de compra ausente no livro de ofertas.",
            )

        if not has_ask:
            if regeneration_active:
                return SnapshotEvaluationResult(
                    snapshot=snapshot,
                    status=PmmObligationStatus.REGENERATING,
                    is_valid_presence=True,
                    is_eligible_phase=is_eligible_phase,
                    regeneration_elapsed_seconds=elapsed_regeneration,
                    notes=f"Recomposição de ponta vendedora ({elapsed_regeneration}s <= {contract.max_regeneration_seconds}s).",
                )
            return SnapshotEvaluationResult(
                snapshot=snapshot,
                status=PmmObligationStatus.MISSING_ASK,
                is_valid_presence=False,
                is_eligible_phase=is_eligible_phase,
                notes="Oferta de venda ausente no livro de ofertas.",
            )

        # 3. Ambas as pontas presentes
        assert snapshot.bid_price is not None and snapshot.ask_price is not None
        assert snapshot.bid_qty is not None and snapshot.ask_qty is not None

        if snapshot.ask_price <= snapshot.bid_price:
            return SnapshotEvaluationResult(
                snapshot=snapshot,
                status=PmmObligationStatus.SPREAD_VIOLATION,
                is_valid_presence=False,
                is_eligible_phase=is_eligible_phase,
                spread_pct=Decimal("0.0000"),
                spread_ticks=snapshot.ask_price - snapshot.bid_price,
                notes=f"Inconsistência de livro cruzado/invertido: Ask={snapshot.ask_price} <= Bid={snapshot.bid_price}.",
            )

        spread_ticks = snapshot.ask_price - snapshot.bid_price
        spread_pct = ((spread_ticks) / snapshot.bid_price) * Decimal("100.0")

        spread_ok = spread_pct <= contract.max_spread_pct
        if contract.max_spread_ticks is not None and spread_ticks > contract.max_spread_ticks:
            spread_ok = False

        depth_ok = (snapshot.bid_qty >= contract.min_bid_qty) and (snapshot.ask_qty >= contract.min_ask_qty)

        # Se spread e lote estiverem estritamente conformes
        if spread_ok and depth_ok:
            return SnapshotEvaluationResult(
                snapshot=snapshot,
                status=PmmObligationStatus.COMPLIANT,
                is_valid_presence=True,
                is_eligible_phase=is_eligible_phase,
                spread_pct=round_pct(spread_pct),
                spread_ticks=spread_ticks,
                regeneration_elapsed_seconds=elapsed_regeneration,
                notes="Ofertas bilaterais em estrita conformidade com o contrato de PMM.",
            )

        # Tolerância de recomposição de profundidade de lote após trade recente com spread ok
        if not depth_ok and regeneration_active and spread_ok:
            return SnapshotEvaluationResult(
                snapshot=snapshot,
                status=PmmObligationStatus.REGENERATING,
                is_valid_presence=True,
                is_eligible_phase=is_eligible_phase,
                spread_pct=round_pct(spread_pct),
                spread_ticks=spread_ticks,
                regeneration_elapsed_seconds=elapsed_regeneration,
                notes=f"Recomposição de lote em andamento pós-execução ({elapsed_regeneration}s <= {contract.max_regeneration_seconds}s).",
            )

        # Classificação de violações objetivas
        if not spread_ok and not depth_ok:
            status = PmmObligationStatus.SPREAD_AND_DEPTH_VIOLATION
            notes = (
                f"Violação simultânea de Spread ({spread_pct:.4f}% > {contract.max_spread_pct}%) "
                f"e Lotes (Bid={snapshot.bid_qty} < {contract.min_bid_qty}, Ask={snapshot.ask_qty} < {contract.min_ask_qty})."
            )
        elif not spread_ok:
            status = PmmObligationStatus.SPREAD_VIOLATION
            notes = f"Spread de {spread_pct:.4f}% excedeu o teto contratual de {contract.max_spread_pct}%."
        else:
            status = PmmObligationStatus.DEPTH_VIOLATION
            notes = (
                f"Profundidade insuficiente: Bid={snapshot.bid_qty} (min={contract.min_bid_qty}), "
                f"Ask={snapshot.ask_qty} (min={contract.min_ask_qty})."
            )

        return SnapshotEvaluationResult(
            snapshot=snapshot,
            status=status,
            is_valid_presence=False,
            is_eligible_phase=is_eligible_phase,
            spread_pct=round_pct(spread_pct),
            spread_ticks=spread_ticks,
            regeneration_elapsed_seconds=elapsed_regeneration,
            notes=notes,
        )

    def audit_session(
        self,
        snapshots: Sequence[MarketMakerQuoteSnapshot],
        contract: Optional[PmmContract] = None,
        session_date: Optional[str] = None,
    ) -> PmmSessionAudit:
        """Audita integralmente um pregão diário com base na sequência de snapshots do livro."""
        if not snapshots:
            raise PmmValidationError("Lista de snapshots para auditoria de sessão não pode ser vazia.")

        first = snapshots[0]
        if contract is None:
            contract = self.get_contract(first.participant_id, first.symbol)
            if contract is None:
                raise PmmContractNotFoundError(
                    f"Contrato PMM não encontrado para {first.participant_id} / {first.symbol}."
                )

        if session_date is None:
            session_date = first.timestamp.strftime("%Y-%m-%d")

        total_session_seconds = Decimal("0.0")
        eligible_continuous_seconds = Decimal("0.0")
        valid_presence_seconds = Decimal("0.0")

        spread_violation_count = 0
        depth_violation_count = 0
        missing_quote_count = 0
        regeneration_count = 0

        evaluations: List[SnapshotEvaluationResult] = []

        for snp in snapshots:
            res = self.evaluate_snapshot(snp, contract)
            evaluations.append(res)
            duration = snp.duration_seconds
            total_session_seconds += duration

            if res.is_eligible_phase:
                eligible_continuous_seconds += duration

            if res.is_valid_presence:
                valid_presence_seconds += duration

            if res.status in (PmmObligationStatus.SPREAD_VIOLATION, PmmObligationStatus.SPREAD_AND_DEPTH_VIOLATION):
                spread_violation_count += 1
            if res.status in (PmmObligationStatus.DEPTH_VIOLATION, PmmObligationStatus.SPREAD_AND_DEPTH_VIOLATION):
                depth_violation_count += 1
            if res.status in (PmmObligationStatus.MISSING_BID, PmmObligationStatus.MISSING_ASK, PmmObligationStatus.ABSENT):
                missing_quote_count += 1
            if res.status == PmmObligationStatus.REGENERATING:
                regeneration_count += 1

        if eligible_continuous_seconds > Decimal("0.0"):
            presence_pct = (valid_presence_seconds / eligible_continuous_seconds) * Decimal("100.0")
            presence_pct = round_pct(presence_pct)
        else:
            presence_pct = Decimal("0.0000")

        # Critérios formais de conformidade diária da B3
        failure_reasons: List[str] = []
        is_compliant = True

        if presence_pct < contract.min_presence_pct:
            is_compliant = False
            failure_reasons.append(
                f"Taxa de presença diária ({presence_pct}%) abaixo da meta contratual de {contract.min_presence_pct}%."
            )

        if spread_violation_count > contract.max_daily_spread_violations:
            is_compliant = False
            failure_reasons.append(
                f"Violações de spread ({spread_violation_count}) excederam o teto tolerado de {contract.max_daily_spread_violations}."
            )

        audit_res = PmmSessionAudit(
            session_date=session_date,
            symbol=contract.symbol,
            participant_id=contract.participant_id,
            total_session_seconds=total_session_seconds,
            eligible_continuous_seconds=eligible_continuous_seconds,
            valid_presence_seconds=valid_presence_seconds,
            presence_pct=presence_pct,
            required_presence_pct=contract.min_presence_pct,
            spread_violation_count=spread_violation_count,
            depth_violation_count=depth_violation_count,
            missing_quote_count=missing_quote_count,
            regeneration_count=regeneration_count,
            is_compliant=is_compliant,
            snapshots_count=len(snapshots),
            failure_reasons=failure_reasons,
            snapshot_results=evaluations,
        )
        self.session_audits.append(audit_res)
        return audit_res

    def calculate_monthly_settlement(
        self,
        session_results: Sequence[PmmSessionAudit],
        gross_trading_fees: Decimal,
        gross_clearing_fees: Decimal,
        contract: Optional[PmmContract] = None,
        month_reference: Optional[str] = None,
        use_tiered_discount: bool = False,
    ) -> PmmMonthlySettlement:
        """Consolida as sessões ordinárias mensais e apura os incentivos e descontos tarifários B3."""
        if not session_results:
            raise PmmValidationError("Lista de sessões mensais para liquidação não pode ser vazia.")

        first = session_results[0]
        if contract is None:
            contract = self.get_contract(first.participant_id, first.symbol)
            if contract is None:
                raise PmmContractNotFoundError(
                    f"Contrato não encontrado para {first.participant_id} / {first.symbol}."
                )

        if month_reference is None:
            month_reference = first.session_date[:7]

        gross_trading = round_cents(gross_trading_fees)
        gross_clearing = round_cents(gross_clearing_fees)
        if gross_trading < Decimal("0.00") or gross_clearing < Decimal("0.00"):
            raise PmmValidationError("Tarifas brutas de negociação e liquidação não podem ser negativas.")

        total_sessions = len(session_results)
        compliant_sessions = sum(1 for s in session_results if s.is_compliant)
        compliance_ratio_pct = round_pct((Decimal(compliant_sessions) / Decimal(total_sessions)) * Decimal("100.0"))

        sum_presence = sum(s.presence_pct for s in session_results)
        average_presence_pct = round_pct(sum_presence / Decimal(total_sessions))

        total_gross_fees = gross_trading + gross_clearing

        # Elegibilidade formal para fruição dos benefícios de formador
        is_reward_eligible = (
            compliance_ratio_pct >= contract.min_monthly_compliant_ratio
            and average_presence_pct >= contract.min_presence_pct
        )

        discount_pct_applied = Decimal("0.00")
        tier_description = "INELIGIBLE: Não atingiu a meta mínima de presença ou índice mensal de conformidade."

        if is_reward_eligible:
            if use_tiered_discount:
                if average_presence_pct >= Decimal("95.0000"):
                    discount_pct_applied = Decimal("100.00")
                    tier_description = "TIER_PLATINUM_100: Presença >= 95% (Isenção Total 100%)"
                elif average_presence_pct >= Decimal("90.0000"):
                    discount_pct_applied = Decimal("90.00")
                    tier_description = "TIER_GOLD_90: Presença >= 90% (Desconto de 90%)"
                elif average_presence_pct >= Decimal("80.0000"):
                    discount_pct_applied = Decimal("75.00")
                    tier_description = "TIER_SILVER_75: Presença >= 80% (Desconto de 75%)"
                else:
                    discount_pct_applied = contract.base_discount_pct
                    tier_description = f"TIER_STANDARD: Desconto contratual padrão de {contract.base_discount_pct}%"
            else:
                discount_pct_applied = contract.base_discount_pct
                tier_description = f"ISENCAO_CONTRATUAL_PLENA: Desconto de {contract.base_discount_pct}% concedido."

        discount_amount = round_cents(total_gross_fees * (discount_pct_applied / Decimal("100.0")))
        net_fees_payable = total_gross_fees - discount_amount

        settlement = PmmMonthlySettlement(
            month_reference=month_reference,
            participant_id=contract.participant_id,
            symbol=contract.symbol,
            total_sessions=total_sessions,
            compliant_sessions=compliant_sessions,
            compliance_ratio_pct=compliance_ratio_pct,
            average_presence_pct=average_presence_pct,
            min_required_presence_pct=contract.min_presence_pct,
            is_reward_eligible=is_reward_eligible,
            discount_pct_applied=discount_pct_applied,
            gross_trading_fees=gross_trading,
            gross_clearing_fees=gross_clearing,
            total_gross_fees=total_gross_fees,
            discount_amount=discount_amount,
            net_fees_payable=net_fees_payable,
            tier_description=tier_description,
        )
        self.monthly_settlements.append(settlement)
        return settlement

    def audit_deaccreditation(
        self,
        session_results: Sequence[PmmSessionAudit],
        contract: Optional[PmmContract] = None,
        audit_date: Optional[str] = None,
        enforce_immediate_loss: bool = True,
    ) -> PmmDeaccreditationAudit:
        """Executa auditoria regulatória de descredenciamento e suspensão de incentivos da BSM/B3.

        Gatilhos de Sanção:
        1. Descredenciamento Sumário (DEACCREDITED):
           - Presença média abaixo do limiar crítico (ex: < 70.00%); OU
           - Número de sessões não-conformes consecutivas >= max_consecutive_non_compliant_sessions (ex: 3 dias); OU
           - Razão de conformidade mensal abaixo de 50%.
        2. Suspensão de Incentivos (INCENTIVE_SUSPENDED):
           - Razão de conformidade mensal abaixo de min_monthly_compliant_ratio (ex: < 80.00%); OU
           - Presença média abaixo de min_presence_pct, mas acima de deaccreditation_threshold_pct.
        3. Notificação de Advertência (WARNING):
           - Presença pontualmente descumprida em 1 ou 2 sessões esparsas.
        4. Regular (ACTIVE): Plena conformidade com as metas.
        """
        if not session_results:
            raise PmmValidationError("Lista de sessões para auditoria de descredenciamento não pode ser vazia.")

        first = session_results[0]
        if contract is None:
            contract = self.get_contract(first.participant_id, first.symbol)
            if contract is None:
                raise PmmContractNotFoundError(
                    f"Contrato não encontrado para {first.participant_id} / {first.symbol}."
                )

        if audit_date is None:
            audit_date = session_results[-1].session_date

        total_sessions = len(session_results)
        compliant_sessions = sum(1 for s in session_results if s.is_compliant)
        compliance_ratio_pct = round_pct((Decimal(compliant_sessions) / Decimal(total_sessions)) * Decimal("100.0"))

        sum_presence = sum(s.presence_pct for s in session_results)
        average_presence_pct = round_pct(sum_presence / Decimal(total_sessions))

        # Cálculo do máximo de sessões consecutivas com falha
        max_consecutive_failures = 0
        current_streak = 0
        for s in session_results:
            if not s.is_compliant:
                current_streak += 1
                if current_streak > max_consecutive_failures:
                    max_consecutive_failures = current_streak
            else:
                current_streak = 0

        # Aplicação determinística da régua regulatória BSM
        status = DeaccreditationStatus.ACTIVE
        reason = DeaccreditationReason.NONE
        incentives_revoked = False
        penalty = Decimal("0.00")
        msg = "Formador de Mercado em situação regular e adimplente perante a B3 e BSM."
        recommendation = "Manter credenciamento e concessão integral de isenção de emolumentos."

        critical_presence = average_presence_pct < contract.deaccreditation_threshold_pct
        excessive_consecutive = max_consecutive_failures >= contract.max_consecutive_non_compliant_sessions
        critical_monthly_ratio = compliance_ratio_pct < Decimal("50.0000")

        if critical_presence or excessive_consecutive or critical_monthly_ratio:
            status = DeaccreditationStatus.DEACCREDITED
            reason = DeaccreditationReason.PERSISTENT_PRESENCE_FAILURE
            incentives_revoked = True
            penalty = round_cents(Decimal("10000.00"))  # Multa cominatória regulatória padrão
            reasons_list = []
            if critical_presence:
                reasons_list.append(f"presença média ({average_presence_pct}%) abaixo do piso crítico de {contract.deaccreditation_threshold_pct}%")
            if excessive_consecutive:
                reasons_list.append(f"{max_consecutive_failures} sessões consecutivas com inadimplemento (teto={contract.max_consecutive_non_compliant_sessions})")
            if critical_monthly_ratio:
                reasons_list.append(f"índice de conformidade mensal crítico ({compliance_ratio_pct}% < 50%)")

            msg = f"DESCREDENCIAMENTO DETERMINADO PELA BSM: Formador descumpriu metas regulatórias fundamentais: {'; '.join(reasons_list)}."
            recommendation = (
                "Instaurar processo de descredenciamento definitivo, cassar benefícios fiscais e "
                "recalcular emolumentos à tarifa cheia (sem desconto) para todo o período."
            )

        elif compliance_ratio_pct < contract.min_monthly_compliant_ratio or average_presence_pct < contract.min_presence_pct:
            status = DeaccreditationStatus.INCENTIVE_SUSPENDED
            reason = DeaccreditationReason.PERSISTENT_PRESENCE_FAILURE
            incentives_revoked = True
            msg = (
                f"SUSPENSÃO DE INCENTIVOS TARIFÁRIOS: Índice mensal ({compliance_ratio_pct}%) ou "
                f"presença média ({average_presence_pct}%) abaixo do mínimo contratual ({contract.min_monthly_compliant_ratio}% / {contract.min_presence_pct}%)."
            )
            recommendation = "Suspender isenção de tarifas do mês e reavaliar desempenho no próximo ciclo de apuração."

        elif any(not s.is_compliant for s in session_results):
            status = DeaccreditationStatus.WARNING
            reason = DeaccreditationReason.NONE
            incentives_revoked = False
            failed_dates = [s.session_date for s in session_results if not s.is_compliant]
            msg = f"NOTIFICAÇÃO DE ADVERTÊNCIA: Inadimplemento pontual identificado nas datas: {', '.join(failed_dates)}."
            recommendation = "Emitir notificação formal de alerta ao participante sem perda imediata de credenciamento."

        deacc_audit = PmmDeaccreditationAudit(
            participant_id=contract.participant_id,
            symbol=contract.symbol,
            contract_id=contract.contract_id,
            audit_date=audit_date,
            status=status,
            reason=reason,
            consecutive_non_compliant_count=max_consecutive_failures,
            compliance_ratio_pct=compliance_ratio_pct,
            average_presence_pct=average_presence_pct,
            incentives_revoked=incentives_revoked,
            fine_or_penalty_amount=penalty,
            audit_message=msg,
            recommendation=recommendation,
        )
        self.deaccreditation_audits.append(deacc_audit)
        return deacc_audit

    def load_from_fixture(self, fixture_path: Union[str, Path]) -> Dict[str, Any]:
        """Carrega contratos, sessões e cobranças de uma fixture JSON e processa auditoria completa."""
        path = Path(fixture_path)
        if not path.exists():
            raise FileNotFoundError(f"Fixture PMM não encontrada no caminho especificado: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 1. Registrar contratos
        contracts_data = data.get("contracts", [])
        for c_dict in contracts_data:
            c = PmmContract.from_dict(c_dict)
            self.register_contract(c)

        # 2. Auditar sessões de snapshots
        sessions_data = data.get("sessions", [])
        session_results: List[PmmSessionAudit] = []
        sessions_by_key: Dict[Tuple[str, str], List[PmmSessionAudit]] = {}

        for s_dict in sessions_data:
            pid = s_dict["participant_id"]
            sym = s_dict["symbol"]
            s_date = s_dict.get("session_date")

            snapshots: List[MarketMakerQuoteSnapshot] = []
            for q_dict in s_dict["snapshots"]:
                snp = MarketMakerQuoteSnapshot(
                    timestamp=parse_datetime(q_dict["timestamp"]),
                    symbol=sym,
                    participant_id=pid,
                    bid_price=Decimal(str(q_dict["bid_price"])) if q_dict.get("bid_price") is not None else None,
                    bid_qty=Decimal(str(q_dict["bid_qty"])) if q_dict.get("bid_qty") is not None else None,
                    ask_price=Decimal(str(q_dict["ask_price"])) if q_dict.get("ask_price") is not None else None,
                    ask_qty=Decimal(str(q_dict["ask_qty"])) if q_dict.get("ask_qty") is not None else None,
                    phase=MarketPhase(q_dict.get("phase", MarketPhase.CONTINUOUS.value)),
                    last_trade_timestamp=parse_datetime(q_dict["last_trade_timestamp"]) if q_dict.get("last_trade_timestamp") else None,
                    duration_seconds=Decimal(str(q_dict.get("duration_seconds", "1.0"))),
                )
                snapshots.append(snp)

            res = self.audit_session(snapshots, session_date=s_date)
            session_results.append(res)
            key = (pid.strip(), sym.strip().upper())
            sessions_by_key.setdefault(key, []).append(res)

        # 3. Liquidar incentivos e tarifas mensais
        monthly_settlements: List[PmmMonthlySettlement] = []
        monthly_bills = data.get("monthly_billings", [])
        for mb in monthly_bills:
            pid = mb["participant_id"]
            sym = mb["symbol"]
            m_ref = mb.get("month_reference")
            tiered = mb.get("use_tiered_discount", False)
            key = (pid.strip(), sym.strip().upper())
            key_sessions = sessions_by_key.get(key, [])
            if key_sessions:
                settle = self.calculate_monthly_settlement(
                    session_results=key_sessions,
                    gross_trading_fees=Decimal(str(mb["gross_trading_fees"])),
                    gross_clearing_fees=Decimal(str(mb["gross_clearing_fees"])),
                    month_reference=m_ref,
                    use_tiered_discount=tiered,
                )
                monthly_settlements.append(settle)

        # 4. Auditorias de descredenciamento
        deacc_results: List[PmmDeaccreditationAudit] = []
        for key, sess_list in sessions_by_key.items():
            contract = self.get_contract(key[0], key[1])
            deacc = self.audit_deaccreditation(sess_list, contract=contract)
            deacc_results.append(deacc)

        return {
            "contracts_loaded": len(contracts_data),
            "sessions_audited": len(session_results),
            "monthly_settlements": len(monthly_settlements),
            "deaccreditations_audited": len(deacc_results),
        }

    # ==============================================================================
    # 7. AUDITORIA FORMAL DAS LEIS DE INVARIANTES (LAW-109)
    # ==============================================================================

    def verify_invariants(self) -> Dict[str, bool]:
        """Auditoria formal das invariantes regulatórias do Programa de Formador de Mercado (LAW-109).

        Invariantes Auditadas:
        - LAW-109A: Cumprimento estrito da taxa de presença mínima ordinária (Tempo de Presença >= 80%).
          Toda sessão auditada com is_compliant == True DEVE ter presence_pct >= required_presence_pct.
        - LAW-109B: Não-negatividade e teto estrito de spread de cotação bilateral (0 < Spread <= MaxSpread).
          Em todo snapshot avaliado como COMPLIANT, ask_price > bid_price > 0 e spread_pct <= max_spread_pct.
        - LAW-109C: Conservação de profundidade bilateral de lotes mínimos e respeito à janela de carência.
          Em todo snapshot COMPLIANT, bid_qty >= min_bid_qty > 0 e ask_qty >= min_ask_qty > 0.
          Em snapshots REGENERATING, elapsed_seconds <= max_regeneration_seconds.
        - LAW-109D: Integridade de incentivos fiscais/tarifários e auditoria de descredenciamento.
          Nenhum desconto pode ser concedido se is_reward_eligible == False.
          Se um formador estiver DEACCREDITED ou com incentivo suspenso, discount_amount DEVE ser zero.
        """
        # LAW-109A: Presença Mínima Obrigatória
        law_109a_holds = True
        for session in self.session_audits:
            contract = self.get_contract(session.participant_id, session.symbol)
            min_req = contract.min_presence_pct if contract else session.required_presence_pct
            if session.is_compliant:
                if session.presence_pct < min_req:
                    raise PmmInvariantViolationError(
                        f"Violação LAW-109A na sessão {session.session_date} ({session.symbol}): "
                        f"Marcada como COMPLIANT, mas presença {session.presence_pct}% < {min_req}%.",
                        law_code="LAW-109A",
                        details={"session": session.to_dict()},
                    )
            else:
                # Se não for conforme, deve ter motivo registrado
                if not session.failure_reasons:
                    raise PmmInvariantViolationError(
                        f"Violação LAW-109A na sessão {session.session_date} ({session.symbol}): "
                        "Sessão não-conforme sem registro de motivos de reprovação.",
                        law_code="LAW-109A",
                    )

        # LAW-109B: Spread Máximo e Não-Negatividade
        law_109b_holds = True
        for session in self.session_audits:
            contract = self.get_contract(session.participant_id, session.symbol)
            max_spr = contract.max_spread_pct if contract else Decimal("2.00")
            max_ticks = contract.max_spread_ticks if contract else None

            for eval_res in session.snapshot_results:
                if eval_res.status == PmmObligationStatus.COMPLIANT:
                    snp = eval_res.snapshot
                    if snp.bid_price is None or snp.ask_price is None:
                        raise PmmInvariantViolationError(
                            "Violação LAW-109B: Snapshot COMPLIANT possui preços nulos.",
                            law_code="LAW-109B",
                            details={"snapshot": snp.to_dict()},
                        )
                    if snp.bid_price <= Decimal("0.0") or snp.ask_price <= snp.bid_price:
                        raise PmmInvariantViolationError(
                            f"Violação LAW-109B: Preços de compra/venda inválidos ou livro cruzado (Bid={snp.bid_price}, Ask={snp.ask_price}).",
                            law_code="LAW-109B",
                            details={"snapshot": snp.to_dict()},
                        )
                    if eval_res.spread_pct is None or eval_res.spread_pct > max_spr:
                        raise PmmInvariantViolationError(
                            f"Violação LAW-109B: Spread {eval_res.spread_pct}% excedeu o teto de {max_spr}%.",
                            law_code="LAW-109B",
                            details={"snapshot": snp.to_dict()},
                        )
                    if max_ticks is not None and eval_res.spread_ticks is not None:
                        if eval_res.spread_ticks > max_ticks:
                            raise PmmInvariantViolationError(
                                f"Violação LAW-109B: Spread em ticks {eval_res.spread_ticks} excedeu o teto de {max_ticks}.",
                                law_code="LAW-109B",
                            )

        # LAW-109C: Profundidade Bilateral e Recomposição
        law_109c_holds = True
        for session in self.session_audits:
            contract = self.get_contract(session.participant_id, session.symbol)
            min_bid = contract.min_bid_qty if contract else Decimal("100")
            min_ask = contract.min_ask_qty if contract else Decimal("100")
            max_regen = contract.max_regeneration_seconds if contract else DEFAULT_MAX_REGENERATION_SECONDS

            for eval_res in session.snapshot_results:
                if eval_res.status == PmmObligationStatus.COMPLIANT:
                    snp = eval_res.snapshot
                    if snp.bid_qty is None or snp.ask_qty is None:
                        raise PmmInvariantViolationError(
                            "Violação LAW-109C: Snapshot COMPLIANT com quantidade nula.",
                            law_code="LAW-109C",
                        )
                    if snp.bid_qty < min_bid or snp.ask_qty < min_ask:
                        raise PmmInvariantViolationError(
                            f"Violação LAW-109C: Quantidade insuficiente em snapshot COMPLIANT (Bid={snp.bid_qty} < {min_bid}, Ask={snp.ask_qty} < {min_ask}).",
                            law_code="LAW-109C",
                        )
                elif eval_res.status == PmmObligationStatus.REGENERATING:
                    if eval_res.regeneration_elapsed_seconds is not None:
                        if eval_res.regeneration_elapsed_seconds > Decimal(str(max_regen)):
                            raise PmmInvariantViolationError(
                                f"Violação LAW-109C: Janela de carência de recomposição extrapolada ({eval_res.regeneration_elapsed_seconds}s > {max_regen}s).",
                                law_code="LAW-109C",
                            )

        # LAW-109D: Integridade de Incentivos e Descredenciamento
        law_109d_holds = True
        for settlement in self.monthly_settlements:
            if not settlement.is_reward_eligible:
                if settlement.discount_amount > Decimal("0.00") or settlement.discount_pct_applied > Decimal("0.00"):
                    raise PmmInvariantViolationError(
                        f"Violação LAW-109D: Concessão indevida de desconto tarifário ({settlement.discount_amount}) para participante inelegível.",
                        law_code="LAW-109D",
                        details={"settlement": settlement.to_dict()},
                    )
            else:
                # Se elegível, o valor líquido a pagar não pode exceder o bruto
                if settlement.net_fees_payable > settlement.total_gross_fees:
                    raise PmmInvariantViolationError(
                        f"Violação LAW-109D: Valor líquido ({settlement.net_fees_payable}) maior que bruto ({settlement.total_gross_fees}).",
                        law_code="LAW-109D",
                    )

        for deacc in self.deaccreditation_audits:
            if deacc.status in (DeaccreditationStatus.DEACCREDITED, DeaccreditationStatus.INCENTIVE_SUSPENDED):
                if not deacc.incentives_revoked:
                    raise PmmInvariantViolationError(
                        f"Violação LAW-109D: Formador {deacc.status.value} deve ter incentives_revoked == True obrigatoriamente.",
                        law_code="LAW-109D",
                        details={"deaccreditation": deacc.to_dict()},
                    )

        return {
            "LAW-109A": law_109a_holds,
            "LAW-109B": law_109b_holds,
            "LAW-109C": law_109c_holds,
            "LAW-109D": law_109d_holds,
        }

    # ==============================================================================
    # 8. SERIALIZAÇÃO INTEGRAL DO MOTOR
    # ==============================================================================

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado integral do motor para representação em dicionário JSON-compatível."""
        return {
            "contracts": [c.to_dict() for c in self.contracts.values()],
            "session_audits": [s.to_dict() for s in self.session_audits],
            "monthly_settlements": [m.to_dict() for m in self.monthly_settlements],
            "deaccreditation_audits": [d.to_dict() for d in self.deaccreditation_audits],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MarketMakerPmmEngine:
        """Restaura o estado do motor a partir de um dicionário previamente serializado."""
        engine = cls()
        for c_data in data.get("contracts", []):
            contract = PmmContract.from_dict(c_data)
            engine.register_contract(contract)

        for s_data in data.get("session_audits", []):
            session = PmmSessionAudit.from_dict(s_data)
            engine.session_audits.append(session)

        for m_data in data.get("monthly_settlements", []):
            settlement = PmmMonthlySettlement.from_dict(m_data)
            engine.monthly_settlements.append(settlement)

        for d_data in data.get("deaccreditation_audits", []):
            deacc = PmmDeaccreditationAudit.from_dict(d_data)
            engine.deaccreditation_audits.append(deacc)

        return engine
