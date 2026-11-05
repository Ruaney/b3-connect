"""Módulo de Futuros de Crédito de Descarbonização (CBIO / CBI) no PUMA Trading System e Câmara B3 (FL-086).

Implementa a especificação regulatória, de negociação eletrônica e de compensação/liquidação
para os Contratos Futuros de CBIO (código B3: CBI) conforme:
- Política Nacional de Biocombustíveis (RenovaBio — Lei Federal nº 13.576/2017 e Decreto nº 9.888/2019);
- Manual de Procedimentos Operacionais de Negociação da B3 (PUMA Trading System);
- Manual de Risco e Garantias da Câmara B3 (CORE — Closeout Risk Evaluation);
- Manual de Compensação e Liquidação de Derivativos Listados da Câmara B3.

Pilares de Implementação:
1. Contrato Futuro Padronizado CBI:
   - Objeto de negociação: Crédito de Descarbonização (CBIO), onde 1 CBIO = 1 tCO2e evitada;
   - Tamanho do contrato / multiplicador: 100 CBIOs por contrato;
   - Cotação: R$ por CBIO com 2 casas decimais (tick size: R$ 0,01 por CBIO / R$ 1,00 por contrato);
   - Vencimento mensal nos códigos padronizados B3 (F, G, H, J, K, M, N, Q, U, V, X, Z).
2. PUMA Trading System & Túneis Dinâmicos de Negociação:
   - Túnel de Rejeição (Hard Limit / Pre-Trade Collar): Rejeição estrita de ofertas com preço fora
     do intervalo máximo permitido em relação ao preço de referência (default +/- 10,0%);
   - Túnel de Leilão (Auction Collar): Interrupção da negociação contínua e acionamento automático
     de Leilão de Volatilidade quando uma oferta executável extrapola o túnel dinâmico (default +/- 4,0%);
   - Matching determinístico por prioridade Preço-Tempo (FIFO) e cálculo de preço de equilíbrio em leilão.
3. Gestão de Risco e Margem de Garantia da Câmara B3 (CORE):
   - Apuração de margem de risco requerida por contrato e por posição líquida direcional;
   - Aceitação de garantias elegíveis (Dinheiro em BRL com haircut 0% e Títulos Públicos Federais
     com haircut regulatório de 5%);
   - Emissão e monitoramento de Chamadas de Margem (Margin Calls) em caso de insuficiência patrimonial.
4. Ajuste Diário de Perdas e Ganhos (Mark-to-Market) em D+1:
   - Apuração diária de variação financeira com base no Preço de Ajuste do Dia (PA_t);
   - Invariante formal de Soma-Zero estrita: Somatório(Ajuste_Compradores) + Somatório(Ajuste_Vendedores) == 0,00;
   - Liquidação financeira creditada/debitada em D+1 nas contas de liquidação da câmara.
5. Liquidação Final Exclusivamente Financeira no Vencimento:
   - Baseada no preço oficial de fechamento/média ponderada do mercado spot de CBIO no Balcão B3;
   - Sem entrega física de títulos; encerramento integral das posições e extinção do Open Interest;
   - Invariante formal de Open Interest: Posições_Compradas == Posições_Vendidas == Open_Interest_Total.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from src.invariants import (
    check,
    InvariantViolationError,
    assert_cbio_futures_daily_adjustment_zero_sum,
    assert_cbio_futures_open_interest_parity,
    assert_cbio_futures_final_settlement_zero_sum,
)


# ---------------------------------------------------------------------------
# Constantes de Domínio B3 e RenovaBio
# ---------------------------------------------------------------------------

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
CBI_COMMODITY_CODE = "CBI"
CBI_CONTRACT_MULTIPLIER = 100  # 100 CBIOs = 100 tCO2e por contrato futuro
CBI_TICK_SIZE = Decimal("0.01")  # R$ 0,01 por CBIO -> R$ 1,00 por contrato
DEFAULT_HARD_LIMIT_PCT = Decimal("10.00")  # Túnel de rejeição (+/- 10%)
DEFAULT_AUCTION_COLLAR_PCT = Decimal("4.00")  # Túnel de leilão de volatilidade (+/- 4%)
DEFAULT_MARGIN_RATE = Decimal("0.12")  # 12% do nocional para risco CORE
MIN_MARGIN_PER_CONTRACT = Decimal("1000.00")  # Piso de margem por contrato R$ 1.000,00
DEFAULT_SOVEREIGN_BOND_HAIRCUT = Decimal("0.05")  # 5% de haircut regulatório em TPF

VALID_EXPIRATION_MONTH_CODES = frozenset(
    {"F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z"}
)
CBI_TICKER_REGEX = re.compile(r"^CBI([FGHJKMNQUVXZ])(\d{2})$")


def round_cents(value: Decimal) -> Decimal:
    """Arredonda valor financeiro para 2 casas decimais (half-up bancário B3)."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Exceções Customizadas do Domínio
# ---------------------------------------------------------------------------


class CbioFuturesError(Exception):
    """Exceção base para erros do motor de futuros de CBIO (CBI) da B3."""


class CbioFuturesValidationError(CbioFuturesError):
    """Erro de validação cadastral, ticker ou parâmetros do contrato/ordem."""


class PumaCollarViolationError(CbioFuturesError):
    """Erro emitido pelo PUMA quando uma oferta viola os limites rígidos do túnel de rejeição."""


class MarginDeficitError(CbioFuturesError):
    """Erro de margem insuficiente para abertura de novas posições em aberto."""


class OrderNotFoundError(CbioFuturesError):
    """Erro de busca de ordem inexistente no livro de ofertas."""


class InvalidTradingPhaseError(CbioFuturesError):
    """Erro de transição ou operação incompatível com a fase atual do mercado."""


class ExpiredContractError(CbioFuturesError):
    """Tentativa de negociar ou manipular contrato já expirado/liquidado."""


# ---------------------------------------------------------------------------
# Enums de Negociação e Compensação B3
# ---------------------------------------------------------------------------


class OrderSide(str, Enum):
    """Lado da oferta no PUMA Trading System."""

    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> OrderSide:
        """Retorna o lado oposto da oferta."""
        return OrderSide.SELL if self == OrderSide.BUY else OrderSide.BUY


class OrderType(str, Enum):
    """Tipo de ordem aceita no mercado de futuros CBI."""

    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    """Ciclo de vida de uma ordem no PUMA Trading System."""

    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class TradingPhase(str, Enum):
    """Fases operacionais do instrumento no PUMA Trading System."""

    PRE_OPEN = "PRE_OPEN"
    CONTINUOUS = "CONTINUOUS"
    VOLATILITY_AUCTION = "VOLATILITY_AUCTION"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class PositionSide(str, Enum):
    """Lado da posição em aberto mantida na Câmara B3."""

    LONG = "LONG"
    SHORT = "SHORT"


class CollateralType(str, Enum):
    """Tipo de garantia aceita na Câmara B3 para cobertura de margem."""

    CASH_BRL = "CASH_BRL"
    SOVEREIGN_BOND = "SOVEREIGN_BOND"


# ---------------------------------------------------------------------------
# Modelagem de Entidades do Contrato e Mercado
# ---------------------------------------------------------------------------


@dataclass
class CbioContract:
    """Especificação formal do Contrato Futuro de Crédito de Descarbonização (CBI)."""

    ticker: str
    expiration_date: date
    reference_price: Decimal
    settlement_price: Optional[Decimal] = None
    spot_final_price: Optional[Decimal] = None
    hard_limit_pct: Decimal = DEFAULT_HARD_LIMIT_PCT
    auction_collar_pct: Decimal = DEFAULT_AUCTION_COLLAR_PCT
    contract_multiplier: int = CBI_CONTRACT_MULTIPLIER
    tick_size: Decimal = CBI_TICK_SIZE
    trading_phase: TradingPhase = TradingPhase.CONTINUOUS

    def __post_init__(self) -> None:
        match = CBI_TICKER_REGEX.match(self.ticker)
        if not match:
            raise CbioFuturesValidationError(
                f"Ticker inválido para Futuro de CBIO: '{self.ticker}'. Formato esperado: CBI<Mês><Ano>, ex: CBIZ24."
            )
        month_code = match.group(1)
        if month_code not in VALID_EXPIRATION_MONTH_CODES:
            raise CbioFuturesValidationError(
                f"Código de mês inválido no ticker: '{month_code}'."
            )
        self.reference_price = round_cents(Decimal(str(self.reference_price)))
        if self.reference_price <= Decimal("0.00"):
            raise CbioFuturesValidationError("Preço de referência deve ser estritamente positivo.")
        self.hard_limit_pct = Decimal(str(self.hard_limit_pct))
        self.auction_collar_pct = Decimal(str(self.auction_collar_pct))
        if self.hard_limit_pct <= self.auction_collar_pct:
            raise CbioFuturesValidationError("Túnel de rejeição (hard limit) deve ser maior que o túnel de leilão.")

    def hard_limits(self) -> Tuple[Decimal, Decimal]:
        """Calcula a faixa de preços do Túnel de Rejeição [Preço Mínimo, Preço Máximo]."""
        delta = round_cents(self.reference_price * (self.hard_limit_pct / Decimal("100")))
        min_p = round_cents(self.reference_price - delta)
        max_p = round_cents(self.reference_price + delta)
        return max(Decimal("0.01"), min_p), max_p

    def auction_collars(self) -> Tuple[Decimal, Decimal]:
        """Calcula a faixa de preços do Túnel de Leilão de Volatilidade [Preço Mínimo, Preço Máximo]."""
        delta = round_cents(self.reference_price * (self.auction_collar_pct / Decimal("100")))
        min_p = round_cents(self.reference_price - delta)
        max_p = round_cents(self.reference_price + delta)
        return max(Decimal("0.01"), min_p), max_p

    def notional_per_contract(self, price: Decimal) -> Decimal:
        """Calcula o valor nocional de 1 contrato futuro CBI (100 CBIOs * Preço)."""
        return round_cents(price * Decimal(self.contract_multiplier))

    def to_dict(self) -> Dict[str, Any]:
        """Exporta contrato para dicionário serializável."""
        return {
            "ticker": self.ticker,
            "expiration_date": self.expiration_date.isoformat(),
            "reference_price": str(self.reference_price),
            "settlement_price": str(self.settlement_price) if self.settlement_price is not None else None,
            "spot_final_price": str(self.spot_final_price) if self.spot_final_price is not None else None,
            "hard_limit_pct": str(self.hard_limit_pct),
            "auction_collar_pct": str(self.auction_collar_pct),
            "contract_multiplier": self.contract_multiplier,
            "tick_size": str(self.tick_size),
            "trading_phase": self.trading_phase.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CbioContract:
        """Reconstitui contrato a partir de dicionário."""
        exp_raw = data["expiration_date"]
        exp_date = date.fromisoformat(exp_raw) if isinstance(exp_raw, str) else exp_raw
        return cls(
            ticker=data["ticker"],
            expiration_date=exp_date,
            reference_price=Decimal(str(data["reference_price"])),
            settlement_price=Decimal(str(data["settlement_price"])) if data.get("settlement_price") else None,
            spot_final_price=Decimal(str(data["spot_final_price"])) if data.get("spot_final_price") else None,
            hard_limit_pct=Decimal(str(data.get("hard_limit_pct", DEFAULT_HARD_LIMIT_PCT))),
            auction_collar_pct=Decimal(str(data.get("auction_collar_pct", DEFAULT_AUCTION_COLLAR_PCT))),
            contract_multiplier=int(data.get("contract_multiplier", CBI_CONTRACT_MULTIPLIER)),
            tick_size=Decimal(str(data.get("tick_size", CBI_TICK_SIZE))),
            trading_phase=TradingPhase(data.get("trading_phase", TradingPhase.CONTINUOUS.value)),
        )


@dataclass
class Order:
    """Representação de uma Ordem submetida ao PUMA Trading System."""

    order_id: str
    ticker: str
    account_id: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: Optional[Decimal] = None
    filled_quantity: int = 0
    status: OrderStatus = OrderStatus.NEW
    created_at: datetime = field(default_factory=datetime.now)
    rejection_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise CbioFuturesValidationError("Quantidade da ordem deve ser estritamente positiva.")
        if self.order_type == OrderType.LIMIT:
            if self.price is None:
                raise CbioFuturesValidationError("Ordem do tipo LIMIT exige preço obrigatório.")
            self.price = round_cents(Decimal(str(self.price)))
            if self.price <= Decimal("0.00"):
                raise CbioFuturesValidationError("Preço da ordem limitada deve ser estritamente positivo.")
        elif self.order_type == OrderType.MARKET:
            self.price = None

    @property
    def remaining_quantity(self) -> int:
        """Quantidade de contratos pendente de execução."""
        return self.quantity - self.filled_quantity

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a ordem para JSON/dict."""
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "account_id": self.account_id,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "price": str(self.price) if self.price is not None else None,
            "filled_quantity": self.filled_quantity,
            "remaining_quantity": self.remaining_quantity,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Order:
        """Deserializa ordem a partir de dicionário."""
        ts_raw = data.get("created_at")
        ts = datetime.fromisoformat(ts_raw) if isinstance(ts_raw, str) else (ts_raw or datetime.now())
        return cls(
            order_id=data["order_id"],
            ticker=data["ticker"],
            account_id=data["account_id"],
            side=OrderSide(data["side"]),
            order_type=OrderType(data["order_type"]),
            quantity=int(data["quantity"]),
            price=Decimal(str(data["price"])) if data.get("price") is not None else None,
            filled_quantity=int(data.get("filled_quantity", 0)),
            status=OrderStatus(data.get("status", OrderStatus.NEW.value)),
            created_at=ts,
            rejection_reason=data.get("rejection_reason"),
        )


@dataclass
class Trade:
    """Negócio executado no PUMA Trading System com contratos futuros de CBIO."""

    trade_id: str
    ticker: str
    buy_order_id: str
    sell_order_id: str
    buyer_account_id: str
    seller_account_id: str
    price: Decimal
    quantity: int
    executed_at: datetime = field(default_factory=datetime.now)
    in_auction: bool = False
    contract_multiplier: int = CBI_CONTRACT_MULTIPLIER

    def __post_init__(self) -> None:
        self.price = round_cents(Decimal(str(self.price)))
        if self.quantity <= 0:
            raise CbioFuturesValidationError("Quantidade negociada deve ser estritamente positiva.")

    @property
    def notional_value(self) -> Decimal:
        """Valor financeiro total do trade (Preço * 100 * Qtd)."""
        return round_cents(self.price * Decimal(self.contract_multiplier) * Decimal(self.quantity))

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o trade."""
        return {
            "trade_id": self.trade_id,
            "ticker": self.ticker,
            "buy_order_id": self.buy_order_id,
            "sell_order_id": self.sell_order_id,
            "buyer_account_id": self.buyer_account_id,
            "seller_account_id": self.seller_account_id,
            "price": str(self.price),
            "quantity": self.quantity,
            "notional_value": str(self.notional_value),
            "executed_at": self.executed_at.isoformat(),
            "in_auction": self.in_auction,
            "contract_multiplier": self.contract_multiplier,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Trade:
        """Reconstitui trade a partir de dicionário."""
        ts_raw = data.get("executed_at")
        ts = datetime.fromisoformat(ts_raw) if isinstance(ts_raw, str) else (ts_raw or datetime.now())
        return cls(
            trade_id=data["trade_id"],
            ticker=data["ticker"],
            buy_order_id=data["buy_order_id"],
            sell_order_id=data["sell_order_id"],
            buyer_account_id=data["buyer_account_id"],
            seller_account_id=data["seller_account_id"],
            price=Decimal(str(data["price"])),
            quantity=int(data["quantity"]),
            executed_at=ts,
            in_auction=bool(data.get("in_auction", False)),
            contract_multiplier=int(data.get("contract_multiplier", CBI_CONTRACT_MULTIPLIER)),
        )


@dataclass
class Position:
    """Posição em aberto mantida por um participante na Câmara B3."""

    account_id: str
    ticker: str
    side: PositionSide
    quantity: int
    entry_price: Decimal
    current_reference_price: Decimal
    accumulated_pnl: Decimal = Decimal("0.00")
    status: str = "OPEN"  # OPEN, CLOSED, EXPIRED

    def __post_init__(self) -> None:
        self.entry_price = round_cents(Decimal(str(self.entry_price)))
        self.current_reference_price = round_cents(Decimal(str(self.current_reference_price)))
        self.accumulated_pnl = round_cents(Decimal(str(self.accumulated_pnl)))

    @property
    def notional_value(self) -> Decimal:
        """Valor nocional da posição (100 CBIOs * Preço * Quantidade)."""
        return round_cents(self.current_reference_price * Decimal(CBI_CONTRACT_MULTIPLIER) * Decimal(self.quantity))

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a posição."""
        return {
            "account_id": self.account_id,
            "ticker": self.ticker,
            "side": self.side.value,
            "quantity": self.quantity,
            "entry_price": str(self.entry_price),
            "current_reference_price": str(self.current_reference_price),
            "accumulated_pnl": str(self.accumulated_pnl),
            "notional_value": str(self.notional_value),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Position:
        """Reconstitui posição a partir de dicionário."""
        return cls(
            account_id=data["account_id"],
            ticker=data["ticker"],
            side=PositionSide(data["side"]),
            quantity=int(data["quantity"]),
            entry_price=Decimal(str(data["entry_price"])),
            current_reference_price=Decimal(str(data["current_reference_price"])),
            accumulated_pnl=Decimal(str(data.get("accumulated_pnl", "0.00"))),
            status=data.get("status", "OPEN"),
        )


@dataclass
class CollateralDeposit:
    """Depósito de colateral/garantia na Câmara B3."""

    deposit_id: str
    account_id: str
    collateral_type: CollateralType
    nominal_amount: Decimal
    haircut_pct: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.nominal_amount = round_cents(Decimal(str(self.nominal_amount)))
        self.haircut_pct = Decimal(str(self.haircut_pct))
        if self.nominal_amount < Decimal("0.00"):
            raise CbioFuturesValidationError("Valor nominal do depósito não pode ser negativo.")

    @property
    def effective_value(self) -> Decimal:
        """Valor efetivo após aplicação do haircut da câmara."""
        haircut_factor = Decimal("1.00") - (self.haircut_pct / Decimal("100"))
        return round_cents(self.nominal_amount * max(Decimal("0.00"), haircut_factor))

    def to_dict(self) -> Dict[str, Any]:
        """Serializa depósito de garantia."""
        return {
            "deposit_id": self.deposit_id,
            "account_id": self.account_id,
            "collateral_type": self.collateral_type.value,
            "nominal_amount": str(self.nominal_amount),
            "haircut_pct": str(self.haircut_pct),
            "effective_value": str(self.effective_value),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CollateralDeposit:
        """Reconstitui depósito de garantia."""
        return cls(
            deposit_id=data["deposit_id"],
            account_id=data["account_id"],
            collateral_type=CollateralType(data["collateral_type"]),
            nominal_amount=Decimal(str(data["nominal_amount"])),
            haircut_pct=Decimal(str(data.get("haircut_pct", "0.00"))),
        )


@dataclass
class MarginCall:
    """Registro de Chamada de Margem emitida pela Câmara B3."""

    call_id: str
    account_id: str
    call_date: date
    required_margin: Decimal
    total_collateral: Decimal
    deficit_amount: Decimal
    due_date: date
    resolved: bool = False

    def __post_init__(self) -> None:
        self.required_margin = round_cents(Decimal(str(self.required_margin)))
        self.total_collateral = round_cents(Decimal(str(self.total_collateral)))
        self.deficit_amount = round_cents(Decimal(str(self.deficit_amount)))

    def to_dict(self) -> Dict[str, Any]:
        """Serializa chamada de margem."""
        return {
            "call_id": self.call_id,
            "account_id": self.account_id,
            "call_date": self.call_date.isoformat(),
            "required_margin": str(self.required_margin),
            "total_collateral": str(self.total_collateral),
            "deficit_amount": str(self.deficit_amount),
            "due_date": self.due_date.isoformat(),
            "resolved": self.resolved,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MarginCall:
        """Reconstitui chamada de margem."""
        call_d = date.fromisoformat(data["call_date"]) if isinstance(data["call_date"], str) else data["call_date"]
        due_d = date.fromisoformat(data["due_date"]) if isinstance(data["due_date"], str) else data["due_date"]
        return cls(
            call_id=data["call_id"],
            account_id=data["account_id"],
            call_date=call_d,
            required_margin=Decimal(str(data["required_margin"])),
            total_collateral=Decimal(str(data["total_collateral"])),
            deficit_amount=Decimal(str(data["deficit_amount"])),
            due_date=due_d,
            resolved=bool(data.get("resolved", False)),
        )


@dataclass
class AccountMarginProfile:
    """Perfil de garantias, margens e saldo de liquidação da conta do participante."""

    account_id: str
    participant_name: str
    deposits: List[CollateralDeposit] = field(default_factory=list)
    cash_settlement_balance: Decimal = Decimal("0.00")
    required_margin: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.cash_settlement_balance = round_cents(Decimal(str(self.cash_settlement_balance)))
        self.required_margin = round_cents(Decimal(str(self.required_margin)))

    @property
    def total_collateral_value(self) -> Decimal:
        """Valor total de garantias disponíveis após haircuts."""
        return round_cents(sum((d.effective_value for d in self.deposits), Decimal("0.00")))

    @property
    def available_margin(self) -> Decimal:
        """Margem livre/disponível (Total de Colateral - Margem Requerida)."""
        return round_cents(self.total_collateral_value - self.required_margin)

    @property
    def has_margin_deficit(self) -> bool:
        """Verifica se a conta está em déficit de margem."""
        return self.available_margin < Decimal("0.00")

    def to_dict(self) -> Dict[str, Any]:
        """Serializa perfil de margem."""
        return {
            "account_id": self.account_id,
            "participant_name": self.participant_name,
            "deposits": [d.to_dict() for d in self.deposits],
            "total_collateral_value": str(self.total_collateral_value),
            "cash_settlement_balance": str(self.cash_settlement_balance),
            "required_margin": str(self.required_margin),
            "available_margin": str(self.available_margin),
            "has_margin_deficit": self.has_margin_deficit,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AccountMarginProfile:
        """Reconstitui perfil de margem."""
        deposits = [CollateralDeposit.from_dict(d) for d in data.get("deposits", [])]
        return cls(
            account_id=data["account_id"],
            participant_name=data["participant_name"],
            deposits=deposits,
            cash_settlement_balance=Decimal(str(data.get("cash_settlement_balance", "0.00"))),
            required_margin=Decimal(str(data.get("required_margin", "0.00"))),
        )


@dataclass
class DailyAdjustmentResult:
    """Resultado formal do Ajuste Diário (Mark-to-Market) de Contratos Futuros CBI."""

    settlement_date: date
    ticker: str
    settlement_price: Decimal
    account_adjustments: Dict[str, Decimal]
    long_total_adjustment: Decimal
    short_total_adjustment: Decimal
    net_clearing_adjustment: Decimal
    settlement_cycle: str = "D+1"
    details: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.settlement_price = round_cents(Decimal(str(self.settlement_price)))
        self.long_total_adjustment = round_cents(Decimal(str(self.long_total_adjustment)))
        self.short_total_adjustment = round_cents(Decimal(str(self.short_total_adjustment)))
        self.net_clearing_adjustment = round_cents(Decimal(str(self.net_clearing_adjustment)))

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o resultado do ajuste diário."""
        return {
            "settlement_date": self.settlement_date.isoformat(),
            "ticker": self.ticker,
            "settlement_price": str(self.settlement_price),
            "account_adjustments": {k: str(v) for k, v in self.account_adjustments.items()},
            "long_total_adjustment": str(self.long_total_adjustment),
            "short_total_adjustment": str(self.short_total_adjustment),
            "net_clearing_adjustment": str(self.net_clearing_adjustment),
            "settlement_cycle": self.settlement_cycle,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DailyAdjustmentResult:
        """Reconstitui resultado de ajuste diário."""
        s_date = date.fromisoformat(data["settlement_date"]) if isinstance(data["settlement_date"], str) else data["settlement_date"]
        return cls(
            settlement_date=s_date,
            ticker=data["ticker"],
            settlement_price=Decimal(str(data["settlement_price"])),
            account_adjustments={k: Decimal(str(v)) for k, v in data["account_adjustments"].items()},
            long_total_adjustment=Decimal(str(data["long_total_adjustment"])),
            short_total_adjustment=Decimal(str(data["short_total_adjustment"])),
            net_clearing_adjustment=Decimal(str(data["net_clearing_adjustment"])),
            settlement_cycle=data.get("settlement_cycle", "D+1"),
            details=data.get("details", []),
        )


@dataclass
class FinalCashSettlementResult:
    """Resultado formal da Liquidação Final Exclusivamente Financeira no Vencimento."""

    settlement_date: date
    ticker: str
    spot_final_price: Decimal
    previous_settlement_price: Decimal
    account_final_pnl: Dict[str, Decimal]
    long_total_pnl: Decimal
    short_total_pnl: Decimal
    net_clearing_pnl: Decimal
    closed_contracts: int
    settlement_cycle: str = "D+1"
    details: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.spot_final_price = round_cents(Decimal(str(self.spot_final_price)))
        self.previous_settlement_price = round_cents(Decimal(str(self.previous_settlement_price)))
        self.long_total_pnl = round_cents(Decimal(str(self.long_total_pnl)))
        self.short_total_pnl = round_cents(Decimal(str(self.short_total_pnl)))
        self.net_clearing_pnl = round_cents(Decimal(str(self.net_clearing_pnl)))

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o resultado de liquidação final financeira."""
        return {
            "settlement_date": self.settlement_date.isoformat(),
            "ticker": self.ticker,
            "spot_final_price": str(self.spot_final_price),
            "previous_settlement_price": str(self.previous_settlement_price),
            "account_final_pnl": {k: str(v) for k, v in self.account_final_pnl.items()},
            "long_total_pnl": str(self.long_total_pnl),
            "short_total_pnl": str(self.short_total_pnl),
            "net_clearing_pnl": str(self.net_clearing_pnl),
            "closed_contracts": self.closed_contracts,
            "settlement_cycle": self.settlement_cycle,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FinalCashSettlementResult:
        """Reconstitui resultado de liquidação final financeira."""
        s_date = date.fromisoformat(data["settlement_date"]) if isinstance(data["settlement_date"], str) else data["settlement_date"]
        return cls(
            settlement_date=s_date,
            ticker=data["ticker"],
            spot_final_price=Decimal(str(data["spot_final_price"])),
            previous_settlement_price=Decimal(str(data["previous_settlement_price"])),
            account_final_pnl={k: Decimal(str(v)) for k, v in data["account_final_pnl"].items()},
            long_total_pnl=Decimal(str(data["long_total_pnl"])),
            short_total_pnl=Decimal(str(data["short_total_pnl"])),
            net_clearing_pnl=Decimal(str(data["net_clearing_pnl"])),
            closed_contracts=int(data["closed_contracts"]),
            settlement_cycle=data.get("settlement_cycle", "D+1"),
            details=data.get("details", []),
        )


# ---------------------------------------------------------------------------
# Motor do PUMA Trading System e Câmara B3
# ---------------------------------------------------------------------------


class CbioFuturesEngine:
    """Motor Unificado de Negociação PUMA e Compensação/Liquidação da Câmara B3 para Futuros de CBIO."""

    def __init__(self) -> None:
        self.contracts: Dict[str, CbioContract] = {}
        self.orders: Dict[str, Order] = {}
        self.order_book_bids: Dict[str, List[Order]] = {}  # ticker -> lista de bids ordenadas
        self.order_book_asks: Dict[str, List[Order]] = {}  # ticker -> lista de asks ordenadas
        self.trades: List[Trade] = []
        self.margin_profiles: Dict[str, AccountMarginProfile] = {}
        self.positions: Dict[Tuple[str, str], Position] = {}  # (account_id, ticker) -> Position
        self.margin_calls: List[MarginCall] = []
        self.daily_adjustments: List[DailyAdjustmentResult] = []
        self.final_settlements: List[FinalCashSettlementResult] = []
        self.trade_counter: int = 0
        self.margin_call_counter: int = 0

    # -----------------------------------------------------------------------
    # Gestão de Contratos e Contas
    # -----------------------------------------------------------------------

    def register_contract(self, contract: CbioContract) -> None:
        """Cadastra um contrato futuro de CBIO no motor."""
        if contract.ticker in self.contracts:
            raise CbioFuturesValidationError(f"Contrato '{contract.ticker}' já está registrado.")
        self.contracts[contract.ticker] = contract
        self.order_book_bids[contract.ticker] = []
        self.order_book_asks[contract.ticker] = []

    def register_account(self, account_id: str, participant_name: str) -> AccountMarginProfile:
        """Registra uma conta de participante na câmara."""
        if account_id in self.margin_profiles:
            raise CbioFuturesValidationError(f"Conta '{account_id}' já está registrada.")
        profile = AccountMarginProfile(account_id=account_id, participant_name=participant_name)
        self.margin_profiles[account_id] = profile
        return profile

    def deposit_collateral(
        self,
        deposit_id: str,
        account_id: str,
        collateral_type: CollateralType,
        nominal_amount: Decimal,
        haircut_pct: Optional[Decimal] = None,
    ) -> CollateralDeposit:
        """Efetua depósito de colateral para cobertura de margem na câmara."""
        if account_id not in self.margin_profiles:
            raise CbioFuturesValidationError(f"Conta '{account_id}' não encontrada.")
        if haircut_pct is None:
            haircut_pct = (
                Decimal("0.00")
                if collateral_type == CollateralType.CASH_BRL
                else DEFAULT_SOVEREIGN_BOND_HAIRCUT * Decimal("100")
            )
        deposit = CollateralDeposit(
            deposit_id=deposit_id,
            account_id=account_id,
            collateral_type=collateral_type,
            nominal_amount=nominal_amount,
            haircut_pct=haircut_pct,
        )
        profile = self.margin_profiles[account_id]
        profile.deposits.append(deposit)
        self._check_margin_calls_resolution(account_id)
        return deposit

    # -----------------------------------------------------------------------
    # Negociação no PUMA Trading System & Túneis Dinâmicos
    # -----------------------------------------------------------------------

    def submit_order(self, order: Order) -> List[Trade]:
        """Submete uma ordem ao PUMA com validação de túneis e verificação pré-negociação de margem."""
        contract = self.contracts.get(order.ticker)
        if not contract:
            raise CbioFuturesValidationError(f"Contrato '{order.ticker}' não encontrado.")
        if contract.trading_phase == TradingPhase.EXPIRED:
            raise ExpiredContractError(f"Contrato '{order.ticker}' já expirou e foi liquidado financeiramente.")
        if order.account_id not in self.margin_profiles:
            raise CbioFuturesValidationError(f"Conta '{order.account_id}' não está cadastrada na câmara.")

        # 1. Validação de Túnel de Rejeição (Hard Limit Collar)
        min_hard, max_hard = contract.hard_limits()
        if order.order_type == OrderType.LIMIT and order.price is not None:
            if order.price < min_hard or order.price > max_hard:
                order.status = OrderStatus.REJECTED
                order.rejection_reason = (
                    f"Preço {order.price} violou o Túnel de Rejeição PUMA [{min_hard}, {max_hard}]. "
                    f"Preço de referência: {contract.reference_price}."
                )
                self.orders[order.order_id] = order
                raise PumaCollarViolationError(order.rejection_reason)

        # 2. Verificação Pré-Negociação de Margem (Pre-Trade Margin Check)
        self._pre_trade_margin_check(order, contract)

        self.orders[order.order_id] = order

        # 3. Tratamento de Fases de Negociação e Túnel de Leilão
        if contract.trading_phase == TradingPhase.VOLATILITY_AUCTION:
            # Em leilão, acumula a ordem no livro de leilão
            self._insert_order_into_book(order)
            return []

        # Em negociação contínua, valida se ordem cruza túnel de leilão (Auction Collar)
        min_auction, max_auction = contract.auction_collars()
        if self._crosses_auction_collar(order, contract, min_auction, max_auction):
            # Aciona Leilão de Volatilidade
            contract.trading_phase = TradingPhase.VOLATILITY_AUCTION
            self._insert_order_into_book(order)
            return []

        # 4. Matching Contínuo (Price-Time Priority)
        executed_trades = self._match_continuous(order, contract)
        return executed_trades

    def cancel_order(self, order_id: str) -> Order:
        """Cancela uma ordem ativa no livro de ofertas."""
        order = self.orders.get(order_id)
        if not order:
            raise OrderNotFoundError(f"Ordem '{order_id}' não encontrada.")
        if order.status not in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED):
            raise CbioFuturesValidationError(f"Ordem '{order_id}' não pode ser cancelada no status '{order.status.value}'.")

        order.status = OrderStatus.CANCELLED
        # Remove do livro
        bids = self.order_book_bids.get(order.ticker, [])
        asks = self.order_book_asks.get(order.ticker, [])
        if order in bids:
            bids.remove(order)
        if order in asks:
            asks.remove(order)

        self._recalculate_account_margin(order.account_id)
        return order

    def resolve_volatility_auction(self, ticker: str) -> Tuple[Optional[Decimal], List[Trade]]:
        """Resolve o Leilão de Volatilidade determinando o preço de equilíbrio que maximiza o volume."""
        contract = self.contracts.get(ticker)
        if not contract:
            raise CbioFuturesValidationError(f"Contrato '{ticker}' não encontrado.")
        if contract.trading_phase != TradingPhase.VOLATILITY_AUCTION:
            raise InvalidTradingPhaseError(f"Contrato '{ticker}' não está em Leilão de Volatilidade.")

        bids = [o for o in self.order_book_bids.get(ticker, []) if o.remaining_quantity > 0]
        asks = [o for o in self.order_book_asks.get(ticker, []) if o.remaining_quantity > 0]

        if not bids or not asks:
            # Não há cruzamento, retorna para contínua
            contract.trading_phase = TradingPhase.CONTINUOUS
            return None, []

        clearing_price, max_executable_qty = self._calculate_auction_equilibrium(bids, asks, contract.reference_price)
        if clearing_price is None or max_executable_qty == 0:
            contract.trading_phase = TradingPhase.CONTINUOUS
            return None, []

        auction_trades: List[Trade] = []
        # Executa casamentos ao preço de equilíbrio
        bids.sort(key=lambda o: (- (o.price or Decimal("999999")), o.created_at))
        asks.sort(key=lambda o: (o.price or Decimal("0.00"), o.created_at))

        qty_to_fill = max_executable_qty
        b_idx = 0
        a_idx = 0

        while qty_to_fill > 0 and b_idx < len(bids) and a_idx < len(asks):
            bid = bids[b_idx]
            ask = asks[a_idx]

            # Ambos devem ser elegíveis ao preço de leilão
            if (bid.price is not None and bid.price < clearing_price) or (ask.price is not None and ask.price > clearing_price):
                break

            match_qty = min(bid.remaining_quantity, ask.remaining_quantity, qty_to_fill)
            if match_qty == 0:
                break

            self.trade_counter += 1
            trade = Trade(
                trade_id=f"TRD-{contract.ticker}-{self.trade_counter:06d}",
                ticker=contract.ticker,
                buy_order_id=bid.order_id,
                sell_order_id=ask.order_id,
                buyer_account_id=bid.account_id,
                seller_account_id=ask.account_id,
                price=clearing_price,
                quantity=match_qty,
                executed_at=datetime.now(),
                in_auction=True,
                contract_multiplier=contract.contract_multiplier,
            )
            self.trades.append(trade)
            auction_trades.append(trade)

            bid.filled_quantity += match_qty
            ask.filled_quantity += match_qty
            qty_to_fill -= match_qty

            if bid.remaining_quantity == 0:
                bid.status = OrderStatus.FILLED
                b_idx += 1
            else:
                bid.status = OrderStatus.PARTIALLY_FILLED

            if ask.remaining_quantity == 0:
                ask.status = OrderStatus.FILLED
                a_idx += 1
            else:
                ask.status = OrderStatus.PARTIALLY_FILLED

            self._update_position_from_trade(trade)

        # Remove ordens preenchidas dos livros
        self.order_book_bids[ticker] = [o for o in self.order_book_bids[ticker] if o.remaining_quantity > 0]
        self.order_book_asks[ticker] = [o for o in self.order_book_asks[ticker] if o.remaining_quantity > 0]

        # Atualiza preço de referência com o preço do leilão e retorna para contínua
        contract.reference_price = clearing_price
        contract.trading_phase = TradingPhase.CONTINUOUS

        # Recalcula margens das contas envolvidas
        for tr in auction_trades:
            self._recalculate_account_margin(tr.buyer_account_id)
            self._recalculate_account_margin(tr.seller_account_id)

        return clearing_price, auction_trades

    # -----------------------------------------------------------------------
    # Ajuste Diário e Margens da Câmara B3
    # -----------------------------------------------------------------------

    def run_daily_settlement(
        self,
        ticker: str,
        settlement_price: Decimal,
        settlement_date: date,
    ) -> DailyAdjustmentResult:
        """Executa a rotina de Ajuste Diário (Mark-to-Market) da Câmara B3 para o contrato CBI.

        Ajuste do Dia t:
        - Para posições novas abertas no dia ao preço P_trade:
          * Comprador: (PA_t - P_trade) * 100 * Quantidade
          * Vendedor:  (P_trade - PA_t) * 100 * Quantidade
        - Para posições carregadas do dia anterior (PA_t-1):
          * Comprador: (PA_t - PA_t-1) * 100 * Quantidade
          * Vendedor:  (PA_t-1 - PA_t) * 100 * Quantidade

        Garante invariante estrita: Somatório(Ajuste_Compradores) + Somatório(Ajuste_Vendedores) == 0,00.
        """
        contract = self.contracts.get(ticker)
        if not contract:
            raise CbioFuturesValidationError(f"Contrato '{ticker}' não encontrado.")
        if contract.trading_phase == TradingPhase.EXPIRED:
            raise ExpiredContractError(f"Contrato '{ticker}' já expirou.")

        settlement_price = round_cents(Decimal(str(settlement_price)))
        contract.settlement_price = settlement_price

        account_adjustments: Dict[str, Decimal] = {}
        details: List[Dict[str, Any]] = []
        long_total = Decimal("0.00")
        short_total = Decimal("0.00")

        # Posições ativas do ticker
        active_positions = [
            pos for pos in self.positions.values()
            if pos.ticker == ticker and pos.status == "OPEN" and pos.quantity > 0
        ]

        for pos in active_positions:
            base_p = pos.current_reference_price
            qty = pos.quantity
            multiplier = Decimal(contract.contract_multiplier)

            if pos.side == PositionSide.LONG:
                # Comprador ganha se PA_t > base_p, perde se PA_t < base_p
                adj = round_cents((settlement_price - base_p) * multiplier * Decimal(qty))
                long_total += adj
            else:
                # Vendedor ganha se base_p > PA_t, perde se base_p < PA_t
                adj = round_cents((base_p - settlement_price) * multiplier * Decimal(qty))
                short_total += adj

            account_adjustments[pos.account_id] = round_cents(
                account_adjustments.get(pos.account_id, Decimal("0.00")) + adj
            )

            # Atualiza posição
            pos.accumulated_pnl = round_cents(pos.accumulated_pnl + adj)
            pos.current_reference_price = settlement_price

            details.append({
                "account_id": pos.account_id,
                "side": pos.side.value,
                "quantity": pos.quantity,
                "base_price": str(base_p),
                "settlement_price": str(settlement_price),
                "daily_adjustment": str(adj),
            })

        net_clearing = round_cents(long_total + short_total)

        # Aplica na conta corrente de liquidação D+1 de cada participante
        for acc_id, adj_val in account_adjustments.items():
            profile = self.margin_profiles[acc_id]
            profile.cash_settlement_balance = round_cents(profile.cash_settlement_balance + adj_val)
            self._recalculate_account_margin(acc_id)

        # O preço de referência para o dia seguinte se torna o preço de ajuste do dia
        contract.reference_price = settlement_price

        result = DailyAdjustmentResult(
            settlement_date=settlement_date,
            ticker=ticker,
            settlement_price=settlement_price,
            account_adjustments=account_adjustments,
            long_total_adjustment=long_total,
            short_total_adjustment=short_total,
            net_clearing_adjustment=net_clearing,
            settlement_cycle="D+1",
            details=details,
        )
        self.daily_adjustments.append(result)
        return result

    def run_final_cash_settlement(
        self,
        ticker: str,
        spot_final_price: Decimal,
        settlement_date: date,
    ) -> FinalCashSettlementResult:
        """Executa a Liquidação Final Exclusivamente Financeira no Vencimento do Futuro CBI.

        Baseada no Preço Médio/Fechamento Spot de CBIO apurado na B3 na data de expiração.
        Extingue todas as posições em aberto, zerando o Open Interest e liquidando a diferença
        contra o último preço de ajuste do contrato em D+1.
        """
        contract = self.contracts.get(ticker)
        if not contract:
            raise CbioFuturesValidationError(f"Contrato '{ticker}' não encontrado.")
        if contract.trading_phase == TradingPhase.EXPIRED:
            raise ExpiredContractError(f"Contrato '{ticker}' já foi liquidado financeiramente.")

        spot_final_price = round_cents(Decimal(str(spot_final_price)))
        contract.spot_final_price = spot_final_price
        prev_p = contract.reference_price
        multiplier = Decimal(contract.contract_multiplier)

        account_pnl: Dict[str, Decimal] = {}
        details: List[Dict[str, Any]] = []
        long_total = Decimal("0.00")
        short_total = Decimal("0.00")
        closed_contracts = 0

        active_positions = [
            pos for pos in self.positions.values()
            if pos.ticker == ticker and pos.status == "OPEN" and pos.quantity > 0
        ]

        for pos in active_positions:
            base_p = pos.current_reference_price
            qty = pos.quantity
            closed_contracts += qty

            if pos.side == PositionSide.LONG:
                pnl = round_cents((spot_final_price - base_p) * multiplier * Decimal(qty))
                long_total += pnl
            else:
                pnl = round_cents((base_p - spot_final_price) * multiplier * Decimal(qty))
                short_total += pnl

            account_pnl[pos.account_id] = round_cents(account_pnl.get(pos.account_id, Decimal("0.00")) + pnl)

            # Extingue a posição
            pos.accumulated_pnl = round_cents(pos.accumulated_pnl + pnl)
            pos.status = "EXPIRED"
            pos.quantity = 0

            details.append({
                "account_id": pos.account_id,
                "side": pos.side.value,
                "closed_quantity": qty,
                "base_price": str(base_p),
                "spot_final_price": str(spot_final_price),
                "final_pnl": str(pnl),
            })

        net_clearing = round_cents(long_total + short_total)

        # Credita/Debita saldo nas contas de liquidação financeira D+1
        for acc_id, pnl_val in account_pnl.items():
            profile = self.margin_profiles[acc_id]
            profile.cash_settlement_balance = round_cents(profile.cash_settlement_balance + pnl_val)
            self._recalculate_account_margin(acc_id)

        # Marca contrato como expirado e cancela ordens remanescentes
        contract.trading_phase = TradingPhase.EXPIRED
        for o in self.order_book_bids.get(ticker, []):
            o.status = OrderStatus.CANCELLED
        for o in self.order_book_asks.get(ticker, []):
            o.status = OrderStatus.CANCELLED
        self.order_book_bids[ticker] = []
        self.order_book_asks[ticker] = []

        result = FinalCashSettlementResult(
            settlement_date=settlement_date,
            ticker=ticker,
            spot_final_price=spot_final_price,
            previous_settlement_price=prev_p,
            account_final_pnl=account_pnl,
            long_total_pnl=long_total,
            short_total_pnl=short_total,
            net_clearing_pnl=net_clearing,
            closed_contracts=closed_contracts,
            settlement_cycle="D+1",
            details=details,
        )
        self.final_settlements.append(result)
        return result

    def get_open_interest(self, ticker: str) -> int:
        """Retorna a quantidade total de contratos em aberto (Open Interest) para o ticker."""
        long_qty = sum(
            pos.quantity for pos in self.positions.values()
            if pos.ticker == ticker and pos.status == "OPEN" and pos.side == PositionSide.LONG
        )
        short_qty = sum(
            pos.quantity for pos in self.positions.values()
            if pos.ticker == ticker and pos.status == "OPEN" and pos.side == PositionSide.SHORT
        )
        # Invariante intrínseca: long_qty deve ser igual a short_qty
        if long_qty != short_qty:
            raise InvariantViolationError(
                f"Assimetria de Open Interest para ticker '{ticker}': Compradas ({long_qty}) != Vendidas ({short_qty}).",
                law_code="LAW-087",
            )
        return long_qty

    # -----------------------------------------------------------------------
    # Verificação de Invariantes Formais
    # -----------------------------------------------------------------------

    def verify_invariants(self) -> Dict[str, bool]:
        """Audita rigorosamente as leis de conservação matemática e regras regulatórias do fluxo FL-086:

        1. Invariante de Soma-Zero no Ajuste Diário:
           Somatório(Ajuste_Compradores) + Somatório(Ajuste_Vendedores) == 0,00 para todo ciclo diário.
        2. Invariante de Open Interest:
           Posições_Compradas == Posições_Vendidas == Open_Interest_Total para cada contrato ativo.
        3. Conservação de Lotes e Volume nos Trades:
           Quantidade comprada executada == Quantidade vendida executada.
        4. Inviolabilidade do Túnel de Rejeição PUMA:
           Nenhum negócio executado ou ordem aceita pode violar os limites rígidos do túnel de rejeição.
        5. Invariante de Soma-Zero na Liquidação Final Financeira:
           Somatório(P&L Final Compradores) + Somatório(P&L Final Vendedores) == 0,00 e Open Interest == 0.
        6. Integridade de Margem e Chamadas de Margem:
           Toda conta em déficit de margem possui chamada de margem registrada pela Câmara B3.
        """
        # Lei 1: Soma-zero em todos os ajustes diários
        daily_zero_sum_verified = True
        for adj in self.daily_adjustments:
            assert_cbio_futures_daily_adjustment_zero_sum(
                buyer_total_adjustment=adj.long_total_adjustment,
                seller_total_adjustment=adj.short_total_adjustment,
                contract_ticker=adj.ticker,
                settlement_date=adj.settlement_date.isoformat(),
            )
            if adj.net_clearing_adjustment != Decimal("0.00"):
                daily_zero_sum_verified = False

        # Lei 2: Open Interest balanceado
        open_interest_verified = True
        for ticker in self.contracts:
            long_contracts = sum(
                pos.quantity for pos in self.positions.values()
                if pos.ticker == ticker and pos.status == "OPEN" and pos.side == PositionSide.LONG
            )
            short_contracts = sum(
                pos.quantity for pos in self.positions.values()
                if pos.ticker == ticker and pos.status == "OPEN" and pos.side == PositionSide.SHORT
            )
            reported_oi = self.get_open_interest(ticker)
            assert_cbio_futures_open_interest_parity(
                long_open_interest=long_contracts,
                short_open_interest=short_contracts,
                reported_open_interest=reported_oi,
                contract_ticker=ticker,
            )
            if long_contracts != short_contracts:
                open_interest_verified = False

        # Lei 3: Conservação de Lotes de Trades
        trades_volume_verified = True
        for tr in self.trades:
            if tr.quantity <= 0:
                trades_volume_verified = False
                break
            buy_ord = self.orders.get(tr.buy_order_id)
            sell_ord = self.orders.get(tr.sell_order_id)
            if buy_ord and sell_ord:
                if buy_ord.ticker != tr.ticker or sell_ord.ticker != tr.ticker:
                    trades_volume_verified = False
                    break

        # Lei 4: Túneis rígidos de negociação
        collars_verified = True
        for tr in self.trades:
            contract = self.contracts[tr.ticker]
            min_h, max_h = contract.hard_limits()
            if tr.price < min_h or tr.price > max_h:
                collars_verified = False
                raise InvariantViolationError(
                    f"Negócio '{tr.trade_id}' executado fora do túnel de rejeição [{min_h}, {max_h}]: Preço={tr.price}.",
                    law_code="PUMA-COLLAR-BREACH",
                )

        # Lei 5: Soma-zero na Liquidação Final Financeira
        final_settlement_verified = True
        for fin in self.final_settlements:
            assert_cbio_futures_final_settlement_zero_sum(
                buyer_final_pnl=fin.long_total_pnl,
                seller_final_pnl=fin.short_total_pnl,
                contract_ticker=fin.ticker,
                settlement_date=fin.settlement_date.isoformat(),
            )
            if fin.net_clearing_pnl != Decimal("0.00"):
                final_settlement_verified = False
            # Ao expirar, o Open Interest deve ser 0
            if self.get_open_interest(fin.ticker) != 0:
                final_settlement_verified = False
                raise InvariantViolationError(
                    f"Contrato expirado '{fin.ticker}' mantém Open Interest residual não nulo.",
                    law_code="LAW-088",
                )

        # Lei 6: Cobertura de Chamadas de Margem para Contas Deficitárias
        margin_calls_verified = True
        for acc_id, profile in self.margin_profiles.items():
            if profile.has_margin_deficit:
                has_active_call = any(
                    mc.account_id == acc_id and not mc.resolved for mc in self.margin_calls
                )
                if not has_active_call:
                    margin_calls_verified = False
                    raise InvariantViolationError(
                        f"Conta '{acc_id}' possui déficit de margem de {profile.available_margin} sem Chamada de Margem ativa.",
                        law_code="CORE-MARGIN-BREACH",
                    )

        return {
            "daily_zero_sum_verified": daily_zero_sum_verified,
            "open_interest_verified": open_interest_verified,
            "trades_volume_verified": trades_volume_verified,
            "collars_verified": collars_verified,
            "final_settlement_verified": final_settlement_verified,
            "margin_calls_verified": margin_calls_verified,
            "all_invariants_hold": (
                daily_zero_sum_verified
                and open_interest_verified
                and trades_volume_verified
                and collars_verified
                and final_settlement_verified
                and margin_calls_verified
            ),
        }

    # -----------------------------------------------------------------------
    # Métodos Internos e Auxiliares
    # -----------------------------------------------------------------------

    def _crosses_auction_collar(
        self,
        order: Order,
        contract: CbioContract,
        min_auction: Decimal,
        max_auction: Decimal,
    ) -> bool:
        """Verifica se uma ordem limitada submetida ou executável acionaria o Túnel de Leilão."""
        if order.order_type == OrderType.LIMIT and order.price is not None:
            if order.side == OrderSide.BUY and order.price > max_auction:
                return True
            if order.side == OrderSide.SELL and order.price < min_auction:
                return True

        # Verifica se o matching com a ponta oposta descansada se daria fora do túnel de leilão
        if order.side == OrderSide.BUY:
            asks = self.order_book_asks.get(order.ticker, [])
            if asks and asks[0].price is not None and asks[0].price < min_auction:
                return True
        else:
            bids = self.order_book_bids.get(order.ticker, [])
            if bids and bids[0].price is not None and bids[0].price > max_auction:
                return True

        return False

    def _insert_order_into_book(self, order: Order) -> None:
        """Insere ordem no livro de ofertas mantendo prioridade Preço-Tempo (FIFO)."""
        ticker = order.ticker
        if order.side == OrderSide.BUY:
            bids = self.order_book_bids[ticker]
            bids.append(order)
            bids.sort(key=lambda o: (- (o.price or Decimal("999999.00")), o.created_at))
        else:
            asks = self.order_book_asks[ticker]
            asks.append(order)
            asks.sort(key=lambda o: (o.price or Decimal("0.00"), o.created_at))

    def _match_continuous(self, incoming_order: Order, contract: CbioContract) -> List[Trade]:
        """Executa o matching contínuo da ordem entrante com as ordens passivas em repouso."""
        trades: List[Trade] = []
        ticker = incoming_order.ticker

        if incoming_order.side == OrderSide.BUY:
            resting_orders = self.order_book_asks[ticker]
            # While incoming order has quantity and best ask matches
            while incoming_order.remaining_quantity > 0 and resting_orders:
                best_ask = resting_orders[0]
                if incoming_order.order_type == OrderType.LIMIT:
                    if incoming_order.price is not None and best_ask.price is not None:
                        if incoming_order.price < best_ask.price:
                            break  # Preço não cruza

                exec_price = best_ask.price or incoming_order.price or contract.reference_price
                exec_qty = min(incoming_order.remaining_quantity, best_ask.remaining_quantity)

                self.trade_counter += 1
                trade = Trade(
                    trade_id=f"TRD-{ticker}-{self.trade_counter:06d}",
                    ticker=ticker,
                    buy_order_id=incoming_order.order_id,
                    sell_order_id=best_ask.order_id,
                    buyer_account_id=incoming_order.account_id,
                    seller_account_id=best_ask.account_id,
                    price=exec_price,
                    quantity=exec_qty,
                    executed_at=datetime.now(),
                    in_auction=False,
                    contract_multiplier=contract.contract_multiplier,
                )
                self.trades.append(trade)
                trades.append(trade)

                incoming_order.filled_quantity += exec_qty
                best_ask.filled_quantity += exec_qty

                if best_ask.remaining_quantity == 0:
                    best_ask.status = OrderStatus.FILLED
                    resting_orders.pop(0)
                else:
                    best_ask.status = OrderStatus.PARTIALLY_FILLED

                self._update_position_from_trade(trade)

            if incoming_order.remaining_quantity == 0:
                incoming_order.status = OrderStatus.FILLED
            else:
                incoming_order.status = (
                    OrderStatus.PARTIALLY_FILLED if incoming_order.filled_quantity > 0 else OrderStatus.NEW
                )
                if incoming_order.order_type == OrderType.LIMIT:
                    self._insert_order_into_book(incoming_order)
                else:
                    # Ordem a mercado não preenchida integralmente é cancelada (IOC behavior B3)
                    incoming_order.status = OrderStatus.CANCELLED

        else:  # OrderSide.SELL
            resting_orders = self.order_book_bids[ticker]
            while incoming_order.remaining_quantity > 0 and resting_orders:
                best_bid = resting_orders[0]
                if incoming_order.order_type == OrderType.LIMIT:
                    if incoming_order.price is not None and best_bid.price is not None:
                        if incoming_order.price > best_bid.price:
                            break

                exec_price = best_bid.price or incoming_order.price or contract.reference_price
                exec_qty = min(incoming_order.remaining_quantity, best_bid.remaining_quantity)

                self.trade_counter += 1
                trade = Trade(
                    trade_id=f"TRD-{ticker}-{self.trade_counter:06d}",
                    ticker=ticker,
                    buy_order_id=best_bid.order_id,
                    sell_order_id=incoming_order.order_id,
                    buyer_account_id=best_bid.account_id,
                    seller_account_id=incoming_order.account_id,
                    price=exec_price,
                    quantity=exec_qty,
                    executed_at=datetime.now(),
                    in_auction=False,
                    contract_multiplier=contract.contract_multiplier,
                )
                self.trades.append(trade)
                trades.append(trade)

                incoming_order.filled_quantity += exec_qty
                best_bid.filled_quantity += exec_qty

                if best_bid.remaining_quantity == 0:
                    best_bid.status = OrderStatus.FILLED
                    resting_orders.pop(0)
                else:
                    best_bid.status = OrderStatus.PARTIALLY_FILLED

                self._update_position_from_trade(trade)

            if incoming_order.remaining_quantity == 0:
                incoming_order.status = OrderStatus.FILLED
            else:
                incoming_order.status = (
                    OrderStatus.PARTIALLY_FILLED if incoming_order.filled_quantity > 0 else OrderStatus.NEW
                )
                if incoming_order.order_type == OrderType.LIMIT:
                    self._insert_order_into_book(incoming_order)
                else:
                    incoming_order.status = OrderStatus.CANCELLED

        # Recalcula margens
        self._recalculate_account_margin(incoming_order.account_id)
        for tr in trades:
            self._recalculate_account_margin(tr.buyer_account_id)
            self._recalculate_account_margin(tr.seller_account_id)

        return trades

    def _calculate_auction_equilibrium(
        self,
        bids: List[Order],
        asks: List[Order],
        reference_price: Decimal,
    ) -> Tuple[Optional[Decimal], int]:
        """Calcula o Preço Teórico de Fechamento (PTF) do leilão que maximiza o volume executável."""
        all_prices = set()
        for b in bids:
            if b.price is not None:
                all_prices.add(b.price)
        for a in asks:
            if a.price is not None:
                all_prices.add(a.price)

        if not all_prices:
            return None, 0

        best_price: Optional[Decimal] = None
        max_volume = 0
        min_imbalance = 999999999
        min_dist_ref = Decimal("999999.00")

        sorted_prices = sorted(list(all_prices))
        for p in sorted_prices:
            cum_bid = sum(o.remaining_quantity for o in bids if o.price is None or o.price >= p)
            cum_ask = sum(o.remaining_quantity for o in asks if o.price is None or o.price <= p)
            executable = min(cum_bid, cum_ask)
            imbalance = abs(cum_bid - cum_ask)
            dist_ref = abs(p - reference_price)

            if executable > max_volume:
                max_volume = executable
                min_imbalance = imbalance
                min_dist_ref = dist_ref
                best_price = p
            elif executable == max_volume and executable > 0:
                if imbalance < min_imbalance:
                    min_imbalance = imbalance
                    min_dist_ref = dist_ref
                    best_price = p
                elif imbalance == min_imbalance and dist_ref < min_dist_ref:
                    min_dist_ref = dist_ref
                    best_price = p

        return best_price, max_volume

    def _update_position_from_trade(self, trade: Trade) -> None:
        """Atualiza ou cria as posições dos participantes comprador e vendedor a partir do trade."""
        # Comprador
        self._apply_trade_to_position(
            account_id=trade.buyer_account_id,
            ticker=trade.ticker,
            side=PositionSide.LONG,
            quantity=trade.quantity,
            price=trade.price,
        )
        # Vendedor
        self._apply_trade_to_position(
            account_id=trade.seller_account_id,
            ticker=trade.ticker,
            side=PositionSide.SHORT,
            quantity=trade.quantity,
            price=trade.price,
        )

    def _apply_trade_to_position(
        self,
        account_id: str,
        ticker: str,
        side: PositionSide,
        quantity: int,
        price: Decimal,
    ) -> None:
        """Atualiza a posição direcional com netting automático de posições opostas."""
        key = (account_id, ticker)
        pos = self.positions.get(key)

        if not pos or pos.status != "OPEN" or pos.quantity == 0:
            # Nova posição
            self.positions[key] = Position(
                account_id=account_id,
                ticker=ticker,
                side=side,
                quantity=quantity,
                entry_price=price,
                current_reference_price=price,
                accumulated_pnl=Decimal("0.00"),
                status="OPEN",
            )
            return

        if pos.side == side:
            # Aumento de posição no mesmo lado: calcula preço médio ponderado
            total_qty = pos.quantity + quantity
            weighted_entry = round_cents(
                (pos.entry_price * Decimal(pos.quantity) + price * Decimal(quantity)) / Decimal(total_qty)
            )
            weighted_ref = round_cents(
                (pos.current_reference_price * Decimal(pos.quantity) + price * Decimal(quantity)) / Decimal(total_qty)
            )
            pos.quantity = total_qty
            pos.entry_price = weighted_entry
            pos.current_reference_price = weighted_ref
        else:
            # Redução ou reversão de posição
            if pos.quantity > quantity:
                # Redução parcial
                pos.quantity -= quantity
            elif pos.quantity == quantity:
                # Encerramento total
                pos.quantity = 0
                pos.status = "CLOSED"
            else:
                # Reversão de posição (flip de Long para Short ou vice-versa)
                remaining_qty = quantity - pos.quantity
                pos.side = side
                pos.quantity = remaining_qty
                pos.entry_price = price
                pos.current_reference_price = price

    def _pre_trade_margin_check(self, order: Order, contract: CbioContract) -> None:
        """Verifica se a conta possui garantias suficientes para suportar o risco incremental."""
        profile = self.margin_profiles[order.account_id]
        order_price = order.price or contract.reference_price
        notional = contract.notional_per_contract(order_price) * Decimal(order.quantity)
        incremental_margin = max(
            Decimal(order.quantity) * MIN_MARGIN_PER_CONTRACT,
            round_cents(notional * DEFAULT_MARGIN_RATE),
        )

        # Se já estiver em déficit e a ordem for agressora direcional aumentando risco
        key = (order.account_id, order.ticker)
        pos = self.positions.get(key)
        is_increasing_risk = True
        if pos and pos.status == "OPEN" and pos.quantity > 0:
            if (pos.side == PositionSide.LONG and order.side == OrderSide.SELL) or (
                pos.side == PositionSide.SHORT and order.side == OrderSide.BUY
            ):
                # Ordem redutora de risco não deve ser bloqueada por margem
                is_increasing_risk = False

        if is_increasing_risk and (profile.available_margin < incremental_margin):
            # Emite chamada de margem e rejeita a ordem por insuficiência
            self._issue_margin_call(
                account_id=order.account_id,
                required_margin=profile.required_margin + incremental_margin,
                total_collateral=profile.total_collateral_value,
            )
            raise MarginDeficitError(
                f"Conta '{order.account_id}' com margem insuficiente. Disponível: {profile.available_margin}, "
                f"Requerida para a ordem: {incremental_margin}."
            )

    def _recalculate_account_margin(self, account_id: str) -> None:
        """Recalcula a margem CORE requerida total para a conta e valida status de Margin Call."""
        profile = self.margin_profiles.get(account_id)
        if not profile:
            return

        total_req = Decimal("0.00")
        for (acc, ticker), pos in self.positions.items():
            if acc == account_id and pos.status == "OPEN" and pos.quantity > 0:
                contract = self.contracts.get(ticker)
                ref_p = contract.reference_price if contract else pos.current_reference_price
                mult = Decimal(contract.contract_multiplier if contract else CBI_CONTRACT_MULTIPLIER)
                notional = ref_p * mult * Decimal(pos.quantity)
                req_pos = max(
                    Decimal(pos.quantity) * MIN_MARGIN_PER_CONTRACT,
                    round_cents(notional * DEFAULT_MARGIN_RATE),
                )
                total_req += req_pos

        profile.required_margin = total_req
        if profile.has_margin_deficit:
            self._issue_margin_call(
                account_id=account_id,
                required_margin=total_req,
                total_collateral=profile.total_collateral_value,
            )
        else:
            self._check_margin_calls_resolution(account_id)

    def _issue_margin_call(self, account_id: str, required_margin: Decimal, total_collateral: Decimal) -> MarginCall:
        """Emite ou atualiza chamada de margem para uma conta com déficit de colateral."""
        deficit = round_cents(required_margin - total_collateral)
        if deficit <= Decimal("0.00"):
            return None  # Sem déficit

        # Verifica se já existe chamada aberta não resolvida
        for mc in self.margin_calls:
            if mc.account_id == account_id and not mc.resolved:
                mc.required_margin = required_margin
                mc.total_collateral = total_collateral
                mc.deficit_amount = deficit
                return mc

        self.margin_call_counter += 1
        today = date.today()
        call = MarginCall(
            call_id=f"MC-CBI-{self.margin_call_counter:05d}",
            account_id=account_id,
            call_date=today,
            required_margin=required_margin,
            total_collateral=total_collateral,
            deficit_amount=deficit,
            due_date=today,
            resolved=False,
        )
        self.margin_calls.append(call)
        return call

    def _check_margin_calls_resolution(self, account_id: str) -> None:
        """Resolve chamadas de margem pendentes caso a conta tenha restabelecido garantias suficientes."""
        profile = self.margin_profiles.get(account_id)
        if not profile:
            return
        if not profile.has_margin_deficit:
            for mc in self.margin_calls:
                if mc.account_id == account_id and not mc.resolved:
                    mc.resolved = True

    # -----------------------------------------------------------------------
    # Serialização e Carga a partir de Fixture
    # -----------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serializa todo o estado operacional do motor de futuros CBI."""
        return {
            "contracts": [c.to_dict() for c in self.contracts.values()],
            "margin_profiles": [p.to_dict() for p in self.margin_profiles.values()],
            "orders": [o.to_dict() for o in self.orders.values()],
            "trades": [t.to_dict() for t in self.trades],
            "positions": [pos.to_dict() for pos in self.positions.values()],
            "margin_calls": [mc.to_dict() for mc in self.margin_calls],
            "daily_adjustments": [da.to_dict() for da in self.daily_adjustments],
            "final_settlements": [fs.to_dict() for fs in self.final_settlements],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CbioFuturesEngine:
        """Reconstitui o motor a partir de um dicionário."""
        engine = cls()
        for c_dict in data.get("contracts", []):
            contract = CbioContract.from_dict(c_dict)
            engine.contracts[contract.ticker] = contract
            engine.order_book_bids[contract.ticker] = []
            engine.order_book_asks[contract.ticker] = []

        for p_dict in data.get("margin_profiles", []):
            profile = AccountMarginProfile.from_dict(p_dict)
            engine.margin_profiles[profile.account_id] = profile

        for o_dict in data.get("orders", []):
            order = Order.from_dict(o_dict)
            engine.orders[order.order_id] = order
            if order.status in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED):
                engine._insert_order_into_book(order)

        for t_dict in data.get("trades", []):
            trade = Trade.from_dict(t_dict)
            engine.trades.append(trade)

        for pos_dict in data.get("positions", []):
            pos = Position.from_dict(pos_dict)
            engine.positions[(pos.account_id, pos.ticker)] = pos

        for mc_dict in data.get("margin_calls", []):
            mc = MarginCall.from_dict(mc_dict)
            engine.margin_calls.append(mc)

        for da_dict in data.get("daily_adjustments", []):
            da = DailyAdjustmentResult.from_dict(da_dict)
            engine.daily_adjustments.append(da)

        for fs_dict in data.get("final_settlements", []):
            fs = FinalCashSettlementResult.from_dict(fs_dict)
            engine.final_settlements.append(fs)

        return engine

    @classmethod
    def load_from_fixture(cls, filepath: Path) -> CbioFuturesEngine:
        """Carrega e instancia o motor de futuros CBI a partir de arquivo JSON de fixture."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
