"""Gestão de Falha de Entrega D+2 e Execução de Buy-In na Câmara B3 (FL-038).

Implementa o ciclo operacional de tratamento de falhas de liquidação física
no mercado à vista em D+2 conforme o Manual de Procedimentos Operacionais
(MPO) da Câmara B3:
1. Identificação de insuficiência de saldo livre na conta de entrega (21-0) em D+2.
2. Aplicação determinística de Multa Operacional de Falha sobre o volume financeiro
   não entregue na janela ordinária de D+2.
3. Regularização parcial ou integral mediante tomada de empréstimo de ativos no
   Banco de Títulos CBLC (BTC — FL-025 / IMBARQ011) na janela de regularização.
4. Emissão e execução de Ordem de Recompra Compulsória (Buy-In) pela Câmara B3 em D+3
   para a quantidade pendente líquida (`Shortfall = Obrigação - Entregue - Regularizado_BTC`).
5. Alocação assimétrica do diferencial de preço (debitado do vendedor inadimplente
   quando `Preço_Recompra > Preço_Venda_Original`; zerado caso contrário).

Estritamente baseado na biblioteca padrão do Python (`dataclasses`, `Decimal`,
`datetime`, `enum`, `json`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Constantes Regulatórias da Câmara B3 (Falha de Entrega e Buy-In)
# ---------------------------------------------------------------------------

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_RATE = Decimal("0.0001")
HUNDRED = Decimal("100")

DEFAULT_PENALTY_RATE_PCT = Decimal("0.50")
DEFAULT_MIN_PENALTY_BRL = Decimal("0.00")


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class BuyInFailureError(Exception):
    """Exceção base para o módulo de gestão de falhas de entrega e Buy-In da B3."""


class BuyInValidationError(BuyInFailureError):
    """Erro de validação de dados ou invariantes de falha de entrega e Buy-In."""


class ExcessRegularizationError(BuyInValidationError):
    """Quantidade regularizada via BTC excede o saldo em falha de D+2."""


class BuyInExecutionQuantityError(BuyInValidationError):
    """Quantidade executada no Buy-In excede o Shortfall líquido pendente."""


# ---------------------------------------------------------------------------
# Funções Auxiliares de Conversão Determinística
# ---------------------------------------------------------------------------

def _to_decimal(value: Any, field_name: str) -> Decimal:
    """Converte valor numérico ou textual para Decimal com validação estrita."""
    if isinstance(value, Decimal):
        return value
    if value is None or isinstance(value, bool):
        raise BuyInValidationError(
            f"O campo '{field_name}' não pode ser nulo ou booleano."
        )
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise BuyInValidationError(
            f"Valor decimal inválido para '{field_name}': {value!r}"
        ) from exc
    if not dec.is_finite():
        raise BuyInValidationError(
            f"Valor numérico não finito para '{field_name}': {value!r}"
        )
    return dec


def _to_int(value: Any, field_name: str) -> int:
    """Converte valor para inteiro estrito (rejeita booleanos e decimais fracionários)."""
    if isinstance(value, bool) or value is None:
        raise BuyInValidationError(
            f"O campo inteiro '{field_name}' não pode ser nulo ou booleano."
        )
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lstrip("-").isdigit():
            return int(stripped)
    raise BuyInValidationError(
        f"Valor inteiro inválido para '{field_name}': {value!r}"
    )


def _to_date(value: Any, field_name: str) -> date:
    """Converte string ISO-8601 (YYYY-MM-DD) ou objeto date/datetime em date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value or not isinstance(value, str):
        raise BuyInValidationError(f"Data inválida para '{field_name}': {value!r}")
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise BuyInValidationError(
            f"Formato de data inválido para '{field_name}' (esperado YYYY-MM-DD): {value!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Enumerações de Estado do Ciclo de Falha e Buy-In
# ---------------------------------------------------------------------------

class FailureStatus(str, Enum):
    """Estados do ciclo operacional de falha de entrega D+2 e Buy-In D+3 na Câmara B3."""

    IDENTIFIED_D2 = "IDENTIFIED_D2"
    PARTIALLY_REGULARIZED_BTC = "PARTIALLY_REGULARIZED_BTC"
    REGULARIZED_BTC = "REGULARIZED_BTC"
    REVERTED_BY_BTC = "REVERTED_BY_BTC"
    BUYIN_ISSUED_D3 = "BUYIN_ISSUED_D3"
    BUYIN_PARTIALLY_EXECUTED = "BUYIN_PARTIALLY_EXECUTED"
    BUYIN_EXECUTED = "BUYIN_EXECUTED"


# ---------------------------------------------------------------------------
# Entidades de Domínio
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeliveryFailureRecord:
    """Registro de falha de entrega de ativos no mercado à vista em D+2.

    Invariante fundamental da Câmara B3:
        Shortfall == Obrigação - Entregue - Regularizado_BTC
    """

    failure_id: str
    trade_id: str
    participant_code: str
    seller_account: str
    ticker: str
    isin: str
    trade_date: date
    settlement_date: date
    obligated_quantity: int
    delivered_quantity: int
    original_sale_price: Decimal
    btc_regularized_quantity: int = 0
    penalty_rate_pct: Decimal = DEFAULT_PENALTY_RATE_PCT
    min_penalty_brl: Decimal = DEFAULT_MIN_PENALTY_BRL
    buyer_participant_code: str = "00208"
    buyer_account: str = "000000-0"
    buyin_date: Optional[date] = None
    buyin_order_id: Optional[str] = None
    buyin_executed_quantity: Optional[int] = None
    buyin_execution_price: Optional[Decimal] = None
    btc_contract_id: Optional[str] = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.failure_id or not str(self.failure_id).strip():
            raise BuyInValidationError("O campo 'failure_id' é obrigatório.")
        if not self.trade_id or not str(self.trade_id).strip():
            raise BuyInValidationError("O campo 'trade_id' é obrigatório.")
        if not self.participant_code or not str(self.participant_code).strip():
            raise BuyInValidationError("O campo 'participant_code' é obrigatório.")
        if not self.seller_account or not str(self.seller_account).strip():
            raise BuyInValidationError("O campo 'seller_account' é obrigatório.")
        if not self.ticker or not str(self.ticker).strip():
            raise BuyInValidationError("O campo 'ticker' é obrigatório.")
        if not self.isin or len(str(self.isin).strip()) != 12:
            raise BuyInValidationError(
                f"O campo 'isin' deve conter exatamente 12 caracteres: {self.isin!r}"
            )

        object.__setattr__(self, "trade_date", _to_date(self.trade_date, "trade_date"))
        object.__setattr__(
            self, "settlement_date", _to_date(self.settlement_date, "settlement_date")
        )
        if self.buyin_date is not None:
            object.__setattr__(
                self, "buyin_date", _to_date(self.buyin_date, "buyin_date")
            )
        else:
            object.__setattr__(
                self, "buyin_date", self.settlement_date + timedelta(days=1)
            )

        if self.settlement_date < self.trade_date:
            raise BuyInValidationError(
                "A data de liquidação D+2 ('settlement_date') não pode ser anterior à data do negócio ('trade_date')."
            )
        if self.buyin_date < self.settlement_date:
            raise BuyInValidationError(
                "A data de Buy-In D+3 ('buyin_date') não pode ser anterior à data de liquidação D+2 ('settlement_date')."
            )

        obligated = _to_int(self.obligated_quantity, "obligated_quantity")
        delivered = _to_int(self.delivered_quantity, "delivered_quantity")
        btc_reg = _to_int(self.btc_regularized_quantity, "btc_regularized_quantity")

        if obligated <= 0:
            raise BuyInValidationError(
                f"A obrigação de entrega ('obligated_quantity') deve ser positiva: {obligated}"
            )
        if delivered < 0:
            raise BuyInValidationError(
                f"A quantidade entregue ('delivered_quantity') não pode ser negativa: {delivered}"
            )
        if delivered >= obligated:
            raise BuyInValidationError(
                f"Registro sem falha de entrega em D+2: entregue ({delivered}) >= obrigação ({obligated})."
            )
        if btc_reg < 0:
            raise BuyInValidationError(
                f"A quantidade regularizada via BTC ('btc_regularized_quantity') não pode ser negativa: {btc_reg}"
            )
        if delivered + btc_reg > obligated:
            raise ExcessRegularizationError(
                f"A soma de entregue ({delivered}) + regularizado via BTC ({btc_reg}) "
                f"excede a obrigação original ({obligated})."
            )

        object.__setattr__(self, "obligated_quantity", obligated)
        object.__setattr__(self, "delivered_quantity", delivered)
        object.__setattr__(self, "btc_regularized_quantity", btc_reg)

        sale_price = _to_decimal(self.original_sale_price, "original_sale_price").quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )
        if sale_price <= Decimal("0.00"):
            raise BuyInValidationError(
                f"O preço original de venda ('original_sale_price') deve ser positivo: {sale_price}"
            )
        object.__setattr__(self, "original_sale_price", sale_price)

        penalty_rate = _to_decimal(self.penalty_rate_pct, "penalty_rate_pct").quantize(
            QUANTIZE_RATE, rounding=ROUND_HALF_UP
        )
        if penalty_rate < Decimal("0.00"):
            raise BuyInValidationError(
                f"A alíquota de multa ('penalty_rate_pct') não pode ser negativa: {penalty_rate}"
            )
        object.__setattr__(self, "penalty_rate_pct", penalty_rate)

        min_penalty = _to_decimal(self.min_penalty_brl, "min_penalty_brl").quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )
        if min_penalty < Decimal("0.00"):
            raise BuyInValidationError(
                f"O piso de multa ('min_penalty_brl') não pode ser negativo: {min_penalty}"
            )
        object.__setattr__(self, "min_penalty_brl", min_penalty)

        shortfall = obligated - delivered - btc_reg
        if self.buyin_executed_quantity is not None:
            exec_qty = _to_int(self.buyin_executed_quantity, "buyin_executed_quantity")
            if exec_qty < 0:
                raise BuyInValidationError(
                    f"A quantidade executada no Buy-In não pode ser negativa: {exec_qty}"
                )
            if exec_qty > shortfall:
                raise BuyInExecutionQuantityError(
                    f"Quantidade executada no Buy-In ({exec_qty}) excede o Shortfall pendente ({shortfall})."
                )
            object.__setattr__(self, "buyin_executed_quantity", exec_qty)

        if self.buyin_execution_price is not None:
            exec_price = _to_decimal(
                self.buyin_execution_price, "buyin_execution_price"
            ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)
            if exec_price <= Decimal("0.00"):
                raise BuyInValidationError(
                    f"O preço de execução do Buy-In deve ser positivo quando informado: {exec_price}"
                )
            object.__setattr__(self, "buyin_execution_price", exec_price)

    @property
    def initial_failure_quantity(self) -> int:
        """Quantidade não entregue no encerramento da janela ordinária de D+2."""
        return self.obligated_quantity - self.delivered_quantity

    @property
    def shortfall_quantity(self) -> int:
        """Quantidade pendente líquida para Buy-In (`Obrigação - Entregue - Regularizado_BTC`)."""
        return (
            self.obligated_quantity
            - self.delivered_quantity
            - self.btc_regularized_quantity
        )

    @property
    def initial_failed_notional_brl(self) -> Decimal:
        """Volume financeiro em falha no fechamento de D+2 (base da multa regulamentar)."""
        return (
            Decimal(self.initial_failure_quantity) * self.original_sale_price
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)

    @property
    def shortfall_notional_brl(self) -> Decimal:
        """Volume financeiro do Shortfall residual ao preço original da venda."""
        return (
            Decimal(self.shortfall_quantity) * self.original_sale_price
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "DeliveryFailureRecord":
        """Constrói um DeliveryFailureRecord a partir de dicionário JSON."""
        return cls(
            failure_id=str(payload["failure_id"]),
            trade_id=str(payload["trade_id"]),
            participant_code=str(payload["participant_code"]),
            seller_account=str(payload["seller_account"]),
            buyer_participant_code=str(payload.get("buyer_participant_code", "00208")),
            buyer_account=str(payload.get("buyer_account", "000000-0")),
            ticker=str(payload["ticker"]),
            isin=str(payload["isin"]),
            trade_date=payload["trade_date"],
            settlement_date=payload["settlement_date"],
            buyin_date=payload.get("buyin_date"),
            obligated_quantity=payload["obligated_quantity"],
            delivered_quantity=payload["delivered_quantity"],
            btc_regularized_quantity=payload.get("btc_regularized_quantity", 0),
            original_sale_price=payload["original_sale_price"],
            penalty_rate_pct=payload.get("penalty_rate_pct", DEFAULT_PENALTY_RATE_PCT),
            min_penalty_brl=payload.get("min_penalty_brl", DEFAULT_MIN_PENALTY_BRL),
            buyin_order_id=payload.get("buyin_order_id"),
            buyin_executed_quantity=payload.get("buyin_executed_quantity"),
            buyin_execution_price=payload.get("buyin_execution_price"),
            btc_contract_id=payload.get("btc_contract_id"),
            description=str(payload.get("description", "")),
        )


@dataclass(frozen=True)
class BuyInExecutionOrder:
    """Ordem de Recompra Compulsória (Buy-In) emitida pela Câmara B3 em D+3."""

    buyin_order_id: str
    failure_id: str
    participant_code: str
    seller_account: str
    buyer_participant_code: str
    buyer_account: str
    ticker: str
    isin: str
    issue_date: date
    original_failure_quantity: int
    btc_regularized_quantity: int
    buyin_quantity: int
    executed_quantity: int
    original_sale_price: Decimal
    buyin_execution_price: Optional[Decimal]
    status: FailureStatus
    btc_contract_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.buyin_order_id or not str(self.buyin_order_id).strip():
            raise BuyInValidationError("O campo 'buyin_order_id' é obrigatório.")
        if self.buyin_quantity < 0:
            raise BuyInValidationError(
                f"A quantidade da ordem de Buy-In ('buyin_quantity') não pode ser negativa: {self.buyin_quantity}"
            )
        if self.executed_quantity < 0:
            raise BuyInValidationError(
                f"A quantidade executada ('executed_quantity') não pode ser negativa: {self.executed_quantity}"
            )
        if self.executed_quantity > self.buyin_quantity:
            raise BuyInExecutionQuantityError(
                f"Quantidade executada ({self.executed_quantity}) maior que a quantidade da ordem de Buy-In ({self.buyin_quantity})."
            )
        if self.executed_quantity > 0 and self.buyin_execution_price is None:
            raise BuyInValidationError(
                "Ordem de Buy-In com quantidade executada positiva requer 'buyin_execution_price'."
            )

    @property
    def remaining_quantity(self) -> int:
        """Quantidade residual da ordem de Buy-In ainda pendente de execução."""
        return self.buyin_quantity - self.executed_quantity

    @property
    def is_reverted_by_btc(self) -> bool:
        """Indica se a ordem de Buy-In foi integralmente revertida via empréstimo BTC."""
        return self.buyin_quantity == 0 and self.status in (
            FailureStatus.REVERTED_BY_BTC,
            FailureStatus.REGULARIZED_BTC,
        )

    @property
    def unit_price_differential_brl(self) -> Decimal:
        """Diferencial unitário cobrado do vendedor inadimplente (`max(0, P_buyin - P_venda)`)."""
        if self.executed_quantity == 0 or self.buyin_execution_price is None:
            return Decimal("0.00")
        if self.buyin_execution_price > self.original_sale_price:
            return (self.buyin_execution_price - self.original_sale_price).quantize(
                QUANTIZE_CENTS, rounding=ROUND_HALF_UP
            )
        return Decimal("0.00")

    @property
    def price_differential_amount_brl(self) -> Decimal:
        """Valor financeiro total do diferencial de preço debitado do vendedor inadimplente."""
        return (
            Decimal(self.executed_quantity) * self.unit_price_differential_brl
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a ordem de Buy-In para dicionário compatível com JSON."""
        return {
            "buyin_order_id": self.buyin_order_id,
            "failure_id": self.failure_id,
            "participant_code": self.participant_code,
            "seller_account": self.seller_account,
            "buyer_participant_code": self.buyer_participant_code,
            "buyer_account": self.buyer_account,
            "ticker": self.ticker,
            "isin": self.isin,
            "issue_date": self.issue_date.isoformat(),
            "original_failure_quantity": self.original_failure_quantity,
            "btc_regularized_quantity": self.btc_regularized_quantity,
            "buyin_quantity": self.buyin_quantity,
            "executed_quantity": self.executed_quantity,
            "remaining_quantity": self.remaining_quantity,
            "original_sale_price": str(self.original_sale_price),
            "buyin_execution_price": (
                str(self.buyin_execution_price)
                if self.buyin_execution_price is not None
                else None
            ),
            "unit_price_differential_brl": str(self.unit_price_differential_brl),
            "price_differential_amount_brl": str(self.price_differential_amount_brl),
            "status": self.status.value,
            "btc_contract_id": self.btc_contract_id,
        }


@dataclass(frozen=True)
class PenaltyAndDifferentialSummary:
    """Demonstrativo consolidado de multa operacional D+2 e diferencial de Buy-In D+3."""

    failure_id: str
    trade_id: str
    participant_code: str
    seller_account: str
    ticker: str
    isin: str
    trade_date: date
    settlement_date: date
    buyin_date: date
    obligated_quantity: int
    delivered_quantity: int
    initial_failure_quantity: int
    btc_regularized_quantity: int
    shortfall_quantity: int
    buyin_executed_quantity: int
    unexecuted_shortfall_quantity: int
    original_sale_price: Decimal
    buyin_execution_price: Optional[Decimal]
    original_failed_notional_brl: Decimal
    shortfall_notional_brl: Decimal
    buyin_executed_notional_brl: Decimal
    penalty_rate_pct: Decimal
    penalty_amount_brl: Decimal
    unit_price_differential_brl: Decimal
    price_differential_amount_brl: Decimal
    favorable_price_spread_ignored_brl: Decimal
    total_debited_from_seller_brl: Decimal
    status: FailureStatus
    buyin_order_id: Optional[str] = None
    btc_contract_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o demonstrativo financeiro para dicionário compatível com JSON."""
        return {
            "failure_id": self.failure_id,
            "trade_id": self.trade_id,
            "participant_code": self.participant_code,
            "seller_account": self.seller_account,
            "ticker": self.ticker,
            "isin": self.isin,
            "trade_date": self.trade_date.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
            "buyin_date": self.buyin_date.isoformat(),
            "obligated_quantity": self.obligated_quantity,
            "delivered_quantity": self.delivered_quantity,
            "initial_failure_quantity": self.initial_failure_quantity,
            "btc_regularized_quantity": self.btc_regularized_quantity,
            "shortfall_quantity": self.shortfall_quantity,
            "buyin_executed_quantity": self.buyin_executed_quantity,
            "unexecuted_shortfall_quantity": self.unexecuted_shortfall_quantity,
            "original_sale_price": str(self.original_sale_price),
            "buyin_execution_price": (
                str(self.buyin_execution_price)
                if self.buyin_execution_price is not None
                else None
            ),
            "original_failed_notional_brl": str(self.original_failed_notional_brl),
            "shortfall_notional_brl": str(self.shortfall_notional_brl),
            "buyin_executed_notional_brl": str(self.buyin_executed_notional_brl),
            "penalty_rate_pct": str(self.penalty_rate_pct),
            "penalty_amount_brl": str(self.penalty_amount_brl),
            "unit_price_differential_brl": str(self.unit_price_differential_brl),
            "price_differential_amount_brl": str(self.price_differential_amount_brl),
            "favorable_price_spread_ignored_brl": str(
                self.favorable_price_spread_ignored_brl
            ),
            "total_debited_from_seller_brl": str(self.total_debited_from_seller_brl),
            "status": self.status.value,
            "buyin_order_id": self.buyin_order_id,
            "btc_contract_id": self.btc_contract_id,
        }


# ---------------------------------------------------------------------------
# Motor Determinístico de Gestão de Falhas e Execução de Buy-In
# ---------------------------------------------------------------------------

class BuyInFailureEngine:
    """Motor determinístico para apuração de falhas D+2, regularização BTC e Buy-In D+3."""

    def __init__(
        self,
        default_penalty_rate_pct: Union[Decimal, str] = DEFAULT_PENALTY_RATE_PCT,
        min_penalty_brl: Union[Decimal, str] = DEFAULT_MIN_PENALTY_BRL,
    ) -> None:
        self.default_penalty_rate_pct = _to_decimal(
            default_penalty_rate_pct, "default_penalty_rate_pct"
        ).quantize(QUANTIZE_RATE, rounding=ROUND_HALF_UP)
        self.min_penalty_brl = _to_decimal(
            min_penalty_brl, "min_penalty_brl"
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)

        if self.default_penalty_rate_pct < Decimal("0.00"):
            raise BuyInValidationError(
                "A alíquota padrão de multa não pode ser negativa."
            )
        if self.min_penalty_brl < Decimal("0.00"):
            raise BuyInValidationError(
                "O piso mínimo de multa não pode ser negativo."
            )

    @staticmethod
    def calculate_shortfall(
        obligated_quantity: int,
        delivered_quantity: int,
        btc_regularized_quantity: int = 0,
    ) -> int:
        """Calcula deterministicamente `Shortfall == Obrigação - Entregue - Regularizado_BTC`."""
        obligated = _to_int(obligated_quantity, "obligated_quantity")
        delivered = _to_int(delivered_quantity, "delivered_quantity")
        btc_reg = _to_int(btc_regularized_quantity, "btc_regularized_quantity")

        if obligated <= 0:
            raise BuyInValidationError(
                f"Obrigação ('obligated_quantity') deve ser positiva: {obligated}"
            )
        if delivered < 0 or btc_reg < 0:
            raise BuyInValidationError(
                "Quantidades entregue e regularizada via BTC não podem ser negativas."
            )
        if delivered > obligated:
            raise BuyInValidationError(
                f"Quantidade entregue ({delivered}) não pode exceder a obrigação ({obligated})."
            )
        if delivered + btc_reg > obligated:
            raise ExcessRegularizationError(
                f"Regularização via BTC ({btc_reg}) excede o saldo em falha ({obligated - delivered})."
            )
        return obligated - delivered - btc_reg

    def calculate_penalty(
        self,
        failed_quantity: int,
        original_sale_price: Union[Decimal, str],
        penalty_rate_pct: Optional[Union[Decimal, str]] = None,
        min_penalty_brl: Optional[Union[Decimal, str]] = None,
    ) -> Decimal:
        """Calcula a multa operacional regulamentar sobre a falha de entrega em D+2."""
        qty = _to_int(failed_quantity, "failed_quantity")
        if qty < 0:
            raise BuyInValidationError(
                f"A quantidade em falha não pode ser negativa: {qty}"
            )
        if qty == 0:
            return Decimal("0.00")

        sale_price = _to_decimal(original_sale_price, "original_sale_price").quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )
        if sale_price <= Decimal("0.00"):
            raise BuyInValidationError(
                f"O preço original da venda deve ser positivo: {sale_price}"
            )

        rate = (
            _to_decimal(penalty_rate_pct, "penalty_rate_pct").quantize(
                QUANTIZE_RATE, rounding=ROUND_HALF_UP
            )
            if penalty_rate_pct is not None
            else self.default_penalty_rate_pct
        )
        floor = (
            _to_decimal(min_penalty_brl, "min_penalty_brl").quantize(
                QUANTIZE_CENTS, rounding=ROUND_HALF_UP
            )
            if min_penalty_brl is not None
            else self.min_penalty_brl
        )

        failed_notional = (Decimal(qty) * sale_price).quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )
        computed_penalty = ((failed_notional * rate) / HUNDRED).quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )
        return max(floor, computed_penalty)

    @staticmethod
    def calculate_price_differential(
        executed_quantity: int,
        original_sale_price: Union[Decimal, str],
        buyin_execution_price: Optional[Union[Decimal, str]],
    ) -> Tuple[Decimal, Decimal, Decimal]:
        """Calcula o diferencial de preço do Buy-In em D+3.

        Retorna uma tupla `(unit_differential_brl, total_differential_debited_brl, favorable_spread_ignored_brl)`:
        - Se `buyin_execution_price > original_sale_price`: o vendedor inadimplente arca com a diferença.
        - Se `buyin_execution_price <= original_sale_price`: o diferencial debitado é `R$ 0,00` e o
          ganho teórico ignorado é registrado em `favorable_spread_ignored_brl`.
        """
        qty = _to_int(executed_quantity, "executed_quantity")
        if qty < 0:
            raise BuyInValidationError(
                f"A quantidade executada não pode ser negativa: {qty}"
            )
        sale_price = _to_decimal(original_sale_price, "original_sale_price").quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )
        if sale_price <= Decimal("0.00"):
            raise BuyInValidationError(
                f"O preço original da venda deve ser positivo: {sale_price}"
            )

        if qty == 0 or buyin_execution_price is None:
            return Decimal("0.00"), Decimal("0.00"), Decimal("0.00")

        exec_price = _to_decimal(
            buyin_execution_price, "buyin_execution_price"
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)
        if exec_price <= Decimal("0.00"):
            raise BuyInValidationError(
                f"O preço de execução do Buy-In deve ser positivo: {exec_price}"
            )

        if exec_price > sale_price:
            unit_diff = (exec_price - sale_price).quantize(
                QUANTIZE_CENTS, rounding=ROUND_HALF_UP
            )
            total_diff = (Decimal(qty) * unit_diff).quantize(
                QUANTIZE_CENTS, rounding=ROUND_HALF_UP
            )
            return unit_diff, total_diff, Decimal("0.00")

        favorable_spread = (Decimal(qty) * (sale_price - exec_price)).quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )
        return Decimal("0.00"), Decimal("0.00"), favorable_spread

    @staticmethod
    def apply_btc_regularization(
        record: DeliveryFailureRecord,
        additional_btc_quantity: int,
        btc_contract_id: Optional[str] = None,
    ) -> DeliveryFailureRecord:
        """Aplica regularização parcial ou total via empréstimo BTC ao registro de falha."""
        add_qty = _to_int(additional_btc_quantity, "additional_btc_quantity")
        if add_qty <= 0:
            raise BuyInValidationError(
                f"A quantidade adicional regularizada via BTC deve ser positiva: {add_qty}"
            )
        new_btc_qty = record.btc_regularized_quantity + add_qty
        new_shortfall = (
            record.obligated_quantity - record.delivered_quantity - new_btc_qty
        )
        new_exec_qty = record.buyin_executed_quantity
        if new_exec_qty is not None and new_exec_qty > max(0, new_shortfall):
            new_exec_qty = max(0, new_shortfall)
        new_exec_price = (
            None if new_shortfall == 0 else record.buyin_execution_price
        )

        return DeliveryFailureRecord(
            failure_id=record.failure_id,
            trade_id=record.trade_id,
            participant_code=record.participant_code,
            seller_account=record.seller_account,
            buyer_participant_code=record.buyer_participant_code,
            buyer_account=record.buyer_account,
            ticker=record.ticker,
            isin=record.isin,
            trade_date=record.trade_date,
            settlement_date=record.settlement_date,
            buyin_date=record.buyin_date,
            obligated_quantity=record.obligated_quantity,
            delivered_quantity=record.delivered_quantity,
            btc_regularized_quantity=new_btc_qty,
            original_sale_price=record.original_sale_price,
            penalty_rate_pct=record.penalty_rate_pct,
            min_penalty_brl=record.min_penalty_brl,
            buyin_order_id=record.buyin_order_id,
            buyin_executed_quantity=new_exec_qty,
            buyin_execution_price=new_exec_price,
            btc_contract_id=btc_contract_id or record.btc_contract_id,
            description=record.description,
        )

    @staticmethod
    def resolve_lifecycle_status(
        shortfall_quantity: int,
        btc_regularized_quantity: int,
        buyin_executed_quantity: int,
        buyin_issued: bool = True,
    ) -> FailureStatus:
        """Determina o status operacional exato do ciclo de falha e Buy-In."""
        if shortfall_quantity == 0:
            return FailureStatus.REVERTED_BY_BTC
        if buyin_executed_quantity == shortfall_quantity:
            return FailureStatus.BUYIN_EXECUTED
        if 0 < buyin_executed_quantity < shortfall_quantity:
            return FailureStatus.BUYIN_PARTIALLY_EXECUTED
        if buyin_issued:
            return FailureStatus.BUYIN_ISSUED_D3
        if btc_regularized_quantity > 0:
            return FailureStatus.PARTIALLY_REGULARIZED_BTC
        return FailureStatus.IDENTIFIED_D2

    def issue_buyin_order(
        self,
        record: DeliveryFailureRecord,
        buyin_order_id: Optional[str] = None,
        issue_date: Optional[date] = None,
    ) -> BuyInExecutionOrder:
        """Emite a Ordem de Recompra Compulsória (Buy-In) em D+3 com base no Shortfall."""
        order_id = (
            buyin_order_id
            or record.buyin_order_id
            or f"BUYIN-{record.failure_id}"
        )
        effective_issue_date = issue_date or record.buyin_date or (
            record.settlement_date + timedelta(days=1)
        )
        shortfall = record.shortfall_quantity
        exec_qty = (
            record.buyin_executed_quantity
            if record.buyin_executed_quantity is not None
            else 0
        )
        status = self.resolve_lifecycle_status(
            shortfall_quantity=shortfall,
            btc_regularized_quantity=record.btc_regularized_quantity,
            buyin_executed_quantity=exec_qty,
            buyin_issued=True,
        )
        return BuyInExecutionOrder(
            buyin_order_id=order_id,
            failure_id=record.failure_id,
            participant_code=record.participant_code,
            seller_account=record.seller_account,
            buyer_participant_code=record.buyer_participant_code,
            buyer_account=record.buyer_account,
            ticker=record.ticker,
            isin=record.isin,
            issue_date=effective_issue_date,
            original_failure_quantity=record.initial_failure_quantity,
            btc_regularized_quantity=record.btc_regularized_quantity,
            buyin_quantity=shortfall,
            executed_quantity=exec_qty,
            original_sale_price=record.original_sale_price,
            buyin_execution_price=(
                record.buyin_execution_price if exec_qty > 0 else None
            ),
            status=status,
            btc_contract_id=record.btc_contract_id,
        )

    @staticmethod
    def execute_buyin_order(
        order: BuyInExecutionOrder,
        executed_quantity: int,
        execution_price: Union[Decimal, str],
    ) -> BuyInExecutionOrder:
        """Registra a execução total ou parcial de uma ordem de Buy-In em D+3."""
        if order.buyin_quantity == 0:
            raise BuyInValidationError(
                "Não é possível executar uma ordem de Buy-In cujo Shortfall já foi revertido via BTC."
            )
        qty = _to_int(executed_quantity, "executed_quantity")
        if qty <= 0:
            raise BuyInValidationError(
                f"A quantidade executada no Buy-In deve ser positiva: {qty}"
            )
        if qty > order.buyin_quantity:
            raise BuyInExecutionQuantityError(
                f"A quantidade executada ({qty}) excede o lote da ordem de Buy-In ({order.buyin_quantity})."
            )
        price = _to_decimal(execution_price, "execution_price").quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )
        if price <= Decimal("0.00"):
            raise BuyInValidationError(
                f"O preço de execução do Buy-In deve ser positivo: {price}"
            )

        new_status = (
            FailureStatus.BUYIN_EXECUTED
            if qty == order.buyin_quantity
            else FailureStatus.BUYIN_PARTIALLY_EXECUTED
        )
        return BuyInExecutionOrder(
            buyin_order_id=order.buyin_order_id,
            failure_id=order.failure_id,
            participant_code=order.participant_code,
            seller_account=order.seller_account,
            buyer_participant_code=order.buyer_participant_code,
            buyer_account=order.buyer_account,
            ticker=order.ticker,
            isin=order.isin,
            issue_date=order.issue_date,
            original_failure_quantity=order.original_failure_quantity,
            btc_regularized_quantity=order.btc_regularized_quantity,
            buyin_quantity=order.buyin_quantity,
            executed_quantity=qty,
            original_sale_price=order.original_sale_price,
            buyin_execution_price=price,
            status=new_status,
            btc_contract_id=order.btc_contract_id,
        )

    def evaluate_failure_case(
        self, record: DeliveryFailureRecord
    ) -> Tuple[BuyInExecutionOrder, PenaltyAndDifferentialSummary]:
        """Avalia um caso completo de falha D+2, emitindo a ordem D+3 e apurando multa e diferencial."""
        order = self.issue_buyin_order(record)

        penalty_brl = self.calculate_penalty(
            failed_quantity=record.initial_failure_quantity,
            original_sale_price=record.original_sale_price,
            penalty_rate_pct=record.penalty_rate_pct,
            min_penalty_brl=record.min_penalty_brl,
        )

        unit_diff, total_diff, favorable_ignored = self.calculate_price_differential(
            executed_quantity=order.executed_quantity,
            original_sale_price=record.original_sale_price,
            buyin_execution_price=order.buyin_execution_price,
        )

        executed_notional = (
            (Decimal(order.executed_quantity) * order.buyin_execution_price).quantize(
                QUANTIZE_CENTS, rounding=ROUND_HALF_UP
            )
            if order.executed_quantity > 0 and order.buyin_execution_price is not None
            else Decimal("0.00")
        )

        total_debited = (penalty_brl + total_diff).quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )

        summary = PenaltyAndDifferentialSummary(
            failure_id=record.failure_id,
            trade_id=record.trade_id,
            participant_code=record.participant_code,
            seller_account=record.seller_account,
            ticker=record.ticker,
            isin=record.isin,
            trade_date=record.trade_date,
            settlement_date=record.settlement_date,
            buyin_date=order.issue_date,
            obligated_quantity=record.obligated_quantity,
            delivered_quantity=record.delivered_quantity,
            initial_failure_quantity=record.initial_failure_quantity,
            btc_regularized_quantity=record.btc_regularized_quantity,
            shortfall_quantity=record.shortfall_quantity,
            buyin_executed_quantity=order.executed_quantity,
            unexecuted_shortfall_quantity=order.remaining_quantity,
            original_sale_price=record.original_sale_price,
            buyin_execution_price=order.buyin_execution_price,
            original_failed_notional_brl=record.initial_failed_notional_brl,
            shortfall_notional_brl=record.shortfall_notional_brl,
            buyin_executed_notional_brl=executed_notional,
            penalty_rate_pct=record.penalty_rate_pct,
            penalty_amount_brl=penalty_brl,
            unit_price_differential_brl=unit_diff,
            price_differential_amount_brl=total_diff,
            favorable_price_spread_ignored_brl=favorable_ignored,
            total_debited_from_seller_brl=total_debited,
            status=order.status,
            buyin_order_id=order.buyin_order_id,
            btc_contract_id=record.btc_contract_id,
        )
        return order, summary

    def process_batch(
        self, records: Sequence[DeliveryFailureRecord]
    ) -> Dict[str, Any]:
        """Processa um lote de falhas de entrega D+2 e consolida os totais financeiros."""
        orders: List[BuyInExecutionOrder] = []
        summaries: List[PenaltyAndDifferentialSummary] = []

        total_obligated_qty = 0
        total_delivered_qty = 0
        total_initial_failure_qty = 0
        total_btc_regularized_qty = 0
        total_shortfall_qty = 0
        total_buyin_executed_qty = 0
        total_penalties_brl = Decimal("0.00")
        total_differentials_brl = Decimal("0.00")
        total_debited_brl = Decimal("0.00")

        for record in records:
            order, summary = self.evaluate_failure_case(record)
            orders.append(order)
            summaries.append(summary)

            total_obligated_qty += summary.obligated_quantity
            total_delivered_qty += summary.delivered_quantity
            total_initial_failure_qty += summary.initial_failure_quantity
            total_btc_regularized_qty += summary.btc_regularized_quantity
            total_shortfall_qty += summary.shortfall_quantity
            total_buyin_executed_qty += summary.buyin_executed_quantity
            total_penalties_brl += summary.penalty_amount_brl
            total_differentials_brl += summary.price_differential_amount_brl
            total_debited_brl += summary.total_debited_from_seller_brl

        return {
            "total_cases": len(records),
            "total_obligated_quantity": total_obligated_qty,
            "total_delivered_quantity": total_delivered_qty,
            "total_initial_failure_quantity": total_initial_failure_qty,
            "total_btc_regularized_quantity": total_btc_regularized_qty,
            "total_shortfall_quantity": total_shortfall_qty,
            "total_buyin_executed_quantity": total_buyin_executed_qty,
            "total_penalties_brl": total_penalties_brl.quantize(
                QUANTIZE_CENTS, rounding=ROUND_HALF_UP
            ),
            "total_price_differentials_brl": total_differentials_brl.quantize(
                QUANTIZE_CENTS, rounding=ROUND_HALF_UP
            ),
            "total_debited_from_sellers_brl": total_debited_brl.quantize(
                QUANTIZE_CENTS, rounding=ROUND_HALF_UP
            ),
            "orders": orders,
            "summaries": summaries,
        }

    @staticmethod
    def load_fixture(fixture_path: Union[str, Path]) -> List[DeliveryFailureRecord]:
        """Carrega os registros de falha de entrega a partir de um arquivo JSON."""
        path = Path(fixture_path)
        raw_data = json.loads(path.read_text(encoding="utf-8"))
        default_rate = raw_data.get(
            "default_penalty_rate_pct", str(DEFAULT_PENALTY_RATE_PCT)
        )
        min_penalty = raw_data.get(
            "min_penalty_brl", str(DEFAULT_MIN_PENALTY_BRL)
        )
        records: List[DeliveryFailureRecord] = []
        for item in raw_data.get("failures", []):
            merged = dict(item)
            merged.setdefault("penalty_rate_pct", default_rate)
            merged.setdefault("min_penalty_brl", min_penalty)
            records.append(DeliveryFailureRecord.from_dict(merged))
        return records

    def evaluate_fixture(self, fixture_path: Union[str, Path]) -> Dict[str, Any]:
        """Carrega e processa integralmente o arquivo de fixture JSON."""
        records = self.load_fixture(fixture_path)
        return self.process_batch(records)
