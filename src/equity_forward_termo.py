"""Mercado a Termo de Ações B3 — Termo Tradicional, Flexível, em Pontos, Cobertura e Liquidação Antecipada (FL-041).

Implementa o motor determinístico de registro, precificação exponencial (DU/252),
enquadramento de garantia bilateral (100% de cobertura física do vendedor na
Carteira 2306-6 da Central Depositária B3 e Margem Requerida CORE do comprador),
substituição de ativo-objeto no Termo Flexível, liquidação antecipada total ou parcial
por solicitação do comprador, rolagem e liquidação no vencimento para o Mercado a Termo
de Ações da Câmara B3, em conformidade com o Manual de Procedimentos Operacionais (MPO).

Regras e invariantes principais:
1. Prazo regulamentar estrito: mínimo de 16 dias corridos e máximo de 999 dias corridos
   entre a data de contratação (D+0) e a data de vencimento.
2. Equação do Preço a Termo: P_termo = round_2(P_vista * (1 + i_aa)^(DU / 252)).
3. Modalidades operacionais:
   - Termo em Reais / Tradicional (`TRADITIONAL_BRL`)
   - Termo em Pontos (`IN_POINTS`, referenciado em taxa de câmbio ou índice)
   - Termo Flexível (`FLEXIBLE`, permite substituição do ativo-objeto pelo vendedor
     mediante venda à vista do ativo original e compra de novo ativo elegível na carteira 2306-6)
4. Garantia do Vendedor: 100% da quantidade em aberto depositada na carteira `2306-6`.
5. Garantia do Comprador: Margem Requerida CORE cobrindo risco de mercado (MTM) e deságio (haircut).
6. Invariante de Conservação Estrita de Quantidade de Ações:
   Quantidade Original == Quantidade Liquidada Antecipadamente
                        + Quantidade Liquidada no Vencimento
                        + Quantidade em Aberto.

100% biblioteca padrão do Python (`dataclasses`, `Decimal`, `datetime`, `enum`, `json`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Constantes Regulatórias da Câmara B3 (Mercado a Termo de Ações)
# ---------------------------------------------------------------------------

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_FOUR = Decimal("0.0001")
QUANTIZE_EIGHT = Decimal("0.00000001")

BUSINESS_DAYS_YEAR = Decimal("252")

MIN_FORWARD_CALENDAR_DAYS = 16
MAX_FORWARD_CALENDAR_DAYS = 999

CUSTODY_WALLET_FORWARD_COVERAGE = "2306-6"
CUSTODY_WALLET_FREE_SPOT = "2101-6"


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class EquityForwardError(Exception):
    """Exceção base para o motor de Mercado a Termo de Ações da Câmara B3 (FL-041)."""


class EquityForwardValidationError(EquityForwardError):
    """Erro de validação cadastral, financeira ou operacional de contrato a termo."""


class ForwardTermRangeError(EquityForwardValidationError):
    """Prazo do contrato a termo fora do intervalo regulamentar B3 (16 a 999 dias corridos)."""


class InsufficientSellerCoverageError(EquityForwardValidationError):
    """Cobertura física do vendedor na carteira 2306-6 inferior a 100% da quantidade em aberto."""


class InsufficientBuyerMarginError(EquityForwardValidationError):
    """Colateral depositado pelo comprador insuficiente frente à Margem Requerida CORE."""


class InvalidModalityOperationError(EquityForwardValidationError):
    """Operação incompatível com a modalidade do contrato a termo (ex.: substituição fora do Termo Flexível)."""


class ForwardQuantityConservationError(EquityForwardValidationError):
    """Violação da invariante de conservação da quantidade de ações do contrato a termo."""


class DuplicateForwardContractError(EquityForwardValidationError):
    """Tentativa de registrar contrato a termo com identificador duplicado."""


class ForwardContractNotFoundError(EquityForwardError):
    """Contrato a termo não localizado no livro de posições da Câmara B3."""


# ---------------------------------------------------------------------------
# Funções Auxiliares Determinísticas
# ---------------------------------------------------------------------------

def _to_decimal(value: Any, field_name: str) -> Decimal:
    """Converte valor numérico ou textual para Decimal com validação estrita."""
    if isinstance(value, Decimal):
        return value
    if value is None or isinstance(value, bool):
        raise EquityForwardValidationError(
            f"O campo '{field_name}' não pode ser nulo ou booleano."
        )
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise EquityForwardValidationError(
            f"Valor decimal inválido para '{field_name}': {value!r}"
        ) from exc
    if not dec.is_finite():
        raise EquityForwardValidationError(
            f"Valor numérico não finito para '{field_name}': {value!r}"
        )
    return dec


def _to_int(value: Any, field_name: str) -> int:
    """Converte valor para inteiro estrito (rejeita booleanos e decimais fracionários)."""
    if isinstance(value, bool) or value is None:
        raise EquityForwardValidationError(
            f"O campo inteiro '{field_name}' não pode ser nulo ou booleano."
        )
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lstrip("-").isdigit():
            return int(stripped)
    raise EquityForwardValidationError(
        f"Valor inteiro inválido para '{field_name}': {value!r}"
    )


def _to_date(value: Any, field_name: str) -> date:
    """Converte string ISO-8601 (YYYY-MM-DD) ou objeto date/datetime em date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value or not isinstance(value, str):
        raise EquityForwardValidationError(
            f"Data inválida para '{field_name}': {value!r}"
        )
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise EquityForwardValidationError(
            f"Formato de data inválido para '{field_name}' (esperado YYYY-MM-DD): {value!r}"
        ) from exc


def _require_non_empty_str(value: Any, field_name: str) -> str:
    """Valida que um campo textual não é vazio."""
    if not isinstance(value, str) or not value.strip():
        raise EquityForwardValidationError(
            f"O campo '{field_name}' deve ser uma string não vazia."
        )
    return value.strip()


def _quantize_money(value: Decimal) -> Decimal:
    """Arredonda valor monetário para 2 casas decimais (ROUND_HALF_UP)."""
    return value.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)


def _quantize_four(value: Decimal) -> Decimal:
    """Arredonda taxa ou cotação em pontos para 4 casas decimais (ROUND_HALF_UP)."""
    return value.quantize(QUANTIZE_FOUR, rounding=ROUND_HALF_UP)


def _quantize_eight(value: Decimal) -> Decimal:
    """Arredonda fator de capitalização para 8 casas decimais (ROUND_HALF_UP)."""
    return value.quantize(QUANTIZE_EIGHT, rounding=ROUND_HALF_UP)


def calculate_forward_compound_factor(
    annual_interest_rate_pct: Union[Decimal, str, int, float],
    business_days_du: int,
) -> Decimal:
    """Calcula o fator de capitalização exponencial (1 + i_aa)^(DU / 252) com precisão de 50 dígitos.

    Args:
        annual_interest_rate_pct: Taxa de juros efetiva anual em percentual (ex.: 12.6825 para 12,6825% a.a.).
        business_days_du: Número de dias úteis (DU) entre a contratação e o vencimento.

    Returns:
        Fator de capitalização arredondado em 8 casas decimais.
    """
    rate_pct = _to_decimal(annual_interest_rate_pct, "annual_interest_rate_pct")
    if rate_pct < Decimal("0"):
        raise EquityForwardValidationError(
            f"A taxa de juros anual 'annual_interest_rate_pct' não pode ser negativa: {rate_pct}"
        )
    du = _to_int(business_days_du, "business_days_du")
    if du <= 0:
        raise EquityForwardValidationError(
            f"O prazo em dias úteis 'business_days_du' deve ser estritamente positivo: {du}"
        )

    rate_decimal = rate_pct / Decimal("100")
    base = Decimal("1") + rate_decimal
    if rate_decimal == Decimal("0"):
        return Decimal("1.00000000")
    if du == 252:
        return _quantize_eight(base)

    with localcontext() as ctx:
        ctx.prec = 50
        exponent = Decimal(du) / BUSINESS_DAYS_YEAR
        raw_factor = (base.ln() * exponent).exp()
        return _quantize_eight(raw_factor)


def calculate_forward_unit_price(
    spot_price_brl: Union[Decimal, str, int, float],
    annual_interest_rate_pct: Union[Decimal, str, int, float],
    business_days_du: int,
) -> Decimal:
    """Calcula o Preço Unitário a Termo exato segundo a equação da B3:

    P_termo = round_2(P_vista * (1 + i_aa)^(DU / 252))
    """
    spot = _to_decimal(spot_price_brl, "spot_price_brl")
    if spot <= Decimal("0"):
        raise EquityForwardValidationError(
            f"O preço à vista 'spot_price_brl' deve ser estritamente positivo: {spot}"
        )
    rate_pct = _to_decimal(annual_interest_rate_pct, "annual_interest_rate_pct")
    if rate_pct < Decimal("0"):
        raise EquityForwardValidationError(
            f"A taxa de juros anual 'annual_interest_rate_pct' não pode ser negativa: {rate_pct}"
        )
    du = _to_int(business_days_du, "business_days_du")
    if du <= 0:
        raise EquityForwardValidationError(
            f"O prazo em dias úteis 'business_days_du' deve ser estritamente positivo: {du}"
        )

    rate_decimal = rate_pct / Decimal("100")
    base = Decimal("1") + rate_decimal
    with localcontext() as ctx:
        ctx.prec = 50
        exponent = Decimal(du) / BUSINESS_DAYS_YEAR
        factor = Decimal("1") if rate_decimal == Decimal("0") else (base.ln() * exponent).exp()
        return _quantize_money(spot * factor)


# ---------------------------------------------------------------------------
# Enumerações de Domínio
# ---------------------------------------------------------------------------

class ForwardModality(str, Enum):
    """Modalidade do contrato a termo de ações na Câmara B3."""

    TRADITIONAL_BRL = "TRADITIONAL_BRL"  # Termo Tradicional em Reais
    IN_POINTS = "IN_POINTS"              # Termo em Pontos (referenciado em câmbio PTAX ou índice)
    FLEXIBLE = "FLEXIBLE"                # Termo Flexível (permite substituição do ativo-objeto na 2306-6)

    @classmethod
    def from_str(cls, value: Any) -> ForwardModality:
        if isinstance(value, ForwardModality):
            return value
        normalized = str(value).strip().upper()
        mapping = {
            "TRADITIONAL_BRL": cls.TRADITIONAL_BRL,
            "TRADITIONAL": cls.TRADITIONAL_BRL,
            "TERMO_REAIS": cls.TRADITIONAL_BRL,
            "REAIS": cls.TRADITIONAL_BRL,
            "IN_POINTS": cls.IN_POINTS,
            "POINTS": cls.IN_POINTS,
            "TERMO_PONTOS": cls.IN_POINTS,
            "PONTOS": cls.IN_POINTS,
            "FLEXIBLE": cls.FLEXIBLE,
            "TERMO_FLEXIVEL": cls.FLEXIBLE,
            "FLEXIVEL": cls.FLEXIBLE,
        }
        if normalized in mapping:
            return mapping[normalized]
        raise EquityForwardValidationError(
            f"Modalidade de termo inválida: {value!r}. Aceitos: TRADITIONAL_BRL, IN_POINTS, FLEXIBLE."
        )


class ForwardLifecycleStatus(str, Enum):
    """Status do ciclo de vida do contrato a termo de ações na Câmara B3."""

    ACTIVE = "ACTIVE"                            # Contrato vigente com 100% da quantidade original em aberto
    PARTIALLY_SETTLED = "PARTIALLY_SETTLED"      # Liquidação antecipada parcial já ocorrida, saldo remanescente aberto
    SETTLED_EARLY = "SETTLED_EARLY"              # Contrato 100% encerrado por liquidação antecipada do comprador
    SETTLED_AT_MATURITY = "SETTLED_AT_MATURITY"  # Contrato encerrado por decurso de prazo no vencimento
    ROLLED_OVER = "ROLLED_OVER"                  # Contrato encerrado mediante rolagem para novo vencimento

    @classmethod
    def from_str(cls, value: Any) -> ForwardLifecycleStatus:
        if isinstance(value, ForwardLifecycleStatus):
            return value
        normalized = str(value).strip().upper()
        for member in cls:
            if member.value == normalized:
                return member
        raise EquityForwardValidationError(
            f"Status de ciclo de vida de termo inválido: {value!r}."
        )


class ForwardCoverageStatus(str, Enum):
    """Status de enquadramento da cobertura física do vendedor na carteira 2306-6."""

    FULLY_COVERED = "FULLY_COVERED"        # 100% da quantidade em aberto depositada na carteira 2306-6
    RELEASED_SETTLED = "RELEASED_SETTLED"  # Contrato liquidado (open_quantity == 0 e cobertura liberada/entregue)
    DEFICIENT = "DEFICIENT"                # Cobertura insuficiente (seller_covered_quantity < open_quantity)

    @classmethod
    def from_str(cls, value: Any) -> ForwardCoverageStatus:
        if isinstance(value, ForwardCoverageStatus):
            return value
        normalized = str(value).strip().upper()
        for member in cls:
            if member.value == normalized:
                return member
        raise EquityForwardValidationError(
            f"Status de cobertura de termo inválido: {value!r}."
        )


# ---------------------------------------------------------------------------
# Modelos de Eventos e Contratos a Termo
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FlexibleSubstitutionRecord:
    """Registro de substituição de ativo-objeto na carteira 2306-6 para Termo Flexível."""

    substitution_date: date
    previous_ticker: str
    previous_isin: str
    new_ticker: str
    new_isin: str
    substituted_quantity: int
    retained_proceeds_brl: Decimal
    new_collateral_value_brl: Decimal

    def __post_init__(self) -> None:
        prev_t = _require_non_empty_str(self.previous_ticker, "previous_ticker").upper()
        new_t = _require_non_empty_str(self.new_ticker, "new_ticker").upper()
        prev_i = _require_non_empty_str(self.previous_isin, "previous_isin").upper()
        new_i = _require_non_empty_str(self.new_isin, "new_isin").upper()
        if prev_t == new_t:
            raise EquityForwardValidationError(
                f"Substituição em Termo Flexível exige novo ativo diferente do anterior ({prev_t})."
            )
        qty = _to_int(self.substituted_quantity, "substituted_quantity")
        if qty <= 0:
            raise EquityForwardValidationError(
                f"Quantidade substituída deve ser positiva: {qty}"
            )
        proceeds = _quantize_money(_to_decimal(self.retained_proceeds_brl, "retained_proceeds_brl"))
        new_val = _quantize_money(_to_decimal(self.new_collateral_value_brl, "new_collateral_value_brl"))
        if proceeds <= Decimal("0.00") or new_val <= Decimal("0.00"):
            raise EquityForwardValidationError(
                "Valores financeiros da substituição de Termo Flexível devem ser positivos."
            )
        if new_val < proceeds:
            raise EquityForwardValidationError(
                f"Valor da nova cobertura ({new_val}) inferior aos recursos retidos da venda ({proceeds})."
            )
        object.__setattr__(self, "substitution_date", _to_date(self.substitution_date, "substitution_date"))
        object.__setattr__(self, "previous_ticker", prev_t)
        object.__setattr__(self, "previous_isin", prev_i)
        object.__setattr__(self, "new_ticker", new_t)
        object.__setattr__(self, "new_isin", new_i)
        object.__setattr__(self, "substituted_quantity", qty)
        object.__setattr__(self, "retained_proceeds_brl", proceeds)
        object.__setattr__(self, "new_collateral_value_brl", new_val)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FlexibleSubstitutionRecord:
        return cls(
            substitution_date=_to_date(data.get("substitution_date"), "substitution_date"),
            previous_ticker=_require_non_empty_str(data.get("previous_ticker"), "previous_ticker"),
            previous_isin=_require_non_empty_str(data.get("previous_isin"), "previous_isin"),
            new_ticker=_require_non_empty_str(data.get("new_ticker"), "new_ticker"),
            new_isin=_require_non_empty_str(data.get("new_isin"), "new_isin"),
            substituted_quantity=_to_int(data.get("substituted_quantity"), "substituted_quantity"),
            retained_proceeds_brl=_to_decimal(data.get("retained_proceeds_brl"), "retained_proceeds_brl"),
            new_collateral_value_brl=_to_decimal(data.get("new_collateral_value_brl"), "new_collateral_value_brl"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "substitution_date": self.substitution_date.isoformat(),
            "previous_ticker": self.previous_ticker,
            "previous_isin": self.previous_isin,
            "new_ticker": self.new_ticker,
            "new_isin": self.new_isin,
            "substituted_quantity": self.substituted_quantity,
            "retained_proceeds_brl": str(self.retained_proceeds_brl),
            "new_collateral_value_brl": str(self.new_collateral_value_brl),
        }


@dataclass(frozen=True)
class ForwardSettlementEvent:
    """Evento de liquidação ou ciclo de vida de um contrato a termo de ações (FL-041).

    Tipos suportados (`event_type`):
    - `EARLY_SETTLEMENT`: Liquidação antecipada total ou parcial solicitada pelo comprador.
    - `MATURITY_SETTLEMENT`: Liquidação por decurso de prazo na data de vencimento.
    - `ROLLOVER`: Liquidação vinculada à rolagem para um novo contrato a termo.
    """

    event_id: str
    contract_id: str
    event_type: str
    event_date: date
    settled_quantity: int
    settlement_unit_price_brl: Decimal
    financial_amount_brl: Decimal
    requested_by: str = "BUYER"
    delivered_ticker: str = ""
    source_wallet_code: str = CUSTODY_WALLET_FORWARD_COVERAGE
    target_wallet_code: str = CUSTODY_WALLET_FREE_SPOT
    discount_applied: bool = False
    rolled_into_contract_id: Optional[str] = None

    def __post_init__(self) -> None:
        ev_id = _require_non_empty_str(self.event_id, "event_id")
        cid = _require_non_empty_str(self.contract_id, "contract_id")
        ev_type = _require_non_empty_str(self.event_type, "event_type").upper()
        valid_types = {"EARLY_SETTLEMENT", "MATURITY_SETTLEMENT", "ROLLOVER"}
        if ev_type not in valid_types:
            raise EquityForwardValidationError(
                f"Tipo de evento de liquidação inválido: {ev_type!r}. Aceitos: {sorted(valid_types)}"
            )
        ev_date = _to_date(self.event_date, "event_date")
        qty = _to_int(self.settled_quantity, "settled_quantity")
        if qty <= 0:
            raise EquityForwardValidationError(
                f"A quantidade liquidada no evento '{ev_id}' deve ser estritamente positiva: {qty}"
            )
        unit_price = _quantize_money(
            _to_decimal(self.settlement_unit_price_brl, "settlement_unit_price_brl")
        )
        if unit_price <= Decimal("0.00"):
            raise EquityForwardValidationError(
                f"O preço unitário de liquidação deve ser positivo: {unit_price}"
            )
        expected_financial = _quantize_money(Decimal(qty) * unit_price)
        raw_financial = _quantize_money(
            _to_decimal(self.financial_amount_brl, "financial_amount_brl")
        )
        if raw_financial != expected_financial:
            raise EquityForwardValidationError(
                f"Divergência financeira no evento '{ev_id}': "
                f"informado {raw_financial}, esperado {expected_financial} ({qty} * {unit_price})."
            )
        req_by = _require_non_empty_str(self.requested_by, "requested_by").upper()
        if ev_type == "EARLY_SETTLEMENT" and req_by != "BUYER":
            raise EquityForwardValidationError(
                f"Liquidação antecipada ('EARLY_SETTLEMENT') só pode ser solicitada pelo comprador ('BUYER'), "
                f"recebido: {req_by!r}."
            )
        src_wallet = _require_non_empty_str(self.source_wallet_code, "source_wallet_code")
        tgt_wallet = _require_non_empty_str(self.target_wallet_code, "target_wallet_code")
        deliv_ticker = self.delivered_ticker.strip().upper() if self.delivered_ticker else ""

        object.__setattr__(self, "event_id", ev_id)
        object.__setattr__(self, "contract_id", cid)
        object.__setattr__(self, "event_type", ev_type)
        object.__setattr__(self, "event_date", ev_date)
        object.__setattr__(self, "settled_quantity", qty)
        object.__setattr__(self, "settlement_unit_price_brl", unit_price)
        object.__setattr__(self, "financial_amount_brl", raw_financial)
        object.__setattr__(self, "requested_by", req_by)
        object.__setattr__(self, "delivered_ticker", deliv_ticker)
        object.__setattr__(self, "source_wallet_code", src_wallet)
        object.__setattr__(self, "target_wallet_code", tgt_wallet)

    @property
    def buyer_debit_brl(self) -> Decimal:
        """Débito financeiro multilateral do comprador no IMBARQ004 (negativo)."""
        return -self.financial_amount_brl

    @property
    def seller_credit_brl(self) -> Decimal:
        """Crédito financeiro multilateral do vendedor no IMBARQ004 (positivo)."""
        return self.financial_amount_brl

    @property
    def is_zero_sum_cashflow(self) -> bool:
        """Invariante financeira bilateral: débito do comprador + crédito do vendedor == 0.00."""
        return (self.buyer_debit_brl + self.seller_credit_brl) == Decimal("0.00")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ForwardSettlementEvent:
        unit_price = _quantize_money(
            _to_decimal(data.get("settlement_unit_price_brl"), "settlement_unit_price_brl")
        )
        qty = _to_int(data.get("settled_quantity"), "settled_quantity")
        fin_raw = data.get("financial_amount_brl")
        fin_amount = (
            _quantize_money(_to_decimal(fin_raw, "financial_amount_brl"))
            if fin_raw is not None
            else _quantize_money(Decimal(qty) * unit_price)
        )
        return cls(
            event_id=_require_non_empty_str(data.get("event_id"), "event_id"),
            contract_id=_require_non_empty_str(data.get("contract_id"), "contract_id"),
            event_type=_require_non_empty_str(data.get("event_type"), "event_type"),
            event_date=_to_date(data.get("event_date"), "event_date"),
            settled_quantity=qty,
            settlement_unit_price_brl=unit_price,
            financial_amount_brl=fin_amount,
            requested_by=str(data.get("requested_by", "BUYER")),
            delivered_ticker=str(data.get("delivered_ticker", "")),
            source_wallet_code=str(data.get("source_wallet_code", CUSTODY_WALLET_FORWARD_COVERAGE)),
            target_wallet_code=str(data.get("target_wallet_code", CUSTODY_WALLET_FREE_SPOT)),
            discount_applied=bool(data.get("discount_applied", False)),
            rolled_into_contract_id=data.get("rolled_into_contract_id"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "contract_id": self.contract_id,
            "event_type": self.event_type,
            "event_date": self.event_date.isoformat(),
            "settled_quantity": self.settled_quantity,
            "settlement_unit_price_brl": str(self.settlement_unit_price_brl),
            "financial_amount_brl": str(self.financial_amount_brl),
            "buyer_debit_brl": str(self.buyer_debit_brl),
            "seller_credit_brl": str(self.seller_credit_brl),
            "requested_by": self.requested_by,
            "delivered_ticker": self.delivered_ticker,
            "source_wallet_code": self.source_wallet_code,
            "target_wallet_code": self.target_wallet_code,
            "discount_applied": self.discount_applied,
            "rolled_into_contract_id": self.rolled_into_contract_id,
        }


@dataclass
class EquityForwardContract:
    """Contrato a Termo de Ações registrado e compensado na Câmara B3 (FL-041)."""

    contract_id: str
    ticker: str
    underlying_ticker: str
    underlying_isin: str
    modality: ForwardModality
    trade_date: date
    maturity_date: date
    calendar_days_dc: int
    business_days_du: int
    spot_price_brl: Decimal
    annual_interest_rate_pct: Decimal
    forward_unit_price_brl: Decimal
    original_quantity: int
    early_settled_quantity: int
    maturity_settled_quantity: int
    open_quantity: int
    seller_participant_code: str
    seller_investor_account: str
    seller_covered_quantity: int
    buyer_participant_code: str
    buyer_investor_account: str
    current_market_price_brl: Decimal
    core_haircut_pct: Decimal
    buyer_deposited_collateral_brl: Decimal
    custody_wallet_code: str = CUSTODY_WALLET_FORWARD_COVERAGE
    current_underlying_ticker: Optional[str] = None
    current_underlying_isin: Optional[str] = None
    points_index_name: Optional[str] = None
    initial_index_quote: Optional[Decimal] = None
    settlement_index_quote: Optional[Decimal] = None
    forward_price_points: Optional[Decimal] = None
    rolled_into_contract_id: Optional[str] = None
    lifecycle_status: ForwardLifecycleStatus = ForwardLifecycleStatus.ACTIVE
    coverage_status: ForwardCoverageStatus = ForwardCoverageStatus.FULLY_COVERED
    substitution_history: List[FlexibleSubstitutionRecord] = field(default_factory=list)
    settlement_events: List[ForwardSettlementEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.contract_id = _require_non_empty_str(self.contract_id, "contract_id")
        self.ticker = _require_non_empty_str(self.ticker, "ticker").upper()
        self.underlying_ticker = _require_non_empty_str(
            self.underlying_ticker, "underlying_ticker"
        ).upper()
        self.underlying_isin = _require_non_empty_str(
            self.underlying_isin, "underlying_isin"
        ).upper()
        if len(self.underlying_isin) != 12:
            raise EquityForwardValidationError(
                f"ISIN inválido '{self.underlying_isin}' no contrato '{self.contract_id}' (esperado 12 caracteres)."
            )

        self.modality = ForwardModality.from_str(self.modality)
        self.trade_date = _to_date(self.trade_date, "trade_date")
        self.maturity_date = _to_date(self.maturity_date, "maturity_date")

        actual_dc = (self.maturity_date - self.trade_date).days
        self.calendar_days_dc = _to_int(self.calendar_days_dc, "calendar_days_dc")
        if self.calendar_days_dc != actual_dc:
            raise EquityForwardValidationError(
                f"Prazo em dias corridos ('calendar_days_dc'={self.calendar_days_dc}) diverge da diferença "
                f"entre maturity_date ({self.maturity_date}) e trade_date ({self.trade_date}): {actual_dc} dias."
            )
        if (
            self.calendar_days_dc < MIN_FORWARD_CALENDAR_DAYS
            or self.calendar_days_dc > MAX_FORWARD_CALENDAR_DAYS
        ):
            raise ForwardTermRangeError(
                f"Prazo do contrato a termo '{self.contract_id}' ({self.calendar_days_dc} dias corridos) "
                f"fora do intervalo regulamentar B3 [{MIN_FORWARD_CALENDAR_DAYS}, {MAX_FORWARD_CALENDAR_DAYS}]."
            )

        self.business_days_du = _to_int(self.business_days_du, "business_days_du")
        if self.business_days_du <= 0 or self.business_days_du > self.calendar_days_dc:
            raise EquityForwardValidationError(
                f"Prazo em dias úteis ('business_days_du'={self.business_days_du}) inválido para "
                f"{self.calendar_days_dc} dias corridos."
            )

        self.spot_price_brl = _quantize_money(_to_decimal(self.spot_price_brl, "spot_price_brl"))
        if self.spot_price_brl <= Decimal("0.00"):
            raise EquityForwardValidationError(
                f"Preço à vista deve ser positivo: {self.spot_price_brl}"
            )

        self.annual_interest_rate_pct = _quantize_four(
            _to_decimal(self.annual_interest_rate_pct, "annual_interest_rate_pct")
        )
        if self.annual_interest_rate_pct < Decimal("0.0000"):
            raise EquityForwardValidationError(
                f"Taxa anual não pode ser negativa: {self.annual_interest_rate_pct}"
            )

        expected_forward_price = calculate_forward_unit_price(
            self.spot_price_brl,
            self.annual_interest_rate_pct,
            self.business_days_du,
        )
        self.forward_unit_price_brl = _quantize_money(
            _to_decimal(self.forward_unit_price_brl, "forward_unit_price_brl")
        )
        if self.forward_unit_price_brl != expected_forward_price:
            raise EquityForwardValidationError(
                f"Preço unitário a termo inválido no contrato '{self.contract_id}': "
                f"informado {self.forward_unit_price_brl}, calculado via DU/252 = {expected_forward_price}."
            )

        self.original_quantity = _to_int(self.original_quantity, "original_quantity")
        self.early_settled_quantity = _to_int(self.early_settled_quantity, "early_settled_quantity")
        self.maturity_settled_quantity = _to_int(
            self.maturity_settled_quantity, "maturity_settled_quantity"
        )
        self.open_quantity = _to_int(self.open_quantity, "open_quantity")

        if self.original_quantity <= 0:
            raise EquityForwardValidationError(
                f"A quantidade original deve ser estritamente positiva: {self.original_quantity}"
            )
        if (
            self.early_settled_quantity < 0
            or self.maturity_settled_quantity < 0
            or self.open_quantity < 0
        ):
            raise EquityForwardValidationError(
                "As quantidades liquidada antecipadamente, no vencimento e em aberto não podem ser negativas."
            )

        self._validate_quantity_conservation()

        self.seller_participant_code = _require_non_empty_str(
            self.seller_participant_code, "seller_participant_code"
        )
        self.seller_investor_account = _require_non_empty_str(
            self.seller_investor_account, "seller_investor_account"
        )
        self.seller_covered_quantity = _to_int(
            self.seller_covered_quantity, "seller_covered_quantity"
        )
        self.custody_wallet_code = _require_non_empty_str(
            self.custody_wallet_code, "custody_wallet_code"
        )
        if self.custody_wallet_code != CUSTODY_WALLET_FORWARD_COVERAGE:
            raise EquityForwardValidationError(
                f"Carteira de cobertura do vendedor inválida: {self.custody_wallet_code!r}. "
                f"Esperado carteira de cobertura B3 '{CUSTODY_WALLET_FORWARD_COVERAGE}'."
            )

        self.buyer_participant_code = _require_non_empty_str(
            self.buyer_participant_code, "buyer_participant_code"
        )
        self.buyer_investor_account = _require_non_empty_str(
            self.buyer_investor_account, "buyer_investor_account"
        )

        self.current_market_price_brl = _quantize_money(
            _to_decimal(self.current_market_price_brl, "current_market_price_brl")
        )
        if self.current_market_price_brl <= Decimal("0.00"):
            raise EquityForwardValidationError(
                f"Preço de mercado atual deve ser positivo: {self.current_market_price_brl}"
            )

        self.core_haircut_pct = _quantize_four(
            _to_decimal(self.core_haircut_pct, "core_haircut_pct")
        )
        if self.core_haircut_pct <= Decimal("0.0000") or self.core_haircut_pct >= Decimal("100.0000"):
            raise EquityForwardValidationError(
                f"Deságio CORE ('core_haircut_pct') deve estar entre 0% e 100%: {self.core_haircut_pct}"
            )

        self.buyer_deposited_collateral_brl = _quantize_money(
            _to_decimal(self.buyer_deposited_collateral_brl, "buyer_deposited_collateral_brl")
        )
        if self.buyer_deposited_collateral_brl < Decimal("0.00"):
            raise EquityForwardValidationError(
                f"Colateral depositado pelo comprador não pode ser negativo: {self.buyer_deposited_collateral_brl}"
            )

        if self.current_underlying_ticker:
            self.current_underlying_ticker = self.current_underlying_ticker.strip().upper()
        else:
            self.current_underlying_ticker = self.underlying_ticker

        if self.current_underlying_isin:
            self.current_underlying_isin = self.current_underlying_isin.strip().upper()
        else:
            self.current_underlying_isin = self.underlying_isin

        if (
            self.current_underlying_ticker != self.underlying_ticker
            and self.modality != ForwardModality.FLEXIBLE
        ):
            raise InvalidModalityOperationError(
                f"Substituição de ativo-objeto ({self.underlying_ticker} -> {self.current_underlying_ticker}) "
                f"só é permitida na modalidade FLEXIBLE (modalidade atual: {self.modality.value})."
            )

        if self.modality == ForwardModality.IN_POINTS:
            self.points_index_name = _require_non_empty_str(
                self.points_index_name, "points_index_name"
            ).upper()
            self.initial_index_quote = _quantize_four(
                _to_decimal(self.initial_index_quote, "initial_index_quote")
            )
            if self.initial_index_quote <= Decimal("0.0000"):
                raise EquityForwardValidationError(
                    f"Cotação inicial do indexador em pontos deve ser positiva: {self.initial_index_quote}"
                )
            if self.settlement_index_quote is not None:
                self.settlement_index_quote = _quantize_four(
                    _to_decimal(self.settlement_index_quote, "settlement_index_quote")
                )
                if self.settlement_index_quote <= Decimal("0.0000"):
                    raise EquityForwardValidationError(
                        f"Cotação de liquidação do indexador em pontos deve ser positiva: {self.settlement_index_quote}"
                    )
            else:
                self.settlement_index_quote = self.initial_index_quote

            expected_points = _quantize_four(
                self.forward_unit_price_brl / self.initial_index_quote
            )
            if self.forward_price_points is not None:
                self.forward_price_points = _quantize_four(
                    _to_decimal(self.forward_price_points, "forward_price_points")
                )
                if self.forward_price_points != expected_points:
                    raise EquityForwardValidationError(
                        f"Preço em pontos ('forward_price_points'={self.forward_price_points}) diverge de "
                        f"P_termo / cotação_inicial = {expected_points}."
                    )
            else:
                self.forward_price_points = expected_points

        self.lifecycle_status = ForwardLifecycleStatus.from_str(self.lifecycle_status)
        self.coverage_status = ForwardCoverageStatus.from_str(self.coverage_status)

        self._validate_seller_coverage()
        self._validate_buyer_core_margin()

    def _validate_quantity_conservation(self) -> None:
        """Garante a invariante Q_original == Q_antecipada + Q_vencimento + Q_em_aberto."""
        total_accounted = (
            self.early_settled_quantity
            + self.maturity_settled_quantity
            + self.open_quantity
        )
        if total_accounted != self.original_quantity:
            raise ForwardQuantityConservationError(
                f"Violação de conservação de quantidade no contrato '{self.contract_id}': "
                f"original={self.original_quantity}, "
                f"antecipada={self.early_settled_quantity} + "
                f"vencimento={self.maturity_settled_quantity} + "
                f"em_aberto={self.open_quantity} (soma={total_accounted})."
            )

    def _validate_seller_coverage(self) -> None:
        """Valida que o vendedor possui 100% de cobertura física na carteira 2306-6 para o saldo em aberto."""
        if self.open_quantity > 0:
            if self.seller_covered_quantity < self.open_quantity:
                self.coverage_status = ForwardCoverageStatus.DEFICIENT
                raise InsufficientSellerCoverageError(
                    f"Cobertura insuficiente na carteira {self.custody_wallet_code} para o contrato "
                    f"'{self.contract_id}': coberto={self.seller_covered_quantity}, "
                    f"exigido (100% em aberto)={self.open_quantity}."
                )
            if self.seller_covered_quantity > self.open_quantity:
                raise InsufficientSellerCoverageError(
                    f"Quantidade bloqueada na carteira {self.custody_wallet_code} ({self.seller_covered_quantity}) "
                    f"excede a quantidade em aberto ({self.open_quantity}) no contrato '{self.contract_id}'."
                )
            self.coverage_status = ForwardCoverageStatus.FULLY_COVERED
        else:
            if self.seller_covered_quantity != 0:
                raise InsufficientSellerCoverageError(
                    f"Contrato liquidado '{self.contract_id}' (open_quantity=0) ainda retém "
                    f"{self.seller_covered_quantity} ações bloqueadas na carteira {self.custody_wallet_code}."
                )
            self.coverage_status = ForwardCoverageStatus.RELEASED_SETTLED

    def _validate_buyer_core_margin(self) -> None:
        """Valida que o comprador possui colateral suficiente para cobrir a Margem Requerida CORE."""
        required_margin = self.buyer_required_core_margin_brl
        if self.open_quantity > 0 and self.buyer_deposited_collateral_brl < required_margin:
            raise InsufficientBuyerMarginError(
                f"Margem CORE insuficiente para o comprador no contrato '{self.contract_id}': "
                f"depositado R$ {self.buyer_deposited_collateral_brl}, "
                f"requerido CORE R$ {required_margin}."
            )

    @property
    def compound_factor_du252(self) -> Decimal:
        """Fator de capitalização exponencial (1 + i_aa)^(DU / 252)."""
        return calculate_forward_compound_factor(
            self.annual_interest_rate_pct, self.business_days_du
        )

    @property
    def effective_settlement_unit_price_brl(self) -> Decimal:
        """Preço unitário de liquidação efetivo em BRL (considera cotação do ponto se IN_POINTS)."""
        if (
            self.modality == ForwardModality.IN_POINTS
            and self.forward_price_points is not None
            and self.settlement_index_quote is not None
        ):
            return _quantize_money(self.forward_price_points * self.settlement_index_quote)
        return self.forward_unit_price_brl

    @property
    def original_notional_brl(self) -> Decimal:
        """Valor financeiro original contratado a termo: round_2(Q_original * P_termo)."""
        return _quantize_money(Decimal(self.original_quantity) * self.forward_unit_price_brl)

    @property
    def open_notional_brl(self) -> Decimal:
        """Valor financeiro em aberto pelo preço efetivo a termo."""
        return _quantize_money(
            Decimal(self.open_quantity) * self.effective_settlement_unit_price_brl
        )

    @property
    def buyer_mtm_exposure_brl(self) -> Decimal:
        """Perda potencial de marcação a mercado do comprador sobre o saldo em aberto."""
        if self.open_quantity == 0:
            return Decimal("0.00")
        price_diff = self.effective_settlement_unit_price_brl - self.current_market_price_brl
        if price_diff <= Decimal("0.00"):
            return Decimal("0.00")
        return _quantize_money(Decimal(self.open_quantity) * price_diff)

    @property
    def buyer_haircut_buffer_brl(self) -> Decimal:
        """Parcela de deságio (haircut CORE) sobre o valor de mercado da posição aberta."""
        if self.open_quantity == 0:
            return Decimal("0.00")
        haircut_rate = self.core_haircut_pct / Decimal("100")
        return _quantize_money(
            Decimal(self.open_quantity) * self.current_market_price_brl * haircut_rate
        )

    @property
    def buyer_required_core_margin_brl(self) -> Decimal:
        """Margem Requerida CORE do comprador para a quantidade em aberto."""
        if self.open_quantity == 0:
            return Decimal("0.00")
        return _quantize_money(self.buyer_mtm_exposure_brl + self.buyer_haircut_buffer_brl)

    @property
    def seller_coverage_ratio(self) -> Decimal:
        """Razão de cobertura física do vendedor (1.0000 == 100%)."""
        if self.open_quantity == 0:
            return Decimal("1.0000") if self.seller_covered_quantity == 0 else Decimal("0.0000")
        return _quantize_four(
            Decimal(self.seller_covered_quantity) / Decimal(self.open_quantity)
        )

    @property
    def is_quantity_conserved(self) -> bool:
        """Retorna True se Q_original == Q_antecipada + Q_vencimento + Q_em_aberto."""
        return (
            self.original_quantity
            == self.early_settled_quantity
            + self.maturity_settled_quantity
            + self.open_quantity
        )

    @property
    def is_seller_fully_covered(self) -> bool:
        """Retorna True se 100% da quantidade em aberto está coberta na carteira 2306-6."""
        return self.seller_covered_quantity == self.open_quantity

    @property
    def total_settled_financial_brl(self) -> Decimal:
        """Soma financeira de todos os eventos de liquidação registrados no contrato."""
        return _quantize_money(
            sum((ev.financial_amount_brl for ev in self.settlement_events), Decimal("0.00"))
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EquityForwardContract:
        subs = [
            FlexibleSubstitutionRecord.from_dict(item)
            for item in data.get("substitution_history", [])
        ]
        events = [
            ForwardSettlementEvent.from_dict(item)
            for item in data.get("settlement_events", [])
        ]
        spot = _to_decimal(data.get("spot_price_brl"), "spot_price_brl")
        rate = _to_decimal(data.get("annual_interest_rate_pct"), "annual_interest_rate_pct")
        du = _to_int(data.get("business_days_du"), "business_days_du")
        raw_fwd_price = data.get("forward_unit_price_brl")
        fwd_price = (
            _to_decimal(raw_fwd_price, "forward_unit_price_brl")
            if raw_fwd_price is not None
            else calculate_forward_unit_price(spot, rate, du)
        )
        t_date = _to_date(data.get("trade_date"), "trade_date")
        m_date = _to_date(data.get("maturity_date"), "maturity_date")
        raw_dc = data.get("calendar_days_dc")
        dc = _to_int(raw_dc, "calendar_days_dc") if raw_dc is not None else (m_date - t_date).days

        orig_qty = _to_int(data.get("original_quantity"), "original_quantity")
        early_qty = _to_int(data.get("early_settled_quantity", 0), "early_settled_quantity")
        mat_qty = _to_int(data.get("maturity_settled_quantity", 0), "maturity_settled_quantity")
        raw_open = data.get("open_quantity")
        open_qty = (
            _to_int(raw_open, "open_quantity")
            if raw_open is not None
            else orig_qty - early_qty - mat_qty
        )
        raw_cov = data.get("seller_covered_quantity")
        cov_qty = (
            _to_int(raw_cov, "seller_covered_quantity")
            if raw_cov is not None
            else open_qty
        )

        return cls(
            contract_id=_require_non_empty_str(data.get("contract_id"), "contract_id"),
            ticker=_require_non_empty_str(data.get("ticker"), "ticker"),
            underlying_ticker=_require_non_empty_str(
                data.get("underlying_ticker"), "underlying_ticker"
            ),
            underlying_isin=_require_non_empty_str(
                data.get("underlying_isin"), "underlying_isin"
            ),
            modality=ForwardModality.from_str(data.get("modality", "TRADITIONAL_BRL")),
            trade_date=t_date,
            maturity_date=m_date,
            calendar_days_dc=dc,
            business_days_du=du,
            spot_price_brl=spot,
            annual_interest_rate_pct=rate,
            forward_unit_price_brl=fwd_price,
            original_quantity=orig_qty,
            early_settled_quantity=early_qty,
            maturity_settled_quantity=mat_qty,
            open_quantity=open_qty,
            seller_participant_code=_require_non_empty_str(
                data.get("seller_participant_code"), "seller_participant_code"
            ),
            seller_investor_account=_require_non_empty_str(
                data.get("seller_investor_account"), "seller_investor_account"
            ),
            seller_covered_quantity=cov_qty,
            custody_wallet_code=str(
                data.get("custody_wallet_code", CUSTODY_WALLET_FORWARD_COVERAGE)
            ),
            buyer_participant_code=_require_non_empty_str(
                data.get("buyer_participant_code"), "buyer_participant_code"
            ),
            buyer_investor_account=_require_non_empty_str(
                data.get("buyer_investor_account"), "buyer_investor_account"
            ),
            current_market_price_brl=_to_decimal(
                data.get("current_market_price_brl", spot), "current_market_price_brl"
            ),
            core_haircut_pct=_to_decimal(
                data.get("core_haircut_pct", "15.0000"), "core_haircut_pct"
            ),
            buyer_deposited_collateral_brl=_to_decimal(
                data.get("buyer_deposited_collateral_brl"), "buyer_deposited_collateral_brl"
            ),
            current_underlying_ticker=data.get("current_underlying_ticker"),
            current_underlying_isin=data.get("current_underlying_isin"),
            points_index_name=data.get("points_index_name"),
            initial_index_quote=(
                _to_decimal(data["initial_index_quote"], "initial_index_quote")
                if data.get("initial_index_quote") is not None
                else None
            ),
            settlement_index_quote=(
                _to_decimal(data["settlement_index_quote"], "settlement_index_quote")
                if data.get("settlement_index_quote") is not None
                else None
            ),
            forward_price_points=(
                _to_decimal(data["forward_price_points"], "forward_price_points")
                if data.get("forward_price_points") is not None
                else None
            ),
            rolled_into_contract_id=data.get("rolled_into_contract_id"),
            lifecycle_status=ForwardLifecycleStatus.from_str(
                data.get("lifecycle_status", "ACTIVE")
            ),
            coverage_status=ForwardCoverageStatus.from_str(
                data.get(
                    "coverage_status",
                    "FULLY_COVERED" if open_qty > 0 else "RELEASED_SETTLED",
                )
            ),
            substitution_history=subs,
            settlement_events=events,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "ticker": self.ticker,
            "underlying_ticker": self.underlying_ticker,
            "underlying_isin": self.underlying_isin,
            "current_underlying_ticker": self.current_underlying_ticker,
            "current_underlying_isin": self.current_underlying_isin,
            "modality": self.modality.value,
            "trade_date": self.trade_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "calendar_days_dc": self.calendar_days_dc,
            "business_days_du": self.business_days_du,
            "spot_price_brl": str(self.spot_price_brl),
            "annual_interest_rate_pct": str(self.annual_interest_rate_pct),
            "compound_factor_du252": str(self.compound_factor_du252),
            "forward_unit_price_brl": str(self.forward_unit_price_brl),
            "effective_settlement_unit_price_brl": str(self.effective_settlement_unit_price_brl),
            "points_index_name": self.points_index_name,
            "initial_index_quote": (
                str(self.initial_index_quote) if self.initial_index_quote is not None else None
            ),
            "settlement_index_quote": (
                str(self.settlement_index_quote) if self.settlement_index_quote is not None else None
            ),
            "forward_price_points": (
                str(self.forward_price_points) if self.forward_price_points is not None else None
            ),
            "original_quantity": self.original_quantity,
            "early_settled_quantity": self.early_settled_quantity,
            "maturity_settled_quantity": self.maturity_settled_quantity,
            "open_quantity": self.open_quantity,
            "original_notional_brl": str(self.original_notional_brl),
            "open_notional_brl": str(self.open_notional_brl),
            "seller_participant_code": self.seller_participant_code,
            "seller_investor_account": self.seller_investor_account,
            "seller_covered_quantity": self.seller_covered_quantity,
            "seller_coverage_ratio": str(self.seller_coverage_ratio),
            "custody_wallet_code": self.custody_wallet_code,
            "buyer_participant_code": self.buyer_participant_code,
            "buyer_investor_account": self.buyer_investor_account,
            "current_market_price_brl": str(self.current_market_price_brl),
            "core_haircut_pct": str(self.core_haircut_pct),
            "buyer_required_core_margin_brl": str(self.buyer_required_core_margin_brl),
            "buyer_deposited_collateral_brl": str(self.buyer_deposited_collateral_brl),
            "lifecycle_status": self.lifecycle_status.value,
            "coverage_status": self.coverage_status.value,
            "rolled_into_contract_id": self.rolled_into_contract_id,
            "is_quantity_conserved": self.is_quantity_conserved,
            "is_seller_fully_covered": self.is_seller_fully_covered,
            "total_settled_financial_brl": str(self.total_settled_financial_brl),
            "substitution_history": [s.to_dict() for s in self.substitution_history],
            "settlement_events": [e.to_dict() for e in self.settlement_events],
        }


# ---------------------------------------------------------------------------
# Resumo Consolidado da Carteira de Mercado a Termo
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EquityForwardSummary:
    """Sumário executivo e verificação de invariantes da carteira de Termo de Ações (FL-041)."""

    total_contracts: int
    traditional_brl_count: int
    in_points_count: int
    flexible_count: int
    active_or_partial_count: int
    settled_count: int
    total_original_quantity: int
    total_early_settled_quantity: int
    total_maturity_settled_quantity: int
    total_open_quantity: int
    total_seller_covered_quantity: int
    total_original_notional_brl: Decimal
    total_open_notional_brl: Decimal
    total_settled_financial_brl: Decimal
    total_buyer_required_core_margin_brl: Decimal
    total_buyer_deposited_collateral_brl: Decimal
    all_quantity_conserved: bool
    all_seller_100pct_covered: bool
    all_terms_within_b3_limits: bool
    all_zero_sum_settlements: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_contracts": self.total_contracts,
            "traditional_brl_count": self.traditional_brl_count,
            "in_points_count": self.in_points_count,
            "flexible_count": self.flexible_count,
            "active_or_partial_count": self.active_or_partial_count,
            "settled_count": self.settled_count,
            "total_original_quantity": self.total_original_quantity,
            "total_early_settled_quantity": self.total_early_settled_quantity,
            "total_maturity_settled_quantity": self.total_maturity_settled_quantity,
            "total_open_quantity": self.total_open_quantity,
            "total_seller_covered_quantity": self.total_seller_covered_quantity,
            "total_original_notional_brl": str(self.total_original_notional_brl),
            "total_open_notional_brl": str(self.total_open_notional_brl),
            "total_settled_financial_brl": str(self.total_settled_financial_brl),
            "total_buyer_required_core_margin_brl": str(self.total_buyer_required_core_margin_brl),
            "total_buyer_deposited_collateral_brl": str(self.total_buyer_deposited_collateral_brl),
            "all_quantity_conserved": self.all_quantity_conserved,
            "all_seller_100pct_covered": self.all_seller_100pct_covered,
            "all_terms_within_b3_limits": self.all_terms_within_b3_limits,
            "all_zero_sum_settlements": self.all_zero_sum_settlements,
        }


# ---------------------------------------------------------------------------
# Motor Determinístico de Mercado a Termo de Ações (EquityForwardEngine)
# ---------------------------------------------------------------------------

class EquityForwardEngine:
    """Motor de registro, cobertura 2306-6, substituição flexível e liquidação de Termo de Ações B3."""

    def __init__(self) -> None:
        self._contracts: Dict[str, EquityForwardContract] = {}

    @property
    def contracts(self) -> Tuple[EquityForwardContract, ...]:
        """Retorna todos os contratos a termo registrados na ordem de inserção."""
        return tuple(self._contracts.values())

    def get_contract(self, contract_id: str) -> EquityForwardContract:
        """Obtém um contrato a termo pelo seu `contract_id`."""
        if contract_id not in self._contracts:
            raise ForwardContractNotFoundError(
                f"Contrato a termo '{contract_id}' não encontrado na Câmara B3."
            )
        return self._contracts[contract_id]

    def register_contract(self, contract: EquityForwardContract) -> EquityForwardContract:
        """Registra um contrato a termo validando prazos (16..999), DU/252, cobertura 2306-6 e margem CORE."""
        if contract.contract_id in self._contracts:
            raise DuplicateForwardContractError(
                f"Contrato a termo '{contract.contract_id}' já registrado."
            )
        contract._validate_quantity_conservation()
        contract._validate_seller_coverage()
        contract._validate_buyer_core_margin()
        self._contracts[contract.contract_id] = contract
        return contract

    def substitute_underlying(
        self,
        contract_id: str,
        *,
        substitution_date: Union[date, str],
        new_ticker: str,
        new_isin: str,
        retained_proceeds_brl: Union[Decimal, str, int, float],
        new_collateral_value_brl: Union[Decimal, str, int, float],
    ) -> FlexibleSubstitutionRecord:
        """Executa a substituição do ativo-objeto na carteira 2306-6 (exclusivo para Termo Flexível).

        O vendedor vende o ativo original à vista (recursos retidos pela Câmara B3) e adquire
        novo ativo elegível de valor igual ou superior para compor a carteira 2306-6.
        """
        contract = self.get_contract(contract_id)
        if contract.modality != ForwardModality.FLEXIBLE:
            raise InvalidModalityOperationError(
                f"Contrato '{contract_id}' pertence à modalidade {contract.modality.value}; "
                "apenas contratos FLEXIBLE permitem substituição do ativo-objeto."
            )
        if contract.open_quantity <= 0:
            raise EquityForwardValidationError(
                f"Não é possível substituir ativo-objeto no contrato '{contract_id}' sem saldo em aberto."
            )
        sub_date = _to_date(substitution_date, "substitution_date")
        if sub_date < contract.trade_date or sub_date >= contract.maturity_date:
            raise EquityForwardValidationError(
                f"Data de substituição ({sub_date}) deve estar entre a contratação "
                f"({contract.trade_date}) e antes do vencimento ({contract.maturity_date})."
            )

        record = FlexibleSubstitutionRecord(
            substitution_date=sub_date,
            previous_ticker=contract.current_underlying_ticker or contract.underlying_ticker,
            previous_isin=contract.current_underlying_isin or contract.underlying_isin,
            new_ticker=new_ticker,
            new_isin=new_isin,
            substituted_quantity=contract.open_quantity,
            retained_proceeds_brl=_to_decimal(retained_proceeds_brl, "retained_proceeds_brl"),
            new_collateral_value_brl=_to_decimal(new_collateral_value_brl, "new_collateral_value_brl"),
        )
        contract.current_underlying_ticker = record.new_ticker
        contract.current_underlying_isin = record.new_isin
        contract.substitution_history.append(record)
        contract._validate_seller_coverage()
        return record

    def settle_early(
        self,
        contract_id: str,
        *,
        event_id: str,
        settlement_date: Union[date, str],
        quantity: int,
        requested_by: str = "BUYER",
        agreed_unit_price_brl: Optional[Union[Decimal, str, int, float]] = None,
    ) -> ForwardSettlementEvent:
        """Processa Liquidação Antecipada Total ou Parcial (`EARLY_SETTLEMENT`) solicitada pelo comprador.

        Pela regra da Câmara B3, a liquidação antecipada ocorre pelo valor integral do Preço a Termo
        contratado (sem desconto pro-rata de juros), salvo se houver repactuação expressa (`agreed_unit_price_brl`).
        """
        contract = self.get_contract(contract_id)
        if contract.open_quantity <= 0:
            raise EquityForwardValidationError(
                f"Contrato '{contract_id}' já está integralmente liquidado (open_quantity=0)."
            )
        ev_date = _to_date(settlement_date, "settlement_date")
        if ev_date < contract.trade_date or ev_date >= contract.maturity_date:
            raise EquityForwardValidationError(
                f"Data de liquidação antecipada ({ev_date}) deve ser maior ou igual à data de contratação "
                f"({contract.trade_date}) e estritamente anterior ao vencimento ({contract.maturity_date})."
            )

        qty = _to_int(quantity, "quantity")
        if qty <= 0 or qty > contract.open_quantity:
            raise EquityForwardValidationError(
                f"Quantidade para liquidação antecipada ({qty}) inválida frente ao saldo em aberto "
                f"({contract.open_quantity}) no contrato '{contract_id}'."
            )

        if agreed_unit_price_brl is not None:
            unit_price = _quantize_money(_to_decimal(agreed_unit_price_brl, "agreed_unit_price_brl"))
            discount_applied = unit_price < contract.effective_settlement_unit_price_brl
        else:
            unit_price = contract.effective_settlement_unit_price_brl
            discount_applied = False

        financial_amount = _quantize_money(Decimal(qty) * unit_price)
        event = ForwardSettlementEvent(
            event_id=event_id,
            contract_id=contract.contract_id,
            event_type="EARLY_SETTLEMENT",
            event_date=ev_date,
            settled_quantity=qty,
            settlement_unit_price_brl=unit_price,
            financial_amount_brl=financial_amount,
            requested_by=requested_by,
            delivered_ticker=contract.current_underlying_ticker or contract.underlying_ticker,
            source_wallet_code=contract.custody_wallet_code,
            target_wallet_code=CUSTODY_WALLET_FREE_SPOT,
            discount_applied=discount_applied,
        )

        contract.early_settled_quantity += qty
        contract.open_quantity -= qty
        contract.seller_covered_quantity -= qty
        contract.settlement_events.append(event)

        if contract.open_quantity == 0:
            contract.lifecycle_status = ForwardLifecycleStatus.SETTLED_EARLY
            contract.coverage_status = ForwardCoverageStatus.RELEASED_SETTLED
        else:
            contract.lifecycle_status = ForwardLifecycleStatus.PARTIALLY_SETTLED
            contract.coverage_status = ForwardCoverageStatus.FULLY_COVERED

        contract._validate_quantity_conservation()
        contract._validate_seller_coverage()
        return event

    def settle_at_maturity(
        self,
        contract_id: str,
        *,
        event_id: str,
        settlement_index_quote: Optional[Union[Decimal, str, int, float]] = None,
    ) -> ForwardSettlementEvent:
        """Processa a Liquidação no Vencimento (`MATURITY_SETTLEMENT`) para o saldo em aberto."""
        contract = self.get_contract(contract_id)
        if contract.open_quantity <= 0:
            raise EquityForwardValidationError(
                f"Contrato '{contract_id}' não possui quantidade em aberto para liquidação no vencimento."
            )

        if settlement_index_quote is not None:
            if contract.modality != ForwardModality.IN_POINTS:
                raise InvalidModalityOperationError(
                    f"Cotação de indexador em pontos informada para contrato '{contract_id}' "
                    f"de modalidade {contract.modality.value}."
                )
            contract.settlement_index_quote = _quantize_four(
                _to_decimal(settlement_index_quote, "settlement_index_quote")
            )

        qty = contract.open_quantity
        unit_price = contract.effective_settlement_unit_price_brl
        financial_amount = _quantize_money(Decimal(qty) * unit_price)

        event = ForwardSettlementEvent(
            event_id=event_id,
            contract_id=contract.contract_id,
            event_type="MATURITY_SETTLEMENT",
            event_date=contract.maturity_date,
            settled_quantity=qty,
            settlement_unit_price_brl=unit_price,
            financial_amount_brl=financial_amount,
            requested_by="CLEARING_MATURITY",
            delivered_ticker=contract.current_underlying_ticker or contract.underlying_ticker,
            source_wallet_code=contract.custody_wallet_code,
            target_wallet_code=CUSTODY_WALLET_FREE_SPOT,
            discount_applied=False,
        )

        contract.maturity_settled_quantity += qty
        contract.open_quantity = 0
        contract.seller_covered_quantity = 0
        contract.lifecycle_status = ForwardLifecycleStatus.SETTLED_AT_MATURITY
        contract.coverage_status = ForwardCoverageStatus.RELEASED_SETTLED
        contract.settlement_events.append(event)

        contract._validate_quantity_conservation()
        contract._validate_seller_coverage()
        return event

    def rollover_contract(
        self,
        contract_id: str,
        *,
        event_id: str,
        new_contract_id: str,
        new_ticker: str,
        new_maturity_date: Union[date, str],
        new_business_days_du: int,
        new_annual_interest_rate_pct: Union[Decimal, str, int, float],
        new_spot_price_brl: Optional[Union[Decimal, str, int, float]] = None,
    ) -> Tuple[ForwardSettlementEvent, EquityForwardContract]:
        """Executa a Rolagem (`ROLLOVER`) de um contrato a termo vincendo para um novo vencimento.

        Liquida integralmente o saldo em aberto do contrato atual na data de vencimento e
        registra simultaneamente o novo contrato a termo com prazo regulamentar (16..999 DC)
        e cobertura integral de 100% na carteira 2306-6.
        """
        contract = self.get_contract(contract_id)
        if contract.open_quantity <= 0:
            raise EquityForwardValidationError(
                f"Contrato '{contract_id}' não possui saldo em aberto para rolagem."
            )

        rollover_qty = contract.open_quantity
        unit_price = contract.effective_settlement_unit_price_brl
        financial_amount = _quantize_money(Decimal(rollover_qty) * unit_price)

        rollover_trade_date = contract.maturity_date
        target_maturity = _to_date(new_maturity_date, "new_maturity_date")
        new_dc = (target_maturity - rollover_trade_date).days
        new_du = _to_int(new_business_days_du, "new_business_days_du")
        new_rate = _quantize_four(
            _to_decimal(new_annual_interest_rate_pct, "new_annual_interest_rate_pct")
        )
        base_spot = (
            _quantize_money(_to_decimal(new_spot_price_brl, "new_spot_price_brl"))
            if new_spot_price_brl is not None
            else unit_price
        )
        new_fwd_price = calculate_forward_unit_price(base_spot, new_rate, new_du)

        active_ticker = contract.current_underlying_ticker or contract.underlying_ticker
        active_isin = contract.current_underlying_isin or contract.underlying_isin

        new_contract = EquityForwardContract(
            contract_id=new_contract_id,
            ticker=new_ticker,
            underlying_ticker=active_ticker,
            underlying_isin=active_isin,
            modality=contract.modality,
            trade_date=rollover_trade_date,
            maturity_date=target_maturity,
            calendar_days_dc=new_dc,
            business_days_du=new_du,
            spot_price_brl=base_spot,
            annual_interest_rate_pct=new_rate,
            forward_unit_price_brl=new_fwd_price,
            original_quantity=rollover_qty,
            early_settled_quantity=0,
            maturity_settled_quantity=0,
            open_quantity=rollover_qty,
            seller_participant_code=contract.seller_participant_code,
            seller_investor_account=contract.seller_investor_account,
            seller_covered_quantity=rollover_qty,
            custody_wallet_code=contract.custody_wallet_code,
            buyer_participant_code=contract.buyer_participant_code,
            buyer_investor_account=contract.buyer_investor_account,
            current_market_price_brl=contract.current_market_price_brl,
            core_haircut_pct=contract.core_haircut_pct,
            buyer_deposited_collateral_brl=contract.buyer_deposited_collateral_brl,
            points_index_name=contract.points_index_name,
            initial_index_quote=contract.settlement_index_quote,
            settlement_index_quote=contract.settlement_index_quote,
            lifecycle_status=ForwardLifecycleStatus.ACTIVE,
            coverage_status=ForwardCoverageStatus.FULLY_COVERED,
        )
        self.register_contract(new_contract)

        event = ForwardSettlementEvent(
            event_id=event_id,
            contract_id=contract.contract_id,
            event_type="ROLLOVER",
            event_date=rollover_trade_date,
            settled_quantity=rollover_qty,
            settlement_unit_price_brl=unit_price,
            financial_amount_brl=financial_amount,
            requested_by="BUYER",
            delivered_ticker=active_ticker,
            source_wallet_code=contract.custody_wallet_code,
            target_wallet_code=contract.custody_wallet_code,
            discount_applied=False,
            rolled_into_contract_id=new_contract.contract_id,
        )

        contract.maturity_settled_quantity += rollover_qty
        contract.open_quantity = 0
        contract.seller_covered_quantity = 0
        contract.rolled_into_contract_id = new_contract.contract_id
        contract.lifecycle_status = ForwardLifecycleStatus.ROLLED_OVER
        contract.coverage_status = ForwardCoverageStatus.RELEASED_SETTLED
        contract.settlement_events.append(event)

        contract._validate_quantity_conservation()
        contract._validate_seller_coverage()
        return event, new_contract

    def summarize(self) -> EquityForwardSummary:
        """Consolida métricas e invariantes de todos os contratos a termo da carteira."""
        contracts = list(self._contracts.values())
        trad_count = sum(1 for c in contracts if c.modality == ForwardModality.TRADITIONAL_BRL)
        pts_count = sum(1 for c in contracts if c.modality == ForwardModality.IN_POINTS)
        flex_count = sum(1 for c in contracts if c.modality == ForwardModality.FLEXIBLE)

        active_or_partial = sum(1 for c in contracts if c.open_quantity > 0)
        settled_count = sum(1 for c in contracts if c.open_quantity == 0)

        tot_orig = sum(c.original_quantity for c in contracts)
        tot_early = sum(c.early_settled_quantity for c in contracts)
        tot_mat = sum(c.maturity_settled_quantity for c in contracts)
        tot_open = sum(c.open_quantity for c in contracts)
        tot_cov = sum(c.seller_covered_quantity for c in contracts)

        tot_orig_notional = _quantize_money(
            sum((c.original_notional_brl for c in contracts), Decimal("0.00"))
        )
        tot_open_notional = _quantize_money(
            sum((c.open_notional_brl for c in contracts), Decimal("0.00"))
        )
        tot_settled_fin = _quantize_money(
            sum((c.total_settled_financial_brl for c in contracts), Decimal("0.00"))
        )
        tot_req_margin = _quantize_money(
            sum((c.buyer_required_core_margin_brl for c in contracts), Decimal("0.00"))
        )
        tot_dep_collat = _quantize_money(
            sum((c.buyer_deposited_collateral_brl for c in contracts), Decimal("0.00"))
        )

        all_conserved = all(c.is_quantity_conserved for c in contracts) and (
            tot_orig == tot_early + tot_mat + tot_open
        )
        all_covered = all(c.is_seller_fully_covered for c in contracts) and (
            tot_cov == tot_open
        )
        all_terms_ok = all(
            MIN_FORWARD_CALENDAR_DAYS <= c.calendar_days_dc <= MAX_FORWARD_CALENDAR_DAYS
            for c in contracts
        )
        all_zero_sum = all(
            ev.is_zero_sum_cashflow
            for c in contracts
            for ev in c.settlement_events
        )

        return EquityForwardSummary(
            total_contracts=len(contracts),
            traditional_brl_count=trad_count,
            in_points_count=pts_count,
            flexible_count=flex_count,
            active_or_partial_count=active_or_partial,
            settled_count=settled_count,
            total_original_quantity=tot_orig,
            total_early_settled_quantity=tot_early,
            total_maturity_settled_quantity=tot_mat,
            total_open_quantity=tot_open,
            total_seller_covered_quantity=tot_cov,
            total_original_notional_brl=tot_orig_notional,
            total_open_notional_brl=tot_open_notional,
            total_settled_financial_brl=tot_settled_fin,
            total_buyer_required_core_margin_brl=tot_req_margin,
            total_buyer_deposited_collateral_brl=tot_dep_collat,
            all_quantity_conserved=all_conserved,
            all_seller_100pct_covered=all_covered,
            all_terms_within_b3_limits=all_terms_ok,
            all_zero_sum_settlements=all_zero_sum,
        )

    @classmethod
    def from_json_fixture(cls, path: Union[str, Path]) -> EquityForwardEngine:
        """Carrega carteira de contratos a termo a partir de arquivo JSON de fixture."""
        fixture_path = Path(path)
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        engine = cls()
        for item in raw.get("contracts", []):
            engine.register_contract(EquityForwardContract.from_dict(item))
        return engine


__all__ = [
    "BUSINESS_DAYS_YEAR",
    "CUSTODY_WALLET_FORWARD_COVERAGE",
    "CUSTODY_WALLET_FREE_SPOT",
    "DuplicateForwardContractError",
    "EquityForwardContract",
    "EquityForwardEngine",
    "EquityForwardError",
    "EquityForwardSummary",
    "EquityForwardValidationError",
    "FlexibleSubstitutionRecord",
    "ForwardContractNotFoundError",
    "ForwardCoverageStatus",
    "ForwardLifecycleStatus",
    "ForwardModality",
    "ForwardQuantityConservationError",
    "ForwardSettlementEvent",
    "ForwardTermRangeError",
    "InsufficientBuyerMarginError",
    "InsufficientSellerCoverageError",
    "InvalidModalityOperationError",
    "MAX_FORWARD_CALENDAR_DAYS",
    "MIN_FORWARD_CALENDAR_DAYS",
    "QUANTIZE_CENTS",
    "QUANTIZE_EIGHT",
    "QUANTIZE_FOUR",
    "calculate_forward_compound_factor",
    "calculate_forward_unit_price",
]
