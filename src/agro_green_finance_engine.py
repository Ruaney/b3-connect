"""Motor Especializado de Agronegócio e Finanças Verdes B3 (Agro & Green Finance Registry Engine).

Este módulo implementa os três subsistemas regulados integrados da B3 e CSD:
1. Cédula de Produto Rural (CPR Digital / Lei nº 13.986/2020 - Nova Lei do Agro):
   - CPR Física (entrega de produto) e CPR Financeira (liquidação por índice CEPEA/preço referencial);
   - Fracionamento eletrônico determinístico (desdobramento em cotas/frações sem resíduo);
   - Averbação de garantias reais e fidejussórias: Penhor Rural (Agrícola/Pecuário),
     Alienação Fiduciária de Safra Futura/Bens e Patrimônio Rural em Afetação (Lei nº 13.986/2020).
2. Créditos de Descarbonização (CBIO / Lei nº 13.576/2017 - Política Nacional de Biocombustíveis RenovaBio):
   - Escrituração primária lastreada em NF-e válida (44 dígitos) e certificado ANP;
   - Mercado secundário livre de dupla contagem e transferências atômicas de custódia;
   - Aposentadoria compulsória de CBIOs para atendimento de metas individuais de distribuidores
     de combustíveis fósseis, emissão de certidão irrevogável e cálculo de carry-over/penalidades ANP.
3. Contratos Bilaterais de Energia Elétrica (CCEE / Aneel - Mercado Livre / ACL):
   - Registro de contratos bilaterais de compra e venda no ACL (Submercados SIN: SE/CO, S, NE, N);
   - Balanço de potência e energia (MWmédio <-> MWh, apuração física vs contratual do agente);
   - Liquidação de diferenças no Mercado de Curto Prazo (MCP CCEE) valorada ao PLD (Preço de Liquidação das Diferenças).

Invariantes Formais de Conservação e Integridade:
- LAW-044: Split Financeiro Fechado e Preservação de Balanço em Fracionamento de CPR e Liquidação.
- LAW-048: Inviolabilidade da Janela de Congelamento e Irrevogabilidade de Aposentadoria de Ativos.
- LAW-065: Inviolabilidade de Gravames e Garantias sobre Lastro de Safra (Preservação de Lastro Físico / Garantias <= Lastro).
- LAW-066: Conservação de Valor Patrimonial Total em Fracionamento e Liquidação Multilateral Soma-Zero CCEE PLD.

100% Python Standard Library pura (sem dependências externas).
"""

from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

try:
    from src.invariants import InvariantViolationError, check
except ImportError:
    class InvariantViolationError(AssertionError):
        """Fallback para InvariantViolationError quando importado diretamente."""
        def __init__(self, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
            super().__init__(message)
            self.message = message
            self.law_code = law_code
            self.details = details or {}

        def __str__(self) -> str:
            prefix = f"[{self.law_code}] " if self.law_code else ""
            return f"VIOLAÇÃO DE INVARIANTE: {prefix}{self.message}"

    def check(condition: bool, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
        if not condition:
            raise InvariantViolationError(message, law_code=law_code, details=details)


# ---------------------------------------------------------------------------
# Constantes de Precisão, Conversão e Domínio Regulatório
# ---------------------------------------------------------------------------

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")

TCO2E_PER_CBIO = Decimal("1.00")
NFE_KEY_REGEX = re.compile(r"^\d{44}$")
CAR_REGEX = re.compile(r"^([A-Z]{2})-(\d{7})-([A-Fa-f0-9]{32})$")
SIGEF_REGEX = re.compile(r"^[A-Fa-f0-9-]{8,36}$")
CROP_YEAR_REGEX = re.compile(r"^(\d{4})(?:/(\d{4}))?$")

ANP_CARRYOVER_MIN_COMPLIANCE_PCT = Decimal("85.00")
ANP_CARRYOVER_MAX_PCT = Decimal("15.00")
DEFAULT_PENALTY_PER_CBIO_BRL = Decimal("100.00")

DEFAULT_PLD_MIN = Decimal("69.04")
DEFAULT_PLD_MAX = Decimal("679.29")

BRAZILIAN_UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
    "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
    "RS", "RO", "RR", "SC", "SP", "SE", "TO"
}


# ---------------------------------------------------------------------------
# Funções Utilitárias Determinísticas
# ---------------------------------------------------------------------------

def round_cents(value: Union[Decimal, float, int, str]) -> Decimal:
    """Arredonda deterministicamente para 2 casas decimais (ROUND_HALF_UP)."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def round_energy(value: Union[Decimal, float, int, str]) -> Decimal:
    """Arredonda grandezas de energia (MW / MWh) para 4 casas decimais."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def round_qty(value: Union[Decimal, float, int, str]) -> Decimal:
    """Arredonda quantidades físicas (sacas, arrobas, toneladas) para 4 casas decimais."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def sha256_digest(content: str) -> str:
    """Retorna o hash SHA-256 hexadecimal da string informada."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_document(document: str) -> str:
    """Normaliza e valida dígitos de CPF (11 dígitos) ou CNPJ (14 dígitos)."""
    digits = re.sub(r"\D", "", document or "")
    if len(digits) not in (11, 14):
        raise AgroGreenFinanceError(
            f"Documento de identificação inválido: '{document}'. Esperado CPF (11 dígitos) ou CNPJ (14 dígitos)."
        )
    return digits


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class AgroGreenFinanceError(Exception):
    """Exceção base para o motor de agronegócio e finanças verdes."""
    pass


class CprValidationError(AgroGreenFinanceError):
    """Erro de validação cadastral ou negocial de CPR."""
    pass


class CprFractionationError(AgroGreenFinanceError):
    """Erro no processo de fracionamento eletrônico de CPR."""
    pass


class GuaranteeValidationError(AgroGreenFinanceError):
    """Erro de validação ou averbação de garantia agropecuária."""
    pass


class CbioValidationError(AgroGreenFinanceError):
    """Erro de escrituração primária ou validação de CBIO."""
    pass


class CbioTransferError(AgroGreenFinanceError):
    """Erro em transferência ou negociação de CBIO no mercado secundário."""
    pass


class CbioRetirementError(AgroGreenFinanceError):
    """Erro no processo de aposentadoria de CBIO ou apuração de conformidade ANP."""
    pass


class PowerContractValidationError(AgroGreenFinanceError):
    """Erro de validação de contratos de energia elétrica no ACL."""
    pass


class PowerSettlementError(AgroGreenFinanceError):
    """Erro no balanço de potência ou liquidação financeira de diferenças CCEE PLD."""
    pass


# ---------------------------------------------------------------------------
# Enums do Domínio Agropecuário e Energético
# ---------------------------------------------------------------------------

class CprType(str, Enum):
    """Modalidade da Cédula de Produto Rural conforme a Lei nº 8.929/1994 e 13.986/2020."""
    FISICA = "FISICA"
    FINANCEIRA = "FINANCEIRA"

    @classmethod
    def from_str(cls, value: Any) -> CprType:
        if isinstance(value, cls):
            return value
        norm = str(value or "").strip().upper().replace("Í", "I")
        if norm in ("FISICA", "PHYSICAL"):
            return cls.FISICA
        if norm in ("FINANCEIRA", "FINANCIAL"):
            return cls.FINANCEIRA
        raise CprValidationError(f"Tipo de CPR inválido: '{value}'. Permitidos: FISICA, FINANCEIRA.")


class CommodityType(str, Enum):
    """Produtos e commodities elegíveis para lastro de CPR."""
    SOJA = "SOJA"
    MILHO = "MILHO"
    CAFE = "CAFE"
    BOI_GORDO = "BOI_GORDO"
    ALGODAO = "ALGODAO"
    CANA_DE_ACUCAR = "CANA_DE_ACUCAR"
    TRIGO = "TRIGO"
    ARROZ = "ARROZ"
    FEIJAO = "FEIJAO"

    @classmethod
    def from_str(cls, value: Any) -> CommodityType:
        if isinstance(value, cls):
            return value
        norm = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
        norm = norm.replace("É", "E").replace("Õ", "O").replace("Ã", "A").replace("Ú", "U")
        synonyms = {
            "SOJA": cls.SOJA, "MILHO": cls.MILHO, "CAFE": cls.CAFE,
            "BOI_GORDO": cls.BOI_GORDO, "BOI": cls.BOI_GORDO,
            "ALGODAO": cls.ALGODAO, "CANA_DE_ACUCAR": cls.CANA_DE_ACUCAR, "CANA": cls.CANA_DE_ACUCAR,
            "TRIGO": cls.TRIGO, "ARROZ": cls.ARROZ, "FEIJAO": cls.FEIJAO
        }
        if norm in synonyms:
            return synonyms[norm]
        raise CprValidationError(f"Commodity inválida: '{value}'.")


class MeasurementUnit(str, Enum):
    """Unidades de medida padronizadas para produtos agropecuários."""
    SACAS_60KG = "SACAS_60KG"
    ARROBAS = "ARROBAS"
    TONELADAS = "TONELADAS"
    QUILOGRAMAS = "QUILOGRAMAS"
    LITROS = "LITROS"
    METROS_CUBICOS = "METROS_CUBICOS"

    @classmethod
    def from_str(cls, value: Any) -> MeasurementUnit:
        if isinstance(value, cls):
            return value
        norm = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
        synonyms = {
            "SACAS_60KG": cls.SACAS_60KG, "SACAS": cls.SACAS_60KG, "SC": cls.SACAS_60KG,
            "ARROBAS": cls.ARROBAS, "ARR": cls.ARROBAS, "@": cls.ARROBAS,
            "TONELADAS": cls.TONELADAS, "TON": cls.TONELADAS, "T": cls.TONELADAS,
            "QUILOGRAMAS": cls.QUILOGRAMAS, "KG": cls.QUILOGRAMAS,
            "LITROS": cls.LITROS, "L": cls.LITROS,
            "METROS_CUBICOS": cls.METROS_CUBICOS, "M3": cls.METROS_CUBICOS
        }
        if norm in synonyms:
            return synonyms[norm]
        raise CprValidationError(f"Unidade de medida inválida: '{value}'.")


class GuaranteeType(str, Enum):
    """Garantias reais e afetações patrimoniais da Nova Lei do Agro (Lei nº 13.986/2020)."""
    PENHOR_RURAL = "PENHOR_RURAL"
    ALIENACAO_FIDUCIARIA_SAFRA = "ALIENACAO_FIDUCIARIA_SAFRA"
    PATRIMONIO_RURAL_AFETACAO = "PATRIMONIO_RURAL_AFETACAO"
    ALIENACAO_FIDUCIARIA_IMOVEL = "ALIENACAO_FIDUCIARIA_IMOVEL"
    HIPOTECA_CEDULAR = "HIPOTECA_CEDULAR"
    AVAL = "AVAL"

    @classmethod
    def from_str(cls, value: Any) -> GuaranteeType:
        if isinstance(value, cls):
            return value
        norm = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
        norm = norm.replace("Á", "A").replace("É", "E").replace("Í", "I").replace("Ó", "O").replace("Ç", "C").replace("Ã", "A")
        synonyms = {
            "PENHOR_RURAL": cls.PENHOR_RURAL,
            "PENHOR_AGRICOLA": cls.PENHOR_RURAL,
            "PENHOR": cls.PENHOR_RURAL,
            "ALIENACAO_FIDUCIARIA_SAFRA": cls.ALIENACAO_FIDUCIARIA_SAFRA,
            "ALIENACAO_SAFRA": cls.ALIENACAO_FIDUCIARIA_SAFRA,
            "PATRIMONIO_RURAL_AFETACAO": cls.PATRIMONIO_RURAL_AFETACAO,
            "PATRIMONIO_DE_AFETACAO": cls.PATRIMONIO_RURAL_AFETACAO,
            "AFETACAO": cls.PATRIMONIO_RURAL_AFETACAO,
            "ALIENACAO_FIDUCIARIA_IMOVEL": cls.ALIENACAO_FIDUCIARIA_IMOVEL,
            "ALIENACAO_FIDUCIARIA": cls.ALIENACAO_FIDUCIARIA_IMOVEL,
            "HIPOTECA_CEDULAR": cls.HIPOTECA_CEDULAR,
            "HIPOTECA_RURAL": cls.HIPOTECA_CEDULAR,
            "AVAL": cls.AVAL,
        }
        if norm in synonyms:
            return synonyms[norm]
        raise GuaranteeValidationError(f"Tipo de garantia agropecuária inválido: '{value}'.")


class CprStatus(str, Enum):
    """Ciclo de vida do registro escritural da CPR no Sistema B3 NoMe."""
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    FRACTIONATED = "FRACTIONATED"
    SETTLED = "SETTLED"
    CANCELLED = "CANCELLED"


class BiofuelType(str, Enum):
    """Tipos de biocombustíveis elegíveis para emissão primária de CBIO (RenovaBio)."""
    ETANOL_HIDRATADO = "ETANOL_HIDRATADO"
    ETANOL_ANIDRO = "ETANOL_ANIDRO"
    BIODIESEL = "BIODIESEL"
    BIOMETANO = "BIOMETANO"

    @classmethod
    def from_str(cls, value: Any) -> BiofuelType:
        if isinstance(value, cls):
            return value
        norm = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
        if norm in cls.__members__:
            return cls[norm]
        raise CbioValidationError(f"Tipo de biocombustível inválido: '{value}'.")


class CbioBatchStatus(str, Enum):
    """Status do lote de CBIOs na custódia e escrituração."""
    EM_CIRCULACAO = "EM_CIRCULACAO"
    PARCIALMENTE_APOSENTADO = "PARCIALMENTE_APOSENTADO"
    APOSENTADO = "APOSENTADO"
    CANCELADO = "CANCELADO"


class EnergySource(str, Enum):
    """Fontes de energia elétrica comercializadas no ACL."""
    CONV = "CONV"    # Convencional
    I50 = "I50"      # Incentivada com 50% de desconto no fio (TUSD/TUST)
    I100 = "I100"    # Incentivada com 100% de desconto no fio


class SinSubmarket(str, Enum):
    """Submercados de energia do Sistema Interligado Nacional (SIN)."""
    SE_CO = "SE_CO"  # Sudeste / Centro-Oeste
    S = "S"          # Sul
    NE = "NE"        # Nordeste
    N = "N"          # Norte


# ---------------------------------------------------------------------------
# Modelos de Dados: Cédula de Produto Rural (CPR Digital)
# ---------------------------------------------------------------------------

@dataclass
class Party:
    """Parte integrante de título ou contrato (emitente, credor, comprador, vendedor)."""
    name: str
    document: str
    party_type: str = "PJ"
    city: str = ""
    state: str = ""

    def __post_init__(self) -> None:
        self.document = validate_document(self.document)
        self.party_type = self.party_type.upper()
        if self.state and self.state.upper() not in BRAZILIAN_UFS:
            raise CprValidationError(f"Estado/UF inválido: '{self.state}'.")
        self.state = self.state.upper()


@dataclass
class DeliveryLocation:
    """Local de entrega física da safra (Armazém Geral ou Terminal Graneleiro)."""
    warehouse_name: str
    address: str
    city: str
    state: str
    ibge_code: str
    warehouse_cnpj: str
    delivery_start_date: str
    delivery_end_date: str

    def __post_init__(self) -> None:
        self.warehouse_cnpj = validate_document(self.warehouse_cnpj)
        if self.state.upper() not in BRAZILIAN_UFS:
            raise CprValidationError(f"Estado do armazém inválido: '{self.state}'.")
        self.state = self.state.upper()


@dataclass
class CprGuarantee:
    """Averbação de garantia real ou afetação patrimonial vinculada à CPR."""
    guarantee_id: str
    guarantee_type: GuaranteeType
    description: str
    guarantee_value: Decimal
    registry_office: str
    registration_number: str
    car_code: Optional[str] = None
    sigef_parcel_code: Optional[str] = None
    affected_area_ha: Optional[Decimal] = None
    registered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        self.guarantee_value = round_cents(self.guarantee_value)
        if self.guarantee_value <= Decimal("0.00"):
            raise GuaranteeValidationError("Valor da garantia averbada deve ser estritamente positivo.")
        if self.car_code:
            norm_car = self.car_code.strip()
            if not CAR_REGEX.match(norm_car):
                raise GuaranteeValidationError(f"Código CAR/SICAR com formato inválido: '{self.car_code}'.")
            self.car_code = norm_car
        if self.affected_area_ha is not None:
            self.affected_area_ha = round_qty(self.affected_area_ha)
            if self.affected_area_ha <= Decimal("0.00"):
                raise GuaranteeValidationError("Área afetada em hectares deve ser positiva.")


# Alias semântico para a legislação brasileira
AverbaçãoGarantia = CprGuarantee


@dataclass
class CprFraction:
    """Fração eletrônica resultante de desdobramento escritural da CPR (LAW-044)."""
    fraction_id: str
    parent_cpr_id: str
    fraction_index: int
    total_fractions: int
    quantity: Decimal
    unit: MeasurementUnit
    notional_amount: Decimal
    creditor: Party
    allocated_guarantee_value: Decimal
    percentage_share: Decimal
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        self.quantity = round_qty(self.quantity)
        self.notional_amount = round_cents(self.notional_amount)
        self.allocated_guarantee_value = round_cents(self.allocated_guarantee_value)
        self.percentage_share = round_cents(self.percentage_share)


@dataclass
class DigitalCpr:
    """Registro escritural de CPR Física ou Financeira (Lei nº 8.929/94 e 13.986/20)."""
    cpr_id: str
    cpr_type: CprType
    issuer: Party
    creditor: Party
    commodity: CommodityType
    quantity: Decimal
    unit: MeasurementUnit
    crop_year: str
    issue_date: str
    maturity_date: str
    estimated_harvest_value: Decimal
    notional_amount: Decimal
    unit_price: Optional[Decimal] = None
    price_index: Optional[str] = None
    spread_percentage: Decimal = Decimal("0.00")
    interest_rate_annual: Decimal = Decimal("0.00")
    delivery_location: Optional[DeliveryLocation] = None
    guarantees: List[CprGuarantee] = field(default_factory=list)
    fractions: List[CprFraction] = field(default_factory=list)
    status: CprStatus = CprStatus.ACTIVE
    registered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    electronic_signature_hash: str = ""

    def __post_init__(self) -> None:
        self.quantity = round_qty(self.quantity)
        if self.quantity <= Decimal("0.00"):
            raise CprValidationError("Quantidade da commodity na CPR deve ser estritamente positiva.")
        self.estimated_harvest_value = round_cents(self.estimated_harvest_value)
        if self.estimated_harvest_value <= Decimal("0.00"):
            raise CprValidationError("Valor estimado do lastro físico da safra deve ser positivo.")
        self.notional_amount = round_cents(self.notional_amount)
        if self.notional_amount <= Decimal("0.00"):
            raise CprValidationError("Valor nocional da CPR deve ser estritamente positivo.")

        if not CROP_YEAR_REGEX.match(self.crop_year.strip()):
            raise CprValidationError(f"Padrão de ano-safra inválido: '{self.crop_year}'. Use YYYY ou YYYY/YYYY.")

        if self.cpr_type == CprType.FISICA and self.delivery_location is None:
            raise CprValidationError("CPR Física exige especificação de armazém/local de entrega.")

        if not self.electronic_signature_hash:
            seed = f"{self.cpr_id}|{self.issuer.document}|{self.creditor.document}|{self.commodity.value}|{self.quantity}|{self.notional_amount}"
            self.electronic_signature_hash = sha256_digest(seed)

    @property
    def total_guarantees_value(self) -> Decimal:
        """Soma total do valor das garantias averbadas ativas."""
        return sum((g.guarantee_value for g in self.guarantees if g.status == "ACTIVE"), Decimal("0.00"))


# ---------------------------------------------------------------------------
# Modelos de Dados: Créditos de Descarbonização (CBIO / RenovaBio)
# ---------------------------------------------------------------------------

@dataclass
class CbioBatch:
    """Lote escriturado de CBIO emitido no Balcão B3 NoMe (Lei nº 13.576/2017)."""
    batch_id: str
    issuer_id: str
    issuer_name: str
    issuer_cnpj: str
    bookkeeper_id: str
    bookkeeper_name: str
    bookkeeper_cnpj: str
    anp_certificate_code: str
    nfe_key: str
    biofuel_type: BiofuelType
    energy_efficiency_score: Decimal
    total_quantity: int
    circulating_quantity: int
    retired_quantity: int
    unit_price_brl: Decimal
    current_owner_cnpj: str
    current_owner_name: str
    issuance_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: CbioBatchStatus = CbioBatchStatus.EM_CIRCULACAO

    def __post_init__(self) -> None:
        self.issuer_cnpj = validate_document(self.issuer_cnpj)
        self.bookkeeper_cnpj = validate_document(self.bookkeeper_cnpj)
        self.current_owner_cnpj = validate_document(self.current_owner_cnpj)
        if not NFE_KEY_REGEX.match(self.nfe_key):
            raise CbioValidationError(f"Chave de NF-e inválida: '{self.nfe_key}'. Deve conter exatamente 44 dígitos.")
        if self.total_quantity <= 0:
            raise CbioValidationError("Quantidade de CBIOs no lote deve ser maior que zero.")
        if self.circulating_quantity < 0 or self.retired_quantity < 0:
            raise CbioValidationError("Quantidades em circulação e aposentadas não podem ser negativas.")
        if (self.circulating_quantity + self.retired_quantity) != self.total_quantity:
            raise CbioValidationError(
                f"Conservação violada: total ({self.total_quantity}) != circulação ({self.circulating_quantity}) + aposentados ({self.retired_quantity})"
            )
        self.unit_price_brl = round_cents(self.unit_price_brl)
        self.energy_efficiency_score = round_cents(self.energy_efficiency_score)


@dataclass
class CbioTradeRecord:
    """Registro de transferência/negociação de CBIO no mercado secundário da B3."""
    trade_id: str
    batch_id: str
    seller_cnpj: str
    buyer_cnpj: str
    quantity: int
    unit_price_brl: Decimal
    total_amount_brl: Decimal
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        self.seller_cnpj = validate_document(self.seller_cnpj)
        self.buyer_cnpj = validate_document(self.buyer_cnpj)
        if self.quantity <= 0:
            raise CbioTransferError("Quantidade negociada deve ser estritamente positiva.")
        self.unit_price_brl = round_cents(self.unit_price_brl)
        self.total_amount_brl = round_cents(self.total_amount_brl)


@dataclass
class CbioRetirementCertificate:
    """Certidão de Aposentadoria Definitiva de CBIO emitida pela B3 (Cancelamento de Lastro)."""
    certificate_id: str
    obligated_party_cnpj: str
    obligated_party_name: str
    compliance_year: int
    quantity_retired: int
    tco2e_avoided: Decimal
    batch_ids: List[str]
    certificate_hash: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        self.obligated_party_cnpj = validate_document(self.obligated_party_cnpj)
        self.tco2e_avoided = round_cents(self.tco2e_avoided)
        if not self.certificate_hash:
            seed = f"{self.certificate_id}|{self.obligated_party_cnpj}|{self.compliance_year}|{self.quantity_retired}"
            self.certificate_hash = sha256_digest(seed)


@dataclass
class AnpComplianceReport:
    """Relatório oficial de apuração de cumprimento de meta compulsória ANP."""
    obligated_party_cnpj: str
    obligated_party_name: str
    compliance_year: int
    annual_target_cbios: int
    retired_cbios: int
    compliance_percentage: Decimal
    is_fully_compliant: bool
    carry_over_eligible: bool
    carry_over_quantity: int
    deficit_quantity: int
    penalty_per_cbio_brl: Decimal
    total_penalty_brl: Decimal

    def __post_init__(self) -> None:
        self.compliance_percentage = round_cents(self.compliance_percentage)
        self.penalty_per_cbio_brl = round_cents(self.penalty_per_cbio_brl)
        self.total_penalty_brl = round_cents(self.total_penalty_brl)


# ---------------------------------------------------------------------------
# Modelos de Dados: Contratos de Energia Elétrica (CCEE / Aneel - ACL)
# ---------------------------------------------------------------------------

@dataclass
class BilateralPowerContract:
    """Contrato Bilateral de Compra e Venda de Energia no ACL (Sistema NoMe / CCEE)."""
    contract_id: str
    ccee_contract_code: str
    buyer_id: str
    buyer_name: str
    seller_id: str
    seller_name: str
    energy_source: EnergySource
    submarket: SinSubmarket
    start_date: str
    end_date: str
    mw_average: Decimal
    total_hours: int
    volume_mwh: Decimal
    price_per_mwh: Decimal
    notional_amount: Decimal
    fiduciary_lock_amount: Decimal = Decimal("0.00")
    fiduciary_creditor_id: Optional[str] = None
    status: str = "ACTIVE"
    registered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        self.mw_average = round_energy(self.mw_average)
        if self.mw_average <= Decimal("0.0000"):
            raise PowerContractValidationError("Potência média (MWmédio) deve ser positiva.")
        if self.total_hours <= 0:
            raise PowerContractValidationError("Número de horas de suprimento deve ser maior que zero.")
        self.volume_mwh = round_energy(self.volume_mwh)
        self.price_per_mwh = round_cents(self.price_per_mwh)
        if self.price_per_mwh <= Decimal("0.00"):
            raise PowerContractValidationError("Preço da energia deve ser positivo.")
        self.notional_amount = round_cents(self.notional_amount)
        self.fiduciary_lock_amount = round_cents(self.fiduciary_lock_amount)


@dataclass
class PowerBalanceReport:
    """Balanço físico e contratual de energia apurado por participante e submercado."""
    participant_id: str
    submarket: SinSubmarket
    period: str
    measured_generation_mwh: Decimal
    contract_purchases_mwh: Decimal
    measured_consumption_mwh: Decimal
    contract_sales_mwh: Decimal
    net_balance_mwh: Decimal
    exposure_type: str  # "SURPLUS", "DEFICIT", "BALANCED"

    def __post_init__(self) -> None:
        self.measured_generation_mwh = round_energy(self.measured_generation_mwh)
        self.contract_purchases_mwh = round_energy(self.contract_purchases_mwh)
        self.measured_consumption_mwh = round_energy(self.measured_consumption_mwh)
        self.contract_sales_mwh = round_energy(self.contract_sales_mwh)
        self.net_balance_mwh = round_energy(self.net_balance_mwh)


@dataclass
class CceeMcpSettlementBatch:
    """Lote de liquidação multilateral de diferenças no Mercado de Curto Prazo CCEE."""
    batch_id: str
    period: str
    submarket: SinSubmarket
    pld_applied: Decimal
    participant_settlements: Dict[str, Dict[str, Any]]
    total_credits_brl: Decimal
    total_debits_brl: Decimal
    net_clearing_discrepancy_brl: Decimal
    settled_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Motor Unificado: AgroGreenFinanceEngine
# ---------------------------------------------------------------------------

class AgroGreenFinanceEngine:
    """Motor Unificado de Registro de Agronegócio e Finanças Verdes B3."""

    def __init__(self) -> None:
        # CPR Digital
        self.cprs: Dict[str, DigitalCpr] = {}
        # CBIOs RenovaBio
        self.cbio_batches: Dict[str, CbioBatch] = {}
        self.cbio_trades: List[CbioTradeRecord] = []
        self.cbio_retirements: List[CbioRetirementCertificate] = []
        # Energia Elétrica ACL / CCEE
        self.power_contracts: Dict[str, BilateralPowerContract] = {}
        self.ccee_settlement_history: List[CceeMcpSettlementBatch] = []

    # =========================================================================
    # SUBSISTEMA 1: Cédula de Produto Rural (CPR Digital / Nova Lei do Agro)
    # =========================================================================

    def register_cpr(
        self,
        cpr_id: str,
        cpr_type: Union[CprType, str],
        issuer: Union[Party, Dict[str, Any]],
        creditor: Union[Party, Dict[str, Any]],
        commodity: Union[CommodityType, str],
        quantity: Union[Decimal, int, float, str],
        unit: Union[MeasurementUnit, str],
        crop_year: str,
        issue_date: str,
        maturity_date: str,
        estimated_harvest_value: Union[Decimal, int, float, str],
        notional_amount: Optional[Union[Decimal, int, float, str]] = None,
        unit_price: Optional[Union[Decimal, int, float, str]] = None,
        price_index: Optional[str] = None,
        spread_percentage: Union[Decimal, int, float, str] = Decimal("0.00"),
        interest_rate_annual: Union[Decimal, int, float, str] = Decimal("0.00"),
        delivery_location: Optional[Union[DeliveryLocation, Dict[str, Any]]] = None,
        guarantees: Optional[List[Union[CprGuarantee, Dict[str, Any]]]] = None,
    ) -> DigitalCpr:
        """Registra uma nova CPR Física ou Financeira no livro escritural NoMe Agro."""
        if cpr_id in self.cprs:
            raise CprValidationError(f"CPR com ID '{cpr_id}' já está registrada.")

        resolved_type = CprType.from_str(cpr_type)
        resolved_commodity = CommodityType.from_str(commodity)
        resolved_unit = MeasurementUnit.from_str(unit)

        party_issuer = issuer if isinstance(issuer, Party) else Party(**issuer)
        party_creditor = creditor if isinstance(creditor, Party) else Party(**creditor)

        dec_qty = round_qty(quantity)
        dec_harvest_val = round_cents(estimated_harvest_value)
        dec_unit_price = round_cents(unit_price) if unit_price is not None else None

        if notional_amount is not None:
            dec_notional = round_cents(notional_amount)
        elif dec_unit_price is not None:
            dec_notional = round_cents(dec_qty * dec_unit_price)
        else:
            dec_notional = dec_harvest_val

        deliv_obj: Optional[DeliveryLocation] = None
        if delivery_location is not None:
            deliv_obj = (
                delivery_location
                if isinstance(delivery_location, DeliveryLocation)
                else DeliveryLocation(**delivery_location)
            )

        parsed_guarantees: List[CprGuarantee] = []
        if guarantees:
            for g in guarantees:
                if isinstance(g, CprGuarantee):
                    parsed_guarantees.append(g)
                else:
                    g_data = dict(g)
                    g_data["guarantee_type"] = GuaranteeType.from_str(g_data["guarantee_type"])
                    parsed_guarantees.append(CprGuarantee(**g_data))

        cpr = DigitalCpr(
            cpr_id=cpr_id,
            cpr_type=resolved_type,
            issuer=party_issuer,
            creditor=party_creditor,
            commodity=resolved_commodity,
            quantity=dec_qty,
            unit=resolved_unit,
            crop_year=crop_year,
            issue_date=issue_date,
            maturity_date=maturity_date,
            estimated_harvest_value=dec_harvest_val,
            notional_amount=dec_notional,
            unit_price=dec_unit_price,
            price_index=price_index,
            spread_percentage=round_cents(spread_percentage),
            interest_rate_annual=round_cents(interest_rate_annual),
            delivery_location=deliv_obj,
            guarantees=parsed_guarantees,
            status=CprStatus.ACTIVE,
        )

        # Invariante LAW-065: Soma de garantias não pode exceder o valor avaliado do lastro
        if cpr.total_guarantees_value > cpr.estimated_harvest_value:
            raise GuaranteeValidationError(
                f"Violação de LAW-065: Valor total de garantias ({cpr.total_guarantees_value}) "
                f"excede o lastro avaliado da safra ({cpr.estimated_harvest_value})."
            )

        self.cprs[cpr_id] = cpr
        return cpr

    def averbar_garantia(
        self,
        cpr_id: str,
        guarantee_id: str,
        guarantee_type: Union[GuaranteeType, str],
        description: str,
        guarantee_value: Union[Decimal, int, float, str],
        registry_office: str,
        registration_number: str,
        car_code: Optional[str] = None,
        sigef_parcel_code: Optional[str] = None,
        affected_area_ha: Optional[Union[Decimal, int, float, str]] = None,
    ) -> CprGuarantee:
        """Averba uma garantia real ou patrimônio de afetação à CPR registrada (LAW-065)."""
        cpr = self._get_cpr(cpr_id)
        if cpr.status != CprStatus.ACTIVE:
            raise GuaranteeValidationError(f"Não é permitido averbar garantia em CPR com status '{cpr.status.value}'.")

        resolved_type = GuaranteeType.from_str(guarantee_type)
        dec_val = round_cents(guarantee_value)
        dec_area = round_qty(affected_area_ha) if affected_area_ha is not None else None

        # Validação específica da Lei nº 13.986/2020 para Patrimônio Rural em Afetação
        if resolved_type == GuaranteeType.PATRIMONIO_RURAL_AFETACAO:
            if not sigef_parcel_code:
                raise GuaranteeValidationError("Patrimônio de afetação rural exige código de certificação SIGEF/INCRA.")
            if not car_code:
                raise GuaranteeValidationError("Patrimônio de afetação rural exige cadastro SICAR/CAR regular.")

        # Invariante Formal LAW-065: Limite Fiduciário sobre o Lastro da Safra / Bem Afetado
        projected_guarantees = cpr.total_guarantees_value + dec_val
        check(
            projected_guarantees <= cpr.estimated_harvest_value,
            f"LAW-065: Garantia averbada de R$ {dec_val} faria a soma ({projected_guarantees}) "
            f"exceder o lastro avaliado da safra (R$ {cpr.estimated_harvest_value}).",
            law_code="LAW-065",
            details={
                "cpr_id": cpr_id,
                "projected_guarantees": str(projected_guarantees),
                "estimated_harvest_value": str(cpr.estimated_harvest_value),
            },
        )

        guarantee = CprGuarantee(
            guarantee_id=guarantee_id,
            guarantee_type=resolved_type,
            description=description,
            guarantee_value=dec_val,
            registry_office=registry_office,
            registration_number=registration_number,
            car_code=car_code,
            sigef_parcel_code=sigef_parcel_code,
            affected_area_ha=dec_area,
        )

        cpr.guarantees.append(guarantee)
        return guarantee

    def fracionar_cpr(
        self,
        cpr_id: str,
        fractions_spec: List[Dict[str, Any]],
    ) -> List[CprFraction]:
        """Executa o fracionamento eletrônico determinístico da CPR (LAW-044, LAW-048, LAW-066).

        Cada fração recebe cota proporcional exata da quantidade física, valor financeiro e garantia,
        garantindo soma exata e congelando irrevogavelmente a CPR original.
        """
        cpr = self._get_cpr(cpr_id)

        # Invariante LAW-048: CPR já fracionada é congelada e não pode sofrer novo fracionamento
        if cpr.status == CprStatus.FRACTIONATED:
            raise CprFractionationError(
                f"Violação de LAW-048: CPR '{cpr_id}' já se encontra fracionada e congelada no livro escritural."
            )
        if cpr.status != CprStatus.ACTIVE:
            raise CprFractionationError(
                f"CPR '{cpr_id}' com status '{cpr.status.value}' não é elegível para fracionamento."
            )
        if len(fractions_spec) < 2:
            raise CprFractionationError("Fracionamento exige no mínimo 2 frações.")

        total_fractions = len(fractions_spec)
        created_fractions: List[CprFraction] = []

        total_spec_quantity = Decimal("0.0000")
        total_spec_notional = Decimal("0.00")
        total_guarantees = cpr.total_guarantees_value

        for i, spec in enumerate(fractions_spec, start=1):
            f_qty = round_qty(spec.get("quantity", Decimal("0.0000")))
            f_notional = round_cents(spec.get("notional_amount", Decimal("0.00")))
            total_spec_quantity += f_qty
            total_spec_notional += f_notional

        # Invariante LAW-044 & LAW-066: Fechamento exato de quantidades físicas e financeiras
        check(
            total_spec_quantity == cpr.quantity,
            f"LAW-044: Soma das quantidades fracionadas ({total_spec_quantity}) != CPR original ({cpr.quantity}).",
            law_code="LAW-044",
            details={"parent_quantity": str(cpr.quantity), "fractions_quantity": str(total_spec_quantity)},
        )
        check(
            total_spec_notional == cpr.notional_amount,
            f"LAW-066: Soma dos valores nocionais fracionados ({total_spec_notional}) != CPR original ({cpr.notional_amount}).",
            law_code="LAW-066",
            details={"parent_notional": str(cpr.notional_amount), "fractions_notional": str(total_spec_notional)},
        )

        # Rateio proporcional determinístico de garantias com ajuste no último lote
        allocated_guarantees_sum = Decimal("0.00")
        for i, spec in enumerate(fractions_spec, start=1):
            fraction_id = spec.get("fraction_id", f"{cpr.cpr_id}-F{i:02d}")
            f_qty = round_qty(spec["quantity"])
            f_notional = round_cents(spec["notional_amount"])
            f_creditor_data = spec["creditor"]
            f_creditor = (
                f_creditor_data if isinstance(f_creditor_data, Party) else Party(**f_creditor_data)
            )

            # Parcela percentual
            share_pct = round_cents((f_qty / cpr.quantity) * Decimal("100.00"))

            if i == total_fractions:
                # Último lote absorve qualquer resíduo de arredondamento de garantia
                f_guar = total_guarantees - allocated_guarantees_sum
            else:
                f_guar = round_cents((f_qty / cpr.quantity) * total_guarantees)
                allocated_guarantees_sum += f_guar

            frac = CprFraction(
                fraction_id=fraction_id,
                parent_cpr_id=cpr.cpr_id,
                fraction_index=i,
                total_fractions=total_fractions,
                quantity=f_qty,
                unit=cpr.unit,
                notional_amount=f_notional,
                creditor=f_creditor,
                allocated_guarantee_value=f_guar,
                percentage_share=share_pct,
            )
            created_fractions.append(frac)

        # Transição de estado com congelamento da CPR-mãe (LAW-048)
        cpr.fractions = created_fractions
        cpr.status = CprStatus.FRACTIONATED

        return created_fractions

    # =========================================================================
    # SUBSISTEMA 2: Créditos de Descarbonização (CBIO / RenovaBio - Lei 13.576/17)
    # =========================================================================

    def issue_cbio_batch(
        self,
        batch_id: str,
        issuer_id: str,
        issuer_name: str,
        issuer_cnpj: str,
        bookkeeper_id: str,
        bookkeeper_name: str,
        bookkeeper_cnpj: str,
        anp_certificate_code: str,
        nfe_key: str,
        biofuel_type: Union[BiofuelType, str],
        energy_efficiency_score: Union[Decimal, int, float, str],
        quantity: int,
        unit_price_brl: Union[Decimal, int, float, str],
        issuance_timestamp: Optional[str] = None,
    ) -> CbioBatch:
        """Escrituração primária de lote de CBIOs na B3."""
        if batch_id in self.cbio_batches:
            raise CbioValidationError(f"Lote de CBIO '{batch_id}' já escriturado.")

        resolved_biofuel = BiofuelType.from_str(biofuel_type)

        batch = CbioBatch(
            batch_id=batch_id,
            issuer_id=issuer_id,
            issuer_name=issuer_name,
            issuer_cnpj=issuer_cnpj,
            bookkeeper_id=bookkeeper_id,
            bookkeeper_name=bookkeeper_name,
            bookkeeper_cnpj=bookkeeper_cnpj,
            anp_certificate_code=anp_certificate_code,
            nfe_key=nfe_key,
            biofuel_type=resolved_biofuel,
            energy_efficiency_score=Decimal(str(energy_efficiency_score)),
            total_quantity=quantity,
            circulating_quantity=quantity,
            retired_quantity=0,
            unit_price_brl=Decimal(str(unit_price_brl)),
            current_owner_cnpj=issuer_cnpj,
            current_owner_name=issuer_name,
            issuance_timestamp=issuance_timestamp or datetime.now(timezone.utc).isoformat(),
            status=CbioBatchStatus.EM_CIRCULACAO,
        )

        self.cbio_batches[batch_id] = batch
        return batch

    def transfer_cbio(
        self,
        trade_id: str,
        batch_id: str,
        seller_cnpj: str,
        buyer_cnpj: str,
        buyer_name: str,
        quantity: int,
        unit_price_brl: Union[Decimal, int, float, str],
    ) -> CbioTradeRecord:
        """Executa transferência de custódia de CBIO no mercado secundário sem dupla contagem."""
        batch = self._get_cbio_batch(batch_id)

        norm_seller = validate_document(seller_cnpj)
        norm_buyer = validate_document(buyer_cnpj)

        if batch.current_owner_cnpj != norm_seller:
            raise CbioTransferError(
                f"Vendedor '{norm_seller}' não é o titular custodiante do lote '{batch_id}' (Titular: '{batch.current_owner_cnpj}')."
            )

        if batch.status == CbioBatchStatus.APOSENTADO:
            # Violação de LAW-048
            raise CbioTransferError(
                f"Violação de LAW-048: Lote de CBIOs '{batch_id}' já está totalmente aposentado e bloqueado para negociação."
            )

        if quantity > batch.circulating_quantity:
            raise CbioTransferError(
                f"Saldo insuficiente: quantidade solicitada ({quantity}) excede o saldo em circulação ({batch.circulating_quantity})."
            )

        dec_price = round_cents(unit_price_brl)
        total_amount = round_cents(Decimal(quantity) * dec_price)

        # Atualização atômica de titularidade
        if quantity == batch.circulating_quantity:
            batch.current_owner_cnpj = norm_buyer
            batch.current_owner_name = buyer_name
            batch.unit_price_brl = dec_price
        else:
            # Desmembramento em novo sublote para o comprador mantendo conservação
            batch.circulating_quantity -= quantity
            batch.total_quantity -= quantity
            sub_id = f"{batch_id}-TR-{len(self.cbio_trades) + 1:03d}"
            sub_batch = CbioBatch(
                batch_id=sub_id,
                issuer_id=batch.issuer_id,
                issuer_name=batch.issuer_name,
                issuer_cnpj=batch.issuer_cnpj,
                bookkeeper_id=batch.bookkeeper_id,
                bookkeeper_name=batch.bookkeeper_name,
                bookkeeper_cnpj=batch.bookkeeper_cnpj,
                anp_certificate_code=batch.anp_certificate_code,
                nfe_key=batch.nfe_key,
                biofuel_type=batch.biofuel_type,
                energy_efficiency_score=batch.energy_efficiency_score,
                total_quantity=quantity,
                circulating_quantity=quantity,
                retired_quantity=0,
                unit_price_brl=dec_price,
                current_owner_cnpj=norm_buyer,
                current_owner_name=buyer_name,
                issuance_timestamp=batch.issuance_timestamp,
                status=CbioBatchStatus.EM_CIRCULACAO,
            )
            self.cbio_batches[sub_id] = sub_batch

        trade = CbioTradeRecord(
            trade_id=trade_id,
            batch_id=batch_id,
            seller_cnpj=norm_seller,
            buyer_cnpj=norm_buyer,
            quantity=quantity,
            unit_price_brl=dec_price,
            total_amount_brl=total_amount,
        )
        self.cbio_trades.append(trade)
        return trade

    def retire_cbios_compulsory(
        self,
        certificate_id: str,
        obligated_party_cnpj: str,
        obligated_party_name: str,
        compliance_year: int,
        batch_retirements: List[Dict[str, Any]],
    ) -> CbioRetirementCertificate:
        """Aposentadoria compulsória definitiva de CBIOs para atendimento de meta ANP (LAW-048)."""
        norm_obligated = validate_document(obligated_party_cnpj)
        total_retired_in_cert = 0
        retired_batch_ids: List[str] = []

        for item in batch_retirements:
            b_id = item["batch_id"]
            qty = int(item["quantity"])
            if qty <= 0:
                raise CbioRetirementError("Quantidade para aposentadoria deve ser maior que zero.")

            batch = self._get_cbio_batch(b_id)

            # Invariante LAW-048: Bloqueio estrito de re-aposentadoria ou uso de lote aposentado
            if batch.status == CbioBatchStatus.APOSENTADO:
                raise CbioRetirementError(
                    f"Violação de LAW-048: Lote '{b_id}' já se encontra totalmente aposentado e cancelado."
                )

            if batch.current_owner_cnpj != norm_obligated:
                raise CbioRetirementError(
                    f"Distribuidor '{norm_obligated}' não é o proprietário custodiante do lote '{b_id}'."
                )

            if qty > batch.circulating_quantity:
                raise CbioRetirementError(
                    f"Quantidade para aposentadoria ({qty}) excede o saldo em circulação ({batch.circulating_quantity}) do lote '{b_id}'."
                )

            # Executa baixa irrevogável
            batch.circulating_quantity -= qty
            batch.retired_quantity += qty
            if batch.circulating_quantity == 0:
                batch.status = CbioBatchStatus.APOSENTADO
            else:
                batch.status = CbioBatchStatus.PARCIALMENTE_APOSENTADO

            total_retired_in_cert += qty
            retired_batch_ids.append(b_id)

        tco2e = Decimal(total_retired_in_cert) * TCO2E_PER_CBIO

        cert = CbioRetirementCertificate(
            certificate_id=certificate_id,
            obligated_party_cnpj=norm_obligated,
            obligated_party_name=obligated_party_name,
            compliance_year=compliance_year,
            quantity_retired=total_retired_in_cert,
            tco2e_avoided=tco2e,
            batch_ids=retired_batch_ids,
            certificate_hash="",
        )
        self.cbio_retirements.append(cert)
        return cert

    def calculate_anp_compliance(
        self,
        obligated_party_cnpj: str,
        obligated_party_name: str,
        compliance_year: int,
        annual_target_cbios: int,
        penalty_per_cbio: Union[Decimal, int, float, str] = DEFAULT_PENALTY_PER_CBIO_BRL,
    ) -> AnpComplianceReport:
        """Gera o relatório de conformidade da meta compulsória ANP com cálculo de carry-over."""
        norm_cnpj = validate_document(obligated_party_cnpj)
        total_retired = sum(
            c.quantity_retired
            for c in self.cbio_retirements
            if c.obligated_party_cnpj == norm_cnpj and c.compliance_year == compliance_year
        )

        dec_target = Decimal(annual_target_cbios)
        dec_penalty_unit = round_cents(penalty_per_cbio)

        if dec_target > Decimal("0"):
            compliance_pct = round_cents((Decimal(total_retired) / dec_target) * Decimal("100.00"))
        else:
            compliance_pct = Decimal("100.00")

        is_compliant = total_retired >= annual_target_cbios
        deficit = max(0, annual_target_cbios - total_retired)

        # Regra RenovaBio: se atingiu >= 85%, até 15% de déficit pode ser carry-over para o ano seguinte
        carry_over_eligible = False
        carry_over_qty = 0
        penalized_deficit = deficit

        if not is_compliant and compliance_pct >= ANP_CARRYOVER_MIN_COMPLIANCE_PCT:
            carry_over_eligible = True
            max_carry_over = int(dec_target * (ANP_CARRYOVER_MAX_PCT / Decimal("100.00")))
            carry_over_qty = min(deficit, max_carry_over)
            penalized_deficit = max(0, deficit - carry_over_qty)

        total_penalty = round_cents(Decimal(penalized_deficit) * dec_penalty_unit)

        return AnpComplianceReport(
            obligated_party_cnpj=norm_cnpj,
            obligated_party_name=obligated_party_name,
            compliance_year=compliance_year,
            annual_target_cbios=annual_target_cbios,
            retired_cbios=total_retired,
            compliance_percentage=compliance_pct,
            is_fully_compliant=is_compliant,
            carry_over_eligible=carry_over_eligible,
            carry_over_quantity=carry_over_qty,
            deficit_quantity=deficit,
            penalty_per_cbio_brl=dec_penalty_unit,
            total_penalty_brl=total_penalty,
        )

    # =========================================================================
    # SUBSISTEMA 3: Contratos Bilaterais de Energia Elétrica (CCEE / Aneel - ACL)
    # =========================================================================

    def register_power_contract(
        self,
        contract_id: str,
        ccee_contract_code: str,
        buyer_id: str,
        buyer_name: str,
        seller_id: str,
        seller_name: str,
        energy_source: Union[EnergySource, str],
        submarket: Union[SinSubmarket, str],
        start_date: str,
        end_date: str,
        mw_average: Union[Decimal, int, float, str],
        total_hours: int,
        price_per_mwh: Union[Decimal, int, float, str],
        fiduciary_lock_amount: Union[Decimal, int, float, str] = Decimal("0.00"),
        fiduciary_creditor_id: Optional[str] = None,
    ) -> BilateralPowerContract:
        """Registra contrato bilateral de compra e venda de energia no ACL."""
        if contract_id in self.power_contracts:
            raise PowerContractValidationError(f"Contrato de energia '{contract_id}' já registrado.")

        resolved_source = EnergySource(energy_source) if isinstance(energy_source, str) else energy_source
        resolved_submarket = SinSubmarket(submarket) if isinstance(submarket, str) else submarket

        dec_mw = round_energy(mw_average)
        volume_mwh = round_energy(dec_mw * Decimal(total_hours))
        dec_price = round_cents(price_per_mwh)
        notional = round_cents(volume_mwh * dec_price)
        dec_lock = round_cents(fiduciary_lock_amount)

        # Invariante LAW-065: Trava fiduciária de recebíveis não pode exceder o valor nocional
        check(
            dec_lock <= notional,
            f"LAW-065: Trava fiduciária ({dec_lock}) excede o valor total nocional do contrato ({notional}).",
            law_code="LAW-065",
            details={"contract_id": contract_id, "lock_amount": str(dec_lock), "notional": str(notional)},
        )

        contract = BilateralPowerContract(
            contract_id=contract_id,
            ccee_contract_code=ccee_contract_code,
            buyer_id=buyer_id,
            buyer_name=buyer_name,
            seller_id=seller_id,
            seller_name=seller_name,
            energy_source=resolved_source,
            submarket=resolved_submarket,
            start_date=start_date,
            end_date=end_date,
            mw_average=dec_mw,
            total_hours=total_hours,
            volume_mwh=volume_mwh,
            price_per_mwh=dec_price,
            notional_amount=notional,
            fiduciary_lock_amount=dec_lock,
            fiduciary_creditor_id=fiduciary_creditor_id,
            status="ACTIVE",
        )

        self.power_contracts[contract_id] = contract
        return contract

    def calculate_power_balance(
        self,
        participant_id: str,
        submarket: Union[SinSubmarket, str],
        period: str,
        measured_generation_mwh: Union[Decimal, int, float, str],
        measured_consumption_mwh: Union[Decimal, int, float, str],
    ) -> PowerBalanceReport:
        """Calcula o balanço de potência e energia do participante no submercado (CCEE)."""
        resolved_submarket = SinSubmarket(submarket) if isinstance(submarket, str) else submarket
        dec_gen = round_energy(measured_generation_mwh)
        dec_cons = round_energy(measured_consumption_mwh)

        # Contratos de compra e venda ativos do participante
        purchases = Decimal("0.0000")
        sales = Decimal("0.0000")

        for ctr in self.power_contracts.values():
            if ctr.status != "ACTIVE" or ctr.submarket != resolved_submarket:
                continue
            if ctr.buyer_id == participant_id:
                purchases += ctr.volume_mwh
            if ctr.seller_id == participant_id:
                sales += ctr.volume_mwh

        # Equação de Balanço CCEE: (Geração + Compras) - (Consumo + Vendas)
        net_balance = (dec_gen + purchases) - (dec_cons + sales)

        if net_balance > Decimal("0.0000"):
            exp_type = "SURPLUS"
        elif net_balance < Decimal("0.0000"):
            exp_type = "DEFICIT"
        else:
            exp_type = "BALANCED"

        return PowerBalanceReport(
            participant_id=participant_id,
            submarket=resolved_submarket,
            period=period,
            measured_generation_mwh=dec_gen,
            contract_purchases_mwh=purchases,
            measured_consumption_mwh=dec_cons,
            contract_sales_mwh=sales,
            net_balance_mwh=net_balance,
            exposure_type=exp_type,
        )

    def settle_ccee_mcp_batch(
        self,
        batch_id: str,
        period: str,
        submarket: Union[SinSubmarket, str],
        pld_price: Union[Decimal, int, float, str],
        participants_measurements: Dict[str, Dict[str, Any]],
    ) -> CceeMcpSettlementBatch:
        """Executa a liquidação multilateral de diferenças do MCP CCEE ao PLD (LAW-066)."""
        resolved_submarket = SinSubmarket(submarket) if isinstance(submarket, str) else submarket
        dec_pld = round_cents(pld_price)

        # Validação regulatória de limites ANEEL para PLD
        if dec_pld < DEFAULT_PLD_MIN or dec_pld > DEFAULT_PLD_MAX:
            raise PowerSettlementError(
                f"PLD informado (R$ {dec_pld}) fora dos limites ANEEL (Min: R$ {DEFAULT_PLD_MIN}, Max: R$ {DEFAULT_PLD_MAX})."
            )

        settlements: Dict[str, Dict[str, Any]] = {}
        total_credits = Decimal("0.00")
        total_debits = Decimal("0.00")
        sum_net_adjustments = Decimal("0.00")

        for p_id, meas in participants_measurements.items():
            gen = meas.get("measured_generation_mwh", Decimal("0.0000"))
            cons = meas.get("measured_consumption_mwh", Decimal("0.0000"))
            balance = self.calculate_power_balance(p_id, resolved_submarket, period, gen, cons)

            financial_adj = round_cents(balance.net_balance_mwh * dec_pld)
            sum_net_adjustments += financial_adj

            if financial_adj > Decimal("0.00"):
                total_credits += financial_adj
            else:
                total_debits += abs(financial_adj)

            settlements[p_id] = {
                "net_balance_mwh": str(balance.net_balance_mwh),
                "exposure_type": balance.exposure_type,
                "financial_adjustment_brl": str(financial_adj),
            }

        # Invariante Formal LAW-066: Compensação Multilateral com Balanço Fechado Soma-Zero
        discrepancy = abs(sum_net_adjustments)
        check(
            discrepancy <= TWO_PLACES,
            f"LAW-066: Violação de conservação soma-zero no MCP CCEE: discrepância de R$ {sum_net_adjustments} != 0.00.",
            law_code="LAW-066",
            details={
                "batch_id": batch_id,
                "total_credits": str(total_credits),
                "total_debits": str(total_debits),
                "discrepancy": str(sum_net_adjustments),
            },
        )

        batch = CceeMcpSettlementBatch(
            batch_id=batch_id,
            period=period,
            submarket=resolved_submarket,
            pld_applied=dec_pld,
            participant_settlements=settlements,
            total_credits_brl=total_credits,
            total_debits_brl=total_debits,
            net_clearing_discrepancy_brl=sum_net_adjustments,
        )
        self.ccee_settlement_history.append(batch)
        return batch

    # =========================================================================
    # INVARIANTES FORMAIS: Auditoria Global de Leis (LAW-044, 048, 065, 066)
    # =========================================================================

    def assert_law_044_cpr_fractionation_conservation(self, cpr_id: str) -> None:
        """LAW-044: Conservação estrita de balanço físico e financeiro no fracionamento de CPR."""
        cpr = self._get_cpr(cpr_id)
        if cpr.status != CprStatus.FRACTIONATED:
            return

        sum_q = sum((f.quantity for f in cpr.fractions), Decimal("0.0000"))
        sum_v = sum((f.notional_amount for f in cpr.fractions), Decimal("0.00"))

        check(
            sum_q == cpr.quantity,
            f"LAW-044: Quantidade fracionada ({sum_q}) diverge da CPR matriz ({cpr.quantity}).",
            law_code="LAW-044",
        )
        check(
            sum_v == cpr.notional_amount,
            f"LAW-044: Valor nocional fracionado ({sum_v}) diverge da CPR matriz ({cpr.notional_amount}).",
            law_code="LAW-044",
        )

    def assert_law_048_irrevocability_and_freeze(self) -> None:
        """LAW-048: Inviolabilidade de Aposentadoria de CBIO e CPR Fracionada (Freeze Book)."""
        # Verifica CBIOs aposentados
        for b in self.cbio_batches.values():
            if b.status == CbioBatchStatus.APOSENTADO:
                check(
                    b.circulating_quantity == 0,
                    f"LAW-048: Lote aposentado '{b.batch_id}' possui saldo circulante não nulo ({b.circulating_quantity}).",
                    law_code="LAW-048",
                )
                check(
                    b.retired_quantity == b.total_quantity,
                    f"LAW-048: Lote aposentado '{b.batch_id}' com aposentadoria incompleta.",
                    law_code="LAW-048",
                )

        # Verifica CPRs fracionadas
        for c in self.cprs.values():
            if c.status == CprStatus.FRACTIONATED:
                check(
                    len(c.fractions) >= 2,
                    f"LAW-048: CPR fracionada '{c.cpr_id}' possui menos de 2 frações registradas.",
                    law_code="LAW-048",
                )

    def assert_law_065_collateral_under_harvest_bound(self) -> None:
        """LAW-065: Inviolabilidade de Gravames e Garantias sobre Lastro de Safra e Recebíveis."""
        for cpr in self.cprs.values():
            check(
                cpr.total_guarantees_value <= cpr.estimated_harvest_value,
                f"LAW-065: Garantias ({cpr.total_guarantees_value}) superam lastro avaliado ({cpr.estimated_harvest_value}) na CPR '{cpr.cpr_id}'.",
                law_code="LAW-065",
            )
        for ctr in self.power_contracts.values():
            check(
                ctr.fiduciary_lock_amount <= ctr.notional_amount,
                f"LAW-065: Trava fiduciária ({ctr.fiduciary_lock_amount}) supera o nocional ({ctr.notional_amount}) no contrato '{ctr.contract_id}'.",
                law_code="LAW-065",
            )

    def assert_law_066_multilateral_clearing_and_value_conservation(self) -> None:
        """LAW-066: Conservação de Valor Patrimonial e Balanço Fechado no MCP CCEE."""
        for batch in self.ccee_settlement_history:
            diff = abs(batch.net_clearing_discrepancy_brl)
            check(
                diff <= TWO_PLACES,
                f"LAW-066: Desbalanço multilateral no lote MCP '{batch.batch_id}': {batch.net_clearing_discrepancy_brl}.",
                law_code="LAW-066",
            )

    def verify_all_invariants(self) -> Dict[str, Any]:
        """Audita formalmente todas as leis invariantes do motor."""
        # 1. CPRs
        for cpr_id, cpr in self.cprs.items():
            if cpr.status == CprStatus.FRACTIONATED:
                self.assert_law_044_cpr_fractionation_conservation(cpr_id)

        # 2. Freeze / Aposentadoria
        self.assert_law_048_irrevocability_and_freeze()

        # 3. Garantias e Lastros
        self.assert_law_065_collateral_under_harvest_bound()

        # 4. Multilateral CCEE
        self.assert_law_066_multilateral_clearing_and_value_conservation()

        # Conservação geral de CBIOs emitidos vs aposentados/circulantes
        total_issued_cbios = sum(b.total_quantity for b in self.cbio_batches.values())
        total_circ_cbios = sum(b.circulating_quantity for b in self.cbio_batches.values())
        total_ret_cbios = sum(b.retired_quantity for b in self.cbio_batches.values())
        check(
            total_issued_cbios == (total_circ_cbios + total_ret_cbios),
            f"Conservação Geral de CBIOs violada: {total_issued_cbios} != {total_circ_cbios} + {total_ret_cbios}",
        )

        return {
            "all_invariants_satisfied": True,
            "audited_cprs_count": len(self.cprs),
            "audited_cbio_batches_count": len(self.cbio_batches),
            "audited_power_contracts_count": len(self.power_contracts),
            "audited_ccee_batches_count": len(self.ccee_settlement_history),
            "total_issued_cbios": total_issued_cbios,
            "total_circulating_cbios": total_circ_cbios,
            "total_retired_cbios": total_ret_cbios,
        }

    # =========================================================================
    # Serialização e Persistência em Fixtures JSON
    # =========================================================================

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado completo do motor para dicionário puro."""
        def serialize_item(obj: Any) -> Any:
            if isinstance(obj, Enum):
                return obj.value
            if isinstance(obj, Decimal):
                return str(obj)
            if hasattr(obj, "__dataclass_fields__"):
                return {k: serialize_item(getattr(obj, k)) for k in obj.__dataclass_fields__}
            if isinstance(obj, list):
                return [serialize_item(x) for x in obj]
            if isinstance(obj, dict):
                return {k: serialize_item(v) for k, v in obj.items()}
            return obj

        return {
            "version": "1.0.0",
            "cprs": [serialize_item(c) for c in self.cprs.values()],
            "cbio_batches": [serialize_item(b) for b in self.cbio_batches.values()],
            "cbio_trades": [serialize_item(t) for t in self.cbio_trades],
            "cbio_retirements": [serialize_item(r) for r in self.cbio_retirements],
            "power_contracts": [serialize_item(p) for p in self.power_contracts.values()],
            "ccee_settlements": [serialize_item(s) for s in self.ccee_settlement_history],
        }

    def dump_to_fixture_file(self, filepath: Union[str, Path]) -> None:
        """Exporta os dados operacionais do motor em arquivo JSON legível."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgroGreenFinanceEngine:
        """Reconstitui a instância do motor a partir de dicionário estruturado."""
        engine = cls()

        # 1. CPRs
        for cpr_raw in data.get("cprs", []):
            deliv_raw = cpr_raw.get("delivery_location")
            deliv_obj = DeliveryLocation(**deliv_raw) if deliv_raw else None

            guarantees_raw = cpr_raw.get("guarantees", [])
            guar_objs = [
                CprGuarantee(
                    guarantee_id=g["guarantee_id"],
                    guarantee_type=GuaranteeType.from_str(g["guarantee_type"]),
                    description=g["description"],
                    guarantee_value=Decimal(g["guarantee_value"]),
                    registry_office=g["registry_office"],
                    registration_number=g["registration_number"],
                    car_code=g.get("car_code"),
                    sigef_parcel_code=g.get("sigef_parcel_code"),
                    affected_area_ha=Decimal(g["affected_area_ha"]) if g.get("affected_area_ha") else None,
                    registered_at=g.get("registered_at", ""),
                    status=g.get("status", "ACTIVE"),
                )
                for g in guarantees_raw
            ]

            fractions_raw = cpr_raw.get("fractions", [])
            frac_objs = [
                CprFraction(
                    fraction_id=fr["fraction_id"],
                    parent_cpr_id=fr["parent_cpr_id"],
                    fraction_index=int(fr["fraction_index"]),
                    total_fractions=int(fr["total_fractions"]),
                    quantity=Decimal(fr["quantity"]),
                    unit=MeasurementUnit.from_str(fr["unit"]),
                    notional_amount=Decimal(fr["notional_amount"]),
                    creditor=Party(**fr["creditor"]),
                    allocated_guarantee_value=Decimal(fr["allocated_guarantee_value"]),
                    percentage_share=Decimal(fr["percentage_share"]),
                    created_at=fr.get("created_at", ""),
                    status=fr.get("status", "ACTIVE"),
                )
                for fr in fractions_raw
            ]

            cpr_obj = DigitalCpr(
                cpr_id=cpr_raw["cpr_id"],
                cpr_type=CprType.from_str(cpr_raw["cpr_type"]),
                issuer=Party(**cpr_raw["issuer"]),
                creditor=Party(**cpr_raw["creditor"]),
                commodity=CommodityType.from_str(cpr_raw["commodity"]),
                quantity=Decimal(cpr_raw["quantity"]),
                unit=MeasurementUnit.from_str(cpr_raw["unit"]),
                crop_year=cpr_raw["crop_year"],
                issue_date=cpr_raw["issue_date"],
                maturity_date=cpr_raw["maturity_date"],
                estimated_harvest_value=Decimal(cpr_raw["estimated_harvest_value"]),
                notional_amount=Decimal(cpr_raw["notional_amount"]),
                unit_price=Decimal(cpr_raw["unit_price"]) if cpr_raw.get("unit_price") else None,
                price_index=cpr_raw.get("price_index"),
                spread_percentage=Decimal(cpr_raw.get("spread_percentage", "0.00")),
                interest_rate_annual=Decimal(cpr_raw.get("interest_rate_annual", "0.00")),
                delivery_location=deliv_obj,
                guarantees=guar_objs,
                fractions=frac_objs,
                status=CprStatus(cpr_raw["status"]),
                registered_at=cpr_raw.get("registered_at", ""),
                electronic_signature_hash=cpr_raw.get("electronic_signature_hash", ""),
            )
            engine.cprs[cpr_obj.cpr_id] = cpr_obj

        # 2. CBIOs
        for b_raw in data.get("cbio_batches", []):
            b_obj = CbioBatch(
                batch_id=b_raw["batch_id"],
                issuer_id=b_raw["issuer_id"],
                issuer_name=b_raw["issuer_name"],
                issuer_cnpj=b_raw["issuer_cnpj"],
                bookkeeper_id=b_raw["bookkeeper_id"],
                bookkeeper_name=b_raw["bookkeeper_name"],
                bookkeeper_cnpj=b_raw["bookkeeper_cnpj"],
                anp_certificate_code=b_raw["anp_certificate_code"],
                nfe_key=b_raw["nfe_key"],
                biofuel_type=BiofuelType.from_str(b_raw["biofuel_type"]),
                energy_efficiency_score=Decimal(b_raw["energy_efficiency_score"]),
                total_quantity=int(b_raw["total_quantity"]),
                circulating_quantity=int(b_raw["circulating_quantity"]),
                retired_quantity=int(b_raw["retired_quantity"]),
                unit_price_brl=Decimal(b_raw["unit_price_brl"]),
                current_owner_cnpj=b_raw["current_owner_cnpj"],
                current_owner_name=b_raw["current_owner_name"],
                issuance_timestamp=b_raw.get("issuance_timestamp", ""),
                status=CbioBatchStatus(b_raw["status"]),
            )
            engine.cbio_batches[b_obj.batch_id] = b_obj

        for t_raw in data.get("cbio_trades", []):
            trade_obj = CbioTradeRecord(
                trade_id=t_raw["trade_id"],
                batch_id=t_raw["batch_id"],
                seller_cnpj=t_raw["seller_cnpj"],
                buyer_cnpj=t_raw["buyer_cnpj"],
                quantity=int(t_raw["quantity"]),
                unit_price_brl=Decimal(t_raw["unit_price_brl"]),
                total_amount_brl=Decimal(t_raw["total_amount_brl"]),
                timestamp=t_raw.get("timestamp", ""),
            )
            engine.cbio_trades.append(trade_obj)

        for r_raw in data.get("cbio_retirements", []):
            cert_obj = CbioRetirementCertificate(
                certificate_id=r_raw["certificate_id"],
                obligated_party_cnpj=r_raw["obligated_party_cnpj"],
                obligated_party_name=r_raw["obligated_party_name"],
                compliance_year=int(r_raw["compliance_year"]),
                quantity_retired=int(r_raw["quantity_retired"]),
                tco2e_avoided=Decimal(r_raw["tco2e_avoided"]),
                batch_ids=r_raw["batch_ids"],
                certificate_hash=r_raw.get("certificate_hash", ""),
                timestamp=r_raw.get("timestamp", ""),
            )
            engine.cbio_retirements.append(cert_obj)

        # 3. Contratos de Energia Elétrica
        for p_raw in data.get("power_contracts", []):
            p_obj = BilateralPowerContract(
                contract_id=p_raw["contract_id"],
                ccee_contract_code=p_raw["ccee_contract_code"],
                buyer_id=p_raw["buyer_id"],
                buyer_name=p_raw["buyer_name"],
                seller_id=p_raw["seller_id"],
                seller_name=p_raw["seller_name"],
                energy_source=EnergySource(p_raw["energy_source"]),
                submarket=SinSubmarket(p_raw["submarket"]),
                start_date=p_raw["start_date"],
                end_date=p_raw["end_date"],
                mw_average=Decimal(p_raw["mw_average"]),
                total_hours=int(p_raw["total_hours"]),
                volume_mwh=Decimal(p_raw["volume_mwh"]),
                price_per_mwh=Decimal(p_raw["price_per_mwh"]),
                notional_amount=Decimal(p_raw["notional_amount"]),
                fiduciary_lock_amount=Decimal(p_raw.get("fiduciary_lock_amount", "0.00")),
                fiduciary_creditor_id=p_raw.get("fiduciary_creditor_id"),
                status=p_raw.get("status", "ACTIVE"),
                registered_at=p_raw.get("registered_at", ""),
            )
            engine.power_contracts[p_obj.contract_id] = p_obj

        for s_raw in data.get("ccee_settlements", []):
            s_obj = CceeMcpSettlementBatch(
                batch_id=s_raw["batch_id"],
                period=s_raw["period"],
                submarket=SinSubmarket(s_raw["submarket"]),
                pld_applied=Decimal(s_raw["pld_applied"]),
                participant_settlements=s_raw["participant_settlements"],
                total_credits_brl=Decimal(s_raw["total_credits_brl"]),
                total_debits_brl=Decimal(s_raw["total_debits_brl"]),
                net_clearing_discrepancy_brl=Decimal(s_raw["net_clearing_discrepancy_brl"]),
                settled_at=s_raw.get("settled_at", ""),
            )
            engine.ccee_settlement_history.append(s_obj)

        return engine

    @classmethod
    def load_from_fixture_file(cls, filepath: Union[str, Path]) -> AgroGreenFinanceEngine:
        """Carrega e instancia o motor a partir de arquivo JSON de fixture."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Arquivo de fixture não encontrado: '{filepath}'.")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    # =========================================================================
    # Métodos Auxiliares Internos
    # =========================================================================

    def _get_cpr(self, cpr_id: str) -> DigitalCpr:
        if cpr_id not in self.cprs:
            raise CprValidationError(f"CPR '{cpr_id}' não localizada no livro de registro.")
        return self.cprs[cpr_id]

    def _get_cbio_batch(self, batch_id: str) -> CbioBatch:
        if batch_id not in self.cbio_batches:
            raise CbioValidationError(f"Lote de CBIO '{batch_id}' não encontrado na escrituração.")
        return self.cbio_batches[batch_id]
