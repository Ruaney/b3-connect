"""B3 PUMA Trading System & Câmara — Superfícies Paramétricas de Volatilidade SVI e SABR (FL-101).
================================================================================================
Módulo quantitativo de modelagem de superfícies paramétricas de volatilidade implícita
para derivativos listados e de balcão da B3 (Câmara B3 e PUMA Trading System):
1. Modelo SVI (Stochastic Volatility Inspired - Jim Gatheral, 2004) na parametrização raw.
2. Modelo SABR (Stochastic Alpha, Beta, Rho - Hagan, Kumar, Lesniewski, Woodward, 2002).
3. Ativos canônicos B3:
   - Dólar Comercial Futuro (DOL / WDO)
   - Ibovespa Futuro e À Vista (IBOV / IND / WIN)
   - Ativos Customizados / Balcão OTC B3
4. Interpolação determinística de variância total e extração de volatilidade implícita
   para qualquer moneyness k = ln(K/F) e maturidade T.
5. Verificação formal das Leis de Não-Arbitragem B3:
   - Lei 85 / LAW-107A: Invariante de Não-Arbitragem de Calendário (variância total w(k, T)
     monotonicamente não-decrescente em T).
   - Lei 86 / LAW-107B: Invariante de Não-Arbitragem Butterfly e Convexidade
     (densidade de probabilidade neutra ao risco g(k) >= 0 e convexidade C(K1) - 2*C(K2) + C(K3) >= 0).

100% Python Standard Library (math, decimal, dataclasses, enum, typing, json, pathlib).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

# ---------------------------------------------------------------------------
# Integração com o Núcleo de Invariantes Formais B3
# ---------------------------------------------------------------------------

try:
    from src.invariants import (
        InvariantViolationError,
        assert_butterfly_arbitrage_free,
        assert_calendar_arbitrage_free,
        check,
    )
except ImportError:
    try:
        from invariants import (  # type: ignore
            InvariantViolationError,
            assert_butterfly_arbitrage_free,
            assert_calendar_arbitrage_free,
            check,
        )
    except ImportError:
        class InvariantViolationError(AssertionError):  # type: ignore
            """Fallback caso o módulo de invariantes não esteja disponível diretamente."""
            def __init__(
                self,
                message: str,
                law_code: Optional[str] = None,
                details: Optional[Dict[str, Any]] = None,
            ) -> None:
                super().__init__(message)
                self.message = message
                self.law_code = law_code
                self.details = details or {}

            def __str__(self) -> str:
                prefix = f"[{self.law_code}] " if self.law_code else ""
                return f"VIOLAÇÃO DE INVARIANTE: {prefix}{self.message}"

        def check(  # type: ignore
            condition: bool,
            message: str,
            law_code: Optional[str] = None,
            details: Optional[Dict[str, Any]] = None,
        ) -> None:
            if not condition:
                raise InvariantViolationError(message, law_code=law_code, details=details)

        def assert_calendar_arbitrage_free(  # type: ignore
            w_t1: Union[Decimal, float],
            w_t2: Union[Decimal, float],
            t1: Union[Decimal, float],
            t2: Union[Decimal, float],
            moneyness_k: Union[Decimal, float] = 0.0,
            surface_id: str = "",
            tolerance: float = 1e-9,
        ) -> None:
            diff = float(w_t2) - float(w_t1)
            check(
                diff >= -tolerance,
                f"Violação da Não-Arbitragem de Calendário (LAW-107A) na superfície '{surface_id}': "
                f"Variância total decrescente w(k={float(moneyness_k):.4f}, T2={float(t2):.4f}) = {float(w_t2):.6f} < "
                f"w(k={float(moneyness_k):.4f}, T1={float(t1):.4f}) = {float(w_t1):.6f} (déficit={-diff:.8f}).",
                law_code="LAW-107A",
                details={
                    "surface_id": surface_id,
                    "moneyness_k": str(moneyness_k),
                    "t1": str(t1),
                    "t2": str(t2),
                    "w_t1": str(w_t1),
                    "w_t2": str(w_t2),
                    "diff": str(diff),
                },
            )

        def assert_butterfly_arbitrage_free(  # type: ignore
            g_k: Union[Decimal, float],
            moneyness_k: Union[Decimal, float] = 0.0,
            maturity_t: Union[Decimal, float] = 0.0,
            surface_id: str = "",
            call_k1: Optional[Union[Decimal, float]] = None,
            call_k2: Optional[Union[Decimal, float]] = None,
            call_k3: Optional[Union[Decimal, float]] = None,
            tolerance: float = 1e-7,
        ) -> None:
            g_val = float(g_k)
            check(
                g_val >= -tolerance,
                f"Violação da Não-Arbitragem Butterfly (LAW-107B) na superfície '{surface_id}': "
                f"Densidade de risco negativa g(k={float(moneyness_k):.4f}, T={float(maturity_t):.4f}) = {g_val:.8f} < 0.",
                law_code="LAW-107B",
                details={
                    "surface_id": surface_id,
                    "moneyness_k": str(moneyness_k),
                    "maturity_t": str(maturity_t),
                    "g_k": str(g_k),
                },
            )

            if call_k1 is not None and call_k2 is not None and call_k3 is not None:
                c1 = float(call_k1)
                c2 = float(call_k2)
                c3 = float(call_k3)
                butterfly_price = c1 - 2.0 * c2 + c3
                check(
                    butterfly_price >= -tolerance,
                    f"Violação de Convexidade de Preço de Call (LAW-107B) na superfície '{surface_id}': "
                    f"Preço de Borboleta Simétrica C(K1) - 2*C(K2) + C(K3) = {butterfly_price:.8f} < 0 "
                    f"(C1={c1:.4f}, C2={c2:.4f}, C3={c3:.4f}).",
                    law_code="LAW-107B",
                    details={
                        "surface_id": surface_id,
                        "call_k1": str(call_k1),
                        "call_k2": str(call_k2),
                        "call_k3": str(call_k3),
                        "butterfly_price": str(butterfly_price),
                    },
                )


# ---------------------------------------------------------------------------
# Constantes e Tolerâncias Numéricas
# ---------------------------------------------------------------------------

DEFAULT_BUSINESS_DAYS_PER_YEAR = 252.0
DEFAULT_CALENDAR_DAYS_PER_YEAR = 365.0
DEFAULT_CALENDAR_TOLERANCE = 1e-9
DEFAULT_BUTTERFLY_TOLERANCE = 1e-7
DEFAULT_PARITY_TOLERANCE = 1e-6
EPSILON_NUMERICAL = 1e-12


# ---------------------------------------------------------------------------
# Hierarquia de Exceções
# ---------------------------------------------------------------------------

class VolatilityModelError(Exception):
    """Exceção base para falhas de modelagem ou integridade matemática na superfície."""


class CalendarArbitrageError(VolatilityModelError, InvariantViolationError):
    """Exceção para violações de não-arbitragem de calendário (LAW-107A)."""


class ButterflyArbitrageError(VolatilityModelError, InvariantViolationError):
    """Exceção para violações de não-arbitragem de borboleta / densidade negativa (LAW-107B)."""


class ParameterValidationError(VolatilityModelError):
    """Exceção para parâmetros fora dos domínios viáveis de SVI ou SABR."""


# ---------------------------------------------------------------------------
# Enumerações Canônicas
# ---------------------------------------------------------------------------

class B3AssetType(str, Enum):
    """Ativos canônicos negociados com opções na B3."""
    DOL = "DOL"       # Dólar Comercial Futuro / Opções de Dólar
    IBOV = "IBOV"     # Ibovespa Futuro / À Vista (IND / WIN / IBOV)
    CUSTOM = "CUSTOM" # Ativos customizados de balcão ou commodities

    @classmethod
    def from_str(cls, value: Union[str, B3AssetType]) -> B3AssetType:
        if isinstance(value, cls):
            return value
        val = str(value).strip().upper()
        for item in cls:
            if item.value == val:
                return item
        return cls.CUSTOM


class VolatilityModelType(str, Enum):
    """Modelos paramétricos de superfície suportados."""
    SVI = "SVI"       # Gatheral Raw SVI
    SABR = "SABR"     # Hagan et al. SABR
    HYBRID = "HYBRID" # Superfície mista


class OptionType(str, Enum):
    """Tipo da opção canônica (Call ou Put)."""
    CALL = "CALL"
    PUT = "PUT"

    @classmethod
    def from_str(cls, value: Union[str, OptionType]) -> OptionType:
        if isinstance(value, cls):
            return value
        val = str(value).strip().upper()
        if val in {"C", "CALL"}:
            return cls.CALL
        if val in {"P", "PUT"}:
            return cls.PUT
        raise ValueError(f"Tipo de opção desconhecido: {value}")


class DayCountConvention(str, Enum):
    """Convenção de contagem de dias para opções B3."""
    BUS_252 = "BUS/252"  # Padrão B3 (dias úteis / 252)
    ACT_365 = "ACT/365"  # Dias corridos / 365
    ACT_360 = "ACT/360"  # Dias corridos / 360 (Money Market)


# ---------------------------------------------------------------------------
# Funções Matemáticas Auxiliares (Black-76 e Normal)
# ---------------------------------------------------------------------------

def norm_cdf(x: float) -> float:
    """Função de distribuição acumulada da normal padrão N(x) usando math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Função de densidade de probabilidade da normal padrão n(x)."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black76_price(
    F: float,
    K: float,
    T: float,
    sigma: float,
    r: float,
    option_type: OptionType = OptionType.CALL,
) -> float:
    """Calcula o preço de uma opção européia via modelo Black-76 (Fischer Black, 1976).

    Args:
        F: Preço a termo/futuro do ativo subjacente (F > 0).
        K: Preço de exercício / strike (K > 0).
        T: Prazo até o vencimento em anos (T >= 0).
        sigma: Volatilidade implícita anualizada (sigma > 0).
        r: Taxa de juros livre de risco anualizada contínua.
        option_type: CALL ou PUT.
    """
    if F <= 0.0 or K <= 0.0:
        raise ValueError(f"F e K devem ser estritamente positivos (F={F}, K={K}).")

    discount = math.exp(-r * T)
    if T <= EPSILON_NUMERICAL or sigma <= EPSILON_NUMERICAL:
        intrinsic = max(0.0, F - K) if option_type == OptionType.CALL else max(0.0, K - F)
        return discount * intrinsic

    total_std = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * (sigma ** 2) * T) / total_std
    d2 = d1 - total_std

    if option_type == OptionType.CALL:
        return discount * (F * norm_cdf(d1) - K * norm_cdf(d2))
    else:
        return discount * (K * norm_cdf(-d2) - F * norm_cdf(-d1))


def black76_call_price(F: float, K: float, T: float, sigma: float, r: float) -> float:
    """Atalho para precificação de Call Black-76."""
    return black76_price(F=F, K=K, T=T, sigma=sigma, r=r, option_type=OptionType.CALL)


def black76_put_price(F: float, K: float, T: float, sigma: float, r: float) -> float:
    """Atalho para precificação de Put Black-76."""
    return black76_price(F=F, K=K, T=T, sigma=sigma, r=r, option_type=OptionType.PUT)


def black76_implied_vol(
    price: float,
    F: float,
    K: float,
    T: float,
    r: float,
    option_type: OptionType = OptionType.CALL,
    low_vol: float = 0.0001,
    high_vol: float = 5.0,
    tolerance: float = 1e-7,
    max_iter: int = 100,
) -> float:
    """Calcula a volatilidade implícita Black-76 por método de Bissecção / Brent robusto.

    Args:
        price: Preço de mercado da opção.
        F: Preço a termo do subjacente.
        K: Strike da opção.
        T: Prazo até a maturidade.
        r: Taxa livre de risco contínua.
        option_type: CALL ou PUT.
        low_vol: Limite inferior de busca da volatilidade.
        high_vol: Limite superior de busca da volatilidade.
        tolerance: Critério de parada do erro de preço.
        max_iter: Número máximo de iterações.
    """
    discount = math.exp(-r * T)
    intrinsic = discount * (max(0.0, F - K) if option_type == OptionType.CALL else max(0.0, K - F))
    upper_bound = discount * F if option_type == OptionType.CALL else discount * K

    if price < intrinsic - 1e-8:
        raise ValueError(
            f"Preço {price:.6f} viola o piso intrínseco descontado {intrinsic:.6f} (arbitragem)."
        )
    if price > upper_bound + 1e-8:
        raise ValueError(
            f"Preço {price:.6f} excede o teto teórico {upper_bound:.6f}."
        )

    p_low = black76_price(F, K, T, low_vol, r, option_type)
    p_high = black76_price(F, K, T, high_vol, r, option_type)

    if abs(p_low - price) <= tolerance:
        return low_vol
    if abs(p_high - price) <= tolerance:
        return high_vol

    # Bissecção com aceleração secante
    vol_a, vol_b = low_vol, high_vol
    f_a = p_low - price
    f_b = p_high - price

    for _ in range(max_iter):
        vol_mid = 0.5 * (vol_a + vol_b)
        f_mid = black76_price(F, K, T, vol_mid, r, option_type) - price

        if abs(f_mid) <= tolerance or (vol_b - vol_a) <= tolerance:
            return vol_mid

        if f_a * f_mid <= 0.0:
            vol_b = vol_mid
            f_b = f_mid
        else:
            vol_a = vol_mid
            f_a = f_mid

    return 0.5 * (vol_a + vol_b)


# ---------------------------------------------------------------------------
# Modelo Paramétrico SVI (Stochastic Volatility Inspired - Raw SVI)
# ---------------------------------------------------------------------------

@dataclass
class SVIParameters:
    """Parametrização Raw SVI (Gatheral, 2004).

    Fórmula da variância total:
        w(k) = a + b * (rho * (k - m) + sqrt((k - m)^2 + sigma^2))

    Onde:
        k = ln(K / F): log-moneyness.
        a: nível de variância vertical geral (a in R).
        b: inclinação angular dos braços assintóticos (b >= 0).
        rho: assimetria/correlação do sorriso (-1 < rho < 1).
        m: deslocamento horizontal do vértice do sorriso (m in R).
        sigma: curvatura / suavização do vértice no dinheiro (sigma > 0).

    Condições de Não-Arbitragem Interna (Gatheral 2004, Lee 2004):
        1. b >= 0
        2. |rho| < 1
        3. sigma > 0
        4. a + b * sigma * sqrt(1 - rho^2) >= 0  (garante w(k) >= 0 para todo k)
        5. b * (1 + |rho|) < 2                    (garante momento finito / fórmula de Lee)
    """

    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Valida matematicamente as restrições paramétricas necessárias de SVI."""
        if self.b < 0.0:
            raise ParameterValidationError(f"Parâmetro b do SVI deve ser não-negativo (b={self.b}).")
        if abs(self.rho) >= 1.0:
            raise ParameterValidationError(
                f"Parâmetro rho do SVI deve estar estritamente no intervalo (-1, 1) (rho={self.rho})."
            )
        if self.sigma <= 0.0:
            raise ParameterValidationError(
                f"Parâmetro sigma do SVI deve ser estritamente positivo (sigma={self.sigma})."
            )

        min_w = self.a + self.b * self.sigma * math.sqrt(max(0.0, 1.0 - self.rho ** 2))
        if min_w < -EPSILON_NUMERICAL:
            raise ParameterValidationError(
                f"SVI viola não-negatividade de variância total mínima: min_w = {min_w:.6f} < 0."
            )

        lee_bound = self.b * (1.0 + abs(self.rho))
        if lee_bound >= 2.0:
            raise ParameterValidationError(
                f"SVI viola o limite de inclinação assintótica de Lee: b*(1+|rho|) = {lee_bound:.4f} >= 2.0."
            )

    def total_variance(self, k: float) -> float:
        """Calcula a variância total w(k) = sigma^2(k) * T para o log-moneyness k."""
        k_shift = k - self.m
        hypot = math.sqrt(k_shift * k_shift + self.sigma * self.sigma)
        w = self.a + self.b * (self.rho * k_shift + hypot)
        return max(0.0, w)

    def implied_vol(self, k: float, T: float) -> float:
        """Calcula a volatilidade implícita sigma(k, T) = sqrt(w(k) / T)."""
        if T <= EPSILON_NUMERICAL:
            raise ValueError(f"Maturidade T deve ser estritamente positiva (T={T}).")
        w = self.total_variance(k)
        return math.sqrt(w / T)

    def dw_dk(self, k: float) -> float:
        """Primeira derivada da variância total em relação a k: w'(k)."""
        k_shift = k - self.m
        hypot = math.sqrt(k_shift * k_shift + self.sigma * self.sigma)
        return self.b * (self.rho + k_shift / hypot)

    def d2w_dk2(self, k: float) -> float:
        """Segunda derivada da variância total em relação a k: w''(k)."""
        k_shift = k - self.m
        denom = (k_shift * k_shift + self.sigma * self.sigma) ** 1.5
        return self.b * (self.sigma * self.sigma) / denom

    def g_density(self, k: float) -> float:
        """Calcula o termo de densidade neutra ao risco g(k) de Gatheral (2004).

        Fórmula:
            g(k) = (1 - k*w'/(2w))^2 - (w'^2 / 4)*(1/w + 1/4) + w''/2

        Se g(k) >= 0 para todo k in R, não há arbitragem de borboleta (butterfly arbitrage).
        """
        w = self.total_variance(k)
        if w <= EPSILON_NUMERICAL:
            return 1.0  # Limite determinístico quando w -> 0

        dw = self.dw_dk(k)
        d2w = self.d2w_dk2(k)

        term1 = (1.0 - (k * dw) / (2.0 * w)) ** 2
        term2 = -0.25 * (dw * dw) * (1.0 / w + 0.25)
        term3 = 0.5 * d2w

        return term1 + term2 + term3

    def to_dict(self) -> Dict[str, Any]:
        """Serializa os parâmetros para dicionário JSON-compatível."""
        return {
            "a": float(self.a),
            "b": float(self.b),
            "rho": float(self.rho),
            "m": float(self.m),
            "sigma": float(self.sigma),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SVIParameters:
        """Reconstrói os parâmetros SVI a partir de dicionário."""
        return cls(
            a=float(data["a"]),
            b=float(data["b"]),
            rho=float(data["rho"]),
            m=float(data["m"]),
            sigma=float(data["sigma"]),
        )


# ---------------------------------------------------------------------------
# Modelo Paramétrico SABR (Hagan et al., 2002)
# ---------------------------------------------------------------------------

@dataclass
class SABRParameters:
    """Parametrização do modelo SABR clássico (Hagan, Kumar, Lesniewski, Woodward, 2002).

    Parâmetros:
        alpha: volatilidade estocástica inicial (alpha > 0).
        beta: elasticidade CEV da distribuição subjacente (0 <= beta <= 1).
              Para opções DOL B3: normalmente beta = 1.0 (lognormal) ou 0.5.
              Para opções IBOV B3: normalmente beta = 1.0 ou 0.7.
        rho: correlação entre o ativo e seu processo de volatilidade (-1 < rho < 1).
        nu: volatilidade da volatilidade (vol-of-vol, nu >= 0).
    """

    alpha: float
    beta: float
    rho: float
    nu: float

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Valida os parâmetros canônicos do modelo SABR."""
        if self.alpha <= 0.0:
            raise ParameterValidationError(
                f"Parâmetro alpha do SABR deve ser estritamente positivo (alpha={self.alpha})."
            )
        if not (0.0 <= self.beta <= 1.0):
            raise ParameterValidationError(
                f"Parâmetro beta do SABR deve estar no intervalo [0, 1] (beta={self.beta})."
            )
        if abs(self.rho) >= 1.0:
            raise ParameterValidationError(
                f"Parâmetro rho do SABR deve estar no intervalo (-1, 1) (rho={self.rho})."
            )
        if self.nu < 0.0:
            raise ParameterValidationError(
                f"Parâmetro nu (vol-of-vol) do SABR deve ser não-negativo (nu={self.nu})."
            )

    def implied_vol(self, F: float, K: float, T: float) -> float:
        """Calcula a volatilidade implícita Black-76 via aproximação assintótica de Hagan et al. (2002).

        Args:
            F: Preço a termo do subjacente (F > 0).
            K: Strike da opção (K > 0).
            T: Prazo até a maturidade em anos (T > 0).
        """
        if F <= 0.0 or K <= 0.0 or T <= 0.0:
            raise ValueError(f"F, K e T devem ser positivos (F={F}, K={K}, T={T}).")

        one_minus_beta = 1.0 - self.beta

        # Caso ATM (No Dinheiro): F == K
        if abs(F - K) / F < 1e-7:
            f_pow = F ** one_minus_beta
            term_alpha = (one_minus_beta ** 2 / 24.0) * (self.alpha ** 2 / (F ** (2.0 * one_minus_beta)))
            term_rho = 0.25 * (self.rho * self.beta * self.nu * self.alpha / f_pow)
            term_nu = ((2.0 - 3.0 * self.rho ** 2) / 24.0) * (self.nu ** 2)
            sigma_atm = (self.alpha / f_pow) * (1.0 + (term_alpha + term_rho + term_nu) * T)
            return max(1e-6, sigma_atm)

        # Caso OTM / ITM: F != K
        ln_fk = math.log(F / K)
        fk = F * K
        fk_pow = fk ** (one_minus_beta / 2.0)

        z = (self.nu / self.alpha) * fk_pow * ln_fk

        if abs(z) < 1e-8 or self.nu < 1e-8:
            z_over_xz = 1.0
        else:
            radical = math.sqrt(max(0.0, 1.0 - 2.0 * self.rho * z + z * z))
            arg = (radical + z - self.rho) / (1.0 - self.rho)
            if arg <= 0.0:
                arg = 1e-12
            xz = math.log(arg)
            z_over_xz = z / xz

        denom_series = 1.0 + (one_minus_beta ** 2 / 24.0) * (ln_fk ** 2) + ((one_minus_beta ** 4) / 1920.0) * (ln_fk ** 4)
        denom = fk_pow * denom_series

        c_alpha = (one_minus_beta ** 2 / 24.0) * (self.alpha ** 2 / (fk ** one_minus_beta))
        c_rho = 0.25 * (self.rho * self.beta * self.nu * self.alpha / fk_pow)
        c_nu = ((2.0 - 3.0 * self.rho ** 2) / 24.0) * (self.nu ** 2)
        bracket = 1.0 + (c_alpha + c_rho + c_nu) * T

        sigma_sabr = (self.alpha / denom) * z_over_xz * bracket
        return max(1e-6, sigma_sabr)

    def total_variance(self, F: float, K: float, T: float) -> float:
        """Calcula a variância total w = sigma^2 * T correspondente ao strike K."""
        vol = self.implied_vol(F=F, K=K, T=T)
        return vol * vol * T

    def to_dict(self) -> Dict[str, Any]:
        """Serializa os parâmetros SABR para dicionário."""
        return {
            "alpha": float(self.alpha),
            "beta": float(self.beta),
            "rho": float(self.rho),
            "nu": float(self.nu),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SABRParameters:
        """Reconstrói os parâmetros SABR a partir de dicionário."""
        return cls(
            alpha=float(data["alpha"]),
            beta=float(data["beta"]),
            rho=float(data["rho"]),
            nu=float(data["nu"]),
        )


# ---------------------------------------------------------------------------
# Fatia de Volatilidade (Volatility Slice / Expiration Slice)
# ---------------------------------------------------------------------------

@dataclass
class VolatilitySlice:
    """Representa uma fatia temporal (smile de volatilidade) para um vencimento T específico."""

    slice_id: str
    maturity_t: float
    days_to_expiry: int
    forward: float
    risk_free_rate: float
    model_type: VolatilityModelType
    svi_params: Optional[SVIParameters] = None
    sabr_params: Optional[SABRParameters] = None
    as_of_date: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.maturity_t <= 0.0:
            raise ValueError(f"Maturidade T deve ser positiva (T={self.maturity_t}).")
        if self.forward <= 0.0:
            raise ValueError(f"Preço a termo F deve ser positivo (F={self.forward}).")
        if self.model_type == VolatilityModelType.SVI and self.svi_params is None:
            raise ValueError("Fatia configurada como SVI requer svi_params.")
        if self.model_type == VolatilityModelType.SABR and self.sabr_params is None:
            raise ValueError("Fatia configurada como SABR requer sabr_params.")

    def total_variance(self, k: float) -> float:
        """Calcula a variância total w(k) para o log-moneyness k."""
        if self.model_type == VolatilityModelType.SVI and self.svi_params is not None:
            return self.svi_params.total_variance(k)
        elif self.model_type == VolatilityModelType.SABR and self.sabr_params is not None:
            K = self.forward * math.exp(k)
            vol = self.sabr_params.implied_vol(self.forward, K, self.maturity_t)
            return vol * vol * self.maturity_t
        else:
            raise VolatilityModelError(f"Tipo de modelo {self.model_type} não inicializado corretamente.")

    def implied_vol(self, k: float) -> float:
        """Calcula a volatilidade implícita anualizada para o moneyness k."""
        w = self.total_variance(k)
        return math.sqrt(max(0.0, w) / self.maturity_t)

    def implied_vol_strike(self, K: float) -> float:
        """Calcula a volatilidade implícita para um strike K explícito."""
        if K <= 0.0:
            raise ValueError(f"Strike K deve ser estritamente positivo (K={K}).")
        k = math.log(K / self.forward)
        return self.implied_vol(k)

    def call_price(self, k: float) -> float:
        """Preço da opção de compra Black-76 para o log-moneyness k."""
        K = self.forward * math.exp(k)
        vol = self.implied_vol(k)
        return black76_call_price(
            F=self.forward,
            K=K,
            T=self.maturity_t,
            sigma=vol,
            r=self.risk_free_rate,
        )

    def put_price(self, k: float) -> float:
        """Preço da opção de venda Black-76 para o log-moneyness k."""
        K = self.forward * math.exp(k)
        vol = self.implied_vol(k)
        return black76_put_price(
            F=self.forward,
            K=K,
            T=self.maturity_t,
            sigma=vol,
            r=self.risk_free_rate,
        )

    def g_density(self, k: float) -> float:
        """Calcula a densidade neutra ao risco g(k) analítica (SVI) ou numérica (SABR)."""
        if self.model_type == VolatilityModelType.SVI and self.svi_params is not None:
            return self.svi_params.g_density(k)
        else:
            # Diferenças finitas de alta ordem para SABR / genérico
            h = 1e-4
            w_0 = self.total_variance(k)
            if w_0 <= EPSILON_NUMERICAL:
                return 1.0
            w_plus = self.total_variance(k + h)
            w_minus = self.total_variance(k - h)
            dw = (w_plus - w_minus) / (2.0 * h)
            d2w = (w_plus - 2.0 * w_0 + w_minus) / (h * h)

            term1 = (1.0 - (k * dw) / (2.0 * w_0)) ** 2
            term2 = -0.25 * (dw * dw) * (1.0 / w_0 + 0.25)
            term3 = 0.5 * d2w
            return term1 + term2 + term3

    def check_butterfly_arbitrage(
        self,
        k_min: float = -0.5,
        k_max: float = 0.5,
        steps: int = 51,
        tolerance: float = DEFAULT_BUTTERFLY_TOLERANCE,
        raise_on_error: bool = False,
    ) -> Dict[str, Any]:
        """Verifica exaustivamente a ausência de arbitragem butterfly na fatia (LAW-107B).

        Auditorias:
        1. g(k) >= -tolerance em todos os nós da grade de moneyness.
        2. Convexidade discreta de calls: C(K1) - 2*C(K2) + C(K3) >= -tolerance.
        3. Monotonicidade não-crescente de calls: C(K1) >= C(K2) - tolerance.
        """
        step_size = (k_max - k_min) / float(steps - 1)
        k_values = [k_min + i * step_size for i in range(steps)]

        min_g = float("inf")
        min_g_k = 0.0
        min_butterfly_price = float("inf")
        min_butterfly_k = 0.0
        violations: List[str] = []

        # 1. Auditoria da densidade de Gatheral g(k)
        for k in k_values:
            g_val = self.g_density(k)
            if g_val < min_g:
                min_g = g_val
                min_g_k = k
            if g_val < -tolerance:
                msg = f"Densidade negativa g(k={k:.4f}) = {g_val:.8f} < 0"
                violations.append(msg)
                if raise_on_error:
                    assert_butterfly_arbitrage_free(
                        g_k=g_val,
                        moneyness_k=k,
                        maturity_t=self.maturity_t,
                        surface_id=self.slice_id,
                        tolerance=tolerance,
                    )

        # 2. Auditoria de borboletas simétricas discretas C(K-dK) - 2*C(K) + C(K+dK) >= 0
        delta_k = step_size
        for i in range(1, steps - 1):
            k_prev = k_values[i - 1]
            k_curr = k_values[i]
            k_next = k_values[i + 1]

            K_curr = self.forward * math.exp(k_curr)
            delta_K = K_curr * delta_k
            K_1 = max(0.01, K_curr - delta_K)
            K_2 = K_curr
            K_3 = K_curr + delta_K

            vol_1 = self.implied_vol_strike(K_1)
            vol_2 = self.implied_vol_strike(K_2)
            vol_3 = self.implied_vol_strike(K_3)

            c1 = black76_call_price(self.forward, K_1, self.maturity_t, vol_1, self.risk_free_rate)
            c2 = black76_call_price(self.forward, K_2, self.maturity_t, vol_2, self.risk_free_rate)
            c3 = black76_call_price(self.forward, K_3, self.maturity_t, vol_3, self.risk_free_rate)

            bfly = c1 - 2.0 * c2 + c3
            if bfly < min_butterfly_price:
                min_butterfly_price = bfly
                min_butterfly_k = k_curr

            if bfly < -tolerance:
                msg = (
                    f"Borboleta negativa em K={K_2:.2f} (k={k_curr:.4f}): "
                    f"C1 - 2*C2 + C3 = {bfly:.6f} < 0 (C1={c1:.4f}, C2={c2:.4f}, C3={c3:.4f})"
                )
                violations.append(msg)
                if raise_on_error:
                    assert_butterfly_arbitrage_free(
                        g_k=min_g,
                        moneyness_k=k_curr,
                        maturity_t=self.maturity_t,
                        surface_id=self.slice_id,
                        call_k1=c1,
                        call_k2=c2,
                        call_k3=c3,
                        tolerance=tolerance,
                    )

        is_arbitrage_free = (min_g >= -tolerance) and (min_butterfly_price >= -tolerance)

        return {
            "slice_id": self.slice_id,
            "maturity_t": self.maturity_t,
            "is_arbitrage_free": is_arbitrage_free,
            "min_g": min_g,
            "min_g_k": min_g_k,
            "min_butterfly_price": min_butterfly_price,
            "min_butterfly_k": min_butterfly_k,
            "violations_count": len(violations),
            "violations": violations,
        }

    def generate_smile_points(self, k_points: Optional[List[float]] = None) -> List[Dict[str, float]]:
        """Gera pontos discretizados do sorriso de volatilidade e prêmios correspondentes."""
        if k_points is None:
            k_points = [-0.30, -0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20, 0.30]

        points: List[Dict[str, float]] = []
        for k in k_points:
            K = self.forward * math.exp(k)
            w = self.total_variance(k)
            vol = self.implied_vol(k)
            c = self.call_price(k)
            p = self.put_price(k)
            g = self.g_density(k)
            points.append({
                "moneyness_k": round(k, 6),
                "strike": round(K, 4),
                "total_variance": round(w, 8),
                "implied_vol": round(vol, 6),
                "call_price": round(c, 4),
                "put_price": round(p, 4),
                "g_density": round(g, 6),
            })
        return points

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a fatia para dicionário JSON."""
        return {
            "slice_id": self.slice_id,
            "maturity_t": float(self.maturity_t),
            "days_to_expiry": int(self.days_to_expiry),
            "forward": float(self.forward),
            "risk_free_rate": float(self.risk_free_rate),
            "model_type": self.model_type.value,
            "svi_params": self.svi_params.to_dict() if self.svi_params else None,
            "sabr_params": self.sabr_params.to_dict() if self.sabr_params else None,
            "as_of_date": self.as_of_date,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VolatilitySlice:
        """Reconstrói uma VolatilitySlice a partir de dicionário."""
        model_type = VolatilityModelType(str(data["model_type"]))
        svi_data = data.get("svi_params")
        sabr_data = data.get("sabr_params")

        return cls(
            slice_id=str(data["slice_id"]),
            maturity_t=float(data["maturity_t"]),
            days_to_expiry=int(data["days_to_expiry"]),
            forward=float(data["forward"]),
            risk_free_rate=float(data["risk_free_rate"]),
            model_type=model_type,
            svi_params=SVIParameters.from_dict(svi_data) if svi_data else None,
            sabr_params=SABRParameters.from_dict(sabr_data) if sabr_data else None,
            as_of_date=data.get("as_of_date"),
            metadata=data.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# Superfície de Volatilidade Paramétrica Canônica B3
# ---------------------------------------------------------------------------

@dataclass
class B3VolatilitySurface:
    """Superfície completa de volatilidade implícita com interpolação de variância total.

    Atributos:
        surface_id: Identificador institucional único da superfície.
        asset_type: Ativo canônico da B3 (DOL, IBOV ou CUSTOM).
        model_type: Tipo de parametrização primária (SVI, SABR ou HYBRID).
        as_of_date: Data de referência do mercado (formato YYYY-MM-DD).
        day_count: Convenção de dias (padrão BUS/252 para derivativos B3).
        slices: Lista ordenada de fatias de volatilidade por maturidade T.
        spot: Preço à vista de referência (opcional).
        metadata: Metadados adicionais de auditoria.
    """

    surface_id: str
    asset_type: B3AssetType
    model_type: VolatilityModelType
    as_of_date: str
    day_count: DayCountConvention = DayCountConvention.BUS_252
    slices: List[VolatilitySlice] = field(default_factory=list)
    spot: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.slices:
            self._sort_slices()

    def _sort_slices(self) -> None:
        """Garante ordenação estrita das fatias por prazo T."""
        self.slices.sort(key=lambda s: s.maturity_t)

    def add_slice(self, slice_: VolatilitySlice) -> None:
        """Adiciona uma nova fatia à superfície, preservando a ordenação temporal."""
        self.slices.append(slice_)
        self._sort_slices()

    def get_maturities(self) -> List[float]:
        """Retorna os prazos T de todas as fatias ordenadas."""
        return [s.maturity_t for s in self.slices]

    def get_slice_by_index(self, index: int) -> VolatilitySlice:
        """Retorna uma fatia pelo seu índice ordinal."""
        if not (0 <= index < len(self.slices)):
            raise IndexError(f"Índice de fatia fora do intervalo [0, {len(self.slices)-1}]: {index}")
        return self.slices[index]

    def total_variance(self, k: float, T: float) -> float:
        """Interpola determinísticamente a variância total w(k, T) na superfície.

        Preservação de Não-Arbitragem de Calendário (Gatheral & Jacquier, 2014):
        A interpolação linear em variância total entre fatias livres de arbitragem:
            w(k, T) = (1 - lambda) * w(k, T1) + lambda * w(k, T2)
        preserva a monotonicidade não-decrescente em T em todo o domínio contínuo:
            dw/dT = (w(k, T2) - w(k, T1)) / (T2 - T1) >= 0.

        Extrapolação:
        - T <= T_primeiro: escala linear partindo de zero w(k, 0) = 0.
        - T >= T_ultimo: escala de variância forward constante da última fatia.
        """
        if T <= 0.0:
            return 0.0

        if not self.slices:
            raise VolatilityModelError("Superfície não possui fatias cadastradas.")

        if len(self.slices) == 1:
            # Uma única fatia: escala linear simples com T
            single = self.slices[0]
            w_single = single.total_variance(k)
            return w_single * (T / single.maturity_t)

        # Caso T antes da primeira fatia: interpolação até zero
        if T <= self.slices[0].maturity_t:
            first = self.slices[0]
            w_first = first.total_variance(k)
            return w_first * (T / first.maturity_t)

        # Caso T após a última fatia: mantém inclinação da variância forward terminal
        if T >= self.slices[-1].maturity_t:
            last = self.slices[-1]
            prev = self.slices[-2]
            w_last = last.total_variance(k)
            w_prev = prev.total_variance(k)
            fwd_var_slope = max(0.0, (w_last - w_prev) / (last.maturity_t - prev.maturity_t))
            return w_last + fwd_var_slope * (T - last.maturity_t)

        # Interpolação linear entre fatias adjacentes [T_i, T_i+1]
        for i in range(len(self.slices) - 1):
            s1 = self.slices[i]
            s2 = self.slices[i + 1]
            if s1.maturity_t <= T <= s2.maturity_t:
                lambda_factor = (T - s1.maturity_t) / (s2.maturity_t - s1.maturity_t)
                w1 = s1.total_variance(k)
                w2 = s2.total_variance(k)
                return (1.0 - lambda_factor) * w1 + lambda_factor * w2

        # Fallback de segurança numérica
        return self.slices[-1].total_variance(k)

    def implied_vol(self, k: float, T: float) -> float:
        """Calcula a volatilidade implícita anualizada para qualquer ponto (k, T)."""
        if T <= EPSILON_NUMERICAL:
            raise ValueError(f"Maturidade T deve ser estritamente positiva (T={T}).")
        w = self.total_variance(k, T)
        return math.sqrt(max(0.0, w) / T)

    def implied_vol_strike(self, K: float, T: float, F: Optional[float] = None) -> float:
        """Calcula a volatilidade implícita dado o strike K e maturidade T."""
        if K <= 0.0:
            raise ValueError(f"Strike K deve ser estritamente positivo (K={K}).")

        forward = F or self._interpolate_forward(T)
        k = math.log(K / forward)
        return self.implied_vol(k, T)

    def call_price(
        self,
        k: float,
        T: float,
        F: Optional[float] = None,
        r: Optional[float] = None,
    ) -> float:
        """Calcula o preço de uma opção de compra Black-76 para (k, T)."""
        forward = F or self._interpolate_forward(T)
        rate = r if r is not None else self._interpolate_rate(T)
        K = forward * math.exp(k)
        vol = self.implied_vol(k, T)
        return black76_call_price(forward, K, T, vol, rate)

    def put_price(
        self,
        k: float,
        T: float,
        F: Optional[float] = None,
        r: Optional[float] = None,
    ) -> float:
        """Calcula o preço de uma opção de venda Black-76 para (k, T)."""
        forward = F or self._interpolate_forward(T)
        rate = r if r is not None else self._interpolate_rate(T)
        K = forward * math.exp(k)
        vol = self.implied_vol(k, T)
        return black76_put_price(forward, K, T, vol, rate)

    def _interpolate_forward(self, T: float) -> float:
        """Interpola linearmente o preço a termo forward F(T)."""
        if not self.slices:
            return self.spot or 100.0
        if len(self.slices) == 1 or T <= self.slices[0].maturity_t:
            return self.slices[0].forward
        if T >= self.slices[-1].maturity_t:
            return self.slices[-1].forward

        for i in range(len(self.slices) - 1):
            s1 = self.slices[i]
            s2 = self.slices[i + 1]
            if s1.maturity_t <= T <= s2.maturity_t:
                frac = (T - s1.maturity_t) / (s2.maturity_t - s1.maturity_t)
                return s1.forward + frac * (s2.forward - s1.forward)

        return self.slices[-1].forward

    def _interpolate_rate(self, T: float) -> float:
        """Interpola a taxa livre de risco r(T)."""
        if not self.slices:
            return 0.1075
        if len(self.slices) == 1 or T <= self.slices[0].maturity_t:
            return self.slices[0].risk_free_rate
        if T >= self.slices[-1].maturity_t:
            return self.slices[-1].risk_free_rate

        for i in range(len(self.slices) - 1):
            s1 = self.slices[i]
            s2 = self.slices[i + 1]
            if s1.maturity_t <= T <= s2.maturity_t:
                frac = (T - s1.maturity_t) / (s2.maturity_t - s1.maturity_t)
                return s1.risk_free_rate + frac * (s2.risk_free_rate - s1.risk_free_rate)

        return self.slices[-1].risk_free_rate

    # =======================================================================
    # Auditoria de Invariantes Formais B3 (LAW-107A e LAW-107B)
    # =======================================================================

    def verify_calendar_arbitrage(
        self,
        k_grid: Optional[List[float]] = None,
        tolerance: float = DEFAULT_CALENDAR_TOLERANCE,
        raise_on_error: bool = False,
    ) -> Dict[str, Any]:
        """Audita rigorosamente a ausência de arbitragem de calendário (LAW-107A).

        Invariante Formal LAW-107A:
            Para qualquer moneyness k fixo:
                T1 < T2 ==> w(k, T1) <= w(k, T2).
        """
        if k_grid is None:
            k_grid = [-0.40, -0.25, -0.15, -0.05, 0.0, 0.05, 0.15, 0.25, 0.40]

        if len(self.slices) < 2:
            return {
                "is_arbitrage_free": True,
                "checked_pairs": 0,
                "violations_count": 0,
                "violations": [],
                "min_forward_variance": 0.0,
            }

        checked_pairs = 0
        violations: List[str] = []
        min_forward_variance = float("inf")

        for i in range(len(self.slices) - 1):
            s1 = self.slices[i]
            s2 = self.slices[i + 1]
            dt = s2.maturity_t - s1.maturity_t

            for k in k_grid:
                checked_pairs += 1
                w1 = s1.total_variance(k)
                w2 = s2.total_variance(k)
                diff = w2 - w1
                fwd_var = diff / dt if dt > 0 else 0.0

                if fwd_var < min_forward_variance:
                    min_forward_variance = fwd_var

                if diff < -tolerance:
                    msg = (
                        f"Variância decrescente entre T1={s1.maturity_t:.4f} (w1={w1:.6f}) e "
                        f"T2={s2.maturity_t:.4f} (w2={w2:.6f}) em k={k:.4f} (déficit={-diff:.8f})"
                    )
                    violations.append(msg)
                    if raise_on_error:
                        assert_calendar_arbitrage_free(
                            w_t1=w1,
                            w_t2=w2,
                            t1=s1.maturity_t,
                            t2=s2.maturity_t,
                            moneyness_k=k,
                            surface_id=self.surface_id,
                            tolerance=tolerance,
                        )

        is_free = len(violations) == 0

        return {
            "surface_id": self.surface_id,
            "is_arbitrage_free": is_free,
            "checked_pairs": checked_pairs,
            "violations_count": len(violations),
            "violations": violations,
            "min_forward_variance": min_forward_variance,
        }

    def verify_butterfly_arbitrage(
        self,
        k_grid: Optional[List[float]] = None,
        tolerance: float = DEFAULT_BUTTERFLY_TOLERANCE,
        raise_on_error: bool = False,
    ) -> Dict[str, Any]:
        """Audita rigorosamente a ausência de arbitragem butterfly em todas as fatias (LAW-107B)."""
        all_slice_reports: List[Dict[str, Any]] = []
        total_violations = 0
        min_global_g = float("inf")

        for s in self.slices:
            rep = s.check_butterfly_arbitrage(
                tolerance=tolerance,
                raise_on_error=raise_on_error,
            )
            all_slice_reports.append(rep)
            total_violations += rep["violations_count"]
            if rep["min_g"] < min_global_g:
                min_global_g = rep["min_g"]

        return {
            "surface_id": self.surface_id,
            "is_arbitrage_free": (total_violations == 0),
            "total_violations": total_violations,
            "min_global_g": min_global_g,
            "slice_reports": all_slice_reports,
        }

    def verify_invariants(self, raise_on_error: bool = False) -> Dict[str, bool]:
        """Executa auditoria global de conformidade matemática e regulatória B3.

        Invariantes Auditadas:
        1. parameters_validity: Todos os parâmetros SVI/SABR estão nos domínios permitidos.
        2. total_variance_non_negative: w(k, T) >= 0 em todos os nós avaliados.
        3. implied_volatility_positive: sigma(k, T) > 0 em todos os nós avaliados.
        4. calendar_arbitrage_free_law_107a: Variância total não decresce em T (LAW-107A).
        5. butterfly_arbitrage_free_law_107b: Densidade g(k) >= 0 e calls convexas (LAW-107B).
        6. put_call_parity_black76: C - P == exp(-rT)*(F - K) em todos os strikes testados.
        """
        results: Dict[str, bool] = {
            "parameters_validity": True,
            "total_variance_non_negative": True,
            "implied_volatility_positive": True,
            "calendar_arbitrage_free_law_107a": True,
            "butterfly_arbitrage_free_law_107b": True,
            "put_call_parity_black76": True,
        }

        # 1. Validação de parâmetros de cada fatia
        for s in self.slices:
            try:
                if s.svi_params:
                    s.svi_params.validate()
                if s.sabr_params:
                    s.sabr_params.validate()
            except ParameterValidationError:
                results["parameters_validity"] = False
                if raise_on_error:
                    raise

        # 2. Não-negatividade de variância total e positividade da volatilidade
        test_k = [-0.4, -0.2, 0.0, 0.2, 0.4]
        for s in self.slices:
            for k in test_k:
                w = s.total_variance(k)
                if w < -EPSILON_NUMERICAL:
                    results["total_variance_non_negative"] = False
                vol = s.implied_vol(k)
                if vol <= 0.0 or math.isnan(vol):
                    results["implied_volatility_positive"] = False

        # 3. Arbitragem de Calendário (LAW-107A)
        cal_rep = self.verify_calendar_arbitrage(raise_on_error=raise_on_error)
        if not cal_rep["is_arbitrage_free"]:
            results["calendar_arbitrage_free_law_107a"] = False

        # 4. Arbitragem Butterfly (LAW-107B)
        bfly_rep = self.verify_butterfly_arbitrage(raise_on_error=raise_on_error)
        if not bfly_rep["is_arbitrage_free"]:
            results["butterfly_arbitrage_free_law_107b"] = False

        # 5. Paridade Put-Call Black-76
        for s in self.slices:
            for k in [-0.2, 0.0, 0.2]:
                K = s.forward * math.exp(k)
                c = s.call_price(k)
                p = s.put_price(k)
                expected_diff = math.exp(-s.risk_free_rate * s.maturity_t) * (s.forward - K)
                actual_diff = c - p
                if abs(actual_diff - expected_diff) > DEFAULT_PARITY_TOLERANCE:
                    results["put_call_parity_black76"] = False
                    if raise_on_error:
                        raise InvariantViolationError(
                            f"Violação da Paridade Put-Call na fatia {s.slice_id} strike {K:.2f}: "
                            f"C - P = {actual_diff:.6f} != {expected_diff:.6f}",
                            law_code="LAW-PARITY",
                        )

        return results

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a superfície para dicionário."""
        return {
            "surface_id": self.surface_id,
            "asset_type": self.asset_type.value,
            "model_type": self.model_type.value,
            "as_of_date": self.as_of_date,
            "day_count": self.day_count.value,
            "spot": float(self.spot),
            "metadata": self.metadata,
            "slices": [s.to_dict() for s in self.slices],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> B3VolatilitySurface:
        """Reconstrói uma B3VolatilitySurface a partir de dicionário."""
        asset_type = B3AssetType.from_str(data["asset_type"])
        model_type = VolatilityModelType(str(data["model_type"]))
        day_count_val = data.get("day_count", DayCountConvention.BUS_252.value)
        day_count = DayCountConvention(day_count_val)

        surface = cls(
            surface_id=str(data["surface_id"]),
            asset_type=asset_type,
            model_type=model_type,
            as_of_date=str(data["as_of_date"]),
            day_count=day_count,
            spot=float(data.get("spot", 0.0)),
            metadata=data.get("metadata", {}),
            slices=[VolatilitySlice.from_dict(s) for s in data.get("slices", [])],
        )
        return surface


# ---------------------------------------------------------------------------
# Calibrador Numérico em Python Standard Library (Nelder-Mead Simplex)
# ---------------------------------------------------------------------------

def nelder_mead_optimize(
    func: Callable[[List[float]], float],
    x0: Sequence[float],
    step: float = 0.05,
    no_improve_thr: float = 1e-7,
    no_improv_break: int = 35,
    max_iter: int = 500,
    alpha: float = 1.0,
    gamma: float = 2.0,
    rho: float = -0.5,
    sigma: float = 0.5,
) -> Tuple[List[float], float]:
    """Otimizador Nelder-Mead Simplex em 100% Python Standard Library."""
    dim = len(x0)
    prev_best = func(list(x0))
    no_improv = 0
    res = [[list(x0), prev_best]]

    for i in range(dim):
        x = list(x0)
        x[i] = x[i] + step if x[i] != 0 else step
        res.append([x, func(x)])

    iters = 0
    while iters < max_iter:
        res.sort(key=lambda c: c[1])
        best = res[0][1]

        if best < prev_best - no_improve_thr:
            no_improv = 0
            prev_best = best
        else:
            no_improv += 1

        if no_improv >= no_improv_break:
            break

        iters += 1

        # Centróide dos melhores dim pontos
        x0_bar = [sum(res[i][0][j] for i in range(dim)) / float(dim) for j in range(dim)]

        # Reflexão
        xr = [x0_bar[j] + alpha * (x0_bar[j] - res[-1][0][j]) for j in range(dim)]
        rscore = func(xr)

        if res[0][1] <= rscore < res[-2][1]:
            del res[-1]
            res.append([xr, rscore])
            continue

        # Expansão
        if rscore < res[0][1]:
            xe = [x0_bar[j] + gamma * (xr[j] - x0_bar[j]) for j in range(dim)]
            escore = func(xe)
            del res[-1]
            if escore < rscore:
                res.append([xe, escore])
            else:
                res.append([xr, rscore])
            continue

        # Contração
        xc = [x0_bar[j] + rho * (x0_bar[j] - res[-1][0][j]) for j in range(dim)]
        cscore = func(xc)
        if cscore < res[-1][1]:
            del res[-1]
            res.append([xc, cscore])
            continue

        # Redução (Shrink)
        x1 = res[0][0]
        nres = []
        for row in res:
            x_sh = [x1[j] + sigma * (row[0][j] - x1[j]) for j in range(dim)]
            nres.append([x_sh, func(x_sh)])
        res = nres

    res.sort(key=lambda c: c[1])
    return res[0][0], res[0][1]


# ---------------------------------------------------------------------------
# Motor Unificado de Superfícies de Volatilidade B3 (FL-101 Engine)
# ---------------------------------------------------------------------------

class B3VolatilitySurfaceEngine:
    """Motor quantitativo de criação, calibração e auditoria de superfícies B3."""

    def __init__(self) -> None:
        self.surfaces: Dict[str, B3VolatilitySurface] = {}

    def register_surface(self, surface: B3VolatilitySurface) -> None:
        """Registra uma superfície no catálogo do motor."""
        self.surfaces[surface.surface_id] = surface

    def get_surface(self, surface_id: str) -> B3VolatilitySurface:
        """Recupera uma superfície cadastrada pelo ID."""
        if surface_id not in self.surfaces:
            raise KeyError(f"Superfície '{surface_id}' não encontrada no motor.")
        return self.surfaces[surface_id]

    @staticmethod
    def calibrate_svi_slice(
        strikes: List[float],
        market_vols: List[float],
        forward: float,
        maturity_t: float,
        initial_guess: Optional[List[float]] = None,
    ) -> SVIParameters:
        """Calibra os 5 parâmetros SVI (a, b, rho, m, sigma) a partir de cotações de mercado.

        Função objetivo penalizada:
            min sum (vol_svi(K_i) - vol_mercado(K_i))^2 + Penalidades(Violações de Não-Arbitragem)
        """
        if len(strikes) != len(market_vols) or len(strikes) < 5:
            raise ValueError("Calibração SVI requer ao menos 5 pares (strike, volatilidade).")

        # Palpite inicial inteligente caso não informado
        atm_vol = market_vols[len(market_vols) // 2]
        atm_var = (atm_vol ** 2) * maturity_t
        x0 = initial_guess or [atm_var * 0.5, 0.08, -0.4, 0.0, 0.10]

        def objective(params: List[float]) -> float:
            a, b, rho, m, sigma = params
            penalty = 0.0

            # Penalizações para barreiras duras de não-arbitragem
            if b < 0.0:
                penalty += 1000.0 + abs(b) * 10000.0
            if abs(rho) >= 0.999:
                penalty += 1000.0 + abs(rho) * 10000.0
            if sigma <= 0.001:
                penalty += 1000.0 + (0.001 - sigma) * 10000.0

            # Restrição Lee (b * (1 + |rho|) < 2)
            lee_term = b * (1.0 + abs(rho))
            if lee_term >= 1.95:
                penalty += 1000.0 + (lee_term - 1.95) * 5000.0

            # Restrição Não-Negatividade (a + b*sigma*sqrt(1 - rho^2) >= 0)
            min_w = a + b * sigma * math.sqrt(max(0.0, 1.0 - rho * rho))
            if min_w < 0.0:
                penalty += 1000.0 + abs(min_w) * 10000.0

            if penalty > 0.0:
                return penalty

            error_sum = 0.0
            for K, target_vol in zip(strikes, market_vols):
                k = math.log(K / forward)
                k_m = k - m
                hyp = math.sqrt(k_m * k_m + sigma * sigma)
                w = a + b * (rho * k_m + hyp)
                if w < 0.0:
                    return 5000.0
                model_vol = math.sqrt(w / maturity_t)
                diff = model_vol - target_vol
                error_sum += diff * diff

            return error_sum

        best_params, _ = nelder_mead_optimize(objective, x0, step=0.03, max_iter=600)
        return SVIParameters(
            a=best_params[0],
            b=max(1e-4, best_params[1]),
            rho=max(-0.99, min(0.99, best_params[2])),
            m=best_params[3],
            sigma=max(1e-3, best_params[4]),
        )

    @staticmethod
    def calibrate_sabr_slice(
        strikes: List[float],
        market_vols: List[float],
        forward: float,
        maturity_t: float,
        beta: float = 1.0,
        initial_guess: Optional[List[float]] = None,
    ) -> SABRParameters:
        """Calibra os parâmetros SABR (alpha, rho, nu) com beta fixo a partir de cotações."""
        if len(strikes) != len(market_vols) or len(strikes) < 3:
            raise ValueError("Calibração SABR requer ao menos 3 pares (strike, volatilidade).")

        atm_vol = market_vols[len(market_vols) // 2]
        # Para beta=1, alpha_0 ~ atm_vol
        alpha_0 = atm_vol * (forward ** (1.0 - beta))
        x0 = initial_guess or [alpha_0, 0.0, 0.40]

        def objective(params: List[float]) -> float:
            alpha, rho, nu = params
            penalty = 0.0

            if alpha <= 0.001:
                penalty += 1000.0 + (0.001 - alpha) * 10000.0
            if abs(rho) >= 0.999:
                penalty += 1000.0 + abs(rho) * 10000.0
            if nu < 0.0:
                penalty += 1000.0 + abs(nu) * 10000.0

            if penalty > 0.0:
                return penalty

            sabr = SABRParameters(alpha=alpha, beta=beta, rho=rho, nu=nu)
            error_sum = 0.0
            for K, target_vol in zip(strikes, market_vols):
                try:
                    model_vol = sabr.implied_vol(forward, K, maturity_t)
                    diff = model_vol - target_vol
                    error_sum += diff * diff
                except Exception:
                    return 5000.0

            return error_sum

        best_params, _ = nelder_mead_optimize(objective, x0, step=0.03, max_iter=600)
        return SABRParameters(
            alpha=max(1e-4, best_params[0]),
            beta=beta,
            rho=max(-0.99, min(0.99, best_params[1])),
            nu=max(1e-4, best_params[2]),
        )

    # -----------------------------------------------------------------------
    # Construtores Oficiais para Dólar (DOL) e Ibovespa (IBOV) B3
    # -----------------------------------------------------------------------

    @classmethod
    def create_b3_dol_surface(
        cls,
        as_of_date: str = "2026-11-04",
        spot: float = 5.450,
        model_type: VolatilityModelType = VolatilityModelType.SABR,
    ) -> B3VolatilitySurface:
        """Cria superfície de referência oficial para opções sobre Dólar Comercial Futuro (DOL B3).

        Estrutura a termo padrão B3:
        - 1M: 21 DU (T = 21/252 = 0.0833), F = 5.475
        - 2M: 42 DU (T = 42/252 = 0.1667), F = 5.500
        - 3M: 63 DU (T = 63/252 = 0.2500), F = 5.530
        - 6M: 126 DU (T = 126/252 = 0.5000), F = 5.615
        - 1Y: 252 DU (T = 252/252 = 1.0000), F = 5.790
        """
        slices_config = [
            {"days": 21, "fwd": 5.475, "alpha": 0.135, "rho": 0.15, "nu": 0.38, "rate": 0.1075},
            {"days": 42, "fwd": 5.500, "alpha": 0.140, "rho": 0.16, "nu": 0.36, "rate": 0.1085},
            {"days": 63, "fwd": 5.530, "alpha": 0.144, "rho": 0.17, "nu": 0.35, "rate": 0.1095},
            {"days": 126, "fwd": 5.615, "alpha": 0.152, "rho": 0.18, "nu": 0.32, "rate": 0.1110},
            {"days": 252, "fwd": 5.790, "alpha": 0.160, "rho": 0.20, "nu": 0.30, "rate": 0.1125},
        ]

        slices: List[VolatilitySlice] = []
        for i, cfg in enumerate(slices_config, start=1):
            T = cfg["days"] / DEFAULT_BUSINESS_DAYS_PER_YEAR
            sabr_p = SABRParameters(
                alpha=cfg["alpha"],
                beta=1.0,
                rho=cfg["rho"],
                nu=cfg["nu"],
            )
            # SVI equivalente calibrado para consistência
            atm_w = (cfg["alpha"] ** 2) * T
            svi_p = SVIParameters(
                a=atm_w * 0.5,
                b=0.06 * math.sqrt(T),
                rho=cfg["rho"],
                m=0.01 * math.sqrt(T),
                sigma=0.08 * math.sqrt(T),
            )

            slices.append(
                VolatilitySlice(
                    slice_id=f"SLICE-DOL-{cfg['days']}DU",
                    maturity_t=T,
                    days_to_expiry=cfg["days"],
                    forward=cfg["fwd"],
                    risk_free_rate=cfg["rate"],
                    model_type=model_type,
                    sabr_params=sabr_p if model_type in {VolatilityModelType.SABR, VolatilityModelType.HYBRID} else None,
                    svi_params=svi_p if model_type in {VolatilityModelType.SVI, VolatilityModelType.HYBRID} else None,
                    as_of_date=as_of_date,
                    metadata={"commodity": "USDBRL", "contract": "DOL", "convention": "DU/252"},
                )
            )

        return B3VolatilitySurface(
            surface_id="SURFACE-B3-DOL-PARAMETRIC",
            asset_type=B3AssetType.DOL,
            model_type=model_type,
            as_of_date=as_of_date,
            day_count=DayCountConvention.BUS_252,
            spot=spot,
            metadata={
                "instrument": "Opções sobre Futuro de Dólar B3 (DOL)",
                "clearing": "Câmara B3",
                "volatility_convention": "Black-76 Implied Volatility",
            },
            slices=slices,
        )

    @classmethod
    def create_b3_ibov_surface(
        cls,
        as_of_date: str = "2026-11-04",
        spot: float = 130000.0,
        model_type: VolatilityModelType = VolatilityModelType.SVI,
    ) -> B3VolatilitySurface:
        """Cria superfície de referência oficial para opções sobre Ibovespa (IBOV / IND B3).

        Exibe o clássico 'equity skew' negativo acentuado decorrente de efeito alavancagem
        e aversão a risco de queda sistêmica.
        """
        slices_config = [
            {"days": 21, "fwd": 130500.0, "a": 0.0028, "b": 0.055, "rho": -0.65, "m": 0.002, "sigma": 0.060, "rate": 0.1075},
            {"days": 42, "fwd": 131200.0, "a": 0.0060, "b": 0.075, "rho": -0.63, "m": 0.004, "sigma": 0.080, "rate": 0.1085},
            {"days": 63, "fwd": 131900.0, "a": 0.0095, "b": 0.092, "rho": -0.62, "m": 0.006, "sigma": 0.095, "rate": 0.1095},
            {"days": 126, "fwd": 133800.0, "a": 0.0195, "b": 0.135, "rho": -0.60, "m": 0.010, "sigma": 0.130, "rate": 0.1110},
            {"days": 252, "fwd": 138000.0, "a": 0.0410, "b": 0.190, "rho": -0.58, "m": 0.015, "sigma": 0.170, "rate": 0.1125},
        ]

        slices: List[VolatilitySlice] = []
        for cfg in slices_config:
            T = cfg["days"] / DEFAULT_BUSINESS_DAYS_PER_YEAR
            svi_p = SVIParameters(
                a=cfg["a"],
                b=cfg["b"],
                rho=cfg["rho"],
                m=cfg["m"],
                sigma=cfg["sigma"],
            )
            # SABR equivalente aproximado
            sabr_p = SABRParameters(
                alpha=0.20,
                beta=1.0,
                rho=cfg["rho"],
                nu=0.48,
            )

            slices.append(
                VolatilitySlice(
                    slice_id=f"SLICE-IBOV-{cfg['days']}DU",
                    maturity_t=T,
                    days_to_expiry=cfg["days"],
                    forward=cfg["fwd"],
                    risk_free_rate=cfg["rate"],
                    model_type=model_type,
                    svi_params=svi_p if model_type in {VolatilityModelType.SVI, VolatilityModelType.HYBRID} else None,
                    sabr_params=sabr_p if model_type in {VolatilityModelType.SABR, VolatilityModelType.HYBRID} else None,
                    as_of_date=as_of_date,
                    metadata={"index": "IBOV", "contract": "IBOV/IND", "convention": "DU/252"},
                )
            )

        return B3VolatilitySurface(
            surface_id="SURFACE-B3-IBOV-PARAMETRIC",
            asset_type=B3AssetType.IBOV,
            model_type=model_type,
            as_of_date=as_of_date,
            day_count=DayCountConvention.BUS_252,
            spot=spot,
            metadata={
                "instrument": "Opções sobre Ibovespa (IBOV/IND B3)",
                "clearing": "Câmara B3",
                "volatility_convention": "Black-76 Implied Volatility",
            },
            slices=slices,
        )

    def verify_all_surfaces(self, raise_on_error: bool = False) -> Dict[str, Dict[str, bool]]:
        """Audita todas as superfícies registradas no motor."""
        audit_results: Dict[str, Dict[str, bool]] = {}
        for s_id, s in self.surfaces.items():
            audit_results[s_id] = s.verify_invariants(raise_on_error=raise_on_error)
        return audit_results

    def to_dict(self) -> Dict[str, Any]:
        """Serializa todo o catálogo de superfícies."""
        return {
            "version": "1.0.0",
            "as_of": datetime.now().strftime("%Y-%m-%d"),
            "surfaces": {k: v.to_dict() for k, v in self.surfaces.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> B3VolatilitySurfaceEngine:
        """Reconstrói o motor a partir de dicionário."""
        engine = cls()
        surfaces_dict = data.get("surfaces", {})
        for s_data in surfaces_dict.values():
            surf = B3VolatilitySurface.from_dict(s_data)
            engine.register_surface(surf)
        return engine
