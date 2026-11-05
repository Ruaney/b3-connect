"""Motor de Empréstimo de Cotas de Fundos Imobiliários (FII) e ETFs na Câmara B3 (FL-084).

Implementação 100% pura em Python Standard Library (sem dependências externas)
para o ciclo completo de empréstimo de cotas de FII e ETFs (Ações e Renda Fixa)
no Sistema BTC (Banco de Títulos CBLC) da Câmara B3:
- Registro e alocação atômica de contratos de aluguel (Doador e Tomador).
- Cálculo determinístico de remuneração pró-rata die sob convenção DU/252.
- Repasse compulsório de proventos (dividendos mensais de FIIs e rendimentos de ETFs)
  com compensação financeira integral ao doador.
- Devolução antecipada ou liquidação no vencimento com entrega física das cotas
  na custódia da Central Depositária.
- Invariantes formais:
  * Conservação de Cotas (LAW-084): Cotas_Doador == Cotas_Emprestadas_Ativas + Cotas_Devolvidas.
  * Paridade no Repasse de Proventos (LAW-085): Provento_Recebido_Tomador == Provento_Repassado_Doador (centavo a centavo).
  * Conservação Multilateral de Custódia (LAW-001/BTC): Total_Lent_Active == Total_Borrowed_Active.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

try:
    from src.invariants import (
        assert_btc_dividend_forwarding_parity,
        assert_fii_etf_lending_conservation,
        check,
        InvariantViolationError,
    )
except ImportError:
    try:
        from invariants import (  # type: ignore
            assert_btc_dividend_forwarding_parity,
            assert_fii_etf_lending_conservation,
            check,
            InvariantViolationError,
        )
    except ImportError:
        class InvariantViolationError(AssertionError):  # type: ignore
            pass

        def check(condition: bool, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:  # type: ignore
            if not condition:
                raise InvariantViolationError(message)

        def assert_fii_etf_lending_conservation(  # type: ignore
            lender_total_contract_qty: Union[int, Decimal],
            active_lent_qty: Union[int, Decimal],
            returned_cum_qty: Union[int, Decimal],
            symbol: str = "",
            contract_id: str = "",
        ) -> None:
            total_calc = active_lent_qty + returned_cum_qty
            check(
                lender_total_contract_qty == total_calc,
                f"Violação da conservação de cotas no contrato '{contract_id}' ativo '{symbol}': "
                f"{lender_total_contract_qty} != {active_lent_qty} + {returned_cum_qty}",
                law_code="LAW-084",
            )

        def assert_btc_dividend_forwarding_parity(  # type: ignore
            borrower_debit: Decimal,
            lender_credit: Decimal,
            symbol: str = "",
            event_id: str = "",
            contract_id: str = "",
        ) -> None:
            check(
                borrower_debit == lender_credit,
                f"Violação da paridade de repasse de proventos no contrato '{contract_id}': "
                f"Débito Tomador ({borrower_debit}) != Crédito Doador ({lender_credit})",
                law_code="LAW-085",
            )


# ==============================================================================
# 1. Constantes Numéricas e Calendário B3 (DU/252)
# ==============================================================================

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_RATE = Decimal("0.00000001")
QUANTIZE_PRICE = Decimal("0.01")
DAYS_PER_YEAR_DU = Decimal("252")

# Feriados bancários nacionais fixos (mês, dia) para contagem de dias úteis B3
DEFAULT_BRAZILIAN_HOLIDAYS: Set[Tuple[int, int]] = {
    (1, 1),    # Confraternização Universal
    (4, 21),   # Tiradentes
    (5, 1),    # Dia do Trabalho
    (9, 7),    # Independência do Brasil
    (10, 12),  # Nossa Senhora Aparecida
    (11, 2),   # Finados
    (11, 15),  # Proclamação da República
    (11, 20),  # Dia da Consciência Negra
    (12, 25),  # Natal
}


def round_cents(val: Decimal) -> Decimal:
    """Arredonda valor financeiro para centavos (2 decimais) via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)


def to_decimal(val: Any, field_name: str = "valor") -> Decimal:
    """Converte valor para Decimal de forma determinística e segura."""
    if isinstance(val, Decimal):
        return val
    if isinstance(val, bool) or val is None:
        raise FiiEtfValidationError(f"Valor numérico inválido para '{field_name}': {val!r}")
    try:
        cleaned = str(val).strip().replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError) as err:
        raise FiiEtfValidationError(
            f"Valor decimal inválido para '{field_name}': {val!r}"
        ) from err


def to_date(val: Any, field_name: str = "data") -> date:
    """Converte string ISO 'YYYY-MM-DD' ou datetime/date para date."""
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if not val or not isinstance(val, str):
        raise FiiEtfValidationError(f"Data inválida ou vazia para '{field_name}': {val!r}")
    try:
        return date.fromisoformat(val.strip())
    except ValueError as err:
        raise FiiEtfValidationError(
            f"Formato de data inválido para '{field_name}' ('{val}'). Use AAAA-MM-DD."
        ) from err


def to_int(val: Any, field_name: str = "quantidade") -> int:
    """Converte valor para inteiro estrito (rejeitando números fracionários)."""
    if isinstance(val, bool) or val is None:
        raise FiiEtfValidationError(f"Inteiro inválido para '{field_name}': {val!r}")
    if isinstance(val, int):
        return val
    if isinstance(val, Decimal):
        if val != val.to_integral_value():
            raise FiiEtfValidationError(f"O campo '{field_name}' deve ser um inteiro exato: {val}")
        return int(val)
    if isinstance(val, str):
        cleaned = val.strip()
        try:
            d = Decimal(cleaned)
            if d != d.to_integral_value():
                raise FiiEtfValidationError(f"O campo '{field_name}' deve ser um inteiro exato: {val}")
            return int(d)
        except InvalidOperation as err:
            raise FiiEtfValidationError(f"Inteiro inválido para '{field_name}': {val!r}") from err
    raise FiiEtfValidationError(f"Tipo não suportado para '{field_name}': {type(val).__name__}")


def is_business_day(d: date, holidays: Optional[Set[Tuple[int, int]]] = None) -> bool:
    """Retorna True se d for dia útil de negociação bancária e bolsa B3."""
    if d.weekday() >= 5:  # Sábado (5) ou Domingo (6)
        return False
    active_holidays = holidays if holidays is not None else DEFAULT_BRAZILIAN_HOLIDAYS
    return (d.month, d.day) not in active_holidays


def count_business_days(
    start_date: date,
    end_date: date,
    holidays: Optional[Set[Tuple[int, int]]] = None,
) -> int:
    """Conta dias úteis (DU) entre start_date e end_date (exclusivo start, inclusivo end)."""
    if end_date < start_date:
        raise FiiEtfValidationError(
            f"Data final ({end_date}) anterior à data inicial ({start_date})."
        )
    if start_date == end_date:
        return 0

    curr = start_date + timedelta(days=1)
    du = 0
    while curr <= end_date:
        if is_business_day(curr, holidays):
            du += 1
        curr += timedelta(days=1)
    return du


def add_business_days(
    start_date: date,
    n: int,
    holidays: Optional[Set[Tuple[int, int]]] = None,
) -> date:
    """Adiciona n dias úteis a uma data inicial."""
    if n < 0:
        raise FiiEtfValidationError("Quantidade de dias úteis deve ser >= 0.")
    curr = start_date
    added = 0
    while added < n:
        curr += timedelta(days=1)
        if is_business_day(curr, holidays):
            added += 1
    return curr


# ==============================================================================
# 2. Enumerações Canônicas
# ==============================================================================

class AssetType(str, Enum):
    """Tipologia de cotas elegíveis para empréstimo BTC (FL-084)."""
    FII = "FII"                                  # Fundo de Investimento Imobiliário (ex: HGLG11)
    ETF_EQUITY = "ETF_EQUITY"                    # ETF de Renda Variável / Ações (ex: BOVA11)
    ETF_FIXED_INCOME = "ETF_FIXED_INCOME"        # ETF de Renda Fixa (ex: IMAB11, B5P211)

    @classmethod
    def from_value(cls, val: Any) -> AssetType:
        if isinstance(val, AssetType):
            return val
        if isinstance(val, str):
            v = val.strip().upper()
            if v in ("FII", "FUNDO_IMOBILIARIO", "FUNDO_DE_INVESTIMENTO_IMOBILIARIO"):
                return cls.FII
            if v in ("ETF_EQUITY", "ETF_ACOES", "ETF_RENDA_VARIAVEL", "ETF"):
                return cls.ETF_EQUITY
            if v in ("ETF_FIXED_INCOME", "ETF_RENDA_FIXA", "ETF_RF"):
                return cls.ETF_FIXED_INCOME
        raise ValueError(f"Tipo de ativo de cotas inválido para BTC: {val!r}")


class ContractStatus(str, Enum):
    """Estado do contrato de empréstimo de cotas no BTC."""
    OPEN = "OPEN"                          # Em aberto / ativo
    PARTIALLY_RETURNED = "PARTIALLY_RETURNED"  # Parcialmente devolvido
    RETURNED = "RETURNED"                  # Totalmente devolvido / liquidado
    CANCELLED = "CANCELLED"                # Cancelado


class ReversibilityType(str, Enum):
    """Cláusula de reversibilidade do contrato BTC."""
    BORROWER_REVERSIBLE = "BORROWER_REVERSIBLE"  # Tomador pode devolver antecipadamente
    LENDER_REVERSIBLE = "LENDER_REVERSIBLE"      # Doador pode solicitar recall antecipado
    BOTH_REVERSIBLE = "BOTH_REVERSIBLE"          # Ambos podem solicitar liquidação antecipada
    FIXED_TERM = "FIXED_TERM"                    # Prazo fixo / não reversível antecipadamente

    @classmethod
    def from_value(cls, val: Any) -> ReversibilityType:
        if isinstance(val, ReversibilityType):
            return val
        if isinstance(val, str):
            v = val.strip().upper()
            if v in ("BORROWER_REVERSIBLE", "BORROWER", "TOMADOR", "T", "B"):
                return cls.BORROWER_REVERSIBLE
            if v in ("LENDER_REVERSIBLE", "LENDER", "DOADOR", "D", "L"):
                return cls.LENDER_REVERSIBLE
            if v in ("BOTH_REVERSIBLE", "BOTH", "AMBOS", "A"):
                return cls.BOTH_REVERSIBLE
            if v in ("FIXED_TERM", "FIXED", "FIXO", "N", "NAO_REVERSIVEL"):
                return cls.FIXED_TERM
        raise ValueError(f"Cláusula de reversibilidade inválida: {val!r}")


class DividendEventType(str, Enum):
    """Tipologia de eventos de proventos em cotas de FII e ETF."""
    FII_EARNINGS = "FII_EARNINGS"          # Rendimento mensal de FII (isento PF)
    ETF_INCOME = "ETF_INCOME"              # Rendimento distribuído por ETF
    AMORTIZATION = "AMORTIZATION"          # Amortização de principal da cota

    @classmethod
    def from_value(cls, val: Any) -> DividendEventType:
        if isinstance(val, DividendEventType):
            return val
        if isinstance(val, str):
            v = val.strip().upper()
            if v in ("FII_EARNINGS", "RENDIMENTO_FII", "DIVIDENDO_FII", "EARNINGS"):
                return cls.FII_EARNINGS
            if v in ("ETF_INCOME", "RENDIMENTO_ETF", "INCOME"):
                return cls.ETF_INCOME
            if v in ("AMORTIZATION", "AMORTIZACAO"):
                return cls.AMORTIZATION
        raise ValueError(f"Tipo de provento inválido: {val!r}")


# ==============================================================================
# 3. Exceções Especializadas
# ==============================================================================

class FiiEtfLendingError(Exception):
    """Exceção base para o motor de empréstimo de cotas FII/ETF (FL-084)."""
    pass


class FiiEtfValidationError(FiiEtfLendingError):
    """Erro de validação cadastral ou parâmetros de entrada."""
    pass


class InsufficientCustodyError(FiiEtfLendingError):
    """Saldo de custódia insuficiente para alocação ou devolução física."""
    pass


class ContractTermError(FiiEtfLendingError):
    """Violação de regras contratuais, prazos ou reversibilidade."""
    pass


class ContractNotFoundError(FiiEtfLendingError):
    """Contrato não encontrado no livro de aluguéis."""
    pass


class ContractConservationError(FiiEtfLendingError):
    """Violação de invariante formal de conservação ou integridade."""
    pass


# ==============================================================================
# 4. Modelos de Dados Tipados (Dataclasses)
# ==============================================================================

@dataclass
class CustodyPosition:
    """Posição escritural de custódia depositária de cotas na B3."""
    symbol: str
    isin: str
    available_qty: int = 0
    lent_active_qty: int = 0
    borrowed_active_qty: int = 0
    returned_cum_qty: int = 0

    def __post_init__(self) -> None:
        self.available_qty = to_int(self.available_qty, "available_qty")
        self.lent_active_qty = to_int(self.lent_active_qty, "lent_active_qty")
        self.borrowed_active_qty = to_int(self.borrowed_active_qty, "borrowed_active_qty")
        self.returned_cum_qty = to_int(self.returned_cum_qty, "returned_cum_qty")
        if self.available_qty < 0 or self.lent_active_qty < 0 or self.borrowed_active_qty < 0 or self.returned_cum_qty < 0:
            raise FiiEtfValidationError("Saldos de custódia não podem ser negativos.")

    @property
    def total_owned_qty(self) -> int:
        """Patrimônio total em propriedade (disponível + emprestado ativo)."""
        return self.available_qty + self.lent_active_qty

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "isin": self.isin,
            "available_qty": self.available_qty,
            "lent_active_qty": self.lent_active_qty,
            "borrowed_active_qty": self.borrowed_active_qty,
            "returned_cum_qty": self.returned_cum_qty,
            "total_owned_qty": self.total_owned_qty,
        }


@dataclass
class CustodyAccount:
    """Conta de custódia associada a um participante de liquidação."""
    participant_id: int
    account_id: int
    positions: Dict[str, CustodyPosition] = field(default_factory=dict)

    def get_position(self, symbol: str, isin: str = "") -> CustodyPosition:
        """Obtém ou inicializa a posição de custódia de um ativo."""
        sym = symbol.strip().upper()
        if sym not in self.positions:
            self.positions[sym] = CustodyPosition(symbol=sym, isin=isin)
        return self.positions[sym]

    def deposit(self, symbol: str, isin: str, quantity: int) -> None:
        """Deposita cotas livres na conta de custódia."""
        qty = to_int(quantity, "quantity")
        if qty <= 0:
            raise FiiEtfValidationError(f"Quantidade de depósito deve ser positiva: {qty}")
        pos = self.get_position(symbol, isin)
        pos.available_qty += qty

    def withdraw(self, symbol: str, quantity: int) -> None:
        """Retira cotas livres da conta de custódia."""
        qty = to_int(quantity, "quantity")
        if qty <= 0:
            raise FiiEtfValidationError(f"Quantidade de retirada deve ser positiva: {qty}")
        pos = self.get_position(symbol)
        if pos.available_qty < qty:
            raise InsufficientCustodyError(
                f"Saldo insuficiente na conta {self.account_id} ativo {symbol}: "
                f"disponível {pos.available_qty}, requerido {qty}"
            )
        pos.available_qty -= qty

    def to_dict(self) -> Dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "account_id": self.account_id,
            "positions": {sym: pos.to_dict() for sym, pos in self.positions.items()},
        }


@dataclass
class DividendAnnouncement:
    """Anúncio oficial de evento corporativo de proventos em cotas de FII ou ETF."""
    event_id: str
    symbol: str
    isin: str
    event_type: DividendEventType
    record_date: date
    ex_date: date
    payment_date: date
    rate_per_quota: Decimal
    tax_withholding_rate: Decimal = Decimal("0.0000")
    description: str = ""

    def __post_init__(self) -> None:
        self.symbol = self.symbol.strip().upper()
        self.isin = self.isin.strip().upper()
        self.event_type = DividendEventType.from_value(self.event_type)
        self.record_date = to_date(self.record_date, "record_date")
        self.ex_date = to_date(self.ex_date, "ex_date")
        self.payment_date = to_date(self.payment_date, "payment_date")
        self.rate_per_quota = to_decimal(self.rate_per_quota, "rate_per_quota")
        self.tax_withholding_rate = to_decimal(self.tax_withholding_rate, "tax_withholding_rate")

        if self.rate_per_quota <= Decimal("0"):
            raise FiiEtfValidationError(f"Valor do provento por cota deve ser positivo: {self.rate_per_quota}")
        if self.tax_withholding_rate < Decimal("0") or self.tax_withholding_rate > Decimal("1"):
            raise FiiEtfValidationError(
                f"Alíquota de retenção deve estar entre 0 e 1: {self.tax_withholding_rate}"
            )
        if self.ex_date <= self.record_date:
            raise FiiEtfValidationError(
                f"Data Ex ({self.ex_date}) deve ser posterior à Data Com / Record Date ({self.record_date})."
            )
        if self.payment_date < self.ex_date:
            raise FiiEtfValidationError(
                f"Data de pagamento ({self.payment_date}) não pode ser anterior à data ex ({self.ex_date})."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "symbol": self.symbol,
            "isin": self.isin,
            "event_type": self.event_type.value,
            "record_date": self.record_date.isoformat(),
            "ex_date": self.ex_date.isoformat(),
            "payment_date": self.payment_date.isoformat(),
            "rate_per_quota": str(self.rate_per_quota),
            "tax_withholding_rate": str(self.tax_withholding_rate),
            "description": self.description,
        }


@dataclass
class DividendSettlementReceipt:
    """Recibo de liquidação do repasse compulsório de provento BTC (LAW-085)."""
    receipt_id: str
    contract_id: str
    event_id: str
    symbol: str
    lender_participant_id: int
    lender_account: int
    borrower_participant_id: int
    borrower_account: int
    quantity_at_record_date: int
    rate_per_quota: Decimal
    gross_amount: Decimal
    withholding_tax: Decimal
    net_amount: Decimal
    borrower_debit: Decimal
    lender_credit: Decimal
    tax_compensation_credit: Decimal
    payment_date: date
    value_conserved: bool = True
    settlement_status: str = "SETTLED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "contract_id": self.contract_id,
            "event_id": self.event_id,
            "symbol": self.symbol,
            "lender_participant_id": self.lender_participant_id,
            "lender_account": self.lender_account,
            "borrower_participant_id": self.borrower_participant_id,
            "borrower_account": self.borrower_account,
            "quantity_at_record_date": self.quantity_at_record_date,
            "rate_per_quota": str(self.rate_per_quota),
            "gross_amount": str(self.gross_amount),
            "withholding_tax": str(self.withholding_tax),
            "net_amount": str(self.net_amount),
            "borrower_debit": str(self.borrower_debit),
            "lender_credit": str(self.lender_credit),
            "tax_compensation_credit": str(self.tax_compensation_credit),
            "payment_date": self.payment_date.isoformat(),
            "value_conserved": self.value_conserved,
            "settlement_status": self.settlement_status,
        }


@dataclass
class ReturnExecutionReceipt:
    """Recibo de devolução física de cotas e liquidação de remuneração de aluguel."""
    return_id: str
    contract_id: str
    symbol: str
    return_date: date
    returned_quantity: int
    remaining_quantity: int
    business_days: int
    rental_fee: Decimal
    is_early_return: bool
    is_final_settlement: bool
    lender_participant_id: int
    lender_account: int
    borrower_participant_id: int
    borrower_account: int
    status: str = "SETTLED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "return_id": self.return_id,
            "contract_id": self.contract_id,
            "symbol": self.symbol,
            "return_date": self.return_date.isoformat(),
            "returned_quantity": self.returned_quantity,
            "remaining_quantity": self.remaining_quantity,
            "business_days": self.business_days,
            "rental_fee": str(self.rental_fee),
            "is_early_return": self.is_early_return,
            "is_final_settlement": self.is_final_settlement,
            "lender_participant_id": self.lender_participant_id,
            "lender_account": self.lender_account,
            "borrower_participant_id": self.borrower_participant_id,
            "borrower_account": self.borrower_account,
            "status": self.status,
        }


@dataclass
class FiiEtfLoanContract:
    """Contrato de empréstimo de cotas FII/ETF formalizado no BTC B3."""
    contract_id: str
    asset_type: AssetType
    symbol: str
    isin: str
    lender_participant_id: int
    lender_account: int
    borrower_participant_id: int
    borrower_account: int
    quantity: int
    base_price: Decimal
    rental_rate: Decimal
    open_date: date
    maturity_date: date
    reversibility: ReversibilityType = ReversibilityType.BORROWER_REVERSIBLE
    status: ContractStatus = ContractStatus.OPEN
    returned_quantity: int = 0
    active_quantity: int = field(init=False)
    contract_value: Decimal = field(init=False)
    accrued_rental_fee: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.symbol = self.symbol.strip().upper()
        self.isin = self.isin.strip().upper()
        self.asset_type = AssetType.from_value(self.asset_type)
        self.reversibility = ReversibilityType.from_value(self.reversibility)
        self.status = ContractStatus(self.status) if isinstance(self.status, str) else self.status
        self.open_date = to_date(self.open_date, "open_date")
        self.maturity_date = to_date(self.maturity_date, "maturity_date")
        self.quantity = to_int(self.quantity, "quantity")
        self.returned_quantity = to_int(self.returned_quantity, "returned_quantity")
        self.base_price = to_decimal(self.base_price, "base_price")
        self.rental_rate = to_decimal(self.rental_rate, "rental_rate")

        if self.quantity <= 0:
            raise FiiEtfValidationError(f"Quantidade do contrato deve ser positiva: {self.quantity}")
        if self.returned_quantity < 0:
            raise FiiEtfValidationError(f"Quantidade devolvida não pode ser negativa: {self.returned_quantity}")
        if self.returned_quantity > self.quantity:
            raise FiiEtfValidationError(
                f"Quantidade devolvida ({self.returned_quantity}) não pode exceder total contratado ({self.quantity})."
            )
        if self.base_price <= Decimal("0"):
            raise FiiEtfValidationError(f"Preço base deve ser estritamente positivo: {self.base_price}")
        if self.rental_rate < Decimal("0"):
            raise FiiEtfValidationError(f"Taxa de remuneração não pode ser negativa: {self.rental_rate}")
        if self.maturity_date < self.open_date:
            raise FiiEtfValidationError(
                f"Data de vencimento ({self.maturity_date}) anterior à data de abertura ({self.open_date})."
            )

        self.active_quantity = self.quantity - self.returned_quantity
        self.contract_value = round_cents(Decimal(self.quantity) * self.base_price)

    @property
    def active_value(self) -> Decimal:
        """Valor financeiro das cotas ativas a preço base."""
        return round_cents(Decimal(self.active_quantity) * self.base_price)

    @property
    def is_open(self) -> bool:
        """Verifica se o contrato está ativo (OPEN ou PARTIALLY_RETURNED)."""
        return self.status in (ContractStatus.OPEN, ContractStatus.PARTIALLY_RETURNED)

    def calculate_accrued_rental(
        self,
        as_of_date: date,
        holidays: Optional[Set[Tuple[int, int]]] = None,
    ) -> Tuple[int, Decimal]:
        """Calcula juros acumulados pro-rata die DU/252 até uma data de referência."""
        ref_date = to_date(as_of_date, "as_of_date")
        if ref_date < self.open_date:
            raise FiiEtfValidationError(f"Data de referência ({ref_date}) anterior à abertura ({self.open_date}).")
        du = count_business_days(self.open_date, ref_date, holidays)
        if du == 0 or self.active_quantity == 0 or self.rental_rate == Decimal("0"):
            return du, Decimal("0.00")

        # Metodologia B3: Fator = (1 + r)^(DU/252) - 1
        t_exp = Decimal(du) / DAYS_PER_YEAR_DU
        factor = (Decimal("1") + self.rental_rate) ** t_exp - Decimal("1")
        fee = round_cents(self.active_value * factor)
        return du, fee

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "asset_type": self.asset_type.value,
            "symbol": self.symbol,
            "isin": self.isin,
            "lender_participant_id": self.lender_participant_id,
            "lender_account": self.lender_account,
            "borrower_participant_id": self.borrower_participant_id,
            "borrower_account": self.borrower_account,
            "quantity": self.quantity,
            "returned_quantity": self.returned_quantity,
            "active_quantity": self.active_quantity,
            "base_price": str(self.base_price),
            "rental_rate": str(self.rental_rate),
            "contract_value": str(self.contract_value),
            "active_value": str(self.active_value),
            "open_date": self.open_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "reversibility": self.reversibility.value,
            "status": self.status.value,
            "accrued_rental_fee": str(self.accrued_rental_fee),
        }


# ==============================================================================
# 5. Motor Central BTC: FII & ETF Lending Engine
# ==============================================================================

class FiiEtfLendingEngine:
    """Motor B3 para Empréstimo, Alocação, Proventos e Devolução de Cotas FII/ETF (FL-084)."""

    def __init__(self, holidays: Optional[Set[Tuple[int, int]]] = None) -> None:
        self.holidays: Set[Tuple[int, int]] = holidays if holidays is not None else DEFAULT_BRAZILIAN_HOLIDAYS
        self.contracts: Dict[str, FiiEtfLoanContract] = {}
        self.custody: Dict[Tuple[int, int], CustodyAccount] = {}
        self.dividend_receipts: List[DividendSettlementReceipt] = []
        self.return_receipts: List[ReturnExecutionReceipt] = []

    # --------------------------------------------------------------------------
    # Gestão de Custódia Depositária
    # --------------------------------------------------------------------------

    def get_or_create_account(self, participant_id: int, account_id: int) -> CustodyAccount:
        """Obtém ou cria uma conta de custódia na Central Depositária."""
        key = (participant_id, account_id)
        if key not in self.custody:
            self.custody[key] = CustodyAccount(participant_id=participant_id, account_id=account_id)
        return self.custody[key]

    def deposit_custody(
        self,
        participant_id: int,
        account_id: int,
        symbol: str,
        isin: str,
        quantity: int,
    ) -> CustodyPosition:
        """Realiza depósito inicial de cotas livres na conta de custódia."""
        account = self.get_or_create_account(participant_id, account_id)
        account.deposit(symbol=symbol, isin=isin, quantity=quantity)
        return account.get_position(symbol, isin)

    def get_custody_position(
        self,
        participant_id: int,
        account_id: int,
        symbol: str,
    ) -> CustodyPosition:
        """Consulta a posição de custódia de um participante/conta para um ativo."""
        account = self.get_or_create_account(participant_id, account_id)
        return account.get_position(symbol)

    # --------------------------------------------------------------------------
    # Registro e Alocação de Contratos
    # --------------------------------------------------------------------------

    def register_contract(self, contract: FiiEtfLoanContract) -> FiiEtfLoanContract:
        """Registra novo contrato de empréstimo e executa a alocação de custódia em D0."""
        cid = contract.contract_id.strip()
        if cid in self.contracts:
            raise FiiEtfValidationError(f"Contrato BTC com ID '{cid}' já registrado no sistema.")

        # Validações estritas de elegibilidade
        if contract.asset_type not in (AssetType.FII, AssetType.ETF_EQUITY, AssetType.ETF_FIXED_INCOME):
            raise FiiEtfValidationError(f"Ativo de tipo {contract.asset_type} não elegível para FL-084.")

        # Contas de doador e tomador não podem ser idênticas no mesmo participante
        if (
            contract.lender_participant_id == contract.borrower_participant_id
            and contract.lender_account == contract.borrower_account
        ):
            raise FiiEtfValidationError("Doador e Tomador não podem ser a mesma conta de custódia.")

        # Verificação prévia de custódia livre do Doador
        lender_acc = self.get_or_create_account(contract.lender_participant_id, contract.lender_account)
        lender_pos = lender_acc.get_position(contract.symbol, contract.isin)
        if lender_pos.available_qty < contract.quantity:
            raise InsufficientCustodyError(
                f"Custódia insuficiente do doador (Conta {contract.lender_account}): "
                f"disponível {lender_pos.available_qty}, requerido {contract.quantity}"
            )

        # Alocação fiduciária atômica na Central Depositária:
        # Doador: transfere da custódia disponível para empréstimo ativo
        lender_pos.available_qty -= contract.quantity
        lender_pos.lent_active_qty += contract.quantity

        # Tomador: recebe a posse física na custódia disponível e assume o passivo de empréstimo
        borrower_acc = self.get_or_create_account(contract.borrower_participant_id, contract.borrower_account)
        borrower_pos = borrower_acc.get_position(contract.symbol, contract.isin)
        borrower_pos.available_qty += contract.quantity
        borrower_pos.borrowed_active_qty += contract.quantity

        self.contracts[cid] = contract

        # Verificação imediata da invariante de conservação
        assert_fii_etf_lending_conservation(
            lender_total_contract_qty=contract.quantity,
            active_lent_qty=contract.active_quantity,
            returned_cum_qty=contract.returned_quantity,
            symbol=contract.symbol,
            contract_id=contract.contract_id,
        )

        return contract

    # --------------------------------------------------------------------------
    # Cálculo de Remuneração Pró-Rata Die DU/252
    # --------------------------------------------------------------------------

    def calculate_rental_fee(
        self,
        contract_id: str,
        return_date: date,
        quantity: Optional[int] = None,
    ) -> Tuple[int, Decimal]:
        """Calcula a remuneração de aluguel pró-rata die DU/252 sobre as cotas devolvidas."""
        cid = contract_id.strip()
        if cid not in self.contracts:
            raise ContractNotFoundError(f"Contrato BTC '{cid}' não encontrado.")
        contract = self.contracts[cid]

        r_date = to_date(return_date, "return_date")
        if r_date < contract.open_date:
            raise FiiEtfValidationError(f"Data de devolução ({r_date}) anterior à abertura ({contract.open_date}).")

        target_qty = contract.active_quantity if quantity is None else to_int(quantity, "quantity")
        if target_qty <= 0 or target_qty > contract.active_quantity:
            raise FiiEtfValidationError(
                f"Quantidade para cálculo de taxa inválida ({target_qty}). Cotas ativas: {contract.active_quantity}"
            )

        du = count_business_days(contract.open_date, r_date, self.holidays)
        if du == 0 or contract.rental_rate == Decimal("0"):
            return du, Decimal("0.00")

        # Volume financeiro correspondente à quantidade devolvida
        volume = round_cents(Decimal(target_qty) * contract.base_price)
        t_exp = Decimal(du) / DAYS_PER_YEAR_DU
        factor = (Decimal("1") + contract.rental_rate) ** t_exp - Decimal("1")
        fee = round_cents(volume * factor)
        return du, fee

    # --------------------------------------------------------------------------
    # Repasse Compulsório de Proventos (FII e ETF)
    # --------------------------------------------------------------------------

    def process_dividend(
        self,
        announcement: DividendAnnouncement,
    ) -> List[DividendSettlementReceipt]:
        """Executa o repasse compulsório de proventos para todos os contratos em aberto (LAW-085)."""
        receipts: List[DividendSettlementReceipt] = []

        for cid, contract in self.contracts.items():
            if contract.symbol != announcement.symbol:
                continue

            # Contrato deve estar ativo e ter sido aberto antes ou na record_date
            if not contract.is_open:
                continue
            if contract.open_date > announcement.record_date:
                continue
            if contract.maturity_date < announcement.record_date:
                continue
            if contract.active_quantity <= 0:
                continue

            qty = contract.active_quantity
            gross_amount = round_cents(Decimal(qty) * announcement.rate_per_quota)
            tax_amount = round_cents(gross_amount * announcement.tax_withholding_rate)
            net_amount = gross_amount - tax_amount

            # Regras da Câmara B3:
            # - Para FIIs (isenção fiscal PF): repasse integral bruto sem retenção.
            # - Para ETFs com tributação na fonte: o tomador é debitado pelo valor bruto/integral,
            #   o doador é mantido indene (compensação financeira integral).
            #   Invariante estrita: Débito Tomador == Crédito Doador centavo a centavo.
            if announcement.event_type == DividendEventType.FII_EARNINGS or announcement.tax_withholding_rate == Decimal("0"):
                borrower_debit = gross_amount
                lender_credit = gross_amount
                compensation_credit = Decimal("0.00")
            else:
                # Caso com retenção na fonte em ETF
                borrower_debit = net_amount
                lender_credit = net_amount
                compensation_credit = tax_amount

            assert_btc_dividend_forwarding_parity(
                borrower_debit=borrower_debit,
                lender_credit=lender_credit,
                symbol=contract.symbol,
                event_id=announcement.event_id,
                contract_id=contract.contract_id,
            )

            receipt_id = f"REC-PROV-{contract.contract_id}-{announcement.event_id}"
            receipt = DividendSettlementReceipt(
                receipt_id=receipt_id,
                contract_id=contract.contract_id,
                event_id=announcement.event_id,
                symbol=contract.symbol,
                lender_participant_id=contract.lender_participant_id,
                lender_account=contract.lender_account,
                borrower_participant_id=contract.borrower_participant_id,
                borrower_account=contract.borrower_account,
                quantity_at_record_date=qty,
                rate_per_quota=announcement.rate_per_quota,
                gross_amount=gross_amount,
                withholding_tax=tax_amount,
                net_amount=net_amount,
                borrower_debit=borrower_debit,
                lender_credit=lender_credit,
                tax_compensation_credit=compensation_credit,
                payment_date=announcement.payment_date,
                value_conserved=True,
                settlement_status="SETTLED",
            )

            receipts.append(receipt)
            self.dividend_receipts.append(receipt)

        return receipts

    # --------------------------------------------------------------------------
    # Devolução de Cotas (Antecipada ou Liquidação no Vencimento)
    # --------------------------------------------------------------------------

    def process_return(
        self,
        contract_id: str,
        return_date: date,
        quantity: Optional[int] = None,
    ) -> ReturnExecutionReceipt:
        """Processa a devolução física de cotas e liquidação de remuneração de aluguel."""
        cid = contract_id.strip()
        if cid not in self.contracts:
            raise ContractNotFoundError(f"Contrato BTC '{cid}' não encontrado.")
        contract = self.contracts[cid]

        if not contract.is_open:
            raise ContractTermError(f"Contrato '{cid}' não está em aberto para devolução (Status: {contract.status}).")

        r_date = to_date(return_date, "return_date")
        if r_date < contract.open_date:
            raise FiiEtfValidationError(f"Data de devolução ({r_date}) anterior à data de abertura ({contract.open_date}).")

        is_early_return = r_date < contract.maturity_date

        # Validação de reversibilidade para devolução antecipada
        if is_early_return and contract.reversibility == ReversibilityType.FIXED_TERM:
            raise ContractTermError(
                f"Contrato '{cid}' possui prazo fixo (FIXED_TERM). Devolução antecipada unilateral não permitida."
            )

        return_qty = contract.active_quantity if quantity is None else to_int(quantity, "quantity")
        if return_qty <= 0:
            raise FiiEtfValidationError(f"Quantidade de devolução deve ser positiva: {return_qty}")
        if return_qty > contract.active_quantity:
            raise FiiEtfValidationError(
                f"Quantidade de devolução ({return_qty}) excede o saldo ativo do contrato ({contract.active_quantity})."
            )

        # Validação de saldo de custódia disponível na conta do tomador (entrega física)
        borrower_acc = self.get_or_create_account(contract.borrower_participant_id, contract.borrower_account)
        borrower_pos = borrower_acc.get_position(contract.symbol, contract.isin)
        if borrower_pos.available_qty < return_qty:
            raise InsufficientCustodyError(
                f"Tomador (Conta {contract.borrower_account}) não possui saldo disponível para entrega física: "
                f"disponível {borrower_pos.available_qty}, requerido {return_qty}. Falha de liquidação BTC."
            )

        # Cálculo da remuneração pró-rata die DU/252 para a quantidade devolvida
        du, rental_fee = self.calculate_rental_fee(contract_id=cid, return_date=r_date, quantity=return_qty)

        # Transferência física escritural de custódia na Central Depositária:
        # Tomador devolve cotas
        borrower_pos.available_qty -= return_qty
        borrower_pos.borrowed_active_qty -= return_qty

        # Doador recebe cotas de volta
        lender_acc = self.get_or_create_account(contract.lender_participant_id, contract.lender_account)
        lender_pos = lender_acc.get_position(contract.symbol, contract.isin)
        lender_pos.available_qty += return_qty
        lender_pos.lent_active_qty -= return_qty
        lender_pos.returned_cum_qty += return_qty

        # Atualização do contrato
        contract.returned_quantity += return_qty
        contract.active_quantity -= return_qty
        contract.accrued_rental_fee += rental_fee

        is_final_settlement = contract.active_quantity == 0
        if is_final_settlement:
            contract.status = ContractStatus.RETURNED
        else:
            contract.status = ContractStatus.PARTIALLY_RETURNED

        # Verificação da invariante formal de conservação de cotas (LAW-084)
        assert_fii_etf_lending_conservation(
            lender_total_contract_qty=contract.quantity,
            active_lent_qty=contract.active_quantity,
            returned_cum_qty=contract.returned_quantity,
            symbol=contract.symbol,
            contract_id=contract.contract_id,
        )

        receipt_id = f"REC-DEV-{contract.contract_id}-{len(self.return_receipts) + 1:04d}"
        receipt = ReturnExecutionReceipt(
            return_id=receipt_id,
            contract_id=contract.contract_id,
            symbol=contract.symbol,
            return_date=r_date,
            returned_quantity=return_qty,
            remaining_quantity=contract.active_quantity,
            business_days=du,
            rental_fee=rental_fee,
            is_early_return=is_early_return,
            is_final_settlement=is_final_settlement,
            lender_participant_id=contract.lender_participant_id,
            lender_account=contract.lender_account,
            borrower_participant_id=contract.borrower_participant_id,
            borrower_account=contract.borrower_account,
            status="SETTLED",
        )

        self.return_receipts.append(receipt)
        return receipt

    # --------------------------------------------------------------------------
    # Verificação de Invariantes Formais do Sistema
    # --------------------------------------------------------------------------

    def verify_invariants(self) -> Dict[str, Any]:
        """Executa auditoria matemática de todas as leis invariantes do sistema BTC FII/ETF.

        Retorna dicionário com status de conformidade ou levanta ContractConservationError.
        """
        audit_results: Dict[str, Any] = {
            "law_084_contract_quota_conservation": True,
            "law_085_dividend_forwarding_parity": True,
            "law_multilateral_custody_balance": True,
            "law_non_negative_custody": True,
            "total_contracts": len(self.contracts),
            "total_dividend_receipts": len(self.dividend_receipts),
            "total_return_receipts": len(self.return_receipts),
        }

        # 1. LAW-084: Conservação de Cotas em cada contrato
        for cid, contract in self.contracts.items():
            if contract.quantity != (contract.active_quantity + contract.returned_quantity):
                raise ContractConservationError(
                    f"Violação de LAW-084 no contrato '{cid}': "
                    f"Total ({contract.quantity}) != Ativo ({contract.active_quantity}) + Devolvido ({contract.returned_quantity})"
                )

        # 2. LAW-085: Paridade Estrita de Proventos Centavo a Centavo
        for receipt in self.dividend_receipts:
            if receipt.borrower_debit != receipt.lender_credit:
                raise ContractConservationError(
                    f"Violação de LAW-085 no recibo '{receipt.receipt_id}': "
                    f"Débito Tomador ({receipt.borrower_debit}) != Crédito Doador ({receipt.lender_credit})"
                )

        # 3. Conservação Multilateral de Custódia (Zero-sum de aluguel por ativo)
        symbol_lent: Dict[str, int] = {}
        symbol_borrowed: Dict[str, int] = {}

        for (part_id, acc_id), account in self.custody.items():
            for sym, pos in account.positions.items():
                # Validação de não-negatividade
                if (
                    pos.available_qty < 0
                    or pos.lent_active_qty < 0
                    or pos.borrowed_active_qty < 0
                    or pos.returned_cum_qty < 0
                ):
                    raise ContractConservationError(
                        f"Violação de não-negatividade na conta {acc_id} ativo {sym}: {pos.to_dict()}"
                    )
                symbol_lent[sym] = symbol_lent.get(sym, 0) + pos.lent_active_qty
                symbol_borrowed[sym] = symbol_borrowed.get(sym, 0) + pos.borrowed_active_qty

        all_symbols = set(symbol_lent.keys()).union(set(symbol_borrowed.keys()))
        for sym in all_symbols:
            tot_lent = symbol_lent.get(sym, 0)
            tot_borrowed = symbol_borrowed.get(sym, 0)
            if tot_lent != tot_borrowed:
                raise ContractConservationError(
                    f"Desbalanceamento multilateral de custódia no ativo '{sym}': "
                    f"Total Emprestado Ativo ({tot_lent}) != Total Tomado Ativo ({tot_borrowed})"
                )

        return audit_results

    # --------------------------------------------------------------------------
    # Serialização e Carregamento (to_dict / from_dict / load_fixture)
    # --------------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado completo do motor BTC em dicionário compatível com JSON."""
        return {
            "version": "1.0",
            "system": "Câmara B3 - BTC FII e ETF (FL-084)",
            "contracts": [c.to_dict() for c in self.contracts.values()],
            "custody_accounts": [
                acc.to_dict() for acc in self.custody.values()
            ],
            "dividend_receipts": [r.to_dict() for r in self.dividend_receipts],
            "return_receipts": [r.to_dict() for r in self.return_receipts],
            "invariants_audit": self.verify_invariants(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FiiEtfLendingEngine:
        """Reconstitui o estado do motor a partir de um dicionário serializado."""
        engine = cls()

        # Reconstrói contas e posições de custódia
        for acc_data in data.get("custody_accounts", []):
            part_id = int(acc_data["participant_id"])
            acc_id = int(acc_data["account_id"])
            account = engine.get_or_create_account(part_id, acc_id)
            for sym, pos_data in acc_data.get("positions", {}).items():
                pos = account.get_position(sym, pos_data.get("isin", ""))
                pos.available_qty = int(pos_data["available_qty"])
                pos.lent_active_qty = int(pos_data["lent_active_qty"])
                pos.borrowed_active_qty = int(pos_data["borrowed_active_qty"])
                pos.returned_cum_qty = int(pos_data["returned_cum_qty"])

        # Reconstrói contratos
        for c_data in data.get("contracts", []):
            contract = FiiEtfLoanContract(
                contract_id=str(c_data["contract_id"]),
                asset_type=AssetType.from_value(c_data["asset_type"]),
                symbol=str(c_data["symbol"]),
                isin=str(c_data["isin"]),
                lender_participant_id=int(c_data["lender_participant_id"]),
                lender_account=int(c_data["lender_account"]),
                borrower_participant_id=int(c_data["borrower_participant_id"]),
                borrower_account=int(c_data["borrower_account"]),
                quantity=int(c_data["quantity"]),
                base_price=Decimal(str(c_data["base_price"])),
                rental_rate=Decimal(str(c_data["rental_rate"])),
                open_date=date.fromisoformat(c_data["open_date"]),
                maturity_date=date.fromisoformat(c_data["maturity_date"]),
                reversibility=ReversibilityType.from_value(c_data.get("reversibility", "BORROWER_REVERSIBLE")),
                status=ContractStatus(c_data.get("status", "OPEN")),
                returned_quantity=int(c_data.get("returned_quantity", 0)),
                accrued_rental_fee=Decimal(str(c_data.get("accrued_rental_fee", "0.00"))),
            )
            engine.contracts[contract.contract_id] = contract

        # Reconstrói recibos de dividendos
        for r_data in data.get("dividend_receipts", []):
            receipt = DividendSettlementReceipt(
                receipt_id=str(r_data["receipt_id"]),
                contract_id=str(r_data["contract_id"]),
                event_id=str(r_data["event_id"]),
                symbol=str(r_data["symbol"]),
                lender_participant_id=int(r_data["lender_participant_id"]),
                lender_account=int(r_data["lender_account"]),
                borrower_participant_id=int(r_data["borrower_participant_id"]),
                borrower_account=int(r_data["borrower_account"]),
                quantity_at_record_date=int(r_data["quantity_at_record_date"]),
                rate_per_quota=Decimal(str(r_data["rate_per_quota"])),
                gross_amount=Decimal(str(r_data["gross_amount"])),
                withholding_tax=Decimal(str(r_data["withholding_tax"])),
                net_amount=Decimal(str(r_data["net_amount"])),
                borrower_debit=Decimal(str(r_data["borrower_debit"])),
                lender_credit=Decimal(str(r_data["lender_credit"])),
                tax_compensation_credit=Decimal(str(r_data.get("tax_compensation_credit", "0.00"))),
                payment_date=date.fromisoformat(r_data["payment_date"]),
                value_conserved=bool(r_data.get("value_conserved", True)),
                settlement_status=str(r_data.get("settlement_status", "SETTLED")),
            )
            engine.dividend_receipts.append(receipt)

        # Reconstrói recibos de devolução
        for ret_data in data.get("return_receipts", []):
            ret_receipt = ReturnExecutionReceipt(
                return_id=str(ret_data["return_id"]),
                contract_id=str(ret_data["contract_id"]),
                symbol=str(ret_data["symbol"]),
                return_date=date.fromisoformat(ret_data["return_date"]),
                returned_quantity=int(ret_data["returned_quantity"]),
                remaining_quantity=int(ret_data["remaining_quantity"]),
                business_days=int(ret_data["business_days"]),
                rental_fee=Decimal(str(ret_data["rental_fee"])),
                is_early_return=bool(ret_data["is_early_return"]),
                is_final_settlement=bool(ret_data["is_final_settlement"]),
                lender_participant_id=int(ret_data["lender_participant_id"]),
                lender_account=int(ret_data["lender_account"]),
                borrower_participant_id=int(ret_data["borrower_participant_id"]),
                borrower_account=int(ret_data["borrower_account"]),
                status=str(ret_data.get("status", "SETTLED")),
            )
            engine.return_receipts.append(ret_receipt)

        return engine

    @classmethod
    def load_fixture(cls, path: Union[str, Path]) -> FiiEtfLendingEngine:
        """Carrega e instancia o motor a partir de arquivo JSON de fixture."""
        file_path = Path(path)
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
