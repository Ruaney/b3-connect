"""Integração Tesouro Direto & API Hub de Varejo B3 (FL-031).

Este módulo implementa a modelagem de domínio, validações pré-negociação,
cálculo determinístico de valores financeiros brutos, provisionamento de
Taxa de Custódia B3 (0,20% a.a. pro-rata die), controle de limite mensal
de aplicação por CPF e liquidação D+1 de aplicações e recompras pelo
Tesouro Nacional via API Hub Varejo B3 para Agentes de Custódia.

Títulos suportados:
- Tesouro Selic (LFT)
- Tesouro Prefixado (LTN)
- Tesouro Prefixado com Juros Semestrais (NTN-F)
- Tesouro IPCA+ (NTN-B Principal)
- Tesouro IPCA+ com Juros Semestrais (NTN-B)
- Tesouro Renda+ (NTN-B1)
- Tesouro Educa+ (NTN-B1 Educa+)

Estritamente baseado na biblioteca padrão do Python (`dataclasses`, `Decimal`,
`datetime`, `enum`, `json`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Constantes Regulatórias do Tesouro Direto / B3
# ---------------------------------------------------------------------------

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_QUANTITY = Decimal("0.01")
QUANTIZE_RATE = Decimal("0.0001")

MIN_ORDER_AMOUNT_BRL = Decimal("30.00")
MIN_BOND_FRACTION = Decimal("0.01")
DEFAULT_MONTHLY_CPF_LIMIT_BRL = Decimal("1000000.00")
DEFAULT_B3_CUSTODY_FEE_ANNUAL_PCT = Decimal("0.20")
DEFAULT_DAY_COUNT_BASE = 365


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class TesouroDiretoError(Exception):
    """Exceção base para operações do módulo Tesouro Direto B3."""
    pass


class TesouroValidationError(TesouroDiretoError):
    """Violação de regra de negócio ou validação de ordem do Tesouro Direto."""
    pass


class MinimumAmountError(TesouroValidationError):
    """Valor financeiro bruto da operação inferior ao mínimo de R$ 30,00."""
    pass


class InvalidQuantityFractionError(TesouroValidationError):
    """Quantidade de títulos não é múltiplo exato de 0,01 ou não é positiva."""
    pass


class MonthlyLimitExceededError(TesouroValidationError):
    """Ordem de aplicação excede o limite mensal de compras por CPF."""
    pass


class InsufficientCustodyBalanceError(TesouroValidationError):
    """Saldo insuficiente em custódia para resgate antecipado (recompra)."""
    pass


# ---------------------------------------------------------------------------
# Funções Auxiliares de Conversão e Normalização
# ---------------------------------------------------------------------------

def _to_decimal(value: Any, field_name: str) -> Decimal:
    """Converte valor numérico ou textual para Decimal com validação."""
    if isinstance(value, Decimal):
        return value
    if value is None or isinstance(value, bool):
        raise TesouroValidationError(f"Campo '{field_name}' não pode ser nulo ou booleano.")
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise TesouroValidationError(
            f"Valor decimal inválido para '{field_name}': {value!r}"
        ) from exc
    if not dec.is_finite():
        raise TesouroValidationError(f"Valor não finito para '{field_name}': {value!r}")
    return dec


def _to_date(value: Any, field_name: str) -> date:
    """Converte string ISO-8601 (YYYY-MM-DD) ou objeto date/datetime em date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value or not isinstance(value, str):
        raise TesouroValidationError(f"Data inválida para '{field_name}': {value!r}")
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise TesouroValidationError(
            f"Formato de data inválido para '{field_name}' (esperado YYYY-MM-DD): {value!r}"
        ) from exc


def normalize_cpf(cpf: str) -> str:
    """Remove pontuação do CPF e valida que possui 11 dígitos numéricos."""
    if not cpf or not isinstance(cpf, str):
        raise TesouroValidationError("CPF do investidor deve ser informado.")
    digits = re.sub(r"\D", "", cpf)
    if len(digits) != 11:
        raise TesouroValidationError(
            f"CPF inválido '{cpf}': deve conter exatamente 11 dígitos numéricos."
        )
    return digits


def round_currency(amount: Decimal) -> Decimal:
    """Arredonda valores monetários para 2 casas decimais via ROUND_HALF_UP."""
    return amount.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------

class TreasuryBondType(str, Enum):
    """Modalidades de títulos públicos suportadas no Tesouro Direto B3."""

    TESOURO_SELIC = "TESOURO_SELIC"  # LFT
    TESOURO_PREFIXADO = "TESOURO_PREFIXADO"  # LTN
    TESOURO_PREFIXADO_JUROS_SEMESTRAIS = "TESOURO_PREFIXADO_JUROS_SEMESTRAIS"  # NTN-F
    TESOURO_IPCA = "TESOURO_IPCA"  # NTN-B Principal
    TESOURO_IPCA_JUROS_SEMESTRAIS = "TESOURO_IPCA_JUROS_SEMESTRAIS"  # NTN-B
    TESOURO_RENDA_MAIS = "TESOURO_RENDA_MAIS"  # NTN-B1 (Aposentadoria Extra)
    TESOURO_EDUCA_MAIS = "TESOURO_EDUCA_MAIS"  # NTN-B1 Educa+

    @classmethod
    def from_str(cls, value: Any) -> TreasuryBondType:
        """Converte códigos, siglas STN/Selic ou nomes comerciais em TreasuryBondType."""
        if isinstance(value, cls):
            return value
        if not value:
            raise TesouroValidationError("Tipo de título público não pode ser vazio.")
        norm = str(value).strip().upper().replace("-", "_").replace(" ", "_")
        norm = norm.replace("+", "_MAIS")
        while "__" in norm:
            norm = norm.replace("__", "_")
        norm_clean = norm.strip("_")

        synonyms = {
            "TESOURO_SELIC": cls.TESOURO_SELIC,
            "LFT": cls.TESOURO_SELIC,
            "SELIC": cls.TESOURO_SELIC,
            "TESOURO_PREFIXADO": cls.TESOURO_PREFIXADO,
            "LTN": cls.TESOURO_PREFIXADO,
            "PREFIXADO": cls.TESOURO_PREFIXADO,
            "TESOURO_PREFIXADO_JUROS_SEMESTRAIS": cls.TESOURO_PREFIXADO_JUROS_SEMESTRAIS,
            "TESOURO_PREFIXADO_COM_JUROS_SEMESTRAIS": cls.TESOURO_PREFIXADO_JUROS_SEMESTRAIS,
            "NTN_F": cls.TESOURO_PREFIXADO_JUROS_SEMESTRAIS,
            "NTNF": cls.TESOURO_PREFIXADO_JUROS_SEMESTRAIS,
            "TESOURO_IPCA": cls.TESOURO_IPCA,
            "TESOURO_IPCA_MAIS": cls.TESOURO_IPCA,
            "NTN_B_PRINCIPAL": cls.TESOURO_IPCA,
            "NTNB_PRINCIPAL": cls.TESOURO_IPCA,
            "IPCA_PRINCIPAL": cls.TESOURO_IPCA,
            "TESOURO_IPCA_JUROS_SEMESTRAIS": cls.TESOURO_IPCA_JUROS_SEMESTRAIS,
            "TESOURO_IPCA_MAIS_COM_JUROS_SEMESTRAIS": cls.TESOURO_IPCA_JUROS_SEMESTRAIS,
            "TESOURO_IPCA_MAIS_JUROS_SEMESTRAIS": cls.TESOURO_IPCA_JUROS_SEMESTRAIS,
            "NTN_B": cls.TESOURO_IPCA_JUROS_SEMESTRAIS,
            "NTNB": cls.TESOURO_IPCA_JUROS_SEMESTRAIS,
            "TESOURO_RENDA_MAIS": cls.TESOURO_RENDA_MAIS,
            "TESOURO_RENDA": cls.TESOURO_RENDA_MAIS,
            "RENDA_MAIS": cls.TESOURO_RENDA_MAIS,
            "NTN_B1": cls.TESOURO_RENDA_MAIS,
            "NTNB1": cls.TESOURO_RENDA_MAIS,
            "TESOURO_EDUCA_MAIS": cls.TESOURO_EDUCA_MAIS,
            "TESOURO_EDUCA": cls.TESOURO_EDUCA_MAIS,
            "EDUCA_MAIS": cls.TESOURO_EDUCA_MAIS,
            "NTN_B1_EDUCA_MAIS": cls.TESOURO_EDUCA_MAIS,
        }
        if norm_clean in synonyms:
            return synonyms[norm_clean]
        valid = ", ".join(m.value for m in cls)
        raise TesouroValidationError(
            f"Modalidade de título do Tesouro Direto inválida: {value!r}. "
            f"Valores suportados: {valid}"
        )

    @property
    def stn_acronym(self) -> str:
        """Retorna a sigla clássica STN/Selic correspondente à modalidade."""
        mapping = {
            TreasuryBondType.TESOURO_SELIC: "LFT",
            TreasuryBondType.TESOURO_PREFIXADO: "LTN",
            TreasuryBondType.TESOURO_PREFIXADO_JUROS_SEMESTRAIS: "NTN-F",
            TreasuryBondType.TESOURO_IPCA: "NTN-B Principal",
            TreasuryBondType.TESOURO_IPCA_JUROS_SEMESTRAIS: "NTN-B",
            TreasuryBondType.TESOURO_RENDA_MAIS: "NTN-B1",
            TreasuryBondType.TESOURO_EDUCA_MAIS: "NTN-B1 Educa+",
        }
        return mapping[self]

    @property
    def indexer_name(self) -> str:
        """Retorna o indexador base do título público."""
        if self == TreasuryBondType.TESOURO_SELIC:
            return "SELIC"
        if self in (
            TreasuryBondType.TESOURO_PREFIXADO,
            TreasuryBondType.TESOURO_PREFIXADO_JUROS_SEMESTRAIS,
        ):
            return "PREFIXADO"
        return "IPCA"


class OrderOperationType(str, Enum):
    """Tipo de operação da ordem no Tesouro Direto B3."""

    INVESTMENT = "INVESTMENT"  # Aplicação / Compra de títulos
    REDEMPTION = "REDEMPTION"  # Resgate antecipado / Recompra pelo Tesouro Nacional

    @classmethod
    def from_str(cls, value: Any) -> OrderOperationType:
        """Converte e normaliza strings de operação para OrderOperationType."""
        if isinstance(value, cls):
            return value
        if not value:
            raise TesouroValidationError("Tipo de operação não pode ser vazio.")
        norm = str(value).strip().upper()
        synonyms = {
            "INVESTMENT": cls.INVESTMENT,
            "BUY": cls.INVESTMENT,
            "COMPRA": cls.INVESTMENT,
            "APLICACAO": cls.INVESTMENT,
            "APPLICATION": cls.INVESTMENT,
            "SUBSCRIPTION": cls.INVESTMENT,
            "REDEMPTION": cls.REDEMPTION,
            "SELL": cls.REDEMPTION,
            "VENDA": cls.REDEMPTION,
            "RESGATE": cls.REDEMPTION,
            "RECOMPRA": cls.REDEMPTION,
            "BUYBACK": cls.REDEMPTION,
        }
        if norm in synonyms:
            return synonyms[norm]
        valid = ", ".join(m.value for m in cls)
        raise TesouroValidationError(
            f"Tipo de operação inválido: {value!r}. Valores permitidos: {valid}"
        )


# ---------------------------------------------------------------------------
# Modelos de Dados (Dataclasses)
# ---------------------------------------------------------------------------

@dataclass
class TreasuryBond:
    """Representa um Título Público Federal disponível na cesta do Tesouro Direto."""

    bond_code: str
    bond_type: TreasuryBondType
    bond_name: str
    maturity_date: date
    unit_buy_price: Decimal
    unit_sell_price: Decimal
    buy_rate_pct: Decimal = Decimal("0.0000")
    sell_rate_pct: Decimal = Decimal("0.0000")
    isin: str = ""
    indexer: str = ""
    pays_semiannual_interest: bool = False
    suspended: bool = False

    def __post_init__(self) -> None:
        if not self.bond_code or not str(self.bond_code).strip():
            raise TesouroValidationError("Código do título ('bond_code') é obrigatório.")
        self.bond_code = str(self.bond_code).strip()
        self.bond_type = TreasuryBondType.from_str(self.bond_type)
        if not self.bond_name or not str(self.bond_name).strip():
            raise TesouroValidationError("Nome do título ('bond_name') é obrigatório.")
        self.bond_name = str(self.bond_name).strip()
        self.maturity_date = _to_date(self.maturity_date, "maturity_date")
        self.unit_buy_price = round_currency(
            _to_decimal(self.unit_buy_price, "unit_buy_price")
        )
        self.unit_sell_price = round_currency(
            _to_decimal(self.unit_sell_price, "unit_sell_price")
        )
        self.buy_rate_pct = _to_decimal(self.buy_rate_pct, "buy_rate_pct").quantize(
            QUANTIZE_RATE, rounding=ROUND_HALF_UP
        )
        self.sell_rate_pct = _to_decimal(self.sell_rate_pct, "sell_rate_pct").quantize(
            QUANTIZE_RATE, rounding=ROUND_HALF_UP
        )
        if self.unit_buy_price <= Decimal("0.00"):
            raise TesouroValidationError("PU de compra deve ser estritamente positivo.")
        if self.unit_sell_price <= Decimal("0.00"):
            raise TesouroValidationError("PU de venda deve ser estritamente positivo.")
        if not self.indexer:
            self.indexer = self.bond_type.indexer_name
        if self.bond_type in (
            TreasuryBondType.TESOURO_PREFIXADO_JUROS_SEMESTRAIS,
            TreasuryBondType.TESOURO_IPCA_JUROS_SEMESTRAIS,
        ):
            self.pays_semiannual_interest = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TreasuryBond:
        """Instancia TreasuryBond a partir de um dicionário JSON."""
        return cls(
            bond_code=data["bond_code"],
            bond_type=TreasuryBondType.from_str(data["bond_type"]),
            bond_name=data["bond_name"],
            maturity_date=_to_date(data["maturity_date"], "maturity_date"),
            unit_buy_price=_to_decimal(data["unit_buy_price"], "unit_buy_price"),
            unit_sell_price=_to_decimal(data["unit_sell_price"], "unit_sell_price"),
            buy_rate_pct=_to_decimal(data.get("buy_rate_pct", "0.0000"), "buy_rate_pct"),
            sell_rate_pct=_to_decimal(data.get("sell_rate_pct", "0.0000"), "sell_rate_pct"),
            isin=str(data.get("isin", "")).strip(),
            indexer=str(data.get("indexer", "")).strip(),
            pays_semiannual_interest=bool(data.get("pays_semiannual_interest", False)),
            suspended=bool(data.get("suspended", False)),
        )


@dataclass
class CustodyFeeCalculation:
    """Memória de cálculo da Taxa de Custódia B3 (0,20% a.a. pro-rata die) e Taxa do Agente."""

    gross_amount: Decimal
    annual_b3_fee_rate_pct: Decimal
    holding_days: int
    day_count_base: int
    b3_custody_fee: Decimal
    annual_agent_fee_rate_pct: Decimal = Decimal("0.00")
    agent_custody_fee: Decimal = Decimal("0.00")
    total_custody_fee: Decimal = Decimal("0.00")
    exempt_amount: Decimal = Decimal("0.00")
    taxable_base_amount: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.gross_amount = round_currency(_to_decimal(self.gross_amount, "gross_amount"))
        self.annual_b3_fee_rate_pct = _to_decimal(
            self.annual_b3_fee_rate_pct, "annual_b3_fee_rate_pct"
        )
        self.holding_days = int(self.holding_days)
        self.day_count_base = int(self.day_count_base)
        self.b3_custody_fee = round_currency(
            _to_decimal(self.b3_custody_fee, "b3_custody_fee")
        )
        self.annual_agent_fee_rate_pct = _to_decimal(
            self.annual_agent_fee_rate_pct, "annual_agent_fee_rate_pct"
        )
        self.agent_custody_fee = round_currency(
            _to_decimal(self.agent_custody_fee, "agent_custody_fee")
        )
        self.total_custody_fee = round_currency(
            self.b3_custody_fee + self.agent_custody_fee
        )
        self.exempt_amount = round_currency(
            _to_decimal(self.exempt_amount, "exempt_amount")
        )
        if self.taxable_base_amount == Decimal("0.00") and self.gross_amount > self.exempt_amount:
            self.taxable_base_amount = round_currency(
                max(Decimal("0.00"), self.gross_amount - self.exempt_amount)
            )
        else:
            self.taxable_base_amount = round_currency(
                _to_decimal(self.taxable_base_amount, "taxable_base_amount")
            )


@dataclass
class TreasuryDirectOrder:
    """Representa uma ordem de aplicação ou resgate no Tesouro Direto B3."""

    order_id: str
    investor_cpf: str
    investor_name: str
    custody_account: str
    custody_agent_code: str
    operation_type: OrderOperationType
    bond_code: str
    bond_type: TreasuryBondType
    quantity: Decimal
    unit_price: Decimal
    trade_date: date
    settlement_date: Optional[date] = None
    b3_protocol: str = ""
    holding_days: int = 0
    agent_fee_rate_annual_pct: Decimal = Decimal("0.00")
    gross_amount: Decimal = Decimal("0.00")
    b3_custody_fee: Decimal = Decimal("0.00")
    agent_custody_fee: Decimal = Decimal("0.00")
    net_settlement_amount: Decimal = Decimal("0.00")
    fee_calculation: Optional[CustodyFeeCalculation] = None
    status: str = "PENDING"

    def __post_init__(self) -> None:
        if not self.order_id or not str(self.order_id).strip():
            raise TesouroValidationError("Identificador da ordem ('order_id') é obrigatório.")
        self.order_id = str(self.order_id).strip()
        self.investor_cpf = normalize_cpf(self.investor_cpf)
        if not self.investor_name or not str(self.investor_name).strip():
            raise TesouroValidationError("Nome do investidor ('investor_name') é obrigatório.")
        self.investor_name = str(self.investor_name).strip()
        if not self.custody_account or not str(self.custody_account).strip():
            raise TesouroValidationError("Conta de custódia ('custody_account') é obrigatória.")
        self.custody_account = str(self.custody_account).strip()
        if not self.custody_agent_code or not str(self.custody_agent_code).strip():
            raise TesouroValidationError(
                "Código do Agente de Custódia ('custody_agent_code') é obrigatório."
            )
        self.custody_agent_code = str(self.custody_agent_code).strip()
        self.operation_type = OrderOperationType.from_str(self.operation_type)
        if not self.bond_code or not str(self.bond_code).strip():
            raise TesouroValidationError("Código do título ('bond_code') é obrigatório.")
        self.bond_code = str(self.bond_code).strip()
        self.bond_type = TreasuryBondType.from_str(self.bond_type)
        self.quantity = _to_decimal(self.quantity, "quantity")
        self.unit_price = round_currency(_to_decimal(self.unit_price, "unit_price"))
        self.trade_date = _to_date(self.trade_date, "trade_date")
        if self.settlement_date is None:
            self.settlement_date = TesouroDiretoEngine.calculate_d_plus_1_settlement_date(
                self.trade_date
            )
        else:
            self.settlement_date = _to_date(self.settlement_date, "settlement_date")
        self.holding_days = int(self.holding_days)
        if self.holding_days < 0:
            raise TesouroValidationError(
                f"Dias de custódia ('holding_days') não pode ser negativo: {self.holding_days}"
            )
        self.agent_fee_rate_annual_pct = _to_decimal(
            self.agent_fee_rate_annual_pct, "agent_fee_rate_annual_pct"
        )
        if self.agent_fee_rate_annual_pct < Decimal("0.00"):
            raise TesouroValidationError(
                "Taxa anual do agente de custódia não pode ser negativa."
            )

    @property
    def competence_month(self) -> str:
        """Retorna o mês de competência da ordem no formato 'YYYY-MM'."""
        return self.trade_date.strftime("%Y-%m")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TreasuryDirectOrder:
        """Constrói uma instância de TreasuryDirectOrder a partir de dicionário JSON."""
        return cls(
            order_id=data["order_id"],
            b3_protocol=str(data.get("b3_protocol", "")).strip(),
            investor_cpf=data["investor_cpf"],
            investor_name=data["investor_name"],
            custody_account=data["custody_account"],
            custody_agent_code=data["custody_agent_code"],
            operation_type=OrderOperationType.from_str(data["operation_type"]),
            bond_code=data["bond_code"],
            bond_type=TreasuryBondType.from_str(data["bond_type"]),
            quantity=_to_decimal(data["quantity"], "quantity"),
            unit_price=_to_decimal(data["unit_price"], "unit_price"),
            trade_date=_to_date(data["trade_date"], "trade_date"),
            settlement_date=(
                _to_date(data["settlement_date"], "settlement_date")
                if data.get("settlement_date")
                else None
            ),
            holding_days=int(data.get("holding_days", 0)),
            agent_fee_rate_annual_pct=_to_decimal(
                data.get("agent_fee_rate_annual_pct", "0.00"),
                "agent_fee_rate_annual_pct",
            ),
        )


# ---------------------------------------------------------------------------
# Motor de Regras de Negócio e Liquidação Tesouro Direto B3
# ---------------------------------------------------------------------------

class TesouroDiretoEngine:
    """Motor determinístico para validação, cálculo de taxas e liquidação do Tesouro Direto B3."""

    def __init__(
        self,
        minimum_order_amount: Decimal = MIN_ORDER_AMOUNT_BRL,
        minimum_quantity_fraction: Decimal = MIN_BOND_FRACTION,
        monthly_limit_per_cpf: Decimal = DEFAULT_MONTHLY_CPF_LIMIT_BRL,
        b3_custody_fee_rate_annual_pct: Decimal = DEFAULT_B3_CUSTODY_FEE_ANNUAL_PCT,
        day_count_base: int = DEFAULT_DAY_COUNT_BASE,
    ) -> None:
        self.minimum_order_amount = round_currency(
            _to_decimal(minimum_order_amount, "minimum_order_amount")
        )
        self.minimum_quantity_fraction = _to_decimal(
            minimum_quantity_fraction, "minimum_quantity_fraction"
        )
        self.monthly_limit_per_cpf = round_currency(
            _to_decimal(monthly_limit_per_cpf, "monthly_limit_per_cpf")
        )
        self.b3_custody_fee_rate_annual_pct = _to_decimal(
            b3_custody_fee_rate_annual_pct, "b3_custody_fee_rate_annual_pct"
        )
        if day_count_base <= 0:
            raise TesouroValidationError("Base anual de dias deve ser estritamente positiva.")
        self.day_count_base = int(day_count_base)

        # Catálogo de títulos registrados: bond_code -> TreasuryBond
        self.bonds_catalog: Dict[str, TreasuryBond] = {}
        # Ordens processadas e liquidadas
        self.processed_orders: List[TreasuryDirectOrder] = []
        # Acumulado mensal de compras por (cpf_normalizado, 'YYYY-MM') -> Decimal
        self._monthly_investments: Dict[Tuple[str, str], Decimal] = {}
        # Posições opcionais de custódia por (cpf_normalizado, bond_code) -> Decimal
        self._custody_positions: Dict[Tuple[str, str], Decimal] = {}

    # -----------------------------------------------------------------------
    # Gestão de Catálogo e Posições
    # -----------------------------------------------------------------------

    def register_bond(self, bond: TreasuryBond) -> None:
        """Registra ou atualiza um título público na cesta do Tesouro Direto."""
        self.bonds_catalog[bond.bond_code] = bond

    def get_bond(self, bond_code: str) -> Optional[TreasuryBond]:
        """Recupera um título público pelo seu código na cesta."""
        return self.bonds_catalog.get(bond_code)

    def set_custody_position(
        self, investor_cpf: str, bond_code: str, quantity: Decimal
    ) -> None:
        """Define o saldo inicial de custódia de um investidor para um título específico."""
        cpf_norm = normalize_cpf(investor_cpf)
        qty = _to_decimal(quantity, "quantity")
        if qty < Decimal("0.00"):
            raise TesouroValidationError("Saldo de custódia não pode ser negativo.")
        self._custody_positions[(cpf_norm, bond_code)] = qty

    def get_custody_position(self, investor_cpf: str, bond_code: str) -> Decimal:
        """Retorna a quantidade atual custodiada pelo CPF para o título informado."""
        cpf_norm = normalize_cpf(investor_cpf)
        return self._custody_positions.get((cpf_norm, bond_code), Decimal("0.00"))

    def get_monthly_invested_by_cpf(
        self, investor_cpf: str, competence_month: str
    ) -> Decimal:
        """Retorna o valor bruto total investido pelo CPF no mês de competência ('YYYY-MM')."""
        cpf_norm = normalize_cpf(investor_cpf)
        return self._monthly_investments.get(
            (cpf_norm, competence_month), Decimal("0.00")
        )

    # -----------------------------------------------------------------------
    # Regras de Validação e Cálculos Determinísticos
    # -----------------------------------------------------------------------

    @staticmethod
    def calculate_d_plus_1_settlement_date(trade_date: date) -> date:
        """Calcula a data de liquidação D+1 dia útil (pulando sábado e domingo)."""
        next_day = trade_date + timedelta(days=1)
        while next_day.weekday() >= 5:  # 5 = Sábado, 6 = Domingo
            next_day += timedelta(days=1)
        return next_day

    def validate_quantity_fraction(self, quantity: Decimal) -> Decimal:
        """Valida que a quantidade é estritamente positiva e múltiplo exato de 0,01 título."""
        qty = _to_decimal(quantity, "quantity")
        if qty <= Decimal("0.00"):
            raise InvalidQuantityFractionError(
                f"Quantidade deve ser estritamente positiva: {qty}"
            )
        remainder = qty % self.minimum_quantity_fraction
        if remainder != Decimal("0"):
            raise InvalidQuantityFractionError(
                f"Quantidade ({qty}) inválida: deve ser múltiplo exato de "
                f"{self.minimum_quantity_fraction} título."
            )
        return qty.quantize(QUANTIZE_QUANTITY)

    def calculate_gross_amount(
        self, quantity: Decimal, unit_price: Decimal
    ) -> Decimal:
        """Calcula o valor financeiro bruto exato da ordem: round(Q * PU, 2)."""
        qty = self.validate_quantity_fraction(quantity)
        pu = round_currency(_to_decimal(unit_price, "unit_price"))
        if pu <= Decimal("0.00"):
            raise TesouroValidationError(
                f"Preço unitário deve ser estritamente positivo: {pu}"
            )
        return round_currency(qty * pu)

    def validate_minimum_amount(self, gross_amount: Decimal) -> Decimal:
        """Valida se o valor financeiro bruto atinge o piso mínimo regulamentar (R$ 30,00)."""
        gross = round_currency(_to_decimal(gross_amount, "gross_amount"))
        if gross < self.minimum_order_amount:
            raise MinimumAmountError(
                f"Valor financeiro bruto (R$ {gross}) é inferior ao mínimo "
                f"regulamentar de R$ {self.minimum_order_amount}."
            )
        return gross

    def calculate_custody_fee(
        self,
        gross_amount: Decimal,
        holding_days: int,
        annual_b3_fee_rate_pct: Optional[Decimal] = None,
        annual_agent_fee_rate_pct: Decimal = Decimal("0.00"),
        exempt_amount: Decimal = Decimal("0.00"),
    ) -> CustodyFeeCalculation:
        """Calcula a Taxa de Custódia B3 (0,20% a.a. pro-rata die) e taxa do agente.

        Fórmula:
            Taxa_B3 = round(Base_Tributavel * (Taxa_Anual_B3 / 100) * (holding_days / base_dias), 2)
        """
        gross = round_currency(_to_decimal(gross_amount, "gross_amount"))
        if gross < Decimal("0.00"):
            raise TesouroValidationError("Valor bruto não pode ser negativo.")
        if holding_days < 0:
            raise TesouroValidationError(
                f"Dias de custódia ('holding_days') não pode ser negativo: {holding_days}"
            )

        b3_rate = (
            self.b3_custody_fee_rate_annual_pct
            if annual_b3_fee_rate_pct is None
            else _to_decimal(annual_b3_fee_rate_pct, "annual_b3_fee_rate_pct")
        )
        agent_rate = _to_decimal(
            annual_agent_fee_rate_pct, "annual_agent_fee_rate_pct"
        )
        exempt = round_currency(_to_decimal(exempt_amount, "exempt_amount"))
        taxable_base = max(Decimal("0.00"), gross - exempt)

        if holding_days == 0 or taxable_base == Decimal("0.00"):
            b3_fee = Decimal("0.00")
            agent_fee = Decimal("0.00")
        else:
            day_fraction = Decimal(holding_days) / Decimal(self.day_count_base)
            b3_fee = round_currency(
                taxable_base * (b3_rate / Decimal("100")) * day_fraction
            )
            agent_fee = round_currency(
                taxable_base * (agent_rate / Decimal("100")) * day_fraction
            )

        return CustodyFeeCalculation(
            gross_amount=gross,
            annual_b3_fee_rate_pct=b3_rate,
            holding_days=holding_days,
            day_count_base=self.day_count_base,
            b3_custody_fee=b3_fee,
            annual_agent_fee_rate_pct=agent_rate,
            agent_custody_fee=agent_fee,
            total_custody_fee=round_currency(b3_fee + agent_fee),
            exempt_amount=exempt,
            taxable_base_amount=taxable_base,
        )

    def calculate_net_settlement_amount(
        self,
        operation_type: OrderOperationType,
        gross_amount: Decimal,
        custody_fee_calc: CustodyFeeCalculation,
    ) -> Decimal:
        """Calcula o valor líquido de liquidação da operação em D+1.

        - INVESTMENT (Aplicação): Valor Bruto + Taxas incidentes no ato.
        - REDEMPTION (Resgate / Recompra STN): Valor Bruto - Taxas de custódia provisionadas.
        """
        op = OrderOperationType.from_str(operation_type)
        gross = round_currency(_to_decimal(gross_amount, "gross_amount"))
        total_fees = custody_fee_calc.total_custody_fee

        if op == OrderOperationType.INVESTMENT:
            return round_currency(gross + total_fees)
        else:
            net = round_currency(gross - total_fees)
            if net < Decimal("0.00"):
                raise TesouroValidationError(
                    f"Valor líquido de resgate não pode ser negativo: {net}"
                )
            return net

    def validate_monthly_cpf_limit(
        self,
        investor_cpf: str,
        competence_month: str,
        order_gross_amount: Decimal,
    ) -> None:
        """Verifica se a nova aplicação respeita o teto mensal por CPF."""
        cpf_norm = normalize_cpf(investor_cpf)
        already_invested = self.get_monthly_invested_by_cpf(cpf_norm, competence_month)
        projected_total = round_currency(already_invested + order_gross_amount)
        if projected_total > self.monthly_limit_per_cpf:
            raise MonthlyLimitExceededError(
                f"Limite mensal de aplicação excedido para o CPF {cpf_norm} no mês "
                f"{competence_month}: acumulado R$ {already_invested} + ordem "
                f"R$ {order_gross_amount} = R$ {projected_total} > limite R$ {self.monthly_limit_per_cpf}."
            )

    # -----------------------------------------------------------------------
    # Processamento de Ordens Individuais e em Lote
    # -----------------------------------------------------------------------

    def process_order(
        self,
        order: TreasuryDirectOrder,
        enforce_custody_balance: bool = False,
    ) -> TreasuryDirectOrder:
        """Valida, calcula taxas e liquida uma ordem do Tesouro Direto B3."""
        # 1. Validação de datas (Liquidação D+1 >= Trade Date)
        if order.settlement_date is None:
            order.settlement_date = self.calculate_d_plus_1_settlement_date(
                order.trade_date
            )
        if order.settlement_date < order.trade_date:
            raise TesouroValidationError(
                f"Data de liquidação ({order.settlement_date}) não pode ser anterior "
                f"à data da operação ({order.trade_date})."
            )

        # 2. Validação contra o catálogo de títulos, se registrado
        catalog_bond = self.bonds_catalog.get(order.bond_code)
        if catalog_bond is not None:
            if catalog_bond.suspended:
                raise TesouroValidationError(
                    f"Negociação suspensa para o título {order.bond_code}."
                )
            if catalog_bond.bond_type != order.bond_type:
                raise TesouroValidationError(
                    f"Divergência de tipo de título para {order.bond_code}: "
                    f"ordem={order.bond_type.value}, catálogo={catalog_bond.bond_type.value}."
                )
            if order.trade_date >= catalog_bond.maturity_date:
                raise TesouroValidationError(
                    f"Título {order.bond_code} já vencido em {catalog_bond.maturity_date}."
                )

        # 3. Validação de múltiplo de 0,01 título e cálculo do valor financeiro bruto
        order.quantity = self.validate_quantity_fraction(order.quantity)
        gross_amount = self.calculate_gross_amount(order.quantity, order.unit_price)

        # 4. Validação de valor financeiro mínimo (R$ 30,00)
        self.validate_minimum_amount(gross_amount)

        # 5. Regras específicas por tipo de operação (Aplicação vs. Resgate)
        if order.operation_type == OrderOperationType.INVESTMENT:
            self.validate_monthly_cpf_limit(
                order.investor_cpf, order.competence_month, gross_amount
            )
        elif order.operation_type == OrderOperationType.REDEMPTION:
            if enforce_custody_balance or (order.investor_cpf, order.bond_code) in self._custody_positions:
                current_qty = self.get_custody_position(
                    order.investor_cpf, order.bond_code
                )
                if current_qty < order.quantity:
                    raise InsufficientCustodyBalanceError(
                        f"Saldo em custódia insuficiente para o CPF {order.investor_cpf} "
                        f"no título {order.bond_code}: disponível={current_qty}, "
                        f"solicitado={order.quantity}."
                    )

        # 6. Cálculo de Taxa de Custódia B3 (0,20% a.a. pro-rata die) e valor líquido
        fee_calc = self.calculate_custody_fee(
            gross_amount=gross_amount,
            holding_days=order.holding_days,
            annual_b3_fee_rate_pct=self.b3_custody_fee_rate_annual_pct,
            annual_agent_fee_rate_pct=order.agent_fee_rate_annual_pct,
        )
        net_amount = self.calculate_net_settlement_amount(
            operation_type=order.operation_type,
            gross_amount=gross_amount,
            custody_fee_calc=fee_calc,
        )

        order.gross_amount = gross_amount
        order.b3_custody_fee = fee_calc.b3_custody_fee
        order.agent_custody_fee = fee_calc.agent_custody_fee
        order.net_settlement_amount = net_amount
        order.fee_calculation = fee_calc
        order.status = "SETTLED_D1"

        # 7. Atualização de acumulado mensal de aplicação e posição de custódia
        if order.operation_type == OrderOperationType.INVESTMENT:
            key = (order.investor_cpf, order.competence_month)
            self._monthly_investments[key] = round_currency(
                self._monthly_investments.get(key, Decimal("0.00")) + gross_amount
            )
            pos_key = (order.investor_cpf, order.bond_code)
            self._custody_positions[pos_key] = (
                self._custody_positions.get(pos_key, Decimal("0.00")) + order.quantity
            )
        else:
            pos_key = (order.investor_cpf, order.bond_code)
            if pos_key in self._custody_positions:
                self._custody_positions[pos_key] = max(
                    Decimal("0.00"),
                    self._custody_positions[pos_key] - order.quantity,
                )

        self.processed_orders.append(order)
        return order

    def load_and_process_batch(
        self, json_path: Union[str, Path]
    ) -> Dict[str, Any]:
        """Carrega um lote JSON de ordens do Tesouro Direto, registra o catálogo e processa as ordens."""
        path = Path(json_path)
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)

        if "minimum_order_amount" in payload:
            self.minimum_order_amount = round_currency(
                _to_decimal(payload["minimum_order_amount"], "minimum_order_amount")
            )
        if "minimum_quantity_fraction" in payload:
            self.minimum_quantity_fraction = _to_decimal(
                payload["minimum_quantity_fraction"], "minimum_quantity_fraction"
            )
        if "monthly_limit_per_cpf" in payload:
            self.monthly_limit_per_cpf = round_currency(
                _to_decimal(payload["monthly_limit_per_cpf"], "monthly_limit_per_cpf")
            )
        if "b3_custody_fee_rate_annual_pct" in payload:
            self.b3_custody_fee_rate_annual_pct = _to_decimal(
                payload["b3_custody_fee_rate_annual_pct"],
                "b3_custody_fee_rate_annual_pct",
            )

        for bond_data in payload.get("bonds_catalog", []):
            bond = TreasuryBond.from_dict(bond_data)
            self.register_bond(bond)

        processed: List[TreasuryDirectOrder] = []
        for order_data in payload.get("orders", []):
            order = TreasuryDirectOrder.from_dict(order_data)
            self.process_order(order)
            processed.append(order)

        total_investments_gross = round_currency(
            sum(
                (
                    o.gross_amount
                    for o in processed
                    if o.operation_type == OrderOperationType.INVESTMENT
                ),
                Decimal("0.00"),
            )
        )
        total_redemptions_gross = round_currency(
            sum(
                (
                    o.gross_amount
                    for o in processed
                    if o.operation_type == OrderOperationType.REDEMPTION
                ),
                Decimal("0.00"),
            )
        )
        total_b3_fees = round_currency(
            sum((o.b3_custody_fee for o in processed), Decimal("0.00"))
        )
        total_net_settlement = round_currency(
            sum((o.net_settlement_amount for o in processed), Decimal("0.00"))
        )

        return {
            "batch_id": payload.get("batch_id", ""),
            "reference_date": payload.get("reference_date", ""),
            "custody_agent_code": payload.get("custody_agent_code", ""),
            "total_orders": len(processed),
            "total_investments_gross": total_investments_gross,
            "total_redemptions_gross": total_redemptions_gross,
            "total_b3_custody_fees": total_b3_fees,
            "total_net_settlement": total_net_settlement,
            "orders": processed,
        }
