"""Derivativos de Balcão B3 (NoMe) — Registro e Liquidação de Contratos de Swap e Termo de Moeda NDF (FL-040).

Implementa o motor determinístico de registro escritural, cálculo de fatores acumulados
(CDI, Taxa Pré, Variação Cambial PTAX + Cupom Cambial, IPCA), apuração de ajuste bilateral
soma-zero, retenção tributária na fonte (IRRF exclusivamente sobre resultado líquido positivo)
e liquidação financeira de Contratos de Swap e Termo de Moeda sem Entrega Física (NDF)
na Registradora B3 (NoMe / Balcão B3), em conformidade com a Resolução CMN nº 4.952/2021,
Resolução BCB nº 264/2022 e Instrução Normativa RFB nº 1.585/2015.

Zero dependências externas (100% biblioteca padrão Python).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Constantes de Precisão e Convenções de Mercado B3 / Cetip (NoMe)
# ---------------------------------------------------------------------------

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
EIGHT_PLACES = Decimal("0.00000001")
BUSINESS_DAYS_YEAR = Decimal("252")
CALENDAR_DAYS_COUPON_YEAR = Decimal("360")


def _quantize_money(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda valores monetários em BRL ou moeda estrangeira para 2 casas decimais."""
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _quantize_rate(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda taxas anuais, percentuais e cotações PTAX para 4 casas decimais."""
    return Decimal(str(value)).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def _quantize_factor(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda fatores acumulados de atualização (padrão Cetip/NoMe) para 8 casas decimais."""
    return Decimal(str(value)).quantize(EIGHT_PLACES, rounding=ROUND_HALF_UP)


def _parse_iso_date(value: Union[str, date, datetime]) -> date:
    """Converte string ISO-8601 (YYYY-MM-DD) ou datetime para date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    cleaned = str(value).strip()
    if "T" in cleaned:
        cleaned = cleaned.split("T", 1)[0]
    return date.fromisoformat(cleaned)


def _decimal_power_du252(base: Decimal, du_days: int) -> Decimal:
    """Calcula base^(du_days / 252) com alta precisão decimal (50 dígitos significativos).

    Para expoentes fracionários em Decimal, utiliza exp(ln(base) * (du / 252)) dentro de
    localcontext(prec=50), preservando determinismo absoluto sem imprecisão IEEE 754 float.
    """
    if du_days == 0 or base == Decimal("1"):
        return Decimal("1")
    if du_days == 252:
        return base
    with localcontext() as ctx:
        ctx.prec = 50
        exponent = Decimal(du_days) / BUSINESS_DAYS_YEAR
        return (base.ln() * exponent).exp()


def regressive_irrf_rate_for_days(calendar_days: int) -> Decimal:
    """Retorna a alíquota regressiva de IRRF (IN RFB 1.585/2015) conforme o prazo em dias corridos.

    - Até 180 dias: 22,5% (0.2250)
    - De 181 a 360 dias: 20,0% (0.2000)
    - De 361 a 720 dias: 17,5% (0.1750)
    - Acima de 720 dias: 15,0% (0.1500)
    """
    if calendar_days <= 0:
        raise OtcDerivativeValidationError(
            f"Prazo em dias corridos para tabela regressiva de IRRF deve ser positivo: {calendar_days}"
        )
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


class OtcDerivativeError(Exception):
    """Exceção base para o módulo de Derivativos de Balcão B3 (FL-040)."""


class OtcDerivativeValidationError(OtcDerivativeError):
    """Lançada quando parâmetros contratuais violam regras do Balcão B3 / Res. CMN 4.952."""


class DuplicateOtcContractError(OtcDerivativeError):
    """Lançada ao tentar registrar um contrato ou protocolo NoMe duplicado."""


class OtcContractNotFoundError(OtcDerivativeError):
    """Lançada quando o contrato consultado ou liquidado não consta no registro."""


# ---------------------------------------------------------------------------
# Enumerações Tipadas
# ---------------------------------------------------------------------------


class OtcDerivativeType(str, Enum):
    """Modalidade do instrumento derivativo de balcão registrado no NoMe B3."""

    SWAP = "SWAP"
    NDF = "NDF"

    @classmethod
    def from_str(cls, value: Any) -> OtcDerivativeType:
        if isinstance(value, OtcDerivativeType):
            return value
        normalized = str(value).strip().upper()
        mapping = {
            "SWAP": cls.SWAP,
            "CONTRATO_SWAP": cls.SWAP,
            "NDF": cls.NDF,
            "TERMO_MOEDA": cls.NDF,
            "NON_DELIVERABLE_FORWARD": cls.NDF,
        }
        if normalized in mapping:
            return mapping[normalized]
        raise OtcDerivativeValidationError(
            f"Tipo de derivativo de balcão inválido: {value!r}. Aceitos: SWAP, NDF."
        )


class IndexerType(str, Enum):
    """Indexadores financeiros suportados nas pontas de Contratos de Swap no Balcão B3."""

    CDI = "CDI"
    PRE = "PRE"
    DOLAR = "DOLAR"
    IPCA = "IPCA"

    @classmethod
    def from_str(cls, value: Any) -> IndexerType:
        if isinstance(value, IndexerType):
            return value
        normalized = str(value).strip().upper()
        mapping = {
            "CDI": cls.CDI,
            "DI": cls.CDI,
            "TAXA_DI": cls.CDI,
            "PRE": cls.PRE,
            "PRÉ": cls.PRE,
            "PREFIXADO": cls.PRE,
            "FIXED": cls.PRE,
            "DOLAR": cls.DOLAR,
            "DÓLAR": cls.DOLAR,
            "USD": cls.DOLAR,
            "PTAX": cls.DOLAR,
            "CAMBIO": cls.DOLAR,
            "IPCA": cls.IPCA,
        }
        if normalized in mapping:
            return mapping[normalized]
        raise OtcDerivativeValidationError(
            f"Indexador inválido: {value!r}. Aceitos: CDI, PRE, DOLAR, IPCA."
        )


class NdfDirection(str, Enum):
    """Posição da Parte A no Termo de Moeda (NDF)."""

    BUYER = "BUYER"    # Comprador de moeda estrangeira a termo (ganha se PTAX_final > K)
    SELLER = "SELLER"  # Vendedor de moeda estrangeira a termo (ganha se PTAX_final < K)

    @classmethod
    def from_str(cls, value: Any) -> NdfDirection:
        if isinstance(value, NdfDirection):
            return value
        normalized = str(value).strip().upper()
        mapping = {
            "BUYER": cls.BUYER,
            "BUY": cls.BUYER,
            "COMPRADOR": cls.BUYER,
            "COMPRA": cls.BUYER,
            "LONG": cls.BUYER,
            "SELLER": cls.SELLER,
            "SELL": cls.SELLER,
            "VENDEDOR": cls.SELLER,
            "VENDA": cls.SELLER,
            "SHORT": cls.SELLER,
        }
        if normalized in mapping:
            return mapping[normalized]
        raise OtcDerivativeValidationError(
            f"Direção de NDF inválida: {value!r}. Aceitos: BUYER, SELLER."
        )

    @property
    def opposite(self) -> NdfDirection:
        """Retorna a ponta oposta assumida pela Contraparte (Parte B)."""
        return NdfDirection.SELLER if self == NdfDirection.BUYER else NdfDirection.BUYER


class GuaranteeMode(str, Enum):
    """Modalidade de garantia na Câmara B3 para contratos de balcão (NoMe)."""

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
            "GUARANTEED": cls.COM_GARANTIA_CCP,
            "SEM_GARANTIA_CCP": cls.SEM_GARANTIA_CCP,
            "SEM_GARANTIA": cls.SEM_GARANTIA_CCP,
            "BILATERAL": cls.SEM_GARANTIA_CCP,
            "UNGUARANTEED": cls.SEM_GARANTIA_CCP,
        }
        if normalized in mapping:
            return mapping[normalized]
        raise OtcDerivativeValidationError(
            f"Modalidade de garantia inválida: {value!r}. Aceitos: COM_GARANTIA_CCP, SEM_GARANTIA_CCP."
        )


# ---------------------------------------------------------------------------
# Especificação de Ponta de Swap (SwapLegSpec)
# ---------------------------------------------------------------------------


@dataclass
class SwapLegSpec:
    """Especificação financeira de uma ponta (Ativa ou Passiva) de um Contrato de Swap.

    Atributos:
        indexer: Indexador da ponta (CDI, PRE, DOLAR, IPCA).
        rate_annual_pct:
            - Para PRE: taxa prefixada anual em % a.a. (base DU/252).
            - Para CDI: taxa média anualizada efetiva do DI no período em % a.a. (base DU/252).
            - Para DOLAR: cupom cambial anual em % a.a. (linear DC/360 ou exponencial DU/252).
            - Para IPCA: cupom de juros reais anual em % a.a. (base DU/252).
        cdi_percentage: Percentual do CDI aplicável quando indexer == CDI (ex: 100.0000 para 100% do CDI).
        spread_annual_pct: Spread adicional em % a.a. (base DU/252) para pontas CDI + Spread.
        initial_fx_ptax: Cotação PTAX inicial (data-base de contratação) para ponta DOLAR.
        final_fx_ptax: Cotação PTAX final (D-1 do vencimento) para ponta DOLAR.
        linear_coupon_360: Se True (padrão), capitaliza o cupom cambial da ponta DOLAR de forma linear (DC/360).
        ipca_period_pct: Variação percentual acumulada do IPCA no período para ponta IPCA.
        explicit_accumulated_factor: Caso informado (> 0), sobrepõe o fator acumulado com o valor exato da série Cetip/B3.
    """

    indexer: IndexerType
    rate_annual_pct: Decimal = field(default=Decimal("0.0000"))
    cdi_percentage: Decimal = field(default=Decimal("100.0000"))
    spread_annual_pct: Decimal = field(default=Decimal("0.0000"))
    initial_fx_ptax: Optional[Decimal] = None
    final_fx_ptax: Optional[Decimal] = None
    linear_coupon_360: bool = True
    ipca_period_pct: Decimal = field(default=Decimal("0.0000"))
    explicit_accumulated_factor: Optional[Decimal] = None

    def __post_init__(self) -> None:
        self.indexer = IndexerType.from_str(self.indexer)
        self.rate_annual_pct = _quantize_rate(self.rate_annual_pct)
        self.cdi_percentage = _quantize_rate(self.cdi_percentage)
        self.spread_annual_pct = _quantize_rate(self.spread_annual_pct)
        self.ipca_period_pct = _quantize_rate(self.ipca_period_pct)

        if self.initial_fx_ptax is not None:
            self.initial_fx_ptax = _quantize_rate(self.initial_fx_ptax)
            if self.initial_fx_ptax <= Decimal("0"):
                raise OtcDerivativeValidationError("initial_fx_ptax deve ser estritamente positivo.")
        if self.final_fx_ptax is not None:
            self.final_fx_ptax = _quantize_rate(self.final_fx_ptax)
            if self.final_fx_ptax <= Decimal("0"):
                raise OtcDerivativeValidationError("final_fx_ptax deve ser estritamente positivo.")
        if self.explicit_accumulated_factor is not None:
            self.explicit_accumulated_factor = _quantize_factor(self.explicit_accumulated_factor)
            if self.explicit_accumulated_factor <= Decimal("0"):
                raise OtcDerivativeValidationError(
                    "explicit_accumulated_factor deve ser estritamente positivo."
                )

        if self.indexer == IndexerType.DOLAR and self.explicit_accumulated_factor is None:
            if self.initial_fx_ptax is None or self.final_fx_ptax is None:
                raise OtcDerivativeValidationError(
                    "Ponta indexada a DOLAR exige initial_fx_ptax e final_fx_ptax."
                )
        if self.indexer == IndexerType.CDI and self.cdi_percentage <= Decimal("0"):
            raise OtcDerivativeValidationError(
                "Ponta indexada a CDI exige cdi_percentage positivo (ex: 100.0000)."
            )

    def calculate_factor(self, business_days_du: int, calendar_days_dc: int = 0) -> Decimal:
        """Calcula o Fator Acumulado da ponta ($F_{\\text{leg}}$) com 8 casas decimais.

        Fórmulas:
        - PRE: (1 + r_pre / 100)^(DU / 252)
        - CDI: [1 + ((1 + DI_aa / 100)^(DU / 252) - 1) * (pct_CDI / 100)] * (1 + spread / 100)^(DU / 252)
        - DOLAR: (PTAX_final / PTAX_inicial) * [1 + (cupom / 100) * (DC / 360)] (ou exponencial DU/252)
        - IPCA: (1 + ipca_pct / 100) * (1 + cupom_real / 100)^(DU / 252)
        """
        if self.explicit_accumulated_factor is not None:
            return self.explicit_accumulated_factor

        if business_days_du < 0 or calendar_days_dc < 0:
            raise OtcDerivativeValidationError("Prazo de dias (DU/DC) não pode ser negativo.")

        with localcontext() as ctx:
            ctx.prec = 50
            if self.indexer == IndexerType.PRE:
                base = Decimal("1") + (self.rate_annual_pct / Decimal("100"))
                raw_factor = _decimal_power_du252(base, business_days_du)
                return _quantize_factor(raw_factor)

            if self.indexer == IndexerType.CDI:
                base_di = Decimal("1") + (self.rate_annual_pct / Decimal("100"))
                pure_di_factor = _decimal_power_du252(base_di, business_days_du)
                pct_multiplier = self.cdi_percentage / Decimal("100")
                cdi_factor = Decimal("1") + (pure_di_factor - Decimal("1")) * pct_multiplier
                if self.spread_annual_pct != Decimal("0"):
                    base_spread = Decimal("1") + (self.spread_annual_pct / Decimal("100"))
                    spread_factor = _decimal_power_du252(base_spread, business_days_du)
                    cdi_factor = cdi_factor * spread_factor
                return _quantize_factor(cdi_factor)

            if self.indexer == IndexerType.DOLAR:
                assert self.initial_fx_ptax is not None and self.final_fx_ptax is not None
                fx_ratio = self.final_fx_ptax / self.initial_fx_ptax
                if self.linear_coupon_360 and calendar_days_dc > 0:
                    coupon_factor = Decimal("1") + (
                        (self.rate_annual_pct / Decimal("100"))
                        * (Decimal(calendar_days_dc) / CALENDAR_DAYS_COUPON_YEAR)
                    )
                else:
                    base_coupon = Decimal("1") + (self.rate_annual_pct / Decimal("100"))
                    coupon_factor = _decimal_power_du252(base_coupon, business_days_du)
                return _quantize_factor(fx_ratio * coupon_factor)

            if self.indexer == IndexerType.IPCA:
                inflation_factor = Decimal("1") + (self.ipca_period_pct / Decimal("100"))
                base_real = Decimal("1") + (self.rate_annual_pct / Decimal("100"))
                real_factor = _decimal_power_du252(base_real, business_days_du)
                return _quantize_factor(inflation_factor * real_factor)

        raise OtcDerivativeValidationError(f"Indexador não suportado: {self.indexer}")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SwapLegSpec:
        return cls(
            indexer=IndexerType.from_str(data["indexer"]),
            rate_annual_pct=Decimal(str(data.get("rate_annual_pct", "0.0000"))),
            cdi_percentage=Decimal(str(data.get("cdi_percentage", "100.0000"))),
            spread_annual_pct=Decimal(str(data.get("spread_annual_pct", "0.0000"))),
            initial_fx_ptax=(
                Decimal(str(data["initial_fx_ptax"]))
                if data.get("initial_fx_ptax") is not None
                else None
            ),
            final_fx_ptax=(
                Decimal(str(data["final_fx_ptax"]))
                if data.get("final_fx_ptax") is not None
                else None
            ),
            linear_coupon_360=bool(data.get("linear_coupon_360", True)),
            ipca_period_pct=Decimal(str(data.get("ipca_period_pct", "0.0000"))),
            explicit_accumulated_factor=(
                Decimal(str(data["explicit_accumulated_factor"]))
                if data.get("explicit_accumulated_factor") is not None
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Contrato de Swap de Balcão (OtcSwapContract)
# ---------------------------------------------------------------------------


@dataclass
class OtcSwapContract:
    """Contrato bilateral de Swap registrado no Balcão B3 (NoMe) — Res. CMN 4.952.

    Perspectiva contratual:
    - A Parte A (`party_a`) é titular da Ponta Ativa (`active_leg`, recebe a variação da ponta ativa)
      e devedora da Ponta Passiva (`passive_leg`, paga a variação da ponta passiva).
    - A Parte B (`party_b`) detém a posição simétrica inversa (recebe `passive_leg` e paga `active_leg`).
    """

    contract_id: str
    nome_protocol: str
    trade_date: date
    maturity_date: date
    business_days_du: int
    notional_brl: Decimal
    party_a_name: str
    party_a_document: str
    party_b_name: str
    party_b_document: str
    active_leg: SwapLegSpec
    passive_leg: SwapLegSpec
    calendar_days_dc: int = 0
    party_a_irrf_rate: Decimal = field(default=Decimal("0.0000"))
    party_a_irrf_exempt: bool = False
    party_b_irrf_rate: Decimal = field(default=Decimal("0.0000"))
    party_b_irrf_exempt: bool = False
    guarantee_mode: GuaranteeMode = GuaranteeMode.SEM_GARANTIA_CCP
    is_corporate_hedge: bool = True

    def __post_init__(self) -> None:
        self.trade_date = _parse_iso_date(self.trade_date)
        self.maturity_date = _parse_iso_date(self.maturity_date)
        self.notional_brl = _quantize_money(self.notional_brl)
        self.party_a_irrf_rate = _quantize_rate(self.party_a_irrf_rate)
        self.party_b_irrf_rate = _quantize_rate(self.party_b_irrf_rate)
        self.guarantee_mode = GuaranteeMode.from_str(self.guarantee_mode)

        if not self.contract_id or not self.contract_id.strip():
            raise OtcDerivativeValidationError("contract_id do Swap não pode ser vazio.")
        if not self.nome_protocol or not self.nome_protocol.strip():
            raise OtcDerivativeValidationError("nome_protocol do Swap não pode ser vazio.")
        if self.maturity_date <= self.trade_date:
            raise OtcDerivativeValidationError(
                f"maturity_date ({self.maturity_date}) deve ser posterior a trade_date ({self.trade_date})."
            )
        if self.business_days_du <= 0:
            raise OtcDerivativeValidationError(
                f"business_days_du deve ser positivo: {self.business_days_du}."
            )
        if self.calendar_days_dc <= 0:
            self.calendar_days_dc = (self.maturity_date - self.trade_date).days
        if self.notional_brl <= Decimal("0"):
            raise OtcDerivativeValidationError(
                f"Valor Base (notional_brl) do Swap deve ser estritamente positivo: {self.notional_brl}."
            )
        if not self.party_a_document.strip() or not self.party_b_document.strip():
            raise OtcDerivativeValidationError(
                "Documentos (CNPJ/CPF) das partes A e B são obrigatórios."
            )
        if self.party_a_document.strip() == self.party_b_document.strip():
            raise OtcDerivativeValidationError(
                "Operação consigo mesmo vedada: Parte A e Parte B devem possuir documentos distintos."
            )
        if not (Decimal("0") <= self.party_a_irrf_rate < Decimal("1")):
            raise OtcDerivativeValidationError(
                f"Alíquota de IRRF da Parte A inválida: {self.party_a_irrf_rate}."
            )
        if not (Decimal("0") <= self.party_b_irrf_rate < Decimal("1")):
            raise OtcDerivativeValidationError(
                f"Alíquota de IRRF da Parte B inválida: {self.party_b_irrf_rate}."
            )

    @property
    def derivative_type(self) -> OtcDerivativeType:
        return OtcDerivativeType.SWAP

    @property
    def active_factor(self) -> Decimal:
        """Fator acumulado da Ponta Ativa ($F_{\\text{ativo}}$) com 8 casas decimais."""
        return self.active_leg.calculate_factor(self.business_days_du, self.calendar_days_dc)

    @property
    def passive_factor(self) -> Decimal:
        """Fator acumulado da Ponta Passiva ($F_{\\text{passivo}}$) com 8 casas decimais."""
        return self.passive_leg.calculate_factor(self.business_days_du, self.calendar_days_dc)

    @property
    def active_leg_final_value_brl(self) -> Decimal:
        """Valor atualizado da Ponta Ativa: Notional * F_ativo."""
        return _quantize_money(self.notional_brl * self.active_factor)

    @property
    def passive_leg_final_value_brl(self) -> Decimal:
        """Valor atualizado da Ponta Passiva: Notional * F_passivo."""
        return _quantize_money(self.notional_brl * self.passive_factor)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OtcSwapContract:
        active_leg_data = data["active_leg"]
        passive_leg_data = data["passive_leg"]
        return cls(
            contract_id=str(data["contract_id"]),
            nome_protocol=str(data["nome_protocol"]),
            trade_date=_parse_iso_date(data["trade_date"]),
            maturity_date=_parse_iso_date(data["maturity_date"]),
            business_days_du=int(data["business_days_du"]),
            calendar_days_dc=int(data.get("calendar_days_dc", 0)),
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
                if isinstance(active_leg_data, SwapLegSpec)
                else SwapLegSpec.from_dict(active_leg_data)
            ),
            passive_leg=(
                passive_leg_data
                if isinstance(passive_leg_data, SwapLegSpec)
                else SwapLegSpec.from_dict(passive_leg_data)
            ),
        )


# ---------------------------------------------------------------------------
# Contrato a Termo de Moeda sem Entrega Física — NDF (OtcNdfContract)
# ---------------------------------------------------------------------------


@dataclass
class OtcNdfContract:
    """Contrato a Termo de Moeda sem Entrega Física (NDF) registrado no Balcão B3 (NoMe).

    Fórmula de Ajuste Financeiro em BRL:
    - Para Parte A COMPRADORA (`NdfDirection.BUYER`):
        Ajuste_A = Notional_FX * (S_PTAX - K)
    - Para Parte A VENDEDORA (`NdfDirection.SELLER`):
        Ajuste_A = Notional_FX * (K - S_PTAX)
    - Parte B (Contraparte):
        Ajuste_B = -Ajuste_A
    """

    contract_id: str
    nome_protocol: str
    trade_date: date
    maturity_date: date
    currency: str
    notional_foreign: Decimal
    forward_rate_k: Decimal
    settlement_ptax: Decimal
    party_a_direction: NdfDirection
    party_a_name: str
    party_a_document: str
    party_b_name: str
    party_b_document: str
    calendar_days_dc: int = 0
    party_a_irrf_rate: Decimal = field(default=Decimal("0.0000"))
    party_a_irrf_exempt: bool = False
    party_b_irrf_rate: Decimal = field(default=Decimal("0.0000"))
    party_b_irrf_exempt: bool = False
    guarantee_mode: GuaranteeMode = GuaranteeMode.SEM_GARANTIA_CCP
    is_corporate_hedge: bool = True

    def __post_init__(self) -> None:
        self.trade_date = _parse_iso_date(self.trade_date)
        self.maturity_date = _parse_iso_date(self.maturity_date)
        self.currency = str(self.currency).strip().upper()
        self.notional_foreign = _quantize_money(self.notional_foreign)
        self.forward_rate_k = _quantize_rate(self.forward_rate_k)
        self.settlement_ptax = _quantize_rate(self.settlement_ptax)
        self.party_a_direction = NdfDirection.from_str(self.party_a_direction)
        self.party_a_irrf_rate = _quantize_rate(self.party_a_irrf_rate)
        self.party_b_irrf_rate = _quantize_rate(self.party_b_irrf_rate)
        self.guarantee_mode = GuaranteeMode.from_str(self.guarantee_mode)

        if not self.contract_id or not self.contract_id.strip():
            raise OtcDerivativeValidationError("contract_id do NDF não pode ser vazio.")
        if not self.nome_protocol or not self.nome_protocol.strip():
            raise OtcDerivativeValidationError("nome_protocol do NDF não pode ser vazio.")
        if self.maturity_date <= self.trade_date:
            raise OtcDerivativeValidationError(
                f"maturity_date ({self.maturity_date}) deve ser posterior a trade_date ({self.trade_date})."
            )
        if self.calendar_days_dc <= 0:
            self.calendar_days_dc = (self.maturity_date - self.trade_date).days
        if len(self.currency) != 3:
            raise OtcDerivativeValidationError(
                f"Código ISO de moeda estrangeira inválido no NDF: {self.currency!r}."
            )
        if self.notional_foreign <= Decimal("0"):
            raise OtcDerivativeValidationError(
                f"notional_foreign do NDF deve ser estritamente positivo: {self.notional_foreign}."
            )
        if self.forward_rate_k <= Decimal("0"):
            raise OtcDerivativeValidationError(
                f"Cotação a termo contratada (forward_rate_k) deve ser positiva: {self.forward_rate_k}."
            )
        if self.settlement_ptax <= Decimal("0"):
            raise OtcDerivativeValidationError(
                f"Cotação PTAX de liquidação (settlement_ptax) deve ser positiva: {self.settlement_ptax}."
            )
        if not self.party_a_document.strip() or not self.party_b_document.strip():
            raise OtcDerivativeValidationError(
                "Documentos (CNPJ/CPF) das partes A e B são obrigatórios no NDF."
            )
        if self.party_a_document.strip() == self.party_b_document.strip():
            raise OtcDerivativeValidationError(
                "Operação consigo mesmo vedada: Parte A e Parte B devem possuir documentos distintos."
            )
        if not (Decimal("0") <= self.party_a_irrf_rate < Decimal("1")):
            raise OtcDerivativeValidationError(
                f"Alíquota de IRRF da Parte A inválida: {self.party_a_irrf_rate}."
            )
        if not (Decimal("0") <= self.party_b_irrf_rate < Decimal("1")):
            raise OtcDerivativeValidationError(
                f"Alíquota de IRRF da Parte B inválida: {self.party_b_irrf_rate}."
            )

    @property
    def derivative_type(self) -> OtcDerivativeType:
        return OtcDerivativeType.NDF

    @property
    def party_b_direction(self) -> NdfDirection:
        """Sentido simétrico oposto da Parte B no contrato NDF."""
        return self.party_a_direction.opposite

    @property
    def forward_notional_brl(self) -> Decimal:
        """Valor financeiro contratado a termo em BRL (Notional_FX * K)."""
        return _quantize_money(self.notional_foreign * self.forward_rate_k)

    @property
    def settlement_reference_brl(self) -> Decimal:
        """Valor financeiro de referência na PTAX de fechamento em BRL (Notional_FX * S_PTAX)."""
        return _quantize_money(self.notional_foreign * self.settlement_ptax)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OtcNdfContract:
        return cls(
            contract_id=str(data["contract_id"]),
            nome_protocol=str(data["nome_protocol"]),
            trade_date=_parse_iso_date(data["trade_date"]),
            maturity_date=_parse_iso_date(data["maturity_date"]),
            calendar_days_dc=int(data.get("calendar_days_dc", 0)),
            currency=str(data.get("currency", "USD")),
            notional_foreign=Decimal(str(data["notional_foreign"])),
            forward_rate_k=Decimal(str(data["forward_rate_k"])),
            settlement_ptax=Decimal(str(data["settlement_ptax"])),
            party_a_direction=NdfDirection.from_str(data["party_a_direction"]),
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
        )


# ---------------------------------------------------------------------------
# Resultado Determinístico de Liquidação (OtcSettlementResult)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OtcSettlementResult:
    """Resultado auditável da apuração e liquidação de um Contrato de Swap ou NDF no Balcão B3.

    Invariantes garantidos:
    1. Conservação Bilateral Soma-Zero Bruta:
       `party_a_gross_brl + party_b_gross_brl == Decimal("0.00")`
    2. Incidência de IRRF estritamente sobre ganho líquido positivo:
       - Se `party_a_gross_brl <= 0`, então `party_a_irrf_brl == 0.00`.
       - Se `party_b_gross_brl <= 0`, então `party_b_irrf_brl == 0.00`.
    3. Equilíbrio de Fluxos de Caixa:
       `party_a_net_brl + party_b_net_brl + total_irrf_brl == Decimal("0.00")`
    """

    contract_id: str
    nome_protocol: str
    derivative_type: OtcDerivativeType
    settlement_date: date
    guarantee_mode: GuaranteeMode
    is_corporate_hedge: bool
    notional_reference_brl: Decimal
    active_factor: Optional[Decimal]
    passive_factor: Optional[Decimal]
    forward_rate_k: Optional[Decimal]
    settlement_ptax: Optional[Decimal]
    party_a_name: str
    party_a_document: str
    party_a_gross_brl: Decimal
    party_a_irrf_brl: Decimal
    party_a_net_brl: Decimal
    party_b_name: str
    party_b_document: str
    party_b_gross_brl: Decimal
    party_b_irrf_brl: Decimal
    party_b_net_brl: Decimal
    winning_party_document: Optional[str]
    losing_party_document: Optional[str]

    @property
    def is_zero_sum_conserved(self) -> bool:
        """Verifica se o ajuste bruto é estritamente soma-zero entre Parte A e Parte B."""
        return (self.party_a_gross_brl + self.party_b_gross_brl) == Decimal("0.00")

    @property
    def total_irrf_brl(self) -> Decimal:
        """Soma total de IRRF retido na fonte na operação."""
        return _quantize_money(self.party_a_irrf_brl + self.party_b_irrf_brl)

    @property
    def is_cashflow_balanced(self) -> bool:
        """Verifica se Net_A + Net_B + IRRF_Total == 0.00."""
        return (
            self.party_a_net_brl + self.party_b_net_brl + self.total_irrf_brl
        ) == Decimal("0.00")

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o resultado da liquidação para dicionário JSON-compatível."""
        return {
            "contract_id": self.contract_id,
            "nome_protocol": self.nome_protocol,
            "derivative_type": self.derivative_type.value,
            "settlement_date": self.settlement_date.isoformat(),
            "guarantee_mode": self.guarantee_mode.value,
            "is_corporate_hedge": self.is_corporate_hedge,
            "notional_reference_brl": str(self.notional_reference_brl),
            "active_factor": str(self.active_factor) if self.active_factor is not None else None,
            "passive_factor": str(self.passive_factor) if self.passive_factor is not None else None,
            "forward_rate_k": str(self.forward_rate_k) if self.forward_rate_k is not None else None,
            "settlement_ptax": str(self.settlement_ptax) if self.settlement_ptax is not None else None,
            "party_a_name": self.party_a_name,
            "party_a_document": self.party_a_document,
            "party_a_gross_brl": str(self.party_a_gross_brl),
            "party_a_irrf_brl": str(self.party_a_irrf_brl),
            "party_a_net_brl": str(self.party_a_net_brl),
            "party_b_name": self.party_b_name,
            "party_b_document": self.party_b_document,
            "party_b_gross_brl": str(self.party_b_gross_brl),
            "party_b_irrf_brl": str(self.party_b_irrf_brl),
            "party_b_net_brl": str(self.party_b_net_brl),
            "total_irrf_brl": str(self.total_irrf_brl),
            "winning_party_document": self.winning_party_document,
            "losing_party_document": self.losing_party_document,
            "is_zero_sum_conserved": self.is_zero_sum_conserved,
            "is_cashflow_balanced": self.is_cashflow_balanced,
        }


# ---------------------------------------------------------------------------
# Motor de Registro e Liquidação de Derivativos de Balcão (OtcDerivativesEngine)
# ---------------------------------------------------------------------------


class OtcDerivativesEngine:
    """Motor da Registradora B3 (NoMe) para Contratos de Swap e Termo de Moeda NDF."""

    def __init__(self) -> None:
        self._swaps: Dict[str, OtcSwapContract] = {}
        self._ndfs: Dict[str, OtcNdfContract] = {}
        self._protocols: Dict[str, str] = {}
        self._settlements: Dict[str, OtcSettlementResult] = {}

    @property
    def swap_contracts(self) -> Dict[str, OtcSwapContract]:
        return dict(self._swaps)

    @property
    def ndf_contracts(self) -> Dict[str, OtcNdfContract]:
        return dict(self._ndfs)

    @property
    def settlements(self) -> Dict[str, OtcSettlementResult]:
        return dict(self._settlements)

    def _validate_uniqueness(self, contract_id: str, nome_protocol: str) -> None:
        if contract_id in self._swaps or contract_id in self._ndfs:
            raise DuplicateOtcContractError(
                f"Contrato com contract_id '{contract_id}' já está registrado no NoMe B3."
            )
        if nome_protocol in self._protocols:
            raise DuplicateOtcContractError(
                f"Protocolo NoMe '{nome_protocol}' já utilizado pelo contrato '{self._protocols[nome_protocol]}'."
            )

    def register_swap(self, contract: OtcSwapContract) -> OtcSwapContract:
        """Registra um Contrato de Swap bilateral na Registradora B3 (NoMe)."""
        self._validate_uniqueness(contract.contract_id, contract.nome_protocol)
        self._swaps[contract.contract_id] = contract
        self._protocols[contract.nome_protocol] = contract.contract_id
        return contract

    def register_ndf(self, contract: OtcNdfContract) -> OtcNdfContract:
        """Registra um Contrato a Termo de Moeda (NDF) na Registradora B3 (NoMe)."""
        self._validate_uniqueness(contract.contract_id, contract.nome_protocol)
        self._ndfs[contract.contract_id] = contract
        self._protocols[contract.nome_protocol] = contract.contract_id
        return contract

    @staticmethod
    def _compute_irrf(gross_brl: Decimal, irrf_rate: Decimal, is_exempt: bool) -> Decimal:
        """Calcula o IRRF retido na fonte: incide exclusivamente sobre ganho bruto positivo."""
        if gross_brl <= Decimal("0.00") or is_exempt or irrf_rate <= Decimal("0.0000"):
            return Decimal("0.00")
        return _quantize_money(gross_brl * irrf_rate)

    @classmethod
    def _build_settlement_result(
        cls,
        *,
        contract_id: str,
        nome_protocol: str,
        derivative_type: OtcDerivativeType,
        settlement_date: date,
        guarantee_mode: GuaranteeMode,
        is_corporate_hedge: bool,
        notional_reference_brl: Decimal,
        active_factor: Optional[Decimal],
        passive_factor: Optional[Decimal],
        forward_rate_k: Optional[Decimal],
        settlement_ptax: Optional[Decimal],
        party_a_name: str,
        party_a_document: str,
        party_a_gross_brl: Decimal,
        party_a_irrf_rate: Decimal,
        party_a_irrf_exempt: bool,
        party_b_name: str,
        party_b_document: str,
        party_b_irrf_rate: Decimal,
        party_b_irrf_exempt: bool,
    ) -> OtcSettlementResult:
        party_a_gross = _quantize_money(party_a_gross_brl)
        # Conservação bilateral soma-zero: o ganho/perda bruta da Parte B é o simétrico exato da Parte A
        party_b_gross = _quantize_money(-party_a_gross)

        party_a_irrf = cls._compute_irrf(
            party_a_gross, party_a_irrf_rate, party_a_irrf_exempt
        )
        party_b_irrf = cls._compute_irrf(
            party_b_gross, party_b_irrf_rate, party_b_irrf_exempt
        )

        party_a_net = _quantize_money(party_a_gross - party_a_irrf)
        party_b_net = _quantize_money(party_b_gross - party_b_irrf)

        winning_doc: Optional[str] = None
        losing_doc: Optional[str] = None
        if party_a_gross > Decimal("0.00"):
            winning_doc = party_a_document
            losing_doc = party_b_document
        elif party_b_gross > Decimal("0.00"):
            winning_doc = party_b_document
            losing_doc = party_a_document

        return OtcSettlementResult(
            contract_id=contract_id,
            nome_protocol=nome_protocol,
            derivative_type=derivative_type,
            settlement_date=settlement_date,
            guarantee_mode=guarantee_mode,
            is_corporate_hedge=is_corporate_hedge,
            notional_reference_brl=_quantize_money(notional_reference_brl),
            active_factor=active_factor,
            passive_factor=passive_factor,
            forward_rate_k=forward_rate_k,
            settlement_ptax=settlement_ptax,
            party_a_name=party_a_name,
            party_a_document=party_a_document,
            party_a_gross_brl=party_a_gross,
            party_a_irrf_brl=party_a_irrf,
            party_a_net_brl=party_a_net,
            party_b_name=party_b_name,
            party_b_document=party_b_document,
            party_b_gross_brl=party_b_gross,
            party_b_irrf_brl=party_b_irrf,
            party_b_net_brl=party_b_net,
            winning_party_document=winning_doc,
            losing_party_document=losing_doc,
        )

    def settle_swap(self, contract_or_id: Union[str, OtcSwapContract]) -> OtcSettlementResult:
        """Apura os fatores acumulados ($F_{\\text{ativo}}$, $F_{\\text{passivo}}$), IRRF e ajuste líquido do Swap.

        Fórmula:
            Ajuste_Bruto_A = round_2(Notional * (F_ativo - F_passivo))
            Ajuste_Bruto_B = -Ajuste_Bruto_A
        """
        if isinstance(contract_or_id, OtcSwapContract):
            contract = contract_or_id
            if contract.contract_id not in self._swaps:
                self.register_swap(contract)
        else:
            if contract_or_id not in self._swaps:
                raise OtcContractNotFoundError(
                    f"Contrato de Swap '{contract_or_id}' não encontrado no registro NoMe."
                )
            contract = self._swaps[contract_or_id]

        f_active = contract.active_factor
        f_passive = contract.passive_factor
        gross_a = _quantize_money(contract.notional_brl * (f_active - f_passive))

        result = self._build_settlement_result(
            contract_id=contract.contract_id,
            nome_protocol=contract.nome_protocol,
            derivative_type=OtcDerivativeType.SWAP,
            settlement_date=contract.maturity_date,
            guarantee_mode=contract.guarantee_mode,
            is_corporate_hedge=contract.is_corporate_hedge,
            notional_reference_brl=contract.notional_brl,
            active_factor=f_active,
            passive_factor=f_passive,
            forward_rate_k=None,
            settlement_ptax=None,
            party_a_name=contract.party_a_name,
            party_a_document=contract.party_a_document,
            party_a_gross_brl=gross_a,
            party_a_irrf_rate=contract.party_a_irrf_rate,
            party_a_irrf_exempt=contract.party_a_irrf_exempt,
            party_b_name=contract.party_b_name,
            party_b_document=contract.party_b_document,
            party_b_irrf_rate=contract.party_b_irrf_rate,
            party_b_irrf_exempt=contract.party_b_irrf_exempt,
        )
        self._settlements[contract.contract_id] = result
        return result

    def settle_ndf(self, contract_or_id: Union[str, OtcNdfContract]) -> OtcSettlementResult:
        """Apura o ajuste financeiro em BRL, retenção de IRRF e liquidação líquida de um NDF.

        Fórmula:
            - Se Parte A é BUYER (compradora a termo):
                Ajuste_Bruto_A = round_2(Notional_FX * (S_PTAX - K))
            - Se Parte A é SELLER (vendedora a termo):
                Ajuste_Bruto_A = round_2(Notional_FX * (K - S_PTAX))
            - Ajuste_Bruto_B = -Ajuste_Bruto_A
        """
        if isinstance(contract_or_id, OtcNdfContract):
            contract = contract_or_id
            if contract.contract_id not in self._ndfs:
                self.register_ndf(contract)
        else:
            if contract_or_id not in self._ndfs:
                raise OtcContractNotFoundError(
                    f"Contrato NDF '{contract_or_id}' não encontrado no registro NoMe."
                )
            contract = self._ndfs[contract_or_id]

        if contract.party_a_direction == NdfDirection.BUYER:
            rate_diff = contract.settlement_ptax - contract.forward_rate_k
        else:
            rate_diff = contract.forward_rate_k - contract.settlement_ptax

        gross_a = _quantize_money(contract.notional_foreign * rate_diff)

        result = self._build_settlement_result(
            contract_id=contract.contract_id,
            nome_protocol=contract.nome_protocol,
            derivative_type=OtcDerivativeType.NDF,
            settlement_date=contract.maturity_date,
            guarantee_mode=contract.guarantee_mode,
            is_corporate_hedge=contract.is_corporate_hedge,
            notional_reference_brl=contract.forward_notional_brl,
            active_factor=None,
            passive_factor=None,
            forward_rate_k=contract.forward_rate_k,
            settlement_ptax=contract.settlement_ptax,
            party_a_name=contract.party_a_name,
            party_a_document=contract.party_a_document,
            party_a_gross_brl=gross_a,
            party_a_irrf_rate=contract.party_a_irrf_rate,
            party_a_irrf_exempt=contract.party_a_irrf_exempt,
            party_b_name=contract.party_b_name,
            party_b_document=contract.party_b_document,
            party_b_irrf_rate=contract.party_b_irrf_rate,
            party_b_irrf_exempt=contract.party_b_irrf_exempt,
        )
        self._settlements[contract.contract_id] = result
        return result

    def settle_contract(self, contract_id: str) -> OtcSettlementResult:
        """Liquida qualquer contrato registrado (Swap ou NDF) pelo seu `contract_id`."""
        if contract_id in self._swaps:
            return self.settle_swap(contract_id)
        if contract_id in self._ndfs:
            return self.settle_ndf(contract_id)
        raise OtcContractNotFoundError(
            f"Contrato '{contract_id}' não localizado na Registradora B3 (NoMe)."
        )

    def settle_all(self) -> List[OtcSettlementResult]:
        """Executa a liquidação em lote de todos os Swaps e NDFs registrados."""
        results: List[OtcSettlementResult] = []
        for swap_id in self._swaps:
            results.append(self.settle_swap(swap_id))
        for ndf_id in self._ndfs:
            results.append(self.settle_ndf(ndf_id))
        return results

    def portfolio_summary(self) -> Dict[str, Any]:
        """Gera o resumo consolidado da carteira de derivativos de balcão liquidada."""
        if not self._settlements:
            self.settle_all()

        results = list(self._settlements.values())
        total_gross_volume = _quantize_money(
            sum((abs(r.party_a_gross_brl) for r in results), Decimal("0.00"))
        )
        total_irrf = _quantize_money(
            sum((r.total_irrf_brl for r in results), Decimal("0.00"))
        )
        zero_sum_all = all(r.is_zero_sum_conserved for r in results)
        cashflow_balanced_all = all(r.is_cashflow_balanced for r in results)

        return {
            "total_contracts": len(results),
            "swap_count": len(self._swaps),
            "ndf_count": len(self._ndfs),
            "guaranteed_ccp_count": sum(
                1 for r in results if r.guarantee_mode == GuaranteeMode.COM_GARANTIA_CCP
            ),
            "bilateral_unguarenteed_count": sum(
                1 for r in results if r.guarantee_mode == GuaranteeMode.SEM_GARANTIA_CCP
            ),
            "total_gross_settlement_transfer_brl": str(total_gross_volume),
            "total_irrf_withheld_brl": str(total_irrf),
            "all_zero_sum_conserved": zero_sum_all,
            "all_cashflow_balanced": cashflow_balanced_all,
            "settlements": [r.to_dict() for r in results],
        }

    @classmethod
    def from_json_fixture(cls, path: Union[str, Path]) -> OtcDerivativesEngine:
        """Carrega lote de Contratos de Swap e NDFs a partir de fixture JSON."""
        fixture_path = Path(path)
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        engine = cls()
        for swap_item in raw.get("swap_contracts", []):
            engine.register_swap(OtcSwapContract.from_dict(swap_item))
        for ndf_item in raw.get("ndf_contracts", []):
            engine.register_ndf(OtcNdfContract.from_dict(ndf_item))
        return engine


__all__ = [
    "BUSINESS_DAYS_YEAR",
    "CALENDAR_DAYS_COUPON_YEAR",
    "DuplicateOtcContractError",
    "EIGHT_PLACES",
    "FOUR_PLACES",
    "GuaranteeMode",
    "IndexerType",
    "NdfDirection",
    "OtcContractNotFoundError",
    "OtcDerivativeError",
    "OtcDerivativeType",
    "OtcDerivativeValidationError",
    "OtcDerivativesEngine",
    "OtcNdfContract",
    "OtcSettlementResult",
    "OtcSwapContract",
    "SwapLegSpec",
    "TWO_PLACES",
    "regressive_irrf_rate_for_days",
]
