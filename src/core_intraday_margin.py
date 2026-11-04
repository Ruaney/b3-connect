"""Módulo de Risco e Margem Intradiária CORE B3 (FL-050).

Implementa a metodologia Closeout Risk Evaluation (CORE) da Câmara B3 para
cálculo determinístico de risco de liquidação e chamadas de margem intradiárias:
- Matriz de Cenários Primitivos de Risco: choques de preço à vista (+/- 10%, +/- 20%),
  volatilidade implícita (+/- 15%, +/- 30%), vértices da curva de juros pré (DI1 DU/252)
  e choques cambiais (DOL USD/BRL).
- Reavaliação Integral (Full Repricing) multi-ativo: ações à vista, opções de ações (BSM),
  futuros de taxa de juros (DI1) e futuros de dólar comercial (DOL).
- Efeito Portfólio e Compensação Multilateral: compensação direta intragrupo/ativo objeto
  e agregação conjunta entre classes com aditivos regulatórios de liquidez e concentração.
- Apuração do Pior Cenário de Perda (Worst-Case Closeout Loss).
- Gestão de Cesta de Colaterais com Haircuts Regulatórios B3 (Dinheiro, TPF, USD, Ações).
- Gatilho Determinístico de Chamada de Margem Adicional Intradiária (Intraday Margin Call).
- Alta performance em memória com tempo de resposta em microssegundos (< 1 ms).

Zero dependências externas (100% Python Standard Library).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
import math
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")


# ==============================================================================
# 1. Enumerações e Tipos do Modelo CORE
# ==============================================================================

class AssetCategory(str, Enum):
    """Categorias de instrumentos financeiros suportados pelo motor CORE."""
    EQUITY = "EQUITY"
    OPTION_CALL = "OPTION_CALL"
    OPTION_PUT = "OPTION_PUT"
    FUTURE_DI = "FUTURE_DI"
    FUTURE_DOL = "FUTURE_DOL"
    OTHER = "OTHER"


class MarginStatus(str, Enum):
    """Status regulamentar da apuração de margem da conta."""
    NORMAL = "NORMAL"                         # Garantias cobrem integralmente o risco
    WARNING = "WARNING"                       # Déficit dentro da tolerância regulamentar
    MARGIN_CALL_REQUIRED = "MARGIN_CALL_REQUIRED"  # Chamada de margem intradiária disparada
    CRITICAL = "CRITICAL"                     # Déficit crítico com risco iminente de closeout


class CollateralType(str, Enum):
    """Tipos de garantias aceitas pela Câmara B3 e haircuts regulatórios padrão."""
    CASH_BRL = "CASH_BRL"                     # Moeda corrente nacional (0% haircut)
    FEDERAL_BONDS_LFT = "FEDERAL_BONDS_LFT"   # Títulos Públicos Federais pós-fixados (2% haircut)
    FEDERAL_BONDS_LTN = "FEDERAL_BONDS_LTN"   # Títulos Públicos Federais pré-fixados (3.5% haircut)
    FEDERAL_BONDS_NTNF = "FEDERAL_BONDS_NTNF" # TPF com cupom semestral (4% haircut)
    CASH_USD = "CASH_USD"                     # Moeda estrangeira em conta no exterior (10% haircut)
    EQUITY_IBRX = "EQUITY_IBRX"               # Ações líquidas pertencentes ao IBrX-100 (20% haircut)
    GOLD_OZ1D = "GOLD_OZ1D"                   # Ouro físico em custódia na B3 (10% haircut)


# ==============================================================================
# 2. Exceções Especializadas
# ==============================================================================

class CoreRiskError(Exception):
    """Exceção base do motor de risco CORE da Câmara B3."""
    pass


class InvalidPositionError(CoreRiskError):
    """Lançada quando os parâmetros de uma posição são inconsistentes."""
    pass


class ScenarioEvaluationError(CoreRiskError):
    """Lançada quando ocorre erro durante a reavaliação de um cenário de estresse."""
    pass


# ==============================================================================
# 3. Funções Numéricas e Modelo Analítico Black-Scholes
# ==============================================================================

def _std_norm_cdf(x: float) -> float:
    """Função de distribuição acumulada da normal padrão N(x) via função erro."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _black_scholes_price(
    spot: float,
    strike: float,
    maturity_years: float,
    rate: float,
    vol: float,
    is_call: bool,
) -> float:
    """Precificação analítica Black-Scholes de opções européias.
    
    Permite reavaliação integral ultra-rápida (nanossegundos) de prêmios teóricos
    sob choques simultâneos de preço do ativo objeto, volatilidade e juros.
    """
    if maturity_years <= 0.0:
        return max(0.0, spot - strike) if is_call else max(0.0, strike - spot)
    
    if vol <= 1e-6:
        discount = math.exp(-rate * maturity_years)
        forward = spot / discount
        return discount * (max(0.0, forward - strike) if is_call else max(0.0, strike - forward))

    sqrt_t = math.sqrt(maturity_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * maturity_years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    discount = math.exp(-rate * maturity_years)

    if is_call:
        price = spot * _std_norm_cdf(d1) - strike * discount * _std_norm_cdf(d2)
    else:
        price = strike * discount * _std_norm_cdf(-d2) - spot * _std_norm_cdf(-d1)
    
    return max(0.0, price)


def _round_currency(val: Union[Decimal, float, int]) -> Decimal:
    """Arredonda valor monetário para 2 casas decimais regulamentares (BRL centavos)."""
    if isinstance(val, (float, int)):
        val = Decimal(str(round(val, 6)))
    return val.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ==============================================================================
# 4. Estruturas de Dados Fundamentais
# ==============================================================================

@dataclass
class RiskScenario:
    """Cenário de estresse conjunto para fatores primitivos de risco da B3.
    
    Atributos:
        scenario_id: Identificador unívoco do cenário (ex: 'SCN_CRASH_20').
        name: Nome descritivo do cenário.
        description: Detalhes dos choques aplicados.
        equity_shocks: Choques percentuais em ações (ex: {'PETR4': Decimal('-0.20')}).
        volatility_shocks: Choques relativos na volatilidade implícita (ex: {'PETR4': Decimal('0.30')}).
        interest_rate_shocks: Choques absolutos em taxas anuais (ex: {'PRE_CURVE': Decimal('0.0150')}).
        fx_shocks: Choques percentuais em moedas (ex: {'USD_BRL': Decimal('0.10')}).
        probability_weight: Peso/probabilidade associada (opcional, padrão 1.0).
    """
    scenario_id: str
    name: str
    description: str = ""
    equity_shocks: Dict[str, Decimal] = field(default_factory=dict)
    volatility_shocks: Dict[str, Decimal] = field(default_factory=dict)
    interest_rate_shocks: Dict[str, Decimal] = field(default_factory=dict)
    fx_shocks: Dict[str, Decimal] = field(default_factory=dict)
    probability_weight: Decimal = Decimal("1.0")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "name": self.name,
            "description": self.description,
            "equity_shocks": {k: str(v) for k, v in self.equity_shocks.items()},
            "volatility_shocks": {k: str(v) for k, v in self.volatility_shocks.items()},
            "interest_rate_shocks": {k: str(v) for k, v in self.interest_rate_shocks.items()},
            "fx_shocks": {k: str(v) for k, v in self.fx_shocks.items()},
            "probability_weight": str(self.probability_weight),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RiskScenario:
        return cls(
            scenario_id=data["scenario_id"],
            name=data["name"],
            description=data.get("description", ""),
            equity_shocks={k: Decimal(str(v)) for k, v in data.get("equity_shocks", {}).items()},
            volatility_shocks={k: Decimal(str(v)) for k, v in data.get("volatility_shocks", {}).items()},
            interest_rate_shocks={k: Decimal(str(v)) for k, v in data.get("interest_rate_shocks", {}).items()},
            fx_shocks={k: Decimal(str(v)) for k, v in data.get("fx_shocks", {}).items()},
            probability_weight=Decimal(str(data.get("probability_weight", "1.0"))),
        )


@dataclass
class PositionItem:
    """Posição custodiada ou em aberto no mercado listado da B3.
    
    Atributos:
        position_id: Identificador da posição (ex: 'POS_PETR4_01').
        symbol: Código de negociação do ativo na B3 (ex: 'PETR4', 'DI1F27', 'DOLZ26', 'PETRL360').
        asset_category: Categoria de risco do ativo.
        underlying_symbol: Ativo objeto fundamental (ex: 'PETR4', 'USD_BRL', 'PRE_CURVE').
        quantity: Quantidade em aberto (positivo = comprado / long, negativo = vendido / short).
        current_price: Preço unitário corrente de referência (R$, taxa ou cotação).
        contract_multiplier: Multiplicador financeiro do contrato (padrão 1.0 para ações/opções, 50 para DOL).
        strike: Preço de exercício (para opções).
        maturity_years: Prazo a decorrer até o vencimento em frações de ano (para opções).
        implied_vol: Volatilidade implícita anualizada corrente (ex: 0.32 para 32%).
        risk_free_rate: Taxa livre de risco anual contínua para precificação (ex: 0.1175).
        business_days: Dias úteis até o vencimento (para contratos futuros de taxa de juros DI1).
        annual_rate: Taxa de juros anual base 252 (para contratos futuros de taxa de juros DI1).
    """
    position_id: str
    symbol: str
    asset_category: AssetCategory
    underlying_symbol: str
    quantity: Decimal
    current_price: Decimal
    contract_multiplier: Decimal = Decimal("1.0")
    strike: Optional[Decimal] = None
    maturity_years: Optional[Decimal] = None
    implied_vol: Optional[Decimal] = None
    risk_free_rate: Optional[Decimal] = None
    business_days: Optional[int] = None
    annual_rate: Optional[Decimal] = None

    def __post_init__(self) -> None:
        if isinstance(self.asset_category, str):
            self.asset_category = AssetCategory(self.asset_category)
        if not isinstance(self.quantity, Decimal):
            self.quantity = Decimal(str(self.quantity))
        if not isinstance(self.current_price, Decimal):
            self.current_price = Decimal(str(self.current_price))
        if not isinstance(self.contract_multiplier, Decimal):
            self.contract_multiplier = Decimal(str(self.contract_multiplier))
        if self.strike is not None and not isinstance(self.strike, Decimal):
            self.strike = Decimal(str(self.strike))
        if self.maturity_years is not None and not isinstance(self.maturity_years, Decimal):
            self.maturity_years = Decimal(str(self.maturity_years))
        if self.implied_vol is not None and not isinstance(self.implied_vol, Decimal):
            self.implied_vol = Decimal(str(self.implied_vol))
        if self.risk_free_rate is not None and not isinstance(self.risk_free_rate, Decimal):
            self.risk_free_rate = Decimal(str(self.risk_free_rate))
        if self.annual_rate is not None and not isinstance(self.annual_rate, Decimal):
            self.annual_rate = Decimal(str(self.annual_rate))

    @property
    def gross_market_value(self) -> Decimal:
        """Valor financeiro nocional bruto de mercado da posição."""
        if self.asset_category == AssetCategory.FUTURE_DI and self.annual_rate is not None and self.business_days:
            du_factor = Decimal(str(self.business_days)) / Decimal("252")
            base = Decimal("1") + self.annual_rate
            pu = Decimal("100000") / (base ** du_factor)
            return abs(self.quantity) * pu * self.contract_multiplier
        return abs(self.quantity) * self.current_price * self.contract_multiplier

    def calculate_scenario_pnl(self, scenario: RiskScenario) -> Decimal:
        """Calcula a variação do valor financeiro (PnL) da posição sob um cenário de estresse."""
        cat = self.asset_category
        qty = self.quantity
        multiplier = self.contract_multiplier

        # 1. Ações à Vista (Equities)
        if cat == AssetCategory.EQUITY:
            shock = scenario.equity_shocks.get(self.symbol)
            if shock is None:
                shock = scenario.equity_shocks.get(self.underlying_symbol, Decimal("0.0"))
            new_price = self.current_price * (Decimal("1.0") + shock)
            delta_p = new_price - self.current_price
            return _round_currency(qty * delta_p * multiplier)

        # 2. Opções sobre Ações (Calls e Puts via Black-Scholes)
        elif cat in (AssetCategory.OPTION_CALL, AssetCategory.OPTION_PUT):
            spot_shock = scenario.equity_shocks.get(self.underlying_symbol, Decimal("0.0"))
            vol_shock = scenario.volatility_shocks.get(self.symbol)
            if vol_shock is None:
                vol_shock = scenario.volatility_shocks.get(self.underlying_symbol, Decimal("0.0"))
            rate_shock = scenario.interest_rate_shocks.get("PRE_CURVE", Decimal("0.0"))

            base_spot = float(self.current_price) if self.strike is None else float(self.current_price)
            # Se current_price da opção for o prêmio de mercado, precisamos do spot do ativo objeto:
            # Em modelagem prática, podemos inferir o spot ou usar underlying_price
            spot_val = float(self.current_price)
            strike_val = float(self.strike) if self.strike is not None else spot_val
            mat_val = float(self.maturity_years) if self.maturity_years is not None else 0.25
            vol_val = float(self.implied_vol) if self.implied_vol is not None else 0.30
            r_val = float(self.risk_free_rate) if self.risk_free_rate is not None else 0.1175
            is_call = (cat == AssetCategory.OPTION_CALL)

            # Spot inicial do ativo objeto (se strike estiver definido e a opção for cotada pelo prêmio)
            # Para reavaliação canônica, tratamos o ativo objeto sob choque:
            # Assumimos que o preço de referência do ativo objeto é o strike caso não fornecido,
            # ou que strike e spot foram devidamente informados.
            spot_0 = strike_val if abs(spot_val - strike_val) > strike_val * 0.8 else spot_val
            spot_shocked = max(0.01, spot_0 * (1.0 + float(spot_shock)))
            vol_shocked = max(0.01, vol_val * (1.0 + float(vol_shock)))
            rate_shocked = max(0.0, r_val + float(rate_shock))

            p0 = _black_scholes_price(spot_0, strike_val, mat_val, r_val, vol_val, is_call)
            p_new = _black_scholes_price(spot_shocked, strike_val, mat_val, rate_shocked, vol_shocked, is_call)

            delta_price = Decimal(str(p_new - p0))
            return _round_currency(qty * delta_price * multiplier)

        # 3. Futuros de DI de 1 Dia (DI1)
        elif cat == AssetCategory.FUTURE_DI:
            rate_shock = scenario.interest_rate_shocks.get(self.symbol)
            if rate_shock is None:
                rate_shock = scenario.interest_rate_shocks.get(self.underlying_symbol)
            if rate_shock is None:
                rate_shock = scenario.interest_rate_shocks.get("PRE_CURVE", Decimal("0.0"))

            annual_r = self.annual_rate if self.annual_rate is not None else (self.current_price / Decimal("100"))
            du = self.business_days if self.business_days is not None else 252

            du_factor = Decimal(str(du)) / Decimal("252")
            base_0 = Decimal("1") + annual_r
            pu_0 = Decimal("100000") / (base_0 ** du_factor)

            r_shocked = max(Decimal("0.0001"), annual_r + rate_shock)
            base_shocked = Decimal("1") + r_shocked
            pu_shocked = Decimal("100000") / (base_shocked ** du_factor)

            delta_pu = pu_shocked - pu_0
            return _round_currency(qty * delta_pu * multiplier)

        # 4. Futuros de Dólar Comercial (DOL)
        elif cat == AssetCategory.FUTURE_DOL:
            fx_shock = scenario.fx_shocks.get(self.symbol)
            if fx_shock is None:
                fx_shock = scenario.fx_shocks.get(self.underlying_symbol)
            if fx_shock is None:
                fx_shock = scenario.fx_shocks.get("USD_BRL", Decimal("0.0"))

            new_price = self.current_price * (Decimal("1.0") + fx_shock)
            delta_price = new_price - self.current_price
            return _round_currency(qty * delta_price * multiplier)

        # 5. Outros ativos lineares
        else:
            generic_shock = scenario.equity_shocks.get(self.symbol, Decimal("0.0"))
            delta_price = self.current_price * generic_shock
            return _round_currency(qty * delta_price * multiplier)

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "asset_category": self.asset_category.value,
            "underlying_symbol": self.underlying_symbol,
            "quantity": str(self.quantity),
            "current_price": str(self.current_price),
            "contract_multiplier": str(self.contract_multiplier),
        }
        if self.strike is not None:
            res["strike"] = str(self.strike)
        if self.maturity_years is not None:
            res["maturity_years"] = str(self.maturity_years)
        if self.implied_vol is not None:
            res["implied_vol"] = str(self.implied_vol)
        if self.risk_free_rate is not None:
            res["risk_free_rate"] = str(self.risk_free_rate)
        if self.business_days is not None:
            res["business_days"] = self.business_days
        if self.annual_rate is not None:
            res["annual_rate"] = str(self.annual_rate)
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PositionItem:
        return cls(
            position_id=data["position_id"],
            symbol=data["symbol"],
            asset_category=AssetCategory(data["asset_category"]),
            underlying_symbol=data["underlying_symbol"],
            quantity=Decimal(str(data["quantity"])),
            current_price=Decimal(str(data["current_price"])),
            contract_multiplier=Decimal(str(data.get("contract_multiplier", "1.0"))),
            strike=Decimal(str(data["strike"])) if "strike" in data and data["strike"] is not None else None,
            maturity_years=Decimal(str(data["maturity_years"])) if "maturity_years" in data and data["maturity_years"] is not None else None,
            implied_vol=Decimal(str(data["implied_vol"])) if "implied_vol" in data and data["implied_vol"] is not None else None,
            risk_free_rate=Decimal(str(data["risk_free_rate"])) if "risk_free_rate" in data and data["risk_free_rate"] is not None else None,
            business_days=int(data["business_days"]) if "business_days" in data and data["business_days"] is not None else None,
            annual_rate=Decimal(str(data["annual_rate"])) if "annual_rate" in data and data["annual_rate"] is not None else None,
        )


@dataclass
class CollateralItem:
    """Garantia depositada pelo participante para cobertura de margem na B3.
    
    Atributos:
        collateral_id: Identificador unívoco do ativo em garantia.
        collateral_type: Categoria regulamentar de colateral B3.
        description: Descrição do ativo (ex: 'LFT 2027', 'USD Cash Citibank NY').
        nominal_value: Valor de mercado bruto do ativo depositado.
        haircut_pct: Desconto regulamentar aplicável (0.0 a 1.0).
    """
    collateral_id: str
    collateral_type: CollateralType
    description: str
    nominal_value: Decimal
    haircut_pct: Decimal

    def __post_init__(self) -> None:
        if isinstance(self.collateral_type, str):
            self.collateral_type = CollateralType(self.collateral_type)
        if not isinstance(self.nominal_value, Decimal):
            self.nominal_value = Decimal(str(self.nominal_value))
        if not isinstance(self.haircut_pct, Decimal):
            self.haircut_pct = Decimal(str(self.haircut_pct))

    @property
    def effective_value(self) -> Decimal:
        """Valor financeiro líquido elegível pós-haircut regulatório."""
        multiplier = max(Decimal("0.0"), Decimal("1.0") - self.haircut_pct)
        return _round_currency(self.nominal_value * multiplier)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "collateral_id": self.collateral_id,
            "collateral_type": self.collateral_type.value,
            "description": self.description,
            "nominal_value": str(self.nominal_value),
            "haircut_pct": str(self.haircut_pct),
            "effective_value": str(self.effective_value),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CollateralItem:
        return cls(
            collateral_id=data["collateral_id"],
            collateral_type=CollateralType(data["collateral_type"]),
            description=data.get("description", ""),
            nominal_value=Decimal(str(data["nominal_value"])),
            haircut_pct=Decimal(str(data["haircut_pct"])),
        )


@dataclass
class StressScenarioResult:
    """Resultado da simulação de estresse para um cenário específico sob o modelo CORE.
    
    Atributos:
        scenario_id: Identificador do cenário testado.
        scenario_name: Nome do cenário.
        portfolio_pnl: Resultado líquido total somado da carteira no cenário.
        portfolio_loss: Perda financeira da carteira (max(0, -portfolio_pnl)).
        position_pnls: Variação de valor por identificador de posição.
        asset_class_pnls: Variação de valor agregada por classe de ativo.
    """
    scenario_id: str
    scenario_name: str
    portfolio_pnl: Decimal
    portfolio_loss: Decimal
    position_pnls: Dict[str, Decimal] = field(default_factory=dict)
    asset_class_pnls: Dict[str, Decimal] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "portfolio_pnl": str(self.portfolio_pnl),
            "portfolio_loss": str(self.portfolio_loss),
            "position_pnls": {k: str(v) for k, v in self.position_pnls.items()},
            "asset_class_pnls": {k: str(v) for k, v in self.asset_class_pnls.items()},
        }


@dataclass
class IntradayMarginCalculation:
    """Resultado consolidado da apuração contínua de risco e margem CORE B3.
    
    Atributos:
        calculation_id: Identificador unívoco do cálculo de margem.
        account_id: Conta de liquidação ou código do investidor/participante.
        timestamp: Carimbo de data/hora da apuração.
        positions_evaluated_count: Total de posições ativas avaliadas.
        scenarios_evaluated_count: Total de cenários de estresse simulados.
        worst_case_scenario_id: Cenário que produziu a maior perda de liquidação.
        worst_case_loss: Montante da perda máxima no pior cenário (WCL).
        liquidity_addon: Adicional regulamentar de liquidez.
        concentration_addon: Adicional regulamentar de concentração de risco.
        total_margin_required: Margem total requerida pela câmara (WCL + Addons).
        total_collateral_deposited: Valor nominal total de garantias depositadas.
        total_collateral_available: Valor líquido de garantias pós-haircuts regulatórios.
        collateral_breakdown: Lista discriminada de garantias aceitas e haircuts.
        risk_deficit: Déficit de garantia projetado (max(0, Margem - Colateral)).
        regulatory_tolerance: Tolerância regulamentar permitida antes de disparar chamada.
        margin_call_triggered: Indicador se a chamada de margem intradiária foi acionada.
        margin_call_amount: Valor da margem adicional exigida a ser aportada.
        status: Estado de risco da conta (NORMAL, WARNING, MARGIN_CALL_REQUIRED, CRITICAL).
        calculation_latency_us: Latência de cálculo em microssegundos.
        scenario_results: Lista detalhada de resultados por cenário de estresse.
    """
    calculation_id: str
    account_id: str
    timestamp: datetime
    positions_evaluated_count: int
    scenarios_evaluated_count: int
    worst_case_scenario_id: str
    worst_case_loss: Decimal
    liquidity_addon: Decimal
    concentration_addon: Decimal
    total_margin_required: Decimal
    total_collateral_deposited: Decimal
    total_collateral_available: Decimal
    risk_deficit: Decimal
    regulatory_tolerance: Decimal
    margin_call_triggered: bool
    margin_call_amount: Decimal
    status: MarginStatus
    calculation_latency_us: float
    collateral_breakdown: List[Dict[str, Any]] = field(default_factory=list)
    scenario_results: List[StressScenarioResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "calculation_id": self.calculation_id,
            "account_id": self.account_id,
            "timestamp": self.timestamp.isoformat(),
            "positions_evaluated_count": self.positions_evaluated_count,
            "scenarios_evaluated_count": self.scenarios_evaluated_count,
            "worst_case_scenario_id": self.worst_case_scenario_id,
            "worst_case_loss": str(self.worst_case_loss),
            "liquidity_addon": str(self.liquidity_addon),
            "concentration_addon": str(self.concentration_addon),
            "total_margin_required": str(self.total_margin_required),
            "total_collateral_deposited": str(self.total_collateral_deposited),
            "total_collateral_available": str(self.total_collateral_available),
            "risk_deficit": str(self.risk_deficit),
            "regulatory_tolerance": str(self.regulatory_tolerance),
            "margin_call_triggered": self.margin_call_triggered,
            "margin_call_amount": str(self.margin_call_amount),
            "status": self.status.value,
            "calculation_latency_us": round(self.calculation_latency_us, 2),
            "collateral_breakdown": self.collateral_breakdown,
            "scenario_results": [s.to_dict() for s in self.scenario_results],
        }


# ==============================================================================
# 5. Motor de Risco CORE da Câmara B3 (CoreRiskEngine)
# ==============================================================================

class CoreRiskEngine:
    """Motor determinístico de risco e cálculo de margem intradiária CORE B3.
    
    Implementa:
    1. Armazenamento e indexação em memória de posições, cenários e garantias.
    2. Reavaliação integral de cenários de estresse multi-ativo com compensação multilateral.
    3. Apuração da perda no pior cenário (Worst-Case Closeout Loss).
    4. Cálculo de adicionais regulatórios (liquidez e concentração).
    5. Confronto com cesta de colaterais elegíveis com haircuts B3.
    6. Disparo determinístico de chamada de margem intradiária.
    """

    def __init__(
        self,
        regulatory_tolerance_pct: Decimal = Decimal("0.05"),
        regulatory_tolerance_min_brl: Decimal = Decimal("5000.00"),
        liquidity_addon_rate: Decimal = Decimal("0.005"),
        concentration_threshold_pct: Decimal = Decimal("0.40"),
        concentration_addon_rate: Decimal = Decimal("0.015"),
    ) -> None:
        self.regulatory_tolerance_pct = regulatory_tolerance_pct
        self.regulatory_tolerance_min_brl = regulatory_tolerance_min_brl
        self.liquidity_addon_rate = liquidity_addon_rate
        self.concentration_threshold_pct = concentration_threshold_pct
        self.concentration_addon_rate = concentration_addon_rate

        self._positions: Dict[str, PositionItem] = {}
        self._scenarios: Dict[str, RiskScenario] = {}
        self._collateral: Dict[str, CollateralItem] = {}
        self._calculation_seq: int = 0

    # --------------------------------------------------------------------------
    # Gestão de Posições
    # --------------------------------------------------------------------------

    def add_position(self, position: PositionItem) -> None:
        """Registra ou atualiza uma posição na carteira."""
        self._positions[position.position_id] = position

    def remove_position(self, position_id: str) -> None:
        """Remove uma posição da carteira."""
        self._positions.pop(position_id, None)

    def clear_positions(self) -> None:
        """Limpa todas as posições registradas."""
        self._positions.clear()

    def get_positions(self) -> List[PositionItem]:
        """Retorna a lista de posições ativas."""
        return list(self._positions.values())

    # --------------------------------------------------------------------------
    # Gestão de Cenários de Risco
    # --------------------------------------------------------------------------

    def add_scenario(self, scenario: RiskScenario) -> None:
        """Registra um cenário de estresse na matriz de risco."""
        self._scenarios[scenario.scenario_id] = scenario

    def remove_scenario(self, scenario_id: str) -> None:
        """Remove um cenário de estresse da matriz."""
        self._scenarios.pop(scenario_id, None)

    def clear_scenarios(self) -> None:
        """Limpa todos os cenários registrados."""
        self._scenarios.clear()

    def get_scenarios(self) -> List[RiskScenario]:
        """Retorna a lista de cenários de estresse da matriz."""
        return list(self._scenarios.values())

    # --------------------------------------------------------------------------
    # Gestão de Garantias e Colateral
    # --------------------------------------------------------------------------

    def add_collateral(self, item: CollateralItem) -> None:
        """Registra ou atualiza um item de garantia depositada."""
        self._collateral[item.collateral_id] = item

    def remove_collateral(self, collateral_id: str) -> None:
        """Remove um item de garantia da custódia."""
        self._collateral.pop(collateral_id, None)

    def clear_collateral(self) -> None:
        """Limpa todas as garantias registradas."""
        self._collateral.clear()

    def get_collateral(self) -> List[CollateralItem]:
        """Retorna a lista de itens de garantia depositados."""
        return list(self._collateral.values())

    # --------------------------------------------------------------------------
    # Avaliação de Cenário Único
    # --------------------------------------------------------------------------

    def evaluate_scenario(
        self,
        scenario: RiskScenario,
        positions: Optional[List[PositionItem]] = None,
    ) -> StressScenarioResult:
        """Avalia o resultado financeiro (PnL) de todas as posições sob um cenário específico.
        
        Aplica o efeito portfólio integral (compensação multilateral) entre ativos
        da carteira para o cenário fornecido.
        """
        eval_positions = positions if positions is not None else list(self._positions.values())

        total_pnl = Decimal("0.00")
        position_pnls: Dict[str, Decimal] = {}
        asset_class_pnls: Dict[str, Decimal] = {}

        for pos in eval_positions:
            pnl = pos.calculate_scenario_pnl(scenario)
            position_pnls[pos.position_id] = pnl
            total_pnl += pnl

            cat_key = pos.asset_category.value
            asset_class_pnls[cat_key] = asset_class_pnls.get(cat_key, Decimal("0.00")) + pnl

        portfolio_loss = max(Decimal("0.00"), -total_pnl)

        return StressScenarioResult(
            scenario_id=scenario.scenario_id,
            scenario_name=scenario.name,
            portfolio_pnl=total_pnl,
            portfolio_loss=portfolio_loss,
            position_pnls=position_pnls,
            asset_class_pnls=asset_class_pnls,
        )

    # --------------------------------------------------------------------------
    # Cálculo Completo de Margem Intradiária CORE
    # --------------------------------------------------------------------------

    def calculate_intraday_margin(
        self,
        account_id: str,
        timestamp: Optional[datetime] = None,
    ) -> IntradayMarginCalculation:
        """Executa a apuração completa de risco de liquidação e chamada de margem.
        
        Passos:
        1. Simulação determinística de todos os cenários da matriz de risco.
        2. Determinação da perda no pior cenário (Worst-Case Closeout Loss).
        3. Cálculo de adicionais de liquidez e concentração setorial.
        4. Avaliação de garantias disponíveis ajustadas por haircuts regulatórios.
        5. Confronto do déficit projetado contra a tolerância regulamentar.
        6. Emissão de chamada de margem intradiária quando aplicável.
        """
        start_time = time.perf_counter()
        calc_dt = timestamp or datetime.now(timezone.utc)
        self._calculation_seq += 1
        calc_id = f"CORE-CALC-{calc_dt.strftime('%Y%m%d')}-{self._calculation_seq:06d}"

        positions = list(self._positions.values())
        scenarios = list(self._scenarios.values())
        collateral_items = list(self._collateral.values())

        # Caso base sem cenários ou posições
        if not scenarios or not positions:
            worst_loss = Decimal("0.00")
            worst_scenario_id = "NO_SCENARIOS" if not scenarios else "NO_POSITIONS"
            scenario_results: List[StressScenarioResult] = []
        else:
            scenario_results = [self.evaluate_scenario(sc, positions) for sc in scenarios]
            # Seleciona o cenário com a maior perda para a carteira
            worst_res = max(scenario_results, key=lambda res: res.portfolio_loss)
            worst_loss = worst_res.portfolio_loss
            worst_scenario_id = worst_res.scenario_id

        # Cálculo de Add-ons de Liquidez e Concentração
        total_gross_exposure = Decimal("0.00")
        exposure_by_underlying: Dict[str, Decimal] = {}

        for p in positions:
            exp = p.gross_market_value
            total_gross_exposure += exp
            exposure_by_underlying[p.underlying_symbol] = exposure_by_underlying.get(p.underlying_symbol, Decimal("0.00")) + exp

        liquidity_addon = _round_currency(total_gross_exposure * self.liquidity_addon_rate)

        # Concentração: se algum ativo objeto exceder o limiar de concentração
        concentration_addon = Decimal("0.00")
        if total_gross_exposure > Decimal("0.00"):
            for und, und_exp in exposure_by_underlying.items():
                ratio = und_exp / total_gross_exposure
                if ratio > self.concentration_threshold_pct:
                    excess_exp = und_exp - (total_gross_exposure * self.concentration_threshold_pct)
                    concentration_addon += _round_currency(excess_exp * self.concentration_addon_rate)

        total_margin_required = worst_loss + liquidity_addon + concentration_addon

        # Avaliação da Cesta de Garantias
        total_collateral_deposited = Decimal("0.00")
        total_collateral_available = Decimal("0.00")
        collateral_breakdown: List[Dict[str, Any]] = []

        for item in collateral_items:
            total_collateral_deposited += item.nominal_value
            total_collateral_available += item.effective_value
            collateral_breakdown.append(item.to_dict())

        # Apuração do Déficit de Risco
        risk_deficit = max(Decimal("0.00"), total_margin_required - total_collateral_available)

        # Tolerância regulamentar intradiária
        pct_tolerance = total_margin_required * self.regulatory_tolerance_pct
        tolerance = _round_currency(max(self.regulatory_tolerance_min_brl, pct_tolerance))

        # Gatilho de Chamada de Margem Adicional Intradiária
        if risk_deficit > tolerance:
            margin_call_triggered = True
            margin_call_amount = risk_deficit

            # Limite crítico: déficit superior a 50% do colateral disponível ou valor elevado
            critical_ceiling = total_collateral_available * Decimal("0.50")
            if total_collateral_available == Decimal("0.00") or risk_deficit > critical_ceiling:
                status = MarginStatus.CRITICAL
            else:
                status = MarginStatus.MARGIN_CALL_REQUIRED
        elif risk_deficit > Decimal("0.00"):
            margin_call_triggered = False
            margin_call_amount = Decimal("0.00")
            status = MarginStatus.WARNING
        else:
            margin_call_triggered = False
            margin_call_amount = Decimal("0.00")
            status = MarginStatus.NORMAL

        elapsed_us = (time.perf_counter() - start_time) * 1_000_000.0

        return IntradayMarginCalculation(
            calculation_id=calc_id,
            account_id=account_id,
            timestamp=calc_dt,
            positions_evaluated_count=len(positions),
            scenarios_evaluated_count=len(scenarios),
            worst_case_scenario_id=worst_scenario_id,
            worst_case_loss=worst_loss,
            liquidity_addon=liquidity_addon,
            concentration_addon=concentration_addon,
            total_margin_required=total_margin_required,
            total_collateral_deposited=total_collateral_deposited,
            total_collateral_available=total_collateral_available,
            risk_deficit=risk_deficit,
            regulatory_tolerance=tolerance,
            margin_call_triggered=margin_call_triggered,
            margin_call_amount=margin_call_amount,
            status=status,
            calculation_latency_us=elapsed_us,
            collateral_breakdown=collateral_breakdown,
            scenario_results=scenario_results,
        )

    # --------------------------------------------------------------------------
    # Carregamento e Serialização JSON
    # --------------------------------------------------------------------------

    def load_from_json(self, path_or_dict: Union[str, Path, dict]) -> None:
        """Carrega posições, cenários de estresse e colaterais a partir de JSON."""
        if isinstance(path_or_dict, (str, Path)):
            with open(path_or_dict, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = path_or_dict

        # Parâmetros regulatórios (se definidos no JSON)
        params = data.get("regulatory_parameters", {})
        if "regulatory_tolerance_pct" in params:
            self.regulatory_tolerance_pct = Decimal(str(params["regulatory_tolerance_pct"]))
        if "regulatory_tolerance_min_brl" in params:
            self.regulatory_tolerance_min_brl = Decimal(str(params["regulatory_tolerance_min_brl"]))
        if "liquidity_addon_rate" in params:
            self.liquidity_addon_rate = Decimal(str(params["liquidity_addon_rate"]))
        if "concentration_threshold_pct" in params:
            self.concentration_threshold_pct = Decimal(str(params["concentration_threshold_pct"]))
        if "concentration_addon_rate" in params:
            self.concentration_addon_rate = Decimal(str(params["concentration_addon_rate"]))

        # Carregar Cenários de Estresse
        self.clear_scenarios()
        for sc_data in data.get("stress_scenarios", []):
            self.add_scenario(RiskScenario.from_dict(sc_data))

        # Carregar Posições da Carteira
        self.clear_positions()
        for pos_data in data.get("positions", []):
            self.add_position(PositionItem.from_dict(pos_data))

        # Carregar Cesta de Garantias
        self.clear_collateral()
        for col_data in data.get("collateral_items", []):
            self.add_collateral(CollateralItem.from_dict(col_data))

    # --------------------------------------------------------------------------
    # Benchmark de Latência Ultrabaixa
    # --------------------------------------------------------------------------

    def benchmark_latency(self, iterations: int = 1000) -> Dict[str, Any]:
        """Executa benchmark de latência em microssegundos do cálculo de margem CORE."""
        latencies_us: List[float] = []
        account_id = "BENCH_ACCT_001"

        for _ in range(iterations):
            res = self.calculate_intraday_margin(account_id)
            latencies_us.append(res.calculation_latency_us)

        latencies_us.sort()
        mean_us = sum(latencies_us) / len(latencies_us)
        p50_us = latencies_us[int(len(latencies_us) * 0.50)]
        p95_us = latencies_us[int(len(latencies_us) * 0.95)]
        p99_us = latencies_us[int(len(latencies_us) * 0.99)]
        max_us = max(latencies_us)

        return {
            "iterations": iterations,
            "mean_micros": round(mean_us, 2),
            "p50_micros": round(p50_us, 2),
            "p95_micros": round(p95_us, 2),
            "p99_micros": round(p99_us, 2),
            "max_micros": round(max_us, 2),
            "sub_millisecond": p99_us < 1000.0,
        }
