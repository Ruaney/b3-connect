"""Opções Listadas sobre Ações e Índices — Prêmio D+1, Cobertura e Exercício (FL-039).

Implementa o ciclo operacional completo de Opções Listadas (Compra/Call e Venda/Put,
estilos Americano e Europeu) compensadas pela Câmara B3 conforme o Manual de
Procedimentos Operacionais (MPO):
1. Simbologia padronizada de séries B3 (letras A–L para Calls e M–X para Puts de Jan–Dez).
2. Liquidação financeira multilateral do Prêmio em D+1 (débito ao Titular / crédito ao Lançador).
3. Enquadramento de garantia de posições lançadoras (Vendidas):
   - Coberta (`COVERED`): bloqueio do ativo-objeto na Carteira de Cobertura `2306-6` da Central Depositária.
   - Travada (`SPREAD_LOCKED`): trava de alta/baixa (*Bull/Bear Spread*) com ponta titular protetora.
   - Descoberta (`UNCOVERED`): exigência integral de margem de risco pela metodologia CORE.
4. Apuração determinística de *Moneyness* (`ITM`, `ATM`, `OTM`) e Valor Intrínseco no vencimento
   (3ª sexta-feira do mês).
5. Exercício Automático no vencimento para séries `ITM` acima do limiar regulamentar, com suporte
   a *Contrary Instruction* (*Barreira / Não Exercer* total ou parcial pelo titular).
6. Atribuição determinística (*Assignment*) às posições lançadoras e invariante estrita de conservação
   física (`Total Quantidade Exercida pelos Titulares == Total Quantidade Atribuída aos Lançadores`),
   gerando operações no mercado à vista de exercício com liquidação em D+2 pelo *Strike*.

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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Constantes Regulatórias da Câmara B3 (Opções Listadas e Exercício)
# ---------------------------------------------------------------------------

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_FOUR = Decimal("0.0001")

CUSTODY_WALLET_COVERED_OPTIONS = "2306-6"
CUSTODY_WALLET_FREE_SPOT = "2101-6"

DEFAULT_AUTO_EXERCISE_MIN_INTRINSIC_BRL = Decimal("0.01")

CALL_MONTH_LETTERS: Dict[str, int] = {
    "A": 1,
    "B": 2,
    "C": 3,
    "D": 4,
    "E": 5,
    "F": 6,
    "G": 7,
    "H": 8,
    "I": 9,
    "J": 10,
    "K": 11,
    "L": 12,
}

PUT_MONTH_LETTERS: Dict[str, int] = {
    "M": 1,
    "N": 2,
    "O": 3,
    "P": 4,
    "Q": 5,
    "R": 6,
    "S": 7,
    "T": 8,
    "U": 9,
    "V": 10,
    "W": 11,
    "X": 12,
}


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class ListedOptionsError(Exception):
    """Exceção base para o motor de Opções Listadas e Exercício da Câmara B3."""


class ListedOptionsValidationError(ListedOptionsError):
    """Erro de validação de dados cadastrais, séries ou posições de opções."""


class InsufficientCoverageError(ListedOptionsValidationError):
    """Saldo depositado na carteira 2306-6 insuficiente para posição lançadora coberta."""


class ContraryInstructionError(ListedOptionsValidationError):
    """Instrução de barreira (Contrary Instruction) inválida ou superior à posição titular."""


class EarlyExerciseStyleError(ListedOptionsValidationError):
    """Tentativa de exercício antecipado em série de estilo Europeu antes do vencimento."""


class ConservationInvariantError(ListedOptionsValidationError):
    """Violação da invariante de conservação entre titulares e lançadores na série."""


# ---------------------------------------------------------------------------
# Funções Auxiliares de Conversão Determinística
# ---------------------------------------------------------------------------

def _to_decimal(value: Any, field_name: str) -> Decimal:
    """Converte valor numérico ou textual para Decimal com validação estrita."""
    if isinstance(value, Decimal):
        return value
    if value is None or isinstance(value, bool):
        raise ListedOptionsValidationError(
            f"O campo '{field_name}' não pode ser nulo ou booleano."
        )
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ListedOptionsValidationError(
            f"Valor decimal inválido para '{field_name}': {value!r}"
        ) from exc
    if not dec.is_finite():
        raise ListedOptionsValidationError(
            f"Valor numérico não finito para '{field_name}': {value!r}"
        )
    return dec


def _to_int(value: Any, field_name: str) -> int:
    """Converte valor para inteiro estrito (rejeita booleanos e decimais fracionários)."""
    if isinstance(value, bool) or value is None:
        raise ListedOptionsValidationError(
            f"O campo inteiro '{field_name}' não pode ser nulo ou booleano."
        )
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lstrip("-").isdigit():
            return int(stripped)
    raise ListedOptionsValidationError(
        f"Valor inteiro inválido para '{field_name}': {value!r}"
    )


def _to_date(value: Any, field_name: str) -> date:
    """Converte string ISO-8601 (YYYY-MM-DD) ou objeto date/datetime em date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value or not isinstance(value, str):
        raise ListedOptionsValidationError(
            f"Data inválida para '{field_name}': {value!r}"
        )
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise ListedOptionsValidationError(
            f"Formato de data inválido para '{field_name}' (esperado YYYY-MM-DD): {value!r}"
        ) from exc


def _require_non_empty_str(value: Any, field_name: str) -> str:
    """Valida que um campo textual não é vazio."""
    if not isinstance(value, str) or not value.strip():
        raise ListedOptionsValidationError(
            f"O campo '{field_name}' deve ser uma string não vazia."
        )
    return value.strip()


# ---------------------------------------------------------------------------
# Enumerações de Domínio
# ---------------------------------------------------------------------------

class OptionType(str, Enum):
    """Tipo da opção listada na B3 (Compra / Call ou Venda / Put)."""

    CALL = "CALL"
    PUT = "PUT"


class OptionStyle(str, Enum):
    """Estilo de exercício da série de opção (Americano ou Europeu)."""

    AMERICAN = "AMERICAN"
    EUROPEAN = "EUROPEAN"


class PositionSide(str, Enum):
    """Ponta da posição no contrato de opção (Titular/Comprado ou Lançador/Vendido)."""

    HOLDER = "HOLDER"
    WRITER = "WRITER"


class CoverageType(str, Enum):
    """Modalidade de garantia ou cobertura da posição na Câmara B3."""

    COVERED = "COVERED"
    UNCOVERED = "UNCOVERED"
    SPREAD_LOCKED = "SPREAD_LOCKED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Moneyness(str, Enum):
    """Classificação de valor intrínseco da série em relação ao preço do ativo-objeto."""

    ITM = "ITM"
    ATM = "ATM"
    OTM = "OTM"


# ---------------------------------------------------------------------------
# Modelos de Dados (Dataclasses Imutáveis)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OptionSeries:
    """Série padronizada de Opção Listada registrada na Câmara B3."""

    series_ticker: str
    underlying_ticker: str
    underlying_isin: str
    option_type: OptionType
    option_style: OptionStyle
    strike_price: Decimal
    expiration_date: date
    contract_size: int = 1

    def __post_init__(self) -> None:
        ticker = _require_non_empty_str(self.series_ticker, "series_ticker").upper()
        if len(ticker) < 6:
            raise ListedOptionsValidationError(
                f"Código de série B3 inválido (mínimo 6 caracteres): {ticker!r}"
            )
        object.__setattr__(self, "series_ticker", ticker)
        object.__setattr__(
            self,
            "underlying_ticker",
            _require_non_empty_str(self.underlying_ticker, "underlying_ticker").upper(),
        )
        object.__setattr__(
            self,
            "underlying_isin",
            _require_non_empty_str(self.underlying_isin, "underlying_isin").upper(),
        )

        if not isinstance(self.option_type, OptionType):
            try:
                object.__setattr__(
                    self, "option_type", OptionType(str(self.option_type).upper())
                )
            except ValueError as exc:
                raise ListedOptionsValidationError(
                    f"OptionType inválido: {self.option_type!r}"
                ) from exc

        if not isinstance(self.option_style, OptionStyle):
            try:
                object.__setattr__(
                    self, "option_style", OptionStyle(str(self.option_style).upper())
                )
            except ValueError as exc:
                raise ListedOptionsValidationError(
                    f"OptionStyle inválido: {self.option_style!r}"
                ) from exc

        # Valida consistência da 5ª letra do ticker B3 com OptionType (A-L = CALL, M-X = PUT)
        fifth_letter = ticker[4]
        if self.option_type == OptionType.CALL and fifth_letter not in CALL_MONTH_LETTERS:
            raise ListedOptionsValidationError(
                f"Série CALL '{ticker}' deve possuir 5ª letra entre A e L (encontrado '{fifth_letter}')."
            )
        if self.option_type == OptionType.PUT and fifth_letter not in PUT_MONTH_LETTERS:
            raise ListedOptionsValidationError(
                f"Série PUT '{ticker}' deve possuir 5ª letra entre M e X (encontrado '{fifth_letter}')."
            )

        strike = _to_decimal(self.strike_price, "strike_price").quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )
        if strike <= Decimal("0.00"):
            raise ListedOptionsValidationError(
                f"O preço de exercício (strike_price) deve ser positivo: {strike}"
            )
        object.__setattr__(self, "strike_price", strike)

        exp_date = _to_date(self.expiration_date, "expiration_date")
        object.__setattr__(self, "expiration_date", exp_date)

        c_size = _to_int(self.contract_size, "contract_size")
        if c_size <= 0:
            raise ListedOptionsValidationError(
                f"O tamanho do contrato (contract_size) deve ser positivo: {c_size}"
            )
        object.__setattr__(self, "contract_size", c_size)

    @property
    def expiration_month_letter(self) -> str:
        """Retorna a letra do mês de vencimento (5º caractere do ticker B3)."""
        return self.series_ticker[4]

    @property
    def expiration_month_number(self) -> int:
        """Retorna o mês numérico (1 a 12) codificado na 5ª letra da série B3."""
        letter = self.expiration_month_letter
        if letter in CALL_MONTH_LETTERS:
            return CALL_MONTH_LETTERS[letter]
        return PUT_MONTH_LETTERS[letter]

    @property
    def spot_exercise_ticker(self) -> str:
        """Retorna o código do negócio de exercício gerado no mercado à vista (sufixo E)."""
        return f"{self.underlying_ticker}E"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OptionSeries":
        """Constrói uma OptionSeries a partir de um dicionário JSON."""
        return cls(
            series_ticker=data["series_ticker"],
            underlying_ticker=data["underlying_ticker"],
            underlying_isin=data.get("underlying_isin", f"BR{data['underlying_ticker'][:4]}ACNOR0"),
            option_type=OptionType(str(data["option_type"]).upper()),
            option_style=OptionStyle(str(data["option_style"]).upper()),
            strike_price=_to_decimal(data["strike_price"], "strike_price"),
            expiration_date=_to_date(data["expiration_date"], "expiration_date"),
            contract_size=_to_int(data.get("contract_size", 1), "contract_size"),
        )


@dataclass(frozen=True)
class OptionPosition:
    """Posição aberta (Titular ou Lançadora) em uma série de Opção Listada na B3."""

    position_id: str
    participant_code: str
    investor_account: str
    series_ticker: str
    side: PositionSide
    quantity: int
    trade_date: date
    premium_per_unit: Decimal
    coverage_type: CoverageType = CoverageType.NOT_APPLICABLE
    covered_quantity: int = 0
    custody_wallet_code: str = CUSTODY_WALLET_FREE_SPOT
    spread_long_series_ticker: Optional[str] = None
    contrary_instruction: bool = False
    contrary_quantity: int = 0
    premium_settlement_date_d1: Optional[date] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "position_id", _require_non_empty_str(self.position_id, "position_id")
        )
        object.__setattr__(
            self,
            "participant_code",
            _require_non_empty_str(self.participant_code, "participant_code"),
        )
        object.__setattr__(
            self,
            "investor_account",
            _require_non_empty_str(self.investor_account, "investor_account"),
        )
        object.__setattr__(
            self,
            "series_ticker",
            _require_non_empty_str(self.series_ticker, "series_ticker").upper(),
        )

        if not isinstance(self.side, PositionSide):
            try:
                object.__setattr__(self, "side", PositionSide(str(self.side).upper()))
            except ValueError as exc:
                raise ListedOptionsValidationError(
                    f"PositionSide inválido: {self.side!r}"
                ) from exc

        qty = _to_int(self.quantity, "quantity")
        if qty <= 0:
            raise ListedOptionsValidationError(
                f"A quantidade da posição deve ser estritamente positiva: {qty}"
            )
        object.__setattr__(self, "quantity", qty)

        t_date = _to_date(self.trade_date, "trade_date")
        object.__setattr__(self, "trade_date", t_date)

        if self.premium_settlement_date_d1 is None:
            object.__setattr__(
                self, "premium_settlement_date_d1", t_date + timedelta(days=1)
            )
        else:
            p_date = _to_date(
                self.premium_settlement_date_d1, "premium_settlement_date_d1"
            )
            if p_date < t_date:
                raise ListedOptionsValidationError(
                    f"A data de liquidação D+1 do prêmio ({p_date}) não pode anteceder a data de negócio ({t_date})."
                )
            object.__setattr__(self, "premium_settlement_date_d1", p_date)

        prem = _to_decimal(self.premium_per_unit, "premium_per_unit").quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )
        if prem < Decimal("0.00"):
            raise ListedOptionsValidationError(
                f"O prêmio unitário não pode ser negativo: {prem}"
            )
        object.__setattr__(self, "premium_per_unit", prem)

        if not isinstance(self.coverage_type, CoverageType):
            try:
                object.__setattr__(
                    self, "coverage_type", CoverageType(str(self.coverage_type).upper())
                )
            except ValueError as exc:
                raise ListedOptionsValidationError(
                    f"CoverageType inválido: {self.coverage_type!r}"
                ) from exc

        cov_qty = _to_int(self.covered_quantity, "covered_quantity")
        if cov_qty < 0:
            raise ListedOptionsValidationError(
                f"A quantidade coberta não pode ser negativa: {cov_qty}"
            )
        object.__setattr__(self, "covered_quantity", cov_qty)

        # Validação de regras de negócio entre PositionSide e CoverageType
        if self.side == PositionSide.HOLDER:
            if self.coverage_type != CoverageType.NOT_APPLICABLE:
                raise ListedOptionsValidationError(
                    f"Posição titular (HOLDER '{self.position_id}') deve possuir coverage_type=NOT_APPLICABLE."
                )
            if cov_qty != 0:
                raise ListedOptionsValidationError(
                    f"Posição titular (HOLDER '{self.position_id}') não pode ter saldo em carteira de cobertura."
                )
        else:
            # WRITER (Lançador)
            if self.coverage_type == CoverageType.NOT_APPLICABLE:
                raise ListedOptionsValidationError(
                    f"Posição lançadora (WRITER '{self.position_id}') exige enquadramento COVERED, UNCOVERED ou SPREAD_LOCKED."
                )
            if self.coverage_type == CoverageType.COVERED:
                if cov_qty < qty:
                    raise InsufficientCoverageError(
                        f"Posição lançadora coberta '{self.position_id}' exige saldo na carteira {CUSTODY_WALLET_COVERED_OPTIONS} "
                        f">= {qty} (depositado: {cov_qty})."
                    )
                object.__setattr__(
                    self, "custody_wallet_code", CUSTODY_WALLET_COVERED_OPTIONS
                )
            elif self.coverage_type == CoverageType.SPREAD_LOCKED:
                if not self.spread_long_series_ticker or not str(
                    self.spread_long_series_ticker
                ).strip():
                    raise ListedOptionsValidationError(
                        f"Posição lançadora travada (SPREAD_LOCKED '{self.position_id}') exige 'spread_long_series_ticker'."
                    )
                object.__setattr__(
                    self,
                    "spread_long_series_ticker",
                    str(self.spread_long_series_ticker).strip().upper(),
                )
                object.__setattr__(
                    self, "custody_wallet_code", CUSTODY_WALLET_FREE_SPOT
                )
            else:
                object.__setattr__(
                    self, "custody_wallet_code", CUSTODY_WALLET_FREE_SPOT
                )

        # Validação de Contrary Instruction (Barreira de Exercício)
        if not isinstance(self.contrary_instruction, bool):
            raise ListedOptionsValidationError(
                "O campo 'contrary_instruction' deve ser um booleano."
            )
        c_qty = _to_int(self.contrary_quantity, "contrary_quantity")
        if c_qty < 0:
            raise ContraryInstructionError(
                f"A quantidade de barreira (contrary_quantity) não pode ser negativa: {c_qty}"
            )
        if self.side == PositionSide.WRITER and (self.contrary_instruction or c_qty > 0):
            raise ContraryInstructionError(
                f"Apenas o titular (HOLDER) pode enviar Contrary Instruction (posição lançadora '{self.position_id}')."
            )
        if self.contrary_instruction:
            if c_qty == 0:
                c_qty = qty
                object.__setattr__(self, "contrary_quantity", c_qty)
            elif c_qty > qty:
                raise ContraryInstructionError(
                    f"Quantidade bloqueada por barreira ({c_qty}) excede a posição titular ({qty}) em '{self.position_id}'."
                )
        else:
            if c_qty > 0:
                raise ContraryInstructionError(
                    f"contrary_quantity ({c_qty}) informado com contrary_instruction=False em '{self.position_id}'."
                )

    @property
    def gross_premium_brl(self) -> Decimal:
        """Valor financeiro bruto do prêmio liquidado em D+1 (`quantity * premium_per_unit`)."""
        return (Decimal(self.quantity) * self.premium_per_unit).quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )

    @property
    def d1_cashflow_brl(self) -> Decimal:
        """Fluxo financeiro multilateral em D+1 (negativo para Titular, positivo para Lançador)."""
        gross = self.gross_premium_brl
        return -gross if self.side == PositionSide.HOLDER else gross

    @property
    def requires_core_margin(self) -> bool:
        """Indica se a posição exige apuração de margem de risco CORE (descoberta ou spread)."""
        return self.side == PositionSide.WRITER and self.coverage_type in (
            CoverageType.UNCOVERED,
            CoverageType.SPREAD_LOCKED,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OptionPosition":
        """Constrói uma OptionPosition a partir de um dicionário JSON."""
        return cls(
            position_id=data["position_id"],
            participant_code=data["participant_code"],
            investor_account=data["investor_account"],
            series_ticker=data["series_ticker"],
            side=PositionSide(str(data["side"]).upper()),
            quantity=_to_int(data["quantity"], "quantity"),
            trade_date=_to_date(data["trade_date"], "trade_date"),
            premium_per_unit=_to_decimal(data["premium_per_unit"], "premium_per_unit"),
            coverage_type=CoverageType(
                str(data.get("coverage_type", "NOT_APPLICABLE")).upper()
            ),
            covered_quantity=_to_int(data.get("covered_quantity", 0), "covered_quantity"),
            custody_wallet_code=data.get("custody_wallet_code", CUSTODY_WALLET_FREE_SPOT),
            spread_long_series_ticker=data.get("spread_long_series_ticker"),
            contrary_instruction=bool(data.get("contrary_instruction", False)),
            contrary_quantity=_to_int(
                data.get("contrary_quantity", 0), "contrary_quantity"
            ),
            premium_settlement_date_d1=(
                _to_date(data["premium_settlement_date_d1"], "premium_settlement_date_d1")
                if data.get("premium_settlement_date_d1")
                else None
            ),
        )


@dataclass(frozen=True)
class ExerciseInstruction:
    """Resultado do processamento de exercício no vencimento para uma posição Titular (`HOLDER`)."""

    instruction_id: str
    position_id: str
    participant_code: str
    investor_account: str
    series_ticker: str
    underlying_ticker: str
    option_type: OptionType
    strike_price: Decimal
    underlying_closing_price: Decimal
    moneyness: Moneyness
    intrinsic_value_per_unit: Decimal
    total_position_quantity: int
    contrary_blocked_quantity: int
    exercised_quantity: int
    lapsed_quantity: int
    is_automatic_exercise: bool
    contrary_instruction_applied: bool
    spot_exercise_ticker: str
    spot_side_generated: Optional[str]
    settlement_date_d2: date
    gross_exercise_amount_brl: Decimal
    intrinsic_gain_brl: Decimal


@dataclass(frozen=True)
class AssignmentRecord:
    """Registro de Atribuição de Exercício (*Assignment*) a uma posição Lançadora (`WRITER`)."""

    assignment_id: str
    writer_position_id: str
    participant_code: str
    investor_account: str
    series_ticker: str
    underlying_ticker: str
    option_type: OptionType
    coverage_type: CoverageType
    custody_wallet_code: str
    open_short_quantity: int
    assigned_quantity: int
    unassigned_expired_quantity: int
    strike_price: Decimal
    gross_settlement_amount_brl: Decimal
    settlement_date_d2: date
    spot_exercise_ticker: str
    spot_side_generated: Optional[str]
    requires_core_margin_release: bool


@dataclass(frozen=True)
class ExpirationSettlementSummary:
    """Resumo consolidado da liquidação de vencimento e exercício de uma série de opções."""

    series_ticker: str
    underlying_ticker: str
    option_type: OptionType
    option_style: OptionStyle
    strike_price: Decimal
    underlying_closing_price: Decimal
    moneyness: Moneyness
    intrinsic_value_per_unit: Decimal
    expiration_date: date
    exercise_settlement_date_d2: date
    total_holder_open_quantity: int
    total_writer_open_quantity: int
    total_holder_exercised_quantity: int
    total_holder_contrary_blocked_quantity: int
    total_holder_lapsed_quantity: int
    total_writer_assigned_quantity: int
    total_writer_unassigned_quantity: int
    total_writer_covered_assigned_quantity: int
    total_writer_uncovered_assigned_quantity: int
    total_writer_spread_assigned_quantity: int
    total_holder_premium_d1_brl: Decimal
    total_writer_premium_d1_brl: Decimal
    total_exercise_notional_d2_brl: Decimal
    total_intrinsic_value_exercised_brl: Decimal
    conservation_invariant_satisfied: bool
    exercise_instructions: Tuple[ExerciseInstruction, ...]
    assignments: Tuple[AssignmentRecord, ...]
    generated_spot_trades: Tuple[Dict[str, Any], ...]


# ---------------------------------------------------------------------------
# Motor Determinístico de Opções Listadas e Exercício (ListedOptionsEngine)
# ---------------------------------------------------------------------------

class ListedOptionsEngine:
    """Motor determinístico da Câmara B3 para Opções Listadas e Exercício no Vencimento (FL-039)."""

    def __init__(
        self,
        auto_exercise_min_intrinsic_brl: Union[Decimal, str] = DEFAULT_AUTO_EXERCISE_MIN_INTRINSIC_BRL,
    ) -> None:
        threshold = _to_decimal(
            auto_exercise_min_intrinsic_brl, "auto_exercise_min_intrinsic_brl"
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)
        if threshold <= Decimal("0.00"):
            raise ListedOptionsValidationError(
                f"O limiar mínimo de exercício automático deve ser positivo: {threshold}"
            )
        self.auto_exercise_min_intrinsic_brl = threshold

    @staticmethod
    def evaluate_moneyness(
        option_type: OptionType,
        strike_price: Union[Decimal, str],
        underlying_spot_price: Union[Decimal, str],
    ) -> Tuple[Moneyness, Decimal]:
        """Calcula deterministicamente o Moneyness (ITM, ATM, OTM) e o Valor Intrínseco unitário."""
        if not isinstance(option_type, OptionType):
            option_type = OptionType(str(option_type).upper())

        strike = _to_decimal(strike_price, "strike_price").quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )
        spot = _to_decimal(underlying_spot_price, "underlying_spot_price").quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )
        if strike <= Decimal("0.00") or spot <= Decimal("0.00"):
            raise ListedOptionsValidationError(
                "Preços de strike e de fechamento do ativo-objeto devem ser positivos."
            )

        if option_type == OptionType.CALL:
            if spot > strike:
                return Moneyness.ITM, (spot - strike).quantize(
                    QUANTIZE_CENTS, rounding=ROUND_HALF_UP
                )
            if spot == strike:
                return Moneyness.ATM, Decimal("0.00")
            return Moneyness.OTM, Decimal("0.00")

        # OptionType.PUT
        if spot < strike:
            return Moneyness.ITM, (strike - spot).quantize(
                QUANTIZE_CENTS, rounding=ROUND_HALF_UP
            )
        if spot == strike:
            return Moneyness.ATM, Decimal("0.00")
        return Moneyness.OTM, Decimal("0.00")

    @staticmethod
    def calculate_d1_premium(
        quantity: int,
        premium_per_unit: Union[Decimal, str],
        side: PositionSide,
    ) -> Tuple[Decimal, Decimal]:
        """Calcula o valor bruto do prêmio em D+1 e o fluxo líquido (débito Titular / crédito Lançador)."""
        qty = _to_int(quantity, "quantity")
        if qty <= 0:
            raise ListedOptionsValidationError(
                f"A quantidade deve ser positiva para cálculo de prêmio: {qty}"
            )
        prem = _to_decimal(premium_per_unit, "premium_per_unit").quantize(
            QUANTIZE_CENTS, rounding=ROUND_HALF_UP
        )
        if prem < Decimal("0.00"):
            raise ListedOptionsValidationError(
                f"O prêmio unitário não pode ser negativo: {prem}"
            )
        gross = (Decimal(qty) * prem).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)
        signed = -gross if side == PositionSide.HOLDER else gross
        return gross, signed

    @staticmethod
    def validate_exercise_date_for_style(
        series: OptionSeries,
        exercise_date: date,
    ) -> None:
        """Valida se o exercício na data informada respeita o estilo (Americano vs Europeu)."""
        ex_date = _to_date(exercise_date, "exercise_date")
        if ex_date > series.expiration_date:
            raise ListedOptionsValidationError(
                f"Data de exercício ({ex_date}) posterior ao vencimento da série '{series.series_ticker}' ({series.expiration_date})."
            )
        if (
            series.option_style == OptionStyle.EUROPEAN
            and ex_date < series.expiration_date
        ):
            raise EarlyExerciseStyleError(
                f"Série '{series.series_ticker}' possui estilo EUROPEU e não admite exercício antecipado em {ex_date} "
                f"(vencimento: {series.expiration_date})."
            )

    def evaluate_holder_exercise(
        self,
        position: OptionPosition,
        series: OptionSeries,
        underlying_closing_price: Union[Decimal, str],
        settlement_date_d2: date,
    ) -> ExerciseInstruction:
        """Avalia o exercício automático e aplicação de Contrary Instruction para posição titular."""
        if position.side != PositionSide.HOLDER:
            raise ListedOptionsValidationError(
                f"Apenas posições titulares (HOLDER) podem gerar ExerciseInstruction: '{position.position_id}'."
            )
        if position.series_ticker != series.series_ticker:
            raise ListedOptionsValidationError(
                f"Inconsistência de série entre posição ({position.series_ticker}) e série ({series.series_ticker})."
            )

        spot = _to_decimal(
            underlying_closing_price, "underlying_closing_price"
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)
        d2_date = _to_date(settlement_date_d2, "settlement_date_d2")

        moneyness, intrinsic_unit = self.evaluate_moneyness(
            option_type=series.option_type,
            strike_price=series.strike_price,
            underlying_spot_price=spot,
        )

        eligible_for_auto = (
            moneyness == Moneyness.ITM
            and intrinsic_unit >= self.auto_exercise_min_intrinsic_brl
        )

        if eligible_for_auto:
            blocked_qty = position.contrary_quantity if position.contrary_instruction else 0
            exercised_qty = position.quantity - blocked_qty
            lapsed_qty = blocked_qty
            is_auto = exercised_qty > 0
            contrary_applied = blocked_qty > 0
        else:
            blocked_qty = 0
            exercised_qty = 0
            lapsed_qty = position.quantity
            is_auto = False
            contrary_applied = False

        gross_exercise_brl = (
            Decimal(exercised_qty) * series.strike_price * Decimal(series.contract_size)
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)

        intrinsic_gain_brl = (
            Decimal(exercised_qty) * intrinsic_unit * Decimal(series.contract_size)
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)

        spot_side = (
            ("BUYER" if series.option_type == OptionType.CALL else "SELLER")
            if exercised_qty > 0
            else None
        )

        return ExerciseInstruction(
            instruction_id=f"EXI-{position.position_id}",
            position_id=position.position_id,
            participant_code=position.participant_code,
            investor_account=position.investor_account,
            series_ticker=series.series_ticker,
            underlying_ticker=series.underlying_ticker,
            option_type=series.option_type,
            strike_price=series.strike_price,
            underlying_closing_price=spot,
            moneyness=moneyness,
            intrinsic_value_per_unit=intrinsic_unit,
            total_position_quantity=position.quantity,
            contrary_blocked_quantity=blocked_qty,
            exercised_quantity=exercised_qty,
            lapsed_quantity=lapsed_qty,
            is_automatic_exercise=is_auto,
            contrary_instruction_applied=contrary_applied,
            spot_exercise_ticker=series.spot_exercise_ticker,
            spot_side_generated=spot_side,
            settlement_date_d2=d2_date,
            gross_exercise_amount_brl=gross_exercise_brl,
            intrinsic_gain_brl=intrinsic_gain_brl,
        )

    @staticmethod
    def assign_writers(
        series: OptionSeries,
        writer_positions: Sequence[OptionPosition],
        total_exercised_quantity: int,
        settlement_date_d2: date,
    ) -> List[AssignmentRecord]:
        """Atribui deterministicamente a quantidade exercida às posições lançadoras (pro-rata exato)."""
        if not writer_positions:
            raise ListedOptionsValidationError(
                f"Nenhuma posição lançadora (WRITER) encontrada para a série '{series.series_ticker}'."
            )
        for pos in writer_positions:
            if pos.side != PositionSide.WRITER:
                raise ListedOptionsValidationError(
                    f"Posição '{pos.position_id}' não é lançadora (WRITER)."
                )
            if pos.series_ticker != series.series_ticker:
                raise ListedOptionsValidationError(
                    f"Posição '{pos.position_id}' pertence à série '{pos.series_ticker}', esperado '{series.series_ticker}'."
                )

        total_writer_open = sum(p.quantity for p in writer_positions)
        target_qty = _to_int(total_exercised_quantity, "total_exercised_quantity")
        if target_qty < 0 or target_qty > total_writer_open:
            raise ConservationInvariantError(
                f"Quantidade exercida ({target_qty}) inválida frente ao total lançado aberto ({total_writer_open}) "
                f"na série '{series.series_ticker}'."
            )

        d2_date = _to_date(settlement_date_d2, "settlement_date_d2")

        # Cálculo pro-rata determinístico com distribuição exata de resíduos (Método dos Maiores Restos)
        floor_allocations: List[int] = []
        remainders: List[Tuple[Decimal, int, str, int]] = []

        if target_qty == 0:
            floor_allocations = [0 for _ in writer_positions]
        elif target_qty == total_writer_open:
            floor_allocations = [p.quantity for p in writer_positions]
        else:
            for idx, pos in enumerate(writer_positions):
                exact_share = (Decimal(pos.quantity) * Decimal(target_qty)) / Decimal(
                    total_writer_open
                )
                floored = int(exact_share)
                remainder = exact_share - Decimal(floored)
                floor_allocations.append(floored)
                # Critério de desempate determinístico: maior resto, maior posição aberta, menor position_id
                remainders.append((remainder, pos.quantity, pos.position_id, idx))

            residual = target_qty - sum(floor_allocations)
            remainders.sort(key=lambda item: (-item[0], -item[1], item[2]))
            for i in range(residual):
                winner_idx = remainders[i][3]
                floor_allocations[winner_idx] += 1

        assignments: List[AssignmentRecord] = []
        for pos, assigned_qty in zip(writer_positions, floor_allocations):
            unassigned_qty = pos.quantity - assigned_qty
            gross_brl = (
                Decimal(assigned_qty)
                * series.strike_price
                * Decimal(series.contract_size)
            ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)

            spot_side = (
                ("SELLER" if series.option_type == OptionType.CALL else "BUYER")
                if assigned_qty > 0
                else None
            )

            assignments.append(
                AssignmentRecord(
                    assignment_id=f"ASG-{pos.position_id}",
                    writer_position_id=pos.position_id,
                    participant_code=pos.participant_code,
                    investor_account=pos.investor_account,
                    series_ticker=series.series_ticker,
                    underlying_ticker=series.underlying_ticker,
                    option_type=series.option_type,
                    coverage_type=pos.coverage_type,
                    custody_wallet_code=pos.custody_wallet_code,
                    open_short_quantity=pos.quantity,
                    assigned_quantity=assigned_qty,
                    unassigned_expired_quantity=unassigned_qty,
                    strike_price=series.strike_price,
                    gross_settlement_amount_brl=gross_brl,
                    settlement_date_d2=d2_date,
                    spot_exercise_ticker=series.spot_exercise_ticker,
                    spot_side_generated=spot_side,
                    requires_core_margin_release=pos.requires_core_margin,
                )
            )

        total_assigned = sum(a.assigned_quantity for a in assignments)
        if total_assigned != target_qty:
            raise ConservationInvariantError(
                f"Violação de conservação na atribuição da série '{series.series_ticker}': "
                f"exercido={target_qty} != atribuído={total_assigned}."
            )

        return assignments

    @staticmethod
    def generate_spot_exercise_trades(
        series: OptionSeries,
        exercise_instructions: Sequence[ExerciseInstruction],
        assignments: Sequence[AssignmentRecord],
    ) -> List[Dict[str, Any]]:
        """Gera as operações de exercício no mercado à vista (liquidação em D+2 pelo Strike)."""
        trades: List[Dict[str, Any]] = []

        active_holders = [
            inst for inst in exercise_instructions if inst.exercised_quantity > 0
        ]
        active_writers = [asg for asg in assignments if asg.assigned_quantity > 0]

        total_holder_qty = sum(i.exercised_quantity for i in active_holders)
        total_writer_qty = sum(a.assigned_quantity for a in active_writers)
        if total_holder_qty != total_writer_qty:
            raise ConservationInvariantError(
                f"Invariante violada ao gerar negócios à vista para '{series.series_ticker}': "
                f"Titulares={total_holder_qty} != Lançadores={total_writer_qty}."
            )

        if total_holder_qty == 0:
            return trades

        # Casamento determinístico FIFO entre pontas titulares exercidas e lançadoras atribuídas
        h_idx = 0
        w_idx = 0
        h_rem = active_holders[0].exercised_quantity
        w_rem = active_writers[0].assigned_quantity
        seq = 1

        while h_idx < len(active_holders) and w_idx < len(active_writers):
            matched_qty = min(h_rem, w_rem)
            holder = active_holders[h_idx]
            writer = active_writers[w_idx]

            notional_brl = (
                Decimal(matched_qty)
                * series.strike_price
                * Decimal(series.contract_size)
            ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)

            if series.option_type == OptionType.CALL:
                buyer_participant = holder.participant_code
                buyer_account = holder.investor_account
                seller_participant = writer.participant_code
                seller_account = writer.investor_account
                delivery_wallet = writer.custody_wallet_code
            else:
                buyer_participant = writer.participant_code
                buyer_account = writer.investor_account
                seller_participant = holder.participant_code
                seller_account = holder.investor_account
                delivery_wallet = CUSTODY_WALLET_FREE_SPOT

            trades.append(
                {
                    "spot_trade_id": f"SPOT-EX-{series.series_ticker}-{seq:03d}",
                    "series_ticker": series.series_ticker,
                    "spot_exercise_ticker": series.spot_exercise_ticker,
                    "underlying_ticker": series.underlying_ticker,
                    "option_type": series.option_type.value,
                    "holder_position_id": holder.position_id,
                    "writer_position_id": writer.writer_position_id,
                    "buyer_participant_code": buyer_participant,
                    "buyer_account": buyer_account,
                    "seller_participant_code": seller_participant,
                    "seller_account": seller_account,
                    "seller_delivery_wallet_code": delivery_wallet,
                    "writer_coverage_type": writer.coverage_type.value,
                    "quantity": matched_qty,
                    "strike_price": series.strike_price,
                    "gross_settlement_amount_brl": notional_brl,
                    "settlement_date_d2": holder.settlement_date_d2.isoformat(),
                }
            )
            seq += 1

            h_rem -= matched_qty
            w_rem -= matched_qty

            if h_rem == 0:
                h_idx += 1
                if h_idx < len(active_holders):
                    h_rem = active_holders[h_idx].exercised_quantity
            if w_rem == 0:
                w_idx += 1
                if w_idx < len(active_writers):
                    w_rem = active_writers[w_idx].assigned_quantity

        return trades

    def settle_series_expiration(
        self,
        series: OptionSeries,
        positions: Sequence[OptionPosition],
        underlying_closing_price: Union[Decimal, str],
        settlement_date_d2: Optional[date] = None,
    ) -> ExpirationSettlementSummary:
        """Processa o vencimento completo de uma série de opções e verifica a invariante de conservação."""
        series_positions = [
            p for p in positions if p.series_ticker == series.series_ticker
        ]
        if not series_positions:
            raise ListedOptionsValidationError(
                f"Nenhuma posição informada para a série '{series.series_ticker}'."
            )

        holders = [p for p in series_positions if p.side == PositionSide.HOLDER]
        writers = [p for p in series_positions if p.side == PositionSide.WRITER]
        if not holders or not writers:
            raise ConservationInvariantError(
                f"A série '{series.series_ticker}' deve conter posições titulares e lançadoras abertas."
            )

        total_holder_open = sum(p.quantity for p in holders)
        total_writer_open = sum(p.quantity for p in writers)
        if total_holder_open != total_writer_open:
            raise ConservationInvariantError(
                f"Violação de conservação de interesse em aberto na série '{series.series_ticker}': "
                f"Total Titular ({total_holder_open}) != Total Lançador ({total_writer_open})."
            )

        d2_date = (
            _to_date(settlement_date_d2, "settlement_date_d2")
            if settlement_date_d2 is not None
            else series.expiration_date + timedelta(days=2)
        )
        if d2_date < series.expiration_date:
            raise ListedOptionsValidationError(
                f"A data de liquidação D+2 do exercício ({d2_date}) não pode anteceder o vencimento ({series.expiration_date})."
            )

        spot = _to_decimal(
            underlying_closing_price, "underlying_closing_price"
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)

        moneyness, intrinsic_unit = self.evaluate_moneyness(
            option_type=series.option_type,
            strike_price=series.strike_price,
            underlying_spot_price=spot,
        )

        instructions: List[ExerciseInstruction] = [
            self.evaluate_holder_exercise(
                position=h_pos,
                series=series,
                underlying_closing_price=spot,
                settlement_date_d2=d2_date,
            )
            for h_pos in holders
        ]

        total_exercised = sum(i.exercised_quantity for i in instructions)
        total_contrary_blocked = sum(i.contrary_blocked_quantity for i in instructions)
        total_lapsed = sum(i.lapsed_quantity for i in instructions)

        assignments = self.assign_writers(
            series=series,
            writer_positions=writers,
            total_exercised_quantity=total_exercised,
            settlement_date_d2=d2_date,
        )

        total_assigned = sum(a.assigned_quantity for a in assignments)
        total_unassigned = sum(a.unassigned_expired_quantity for a in assignments)

        if total_exercised != total_assigned:
            raise ConservationInvariantError(
                f"Violação da invariante de exercício na série '{series.series_ticker}': "
                f"Exercido ({total_exercised}) != Atribuído ({total_assigned})."
            )

        covered_assigned = sum(
            a.assigned_quantity
            for a in assignments
            if a.coverage_type == CoverageType.COVERED
        )
        uncovered_assigned = sum(
            a.assigned_quantity
            for a in assignments
            if a.coverage_type == CoverageType.UNCOVERED
        )
        spread_assigned = sum(
            a.assigned_quantity
            for a in assignments
            if a.coverage_type == CoverageType.SPREAD_LOCKED
        )

        spot_trades = self.generate_spot_exercise_trades(
            series=series,
            exercise_instructions=instructions,
            assignments=assignments,
        )

        total_holder_premium_d1 = sum(
            (p.gross_premium_brl for p in holders), Decimal("0.00")
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)
        total_writer_premium_d1 = sum(
            (p.gross_premium_brl for p in writers), Decimal("0.00")
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)

        total_exercise_notional_d2 = sum(
            (i.gross_exercise_amount_brl for i in instructions), Decimal("0.00")
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)

        total_intrinsic_exercised = sum(
            (i.intrinsic_gain_brl for i in instructions), Decimal("0.00")
        ).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)

        return ExpirationSettlementSummary(
            series_ticker=series.series_ticker,
            underlying_ticker=series.underlying_ticker,
            option_type=series.option_type,
            option_style=series.option_style,
            strike_price=series.strike_price,
            underlying_closing_price=spot,
            moneyness=moneyness,
            intrinsic_value_per_unit=intrinsic_unit,
            expiration_date=series.expiration_date,
            exercise_settlement_date_d2=d2_date,
            total_holder_open_quantity=total_holder_open,
            total_writer_open_quantity=total_writer_open,
            total_holder_exercised_quantity=total_exercised,
            total_holder_contrary_blocked_quantity=total_contrary_blocked,
            total_holder_lapsed_quantity=total_lapsed,
            total_writer_assigned_quantity=total_assigned,
            total_writer_unassigned_quantity=total_unassigned,
            total_writer_covered_assigned_quantity=covered_assigned,
            total_writer_uncovered_assigned_quantity=uncovered_assigned,
            total_writer_spread_assigned_quantity=spread_assigned,
            total_holder_premium_d1_brl=total_holder_premium_d1,
            total_writer_premium_d1_brl=total_writer_premium_d1,
            total_exercise_notional_d2_brl=total_exercise_notional_d2,
            total_intrinsic_value_exercised_brl=total_intrinsic_exercised,
            conservation_invariant_satisfied=(
                total_holder_open == total_writer_open
                and total_exercised == total_assigned
            ),
            exercise_instructions=tuple(instructions),
            assignments=tuple(assignments),
            generated_spot_trades=tuple(spot_trades),
        )

    def process_expiration_portfolio(
        self,
        series_list: Sequence[OptionSeries],
        positions: Sequence[OptionPosition],
        underlying_closing_prices: Mapping[str, Union[Decimal, str]],
        settlement_date_d2: Optional[date] = None,
    ) -> Dict[str, Any]:
        """Processa uma carteira completa de séries em vencimento e consolida as invariantes."""
        summaries: List[ExpirationSettlementSummary] = []
        all_spot_trades: List[Dict[str, Any]] = []

        total_holder_open_qty = 0
        total_writer_open_qty = 0
        total_exercised_qty = 0
        total_contrary_blocked_qty = 0
        total_lapsed_qty = 0
        total_assigned_qty = 0
        total_covered_assigned_qty = 0
        total_uncovered_assigned_qty = 0
        total_spread_assigned_qty = 0
        total_holder_premium_brl = Decimal("0.00")
        total_writer_premium_brl = Decimal("0.00")
        total_exercise_notional_brl = Decimal("0.00")
        total_intrinsic_exercised_brl = Decimal("0.00")

        for series in series_list:
            if series.underlying_ticker not in underlying_closing_prices:
                raise ListedOptionsValidationError(
                    f"Preço de fechamento ausente para o ativo-objeto '{series.underlying_ticker}'."
                )
            spot_price = underlying_closing_prices[series.underlying_ticker]
            summary = self.settle_series_expiration(
                series=series,
                positions=positions,
                underlying_closing_price=spot_price,
                settlement_date_d2=settlement_date_d2,
            )
            summaries.append(summary)
            all_spot_trades.extend(summary.generated_spot_trades)

            total_holder_open_qty += summary.total_holder_open_quantity
            total_writer_open_qty += summary.total_writer_open_quantity
            total_exercised_qty += summary.total_holder_exercised_quantity
            total_contrary_blocked_qty += summary.total_holder_contrary_blocked_quantity
            total_lapsed_qty += summary.total_holder_lapsed_quantity
            total_assigned_qty += summary.total_writer_assigned_quantity
            total_covered_assigned_qty += summary.total_writer_covered_assigned_quantity
            total_uncovered_assigned_qty += (
                summary.total_writer_uncovered_assigned_quantity
            )
            total_spread_assigned_qty += summary.total_writer_spread_assigned_quantity
            total_holder_premium_brl += summary.total_holder_premium_d1_brl
            total_writer_premium_brl += summary.total_writer_premium_d1_brl
            total_exercise_notional_brl += summary.total_exercise_notional_d2_brl
            total_intrinsic_exercised_brl += summary.total_intrinsic_value_exercised_brl

        return {
            "total_series": len(summaries),
            "total_holder_open_quantity": total_holder_open_qty,
            "total_writer_open_quantity": total_writer_open_qty,
            "total_exercised_quantity": total_exercised_qty,
            "total_contrary_blocked_quantity": total_contrary_blocked_qty,
            "total_lapsed_quantity": total_lapsed_qty,
            "total_assigned_quantity": total_assigned_qty,
            "total_covered_assigned_quantity": total_covered_assigned_qty,
            "total_uncovered_assigned_quantity": total_uncovered_assigned_qty,
            "total_spread_assigned_quantity": total_spread_assigned_qty,
            "total_holder_premium_d1_brl": total_holder_premium_brl.quantize(
                QUANTIZE_CENTS, rounding=ROUND_HALF_UP
            ),
            "total_writer_premium_d1_brl": total_writer_premium_brl.quantize(
                QUANTIZE_CENTS, rounding=ROUND_HALF_UP
            ),
            "total_exercise_notional_d2_brl": total_exercise_notional_brl.quantize(
                QUANTIZE_CENTS, rounding=ROUND_HALF_UP
            ),
            "total_intrinsic_value_exercised_brl": total_intrinsic_exercised_brl.quantize(
                QUANTIZE_CENTS, rounding=ROUND_HALF_UP
            ),
            "conservation_invariant_satisfied": (
                total_holder_open_qty == total_writer_open_qty
                and total_exercised_qty == total_assigned_qty
                and all(s.conservation_invariant_satisfied for s in summaries)
            ),
            "series_summaries": summaries,
            "spot_exercise_trades": all_spot_trades,
        }

    @staticmethod
    def load_fixture(
        fixture_path: Union[str, Path]
    ) -> Tuple[List[OptionSeries], List[OptionPosition], Dict[str, Decimal], date, Decimal]:
        """Carrega séries, posições e preços de fechamento a partir de um arquivo JSON."""
        path = Path(fixture_path)
        raw_data = json.loads(path.read_text(encoding="utf-8"))

        series_list = [
            OptionSeries.from_dict(item) for item in raw_data.get("series", [])
        ]
        positions = [
            OptionPosition.from_dict(item) for item in raw_data.get("positions", [])
        ]
        closing_prices: Dict[str, Decimal] = {
            ticker.upper(): _to_decimal(price, f"closing_price[{ticker}]")
            for ticker, price in raw_data.get("underlying_closing_prices", {}).items()
        }
        settlement_d2 = _to_date(
            raw_data["exercise_settlement_date_d2"], "exercise_settlement_date_d2"
        )
        min_intrinsic = _to_decimal(
            raw_data.get(
                "auto_exercise_min_intrinsic_brl",
                str(DEFAULT_AUTO_EXERCISE_MIN_INTRINSIC_BRL),
            ),
            "auto_exercise_min_intrinsic_brl",
        )
        return series_list, positions, closing_prices, settlement_d2, min_intrinsic

    def evaluate_fixture(self, fixture_path: Union[str, Path]) -> Dict[str, Any]:
        """Carrega e processa integralmente o arquivo de fixture JSON de vencimento de opções."""
        (
            series_list,
            positions,
            closing_prices,
            settlement_d2,
            min_intrinsic,
        ) = self.load_fixture(fixture_path)
        engine = ListedOptionsEngine(auto_exercise_min_intrinsic_brl=min_intrinsic)
        return engine.process_expiration_portfolio(
            series_list=series_list,
            positions=positions,
            underlying_closing_prices=closing_prices,
            settlement_date_d2=settlement_d2,
        )
