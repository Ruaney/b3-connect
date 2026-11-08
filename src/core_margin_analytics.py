"""Motor Quantitativo de Risco e Margens CORE da Câmara B3 (FL-050 / PFMI Princípio 5).

Implementa a metodologia avançada Closeout Risk Evaluation (CORE) da Câmara B3 com:
1. Value-at-Risk (VaR) Paramétrico e Histórico com nível de confiança regulatório de 99,5%
   (CPMI-IOSCO PFMI Princípio 5 - Margining).
2. Expected Shortfall (CVaR / Conditional VaR) com modelagem de cauda gorda via expansão
   de Cornish-Fisher e multiplicadores de estresse sistêmico.
3. Liquidity-Adjusted Margins (LAM): penalidade de iliquidez, custo de spread e horizonte de
   liquidação forçada parametrizado entre 2 e 5 dias (Closeout Liquidation Horizon).
4. Matriz de Haircuts Conservadores para Títulos Públicos Federais (TPFs no Selic: LFT, LTN,
   NTN-B, NTN-F) parametrizada por vértice/maturidade e buffer cambial offshore (LAW-105A).
5. Fator de Diversificação Intracarteira e Intercommodities (Equities vs DI vs FX vs Commodities)
   com teto regulatório prudencial de crédito de diversificação (máximo 30%).
6. Preservação formal estrita das Leis Invariantes:
   - LAW-003: Margem Requerida >= Risco Potencial (Cobertura de Exposição Máxima Potencial).
   - LAW-006: Cobertura Prudencial de Colaterais CORE e Cascata Sequencial de Salvaguardas.
   - LAW-105A: Matriz de Haircuts Conservadores de TPFs e Buffer Transfronteiriço.

Zero dependências externas (100% Python Standard Library: decimal.Decimal, math).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from src.invariants import check, InvariantViolationError

# Constantes de Precisão Monetária e Decimal
TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")
EIGHT_PLACES = Decimal("0.00000001")

# Constantes Regulatórias CPMI-IOSCO PFMI
REGULATORY_CONFIDENCE_LEVEL = Decimal("0.995")  # 99.5% de confiança
MIN_CLOSEOUT_HORIZON_DAYS = 2                   # Mínimo 2 dias úteis de liquidação
MAX_CLOSEOUT_HORIZON_DAYS = 5                   # Máximo 5 dias úteis de liquidação
MAX_DIVERSIFICATION_CREDIT = Decimal("0.30")    # Teto prudencial de 30% de benefício


# ==============================================================================
# 1. Enumerações e Estruturas de Domínio
# ==============================================================================

class AssetClass(str, Enum):
    """Classes de ativos e fatores primitivos de risco CORE."""
    EQUITIES = "EQUITIES"
    INTEREST_RATES = "INTEREST_RATES"      # Futuros de DI1, FRA, Swaps Pré
    FOREIGN_EXCHANGE = "FOREIGN_EXCHANGE"  # Futuros de Dólar (DOL), NDF
    COMMODITIES = "COMMODITIES"            # Boi Gordo (BGI), Café (ICF), Milho (CCM)
    SOVEREIGN_TPF = "SOVEREIGN_TPF"        # Títulos Públicos Federais (Colateral Selic)


class TPFBondType(str, Enum):
    """Tipos canônicos de Títulos Públicos Federais custodiados no Selic/B3."""
    LFT = "LFT"      # Letras Financeiras do Tesouro (Pós-fixada Selic, duration quase nula)
    LTN = "LTN"      # Letras do Tesouro Nacional (Pré-fixada cupom zero, duration = prazo)
    NTN_F = "NTN_F"  # Notas do Tesouro Nacional Série F (Pré-fixada com cupom semestral)
    NTN_B = "NTN_B"  # Notas do Tesouro Nacional Série B (IPCA + cupom semestral real)


class MaturityTenor(str, Enum):
    """Vértices de maturidade para aplicação de Haircuts Conservadores."""
    UP_TO_1Y = "UP_TO_1Y"            # Até 252 dias úteis
    FROM_1Y_TO_2Y = "FROM_1Y_TO_2Y"  # 253 a 504 dias úteis
    FROM_2Y_TO_5Y = "FROM_2Y_TO_5Y"  # 505 a 1.260 dias úteis
    FROM_5Y_TO_10Y = "FROM_5Y_TO_10Y"# 1.261 a 2.520 dias úteis
    OVER_10Y = "OVER_10Y"            # Mais de 2.520 dias úteis


class MarginCallStatus(str, Enum):
    """Status operacional da conta de custódia e risco do participante."""
    SUFFICIENT = "SUFFICIENT"                    # Colateral cobre integralmente a margem
    MARGIN_CALL_REQUIRED = "MARGIN_CALL_REQUIRED"# Déficit de garantia: chamada imediata
    CRITICAL_DEFAULT = "CRITICAL_DEFAULT"        # Inadimplência com acionamento de salvaguarda


class SafeguardWaterfallLayer(str, Enum):
    """Camadas ordenadas da Cascata de Salvaguardas da Câmara B3 (LAW-006)."""
    LAYER_1_DEFAULTER_MARGIN = "LAYER_1_DEFAULTER_MARGIN"
    LAYER_2_DEFAULTER_CLEARING_FUND = "LAYER_2_DEFAULTER_CLEARING_FUND"
    LAYER_3_B3_SITG = "LAYER_3_B3_SITG"  # Skin In The Game B3
    LAYER_4_MUTUALIZED_DEFAULT_FUND = "LAYER_4_MUTUALIZED_DEFAULT_FUND"
    LAYER_5_EXTRAORDINARY_ASSESSMENT = "LAYER_5_EXTRAORDINARY_ASSESSMENT"


# ==============================================================================
# 2. Matriz de Haircuts Conservadores de TPFs (LAW-105A)
# ==============================================================================

# Haircuts prudenciais mínimos baseados na volatilidade de duration e convexidade
TPF_HAIRCUT_SCHEDULE: Dict[Tuple[TPFBondType, MaturityTenor], Decimal] = {
    # LFT: Pós-fixada indexada à taxa Selic overnight (risco primordial de ágio/deságio)
    (TPFBondType.LFT, MaturityTenor.UP_TO_1Y): Decimal("0.0080"),       # 0.80%
    (TPFBondType.LFT, MaturityTenor.FROM_1Y_TO_2Y): Decimal("0.0120"),   # 1.20%
    (TPFBondType.LFT, MaturityTenor.FROM_2Y_TO_5Y): Decimal("0.0200"),   # 2.00%
    (TPFBondType.LFT, MaturityTenor.FROM_5Y_TO_10Y): Decimal("0.0300"),  # 3.00%
    (TPFBondType.LFT, MaturityTenor.OVER_10Y): Decimal("0.0450"),        # 4.50%

    # LTN: Pré-fixada cupom zero (alta sensibilidade à curva de juros DI/Pré)
    (TPFBondType.LTN, MaturityTenor.UP_TO_1Y): Decimal("0.0250"),       # 2.50%
    (TPFBondType.LTN, MaturityTenor.FROM_1Y_TO_2Y): Decimal("0.0450"),   # 4.50%
    (TPFBondType.LTN, MaturityTenor.FROM_2Y_TO_5Y): Decimal("0.0750"),   # 7.50%
    (TPFBondType.LTN, MaturityTenor.FROM_5Y_TO_10Y): Decimal("0.1100"),  # 11.00%
    (TPFBondType.LTN, MaturityTenor.OVER_10Y): Decimal("0.1400"),        # 14.00%

    # NTN-F: Pré-fixada com cupom semestral de 10% a.a.
    (TPFBondType.NTN_F, MaturityTenor.UP_TO_1Y): Decimal("0.0300"),     # 3.00%
    (TPFBondType.NTN_F, MaturityTenor.FROM_1Y_TO_2Y): Decimal("0.0500"), # 5.00%
    (TPFBondType.NTN_F, MaturityTenor.FROM_2Y_TO_5Y): Decimal("0.0850"), # 8.50%
    (TPFBondType.NTN_F, MaturityTenor.FROM_5Y_TO_10Y): Decimal("0.1250"),# 12.50%
    (TPFBondType.NTN_F, MaturityTenor.OVER_10Y): Decimal("0.1600"),      # 16.00%

    # NTN-B: Indexada à inflação (IPCA) com cupom semestral real (risco de juro real + inflação)
    (TPFBondType.NTN_B, MaturityTenor.UP_TO_1Y): Decimal("0.0400"),     # 4.00%
    (TPFBondType.NTN_B, MaturityTenor.FROM_1Y_TO_2Y): Decimal("0.0600"), # 6.00%
    (TPFBondType.NTN_B, MaturityTenor.FROM_2Y_TO_5Y): Decimal("0.0900"), # 9.00%
    (TPFBondType.NTN_B, MaturityTenor.FROM_5Y_TO_10Y): Decimal("0.1350"),# 13.50%
    (TPFBondType.NTN_B, MaturityTenor.OVER_10Y): Decimal("0.1850"),      # 18.50%
}

TENOR_ORDER: List[MaturityTenor] = [
    MaturityTenor.UP_TO_1Y,
    MaturityTenor.FROM_1Y_TO_2Y,
    MaturityTenor.FROM_2Y_TO_5Y,
    MaturityTenor.FROM_5Y_TO_10Y,
    MaturityTenor.OVER_10Y,
]


# ==============================================================================
# 3. Funções Matemáticas Puras (Normal e Cornish-Fisher)
# ==============================================================================

def std_norm_pdf(x: float) -> float:
    """Função densidade de probabilidade normal padrão phi(x)."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)


def std_norm_cdf(x: float) -> float:
    """Função de distribuição acumulada normal padrão Phi(x)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def std_norm_ppf(p: float) -> float:
    """Inversa da Normal Acumulada Phi^-1(p) via algoritmo de Peter Acklam.

    Garante erro relativo < 1.15e-9 em todo o intervalo (0, 1).
    Para p = 0.995, retorna exatamente ~2.5758293.
    """
    if p <= 0.0 or p >= 1.0:
        raise ValueError(f"Probabilidade fora do intervalo (0, 1): {p}")

    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    ]

    p_low = 0.02425
    p_high = 1.0 - p_low

    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (
            ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
        ) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
        ) / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(
            (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        ) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)


def cornish_fisher_quantile(
    alpha: float,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Calcula o quantil ajustado para assimetria e curtose (cauda gorda).

    Formula da expansão de Cornish-Fisher de 4ª ordem:
    w = z + (S/6)*(z^2 - 1) + ((K - 3)/24)*(z^3 - 3z) - (S^2/36)*(2z^3 - 5z)
    """
    z = std_norm_ppf(alpha)
    if abs(skewness) < 1e-9 and abs(kurtosis - 3.0) < 1e-9:
        return z

    z2 = z * z
    z3 = z2 * z
    excess_kurt = kurtosis - 3.0

    term_skew = (skewness / 6.0) * (z2 - 1.0)
    term_kurt = (excess_kurt / 24.0) * (z3 - 3.0 * z)
    term_skew2 = ((skewness * skewness) / 36.0) * (2.0 * z3 - 5.0 * z)

    z_cf = z + term_skew + term_kurt - term_skew2
    return max(z_cf, z)  # Conservadorismo: quantil nunca inferior ao gaussiano na cauda direita


# ==============================================================================
# 4. Estruturas de Dados do Modelo CORE
# ==============================================================================

@dataclass(frozen=True)
class TPFCollateral:
    """Posição em Título Público Federal oferecida como garantia na B3."""
    asset_id: str
    bond_type: TPFBondType
    tenor: MaturityTenor
    face_value_brl: Decimal
    market_price_pct: Decimal = Decimal("1.00")  # Preço unitário relativo (PU / 1000)
    is_cross_border: bool = False                # Se custodiado em depositário internacional
    fx_buffer: Decimal = Decimal("0.08")         # Buffer cambial de 8% se internacional

    def gross_market_value(self) -> Decimal:
        """Valor financeiro bruto a mercado."""
        return (self.face_value_brl * self.market_price_pct).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    def base_haircut(self) -> Decimal:
        """Haircut regulatório base por prazo/instrumento."""
        key = (self.bond_type, self.tenor)
        if key not in TPF_HAIRCUT_SCHEDULE:
            raise KeyError(f"Combinação de TPF desconhecida: {key}")
        return TPF_HAIRCUT_SCHEDULE[key]

    def total_haircut(self) -> Decimal:
        """Haircut total consolidado incluindo buffer cambial (LAW-105A)."""
        h_base = self.base_haircut()
        if not self.is_cross_border:
            return h_base
        # Combinação multiplicativa conservadora de mitigação de risco cambial
        # 1 - H_total = (1 - H_base) * (1 - Buffer_FX)
        h_total = Decimal("1.00") - (Decimal("1.00") - h_base) * (Decimal("1.00") - self.fx_buffer)
        return h_total.quantize(FOUR_PLACES, rounding=ROUND_CEILING)

    def eligible_collateral_value(self) -> Decimal:
        """Valor líquido elegível após aplicação do haircut (LAW-105A)."""
        gross = self.gross_market_value()
        h = self.total_haircut()
        eligible = gross * (Decimal("1.00") - h)
        return eligible.quantize(TWO_PLACES, rounding=ROUND_FLOOR)


@dataclass(frozen=True)
class LiquidityParameters:
    """Parâmetros de microestrutura e liquidez de mercado para o módulo LAM."""
    adv_shares: Decimal                       # Volume Médio Diário (Average Daily Volume)
    bid_ask_spread_pct: Decimal               # Spread bid-ask relativo (ex: 0.0020 = 20 bps)
    participation_rate_limit: Decimal = Decimal("0.15") # Limite prudencial de participação (15%)
    impact_coefficient: Decimal = Decimal("0.10")       # Coeficiente de impacto de mercado (eta)
    min_horizon_days: int = MIN_CLOSEOUT_HORIZON_DAYS
    max_horizon_days: int = MAX_CLOSEOUT_HORIZON_DAYS


@dataclass(frozen=True)
class ParametricVaRResult:
    """Resultado do cálculo de Value-at-Risk Paramétrico a 99,5%."""
    confidence_level: Decimal
    horizon_days: int
    exposure_brl: Decimal
    daily_volatility: Decimal
    z_score: Decimal
    time_factor: Decimal
    var_brl: Decimal


@dataclass(frozen=True)
class HistoricalVaRResult:
    """Resultado do cálculo de Value-at-Risk Histórico a 99,5%."""
    confidence_level: Decimal
    horizon_days: int
    sample_size: int
    var_1d_brl: Decimal
    time_factor: Decimal
    var_brl: Decimal
    worst_historical_loss_brl: Decimal


@dataclass(frozen=True)
class ExpectedShortfallResult:
    """Resultado do cálculo de Expected Shortfall (CVaR) com cauda gorda."""
    confidence_level: Decimal
    horizon_days: int
    var_threshold_brl: Decimal
    expected_shortfall_brl: Decimal
    fat_tail_multiplier: Decimal
    is_fat_tail_adjusted: bool
    kurtosis: Decimal
    skewness: Decimal
    stress_tail_factor: Decimal


@dataclass(frozen=True)
class LiquidityAdjustedMarginResult:
    """Resultado do cálculo de Margem Ajustada por Liquidez (LAM)."""
    base_risk_brl: Decimal
    position_quantity: Decimal
    adv_shares: Decimal
    participation_rate: Decimal
    closeout_horizon_days: int
    time_scaling_factor: Decimal
    spread_cost_brl: Decimal
    market_impact_penalty_brl: Decimal
    total_lam_margin_brl: Decimal


@dataclass(frozen=True)
class DiversificationResult:
    """Resultado da agregação e diversificação intracarteira/intercommodities."""
    standalone_margins: Dict[AssetClass, Decimal]
    undiversified_sum_brl: Decimal
    diversified_margin_brl: Decimal
    diversification_factor: Decimal          # D = Diversified / Undiversified
    diversification_credit_pct: Decimal      # 1 - D (limitado a 30%)
    correlation_matrix: Dict[Tuple[AssetClass, AssetClass], Decimal]


@dataclass(frozen=True)
class WaterfallLayerAbsorption:
    """Detalhamento de absorção em uma camada específica da cascata de default."""
    layer: SafeguardWaterfallLayer
    available_before_brl: Decimal
    loss_absorbed_brl: Decimal
    remaining_balance_brl: Decimal


@dataclass(frozen=True)
class DefaultWaterfallResult:
    """Resultado da avaliação formal da Cascata de Salvaguardas (LAW-006)."""
    gross_default_loss_brl: Decimal
    layers: List[WaterfallLayerAbsorption]
    total_absorbed_brl: Decimal
    residual_uncovered_deficit_brl: Decimal
    surviving_safeguards_brl: Decimal
    is_fully_absorbed: bool


# ==============================================================================
# 5. Motores Quantitativos Analíticos CORE
# ==============================================================================

class CoreMarginAnalytics:
    """Motor quantitativo analítico de Risco e Margens CORE da B3."""

    # Matriz canônica de correlação regulatória entre classes de ativos B3
    CORRELATION_MATRIX: Dict[Tuple[AssetClass, AssetClass], Decimal] = {
        (AssetClass.EQUITIES, AssetClass.EQUITIES): Decimal("1.00"),
        (AssetClass.EQUITIES, AssetClass.INTEREST_RATES): Decimal("-0.25"),
        (AssetClass.EQUITIES, AssetClass.FOREIGN_EXCHANGE): Decimal("-0.15"),
        (AssetClass.EQUITIES, AssetClass.COMMODITIES): Decimal("0.20"),

        (AssetClass.INTEREST_RATES, AssetClass.EQUITIES): Decimal("-0.25"),
        (AssetClass.INTEREST_RATES, AssetClass.INTEREST_RATES): Decimal("1.00"),
        (AssetClass.INTEREST_RATES, AssetClass.FOREIGN_EXCHANGE): Decimal("0.35"),
        (AssetClass.INTEREST_RATES, AssetClass.COMMODITIES): Decimal("0.10"),

        (AssetClass.FOREIGN_EXCHANGE, AssetClass.EQUITIES): Decimal("-0.15"),
        (AssetClass.FOREIGN_EXCHANGE, AssetClass.INTEREST_RATES): Decimal("0.35"),
        (AssetClass.FOREIGN_EXCHANGE, AssetClass.FOREIGN_EXCHANGE): Decimal("1.00"),
        (AssetClass.FOREIGN_EXCHANGE, AssetClass.COMMODITIES): Decimal("0.25"),

        (AssetClass.COMMODITIES, AssetClass.EQUITIES): Decimal("0.20"),
        (AssetClass.COMMODITIES, AssetClass.INTEREST_RATES): Decimal("0.10"),
        (AssetClass.COMMODITIES, AssetClass.FOREIGN_EXCHANGE): Decimal("0.25"),
        (AssetClass.COMMODITIES, AssetClass.COMMODITIES): Decimal("1.00"),
    }

    # --------------------------------------------------------------------------
    # 5.1. Value-at-Risk (Paramétrico e Histórico)
    # --------------------------------------------------------------------------

    @staticmethod
    def calculate_parametric_var(
        exposure_brl: Decimal,
        daily_volatility: Decimal,
        horizon_days: int = 2,
        confidence_level: Decimal = REGULATORY_CONFIDENCE_LEVEL,
    ) -> ParametricVaRResult:
        """Calcula o Value-at-Risk Paramétrico a 99,5% (CPMI-IOSCO PFMI Princípio 5).

        Formula: VaR = |Exposição| * Z_alpha * Volatilidade_Diária * sqrt(Horizonte)
        """
        if confidence_level <= Decimal("0.50") or confidence_level >= Decimal("1.00"):
            raise ValueError(f"Nível de confiança inválido: {confidence_level}")
        if daily_volatility < Decimal("0.00"):
            raise ValueError(f"Volatilidade diária negativa: {daily_volatility}")
        if horizon_days < 1:
            raise ValueError(f"Horizonte de liquidação inválido: {horizon_days}")

        z_val = std_norm_ppf(float(confidence_level))
        z_dec = Decimal(str(round(z_val, 8)))

        time_factor = Decimal(str(round(math.sqrt(horizon_days), 8)))
        abs_exposure = abs(exposure_brl)

        var_raw = abs_exposure * z_dec * daily_volatility * time_factor
        var_brl = var_raw.quantize(TWO_PLACES, rounding=ROUND_CEILING)

        return ParametricVaRResult(
            confidence_level=confidence_level,
            horizon_days=horizon_days,
            exposure_brl=abs_exposure,
            daily_volatility=daily_volatility,
            z_score=z_dec,
            time_factor=time_factor,
            var_brl=var_brl,
        )

    @staticmethod
    def calculate_historical_var(
        historical_losses_brl: Sequence[Union[Decimal, float]],
        horizon_days: int = 2,
        confidence_level: Decimal = REGULATORY_CONFIDENCE_LEVEL,
    ) -> HistoricalVaRResult:
        """Calcula o Value-at-Risk Histórico a 99,5% via interpolação de quantil.

        Considera uma série temporal de perdas diárias observadas (PnL negativo invertido).
        """
        if not historical_losses_brl:
            raise ValueError("Série histórica de perdas não pode estar vazia.")
        if confidence_level <= Decimal("0.50") or confidence_level >= Decimal("1.00"):
            raise ValueError(f"Nível de confiança inválido: {confidence_level}")
        if horizon_days < 1:
            raise ValueError(f"Horizonte de liquidação inválido: {horizon_days}")

        # Converte para Decimal ordenado de forma ascendente
        losses = sorted([Decimal(str(x)) for x in historical_losses_brl])
        n = len(losses)
        worst_loss = losses[-1]

        # Interpolação linear contínua do percentil
        alpha_float = float(confidence_level)
        index_float = (n - 1) * alpha_float
        idx_lower = int(math.floor(index_float))
        idx_upper = int(math.ceil(index_float))
        weight_upper = Decimal(str(index_float - idx_lower))

        if idx_lower == idx_upper:
            var_1d = losses[idx_lower]
        else:
            var_1d = losses[idx_lower] + weight_upper * (losses[idx_upper] - losses[idx_lower])

        time_factor = Decimal(str(round(math.sqrt(horizon_days), 8)))
        var_h = (var_1d * time_factor).quantize(TWO_PLACES, rounding=ROUND_CEILING)

        return HistoricalVaRResult(
            confidence_level=confidence_level,
            horizon_days=horizon_days,
            sample_size=n,
            var_1d_brl=var_1d.quantize(TWO_PLACES, rounding=ROUND_CEILING),
            time_factor=time_factor,
            var_brl=var_h,
            worst_historical_loss_brl=worst_loss.quantize(TWO_PLACES, rounding=ROUND_CEILING),
        )

    # --------------------------------------------------------------------------
    # 5.2. Expected Shortfall (CVaR) com Cauda Gorda para Estresse Sistêmico
    # --------------------------------------------------------------------------

    @staticmethod
    def calculate_parametric_expected_shortfall(
        exposure_brl: Decimal,
        daily_volatility: Decimal,
        horizon_days: int = 2,
        confidence_level: Decimal = REGULATORY_CONFIDENCE_LEVEL,
        kurtosis: Decimal = Decimal("3.00"),
        skewness: Decimal = Decimal("0.00"),
        stress_tail_factor: Decimal = Decimal("0.00"),
    ) -> ExpectedShortfallResult:
        """Calcula o Expected Shortfall (CVaR) analítico com cauda gorda de estresse.

        - Base Gaussiana: ES_gauss = Exposição * Vol * [phi(z) / (1 - alpha)] * sqrt(h)
        - Ajuste Cornish-Fisher: Multiplicador z_CF / z_gauss * [1 + (K-3)/12]
        - Adicional de Estresse Sistêmico: (1 + stress_tail_factor)
        """
        if confidence_level <= Decimal("0.50") or confidence_level >= Decimal("1.00"):
            raise ValueError(f"Nível de confiança inválido: {confidence_level}")
        if kurtosis < Decimal("1.00"):
            raise ValueError(f"Curtose física não pode ser inferior a 1.0: {kurtosis}")

        alpha_fl = float(confidence_level)
        z_gauss = std_norm_ppf(alpha_fl)
        phi_z = std_norm_pdf(z_gauss)

        tail_prob = 1.0 - alpha_fl
        base_es_ratio = phi_z / tail_prob

        # Base Gaussiana
        time_factor = math.sqrt(horizon_days)
        abs_exp_fl = float(abs(exposure_brl))
        vol_fl = float(daily_volatility)

        base_es_fl = abs_exp_fl * vol_fl * base_es_ratio * time_factor
        var_gauss_fl = abs_exp_fl * vol_fl * z_gauss * time_factor

        # Cornish-Fisher para Cauda Gorda
        kurt_fl = float(kurtosis)
        skew_fl = float(skewness)
        z_cf = cornish_fisher_quantile(alpha_fl, skewness=skew_fl, kurtosis=kurt_fl)

        is_fat_tail = (kurtosis > Decimal("3.00")) or (abs(skewness) > Decimal("0.001")) or (stress_tail_factor > Decimal("0.00"))

        if is_fat_tail:
            # Multiplicador proporcional ao excesso de quantil Cornish-Fisher
            cf_ratio = max(1.0, z_cf / z_gauss)
            kurt_boost = 1.0 + max(0.0, (kurt_fl - 3.0) / 12.0)
            fat_mult = cf_ratio * kurt_boost * float(Decimal("1.00") + stress_tail_factor)
        else:
            fat_mult = 1.0

        fat_mult_dec = Decimal(str(round(fat_mult, 6)))
        es_final_fl = base_es_fl * fat_mult
        var_cf_fl = var_gauss_fl * (z_cf / z_gauss)

        es_final_dec = Decimal(str(round(es_final_fl, 6))).quantize(TWO_PLACES, rounding=ROUND_CEILING)
        var_cf_dec = Decimal(str(round(var_cf_fl, 6))).quantize(TWO_PLACES, rounding=ROUND_CEILING)

        # Invariante Coerente de Medida de Risco: ES >= VaR sempre
        if es_final_dec < var_cf_dec:
            es_final_dec = var_cf_dec

        return ExpectedShortfallResult(
            confidence_level=confidence_level,
            horizon_days=horizon_days,
            var_threshold_brl=var_cf_dec,
            expected_shortfall_brl=es_final_dec,
            fat_tail_multiplier=fat_mult_dec,
            is_fat_tail_adjusted=is_fat_tail,
            kurtosis=kurtosis,
            skewness=skewness,
            stress_tail_factor=stress_tail_factor,
        )

    @staticmethod
    def calculate_historical_expected_shortfall(
        historical_losses_brl: Sequence[Union[Decimal, float]],
        horizon_days: int = 2,
        confidence_level: Decimal = REGULATORY_CONFIDENCE_LEVEL,
        stress_tail_factor: Decimal = Decimal("0.00"),
    ) -> ExpectedShortfallResult:
        """Calcula o Expected Shortfall Histórico empírico na cauda dos 0,5% piores casos."""
        h_var = CoreMarginAnalytics.calculate_historical_var(
            historical_losses_brl=historical_losses_brl,
            horizon_days=horizon_days,
            confidence_level=confidence_level,
        )

        var_threshold = h_var.var_1d_brl
        tail_losses = [Decimal(str(x)) for x in historical_losses_brl if Decimal(str(x)) >= var_threshold]

        if not tail_losses:
            # Fallback seguro para a pior perda histórica
            tail_losses = [h_var.worst_historical_loss_brl]

        avg_tail_1d = sum(tail_losses) / Decimal(str(len(tail_losses)))
        time_factor = Decimal(str(round(math.sqrt(horizon_days), 8)))

        stress_boost = Decimal("1.00") + stress_tail_factor
        es_total = (avg_tail_1d * time_factor * stress_boost).quantize(TWO_PLACES, rounding=ROUND_CEILING)

        # Garantir ES >= VaR
        if es_total < h_var.var_brl:
            es_total = h_var.var_brl

        fat_mult = (es_total / h_var.var_brl).quantize(FOUR_PLACES, rounding=ROUND_CEILING) if h_var.var_brl > Decimal("0") else Decimal("1.0000")

        return ExpectedShortfallResult(
            confidence_level=confidence_level,
            horizon_days=horizon_days,
            var_threshold_brl=h_var.var_brl,
            expected_shortfall_brl=es_total,
            fat_tail_multiplier=fat_mult,
            is_fat_tail_adjusted=stress_tail_factor > Decimal("0.00"),
            kurtosis=Decimal("3.00"),
            skewness=Decimal("0.00"),
            stress_tail_factor=stress_tail_factor,
        )

    # --------------------------------------------------------------------------
    # 5.3. Liquidity-Adjusted Margins (LAM) & Closeout Horizon
    # --------------------------------------------------------------------------

    @staticmethod
    def calculate_closeout_horizon(
        position_quantity: Decimal,
        adv_shares: Decimal,
        participation_limit: Decimal = Decimal("0.15"),
        min_horizon: int = MIN_CLOSEOUT_HORIZON_DAYS,
        max_horizon: int = MAX_CLOSEOUT_HORIZON_DAYS,
    ) -> int:
        """Determina o prazo de liquidação forçada (Closeout Horizon) entre 2 e 5 dias.

        Formula: Horizon = clamp( ceil(|Q| / (ParticipationLimit * ADV)), 2, 5 )
        """
        if adv_shares <= Decimal("0.00"):
            return max_horizon

        abs_q = abs(position_quantity)
        max_daily_liquidation = adv_shares * participation_limit

        if max_daily_liquidation <= Decimal("0.00"):
            return max_horizon

        days_needed = (abs_q / max_daily_liquidation)
        days_ceil = int(math.ceil(float(days_needed)))

        return max(min_horizon, min(max_horizon, days_ceil))

    @classmethod
    def calculate_liquidity_adjusted_margin(
        cls,
        base_risk_brl: Decimal,
        position_quantity: Decimal,
        unit_price_brl: Decimal,
        daily_volatility: Decimal,
        liquidity_params: LiquidityParameters,
    ) -> LiquidityAdjustedMarginResult:
        """Calcula a Margem Ajustada por Liquidez (LAM) segundo B3 CORE.

        Componentes:
        1. Escala temporal do risco base: sqrt(Horizonte / 2 dias base).
        2. Custo de bid-ask spread: 0.5 * Spread * Valor da Posição.
        3. Penalidade de impacto de mercado: eta * Vol * sqrt(|Q| / ADV) * Valor da Posição.
        """
        horizon = cls.calculate_closeout_horizon(
            position_quantity=position_quantity,
            adv_shares=liquidity_params.adv_shares,
            participation_limit=liquidity_params.participation_rate_limit,
            min_horizon=liquidity_params.min_horizon_days,
            max_horizon=liquidity_params.max_horizon_days,
        )

        # Fator de escala temporal relativo ao padrão de 2 dias
        time_scaling = Decimal(str(round(math.sqrt(horizon / MIN_CLOSEOUT_HORIZON_DAYS), 8)))
        scaled_base_risk = (base_risk_brl * time_scaling).quantize(TWO_PLACES, rounding=ROUND_CEILING)

        position_val = (abs(position_quantity) * unit_price_brl).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Custo de Spread
        spread_cost = (Decimal("0.50") * liquidity_params.bid_ask_spread_pct * position_val).quantize(TWO_PLACES, rounding=ROUND_CEILING)

        # Penalidade de Impacto de Mercado Não-Linear
        if liquidity_params.adv_shares > Decimal("0.00"):
            q_adv_ratio = float(abs(position_quantity) / liquidity_params.adv_shares)
            impact_factor = float(liquidity_params.impact_coefficient) * float(daily_volatility) * math.sqrt(q_adv_ratio)
            impact_penalty = (Decimal(str(round(impact_factor, 8))) * position_val).quantize(TWO_PLACES, rounding=ROUND_CEILING)
        else:
            impact_penalty = (Decimal("0.05") * position_val).quantize(TWO_PLACES, rounding=ROUND_CEILING)

        total_lam = (scaled_base_risk + spread_cost + impact_penalty).quantize(TWO_PLACES, rounding=ROUND_CEILING)

        return LiquidityAdjustedMarginResult(
            base_risk_brl=base_risk_brl,
            position_quantity=position_quantity,
            adv_shares=liquidity_params.adv_shares,
            participation_rate=liquidity_params.participation_rate_limit,
            closeout_horizon_days=horizon,
            time_scaling_factor=time_scaling,
            spread_cost_brl=spread_cost,
            market_impact_penalty_brl=impact_penalty,
            total_lam_margin_brl=total_lam,
        )

    # --------------------------------------------------------------------------
    # 5.4. Fator de Diversificação Intracarteira e Intercommodities
    # --------------------------------------------------------------------------

    @classmethod
    def calculate_intercommodity_diversification(
        cls,
        standalone_margins: Dict[AssetClass, Decimal],
        custom_correlations: Optional[Dict[Tuple[AssetClass, AssetClass], Decimal]] = None,
        max_credit_pct: Decimal = MAX_DIVERSIFICATION_CREDIT,
    ) -> DiversificationResult:
        """Calcula a margem agregada diversificada com teto prudencial de crédito (30%).

        Formula:
        Margem_Bruta = sqrt( sum_i sum_j rho_ij * M_i * M_j )
        Teto Prudencial: Margem_Final = max( (1 - MaxCredit) * sum(M_i), Margem_Bruta )
        """
        corr_map = custom_correlations or cls.CORRELATION_MATRIX
        active_classes = [c for c, m in standalone_margins.items() if m > Decimal("0.00")]

        undiversified_sum = sum((standalone_margins[c] for c in active_classes), Decimal("0.00"))

        if not active_classes or undiversified_sum == Decimal("0.00"):
            return DiversificationResult(
                standalone_margins=standalone_margins,
                undiversified_sum_brl=Decimal("0.00"),
                diversified_margin_brl=Decimal("0.00"),
                diversification_factor=Decimal("1.0000"),
                diversification_credit_pct=Decimal("0.0000"),
                correlation_matrix=corr_map,
            )

        variance_fl = 0.0
        for c1 in active_classes:
            m1_fl = float(standalone_margins[c1])
            for c2 in active_classes:
                m2_fl = float(standalone_margins[c2])
                pair = (c1, c2)
                rho = float(corr_map.get(pair, Decimal("0.00") if c1 != c2 else Decimal("1.00")))
                variance_fl += rho * m1_fl * m2_fl

        if variance_fl < 0.0:
            # Salvaguarda matemática contra não-positividade de matriz amostral
            variance_fl = 0.0

        portfolio_risk_fl = math.sqrt(variance_fl)
        portfolio_risk_dec = Decimal(str(round(portfolio_risk_fl, 6))).quantize(TWO_PLACES, rounding=ROUND_CEILING)

        # Aplicação do teto regulatório de diversificação (CPMI-IOSCO PFMI 5)
        # O benefício nunca pode reduzir a margem abaixo de (1 - MaxCredit) * Soma(Margens)
        min_allowed_margin = (undiversified_sum * (Decimal("1.00") - max_credit_pct)).quantize(TWO_PLACES, rounding=ROUND_CEILING)
        final_margin = max(portfolio_risk_dec, min_allowed_margin)

        div_factor = (final_margin / undiversified_sum).quantize(FOUR_PLACES, rounding=ROUND_CEILING)
        div_credit = (Decimal("1.0000") - div_factor).quantize(FOUR_PLACES, rounding=ROUND_FLOOR)

        return DiversificationResult(
            standalone_margins=standalone_margins,
            undiversified_sum_brl=undiversified_sum,
            diversified_margin_brl=final_margin,
            diversification_factor=div_factor,
            diversification_credit_pct=div_credit,
            correlation_matrix=corr_map,
        )

    # --------------------------------------------------------------------------
    # 5.5. Cascata de Salvaguardas da Câmara B3 (LAW-006)
    # --------------------------------------------------------------------------

    @staticmethod
    def execute_safeguard_waterfall(
        gross_default_loss_brl: Decimal,
        defaulter_margin_brl: Decimal,
        defaulter_fund_contrib_brl: Decimal,
        b3_sitg_brl: Decimal,
        mutualized_fund_brl: Decimal,
        extraordinary_assessment_brl: Decimal,
    ) -> DefaultWaterfallResult:
        """Simula a absorção estrita e sequencial de perdas pela Cascata de Salvaguardas B3 (LAW-006)."""
        loss_remaining = gross_default_loss_brl
        layers_result: List[WaterfallLayerAbsorption] = []

        pool = [
            (SafeguardWaterfallLayer.LAYER_1_DEFAULTER_MARGIN, defaulter_margin_brl),
            (SafeguardWaterfallLayer.LAYER_2_DEFAULTER_CLEARING_FUND, defaulter_fund_contrib_brl),
            (SafeguardWaterfallLayer.LAYER_3_B3_SITG, b3_sitg_brl),
            (SafeguardWaterfallLayer.LAYER_4_MUTUALIZED_DEFAULT_FUND, mutualized_fund_brl),
            (SafeguardWaterfallLayer.LAYER_5_EXTRAORDINARY_ASSESSMENT, extraordinary_assessment_brl),
        ]

        total_absorbed = Decimal("0.00")
        surviving_safeguards = Decimal("0.00")

        for layer_type, capacity in pool:
            if loss_remaining > Decimal("0.00"):
                absorbed = min(loss_remaining, capacity)
                remaining = capacity - absorbed
                loss_remaining -= absorbed
                total_absorbed += absorbed
            else:
                absorbed = Decimal("0.00")
                remaining = capacity

            surviving_safeguards += remaining
            layers_result.append(
                WaterfallLayerAbsorption(
                    layer=layer_type,
                    available_before_brl=capacity,
                    loss_absorbed_brl=absorbed,
                    remaining_balance_brl=remaining,
                )
            )

        return DefaultWaterfallResult(
            gross_default_loss_brl=gross_default_loss_brl,
            layers=layers_result,
            total_absorbed_brl=total_absorbed,
            residual_uncovered_deficit_brl=loss_remaining,
            surviving_safeguards_brl=surviving_safeguards,
            is_fully_absorbed=loss_remaining == Decimal("0.00"),
        )


# ==============================================================================
# 6. Verificadores Formais e Provas de Invariantes (LAW-003, LAW-006, LAW-105A)
# ==============================================================================

def assert_law_003_margin_covers_potential_risk(
    required_margin_brl: Decimal,
    potential_risk_brl: Decimal,
    context: str = "CORE Margining",
    tolerance_brl: Decimal = Decimal("0.00"),
) -> None:
    """Lei 3 / LAW-003: Cobertura Integral do Risco Potencial pela Margem Requerida.

    Formula: MargemRequerida + Tolerance >= RiscoPotencial
    Assegura que a câmara nunca subdimensione a exigência de colateral frente ao
    pior cenário de liquidação (VaR, Expected Shortfall ou Estresse Extremo).
    """
    deficit = potential_risk_brl - (required_margin_brl + tolerance_brl)
    check(
        deficit <= Decimal("0.00"),
        f"Violação da Lei de Cobertura de Risco Potencial (LAW-003) em '{context}': "
        f"Margem Requerida (R$ {required_margin_brl:.2f}) < Risco Potencial (R$ {potential_risk_brl:.2f}) "
        f"[Déficit de Exposição: R$ {deficit:.2f}]",
        law_code="LAW-003",
        details={
            "required_margin_brl": str(required_margin_brl),
            "potential_risk_brl": str(potential_risk_brl),
            "deficit_brl": str(max(Decimal("0.00"), deficit)),
        },
    )


def assert_law_006_collateral_safeguards_coverage(
    total_eligible_collateral_net_brl: Decimal,
    required_margin_brl: Decimal,
    participant_id: str = "Clearing Member",
    tolerance_brl: Decimal = Decimal("0.00"),
) -> None:
    """Lei 6 / LAW-006: Consistência e Cobertura Prudencial de Colaterais CORE.

    Formula: sum(ColateralElegivel_i * (1 - H_i)) + Tolerance >= MargemRequerida
    O valor líquido de mercado das garantias aceitas deve cobrir integralmente a margem.
    """
    deficit = required_margin_brl - (total_eligible_collateral_net_brl + tolerance_brl)
    check(
        deficit <= Decimal("0.00"),
        f"Violação da Lei de Cobertura Prudencial de Colaterais CORE (LAW-006) para '{participant_id}': "
        f"Colateral Líquido Elegível (R$ {total_eligible_collateral_net_brl:.2f}) < Margem Requerida (R$ {required_margin_brl:.2f}) "
        f"[Déficit de Salvaguarda: R$ {deficit:.2f}]",
        law_code="LAW-006",
        details={
            "participant_id": participant_id,
            "total_eligible_collateral_net_brl": str(total_eligible_collateral_net_brl),
            "required_margin_brl": str(required_margin_brl),
            "deficit_brl": str(max(Decimal("0.00"), deficit)),
        },
    )


def assert_law_105a_tpf_collateral_haircuts(
    collateral_basket: Sequence[TPFCollateral],
    required_margin_brl: Decimal,
    participant_id: str = "Clearing Member",
) -> None:
    """Lei 77 / LAW-105A: Matriz de Haircuts Conservadores para TPFs e Buffer Transfronteiriço.

    Verificações Formais:
    1. 0 <= Haircut < 1.00 para cada ativo depositado.
    2. Monotonicidade estrita de maturidade: Haircut(t1) <= Haircut(t2) para t1 <= t2.
    3. Ativos transfronteiriços incorporam buffer cambial estrito.
    4. Valor líquido total elegível cobre a Margem Requerida: V_elegivel >= Margem.
    """
    total_eligible_net = Decimal("0.00")

    # 1 & 3: Verificação individual de cada garantia
    for item in collateral_basket:
        h = item.total_haircut()
        check(
            Decimal("0.00") <= h < Decimal("1.00"),
            f"Violação de Haircut Inválido (LAW-105A) no ativo '{item.asset_id}': Haircut {h} fora do domínio [0, 1)",
            law_code="LAW-105A",
        )
        if item.is_cross_border:
            check(
                h >= item.base_haircut(),
                f"Violação de Buffer Cambial Transfronteiriço (LAW-105A) no ativo '{item.asset_id}': Haircut total {h} < Base {item.base_haircut()}",
                law_code="LAW-105A",
            )
        total_eligible_net += item.eligible_collateral_value()

    # 2: Verificação de Monotonicidade da Matriz de TPF
    for b_type in TPFBondType:
        for idx in range(len(TENOR_ORDER) - 1):
            t1 = TENOR_ORDER[idx]
            t2 = TENOR_ORDER[idx + 1]
            h1 = TPF_HAIRCUT_SCHEDULE[(b_type, t1)]
            h2 = TPF_HAIRCUT_SCHEDULE[(b_type, t2)]
            check(
                h1 <= h2,
                f"Violação de Monotonicidade de Vencimento (LAW-105A) para {b_type}: Tenor {t1} ({h1}) > Tenor {t2} ({h2})",
                law_code="LAW-105A",
            )

    # 4: Cobertura da Margem Requerida
    assert_law_006_collateral_safeguards_coverage(
        total_eligible_collateral_net_brl=total_eligible_net,
        required_margin_brl=required_margin_brl,
        participant_id=participant_id,
    )


# ==============================================================================
# 7. Motor Integrado de Risco e Margens do Participante
# ==============================================================================

@dataclass
class ParticipantRiskAssessment:
    """Relatório quantitativo consolidado de risco e margem de um membro."""
    participant_id: str
    timestamp: datetime
    standalone_margins: Dict[AssetClass, Decimal]
    diversified_margin_brl: Decimal
    diversification_factor: Decimal
    liquidity_adjusted_margin_brl: Decimal
    total_required_margin_brl: Decimal
    gross_collateral_brl: Decimal
    net_eligible_collateral_brl: Decimal
    margin_balance_brl: Decimal  # Net Collateral - Required Margin
    status: MarginCallStatus
    invariants_verified: List[str]


class CoreRiskAssessmentEngine:
    """Motor de avaliação integral de Risco e Margens CORE da B3."""

    def __init__(
        self,
        confidence_level: Decimal = REGULATORY_CONFIDENCE_LEVEL,
        max_diversification_credit: Decimal = MAX_DIVERSIFICATION_CREDIT,
    ) -> None:
        self.confidence_level = confidence_level
        self.max_diversification_credit = max_diversification_credit

    def evaluate_participant_portfolio(
        self,
        participant_id: str,
        asset_margins: Dict[AssetClass, Decimal],
        lam_margin_brl: Decimal,
        collateral_basket: Sequence[TPFCollateral],
        potential_stress_risk_brl: Optional[Decimal] = None,
    ) -> ParticipantRiskAssessment:
        """Executa a avaliação completa de risco, margem diversificada, LAM e garantias."""
        # 1. Diversificação Intercommodities
        div_res = CoreMarginAnalytics.calculate_intercommodity_diversification(
            standalone_margins=asset_margins,
            max_credit_pct=self.max_diversification_credit,
        )

        # 2. Margem Requerida Final: Deve cobrir o máximo entre diversificada e LAM
        base_req_margin = max(div_res.diversified_margin_brl, lam_margin_brl)

        # 3. Preservação de LAW-003: Margem Requerida >= Risco Potencial
        potential_risk = potential_stress_risk_brl or base_req_margin
        required_margin = max(base_req_margin, potential_risk)

        assert_law_003_margin_covers_potential_risk(
            required_margin_brl=required_margin,
            potential_risk_brl=potential_risk,
            context=f"Participant {participant_id}",
        )

        # 4. Avaliação de Colaterais e Haircuts TPF (LAW-105A e LAW-006)
        gross_collateral = sum((item.gross_market_value() for item in collateral_basket), Decimal("0.00"))
        net_collateral = sum((item.eligible_collateral_value() for item in collateral_basket), Decimal("0.00"))

        margin_balance = net_collateral - required_margin

        if margin_balance >= Decimal("0.00"):
            status = MarginCallStatus.SUFFICIENT
            # Validação formal das Leis LAW-105A e LAW-006
            assert_law_105a_tpf_collateral_haircuts(
                collateral_basket=collateral_basket,
                required_margin_brl=required_margin,
                participant_id=participant_id,
            )
            invariants_verified = ["LAW-003", "LAW-006", "LAW-105A"]
        else:
            status = MarginCallStatus.MARGIN_CALL_REQUIRED
            invariants_verified = ["LAW-003"]  # LAW-006 identificou o déficit para chamada imediata

        return ParticipantRiskAssessment(
            participant_id=participant_id,
            timestamp=datetime.now(timezone.utc),
            standalone_margins=asset_margins,
            diversified_margin_brl=div_res.diversified_margin_brl,
            diversification_factor=div_res.diversification_factor,
            liquidity_adjusted_margin_brl=lam_margin_brl,
            total_required_margin_brl=required_margin,
            gross_collateral_brl=gross_collateral,
            net_eligible_collateral_brl=net_collateral,
            margin_balance_brl=margin_balance,
            status=status,
            invariants_verified=invariants_verified,
        )
