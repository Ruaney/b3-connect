"""Módulo de Negociação, Precificação Black-76 e Exercício de Opções sobre Futuros de Commodities Agrícolas (FL-064).

Implementa a precificação analítica, cálculo de gregas de primeira e segunda ordem,
gestão de posições, exercício automático no vencimento e conversão de opções
em contratos futuros agropecuários da B3:
- Soja em Grão (SJC / SOJ): 450 sacas de 60 kg (cotação em USD/saca).
- Milho em Grão (CCM): 450 sacas de 60 kg (cotação em BRL/saca).
- Boi Gordo (BGI): 330 arrobas líquidas de 15 kg (cotação em BRL/arroba).
- Café Arábica (ICF): 100 sacas de 60 kg (cotação em USD/saca).

Regras de Negócio e Invariantes Formais da B3:
1. Modelo Analítico Black-76 (Fischer Black 1976):
   - Precificação exata de Calls e Puts sobre futuros com desconto a termo.
   - Gregas analíticas de 1ª ordem: Delta (Call e Put), Vega, Theta (anual e diário 252 DU), Rho.
   - Gregas analíticas de 2ª ordem: Gamma, Vanna, Volga (Vomma).
2. Lei da Paridade Put-Call Black-76:
   C - P == (F - K) * exp(-r * T) comprovada com tolerância estrita em Decimal.
3. Exercício Automático no Vencimento:
   - Condição de Moneyness: Call ITM se (F_settle - K) >= 1 tick; Put ITM se (K - F_settle) >= 1 tick.
   - Suporte formal a Contrary Instruction (instrução contrária de não-exercício pelo titular).
4. Conversão Determinística em Contratos Futuros:
   - Call compradora (Long Call) exercida -> Posição Comprada no Futuro ao preço de Strike K.
   - Call vendedora (Short Call) atribuída -> Posição Vendida no Futuro ao preço de Strike K.
   - Put compradora (Long Put) exercida -> Posição Vendida no Futuro ao preço de Strike K.
   - Put vendedora (Short Put) atribuída -> Posição Comprada no Futuro ao preço de Strike K.
5. Lei de Conservação Física e Financeira de Exercício:
   - Quantidade_Futuros_Criada == Quantidade_Opções_Exercidas (conservação bilateral).
   - Variação Financeira MTM de Abertura: Soma_MTM(Holders) + Soma_MTM(Writers) == 0,00 (Zero-Sum).

100% Python Standard Library (sem dependências externas).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

try:
    from src.invariants import InvariantViolationError, check
except ImportError:
    class InvariantViolationError(AssertionError):
        """Fallback caso módulo de invariantes não esteja disponível diretamente."""
        def __init__(self, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
            super().__init__(message)
            self.message = message
            self.law_code = law_code
            self.details = details or {}

    def check(condition: bool, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
        if not condition:
            raise InvariantViolationError(message, law_code=law_code, details=details)


# ===========================================================================
# Constantes de Precisão Numérica e Parâmetros B3
# ===========================================================================

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_FOUR = Decimal("0.0001")
QUANTIZE_GREEK = Decimal("0.000001")
QUANTIZE_RATE = Decimal("0.00000001")
BUSINESS_DAYS_YEAR = Decimal("252")
CALENDAR_DAYS_YEAR = Decimal("365")

# Tolerância padrão para Paridade Put-Call Black-76
DEFAULT_PARITY_TOLERANCE = Decimal("0.0001")


def round_cents(val: Decimal) -> Decimal:
    """Arredonda valor financeiro para 2 casas decimais (centavos)."""
    return val.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)


def round_four(val: Decimal) -> Decimal:
    """Arredonda para 4 casas decimais (preço unitário de commodity)."""
    return val.quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_UP)


def round_greek(val: Decimal) -> Decimal:
    """Arredonda sensibilidade grega para 6 casas decimais."""
    return val.quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_EVEN)


def to_decimal(value: Any, field_name: str = "campo") -> Decimal:
    """Converte valor para Decimal de forma determinística."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise CommodityOptionsValidationError(f"Valor nulo não permitido para '{field_name}'.")
    try:
        cleaned = str(value).strip().replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError) as err:
        raise CommodityOptionsValidationError(
            f"Valor inválido '{value}' para o campo '{field_name}': {err}"
        ) from err


def parse_date(value: Any, field_name: str = "data") -> date:
    """Converte string ISO (YYYY-MM-DD) ou objeto date em date."""
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError as err:
            raise CommodityOptionsValidationError(
                f"Formato de data inválido para '{field_name}': '{value}'. Esperado YYYY-MM-DD."
            ) from err
    raise CommodityOptionsValidationError(
        f"Tipo inválido para '{field_name}': {type(value).__name__}."
    )


# ===========================================================================
# Exceções de Domínio
# ===========================================================================

class CommodityOptionsError(Exception):
    """Exceção base para o motor de Opções sobre Futuros de Commodities Agrícolas B3."""


class CommodityOptionsValidationError(CommodityOptionsError):
    """Erro de validação cadastral, parâmetros contratuais ou consistência de dados."""


class Black76CalculationError(CommodityOptionsError):
    """Erro numérico ou analítico durante precificação ou cômputo de gregas no Black-76."""


class ExerciseRuleViolationError(CommodityOptionsError):
    """Violação de regras operacionais de exercício ou conversão de futuros."""


class CommodityOptionInvariantError(InvariantViolationError, CommodityOptionsError):
    """Violação formal de leis invariantes de mercado ou conservação física/financeira."""


# ===========================================================================
# Enumerações Canônicas
# ===========================================================================

class CommodityType(str, Enum):
    """Commodities agrícolas listadas na B3 com contratos futuros e opções."""
    SOYBEAN = "SOYBEAN"           # Soja em Grão (Futuros SJC / SOJ)
    CORN = "CORN"                 # Milho em Grão (Futuro CCM)
    LIVE_CATTLE = "LIVE_CATTLE"   # Boi Gordo (Futuro BGI)
    ARABICA_COFFEE = "ARABICA_COFFEE"  # Café Arábica (Futuro ICF)

    @classmethod
    def from_code(cls, value: Union[str, CommodityType]) -> CommodityType:
        """Mapeia código ticker B3 ou string para a enumeração canônica."""
        if isinstance(value, cls):
            return value
        val = str(value).strip().upper()
        if val in ("SOYBEAN", "SOJ", "SJC", "SOJA"):
            return cls.SOYBEAN
        if val in ("CORN", "CCM", "MILHO"):
            return cls.CORN
        if val in ("LIVE_CATTLE", "BGI", "BOI", "BOI_GORDO", "CATTLE"):
            return cls.LIVE_CATTLE
        if val in ("ARABICA_COFFEE", "ICF", "CAFE", "COFFEE"):
            return cls.ARABICA_COFFEE
        for item in cls:
            if item.value == val:
                return item
        raise CommodityOptionsValidationError(f"Commodity agrícola desconhecida ou não suportada: '{value}'")


class OptionType(str, Enum):
    """Tipo da opção: Compra (CALL) ou Venda (PUT)."""
    CALL = "CALL"
    PUT = "PUT"

    @classmethod
    def from_str(cls, value: Union[str, OptionType]) -> OptionType:
        if isinstance(value, cls):
            return value
        val = str(value).strip().upper()
        if val in ("CALL", "C", "COMPRA"):
            return cls.CALL
        if val in ("PUT", "P", "VENDA"):
            return cls.PUT
        raise CommodityOptionsValidationError(f"Tipo de opção inválido: '{value}'")


class OptionStyle(str, Enum):
    """Estilo de exercício da opção (Europeu ou Americano)."""
    EUROPEAN = "EUROPEAN"
    AMERICAN = "AMERICAN"

    @classmethod
    def from_str(cls, value: Union[str, OptionStyle]) -> OptionStyle:
        if isinstance(value, cls):
            return value
        val = str(value).strip().upper()
        if val in ("EUROPEAN", "EUROPEIA", "EUR", "E"):
            return cls.EUROPEAN
        if val in ("AMERICAN", "AMERICANA", "USA", "A"):
            return cls.AMERICAN
        raise CommodityOptionsValidationError(f"Estilo de opção inválido: '{value}'")


class PositionSide(str, Enum):
    """Sentido da posição do participante (Titular / Lançador)."""
    LONG = "LONG"    # Comprador / Titular
    SHORT = "SHORT"  # Vendedor / Lançador

    @classmethod
    def from_str(cls, value: Union[str, PositionSide]) -> PositionSide:
        if isinstance(value, cls):
            return value
        val = str(value).strip().upper()
        if val in ("LONG", "BUY", "COMPRADO", "COMPRADOR", "TITULAR"):
            return cls.LONG
        if val in ("SHORT", "SELL", "VENDIDO", "VENDEDOR", "LANCADOR"):
            return cls.SHORT
        raise CommodityOptionsValidationError(f"Lado de posição inválido: '{value}'")


class ExerciseDisposition(str, Enum):
    """Resultado da apuração do exercício de uma opção no vencimento."""
    AUTOMATIC_EXERCISED = "AUTOMATIC_EXERCISED"              # Exercida automaticamente (ITM >= 1 tick)
    CONTRARY_INSTRUCTION = "CONTRARY_INSTRUCTION"            # Não exercida por instrução contrária do titular
    EXPIRED_OTM = "EXPIRED_OTM"                              # Expirou sem exercício (Fora do Dinheiro)
    BELOW_TICK_THRESHOLD = "BELOW_TICK_THRESHOLD"            # ITM porém inferior ao tick mínimo regulamentar


# ===========================================================================
# Especificações Contratuais da B3
# ===========================================================================

@dataclass(frozen=True)
class CommoditySpecification:
    """Especificações canônicas de contrato futuro e opções agrícolas da B3."""
    commodity: CommodityType
    ticker_prefix: str
    name: str
    contract_size: Decimal       # Quantidade física por contrato (multiplicador)
    unit_of_measure: str        # Unidade física (sacas 60kg, arrobas)
    currency: str               # Moeda de cotação ("USD" ou "BRL")
    tick_size: Decimal          # Variação mínima de apreçamento

    def to_dict(self) -> Dict[str, Any]:
        return {
            "commodity": self.commodity.value,
            "ticker_prefix": self.ticker_prefix,
            "name": self.name,
            "contract_size": str(self.contract_size),
            "unit_of_measure": self.unit_of_measure,
            "currency": self.currency,
            "tick_size": str(self.tick_size),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CommoditySpecification:
        return cls(
            commodity=CommodityType.from_code(data["commodity"]),
            ticker_prefix=str(data["ticker_prefix"]).strip().upper(),
            name=str(data["name"]),
            contract_size=to_decimal(data["contract_size"], "contract_size"),
            unit_of_measure=str(data["unit_of_measure"]),
            currency=str(data["currency"]).strip().upper(),
            tick_size=to_decimal(data["tick_size"], "tick_size"),
        )

    @classmethod
    def get_default_specification(cls, commodity_or_code: Union[str, CommodityType]) -> CommoditySpecification:
        """Retorna a especificação padrão oficial da B3 para a commodity."""
        cmd = CommodityType.from_code(commodity_or_code)
        if cmd == CommodityType.SOYBEAN:
            return cls(
                commodity=cmd,
                ticker_prefix="SJC",
                name="Soja em Grão FOB Santos (B3)",
                contract_size=Decimal("450"),
                unit_of_measure="sacas de 60kg (27 metric tons)",
                currency="USD",
                tick_size=Decimal("0.01"),
            )
        if cmd == CommodityType.CORN:
            return cls(
                commodity=cmd,
                ticker_prefix="CCM",
                name="Milho em Grão Campinas (B3)",
                contract_size=Decimal("450"),
                unit_of_measure="sacas de 60kg (27 metric tons)",
                currency="BRL",
                tick_size=Decimal("0.01"),
            )
        if cmd == CommodityType.LIVE_CATTLE:
            return cls(
                commodity=cmd,
                ticker_prefix="BGI",
                name="Boi Gordo Indicador Cepea (B3)",
                contract_size=Decimal("330"),
                unit_of_measure="arrobas líquidas de 15kg (4.950 kg)",
                currency="BRL",
                tick_size=Decimal("0.01"),
            )
        if cmd == CommodityType.ARABICA_COFFEE:
            return cls(
                commodity=cmd,
                ticker_prefix="ICF",
                name="Café Arábica tipo 4/5 (B3)",
                contract_size=Decimal("100"),
                unit_of_measure="sacas de 60kg (6 metric tons)",
                currency="USD",
                tick_size=Decimal("0.05"),
            )
        raise CommodityOptionsValidationError(f"Sem especificação padrão para commodity: {commodity_or_code}")


# ===========================================================================
# Modelo Analítico Black-76 (Fischer Black 1976) e Gregas
# ===========================================================================

@dataclass
class Black76Greeks:
    """Conjunto completo de gregas analíticas de primeira e segunda ordem no modelo Black-76."""
    delta: Decimal           # Sensibilidade ao preço futuro (dPrice/dF)
    gamma: Decimal           # Segunda derivada w.r.t futuro (d2Price/dF2)
    vega: Decimal            # Sensibilidade à vol absoluta (dPrice/dVol)
    vega_1pct: Decimal       # Sensibilidade à variação de 1 ponto percentual de vol (vega * 0.01)
    theta_annual: Decimal    # Decaimento temporal anual (-dPrice/dT)
    theta_daily: Decimal     # Decaimento temporal por dia útil (theta_annual / 252)
    rho: Decimal             # Sensibilidade à taxa livre de risco contínua (dPrice/dr)
    rho_1pct: Decimal        # Sensibilidade a 1% de juros (rho * 0.01)
    vanna: Decimal           # Segunda ordem mista: sensibilidade cruzada dDelta/dVol == dVega/dF
    volga: Decimal           # Segunda ordem: sensibilidade de convexidade de vol dVega/dVol (Vomma)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delta": str(self.delta),
            "gamma": str(self.gamma),
            "vega": str(self.vega),
            "vega_1pct": str(self.vega_1pct),
            "theta_annual": str(self.theta_annual),
            "theta_daily": str(self.theta_daily),
            "rho": str(self.rho),
            "rho_1pct": str(self.rho_1pct),
            "vanna": str(self.vanna),
            "volga": str(self.volga),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Black76Greeks:
        return cls(
            delta=to_decimal(data["delta"], "delta"),
            gamma=to_decimal(data["gamma"], "gamma"),
            vega=to_decimal(data["vega"], "vega"),
            vega_1pct=to_decimal(data["vega_1pct"], "vega_1pct"),
            theta_annual=to_decimal(data["theta_annual"], "theta_annual"),
            theta_daily=to_decimal(data["theta_daily"], "theta_daily"),
            rho=to_decimal(data["rho"], "rho"),
            rho_1pct=to_decimal(data["rho_1pct"], "rho_1pct"),
            vanna=to_decimal(data["vanna"], "vanna"),
            volga=to_decimal(data["volga"], "volga"),
        )


@dataclass
class Black76PricingResult:
    """Resultado analítico da avaliação Black-76 para uma série de opções."""
    forward_price: Decimal
    strike: Decimal
    time_to_maturity: Decimal
    risk_free_rate: Decimal
    volatility: Decimal
    discount_factor: Decimal
    call_price: Decimal
    put_price: Decimal
    call_greeks: Black76Greeks
    put_greeks: Black76Greeks
    put_call_parity_diff: Decimal
    parity_holds: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "forward_price": str(self.forward_price),
            "strike": str(self.strike),
            "time_to_maturity": str(self.time_to_maturity),
            "risk_free_rate": str(self.risk_free_rate),
            "volatility": str(self.volatility),
            "discount_factor": str(self.discount_factor),
            "call_price": str(self.call_price),
            "put_price": str(self.put_price),
            "call_greeks": self.call_greeks.to_dict(),
            "put_greeks": self.put_greeks.to_dict(),
            "put_call_parity_diff": str(self.put_call_parity_diff),
            "parity_holds": self.parity_holds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Black76PricingResult:
        return cls(
            forward_price=to_decimal(data["forward_price"], "forward_price"),
            strike=to_decimal(data["strike"], "strike"),
            time_to_maturity=to_decimal(data["time_to_maturity"], "time_to_maturity"),
            risk_free_rate=to_decimal(data["risk_free_rate"], "risk_free_rate"),
            volatility=to_decimal(data["volatility"], "volatility"),
            discount_factor=to_decimal(data["discount_factor"], "discount_factor"),
            call_price=to_decimal(data["call_price"], "call_price"),
            put_price=to_decimal(data["put_price"], "put_price"),
            call_greeks=Black76Greeks.from_dict(data["call_greeks"]),
            put_greeks=Black76Greeks.from_dict(data["put_greeks"]),
            put_call_parity_diff=to_decimal(data["put_call_parity_diff"], "put_call_parity_diff"),
            parity_holds=bool(data["parity_holds"]),
        )


class Black76Model:
    """Implementação analítica estrita do modelo Black-76 para opções sobre contratos futuros."""

    @staticmethod
    def std_norm_cdf(x: float) -> float:
        """Função de distribuição acumulada da normal padrão N(x) via erf."""
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    @staticmethod
    def std_norm_pdf(x: float) -> float:
        """Função de densidade de probabilidade normal padrão N'(x)."""
        return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

    @classmethod
    def calculate_d1_d2(
        cls,
        forward: float,
        strike: float,
        time_to_maturity: float,
        volatility: float,
    ) -> Tuple[float, float]:
        """Calcula analiticamente d1 e d2 de Fischer Black (1976).

        d1 = [ln(F/K) + (1/2)*sigma^2 * T] / [sigma * sqrt(T)]
        d2 = d1 - sigma * sqrt(T)
        """
        if forward <= 0.0 or strike <= 0.0:
            raise Black76CalculationError(
                f"Preços Forward ({forward}) e Strike ({strike}) devem ser estritamente positivos."
            )
        if time_to_maturity <= 0.0 or volatility <= 0.0:
            raise Black76CalculationError(
                f"Tempo ({time_to_maturity}) e Volatilidade ({volatility}) devem ser estritamente positivos para d1/d2."
            )
        sqrt_t = math.sqrt(time_to_maturity)
        vol_sqrt_t = volatility * sqrt_t
        d1 = (math.log(forward / strike) + 0.5 * (volatility ** 2) * time_to_maturity) / vol_sqrt_t
        d2 = d1 - vol_sqrt_t
        return d1, d2

    @classmethod
    def price_call(
        cls,
        forward: Union[Decimal, float],
        strike: Union[Decimal, float],
        time_to_maturity: Union[Decimal, float],
        volatility: Union[Decimal, float],
        risk_free_rate: Union[Decimal, float],
    ) -> Decimal:
        """Calcula o prêmio analítico da Call: C = exp(-r*T) * [F*N(d1) - K*N(d2)]."""
        F = float(forward)
        K = float(strike)
        T = float(time_to_maturity)
        vol = float(volatility)
        r = float(risk_free_rate)

        if F <= 0.0 or K <= 0.0:
            raise Black76CalculationError("Preços forward e strike devem ser estritamente positivos.")

        if T <= 0.0:
            # Opção no vencimento: valor intrínseco puro
            val = max(0.0, F - K)
            return Decimal(str(round(val, 6))).quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_UP)

        disc = math.exp(-r * T)
        if vol <= 0.0:
            val = disc * max(0.0, F - K)
            return Decimal(str(round(val, 6))).quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_UP)

        d1, d2 = cls.calculate_d1_d2(F, K, T, vol)
        call_val = disc * (F * cls.std_norm_cdf(d1) - K * cls.std_norm_cdf(d2))
        return Decimal(str(round(call_val, 8))).quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_UP)

    @classmethod
    def price_put(
        cls,
        forward: Union[Decimal, float],
        strike: Union[Decimal, float],
        time_to_maturity: Union[Decimal, float],
        volatility: Union[Decimal, float],
        risk_free_rate: Union[Decimal, float],
    ) -> Decimal:
        """Calcula o prêmio analítico da Put: P = exp(-r*T) * [K*N(-d2) - F*N(-d1)]."""
        F = float(forward)
        K = float(strike)
        T = float(time_to_maturity)
        vol = float(volatility)
        r = float(risk_free_rate)

        if F <= 0.0 or K <= 0.0:
            raise Black76CalculationError("Preços forward e strike devem ser estritamente positivos.")

        if T <= 0.0:
            val = max(0.0, K - F)
            return Decimal(str(round(val, 6))).quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_UP)

        disc = math.exp(-r * T)
        if vol <= 0.0:
            val = disc * max(0.0, K - F)
            return Decimal(str(round(val, 6))).quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_UP)

        d1, d2 = cls.calculate_d1_d2(F, K, T, vol)
        put_val = disc * (K * cls.std_norm_cdf(-d2) - F * cls.std_norm_cdf(-d1))
        return Decimal(str(round(put_val, 8))).quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_UP)

    @classmethod
    def calculate_greeks(
        cls,
        forward: Union[Decimal, float],
        strike: Union[Decimal, float],
        time_to_maturity: Union[Decimal, float],
        volatility: Union[Decimal, float],
        risk_free_rate: Union[Decimal, float],
        option_type: OptionType,
    ) -> Black76Greeks:
        """Calcula todas as gregas analíticas de primeira e segunda ordem no Black-76.

        Delta_Call = exp(-r*T) * N(d1)
        Delta_Put  = -exp(-r*T) * N(-d1) = Delta_Call - exp(-r*T)
        Gamma      = exp(-r*T) * N'(d1) / [F * sigma * sqrt(T)]
        Vega       = exp(-r*T) * F * sqrt(T) * N'(d1)
        Theta_Call = -exp(-r*T)*F*N'(d1)*sigma / (2*sqrt(T)) + r*Call
        Theta_Put  = -exp(-r*T)*F*N'(d1)*sigma / (2*sqrt(T)) + r*Put
        Rho_Call   = -T * Call
        Rho_Put    = -T * Put
        Vanna      = -exp(-r*T) * N'(d1) * d2 / sigma
        Volga      = Vega * d1 * d2 / sigma
        """
        F = float(forward)
        K = float(strike)
        T = float(time_to_maturity)
        vol = float(volatility)
        r = float(risk_free_rate)

        if T <= 0.0 or vol <= 0.0:
            # Caso no vencimento ou vol zero
            if option_type == OptionType.CALL:
                delta = 1.0 if F > K else (0.5 if F == K else 0.0)
            else:
                delta = -1.0 if F < K else (-0.5 if F == K else 0.0)
            return Black76Greeks(
                delta=Decimal(str(delta)).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_EVEN),
                gamma=Decimal("0.000000"),
                vega=Decimal("0.000000"),
                vega_1pct=Decimal("0.000000"),
                theta_annual=Decimal("0.000000"),
                theta_daily=Decimal("0.000000"),
                rho=Decimal("0.000000"),
                rho_1pct=Decimal("0.000000"),
                vanna=Decimal("0.000000"),
                volga=Decimal("0.000000"),
            )

        sqrt_t = math.sqrt(T)
        disc = math.exp(-r * T)
        d1, d2 = cls.calculate_d1_d2(F, K, T, vol)
        pdf_d1 = cls.std_norm_pdf(d1)
        cdf_d1 = cls.std_norm_cdf(d1)
        cdf_d2 = cls.std_norm_cdf(d2)

        # Preços analíticos para Theta e Rho
        call_val = disc * (F * cdf_d1 - K * cdf_d2)
        put_val = disc * (K * cls.std_norm_cdf(-d2) - F * cls.std_norm_cdf(-d1))

        # Primeira Ordem
        if option_type == OptionType.CALL:
            delta = disc * cdf_d1
            theta_annual = -(disc * F * pdf_d1 * vol) / (2.0 * sqrt_t) + r * call_val
            rho = -T * call_val
        else:
            delta = -disc * cls.std_norm_cdf(-d1)
            theta_annual = -(disc * F * pdf_d1 * vol) / (2.0 * sqrt_t) + r * put_val
            rho = -T * put_val

        # Segunda Ordem e Sensibilidades Comuns (Iguais para Call e Put)
        gamma = (disc * pdf_d1) / (F * vol * sqrt_t)
        vega_raw = disc * F * sqrt_t * pdf_d1
        vega_1pct = vega_raw * 0.01
        theta_daily = theta_annual / 252.0
        rho_1pct = rho * 0.01

        # Vanna e Volga (Vomma)
        vanna = -disc * pdf_d1 * (d2 / vol)
        volga = vega_raw * (d1 * d2 / vol)

        return Black76Greeks(
            delta=Decimal(str(round(delta, 8))).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_EVEN),
            gamma=Decimal(str(round(gamma, 8))).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_EVEN),
            vega=Decimal(str(round(vega_raw, 8))).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_EVEN),
            vega_1pct=Decimal(str(round(vega_1pct, 8))).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_EVEN),
            theta_annual=Decimal(str(round(theta_annual, 8))).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_EVEN),
            theta_daily=Decimal(str(round(theta_daily, 8))).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_EVEN),
            rho=Decimal(str(round(rho, 8))).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_EVEN),
            rho_1pct=Decimal(str(round(rho_1pct, 8))).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_EVEN),
            vanna=Decimal(str(round(vanna, 8))).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_EVEN),
            volga=Decimal(str(round(volga, 8))).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_EVEN),
        )

    @classmethod
    def price(
        cls,
        forward: Union[Decimal, float],
        strike: Union[Decimal, float],
        time_to_maturity: Union[Decimal, float],
        volatility: Union[Decimal, float],
        risk_free_rate: Union[Decimal, float],
        tolerance: Decimal = DEFAULT_PARITY_TOLERANCE,
    ) -> Black76PricingResult:
        """Avalia completamente uma série par (Call e Put), computando gregas e validando paridade."""
        F_dec = to_decimal(forward, "forward")
        K_dec = to_decimal(strike, "strike")
        T_dec = to_decimal(time_to_maturity, "time_to_maturity")
        vol_dec = to_decimal(volatility, "volatility")
        r_dec = to_decimal(risk_free_rate, "risk_free_rate")

        call_p = cls.price_call(F_dec, K_dec, T_dec, vol_dec, r_dec)
        put_p = cls.price_put(F_dec, K_dec, T_dec, vol_dec, r_dec)

        call_g = cls.calculate_greeks(F_dec, K_dec, T_dec, vol_dec, r_dec, OptionType.CALL)
        put_g = cls.calculate_greeks(F_dec, K_dec, T_dec, vol_dec, r_dec, OptionType.PUT)

        disc_float = math.exp(-float(r_dec) * float(T_dec))
        disc_dec = Decimal(str(round(disc_float, 8))).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_UP)

        parity_holds, parity_diff = cls.check_put_call_parity(
            F_dec, K_dec, T_dec, r_dec, call_p, put_p, tolerance=tolerance
        )

        return Black76PricingResult(
            forward_price=F_dec,
            strike=K_dec,
            time_to_maturity=T_dec,
            risk_free_rate=r_dec,
            volatility=vol_dec,
            discount_factor=disc_dec,
            call_price=call_p,
            put_price=put_p,
            call_greeks=call_g,
            put_greeks=put_g,
            put_call_parity_diff=parity_diff,
            parity_holds=parity_holds,
        )

    @classmethod
    def check_put_call_parity(
        cls,
        forward: Decimal,
        strike: Decimal,
        time_to_maturity: Decimal,
        risk_free_rate: Decimal,
        call_price: Decimal,
        put_price: Decimal,
        tolerance: Decimal = DEFAULT_PARITY_TOLERANCE,
    ) -> Tuple[bool, Decimal]:
        """Verifica a Lei de Paridade Put-Call Black-76: C - P == (F - K) * exp(-r*T)."""
        F = float(forward)
        K = float(strike)
        T = float(time_to_maturity)
        r = float(risk_free_rate)

        disc = math.exp(-r * T)
        expected_diff = (F - K) * disc
        actual_diff = float(call_price - put_price)
        error = abs(actual_diff - expected_diff)
        error_dec = Decimal(str(round(error, 8))).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_UP)
        holds = error_dec <= tolerance
        return holds, error_dec


# ===========================================================================
# Contratos, Posições e Conversão em Futuros B3
# ===========================================================================

@dataclass
class AgriculturalOptionContract:
    """Contrato de opção listada sobre futuro agropecuário na B3."""
    ticker: str
    commodity: CommodityType
    underlying_future_ticker: str
    option_type: OptionType
    strike: Decimal
    expiration_date: date
    style: OptionStyle = OptionStyle.EUROPEAN
    specification: Optional[CommoditySpecification] = None

    def __post_init__(self) -> None:
        self.ticker = self.ticker.strip().upper()
        self.underlying_future_ticker = self.underlying_future_ticker.strip().upper()
        self.commodity = CommodityType.from_code(self.commodity)
        self.option_type = OptionType.from_str(self.option_type)
        self.style = OptionStyle.from_str(self.style)
        self.strike = to_decimal(self.strike, "strike")
        self.expiration_date = parse_date(self.expiration_date, "expiration_date")
        if self.specification is None:
            self.specification = CommoditySpecification.get_default_specification(self.commodity)

    @property
    def contract_size(self) -> Decimal:
        return self.specification.contract_size

    @property
    def tick_size(self) -> Decimal:
        return self.specification.tick_size

    @property
    def currency(self) -> str:
        return self.specification.currency

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "commodity": self.commodity.value,
            "underlying_future_ticker": self.underlying_future_ticker,
            "option_type": self.option_type.value,
            "strike": str(self.strike),
            "expiration_date": self.expiration_date.isoformat(),
            "style": self.style.value,
            "specification": self.specification.to_dict() if self.specification else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgriculturalOptionContract:
        spec_data = data.get("specification")
        spec = CommoditySpecification.from_dict(spec_data) if spec_data else None
        return cls(
            ticker=data["ticker"],
            commodity=CommodityType.from_code(data["commodity"]),
            underlying_future_ticker=data["underlying_future_ticker"],
            option_type=OptionType.from_str(data["option_type"]),
            strike=to_decimal(data["strike"], "strike"),
            expiration_date=parse_date(data["expiration_date"], "expiration_date"),
            style=OptionStyle.from_str(data.get("style", "EUROPEAN")),
            specification=spec,
        )


@dataclass
class ParticipantPosition:
    """Posição custodiada de um participante em uma série de opção."""
    position_id: str
    participant_id: str
    option_ticker: str
    side: PositionSide
    quantity: int
    unit_premium: Decimal
    contrary_instruction: bool = False

    def __post_init__(self) -> None:
        self.position_id = self.position_id.strip()
        self.participant_id = self.participant_id.strip().upper()
        self.option_ticker = self.option_ticker.strip().upper()
        self.side = PositionSide.from_str(self.side)
        if self.quantity <= 0:
            raise CommodityOptionsValidationError(
                f"Quantidade da posição '{self.position_id}' deve ser estritamente positiva: {self.quantity}"
            )
        self.unit_premium = to_decimal(self.unit_premium, "unit_premium")
        self.contrary_instruction = bool(self.contrary_instruction)

    def total_premium(self, contract_size: Decimal) -> Decimal:
        """Volume financeiro total do prêmio negociado."""
        return round_cents(Decimal(self.quantity) * contract_size * self.unit_premium)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "participant_id": self.participant_id,
            "option_ticker": self.option_ticker,
            "side": self.side.value,
            "quantity": self.quantity,
            "unit_premium": str(self.unit_premium),
            "contrary_instruction": self.contrary_instruction,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ParticipantPosition:
        return cls(
            position_id=str(data["position_id"]),
            participant_id=str(data["participant_id"]),
            option_ticker=str(data["option_ticker"]),
            side=PositionSide.from_str(data["side"]),
            quantity=int(data["quantity"]),
            unit_premium=to_decimal(data["unit_premium"], "unit_premium"),
            contrary_instruction=bool(data.get("contrary_instruction", False)),
        )


@dataclass
class CreatedFuturePosition:
    """Posição em contrato futuro gerada pelo exercício/atribuição da opção."""
    position_id: str
    participant_id: str
    future_ticker: str
    side: PositionSide              # LONG (Comprado) ou SHORT (Vendido)
    quantity: int                   # Contratos de futuros criados
    entry_price: Decimal            # Preço de abertura no futuro (estritamente igual ao Strike K da opção)
    origin_option_ticker: str
    origin_option_side: PositionSide
    origin_option_type: OptionType
    contract_size: Decimal
    currency: str

    def mtm_variation(self, settlement_future_price: Decimal) -> Decimal:
        """Variação financeira inicial (Ajuste Diário no D0 do exercício)."""
        diff = settlement_future_price - self.entry_price if self.side == PositionSide.LONG else self.entry_price - settlement_future_price
        return round_cents(diff * self.contract_size * Decimal(self.quantity))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "participant_id": self.participant_id,
            "future_ticker": self.future_ticker,
            "side": self.side.value,
            "quantity": self.quantity,
            "entry_price": str(self.entry_price),
            "origin_option_ticker": self.origin_option_ticker,
            "origin_option_side": self.origin_option_side.value,
            "origin_option_type": self.origin_option_type.value,
            "contract_size": str(self.contract_size),
            "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CreatedFuturePosition:
        return cls(
            position_id=str(data["position_id"]),
            participant_id=str(data["participant_id"]),
            future_ticker=str(data["future_ticker"]),
            side=PositionSide.from_str(data["side"]),
            quantity=int(data["quantity"]),
            entry_price=to_decimal(data["entry_price"], "entry_price"),
            origin_option_ticker=str(data["origin_option_ticker"]),
            origin_option_side=PositionSide.from_str(data["origin_option_side"]),
            origin_option_type=OptionType.from_str(data["origin_option_type"]),
            contract_size=to_decimal(data["contract_size"], "contract_size"),
            currency=str(data["currency"]),
        )


@dataclass
class ExerciseEvent:
    """Registro de evento de exercício/expiração de série de opções agrícolas."""
    event_id: str
    option_ticker: str
    underlying_future_ticker: str
    option_type: OptionType
    strike: Decimal
    settlement_future_price: Decimal
    tick_size: Decimal
    intrinsic_value_unit: Decimal
    disposition: ExerciseDisposition
    contracts_exercised: int
    holder_participant_id: str
    writer_participant_id: str
    holder_future_position_id: Optional[str] = None
    writer_future_position_id: Optional[str] = None
    holder_mtm_gain: Decimal = Decimal("0.00")
    writer_mtm_gain: Decimal = Decimal("0.00")

    @property
    def net_financial_mtm(self) -> Decimal:
        """Invariante de conservação financeira: o ganho de uma ponta anula a perda da outra."""
        return round_cents(self.holder_mtm_gain + self.writer_mtm_gain)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "option_ticker": self.option_ticker,
            "underlying_future_ticker": self.underlying_future_ticker,
            "option_type": self.option_type.value,
            "strike": str(self.strike),
            "settlement_future_price": str(self.settlement_future_price),
            "tick_size": str(self.tick_size),
            "intrinsic_value_unit": str(self.intrinsic_value_unit),
            "disposition": self.disposition.value,
            "contracts_exercised": self.contracts_exercised,
            "holder_participant_id": self.holder_participant_id,
            "writer_participant_id": self.writer_participant_id,
            "holder_future_position_id": self.holder_future_position_id,
            "writer_future_position_id": self.writer_future_position_id,
            "holder_mtm_gain": str(self.holder_mtm_gain),
            "writer_mtm_gain": str(self.writer_mtm_gain),
            "net_financial_mtm": str(self.net_financial_mtm),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExerciseEvent:
        return cls(
            event_id=str(data["event_id"]),
            option_ticker=str(data["option_ticker"]),
            underlying_future_ticker=str(data["underlying_future_ticker"]),
            option_type=OptionType.from_str(data["option_type"]),
            strike=to_decimal(data["strike"], "strike"),
            settlement_future_price=to_decimal(data["settlement_future_price"], "settlement_future_price"),
            tick_size=to_decimal(data["tick_size"], "tick_size"),
            intrinsic_value_unit=to_decimal(data["intrinsic_value_unit"], "intrinsic_value_unit"),
            disposition=ExerciseDisposition(data["disposition"]),
            contracts_exercised=int(data["contracts_exercised"]),
            holder_participant_id=str(data["holder_participant_id"]),
            writer_participant_id=str(data["writer_participant_id"]),
            holder_future_position_id=data.get("holder_future_position_id"),
            writer_future_position_id=data.get("writer_future_position_id"),
            holder_mtm_gain=to_decimal(data.get("holder_mtm_gain", "0.00"), "holder_mtm_gain"),
            writer_mtm_gain=to_decimal(data.get("writer_mtm_gain", "0.00"), "writer_mtm_gain"),
        )


# ===========================================================================
# Motor Central de Opções Agrícolas B3 (CommodityOptionsEngine)
# ===========================================================================

class CommodityOptionsEngine:
    """Motor institucional B3 de negociação, precificação Black-76 e exercício de opções agrícolas."""

    def __init__(self, risk_free_rate_annual: Decimal = Decimal("0.1075")) -> None:
        self.risk_free_rate_annual = to_decimal(risk_free_rate_annual, "risk_free_rate_annual")
        self.contracts: Dict[str, AgriculturalOptionContract] = {}
        self.positions: Dict[str, ParticipantPosition] = {}
        self.pricing_cache: Dict[str, Black76PricingResult] = {}
        self.exercise_events: List[ExerciseEvent] = []
        self.created_futures: List[CreatedFuturePosition] = []

    def register_contract(self, contract: AgriculturalOptionContract) -> AgriculturalOptionContract:
        """Registra uma série de opção agrícola no catálogo ativo."""
        if contract.ticker in self.contracts:
            raise CommodityOptionsValidationError(f"Contrato '{contract.ticker}' já está registrado.")
        self.contracts[contract.ticker] = contract
        return contract

    def add_position(self, position: ParticipantPosition) -> ParticipantPosition:
        """Registra uma nova posição de custódia de participante."""
        if position.option_ticker not in self.contracts:
            raise CommodityOptionsValidationError(
                f"Série '{position.option_ticker}' não está cadastrada no motor de opções."
            )
        if position.position_id in self.positions:
            raise CommodityOptionsValidationError(
                f"Posição '{position.position_id}' já cadastrada."
            )
        self.positions[position.position_id] = position
        return position

    def set_contrary_instruction(self, position_id: str, flag: bool = True) -> None:
        """Submete ou cancela instrução contrária de não-exercício (Contrary Instruction) do titular."""
        if position_id not in self.positions:
            raise CommodityOptionsValidationError(f"Posição '{position_id}' não encontrada.")
        pos = self.positions[position_id]
        if pos.side != PositionSide.LONG:
            raise ExerciseRuleViolationError(
                f"Apenas posições titulares (LONG) podem registrar Contrary Instruction. Posição '{position_id}' é {pos.side.value}."
            )
        pos.contrary_instruction = bool(flag)

    def price_series(
        self,
        ticker: str,
        forward_price: Union[Decimal, float],
        time_to_maturity: Union[Decimal, float],
        volatility: Union[Decimal, float],
        risk_free_rate: Optional[Union[Decimal, float]] = None,
    ) -> Black76PricingResult:
        """Precifica analiticamente o contrato e seu par de put/call correspondente via Black-76."""
        if ticker not in self.contracts:
            raise CommodityOptionsValidationError(f"Contrato '{ticker}' não registrado.")
        contract = self.contracts[ticker]
        r = self.risk_free_rate_annual if risk_free_rate is None else to_decimal(risk_free_rate, "rate")
        res = Black76Model.price(
            forward=forward_price,
            strike=contract.strike,
            time_to_maturity=time_to_maturity,
            volatility=volatility,
            risk_free_rate=r,
        )
        self.pricing_cache[ticker] = res
        return res

    def evaluate_and_execute_exercise(
        self,
        expiration_date: date,
        settlement_prices: Mapping[str, Union[Decimal, float, str]],
    ) -> List[ExerciseEvent]:
        """Executa a rotina de exercício automático no vencimento com conversão em futuros.

        Passos:
        1. Identifica contratos que vencem na data informada.
        2. Determina o Moneyness contra o preço de ajuste do futuro (F_settle).
        3. Verifica o threshold de 1 tick mínimo regulamentar.
        4. Checa instruções contrárias (Contrary Instruction).
        5. Atribui titulares exercidos aos lançadores de forma conservativa.
        6. Gera as posições em futuros resultantes ao preço de Strike K.
        7. Apura a variação inicial de MTM comprovando a conservação de valor.
        """
        exp_date = parse_date(expiration_date, "expiration_date")
        executed_events: List[ExerciseEvent] = []

        # Agrupar contratos por ticker
        expiring_contracts = [
            c for c in self.contracts.values() if c.expiration_date == exp_date
        ]

        for contract in expiring_contracts:
            ticker = contract.ticker
            future_ticker = contract.underlying_future_ticker
            if future_ticker not in settlement_prices:
                raise ExerciseRuleViolationError(
                    f"Preço de ajuste ausente para o futuro subjacente '{future_ticker}' no vencimento {exp_date}."
                )

            F_settle = to_decimal(settlement_prices[future_ticker], "settlement_price")
            K = contract.strike
            tick = contract.tick_size

            # Cálculo de valor intrínseco unitário
            if contract.option_type == OptionType.CALL:
                intrinsic = F_settle - K
            else:
                intrinsic = K - F_settle

            # Posições long e short desta série
            long_positions = [
                p for p in self.positions.values()
                if p.option_ticker == ticker and p.side == PositionSide.LONG
            ]
            short_positions = [
                p for p in self.positions.values()
                if p.option_ticker == ticker and p.side == PositionSide.SHORT
            ]

            total_long_qty = sum(p.quantity for p in long_positions)
            total_short_qty = sum(p.quantity for p in short_positions)

            if total_long_qty != total_short_qty:
                raise CommodityOptionInvariantError(
                    f"Desbalanceamento de mercado aberto na série '{ticker}': Long ({total_long_qty}) != Short ({total_short_qty})",
                    law_code="LAW-BMF-OI",
                )

            # Avaliar condição de exercício
            is_itm = intrinsic > Decimal("0.00")
            meets_tick = intrinsic >= tick

            # Fila de lançadores para atribuição proporcional/FIFO
            writer_pool: List[Dict[str, Any]] = [
                {"pos": sp, "remaining": sp.quantity} for sp in short_positions
            ]

            for lp in long_positions:
                event_counter = len(self.exercise_events) + len(executed_events) + 1
                event_id = f"EX-EVT-{ticker}-{lp.participant_id}-{event_counter:04d}"

                if lp.contrary_instruction:
                    # Titular solicitou expressamente não exercer
                    evt = ExerciseEvent(
                        event_id=event_id,
                        option_ticker=ticker,
                        underlying_future_ticker=future_ticker,
                        option_type=contract.option_type,
                        strike=K,
                        settlement_future_price=F_settle,
                        tick_size=tick,
                        intrinsic_value_unit=max(Decimal("0.00"), intrinsic),
                        disposition=ExerciseDisposition.CONTRARY_INSTRUCTION,
                        contracts_exercised=0,
                        holder_participant_id=lp.participant_id,
                        writer_participant_id="N/A",
                    )
                    executed_events.append(evt)
                    continue

                if not is_itm:
                    # Fora do dinheiro (OTM)
                    evt = ExerciseEvent(
                        event_id=event_id,
                        option_ticker=ticker,
                        underlying_future_ticker=future_ticker,
                        option_type=contract.option_type,
                        strike=K,
                        settlement_future_price=F_settle,
                        tick_size=tick,
                        intrinsic_value_unit=Decimal("0.00"),
                        disposition=ExerciseDisposition.EXPIRED_OTM,
                        contracts_exercised=0,
                        holder_participant_id=lp.participant_id,
                        writer_participant_id="N/A",
                    )
                    executed_events.append(evt)
                    continue

                if is_itm and not meets_tick:
                    # ITM mas inferior ao tick mínimo (não exerce por threshold regulamentar B3)
                    evt = ExerciseEvent(
                        event_id=event_id,
                        option_ticker=ticker,
                        underlying_future_ticker=future_ticker,
                        option_type=contract.option_type,
                        strike=K,
                        settlement_future_price=F_settle,
                        tick_size=tick,
                        intrinsic_value_unit=intrinsic,
                        disposition=ExerciseDisposition.BELOW_TICK_THRESHOLD,
                        contracts_exercised=0,
                        holder_participant_id=lp.participant_id,
                        writer_participant_id="N/A",
                    )
                    executed_events.append(evt)
                    continue

                # Exercício Automático no Vencimento (ITM >= tick)
                qty_to_exercise = lp.quantity
                while qty_to_exercise > 0:
                    # Encontrar o próximo lançador com saldo para atribuição
                    available_writer = None
                    for w in writer_pool:
                        if w["remaining"] > 0:
                            available_writer = w
                            break

                    if available_writer is None:
                        raise ExerciseRuleViolationError(
                            f"Esgotamento inconsistente de lançadores para atribuição na série '{ticker}'."
                        )

                    allocated_qty = min(qty_to_exercise, available_writer["remaining"])
                    available_writer["remaining"] -= allocated_qty
                    qty_to_exercise -= allocated_qty
                    writer_pos = available_writer["pos"]

                    # Criar Posições em Futuro
                    # Regra B3:
                    # Call: Titular compra futuro ao strike K; Lançador vende futuro ao strike K.
                    # Put: Titular vende futuro ao strike K; Lançador compra futuro ao strike K.
                    if contract.option_type == OptionType.CALL:
                        holder_fut_side = PositionSide.LONG
                        writer_fut_side = PositionSide.SHORT
                    else:
                        holder_fut_side = PositionSide.SHORT
                        writer_fut_side = PositionSide.LONG

                    holder_fut_id = f"FUT-{future_ticker}-{lp.participant_id}-{len(self.created_futures)+1:05d}"
                    writer_fut_id = f"FUT-{future_ticker}-{writer_pos.participant_id}-{len(self.created_futures)+2:05d}"

                    fut_holder = CreatedFuturePosition(
                        position_id=holder_fut_id,
                        participant_id=lp.participant_id,
                        future_ticker=future_ticker,
                        side=holder_fut_side,
                        quantity=allocated_qty,
                        entry_price=K,
                        origin_option_ticker=ticker,
                        origin_option_side=PositionSide.LONG,
                        origin_option_type=contract.option_type,
                        contract_size=contract.contract_size,
                        currency=contract.currency,
                    )

                    fut_writer = CreatedFuturePosition(
                        position_id=writer_fut_id,
                        participant_id=writer_pos.participant_id,
                        future_ticker=future_ticker,
                        side=writer_fut_side,
                        quantity=allocated_qty,
                        entry_price=K,
                        origin_option_ticker=ticker,
                        origin_option_side=PositionSide.SHORT,
                        origin_option_type=contract.option_type,
                        contract_size=contract.contract_size,
                        currency=contract.currency,
                    )

                    self.created_futures.append(fut_holder)
                    self.created_futures.append(fut_writer)

                    # Variações MTM no D0
                    h_mtm = fut_holder.mtm_variation(F_settle)
                    w_mtm = fut_writer.mtm_variation(F_settle)

                    sub_event_id = f"EX-EVT-{ticker}-{lp.participant_id}-{writer_pos.participant_id}-{len(executed_events)+1:04d}"
                    evt = ExerciseEvent(
                        event_id=sub_event_id,
                        option_ticker=ticker,
                        underlying_future_ticker=future_ticker,
                        option_type=contract.option_type,
                        strike=K,
                        settlement_future_price=F_settle,
                        tick_size=tick,
                        intrinsic_value_unit=intrinsic,
                        disposition=ExerciseDisposition.AUTOMATIC_EXERCISED,
                        contracts_exercised=allocated_qty,
                        holder_participant_id=lp.participant_id,
                        writer_participant_id=writer_pos.participant_id,
                        holder_future_position_id=holder_fut_id,
                        writer_future_position_id=writer_fut_id,
                        holder_mtm_gain=h_mtm,
                        writer_mtm_gain=w_mtm,
                    )
                    executed_events.append(evt)

        self.exercise_events.extend(executed_events)
        return executed_events

    # =======================================================================
    # Verificação Formal de Invariantes da B3
    # =======================================================================

    def verify_invariants(self, raise_on_error: bool = False) -> Dict[str, bool]:
        """Verifica exaustivamente as leis de conservação matemática e regras regulatórias B3.

        Invariantes Auditadas:
        1. put_call_parity_black76: C - P == (F - K) * exp(-r*T) em todas as séries precificadas.
        2. exercise_quantity_conservation: Q_futuros_long == Q_futuros_short == Q_opções_exercidas.
        3. exercise_zero_sum_financial_balance: Soma_MTM(Holders) + Soma_MTM(Writers) == 0.00.
        4. intrinsic_minimum_tick_threshold: Exercício automático somente se ITM >= 1 tick.
        5. contrary_instruction_respected: Posições com instrução contrária nunca geram futuros.
        6. greeks_monotonicity_and_bounds: Delta nos limites teóricos, Gamma >= 0, Vega >= 0.
        7. premium_non_negativity: Prêmios C >= 0 e P >= 0.
        8. lower_bound_discounted_intrinsic: C >= max(0, (F-K)*exp(-rT)), P >= max(0, (K-F)*exp(-rT)).
        9. commodity_specifications_valid: Multiplicadores oficiais B3 (450, 330, 100).
        """
        results: Dict[str, bool] = {
            "put_call_parity_black76": True,
            "exercise_quantity_conservation": True,
            "exercise_zero_sum_financial_balance": True,
            "intrinsic_minimum_tick_threshold": True,
            "contrary_instruction_respected": True,
            "greeks_monotonicity_and_bounds": True,
            "premium_non_negativity": True,
            "lower_bound_discounted_intrinsic": True,
            "commodity_specifications_valid": True,
        }

        # 1. Especificações contratuais válidas
        for c in self.contracts.values():
            spec = c.specification
            if spec.commodity == CommodityType.SOYBEAN and spec.contract_size != Decimal("450"):
                results["commodity_specifications_valid"] = False
            if spec.commodity == CommodityType.CORN and spec.contract_size != Decimal("450"):
                results["commodity_specifications_valid"] = False
            if spec.commodity == CommodityType.LIVE_CATTLE and spec.contract_size != Decimal("330"):
                results["commodity_specifications_valid"] = False
            if spec.commodity == CommodityType.ARABICA_COFFEE and spec.contract_size != Decimal("100"):
                results["commodity_specifications_valid"] = False

        # 2. Invariantes de Precificação Black-76 (Paridade, Não-negatividade e Gregas)
        for ticker, pr in self.pricing_cache.items():
            if not pr.parity_holds or pr.put_call_parity_diff > DEFAULT_PARITY_TOLERANCE:
                results["put_call_parity_black76"] = False

            if pr.call_price < Decimal("0.00") or pr.put_price < Decimal("0.00"):
                results["premium_non_negativity"] = False

            # Limite inferior descontado
            disc = pr.discount_factor
            F = pr.forward_price
            K = pr.strike
            min_c = max(Decimal("0.00"), round_four((F - K) * disc))
            min_p = max(Decimal("0.00"), round_four((K - F) * disc))
            if pr.call_price < min_c - DEFAULT_PARITY_TOLERANCE:
                results["lower_bound_discounted_intrinsic"] = False
            if pr.put_price < min_p - DEFAULT_PARITY_TOLERANCE:
                results["lower_bound_discounted_intrinsic"] = False

            # Gregas
            cg = pr.call_greeks
            pg = pr.put_greeks
            disc_tol = disc + Decimal("0.0001")
            if not (Decimal("-0.0001") <= cg.delta <= disc_tol):
                results["greeks_monotonicity_and_bounds"] = False
            if not (-disc_tol <= pg.delta <= Decimal("0.0001")):
                results["greeks_monotonicity_and_bounds"] = False
            if cg.gamma < Decimal("-0.000001") or cg.vega < Decimal("-0.000001"):
                results["greeks_monotonicity_and_bounds"] = False

        # 3. Invariantes de Exercício e Conversão em Futuros
        exercised_events = [
            e for e in self.exercise_events if e.disposition == ExerciseDisposition.AUTOMATIC_EXERCISED
        ]
        total_options_exercised = sum(e.contracts_exercised for e in exercised_events)

        long_futures_created = sum(f.quantity for f in self.created_futures if f.side == PositionSide.LONG)
        short_futures_created = sum(f.quantity for f in self.created_futures if f.side == PositionSide.SHORT)

        # Lei de Conservação Física: Quantidade_Futuros_Criada == Quantidade_Opções_Exercidas
        if long_futures_created != total_options_exercised or short_futures_created != total_options_exercised:
            results["exercise_quantity_conservation"] = False

        # Lei de Conservação Financeira Zero-Sum
        total_holder_mtm = sum(e.holder_mtm_gain for e in self.exercise_events)
        total_writer_mtm = sum(e.writer_mtm_gain for e in self.exercise_events)
        net_exercise_mtm = round_cents(total_holder_mtm + total_writer_mtm)
        if net_exercise_mtm != Decimal("0.00"):
            results["exercise_zero_sum_financial_balance"] = False

        # Threshold de Tick e Instruções Contrárias
        for e in self.exercise_events:
            if e.disposition == ExerciseDisposition.AUTOMATIC_EXERCISED:
                if e.intrinsic_value_unit < e.tick_size:
                    results["intrinsic_minimum_tick_threshold"] = False

            if e.disposition == ExerciseDisposition.CONTRARY_INSTRUCTION:
                if e.contracts_exercised != 0 or e.holder_future_position_id is not None:
                    results["contrary_instruction_respected"] = False

        if raise_on_error:
            for inv_name, status in results.items():
                if not status:
                    raise CommodityOptionInvariantError(
                        f"Violação da invariante formal B3: '{inv_name}'",
                        law_code=f"LAW-FL064-{inv_name.upper()}",
                    )

        return results

    # =======================================================================
    # Serialização e Carga de Fixtures
    # =======================================================================

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado integral do motor para dicionário JSON-compliant."""
        return {
            "version": "1.0.0",
            "flow_id": "FL-064",
            "risk_free_rate_annual": str(self.risk_free_rate_annual),
            "contracts": [c.to_dict() for c in self.contracts.values()],
            "positions": [p.to_dict() for p in self.positions.values()],
            "pricing_cache": {k: v.to_dict() for k, v in self.pricing_cache.items()},
            "exercise_events": [e.to_dict() for e in self.exercise_events],
            "created_futures": [f.to_dict() for f in self.created_futures],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CommodityOptionsEngine:
        """Restaura o estado do motor a partir de um dicionário serializado."""
        engine = cls(risk_free_rate_annual=to_decimal(data.get("risk_free_rate_annual", "0.1075"), "rate"))

        for c_data in data.get("contracts", []):
            engine.register_contract(AgriculturalOptionContract.from_dict(c_data))

        for p_data in data.get("positions", []):
            engine.add_position(ParticipantPosition.from_dict(p_data))

        for k, pr_data in data.get("pricing_cache", {}).items():
            engine.pricing_cache[k] = Black76PricingResult.from_dict(pr_data)

        for e_data in data.get("exercise_events", []):
            engine.exercise_events.append(ExerciseEvent.from_dict(e_data))

        for f_data in data.get("created_futures", []):
            engine.created_futures.append(CreatedFuturePosition.from_dict(f_data))

        return engine

    @classmethod
    def load_from_fixture(cls, filepath: Union[str, Path]) -> CommodityOptionsEngine:
        """Carrega e instancia o motor a partir de um arquivo fixture JSON B3."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Arquivo de fixture B3 não encontrado: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        engine = cls(
            risk_free_rate_annual=to_decimal(
                data.get("market_context", {}).get("risk_free_rate_annual", "0.1075"), "rate"
            )
        )

        # Carregar contratos
        for c_data in data.get("contracts", []):
            engine.register_contract(AgriculturalOptionContract.from_dict(c_data))

        # Carregar posições
        for p_data in data.get("positions", []):
            engine.add_position(ParticipantPosition.from_dict(p_data))

        return engine
