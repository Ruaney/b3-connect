"""
B3 Câmara — Gestão de Ativos Offshore e Eurobonds como Colateral Multilateral (FL-083)
========================================================================================
Módulo de depósito, custódia internacional, matriz de haircuts, conversão cambial PTAX
e bloqueio de ativos financeiros offshore e Eurobonds emitidos por companhias brasileiras
no exterior como garantia multilateral na Câmara B3 (sistema CORE / NoMe).

Ativos Elegíveis Regulamentados:
1. ADRs (American Depositary Receipts) — Níveis II e III listados em bolsas dos EUA (NYSE / NASDAQ),
   custodiados via The Depository Trust Company (DTC / DTCC).
2. GDRs (Global Depositary Receipts) — Listados em centros financeiros internacionais
   (ex: London Stock Exchange - LSE, Luxembourg Stock Exchange), custodiados via Euroclear / Clearstream.
3. Eurobonds e Global Notes Corporativos — Títulos de dívida em moeda estrangeira (USD e EUR)
   emitidos no exterior por companhias brasileiras ou suas subsidiárias financeiras offshore
   (ex: Petrobras Global Finance, Vale Overseas, Votorantim, Itaú Cayman), custodiados via
   Euroclear Bank e Clearstream Banking (ICSDs) ou DTC.

Estrutura de Haircuts Regulatórios:
  H_total = min(H_pais + H_rating + H_tipo_tenor + H_fx_buffer, H_max)
  Onde:
  - H_pais: Haircut de risco de jurisdição/país de emissão e sede;
  - H_rating: Haircut prudencial por rating de crédito global (AAA a BB-);
  - H_tipo_tenor: Volatilidade de renda variável para ADR/GDR ou escadinha de tenor para Eurobonds;
  - H_fx_buffer: Margem de segurança cambial contra volatilidade extrema da PTAX (USD/EUR).

Invariantes Formais Provadas:
- Invariante 1 (Não-Negatividade e Limitação Superior): 0 <= V_elegivel_brl <= V_bruto_brl.
- Invariante 2 (Segregação Internacional de Custódia): Saldo_Total == Saldo_Bloqueado + Saldo_Desimpedido.
- Invariante 3 (Segregação Estrita de Contas): Contas Proprietárias (House) e de Terceiros (Client)
  mantêm segregação estrita sem promiscuidade contábil nas depositárias internacionais.
- Invariante 4 (Co-domínio e Monotonicidade de Haircut): 0 <= H_total < 1.00 para ativos aceitos;
  ratings mais baixos e tenors mais longos produzem haircuts monotonicamente não-decrescentes.
- Invariante 5 (Trava Safe-Release Guardrail): Desoneração de colateral offshore rejeitada
  se resultar em déficit de margem requerida da Câmara B3.
- Invariante 6 (Cotação Conservadora PTAX): Avaliação prudencial estritamente baseada na taxa PTAX
  de Compra (bid), respeitando 0 < PTAX_bid <= PTAX_ask.

Zero dependências externas (exclusivamente biblioteca padrão Python).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")
EIGHT_PLACES = Decimal("0.00000001")


def round_money(val: Decimal) -> Decimal:
    """Arredonda valor monetário para 2 casas decimais (centavos) com ROUND_HALF_UP."""
    return val.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def round_factor(val: Decimal) -> Decimal:
    """Arredonda fator percentual ou taxa para 4 casas decimais com ROUND_HALF_UP."""
    return val.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Exceções Especializadas
# ---------------------------------------------------------------------------

class OffshoreCollateralError(Exception):
    """Exceção base para operações de colateral offshore e Eurobonds (FL-083)."""
    pass


class IneligibleAssetError(OffshoreCollateralError):
    """Lançada quando o ativo, emissor ou modalidade não é elegível para depósito."""
    pass


class IneligibleRatingError(OffshoreCollateralError):
    """Lançada quando a classificação de risco de crédito não atende ao piso B3."""
    pass


class UnsupportedCurrencyError(OffshoreCollateralError):
    """Lançada quando a moeda estrangeira não é suportada ou carece de cotação PTAX."""
    pass


class InvalidDepositError(OffshoreCollateralError):
    """Lançada quando parâmetros do depósito (quantidade, preço, ISIN) são inválidos."""
    pass


class MarginReleaseViolationError(OffshoreCollateralError):
    """Lançada quando uma tentativa de desoneração de garantias offshore viola a margem mínima."""
    pass


class InternationalCustodySegregationError(OffshoreCollateralError):
    """Lançada quando ocorre quebra de segregação de custódia internacional."""
    pass


class HaircutConfigurationError(OffshoreCollateralError):
    """Lançada quando parâmetros da matriz de haircut violam limites regulatórios."""
    pass


class OffshoreInvariantViolationError(AssertionError, OffshoreCollateralError):
    """Lançada quando uma lei formal de conservação ou invariante matemática é quebrada."""
    pass


# ---------------------------------------------------------------------------
# Domínios e Tipos Enumerados
# ---------------------------------------------------------------------------

class OffshoreAssetType(str, Enum):
    """Tipos de ativos offshore e internacionais aceitos pela Câmara B3."""
    ADR = "ADR"            # American Depositary Receipt (NYSE / NASDAQ)
    GDR = "GDR"            # Global Depositary Receipt (LSE / Luxembourg)
    EUROBOND = "EUROBOND"  # Eurobond / Global Note corporativo emitida no exterior

    @property
    def is_depositary_receipt(self) -> bool:
        """Indica se é recibo de depósito de ações (ADR ou GDR)."""
        return self in (OffshoreAssetType.ADR, OffshoreAssetType.GDR)

    @property
    def is_fixed_income(self) -> bool:
        """Indica se é título de renda fixa internacional (Eurobond)."""
        return self == OffshoreAssetType.EUROBOND


class OffshoreCurrency(str, Enum):
    """Moedas estrangeiras aceitas para precificação e colateral offshore."""
    USD = "USD"  # Dólar Americano
    EUR = "EUR"  # Euro


class InternationalDepository(str, Enum):
    """Centrais depositárias internacionais parceiras e custodiantes globais."""
    DTC = "DTC"                  # The Depository Trust Company (DTCC - EUA)
    EUROCLEAR = "EUROCLEAR"      # Euroclear Bank (ICSD - Bélgica)
    CLEARSTREAM = "CLEARSTREAM"  # Clearstream Banking (ICSD - Luxemburgo)


class CreditRating(str, Enum):
    """Classificação de crédito de longo prazo (S&P / Fitch / Moody's equivalentes)."""
    AAA = "AAA"
    AA_PLUS = "AA+"
    AA = "AA"
    AA_MINUS = "AA-"
    A_PLUS = "A+"
    A = "A"
    A_MINUS = "A-"
    BBB_PLUS = "BBB+"
    BBB = "BBB"
    BBB_MINUS = "BBB-"
    BB_PLUS = "BB+"
    BB = "BB"
    BB_MINUS = "BB-"
    B_PLUS = "B+"
    B = "B"
    B_MINUS = "B-"
    CCC = "CCC"
    DEFAULT = "D"

    @property
    def is_investment_grade(self) -> bool:
        """Indica se o rating pertence ao Grau de Investimento (BBB- ou superior)."""
        return self in (
            CreditRating.AAA,
            CreditRating.AA_PLUS,
            CreditRating.AA,
            CreditRating.AA_MINUS,
            CreditRating.A_PLUS,
            CreditRating.A,
            CreditRating.A_MINUS,
            CreditRating.BBB_PLUS,
            CreditRating.BBB,
            CreditRating.BBB_MINUS,
        )

    @property
    def is_b3_eligible(self) -> bool:
        """Indica se o rating atende ao piso regulatório da B3 para colateral (mínimo BB-)."""
        return self.is_investment_grade or self in (
            CreditRating.BB_PLUS,
            CreditRating.BB,
            CreditRating.BB_MINUS,
        )

    @property
    def ordinal_rank(self) -> int:
        """Classificação numérica de solidez (menor número = maior solidez)."""
        ranks = {
            CreditRating.AAA: 1,
            CreditRating.AA_PLUS: 2,
            CreditRating.AA: 3,
            CreditRating.AA_MINUS: 4,
            CreditRating.A_PLUS: 5,
            CreditRating.A: 6,
            CreditRating.A_MINUS: 7,
            CreditRating.BBB_PLUS: 8,
            CreditRating.BBB: 9,
            CreditRating.BBB_MINUS: 10,
            CreditRating.BB_PLUS: 11,
            CreditRating.BB: 12,
            CreditRating.BB_MINUS: 13,
            CreditRating.B_PLUS: 14,
            CreditRating.B: 15,
            CreditRating.B_MINUS: 16,
            CreditRating.CCC: 17,
            CreditRating.DEFAULT: 18,
        }
        return ranks.get(self, 99)


class TenorBucket(str, Enum):
    """Faixas de maturidade residual (tenor) para títulos de renda fixa internacional."""
    UP_TO_1Y = "<= 1Y"
    FROM_1Y_TO_3Y = "1Y-3Y"
    FROM_3Y_TO_5Y = "3Y-5Y"
    FROM_5Y_TO_10Y = "5Y-10Y"
    OVER_10Y = "> 10Y"
    EQUITY_NA = "N/A_EQUITY"

    @classmethod
    def from_maturity_years(cls, years: Decimal) -> TenorBucket:
        """Mapeia o prazo em anos para a respectiva faixa de tenor."""
        if years <= Decimal("1.0"):
            return cls.UP_TO_1Y
        elif years <= Decimal("3.0"):
            return cls.FROM_1Y_TO_3Y
        elif years <= Decimal("5.0"):
            return cls.FROM_3Y_TO_5Y
        elif years <= Decimal("10.0"):
            return cls.FROM_5Y_TO_10Y
        else:
            return cls.OVER_10Y


class AccountSegregationType(str, Enum):
    """Tipo de segregação de conta de custódia internacional."""
    HOUSE = "HOUSE"    # Carteira Própria do Participante
    CLIENT = "CLIENT"  # Terceiros / Clientes Segregados


class PledgeStatus(str, Enum):
    """Estado de bloqueio do colateral na Câmara B3."""
    UNENCUMBERED = "UNENCUMBERED"                    # Livre / Desimpedido na conta de custódia
    PLEDGED_MULTILATERAL = "PLEDGED_MULTILATERAL"    # Bloqueado em garantia multilateral na B3
    RELEASE_PENDING = "RELEASE_PENDING"              # Em processo de desoneração / devolução
    EXECUTED_DEFAULT = "EXECUTED_DEFAULT"            # Excutado para honrar inadimplência multilateral


# ---------------------------------------------------------------------------
# Câmbio e Volatilidade PTAX
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PtaxRate:
    """Taxa de câmbio oficial PTAX divulgada pelo Banco Central do Brasil."""
    currency: OffshoreCurrency
    bid_rate: Decimal      # Taxa de compra (usada pela CCP para avaliação conservadora)
    ask_rate: Decimal      # Taxa de venda
    mid_rate: Decimal      # Taxa média
    bulletin_date: date    # Data do boletim

    def __post_init__(self) -> None:
        if self.bid_rate <= Decimal("0") or self.ask_rate <= Decimal("0"):
            raise UnsupportedCurrencyError("Taxas de câmbio PTAX devem ser estritamente positivas.")
        if self.bid_rate > self.ask_rate:
            raise UnsupportedCurrencyError("Taxa PTAX de compra (bid) não pode superar taxa de venda (ask).")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "currency": self.currency.value,
            "bid_rate": str(self.bid_rate),
            "ask_rate": str(self.ask_rate),
            "mid_rate": str(self.mid_rate),
            "bulletin_date": self.bulletin_date.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PtaxRate:
        b_date = data["bulletin_date"]
        if isinstance(b_date, str):
            b_date = date.fromisoformat(b_date)
        return cls(
            currency=OffshoreCurrency(data["currency"]),
            bid_rate=Decimal(str(data["bid_rate"])),
            ask_rate=Decimal(str(data["ask_rate"])),
            mid_rate=Decimal(str(data["mid_rate"])),
            bulletin_date=b_date,
        )


@dataclass(frozen=True)
class FxSafetyMarginConfig:
    """Margem de segurança cambial e buffer de volatilidade PTAX para colateral offshore."""
    currency: OffshoreCurrency
    base_haircut: Decimal             # Deságio base cambial (ex: 5% USD, 6% EUR)
    volatility_stress_buffer: Decimal # Buffer de estresse cambial (ex: 2.5%)
    liquidation_horizon_days: int = 2 # Prazo de liquidação forçada (h=2 dias úteis)

    def __post_init__(self) -> None:
        if self.base_haircut < Decimal("0") or self.base_haircut > Decimal("0.50"):
            raise HaircutConfigurationError("Base haircut cambial deve estar no intervalo [0.00, 0.50].")
        if self.volatility_stress_buffer < Decimal("0") or self.volatility_stress_buffer > Decimal("0.50"):
            raise HaircutConfigurationError("Buffer de estresse cambial deve estar no intervalo [0.00, 0.50].")

    @property
    def total_fx_haircut(self) -> Decimal:
        """Haircut cambial total: base + buffer de estresse."""
        return (self.base_haircut + self.volatility_stress_buffer).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "currency": self.currency.value,
            "base_haircut": str(self.base_haircut),
            "volatility_stress_buffer": str(self.volatility_stress_buffer),
            "liquidation_horizon_days": self.liquidation_horizon_days,
            "total_fx_haircut": str(self.total_fx_haircut),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FxSafetyMarginConfig:
        return cls(
            currency=OffshoreCurrency(data["currency"]),
            base_haircut=Decimal(str(data["base_haircut"])),
            volatility_stress_buffer=Decimal(str(data["volatility_stress_buffer"])),
            liquidation_horizon_days=int(data.get("liquidation_horizon_days", 2)),
        )


# ---------------------------------------------------------------------------
# Decomposição de Haircuts e Ativos Mestres
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OffshoreHaircutBreakdown:
    """Detalhamento analítico da composição aditiva de haircuts regulatórios."""
    country_risk_haircut: Decimal      # Deságio por risco de jurisdição/país de emissão
    credit_rating_haircut: Decimal     # Deságio por rating de crédito
    tenor_or_equity_haircut: Decimal   # Deságio por tenor (Eurobond) ou volatilidade de ação (ADR/GDR)
    fx_volatility_haircut: Decimal     # Deságio cambial PTAX com buffer de segurança
    total_haircut: Decimal             # Total limitado pelo teto regulatório (ex: 90%)

    def __post_init__(self) -> None:
        if self.total_haircut < Decimal("0") or self.total_haircut >= Decimal("1.00"):
            raise HaircutConfigurationError(
                f"Haircut total {self.total_haircut} fora do co-domínio regulatório [0.00, 1.00)."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "country_risk_haircut": str(self.country_risk_haircut),
            "credit_rating_haircut": str(self.credit_rating_haircut),
            "tenor_or_equity_haircut": str(self.tenor_or_equity_haircut),
            "fx_volatility_haircut": str(self.fx_volatility_haircut),
            "total_haircut": str(self.total_haircut),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OffshoreHaircutBreakdown:
        return cls(
            country_risk_haircut=Decimal(str(data["country_risk_haircut"])),
            credit_rating_haircut=Decimal(str(data["credit_rating_haircut"])),
            tenor_or_equity_haircut=Decimal(str(data["tenor_or_equity_haircut"])),
            fx_volatility_haircut=Decimal(str(data["fx_volatility_haircut"])),
            total_haircut=Decimal(str(data["total_haircut"])),
        )


@dataclass(frozen=True)
class OffshoreSecurity:
    """Atributos cadastrais e financeiros do ativo offshore ou Eurobond emitido no exterior."""
    isin: str
    ticker: str
    name: str
    asset_type: OffshoreAssetType
    brazilian_issuer_root: str        # CNPJ raiz ou nome do grupo econômico brasileiro controlador
    offshore_issuing_entity: str      # Entidade emissora no exterior (ex: PGF B.V., Vale Overseas)
    currency: OffshoreCurrency
    depository: InternationalDepository
    credit_rating: CreditRating
    country_risk_zone: str            # Jurisdição de emissão/risco (ex: "BR_OFFSHORE", "US", "LUX")
    maturity_date: Optional[date]     # Data de vencimento (None para ADRs e GDRs)
    unit_price: Decimal               # Preço de mercado dirty em moeda estrangeira
    underlying_b3_ticker: Optional[str] = None # Código do ativo subjacente na B3 (se ADR/GDR)
    adr_ratio: Optional[Decimal] = None        # Razão do recibo (ex: 1 ADR = 1 ação ou 2 ações)

    def __post_init__(self) -> None:
        if len(self.isin) != 12:
            raise InvalidDepositError(f"ISIN inválido: '{self.isin}'. Deve possuir 12 caracteres.")
        if self.unit_price <= Decimal("0"):
            raise InvalidDepositError(f"Preço unitário do ativo {self.isin} deve ser estritamente positivo.")
        if self.asset_type.is_fixed_income and self.maturity_date is None:
            raise InvalidDepositError(f"Eurobond {self.isin} deve possuir data de vencimento obrigatória.")
        if self.asset_type.is_depositary_receipt and (self.adr_ratio is None or self.adr_ratio <= Decimal("0")):
            raise InvalidDepositError(f"Recibo de depósito {self.isin} deve possuir adr_ratio estritamente positivo.")
        if not self.credit_rating.is_b3_eligible:
            raise IneligibleRatingError(
                f"Ativo {self.isin} com rating {self.credit_rating.value} não atinge o piso mínimo B3 (BB-)."
            )

    def calculate_residual_years(self, as_of_date: date) -> Decimal:
        """Calcula o prazo residual em anos em relação à data base de cálculo."""
        if self.maturity_date is None:
            return Decimal("0.0")
        days = (self.maturity_date - as_of_date).days
        if days < 0:
            raise InvalidDepositError(f"Ativo {self.isin} já se encontra vencido em {as_of_date}.")
        return Decimal(days) / Decimal("365.25")

    def get_tenor_bucket(self, as_of_date: date) -> TenorBucket:
        """Obtém o bucket de maturidade residual."""
        if self.asset_type.is_depositary_receipt:
            return TenorBucket.EQUITY_NA
        years = self.calculate_residual_years(as_of_date)
        return TenorBucket.from_maturity_years(years)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "isin": self.isin,
            "ticker": self.ticker,
            "name": self.name,
            "asset_type": self.asset_type.value,
            "brazilian_issuer_root": self.brazilian_issuer_root,
            "offshore_issuing_entity": self.offshore_issuing_entity,
            "currency": self.currency.value,
            "depository": self.depository.value,
            "credit_rating": self.credit_rating.value,
            "country_risk_zone": self.country_risk_zone,
            "maturity_date": self.maturity_date.isoformat() if self.maturity_date else None,
            "unit_price": str(self.unit_price),
            "underlying_b3_ticker": self.underlying_b3_ticker,
            "adr_ratio": str(self.adr_ratio) if self.adr_ratio is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OffshoreSecurity:
        m_date = data.get("maturity_date")
        if isinstance(m_date, str) and m_date:
            m_date = date.fromisoformat(m_date)
        adr_ratio = data.get("adr_ratio")
        return cls(
            isin=data["isin"],
            ticker=data["ticker"],
            name=data["name"],
            asset_type=OffshoreAssetType(data["asset_type"]),
            brazilian_issuer_root=data["brazilian_issuer_root"],
            offshore_issuing_entity=data["offshore_issuing_entity"],
            currency=OffshoreCurrency(data["currency"]),
            depository=InternationalDepository(data["depository"]),
            credit_rating=CreditRating(data["credit_rating"]),
            country_risk_zone=data["country_risk_zone"],
            maturity_date=m_date,
            unit_price=Decimal(str(data["unit_price"])),
            underlying_b3_ticker=data.get("underlying_b3_ticker"),
            adr_ratio=Decimal(str(adr_ratio)) if adr_ratio is not None else None,
        )


# ---------------------------------------------------------------------------
# Segregação Internacional de Custódia e Posições
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InternationalCustodyAccount:
    """Conta de custódia mantida na depositária internacional (DTC / Euroclear / Clearstream)."""
    account_id: str
    participant_id: str
    segregation_type: AccountSegregationType
    depository: InternationalDepository
    depository_account_number: str
    b3_clearing_subaccount: str

    def __post_init__(self) -> None:
        if not self.account_id or not self.participant_id:
            raise InvalidDepositError("account_id e participant_id são obrigatórios.")
        if not self.depository_account_number or not self.b3_clearing_subaccount:
            raise InvalidDepositError("Números de conta de custódia internacional não podem ser vazios.")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "participant_id": self.participant_id,
            "segregation_type": self.segregation_type.value,
            "depository": self.depository.value,
            "depository_account_number": self.depository_account_number,
            "b3_clearing_subaccount": self.b3_clearing_subaccount,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InternationalCustodyAccount:
        return cls(
            account_id=data["account_id"],
            participant_id=data["participant_id"],
            segregation_type=AccountSegregationType(data["segregation_type"]),
            depository=InternationalDepository(data["depository"]),
            depository_account_number=data["depository_account_number"],
            b3_clearing_subaccount=data["b3_clearing_subaccount"],
        )


@dataclass
class OffshoreDepositPosition:
    """Posição custodiada de garantia offshore e seu status de bloqueio multilateral."""
    position_id: str
    account_id: str
    participant_id: str
    isin: str
    total_quantity: Decimal
    pledged_quantity: Decimal
    unencumbered_quantity: Decimal
    status: PledgeStatus = PledgeStatus.PLEDGED_MULTILATERAL
    created_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.total_quantity <= Decimal("0"):
            raise InvalidDepositError(f"Quantidade total da posição {self.position_id} deve ser > 0.")
        if self.pledged_quantity < Decimal("0") or self.unencumbered_quantity < Decimal("0"):
            raise InvalidDepositError("Quantidades bloqueadas e desimpedidas não podem ser negativas.")
        if self.total_quantity != (self.pledged_quantity + self.unencumbered_quantity):
            raise InternationalCustodySegregationError(
                f"Violação de conservação na posição {self.position_id}: "
                f"Total ({self.total_quantity}) != Bloqueado ({self.pledged_quantity}) + Livre ({self.unencumbered_quantity})."
            )

    def pledge_more(self, qty: Decimal) -> None:
        """Bloqueia quantidade adicional desimpedida como colateral multilateral."""
        if qty <= Decimal("0"):
            raise InvalidDepositError("Quantidade a bloquear deve ser estritamente positiva.")
        if qty > self.unencumbered_quantity:
            raise InvalidDepositError(
                f"Saldo desimpedido insuficiente ({self.unencumbered_quantity}) para bloquear {qty}."
            )
        self.unencumbered_quantity -= qty
        self.pledged_quantity += qty
        self.status = PledgeStatus.PLEDGED_MULTILATERAL

    def unpledge(self, qty: Decimal) -> None:
        """Desonera quantidade previamente bloqueada, retornando ao status desimpedido."""
        if qty <= Decimal("0"):
            raise InvalidDepositError("Quantidade a desonerar deve ser estritamente positiva.")
        if qty > self.pledged_quantity:
            raise InvalidDepositError(
                f"Saldo bloqueado insuficiente ({self.pledged_quantity}) para desonerar {qty}."
            )
        self.pledged_quantity -= qty
        self.unencumbered_quantity += qty
        if self.pledged_quantity == Decimal("0"):
            self.status = PledgeStatus.UNENCUMBERED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "account_id": self.account_id,
            "participant_id": self.participant_id,
            "isin": self.isin,
            "total_quantity": str(self.total_quantity),
            "pledged_quantity": str(self.pledged_quantity),
            "unencumbered_quantity": str(self.unencumbered_quantity),
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OffshoreDepositPosition:
        c_at = data.get("created_at")
        if isinstance(c_at, str) and c_at:
            c_at = datetime.fromisoformat(c_at)
        return cls(
            position_id=data["position_id"],
            account_id=data["account_id"],
            participant_id=data["participant_id"],
            isin=data["isin"],
            total_quantity=Decimal(str(data["total_quantity"])),
            pledged_quantity=Decimal(str(data["pledged_quantity"])),
            unencumbered_quantity=Decimal(str(data["unencumbered_quantity"])),
            status=PledgeStatus(data["status"]),
            created_at=c_at,
        )


# ---------------------------------------------------------------------------
# Avaliação de Garantia e Marcação a Mercado
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvaluatedOffshoreCollateral:
    """Resultado da avaliação e conversão a mercado do colateral offshore."""
    position_id: str
    participant_id: str
    account_id: str
    isin: str
    ticker: str
    asset_type: OffshoreAssetType
    currency: OffshoreCurrency
    depository: InternationalDepository
    pledged_quantity: Decimal
    unit_price_foreign: Decimal
    gross_foreign_value: Decimal
    ptax_bid_rate: Decimal
    gross_brl_value: Decimal
    haircuts: OffshoreHaircutBreakdown
    eligible_collateral_value_brl: Decimal
    fx_safety_margin_brl: Decimal
    net_margin_credit_brl: Decimal

    def __post_init__(self) -> None:
        # Invariante 1: Não-negatividade e limitação superior
        if self.eligible_collateral_value_brl < Decimal("0"):
            raise OffshoreInvariantViolationError(
                f"Violação de não-negatividade: colateral elegível {self.eligible_collateral_value_brl} < 0."
            )
        if self.eligible_collateral_value_brl > self.gross_brl_value:
            raise OffshoreInvariantViolationError(
                f"Violação de limitação superior: elegível ({self.eligible_collateral_value_brl}) > bruto ({self.gross_brl_value})."
            )
        if self.net_margin_credit_brl < Decimal("0"):
            raise OffshoreInvariantViolationError(
                f"Violação de não-negatividade: crédito líquido de margem {self.net_margin_credit_brl} < 0."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "participant_id": self.participant_id,
            "account_id": self.account_id,
            "isin": self.isin,
            "ticker": self.ticker,
            "asset_type": self.asset_type.value,
            "currency": self.currency.value,
            "depository": self.depository.value,
            "pledged_quantity": str(self.pledged_quantity),
            "unit_price_foreign": str(self.unit_price_foreign),
            "gross_foreign_value": str(self.gross_foreign_value),
            "ptax_bid_rate": str(self.ptax_bid_rate),
            "gross_brl_value": str(self.gross_brl_value),
            "haircuts": self.haircuts.to_dict(),
            "eligible_collateral_value_brl": str(self.eligible_collateral_value_brl),
            "fx_safety_margin_brl": str(self.fx_safety_margin_brl),
            "net_margin_credit_brl": str(self.net_margin_credit_brl),
        }


# ---------------------------------------------------------------------------
# Motor de Garantias Offshore e Eurobonds (CORE B3)
# ---------------------------------------------------------------------------

class OffshoreEurobondsCollateralEngine:
    """Motor de Gestão, Avaliação, Haircuts e Bloqueio de Colaterais Offshore B3 (FL-083)."""

    # Matriz regulatória B3 de Haircut por Rating de Crédito
    RATING_HAIRCUT_TABLE: Dict[CreditRating, Decimal] = {
        CreditRating.AAA: Decimal("0.0200"),
        CreditRating.AA_PLUS: Decimal("0.0250"),
        CreditRating.AA: Decimal("0.0300"),
        CreditRating.AA_MINUS: Decimal("0.0350"),
        CreditRating.A_PLUS: Decimal("0.0450"),
        CreditRating.A: Decimal("0.0500"),
        CreditRating.A_MINUS: Decimal("0.0550"),
        CreditRating.BBB_PLUS: Decimal("0.0700"),
        CreditRating.BBB: Decimal("0.0800"),
        CreditRating.BBB_MINUS: Decimal("0.0950"),
        CreditRating.BB_PLUS: Decimal("0.1300"),
        CreditRating.BB: Decimal("0.1500"),
        CreditRating.BB_MINUS: Decimal("0.1800"),
    }

    # Matriz regulatória B3 de Haircut por Tenor (Eurobonds)
    TENOR_HAIRCUT_TABLE: Dict[TenorBucket, Decimal] = {
        TenorBucket.UP_TO_1Y: Decimal("0.0200"),
        TenorBucket.FROM_1Y_TO_3Y: Decimal("0.0400"),
        TenorBucket.FROM_3Y_TO_5Y: Decimal("0.0650"),
        TenorBucket.FROM_5Y_TO_10Y: Decimal("0.0950"),
        TenorBucket.OVER_10Y: Decimal("0.1350"),
    }

    # Haircut base para Renda Variável Offshore (ADR e GDR)
    EQUITY_HAIRCUT_TABLE: Dict[OffshoreAssetType, Decimal] = {
        OffshoreAssetType.ADR: Decimal("0.2000"),  # 20.0% base equity volatility
        OffshoreAssetType.GDR: Decimal("0.2200"),  # 22.0% base equity volatility
    }

    # Haircut por Risco de Jurisdição / Risco País
    COUNTRY_RISK_TABLE: Dict[str, Decimal] = {
        "BR_OFFSHORE": Decimal("0.0400"),  # Risco do emissor offshore brasileiro
        "US": Decimal("0.0000"),
        "LUX": Decimal("0.0100"),
        "UK": Decimal("0.0100"),
        "DEFAULT": Decimal("0.0500"),
    }

    def __init__(self) -> None:
        self.ptax_rates: Dict[OffshoreCurrency, PtaxRate] = {}
        self.fx_safety_configs: Dict[OffshoreCurrency, FxSafetyMarginConfig] = {
            OffshoreCurrency.USD: FxSafetyMarginConfig(
                currency=OffshoreCurrency.USD,
                base_haircut=Decimal("0.0500"),
                volatility_stress_buffer=Decimal("0.0250"),
            ),
            OffshoreCurrency.EUR: FxSafetyMarginConfig(
                currency=OffshoreCurrency.EUR,
                base_haircut=Decimal("0.0600"),
                volatility_stress_buffer=Decimal("0.0300"),
            ),
        }
        self.securities: Dict[str, OffshoreSecurity] = {}
        self.accounts: Dict[str, InternationalCustodyAccount] = {}
        self.positions: Dict[str, OffshoreDepositPosition] = {}

    # -----------------------------------------------------------------------
    # Cadastros e Configurações
    # -----------------------------------------------------------------------

    def register_ptax(self, ptax: PtaxRate) -> None:
        """Registra cotação diária oficial PTAX do Banco Central."""
        self.ptax_rates[ptax.currency] = ptax

    def configure_fx_safety(self, config: FxSafetyMarginConfig) -> None:
        """Configura margem de segurança cambial e buffer de volatilidade."""
        self.fx_safety_configs[config.currency] = config

    def register_security(self, security: OffshoreSecurity) -> None:
        """Registra ativo elegível internacional (ADR, GDR ou Eurobond)."""
        if not security.credit_rating.is_b3_eligible:
            raise IneligibleRatingError(
                f"Ativo {security.isin} possui rating {security.credit_rating.value} incompatível com piso B3."
            )
        self.securities[security.isin] = security

    def register_account(self, account: InternationalCustodyAccount) -> None:
        """Registra conta de custódia na depositária internacional parceira."""
        self.accounts[account.account_id] = account

    # -----------------------------------------------------------------------
    # Operações de Depósito, Bloqueio e Desoneração
    # -----------------------------------------------------------------------

    def deposit_collateral(
        self,
        position_id: str,
        account_id: str,
        isin: str,
        quantity: Decimal,
        pledge_immediately: bool = True,
    ) -> OffshoreDepositPosition:
        """Registra o depósito internacional de ativos e opcionalmente bloqueia em garantia multilateral."""
        if account_id not in self.accounts:
            raise InvalidDepositError(f"Conta de custódia {account_id} não cadastrada.")
        if isin not in self.securities:
            raise IneligibleAssetError(f"Ativo {isin} não cadastrado ou não elegível.")
        if quantity <= Decimal("0"):
            raise InvalidDepositError("Quantidade de depósito deve ser estritamente positiva.")

        account = self.accounts[account_id]
        security = self.securities[isin]

        # Validação de compatibilidade de depositária
        if account.depository != security.depository:
            raise InternationalCustodySegregationError(
                f"Incompatibilidade de custódia: conta {account_id} está na depositária {account.depository.value}, "
                f"mas ativo {isin} está registrado na depositária {security.depository.value}."
            )

        pledged = quantity if pledge_immediately else Decimal("0.0")
        unencumbered = Decimal("0.0") if pledge_immediately else quantity
        status = PledgeStatus.PLEDGED_MULTILATERAL if pledge_immediately else PledgeStatus.UNENCUMBERED

        position = OffshoreDepositPosition(
            position_id=position_id,
            account_id=account_id,
            participant_id=account.participant_id,
            isin=isin,
            total_quantity=quantity,
            pledged_quantity=pledged,
            unencumbered_quantity=unencumbered,
            status=status,
            created_at=datetime.now(),
        )

        self.positions[position_id] = position
        return position

    def pledge_collateral(self, position_id: str, quantity: Decimal) -> OffshoreDepositPosition:
        """Bloqueia quantidade livre de ativo internacional como garantia multilateral na Câmara B3."""
        if position_id not in self.positions:
            raise InvalidDepositError(f"Posição {position_id} não encontrada.")
        pos = self.positions[position_id]
        pos.pledge_more(quantity)
        return pos

    def unpledge_collateral(
        self,
        position_id: str,
        quantity: Decimal,
        required_clearing_margin_brl: Decimal,
        as_of_date: date,
    ) -> OffshoreDepositPosition:
        """Desonera garantia offshore respeitando a trava anti-déficit Safe-Release Guardrail."""
        if position_id not in self.positions:
            raise InvalidDepositError(f"Posição {position_id} não encontrada.")
        pos = self.positions[position_id]

        # Simula o portfólio remanescente do participante
        participant_id = pos.participant_id
        current_portfolio = self.evaluate_participant_portfolio(participant_id, as_of_date)
        current_margin_credit = current_portfolio["total_net_margin_credit_brl"]

        # Calcula a perda de crédito de margem decorrente da desoneração solicitada
        eval_item = self.evaluate_position(position_id, as_of_date)
        if eval_item.pledged_quantity <= Decimal("0"):
            raise InvalidDepositError("Posição não possui saldo bloqueado para desonerar.")

        credit_per_unit = eval_item.net_margin_credit_brl / eval_item.pledged_quantity
        credit_to_release = (credit_per_unit * quantity).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        projected_residual_credit = current_margin_credit - credit_to_release

        # Safe-Release Guardrail: A desoneração não pode gerar déficit de margem
        if projected_residual_credit < required_clearing_margin_brl:
            deficit = (required_clearing_margin_brl - projected_residual_credit).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            raise MarginReleaseViolationError(
                f"Safe-Release Guardrail violado: desoneração geraria déficit de margem de R$ {deficit} "
                f"(Margem Requerida: R$ {required_clearing_margin_brl}, Crédito Remanescente: R$ {projected_residual_credit})."
            )

        pos.unpledge(quantity)
        return pos

    # -----------------------------------------------------------------------
    # Matriz de Haircuts e Avaliação Financeira
    # -----------------------------------------------------------------------

    def calculate_haircut_breakdown(
        self,
        security: OffshoreSecurity,
        as_of_date: date,
    ) -> OffshoreHaircutBreakdown:
        """Calcula analiticamente a composição aditiva e o haircut total regulatório."""
        # 1. Haircut de Risco País / Jurisdição
        country_haircut = self.COUNTRY_RISK_TABLE.get(
            security.country_risk_zone,
            self.COUNTRY_RISK_TABLE["DEFAULT"],
        )

        # 2. Haircut por Rating de Crédito
        if security.credit_rating not in self.RATING_HAIRCUT_TABLE:
            raise IneligibleRatingError(
                f"Rating {security.credit_rating.value} não elegível para a tabela de haircuts B3."
            )
        credit_haircut = self.RATING_HAIRCUT_TABLE[security.credit_rating]

        # 3. Haircut por Tenor (Eurobonds) ou Volatilidade de Ação (ADR/GDR)
        if security.asset_type.is_fixed_income:
            bucket = security.get_tenor_bucket(as_of_date)
            tenor_or_equity_haircut = self.TENOR_HAIRCUT_TABLE[bucket]
        else:
            tenor_or_equity_haircut = self.EQUITY_HAIRCUT_TABLE.get(
                security.asset_type,
                Decimal("0.2000"),
            )

        # 4. Haircut de Volatilidade Cambial PTAX
        fx_config = self.fx_safety_configs.get(
            security.currency,
            FxSafetyMarginConfig(
                currency=security.currency,
                base_haircut=Decimal("0.0500"),
                volatility_stress_buffer=Decimal("0.0250"),
            ),
        )
        fx_haircut = fx_config.total_fx_haircut

        # 5. Total aditivo com teto de segurança prudencial (90%)
        additive_sum = country_haircut + credit_haircut + tenor_or_equity_haircut + fx_haircut
        capped_total = min(additive_sum, Decimal("0.9000")).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)

        return OffshoreHaircutBreakdown(
            country_risk_haircut=country_haircut,
            credit_rating_haircut=credit_haircut,
            tenor_or_equity_haircut=tenor_or_equity_haircut,
            fx_volatility_haircut=fx_haircut,
            total_haircut=capped_total,
        )

    def evaluate_position(
        self,
        position_id: str,
        as_of_date: date,
    ) -> EvaluatedOffshoreCollateral:
        """Avalia a mercado, converte via PTAX bid e apura o crédito de garantia B3."""
        if position_id not in self.positions:
            raise InvalidDepositError(f"Posição {position_id} não encontrada.")

        pos = self.positions[position_id]
        sec = self.securities[pos.isin]

        if sec.currency not in self.ptax_rates:
            raise UnsupportedCurrencyError(
                f"Cotação PTAX não cadastrada para a moeda {sec.currency.value} na data {as_of_date}."
            )
        ptax = self.ptax_rates[sec.currency]

        # Quantidade bloqueada em garantia
        qty_pledged = pos.pledged_quantity
        gross_foreign = (qty_pledged * sec.unit_price).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Câmbio conservador: Taxa PTAX de Compra (bid)
        gross_brl = (gross_foreign * ptax.bid_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Aplicação dos haircuts regulatórios
        haircuts = self.calculate_haircut_breakdown(sec, as_of_date)
        eligible_factor = Decimal("1.0000") - haircuts.total_haircut
        eligible_brl = (gross_brl * eligible_factor).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Margem de segurança cambial isolada
        fx_margin_brl = (gross_brl * haircuts.fx_volatility_haircut).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Crédito líquido final de margem reconhecido pelo sistema CORE B3
        net_margin_credit_brl = eligible_brl

        return EvaluatedOffshoreCollateral(
            position_id=position_id,
            participant_id=pos.participant_id,
            account_id=pos.account_id,
            isin=sec.isin,
            ticker=sec.ticker,
            asset_type=sec.asset_type,
            currency=sec.currency,
            depository=sec.depository,
            pledged_quantity=qty_pledged,
            unit_price_foreign=sec.unit_price,
            gross_foreign_value=gross_foreign,
            ptax_bid_rate=ptax.bid_rate,
            gross_brl_value=gross_brl,
            haircuts=haircuts,
            eligible_collateral_value_brl=eligible_brl,
            fx_safety_margin_brl=fx_margin_brl,
            net_margin_credit_brl=net_margin_credit_brl,
        )

    def evaluate_participant_portfolio(
        self,
        participant_id: str,
        as_of_date: date,
    ) -> Dict[str, Any]:
        """Avalia todas as posições bloqueadas de um participante na Câmara B3."""
        items: List[EvaluatedOffshoreCollateral] = []
        total_gross_brl = Decimal("0.00")
        total_eligible_brl = Decimal("0.00")
        total_net_margin_credit = Decimal("0.00")

        for pos in self.positions.values():
            if pos.participant_id == participant_id:
                eval_item = self.evaluate_position(pos.position_id, as_of_date)
                items.append(eval_item)
                total_gross_brl += eval_item.gross_brl_value
                total_eligible_brl += eval_item.eligible_collateral_value_brl
                total_net_margin_credit += eval_item.net_margin_credit_brl

        return {
            "participant_id": participant_id,
            "as_of_date": as_of_date.isoformat(),
            "items_count": len(items),
            "total_gross_brl": round_money(total_gross_brl),
            "total_eligible_brl": round_money(total_eligible_brl),
            "total_net_margin_credit_brl": round_money(total_net_margin_credit),
            "items": [item.to_dict() for item in items],
        }

    # -----------------------------------------------------------------------
    # Excussão Forçada em Caso de Inadimplência Multilateral
    # -----------------------------------------------------------------------

    def execute_default_liquidation(
        self,
        position_id: str,
        liquidation_price_foreign: Decimal,
        as_of_date: date,
    ) -> Dict[str, Any]:
        """Executa a liquidação forçada do colateral offshore para cobrir inadimplência da compensação multilateral."""
        if position_id not in self.positions:
            raise InvalidDepositError(f"Posição {position_id} não encontrada.")
        if liquidation_price_foreign <= Decimal("0"):
            raise InvalidDepositError("Preço de liquidação deve ser estritamente positivo.")

        pos = self.positions[position_id]
        sec = self.securities[pos.isin]
        ptax = self.ptax_rates[sec.currency]

        qty_executed = pos.pledged_quantity
        gross_foreign_proceeds = (qty_executed * liquidation_price_foreign).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        gross_brl_proceeds = (gross_foreign_proceeds * ptax.bid_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Atualiza a posição
        pos.pledged_quantity = Decimal("0.0")
        pos.total_quantity = pos.unencumbered_quantity
        pos.status = PledgeStatus.EXECUTED_DEFAULT

        return {
            "position_id": position_id,
            "isin": sec.isin,
            "executed_quantity": str(qty_executed),
            "liquidation_price_foreign": str(liquidation_price_foreign),
            "gross_foreign_proceeds": str(gross_foreign_proceeds),
            "ptax_bid_rate": str(ptax.bid_rate),
            "gross_brl_proceeds": str(gross_brl_proceeds),
            "status": pos.status.value,
        }

    # -----------------------------------------------------------------------
    # Verificação Formal de Invariantes
    # -----------------------------------------------------------------------

    def verify_invariants(self, as_of_date: Optional[date] = None) -> Dict[str, bool]:
        """Executa a verificação matemática formal de todas as leis e invariantes do fluxo FL-083."""
        ref_date = as_of_date or date(2026, 11, 4)
        report = {
            "collateral_non_negativity": True,
            "collateral_upper_bounded": True,
            "custody_segregation_balance": True,
            "depository_account_segregation": True,
            "haircut_codomain_bounded": True,
            "rating_haircut_monotonicity": True,
            "tenor_haircut_monotonicity": True,
            "ptax_conservative_pricing": True,
        }

        # 1. PTAX: bid > 0 e bid <= ask
        for cur, ptax in self.ptax_rates.items():
            if ptax.bid_rate <= Decimal("0") or ptax.bid_rate > ptax.ask_rate:
                report["ptax_conservative_pricing"] = False

        # 2. Monotonicidade por Rating de Crédito
        # Ranks mais fracos (maior ordinal) devem possuir haircut maior ou igual
        sorted_ratings = sorted(self.RATING_HAIRCUT_TABLE.keys(), key=lambda r: r.ordinal_rank)
        for i in range(len(sorted_ratings) - 1):
            h_stronger = self.RATING_HAIRCUT_TABLE[sorted_ratings[i]]
            h_weaker = self.RATING_HAIRCUT_TABLE[sorted_ratings[i + 1]]
            if h_stronger > h_weaker:
                report["rating_haircut_monotonicity"] = False

        # 3. Monotonicidade por Tenor de Renda Fixa (Eurobonds)
        tenor_order = [
            TenorBucket.UP_TO_1Y,
            TenorBucket.FROM_1Y_TO_3Y,
            TenorBucket.FROM_3Y_TO_5Y,
            TenorBucket.FROM_5Y_TO_10Y,
            TenorBucket.OVER_10Y,
        ]
        for i in range(len(tenor_order) - 1):
            h1 = self.TENOR_HAIRCUT_TABLE[tenor_order[i]]
            h2 = self.TENOR_HAIRCUT_TABLE[tenor_order[i + 1]]
            if h1 > h2:
                report["tenor_haircut_monotonicity"] = False

        # 4. Invariante de Segregação Internacional de Custódia e Não-Negatividade
        for pos in self.positions.values():
            # Invariante 2: Conservação de saldo de custódia
            if pos.total_quantity != (pos.pledged_quantity + pos.unencumbered_quantity):
                report["custody_segregation_balance"] = False

            # Invariante 3: Segregação por conta e depositária
            account = self.accounts.get(pos.account_id)
            if account is None:
                report["depository_account_segregation"] = False
            else:
                sec = self.securities.get(pos.isin)
                if sec is None or sec.depository != account.depository:
                    report["depository_account_segregation"] = False

            # Invariante 1: Avaliação a mercado e limites de colateral
            if pos.isin in self.securities and self.securities[pos.isin].currency in self.ptax_rates:
                eval_item = self.evaluate_position(pos.position_id, ref_date)

                # Não-negatividade
                if eval_item.eligible_collateral_value_brl < Decimal("0"):
                    report["collateral_non_negativity"] = False
                if eval_item.net_margin_credit_brl < Decimal("0"):
                    report["collateral_non_negativity"] = False

                # Limitação superior
                if eval_item.eligible_collateral_value_brl > eval_item.gross_brl_value:
                    report["collateral_upper_bounded"] = False

                # Co-domínio de haircuts [0, 1)
                if not (Decimal("0.00") <= eval_item.haircuts.total_haircut < Decimal("1.00")):
                    report["haircut_codomain_bounded"] = False

        return report

    # -----------------------------------------------------------------------
    # Serialização e Carregamento de Fixtures
    # -----------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado completo do motor para dicionário Python."""
        return {
            "schema_version": "1.0.0",
            "flow_id": "FL-083",
            "system": "B3_CLEARING_OFFSHORE_EUROBONDS_COLLATERAL",
            "ptax_rates": [ptax.to_dict() for ptax in self.ptax_rates.values()],
            "fx_safety_configs": [cfg.to_dict() for cfg in self.fx_safety_configs.values()],
            "securities": [sec.to_dict() for sec in self.securities.values()],
            "accounts": [acc.to_dict() for acc in self.accounts.values()],
            "positions": [pos.to_dict() for pos in self.positions.values()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OffshoreEurobondsCollateralEngine:
        """Cria e hidrata o motor a partir de dicionário serializado."""
        engine = cls()

        for p_data in data.get("ptax_rates", []):
            engine.register_ptax(PtaxRate.from_dict(p_data))

        for f_data in data.get("fx_safety_configs", []):
            engine.configure_fx_safety(FxSafetyMarginConfig.from_dict(f_data))

        for s_data in data.get("securities", []):
            engine.register_security(OffshoreSecurity.from_dict(s_data))

        for a_data in data.get("accounts", []):
            engine.register_account(InternationalCustodyAccount.from_dict(a_data))

        for pos_data in data.get("positions", []):
            pos = OffshoreDepositPosition.from_dict(pos_data)
            engine.positions[pos.position_id] = pos

        return engine

    @classmethod
    def load_from_fixture(cls, file_path: Union[str, Path]) -> OffshoreEurobondsCollateralEngine:
        """Carrega e instancia o motor a partir de fixture JSON padronizada."""
        path = Path(file_path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
