"""
B3 Câmara & SPB — Gestão de Risco de Liquidez Intradiária e Testes de Estresse Cambial (FL-072)
=============================================================================================
Implementação normativa em conformidade com:
- Resolução CMN nº 4.557/2017 (Arts. 28 a 32 - Gerenciamento do Risco de Liquidez e GIR).
- Princípios CPMI-IOSCO PFMI nº 7 (Risco de Liquidez em Infraestruturas do Mercado Financeiro).
- Circular BACEN nº 3.749/2015 e Resolução BCB nº 229/2022 (LCR e Ativos de Alta Liquidez - HQLA).
- Regulamento da Câmara B3 e Catálogo de Mensageria SPB/STR.

Funcionalidades Principais:
1. Monitoramento contínuo da grade de liquidação intradiária em janelas horárias do SPB e Câmara B3:
   - Rastreamento de entradas e saídas programadas por sistema (SPB/STR, BM&F Derivativos,
     Ações/Renda Fixa D+2, Câmbio PvP, Balcão NoMe, SELIC).
   - Apuração do saldo cumulativo de caixa intradiário e identificação do pico de necessidade
     de liquidez (Maximum Cumulative Intraday Cash Outflow).
2. Cálculo do Colchão de Ativos de Alta Liquidez (HQLA) e Liquidity Coverage Ratio (LCR) Intradiário:
   - Categorização em Nível 1 (TPFs, Caixa/Reservas BACEN), Nível 2A (Soberanos Estrangeiros AAA, LIGs)
     e Nível 2B (Debêntures corporativas de alta liquidez, CDBs S1/S2).
   - Aplicação de haircuts regulatórios e dedução de ativos gravados/onerados.
   - Aplicação dos tetos regulatórios de composição (Cap de Nível 2 máximo de 40% e Cap de Nível 2B
     máximo de 15% do total de HQLA elegível).
   - Cálculo contínuo do LCR intradiário por janela horária e apuração do status de conformidade.
3. Simulação de Choques de Estresse Cambial Simultâneo na PTAX (saltos de 5% a 20%):
   - Reavaliação de posições ativas em derivativos cambiais (Futuros DOL/WDO BM&F, NDFs NoMe).
   - Geração determinística de chamadas de margem adicionais (Variation Margin e Adicional CORE).
   - Reavaliação de garantias internacionais em moeda estrangeira (efeito atenuador em BRL).
   - Injeção das obrigações de estresse nas janelas de liquidação da câmara.
4. Algoritmo de Projeção de Saldo Mínimo de Sobrevivência de Caixa (Survival Horizon):
   - Projeção dinâmica da trajetória de caixa ao longo das janelas intradiárias.
   - Absorção de déficits temporários mediante monetização ordenada de HQLA (Nível 1 -> 2A -> 2B).
   - Determinação do status de sobrevivência (ROBUST, DEFICIT_MONETIZED, DEFAULT).
   - Identificação do horizonte de sobrevivência em horas (Survival Horizon) e eventual shortfall.

Invariantes Formais Provadas:
- Lei 1: Conservação do colchão de liquidez estritamente não-negativo (HQLA_rem >= 0 e HQLA_mon + HQLA_rem == HQLA_elig).
- Lei 2: Conservação contábil dos fluxos de caixa da grade horária.
- Lei 3: Monotonicidade estrita dos choques cambiais na exigência de margem de estresse.
- Lei 4: Limites regulatórios de haircuts [0.00, 1.00] e co-domínio de HQLA elegível.
- Lei 5: Identidade de cobertura do LCR intradiário.
- Lei 6: Inviolabilidade dos tetos regulatórios de HQLA de Nível 2 (40%) e Nível 2B (15%).
- Lei 7: Exclusão mandante de ativos gravados ou com ônus no cômputo do HQLA.

Zero dependências externas: 100% Python Standard Library.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN, InvalidOperation
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")

# Tetos Regulatórios de Basiléia III / CMN 4.557
DEFAULT_MAX_LEVEL_2_CAP_RATIO = Decimal("0.40")  # Máximo 40% do total de HQLA
DEFAULT_MAX_LEVEL_2B_CAP_RATIO = Decimal("0.15")  # Máximo 15% do total de HQLA


# ==============================================================================
# 1. Enumerações Especializadas
# ==============================================================================

class SettlementSystem(str, Enum):
    """Sistemas e câmaras de liquidação financeira e de custódia participantes da grade."""
    SPB_STR = "SPB_STR"                   # Sistema de Transferência de Reservas (BACEN)
    B3_DERIVATIVES = "B3_DERIVATIVES"     # Câmara B3 — Ajustes Diários de Futuros e Margem CORE
    B3_EQUITIES = "B3_EQUITIES"           # Câmara B3 — Ações e Renda Fixa Privada (D+2)
    B3_FX_PVP = "B3_FX_PVP"               # Câmara de Câmbio B3 — Liquidação PvP Interbancária
    B3_BALCAO_NOME = "B3_BALCAO_NOME"     # B3 Balcão NoMe — Swaps, NDFs e Eventos Financeiros
    SELIC = "SELIC"                       # Sistema Especial de Liquidação e Custódia (BACEN/TPF)


class FlowDirection(str, Enum):
    """Direção do fluxo financeiro intradiário."""
    INFLOW = "INFLOW"     # Recebimento / Entrada de caixa
    OUTFLOW = "OUTFLOW"   # Pagamento / Saída de caixa


class HQLALevel(str, Enum):
    """Classificação regulatória de Ativos de Alta Liquidez (HQLA - Basiléia III / CMN 4.557)."""
    LEVEL_1 = "LEVEL_1"     # Caixa, Reservas no BACEN, Títulos Públicos Federais (LFT, LTN, NTN)
    LEVEL_2A = "LEVEL_2A"   # Títulos Soberanos Estrangeiros AAA/AA (US Treasuries, Bunds), LIGs
    LEVEL_2B = "LEVEL_2B"   # Debêntures Corporativas AAA/AA, CDBs S1/S2 de alta liquidez


class LCRComplianceStatus(str, Enum):
    """Status regulamentar de adequação do Liquidity Coverage Ratio (LCR) intradiário."""
    COMPLIANT = "COMPLIANT"                 # LCR >= 100% (Em conformidade plena)
    ALERT = "ALERT"                         # 80% <= LCR < 100% (Alerta preventivo de liquidez)
    CRITICAL_DEFICIT = "CRITICAL_DEFICIT"   # LCR < 80% (Déficit grave de cobertura)


class SurvivalStatus(str, Enum):
    """Status de solvência do horizonte de sobrevivência de caixa (Survival Horizon)."""
    ROBUST = "ROBUST"                       # Caixa permaneceu positivo sem necessidade de monetização
    DEFICIT_MONETIZED = "DEFICIT_MONETIZED" # Déficit temporário absorvido com sucesso via HQLA
    DEFAULT = "DEFAULT"                     # Esgotamento total de Caixa e HQLA (Inadimplência)


class FxDerivativeType(str, Enum):
    """Instrumentos derivativos cambiais sujeitos a estresse de variação e margem."""
    FUTURE_DOL = "FUTURE_DOL"                 # Futuro de Dólar Cheio BM&F (US$ 50.000)
    FUTURE_WDO = "FUTURE_WDO"                 # Mini-Contrato Futuro de Dólar (US$ 10.000)
    OTC_NDF = "OTC_NDF"                       # Termo de Moeda sem Entrega Física (NDF) NoMe
    FOREIGN_COLLATERAL = "FOREIGN_COLLATERAL" # Garantia em Moeda Estrangeira / US Treasuries


class PositionSide(str, Enum):
    """Lado da exposição patrimonial em moeda estrangeira."""
    LONG = "LONG"     # Comprado em USD (beneficia-se de alta da PTAX)
    SHORT = "SHORT"   # Vendido em USD (sofre chamada de margem com alta da PTAX)


# ==============================================================================
# 2. Exceções Especializadas
# ==============================================================================

class IntradayLiquidityError(Exception):
    """Exceção base para o fluxo de risco de liquidez intradiária (FL-072)."""
    pass


class InvalidTimeWindowError(IntradayLiquidityError):
    """Lançada quando a janela horária de liquidação é inválida ou fora de ordem."""
    pass


class InvalidHQLAError(IntradayLiquidityError):
    """Lançada quando um ativo HQLA possui parâmetros regulatórios inconsistentes."""
    pass


class InvalidFxShockError(IntradayLiquidityError):
    """Lançada quando o cenário de choque cambial é inconsistente ou extrapola limites."""
    pass


class LiquidityDeficitExhaustionError(IntradayLiquidityError):
    """Lançada quando a demanda de caixa supera a capacidade total de monetização."""
    pass


class LiquidityInvariantViolationError(IntradayLiquidityError):
    """Lançada quando uma lei formal de conservação financeira ou de colateral é violada."""
    pass


# ==============================================================================
# 3. Modelos de Domínio e Dataclasses
# ==============================================================================

@dataclass
class SettlementWindow:
    """Janela horária de liquidação financeira na grade intradiária B3 / SPB."""
    window_id: str                      # Ex: 'W0800', 'W1000', 'W1200', 'W1330', 'W1530', 'W1700', 'W1830'
    name: str                           # Ex: 'Abertura SPB e SELIC'
    start_time: str                     # Ex: '08:00'
    cutoff_time: str                    # Ex: '10:00'
    system_primary: SettlementSystem    # Sistema principal operante
    description: str                    # Detalhes operacionais da janela
    elapsed_hours_from_open: Decimal = Decimal("0.0")  # Horas decorridas desde a abertura do dia

    def __post_init__(self) -> None:
        if not self.window_id or not self.window_id.strip():
            raise InvalidTimeWindowError("Identificador de janela horária não pode ser vazio.")
        if self.elapsed_hours_from_open < Decimal("0.0"):
            raise InvalidTimeWindowError("Horas decorridas não podem ser negativas.")


@dataclass
class IntradayFlow:
    """Fluxo financeiro programado ou realizado em uma janela da grade."""
    flow_id: str
    window_id: str
    system: SettlementSystem
    direction: FlowDirection
    amount: Decimal
    description: str
    counterparty: Optional[str] = None
    is_mandatory: bool = True           # Se for obrigação mandatória com a CCP / SPB
    currency: str = "BRL"

    def __post_init__(self) -> None:
        if self.amount <= Decimal("0.00"):
            raise IntradayLiquidityError(f"Valor do fluxo {self.flow_id} deve ser estritamente positivo: {self.amount}")
        self.amount = self.amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass
class HQLAAsset:
    """Ativo integrante do colchão de alta liquidez (HQLA)."""
    asset_id: str
    asset_name: str
    level: HQLALevel
    market_value: Decimal               # Valor de mercado na moeda do ativo
    haircut_pct: Decimal                # Deságio regulatório [0.00, 1.00]
    is_encumbered: bool = False         # Se True, está bloqueado/onerado e é inelegível
    currency: str = "BRL"
    fx_rate: Decimal = Decimal("1.0000")# Taxa de conversão para BRL

    def __post_init__(self) -> None:
        if self.market_value < Decimal("0.00"):
            raise InvalidHQLAError(f"Valor de mercado do ativo {self.asset_id} não pode ser negativo.")
        if self.haircut_pct < Decimal("0.00") or self.haircut_pct > Decimal("1.00"):
            raise InvalidHQLAError(f"Haircut regulatório {self.haircut_pct} deve estar no intervalo [0.00, 1.00].")
        if self.fx_rate <= Decimal("0.00"):
            raise InvalidHQLAError(f"Taxa cambial do ativo {self.asset_id} deve ser estritamente positiva.")
        self.market_value = self.market_value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        self.fx_rate = self.fx_rate.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
        self.haircut_pct = self.haircut_pct.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)

    @property
    def gross_value_brl(self) -> Decimal:
        """Valor bruto convertido em BRL."""
        return (self.market_value * self.fx_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    @property
    def unencumbered_eligible_value(self) -> Decimal:
        """Valor líquido após haircut regulatório se desimpedido, ou 0 se gravado."""
        if self.is_encumbered:
            return Decimal("0.00")
        factor = Decimal("1.0000") - self.haircut_pct
        return (self.gross_value_brl * factor).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass
class HQLABufferReport:
    """Relatório estruturado de apuração do colchão de liquidez HQLA."""
    total_gross_market_value: Decimal
    total_encumbered_value: Decimal
    level_1_adjusted: Decimal
    level_2a_adjusted: Decimal
    level_2b_adjusted: Decimal
    level_2a_cap_allowed: Decimal
    level_2b_cap_allowed: Decimal
    level_2a_excess_deducted: Decimal
    level_2b_excess_deducted: Decimal
    total_eligible_hqla: Decimal
    effective_haircut_pct: Decimal


@dataclass
class FxPosition:
    """Posição em derivativo cambial ou garantia internacional exposta à PTAX."""
    position_id: str
    derivative_type: FxDerivativeType
    side: PositionSide
    quantity: int                       # Quantidade de contratos (positivo)
    contract_multiplier: Decimal        # Multiplicador (ex: US$ 50k para DOL, US$ 10k para WDO)
    entry_ptax: Decimal                 # Cotação base de entrada / fechamento anterior
    currency: str = "USD"

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise IntradayLiquidityError(f"Quantidade de contratos deve ser estritamente positiva: {self.quantity}")
        if self.contract_multiplier <= Decimal("0.00"):
            raise IntradayLiquidityError(f"Multiplicador contratual deve ser positivo: {self.contract_multiplier}")
        if self.entry_ptax <= Decimal("0.00"):
            raise IntradayLiquidityError(f"Taxa PTAX base de entrada deve ser positiva: {self.entry_ptax}")
        self.entry_ptax = self.entry_ptax.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)

    @property
    def notional_usd(self) -> Decimal:
        """Notional total em Dólares dos EUA (USD)."""
        return (Decimal(self.quantity) * self.contract_multiplier).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    def calculate_variation_pnl(self, shocked_ptax: Decimal) -> Decimal:
        """
        Calcula o P&L de variação de ajuste financeiro em BRL.
        Se SHORT e shocked_ptax > entry_ptax, o P&L é negativo (débito de margem).
        Se LONG e shocked_ptax > entry_ptax, o P&L é positivo (crédito de margem).
        """
        ptax_diff = shocked_ptax - self.entry_ptax
        if self.side == PositionSide.LONG:
            return (self.notional_usd * ptax_diff).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        else:
            return (self.notional_usd * -ptax_diff).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass
class FxStressScenario:
    """Cenário de choque simultâneo de estresse na cotação PTAX (BACEN)."""
    scenario_id: str
    name: str
    shock_pct: Decimal                  # Ex: Decimal("0.05") para +5%, Decimal("0.20") para +20%
    base_ptax: Decimal                  # PTAX base de referência (ex: Decimal("5.5000"))
    core_initial_margin_addon_pct: Decimal = Decimal("0.10")  # Adicional de margem inicial CORE (10%)

    def __post_init__(self) -> None:
        if self.base_ptax <= Decimal("0.00"):
            raise InvalidFxShockError("Taxa PTAX base deve ser estritamente positiva.")
        self.base_ptax = self.base_ptax.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
        self.shock_pct = self.shock_pct.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
        self.core_initial_margin_addon_pct = self.core_initial_margin_addon_pct.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)

    @property
    def shocked_ptax(self) -> Decimal:
        """Cotação PTAX estressada após a aplicação do salto percentual."""
        factor = Decimal("1.0000") + self.shock_pct
        return (self.base_ptax * factor).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


@dataclass
class FxStressResult:
    """Resultado da simulação do choque cambial PTAX sobre o portfólio da instituição."""
    scenario_id: str
    shock_pct: Decimal
    base_ptax: Decimal
    shocked_ptax: Decimal
    gross_variation_margin_debit: Decimal     # Débito de variação intradiária de futuros/NDF (BRL)
    gross_variation_margin_credit: Decimal    # Crédito de variação de posições compradas (BRL)
    core_initial_margin_addon: Decimal        # Exigência adicional de garantia CORE por volatilidade (BRL)
    foreign_collateral_revaluation_gain: Decimal # Ganho de valor em BRL das garantias em USD desimpedidas
    net_additional_margin_call: Decimal       # Chamada de margem líquida adicional exigida pela CCP (BRL)
    target_settlement_window: str             # Janela em que a chamada de margem deve ser honrada


@dataclass
class WindowLiquidityPoint:
    """Ponto de trajetória da grade de liquidação em uma janela horária específica."""
    window_id: str
    window_name: str
    inflows: Decimal
    outflows: Decimal
    scheduled_net_flow: Decimal
    stress_margin_call: Decimal
    cash_before_monetization: Decimal
    hqla_monetized: Decimal
    cash_after_monetization: Decimal
    remaining_hqla_buffer: Decimal
    remaining_cumulative_outflow: Decimal
    intraday_lcr_pct: Decimal
    lcr_status: LCRComplianceStatus
    is_exhausted: bool


@dataclass
class SurvivalHorizonReport:
    """Relatório analítico consolidado do horizonte de sobrevivência de caixa e estresse."""
    scenario_id: str
    scenario_name: str
    initial_cash: Decimal
    initial_hqla: Decimal
    total_inflows: Decimal
    total_outflows: Decimal
    total_stress_margin_calls: Decimal
    minimum_projected_cash: Decimal           # Menor saldo monetário antes da monetização
    maximum_cumulative_outflow: Decimal       # Pico máximo de saída acumulada intradiária
    total_hqla_monetized: Decimal
    final_cash_balance: Decimal
    remaining_hqla_buffer: Decimal
    survival_status: SurvivalStatus
    survival_horizon_hours: Decimal           # Horas de sobrevivência até eventual exaustão (ou 24h)
    exhaustion_window: Optional[str]          # Janela onde ocorreu o default se houver
    liquidity_shortfall: Decimal              # Volume de caixa não honrado (0.00 se sobreviveu)
    window_points: List[WindowLiquidityPoint] = field(default_factory=list)


# ==============================================================================
# 4. Motor Principal de Risco de Liquidez Intradiária
# ==============================================================================

class IntradayLiquidityRiskEngine:
    """
    Motor determinístico de monitoramento contínuo de liquidez intradiária,
    cálculo de HQLA/LCR e simulação de choques cambiais PTAX.
    """

    def __init__(
        self,
        institution_id: str = "INST-B3-001",
        institution_name: str = "Banco de Investimentos B3 S.A.",
        ispb: str = "00997185",
        initial_cash_balance: Decimal = Decimal("100000000.00"),
        level_2a_cap_ratio: Decimal = DEFAULT_MAX_LEVEL_2_CAP_RATIO,
        level_2b_cap_ratio: Decimal = DEFAULT_MAX_LEVEL_2B_CAP_RATIO,
    ) -> None:
        self.institution_id = institution_id
        self.institution_name = institution_name
        self.ispb = ispb
        self.initial_cash_balance = initial_cash_balance.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        self.level_2a_cap_ratio = level_2a_cap_ratio
        self.level_2b_cap_ratio = level_2b_cap_ratio

        self.windows: Dict[str, SettlementWindow] = {}
        self.flows: List[IntradayFlow] = []
        self.hqla_assets: Dict[str, HQLAAsset] = {}
        self.fx_positions: Dict[str, FxPosition] = {}

    # --------------------------------------------------------------------------
    # Registro de Componentes
    # --------------------------------------------------------------------------

    def register_window(self, window: SettlementWindow) -> None:
        """Registra uma janela horária de liquidação na grade."""
        self.windows[window.window_id] = window

    def register_flow(self, flow: IntradayFlow) -> None:
        """Registra um fluxo de entrada ou saída intradiário."""
        if flow.window_id not in self.windows:
            raise InvalidTimeWindowError(f"Janela horária {flow.window_id} não cadastrada na grade.")
        self.flows.append(flow)

    def register_hqla_asset(self, asset: HQLAAsset) -> None:
        """Registra um ativo no inventário de colateral de alta liquidez."""
        self.hqla_assets[asset.asset_id] = asset

    def register_fx_position(self, position: FxPosition) -> None:
        """Registra uma posição derivativa cambial ou garantia sujeita a estresse PTAX."""
        self.fx_positions[position.position_id] = position

    # --------------------------------------------------------------------------
    # 1. Cálculo do Colchão de Ativos de Alta Liquidez (HQLA)
    # --------------------------------------------------------------------------

    def calculate_hqla_buffer(self) -> HQLABufferReport:
        """
        Calcula o valor do colchão de alta liquidez (HQLA) aplicando:
        1. Deságios regulatórios (haircuts) por nível de liquidez.
        2. Dedução integral de ativos onerados/gravados (is_encumbered == True).
        3. Tetos regulatórios de Basiléia III / CMN 4.557:
           - Ativos de Nível 2 (2A + 2B) não podem exceder 40% do total de HQLA:
             L2 <= 40/60 * L1 = 2/3 * L1.
           - Ativos de Nível 2B não podem exceder 15% do total de HQLA:
             L2B <= 15/85 * (L1 + L2A).
        """
        total_gross = Decimal("0.00")
        total_encumbered = Decimal("0.00")
        l1_sum = Decimal("0.00")
        l2a_sum = Decimal("0.00")
        l2b_sum = Decimal("0.00")

        for asset in self.hqla_assets.values():
            gross_brl = asset.gross_value_brl
            total_gross += gross_brl

            if asset.is_encumbered:
                total_encumbered += gross_brl
                continue

            adj_val = asset.unencumbered_eligible_value
            if asset.level == HQLALevel.LEVEL_1:
                l1_sum += adj_val
            elif asset.level == HQLALevel.LEVEL_2A:
                l2a_sum += adj_val
            elif asset.level == HQLALevel.LEVEL_2B:
                l2b_sum += adj_val

        # Aplicação dos Tetos Regulatórios de Nível 2
        # Fórmula de Basiléia:
        # Max Level 2 = (40 / 60) * Level 1 = 2/3 * Level 1
        max_l2_allowed = (l1_sum * Decimal("40") / Decimal("60")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        
        # Max Level 2B = (15 / 85) * (Level 1 + Level 2A ajustado)
        # Primeiro calculamos o teto de 2B considerando L1 e L2A preliminar:
        max_l2b_allowed = ((l1_sum + l2a_sum) * Decimal("15") / Decimal("85")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Excesso em 2B
        l2b_eligible = min(l2b_sum, max_l2b_allowed)
        l2b_excess = max(Decimal("0.00"), l2b_sum - l2b_eligible)

        # Excesso no agregado de Nível 2 (2A + 2B_elegível)
        l2_combined = l2a_sum + l2b_eligible
        if l2_combined > max_l2_allowed:
            excess_l2 = l2_combined - max_l2_allowed
            # Deduz primeiramente do Nível 2B se houver espaço, senão de 2A
            deduct_from_2b = min(l2b_eligible, excess_l2)
            l2b_eligible -= deduct_from_2b
            l2b_excess += deduct_from_2b
            
            remaining_excess = excess_l2 - deduct_from_2b
            l2a_eligible = l2a_sum - remaining_excess
            l2a_excess = remaining_excess
        else:
            l2a_eligible = l2a_sum
            l2a_excess = Decimal("0.00")

        total_eligible = (l1_sum + l2a_eligible + l2b_eligible).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Haircut efetivo ponderado sobre os ativos desimpedidos
        unencumbered_gross = total_gross - total_encumbered
        if unencumbered_gross > Decimal("0.00"):
            effective_haircut = ((unencumbered_gross - total_eligible) / unencumbered_gross).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
        else:
            effective_haircut = Decimal("0.0000")

        return HQLABufferReport(
            total_gross_market_value=total_gross.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            total_encumbered_value=total_encumbered.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            level_1_adjusted=l1_sum.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            level_2a_adjusted=l2a_sum.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            level_2b_adjusted=l2b_sum.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            level_2a_cap_allowed=max_l2_allowed,
            level_2b_cap_allowed=max_l2b_allowed,
            level_2a_excess_deducted=l2a_excess.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            level_2b_excess_deducted=l2b_excess.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            total_eligible_hqla=total_eligible,
            effective_haircut_pct=effective_haircut,
        )

    # --------------------------------------------------------------------------
    # 2. Simulação de Choques de Estresse Cambial Simultâneo na PTAX
    # --------------------------------------------------------------------------

    def simulate_fx_stress(
        self,
        scenario: FxStressScenario,
        target_window_id: str = "W1000",
    ) -> FxStressResult:
        """
        Simula o impacto financeiro de um choque de estresse cambial na PTAX:
        - Variação de ajuste diário de derivativos (DOL, WDO, NDF).
        - Chamada de margem inicial adicional CORE proporcional ao choque.
        - Reavaliação benéfica de garantias em moeda estrangeira desimpedidas.
        - Consolidação da chamada de margem líquida intradiária.
        """
        if target_window_id not in self.windows:
            raise InvalidTimeWindowError(f"Janela alvo {target_window_id} inexistente.")

        shocked_ptax = scenario.shocked_ptax
        ptax_delta = shocked_ptax - scenario.base_ptax

        variation_debits = Decimal("0.00")
        variation_credits = Decimal("0.00")
        net_notional_short_usd = Decimal("0.00")

        # 1. Impacto nos derivativos cambiais
        for pos in self.fx_positions.values():
            pnl = pos.calculate_variation_pnl(shocked_ptax)
            if pnl < Decimal("0.00"):
                variation_debits += abs(pnl)
            else:
                variation_credits += pnl

            if pos.side == PositionSide.SHORT:
                net_notional_short_usd += pos.notional_usd
            else:
                net_notional_short_usd -= pos.notional_usd

        # 2. Margem inicial adicional CORE por volatilidade cambial estressada
        # Aplicada sobre a exposição direcional líquida descoberta
        abs_net_usd = max(Decimal("0.00"), net_notional_short_usd)
        core_addon = (abs_net_usd * ptax_delta * scenario.core_initial_margin_addon_pct).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        if core_addon < Decimal("0.00"):
            core_addon = Decimal("0.00")

        # 3. Ganho de valor das garantias em moeda estrangeira desimpedidas
        collateral_gain = Decimal("0.00")
        for asset in self.hqla_assets.values():
            if asset.currency == "USD" and not asset.is_encumbered:
                # O ativo em USD valoriza em BRL com o choque da PTAX
                gain = (asset.market_value * ptax_delta * (Decimal("1.0000") - asset.haircut_pct)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                if gain > Decimal("0.00"):
                    collateral_gain += gain

        # 4. Chamada de margem líquida adicional exigida pela câmara
        # Margem Líquida = Débito Variação + Adicional CORE - Ganho de Colateral em USD
        net_margin_call = variation_debits + core_addon - collateral_gain
        if net_margin_call < Decimal("0.00"):
            net_margin_call = Decimal("0.00")

        return FxStressResult(
            scenario_id=scenario.scenario_id,
            shock_pct=scenario.shock_pct,
            base_ptax=scenario.base_ptax,
            shocked_ptax=shocked_ptax,
            gross_variation_margin_debit=variation_debits.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            gross_variation_margin_credit=variation_credits.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            core_initial_margin_addon=core_addon.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            foreign_collateral_revaluation_gain=collateral_gain.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            net_additional_margin_call=net_margin_call.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            target_settlement_window=target_window_id,
        )

    # --------------------------------------------------------------------------
    # 3. Algoritmo de Projeção de Caixa e Survival Horizon
    # --------------------------------------------------------------------------

    def calculate_survival_horizon(
        self,
        scenario: Optional[FxStressScenario] = None,
        target_stress_window: str = "W1000",
    ) -> SurvivalHorizonReport:
        """
        Executa a projeção dinâmica da trajetória de caixa ao longo da grade intradiária:
        1. Processa entradas e saídas programadas por janela.
        2. Injeta chamadas de margem adicionais do cenário de estresse cambial PTAX.
        3. Identifica déficits de caixa temporários e monetiza ordenadamente o colchão HQLA.
        4. Avalia o Liquidity Coverage Ratio (LCR) intradiário a cada janela.
        5. Determina o horizonte de sobrevivência em horas (Survival Horizon) e status final.
        """
        # 1. Apura HQLA inicial elegível
        hqla_report = self.calculate_hqla_buffer()
        available_hqla = hqla_report.total_eligible_hqla
        initial_hqla = available_hqla

        # 2. Simula estresse cambial se fornecido
        fx_result: Optional[FxStressResult] = None
        if scenario is not None:
            fx_result = self.simulate_fx_stress(scenario, target_window_id=target_stress_window)

        # 3. Ordena as janelas cronologicamente
        sorted_windows = sorted(
            self.windows.values(),
            key=lambda w: (w.elapsed_hours_from_open, w.start_time, w.window_id),
        )

        # 4. Agrupa fluxos programados por janela
        window_inflows: Dict[str, Decimal] = {w.window_id: Decimal("0.00") for w in sorted_windows}
        window_outflows: Dict[str, Decimal] = {w.window_id: Decimal("0.00") for w in sorted_windows}

        for f in self.flows:
            if f.direction == FlowDirection.INFLOW:
                window_inflows[f.window_id] += f.amount
            else:
                window_outflows[f.window_id] += f.amount

        # 5. Inicialização da simulação dinâmica
        current_cash = self.initial_cash_balance
        min_projected_cash = current_cash
        max_cumulative_outflow = Decimal("0.00")
        cumulative_net_flow_tracking = Decimal("0.00")
        total_inflows = Decimal("0.00")
        total_outflows = Decimal("0.00")
        total_stress_margin_calls = Decimal("0.00")
        total_hqla_monetized = Decimal("0.00")

        survival_status = SurvivalStatus.ROBUST
        exhaustion_window: Optional[str] = None
        survival_hours = Decimal("24.0")  # Padrão: dia completo superado com sucesso
        shortfall = Decimal("0.00")
        window_points: List[WindowLiquidityPoint] = []

        # Calcula saídas líquidas totais remanescentes para o LCR
        total_remaining_scheduled_outflow = sum(window_outflows.values())
        if fx_result:
            total_remaining_scheduled_outflow += fx_result.net_additional_margin_call

        for idx, win in enumerate(sorted_windows):
            w_id = win.window_id
            inflow = window_inflows[w_id]
            outflow = window_outflows[w_id]
            total_inflows += inflow
            total_outflows += outflow

            # Verifica se há chamada de margem de estresse nesta janela
            margin_call = Decimal("0.00")
            if fx_result and fx_result.target_settlement_window == w_id:
                margin_call = fx_result.net_additional_margin_call
                total_stress_margin_calls += margin_call

            # Saldo financeiro da janela antes de qualquer monetização
            scheduled_net = inflow - outflow - margin_call
            cumulative_net_flow_tracking += scheduled_net

            # Rastreamento do pico de necessidade de liquidez (Max Cumulative Outflow)
            if cumulative_net_flow_tracking < Decimal("0.00"):
                peak_need = abs(cumulative_net_flow_tracking)
                if peak_need > max_cumulative_outflow:
                    max_cumulative_outflow = peak_need

            cash_before = current_cash + scheduled_net
            if cash_before < min_projected_cash:
                min_projected_cash = cash_before

            hqla_monetized_this_window = Decimal("0.00")
            cash_after = cash_before
            is_exhausted = False

            # Se o caixa ficar negativo, aciona monetização do colchão HQLA
            if cash_before < Decimal("0.00"):
                deficit = abs(cash_before)
                if available_hqla >= deficit:
                    # Déficit absorvido integralmente pelo HQLA
                    hqla_monetized_this_window = deficit
                    available_hqla -= deficit
                    total_hqla_monetized += deficit
                    cash_after = Decimal("0.00")
                    if survival_status == SurvivalStatus.ROBUST:
                        survival_status = SurvivalStatus.DEFICIT_MONETIZED
                else:
                    # HQLA insuficiente: DEFAULT da instituição na janela!
                    hqla_monetized_this_window = available_hqla
                    shortfall = deficit - available_hqla
                    cash_after = -shortfall
                    total_hqla_monetized += available_hqla
                    available_hqla = Decimal("0.00")
                    is_exhausted = True
                    survival_status = SurvivalStatus.DEFAULT
                    if exhaustion_window is None:
                        exhaustion_window = w_id
                        survival_hours = win.elapsed_hours_from_open

            current_cash = cash_after

            # Cálculo das saídas líquidas estressadas remanescentes para o LCR
            # Net Outflows = Max(0, Saídas Remanescentes - Entradas Remanescentes)
            remaining_outflows_sum = sum(window_outflows[w.window_id] for w in sorted_windows[idx:])
            remaining_inflows_sum = sum(window_inflows[w.window_id] for w in sorted_windows[idx:])
            
            # Adiciona a chamada de margem remanescente se ainda não ocorreu
            if fx_result and any(w.window_id == fx_result.target_settlement_window for w in sorted_windows[idx:]):
                remaining_outflows_sum += fx_result.net_additional_margin_call

            net_remaining_outflow = max(Decimal("0.00"), remaining_outflows_sum - remaining_inflows_sum)

            # Cálculo do LCR Intradiário
            # LCR = (HQLA Remanescente + Caixa Atual) / Saídas Líquidas Remanescentes
            liquid_resources = available_hqla + max(Decimal("0.00"), current_cash)
            if net_remaining_outflow > Decimal("0.00"):
                lcr_ratio = ((liquid_resources / net_remaining_outflow) * Decimal("100.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            else:
                lcr_ratio = Decimal("999.99")

            if lcr_ratio >= Decimal("100.00"):
                lcr_status = LCRComplianceStatus.COMPLIANT
            elif lcr_ratio >= Decimal("80.00"):
                lcr_status = LCRComplianceStatus.ALERT
            else:
                lcr_status = LCRComplianceStatus.CRITICAL_DEFICIT

            point = WindowLiquidityPoint(
                window_id=w_id,
                window_name=win.name,
                inflows=inflow,
                outflows=outflow,
                scheduled_net_flow=scheduled_net,
                stress_margin_call=margin_call,
                cash_before_monetization=cash_before,
                hqla_monetized=hqla_monetized_this_window,
                cash_after_monetization=cash_after,
                remaining_hqla_buffer=available_hqla,
                remaining_cumulative_outflow=net_remaining_outflow,
                intraday_lcr_pct=lcr_ratio,
                lcr_status=lcr_status,
                is_exhausted=is_exhausted,
            )
            window_points.append(point)

            if is_exhausted:
                # Se entrou em default, preenche as janelas subsequentes com status de default
                for subsequent_win in sorted_windows[idx + 1:]:
                    sub_point = WindowLiquidityPoint(
                        window_id=subsequent_win.window_id,
                        window_name=subsequent_win.name,
                        inflows=window_inflows[subsequent_win.window_id],
                        outflows=window_outflows[subsequent_win.window_id],
                        scheduled_net_flow=window_inflows[subsequent_win.window_id] - window_outflows[subsequent_win.window_id],
                        stress_margin_call=Decimal("0.00"),
                        cash_before_monetization=current_cash,
                        hqla_monetized=Decimal("0.00"),
                        cash_after_monetization=current_cash,
                        remaining_hqla_buffer=Decimal("0.00"),
                        remaining_cumulative_outflow=Decimal("0.00"),
                        intraday_lcr_pct=Decimal("0.00"),
                        lcr_status=LCRComplianceStatus.CRITICAL_DEFICIT,
                        is_exhausted=True,
                    )
                    window_points.append(sub_point)
                break

        scenario_id = scenario.scenario_id if scenario else "BASELINE"
        scenario_name = scenario.name if scenario else "Cenário Base (Sem Estresse Cambial)"

        return SurvivalHorizonReport(
            scenario_id=scenario_id,
            scenario_name=scenario_name,
            initial_cash=self.initial_cash_balance,
            initial_hqla=initial_hqla,
            total_inflows=total_inflows.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            total_outflows=total_outflows.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            total_stress_margin_calls=total_stress_margin_calls.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            minimum_projected_cash=min_projected_cash.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            maximum_cumulative_outflow=max_cumulative_outflow.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            total_hqla_monetized=total_hqla_monetized.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            final_cash_balance=current_cash.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            remaining_hqla_buffer=available_hqla.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            survival_status=survival_status,
            survival_horizon_hours=survival_hours.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            exhaustion_window=exhaustion_window,
            liquidity_shortfall=shortfall.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            window_points=window_points,
        )

    def run_stress_testing_suite(
        self,
        scenarios: List[FxStressScenario],
        target_stress_window: str = "W1000",
    ) -> Dict[str, SurvivalHorizonReport]:
        """Executa bateria de testes de estresse cambial para múltiplos cenários PTAX."""
        reports: Dict[str, SurvivalHorizonReport] = {}
        for sc in scenarios:
            reports[sc.scenario_id] = self.calculate_survival_horizon(
                scenario=sc,
                target_stress_window=target_stress_window,
            )
        return reports

    def verify_fx_shock_monotonicity(
        self,
        ordered_scenarios: List[FxStressScenario],
        target_stress_window: str = "W1000",
    ) -> bool:
        """
        Verifica a monotonicidade estrita da chamada de margem de estresse:
        Para saltos crescentes de PTAX (+5% < +10% < +15% < +20%),
        a chamada de margem adicional calculada deve ser monotonicamente não-decrescente.
        """
        last_margin_call = Decimal("-1.00")
        for sc in ordered_scenarios:
            res = self.simulate_fx_stress(sc, target_window_id=target_stress_window)
            if res.net_additional_margin_call < last_margin_call:
                return False
            last_margin_call = res.net_additional_margin_call
        return True

    # --------------------------------------------------------------------------
    # 4. Prova Formal de Invariantes
    # --------------------------------------------------------------------------

    def verify_formal_invariants(self, report: SurvivalHorizonReport) -> Dict[str, bool]:
        """
        Verificação formal estrita das 6 Leis Matemáticas e Regulatórias:
        1. Invariante de Conservação do Colchão de Liquidez:
           HQLA_monetized + HQLA_remaining == HQLA_initial e HQLA_remaining >= 0.
        2. Invariante Contábil de Fechamento de Caixa:
           Final_Cash == Initial_Cash + Inflows - Outflows - MarginCalls + HQLA_monetized.
        3. Invariante de Não-Negatividade sob Sobrevivência:
           Se status != DEFAULT, Final_Cash >= 0 e Shortfall == 0.
        4. Invariante de Limites de Haircuts e Co-domínio HQLA:
           Todo haircut em [0.00, 1.00] e HQLA_elegível <= Ativos Brutos.
        5. Invariante de Inviolabilidade de Tetos Regulatórios de Nível 2:
           Level 2 <= 40% do HQLA e Level 2B <= 15% do HQLA.
        6. Invariante de Exclusão de Ativos Onerados / Gravados:
           Nenhum ativo encumbered participa do colchão HQLA.
        """
        results: Dict[str, bool] = {}

        # Lei 1: Conservação do Colchão de Liquidez
        hqla_sum = report.total_hqla_monetized + report.remaining_hqla_buffer
        law1 = (
            abs(hqla_sum - report.initial_hqla) <= Decimal("0.02")
            and report.remaining_hqla_buffer >= Decimal("0.00")
            and report.total_hqla_monetized >= Decimal("0.00")
        )
        results["liquidity_buffer_conservation"] = law1

        # Lei 2: Conservação Contábil de Fechamento de Caixa
        expected_cash = (
            report.initial_cash
            + report.total_inflows
            - report.total_outflows
            - report.total_stress_margin_calls
            + report.total_hqla_monetized
        )
        law2 = abs(report.final_cash_balance - expected_cash) <= Decimal("0.02")
        results["cash_grid_accounting_conservation"] = law2

        # Lei 3: Não-Negatividade de Caixa sob Sobrevivência
        if report.survival_status in (SurvivalStatus.ROBUST, SurvivalStatus.DEFICIT_MONETIZED):
            law3 = report.final_cash_balance >= Decimal("0.00") and report.liquidity_shortfall == Decimal("0.00")
        else:
            law3 = report.liquidity_shortfall > Decimal("0.00")
        results["survival_non_negativity"] = law3

        # Lei 4: Limites de Haircuts e Co-domínio HQLA
        hqla_rep = self.calculate_hqla_buffer()
        law4 = (
            all(Decimal("0.00") <= a.haircut_pct <= Decimal("1.00") for a in self.hqla_assets.values())
            and Decimal("0.00") <= hqla_rep.total_eligible_hqla <= hqla_rep.total_gross_market_value
        )
        results["hqla_haircut_bounds"] = law4

        # Lei 5: Inviolabilidade dos Tetos Regulatórios de Basiléia III
        # Level 2 total elegível <= 40% do total de HQLA (com tolerância de arredondamento)
        total_hqla = hqla_rep.total_eligible_hqla
        if total_hqla > Decimal("0.00"):
            l2_eligible_total = total_hqla - hqla_rep.level_1_adjusted
            l2_ratio = l2_eligible_total / total_hqla
            # 40% com folga de arredondamento
            law5_a = l2_ratio <= (DEFAULT_MAX_LEVEL_2_CAP_RATIO + Decimal("0.005"))
            
            l2b_ratio = (hqla_rep.level_2b_adjusted - hqla_rep.level_2b_excess_deducted) / total_hqla
            law5_b = l2b_ratio <= (DEFAULT_MAX_LEVEL_2B_CAP_RATIO + Decimal("0.005"))
            law5 = law5_a and law5_b
        else:
            law5 = True
        results["regulatory_hqla_caps"] = law5

        # Lei 6: Exclusão Mandatória de Ativos Onerados / Gravados
        law6 = all(
            a.unencumbered_eligible_value == Decimal("0.00")
            for a in self.hqla_assets.values()
            if a.is_encumbered
        )
        results["encumbered_asset_exclusion"] = law6

        return results

    # --------------------------------------------------------------------------
    # 5. Serialização e Carga de Fixtures
    # --------------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o motor e seu estado completo para um dicionário JSON compatível."""
        return {
            "institution_id": self.institution_id,
            "institution_name": self.institution_name,
            "ispb": self.ispb,
            "initial_cash_balance": str(self.initial_cash_balance),
            "level_2a_cap_ratio": str(self.level_2a_cap_ratio),
            "level_2b_cap_ratio": str(self.level_2b_cap_ratio),
            "windows": [
                {
                    "window_id": w.window_id,
                    "name": w.name,
                    "start_time": w.start_time,
                    "cutoff_time": w.cutoff_time,
                    "system_primary": w.system_primary.value,
                    "description": w.description,
                    "elapsed_hours_from_open": str(w.elapsed_hours_from_open),
                }
                for w in self.windows.values()
            ],
            "flows": [
                {
                    "flow_id": f.flow_id,
                    "window_id": f.window_id,
                    "system": f.system.value,
                    "direction": f.direction.value,
                    "amount": str(f.amount),
                    "description": f.description,
                    "counterparty": f.counterparty,
                    "is_mandatory": f.is_mandatory,
                    "currency": f.currency,
                }
                for f in self.flows
            ],
            "hqla_assets": [
                {
                    "asset_id": a.asset_id,
                    "asset_name": a.asset_name,
                    "level": a.level.value,
                    "market_value": str(a.market_value),
                    "haircut_pct": str(a.haircut_pct),
                    "is_encumbered": a.is_encumbered,
                    "currency": a.currency,
                    "fx_rate": str(a.fx_rate),
                }
                for a in self.hqla_assets.values()
            ],
            "fx_positions": [
                {
                    "position_id": p.position_id,
                    "derivative_type": p.derivative_type.value,
                    "side": p.side.value,
                    "quantity": p.quantity,
                    "contract_multiplier": str(p.contract_multiplier),
                    "entry_ptax": str(p.entry_ptax),
                    "currency": p.currency,
                }
                for p in self.fx_positions.values()
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> IntradayLiquidityRiskEngine:
        """Instancia o motor a partir de um dicionário estruturado."""
        engine = cls(
            institution_id=data.get("institution_id", "INST-B3-001"),
            institution_name=data.get("institution_name", "Banco B3"),
            ispb=data.get("ispb", "00997185"),
            initial_cash_balance=Decimal(str(data.get("initial_cash_balance", "100000000.00"))),
            level_2a_cap_ratio=Decimal(str(data.get("level_2a_cap_ratio", DEFAULT_MAX_LEVEL_2_CAP_RATIO))),
            level_2b_cap_ratio=Decimal(str(data.get("level_2b_cap_ratio", DEFAULT_MAX_LEVEL_2B_CAP_RATIO))),
        )

        # 1. Carrega Janelas
        for win_data in data.get("windows", []):
            win = SettlementWindow(
                window_id=win_data["window_id"],
                name=win_data["name"],
                start_time=win_data["start_time"],
                cutoff_time=win_data["cutoff_time"],
                system_primary=SettlementSystem(win_data["system_primary"]),
                description=win_data.get("description", ""),
                elapsed_hours_from_open=Decimal(str(win_data.get("elapsed_hours_from_open", "0.0"))),
            )
            engine.register_window(win)

        # 2. Carrega Fluxos
        for flow_data in data.get("flows", []):
            flow = IntradayFlow(
                flow_id=flow_data["flow_id"],
                window_id=flow_data["window_id"],
                system=SettlementSystem(flow_data["system"]),
                direction=FlowDirection(flow_data["direction"]),
                amount=Decimal(str(flow_data["amount"])),
                description=flow_data.get("description", ""),
                counterparty=flow_data.get("counterparty"),
                is_mandatory=flow_data.get("is_mandatory", True),
                currency=flow_data.get("currency", "BRL"),
            )
            engine.register_flow(flow)

        # 3. Carrega HQLA
        for hqla_data in data.get("hqla_assets", []):
            asset = HQLAAsset(
                asset_id=hqla_data["asset_id"],
                asset_name=hqla_data["asset_name"],
                level=HQLALevel(hqla_data["level"]),
                market_value=Decimal(str(hqla_data["market_value"])),
                haircut_pct=Decimal(str(hqla_data["haircut_pct"])),
                is_encumbered=hqla_data.get("is_encumbered", False),
                currency=hqla_data.get("currency", "BRL"),
                fx_rate=Decimal(str(hqla_data.get("fx_rate", "1.0000"))),
            )
            engine.register_hqla_asset(asset)

        # 4. Carrega Posições Cambiais
        for fx_data in data.get("fx_positions", []):
            pos = FxPosition(
                position_id=fx_data["position_id"],
                derivative_type=FxDerivativeType(fx_data["derivative_type"]),
                side=PositionSide(fx_data["side"]),
                quantity=int(fx_data["quantity"]),
                contract_multiplier=Decimal(str(fx_data["contract_multiplier"])),
                entry_ptax=Decimal(str(fx_data["entry_ptax"])),
                currency=fx_data.get("currency", "USD"),
            )
            engine.register_fx_position(pos)

        return engine

    @classmethod
    def load_from_fixture(cls, file_path: Union[str, Path]) -> IntradayLiquidityRiskEngine:
        """Carrega e instancia o motor a partir de um arquivo JSON de fixture oficial."""
        path = Path(file_path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
