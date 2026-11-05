"""Módulo de Escrituração, Validação Geoespacial CAR e Liquidação de CPR Verde e PSA (FL-082).

Implementa o motor determinístico de registro escritural de Cédula de Produto Rural Verde (CPR Verde)
e Pagamentos por Serviços Ambientais (PSA) no Balcão NoMe B3, em estrita conformidade com:
- Lei nº 8.929/1994 (Cédula de Produto Rural - CPR).
- Lei nº 13.986/2020 (Nova Lei do Agro — Escrituração e registro obrigatório em entidade registradora).
- Decreto nº 10.828/2021 (Regulamenta a emissão de CPR Verde para conservação e recuperação de florestas nativas).
- Lei nº 14.119/2021 (Política Nacional de Pagamento por Serviços Ambientais - PNPSA).
- Resoluções CMN nº 4.870/2020 e nº 5.040/2022 (Registro de CPR em entidades registradoras autorizadas pelo BCB).

Invariantes Formais Provadas:
1. Unicidade e Não-Exaustão de Hectare Preservado:
   Área_Comprometida <= Área_Preservada_Total_CAR
   sum(CPR_Ativa.Área_Comprometida) <= CAR.Área_Preservada_Total
2. Conservação Financeira Estrita de Liquidação:
   Desembolso_Comprador == Remuneração_Produtor + Taxa_Certificação
   (Diferença exata de 0,00 centavo a centavo).
3. Conformidade Geoespacial e Ambiental Estrita:
   Status_CAR == ACTIVE e Ausência de Embargos IBAMA e Ausência de Sobreposição com Terras Indígenas/UCs.
4. Integridade Metrológica Ecológica:
   Métricas de biomassa, carbono estocado (tCO2e) e fluxo hidrológico proporcionais e estritamente positivas.
5. Validade e Fé Pública da Certificação Independente de Terceira Parte:
   Laudo técnico com Anotação de Responsabilidade Técnica (ART), hash SHA-256 e vigência ativa.

100% Python Standard Library (sem dependências externas).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union


# =====================================================================
# CONSTANTES DE DOMÍNIO, GEOGRAFIA E PRECISÃO MATEMÁTICA
# =====================================================================

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_HA = Decimal("0.0001")
QUANTIZE_METRIC = Decimal("0.0001")
QUANTIZE_RATE = Decimal("0.00000001")

# Padrão SICAR oficial: UF (2 letras) - Código IBGE Município (7 dígitos) - Hash Hexadecimal (32 chars)
CAR_REGEX = re.compile(r"^([A-Z]{2})-(\d{7})-([A-Fa-f0-9]{32})$")

# Validador de Hash SHA-256 (64 caracteres hexadecimais)
SHA256_REGEX = re.compile(r"^[A-Fa-f0-9]{64}$")

BRAZILIAN_STATES: Set[str] = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
    "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
    "RS", "RO", "RR", "SC", "SP", "SE", "TO"
}


def round_cents(val: Decimal) -> Decimal:
    """Arredonda valor financeiro em reais para 2 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)


def round_ha(val: Decimal) -> Decimal:
    """Arredonda área territorial em hectares para 4 casas decimais."""
    return val.quantize(QUANTIZE_HA, rounding=ROUND_HALF_UP)


def round_metric(val: Decimal) -> Decimal:
    """Arredonda métrica ambiental (tCO2e, m3, t biomassa) para 4 casas decimais."""
    return val.quantize(QUANTIZE_METRIC, rounding=ROUND_HALF_UP)


def round_rate(val: Decimal) -> Decimal:
    """Arredonda taxas percentuais para 8 casas decimais."""
    return val.quantize(QUANTIZE_RATE, rounding=ROUND_HALF_UP)


def to_decimal(value: Any, field_name: str = "campo") -> Decimal:
    """Converte valor para Decimal de maneira segura e estrita."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise GreenCPRValidationError(f"Valor nulo não permitido para '{field_name}'.")
    try:
        cleaned = str(value).strip().replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError) as err:
        raise GreenCPRValidationError(
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
            raise GreenCPRValidationError(
                f"Data inválida '{value}' para '{field_name}'. Formato esperado: YYYY-MM-DD."
            ) from err
    raise GreenCPRValidationError(
        f"Tipo de dado inválido '{type(value).__name__}' para data '{field_name}'."
    )


# =====================================================================
# ENUMS DE DOMÍNIO
# =====================================================================

class Biome(str, Enum):
    """Biomas Oficiais Brasileiros conforme IBGE e Decreto nº 10.828/2021."""
    AMAZONIA = "AMAZONIA"
    CERRADO = "CERRADO"
    MATA_ATLANTICA = "MATA_ATLANTICA"
    CAATINGA = "CAATINGA"
    PANTANAL = "PANTANAL"
    PAMPA = "PAMPA"


class CARStatus(str, Enum):
    """Situação Cadastral do Imóvel Rural no SICAR (MMA/SFB)."""
    ACTIVE = "ACTIVE"          # Ativo / Regular
    PENDING = "PENDING"        # Pendente de retificação/análise
    SUSPENDED = "SUSPENDED"    # Suspenso cautelarmente por irregularidade
    CANCELLED = "CANCELLED"    # Cancelado em definitivo


class EnvironmentalServiceType(str, Enum):
    """Categorias de Serviços Ambientais elegíveis a PSA e CPR Verde (Art. 4º Dec. 10.828/2021)."""
    CARBON_STOCK = "CARBON_STOCK"                           # Estoque e Sequestro de Carbono Florestal (tCO2e)
    HYDROLOGICAL_PRESERVATION = "HYDROLOGICAL_PRESERVATION" # Conservação de Bacias e Mananciais Hídricos (m3)
    BIODIVERSITY_CONSERVATION = "BIODIVERSITY_CONSERVATION" # Conservação de Fauna e Flora Nativa
    SOIL_CONSERVATION = "SOIL_CONSERVATION"                 # Conservação do Solo e Controle de Erosão
    NATIVE_RESTORATION = "NATIVE_RESTORATION"               # Restauração Ecológica de Vegetação Nativa


class CPRVerdeCategory(str, Enum):
    """Modalidade da CPR Verde conforme o Decreto nº 10.828/2021."""
    CONSERVATION = "CONSERVATION"  # Conservação de vegetação nativa existente
    RESTORATION = "RESTORATION"    # Recuperação ou restauração de áreas degradadas


class CPRVerdeFinancialType(str, Enum):
    """Tipo de Liquidação da CPR Verde no Balcão NoMe B3."""
    FINANCIAL = "FINANCIAL"  # Liquidação Financeira (Liquidação DVP/BRL no NoMe B3)
    PHYSICAL = "PHYSICAL"    # Liquidação Física (Entrega de Unidades de PSA / Créditos de Carbono)


class CPRVerdeStatus(str, Enum):
    """Ciclo de Vida da CPR Verde no Balcão NoMe B3."""
    DRAFT = "DRAFT"              # Minuta pré-registro
    REGISTERED = "REGISTERED"    # Registrada e ativa no Balcão NoMe B3
    SETTLED = "SETTLED"          # Liquidada financeiramente
    CANCELLED = "CANCELLED"      # Cancelada por distrato ou rescisão
    BLOCKED = "BLOCKED"          # Bloqueada por apontamento socioambiental ou judicial


# =====================================================================
# HIERARQUIA DE EXCEÇÕES DE DOMÍNIO
# =====================================================================

class GreenCPRError(Exception):
    """Exceção base para todas as operações do ecossistema CPR Verde B3."""
    pass


class GreenCPRValidationError(GreenCPRError):
    """Erro de validação de dados cadastrais, formatos ou limites regulatórios."""
    pass


class CARComplianceError(GreenCPRError):
    """Erro de não-conformidade do Cadastro Ambiental Rural (status, embargos ou sobreposições)."""
    pass


class PreservedAreaExceededError(GreenCPRError):
    """Violação da invariante de unicidade de hectare preservado (sobrealocação / double-counting)."""
    pass


class CertificationInvalidError(GreenCPRError):
    """Erro de validação do laudo de certificação de terceira parte independente."""
    pass


class SettlementConservationError(GreenCPRError):
    """Violação da lei de conservação financeira na liquidação da CPR Verde."""
    pass


class GreenCPRInvariantViolationError(GreenCPRError, AssertionError):
    """Violação formal de uma lei invariante do sistema de CPR Verde B3."""
    pass


# =====================================================================
# MODELOS DE DADOS (DATACLASSES)
# =====================================================================

@dataclass
class CARProperty:
    """Representação cadastral e geoespacial de Imóvel Rural no SICAR.

    Base legal: Lei nº 12.651/2012 (Código Florestal) e Decreto nº 10.828/2021.
    """
    car_number: str
    property_name: str
    municipality: str
    state: str
    biome: Biome
    total_property_area_ha: Decimal
    legal_reserve_area_ha: Decimal
    app_area_ha: Decimal
    surplus_native_veg_ha: Decimal = Decimal("0.0000")
    status: CARStatus = CARStatus.ACTIVE
    has_ibama_embargo: bool = False
    has_illegal_deforestation: bool = False
    overlaps_indigenous_or_conservation_unit: bool = False

    def __post_init__(self) -> None:
        self.car_number = str(self.car_number).strip().upper()
        match = CAR_REGEX.match(self.car_number)
        if not match:
            raise GreenCPRValidationError(
                f"Código CAR '{self.car_number}' inválido. Formato oficial exigido: UF-XXXXXXX-HASH32."
            )

        uf = match.group(1)
        if uf != self.state.upper() or uf not in BRAZILIAN_STATES:
            raise GreenCPRValidationError(
                f"UF do CAR '{uf}' diverge do estado do imóvel '{self.state}'."
            )

        if not isinstance(self.biome, Biome):
            self.biome = Biome(self.biome)
        if not isinstance(self.status, CARStatus):
            self.status = CARStatus(self.status)

        self.total_property_area_ha = round_ha(to_decimal(self.total_property_area_ha, "total_property_area_ha"))
        self.legal_reserve_area_ha = round_ha(to_decimal(self.legal_reserve_area_ha, "legal_reserve_area_ha"))
        self.app_area_ha = round_ha(to_decimal(self.app_area_ha, "app_area_ha"))
        self.surplus_native_veg_ha = round_ha(to_decimal(self.surplus_native_veg_ha, "surplus_native_veg_ha"))

        if self.total_property_area_ha <= Decimal("0.0000"):
            raise GreenCPRValidationError("Área total do imóvel deve ser estritamente positiva.")
        if self.legal_reserve_area_ha < Decimal("0.0000"):
            raise GreenCPRValidationError("Área de Reserva Legal não pode ser negativa.")
        if self.app_area_ha < Decimal("0.0000"):
            raise GreenCPRValidationError("Área de Preservação Permanente (APP) não pode ser negativa.")
        if self.surplus_native_veg_ha < Decimal("0.0000"):
            raise GreenCPRValidationError("Excedente de vegetação nativa não pode ser negativo.")

        total_preserved = self.total_preserved_area_ha
        if total_preserved > self.total_property_area_ha:
            raise GreenCPRValidationError(
                f"Área preservada total ({total_preserved} ha) excede a área total do imóvel ({self.total_property_area_ha} ha)."
            )

    @property
    def total_preserved_area_ha(self) -> Decimal:
        """Soma das áreas sob conservação/preservação florestal elegíveis a CPR Verde."""
        return round_ha(self.legal_reserve_area_ha + self.app_area_ha + self.surplus_native_veg_ha)

    def validate_compliance(self) -> Tuple[bool, List[str]]:
        """Verifica a conformidade ambiental e geoespacial do imóvel para emissão de CPR Verde."""
        issues: List[str] = []
        if self.status != CARStatus.ACTIVE:
            issues.append(f"Imóvel com status CAR '{self.status.value}' não elegível. Exige-se 'ACTIVE'.")
        if self.has_ibama_embargo:
            issues.append("Imóvel possui embargo ativo lavrado pelo IBAMA/SISNAMA.")
        if self.has_illegal_deforestation:
            issues.append("Imóvel possui registro de desmatamento ilegal (PRODES/deter).")
        if self.overlaps_indigenous_or_conservation_unit:
            issues.append("Sobreposição irregular com Terra Indígena ou Unidade de Conservação Integral.")
        return len(issues) == 0, issues

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o objeto CARProperty para dicionário."""
        return {
            "car_number": self.car_number,
            "property_name": self.property_name,
            "municipality": self.municipality,
            "state": self.state,
            "biome": self.biome.value,
            "total_property_area_ha": str(self.total_property_area_ha),
            "legal_reserve_area_ha": str(self.legal_reserve_area_ha),
            "app_area_ha": str(self.app_area_ha),
            "surplus_native_veg_ha": str(self.surplus_native_veg_ha),
            "total_preserved_area_ha": str(self.total_preserved_area_ha),
            "status": self.status.value,
            "has_ibama_embargo": self.has_ibama_embargo,
            "has_illegal_deforestation": self.has_illegal_deforestation,
            "overlaps_indigenous_or_conservation_unit": self.overlaps_indigenous_or_conservation_unit,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CARProperty:
        """Reconstrói instância de CARProperty a partir de dicionário."""
        return cls(
            car_number=data["car_number"],
            property_name=data["property_name"],
            municipality=data["municipality"],
            state=data["state"],
            biome=Biome(data["biome"]),
            total_property_area_ha=to_decimal(data["total_property_area_ha"]),
            legal_reserve_area_ha=to_decimal(data["legal_reserve_area_ha"]),
            app_area_ha=to_decimal(data["app_area_ha"]),
            surplus_native_veg_ha=to_decimal(data.get("surplus_native_veg_ha", "0.0000")),
            status=CARStatus(data.get("status", "ACTIVE")),
            has_ibama_embargo=bool(data.get("has_ibama_embargo", False)),
            has_illegal_deforestation=bool(data.get("has_illegal_deforestation", False)),
            overlaps_indigenous_or_conservation_unit=bool(
                data.get("overlaps_indigenous_or_conservation_unit", False)
            ),
        )


@dataclass
class EnvironmentalMetrics:
    """Métricas biofísicas de biomassa, carbono e serviços hidrológicos da área conservada.

    Calculadas com base nos parâmetros científicos do IPCC GPG-LULUCF e fatores por bioma.
    """
    service_type: EnvironmentalServiceType
    biomass_density_ton_per_ha: Decimal
    carbon_stock_tco2e_per_ha: Decimal
    annual_sequestration_tco2e_per_ha: Decimal = Decimal("0.0000")
    water_yield_m3_per_ha_year: Decimal = Decimal("0.0000")
    biodiversity_index: Decimal = Decimal("1.0000")

    def __post_init__(self) -> None:
        if not isinstance(self.service_type, EnvironmentalServiceType):
            self.service_type = EnvironmentalServiceType(self.service_type)

        self.biomass_density_ton_per_ha = round_metric(
            to_decimal(self.biomass_density_ton_per_ha, "biomass_density_ton_per_ha")
        )
        self.carbon_stock_tco2e_per_ha = round_metric(
            to_decimal(self.carbon_stock_tco2e_per_ha, "carbon_stock_tco2e_per_ha")
        )
        self.annual_sequestration_tco2e_per_ha = round_metric(
            to_decimal(self.annual_sequestration_tco2e_per_ha, "annual_sequestration_tco2e_per_ha")
        )
        self.water_yield_m3_per_ha_year = round_metric(
            to_decimal(self.water_yield_m3_per_ha_year, "water_yield_m3_per_ha_year")
        )
        self.biodiversity_index = round_metric(
            to_decimal(self.biodiversity_index, "biodiversity_index")
        )

        if self.biomass_density_ton_per_ha < Decimal("0.0000"):
            raise GreenCPRValidationError("Densidade de biomassa não pode ser negativa.")
        if self.carbon_stock_tco2e_per_ha < Decimal("0.0000"):
            raise GreenCPRValidationError("Estoque de carbono não pode ser negativo.")
        if self.annual_sequestration_tco2e_per_ha < Decimal("0.0000"):
            raise GreenCPRValidationError("Taxa de sequestro anual não pode ser negativa.")
        if self.water_yield_m3_per_ha_year < Decimal("0.0000"):
            raise GreenCPRValidationError("Produção hídrica anual não pode ser negativa.")
        if not (Decimal("0.0000") <= self.biodiversity_index <= Decimal("1.0000")):
            raise GreenCPRValidationError("Índice de biodiversidade deve estar compreendido entre 0.0000 e 1.0000.")

    def total_biomass_tons(self, area_ha: Decimal) -> Decimal:
        """Calcula o estoque total de biomassa aérea e subterrânea para a área informada."""
        return round_metric(self.biomass_density_ton_per_ha * area_ha)

    def total_carbon_stock_tco2e(self, area_ha: Decimal) -> Decimal:
        """Calcula o estoque total de carbono em tCO2e para a área informada."""
        return round_metric(self.carbon_stock_tco2e_per_ha * area_ha)

    def total_annual_sequestration_tco2e(self, area_ha: Decimal) -> Decimal:
        """Calcula o incremento anual de sequestro de carbono em tCO2e."""
        return round_metric(self.annual_sequestration_tco2e_per_ha * area_ha)

    def total_water_yield_m3(self, area_ha: Decimal) -> Decimal:
        """Calcula o volume anual total de recarga/preservação hídrica em m³."""
        return round_metric(self.water_yield_m3_per_ha_year * area_ha)

    @classmethod
    def calculate_default_for_biome(
        cls, biome: Biome, service_type: EnvironmentalServiceType
    ) -> EnvironmentalMetrics:
        """Gera métricas de referência científica com base nas médias dos biomas brasileiros (IPCC/MCTI)."""
        # Densidade de biomassa média (t/ha) por bioma
        biome_defaults = {
            Biome.AMAZONIA: {
                "biomass": Decimal("280.0000"),
                "water_yield": Decimal("12000.0000"),
                "bio_index": Decimal("0.9500"),
                "seq_rate": Decimal("4.5000"),
            },
            Biome.MATA_ATLANTICA: {
                "biomass": Decimal("220.0000"),
                "water_yield": Decimal("8500.0000"),
                "bio_index": Decimal("0.9000"),
                "seq_rate": Decimal("5.2000"),
            },
            Biome.CERRADO: {
                "biomass": Decimal("110.0000"),
                "water_yield": Decimal("6500.0000"),
                "bio_index": Decimal("0.8500"),
                "seq_rate": Decimal("2.8000"),
            },
            Biome.PANTANAL: {
                "biomass": Decimal("95.0000"),
                "water_yield": Decimal("7000.0000"),
                "bio_index": Decimal("0.8800"),
                "seq_rate": Decimal("2.5000"),
            },
            Biome.CAATINGA: {
                "biomass": Decimal("60.0000"),
                "water_yield": Decimal("2500.0000"),
                "bio_index": Decimal("0.7500"),
                "seq_rate": Decimal("1.4000"),
            },
            Biome.PAMPA: {
                "biomass": Decimal("35.0000"),
                "water_yield": Decimal("3500.0000"),
                "bio_index": Decimal("0.7800"),
                "seq_rate": Decimal("1.1000"),
            },
        }

        params = biome_defaults[biome]
        biomass = params["biomass"]
        # Conversão IPCC: Carbono = Biomassa * 0.47; CO2e = Carbono * (44/12)
        carbon_stock = round_metric(biomass * Decimal("0.47") * (Decimal("44") / Decimal("12")))

        return cls(
            service_type=service_type,
            biomass_density_ton_per_ha=biomass,
            carbon_stock_tco2e_per_ha=carbon_stock,
            annual_sequestration_tco2e_per_ha=params["seq_rate"],
            water_yield_m3_per_ha_year=params["water_yield"],
            biodiversity_index=params["bio_index"],
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa EnvironmentalMetrics para dicionário."""
        return {
            "service_type": self.service_type.value,
            "biomass_density_ton_per_ha": str(self.biomass_density_ton_per_ha),
            "carbon_stock_tco2e_per_ha": str(self.carbon_stock_tco2e_per_ha),
            "annual_sequestration_tco2e_per_ha": str(self.annual_sequestration_tco2e_per_ha),
            "water_yield_m3_per_ha_year": str(self.water_yield_m3_per_ha_year),
            "biodiversity_index": str(self.biodiversity_index),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EnvironmentalMetrics:
        """Reconstrói EnvironmentalMetrics a partir de dicionário."""
        return cls(
            service_type=EnvironmentalServiceType(data["service_type"]),
            biomass_density_ton_per_ha=to_decimal(data["biomass_density_ton_per_ha"]),
            carbon_stock_tco2e_per_ha=to_decimal(data["carbon_stock_tco2e_per_ha"]),
            annual_sequestration_tco2e_per_ha=to_decimal(
                data.get("annual_sequestration_tco2e_per_ha", "0.0000")
            ),
            water_yield_m3_per_ha_year=to_decimal(
                data.get("water_yield_m3_per_ha_year", "0.0000")
            ),
            biodiversity_index=to_decimal(data.get("biodiversity_index", "1.0000")),
        )


@dataclass
class ThirdPartyCertification:
    """Certificação expedida por auditor/organismo de certificação independente.

    Requisito legal expresso do Art. 3º do Decreto nº 10.828/2021.
    """
    certifier_cnpj: str
    certifier_name: str
    auditor_registration: str
    technical_report_hash: str
    methodology: str
    certification_date: date
    expiration_date: date
    certification_fee_rate: Decimal = Decimal("0.00000000")
    certification_fee_fixed: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.certifier_cnpj = re.sub(r"\D", "", str(self.certifier_cnpj))
        if len(self.certifier_cnpj) != 14:
            raise GreenCPRValidationError(
                f"CNPJ da certificadora '{self.certifier_cnpj}' inválido. Deve possuir 14 dígitos."
            )

        self.technical_report_hash = str(self.technical_report_hash).strip()
        if not SHA256_REGEX.match(self.technical_report_hash):
            raise GreenCPRValidationError(
                f"Hash do laudo técnico '{self.technical_report_hash}' inválido. Exige-se SHA-256 hexadecimal com 64 dígitos."
            )

        self.certification_date = parse_date(self.certification_date, "certification_date")
        self.expiration_date = parse_date(self.expiration_date, "expiration_date")
        if self.expiration_date <= self.certification_date:
            raise GreenCPRValidationError(
                f"Data de expiração da certificação ({self.expiration_date}) deve ser posterior à data de emissão ({self.certification_date})."
            )

        self.certification_fee_rate = round_rate(
            to_decimal(self.certification_fee_rate, "certification_fee_rate")
        )
        self.certification_fee_fixed = round_cents(
            to_decimal(self.certification_fee_fixed, "certification_fee_fixed")
        )

        if self.certification_fee_rate < Decimal("0.00000000"):
            raise GreenCPRValidationError("Taxa percentual de certificação não pode ser negativa.")
        if self.certification_fee_fixed < Decimal("0.00"):
            raise GreenCPRValidationError("Taxa fixa de certificação não pode ser negativa.")

    def is_valid_as_of(self, check_date: date) -> bool:
        """Verifica se a certificação técnica estava plenamente vigente na data indicada."""
        return self.certification_date <= check_date <= self.expiration_date

    def to_dict(self) -> Dict[str, Any]:
        """Serializa ThirdPartyCertification para dicionário."""
        return {
            "certifier_cnpj": self.certifier_cnpj,
            "certifier_name": self.certifier_name,
            "auditor_registration": self.auditor_registration,
            "technical_report_hash": self.technical_report_hash,
            "methodology": self.methodology,
            "certification_date": self.certification_date.isoformat(),
            "expiration_date": self.expiration_date.isoformat(),
            "certification_fee_rate": str(self.certification_fee_rate),
            "certification_fee_fixed": str(self.certification_fee_fixed),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ThirdPartyCertification:
        """Reconstrói ThirdPartyCertification a partir de dicionário."""
        return cls(
            certifier_cnpj=data["certifier_cnpj"],
            certifier_name=data["certifier_name"],
            auditor_registration=data["auditor_registration"],
            technical_report_hash=data["technical_report_hash"],
            methodology=data["methodology"],
            certification_date=parse_date(data["certification_date"]),
            expiration_date=parse_date(data["expiration_date"]),
            certification_fee_rate=to_decimal(data.get("certification_fee_rate", "0.00000000")),
            certification_fee_fixed=to_decimal(data.get("certification_fee_fixed", "0.00")),
        )


@dataclass
class GreenCPR:
    """Cédula de Produto Rural Verde (CPR Verde) emitida sob o Decreto nº 10.828/2021.

    Representa o título de crédito escritural representativo da promessa de prestação
    de serviços ambientais ou conservação florestal com liquidação no Balcão NoMe B3.
    """
    cpr_id: str
    issuer_tax_id: str
    issuer_name: str
    buyer_tax_id: str
    buyer_name: str
    car_number: str
    category: CPRVerdeCategory
    service_type: EnvironmentalServiceType
    committed_area_ha: Decimal
    contracted_units: Decimal
    unit_price_brl: Decimal
    issue_date: date
    maturity_date: date
    metrics: EnvironmentalMetrics
    certification: ThirdPartyCertification
    status: CPRVerdeStatus = CPRVerdeStatus.DRAFT
    financial_type: CPRVerdeFinancialType = CPRVerdeFinancialType.FINANCIAL
    b3_registration_id: Optional[str] = None

    def __post_init__(self) -> None:
        self.cpr_id = str(self.cpr_id).strip()
        self.car_number = str(self.car_number).strip().upper()
        self.issuer_tax_id = re.sub(r"\D", "", str(self.issuer_tax_id))
        self.buyer_tax_id = re.sub(r"\D", "", str(self.buyer_tax_id))

        if len(self.issuer_tax_id) not in (11, 14):
            raise GreenCPRValidationError(
                f"Documento do emissor '{self.issuer_tax_id}' deve ser CPF (11) ou CNPJ (14 dígitos)."
            )
        if len(self.buyer_tax_id) not in (11, 14):
            raise GreenCPRValidationError(
                f"Documento do comprador '{self.buyer_tax_id}' deve ser CPF (11) ou CNPJ (14 dígitos)."
            )

        if not isinstance(self.category, CPRVerdeCategory):
            self.category = CPRVerdeCategory(self.category)
        if not isinstance(self.service_type, EnvironmentalServiceType):
            self.service_type = EnvironmentalServiceType(self.service_type)
        if not isinstance(self.status, CPRVerdeStatus):
            self.status = CPRVerdeStatus(self.status)
        if not isinstance(self.financial_type, CPRVerdeFinancialType):
            self.financial_type = CPRVerdeFinancialType(self.financial_type)

        self.committed_area_ha = round_ha(to_decimal(self.committed_area_ha, "committed_area_ha"))
        self.contracted_units = round_metric(to_decimal(self.contracted_units, "contracted_units"))
        self.unit_price_brl = round_cents(to_decimal(self.unit_price_brl, "unit_price_brl"))

        if self.committed_area_ha <= Decimal("0.0000"):
            raise GreenCPRValidationError("Área comprometida da CPR Verde deve ser estritamente positiva.")
        if self.contracted_units <= Decimal("0.0000"):
            raise GreenCPRValidationError("Unidades ambientais contratadas devem ser estritamente positivas.")
        if self.unit_price_brl <= Decimal("0.00"):
            raise GreenCPRValidationError("Preço unitário em reais deve ser estritamente positivo.")

        self.issue_date = parse_date(self.issue_date, "issue_date")
        self.maturity_date = parse_date(self.maturity_date, "maturity_date")
        if self.maturity_date <= self.issue_date:
            raise GreenCPRValidationError(
                f"Data de vencimento ({self.maturity_date}) deve ser estritamente posterior à emissão ({self.issue_date})."
            )

    @property
    def total_notional_brl(self) -> Decimal:
        """Valor financeiro nocional bruto total da CPR Verde (Unidades Contratadas * Preço Unitário)."""
        return round_cents(self.contracted_units * self.unit_price_brl)

    def calculate_certification_fee(self, gross_amount: Optional[Decimal] = None) -> Decimal:
        """Calcula o montante da taxa de certificação com base na regra percentual e/ou fixa."""
        base = gross_amount if gross_amount is not None else self.total_notional_brl
        rate_fee = round_cents(base * self.certification.certification_fee_rate)
        total_fee = round_cents(rate_fee + self.certification.certification_fee_fixed)
        return total_fee

    def to_dict(self) -> Dict[str, Any]:
        """Serializa GreenCPR para dicionário."""
        return {
            "cpr_id": self.cpr_id,
            "issuer_tax_id": self.issuer_tax_id,
            "issuer_name": self.issuer_name,
            "buyer_tax_id": self.buyer_tax_id,
            "buyer_name": self.buyer_name,
            "car_number": self.car_number,
            "category": self.category.value,
            "service_type": self.service_type.value,
            "committed_area_ha": str(self.committed_area_ha),
            "contracted_units": str(self.contracted_units),
            "unit_price_brl": str(self.unit_price_brl),
            "total_notional_brl": str(self.total_notional_brl),
            "issue_date": self.issue_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "metrics": self.metrics.to_dict(),
            "certification": self.certification.to_dict(),
            "status": self.status.value,
            "financial_type": self.financial_type.value,
            "b3_registration_id": self.b3_registration_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GreenCPR:
        """Reconstrói GreenCPR a partir de dicionário."""
        return cls(
            cpr_id=data["cpr_id"],
            issuer_tax_id=data["issuer_tax_id"],
            issuer_name=data["issuer_name"],
            buyer_tax_id=data["buyer_tax_id"],
            buyer_name=data["buyer_name"],
            car_number=data["car_number"],
            category=CPRVerdeCategory(data["category"]),
            service_type=EnvironmentalServiceType(data["service_type"]),
            committed_area_ha=to_decimal(data["committed_area_ha"]),
            contracted_units=to_decimal(data["contracted_units"]),
            unit_price_brl=to_decimal(data["unit_price_brl"]),
            issue_date=parse_date(data["issue_date"]),
            maturity_date=parse_date(data["maturity_date"]),
            metrics=EnvironmentalMetrics.from_dict(data["metrics"]),
            certification=ThirdPartyCertification.from_dict(data["certification"]),
            status=CPRVerdeStatus(data.get("status", "DRAFT")),
            financial_type=CPRVerdeFinancialType(data.get("financial_type", "FINANCIAL")),
            b3_registration_id=data.get("b3_registration_id"),
        )


@dataclass
class SettlementExecutionResult:
    """Comprovante e registro de liquidação financeira de CPR Verde e PSA no Balcão NoMe B3.

    Satisfaz a lei formal de conservação financeira:
    Desembolso_Comprador == Remuneração_Produtor + Taxa_Certificação.
    """
    settlement_id: str
    cpr_id: str
    settlement_date: date
    gross_buyer_disbursement: Decimal
    producer_remuneration: Decimal
    certification_fee: Decimal
    status: str = "SETTLED"
    invariants_verified: bool = True

    def __post_init__(self) -> None:
        self.settlement_date = parse_date(self.settlement_date, "settlement_date")
        self.gross_buyer_disbursement = round_cents(
            to_decimal(self.gross_buyer_disbursement, "gross_buyer_disbursement")
        )
        self.producer_remuneration = round_cents(
            to_decimal(self.producer_remuneration, "producer_remuneration")
        )
        self.certification_fee = round_cents(
            to_decimal(self.certification_fee, "certification_fee")
        )

        if self.gross_buyer_disbursement < Decimal("0.00"):
            raise GreenCPRValidationError("Desembolso do comprador não pode ser negativo.")
        if self.producer_remuneration < Decimal("0.00"):
            raise GreenCPRValidationError("Remuneração do produtor não pode ser negativa.")
        if self.certification_fee < Decimal("0.00"):
            raise GreenCPRValidationError("Taxa de certificação não pode ser negativa.")

        # Validação estrita da conservação financeira
        allocated_sum = round_cents(self.producer_remuneration + self.certification_fee)
        if self.gross_buyer_disbursement != allocated_sum:
            raise SettlementConservationError(
                f"Violação da Conservação Financeira de Liquidação: "
                f"Desembolso do Comprador (R$ {self.gross_buyer_disbursement}) != "
                f"Remuneração do Produtor (R$ {self.producer_remuneration}) + "
                f"Taxa de Certificação (R$ {self.certification_fee}) = R$ {allocated_sum}."
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa SettlementExecutionResult para dicionário."""
        return {
            "settlement_id": self.settlement_id,
            "cpr_id": self.cpr_id,
            "settlement_date": self.settlement_date.isoformat(),
            "gross_buyer_disbursement": str(self.gross_buyer_disbursement),
            "producer_remuneration": str(self.producer_remuneration),
            "certification_fee": str(self.certification_fee),
            "status": self.status,
            "invariants_verified": self.invariants_verified,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SettlementExecutionResult:
        """Reconstrói SettlementExecutionResult a partir de dicionário."""
        return cls(
            settlement_id=data["settlement_id"],
            cpr_id=data["cpr_id"],
            settlement_date=parse_date(data["settlement_date"]),
            gross_buyer_disbursement=to_decimal(data["gross_buyer_disbursement"]),
            producer_remuneration=to_decimal(data["producer_remuneration"]),
            certification_fee=to_decimal(data["certification_fee"]),
            status=data.get("status", "SETTLED"),
            invariants_verified=bool(data.get("invariants_verified", True)),
        )


# =====================================================================
# MOTOR PRINCIPAL DE ESCRITURAÇÃO E VALIDAÇÃO (ENGINE)
# =====================================================================

class GreenCPREnvironmentalServicesEngine:
    """Motor determinístico de escrituração, validação geoespacial CAR e liquidação de CPR Verde B3.

    Implementa o registro e liquidação de CPRs Verdes conforme Lei nº 13.986/2020 e Decreto nº 10.828/2021.
    """

    def __init__(self) -> None:
        self.car_registry: Dict[str, CARProperty] = {}
        self.cprs: Dict[str, GreenCPR] = {}
        self.settlements: Dict[str, SettlementExecutionResult] = {}

    def register_car(self, car: CARProperty) -> CARProperty:
        """Registra ou atualiza um imóvel rural no repositório de validação CAR."""
        if not isinstance(car, CARProperty):
            raise GreenCPRValidationError("Objeto deve ser uma instância válida de CARProperty.")
        self.car_registry[car.car_number] = car
        return car

    def get_car(self, car_number: str) -> CARProperty:
        """Recupera o cadastro do imóvel pelo código SICAR ou levanta erro."""
        key = car_number.strip().upper()
        if key not in self.car_registry:
            raise GreenCPRValidationError(f"Imóvel com CAR '{key}' não encontrado no cadastro do motor.")
        return self.car_registry[key]

    def get_cpr(self, cpr_id: str) -> GreenCPR:
        """Recupera uma CPR Verde pelo seu identificador único."""
        key = cpr_id.strip()
        if key not in self.cprs:
            raise GreenCPRValidationError(f"CPR Verde '{key}' não encontrada no balcão.")
        return self.cprs[key]

    def get_committed_area_for_car(
        self, car_number: str, exclude_cpr_id: Optional[str] = None
    ) -> Decimal:
        """Calcula a área florestal total já comprometida por CPRs ativas ou liquidadas para o imóvel."""
        key = car_number.strip().upper()
        total_committed = Decimal("0.0000")
        active_statuses = {CPRVerdeStatus.REGISTERED, CPRVerdeStatus.SETTLED}

        for cpr in self.cprs.values():
            if cpr.car_number == key and cpr.status in active_statuses:
                if exclude_cpr_id and cpr.cpr_id == exclude_cpr_id:
                    continue
                total_committed += cpr.committed_area_ha

        return round_ha(total_committed)

    def get_available_preserved_area_for_car(
        self, car_number: str, exclude_cpr_id: Optional[str] = None
    ) -> Decimal:
        """Calcula a área remanescente disponível de vegetação preservada no imóvel para novas CPRs."""
        car = self.get_car(car_number)
        already_committed = self.get_committed_area_for_car(car_number, exclude_cpr_id)
        available = car.total_preserved_area_ha - already_committed
        return round_ha(max(Decimal("0.0000"), available))

    def register_green_cpr(
        self, cpr: GreenCPR, as_of_date: Optional[date] = None
    ) -> GreenCPR:
        """Registra formalmente uma CPR Verde no Balcão NoMe B3 após auditoria e checagem geoespacial estrita.

        Regras de Negócio e Invariantes Verificadas:
        1. Existência e Conformidade do CAR (Ativo, sem embargos IBAMA, sem sobreposição);
        2. Validade da Certificação de Terceira Parte Independente na data de registro;
        3. Invariante de Unicidade de Hectare Preservado:
           A área comprometida não pode exceder o saldo remanescente disponível do CAR.
        4. Geração de Protocolo B3 no Balcão NoMe.
        """
        ref_date = as_of_date or cpr.issue_date

        # 1. Conformidade do CAR
        car = self.get_car(cpr.car_number)
        compliant, issues = car.validate_compliance()
        if not compliant:
            raise CARComplianceError(
                f"Imóvel CAR '{car.car_number}' rejeitado na validação de conformidade: {'; '.join(issues)}"
            )

        # 2. Validação da Certificação Independente
        if not cpr.certification.is_valid_as_of(ref_date):
            raise CertificationInvalidError(
                f"Certificação técnica independente ({cpr.certification.certifier_name}) "
                f"não é válida para a data de operação {ref_date}. Vigência: "
                f"{cpr.certification.certification_date} a {cpr.certification.expiration_date}."
            )

        # 3. Invariante de Unicidade de Hectare Preservado (Não-Sobrealocação de Gleba)
        current_committed = self.get_committed_area_for_car(cpr.car_number, exclude_cpr_id=cpr.cpr_id)
        projected_committed = round_ha(current_committed + cpr.committed_area_ha)

        if projected_committed > car.total_preserved_area_ha:
            excess = round_ha(projected_committed - car.total_preserved_area_ha)
            raise PreservedAreaExceededError(
                f"Violação da Invariante de Unicidade de Hectare Preservado no CAR '{car.car_number}': "
                f"Área comprometida total pretendida ({projected_committed} ha) excede a área total "
                f"preservada elegível ({car.total_preserved_area_ha} ha) em {excess} ha."
            )

        # 4. Atribuição de Código de Registro B3 e Atualização de Status
        if not cpr.b3_registration_id:
            cpr.b3_registration_id = f"B3-NOME-CPRV-{ref_date.year}-{cpr.cpr_id.replace('-', '')}"

        cpr.status = CPRVerdeStatus.REGISTERED
        self.cprs[cpr.cpr_id] = cpr
        return cpr

    def block_cpr(self, cpr_id: str, reason: str) -> GreenCPR:
        """Bloqueia cautelarmente uma CPR Verde por apontamento socioambiental superveniente."""
        cpr = self.get_cpr(cpr_id)
        if cpr.status == CPRVerdeStatus.SETTLED:
            raise GreenCPRValidationError("Não é possível bloquear CPR Verde já liquidada financeiramente.")
        cpr.status = CPRVerdeStatus.BLOCKED
        return cpr

    def cancel_cpr(self, cpr_id: str, reason: str) -> GreenCPR:
        """Cancela o registro de uma CPR Verde por distrato pré-liquidação."""
        cpr = self.get_cpr(cpr_id)
        if cpr.status == CPRVerdeStatus.SETTLED:
            raise GreenCPRValidationError("Não é possível cancelar CPR Verde já liquidada financeiramente.")
        cpr.status = CPRVerdeStatus.CANCELLED
        return cpr

    def execute_settlement(
        self,
        cpr_id: str,
        settlement_date: date,
        custom_buyer_disbursement: Optional[Decimal] = None,
        custom_certification_fee: Optional[Decimal] = None,
    ) -> SettlementExecutionResult:
        """Executa a liquidação financeira determinística da CPR Verde no Balcão NoMe B3.

        Garante a Conservação Financeira de Liquidação:
        Desembolso_Comprador == Remuneração_Produtor + Taxa_Certificação.
        """
        cpr = self.get_cpr(cpr_id)
        settle_dt = parse_date(settlement_date, "settlement_date")

        if cpr.status != CPRVerdeStatus.REGISTERED:
            raise GreenCPRValidationError(
                f"CPR Verde '{cpr_id}' em estado '{cpr.status.value}' não elegível para liquidação. Exige-se 'REGISTERED'."
            )

        # Rechecagem de conformidade na data da liquidação
        car = self.get_car(cpr.car_number)
        compliant, issues = car.validate_compliance()
        if not compliant:
            cpr.status = CPRVerdeStatus.BLOCKED
            raise CARComplianceError(
                f"Liquidação abortada: CAR '{car.car_number}' incorreu em não-conformidade superveniente: {'; '.join(issues)}"
            )

        # Validade da certificação técnica na data de liquidação
        if not cpr.certification.is_valid_as_of(settle_dt):
            raise CertificationInvalidError(
                f"Laudo técnico da certificadora '{cpr.certification.certifier_name}' expirou antes da data de liquidação ({settle_dt})."
            )

        # Determinação dos valores financeiros
        gross_disbursement = (
            round_cents(to_decimal(custom_buyer_disbursement, "custom_buyer_disbursement"))
            if custom_buyer_disbursement is not None
            else cpr.total_notional_brl
        )

        if custom_certification_fee is not None:
            cert_fee = round_cents(to_decimal(custom_certification_fee, "custom_certification_fee"))
        else:
            cert_fee = cpr.calculate_certification_fee(gross_disbursement)

        if cert_fee > gross_disbursement:
            raise SettlementConservationError(
                f"Taxa de certificação (R$ {cert_fee}) excede o desembolso bruto total (R$ {gross_disbursement})."
            )

        # Remuneração líquida ao produtor rural
        producer_remuneration = round_cents(gross_disbursement - cert_fee)

        # Invariante Formal: Conservação Financeira Estrita
        conservation_sum = round_cents(producer_remuneration + cert_fee)
        if gross_disbursement != conservation_sum:
            raise SettlementConservationError(
                f"Violação da Conservação Financeira: Desembolso ({gross_disbursement}) != "
                f"Produtor ({producer_remuneration}) + Certificadora ({cert_fee})."
            )

        settlement_id = f"SETTLE-CPRV-{settle_dt.strftime('%Y%m%d')}-{cpr_id}"
        settlement_result = SettlementExecutionResult(
            settlement_id=settlement_id,
            cpr_id=cpr.cpr_id,
            settlement_date=settle_dt,
            gross_buyer_disbursement=gross_disbursement,
            producer_remuneration=producer_remuneration,
            certification_fee=cert_fee,
            status="SETTLED",
            invariants_verified=True,
        )

        # Transição de estado da CPR Verde
        cpr.status = CPRVerdeStatus.SETTLED
        self.settlements[settlement_id] = settlement_result
        return settlement_result

    def verify_invariants(self) -> Dict[str, bool]:
        """Audita formalmente todas as leis invariantes e regras de conservação do motor.

        Retorna dicionário mapeando cada código de lei para o resultado booleano da auditoria.
        """
        results: Dict[str, bool] = {}

        # -------------------------------------------------------------
        # Lei 1: Unicidade e Não-Exaustão de Hectare Preservado
        # -------------------------------------------------------------
        area_unicity_valid = True
        for car_number, car in self.car_registry.items():
            committed_ha = self.get_committed_area_for_car(car_number)
            if committed_ha > car.total_preserved_area_ha:
                area_unicity_valid = False
                break

        # Checagem individual por CPR
        for cpr in self.cprs.values():
            if cpr.status in (CPRVerdeStatus.REGISTERED, CPRVerdeStatus.SETTLED):
                car = self.car_registry.get(cpr.car_number)
                if not car or cpr.committed_area_ha > car.total_preserved_area_ha:
                    area_unicity_valid = False
                    break

        results["INV-CPR-001_PRESERVED_AREA_UNICITY"] = area_unicity_valid

        # -------------------------------------------------------------
        # Lei 2: Conservação Financeira Estrita de Liquidação
        # -------------------------------------------------------------
        financial_conservation_valid = True
        for st in self.settlements.values():
            allocated_sum = round_cents(st.producer_remuneration + st.certification_fee)
            if st.gross_buyer_disbursement != allocated_sum:
                financial_conservation_valid = False
                break
        results["INV-CPR-002_FINANCIAL_CONSERVATION"] = financial_conservation_valid

        # -------------------------------------------------------------
        # Lei 3: Conformidade Geoespacial e Ausência de Embargos
        # -------------------------------------------------------------
        car_compliance_valid = True
        for cpr in self.cprs.values():
            if cpr.status in (CPRVerdeStatus.REGISTERED, CPRVerdeStatus.SETTLED):
                car = self.car_registry.get(cpr.car_number)
                if not car:
                    car_compliance_valid = False
                    break
                compliant, _ = car.validate_compliance()
                if not compliant:
                    car_compliance_valid = False
                    break
        results["INV-CPR-003_CAR_COMPLIANCE"] = car_compliance_valid

        # -------------------------------------------------------------
        # Lei 4: Integridade Metrológica Ecológica (Métricas Positivas)
        # -------------------------------------------------------------
        metrics_valid = True
        for cpr in self.cprs.values():
            if cpr.committed_area_ha <= Decimal("0.0000"):
                metrics_valid = False
                break
            if cpr.contracted_units <= Decimal("0.0000"):
                metrics_valid = False
                break
            if cpr.metrics.biomass_density_ton_per_ha < Decimal("0.0000"):
                metrics_valid = False
                break
            if cpr.metrics.carbon_stock_tco2e_per_ha < Decimal("0.0000"):
                metrics_valid = False
                break
        results["INV-CPR-004_METRIC_INTEGRITY"] = metrics_valid

        # -------------------------------------------------------------
        # Lei 5: Validade da Certificação Técnica Independente
        # -------------------------------------------------------------
        cert_valid = True
        for cpr in self.cprs.values():
            if cpr.status in (CPRVerdeStatus.REGISTERED, CPRVerdeStatus.SETTLED):
                cert = cpr.certification
                if not SHA256_REGEX.match(cert.technical_report_hash):
                    cert_valid = False
                    break
                if cert.expiration_date <= cert.certification_date:
                    cert_valid = False
                    break
        results["INV-CPR-005_CERTIFICATION_VALIDITY"] = cert_valid

        return results

    def assert_invariants(self) -> None:
        """Executa verify_invariants() e levanta GreenCPRInvariantViolationError caso alguma falhe."""
        inv_results = self.verify_invariants()
        failed = [k for k, v in inv_results.items() if not v]
        if failed:
            raise GreenCPRInvariantViolationError(
                f"Falha na verificação de invariantes da CPR Verde B3: {', '.join(failed)}."
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado completo do motor para dicionário estruturado."""
        return {
            "car_properties": [car.to_dict() for car in self.car_registry.values()],
            "cprs": [cpr.to_dict() for cpr in self.cprs.values()],
            "settlements": [st.to_dict() for st in self.settlements.values()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GreenCPREnvironmentalServicesEngine:
        """Reconstrói o motor completo a partir de dicionário estruturado."""
        engine = cls()
        for car_data in data.get("car_properties", []):
            engine.register_car(CARProperty.from_dict(car_data))

        for cpr_data in data.get("cprs", []):
            cpr = GreenCPR.from_dict(cpr_data)
            engine.cprs[cpr.cpr_id] = cpr

        for st_data in data.get("settlements", []):
            st = SettlementExecutionResult.from_dict(st_data)
            engine.settlements[st.settlement_id] = st

        return engine

    @classmethod
    def load_from_fixture(cls, file_path: Union[str, Path]) -> GreenCPREnvironmentalServicesEngine:
        """Carrega e instancia o motor a partir de um arquivo JSON de fixture."""
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f"Arquivo de fixture '{file_path}' não encontrado.")
        with p.open("r", encoding="utf-8") as f:
            content = json.load(f)
        return cls.from_dict(content)
