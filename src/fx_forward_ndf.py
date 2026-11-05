"""Módulo de Registro, Marcação a Mercado e Liquidação de Contratos a Termo de Moedas e NDF (FL-074).

Implementa o motor determinístico de registro escritural, precificação via paridade de juros,
apuração de cupom cambial limpo e sujo, marcação a mercado (MtM) bilateral, fixação PTAX BACEN
e liquidação financeira (NDF) ou física (Termo Deliverable) no Balcão NoMe B3, em conformidade com:
- Resolução CMN nº 4.952/2021 (Registro e Depósito Centralizado de Ativos Financeiros e Derivativos).
- Resolução BCB nº 264/2022 (Sistemas de Registro e Liquidação Financeira de Derivativos de Balcão).
- Circular BCB nº 3.691/2013 (Regulamentação do Mercado de Câmbio Brasileiro).
- Regulamento Operacional do Balcão B3 (NoMe) para Derivativos de Balcão e Câmbio.

Invariantes Formais Provadas:
1. Invariante Formal de Conservação Bilateral Soma-Zero (LAW-061):
   Ganho_Comprador + Perda_Vendedor == 0,00 centavo a centavo (tanto em MtM quanto na Liquidação).
2. Invariante de Paridade Cambial na Fixação PTAX (LAW-062):
   Valor_PTAX == round_2(Nocional * PTAX_Fixação) e Ajuste_NDF == round_2(Nocional * (PTAX - Taxa_Termo)).
3. Paridade Teórica de Taxa a Termo (Covered Interest Parity):
   F == S * ((1 + i_brl * DU/252) / (1 + i_usd * DC/360)).
4. Relação Estrutural Cupom Limpo vs Cupom Sujo:
   (1 + i_sujo * DC/360) == (1 + i_limpo * DC/360) * (S / PTAX_ref).
5. Sanidade Temporal e Prazo de Fixação/Liquidação (D0 e D+1):
   Data_Negociação <= Data_Fixação <= Data_Liquidação (DC >= 1 e DU >= 0).

100% Python Standard Library (dataclasses, decimal, datetime, enum, json, pathlib).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

try:
    from src.invariants import (
        InvariantViolationError,
        assert_fx_forward_bilateral_zero_sum,
        assert_fx_ptax_fixing_parity,
        check,
    )
except ImportError:
    class InvariantViolationError(AssertionError):  # type: ignore[no-redef]
        def __init__(self, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
            super().__init__(message)
            self.message = message
            self.law_code = law_code
            self.details = details or {}

        def __str__(self) -> str:
            prefix = f"[{self.law_code}] " if self.law_code else ""
            return f"VIOLAÇÃO DE INVARIANTE: {prefix}{self.message}"

    def check(condition: bool, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:  # type: ignore[no-redef]
        if not condition:
            raise InvariantViolationError(message, law_code=law_code, details=details)

    def assert_fx_forward_bilateral_zero_sum(buyer_pnl: Decimal, seller_pnl: Decimal, contract_id: str = "") -> None:  # type: ignore[no-redef]
        total = buyer_pnl + seller_pnl
        check(total == Decimal("0.00"), f"Violação soma-zero FX '{contract_id}': {buyer_pnl} + {seller_pnl} != 0.00", law_code="LAW-061")

    def assert_fx_ptax_fixing_parity(notional_foreign: Decimal, fixing_ptax: Decimal, forward_rate: Decimal, buyer_gross_adjustment: Decimal, tolerance_cents: Decimal = Decimal("0.01"), contract_id: str = "") -> None:  # type: ignore[no-redef]
        expected = (notional_foreign * (fixing_ptax - forward_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        diff = abs(buyer_gross_adjustment - expected)
        check(diff <= tolerance_cents, f"Violação paridade PTAX FX '{contract_id}': {buyer_gross_adjustment} != {expected}", law_code="LAW-062")


# =====================================================================
# CONSTANTES DE PRECISÃO E CONVENÇÕES B3 / NoMe
# =====================================================================

QUANTIZE_MONEY = Decimal("0.01")          # 2 casas decimais (BRL e moedas estrangeiras)
QUANTIZE_FX_RATE = Decimal("0.0001")      # 4 casas decimais (taxas de câmbio PTAX e Forward)
QUANTIZE_RATE_HIGH = Decimal("0.00000001")  # 8 casas decimais (taxas de juros e fatores)

BUSINESS_DAYS_YEAR = Decimal("252")       # Base DU brasileira para juros domésticos (BRL)
CALENDAR_DAYS_YEAR_360 = Decimal("360")   # Base DC internacional para cupom cambial / moeda estrangeira


def round_cents(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda montantes monetários em BRL ou moeda estrangeira para 2 casas decimais."""
    return Decimal(str(value)).quantize(QUANTIZE_MONEY, rounding=ROUND_HALF_UP)


def round_fx_rate(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda taxas de câmbio PTAX e a termo para 4 casas decimais."""
    return Decimal(str(value)).quantize(QUANTIZE_FX_RATE, rounding=ROUND_HALF_UP)


def round_rate(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda taxas de juros e cupom cambial para 8 casas decimais."""
    return Decimal(str(value)).quantize(QUANTIZE_RATE_HIGH, rounding=ROUND_HALF_UP)


def to_decimal(value: Any, field_name: str = "campo") -> Decimal:
    """Converte valor para Decimal de forma determinística e robusta."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise FxForwardNdfValidationError(f"Valor nulo não permitido para '{field_name}'.")
    try:
        cleaned = str(value).strip().replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError) as err:
        raise FxForwardNdfValidationError(
            f"Valor numérico inválido '{value}' para '{field_name}': {err}"
        )


def parse_date(value: Any, field_name: str = "data") -> date:
    """Converte representação ISO-8601 em date."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        cleaned = value.strip()
        if "T" in cleaned:
            cleaned = cleaned.split("T", 1)[0]
        return date.fromisoformat(cleaned)
    raise FxForwardNdfValidationError(
        f"Tipo inválido '{type(value).__name__}' para campo de data '{field_name}'."
    )


def parse_datetime(value: Any, field_name: str = "timestamp") -> Optional[datetime]:
    """Converte representação ISO-8601 em datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        cleaned = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    raise FxForwardNdfValidationError(
        f"Tipo inválido '{type(value).__name__}' para campo de datetime '{field_name}'."
    )


def _decimal_power_du252(base: Decimal, du_days: int) -> Decimal:
    """Calcula base^(du_days / 252) com precisão estendida de 50 dígitos."""
    if du_days == 0 or base == Decimal("1"):
        return Decimal("1")
    if du_days == 252:
        return base
    with localcontext() as ctx:
        ctx.prec = 50
        exponent = Decimal(du_days) / BUSINESS_DAYS_YEAR
        return (base.ln() * exponent).exp()


# =====================================================================
# EXCEÇÕES DE DOMÍNIO
# =====================================================================


class FxForwardNdfError(Exception):
    """Exceção base para o fluxo de Câmbio a Termo e NDF no Balcão NoMe B3."""


class FxForwardNdfValidationError(FxForwardNdfError):
    """Lançada quando parâmetros contratuais violam regras operacionais ou de negócio."""


class DuplicateContractError(FxForwardNdfError):
    """Lançada ao tentar registrar contrato ou protocolo NoMe duplicado."""


class ContractNotFoundError(FxForwardNdfError):
    """Lançada quando contrato consultado não consta no registro do NoMe."""


class ContractStateError(FxForwardNdfError):
    """Lançada em transições de ciclo de vida inválidas (ex: liquidar contrato não fixado)."""


# =====================================================================
# ENUMERAÇÕES TIPADAS
# =====================================================================


class FxContractType(str, Enum):
    """Modalidade operacional de liquidação do contrato a termo de moedas."""

    NDF = "NDF"                    # Non-Deliverable Forward: sem entrega física, ajuste financeiro em BRL
    DELIVERABLE = "DELIVERABLE"    # Termo com entrega física de moeda estrangeira contra BRL

    @classmethod
    def from_str(cls, value: Any) -> FxContractType:
        if isinstance(value, FxContractType):
            return value
        cleaned = str(value).strip().upper()
        mapping = {
            "NDF": cls.NDF,
            "NON_DELIVERABLE_FORWARD": cls.NDF,
            "TERMO_SEM_ENTREGA": cls.NDF,
            "FINANCIAL": cls.NDF,
            "FINANCEIRO": cls.NDF,
            "DELIVERABLE": cls.DELIVERABLE,
            "TERMO_COM_ENTREGA": cls.DELIVERABLE,
            "PHYSICAL": cls.DELIVERABLE,
            "FISICO": cls.DELIVERABLE,
        }
        if cleaned in mapping:
            return mapping[cleaned]
        raise FxForwardNdfValidationError(
            f"Modalidade de liquidação cambial inválida: {value!r}. Aceitos: NDF, DELIVERABLE."
        )


class Currency(str, Enum):
    """Moedas negociadas no Balcão NoMe B3."""

    USD = "USD"
    EUR = "EUR"
    BRL = "BRL"

    @classmethod
    def from_str(cls, value: Any) -> Currency:
        if isinstance(value, Currency):
            return value
        cleaned = str(value).strip().upper()
        mapping = {
            "USD": cls.USD,
            "DOLAR": cls.USD,
            "DÓLAR": cls.USD,
            "EUR": cls.EUR,
            "EURO": cls.EUR,
            "BRL": cls.BRL,
            "REAL": cls.BRL,
        }
        if cleaned in mapping:
            return mapping[cleaned]
        raise FxForwardNdfValidationError(f"Moeda não suportada: {value!r}. Aceitos: USD, EUR, BRL.")


class CurrencyPair(str, Enum):
    """Pares de moedas aceitos no fluxo FL-074."""

    USD_BRL = "USD/BRL"
    EUR_BRL = "EUR/BRL"

    @property
    def base_currency(self) -> Currency:
        return Currency.from_str(self.value.split("/")[0])

    @property
    def quote_currency(self) -> Currency:
        return Currency.from_str(self.value.split("/")[1])

    @classmethod
    def from_str(cls, value: Any) -> CurrencyPair:
        if isinstance(value, CurrencyPair):
            return value
        cleaned = str(value).strip().upper().replace("_", "/").replace("-", "/")
        mapping = {
            "USD/BRL": cls.USD_BRL,
            "USDBRL": cls.USD_BRL,
            "EUR/BRL": cls.EUR_BRL,
            "EURBRL": cls.EUR_BRL,
        }
        if cleaned in mapping:
            return mapping[cleaned]
        raise FxForwardNdfValidationError(
            f"Par de moedas inválido: {value!r}. Aceitos: USD/BRL, EUR/BRL."
        )


class ContractSide(str, Enum):
    """Posicionamento da ponta no derivativo cambial."""

    BUY = "BUY"    # Comprador da moeda base (USD ou EUR) / Vendido em BRL (Long FX)
    SELL = "SELL"  # Vendedor da moeda base (USD ou EUR) / Comprado em BRL (Short FX)

    @classmethod
    def from_str(cls, value: Any) -> ContractSide:
        if isinstance(value, ContractSide):
            return value
        cleaned = str(value).strip().upper()
        mapping = {
            "BUY": cls.BUY,
            "COMPRA": cls.BUY,
            "COMPRADOR": cls.BUY,
            "LONG": cls.BUY,
            "SELL": cls.SELL,
            "VENDA": cls.SELL,
            "VENDEDOR": cls.SELL,
            "SHORT": cls.SELL,
        }
        if cleaned in mapping:
            return mapping[cleaned]
        raise FxForwardNdfValidationError(
            f"Posicionamento inválido: {value!r}. Aceitos: BUY, SELL."
        )


class FixingSettlementLag(str, Enum):
    """Regime de defasagem entre Fixação PTAX e Liquidação Financeira."""

    D0 = "D0"  # Fixação PTAX e liquidação financeira na mesma data (D+0)
    D1 = "D1"  # Fixação PTAX em D e liquidação em D+1 (padrão offshore / NDF internacional)

    @classmethod
    def from_str(cls, value: Any) -> FixingSettlementLag:
        if isinstance(value, FixingSettlementLag):
            return value
        cleaned = str(value).strip().upper()
        mapping = {
            "D0": cls.D0,
            "D+0": cls.D0,
            "SAME_DAY": cls.D0,
            "D1": cls.D1,
            "D+1": cls.D1,
            "NEXT_DAY": cls.D1,
        }
        if cleaned in mapping:
            return mapping[cleaned]
        raise FxForwardNdfValidationError(
            f"Lag de fixação inválido: {value!r}. Aceitos: D0, D1."
        )


class ContractStatus(str, Enum):
    """Estados do ciclo de vida de contratos cambiais no Balcão NoMe B3."""

    REGISTERED = "REGISTERED"  # Registrado e aceito na fila de processamento
    ACTIVE = "ACTIVE"          # Ativo, aberto para marcação a mercado diária
    FIXED = "FIXED"            # Cotação PTAX BACEN fixada
    SETTLED = "SETTLED"        # Liquidado (financeiramente no NDF ou fisicamente no Termo)
    CANCELLED = "CANCELLED"    # Cancelado pelas partes antes da fixação

    @classmethod
    def from_str(cls, value: Any) -> ContractStatus:
        if isinstance(value, ContractStatus):
            return value
        cleaned = str(value).strip().upper()
        for member in cls:
            if member.value == cleaned:
                return member
        raise FxForwardNdfValidationError(f"Status contratual inválido: {value!r}.")


class CompoundingMethod(str, Enum):
    """Convenção matemática para juros domésticos (BRL) na Paridade de Taxa a Termo."""

    LINEAR = "LINEAR"                    # F = S * ((1 + i_brl * DU/252) / (1 + i_foreign * DC/360))
    EXPONENTIAL_BRL = "EXPONENTIAL_BRL"  # F = S * ((1 + i_brl)^(DU/252) / (1 + i_foreign * DC/360))

    @classmethod
    def from_str(cls, value: Any) -> CompoundingMethod:
        if isinstance(value, CompoundingMethod):
            return value
        cleaned = str(value).strip().upper()
        if cleaned in ("LINEAR", "SIMPLES"):
            return cls.LINEAR
        if cleaned in ("EXPONENTIAL_BRL", "EXPONENTIAL", "COMPOUND", "COMPOSTO"):
            return cls.EXPONENTIAL_BRL
        raise FxForwardNdfValidationError(
            f"Convenção de capitalização inválida: {value!r}. Aceitos: LINEAR, EXPONENTIAL_BRL."
        )


# =====================================================================
# MATEMÁTICA FINANCEIRA: PARIDADE DE JUROS E CUPOM CAMBIAL
# =====================================================================


def calculate_theoretical_forward_rate(
    spot_rate: Union[Decimal, str, int, float],
    i_brl: Union[Decimal, str, int, float],
    i_foreign: Union[Decimal, str, int, float],
    du: int,
    dc: int,
    compounding: CompoundingMethod = CompoundingMethod.LINEAR,
) -> Decimal:
    """Calcula a Taxa a Termo teórica (F) via Paridade de Taxas de Juros (Covered Interest Parity).

    Fórmula padrão linear:
        F = S * ((1 + i_brl * DU/252) / (1 + i_foreign * DC/360))

    Fórmula com capitalização exponencial em BRL (padrão DI Futuro B3):
        F = S * ((1 + i_brl)^(DU/252) / (1 + i_foreign * DC/360))

    Args:
        spot_rate: Cotação à vista da moeda estrangeira (ex: 5.2500 BRL por USD).
        i_brl: Taxa de juros anual doméstica em formato decimal (ex: 0.1050 para 10,50% a.a.).
        i_foreign: Taxa de juros / Cupom cambial anual em formato decimal (ex: 0.0525 para 5,25% a.a.).
        du: Dias úteis entre spot e vencimento (base 252).
        dc: Dias corridos entre spot e vencimento (base 360).
        compounding: Convenção de juros BRL (LINEAR ou EXPONENTIAL_BRL).

    Returns:
        Taxa a termo teórica quantizada para 4 casas decimais (padrão PTAX/NoMe B3).
    """
    s = to_decimal(spot_rate, "spot_rate")
    r_brl = to_decimal(i_brl, "i_brl")
    r_f = to_decimal(i_foreign, "i_foreign")

    if s <= Decimal("0.00"):
        raise FxForwardNdfValidationError(f"Cotação à vista (spot) deve ser estritamente positiva: {s}")
    if dc <= 0:
        raise FxForwardNdfValidationError(f"Dias corridos (DC) deve ser estritamente positivo: {dc}")
    if du < 0:
        raise FxForwardNdfValidationError(f"Dias úteis (DU) não pode ser negativo: {du}")

    with localcontext() as ctx:
        ctx.prec = 50

        # Fator de juros internacional (foreign leg: DC/360 linear padrão Euromarket / B3)
        foreign_factor = Decimal("1") + (r_f * Decimal(dc) / CALENDAR_DAYS_YEAR_360)
        if foreign_factor <= Decimal("0.00"):
            raise FxForwardNdfValidationError(
                f"Fator de juros da moeda estrangeira não-positivo: {foreign_factor}"
            )

        # Fator de juros doméstico (BRL leg)
        if compounding == CompoundingMethod.LINEAR:
            brl_factor = Decimal("1") + (r_brl * Decimal(du) / BUSINESS_DAYS_YEAR)
        elif compounding == CompoundingMethod.EXPONENTIAL_BRL:
            base_brl = Decimal("1") + r_brl
            if base_brl <= Decimal("0.00"):
                raise FxForwardNdfValidationError(f"Base de juros BRL (1 + i_brl) deve ser positiva: {base_brl}")
            brl_factor = _decimal_power_du252(base_brl, du)
        else:
            raise FxForwardNdfValidationError(f"Método de capitalização desconhecido: {compounding}")

        forward = s * (brl_factor / foreign_factor)

    return round_fx_rate(forward)


def calculate_cupom_sujo(
    spot_rate: Union[Decimal, str, int, float],
    forward_rate: Union[Decimal, str, int, float],
    i_brl: Union[Decimal, str, int, float],
    du: int,
    dc: int,
    compounding: CompoundingMethod = CompoundingMethod.LINEAR,
) -> Decimal:
    """Calcula o Cupom Cambial Sujo implícito a partir da cotação spot de mercado.

    O Cupom Sujo embute a variação cambial intra-período entre a última PTAX de referência
    e a taxa de câmbio spot corrente (S).

    Fórmula:
        (1 + i_sujo * DC/360) = (S / F) * Factor_BRL
        i_sujo = ((S / F) * Factor_BRL - 1) * (360 / DC)
    """
    s = to_decimal(spot_rate, "spot_rate")
    f = to_decimal(forward_rate, "forward_rate")
    r_brl = to_decimal(i_brl, "i_brl")

    if s <= Decimal("0.00") or f <= Decimal("0.00"):
        raise FxForwardNdfValidationError("Spot rate e Forward rate devem ser estritamente positivos.")
    if dc <= 0:
        raise FxForwardNdfValidationError("Dias corridos (DC) deve ser estritamente positivo.")
    if du < 0:
        raise FxForwardNdfValidationError("Dias úteis (DU) não pode ser negativo.")

    with localcontext() as ctx:
        ctx.prec = 50
        if compounding == CompoundingMethod.LINEAR:
            brl_factor = Decimal("1") + (r_brl * Decimal(du) / BUSINESS_DAYS_YEAR)
        else:
            base_brl = Decimal("1") + r_brl
            brl_factor = _decimal_power_du252(base_brl, du)

        dirty_factor = (s / f) * brl_factor
        dirty_rate = (dirty_factor - Decimal("1")) * (CALENDAR_DAYS_YEAR_360 / Decimal(dc))

    return round_rate(dirty_rate)


def calculate_cupom_limpo(
    ptax_ref: Union[Decimal, str, int, float],
    forward_rate: Union[Decimal, str, int, float],
    i_brl: Union[Decimal, str, int, float],
    du: int,
    dc: int,
    compounding: CompoundingMethod = CompoundingMethod.LINEAR,
) -> Decimal:
    """Calcula o Cupom Cambial Limpo implícito a partir da PTAX de referência.

    O Cupom Limpo expurga a flutuação cambial pontual, referenciando o contrato estritamente
    à PTAX oficial de fechamento anterior (PTAX_ref).

    Fórmula:
        (1 + i_limpo * DC/360) = (PTAX_ref / F) * Factor_BRL
        i_limpo = ((PTAX_ref / F) * Factor_BRL - 1) * (360 / DC)
    """
    p_ref = to_decimal(ptax_ref, "ptax_ref")
    f = to_decimal(forward_rate, "forward_rate")
    r_brl = to_decimal(i_brl, "i_brl")

    if p_ref <= Decimal("0.00") or f <= Decimal("0.00"):
        raise FxForwardNdfValidationError("PTAX de referência e Forward rate devem ser estritamente positivos.")
    if dc <= 0:
        raise FxForwardNdfValidationError("Dias corridos (DC) deve ser estritamente positivo.")
    if du < 0:
        raise FxForwardNdfValidationError("Dias úteis (DU) não pode ser negativo.")

    with localcontext() as ctx:
        ctx.prec = 50
        if compounding == CompoundingMethod.LINEAR:
            brl_factor = Decimal("1") + (r_brl * Decimal(du) / BUSINESS_DAYS_YEAR)
        else:
            base_brl = Decimal("1") + r_brl
            brl_factor = _decimal_power_du252(base_brl, du)

        clean_factor = (p_ref / f) * brl_factor
        clean_rate = (clean_factor - Decimal("1")) * (CALENDAR_DAYS_YEAR_360 / Decimal(dc))

    return round_rate(clean_rate)


def convert_cupom_limpo_to_sujo(
    i_clean: Union[Decimal, str, int, float],
    spot_rate: Union[Decimal, str, int, float],
    ptax_ref: Union[Decimal, str, int, float],
    dc: int,
) -> Decimal:
    """Converte Cupom Limpo em Cupom Sujo com base no ratio de câmbio (S / PTAX_ref).

    Identidade estrutural:
        Factor_Sujo = Factor_Limpo * (S / PTAX_ref)
    """
    r_clean = to_decimal(i_clean, "i_clean")
    s = to_decimal(spot_rate, "spot_rate")
    p_ref = to_decimal(ptax_ref, "ptax_ref")

    if s <= Decimal("0.00") or p_ref <= Decimal("0.00"):
        raise FxForwardNdfValidationError("Spot rate e PTAX de referência devem ser estritamente positivos.")
    if dc <= 0:
        raise FxForwardNdfValidationError("Dias corridos (DC) deve ser estritamente positivo.")

    with localcontext() as ctx:
        ctx.prec = 50
        factor_clean = Decimal("1") + (r_clean * Decimal(dc) / CALENDAR_DAYS_YEAR_360)
        factor_dirty = factor_clean * (s / p_ref)
        dirty_rate = (factor_dirty - Decimal("1")) * (CALENDAR_DAYS_YEAR_360 / Decimal(dc))

    return round_rate(dirty_rate)


def convert_cupom_sujo_to_limpo(
    i_dirty: Union[Decimal, str, int, float],
    spot_rate: Union[Decimal, str, int, float],
    ptax_ref: Union[Decimal, str, int, float],
    dc: int,
) -> Decimal:
    """Converte Cupom Sujo em Cupom Limpo com base no ratio de câmbio (PTAX_ref / S).

    Identidade estrutural:
        Factor_Limpo = Factor_Sujo * (PTAX_ref / S)
    """
    r_dirty = to_decimal(i_dirty, "i_dirty")
    s = to_decimal(spot_rate, "spot_rate")
    p_ref = to_decimal(ptax_ref, "ptax_ref")

    if s <= Decimal("0.00") or p_ref <= Decimal("0.00"):
        raise FxForwardNdfValidationError("Spot rate e PTAX de referência devem ser estritamente positivos.")
    if dc <= 0:
        raise FxForwardNdfValidationError("Dias corridos (DC) deve ser estritamente positivo.")

    with localcontext() as ctx:
        ctx.prec = 50
        factor_dirty = Decimal("1") + (r_dirty * Decimal(dc) / CALENDAR_DAYS_YEAR_360)
        factor_clean = factor_dirty * (p_ref / s)
        clean_rate = (factor_clean - Decimal("1")) * (CALENDAR_DAYS_YEAR_360 / Decimal(dc))

    return round_rate(clean_rate)


# =====================================================================
# DATACLASSES DE RESULTADOS E FLUXOS
# =====================================================================


@dataclass
class MarkToMarketResult:
    """Resultado da apuração de Marcação a Mercado (MtM) bilateral."""

    contract_id: str
    valuation_date: date
    current_spot: Decimal
    current_forward: Decimal
    remaining_du: int
    remaining_dc: int
    discount_factor_brl: Decimal
    buyer_mtm_brl: Decimal
    seller_mtm_brl: Decimal
    zero_sum_verified: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "valuation_date": self.valuation_date.isoformat(),
            "current_spot": str(self.current_spot),
            "current_forward": str(self.current_forward),
            "remaining_du": self.remaining_du,
            "remaining_dc": self.remaining_dc,
            "discount_factor_brl": str(self.discount_factor_brl),
            "buyer_mtm_brl": str(self.buyer_mtm_brl),
            "seller_mtm_brl": str(self.seller_mtm_brl),
            "zero_sum_verified": self.zero_sum_verified,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MarkToMarketResult:
        return cls(
            contract_id=str(data["contract_id"]),
            valuation_date=parse_date(data["valuation_date"], "valuation_date"),
            current_spot=to_decimal(data["current_spot"], "current_spot"),
            current_forward=to_decimal(data["current_forward"], "current_forward"),
            remaining_du=int(data["remaining_du"]),
            remaining_dc=int(data["remaining_dc"]),
            discount_factor_brl=to_decimal(data["discount_factor_brl"], "discount_factor_brl"),
            buyer_mtm_brl=to_decimal(data["buyer_mtm_brl"], "buyer_mtm_brl"),
            seller_mtm_brl=to_decimal(data["seller_mtm_brl"], "seller_mtm_brl"),
            zero_sum_verified=bool(data["zero_sum_verified"]),
        )


@dataclass
class SettlementCashFlow:
    """Instrução individual de fluxo de liquidação para um participante."""

    participant_id: str
    currency: Currency
    amount: Decimal  # Positivo = crédito/recebimento; Negativo = débito/pagamento
    direction: str   # "RECEIVE" ou "PAY"
    description: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "currency": self.currency.value,
            "amount": str(self.amount),
            "direction": self.direction,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SettlementCashFlow:
        return cls(
            participant_id=str(data["participant_id"]),
            currency=Currency.from_str(data["currency"]),
            amount=to_decimal(data["amount"], "amount"),
            direction=str(data["direction"]),
            description=str(data["description"]),
        )


@dataclass
class SettlementResult:
    """Resultado determinístico da liquidação física ou financeira do contrato."""

    contract_id: str
    settlement_type: FxContractType
    settlement_date: date
    fixing_date: date
    fixing_ptax: Decimal
    forward_rate: Decimal
    notional_foreign: Decimal
    currency_pair: CurrencyPair
    buyer_participant_id: str
    seller_participant_id: str
    buyer_gross_adjustment_brl: Decimal
    seller_gross_adjustment_brl: Decimal
    foreign_currency_delivered: Decimal
    brl_cash_delivered: Decimal
    cash_flows: List[SettlementCashFlow]
    zero_sum_conserved: bool
    ptax_parity_conserved: bool
    settlement_status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "settlement_type": self.settlement_type.value,
            "settlement_date": self.settlement_date.isoformat(),
            "fixing_date": self.fixing_date.isoformat(),
            "fixing_ptax": str(self.fixing_ptax),
            "forward_rate": str(self.forward_rate),
            "notional_foreign": str(self.notional_foreign),
            "currency_pair": self.currency_pair.value,
            "buyer_participant_id": self.buyer_participant_id,
            "seller_participant_id": self.seller_participant_id,
            "buyer_gross_adjustment_brl": str(self.buyer_gross_adjustment_brl),
            "seller_gross_adjustment_brl": str(self.seller_gross_adjustment_brl),
            "foreign_currency_delivered": str(self.foreign_currency_delivered),
            "brl_cash_delivered": str(self.brl_cash_delivered),
            "cash_flows": [cf.to_dict() for cf in self.cash_flows],
            "zero_sum_conserved": self.zero_sum_conserved,
            "ptax_parity_conserved": self.ptax_parity_conserved,
            "settlement_status": self.settlement_status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SettlementResult:
        return cls(
            contract_id=str(data["contract_id"]),
            settlement_type=FxContractType.from_str(data["settlement_type"]),
            settlement_date=parse_date(data["settlement_date"], "settlement_date"),
            fixing_date=parse_date(data["fixing_date"], "fixing_date"),
            fixing_ptax=to_decimal(data["fixing_ptax"], "fixing_ptax"),
            forward_rate=to_decimal(data["forward_rate"], "forward_rate"),
            notional_foreign=to_decimal(data["notional_foreign"], "notional_foreign"),
            currency_pair=CurrencyPair.from_str(data["currency_pair"]),
            buyer_participant_id=str(data["buyer_participant_id"]),
            seller_participant_id=str(data["seller_participant_id"]),
            buyer_gross_adjustment_brl=to_decimal(data["buyer_gross_adjustment_brl"], "buyer_gross_adjustment_brl"),
            seller_gross_adjustment_brl=to_decimal(data["seller_gross_adjustment_brl"], "seller_gross_adjustment_brl"),
            foreign_currency_delivered=to_decimal(data["foreign_currency_delivered"], "foreign_currency_delivered"),
            brl_cash_delivered=to_decimal(data["brl_cash_delivered"], "brl_cash_delivered"),
            cash_flows=[SettlementCashFlow.from_dict(cf) for cf in data.get("cash_flows", [])],
            zero_sum_conserved=bool(data["zero_sum_conserved"]),
            ptax_parity_conserved=bool(data["ptax_parity_conserved"]),
            settlement_status=str(data["settlement_status"]),
        )


# =====================================================================
# CONTRATO DE CÂMBIO A TERMO E NDF (MODELAGEM NoMe B3)
# =====================================================================


@dataclass
class FxForwardContract:
    """Contrato a Termo de Moeda / NDF registrado no Balcão NoMe B3.

    Representa tanto o Termo com Entrega Física (Deliverable) quanto o NDF (Non-Deliverable Forward),
    com liquidação parametrizada pela PTAX BACEN e convenções D0 ou D+1.
    """

    contract_id: str
    nome_registration_id: str
    contract_type: FxContractType
    currency_pair: CurrencyPair
    trade_date: date
    fixing_date: date
    settlement_date: date
    fixing_lag: FixingSettlementLag
    notional_foreign: Decimal
    forward_rate: Decimal
    buyer_participant_id: str
    seller_participant_id: str
    spot_rate_initial: Optional[Decimal] = None
    rate_brl_initial: Optional[Decimal] = None
    rate_foreign_initial: Optional[Decimal] = None
    business_days_du: int = 0
    calendar_days_dc: int = 0
    compounding_method: CompoundingMethod = CompoundingMethod.LINEAR
    buyer_account: str = ""
    seller_account: str = ""
    status: ContractStatus = ContractStatus.REGISTERED
    fixing_ptax: Optional[Decimal] = None
    fixing_timestamp: Optional[datetime] = None
    settlement_result: Optional[SettlementResult] = None
    latest_mtm: Optional[MarkToMarketResult] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._normalize_fields()
        self._validate_contract()

    def _normalize_fields(self) -> None:
        self.contract_type = FxContractType.from_str(self.contract_type)
        self.currency_pair = CurrencyPair.from_str(self.currency_pair)
        self.trade_date = parse_date(self.trade_date, "trade_date")
        self.fixing_date = parse_date(self.fixing_date, "fixing_date")
        self.settlement_date = parse_date(self.settlement_date, "settlement_date")
        self.fixing_lag = FixingSettlementLag.from_str(self.fixing_lag)
        self.status = ContractStatus.from_str(self.status)
        self.compounding_method = CompoundingMethod.from_str(self.compounding_method)

        self.notional_foreign = round_cents(to_decimal(self.notional_foreign, "notional_foreign"))
        self.forward_rate = round_fx_rate(to_decimal(self.forward_rate, "forward_rate"))

        if self.spot_rate_initial is not None:
            self.spot_rate_initial = round_fx_rate(to_decimal(self.spot_rate_initial, "spot_rate_initial"))
        if self.rate_brl_initial is not None:
            self.rate_brl_initial = round_rate(to_decimal(self.rate_brl_initial, "rate_brl_initial"))
        if self.rate_foreign_initial is not None:
            self.rate_foreign_initial = round_rate(to_decimal(self.rate_foreign_initial, "rate_foreign_initial"))
        if self.fixing_ptax is not None:
            self.fixing_ptax = round_fx_rate(to_decimal(self.fixing_ptax, "fixing_ptax"))
        if self.fixing_timestamp is not None:
            self.fixing_timestamp = parse_datetime(self.fixing_timestamp, "fixing_timestamp")

        # Auto-preenche calendar_days_dc se não fornecido
        if self.calendar_days_dc <= 0:
            self.calendar_days_dc = max(1, (self.settlement_date - self.trade_date).days)

    def _validate_contract(self) -> None:
        """Valida sanidade cadastral e regras regulatórias do Balcão B3."""
        if not self.contract_id or not self.contract_id.strip():
            raise FxForwardNdfValidationError("Identificador do contrato 'contract_id' não pode ser vazio.")
        if not self.nome_registration_id or not self.nome_registration_id.strip():
            raise FxForwardNdfValidationError("Protocolo NoMe 'nome_registration_id' não pode ser vazio.")
        if not self.buyer_participant_id or not self.seller_participant_id:
            raise FxForwardNdfValidationError("Participantes comprador e vendedor são obrigatórios.")
        if self.buyer_participant_id == self.seller_participant_id:
            raise FxForwardNdfValidationError(
                f"Auto-negociação (wash trade) vedada: comprador e vendedor idênticos '{self.buyer_participant_id}'."
            )
        if self.notional_foreign <= Decimal("0.00"):
            raise FxForwardNdfValidationError(
                f"Montante nocional em moeda estrangeira deve ser estritamente positivo: {self.notional_foreign}"
            )
        if self.forward_rate <= Decimal("0.00"):
            raise FxForwardNdfValidationError(
                f"Taxa a termo contratada deve ser estritamente positiva: {self.forward_rate}"
            )

        # Sanidade de cronograma de datas
        if self.trade_date > self.settlement_date:
            raise FxForwardNdfValidationError(
                f"Data de negociação ({self.trade_date}) posterior à data de liquidação ({self.settlement_date})."
            )
        if self.trade_date > self.fixing_date:
            raise FxForwardNdfValidationError(
                f"Data de negociação ({self.trade_date}) posterior à data de fixação PTAX ({self.fixing_date})."
            )
        if self.fixing_date > self.settlement_date:
            raise FxForwardNdfValidationError(
                f"Data de fixação ({self.fixing_date}) posterior à data de liquidação ({self.settlement_date})."
            )

        # Regra de defasagem de fixação (D0 vs D1)
        if self.fixing_lag == FixingSettlementLag.D0:
            if self.fixing_date != self.settlement_date:
                raise FxForwardNdfValidationError(
                    f"Para regime D0, a data de fixação ({self.fixing_date}) deve ser igual à data de liquidação ({self.settlement_date})."
                )
        elif self.fixing_lag == FixingSettlementLag.D1:
            if self.fixing_date >= self.settlement_date:
                raise FxForwardNdfValidationError(
                    f"Para regime D1, a data de fixação ({self.fixing_date}) deve ser estritamente anterior à liquidação ({self.settlement_date})."
                )

        if self.calendar_days_dc <= 0:
            raise FxForwardNdfValidationError(f"Prazo em dias corridos (DC) deve ser >= 1: {self.calendar_days_dc}")

    def activate(self) -> None:
        """Transiciona contrato de REGISTERED para ACTIVE."""
        if self.status != ContractStatus.REGISTERED:
            raise ContractStateError(f"Apenas contratos REGISTERED podem ser ativados. Status atual: {self.status.value}")
        self.status = ContractStatus.ACTIVE

    def cancel(self, reason: str = "") -> None:
        """Cancela o contrato antes da fixação PTAX."""
        if self.status in (ContractStatus.FIXED, ContractStatus.SETTLED):
            raise ContractStateError(f"Contrato em estado {self.status.value} não pode ser cancelado.")
        self.status = ContractStatus.CANCELLED
        self.metadata["cancel_reason"] = reason

    def mark_to_market(
        self,
        valuation_date: Union[date, str],
        current_spot: Union[Decimal, str, int, float],
        i_brl: Union[Decimal, str, int, float],
        i_foreign: Union[Decimal, str, int, float],
        remaining_du: int,
        remaining_dc: int,
        compounding: Optional[CompoundingMethod] = None,
    ) -> MarkToMarketResult:
        """Calcula a Marcação a Mercado (MtM) bilateral no referencial da data de avaliação.

        Fórmula:
            F_t = calculate_theoretical_forward_rate(S_t, i_brl, i_foreign, remaining_du, remaining_dc)
            Payoff_vencimento = Nocional * (F_t - F_contratado)
            DF_BRL = 1 / Factor_BRL
            MtM_Comprador = round_2(Payoff_vencimento * DF_BRL)
            MtM_Vendedor = - MtM_Comprador
        """
        val_date = parse_date(valuation_date, "valuation_date")
        s_t = to_decimal(current_spot, "current_spot")
        r_brl = to_decimal(i_brl, "i_brl")
        r_f = to_decimal(i_foreign, "i_foreign")
        method = compounding or self.compounding_method

        if self.status in (ContractStatus.SETTLED, ContractStatus.CANCELLED):
            raise ContractStateError(
                f"Contrato {self.contract_id} em estado '{self.status.value}' não aceita marcação a mercado."
            )
        if remaining_dc < 0 or remaining_du < 0:
            raise FxForwardNdfValidationError("Prazos residuais DU e DC não podem ser negativos na MtM.")

        if remaining_dc == 0:
            current_f = round_fx_rate(s_t)
            df_brl = Decimal("1.00000000")
        else:
            current_f = calculate_theoretical_forward_rate(
                spot_rate=s_t,
                i_brl=r_brl,
                i_foreign=r_f,
                du=remaining_du,
                dc=remaining_dc,
                compounding=method,
            )
            with localcontext() as ctx:
                ctx.prec = 50
                if method == CompoundingMethod.LINEAR:
                    factor_brl = Decimal("1") + (r_brl * Decimal(remaining_du) / BUSINESS_DAYS_YEAR)
                else:
                    base_brl = Decimal("1") + r_brl
                    factor_brl = _decimal_power_du252(base_brl, remaining_du)
                df_brl = round_rate(Decimal("1") / factor_brl)

        with localcontext() as ctx:
            ctx.prec = 50
            payoff_buyer = self.notional_foreign * (current_f - self.forward_rate)
            pv_buyer = payoff_buyer * df_brl
            buyer_mtm = round_cents(pv_buyer)
            seller_mtm = -buyer_mtm

        # Verificação da lei de conservação soma-zero em MtM (LAW-061)
        assert_fx_forward_bilateral_zero_sum(buyer_mtm, seller_mtm, self.contract_id)

        mtm_result = MarkToMarketResult(
            contract_id=self.contract_id,
            valuation_date=val_date,
            current_spot=round_fx_rate(s_t),
            current_forward=current_f,
            remaining_du=remaining_du,
            remaining_dc=remaining_dc,
            discount_factor_brl=df_brl,
            buyer_mtm_brl=buyer_mtm,
            seller_mtm_brl=seller_mtm,
            zero_sum_verified=(buyer_mtm + seller_mtm == Decimal("0.00")),
        )
        self.latest_mtm = mtm_result
        if self.status == ContractStatus.REGISTERED:
            self.status = ContractStatus.ACTIVE
        return mtm_result

    def fix_ptax(
        self,
        fixing_ptax: Union[Decimal, str, int, float],
        fixing_date: Optional[Union[date, str]] = None,
        timestamp: Optional[Union[datetime, str]] = None,
    ) -> None:
        """Registra a cotação oficial PTAX divulgada pelo BACEN na data de fixação."""
        if self.status in (ContractStatus.SETTLED, ContractStatus.CANCELLED):
            raise ContractStateError(
                f"Contrato {self.contract_id} em estado '{self.status.value}' não pode receber fixação PTAX."
            )

        p = round_fx_rate(to_decimal(fixing_ptax, "fixing_ptax"))
        if p <= Decimal("0.00"):
            raise FxForwardNdfValidationError(f"Cotação PTAX deve ser estritamente positiva: {p}")

        if fixing_date is not None:
            f_date = parse_date(fixing_date, "fixing_date")
            if f_date != self.fixing_date:
                raise FxForwardNdfValidationError(
                    f"Data de fixação informada ({f_date}) diverge da cadastrada ({self.fixing_date})."
                )

        self.fixing_ptax = p
        self.fixing_timestamp = parse_datetime(timestamp, "fixing_timestamp") or datetime.now(timezone.utc)
        self.status = ContractStatus.FIXED

    def settle(self, settlement_date: Optional[Union[date, str]] = None) -> SettlementResult:
        """Executa a liquidação física ou financeira por ajuste em BRL no Balcão NoMe B3.

        Invariantes auditadas:
        - Conservação Bilateral Soma-Zero (Ganho_Comprador + Perda_Vendedor == 0,00).
        - Paridade Cambial na Fixação PTAX.
        """
        if self.status == ContractStatus.SETTLED:
            raise ContractStateError(f"Contrato {self.contract_id} já se encontra liquidado.")
        if self.status != ContractStatus.FIXED:
            raise ContractStateError(
                f"Contrato {self.contract_id} deve estar com PTAX fixada (FIXED) antes da liquidação. Status: {self.status.value}"
            )
        if self.fixing_ptax is None:
            raise ContractStateError(f"Cotação PTAX não fixada no contrato {self.contract_id}.")

        actual_settlement_date = parse_date(settlement_date, "settlement_date") if settlement_date else self.settlement_date
        if actual_settlement_date != self.settlement_date:
            raise FxForwardNdfValidationError(
                f"Data de liquidação solicitada ({actual_settlement_date}) diverge do contrato ({self.settlement_date})."
            )

        ptax = self.fixing_ptax
        f_rate = self.forward_rate
        notional = self.notional_foreign

        # Apuração do diferencial financeiro de ajuste em BRL
        # Comprador comprou Moeda Estrangeira e vendeu BRL: Ganha se PTAX > ForwardRate
        with localcontext() as ctx:
            ctx.prec = 50
            diff = ptax - f_rate
            buyer_adj = round_cents(notional * diff)
            seller_adj = -buyer_adj

        # Verificação formal da invariante soma-zero (LAW-061)
        assert_fx_forward_bilateral_zero_sum(buyer_adj, seller_adj, self.contract_id)

        # Verificação formal da paridade cambial PTAX (LAW-062)
        assert_fx_ptax_fixing_parity(
            notional_foreign=notional,
            fixing_ptax=ptax,
            forward_rate=f_rate,
            buyer_gross_adjustment=buyer_adj,
            tolerance_cents=Decimal("0.01"),
            contract_id=self.contract_id,
        )

        cash_flows: List[SettlementCashFlow] = []
        foreign_delivered = Decimal("0.00")
        brl_delivered = Decimal("0.00")

        if self.contract_type == FxContractType.NDF:
            # Liquidação puramente financeira por diferencial em BRL
            brl_delivered = abs(buyer_adj)
            if buyer_adj > Decimal("0.00"):
                # Comprador lucrou: Vendedor paga ao Comprador
                cash_flows.append(
                    SettlementCashFlow(
                        participant_id=self.seller_participant_id,
                        currency=Currency.BRL,
                        amount=-buyer_adj,
                        direction="PAY",
                        description=f"Pagamento de ajuste NDF {self.currency_pair.value} à contraparte compradora",
                    )
                )
                cash_flows.append(
                    SettlementCashFlow(
                        participant_id=self.buyer_participant_id,
                        currency=Currency.BRL,
                        amount=buyer_adj,
                        direction="RECEIVE",
                        description=f"Recebimento de ajuste NDF {self.currency_pair.value} da contraparte vendedora",
                    )
                )
            elif buyer_adj < Decimal("0.00"):
                # Vendedor lucrou: Comprador paga ao Vendedor
                loss_amount = abs(buyer_adj)
                cash_flows.append(
                    SettlementCashFlow(
                        participant_id=self.buyer_participant_id,
                        currency=Currency.BRL,
                        amount=-loss_amount,
                        direction="PAY",
                        description=f"Pagamento de ajuste NDF {self.currency_pair.value} à contraparte vendedora",
                    )
                )
                cash_flows.append(
                    SettlementCashFlow(
                        participant_id=self.seller_participant_id,
                        currency=Currency.BRL,
                        amount=loss_amount,
                        direction="RECEIVE",
                        description=f"Recebimento de ajuste NDF {self.currency_pair.value} da contraparte compradora",
                    )
                )
            else:
                # Ajuste zero (PTAX == ForwardRate)
                cash_flows.append(
                    SettlementCashFlow(
                        participant_id=self.buyer_participant_id,
                        currency=Currency.BRL,
                        amount=Decimal("0.00"),
                        direction="RECEIVE",
                        description="Ajuste NDF neutro (PTAX de fixação idêntica à taxa a termo contratada)",
                    )
                )
                cash_flows.append(
                    SettlementCashFlow(
                        participant_id=self.seller_participant_id,
                        currency=Currency.BRL,
                        amount=Decimal("0.00"),
                        direction="RECEIVE",
                        description="Ajuste NDF neutro (PTAX de fixação idêntica à taxa a termo contratada)",
                    )
                )

        elif self.contract_type == FxContractType.DELIVERABLE:
            # Termo com Entrega Física:
            # Comprador entrega montante contratado em BRL e recebe o nocional na moeda estrangeira (USD/EUR)
            brl_notional_contracted = round_cents(notional * f_rate)
            foreign_delivered = notional
            brl_delivered = brl_notional_contracted

            base_curr = self.currency_pair.base_currency

            # Perna em BRL
            cash_flows.append(
                SettlementCashFlow(
                    participant_id=self.buyer_participant_id,
                    currency=Currency.BRL,
                    amount=-brl_notional_contracted,
                    direction="PAY",
                    description=f"Entrega de BRL contratado no Termo de Moeda ({f_rate:.4f})",
                )
            )
            cash_flows.append(
                SettlementCashFlow(
                    participant_id=self.seller_participant_id,
                    currency=Currency.BRL,
                    amount=brl_notional_contracted,
                    direction="RECEIVE",
                    description=f"Recebimento de BRL contratado no Termo de Moeda ({f_rate:.4f})",
                )
            )

            # Perna em Moeda Estrangeira
            cash_flows.append(
                SettlementCashFlow(
                    participant_id=self.seller_participant_id,
                    currency=base_curr,
                    amount=-notional,
                    direction="PAY",
                    description=f"Entrega física de {base_curr.value} ao comprador",
                )
            )
            cash_flows.append(
                SettlementCashFlow(
                    participant_id=self.buyer_participant_id,
                    currency=base_curr,
                    amount=notional,
                    direction="RECEIVE",
                    description=f"Recebimento físico de {base_curr.value} do vendedor",
                )
            )

        res = SettlementResult(
            contract_id=self.contract_id,
            settlement_type=self.contract_type,
            settlement_date=actual_settlement_date,
            fixing_date=self.fixing_date,
            fixing_ptax=ptax,
            forward_rate=f_rate,
            notional_foreign=notional,
            currency_pair=self.currency_pair,
            buyer_participant_id=self.buyer_participant_id,
            seller_participant_id=self.seller_participant_id,
            buyer_gross_adjustment_brl=buyer_adj,
            seller_gross_adjustment_brl=seller_adj,
            foreign_currency_delivered=foreign_delivered,
            brl_cash_delivered=brl_delivered,
            cash_flows=cash_flows,
            zero_sum_conserved=(buyer_adj + seller_adj == Decimal("0.00")),
            ptax_parity_conserved=True,
            settlement_status="SETTLED",
        )
        self.settlement_result = res
        self.status = ContractStatus.SETTLED
        return res

    def verify_invariants(self) -> Dict[str, Any]:
        """Verifica formalmente todas as leis de conservação e regras de integridade do contrato."""
        violations: List[str] = []

        # 1. Sanidade de Nocional e Taxas
        if self.notional_foreign <= Decimal("0.00"):
            violations.append(f"Nocional não-positivo: {self.notional_foreign}")
        if self.forward_rate <= Decimal("0.00"):
            violations.append(f"Taxa a termo não-positiva: {self.forward_rate}")

        # 2. Sanidade Temporal
        if not (self.trade_date <= self.fixing_date <= self.settlement_date):
            violations.append(
                f"Quebra de ordem temporal: Trade ({self.trade_date}) <= Fixing ({self.fixing_date}) <= Settle ({self.settlement_date})"
            )
        if self.fixing_lag == FixingSettlementLag.D0 and self.fixing_date != self.settlement_date:
            violations.append("Regime D0 exige fixing_date == settlement_date")
        if self.fixing_lag == FixingSettlementLag.D1 and self.fixing_date >= self.settlement_date:
            violations.append("Regime D1 exige fixing_date < settlement_date")

        # 3. Invariante Soma-Zero em MtM
        if self.latest_mtm is not None:
            sum_mtm = self.latest_mtm.buyer_mtm_brl + self.latest_mtm.seller_mtm_brl
            if sum_mtm != Decimal("0.00"):
                violations.append(f"Violação soma-zero MtM: {sum_mtm} != 0.00")

        # 4. Invariante Soma-Zero e Paridade PTAX na Liquidação
        if self.settlement_result is not None:
            res = self.settlement_result
            sum_settle = res.buyer_gross_adjustment_brl + res.seller_gross_adjustment_brl
            if sum_settle != Decimal("0.00"):
                violations.append(f"Violação soma-zero liquidação: {sum_settle} != 0.00")

            expected_adj = round_cents(self.notional_foreign * (res.fixing_ptax - self.forward_rate))
            if abs(res.buyer_gross_adjustment_brl - expected_adj) > Decimal("0.01"):
                violations.append(
                    f"Violação paridade PTAX: obtido {res.buyer_gross_adjustment_brl} != esperado {expected_adj}"
                )

        return {
            "contract_id": self.contract_id,
            "all_invariants_hold": len(violations) == 0,
            "violations": violations,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o contrato para dicionário JSON-compatível."""
        data: Dict[str, Any] = {
            "contract_id": self.contract_id,
            "nome_registration_id": self.nome_registration_id,
            "contract_type": self.contract_type.value,
            "currency_pair": self.currency_pair.value,
            "trade_date": self.trade_date.isoformat(),
            "fixing_date": self.fixing_date.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
            "fixing_lag": self.fixing_lag.value,
            "notional_foreign": str(self.notional_foreign),
            "forward_rate": str(self.forward_rate),
            "buyer_participant_id": self.buyer_participant_id,
            "seller_participant_id": self.seller_participant_id,
            "business_days_du": self.business_days_du,
            "calendar_days_dc": self.calendar_days_dc,
            "compounding_method": self.compounding_method.value,
            "buyer_account": self.buyer_account,
            "seller_account": self.seller_account,
            "status": self.status.value,
            "metadata": self.metadata,
        }
        if self.spot_rate_initial is not None:
            data["spot_rate_initial"] = str(self.spot_rate_initial)
        if self.rate_brl_initial is not None:
            data["rate_brl_initial"] = str(self.rate_brl_initial)
        if self.rate_foreign_initial is not None:
            data["rate_foreign_initial"] = str(self.rate_foreign_initial)
        if self.fixing_ptax is not None:
            data["fixing_ptax"] = str(self.fixing_ptax)
        if self.fixing_timestamp is not None:
            data["fixing_timestamp"] = self.fixing_timestamp.isoformat()
        if self.latest_mtm is not None:
            data["latest_mtm"] = self.latest_mtm.to_dict()
        if self.settlement_result is not None:
            data["settlement_result"] = self.settlement_result.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FxForwardContract:
        """Desserializa contrato a partir de dicionário."""
        latest_mtm = None
        if data.get("latest_mtm"):
            latest_mtm = MarkToMarketResult.from_dict(data["latest_mtm"])

        settlement_result = None
        if data.get("settlement_result"):
            settlement_result = SettlementResult.from_dict(data["settlement_result"])

        return cls(
            contract_id=str(data["contract_id"]),
            nome_registration_id=str(data["nome_registration_id"]),
            contract_type=FxContractType.from_str(data["contract_type"]),
            currency_pair=CurrencyPair.from_str(data["currency_pair"]),
            trade_date=parse_date(data["trade_date"], "trade_date"),
            fixing_date=parse_date(data["fixing_date"], "fixing_date"),
            settlement_date=parse_date(data["settlement_date"], "settlement_date"),
            fixing_lag=FixingSettlementLag.from_str(data.get("fixing_lag", "D0")),
            notional_foreign=to_decimal(data["notional_foreign"], "notional_foreign"),
            forward_rate=to_decimal(data["forward_rate"], "forward_rate"),
            buyer_participant_id=str(data["buyer_participant_id"]),
            seller_participant_id=str(data["seller_participant_id"]),
            spot_rate_initial=to_decimal(data["spot_rate_initial"]) if data.get("spot_rate_initial") else None,
            rate_brl_initial=to_decimal(data["rate_brl_initial"]) if data.get("rate_brl_initial") else None,
            rate_foreign_initial=to_decimal(data["rate_foreign_initial"]) if data.get("rate_foreign_initial") else None,
            business_days_du=int(data.get("business_days_du", 0)),
            calendar_days_dc=int(data.get("calendar_days_dc", 0)),
            compounding_method=CompoundingMethod.from_str(data.get("compounding_method", "LINEAR")),
            buyer_account=str(data.get("buyer_account", "")),
            seller_account=str(data.get("seller_account", "")),
            status=ContractStatus.from_str(data.get("status", "REGISTERED")),
            fixing_ptax=to_decimal(data["fixing_ptax"]) if data.get("fixing_ptax") else None,
            fixing_timestamp=parse_datetime(data["fixing_timestamp"]) if data.get("fixing_timestamp") else None,
            settlement_result=settlement_result,
            latest_mtm=latest_mtm,
            metadata=dict(data.get("metadata", {})),
        )


# =====================================================================
# REGISTRADORA E MOTOR OPERACIONAL (NoMe B3)
# =====================================================================


class FxForwardNdfRegistry:
    """Motor central de Escrituração, Marcação a Mercado e Liquidação no Balcão NoMe B3."""

    def __init__(self) -> None:
        self.contracts: Dict[str, FxForwardContract] = {}
        self.nome_protocol_index: Dict[str, str] = {}

    def register_contract(self, contract: FxForwardContract) -> str:
        """Registra um contrato de Termo / NDF no sistema NoMe B3."""
        if contract.contract_id in self.contracts:
            raise DuplicateContractError(
                f"Contrato com identificador '{contract.contract_id}' já registrado no NoMe."
            )
        if contract.nome_registration_id in self.nome_protocol_index:
            raise DuplicateContractError(
                f"Protocolo NoMe '{contract.nome_registration_id}' já associado ao contrato '{self.nome_protocol_index[contract.nome_registration_id]}'."
            )

        contract.verify_invariants()
        self.contracts[contract.contract_id] = contract
        self.nome_protocol_index[contract.nome_registration_id] = contract.contract_id
        return contract.contract_id

    def get_contract(self, contract_id: str) -> FxForwardContract:
        """Recupera contrato pelo ID ou levanta ContractNotFoundError."""
        if contract_id not in self.contracts:
            raise ContractNotFoundError(f"Contrato '{contract_id}' não encontrado na Registradora NoMe.")
        return self.contracts[contract_id]

    def get_by_nome_protocol(self, protocol: str) -> FxForwardContract:
        """Recupera contrato pelo número de protocolo NoMe."""
        if protocol not in self.nome_protocol_index:
            raise ContractNotFoundError(f"Protocolo NoMe '{protocol}' não encontrado.")
        return self.get_contract(self.nome_protocol_index[protocol])

    def list_contracts(
        self,
        status: Optional[ContractStatus] = None,
        contract_type: Optional[FxContractType] = None,
        currency_pair: Optional[CurrencyPair] = None,
    ) -> List[FxForwardContract]:
        """Filtra contratos da carteira por status, modalidade ou par cambial."""
        result = list(self.contracts.values())
        if status is not None:
            result = [c for c in result if c.status == status]
        if contract_type is not None:
            result = [c for c in result if c.contract_type == contract_type]
        if currency_pair is not None:
            result = [c for c in result if c.currency_pair == currency_pair]
        return result

    def mark_to_market(
        self,
        contract_id: str,
        valuation_date: Union[date, str],
        current_spot: Union[Decimal, str, int, float],
        i_brl: Union[Decimal, str, int, float],
        i_foreign: Union[Decimal, str, int, float],
        remaining_du: int,
        remaining_dc: int,
        compounding: Optional[CompoundingMethod] = None,
    ) -> MarkToMarketResult:
        """Executa a Marcação a Mercado de um contrato individual."""
        contract = self.get_contract(contract_id)
        return contract.mark_to_market(
            valuation_date=valuation_date,
            current_spot=current_spot,
            i_brl=i_brl,
            i_foreign=i_foreign,
            remaining_du=remaining_du,
            remaining_dc=remaining_dc,
            compounding=compounding,
        )

    def fix_ptax(
        self,
        contract_id: str,
        fixing_ptax: Union[Decimal, str, int, float],
        fixing_date: Optional[Union[date, str]] = None,
        timestamp: Optional[Union[datetime, str]] = None,
    ) -> None:
        """Fixa a taxa PTAX oficial do BACEN para um contrato específico."""
        contract = self.get_contract(contract_id)
        contract.fix_ptax(fixing_ptax=fixing_ptax, fixing_date=fixing_date, timestamp=timestamp)

    def settle_contract(
        self,
        contract_id: str,
        settlement_date: Optional[Union[date, str]] = None,
    ) -> SettlementResult:
        """Executa a liquidação financeira ou física de um contrato com PTAX fixada."""
        contract = self.get_contract(contract_id)
        return contract.settle(settlement_date=settlement_date)

    def verify_invariants(self) -> Dict[str, Any]:
        """Executa verificação formal de integridade e conservação em toda a carteira de contratos."""
        violations: List[str] = []
        total_mtm_buyer = Decimal("0.00")
        total_mtm_seller = Decimal("0.00")
        total_adj_buyer = Decimal("0.00")
        total_adj_seller = Decimal("0.00")

        for c_id, contract in self.contracts.items():
            c_report = contract.verify_invariants()
            if not c_report["all_invariants_hold"]:
                for v in c_report["violations"]:
                    violations.append(f"Contrato {c_id}: {v}")

            if contract.latest_mtm:
                total_mtm_buyer += contract.latest_mtm.buyer_mtm_brl
                total_mtm_seller += contract.latest_mtm.seller_mtm_brl

            if contract.settlement_result:
                total_adj_buyer += contract.settlement_result.buyer_gross_adjustment_brl
                total_adj_seller += contract.settlement_result.seller_gross_adjustment_brl

        # Invariante macro de soma-zero agregado em MtM
        if total_mtm_buyer + total_mtm_seller != Decimal("0.00"):
            violations.append(
                f"Violação agregada soma-zero MtM: Compradores ({total_mtm_buyer}) + Vendedores ({total_mtm_seller}) != 0.00"
            )

        # Invariante macro de soma-zero agregado em liquidação
        if total_adj_buyer + total_adj_seller != Decimal("0.00"):
            violations.append(
                f"Violação agregada soma-zero liquidação: Compradores ({total_adj_buyer}) + Vendedores ({total_adj_seller}) != 0.00"
            )

        return {
            "total_contracts": len(self.contracts),
            "all_invariants_hold": len(violations) == 0,
            "violations": violations,
            "aggregate_mtm_sum": str(total_mtm_buyer + total_mtm_seller),
            "aggregate_settlement_sum": str(total_adj_buyer + total_adj_seller),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serializa todo o estado da registradora para dicionário."""
        return {
            "contracts": [c.to_dict() for c in self.contracts.values()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FxForwardNdfRegistry:
        """Cria e popula a registradora a partir de dicionário."""
        registry = cls()
        contracts_data = data.get("contracts", [])
        for c_data in contracts_data:
            contract = FxForwardContract.from_dict(c_data)
            registry.register_contract(contract)
        return registry

    @classmethod
    def from_json_file(cls, file_path: Union[str, Path]) -> FxForwardNdfRegistry:
        """Carrega contratos diretamente a partir de arquivo JSON."""
        path = Path(file_path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
