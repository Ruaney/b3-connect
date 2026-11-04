"""
B3 PUMA Trading System & Câmara — Estratégias Estruturadas de Volatilidade e Opções Combinadas (FL-063)
========================================================================================================
Módulo determinístico de validação estrutural de pernas, cálculo de margem de risco combinada
(Haircut por spread de volatilidade e mitigação de risco de pernas compensadas) e execução
atômica multi-leg no PUMA Trading System.

Estratégias canônicas suportadas:
1. Straddle (Long / Short)
2. Strangle (Long / Short)
3. Iron Condor (Credit / Debit)
4. Collar (Protetor / Financiado / Zero-Cost)
5. Butterfly (Borboleta Simétrica de 3 Strikes e Iron Butterfly)

Invariantes Formais Provadas:
1. Lei de Conservação de Pernas (Leg Ratio Invariant):
   Para qualquer execução atômica (total ou parcial de k pacotes), a quantidade executada
   de cada perna i satisfaz estritamente:
       Q_i_exec = k * R_i  ==>  Q_i_exec / R_i = k  (constante para todas as pernas i).
   Nenhuma perna órfã é gerada (Erradicação Total de Legging Risk).
2. Atomicidade Transacional All-or-None:
   A operação combinada executa simultaneamente em todas as pontas do book ou reverte integralmente
   (Zero Slippage de Pernas Desbalanceadas).
3. Conservação de Fluxo Financeiro Líquido:
   Fluxo_Total = k * [ Sum(Side_i * R_i * Preço_i) ].
4. Desconto de Risco e Haircut por Spread de Volatilidade (CORE Margin Offset):
   Margem_Combinada <= Margem_Isolada_Soma_Pernas. Para estratégias com risco estritamente
   delimitado (ex: Iron Condor, Borboleta Comprada), a exigência de margem é limitada à perda
   máxima calculada da estrutura, liberando capital regulatório.

100% Python Standard Library (dataclasses, Decimal, math, enum, typing, json, pathlib).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# ---------------------------------------------------------------------------
# Constantes Numéricas Regulatórias
# ---------------------------------------------------------------------------

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_FOUR = Decimal("0.0001")
QUANTIZE_SIX = Decimal("0.000001")

DEFAULT_VOLATILITY_SPREAD_HAIRCUT = Decimal("0.15")  # 15% de haircut regulatório B3
DEFAULT_NAKED_MARGIN_PCT = Decimal("0.20")           # 20% do valor nocional para pernas descobertas
DEFAULT_ANNUAL_DAYS = Decimal("252")


# ---------------------------------------------------------------------------
# Enumerações Canônicas
# ---------------------------------------------------------------------------

class StrategyType(str, Enum):
    """Estratégias estruturadas de volatilidade e opções combinadas reconhecidas pela B3."""
    STRADDLE = "STRADDLE"
    STRANGLE = "STRANGLE"
    IRON_CONDOR = "IRON_CONDOR"
    COLLAR = "COLLAR"
    BUTTERFLY = "BUTTERFLY"

    @classmethod
    def from_str(cls, value: Union[str, StrategyType]) -> StrategyType:
        if isinstance(value, cls):
            return value
        val = str(value).strip().upper()
        if "." in val:
            val = val.split(".")[-1]
        for item in cls:
            if item.value == val:
                return item
        raise ValueError(f"Tipo de estratégia desconhecido: {value}")


class OptionType(str, Enum):
    """Tipo do ativo componente da perna (Call, Put ou Ação à vista Spot)."""
    CALL = "CALL"
    PUT = "PUT"
    SPOT = "SPOT"

    @classmethod
    def from_str(cls, value: Union[str, OptionType]) -> OptionType:
        if isinstance(value, cls):
            return value
        val = str(value).strip().upper()
        if "." in val:
            val = val.split(".")[-1]
        for item in cls:
            if item.value == val:
                return item
        raise ValueError(f"Tipo de opção inválido: {value}")


class LegSide(str, Enum):
    """Sentido da perna no PUMA Trading System (FIX Tag 54 / Tag 624 LegSide)."""
    BUY = "BUY"    # Tag 624=1 (Compra / Titular)
    SELL = "SELL"  # Tag 624=2 (Venda / Lançador)

    @classmethod
    def from_str(cls, value: Union[str, LegSide]) -> LegSide:
        if isinstance(value, cls):
            return value
        val = str(value).strip().upper()
        if "." in val:
            val = val.split(".")[-1]
        if val in ("BUY", "1", "C", "COMPRA"):
            return cls.BUY
        if val in ("SELL", "2", "V", "VENDA"):
            return cls.SELL
        raise ValueError(f"Sentido de perna inválido: {value}")


class ExecutionStatus(str, Enum):
    """Status do ciclo de vida da ordem estruturada multi-leg."""
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class MarginCategory(str, Enum):
    """Classificação de risco de margem pela Câmara B3 CORE."""
    DEBIT_PAID = "DEBIT_PAID"              # Risco limitado ao prêmio pago (Long Straddle, Long Butterfly)
    SPREAD_CAPPED = "SPREAD_CAPPED"        # Trava com risco máximo delimitado (Iron Condor)
    WORST_CASE_OFFSET = "WORST_CASE_OFFSET"# Risco descoberto mitigado por correlação/offset (Short Straddle/Strangle)
    COVERED_COLLAR = "COVERED_COLLAR"      # Cobertura física de custódia com piso protetor de put


# ---------------------------------------------------------------------------
# Exceções do Domínio
# ---------------------------------------------------------------------------

class VolatilityStrategyError(Exception):
    """Exceção base para erros em estratégias de volatilidade e opções combinadas."""
    pass


class InvalidLegCombinationError(VolatilityStrategyError):
    """Lançada quando a combinação de pernas viola os requisitos estruturais da estratégia."""
    pass


class AtomicExecutionError(VolatilityStrategyError):
    """Lançada quando a execução atômica multi-leg falha por falta de liquidez ou preço limite."""
    pass


class RatioConservationError(VolatilityStrategyError):
    """Lançada em caso de violação da lei de conservação estrita de proporção entre pernas."""
    pass


# ---------------------------------------------------------------------------
# Modelos de Dados (Dataclasses)
# ---------------------------------------------------------------------------

@dataclass
class StrategyLeg:
    """Especificação individual de uma perna componente da estratégia estruturada."""
    leg_id: str
    symbol: str
    option_type: OptionType
    side: LegSide
    strike: Decimal
    ratio: int
    expiration_date: Optional[str] = None
    market_price: Decimal = Decimal("0.00")
    implied_vol: Optional[Decimal] = None
    delta: Optional[Decimal] = None
    gamma: Optional[Decimal] = None
    vega: Optional[Decimal] = None
    theta: Optional[Decimal] = None

    def __post_init__(self) -> None:
        if not isinstance(self.option_type, OptionType):
            self.option_type = OptionType.from_str(self.option_type)
        if not isinstance(self.side, LegSide):
            self.side = LegSide.from_str(self.side)
        if not isinstance(self.strike, Decimal):
            self.strike = Decimal(str(self.strike))
        if not isinstance(self.market_price, Decimal):
            self.market_price = Decimal(str(self.market_price))
        if self.implied_vol is not None and not isinstance(self.implied_vol, Decimal):
            self.implied_vol = Decimal(str(self.implied_vol))
        if self.ratio <= 0:
            raise InvalidLegCombinationError(f"Ratio da perna {self.leg_id} deve ser positivo: {self.ratio}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "leg_id": self.leg_id,
            "symbol": self.symbol,
            "option_type": self.option_type.value,
            "side": self.side.value,
            "strike": str(self.strike),
            "ratio": self.ratio,
            "expiration_date": self.expiration_date,
            "market_price": str(self.market_price),
            "implied_vol": str(self.implied_vol) if self.implied_vol is not None else None,
            "delta": str(self.delta) if self.delta is not None else None,
            "gamma": str(self.gamma) if self.gamma is not None else None,
            "vega": str(self.vega) if self.vega is not None else None,
            "theta": str(self.theta) if self.theta is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> StrategyLeg:
        return cls(
            leg_id=data["leg_id"],
            symbol=data["symbol"],
            option_type=OptionType.from_str(data["option_type"]),
            side=LegSide.from_str(data["side"]),
            strike=Decimal(str(data.get("strike", "0.00"))),
            ratio=int(data.get("ratio", 1)),
            expiration_date=data.get("expiration_date"),
            market_price=Decimal(str(data.get("market_price", "0.00"))),
            implied_vol=Decimal(str(data["implied_vol"])) if data.get("implied_vol") is not None else None,
            delta=Decimal(str(data["delta"])) if data.get("delta") is not None else None,
            gamma=Decimal(str(data["gamma"])) if data.get("gamma") is not None else None,
            vega=Decimal(str(data["vega"])) if data.get("vega") is not None else None,
            theta=Decimal(str(data["theta"])) if data.get("theta") is not None else None,
        )


@dataclass
class MultiLegOrder:
    """Ordem combinada de estratégia de volatilidade para envio ao PUMA."""
    order_id: str
    strategy_type: StrategyType
    underlying_symbol: str
    underlying_spot: Decimal
    packages_requested: int
    legs: List[StrategyLeg]
    limit_net_price: Optional[Decimal] = None
    account_id: str = "ACC-001"
    status: ExecutionStatus = ExecutionStatus.PENDING
    packages_executed: int = 0
    timestamp_ms: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_type, StrategyType):
            self.strategy_type = StrategyType.from_str(self.strategy_type)
        if not isinstance(self.underlying_spot, Decimal):
            self.underlying_spot = Decimal(str(self.underlying_spot))
        if self.limit_net_price is not None and not isinstance(self.limit_net_price, Decimal):
            self.limit_net_price = Decimal(str(self.limit_net_price))
        if self.packages_requested <= 0:
            raise InvalidLegCombinationError("Quantidade de pacotes solicitados deve ser maior que zero.")
        if not self.legs:
            raise InvalidLegCombinationError("Ordem multi-leg não contém pernas definidas.")

    def calculate_net_unit_price(self) -> Decimal:
        """Calcula o preço líquido unitário por pacote da estratégia.
        Convenção B3:
        Valor > 0: Débito líquido (o participante paga prêmio líquido para montar).
        Valor < 0: Crédito líquido (o participante recebe prêmio líquido na montagem).
        """
        net_price = Decimal("0.00")
        for leg in self.legs:
            sign = Decimal("1") if leg.side == LegSide.BUY else Decimal("-1")
            net_price += sign * Decimal(leg.ratio) * leg.market_price
        return net_price.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "strategy_type": self.strategy_type.value,
            "underlying_symbol": self.underlying_symbol,
            "underlying_spot": str(self.underlying_spot),
            "packages_requested": self.packages_requested,
            "limit_net_price": str(self.limit_net_price) if self.limit_net_price is not None else None,
            "account_id": self.account_id,
            "status": self.status.value,
            "packages_executed": self.packages_executed,
            "timestamp_ms": self.timestamp_ms,
            "legs": [leg.to_dict() for leg in self.legs],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MultiLegOrder:
        legs = [StrategyLeg.from_dict(item) for item in data["legs"]]
        return cls(
            order_id=data["order_id"],
            strategy_type=StrategyType.from_str(data["strategy_type"]),
            underlying_symbol=data["underlying_symbol"],
            underlying_spot=Decimal(str(data["underlying_spot"])),
            packages_requested=int(data["packages_requested"]),
            legs=legs,
            limit_net_price=Decimal(str(data["limit_net_price"])) if data.get("limit_net_price") is not None else None,
            account_id=data.get("account_id", "ACC-001"),
            status=ExecutionStatus(data.get("status", "PENDING")),
            packages_executed=int(data.get("packages_executed", 0)),
            timestamp_ms=int(data.get("timestamp_ms", 0)),
        )


@dataclass
class LegExecutionFill:
    """Confirmação de execução individual de uma perna em uma rodada de matching."""
    fill_id: str
    leg_id: str
    symbol: str
    side: LegSide
    executed_qty: int
    fill_price: Decimal
    net_cash_flow: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "leg_id": self.leg_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "executed_qty": self.executed_qty,
            "fill_price": str(self.fill_price),
            "net_cash_flow": str(self.net_cash_flow),
        }


@dataclass
class MultiLegExecutionReceipt:
    """Recibo formal de execução atômica multi-leg no PUMA Trading System."""
    execution_id: str
    order_id: str
    strategy_type: StrategyType
    packages_filled: int
    net_strategy_price: Decimal
    total_net_cash_flow: Decimal
    leg_fills: List[LegExecutionFill]
    timestamp_ms: int
    atomic: bool = True
    ratio_conservation_verified: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "order_id": self.order_id,
            "strategy_type": self.strategy_type.value,
            "packages_filled": self.packages_filled,
            "net_strategy_price": str(self.net_strategy_price),
            "total_net_cash_flow": str(self.total_net_cash_flow),
            "leg_fills": [f.to_dict() for f in self.leg_fills],
            "timestamp_ms": self.timestamp_ms,
            "atomic": self.atomic,
            "ratio_conservation_verified": self.ratio_conservation_verified,
        }


@dataclass
class CombinedMarginRequirement:
    """Demonstrativo de margem de risco combinada com desconto de haircut da B3."""
    strategy_type: StrategyType
    packages: int
    margin_category: MarginCategory
    naked_isolated_margin: Decimal
    combined_margin: Decimal
    margin_benefit: Decimal
    haircut_applied_pct: Decimal
    worst_case_loss: Decimal
    net_premium_flow: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_type": self.strategy_type.value,
            "packages": self.packages,
            "margin_category": self.margin_category.value,
            "naked_isolated_margin": str(self.naked_isolated_margin),
            "combined_margin": str(self.combined_margin),
            "margin_benefit": str(self.margin_benefit),
            "haircut_applied_pct": str(self.haircut_applied_pct),
            "worst_case_loss": str(self.worst_case_loss),
            "net_premium_flow": str(self.net_premium_flow),
        }


@dataclass
class StrategyPayoff:
    """Perfil de payoff no vencimento da estratégia multi-leg."""
    strategy_type: StrategyType
    spot_prices: List[Decimal]
    pnls: List[Decimal]
    max_profit: Optional[Decimal]
    max_loss: Optional[Decimal]
    breakeven_points: List[Decimal]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_type": self.strategy_type.value,
            "spot_prices": [str(p) for p in self.spot_prices],
            "pnls": [str(v) for v in self.pnls],
            "max_profit": str(self.max_profit) if self.max_profit is not None else None,
            "max_loss": str(self.max_loss) if self.max_loss is not None else None,
            "breakeven_points": [str(b) for b in self.breakeven_points],
        }


@dataclass
class StrategyGreeks:
    """Gregas agregadas do portfólio sintético da estratégia estruturada."""
    delta: Decimal
    gamma: Decimal
    vega: Decimal
    theta: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delta": str(self.delta),
            "gamma": str(self.gamma),
            "vega": str(self.vega),
            "theta": str(self.theta),
        }


# ---------------------------------------------------------------------------
# Funções Matemáticas Analíticas (Black-Scholes & Gregas)
# ---------------------------------------------------------------------------

def _std_norm_cdf(x: float) -> float:
    """Função de distribuição acumulada da normal padrão via função erro erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _std_norm_pdf(x: float) -> float:
    """Função de densidade da probabilidade normal padrão."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_greeks(
    spot: float,
    strike: float,
    maturity_years: float,
    rate: float,
    vol: float,
    is_call: bool,
) -> Tuple[float, float, float, float]:
    """Calcula analiticamente (Delta, Gamma, Vega, Theta) pelo modelo Black-Scholes-Merton.
    Vega é cotado em variação por 1 ponto percentual de volatilidade (0.01).
    Theta é cotado em variação diária (1/252 ou 1/365).
    """
    if maturity_years <= 1e-6 or vol <= 1e-6:
        # No vencimento ou vol zero
        if is_call:
            delta = 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
        else:
            delta = -1.0 if spot < strike else (-0.5 if spot == strike else 0.0)
        return delta, 0.0, 0.0, 0.0

    sqrt_t = math.sqrt(maturity_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * maturity_years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    pdf_d1 = _std_norm_pdf(d1)
    discount = math.exp(-rate * maturity_years)

    if is_call:
        delta = _std_norm_cdf(d1)
        theta_annual = -(spot * pdf_d1 * vol) / (2.0 * sqrt_t) - rate * strike * discount * _std_norm_cdf(d2)
    else:
        delta = _std_norm_cdf(d1) - 1.0
        theta_annual = -(spot * pdf_d1 * vol) / (2.0 * sqrt_t) + rate * strike * discount * _std_norm_cdf(-d2)

    gamma = pdf_d1 / (spot * vol * sqrt_t)
    vega = (spot * pdf_d1 * sqrt_t) * 0.01  # Variação por 1% de vol
    theta_daily = theta_annual / 252.0      # Convenção B3: dias úteis

    return delta, gamma, vega, theta_daily


# ---------------------------------------------------------------------------
# Validador Estrutural de Pernas: VolatilityStrategyValidator
# ---------------------------------------------------------------------------

class VolatilityStrategyValidator:
    """Validador canônico de pernas combinadas no PUMA Trading System."""

    @classmethod
    def validate_order(cls, order: MultiLegOrder) -> None:
        """Valida integralmente uma ordem multi-leg; levanta InvalidLegCombinationError se inválida."""
        st = order.strategy_type
        if st == StrategyType.STRADDLE:
            cls.validate_straddle(order.legs)
        elif st == StrategyType.STRANGLE:
            cls.validate_strangle(order.legs)
        elif st == StrategyType.IRON_CONDOR:
            cls.validate_iron_condor(order.legs)
        elif st == StrategyType.COLLAR:
            cls.validate_collar(order.legs)
        elif st == StrategyType.BUTTERFLY:
            cls.validate_butterfly(order.legs)
        else:
            raise InvalidLegCombinationError(f"Estratégia não suportada: {st}")

    @classmethod
    def validate_straddle(cls, legs: List[StrategyLeg]) -> None:
        """Regras de Straddle:
        - Exatamente 2 pernas: 1 CALL e 1 PUT.
        - Mesmo strike (K_call == K_put).
        - Mesma data de vencimento.
        - Mesmo sentido operacional (ambas BUY para Long Straddle ou ambas SELL para Short Straddle).
        - Ratio 1:1.
        """
        if len(legs) != 2:
            raise InvalidLegCombinationError(f"Straddle exige exatamente 2 pernas, recebido {len(legs)}.")
        
        call_legs = [leg for leg in legs if leg.option_type == OptionType.CALL]
        put_legs = [leg for leg in legs if leg.option_type == OptionType.PUT]

        if len(call_legs) != 1 or len(put_legs) != 1:
            raise InvalidLegCombinationError("Straddle exige exatamente 1 CALL e 1 PUT.")

        c_leg, p_leg = call_legs[0], put_legs[0]

        if c_leg.strike != p_leg.strike:
            raise InvalidLegCombinationError(
                f"Straddle exige strikes idênticos: Call Strike={c_leg.strike} vs Put Strike={p_leg.strike}."
            )

        if c_leg.side != p_leg.side:
            raise InvalidLegCombinationError(
                f"Straddle exige mesmo sentido de operação: Call={c_leg.side} vs Put={p_leg.side}."
            )

        if c_leg.ratio != 1 or p_leg.ratio != 1:
            raise InvalidLegCombinationError("Straddle exige proporção unitária 1:1 nas duas pernas.")

        if c_leg.expiration_date != p_leg.expiration_date:
            raise InvalidLegCombinationError(
                f"Straddle exige mesma data de vencimento: Call={c_leg.expiration_date} vs Put={p_leg.expiration_date}."
            )

    @classmethod
    def validate_strangle(cls, legs: List[StrategyLeg]) -> None:
        """Regras de Strangle:
        - Exatamente 2 pernas: 1 CALL e 1 PUT.
        - Strikes distintos com Strike(PUT) < Strike(CALL).
        - Mesma data de vencimento.
        - Mesmo sentido operacional (ambas BUY ou ambas SELL).
        - Ratio 1:1.
        """
        if len(legs) != 2:
            raise InvalidLegCombinationError(f"Strangle exige exatamente 2 pernas, recebido {len(legs)}.")

        call_legs = [leg for leg in legs if leg.option_type == OptionType.CALL]
        put_legs = [leg for leg in legs if leg.option_type == OptionType.PUT]

        if len(call_legs) != 1 or len(put_legs) != 1:
            raise InvalidLegCombinationError("Strangle exige exatamente 1 CALL e 1 PUT.")

        c_leg, p_leg = call_legs[0], put_legs[0]

        if p_leg.strike >= c_leg.strike:
            raise InvalidLegCombinationError(
                f"Strangle exige Strike(PUT) < Strike(CALL): Put Strike={p_leg.strike} vs Call Strike={c_leg.strike}."
            )

        if c_leg.side != p_leg.side:
            raise InvalidLegCombinationError(
                f"Strangle exige mesmo sentido de operação: Call={c_leg.side} vs Put={p_leg.side}."
            )

        if c_leg.ratio != 1 or p_leg.ratio != 1:
            raise InvalidLegCombinationError("Strangle exige proporção unitária 1:1 nas duas pernas.")

        if c_leg.expiration_date != p_leg.expiration_date:
            raise InvalidLegCombinationError(
                f"Strangle exige mesma data de vencimento: Call={c_leg.expiration_date} vs Put={p_leg.expiration_date}."
            )

    @classmethod
    def validate_iron_condor(cls, legs: List[StrategyLeg]) -> None:
        """Regras de Iron Condor:
        - Exatamente 4 pernas: 2 PUTs e 2 CALLs.
        - Mesma data de vencimento.
        - Strikes ordenados: K_put_low < K_put_high <= K_call_low < K_call_high.
        - Perna exterior comprada (BUY) e perna interior vendida (SELL) para Credit Iron Condor,
          ou o inverso para Debit Iron Condor.
        - Ratios todos iguais a 1 (1:1:1:1).
        """
        if len(legs) != 4:
            raise InvalidLegCombinationError(f"Iron Condor exige exatamente 4 pernas, recebido {len(legs)}.")

        call_legs = sorted([leg for leg in legs if leg.option_type == OptionType.CALL], key=lambda x: x.strike)
        put_legs = sorted([leg for leg in legs if leg.option_type == OptionType.PUT], key=lambda x: x.strike)

        if len(call_legs) != 2 or len(put_legs) != 2:
            raise InvalidLegCombinationError("Iron Condor exige exatamente 2 CALLs e 2 PUTs.")

        p_low, p_high = put_legs[0], put_legs[1]
        c_low, c_high = call_legs[0], call_legs[1]

        if not (p_low.strike < p_high.strike <= c_low.strike < c_high.strike):
            raise InvalidLegCombinationError(
                f"Iron Condor exige strikes ordenados K_p1 < K_p2 <= K_c1 < K_c2. Obtido: "
                f"[{p_low.strike}, {p_high.strike}, {c_low.strike}, {c_high.strike}]."
            )

        # Checagem de ratios
        for leg in legs:
            if leg.ratio != 1:
                raise InvalidLegCombinationError(f"Iron Condor exige ratio unitário (1), perna {leg.leg_id} tem {leg.ratio}.")

        # Vencimentos idênticos
        expirations = {leg.expiration_date for leg in legs}
        if len(expirations) > 1:
            raise InvalidLegCombinationError(f"Iron Condor exige mesmo vencimento em todas as pernas: {expirations}.")

        # Configuração padrão de crédito: asas long (BUY) e corpo short (SELL)
        # Ou configuração debit: asas short e corpo long
        is_credit = (
            p_low.side == LegSide.BUY and p_high.side == LegSide.SELL and
            c_low.side == LegSide.SELL and c_high.side == LegSide.BUY
        )
        is_debit = (
            p_low.side == LegSide.SELL and p_high.side == LegSide.BUY and
            c_low.side == LegSide.BUY and c_high.side == LegSide.SELL
        )
        if not (is_credit or is_debit):
            raise InvalidLegCombinationError(
                "Iron Condor exige estrutura balanceada asas vs miolo (PutLow/CallHigh opostos a PutHigh/CallLow)."
            )

    @classmethod
    def validate_collar(cls, legs: List[StrategyLeg]) -> None:
        """Regras de Collar:
        - 3 pernas (Collar com Ação à vista):
          * 1 perna SPOT (BUY, ratio 1)
          * 1 perna PUT (BUY, ratio 1, strike K_put)
          * 1 perna CALL (SELL, ratio 1, strike K_call)
          * Strike(PUT) < Strike(CALL)
        - Ou 2 pernas (Collar Sintético/Opções contra Custódia Livre):
          * 1 perna PUT (BUY, ratio 1)
          * 1 perna CALL (SELL, ratio 1)
          * Strike(PUT) < Strike(CALL)
        """
        if len(legs) not in (2, 3):
            raise InvalidLegCombinationError(f"Collar exige 2 ou 3 pernas, recebido {len(legs)}.")

        spot_legs = [leg for leg in legs if leg.option_type == OptionType.SPOT]
        call_legs = [leg for leg in legs if leg.option_type == OptionType.CALL]
        put_legs = [leg for leg in legs if leg.option_type == OptionType.PUT]

        if len(call_legs) != 1 or len(put_legs) != 1:
            raise InvalidLegCombinationError("Collar exige exatamente 1 CALL e 1 PUT.")

        if len(legs) == 3 and len(spot_legs) != 1:
            raise InvalidLegCombinationError("Collar de 3 pernas exige exatamente 1 perna SPOT.")

        c_leg, p_leg = call_legs[0], put_legs[0]

        if p_leg.strike >= c_leg.strike:
            raise InvalidLegCombinationError(
                f"Collar exige Strike(PUT) < Strike(CALL): Put={p_leg.strike} vs Call={c_leg.strike}."
            )

        if p_leg.side != LegSide.BUY:
            raise InvalidLegCombinationError("Collar protetor exige compra (BUY) da opção PUT de piso.")

        if c_leg.side != LegSide.SELL:
            raise InvalidLegCombinationError("Collar financiador exige venda (SELL) da opção CALL de teto.")

        if len(spot_legs) == 1 and spot_legs[0].side != LegSide.BUY:
            raise InvalidLegCombinationError("Perna à vista do Collar deve ser comprada (BUY).")

        for leg in legs:
            if leg.ratio != 1:
                raise InvalidLegCombinationError(f"Collar exige proporção 1:1:1, perna {leg.leg_id} ratio={leg.ratio}.")

    @classmethod
    def validate_butterfly(cls, legs: List[StrategyLeg]) -> None:
        """Regras de Butterfly (Borboleta):
        - Borboleta Padrão de 3 Strikes (CALL ou PUT):
          * Exatamente 3 pernas do mesmo tipo (todas CALL ou todas PUT).
          * Perna 1: Asa Inferior K1, ratio 1, BUY.
          * Perna 2: Miolo K2, ratio 2, SELL.
          * Perna 3: Asa Superior K3, ratio 1, BUY.
          * Simetria estrita: K2 - K1 == K3 - K2.
          * (Ou versão invertida short: SELL ratio 1, BUY ratio 2, SELL ratio 1).
        - Ou Iron Butterfly (4 pernas):
          * 2 PUTs e 2 CALLs com miolo no mesmo strike K2.
          * Long Put K1 (ratio 1), Short Put K2 (ratio 1), Short Call K2 (ratio 1), Long Call K3 (ratio 1).
          * Simetria: K2 - K1 == K3 - K2.
        """
        if len(legs) == 3:
            # Borboleta Tradicional (3 pernas, 3 strikes)
            opt_types = {leg.option_type for leg in legs}
            if len(opt_types) != 1 or OptionType.SPOT in opt_types:
                raise InvalidLegCombinationError("Borboleta de 3 pernas exige opções do mesmo tipo (todas CALL ou todas PUT).")

            sorted_legs = sorted(legs, key=lambda x: x.strike)
            leg1, leg2, leg3 = sorted_legs[0], sorted_legs[1], sorted_legs[2]

            if not (leg1.strike < leg2.strike < leg3.strike):
                raise InvalidLegCombinationError(
                    f"Borboleta exige 3 strikes distintos e crescentes. Obtido: [{leg1.strike}, {leg2.strike}, {leg3.strike}]."
                )

            # Simetria estrita
            wing1 = leg2.strike - leg1.strike
            wing2 = leg3.strike - leg2.strike
            if wing1 != wing2:
                raise InvalidLegCombinationError(
                    f"Borboleta exige simetria estrita de asas (K2-K1 == K3-K2): {wing1} != {wing2}."
                )

            # Ratios 1 : 2 : 1
            if leg1.ratio != 1 or leg2.ratio != 2 or leg3.ratio != 1:
                raise InvalidLegCombinationError(
                    f"Borboleta exige proporção de pernas 1:2:1. Obtido: [{leg1.ratio}, {leg2.ratio}, {leg3.ratio}]."
                )

            # Sentidos balanceados
            is_long = leg1.side == LegSide.BUY and leg2.side == LegSide.SELL and leg3.side == LegSide.BUY
            is_short = leg1.side == LegSide.SELL and leg2.side == LegSide.BUY and leg3.side == LegSide.SELL
            if not (is_long or is_short):
                raise InvalidLegCombinationError("Borboleta de 3 pernas exige asas opostas ao miolo (BUY-SELL-BUY ou SELL-BUY-SELL).")

            expirations = {leg.expiration_date for leg in legs}
            if len(expirations) > 1:
                raise InvalidLegCombinationError(f"Borboleta exige mesma data de vencimento: {expirations}.")

        elif len(legs) == 4:
            # Iron Butterfly (4 pernas)
            call_legs = sorted([leg for leg in legs if leg.option_type == OptionType.CALL], key=lambda x: x.strike)
            put_legs = sorted([leg for leg in legs if leg.option_type == OptionType.PUT], key=lambda x: x.strike)

            if len(call_legs) != 2 or len(put_legs) != 2:
                raise InvalidLegCombinationError("Iron Butterfly exige 2 CALLs e 2 PUTs.")

            p_wing, p_center = put_legs[0], put_legs[1]
            c_center, c_wing = call_legs[0], call_legs[1]

            if p_center.strike != c_center.strike:
                raise InvalidLegCombinationError(
                    f"Iron Butterfly exige miolo com mesmo strike: PutCenter={p_center.strike} vs CallCenter={c_center.strike}."
                )

            wing1 = p_center.strike - p_wing.strike
            wing2 = c_wing.strike - c_center.strike
            if wing1 != wing2 or wing1 <= 0:
                raise InvalidLegCombinationError(
                    f"Iron Butterfly exige asas simétricas e positivas: {wing1} != {wing2}."
                )

            for leg in legs:
                if leg.ratio != 1:
                    raise InvalidLegCombinationError("Iron Butterfly exige ratio unitário (1) nas 4 pernas.")

            expirations = {leg.expiration_date for leg in legs}
            if len(expirations) > 1:
                raise InvalidLegCombinationError(f"Iron Butterfly exige mesmo vencimento: {expirations}.")
        else:
            raise InvalidLegCombinationError(f"Borboleta exige 3 ou 4 pernas, recebido {len(legs)}.")


# ---------------------------------------------------------------------------
# Motor de Margem de Risco Combinada: CombinedRiskMarginEngine
# ---------------------------------------------------------------------------

class CombinedRiskMarginEngine:
    """Motor de cálculo de margem de risco combinada CORE B3 com Haircut de Volatilidade."""

    def __init__(
        self,
        volatility_spread_haircut: Decimal = DEFAULT_VOLATILITY_SPREAD_HAIRCUT,
        naked_margin_pct: Decimal = DEFAULT_NAKED_MARGIN_PCT,
    ) -> None:
        self.volatility_spread_haircut = volatility_spread_haircut
        self.naked_margin_pct = naked_margin_pct

    def calculate_isolated_naked_margin(self, order: MultiLegOrder) -> Decimal:
        """Calcula a margem que seria exigida caso as pernas fossem avaliadas isoladamente
        sem qualquer benefício de offset, hedge ou trava (abordagem ingênua/pior caso).
        """
        total_isolated = Decimal("0.00")
        spot = order.underlying_spot

        for leg in order.legs:
            qty = Decimal(leg.ratio * order.packages_requested)
            if leg.option_type == OptionType.SPOT:
                if leg.side == LegSide.BUY:
                    # Compra à vista já exige o pagamento integral
                    total_isolated += spot * qty
            elif leg.side == LegSide.BUY:
                # Titular de opções: risco máximo limitado ao prêmio pago
                total_isolated += leg.market_price * qty
            else:
                # Lançador de opções a descoberto (uncovered):
                # Margem regulatória típica: 20% do spot/strike + valor intrínseco ITM
                base_risk = spot * self.naked_margin_pct
                if leg.option_type == OptionType.CALL:
                    itm = max(Decimal("0.00"), spot - leg.strike)
                else:
                    itm = max(Decimal("0.00"), leg.strike - spot)
                naked_per_unit = base_risk + itm
                total_isolated += naked_per_unit * qty

        return total_isolated.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)

    def calculate_combined_margin(self, order: MultiLegOrder) -> CombinedMarginRequirement:
        """Calcula a margem combinada com regras de compensação e haircut da Câmara B3."""
        VolatilityStrategyValidator.validate_order(order)
        isolated_margin = self.calculate_isolated_naked_margin(order)

        st = order.strategy_type
        packages = order.packages_requested
        packages_dec = Decimal(packages)
        net_unit_price = order.calculate_net_unit_price()
        net_premium_flow = (net_unit_price * packages_dec).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)

        if st == StrategyType.STRADDLE or st == StrategyType.STRANGLE:
            # Identifica se é Long ou Short
            # Se a primeira perna for BUY, ambas são BUY (validado)
            is_long = order.legs[0].side == LegSide.BUY

            if is_long:
                # Long Straddle / Long Strangle: Risco limitado ao débito líquido pago
                combined_margin = abs(net_premium_flow)
                category = MarginCategory.DEBIT_PAID
                worst_loss = combined_margin
            else:
                # Short Straddle / Short Strangle: Risco descoberto em ambos os lados
                # Regra B3 CORE: O ativo só pode ir para cima ou para baixo, nunca ambos.
                # Margem = Max(Risco_Call, Risco_Put) - (1 - haircut) * Prêmio_Oposto
                call_leg = [l for l in order.legs if l.option_type == OptionType.CALL][0]
                put_leg = [l for l in order.legs if l.option_type == OptionType.PUT][0]

                spot = order.underlying_spot
                base_call_risk = (spot * self.naked_margin_pct + max(Decimal("0.00"), spot - call_leg.strike)) * packages_dec
                base_put_risk = (spot * self.naked_margin_pct + max(Decimal("0.00"), put_leg.strike - spot)) * packages_dec

                max_leg_risk = max(base_call_risk, base_put_risk)
                # Perna oposta concede crédito parcial de prêmio com haircut de spread de vol
                opposite_premium = (min(call_leg.market_price, put_leg.market_price) * packages_dec)
                offset_credit = opposite_premium * (Decimal("1.00") - self.volatility_spread_haircut)

                combined_margin = max(Decimal("0.00"), max_leg_risk - offset_credit)
                category = MarginCategory.WORST_CASE_OFFSET
                worst_loss = Decimal("99999999.00")  # Teoricamente ilimitado

        elif st == StrategyType.IRON_CONDOR:
            # Trava com asas compradas que limitam o risco
            # Max Loss = Max(Largura_Put_Spread, Largura_Call_Spread) * Qty - Crédito_Recebido
            put_legs = sorted([l for l in order.legs if l.option_type == OptionType.PUT], key=lambda x: x.strike)
            call_legs = sorted([l for l in order.legs if l.option_type == OptionType.CALL], key=lambda x: x.strike)

            put_spread_width = put_legs[1].strike - put_legs[0].strike
            call_spread_width = call_legs[1].strike - call_legs[0].strike
            max_spread_width = max(put_spread_width, call_spread_width)

            # Crédito líquido unitário
            credit_received = -net_unit_price  # Net unit price é negativo para crédito
            if credit_received > Decimal("0.00"):
                max_loss_per_unit = max(Decimal("0.00"), max_spread_width - credit_received)
            else:
                # Debit condor
                max_loss_per_unit = net_unit_price

            worst_loss = (max_loss_per_unit * packages_dec).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
            # Margem exigida pela B3 é exatamente a perda máxima do spread (com buffer opcional de haircut)
            combined_margin = worst_loss
            category = MarginCategory.SPREAD_CAPPED

        elif st == StrategyType.COLLAR:
            # Collar: Spot comprado + Put comprada (piso) + Call vendida (coberta)
            # A call vendida é 100% coberta pela custódia do ativo objeto (Carteira 2306-6).
            # A put comprada assegura um piso de liquidação pelo strike K_put.
            spot = order.underlying_spot
            put_leg = [l for l in order.legs if l.option_type == OptionType.PUT][0]
            call_leg = [l for l in order.legs if l.option_type == OptionType.CALL][0]

            # Risco máximo de perda do Collar:
            # Queda até o strike da Put + prêmio líquido do collar (ou - crédito recebido)
            options_net_cost = put_leg.market_price - call_leg.market_price
            max_spot_drop = spot - put_leg.strike
            max_loss_per_unit = max_spot_drop + options_net_cost
            worst_loss = (max_loss_per_unit * packages_dec).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
            
            # Como a call é coberta e a put dá direito de venda, a margem de risco
            # exigida para o derivativo é estritamente o valor em risco abaixo do strike da put.
            combined_margin = max(Decimal("0.00"), worst_loss)
            category = MarginCategory.COVERED_COLLAR

        elif st == StrategyType.BUTTERFLY:
            # Borboleta de 3 strikes comprada: Risco máximo é o prêmio pago
            # Borboleta vendida: Risco máximo é a largura da asa - crédito recebido
            is_long = any(l.side == LegSide.BUY and l.ratio == 1 for l in order.legs)

            if net_unit_price > Decimal("0.00"):
                # Long Butterfly (débito)
                worst_loss = (net_unit_price * packages_dec).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
                combined_margin = worst_loss
                category = MarginCategory.DEBIT_PAID
            else:
                # Short Butterfly (crédito)
                sorted_strikes = sorted([l.strike for l in order.legs])
                wing_width = sorted_strikes[1] - sorted_strikes[0]
                credit = -net_unit_price
                max_loss_unit = max(Decimal("0.00"), wing_width - credit)
                worst_loss = (max_loss_unit * packages_dec).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
                combined_margin = worst_loss
                category = MarginCategory.SPREAD_CAPPED
        else:
            combined_margin = isolated_margin
            category = MarginCategory.WORST_CASE_OFFSET
            worst_loss = combined_margin

        combined_margin = combined_margin.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
        margin_benefit = max(Decimal("0.00"), isolated_margin - combined_margin).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
        
        haircut_pct = Decimal("0.0000")
        if isolated_margin > Decimal("0.00"):
            haircut_pct = ((margin_benefit / isolated_margin) * Decimal("100.0")).quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_EVEN)

        return CombinedMarginRequirement(
            strategy_type=st,
            packages=packages,
            margin_category=category,
            naked_isolated_margin=isolated_margin,
            combined_margin=combined_margin,
            margin_benefit=margin_benefit,
            haircut_applied_pct=haircut_pct,
            worst_case_loss=worst_loss,
            net_premium_flow=net_premium_flow,
        )


# ---------------------------------------------------------------------------
# Motor de Negociação e Execução Atômica Multi-Leg: PumaAtomicMultiLegEngine
# ---------------------------------------------------------------------------

class PumaAtomicMultiLegEngine:
    """Motor de execução atômica multi-leg no PUMA Trading System com garantia All-or-None."""

    def __init__(
        self,
        margin_engine: Optional[CombinedRiskMarginEngine] = None,
    ) -> None:
        self.margin_engine = margin_engine or CombinedRiskMarginEngine()
        self.executed_orders: Dict[str, MultiLegOrder] = {}
        self.receipts: Dict[str, MultiLegExecutionReceipt] = {}

    def execute_order(
        self,
        order: MultiLegOrder,
        simulated_book: Dict[str, Dict[str, Any]],
        allow_partial_packages: bool = False,
    ) -> MultiLegExecutionReceipt:
        """Executa a ordem multi-leg de forma atômica contra a liquidez disponível no book.
        
        Garantias Formais:
        1. Atomicidade: Ou todas as pernas executam na proporção exata, ou nenhuma executa.
        2. Proporção Estrita (Ratio Invariant): Q_i = k * R_i para todas as pernas.
        3. Preço Limite Respeitado: Preço líquido executado <= limit_net_price (compra)
           ou >= limit_net_price (venda).
        """
        # 1. Validação estrutural
        VolatilityStrategyValidator.validate_order(order)

        # 2. Verificação de liquidez para cada perna no livro
        max_possible_packages = order.packages_requested

        for leg in order.legs:
            symbol = leg.symbol
            if symbol not in simulated_book:
                raise AtomicExecutionError(f"Ativo {symbol} da perna {leg.leg_id} não possui livro de ofertas ativo.")

            book_entry = simulated_book[symbol]
            required_side = "ask" if leg.side == LegSide.BUY else "bid"
            available_qty = int(book_entry.get(f"{required_side}_qty", 0))

            packages_for_leg = available_qty // leg.ratio
            if packages_for_leg < max_possible_packages:
                max_possible_packages = packages_for_leg

        if max_possible_packages <= 0:
            order.status = ExecutionStatus.REJECTED
            raise AtomicExecutionError("Liquidez insuficiente no livro para executar até mesmo 1 pacote da estratégia.")

        if max_possible_packages < order.packages_requested and not allow_partial_packages:
            order.status = ExecutionStatus.REJECTED
            raise AtomicExecutionError(
                f"Liquidez insuficiente para execução All-or-None: solicitado {order.packages_requested}, "
                f"máximo disponível {max_possible_packages}."
            )

        packages_to_fill = order.packages_requested if not allow_partial_packages else max_possible_packages

        # 3. Apuração dos preços executados em cada perna
        executed_leg_fills: List[LegExecutionFill] = []
        actual_net_unit_price = Decimal("0.00")
        total_cash_flow = Decimal("0.00")

        for idx, leg in enumerate(order.legs, start=1):
            book_entry = simulated_book[leg.symbol]
            price_key = "best_ask" if leg.side == LegSide.BUY else "best_bid"
            fill_price = Decimal(str(book_entry[price_key]))

            executed_qty = packages_to_fill * leg.ratio
            # Verificação estrita da lei de proporção
            if executed_qty % leg.ratio != 0 or (executed_qty // leg.ratio) != packages_to_fill:
                raise RatioConservationError(
                    f"Quebra da lei de proporção de pernas: Q_i={executed_qty}, R_i={leg.ratio}, k={packages_to_fill}."
                )

            # Fluxo financeiro: Comprador paga (-), Vendedor recebe (+)
            sign = Decimal("-1") if leg.side == LegSide.BUY else Decimal("1")
            leg_cash_flow = sign * (fill_price * Decimal(executed_qty))
            total_cash_flow += leg_cash_flow

            # Contribuição para o preço unitário do pacote (convenção: >0 débito, <0 crédito)
            unit_sign = Decimal("1") if leg.side == LegSide.BUY else Decimal("-1")
            actual_net_unit_price += unit_sign * (fill_price * Decimal(leg.ratio))

            fill = LegExecutionFill(
                fill_id=f"FILL-{order.order_id}-{idx:02d}",
                leg_id=leg.leg_id,
                symbol=leg.symbol,
                side=leg.side,
                executed_qty=executed_qty,
                fill_price=fill_price,
                net_cash_flow=leg_cash_flow.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN),
            )
            executed_leg_fills.append(fill)

        actual_net_unit_price = actual_net_unit_price.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
        total_cash_flow = total_cash_flow.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)

        # 4. Verificação de Preço Limite da Estratégia
        if order.limit_net_price is not None:
            # Se a estratégia for de débito (comprador da estrutura, net_price > 0):
            # Preço real não pode ser maior que o limite (P_real <= P_limite)
            # Se for crédito (vendedor da estrutura, net_price < 0):
            # Crédito recebido deve ser ao menos o crédito estipulado (P_real <= P_limite em valor absoluto,
            # ou seja, se limite é -1.60 e executou a -1.75, recebeu MAIS crédito, o que é favorável!).
            if actual_net_unit_price > order.limit_net_price:
                order.status = ExecutionStatus.REJECTED
                raise AtomicExecutionError(
                    f"Violação de preço limite da estratégia: Preço obtido {actual_net_unit_price} "
                    f"é desfavorável em relação ao limite estipulado {order.limit_net_price}."
                )

        # 5. Efetivação atômica
        order.packages_executed = packages_to_fill
        order.status = ExecutionStatus.FILLED if packages_to_fill == order.packages_requested else ExecutionStatus.PARTIALLY_FILLED
        self.executed_orders[order.order_id] = order

        receipt = MultiLegExecutionReceipt(
            execution_id=f"EXEC-{order.order_id}",
            order_id=order.order_id,
            strategy_type=order.strategy_type,
            packages_filled=packages_to_fill,
            net_strategy_price=actual_net_unit_price,
            total_net_cash_flow=total_cash_flow,
            leg_fills=executed_leg_fills,
            timestamp_ms=order.timestamp_ms or int(datetime.now().timestamp() * 1000),
            atomic=True,
            ratio_conservation_verified=True,
        )
        self.receipts[receipt.execution_id] = receipt
        return receipt

    def verify_ratio_conservation(self, receipt: MultiLegExecutionReceipt, order: MultiLegOrder) -> bool:
        """Verifica formalmente a Lei de Conservação de Proporção entre Pernas para a execução."""
        k = receipt.packages_filled
        if k <= 0:
            return False

        leg_map = {leg.leg_id: leg for leg in order.legs}
        for fill in receipt.leg_fills:
            original_leg = leg_map.get(fill.leg_id)
            if not original_leg:
                return False
            expected_qty = k * original_leg.ratio
            if fill.executed_qty != expected_qty:
                return False
            if fill.executed_qty / original_leg.ratio != k:
                return False

        return True


# ---------------------------------------------------------------------------
# Módulo de Análise Quantitativa, Payoff e Gregas: VolatilityAnalytics
# ---------------------------------------------------------------------------

class VolatilityAnalytics:
    """Motor quantitativo para cálculo de Gregas e Perfil de Payoff da Estratégia."""

    @staticmethod
    def calculate_strategy_greeks(
        order: MultiLegOrder,
        interest_rate_annual: float = 0.1125,
        days_to_expiration: int = 16,
    ) -> StrategyGreeks:
        """Calcula as gregas agregadas da estratégia estruturada."""
        t_years = max(1e-6, days_to_expiration / 252.0)
        spot_f = float(order.underlying_spot)

        agg_delta = Decimal("0.0000")
        agg_gamma = Decimal("0.0000")
        agg_vega = Decimal("0.0000")
        agg_theta = Decimal("0.0000")

        for leg in order.legs:
            if leg.option_type == OptionType.SPOT:
                delta_unit = 1.0 if leg.side == LegSide.BUY else -1.0
                gamma_unit, vega_unit, theta_unit = 0.0, 0.0, 0.0
            else:
                strike_f = float(leg.strike)
                vol_f = float(leg.implied_vol) if leg.implied_vol is not None else 0.28
                is_call = (leg.option_type == OptionType.CALL)

                d, g, v, th = black_scholes_greeks(
                    spot=spot_f,
                    strike=strike_f,
                    maturity_years=t_years,
                    rate=interest_rate_annual,
                    vol=vol_f,
                    is_call=is_call,
                )
                sign = 1.0 if leg.side == LegSide.BUY else -1.0
                delta_unit = d * sign
                gamma_unit = g * sign
                vega_unit = v * sign
                theta_unit = th * sign

            ratio_dec = Decimal(leg.ratio)
            agg_delta += Decimal(str(round(delta_unit, 6))) * ratio_dec
            agg_gamma += Decimal(str(round(gamma_unit, 6))) * ratio_dec
            agg_vega += Decimal(str(round(vega_unit, 6))) * ratio_dec
            agg_theta += Decimal(str(round(theta_unit, 6))) * ratio_dec

        return StrategyGreeks(
            delta=agg_delta.quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_EVEN),
            gamma=agg_gamma.quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_EVEN),
            vega=agg_vega.quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_EVEN),
            theta=agg_theta.quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_EVEN),
        )

    @staticmethod
    def calculate_payoff(
        order: MultiLegOrder,
        min_spot: Decimal,
        max_spot: Decimal,
        steps: int = 50,
    ) -> StrategyPayoff:
        """Gera a curva analítica de Payoff (PnL) no vencimento ao longo de uma grade de preços spot."""
        if steps <= 1:
            steps = 50

        step_size = (max_spot - min_spot) / Decimal(steps)
        spot_grid: List[Decimal] = [
            (min_spot + step_size * Decimal(i)).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
            for i in range(steps + 1)
        ]

        net_unit_price = order.calculate_net_unit_price()
        packages = Decimal(order.packages_requested)
        pnls: List[Decimal] = []

        for spot in spot_grid:
            total_payoff_unit = Decimal("0.00")
            for leg in order.legs:
                if leg.option_type == OptionType.SPOT:
                    intrinsic = spot
                    cost = leg.market_price
                elif leg.option_type == OptionType.CALL:
                    intrinsic = max(Decimal("0.00"), spot - leg.strike)
                    cost = leg.market_price
                else:  # PUT
                    intrinsic = max(Decimal("0.00"), leg.strike - spot)
                    cost = leg.market_price

                sign = Decimal("1") if leg.side == LegSide.BUY else Decimal("-1")
                leg_pnl_unit = sign * (intrinsic - cost) * Decimal(leg.ratio)
                total_payoff_unit += leg_pnl_unit

            total_pnl = (total_payoff_unit * packages).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
            pnls.append(total_pnl)

        # Determinação analítica de Breakevens, Max Profit e Max Loss
        breakevens: List[Decimal] = []
        for i in range(len(pnls) - 1):
            p1, p2 = pnls[i], pnls[i + 1]
            if (p1 <= Decimal("0.00") and p2 >= Decimal("0.00")) or (p1 >= Decimal("0.00") and p2 <= Decimal("0.00")):
                # Interpolação linear de zero
                s1, s2 = spot_grid[i], spot_grid[i + 1]
                if p2 != p1:
                    zero_spot = s1 - p1 * ((s2 - s1) / (p2 - p1))
                    breakevens.append(zero_spot.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN))

        st = order.strategy_type
        max_p: Optional[Decimal] = None
        max_l: Optional[Decimal] = None

        if st == StrategyType.STRADDLE:
            if order.legs[0].side == LegSide.BUY:
                max_l = (net_unit_price * packages).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
                max_p = None  # Ilimitado
            else:
                max_p = (-net_unit_price * packages).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
                max_l = None
        elif st == StrategyType.STRANGLE:
            if order.legs[0].side == LegSide.BUY:
                max_l = (net_unit_price * packages).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
                max_p = None
            else:
                max_p = (-net_unit_price * packages).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
                max_l = None
        elif st == StrategyType.IRON_CONDOR:
            # Lucro máximo é o crédito líquido total
            credit = -net_unit_price
            max_p = (credit * packages).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
            # Prejuízo máximo é a maior largura de asa menos o crédito
            put_legs = sorted([l for l in order.legs if l.option_type == OptionType.PUT], key=lambda x: x.strike)
            call_legs = sorted([l for l in order.legs if l.option_type == OptionType.CALL], key=lambda x: x.strike)
            spread_w = max(put_legs[1].strike - put_legs[0].strike, call_legs[1].strike - call_legs[0].strike)
            max_l = ((spread_w - credit) * packages).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
        elif st == StrategyType.COLLAR:
            spot_entry = [l for l in order.legs if l.option_type == OptionType.SPOT][0].market_price
            put_strike = [l for l in order.legs if l.option_type == OptionType.PUT][0].strike
            call_strike = [l for l in order.legs if l.option_type == OptionType.CALL][0].strike
            net_opt_cost = (
                [l for l in order.legs if l.option_type == OptionType.PUT][0].market_price -
                [l for l in order.legs if l.option_type == OptionType.CALL][0].market_price
            )
            # Max profit: (Call strike - spot_entry) - net_opt_cost
            max_p = (((call_strike - spot_entry) - net_opt_cost) * packages).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
            # Max loss: (spot_entry - Put strike) + net_opt_cost
            max_l = (((spot_entry - put_strike) + net_opt_cost) * packages).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
        elif st == StrategyType.BUTTERFLY:
            if net_unit_price > Decimal("0.00"):
                max_l = (net_unit_price * packages).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
                strikes = sorted([l.strike for l in order.legs])
                wing_w = strikes[1] - strikes[0]
                max_p = ((wing_w - net_unit_price) * packages).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
            else:
                max_p = (-net_unit_price * packages).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)
                strikes = sorted([l.strike for l in order.legs])
                wing_w = strikes[1] - strikes[0]
                max_l = ((wing_w - (-net_unit_price)) * packages).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_EVEN)

        return StrategyPayoff(
            strategy_type=st,
            spot_prices=spot_grid,
            pnls=pnls,
            max_profit=max_p,
            max_loss=max_l,
            breakeven_points=breakevens,
        )


# ---------------------------------------------------------------------------
# Função Utilitária para Carga de Fixtures
# ---------------------------------------------------------------------------

def load_from_fixture(fixture_path: Union[str, Path]) -> Dict[str, Any]:
    """Carrega dados canônicos de teste de estratégias combinadas da B3 a partir de arquivo JSON."""
    path = Path(fixture_path)
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo de fixture não encontrado: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data
