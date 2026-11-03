"""Módulo de Créditos de Descarbonização (CBIO / RenovaBio), Energia e Commodities Ambientais na B3 (FL-036).

Implementa a especificação regulatória e o motor determinístico da Plataforma CBIO no Balcão B3 (NoMe)
conforme a Política Nacional de Biocombustíveis (RenovaBio — Lei nº 13.576/2017, Decreto nº 9.888/2019,
Resoluções ANP nº 758/2018 e nº 802/2019 e Portaria MME nº 419/2019):

1. Escrituração Primária (Primary Issuance): Registro de lotes de CBIOs emitidos por Produtor ou
   Importador de Biocombustível certificado pela ANP (`EMISSOR_PRIMARIO`), intermediado por instituição
   escrituradora (`ESCRITURADOR`) e lastreado em chave de acesso de Nota Fiscal Eletrônica (NF-e de 44 dígitos).
2. Negociação Secundária no Balcão B3: Transferência integral ou parcial (desmembramento de lote)
   entre produtores, distribuidores de combustíveis (`PARTE_OBRIGADA`) e investidores não obrigados
   (`INVESTIDOR_NAO_OBRIGADO`), com bloqueio estrito de negociação para ativos aposentados ou bloqueados.
3. Aposentadoria Definitiva (Retirement): Baixa irrevogável do CBIO de circulação (`APOSENTADO`),
   prevenindo dupla contagem, emitindo Certidão de Aposentadoria (`CbioRetirementReceipt`) onde
   1 CBIO = 1,00 tCO2e evitada.
4. Apuração de Conformidade de Meta Compulsória ANP (`AnpTargetCompliance`): Cálculo do percentual
   de cumprimento anual por distribuidora, verificação da tolerância legal de carry-over (até 15% para o
   ano seguinte se atingido >= 85% no ano corrente) e invariante de conservação total:
   `Emitidos == Em_Circulacao + Aposentados`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Constantes de Precisão e Domínio Regulatório RenovaBio / B3
# ---------------------------------------------------------------------------

TWO_PLACES = Decimal("0.01")
TCO2E_PER_CBIO = Decimal("1.00")
NFE_KEY_LENGTH = 44
CNPJ_LENGTH = 14
ANP_CARRYOVER_MIN_COMPLIANCE_PCT = Decimal("85.00")
ANP_FULL_COMPLIANCE_PCT = Decimal("100.00")
DEFAULT_PENALTY_PER_CBIO_BRL = Decimal("100.00")
MIN_ANP_FINE_BRL = Decimal("100000.00")
MAX_ANP_FINE_BRL = Decimal("50000000.00")


# ---------------------------------------------------------------------------
# Exceções Customizadas
# ---------------------------------------------------------------------------


class CbioError(Exception):
    """Exceção base para erros do motor da Plataforma CBIO / RenovaBio da B3."""


class CbioValidationError(CbioError):
    """Erro de validação cadastral, chave de NF-e, CNPJ, certificado ANP ou parâmetros."""


class CbioRetiredAssetError(CbioError):
    """Violação da Regra de Ouro: tentativa de negociar ou aposentar novamente um CBIO já aposentado."""


class CbioInsufficientBalanceError(CbioError):
    """Saldo em circulação insuficiente no lote ou incompatibilidade de titularidade na custódia."""


class CbioConservationError(CbioError):
    """Violação da identidade de conservação: Emitidos != Em_Circulacao + Aposentados."""


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------


class CbioParticipantRole(str, Enum):
    """Papéis regulatórios dos participantes na Plataforma CBIO da B3 (RenovaBio)."""

    EMISSOR_PRIMARIO = "EMISSOR_PRIMARIO"
    ESCRITURADOR = "ESCRITURADOR"
    PARTE_OBRIGADA = "PARTE_OBRIGADA"
    INVESTIDOR_NAO_OBRIGADO = "INVESTIDOR_NAO_OBRIGADO"

    @classmethod
    def from_str(cls, value: Any) -> CbioParticipantRole:
        """Converte uma string para CbioParticipantRole validada."""
        if isinstance(value, CbioParticipantRole):
            return value
        if not value:
            raise CbioValidationError("Papel do participante CBIO não pode ser vazio.")
        normalized = str(value).strip().upper()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise CbioValidationError(
                f"Papel de participante CBIO inválido: {value!r}. "
                f"Esperado um de: {[e.value for e in cls]}"
            ) from exc


class CbioStatus(str, Enum):
    """Estados do ciclo de vida do lote de CBIO na depositária B3."""

    EM_CIRCULACAO = "EM_CIRCULACAO"
    BLOQUEADO = "BLOQUEADO"
    APOSENTADO = "APOSENTADO"

    @classmethod
    def from_str(cls, value: Any) -> CbioStatus:
        """Converte uma string para CbioStatus validado."""
        if isinstance(value, CbioStatus):
            return value
        if not value:
            raise CbioValidationError("Status do lote CBIO não pode ser vazio.")
        normalized = str(value).strip().upper()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise CbioValidationError(
                f"Status CBIO inválido: {value!r}. "
                f"Esperado um de: {[e.value for e in cls]}"
            ) from exc


class BiofuelType(str, Enum):
    """Rotas tecnológicas de biocombustíveis elegíveis à emissão de CBIOs (ANP / RenovaCalc)."""

    ETANOL_HIDRATADO = "ETANOL_HIDRATADO"
    ETANOL_ANIDRO = "ETANOL_ANIDRO"
    BIODIESEL = "BIODIESEL"
    BIOMETANO = "BIOMETANO"
    BIOQUEROSENE_SAF = "BIOQUEROSENE_SAF"

    @classmethod
    def from_str(cls, value: Any) -> BiofuelType:
        """Converte uma string para BiofuelType validado."""
        if isinstance(value, BiofuelType):
            return value
        if not value:
            raise CbioValidationError("Tipo de biocombustível não pode ser vazio.")
        normalized = str(value).strip().upper()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise CbioValidationError(
                f"Tipo de biocombustível inválido: {value!r}. "
                f"Esperado um de: {[e.value for e in cls]}"
            ) from exc


# ---------------------------------------------------------------------------
# Funções Auxiliares de Validação e Conversão Determinística
# ---------------------------------------------------------------------------


def _to_decimal(value: Any, field_name: str) -> Decimal:
    """Converte valor numérico/string para Decimal com validação estrita."""
    if value is None or isinstance(value, bool):
        raise CbioValidationError(f"Campo decimal obrigatório inválido: {field_name!r}.")
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CbioValidationError(
            f"Valor decimal inválido para {field_name!r}: {value!r}."
        ) from exc
    if not dec.is_finite():
        raise CbioValidationError(f"Valor decimal não finito para {field_name!r}: {value!r}.")
    return dec


def _quantize_2(value: Decimal) -> Decimal:
    """Arredonda um Decimal para 2 casas decimais pelo critério ROUND_HALF_UP."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _validate_nfe_key(nfe_key: str) -> str:
    """Valida se a chave de acesso da NF-e possui exatamente 44 dígitos numéricos."""
    if not nfe_key or not isinstance(nfe_key, str):
        raise CbioValidationError("Chave de acesso da NF-e é obrigatória.")
    cleaned = nfe_key.strip()
    if len(cleaned) != NFE_KEY_LENGTH or not cleaned.isdigit():
        raise CbioValidationError(
            f"Chave de acesso da NF-e inválida: {nfe_key!r}. "
            f"Deve conter exatamente {NFE_KEY_LENGTH} dígitos numéricos."
        )
    return cleaned


def _validate_cnpj(cnpj: str, field_name: str = "cnpj") -> str:
    """Normaliza e valida se o CNPJ possui 14 dígitos numéricos."""
    if not cnpj or not isinstance(cnpj, str):
        raise CbioValidationError(f"CNPJ obrigatório ausente no campo {field_name!r}.")
    digits = "".join(ch for ch in cnpj.strip() if ch.isdigit())
    if len(digits) != CNPJ_LENGTH:
        raise CbioValidationError(
            f"CNPJ inválido em {field_name!r}: {cnpj!r} (esperado 14 dígitos numéricos)."
        )
    return digits


def _parse_iso_timestamp(ts: Union[str, datetime], field_name: str) -> datetime:
    """Converte string ISO-8601 (incluindo sufixo 'Z') ou datetime para objeto datetime."""
    if isinstance(ts, datetime):
        return ts
    if not ts or not isinstance(ts, str):
        raise CbioValidationError(f"Timestamp obrigatório inválido em {field_name!r}.")
    normalized = ts.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CbioValidationError(
            f"Formato de data/hora ISO-8601 inválido em {field_name!r}: {ts!r}."
        ) from exc


# ---------------------------------------------------------------------------
# Modelos de Dados (Dataclasses)
# ---------------------------------------------------------------------------


@dataclass
class CbioBatch:
    """Representa um lote ou sub-lote escriturado e custodiado de CBIOs na B3.

    Rastreia tanto a origem primária (usina emissora, escriturador, certificado ANP e
    chave de 44 dígitos da NF-e) quanto a custódia atual (`current_holder_id`),
    as quantidades em circulação e aposentadas, e o estado atual (`CbioStatus`).
    """

    batch_id: str
    root_batch_id: str
    issuer_id: str
    issuer_name: str
    issuer_cnpj: str
    bookkeeper_id: str
    bookkeeper_name: str
    bookkeeper_cnpj: str
    anp_certificate_code: str
    nfe_key: str
    biofuel_type: BiofuelType
    energy_efficiency_note_gco2_mj: Decimal
    current_holder_id: str
    current_holder_name: str
    current_holder_cnpj: str
    current_holder_role: CbioParticipantRole
    initial_quantity: int
    circulating_quantity: int
    retired_quantity: int
    unit_price_brl: Decimal
    issuance_timestamp: datetime
    status: CbioStatus = CbioStatus.EM_CIRCULACAO

    def __post_init__(self) -> None:
        if not self.batch_id or not str(self.batch_id).strip():
            raise CbioValidationError("Identificador do lote CBIO (batch_id) não pode ser vazio.")
        self.batch_id = str(self.batch_id).strip()
        self.root_batch_id = str(self.root_batch_id or self.batch_id).strip()
        if not self.issuer_id or not str(self.issuer_id).strip():
            raise CbioValidationError("Identificador do emissor primário (issuer_id) é obrigatório.")
        if not self.bookkeeper_id or not str(self.bookkeeper_id).strip():
            raise CbioValidationError("Identificador do escriturador (bookkeeper_id) é obrigatório.")
        if not self.anp_certificate_code or not str(self.anp_certificate_code).strip():
            raise CbioValidationError(
                "Código do Certificado da Produção Eficiente ANP (anp_certificate_code) é obrigatório."
            )
        self.issuer_cnpj = _validate_cnpj(self.issuer_cnpj, "issuer_cnpj")
        self.bookkeeper_cnpj = _validate_cnpj(self.bookkeeper_cnpj, "bookkeeper_cnpj")
        self.current_holder_cnpj = _validate_cnpj(self.current_holder_cnpj, "current_holder_cnpj")
        self.nfe_key = _validate_nfe_key(self.nfe_key)
        self.biofuel_type = BiofuelType.from_str(self.biofuel_type)
        self.current_holder_role = CbioParticipantRole.from_str(self.current_holder_role)
        self.status = CbioStatus.from_str(self.status)
        self.energy_efficiency_note_gco2_mj = _quantize_2(
            _to_decimal(self.energy_efficiency_note_gco2_mj, "energy_efficiency_note_gco2_mj")
        )
        if self.energy_efficiency_note_gco2_mj <= Decimal("0"):
            raise CbioValidationError(
                "Nota de Eficiência Energético-Ambiental (NEEA) deve ser estritamente positiva."
            )
        self.unit_price_brl = _quantize_2(_to_decimal(self.unit_price_brl, "unit_price_brl"))
        if self.unit_price_brl <= Decimal("0"):
            raise CbioValidationError("Preço unitário de referência/negociação do CBIO deve ser > 0.")
        self.issuance_timestamp = _parse_iso_timestamp(
            self.issuance_timestamp, "issuance_timestamp"
        )

        if not isinstance(self.initial_quantity, int) or self.initial_quantity <= 0:
            raise CbioValidationError("initial_quantity deve ser um inteiro positivo (> 0).")
        if not isinstance(self.circulating_quantity, int) or self.circulating_quantity < 0:
            raise CbioValidationError("circulating_quantity deve ser um inteiro não negativo (>= 0).")
        if not isinstance(self.retired_quantity, int) or self.retired_quantity < 0:
            raise CbioValidationError("retired_quantity deve ser um inteiro não negativo (>= 0).")
        if self.circulating_quantity + self.retired_quantity != self.initial_quantity:
            raise CbioConservationError(
                f"Inconsistência interna no lote {self.batch_id}: "
                f"circulating ({self.circulating_quantity}) + retired ({self.retired_quantity}) "
                f"!= initial_quantity ({self.initial_quantity})."
            )

    @property
    def equivalent_tco2e(self) -> Decimal:
        """Toneladas de CO2 equivalente representadas pela quantidade atual do lote (1 CBIO = 1 tCO2e)."""
        return _quantize_2(Decimal(self.initial_quantity) * TCO2E_PER_CBIO)

    @property
    def is_tradable(self) -> bool:
        """Indica se o lote possui saldo livre elegível à negociação secundária."""
        return self.status == CbioStatus.EM_CIRCULACAO and self.circulating_quantity > 0

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o lote para dicionário compatível com JSON."""
        data = asdict(self)
        data["biofuel_type"] = self.biofuel_type.value
        data["current_holder_role"] = self.current_holder_role.value
        data["status"] = self.status.value
        data["energy_efficiency_note_gco2_mj"] = str(self.energy_efficiency_note_gco2_mj)
        data["unit_price_brl"] = str(self.unit_price_brl)
        data["issuance_timestamp"] = self.issuance_timestamp.isoformat()
        data["equivalent_tco2e"] = str(self.equivalent_tco2e)
        return data


@dataclass
class CbioSecondaryTrade:
    """Registro de transferência secundária de CBIOs no Balcão B3."""

    trade_id: str
    source_batch_id: str
    target_batch_id: str
    seller_id: str
    buyer_id: str
    buyer_name: str
    buyer_cnpj: str
    buyer_role: CbioParticipantRole
    quantity: int
    unit_price_brl: Decimal
    financial_volume_brl: Decimal
    trade_timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o negócio secundário para dicionário compatível com JSON."""
        return {
            "trade_id": self.trade_id,
            "source_batch_id": self.source_batch_id,
            "target_batch_id": self.target_batch_id,
            "seller_id": self.seller_id,
            "buyer_id": self.buyer_id,
            "buyer_name": self.buyer_name,
            "buyer_cnpj": self.buyer_cnpj,
            "buyer_role": self.buyer_role.value,
            "quantity": self.quantity,
            "unit_price_brl": str(self.unit_price_brl),
            "financial_volume_brl": str(self.financial_volume_brl),
            "trade_timestamp": self.trade_timestamp.isoformat(),
        }


@dataclass
class CbioRetirementReceipt:
    """Certidão / Comprovante de Aposentadoria Definitiva de CBIO emitido pela B3.

    Documento comprobatório oficial utilizado pelos Distribuidores de Combustíveis
    (`PARTE_OBRIGADA`) perante a ANP e por investidores voluntários (`INVESTIDOR_NAO_OBRIGADO`)
    em auditorias de inventário de emissões GEE (GHG Protocol / ESG).
    """

    receipt_id: str
    instruction_id: str
    batch_id: str
    root_batch_id: str
    holder_id: str
    holder_name: str
    holder_cnpj: str
    holder_role: CbioParticipantRole
    compliance_year: int
    retired_quantity: int
    equivalent_tco2e: Decimal
    nfe_key: str
    anp_certificate_code: str
    biofuel_type: BiofuelType
    beneficiary_justification: str
    retirement_timestamp: datetime
    status: CbioStatus = CbioStatus.APOSENTADO

    def __post_init__(self) -> None:
        if self.status != CbioStatus.APOSENTADO:
            raise CbioValidationError(
                "Recibo de aposentadoria deve ter obrigatoriamente status APOSENTADO."
            )
        if self.retired_quantity <= 0:
            raise CbioValidationError("Quantidade aposentada no recibo deve ser > 0.")

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a certidão de aposentadoria para dicionário compatível com JSON."""
        return {
            "receipt_id": self.receipt_id,
            "instruction_id": self.instruction_id,
            "batch_id": self.batch_id,
            "root_batch_id": self.root_batch_id,
            "holder_id": self.holder_id,
            "holder_name": self.holder_name,
            "holder_cnpj": self.holder_cnpj,
            "holder_role": self.holder_role.value,
            "compliance_year": self.compliance_year,
            "retired_quantity": self.retired_quantity,
            "equivalent_tco2e": str(self.equivalent_tco2e),
            "nfe_key": self.nfe_key,
            "anp_certificate_code": self.anp_certificate_code,
            "biofuel_type": self.biofuel_type.value,
            "beneficiary_justification": self.beneficiary_justification,
            "retirement_timestamp": self.retirement_timestamp.isoformat(),
            "status": self.status.value,
        }


@dataclass
class AnpTargetCompliance:
    """Relatório de apuração de cumprimento da meta compulsória anual ANP (RenovaBio).

    Avalia exclusivamente os CBIOs efetivamente aposentados (`retired_cbios`) pelo
    distribuidor (`PARTE_OBRIGADA`) para o ano-meta de referência, reportando separadamente
    eventuais CBIOs ainda em carteira (`circulating_held_cbios`) que não contam para a meta
    enquanto não forem aposentados.
    """

    distributor_id: str
    distributor_name: str
    cnpj: str
    compliance_year: int
    mandatory_target_cbios: int
    retired_cbios: int
    circulating_held_cbios: int
    deficit_cbios: int
    surplus_cbios: int
    compliance_percentage: Decimal
    is_fully_compliant: bool
    within_carryover_tolerance: bool
    carryover_to_next_year_cbios: int
    estimated_fine_brl: Decimal

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a apuração de meta ANP para dicionário compatível com JSON."""
        return {
            "distributor_id": self.distributor_id,
            "distributor_name": self.distributor_name,
            "cnpj": self.cnpj,
            "compliance_year": self.compliance_year,
            "mandatory_target_cbios": self.mandatory_target_cbios,
            "retired_cbios": self.retired_cbios,
            "circulating_held_cbios": self.circulating_held_cbios,
            "deficit_cbios": self.deficit_cbios,
            "surplus_cbios": self.surplus_cbios,
            "compliance_percentage": str(self.compliance_percentage),
            "is_fully_compliant": self.is_fully_compliant,
            "within_carryover_tolerance": self.within_carryover_tolerance,
            "carryover_to_next_year_cbios": self.carryover_to_next_year_cbios,
            "estimated_fine_brl": str(self.estimated_fine_brl),
        }


@dataclass
class CbioPlatformSummary:
    """Resumo consolidado do livro da Plataforma CBIO B3 após processamento de lote."""

    total_issued_cbios: int
    total_in_circulation_cbios: int
    total_retired_cbios: int
    total_retired_tco2e: Decimal
    total_secondary_volume_brl: Decimal
    conservation_invariant_ok: bool
    batches: List[CbioBatch] = field(default_factory=list)
    trades: List[CbioSecondaryTrade] = field(default_factory=list)
    retirement_receipts: List[CbioRetirementReceipt] = field(default_factory=list)
    anp_compliance_reports: List[AnpTargetCompliance] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o resumo consolidado da plataforma para dicionário JSON."""
        return {
            "total_issued_cbios": self.total_issued_cbios,
            "total_in_circulation_cbios": self.total_in_circulation_cbios,
            "total_retired_cbios": self.total_retired_cbios,
            "total_retired_tco2e": str(self.total_retired_tco2e),
            "total_secondary_volume_brl": str(self.total_secondary_volume_brl),
            "conservation_invariant_ok": self.conservation_invariant_ok,
            "batches": [b.to_dict() for b in self.batches],
            "trades": [t.to_dict() for t in self.trades],
            "retirement_receipts": [r.to_dict() for r in self.retirement_receipts],
            "anp_compliance_reports": [c.to_dict() for c in self.anp_compliance_reports],
        }


# ---------------------------------------------------------------------------
# Motor Principal da Plataforma CBIO B3 (RenovaBio)
# ---------------------------------------------------------------------------


class CbioPlatformEngine:
    """Motor determinístico de Escrituração, Negociação Secundária, Aposentadoria e Meta ANP de CBIOs.

    Garante as regras de negócio da depositária Balcão B3:
    - Unicidade de lotes e chaves NF-e na emissão primária.
    - Rastreabilidade completa de lotes desmembrados (`root_batch_id` e `nfe_key`).
    - Bloqueio perpétuo e irrevogável de CBIOs aposentados contra qualquer renegociação ou
      dupla aposentadoria (`CbioRetiredAssetError`).
    - Invariante de conservação estrita: `Emitidos == Em_Circulacao + Aposentados`.
    """

    def __init__(
        self,
        penalty_reference_per_cbio_brl: Union[str, Decimal] = DEFAULT_PENALTY_PER_CBIO_BRL,
    ) -> None:
        self.penalty_reference_per_cbio_brl: Decimal = _quantize_2(
            _to_decimal(penalty_reference_per_cbio_brl, "penalty_reference_per_cbio_brl")
        )
        if self.penalty_reference_per_cbio_brl <= Decimal("0"):
            raise CbioValidationError("Valor de referência de multa por CBIO deve ser positivo.")

        self._batches: Dict[str, CbioBatch] = {}
        self._registered_nfe_keys: Dict[str, str] = {}  # nfe_key -> root_batch_id
        self._total_primary_issued: int = 0
        self._trades: List[CbioSecondaryTrade] = []
        self._retirement_receipts: List[CbioRetirementReceipt] = []
        self._anp_targets: Dict[Tuple[str, int], Dict[str, Any]] = {}

    @property
    def batches(self) -> Dict[str, CbioBatch]:
        """Retorna os lotes registrados no livro de custódia."""
        return dict(self._batches)

    @property
    def trades(self) -> List[CbioSecondaryTrade]:
        """Retorna a lista cronológica de operações secundárias liquidadas."""
        return list(self._trades)

    @property
    def retirement_receipts(self) -> List[CbioRetirementReceipt]:
        """Retorna a lista de certidões de aposentadoria emitidas."""
        return list(self._retirement_receipts)

    @property
    def total_issued_cbios(self) -> int:
        """Total acumulado de CBIOs emitidos primariamente pelos escrituradores."""
        return self._total_primary_issued

    @property
    def total_in_circulation_cbios(self) -> int:
        """Total de CBIOs atualmente ativos (não aposentados) em todos os lotes."""
        return sum(batch.circulating_quantity for batch in self._batches.values())

    @property
    def total_retired_cbios(self) -> int:
        """Total de CBIOs retirados permanentemente de circulação."""
        return sum(batch.retired_quantity for batch in self._batches.values())

    def verify_conservation_invariant(self) -> bool:
        """Verifica se `Emitidos == Em_Circulacao + Aposentados` em todo o livro e nos recibos.

        Raises:
            CbioConservationError: Caso haja divergência de quantidades.
        """
        in_circulation = self.total_in_circulation_cbios
        retired_in_batches = self.total_retired_cbios
        retired_in_receipts = sum(r.retired_quantity for r in self._retirement_receipts)
        sum_batch_initials = sum(b.initial_quantity for b in self._batches.values())

        if retired_in_batches != retired_in_receipts:
            raise CbioConservationError(
                f"Divergência entre CBIOs aposentados nos lotes ({retired_in_batches}) "
                f"e total nas certidões de aposentadoria ({retired_in_receipts})."
            )
        if self._total_primary_issued != sum_batch_initials:
            raise CbioConservationError(
                f"Divergência entre total emitido primariamente ({self._total_primary_issued}) "
                f"e soma das quantidades dos lotes ({sum_batch_initials})."
            )
        if self._total_primary_issued != (in_circulation + retired_in_batches):
            raise CbioConservationError(
                f"Violação da conservação de massa CBIO: Emitidos ({self._total_primary_issued}) != "
                f"Em_Circulacao ({in_circulation}) + Aposentados ({retired_in_batches})."
            )
        return True

    def issue_primary_batch(
        self,
        *,
        batch_id: str,
        issuer_id: str,
        issuer_name: str,
        issuer_cnpj: str,
        issuer_role: Union[str, CbioParticipantRole] = CbioParticipantRole.EMISSOR_PRIMARIO,
        bookkeeper_id: str,
        bookkeeper_name: str,
        bookkeeper_cnpj: str,
        anp_certificate_code: str,
        nfe_key: str,
        biofuel_type: Union[str, BiofuelType],
        energy_efficiency_note_gco2_mj: Union[str, Decimal],
        quantity: int,
        reference_unit_price_brl: Union[str, Decimal],
        issuance_timestamp: Union[str, datetime],
    ) -> CbioBatch:
        """Executa a Escrituração Primária de um lote de CBIOs na depositária B3.

        Regras:
        - O emissor deve possuir o papel `EMISSOR_PRIMARIO`.
        - A chave de acesso da NF-e deve possuir 44 dígitos numéricos e não pode ter sido
          utilizada em outra emissão primária anterior (prevenção de emissão duplicada sobre
          a mesma nota fiscal).
        - O lote nasce com `status = CbioStatus.EM_CIRCULACAO` sob custódia inicial do emissor.
        """
        role = CbioParticipantRole.from_str(issuer_role)
        if role != CbioParticipantRole.EMISSOR_PRIMARIO:
            raise CbioValidationError(
                f"Apenas participantes com papel EMISSOR_PRIMARIO podem originar lastro de CBIO "
                f"(recebido: {role.value})."
            )

        cleaned_batch_id = str(batch_id or "").strip()
        if cleaned_batch_id in self._batches:
            raise CbioValidationError(
                f"Lote CBIO com identificador {cleaned_batch_id!r} já registrado na plataforma."
            )

        validated_nfe = _validate_nfe_key(nfe_key)
        if validated_nfe in self._registered_nfe_keys:
            existing_batch = self._registered_nfe_keys[validated_nfe]
            raise CbioValidationError(
                f"Chave de NF-e {validated_nfe!r} já utilizada na emissão primária do lote "
                f"{existing_batch!r} (proibida duplicidade de lastro fiscal)."
            )

        batch = CbioBatch(
            batch_id=cleaned_batch_id,
            root_batch_id=cleaned_batch_id,
            issuer_id=issuer_id,
            issuer_name=issuer_name,
            issuer_cnpj=issuer_cnpj,
            bookkeeper_id=bookkeeper_id,
            bookkeeper_name=bookkeeper_name,
            bookkeeper_cnpj=bookkeeper_cnpj,
            anp_certificate_code=anp_certificate_code,
            nfe_key=validated_nfe,
            biofuel_type=BiofuelType.from_str(biofuel_type),
            energy_efficiency_note_gco2_mj=_to_decimal(
                energy_efficiency_note_gco2_mj, "energy_efficiency_note_gco2_mj"
            ),
            current_holder_id=issuer_id,
            current_holder_name=issuer_name,
            current_holder_cnpj=issuer_cnpj,
            current_holder_role=role,
            initial_quantity=quantity,
            circulating_quantity=quantity,
            retired_quantity=0,
            unit_price_brl=_to_decimal(reference_unit_price_brl, "reference_unit_price_brl"),
            issuance_timestamp=_parse_iso_timestamp(issuance_timestamp, "issuance_timestamp"),
            status=CbioStatus.EM_CIRCULACAO,
        )

        self._batches[batch.batch_id] = batch
        self._registered_nfe_keys[validated_nfe] = batch.batch_id
        self._total_primary_issued += quantity
        self.verify_conservation_invariant()
        return batch

    def transfer_secondary(
        self,
        *,
        trade_id: str,
        batch_id: str,
        seller_id: str,
        buyer_id: str,
        buyer_name: str,
        buyer_cnpj: str,
        buyer_role: Union[str, CbioParticipantRole],
        quantity: int,
        unit_price_brl: Union[str, Decimal],
        trade_timestamp: Union[str, datetime],
        new_batch_id: Optional[str] = None,
    ) -> CbioSecondaryTrade:
        """Executa uma transferência de titularidade no Mercado Secundário do Balcão B3.

        Regras Determinísticas:
        - Se o lote estiver com status `APOSENTADO`, levanta `CbioRetiredAssetError`
          (Regra de Ouro: CBIO aposentado jamais pode voltar a ser negociado).
        - Se o lote estiver `BLOQUEADO`, levanta `CbioValidationError`.
        - O vendedor informado (`seller_id`) deve ser o titular atual do lote (`current_holder_id`).
        - Se `quantity == batch.circulating_quantity` e `batch.retired_quantity == 0`, transfere
          integralmente a titularidade do próprio lote (ou cria `new_batch_id` se aplicável).
        - Se a venda for parcial (`quantity < batch.circulating_quantity`) ou se o lote de origem
          já possuir parcela aposentada, desmembra a quantidade negociada em um novo sub-lote
          rastreável ao lote e à NF-e originais, preservando `Emitidos == Em_Circulacao + Aposentados`.
        """
        if not trade_id or not str(trade_id).strip():
            raise CbioValidationError("Identificador do negócio secundário (trade_id) é obrigatório.")
        if batch_id not in self._batches:
            raise CbioValidationError(f"Lote CBIO {batch_id!r} não encontrado na custódia B3.")

        source_batch = self._batches[batch_id]

        # Regra de Ouro: Bloqueio absoluto de CBIO aposentado
        if source_batch.status == CbioStatus.APOSENTADO:
            raise CbioRetiredAssetError(
                f"Operação rejeitada ({trade_id}): O lote {batch_id!r} encontra-se APOSENTADO "
                f"e foi retirado permanentemente de circulação (proibida renegociação)."
            )
        if source_batch.status == CbioStatus.BLOQUEADO:
            raise CbioValidationError(
                f"Operação rejeitada ({trade_id}): O lote {batch_id!r} encontra-se BLOQUEADO."
            )

        if source_batch.current_holder_id != seller_id:
            raise CbioInsufficientBalanceError(
                f"Titularidade divergente no lote {batch_id!r}: vendedor informado {seller_id!r} "
                f"não corresponde ao custodiante atual {source_batch.current_holder_id!r}."
            )

        if seller_id == buyer_id:
            raise CbioValidationError(
                f"Operação inválida ({trade_id}): vendedor e comprador não podem ser o mesmo participante."
            )

        if not isinstance(quantity, int) or quantity <= 0:
            raise CbioValidationError("Quantidade negociada de CBIOs deve ser um inteiro positivo.")

        if quantity > source_batch.circulating_quantity:
            raise CbioInsufficientBalanceError(
                f"Saldo em circulação insuficiente no lote {batch_id!r}: solicitado {quantity}, "
                f"disponível em circulação {source_batch.circulating_quantity} "
                f"(aposentado no lote: {source_batch.retired_quantity})."
            )

        parsed_buyer_role = CbioParticipantRole.from_str(buyer_role)
        validated_buyer_cnpj = _validate_cnpj(buyer_cnpj, "buyer_cnpj")
        price_dec = _quantize_2(_to_decimal(unit_price_brl, "unit_price_brl"))
        if price_dec <= Decimal("0"):
            raise CbioValidationError("Preço unitário na negociação secundária deve ser > 0.")
        parsed_ts = _parse_iso_timestamp(trade_timestamp, "trade_timestamp")
        financial_volume = _quantize_2(Decimal(quantity) * price_dec)

        # Caso 1: Transferência integral de lote sem aposentadorias parciais prévias e sem pedido de split
        if (
            quantity == source_batch.circulating_quantity
            and source_batch.retired_quantity == 0
            and not new_batch_id
        ):
            source_batch.current_holder_id = buyer_id
            source_batch.current_holder_name = buyer_name
            source_batch.current_holder_cnpj = validated_buyer_cnpj
            source_batch.current_holder_role = parsed_buyer_role
            source_batch.unit_price_brl = price_dec
            target_batch_id = source_batch.batch_id
        else:
            # Caso 2: Desmembramento (split) de lote mantendo conservação exata de custódia
            split_id = (
                str(new_batch_id).strip()
                if new_batch_id
                else f"{source_batch.batch_id}-T{len(self._trades) + 1:02d}"
            )
            if split_id in self._batches:
                raise CbioValidationError(
                    f"Identificador de sub-lote desmembrado {split_id!r} já existe na custódia."
                )

            source_batch.initial_quantity -= quantity
            source_batch.circulating_quantity -= quantity
            if source_batch.circulating_quantity == 0 and source_batch.retired_quantity > 0:
                source_batch.status = CbioStatus.APOSENTADO

            split_batch = CbioBatch(
                batch_id=split_id,
                root_batch_id=source_batch.root_batch_id,
                issuer_id=source_batch.issuer_id,
                issuer_name=source_batch.issuer_name,
                issuer_cnpj=source_batch.issuer_cnpj,
                bookkeeper_id=source_batch.bookkeeper_id,
                bookkeeper_name=source_batch.bookkeeper_name,
                bookkeeper_cnpj=source_batch.bookkeeper_cnpj,
                anp_certificate_code=source_batch.anp_certificate_code,
                nfe_key=source_batch.nfe_key,
                biofuel_type=source_batch.biofuel_type,
                energy_efficiency_note_gco2_mj=source_batch.energy_efficiency_note_gco2_mj,
                current_holder_id=buyer_id,
                current_holder_name=buyer_name,
                current_holder_cnpj=validated_buyer_cnpj,
                current_holder_role=parsed_buyer_role,
                initial_quantity=quantity,
                circulating_quantity=quantity,
                retired_quantity=0,
                unit_price_brl=price_dec,
                issuance_timestamp=source_batch.issuance_timestamp,
                status=CbioStatus.EM_CIRCULACAO,
            )
            self._batches[split_id] = split_batch
            target_batch_id = split_id

        trade = CbioSecondaryTrade(
            trade_id=str(trade_id).strip(),
            source_batch_id=source_batch.batch_id,
            target_batch_id=target_batch_id,
            seller_id=seller_id,
            buyer_id=buyer_id,
            buyer_name=buyer_name,
            buyer_cnpj=validated_buyer_cnpj,
            buyer_role=parsed_buyer_role,
            quantity=quantity,
            unit_price_brl=price_dec,
            financial_volume_brl=financial_volume,
            trade_timestamp=parsed_ts,
        )
        self._trades.append(trade)
        self.verify_conservation_invariant()
        return trade

    def retire_cbios(
        self,
        *,
        instruction_id: str,
        batch_id: str,
        holder_id: str,
        quantity: int,
        compliance_year: int,
        beneficiary_justification: str,
        retirement_timestamp: Union[str, datetime],
        receipt_id: Optional[str] = None,
    ) -> CbioRetirementReceipt:
        """Realiza a Aposentadoria Definitiva (Retirement) de CBIOs na depositária B3.

        Regras de Ouro:
        - CBIOs já aposentados (`status == CbioStatus.APOSENTADO`) não podem sofrer nova
          aposentadoria (`CbioRetiredAssetError`), prevenindo dupla contagem.
        - Apenas o detentor atual (`current_holder_id`) pode solicitar a aposentadoria.
        - A quantidade aposentada é subtraída de `circulating_quantity` e somada a
          `retired_quantity`. Quando `circulating_quantity` atinge zero, o lote assume
          definitivamente o status `CbioStatus.APOSENTADO`.
        - Gera uma Certidão de Aposentadoria (`CbioRetirementReceipt`) imutável com
          equivalência exata `1 CBIO = 1.00 tCO2e`.
        """
        if not instruction_id or not str(instruction_id).strip():
            raise CbioValidationError("instruction_id é obrigatório para aposentadoria de CBIO.")
        if batch_id not in self._batches:
            raise CbioValidationError(f"Lote CBIO {batch_id!r} não encontrado na custódia B3.")

        batch = self._batches[batch_id]

        if batch.status == CbioStatus.APOSENTADO:
            raise CbioRetiredAssetError(
                f"Dupla contagem bloqueada ({instruction_id}): O lote {batch_id!r} já está "
                f"integralmente APOSENTADO e não pode ser aposentado novamente."
            )
        if batch.status == CbioStatus.BLOQUEADO:
            raise CbioValidationError(
                f"Operação rejeitada ({instruction_id}): O lote {batch_id!r} encontra-se BLOQUEADO."
            )

        if batch.current_holder_id != holder_id:
            raise CbioInsufficientBalanceError(
                f"Apenas o titular atual ({batch.current_holder_id!r}) pode aposentar o lote "
                f"{batch_id!r} (solicitante: {holder_id!r})."
            )

        if not isinstance(quantity, int) or quantity <= 0:
            raise CbioValidationError("Quantidade a aposentar deve ser um inteiro positivo (> 0).")

        if quantity > batch.circulating_quantity:
            raise CbioInsufficientBalanceError(
                f"Saldo em circulação insuficiente para aposentadoria no lote {batch_id!r}: "
                f"solicitado {quantity}, disponível em circulação {batch.circulating_quantity}."
            )

        if not isinstance(compliance_year, int) or compliance_year < 2019:
            raise CbioValidationError(
                f"Ano-meta RenovaBio inválido: {compliance_year!r} (esperado >= 2019)."
            )

        if not beneficiary_justification or not str(beneficiary_justification).strip():
            raise CbioValidationError(
                "Justificativa / finalidade da aposentadoria (beneficiary_justification) é obrigatória."
            )

        parsed_ts = _parse_iso_timestamp(retirement_timestamp, "retirement_timestamp")

        # Atualiza os saldos do lote
        batch.circulating_quantity -= quantity
        batch.retired_quantity += quantity
        if batch.circulating_quantity == 0:
            batch.status = CbioStatus.APOSENTADO

        generated_receipt_id = (
            str(receipt_id).strip()
            if receipt_id
            else f"CERT-RET-B3-{compliance_year}-{len(self._retirement_receipts) + 1:04d}"
        )
        equivalent_tco2e = _quantize_2(Decimal(quantity) * TCO2E_PER_CBIO)

        receipt = CbioRetirementReceipt(
            receipt_id=generated_receipt_id,
            instruction_id=str(instruction_id).strip(),
            batch_id=batch.batch_id,
            root_batch_id=batch.root_batch_id,
            holder_id=batch.current_holder_id,
            holder_name=batch.current_holder_name,
            holder_cnpj=batch.current_holder_cnpj,
            holder_role=batch.current_holder_role,
            compliance_year=compliance_year,
            retired_quantity=quantity,
            equivalent_tco2e=equivalent_tco2e,
            nfe_key=batch.nfe_key,
            anp_certificate_code=batch.anp_certificate_code,
            biofuel_type=batch.biofuel_type,
            beneficiary_justification=str(beneficiary_justification).strip(),
            retirement_timestamp=parsed_ts,
            status=CbioStatus.APOSENTADO,
        )
        self._retirement_receipts.append(receipt)
        self.verify_conservation_invariant()
        return receipt

    def register_anp_target(
        self,
        *,
        distributor_id: str,
        distributor_name: str,
        cnpj: str,
        compliance_year: int,
        mandatory_target_cbios: int,
    ) -> None:
        """Registra a meta individual compulsória anual estipulada pela ANP para um distribuidor."""
        if not distributor_id or not str(distributor_id).strip():
            raise CbioValidationError("distributor_id é obrigatório para registro de meta ANP.")
        if not isinstance(mandatory_target_cbios, int) or mandatory_target_cbios <= 0:
            raise CbioValidationError("Meta compulsória ANP deve ser um inteiro positivo (> 0).")
        validated_cnpj = _validate_cnpj(cnpj, "cnpj")
        key = (str(distributor_id).strip(), int(compliance_year))
        self._anp_targets[key] = {
            "distributor_id": str(distributor_id).strip(),
            "distributor_name": str(distributor_name).strip(),
            "cnpj": validated_cnpj,
            "compliance_year": int(compliance_year),
            "mandatory_target_cbios": mandatory_target_cbios,
        }

    def evaluate_anp_compliance(
        self,
        distributor_id: str,
        compliance_year: int,
        mandatory_target_cbios: Optional[int] = None,
        distributor_name: Optional[str] = None,
        cnpj: Optional[str] = None,
    ) -> AnpTargetCompliance:
        """Apura o cumprimento da meta individual compulsória ANP de um distribuidor (`PARTE_OBRIGADA`).

        Regras Regulatórias (Lei nº 13.576/2017, Art. 9º e Art. 10, § 2º):
        1. Apenas CBIOs **efetivamente aposentados** (`retired_cbios`) pelo distribuidor no
           `compliance_year` abatem a meta compulsória.
        2. CBIOs adquiridos e mantidos em custódia sem aposentadoria são contabilizados em
           `circulating_held_cbios`, mas não contam para o percentual de cumprimento.
        3. Percentual de cumprimento: `round((retired_cbios / mandatory_target_cbios) * 100, 2)`.
        4. Tolerância de Carry-Over (até 15%): se `85.00% <= compliance_percentage < 100.00%`,
           `within_carryover_tolerance = True` e o saldo devedor (`deficit_cbios`) é carregado
           para o ano seguinte sem incidência de multa imediata.
        5. Se `compliance_percentage < 85.00%`, a distribuidora está inadimplente além da
           tolerância legal (`within_carryover_tolerance = False`), sujeitando-se à multa ANP
           sobre o déficit de CBIOs (respeitando o piso de R$ 100.000,00 e teto de R$ 50.000.000,00).
        """
        key = (str(distributor_id).strip(), int(compliance_year))
        target_info = self._anp_targets.get(key)

        if target_info is None and mandatory_target_cbios is None:
            raise CbioValidationError(
                f"Meta compulsória ANP não cadastrada para distribuidor {distributor_id!r} "
                f"no ano {compliance_year}."
            )

        target_qty = (
            mandatory_target_cbios
            if mandatory_target_cbios is not None
            else int(target_info["mandatory_target_cbios"])  # type: ignore[index]
        )
        if target_qty <= 0:
            raise CbioValidationError("Meta compulsória ANP deve ser estritamente positiva (> 0).")

        dist_name = (
            distributor_name
            or (target_info["distributor_name"] if target_info else distributor_id)
        )
        dist_cnpj = _validate_cnpj(
            cnpj or (target_info["cnpj"] if target_info else "00000000000191"),
            "cnpj",
        )

        # Soma exclusivamente as certidões de aposentadoria do distribuidor no ano-meta
        retired_qty = sum(
            r.retired_quantity
            for r in self._retirement_receipts
            if r.holder_id == distributor_id and r.compliance_year == compliance_year
        )

        # Soma os CBIOs que o distribuidor ainda possui em circulação (não aposentados)
        circulating_held_qty = sum(
            b.circulating_quantity
            for b in self._batches.values()
            if b.current_holder_id == distributor_id and b.status == CbioStatus.EM_CIRCULACAO
        )

        deficit_qty = max(0, target_qty - retired_qty)
        surplus_qty = max(0, retired_qty - target_qty)

        raw_pct = (Decimal(retired_qty) / Decimal(target_qty)) * Decimal("100")
        compliance_pct = _quantize_2(raw_pct)

        is_fully_compliant = retired_qty >= target_qty
        within_carryover = compliance_pct >= ANP_CARRYOVER_MIN_COMPLIANCE_PCT

        if is_fully_compliant:
            carryover_qty = 0
            estimated_fine = Decimal("0.00")
        elif within_carryover:
            # Entre 85% e 99.99%: pode postergar até 15% para o ano seguinte sem multa
            carryover_qty = deficit_qty
            estimated_fine = Decimal("0.00")
        else:
            # Abaixo de 85%: inadimplente, sujeito a multa proporcional aos CBIOs não cumpridos
            carryover_qty = 0
            raw_fine = _quantize_2(Decimal(deficit_qty) * self.penalty_reference_per_cbio_brl)
            estimated_fine = min(MAX_ANP_FINE_BRL, max(MIN_ANP_FINE_BRL, raw_fine))

        return AnpTargetCompliance(
            distributor_id=str(distributor_id).strip(),
            distributor_name=str(dist_name).strip(),
            cnpj=dist_cnpj,
            compliance_year=int(compliance_year),
            mandatory_target_cbios=target_qty,
            retired_cbios=retired_qty,
            circulating_held_cbios=circulating_held_qty,
            deficit_cbios=deficit_qty,
            surplus_cbios=surplus_qty,
            compliance_percentage=compliance_pct,
            is_fully_compliant=is_fully_compliant,
            within_carryover_tolerance=within_carryover,
            carryover_to_next_year_cbios=carryover_qty,
            estimated_fine_brl=estimated_fine,
        )

    def evaluate_all_anp_targets(self) -> List[AnpTargetCompliance]:
        """Avalia todas as metas compulsórias ANP registradas na plataforma."""
        reports: List[AnpTargetCompliance] = []
        for dist_id, year in sorted(self._anp_targets.keys()):
            reports.append(self.evaluate_anp_compliance(dist_id, year))
        return reports

    def build_summary(self) -> CbioPlatformSummary:
        """Constrói o relatório consolidado do livro da Plataforma CBIO B3."""
        invariant_ok = self.verify_conservation_invariant()
        total_retired = self.total_retired_cbios
        total_volume_brl = _quantize_2(
            sum((t.financial_volume_brl for t in self._trades), Decimal("0.00"))
        )
        return CbioPlatformSummary(
            total_issued_cbios=self.total_issued_cbios,
            total_in_circulation_cbios=self.total_in_circulation_cbios,
            total_retired_cbios=total_retired,
            total_retired_tco2e=_quantize_2(Decimal(total_retired) * TCO2E_PER_CBIO),
            total_secondary_volume_brl=total_volume_brl,
            conservation_invariant_ok=invariant_ok,
            batches=list(self._batches.values()),
            trades=list(self._trades),
            retirement_receipts=list(self._retirement_receipts),
            anp_compliance_reports=self.evaluate_all_anp_targets(),
        )

    def process_batch_payload(self, payload: Dict[str, Any]) -> CbioPlatformSummary:
        """Processa um payload estruturado de emissões, negócios secundários, aposentadorias e metas ANP."""
        if not isinstance(payload, dict):
            raise CbioValidationError("Payload de lote CBIO deve ser um dicionário JSON.")

        if "penalty_reference_per_cbio_brl" in payload:
            self.penalty_reference_per_cbio_brl = _quantize_2(
                _to_decimal(
                    payload["penalty_reference_per_cbio_brl"],
                    "penalty_reference_per_cbio_brl",
                )
            )

        for issuance in payload.get("primary_issuances", []):
            self.issue_primary_batch(
                batch_id=issuance["batch_id"],
                issuer_id=issuance["issuer_id"],
                issuer_name=issuance["issuer_name"],
                issuer_cnpj=issuance["issuer_cnpj"],
                issuer_role=issuance.get("issuer_role", CbioParticipantRole.EMISSOR_PRIMARIO),
                bookkeeper_id=issuance["bookkeeper_id"],
                bookkeeper_name=issuance["bookkeeper_name"],
                bookkeeper_cnpj=issuance["bookkeeper_cnpj"],
                anp_certificate_code=issuance["anp_certificate_code"],
                nfe_key=issuance["nfe_key"],
                biofuel_type=issuance["biofuel_type"],
                energy_efficiency_note_gco2_mj=issuance["energy_efficiency_note_gco2_mj"],
                quantity=int(issuance["quantity"]),
                reference_unit_price_brl=issuance["reference_unit_price_brl"],
                issuance_timestamp=issuance["issuance_timestamp"],
            )

        for trade in payload.get("secondary_trades", []):
            self.transfer_secondary(
                trade_id=trade["trade_id"],
                batch_id=trade["batch_id"],
                seller_id=trade["seller_id"],
                buyer_id=trade["buyer_id"],
                buyer_name=trade["buyer_name"],
                buyer_cnpj=trade["buyer_cnpj"],
                buyer_role=trade["buyer_role"],
                quantity=int(trade["quantity"]),
                unit_price_brl=trade["unit_price_brl"],
                trade_timestamp=trade["trade_timestamp"],
                new_batch_id=trade.get("new_batch_id"),
            )

        for target in payload.get("anp_mandatory_targets", []):
            self.register_anp_target(
                distributor_id=target["distributor_id"],
                distributor_name=target["distributor_name"],
                cnpj=target["cnpj"],
                compliance_year=int(target["compliance_year"]),
                mandatory_target_cbios=int(target["mandatory_target_cbios"]),
            )

        for retirement in payload.get("retirement_instructions", []):
            self.retire_cbios(
                instruction_id=retirement["instruction_id"],
                receipt_id=retirement.get("receipt_id"),
                batch_id=retirement["batch_id"],
                holder_id=retirement["holder_id"],
                quantity=int(retirement["quantity"]),
                compliance_year=int(retirement["compliance_year"]),
                beneficiary_justification=retirement["beneficiary_justification"],
                retirement_timestamp=retirement["retirement_timestamp"],
            )

        return self.build_summary()

    def process_batch_file(self, file_path: Union[str, Path]) -> CbioPlatformSummary:
        """Carrega e processa um arquivo JSON de lote CBIO / RenovaBio."""
        path = Path(file_path)
        if not path.is_file():
            raise CbioValidationError(f"Arquivo de lote CBIO não encontrado: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CbioValidationError(f"JSON inválido no arquivo {path}: {exc}") from exc
        return self.process_batch_payload(payload)
