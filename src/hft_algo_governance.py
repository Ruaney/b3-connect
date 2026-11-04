"""Motor de Governança de Negociação Algorítmica e HFT — B3 / BSM (FL-047).

Implementa os requisitos regulatórios e técnicos de governança para algoritmos de
negociação e High Frequency Trading (HFT) sob as diretrizes da:
- Resolução CVM nº 35/2021 (sucessora da Instrução CVM nº 505/2011)
- MiFID II / RTS 25 (Clock Synchronization tolerance < 100 µs para HFT)
- Política de Order-to-Trade Ratio (OTR) Dinâmico da B3 e Supervisão da BSM
- Protocolo FIX PUMA Trading System (Tags 1031, 57, 1724, 8015)

Zero dependências externas (Python Standard Library pura).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union


# =====================================================================
# ENUMS E CONSTANTES REGULATÓRIAS (CVM 505 / RTS 25 / B3 / BSM)
# =====================================================================

class TradingAlgorithmClass(str, Enum):
    """Classificação operacional de estratégia de negociação conforme CVM 35 / RTS 25."""

    MANUAL_DISCRETIONARY = "MANUAL_DISCRETIONARY"
    """Ordem inserida por operador humano via mesa ou Home Broker sem automação."""

    ALGORITHMIC_STANDARD = "ALGORITHMIC_STANDARD"
    """Algoritmo de execução programada convencional (VWAP, TWAP, POV, rebalanceamento)."""

    HIGH_FREQUENCY_TRADING = "HIGH_FREQUENCY_TRADING"
    """Algoritmo de Alta Frequência (HFT) com co-location, sub-milissegundo e alto giro de mensagens."""


class CustOrderHandlingType(str, Enum):
    """Tag FIX 1031: Tipo de manuseio e canal de roteamento da ordem."""

    ALGORITHMIC = "D"          # Execução automatizada algorítmica
    DMA = "N"                  # Acesso Direto ao Mercado tradicional
    DESK_MANUAL = "W"          # Mesa de operações manual discricionária
    SPONSORED_DMA = "Y"        # DMA Patrocinado via infraestrutura dedicada


class OrderOriginationRole(int, Enum):
    """Tag FIX 1724: Papel regulatório da entidade originadora da ordem."""

    AGENCY = 1                 # Ordem por conta de terceiros (cliente)
    PRINCIPAL = 2              # Carteira própria / mesa proprietária
    RISKLESS_PRINCIPAL = 3     # Principal sem risco
    ALGORITHM_DEA = 4          # Algoritmo decisor direto / DEA investment firm
    SYSTEMATIC_INTERNALISER = 5 # Internalizador sistemático


class ActionType(str, Enum):
    """Ciclo de vida de eventos de ordem no PUMA Trading System."""

    NEW = "NEW"                # Inserção de nova oferta (NewOrderSingle 35=D)
    REPLACE = "REPLACE"        # Alteração de oferta (OrderCancelReplaceRequest 35=G)
    CANCEL = "CANCEL"          # Cancelamento de oferta (OrderCancelRequest 35=F)
    TRADE = "TRADE"            # Execução de negócio (ExecutionReport Fill/Partial 35=8)


class AssetClass(str, Enum):
    """Classes de ativos B3 para enquadramento de limites dinâmicos de OTR."""

    EQUITIES_LARGE_CAP = "EQUITIES_LARGE_CAP"   # Ações Ibovespa / Alta Liquidez
    EQUITIES_SMALL_CAP = "EQUITIES_SMALL_CAP"   # Ações fora do Ibovespa / Small Caps
    FUTURES_FINANCIAL = "FUTURES_FINANCIAL"     # Futuros B3 (DI1, DOL, IND, WDO, WIN)
    OPTIONS = "OPTIONS"                         # Opções sobre Ações e Futuros


class PenaltyTier(str, Enum):
    """Faixas progressivas de penalidade tarifária por excesso de OTR da B3."""

    NONE = "NONE"              # Dentro do teto permitido
    TIER_1 = "TIER_1"          # Excesso Moderado (1.0x < Er <= 1.5x)
    TIER_2 = "TIER_2"          # Excesso Grave (1.5x < Er <= 2.5x)
    TIER_3 = "TIER_3"          # Excesso Crítico (Er > 2.5x + Taxa Administrativa Fixa)


# Tolerâncias máximas de desvio temporal UTC (RTS 25 Clock Drift) em microssegundos
MAX_CLOCK_DRIFT_MICROS: Dict[TradingAlgorithmClass, int] = {
    TradingAlgorithmClass.HIGH_FREQUENCY_TRADING: 100,          # < 100 µs (RTS 25 HFT)
    TradingAlgorithmClass.ALGORITHMIC_STANDARD: 1_000,          # < 1 ms (1.000 µs)
    TradingAlgorithmClass.MANUAL_DISCRETIONARY: 1_000_000,      # < 1 s (1.000.000 µs)
}


# =====================================================================
# EXCEÇÕES DE GOVERNANÇA E AUDITORIA
# =====================================================================

class HftGovernanceError(Exception):
    """Exceção base de governança e fiscalização algorítmica."""


class RegulatoryTagValidationError(HftGovernanceError):
    """Erro de ausência ou inconsistência em tags mandatórias CVM 505 / FIX."""


class ClockSynchronizationError(HftGovernanceError):
    """Divergência de sincronização de relógio superior aos limites da RTS 25."""


class OtrViolationError(HftGovernanceError):
    """Violação de limites de Order-to-Trade Ratio (OTR) da B3."""


# =====================================================================
# DATA CLASSES DE GOVERNANÇA, TAGS E LIMITES
# =====================================================================

@dataclass
class AlgoOrderTag:
    """Metadados e tags regulatórias FIX / CVM 505 / RTS 25 de um evento de ordem."""

    cl_ord_id: str
    symbol: str
    action_type: ActionType
    quantity: int
    price: Optional[Decimal]
    timestamp: datetime
    cust_order_handling_type: CustOrderHandlingType
    trading_algorithm_id: Optional[str] = None
    order_origination_role: OrderOriginationRole = OrderOriginationRole.ALGORITHM_DEA
    executing_trader: str = ""
    algo_class: TradingAlgorithmClass = TradingAlgorithmClass.HIGH_FREQUENCY_TRADING
    clock_drift_micros: int = 0
    order_id: Optional[str] = None
    execution_id: Optional[str] = None
    account_id: Optional[str] = None
    client_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.action_type, str):
            self.action_type = ActionType(self.action_type)
        if isinstance(self.cust_order_handling_type, str):
            self.cust_order_handling_type = CustOrderHandlingType(self.cust_order_handling_type)
        if isinstance(self.order_origination_role, (int, str)):
            self.order_origination_role = OrderOriginationRole(int(self.order_origination_role))
        if isinstance(self.algo_class, str):
            self.algo_class = TradingAlgorithmClass(self.algo_class)
        if self.price is not None and not isinstance(self.price, Decimal):
            self.price = Decimal(str(self.price))

    def to_dict(self) -> Dict[str, Any]:
        """Converte a estrutura para dicionário compatível com JSON."""
        return {
            "cl_ord_id": self.cl_ord_id,
            "symbol": self.symbol,
            "action_type": self.action_type.value,
            "quantity": self.quantity,
            "price": float(self.price) if self.price is not None else None,
            "timestamp": self.timestamp.isoformat(),
            "cust_order_handling_type": self.cust_order_handling_type.value,
            "trading_algorithm_id": self.trading_algorithm_id,
            "order_origination_role": self.order_origination_role.value,
            "executing_trader": self.executing_trader,
            "algo_class": self.algo_class.value,
            "clock_drift_micros": self.clock_drift_micros,
            "order_id": self.order_id,
            "execution_id": self.execution_id,
            "account_id": self.account_id,
            "client_id": self.client_id,
            "metadata": self.metadata,
        }


@dataclass
class OtrAssetLimit:
    """Parâmetros e limites de Order-to-Trade Ratio e tarifação por classe de ativo."""

    asset_class: AssetClass
    max_otr_volume: Decimal
    max_otr_quantity: Decimal
    tier1_rate_per_order: Decimal = Decimal("0.05")
    tier2_rate_per_order: Decimal = Decimal("0.15")
    tier3_rate_per_order: Decimal = Decimal("0.35")
    tier3_fixed_penalty: Decimal = Decimal("1000.00")
    quantity_surcharge_rate: Decimal = Decimal("0.001")
    description: str = ""
    symbols_override: Set[str] = field(default_factory=set)

    def to_dict(self) -> Dict[str, Any]:
        """Serialização para dicionário."""
        return {
            "asset_class": self.asset_class.value,
            "max_otr_volume": float(self.max_otr_volume),
            "max_otr_quantity": float(self.max_otr_quantity),
            "tier1_rate_per_order": float(self.tier1_rate_per_order),
            "tier2_rate_per_order": float(self.tier2_rate_per_order),
            "tier3_rate_per_order": float(self.tier3_rate_per_order),
            "tier3_fixed_penalty": float(self.tier3_fixed_penalty),
            "quantity_surcharge_rate": float(self.quantity_surcharge_rate),
            "description": self.description,
            "symbols_override": sorted(list(self.symbols_override)),
        }


@dataclass
class OtrCalculationResult:
    """Resultado analítico da apuração de OTR por algoritmo e instrumento."""

    algorithm_id: Optional[str]
    symbol: str
    asset_class: AssetClass
    algo_class: TradingAlgorithmClass
    total_new_orders: int
    total_replace_orders: int
    total_cancel_orders: int
    total_orders_inserted_amended: int
    executed_trades: int
    total_qty_offered: int
    executed_qty: int
    otr_volume: Decimal
    otr_quantity: Decimal
    max_otr_volume: Decimal
    max_otr_quantity: Decimal
    is_volume_violation: bool
    is_quantity_violation: bool
    excess_orders: int
    excess_quantity: int

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o resultado para formato legível/JSON."""
        return {
            "algorithm_id": self.algorithm_id,
            "symbol": self.symbol,
            "asset_class": self.asset_class.value,
            "algo_class": self.algo_class.value,
            "total_new_orders": self.total_new_orders,
            "total_replace_orders": self.total_replace_orders,
            "total_cancel_orders": self.total_cancel_orders,
            "total_orders_inserted_amended": self.total_orders_inserted_amended,
            "executed_trades": self.executed_trades,
            "total_qty_offered": self.total_qty_offered,
            "executed_qty": self.executed_qty,
            "otr_volume": float(self.otr_volume),
            "otr_quantity": float(self.otr_quantity),
            "max_otr_volume": float(self.max_otr_volume),
            "max_otr_quantity": float(self.max_otr_quantity),
            "is_volume_violation": self.is_volume_violation,
            "is_quantity_violation": self.is_quantity_violation,
            "excess_orders": self.excess_orders,
            "excess_quantity": self.excess_quantity,
        }


@dataclass
class OtrTariffPenalty:
    """Demonstrativo de cálculo de sobretaxa e penalidade de OTR B3."""

    algorithm_id: Optional[str]
    symbol: str
    asset_class: AssetClass
    otr_volume: Decimal
    max_otr_volume: Decimal
    excess_orders: int
    excess_quantity: int
    penalty_tier: PenaltyTier
    volume_penalty_fee: Decimal
    quantity_surcharge_fee: Decimal
    fixed_penalty_fee: Decimal
    total_penalty_brl: Decimal
    details: str

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o demonstrativo de penalidade para formato legível/JSON."""
        return {
            "algorithm_id": self.algorithm_id,
            "symbol": self.symbol,
            "asset_class": self.asset_class.value,
            "otr_volume": float(self.otr_volume),
            "max_otr_volume": float(self.max_otr_volume),
            "excess_orders": self.excess_orders,
            "excess_quantity": self.excess_quantity,
            "penalty_tier": self.penalty_tier.value,
            "volume_penalty_fee": float(self.volume_penalty_fee),
            "quantity_surcharge_fee": float(self.quantity_surcharge_fee),
            "fixed_penalty_fee": float(self.fixed_penalty_fee),
            "total_penalty_brl": float(self.total_penalty_brl),
            "details": self.details,
        }


@dataclass
class HftGovernanceReport:
    """Dossiê consolidado de auditoria regulatória e enquadramento de OTR."""

    report_id: str
    generated_at: str
    total_events: int
    total_hft_events: int
    total_standard_algo_events: int
    total_manual_events: int
    tag_validation_errors: List[str]
    clock_sync_violations: List[Dict[str, Any]]
    otr_results: List[OtrCalculationResult]
    penalties: List[OtrTariffPenalty]
    total_penalties_brl: Decimal

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o relatório consolidado."""
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "total_events": self.total_events,
            "total_hft_events": self.total_hft_events,
            "total_standard_algo_events": self.total_standard_algo_events,
            "total_manual_events": self.total_manual_events,
            "tag_validation_errors": self.tag_validation_errors,
            "clock_sync_violations": self.clock_sync_violations,
            "otr_results": [r.to_dict() for r in self.otr_results],
            "penalties": [p.to_dict() for p in self.penalties],
            "total_penalties_brl": float(self.total_penalties_brl),
        }


# =====================================================================
# MOTOR PRINCIPAL DE GOVERNANÇA E FISCALIZAÇÃO
# =====================================================================

class HftAlgoGovernanceEngine:
    """Motor central de validação de tags FIX CVM 505, RTS 25 e apuração de OTR."""

    # Tickers de referência do Ibovespa (Large Caps / Blue Chips)
    IBOVESPA_LARGE_CAPS: Set[str] = {
        "PETR3", "PETR4", "VALE3", "ITUB4", "BBDC4", "B3SA3", "ABEV3",
        "BBAS3", "ELET3", "ELET6", "RENT3", "WEGE3", "BPAC11", "SUZB3",
        "JBSS3", "PRIO3", "RAIL3", "GGBR4", "CSAN3", "EMBR3",
    }

    # Prefixos de Futuros Financeiros B3
    FUTURES_PREFIXES: Tuple[str, ...] = ("DI1", "DOL", "WDO", "IND", "WIN", "BGI", "CCM", "ICF")

    def __init__(self) -> None:
        self.limits: Dict[AssetClass, OtrAssetLimit] = {}
        self.symbol_overrides: Dict[str, AssetClass] = {}
        self.events: List[AlgoOrderTag] = []
        self._init_default_limits()

    def _init_default_limits(self) -> None:
        """Inicializa os limites regulatórios padrão conforme a especificação FL-047."""
        # 1. Ações Ibovespa (Large Caps)
        self.register_asset_limit(
            OtrAssetLimit(
                asset_class=AssetClass.EQUITIES_LARGE_CAP,
                max_otr_volume=Decimal("150"),
                max_otr_quantity=Decimal("250"),
                tier1_rate_per_order=Decimal("0.05"),
                tier2_rate_per_order=Decimal("0.15"),
                tier3_rate_per_order=Decimal("0.35"),
                tier3_fixed_penalty=Decimal("1000.00"),
                quantity_surcharge_rate=Decimal("0.001"),
                description="Ações do Índice Ibovespa (Blue Chips e Alta Liquidez)",
                symbols_override=set(self.IBOVESPA_LARGE_CAPS),
            )
        )

        # 2. Small Caps (Ações fora do Ibovespa)
        self.register_asset_limit(
            OtrAssetLimit(
                asset_class=AssetClass.EQUITIES_SMALL_CAP,
                max_otr_volume=Decimal("300"),
                max_otr_quantity=Decimal("500"),
                tier1_rate_per_order=Decimal("0.05"),
                tier2_rate_per_order=Decimal("0.15"),
                tier3_rate_per_order=Decimal("0.35"),
                tier3_fixed_penalty=Decimal("1000.00"),
                quantity_surcharge_rate=Decimal("0.001"),
                description="Ações de Média e Pequena Capitalização (Fora do Ibovespa)",
            )
        )

        # 3. Futuros Financeiros (DI1, DOL, IND)
        self.register_asset_limit(
            OtrAssetLimit(
                asset_class=AssetClass.FUTURES_FINANCIAL,
                max_otr_volume=Decimal("100"),
                max_otr_quantity=Decimal("150"),
                tier1_rate_per_order=Decimal("0.05"),
                tier2_rate_per_order=Decimal("0.15"),
                tier3_rate_per_order=Decimal("0.35"),
                tier3_fixed_penalty=Decimal("1000.00"),
                quantity_surcharge_rate=Decimal("0.001"),
                description="Futuros Financeiros B3 (DI1, Câmbio DOL/WDO e Índice IND/WIN)",
            )
        )

        # 4. Opções Listadas (Ações e Futuros)
        self.register_asset_limit(
            OtrAssetLimit(
                asset_class=AssetClass.OPTIONS,
                max_otr_volume=Decimal("500"),
                max_otr_quantity=Decimal("1000"),
                tier1_rate_per_order=Decimal("0.05"),
                tier2_rate_per_order=Decimal("0.15"),
                tier3_rate_per_order=Decimal("0.35"),
                tier3_fixed_penalty=Decimal("1000.00"),
                quantity_surcharge_rate=Decimal("0.001"),
                description="Opções Listadas sobre Ações e Contratos Futuros",
            )
        )

    def register_asset_limit(self, limit: OtrAssetLimit) -> None:
        """Registra ou sobrescreve uma política de limites de OTR por classe de ativo."""
        self.limits[limit.asset_class] = limit
        for sym in limit.symbols_override:
            self.symbol_overrides[sym.upper()] = limit.asset_class

    def classify_asset(self, symbol: str) -> AssetClass:
        """Identifica a classe de ativo a partir do ticker do instrumento."""
        s = symbol.strip().upper()
        if s in self.symbol_overrides:
            return self.symbol_overrides[s]

        # Verifica opções de ações (ex: PETRB300, VALEA650, ITUBM250)
        # Padrão: 4 letras + 1 letra de mês de opção (A-X) + números
        if re.match(r"^[A-Z]{4}[A-X]\d+$", s):
            return AssetClass.OPTIONS

        # Verifica se é futuro financeiro / agro
        for prefix in self.FUTURES_PREFIXES:
            if s.startswith(prefix):
                return AssetClass.FUTURES_FINANCIAL

        # Verifica se é Large Cap cadastrada
        if s in self.IBOVESPA_LARGE_CAPS:
            return AssetClass.EQUITIES_LARGE_CAP

        # Se for ticker de ação padrão (ex: CASH3, MGLU3, LWSA3, POSI3)
        if re.match(r"^[A-Z]{4}\d{1,2}$", s):
            return AssetClass.EQUITIES_SMALL_CAP

        # Padrão de segurança: Small Cap
        return AssetClass.EQUITIES_SMALL_CAP

    def get_asset_limit(self, symbol: str) -> OtrAssetLimit:
        """Recupera o limite de OTR configurado para o símbolo especificado."""
        asset_class = self.classify_asset(symbol)
        if asset_class not in self.limits:
            raise KeyError(f"Classe de ativo '{asset_class}' não configurada no motor.")
        return self.limits[asset_class]

    # =================================================================
    # VALIDAÇÃO REGULATÓRIA DE TAGS E SINCRONIZAÇÃO RTS 25
    # =================================================================

    def validate_regulatory_tags(self, tag: AlgoOrderTag) -> List[str]:
        """Valida a presença e consistência das tags CVM 505 / RTS 25 / B3."""
        errors: List[str] = []

        # 1. Validação de ExecutingTrader (Tag 8015)
        if not tag.executing_trader or not tag.executing_trader.strip():
            errors.append(
                f"[Tag 8015] ExecutingTrader é obrigatório para identificação regulatória no evento {tag.cl_ord_id}."
            )

        # 2. Validação de CustOrderHandlingType (Tag 1031) vs Algo Class
        if tag.algo_class == TradingAlgorithmClass.HIGH_FREQUENCY_TRADING:
            if tag.cust_order_handling_type != CustOrderHandlingType.ALGORITHMIC:
                errors.append(
                    f"[Tag 1031] Ordem HFT {tag.cl_ord_id} deve conter CustOrderHandlingType='D', "
                    f"mas recebeu '{tag.cust_order_handling_type.value}'."
                )
            if not tag.trading_algorithm_id or not tag.trading_algorithm_id.strip():
                errors.append(
                    f"[Tag 57] Ordem HFT {tag.cl_ord_id} exige TradingAlgorithmID obrigatório e homologado."
                )

        elif tag.algo_class == TradingAlgorithmClass.ALGORITHMIC_STANDARD:
            if tag.cust_order_handling_type != CustOrderHandlingType.ALGORITHMIC:
                errors.append(
                    f"[Tag 1031] Ordem algorítmica convencional {tag.cl_ord_id} deve conter CustOrderHandlingType='D', "
                    f"mas recebeu '{tag.cust_order_handling_type.value}'."
                )
            if not tag.trading_algorithm_id or not tag.trading_algorithm_id.strip():
                errors.append(
                    f"[Tag 57] Ordem algorítmica {tag.cl_ord_id} exige TradingAlgorithmID homologado."
                )

        elif tag.algo_class == TradingAlgorithmClass.MANUAL_DISCRETIONARY:
            if tag.cust_order_handling_type != CustOrderHandlingType.DESK_MANUAL:
                errors.append(
                    f"[Tag 1031] Ordem manual {tag.cl_ord_id} deve conter CustOrderHandlingType='W', "
                    f"mas recebeu '{tag.cust_order_handling_type.value}'."
                )
            if tag.trading_algorithm_id:
                errors.append(
                    f"[Tag 57] Ordem manual {tag.cl_ord_id} não pode possuir TradingAlgorithmID preenchido "
                    f"('{tag.trading_algorithm_id}')."
                )

        # 3. Validação de OrderOriginationRole (Tag 1724)
        if tag.algo_class == TradingAlgorithmClass.HIGH_FREQUENCY_TRADING:
            if tag.order_origination_role not in (
                OrderOriginationRole.ALGORITHM_DEA,
                OrderOriginationRole.PRINCIPAL,
            ):
                errors.append(
                    f"[Tag 1724] Ordem HFT {tag.cl_ord_id} deve ter OrderOriginationRole=4 (Algorithm) "
                    f"ou 2 (Principal), mas recebeu {tag.order_origination_role.value}."
                )

        return errors

    def validate_clock_synchronization(self, tag: AlgoOrderTag) -> Tuple[bool, str]:
        """Verifica a conformidade do desvio de relógio temporal segundo RTS 25."""
        max_allowed = MAX_CLOCK_DRIFT_MICROS.get(tag.algo_class, 1_000_000)
        abs_drift = abs(tag.clock_drift_micros)

        if abs_drift > max_allowed:
            msg = (
                f"[RTS 25 Non-Compliance] Desvio de relógio ({abs_drift} µs) excede a tolerância máxima "
                f"permitida de {max_allowed} µs para a classe {tag.algo_class.value} no evento {tag.cl_ord_id}."
            )
            return False, msg

        return True, "Em conformidade com a tolerância de sincronização RTS 25."

    def add_order_event(self, event: AlgoOrderTag) -> None:
        """Adiciona um evento de ordem à trilha de governança do motor."""
        self.events.append(event)

    # =================================================================
    # CÁLCULO DE ORDER-TO-TRADE RATIO (OTR)
    # =================================================================

    def calculate_otr(
        self,
        algorithm_id: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> List[OtrCalculationResult]:
        """Calcula deterministicamente o OTR por volume e por quantidade.

        Fórmulas normativas B3:
        OTR_vol = Total_Ordens_Inseridas_Alteradas / max(1, Negocios_Executados)
        OTR_qty = Quantidade_Total_Ofertada / max(1, Quantidade_Executada)
        """
        # Agrupamento de eventos por (algorithm_id, symbol)
        grouped_events: Dict[Tuple[Optional[str], str], List[AlgoOrderTag]] = {}
        for ev in self.events:
            if algorithm_id is not None and ev.trading_algorithm_id != algorithm_id:
                continue
            if symbol is not None and ev.symbol.upper() != symbol.upper():
                continue

            key = (ev.trading_algorithm_id, ev.symbol.upper())
            grouped_events.setdefault(key, []).append(ev)

        results: List[OtrCalculationResult] = []

        for (algo_id, sym), ev_list in grouped_events.items():
            # Determina a classe predominante do algoritmo
            algo_classes = [e.algo_class for e in ev_list]
            primary_algo_class = max(set(algo_classes), key=algo_classes.count) if algo_classes else TradingAlgorithmClass.HIGH_FREQUENCY_TRADING

            asset_class = self.classify_asset(sym)
            limit = self.get_asset_limit(sym)

            new_count = 0
            replace_count = 0
            cancel_count = 0
            trade_count = 0

            qty_offered = 0
            qty_executed = 0

            for ev in ev_list:
                if ev.action_type == ActionType.NEW:
                    new_count += 1
                    qty_offered += ev.quantity
                elif ev.action_type == ActionType.REPLACE:
                    replace_count += 1
                    qty_offered += ev.quantity
                elif ev.action_type == ActionType.CANCEL:
                    cancel_count += 1
                elif ev.action_type == ActionType.TRADE:
                    trade_count += 1
                    qty_executed += ev.quantity

            total_inserted_amended = new_count + replace_count

            # Divisão com proteção contra zero (piso = 1 trade / 1 cota)
            effective_trades = max(1, trade_count)
            effective_qty_exec = max(1, qty_executed)

            otr_volume = (Decimal(total_inserted_amended) / Decimal(effective_trades)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            otr_quantity = (Decimal(qty_offered) / Decimal(effective_qty_exec)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            # Verificação de violação dos tetos
            is_vol_violation = otr_volume > limit.max_otr_volume
            is_qty_violation = otr_quantity > limit.max_otr_quantity

            # Quantificação dos excessos
            max_allowed_orders = int(limit.max_otr_volume * Decimal(effective_trades))
            excess_orders = max(0, total_inserted_amended - max_allowed_orders)

            max_allowed_qty = int(limit.max_otr_quantity * Decimal(effective_qty_exec))
            excess_qty = max(0, qty_offered - max_allowed_qty)

            results.append(
                OtrCalculationResult(
                    algorithm_id=algo_id,
                    symbol=sym,
                    asset_class=asset_class,
                    algo_class=primary_algo_class,
                    total_new_orders=new_count,
                    total_replace_orders=replace_count,
                    total_cancel_orders=cancel_count,
                    total_orders_inserted_amended=total_inserted_amended,
                    executed_trades=trade_count,
                    total_qty_offered=qty_offered,
                    executed_qty=qty_executed,
                    otr_volume=otr_volume,
                    otr_quantity=otr_quantity,
                    max_otr_volume=limit.max_otr_volume,
                    max_otr_quantity=limit.max_otr_quantity,
                    is_volume_violation=is_vol_violation,
                    is_quantity_violation=is_qty_violation,
                    excess_orders=excess_orders,
                    excess_quantity=excess_qty,
                )
            )

        return results

    # =================================================================
    # APURAÇÃO DE TARIFAS E PENALIDADES DE OTR DA B3
    # =================================================================

    def calculate_penalties(
        self,
        calculation_results: Optional[List[OtrCalculationResult]] = None,
    ) -> List[OtrTariffPenalty]:
        """Apurar taxas punitivas e multas de OTR da B3 sobre ordens excedentes."""
        if calculation_results is None:
            calculation_results = self.calculate_otr()

        penalties: List[OtrTariffPenalty] = []

        for res in calculation_results:
            limit = self.get_asset_limit(res.symbol)

            # Caso não haja violação de volume nem de quantidade, sem cobrança
            if not res.is_volume_violation and not res.is_quantity_violation:
                penalties.append(
                    OtrTariffPenalty(
                        algorithm_id=res.algorithm_id,
                        symbol=res.symbol,
                        asset_class=res.asset_class,
                        otr_volume=res.otr_volume,
                        max_otr_volume=res.max_otr_volume,
                        excess_orders=0,
                        excess_quantity=0,
                        penalty_tier=PenaltyTier.NONE,
                        volume_penalty_fee=Decimal("0.00"),
                        quantity_surcharge_fee=Decimal("0.00"),
                        fixed_penalty_fee=Decimal("0.00"),
                        total_penalty_brl=Decimal("0.00"),
                        details="Operação em conformidade com os tetos normativos de OTR B3.",
                    )
                )
                continue

            # Cálculo da Razão de Excesso (Excess Ratio Er)
            excess_ratio = res.otr_volume / limit.max_otr_volume if limit.max_otr_volume > 0 else Decimal("0")

            tier = PenaltyTier.NONE
            order_rate = Decimal("0.00")
            fixed_fee = Decimal("0.00")

            if excess_ratio > Decimal("2.5"):
                tier = PenaltyTier.TIER_3
                order_rate = limit.tier3_rate_per_order
                fixed_fee = limit.tier3_fixed_penalty
            elif excess_ratio > Decimal("1.5"):
                tier = PenaltyTier.TIER_2
                order_rate = limit.tier2_rate_per_order
            elif excess_ratio > Decimal("1.0"):
                tier = PenaltyTier.TIER_1
                order_rate = limit.tier1_rate_per_order
            else:
                # Violação apenas de quantidade
                tier = PenaltyTier.TIER_1

            vol_penalty = (Decimal(res.excess_orders) * order_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            qty_surcharge = (Decimal(res.excess_quantity) * limit.quantity_surcharge_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            total_penalty = vol_penalty + qty_surcharge + fixed_fee

            details = (
                f"Infração OTR: Volume ratio {res.otr_volume} (limite {res.max_otr_volume}), "
                f"Razão de Excesso Er={excess_ratio:.2f}x ({tier.value}). "
                f"{res.excess_orders} ordens excedentes tarifadas a R$ {order_rate}/ordem."
            )
            if res.excess_quantity > 0:
                details += (
                    f" Surcharge de quantidade: {res.excess_quantity} cotas excedentes "
                    f"tarifadas a R$ {limit.quantity_surcharge_rate}/cota (R$ {qty_surcharge})."
                )
            if fixed_fee > 0:
                details += f" Multa administrativa fixa de R$ {fixed_fee} aplicada para infração crítica."

            penalties.append(
                OtrTariffPenalty(
                    algorithm_id=res.algorithm_id,
                    symbol=res.symbol,
                    asset_class=res.asset_class,
                    otr_volume=res.otr_volume,
                    max_otr_volume=res.max_otr_volume,
                    excess_orders=res.excess_orders,
                    excess_quantity=res.excess_quantity,
                    penalty_tier=tier,
                    volume_penalty_fee=vol_penalty,
                    quantity_surcharge_fee=qty_surcharge,
                    fixed_penalty_fee=fixed_fee,
                    total_penalty_brl=total_penalty,
                    details=details,
                )
            )

        return penalties

    # =================================================================
    # DOSSIÊ CONSOLIDADO DE GOVERNANÇA E AUDITORIA
    # =================================================================

    def generate_governance_report(self, report_id: str = "HFT-GOV-REPORT-001") -> HftGovernanceReport:
        """Gera o dossiê formal de auditoria e conformidade regulatória para BSM/B3."""
        tag_errors: List[str] = []
        clock_violations: List[Dict[str, Any]] = []

        hft_count = 0
        std_algo_count = 0
        manual_count = 0

        for ev in self.events:
            if ev.algo_class == TradingAlgorithmClass.HIGH_FREQUENCY_TRADING:
                hft_count += 1
            elif ev.algo_class == TradingAlgorithmClass.ALGORITHMIC_STANDARD:
                std_algo_count += 1
            elif ev.algo_class == TradingAlgorithmClass.MANUAL_DISCRETIONARY:
                manual_count += 1

            # Validação de tags
            errs = self.validate_regulatory_tags(ev)
            tag_errors.extend(errs)

            # Validação de relógio
            is_sync, sync_msg = self.validate_clock_synchronization(ev)
            if not is_sync:
                clock_violations.append({
                    "cl_ord_id": ev.cl_ord_id,
                    "symbol": ev.symbol,
                    "algo_class": ev.algo_class.value,
                    "clock_drift_micros": ev.clock_drift_micros,
                    "max_allowed_micros": MAX_CLOCK_DRIFT_MICROS.get(ev.algo_class, 1_000_000),
                    "message": sync_msg,
                })

        otr_results = self.calculate_otr()
        penalties = self.calculate_penalties(otr_results)
        total_penalties = sum((p.total_penalty_brl for p in penalties), Decimal("0.00"))

        return HftGovernanceReport(
            report_id=report_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            total_events=len(self.events),
            total_hft_events=hft_count,
            total_standard_algo_events=std_algo_count,
            total_manual_events=manual_count,
            tag_validation_errors=tag_errors,
            clock_sync_violations=clock_violations,
            otr_results=otr_results,
            penalties=penalties,
            total_penalties_brl=total_penalties,
        )

    # =================================================================
    # CARREGAMENTO E EXPORTAÇÃO JSON
    # =================================================================

    @classmethod
    def load_from_json(cls, source: Union[str, Path]) -> HftAlgoGovernanceEngine:
        """Carrega e instancia o motor a partir de arquivo JSON ou string JSON."""
        if isinstance(source, Path) or (isinstance(source, str) and (Path(source).exists() or "/" in source or "\\" in source)):
            path = Path(source)
            if not path.is_file():
                raise FileNotFoundError(f"Arquivo de fixtures não encontrado: {source}")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = json.loads(source)

        engine = cls()

        # O JSON pode conter uma lista de eventos diretamente ou um dict com "orders"
        order_list = data.get("orders", data) if isinstance(data, dict) else data
        if not isinstance(order_list, list):
            raise ValueError("O conteúdo JSON deve ser uma lista de ordens ou objeto com a chave 'orders'.")

        for item in order_list:
            ts_str = item["timestamp"]
            # Suporta ISO com microssegundos e sufixo Z
            if ts_str.endswith("Z"):
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                ts = datetime.fromisoformat(ts_str)

            price_val = Decimal(str(item["price"])) if item.get("price") is not None else None

            tag = AlgoOrderTag(
                cl_ord_id=item["cl_ord_id"],
                symbol=item["symbol"],
                action_type=ActionType(item["action_type"]),
                quantity=int(item["quantity"]),
                price=price_val,
                timestamp=ts,
                cust_order_handling_type=CustOrderHandlingType(item["cust_order_handling_type"]),
                trading_algorithm_id=item.get("trading_algorithm_id"),
                order_origination_role=OrderOriginationRole(int(item.get("order_origination_role", 4))),
                executing_trader=item.get("executing_trader", ""),
                algo_class=TradingAlgorithmClass(item.get("algo_class", "HIGH_FREQUENCY_TRADING")),
                clock_drift_micros=int(item.get("clock_drift_micros", 0)),
                order_id=item.get("order_id"),
                execution_id=item.get("execution_id"),
                account_id=item.get("account_id"),
                client_id=item.get("client_id"),
                metadata=item.get("metadata", {}),
            )
            engine.add_order_event(tag)

        return engine

    def to_dict(self) -> Dict[str, Any]:
        """Exporta o estado atual do motor para dicionário serializável."""
        return {
            "limits": {ac.value: limit.to_dict() for ac, limit in self.limits.items()},
            "events_count": len(self.events),
            "events": [e.to_dict() for e in self.events],
        }
