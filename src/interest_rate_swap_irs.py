"""Derivativos de Balcão B3 (NoMe) — Registro e Liquidação Periódica de Swaps de Taxa de Juros (IRS - FL-080).

Implementa o motor determinístico da Registradora B3 (NoMe / Balcão B3) para Contratos
de Swap de Taxa de Juros com Ajustes Periódicos (Interest Rate Swap - IRS), contemplando:
- Estruturas Pré x DI, IPCA x DI e DI + Spread com fluxos de liquidação periódica
  (resets trimestrais, semestrais, anuais ou personalizados);
- Acumulação determinística de fator DI diário via produtório dos fatores diários
  (1 + CDI_k / 100)^(1/252) com arredondamento estrito Cetip a 8 casas decimais;
- Liquidação por ajuste diferencial líquido (Cash Settlement) a cada data de reset;
- Retenção tributária na fonte (IRRF sobre rendimento líquido positivo - IN RFB nº 1.585/2015);
- Invariante formal bilateral soma-zero (LAW-080 / Saldo_Líquido_A + Saldo_Líquido_B == 0,00);
- Invariante formal de Não-Arbitragem na Curva Pré/DI e estrutura a termo (LAW-081).

Zero dependências externas (100% Python Standard Library).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

try:
    from src.invariants import (
        InvariantViolationError,
        assert_bilateral_zero_sum,
        assert_chronological_sanity,
        assert_irs_periodic_reset_zero_sum,
        assert_no_arbitrage_pre_di_curve,
        check,
    )
except ImportError:
    try:
        from invariants import (  # type: ignore
            InvariantViolationError,
            assert_bilateral_zero_sum,
            assert_chronological_sanity,
            assert_irs_periodic_reset_zero_sum,
            assert_no_arbitrage_pre_di_curve,
            check,
        )
    except ImportError:
        class InvariantViolationError(AssertionError):  # type: ignore
            def __init__(self, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
                super().__init__(message)
                self.message = message
                self.law_code = law_code
                self.details = details or {}

        def check(condition: bool, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
            if not condition:
                raise InvariantViolationError(message, law_code=law_code, details=details)

        def assert_irs_periodic_reset_zero_sum(party_a_amount: Decimal, party_b_amount: Decimal, period_index: int = 1, contract_id: str = "") -> None:
            total = party_a_amount + party_b_amount
            check(total == Decimal("0.00"), f"Violação soma-zero IRS {contract_id}: {total} != 0", law_code="LAW-080")

        def assert_no_arbitrage_pre_di_curve(telescoping_forward_sum: Decimal, terminal_discount_factor: Decimal, initial_discount_factor: Decimal = Decimal("1.00000000"), tolerance: Decimal = Decimal("0.0001"), context_desc: str = "") -> None:
            expected = initial_discount_factor - terminal_discount_factor
            diff = abs(telescoping_forward_sum - expected)
            check(diff <= tolerance, f"Violação não-arbitragem {context_desc}: diff {diff} > {tolerance}", law_code="LAW-081")

        def assert_bilateral_zero_sum(party_a_amount: Decimal, party_b_amount: Decimal, tolerance: Decimal = Decimal("0.00"), context_desc: str = "") -> None:
            diff = abs(party_a_amount + party_b_amount)
            check(diff <= tolerance, f"Violação soma-zero {context_desc}: {diff} != 0", law_code="LAW-ZERO-SUM")

        def assert_chronological_sanity(start_date: Union[date, datetime], end_date: Union[date, datetime], context_desc: str = "") -> None:
            check(end_date >= start_date, f"Inversão cronológica {context_desc}", law_code="LAW-CHRONO")


# ---------------------------------------------------------------------------
# Constantes Numéricas e Convenções Cetip / NoMe B3
# ---------------------------------------------------------------------------

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
EIGHT_PLACES = Decimal("0.00000001")
BUSINESS_DAYS_YEAR = Decimal("252")


def _quantize_money(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda valores monetários em BRL para 2 casas decimais (centavos)."""
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _quantize_rate(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda taxas anuais e percentuais para 4 casas decimais."""
    return Decimal(str(value)).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def _quantize_factor(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda fatores diários e acumulados (padrão Cetip/B3) para 8 casas decimais."""
    return Decimal(str(value)).quantize(EIGHT_PLACES, rounding=ROUND_HALF_UP)


def _parse_iso_date(value: Union[str, date, datetime]) -> date:
    """Converte ISO-8601 (YYYY-MM-DD) ou datetime para date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    cleaned = str(value).strip()
    if "T" in cleaned:
        cleaned = cleaned.split("T", 1)[0]
    return date.fromisoformat(cleaned)


def _decimal_power_du252(base: Decimal, du_days: int) -> Decimal:
    """Calcula base^(du_days / 252) com precisão estendida de 50 dígitos em Decimal."""
    if du_days == 0 or base == Decimal("1"):
        return Decimal("1")
    if du_days == 252:
        return base
    with localcontext() as ctx:
        ctx.prec = 50
        exponent = Decimal(du_days) / BUSINESS_DAYS_YEAR
        return (base.ln() * exponent).exp()


def _decimal_power(base: Decimal, exponent: Decimal) -> Decimal:
    """Calcula base^exponent em Decimal com precisão de 50 dígitos."""
    if exponent == Decimal("0") or base == Decimal("1"):
        return Decimal("1")
    if exponent == Decimal("1"):
        return base
    with localcontext() as ctx:
        ctx.prec = 50
        return (base.ln() * exponent).exp()


def regressive_irrf_rate_for_days(calendar_days: int) -> Decimal:
    """Retorna alíquota regressiva de IRRF (IN RFB nº 1.585/2015, art. 46/54).

    - Até 180 dias: 22,5% (0.2250)
    - De 181 a 360 dias: 20,0% (0.2000)
    - De 361 a 720 dias: 17,5% (0.1750)
    - Acima de 720 dias: 15,0% (0.1500)
    """
    if calendar_days <= 0:
        raise IrsValidationError(f"Prazo em dias corridos para IRRF deve ser positivo: {calendar_days}")
    if calendar_days <= 180:
        return Decimal("0.2250")
    if calendar_days <= 360:
        return Decimal("0.2000")
    if calendar_days <= 720:
        return Decimal("0.1750")
    return Decimal("0.1500")


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------


class IrsError(Exception):
    """Exceção base para o subsistema de Swaps de Taxa de Juros (IRS - FL-080)."""


class IrsValidationError(IrsError):
    """Lançada quando parâmetros de swap ou período violam regras da B3 ou CMN."""


class DuplicateIrsContractError(IrsError):
    """Lançada ao registrar contrato ou protocolo NoMe duplicado."""


class IrsContractNotFoundError(IrsError):
    """Lançada ao consultar ou liquidar contrato inexistente no registro NoMe."""


class ArbitrageViolationError(IrsError, InvariantViolationError):
    """Lançada quando a curva Pré/DI ou taxa de swap viola condições de não-arbitragem."""


# ---------------------------------------------------------------------------
# Enumerações
# ---------------------------------------------------------------------------


class ResetFrequency(str, Enum):
    """Periodicidade de repactuação e liquidação de ajuste periódico (Reset)."""

    QUARTERLY = "QUARTERLY"      # Trimestral (~63 DU)
    SEMIANNUAL = "SEMIANNUAL"    # Semestral (~126 DU)
    ANNUAL = "ANNUAL"            # Anual (~252 DU)
    MONTHLY = "MONTHLY"          # Mensal (~21 DU)
    CUSTOM = "CUSTOM"            # Grade de períodos customizada

    @classmethod
    def from_str(cls, value: Any) -> ResetFrequency:
        if isinstance(value, ResetFrequency):
            return value
        normalized = str(value).strip().upper()
        mapping = {
            "QUARTERLY": cls.QUARTERLY,
            "TRIMESTRAL": cls.QUARTERLY,
            "TRIMESTRE": cls.QUARTERLY,
            "SEMIANNUAL": cls.SEMIANNUAL,
            "SEMESTRAL": cls.SEMIANNUAL,
            "SEMESTRE": cls.SEMIANNUAL,
            "ANNUAL": cls.ANNUAL,
            "ANUAL": cls.ANNUAL,
            "ANO": cls.ANNUAL,
            "MONTHLY": cls.MONTHLY,
            "MENSAL": cls.MONTHLY,
            "CUSTOM": cls.CUSTOM,
            "PERSONALIZADO": cls.CUSTOM,
        }
        if normalized in mapping:
            return mapping[normalized]
        raise IrsValidationError(f"Frequência de reset inválida: {value!r}")


class IrsIndexerType(str, Enum):
    """Indexadores financeiros aceitos em pontas de IRS no Balcão B3."""

    PRE = "PRE"      # Taxa Prefixada anual base 252
    CDI = "CDI"      # Taxa DI Over acumulada diária base 252
    IPCA = "IPCA"    # IPCA acumulado no período + Cupom de juros real anual base 252

    @classmethod
    def from_str(cls, value: Any) -> IrsIndexerType:
        if isinstance(value, IrsIndexerType):
            return value
        normalized = str(value).strip().upper()
        mapping = {
            "PRE": cls.PRE,
            "PRÉ": cls.PRE,
            "PREFIXADO": cls.PRE,
            "FIXED": cls.PRE,
            "CDI": cls.CDI,
            "DI": cls.CDI,
            "TAXA_DI": cls.CDI,
            "IPCA": cls.IPCA,
            "INFLACAO": cls.IPCA,
            "INFLAÇÃO": cls.IPCA,
        }
        if normalized in mapping:
            return mapping[normalized]
        raise IrsValidationError(f"Indexador IRS inválido: {value!r}")


class GuaranteeMode(str, Enum):
    """Modalidade de garantia na Câmara B3 para contratos de balcão NoMe."""

    COM_GARANTIA_CCP = "COM_GARANTIA_CCP"
    SEM_GARANTIA_CCP = "SEM_GARANTIA_CCP"

    @classmethod
    def from_str(cls, value: Any) -> GuaranteeMode:
        if isinstance(value, GuaranteeMode):
            return value
        normalized = str(value).strip().upper()
        mapping = {
            "COM_GARANTIA_CCP": cls.COM_GARANTIA_CCP,
            "COM_GARANTIA": cls.COM_GARANTIA_CCP,
            "CCP": cls.COM_GARANTIA_CCP,
            "SEM_GARANTIA_CCP": cls.SEM_GARANTIA_CCP,
            "SEM_GARANTIA": cls.SEM_GARANTIA_CCP,
            "BILATERAL": cls.SEM_GARANTIA_CCP,
        }
        if normalized in mapping:
            return mapping[normalized]
        raise IrsValidationError(f"Modalidade de garantia inválida: {value!r}")


# ---------------------------------------------------------------------------
# Especificação de Ponta de Swap (IrsLegSpec)
# ---------------------------------------------------------------------------


@dataclass
class IrsLegSpec:
    """Especificação contratual de uma ponta do Swap de Taxa de Juros (IRS).

    Atributos:
        indexer: Indexador financeiro (PRE, CDI, IPCA).
        rate_annual_pct:
            - Para PRE: taxa fixa contratada (% a.a., base DU/252).
            - Para CDI: taxa DI nominal média esperada/fixa para projeção (% a.a.).
            - Para IPCA: cupom de juros real (% a.a., base DU/252).
        cdi_percentage: Percentual do CDI incidente (ex: 100.0000 para 100% do DI).
        spread_annual_pct: Spread adicional em taxa anual (% a.a., base DU/252).
    """

    indexer: IrsIndexerType
    rate_annual_pct: Decimal = field(default=Decimal("0.0000"))
    cdi_percentage: Decimal = field(default=Decimal("100.0000"))
    spread_annual_pct: Decimal = field(default=Decimal("0.0000"))

    def __post_init__(self) -> None:
        self.indexer = IrsIndexerType.from_str(self.indexer)
        self.rate_annual_pct = _quantize_rate(self.rate_annual_pct)
        self.cdi_percentage = _quantize_rate(self.cdi_percentage)
        self.spread_annual_pct = _quantize_rate(self.spread_annual_pct)

        if self.indexer == IrsIndexerType.CDI and self.cdi_percentage <= Decimal("0"):
            raise IrsValidationError("cdi_percentage para ponta CDI deve ser estritamente positivo.")

    def calculate_factor(
        self,
        business_days_du: int,
        daily_cdi_rates: Optional[Sequence[Decimal]] = None,
        ipca_period_pct: Optional[Decimal] = None,
        rate_override: Optional[Decimal] = None,
        explicit_factor: Optional[Decimal] = None,
    ) -> Decimal:
        """Calcula o Fator Acumulado da ponta no período de reset com 8 casas decimais.

        Regras:
        1. Se explicit_factor for fornecido (> 0), retorna-o imediatamente (fator oficial Cetip).
        2. PRE: F = (1 + r_pre / 100)^(DU / 252)
        3. CDI:
           - Se daily_cdi_rates for fornecido: acumula cada dia k:
             f_k = round((1 + CDI_k / 100)^(1/252), 8)
             F_cdi = round(prod(f_k), 8)
           - Caso contrário: utiliza taxa anual (rate_override ou rate_annual_pct):
             F_cdi = round((1 + r_cdi / 100)^(DU / 252), 8)
           - Aplica percentual do CDI e spread se contratados:
             F_adj = 1 + (F_cdi - 1) * (cdi_percentage / 100)
             Se houver spread: F_adj = F_adj * (1 + spread / 100)^(DU / 252)
             Fator final arredondado a 8 casas decimais.
        4. IPCA: F = round((1 + ipca_pct / 100) * (1 + cupom_real / 100)^(DU / 252), 8)
        """
        if explicit_factor is not None:
            return _quantize_factor(explicit_factor)

        if business_days_du < 0:
            raise IrsValidationError(f"business_days_du não pode ser negativo: {business_days_du}")

        effective_rate = rate_override if rate_override is not None else self.rate_annual_pct
        effective_rate = _quantize_rate(effective_rate)

        with localcontext() as ctx:
            ctx.prec = 50

            if self.indexer == IrsIndexerType.PRE:
                base = Decimal("1") + (effective_rate / Decimal("100"))
                raw_factor = _decimal_power_du252(base, business_days_du)
                return _quantize_factor(raw_factor)

            if self.indexer == IrsIndexerType.CDI:
                if daily_cdi_rates is not None and len(daily_cdi_rates) > 0:
                    accumulated = Decimal("1.00000000")
                    for r_k in daily_cdi_rates:
                        r_dec = _quantize_rate(r_k)
                        base_k = Decimal("1") + (r_dec / Decimal("100"))
                        f_k = _quantize_factor(_decimal_power_du252(base_k, 1))
                        accumulated = _quantize_factor(accumulated * f_k)
                    pure_di_factor = accumulated
                else:
                    base_di = Decimal("1") + (effective_rate / Decimal("100"))
                    pure_di_factor = _quantize_factor(_decimal_power_du252(base_di, business_days_du))

                pct_multiplier = self.cdi_percentage / Decimal("100")
                cdi_factor = Decimal("1") + (pure_di_factor - Decimal("1")) * pct_multiplier

                if self.spread_annual_pct != Decimal("0"):
                    base_spread = Decimal("1") + (self.spread_annual_pct / Decimal("100"))
                    spread_factor = _decimal_power_du252(base_spread, business_days_du)
                    cdi_factor = cdi_factor * spread_factor

                return _quantize_factor(cdi_factor)

            if self.indexer == IrsIndexerType.IPCA:
                ipca_pct = ipca_period_pct if ipca_period_pct is not None else Decimal("0.0000")
                inflation_factor = Decimal("1") + (ipca_pct / Decimal("100"))
                base_real = Decimal("1") + (effective_rate / Decimal("100"))
                real_factor = _decimal_power_du252(base_real, business_days_du)
                return _quantize_factor(inflation_factor * real_factor)

        raise IrsValidationError(f"Indexador não suportado: {self.indexer}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "indexer": self.indexer.value,
            "rate_annual_pct": str(self.rate_annual_pct),
            "cdi_percentage": str(self.cdi_percentage),
            "spread_annual_pct": str(self.spread_annual_pct),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> IrsLegSpec:
        return cls(
            indexer=IrsIndexerType.from_str(data["indexer"]),
            rate_annual_pct=Decimal(str(data.get("rate_annual_pct", "0.0000"))),
            cdi_percentage=Decimal(str(data.get("cdi_percentage", "100.0000"))),
            spread_annual_pct=Decimal(str(data.get("spread_annual_pct", "0.0000"))),
        )


# ---------------------------------------------------------------------------
# Período de Repactuação / Reset (IrsResetPeriod)
# ---------------------------------------------------------------------------


@dataclass
class IrsResetPeriod:
    """Especificação de um período individual de repactuação/reset e apuração de fluxo.

    Atributos:
        period_index: Número de ordem do período (1, 2, ..., N).
        start_date: Data de início do período de cálculo.
        end_date: Data de corte/apuração do período (inclusive início, exclusive fim ou corte D-1).
        settlement_date: Data de liquidação financeira na câmara/balcão (Cash Settlement).
        business_days_du: Número de dias úteis no período (convenção 252).
        calendar_days_dc: Número de dias corridos no período.
        notional_brl: Valor nocional de referência para este período (permite amortização).
        daily_cdi_rates: Lista opcional de taxas diárias anualizadas do CDI no período (% a.a.).
        ipca_period_pct: Variação acumulada do IPCA no período em percentual (% ex: 1.2500).
        active_rate_override: Sobrescrita de taxa anual para a ponta ativa no período.
        passive_rate_override: Sobrescrita de taxa anual para a ponta passiva no período.
        active_explicit_factor: Fator acumulado explícito para ponta ativa.
        passive_explicit_factor: Fator acumulado explícito para ponta passiva.
    """

    period_index: int
    start_date: date
    end_date: date
    settlement_date: date
    business_days_du: int
    calendar_days_dc: int
    notional_brl: Decimal
    daily_cdi_rates: Optional[List[Decimal]] = None
    ipca_period_pct: Optional[Decimal] = None
    active_rate_override: Optional[Decimal] = None
    passive_rate_override: Optional[Decimal] = None
    active_explicit_factor: Optional[Decimal] = None
    passive_explicit_factor: Optional[Decimal] = None

    def __post_init__(self) -> None:
        self.start_date = _parse_iso_date(self.start_date)
        self.end_date = _parse_iso_date(self.end_date)
        self.settlement_date = _parse_iso_date(self.settlement_date)
        self.notional_brl = _quantize_money(self.notional_brl)

        if self.period_index <= 0:
            raise IrsValidationError(f"period_index deve ser positivo: {self.period_index}")
        if self.end_date <= self.start_date:
            raise IrsValidationError(
                f"end_date ({self.end_date}) deve ser estritamente posterior a start_date ({self.start_date})."
            )
        if self.settlement_date < self.end_date:
            raise IrsValidationError(
                f"settlement_date ({self.settlement_date}) deve ser posterior ou igual a end_date ({self.end_date})."
            )
        if self.business_days_du <= 0:
            raise IrsValidationError(f"business_days_du deve ser positivo: {self.business_days_du}")
        if self.calendar_days_dc <= 0:
            self.calendar_days_dc = (self.end_date - self.start_date).days
        if self.notional_brl <= Decimal("0"):
            raise IrsValidationError(f"notional_brl deve ser positivo: {self.notional_brl}")

        if self.daily_cdi_rates is not None:
            self.daily_cdi_rates = [_quantize_rate(r) for r in self.daily_cdi_rates]
        if self.ipca_period_pct is not None:
            self.ipca_period_pct = _quantize_rate(self.ipca_period_pct)
        if self.active_rate_override is not None:
            self.active_rate_override = _quantize_rate(self.active_rate_override)
        if self.passive_rate_override is not None:
            self.passive_rate_override = _quantize_rate(self.passive_rate_override)
        if self.active_explicit_factor is not None:
            self.active_explicit_factor = _quantize_factor(self.active_explicit_factor)
        if self.passive_explicit_factor is not None:
            self.passive_explicit_factor = _quantize_factor(self.passive_explicit_factor)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "period_index": self.period_index,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
            "business_days_du": self.business_days_du,
            "calendar_days_dc": self.calendar_days_dc,
            "notional_brl": str(self.notional_brl),
            "daily_cdi_rates": [str(r) for r in self.daily_cdi_rates] if self.daily_cdi_rates else None,
            "ipca_period_pct": str(self.ipca_period_pct) if self.ipca_period_pct is not None else None,
            "active_rate_override": str(self.active_rate_override) if self.active_rate_override is not None else None,
            "passive_rate_override": str(self.passive_rate_override) if self.passive_rate_override is not None else None,
            "active_explicit_factor": str(self.active_explicit_factor) if self.active_explicit_factor is not None else None,
            "passive_explicit_factor": str(self.passive_explicit_factor) if self.passive_explicit_factor is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> IrsResetPeriod:
        daily_rates = data.get("daily_cdi_rates")
        return cls(
            period_index=int(data["period_index"]),
            start_date=_parse_iso_date(data["start_date"]),
            end_date=_parse_iso_date(data["end_date"]),
            settlement_date=_parse_iso_date(data["settlement_date"]),
            business_days_du=int(data["business_days_du"]),
            calendar_days_dc=int(data.get("calendar_days_dc", 0)),
            notional_brl=Decimal(str(data["notional_brl"])),
            daily_cdi_rates=[Decimal(str(r)) for r in daily_rates] if daily_rates is not None else None,
            ipca_period_pct=Decimal(str(data["ipca_period_pct"])) if data.get("ipca_period_pct") is not None else None,
            active_rate_override=Decimal(str(data["active_rate_override"])) if data.get("active_rate_override") is not None else None,
            passive_rate_override=Decimal(str(data["passive_rate_override"])) if data.get("passive_rate_override") is not None else None,
            active_explicit_factor=Decimal(str(data["active_explicit_factor"])) if data.get("active_explicit_factor") is not None else None,
            passive_explicit_factor=Decimal(str(data["passive_explicit_factor"])) if data.get("passive_explicit_factor") is not None else None,
        )


# ---------------------------------------------------------------------------
# Contrato de Swap de Taxa de Juros com Ajustes Periódicos (IRS)
# ---------------------------------------------------------------------------


@dataclass
class InterestRateSwapContract:
    """Contrato bilateral de Swap de Taxa de Juros com Ajustes Periódicos (IRS) - Balcão NoMe B3.

    Perspectiva contratual:
    - Parte A (`party_a`): titular da Ponta Ativa (recebe `active_leg`) e pagadora da Ponta Passiva (`passive_leg`).
    - Parte B (`party_b`): titular da Ponta Passiva (recebe `passive_leg`) e pagadora da Ponta Ativa (`active_leg`).
    - A cada período de repactuação/reset, liquida-se o diferencial líquido de juros (Cash Settlement).
    """

    contract_id: str
    nome_protocol: str
    trade_date: date
    effective_date: date
    maturity_date: date
    reset_frequency: ResetFrequency
    notional_brl: Decimal
    party_a_name: str
    party_a_document: str
    party_b_name: str
    party_b_document: str
    active_leg: IrsLegSpec
    passive_leg: IrsLegSpec
    periods: List[IrsResetPeriod] = field(default_factory=list)
    party_a_irrf_rate: Decimal = field(default=Decimal("0.0000"))
    party_a_irrf_exempt: bool = False
    party_b_irrf_rate: Decimal = field(default=Decimal("0.0000"))
    party_b_irrf_exempt: bool = False
    guarantee_mode: GuaranteeMode = GuaranteeMode.SEM_GARANTIA_CCP
    is_corporate_hedge: bool = True

    def __post_init__(self) -> None:
        self.trade_date = _parse_iso_date(self.trade_date)
        self.effective_date = _parse_iso_date(self.effective_date)
        self.maturity_date = _parse_iso_date(self.maturity_date)
        self.reset_frequency = ResetFrequency.from_str(self.reset_frequency)
        self.notional_brl = _quantize_money(self.notional_brl)
        self.party_a_irrf_rate = _quantize_rate(self.party_a_irrf_rate)
        self.party_b_irrf_rate = _quantize_rate(self.party_b_irrf_rate)
        self.guarantee_mode = GuaranteeMode.from_str(self.guarantee_mode)

        if not self.contract_id or not self.contract_id.strip():
            raise IrsValidationError("contract_id não pode ser vazio.")
        if not self.nome_protocol or not self.nome_protocol.strip():
            raise IrsValidationError("nome_protocol não pode ser vazio.")
        if self.effective_date < self.trade_date:
            raise IrsValidationError(
                f"effective_date ({self.effective_date}) anterior à data de registro trade_date ({self.trade_date})."
            )
        if self.maturity_date <= self.effective_date:
            raise IrsValidationError(
                f"maturity_date ({self.maturity_date}) deve ser posterior a effective_date ({self.effective_date})."
            )
        if self.notional_brl <= Decimal("0"):
            raise IrsValidationError(f"notional_brl deve ser positivo: {self.notional_brl}")
        if not self.party_a_document.strip() or not self.party_b_document.strip():
            raise IrsValidationError("CNPJ/CPF das partes A e B são obrigatórios.")
        if self.party_a_document.strip() == self.party_b_document.strip():
            raise IrsValidationError("Operação consigo mesmo vedada no Balcão B3 (Partes A e B idênticas).")

        assert_chronological_sanity(self.effective_date, self.maturity_date, f"IRS {self.contract_id}")

        if not self.periods:
            raise IrsValidationError(f"Contrato IRS '{self.contract_id}' deve conter ao menos um período de reset.")

        # Validação de continuidade e ordenação dos períodos
        for idx, p in enumerate(self.periods):
            if p.period_index != idx + 1:
                raise IrsValidationError(
                    f"Sequência inválida de período #{p.period_index} na posição {idx + 1}."
                )
            if idx > 0:
                prev = self.periods[idx - 1]
                if p.start_date < prev.end_date:
                    raise IrsValidationError(
                        f"Sobreposição cronológica no período #{p.period_index}: início {p.start_date} < fim anterior {prev.end_date}."
                    )

    @property
    def total_business_days_du(self) -> int:
        """Soma total de dias úteis em todos os períodos de reset."""
        return sum(p.business_days_du for p in self.periods)

    @property
    def total_calendar_days_dc(self) -> int:
        """Soma total de dias corridos em todos os períodos de reset."""
        return sum(p.calendar_days_dc for p in self.periods)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "nome_protocol": self.nome_protocol,
            "trade_date": self.trade_date.isoformat(),
            "effective_date": self.effective_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "reset_frequency": self.reset_frequency.value,
            "notional_brl": str(self.notional_brl),
            "party_a_name": self.party_a_name,
            "party_a_document": self.party_a_document,
            "party_a_irrf_rate": str(self.party_a_irrf_rate),
            "party_a_irrf_exempt": self.party_a_irrf_exempt,
            "party_b_name": self.party_b_name,
            "party_b_document": self.party_b_document,
            "party_b_irrf_rate": str(self.party_b_irrf_rate),
            "party_b_irrf_exempt": self.party_b_irrf_exempt,
            "guarantee_mode": self.guarantee_mode.value,
            "is_corporate_hedge": self.is_corporate_hedge,
            "active_leg": self.active_leg.to_dict(),
            "passive_leg": self.passive_leg.to_dict(),
            "periods": [p.to_dict() for p in self.periods],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InterestRateSwapContract:
        active_leg_data = data["active_leg"]
        passive_leg_data = data["passive_leg"]
        return cls(
            contract_id=str(data["contract_id"]),
            nome_protocol=str(data["nome_protocol"]),
            trade_date=_parse_iso_date(data["trade_date"]),
            effective_date=_parse_iso_date(data.get("effective_date", data["trade_date"])),
            maturity_date=_parse_iso_date(data["maturity_date"]),
            reset_frequency=ResetFrequency.from_str(data.get("reset_frequency", "CUSTOM")),
            notional_brl=Decimal(str(data["notional_brl"])),
            party_a_name=str(data["party_a_name"]),
            party_a_document=str(data["party_a_document"]),
            party_a_irrf_rate=Decimal(str(data.get("party_a_irrf_rate", "0.0000"))),
            party_a_irrf_exempt=bool(data.get("party_a_irrf_exempt", False)),
            party_b_name=str(data["party_b_name"]),
            party_b_document=str(data["party_b_document"]),
            party_b_irrf_rate=Decimal(str(data.get("party_b_irrf_rate", "0.0000"))),
            party_b_irrf_exempt=bool(data.get("party_b_irrf_exempt", False)),
            guarantee_mode=GuaranteeMode.from_str(data.get("guarantee_mode", "SEM_GARANTIA_CCP")),
            is_corporate_hedge=bool(data.get("is_corporate_hedge", True)),
            active_leg=(
                active_leg_data
                if isinstance(active_leg_data, IrsLegSpec)
                else IrsLegSpec.from_dict(active_leg_data)
            ),
            passive_leg=(
                passive_leg_data
                if isinstance(passive_leg_data, IrsLegSpec)
                else IrsLegSpec.from_dict(passive_leg_data)
            ),
            periods=[
                p if isinstance(p, IrsResetPeriod) else IrsResetPeriod.from_dict(p)
                for p in data.get("periods", [])
            ],
        )


# ---------------------------------------------------------------------------
# Resultados Determinísticos de Liquidação (Periódico e Consolidado)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IrsPeriodSettlementResult:
    """Resultado auditável da apuração de ajuste diferencial em um período de reset."""

    period_index: int
    start_date: date
    end_date: date
    settlement_date: date
    business_days_du: int
    calendar_days_dc: int
    notional_brl: Decimal
    active_factor: Decimal
    passive_factor: Decimal
    active_cashflow_brl: Decimal
    passive_cashflow_brl: Decimal
    party_a_gross_brl: Decimal
    party_a_irrf_brl: Decimal
    party_a_net_brl: Decimal
    party_b_gross_brl: Decimal
    party_b_irrf_brl: Decimal
    party_b_net_brl: Decimal
    total_irrf_brl: Decimal
    payer_document: Optional[str]
    receiver_document: Optional[str]

    @property
    def is_zero_sum_conserved(self) -> bool:
        """Lei 65 (LAW-080): Conservação Bilateral Soma-Zero centavo a centavo."""
        return (self.party_a_gross_brl + self.party_b_gross_brl) == Decimal("0.00")

    @property
    def is_cashflow_balanced(self) -> bool:
        """Conservação financeira: Net_A + Net_B + IRRF_Total == 0.00."""
        return (self.party_a_net_brl + self.party_b_net_brl + self.total_irrf_brl) == Decimal("0.00")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "period_index": self.period_index,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
            "business_days_du": self.business_days_du,
            "calendar_days_dc": self.calendar_days_dc,
            "notional_brl": str(self.notional_brl),
            "active_factor": str(self.active_factor),
            "passive_factor": str(self.passive_factor),
            "active_cashflow_brl": str(self.active_cashflow_brl),
            "passive_cashflow_brl": str(self.passive_cashflow_brl),
            "party_a_gross_brl": str(self.party_a_gross_brl),
            "party_a_irrf_brl": str(self.party_a_irrf_brl),
            "party_a_net_brl": str(self.party_a_net_brl),
            "party_b_gross_brl": str(self.party_b_gross_brl),
            "party_b_irrf_brl": str(self.party_b_irrf_brl),
            "party_b_net_brl": str(self.party_b_net_brl),
            "total_irrf_brl": str(self.total_irrf_brl),
            "payer_document": self.payer_document,
            "receiver_document": self.receiver_document,
            "is_zero_sum_conserved": self.is_zero_sum_conserved,
            "is_cashflow_balanced": self.is_cashflow_balanced,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> IrsPeriodSettlementResult:
        return cls(
            period_index=int(data["period_index"]),
            start_date=_parse_iso_date(data["start_date"]),
            end_date=_parse_iso_date(data["end_date"]),
            settlement_date=_parse_iso_date(data["settlement_date"]),
            business_days_du=int(data["business_days_du"]),
            calendar_days_dc=int(data.get("calendar_days_dc", 0)),
            notional_brl=Decimal(str(data["notional_brl"])),
            active_factor=Decimal(str(data["active_factor"])),
            passive_factor=Decimal(str(data["passive_factor"])),
            active_cashflow_brl=Decimal(str(data["active_cashflow_brl"])),
            passive_cashflow_brl=Decimal(str(data["passive_cashflow_brl"])),
            party_a_gross_brl=Decimal(str(data["party_a_gross_brl"])),
            party_a_irrf_brl=Decimal(str(data["party_a_irrf_brl"])),
            party_a_net_brl=Decimal(str(data["party_a_net_brl"])),
            party_b_gross_brl=Decimal(str(data["party_b_gross_brl"])),
            party_b_irrf_brl=Decimal(str(data["party_b_irrf_brl"])),
            party_b_net_brl=Decimal(str(data["party_b_net_brl"])),
            total_irrf_brl=Decimal(str(data["total_irrf_brl"])),
            payer_document=data.get("payer_document"),
            receiver_document=data.get("receiver_document"),
        )



@dataclass(frozen=True)
class IrsContractSettlementResult:
    """Resultado consolidado da liquidação completa de um contrato IRS."""

    contract_id: str
    nome_protocol: str
    trade_date: date
    maturity_date: date
    notional_brl: Decimal
    party_a_name: str
    party_a_document: str
    party_b_name: str
    party_b_document: str
    guarantee_mode: GuaranteeMode
    is_corporate_hedge: bool
    period_settlements: List[IrsPeriodSettlementResult]
    total_periods: int
    cum_party_a_gross_brl: Decimal
    cum_party_a_irrf_brl: Decimal
    cum_party_a_net_brl: Decimal
    cum_party_b_gross_brl: Decimal
    cum_party_b_irrf_brl: Decimal
    cum_party_b_net_brl: Decimal
    cum_total_irrf_brl: Decimal

    @property
    def is_zero_sum_conserved(self) -> bool:
        """Conservação cumulativa soma-zero: Cum_Gross_A + Cum_Gross_B == 0.00."""
        return (self.cum_party_a_gross_brl + self.cum_party_b_gross_brl) == Decimal("0.00")

    @property
    def is_cashflow_balanced(self) -> bool:
        """Conservação líquida consolidada com IRRF retido na fonte."""
        return (
            self.cum_party_a_net_brl + self.cum_party_b_net_brl + self.cum_total_irrf_brl
        ) == Decimal("0.00")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "nome_protocol": self.nome_protocol,
            "trade_date": self.trade_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "notional_brl": str(self.notional_brl),
            "party_a_name": self.party_a_name,
            "party_a_document": self.party_a_document,
            "party_b_name": self.party_b_name,
            "party_b_document": self.party_b_document,
            "guarantee_mode": self.guarantee_mode.value,
            "is_corporate_hedge": self.is_corporate_hedge,
            "total_periods": self.total_periods,
            "cum_party_a_gross_brl": str(self.cum_party_a_gross_brl),
            "cum_party_a_irrf_brl": str(self.cum_party_a_irrf_brl),
            "cum_party_a_net_brl": str(self.cum_party_a_net_brl),
            "cum_party_b_gross_brl": str(self.cum_party_b_gross_brl),
            "cum_party_b_irrf_brl": str(self.cum_party_b_irrf_brl),
            "cum_party_b_net_brl": str(self.cum_party_b_net_brl),
            "cum_total_irrf_brl": str(self.cum_total_irrf_brl),
            "is_zero_sum_conserved": self.is_zero_sum_conserved,
            "is_cashflow_balanced": self.is_cashflow_balanced,
            "period_settlements": [p.to_dict() for p in self.period_settlements],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> IrsContractSettlementResult:
        period_settlements = [
            IrsPeriodSettlementResult.from_dict(p)
            for p in data.get("period_settlements", [])
        ]
        return cls(
            contract_id=str(data["contract_id"]),
            nome_protocol=str(data["nome_protocol"]),
            trade_date=_parse_iso_date(data["trade_date"]),
            maturity_date=_parse_iso_date(data["maturity_date"]),
            notional_brl=Decimal(str(data["notional_brl"])),
            party_a_name=str(data["party_a_name"]),
            party_a_document=str(data["party_a_document"]),
            party_b_name=str(data["party_b_name"]),
            party_b_document=str(data["party_b_document"]),
            guarantee_mode=GuaranteeMode.from_str(data.get("guarantee_mode", "SEM_GARANTIA_CCP")),
            is_corporate_hedge=bool(data.get("is_corporate_hedge", True)),
            period_settlements=period_settlements,
            total_periods=int(data.get("total_periods", len(period_settlements))),
            cum_party_a_gross_brl=Decimal(str(data["cum_party_a_gross_brl"])),
            cum_party_a_irrf_brl=Decimal(str(data["cum_party_a_irrf_brl"])),
            cum_party_a_net_brl=Decimal(str(data["cum_party_a_net_brl"])),
            cum_party_b_gross_brl=Decimal(str(data["cum_party_b_gross_brl"])),
            cum_party_b_irrf_brl=Decimal(str(data["cum_party_b_irrf_brl"])),
            cum_party_b_net_brl=Decimal(str(data["cum_party_b_net_brl"])),
            cum_total_irrf_brl=Decimal(str(data["cum_total_irrf_brl"])),
        )



# ---------------------------------------------------------------------------
# Curva Pré/DI e Verificação Formal de Não-Arbitragem (PreDiZeroCurve)
# ---------------------------------------------------------------------------


@dataclass
class ZeroCurveVertex:
    """Vértice da estrutura a termo da curva Pré/DI."""

    du_days: int
    zero_rate_pct: Decimal  # % a.a. base 252

    def __post_init__(self) -> None:
        self.du_days = int(self.du_days)
        self.zero_rate_pct = _quantize_rate(self.zero_rate_pct)
        if self.du_days < 0:
            raise IrsValidationError("du_days do vértice não pode ser negativo.")

    @property
    def discount_factor(self) -> Decimal:
        """Calcula o Fator de Desconto DF = (1 + r / 100)^(-du / 252)."""
        if self.du_days == 0:
            return Decimal("1.00000000")
        base = Decimal("1") + (self.zero_rate_pct / Decimal("100"))
        with localcontext() as ctx:
            ctx.prec = 50
            inv = _decimal_power_du252(base, self.du_days)
            df = Decimal("1") / inv
            return _quantize_factor(df)


@dataclass
class PreDiZeroCurve:
    """Representação determinística da curva de juros Pré x DI para precificação e não-arbitragem."""

    curve_date: date
    vertices: List[ZeroCurveVertex]

    def __post_init__(self) -> None:
        self.curve_date = _parse_iso_date(self.curve_date)
        self.vertices.sort(key=lambda v: v.du_days)
        if not self.vertices:
            raise IrsValidationError("PreDiZeroCurve exige ao menos um vértice.")
        # Garante vértice DU=0
        if self.vertices[0].du_days > 0:
            self.vertices.insert(0, ZeroCurveVertex(du_days=0, zero_rate_pct=Decimal("0.0000")))

    def zero_rate_at(self, du_days: int) -> Decimal:
        """Interpolação linear da taxa zero % a.a. para o prazo em DU."""
        if du_days <= 0:
            return Decimal("0.0000")
        if du_days <= self.vertices[0].du_days:
            return self.vertices[0].zero_rate_pct
        if du_days >= self.vertices[-1].du_days:
            return self.vertices[-1].zero_rate_pct

        for i in range(len(self.vertices) - 1):
            v0 = self.vertices[i]
            v1 = self.vertices[i + 1]
            if v0.du_days <= du_days <= v1.du_days:
                if v1.du_days == v0.du_days:
                    return v0.zero_rate_pct
                weight = Decimal(du_days - v0.du_days) / Decimal(v1.du_days - v0.du_days)
                rate = v0.zero_rate_pct + weight * (v1.zero_rate_pct - v0.zero_rate_pct)
                return _quantize_rate(rate)

        return self.vertices[-1].zero_rate_pct

    def discount_factor(self, du_days: int) -> Decimal:
        """Fator de Desconto DF(0, du) = (1 + r(du)/100)^(-du/252) com 8 casas decimais."""
        if du_days == 0:
            return Decimal("1.00000000")
        rate = self.zero_rate_at(du_days)
        base = Decimal("1") + (rate / Decimal("100"))
        with localcontext() as ctx:
            ctx.prec = 50
            inv = _decimal_power_du252(base, du_days)
            df = Decimal("1") / inv
            return _quantize_factor(df)

    def forward_factor(self, du_start: int, du_end: int) -> Decimal:
        """Fator forward implícito de DI entre du_start e du_end: DF(0, start) / DF(0, end)."""
        if du_end <= du_start:
            raise IrsValidationError(f"du_end ({du_end}) deve ser maior que du_start ({du_start}).")
        df_start = self.discount_factor(du_start)
        df_end = self.discount_factor(du_end)
        with localcontext() as ctx:
            ctx.prec = 50
            fwd = df_start / df_end
            return _quantize_factor(fwd)

    def forward_annual_rate(self, du_start: int, du_end: int) -> Decimal:
        """Taxa forward anualizada % a.a. entre du_start e du_end."""
        du_diff = du_end - du_start
        fwd_f = self.forward_factor(du_start, du_end)
        with localcontext() as ctx:
            ctx.prec = 50
            exponent = BUSINESS_DAYS_YEAR / Decimal(du_diff)
            fwd_rate = (_decimal_power(fwd_f, exponent) - Decimal("1")) * Decimal("100")
            return _quantize_rate(fwd_rate)

    def fair_swap_rate(
        self,
        periods_du: Sequence[int],
        notionals: Optional[Sequence[Decimal]] = None,
    ) -> Decimal:
        """Calcula a Taxa Par Teórica do Swap Pré x DI (Fair Swap Rate) que zera o NPV na contratação.

        Fórmula de não-arbitragem por replicação:
        Soma( N_j * ((1 + R_par)^(DU_j / 252) - 1) * DF_j ) == Soma( N_j * (F_fwd,j - 1) * DF_j ).
        Para nocional constante e períodos uniformes:
        (1 + R_par)^(DU / 252) - 1 = (1 - DF_terminal) / Soma(DF_j).
        Para períodos ou nocionais arbitrários: utiliza busca secante de alta precisão.
        """
        if not periods_du:
            raise IrsValidationError("Lista periods_du não pode ser vazia.")

        n_periods = len(periods_du)
        notionals_list = list(notionals) if notionals else [Decimal("1000000.00")] * n_periods

        cum_du: List[int] = []
        running = 0
        for du in periods_du:
            running += du
            cum_du.append(running)

        # Lado flutuante (Floating PV)
        floating_pv = Decimal("0.00000000")
        prev_du = 0
        for idx, du_step in enumerate(periods_du):
            target_du = cum_du[idx]
            df = self.discount_factor(target_du)
            fwd_factor = self.forward_factor(prev_du, target_du)
            floating_flow = notionals_list[idx] * (fwd_factor - Decimal("1.00000000"))
            floating_pv += floating_flow * df
            prev_du = target_du

        # Busca da taxa par R_par
        def pv_diff(test_rate_pct: Decimal) -> Decimal:
            fixed_pv = Decimal("0.00000000")
            base = Decimal("1") + (test_rate_pct / Decimal("100"))
            for idx, du_step in enumerate(periods_du):
                target_du = cum_du[idx]
                df = self.discount_factor(target_du)
                factor = _quantize_factor(_decimal_power_du252(base, du_step))
                fixed_flow = notionals_list[idx] * (factor - Decimal("1.00000000"))
                fixed_pv += fixed_flow * df
            return fixed_pv - floating_pv

        # Método secante
        r0 = self.zero_rate_at(cum_du[-1])
        r1 = r0 + Decimal("0.1000")
        f0 = pv_diff(r0)
        f1 = pv_diff(r1)

        for _ in range(100):
            if abs(f1) < Decimal("0.00000001"):
                break
            if (f1 - f0) == Decimal("0"):
                break
            r_next = r1 - f1 * (r1 - r0) / (f1 - f0)
            r0, r1 = r1, r_next
            f0 = f1
            f1 = pv_diff(r1)

        return _quantize_rate(r1)

    def verify_no_arbitrage(
        self,
        periods_du: Sequence[int],
        tolerance: Decimal = Decimal("0.0001"),
    ) -> bool:
        """Verifica formalmente a Lei 66 (LAW-081): Invariante de Não-Arbitragem na Curva Pré/DI.

        Condições verificadas:
        1. Monotonia dos Fatores de Desconto: DF(0, t_0) >= DF(0, t_1) >= ... >= DF(0, t_n) > 0.
        2. Ausência de Taxas Forward Negativas: F_fwd,j >= 1,00000000.
        3. Identidade Telescópica Estrita:
           Soma( (F_fwd,j - 1) * DF_j ) == DF_0 - DF_terminal (identidade de replicação).
        4. Telescopagem Multiplicativa dos Fatores Forward:
           prod(F_fwd,j) == 1 / DF_terminal == F_fwd(0, terminal).
        """
        if not periods_du:
            raise IrsValidationError("Lista periods_du não pode ser vazia para verificação.")

        cum_du: List[int] = []
        running = 0
        for du in periods_du:
            running += du
            cum_du.append(running)

        # 1. Monotonia dos fatores de desconto
        dfs: List[Decimal] = [Decimal("1.00000000")]
        for cdu in cum_du:
            df = self.discount_factor(cdu)
            check(
                df > Decimal("0"),
                f"Fator de desconto não positivo em DU={cdu}: {df}",
                law_code="LAW-081",
            )
            check(
                df <= dfs[-1],
                f"Quebra de monotonia na curva em DU={cdu}: DF={df} > anterior={dfs[-1]}",
                law_code="LAW-081",
            )
            dfs.append(df)

        # 2 & 3. Fatores forward e soma telescópica
        telescoping_sum = Decimal("0.00000000")
        fwd_product = Decimal("1.00000000")
        prev_du = 0

        for idx, du_step in enumerate(periods_du):
            target_du = cum_du[idx]
            df = self.discount_factor(target_du)
            fwd_f = self.forward_factor(prev_du, target_du)

            check(
                fwd_f >= Decimal("1.00000000"),
                f"Fator forward de juros negativo no período {idx + 1}: {fwd_f} < 1",
                law_code="LAW-081",
            )

            flow = fwd_f - Decimal("1.00000000")
            telescoping_sum += flow * df
            fwd_product = _quantize_factor(fwd_product * fwd_f)
            prev_du = target_du

        # Verificação da Lei 66 via invariante formal
        terminal_df = self.discount_factor(cum_du[-1])
        assert_no_arbitrage_pre_di_curve(
            telescoping_forward_sum=telescoping_sum,
            terminal_discount_factor=terminal_df,
            initial_discount_factor=Decimal("1.00000000"),
            tolerance=tolerance,
            context_desc=f"Curva Pré/DI data {self.curve_date} prazo {cum_du[-1]} DU",
        )

        # Telescopagem multiplicativa: prod(F_fwd) == 1 / DF_terminal
        expected_product = _quantize_factor(Decimal("1.00000000") / terminal_df)
        diff_prod = abs(fwd_product - expected_product)
        check(
            diff_prod <= Decimal("0.0001"),
            f"Falha na telescopagem multiplicativa de fatores forward: {fwd_product} != {expected_product}",
            law_code="LAW-081",
        )

        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "curve_date": self.curve_date.isoformat(),
            "vertices": [
                {"du_days": v.du_days, "zero_rate_pct": str(v.zero_rate_pct)}
                for v in self.vertices
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PreDiZeroCurve:
        return cls(
            curve_date=_parse_iso_date(data["curve_date"]),
            vertices=[
                ZeroCurveVertex(
                    du_days=int(v["du_days"]),
                    zero_rate_pct=Decimal(str(v["zero_rate_pct"])),
                )
                for v in data["vertices"]
            ],
        )


# ---------------------------------------------------------------------------
# Motor de Registro e Liquidação Periódica de IRS (InterestRateSwapEngine)
# ---------------------------------------------------------------------------


class InterestRateSwapEngine:
    """Motor da Registradora B3 (NoMe) para Registro e Liquidação de Swaps com Reset Periódico (FL-080)."""

    def __init__(self) -> None:
        self._contracts: Dict[str, InterestRateSwapContract] = {}
        self._protocols: Dict[str, str] = {}
        self._settlements: Dict[str, IrsContractSettlementResult] = {}
        self._curves: Dict[str, PreDiZeroCurve] = {}

    @property
    def contracts(self) -> Dict[str, InterestRateSwapContract]:
        return dict(self._contracts)

    @property
    def settlements(self) -> Dict[str, IrsContractSettlementResult]:
        return dict(self._settlements)

    @property
    def curves(self) -> Dict[str, PreDiZeroCurve]:
        return dict(self._curves)

    def register_curve(self, curve_id: str, curve: PreDiZeroCurve) -> PreDiZeroCurve:
        """Registra uma curva de juros Pré/DI de referência no subsistema de balcão."""
        self._curves[curve_id] = curve
        return curve

    def register_contract(self, contract: InterestRateSwapContract) -> InterestRateSwapContract:
        """Registra um Contrato de Swap com Ajustes Periódicos (IRS) no NoMe B3."""
        if contract.contract_id in self._contracts:
            raise DuplicateIrsContractError(
                f"Contrato IRS '{contract.contract_id}' já se encontra registrado no NoMe B3."
            )
        if contract.nome_protocol in self._protocols:
            raise DuplicateIrsContractError(
                f"Protocolo NoMe '{contract.nome_protocol}' já utilizado pelo contrato '{self._protocols[contract.nome_protocol]}'."
            )

        self._contracts[contract.contract_id] = contract
        self._protocols[contract.nome_protocol] = contract.contract_id
        return contract

    def get_contract(self, contract_id: str) -> InterestRateSwapContract:
        """Recupera contrato IRS registrado pelo identificador único."""
        if contract_id not in self._contracts:
            raise IrsContractNotFoundError(f"Contrato IRS '{contract_id}' não localizado no NoMe B3.")
        return self._contracts[contract_id]

    @staticmethod
    def _compute_irrf(gross_brl: Decimal, irrf_rate: Decimal, is_exempt: bool) -> Decimal:
        """Calcula o IRRF retido na fonte: incide estritamente sobre ganho bruto positivo."""
        if gross_brl <= Decimal("0.00") or is_exempt or irrf_rate <= Decimal("0.0000"):
            return Decimal("0.00")
        return _quantize_money(gross_brl * irrf_rate)

    def settle_period(
        self,
        contract: InterestRateSwapContract,
        period: IrsResetPeriod,
        elapsed_calendar_days: int,
    ) -> IrsPeriodSettlementResult:
        """Apura e liquida os fluxos de caixa de um único período de reset (Cash Settlement)."""
        f_active = contract.active_leg.calculate_factor(
            business_days_du=period.business_days_du,
            daily_cdi_rates=period.daily_cdi_rates,
            ipca_period_pct=period.ipca_period_pct,
            rate_override=period.active_rate_override,
            explicit_factor=period.active_explicit_factor,
        )

        f_passive = contract.passive_leg.calculate_factor(
            business_days_du=period.business_days_du,
            daily_cdi_rates=period.daily_cdi_rates,
            ipca_period_pct=period.ipca_period_pct,
            rate_override=period.passive_rate_override,
            explicit_factor=period.passive_explicit_factor,
        )

        active_flow = _quantize_money(period.notional_brl * (f_active - Decimal("1.00000000")))
        passive_flow = _quantize_money(period.notional_brl * (f_passive - Decimal("1.00000000")))

        # Ajuste diferencial da Parte A: Notional * (F_ativo - F_passivo)
        gross_a = _quantize_money(period.notional_brl * (f_active - f_passive))
        # Conservação bilateral soma-zero estrita centavo a centavo (LAW-080)
        gross_b = _quantize_money(-gross_a)

        assert_irs_periodic_reset_zero_sum(
            gross_a,
            gross_b,
            period_index=period.period_index,
            contract_id=contract.contract_id,
        )

        # Determinação de alíquotas de IRRF (tabela regressiva ou contratual)
        rate_a = contract.party_a_irrf_rate
        if rate_a == Decimal("0.0000") and not contract.party_a_irrf_exempt:
            rate_a = regressive_irrf_rate_for_days(elapsed_calendar_days)

        rate_b = contract.party_b_irrf_rate
        if rate_b == Decimal("0.0000") and not contract.party_b_irrf_exempt:
            rate_b = regressive_irrf_rate_for_days(elapsed_calendar_days)

        irrf_a = self._compute_irrf(gross_a, rate_a, contract.party_a_irrf_exempt)
        irrf_b = self._compute_irrf(gross_b, rate_b, contract.party_b_irrf_exempt)

        net_a = _quantize_money(gross_a - irrf_a)
        net_b = _quantize_money(gross_b - irrf_b)
        total_irrf = _quantize_money(irrf_a + irrf_b)

        # Definição de pagador e recebedor
        payer_doc: Optional[str] = None
        receiver_doc: Optional[str] = None
        if gross_a > Decimal("0.00"):
            receiver_doc = contract.party_a_document
            payer_doc = contract.party_b_document
        elif gross_b > Decimal("0.00"):
            receiver_doc = contract.party_b_document
            payer_doc = contract.party_a_document

        return IrsPeriodSettlementResult(
            period_index=period.period_index,
            start_date=period.start_date,
            end_date=period.end_date,
            settlement_date=period.settlement_date,
            business_days_du=period.business_days_du,
            calendar_days_dc=period.calendar_days_dc,
            notional_brl=period.notional_brl,
            active_factor=f_active,
            passive_factor=f_passive,
            active_cashflow_brl=active_flow,
            passive_cashflow_brl=passive_flow,
            party_a_gross_brl=gross_a,
            party_a_irrf_brl=irrf_a,
            party_a_net_brl=net_a,
            party_b_gross_brl=gross_b,
            party_b_irrf_brl=irrf_b,
            party_b_net_brl=net_b,
            total_irrf_brl=total_irrf,
            payer_document=payer_doc,
            receiver_document=receiver_doc,
        )

    def settle_contract(
        self,
        contract_or_id: Union[str, InterestRateSwapContract],
    ) -> IrsContractSettlementResult:
        """Executa a apuração e liquidação completa de todos os períodos de reset do IRS."""
        if isinstance(contract_or_id, InterestRateSwapContract):
            contract = contract_or_id
            if contract.contract_id not in self._contracts:
                self.register_contract(contract)
        else:
            contract = self.get_contract(contract_or_id)

        period_results: List[IrsPeriodSettlementResult] = []
        elapsed_dc = 0

        for p in contract.periods:
            elapsed_dc += p.calendar_days_dc
            p_res = self.settle_period(contract, p, elapsed_dc)
            period_results.append(p_res)

        cum_gross_a = _quantize_money(sum((pr.party_a_gross_brl for pr in period_results), Decimal("0.00")))
        cum_gross_b = _quantize_money(sum((pr.party_b_gross_brl for pr in period_results), Decimal("0.00")))
        cum_irrf_a = _quantize_money(sum((pr.party_a_irrf_brl for pr in period_results), Decimal("0.00")))
        cum_irrf_b = _quantize_money(sum((pr.party_b_irrf_brl for pr in period_results), Decimal("0.00")))
        cum_net_a = _quantize_money(sum((pr.party_a_net_brl for pr in period_results), Decimal("0.00")))
        cum_net_b = _quantize_money(sum((pr.party_b_net_brl for pr in period_results), Decimal("0.00")))
        cum_irrf = _quantize_money(sum((pr.total_irrf_brl for pr in period_results), Decimal("0.00")))

        # Invariante cumulativo soma-zero
        assert_bilateral_zero_sum(
            cum_gross_a,
            cum_gross_b,
            context_desc=f"IRS {contract.contract_id} cumulativo",
        )

        res = IrsContractSettlementResult(
            contract_id=contract.contract_id,
            nome_protocol=contract.nome_protocol,
            trade_date=contract.trade_date,
            maturity_date=contract.maturity_date,
            notional_brl=contract.notional_brl,
            party_a_name=contract.party_a_name,
            party_a_document=contract.party_a_document,
            party_b_name=contract.party_b_name,
            party_b_document=contract.party_b_document,
            guarantee_mode=contract.guarantee_mode,
            is_corporate_hedge=contract.is_corporate_hedge,
            period_settlements=period_results,
            total_periods=len(period_results),
            cum_party_a_gross_brl=cum_gross_a,
            cum_party_a_irrf_brl=cum_irrf_a,
            cum_party_a_net_brl=cum_net_a,
            cum_party_b_gross_brl=cum_gross_b,
            cum_party_b_irrf_brl=cum_irrf_b,
            cum_party_b_net_brl=cum_net_b,
            cum_total_irrf_brl=cum_irrf,
        )

        self._settlements[contract.contract_id] = res
        return res

    def settle_all(self) -> List[IrsContractSettlementResult]:
        """Liquida sequencialmente todos os contratos IRS registrados no NoMe B3."""
        return [self.settle_contract(cid) for cid in self._contracts]

    def verify_invariants(
        self,
        contract_id: Optional[str] = None,
        curve: Optional[PreDiZeroCurve] = None,
    ) -> Dict[str, bool]:
        """Verifica formalmente todas as leis de conservação e não-arbitragem do módulo IRS.

        Leis Verificadas:
        1. `irs_period_zero_sum` (LAW-080): Saldo bruto A + Saldo bruto B == 0.00 a cada período.
        2. `irs_cashflow_balanced`: Net_A + Net_B + IRRF == 0.00 a cada período e cumulativo.
        3. `irs_cumulative_zero_sum`: Soma cumulativa dos ajustes brutos == 0.00.
        4. `no_arbitrage_pre_di` (LAW-081): Telescopagem e não-arbitragem na curva Pré/DI.
        """
        targets = [contract_id] if contract_id else list(self._contracts.keys())
        all_period_zero_sum = True
        all_cashflow_balanced = True
        all_cum_zero_sum = True
        no_arbitrage_valid = True

        for cid in targets:
            settlement = self._settlements.get(cid)
            if not settlement:
                settlement = self.settle_contract(cid)

            if not settlement.is_zero_sum_conserved:
                all_cum_zero_sum = False
            if not settlement.is_cashflow_balanced:
                all_cashflow_balanced = False

            for pr in settlement.period_settlements:
                if not pr.is_zero_sum_conserved:
                    all_period_zero_sum = False
                if not pr.is_cashflow_balanced:
                    all_cashflow_balanced = False

        if curve is not None:
            # Verifica não-arbitragem para cada contrato contra a curva
            for cid in targets:
                c = self._contracts[cid]
                periods_du = [p.business_days_du for p in c.periods]
                if not curve.verify_no_arbitrage(periods_du):
                    no_arbitrage_valid = False

        return {
            "irs_period_zero_sum": all_period_zero_sum,
            "irs_cashflow_balanced": all_cashflow_balanced,
            "irs_cumulative_zero_sum": all_cum_zero_sum,
            "no_arbitrage_pre_di": no_arbitrage_valid,
        }

    def portfolio_summary(self) -> Dict[str, Any]:
        """Gera sumário auditável consolidado de toda a carteira de IRS liquidada."""
        if not self._settlements or len(self._settlements) < len(self._contracts):
            self.settle_all()

        results = list(self._settlements.values())
        total_gross_transferred = _quantize_money(
            sum((sum(abs(p.party_a_gross_brl) for p in r.period_settlements) for r in results), Decimal("0.00"))
        )
        total_irrf_withheld = _quantize_money(
            sum((r.cum_total_irrf_brl for r in results), Decimal("0.00"))
        )
        total_resets = sum(r.total_periods for r in results)

        return {
            "total_contracts": len(results),
            "total_reset_events": total_resets,
            "total_gross_cash_settlement_brl": str(total_gross_transferred),
            "total_irrf_withheld_brl": str(total_irrf_withheld),
            "all_zero_sum_conserved": all(r.is_zero_sum_conserved for r in results),
            "all_cashflow_balanced": all(r.is_cashflow_balanced for r in results),
            "settlements": [r.to_dict() for r in results],
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contracts": [c.to_dict() for c in self._contracts.values()],
            "curves": {cid: cv.to_dict() for cid, cv in self._curves.items()},
            "settlements": [s.to_dict() for s in self._settlements.values()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InterestRateSwapEngine:
        engine = cls()
        for c_data in data.get("contracts", []):
            engine.register_contract(InterestRateSwapContract.from_dict(c_data))
        for cid, cv_data in data.get("curves", {}).items():
            engine.register_curve(cid, PreDiZeroCurve.from_dict(cv_data))
        for s_data in data.get("settlements", []):
            s_res = IrsContractSettlementResult.from_dict(s_data)
            engine._settlements[s_res.contract_id] = s_res
        return engine


    @classmethod
    def from_json_fixture(cls, path: Union[str, Path]) -> InterestRateSwapEngine:
        """Carrega e instancia o motor a partir de fixture JSON."""
        fixture_path = Path(path)
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        engine = cls()
        for item in raw.get("curves", []):
            curve = PreDiZeroCurve.from_dict(item)
            engine.register_curve(item.get("curve_id", "CURVA-PRE-DI"), curve)
        for item in raw.get("contracts", []):
            contract = InterestRateSwapContract.from_dict(item)
            engine.register_contract(contract)
        return engine

    def save_json(self, path: Union[str, Path]) -> None:
        """Serializa o estado atual do motor para arquivo JSON."""
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


__all__ = [
    "ArbitrageViolationError",
    "DuplicateIrsContractError",
    "GuaranteeMode",
    "InterestRateSwapContract",
    "InterestRateSwapEngine",
    "IrsContractNotFoundError",
    "IrsContractSettlementResult",
    "IrsError",
    "IrsIndexerType",
    "IrsLegSpec",
    "IrsPeriodSettlementResult",
    "IrsResetPeriod",
    "IrsValidationError",
    "PreDiZeroCurve",
    "ResetFrequency",
    "ZeroCurveVertex",
    "regressive_irrf_rate_for_days",
]
