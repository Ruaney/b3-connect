"""Módulo de Derivativos Listados BM&F e Ajuste Diário de Contratos Futuros (FL-032).

Implementa a modelagem contratual, o cálculo determinístico de Preço Unitário (PU)
a partir da Taxa DI (base DU/252) e o motor de apuração de Ajuste Diário (Variation
Margin liquidado em D+1) para os principais contratos futuros listados na B3:
- DI1 (Futuro de Taxa Média de Depósitos Interfinanceiros de Um Dia)
- DOL (Futuro de Dólar Comercial — Lote Padrão US$ 50.000,00)
- WDO (Minicontrato Futuro de Dólar Comercial — US$ 10.000,00)
- IND (Futuro de Índice Bovespa — R$ 1,00 por ponto)
- WIN (Minicontrato Futuro de Índice Bovespa — R$ 0,20 por ponto)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Union


# ---------------------------------------------------------------------------
# Constantes de Precisão e Domínio B3
# ---------------------------------------------------------------------------

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")
DI1_NOTIONAL_PU = Decimal("100000.00")
BUSINESS_DAYS_YEAR = Decimal("252")

VALID_MATURITY_MONTH_CODES = frozenset(
    {"F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z"}
)
TICKER_REGEX = re.compile(r"^(DI1|DOL|WDO|IND|WIN)([FGHJKMNQUVXZ])(\d{2})$")


# ---------------------------------------------------------------------------
# Exceções Customizadas
# ---------------------------------------------------------------------------


class BmfFuturesError(Exception):
    """Exceção base para erros do motor de ajuste diário de futuros BM&F."""


class BmfFuturesValidationError(BmfFuturesError):
    """Erro de validação de dados cadastrais, ticker ou parâmetros da posição."""


class BmfFuturesCalculationError(BmfFuturesError):
    """Erro durante o cálculo matemático de PU ou ajuste diário."""


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------


class FutureContractFamily(str, Enum):
    """Famílias de contratos futuros listados no segmento BM&F da B3."""

    DI1 = "DI1"
    DOL = "DOL"
    WDO = "WDO"
    IND = "IND"
    WIN = "WIN"

    @classmethod
    def from_str(cls, value: Any) -> FutureContractFamily:
        """Converte string para FutureContractFamily."""
        if isinstance(value, FutureContractFamily):
            return value
        if not value:
            raise BmfFuturesValidationError(
                "Família de contrato futuro não pode ser vazia."
            )
        normalized = str(value).strip().upper()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise BmfFuturesValidationError(
                f"Família de contrato futuro inválida: {value!r}. "
                f"Esperado um de: {[e.value for e in cls]}"
            ) from exc

    @classmethod
    def from_ticker(cls, ticker: str) -> FutureContractFamily:
        """Extrai e valida a família de contrato a partir do código de negociação B3."""
        if not ticker or not isinstance(ticker, str):
            raise BmfFuturesValidationError("Ticker do contrato futuro inválido ou vazio.")
        normalized = ticker.strip().upper()
        match = TICKER_REGEX.match(normalized)
        if not match:
            raise BmfFuturesValidationError(
                f"Ticker {ticker!r} fora do padrão B3 (<FAMILIA><MES><ANO>, ex: DI1F27, DOLZ26)."
            )
        return cls(match.group(1))

    @property
    def point_multiplier(self) -> Decimal:
        """Valor financeiro em R$ de 1,00 ponto de PU/cotação por contrato.

        - DI1: R$ 1,00 por ponto de PU (PU nominal 100.000).
        - DOL: R$ 50,00 por ponto de cotação (US$ 50.000 / US$ 1.000).
        - WDO: R$ 10,00 por ponto de cotação (US$ 10.000 / US$ 1.000).
        - IND: R$ 1,00 por ponto de índice.
        - WIN: R$ 0,20 por ponto de índice.
        """
        multipliers = {
            FutureContractFamily.DI1: Decimal("1.00"),
            FutureContractFamily.DOL: Decimal("50.00"),
            FutureContractFamily.WDO: Decimal("10.00"),
            FutureContractFamily.IND: Decimal("1.00"),
            FutureContractFamily.WIN: Decimal("0.20"),
        }
        return multipliers[self]

    @property
    def contract_notional_size(self) -> Decimal:
        """Tamanho nominal de referência do contrato."""
        sizes = {
            FutureContractFamily.DI1: Decimal("100000.00"),  # R$ 100.000 no vencimento
            FutureContractFamily.DOL: Decimal("50000.00"),   # US$ 50.000
            FutureContractFamily.WDO: Decimal("10000.00"),   # US$ 10.000
            FutureContractFamily.IND: Decimal("1.00"),       # R$ 1,00 x Ibovespa
            FutureContractFamily.WIN: Decimal("0.20"),       # R$ 0,20 x Ibovespa
        }
        return sizes[self]

    @property
    def is_rate_inverted(self) -> bool:
        """Indica se a cotação em taxa possui relação inversa com o PU (caso do DI1)."""
        return self == FutureContractFamily.DI1


class PositionSide(str, Enum):
    """Natureza da posição ou operação (Comprado vs. Vendido na cotação do contrato)."""

    BUY = "BUY"
    SELL = "SELL"

    @classmethod
    def from_str(cls, value: Any) -> PositionSide:
        """Converte strings usuais de mercado para PositionSide."""
        if isinstance(value, PositionSide):
            return value
        if not value:
            raise BmfFuturesValidationError("Sentido da posição (side) não pode ser vazio.")
        normalized = str(value).strip().upper()
        mapping = {
            "BUY": cls.BUY,
            "C": cls.BUY,
            "COMPRA": cls.BUY,
            "COMPRADO": cls.BUY,
            "LONG": cls.BUY,
            "SELL": cls.SELL,
            "V": cls.SELL,
            "VENDA": cls.SELL,
            "VENDIDO": cls.SELL,
            "SHORT": cls.SELL,
        }
        if normalized in mapping:
            return mapping[normalized]
        raise BmfFuturesValidationError(
            f"Sentido de posição inválido: {value!r}. Aceitos: BUY, SELL."
        )


class PositionType(str, Enum):
    """Origem da posição para efeito de cálculo do Ajuste Diário."""

    CARRIED = "CARRIED"          # Posição aberta carregada de D-1
    NEW_POSITION = "NEW_POSITION"  # Posição aberta em D e mantida para o fechamento
    DAY_TRADE = "DAY_TRADE"      # Compra e venda encerradas no mesmo pregão D

    @classmethod
    def from_str(cls, value: Any) -> PositionType:
        """Converte string para PositionType."""
        if isinstance(value, PositionType):
            return value
        if not value:
            return cls.CARRIED
        normalized = str(value).strip().upper()
        mapping = {
            "CARRIED": cls.CARRIED,
            "OVERNIGHT": cls.CARRIED,
            "D-1": cls.CARRIED,
            "D_1": cls.CARRIED,
            "CARREGADA": cls.CARRIED,
            "NEW_POSITION": cls.NEW_POSITION,
            "NEW": cls.NEW_POSITION,
            "SAME_DAY": cls.NEW_POSITION,
            "ABERTA_NO_DIA": cls.NEW_POSITION,
            "DAY_TRADE": cls.DAY_TRADE,
            "DAYTRADE": cls.DAY_TRADE,
            "DT": cls.DAY_TRADE,
        }
        if normalized in mapping:
            return mapping[normalized]
        raise BmfFuturesValidationError(
            f"Tipo de posição inválido: {value!r}. Aceitos: CARRIED, NEW_POSITION, DAY_TRADE."
        )


# ---------------------------------------------------------------------------
# Utilitários de Conversão
# ---------------------------------------------------------------------------


def _to_decimal(value: Any, field_name: str) -> Decimal:
    """Converte valor numérico ou textual para Decimal com validação."""
    if value is None or value == "":
        raise BmfFuturesValidationError(f"Campo obrigatório ausente: {field_name}.")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise BmfFuturesValidationError(
            f"Valor numérico inválido para {field_name}: {value!r}."
        ) from exc


def _to_optional_decimal(value: Any, field_name: str) -> Optional[Decimal]:
    """Converte valor opcional para Decimal."""
    if value is None or value == "":
        return None
    return _to_decimal(value, field_name)


def _to_date(value: Any, field_name: str) -> date:
    """Converte string ISO-8601 ou objeto date/datetime em date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value or not isinstance(value, str):
        raise BmfFuturesValidationError(f"Data inválida para {field_name}: {value!r}.")
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise BmfFuturesValidationError(
            f"Formato de data inválido para {field_name}: {value!r}. Esperado YYYY-MM-DD."
        ) from exc


def _to_optional_date(value: Any, field_name: str) -> Optional[date]:
    """Converte data opcional."""
    if value is None or value == "":
        return None
    return _to_date(value, field_name)


# ---------------------------------------------------------------------------
# Modelos de Dados (Dataclasses)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FuturePosition:
    """Representa uma posição ou operação em contrato futuro listado na B3.

    Suporta:
    - Posições carregadas de D-1 (`CARRIED`)
    - Novas posições abertas no dia D (`NEW_POSITION`)
    - Operações de Day-Trade encerradas no dia D (`DAY_TRADE`)
    """

    position_id: str
    ticker: str
    contract_family: FutureContractFamily
    side: PositionSide
    quantity: int
    position_type: PositionType = PositionType.CARRIED
    maturity_date: Optional[date] = None
    business_days_to_maturity: Optional[int] = None
    # Parâmetros para contratos cotados em preço/pontos (DOL, WDO, IND, WIN)
    previous_settlement_price: Optional[Decimal] = None
    current_settlement_price: Optional[Decimal] = None
    entry_price: Optional[Decimal] = None
    exit_price: Optional[Decimal] = None
    # Parâmetros para contratos cotados em taxa/PU (DI1)
    previous_settlement_rate_pct: Optional[Decimal] = None
    previous_settlement_pu: Optional[Decimal] = None
    current_settlement_rate_pct: Optional[Decimal] = None
    current_settlement_pu: Optional[Decimal] = None
    entry_rate_pct: Optional[Decimal] = None
    entry_pu: Optional[Decimal] = None
    exit_rate_pct: Optional[Decimal] = None
    exit_pu: Optional[Decimal] = None
    apply_cdi_correction: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        """Executa validações estruturais e de domínio da posição."""
        if not self.position_id or not str(self.position_id).strip():
            raise BmfFuturesValidationError("position_id não pode ser vazio.")

        inferred_family = FutureContractFamily.from_ticker(self.ticker)
        if self.contract_family != inferred_family:
            raise BmfFuturesValidationError(
                f"Inconsistência entre ticker {self.ticker!r} ({inferred_family.value}) "
                f"e contract_family {self.contract_family.value!r}."
            )

        if not isinstance(self.quantity, int) or self.quantity <= 0:
            raise BmfFuturesValidationError(
                f"Quantidade de contratos deve ser inteiro positivo: {self.quantity!r}."
            )

        if self.contract_family == FutureContractFamily.DI1:
            self._validate_di1_fields()
        else:
            self._validate_price_contract_fields()

    def _validate_di1_fields(self) -> None:
        """Valida atributos obrigatórios de posições em Futuro de DI (DI1)."""
        if (
            self.business_days_to_maturity is not None
            and self.business_days_to_maturity < 0
        ):
            raise BmfFuturesValidationError(
                "business_days_to_maturity (DU) não pode ser negativo."
            )

        if self.position_type == PositionType.CARRIED:
            has_prev = (
                self.previous_settlement_pu is not None
                or self.previous_settlement_rate_pct is not None
            )
            has_curr = (
                self.current_settlement_pu is not None
                or self.current_settlement_rate_pct is not None
            )
            if not has_prev or not has_curr:
                raise BmfFuturesValidationError(
                    f"Posição DI1 carregada ({self.position_id}) requer PU ou taxa "
                    "de ajuste anterior (D-1) e atual (D)."
                )
        elif self.position_type == PositionType.NEW_POSITION:
            has_entry = self.entry_pu is not None or self.entry_rate_pct is not None
            has_curr = (
                self.current_settlement_pu is not None
                or self.current_settlement_rate_pct is not None
            )
            if not has_entry or not has_curr:
                raise BmfFuturesValidationError(
                    f"Nova posição DI1 ({self.position_id}) requer PU/taxa de entrada "
                    "e PU/taxa de ajuste atual (D)."
                )
        elif self.position_type == PositionType.DAY_TRADE:
            has_entry = self.entry_pu is not None or self.entry_rate_pct is not None
            has_exit = self.exit_pu is not None or self.exit_rate_pct is not None
            if not has_entry or not has_exit:
                raise BmfFuturesValidationError(
                    f"Day-trade DI1 ({self.position_id}) requer PU/taxa de entrada "
                    "e PU/taxa de saída."
                )

    def _validate_price_contract_fields(self) -> None:
        """Valida atributos obrigatórios para contratos cotados em preço/pontos (DOL, WDO, IND, WIN)."""
        if self.position_type == PositionType.CARRIED:
            if (
                self.previous_settlement_price is None
                or self.current_settlement_price is None
            ):
                raise BmfFuturesValidationError(
                    f"Posição carregada ({self.position_id}) em {self.contract_family.value} "
                    "requer previous_settlement_price e current_settlement_price."
                )
            if (
                self.previous_settlement_price <= Decimal("0")
                or self.current_settlement_price <= Decimal("0")
            ):
                raise BmfFuturesValidationError(
                    "Preços de ajuste devem ser estritamente positivos."
                )
        elif self.position_type == PositionType.NEW_POSITION:
            if self.entry_price is None or self.current_settlement_price is None:
                raise BmfFuturesValidationError(
                    f"Nova posição ({self.position_id}) em {self.contract_family.value} "
                    "requer entry_price e current_settlement_price."
                )
            if (
                self.entry_price <= Decimal("0")
                or self.current_settlement_price <= Decimal("0")
            ):
                raise BmfFuturesValidationError(
                    "Preço de entrada e ajuste devem ser estritamente positivos."
                )
        elif self.position_type == PositionType.DAY_TRADE:
            if self.entry_price is None or self.exit_price is None:
                raise BmfFuturesValidationError(
                    f"Day-trade ({self.position_id}) em {self.contract_family.value} "
                    "requer entry_price e exit_price."
                )
            if self.entry_price <= Decimal("0") or self.exit_price <= Decimal("0"):
                raise BmfFuturesValidationError(
                    "Preços de compra e venda no day-trade devem ser estritamente positivos."
                )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FuturePosition:
        """Constrói uma instância validada de FuturePosition a partir de dicionário."""
        ticker = str(data.get("ticker", "")).strip().upper()
        family_raw = data.get("contract_family")
        family = (
            FutureContractFamily.from_str(family_raw)
            if family_raw
            else FutureContractFamily.from_ticker(ticker)
        )
        return cls(
            position_id=str(data.get("position_id", "")).strip(),
            ticker=ticker,
            contract_family=family,
            side=PositionSide.from_str(data.get("side")),
            quantity=int(data.get("quantity", 0)),
            position_type=PositionType.from_str(data.get("position_type", "CARRIED")),
            maturity_date=_to_optional_date(data.get("maturity_date"), "maturity_date"),
            business_days_to_maturity=(
                int(data["business_days_to_maturity"])
                if data.get("business_days_to_maturity") is not None
                else None
            ),
            previous_settlement_price=_to_optional_decimal(
                data.get("previous_settlement_price"), "previous_settlement_price"
            ),
            current_settlement_price=_to_optional_decimal(
                data.get("current_settlement_price"), "current_settlement_price"
            ),
            entry_price=_to_optional_decimal(data.get("entry_price"), "entry_price"),
            exit_price=_to_optional_decimal(data.get("exit_price"), "exit_price"),
            previous_settlement_rate_pct=_to_optional_decimal(
                data.get("previous_settlement_rate_pct"), "previous_settlement_rate_pct"
            ),
            previous_settlement_pu=_to_optional_decimal(
                data.get("previous_settlement_pu"), "previous_settlement_pu"
            ),
            current_settlement_rate_pct=_to_optional_decimal(
                data.get("current_settlement_rate_pct"), "current_settlement_rate_pct"
            ),
            current_settlement_pu=_to_optional_decimal(
                data.get("current_settlement_pu"), "current_settlement_pu"
            ),
            entry_rate_pct=_to_optional_decimal(
                data.get("entry_rate_pct"), "entry_rate_pct"
            ),
            entry_pu=_to_optional_decimal(data.get("entry_pu"), "entry_pu"),
            exit_rate_pct=_to_optional_decimal(
                data.get("exit_rate_pct"), "exit_rate_pct"
            ),
            exit_pu=_to_optional_decimal(data.get("exit_pu"), "exit_pu"),
            apply_cdi_correction=bool(data.get("apply_cdi_correction", False)),
            description=str(data.get("description", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a posição para dicionário compatível com JSON."""
        raw = asdict(self)
        result: Dict[str, Any] = {}
        for k, v in raw.items():
            if isinstance(v, Enum):
                result[k] = v.value
            elif isinstance(v, Decimal):
                result[k] = str(v)
            elif isinstance(v, date):
                result[k] = v.isoformat()
            elif v is not None:
                result[k] = v
        return result


@dataclass(frozen=True)
class DailySettlementResult:
    """Resultado determinístico da apuração de Ajuste Diário de uma posição."""

    position_id: str
    ticker: str
    contract_family: FutureContractFamily
    position_type: PositionType
    side: PositionSide
    pu_direction: str  # "LONG_PU", "SHORT_PU", "LONG_PRICE", "SHORT_PRICE"
    quantity: int
    reference_value: Decimal       # PU ou Preço base (D-1 corrigido ou entrada)
    settlement_value: Decimal      # PU ou Preço final (Ajuste D ou saída day-trade)
    variation_points: Decimal      # Diferença direcionada de pontos/PU por contrato
    point_multiplier: Decimal      # Multiplicador R$/ponto por contrato
    unit_settlement_brl: Decimal   # Ajuste financeiro de 1 contrato em R$
    settlement_amount_brl: Decimal  # Ajuste financeiro total da posição em R$ (2 casas)
    trade_date: date
    settlement_date: date          # Data de liquidação financeira (D+1)
    is_credit: bool                # True se valor > 0 (crédito ao participante)

    def to_dict(self) -> Dict[str, Any]:
        """Converte o resultado para dicionário serializável em JSON."""
        return {
            "position_id": self.position_id,
            "ticker": self.ticker,
            "contract_family": self.contract_family.value,
            "position_type": self.position_type.value,
            "side": self.side.value,
            "pu_direction": self.pu_direction,
            "quantity": self.quantity,
            "reference_value": str(self.reference_value),
            "settlement_value": str(self.settlement_value),
            "variation_points": str(self.variation_points),
            "point_multiplier": str(self.point_multiplier),
            "unit_settlement_brl": str(self.unit_settlement_brl),
            "settlement_amount_brl": str(self.settlement_amount_brl),
            "trade_date": self.trade_date.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
            "is_credit": self.is_credit,
        }


@dataclass(frozen=True)
class PortfolioSettlementSummary:
    """Consolidação multilateral do Ajuste Diário de uma carteira de futuros em D+1."""

    portfolio_id: str
    participant_code: str
    account_id: str
    trade_date: date
    settlement_date: date
    results: List[DailySettlementResult] = field(default_factory=list)
    total_credits_brl: Decimal = Decimal("0.00")
    total_debits_brl: Decimal = Decimal("0.00")
    net_settlement_brl: Decimal = Decimal("0.00")
    by_family_net_brl: Dict[str, Decimal] = field(default_factory=dict)

    @property
    def net_financial_status(self) -> str:
        """Retorna 'CREDIT', 'DEBIT' ou 'FLAT' conforme o saldo líquido em D+1."""
        if self.net_settlement_brl > Decimal("0.00"):
            return "CREDIT"
        if self.net_settlement_brl < Decimal("0.00"):
            return "DEBIT"
        return "FLAT"

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o resumo consolidado da carteira para dicionário."""
        return {
            "portfolio_id": self.portfolio_id,
            "participant_code": self.participant_code,
            "account_id": self.account_id,
            "trade_date": self.trade_date.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
            "net_financial_status": self.net_financial_status,
            "total_credits_brl": str(self.total_credits_brl),
            "total_debits_brl": str(self.total_debits_brl),
            "net_settlement_brl": str(self.net_settlement_brl),
            "by_family_net_brl": {k: str(v) for k, v in self.by_family_net_brl.items()},
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Motor de Cálculo de PU e Ajuste Diário BM&F
# ---------------------------------------------------------------------------


class BmfFuturesSettlementEngine:
    """Motor determinístico de cálculo de PU e Ajuste Diário de Futuros BM&F (B3)."""

    @staticmethod
    def calculate_di1_pu(
        rate_pct: Union[Decimal, str, float, int],
        business_days: int,
        precision: int = 2,
    ) -> Decimal:
        """Calcula o Preço Unitário (PU) de um contrato Futuro de DI1.

        Fórmula B3:
            PU = 100.000 / (1 + taxa / 100) ** (DU / 252)

        Args:
            rate_pct: Taxa efetiva anual em percentual (ex.: 11.50 para 11,50% a.a.).
            business_days: Número de dias úteis (DU) até o vencimento.
            precision: Casas decimais de arredondamento (padrão 2 casas = 0.01;
                aceita 6 casas para alta precisão).

        Returns:
            Decimal representando o PU arredondado com ROUND_HALF_UP.
        """
        rate_dec = _to_decimal(rate_pct, "rate_pct")
        if rate_dec <= Decimal("-100"):
            raise BmfFuturesCalculationError(
                f"Taxa de juros DI1 inválida (<= -100%): {rate_dec}."
            )
        if not isinstance(business_days, int) or business_days < 0:
            raise BmfFuturesCalculationError(
                f"Prazo em dias úteis (business_days) deve ser inteiro >= 0: {business_days!r}."
            )
        if precision < 0:
            raise BmfFuturesCalculationError("Precisão decimal não pode ser negativa.")

        quantizer = Decimal("10") ** (-precision) if precision > 0 else Decimal("1")

        if business_days == 0:
            return DI1_NOTIONAL_PU.quantize(quantizer, rounding=ROUND_HALF_UP)

        with localcontext() as ctx:
            ctx.prec = 50
            base = Decimal("1") + (rate_dec / Decimal("100"))
            exponent = Decimal(business_days) / BUSINESS_DAYS_YEAR
            # x ** y = exp(y * ln(x)) com 50 dígitos de precisão significativa
            discount_factor = (exponent * base.ln()).exp()
            raw_pu = DI1_NOTIONAL_PU / discount_factor
            return raw_pu.quantize(quantizer, rounding=ROUND_HALF_UP)

    @staticmethod
    def correct_di1_previous_pu(
        previous_pu: Union[Decimal, str, float],
        cdi_overnight_rate_pct: Union[Decimal, str, float],
        precision: int = 2,
    ) -> Decimal:
        """Corrige o PU de Ajuste de D-1 por 1 dia útil pela taxa DI over (CDI de D-1).

        Fórmula B3:
            PA_{t-1}^* = PA_{t-1} * (1 + CDI_{t-1} / 100) ** (1 / 252)
        """
        prev_pu_dec = _to_decimal(previous_pu, "previous_pu")
        cdi_dec = _to_decimal(cdi_overnight_rate_pct, "cdi_overnight_rate_pct")
        if prev_pu_dec <= Decimal("0"):
            raise BmfFuturesCalculationError("previous_pu deve ser positivo.")
        if cdi_dec <= Decimal("-100"):
            raise BmfFuturesCalculationError("Taxa CDI overnight inválida.")

        quantizer = Decimal("10") ** (-precision) if precision > 0 else Decimal("1")

        with localcontext() as ctx:
            ctx.prec = 50
            base = Decimal("1") + (cdi_dec / Decimal("100"))
            daily_factor = (Decimal("1") / BUSINESS_DAYS_YEAR * base.ln()).exp()
            corrected = prev_pu_dec * daily_factor
            return corrected.quantize(quantizer, rounding=ROUND_HALF_UP)

    @staticmethod
    def calculate_next_business_day(reference_date: date) -> date:
        """Calcula o próximo dia útil (D+1) para liquidação financeira do ajuste."""
        next_day = reference_date + timedelta(days=1)
        while next_day.weekday() >= 5:  # 5 = Sábado, 6 = Domingo
            next_day += timedelta(days=1)
        return next_day

    def _resolve_di1_pu(
        self,
        explicit_pu: Optional[Decimal],
        rate_pct: Optional[Decimal],
        business_days: Optional[int],
        precision: int,
        label: str,
    ) -> Decimal:
        """Obtém o PU do DI1 a partir do valor explícito ou calcula via taxa e DU."""
        quantizer = Decimal("10") ** (-precision) if precision > 0 else Decimal("1")
        if explicit_pu is not None:
            return explicit_pu.quantize(quantizer, rounding=ROUND_HALF_UP)
        if rate_pct is None or business_days is None:
            raise BmfFuturesCalculationError(
                f"Não foi possível determinar o PU ({label}): informe o PU explícito "
                "ou o par (taxa %, business_days_to_maturity)."
            )
        return self.calculate_di1_pu(rate_pct, business_days, precision=precision)

    def calculate_position_settlement(
        self,
        position: FuturePosition,
        trade_date: Optional[date] = None,
        settlement_date: Optional[date] = None,
        cdi_overnight_rate_pct: Optional[Union[Decimal, str, float]] = None,
        high_precision_pu: bool = False,
    ) -> DailySettlementResult:
        """Calcula o Ajuste Diário (D+1) de uma posição individual em futuro BM&F.

        Regras de sentido:
        - Para `DI1`:
          * `BUY` (Comprado em Taxa) = Vendido em PU (`SHORT_PU`).
            Ganha quando a taxa sobe e o PU cai: `(PU_ref - PU_ajuste) * Q * 1.00`.
          * `SELL` (Vendido em Taxa) = Comprado em PU (`LONG_PU`).
            Ganha quando a taxa cai e o PU sobe: `(PU_ajuste - PU_ref) * Q * 1.00`.
        - Para `DOL`, `WDO`, `IND`, `WIN`:
          * `BUY` (Comprado em Preço/Índice) = `LONG_PRICE`.
            Ganha quando a cotação sobe: `(P_ajuste - P_ref) * M * Q`.
          * `SELL` (Vendido em Preço/Índice) = `SHORT_PRICE`.
            Ganha quando a cotação cai: `(P_ref - P_ajuste) * M * Q`.
        """
        eff_trade_date = trade_date or date.today()
        eff_settlement_date = settlement_date or self.calculate_next_business_day(
            eff_trade_date
        )
        pu_precision = 6 if high_precision_pu else 2
        multiplier = position.contract_family.point_multiplier

        if position.contract_family == FutureContractFamily.DI1:
            return self._settle_di1_position(
                position=position,
                trade_date=eff_trade_date,
                settlement_date=eff_settlement_date,
                cdi_overnight_rate_pct=cdi_overnight_rate_pct,
                pu_precision=pu_precision,
                multiplier=multiplier,
            )

        return self._settle_price_position(
            position=position,
            trade_date=eff_trade_date,
            settlement_date=eff_settlement_date,
            multiplier=multiplier,
        )

    def _settle_di1_position(
        self,
        position: FuturePosition,
        trade_date: date,
        settlement_date: date,
        cdi_overnight_rate_pct: Optional[Union[Decimal, str, float]],
        pu_precision: int,
        multiplier: Decimal,
    ) -> DailySettlementResult:
        """Calcula o ajuste diário para o contrato Futuro de DI1 (inversão Taxa vs PU)."""
        du = position.business_days_to_maturity
        # Comprado em Taxa (BUY) = Vendido em PU (SHORT_PU)
        # Vendido em Taxa (SELL) = Comprado em PU (LONG_PU)
        pu_direction = (
            "SHORT_PU" if position.side == PositionSide.BUY else "LONG_PU"
        )

        if position.position_type == PositionType.CARRIED:
            # Se previous_settlement_pu não foi fornecido, calcula com DU + 1 (ou DU informado)
            prev_du = (du + 1) if (du is not None and position.apply_cdi_correction) else du
            ref_pu = self._resolve_di1_pu(
                explicit_pu=position.previous_settlement_pu,
                rate_pct=position.previous_settlement_rate_pct,
                business_days=prev_du,
                precision=pu_precision,
                label="previous_settlement_pu",
            )
            if position.apply_cdi_correction:
                if cdi_overnight_rate_pct is None:
                    raise BmfFuturesCalculationError(
                        f"Posição {position.position_id} requer cdi_overnight_rate_pct "
                        "para correção do PU de D-1."
                    )
                ref_pu = self.correct_di1_previous_pu(
                    ref_pu, cdi_overnight_rate_pct, precision=pu_precision
                )

            curr_pu = self._resolve_di1_pu(
                explicit_pu=position.current_settlement_pu,
                rate_pct=position.current_settlement_rate_pct,
                business_days=du,
                precision=pu_precision,
                label="current_settlement_pu",
            )

            # Em DI1: Comprado em taxa (SHORT_PU) ganha quando PU cai: (ref_pu - curr_pu)
            # Vendido em taxa (LONG_PU) ganha quando PU sobe: (curr_pu - ref_pu)
            if position.side == PositionSide.BUY:
                variation_points = ref_pu - curr_pu
            else:
                variation_points = curr_pu - ref_pu

        elif position.position_type == PositionType.NEW_POSITION:
            ref_pu = self._resolve_di1_pu(
                explicit_pu=position.entry_pu,
                rate_pct=position.entry_rate_pct,
                business_days=du,
                precision=pu_precision,
                label="entry_pu",
            )
            curr_pu = self._resolve_di1_pu(
                explicit_pu=position.current_settlement_pu,
                rate_pct=position.current_settlement_rate_pct,
                business_days=du,
                precision=pu_precision,
                label="current_settlement_pu",
            )
            if position.side == PositionSide.BUY:
                variation_points = ref_pu - curr_pu
            else:
                variation_points = curr_pu - ref_pu

        else:  # DAY_TRADE
            ref_pu = self._resolve_di1_pu(
                explicit_pu=position.entry_pu,
                rate_pct=position.entry_rate_pct,
                business_days=du,
                precision=pu_precision,
                label="entry_pu",
            )
            curr_pu = self._resolve_di1_pu(
                explicit_pu=position.exit_pu,
                rate_pct=position.exit_rate_pct,
                business_days=du,
                precision=pu_precision,
                label="exit_pu",
            )
            # Se entrou Comprado em Taxa (BUY = vendeu PU na entrada a ref_pu) e
            # encerrou Vendido em Taxa (comprou PU na saída a curr_pu): resultado = ref_pu - curr_pu.
            # Se entrou Vendido em Taxa (SELL = comprou PU na entrada a ref_pu) e
            # encerrou Comprado em Taxa (vendeu PU na saída a curr_pu): resultado = curr_pu - ref_pu.
            if position.side == PositionSide.BUY:
                variation_points = ref_pu - curr_pu
            else:
                variation_points = curr_pu - ref_pu

        unit_settlement = (variation_points * multiplier).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        total_settlement = (
            variation_points * multiplier * Decimal(position.quantity)
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        return DailySettlementResult(
            position_id=position.position_id,
            ticker=position.ticker,
            contract_family=position.contract_family,
            position_type=position.position_type,
            side=position.side,
            pu_direction=pu_direction,
            quantity=position.quantity,
            reference_value=ref_pu,
            settlement_value=curr_pu,
            variation_points=variation_points,
            point_multiplier=multiplier,
            unit_settlement_brl=unit_settlement,
            settlement_amount_brl=total_settlement,
            trade_date=trade_date,
            settlement_date=settlement_date,
            is_credit=total_settlement > Decimal("0.00"),
        )

    def _settle_price_position(
        self,
        position: FuturePosition,
        trade_date: date,
        settlement_date: date,
        multiplier: Decimal,
    ) -> DailySettlementResult:
        """Calcula o ajuste diário para contratos cotados em preço/pontos (DOL, WDO, IND, WIN)."""
        pu_direction = (
            "LONG_PRICE" if position.side == PositionSide.BUY else "SHORT_PRICE"
        )

        if position.position_type == PositionType.CARRIED:
            assert position.previous_settlement_price is not None
            assert position.current_settlement_price is not None
            ref_price = position.previous_settlement_price.quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP
            )
            curr_price = position.current_settlement_price.quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP
            )
        elif position.position_type == PositionType.NEW_POSITION:
            assert position.entry_price is not None
            assert position.current_settlement_price is not None
            ref_price = position.entry_price.quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP
            )
            curr_price = position.current_settlement_price.quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP
            )
        else:  # DAY_TRADE
            assert position.entry_price is not None
            assert position.exit_price is not None
            ref_price = position.entry_price.quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP
            )
            curr_price = position.exit_price.quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP
            )

        if position.side == PositionSide.BUY:
            variation_points = curr_price - ref_price
        else:
            variation_points = ref_price - curr_price

        unit_settlement = (variation_points * multiplier).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        total_settlement = (
            variation_points * multiplier * Decimal(position.quantity)
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        return DailySettlementResult(
            position_id=position.position_id,
            ticker=position.ticker,
            contract_family=position.contract_family,
            position_type=position.position_type,
            side=position.side,
            pu_direction=pu_direction,
            quantity=position.quantity,
            reference_value=ref_price,
            settlement_value=curr_price,
            variation_points=variation_points,
            point_multiplier=multiplier,
            unit_settlement_brl=unit_settlement,
            settlement_amount_brl=total_settlement,
            trade_date=trade_date,
            settlement_date=settlement_date,
            is_credit=total_settlement > Decimal("0.00"),
        )

    def settle_portfolio(
        self,
        positions: Sequence[FuturePosition],
        portfolio_id: str = "DEFAULT-PORTFOLIO",
        participant_code: str = "00000",
        account_id: str = "000000-0",
        trade_date: Optional[date] = None,
        settlement_date: Optional[date] = None,
        cdi_overnight_rate_pct: Optional[Union[Decimal, str, float]] = None,
        high_precision_pu: bool = False,
    ) -> PortfolioSettlementSummary:
        """Apura e consolida o Ajuste Diário multilateral de uma carteira de futuros."""
        eff_trade_date = trade_date or date.today()
        eff_settlement_date = settlement_date or self.calculate_next_business_day(
            eff_trade_date
        )

        results: List[DailySettlementResult] = []
        total_credits = Decimal("0.00")
        total_debits = Decimal("0.00")
        by_family: Dict[str, Decimal] = {}

        for pos in positions:
            res = self.calculate_position_settlement(
                position=pos,
                trade_date=eff_trade_date,
                settlement_date=eff_settlement_date,
                cdi_overnight_rate_pct=cdi_overnight_rate_pct,
                high_precision_pu=high_precision_pu,
            )
            results.append(res)
            amount = res.settlement_amount_brl
            if amount > Decimal("0.00"):
                total_credits += amount
            elif amount < Decimal("0.00"):
                total_debits += amount

            fam_key = res.contract_family.value
            by_family[fam_key] = by_family.get(fam_key, Decimal("0.00")) + amount

        net_settlement = (total_credits + total_debits).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )

        return PortfolioSettlementSummary(
            portfolio_id=portfolio_id,
            participant_code=participant_code,
            account_id=account_id,
            trade_date=eff_trade_date,
            settlement_date=eff_settlement_date,
            results=results,
            total_credits_brl=total_credits.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            total_debits_brl=total_debits.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            net_settlement_brl=net_settlement,
            by_family_net_brl=by_family,
        )

    def settle_from_json(
        self,
        source: Union[str, Path, Dict[str, Any]],
        high_precision_pu: bool = False,
    ) -> PortfolioSettlementSummary:
        """Carrega uma carteira de posições futuras de um arquivo/payload JSON e apura o ajuste."""
        if isinstance(source, dict):
            payload = source
        elif isinstance(source, Path):
            payload = json.loads(source.read_text(encoding="utf-8"))
        elif isinstance(source, str):
            stripped = source.strip()
            if stripped.startswith("{"):
                payload = json.loads(stripped)
            else:
                payload = json.loads(Path(source).read_text(encoding="utf-8"))
        else:
            raise BmfFuturesValidationError(
                f"Fonte de dados JSON inválida: {type(source)!r}."
            )

        portfolio_id = str(payload.get("portfolio_id", "DEFAULT-PORTFOLIO"))
        participant_code = str(payload.get("participant_code", "00000"))
        account_id = str(payload.get("account_id", "000000-0"))
        trade_date = _to_date(payload.get("trade_date", "2026-11-03"), "trade_date")
        settlement_date = _to_optional_date(
            payload.get("settlement_date"), "settlement_date"
        )
        cdi_rate = _to_optional_decimal(
            payload.get("cdi_overnight_rate_pct"), "cdi_overnight_rate_pct"
        )

        raw_positions = payload.get("positions")
        if not isinstance(raw_positions, list) or not raw_positions:
            raise BmfFuturesValidationError(
                "O payload JSON deve conter uma lista não vazia em 'positions'."
            )

        positions = [FuturePosition.from_dict(item) for item in raw_positions]
        return self.settle_portfolio(
            positions=positions,
            portfolio_id=portfolio_id,
            participant_code=participant_code,
            account_id=account_id,
            trade_date=trade_date,
            settlement_date=settlement_date,
            cdi_overnight_rate_pct=cdi_rate,
            high_precision_pu=high_precision_pu,
        )
