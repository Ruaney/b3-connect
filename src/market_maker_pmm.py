"""Módulo do Programa de Formador de Mercado (PMM) B3 (FL-046).

Implementa os padrões determinísticos de auditoria contínua e apuração tarifária
para Formadores de Mercado credenciados na B3 S.A., em estrita conformidade com:
1. Resolução CVM nº 135/2022 (antiga Instrução CVM nº 384/2003).
2. Regulamento de Operações e Manual de Procedimentos Operacionais da B3.
3. Ofícios Circulares de PMM (Parâmetros de Presença, Spread Máximo e Lote Mínimo).

Funcionalidades:
- Cadastro e validação de contratos de PMM para instrumentos elegíveis (Ações Small/Mid Caps,
  Opções de Liquidez Moderada, Futuros Financeiros/Commodities e ETFs).
- Avaliação determinística de snapshots de cotação do livro de ofertas (BookQuoteSnapshot)
  com checagem simultânea de spread percentual e profundidade de lote em ambas as pontas.
- Tratamento da janela de recomposição de ofertas (Regeneration Time, até 60 segundos)
  após execuções parciais ou totais.
- Exclusão regulatória de fases de leilão (Abertura, Fechamento, Volatilidade) e suspensão.
- Cômputo exato da taxa diária de presença no pregão contínuo ((Tempo_Valido / Tempo_Total) * 100).
- Apuração mensal de conformidade regulatória e cálculo de isenções tarifárias
  (emolumentos e taxas de negociação/liquidação da B3, até 100% de desconto).

Zero dependências externas (100% Python Standard Library).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


# ==============================================================================
# 1. EXCEÇÕES CUSTOMIZADAS DO MOTOR PMM
# ==============================================================================

class PmmError(Exception):
    """Exceção base para o subsistema de Formador de Mercado (PMM)."""


class PmmValidationError(PmmError):
    """Disparada quando parâmetros de contrato ou dados de cotação são inválidos."""


class PmmContractNotFoundError(PmmError):
    """Disparada quando uma consulta a contrato de PMM não é localizada."""


class PmmAuditCalculationError(PmmError):
    """Disparada quando ocorre falha aritmética ou inconsistência na apuração."""


# ==============================================================================
# 2. ENUMERAÇÕES REGULATÓRIAS B3
# ==============================================================================

class PmmObligationStatus(str, Enum):
    """Classificação de conformidade do formador em determinado instante do livro."""

    COMPLIANT = "COMPLIANT"  # Ambas as pontas presentes, spread <= max e lote >= min
    SPREAD_VIOLATION = "SPREAD_VIOLATION"  # Pontas presentes e com lote, mas spread > max
    DEPTH_VIOLATION = "DEPTH_VIOLATION"  # Spread adequado, mas quantidade em uma/ambas as pontas < min
    SPREAD_AND_DEPTH_VIOLATION = "SPREAD_AND_DEPTH_VIOLATION"  # Spread > max E quantidade < min
    MISSING_BID = "MISSING_BID"  # Apenas oferta de venda presente no livro
    MISSING_ASK = "MISSING_ASK"  # Apenas oferta de compra presente no livro
    ABSENT = "ABSENT"  # Ausência total de ofertas do formador no livro
    REGENERATING = "REGENERATING"  # Janela de carência de recomposição após execução (<= max_regeneration)
    AUCTION_EXCLUDED = "AUCTION_EXCLUDED"  # Fase de leilão ou suspensão (não computada no pregão contínuo)


class MarketPhase(str, Enum):
    """Fases operacionais de negociação no PUMA Trading System da B3."""

    CONTINUOUS = "CONTINUOUS"  # Negociação contínua regular (obrigação ativa)
    OPENING_AUCTION = "OPENING_AUCTION"  # Leilão de abertura (excluído)
    CLOSING_AUCTION = "CLOSING_AUCTION"  # Leilão de fechamento (excluído)
    VOLATILITY_AUCTION = "VOLATILITY_AUCTION"  # Leilão de volatilidade / intraday (excluído)
    TRADING_HALT = "TRADING_HALT"  # Suspensão temporária de negociação (excluído)
    CLOSED = "CLOSED"  # Mercado fechado (excluído)


class AssetClass(str, Enum):
    """Classes de ativos elegíveis ao Programa de Formador de Mercado B3."""

    EQUITY_SMALL_CAP = "EQUITY_SMALL_CAP"  # Ações de menor liquidez / Small e Mid Caps
    EQUITY_OPTION = "EQUITY_OPTION"  # Séries de opções com liquidez moderada
    FINANCIAL_FUTURE = "FINANCIAL_FUTURE"  # Futuros de taxas, índices e moedas
    COMMODITY_FUTURE = "COMMODITY_FUTURE"  # Futuros agropecuários (BGI, CCM, ICF, OZ1D)
    ETF = "ETF"  # Fundos de Índice listados (BOVA11, SMAL11, HASH11, etc.)


# ==============================================================================
# 3. CONTRATO DE PMM E PARÂMETROS OPERACIONAIS
# ==============================================================================

def _to_decimal(val: Any) -> Decimal:
    """Converte valor para Decimal de forma determinística."""
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val))


def parse_datetime(val: Union[str, datetime]) -> datetime:
    """Converte string ISO-8601 ou objeto datetime para datetime timezone-aware (UTC)."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    cleaned = val.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True)
class PmmAssetContract:
    """Definição formal dos parâmetros contratuais de PMM para um ativo na B3."""

    contract_id: str
    participant_id: str
    symbol: str
    asset_class: AssetClass
    min_presence_pct: Decimal  # Meta diária/mensal de presença (ex: 80.00 ou 90.00)
    max_spread_pct: Decimal  # Teto de spread percentual: ((Ask - Bid) / Bid) * 100
    min_bid_qty: Decimal  # Profundidade mínima da oferta de compra
    min_ask_qty: Decimal  # Profundidade mínima da oferta de venda
    max_regeneration_seconds: int = 60  # Janela de carência para recompor cotação
    max_daily_spread_violations: int = 10  # Limite máximo de violações toleradas por sessão
    base_discount_pct: Decimal = Decimal("100.00")  # Desconto contratual em emolumentos/liquidação
    min_monthly_compliant_ratio: Decimal = Decimal("80.00")  # % mínimo de dias conformes no mês
    max_spread_ticks: Optional[Decimal] = None  # Teto alternativo/adicional em ticks absolutos

    def __post_init__(self) -> None:
        """Validação estrita dos parâmetros regulatórios do contrato."""
        if not self.contract_id or not self.contract_id.strip():
            raise PmmValidationError("Identificador do contrato (contract_id) é obrigatório.")
        if not self.participant_id or not self.participant_id.strip():
            raise PmmValidationError("Código do participante (participant_id) é obrigatório.")
        if not self.symbol or not self.symbol.strip():
            raise PmmValidationError("Ticker do ativo (symbol) é obrigatório.")

        min_pres = _to_decimal(self.min_presence_pct)
        if min_pres <= Decimal("0") or min_pres > Decimal("100"):
            raise PmmValidationError(f"Taxa mínima de presença deve estar em (0, 100], recebido: {min_pres}")

        max_spr = _to_decimal(self.max_spread_pct)
        if max_spr <= Decimal("0"):
            raise PmmValidationError(f"Spread máximo percentual deve ser positivo, recebido: {max_spr}")

        min_bid = _to_decimal(self.min_bid_qty)
        min_ask = _to_decimal(self.min_ask_qty)
        if min_bid <= Decimal("0") or min_ask <= Decimal("0"):
            raise PmmValidationError(f"Lotes mínimos de compra e venda devem ser positivos: ({min_bid}, {min_ask})")

        if self.max_regeneration_seconds < 0:
            raise PmmValidationError("Tempo máximo de recomposição não pode ser negativo.")

        base_disc = _to_decimal(self.base_discount_pct)
        if base_disc < Decimal("0") or base_disc > Decimal("100"):
            raise PmmValidationError(f"Desconto base deve estar em [0, 100], recebido: {base_disc}")

        min_ratio = _to_decimal(self.min_monthly_compliant_ratio)
        if min_ratio < Decimal("0") or min_ratio > Decimal("100"):
            raise PmmValidationError(f"Razão mensal mínima deve estar em [0, 100], recebido: {min_ratio}")


# ==============================================================================
# 4. SNAPSHOT DE COTAÇÃO DO LIVRO DE OFERTAS
# ==============================================================================

@dataclass
class BookQuoteSnapshot:
    """Snapshot instantâneo das ofertas vigentes do Formador de Mercado."""

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
        """Normalização de campos numéricos e datas."""
        self.timestamp = parse_datetime(self.timestamp)
        if self.last_trade_timestamp is not None:
            self.last_trade_timestamp = parse_datetime(self.last_trade_timestamp)
        if self.bid_price is not None:
            self.bid_price = _to_decimal(self.bid_price)
        if self.bid_qty is not None:
            self.bid_qty = _to_decimal(self.bid_qty)
        if self.ask_price is not None:
            self.ask_price = _to_decimal(self.ask_price)
        if self.ask_qty is not None:
            self.ask_qty = _to_decimal(self.ask_qty)
        self.duration_seconds = _to_decimal(self.duration_seconds)
        if self.duration_seconds <= Decimal("0"):
            raise PmmValidationError("duration_seconds do snapshot deve ser estritamente positivo.")


@dataclass
class SnapshotAuditResult:
    """Resultado da avaliação de conformidade de um snapshot específico."""

    snapshot: BookQuoteSnapshot
    status: PmmObligationStatus
    is_valid_presence: bool
    is_eligible_phase: bool
    spread_pct: Optional[Decimal] = None
    spread_ticks: Optional[Decimal] = None
    regeneration_elapsed_seconds: Optional[Decimal] = None
    notes: str = ""


# ==============================================================================
# 5. APURAÇÃO DIÁRIA DE SESSÃO E CONSOLIDAÇÃO MENSAL
# ==============================================================================

@dataclass
class PmmSessionAuditResult:
    """Resultado consolidado da auditoria de um pregão para determinado ativo."""

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
    snapshot_results: List[SnapshotAuditResult] = field(default_factory=list)
    failure_reasons: List[str] = field(default_factory=list)


@dataclass
class PmmMonthlyTariffReward:
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


# ==============================================================================
# 6. MOTOR PRINCIPAL DE AUDITORIA E TARIFAS PMM
# ==============================================================================

class MarketMakerPmmEngine:
    """Motor regulatório determinístico do Programa de Formador de Mercado B3."""

    def __init__(self) -> None:
        self._contracts: Dict[Tuple[str, str], PmmAssetContract] = {}

    def register_contract(self, contract: PmmAssetContract) -> None:
        """Registra ou atualiza um contrato de PMM para o par (participant_id, symbol)."""
        key = (contract.participant_id.strip(), contract.symbol.strip().upper())
        self._contracts[key] = contract

    def get_contract(self, participant_id: str, symbol: str) -> Optional[PmmAssetContract]:
        """Obtém o contrato registrado para o participante e símbolo especificados."""
        key = (participant_id.strip(), symbol.strip().upper())
        return self._contracts.get(key)

    def evaluate_snapshot(
        self,
        snapshot: BookQuoteSnapshot,
        contract: Optional[PmmAssetContract] = None,
    ) -> SnapshotAuditResult:
        """Avalia deterministamente um snapshot de cotação contra as obrigações do PMM.

        Regras:
        1. Fases de Leilão / Suspensão são marcadas como AUCTION_EXCLUDED (não contam
           para a negociação contínua elegível nem para a presença válida).
        2. Se houver execução recente e o tempo decorrido estiver dentro de
           `max_regeneration_seconds`, ausências parciais ou totais contam como REGENERATING
           (presença válida aprovada pela regra de recomposição).
        3. Para snapshots em negociação contínua fora de recomposição:
           - Ausência total: ABSENT (violação).
           - Ausência parcial: MISSING_BID ou MISSING_ASK (violação).
           - Ambas as pontas presentes:
             * Cálculo do spread: ((Ask - Bid) / Bid) * 100.
             * Checagem de spread_pct <= max_spread_pct.
             * Checagem de bid_qty >= min_bid_qty e ask_qty >= min_ask_qty.
        """
        if contract is None:
            contract = self.get_contract(snapshot.participant_id, snapshot.symbol)
            if contract is None:
                raise PmmContractNotFoundError(
                    f"Contrato não encontrado para participante '{snapshot.participant_id}' e ativo '{snapshot.symbol}'."
                )

        # Regra 1: Fases não contínuas são desconsideradas
        if snapshot.phase != MarketPhase.CONTINUOUS:
            return SnapshotAuditResult(
                snapshot=snapshot,
                status=PmmObligationStatus.AUCTION_EXCLUDED,
                is_valid_presence=False,
                is_eligible_phase=False,
                notes=f"Fase de mercado '{snapshot.phase.value}' excluída do cômputo da presença.",
            )

        # Fase contínua elegível
        is_eligible = True

        # Verifica janela de recomposição legítima após trade
        regeneration_active = False
        elapsed_regeneration: Optional[Decimal] = None
        if snapshot.last_trade_timestamp is not None:
            delta_sec = (snapshot.timestamp - snapshot.last_trade_timestamp).total_seconds()
            if delta_sec >= 0:
                elapsed_regeneration = Decimal(str(delta_sec))
                if delta_sec <= contract.max_regeneration_seconds:
                    regeneration_active = True

        has_bid = snapshot.bid_price is not None and snapshot.bid_qty is not None and snapshot.bid_price > Decimal("0")
        has_ask = snapshot.ask_price is not None and snapshot.ask_qty is not None and snapshot.ask_price > Decimal("0")

        # Casos com ausência de cotação
        if not has_bid and not has_ask:
            if regeneration_active:
                return SnapshotAuditResult(
                    snapshot=snapshot,
                    status=PmmObligationStatus.REGENERATING,
                    is_valid_presence=True,
                    is_eligible_phase=True,
                    regeneration_elapsed_seconds=elapsed_regeneration,
                    notes=f"Em recomposição legítima ({elapsed_regeneration}s <= {contract.max_regeneration_seconds}s).",
                )
            return SnapshotAuditResult(
                snapshot=snapshot,
                status=PmmObligationStatus.ABSENT,
                is_valid_presence=False,
                is_eligible_phase=True,
                regeneration_elapsed_seconds=elapsed_regeneration,
                notes="Ausência de ofertas de compra e venda sem justificativa de recomposição.",
            )

        if not has_bid:
            if regeneration_active:
                return SnapshotAuditResult(
                    snapshot=snapshot,
                    status=PmmObligationStatus.REGENERATING,
                    is_valid_presence=True,
                    is_eligible_phase=True,
                    regeneration_elapsed_seconds=elapsed_regeneration,
                    notes="Ponta compradora recompondo após trade.",
                )
            return SnapshotAuditResult(
                snapshot=snapshot,
                status=PmmObligationStatus.MISSING_BID,
                is_valid_presence=False,
                is_eligible_phase=True,
                notes="Oferta de compra ausente.",
            )

        if not has_ask:
            if regeneration_active:
                return SnapshotAuditResult(
                    snapshot=snapshot,
                    status=PmmObligationStatus.REGENERATING,
                    is_valid_presence=True,
                    is_eligible_phase=True,
                    regeneration_elapsed_seconds=elapsed_regeneration,
                    notes="Ponta vendedora recompondo após trade.",
                )
            return SnapshotAuditResult(
                snapshot=snapshot,
                status=PmmObligationStatus.MISSING_ASK,
                is_valid_presence=False,
                is_eligible_phase=True,
                notes="Oferta de venda ausente.",
            )

        # Ambas as pontas presentes: validação matemática
        assert snapshot.bid_price is not None and snapshot.ask_price is not None
        assert snapshot.bid_qty is not None and snapshot.ask_qty is not None

        if snapshot.ask_price <= snapshot.bid_price:
            # Livro invertido ou cruzado pelo formador (inconsistência crítica)
            return SnapshotAuditResult(
                snapshot=snapshot,
                status=PmmObligationStatus.SPREAD_VIOLATION,
                is_valid_presence=False,
                is_eligible_phase=True,
                spread_pct=Decimal("0.0"),
                spread_ticks=snapshot.ask_price - snapshot.bid_price,
                notes=f"Livro cruzado ou nulo (Ask={snapshot.ask_price} <= Bid={snapshot.bid_price}).",
            )

        spread_ticks = snapshot.ask_price - snapshot.bid_price
        spread_pct = ((spread_ticks) / snapshot.bid_price) * Decimal("100")

        # Checagem de spread
        spread_ok = spread_pct <= contract.max_spread_pct
        if contract.max_spread_ticks is not None and spread_ticks > contract.max_spread_ticks:
            spread_ok = False

        # Checagem de profundidade
        depth_ok = (snapshot.bid_qty >= contract.min_bid_qty) and (snapshot.ask_qty >= contract.min_ask_qty)

        # Se ambas as condições forem atendidas: pleno cumprimento
        if spread_ok and depth_ok:
            return SnapshotAuditResult(
                snapshot=snapshot,
                status=PmmObligationStatus.COMPLIANT,
                is_valid_presence=True,
                is_eligible_phase=True,
                spread_pct=spread_pct.quantize(Decimal("0.0001")),
                spread_ticks=spread_ticks,
                regeneration_elapsed_seconds=elapsed_regeneration,
                notes="Ofertas em plena conformidade regulatória com o contrato de PMM.",
            )

        # Se profundidade estiver abaixo do mínimo durante recomposição legítima, tolera
        if not depth_ok and regeneration_active and spread_ok:
            return SnapshotAuditResult(
                snapshot=snapshot,
                status=PmmObligationStatus.REGENERATING,
                is_valid_presence=True,
                is_eligible_phase=True,
                spread_pct=spread_pct.quantize(Decimal("0.0001")),
                spread_ticks=spread_ticks,
                regeneration_elapsed_seconds=elapsed_regeneration,
                notes=f"Recomposição de lote em andamento ({elapsed_regeneration}s <= {contract.max_regeneration_seconds}s).",
            )

        # Classificação de violações
        if not spread_ok and not depth_ok:
            status = PmmObligationStatus.SPREAD_AND_DEPTH_VIOLATION
            notes = (
                f"Violação simultânea: Spread {spread_pct:.4f}% > {contract.max_spread_pct}% "
                f"e Lotes (Bid={snapshot.bid_qty}, Ask={snapshot.ask_qty}) abaixo do mínimo."
            )
        elif not spread_ok:
            status = PmmObligationStatus.SPREAD_VIOLATION
            notes = f"Spread {spread_pct:.4f}% excedeu o limite máximo contratual de {contract.max_spread_pct}%."
        else:
            status = PmmObligationStatus.DEPTH_VIOLATION
            notes = (
                f"Profundidade insuficiente: Bid={snapshot.bid_qty} (min={contract.min_bid_qty}), "
                f"Ask={snapshot.ask_qty} (min={contract.min_ask_qty})."
            )

        return SnapshotAuditResult(
            snapshot=snapshot,
            status=status,
            is_valid_presence=False,
            is_eligible_phase=True,
            spread_pct=spread_pct.quantize(Decimal("0.0001")),
            spread_ticks=spread_ticks,
            regeneration_elapsed_seconds=elapsed_regeneration,
            notes=notes,
        )

    def audit_session(
        self,
        snapshots: Sequence[BookQuoteSnapshot],
        contract: Optional[PmmAssetContract] = None,
        session_date: Optional[str] = None,
    ) -> PmmSessionAuditResult:
        """Audita integralmente um pregão diário com base em seus snapshots."""
        if not snapshots:
            raise PmmValidationError("Lista de snapshots para auditoria de sessão não pode ser vazia.")

        first = snapshots[0]
        if contract is None:
            contract = self.get_contract(first.participant_id, first.symbol)
            if contract is None:
                raise PmmContractNotFoundError(
                    f"Contrato não encontrado para {first.participant_id} / {first.symbol}."
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

        audit_results: List[SnapshotAuditResult] = []

        for snp in snapshots:
            res = self.evaluate_snapshot(snp, contract)
            audit_results.append(res)
            duration = snp.duration_seconds
            total_session_seconds += duration

            if res.is_eligible_phase:
                eligible_continuous_seconds += duration

            if res.is_valid_presence:
                valid_presence_seconds += duration

            # Estatísticas de auditoria
            if res.status in (PmmObligationStatus.SPREAD_VIOLATION, PmmObligationStatus.SPREAD_AND_DEPTH_VIOLATION):
                spread_violation_count += 1
            if res.status in (PmmObligationStatus.DEPTH_VIOLATION, PmmObligationStatus.SPREAD_AND_DEPTH_VIOLATION):
                depth_violation_count += 1
            if res.status in (PmmObligationStatus.MISSING_BID, PmmObligationStatus.MISSING_ASK, PmmObligationStatus.ABSENT):
                missing_quote_count += 1
            if res.status == PmmObligationStatus.REGENERATING:
                regeneration_count += 1

        # Cálculo da taxa de presença
        if eligible_continuous_seconds > Decimal("0"):
            presence_pct = (valid_presence_seconds / eligible_continuous_seconds) * Decimal("100")
            presence_pct = presence_pct.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        else:
            presence_pct = Decimal("0.0000")

        # Critérios de conformidade diária
        failure_reasons: List[str] = []
        is_compliant = True

        if presence_pct < contract.min_presence_pct:
            is_compliant = False
            failure_reasons.append(
                f"Taxa de presença diária ({presence_pct}%) abaixo da meta contratual ({contract.min_presence_pct}%)."
            )

        if spread_violation_count > contract.max_daily_spread_violations:
            is_compliant = False
            failure_reasons.append(
                f"Violações de spread ({spread_violation_count}) excederam o teto tolerado ({contract.max_daily_spread_violations})."
            )

        return PmmSessionAuditResult(
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
            snapshot_results=audit_results,
            failure_reasons=failure_reasons,
        )

    def calculate_monthly_tariffs(
        self,
        session_results: Sequence[PmmSessionAuditResult],
        gross_trading_fees: Decimal,
        gross_clearing_fees: Decimal,
        contract: Optional[PmmAssetContract] = None,
        month_reference: Optional[str] = None,
        use_tiered_discount: bool = False,
    ) -> PmmMonthlyTariffReward:
        """Consolida os pregões mensais e calcula os descontos tarifários devidos pela B3."""
        if not session_results:
            raise PmmValidationError("Lista de sessões mensais não pode ser vazia.")

        first = session_results[0]
        if contract is None:
            contract = self.get_contract(first.participant_id, first.symbol)
            if contract is None:
                raise PmmContractNotFoundError(
                    f"Contrato não encontrado para {first.participant_id} / {first.symbol}."
                )

        if month_reference is None:
            month_reference = first.session_date[:7]

        gross_trading_fees = _to_decimal(gross_trading_fees)
        gross_clearing_fees = _to_decimal(gross_clearing_fees)
        if gross_trading_fees < Decimal("0") or gross_clearing_fees < Decimal("0"):
            raise PmmValidationError("Valores brutos de tarifas não podem ser negativos.")

        total_sessions = len(session_results)
        compliant_sessions = sum(1 for s in session_results if s.is_compliant)
        compliance_ratio_pct = (Decimal(compliant_sessions) / Decimal(total_sessions)) * Decimal("100")
        compliance_ratio_pct = compliance_ratio_pct.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        sum_presence = sum(s.presence_pct for s in session_results)
        average_presence_pct = (sum_presence / Decimal(total_sessions)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

        total_gross_fees = gross_trading_fees + gross_clearing_fees

        # Critério de elegibilidade
        is_reward_eligible = (
            compliance_ratio_pct >= contract.min_monthly_compliant_ratio
            and average_presence_pct >= contract.min_presence_pct
        )

        discount_pct_applied = Decimal("0.00")
        tier_description = "INELIGIBLE - Requisitos mensais de presença ou conformidade não atingidos."

        if is_reward_eligible:
            if use_tiered_discount:
                if average_presence_pct >= Decimal("95.00"):
                    discount_pct_applied = Decimal("100.00")
                    tier_description = "TIER_GOLD_100: Presença média >= 95.00% (Isenção Total 100%)"
                elif average_presence_pct >= Decimal("90.00"):
                    discount_pct_applied = Decimal("90.00")
                    tier_description = "TIER_SILVER_90: Presença média >= 90.00% (Desconto de 90%)"
                elif average_presence_pct >= Decimal("80.00"):
                    discount_pct_applied = Decimal("75.00")
                    tier_description = "TIER_BRONZE_75: Presença média >= 80.00% (Desconto de 75%)"
                else:
                    discount_pct_applied = contract.base_discount_pct
                    tier_description = f"TIER_STANDARD: Desconto contratual base de {contract.base_discount_pct}%"
            else:
                discount_pct_applied = contract.base_discount_pct
                tier_description = f"CONTRATUAL_PLENO: Desconto de {contract.base_discount_pct}% concedido."

        discount_amount = (total_gross_fees * (discount_pct_applied / Decimal("100"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        net_fees_payable = total_gross_fees - discount_amount

        return PmmMonthlyTariffReward(
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
            gross_trading_fees=gross_trading_fees,
            gross_clearing_fees=gross_clearing_fees,
            total_gross_fees=total_gross_fees,
            discount_amount=discount_amount,
            net_fees_payable=net_fees_payable,
            tier_description=tier_description,
        )

    def load_fixtures_and_audit(
        self, fixture_path: Union[str, Path]
    ) -> Tuple[List[PmmSessionAuditResult], List[PmmMonthlyTariffReward]]:
        """Carrega lote de contratos e pregões a partir de fixture JSON e executa auditoria."""
        path = Path(fixture_path)
        if not path.exists():
            raise FileNotFoundError(f"Fixture não encontrada: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 1. Registrar contratos
        contracts_data = data.get("contracts", [])
        for c_dict in contracts_data:
            c = PmmAssetContract(
                contract_id=c_dict["contract_id"],
                participant_id=c_dict["participant_id"],
                symbol=c_dict["symbol"],
                asset_class=AssetClass(c_dict["asset_class"]),
                min_presence_pct=Decimal(str(c_dict["min_presence_pct"])),
                max_spread_pct=Decimal(str(c_dict["max_spread_pct"])),
                min_bid_qty=Decimal(str(c_dict["min_bid_qty"])),
                min_ask_qty=Decimal(str(c_dict["min_ask_qty"])),
                max_regeneration_seconds=int(c_dict.get("max_regeneration_seconds", 60)),
                max_daily_spread_violations=int(c_dict.get("max_daily_spread_violations", 10)),
                base_discount_pct=Decimal(str(c_dict.get("base_discount_pct", "100.00"))),
                min_monthly_compliant_ratio=Decimal(str(c_dict.get("min_monthly_compliant_ratio", "80.00"))),
                max_spread_ticks=Decimal(str(c_dict["max_spread_ticks"])) if "max_spread_ticks" in c_dict else None,
            )
            self.register_contract(c)

        # 2. Auditar pregões
        sessions_data = data.get("sessions", [])
        session_results: List[PmmSessionAuditResult] = []
        sessions_by_key: Dict[Tuple[str, str], List[PmmSessionAuditResult]] = {}

        for s_dict in sessions_data:
            participant_id = s_dict["participant_id"]
            symbol = s_dict["symbol"]
            session_date = s_dict.get("session_date")

            snapshots: List[BookQuoteSnapshot] = []
            for q in s_dict["snapshots"]:
                snp = BookQuoteSnapshot(
                    timestamp=parse_datetime(q["timestamp"]),
                    symbol=symbol,
                    participant_id=participant_id,
                    bid_price=Decimal(str(q["bid_price"])) if q.get("bid_price") is not None else None,
                    bid_qty=Decimal(str(q["bid_qty"])) if q.get("bid_qty") is not None else None,
                    ask_price=Decimal(str(q["ask_price"])) if q.get("ask_price") is not None else None,
                    ask_qty=Decimal(str(q["ask_qty"])) if q.get("ask_qty") is not None else None,
                    phase=MarketPhase(q.get("phase", "CONTINUOUS")),
                    last_trade_timestamp=parse_datetime(q["last_trade_timestamp"]) if q.get("last_trade_timestamp") else None,
                    duration_seconds=Decimal(str(q.get("duration_seconds", "1.0"))),
                )
                snapshots.append(snp)

            res = self.audit_session(snapshots, session_date=session_date)
            session_results.append(res)
            key = (participant_id, symbol)
            sessions_by_key.setdefault(key, []).append(res)

        # 3. Apuração de recompensas tarifárias mensais
        monthly_rewards: List[PmmMonthlyTariffReward] = []
        monthly_bills = data.get("monthly_billings", [])
        for mb in monthly_bills:
            pid = mb["participant_id"]
            sym = mb["symbol"]
            month_ref = mb.get("month_reference")
            use_tier = mb.get("use_tiered_discount", False)
            key = (pid, sym)
            key_sessions = sessions_by_key.get(key, [])
            if key_sessions:
                rew = self.calculate_monthly_tariffs(
                    session_results=key_sessions,
                    gross_trading_fees=Decimal(str(mb["gross_trading_fees"])),
                    gross_clearing_fees=Decimal(str(mb["gross_clearing_fees"])),
                    month_reference=month_ref,
                    use_tiered_discount=use_tier,
                )
                monthly_rewards.append(rew)

        return session_results, monthly_rewards

    def export_audit_report(
        self,
        session_results: Sequence[PmmSessionAuditResult],
        monthly_rewards: Sequence[PmmMonthlyTariffReward],
    ) -> Dict[str, Any]:
        """Exporta os resultados da auditoria e apuração tarifária em formato estruturado."""
        return {
            "report_generated_at": datetime.now(timezone.utc).isoformat(),
            "sessions_count": len(session_results),
            "monthly_rewards_count": len(monthly_rewards),
            "sessions": [
                {
                    "session_date": s.session_date,
                    "symbol": s.symbol,
                    "participant_id": s.participant_id,
                    "total_session_seconds": str(s.total_session_seconds),
                    "eligible_continuous_seconds": str(s.eligible_continuous_seconds),
                    "valid_presence_seconds": str(s.valid_presence_seconds),
                    "presence_pct": str(s.presence_pct),
                    "required_presence_pct": str(s.required_presence_pct),
                    "spread_violation_count": s.spread_violation_count,
                    "depth_violation_count": s.depth_violation_count,
                    "missing_quote_count": s.missing_quote_count,
                    "regeneration_count": s.regeneration_count,
                    "is_compliant": s.is_compliant,
                    "failure_reasons": s.failure_reasons,
                }
                for s in session_results
            ],
            "monthly_rewards": [
                {
                    "month_reference": m.month_reference,
                    "participant_id": m.participant_id,
                    "symbol": m.symbol,
                    "total_sessions": m.total_sessions,
                    "compliant_sessions": m.compliant_sessions,
                    "compliance_ratio_pct": str(m.compliance_ratio_pct),
                    "average_presence_pct": str(m.average_presence_pct),
                    "min_required_presence_pct": str(m.min_required_presence_pct),
                    "is_reward_eligible": m.is_reward_eligible,
                    "discount_pct_applied": str(m.discount_pct_applied),
                    "gross_trading_fees": str(m.gross_trading_fees),
                    "gross_clearing_fees": str(m.gross_clearing_fees),
                    "total_gross_fees": str(m.total_gross_fees),
                    "discount_amount": str(m.discount_amount),
                    "net_fees_payable": str(m.net_fees_payable),
                    "tier_description": m.tier_description,
                }
                for m in monthly_rewards
            ],
        }
