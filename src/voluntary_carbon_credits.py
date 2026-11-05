"""Módulo de Registro Escritural, Custódia Segregada, Negociação e Aposentadoria de Créditos de Carbono (FL-088).

Implementa o motor determinístico do Mercado Voluntário de Carbono (Voluntary Carbon Market - VCM)
na Plataforma de Carbono B3 e Balcão NoMe, em estrita conformidade com os padrões globais e regulamentação brasileira:
- Padrões Internacionais: VCS (Verified Carbon Standard / Verra), Gold Standard (GS), ACR (American Carbon Registry),
  CAR (Climate Action Reserve), Cercarbono e Padrão de Carbono B3.
- Tipos de Projetos: REDD+ (Desmatamento Evitado e Conservação), ARR (Aforamento, Reflorestamento e Revegetação),
  Bioenergia/Biomassa, Energia Renovável e DAC (Captura Direta de Ar).
- Lei nº 12.187/2009 (Política Nacional sobre Mudança do Clima - PNMC) e Lei nº 14.119/2021 (Pagamento por Serviços Ambientais).
- Padrão GHG Protocol Corporate Standard (Compensação de Emissões de Escopos 1, 2 e 3).
- Registro escritural segregado, rastreabilidade de vintage, intervalo serial único e prevenção formal de dupla contagem.
- Liquidação financeira Delivery versus Payment (DvP) no Balcão B3.
- Aposentadoria irrevogável (Retirement) com geração de Certidão Criptográfica SHA-256 de Compensação Corporativa.

Invariantes Formais Provadas:
1. INV-VCM-001_ANTI_DOUBLE_COUNTING:
   - Unicidade e não sobreposição de números seriais internacionais por padrão e projeto.
   - Conservação global de estoque: Total_Emitido == Em_Circulação (Disponível) + Bloqueados + Aposentados.
   - Reconciliação exata entre saldos dos lotes e soma das posições de custódia segregada de todos os participantes.
2. INV-VCM-002_NON_NEGATIVE_BALANCES:
   - Não-negatividade estrita de saldos disponíveis, bloqueados e aposentados em todas as contas e lotes.
3. INV-VCM-003_DVP_FINANCIAL_SETTLEMENT:
   - Conservação financeira estrita: Desembolso do Comprador == Recebimento Líquido do Vendedor + Taxas B3.
   - Valor Bruto da Negociação == Quantidade (tCO2e) * Preço Unitário (BRL).
   - Atomicidade estrita na transferência de titularidade de créditos.
4. INV-VCM-004_IRREVOCABLE_RETIREMENT:
   - Aposentadoria definitiva irrevogável: créditos aposentados jamais retornam à circulação ativa.
   - Integridade da Certidão de Aposentadoria com Hash Criptográfico SHA-256.
   - Soma do volume de certidões emitidas iguala exatamente o saldo aposentado do lote.
5. INV-VCM-005_METRIC_PARITY:
   - Paridade metrológica estrita: 1 crédito de carbono == 1,00 tCO2e evitada ou sequestrada.
   - Fracionamento com precisão decimal exata.

100% Python Standard Library (sem dependências externas).
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
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union


# =====================================================================
# CONSTANTES DE PRECISÃO, METROLOGIA E DOMÍNIO VCM / B3
# =====================================================================

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_TCO2E = Decimal("0.01")
QUANTIZE_RATE = Decimal("0.000001")

# Paridade: 1 crédito de carbono = 1,00 tonelada métrica de CO2 equivalente
TCO2E_PER_CREDIT = Decimal("1.00")

# Expressão regular para hash SHA-256 (64 hexadecimais)
SHA256_REGEX = re.compile(r"^[A-Fa-f0-9]{64}$")

# Expressão regular para validação de CNPJ (14 dígitos numéricos)
CNPJ_REGEX = re.compile(r"^\d{14}$")

# Expressão regular para validação de CPF (11 dígitos numéricos)
CPF_REGEX = re.compile(r"^\d{11}$")


# =====================================================================
# FUNÇÕES UTILITÁRIAS DE CONVERSÃO E ARREDONDAMENTO DETERMINÍSTICO
# =====================================================================

def to_decimal(value: Union[int, float, str, Decimal]) -> Decimal:
    """Converte valores numéricos para Decimal de alta precisão."""
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as err:
        raise CarbonValidationError(f"Valor numérico inválido para Decimal: {value}") from err


def round_cents(value: Union[int, float, str, Decimal]) -> Decimal:
    """Arredonda valor monetário em BRL para 2 casas decimais (centavos)."""
    return to_decimal(value).quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)


def round_tco2e(value: Union[int, float, str, Decimal]) -> Decimal:
    """Arredonda volume de carbono em tCO2e para 2 casas decimais."""
    return to_decimal(value).quantize(QUANTIZE_TCO2E, rounding=ROUND_HALF_UP)


def sanitize_document(doc: str) -> str:
    """Remove pontuação de CNPJ ou CPF para consistência operacional."""
    return re.sub(r"[.\-/]", "", str(doc).strip())


# =====================================================================
# HIERARQUIA DE EXCEÇÕES DO MOTOR VCM B3
# =====================================================================

class VoluntaryCarbonCreditError(Exception):
    """Exceção base para o motor de créditos de carbono voluntário B3."""


class CarbonValidationError(VoluntaryCarbonCreditError):
    """Erro de validação cadastral, parâmetros ou dados obrigatórios."""


class DoubleCountingError(VoluntaryCarbonCreditError):
    """Violação do princípio de não-dupla contagem ou colisão de número serial."""


class InsufficientCarbonBalanceError(VoluntaryCarbonCreditError):
    """Saldo insuficiente em conta de custódia para débito, bloqueio ou aposentadoria."""


class InvalidRetirementError(VoluntaryCarbonCreditError):
    """Operação inválida de aposentadoria ou tentativa de modificar crédito aposentado."""


class DvPSettlementError(VoluntaryCarbonCreditError):
    """Falha de liquidação financeira ou inconsistência matemática no DvP."""


class CarbonInvariantViolationError(VoluntaryCarbonCreditError, AssertionError):
    """Falha na verificação formal de leis invariantes e regras de conservação."""


# =====================================================================
# ENUMERAÇÕES DO DOMÍNIO
# =====================================================================

class CertificationStandard(str, Enum):
    """Padrões internacionais e nacionais de certificação de créditos de carbono."""
    VCS = "VCS"                     # Verified Carbon Standard (Verra)
    GOLD_STANDARD = "GOLD_STANDARD" # Gold Standard for the Global Goals (GS)
    ACR = "ACR"                     # American Carbon Registry (Winrock)
    CAR = "CAR"                     # Climate Action Reserve
    CERCARBONO = "CERCARBONO"       # Cercarbono Certified Carbon Standard
    PADRAO_B3 = "PADRAO_B3"         # Plataforma e Metodologia de Carbono B3


class CarbonProjectType(str, Enum):
    """Tipologia setorial de projetos geradores de créditos de carbono."""
    REDD_PLUS = "REDD_PLUS"                 # Desmatamento Evitado e Conservação Florestal
    ARR = "ARR"                             # Aforamento, Reflorestamento e Revegetação
    BIOENERGY_BIOMASS = "BIOENERGY_BIOMASS" # Bioenergia, Resíduos Agroindustriais e Biomassa
    RENEWABLE_ENERGY = "RENEWABLE_ENERGY"   # Energia Renovável (Solar, Eólica, PCH)
    DAC = "DAC"                             # Captura Direta de Ar (Direct Air Capture & Storage)


class CarbonOffsetScope(str, Enum):
    """Escopos de emissão corporativa conforme GHG Protocol Corporate Standard."""
    SCOPE_1 = "SCOPE_1"                         # Emissões Diretas da Operação Corporativa
    SCOPE_2 = "SCOPE_2"                         # Emissões Indiretas por Consumo Elétrico ou Térmico
    SCOPE_3 = "SCOPE_3"                         # Emissões Indiretas da Cadeia de Valor e Fornecedores
    NET_ZERO_CORPORATE = "NET_ZERO_CORPORATE"   # Neutralização Integrada Multi-Escopo (Net Zero)


class CarbonCreditStatus(str, Enum):
    """Ciclo de vida operacional e registral do lote de crédito de carbono."""
    ISSUED = "ISSUED"           # Emitido pelo standard internacional e registrado na B3
    ACTIVE = "ACTIVE"           # Em circulação ativa para custódia e negociação secundária
    LOCKED = "LOCKED"           # Totalmente ou parcialmente bloqueado em garantia / DvP
    RETIRED = "RETIRED"         # Aposentado definitivamente para compensação corporativa
    CANCELLED = "CANCELLED"     # Cancelado / Invalidado no standard de origem


class CustodyAccountType(str, Enum):
    """Classificação e natureza jurídica da conta de custódia segregada B3."""
    PROPRIETARY = "PROPRIETARY"                   # Carteira Própria da Instituição Participante
    CLIENT = "CLIENT"                             # Conta Individualizada de Cliente Terceiro
    OFFSETTING_CORPORATE = "OFFSETTING_CORPORATE" # Conta de Empresa Compensadora Final
    MARKET_MAKER = "MARKET_MAKER"                 # Formador de Mercado Credenciado B3


class DvPTradeStatus(str, Enum):
    """Estado de execução da liquidação bilateral de balcão (DvP)."""
    PENDING = "PENDING"       # Registrado, aguardando liquidação financeira
    SETTLED = "SETTLED"       # Liquidado financeiramente com transferência simultânea de custódia
    REJECTED = "REJECTED"     # Rejeitado por insuficiência de saldo ou divergência financeira
    CANCELLED = "CANCELLED"   # Cancelado por acordo mútuo antes da liquidação


# =====================================================================
# MODELOS DE DADOS E ESTRUTURAS IMUTÁVEIS
# =====================================================================

@dataclass
class CarbonProject:
    """Cadastro formal de projeto gerador de créditos de carbono voluntário."""
    project_id: str
    name: str
    standard: CertificationStandard
    project_type: CarbonProjectType
    country: str
    state_or_province: str
    developer_name: str
    registry_url_or_id: str
    methodology: str
    validator_body: str
    active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.project_id or not self.project_id.strip():
            raise CarbonValidationError("O identificador do projeto (project_id) é obrigatório.")
        if not self.name or not self.name.strip():
            raise CarbonValidationError("O nome do projeto é obrigatório.")
        if not self.developer_name or not self.developer_name.strip():
            raise CarbonValidationError("O desenvolvedor do projeto é obrigatório.")
        if not self.validator_body or not self.validator_body.strip():
            raise CarbonValidationError("O organismo validador/verificador (VVB) é obrigatório.")

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o projeto para formato dicionário."""
        return {
            "project_id": self.project_id,
            "name": self.name,
            "standard": self.standard.value,
            "project_type": self.project_type.value,
            "country": self.country,
            "state_or_province": self.state_or_province,
            "developer_name": self.developer_name,
            "registry_url_or_id": self.registry_url_or_id,
            "methodology": self.methodology,
            "validator_body": self.validator_body,
            "active": self.active,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CarbonProject:
        """Desserializa o projeto a partir de dicionário."""
        created_at = (
            datetime.fromisoformat(data["created_at"])
            if isinstance(data.get("created_at"), str)
            else data.get("created_at", datetime.now(timezone.utc))
        )
        return cls(
            project_id=data["project_id"],
            name=data["name"],
            standard=CertificationStandard(data["standard"]),
            project_type=CarbonProjectType(data["project_type"]),
            country=data["country"],
            state_or_province=data["state_or_province"],
            developer_name=data["developer_name"],
            registry_url_or_id=data["registry_url_or_id"],
            methodology=data["methodology"],
            validator_body=data["validator_body"],
            active=bool(data.get("active", True)),
            created_at=created_at,
        )


@dataclass(frozen=True)
class CarbonSerialRange:
    """Intervalo serial internacional único de créditos emitidos."""
    prefix: str
    start_index: int
    end_index: int

    def __post_init__(self) -> None:
        if self.start_index <= 0:
            raise CarbonValidationError(f"Índice serial inicial deve ser positivo: {self.start_index}")
        if self.end_index < self.start_index:
            raise CarbonValidationError(
                f"Índice final ({self.end_index}) não pode ser menor que o inicial ({self.start_index})."
            )

    @property
    def total_units(self) -> int:
        """Quantidade inteira de unidades no intervalo serial."""
        return self.end_index - self.start_index + 1

    @property
    def formatted_range(self) -> str:
        """Representação serial formatada canônica."""
        return f"{self.prefix}-{self.start_index:08d}-{self.end_index:08d}"

    def overlaps_with(self, other: CarbonSerialRange) -> bool:
        """Verifica se há sobreposição com outro intervalo de mesmo prefixo."""
        if self.prefix != other.prefix:
            return False
        return not (self.end_index < other.start_index or self.start_index > other.end_index)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o intervalo serial."""
        return {
            "prefix": self.prefix,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "formatted_range": self.formatted_range,
            "total_units": self.total_units,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CarbonSerialRange:
        """Desserializa intervalo serial."""
        return cls(
            prefix=data["prefix"],
            start_index=int(data["start_index"]),
            end_index=int(data["end_index"]),
        )


@dataclass
class CarbonCreditBatch:
    """Lote escritural de créditos de carbono custodiado na Plataforma B3."""
    batch_id: str
    project_id: str
    standard: CertificationStandard
    project_type: CarbonProjectType
    vintage: int
    serial_range: CarbonSerialRange
    total_issued_tco2e: Decimal
    available_tco2e: Decimal
    locked_tco2e: Decimal = field(default_factory=lambda: Decimal("0.00"))
    retired_tco2e: Decimal = field(default_factory=lambda: Decimal("0.00"))
    registry_issuance_date: date = field(default_factory=date.today)
    b3_registration_date: date = field(default_factory=date.today)
    verifier_report_hash: str = ""
    status: CarbonCreditStatus = CarbonCreditStatus.ISSUED
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.total_issued_tco2e = round_tco2e(self.total_issued_tco2e)
        self.available_tco2e = round_tco2e(self.available_tco2e)
        self.locked_tco2e = round_tco2e(self.locked_tco2e)
        self.retired_tco2e = round_tco2e(self.retired_tco2e)

        if self.vintage < 2000 or self.vintage > 2100:
            raise CarbonValidationError(f"Ano de vintage inválido: {self.vintage}")
        if self.total_issued_tco2e <= Decimal("0.00"):
            raise CarbonValidationError("O total emitido do lote deve ser estritamente positivo.")
        if self.verifier_report_hash and not SHA256_REGEX.match(self.verifier_report_hash):
            raise CarbonValidationError(f"Hash do relatório de verificação deve ser SHA-256: {self.verifier_report_hash}")

        # Invariante interna do lote
        expected_total = self.available_tco2e + self.locked_tco2e + self.retired_tco2e
        if self.total_issued_tco2e != expected_total:
            raise CarbonInvariantViolationError(
                f"Lote {self.batch_id}: Total emitido ({self.total_issued_tco2e}) "
                f"inconsistente com soma de estados ({expected_total})."
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o lote para dicionário estruturado."""
        return {
            "batch_id": self.batch_id,
            "project_id": self.project_id,
            "standard": self.standard.value,
            "project_type": self.project_type.value,
            "vintage": self.vintage,
            "serial_range": self.serial_range.to_dict(),
            "total_issued_tco2e": str(self.total_issued_tco2e),
            "available_tco2e": str(self.available_tco2e),
            "locked_tco2e": str(self.locked_tco2e),
            "retired_tco2e": str(self.retired_tco2e),
            "registry_issuance_date": self.registry_issuance_date.isoformat(),
            "b3_registration_date": self.b3_registration_date.isoformat(),
            "verifier_report_hash": self.verifier_report_hash,
            "status": self.status.value,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CarbonCreditBatch:
        """Desserializa lote a partir de dicionário."""
        reg_date = (
            date.fromisoformat(data["registry_issuance_date"])
            if isinstance(data.get("registry_issuance_date"), str)
            else data.get("registry_issuance_date", date.today())
        )
        b3_date = (
            date.fromisoformat(data["b3_registration_date"])
            if isinstance(data.get("b3_registration_date"), str)
            else data.get("b3_registration_date", date.today())
        )
        return cls(
            batch_id=data["batch_id"],
            project_id=data["project_id"],
            standard=CertificationStandard(data["standard"]),
            project_type=CarbonProjectType(data["project_type"]),
            vintage=int(data["vintage"]),
            serial_range=CarbonSerialRange.from_dict(data["serial_range"]),
            total_issued_tco2e=to_decimal(data["total_issued_tco2e"]),
            available_tco2e=to_decimal(data["available_tco2e"]),
            locked_tco2e=to_decimal(data.get("locked_tco2e", "0.00")),
            retired_tco2e=to_decimal(data.get("retired_tco2e", "0.00")),
            registry_issuance_date=reg_date,
            b3_registration_date=b3_date,
            verifier_report_hash=data.get("verifier_report_hash", ""),
            status=CarbonCreditStatus(data.get("status", CarbonCreditStatus.ISSUED.value)),
            metadata=data.get("metadata", {}),
        )


@dataclass
class CustodyBatchPosition:
    """Posição de custódia segregada em determinado lote de carbono."""
    batch_id: str
    available_quantity: Decimal = field(default_factory=lambda: Decimal("0.00"))
    locked_quantity: Decimal = field(default_factory=lambda: Decimal("0.00"))
    retired_quantity: Decimal = field(default_factory=lambda: Decimal("0.00"))

    def __post_init__(self) -> None:
        self.available_quantity = round_tco2e(self.available_quantity)
        self.locked_quantity = round_tco2e(self.locked_quantity)
        self.retired_quantity = round_tco2e(self.retired_quantity)
        self._validate_non_negative()

    def _validate_non_negative(self) -> None:
        if self.available_quantity < Decimal("0.00"):
            raise InsufficientCarbonBalanceError(
                f"Saldo disponível negativo para lote {self.batch_id}: {self.available_quantity}"
            )
        if self.locked_quantity < Decimal("0.00"):
            raise InsufficientCarbonBalanceError(
                f"Saldo bloqueado negativo para lote {self.batch_id}: {self.locked_quantity}"
            )
        if self.retired_quantity < Decimal("0.00"):
            raise InsufficientCarbonBalanceError(
                f"Saldo aposentado negativo para lote {self.batch_id}: {self.retired_quantity}"
            )

    @property
    def total_quantity(self) -> Decimal:
        """Soma de todas as quantidades (disponível + bloqueado + aposentado)."""
        return round_tco2e(self.available_quantity + self.locked_quantity + self.retired_quantity)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a posição de custódia."""
        return {
            "batch_id": self.batch_id,
            "available_quantity": str(self.available_quantity),
            "locked_quantity": str(self.locked_quantity),
            "retired_quantity": str(self.retired_quantity),
            "total_quantity": str(self.total_quantity),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CustodyBatchPosition:
        """Desserializa posição de custódia."""
        return cls(
            batch_id=data["batch_id"],
            available_quantity=to_decimal(data.get("available_quantity", "0.00")),
            locked_quantity=to_decimal(data.get("locked_quantity", "0.00")),
            retired_quantity=to_decimal(data.get("retired_quantity", "0.00")),
        )


@dataclass
class CustodyAccount:
    """Conta de custódia segregada no depositário central de carbono B3."""
    account_id: str
    participant_code: str
    holder_name: str
    holder_document: str
    account_type: CustodyAccountType
    positions: Dict[str, CustodyBatchPosition] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True

    def __post_init__(self) -> None:
        if not self.account_id or not self.account_id.strip():
            raise CarbonValidationError("O identificador da conta de custódia é obrigatório.")
        if not self.holder_name or not self.holder_name.strip():
            raise CarbonValidationError("O nome do titular da conta de custódia é obrigatório.")
        clean_doc = sanitize_document(self.holder_document)
        if not (CNPJ_REGEX.match(clean_doc) or CPF_REGEX.match(clean_doc)):
            raise CarbonValidationError(f"Documento do titular inválido (esperado CPF ou CNPJ): {self.holder_document}")
        self.holder_document = clean_doc

    def get_position(self, batch_id: str) -> CustodyBatchPosition:
        """Obtém ou cria sob demanda a posição do lote nesta conta."""
        if batch_id not in self.positions:
            self.positions[batch_id] = CustodyBatchPosition(batch_id=batch_id)
        return self.positions[batch_id]

    def credit(self, batch_id: str, quantity: Union[Decimal, str, int, float]) -> None:
        """Credita volume disponível em determinado lote."""
        qty = round_tco2e(quantity)
        if qty <= Decimal("0.00"):
            raise CarbonValidationError(f"Quantidade a creditar deve ser estritamente positiva: {qty}")
        pos = self.get_position(batch_id)
        pos.available_quantity = round_tco2e(pos.available_quantity + qty)

    def debit(self, batch_id: str, quantity: Union[Decimal, str, int, float]) -> None:
        """Debita volume disponível em determinado lote."""
        qty = round_tco2e(quantity)
        if qty <= Decimal("0.00"):
            raise CarbonValidationError(f"Quantidade a debitar deve ser estritamente positiva: {qty}")
        pos = self.get_position(batch_id)
        if pos.available_quantity < qty:
            raise InsufficientCarbonBalanceError(
                f"Conta {self.account_id}: saldo disponível insuficiente para lote {batch_id}. "
                f"Disponível: {pos.available_quantity}, Solicitado: {qty}"
            )
        pos.available_quantity = round_tco2e(pos.available_quantity - qty)

    def lock(self, batch_id: str, quantity: Union[Decimal, str, int, float]) -> None:
        """Bloqueia volume disponível para garantia ou liquidação."""
        qty = round_tco2e(quantity)
        if qty <= Decimal("0.00"):
            raise CarbonValidationError(f"Quantidade a bloquear deve ser estritamente positiva: {qty}")
        pos = self.get_position(batch_id)
        if pos.available_quantity < qty:
            raise InsufficientCarbonBalanceError(
                f"Conta {self.account_id}: saldo disponível insuficiente para bloqueio no lote {batch_id}. "
                f"Disponível: {pos.available_quantity}, Solicitado: {qty}"
            )
        pos.available_quantity = round_tco2e(pos.available_quantity - qty)
        pos.locked_quantity = round_tco2e(pos.locked_quantity + qty)

    def unlock(self, batch_id: str, quantity: Union[Decimal, str, int, float]) -> None:
        """Desbloqueia volume previamente retido."""
        qty = round_tco2e(quantity)
        if qty <= Decimal("0.00"):
            raise CarbonValidationError(f"Quantidade a desbloquear deve ser estritamente positiva: {qty}")
        pos = self.get_position(batch_id)
        if pos.locked_quantity < qty:
            raise InsufficientCarbonBalanceError(
                f"Conta {self.account_id}: saldo bloqueado insuficiente para liberação no lote {batch_id}. "
                f"Bloqueado: {pos.locked_quantity}, Solicitado: {qty}"
            )
        pos.locked_quantity = round_tco2e(pos.locked_quantity - qty)
        pos.available_quantity = round_tco2e(pos.available_quantity + qty)

    def retire(self, batch_id: str, quantity: Union[Decimal, str, int, float]) -> None:
        """Aposenta definitivamente volume do saldo disponível."""
        qty = round_tco2e(quantity)
        if qty <= Decimal("0.00"):
            raise CarbonValidationError(f"Quantidade a aposentar deve ser estritamente positiva: {qty}")
        pos = self.get_position(batch_id)
        if pos.available_quantity < qty:
            raise InsufficientCarbonBalanceError(
                f"Conta {self.account_id}: saldo disponível insuficiente para aposentadoria no lote {batch_id}. "
                f"Disponível: {pos.available_quantity}, Solicitado: {qty}"
            )
        pos.available_quantity = round_tco2e(pos.available_quantity - qty)
        pos.retired_quantity = round_tco2e(pos.retired_quantity + qty)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a conta de custódia e suas posições."""
        return {
            "account_id": self.account_id,
            "participant_code": self.participant_code,
            "holder_name": self.holder_name,
            "holder_document": self.holder_document,
            "account_type": self.account_type.value,
            "positions": {b_id: pos.to_dict() for b_id, pos in self.positions.items()},
            "created_at": self.created_at.isoformat(),
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CustodyAccount:
        """Desserializa conta de custódia."""
        created_at = (
            datetime.fromisoformat(data["created_at"])
            if isinstance(data.get("created_at"), str)
            else data.get("created_at", datetime.now(timezone.utc))
        )
        positions = {
            b_id: CustodyBatchPosition.from_dict(pos_data)
            for b_id, pos_data in data.get("positions", {}).items()
        }
        return cls(
            account_id=data["account_id"],
            participant_code=data["participant_code"],
            holder_name=data["holder_name"],
            holder_document=data["holder_document"],
            account_type=CustodyAccountType(data["account_type"]),
            positions=positions,
            created_at=created_at,
            is_active=bool(data.get("is_active", True)),
        )


@dataclass(frozen=True)
class CarbonRetirementCertificate:
    """Certidão oficial e irrevogável de aposentadoria de créditos de carbono B3."""
    certificate_id: str
    batch_id: str
    project_id: str
    project_name: str
    standard: CertificationStandard
    project_type: CarbonProjectType
    vintage: int
    quantity_tco2e: Decimal
    beneficiary_name: str
    beneficiary_document: str
    scope: CarbonOffsetScope
    retirement_reason: str
    retirement_timestamp: datetime
    serial_subrange: str
    certificate_hash: str
    is_irrevocable: bool = True

    def __post_init__(self) -> None:
        if not self.certificate_id or not self.certificate_id.strip():
            raise CarbonValidationError("O identificador da certidão é obrigatório.")
        if not self.beneficiary_name or not self.beneficiary_name.strip():
            raise CarbonValidationError("A razão social ou nome do beneficiário é obrigatório.")
        clean_doc = sanitize_document(self.beneficiary_document)
        if not (CNPJ_REGEX.match(clean_doc) or CPF_REGEX.match(clean_doc)):
            raise CarbonValidationError(f"Documento do beneficiário inválido: {self.beneficiary_document}")
        if self.quantity_tco2e <= Decimal("0.00"):
            raise CarbonValidationError("O volume aposentado deve ser estritamente positivo.")
        if not SHA256_REGEX.match(self.certificate_hash):
            raise CarbonValidationError(f"Hash da certidão deve ser SHA-256 válido: {self.certificate_hash}")

    @staticmethod
    def generate_hash(
        certificate_id: str,
        batch_id: str,
        project_id: str,
        standard: CertificationStandard,
        vintage: int,
        quantity_tco2e: Decimal,
        beneficiary_document: str,
        scope: CarbonOffsetScope,
        timestamp_iso: str,
    ) -> str:
        """Gera digest criptográfico SHA-256 canônico para a certidão."""
        canonical_str = (
            f"{certificate_id}|{batch_id}|{project_id}|{standard.value}|"
            f"{vintage}|{round_tco2e(quantity_tco2e)}|{sanitize_document(beneficiary_document)}|"
            f"{scope.value}|{timestamp_iso}"
        )
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a certidão de aposentadoria."""
        return {
            "certificate_id": self.certificate_id,
            "batch_id": self.batch_id,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "standard": self.standard.value,
            "project_type": self.project_type.value,
            "vintage": self.vintage,
            "quantity_tco2e": str(self.quantity_tco2e),
            "beneficiary_name": self.beneficiary_name,
            "beneficiary_document": self.beneficiary_document,
            "scope": self.scope.value,
            "retirement_reason": self.retirement_reason,
            "retirement_timestamp": self.retirement_timestamp.isoformat(),
            "serial_subrange": self.serial_subrange,
            "certificate_hash": self.certificate_hash,
            "is_irrevocable": self.is_irrevocable,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CarbonRetirementCertificate:
        """Desserializa certidão de aposentadoria."""
        ts = (
            datetime.fromisoformat(data["retirement_timestamp"])
            if isinstance(data.get("retirement_timestamp"), str)
            else data.get("retirement_timestamp", datetime.now(timezone.utc))
        )
        return cls(
            certificate_id=data["certificate_id"],
            batch_id=data["batch_id"],
            project_id=data["project_id"],
            project_name=data["project_name"],
            standard=CertificationStandard(data["standard"]),
            project_type=CarbonProjectType(data["project_type"]),
            vintage=int(data["vintage"]),
            quantity_tco2e=to_decimal(data["quantity_tco2e"]),
            beneficiary_name=data["beneficiary_name"],
            beneficiary_document=sanitize_document(data["beneficiary_document"]),
            scope=CarbonOffsetScope(data["scope"]),
            retirement_reason=data["retirement_reason"],
            retirement_timestamp=ts,
            serial_subrange=data["serial_subrange"],
            certificate_hash=data["certificate_hash"],
            is_irrevocable=bool(data.get("is_irrevocable", True)),
        )


@dataclass
class DvPTrade:
    """Operação de negociação bilateral em Balcão B3 com liquidação financeira DvP."""
    trade_id: str
    batch_id: str
    seller_account_id: str
    buyer_account_id: str
    quantity_tco2e: Decimal
    unit_price_brl: Decimal
    gross_amount_brl: Decimal
    b3_fee_rate: Decimal
    b3_fee_brl: Decimal
    net_seller_amount_brl: Decimal
    net_buyer_disbursement_brl: Decimal
    trade_timestamp: datetime
    settlement_date: date
    status: DvPTradeStatus
    financial_transaction_ref: str

    def __post_init__(self) -> None:
        self.quantity_tco2e = round_tco2e(self.quantity_tco2e)
        self.unit_price_brl = round_cents(self.unit_price_brl)
        self.gross_amount_brl = round_cents(self.gross_amount_brl)
        self.b3_fee_brl = round_cents(self.b3_fee_brl)
        self.net_seller_amount_brl = round_cents(self.net_seller_amount_brl)
        self.net_buyer_disbursement_brl = round_cents(self.net_buyer_disbursement_brl)

        if self.quantity_tco2e <= Decimal("0.00"):
            raise CarbonValidationError(f"Quantidade negociada no DvP deve ser estritamente positiva: {self.quantity_tco2e}")
        if self.unit_price_brl <= Decimal("0.00"):
            raise CarbonValidationError(f"Preço unitário no DvP deve ser estritamente positivo: {self.unit_price_brl}")

        # Validação financeira de conservação centavo a centavo
        expected_gross = round_cents(self.quantity_tco2e * self.unit_price_brl)
        if self.gross_amount_brl != expected_gross:
            raise DvPSettlementError(
                f"Valor bruto ({self.gross_amount_brl}) difere de quantidade * preço unitário ({expected_gross})."
            )

        expected_buyer = round_cents(self.gross_amount_brl + self.b3_fee_brl)
        if self.net_buyer_disbursement_brl != expected_buyer:
            raise DvPSettlementError(
                f"Desembolso do comprador ({self.net_buyer_disbursement_brl}) "
                f"difere do total bruto + taxa B3 ({expected_buyer})."
            )

        if self.net_seller_amount_brl != self.gross_amount_brl:
            raise DvPSettlementError(
                f"Recebimento líquido do vendedor ({self.net_seller_amount_brl}) difere do valor bruto ({self.gross_amount_brl})."
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a operação DvP."""
        return {
            "trade_id": self.trade_id,
            "batch_id": self.batch_id,
            "seller_account_id": self.seller_account_id,
            "buyer_account_id": self.buyer_account_id,
            "quantity_tco2e": str(self.quantity_tco2e),
            "unit_price_brl": str(self.unit_price_brl),
            "gross_amount_brl": str(self.gross_amount_brl),
            "b3_fee_rate": str(self.b3_fee_rate),
            "b3_fee_brl": str(self.b3_fee_brl),
            "net_seller_amount_brl": str(self.net_seller_amount_brl),
            "net_buyer_disbursement_brl": str(self.net_buyer_disbursement_brl),
            "trade_timestamp": self.trade_timestamp.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
            "status": self.status.value,
            "financial_transaction_ref": self.financial_transaction_ref,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DvPTrade:
        """Desserializa operação DvP."""
        ts = (
            datetime.fromisoformat(data["trade_timestamp"])
            if isinstance(data.get("trade_timestamp"), str)
            else data.get("trade_timestamp", datetime.now(timezone.utc))
        )
        s_date = (
            date.fromisoformat(data["settlement_date"])
            if isinstance(data.get("settlement_date"), str)
            else data.get("settlement_date", date.today())
        )
        return cls(
            trade_id=data["trade_id"],
            batch_id=data["batch_id"],
            seller_account_id=data["seller_account_id"],
            buyer_account_id=data["buyer_account_id"],
            quantity_tco2e=to_decimal(data["quantity_tco2e"]),
            unit_price_brl=to_decimal(data["unit_price_brl"]),
            gross_amount_brl=to_decimal(data["gross_amount_brl"]),
            b3_fee_rate=to_decimal(data["b3_fee_rate"]),
            b3_fee_brl=to_decimal(data["b3_fee_brl"]),
            net_seller_amount_brl=to_decimal(data["net_seller_amount_brl"]),
            net_buyer_disbursement_brl=to_decimal(data["net_buyer_disbursement_brl"]),
            trade_timestamp=ts,
            settlement_date=s_date,
            status=DvPTradeStatus(data["status"]),
            financial_transaction_ref=data["financial_transaction_ref"],
        )


# =====================================================================
# MOTOR DETERMINÍSTICO DE CRÉDITOS DE CARBONO VOLUNTÁRIO B3
# =====================================================================

class VoluntaryCarbonCreditsEngine:
    """Motor unificado de Escrituração, Custódia, Negociação e Aposentadoria de Créditos de Carbono B3 (FL-088)."""

    def __init__(self, default_b3_fee_rate: Union[Decimal, str, float] = Decimal("0.0010")) -> None:
        self.default_b3_fee_rate: Decimal = to_decimal(default_b3_fee_rate).quantize(QUANTIZE_RATE)
        self.projects: Dict[str, CarbonProject] = {}
        self.batches: Dict[str, CarbonCreditBatch] = {}
        self.custody_accounts: Dict[str, CustodyAccount] = {}
        self.trades: Dict[str, DvPTrade] = {}
        self.retirement_certificates: Dict[str, CarbonRetirementCertificate] = {}

    # -----------------------------------------------------------------
    # REGISTRO DE PROJETOS E EMISSÕES
    # -----------------------------------------------------------------

    def register_project(
        self,
        project_id: str,
        name: str,
        standard: Union[CertificationStandard, str],
        project_type: Union[CarbonProjectType, str],
        country: str,
        state_or_province: str,
        developer_name: str,
        registry_url_or_id: str,
        methodology: str,
        validator_body: str,
    ) -> CarbonProject:
        """Registra novo projeto de crédito de carbono elegível no Balcão B3."""
        if project_id in self.projects:
            raise CarbonValidationError(f"Projeto com ID '{project_id}' já está cadastrado.")

        std_enum = CertificationStandard(standard) if isinstance(standard, str) else standard
        type_enum = CarbonProjectType(project_type) if isinstance(project_type, str) else project_type

        project = CarbonProject(
            project_id=project_id,
            name=name,
            standard=std_enum,
            project_type=type_enum,
            country=country.upper().strip(),
            state_or_province=state_or_province.upper().strip(),
            developer_name=developer_name.strip(),
            registry_url_or_id=registry_url_or_id.strip(),
            methodology=methodology.strip(),
            validator_body=validator_body.strip(),
        )
        self.projects[project_id] = project
        return project

    def register_batch(
        self,
        batch_id: str,
        project_id: str,
        vintage: int,
        serial_prefix: str,
        serial_start: int,
        serial_end: int,
        total_issued_tco2e: Union[Decimal, str, int, float],
        registry_issuance_date: Optional[Union[date, str]] = None,
        b3_registration_date: Optional[Union[date, str]] = None,
        verifier_report_hash: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CarbonCreditBatch:
        """Registra e escritura lote primário de créditos com verificação formal anti-dupla contagem."""
        if batch_id in self.batches:
            raise CarbonValidationError(f"Lote '{batch_id}' já escriturado na plataforma B3.")
        if project_id not in self.projects:
            raise CarbonValidationError(f"Projeto '{project_id}' não encontrado.")

        project = self.projects[project_id]
        if not project.active:
            raise CarbonValidationError(f"Projeto '{project_id}' encontra-se inativo.")

        serial_range = CarbonSerialRange(
            prefix=serial_prefix.strip(),
            start_index=int(serial_start),
            end_index=int(serial_end),
        )

        issued_dec = round_tco2e(total_issued_tco2e)
        if issued_dec <= Decimal("0.00"):
            raise CarbonValidationError("O total emitido deve ser estritamente positivo.")

        # Validação de paridade metrológica com o intervalo serial (se serial inteiro)
        if Decimal(serial_range.total_units) != issued_dec:
            raise CarbonValidationError(
                f"Contagem serial ({serial_range.total_units} unidades) difere do volume emitido ({issued_dec} tCO2e)."
            )

        # Invariante Anti-Double Counting: checagem de sobreposição serial contra lotes existentes
        for existing_batch in self.batches.values():
            if existing_batch.standard == project.standard and existing_batch.serial_range.overlaps_with(serial_range):
                raise DoubleCountingError(
                    f"Colisão de intervalo serial detectada! O intervalo {serial_range.formatted_range} "
                    f"sobrepõe o lote existente {existing_batch.batch_id} ({existing_batch.serial_range.formatted_range})."
                )

        reg_iss_date = (
            date.fromisoformat(registry_issuance_date)
            if isinstance(registry_issuance_date, str)
            else (registry_issuance_date or date.today())
        )
        b3_reg_date = (
            date.fromisoformat(b3_registration_date)
            if isinstance(b3_registration_date, str)
            else (b3_registration_date or date.today())
        )

        batch = CarbonCreditBatch(
            batch_id=batch_id,
            project_id=project_id,
            standard=project.standard,
            project_type=project.project_type,
            vintage=vintage,
            serial_range=serial_range,
            total_issued_tco2e=issued_dec,
            available_tco2e=issued_dec,
            locked_tco2e=Decimal("0.00"),
            retired_tco2e=Decimal("0.00"),
            registry_issuance_date=reg_iss_date,
            b3_registration_date=b3_reg_date,
            verifier_report_hash=verifier_report_hash,
            status=CarbonCreditStatus.ACTIVE,
            metadata=metadata or {},
        )
        self.batches[batch_id] = batch
        return batch

    # -----------------------------------------------------------------
    # GESTÃO DE CONTAS DE CUSTÓDIA SEGREGADA
    # -----------------------------------------------------------------

    def open_custody_account(
        self,
        account_id: str,
        participant_code: str,
        holder_name: str,
        holder_document: str,
        account_type: Union[CustodyAccountType, str] = CustodyAccountType.CLIENT,
    ) -> CustodyAccount:
        """Abre conta de custódia segregada no depositário de carbono B3."""
        if account_id in self.custody_accounts:
            raise CarbonValidationError(f"Conta de custódia '{account_id}' já cadastrada.")

        acct_type_enum = (
            CustodyAccountType(account_type)
            if isinstance(account_type, str)
            else account_type
        )
        account = CustodyAccount(
            account_id=account_id,
            participant_code=participant_code.strip(),
            holder_name=holder_name.strip(),
            holder_document=holder_document,
            account_type=acct_type_enum,
        )
        self.custody_accounts[account_id] = account
        return account

    def deposit_initial_batch(
        self,
        account_id: str,
        batch_id: str,
        quantity_tco2e: Union[Decimal, str, int, float],
    ) -> None:
        """Aloca volume inicial de lote recém-escriturado na conta de custódia do originador."""
        if account_id not in self.custody_accounts:
            raise CarbonValidationError(f"Conta de custódia '{account_id}' não encontrada.")
        if batch_id not in self.batches:
            raise CarbonValidationError(f"Lote '{batch_id}' não encontrado.")

        qty = round_tco2e(quantity_tco2e)
        batch = self.batches[batch_id]
        account = self.custody_accounts[account_id]

        # Verifica se o lote já não foi todo distribuído entre as contas
        total_in_accounts = sum(
            acc.positions[batch_id].total_quantity
            for acc in self.custody_accounts.values()
            if batch_id in acc.positions
        )
        if total_in_accounts + qty > batch.total_issued_tco2e:
            raise DoubleCountingError(
                f"Tentativa de depositar volume ({qty}) além da emissão total do lote ({batch.total_issued_tco2e}). "
                f"Alocado anteriormente: {total_in_accounts}."
            )

        account.credit(batch_id, qty)

    def transfer_custody(
        self,
        from_account_id: str,
        to_account_id: str,
        batch_id: str,
        quantity_tco2e: Union[Decimal, str, int, float],
    ) -> None:
        """Transfere custódia de créditos entre duas contas com conservação total."""
        if from_account_id not in self.custody_accounts:
            raise CarbonValidationError(f"Conta de origem '{from_account_id}' não encontrada.")
        if to_account_id not in self.custody_accounts:
            raise CarbonValidationError(f"Conta de destino '{to_account_id}' não encontrada.")
        if from_account_id == to_account_id:
            raise CarbonValidationError("Conta de origem e destino da transferência de custódia devem ser distintas.")

        qty = round_tco2e(quantity_tco2e)
        from_acc = self.custody_accounts[from_account_id]
        to_acc = self.custody_accounts[to_account_id]

        # Execução atômica
        from_acc.debit(batch_id, qty)
        to_acc.credit(batch_id, qty)

    def lock_credits(
        self,
        account_id: str,
        batch_id: str,
        quantity_tco2e: Union[Decimal, str, int, float],
    ) -> None:
        """Bloqueia créditos de carbono em custódia para garantia ou pendência."""
        if account_id not in self.custody_accounts:
            raise CarbonValidationError(f"Conta '{account_id}' não encontrada.")
        if batch_id not in self.batches:
            raise CarbonValidationError(f"Lote '{batch_id}' não encontrado.")

        qty = round_tco2e(quantity_tco2e)
        account = self.custody_accounts[account_id]
        batch = self.batches[batch_id]

        account.lock(batch_id, qty)
        batch.available_tco2e = round_tco2e(batch.available_tco2e - qty)
        batch.locked_tco2e = round_tco2e(batch.locked_tco2e + qty)

    def unlock_credits(
        self,
        account_id: str,
        batch_id: str,
        quantity_tco2e: Union[Decimal, str, int, float],
    ) -> None:
        """Desbloqueia créditos de carbono retornando-os ao saldo disponível."""
        if account_id not in self.custody_accounts:
            raise CarbonValidationError(f"Conta '{account_id}' não encontrada.")
        if batch_id not in self.batches:
            raise CarbonValidationError(f"Lote '{batch_id}' não encontrado.")

        qty = round_tco2e(quantity_tco2e)
        account = self.custody_accounts[account_id]
        batch = self.batches[batch_id]

        account.unlock(batch_id, qty)
        batch.locked_tco2e = round_tco2e(batch.locked_tco2e - qty)
        batch.available_tco2e = round_tco2e(batch.available_tco2e + qty)

    # -----------------------------------------------------------------
    # NEGOCIAÇÃO DE BALCÃO E LIQUIDAÇÃO FINANCEIRA DvP
    # -----------------------------------------------------------------

    def execute_dvp_trade(
        self,
        trade_id: str,
        batch_id: str,
        seller_account_id: str,
        buyer_account_id: str,
        quantity_tco2e: Union[Decimal, str, int, float],
        unit_price_brl: Union[Decimal, str, int, float],
        b3_fee_rate: Optional[Union[Decimal, str, float]] = None,
        financial_transaction_ref: Optional[str] = None,
        trade_timestamp: Optional[Union[datetime, str]] = None,
        settlement_date: Optional[Union[date, str]] = None,
    ) -> DvPTrade:
        """Executa negociação atômica DvP (Delivery versus Payment) no Balcão B3."""
        if trade_id in self.trades:
            raise CarbonValidationError(f"Operação '{trade_id}' já registrada.")
        if seller_account_id not in self.custody_accounts:
            raise CarbonValidationError(f"Conta vendedora '{seller_account_id}' não encontrada.")
        if buyer_account_id not in self.custody_accounts:
            raise CarbonValidationError(f"Conta compradora '{buyer_account_id}' não encontrada.")
        if seller_account_id == buyer_account_id:
            raise CarbonValidationError("Conta vendedora e compradora não podem ser idênticas em operação DvP.")
        if batch_id not in self.batches:
            raise CarbonValidationError(f"Lote '{batch_id}' não encontrado.")

        qty = round_tco2e(quantity_tco2e)
        unit_p = round_cents(unit_price_brl)
        rate = (
            to_decimal(b3_fee_rate).quantize(QUANTIZE_RATE)
            if b3_fee_rate is not None
            else self.default_b3_fee_rate
        )

        gross = round_cents(qty * unit_p)
        fee = round_cents(gross * rate)
        net_seller = gross
        net_buyer = round_cents(gross + fee)

        seller_acc = self.custody_accounts[seller_account_id]
        buyer_acc = self.custody_accounts[buyer_account_id]

        pos_seller = seller_acc.get_position(batch_id)
        if pos_seller.available_quantity < qty:
            raise InsufficientCarbonBalanceError(
                f"Vendedor {seller_account_id} não possui saldo suficiente ({pos_seller.available_quantity} tCO2e) "
                f"para honrar venda DvP de {qty} tCO2e do lote {batch_id}."
            )

        ts = (
            datetime.fromisoformat(trade_timestamp)
            if isinstance(trade_timestamp, str)
            else (trade_timestamp or datetime.now(timezone.utc))
        )
        s_date = (
            date.fromisoformat(settlement_date)
            if isinstance(settlement_date, str)
            else (settlement_date or date.today())
        )
        fin_ref = financial_transaction_ref or f"B3-SPB-{trade_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        trade = DvPTrade(
            trade_id=trade_id,
            batch_id=batch_id,
            seller_account_id=seller_account_id,
            buyer_account_id=buyer_account_id,
            quantity_tco2e=qty,
            unit_price_brl=unit_p,
            gross_amount_brl=gross,
            b3_fee_rate=rate,
            b3_fee_brl=fee,
            net_seller_amount_brl=net_seller,
            net_buyer_disbursement_brl=net_buyer,
            trade_timestamp=ts,
            settlement_date=s_date,
            status=DvPTradeStatus.SETTLED,
            financial_transaction_ref=fin_ref,
        )

        # Execução atômica da entrega de custódia
        seller_acc.debit(batch_id, qty)
        buyer_acc.credit(batch_id, qty)

        self.trades[trade_id] = trade
        return trade

    # -----------------------------------------------------------------
    # APOSENTADORIA DEFINITIVA IRREVOGÁVEL (RETIREMENT)
    # -----------------------------------------------------------------

    def retire_credits(
        self,
        certificate_id: str,
        account_id: str,
        batch_id: str,
        quantity_tco2e: Union[Decimal, str, int, float],
        beneficiary_name: str,
        beneficiary_document: str,
        scope: Union[CarbonOffsetScope, str],
        retirement_reason: str,
        retirement_timestamp: Optional[Union[datetime, str]] = None,
    ) -> CarbonRetirementCertificate:
        """Aposenta definitivamente créditos de carbono para compensação corporativa (GHG Protocol)."""
        if certificate_id in self.retirement_certificates:
            raise CarbonValidationError(f"Certidão com ID '{certificate_id}' já emitida.")
        if account_id not in self.custody_accounts:
            raise CarbonValidationError(f"Conta de custódia '{account_id}' não encontrada.")
        if batch_id not in self.batches:
            raise CarbonValidationError(f"Lote '{batch_id}' não encontrado.")

        qty = round_tco2e(quantity_tco2e)
        if qty <= Decimal("0.00"):
            raise CarbonValidationError("O volume para aposentadoria deve ser estritamente positivo.")

        account = self.custody_accounts[account_id]
        batch = self.batches[batch_id]
        project = self.projects[batch.project_id]

        pos = account.get_position(batch_id)
        if pos.available_quantity < qty:
            raise InsufficientCarbonBalanceError(
                f"Conta {account_id} tem saldo disponível insuficiente ({pos.available_quantity}) "
                f"para aposentadoria de {qty} tCO2e."
            )

        if batch.available_tco2e < qty:
            raise InsufficientCarbonBalanceError(
                f"Lote {batch_id} tem saldo circulante insuficiente ({batch.available_tco2e}) "
                f"para aposentadoria de {qty} tCO2e."
            )

        scope_enum = CarbonOffsetScope(scope) if isinstance(scope, str) else scope
        clean_doc = sanitize_document(beneficiary_document)
        ts = (
            datetime.fromisoformat(retirement_timestamp)
            if isinstance(retirement_timestamp, str)
            else (retirement_timestamp or datetime.now(timezone.utc))
        )
        ts_iso = ts.isoformat()

        # Determina sub-intervalo serial aposentado a partir do acumulado anterior
        start_offset = int(batch.retired_tco2e)
        units_to_retire = int(qty)
        sub_start = batch.serial_range.start_index + start_offset
        sub_end = sub_start + units_to_retire - 1
        serial_subrange = f"{batch.serial_range.prefix}-{sub_start:08d}-{sub_end:08d}"

        cert_hash = CarbonRetirementCertificate.generate_hash(
            certificate_id=certificate_id,
            batch_id=batch_id,
            project_id=project.project_id,
            standard=batch.standard,
            vintage=batch.vintage,
            quantity_tco2e=qty,
            beneficiary_document=clean_doc,
            scope=scope_enum,
            timestamp_iso=ts_iso,
        )

        certificate = CarbonRetirementCertificate(
            certificate_id=certificate_id,
            batch_id=batch_id,
            project_id=project.project_id,
            project_name=project.name,
            standard=batch.standard,
            project_type=batch.project_type,
            vintage=batch.vintage,
            quantity_tco2e=qty,
            beneficiary_name=beneficiary_name.strip(),
            beneficiary_document=clean_doc,
            scope=scope_enum,
            retirement_reason=retirement_reason.strip(),
            retirement_timestamp=ts,
            serial_subrange=serial_subrange,
            certificate_hash=cert_hash,
            is_irrevocable=True,
        )

        # Baixa irrevogável na conta e no lote
        account.retire(batch_id, qty)
        batch.available_tco2e = round_tco2e(batch.available_tco2e - qty)
        batch.retired_tco2e = round_tco2e(batch.retired_tco2e + qty)

        if batch.available_tco2e == Decimal("0.00") and batch.locked_tco2e == Decimal("0.00"):
            batch.status = CarbonCreditStatus.RETIRED

        self.retirement_certificates[certificate_id] = certificate
        return certificate

    # -----------------------------------------------------------------
    # VERIFICAÇÃO FORMAL DE INVARIANTES
    # -----------------------------------------------------------------

    def verify_invariants(self) -> Dict[str, bool]:
        """Executa auditoria formal das leis de conservação e regras de integridade do mercado de carbono.

        Retorna dicionário mapeando cada identificador de invariante para seu status booleano.
        """
        results: Dict[str, bool] = {}

        # -------------------------------------------------------------
        # Lei 1: INV-VCM-001_ANTI_DOUBLE_COUNTING
        # -------------------------------------------------------------
        anti_double_counting_valid = True

        # (a) Unicidade e não sobreposição serial
        batch_list = list(self.batches.values())
        for i in range(len(batch_list)):
            for j in range(i + 1, len(batch_list)):
                b1 = batch_list[i]
                b2 = batch_list[j]
                if b1.standard == b2.standard and b1.serial_range.overlaps_with(b2.serial_range):
                    anti_double_counting_valid = False
                    break
            if not anti_double_counting_valid:
                break

        # (b) Conservação de estoque por lote: Total_Emitido == Disponível + Bloqueado + Aposentado
        if anti_double_counting_valid:
            for b in self.batches.values():
                expected_sum = round_tco2e(b.available_tco2e + b.locked_tco2e + b.retired_tco2e)
                if b.total_issued_tco2e != expected_sum:
                    anti_double_counting_valid = False
                    break

        # (c) Reconciliação dos lotes com as posições de custódia segregada
        if anti_double_counting_valid:
            for b_id, b in self.batches.items():
                acc_available = Decimal("0.00")
                acc_locked = Decimal("0.00")
                acc_retired = Decimal("0.00")

                for acc in self.custody_accounts.values():
                    if b_id in acc.positions:
                        pos = acc.positions[b_id]
                        acc_available += pos.available_quantity
                        acc_locked += pos.locked_quantity
                        acc_retired += pos.retired_quantity

                acc_available = round_tco2e(acc_available)
                acc_locked = round_tco2e(acc_locked)
                acc_retired = round_tco2e(acc_retired)

                if b.available_tco2e != acc_available or b.locked_tco2e != acc_locked or b.retired_tco2e != acc_retired:
                    anti_double_counting_valid = False
                    break

        results["INV-VCM-001_ANTI_DOUBLE_COUNTING"] = anti_double_counting_valid

        # -------------------------------------------------------------
        # Lei 2: INV-VCM-002_NON_NEGATIVE_BALANCES
        # -------------------------------------------------------------
        non_negative_valid = True
        for b in self.batches.values():
            if b.available_tco2e < Decimal("0.00") or b.locked_tco2e < Decimal("0.00") or b.retired_tco2e < Decimal("0.00"):
                non_negative_valid = False
                break

        if non_negative_valid:
            for acc in self.custody_accounts.values():
                for pos in acc.positions.values():
                    if (
                        pos.available_quantity < Decimal("0.00")
                        or pos.locked_quantity < Decimal("0.00")
                        or pos.retired_quantity < Decimal("0.00")
                    ):
                        non_negative_valid = False
                        break
                if not non_negative_valid:
                    break

        results["INV-VCM-002_NON_NEGATIVE_BALANCES"] = non_negative_valid

        # -------------------------------------------------------------
        # Lei 3: INV-VCM-003_DVP_FINANCIAL_SETTLEMENT
        # -------------------------------------------------------------
        dvp_valid = True
        for tr in self.trades.values():
            if tr.status == DvPTradeStatus.SETTLED:
                expected_gross = round_cents(tr.quantity_tco2e * tr.unit_price_brl)
                if tr.gross_amount_brl != expected_gross:
                    dvp_valid = False
                    break
                expected_disbursement = round_cents(tr.net_seller_amount_brl + tr.b3_fee_brl)
                if tr.net_buyer_disbursement_brl != expected_disbursement:
                    dvp_valid = False
                    break
        results["INV-VCM-003_DVP_FINANCIAL_SETTLEMENT"] = dvp_valid

        # -------------------------------------------------------------
        # Lei 4: INV-VCM-004_IRREVOCABLE_RETIREMENT
        # -------------------------------------------------------------
        retirement_valid = True
        for b_id, b in self.batches.items():
            certs_for_batch = [c for c in self.retirement_certificates.values() if c.batch_id == b_id]
            sum_certs = round_tco2e(sum((c.quantity_tco2e for c in certs_for_batch), Decimal("0.00")))
            if b.retired_tco2e != sum_certs:
                retirement_valid = False
                break

        if retirement_valid:
            for c in self.retirement_certificates.values():
                if not c.is_irrevocable:
                    retirement_valid = False
                    break
                if not SHA256_REGEX.match(c.certificate_hash):
                    retirement_valid = False
                    break
                # Recalcula hash para auditoria forense
                recomputed_hash = CarbonRetirementCertificate.generate_hash(
                    certificate_id=c.certificate_id,
                    batch_id=c.batch_id,
                    project_id=c.project_id,
                    standard=c.standard,
                    vintage=c.vintage,
                    quantity_tco2e=c.quantity_tco2e,
                    beneficiary_document=c.beneficiary_document,
                    scope=c.scope,
                    timestamp_iso=c.retirement_timestamp.isoformat(),
                )
                if c.certificate_hash != recomputed_hash:
                    retirement_valid = False
                    break

        results["INV-VCM-004_IRREVOCABLE_RETIREMENT"] = retirement_valid

        # -------------------------------------------------------------
        # Lei 5: INV-VCM-005_METRIC_PARITY
        # -------------------------------------------------------------
        parity_valid = True
        for b in self.batches.values():
            if b.vintage < 2000 or b.vintage > 2100:
                parity_valid = False
                break
            if b.total_issued_tco2e <= Decimal("0.00"):
                parity_valid = False
                break
            if b.total_issued_tco2e != round_tco2e(b.total_issued_tco2e):
                parity_valid = False
                break
        results["INV-VCM-005_METRIC_PARITY"] = parity_valid

        return results

    def assert_invariants(self) -> None:
        """Audita formalmente todas as invariantes e levanta CarbonInvariantViolationError em caso de falha."""
        audit = self.verify_invariants()
        failed = [law for law, passed in audit.items() if not passed]
        if failed:
            raise CarbonInvariantViolationError(
                f"Violação de Invariante detectada no mercado de carbono B3: {', '.join(failed)}"
            )

    # -----------------------------------------------------------------
    # SERIALIZAÇÃO E CARREGAMENTO
    # -----------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado integral do motor para representação JSON-compatível."""
        return {
            "default_b3_fee_rate": str(self.default_b3_fee_rate),
            "projects": {pid: prj.to_dict() for pid, prj in self.projects.items()},
            "batches": {bid: b.to_dict() for bid, b in self.batches.items()},
            "custody_accounts": {aid: acc.to_dict() for aid, acc in self.custody_accounts.items()},
            "trades": {tid: tr.to_dict() for tid, tr in self.trades.items()},
            "retirement_certificates": {cid: c.to_dict() for cid, c in self.retirement_certificates.items()},
        }

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        """Reconstitui o estado do motor a partir de dicionário estruturado."""
        if "default_b3_fee_rate" in data:
            self.default_b3_fee_rate = to_decimal(data["default_b3_fee_rate"]).quantize(QUANTIZE_RATE)

        self.projects = {
            pid: CarbonProject.from_dict(p_data)
            for pid, p_data in data.get("projects", {}).items()
        }
        self.batches = {
            bid: CarbonCreditBatch.from_dict(b_data)
            for bid, b_data in data.get("batches", {}).items()
        }
        self.custody_accounts = {
            aid: CustodyAccount.from_dict(a_data)
            for aid, a_data in data.get("custody_accounts", {}).items()
        }
        self.trades = {
            tid: DvPTrade.from_dict(t_data)
            for tid, t_data in data.get("trades", {}).items()
        }
        self.retirement_certificates = {
            cid: CarbonRetirementCertificate.from_dict(c_data)
            for cid, c_data in data.get("retirement_certificates", {}).items()
        }

        # Validação formal imediata do estado carregado
        self.assert_invariants()
