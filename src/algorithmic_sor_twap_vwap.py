"""B3 PUMA Trading System — Smart Order Router (SOR) & Fatiamento Algorítmico TWAP/VWAP/POV (FL-104).
===================================================================================================

Motor determinístico de Roteamento Inteligente de Ordens (SOR) e fatiamento algorítmico de ordens
institucionais de grande porte (Block Orders / Institutional Parent Orders) para o ecossistema B3 PUMA.

Estratégias Algorítmicas Suportadas:
1. TWAP (Time-Weighted Average Price): Fatiamento uniforme em N janelas temporais com jitter
   estocástico anti-predação para ofuscação de footprints e mitigação de robôs HFT predadores.
2. VWAP (Volume-Weighted Average Price): Fatiamento dinâmico ponderado pelo perfil intradiário
   histórico de liquidez da B3 (curva clássica em "U" / U-shape volume curve).
3. POV (Percent-of-Volume / Participation Rate): Execução condicionada à taxa máxima de absorção
   e participação de mercado por janela de liquidez.

Guardrails de Proteção Operacional e Regulatória:
- Prevenção de Fat-Finger: Bloqueio estrito de ordens com valor nocional excessivo, quantidades anômalas,
  desvios desmedidos de preço ou fatias desproporcionais.
- Túneis de Negociação PUMA B3: Verificação de túneis de proteção operacional e de rejeição de preço.
- Limites de Slippage: Controle de desvio máximo de preço de execução em relação ao preço de chegada (Arrival Price).

Invariante Formal LAW-110:
- Conservação Estrita da Quantidade Total:
  Soma(Q_child_i) + Q_residual == Q_parent.
- Não-Transgressão de Preço Limite:
  Para ordens BUY:  P_child_i <= P_parent_limit.
  Para ordens SELL: P_child_i >= P_parent_limit.
- Discretização Estrita: Q_child_i > 0 e Q_child_i % lot_size == 0.

100% Python Standard Library (dataclasses, enum, decimal, typing, json, random, math, datetime).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING, InvalidOperation
from enum import Enum
import json
import math
from pathlib import Path
import random
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

# Importa mecanismo central de verificação de leis invariantes do repositório
try:
    from src.invariants import check, InvariantViolationError, law_110_algorithmic_order_slicing_conservation
except ImportError:
    # Fallback autônomo para compatibilidade e execução isolada
    class InvariantViolationError(AssertionError):  # type: ignore[no-redef]
        def __init__(self, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
            super().__init__(message)
            self.message = message
            self.law_code = law_code
            self.details = details or {}

    def check(condition: bool, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:  # type: ignore[no-redef]
        if not condition:
            raise InvariantViolationError(message, law_code=law_code, details=details)

    def law_110_algorithmic_order_slicing_conservation(  # type: ignore[no-redef]
        parent_quantity: Union[int, Decimal],
        child_quantities: Sequence[Union[int, Decimal]],
        residual_quantity: Union[int, Decimal] = 0,
        parent_side: str = "BUY",
        parent_limit_price: Optional[Union[Decimal, str, float]] = None,
        child_prices: Optional[Sequence[Union[Decimal, str, float]]] = None,
        parent_order_id: str = "N/A",
        lot_size: Optional[int] = None,
    ) -> None:
        total_child = sum(child_quantities)
        total_accounted = total_child + residual_quantity
        check(
            total_accounted == parent_quantity,
            f"Violação da Conservação Estrita de Quantidade (LAW-110) na ordem '{parent_order_id}': "
            f"Soma das fatias ({total_child}) + residual ({residual_quantity}) = {total_accounted} != "
            f"Quantidade da ordem-mãe ({parent_quantity}).",
            law_code="LAW-110",
        )


# ==============================================================================
# Funções Auxiliares de Precisão Decimal e Formatação
# ==============================================================================

def round_price(val: Union[Decimal, str, float, int], tick_size: Decimal = Decimal("0.01")) -> Decimal:
    """Arredonda o preço para o tick size especificado da B3 com precisão estrita."""
    d = Decimal(str(val))
    ticks = (d / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (ticks * tick_size).quantize(tick_size)


def to_decimal(val: Union[Decimal, str, float, int]) -> Decimal:
    """Converte valores numéricos heterogêneos para Decimal estrito."""
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val))


# ==============================================================================
# Exceções Específicas do Domínio Algorítmico SOR
# ==============================================================================

class AlgorithmicSORError(Exception):
    """Exceção base para erros operacionais do motor algorítmico SOR."""
    pass


class FatFingerViolationError(AlgorithmicSORError):
    """Lançada quando a ordem viola os parâmetros de segurança contra fat-finger."""
    pass


class PumaPriceTunnelViolationError(AlgorithmicSORError):
    """Lançada quando o preço da ordem ou fatia extrapola os túneis PUMA B3."""
    pass


class SlippageExceededError(AlgorithmicSORError):
    """Lançada quando o slippage projetado excede a tolerância máxima permitida."""
    pass


class InvariantConservationError(AlgorithmicSORError):
    """Lançada quando a conservação matemática LAW-110 é violada."""
    pass


# ==============================================================================
# Enumerações Canônicas de Mercado B3
# ==============================================================================

class OrderSide(str, Enum):
    """Sentido da ordem no mercado B3."""
    BUY = "BUY"
    SELL = "SELL"

    @classmethod
    def from_str(cls, value: Union[str, OrderSide]) -> OrderSide:
        if isinstance(value, cls):
            return value
        v = str(value).strip().upper()
        if v in ("BUY", "C", "COMPRA", "1"):
            return cls.BUY
        if v in ("SELL", "V", "VENDA", "2"):
            return cls.SELL
        raise ValueError(f"Sentido de ordem desconhecido: {value}")


class AlgorithmicStrategy(str, Enum):
    """Estratégias de fatiamento algorítmico suportadas."""
    TWAP = "TWAP"  # Time-Weighted Average Price
    VWAP = "VWAP"  # Volume-Weighted Average Price
    POV = "POV"    # Percent-of-Volume (Participation Rate)

    @classmethod
    def from_str(cls, value: Union[str, AlgorithmicStrategy]) -> AlgorithmicStrategy:
        if isinstance(value, cls):
            return value
        v = str(value).strip().upper()
        for member in cls:
            if member.value == v:
                return member
        raise ValueError(f"Estratégia algorítmica desconhecida: {value}")


class ExecutionVenue(str, Enum):
    """Ambientes e mecanismos de execução integrados pelo SOR."""
    PUMA_LIT = "PUMA_LIT"                      # Livro transparente central contínuo PUMA
    PUMA_DARK_MIDPOINT = "PUMA_DARK_MIDPOINT"  # Mecanismo midpoint institucional (sem footprint)
    PUMA_RLP = "PUMA_RLP"                      # Retail Liquidity Provider / Provedores autorizados
    PUMA_AUCTION = "PUMA_AUCTION"              # Leilão de fechamento / oscilação B3


class UrgencyLevel(str, Enum):
    """Grau de urgência na execução da ordem institucional."""
    LOW = "LOW"        # Prioriza preço e midpoint dark, minimiza impacto de mercado
    MEDIUM = "MEDIUM"  # Equilíbrio ótimo entre cadência temporal e preço
    HIGH = "HIGH"      # Prioriza velocidade de preenchimento e agressão de liquidez lit


class ChildOrderStatus(str, Enum):
    """Status de ciclo de vida da child order gerada."""
    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    ROUTED = "ROUTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


# ==============================================================================
# Perfil Histórico de Volume Padrão da B3 (U-Shape Curve)
# ==============================================================================

# Curva empírica normalizada da B3 (Mercado à Vista das 10h às 17h — 10 janelas de 42 min):
# Alto na abertura (leilões e ajustes), redução na hora do almoço, pico intenso no pré-fechamento e MOC.
DEFAULT_B3_INTRADAY_PROFILE: List[Decimal] = [
    Decimal("0.15"),  # 10:00 - 10:42 (Abertura, absorção de overnight e leilão)
    Decimal("0.13"),  # 10:42 - 11:24 (Abertura NYSE / fluxo de investidores estrangeiros)
    Decimal("0.09"),  # 11:24 - 12:06 (Fluxo contínuo matinal)
    Decimal("0.06"),  # 12:06 - 12:48 (Horário de almoço / liquidez reduzida)
    Decimal("0.05"),  # 12:48 - 13:30 (Mínimo intradiário de volume)
    Decimal("0.06"),  # 13:30 - 14:12 (Retomada gradual)
    Decimal("0.08"),  # 14:12 - 14:54 (Indicadores macroeconômicos e fluxo corporativo)
    Decimal("0.10"),  # 14:54 - 15:36 (Aceleração vespertina)
    Decimal("0.12"),  # 15:36 - 16:18 (Encerramento dos mercados europeus / posicionamento)
    Decimal("0.16"),  # 16:18 - 17:00 (Reta final e leilão de fechamento / Closing Cross MOC)
]


# ==============================================================================
# Classes de Guardrails de Segurança Operacional
# ==============================================================================

@dataclass
class FatFingerGuardrail:
    """Guardrail de proteção contra erros operacionais críticos de digitação (Fat-Finger)."""
    max_order_quantity: int = 1_000_000
    max_notional_value: Decimal = Decimal("50000000.00")  # R$ 50 milhões
    max_slice_pct: Decimal = Decimal("0.40")             # Nenhuma fatia pode exceder 40% da ordem
    max_price_deviation_pct: Decimal = Decimal("0.10")   # Desvio máximo de 10% vs referência

    def validate_parent_order(self, quantity: int, price: Decimal, reference_price: Decimal) -> None:
        """Valida a integridade da ordem-mãe institucional."""
        if quantity <= 0:
            raise FatFingerViolationError(f"Quantidade da ordem deve ser estritamente positiva: {quantity}")

        if quantity > self.max_order_quantity:
            raise FatFingerViolationError(
                f"Quantidade da ordem ({quantity:,}) excede o limite de fat-finger ({self.max_order_quantity:,})."
            )

        notional = Decimal(quantity) * price
        if notional > self.max_notional_value:
            raise FatFingerViolationError(
                f"Valor nocional da ordem (R$ {notional:,.2f}) excede o teto de fat-finger (R$ {self.max_notional_value:,.2f})."
            )

        if reference_price > Decimal("0"):
            deviation = (abs(price - reference_price) / reference_price)
            if deviation > self.max_price_deviation_pct:
                raise FatFingerViolationError(
                    f"Preço da ordem (R$ {price:.2f}) desvia {deviation * 100:.2f}% do preço de referência "
                    f"(R$ {reference_price:.2f}), excedendo a tolerância de {self.max_price_deviation_pct * 100:.1f}%."
                )

    def validate_child_slice(
        self,
        child_quantity: int,
        parent_quantity: int,
        slice_index: int,
        planned_windows: Optional[int] = None,
    ) -> None:
        """Valida se uma fatia individual não é desproporcionalmente grande."""
        if parent_quantity <= 0:
            raise FatFingerViolationError("Quantidade da ordem-mãe deve ser positiva.")
        ratio = Decimal(child_quantity) / Decimal(parent_quantity)
        effective_max = self.max_slice_pct
        if planned_windows is not None and planned_windows > 0:
            nominal_ratio = Decimal("1.0") / Decimal(planned_windows)
            # Permite margem de tolerância para jitter ou perfil de volume (até 40% acima da média)
            effective_max = max(self.max_slice_pct, nominal_ratio * Decimal("1.40"))

        if ratio > effective_max:
            raise FatFingerViolationError(
                f"Fatia {slice_index} ({child_quantity:,}) representa {ratio * 100:.1f}% da ordem-mãe, "
                f"ultrapassando o teto permitido de {effective_max * 100:.1f}%."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_order_quantity": self.max_order_quantity,
            "max_notional_value": str(self.max_notional_value),
            "max_slice_pct": str(self.max_slice_pct),
            "max_price_deviation_pct": str(self.max_price_deviation_pct),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FatFingerGuardrail:
        return cls(
            max_order_quantity=int(data.get("max_order_quantity", 1_000_000)),
            max_notional_value=to_decimal(data.get("max_notional_value", "50000000.00")),
            max_slice_pct=to_decimal(data.get("max_slice_pct", "0.40")),
            max_price_deviation_pct=to_decimal(data.get("max_price_deviation_pct", "0.10")),
        )


@dataclass
class PumaTunnelGuardrail:
    """Controle de Túneis de Negociação PUMA B3 (Túnel Operacional e de Rejeição)."""
    rejection_tunnel_pct: Decimal = Decimal("0.05")  # Rejeição automática se desvio > 5%
    auction_tunnel_pct: Decimal = Decimal("0.02")    # Disparo de leilão se desvio > 2%
    tick_size: Decimal = Decimal("0.01")

    def get_bounds(self, reference_price: Decimal) -> Tuple[Decimal, Decimal]:
        """Calcula os limites inferior e superior de preço permitidos pelo túnel de rejeição."""
        lower = round_price(reference_price * (Decimal("1.0") - self.rejection_tunnel_pct), self.tick_size)
        upper = round_price(reference_price * (Decimal("1.0") + self.rejection_tunnel_pct), self.tick_size)
        return lower, upper

    def validate_price(self, target_price: Decimal, reference_price: Decimal, context: str = "ordem") -> None:
        """Verifica se o preço se situa estritamente dentro do túnel de rejeição PUMA."""
        lower, upper = self.get_bounds(reference_price)
        if target_price < lower or target_price > upper:
            raise PumaPriceTunnelViolationError(
                f"Preço R$ {target_price:.2f} da {context} violou o Túnel PUMA B3! "
                f"Faixa aceitável: [R$ {lower:.2f}, R$ {upper:.2f}] (Ref: R$ {reference_price:.2f}, ±{self.rejection_tunnel_pct * 100:.1f}%)."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rejection_tunnel_pct": str(self.rejection_tunnel_pct),
            "auction_tunnel_pct": str(self.auction_tunnel_pct),
            "tick_size": str(self.tick_size),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PumaTunnelGuardrail:
        return cls(
            rejection_tunnel_pct=to_decimal(data.get("rejection_tunnel_pct", "0.05")),
            auction_tunnel_pct=to_decimal(data.get("auction_tunnel_pct", "0.02")),
            tick_size=to_decimal(data.get("tick_size", "0.01")),
        )


@dataclass
class SlippageGuardrail:
    """Controle de tolerância de Slippage em pontos-base (bps) vs Arrival Price."""
    max_slippage_bps: Decimal = Decimal("50.0")  # 50 bps = 0.50%

    def validate_slippage(self, execution_price: Decimal, arrival_price: Decimal, side: OrderSide) -> None:
        """Verifica se o preço viola a tolerância máxima de slippage contratada."""
        if arrival_price <= Decimal("0"):
            return

        if side == OrderSide.BUY:
            # Slippage positivo ocorre quando compramos mais caro que o arrival price
            slippage_bps = ((execution_price - arrival_price) / arrival_price) * Decimal("10000")
        else:
            # Slippage em venda ocorre quando vendemos mais barato que o arrival price
            slippage_bps = ((arrival_price - execution_price) / arrival_price) * Decimal("10000")

        if slippage_bps > self.max_slippage_bps:
            raise SlippageExceededError(
                f"Slippage de {slippage_bps:.2f} bps na ponta {side.value} ultrapassou o teto "
                f"permitido de {self.max_slippage_bps:.2f} bps. (Arrival: R$ {arrival_price:.2f}, Exec: R$ {execution_price:.2f})."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_slippage_bps": str(self.max_slippage_bps),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SlippageGuardrail:
        return cls(
            max_slippage_bps=to_decimal(data.get("max_slippage_bps", "50.0")),
        )


# ==============================================================================
# Modelos de Dados: Ordem-Mãe, Ordem-Filha e Plano de Execução
# ==============================================================================

@dataclass
class ParentOrder:
    """Ordem institucional de grande porte (Parent Order) submetida para execução algorítmica."""
    parent_id: str
    symbol: str
    side: OrderSide
    quantity: int
    arrival_price: Decimal
    strategy: AlgorithmicStrategy
    limit_price: Optional[Decimal] = None
    time_windows: int = 10
    start_time: str = "10:00:00"
    end_time: str = "17:00:00"
    lot_size: int = 100
    urgency: UrgencyLevel = UrgencyLevel.MEDIUM
    pov_participation_rate: Optional[Decimal] = None  # Requerido se strategy == POV
    jitter_range_pct: Optional[Decimal] = Decimal("0.15")  # Jitter anti-predatório para TWAP
    seed: Optional[int] = None  # Semente pseudo-aleatória determinística para testes e auditoria
    account_id: str = "BR_INST_001"

    def __post_init__(self) -> None:
        if isinstance(self.side, str):
            self.side = OrderSide.from_str(self.side)
        if isinstance(self.strategy, str):
            self.strategy = AlgorithmicStrategy.from_str(self.strategy)
        if isinstance(self.urgency, str):
            self.urgency = UrgencyLevel(self.urgency.upper())
        self.arrival_price = to_decimal(self.arrival_price)
        if self.limit_price is not None:
            self.limit_price = to_decimal(self.limit_price)
        if self.pov_participation_rate is not None:
            self.pov_participation_rate = to_decimal(self.pov_participation_rate)
        if self.jitter_range_pct is not None:
            self.jitter_range_pct = to_decimal(self.jitter_range_pct)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parent_id": self.parent_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "arrival_price": str(self.arrival_price),
            "limit_price": str(self.limit_price) if self.limit_price is not None else None,
            "strategy": self.strategy.value,
            "time_windows": self.time_windows,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "lot_size": self.lot_size,
            "urgency": self.urgency.value,
            "pov_participation_rate": str(self.pov_participation_rate) if self.pov_participation_rate is not None else None,
            "jitter_range_pct": str(self.jitter_range_pct) if self.jitter_range_pct is not None else None,
            "seed": self.seed,
            "account_id": self.account_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ParentOrder:
        return cls(
            parent_id=data["parent_id"],
            symbol=data["symbol"],
            side=OrderSide.from_str(data["side"]),
            quantity=int(data["quantity"]),
            arrival_price=to_decimal(data["arrival_price"]),
            limit_price=to_decimal(data["limit_price"]) if data.get("limit_price") is not None else None,
            strategy=AlgorithmicStrategy.from_str(data["strategy"]),
            time_windows=int(data.get("time_windows", 10)),
            start_time=data.get("start_time", "10:00:00"),
            end_time=data.get("end_time", "17:00:00"),
            lot_size=int(data.get("lot_size", 100)),
            urgency=UrgencyLevel(data.get("urgency", "MEDIUM").upper()),
            pov_participation_rate=to_decimal(data["pov_participation_rate"]) if data.get("pov_participation_rate") is not None else None,
            jitter_range_pct=to_decimal(data["jitter_range_pct"]) if data.get("jitter_range_pct") is not None else None,
            seed=int(data["seed"]) if data.get("seed") is not None else None,
            account_id=data.get("account_id", "BR_INST_001"),
        )


@dataclass
class ChildOrder:
    """Fatia filha (Child Order) gerada pelo fatiador algorítmico e roteada pelo SOR."""
    child_id: str
    parent_id: str
    slice_index: int
    symbol: str
    side: OrderSide
    quantity: int
    target_price: Decimal
    scheduled_time: str
    time_offset_seconds: int
    target_venue: ExecutionVenue
    status: ChildOrderStatus = ChildOrderStatus.SCHEDULED
    strategy_metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.side, str):
            self.side = OrderSide.from_str(self.side)
        if isinstance(self.target_venue, str):
            self.target_venue = ExecutionVenue(self.target_venue)
        if isinstance(self.status, str):
            self.status = ChildOrderStatus(self.status)
        self.target_price = to_decimal(self.target_price)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "child_id": self.child_id,
            "parent_id": self.parent_id,
            "slice_index": self.slice_index,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "target_price": str(self.target_price),
            "scheduled_time": self.scheduled_time,
            "time_offset_seconds": self.time_offset_seconds,
            "target_venue": self.target_venue.value,
            "status": self.status.value,
            "strategy_metadata": self.strategy_metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ChildOrder:
        return cls(
            child_id=data["child_id"],
            parent_id=data["parent_id"],
            slice_index=int(data["slice_index"]),
            symbol=data["symbol"],
            side=OrderSide.from_str(data["side"]),
            quantity=int(data["quantity"]),
            target_price=to_decimal(data["target_price"]),
            scheduled_time=data["scheduled_time"],
            time_offset_seconds=int(data["time_offset_seconds"]),
            target_venue=ExecutionVenue(data["target_venue"]),
            status=ChildOrderStatus(data.get("status", "SCHEDULED")),
            strategy_metadata=data.get("strategy_metadata", {}),
        )


@dataclass
class SlicingPlan:
    """Plano consolidado de fatiamento algorítmico e roteamento SOR."""
    parent_order: ParentOrder
    child_orders: List[ChildOrder]
    total_sliced_quantity: int
    residual_unallocated_quantity: int
    expected_benchmark_price: Decimal
    guardrails_passed: bool
    audit_log: List[str] = field(default_factory=list)
    profile_weights_used: Optional[List[str]] = None

    def verify_invariants(self, raise_on_error: bool = True) -> Dict[str, bool]:
        """Verifica formalmente todas as regras da invariante LAW-110 e guardrails PUMA."""
        child_qtys = [c.quantity for c in self.child_orders]
        child_prices = [c.target_price for c in self.child_orders]

        # 1. Lei Fundamental de Conservação LAW-110
        conservation_ok = (self.total_sliced_quantity + self.residual_unallocated_quantity) == self.parent_order.quantity
        conservation_sum_ok = sum(child_qtys) == self.total_sliced_quantity

        # 2. Não-negatividade e fatias estritamente positivas
        non_negative_ok = all(q > 0 for q in child_qtys) if child_qtys else (self.parent_order.quantity == 0)

        # 3. Discretização de lote padrão (Round Lot)
        lot_size = self.parent_order.lot_size
        lot_conformance_ok = all(q % lot_size == 0 for q in child_qtys) if lot_size > 0 else True

        # 4. Não-transgressão de Preço Limite
        price_limit_ok = True
        if self.parent_order.limit_price is not None:
            p_lim = self.parent_order.limit_price
            if self.parent_order.side == OrderSide.BUY:
                price_limit_ok = all(p <= p_lim for p in child_prices)
            else:
                price_limit_ok = all(p >= p_lim for p in child_prices)

        # 5. Avaliação global
        all_ok = (
            conservation_ok
            and conservation_sum_ok
            and non_negative_ok
            and lot_conformance_ok
            and price_limit_ok
            and self.guardrails_passed
        )

        invariants_map = {
            "law_110_quantity_conservation": conservation_ok and conservation_sum_ok,
            "law_110_non_negative_slices": non_negative_ok,
            "law_110_lot_size_conformance": lot_conformance_ok,
            "law_110_price_limit_respected": price_limit_ok,
            "guardrails_integrity_passed": self.guardrails_passed,
            "all_invariants_hold": all_ok,
        }

        if raise_on_error and not all_ok:
            # Invoca verificação formal centralizada para lançar InvariantViolationError canônica
            try:
                law_110_algorithmic_order_slicing_conservation(
                    parent_quantity=self.parent_order.quantity,
                    child_quantities=child_qtys,
                    residual_quantity=self.residual_unallocated_quantity,
                    parent_side=self.parent_order.side.value,
                    parent_limit_price=self.parent_order.limit_price,
                    child_prices=child_prices,
                    parent_order_id=self.parent_order.parent_id,
                    lot_size=self.parent_order.lot_size,
                )
            except InvariantViolationError:
                raise
            # Se a invariante de quant repassou mas outro guardrail falhou
            raise InvariantConservationError(
                f"Falha na validação das invariantes do plano de fatiamento: {invariants_map}"
            )

        return invariants_map

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parent_order": self.parent_order.to_dict(),
            "child_orders": [c.to_dict() for c in self.child_orders],
            "total_sliced_quantity": self.total_sliced_quantity,
            "residual_unallocated_quantity": self.residual_unallocated_quantity,
            "expected_benchmark_price": str(self.expected_benchmark_price),
            "guardrails_passed": self.guardrails_passed,
            "audit_log": list(self.audit_log),
            "profile_weights_used": self.profile_weights_used,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SlicingPlan:
        return cls(
            parent_order=ParentOrder.from_dict(data["parent_order"]),
            child_orders=[ChildOrder.from_dict(c) for c in data["child_orders"]],
            total_sliced_quantity=int(data["total_sliced_quantity"]),
            residual_unallocated_quantity=int(data["residual_unallocated_quantity"]),
            expected_benchmark_price=to_decimal(data["expected_benchmark_price"]),
            guardrails_passed=bool(data["guardrails_passed"]),
            audit_log=list(data.get("audit_log", [])),
            profile_weights_used=data.get("profile_weights_used"),
        )


# ==============================================================================
# Motor Central AlgorithmicSOREngine (FL-104)
# ==============================================================================

class AlgorithmicSOREngine:
    """Motor de Smart Order Router e Fatiamento Institucional PUMA B3 (FL-104)."""

    def __init__(
        self,
        fat_finger: Optional[FatFingerGuardrail] = None,
        price_tunnel: Optional[PumaTunnelGuardrail] = None,
        slippage: Optional[SlippageGuardrail] = None,
        intraday_volume_profile: Optional[Sequence[Decimal]] = None,
    ) -> None:
        self.fat_finger = fat_finger or FatFingerGuardrail()
        self.price_tunnel = price_tunnel or PumaTunnelGuardrail()
        self.slippage = slippage or SlippageGuardrail()
        self.intraday_profile = list(intraday_volume_profile or DEFAULT_B3_INTRADAY_PROFILE)

    # --------------------------------------------------------------------------
    # 1. Algoritmos de Fatiamento (TWAP, VWAP, POV)
    # --------------------------------------------------------------------------

    def generate_twap_slices(
        self,
        parent: ParentOrder,
        reference_price: Decimal,
        market_depth: Optional[Dict[ExecutionVenue, int]] = None,
    ) -> SlicingPlan:
        """Gera plano de fatiamento uniforme no tempo (TWAP) com jitter estocástico anti-predação.

        Aplica perturbações pseudo-aleatórias controladas no tamanho dos lotes e no agendamento temporal,
        compensando desvios para que a soma das fatias seja exatamente igual à quantidade da ordem-mãe.
        """
        audit: List[str] = []
        audit.append(f"Iniciando fatiamento TWAP para ordem {parent.parent_id}: {parent.quantity:,} cotas em {parent.time_windows} janelas.")

        # Validações de guardrails na ordem-mãe
        eval_price = parent.limit_price or reference_price
        self.fat_finger.validate_parent_order(parent.quantity, eval_price, reference_price)
        self.price_tunnel.validate_price(eval_price, reference_price, context="Parent Order")

        N = parent.time_windows
        lot_size = parent.lot_size
        total_lots = parent.quantity // lot_size

        if total_lots < N:
            # Caso a quantidade de lotes seja inferior ao número de janelas, ajusta N para total_lots
            N = max(1, total_lots)
            audit.append(f"Ajustando janelas TWAP de {parent.time_windows} para {N} para respeitar o lote padrão de {lot_size}.")

        rng = random.Random(parent.seed) if parent.seed is not None else random.Random(42)

        # Alocação base em lotes por janela
        base_lots_per_window = total_lots // N
        remainder_lots = total_lots % N

        lots_allocation = [base_lots_per_window] * N
        for i in range(remainder_lots):
            lots_allocation[i] += 1

        # Aplicação de Jitter Anti-Predação em lotes se configurado
        jitter_pct = parent.jitter_range_pct or Decimal("0.0")
        if jitter_pct > Decimal("0") and N > 1 and total_lots >= N * 2:
            max_delta_lots = max(1, int(Decimal(base_lots_per_window) * jitter_pct))
            for i in range(N - 1):
                # Desvio limitado de sorte que a fatia mantenha ao menos 1 lote
                available_down = lots_allocation[i] - 1
                if available_down > 0:
                    delta = rng.randint(-min(available_down, max_delta_lots), max_delta_lots)
                    # Verifica se o próximo slice tem capacidade de absorver o oposto
                    if lots_allocation[i + 1] - delta >= 1:
                        lots_allocation[i] += delta
                        lots_allocation[i + 1] -= delta
            audit.append(f"Jitter estocástico aplicado com amplitude máxima de {max_delta_lots} lotes.")

        # Cálculo de tempo e offsets
        time_seconds_step = self._calculate_time_step_seconds(parent.start_time, parent.end_time, N)

        child_orders: List[ChildOrder] = []
        for i in range(N):
            qty = lots_allocation[i] * lot_size
            self.fat_finger.validate_child_slice(qty, parent.quantity, i, planned_windows=N)

            # Jitter temporal estocástico (em segundos)
            jitter_sec = 0
            if jitter_pct > Decimal("0") and time_seconds_step > 10:
                max_time_jitter = min(int(time_seconds_step * float(jitter_pct)), time_seconds_step // 3)
                jitter_sec = rng.randint(-max_time_jitter, max_time_jitter)

            offset = max(0, int(i * time_seconds_step + jitter_sec))
            sched_time = self._format_offset_time(parent.start_time, offset)

            # Preço alvo da fatia e roteamento SOR
            slice_price, venue = self._route_slice_price_and_venue(
                parent=parent,
                reference_price=reference_price,
                slice_index=i,
                total_slices=N,
                market_depth=market_depth,
            )

            # Validações dos guardrails na fatia
            self.price_tunnel.validate_price(slice_price, reference_price, context=f"Fatia TWAP {i}")
            self.slippage.validate_slippage(slice_price, parent.arrival_price, parent.side)

            child = ChildOrder(
                child_id=f"{parent.parent_id}_TWAP_{i + 1:02d}",
                parent_id=parent.parent_id,
                slice_index=i + 1,
                symbol=parent.symbol,
                side=parent.side,
                quantity=qty,
                target_price=slice_price,
                scheduled_time=sched_time,
                time_offset_seconds=offset,
                target_venue=venue,
                strategy_metadata={
                    "strategy": "TWAP",
                    "window_index": i + 1,
                    "lots": lots_allocation[i],
                    "time_jitter_seconds": jitter_sec,
                },
            )
            child_orders.append(child)

        total_sliced = sum(c.quantity for c in child_orders)
        plan = SlicingPlan(
            parent_order=parent,
            child_orders=child_orders,
            total_sliced_quantity=total_sliced,
            residual_unallocated_quantity=parent.quantity - total_sliced,
            expected_benchmark_price=reference_price,
            guardrails_passed=True,
            audit_log=audit,
        )
        plan.verify_invariants(raise_on_error=True)
        return plan

    def generate_vwap_slices(
        self,
        parent: ParentOrder,
        reference_price: Decimal,
        custom_volume_profile: Optional[Sequence[Decimal]] = None,
        market_depth: Optional[Dict[ExecutionVenue, int]] = None,
    ) -> SlicingPlan:
        """Gera plano de fatiamento ponderado pelo perfil histórico de volume da B3 (VWAP).

        Utiliza o algoritmo de Hare-Niemeyer (Largest Remainder) para converter cotas contínuas
        em múltiplos exatos de lotes padrão B3, garantindo estritamente a conservação LAW-110.
        """
        audit: List[str] = []
        audit.append(f"Iniciando fatiamento VWAP para ordem {parent.parent_id}: {parent.quantity:,} cotas.")

        eval_price = parent.limit_price or reference_price
        self.fat_finger.validate_parent_order(parent.quantity, eval_price, reference_price)
        self.price_tunnel.validate_price(eval_price, reference_price, context="Parent Order")

        profile = list(custom_volume_profile or self.intraday_profile)
        # Normalização dos pesos históricos para soma rigorosamente igual a 1.0
        total_weight = sum(profile)
        if total_weight <= Decimal("0"):
            raise AlgorithmicSORError("Soma dos pesos do perfil de volume deve ser estritamente positiva.")
        normalized_weights = [w / total_weight for w in profile]

        N = len(normalized_weights)
        lot_size = parent.lot_size
        total_lots = parent.quantity // lot_size

        if total_lots < N:
            raise AlgorithmicSORError(
                f"Quantidade total ({parent.quantity:,}) insuficiente para alocar {N} janelas VWAP "
                f"com lote mínimo de {lot_size}."
            )

        # Algoritmo Hare-Niemeyer (Largest Remainder Method)
        exact_lots = [Decimal(total_lots) * w for w in normalized_weights]
        integer_lots = [int(math.floor(q)) for q in exact_lots]
        remainders = [(exact_lots[i] - Decimal(integer_lots[i]), i) for i in range(N)]

        unallocated_lots = total_lots - sum(integer_lots)
        remainders.sort(key=lambda x: x[0], reverse=True)

        for k in range(unallocated_lots):
            idx = remainders[k][1]
            integer_lots[idx] += 1

        # Garantia de que nenhuma janela VWAP fique com zero lotes
        for i in range(N):
            if integer_lots[i] == 0:
                # Transfere 1 lote da janela com maior alocação
                max_idx = max(range(N), key=lambda idx: integer_lots[idx])
                integer_lots[max_idx] -= 1
                integer_lots[i] += 1

        time_seconds_step = self._calculate_time_step_seconds(parent.start_time, parent.end_time, N)

        child_orders: List[ChildOrder] = []
        weighted_price_sum = Decimal("0")
        total_volume_sum = Decimal("0")

        for i in range(N):
            qty = integer_lots[i] * lot_size
            self.fat_finger.validate_child_slice(qty, parent.quantity, i, planned_windows=N)

            offset = int(i * time_seconds_step)
            sched_time = self._format_offset_time(parent.start_time, offset)

            slice_price, venue = self._route_slice_price_and_venue(
                parent=parent,
                reference_price=reference_price,
                slice_index=i,
                total_slices=N,
                market_depth=market_depth,
            )

            self.price_tunnel.validate_price(slice_price, reference_price, context=f"Fatia VWAP {i}")
            self.slippage.validate_slippage(slice_price, parent.arrival_price, parent.side)

            weighted_price_sum += slice_price * Decimal(qty)
            total_volume_sum += Decimal(qty)

            child = ChildOrder(
                child_id=f"{parent.parent_id}_VWAP_{i + 1:02d}",
                parent_id=parent.parent_id,
                slice_index=i + 1,
                symbol=parent.symbol,
                side=parent.side,
                quantity=qty,
                target_price=slice_price,
                scheduled_time=sched_time,
                time_offset_seconds=offset,
                target_venue=venue,
                strategy_metadata={
                    "strategy": "VWAP",
                    "window_index": i + 1,
                    "target_weight": str(normalized_weights[i]),
                    "lots": integer_lots[i],
                },
            )
            child_orders.append(child)

        expected_vwap = (weighted_price_sum / total_volume_sum).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_sliced = sum(c.quantity for c in child_orders)

        plan = SlicingPlan(
            parent_order=parent,
            child_orders=child_orders,
            total_sliced_quantity=total_sliced,
            residual_unallocated_quantity=parent.quantity - total_sliced,
            expected_benchmark_price=expected_vwap,
            guardrails_passed=True,
            audit_log=audit,
            profile_weights_used=[str(w) for w in normalized_weights],
        )
        plan.verify_invariants(raise_on_error=True)
        return plan

    def generate_pov_slices(
        self,
        parent: ParentOrder,
        reference_price: Decimal,
        projected_market_volumes: Sequence[int],
        market_depth: Optional[Dict[ExecutionVenue, int]] = None,
    ) -> SlicingPlan:
        """Gera plano de fatiamento com limite de participação de mercado por janela (POV / Participation Rate).

        Restringe estritamente a participação a no máximo `pov_participation_rate` do volume de mercado em cada janela.
        Se a ordem-mãe exceder a capacidade de mercado das janelas fornecidas, o residual remanescente
        é auditado e registrado em `residual_unallocated_quantity`, mantendo a invariante LAW-110.
        """
        audit: List[str] = []
        if parent.pov_participation_rate is None or parent.pov_participation_rate <= Decimal("0"):
            raise AlgorithmicSORError("Estratégia POV exige parâmetro 'pov_participation_rate' estritamente positivo.")

        alpha = parent.pov_participation_rate
        audit.append(f"Iniciando fatiamento POV com taxa de participação máxima de {alpha * 100:.1f}%.")

        eval_price = parent.limit_price or reference_price
        self.fat_finger.validate_parent_order(parent.quantity, eval_price, reference_price)
        self.price_tunnel.validate_price(eval_price, reference_price, context="Parent Order")

        N = len(projected_market_volumes)
        lot_size = parent.lot_size
        time_seconds_step = self._calculate_time_step_seconds(parent.start_time, parent.end_time, N)

        # Capacidade máxima de absorção em lotes por janela: floor(alpha * V_mkt / lot_size)
        window_caps_in_lots = [
            int(math.floor((Decimal(v) * alpha) / Decimal(lot_size))) for v in projected_market_volumes
        ]
        total_capacity_lots = sum(window_caps_in_lots)
        target_lots = parent.quantity // lot_size

        allocated_lots: List[int] = [0] * N
        residual_lots = 0

        if target_lots <= total_capacity_lots:
            # A ordem cabe inteiramente na capacidade disponível:
            # Distribui proporcionalmente ao volume de mercado respeitando o teto de cada janela
            if sum(projected_market_volumes) > 0:
                exact_lots = [
                    (Decimal(target_lots) * Decimal(v)) / Decimal(sum(projected_market_volumes))
                    for v in projected_market_volumes
                ]
                base_lots = [min(window_caps_in_lots[i], int(math.floor(exact_lots[i]))) for i in range(N)]
                rem_to_assign = target_lots - sum(base_lots)

                rem_list = [(exact_lots[i] - Decimal(base_lots[i]), i) for i in range(N)]
                rem_list.sort(key=lambda x: x[0], reverse=True)

                for _, idx in rem_list:
                    if rem_to_assign <= 0:
                        break
                    if base_lots[idx] < window_caps_in_lots[idx]:
                        base_lots[idx] += 1
                        rem_to_assign -= 1

                allocated_lots = base_lots
            residual_lots = 0
        else:
            # Ordem excede a capacidade de mercado sob a taxa alfa estipulada
            allocated_lots = list(window_caps_in_lots)
            residual_lots = target_lots - sum(allocated_lots)
            audit.append(
                f"Ordem excedeu a capacidade de mercado das {N} janelas ({total_capacity_lots * lot_size:,} cotas). "
                f"Residual de {residual_lots * lot_size:,} cotas não alocado para prevenir impacto excessivo."
            )

        child_orders: List[ChildOrder] = []
        slice_counter = 1
        for i in range(N):
            lots = allocated_lots[i]
            if lots <= 0:
                continue
            qty = lots * lot_size
            self.fat_finger.validate_child_slice(qty, parent.quantity, slice_counter, planned_windows=N)

            offset = int(i * time_seconds_step)
            sched_time = self._format_offset_time(parent.start_time, offset)

            slice_price, venue = self._route_slice_price_and_venue(
                parent=parent,
                reference_price=reference_price,
                slice_index=i,
                total_slices=N,
                market_depth=market_depth,
            )

            self.price_tunnel.validate_price(slice_price, reference_price, context=f"Fatia POV {slice_counter}")
            self.slippage.validate_slippage(slice_price, parent.arrival_price, parent.side)

            actual_participation = (Decimal(qty) / Decimal(projected_market_volumes[i])) if projected_market_volumes[i] > 0 else Decimal("0")

            child = ChildOrder(
                child_id=f"{parent.parent_id}_POV_{slice_counter:02d}",
                parent_id=parent.parent_id,
                slice_index=slice_counter,
                symbol=parent.symbol,
                side=parent.side,
                quantity=qty,
                target_price=slice_price,
                scheduled_time=sched_time,
                time_offset_seconds=offset,
                target_venue=venue,
                strategy_metadata={
                    "strategy": "POV",
                    "window_index": i + 1,
                    "market_volume_window": projected_market_volumes[i],
                    "target_pov_cap": str(alpha),
                    "actual_participation": f"{actual_participation * 100:.2f}%",
                },
            )
            child_orders.append(child)
            slice_counter += 1

        total_sliced = sum(c.quantity for c in child_orders)
        residual_qty = residual_lots * lot_size

        plan = SlicingPlan(
            parent_order=parent,
            child_orders=child_orders,
            total_sliced_quantity=total_sliced,
            residual_unallocated_quantity=residual_qty,
            expected_benchmark_price=reference_price,
            guardrails_passed=True,
            audit_log=audit,
        )
        plan.verify_invariants(raise_on_error=True)
        return plan

    # --------------------------------------------------------------------------
    # 2. Roteamento Inteligente (Smart Order Router - SOR)
    # --------------------------------------------------------------------------

    def _route_slice_price_and_venue(
        self,
        parent: ParentOrder,
        reference_price: Decimal,
        slice_index: int,
        total_slices: int,
        market_depth: Optional[Dict[ExecutionVenue, int]] = None,
    ) -> Tuple[Decimal, ExecutionVenue]:
        """Calcula o preço-alvo ótimo e seleciona o venue de execução B3 com base na urgência e profundidade."""
        tick = self.price_tunnel.tick_size

        # Decisão de Venue conforme Urgência e Janela Temporal
        venue = ExecutionVenue.PUMA_LIT
        if parent.urgency == UrgencyLevel.LOW:
            # Baixa urgência: prioriza cruzamento dark midpoint para economizar spread e taxas
            if market_depth and market_depth.get(ExecutionVenue.PUMA_DARK_MIDPOINT, 0) > 0:
                venue = ExecutionVenue.PUMA_DARK_MIDPOINT
            else:
                venue = ExecutionVenue.PUMA_LIT
        elif parent.urgency == UrgencyLevel.MEDIUM:
            # Média urgência: se for a última fatia perto do encerramento, usa PUMA_AUCTION
            if slice_index == total_slices - 1 and parent.end_time.startswith("17"):
                venue = ExecutionVenue.PUMA_AUCTION
            elif market_depth and market_depth.get(ExecutionVenue.PUMA_RLP, 0) > 0 and parent.quantity <= 50_000:
                venue = ExecutionVenue.PUMA_RLP
            else:
                venue = ExecutionVenue.PUMA_LIT
        else:  # HIGH
            # Alta urgência: agressão direta ao livro lit central para liquidez imediata
            venue = ExecutionVenue.PUMA_LIT

        # Determinação do preço da fatia
        target_p = reference_price
        if parent.side == OrderSide.BUY:
            if parent.urgency == UrgencyLevel.HIGH:
                target_p = reference_price + tick
            elif parent.urgency == UrgencyLevel.LOW:
                target_p = reference_price - tick

            # Respeito estrito ao preço limite da ordem-mãe (LAW-110)
            if parent.limit_price is not None:
                target_p = min(target_p, parent.limit_price)
        else:  # SELL
            if parent.urgency == UrgencyLevel.HIGH:
                target_p = reference_price - tick
            elif parent.urgency == UrgencyLevel.LOW:
                target_p = reference_price + tick

            # Respeito estrito ao preço limite da ordem-mãe (LAW-110)
            if parent.limit_price is not None:
                target_p = max(target_p, parent.limit_price)

        target_p = round_price(target_p, tick)
        return target_p, venue

    # --------------------------------------------------------------------------
    # 3. Métodos Auxiliares de Cronometria e Formatação
    # --------------------------------------------------------------------------

    def _calculate_time_step_seconds(self, start_time: str, end_time: str, windows: int) -> int:
        """Calcula o intervalo em segundos entre janelas temporais."""
        fmt = "%H:%M:%S"
        t_start = datetime.strptime(start_time, fmt)
        t_end = datetime.strptime(end_time, fmt)
        total_seconds = int((t_end - t_start).total_seconds())
        if total_seconds <= 0 or windows <= 0:
            return 60
        return max(1, total_seconds // windows)

    def _format_offset_time(self, base_time: str, offset_seconds: int) -> str:
        """Soma um offset em segundos ao horário base e retorna string HH:MM:SS."""
        fmt = "%H:%M:%S"
        t = datetime.strptime(base_time, fmt)
        t_new = t + timedelta(seconds=offset_seconds)
        return t_new.strftime(fmt)

    # --------------------------------------------------------------------------
    # 4. Serialização e Persistência de Fixtures
    # --------------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fat_finger": self.fat_finger.to_dict(),
            "price_tunnel": self.price_tunnel.to_dict(),
            "slippage": self.slippage.to_dict(),
            "intraday_profile": [str(w) for w in self.intraday_profile],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AlgorithmicSOREngine:
        return cls(
            fat_finger=FatFingerGuardrail.from_dict(data["fat_finger"]) if "fat_finger" in data else None,
            price_tunnel=PumaTunnelGuardrail.from_dict(data["price_tunnel"]) if "price_tunnel" in data else None,
            slippage=SlippageGuardrail.from_dict(data["slippage"]) if "slippage" in data else None,
            intraday_volume_profile=[to_decimal(w) for w in data["intraday_profile"]] if "intraday_profile" in data else None,
        )

    @classmethod
    def load_from_fixture(cls, path: Union[str, Path]) -> Tuple[AlgorithmicSOREngine, ParentOrder, Decimal]:
        """Carrega configuração canônica a partir de arquivo JSON de fixture."""
        p = Path(path)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        engine = cls.from_dict(data["engine_config"])
        parent = ParentOrder.from_dict(data["parent_order"])
        ref_price = to_decimal(data["reference_price"])
        return engine, parent, ref_price
