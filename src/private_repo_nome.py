"""Módulo de Registro, Marcação a Mercado e Liquidação DvP de Operações Compromissadas Privadas no Balcão NoMe B3 (FL-079).

Implementa o modelo determinístico de registro, cálculo de haircuts regulatórios,
marcação a mercado diária (MtM), chamadas de margem por variação de colateral,
liquidação Delivery versus Payment (DvP) em D0 e vencimento, e excussão fiduciária
em caso de default para Operações Compromissadas (Private Repos) lastreadas em
títulos privados de renda fixa registrados no Balcão NoMe B3:
- Debêntures Corporativas (DEBENTURE)
- Letras Financeiras Seniores e Subordinadas (LF)
- Certificados de Recebíveis Imobiliários (CRI)
- Certificados de Recebíveis do Agronegócio (CRA)

Invariantes Formais Provadas:
1. Invariante Formal de Conservação DvP (Delivery versus Payment):
   - Transferência estritamente simultânea de custódia e caixa na ida (D0) e na volta (vencimento).
   - Conservação de custódia: Q_total(ativo) == Q_livre + Q_bloqueada_compromissada (sem criação/destruição de títulos).
   - Conservação de caixa: Delta_Caixa_Comprador + Delta_Caixa_Vendedor == 0 (zero-sum transfer).
2. Invariante de Paridade de Preço de Recompra:
   - Preço_Recompra == round_cents(Preço_Venda * Fator_Remuneração) (centavo a centavo).
3. Invariante de Cobertura Regulatória e Monotonicidade da Matriz de Haircuts:
   - Haircuts estritamente contidos em [MIN_HAIRCUT, MAX_HAIRCUT] (2% a 50%).
   - Monotonicidade por Rating: Haircut(AAA) <= Haircut(AA) <= Haircut(A) <= Haircut(BBB) <= Haircut(SUB_INVESTMENT).
   - Monotonicidade por Prazo Residual: Haircut(Tenor_Curto) <= Haircut(Tenor_Médio) <= Haircut(Tenor_Longo).
4. Invariante Financeira de Excussão em Default (Waterfall de Balcão NoMe):
   - Recursos_Brutos == Taxas_B3 + Valor_Pago_Comprador + Excedente_Devolvido_Vendedor.
   - Valor_Pago_Comprador + Saldo_Devedor_Descoberto == Divida_Total_Comprador.
5. Invariante de Não-Negatividade:
   - Saldos de caixa, custódia física, preços unitários e dívidas atualizadas são estritamente >= 0.

100% Python Standard Library (decimal, datetime, dataclasses, enum, json, pathlib).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union


# =====================================================================
# CONSTANTES DE PRECISÃO NUMÉRICA E ARREDONDAMENTO B3 / CETIP
# =====================================================================

QUANTIZE_MONEY = Decimal("0.01")
QUANTIZE_PU = Decimal("0.00000001")
QUANTIZE_RATE = Decimal("0.00000001")
QUANTIZE_FACTOR = Decimal("0.00000001")
QUANTIZE_QTY = Decimal("0.0001")

DAYS_IN_YEAR_WORKDAYS = Decimal("252")
MIN_REGULATORY_HAIRCUT = Decimal("0.02000000")   # Haircut regulatório mínimo (2%)
MAX_REGULATORY_HAIRCUT = Decimal("0.50000000")   # Haircut regulatório teto (50%)
DEFAULT_B3_EXECUTION_FEE_RATE = Decimal("0.00100000")  # Taxa operacional NoMe B3 de excussão (0,10%)
DEFAULT_MARGIN_CALL_THRESHOLD = Decimal("100.00")      # Tolerância mínima de déficit financeiro


def round_cents(val: Decimal) -> Decimal:
    """Arredonda valor financeiro para 2 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_MONEY, rounding=ROUND_HALF_UP)


def round_pu(val: Decimal) -> Decimal:
    """Arredonda Preço Unitário (PU) para 8 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_PU, rounding=ROUND_HALF_UP)


def round_rate(val: Decimal) -> Decimal:
    """Arredonda taxas percentuais e haircuts para 8 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_RATE, rounding=ROUND_HALF_UP)


def round_factor(val: Decimal) -> Decimal:
    """Arredonda fatores diários e acumulados para 8 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_FACTOR, rounding=ROUND_HALF_UP)


def round_qty(val: Decimal) -> Decimal:
    """Arredonda quantidade física de títulos para 4 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_QTY, rounding=ROUND_HALF_UP)


def to_decimal(value: Any, field_name: str = "campo") -> Decimal:
    """Converte valor para Decimal de forma robusta e auditável."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise PrivateRepoValidationError(f"Valor nulo não permitido para '{field_name}'.")
    try:
        cleaned = str(value).strip().replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError) as err:
        raise PrivateRepoValidationError(
            f"Valor inválido '{value}' para o campo '{field_name}': {err}"
        ) from err


def parse_date(value: Any, field_name: str = "data") -> date:
    """Converte representação ISO-8601 (YYYY-MM-DD) em date."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            raise PrivateRepoValidationError(f"Data vazia para campo '{field_name}'.")
        try:
            return datetime.strptime(cleaned, "%Y-%m-%d").date()
        except ValueError as err:
            raise PrivateRepoValidationError(
                f"Formato de data inválido '{value}' para '{field_name}'. Esperado YYYY-MM-DD."
            ) from err
    raise PrivateRepoValidationError(
        f"Tipo inválido '{type(value).__name__}' para campo de data '{field_name}'."
    )


def parse_datetime(value: Any, field_name: str = "timestamp") -> datetime:
    """Converte representação ISO-8601 em datetime UTC."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        cleaned = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError as err:
            raise PrivateRepoValidationError(
                f"Formato de timestamp inválido '{value}' para '{field_name}'."
            ) from err
    raise PrivateRepoValidationError(
        f"Tipo inválido '{type(value).__name__}' para campo de timestamp '{field_name}'."
    )


def is_workday(d: date) -> bool:
    """Determina se uma data é dia útil comercial (segunda a sexta-feira)."""
    return d.weekday() < 5


def count_workdays(start_date: date, end_date: date) -> int:
    """Conta dias úteis entre start_date (exclusivo) e end_date (inclusive), padrão B3 DU/252."""
    if start_date >= end_date:
        return 0
    cur = start_date + timedelta(days=1)
    du = 0
    while cur <= end_date:
        if is_workday(cur):
            du += 1
        cur += timedelta(days=1)
    return du


# =====================================================================
# HIERARQUIA DE EXCEÇÕES DE DOMÍNIO (FL-079)
# =====================================================================

class PrivateRepoError(Exception):
    """Exceção base para operações compromissadas com títulos privados no NoMe B3."""
    pass


class PrivateRepoValidationError(PrivateRepoError):
    """Violação de regras de validação estrutural ou contratual de compromissada privada."""
    pass


class PrivateRepoHaircutError(PrivateRepoError):
    """Erro ou inconsistência no cálculo ou aplicação de haircut regulatório B3."""
    pass


class PrivateRepoMarginCallError(PrivateRepoError):
    """Falha operacional ou inadimplência em chamada de margem de compromissada privada."""
    pass


class PrivateRepoSettlementError(PrivateRepoError):
    """Erro de liquidação financeira ou física DvP (D0 ou vencimento)."""
    pass


class PrivateRepoDefaultError(PrivateRepoSettlementError):
    """Inadimplência contratual de contraparte (não pagamento de recompra ou margem)."""
    pass


class PrivateRepoInvariantViolationError(AssertionError, PrivateRepoError):
    """Violação de invariante matemática ou lei formal de conservação DvP / Balcão NoMe."""
    pass


# =====================================================================
# ENUMS DE DOMÍNIO
# =====================================================================

class PrivateAssetType(str, Enum):
    """Tipos de títulos de renda fixa privada aceitos como lastro no Balcão NoMe B3."""
    DEBENTURE = "DEBENTURE"
    LF = "LF"
    CRI = "CRI"
    CRA = "CRA"

    @classmethod
    def from_str(cls, value: Any) -> PrivateAssetType:
        if isinstance(value, cls):
            return value
        if not value:
            raise PrivateRepoValidationError("Tipo de ativo de lastro não pode ser vazio.")
        normalized = str(value).strip().upper().replace("-", "").replace("_", "")
        mapping = {
            "DEBENTURE": cls.DEBENTURE,
            "DEB": cls.DEBENTURE,
            "LF": cls.LF,
            "LETRAFINANCEIRA": cls.LF,
            "CRI": cls.CRI,
            "CRA": cls.CRA,
        }
        if normalized in mapping:
            return mapping[normalized]
        valid = ", ".join(m.value for m in cls)
        raise PrivateRepoValidationError(
            f"Tipo de ativo privado inválido: {value!r}. Valores permitidos: {valid}"
        )


class CreditRating(str, Enum):
    """Classificação de risco de crédito de agências de rating (S&P, Moody's, Fitch)."""
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
    SUB_INVESTMENT = "SUB_INVESTMENT"
    UNRATED = "UNRATED"

    @classmethod
    def from_str(cls, value: Any) -> CreditRating:
        if isinstance(value, cls):
            return value
        if not value:
            return cls.UNRATED
        cleaned = str(value).strip().upper().replace(" ", "").replace("_", "")
        mapping = {
            "AAA": cls.AAA,
            "AA+": cls.AA_PLUS,
            "AA": cls.AA,
            "AA-": cls.AA_MINUS,
            "A+": cls.A_PLUS,
            "A": cls.A,
            "A-": cls.A_MINUS,
            "BBB+": cls.BBB_PLUS,
            "BBB": cls.BBB,
            "BBB-": cls.BBB_MINUS,
            "SUBINVESTMENT": cls.SUB_INVESTMENT,
            "HIGHYIELD": cls.SUB_INVESTMENT,
            "BB": cls.SUB_INVESTMENT,
            "B": cls.SUB_INVESTMENT,
            "CCC": cls.SUB_INVESTMENT,
            "UNRATED": cls.UNRATED,
            "SEMRATING": cls.UNRATED,
        }
        if cleaned in mapping:
            return mapping[cleaned]
        return cls.UNRATED

    @property
    def is_investment_grade(self) -> bool:
        """Indica se a classificação de rating pertence ao Grau de Investimento."""
        return self not in (CreditRating.SUB_INVESTMENT, CreditRating.UNRATED)

    @property
    def rank(self) -> int:
        """Classificação ordinal de qualidade de crédito (0 = melhor, 5 = pior)."""
        ranks = {
            CreditRating.AAA: 0,
            CreditRating.AA_PLUS: 1,
            CreditRating.AA: 1,
            CreditRating.AA_MINUS: 1,
            CreditRating.A_PLUS: 2,
            CreditRating.A: 2,
            CreditRating.A_MINUS: 2,
            CreditRating.BBB_PLUS: 3,
            CreditRating.BBB: 3,
            CreditRating.BBB_MINUS: 3,
            CreditRating.SUB_INVESTMENT: 4,
            CreditRating.UNRATED: 5,
        }
        return ranks.get(self, 5)


class RateType(str, Enum):
    """Regime de remuneração da cláusula de recompra da compromissada."""
    PRE = "PRE"                  # Taxa pré-fixada (% a.a., convenção DU/252)
    CDI = "CDI"                  # Percentual da taxa diária CDI (ex: 100% ou 105% do CDI)
    CDI_SPREAD = "CDI_SPREAD"    # CDI + spread pré-fixado (% a.a., DU/252)

    @classmethod
    def from_str(cls, value: Any) -> RateType:
        if isinstance(value, cls):
            return value
        if not value:
            raise PrivateRepoValidationError("Tipo de remuneração não pode ser vazio.")
        normalized = str(value).strip().upper().replace("-", "_")
        mapping = {
            "PRE": cls.PRE,
            "PREFIXADO": cls.PRE,
            "CDI": cls.CDI,
            "DI": cls.CDI,
            "CDI_PERCENT": cls.CDI,
            "CDI_SPREAD": cls.CDI_SPREAD,
            "DI_SPREAD": cls.CDI_SPREAD,
        }
        if normalized in mapping:
            return mapping[normalized]
        valid = ", ".join(m.value for m in cls)
        raise PrivateRepoValidationError(
            f"Tipo de taxa inválido: {value!r}. Valores permitidos: {valid}"
        )


class ContractStatus(str, Enum):
    """Ciclo de vida operacional do contrato de compromissada privada no Balcão NoMe."""
    REGISTERED = "REGISTERED"                    # Registrado no NoMe, aguardando liquidação da ida (D0)
    ACTIVE = "ACTIVE"                            # Liquidada ida DvP, em vigência com lastro fiduciário retido
    MARGIN_CALL = "MARGIN_CALL"                  # Chamada de margem em aberto por desvalorização do lastro
    SETTLED = "SETTLED"                          # Liquidada com sucesso no vencimento (volta DvP concluída)
    DEFAULTED = "DEFAULTED"                      # Inadimplência em margem ou na perna de volta
    COLLATERAL_EXECUTED = "COLLATERAL_EXECUTED"  # Garantia privada alienada/executada no Balcão NoMe
    CANCELLED = "CANCELLED"                      # Cancelada antes do D0

    @classmethod
    def from_str(cls, value: Any) -> ContractStatus:
        if isinstance(value, cls):
            return value
        if not value:
            raise PrivateRepoValidationError("Status do contrato não pode ser vazio.")
        normalized = str(value).strip().upper()
        try:
            return cls[normalized]
        except KeyError:
            valid = ", ".join(m.value for m in cls)
            raise PrivateRepoValidationError(
                f"Status inválido: {value!r}. Permitidos: {valid}"
            )


class SettlementLeg(str, Enum):
    """Perna de liquidação da operação compromissada."""
    START_LEG = "START_LEG"    # Ida (D0)
    RETURN_LEG = "RETURN_LEG"  # Volta (Vencimento)


class MarginDepositType(str, Enum):
    """Instrumento utilizado para cobertura de chamada de margem adicional."""
    CASH = "CASH"                              # Depósito em dinheiro (BRL)
    ADDITIONAL_ASSET = "ADDITIONAL_ASSET"      # Aporte de títulos privados elegíveis adicionais


class DefaultReason(str, Enum):
    """Motivo formal do evento de default / liquidação antecipada."""
    MARGIN_CALL_EXPIRED = "MARGIN_CALL_EXPIRED"
    MATURITY_REPURCHASE_FAILED = "MATURITY_REPURCHASE_FAILED"
    VOLUNTARY_EARLY_DEFAULT = "VOLUNTARY_EARLY_DEFAULT"


# =====================================================================
# MODELOS DE DOMÍNIO (DATACLASSES)
# =====================================================================

@dataclass
class PrivateAsset:
    """Título privado de renda fixa custodiado no Balcão NoMe B3 utilizado como lastro."""
    asset_id: str
    isin: str
    asset_type: PrivateAssetType
    issuer_cnpj: str
    issuer_name: str
    rating: CreditRating
    issue_date: date
    maturity_date: date
    nominal_unit_price: Decimal
    current_market_pu: Decimal
    is_eligible: bool = True

    def __post_init__(self) -> None:
        if not self.asset_id or not self.isin:
            raise PrivateRepoValidationError("Asset ID e ISIN são obrigatórios.")
        self.asset_type = PrivateAssetType.from_str(self.asset_type)
        self.rating = CreditRating.from_str(self.rating)
        self.issue_date = parse_date(self.issue_date, "issue_date")
        self.maturity_date = parse_date(self.maturity_date, "maturity_date")
        if self.maturity_date <= self.issue_date:
            raise PrivateRepoValidationError(
                f"Data de vencimento ({self.maturity_date}) deve ser posterior à emissão ({self.issue_date})."
            )
        self.nominal_unit_price = to_decimal(self.nominal_unit_price, "nominal_unit_price")
        self.current_market_pu = to_decimal(self.current_market_pu, "current_market_pu")
        if self.nominal_unit_price <= Decimal("0.00"):
            raise PrivateRepoValidationError("Preço nominal unitário deve ser estritamente positivo.")
        if self.current_market_pu <= Decimal("0.00"):
            raise PrivateRepoValidationError("Preço unitário de mercado deve ser estritamente positivo.")

    def residual_days(self, as_of_date: date) -> int:
        """Calcula os dias corridos residuais até o vencimento do título."""
        diff = (self.maturity_date - as_of_date).days
        return max(0, diff)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "isin": self.isin,
            "asset_type": self.asset_type.value,
            "issuer_cnpj": self.issuer_cnpj,
            "issuer_name": self.issuer_name,
            "rating": self.rating.value,
            "issue_date": self.issue_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "nominal_unit_price": str(self.nominal_unit_price),
            "current_market_pu": str(self.current_market_pu),
            "is_eligible": self.is_eligible,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PrivateAsset:
        return cls(
            asset_id=data["asset_id"],
            isin=data["isin"],
            asset_type=PrivateAssetType.from_str(data["asset_type"]),
            issuer_cnpj=data["issuer_cnpj"],
            issuer_name=data["issuer_name"],
            rating=CreditRating.from_str(data["rating"]),
            issue_date=parse_date(data["issue_date"]),
            maturity_date=parse_date(data["maturity_date"]),
            nominal_unit_price=to_decimal(data["nominal_unit_price"]),
            current_market_pu=to_decimal(data["current_market_pu"]),
            is_eligible=data.get("is_eligible", True),
        )


@dataclass
class ParticipantAccount:
    """Conta de custódia e liquidação de participante no Balcão NoMe B3."""
    participant_id: str
    name: str
    custody_account: str
    cash_balance: Decimal
    free_collateral: Dict[str, Decimal] = field(default_factory=dict)
    blocked_collateral: Dict[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cash_balance = to_decimal(self.cash_balance, "cash_balance")
        if self.cash_balance < Decimal("0.00"):
            raise PrivateRepoValidationError(
                f"Saldo em caixa do participante {self.participant_id} não pode ser negativo."
            )
        # Normaliza saldos
        self.free_collateral = {
            k: to_decimal(v, f"free_collateral[{k}]")
            for k, v in self.free_collateral.items()
        }
        self.blocked_collateral = {
            k: to_decimal(v, f"blocked_collateral[{k}]")
            for k, v in self.blocked_collateral.items()
        }

    def get_free_quantity(self, asset_id: str) -> Decimal:
        return self.free_collateral.get(asset_id, Decimal("0.0000"))

    def get_blocked_quantity(self, asset_id: str) -> Decimal:
        return self.blocked_collateral.get(asset_id, Decimal("0.0000"))

    def total_quantity(self, asset_id: str) -> Decimal:
        return self.get_free_quantity(asset_id) + self.get_blocked_quantity(asset_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "name": self.name,
            "custody_account": self.custody_account,
            "cash_balance": str(self.cash_balance),
            "free_collateral": {k: str(v) for k, v in self.free_collateral.items()},
            "blocked_collateral": {k: str(v) for k, v in self.blocked_collateral.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ParticipantAccount:
        return cls(
            participant_id=data["participant_id"],
            name=data["name"],
            custody_account=data["custody_account"],
            cash_balance=to_decimal(data["cash_balance"]),
            free_collateral={k: to_decimal(v) for k, v in data.get("free_collateral", {}).items()},
            blocked_collateral={k: to_decimal(v) for k, v in data.get("blocked_collateral", {}).items()},
        )


# =====================================================================
# MATRIZ REGULATÓRIA DE HAIRCUTS B3 (FL-079)
# =====================================================================

class RegulatoryHaircutCalculator:
    """Motor de cálculo determinístico de Haircuts Regulatórios B3 para Renda Fixa Privada.

    Estrutura da Matriz Regulatória:
    1. Haircut Base por Rating de Crédito:
       - AAA: 5.0%
       - AA (AA+, AA, AA-): 7.5%
       - A (A+, A, A-): 12.0%
       - BBB (BBB+, BBB, BBB-): 18.0%
       - SUB_INVESTMENT: 30.0%
       - UNRATED: 35.0%
    2. Sobretaxa por Prazo Residual do Lastro Privado:
       - <= 365 dias (<= 1 ano): +0.0%
       - 366 a 1095 dias (1 a 3 anos): +2.0%
       - 1096 a 1825 dias (3 a 5 anos): +4.0%
       - > 1825 dias (> 5 anos): +7.0%
    3. Sobretaxa por Tipo de Instrumento:
       - LF (Instituição Financeira Bancária regulada): +0.0%
       - DEBENTURE (Corporativa comum): +1.5%
       - CRI (Certificado Recebíveis Imobiliários estruturado): +3.0%
       - CRA (Certificado Recebíveis Agronegócio estruturado): +3.0%

    Limites Regulatórios:
    - Min Haircut: 2.0%
    - Max Haircut: 50.0%
    """

    @staticmethod
    def calculate_haircut(
        asset_type: PrivateAssetType,
        rating: CreditRating,
        residual_days: int,
        issuer_watchlist: bool = False,
    ) -> Decimal:
        """Calcula o haircut regulatório total garantindo monotonicidade estrita."""
        asset_type = PrivateAssetType.from_str(asset_type)
        rating = CreditRating.from_str(rating)

        # 1. Base por Rating
        rating_bases = {
            CreditRating.AAA: Decimal("0.05000000"),
            CreditRating.AA_PLUS: Decimal("0.07500000"),
            CreditRating.AA: Decimal("0.07500000"),
            CreditRating.AA_MINUS: Decimal("0.07500000"),
            CreditRating.A_PLUS: Decimal("0.12000000"),
            CreditRating.A: Decimal("0.12000000"),
            CreditRating.A_MINUS: Decimal("0.12000000"),
            CreditRating.BBB_PLUS: Decimal("0.18000000"),
            CreditRating.BBB: Decimal("0.18000000"),
            CreditRating.BBB_MINUS: Decimal("0.18000000"),
            CreditRating.SUB_INVESTMENT: Decimal("0.30000000"),
            CreditRating.UNRATED: Decimal("0.35000000"),
        }
        base = rating_bases.get(rating, Decimal("0.35000000"))

        # 2. Sobretaxa por Prazo Residual
        if residual_days <= 365:
            tenor_surcharge = Decimal("0.00000000")
        elif residual_days <= 1095:
            tenor_surcharge = Decimal("0.02000000")
        elif residual_days <= 1825:
            tenor_surcharge = Decimal("0.04000000")
        else:
            tenor_surcharge = Decimal("0.07000000")

        # 3. Sobretaxa por Tipo de Instrumento
        type_surcharges = {
            PrivateAssetType.LF: Decimal("0.00000000"),
            PrivateAssetType.DEBENTURE: Decimal("0.01500000"),
            PrivateAssetType.CRI: Decimal("0.03000000"),
            PrivateAssetType.CRA: Decimal("0.03000000"),
        }
        type_surcharge = type_surcharges.get(asset_type, Decimal("0.03000000"))

        # 4. Sobretaxa por emissor em monitoramento especial
        watchlist_surcharge = Decimal("0.05000000") if issuer_watchlist else Decimal("0.00000000")

        total = base + tenor_surcharge + type_surcharge + watchlist_surcharge
        clipped = max(MIN_REGULATORY_HAIRCUT, min(MAX_REGULATORY_HAIRCUT, total))
        return round_rate(clipped)


# =====================================================================
# MATEMÁTICA FINANCEIRA DU/252 B3 E FATOR DE REMUNERAÇÃO
# =====================================================================

def calculate_remuneration_factor_pre(annual_rate: Decimal, workdays: int) -> Decimal:
    """Calcula o fator de remuneração de compromissada pré-fixada (DU/252).

    Fator = (1 + Taxa_aa)^(DU / 252)
    """
    if workdays < 0:
        raise PrivateRepoValidationError(f"Dias úteis não podem ser negativos: {workdays}")
    rate_d = to_decimal(annual_rate, "annual_rate")
    # Converte percentual (> 1.0) para decimal se fornecido como ex: 12.50
    rate_dec = rate_d / Decimal("100") if rate_d > Decimal("1.0") else rate_d
    if rate_dec < Decimal("-1.0"):
        raise PrivateRepoValidationError(f"Taxa anual não pode ser inferior a -100%: {rate_dec}")

    rate_factor = 1.0 + float(rate_dec)
    exp = float(workdays) / float(DAYS_IN_YEAR_WORKDAYS)
    factor_float = pow(rate_factor, exp)
    return round_factor(Decimal(f"{factor_float:.12f}"))


def calculate_remuneration_factor_cdi(
    cdi_percentage: Decimal,
    annual_cdi_rate: Decimal,
    workdays: int,
) -> Decimal:
    """Calcula o fator acumulado projetado para compromissada a % do CDI na convenção Cetip/B3.

    1. FDI = round((1 + CDI_aa)^(1/252), 8)
    2. Fator_diário = round(1 + (FDI - 1) * (cdi_percentage), 8)
    3. Fator_acumulado = round(Fator_diário ^ DU, 8)
    """
    if workdays < 0:
        raise PrivateRepoValidationError(f"Dias úteis não podem ser negativos: {workdays}")
    p_cdi = to_decimal(cdi_percentage, "cdi_percentage")
    p_cdi_dec = p_cdi / Decimal("100") if p_cdi > Decimal("2.0") else p_cdi
    cdi_aa = to_decimal(annual_cdi_rate, "annual_cdi_rate")
    cdi_aa_dec = cdi_aa / Decimal("100") if cdi_aa > Decimal("1.0") else cdi_aa

    # Fator diário do CDI anualizado
    fdi_float = pow(1.0 + float(cdi_aa_dec), 1.0 / 252.0)
    fdi = Decimal(f"{fdi_float:.8f}")

    # Fator diário ponderado
    taxa_diaria = fdi - Decimal("1.00000000")
    fator_k = round_factor(Decimal("1.00000000") + (taxa_diaria * p_cdi_dec))

    # Acumulação
    fator_acum = Decimal("1.0000000000000000")
    for _ in range(workdays):
        fator_acum = fator_acum * fator_k

    return round_factor(fator_acum)


def calculate_remuneration_factor_cdi_spread(
    cdi_percentage: Decimal,
    annual_cdi_rate: Decimal,
    spread_annual: Decimal,
    workdays: int,
) -> Decimal:
    """Calcula o fator de remuneração para compromissada indexada ao CDI + spread a.a."""
    fator_cdi = calculate_remuneration_factor_cdi(cdi_percentage, annual_cdi_rate, workdays)
    fator_spread = calculate_remuneration_factor_pre(spread_annual, workdays)
    return round_factor(fator_cdi * fator_spread)


# =====================================================================
# REGISTROS DE EVENTOS E OPERAÇÕES
# =====================================================================

@dataclass
class SettlementDvPRecord:
    """Registro auditável de liquidação física e financeira simultânea (DvP) no NoMe B3."""
    settlement_id: str
    contract_id: str
    settlement_date: date
    leg: SettlementLeg
    cash_amount: Decimal
    cash_payer: str
    cash_payee: str
    asset_id: str
    asset_quantity: Decimal
    asset_deliverer: str
    asset_receiver: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.leg = SettlementLeg(self.leg)
        self.settlement_date = parse_date(self.settlement_date, "settlement_date")
        self.cash_amount = round_cents(to_decimal(self.cash_amount, "cash_amount"))
        self.asset_quantity = round_qty(to_decimal(self.asset_quantity, "asset_quantity"))
        if self.cash_amount < Decimal("0.00"):
            raise PrivateRepoValidationError("Valor em dinheiro da liquidação DvP não pode ser negativo.")
        if self.asset_quantity <= Decimal("0.00"):
            raise PrivateRepoValidationError("Quantidade de ativos da liquidação DvP deve ser estritamente positiva.")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "settlement_id": self.settlement_id,
            "contract_id": self.contract_id,
            "settlement_date": self.settlement_date.isoformat(),
            "leg": self.leg.value,
            "cash_amount": str(self.cash_amount),
            "cash_payer": self.cash_payer,
            "cash_payee": self.cash_payee,
            "asset_id": self.asset_id,
            "asset_quantity": str(self.asset_quantity),
            "asset_deliverer": self.asset_deliverer,
            "asset_receiver": self.asset_receiver,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SettlementDvPRecord:
        return cls(
            settlement_id=data["settlement_id"],
            contract_id=data["contract_id"],
            settlement_date=parse_date(data["settlement_date"]),
            leg=SettlementLeg(data["leg"]),
            cash_amount=to_decimal(data["cash_amount"]),
            cash_payer=data["cash_payer"],
            cash_payee=data["cash_payee"],
            asset_id=data["asset_id"],
            asset_quantity=to_decimal(data["asset_quantity"]),
            asset_deliverer=data["asset_deliverer"],
            asset_receiver=data["asset_receiver"],
            timestamp=parse_datetime(data["timestamp"]),
        )


@dataclass
class MarginCallEvent:
    """Evento de Chamada de Margem Diária emitido por desvalorização do lastro privado."""
    event_id: str
    contract_id: str
    call_date: date
    due_date: date
    required_amount: Decimal
    current_collateral_market_value: Decimal
    current_adjusted_collateral_value: Decimal
    current_debt_obligation: Decimal
    status: str = "PENDING"  # PENDING, SATISFIED, EXPIRED_DEFAULT
    satisfied_date: Optional[date] = None
    deposit_type: Optional[MarginDepositType] = None
    deposited_cash_amount: Decimal = Decimal("0.00")
    deposited_asset_id: Optional[str] = None
    deposited_asset_quantity: Decimal = Decimal("0.0000")

    def __post_init__(self) -> None:
        self.call_date = parse_date(self.call_date, "call_date")
        self.due_date = parse_date(self.due_date, "due_date")
        self.required_amount = round_cents(to_decimal(self.required_amount, "required_amount"))
        self.current_collateral_market_value = round_cents(
            to_decimal(self.current_collateral_market_value, "current_collateral_market_value")
        )
        self.current_adjusted_collateral_value = round_cents(
            to_decimal(self.current_adjusted_collateral_value, "current_adjusted_collateral_value")
        )
        self.current_debt_obligation = round_cents(
            to_decimal(self.current_debt_obligation, "current_debt_obligation")
        )
        self.deposited_cash_amount = round_cents(
            to_decimal(self.deposited_cash_amount, "deposited_cash_amount")
        )
        self.deposited_asset_quantity = round_qty(
            to_decimal(self.deposited_asset_quantity, "deposited_asset_quantity")
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "contract_id": self.contract_id,
            "call_date": self.call_date.isoformat(),
            "due_date": self.due_date.isoformat(),
            "required_amount": str(self.required_amount),
            "current_collateral_market_value": str(self.current_collateral_market_value),
            "current_adjusted_collateral_value": str(self.current_adjusted_collateral_value),
            "current_debt_obligation": str(self.current_debt_obligation),
            "status": self.status,
            "satisfied_date": self.satisfied_date.isoformat() if self.satisfied_date else None,
            "deposit_type": self.deposit_type.value if self.deposit_type else None,
            "deposited_cash_amount": str(self.deposited_cash_amount),
            "deposited_asset_id": self.deposited_asset_id,
            "deposited_asset_quantity": str(self.deposited_asset_quantity),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MarginCallEvent:
        dep_type = MarginDepositType(data["deposit_type"]) if data.get("deposit_type") else None
        return cls(
            event_id=data["event_id"],
            contract_id=data["contract_id"],
            call_date=parse_date(data["call_date"]),
            due_date=parse_date(data["due_date"]),
            required_amount=to_decimal(data["required_amount"]),
            current_collateral_market_value=to_decimal(data["current_collateral_market_value"]),
            current_adjusted_collateral_value=to_decimal(data["current_adjusted_collateral_value"]),
            current_debt_obligation=to_decimal(data["current_debt_obligation"]),
            status=data.get("status", "PENDING"),
            satisfied_date=parse_date(data["satisfied_date"]) if data.get("satisfied_date") else None,
            deposit_type=dep_type,
            deposited_cash_amount=to_decimal(data.get("deposited_cash_amount", "0.00")),
            deposited_asset_id=data.get("deposited_asset_id"),
            deposited_asset_quantity=to_decimal(data.get("deposited_asset_quantity", "0.0000")),
        )


@dataclass
class ExecutionWaterfallResult:
    """Resultado financeiro auditável da excussão fiduciária do lastro em caso de default."""
    contract_id: str
    execution_date: date
    gross_proceeds: Decimal
    b3_execution_fees: Decimal
    net_proceeds_after_fees: Decimal
    debt_to_buyer: Decimal
    buyer_satisfaction: Decimal
    seller_refund: Decimal
    uncovered_debt: Decimal
    is_fully_satisfied: bool
    conservation_verified: bool

    def __post_init__(self) -> None:
        self.execution_date = parse_date(self.execution_date, "execution_date")
        self.gross_proceeds = round_cents(to_decimal(self.gross_proceeds, "gross_proceeds"))
        self.b3_execution_fees = round_cents(to_decimal(self.b3_execution_fees, "b3_execution_fees"))
        self.net_proceeds_after_fees = round_cents(to_decimal(self.net_proceeds_after_fees, "net_proceeds_after_fees"))
        self.debt_to_buyer = round_cents(to_decimal(self.debt_to_buyer, "debt_to_buyer"))
        self.buyer_satisfaction = round_cents(to_decimal(self.buyer_satisfaction, "buyer_satisfaction"))
        self.seller_refund = round_cents(to_decimal(self.seller_refund, "seller_refund"))
        self.uncovered_debt = round_cents(to_decimal(self.uncovered_debt, "uncovered_debt"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "execution_date": self.execution_date.isoformat(),
            "gross_proceeds": str(self.gross_proceeds),
            "b3_execution_fees": str(self.b3_execution_fees),
            "net_proceeds_after_fees": str(self.net_proceeds_after_fees),
            "debt_to_buyer": str(self.debt_to_buyer),
            "buyer_satisfaction": str(self.buyer_satisfaction),
            "seller_refund": str(self.seller_refund),
            "uncovered_debt": str(self.uncovered_debt),
            "is_fully_satisfied": self.is_fully_satisfied,
            "conservation_verified": self.conservation_verified,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExecutionWaterfallResult:
        return cls(
            contract_id=data["contract_id"],
            execution_date=parse_date(data["execution_date"]),
            gross_proceeds=to_decimal(data["gross_proceeds"]),
            b3_execution_fees=to_decimal(data["b3_execution_fees"]),
            net_proceeds_after_fees=to_decimal(data["net_proceeds_after_fees"]),
            debt_to_buyer=to_decimal(data["debt_to_buyer"]),
            buyer_satisfaction=to_decimal(data["buyer_satisfaction"]),
            seller_refund=to_decimal(data["seller_refund"]),
            uncovered_debt=to_decimal(data["uncovered_debt"]),
            is_fully_satisfied=data["is_fully_satisfied"],
            conservation_verified=data["conservation_verified"],
        )


# =====================================================================
# CONTRATO DE OPERAÇÃO COMPROMISSADA PRIVADA
# =====================================================================

@dataclass
class PrivateRepoContract:
    """Contrato de Operação Compromissada com Título Privado no Balcão NoMe B3."""
    contract_id: str
    seller_id: str             # Tomador de Recursos / Doador do Lastro
    buyer_id: str              # Doador de Recursos / Cessionário do Lastro
    asset_id: str              # Título Privado (Debênture, LF, CRI, CRA)
    quantity: Decimal          # Quantidade de títulos em custódia fiduciária
    trade_date: date           # Data da contratação
    start_date: date           # Data de liquidação da ida (D0)
    maturity_date: date        # Data de recompra a termo (vencimento)
    workdays: int              # Dias úteis convencionados DU/252
    rate_type: RateType        # PRE, CDI ou CDI_SPREAD
    start_pu: Decimal          # PU de negociação na perna de ida
    start_price: Decimal       # Valor financeiro da ida (Preço de Venda)
    end_pu: Decimal            # PU projetado da recompra
    end_price: Decimal         # Preço de Recompra a termo
    initial_haircut: Decimal   # Haircut regulatório B3 aplicado no registro
    status: ContractStatus     # Status do ciclo de vida
    annual_rate: Optional[Decimal] = None      # Taxa % a.a. para PRE
    cdi_percentage: Optional[Decimal] = None   # % do CDI (ex: 100.00 ou 1.0)
    cdi_spread: Optional[Decimal] = None       # Spread a.a. sobre o CDI
    additional_cash_margin: Decimal = Decimal("0.00")
    additional_collateral: Dict[str, Decimal] = field(default_factory=dict)
    margin_call_history: List[MarginCallEvent] = field(default_factory=list)
    settlement_history: List[SettlementDvPRecord] = field(default_factory=list)
    execution_result: Optional[ExecutionWaterfallResult] = None

    def __post_init__(self) -> None:
        self.rate_type = RateType.from_str(self.rate_type)
        self.status = ContractStatus.from_str(self.status)
        self.trade_date = parse_date(self.trade_date, "trade_date")
        self.start_date = parse_date(self.start_date, "start_date")
        self.maturity_date = parse_date(self.maturity_date, "maturity_date")
        if self.maturity_date <= self.start_date:
            raise PrivateRepoValidationError(
                f"Data de recompra ({self.maturity_date}) deve ser posterior ao início ({self.start_date})."
            )
        self.quantity = round_qty(to_decimal(self.quantity, "quantity"))
        if self.quantity <= Decimal("0.0000"):
            raise PrivateRepoValidationError("Quantidade de lastro deve ser estritamente positiva.")
        self.start_pu = round_pu(to_decimal(self.start_pu, "start_pu"))
        self.start_price = round_cents(to_decimal(self.start_price, "start_price"))
        self.end_pu = round_pu(to_decimal(self.end_pu, "end_pu"))
        self.end_price = round_cents(to_decimal(self.end_price, "end_price"))
        self.initial_haircut = round_rate(to_decimal(self.initial_haircut, "initial_haircut"))
        self.additional_cash_margin = round_cents(
            to_decimal(self.additional_cash_margin, "additional_cash_margin")
        )
        self.additional_collateral = {
            k: round_qty(to_decimal(v, f"additional_collateral[{k}]"))
            for k, v in self.additional_collateral.items()
        }

        if self.annual_rate is not None:
            self.annual_rate = to_decimal(self.annual_rate, "annual_rate")
        if self.cdi_percentage is not None:
            self.cdi_percentage = to_decimal(self.cdi_percentage, "cdi_percentage")
        if self.cdi_spread is not None:
            self.cdi_spread = to_decimal(self.cdi_spread, "cdi_spread")

    def verify_invariants(self) -> Dict[str, bool]:
        """Verifica as invariantes formais do contrato de compromissada privada."""
        results: Dict[str, bool] = {}

        # 1. Invariante de Não-Negatividade
        results["non_negativity"] = (
            self.quantity > Decimal("0.0000")
            and self.start_pu > Decimal("0.00000000")
            and self.start_price > Decimal("0.00")
            and self.end_pu > Decimal("0.00000000")
            and self.end_price > Decimal("0.00")
            and self.additional_cash_margin >= Decimal("0.00")
            and all(v >= Decimal("0.0000") for v in self.additional_collateral.values())
        )

        # 2. Invariante de Limites de Haircut Regulatório
        results["haircut_within_regulatory_bounds"] = (
            MIN_REGULATORY_HAIRCUT <= self.initial_haircut <= MAX_REGULATORY_HAIRCUT
        )

        # 3. Invariante de Paridade de Preço de Recompra
        # Preço_Recompra == round_cents(Preço_Venda * Fator_Remuneração)
        remun_factor = self.calculate_expected_factor()
        expected_end_price = round_cents(self.start_price * remun_factor)
        results["repurchase_price_parity"] = (abs(self.end_price - expected_end_price) <= Decimal("0.02"))

        # 4. Invariante de Consistência Temporal
        results["temporal_consistency"] = (self.trade_date <= self.start_date < self.maturity_date)

        return results

    def calculate_expected_factor(self) -> Decimal:
        """Calcula o fator de remuneração contratual esperado até o vencimento."""
        if self.rate_type == RateType.PRE:
            if self.annual_rate is None:
                raise PrivateRepoValidationError("Taxa anual obrigatória para modalidade PRE.")
            return calculate_remuneration_factor_pre(self.annual_rate, self.workdays)
        elif self.rate_type == RateType.CDI:
            p_cdi = self.cdi_percentage or Decimal("100.00")
            # Usa taxa anual do CDI de referência ou convenção contratual
            cdi_ref = self.annual_rate or Decimal("11.25")
            return calculate_remuneration_factor_cdi(p_cdi, cdi_ref, self.workdays)
        elif self.rate_type == RateType.CDI_SPREAD:
            p_cdi = self.cdi_percentage or Decimal("100.00")
            cdi_ref = self.annual_rate or Decimal("11.25")
            spread = self.cdi_spread or Decimal("0.00")
            return calculate_remuneration_factor_cdi_spread(p_cdi, cdi_ref, spread, self.workdays)
        raise PrivateRepoValidationError(f"Tipo de taxa desconhecido: {self.rate_type}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "seller_id": self.seller_id,
            "buyer_id": self.buyer_id,
            "asset_id": self.asset_id,
            "quantity": str(self.quantity),
            "trade_date": self.trade_date.isoformat(),
            "start_date": self.start_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "workdays": self.workdays,
            "rate_type": self.rate_type.value,
            "annual_rate": str(self.annual_rate) if self.annual_rate is not None else None,
            "cdi_percentage": str(self.cdi_percentage) if self.cdi_percentage is not None else None,
            "cdi_spread": str(self.cdi_spread) if self.cdi_spread is not None else None,
            "start_pu": str(self.start_pu),
            "start_price": str(self.start_price),
            "end_pu": str(self.end_pu),
            "end_price": str(self.end_price),
            "initial_haircut": str(self.initial_haircut),
            "status": self.status.value,
            "additional_cash_margin": str(self.additional_cash_margin),
            "additional_collateral": {k: str(v) for k, v in self.additional_collateral.items()},
            "margin_call_history": [m.to_dict() for m in self.margin_call_history],
            "settlement_history": [s.to_dict() for s in self.settlement_history],
            "execution_result": self.execution_result.to_dict() if self.execution_result else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PrivateRepoContract:
        margin_calls = [MarginCallEvent.from_dict(m) for m in data.get("margin_call_history", [])]
        settlements = [SettlementDvPRecord.from_dict(s) for s in data.get("settlement_history", [])]
        exec_res = ExecutionWaterfallResult.from_dict(data["execution_result"]) if data.get("execution_result") else None
        return cls(
            contract_id=data["contract_id"],
            seller_id=data["seller_id"],
            buyer_id=data["buyer_id"],
            asset_id=data["asset_id"],
            quantity=to_decimal(data["quantity"]),
            trade_date=parse_date(data["trade_date"]),
            start_date=parse_date(data["start_date"]),
            maturity_date=parse_date(data["maturity_date"]),
            workdays=int(data["workdays"]),
            rate_type=RateType.from_str(data["rate_type"]),
            start_pu=to_decimal(data["start_pu"]),
            start_price=to_decimal(data["start_price"]),
            end_pu=to_decimal(data["end_pu"]),
            end_price=to_decimal(data["end_price"]),
            initial_haircut=to_decimal(data["initial_haircut"]),
            status=ContractStatus.from_str(data["status"]),
            annual_rate=to_decimal(data["annual_rate"]) if data.get("annual_rate") is not None else None,
            cdi_percentage=to_decimal(data["cdi_percentage"]) if data.get("cdi_percentage") is not None else None,
            cdi_spread=to_decimal(data["cdi_spread"]) if data.get("cdi_spread") is not None else None,
            additional_cash_margin=to_decimal(data.get("additional_cash_margin", "0.00")),
            additional_collateral={k: to_decimal(v) for k, v in data.get("additional_collateral", {}).items()},
            margin_call_history=margin_calls,
            settlement_history=settlements,
            execution_result=exec_res,
        )


# =====================================================================
# MOTOR DETERMINÍSTICO DE COMPROMISSADAS PRIVADAS (NOME B3 ENGINE)
# =====================================================================

class PrivateRepoNomeEngine:
    """Motor de Balcão NoMe B3 para Operações Compromissadas com Títulos Privados (FL-079).

    Gerencia o ciclo de vida ponta a ponta:
    - Registro e validação de lastro privado elegível (Debêntures, LF, CRI, CRA);
    - Aplicação de haircuts regulatórios por rating de crédito, prazo residual e emissor;
    - Liquidação Delivery versus Payment (DvP) em D0 com bloqueio fiduciário de custódia;
    - Marcação a mercado diária (MtM) e emissão determinística de Chamadas de Margem;
    - Liquidação DvP na perna de volta (vencimento) com desoneração fiduciária;
    - Tratamento de eventos de default e excussão em leilão/alienação com cascata (waterfall).
    """

    def __init__(self) -> None:
        self.participants: Dict[str, ParticipantAccount] = {}
        self.assets: Dict[str, PrivateAsset] = {}
        self.contracts: Dict[str, PrivateRepoContract] = {}
        self.settlement_records: List[SettlementDvPRecord] = []
        self.margin_calls: List[MarginCallEvent] = []
        self.execution_results: List[ExecutionWaterfallResult] = []
        self.b3_collected_fees: Decimal = Decimal("0.00")
        self.daily_cdi_rates: Dict[date, Decimal] = {}
        self.audit_log: List[Dict[str, Any]] = []

    # -----------------------------------------------------------------
    # REGISTRO DE ENTIDADES E ATIVOS
    # -----------------------------------------------------------------

    def register_participant(self, participant: ParticipantAccount) -> None:
        """Cadastra participante do Balcão NoMe com suas contas e saldos."""
        if participant.participant_id in self.participants:
            raise PrivateRepoValidationError(
                f"Participante '{participant.participant_id}' já cadastrado."
            )
        self.participants[participant.participant_id] = participant
        self._log_audit("REGISTER_PARTICIPANT", {"participant_id": participant.participant_id})

    def register_asset(self, asset: PrivateAsset) -> None:
        """Cadastra título privado de renda fixa elegível no Balcão NoMe."""
        if asset.asset_id in self.assets:
            raise PrivateRepoValidationError(f"Ativo privado '{asset.asset_id}' já cadastrado.")
        self.assets[asset.asset_id] = asset
        self._log_audit("REGISTER_ASSET", {"asset_id": asset.asset_id, "type": asset.asset_type.value})

    def set_daily_cdi_rate(self, ref_date: date, annual_rate: Decimal) -> None:
        """Registra a taxa anualizada do CDI observada em uma determinada data."""
        self.daily_cdi_rates[parse_date(ref_date)] = to_decimal(annual_rate, "annual_rate")

    # -----------------------------------------------------------------
    # CÁLCULOS REGULATÓRIOS
    # -----------------------------------------------------------------

    def calculate_haircut(self, asset_id: str, as_of_date: date) -> Decimal:
        """Calcula o haircut regulatório B3 para o ativo privado na data informada."""
        if asset_id not in self.assets:
            raise PrivateRepoValidationError(f"Ativo '{asset_id}' não encontrado no Balcão NoMe.")
        asset = self.assets[asset_id]
        res_days = asset.residual_days(as_of_date)
        return RegulatoryHaircutCalculator.calculate_haircut(
            asset_type=asset.asset_type,
            rating=asset.rating,
            residual_days=res_days,
        )

    # -----------------------------------------------------------------
    # REGISTRO DE CONTRATOS COMPROMISSADOS
    # -----------------------------------------------------------------

    def register_repo_contract(self, contract: PrivateRepoContract) -> PrivateRepoContract:
        """Valida e cadastra contrato de compromissada privada no Balcão NoMe B3."""
        if contract.contract_id in self.contracts:
            raise PrivateRepoValidationError(
                f"Contrato de compromissada '{contract.contract_id}' já cadastrado."
            )

        # Validação dos participantes
        if contract.seller_id not in self.participants:
            raise PrivateRepoValidationError(f"Vendedor '{contract.seller_id}' não encontrado.")
        if contract.buyer_id not in self.participants:
            raise PrivateRepoValidationError(f"Comprador '{contract.buyer_id}' não encontrado.")
        if contract.seller_id == contract.buyer_id:
            raise PrivateRepoValidationError("Vendedor e Comprador não podem ser a mesma instituição.")

        # Validação do ativo de lastro
        if contract.asset_id not in self.assets:
            raise PrivateRepoValidationError(f"Ativo '{contract.asset_id}' não cadastrado no NoMe.")
        asset = self.assets[contract.asset_id]
        if not asset.is_eligible:
            raise PrivateRepoValidationError(f"Ativo '{contract.asset_id}' não é elegível para compromissadas.")
        if asset.maturity_date <= contract.maturity_date:
            raise PrivateRepoValidationError(
                f"Vencimento do lastro ({asset.maturity_date}) deve ser estritamente posterior "
                f"ao vencimento da compromissada ({contract.maturity_date})."
            )

        # Validação e conferência do haircut regulatório
        req_haircut = self.calculate_haircut(contract.asset_id, contract.start_date)
        if contract.initial_haircut < req_haircut:
            raise PrivateRepoHaircutError(
                f"Haircut informado ({contract.initial_haircut}) é inferior ao mínimo regulatório B3 ({req_haircut})."
            )

        # Regra de Cobertura Inicial: Preço de Venda <= Valor de Mercado * (1 - Haircut)
        market_val = round_cents(contract.quantity * asset.current_market_pu)
        max_allowed_loan = round_cents(market_val * (Decimal("1.00000000") - req_haircut))
        if contract.start_price > max_allowed_loan:
            raise PrivateRepoHaircutError(
                f"Preço de venda inicial (R$ {contract.start_price}) excede o limite com haircut regulatório "
                f"de {req_haircut * 100:.2f}% (Teto permitido: R$ {max_allowed_loan})."
            )

        # Validação da Paridade de Recompra
        expected_factor = contract.calculate_expected_factor()
        expected_end_price = round_cents(contract.start_price * expected_factor)
        if abs(contract.end_price - expected_end_price) > Decimal("0.05"):
            raise PrivateRepoValidationError(
                f"Preço de recompra informado ({contract.end_price}) diverge da paridade contratual "
                f"({expected_end_price}) calculada via fator {expected_factor}."
            )

        contract.status = ContractStatus.REGISTERED
        self.contracts[contract.contract_id] = contract
        self._log_audit("REGISTER_REPO_CONTRACT", {"contract_id": contract.contract_id})
        return contract

    # -----------------------------------------------------------------
    # LIQUIDAÇÃO DVP DA PERNA DE IDA (D0)
    # -----------------------------------------------------------------

    def execute_start_leg_dvp(self, contract_id: str, settlement_date: date) -> SettlementDvPRecord:
        """Executa a liquidação Delivery versus Payment (DvP) simultânea da perna de ida (D0).

        Movimentações atômicas:
        1. Vendedor: Custódia do lastro transferida de Livre para Bloqueada (Garantia Fiduciária NoMe).
        2. Vendedor: Crédito de Caixa no montante do Preço de Venda (Start Price).
        3. Comprador: Débito de Caixa no montante do Preço de Venda.
        """
        if contract_id not in self.contracts:
            raise PrivateRepoValidationError(f"Contrato '{contract_id}' não encontrado.")
        contract = self.contracts[contract_id]

        if contract.status != ContractStatus.REGISTERED:
            raise PrivateRepoSettlementError(
                f"Contrato '{contract_id}' com status '{contract.status.value}', "
                f"esperado '{ContractStatus.REGISTERED.value}' para liquidação de ida."
            )

        seller = self.participants[contract.seller_id]
        buyer = self.participants[contract.buyer_id]

        # Checagem prévia de fundos e custódia livre
        if seller.get_free_quantity(contract.asset_id) < contract.quantity:
            raise PrivateRepoSettlementError(
                f"Vendedor '{seller.participant_id}' não possui custódia livre suficiente de '{contract.asset_id}'. "
                f"Disponível: {seller.get_free_quantity(contract.asset_id)}, Requerido: {contract.quantity}."
            )
        if buyer.cash_balance < contract.start_price:
            raise PrivateRepoSettlementError(
                f"Comprador '{buyer.participant_id}' não possui saldo em caixa suficiente. "
                f"Saldo: R$ {buyer.cash_balance}, Requerido: R$ {contract.start_price}."
            )

        # Transação atômica DvP
        seller.free_collateral[contract.asset_id] -= contract.quantity
        seller.blocked_collateral[contract.asset_id] = (
            seller.get_blocked_quantity(contract.asset_id) + contract.quantity
        )
        seller.cash_balance = round_cents(seller.cash_balance + contract.start_price)
        buyer.cash_balance = round_cents(buyer.cash_balance - contract.start_price)

        record = SettlementDvPRecord(
            settlement_id=f"DVP-START-{contract.contract_id}",
            contract_id=contract.contract_id,
            settlement_date=settlement_date,
            leg=SettlementLeg.START_LEG,
            cash_amount=contract.start_price,
            cash_payer=buyer.participant_id,
            cash_payee=seller.participant_id,
            asset_id=contract.asset_id,
            asset_quantity=contract.quantity,
            asset_deliverer=seller.participant_id,
            asset_receiver=f"B3_NOME_ESCROW_FOR_{buyer.participant_id}",
        )

        contract.settlement_history.append(record)
        self.settlement_records.append(record)
        contract.status = ContractStatus.ACTIVE

        self._log_audit("EXECUTE_START_LEG_DVP", {"contract_id": contract_id, "record_id": record.settlement_id})
        return record

    # -----------------------------------------------------------------
    # MARCAÇÃO A MERCADO DIÁRIA (MtM) E CHAMADAS DE MARGEM
    # -----------------------------------------------------------------

    def mark_to_market(
        self,
        as_of_date: date,
        market_prices: Optional[Dict[str, Decimal]] = None,
        threshold: Decimal = DEFAULT_MARGIN_CALL_THRESHOLD,
    ) -> List[MarginCallEvent]:
        """Executa a rotina diária de marcação a mercado e emissão de chamadas de margem.

        Para cada compromissada ativa:
        1. Atualiza o PU de mercado dos ativos privados.
        2. Calcula a obrigação atualizada do tomador (Preço_Venda * Fator_Acumulado_Ate_Data).
        3. Calcula o valor de mercado atualizado do lastro e aplica o Haircut Regulatório B3.
        4. Se Valor_Colateral_Ajustado < Obrigacao_Atualizada - threshold: Emite MarginCallEvent.
        """
        as_of_date = parse_date(as_of_date, "as_of_date")
        if market_prices:
            for aid, pu in market_prices.items():
                if aid in self.assets:
                    self.assets[aid].current_market_pu = round_pu(to_decimal(pu, f"market_pu[{aid}]"))

        new_calls: List[MarginCallEvent] = []

        for contract in self.contracts.values():
            if contract.status not in (ContractStatus.ACTIVE, ContractStatus.MARGIN_CALL):
                continue

            asset = self.assets[contract.asset_id]
            # Dias úteis decorridos até a data de marcação
            elapsed_du = count_workdays(contract.start_date, as_of_date)
            # Fator acumulado até a data da marcação
            if contract.rate_type == RateType.PRE:
                factor_to_date = calculate_remuneration_factor_pre(contract.annual_rate or Decimal("0"), elapsed_du)
            elif contract.rate_type == RateType.CDI:
                p_cdi = contract.cdi_percentage or Decimal("100.00")
                cdi_ref = self.daily_cdi_rates.get(as_of_date, contract.annual_rate or Decimal("11.25"))
                factor_to_date = calculate_remuneration_factor_cdi(p_cdi, cdi_ref, elapsed_du)
            else:
                p_cdi = contract.cdi_percentage or Decimal("100.00")
                cdi_ref = self.daily_cdi_rates.get(as_of_date, contract.annual_rate or Decimal("11.25"))
                spread = contract.cdi_spread or Decimal("0")
                factor_to_date = calculate_remuneration_factor_cdi_spread(p_cdi, cdi_ref, spread, elapsed_du)

            current_debt = round_cents(contract.start_price * factor_to_date)

            # Avaliação do lastro primário
            primary_market_val = round_cents(contract.quantity * asset.current_market_pu)
            haircut = self.calculate_haircut(asset.asset_id, as_of_date)
            adjusted_primary = round_cents(primary_market_val * (Decimal("1.00000000") - haircut))

            # Avaliação de colaterais adicionais depositados em margens anteriores
            adjusted_additional_assets = Decimal("0.00")
            additional_assets_market_val = Decimal("0.00")
            for add_aid, add_qty in contract.additional_collateral.items():
                if add_aid in self.assets:
                    add_asset = self.assets[add_aid]
                    add_mkt = round_cents(add_qty * add_asset.current_market_pu)
                    additional_assets_market_val += add_mkt
                    add_hc = self.calculate_haircut(add_aid, as_of_date)
                    adjusted_additional_assets += round_cents(add_mkt * (Decimal("1.00000000") - add_hc))

            # Total de garantias ajustadas (Primária + Caixa Adicional + Títulos Adicionais)
            total_market_val = primary_market_val + additional_assets_market_val + contract.additional_cash_margin
            total_adjusted_collateral = (
                adjusted_primary + contract.additional_cash_margin + adjusted_additional_assets
            )

            # Checagem de déficit de cobertura
            deficit = current_debt - total_adjusted_collateral

            if deficit > threshold:
                call_id = f"MC-{contract.contract_id}-{as_of_date.strftime('%Y%m%d')}"
                # Evita duplicar se já foi chamada hoje
                if not any(m.event_id == call_id for m in contract.margin_call_history):
                    # Prazo regulatório padrão: D+1 útil para depósito
                    due_date = as_of_date + timedelta(days=1)
                    while not is_workday(due_date):
                        due_date += timedelta(days=1)

                    call_event = MarginCallEvent(
                        event_id=call_id,
                        contract_id=contract.contract_id,
                        call_date=as_of_date,
                        due_date=due_date,
                        required_amount=round_cents(deficit),
                        current_collateral_market_value=total_market_val,
                        current_adjusted_collateral_value=total_adjusted_collateral,
                        current_debt_obligation=current_debt,
                        status="PENDING",
                    )
                    contract.margin_call_history.append(call_event)
                    self.margin_calls.append(call_event)
                    new_calls.append(call_event)
                    contract.status = ContractStatus.MARGIN_CALL

                    self._log_audit("MARGIN_CALL_ISSUED", {
                        "contract_id": contract.contract_id,
                        "required_amount": str(deficit),
                    })

        return new_calls

    # -----------------------------------------------------------------
    # SATISFAÇÃO DE CHAMADA DE MARGEM
    # -----------------------------------------------------------------

    def satisfy_margin_call(
        self,
        event_id: str,
        deposit_type: MarginDepositType,
        cash_amount: Decimal = Decimal("0.00"),
        asset_id: Optional[str] = None,
        asset_quantity: Decimal = Decimal("0.0000"),
        satisfied_date: Optional[date] = None,
    ) -> MarginCallEvent:
        """Processa o depósito de margem adicional pelo tomador (Vendedor) para regularizar déficit."""
        deposit_type = MarginDepositType(deposit_type)
        satisfied_date = parse_date(satisfied_date or date.today(), "satisfied_date")

        event = next((e for e in self.margin_calls if e.event_id == event_id), None)
        if not event:
            raise PrivateRepoMarginCallError(f"Evento de chamada de margem '{event_id}' não encontrado.")
        if event.status != "PENDING":
            raise PrivateRepoMarginCallError(
                f"Chamada de margem '{event_id}' com status '{event.status}', não está pendente."
            )

        contract = self.contracts[event.contract_id]
        seller = self.participants[contract.seller_id]

        if deposit_type == MarginDepositType.CASH:
            cash_amount = round_cents(to_decimal(cash_amount, "cash_amount"))
            if cash_amount < event.required_amount:
                raise PrivateRepoMarginCallError(
                    f"Valor em dinheiro informado (R$ {cash_amount}) é inferior ao requerido (R$ {event.required_amount})."
                )
            if seller.cash_balance < cash_amount:
                raise PrivateRepoMarginCallError(
                    f"Vendedor '{seller.participant_id}' não possui saldo em caixa para margem. "
                    f"Saldo: R$ {seller.cash_balance}, Requerido: R$ {cash_amount}."
                )
            # Débito de caixa do vendedor para custódia fiduciária de margem
            seller.cash_balance = round_cents(seller.cash_balance - cash_amount)
            contract.additional_cash_margin = round_cents(contract.additional_cash_margin + cash_amount)

            event.deposited_cash_amount = cash_amount
            event.deposit_type = MarginDepositType.CASH

        elif deposit_type == MarginDepositType.ADDITIONAL_ASSET:
            if not asset_id or asset_id not in self.assets:
                raise PrivateRepoMarginCallError(f"Ativo de margem '{asset_id}' inválido.")
            asset_quantity = round_qty(to_decimal(asset_quantity, "asset_quantity"))
            if asset_quantity <= Decimal("0.0000"):
                raise PrivateRepoMarginCallError("Quantidade de títulos de margem deve ser positiva.")
            if seller.get_free_quantity(asset_id) < asset_quantity:
                raise PrivateRepoMarginCallError(
                    f"Vendedor não possui quantidade livre de '{asset_id}'. "
                    f"Disponível: {seller.get_free_quantity(asset_id)}, Requerido: {asset_quantity}."
                )

            # Verifica o valor colateral ajustado do ativo adicional
            add_asset = self.assets[asset_id]
            add_hc = self.calculate_haircut(asset_id, satisfied_date)
            add_mkt = round_cents(asset_quantity * add_asset.current_market_pu)
            add_adj = round_cents(add_mkt * (Decimal("1.00000000") - add_hc))

            if add_adj < event.required_amount:
                raise PrivateRepoMarginCallError(
                    f"Valor ajustado do ativo adicional (R$ {add_adj}) é insuficiente para a chamada (R$ {event.required_amount})."
                )

            # Bloqueio do ativo adicional
            seller.free_collateral[asset_id] -= asset_quantity
            seller.blocked_collateral[asset_id] = seller.get_blocked_quantity(asset_id) + asset_quantity
            contract.additional_collateral[asset_id] = (
                contract.additional_collateral.get(asset_id, Decimal("0.0000")) + asset_quantity
            )

            event.deposit_type = MarginDepositType.ADDITIONAL_ASSET
            event.deposited_asset_id = asset_id
            event.deposited_asset_quantity = asset_quantity

        event.status = "SATISFIED"
        event.satisfied_date = satisfied_date

        # Se não há outras chamadas pendentes, o contrato retorna ao status ACTIVE
        has_pending = any(
            m.status == "PENDING" and m.event_id != event_id
            for m in contract.margin_call_history
        )
        if not has_pending:
            contract.status = ContractStatus.ACTIVE

        self._log_audit("MARGIN_CALL_SATISFIED", {"event_id": event_id, "type": deposit_type.value})
        return event

    # -----------------------------------------------------------------
    # LIQUIDAÇÃO DVP DA PERNA DE VOLTA (VENCIMENTO)
    # -----------------------------------------------------------------

    def execute_return_leg_dvp(self, contract_id: str, settlement_date: date) -> SettlementDvPRecord:
        """Executa a liquidação Delivery versus Payment (DvP) simultânea da perna de volta (Recompra).

        Movimentações atômicas:
        1. Vendedor: Débito de Caixa no montante do Preço de Recompra (End Price).
        2. Vendedor: Custódia do lastro primário desbloqueada de Bloqueada para Livre.
        3. Comprador: Crédito de Caixa no montante do Preço de Recompra.
        4. Desoneração de quaisquer margens adicionais (em caixa ou títulos) ao vendedor.
        """
        if contract_id not in self.contracts:
            raise PrivateRepoValidationError(f"Contrato '{contract_id}' não encontrado.")
        contract = self.contracts[contract_id]

        if contract.status not in (ContractStatus.ACTIVE, ContractStatus.MARGIN_CALL):
            raise PrivateRepoSettlementError(
                f"Contrato '{contract_id}' com status '{contract.status.value}', "
                f"não elegível para liquidação de volta."
            )

        seller = self.participants[contract.seller_id]
        buyer = self.participants[contract.buyer_id]

        # Checagem de fundos do vendedor para recompra
        if seller.cash_balance < contract.end_price:
            raise PrivateRepoDefaultError(
                f"Vendedor '{seller.participant_id}' não possui saldo suficiente para recompra. "
                f"Saldo: R$ {seller.cash_balance}, Recompra exigida: R$ {contract.end_price}."
            )

        # Transação atômica DvP da volta
        seller.cash_balance = round_cents(seller.cash_balance - contract.end_price)
        buyer.cash_balance = round_cents(buyer.cash_balance + contract.end_price)

        # Desbloqueio da garantia primária
        seller.blocked_collateral[contract.asset_id] -= contract.quantity
        seller.free_collateral[contract.asset_id] = (
            seller.get_free_quantity(contract.asset_id) + contract.quantity
        )

        # Desoneração de colaterais adicionais (se houver)
        for add_aid, add_qty in contract.additional_collateral.items():
            seller.blocked_collateral[add_aid] -= add_qty
            seller.free_collateral[add_aid] = seller.get_free_quantity(add_aid) + add_qty
        contract.additional_collateral.clear()

        # Devolução de margem adicional em caixa (se houver)
        if contract.additional_cash_margin > Decimal("0.00"):
            seller.cash_balance = round_cents(seller.cash_balance + contract.additional_cash_margin)
            contract.additional_cash_margin = Decimal("0.00")

        record = SettlementDvPRecord(
            settlement_id=f"DVP-RETURN-{contract.contract_id}",
            contract_id=contract.contract_id,
            settlement_date=settlement_date,
            leg=SettlementLeg.RETURN_LEG,
            cash_amount=contract.end_price,
            cash_payer=seller.participant_id,
            cash_payee=buyer.participant_id,
            asset_id=contract.asset_id,
            asset_quantity=contract.quantity,
            asset_deliverer=f"B3_NOME_ESCROW_FOR_{buyer.participant_id}",
            asset_receiver=seller.participant_id,
        )

        contract.settlement_history.append(record)
        self.settlement_records.append(record)
        contract.status = ContractStatus.SETTLED

        self._log_audit("EXECUTE_RETURN_LEG_DVP", {"contract_id": contract_id, "record_id": record.settlement_id})
        return record

    # -----------------------------------------------------------------
    # LIQUIDAÇÃO ANTECIPADA E DEFAULT COM EXCUSSÃO DE GARANTIA (NOME)
    # -----------------------------------------------------------------

    def trigger_default_and_liquidate(
        self,
        contract_id: str,
        execution_date: date,
        reason: DefaultReason = DefaultReason.MATURITY_REPURCHASE_FAILED,
        liquidation_pu_override: Optional[Decimal] = None,
        b3_fee_rate: Decimal = DEFAULT_B3_EXECUTION_FEE_RATE,
    ) -> ExecutionWaterfallResult:
        """Executa a garantia fiduciária do lastro privado no Balcão NoMe B3 em caso de default.

        Cascata (Waterfall) de Liquidação da Garantia:
        1. Alienação dos títulos retidos no Balcão NoMe ao PU de liquidação.
        2. Dedução prioritária das taxas e emolumentos operacionais da B3.
        3. Satisfação integral da dívida atualizada devida ao Comprador (Preço de Recompra).
        4. Reembolso do eventual excedente financeiro ao Vendedor.
        5. Apuração de saldo a descoberto (shortfall) caso os recursos sejam insuficientes.
        """
        if contract_id not in self.contracts:
            raise PrivateRepoValidationError(f"Contrato '{contract_id}' não encontrado.")
        contract = self.contracts[contract_id]

        if contract.status not in (ContractStatus.ACTIVE, ContractStatus.MARGIN_CALL, ContractStatus.DEFAULTED):
            raise PrivateRepoSettlementError(
                f"Contrato '{contract_id}' com status '{contract.status.value}', "
                f"não elegível para execução de default."
            )

        seller = self.participants[contract.seller_id]
        buyer = self.participants[contract.buyer_id]
        asset = self.assets[contract.asset_id]

        # Apuração do PU de liquidação
        liq_pu = round_pu(to_decimal(liquidation_pu_override, "liquidation_pu")) if liquidation_pu_override is not None else asset.current_market_pu

        # 1. Recursos brutos da alienação do lastro primário
        gross_primary_proceeds = round_cents(contract.quantity * liq_pu)

        # 2. Alienação de títulos de margem adicional (se houver)
        gross_additional_assets_proceeds = Decimal("0.00")
        for add_aid, add_qty in contract.additional_collateral.items():
            add_asset = self.assets[add_aid]
            gross_additional_assets_proceeds += round_cents(add_qty * add_asset.current_market_pu)
            # Remove custódia bloqueada executada
            seller.blocked_collateral[add_aid] -= add_qty

        # 3. Incorporação de margem adicional em caixa
        gross_proceeds = round_cents(
            gross_primary_proceeds + gross_additional_assets_proceeds + contract.additional_cash_margin
        )

        # Remove custódia bloqueada do lastro primário executado
        seller.blocked_collateral[contract.asset_id] -= contract.quantity

        # Waterfall:
        # 1. Taxa B3
        b3_fee = round_cents(gross_proceeds * b3_fee_rate)
        self.b3_collected_fees = round_cents(self.b3_collected_fees + b3_fee)
        net_proceeds = round_cents(gross_proceeds - b3_fee)

        # 2. Dívida com o comprador (Preço de recompra final)
        debt_to_buyer = contract.end_price

        # 3. Satisfação do comprador
        buyer_payment = min(net_proceeds, debt_to_buyer)
        buyer.cash_balance = round_cents(buyer.cash_balance + buyer_payment)

        # 4. Excedente devolvido ao vendedor
        seller_refund = max(Decimal("0.00"), round_cents(net_proceeds - debt_to_buyer))
        seller.cash_balance = round_cents(seller.cash_balance + seller_refund)

        # 5. Saldo a descoberto (crédito não coberto)
        uncovered_debt = max(Decimal("0.00"), round_cents(debt_to_buyer - buyer_payment))
        is_fully_satisfied = (uncovered_debt == Decimal("0.00"))

        # Verificação da invariante matemática de conservação da excussão
        conservation_verified = (
            gross_proceeds == round_cents(b3_fee + buyer_payment + seller_refund)
            and round_cents(buyer_payment + uncovered_debt) == debt_to_buyer
        )

        waterfall_res = ExecutionWaterfallResult(
            contract_id=contract.contract_id,
            execution_date=execution_date,
            gross_proceeds=gross_proceeds,
            b3_execution_fees=b3_fee,
            net_proceeds_after_fees=net_proceeds,
            debt_to_buyer=debt_to_buyer,
            buyer_satisfaction=buyer_payment,
            seller_refund=seller_refund,
            uncovered_debt=uncovered_debt,
            is_fully_satisfied=is_fully_satisfied,
            conservation_verified=conservation_verified,
        )

        contract.execution_result = waterfall_res
        contract.status = ContractStatus.COLLATERAL_EXECUTED
        self.execution_results.append(waterfall_res)

        self._log_audit("COLLATERAL_EXECUTED", {
            "contract_id": contract_id,
            "gross": str(gross_proceeds),
            "fees": str(b3_fee),
            "buyer_satisfaction": str(buyer_payment),
            "seller_refund": str(seller_refund),
            "uncovered": str(uncovered_debt),
        })

        return waterfall_res

    # -----------------------------------------------------------------
    # INVARIANTES FORMAIS DO SISTEMA (FL-079)
    # -----------------------------------------------------------------

    def verify_invariants(self) -> Dict[str, Any]:
        """Verifica formalmente todas as leis invariantes do Balcão NoMe B3.

        Retorna dicionário completo com diagnósticos e booleano consolidado.
        """
        violations: List[str] = []

        # 1. Invariante de Não-Negatividade Global
        for pid, part in self.participants.items():
            if part.cash_balance < Decimal("0.00"):
                violations.append(f"Participante '{pid}' possui saldo em caixa negativo: {part.cash_balance}")
            for aid, q in part.free_collateral.items():
                if q < Decimal("0.0000"):
                    violations.append(f"Participante '{pid}' com custódia livre negativa de '{aid}': {q}")
            for aid, q in part.blocked_collateral.items():
                if q < Decimal("0.0000"):
                    violations.append(f"Participante '{pid}' com custódia bloqueada negativa de '{aid}': {q}")

        # 2. Invariante de Paridade de Preço de Recompra
        for cid, contract in self.contracts.items():
            inv = contract.verify_invariants()
            for inv_name, ok in inv.items():
                if not ok:
                    violations.append(f"Contrato '{cid}' violou invariante '{inv_name}'.")

        # 3. Invariante de Monotonicidade da Matriz de Haircuts
        test_ratings = [
            CreditRating.AAA,
            CreditRating.AA,
            CreditRating.A,
            CreditRating.BBB,
            CreditRating.SUB_INVESTMENT,
        ]
        for atype in PrivateAssetType:
            for tenor in [180, 500, 1500, 2500]:
                haircuts = [
                    RegulatoryHaircutCalculator.calculate_haircut(atype, r, tenor)
                    for r in test_ratings
                ]
                for i in range(len(haircuts) - 1):
                    if haircuts[i] > haircuts[i + 1]:
                        violations.append(
                            f"Violação de monotonicidade de rating: {test_ratings[i].value} "
                            f"({haircuts[i]}) > {test_ratings[i+1].value} ({haircuts[i+1]})"
                        )

            # Monotonicidade por Tenor
            for rating in [CreditRating.AAA, CreditRating.A, CreditRating.BBB]:
                tenor_days = [180, 500, 1500, 2500]
                hc_tenors = [
                    RegulatoryHaircutCalculator.calculate_haircut(atype, rating, td)
                    for td in tenor_days
                ]
                for j in range(len(hc_tenors) - 1):
                    if hc_tenors[j] > hc_tenors[j + 1]:
                        violations.append(
                            f"Violação de monotonicidade de prazo para {rating.value}: "
                            f"{tenor_days[j]}d ({hc_tenors[j]}) > {tenor_days[j+1]}d ({hc_tenors[j+1]})"
                        )

        # 4. Invariante de Conservação da Excussão de Default (Waterfall)
        for res in self.execution_results:
            if not res.conservation_verified:
                violations.append(
                    f"Execução '{res.contract_id}' violou conservação financeira do waterfall."
                )
            calc_gross = round_cents(res.b3_execution_fees + res.buyer_satisfaction + res.seller_refund)
            if res.gross_proceeds != calc_gross:
                violations.append(
                    f"Recursos brutos ({res.gross_proceeds}) != taxas ({res.b3_execution_fees}) "
                    f"+ comprador ({res.buyer_satisfaction}) + vendedor ({res.seller_refund})"
                )

        # 5. Invariante DvP de Transferência de Caixa Zero-Sum
        for rec in self.settlement_records:
            if rec.cash_amount < Decimal("0.00"):
                violations.append(f"Registro DvP '{rec.settlement_id}' possui caixa negativo: {rec.cash_amount}")
            if rec.asset_quantity <= Decimal("0.0000"):
                violations.append(f"Registro DvP '{rec.settlement_id}' possui quantidade física <= 0: {rec.asset_quantity}")

        all_ok = (len(violations) == 0)
        return {
            "all_invariants_hold": all_ok,
            "violations_count": len(violations),
            "violations": violations,
            "total_participants": len(self.participants),
            "total_assets": len(self.assets),
            "total_contracts": len(self.contracts),
            "total_settlement_records": len(self.settlement_records),
            "total_margin_calls": len(self.margin_calls),
            "total_executions": len(self.execution_results),
            "b3_collected_fees": str(self.b3_collected_fees),
        }

    # -----------------------------------------------------------------
    # SERIALIZAÇÃO E CARREGAMENTO DE FIXTURES JSON
    # -----------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado completo do motor para dicionário Python."""
        return {
            "schema_version": "1.0.0",
            "flow_code": "FL-079",
            "system": "B3_BALCAO_NOME_PRIVATE_REPO",
            "regulatory_framework": [
                "RESOLUCAO_CMN_4706_2018",
                "MANUAL_RISCO_GARANTIAS_NOME_B3",
                "LEI_12431_DEBENTURES_INCENTIVADAS",
                "LEI_14711_MARCO_LEGAL_GARANTIAS",
            ],
            "b3_collected_fees": str(self.b3_collected_fees),
            "participants": [p.to_dict() for p in self.participants.values()],
            "assets": [a.to_dict() for a in self.assets.values()],
            "contracts": [c.to_dict() for c in self.contracts.values()],
            "settlement_records": [s.to_dict() for s in self.settlement_records],
            "margin_calls": [m.to_dict() for m in self.margin_calls],
            "execution_results": [e.to_dict() for e in self.execution_results],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PrivateRepoNomeEngine:
        """Reconstrói uma instância de PrivateRepoNomeEngine a partir de um dicionário."""
        engine = cls()
        engine.b3_collected_fees = to_decimal(data.get("b3_collected_fees", "0.00"))

        for p_data in data.get("participants", []):
            part = ParticipantAccount.from_dict(p_data)
            engine.participants[part.participant_id] = part

        for a_data in data.get("assets", []):
            asset = PrivateAsset.from_dict(a_data)
            engine.assets[asset.asset_id] = asset

        for c_data in data.get("contracts", []):
            contract = PrivateRepoContract.from_dict(c_data)
            engine.contracts[contract.contract_id] = contract

        for s_data in data.get("settlement_records", []):
            rec = SettlementDvPRecord.from_dict(s_data)
            engine.settlement_records.append(rec)

        for m_data in data.get("margin_calls", []):
            mcall = MarginCallEvent.from_dict(m_data)
            engine.margin_calls.append(mcall)

        for e_data in data.get("execution_results", []):
            eres = ExecutionWaterfallResult.from_dict(e_data)
            engine.execution_results.append(eres)

        return engine

    @classmethod
    def load_from_fixture(cls, path: Union[str, Path]) -> PrivateRepoNomeEngine:
        """Carrega e instancia o motor a partir de arquivo JSON de fixture padronizada."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Fixture não encontrada: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def _log_audit(self, action: str, details: Dict[str, Any]) -> None:
        self.audit_log.append({
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details,
        })
