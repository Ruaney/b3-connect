"""Módulo de Escrituração, Endosso e Liquidação de Duplicatas Escriturais Eletrônicas (FL-051).

Implementa o modelo determinístico do Balcão NoMe B3 para registro escritural,
governança cambial, controle anti-fraude de unicidade de lastro de NF-e,
cadeia de endossos (Translativo vs Mandato) e liquidação financeira via SPB e Pix,
conforme a Lei nº 13.775/2018, Circular BCB nº 4.016/2020 e Lei nº 5.474/1968.

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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------

class EndorsementType(str, Enum):
    """Modalidades de endosso eletrônico em duplicatas escriturais."""
    TRANSLATIVO = "TRANSLATIVO"  # Transferência plena de titularidade/propriedade creditícia (ex.: FIDC)
    MANDATO = "MANDATO"          # Mandato para cobrança simples / procuração bancária sem cessão de titularidade


class TradeBillStatus(str, Enum):
    """Ciclo de vida e estados da duplicata escritural no NoMe Balcão B3."""
    REGISTRADA = "REGISTRADA"    # Escriturada e ativa no registro
    ENDOSSADA = "ENDOSSADA"      # Endossada via endosso translativo para novo credor
    EM_COBRANCA = "EM_COBRANCA"  # Sob mandato de cobrança bancária simples
    LIQUIDADA = "LIQUIDADA"      # Totalmente liquidada financeiramente e quitada
    BAIXADA = "BAIXADA"          # Baixa operacional por cancelamento ou desconstituição


class SettlementMethod(str, Enum):
    """Canais e mecanismos de liquidação financeira de duplicatas escriturais."""
    PIX = "PIX"                  # Arranjo Pix com identificador End-to-End (E2E ID)
    SPB = "SPB"                  # Sistema de Pagamentos Brasileiro (STR / mensagens LDL0001)
    BOLETO = "BOLETO"            # Compensação bancária e liquidação de boleto registrado


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class TradeBillError(Exception):
    """Exceção base para erros operacionais de duplicatas escriturais eletrônicas."""
    pass


class NfeValidationError(TradeBillError):
    """Erro de validação sintática ou de consistência da chave de acesso da NF-e."""
    pass


class DuplicateTradeBillError(TradeBillError):
    """Erro de violação da unicidade de lastro (tentativa de duplicidade de escrituração)."""
    pass


class InvalidEndorsementError(TradeBillError):
    """Erro de validação cambial ou quebra de legitimidade na cadeia de endossos."""
    pass


class TradeBillSettlementError(TradeBillError):
    """Erro de liquidação financeira ou quebra de conservação contábil."""
    pass


class TradeBillNotFoundError(TradeBillError):
    """Duplicata escritural não localizada no repositório de registros."""
    pass


class TradeBillInvalidStatusError(TradeBillError):
    """Ação requerida incompatível com o status operacional corrente da duplicata."""
    pass


# ---------------------------------------------------------------------------
# Funções Utilitárias e Validações
# ---------------------------------------------------------------------------

# Códigos oficiais de UF do IBGE aceitos no Brasil
VALID_IBGE_UF_CODES = {
    "11", "12", "13", "14", "15", "16", "17",  # Norte
    "21", "22", "23", "24", "25", "26", "27", "28", "29",  # Nordeste
    "31", "32", "33", "35",  # Sudeste (31=MG, 32=ES, 33=RJ, 35=SP)
    "41", "42", "43",  # Sul (41=PR, 42=SC, 43=RS)
    "50", "51", "52", "53",  # Centro-Oeste (50=MS, 51=MT, 52=GO, 53=DF)
}


def clean_document(doc: str) -> str:
    """Remove pontuações e caracteres não-numéricos de CNPJ ou CPF."""
    if not doc:
        return ""
    return re.sub(r"\D", "", str(doc).strip())


def validate_cnpj(cnpj: str) -> bool:
    """Valida formalmente um CNPJ com base no cálculo dos dois dígitos verificadores."""
    cleaned = clean_document(cnpj)
    if len(cleaned) != 14:
        return False
    # Rejeita sequências repetidas triviais
    if len(set(cleaned)) == 1:
        return False

    # Primeiro dígito verificador
    weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    sum1 = sum(int(digit) * weight for digit, weight in zip(cleaned[:12], weights1))
    remainder1 = sum1 % 11
    d1 = 0 if remainder1 < 2 else 11 - remainder1
    if int(cleaned[12]) != d1:
        return False

    # Segundo dígito verificador
    weights2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    sum2 = sum(int(digit) * weight for digit, weight in zip(cleaned[:13], weights2))
    remainder2 = sum2 % 11
    d2 = 0 if remainder2 < 2 else 11 - remainder2
    return int(cleaned[13]) == d2


def calculate_nfe_check_digit(key_43: str) -> int:
    """Calcula o Dígito Verificador (cDV) da Chave de NF-e via Módulo 11 (pesos 2 a 9)."""
    if len(key_43) != 43 or not key_43.isdigit():
        raise NfeValidationError(f"Base da chave de NF-e deve ter exatamente 43 dígitos numéricos: {key_43}")
    weights = [2, 3, 4, 5, 6, 7, 8, 9]
    total = sum(int(digit) * weights[i % len(weights)] for i, digit in enumerate(reversed(key_43)))
    remainder = total % 11
    return 0 if remainder in (0, 1) else 11 - remainder


def validate_nfe_access_key(key: str, expected_issuer_cnpj: Optional[str] = None) -> bool:
    """Valida a integridade estrutural, o cDV e a coerência do emitente da chave da NF-e.
    
    A Chave de Acesso possui 44 dígitos:
    - 01-02: cUF (Código IBGE)
    - 03-06: AAMM (Ano e Mês)
    - 07-20: CNPJ do Emitente (14 dígitos)
    - 21-22: mod (Modelo 55 para NF-e mercantil)
    - 23-25: serie (Série)
    - 26-34: nNF (Número da NF-e)
    - 35-35: tpEmis (Tipo de emissão)
    - 36-43: cNF (Código numérico)
    - 44-44: cDV (Dígito verificador)
    """
    clean_key = clean_document(key)
    if len(clean_key) != 44:
        raise NfeValidationError(f"Chave da NF-e deve conter exatamente 44 dígitos numéricos, recebido: {len(clean_key)}")

    # 1. Validação do cUF
    c_uf = clean_key[:2]
    if c_uf not in VALID_IBGE_UF_CODES:
        raise NfeValidationError(f"Código cUF IBGE inválido na chave NF-e: '{c_uf}'")

    # 2. Validação do AAMM
    mm = int(clean_key[4:6])
    if mm < 1 or mm > 12:
        raise NfeValidationError(f"Mês de emissão inválido na chave NF-e: {mm:02d}")

    # 3. Validação do CNPJ do emitente contido na chave
    key_cnpj = clean_key[6:20]
    if not validate_cnpj(key_cnpj):
        raise NfeValidationError(f"CNPJ do emitente contido na chave NF-e é inválido: {key_cnpj}")

    if expected_issuer_cnpj:
        clean_expected = clean_document(expected_issuer_cnpj)
        if clean_expected and key_cnpj != clean_expected:
            raise NfeValidationError(
                f"CNPJ do emitente declarado ({clean_expected}) diverge do CNPJ codificado na NF-e ({key_cnpj})"
            )

    # 4. Validação do modelo do documento fiscal (55 para NF-e mercantil)
    modelo = clean_key[20:22]
    if modelo not in ("55", "65"):
        raise NfeValidationError(f"Modelo de documento fiscal inválido na chave NF-e: {modelo} (esperado 55 ou 65)")

    # 5. Validação matemática do DV via Módulo 11
    expected_dv = calculate_nfe_check_digit(clean_key[:43])
    actual_dv = int(clean_key[43])
    if actual_dv != expected_dv:
        raise NfeValidationError(
            f"Dígito verificador da chave NF-e divergente: informado {actual_dv}, esperado {expected_dv}"
        )

    return True


def parse_date(value: Union[str, date, datetime]) -> date:
    """Converte strings ISO para date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value.strip())
    raise ValueError(f"Não foi possível converter {value} para date")


def parse_datetime(value: Union[str, datetime]) -> datetime:
    """Converte strings ISO para datetime UTC timezone-aware."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        val = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    raise ValueError(f"Não foi possível converter {value} para datetime")


def generate_signature_hash(content: str) -> str:
    """Gera hash SHA-256 para comprovação de integridade e assinatura digital."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Registros de Dados (Dataclasses)
# ---------------------------------------------------------------------------

@dataclass
class EndorsementRecord:
    """Registro de endosso eletrônico cambial de duplicata escritural."""
    endorsement_id: str
    endorsement_type: EndorsementType
    endorser_document: str
    endorser_name: str
    endorsee_document: str
    endorsee_name: str
    timestamp: datetime
    amount: Decimal
    digital_signature_hash: Optional[str] = None
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        self.endorser_document = clean_document(self.endorser_document)
        self.endorsee_document = clean_document(self.endorsee_document)
        if isinstance(self.endorsement_type, str) and not isinstance(self.endorsement_type, EndorsementType):
            self.endorsement_type = EndorsementType(self.endorsement_type)
        if not isinstance(self.amount, Decimal):
            self.amount = Decimal(str(self.amount))
        self.timestamp = parse_datetime(self.timestamp)
        if not self.digital_signature_hash:
            payload = f"{self.endorsement_id}|{self.endorsement_type.value}|{self.endorser_document}|{self.endorsee_document}|{self.amount}|{self.timestamp.isoformat()}"
            self.digital_signature_hash = generate_signature_hash(payload)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "endorsement_id": self.endorsement_id,
            "endorsement_type": self.endorsement_type.value,
            "endorser_document": self.endorser_document,
            "endorser_name": self.endorser_name,
            "endorsee_document": self.endorsee_document,
            "endorsee_name": self.endorsee_name,
            "timestamp": self.timestamp.isoformat(),
            "amount": str(self.amount),
            "digital_signature_hash": self.digital_signature_hash,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EndorsementRecord:
        return cls(
            endorsement_id=data["endorsement_id"],
            endorsement_type=EndorsementType(data["endorsement_type"]),
            endorser_document=data["endorser_document"],
            endorser_name=data["endorser_name"],
            endorsee_document=data["endorsee_document"],
            endorsee_name=data["endorsee_name"],
            timestamp=parse_datetime(data["timestamp"]),
            amount=Decimal(str(data["amount"])),
            digital_signature_hash=data.get("digital_signature_hash"),
            notes=data.get("notes"),
        )


@dataclass
class SettlementRecord:
    """Registro de liquidação financeira e quitação contábil da duplicata escritural."""
    settlement_id: str
    method: SettlementMethod
    payer_document: str
    receiver_document: str
    principal_amount: Decimal
    interest_amount: Decimal = Decimal("0.00")
    fine_amount: Decimal = Decimal("0.00")
    discount_amount: Decimal = Decimal("0.00")
    settled_amount: Decimal = Decimal("0.00")
    settlement_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    channel_transaction_id: str = ""
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        self.payer_document = clean_document(self.payer_document)
        self.receiver_document = clean_document(self.receiver_document)
        if isinstance(self.method, str) and not isinstance(self.method, SettlementMethod):
            self.method = SettlementMethod(self.method)
        if not isinstance(self.principal_amount, Decimal):
            self.principal_amount = Decimal(str(self.principal_amount))
        if not isinstance(self.interest_amount, Decimal):
            self.interest_amount = Decimal(str(self.interest_amount))
        if not isinstance(self.fine_amount, Decimal):
            self.fine_amount = Decimal(str(self.fine_amount))
        if not isinstance(self.discount_amount, Decimal):
            self.discount_amount = Decimal(str(self.discount_amount))
        if not isinstance(self.settled_amount, Decimal):
            self.settled_amount = Decimal(str(self.settled_amount))

        self.settlement_timestamp = parse_datetime(self.settlement_timestamp)

        # Regra de Conservação Contábil: Total Liquidado == Principal + Juros + Multa - Desconto
        calculated_total = (self.principal_amount + self.interest_amount + self.fine_amount - self.discount_amount).quantize(Decimal("0.01"))
        if self.settled_amount == Decimal("0.00"):
            self.settled_amount = calculated_total
        elif self.settled_amount != calculated_total:
            raise TradeBillSettlementError(
                f"Quebra de conservação financeira na liquidação: informado {self.settled_amount}, esperado {calculated_total}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "settlement_id": self.settlement_id,
            "method": self.method.value,
            "payer_document": self.payer_document,
            "receiver_document": self.receiver_document,
            "principal_amount": str(self.principal_amount),
            "interest_amount": str(self.interest_amount),
            "fine_amount": str(self.fine_amount),
            "discount_amount": str(self.discount_amount),
            "settled_amount": str(self.settled_amount),
            "settlement_timestamp": self.settlement_timestamp.isoformat(),
            "channel_transaction_id": self.channel_transaction_id,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SettlementRecord:
        return cls(
            settlement_id=data["settlement_id"],
            method=SettlementMethod(data["method"]),
            payer_document=data["payer_document"],
            receiver_document=data["receiver_document"],
            principal_amount=Decimal(str(data["principal_amount"])),
            interest_amount=Decimal(str(data.get("interest_amount", "0.00"))),
            fine_amount=Decimal(str(data.get("fine_amount", "0.00"))),
            discount_amount=Decimal(str(data.get("discount_amount", "0.00"))),
            settled_amount=Decimal(str(data.get("settled_amount", "0.00"))),
            settlement_timestamp=parse_datetime(data["settlement_timestamp"]),
            channel_transaction_id=data.get("channel_transaction_id", ""),
            notes=data.get("notes"),
        )


@dataclass
class ElectronicTradeBill:
    """Entidade central de Duplicata Escritural Eletrônica no NoMe Balcão B3."""
    trade_bill_id: str
    nfe_access_key: str
    installment_number: int
    total_installments: int
    issuer_document: str
    issuer_name: str
    drawee_document: str
    drawee_name: str
    face_value: Decimal
    issue_date: date
    due_date: date
    registration_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: TradeBillStatus = TradeBillStatus.REGISTRADA
    current_owner_document: str = ""
    current_owner_name: str = ""
    mandatary_document: Optional[str] = None
    mandatary_name: Optional[str] = None
    balance_due: Decimal = field(default=Decimal("0.00"))
    endorsements: List[EndorsementRecord] = field(default_factory=list)
    settlement: Optional[SettlementRecord] = None
    registrar_code: str = "B3_NOME"
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        self.nfe_access_key = clean_document(self.nfe_access_key)
        self.issuer_document = clean_document(self.issuer_document)
        self.drawee_document = clean_document(self.drawee_document)
        if self.mandatary_document:
            self.mandatary_document = clean_document(self.mandatary_document)

        if not isinstance(self.face_value, Decimal):
            self.face_value = Decimal(str(self.face_value)).quantize(Decimal("0.01"))

        if self.status == TradeBillStatus.LIQUIDADA or self.settlement is not None:
            if not isinstance(self.balance_due, Decimal):
                self.balance_due = Decimal(str(self.balance_due)).quantize(Decimal("0.01"))
            # Se liquidada integralmente, o saldo devedor é zero
            if self.settlement and self.settlement.principal_amount == self.face_value:
                self.balance_due = Decimal("0.00")
        else:
            if not isinstance(self.balance_due, Decimal):
                self.balance_due = Decimal(str(self.balance_due)).quantize(Decimal("0.01"))
            elif self.balance_due == Decimal("0.00"):
                self.balance_due = self.face_value
            else:
                self.balance_due = self.balance_due.quantize(Decimal("0.01"))


        self.issue_date = parse_date(self.issue_date)
        self.due_date = parse_date(self.due_date)
        self.registration_timestamp = parse_datetime(self.registration_timestamp)

        if isinstance(self.status, str) and not isinstance(self.status, TradeBillStatus):
            self.status = TradeBillStatus(self.status)

        # Se titular atual não estiver definido, o titular inicial é o emitente/sacador
        if not self.current_owner_document:
            self.current_owner_document = self.issuer_document
        else:
            self.current_owner_document = clean_document(self.current_owner_document)
        if not self.current_owner_name:
            self.current_owner_name = self.issuer_name

    @property
    def ballast_key(self) -> Tuple[str, int]:
        """Chave composta única de lastro mercantil (Chave NF-e + Número da Parcela)."""
        return (self.nfe_access_key, self.installment_number)

    @property
    def is_settled(self) -> bool:
        """Indica se a duplicata já foi formalmente quitada e liquidada."""
        return self.status == TradeBillStatus.LIQUIDADA

    @property
    def is_active(self) -> bool:
        """Indica se a duplicata está apta para negociação, endosso ou cobrança."""
        return self.status not in (TradeBillStatus.LIQUIDADA, TradeBillStatus.BAIXADA)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_bill_id": self.trade_bill_id,
            "nfe_access_key": self.nfe_access_key,
            "installment_number": self.installment_number,
            "total_installments": self.total_installments,
            "issuer_document": self.issuer_document,
            "issuer_name": self.issuer_name,
            "drawee_document": self.drawee_document,
            "drawee_name": self.drawee_name,
            "face_value": str(self.face_value),
            "balance_due": str(self.balance_due),
            "issue_date": self.issue_date.isoformat(),
            "due_date": self.due_date.isoformat(),
            "registration_timestamp": self.registration_timestamp.isoformat(),
            "status": self.status.value,
            "current_owner_document": self.current_owner_document,
            "current_owner_name": self.current_owner_name,
            "mandatary_document": self.mandatary_document,
            "mandatary_name": self.mandatary_name,
            "registrar_code": self.registrar_code,
            "notes": self.notes,
            "endorsements": [end.to_dict() for end in self.endorsements],
            "settlement": self.settlement.to_dict() if self.settlement else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ElectronicTradeBill:
        endorsements = [EndorsementRecord.from_dict(e) for e in data.get("endorsements", [])]
        settlement = SettlementRecord.from_dict(data["settlement"]) if data.get("settlement") else None
        
        status_val = data.get("status", TradeBillStatus.REGISTRADA.value)
        status = TradeBillStatus(status_val) if isinstance(status_val, str) else status_val

        face_val = Decimal(str(data["face_value"]))
        bal_due = Decimal(str(data["balance_due"])) if "balance_due" in data else face_val

        # Se houver liquidação e não foi fornecido balance_due, deduz
        if settlement and "balance_due" not in data:
            bal_due = (face_val - settlement.principal_amount).quantize(Decimal("0.01"))

        # Determina titular atual com base no histórico de endossos translativos
        current_owner_doc = data.get("current_owner_document") or data["issuer_document"]
        current_owner_name = data.get("current_owner_name") or data["issuer_name"]
        mandatary_doc = data.get("mandatary_document")
        mandatary_name = data.get("mandatary_name")

        # Se não foram fornecidos explicitamente no topo, reconstrói da cadeia
        if not data.get("current_owner_document") or not data.get("mandatary_document"):
            for e in endorsements:
                if e.endorsement_type == EndorsementType.TRANSLATIVO:
                    current_owner_doc = e.endorsee_document
                    current_owner_name = e.endorsee_name
                elif e.endorsement_type == EndorsementType.MANDATO:
                    mandatary_doc = e.endorsee_document
                    mandatary_name = e.endorsee_name

        return cls(
            trade_bill_id=data["trade_bill_id"],
            nfe_access_key=data["nfe_access_key"],
            installment_number=int(data["installment_number"]),
            total_installments=int(data["total_installments"]),
            issuer_document=data["issuer_document"],
            issuer_name=data["issuer_name"],
            drawee_document=data["drawee_document"],
            drawee_name=data["drawee_name"],
            face_value=face_val,
            balance_due=bal_due,
            issue_date=parse_date(data["issue_date"]),
            due_date=parse_date(data["due_date"]),
            registration_timestamp=parse_datetime(data.get("registration_timestamp", datetime.now(timezone.utc))),
            status=status,
            current_owner_document=current_owner_doc,
            current_owner_name=current_owner_name,
            mandatary_document=mandatary_doc,
            mandatary_name=mandatary_name,
            endorsements=endorsements,
            settlement=settlement,
            registrar_code=data.get("registrar_code", "B3_NOME"),
            notes=data.get("notes"),
        )


# ---------------------------------------------------------------------------
# Motor de Escrituração, Endosso e Liquidação (ElectronicTradeBillEngine)
# ---------------------------------------------------------------------------

class ElectronicTradeBillEngine:
    """Motor central de registro escritural e liquidação de duplicatas NoMe B3."""

    def __init__(self, registrar_code: str = "B3_NOME") -> None:
        self.registrar_code = registrar_code
        self.bills: Dict[str, ElectronicTradeBill] = {}
        # Índice de unicidade de lastro: (nfe_access_key, installment_number) -> trade_bill_id
        self.ballast_index: Dict[Tuple[str, int], str] = {}
        # Registros externos simulados para teste de interoperabilidade inter-registradoras
        self.external_ballast_registry: Dict[Tuple[str, int], str] = {}

    def register_trade_bill(
        self,
        trade_bill_id: str,
        nfe_access_key: str,
        installment_number: int,
        total_installments: int,
        issuer_document: str,
        issuer_name: str,
        drawee_document: str,
        drawee_name: str,
        face_value: Union[Decimal, str, float],
        issue_date: Union[date, str],
        due_date: Union[date, str],
        registration_timestamp: Optional[Union[datetime, str]] = None,
        notes: Optional[str] = None,
    ) -> ElectronicTradeBill:
        """Registra e escritura uma nova duplicata mercantil eletrônica com checagem anti-fraude."""
        # 1. Validação do ID
        if not trade_bill_id or not trade_bill_id.strip():
            raise TradeBillError("Identificador da duplicata (trade_bill_id) não pode ser vazio.")
        if trade_bill_id in self.bills:
            raise TradeBillError(f"Duplicata escritural com ID '{trade_bill_id}' já cadastrada no sistema.")

        # 2. Normalização e Validação de CNPJs
        clean_issuer = clean_document(issuer_document)
        clean_drawee = clean_document(drawee_document)
        if not validate_cnpj(clean_issuer):
            raise NfeValidationError(f"CNPJ do emitente/sacador é inválido: {issuer_document}")
        if not validate_cnpj(clean_drawee):
            raise NfeValidationError(f"CNPJ do sacado/devedor é inválido: {drawee_document}")

        # 3. Validação Estrita da Chave de Acesso da NF-e (44 dígitos, cUF, cDV e CNPJ Emitente)
        clean_nfe = clean_document(nfe_access_key)
        validate_nfe_access_key(clean_nfe, expected_issuer_cnpj=clean_issuer)

        # 4. Validação de Parcelas
        if installment_number < 1 or total_installments < 1 or installment_number > total_installments:
            raise TradeBillError(
                f"Número de parcela inválido: {installment_number} de {total_installments}."
            )

        # 5. Validação do Valor de Face
        try:
            val_face = Decimal(str(face_value)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            raise TradeBillError(f"Valor de face inválido: {face_value}")
        if val_face <= Decimal("0.00"):
            raise TradeBillError(f"Valor de face da duplicata deve ser estritamente positivo: {val_face}")

        # 6. Validação de Datas
        p_issue_date = parse_date(issue_date)
        p_due_date = parse_date(due_date)
        if p_due_date < p_issue_date:
            raise TradeBillError(
                f"Data de vencimento ({p_due_date}) não pode ser anterior à data de emissão ({p_issue_date})."
            )

        # 7. MECANISMO ANTI-FRAUDE DE UNICIDADE DE LASTRO
        ballast_key = (clean_nfe, installment_number)
        if ballast_key in self.ballast_index:
            existing_id = self.ballast_index[ballast_key]
            raise DuplicateTradeBillError(
                f"Bloqueio anti-fraude B3: Lastro já escriturado no sistema NoMe para a "
                f"NF-e {clean_nfe}, parcela {installment_number} sob o título '{existing_id}'."
            )
        if ballast_key in self.external_ballast_registry:
            ext_reg = self.external_ballast_registry[ballast_key]
            raise DuplicateTradeBillError(
                f"Bloqueio anti-fraude de interoperabilidade: Lastro já escriturado em registradora concorrente "
                f"({ext_reg}) para a NF-e {clean_nfe}, parcela {installment_number}."
            )

        reg_ts = parse_datetime(registration_timestamp) if registration_timestamp else datetime.now(timezone.utc)

        # 8. Criação da Duplicata
        bill = ElectronicTradeBill(
            trade_bill_id=trade_bill_id.strip(),
            nfe_access_key=clean_nfe,
            installment_number=installment_number,
            total_installments=total_installments,
            issuer_document=clean_issuer,
            issuer_name=issuer_name.strip(),
            drawee_document=clean_drawee,
            drawee_name=drawee_name.strip(),
            face_value=val_face,
            balance_due=val_face,
            issue_date=p_issue_date,
            due_date=p_due_date,
            registration_timestamp=reg_ts,
            status=TradeBillStatus.REGISTRADA,
            current_owner_document=clean_issuer,
            current_owner_name=issuer_name.strip(),
            registrar_code=self.registrar_code,
            notes=notes,
        )

        self.bills[bill.trade_bill_id] = bill
        self.ballast_index[ballast_key] = bill.trade_bill_id
        return bill

    def register_external_ballast(
        self,
        nfe_access_key: str,
        installment_number: int,
        registrar_name: str = "REGISTRADORA_EXTERNA",
    ) -> None:
        """Registra lastro já escriturado em outra infraestrutura (interoperabilidade)."""
        clean_nfe = clean_document(nfe_access_key)
        self.external_ballast_registry[(clean_nfe, installment_number)] = registrar_name

    def endorse_trade_bill(
        self,
        trade_bill_id: str,
        endorsement_type: Union[EndorsementType, str],
        endorser_document: str,
        endorsee_document: str,
        endorsee_name: str,
        endorser_name: Optional[str] = None,
        amount: Optional[Union[Decimal, str, float]] = None,
        timestamp: Optional[Union[datetime, str]] = None,
        notes: Optional[str] = None,
    ) -> EndorsementRecord:
        """Executa o endosso eletrônico cambial (Translativo ou Mandato) na duplicata."""
        bill = self.get_bill(trade_bill_id)

        # 1. Verificação de Elegibilidade de Status
        if not bill.is_active:
            raise TradeBillInvalidStatusError(
                f"Duplicata '{trade_bill_id}' possui status '{bill.status.value}' e não pode receber endosso."
            )

        # 2. Modalidade de Endosso
        if isinstance(endorsement_type, str):
            try:
                e_type = EndorsementType(endorsement_type)
            except ValueError:
                raise InvalidEndorsementError(f"Tipo de endosso desconhecido: '{endorsement_type}'")
        else:
            e_type = endorsement_type

        # 3. Validação de Partes
        clean_endorser = clean_document(endorser_document)
        clean_endorsee = clean_document(endorsee_document)
        if not validate_cnpj(clean_endorser):
            raise InvalidEndorsementError(f"CNPJ do endossante é inválido: {endorser_document}")
        if not validate_cnpj(clean_endorsee):
            raise InvalidEndorsementError(f"CNPJ do endossatário é inválido: {endorsee_document}")

        # 4. Validação da Legitimidade do Endossante (Cadeia de Titularidade)
        if clean_endorser != bill.current_owner_document:
            raise InvalidEndorsementError(
                f"Quebra de legitimidade cambial: O endossante ({clean_endorser}) não é o titular "
                f"legítimo atual da duplicata ({bill.current_owner_document} - {bill.current_owner_name})."
            )

        # 5. Valor do Endosso
        if amount is not None:
            try:
                end_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                raise InvalidEndorsementError(f"Valor do endosso inválido: {amount}")
            if end_amount != bill.balance_due:
                raise InvalidEndorsementError(
                    f"Valor do endosso ({end_amount}) deve corresponder ao saldo residual ({bill.balance_due})."
                )
        else:
            end_amount = bill.balance_due

        # 6. Carimbo de Tempo e Monotonicidade
        end_ts = parse_datetime(timestamp) if timestamp else datetime.now(timezone.utc)
        if end_ts < bill.registration_timestamp:
            raise InvalidEndorsementError(
                f"Timestamp do endosso ({end_ts}) não pode preceder a escrituração ({bill.registration_timestamp})."
            )
        if bill.endorsements and end_ts < bill.endorsements[-1].timestamp:
            raise InvalidEndorsementError(
                f"Timestamp do endosso ({end_ts}) retroativo em relação ao endosso anterior ({bill.endorsements[-1].timestamp})."
            )

        endorsement_index = len(bill.endorsements) + 1
        endorsement_id = f"END-{bill.trade_bill_id}-{endorsement_index:02d}"

        # 7. Criação do Registro de Endosso
        record = EndorsementRecord(
            endorsement_id=endorsement_id,
            endorsement_type=e_type,
            endorser_document=clean_endorser,
            endorser_name=endorser_name or bill.current_owner_name,
            endorsee_document=clean_endorsee,
            endorsee_name=endorsee_name.strip(),
            timestamp=end_ts,
            amount=end_amount,
            notes=notes,
        )

        # 8. Aplicação dos Efeitos Jurídico-Contábeis no Título
        if e_type == EndorsementType.TRANSLATIVO:
            # Transferência plena da propriedade do crédito para o novo titular (ex: FIDC)
            bill.current_owner_document = clean_endorsee
            bill.current_owner_name = endorsee_name.strip()
            # Se havia mandatário prévio, o novo titular pode revogar ou manter; padrão NoMe é limpar
            bill.mandatary_document = None
            bill.mandatary_name = None
            bill.status = TradeBillStatus.ENDOSSADA
        elif e_type == EndorsementType.MANDATO:
            # Titularidade econômica inalterada; constitui-se mandatário de cobrança (Banco)
            bill.mandatary_document = clean_endorsee
            bill.mandatary_name = endorsee_name.strip()
            bill.status = TradeBillStatus.EM_COBRANCA

        bill.endorsements.append(record)
        return record

    def settle_trade_bill(
        self,
        trade_bill_id: str,
        method: Union[SettlementMethod, str],
        payer_document: str,
        receiver_document: str,
        principal_amount: Union[Decimal, str, float],
        interest_amount: Union[Decimal, str, float] = Decimal("0.00"),
        fine_amount: Union[Decimal, str, float] = Decimal("0.00"),
        discount_amount: Union[Decimal, str, float] = Decimal("0.00"),
        settled_amount: Optional[Union[Decimal, str, float]] = None,
        settlement_timestamp: Optional[Union[datetime, str]] = None,
        channel_transaction_id: str = "",
        notes: Optional[str] = None,
    ) -> SettlementRecord:
        """Executa a liquidação financeira com conservação contábil e baixa de escrituração."""
        bill = self.get_bill(trade_bill_id)

        # 1. Verificação de Estado
        if bill.is_settled:
            raise TradeBillSettlementError(
                f"Tentativa de dupla liquidação: Duplicata '{trade_bill_id}' já se encontra quitada e liquidada."
            )
        if bill.status == TradeBillStatus.BAIXADA:
            raise TradeBillInvalidStatusError(
                f"Duplicata '{trade_bill_id}' está baixada por cancelamento e não admite liquidação."
            )

        # 2. Validação dos Participantes
        clean_payer = clean_document(payer_document)
        clean_receiver = clean_document(receiver_document)

        if clean_payer != bill.drawee_document:
            raise TradeBillSettlementError(
                f"Pagador da liquidação ({clean_payer}) diverge do sacado da duplicata ({bill.drawee_document} - {bill.drawee_name})."
            )

        # O recebedor deve ser o legítimo proprietário atual ou o mandatário registrado
        valid_receivers = {bill.current_owner_document}
        if bill.mandatary_document:
            valid_receivers.add(bill.mandatary_document)

        if clean_receiver not in valid_receivers:
            raise TradeBillSettlementError(
                f"Recebedor da liquidação ({clean_receiver}) não é o titular ({bill.current_owner_document}) "
                f"nem o mandatário autorizado ({bill.mandatary_document}) da duplicata."
            )

        # 3. Conversão e Validação Contábil dos Valores
        try:
            p_principal = Decimal(str(principal_amount)).quantize(Decimal("0.01"))
            p_interest = Decimal(str(interest_amount)).quantize(Decimal("0.01"))
            p_fine = Decimal(str(fine_amount)).quantize(Decimal("0.01"))
            p_discount = Decimal(str(discount_amount)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as ex:
            raise TradeBillSettlementError(f"Valores numéricos de liquidação inválidos: {ex}")

        if p_principal <= Decimal("0.00"):
            raise TradeBillSettlementError(f"Principal a liquidar deve ser estritamente positivo: {p_principal}")
        if p_principal > bill.balance_due:
            raise TradeBillSettlementError(
                f"Principal a liquidar ({p_principal}) excede o saldo devedor do título ({bill.balance_due})."
            )

        expected_settled = (p_principal + p_interest + p_fine - p_discount).quantize(Decimal("0.01"))
        if settled_amount is not None:
            try:
                p_settled = Decimal(str(settled_amount)).quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                raise TradeBillSettlementError(f"Valor liquidado inválido: {settled_amount}")
            if p_settled != expected_settled:
                raise TradeBillSettlementError(
                    f"Quebra de conservação contábil: informado {p_settled}, calculado {expected_settled} "
                    f"({p_principal} + {p_interest} + {p_fine} - {p_discount})."
                )
        else:
            p_settled = expected_settled

        # 4. Validação de Método e Identificador de Canal
        if isinstance(method, str):
            try:
                s_method = SettlementMethod(method)
            except ValueError:
                raise TradeBillSettlementError(f"Método de liquidação desconhecido: '{method}'")
        else:
            s_method = method

        settle_ts = parse_datetime(settlement_timestamp) if settlement_timestamp else datetime.now(timezone.utc)
        settlement_id = f"SETTL-{bill.trade_bill_id}"

        record = SettlementRecord(
            settlement_id=settlement_id,
            method=s_method,
            payer_document=clean_payer,
            receiver_document=clean_receiver,
            principal_amount=p_principal,
            interest_amount=p_interest,
            fine_amount=p_fine,
            discount_amount=p_discount,
            settled_amount=p_settled,
            settlement_timestamp=settle_ts,
            channel_transaction_id=channel_transaction_id,
            notes=notes,
        )

        # 5. Efeito Contábil e Baixa de Escrituração
        bill.balance_due = (bill.balance_due - p_principal).quantize(Decimal("0.01"))
        if bill.balance_due == Decimal("0.00"):
            bill.status = TradeBillStatus.LIQUIDADA
        bill.settlement = record

        return record

    def deregister_trade_bill(self, trade_bill_id: str, reason: str = "CANCELAMENTO_OPERACIONAL") -> ElectronicTradeBill:
        """Executa a baixa operacional de uma duplicata não liquidada."""
        bill = self.get_bill(trade_bill_id)
        if bill.is_settled:
            raise TradeBillInvalidStatusError(
                f"Duplicata '{trade_bill_id}' já está liquidada financeiramente e não admite baixa manual."
            )
        bill.status = TradeBillStatus.BAIXADA
        bill.notes = f"{bill.notes or ''} [BAIXADA: {reason}]".strip()
        return bill

    def get_bill(self, trade_bill_id: str) -> ElectronicTradeBill:
        """Obtém uma duplicata pelo seu ID único."""
        if trade_bill_id not in self.bills:
            raise TradeBillNotFoundError(f"Duplicata escritural '{trade_bill_id}' não localizada no NoMe Balcão.")
        return self.bills[trade_bill_id]

    def get_by_ballast(self, nfe_access_key: str, installment_number: int) -> Optional[ElectronicTradeBill]:
        """Obtém uma duplicata a partir da chave unívoca de lastro (NF-e + Parcela)."""
        clean_nfe = clean_document(nfe_access_key)
        ballast_key = (clean_nfe, installment_number)
        bill_id = self.ballast_index.get(ballast_key)
        if bill_id:
            return self.bills.get(bill_id)
        return None

    def verify_ballast_uniqueness(self) -> bool:
        """Verifica a unicidade global de lastro em todo o repositório de títulos."""
        seen: Set[Tuple[str, int]] = set()
        for bill in self.bills.values():
            key = bill.ballast_key
            if key in seen:
                return False
            seen.add(key)
        return True

    def verify_endorsement_chain(self, trade_bill_id: str) -> bool:
        """Valida a integridade formal, causal e temporal da cadeia de endossos."""
        bill = self.get_bill(trade_bill_id)
        expected_owner = bill.issuer_document
        last_timestamp = bill.registration_timestamp

        for endorsement in bill.endorsements:
            # O endossante deve ser o titular legítimo naquele ponto da cadeia
            if endorsement.endorser_document != expected_owner:
                return False
            # Monotonicidade temporal
            if endorsement.timestamp < last_timestamp:
                return False
            last_timestamp = endorsement.timestamp

            # Se for translativo, atualiza o titular esperado
            if endorsement.endorsement_type == EndorsementType.TRANSLATIVO:
                expected_owner = endorsement.endorsee_document

        return expected_owner == bill.current_owner_document

    def verify_accounting_conservation(self) -> Dict[str, Any]:
        """Verifica a conservação contábil do portfólio e das liquidações."""
        total_face = sum(b.face_value for b in self.bills.values())
        total_balance_due = sum(b.balance_due for b in self.bills.values())
        total_principal_settled = sum(
            b.settlement.principal_amount for b in self.bills.values() if b.settlement is not None
        )

        identity_holds = (total_face == (total_balance_due + total_principal_settled))

        settlements_valid = True
        for b in self.bills.values():
            if b.settlement:
                s = b.settlement
                expected = (s.principal_amount + s.interest_amount + s.fine_amount - s.discount_amount).quantize(Decimal("0.01"))
                if s.settled_amount != expected:
                    settlements_valid = False
                    break

        return {
            "total_bills": len(self.bills),
            "total_face_value": str(total_face),
            "total_balance_due": str(total_balance_due),
            "total_principal_settled": str(total_principal_settled),
            "identity_conserved": identity_holds,
            "settlements_arithmetic_valid": settlements_valid,
            "is_conserved": identity_holds and settlements_valid,
        }

    def load_from_fixture(self, fixture_source: Union[Dict[str, Any], str, Path]) -> Dict[str, Any]:
        """Carrega e valida o lote de duplicatas da fixture JSON."""
        if isinstance(fixture_source, (str, Path)):
            with open(fixture_source, "r", encoding="utf-8") as f:
                data = json.load(f)
        elif isinstance(fixture_source, dict):
            data = fixture_source
        else:
            raise ValueError(f"Formato de fixture não suportado: {type(fixture_source)}")

        loaded_bills: List[ElectronicTradeBill] = []
        for bill_raw in data.get("trade_bills", []):
            bill = ElectronicTradeBill.from_dict(bill_raw)
            ballast_key = bill.ballast_key
            if ballast_key in self.ballast_index:
                raise DuplicateTradeBillError(
                    f"Duplicidade na carga de fixture para NF-e {ballast_key[0]}, parcela {ballast_key[1]}."
                )
            self.bills[bill.trade_bill_id] = bill
            self.ballast_index[ballast_key] = bill.trade_bill_id
            loaded_bills.append(bill)

        # Registra tentativas externas bloqueadas da fixture
        blocked_attempts = data.get("blocked_duplicate_attempts", [])
        for attempt in blocked_attempts:
            ext_nfe = attempt.get("nfe_access_key")
            ext_inst = attempt.get("installment_number")
            ext_reg = attempt.get("source_registradora", "REGISTRADORA_EXTERNA")
            if ext_nfe and ext_inst:
                self.register_external_ballast(ext_nfe, int(ext_inst), ext_reg)

        return {
            "loaded_count": len(loaded_bills),
            "bills": [b.trade_bill_id for b in loaded_bills],
            "blocked_attempts_count": len(blocked_attempts),
        }

    def export_state(self) -> Dict[str, Any]:
        """Exporta todo o estado atual do motor em dicionário compatível com JSON."""
        return {
            "registrar_code": self.registrar_code,
            "total_bills": len(self.bills),
            "trade_bills": [bill.to_dict() for bill in self.bills.values()],
            "audit": self.verify_accounting_conservation(),
        }
