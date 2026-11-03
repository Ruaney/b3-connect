"""Motor de ETFs — Criação e Resgate de Cotas no Mercado Primário via Cesta de Ativos / PCF na B3 (FL-035).

Implementação determinística 100% Python stdlib (`dataclasses`, `Decimal`, `datetime`, `enum`, `json`)
para o processamento do Mercado Primário de Fundos de Índice (ETFs — Resolução CVM 175, Anexo Normativo V)
junto à Central Depositária e Câmara B3:
- Estruturação e validação contábil do Arquivo de Composição da Carteira (PCF — Portfolio Composition File).
- Verificação estrita da identidade patrimonial do Lote Mínimo (Creation Unit):
  Sum(Qtd_i * Preco_Fechamento_i) + Cash_Component_Unit == Creation_Unit_Size * NAV_per_Quota.
- Validação de múltiplos inteiros exatos do Lote Mínimo (Creation Unit, ex: 50.000 cotas) nas ordens
  de Integralização (CREATION) e Resgate (REDEMPTION) submetidas por Agentes Autorizados (APs).
- Conferência prévia de saldo livre de custódia (in-kind) para cada ação componente da cesta (na Criação)
  e de cotas do ETF (no Resgate), prevenindo falhas de entrega em D+2.
- Cálculo exato do valor financeiro da cesta + Cash Component == Quotas * NAV e geração atômica das
  instruções de transferência de custódia (IMBARQ012/IMBARQ013) e liquidação financeira (IMBARQ004).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union


TWO_DECIMALS = Decimal("0.01")
FOUR_DECIMALS = Decimal("0.0001")
SIX_DECIMALS = Decimal("0.000001")


def _to_decimal(value: Any, field_name: str = "valor") -> Decimal:
    """Converte número ou string numérica para Decimal de forma determinística."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        raise EtfValidationError(f"Campo numérico inválido para {field_name}: {value!r}")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            raise EtfValidationError(f"Campo numérico vazio para {field_name}.")
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:
            raise EtfValidationError(
                f"Valor decimal inválido para {field_name}: {value!r}"
            ) from exc
    raise EtfValidationError(f"Tipo numérico não suportado para {field_name}: {type(value).__name__}")


def _to_int(value: Any, field_name: str = "quantidade") -> int:
    """Converte valor inteiro estrito (rejeitando frações) para int."""
    if isinstance(value, bool) or value is None:
        raise EtfValidationError(f"Inteiro inválido para {field_name}: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise EtfValidationError(
                f"O campo {field_name} deve ser um número inteiro exato, recebido: {value!r}"
            )
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        try:
            dec = Decimal(cleaned)
        except InvalidOperation as exc:
            raise EtfValidationError(f"Inteiro inválido para {field_name}: {value!r}") from exc
        if dec != dec.to_integral_value():
            raise EtfValidationError(
                f"O campo {field_name} deve ser um número inteiro exato, recebido: {value!r}"
            )
        return int(dec)
    raise EtfValidationError(f"Tipo inválido para inteiro em {field_name}: {type(value).__name__}")


# ==============================================================================
# 1. Exceções Customizadas
# ==============================================================================


class EtfPrimaryMarketError(Exception):
    """Exceção base do módulo de Mercado Primário de ETFs na B3 (FL-035)."""


class EtfValidationError(EtfPrimaryMarketError):
    """Erro de validação estrutural de campos do PCF ou de ordens primárias."""


class PcfParityError(EtfValidationError):
    """Violação da identidade contábil do PCF (Valor da Cesta + Cash Component != Quotas * NAV)."""


class InvalidCreationUnitMultipleError(EtfValidationError):
    """Ordem primária com quantidade de cotas que não é múltiplo exato do Lote Mínimo (Creation Unit)."""


class InsufficientBasketCustodyError(EtfPrimaryMarketError):
    """Saldo livre insuficiente em custódia para entrega in-kind de ações da cesta ou cotas do ETF."""

    def __init__(self, message: str, deficits: Optional[Dict[str, Dict[str, int]]] = None) -> None:
        super().__init__(message)
        self.deficits: Dict[str, Dict[str, int]] = deficits or {}


class InsufficientEtfQuotasError(InsufficientBasketCustodyError):
    """Saldo livre insuficiente de cotas do ETF para operação de Resgate (Redemption)."""


# ==============================================================================
# 2. Enumerações Operacionais
# ==============================================================================


class EtfOrderType(str, Enum):
    """Natureza da ordem no Mercado Primário de ETFs da B3."""

    CREATION = "CREATION"      # Integralização / Emissão Primária de Cotas via entrega de Cesta In-Kind
    REDEMPTION = "REDEMPTION"  # Resgate / Cancelamento Primário de Cotas via recebimento de Cesta In-Kind

    @property
    def is_creation(self) -> bool:
        """Retorna True se a ordem for de Integralização (Creation)."""
        return self == EtfOrderType.CREATION

    @property
    def is_redemption(self) -> bool:
        """Retorna True se a ordem for de Resgate (Redemption)."""
        return self == EtfOrderType.REDEMPTION

    @classmethod
    def from_value(cls, value: Any) -> EtfOrderType:
        """Converte strings e sinônimos operacionais para EtfOrderType."""
        if isinstance(value, EtfOrderType):
            return value
        if isinstance(value, str):
            norm = value.strip().upper().replace("-", "_").replace(" ", "_")
            if norm in ("CREATION", "INTEGRALIZACAO", "INTEGRALIZAÇÃO", "EMISSAO", "EMISSÃO", "MINT", "C"):
                return cls.CREATION
            if norm in ("REDEMPTION", "RESGATE", "CANCELAMENTO", "BURN", "R"):
                return cls.REDEMPTION
        raise EtfValidationError(f"Tipo de ordem primária de ETF inválido: {value!r}")


# ==============================================================================
# 3. Modelos de Domínio (PCF, Componentes da Cesta, Ordens e Instruções)
# ==============================================================================


@dataclass(frozen=True)
class BasketComponent:
    """Ativo componente da cesta teórica de um ETF no PCF (por 1 Lote Mínimo / Creation Unit)."""

    ticker: str
    isin: str
    quantity_per_unit: int
    closing_price: Decimal
    company_name: str = ""
    theoretical_weight_pct: Optional[Decimal] = None

    def __post_init__(self) -> None:
        ticker_clean = str(self.ticker).strip().upper()
        isin_clean = str(self.isin).strip().upper()
        if not ticker_clean:
            raise EtfValidationError("O ticker do ativo componente da cesta não pode ser vazio.")
        if len(isin_clean) != 12 or not isin_clean.isalnum():
            raise EtfValidationError(
                f"ISIN inválido para o componente {ticker_clean}: {self.isin!r} (esperado 12 caracteres alfanuméricos)."
            )
        qty = _to_int(self.quantity_per_unit, f"quantity_per_unit[{ticker_clean}]")
        if qty <= 0:
            raise EtfValidationError(
                f"A quantidade por Lote Mínimo de {ticker_clean} deve ser estritamente positiva: {qty}"
            )
        price = _to_decimal(self.closing_price, f"closing_price[{ticker_clean}]")
        if price <= Decimal("0"):
            raise EtfValidationError(
                f"O preço de fechamento de {ticker_clean} deve ser estritamente positivo: {price}"
            )
        weight: Optional[Decimal] = None
        if self.theoretical_weight_pct is not None:
            weight = _to_decimal(
                self.theoretical_weight_pct, f"theoretical_weight_pct[{ticker_clean}]"
            )

        object.__setattr__(self, "ticker", ticker_clean)
        object.__setattr__(self, "isin", isin_clean)
        object.__setattr__(self, "quantity_per_unit", qty)
        object.__setattr__(self, "closing_price", price.quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP))
        object.__setattr__(self, "company_name", str(self.company_name or "").strip())
        object.__setattr__(self, "theoretical_weight_pct", weight)

    @property
    def component_value_per_unit(self) -> Decimal:
        """Valor financeiro (R$) deste ativo em 1 Lote Mínimo (quantity_per_unit * closing_price)."""
        return (Decimal(self.quantity_per_unit) * self.closing_price).quantize(
            TWO_DECIMALS, rounding=ROUND_HALF_UP
        )

    def quantity_for_units(self, creation_units: int) -> int:
        """Quantidade inteira exigida deste ativo para N Lotes Mínimos."""
        units = _to_int(creation_units, "creation_units")
        if units <= 0:
            raise EtfValidationError(f"O número de Lotes Mínimos deve ser > 0, recebido: {units}")
        return self.quantity_per_unit * units

    def value_for_units(self, creation_units: int) -> Decimal:
        """Valor financeiro total (R$) deste ativo para N Lotes Mínimos."""
        qty = self.quantity_for_units(creation_units)
        return (Decimal(qty) * self.closing_price).quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BasketComponent:
        """Constrói um BasketComponent a partir de dicionário JSON."""
        return cls(
            ticker=data["ticker"],
            isin=data["isin"],
            quantity_per_unit=_to_int(data["quantity_per_unit"], "quantity_per_unit"),
            closing_price=_to_decimal(data["closing_price"], "closing_price"),
            company_name=data.get("company_name", ""),
            theoretical_weight_pct=(
                _to_decimal(data["theoretical_weight_pct"], "theoretical_weight_pct")
                if data.get("theoretical_weight_pct") is not None
                else None
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o componente da cesta para dicionário compatível com JSON."""
        result: Dict[str, Any] = {
            "ticker": self.ticker,
            "isin": self.isin,
            "company_name": self.company_name,
            "quantity_per_unit": self.quantity_per_unit,
            "closing_price": f"{self.closing_price:.2f}",
            "component_value_per_unit": f"{self.component_value_per_unit:.2f}",
        }
        if self.theoretical_weight_pct is not None:
            result["theoretical_weight_pct"] = f"{self.theoretical_weight_pct:.4f}"
        return result


@dataclass(frozen=True)
class PortfolioCompositionFile:
    """Arquivo de Composição da Carteira (PCF — Portfolio Composition File) de um ETF na B3."""

    pcf_id: str
    etf_ticker: str
    etf_isin: str
    creation_unit_size: int
    nav_per_quota: Decimal
    cash_component_per_unit: Decimal
    components: Tuple[BasketComponent, ...]
    reference_date: str = "2026-11-03"
    settlement_date: str = "2026-11-05"
    benchmark_index: str = "IBOV"
    fund_custody_account: str = "99001-0"
    etf_name: str = ""
    administrator_cnpj: str = ""

    def __post_init__(self) -> None:
        pcf_id_clean = str(self.pcf_id).strip()
        ticker_clean = str(self.etf_ticker).strip().upper()
        isin_clean = str(self.etf_isin).strip().upper()
        if not pcf_id_clean:
            raise EtfValidationError("O identificador do PCF (pcf_id) não pode ser vazio.")
        if not ticker_clean:
            raise EtfValidationError("O ticker do ETF (etf_ticker) não pode ser vazio.")
        if len(isin_clean) != 12 or not isin_clean.isalnum():
            raise EtfValidationError(
                f"ISIN inválido para o ETF {ticker_clean}: {self.etf_isin!r}"
            )

        unit_size = _to_int(self.creation_unit_size, "creation_unit_size")
        if unit_size <= 0:
            raise EtfValidationError(
                f"O tamanho do Lote Mínimo (creation_unit_size) deve ser > 0, recebido: {unit_size}"
            )

        nav = _to_decimal(self.nav_per_quota, "nav_per_quota")
        if nav <= Decimal("0"):
            raise EtfValidationError(
                f"O NAV por cota (nav_per_quota) deve ser estritamente positivo: {nav}"
            )

        cash_comp = _to_decimal(self.cash_component_per_unit, "cash_component_per_unit").quantize(
            TWO_DECIMALS, rounding=ROUND_HALF_UP
        )

        if not self.components:
            raise EtfValidationError("O PCF deve conter ao menos 1 ativo componente na cesta.")

        comp_tuple = tuple(
            c if isinstance(c, BasketComponent) else BasketComponent.from_dict(c)
            for c in self.components
        )
        seen_tickers = set()
        for comp in comp_tuple:
            if comp.ticker in seen_tickers:
                raise EtfValidationError(
                    f"Ativo componente duplicado no PCF {pcf_id_clean}: {comp.ticker}"
                )
            seen_tickers.add(comp.ticker)

        object.__setattr__(self, "pcf_id", pcf_id_clean)
        object.__setattr__(self, "etf_ticker", ticker_clean)
        object.__setattr__(self, "etf_isin", isin_clean)
        object.__setattr__(self, "creation_unit_size", unit_size)
        object.__setattr__(self, "nav_per_quota", nav)
        object.__setattr__(self, "cash_component_per_unit", cash_comp)
        object.__setattr__(self, "components", comp_tuple)
        object.__setattr__(self, "fund_custody_account", str(self.fund_custody_account).strip() or "99001-0")

        self.validate_nav_parity()

    @property
    def unit_nav_value(self) -> Decimal:
        """Valor Patrimonial Líquido (R$) de 1 Lote Mínimo (creation_unit_size * nav_per_quota)."""
        return (Decimal(self.creation_unit_size) * self.nav_per_quota).quantize(
            TWO_DECIMALS, rounding=ROUND_HALF_UP
        )

    @property
    def basket_equities_value_per_unit(self) -> Decimal:
        """Soma exata do valor financeiro (R$) de todas as ações da cesta para 1 Lote Mínimo."""
        total = sum((c.component_value_per_unit for c in self.components), Decimal("0.00"))
        return total.quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)

    @property
    def total_pcf_value_per_unit(self) -> Decimal:
        """Soma do valor financeiro da cesta de ações + Cash Component por Lote Mínimo."""
        return (self.basket_equities_value_per_unit + self.cash_component_per_unit).quantize(
            TWO_DECIMALS, rounding=ROUND_HALF_UP
        )

    @property
    def components_by_ticker(self) -> Dict[str, BasketComponent]:
        """Mapa indexado por ticker dos ativos componentes do PCF."""
        return {c.ticker: c for c in self.components}

    def validate_nav_parity(self) -> bool:
        """Valida a identidade contábil do PCF: Cesta + Cash Component == Creation Unit * NAV."""
        expected_nav = self.unit_nav_value
        actual_total = self.total_pcf_value_per_unit
        if actual_total != expected_nav:
            diff = actual_total - expected_nav
            raise PcfParityError(
                f"Divergência contábil no PCF {self.pcf_id} ({self.etf_ticker}): "
                f"Valor da Cesta (R$ {self.basket_equities_value_per_unit:.2f}) + "
                f"Cash Component (R$ {self.cash_component_per_unit:.2f}) = "
                f"R$ {actual_total:.2f} != Creation Unit NAV "
                f"({self.creation_unit_size} * R$ {self.nav_per_quota} = R$ {expected_nav:.2f}; "
                f"diferença = R$ {diff:.2f})."
            )
        return True

    def required_equities_for_units(self, creation_units: int) -> Dict[str, int]:
        """Retorna o mapa {ticker: quantidade_total} exigido para N Lotes Mínimos."""
        units = _to_int(creation_units, "creation_units")
        if units <= 0:
            raise EtfValidationError(f"Número de Lotes Mínimos inválido: {units}")
        return {comp.ticker: comp.quantity_for_units(units) for comp in self.components}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PortfolioCompositionFile:
        """Constrói um PortfolioCompositionFile a partir de dicionário JSON (direto ou aninhado em 'pcf')."""
        payload = data.get("pcf", data)
        components_raw = payload.get("components", [])
        components = tuple(
            c if isinstance(c, BasketComponent) else BasketComponent.from_dict(c)
            for c in components_raw
        )
        return cls(
            pcf_id=payload["pcf_id"],
            etf_ticker=payload["etf_ticker"],
            etf_isin=payload["etf_isin"],
            creation_unit_size=_to_int(payload["creation_unit_size"], "creation_unit_size"),
            nav_per_quota=_to_decimal(payload["nav_per_quota"], "nav_per_quota"),
            cash_component_per_unit=_to_decimal(
                payload["cash_component_per_unit"], "cash_component_per_unit"
            ),
            components=components,
            reference_date=str(payload.get("reference_date", "2026-11-03")),
            settlement_date=str(payload.get("settlement_date", "2026-11-05")),
            benchmark_index=str(payload.get("benchmark_index", "IBOV")),
            fund_custody_account=str(payload.get("fund_custody_account", "99001-0")),
            etf_name=str(payload.get("etf_name", "")),
            administrator_cnpj=str(payload.get("administrator_cnpj", "")),
        )

    @classmethod
    def from_json(cls, json_path_or_str: Union[str, Path]) -> PortfolioCompositionFile:
        """Carrega um PortfolioCompositionFile de um caminho de arquivo JSON ou string JSON."""
        if isinstance(json_path_or_str, Path) or (
            isinstance(json_path_or_str, str) and not json_path_or_str.lstrip().startswith("{")
        ):
            path = Path(json_path_or_str)
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(json_path_or_str)
        return cls.from_dict(data)

    from_json_file = from_json

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o PCF completo para dicionário compatível com JSON."""
        return {
            "pcf_id": self.pcf_id,
            "etf_ticker": self.etf_ticker,
            "etf_isin": self.etf_isin,
            "etf_name": self.etf_name,
            "benchmark_index": self.benchmark_index,
            "administrator_cnpj": self.administrator_cnpj,
            "fund_custody_account": self.fund_custody_account,
            "reference_date": self.reference_date,
            "settlement_date": self.settlement_date,
            "creation_unit_size": self.creation_unit_size,
            "nav_per_quota": f"{self.nav_per_quota:.2f}",
            "unit_nav_value": f"{self.unit_nav_value:.2f}",
            "basket_equities_value_per_unit": f"{self.basket_equities_value_per_unit:.2f}",
            "cash_component_per_unit": f"{self.cash_component_per_unit:.2f}",
            "components": [c.to_dict() for c in self.components],
        }


@dataclass(frozen=True)
class EtfPrimaryOrder:
    """Ordem de Criação (CREATION) ou Resgate (REDEMPTION) submetida por Agente Autorizado (AP)."""

    order_id: str
    order_type: EtfOrderType
    ap_participant_code: str
    ap_custody_account: str
    quotas_quantity: int
    etf_ticker: str = "BOVA11"
    ap_name: str = ""
    timestamp: str = "2026-11-03T16:00:00Z"
    available_equities: Dict[str, int] = field(default_factory=dict)
    available_etf_quotas: int = 0

    def __post_init__(self) -> None:
        order_id_clean = str(self.order_id).strip()
        if not order_id_clean:
            raise EtfValidationError("O identificador da ordem (order_id) não pode ser vazio.")
        otype = EtfOrderType.from_value(self.order_type)
        ap_code = str(self.ap_participant_code).strip()
        ap_account = str(self.ap_custody_account).strip()
        if not ap_code:
            raise EtfValidationError(f"Código do Agente Autorizado vazio na ordem {order_id_clean}.")
        if not ap_account:
            raise EtfValidationError(f"Conta de custódia do AP vazia na ordem {order_id_clean}.")

        quotas = _to_int(self.quotas_quantity, f"quotas_quantity[{order_id_clean}]")
        if quotas <= 0:
            raise InvalidCreationUnitMultipleError(
                f"A quantidade de cotas da ordem {order_id_clean} deve ser estritamente positiva, recebido: {quotas}"
            )

        norm_equities: Dict[str, int] = {}
        for k, v in (self.available_equities or {}).items():
            qty = _to_int(v, f"available_equities[{k}]")
            if qty < 0:
                raise EtfValidationError(f"Saldo disponível de {k} não pode ser negativo: {qty}")
            norm_equities[str(k).strip().upper()] = qty

        avail_quotas = _to_int(self.available_etf_quotas, f"available_etf_quotas[{order_id_clean}]")
        if avail_quotas < 0:
            raise EtfValidationError(
                f"Saldo disponível de cotas do ETF não pode ser negativo: {avail_quotas}"
            )

        object.__setattr__(self, "order_id", order_id_clean)
        object.__setattr__(self, "order_type", otype)
        object.__setattr__(self, "ap_participant_code", ap_code)
        object.__setattr__(self, "ap_custody_account", ap_account)
        object.__setattr__(self, "quotas_quantity", quotas)
        object.__setattr__(self, "etf_ticker", str(self.etf_ticker).strip().upper())
        object.__setattr__(self, "ap_name", str(self.ap_name or "").strip())
        object.__setattr__(self, "timestamp", str(self.timestamp or "").strip())
        object.__setattr__(self, "available_equities", norm_equities)
        object.__setattr__(self, "available_etf_quotas", avail_quotas)

    def creation_units(self, creation_unit_size: int) -> int:
        """Calcula o número exato de Lotes Mínimos (Creation Units) ou lança erro se não for múltiplo."""
        unit_size = _to_int(creation_unit_size, "creation_unit_size")
        if unit_size <= 0:
            raise EtfValidationError(f"creation_unit_size inválido: {unit_size}")
        if self.quotas_quantity <= 0 or self.quotas_quantity % unit_size != 0:
            raise InvalidCreationUnitMultipleError(
                f"Ordem {self.order_id}: quantidade solicitada ({self.quotas_quantity} cotas) "
                f"não é múltiplo inteiro exato do Lote Mínimo ({unit_size} cotas)."
            )
        return self.quotas_quantity // unit_size

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EtfPrimaryOrder:
        """Constrói uma EtfPrimaryOrder a partir de dicionário JSON."""
        return cls(
            order_id=data["order_id"],
            order_type=EtfOrderType.from_value(data["order_type"]),
            ap_participant_code=str(data["ap_participant_code"]),
            ap_custody_account=str(data["ap_custody_account"]),
            quotas_quantity=_to_int(data["quotas_quantity"], "quotas_quantity"),
            etf_ticker=str(data.get("etf_ticker", "BOVA11")),
            ap_name=str(data.get("ap_name", "")),
            timestamp=str(data.get("timestamp", "2026-11-03T16:00:00Z")),
            available_equities=dict(data.get("available_equities") or {}),
            available_etf_quotas=_to_int(data.get("available_etf_quotas", 0), "available_etf_quotas"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a ordem primária para dicionário compatível com JSON."""
        return {
            "order_id": self.order_id,
            "order_type": self.order_type.value,
            "etf_ticker": self.etf_ticker,
            "ap_participant_code": self.ap_participant_code,
            "ap_name": self.ap_name,
            "ap_custody_account": self.ap_custody_account,
            "quotas_quantity": self.quotas_quantity,
            "timestamp": self.timestamp,
            "available_equities": dict(self.available_equities),
            "available_etf_quotas": self.available_etf_quotas,
        }


@dataclass(frozen=True)
class CustodyTransferLeg:
    """Perna individual de transferência de custódia (ação da cesta ou cota de ETF) na Depositária B3."""

    asset_ticker: str
    isin: str
    quantity: int
    unit_price: Decimal
    financial_value: Decimal
    from_custody_account: str
    to_custody_account: str
    transfer_type: str  # "BASKET_IN_KIND_DELIVERY", "BASKET_IN_KIND_RECEIPT", "ETF_PRIMARY_MINT", "ETF_PRIMARY_BURN"
    imbarq_layout: str = "IMBARQ012"

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a perna de transferência de custódia."""
        return {
            "asset_ticker": self.asset_ticker,
            "isin": self.isin,
            "quantity": self.quantity,
            "unit_price": f"{self.unit_price:.2f}",
            "financial_value": f"{self.financial_value:.2f}",
            "from_custody_account": self.from_custody_account,
            "to_custody_account": self.to_custody_account,
            "transfer_type": self.transfer_type,
            "imbarq_layout": self.imbarq_layout,
        }


@dataclass(frozen=True)
class EtfSettlementInstruction:
    """Instrução de liquidação física (in-kind) e financeira (Cash Component) gerada pelo motor B3."""

    instruction_id: str
    order_id: str
    pcf_id: str
    etf_ticker: str
    etf_isin: str
    order_type: EtfOrderType
    ap_participant_code: str
    ap_custody_account: str
    fund_custody_account: str
    settlement_date: str
    creation_units: int
    quotas_quantity: int
    nav_per_quota: Decimal
    basket_equities_value: Decimal
    cash_component_amount: Decimal
    total_nav_value: Decimal
    cash_settlement_direction: str  # "AP_PAYS_FUND", "FUND_PAYS_AP", "NO_CASH_FLOW"
    etf_quota_action: str           # "MINT_TO_AP" ou "BURN_FROM_AP"
    etf_quota_leg: CustodyTransferLeg
    equity_transfer_legs: Tuple[CustodyTransferLeg, ...]
    required_equities: Dict[str, int]
    status: str = "APPROVED"
    parity_verified: bool = True

    def __post_init__(self) -> None:
        expected_total = (Decimal(self.quotas_quantity) * self.nav_per_quota).quantize(
            TWO_DECIMALS, rounding=ROUND_HALF_UP
        )
        actual_sum = (self.basket_equities_value + self.cash_component_amount).quantize(
            TWO_DECIMALS, rounding=ROUND_HALF_UP
        )
        if actual_sum != expected_total or self.total_nav_value != expected_total:
            raise PcfParityError(
                f"Invariante de liquidação violado na instrução {self.instruction_id}: "
                f"Cesta (R$ {self.basket_equities_value:.2f}) + Cash (R$ {self.cash_component_amount:.2f}) "
                f"= R$ {actual_sum:.2f} != Quotas * NAV (R$ {expected_total:.2f})."
            )

    @property
    def total_equity_shares_transferred(self) -> int:
        """Soma total da quantidade de ações transferidas em todas as pernas da cesta."""
        return sum(leg.quantity for leg in self.equity_transfer_legs)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a instrução de liquidação completa para dicionário compatível com JSON."""
        return {
            "instruction_id": self.instruction_id,
            "order_id": self.order_id,
            "pcf_id": self.pcf_id,
            "etf_ticker": self.etf_ticker,
            "etf_isin": self.etf_isin,
            "order_type": self.order_type.value,
            "ap_participant_code": self.ap_participant_code,
            "ap_custody_account": self.ap_custody_account,
            "fund_custody_account": self.fund_custody_account,
            "settlement_date": self.settlement_date,
            "creation_units": self.creation_units,
            "quotas_quantity": self.quotas_quantity,
            "nav_per_quota": f"{self.nav_per_quota:.2f}",
            "basket_equities_value": f"{self.basket_equities_value:.2f}",
            "cash_component_amount": f"{self.cash_component_amount:.2f}",
            "total_nav_value": f"{self.total_nav_value:.2f}",
            "cash_settlement_direction": self.cash_settlement_direction,
            "etf_quota_action": self.etf_quota_action,
            "etf_quota_leg": self.etf_quota_leg.to_dict(),
            "equity_transfer_legs": [leg.to_dict() for leg in self.equity_transfer_legs],
            "required_equities": dict(self.required_equities),
            "status": self.status,
            "parity_verified": self.parity_verified,
        }


# ==============================================================================
# 4. Motor do Mercado Primário de ETFs (EtfPrimaryMarketEngine)
# ==============================================================================


class EtfPrimaryMarketEngine:
    """Motor determinístico para validação de PCF, Criação/Resgate de Lotes Mínimos e liquidação in-kind."""

    def __init__(
        self,
        pcf: PortfolioCompositionFile,
        orders: Optional[Sequence[EtfPrimaryOrder]] = None,
        fund_available_equities: Optional[Dict[str, int]] = None,
    ) -> None:
        if not isinstance(pcf, PortfolioCompositionFile):
            raise EtfValidationError("pcf deve ser uma instância válida de PortfolioCompositionFile.")
        self.pcf: PortfolioCompositionFile = pcf
        self.pcf.validate_nav_parity()
        self.orders: List[EtfPrimaryOrder] = list(orders) if orders else []
        self.fund_available_equities: Optional[Dict[str, int]] = (
            {str(k).strip().upper(): _to_int(v, f"fund_equity[{k}]") for k, v in fund_available_equities.items()}
            if fund_available_equities is not None
            else None
        )

    def validate_creation_unit_multiple(self, quotas_quantity: int, order_id: str = "N/A") -> int:
        """Valida se a quantidade de cotas é múltiplo inteiro exato de pcf.creation_unit_size e retorna o nº de lotes."""
        quotas = _to_int(quotas_quantity, "quotas_quantity")
        unit_size = self.pcf.creation_unit_size
        if quotas <= 0 or quotas % unit_size != 0:
            raise InvalidCreationUnitMultipleError(
                f"Ordem {order_id}: quantidade de cotas ({quotas}) não é múltiplo exato "
                f"do Lote Mínimo ({unit_size} cotas para {self.pcf.etf_ticker})."
            )
        return quotas // unit_size

    def calculate_order_financials(self, quotas_quantity: int, order_id: str = "N/A") -> Dict[str, Any]:
        """Calcula exatamente o número de Lotes Mínimos, valor da cesta, Cash Component e Quotas * NAV."""
        creation_units = self.validate_creation_unit_multiple(quotas_quantity, order_id=order_id)
        units_dec = Decimal(creation_units)

        basket_value = sum(
            (comp.value_for_units(creation_units) for comp in self.pcf.components),
            Decimal("0.00"),
        ).quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)

        cash_component = (units_dec * self.pcf.cash_component_per_unit).quantize(
            TWO_DECIMALS, rounding=ROUND_HALF_UP
        )

        total_nav_value = (Decimal(quotas_quantity) * self.pcf.nav_per_quota).quantize(
            TWO_DECIMALS, rounding=ROUND_HALF_UP
        )

        if basket_value + cash_component != total_nav_value:
            raise PcfParityError(
                f"Ordem {order_id}: soma financeira da Cesta (R$ {basket_value:.2f}) + "
                f"Cash Component (R$ {cash_component:.2f}) = R$ {basket_value + cash_component:.2f} "
                f"difere de Quotas * NAV (R$ {total_nav_value:.2f})."
            )

        return {
            "creation_units": creation_units,
            "quotas_quantity": quotas_quantity,
            "basket_equities_value": basket_value,
            "cash_component_amount": cash_component,
            "total_nav_value": total_nav_value,
        }

    def validate_custody_balance(
        self,
        order: EtfPrimaryOrder,
        creation_units: Optional[int] = None,
        available_equities: Optional[Dict[str, int]] = None,
        available_etf_quotas: Optional[int] = None,
    ) -> Dict[str, int]:
        """Confere o saldo livre de custódia para entrega in-kind de todas as ações (Creation) ou cotas (Redemption)."""
        units = (
            creation_units
            if creation_units is not None
            else self.validate_creation_unit_multiple(order.quotas_quantity, order_id=order.order_id)
        )
        required_equities = self.pcf.required_equities_for_units(units)

        if order.order_type.is_creation:
            ap_equities = (
                {str(k).strip().upper(): _to_int(v, f"available_equities[{k}]") for k, v in available_equities.items()}
                if available_equities is not None
                else order.available_equities
            )
            deficits: Dict[str, Dict[str, int]] = {}
            for ticker, required_qty in required_equities.items():
                free_qty = ap_equities.get(ticker, 0)
                if free_qty < required_qty:
                    deficits[ticker] = {
                        "required": required_qty,
                        "available": free_qty,
                        "shortfall": required_qty - free_qty,
                    }
            if deficits:
                deficit_summary = ", ".join(
                    f"{t} (exigido={d['required']}, disponível={d['available']}, falta={d['shortfall']})"
                    for t, d in deficits.items()
                )
                raise InsufficientBasketCustodyError(
                    f"Ordem de Criação {order.order_id} rejeitada por saldo livre insuficiente "
                    f"na conta de custódia {order.ap_custody_account}: {deficit_summary}.",
                    deficits=deficits,
                )
        else:
            ap_quotas = (
                _to_int(available_etf_quotas, "available_etf_quotas")
                if available_etf_quotas is not None
                else order.available_etf_quotas
            )
            if ap_quotas < order.quotas_quantity:
                shortfall = order.quotas_quantity - ap_quotas
                deficits = {
                    self.pcf.etf_ticker: {
                        "required": order.quotas_quantity,
                        "available": ap_quotas,
                        "shortfall": shortfall,
                    }
                }
                raise InsufficientEtfQuotasError(
                    f"Ordem de Resgate {order.order_id} rejeitada por saldo livre insuficiente de cotas "
                    f"{self.pcf.etf_ticker} na conta {order.ap_custody_account}: "
                    f"exigido={order.quotas_quantity}, disponível={ap_quotas}, falta={shortfall}.",
                    deficits=deficits,
                )

            if self.fund_available_equities is not None:
                fund_deficits: Dict[str, Dict[str, int]] = {}
                for ticker, required_qty in required_equities.items():
                    free_qty = self.fund_available_equities.get(ticker, 0)
                    if free_qty < required_qty:
                        fund_deficits[ticker] = {
                            "required": required_qty,
                            "available": free_qty,
                            "shortfall": required_qty - free_qty,
                        }
                if fund_deficits:
                    raise InsufficientBasketCustodyError(
                        f"Ordem de Resgate {order.order_id}: conta do fundo {self.pcf.fund_custody_account} "
                        f"sem saldo livre suficiente das ações da cesta.",
                        deficits=fund_deficits,
                    )

        return required_equities

    def process_order(
        self,
        order: EtfPrimaryOrder,
        available_equities: Optional[Dict[str, int]] = None,
        available_etf_quotas: Optional[int] = None,
    ) -> EtfSettlementInstruction:
        """Valida uma ordem primária de ETF e gera a instrução atômica de transferência de custódia e caixa."""
        if order.etf_ticker != self.pcf.etf_ticker:
            raise EtfValidationError(
                f"Ordem {order.order_id} referencia o ETF {order.etf_ticker!r}, "
                f"mas o motor está carregado com o PCF do ETF {self.pcf.etf_ticker!r}."
            )

        financials = self.calculate_order_financials(order.quotas_quantity, order_id=order.order_id)
        creation_units: int = financials["creation_units"]
        basket_value: Decimal = financials["basket_equities_value"]
        cash_amount: Decimal = financials["cash_component_amount"]
        total_nav_value: Decimal = financials["total_nav_value"]

        required_equities = self.validate_custody_balance(
            order,
            creation_units=creation_units,
            available_equities=available_equities,
            available_etf_quotas=available_etf_quotas,
        )

        equity_legs: List[CustodyTransferLeg] = []
        if order.order_type.is_creation:
            from_eq_account = order.ap_custody_account
            to_eq_account = self.pcf.fund_custody_account
            eq_transfer_type = "BASKET_IN_KIND_DELIVERY"
            etf_quota_action = "MINT_TO_AP"
            etf_quota_leg = CustodyTransferLeg(
                asset_ticker=self.pcf.etf_ticker,
                isin=self.pcf.etf_isin,
                quantity=order.quotas_quantity,
                unit_price=self.pcf.nav_per_quota,
                financial_value=total_nav_value,
                from_custody_account=self.pcf.fund_custody_account,
                to_custody_account=order.ap_custody_account,
                transfer_type="ETF_PRIMARY_MINT",
                imbarq_layout="IMBARQ012",
            )
            if cash_amount > Decimal("0.00"):
                cash_direction = "AP_PAYS_FUND"
            elif cash_amount < Decimal("0.00"):
                cash_direction = "FUND_PAYS_AP"
            else:
                cash_direction = "NO_CASH_FLOW"
        else:
            from_eq_account = self.pcf.fund_custody_account
            to_eq_account = order.ap_custody_account
            eq_transfer_type = "BASKET_IN_KIND_RECEIPT"
            etf_quota_action = "BURN_FROM_AP"
            etf_quota_leg = CustodyTransferLeg(
                asset_ticker=self.pcf.etf_ticker,
                isin=self.pcf.etf_isin,
                quantity=order.quotas_quantity,
                unit_price=self.pcf.nav_per_quota,
                financial_value=total_nav_value,
                from_custody_account=order.ap_custody_account,
                to_custody_account=self.pcf.fund_custody_account,
                transfer_type="ETF_PRIMARY_BURN",
                imbarq_layout="IMBARQ012",
            )
            if cash_amount > Decimal("0.00"):
                cash_direction = "FUND_PAYS_AP"
            elif cash_amount < Decimal("0.00"):
                cash_direction = "AP_PAYS_FUND"
            else:
                cash_direction = "NO_CASH_FLOW"

        for comp in self.pcf.components:
            qty = required_equities[comp.ticker]
            val = comp.value_for_units(creation_units)
            equity_legs.append(
                CustodyTransferLeg(
                    asset_ticker=comp.ticker,
                    isin=comp.isin,
                    quantity=qty,
                    unit_price=comp.closing_price,
                    financial_value=val,
                    from_custody_account=from_eq_account,
                    to_custody_account=to_eq_account,
                    transfer_type=eq_transfer_type,
                    imbarq_layout="IMBARQ012",
                )
            )

        instruction_id = f"INST-{self.pcf.etf_ticker}-{order.order_id}"
        return EtfSettlementInstruction(
            instruction_id=instruction_id,
            order_id=order.order_id,
            pcf_id=self.pcf.pcf_id,
            etf_ticker=self.pcf.etf_ticker,
            etf_isin=self.pcf.etf_isin,
            order_type=order.order_type,
            ap_participant_code=order.ap_participant_code,
            ap_custody_account=order.ap_custody_account,
            fund_custody_account=self.pcf.fund_custody_account,
            settlement_date=self.pcf.settlement_date,
            creation_units=creation_units,
            quotas_quantity=order.quotas_quantity,
            nav_per_quota=self.pcf.nav_per_quota,
            basket_equities_value=basket_value,
            cash_component_amount=cash_amount,
            total_nav_value=total_nav_value,
            cash_settlement_direction=cash_direction,
            etf_quota_action=etf_quota_action,
            etf_quota_leg=etf_quota_leg,
            equity_transfer_legs=tuple(equity_legs),
            required_equities=required_equities,
            status="APPROVED",
            parity_verified=True,
        )

    def process_batch(
        self, orders: Optional[Sequence[EtfPrimaryOrder]] = None
    ) -> List[EtfSettlementInstruction]:
        """Processa um lote de ordens primárias e retorna a lista de instruções de liquidação."""
        target_orders = list(orders) if orders is not None else self.orders
        return [self.process_order(order) for order in target_orders]

    def reconcile_primary_session(
        self, instructions: Optional[Sequence[EtfSettlementInstruction]] = None
    ) -> Dict[str, Any]:
        """Consolida o livro primário do ETF no dia: emissão/resgate líquido de cotas, ações e caixa."""
        inst_list = (
            list(instructions)
            if instructions is not None
            else self.process_batch()
        )
        created_units = 0
        redeemed_units = 0
        created_quotas = 0
        redeemed_quotas = 0
        net_cash_to_fund = Decimal("0.00")
        net_equities_to_fund: Dict[str, int] = {c.ticker: 0 for c in self.pcf.components}

        for inst in inst_list:
            if inst.order_type.is_creation:
                created_units += inst.creation_units
                created_quotas += inst.quotas_quantity
                net_cash_to_fund += inst.cash_component_amount
                for ticker, qty in inst.required_equities.items():
                    net_equities_to_fund[ticker] = net_equities_to_fund.get(ticker, 0) + qty
            else:
                redeemed_units += inst.creation_units
                redeemed_quotas += inst.quotas_quantity
                net_cash_to_fund -= inst.cash_component_amount
                for ticker, qty in inst.required_equities.items():
                    net_equities_to_fund[ticker] = net_equities_to_fund.get(ticker, 0) - qty

        net_quotas = created_quotas - redeemed_quotas
        net_nav_change = (Decimal(net_quotas) * self.pcf.nav_per_quota).quantize(
            TWO_DECIMALS, rounding=ROUND_HALF_UP
        )

        return {
            "pcf_id": self.pcf.pcf_id,
            "etf_ticker": self.pcf.etf_ticker,
            "settlement_date": self.pcf.settlement_date,
            "total_orders_processed": len(inst_list),
            "created_units": created_units,
            "redeemed_units": redeemed_units,
            "net_units": created_units - redeemed_units,
            "created_quotas": created_quotas,
            "redeemed_quotas": redeemed_quotas,
            "net_quotas_change": net_quotas,
            "net_cash_to_fund": net_cash_to_fund.quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP),
            "net_nav_change": net_nav_change,
            "net_equities_to_fund": net_equities_to_fund,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EtfPrimaryMarketEngine:
        """Instancia o motor a partir de um dicionário JSON contendo PCF e lista de ordens."""
        pcf = PortfolioCompositionFile.from_dict(data)
        orders_raw = data.get("orders", [])
        orders = [
            o if isinstance(o, EtfPrimaryOrder) else EtfPrimaryOrder.from_dict(o)
            for o in orders_raw
        ]
        fund_equities = data.get("fund_available_equities")
        return cls(pcf=pcf, orders=orders, fund_available_equities=fund_equities)

    @classmethod
    def from_json(cls, json_path_or_str: Union[str, Path]) -> EtfPrimaryMarketEngine:
        """Carrega o motor diretamente do arquivo JSON (ex: fixtures/etf_primary_basket.json)."""
        if isinstance(json_path_or_str, Path) or (
            isinstance(json_path_or_str, str) and not json_path_or_str.lstrip().startswith("{")
        ):
            path = Path(json_path_or_str)
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(json_path_or_str)
        return cls.from_dict(data)

    from_json_file = from_json
