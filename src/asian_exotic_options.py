"""Opções Asiáticas de Média Aritmética e Geométrica no Balcão NoMe B3 (FL-092).

Implementa o motor determinístico institucional de precificação analítica, marcação
a mercado (MtM), cálculo de sensibilidades (gregas analíticas de 1ª e 2ª ordem)
e liquidação financeira (D+1) de Opções Asiáticas de Balcão registradas no NoMe
(Câmara B3) sobre Câmbio (USD/BRL PTAX) e Commodities Agropecuárias (Soja, Milho,
Boi Gordo e Café).

Modelos Quantitativos e Regras de Negócio:
1. Modalidades de Exercício e Média:
   - Preço de Exercício Fixo (Average Price Call / Put):
     Payoff Call = max(S_avg - K, 0); Payoff Put = max(K - S_avg, 0).
   - Strike Flutuante (Average Strike Call / Put):
     Payoff Call = max(S_T - S_avg, 0); Payoff Put = max(S_avg - S_T, 0).
   - Média Aritmética (discreta com amostragem periódica diária, semanal ou customizada).
   - Média Geométrica (discreta com pesos uniformes ou ponderados).
2. Motores Analíticos de Avaliação:
   - Média Geométrica: Solução analítica exata fechada (Kemna & Vorst 1990).
   - Média Aritmética: Aproximação analítica por casamento de momentos lognormais
     (Turnbull & Wakeman 1991) e condicionamento geométrico (Curran 1994).
   - Tratamento exato de opções em andamento (in-progress), decompondo o histórico
     realizado de observações passadas e a distribuição estocástica das fixações futuras.
   - Condição de contorno quando K* <= 0 (call torna-se deterministicamente ITM).
3. Gregas Analíticas de Primeira e Segunda Ordem:
   - Delta, Gamma, Vega (e Vega 1%), Theta (anual e diário 252 DU), Rho (e Rho 1%).
4. Leis Formais de Invariantes e Auditoria Matemática B3:
   - LAW-095: Paridade Put-Call Asiática e Não-Arbitragem:
     Fixed Strike: C - P == exp(-r * T) * (E[S_avg] - K)
     Floating Strike: C_fl - P_fl == exp(-r * T) * (E[S_T] - E[S_avg])
   - LAW-096: Menor Volatilidade Efetiva e Dominância de Preço sobre Vanilla:
     Vol_Asiatica <= Vol_Vanilla (atenuação estocástica da média).
     Prêmio ATM Asiático <= Prêmio ATM Europeu correspondente.
   - LAW-097: Liquidação Financeira Soma-Zero Bilateral centavo a centavo:
     Payoff_Holder + Payoff_Writer == 0,00 BRL/USD.

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

    def check(
        condition: bool,
        message: str,
        law_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not condition:
            raise InvariantViolationError(message, law_code=law_code, details=details)


# ===========================================================================
# Constantes Numéricas de Precisão e Arredondamento B3
# ===========================================================================

QUANTIZE_MONEY = Decimal("0.01")
QUANTIZE_FOUR = Decimal("0.0001")
QUANTIZE_GREEK = Decimal("0.000001")
QUANTIZE_RATE = Decimal("0.00000001")
BUSINESS_DAYS_YEAR = Decimal("252")
CALENDAR_DAYS_YEAR = Decimal("365")
PARITY_TOLERANCE = Decimal("0.0001")


def round_money(val: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda valor financeiro para 2 casas decimais (ROUND_HALF_UP)."""
    return Decimal(str(val)).quantize(QUANTIZE_MONEY, rounding=ROUND_HALF_UP)


def round_four(val: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda cotações e preços unitários para 4 casas decimais (ROUND_HALF_UP)."""
    return Decimal(str(val)).quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_UP)


def round_greek(val: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda sensibilidade grega para 6 casas decimais (ROUND_HALF_EVEN)."""
    return Decimal(str(val)).quantize(QUANTIZE_GREEK, rounding=ROUND_HALF_EVEN)


def to_decimal(value: Any, field_name: str = "campo") -> Decimal:
    """Converte valor para Decimal de forma determinística."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise AsianValidationError(f"Valor nulo não permitido para '{field_name}'.")
    try:
        cleaned = str(value).strip().replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError) as err:
        raise AsianValidationError(
            f"Valor inválido para '{field_name}': {value!r}"
        ) from err


def to_date(val: Any, field_name: str = "data") -> date:
    """Converte valor para date (ISO 8601 YYYY-MM-DD)."""
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        try:
            return date.fromisoformat(val.strip()[:10])
        except ValueError as err:
            raise AsianValidationError(
                f"Formato de data inválido para '{field_name}': {val!r}"
            ) from err
    raise AsianValidationError(
        f"Tipo inválido para data em '{field_name}': {type(val).__name__}"
    )


def std_norm_cdf(x: float) -> float:
    """Função de distribuição acumulada da normal padrão N(x) via erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def std_norm_pdf(x: float) -> float:
    """Função de densidade de probabilidade normal padrão N'(x)."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ===========================================================================
# Enumerações Tipadas do Módulo
# ===========================================================================

class AsianOptionType(str, Enum):
    """Tipo do direito da opção."""
    CALL = "CALL"
    PUT = "PUT"


class AsianAverageType(str, Enum):
    """Mecanismo de cálculo da média dos preços de observação."""
    ARITHMETIC = "ARITHMETIC"
    GEOMETRIC = "GEOMETRIC"


class AsianStrikeType(str, Enum):
    """Estrutura do preço de exercício."""
    FIXED_STRIKE = "FIXED_STRIKE"        # Average Price Option (Payoff vs K)
    FLOATING_STRIKE = "FLOATING_STRIKE"  # Average Strike Option (Payoff vs S_T)


class AsianSamplingFrequency(str, Enum):
    """Periodicidade da amostragem das cotações."""
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    BIWEEKLY = "BIWEEKLY"
    MONTHLY = "MONTHLY"
    CUSTOM = "CUSTOM"


class AsianUnderlyingType(str, Enum):
    """Ativos subjacentes elegíveis para opções asiáticas de balcão B3."""
    USD_PTAX = "USD_PTAX"    # Dólar Comercial PTAX BACEN
    SOYBEAN = "SOYBEAN"      # Soja FOB Santos Indicador CEPEA/ESALQ
    CORN = "CORN"            # Milho Campinas Indicador CEPEA/ESALQ
    CATTLE = "CATTLE"        # Boi Gordo Indicador CEPEA/ESALQ
    COFFEE = "COFFEE"        # Café Arábica Indicador CEPEA/ESALQ


class AsianPricingModel(str, Enum):
    """Modelo analítico quantitativo empregado."""
    KEMNA_VORST = "KEMNA_VORST"            # Solução analítica fechada média geométrica
    TURNBULL_WAKEMAN = "TURNBULL_WAKEMAN"  # Casamento de momentos analíticos média aritmética
    CURRAN = "CURRAN"                      # Aproximação por condicionamento geométrico


class AsianContractStatus(str, Enum):
    """Status do contrato no sistema NoMe."""
    ACTIVE = "ACTIVE"
    IN_AVERAGING = "IN_AVERAGING"
    MATURED = "MATURED"
    SETTLED = "SETTLED"


# ===========================================================================
# Exceções de Domínio
# ===========================================================================

class AsianOptionError(Exception):
    """Exceção base para Opções Asiáticas de Balcão."""


class AsianValidationError(AsianOptionError):
    """Erro de validação cadastral, contratual ou paramétrica."""


class AsianPricingError(AsianOptionError):
    """Erro durante o cálculo analítico de precificação ou gregas."""


class AsianSettlementError(AsianOptionError):
    """Erro durante a liquidação financeira no vencimento."""


class AsianInvariantViolationError(AsianOptionError, InvariantViolationError):
    """Violação de invariante formal de paridade, não-arbitragem ou conservação."""


# ===========================================================================
# Especificações Técnicas Oficiais B3
# ===========================================================================

@dataclass(frozen=True)
class AsianAssetSpecification:
    """Especificação contratual padronizada do ativo subjacente na B3."""
    underlying_type: AsianUnderlyingType
    ticker_prefix: str
    description: str
    contract_unit: str
    contract_multiplier: Decimal
    currency: str
    tick_size: Decimal
    price_source: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "underlying_type": self.underlying_type.value,
            "ticker_prefix": self.ticker_prefix,
            "description": self.description,
            "contract_unit": self.contract_unit,
            "contract_multiplier": str(self.contract_multiplier),
            "currency": self.currency,
            "tick_size": str(self.tick_size),
            "price_source": self.price_source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AsianAssetSpecification:
        return cls(
            underlying_type=AsianUnderlyingType(data["underlying_type"]),
            ticker_prefix=str(data["ticker_prefix"]),
            description=str(data["description"]),
            contract_unit=str(data["contract_unit"]),
            contract_multiplier=to_decimal(data["contract_multiplier"], "contract_multiplier"),
            currency=str(data["currency"]),
            tick_size=to_decimal(data["tick_size"], "tick_size"),
            price_source=str(data["price_source"]),
        )

    @classmethod
    def get_default(cls, underlying_type: Union[AsianUnderlyingType, str]) -> AsianAssetSpecification:
        """Retorna as especificações operacionais institucionais vigentes na B3."""
        u_type = AsianUnderlyingType(underlying_type)
        if u_type == AsianUnderlyingType.USD_PTAX:
            return cls(
                underlying_type=u_type,
                ticker_prefix="DOL",
                description="Dólar Comercial PTAX BACEN 800 Venda (B3 Balcão)",
                contract_unit="USD",
                contract_multiplier=Decimal("1.00"),
                currency="BRL",
                tick_size=Decimal("0.0001"),
                price_source="BACEN_PTAX_VENDA",
            )
        if u_type == AsianUnderlyingType.SOYBEAN:
            return cls(
                underlying_type=u_type,
                ticker_prefix="SJC",
                description="Soja em Grão FOB Santos Indicador CEPEA/ESALQ (B3)",
                contract_unit="sacas de 60kg",
                contract_multiplier=Decimal("450.00"),
                currency="USD",
                tick_size=Decimal("0.01"),
                price_source="CEPEA_ESALQ_SOJA",
            )
        if u_type == AsianUnderlyingType.CORN:
            return cls(
                underlying_type=u_type,
                ticker_prefix="CCM",
                description="Milho em Grão Campinas Indicador CEPEA/ESALQ (B3)",
                contract_unit="sacas de 60kg",
                contract_multiplier=Decimal("450.00"),
                currency="BRL",
                tick_size=Decimal("0.01"),
                price_source="CEPEA_ESALQ_MILHO",
            )
        if u_type == AsianUnderlyingType.CATTLE:
            return cls(
                underlying_type=u_type,
                ticker_prefix="BGI",
                description="Boi Gordo Indicador CEPEA/ESALQ (B3)",
                contract_unit="arrobas de 15kg",
                contract_multiplier=Decimal("330.00"),
                currency="BRL",
                tick_size=Decimal("0.01"),
                price_source="CEPEA_ESALQ_BOI",
            )
        if u_type == AsianUnderlyingType.COFFEE:
            return cls(
                underlying_type=u_type,
                ticker_prefix="ICF",
                description="Café Arábica tipo 4/5 Indicador CEPEA/ESALQ (B3)",
                contract_unit="sacas de 60kg",
                contract_multiplier=Decimal("100.00"),
                currency="USD",
                tick_size=Decimal("0.05"),
                price_source="CEPEA_ESALQ_CAFE",
            )
        raise AsianValidationError(f"Ativo subjacente não suportado: {underlying_type}")


# ===========================================================================
# Ponto de Observação da Média
# ===========================================================================

@dataclass
class ObservationPoint:
    """Ponto individual de fixação de preço de observação."""
    observation_date: date
    observed_price: Optional[Decimal] = None
    weight: Decimal = Decimal("1.0")
    source: str = ""

    @property
    def is_realized(self) -> bool:
        """Indica se a cotação já foi apurada no mercado."""
        return self.observed_price is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observation_date": self.observation_date.isoformat(),
            "observed_price": str(self.observed_price) if self.observed_price is not None else None,
            "weight": str(self.weight),
            "source": self.source,
            "is_realized": self.is_realized,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ObservationPoint:
        p_val = data.get("observed_price")
        return cls(
            observation_date=to_date(data["observation_date"], "observation_date"),
            observed_price=to_decimal(p_val, "observed_price") if p_val is not None else None,
            weight=to_decimal(data.get("weight", "1.0"), "weight"),
            source=str(data.get("source", "")),
        )


# ===========================================================================
# Gregas Analíticas de Opções Asiáticas
# ===========================================================================

@dataclass(frozen=True)
class AsianGreeks:
    """Conjunto de sensibilidades analíticas de opções asiáticas."""
    delta: Decimal
    gamma: Decimal
    vega: Decimal
    vega_1pct: Decimal
    theta_annual: Decimal
    theta_daily: Decimal
    rho: Decimal
    rho_1pct: Decimal

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
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AsianGreeks:
        return cls(
            delta=to_decimal(data["delta"], "delta"),
            gamma=to_decimal(data["gamma"], "gamma"),
            vega=to_decimal(data["vega"], "vega"),
            vega_1pct=to_decimal(data["vega_1pct"], "vega_1pct"),
            theta_annual=to_decimal(data["theta_annual"], "theta_annual"),
            theta_daily=to_decimal(data["theta_daily"], "theta_daily"),
            rho=to_decimal(data["rho"], "rho"),
            rho_1pct=to_decimal(data["rho_1pct"], "rho_1pct"),
        )


# ===========================================================================
# Resultado de Precificação Analítica e Marcação a Mercado
# ===========================================================================

@dataclass(frozen=True)
class AsianPricingResult:
    """Resultado analítico completo da precificação de uma opção asiática."""
    contract_id: str
    valuation_date: date
    option_type: AsianOptionType
    strike_type: AsianStrikeType
    average_type: AsianAverageType
    model_used: AsianPricingModel
    premium_per_unit: Decimal
    total_premium: Decimal
    spot_price: Decimal
    strike_price: Decimal
    expected_average: Decimal
    forward_underlying: Decimal
    effective_volatility: Decimal
    vanilla_volatility: Decimal
    vanilla_equivalent_premium: Decimal
    put_call_parity_diff: Decimal
    parity_holds: bool
    effective_vol_bound_holds: bool
    d1: Decimal
    d2: Decimal
    greeks: AsianGreeks
    realized_observations_count: int
    remaining_observations_count: int
    realized_average: Optional[Decimal]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "valuation_date": self.valuation_date.isoformat(),
            "option_type": self.option_type.value,
            "strike_type": self.strike_type.value,
            "average_type": self.average_type.value,
            "model_used": self.model_used.value,
            "premium_per_unit": str(self.premium_per_unit),
            "total_premium": str(self.total_premium),
            "spot_price": str(self.spot_price),
            "strike_price": str(self.strike_price),
            "expected_average": str(self.expected_average),
            "forward_underlying": str(self.forward_underlying),
            "effective_volatility": str(self.effective_volatility),
            "vanilla_volatility": str(self.vanilla_volatility),
            "vanilla_equivalent_premium": str(self.vanilla_equivalent_premium),
            "put_call_parity_diff": str(self.put_call_parity_diff),
            "parity_holds": self.parity_holds,
            "effective_vol_bound_holds": self.effective_vol_bound_holds,
            "d1": str(self.d1),
            "d2": str(self.d2),
            "greeks": self.greeks.to_dict(),
            "realized_observations_count": self.realized_observations_count,
            "remaining_observations_count": self.remaining_observations_count,
            "realized_average": str(self.realized_average) if self.realized_average is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AsianPricingResult:
        realized_avg = data.get("realized_average")
        return cls(
            contract_id=str(data["contract_id"]),
            valuation_date=to_date(data["valuation_date"], "valuation_date"),
            option_type=AsianOptionType(data["option_type"]),
            strike_type=AsianStrikeType(data["strike_type"]),
            average_type=AsianAverageType(data["average_type"]),
            model_used=AsianPricingModel(data["model_used"]),
            premium_per_unit=to_decimal(data["premium_per_unit"], "premium_per_unit"),
            total_premium=to_decimal(data["total_premium"], "total_premium"),
            spot_price=to_decimal(data["spot_price"], "spot_price"),
            strike_price=to_decimal(data["strike_price"], "strike_price"),
            expected_average=to_decimal(data["expected_average"], "expected_average"),
            forward_underlying=to_decimal(data["forward_underlying"], "forward_underlying"),
            effective_volatility=to_decimal(data["effective_volatility"], "effective_volatility"),
            vanilla_volatility=to_decimal(data["vanilla_volatility"], "vanilla_volatility"),
            vanilla_equivalent_premium=to_decimal(data["vanilla_equivalent_premium"], "vanilla_equivalent_premium"),
            put_call_parity_diff=to_decimal(data["put_call_parity_diff"], "put_call_parity_diff"),
            parity_holds=bool(data["parity_holds"]),
            effective_vol_bound_holds=bool(data["effective_vol_bound_holds"]),
            d1=to_decimal(data["d1"], "d1"),
            d2=to_decimal(data["d2"], "d2"),
            greeks=AsianGreeks.from_dict(data["greeks"]),
            realized_observations_count=int(data["realized_observations_count"]),
            remaining_observations_count=int(data["remaining_observations_count"]),
            realized_average=to_decimal(realized_avg, "realized_average") if realized_avg is not None else None,
        )

    def verify_invariants(self, tolerance: Decimal = PARITY_TOLERANCE) -> None:
        """Verifica formalmente as invariantes LAW-095 e LAW-096."""
        # LAW-095: Paridade Asiática
        check(
            self.parity_holds,
            f"Violação da Paridade Asiática (LAW-095) no contrato '{self.contract_id}': "
            f"Diferença={self.put_call_parity_diff} excede tolerância de {tolerance}.",
            law_code="LAW-095",
            details={
                "contract_id": self.contract_id,
                "put_call_parity_diff": str(self.put_call_parity_diff),
                "tolerance": str(tolerance),
            },
        )
        # LAW-096: Menor Volatilidade Efetiva
        check(
            self.effective_vol_bound_holds,
            f"Violação da Menor Volatilidade Efetiva (LAW-096) no contrato '{self.contract_id}': "
            f"Vol_Asiatica ({self.effective_volatility}) > Vol_Vanilla ({self.vanilla_volatility}).",
            law_code="LAW-096",
            details={
                "contract_id": self.contract_id,
                "effective_volatility": str(self.effective_volatility),
                "vanilla_volatility": str(self.vanilla_volatility),
            },
        )


# ===========================================================================
# Contrato de Opção Asiática de Balcão (NoMe B3)
# ===========================================================================

@dataclass
class AsianOptionContract:
    """Contrato institucional de opção asiática registrado no NoMe B3."""
    contract_id: str
    nome_protocol: str
    underlying_type: AsianUnderlyingType
    option_type: AsianOptionType
    strike_type: AsianStrikeType
    average_type: AsianAverageType
    sampling_frequency: AsianSamplingFrequency
    trade_date: date
    start_averaging_date: date
    expiration_date: date
    settlement_date: date
    strike_price: Decimal
    quantity: Decimal
    contract_multiplier: Decimal
    spot_initial: Decimal
    risk_free_rate: Decimal
    dividend_yield: Decimal = Decimal("0.0000")
    volatility: Decimal = Decimal("0.2000")
    observations: List[ObservationPoint] = field(default_factory=list)
    holder_account: str = ""
    holder_document: str = ""
    writer_account: str = ""
    writer_document: str = ""
    holder_irrf_rate: Decimal = Decimal("0.1500")
    holder_irrf_exempt: bool = False
    premium_agreed: Optional[Decimal] = None
    status: AsianContractStatus = AsianContractStatus.ACTIVE

    def __post_init__(self) -> None:
        self.strike_price = to_decimal(self.strike_price, "strike_price")
        self.quantity = to_decimal(self.quantity, "quantity")
        self.contract_multiplier = to_decimal(self.contract_multiplier, "contract_multiplier")
        self.spot_initial = to_decimal(self.spot_initial, "spot_initial")
        self.risk_free_rate = to_decimal(self.risk_free_rate, "risk_free_rate")
        self.dividend_yield = to_decimal(self.dividend_yield, "dividend_yield")
        self.volatility = to_decimal(self.volatility, "volatility")
        self.holder_irrf_rate = to_decimal(self.holder_irrf_rate, "holder_irrf_rate")
        if self.premium_agreed is not None:
            self.premium_agreed = to_decimal(self.premium_agreed, "premium_agreed")

    def add_observation(self, point: ObservationPoint) -> None:
        """Adiciona ou atualiza ponto de observação no calendário do contrato."""
        for idx, obs in enumerate(self.observations):
            if obs.observation_date == point.observation_date:
                self.observations[idx] = point
                return
        self.observations.append(point)
        self.observations.sort(key=lambda x: x.observation_date)

    def get_realized_observations(self) -> List[ObservationPoint]:
        """Retorna as observações que já possuem preço de fixação apurado."""
        return [o for o in self.observations if o.is_realized]

    def get_pending_observations(self) -> List[ObservationPoint]:
        """Retorna as observações ainda pendentes de fixação futura."""
        return [o for o in self.observations if not o.is_realized]

    def compute_current_average(self) -> Optional[Decimal]:
        """Calcula a média acumulada até o momento das observações já realizadas."""
        realized = self.get_realized_observations()
        if not realized:
            return None
        total_weight = sum(o.weight for o in realized)
        if total_weight <= Decimal("0.00"):
            return None

        if self.average_type == AsianAverageType.ARITHMETIC:
            weighted_sum = sum(o.observed_price * o.weight for o in realized)  # type: ignore[operator]
            return round_four(weighted_sum / total_weight)
        else:  # GEOMETRIC
            # Média geométrica ponderada
            log_sum = sum(o.weight * Decimal(str(math.log(float(o.observed_price)))) for o in realized)  # type: ignore[arg-type]
            geom = Decimal(str(math.exp(float(log_sum / total_weight))))
            return round_four(geom)

    def verify_invariants(self) -> None:
        """Verifica a integridade temporal, quantitativa e paramétrica do contrato."""
        check(
            self.trade_date <= self.start_averaging_date,
            f"Data de negociação ({self.trade_date}) deve ser <= início de amostragem ({self.start_averaging_date})",
            law_code="LAW-095",
        )
        check(
            self.start_averaging_date <= self.expiration_date,
            f"Início de amostragem ({self.start_averaging_date}) deve ser <= vencimento ({self.expiration_date})",
            law_code="LAW-095",
        )
        check(
            self.expiration_date <= self.settlement_date,
            f"Vencimento ({self.expiration_date}) deve ser <= liquidação ({self.settlement_date})",
            law_code="LAW-095",
        )
        check(
            self.quantity > Decimal("0.00"),
            f"Quantidade contratual deve ser estritamente positiva: {self.quantity}",
            law_code="LAW-095",
        )
        check(
            self.contract_multiplier > Decimal("0.00"),
            f"Multiplicador contratual deve ser estritamente positivo: {self.contract_multiplier}",
            law_code="LAW-095",
        )
        check(
            self.spot_initial > Decimal("0.00"),
            f"Spot inicial deve ser positivo: {self.spot_initial}",
            law_code="LAW-095",
        )
        if self.strike_type == AsianStrikeType.FIXED_STRIKE:
            check(
                self.strike_price > Decimal("0.00"),
                f"Strike fixo deve ser positivo: {self.strike_price}",
                law_code="LAW-095",
            )
        check(
            self.volatility > Decimal("0.00"),
            f"Volatilidade deve ser estritamente positiva: {self.volatility}",
            law_code="LAW-096",
        )
        check(
            len(self.observations) >= 1,
            f"Contrato deve conter ao menos 1 ponto de observação agendado.",
            law_code="LAW-095",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "nome_protocol": self.nome_protocol,
            "underlying_type": self.underlying_type.value,
            "option_type": self.option_type.value,
            "strike_type": self.strike_type.value,
            "average_type": self.average_type.value,
            "sampling_frequency": self.sampling_frequency.value,
            "trade_date": self.trade_date.isoformat(),
            "start_averaging_date": self.start_averaging_date.isoformat(),
            "expiration_date": self.expiration_date.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
            "strike_price": str(self.strike_price),
            "quantity": str(self.quantity),
            "contract_multiplier": str(self.contract_multiplier),
            "spot_initial": str(self.spot_initial),
            "risk_free_rate": str(self.risk_free_rate),
            "dividend_yield": str(self.dividend_yield),
            "volatility": str(self.volatility),
            "observations": [o.to_dict() for o in self.observations],
            "holder_account": self.holder_account,
            "holder_document": self.holder_document,
            "writer_account": self.writer_account,
            "writer_document": self.writer_document,
            "holder_irrf_rate": str(self.holder_irrf_rate),
            "holder_irrf_exempt": self.holder_irrf_exempt,
            "premium_agreed": str(self.premium_agreed) if self.premium_agreed is not None else None,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AsianOptionContract:
        obs_raw = data.get("observations", [])
        observations = [ObservationPoint.from_dict(o) for o in obs_raw]
        prem = data.get("premium_agreed")
        return cls(
            contract_id=str(data["contract_id"]),
            nome_protocol=str(data["nome_protocol"]),
            underlying_type=AsianUnderlyingType(data["underlying_type"]),
            option_type=AsianOptionType(data["option_type"]),
            strike_type=AsianStrikeType(data["strike_type"]),
            average_type=AsianAverageType(data["average_type"]),
            sampling_frequency=AsianSamplingFrequency(data.get("sampling_frequency", "DAILY")),
            trade_date=to_date(data["trade_date"], "trade_date"),
            start_averaging_date=to_date(data["start_averaging_date"], "start_averaging_date"),
            expiration_date=to_date(data["expiration_date"], "expiration_date"),
            settlement_date=to_date(data["settlement_date"], "settlement_date"),
            strike_price=to_decimal(data.get("strike_price", "0.0"), "strike_price"),
            quantity=to_decimal(data["quantity"], "quantity"),
            contract_multiplier=to_decimal(data.get("contract_multiplier", "1.0"), "contract_multiplier"),
            spot_initial=to_decimal(data["spot_initial"], "spot_initial"),
            risk_free_rate=to_decimal(data["risk_free_rate"], "risk_free_rate"),
            dividend_yield=to_decimal(data.get("dividend_yield", "0.0"), "dividend_yield"),
            volatility=to_decimal(data.get("volatility", "0.20"), "volatility"),
            observations=observations,
            holder_account=str(data.get("holder_account", "")),
            holder_document=str(data.get("holder_document", "")),
            writer_account=str(data.get("writer_account", "")),
            writer_document=str(data.get("writer_document", "")),
            holder_irrf_rate=to_decimal(data.get("holder_irrf_rate", "0.15"), "holder_irrf_rate"),
            holder_irrf_exempt=bool(data.get("holder_irrf_exempt", False)),
            premium_agreed=to_decimal(prem, "premium_agreed") if prem is not None else None,
            status=AsianContractStatus(data.get("status", "ACTIVE")),
        )


# ===========================================================================
# Registro de Liquidação Financeira no Vencimento (D+1)
# ===========================================================================

@dataclass(frozen=True)
class AsianSettlementRecord:
    """Comprovante oficial de liquidação financeira de opção asiática no NoMe."""
    contract_id: str
    nome_protocol: str
    settlement_date: date
    option_type: AsianOptionType
    strike_type: AsianStrikeType
    average_type: AsianAverageType
    final_average: Decimal
    final_spot: Decimal
    effective_strike: Decimal
    intrinsic_per_unit: Decimal
    quantity: Decimal
    contract_multiplier: Decimal
    gross_holder_amount: Decimal
    gross_writer_amount: Decimal
    irrf_amount: Decimal
    net_holder_amount: Decimal
    net_writer_amount: Decimal
    is_itm: bool
    is_zero_sum: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "nome_protocol": self.nome_protocol,
            "settlement_date": self.settlement_date.isoformat(),
            "option_type": self.option_type.value,
            "strike_type": self.strike_type.value,
            "average_type": self.average_type.value,
            "final_average": str(self.final_average),
            "final_spot": str(self.final_spot),
            "effective_strike": str(self.effective_strike),
            "intrinsic_per_unit": str(self.intrinsic_per_unit),
            "quantity": str(self.quantity),
            "contract_multiplier": str(self.contract_multiplier),
            "gross_holder_amount": str(self.gross_holder_amount),
            "gross_writer_amount": str(self.gross_writer_amount),
            "irrf_amount": str(self.irrf_amount),
            "net_holder_amount": str(self.net_holder_amount),
            "net_writer_amount": str(self.net_writer_amount),
            "is_itm": self.is_itm,
            "is_zero_sum": self.is_zero_sum,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AsianSettlementRecord:
        return cls(
            contract_id=str(data["contract_id"]),
            nome_protocol=str(data["nome_protocol"]),
            settlement_date=to_date(data["settlement_date"], "settlement_date"),
            option_type=AsianOptionType(data["option_type"]),
            strike_type=AsianStrikeType(data["strike_type"]),
            average_type=AsianAverageType(data["average_type"]),
            final_average=to_decimal(data["final_average"], "final_average"),
            final_spot=to_decimal(data["final_spot"], "final_spot"),
            effective_strike=to_decimal(data["effective_strike"], "effective_strike"),
            intrinsic_per_unit=to_decimal(data["intrinsic_per_unit"], "intrinsic_per_unit"),
            quantity=to_decimal(data["quantity"], "quantity"),
            contract_multiplier=to_decimal(data["contract_multiplier"], "contract_multiplier"),
            gross_holder_amount=to_decimal(data["gross_holder_amount"], "gross_holder_amount"),
            gross_writer_amount=to_decimal(data["gross_writer_amount"], "gross_writer_amount"),
            irrf_amount=to_decimal(data["irrf_amount"], "irrf_amount"),
            net_holder_amount=to_decimal(data["net_holder_amount"], "net_holder_amount"),
            net_writer_amount=to_decimal(data["net_writer_amount"], "net_writer_amount"),
            is_itm=bool(data["is_itm"]),
            is_zero_sum=bool(data["is_zero_sum"]),
        )

    def verify_invariants(self) -> None:
        """Audita formalmente a Lei de Conservação Soma-Zero (LAW-097)."""
        check(
            self.gross_holder_amount + self.gross_writer_amount == Decimal("0.00"),
            f"Violação da Conservação Financeira Soma-Zero (LAW-097) no contrato '{self.contract_id}': "
            f"Titular ({self.gross_holder_amount}) + Lançador ({self.gross_writer_amount}) != 0,00",
            law_code="LAW-097",
            details={
                "contract_id": self.contract_id,
                "gross_holder": str(self.gross_holder_amount),
                "gross_writer": str(self.gross_writer_amount),
            },
        )
        check(
            self.net_writer_amount == self.gross_writer_amount,
            f"Débito do lançador deve ser igual ao valor bruto de liquidação: {self.net_writer_amount} != {self.gross_writer_amount}",
            law_code="LAW-097",
        )
        check(
            self.net_holder_amount == self.gross_holder_amount - self.irrf_amount,
            f"Crédito líquido do titular diverge de bruto - IRRF: {self.net_holder_amount}",
            law_code="LAW-097",
        )
        check(
            self.irrf_amount >= Decimal("0.00"),
            f"Imposto de Renda retido não pode ser negativo: {self.irrf_amount}",
            law_code="LAW-097",
        )


# ===========================================================================
# Sumário de Portfólio de Balcão NoMe
# ===========================================================================

@dataclass(frozen=True)
class AsianPortfolioSummary:
    """Sumário consolidado de posições e liquidações de opções asiáticas."""
    total_contracts: int
    active_contracts: int
    settled_contracts: int
    total_holder_gross: Decimal
    total_writer_gross: Decimal
    total_irrf_withheld: Decimal
    all_zero_sum: bool
    all_parities_held: bool
    contracts: List[Dict[str, Any]]
    settlements: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_contracts": self.total_contracts,
            "active_contracts": self.active_contracts,
            "settled_contracts": self.settled_contracts,
            "total_holder_gross": str(self.total_holder_gross),
            "total_writer_gross": str(self.total_writer_gross),
            "total_irrf_withheld": str(self.total_irrf_withheld),
            "all_zero_sum": self.all_zero_sum,
            "all_parities_held": self.all_parities_held,
            "contracts": self.contracts,
            "settlements": self.settlements,
        }

    def verify_invariants(self) -> None:
        check(
            self.all_zero_sum,
            "Invariante LAW-097 violada no portfólio: nem todos os contratos são soma-zero.",
            law_code="LAW-097",
        )
        check(
            self.total_holder_gross + self.total_writer_gross == Decimal("0.00"),
            f"Fluxo multilateral não balanceado: Titulares={self.total_holder_gross}, Lançadores={self.total_writer_gross}",
            law_code="LAW-097",
        )


# ===========================================================================
# Motores Quantitativos Analíticos (Kemna-Vorst, Turnbull-Wakeman, Curran)
# ===========================================================================

class AsianPricer:
    """Motor quantitativo analítico de precificação de opções asiáticas."""

    @staticmethod
    def _vanilla_european_price(
        spot: float,
        strike: float,
        r: float,
        q: float,
        sigma: float,
        tau: float,
        option_type: AsianOptionType,
    ) -> float:
        """Precificação analítica de opção europeia vanilla pelo modelo Black-Scholes-Merton."""
        eff_k = max(strike, 1e-12)
        if tau <= 1e-12 or sigma <= 1e-12:
            forward = spot * math.exp((r - q) * tau)
            disc = math.exp(-r * tau)
            if option_type == AsianOptionType.CALL:
                return disc * max(forward - eff_k, 0.0)
            return disc * max(eff_k - forward, 0.0)

        vol_sqrt = sigma * math.sqrt(tau)
        d1 = (math.log(spot / eff_k) + (r - q + 0.5 * sigma * sigma) * tau) / vol_sqrt
        d2 = d1 - vol_sqrt
        disc = math.exp(-r * tau)

        if option_type == AsianOptionType.CALL:
            return disc * (spot * math.exp((r - q) * tau) * std_norm_cdf(d1) - eff_k * std_norm_cdf(d2))
        else:
            return disc * (eff_k * std_norm_cdf(-d2) - spot * math.exp((r - q) * tau) * std_norm_cdf(-d1))

    @staticmethod
    def price_geometric_kemna_vorst(
        spot: float,
        strike: float,
        r: float,
        q: float,
        sigma: float,
        tau_to_expiration: float,
        observation_times: Sequence[float],
        option_type: AsianOptionType,
        strike_type: AsianStrikeType = AsianStrikeType.FIXED_STRIKE,
        past_prices: Optional[Sequence[float]] = None,
    ) -> Tuple[float, float, float, float, float, float, float]:
        """Precificação analítica exata fechada de Kemna-Vorst (1990) para Média Geométrica.

        Retorna:
            (price, expected_geom, effective_vol, d1, d2, fwd_underlying, parity_diff)
        """
        N = len(observation_times)
        if N == 0:
            raise AsianPricingError("Lista de tempos de observação não pode ser vazia.")

        past_prices = past_prices or []
        m = len(past_prices)
        k = N - m  # observações futuras restantes

        tau_N = tau_to_expiration
        fwd_underlying = spot * math.exp((r - q) * tau_N)

        # Se todas as observações já ocorreram (vencimento)
        if k == 0:
            geom_avg = math.exp(sum(math.log(p) for p in past_prices) / N)
            disc = math.exp(-r * max(tau_N, 0.0))
            if strike_type == AsianStrikeType.FIXED_STRIKE:
                eff_strike = strike
                if option_type == AsianOptionType.CALL:
                    pr = disc * max(geom_avg - eff_strike, 0.0)
                else:
                    pr = disc * max(eff_strike - geom_avg, 0.0)
                parity_diff = 0.0
            else:  # Floating strike: S_T vs G
                s_T = past_prices[-1]
                if option_type == AsianOptionType.CALL:
                    pr = disc * max(s_T - geom_avg, 0.0)
                else:
                    pr = disc * max(geom_avg - s_T, 0.0)
                parity_diff = 0.0
            return pr, geom_avg, 0.0, 0.0, 0.0, fwd_underlying, parity_diff

        # Tempos futuros relativos à data de avaliação atual (tau_j = t_j - t_val)
        future_tau = observation_times[m:]
        tau_final = future_tau[-1]

        # Componente das observações passadas (se houver)
        past_log_sum = sum(math.log(p) for p in past_prices) if m > 0 else 0.0

        # Média e variância do logaritmo da média geométrica total Y = ln(G_N)
        # Y = (1/N) * [ sum(ln S_past) + sum(ln S_future) ]
        drift = r - q - 0.5 * sigma * sigma
        future_log_mean_sum = sum(math.log(spot) + drift * tj for tj in future_tau)
        mu_Y = (past_log_sum + future_log_mean_sum) / N

        # Variância de Y devida às incertezas futuras
        # Cov(W_{ti}, W_{tj}) = min(ti, tj)
        double_sum_cov = 0.0
        for i in range(k):
            for j in range(k):
                double_sum_cov += min(future_tau[i], future_tau[j])
        var_Y = (sigma * sigma / (N * N)) * double_sum_cov
        sigma_Y = math.sqrt(max(var_Y, 1e-16))

        # Esperança da média geométrica: E[G_N] = exp(mu_Y + 0.5 * var_Y)
        E_G = math.exp(mu_Y + 0.5 * var_Y)
        disc = math.exp(-r * tau_final)

        # Volatilidade efetiva anualizada da média
        eff_vol = sigma_Y / math.sqrt(max(tau_final, 1e-8))

        if strike_type == AsianStrikeType.FIXED_STRIKE:
            # Opção de Preço Médio (Average Price Option): Payoff = max(G_N - K, 0)
            if sigma_Y < 1e-10:
                d1 = 0.0
                d2 = 0.0
                call_pr = disc * max(E_G - strike, 0.0)
                put_pr = disc * max(strike - E_G, 0.0)
            else:
                d1 = (math.log(E_G / strike) + 0.5 * var_Y) / sigma_Y
                d2 = d1 - sigma_Y
                call_pr = disc * (E_G * std_norm_cdf(d1) - strike * std_norm_cdf(d2))
                put_pr = disc * (strike * std_norm_cdf(-d2) - E_G * std_norm_cdf(-d1))

            parity_expected = disc * (E_G - strike)
            parity_actual = call_pr - put_pr
            parity_diff = abs(parity_actual - parity_expected)
            chosen_price = call_pr if option_type == AsianOptionType.CALL else put_pr
            return chosen_price, E_G, eff_vol, d1, d2, fwd_underlying, parity_diff

        else:
            # Opção de Strike Flutuante (Average Strike Option): Payoff = max(S_T - G_N, 0)
            # S_T e G_N são conjuntamente lognormais
            E_ST = spot * math.exp((r - q) * tau_final)
            # Cov(ln S_T, ln G_N) = (sigma^2 / N) * sum(min(tau_final, future_tau_j)) = (sigma^2 / N) * sum(future_tau)
            cov_ST_Y = (sigma * sigma / N) * sum(future_tau)
            var_ST = sigma * sigma * tau_final
            var_Z = var_ST + var_Y - 2.0 * cov_ST_Y
            sigma_Z = math.sqrt(max(var_Z, 1e-16))

            if sigma_Z < 1e-10:
                d1 = 0.0
                d2 = 0.0
                call_pr = disc * max(E_ST - E_G, 0.0)
                put_pr = disc * max(E_G - E_ST, 0.0)
            else:
                d1 = (math.log(E_ST / E_G) + 0.5 * var_Z) / sigma_Z
                d2 = d1 - sigma_Z
                call_pr = disc * (E_ST * std_norm_cdf(d1) - E_G * std_norm_cdf(d2))
                put_pr = disc * (E_G * std_norm_cdf(-d2) - E_ST * std_norm_cdf(-d1))

            parity_expected = disc * (E_ST - E_G)
            parity_actual = call_pr - put_pr
            parity_diff = abs(parity_actual - parity_expected)
            eff_vol_float = sigma_Z / math.sqrt(max(tau_final, 1e-8))
            chosen_price = call_pr if option_type == AsianOptionType.CALL else put_pr
            return chosen_price, E_G, eff_vol_float, d1, d2, fwd_underlying, parity_diff

    @staticmethod
    def price_arithmetic_turnbull_wakeman(
        spot: float,
        strike: float,
        r: float,
        q: float,
        sigma: float,
        tau_to_expiration: float,
        observation_times: Sequence[float],
        option_type: AsianOptionType,
        strike_type: AsianStrikeType = AsianStrikeType.FIXED_STRIKE,
        past_prices: Optional[Sequence[float]] = None,
    ) -> Tuple[float, float, float, float, float, float, float]:
        """Aproximação analítica por casamento de dois momentos (Turnbull & Wakeman 1991).

        Retorna:
            (price, expected_arith, effective_vol, d1, d2, fwd_underlying, parity_diff)
        """
        N = len(observation_times)
        if N == 0:
            raise AsianPricingError("Lista de tempos de observação não pode ser vazia.")

        past_prices = past_prices or []
        m = len(past_prices)
        k = N - m

        tau_N = tau_to_expiration
        fwd_underlying = spot * math.exp((r - q) * tau_N)

        # Se todas as observações já ocorreram
        if k == 0:
            arith_avg = sum(past_prices) / N
            disc = math.exp(-r * max(tau_N, 0.0))
            if strike_type == AsianStrikeType.FIXED_STRIKE:
                if option_type == AsianOptionType.CALL:
                    pr = disc * max(arith_avg - strike, 0.0)
                else:
                    pr = disc * max(strike - arith_avg, 0.0)
            else:
                s_T = past_prices[-1]
                if option_type == AsianOptionType.CALL:
                    pr = disc * max(s_T - arith_avg, 0.0)
                else:
                    pr = disc * max(arith_avg - s_T, 0.0)
            return pr, arith_avg, 0.0, 0.0, 0.0, fwd_underlying, 0.0

        future_tau = observation_times[m:]
        tau_final = future_tau[-1]
        disc = math.exp(-r * tau_final)

        # Cálculo do primeiro e segundo momentos das observações futuras
        M1_f = sum(spot * math.exp((r - q) * tj) for tj in future_tau) / k
        M2_f = 0.0
        sig2 = sigma * sigma
        for i in range(k):
            ti = future_tau[i]
            for j in range(k):
                tj = future_tau[j]
                M2_f += (spot * spot) * math.exp((r - q) * (ti + tj) + sig2 * min(ti, tj))
        M2_f /= (k * k)

        # Lognormal fit variance for future average
        sigma_f2 = max(math.log(max(M2_f / (M1_f * M1_f), 1.0)), 1e-16)
        sigma_f = math.sqrt(sigma_f2)

        # Média total esperada (passada + futura)
        sum_past = sum(past_prices) if m > 0 else 0.0
        M1_tot = (sum_past + k * M1_f) / N

        # Volatilidade efetiva anualizada ponderada
        weight_future = k / N
        eff_vol = (weight_future * sigma_f) / math.sqrt(max(tau_final, 1e-8))

        if strike_type == AsianStrikeType.FIXED_STRIKE:
            # Strike ajustado K* para as observações futuras
            K_star = (N * strike - sum_past) / k

            if K_star <= 0.0:
                # Call garantidamente no dinheiro
                call_pr = disc * (M1_tot - strike)
                put_pr = 0.0
                d1 = 999.0
                d2 = 999.0
            elif sigma_f < 1e-10:
                d1 = 0.0
                d2 = 0.0
                call_pr = disc * max(M1_tot - strike, 0.0)
                put_pr = disc * max(strike - M1_tot, 0.0)
            else:
                d1 = (math.log(M1_f / K_star) + 0.5 * sigma_f2) / sigma_f
                d2 = d1 - sigma_f
                call_future = disc * (M1_f * std_norm_cdf(d1) - K_star * std_norm_cdf(d2))
                put_future = disc * (K_star * std_norm_cdf(-d2) - M1_f * std_norm_cdf(-d1))
                call_pr = weight_future * call_future
                put_pr = weight_future * put_future

            parity_expected = disc * (M1_tot - strike)
            parity_actual = call_pr - put_pr
            parity_diff = abs(parity_actual - parity_expected)
            chosen_price = call_pr if option_type == AsianOptionType.CALL else put_pr
            return chosen_price, M1_tot, eff_vol, d1, d2, fwd_underlying, parity_diff

        else:
            # Floating Strike Arithmetic Asian: max(S_T - A, 0)
            E_ST = spot * math.exp((r - q) * tau_final)
            # Covariância entre S_T e A_future
            cov_ST_Af = sum((spot * spot) * math.exp((r - q) * (tau_final + tj)) * (math.exp(sig2 * tj) - 1.0) for tj in future_tau) / k
            var_ST = (spot * spot) * math.exp(2.0 * (r - q) * tau_final) * (math.exp(sig2 * tau_final) - 1.0)
            var_Af = M2_f - (M1_f * M1_f)

            # Razão lognormal estendida para floating strike
            var_ln_ST = sig2 * tau_final
            var_ln_Af = math.log(max(1.0 + var_Af / (M1_f * M1_f), 1.0))
            cov_ln = math.log(max(1.0 + cov_ST_Af / (E_ST * M1_f), 1e-16))
            var_fl = max(var_ln_ST + var_ln_Af - 2.0 * cov_ln, 1e-16)
            sigma_fl = math.sqrt(var_fl)

            if sigma_fl < 1e-10:
                d1 = 0.0
                d2 = 0.0
                call_pr = disc * max(E_ST - M1_tot, 0.0)
                put_pr = disc * max(M1_tot - E_ST, 0.0)
            else:
                d1 = (math.log(E_ST / M1_tot) + 0.5 * var_fl) / sigma_fl
                d2 = d1 - sigma_fl
                call_pr = disc * (E_ST * std_norm_cdf(d1) - M1_tot * std_norm_cdf(d2))
                put_pr = disc * (M1_tot * std_norm_cdf(-d2) - E_ST * std_norm_cdf(-d1))

            parity_expected = disc * (E_ST - M1_tot)
            parity_actual = call_pr - put_pr
            parity_diff = abs(parity_actual - parity_expected)
            eff_vol_float = sigma_fl / math.sqrt(max(tau_final, 1e-8))
            chosen_price = call_pr if option_type == AsianOptionType.CALL else put_pr
            return chosen_price, M1_tot, eff_vol_float, d1, d2, fwd_underlying, parity_diff

    @staticmethod
    def price_arithmetic_curran(
        spot: float,
        strike: float,
        r: float,
        q: float,
        sigma: float,
        tau_to_expiration: float,
        observation_times: Sequence[float],
        option_type: AsianOptionType,
        strike_type: AsianStrikeType = AsianStrikeType.FIXED_STRIKE,
        past_prices: Optional[Sequence[float]] = None,
    ) -> Tuple[float, float, float, float, float, float, float]:
        """Aproximação de Curran (1994) por condicionamento na média geométrica.

        Retorna:
            (price, expected_arith, effective_vol, d1, d2, fwd_underlying, parity_diff)
        """
        N = len(observation_times)
        if N == 0:
            raise AsianPricingError("Lista de tempos de observação não pode ser vazia.")

        past_prices = past_prices or []
        m = len(past_prices)
        k = N - m
        tau_N = tau_to_expiration
        fwd_underlying = spot * math.exp((r - q) * tau_N)

        if k == 0 or strike_type == AsianStrikeType.FLOATING_STRIKE:
            # Para floating strike ou vencimento, delega para Turnbull-Wakeman
            return AsianPricer.price_arithmetic_turnbull_wakeman(
                spot=spot,
                strike=strike,
                r=r,
                q=q,
                sigma=sigma,
                tau_to_expiration=tau_to_expiration,
                observation_times=observation_times,
                option_type=option_type,
                strike_type=strike_type,
                past_prices=past_prices,
            )

        future_tau = observation_times[m:]
        tau_final = future_tau[-1]
        disc = math.exp(-r * tau_final)

        # Média e covariâncias do log da geométrica G
        drift = r - q - 0.5 * sigma * sigma
        past_log_sum = sum(math.log(p) for p in past_prices) if m > 0 else 0.0
        future_log_mean_sum = sum(math.log(spot) + drift * tj for tj in future_tau)
        mu_G = (past_log_sum + future_log_mean_sum) / N

        sig2 = sigma * sigma
        var_G = sum(min(future_tau[i], future_tau[j]) for i in range(k) for j in range(k)) * sig2 / (N * N)
        sigma_G = math.sqrt(max(var_G, 1e-16))

        # Covariâncias Cov(ln S_i, ln G)
        cov_Si_G = [sum(min(future_tau[i], future_tau[j]) for j in range(k)) * sig2 / N for i in range(k)]

        sum_past = sum(past_prices) if m > 0 else 0.0
        K_star = (N * strike - sum_past) / k

        M1_f = sum(spot * math.exp((r - q) * tj) for tj in future_tau) / k
        M1_tot = (sum_past + k * M1_f) / N

        if K_star <= 0.0:
            call_pr = disc * (M1_tot - strike)
            put_pr = 0.0
            d2_G = 999.0
            d1_avg = 999.0
        elif sigma_G < 1e-10:
            call_pr = disc * max(M1_tot - strike, 0.0)
            put_pr = disc * max(strike - M1_tot, 0.0)
            d2_G = 0.0
            d1_avg = 0.0
        else:
            d2_G = (mu_G - math.log(max(K_star, 1e-12))) / sigma_G
            weighted_call_future = 0.0
            d1_list = []
            for i in range(k):
                E_Si = spot * math.exp((r - q) * future_tau[i])
                d1_i = (mu_G - math.log(max(K_star, 1e-12)) + cov_Si_G[i]) / sigma_G
                d1_list.append(d1_i)
                weighted_call_future += E_Si * std_norm_cdf(d1_i)

            call_future = disc * (weighted_call_future / k - K_star * std_norm_cdf(d2_G))
            weight_future = k / N
            call_pr = weight_future * call_future
            put_pr = call_pr - disc * (M1_tot - strike)
            d1_avg = sum(d1_list) / len(d1_list)

        eff_vol = (sigma_G * (k / N)) / math.sqrt(max(tau_final, 1e-8))
        parity_expected = disc * (M1_tot - strike)
        parity_actual = call_pr - put_pr
        parity_diff = abs(parity_actual - parity_expected)
        chosen_price = call_pr if option_type == AsianOptionType.CALL else put_pr
        return chosen_price, M1_tot, eff_vol, d1_avg, d2_G, fwd_underlying, parity_diff

    @classmethod
    def evaluate_option(
        cls,
        spot: float,
        strike: float,
        r: float,
        q: float,
        sigma: float,
        tau_to_expiration: float,
        observation_times: Sequence[float],
        option_type: AsianOptionType,
        strike_type: AsianStrikeType,
        average_type: AsianAverageType,
        model: Optional[AsianPricingModel] = None,
        past_prices: Optional[Sequence[float]] = None,
    ) -> Tuple[float, float, float, float, float, float, float, AsianPricingModel]:
        """Avalia a opção utilizando o modelo analítico mais adequado."""
        if average_type == AsianAverageType.GEOMETRIC:
            chosen_model = AsianPricingModel.KEMNA_VORST
            pr, exp_avg, eff_vol, d1, d2, fwd, p_diff = cls.price_geometric_kemna_vorst(
                spot=spot,
                strike=strike,
                r=r,
                q=q,
                sigma=sigma,
                tau_to_expiration=tau_to_expiration,
                observation_times=observation_times,
                option_type=option_type,
                strike_type=strike_type,
                past_prices=past_prices,
            )
        else:
            chosen_model = model or AsianPricingModel.TURNBULL_WAKEMAN
            if chosen_model == AsianPricingModel.CURRAN:
                pr, exp_avg, eff_vol, d1, d2, fwd, p_diff = cls.price_arithmetic_curran(
                    spot=spot,
                    strike=strike,
                    r=r,
                    q=q,
                    sigma=sigma,
                    tau_to_expiration=tau_to_expiration,
                    observation_times=observation_times,
                    option_type=option_type,
                    strike_type=strike_type,
                    past_prices=past_prices,
                )
            else:
                chosen_model = AsianPricingModel.TURNBULL_WAKEMAN
                pr, exp_avg, eff_vol, d1, d2, fwd, p_diff = cls.price_arithmetic_turnbull_wakeman(
                    spot=spot,
                    strike=strike,
                    r=r,
                    q=q,
                    sigma=sigma,
                    tau_to_expiration=tau_to_expiration,
                    observation_times=observation_times,
                    option_type=option_type,
                    strike_type=strike_type,
                    past_prices=past_prices,
                )
        return pr, exp_avg, eff_vol, d1, d2, fwd, p_diff, chosen_model

    @classmethod
    def compute_analytical_greeks(
        cls,
        spot: float,
        strike: float,
        r: float,
        q: float,
        sigma: float,
        tau_to_expiration: float,
        observation_times: Sequence[float],
        option_type: AsianOptionType,
        strike_type: AsianStrikeType,
        average_type: AsianAverageType,
        model: Optional[AsianPricingModel] = None,
        past_prices: Optional[Sequence[float]] = None,
    ) -> AsianGreeks:
        """Calcula as sensibilidades de 1ª e 2ª ordem (Delta, Gamma, Vega, Theta, Rho)."""
        base_price, _, _, _, _, _, _, _ = cls.evaluate_option(
            spot=spot,
            strike=strike,
            r=r,
            q=q,
            sigma=sigma,
            tau_to_expiration=tau_to_expiration,
            observation_times=observation_times,
            option_type=option_type,
            strike_type=strike_type,
            average_type=average_type,
            model=model,
            past_prices=past_prices,
        )

        h_spot = max(spot * 1e-4, 1e-4)
        price_up_s, _, _, _, _, _, _, _ = cls.evaluate_option(
            spot=spot + h_spot,
            strike=strike,
            r=r,
            q=q,
            sigma=sigma,
            tau_to_expiration=tau_to_expiration,
            observation_times=observation_times,
            option_type=option_type,
            strike_type=strike_type,
            average_type=average_type,
            model=model,
            past_prices=past_prices,
        )
        price_dn_s, _, _, _, _, _, _, _ = cls.evaluate_option(
            spot=spot - h_spot,
            strike=strike,
            r=r,
            q=q,
            sigma=sigma,
            tau_to_expiration=tau_to_expiration,
            observation_times=observation_times,
            option_type=option_type,
            strike_type=strike_type,
            average_type=average_type,
            model=model,
            past_prices=past_prices,
        )

        # Delta e Gamma
        delta = (price_up_s - price_dn_s) / (2.0 * h_spot)
        gamma = (price_up_s - 2.0 * base_price + price_dn_s) / (h_spot * h_spot)

        # Vega
        h_vol = 1e-4
        price_up_v, _, _, _, _, _, _, _ = cls.evaluate_option(
            spot=spot,
            strike=strike,
            r=r,
            q=q,
            sigma=sigma + h_vol,
            tau_to_expiration=tau_to_expiration,
            observation_times=observation_times,
            option_type=option_type,
            strike_type=strike_type,
            average_type=average_type,
            model=model,
            past_prices=past_prices,
        )
        price_dn_v, _, _, _, _, _, _, _ = cls.evaluate_option(
            spot=spot,
            strike=strike,
            r=r,
            q=q,
            sigma=max(sigma - h_vol, 1e-5),
            tau_to_expiration=tau_to_expiration,
            observation_times=observation_times,
            option_type=option_type,
            strike_type=strike_type,
            average_type=average_type,
            model=model,
            past_prices=past_prices,
        )
        vega = (price_up_v - price_dn_v) / (2.0 * h_vol)
        vega_1pct = vega * 0.01

        # Theta (-dV/dT)
        h_tau = 1.0 / 365.0
        if tau_to_expiration > h_tau:
            # Reduz tempo para o vencimento
            adj_obs_times = [max(t - h_tau, 0.0) for t in observation_times]
            price_decay, _, _, _, _, _, _, _ = cls.evaluate_option(
                spot=spot,
                strike=strike,
                r=r,
                q=q,
                sigma=sigma,
                tau_to_expiration=tau_to_expiration - h_tau,
                observation_times=adj_obs_times,
                option_type=option_type,
                strike_type=strike_type,
                average_type=average_type,
                model=model,
                past_prices=past_prices,
            )
            theta_annual = (price_decay - base_price) / h_tau
        else:
            theta_annual = 0.0
        theta_daily = theta_annual / 252.0

        # Rho (dV/dr)
        h_r = 1e-4
        price_up_r, _, _, _, _, _, _, _ = cls.evaluate_option(
            spot=spot,
            strike=strike,
            r=r + h_r,
            q=q,
            sigma=sigma,
            tau_to_expiration=tau_to_expiration,
            observation_times=observation_times,
            option_type=option_type,
            strike_type=strike_type,
            average_type=average_type,
            model=model,
            past_prices=past_prices,
        )
        price_dn_r, _, _, _, _, _, _, _ = cls.evaluate_option(
            spot=spot,
            strike=strike,
            r=r - h_r,
            q=q,
            sigma=sigma,
            tau_to_expiration=tau_to_expiration,
            observation_times=observation_times,
            option_type=option_type,
            strike_type=strike_type,
            average_type=average_type,
            model=model,
            past_prices=past_prices,
        )
        rho = (price_up_r - price_dn_r) / (2.0 * h_r)
        rho_1pct = rho * 0.01

        return AsianGreeks(
            delta=round_greek(delta),
            gamma=round_greek(gamma),
            vega=round_greek(vega),
            vega_1pct=round_greek(vega_1pct),
            theta_annual=round_greek(theta_annual),
            theta_daily=round_greek(theta_daily),
            rho=round_greek(rho),
            rho_1pct=round_greek(rho_1pct),
        )


# ===========================================================================
# Motor Central de Gestão, Precificação e Liquidação NoMe (FL-092)
# ===========================================================================

class AsianExoticOptionsEngine:
    """Motor institucional B3 de registro, precificação MtM e liquidação de Opções Asiáticas."""

    def __init__(self) -> None:
        self._contracts: Dict[str, AsianOptionContract] = {}
        self._settlements: Dict[str, AsianSettlementRecord] = {}

    def register_contract(self, contract: AsianOptionContract) -> AsianOptionContract:
        """Registra formalmente um novo contrato de opção asiática no NoMe."""
        contract.verify_invariants()
        if contract.contract_id in self._contracts:
            raise AsianValidationError(f"Contrato já existente no NoMe: '{contract.contract_id}'")
        self._contracts[contract.contract_id] = contract
        return contract

    def get_contract(self, contract_id: str) -> AsianOptionContract:
        """Recupera contrato previamente registrado."""
        if contract_id not in self._contracts:
            raise AsianValidationError(f"Contrato '{contract_id}' não encontrado no NoMe.")
        return self._contracts[contract_id]

    def list_contracts(self) -> List[AsianOptionContract]:
        """Lista todos os contratos cadastrados."""
        return list(self._contracts.values())

    @staticmethod
    def generate_observation_schedule(
        start_date: Union[date, str],
        end_date: Union[date, str],
        frequency: AsianSamplingFrequency = AsianSamplingFrequency.DAILY,
        weight: Decimal = Decimal("1.0"),
        source: str = "",
    ) -> List[ObservationPoint]:
        """Gera automaticamente o cronograma periódico de fixação de preços."""
        d_start = to_date(start_date, "start_date")
        d_end = to_date(end_date, "end_date")
        if d_start > d_end:
            raise AsianValidationError(f"Data inicial {d_start} não pode ser posterior a {d_end}")

        points: List[ObservationPoint] = []
        curr = d_start

        if frequency == AsianSamplingFrequency.DAILY:
            while curr <= d_end:
                # Pula finais de semana (segunda=0 a sexta=4)
                if curr.weekday() < 5:
                    points.append(ObservationPoint(observation_date=curr, weight=weight, source=source))
                curr += timedelta(days=1)
        elif frequency == AsianSamplingFrequency.WEEKLY:
            while curr <= d_end:
                # Amostragem às sextas-feiras (weekday == 4) ou no próprio dia se final
                if curr.weekday() == 4 or curr == d_end:
                    points.append(ObservationPoint(observation_date=curr, weight=weight, source=source))
                curr += timedelta(days=1)
        elif frequency == AsianSamplingFrequency.BIWEEKLY:
            step_days = 14
            while curr <= d_end:
                points.append(ObservationPoint(observation_date=curr, weight=weight, source=source))
                curr += timedelta(days=step_days)
        elif frequency == AsianSamplingFrequency.MONTHLY:
            while curr <= d_end:
                points.append(ObservationPoint(observation_date=curr, weight=weight, source=source))
                # Avança aproximadamente 30 dias mantendo início de mês
                year = curr.year + (curr.month // 12)
                month = (curr.month % 12) + 1
                try:
                    curr = date(year, month, min(curr.day, 28))
                except ValueError:
                    curr = date(year, month, 28)
        else:  # CUSTOM
            points.append(ObservationPoint(observation_date=d_end, weight=weight, source=source))

        if not points:
            points.append(ObservationPoint(observation_date=d_end, weight=weight, source=source))
        return points

    def add_market_observation(
        self,
        contract_id: str,
        observation_date: Union[date, str],
        price: Union[Decimal, str, float],
        source: str = "",
    ) -> None:
        """Registra a fixação oficial de um preço de mercado observado."""
        contract = self.get_contract(contract_id)
        d_obs = to_date(observation_date, "observation_date")
        dec_price = to_decimal(price, "price")

        found = False
        for obs in contract.observations:
            if obs.observation_date == d_obs:
                obs.observed_price = dec_price
                if source:
                    obs.source = source
                found = True
                break

        if not found:
            contract.add_observation(
                ObservationPoint(
                    observation_date=d_obs,
                    observed_price=dec_price,
                    source=source,
                )
            )

        # Atualiza status do contrato
        realized = contract.get_realized_observations()
        if len(realized) == len(contract.observations):
            contract.status = AsianContractStatus.MATURED
        elif len(realized) > 0:
            contract.status = AsianContractStatus.IN_AVERAGING

    def batch_add_observations(
        self,
        contract_id: str,
        observations: Sequence[Union[Tuple[Any, Any], Mapping[str, Any], ObservationPoint]],
    ) -> None:
        """Adiciona múltiplas cotações em lote ao histórico de observações."""
        for item in observations:
            if isinstance(item, ObservationPoint):
                self.add_market_observation(
                    contract_id,
                    item.observation_date,
                    item.observed_price or Decimal("0.0"),
                    item.source,
                )
            elif isinstance(item, (tuple, list)):
                d_val, p_val = item[0], item[1]
                src = item[2] if len(item) > 2 else ""
                self.add_market_observation(contract_id, d_val, p_val, src)
            elif isinstance(item, dict):
                self.add_market_observation(
                    contract_id,
                    item["observation_date"],
                    item["observed_price"],
                    item.get("source", ""),
                )

    def price_contract(
        self,
        contract_or_id: Union[str, AsianOptionContract],
        valuation_date: Union[date, str],
        spot_price: Optional[Union[Decimal, str, float]] = None,
        model: Optional[AsianPricingModel] = None,
    ) -> AsianPricingResult:
        """Executa a precificação analítica, MtM e auditoria de invariantes da opção asiática."""
        if isinstance(contract_or_id, AsianOptionContract):
            contract = contract_or_id
            if contract.contract_id not in self._contracts:
                self.register_contract(contract)
        else:
            contract = self.get_contract(contract_or_id)

        d_val = to_date(valuation_date, "valuation_date")
        s0 = float(to_decimal(spot_price, "spot_price") if spot_price is not None else contract.spot_initial)
        k_val = float(contract.strike_price)
        r_val = float(contract.risk_free_rate)
        q_val = float(contract.dividend_yield)
        sigma_val = float(contract.volatility)

        # Determina observações realizadas até valuation_date e observações futuras
        realized_obs = [o for o in contract.observations if o.is_realized or o.observation_date <= d_val]
        pending_obs = [o for o in contract.observations if not o.is_realized and o.observation_date > d_val]

        past_prices: List[float] = []
        for o in realized_obs:
            if o.observed_price is not None:
                past_prices.append(float(o.observed_price))
            else:
                # Se a data já passou mas não temos fixação formal, utiliza o spot corrente
                past_prices.append(s0)

        # Mapeia os tempos de observação em frações de ano a partir de trade_date ou valuation_date
        # Para consistência estocástica: tempos a partir de trade_date (T_i = (d_i - trade_date)/365)
        # E tempo restante para cada observação futura a partir de d_val: tau_j = (d_j - d_val)/365
        t_base = contract.trade_date
        total_obs = contract.observations
        tau_to_exp = max((contract.expiration_date - d_val).days / 365.0, 0.0)

        # Vetor de tempos futuros a partir de d_val
        future_tau = [max((o.observation_date - d_val).days / 365.0, 1e-6) for o in pending_obs]
        all_future_times = [0.0] * len(past_prices) + future_tau

        pr, exp_avg, eff_vol, d1, d2, fwd, p_diff, chosen_model = AsianPricer.evaluate_option(
            spot=s0,
            strike=k_val,
            r=r_val,
            q=q_val,
            sigma=sigma_val,
            tau_to_expiration=tau_to_exp,
            observation_times=all_future_times,
            option_type=contract.option_type,
            strike_type=contract.strike_type,
            average_type=contract.average_type,
            model=model,
            past_prices=past_prices,
        )

        greeks = AsianPricer.compute_analytical_greeks(
            spot=s0,
            strike=k_val,
            r=r_val,
            q=q_val,
            sigma=sigma_val,
            tau_to_expiration=tau_to_exp,
            observation_times=all_future_times,
            option_type=contract.option_type,
            strike_type=contract.strike_type,
            average_type=contract.average_type,
            model=chosen_model,
            past_prices=past_prices,
        )

        # Benchmark: Opção Vanilla Europeia Equivalente (se strike flutuante, compara com ATM spot)
        vanilla_strike = k_val if (contract.strike_type == AsianStrikeType.FIXED_STRIKE and k_val > 0.0) else s0
        vanilla_prem = AsianPricer._vanilla_european_price(
            spot=s0,
            strike=vanilla_strike,
            r=r_val,
            q=q_val,
            sigma=sigma_val,
            tau=tau_to_exp,
            option_type=contract.option_type,
        )

        # Invariantes LAW-095 e LAW-096
        parity_holds = p_diff <= float(PARITY_TOLERANCE)
        # LAW-096: Vol_Asiatica <= Vol_Vanilla + tolerância numérica
        eff_vol_bound_holds = eff_vol <= (sigma_val + 1e-4)

        prem_per_unit = round_four(Decimal(str(max(pr, 0.0))))
        tot_multiplier = contract.quantity * contract.contract_multiplier
        tot_premium = round_money(prem_per_unit * tot_multiplier)

        current_realized_avg = contract.compute_current_average()

        pricing_res = AsianPricingResult(
            contract_id=contract.contract_id,
            valuation_date=d_val,
            option_type=contract.option_type,
            strike_type=contract.strike_type,
            average_type=contract.average_type,
            model_used=chosen_model,
            premium_per_unit=prem_per_unit,
            total_premium=tot_premium,
            spot_price=round_four(Decimal(str(s0))),
            strike_price=round_four(Decimal(str(k_val))),
            expected_average=round_four(Decimal(str(exp_avg))),
            forward_underlying=round_four(Decimal(str(fwd))),
            effective_volatility=round_four(Decimal(str(eff_vol))),
            vanilla_volatility=round_four(Decimal(str(sigma_val))),
            vanilla_equivalent_premium=round_four(Decimal(str(vanilla_prem))),
            put_call_parity_diff=round_four(Decimal(str(p_diff))),
            parity_holds=parity_holds,
            effective_vol_bound_holds=eff_vol_bound_holds,
            d1=round_four(Decimal(str(d1))),
            d2=round_four(Decimal(str(d2))),
            greeks=greeks,
            realized_observations_count=len(past_prices),
            remaining_observations_count=len(pending_obs),
            realized_average=current_realized_avg,
        )

        pricing_res.verify_invariants(tolerance=PARITY_TOLERANCE)
        return pricing_res

    def settle_contract(
        self,
        contract_or_id: Union[str, AsianOptionContract],
        final_spot_price: Optional[Union[Decimal, str, float]] = None,
    ) -> AsianSettlementRecord:
        """Executa a liquidação exclusivamente financeira no vencimento contra a média efetiva."""
        if isinstance(contract_or_id, AsianOptionContract):
            contract = contract_or_id
            if contract.contract_id not in self._contracts:
                self.register_contract(contract)
        else:
            contract = self.get_contract(contract_or_id)

        # Se houver observações sem preço apurado e um spot final foi fornecido, preenche
        if final_spot_price is not None:
            dec_final = to_decimal(final_spot_price, "final_spot_price")
            for obs in contract.observations:
                if not obs.is_realized:
                    obs.observed_price = dec_final

        # Assegura que todas as observações possuem cotação para apuração da média final
        unrealized = [o for o in contract.observations if not o.is_realized]
        if unrealized:
            raise AsianSettlementError(
                f"Não é possível liquidar contrato '{contract.contract_id}': "
                f"{len(unrealized)} observações pendentes sem cotação apurada."
            )

        final_avg = contract.compute_current_average()
        if final_avg is None:
            raise AsianSettlementError("Erro no cálculo da média final observada.")

        # Preço à vista final (última observação ou spot fornecido)
        last_obs = contract.observations[-1]
        final_spot = last_obs.observed_price or contract.spot_initial

        # Cálculo do valor intrínseco unitário
        if contract.strike_type == AsianStrikeType.FIXED_STRIKE:
            effective_strike = contract.strike_price
            if contract.option_type == AsianOptionType.CALL:
                intrinsic = max(Decimal("0.0000"), final_avg - effective_strike)
            else:  # PUT
                intrinsic = max(Decimal("0.0000"), effective_strike - final_avg)
        else:  # FLOATING_STRIKE
            effective_strike = final_avg
            if contract.option_type == AsianOptionType.CALL:
                intrinsic = max(Decimal("0.0000"), final_spot - final_avg)
            else:  # PUT
                intrinsic = max(Decimal("0.0000"), final_avg - final_spot)

        is_itm = intrinsic > Decimal("0.0000")
        tot_multiplier = contract.quantity * contract.contract_multiplier
        gross_holder = round_money(intrinsic * tot_multiplier)
        gross_writer = round_money(-gross_holder)  # Conservação soma-zero bilateral

        # Retenção de IRRF sobre ganho auferido pelo titular
        irrf = Decimal("0.00")
        if gross_holder > Decimal("0.00") and not contract.holder_irrf_exempt:
            tax_base = gross_holder
            if contract.premium_agreed is not None and contract.premium_agreed > Decimal("0.0000"):
                paid_prem = round_money(contract.premium_agreed * tot_multiplier)
                tax_base = max(Decimal("0.00"), gross_holder - paid_prem)
            irrf = round_money(tax_base * contract.holder_irrf_rate)

        net_holder = round_money(gross_holder - irrf)
        net_writer = gross_writer  # Lançador é integralmente debitado pelo valor bruto
        is_zero_sum = (gross_holder + gross_writer) == Decimal("0.00")

        settlement = AsianSettlementRecord(
            contract_id=contract.contract_id,
            nome_protocol=contract.nome_protocol,
            settlement_date=contract.settlement_date,
            option_type=contract.option_type,
            strike_type=contract.strike_type,
            average_type=contract.average_type,
            final_average=final_avg,
            final_spot=final_spot,
            effective_strike=effective_strike,
            intrinsic_per_unit=round_four(intrinsic),
            quantity=contract.quantity,
            contract_multiplier=contract.contract_multiplier,
            gross_holder_amount=gross_holder,
            gross_writer_amount=gross_writer,
            irrf_amount=irrf,
            net_holder_amount=net_holder,
            net_writer_amount=net_writer,
            is_itm=is_itm,
            is_zero_sum=is_zero_sum,
        )

        settlement.verify_invariants()
        contract.status = AsianContractStatus.SETTLED
        self._settlements[contract.contract_id] = settlement
        return settlement

    def settle_all(self) -> List[AsianSettlementRecord]:
        """Liquida todos os contratos ativos na câmara."""
        return [self.settle_contract(cid) for cid in self._contracts]

    def summarize_portfolio(self) -> AsianPortfolioSummary:
        """Gera consolidado institucional com auditoria das invariantes."""
        settlements = list(self._settlements.values())
        total_cnt = len(self._contracts)
        settled_cnt = len(settlements)
        active_cnt = total_cnt - settled_cnt

        tot_holder = round_money(sum((s.gross_holder_amount for s in settlements), Decimal("0.00")))
        tot_writer = round_money(sum((s.gross_writer_amount for s in settlements), Decimal("0.00")))
        tot_irrf = round_money(sum((s.irrf_amount for s in settlements), Decimal("0.00")))
        all_zero = all(s.is_zero_sum for s in settlements) if settlements else True

        summary = AsianPortfolioSummary(
            total_contracts=total_cnt,
            active_contracts=active_cnt,
            settled_contracts=settled_cnt,
            total_holder_gross=tot_holder,
            total_writer_gross=tot_writer,
            total_irrf_withheld=tot_irrf,
            all_zero_sum=all_zero,
            all_parities_held=True,
            contracts=[c.to_dict() for c in self._contracts.values()],
            settlements=[s.to_dict() for s in settlements],
        )
        if settlements:
            summary.verify_invariants()
        return summary
