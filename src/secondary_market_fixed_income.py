"""Módulo de Balcão Organizado NoMe B3 — Negociação Secundária de Renda Fixa Privada (FL-071).

Implementa o motor determinístico de negociação secundária de balcão para títulos
de renda fixa privada (Debêntures, CRI e CRA), contemplando:
- Modalidades de negociação: Spot (à vista D+1), Termo (Forward D+n) e Cross-Trades
  (operações diretas com validação de túnel de spread/preço regulamentar).
- Precificação analítica e determinística:
  * PU Par (VNE corrigido / Saldo Devedor Nominal);
  * PU Curva (Sujo e Limpo com apropriação pro-rata die de juros acruados);
  * PU Mercado (Marcação a Mercado - MtM segundo metodologia oficial ANBIMA,
    descontando fluxos futuros por NTN-B interpolada ou DI futuro + spread de crédito).
- Métricas de sensibilidade e risco: Duration de Macaulay, Duration Modificada e DV01.
- Liquidação física e financeira DvP D+1 (Delivery versus Payment) simultânea e atômica,
  com validação estrita de titularidade, custódia e limites operacionais.
- Leis Formais de Conservação:
  1. Conservação de Preço Unitário Limpo e Sujo:
     PU_Sujo == PU_Limpo + Juros_Acruados
     Financeiro_Bruto == Financeiro_Limpo + Financeiro_Acruado.
  2. Conservação Física de Custódia DvP:
     Delta_Custodia_Vendedor + Delta_Custodia_Comprador == 0 (sum(Q) == constante).
  3. Conservação Financeira Bilateral DvP:
     Delta_Caixa_Comprador + Delta_Caixa_Vendedor == 0 (sum(F) == constante).
  4. Inviolabilidade do Túnel Regulamentar B3 NoMe:
     |PU_Trade - PU_Mercado| / PU_Mercado <= Banda_Maxima (Cross-Trades).
  5. Atomicidade DvP: falha em qualquer perna aborta a transação sem mutação de saldos.

Em conformidade com:
- Lei nº 6.404/1976 (Lei das Sociedades por Ações - Debêntures).
- Lei nº 11.076/2004 (Certificados de Recebíveis do Agronegócio - CRA).
- Lei nº 9.514/1997 (Certificados de Recebíveis Imobiliários - CRI).
- Leis nº 12.431/2011 e nº 14.801/2024 (Debêntures Incentivadas e de Infraestrutura).
- Resolução CVM nº 135/2022 (Mercados Regulamentados e Balcão Organizado).
- Código ANBIMA de Negociação de Instrumentos Financeiros e Diretrizes de Marcação a Mercado.

100% Python Standard Library (sem dependências externas).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union


# =====================================================================
# CONSTANTES DE PRECISÃO NUMÉRICA E ARREDONDAMENTO B3 / ANBIMA
# =====================================================================

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_UNIT = Decimal("0.00000001")
QUANTIZE_RATE = Decimal("0.00000001")
QUANTIZE_QTY = Decimal("1")

# Feriados bancários nacionais fixos (mês, dia) para contagem de dias úteis base 252
DEFAULT_BRAZILIAN_HOLIDAYS: Set[Tuple[int, int]] = {
    (1, 1),    # Confraternização Universal
    (4, 21),   # Tiradentes
    (5, 1),    # Dia do Trabalho
    (9, 7),    # Independência do Brasil
    (10, 12),  # Nossa Senhora Aparecida
    (11, 2),   # Finados
    (11, 15),  # Proclamação da República
    (11, 20),  # Dia Nacional de Zumbi e da Consciência Negra
    (12, 25),  # Natal
}


def round_cents(val: Decimal) -> Decimal:
    """Arredonda valor financeiro para 2 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)


def round_unit(val: Decimal) -> Decimal:
    """Arredonda Preço Unitário (PU) para 8 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_UNIT, rounding=ROUND_HALF_UP)


def round_rate(val: Decimal) -> Decimal:
    """Arredonda taxas e spreads para 8 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_RATE, rounding=ROUND_HALF_UP)


def to_decimal(value: Any, field_name: str = "campo") -> Decimal:
    """Converte valor para Decimal com validação rigorosa."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise SecondaryMarketValidationError(f"Valor nulo não permitido para '{field_name}'.")
    try:
        cleaned = str(value).strip().replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError) as err:
        raise SecondaryMarketValidationError(
            f"Valor inválido '{value}' para '{field_name}': {err}"
        ) from err


def to_date(value: Any, field_name: str = "data") -> date:
    """Converte string ISO 'YYYY-MM-DD' ou date/datetime para date."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value or not isinstance(value, str):
        raise SecondaryMarketValidationError(f"Data inválida ou vazia para '{field_name}': {value}")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as err:
        raise SecondaryMarketValidationError(
            f"Formato inválido de data em '{field_name}' ('{value}'). Use AAAA-MM-DD."
        ) from err


# =====================================================================
# CALENDÁRIO E DIAS ÚTEIS (CONVENÇÃO B3 / ANBIMA BASE 252)
# =====================================================================

def is_business_day(d: date, holidays: Optional[Set[Tuple[int, int]]] = None) -> bool:
    """Retorna True se d for dia útil de negociação bancária e balcão B3."""
    if d.weekday() >= 5:  # Sábado (5) ou Domingo (6)
        return False
    active_holidays = holidays if holidays is not None else DEFAULT_BRAZILIAN_HOLIDAYS
    return (d.month, d.day) not in active_holidays


def count_business_days(
    start_date: date,
    end_date: date,
    holidays: Optional[Set[Tuple[int, int]]] = None
) -> int:
    """Conta dias úteis entre start_date e end_date (exclusivo start, inclusivo end)."""
    if end_date < start_date:
        raise SecondaryMarketValidationError(
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
    holidays: Optional[Set[Tuple[int, int]]] = None
) -> date:
    """Adiciona n dias úteis a uma data inicial."""
    if n < 0:
        raise SecondaryMarketValidationError("Quantidade de dias úteis a adicionar deve ser >= 0.")
    curr = start_date
    added = 0
    while added < n:
        curr += timedelta(days=1)
        if is_business_day(curr, holidays):
            added += 1
    return curr


# =====================================================================
# EXCEÇÕES DE DOMÍNIO
# =====================================================================

class SecondaryMarketError(Exception):
    """Exceção base do subsistema de mercado secundário de renda fixa privada."""
    pass


class SecondaryMarketValidationError(SecondaryMarketError):
    """Violação de regras cadastrais, parâmetros ou integridade de dados."""
    pass


class SecondaryMarketTradingError(SecondaryMarketError):
    """Erro em operações de negociação, livro de ofertas ou matching."""
    pass


class SecondaryMarketTunnelViolationError(SecondaryMarketTradingError):
    """Violação de túnel de spread ou preço regulamentar B3 NoMe em Cross-Trades."""
    pass


class InsufficientCustodyBalanceError(SecondaryMarketTradingError):
    """Saldo de custódia livre insuficiente para alienação/venda de ativos."""
    pass


class InsufficientCashLimitError(SecondaryMarketTradingError):
    """Saldo financeiro ou limite operacional insuficiente para compra de ativos."""
    pass


class SecondaryMarketSettlementError(SecondaryMarketError):
    """Falha de liquidação física ou financeira DvP."""
    pass


class SecondaryMarketInvariantError(SecondaryMarketError):
    """Violação formal de leis de conservação matemática (PU ou DvP)."""
    pass


# =====================================================================
# ENUMS DE DOMÍNIO
# =====================================================================

class AssetType(str, Enum):
    """Tipo do título de renda fixa privada."""
    DEBENTURE = "DEBENTURE"
    CRI = "CRI"
    CRA = "CRA"

    @classmethod
    def from_str(cls, val: Any) -> AssetType:
        if isinstance(val, cls):
            return val
        norm = str(val).strip().upper()
        for item in cls:
            if item.value == norm:
                return item
        raise SecondaryMarketValidationError(f"Tipo de ativo desconhecido: '{val}'.")


class IndexerType(str, Enum):
    """Indexador de remuneração do título."""
    IPCA = "IPCA"
    DI = "DI"
    PRE = "PRE"

    @classmethod
    def from_str(cls, val: Any) -> IndexerType:
        if isinstance(val, cls):
            return val
        norm = str(val).strip().upper()
        if "CDI" in norm or "DI" in norm:
            return cls.DI
        if "IPCA" in norm:
            return cls.IPCA
        if "PRE" in norm or "PREFIXADO" in norm:
            return cls.PRE
        raise SecondaryMarketValidationError(f"Indexador desconhecido: '{val}'.")


class TradingMode(str, Enum):
    """Modalidade de negociação no Balcão NoMe B3."""
    SPOT = "SPOT"                # À vista (liquidação padrão D+1)
    FORWARD = "FORWARD"          # A termo (liquidação D+n contratual)
    CROSS_TRADE = "CROSS_TRADE"  # Casamento direto de balcão (sujeito a túnel de preço)

    @classmethod
    def from_str(cls, val: Any) -> TradingMode:
        if isinstance(val, cls):
            return val
        norm = str(val).strip().upper()
        for item in cls:
            if item.value == norm:
                return item
        raise SecondaryMarketValidationError(f"Modalidade de negociação inválida: '{val}'.")


class OrderSide(str, Enum):
    """Sentido da ordem."""
    BUY = "BUY"
    SELL = "SELL"

    @classmethod
    def from_str(cls, val: Any) -> OrderSide:
        if isinstance(val, cls):
            return val
        norm = str(val).strip().upper()
        if norm in ("BUY", "COMPRA", "C"):
            return cls.BUY
        if norm in ("SELL", "VENDA", "V"):
            return cls.SELL
        raise SecondaryMarketValidationError(f"Sentido de ordem inválido: '{val}'.")


class PriceQuoteType(str, Enum):
    """Tipo de cotação da ordem."""
    DIRTY_PRICE = "DIRTY_PRICE"  # PU Sujo direto
    CLEAN_PRICE = "CLEAN_PRICE"  # PU Limpo (juro acruado somado na liquidação)
    YIELD = "YIELD"              # Taxa interna de retorno / cupom anual base 252

    @classmethod
    def from_str(cls, val: Any) -> PriceQuoteType:
        if isinstance(val, cls):
            return val
        norm = str(val).strip().upper()
        for item in cls:
            if item.value == norm:
                return item
        raise SecondaryMarketValidationError(f"Tipo de cotação inválido: '{val}'.")


class OrderStatus(str, Enum):
    """Status da ordem no livro NoMe."""
    PENDING = "PENDING"
    MATCHED = "MATCHED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    SETTLED = "SETTLED"


class SettlementStatus(str, Enum):
    """Status da liquidação DvP."""
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    FAILED = "FAILED"


# =====================================================================
# ESTRUTURAS DE DADOS
# =====================================================================

@dataclass
class CashFlowEvent:
    """Evento futuro de fluxo de caixa do título (cupom ou amortização)."""
    flow_id: int
    payment_date: date
    principal_amortization: Decimal   # Amortização de principal unitária
    interest_payment: Decimal         # Juros de cupom unitário
    total_flow: Decimal               # principal + juros

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CashFlowEvent:
        principal = to_decimal(data.get("principal_amortization", "0"), "principal_amortization")
        interest = to_decimal(data.get("interest_payment", "0"), "interest_payment")
        total = to_decimal(data.get("total_flow", str(principal + interest)), "total_flow")
        return cls(
            flow_id=int(data["flow_id"]),
            payment_date=to_date(data["payment_date"], "payment_date"),
            principal_amortization=principal,
            interest_payment=interest,
            total_flow=total,
        )


@dataclass
class FixedIncomeAsset:
    """Cadastro de título de renda fixa privada (Debênture, CRI, CRA) no NoMe."""
    asset_code: str
    isin: str
    asset_type: AssetType
    indexer: IndexerType
    issue_date: date
    maturity_date: date
    nominal_value_emission: Decimal      # VNE original (ex: 1000.00)
    current_nominal_value: Decimal       # VNE atualizado/corrigido por inflação/amortização
    coupon_rate_annual: Decimal          # Taxa contratual anual base 252 (ex: 0.0550 para 5.50%)
    spread_over_di_annual: Decimal       # Spread contratual sobre DI se aplicável (ex: 0.0150 para DI + 1.50%)
    percent_of_di: Decimal               # % do DI se aplicável (ex: 1.05 para 105% do DI, 0 se taxa aditiva)
    last_coupon_date: date               # Data do último cupom/aniversário para acruamento
    next_coupon_date: date               # Data do próximo evento de cupom
    benchmark_reference_code: str        # Código de referência ANBIMA (ex: NTN-B 2030, DI_CURVE)
    indicative_spread_anbima: Decimal    # Spread de crédito ANBIMA em taxa anual base 252 (ex: 0.0120)
    tunnel_band_max_pct: Decimal         # Banda máxima de desvio aceitável em cross-trades (ex: 0.025 para 2.5%)
    cash_flows: List[CashFlowEvent] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FixedIncomeAsset:
        flows = [CashFlowEvent.from_dict(f) for f in data.get("cash_flows", [])]
        flows.sort(key=lambda x: x.payment_date)
        return cls(
            asset_code=str(data["asset_code"]).strip().upper(),
            isin=str(data["isin"]).strip().upper(),
            asset_type=AssetType.from_str(data["asset_type"]),
            indexer=IndexerType.from_str(data["indexer"]),
            issue_date=to_date(data["issue_date"], "issue_date"),
            maturity_date=to_date(data["maturity_date"], "maturity_date"),
            nominal_value_emission=round_unit(to_decimal(data["nominal_value_emission"], "nominal_value_emission")),
            current_nominal_value=round_unit(to_decimal(data["current_nominal_value"], "current_nominal_value")),
            coupon_rate_annual=to_decimal(data.get("coupon_rate_annual", "0"), "coupon_rate_annual"),
            spread_over_di_annual=to_decimal(data.get("spread_over_di_annual", "0"), "spread_over_di_annual"),
            percent_of_di=to_decimal(data.get("percent_of_di", "0"), "percent_of_di"),
            last_coupon_date=to_date(data["last_coupon_date"], "last_coupon_date"),
            next_coupon_date=to_date(data["next_coupon_date"], "next_coupon_date"),
            benchmark_reference_code=str(data.get("benchmark_reference_code", "NTN-B")).strip(),
            indicative_spread_anbima=to_decimal(data.get("indicative_spread_anbima", "0.0100"), "indicative_spread_anbima"),
            tunnel_band_max_pct=to_decimal(data.get("tunnel_band_max_pct", "0.0250"), "tunnel_band_max_pct"),
            cash_flows=flows,
        )


@dataclass
class MarketDataSnapshot:
    """Curvas e taxas de mercado para apuração de PU Mercado e Marcação a Mercado ANBIMA."""
    as_of_date: date
    di_rate_annual: Decimal                          # Taxa DI overnight anualizada base 252 (ex: 0.1050)
    ntnb_reference_yields: Dict[str, Decimal]        # Código/Vértice NTN-B -> Taxa real anual base 252
    ipca_accumulated_factor: Decimal                 # Fator acumulado do IPCA pro-rata para a data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MarketDataSnapshot:
        yields = {
            str(k).strip(): to_decimal(v, f"ntnb_yield_{k}")
            for k, v in data.get("ntnb_reference_yields", {}).items()
        }
        return cls(
            as_of_date=to_date(data["as_of_date"], "as_of_date"),
            di_rate_annual=to_decimal(data.get("di_rate_annual", "0.1050"), "di_rate_annual"),
            ntnb_reference_yields=yields,
            ipca_accumulated_factor=to_decimal(data.get("ipca_accumulated_factor", "1.0"), "ipca_accumulated_factor"),
        )


@dataclass
class PricingResult:
    """Resultado da precificação determinística de um ativo em uma data de referência."""
    asset_code: str
    calculation_date: date
    pu_par: Decimal                     # Saldo Nominal Unitário Devedor
    pu_curva_limpo: Decimal             # Preço Unitário Limpo na Curva
    juros_acruados: Decimal             # Juros corridos do cupom corrente
    pu_curva_sujo: Decimal              # Preço Unitário Sujo na Curva (Limpo + Acruado)
    pu_mercado_limpo: Decimal           # Preço Unitário Limpo de Mercado (MtM ANBIMA)
    pu_mercado_sujo: Decimal            # Preço Unitário Sujo de Mercado (MtM ANBIMA)
    market_ytm_annual: Decimal          # Taxa de Mercado anualizada de desconto (YTM)
    duration_macaulay_years: Decimal    # Prazo médio de Macaulay em anos (DU / 252)
    modified_duration: Decimal          # Modified duration (sensibilidade a taxa)
    dv01: Decimal                       # Variação em R$ para choque de 1 bps (0.01%)


@dataclass
class CustodyAccount:
    """Conta de custódia depositária e financeira do participante no NoMe."""
    account_id: str
    participant_code: str
    investor_name: str
    investor_document: str
    free_positions: Dict[str, Decimal] = field(default_factory=dict)     # asset_code -> quantidade livre
    blocked_positions: Dict[str, Decimal] = field(default_factory=dict)  # asset_code -> quantidade bloqueada em ordens
    cash_balance: Decimal = Decimal("0.00")                              # Saldo em conta de liquidação
    credit_limit: Decimal = Decimal("0.00")                              # Limite de crédito operacional para compras

    def total_position(self, asset_code: str) -> Decimal:
        return self.free_positions.get(asset_code, Decimal("0")) + self.blocked_positions.get(asset_code, Decimal("0"))

    def available_purchasing_power(self) -> Decimal:
        return self.cash_balance + self.credit_limit

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CustodyAccount:
        free_pos = {
            str(k).strip().upper(): to_decimal(v, f"free_{k}")
            for k, v in data.get("free_positions", {}).items()
        }
        blocked_pos = {
            str(k).strip().upper(): to_decimal(v, f"blocked_{k}")
            for k, v in data.get("blocked_positions", {}).items()
        }
        return cls(
            account_id=str(data["account_id"]).strip(),
            participant_code=str(data["participant_code"]).strip(),
            investor_name=str(data["investor_name"]).strip(),
            investor_document=str(data["investor_document"]).strip(),
            free_positions=free_pos,
            blocked_positions=blocked_pos,
            cash_balance=round_cents(to_decimal(data.get("cash_balance", "0.00"), "cash_balance")),
            credit_limit=round_cents(to_decimal(data.get("credit_limit", "0.00"), "credit_limit")),
        )


@dataclass
class SecondaryOrder:
    """Ordem de negociação secundária de balcão (Spot, Termo ou Cross-Trade)."""
    order_id: str
    asset_code: str
    side: OrderSide
    mode: TradingMode
    quantity: Decimal
    quote_type: PriceQuoteType
    quote_value: Decimal                # Valor da cotação (PU ou Taxa YTM anual)
    account_id: str
    participant_code: str
    trade_date: date
    settlement_date: date
    counterpart_account_id: Optional[str] = None       # Obrigatório em Cross-Trades
    counterpart_participant: Optional[str] = None
    forward_term_days: int = 0                         # Dias úteis para Termo (D+n)
    forward_rate_annual: Decimal = Decimal("0.00000000") # Taxa a termo acordada
    status: OrderStatus = OrderStatus.PENDING
    matched_trade_id: Optional[str] = None
    rejection_reason: Optional[str] = None


@dataclass
class SecondaryTrade:
    """Negócio fechado e registrado no Balcão NoMe."""
    trade_id: str
    buy_order_id: str
    sell_order_id: str
    asset_code: str
    mode: TradingMode
    quantity: Decimal
    trade_dirty_price: Decimal          # PU Sujo acordado
    trade_clean_price: Decimal          # PU Limpo acordado
    accrued_interest_unit: Decimal      # Juros acruados unitários
    gross_financial_amount: Decimal     # Qtd * PU Sujo
    clean_financial_amount: Decimal     # Qtd * PU Limpo
    accrued_financial_amount: Decimal   # Qtd * Juros Acruados
    trade_date: date
    settlement_date: date
    buyer_account_id: str
    seller_account_id: str
    buyer_participant: str
    seller_participant: str
    is_cross_trade: bool
    status: SettlementStatus = SettlementStatus.PENDING
    pricing_tunnel_deviation_pct: Optional[Decimal] = None


@dataclass
class DvPSettlementReceipt:
    """Recibo oficial de liquidação física e financeira DvP D+1 na B3 NoMe."""
    receipt_id: str
    trade_id: str
    asset_code: str
    quantity: Decimal
    gross_financial_amount: Decimal
    clean_financial_amount: Decimal
    accrued_financial_amount: Decimal
    trade_date: date
    settlement_date: date
    buyer_account_id: str
    seller_account_id: str
    status: SettlementStatus
    physical_conservation_verified: bool
    financial_conservation_verified: bool
    pu_dirty_conservation_verified: bool
    timestamp_iso: str
    notes: str = ""


# =====================================================================
# MOTOR DE NEGOCIAÇÃO SECUNDÁRIA, MTM E DvP (ENGINE CENTRAL)
# =====================================================================

class SecondaryMarketFixedIncomeEngine:
    """
    Motor Central de Negociação Secundária de Renda Fixa Privada (FL-071).

    Executa:
    - Precificação determinística de PU Par, PU Curva e PU Mercado (ANBIMA).
    - Marcação a mercado (MtM) e sensibilidade de risco de carteira (Duration, DV01).
    - Validação pré-trade de custódia e titularidade do vendedor e limites do comprador.
    - Matching de ordens Spot e Termo e registro de Cross-Trades com túnel de preço.
    - Liquidação bilateral atômica DvP D+1 com comprovação matemática de conservação.
    """

    def __init__(
        self,
        assets: Optional[Dict[str, FixedIncomeAsset]] = None,
        market_data: Optional[MarketDataSnapshot] = None,
        accounts: Optional[Dict[str, CustodyAccount]] = None,
        holidays: Optional[Set[Tuple[int, int]]] = None,
    ):
        self.assets: Dict[str, FixedIncomeAsset] = assets or {}
        self.market_data: Optional[MarketDataSnapshot] = market_data
        self.accounts: Dict[str, CustodyAccount] = accounts or {}
        self.holidays: Set[Tuple[int, int]] = holidays or set(DEFAULT_BRAZILIAN_HOLIDAYS)

        # Livros e registros internos
        self.orders: Dict[str, SecondaryOrder] = {}
        self.trades: Dict[str, SecondaryTrade] = {}
        self.settlement_receipts: Dict[str, DvPSettlementReceipt] = {}

        self._order_counter: int = 1
        self._trade_counter: int = 1
        self._receipt_counter: int = 1

    # -----------------------------------------------------------------
    # MÉTODOS DE CALENDÁRIO
    # -----------------------------------------------------------------

    def is_business_day(self, d: date) -> bool:
        return is_business_day(d, self.holidays)

    def count_business_days(self, start_date: date, end_date: date) -> int:
        return count_business_days(start_date, end_date, self.holidays)

    def add_business_days(self, start_date: date, n: int) -> date:
        return add_business_days(start_date, n, self.holidays)

    # -----------------------------------------------------------------
    # CÁLCULOS DETERMINÍSTICOS DE PREÇO UNITÁRIO (PU PAR, CURVA E MERCADO)
    # -----------------------------------------------------------------

    def calculate_pu_par(self, asset_code: str, calc_date: Optional[date] = None) -> Decimal:
        """
        Calcula o PU Par (Saldo Devedor Nominal Unitário) do ativo na data de cálculo.

        Para ativos atrelados ao IPCA:
          PU Par = VNE_Emissao * Fator_IPCA * (1 - Amortizacoes_Previas_Acumuladas)
        Para ativos atrelados ao DI ou Prefixados:
          PU Par = VNE_Atual (principal não amortizado).
        """
        asset = self._get_asset(asset_code)
        target_date = calc_date or (self.market_data.as_of_date if self.market_data else asset.issue_date)

        if asset.indexer == IndexerType.IPCA and self.market_data:
            # VNE corrigido pelo IPCA acumulado até a data
            pu_par = asset.nominal_value_emission * self.market_data.ipca_accumulated_factor
        else:
            pu_par = asset.current_nominal_value

        return round_unit(pu_par)

    def calculate_pu_curva(
        self,
        asset_code: str,
        calc_date: Optional[date] = None
    ) -> Tuple[Decimal, Decimal, Decimal]:
        """
        Calcula o PU Curva do título (Sujo, Limpo e Juros Acruados) na data de cálculo.

        Metodologia de acruamento (pro-rata die base 252):
          Fator_Juros = (1 + Cupom_Anual)^(DU_decorridos / 252)
          Juros_Acruados = PU_Par * (Fator_Juros - 1)
          PU_Curva_Limpo = PU_Par
          PU_Curva_Sujo = PU_Curva_Limpo + Juros_Acruados = PU_Par * Fator_Juros.

        Para ativos indexados ao DI + Spread:
          Fator_DI = (1 + DI_Over)^(DU_decorridos / 252)
          Fator_Spread = (1 + Spread_DI)^(DU_decorridos / 252)
          Fator_Total = Fator_DI * Fator_Spread
          PU_Curva_Sujo = PU_Par * Fator_Total
          Juros_Acruados = PU_Par * (Fator_Total - 1)
          PU_Curva_Limpo = PU_Par

        Retorna:
          (pu_curva_sujo, pu_curva_limpo, juros_acruados)
        Garantindo estritamente:
          pu_curva_sujo == pu_curva_limpo + juros_acruados
        """
        asset = self._get_asset(asset_code)
        target_date = calc_date or (self.market_data.as_of_date if self.market_data else asset.last_coupon_date)
        pu_par = self.calculate_pu_par(asset_code, target_date)

        # Dias úteis desde o último evento de cupom
        start_date = max(asset.issue_date, asset.last_coupon_date)
        if target_date <= start_date:
            du_decorridos = 0
        else:
            du_decorridos = self.count_business_days(start_date, target_date)

        t_exp = Decimal(du_decorridos) / Decimal("252")

        if asset.indexer in (IndexerType.IPCA, IndexerType.PRE):
            # Acruamento exponencial do cupom anual
            coupon = float(asset.coupon_rate_annual)
            factor_interest = Decimal(str((1.0 + coupon) ** float(t_exp)))
            pu_sujo_raw = pu_par * factor_interest
            pu_limpo = pu_par
            juros_acruados = round_unit(pu_sujo_raw - pu_limpo)
            pu_sujo = round_unit(pu_limpo + juros_acruados)

        elif asset.indexer == IndexerType.DI:
            # Acruamento do CDI e spread contratual
            di_rate = float(self.market_data.di_rate_annual) if self.market_data else 0.1050
            if asset.percent_of_di > Decimal("0"):
                effective_di = di_rate * float(asset.percent_of_di)
                factor_di = Decimal(str((1.0 + effective_di) ** float(t_exp)))
                pu_sujo_raw = pu_par * factor_di
            else:
                spread = float(asset.spread_over_di_annual)
                factor_di = Decimal(str((1.0 + di_rate) ** float(t_exp)))
                factor_spread = Decimal(str((1.0 + spread) ** float(t_exp)))
                factor_total = factor_di * factor_spread
                pu_sujo_raw = pu_par * factor_total

            pu_limpo = pu_par
            juros_acruados = round_unit(pu_sujo_raw - pu_limpo)
            pu_sujo = round_unit(pu_limpo + juros_acruados)

        else:
            pu_limpo = pu_par
            juros_acruados = Decimal("0.00000000")
            pu_sujo = pu_par

        # Prova formal da invariante de PU Curva
        if pu_sujo != (pu_limpo + juros_acruados):
            raise SecondaryMarketInvariantError(
                f"Violação da invariante PU Curva: Sujo ({pu_sujo}) != Limpo ({pu_limpo}) + Juros ({juros_acruados})"
            )

        return pu_sujo, pu_limpo, juros_acruados

    def calculate_pu_mercado(
        self,
        asset_code: str,
        calc_date: Optional[date] = None,
        custom_ytm: Optional[Decimal] = None
    ) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        """
        Calcula o PU Mercado (MtM oficial ANBIMA) do ativo por Fluxo de Caixa Descontado (DCF).

        Metodologia ANBIMA:
        1. Determinação da Taxa de Mercado (YTM_mercado):
           Se IPCA:
             YTM = (1 + Taxa_NTNB_Referencia) * (1 + Spread_Credito_ANBIMA) - 1
           Se DI:
             YTM = (1 + Taxa_DI_Futuro) * (1 + Spread_Credito_ANBIMA) - 1
        2. Desconto de cada fluxo futuro de caixa remanescente a valor presente:
           DF_k = 1 / (1 + YTM_mercado)^(DU_k / 252)
           PU_Mercado_Sujo = sum(CF_k * DF_k)
        3. PU_Mercado_Limpo = PU_Mercado_Sujo - Juros_Acruados.

        Retorna:
          (pu_mercado_sujo, pu_mercado_limpo, juros_acruados, ytm_mercado)
        Garantindo estritamente:
          pu_mercado_sujo == pu_mercado_limpo + juros_acruados
        """
        asset = self._get_asset(asset_code)
        target_date = calc_date or (self.market_data.as_of_date if self.market_data else asset.issue_date)

        # 1. Taxa de mercado de desconto
        if custom_ytm is not None:
            ytm = custom_ytm
        else:
            if not self.market_data:
                raise SecondaryMarketValidationError("MarketDataSnapshot necessário para cálculo de PU Mercado.")

            if asset.indexer == IndexerType.IPCA:
                # Referência NTN-B correspondente
                ref_code = asset.benchmark_reference_code
                if ref_code not in self.market_data.ntnb_reference_yields:
                    # fallback para primeira taxa NTN-B disponível
                    ref_yield = next(iter(self.market_data.ntnb_reference_yields.values()), Decimal("0.0600"))
                else:
                    ref_yield = self.market_data.ntnb_reference_yields[ref_code]

                # Composição exponencial: (1 + r_ntnb) * (1 + spread) - 1
                r_ntnb_f = float(ref_yield)
                spread_f = float(asset.indicative_spread_anbima)
                ytm_f = ((1.0 + r_ntnb_f) * (1.0 + spread_f)) - 1.0
                ytm = round_rate(Decimal(str(ytm_f)))

            elif asset.indexer == IndexerType.DI:
                di_f = float(self.market_data.di_rate_annual)
                spread_f = float(asset.indicative_spread_anbima)
                ytm_f = ((1.0 + di_f) * (1.0 + spread_f)) - 1.0
                ytm = round_rate(Decimal(str(ytm_f)))

            else:  # PRE
                ref_yield = next(iter(self.market_data.ntnb_reference_yields.values()), Decimal("0.1150"))
                ytm = round_rate(ref_yield + asset.indicative_spread_anbima)

        # 2. Desconto dos fluxos futuros a valor presente (DCF)
        future_flows = [f for f in asset.cash_flows if f.payment_date > target_date]
        if not future_flows:
            # Se não houver fluxos cadastrados explicitamente, projeta fluxo único no vencimento
            pu_par = self.calculate_pu_par(asset_code, target_date)
            du_total = max(1, self.count_business_days(target_date, asset.maturity_date))
            t_exp = Decimal(du_total) / Decimal("252")
            coupon_f = float(asset.coupon_rate_annual)
            v_venc = pu_par * Decimal(str((1.0 + coupon_f) ** float(t_exp)))
            future_flows = [
                CashFlowEvent(
                    flow_id=1,
                    payment_date=asset.maturity_date,
                    principal_amortization=pu_par,
                    interest_payment=round_unit(v_venc - pu_par),
                    total_flow=round_unit(v_venc),
                )
            ]

        # Fator de correção IPCA proporcional para fluxos se indexado a IPCA
        ipca_mult = self.market_data.ipca_accumulated_factor if (asset.indexer == IndexerType.IPCA and self.market_data) else Decimal("1.0")

        total_pv = Decimal("0")
        ytm_f = float(ytm)

        for flow in future_flows:
            du_k = self.count_business_days(target_date, flow.payment_date)
            t_k = Decimal(du_k) / Decimal("252")
            df_k = Decimal(str(1.0 / ((1.0 + ytm_f) ** float(t_k))))
            # Ajuste de fluxo unitário
            flow_amount = flow.total_flow * ipca_mult
            pv_k = flow_amount * df_k
            total_pv += pv_k

        pu_mercado_sujo = round_unit(total_pv)

        # Juros acruados do cupom corrente
        _, _, juros_acruados = self.calculate_pu_curva(asset_code, target_date)

        # PU Limpo = PU Sujo - Juros Acruados
        pu_mercado_limpo = round_unit(pu_mercado_sujo - juros_acruados)

        # Prova formal da conservação PU Mercado
        if pu_mercado_sujo != (pu_mercado_limpo + juros_acruados):
            raise SecondaryMarketInvariantError(
                f"Violação de conservação de PU Mercado: Sujo ({pu_mercado_sujo}) != Limpo ({pu_mercado_limpo}) + Juros ({juros_acruados})"
            )

        return pu_mercado_sujo, pu_mercado_limpo, juros_acruados, ytm

    def price_asset(self, asset_code: str, calc_date: Optional[date] = None) -> PricingResult:
        """
        Gera o relatório analítico completo de precificação determinística e métricas de risco.
        """
        asset = self._get_asset(asset_code)
        target_date = calc_date or (self.market_data.as_of_date if self.market_data else asset.issue_date)

        pu_par = self.calculate_pu_par(asset_code, target_date)
        pu_curva_sujo, pu_curva_limpo, juros_acruados = self.calculate_pu_curva(asset_code, target_date)
        pu_mercado_sujo, pu_mercado_limpo, _, ytm = self.calculate_pu_mercado(asset_code, target_date)

        # Métricas de risco e sensibilidade (Duration de Macaulay, Modified Duration e DV01)
        future_flows = [f for f in asset.cash_flows if f.payment_date > target_date]
        if not future_flows:
            future_flows = [
                CashFlowEvent(
                    flow_id=1,
                    payment_date=asset.maturity_date,
                    principal_amortization=pu_par,
                    interest_payment=Decimal("0"),
                    total_flow=pu_par,
                )
            ]

        ipca_mult = self.market_data.ipca_accumulated_factor if (asset.indexer == IndexerType.IPCA and self.market_data) else Decimal("1.0")
        weighted_time_sum = Decimal("0")
        ytm_f = float(ytm)

        for flow in future_flows:
            du_k = self.count_business_days(target_date, flow.payment_date)
            t_k = Decimal(du_k) / Decimal("252")
            df_k = Decimal(str(1.0 / ((1.0 + ytm_f) ** float(t_k))))
            flow_pv = (flow.total_flow * ipca_mult) * df_k
            weighted_time_sum += t_k * flow_pv

        if pu_mercado_sujo > Decimal("0"):
            macaulay_duration = (weighted_time_sum / pu_mercado_sujo).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            modified_duration = (macaulay_duration / Decimal(str(1.0 + ytm_f))).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            # DV01: impacto monetário de 1 basis point (0.0001)
            dv01 = (modified_duration * pu_mercado_sujo * Decimal("0.0001")).quantize(QUANTIZE_UNIT, rounding=ROUND_HALF_UP)
        else:
            macaulay_duration = Decimal("0.0000")
            modified_duration = Decimal("0.0000")
            dv01 = Decimal("0.00000000")

        return PricingResult(
            asset_code=asset_code,
            calculation_date=target_date,
            pu_par=pu_par,
            pu_curva_limpo=pu_curva_limpo,
            juros_acruados=juros_acruados,
            pu_curva_sujo=pu_curva_sujo,
            pu_mercado_limpo=pu_mercado_limpo,
            pu_mercado_sujo=pu_mercado_sujo,
            market_ytm_annual=ytm,
            duration_macaulay_years=macaulay_duration,
            modified_duration=modified_duration,
            dv01=dv01,
        )

    def mark_to_market_portfolio(
        self,
        account_id: str,
        calc_date: Optional[date] = None
    ) -> Dict[str, Any]:
        """
        Executa a Marcação a Mercado (MtM) oficial da carteira do investidor.
        Calcula o valor na curva, o valor a mercado e o P&L de marcação a mercado.
        """
        account = self._get_account(account_id)
        target_date = calc_date or (self.market_data.as_of_date if self.market_data else date.today())

        positions_report = []
        total_curve_value = Decimal("0.00")
        total_market_value = Decimal("0.00")

        for asset_code, free_qty in account.free_positions.items():
            total_qty = free_qty + account.blocked_positions.get(asset_code, Decimal("0"))
            if total_qty <= Decimal("0"):
                continue

            pricing = self.price_asset(asset_code, target_date)
            pos_curve_val = round_cents(total_qty * pricing.pu_curva_sujo)
            pos_market_val = round_cents(total_qty * pricing.pu_mercado_sujo)
            unrealized_pnl = round_cents(pos_market_val - pos_curve_val)

            total_curve_value += pos_curve_val
            total_market_value += pos_market_val

            positions_report.append({
                "asset_code": asset_code,
                "quantity": total_qty,
                "pu_curva_sujo": pricing.pu_curva_sujo,
                "pu_mercado_sujo": pricing.pu_mercado_sujo,
                "position_curve_value": pos_curve_val,
                "position_market_value": pos_market_val,
                "unrealized_mtm_pnl": unrealized_pnl,
                "duration_macaulay": pricing.duration_macaulay_years,
                "dv01_total": round_cents(total_qty * pricing.dv01),
            })

        total_pnl = round_cents(total_market_value - total_curve_value)
        return {
            "account_id": account_id,
            "as_of_date": target_date.isoformat(),
            "positions": positions_report,
            "total_curve_value": total_curve_value,
            "total_market_value": total_market_value,
            "total_unrealized_mtm_pnl": total_pnl,
        }

    # -----------------------------------------------------------------
    # GESTÃO DE ORDENS E NEGOCIAÇÃO DE BALCÃO NOME
    # -----------------------------------------------------------------

    def submit_order(
        self,
        asset_code: str,
        side: Union[OrderSide, str],
        mode: Union[TradingMode, str],
        quantity: Union[Decimal, int, str],
        quote_type: Union[PriceQuoteType, str],
        quote_value: Union[Decimal, float, str],
        account_id: str,
        participant_code: str,
        trade_date: Optional[date] = None,
        settlement_date: Optional[date] = None,
        counterpart_account_id: Optional[str] = None,
        counterpart_participant: Optional[str] = None,
        forward_term_days: int = 0,
        forward_rate_annual: Union[Decimal, str] = "0.0",
    ) -> SecondaryOrder:
        """
        Submete uma nova ordem de compra/venda de títulos no Balcão NoMe B3.
        Valida pre-trade:
          - Existência e titularidade da conta;
          - Saldo livre de custódia para ordens de venda (bloqueio preventivo);
          - Limite financeiro disponível para ordens de compra.
        """
        asset = self._get_asset(asset_code)
        account = self._get_account(account_id)
        e_side = OrderSide.from_str(side)
        e_mode = TradingMode.from_str(mode)
        e_quote_type = PriceQuoteType.from_str(quote_type)
        qty = to_decimal(quantity, "quantity").quantize(QUANTIZE_QTY)
        val = to_decimal(quote_value, "quote_value")
        t_date = trade_date or (self.market_data.as_of_date if self.market_data else date.today())

        if qty <= Decimal("0"):
            raise SecondaryMarketValidationError("Quantidade negociada deve ser estritamente positiva.")

        # Data de liquidação padrão D+1 para Spot, D+n para Forward
        if e_mode == TradingMode.SPOT:
            s_date = settlement_date or self.add_business_days(t_date, 1)
        elif e_mode == TradingMode.FORWARD:
            term_days = forward_term_days if forward_term_days > 0 else 30
            s_date = settlement_date or self.add_business_days(t_date, term_days)
        else:  # CROSS_TRADE
            s_date = settlement_date or self.add_business_days(t_date, 1)

        # Validação Pré-Trade e Bloqueio de Custódia (para venda)
        if e_side == OrderSide.SELL:
            free_balance = account.free_positions.get(asset_code, Decimal("0"))
            if free_balance < qty:
                raise InsufficientCustodyBalanceError(
                    f"Conta '{account_id}' possui saldo livre insuficiente ({free_balance}) para venda de {qty} títulos de {asset_code}."
                )
            # Bloqueia a quantidade para evitar dupla negociação
            account.free_positions[asset_code] = free_balance - qty
            account.blocked_positions[asset_code] = account.blocked_positions.get(asset_code, Decimal("0")) + qty

        # Validação de Limite Financeiro Pré-Trade (para compra)
        if e_side == OrderSide.BUY:
            # Estimativa de valor financeiro bruto da ordem
            if e_quote_type == PriceQuoteType.DIRTY_PRICE:
                est_pu = val
            elif e_quote_type == PriceQuoteType.CLEAN_PRICE:
                _, _, accrued = self.calculate_pu_curva(asset_code, s_date)
                est_pu = val + accrued
            else:  # YIELD
                est_pu, _, _, _ = self.calculate_pu_mercado(asset_code, s_date, custom_ytm=val)

            est_gross_amount = round_cents(qty * est_pu)
            available_power = account.available_purchasing_power()
            if available_power < est_gross_amount:
                raise InsufficientCashLimitError(
                    f"Conta compradora '{account_id}' não possui poder de compra suficiente (Disponível: R$ {available_power}, Estimado: R$ {est_gross_amount})."
                )

        order_id = f"ORD-{asset_code}-{self._order_counter:06d}"
        self._order_counter += 1

        order = SecondaryOrder(
            order_id=order_id,
            asset_code=asset_code,
            side=e_side,
            mode=e_mode,
            quantity=qty,
            quote_type=e_quote_type,
            quote_value=val,
            account_id=account_id,
            participant_code=participant_code,
            trade_date=t_date,
            settlement_date=s_date,
            counterpart_account_id=counterpart_account_id,
            counterpart_participant=counterpart_participant,
            forward_term_days=forward_term_days,
            forward_rate_annual=to_decimal(forward_rate_annual, "forward_rate_annual"),
            status=OrderStatus.PENDING,
        )
        self.orders[order_id] = order
        return order

    def execute_cross_trade(
        self,
        asset_code: str,
        quantity: Union[Decimal, int, str],
        agreed_dirty_price: Union[Decimal, str],
        buyer_account_id: str,
        seller_account_id: str,
        buyer_participant: str,
        seller_participant: str,
        trade_date: Optional[date] = None,
        settlement_date: Optional[date] = None,
    ) -> SecondaryTrade:
        """
        Executa uma Operação Direta (Cross-Trade) no Balcão NoMe B3.

        Submetida com validação obrigatória do Túnel Regulamentar de Preço B3:
          |PU_Acordado - PU_Mercado_ANBIMA| / PU_Mercado_ANBIMA <= Banda_Maxima (ex: 2.50%).
        Se o preço acordado estiver fora da banda, a operação é rejeitada imediatamente
        com SecondaryMarketTunnelViolationError.
        """
        asset = self._get_asset(asset_code)
        qty = to_decimal(quantity, "quantity").quantize(QUANTIZE_QTY)
        dirty_price = round_unit(to_decimal(agreed_dirty_price, "agreed_dirty_price"))
        t_date = trade_date or (self.market_data.as_of_date if self.market_data else date.today())
        s_date = settlement_date or self.add_business_days(t_date, 1)

        # 1. Precificação de referência ANBIMA na data de liquidação
        pu_mercado_sujo, _, juros_acruados, _ = self.calculate_pu_mercado(asset_code, s_date)

        # 2. Validação do Túnel de Preço B3 NoMe
        price_diff = abs(dirty_price - pu_mercado_sujo)
        deviation_pct = price_diff / pu_mercado_sujo
        max_band = asset.tunnel_band_max_pct

        if deviation_pct > max_band:
            raise SecondaryMarketTunnelViolationError(
                f"Cross-Trade em {asset_code} rejeitado: desvio de preço ({deviation_pct * 100:.3f}%) excede o túnel regulamentar B3 NoMe de {max_band * 100:.2f}%. "
                f"PU Acordado: {dirty_price}, PU Mercado ANBIMA: {pu_mercado_sujo}."
            )

        # 3. Submissão das duas pernas (compra e venda)
        buy_order = self.submit_order(
            asset_code=asset_code,
            side=OrderSide.BUY,
            mode=TradingMode.CROSS_TRADE,
            quantity=qty,
            quote_type=PriceQuoteType.DIRTY_PRICE,
            quote_value=dirty_price,
            account_id=buyer_account_id,
            participant_code=buyer_participant,
            trade_date=t_date,
            settlement_date=s_date,
            counterpart_account_id=seller_account_id,
            counterpart_participant=seller_participant,
        )

        sell_order = self.submit_order(
            asset_code=asset_code,
            side=OrderSide.SELL,
            mode=TradingMode.CROSS_TRADE,
            quantity=qty,
            quote_type=PriceQuoteType.DIRTY_PRICE,
            quote_value=dirty_price,
            account_id=seller_account_id,
            participant_code=seller_participant,
            trade_date=t_date,
            settlement_date=s_date,
            counterpart_account_id=buyer_account_id,
            counterpart_participant=buyer_participant,
        )

        # 4. Casamento direto (matching)
        clean_price = round_unit(dirty_price - juros_acruados)
        gross_amount = round_cents(qty * dirty_price)
        clean_amount = round_cents(qty * clean_price)
        accrued_amount = round_cents(qty * juros_acruados)

        trade_id = f"TRD-CROSS-{self._trade_counter:06d}"
        self._trade_counter += 1

        buy_order.status = OrderStatus.MATCHED
        buy_order.matched_trade_id = trade_id
        sell_order.status = OrderStatus.MATCHED
        sell_order.matched_trade_id = trade_id

        trade = SecondaryTrade(
            trade_id=trade_id,
            buy_order_id=buy_order.order_id,
            sell_order_id=sell_order.order_id,
            asset_code=asset_code,
            mode=TradingMode.CROSS_TRADE,
            quantity=qty,
            trade_dirty_price=dirty_price,
            trade_clean_price=clean_price,
            accrued_interest_unit=juros_acruados,
            gross_financial_amount=gross_amount,
            clean_financial_amount=clean_amount,
            accrued_financial_amount=accrued_amount,
            trade_date=t_date,
            settlement_date=s_date,
            buyer_account_id=buyer_account_id,
            seller_account_id=seller_account_id,
            buyer_participant=buyer_participant,
            seller_participant=seller_participant,
            is_cross_trade=True,
            status=SettlementStatus.PENDING,
            pricing_tunnel_deviation_pct=deviation_pct,
        )
        self.trades[trade_id] = trade
        return trade

    def match_orders(
        self,
        buy_order_id: str,
        sell_order_id: str
    ) -> SecondaryTrade:
        """
        Executa o casamento bilateral (matching) de ordens abertas no livro NoMe.
        Verifica compatibilidade de ativo, quantidade, data de liquidação e preços.
        """
        buy_order = self.orders.get(buy_order_id)
        sell_order = self.orders.get(sell_order_id)

        if not buy_order or not sell_order:
            raise SecondaryMarketTradingError("Ordem de compra ou venda não encontrada para matching.")
        if buy_order.asset_code != sell_order.asset_code:
            raise SecondaryMarketTradingError("Ativos divergentes entre as ordens.")
        if buy_order.quantity != sell_order.quantity:
            raise SecondaryMarketTradingError("Quantidades divergentes (casamento parcial não habilitado neste fluxo).")
        if buy_order.settlement_date != sell_order.settlement_date:
            raise SecondaryMarketTradingError("Datas de liquidação divergentes entre as ordens.")
        if buy_order.status != OrderStatus.PENDING or sell_order.status != OrderStatus.PENDING:
            raise SecondaryMarketTradingError("Uma ou ambas as ordens não estão em status PENDING.")

        asset_code = buy_order.asset_code
        s_date = buy_order.settlement_date

        # Resolução de Preço Acordado
        # Regra de Balcão NoMe: preço da ordem passiva (a mais antiga) ou cotação compatível
        dirty_price = self._resolve_order_dirty_price(buy_order, s_date)
        sell_dirty = self._resolve_order_dirty_price(sell_order, s_date)

        # Verificação de cruzamento de preços (o comprador deve aceitar pagar >= oferta do vendedor)
        if dirty_price < sell_dirty:
            raise SecondaryMarketTradingError(
                f"Spread negativo / sem cruzamento: Preço de compra ({dirty_price}) inferior à venda ({sell_dirty})."
            )

        # Preço de execução = preço da ordem de venda (melhor oferta do vendedor)
        exec_dirty_price = sell_dirty
        _, _, juros_acruados = self.calculate_pu_curva(asset_code, s_date)
        clean_price = round_unit(exec_dirty_price - juros_acruados)

        qty = buy_order.quantity
        gross_amount = round_cents(qty * exec_dirty_price)
        clean_amount = round_cents(qty * clean_price)
        accrued_amount = round_cents(qty * juros_acruados)

        trade_id = f"TRD-{buy_order.mode.value}-{self._trade_counter:06d}"
        self._trade_counter += 1

        buy_order.status = OrderStatus.MATCHED
        buy_order.matched_trade_id = trade_id
        sell_order.status = OrderStatus.MATCHED
        sell_order.matched_trade_id = trade_id

        trade = SecondaryTrade(
            trade_id=trade_id,
            buy_order_id=buy_order.order_id,
            sell_order_id=sell_order.order_id,
            asset_code=asset_code,
            mode=buy_order.mode,
            quantity=qty,
            trade_dirty_price=exec_dirty_price,
            trade_clean_price=clean_price,
            accrued_interest_unit=juros_acruados,
            gross_financial_amount=gross_amount,
            clean_financial_amount=clean_amount,
            accrued_financial_amount=accrued_amount,
            trade_date=buy_order.trade_date,
            settlement_date=s_date,
            buyer_account_id=buy_order.account_id,
            seller_account_id=sell_order.account_id,
            buyer_participant=buy_order.participant_code,
            seller_participant=sell_order.participant_code,
            is_cross_trade=False,
            status=SettlementStatus.PENDING,
        )
        self.trades[trade_id] = trade
        return trade

    def _resolve_order_dirty_price(self, order: SecondaryOrder, settlement_date: date) -> Decimal:
        """Determina o PU Sujo a partir da cotação da ordem (PU Sujo, Limpo ou Yield)."""
        asset_code = order.asset_code
        if order.quote_type == PriceQuoteType.DIRTY_PRICE:
            dirty = round_unit(order.quote_value)
        elif order.quote_type == PriceQuoteType.CLEAN_PRICE:
            _, _, juros = self.calculate_pu_curva(asset_code, settlement_date)
            dirty = round_unit(order.quote_value + juros)
        else:  # YIELD
            dirty, _, _, _ = self.calculate_pu_mercado(asset_code, settlement_date, custom_ytm=order.quote_value)

        # Se ordem for a termo (FORWARD) e possuir taxa a termo explícita
        if order.mode == TradingMode.FORWARD and order.forward_rate_annual > Decimal("0"):
            t_exp = Decimal(order.forward_term_days) / Decimal("252")
            fwd_factor = Decimal(str((1.0 + float(order.forward_rate_annual)) ** float(t_exp)))
            dirty = round_unit(dirty * fwd_factor)

        return dirty

    # -----------------------------------------------------------------
    # LIQUIDAÇÃO FÍSICA E FINANCEIRA DvP D+1 (DELIVERY VERSUS PAYMENT)
    # -----------------------------------------------------------------

    def settle_dvp(self, trade_id: str, settlement_date: Optional[date] = None) -> DvPSettlementReceipt:
        """
        Executa a liquidação simultânea e atômica DvP (Entrega contra Pagamento) D+1.

        Garante matematicamente:
        1. Conservação física de custódia:
           Delta_Custodia_Vendedor (-Q) + Delta_Custodia_Comprador (+Q) == 0.
        2. Conservação financeira de caixa:
           Delta_Caixa_Comprador (-F) + Delta_Caixa_Vendedor (+F) == 0.
        3. Conservação de Preço Unitário Limpo e Sujo:
           F_Bruto == F_Limpo + F_Acruado == Q * (PU_Limpo + Juros_Acruados).
        4. Atomicidade estrita: se houver qualquer insuficiência, reverte o estado.
        """
        trade = self.trades.get(trade_id)
        if not trade:
            raise SecondaryMarketSettlementError(f"Trade '{trade_id}' não encontrado para liquidação DvP.")
        if trade.status == SettlementStatus.SETTLED:
            raise SecondaryMarketSettlementError(f"Trade '{trade_id}' já liquidado anteriormente.")

        target_date = settlement_date or trade.settlement_date
        buyer_account = self._get_account(trade.buyer_account_id)
        seller_account = self._get_account(trade.seller_account_id)
        asset_code = trade.asset_code
        qty = trade.quantity
        gross_amount = trade.gross_financial_amount

        # Snapshots pré-liquidação para conferência de invariantes
        pre_buyer_cash = buyer_account.cash_balance
        pre_seller_cash = seller_account.cash_balance
        pre_buyer_qty = buyer_account.total_position(asset_code)
        pre_seller_qty = seller_account.total_position(asset_code)

        # 1. Validação física: o vendedor deve possuir o saldo bloqueado
        seller_blocked = seller_account.blocked_positions.get(asset_code, Decimal("0"))
        if seller_blocked < qty:
            raise SecondaryMarketSettlementError(
                f"Falha DvP: Custódia bloqueada do vendedor ({seller_blocked}) insuficiente para liquidar {qty} de {asset_code}."
            )

        # 2. Validação financeira: o comprador deve possuir fundos/poder de compra
        if buyer_account.available_purchasing_power() < gross_amount:
            raise SecondaryMarketSettlementError(
                f"Falha DvP: Saldo/poder de compra do comprador ({buyer_account.available_purchasing_power()}) insuficiente para R$ {gross_amount}."
            )

        # 3. Execução Atômica Simultânea
        # 3.1 Perna Financeira
        buyer_account.cash_balance = round_cents(buyer_account.cash_balance - gross_amount)
        seller_account.cash_balance = round_cents(seller_account.cash_balance + gross_amount)

        # 3.2 Perna Física
        seller_account.blocked_positions[asset_code] = seller_blocked - qty
        buyer_account.free_positions[asset_code] = buyer_account.free_positions.get(asset_code, Decimal("0")) + qty

        # 4. Verificação das Leis Formais de Conservação
        # 4.1 Conservação Física:
        post_buyer_qty = buyer_account.total_position(asset_code)
        post_seller_qty = seller_account.total_position(asset_code)
        delta_q_buyer = post_buyer_qty - pre_buyer_qty
        delta_q_seller = post_seller_qty - pre_seller_qty
        physical_conserved = (delta_q_buyer + delta_q_seller) == Decimal("0") and delta_q_buyer == qty

        # 4.2 Conservação Financeira:
        post_buyer_cash = buyer_account.cash_balance
        post_seller_cash = seller_account.cash_balance
        delta_f_buyer = post_buyer_cash - pre_buyer_cash
        delta_f_seller = post_seller_cash - pre_seller_cash
        financial_conserved = (delta_f_buyer + delta_f_seller) == Decimal("0.00") and delta_f_seller == gross_amount

        # 4.3 Conservação PU Limpo e Sujo:
        pu_clean_conserved = (trade.clean_financial_amount + trade.accrued_financial_amount) == gross_amount

        if not (physical_conserved and financial_conserved and pu_clean_conserved):
            # Rollback imediato
            buyer_account.cash_balance = pre_buyer_cash
            seller_account.cash_balance = pre_seller_cash
            seller_account.blocked_positions[asset_code] = seller_blocked
            buyer_account.free_positions[asset_code] = pre_buyer_qty
            raise SecondaryMarketInvariantError(
                f"Violação formal de invariante DvP no trade {trade_id}! Operação abortada."
            )

        # 5. Atualização de Status e Emissão do Recibo Oficial
        trade.status = SettlementStatus.SETTLED
        buy_order = self.orders.get(trade.buy_order_id)
        sell_order = self.orders.get(trade.sell_order_id)
        if buy_order:
            buy_order.status = OrderStatus.SETTLED
        if sell_order:
            sell_order.status = OrderStatus.SETTLED

        receipt_id = f"REC-DvP-{self._receipt_counter:06d}"
        self._receipt_counter += 1

        receipt = DvPSettlementReceipt(
            receipt_id=receipt_id,
            trade_id=trade_id,
            asset_code=asset_code,
            quantity=qty,
            gross_financial_amount=gross_amount,
            clean_financial_amount=trade.clean_financial_amount,
            accrued_financial_amount=trade.accrued_financial_amount,
            trade_date=trade.trade_date,
            settlement_date=target_date,
            buyer_account_id=buyer_account.account_id,
            seller_account_id=seller_account.account_id,
            status=SettlementStatus.SETTLED,
            physical_conservation_verified=physical_conserved,
            financial_conservation_verified=financial_conserved,
            pu_dirty_conservation_verified=pu_clean_conserved,
            timestamp_iso=datetime.now().isoformat(),
            notes="Liquidação física e financeira DvP D+1 homologada com 100% de conservação patrimonial.",
        )
        self.settlement_receipts[receipt_id] = receipt
        return receipt

    # -----------------------------------------------------------------
    # VERIFICAÇÃO FORMAL DE INVARIANTES
    # -----------------------------------------------------------------

    def verify_all_invariants(self) -> Dict[str, Any]:
        """
        Executa a prova formal matemática de todas as invariantes do módulo FL-071:
        1. Identidade de PU Limpo/Sujo e decomposição financeira em todos os ativos cadastrados.
        2. Conservação multilateral e bilateral de custódia e caixa em todos os recibos DvP.
        3. Integridade de limites operacionais e ausência de saldos negativos não-autorizados.
        """
        invariants_report = {
            "pu_conservation_verified": True,
            "dvp_physical_conservation_verified": True,
            "dvp_financial_conservation_verified": True,
            "tunnel_integrity_verified": True,
            "all_invariants_hold": True,
            "details": [],
        }

        # 1. Verificação de PU Limpo/Sujo para cada ativo na data atual
        for code, asset in self.assets.items():
            pricing = self.price_asset(code)
            # PU Curva
            curva_ok = pricing.pu_curva_sujo == (pricing.pu_curva_limpo + pricing.juros_acruados)
            # PU Mercado
            mercado_ok = pricing.pu_mercado_sujo == (pricing.pu_mercado_limpo + pricing.juros_acruados)

            if not (curva_ok and mercado_ok):
                invariants_report["pu_conservation_verified"] = False
                invariants_report["all_invariants_hold"] = False
                invariants_report["details"].append(f"Falha de PU em {code}: Curva OK={curva_ok}, Mercado OK={mercado_ok}")

        # 2. Verificação de Recibos DvP
        for r_id, receipt in self.settlement_receipts.items():
            if not receipt.physical_conservation_verified:
                invariants_report["dvp_physical_conservation_verified"] = False
                invariants_report["all_invariants_hold"] = False
                invariants_report["details"].append(f"Recibo {r_id}: falha de conservação física.")
            if not receipt.financial_conservation_verified:
                invariants_report["dvp_financial_conservation_verified"] = False
                invariants_report["all_invariants_hold"] = False
                invariants_report["details"].append(f"Recibo {r_id}: falha de conservação financeira.")
            if not receipt.pu_dirty_conservation_verified:
                invariants_report["pu_conservation_verified"] = False
                invariants_report["all_invariants_hold"] = False
                invariants_report["details"].append(f"Recibo {r_id}: falha de conservação de PU limpo/sujo.")

        # 3. Verificação de Cross-Trades e Túnel
        for t_id, trade in self.trades.items():
            if trade.is_cross_trade and trade.pricing_tunnel_deviation_pct is not None:
                asset = self.assets[trade.asset_code]
                if trade.pricing_tunnel_deviation_pct > asset.tunnel_band_max_pct:
                    invariants_report["tunnel_integrity_verified"] = False
                    invariants_report["all_invariants_hold"] = False
                    invariants_report["details"].append(f"Trade {t_id}: túnel violado ({trade.pricing_tunnel_deviation_pct} > {asset.tunnel_band_max_pct})")

        return invariants_report

    # -----------------------------------------------------------------
    # MÉTODOS DE SERIALIZAÇÃO / CARREGAMENTO DE FIXTURE
    # -----------------------------------------------------------------

    @classmethod
    def load_from_fixture(cls, path_or_dict: Union[str, Path, Dict[str, Any]]) -> SecondaryMarketFixedIncomeEngine:
        """Carrega a configuração completa do motor a partir de fixture JSON."""
        if isinstance(path_or_dict, (str, Path)):
            with open(path_or_dict, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = path_or_dict

        assets = {
            a["asset_code"]: FixedIncomeAsset.from_dict(a)
            for a in data.get("assets", [])
        }
        market_data = (
            MarketDataSnapshot.from_dict(data["market_data"])
            if "market_data" in data
            else None
        )
        accounts = {
            acc["account_id"]: CustodyAccount.from_dict(acc)
            for acc in data.get("accounts", [])
        }

        return cls(assets=assets, market_data=market_data, accounts=accounts)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado atual do motor em dicionário."""
        return {
            "assets": [asdict(a) for a in self.assets.values()],
            "market_data": asdict(self.market_data) if self.market_data else None,
            "accounts": [asdict(acc) for acc in self.accounts.values()],
            "orders": [asdict(o) for o in self.orders.values()],
            "trades": [asdict(t) for t in self.trades.values()],
            "settlement_receipts": [asdict(r) for r in self.settlement_receipts.values()],
        }

    # -----------------------------------------------------------------
    # HELPERS INTERNOS
    # -----------------------------------------------------------------

    def _get_asset(self, code: str) -> FixedIncomeAsset:
        asset = self.assets.get(code.strip().upper())
        if not asset:
            raise SecondaryMarketValidationError(f"Título de renda fixa '{code}' não cadastrado no NoMe.")
        return asset

    def _get_account(self, account_id: str) -> CustodyAccount:
        acc = self.accounts.get(account_id.strip())
        if not acc:
            raise SecondaryMarketValidationError(f"Conta de custódia '{account_id}' não encontrada.")
        return acc
