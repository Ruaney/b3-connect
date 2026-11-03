"""Módulo de Risco Pré-Trading e Gestão de Limites Operacionais PUMA B3 (FL-028).

Implementa controles em memória de ultra-baixa latência (sub-milissegundo) para
validação mandatória de ofertas antes do ingresso no PUMA Matching Engine da B3.

Funcionalidades:
- Prevenção de Fat-Finger: Lote máximo por ordem e valor financeiro máximo por ordem.
- Túneis de Preço (Price Bands):
    * Túnel Estático: Rejeição mandatória (FAT_FINGER_PRICE_OUT_OF_BANDS).
    * Túnel Dinâmico: Acionamento de Leilão de Volatilidade (PRICE_TRIGGERED_DYNAMIC_AUCTION).
- Limites de Exposição Intradiária por Conta / Mesa:
    * Limite Bruto (Gross Exposure): Compras + Vendas executadas e ativas.
    * Limite Líquido (Net Exposure): Risco direcional comprado vs. vendido.
    * Trava de Venda a Descoberto (Short-Selling Check): Restrição de venda descoberta sem custódia.
- Compatibilidade nativa com mensagens FIX (FixMessage) e binárias SBE (NewOrderSingleBinary).
- Zero dependências externas (Python Standard Library pura).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# Importações condicionais para interoperabilidade de tipos com FIX e SBE
try:
    from src.puma_fix import (
        FixMessage,
        FixSide,
        TAG_ACCOUNT,
        TAG_CL_ORD_ID,
        TAG_ORDER_QTY,
        TAG_PRICE,
        TAG_SIDE,
        TAG_SYMBOL,
    )
except ImportError:
    FixMessage = None  # type: ignore
    FixSide = None  # type: ignore
    TAG_ACCOUNT = 1
    TAG_CL_ORD_ID = 11
    TAG_ORDER_QTY = 38
    TAG_PRICE = 44
    TAG_SIDE = 54
    TAG_SYMBOL = 55

try:
    from src.puma_sbe import NewOrderSingleBinary, Side as SbeSide
except ImportError:
    NewOrderSingleBinary = None  # type: ignore
    SbeSide = None  # type: ignore


# ==============================================================================
# 1. Enumerações Canônicas de Decisão e Motivo de Risco
# ==============================================================================

class RiskValidationStatus(str, Enum):
    """Status da decisão do motor de risco pré-trading."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    AUCTION_TRIGGERED = "AUCTION_TRIGGERED"


class RiskReasonCode(str, Enum):
    """Códigos padronizados de motivo de conformidade e violação de risco B3."""

    # Sucesso
    APPROVED = "APPROVED"

    # Prevenção de Fat-Finger e Túneis de Preço
    FAT_FINGER_PRICE_OUT_OF_BANDS = "FAT_FINGER_PRICE_OUT_OF_BANDS"
    PRICE_TRIGGERED_DYNAMIC_AUCTION = "PRICE_TRIGGERED_DYNAMIC_AUCTION"
    MAX_ORDER_QUANTITY_EXCEEDED = "MAX_ORDER_QUANTITY_EXCEEDED"
    MAX_ORDER_VALUE_EXCEEDED = "MAX_ORDER_VALUE_EXCEEDED"

    # Limites Intradiários e Patrimoniais
    GROSS_EXPOSURE_LIMIT_EXCEEDED = "GROSS_EXPOSURE_LIMIT_EXCEEDED"
    NET_EXPOSURE_LIMIT_EXCEEDED = "NET_EXPOSURE_LIMIT_EXCEEDED"
    SHORT_SELLING_RESTRICTED = "SHORT_SELLING_RESTRICTED"
    SHORT_SELLING_LIMIT_EXCEEDED = "SHORT_SELLING_LIMIT_EXCEEDED"

    # Parâmetros e Configuração
    PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
    PROFILE_INACTIVE = "PROFILE_INACTIVE"
    PRICE_BAND_NOT_FOUND = "PRICE_BAND_NOT_FOUND"
    INVALID_ORDER_PARAMETERS = "INVALID_ORDER_PARAMETERS"


# ==============================================================================
# 2. Modelos de Configuração e Estado
# ==============================================================================

@dataclass(frozen=True)
class PriceBandConfig:
    """Configuração dos túneis de preço estático e dinâmico para um instrumento B3.

    Campos:
        symbol: Código do ativo/instrumento (ex.: PETR4, VALE3).
        reference_price: Preço de referência para o túnel estático (ajuste/fechamento anterior).
        static_lower_limit: Preço mínimo permitido pelo túnel estático (rejeição se menor).
        static_upper_limit: Preço máximo permitido pelo túnel estático (rejeição se maior).
        dynamic_reference_price: Preço de referência dinâmico (último negócio - LTP).
        dynamic_lower_limit: Preço mínimo do túnel dinâmico antes de acionar leilão.
        dynamic_upper_limit: Preço máximo do túnel dinâmico antes de acionar leilão.
        auction_on_dynamic_breach: Se True, rompimento do dinâmico gera AUCTION_TRIGGERED.
    """

    symbol: str
    reference_price: float
    static_lower_limit: float
    static_upper_limit: float
    dynamic_reference_price: float
    dynamic_lower_limit: float
    dynamic_upper_limit: float
    auction_on_dynamic_breach: bool = True

    @classmethod
    def from_percentages(
        cls,
        symbol: str,
        reference_price: float,
        static_pct: float = 0.10,
        dynamic_pct: float = 0.03,
        dynamic_reference_price: Optional[float] = None,
        auction_on_dynamic_breach: bool = True,
    ) -> PriceBandConfig:
        """Constrói configuração de túnel a partir de percentuais sobre o preço de referência."""
        dyn_ref = (
            float(dynamic_reference_price)
            if dynamic_reference_price is not None
            else float(reference_price)
        )
        ref = float(reference_price)
        return cls(
            symbol=symbol.upper(),
            reference_price=ref,
            static_lower_limit=round(ref * (1.0 - static_pct), 4),
            static_upper_limit=round(ref * (1.0 + static_pct), 4),
            dynamic_reference_price=dyn_ref,
            dynamic_lower_limit=round(dyn_ref * (1.0 - dynamic_pct), 4),
            dynamic_upper_limit=round(dyn_ref * (1.0 + dynamic_pct), 4),
            auction_on_dynamic_breach=auction_on_dynamic_breach,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PriceBandConfig:
        """Desserializa configuração a partir de dicionário."""
        return cls(
            symbol=str(data["symbol"]).upper(),
            reference_price=float(data["reference_price"]),
            static_lower_limit=float(data["static_lower_limit"]),
            static_upper_limit=float(data["static_upper_limit"]),
            dynamic_reference_price=float(
                data.get("dynamic_reference_price", data["reference_price"])
            ),
            dynamic_lower_limit=float(data["dynamic_lower_limit"]),
            dynamic_upper_limit=float(data["dynamic_upper_limit"]),
            auction_on_dynamic_breach=bool(
                data.get("auction_on_dynamic_breach", True)
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa configuração para dicionário."""
        return asdict(self)


@dataclass(frozen=True)
class RiskLimitProfile:
    """Perfil de limites operacionais de risco pré-trading por Conta ou Mesa.

    Campos:
        account: Identificador unívoco da conta/comitente.
        name: Descrição formal da conta/mesa.
        desk_id: Identificador da mesa de operações ou algoritmo.
        participant_id: Código da corretora / participante B3.
        max_order_qty: Quantidade máxima de contratos/ações permitida por ordem.
        max_order_value: Valor financeiro máximo permitido por ordem (R$).
        max_gross_exposure: Teto financeiro de exposição bruta intradiária acumulada (R$).
        max_net_exposure: Teto financeiro de exposição líquida intradiária acumulada (R$).
        allow_short_selling: Autorização para venda a descoberto (short-selling).
        max_short_position: Limite máximo de quantidade vendida a descoberto (se autorizado).
        active: Indicador se o perfil está apto a negociar.
    """

    account: str
    name: str
    desk_id: Optional[str] = None
    participant_id: Optional[int] = None
    max_order_qty: int = 10000
    max_order_value: float = 1_000_000.0
    max_gross_exposure: float = 10_000_000.0
    max_net_exposure: float = 5_000_000.0
    allow_short_selling: bool = False
    max_short_position: int = 0
    active: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RiskLimitProfile:
        """Instancia perfil a partir de dicionário."""
        return cls(
            account=str(data["account"]),
            name=str(data.get("name", "")),
            desk_id=data.get("desk_id"),
            participant_id=(
                int(data["participant_id"])
                if data.get("participant_id") is not None
                else None
            ),
            max_order_qty=int(data.get("max_order_qty", 10000)),
            max_order_value=float(data.get("max_order_value", 1_000_000.0)),
            max_gross_exposure=float(data.get("max_gross_exposure", 10_000_000.0)),
            max_net_exposure=float(data.get("max_net_exposure", 5_000_000.0)),
            allow_short_selling=bool(data.get("allow_short_selling", False)),
            max_short_position=int(data.get("max_short_position", 0)),
            active=bool(data.get("active", True)),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa perfil para dicionário."""
        return asdict(self)


@dataclass
class AccountExposureState:
    """Estado acumulado de posições, ordens ativas e exposição de uma conta."""

    account: str
    positions: Dict[str, int] = field(default_factory=dict)
    cum_buy_value: float = 0.0
    cum_sell_value: float = 0.0
    pending_buy_value: float = 0.0
    pending_sell_value: float = 0.0
    pending_buy_qty: Dict[str, int] = field(default_factory=dict)
    pending_sell_qty: Dict[str, int] = field(default_factory=dict)
    working_orders: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def gross_exposure(self) -> float:
        """Exposição bruta total: compras e vendas executadas + ordens ativas pendentes."""
        return (
            self.cum_buy_value
            + self.cum_sell_value
            + self.pending_buy_value
            + self.pending_sell_value
        )

    @property
    def net_exposure(self) -> float:
        """Exposição líquida direcional: delta comprado vs. vendido."""
        return (
            (self.cum_buy_value - self.cum_sell_value)
            + (self.pending_buy_value - self.pending_sell_value)
        )

    def get_position(self, symbol: str) -> int:
        """Retorna a posição em custódia para o ativo."""
        return self.positions.get(symbol.upper(), 0)

    def get_available_sell_qty(self, symbol: str) -> int:
        """Retorna a quantidade disponível para novas vendas cobertas."""
        sym = symbol.upper()
        pos = self.positions.get(sym, 0)
        pending = self.pending_sell_qty.get(sym, 0)
        return pos - pending


@dataclass(frozen=True)
class RiskValidationResult:
    """Resultado determinístico da validação de risco pré-trading."""

    status: RiskValidationStatus
    is_approved: bool
    reason_code: RiskReasonCode
    message: str
    account: str
    symbol: str
    side: str
    order_qty: int
    price: float
    order_value: float
    cl_ord_id: Optional[str] = None
    latency_micros: float = 0.0
    current_gross_exposure: float = 0.0
    current_net_exposure: float = 0.0
    current_position: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o resultado para formato estruturado."""
        d = asdict(self)
        d["status"] = self.status.value
        d["reason_code"] = self.reason_code.value
        return d


# ==============================================================================
# 3. Motor Central de Risco Pré-Trading (PreTradeRiskEngine)
# ==============================================================================

class PreTradeRiskEngine:
    """Motor em sub-milissegundo para validação pré-ordem no Entrypoint PUMA.

    Aplica filtros determinísticos em hot-path sem I/O ou dependências externas:
    1. Sanity check de preço, quantidade e lado.
    2. Existência e conformidade do perfil da conta.
    3. Túnel de preço estático (rejeição) e dinâmico (acionamento de leilão).
    4. Limites Fat-Finger de lote máximo e valor financeiro máximo por ordem.
    5. Trava de venda a descoberto (Short-Selling).
    6. Exposição intradiária acumulada (bruta e líquida).
    """

    def __init__(self) -> None:
        self.profiles: Dict[str, RiskLimitProfile] = {}
        self.price_bands: Dict[str, PriceBandConfig] = {}
        self.account_states: Dict[str, AccountExposureState] = {}
        self.security_id_map: Dict[int, str] = {}

    # --------------------------------------------------------------------------
    # Gestão de Configuração e Cadastro
    # --------------------------------------------------------------------------

    def add_profile(self, profile: RiskLimitProfile) -> None:
        """Cadastra ou substitui perfil de risco para uma conta."""
        self.profiles[str(profile.account)] = profile
        if str(profile.account) not in self.account_states:
            self.account_states[str(profile.account)] = AccountExposureState(
                account=str(profile.account)
            )

    def get_profile(self, account: str) -> Optional[RiskLimitProfile]:
        """Recupera o perfil de risco da conta."""
        return self.profiles.get(str(account))

    def add_price_band(self, band: PriceBandConfig) -> None:
        """Cadastra ou atualiza túneis de preço para um instrumento."""
        self.price_bands[band.symbol.upper()] = band

    def get_price_band(self, symbol: str) -> Optional[PriceBandConfig]:
        """Recupera a configuração do túnel de preço de um ativo."""
        return self.price_bands.get(symbol.upper())

    def register_security_mapping(self, security_id: int, symbol: str) -> None:
        """Mapeia SecurityID numérico (SBE) para símbolo ticker textual (FIX)."""
        self.security_id_map[security_id] = symbol.upper()

    def set_custody_position(self, account: str, symbol: str, quantity: int) -> None:
        """Define a posição física inicial em custódia de um comitente."""
        state = self.get_account_state(account)
        state.positions[symbol.upper()] = int(quantity)

    def get_account_state(self, account: str) -> AccountExposureState:
        """Obtém ou instancia o estado acumulado de posições de uma conta."""
        acc_str = str(account)
        if acc_str not in self.account_states:
            self.account_states[acc_str] = AccountExposureState(account=acc_str)
        return self.account_states[acc_str]

    def reset_intraday(self, account: Optional[str] = None) -> None:
        """Reseta contadores intradiários para início de pregão."""
        if account:
            acc_str = str(account)
            if acc_str in self.account_states:
                pos = dict(self.account_states[acc_str].positions)
                self.account_states[acc_str] = AccountExposureState(
                    account=acc_str, positions=pos
                )
        else:
            for acc_str, state in list(self.account_states.items()):
                pos = dict(state.positions)
                self.account_states[acc_str] = AccountExposureState(
                    account=acc_str, positions=pos
                )

    # --------------------------------------------------------------------------
    # Carregamento de Fixtures JSON
    # --------------------------------------------------------------------------

    def load_profiles_json(self, path: Union[str, Path]) -> None:
        """Carrega perfis de risco e túneis de preço a partir de fixture JSON."""
        file_path = Path(path)
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for p_data in data.get("profiles", []):
            profile = RiskLimitProfile.from_dict(p_data)
            self.add_profile(profile)
            # Carrega inventário inicial de custódia se presente
            for sym, qty in p_data.get("custody_inventory", {}).items():
                self.set_custody_position(profile.account, sym, qty)

        for b_data in data.get("price_bands", []):
            band = PriceBandConfig.from_dict(b_data)
            self.add_price_band(band)

        for sec_id_str, sym in data.get("security_id_map", {}).items():
            try:
                self.register_security_mapping(int(sec_id_str), sym)
            except ValueError:
                pass

    # --------------------------------------------------------------------------
    # Normalização de Parâmetros
    # --------------------------------------------------------------------------

    @staticmethod
    def _normalize_side(side: Union[str, int, Any]) -> str:
        """Normaliza o lado da ordem para '1' (Buy) ou '2' (Sell)."""
        if hasattr(side, "value"):
            s_val = str(side.value)
        else:
            s_val = str(side).strip()

        if s_val in ("1", "BUY", "C", "COMPRA"):
            return "1"
        if s_val in ("2", "SELL", "V", "VENDA"):
            return "2"
        return s_val

    # --------------------------------------------------------------------------
    # Motor de Validação em Sub-Milissegundo
    # --------------------------------------------------------------------------

    def validate_order(
        self,
        account: str,
        symbol: str,
        side: Union[str, int, Any],
        order_qty: int,
        price: float,
        cl_ord_id: Optional[str] = None,
        auto_reserve: bool = True,
    ) -> RiskValidationResult:
        """Executa a bateria completa de filtros pré-trading da B3 em sub-milissegundo."""
        start_ns = time.perf_counter_ns()
        acc_str = str(account)
        sym_str = str(symbol).upper()
        norm_side = self._normalize_side(side)
        qty = int(order_qty)
        px = float(price)
        order_val = qty * px
        state = self.get_account_state(acc_str)
        curr_pos = state.get_position(sym_str)

        # 1. Validação de Sanidade dos Parâmetros
        if qty <= 0 or px <= 0.0 or norm_side not in ("1", "2"):
            elapsed_micros = (time.perf_counter_ns() - start_ns) / 1000.0
            return RiskValidationResult(
                status=RiskValidationStatus.REJECTED,
                is_approved=False,
                reason_code=RiskReasonCode.INVALID_ORDER_PARAMETERS,
                message=f"Parâmetros inválidos: Qty={qty}, Price={px}, Side={norm_side}",
                account=acc_str,
                symbol=sym_str,
                side=norm_side,
                order_qty=qty,
                price=px,
                order_value=order_val,
                cl_ord_id=cl_ord_id,
                latency_micros=elapsed_micros,
                current_gross_exposure=state.gross_exposure,
                current_net_exposure=state.net_exposure,
                current_position=curr_pos,
            )

        # 2. Validação do Perfil de Risco da Conta
        profile = self.get_profile(acc_str)
        if not profile:
            elapsed_micros = (time.perf_counter_ns() - start_ns) / 1000.0
            return RiskValidationResult(
                status=RiskValidationStatus.REJECTED,
                is_approved=False,
                reason_code=RiskReasonCode.PROFILE_NOT_FOUND,
                message=f"Perfil de limites operacionais não encontrado para conta {acc_str}",
                account=acc_str,
                symbol=sym_str,
                side=norm_side,
                order_qty=qty,
                price=px,
                order_value=order_val,
                cl_ord_id=cl_ord_id,
                latency_micros=elapsed_micros,
                current_gross_exposure=state.gross_exposure,
                current_net_exposure=state.net_exposure,
                current_position=curr_pos,
            )

        if not profile.active:
            elapsed_micros = (time.perf_counter_ns() - start_ns) / 1000.0
            return RiskValidationResult(
                status=RiskValidationStatus.REJECTED,
                is_approved=False,
                reason_code=RiskReasonCode.PROFILE_INACTIVE,
                message=f"Perfil de negociação suspenso ou inativo para a conta {acc_str}",
                account=acc_str,
                symbol=sym_str,
                side=norm_side,
                order_qty=qty,
                price=px,
                order_value=order_val,
                cl_ord_id=cl_ord_id,
                latency_micros=elapsed_micros,
                current_gross_exposure=state.gross_exposure,
                current_net_exposure=state.net_exposure,
                current_position=curr_pos,
            )

        # 3. Túneis de Preço (Price Bands)
        band = self.get_price_band(sym_str)
        if band:
            # 3.1 Túnel Estático (Rejeição Mandatória / Fat-Finger)
            if px < band.static_lower_limit or px > band.static_upper_limit:
                elapsed_micros = (time.perf_counter_ns() - start_ns) / 1000.0
                return RiskValidationResult(
                    status=RiskValidationStatus.REJECTED,
                    is_approved=False,
                    reason_code=RiskReasonCode.FAT_FINGER_PRICE_OUT_OF_BANDS,
                    message=(
                        f"Preço R$ {px:.2f} fora do túnel estático "
                        f"[{band.static_lower_limit:.2f}, {band.static_upper_limit:.2f}] "
                        f"para {sym_str}"
                    ),
                    account=acc_str,
                    symbol=sym_str,
                    side=norm_side,
                    order_qty=qty,
                    price=px,
                    order_value=order_val,
                    cl_ord_id=cl_ord_id,
                    latency_micros=elapsed_micros,
                    current_gross_exposure=state.gross_exposure,
                    current_net_exposure=state.net_exposure,
                    current_position=curr_pos,
                    details={"band": band.to_dict()},
                )

            # 3.2 Túnel Dinâmico (Leilão de Volatilidade ou Rejeição)
            if px < band.dynamic_lower_limit or px > band.dynamic_upper_limit:
                elapsed_micros = (time.perf_counter_ns() - start_ns) / 1000.0
                if band.auction_on_dynamic_breach:
                    return RiskValidationResult(
                        status=RiskValidationStatus.AUCTION_TRIGGERED,
                        is_approved=False,
                        reason_code=RiskReasonCode.PRICE_TRIGGERED_DYNAMIC_AUCTION,
                        message=(
                            f"Preço R$ {px:.2f} fora do túnel dinâmico "
                            f"[{band.dynamic_lower_limit:.2f}, {band.dynamic_upper_limit:.2f}], "
                            f"acionando leilão de volatilidade B3"
                        ),
                        account=acc_str,
                        symbol=sym_str,
                        side=norm_side,
                        order_qty=qty,
                        price=px,
                        order_value=order_val,
                        cl_ord_id=cl_ord_id,
                        latency_micros=elapsed_micros,
                        current_gross_exposure=state.gross_exposure,
                        current_net_exposure=state.net_exposure,
                        current_position=curr_pos,
                        details={"band": band.to_dict()},
                    )
                else:
                    return RiskValidationResult(
                        status=RiskValidationStatus.REJECTED,
                        is_approved=False,
                        reason_code=RiskReasonCode.PRICE_TRIGGERED_DYNAMIC_AUCTION,
                        message=(
                            f"Preço R$ {px:.2f} fora do túnel dinâmico "
                            f"[{band.dynamic_lower_limit:.2f}, {band.dynamic_upper_limit:.2f}]"
                        ),
                        account=acc_str,
                        symbol=sym_str,
                        side=norm_side,
                        order_qty=qty,
                        price=px,
                        order_value=order_val,
                        cl_ord_id=cl_ord_id,
                        latency_micros=elapsed_micros,
                        current_gross_exposure=state.gross_exposure,
                        current_net_exposure=state.net_exposure,
                        current_position=curr_pos,
                        details={"band": band.to_dict()},
                    )

        # 4. Prevenção de Fat-Finger: Lote Máximo por Ordem
        if qty > profile.max_order_qty:
            elapsed_micros = (time.perf_counter_ns() - start_ns) / 1000.0
            return RiskValidationResult(
                status=RiskValidationStatus.REJECTED,
                is_approved=False,
                reason_code=RiskReasonCode.MAX_ORDER_QUANTITY_EXCEEDED,
                message=(
                    f"Quantidade {qty} excede o lote máximo de {profile.max_order_qty} "
                    f"autorizado para a conta {acc_str}"
                ),
                account=acc_str,
                symbol=sym_str,
                side=norm_side,
                order_qty=qty,
                price=px,
                order_value=order_val,
                cl_ord_id=cl_ord_id,
                latency_micros=elapsed_micros,
                current_gross_exposure=state.gross_exposure,
                current_net_exposure=state.net_exposure,
                current_position=curr_pos,
                details={"max_order_qty": profile.max_order_qty},
            )

        # 5. Prevenção de Fat-Finger: Valor Financeiro Máximo por Ordem
        if order_val > profile.max_order_value:
            elapsed_micros = (time.perf_counter_ns() - start_ns) / 1000.0
            return RiskValidationResult(
                status=RiskValidationStatus.REJECTED,
                is_approved=False,
                reason_code=RiskReasonCode.MAX_ORDER_VALUE_EXCEEDED,
                message=(
                    f"Valor da ordem R$ {order_val:,.2f} excede o limite financeiro "
                    f"de R$ {profile.max_order_value:,.2f} por ordem"
                ),
                account=acc_str,
                symbol=sym_str,
                side=norm_side,
                order_qty=qty,
                price=px,
                order_value=order_val,
                cl_ord_id=cl_ord_id,
                latency_micros=elapsed_micros,
                current_gross_exposure=state.gross_exposure,
                current_net_exposure=state.net_exposure,
                current_position=curr_pos,
                details={"max_order_value": profile.max_order_value},
            )

        # 6. Controle de Venda a Descoberto (Short-Selling Check)
        if norm_side == "2":  # Sell
            avail_sell = state.get_available_sell_qty(sym_str)
            if qty > avail_sell:
                if not profile.allow_short_selling:
                    elapsed_micros = (time.perf_counter_ns() - start_ns) / 1000.0
                    return RiskValidationResult(
                        status=RiskValidationStatus.REJECTED,
                        is_approved=False,
                        reason_code=RiskReasonCode.SHORT_SELLING_RESTRICTED,
                        message=(
                            f"Venda a descoberto não autorizada para comitente {acc_str}. "
                            f"Disponível: {avail_sell}, Solicitado: {qty}"
                        ),
                        account=acc_str,
                        symbol=sym_str,
                        side=norm_side,
                        order_qty=qty,
                        price=px,
                        order_value=order_val,
                        cl_ord_id=cl_ord_id,
                        latency_micros=elapsed_micros,
                        current_gross_exposure=state.gross_exposure,
                        current_net_exposure=state.net_exposure,
                        current_position=curr_pos,
                        details={
                            "available_sell_qty": avail_sell,
                            "allow_short_selling": profile.allow_short_selling,
                        },
                    )
                # Conta institucional com short autorizado: verificar teto de aluguel/descoberto
                projected_short = max(0, (qty - max(0, avail_sell)))
                if (
                    profile.max_short_position > 0
                    and projected_short > profile.max_short_position
                ):
                    elapsed_micros = (time.perf_counter_ns() - start_ns) / 1000.0
                    return RiskValidationResult(
                        status=RiskValidationStatus.REJECTED,
                        is_approved=False,
                        reason_code=RiskReasonCode.SHORT_SELLING_LIMIT_EXCEEDED,
                        message=(
                            f"Posição a descoberto projetada {projected_short} excede "
                            f"limite de {profile.max_short_position} para conta {acc_str}"
                        ),
                        account=acc_str,
                        symbol=sym_str,
                        side=norm_side,
                        order_qty=qty,
                        price=px,
                        order_value=order_val,
                        cl_ord_id=cl_ord_id,
                        latency_micros=elapsed_micros,
                        current_gross_exposure=state.gross_exposure,
                        current_net_exposure=state.net_exposure,
                        current_position=curr_pos,
                        details={"max_short_position": profile.max_short_position},
                    )

        # 7. Limite de Exposição Financeira Bruta Acumulada (Gross Exposure)
        proj_gross = state.gross_exposure + order_val
        if proj_gross > profile.max_gross_exposure:
            elapsed_micros = (time.perf_counter_ns() - start_ns) / 1000.0
            return RiskValidationResult(
                status=RiskValidationStatus.REJECTED,
                is_approved=False,
                reason_code=RiskReasonCode.GROSS_EXPOSURE_LIMIT_EXCEEDED,
                message=(
                    f"Exposição bruta projetada R$ {proj_gross:,.2f} excede "
                    f"limite intradiário de R$ {profile.max_gross_exposure:,.2f}"
                ),
                account=acc_str,
                symbol=sym_str,
                side=norm_side,
                order_qty=qty,
                price=px,
                order_value=order_val,
                cl_ord_id=cl_ord_id,
                latency_micros=elapsed_micros,
                current_gross_exposure=state.gross_exposure,
                current_net_exposure=state.net_exposure,
                current_position=curr_pos,
                details={
                    "projected_gross": proj_gross,
                    "max_gross_exposure": profile.max_gross_exposure,
                },
            )

        # 8. Limite de Exposição Financeira Líquida Acumulada (Net Exposure)
        if norm_side == "1":
            proj_net = state.net_exposure + order_val
        else:
            proj_net = state.net_exposure - order_val

        if abs(proj_net) > profile.max_net_exposure:
            elapsed_micros = (time.perf_counter_ns() - start_ns) / 1000.0
            return RiskValidationResult(
                status=RiskValidationStatus.REJECTED,
                is_approved=False,
                reason_code=RiskReasonCode.NET_EXPOSURE_LIMIT_EXCEEDED,
                message=(
                    f"Exposição líquida projetada R$ {abs(proj_net):,.2f} excede "
                    f"limite intradiário de R$ {profile.max_net_exposure:,.2f}"
                ),
                account=acc_str,
                symbol=sym_str,
                side=norm_side,
                order_qty=qty,
                price=px,
                order_value=order_val,
                cl_ord_id=cl_ord_id,
                latency_micros=elapsed_micros,
                current_gross_exposure=state.gross_exposure,
                current_net_exposure=state.net_exposure,
                current_position=curr_pos,
                details={
                    "projected_net": proj_net,
                    "max_net_exposure": profile.max_net_exposure,
                },
            )

        # 9. Aprovação Conclusiva e Reserva de Exposição
        if auto_reserve:
            order_id = cl_ord_id or f"ORD_{time.time_ns()}"
            if norm_side == "1":
                state.pending_buy_value += order_val
                state.pending_buy_qty[sym_str] = (
                    state.pending_buy_qty.get(sym_str, 0) + qty
                )
            else:
                state.pending_sell_value += order_val
                state.pending_sell_qty[sym_str] = (
                    state.pending_sell_qty.get(sym_str, 0) + qty
                )

            state.working_orders[order_id] = {
                "symbol": sym_str,
                "side": norm_side,
                "qty": qty,
                "price": px,
                "order_value": order_val,
                "leaves_qty": qty,
            }

        elapsed_micros = (time.perf_counter_ns() - start_ns) / 1000.0
        return RiskValidationResult(
            status=RiskValidationStatus.APPROVED,
            is_approved=True,
            reason_code=RiskReasonCode.APPROVED,
            message="Ordem aprovada em todos os filtros de risco pré-trading",
            account=acc_str,
            symbol=sym_str,
            side=norm_side,
            order_qty=qty,
            price=px,
            order_value=order_val,
            cl_ord_id=cl_ord_id,
            latency_micros=elapsed_micros,
            current_gross_exposure=state.gross_exposure,
            current_net_exposure=state.net_exposure,
            current_position=curr_pos,
            details={"reserved": auto_reserve},
        )

    # --------------------------------------------------------------------------
    # Interoperabilidade com Protocolo FIX
    # --------------------------------------------------------------------------

    def validate_fix_order(
        self,
        msg: Any,
        auto_reserve: bool = True,
    ) -> RiskValidationResult:
        """Extrai parâmetros de uma mensagem NewOrderSingle (FIX 4.4/5.0) e valida risco."""
        account = msg.get(TAG_ACCOUNT, "")
        symbol = msg.get(TAG_SYMBOL, "")
        side = msg.get(TAG_SIDE, "1")
        cl_ord_id = msg.get(TAG_CL_ORD_ID, None)

        try:
            order_qty = int(msg.get(TAG_ORDER_QTY, "0"))
        except (ValueError, TypeError):
            order_qty = 0

        try:
            price = float(msg.get(TAG_PRICE, "0.0"))
        except (ValueError, TypeError):
            price = 0.0

        return self.validate_order(
            account=account,
            symbol=symbol,
            side=side,
            order_qty=order_qty,
            price=price,
            cl_ord_id=cl_ord_id,
            auto_reserve=auto_reserve,
        )

    # --------------------------------------------------------------------------
    # Interoperabilidade com Protocolo Binário SBE
    # --------------------------------------------------------------------------

    def validate_sbe_order(
        self,
        msg: Any,
        symbol_map: Optional[Dict[int, str]] = None,
        auto_reserve: bool = True,
    ) -> RiskValidationResult:
        """Extrai parâmetros de NewOrderSingleBinary (SBE Little-Endian) e valida risco."""
        account = str(getattr(msg, "account", 0))
        sec_id = getattr(msg, "security_id", 0)
        active_map = symbol_map or self.security_id_map
        symbol = active_map.get(sec_id, str(sec_id))
        side = getattr(msg, "side", 1)
        order_qty = getattr(msg, "order_qty", 0)
        price = getattr(msg, "price", 0.0)
        cl_ord_id = str(getattr(msg, "cl_ord_id", ""))

        return self.validate_order(
            account=account,
            symbol=symbol,
            side=side,
            order_qty=order_qty,
            price=price,
            cl_ord_id=cl_ord_id,
            auto_reserve=auto_reserve,
        )

    # --------------------------------------------------------------------------
    # Atualizações de Ciclo de Vida da Ordem (Execuções, Cancelamentos e Rejeições)
    # --------------------------------------------------------------------------

    def record_fill(
        self,
        account: str,
        symbol: str,
        side: Union[str, int, Any],
        exec_qty: int,
        exec_price: float,
        cl_ord_id: Optional[str] = None,
    ) -> None:
        """Registra a execução parcial ou total de uma oferta, atualizando o saldo real."""
        state = self.get_account_state(account)
        sym = symbol.upper()
        norm_side = self._normalize_side(side)
        fill_val = exec_qty * exec_price

        # Atualiza posições físicas e valores executados
        if norm_side == "1":
            state.positions[sym] = state.positions.get(sym, 0) + exec_qty
            state.cum_buy_value += fill_val
        else:
            state.positions[sym] = state.positions.get(sym, 0) - exec_qty
            state.cum_sell_value += fill_val

        # Baixa da reserva pendente de ordens ativas
        if cl_ord_id and cl_ord_id in state.working_orders:
            wo = state.working_orders[cl_ord_id]
            reserved_px = wo["price"]
            released_val = exec_qty * reserved_px

            if norm_side == "1":
                state.pending_buy_value = max(0.0, state.pending_buy_value - released_val)
                state.pending_buy_qty[sym] = max(
                    0, state.pending_buy_qty.get(sym, 0) - exec_qty
                )
            else:
                state.pending_sell_value = max(
                    0.0, state.pending_sell_value - released_val
                )
                state.pending_sell_qty[sym] = max(
                    0, state.pending_sell_qty.get(sym, 0) - exec_qty
                )

            wo["leaves_qty"] = max(0, wo["leaves_qty"] - exec_qty)
            if wo["leaves_qty"] == 0:
                del state.working_orders[cl_ord_id]
        else:
            # Baixa genérica quando não há rastreio estrito de ClOrdID
            if norm_side == "1":
                state.pending_buy_value = max(0.0, state.pending_buy_value - fill_val)
                state.pending_buy_qty[sym] = max(
                    0, state.pending_buy_qty.get(sym, 0) - exec_qty
                )
            else:
                state.pending_sell_value = max(0.0, state.pending_sell_value - fill_val)
                state.pending_sell_qty[sym] = max(
                    0, state.pending_sell_qty.get(sym, 0) - exec_qty
                )

    def record_cancel(self, account: str, cl_ord_id: str) -> None:
        """Libera a reserva de risco de uma ordem cancelada antes da execução."""
        state = self.get_account_state(account)
        if cl_ord_id in state.working_orders:
            wo = state.working_orders.pop(cl_ord_id)
            leaves = wo["leaves_qty"]
            reserved_val = leaves * wo["price"]
            sym = wo["symbol"]

            if wo["side"] == "1":
                state.pending_buy_value = max(
                    0.0, state.pending_buy_value - reserved_val
                )
                state.pending_buy_qty[sym] = max(
                    0, state.pending_buy_qty.get(sym, 0) - leaves
                )
            else:
                state.pending_sell_value = max(
                    0.0, state.pending_sell_value - reserved_val
                )
                state.pending_sell_qty[sym] = max(
                    0, state.pending_sell_qty.get(sym, 0) - leaves
                )

    def record_reject(self, account: str, cl_ord_id: str) -> None:
        """Libera a reserva de risco de uma ordem rejeitada pelo Matching Engine."""
        self.record_cancel(account, cl_ord_id)

    # --------------------------------------------------------------------------
    # Medição de Desempenho e Auditoria
    # --------------------------------------------------------------------------

    def benchmark_latency(self, iterations: int = 1000) -> Dict[str, Any]:
        """Executa bateria de benchmarking para comprovar latência em sub-milissegundo."""
        # Garante perfil e túnel de teste
        acc = "__BENCHMARK_ACC__"
        sym = "__BENCHMARK_SYM__"
        self.add_profile(
            RiskLimitProfile(
                account=acc,
                name="Benchmark Account",
                max_order_qty=1_000_000,
                max_order_value=100_000_000.0,
                max_gross_exposure=1_000_000_000.0,
                max_net_exposure=500_000_000.0,
                allow_short_selling=True,
            )
        )
        self.add_price_band(
            PriceBandConfig.from_percentages(sym, 100.0, static_pct=0.10, dynamic_pct=0.03)
        )

        latencies_micros: List[float] = []
        t0 = time.perf_counter()
        for i in range(iterations):
            res = self.validate_order(
                account=acc,
                symbol=sym,
                side="1",
                order_qty=100,
                price=100.0,
                cl_ord_id=f"BNCH_{i}",
                auto_reserve=False,
            )
            latencies_micros.append(res.latency_micros)
        total_time_ms = (time.perf_counter() - t0) * 1000.0

        latencies_sorted = sorted(latencies_micros)
        p50 = latencies_sorted[int(len(latencies_sorted) * 0.50)]
        p95 = latencies_sorted[int(len(latencies_sorted) * 0.95)]
        p99 = latencies_sorted[int(len(latencies_sorted) * 0.99)]
        mean_micros = sum(latencies_micros) / len(latencies_micros)

        return {
            "iterations": iterations,
            "total_time_ms": round(total_time_ms, 3),
            "mean_micros": round(mean_micros, 3),
            "p50_micros": round(p50, 3),
            "p95_micros": round(p95, 3),
            "p99_micros": round(p99, 3),
            "sub_millisecond": bool(p99 < 1000.0),
        }
