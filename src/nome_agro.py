"""Módulo de Registro e Escrituração de Títulos do Agronegócio B3 NoMe Agro (FL-026).

Este módulo implementa o modelo de mensageria, regras de negócio e motor
determinístico de registro escritural para títulos do agronegócio
(Cédula de Produto Rural - CPR Física e Financeira, lastros de CRA e CDCA),
conforme Lei nº 8.929/1994, Lei nº 13.986/2020 (Lei do Agro), Lei nº 14.421/2022
e Resoluções CMN nº 4.870/2020 e 5.040/2022.

Utiliza estritamente a biblioteca padrão do Python (sem dependências externas).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Constantes de Domínio e Geografia Brasileira
# ---------------------------------------------------------------------------

BRAZILIAN_STATES = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
    "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
    "RS", "RO", "RR", "SC", "SP", "SE", "TO"
}

# Padrão SICAR oficial: UF (2 letras) - Código IBGE Município (7 dígitos) - Hash Hexadecimal (32 chars)
CAR_REGEX = re.compile(r"^([A-Z]{2})-(\d{7})-([A-Fa-f0-9]{32})$")

# Padrão Safra: YYYY/YYYY (ex.: 2026/2027) ou YYYY (ex.: 2026)
CROP_YEAR_REGEX = re.compile(r"^(\d{4})(?:/(\d{4}))?$")


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class AgroError(Exception):
    """Exceção base para operações do NoMe Agro B3."""
    pass


class AgroValidationError(AgroError):
    """Erro de validação de regras de negócio ou parâmetros da CPR."""
    pass


class AgroParsingError(AgroError):
    """Erro de parsing estrutural em documentos JSON da CPR."""
    pass


class AgroSettlementError(AgroError):
    """Erro no processo de liquidação física ou financeira da CPR."""
    pass


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------

class CommodityType(str, Enum):
    """Commodities e produtos agropecuários elegíveis para emissão de CPR."""
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
        """Converte e normaliza variações textuais para CommodityType."""
        if isinstance(value, cls):
            return value
        if not value:
            raise AgroValidationError("Commodity agropecuária não pode ser vazia.")

        norm = str(value).strip().upper()
        norm = norm.replace("É", "E").replace("Õ", "O").replace("Ã", "A").replace("Ú", "U").replace("-", "_")

        synonyms = {
            "SOJA": cls.SOJA,
            "SOY": cls.SOJA,
            "SOYBEAN": cls.SOJA,
            "MILHO": cls.MILHO,
            "CORN": cls.MILHO,
            "CAFE": cls.CAFE,
            "COFFEE": cls.CAFE,
            "BOI_GORDO": cls.BOI_GORDO,
            "BOI": cls.BOI_GORDO,
            "CATTLE": cls.BOI_GORDO,
            "ALGODAO": cls.ALGODAO,
            "COTTON": cls.ALGODAO,
            "CANA": cls.CANA_DE_ACUCAR,
            "CANA_DE_ACUCAR": cls.CANA_DE_ACUCAR,
            "SUGARCANE": cls.CANA_DE_ACUCAR,
            "TRIGO": cls.TRIGO,
            "WHEAT": cls.TRIGO,
            "ARROZ": cls.ARROZ,
            "RICE": cls.ARROZ,
            "FEIJAO": cls.FEIJAO,
            "BEANS": cls.FEIJAO,
        }

        if norm in synonyms:
            return synonyms[norm]

        valid = ", ".join(m.value for m in cls)
        raise AgroValidationError(
            f"Commodity inválida: {value!r}. Valores permitidos: {valid}"
        )


class CprType(str, Enum):
    """Modalidade da Cédula de Produto Rural conforme a Lei nº 8.929/1994."""
    FISICA = "FISICA"
    FINANCEIRA = "FINANCEIRA"

    @classmethod
    def from_str(cls, value: Any) -> CprType:
        """Converte e normaliza strings para CprType."""
        if isinstance(value, cls):
            return value
        if not value:
            raise AgroValidationError("Tipo de CPR não pode ser vazio.")

        norm = str(value).strip().upper().replace("Í", "I")
        if norm in ("FISICA", "PHYSICAL"):
            return cls.FISICA
        if norm in ("FINANCEIRA", "FINANCIAL"):
            return cls.FINANCEIRA

        raise AgroValidationError(f"Tipo de CPR inválido: {value!r}. Use 'FISICA' ou 'FINANCEIRA'.")


class AgroGuaranteeType(str, Enum):
    """Garantias reais e fidejussórias do agronegócio (Lei 13.986/2020)."""
    PENHOR_AGRICOLA = "PENHOR_AGRICOLA"
    ALIENACAO_FIDUCIARIA_IMOVEL = "ALIENACAO_FIDUCIARIA_IMOVEL"
    PATRIMONIO_RURAL_AFETACAO = "PATRIMONIO_RURAL_AFETACAO"
    FUNDO_GARANTIDOR_SOLIDARIO = "FUNDO_GARANTIDOR_SOLIDARIO"
    HIPOTECA_RURAL = "HIPOTECA_RURAL"
    AVAL = "AVAL"
    SEM_GARANTIA = "SEM_GARANTIA"

    @classmethod
    def from_str(cls, value: Any) -> AgroGuaranteeType:
        """Converte e normaliza strings para AgroGuaranteeType."""
        if isinstance(value, cls):
            return value
        if not value:
            raise AgroValidationError("Tipo de garantia não pode ser vazio.")

        norm = str(value).strip().upper()
        norm = norm.replace("Ç", "C").replace("Ã", "A").replace("Á", "A").replace("Ó", "O").replace("É", "E")
        norm = norm.replace(" ", "_").replace("-", "_")

        synonyms = {
            "PENHOR_AGRICOLA": cls.PENHOR_AGRICOLA,
            "PENHOR": cls.PENHOR_AGRICOLA,
            "AGRICULTURAL_PLEDGE": cls.PENHOR_AGRICOLA,
            "ALIENACAO_FIDUCIARIA": cls.ALIENACAO_FIDUCIARIA_IMOVEL,
            "ALIENACAO_FIDUCIARIA_IMOVEL": cls.ALIENACAO_FIDUCIARIA_IMOVEL,
            "FIDUCIARY_ASSIGNMENT": cls.ALIENACAO_FIDUCIARIA_IMOVEL,
            "PATRIMONIO_RURAL_AFETACAO": cls.PATRIMONIO_RURAL_AFETACAO,
            "PRA": cls.PATRIMONIO_RURAL_AFETACAO,
            "FUNDO_GARANTIDOR_SOLIDARIO": cls.FUNDO_GARANTIDOR_SOLIDARIO,
            "FGS": cls.FUNDO_GARANTIDOR_SOLIDARIO,
            "HIPOTECA_RURAL": cls.HIPOTECA_RURAL,
            "HIPOTECA": cls.HIPOTECA_RURAL,
            "AVAL": cls.AVAL,
            "SEM_GARANTIA": cls.SEM_GARANTIA,
            "QUIROGRAFARIO": cls.SEM_GARANTIA,
        }

        if norm in synonyms:
            return synonyms[norm]

        valid = ", ".join(m.value for m in cls)
        raise AgroValidationError(
            f"Tipo de garantia agropecuária inválido: {value!r}. Valores permitidos: {valid}"
        )


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
        """Converte e normaliza strings para MeasurementUnit."""
        if isinstance(value, cls):
            return value
        if not value:
            raise AgroValidationError("Unidade de medida não pode ser vazia.")

        norm = str(value).strip().upper().replace(" ", "_").replace("-", "_")
        synonyms = {
            "SACAS_60KG": cls.SACAS_60KG,
            "SACAS": cls.SACAS_60KG,
            "SACA": cls.SACAS_60KG,
            "SC": cls.SACAS_60KG,
            "BAGS": cls.SACAS_60KG,
            "ARROBAS": cls.ARROBAS,
            "ARROBA": cls.ARROBAS,
            "@": cls.ARROBAS,
            "TONELADAS": cls.TONELADAS,
            "TONELADA": cls.TONELADAS,
            "TON": cls.TONELADAS,
            "TONS": cls.TONELADAS,
            "QUILOGRAMAS": cls.QUILOGRAMAS,
            "QUILOGRAMA": cls.QUILOGRAMAS,
            "KG": cls.QUILOGRAMAS,
            "LITROS": cls.LITROS,
            "LITRO": cls.LITROS,
            "L": cls.LITROS,
            "METROS_CUBICOS": cls.METROS_CUBICOS,
            "M3": cls.METROS_CUBICOS,
        }

        if norm in synonyms:
            return synonyms[norm]

        valid = ", ".join(m.value for m in cls)
        raise AgroValidationError(
            f"Unidade de medida inválida: {value!r}. Valores permitidos: {valid}"
        )


class PriceIndexType(str, Enum):
    """Índices de preço e bases referenciais para CPR Financeira."""
    CEPEA_PARANAGUA = "CEPEA_PARANAGUA"
    CEPEA_ESALQ = "CEPEA_ESALQ"
    B3_AGRO = "B3_AGRO"
    FIXO = "FIXO"
    CUSTOM = "CUSTOM"

    @classmethod
    def from_str(cls, value: Any) -> PriceIndexType:
        """Converte e normaliza strings para PriceIndexType."""
        if isinstance(value, cls):
            return value
        if not value:
            raise AgroValidationError("Índice de preço não pode ser vazio.")

        norm = str(value).strip().upper().replace(" ", "_").replace("-", "_")
        synonyms = {
            "CEPEA_PARANAGUA": cls.CEPEA_PARANAGUA,
            "CEPEA_SOJA_PARANAGUA": cls.CEPEA_PARANAGUA,
            "PARANAGUA": cls.CEPEA_PARANAGUA,
            "CEPEA_ESALQ": cls.CEPEA_ESALQ,
            "ESALQ": cls.CEPEA_ESALQ,
            "CEPEA": cls.CEPEA_ESALQ,
            "B3_AGRO": cls.B3_AGRO,
            "B3": cls.B3_AGRO,
            "BMF": cls.B3_AGRO,
            "FIXO": cls.FIXO,
            "PRE_FIXADO": cls.FIXO,
            "FIXED": cls.FIXO,
            "CUSTOM": cls.CUSTOM,
        }

        if norm in synonyms:
            return synonyms[norm]

        valid = ", ".join(m.value for m in cls)
        raise AgroValidationError(
            f"Índice de preço agropecuário inválido: {value!r}. Valores permitidos: {valid}"
        )


class ApprovalStatus(str, Enum):
    """Ciclo de status de anuência e registro da CPR no NoMe Agro."""
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SETTLED = "SETTLED"
    CANCELLED = "CANCELLED"

    @classmethod
    def from_str(cls, value: Any) -> ApprovalStatus:
        """Converte strings para ApprovalStatus."""
        if isinstance(value, cls):
            return value
        if not value:
            return cls.PENDING_APPROVAL

        norm = str(value).strip().upper()
        for status in cls:
            if status.value == norm:
                return status

        raise AgroValidationError(f"Status de aprovação inválido: {value!r}")


# ---------------------------------------------------------------------------
# Funções Auxiliares de Validação (CPF, CNPJ, CAR, Safra)
# ---------------------------------------------------------------------------

def clean_digits(value: str) -> str:
    """Remove caracteres não numéricos de uma string."""
    return re.sub(r"\D", "", value) if value else ""


def validate_cpf(cpf: str) -> bool:
    """Valida número de CPF conforme algoritmo de módulo 11."""
    digits = clean_digits(cpf)
    if len(digits) != 11:
        return False
    if digits == digits[0] * 11:
        return False

    # Primeiro dígito verificador
    s1 = sum(int(digits[i]) * (10 - i) for i in range(9))
    d1 = (s1 * 10 % 11) % 10
    if int(digits[9]) != d1:
        return False

    # Segundo dígito verificador
    s2 = sum(int(digits[i]) * (11 - i) for i in range(10))
    d2 = (s2 * 10 % 11) % 10
    return int(digits[10]) == d2


def validate_cnpj(cnpj: str) -> bool:
    """Valida número de CNPJ conforme algoritmo de módulo 11."""
    digits = clean_digits(cnpj)
    if len(digits) != 14:
        return False
    if digits == digits[0] * 14:
        return False

    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    s1 = sum(int(digits[i]) * w1[i] for i in range(12))
    r1 = s1 % 11
    d1 = 0 if r1 < 2 else 11 - r1
    if int(digits[12]) != d1:
        return False

    w2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    s2 = sum(int(digits[i]) * w2[i] for i in range(13))
    r2 = s2 % 11
    d2 = 0 if r2 < 2 else 11 - r2
    return int(digits[13]) == d2


def validate_document(doc: str) -> bool:
    """Valida se o documento informado é um CPF ou CNPJ regular."""
    digits = clean_digits(doc)
    if len(digits) == 11:
        return validate_cpf(digits)
    if len(digits) == 14:
        return validate_cnpj(digits)
    return False


def validate_car_code(car_code: Optional[str]) -> bool:
    """Valida sintaxe do Cadastro Ambiental Rural (SICAR): UF-IBGE-HASH32."""
    if not car_code or not isinstance(car_code, str):
        return False

    code = car_code.strip()
    match = CAR_REGEX.match(code)
    if not match:
        return False

    uf = match.group(1)
    return uf in BRAZILIAN_STATES


def validate_crop_year_string(crop_year: str, issue_date: date) -> bool:
    """Valida formatação da safra e garante não-retroatividade."""
    if not crop_year or not isinstance(crop_year, str):
        return False

    match = CROP_YEAR_REGEX.match(crop_year.strip())
    if not match:
        return False

    y1 = int(match.group(1))
    y2 = int(match.group(2)) if match.group(2) else y1

    if match.group(2) and y2 < y1:
        return False

    # Safra não pode ser retroativa em relação à data de emissão
    if y2 < issue_date.year:
        return False

    return True


# ---------------------------------------------------------------------------
# Dataclasses de Domínio
# ---------------------------------------------------------------------------

@dataclass
class Party:
    """Participante da relação contratual da CPR (Emissor ou Credor)."""
    name: str
    document: str
    party_type: str = "PJ"  # "PF" ou "PJ"
    email: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None

    @property
    def clean_document(self) -> str:
        """Retorna o documento apenas com dígitos."""
        return clean_digits(self.document)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o participante em dicionário."""
        return {
            "name": self.name,
            "document": self.clean_document,
            "party_type": self.party_type,
            "email": self.email,
            "city": self.city,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Party:
        """Desserializa participante a partir de dicionário."""
        return cls(
            name=str(data.get("name", "")).strip(),
            document=clean_digits(str(data.get("document", ""))),
            party_type=str(data.get("party_type", "PJ")).strip().upper(),
            email=data.get("email"),
            city=data.get("city"),
            state=data.get("state"),
        )


@dataclass
class DeliveryLocation:
    """Local e janela de entrega física da commodity na CPR Física."""
    warehouse_name: str
    address: str
    city: str
    state: str
    ibge_code: Optional[str] = None
    warehouse_cnpj: Optional[str] = None
    delivery_start_date: Optional[date] = None
    delivery_end_date: Optional[date] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o local de entrega em dicionário."""
        return {
            "warehouse_name": self.warehouse_name,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "ibge_code": self.ibge_code,
            "warehouse_cnpj": clean_digits(self.warehouse_cnpj) if self.warehouse_cnpj else None,
            "delivery_start_date": self.delivery_start_date.isoformat() if self.delivery_start_date else None,
            "delivery_end_date": self.delivery_end_date.isoformat() if self.delivery_end_date else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DeliveryLocation:
        """Desserializa local de entrega a partir de dicionário."""
        start_d = data.get("delivery_start_date")
        end_d = data.get("delivery_end_date")

        return cls(
            warehouse_name=str(data.get("warehouse_name", "")).strip(),
            address=str(data.get("address", "")).strip(),
            city=str(data.get("city", "")).strip(),
            state=str(data.get("state", "")).strip().upper(),
            ibge_code=str(data.get("ibge_code")).strip() if data.get("ibge_code") else None,
            warehouse_cnpj=clean_digits(str(data.get("warehouse_cnpj"))) if data.get("warehouse_cnpj") else None,
            delivery_start_date=date.fromisoformat(str(start_d)) if start_d else None,
            delivery_end_date=date.fromisoformat(str(end_d)) if end_d else None,
        )


@dataclass
class AgroGuarantee:
    """Garantia real ou fidejussória vinculada à CPR."""
    guarantee_type: AgroGuaranteeType
    description: str
    car_code: Optional[str] = None
    property_name: Optional[str] = None
    land_registry_number: Optional[str] = None
    registry_office: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    guarantee_value: Optional[Decimal] = None
    coverage_percentage: Optional[Decimal] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a garantia em dicionário."""
        return {
            "guarantee_type": self.guarantee_type.value,
            "description": self.description,
            "car_code": self.car_code,
            "property_name": self.property_name,
            "land_registry_number": self.land_registry_number,
            "registry_office": self.registry_office,
            "city": self.city,
            "state": self.state,
            "guarantee_value": str(self.guarantee_value) if self.guarantee_value is not None else None,
            "coverage_percentage": str(self.coverage_percentage) if self.coverage_percentage is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgroGuarantee:
        """Desserializa garantia a partir de dicionário."""
        g_val = data.get("guarantee_value")
        c_pct = data.get("coverage_percentage")

        return cls(
            guarantee_type=AgroGuaranteeType.from_str(data.get("guarantee_type")),
            description=str(data.get("description", "")).strip(),
            car_code=str(data.get("car_code")).strip() if data.get("car_code") else None,
            property_name=str(data.get("property_name")).strip() if data.get("property_name") else None,
            land_registry_number=str(data.get("land_registry_number")).strip() if data.get("land_registry_number") else None,
            registry_office=str(data.get("registry_office")).strip() if data.get("registry_office") else None,
            city=str(data.get("city")).strip() if data.get("city") else None,
            state=str(data.get("state")).strip().upper() if data.get("state") else None,
            guarantee_value=Decimal(str(g_val)) if g_val is not None else None,
            coverage_percentage=Decimal(str(c_pct)) if c_pct is not None else None,
        )


@dataclass
class ElectronicApproval:
    """Registro de anuência eletrônica emitida pelo credor/beneficiário."""
    approved: bool
    approved_by: str
    creditor_document: str
    timestamp: datetime
    signature_hash: str
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a anuência em dicionário."""
        return {
            "approved": self.approved,
            "approved_by": self.approved_by,
            "creditor_document": clean_digits(self.creditor_document),
            "timestamp": self.timestamp.isoformat(),
            "signature_hash": self.signature_hash,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ElectronicApproval:
        """Desserializa anuência a partir de dicionário."""
        ts = data.get("timestamp")
        return cls(
            approved=bool(data.get("approved", True)),
            approved_by=str(data.get("approved_by", "")).strip(),
            creditor_document=clean_digits(str(data.get("creditor_document", ""))),
            timestamp=datetime.fromisoformat(str(ts)) if ts else datetime.now(timezone.utc),
            signature_hash=str(data.get("signature_hash", "")).strip(),
            notes=data.get("notes"),
        )


@dataclass
class SettlementRecord:
    """Registro de efetivação da liquidação física ou financeira da CPR."""
    settled_at: datetime
    settlement_type: CprType
    settlement_price: Optional[Decimal] = None
    total_financial_value: Optional[Decimal] = None
    quantity_delivered: Optional[Decimal] = None
    receipt_identifier: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o registro de liquidação em dicionário."""
        return {
            "settled_at": self.settled_at.isoformat(),
            "settlement_type": self.settlement_type.value,
            "settlement_price": str(self.settlement_price) if self.settlement_price is not None else None,
            "total_financial_value": str(self.total_financial_value) if self.total_financial_value is not None else None,
            "quantity_delivered": str(self.quantity_delivered) if self.quantity_delivered is not None else None,
            "receipt_identifier": self.receipt_identifier,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SettlementRecord:
        """Desserializa registro de liquidação a partir de dicionário."""
        sat = data.get("settled_at")
        sprice = data.get("settlement_price")
        totval = data.get("total_financial_value")
        qdel = data.get("quantity_delivered")

        return cls(
            settled_at=datetime.fromisoformat(str(sat)) if sat else datetime.now(timezone.utc),
            settlement_type=CprType.from_str(data.get("settlement_type")),
            settlement_price=Decimal(str(sprice)) if sprice is not None else None,
            total_financial_value=Decimal(str(totval)) if totval is not None else None,
            quantity_delivered=Decimal(str(qdel)) if qdel is not None else None,
            receipt_identifier=data.get("receipt_identifier"),
            notes=data.get("notes"),
        )


@dataclass
class CprRegistration:
    """Registro escritural da Cédula de Produto Rural (CPR) no NoMe Agro B3."""
    cpr_id: str
    cpr_type: CprType
    issuer: Party
    creditor: Party
    commodity: CommodityType
    quantity: Decimal
    unit: MeasurementUnit
    crop_year: str
    issue_date: date
    maturity_date: date
    delivery_location: Optional[DeliveryLocation] = None
    price_index: Optional[PriceIndexType] = None
    unit_price: Optional[Decimal] = None
    spread_percentage: Decimal = Decimal("0.00")
    interest_rate_annual: Decimal = Decimal("0.00")
    currency: str = "BRL"
    guarantees: List[AgroGuarantee] = field(default_factory=list)
    status: ApprovalStatus = ApprovalStatus.PENDING_APPROVAL
    approval: Optional[ElectronicApproval] = None
    settlement: Optional[SettlementRecord] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def calculate_financial_value(
        self,
        reference_price: Optional[Union[Decimal, float, str]] = None
    ) -> Decimal:
        """
        Calcula o valor financeiro da CPR com base na cotação de referência.

        Valor = Quantidade * Preço * (1 + spread / 100) * (1 + juros_ano * dias / 365)
        """
        if reference_price is not None:
            price = Decimal(str(reference_price))
        elif self.unit_price is not None:
            price = self.unit_price
        else:
            raise AgroValidationError(
                f"Preço de referência não informado para cálculo da CPR {self.cpr_id}"
            )

        if price <= Decimal("0"):
            raise AgroValidationError(f"Preço de referência deve ser positivo: {price}")

        base_value = self.quantity * price

        spread_factor = Decimal("1") + (self.spread_percentage / Decimal("100"))

        if self.interest_rate_annual > Decimal("0") and self.maturity_date > self.issue_date:
            days = Decimal(str((self.maturity_date - self.issue_date).days))
            interest_factor = Decimal("1") + ((self.interest_rate_annual / Decimal("100")) * (days / Decimal("365")))
        else:
            interest_factor = Decimal("1")

        total = base_value * spread_factor * interest_factor
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o registro da CPR para dicionário compatível com NoMe Agro JSON."""
        return {
            "cpr_id": self.cpr_id,
            "cpr_type": self.cpr_type.value,
            "issuer": self.issuer.to_dict(),
            "creditor": self.creditor.to_dict(),
            "commodity": self.commodity.value,
            "quantity": int(self.quantity) if self.quantity == self.quantity.to_integral() else float(self.quantity),
            "unit": self.unit.value,
            "crop_year": self.crop_year,
            "issue_date": self.issue_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "delivery_location": self.delivery_location.to_dict() if self.delivery_location else None,
            "price_index": self.price_index.value if self.price_index else None,
            "unit_price": str(self.unit_price) if self.unit_price is not None else None,
            "spread_percentage": str(self.spread_percentage),
            "interest_rate_annual": str(self.interest_rate_annual),
            "currency": self.currency,
            "guarantees": [g.to_dict() for g in self.guarantees],
            "status": self.status.value,
            "approval": self.approval.to_dict() if self.approval else None,
            "settlement": self.settlement.to_dict() if self.settlement else None,
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serializa o registro da CPR para string JSON."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CprRegistration:
        """Desserializa o registro da CPR a partir de um dicionário."""
        if not isinstance(data, dict):
            raise AgroParsingError(f"Dados da CPR devem ser um dicionário, recebido: {type(data)}")

        cpr_id = str(data.get("cpr_id", "")).strip()
        if not cpr_id:
            raise AgroValidationError("Identificador 'cpr_id' é obrigatório.")

        cpr_type = CprType.from_str(data.get("cpr_type"))
        issuer = Party.from_dict(data.get("issuer", {}))
        creditor = Party.from_dict(data.get("creditor", {}))
        commodity = CommodityType.from_str(data.get("commodity"))

        raw_qty = data.get("quantity")
        if raw_qty is None:
            raise AgroValidationError("Campo 'quantity' é obrigatório.")
        try:
            quantity = Decimal(str(raw_qty))
        except (InvalidOperation, TypeError):
            raise AgroValidationError(f"Quantidade inválida: {raw_qty!r}")

        unit = MeasurementUnit.from_str(data.get("unit"))
        crop_year = str(data.get("crop_year", "")).strip()

        raw_issue = data.get("issue_date")
        if not raw_issue:
            raise AgroValidationError("Campo 'issue_date' é obrigatório.")
        issue_date = date.fromisoformat(str(raw_issue))

        raw_mat = data.get("maturity_date")
        if not raw_mat:
            raise AgroValidationError("Campo 'maturity_date' é obrigatório.")
        maturity_date = date.fromisoformat(str(raw_mat))

        deliv_loc = None
        if data.get("delivery_location"):
            deliv_loc = DeliveryLocation.from_dict(data["delivery_location"])

        p_index = None
        if data.get("price_index"):
            p_index = PriceIndexType.from_str(data["price_index"])

        unit_p = None
        if data.get("unit_price") is not None:
            unit_p = Decimal(str(data["unit_price"]))

        spread = Decimal("0.00")
        if data.get("spread_percentage") is not None:
            spread = Decimal(str(data["spread_percentage"]))

        irate = Decimal("0.00")
        if data.get("interest_rate_annual") is not None:
            irate = Decimal(str(data["interest_rate_annual"]))

        currency = str(data.get("currency", "BRL")).strip().upper()

        guarantees: List[AgroGuarantee] = []
        for g_data in data.get("guarantees", []):
            guarantees.append(AgroGuarantee.from_dict(g_data))

        status = ApprovalStatus.from_str(data.get("status", ApprovalStatus.PENDING_APPROVAL))

        approval = None
        if data.get("approval"):
            approval = ElectronicApproval.from_dict(data["approval"])

        settlement = None
        if data.get("settlement"):
            settlement = SettlementRecord.from_dict(data["settlement"])

        metadata = data.get("metadata", {}) or {}

        return cls(
            cpr_id=cpr_id,
            cpr_type=cpr_type,
            issuer=issuer,
            creditor=creditor,
            commodity=commodity,
            quantity=quantity,
            unit=unit,
            crop_year=crop_year,
            issue_date=issue_date,
            maturity_date=maturity_date,
            delivery_location=deliv_loc,
            price_index=p_index,
            unit_price=unit_p,
            spread_percentage=spread,
            interest_rate_annual=irate,
            currency=currency,
            guarantees=guarantees,
            status=status,
            approval=approval,
            settlement=settlement,
            metadata=metadata,
        )

    @classmethod
    def from_json(cls, json_str: str) -> CprRegistration:
        """Carrega e desserializa a CPR a partir de string JSON."""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as err:
            raise AgroParsingError(f"Erro de sintaxe JSON ao decodificar CPR: {err}")
        return cls.from_dict(data)

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> CprRegistration:
        """Carrega a CPR diretamente de arquivo JSON no disco."""
        file_path = Path(path)
        if not file_path.is_file():
            raise AgroParsingError(f"Arquivo de CPR não encontrado: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())


# ---------------------------------------------------------------------------
# Motor de Registro Escritural e Validação do Agronegócio
# ---------------------------------------------------------------------------

class AgroRegistrationEngine:
    """
    Motor determinístico de validação, registro escritural e liquidação de CPRs.

    Implementa a conformidade com a Lei do Agro (Lei 13.986/2020) e as regras
    da B3 NoMe Agro.
    """

    def __init__(self) -> None:
        self._cprs: Dict[str, CprRegistration] = {}

    @staticmethod
    def validate_car(car_code: Optional[str]) -> bool:
        """Valida o código do Cadastro Ambiental Rural (SICAR)."""
        return validate_car_code(car_code)

    @staticmethod
    def validate_crop_year(crop_year: str, issue_date: date) -> bool:
        """Valida se a safra é válida e futura/coerente com a emissão."""
        return validate_crop_year_string(crop_year, issue_date)

    @staticmethod
    def validate_document(doc: str) -> bool:
        """Valida regularidade do CPF ou CNPJ."""
        return validate_document(doc)

    def validate_cpr(self, cpr: CprRegistration) -> List[str]:
        """
        Executa todas as validações determinísticas de negócio sobre a CPR.

        Retorna lista de avisos caso passe, ou levanta AgroValidationError.
        """
        warnings: List[str] = []

        # 1. Identificador da CPR
        if not cpr.cpr_id or len(cpr.cpr_id.strip()) == 0:
            raise AgroValidationError("Identificador 'cpr_id' não pode ser vazio.")

        # 2. Quantidade estritamente positiva
        if cpr.quantity <= Decimal("0"):
            raise AgroValidationError(
                f"Quantidade da CPR deve ser estritamente positiva: {cpr.quantity}"
            )

        # 3. Consistência cronológica de emissão e vencimento
        if cpr.maturity_date < cpr.issue_date:
            raise AgroValidationError(
                f"Data de vencimento ({cpr.maturity_date}) não pode ser anterior à data de emissão ({cpr.issue_date})"
            )

        # 4. Safra futura e coerente
        if not self.validate_crop_year(cpr.crop_year, cpr.issue_date):
            raise AgroValidationError(
                f"Safra inválida ou retroativa: '{cpr.crop_year}' não permitida para emissão em {cpr.issue_date.year}"
            )

        # 5. Emissor e Credor
        if not cpr.issuer.name:
            raise AgroValidationError("Nome do emissor é obrigatório.")
        if not self.validate_document(cpr.issuer.document):
            raise AgroValidationError(
                f"Documento do emissor inválido (CPF/CNPJ): {cpr.issuer.document!r}"
            )

        if not cpr.creditor.name:
            raise AgroValidationError("Nome do credor é obrigatório.")
        if not self.validate_document(cpr.creditor.document):
            raise AgroValidationError(
                f"Documento do credor inválido (CPF/CNPJ): {cpr.creditor.document!r}"
            )

        if cpr.issuer.clean_document == cpr.creditor.clean_document:
            raise AgroValidationError("Emissor e credor não podem possuir o mesmo documento.")

        # 6. Regras específicas para CPR Física
        if cpr.cpr_type == CprType.FISICA:
            if cpr.delivery_location is None:
                raise AgroValidationError(
                    "CPR Física exige especificação de local de entrega ('delivery_location')."
                )

            loc = cpr.delivery_location
            if not loc.warehouse_name or not loc.city or not loc.state:
                raise AgroValidationError(
                    "Local de entrega exige preenchimento de armazém/silo, município e UF."
                )

            if loc.state not in BRAZILIAN_STATES:
                raise AgroValidationError(
                    f"UF do local de entrega inválida: {loc.state!r}"
                )

            if loc.delivery_start_date:
                if loc.delivery_start_date < cpr.issue_date:
                    raise AgroValidationError(
                        f"Data inicial de entrega ({loc.delivery_start_date}) anterior à data de emissão ({cpr.issue_date})."
                    )

            if loc.delivery_start_date and loc.delivery_end_date:
                if loc.delivery_end_date < loc.delivery_start_date:
                    raise AgroValidationError(
                        f"Data final de entrega ({loc.delivery_end_date}) anterior à data inicial ({loc.delivery_start_date})."
                    )

        # 7. Regras específicas para CPR Financeira
        if cpr.cpr_type == CprType.FINANCEIRA:
            if cpr.price_index is None:
                raise AgroValidationError(
                    "CPR Financeira exige especificação de índice de preço ('price_index')."
                )

            if cpr.price_index == PriceIndexType.FIXO:
                if cpr.unit_price is None or cpr.unit_price <= Decimal("0"):
                    raise AgroValidationError(
                        "CPR Financeira com índice FIXO exige 'unit_price' estritamente positivo."
                    )

            if cpr.unit_price is not None and cpr.unit_price <= Decimal("0"):
                raise AgroValidationError(
                    f"Preço unitário deve ser estritamente positivo: {cpr.unit_price}"
                )

        # 8. Validação de Garantias
        for idx, g in enumerate(cpr.guarantees):
            if g.guarantee_value is not None and g.guarantee_value < Decimal("0"):
                raise AgroValidationError(
                    f"Garantia #{idx + 1} possui valor negativo: {g.guarantee_value}"
                )

            if g.coverage_percentage is not None:
                if g.coverage_percentage < Decimal("0") or g.coverage_percentage > Decimal("1000"):
                    raise AgroValidationError(
                        f"Garantia #{idx + 1} possui percentual de cobertura inválido: {g.coverage_percentage}"
                    )

            # Se for garantia imobiliária rural, o CAR é mandatório e deve ser rigorosamente válido
            if g.guarantee_type in (
                AgroGuaranteeType.ALIENACAO_FIDUCIARIA_IMOVEL,
                AgroGuaranteeType.PATRIMONIO_RURAL_AFETACAO,
                AgroGuaranteeType.HIPOTECA_RURAL,
            ):
                if not g.car_code:
                    raise AgroValidationError(
                        f"Garantia #{idx + 1} ({g.guarantee_type.value}) exige obrigatoriamente código CAR regular."
                    )

                if not self.validate_car(g.car_code):
                    raise AgroValidationError(
                        f"Código CAR inválido na garantia #{idx + 1}: {g.car_code!r}. "
                        "Esperado padrão SICAR: UF-IBGE-HASH32 (ex: MT-5107909-E8B1A2C3D4E5F6789012345678901234)."
                    )

        return warnings

    def register_cpr(
        self,
        cpr: Union[CprRegistration, Dict[str, Any]]
    ) -> CprRegistration:
        """
        Valida e efetua o registro escritural da CPR no sistema.

        Status inicial definido como PENDING_APPROVAL aguardando anuência do credor.
        """
        if isinstance(cpr, dict):
            instance = CprRegistration.from_dict(cpr)
        else:
            instance = cpr

        self.validate_cpr(instance)

        if instance.cpr_id in self._cprs:
            raise AgroValidationError(f"CPR com ID {instance.cpr_id} já se encontra registrada no sistema.")

        instance.status = ApprovalStatus.PENDING_APPROVAL
        self._cprs[instance.cpr_id] = instance
        return instance

    def grant_creditor_approval(
        self,
        cpr_id: str,
        creditor_document: str,
        approved_by: str,
        signature_hash: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> CprRegistration:
        """
        Registra a anuência eletrônica do credor, transicionando o status para APPROVED.
        """
        cpr = self.get_cpr(cpr_id)

        if cpr.status != ApprovalStatus.PENDING_APPROVAL:
            raise AgroValidationError(
                f"CPR {cpr_id} não está pendente de anuência (status atual: {cpr.status.value})."
            )

        clean_doc = clean_digits(creditor_document)
        if clean_doc != cpr.creditor.clean_document:
            raise AgroValidationError(
                f"Documento do anuente ({creditor_document}) não confere com o credor registrado ({cpr.creditor.document})."
            )

        now = datetime.now(timezone.utc)
        if not signature_hash:
            payload = f"{cpr_id}:{clean_doc}:{approved_by}:{now.isoformat()}"
            signature_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        approval = ElectronicApproval(
            approved=True,
            approved_by=approved_by,
            creditor_document=clean_doc,
            timestamp=now,
            signature_hash=signature_hash,
            notes=notes,
        )

        cpr.approval = approval
        cpr.status = ApprovalStatus.APPROVED
        return cpr

    def reject_cpr(
        self,
        cpr_id: str,
        creditor_document: str,
        reason: str,
    ) -> CprRegistration:
        """
        Registra a recusa eletrônica da CPR pelo credor, transicionando o status para REJECTED.
        """
        cpr = self.get_cpr(cpr_id)

        if cpr.status != ApprovalStatus.PENDING_APPROVAL:
            raise AgroValidationError(
                f"CPR {cpr_id} não está pendente de anuência (status atual: {cpr.status.value})."
            )

        clean_doc = clean_digits(creditor_document)
        if clean_doc != cpr.creditor.clean_document:
            raise AgroValidationError(
                f"Documento de recusa ({creditor_document}) não confere com o credor registrado ({cpr.creditor.document})."
            )

        cpr.status = ApprovalStatus.REJECTED
        cpr.metadata["rejection_reason"] = reason
        cpr.metadata["rejected_at"] = datetime.now(timezone.utc).isoformat()
        return cpr

    def settle_cpr(
        self,
        cpr_id: str,
        settlement_price: Optional[Union[Decimal, float, str]] = None,
        quantity_delivered: Optional[Union[Decimal, float, str]] = None,
        receipt_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> SettlementRecord:
        """
        Executa a liquidação da CPR (Física ou Financeira), transicionando para SETTLED.

        Exige obrigatoriamente anuência prévia do credor (status APPROVED).
        """
        cpr = self.get_cpr(cpr_id)

        if cpr.status != ApprovalStatus.APPROVED:
            raise AgroSettlementError(
                f"Impossível liquidar CPR {cpr_id} com status {cpr.status.value}. "
                "A CPR deve estar previamente com anuência eletrônica (APPROVED)."
            )

        now = datetime.now(timezone.utc)

        if cpr.cpr_type == CprType.FINANCEIRA:
            ref_p = settlement_price if settlement_price is not None else cpr.unit_price
            if ref_p is None:
                raise AgroSettlementError(
                    f"Preço de liquidação é obrigatório para liquidar a CPR Financeira {cpr_id}."
                )

            final_price = Decimal(str(ref_p))
            total_val = cpr.calculate_financial_value(final_price)

            record = SettlementRecord(
                settled_at=now,
                settlement_type=CprType.FINANCEIRA,
                settlement_price=final_price,
                total_financial_value=total_val,
                quantity_delivered=None,
                receipt_identifier=receipt_id,
                notes=notes,
            )

        elif cpr.cpr_type == CprType.FISICA:
            qty = Decimal(str(quantity_delivered)) if quantity_delivered is not None else cpr.quantity
            if qty <= Decimal("0"):
                raise AgroSettlementError(
                    f"Quantidade entregue deve ser estritamente positiva: {qty}"
                )

            record = SettlementRecord(
                settled_at=now,
                settlement_type=CprType.FISICA,
                settlement_price=None,
                total_financial_value=None,
                quantity_delivered=qty,
                receipt_identifier=receipt_id or f"ROMANEIO-{cpr_id}-{now.strftime('%Y%m%d')}",
                notes=notes,
            )
        else:
            raise AgroSettlementError(f"Tipo de CPR não suportado para liquidação: {cpr.cpr_type}")

        cpr.settlement = record
        cpr.status = ApprovalStatus.SETTLED
        return record

    def get_cpr(self, cpr_id: str) -> CprRegistration:
        """Retorna registro da CPR ou levanta AgroValidationError."""
        if cpr_id not in self._cprs:
            raise AgroValidationError(f"CPR não encontrada: {cpr_id}")
        return self._cprs[cpr_id]

    def list_cprs(
        self,
        status: Optional[ApprovalStatus] = None,
        commodity: Optional[CommodityType] = None,
        issuer_document: Optional[str] = None,
        creditor_document: Optional[str] = None,
    ) -> List[CprRegistration]:
        """Filtra CPRs registradas no motor."""
        results = list(self._cprs.values())

        if status:
            results = [c for c in results if c.status == status]
        if commodity:
            results = [c for c in results if c.commodity == commodity]
        if issuer_document:
            clean_iss = clean_digits(issuer_document)
            results = [c for c in results if c.issuer.clean_document == clean_iss]
        if creditor_document:
            clean_cred = clean_digits(creditor_document)
            results = [c for c in results if c.creditor.clean_document == clean_cred]

        return results
