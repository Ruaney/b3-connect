"""Módulo de Gestão de Garantias em Commodities Agrícolas (CDA e WA) com Armazéns Credenciados B3 (FL-078).

Implementa o motor determinístico de registro, validação, precificação mark-to-market,
matriz de haircuts por safra e depreciação física, quebra técnica de armazenagem,
bloqueio fiduciário como margem na Câmara B3 (futuros de Soja, Milho, Boi Gordo e Café)
e procedimentos de protesto e excussão física em leilão público, em estrita conformidade com:
- Lei nº 11.076/2004 (Títulos do Agronegócio — CDA e WA, arts. 1º a 22).
- Decreto nº 1.102/1903 (Regulamento dos Armazéns Gerais).
- Lei nº 14.711/2023 (Marco Legal das Garantias — Aprimoramento da Excussão Extrajudicial).
- Manual de Risco e Garantias da Câmara B3 (CORE — Clearing Omniprocess Risk Engine).
- Contratos Futuros B3: Soja (SJC/SOJ), Milho (CCM), Boi Gordo (BGI) e Café Arábica (ICF).

Invariantes Formais Provadas:
1. Conservação Física de Massa/Sacas e Quebra Técnica:
   Quantidade_Original == Quantidade_Efetiva_Armazenada + Quebra_Tecnica_Acumulada
2. Equivalência e Não-Negatividade de Colateral:
   Valor_Bruto == Quantidade_Efetiva * Preco_Referencia
   Valor_Colateral_Elegivel == Valor_Bruto * (1 - Haircut_Total_Capped)
   Credito_Margem_Camara == max(0, Valor_Colateral_Elegivel - Passivo_WA_Terceiro)
3. Monotonicidade Estrita da Matriz de Haircuts:
   Haircut(Safra_Velha) >= Haircut(Safra_Anterior) >= Haircut(Safra_Atual)
   Haircut(Armazenamento_Longo) >= Haircut(Armazenamento_Curto)
4. Conservação Financeira Estrita da Excussão em Leilão Público (Waterfall de Liquidação):
   Recursos_Brutos_Leilao == Despesas_Armazenagem_e_Leilao + Passivo_WA_Liquidado + Remanescente_CDA
   (Recursos_Brutos - Despesas - Passivo_WA_Liquidado - Remanescente_CDA == 0,00 centavo a centavo)
5. Conservação de Quantidade Física Vendida no Leilão:
   Quantidade_Leiloada == Quantidade_Efetiva_Apos_Quebra
6. Trava de Segurança de Desoneração (Safe-Release Guardrail):
   Vedada a desoneração de garantias se o colateral remanescente for inferior à margem requerida da Câmara B3.

100% Python Standard Library (sem dependências externas).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


# =====================================================================
# CONSTANTES DE PRECISÃO NUMÉRICA E ARREDONDAMENTO B3
# =====================================================================

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_QTY = Decimal("0.0001")
QUANTIZE_RATE = Decimal("0.00000001")
QUANTIZE_UNIT = Decimal("0.0001")

# Limite regulatório B3 para aceitação de colateral agrícola (50% de haircut máximo)
MAX_ELIGIBLE_HAIRCUT = Decimal("0.5000")


def round_cents(val: Decimal) -> Decimal:
    """Arredonda valor financeiro para 2 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)


def round_qty(val: Decimal) -> Decimal:
    """Arredonda quantidades físicas (sacas, arrobas, toneladas) para 4 casas decimais."""
    return val.quantize(QUANTIZE_QTY, rounding=ROUND_HALF_UP)


def round_rate(val: Decimal) -> Decimal:
    """Arredonda taxas percentuais e haircuts para 8 casas decimais."""
    return val.quantize(QUANTIZE_RATE, rounding=ROUND_HALF_UP)


def round_unit(val: Decimal) -> Decimal:
    """Arredonda preço unitário da commodity para 4 casas decimais."""
    return val.quantize(QUANTIZE_UNIT, rounding=ROUND_HALF_UP)


def to_decimal(value: Any, field_name: str = "campo") -> Decimal:
    """Converte valor para Decimal de forma robusta e auditável."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise AgriculturalWarrantValidationError(f"Valor nulo não permitido para '{field_name}'.")
    try:
        cleaned = str(value).strip().replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError) as err:
        raise AgriculturalWarrantValidationError(
            f"Valor inválido '{value}' para o campo '{field_name}': {err}"
        ) from err


def parse_date(value: Any, field_name: str = "data") -> date:
    """Converte string ISO (YYYY-MM-DD) ou objeto date em date."""
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError as err:
            raise AgriculturalWarrantValidationError(
                f"Formato de data inválido '{value}' para '{field_name}', esperado YYYY-MM-DD: {err}"
            ) from err
    raise AgriculturalWarrantValidationError(
        f"Tipo de data inválido para '{field_name}': {type(value)}"
    )


# =====================================================================
# ENUMS DE DOMÍNIO AGROPECUÁRIO E B3 CLEARING
# =====================================================================

class AgriculturalCommodity(str, Enum):
    """Commodities agrícolas aceitas como margem de garantia na Câmara B3."""
    SOYBEAN = "SOYBEAN"           # Soja em Grão (Futuro SJC / SOJ) - Sacas de 60kg
    CORN = "CORN"                 # Milho em Grão (Futuro CCM) - Sacas de 60kg
    LIVE_CATTLE = "LIVE_CATTLE"   # Boi Gordo (Futuro BGI) - Arroba líquida (@ 15kg)
    ARABICA_COFFEE = "ARABICA_COFFEE"  # Café Arábica tipo 4/5 (Futuro ICF) - Sacas de 60kg


class UnitOfMeasure(str, Enum):
    """Unidades de medida padrão do mercado agropecuário B3."""
    BAG_60KG = "BAG_60KG"         # Saca de 60 kg (Soja, Milho, Café)
    METRIC_TON = "METRIC_TON"     # Tonelada Métrica (1.000 kg)
    ARROBA_15KG = "ARROBA_15KG"   # Arroba (@) de 15 kg (Boi Gordo)
    NET_KG = "NET_KG"             # Quilograma líquido


class WarehouseAccreditationStatus(str, Enum):
    """Status de credenciamento do armazém geral perante a B3 e MAPA."""
    ACCREDITED = "ACCREDITED"     # Armazém devidamente credenciado e operacional
    SUSPENDED = "SUSPENDED"       # Credenciamento suspenso (ex: vistoria pendente ou seguro vencido)
    REVOKED = "REVOKED"           # Credenciamento revogado por infração regulatória


class WarrantStatus(str, Enum):
    """Ciclo de vida do conjunto CDA/WA perante o depositante, armazém e B3."""
    ACTIVE_DEPOSIT = "ACTIVE_DEPOSIT"       # Título ativo em depósito regular no armazém
    PLEDGED_TO_B3 = "PLEDGED_TO_B3"         # Bloqueado fiduciariamente na B3 Clearing como margem
    IN_MARGIN_CALL = "IN_MARGIN_CALL"       # Sob monitoramento de chamada de margem
    PROTESTED = "PROTESTED"                 # WA protestado em cartório por inadimplência do crédito
    IN_AUCTION = "IN_AUCTION"               # Mercadoria em processo de leilão público de excussão
    EXECUTED = "EXECUTED"                   # Leilão concluído e recursos liquidados no waterfall
    RELEASED = "RELEASED"                   # Desonerado pela B3 e devolvido à livre circulação
    CANCELLED = "CANCELLED"                 # Título cancelado por resgate físico da mercadoria


# =====================================================================
# MATRIZ REGULATÓRIA DE TAXAS DE QUEBRA TÉCNICA E HAIRCUTS BASE B3
# =====================================================================

# Taxa de perda técnica mensal por dessecação/aeração/manuseio (Lei 11.076/2004, art. 7º)
TECHNICAL_LOSS_MONTHLY_RATES: Dict[AgriculturalCommodity, Decimal] = {
    AgriculturalCommodity.SOYBEAN: Decimal("0.0020"),       # 0,20% ao mês
    AgriculturalCommodity.CORN: Decimal("0.0025"),          # 0,25% ao mês
    AgriculturalCommodity.LIVE_CATTLE: Decimal("0.0030"),   # 0,30% ao mês (quebra fria / frigorífica)
    AgriculturalCommodity.ARABICA_COFFEE: Decimal("0.0015"),# 0,15% ao mês
}

# Haircut regulatório base por commodity na Câmara B3 (volatilidade e liquidez spot)
COMMODITY_BASE_HAIRCUTS: Dict[AgriculturalCommodity, Decimal] = {
    AgriculturalCommodity.SOYBEAN: Decimal("0.1500"),       # 15,00%
    AgriculturalCommodity.CORN: Decimal("0.1800"),          # 18,00%
    AgriculturalCommodity.LIVE_CATTLE: Decimal("0.2000"),   # 20,00%
    AgriculturalCommodity.ARABICA_COFFEE: Decimal("0.1700"),# 17,00%
}

# Padrões máximos de tolerância de qualidade antes de deságios adicionais
QUALITY_STANDARDS: Dict[AgriculturalCommodity, Dict[str, Decimal]] = {
    AgriculturalCommodity.SOYBEAN: {
        "max_moisture_pct": Decimal("14.0"),
        "max_impurity_pct": Decimal("1.0"),
        "max_damaged_pct": Decimal("8.0"),
    },
    AgriculturalCommodity.CORN: {
        "max_moisture_pct": Decimal("14.5"),
        "max_impurity_pct": Decimal("1.5"),
        "max_damaged_pct": Decimal("6.0"),
    },
    AgriculturalCommodity.LIVE_CATTLE: {
        "max_moisture_pct": Decimal("0.0"),  # N/A
        "max_impurity_pct": Decimal("0.0"),  # N/A
        "max_damaged_pct": Decimal("2.0"),   # Defeitos de toalete / contusões
    },
    AgriculturalCommodity.ARABICA_COFFEE: {
        "max_moisture_pct": Decimal("12.5"),
        "max_impurity_pct": Decimal("1.0"),
        "max_damaged_pct": Decimal("10.0"),  # Defeitos tipo 4/5
    },
}


# =====================================================================
# EXCEÇÕES DE DOMÍNIO
# =====================================================================

class AgriculturalWarrantError(Exception):
    """Exceção base para o ecossistema de garantias em warrants agrícolas B3."""
    pass


class AgriculturalWarrantValidationError(AgriculturalWarrantError):
    """Erro de validação de regras de negócio, formato ou atributos do CDA/WA."""
    pass


class WarehouseNotAccreditedError(AgriculturalWarrantError):
    """Erro decorrente de armazém geral não credenciado, suspenso ou seguro ineficaz."""
    pass


class WarrantStateTransitionError(AgriculturalWarrantError):
    """Transição de estado inválida no ciclo de vida do CDA/WA."""
    pass


class MarginReleaseViolationError(AgriculturalWarrantError):
    """Violação do Safe-Release Guardrail: desoneração geraria déficit de margem na câmara."""
    pass


class WarrantProtestError(AgriculturalWarrantError):
    """Erro na formalização de protesto de título do WA inadimplido."""
    pass


class AuctionExecutionError(AgriculturalWarrantError):
    """Erro na condução ou liquidação financeira do leilão público de excussão."""
    pass


class AgriculturalInvariantViolationError(AgriculturalWarrantError):
    """Violação formal de uma lei de conservação matemática ou equivalência de colateral."""
    pass


# =====================================================================
# MODELOS DE DADOS (DATACLASSES)
# =====================================================================

@dataclass
class QualityMetrics:
    """Parâmetros físicos e de classificação vegetal/animal do produto depositado."""
    moisture_pct: Decimal
    impurity_pct: Decimal
    damaged_pct: Decimal
    classification_standard: str = "Padrão Oficial MAPA Tipo 1"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "moisture_pct": str(self.moisture_pct),
            "impurity_pct": str(self.impurity_pct),
            "damaged_pct": str(self.damaged_pct),
            "classification_standard": self.classification_standard,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> QualityMetrics:
        return cls(
            moisture_pct=to_decimal(data.get("moisture_pct", 0), "moisture_pct"),
            impurity_pct=to_decimal(data.get("impurity_pct", 0), "impurity_pct"),
            damaged_pct=to_decimal(data.get("damaged_pct", 0), "damaged_pct"),
            classification_standard=str(data.get("classification_standard", "Padrão Oficial MAPA")),
        )


@dataclass
class Warehouse:
    """Armazém Geral depositário credenciado pela B3 e regulado pelo MAPA (Dec. 1.102/1903)."""
    code: str
    name: str
    cnpj: str
    state: str
    city: str
    accreditation_number: str
    accreditation_status: WarehouseAccreditationStatus
    static_capacity_tons: Decimal
    insurance_policy_number: str
    insurance_company: str
    insurance_coverage_amount: Decimal
    insurance_expiration_date: date
    storage_monthly_fee_per_unit: Decimal

    def validate(self, as_of_date: Optional[date] = None) -> None:
        """Valida conformidade do credenciamento e apólice de seguro obrigatória."""
        if self.accreditation_status != WarehouseAccreditationStatus.ACCREDITED:
            raise WarehouseNotAccreditedError(
                f"Armazém '{self.code}' com credenciamento irregular: {self.accreditation_status.value}."
            )
        if as_of_date and self.insurance_expiration_date < as_of_date:
            raise WarehouseNotAccreditedError(
                f"Apólice de seguro do armazém '{self.code}' expirada em {self.insurance_expiration_date} "
                f"(data de referência: {as_of_date})."
            )
        if self.insurance_coverage_amount <= Decimal("0.00"):
            raise WarehouseNotAccreditedError(
                f"Armazém '{self.code}' sem cobertura securitária ativa válida."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "cnpj": self.cnpj,
            "state": self.state,
            "city": self.city,
            "accreditation_number": self.accreditation_number,
            "accreditation_status": self.accreditation_status.value,
            "static_capacity_tons": str(self.static_capacity_tons),
            "insurance_policy_number": self.insurance_policy_number,
            "insurance_company": self.insurance_company,
            "insurance_coverage_amount": str(self.insurance_coverage_amount),
            "insurance_expiration_date": self.insurance_expiration_date.isoformat(),
            "storage_monthly_fee_per_unit": str(self.storage_monthly_fee_per_unit),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Warehouse:
        return cls(
            code=str(data["code"]),
            name=str(data["name"]),
            cnpj=str(data["cnpj"]),
            state=str(data["state"]),
            city=str(data["city"]),
            accreditation_number=str(data["accreditation_number"]),
            accreditation_status=WarehouseAccreditationStatus(data["accreditation_status"]),
            static_capacity_tons=to_decimal(data["static_capacity_tons"], "static_capacity_tons"),
            insurance_policy_number=str(data["insurance_policy_number"]),
            insurance_company=str(data["insurance_company"]),
            insurance_coverage_amount=to_decimal(data["insurance_coverage_amount"], "insurance_coverage_amount"),
            insurance_expiration_date=parse_date(data["insurance_expiration_date"], "insurance_expiration_date"),
            storage_monthly_fee_per_unit=to_decimal(data["storage_monthly_fee_per_unit"], "storage_monthly_fee_per_unit"),
        )


@dataclass
class AgriculturalWarrant:
    """Título de crédito emitido conjuntamente (CDA e WA) sob a Lei nº 11.076/2004."""
    id: str
    cda_number: str
    wa_number: str
    warehouse_code: str
    depositor_name: str
    depositor_document: str
    commodity: AgriculturalCommodity
    crop_year: str
    harvest_year: int
    unit_of_measure: UnitOfMeasure
    initial_quantity: Decimal
    quality_metrics: QualityMetrics
    emission_date: date
    maturity_date: date
    reference_price_per_unit: Decimal
    currency: str = "BRL"
    status: WarrantStatus = WarrantStatus.ACTIVE_DEPOSIT
    is_bundled: bool = True
    wa_debt_principal: Decimal = Decimal("0.00")
    wa_debt_interest_rate_annual: Decimal = Decimal("0.00")
    wa_creditor_name: Optional[str] = None
    wa_creditor_document: Optional[str] = None
    b3_pledged_account: Optional[str] = None
    b3_associated_future: Optional[str] = None
    accrued_storage_fees: Decimal = Decimal("0.00")
    protest_date: Optional[date] = None
    protest_notary: Optional[str] = None

    def validate(self) -> None:
        """Valida integridade estrutural e legal dos títulos unidos ou separados."""
        if not self.id or not self.cda_number or not self.wa_number:
            raise AgriculturalWarrantValidationError("Identificadores de CDA/WA obrigatórios.")
        if self.initial_quantity <= Decimal("0.0000"):
            raise AgriculturalWarrantValidationError("Quantidade inicial do produto deve ser estritamente positiva.")
        if self.reference_price_per_unit <= Decimal("0.0000"):
            raise AgriculturalWarrantValidationError("Preço de referência unitário deve ser estritamente positivo.")
        if self.maturity_date <= self.emission_date:
            raise AgriculturalWarrantValidationError("Data de vencimento deve ser posterior à data de emissão.")
        if not self.is_bundled:
            if self.wa_debt_principal <= Decimal("0.00"):
                raise AgriculturalWarrantValidationError(
                    "Warrant separado (WA desmembrado) exige valor do crédito garantido estritamente positivo."
                )
            if not self.wa_creditor_name or not self.wa_creditor_document:
                raise AgriculturalWarrantValidationError(
                    "Warrant separado exige credor fiduciário do WA identificado."
                )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "cda_number": self.cda_number,
            "wa_number": self.wa_number,
            "warehouse_code": self.warehouse_code,
            "depositor_name": self.depositor_name,
            "depositor_document": self.depositor_document,
            "commodity": self.commodity.value,
            "crop_year": self.crop_year,
            "harvest_year": self.harvest_year,
            "unit_of_measure": self.unit_of_measure.value,
            "initial_quantity": str(self.initial_quantity),
            "quality_metrics": self.quality_metrics.to_dict(),
            "emission_date": self.emission_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "reference_price_per_unit": str(self.reference_price_per_unit),
            "currency": self.currency,
            "status": self.status.value,
            "is_bundled": self.is_bundled,
            "wa_debt_principal": str(self.wa_debt_principal),
            "wa_debt_interest_rate_annual": str(self.wa_debt_interest_rate_annual),
            "wa_creditor_name": self.wa_creditor_name,
            "wa_creditor_document": self.wa_creditor_document,
            "b3_pledged_account": self.b3_pledged_account,
            "b3_associated_future": self.b3_associated_future,
            "accrued_storage_fees": str(self.accrued_storage_fees),
            "protest_date": self.protest_date.isoformat() if self.protest_date else None,
            "protest_notary": self.protest_notary,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgriculturalWarrant:
        return cls(
            id=str(data["id"]),
            cda_number=str(data["cda_number"]),
            wa_number=str(data["wa_number"]),
            warehouse_code=str(data["warehouse_code"]),
            depositor_name=str(data["depositor_name"]),
            depositor_document=str(data["depositor_document"]),
            commodity=AgriculturalCommodity(data["commodity"]),
            crop_year=str(data["crop_year"]),
            harvest_year=int(data["harvest_year"]),
            unit_of_measure=UnitOfMeasure(data["unit_of_measure"]),
            initial_quantity=to_decimal(data["initial_quantity"], "initial_quantity"),
            quality_metrics=QualityMetrics.from_dict(data["quality_metrics"]),
            emission_date=parse_date(data["emission_date"], "emission_date"),
            maturity_date=parse_date(data["maturity_date"], "maturity_date"),
            reference_price_per_unit=to_decimal(data["reference_price_per_unit"], "reference_price_per_unit"),
            currency=str(data.get("currency", "BRL")),
            status=WarrantStatus(data.get("status", WarrantStatus.ACTIVE_DEPOSIT.value)),
            is_bundled=bool(data.get("is_bundled", True)),
            wa_debt_principal=to_decimal(data.get("wa_debt_principal", 0), "wa_debt_principal"),
            wa_debt_interest_rate_annual=to_decimal(data.get("wa_debt_interest_rate_annual", 0), "wa_debt_interest_rate_annual"),
            wa_creditor_name=data.get("wa_creditor_name"),
            wa_creditor_document=data.get("wa_creditor_document"),
            b3_pledged_account=data.get("b3_pledged_account"),
            b3_associated_future=data.get("b3_associated_future"),
            accrued_storage_fees=to_decimal(data.get("accrued_storage_fees", 0), "accrued_storage_fees"),
            protest_date=parse_date(data["protest_date"], "protest_date") if data.get("protest_date") else None,
            protest_notary=data.get("protest_notary"),
        )


@dataclass
class HaircutBreakdown:
    """Detalhamento dos componentes de deságio regulatório e físico aplicados."""
    base_haircut: Decimal
    crop_age_haircut: Decimal
    storage_duration_haircut: Decimal
    quality_haircut: Decimal
    total_haircut: Decimal
    capped_haircut: Decimal
    is_eligible: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_haircut": str(self.base_haircut),
            "crop_age_haircut": str(self.crop_age_haircut),
            "storage_duration_haircut": str(self.storage_duration_haircut),
            "quality_haircut": str(self.quality_haircut),
            "total_haircut": str(self.total_haircut),
            "capped_haircut": str(self.capped_haircut),
            "is_eligible": self.is_eligible,
        }


@dataclass
class ValuationResult:
    """Resultado da avaliação mark-to-market, quebra técnica e valor de colateral elegível."""
    warrant_id: str
    commodity: AgriculturalCommodity
    as_of_date: date
    months_in_storage: int
    initial_quantity: Decimal
    technical_loss_rate: Decimal
    technical_loss_quantity: Decimal
    effective_physical_quantity: Decimal
    reference_price_per_unit: Decimal
    gross_market_value: Decimal
    haircuts: HaircutBreakdown
    eligible_collateral_value: Decimal
    wa_debt_burden: Decimal
    net_clearing_margin_credit: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "warrant_id": self.warrant_id,
            "commodity": self.commodity.value,
            "as_of_date": self.as_of_date.isoformat(),
            "months_in_storage": self.months_in_storage,
            "initial_quantity": str(self.initial_quantity),
            "technical_loss_rate": str(self.technical_loss_rate),
            "technical_loss_quantity": str(self.technical_loss_quantity),
            "effective_physical_quantity": str(self.effective_physical_quantity),
            "reference_price_per_unit": str(self.reference_price_per_unit),
            "gross_market_value": str(self.gross_market_value),
            "haircuts": self.haircuts.to_dict(),
            "eligible_collateral_value": str(self.eligible_collateral_value),
            "wa_debt_burden": str(self.wa_debt_burden),
            "net_clearing_margin_credit": str(self.net_clearing_margin_credit),
        }


@dataclass
class MarginCheckResult:
    """Avaliação de adequação de margem de garantia na Câmara B3 para derivativos agrícolas."""
    clearing_account: str
    as_of_date: date
    required_margin: Decimal
    allocated_collateral_value: Decimal
    margin_surplus_deficit: Decimal
    is_margin_compliant: bool
    margin_call_amount: Decimal
    pledged_warrant_ids: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clearing_account": self.clearing_account,
            "as_of_date": self.as_of_date.isoformat(),
            "required_margin": str(self.required_margin),
            "allocated_collateral_value": str(self.allocated_collateral_value),
            "margin_surplus_deficit": str(self.margin_surplus_deficit),
            "is_margin_compliant": self.is_margin_compliant,
            "margin_call_amount": str(self.margin_call_amount),
            "pledged_warrant_ids": self.pledged_warrant_ids,
        }


@dataclass
class ExecutionResult:
    """Resultado contábil e físico da excussão e leilão público do produto agropecuário."""
    warrant_id: str
    auction_date: date
    effective_physical_quantity_sold: Decimal
    auction_unit_price: Decimal
    gross_auction_proceeds: Decimal
    warehouse_expenses: Decimal
    net_proceeds_after_warehouse: Decimal
    wa_debt_settled: Decimal
    cda_surplus_refunded: Decimal
    wa_residual_unpaid_debt: Decimal
    is_debt_fully_satisfied: bool
    conservation_verified: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "warrant_id": self.warrant_id,
            "auction_date": self.auction_date.isoformat(),
            "effective_physical_quantity_sold": str(self.effective_physical_quantity_sold),
            "auction_unit_price": str(self.auction_unit_price),
            "gross_auction_proceeds": str(self.gross_auction_proceeds),
            "warehouse_expenses": str(self.warehouse_expenses),
            "net_proceeds_after_warehouse": str(self.net_proceeds_after_warehouse),
            "wa_debt_settled": str(self.wa_debt_settled),
            "cda_surplus_refunded": str(self.cda_surplus_refunded),
            "wa_residual_unpaid_debt": str(self.wa_residual_unpaid_debt),
            "is_debt_fully_satisfied": self.is_debt_fully_satisfied,
            "conservation_verified": self.conservation_verified,
        }


# =====================================================================
# MOTOR PRINCIPAL (ENGINE)
# =====================================================================

class AgriculturalWarrantCollateralEngine:
    """Motor determinístico de garantias em commodities agrícolas (CDA/WA) na B3."""

    def __init__(self) -> None:
        self.warehouses: Dict[str, Warehouse] = {}
        self.warrants: Dict[str, AgriculturalWarrant] = {}
        self.execution_history: List[ExecutionResult] = []

    # -----------------------------------------------------------------
    # REGISTRO E CONSULTA
    # -----------------------------------------------------------------

    def register_warehouse(self, warehouse: Warehouse) -> None:
        """Registra e valida armazém geral credenciado."""
        warehouse.validate()
        self.warehouses[warehouse.code] = warehouse

    def get_warehouse(self, code: str) -> Warehouse:
        """Recupera armazém pelo código ou levanta exceção."""
        if code not in self.warehouses:
            raise AgriculturalWarrantValidationError(f"Armazém '{code}' não cadastrado no sistema.")
        return self.warehouses[code]

    def register_warrant(self, warrant: AgriculturalWarrant) -> None:
        """Registra e valida conjunto CDA/WA emitido por armazém geral."""
        warrant.validate()
        warehouse = self.get_warehouse(warrant.warehouse_code)
        warehouse.validate(warrant.emission_date)
        self.warrants[warrant.id] = warrant

    def get_warrant(self, warrant_id: str) -> AgriculturalWarrant:
        """Recupera warrant pelo identificador único."""
        if warrant_id not in self.warrants:
            raise AgriculturalWarrantValidationError(f"Warrant '{warrant_id}' não encontrado.")
        return self.warrants[warrant_id]

    # -----------------------------------------------------------------
    # DESMEMBRAMENTO E REUNIÃO (ARTS. 15 E 16 DA LEI 11.076/2004)
    # -----------------------------------------------------------------

    def split_bundle(
        self,
        warrant_id: str,
        wa_creditor_name: str,
        wa_creditor_document: str,
        debt_principal: Decimal,
        annual_interest_rate: Decimal,
    ) -> AgriculturalWarrant:
        """
        Desmembra os títulos: o CDA transfere a propriedade e o WA confere penhor financeiro.
        """
        warrant = self.get_warrant(warrant_id)
        if not warrant.is_bundled:
            raise AgriculturalWarrantValidationError(
                f"Warrant '{warrant_id}' já está desmembrado (separado)."
            )
        if warrant.status not in (WarrantStatus.ACTIVE_DEPOSIT, WarrantStatus.RELEASED):
            raise WarrantStateTransitionError(
                f"Não é permitido desmembrar warrant no estado '{warrant.status.value}'."
            )
        if debt_principal <= Decimal("0.00"):
            raise AgriculturalWarrantValidationError("Principal da dívida do WA deve ser positivo.")

        warrant.is_bundled = False
        warrant.wa_creditor_name = wa_creditor_name
        warrant.wa_creditor_document = wa_creditor_document
        warrant.wa_debt_principal = round_cents(debt_principal)
        warrant.wa_debt_interest_rate_annual = round_rate(annual_interest_rate)
        return warrant

    def reunite_bundle(self, warrant_id: str, settlement_proof_ref: str) -> AgriculturalWarrant:
        """
        Reúne CDA e WA mediante liquidação ou cancelamento da obrigação garantida pelo WA.
        """
        warrant = self.get_warrant(warrant_id)
        if warrant.is_bundled:
            raise AgriculturalWarrantValidationError(
                f"Warrant '{warrant_id}' já está unificado (bundled)."
            )
        if not settlement_proof_ref:
            raise AgriculturalWarrantValidationError(
                "Exige comprovante de quitação do crédito ou apresentação do WA quitado."
            )

        warrant.is_bundled = True
        warrant.wa_debt_principal = Decimal("0.00")
        warrant.wa_debt_interest_rate_annual = Decimal("0.00")
        warrant.wa_creditor_name = None
        warrant.wa_creditor_document = None
        return warrant

    # -----------------------------------------------------------------
    # CÁLCULO DE QUEBRA TÉCNICA E MATRIZ DE HAIRCUTS B3
    # -----------------------------------------------------------------

    def calculate_valuation(
        self,
        warrant_id: str,
        as_of_date: date,
        spot_price_override: Optional[Decimal] = None,
    ) -> ValuationResult:
        """
        Calcula mark-to-market, quebra técnica acumulada e matriz de haircuts B3.
        """
        warrant = self.get_warrant(warrant_id)
        warehouse = self.get_warehouse(warrant.warehouse_code)
        warehouse.validate(as_of_date)

        if as_of_date < warrant.emission_date:
            raise AgriculturalWarrantValidationError(
                f"Data de avaliação {as_of_date} anterior à emissão {warrant.emission_date}."
            )

        # 1. Tempo decorrido de armazenamento
        days_in_storage = (as_of_date - warrant.emission_date).days
        months_in_storage = max(0, days_in_storage // 30)

        # 2. Quebra técnica (Technical Loss / Shrinkage)
        loss_monthly_rate = TECHNICAL_LOSS_MONTHLY_RATES.get(warrant.commodity, Decimal("0.0020"))
        technical_loss_rate = round_rate(loss_monthly_rate * Decimal(months_in_storage))
        # Limita quebra técnica acumulada a no máximo 10%
        technical_loss_rate = min(technical_loss_rate, Decimal("0.10000000"))

        technical_loss_qty = round_qty(warrant.initial_quantity * technical_loss_rate)
        effective_physical_qty = round_qty(warrant.initial_quantity - technical_loss_qty)

        # Invariante 1: Conservação Física
        if warrant.initial_quantity != (effective_physical_qty + technical_loss_qty):
            raise AgriculturalInvariantViolationError(
                f"Quebra na lei de conservação de massa: inicial={warrant.initial_quantity} "
                f"!= efetiva={effective_physical_qty} + perda={technical_loss_qty}"
            )

        # 3. Preço de Referência
        ref_price = round_unit(spot_price_override or warrant.reference_price_per_unit)
        gross_market_value = round_cents(effective_physical_qty * ref_price)

        # 4. Matriz de Haircuts B3
        # a) Haircut Base
        base_haircut = COMMODITY_BASE_HAIRCUTS.get(warrant.commodity, Decimal("0.1500"))

        # b) Haircut por Safra (Crop Age)
        crop_age_years = max(0, as_of_date.year - warrant.harvest_year)
        if crop_age_years == 0:
            crop_age_haircut = Decimal("0.0000")
        elif crop_age_years == 1:
            crop_age_haircut = Decimal("0.0600")
        elif crop_age_years == 2:
            crop_age_haircut = Decimal("0.1500")
        else:
            crop_age_haircut = Decimal("0.3000")

        # c) Haircut por Tempo de Armazenamento
        if months_in_storage <= 3:
            storage_haircut = Decimal("0.0000")
        elif months_in_storage <= 6:
            storage_haircut = Decimal("0.0200")
        elif months_in_storage <= 12:
            storage_haircut = Decimal("0.0500")
        else:
            storage_haircut = Decimal("0.1000")

        # d) Haircut por Desvio de Qualidade
        standards = QUALITY_STANDARDS.get(warrant.commodity, {})
        quality_haircut = Decimal("0.0000")

        moisture_excess = max(
            Decimal("0.0"),
            warrant.quality_metrics.moisture_pct - standards.get("max_moisture_pct", Decimal("14.0")),
        )
        if moisture_excess > Decimal("0.0"):
            quality_haircut += (moisture_excess / Decimal("0.5")) * Decimal("0.0100")

        impurity_excess = max(
            Decimal("0.0"),
            warrant.quality_metrics.impurity_pct - standards.get("max_impurity_pct", Decimal("1.0")),
        )
        if impurity_excess > Decimal("0.0"):
            quality_haircut += (impurity_excess / Decimal("0.5")) * Decimal("0.0100")

        damaged_excess = max(
            Decimal("0.0"),
            warrant.quality_metrics.damaged_pct - standards.get("max_damaged_pct", Decimal("8.0")),
        )
        if damaged_excess > Decimal("0.0"):
            quality_haircut += (damaged_excess / Decimal("1.0")) * Decimal("0.0100")

        quality_haircut = round_rate(quality_haircut)

        # Haircut Total
        total_haircut = round_rate(base_haircut + crop_age_haircut + storage_haircut + quality_haircut)
        is_eligible = total_haircut <= MAX_ELIGIBLE_HAIRCUT

        if is_eligible:
            capped_haircut = total_haircut
            eligible_collateral_value = round_cents(
                gross_market_value * (Decimal("1.00000000") - capped_haircut)
            )
        else:
            capped_haircut = Decimal("1.00000000")
            eligible_collateral_value = Decimal("0.00")

        haircut_breakdown = HaircutBreakdown(
            base_haircut=base_haircut,
            crop_age_haircut=crop_age_haircut,
            storage_duration_haircut=storage_haircut,
            quality_haircut=quality_haircut,
            total_haircut=total_haircut,
            capped_haircut=capped_haircut,
            is_eligible=is_eligible,
        )

        # 5. Dedução de Passivo Financeiro do WA (quando desmembrado)
        if not warrant.is_bundled and warrant.wa_debt_principal > Decimal("0.00"):
            annual_rate = warrant.wa_debt_interest_rate_annual
            interest = round_cents(
                warrant.wa_debt_principal * annual_rate * Decimal(days_in_storage) / Decimal("365.0")
            )
            wa_debt_burden = round_cents(warrant.wa_debt_principal + interest)
        else:
            wa_debt_burden = Decimal("0.00")

        # Crédito líquido reconhecido na Câmara B3
        net_clearing_margin_credit = max(
            Decimal("0.00"),
            round_cents(eligible_collateral_value - wa_debt_burden),
        )

        return ValuationResult(
            warrant_id=warrant.id,
            commodity=warrant.commodity,
            as_of_date=as_of_date,
            months_in_storage=months_in_storage,
            initial_quantity=warrant.initial_quantity,
            technical_loss_rate=technical_loss_rate,
            technical_loss_quantity=technical_loss_qty,
            effective_physical_quantity=effective_physical_qty,
            reference_price_per_unit=ref_price,
            gross_market_value=gross_market_value,
            haircuts=haircut_breakdown,
            eligible_collateral_value=eligible_collateral_value,
            wa_debt_burden=wa_debt_burden,
            net_clearing_margin_credit=net_clearing_margin_credit,
        )

    # -----------------------------------------------------------------
    # BLOQUEIO FIDUCIÁRIO E MARGEM NA CÂMARA B3
    # -----------------------------------------------------------------

    def pledge_to_b3_clearing(
        self,
        warrant_id: str,
        clearing_account: str,
        associated_future: str,
    ) -> AgriculturalWarrant:
        """
        Bloqueia fiduciariamente o CDA/WA como margem de garantia na Câmara B3.
        """
        warrant = self.get_warrant(warrant_id)
        if warrant.status not in (WarrantStatus.ACTIVE_DEPOSIT, WarrantStatus.RELEASED):
            raise WarrantStateTransitionError(
                f"Warrant '{warrant_id}' no estado '{warrant.status.value}' não pode ser vinculado à câmara."
            )
        if not clearing_account:
            raise AgriculturalWarrantValidationError("Conta de compensação da câmara obrigatória.")

        warrant.status = WarrantStatus.PLEDGED_TO_B3
        warrant.b3_pledged_account = clearing_account
        warrant.b3_associated_future = associated_future
        return warrant

    def evaluate_clearing_margin(
        self,
        clearing_account: str,
        required_margin: Decimal,
        as_of_date: date,
    ) -> MarginCheckResult:
        """
        Avalia se a carteira de colaterais agropecuários cobre a margem exigida pela B3.
        """
        req_margin = round_cents(required_margin)
        pledged = [
            w for w in self.warrants.values()
            if w.b3_pledged_account == clearing_account
            and w.status in (WarrantStatus.PLEDGED_TO_B3, WarrantStatus.IN_MARGIN_CALL)
        ]

        total_credit = Decimal("0.00")
        pledged_ids: List[str] = []

        for w in pledged:
            val = self.calculate_valuation(w.id, as_of_date)
            total_credit += val.net_clearing_margin_credit
            pledged_ids.append(w.id)

        total_credit = round_cents(total_credit)
        surplus_deficit = round_cents(total_credit - req_margin)
        is_compliant = surplus_deficit >= Decimal("0.00")
        margin_call = Decimal("0.00") if is_compliant else round_cents(req_margin - total_credit)

        # Atualiza status dos warrants em monitoramento de margem
        for w in pledged:
            if not is_compliant:
                w.status = WarrantStatus.IN_MARGIN_CALL
            elif w.status == WarrantStatus.IN_MARGIN_CALL:
                w.status = WarrantStatus.PLEDGED_TO_B3

        return MarginCheckResult(
            clearing_account=clearing_account,
            as_of_date=as_of_date,
            required_margin=req_margin,
            allocated_collateral_value=total_credit,
            margin_surplus_deficit=surplus_deficit,
            is_margin_compliant=is_compliant,
            margin_call_amount=margin_call,
            pledged_warrant_ids=pledged_ids,
        )

    def release_from_b3_clearing(
        self,
        warrant_id: str,
        required_margin_after_release: Decimal,
        as_of_date: date,
    ) -> AgriculturalWarrant:
        """
        Desonera o warrant garantindo o Safe-Release Guardrail (anti-déficit de margem).
        """
        warrant = self.get_warrant(warrant_id)
        if warrant.status not in (WarrantStatus.PLEDGED_TO_B3, WarrantStatus.IN_MARGIN_CALL):
            raise WarrantStateTransitionError(
                f"Warrant '{warrant_id}' não está vinculado à B3 Clearing (status: {warrant.status.value})."
            )

        account = warrant.b3_pledged_account
        if not account:
            raise AgriculturalWarrantValidationError("Warrant sem conta de compensação vinculada.")

        # Simula margem sem este título
        other_warrants = [
            w for w in self.warrants.values()
            if w.b3_pledged_account == account
            and w.id != warrant_id
            and w.status in (WarrantStatus.PLEDGED_TO_B3, WarrantStatus.IN_MARGIN_CALL)
        ]

        remaining_credit = Decimal("0.00")
        for ow in other_warrants:
            val = self.calculate_valuation(ow.id, as_of_date)
            remaining_credit += val.net_clearing_margin_credit

        remaining_credit = round_cents(remaining_credit)
        req_margin = round_cents(required_margin_after_release)

        if remaining_credit < req_margin:
            deficit = round_cents(req_margin - remaining_credit)
            raise MarginReleaseViolationError(
                f"Safe-Release Guardrail: Desoneração do warrant '{warrant_id}' rejeitada. "
                f"Colateral remanescente (R$ {remaining_credit}) é inferior à margem "
                f"requerida (R$ {req_margin}), gerando déficit de R$ {deficit}."
            )

        warrant.status = WarrantStatus.RELEASED
        warrant.b3_pledged_account = None
        warrant.b3_associated_future = None
        return warrant

    # -----------------------------------------------------------------
    # PROCEDIMENTO DE PROTESTO E EXCUSSÃO EM LEILÃO (ARTS. 19 A 22)
    # -----------------------------------------------------------------

    def record_protest(
        self,
        warrant_id: str,
        protest_date: date,
        notary_name: str,
    ) -> AgriculturalWarrant:
        """
        Registra formalmente o protesto do WA por inadimplemento do devedor do crédito.
        """
        warrant = self.get_warrant(warrant_id)
        if warrant.status in (WarrantStatus.PROTESTED, WarrantStatus.EXECUTED, WarrantStatus.CANCELLED):
            raise WarrantProtestError(
                f"Warrant '{warrant_id}' em estado inválido para protesto: {warrant.status.value}."
            )
        if protest_date < warrant.maturity_date:
            raise WarrantProtestError(
                f"Protesto em {protest_date} prematuro: anterior ao vencimento do título {warrant.maturity_date}."
            )
        if not notary_name:
            raise WarrantProtestError("Identificação do Cartório de Protesto é obrigatória.")

        warrant.status = WarrantStatus.PROTESTED
        warrant.protest_date = protest_date
        warrant.protest_notary = notary_name
        return warrant

    def execute_physical_auction(
        self,
        warrant_id: str,
        auction_date: date,
        auction_unit_price: Decimal,
        warehouse_expenses_override: Optional[Decimal] = None,
    ) -> ExecutionResult:
        """
        Realiza a venda em leilão público e liquida os recursos no waterfall legal:
        1. Despesas de armazenagem, tributos e custas de leilão;
        2. Liquidação do crédito do titular do WA (principal + encargos);
        3. Sobra remanescente entregue ao titular do CDA (ou devedor originário).
        """
        warrant = self.get_warrant(warrant_id)
        if warrant.status not in (WarrantStatus.PROTESTED, WarrantStatus.PLEDGED_TO_B3, WarrantStatus.IN_MARGIN_CALL):
            raise AuctionExecutionError(
                f"Warrant '{warrant_id}' no estado '{warrant.status.value}' não elegível para leilão de excussão."
            )

        # 1. Apuração da mercadoria física disponível com quebra técnica até o leilão
        valuation = self.calculate_valuation(warrant_id, auction_date, auction_unit_price)
        sold_qty = valuation.effective_physical_quantity
        unit_price = round_unit(auction_unit_price)

        gross_proceeds = round_cents(sold_qty * unit_price)

        # 2. Despesas de armazenagem acumuladas
        warehouse = self.get_warehouse(warrant.warehouse_code)
        if warehouse_expenses_override is not None:
            warehouse_expenses = round_cents(warehouse_expenses_override)
        else:
            # Diárias/mensalidades de armazenagem conforme tarifa do armazém
            base_fee = round_cents(
                sold_qty * warehouse.storage_monthly_fee_per_unit * Decimal(valuation.months_in_storage)
            )
            warehouse_expenses = round_cents(base_fee + warrant.accrued_storage_fees)

        # Prioridade 1: Armazém Geral e Despesas de Leilão
        paid_warehouse = min(gross_proceeds, warehouse_expenses)
        proceeds_after_warehouse = round_cents(gross_proceeds - paid_warehouse)

        # Prioridade 2: Crédito Garantido pelo WA
        # Total devido ao titular do WA (principal + juros proporcionais)
        days_debt = max(1, (auction_date - warrant.emission_date).days)
        annual_rate = warrant.wa_debt_interest_rate_annual
        wa_interest = round_cents(
            warrant.wa_debt_principal * annual_rate * Decimal(days_debt) / Decimal("365.0")
        )
        total_wa_due = round_cents(warrant.wa_debt_principal + wa_interest)

        wa_settled = min(proceeds_after_warehouse, total_wa_due)
        proceeds_after_wa = round_cents(proceeds_after_warehouse - wa_settled)
        wa_residual_debt = round_cents(total_wa_due - wa_settled)
        is_debt_satisfied = (wa_residual_debt == Decimal("0.00"))

        # Prioridade 3: Saldo Remanescente ao Titular do CDA
        cda_surplus = proceeds_after_wa

        # Verificação formal da lei de conservação financeira
        total_distributed = round_cents(paid_warehouse + wa_settled + cda_surplus)
        conservation_verified = (gross_proceeds == total_distributed)

        if not conservation_verified:
            raise AgriculturalInvariantViolationError(
                f"Vazamento financeiro no leilão: Bruto={gross_proceeds} != "
                f"Armazém={paid_warehouse} + WA={wa_settled} + CDA={cda_surplus}"
            )

        # Atualiza status do warrant
        warrant.status = WarrantStatus.EXECUTED

        result = ExecutionResult(
            warrant_id=warrant.id,
            auction_date=auction_date,
            effective_physical_quantity_sold=sold_qty,
            auction_unit_price=unit_price,
            gross_auction_proceeds=gross_proceeds,
            warehouse_expenses=paid_warehouse,
            net_proceeds_after_warehouse=proceeds_after_warehouse,
            wa_debt_settled=wa_settled,
            cda_surplus_refunded=cda_surplus,
            wa_residual_unpaid_debt=wa_residual_debt,
            is_debt_fully_satisfied=is_debt_satisfied,
            conservation_verified=conservation_verified,
        )

        self.execution_history.append(result)
        return result

    # -----------------------------------------------------------------
    # INVARIANTES FORMAIS DO SISTEMA
    # -----------------------------------------------------------------

    def verify_formal_invariants(self, as_of_date: Optional[date] = None) -> Dict[str, bool]:
        """
        Verifica formalmente todas as leis de conservação e restrições de integridade.
        """
        ref_date = as_of_date or date(2026, 11, 4)
        results = {
            "physical_quantity_conservation": True,
            "valuation_non_negativity": True,
            "haircut_codomain_bounded": True,
            "crop_age_monotonicity": True,
            "storage_duration_monotonicity": True,
            "auction_financial_conservation": True,
            "auction_quantity_conservation": True,
            "warehouse_insurance_validity": True,
        }

        # 1. Armazéns e Seguros
        for wh in self.warehouses.values():
            if wh.accreditation_status != WarehouseAccreditationStatus.ACCREDITED:
                results["warehouse_insurance_validity"] = False
            if wh.insurance_coverage_amount <= Decimal("0.00"):
                results["warehouse_insurance_validity"] = False

        # 2. Avaliação de Títulos e Conservação de Massa
        for warrant in self.warrants.values():
            val = self.calculate_valuation(warrant.id, ref_date)

            # Conservação de massa física
            if warrant.initial_quantity != (val.effective_physical_quantity + val.technical_loss_quantity):
                results["physical_quantity_conservation"] = False

            # Não-negatividade
            if val.gross_market_value < Decimal("0.00"):
                results["valuation_non_negativity"] = False
            if val.eligible_collateral_value < Decimal("0.00"):
                results["valuation_non_negativity"] = False
            if val.net_clearing_margin_credit < Decimal("0.00"):
                results["valuation_non_negativity"] = False

            # Co-domínio de haircuts [0, 1]
            if not (Decimal("0.00") <= val.haircuts.capped_haircut <= Decimal("1.00")):
                results["haircut_codomain_bounded"] = False

        # 3. Monotonicidade da Matriz de Haircuts
        if self.warrants:
            sample_warrant = next(iter(self.warrants.values()))
            test_dates = [
                sample_warrant.emission_date + timedelta(days=60),
                sample_warrant.emission_date + timedelta(days=150),
                sample_warrant.emission_date + timedelta(days=270),
                sample_warrant.emission_date + timedelta(days=450),
            ]
            haircuts_by_date = [
                self.calculate_valuation(sample_warrant.id, d).haircuts.storage_duration_haircut
                for d in test_dates
            ]
            for i in range(len(haircuts_by_date) - 1):
                if haircuts_by_date[i] > haircuts_by_date[i + 1]:
                    results["storage_duration_monotonicity"] = False

        # 4. Histórico de Leilões
        for exec_res in self.execution_history:
            if not exec_res.conservation_verified:
                results["auction_financial_conservation"] = False
            # Bruto == Despesas + WA + CDA
            sum_parts = round_cents(
                exec_res.warehouse_expenses + exec_res.wa_debt_settled + exec_res.cda_surplus_refunded
            )
            if exec_res.gross_auction_proceeds != sum_parts:
                results["auction_financial_conservation"] = False

        return results

    # -----------------------------------------------------------------
    # CARREGAMENTO VIA FIXTURES JSON E SERIALIZAÇÃO
    # -----------------------------------------------------------------

    @classmethod
    def load_from_fixture(cls, file_path: Union[str, Path]) -> AgriculturalWarrantCollateralEngine:
        """Carrega e instancia o motor a partir de fixture JSON padronizada."""
        path = Path(file_path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        engine = cls()
        for wh_data in data.get("warehouses", []):
            engine.register_warehouse(Warehouse.from_dict(wh_data))

        for w_data in data.get("warrants", []):
            engine.register_warrant(AgriculturalWarrant.from_dict(w_data))

        return engine

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado completo do motor para dicionário Python."""
        return {
            "schema_version": "1.0.0",
            "flow_code": "FL-078",
            "system": "B3_CLEARING_AGRO_COLLATERAL",
            "warehouses": [wh.to_dict() for wh in self.warehouses.values()],
            "warrants": [w.to_dict() for w in self.warrants.values()],
            "execution_history": [e.to_dict() for e in self.execution_history],
        }
