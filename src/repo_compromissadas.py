"""Módulo de Registro, Batimento e Ciclo de Vida de Operações Compromissadas B3 (FL-023).

Este módulo implementa o modelo de domínio, validações de integridade, motor de
batimento bilateral (Matching Engine), regras de custódia (com ou sem livre movimentação)
e motor de liquidação determinístico para Operações Compromissadas (Repo / Reverse Repo)
de Renda Fixa Pública (Selic/Cetip) e Renda Fixa Privada (NoMe B3).

Convenção de cálculo de juros: DU/252 (dias úteis sobre ano padrão de 252 dias).
Utiliza estritamente a biblioteca padrão do Python (sem dependências externas).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Exceções de Domínio de Operações Compromissadas
# ---------------------------------------------------------------------------

class RepoError(Exception):
    """Exceção base para operações compromissadas na B3."""
    pass


class RepoValidationError(RepoError):
    """Erro de validação de integridade ou violação de parâmetros contratuais."""
    pass


class RepoBilateralDivergenceError(RepoValidationError):
    """Divergência de atributos no batimento bilateral entre comprador e vendedor."""
    pass


class RepoInsufficientCollateralError(RepoValidationError):
    """Saldo de custódia insuficiente para honrar a entrega ou devolução do lastro."""
    pass


class RepoSettlementError(RepoError):
    """Erro durante o processo de liquidação financeira ou física (D0 ou D+N)."""
    pass


class RepoDefaultError(RepoSettlementError):
    """Inadimplência contratual de uma das partes na perna de volta."""
    pass


class RepoStateError(RepoError):
    """Transição inválida na máquina de estados da operação compromissada."""
    pass


# ---------------------------------------------------------------------------
# Constantes Numéricas de Precisão Cetip / B3
# ---------------------------------------------------------------------------

DECIMAL_PU_PRECISION = Decimal("0.00000001")      # 8 casas decimais para Preço Unitário
DECIMAL_MONEY_PRECISION = Decimal("0.01")          # 2 casas decimais para Valor Financeiro
DECIMAL_FACTOR_PRECISION = Decimal("0.00000001")   # 8 casas decimais para Fator Acumulado
DAYS_PER_YEAR_WORKDAYS = Decimal("252")


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------

class RepoModality(str, Enum):
    """Modalidades de operações compromissadas aceitas no Selic e NoMe B3."""
    PUBLIC_OVERNIGHT = "PUBLIC_OVERNIGHT"
    PUBLIC_TERM = "PUBLIC_TERM"
    PRIVATE_OVERNIGHT = "PRIVATE_OVERNIGHT"
    PRIVATE_TERM = "PRIVATE_TERM"

    @classmethod
    def from_str(cls, value: Any) -> RepoModality:
        """Converte e normaliza variações textuais para RepoModality."""
        if isinstance(value, cls):
            return value
        if not value:
            raise RepoValidationError("Modalidade de compromissada não pode ser vazia.")
        normalized = str(value).strip().upper()
        mapping = {
            "PUBLIC_OVERNIGHT": cls.PUBLIC_OVERNIGHT,
            "PUBLIC_TERM": cls.PUBLIC_TERM,
            "PRIVATE_OVERNIGHT": cls.PRIVATE_OVERNIGHT,
            "PRIVATE_TERM": cls.PRIVATE_TERM,
            "OVERNIGHT_PUBLICO": cls.PUBLIC_OVERNIGHT,
            "A_PRAZO_PUBLICO": cls.PUBLIC_TERM,
            "OVERNIGHT_PRIVADO": cls.PRIVATE_OVERNIGHT,
            "A_PRAZO_PRIVADO": cls.PRIVATE_TERM,
        }
        if normalized in mapping:
            return mapping[normalized]
        valid = ", ".join(m.value for m in cls)
        raise RepoValidationError(
            f"Modalidade inválida: {value!r}. Valores permitidos: {valid}"
        )

    @property
    def is_public(self) -> bool:
        return self in (RepoModality.PUBLIC_OVERNIGHT, RepoModality.PUBLIC_TERM)

    @property
    def is_overnight(self) -> bool:
        return self in (RepoModality.PUBLIC_OVERNIGHT, RepoModality.PRIVATE_OVERNIGHT)


class CollateralRight(str, Enum):
    """Direito de movimentação sobre o lastro da compromissada."""
    COM_LIVRE_MOVIMENTACAO = "COM_LIVRE_MOVIMENTACAO"
    SEM_LIVRE_MOVIMENTACAO = "SEM_LIVRE_MOVIMENTACAO"

    @classmethod
    def from_str(cls, value: Any) -> CollateralRight:
        """Converte e normaliza variações textuais para CollateralRight."""
        if isinstance(value, cls):
            return value
        if not value:
            raise RepoValidationError("Direito sobre lastro não pode ser vazio.")
        normalized = str(value).strip().upper()
        mapping = {
            "COM_LIVRE_MOVIMENTACAO": cls.COM_LIVRE_MOVIMENTACAO,
            "SEM_LIVRE_MOVIMENTACAO": cls.SEM_LIVRE_MOVIMENTACAO,
            "LIVRE": cls.COM_LIVRE_MOVIMENTACAO,
            "RETIDO": cls.SEM_LIVRE_MOVIMENTACAO,
            "BLOQUEADO": cls.SEM_LIVRE_MOVIMENTACAO,
            "TRUE": cls.COM_LIVRE_MOVIMENTACAO,
            "FALSE": cls.SEM_LIVRE_MOVIMENTACAO,
        }
        if normalized in mapping:
            return mapping[normalized]
        valid = ", ".join(c.value for c in cls)
        raise RepoValidationError(
            f"Direito sobre lastro inválido: {value!r}. Valores permitidos: {valid}"
        )


class RepoSide(str, Enum):
    """Sentido da ponta negociadora na compromissada."""
    VENDA_RECOMPRA = "VENDA_RECOMPRA"    # Repo: Cedente / Tomador de Recursos / Doador do Lastro
    COMPRA_REVENDA = "COMPRA_REVENDA"    # Reverse Repo: Cessionário / Doador de Recursos / Tomador do Lastro

    @classmethod
    def from_str(cls, value: Any) -> RepoSide:
        if isinstance(value, cls):
            return value
        if not value:
            raise RepoValidationError("Sentido da operação não pode ser vazio.")
        normalized = str(value).strip().upper()
        mapping = {
            "VENDA_RECOMPRA": cls.VENDA_RECOMPRA,
            "COMPRA_REVENDA": cls.COMPRA_REVENDA,
            "REPO": cls.VENDA_RECOMPRA,
            "REVERSE_REPO": cls.COMPRA_REVENDA,
            "VENDA": cls.VENDA_RECOMPRA,
            "COMPRA": cls.COMPRA_REVENDA,
            "SELL_REPO": cls.VENDA_RECOMPRA,
            "BUY_REPO": cls.COMPRA_REVENDA,
        }
        if normalized in mapping:
            return mapping[normalized]
        valid = ", ".join(s.value for s in cls)
        raise RepoValidationError(
            f"Sentido de compromissada inválido: {value!r}. Valores permitidos: {valid}"
        )


class RateType(str, Enum):
    """Regime de remuneração da taxa compromissada."""
    PRE = "PRE"                  # Taxa pré-fixada (% a.a., base DU/252)
    CDI_PERCENT = "CDI_PERCENT"  # Percentual da Taxa CDI (ex: 100.00% do CDI)
    CDI_SPREAD = "CDI_SPREAD"    # CDI + spread em taxa pré (% a.a., base DU/252)

    @classmethod
    def from_str(cls, value: Any) -> RateType:
        if isinstance(value, cls):
            return value
        if not value:
            raise RepoValidationError("Tipo de taxa não pode ser vazio.")
        normalized = str(value).strip().upper()
        mapping = {
            "PRE": cls.PRE,
            "PREFIXADO": cls.PRE,
            "CDI_PERCENT": cls.CDI_PERCENT,
            "DI": cls.CDI_PERCENT,
            "CDI": cls.CDI_PERCENT,
            "CDI_SPREAD": cls.CDI_SPREAD,
            "DI_SPREAD": cls.CDI_SPREAD,
        }
        if normalized in mapping:
            return mapping[normalized]
        valid = ", ".join(r.value for r in cls)
        raise RepoValidationError(
            f"Tipo de taxa inválido: {value!r}. Valores permitidos: {valid}"
        )


class CollateralAssetType(str, Enum):
    """Instrumentos aceitos como lastro em operações compromissadas."""
    # Títulos Públicos Federais (TPF)
    LFT = "LFT"
    LTN = "LTN"
    NTNF = "NTNF"
    NTNB = "NTNB"
    # Títulos Privados NoMe B3
    CDB = "CDB"
    DEBENTURE = "DEBENTURE"
    LF = "LF"
    CRI = "CRI"
    CRA = "CRA"
    LCI = "LCI"
    LCA = "LCA"

    @classmethod
    def from_str(cls, value: Any) -> CollateralAssetType:
        if isinstance(value, cls):
            return value
        if not value:
            raise RepoValidationError("Tipo de ativo de lastro não pode ser vazio.")
        normalized = str(value).strip().upper().replace("-", "").replace("_", "")
        mapping = {
            "LFT": cls.LFT,
            "LTN": cls.LTN,
            "NTNF": cls.NTNF,
            "NTNB": cls.NTNB,
            "CDB": cls.CDB,
            "DEBENTURE": cls.DEBENTURE,
            "DEB": cls.DEBENTURE,
            "LF": cls.LF,
            "CRI": cls.CRI,
            "CRA": cls.CRA,
            "LCI": cls.LCI,
            "LCA": cls.LCA,
        }
        if normalized in mapping:
            return mapping[normalized]
        valid = ", ".join(a.value for a in cls)
        raise RepoValidationError(
            f"Tipo de ativo de lastro inválido: {value!r}. Valores permitidos: {valid}"
        )

    @property
    def is_public(self) -> bool:
        """Indica se é título público federal (Selic)."""
        return self in (
            CollateralAssetType.LFT,
            CollateralAssetType.LTN,
            CollateralAssetType.NTNF,
            CollateralAssetType.NTNB,
        )


class RepoStatus(str, Enum):
    """Status do ciclo de vida da operação compromissada."""
    PENDING_MATCH = "PENDING_MATCH"    # Registrada unilateralmente, aguardando aceite da contraparte
    MATCHED = "MATCHED"                # Casada bilateralmente com sucesso
    LEG1_SETTLED = "LEG1_SETTLED"      # Perna de ida liquidada (D0 concluído, operação ativa)
    LEG2_SETTLED = "LEG2_SETTLED"      # Perna de volta liquidada (D+N concluído com sucesso)
    REJECTED = "REJECTED"              # Rejeitada por divergência ou lastro insuficiente
    CANCELLED = "CANCELLED"            # Cancelada antes da liquidação de ida
    DEFAULTED = "DEFAULTED"            # Inadimplência na perna de volta


# ---------------------------------------------------------------------------
# Funções Financeiras e Matemáticas DU/252 (Padrão Cetip / B3)
# ---------------------------------------------------------------------------

def calculate_pu_volta_pre(
    pu_ida: Union[Decimal, str, float],
    rate_annual: Union[Decimal, str, float],
    business_days: int,
) -> Decimal:
    """Calcula o PU de volta de compromissada pré-fixada na convenção DU/252.

    Equação:
        Fator = (1 + Taxa_aa / 100) ^ (DU / 252)
        PU_volta = round(PU_ida * Fator, 8)
    """
    pu_d = Decimal(str(pu_ida))
    rate_d = Decimal(str(rate_annual))
    if business_days < 0:
        raise RepoValidationError(f"Dias úteis não podem ser negativos: {business_days}")
    if rate_d < Decimal("-100"):
        raise RepoValidationError(f"Taxa anual não pode ser inferior a -100%: {rate_d}")

    # Cálculo do fator com precisão matemática elevada e conversão para Decimal
    rate_factor = float(Decimal("1") + (rate_d / Decimal("100")))
    exp = float(business_days) / float(DAYS_PER_YEAR_WORKDAYS)
    factor_float = pow(rate_factor, exp)
    factor_dec = Decimal(f"{factor_float:.16f}")

    pu_volta = (pu_d * factor_dec).quantize(DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP)
    return pu_volta


def calculate_pu_volta_cdi_projected(
    pu_ida: Union[Decimal, str, float],
    cdi_percentage: Union[Decimal, str, float],
    annual_cdi_rate: Union[Decimal, str, float],
    business_days: int,
) -> Decimal:
    """Calcula o PU de volta projetado para compromissada a % do CDI com taxa anual constante.

    Segue estritamente as regras de arredondamento Cetip/B3:
        1. FDI = round((1 + CDI_aa / 100) ^ (1/252), 8)
        2. Fator_diario = round(1 + (FDI - 1) * (cdi_percent / 100), 8)
        3. Fator_acum = round(Fator_diario ^ DU, 8)
        4. PU_volta = round(PU_ida * Fator_acum, 8)
    """
    pu_d = Decimal(str(pu_ida))
    p_cdi = Decimal(str(cdi_percentage))
    cdi_aa = Decimal(str(annual_cdi_rate))
    if business_days < 0:
        raise RepoValidationError(f"Dias úteis não podem ser negativos: {business_days}")

    # 1. Fator diário do CDI anualizado (8 casas)
    fdi_float = pow(1.0 + float(cdi_aa) / 100.0, 1.0 / 252.0)
    fdi = Decimal(f"{fdi_float:.8f}")

    # 2. Fator diário remunerado ponderado pelo % do CDI (8 casas)
    taxa_diaria = fdi - Decimal("1")
    fator_k = (Decimal("1") + (taxa_diaria * p_cdi / Decimal("100"))).quantize(
        DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP
    )

    # 3. Fator acumulado para DU dias úteis
    fator_acum = Decimal("1.0000000000000000")
    for _ in range(business_days):
        fator_acum = (fator_acum * fator_k)

    fator_acum_8 = fator_acum.quantize(DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP)

    # 4. PU de volta
    pu_volta = (pu_d * fator_acum_8).quantize(DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP)
    return pu_volta


def calculate_pu_volta_cdi_effective(
    pu_ida: Union[Decimal, str, float],
    cdi_percentage: Union[Decimal, str, float],
    daily_cdi_annual_rates: Sequence[Union[Decimal, str, float]],
) -> Decimal:
    """Calcula o PU de volta efetivo com base em série diária observada de taxas CDI anualizadas."""
    pu_d = Decimal(str(pu_ida))
    p_cdi = Decimal(str(cdi_percentage))

    fator_acum = Decimal("1.0000000000000000")
    for rate in daily_cdi_annual_rates:
        rate_d = Decimal(str(rate))
        fdi_float = pow(1.0 + float(rate_d) / 100.0, 1.0 / 252.0)
        fdi = Decimal(f"{fdi_float:.8f}")
        taxa_diaria = fdi - Decimal("1")
        fator_k = (Decimal("1") + (taxa_diaria * p_cdi / Decimal("100"))).quantize(
            DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP
        )
        fator_acum = (fator_acum * fator_k)

    fator_acum_8 = fator_acum.quantize(DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP)
    pu_volta = (pu_d * fator_acum_8).quantize(DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP)
    return pu_volta


def calculate_pu_volta_cdi_spread(
    pu_ida: Union[Decimal, str, float],
    cdi_percentage: Union[Decimal, str, float],
    annual_cdi_rate: Union[Decimal, str, float],
    spread_annual: Union[Decimal, str, float],
    business_days: int,
) -> Decimal:
    """Calcula o PU de volta para compromissada CDI + spread a.a."""
    pu_d = Decimal(str(pu_ida))
    p_cdi = Decimal(str(cdi_percentage))
    cdi_aa = Decimal(str(annual_cdi_rate))
    spread_d = Decimal(str(spread_annual))

    # Fator CDI
    fdi_float = pow(1.0 + float(cdi_aa) / 100.0, 1.0 / 252.0)
    fdi = Decimal(f"{fdi_float:.8f}")
    taxa_diaria = fdi - Decimal("1")
    fator_k = (Decimal("1") + (taxa_diaria * p_cdi / Decimal("100"))).quantize(
        DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP
    )
    fator_cdi_acum = Decimal("1.0000000000000000")
    for _ in range(business_days):
        fator_cdi_acum = (fator_cdi_acum * fator_k)
    fator_cdi_8 = fator_cdi_acum.quantize(DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP)

    # Fator Spread
    spread_factor_float = pow(1.0 + float(spread_d) / 100.0, float(business_days) / 252.0)
    fator_spread_8 = Decimal(f"{spread_factor_float:.8f}").quantize(
        DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP
    )

    fator_total = (fator_cdi_8 * fator_spread_8).quantize(
        DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP
    )
    pu_volta = (pu_d * fator_total).quantize(DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP)
    return pu_volta


# ---------------------------------------------------------------------------
# Dataclasses de Domínio
# ---------------------------------------------------------------------------

@dataclass
class RepoParticipant:
    """Identificação cadastral e de custódia de um participante da operação."""
    participant_id: str
    custody_account: str
    settlement_account: Optional[str] = None
    name: Optional[str] = None

    def __post_init__(self) -> None:
        self.participant_id = str(self.participant_id).strip()
        self.custody_account = str(self.custody_account).strip()
        if not self.participant_id:
            raise RepoValidationError("ID do participante não pode ser vazio.")
        if not self.custody_account:
            raise RepoValidationError("Conta de custódia não pode ser vazia.")


@dataclass
class RepoCollateral:
    """Ativo de lastro oferecido na operação compromissada."""
    asset_code: str
    asset_type: CollateralAssetType
    issuer: str
    maturity_date: date
    is_public: bool
    description: Optional[str] = None

    def __post_init__(self) -> None:
        self.asset_code = str(self.asset_code).strip().upper()
        if not isinstance(self.asset_type, CollateralAssetType):
            self.asset_type = CollateralAssetType.from_str(self.asset_type)
        if isinstance(self.maturity_date, str):
            self.maturity_date = date.fromisoformat(self.maturity_date)
        self.is_public = bool(self.is_public)


@dataclass
class RepoRate:
    """Estrutura da taxa de remuneração da operação compromissada."""
    rate_type: RateType
    rate_value: Decimal
    spread: Decimal = field(default_factory=lambda: Decimal("0.0"))
    benchmark_annual_rate: Optional[Decimal] = None

    def __post_init__(self) -> None:
        if not isinstance(self.rate_type, RateType):
            self.rate_type = RateType.from_str(self.rate_type)
        self.rate_value = Decimal(str(self.rate_value))
        self.spread = Decimal(str(self.spread))
        if self.benchmark_annual_rate is not None:
            self.benchmark_annual_rate = Decimal(str(self.benchmark_annual_rate))


@dataclass
class RepoOrderProposal:
    """Proposta ou ordem unilateral submetida por um participante para matching."""
    order_id: str
    side: RepoSide
    participant: RepoParticipant
    counterparty_id: str
    modality: RepoModality
    collateral_right: CollateralRight
    asset_code: str
    asset_type: CollateralAssetType
    quantity: int
    trade_date: date
    settlement_date_ida: date
    settlement_date_volta: date
    business_days: int
    unit_price_ida: Decimal
    rate_type: RateType
    rate_value: Decimal
    spread: Decimal = field(default_factory=lambda: Decimal("0.0"))
    benchmark_annual_rate: Optional[Decimal] = None
    unit_price_volta: Optional[Decimal] = None
    financial_amount_ida: Optional[Decimal] = None
    financial_amount_volta: Optional[Decimal] = None

    def __post_init__(self) -> None:
        self.order_id = str(self.order_id).strip()
        if not isinstance(self.side, RepoSide):
            self.side = RepoSide.from_str(self.side)
        if not isinstance(self.modality, RepoModality):
            self.modality = RepoModality.from_str(self.modality)
        if not isinstance(self.collateral_right, CollateralRight):
            self.collateral_right = CollateralRight.from_str(self.collateral_right)
        if not isinstance(self.asset_type, CollateralAssetType):
            self.asset_type = CollateralAssetType.from_str(self.asset_type)
        if not isinstance(self.rate_type, RateType):
            self.rate_type = RateType.from_str(self.rate_type)

        if isinstance(self.trade_date, str):
            self.trade_date = date.fromisoformat(self.trade_date)
        if isinstance(self.settlement_date_ida, str):
            self.settlement_date_ida = date.fromisoformat(self.settlement_date_ida)
        if isinstance(self.settlement_date_volta, str):
            self.settlement_date_volta = date.fromisoformat(self.settlement_date_volta)

        self.quantity = int(self.quantity)
        if self.quantity <= 0:
            raise RepoValidationError(f"Quantidade deve ser estritamente positiva: {self.quantity}")

        self.unit_price_ida = Decimal(str(self.unit_price_ida)).quantize(
            DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP
        )
        self.rate_value = Decimal(str(self.rate_value))
        self.spread = Decimal(str(self.spread))

        if self.benchmark_annual_rate is not None:
            self.benchmark_annual_rate = Decimal(str(self.benchmark_annual_rate))

        if self.unit_price_volta is not None:
            self.unit_price_volta = Decimal(str(self.unit_price_volta)).quantize(
                DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP
            )

        if self.financial_amount_ida is None:
            self.financial_amount_ida = (
                Decimal(self.quantity) * self.unit_price_ida
            ).quantize(DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP)
        else:
            self.financial_amount_ida = Decimal(str(self.financial_amount_ida)).quantize(
                DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
            )

        if self.financial_amount_volta is None and self.unit_price_volta is not None:
            self.financial_amount_volta = (
                Decimal(self.quantity) * self.unit_price_volta
            ).quantize(DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP)


@dataclass
class RepoTrade:
    """Contrato e registro oficial de uma operação compromissada na B3."""
    trade_id: str
    modality: RepoModality
    collateral_right: CollateralRight
    collateral: RepoCollateral
    quantity: int
    trade_date: date
    settlement_date_ida: date
    settlement_date_volta: date
    business_days: int
    seller: RepoParticipant      # Cedente (Recompra)
    buyer: RepoParticipant       # Cessionário (Revenda)
    rate: RepoRate
    unit_price_ida: Decimal
    financial_amount_ida: Decimal
    unit_price_volta: Decimal
    financial_amount_volta: Decimal
    status: RepoStatus = RepoStatus.PENDING_MATCH
    rejection_reason: Optional[str] = None

    def __post_init__(self) -> None:
        self.trade_id = str(self.trade_id).strip()
        if not isinstance(self.modality, RepoModality):
            self.modality = RepoModality.from_str(self.modality)
        if not isinstance(self.collateral_right, CollateralRight):
            self.collateral_right = CollateralRight.from_str(self.collateral_right)
        if not isinstance(self.status, RepoStatus):
            self.status = RepoStatus(self.status)

        if isinstance(self.trade_date, str):
            self.trade_date = date.fromisoformat(self.trade_date)
        if isinstance(self.settlement_date_ida, str):
            self.settlement_date_ida = date.fromisoformat(self.settlement_date_ida)
        if isinstance(self.settlement_date_volta, str):
            self.settlement_date_volta = date.fromisoformat(self.settlement_date_volta)

        self.quantity = int(self.quantity)
        if self.quantity <= 0:
            raise RepoValidationError(f"Quantidade deve ser estritamente positiva: {self.quantity}")

        self.unit_price_ida = Decimal(str(self.unit_price_ida)).quantize(
            DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP
        )
        self.financial_amount_ida = Decimal(str(self.financial_amount_ida)).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        self.unit_price_volta = Decimal(str(self.unit_price_volta)).quantize(
            DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP
        )
        self.financial_amount_volta = Decimal(str(self.financial_amount_volta)).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

    def yield_financial(self) -> Decimal:
        """Retorna o rendimento financeiro bruto da compromissada (Fin_volta - Fin_ida)."""
        return (self.financial_amount_volta - self.financial_amount_ida).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

    def yield_percentage(self) -> Decimal:
        """Retorna a taxa de retorno efetiva percentual no período."""
        if self.financial_amount_ida == Decimal("0"):
            return Decimal("0.0")
        diff = self.financial_amount_volta - self.financial_amount_ida
        rate = (diff / self.financial_amount_ida) * Decimal("100")
        return rate.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a operação para dicionário compatível com JSON."""
        return {
            "trade_id": self.trade_id,
            "modality": self.modality.value,
            "collateral_right": self.collateral_right.value,
            "status": self.status.value,
            "trade_date": self.trade_date.isoformat(),
            "settlement_date_ida": self.settlement_date_ida.isoformat(),
            "settlement_date_volta": self.settlement_date_volta.isoformat(),
            "business_days": self.business_days,
            "collateral": {
                "asset_code": self.collateral.asset_code,
                "asset_type": self.collateral.asset_type.value,
                "issuer": self.collateral.issuer,
                "maturity_date": self.collateral.maturity_date.isoformat(),
                "is_public": self.collateral.is_public,
                "description": self.collateral.description,
            },
            "quantity": self.quantity,
            "unit_price_ida": str(self.unit_price_ida),
            "financial_amount_ida": str(self.financial_amount_ida),
            "rate": {
                "rate_type": self.rate.rate_type.value,
                "rate_value": str(self.rate.rate_value),
                "spread": str(self.rate.spread),
                "benchmark_annual_rate": str(self.rate.benchmark_annual_rate)
                if self.rate.benchmark_annual_rate is not None
                else None,
            },
            "unit_price_volta": str(self.unit_price_volta),
            "financial_amount_volta": str(self.financial_amount_volta),
            "seller": asdict(self.seller),
            "buyer": asdict(self.buyer),
            "rejection_reason": self.rejection_reason,
        }

    def to_json(self, indent: int = 2) -> str:
        """Converte a operação para representação JSON formatada."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RepoTrade:
        """Desserializa e instancia RepoTrade a partir de um dicionário."""
        collateral_data = data.get("collateral", {})
        collateral = RepoCollateral(
            asset_code=collateral_data.get("asset_code", ""),
            asset_type=CollateralAssetType.from_str(collateral_data.get("asset_type")),
            issuer=collateral_data.get("issuer", ""),
            maturity_date=date.fromisoformat(str(collateral_data.get("maturity_date"))),
            is_public=bool(collateral_data.get("is_public", False)),
            description=collateral_data.get("description"),
        )

        seller_data = data.get("seller", {})
        seller = RepoParticipant(
            participant_id=str(seller_data.get("participant_id", "")),
            custody_account=str(seller_data.get("custody_account", "")),
            settlement_account=seller_data.get("settlement_account"),
            name=seller_data.get("name"),
        )

        buyer_data = data.get("buyer", {})
        buyer = RepoParticipant(
            participant_id=str(buyer_data.get("participant_id", "")),
            custody_account=str(buyer_data.get("custody_account", "")),
            settlement_account=buyer_data.get("settlement_account"),
            name=buyer_data.get("name"),
        )

        rate_data = data.get("rate", {})
        rate = RepoRate(
            rate_type=RateType.from_str(rate_data.get("rate_type")),
            rate_value=Decimal(str(rate_data.get("rate_value"))),
            spread=Decimal(str(rate_data.get("spread", "0.0"))),
            benchmark_annual_rate=Decimal(str(rate_data["benchmark_annual_rate"]))
            if rate_data.get("benchmark_annual_rate") is not None
            else None,
        )

        trade = cls(
            trade_id=str(data.get("trade_id", "")),
            modality=RepoModality.from_str(data.get("modality")),
            collateral_right=CollateralRight.from_str(data.get("collateral_right")),
            collateral=collateral,
            quantity=int(data.get("quantity", 0)),
            trade_date=date.fromisoformat(str(data.get("trade_date"))),
            settlement_date_ida=date.fromisoformat(str(data.get("settlement_date_ida"))),
            settlement_date_volta=date.fromisoformat(str(data.get("settlement_date_volta"))),
            business_days=int(data.get("business_days", 0)),
            seller=seller,
            buyer=buyer,
            rate=rate,
            unit_price_ida=Decimal(str(data.get("unit_price_ida"))),
            financial_amount_ida=Decimal(str(data.get("financial_amount_ida"))),
            unit_price_volta=Decimal(str(data.get("unit_price_volta"))),
            financial_amount_volta=Decimal(str(data.get("financial_amount_volta"))),
            status=RepoStatus(data.get("status", RepoStatus.PENDING_MATCH.value)),
            rejection_reason=data.get("rejection_reason"),
        )
        return trade

    @classmethod
    def from_json(cls, json_str: str) -> RepoTrade:
        """Instancia RepoTrade a partir de uma string JSON."""
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def from_file(cls, file_path: Union[str, Path]) -> RepoTrade:
        """Lê arquivo JSON de disco e instancia RepoTrade."""
        path = Path(file_path)
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


# ---------------------------------------------------------------------------
# Estruturas do Livro de Custódia e Liquidação
# ---------------------------------------------------------------------------

@dataclass
class CustodyPosition:
    """Posição de custódia de um participante para um determinado ativo."""
    account_id: str
    asset_code: str
    available_qty: int = 0
    locked_repo_qty: int = 0

    @property
    def total_qty(self) -> int:
        return self.available_qty + self.locked_repo_qty


@dataclass
class SettlementLegResult:
    """Resultado da liquidação de uma perna (Ida D0 ou Volta D+N)."""
    leg: int
    trade_id: str
    settlement_date: date
    financial_amount: Decimal
    quantity: int
    seller_cash_change: Decimal
    buyer_cash_change: Decimal
    collateral_movement: str
    status_after: RepoStatus
    success: bool = True
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Motor de Ciclo de Vida e Batimento (RepoLifecycleEngine)
# ---------------------------------------------------------------------------

class RepoLifecycleEngine:
    """Motor determinístico de matching, custódia e liquidação de compromissadas."""

    def __init__(self) -> None:
        # Custódia indexada por (custody_account, asset_code)
        self.custody_ledger: Dict[Tuple[str, str], CustodyPosition] = {}
        # Contas financeiras indexadas por account_id
        self.cash_ledger: Dict[str, Decimal] = {}
        # Carteira de operações registradas
        self.trades: Dict[str, RepoTrade] = {}
        # Propostas pendentes de matching por order_id
        self.pending_proposals: Dict[str, RepoOrderProposal] = {}

    # --- Gestão de Contas de Custódia e Caixa ---

    def credit_custody(self, account_id: str, asset_code: str, qty: int) -> None:
        """Adiciona saldo disponível de títulos à conta de custódia."""
        if qty < 0:
            raise RepoValidationError(f"Quantidade a creditar não pode ser negativa: {qty}")
        key = (str(account_id), str(asset_code).upper())
        if key not in self.custody_ledger:
            self.custody_ledger[key] = CustodyPosition(
                account_id=str(account_id), asset_code=str(asset_code).upper(), available_qty=0
            )
        self.custody_ledger[key].available_qty += qty

    def get_custody_position(self, account_id: str, asset_code: str) -> CustodyPosition:
        """Retorna a posição de custódia para conta e ativo."""
        key = (str(account_id), str(asset_code).upper())
        if key not in self.custody_ledger:
            return CustodyPosition(
                account_id=str(account_id), asset_code=str(asset_code).upper(), available_qty=0
            )
        return self.custody_ledger[key]

    def set_cash_balance(self, account_id: str, amount: Union[Decimal, str, float]) -> None:
        """Define o saldo de caixa em conta de liquidação."""
        self.cash_ledger[str(account_id)] = Decimal(str(amount)).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

    def get_cash_balance(self, account_id: str) -> Decimal:
        """Retorna o saldo de caixa da conta."""
        return self.cash_ledger.get(str(account_id), Decimal("0.00"))

    # --- Registro Unilateral de Propostas ---

    def submit_proposal(self, proposal: RepoOrderProposal) -> RepoOrderProposal:
        """Submete uma intenção/proposta de compromissada."""
        self.pending_proposals[proposal.order_id] = proposal
        return proposal

    # --- Batimento Bilateral (Matching Engine) ---

    def match_proposals(
        self,
        proposal_a: RepoOrderProposal,
        proposal_b: RepoOrderProposal,
        trade_id: Optional[str] = None,
    ) -> RepoTrade:
        """Realiza o batimento bilateral estrito entre propostas compradora e vendedora.

        Levanta `RepoBilateralDivergenceError` caso haja incompatibilidade contratual.
        """
        # Identificação de vendedor e comprador
        if proposal_a.side == RepoSide.VENDA_RECOMPRA and proposal_b.side == RepoSide.COMPRA_REVENDA:
            seller_prop, buyer_prop = proposal_a, proposal_b
        elif proposal_a.side == RepoSide.COMPRA_REVENDA and proposal_b.side == RepoSide.VENDA_RECOMPRA:
            seller_prop, buyer_prop = proposal_b, proposal_a
        else:
            raise RepoBilateralDivergenceError(
                f"Batimento impossível: propostas possuem sentidos incompatíveis ({proposal_a.side.value} e {proposal_b.side.value})."
            )

        # 1. Validação de Contas e Contrapartes
        if seller_prop.participant.custody_account == buyer_prop.participant.custody_account:
            raise RepoBilateralDivergenceError(
                f"Contas de custódia idênticas ({seller_prop.participant.custody_account}). Auto-compromissada é vedada."
            )
        if seller_prop.counterparty_id != buyer_prop.participant.participant_id:
            raise RepoBilateralDivergenceError(
                f"Divergência de contraparte no vendedor: esperado {seller_prop.counterparty_id}, comprador é {buyer_prop.participant.participant_id}"
            )
        if buyer_prop.counterparty_id != seller_prop.participant.participant_id:
            raise RepoBilateralDivergenceError(
                f"Divergência de contraparte no comprador: esperado {buyer_prop.counterparty_id}, vendedor é {seller_prop.participant.participant_id}"
            )

        # 2. Ativo e Tipo de Lastro
        if seller_prop.asset_code != buyer_prop.asset_code:
            raise RepoBilateralDivergenceError(
                f"Divergência no código do ativo: vendedor={seller_prop.asset_code}, comprador={buyer_prop.asset_code}"
            )
        if seller_prop.asset_type != buyer_prop.asset_type:
            raise RepoBilateralDivergenceError(
                f"Divergência no tipo do ativo: vendedor={seller_prop.asset_type.value}, comprador={buyer_prop.asset_type.value}"
            )

        # 3. Modalidade e Direito sobre Lastro
        if seller_prop.modality != buyer_prop.modality:
            raise RepoBilateralDivergenceError(
                f"Divergência de modalidade: vendedor={seller_prop.modality.value}, comprador={buyer_prop.modality.value}"
            )
        if seller_prop.collateral_right != buyer_prop.collateral_right:
            raise RepoBilateralDivergenceError(
                f"Divergência no direito sobre o lastro: vendedor={seller_prop.collateral_right.value}, comprador={buyer_prop.collateral_right.value}"
            )

        # 4. Quantidade de Títulos
        if seller_prop.quantity != buyer_prop.quantity:
            raise RepoBilateralDivergenceError(
                f"Divergência de quantidade: vendedor={seller_prop.quantity}, comprador={buyer_prop.quantity}"
            )

        # 5. Datas e Dias Úteis
        if seller_prop.trade_date != buyer_prop.trade_date:
            raise RepoBilateralDivergenceError(
                f"Divergência na data de negociação: vendedor={seller_prop.trade_date}, comprador={buyer_prop.trade_date}"
            )
        if seller_prop.settlement_date_ida != buyer_prop.settlement_date_ida:
            raise RepoBilateralDivergenceError(
                f"Divergência na data de liquidação de ida: vendedor={seller_prop.settlement_date_ida}, comprador={buyer_prop.settlement_date_ida}"
            )
        if seller_prop.settlement_date_volta != buyer_prop.settlement_date_volta:
            raise RepoBilateralDivergenceError(
                f"Divergência na data de liquidação de volta: vendedor={seller_prop.settlement_date_volta}, comprador={buyer_prop.settlement_date_volta}"
            )
        if seller_prop.business_days != buyer_prop.business_days:
            raise RepoBilateralDivergenceError(
                f"Divergência no prazo de dias úteis: vendedor={seller_prop.business_days}, comprador={buyer_prop.business_days}"
            )

        # 6. Preço Unitário de Ida
        if seller_prop.unit_price_ida != buyer_prop.unit_price_ida:
            raise RepoBilateralDivergenceError(
                f"Divergência no PU de ida: vendedor={seller_prop.unit_price_ida}, comprador={buyer_prop.unit_price_ida}"
            )

        # 7. Regime e Taxa Contratada
        if seller_prop.rate_type != buyer_prop.rate_type:
            raise RepoBilateralDivergenceError(
                f"Divergência no regime de remuneração: vendedor={seller_prop.rate_type.value}, comprador={buyer_prop.rate_type.value}"
            )
        if seller_prop.rate_value != buyer_prop.rate_value:
            raise RepoBilateralDivergenceError(
                f"Divergência na taxa pactuada: vendedor={seller_prop.rate_value}, comprador={buyer_prop.rate_value}"
            )
        if seller_prop.spread != buyer_prop.spread:
            raise RepoBilateralDivergenceError(
                f"Divergência no spread contratual: vendedor={seller_prop.spread}, comprador={buyer_prop.spread}"
            )

        # 8. Cálculo de PU de Volta Esperado e Verificação de Consistência
        expected_pu_volta = self._compute_expected_pu_volta(seller_prop)

        if seller_prop.unit_price_volta is not None:
            if abs(seller_prop.unit_price_volta - expected_pu_volta) > Decimal("0.0001"):
                raise RepoBilateralDivergenceError(
                    f"PU de volta do vendedor diverge da equação financeira: informado={seller_prop.unit_price_volta}, esperado={expected_pu_volta}"
                )
        if buyer_prop.unit_price_volta is not None:
            if abs(buyer_prop.unit_price_volta - expected_pu_volta) > Decimal("0.0001"):
                raise RepoBilateralDivergenceError(
                    f"PU de volta do comprador diverge da equação financeira: informado={buyer_prop.unit_price_volta}, esperado={expected_pu_volta}"
                )

        pu_volta_final = expected_pu_volta
        fin_ida = (Decimal(seller_prop.quantity) * seller_prop.unit_price_ida).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        fin_volta = (Decimal(seller_prop.quantity) * pu_volta_final).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

        effective_trade_id = trade_id or f"REPO-{seller_prop.asset_code}-{seller_prop.trade_date.strftime('%Y%m%d')}-001"

        collateral = RepoCollateral(
            asset_code=seller_prop.asset_code,
            asset_type=seller_prop.asset_type,
            issuer="EMISSOR PADRÃO B3",
            maturity_date=seller_prop.settlement_date_volta,
            is_public=seller_prop.asset_type.is_public,
        )

        trade = RepoTrade(
            trade_id=effective_trade_id,
            modality=seller_prop.modality,
            collateral_right=seller_prop.collateral_right,
            collateral=collateral,
            quantity=seller_prop.quantity,
            trade_date=seller_prop.trade_date,
            settlement_date_ida=seller_prop.settlement_date_ida,
            settlement_date_volta=seller_prop.settlement_date_volta,
            business_days=seller_prop.business_days,
            seller=seller_prop.participant,
            buyer=buyer_prop.participant,
            rate=RepoRate(
                rate_type=seller_prop.rate_type,
                rate_value=seller_prop.rate_value,
                spread=seller_prop.spread,
                benchmark_annual_rate=seller_prop.benchmark_annual_rate,
            ),
            unit_price_ida=seller_prop.unit_price_ida,
            financial_amount_ida=fin_ida,
            unit_price_volta=pu_volta_final,
            financial_amount_volta=fin_volta,
            status=RepoStatus.MATCHED,
        )

        self.trades[trade.trade_id] = trade
        # Remove propostas pendentes pareadas
        self.pending_proposals.pop(proposal_a.order_id, None)
        self.pending_proposals.pop(proposal_b.order_id, None)

        return trade

    def _compute_expected_pu_volta(self, prop: RepoOrderProposal) -> Decimal:
        """Calcula o PU de volta esperado conforme o regime de taxa pactuado."""
        if prop.rate_type == RateType.PRE:
            return calculate_pu_volta_pre(prop.unit_price_ida, prop.rate_value, prop.business_days)
        elif prop.rate_type == RateType.CDI_PERCENT:
            annual_cdi = prop.benchmark_annual_rate or Decimal("11.25")
            return calculate_pu_volta_cdi_projected(
                prop.unit_price_ida, prop.rate_value, annual_cdi, prop.business_days
            )
        elif prop.rate_type == RateType.CDI_SPREAD:
            annual_cdi = prop.benchmark_annual_rate or Decimal("11.25")
            return calculate_pu_volta_cdi_spread(
                prop.unit_price_ida,
                Decimal("100.00"),
                annual_cdi,
                prop.spread,
                prop.business_days,
            )
        raise RepoValidationError(f"Tipo de taxa desconhecido: {prop.rate_type}")

    # --- Validação Formal de Integridade Pré-Liquidação ---

    def validate_trade_integrity(self, trade: RepoTrade) -> None:
        """Verifica todas as leis e invariantes formais do contrato de compromissada."""
        # Cronologia
        if trade.settlement_date_ida < trade.trade_date:
            raise RepoValidationError("Data de liquidação de ida anterior à data de negociação.")
        if trade.settlement_date_volta < trade.settlement_date_ida:
            raise RepoValidationError("Data de liquidação de volta anterior à data de ida.")
        if trade.modality.is_overnight and trade.business_days != 1:
            raise RepoValidationError(f"Operação overnight deve ter exatamente 1 dia útil, recebido: {trade.business_days}")
        if not trade.modality.is_overnight and trade.business_days < 1:
            raise RepoValidationError(f"Operação a prazo deve ter no mínimo 1 dia útil: {trade.business_days}")

        # Relação ativo vs modalidade
        if trade.modality.is_public and not trade.collateral.asset_type.is_public:
            raise RepoValidationError(
                f"Modalidade pública incompatível com ativo privado: {trade.collateral.asset_type.value}"
            )
        if not trade.modality.is_public and trade.collateral.asset_type.is_public:
            raise RepoValidationError(
                f"Modalidade privada incompatível com título público: {trade.collateral.asset_type.value}"
            )

        # Fechamento Financeiro
        expected_fin_ida = (Decimal(trade.quantity) * trade.unit_price_ida).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        if trade.financial_amount_ida != expected_fin_ida:
            raise RepoValidationError(
                f"Valor financeiro de ida ({trade.financial_amount_ida}) diverge de Qtd x PU ({expected_fin_ida})"
            )

        expected_fin_volta = (Decimal(trade.quantity) * trade.unit_price_volta).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        if trade.financial_amount_volta != expected_fin_volta:
            raise RepoValidationError(
                f"Valor financeiro de volta ({trade.financial_amount_volta}) diverge de Qtd x PU ({expected_fin_volta})"
            )

    # --- Liquidação da Perna de Ida (D0) ---

    def settle_leg1_d0(self, trade_id: str) -> SettlementLegResult:
        """Executa a liquidação da perna de ida (D0) da operação compromissada.

        Regras:
        1. Estado da operação deve ser `MATCHED`.
        2. Vendedor (cedente) deve ter custódia disponível >= trade.quantity.
        3. Comprador (cessionário) transfere `financial_amount_ida` para o vendedor.
        4. Se `COM_LIVRE_MOVIMENTACAO`: transfere os títulos da custódia do vendedor para o comprador.
        5. Se `SEM_LIVRE_MOVIMENTACAO`: imobiliza/bloqueia os títulos na conta do vendedor.
        6. Transiciona estado para `LEG1_SETTLED`.
        """
        if trade_id not in self.trades:
            raise RepoStateError(f"Operação compromissada {trade_id!r} não encontrada.")

        trade = self.trades[trade_id]
        if trade.status != RepoStatus.MATCHED:
            raise RepoStateError(
                f"Operação {trade_id} não pode ser liquidada na ida a partir do status {trade.status.value}. Esperado MATCHED."
            )

        self.validate_trade_integrity(trade)

        # Verificação do saldo de lastro na custódia do cedente
        seller_pos = self.get_custody_position(
            trade.seller.custody_account, trade.collateral.asset_code
        )
        if seller_pos.available_qty < trade.quantity:
            trade.status = RepoStatus.REJECTED
            trade.rejection_reason = (
                f"Lastro insuficiente na custódia do cedente: disponível {seller_pos.available_qty}, exigido {trade.quantity}"
            )
            raise RepoInsufficientCollateralError(trade.rejection_reason)

        # Execução da Transferência Financeira (Comprador -> Vendedor)
        buyer_cash_acc = trade.buyer.settlement_account or trade.buyer.participant_id
        seller_cash_acc = trade.seller.settlement_account or trade.seller.participant_id

        buyer_curr_cash = self.get_cash_balance(buyer_cash_acc)
        seller_curr_cash = self.get_cash_balance(seller_cash_acc)

        self.set_cash_balance(buyer_cash_acc, buyer_curr_cash - trade.financial_amount_ida)
        self.set_cash_balance(seller_cash_acc, seller_curr_cash + trade.financial_amount_ida)

        # Execução da Movimentação de Custódia
        if trade.collateral_right == CollateralRight.COM_LIVRE_MOVIMENTACAO:
            # Transfere propriedade com livre movimentação para o cessionário
            seller_pos.available_qty -= trade.quantity
            self.credit_custody(
                trade.buyer.custody_account, trade.collateral.asset_code, trade.quantity
            )
            movement_desc = f"Transferência livre de {trade.quantity} títulos da conta {trade.seller.custody_account} para {trade.buyer.custody_account}"
        else:
            # Sem livre movimentação: bloqueia títulos sob penhor na conta do cedente
            seller_pos.available_qty -= trade.quantity
            seller_pos.locked_repo_qty += trade.quantity
            movement_desc = f"Bloqueio de lastro de {trade.quantity} títulos na conta {trade.seller.custody_account} em favor de {trade.buyer.custody_account}"

        trade.status = RepoStatus.LEG1_SETTLED

        return SettlementLegResult(
            leg=1,
            trade_id=trade.trade_id,
            settlement_date=trade.settlement_date_ida,
            financial_amount=trade.financial_amount_ida,
            quantity=trade.quantity,
            seller_cash_change=trade.financial_amount_ida,
            buyer_cash_change=-trade.financial_amount_ida,
            collateral_movement=movement_desc,
            status_after=trade.status,
            success=True,
        )

    # --- Liquidação da Perna de Volta (D+N) ---

    def settle_leg2_dn(
        self,
        trade_id: str,
        effective_cdi_rates: Optional[Sequence[Union[Decimal, str, float]]] = None,
    ) -> SettlementLegResult:
        """Executa a liquidação da perna de volta (D+N) da operação compromissada.

        Regras:
        1. Estado da operação deve ser `LEG1_SETTLED`.
        2. Caso informado `effective_cdi_rates`, recalcula o PU e valor financeiro de volta.
        3. Vendedor (cedente) transfere `financial_amount_volta` para o comprador.
        4. Se `COM_LIVRE_MOVIMENTACAO`: comprador deve possuir custódia disponível >= trade.quantity.
           Transfere os títulos de volta para o vendedor. Falha gera `RepoDefaultError`.
        5. Se `SEM_LIVRE_MOVIMENTACAO`: desbloqueia os títulos bloqueados na conta do vendedor.
        6. Transiciona estado para `LEG2_SETTLED`.
        """
        if trade_id not in self.trades:
            raise RepoStateError(f"Operação compromissada {trade_id!r} não encontrada.")

        trade = self.trades[trade_id]
        if trade.status != RepoStatus.LEG1_SETTLED:
            raise RepoStateError(
                f"Operação {trade_id} não pode ser liquidada na volta a partir do status {trade.status.value}. Esperado LEG1_SETTLED."
            )

        # Recálculo com taxa flutuante efetiva caso fornecida
        if effective_cdi_rates is not None and trade.rate.rate_type == RateType.CDI_PERCENT:
            pu_volta_efetivo = calculate_pu_volta_cdi_effective(
                trade.unit_price_ida, trade.rate.rate_value, effective_cdi_rates
            )
            fin_volta_efetivo = (Decimal(trade.quantity) * pu_volta_efetivo).quantize(
                DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
            )
            trade.unit_price_volta = pu_volta_efetivo
            trade.financial_amount_volta = fin_volta_efetivo

        # Execução da Transferência Financeira de Retorno (Vendedor -> Comprador)
        buyer_cash_acc = trade.buyer.settlement_account or trade.buyer.participant_id
        seller_cash_acc = trade.seller.settlement_account or trade.seller.participant_id

        buyer_curr_cash = self.get_cash_balance(buyer_cash_acc)
        seller_curr_cash = self.get_cash_balance(seller_cash_acc)

        self.set_cash_balance(seller_cash_acc, seller_curr_cash - trade.financial_amount_volta)
        self.set_cash_balance(buyer_cash_acc, buyer_curr_cash + trade.financial_amount_volta)

        # Devolução dos Títulos de Lastro
        seller_pos = self.get_custody_position(
            trade.seller.custody_account, trade.collateral.asset_code
        )

        if trade.collateral_right == CollateralRight.COM_LIVRE_MOVIMENTACAO:
            buyer_pos = self.get_custody_position(
                trade.buyer.custody_account, trade.collateral.asset_code
            )
            if buyer_pos.available_qty < trade.quantity:
                trade.status = RepoStatus.DEFAULTED
                trade.rejection_reason = (
                    f"Inadimplência do cessionário na devolução do lastro: disponível {buyer_pos.available_qty}, exigido {trade.quantity}"
                )
                raise RepoDefaultError(trade.rejection_reason)

            buyer_pos.available_qty -= trade.quantity
            seller_pos.available_qty += trade.quantity
            movement_desc = f"Devolução de {trade.quantity} títulos da conta {trade.buyer.custody_account} para {trade.seller.custody_account}"
        else:
            # Desbloqueio da garantia retida
            seller_pos.locked_repo_qty -= trade.quantity
            seller_pos.available_qty += trade.quantity
            movement_desc = f"Desbloqueio de {trade.quantity} títulos na conta do cedente {trade.seller.custody_account}"

        trade.status = RepoStatus.LEG2_SETTLED

        return SettlementLegResult(
            leg=2,
            trade_id=trade.trade_id,
            settlement_date=trade.settlement_date_volta,
            financial_amount=trade.financial_amount_volta,
            quantity=trade.quantity,
            seller_cash_change=-trade.financial_amount_volta,
            buyer_cash_change=trade.financial_amount_volta,
            collateral_movement=movement_desc,
            status_after=trade.status,
            success=True,
        )
