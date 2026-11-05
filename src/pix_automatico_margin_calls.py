"""
Módulo de Gestão e Liquidação em Tempo Real de Chamadas de Margem via Pix Automático (FL-090).
=============================================================================================
Implementação do protocolo de liquidação instantânea em D0 de chamadas de margem
intradiárias do motor de risco CORE da Câmara B3 utilizando a infraestrutura do Pix
Automático e do Sistema de Pagamentos Instantâneos (SPI / DICT) do Banco Central do Brasil.

Em estrita conformidade regulatória com:
- Resolução BCB nº 1/2020 (Regulamento do Pix e do SPI, pacs.008 e pacs.002);
- Resolução BCB nº 341/2023 (Regulamento do Pix Automático e Jornada de Consentimento);
- Manual de Padrões de Iniciação do Pix e Catálogo de Mensagens ISO 20022 do Bacen;
- Regulamento das Câmaras de Compensação, Liquidação e Gerenciamento de Risco da B3.

Invariantes Formais Provadas:
1. Conservação Financeira Estrita no SPI (Soma-Zero):
   Total_Debitado_Pagadores == Total_Creditado_B3 (centavo a centavo).
   Sum(Saldo_Final_PI) == Sum(Saldo_Inicial_PI).
2. Unicidade e Conformidade de End-to-End ID:
   Cada instrução SPI possui um EndToEndId único de 32 posições alfanuméricas
   iniciando com 'E', ISPB de 8 dígitos, timestamp UTC de 12 dígitos e 11 caracteres únicos.
3. Irrevogabilidade e Não-Repúdio da Liquidação no SPI:
   Toda transferência liquidada no SPI com pacs.002 ACSC torna-se irrevogável,
   não podendo ser cancelada unilateralmente pelo pagador nem sofrer estorno sem MED.
4. Consistência de Capacidade de Risco Pré-Trade e Colaterais B3:
   A capacidade de risco pré-trade desbloqueada ou margem coberta não excede
   o montante financeiro efetivamente liquidado com sucesso no SPI.
5. Conformidade Estrita do Mandato de Pix Automático:
   Nenhuma transação liquida sem mandato ACTIVE, dentro da janela de vigência e
   respeitando os limites por chamada (max_limit_per_call) e mensal (total_monthly_limit).
6. Cumprimento do SLA de Liquidação Intradiária:
   Tempo de trânsito e resposta no SPI inferior a 10 segundos (< 10.000 ms).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union
import uuid
import xml.etree.ElementTree as ET

# Constantes de Domínio BACEN / SPI / B3
ISPB_B3_DEFAULT = "00997185"
ISPB_BACEN_DEFAULT = "00038166"
TAX_ID_B3_DEFAULT = "00997185000150"
B3_CLEARING_ACCOUNT_DEFAULT = "PI-B3-CLEARING-MARGIN"
B3_NAME_DEFAULT = "B3 S.A. - Brasil, Bolsa, Balcão"

XML_NS_PACS008 = "urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"
XML_NS_PACS002 = "urn:iso:std:iso:20022:tech:xsd:pacs.002.001.10"
XML_NS_BACEN_AUTO = "https://www.bcb.gov.br/pi/pacs.008/pix-automatico/1.0"

_ISPB_REGEX = re.compile(r"^\d{8}$")
_E2E_REGEX = re.compile(r"^E\d{8}\d{12}[a-zA-Z0-9]{11}$")
_EMAIL_REGEX = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")
_PHONE_REGEX = re.compile(r"^\+[1-9]\d{1,14}$")
_UUID_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)

TWO_PLACES = Decimal("0.01")


def _quantize_cents(val: Union[Decimal, int, float, str]) -> Decimal:
    """Arredonda com precisão estrita em 2 casas decimais (centavos BRL)."""
    if isinstance(val, (int, float, str)):
        val = Decimal(str(val))
    return val.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Importação ou definição de Invariantes Formais
# ---------------------------------------------------------------------------
try:
    from src.invariants import InvariantViolationError, check  # type: ignore
except ImportError:
    try:
        from invariants import InvariantViolationError, check  # type: ignore
    except ImportError:
        class InvariantViolationError(AssertionError):  # type: ignore
            """Exceção levantada quando uma regra formal de conservação é violada."""
            def __init__(self, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
                super().__init__(message)
                self.message = message
                self.law_code = law_code
                self.details = details or {}

        def check(condition: bool, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:  # type: ignore
            if not condition:
                raise InvariantViolationError(message, law_code=law_code, details=details)


# ---------------------------------------------------------------------------
# Exceções de Domínio do Pix Automático e Chamadas de Margem
# ---------------------------------------------------------------------------

class PixAutomaticoMarginError(Exception):
    """Exceção base para operações de Pix Automático e liquidação de margem intradiária."""
    pass


class PixAutomaticoValidationError(PixAutomaticoMarginError):
    """Lançada quando parâmetros cadastrais ou mensagens violam regras sintáticas."""
    pass


class InvalidTaxIdError(PixAutomaticoValidationError):
    """Lançada quando CPF ou CNPJ é inválido conforme cálculo dos dígitos verificadores."""
    pass


class InvalidIspbError(PixAutomaticoValidationError):
    """Lançada quando o código ISPB não cumpre os 8 dígitos numéricos regulamentares."""
    pass


class InvalidEndToEndIdError(PixAutomaticoValidationError):
    """Lançada quando o EndToEndId não segue o padrão estrito de 32 caracteres do BACEN."""
    pass


class InvalidPixKeyError(PixAutomaticoValidationError):
    """Lançada quando uma chave Pix informada é sintaticamente inválida ou inexistente."""
    pass


class MandateError(PixAutomaticoMarginError):
    """Exceção base para problemas com mandatos de autorização de Pix Automático."""
    pass


class MandateNotFoundError(MandateError):
    """Lançada quando não se localiza mandato para o titular pagador."""
    pass


class MandateInactiveError(MandateError):
    """Lançada quando o mandato não se encontra em estado ACTIVE ou está vencido."""
    pass


class MandateLimitExceededError(MandateError):
    """Lançada quando a chamada de margem excede o teto por transação ou limite mensal."""
    pass


class InsufficientFundsSpiError(PixAutomaticoMarginError):
    """Lançada quando a Conta PI do participante não possui saldo/limite para liquidação."""
    pass


class SpiTimeoutError(PixAutomaticoMarginError):
    """Lançada quando a liquidação no SPI excede o SLA de 10 segundos estabelecido pela B3."""
    pass


class MarginCallDefaultError(PixAutomaticoMarginError):
    """Lançada quando uma chamada de margem falha e aciona o status de default compulsório."""
    pass


class PixAutomaticoInvariantViolationError(InvariantViolationError, PixAutomaticoMarginError):
    """Lançada quando há quebra comprovada de lei invariante de liquidação ou conservação."""
    pass


# ---------------------------------------------------------------------------
# Enumerações de Negócio
# ---------------------------------------------------------------------------

class MandateStatus(str, Enum):
    """Status regulamentar do mandato de consentimento do Pix Automático (Res. BCB 341/2023)."""
    PENDING = "PENDING"       # Aguardando confirmação no canal de consentimento
    ACTIVE = "ACTIVE"         # Ativo, vigente e apto a autorizar débitos automáticos
    SUSPENDED = "SUSPENDED"   # Temporariamente suspenso pelo titular ou participante
    REVOKED = "REVOKED"       # Revogado em definitivo pelo titular pagador
    EXPIRED = "EXPIRED"       # Prazo de validade expirado


class RecurrencePurpose(str, Enum):
    """Finalidade regulamentar do débito recorrente na Câmara B3."""
    INTRADAY_MARGIN_CALL = "INTRADAY_MARGIN_CALL"   # Chamada de margem intradiária urgente
    ORDINARY_MARGIN_CALL = "ORDINARY_MARGIN_CALL"   # Chamada de margem ordinária de final de dia
    EXTRAORDINARY_MARGIN = "EXTRAORDINARY_MARGIN"   # Adicional extraordinário de estresse
    SETTLEMENT_OBLIGATION = "SETTLEMENT_OBLIGATION" # Liquidação multilateral de ativos


class AuthorizationChannel(str, Enum):
    """Canal seguro pelo qual o investidor concedeu consentimento prévio."""
    INTERNET_BANKING = "INTERNET_BANKING"
    MOBILE_APP = "MOBILE_APP"
    OPEN_FINANCE = "OPEN_FINANCE"
    BRANCH_TERMINAL = "BRANCH_TERMINAL"


class MarginCallReason(str, Enum):
    """Motivo determinante do disparo da chamada de margem pelo motor CORE B3."""
    INTRADAY_CORE_DEFICIT = "INTRADAY_CORE_DEFICIT"               # Déficit apurado em reavaliação intradiária
    STRESS_SCENARIO_BREACH = "STRESS_SCENARIO_BREACH"             # Violação de cenário primitivo de estresse
    CLOSEOUT_PREVENTION = "CLOSEOUT_PREVENTION"                   # Prevenção de encerramento forçado de posições
    VOLATILITY_SPIKE = "VOLATILITY_SPIKE"                         # Disparo por choque abrupto de volatilidade
    CONCENTRATION_LIMIT_EXCEEDED = "CONCENTRATION_LIMIT_EXCEEDED" # Violação de limite de concentração


class MarginCallStatus(str, Enum):
    """Status do ciclo de vida da chamada de margem intradiária."""
    ISSUED = "ISSUED"                         # Emitida pelo CORE, aguardando início de liquidação
    PENDING_SETTLEMENT = "PENDING_SETTLEMENT" # Mensagem pacs.008 em trânsito no canal SPI/SPB
    SETTLED = "SETTLED"                       # Liquidada com sucesso no SPI (pacs.002 ACSC) em D0
    FAILED = "FAILED"                         # Rejeitada no SPI ou mandato inválido (pacs.002 RJCT)
    DEFAULTED = "DEFAULTED"                   # Default formalizado com bloqueio pré-trade e closeout


class SpiPaymentStatus(str, Enum):
    """Código de status ISO 20022 da transação retornado no pacs.002."""
    ACSC = "ACSC"  # AcceptedSettlementCompleted - Liquidação financeira concluída
    RJCT = "RJCT"  # Rejected - Rejeitada pelo SPI ou participante recebedor
    ACTC = "ACTC"  # AcceptedTechnicalValidation - Aceite técnico prévio


class SpiRejectReasonCode(str, Enum):
    """Códigos de erro regulamentares do SPI / Pix Automático (Bacen)."""
    AM04 = "AM04"  # InsufficientFunds - Saldo insuficiente na Conta PI ou conta bancária
    AC04 = "AC04"  # ClosedAccount - Conta de pagamento encerrada ou bloqueada
    DS04 = "DS04"  # OrderDuplicate - Ordem duplicada / EndToEndId já processado
    RC07 = "RC07"  # InvalidMandate - Mandato inexistente, suspenso ou revogado
    ED05 = "ED05"  # LimitExceeded - Limite por chamada ou mensal do Pix Automático excedido
    AB03 = "AB03"  # SettlementTimeout - Tempo limite do SPI / SLA expirado (> 10s)
    BE05 = "BE05"  # InvalidPixKey - Chave DICT inativa ou inexistente


class PixKeyType(str, Enum):
    """Tipologia de chave Pix registrada no DICT."""
    CPF = "CPF"
    CNPJ = "CNPJ"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    EVP = "EVP"  # Chave Aleatória (UUID)


# ---------------------------------------------------------------------------
# Funções Utilitárias de Validação
# ---------------------------------------------------------------------------

def validate_cpf(cpf: str) -> str:
    """Valida e sanitiza CPF brasileiro através do algoritmo dos dígitos verificadores (módulo 11)."""
    digits = re.sub(r"\D", "", cpf)
    if len(digits) != 11 or len(set(digits)) == 1:
        raise InvalidTaxIdError(f"CPF inválido: '{cpf}' deve possuir 11 dígitos numéricos não repetidos.")
    
    # 1º dígito verificador
    s1 = sum(int(digits[i]) * (10 - i) for i in range(9))
    r1 = 11 - (s1 % 11)
    d1 = 0 if r1 in (10, 11) else r1
    if int(digits[9]) != d1:
        raise InvalidTaxIdError(f"Primeiro dígito verificador do CPF '{cpf}' incorreto.")
    
    # 2º dígito verificador
    s2 = sum(int(digits[i]) * (11 - i) for i in range(10))
    r2 = 11 - (s2 % 11)
    d2 = 0 if r2 in (10, 11) else r2
    if int(digits[10]) != d2:
        raise InvalidTaxIdError(f"Segundo dígito verificador do CPF '{cpf}' incorreto.")
    
    return digits


def validate_cnpj(cnpj: str) -> str:
    """Valida e sanitiza CNPJ brasileiro através do algoritmo dos dígitos verificadores."""
    digits = re.sub(r"\D", "", cnpj)
    if len(digits) != 14 or len(set(digits)) == 1:
        raise InvalidTaxIdError(f"CNPJ inválido: '{cnpj}' deve possuir 14 dígitos numéricos não repetidos.")
    
    weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    s1 = sum(int(digits[i]) * weights1[i] for i in range(12))
    r1 = s1 % 11
    d1 = 0 if r1 < 2 else 11 - r1
    if int(digits[12]) != d1:
        raise InvalidTaxIdError(f"Primeiro dígito verificador do CNPJ '{cnpj}' incorreto.")
    
    weights2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    s2 = sum(int(digits[i]) * weights2[i] for i in range(13))
    r2 = s2 % 11
    d2 = 0 if r2 < 2 else 11 - r2
    if int(digits[13]) != d2:
        raise InvalidTaxIdError(f"Segundo dígito verificador do CNPJ '{cnpj}' incorreto.")
    
    return digits


def validate_tax_id(tax_id: str) -> str:
    """Valida CPF (11 dígitos) ou CNPJ (14 dígitos)."""
    cleaned = re.sub(r"\D", "", tax_id)
    if len(cleaned) == 11:
        return validate_cpf(cleaned)
    elif len(cleaned) == 14:
        return validate_cnpj(cleaned)
    raise InvalidTaxIdError(f"Identificador fiscal '{tax_id}' deve ter 11 (CPF) ou 14 (CNPJ) dígitos.")


def validate_ispb(ispb: str) -> str:
    """Valida código ISPB de 8 dígitos estritos."""
    cleaned = str(ispb).strip()
    if not _ISPB_REGEX.match(cleaned):
        raise InvalidIspbError(f"ISPB inválido: '{ispb}'. Deve conter exatamente 8 dígitos numéricos.")
    return cleaned


def validate_end_to_end_id(e2e_id: str) -> str:
    """Valida EndToEndId padrão Bacen SPI de exatamente 32 caracteres."""
    cleaned = str(e2e_id).strip()
    if not _E2E_REGEX.match(cleaned):
        raise InvalidEndToEndIdError(
            f"EndToEndId '{e2e_id}' inválido. Deve possuir 32 caracteres: "
            "'E' + 8 dígitos ISPB + 12 dígitos UTC (YYYYMMDDHHMM) + 11 alfanuméricos."
        )
    return cleaned


def generate_end_to_end_id(
    ispb: str,
    timestamp: Optional[datetime] = None,
    suffix: Optional[str] = None
) -> str:
    """Gera um EndToEndId determinístico ou pseudoaleatório de 32 posições conforme padrão BACEN."""
    valid_ispb = validate_ispb(ispb)
    ts = timestamp or datetime.now(timezone.utc)
    ts_str = ts.strftime("%Y%m%d%H%M")
    
    if suffix:
        clean_suffix = re.sub(r"[^a-zA-Z0-9]", "", suffix)
        if len(clean_suffix) < 11:
            clean_suffix = clean_suffix.ljust(11, "0")
        else:
            clean_suffix = clean_suffix[:11]
    else:
        clean_suffix = uuid.uuid4().hex[:11].upper()
    
    e2e = f"E{valid_ispb}{ts_str}{clean_suffix}"
    return validate_end_to_end_id(e2e)


def validate_pix_key(key: str, key_type: PixKeyType) -> bool:
    """Valida sintaxe de chave Pix conforme sua tipologia."""
    clean_key = key.strip()
    if key_type == PixKeyType.CPF:
        try:
            validate_cpf(clean_key)
            return True
        except InvalidTaxIdError:
            return False
    elif key_type == PixKeyType.CNPJ:
        try:
            validate_cnpj(clean_key)
            return True
        except InvalidTaxIdError:
            return False
    elif key_type == PixKeyType.EMAIL:
        return bool(_EMAIL_REGEX.match(clean_key))
    elif key_type == PixKeyType.PHONE:
        return bool(_PHONE_REGEX.match(clean_key))
    elif key_type == PixKeyType.EVP:
        return bool(_UUID_REGEX.match(clean_key))
    return False


# ---------------------------------------------------------------------------
# Estruturas de Dados do Domínio
# ---------------------------------------------------------------------------

@dataclass
class DictKeyRecord:
    """Registro de identificador no Diretório de Identificadores de Contas Transacionais (DICT)."""
    pix_key: str
    key_type: PixKeyType
    tax_id: str
    name: str
    ispb: str
    branch: str
    account_number: str
    account_type: str = "CACC"
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.tax_id = validate_tax_id(self.tax_id)
        self.ispb = validate_ispb(self.ispb)
        if not validate_pix_key(self.pix_key, self.key_type):
            raise InvalidPixKeyError(f"Chave Pix '{self.pix_key}' inválida para o tipo {self.key_type.value}.")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pix_key": self.pix_key,
            "key_type": self.key_type.value,
            "tax_id": self.tax_id,
            "name": self.name,
            "ispb": self.ispb,
            "branch": self.branch,
            "account_number": self.account_number,
            "account_type": self.account_type,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class PixAutomaticoMandate:
    """Mandato de Autorização Prévia de Débito Recorrente (Pix Automático - Res. BCB 341/2023)."""
    mandate_id: str
    payer_tax_id: str
    payer_name: str
    payer_ispb: str
    payer_branch: str
    payer_account_number: str
    payer_pix_key: Optional[str] = None
    receiver_tax_id: str = TAX_ID_B3_DEFAULT
    receiver_name: str = B3_NAME_DEFAULT
    receiver_ispb: str = ISPB_B3_DEFAULT
    receiver_pix_key: str = TAX_ID_B3_DEFAULT
    receiver_account: str = B3_CLEARING_ACCOUNT_DEFAULT
    max_limit_per_call: Decimal = field(default=Decimal("10000000.00"))
    total_monthly_limit: Decimal = field(default=Decimal("50000000.00"))
    current_monthly_utilized: Decimal = field(default=Decimal("0.00"))
    start_date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expiration_date: Optional[datetime] = None
    status: MandateStatus = MandateStatus.ACTIVE
    authorization_channel: AuthorizationChannel = AuthorizationChannel.OPEN_FINANCE
    recurrence_purpose: RecurrencePurpose = RecurrencePurpose.INTRADAY_MARGIN_CALL
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.payer_tax_id = validate_tax_id(self.payer_tax_id)
        self.payer_ispb = validate_ispb(self.payer_ispb)
        self.receiver_tax_id = validate_tax_id(self.receiver_tax_id)
        self.receiver_ispb = validate_ispb(self.receiver_ispb)
        self.max_limit_per_call = _quantize_cents(self.max_limit_per_call)
        self.total_monthly_limit = _quantize_cents(self.total_monthly_limit)
        self.current_monthly_utilized = _quantize_cents(self.current_monthly_utilized)
        if self.max_limit_per_call <= Decimal("0.00"):
            raise PixAutomaticoValidationError("max_limit_per_call deve ser estritamente positivo.")
        if self.total_monthly_limit < self.max_limit_per_call:
            raise PixAutomaticoValidationError("total_monthly_limit não pode ser inferior ao max_limit_per_call.")

    def is_valid_for_call(
        self,
        amount: Decimal,
        timestamp: Optional[datetime] = None
    ) -> Tuple[bool, Optional[SpiRejectReasonCode], Optional[str]]:
        """Verifica se o mandato autoriza a chamada de margem específica."""
        now = timestamp or datetime.now(timezone.utc)
        amount_q = _quantize_cents(amount)

        if self.status != MandateStatus.ACTIVE:
            return False, SpiRejectReasonCode.RC07, f"Mandato com status {self.status.value} não autoriza débito."

        if now < self.start_date:
            return False, SpiRejectReasonCode.RC07, "Mandato ainda não entrou em vigência."

        if self.expiration_date and now > self.expiration_date:
            return False, SpiRejectReasonCode.RC07, "Mandato expirado temporalmente."

        if amount_q > self.max_limit_per_call:
            return (
                False,
                SpiRejectReasonCode.ED05,
                f"Valor da chamada R$ {amount_q} excede limite por transação autorizado R$ {self.max_limit_per_call}."
            )

        if (self.current_monthly_utilized + amount_q) > self.total_monthly_limit:
            return (
                False,
                SpiRejectReasonCode.ED05,
                f"Valor da chamada R$ {amount_q} somado ao utilizado R$ {self.current_monthly_utilized} "
                f"excede o teto mensal R$ {self.total_monthly_limit}."
            )

        return True, None, None

    def apply_utilization(self, amount: Decimal) -> None:
        """Registra a utilização de limite no ciclo corrente."""
        amount_q = _quantize_cents(amount)
        self.current_monthly_utilized = _quantize_cents(self.current_monthly_utilized + amount_q)
        self.updated_at = datetime.now(timezone.utc)

    def revoke(self, reason: str = "") -> None:
        """Revoga o mandato permanentemente a pedido do pagador."""
        self.status = MandateStatus.REVOKED
        self.updated_at = datetime.now(timezone.utc)

    def suspend(self, reason: str = "") -> None:
        """Suspende preventivamente o mandato."""
        self.status = MandateStatus.SUSPENDED
        self.updated_at = datetime.now(timezone.utc)

    def reactivate(self) -> None:
        """Reativa um mandato suspenso se não tiver expirado."""
        now = datetime.now(timezone.utc)
        if self.expiration_date and now > self.expiration_date:
            self.status = MandateStatus.EXPIRED
            raise MandateError("Não é possível reativar mandato cujo prazo de validade expirou.")
        self.status = MandateStatus.ACTIVE
        self.updated_at = now

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mandate_id": self.mandate_id,
            "payer_tax_id": self.payer_tax_id,
            "payer_name": self.payer_name,
            "payer_ispb": self.payer_ispb,
            "payer_branch": self.payer_branch,
            "payer_account_number": self.payer_account_number,
            "payer_pix_key": self.payer_pix_key,
            "receiver_tax_id": self.receiver_tax_id,
            "receiver_name": self.receiver_name,
            "receiver_ispb": self.receiver_ispb,
            "receiver_pix_key": self.receiver_pix_key,
            "receiver_account": self.receiver_account,
            "max_limit_per_call": str(self.max_limit_per_call),
            "total_monthly_limit": str(self.total_monthly_limit),
            "current_monthly_utilized": str(self.current_monthly_utilized),
            "start_date": self.start_date.isoformat(),
            "expiration_date": self.expiration_date.isoformat() if self.expiration_date else None,
            "status": self.status.value,
            "authorization_channel": self.authorization_channel.value,
            "recurrence_purpose": self.recurrence_purpose.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class Pacs008Message:
    """Instrução ISO 20022 pacs.008 (Customer Credit Transfer via Pix Automático SPI)."""
    msg_id: str
    e2e_id: str
    mandate_id: str
    amount: Decimal
    currency: str = "BRL"
    debtor_tax_id: str = ""
    debtor_name: str = ""
    debtor_ispb: str = ""
    debtor_account: str = ""
    creditor_tax_id: str = TAX_ID_B3_DEFAULT
    creditor_name: str = B3_NAME_DEFAULT
    creditor_ispb: str = ISPB_B3_DEFAULT
    creditor_account: str = B3_CLEARING_ACCOUNT_DEFAULT
    payment_purpose: str = "B3_INTRADAY_MARGIN_CALL"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.amount = _quantize_cents(self.amount)
        validate_end_to_end_id(self.e2e_id)
        validate_ispb(self.debtor_ispb)
        validate_ispb(self.creditor_ispb)

    def to_xml(self) -> str:
        """Gera XML sintaticamente compatível com ISO 20022 pacs.008."""
        root = ET.Element("Document", {"xmlns": XML_NS_PACS008, "xmlns:pix": XML_NS_BACEN_AUTO})
        fi_to_fi = ET.SubElement(root, "FIToFICstmrCdtTrf")
        
        # GrpHdr
        grp_hdr = ET.SubElement(fi_to_fi, "GrpHdr")
        ET.SubElement(grp_hdr, "MsgId").text = self.msg_id
        ET.SubElement(grp_hdr, "CreDtTm").text = self.created_at.isoformat()
        ET.SubElement(grp_hdr, "NbOfTxs").text = "1"
        sttlm_inf = ET.SubElement(grp_hdr, "SttlmInf")
        ET.SubElement(sttlm_inf, "SttlmMtd").text = "CLRG"
        clr_sys = ET.SubElement(sttlm_inf, "ClrSys")
        ET.SubElement(clr_sys, "Prtry").text = "SPI"
        
        # CdtTrfTxInf
        tx_inf = ET.SubElement(fi_to_fi, "CdtTrfTxInf")
        pmt_id = ET.SubElement(tx_inf, "PmtId")
        ET.SubElement(pmt_id, "EndToEndId").text = self.e2e_id
        
        amt = ET.SubElement(tx_inf, "IntrBkSttlmAmt", {"Ccy": self.currency})
        amt.text = str(self.amount)
        
        # Mandate Reference
        mndt_rel = ET.SubElement(tx_inf, "MndtRltdInf")
        ET.SubElement(mndt_rel, "MndtId").text = self.mandate_id
        
        # Dbtr
        dbtr = ET.SubElement(tx_inf, "Dbtr")
        ET.SubElement(dbtr, "Nm").text = self.debtor_name
        
        dbtr_agt = ET.SubElement(tx_inf, "DbtrAgt")
        fin_inst_dbtr = ET.SubElement(dbtr_agt, "FinInstnId")
        ET.SubElement(fin_inst_dbtr, "ISPB").text = self.debtor_ispb
        
        # Cdtr
        cdtr = ET.SubElement(tx_inf, "Cdtr")
        ET.SubElement(cdtr, "Nm").text = self.creditor_name
        
        cdtr_agt = ET.SubElement(tx_inf, "CdtrAgt")
        fin_inst_cdtr = ET.SubElement(cdtr_agt, "FinInstnId")
        ET.SubElement(fin_inst_cdtr, "ISPB").text = self.creditor_ispb
        
        # Purp
        purp = ET.SubElement(tx_inf, "Purp")
        ET.SubElement(purp, "Prtry").text = self.payment_purpose

        return ET.tostring(root, encoding="utf-8").decode("utf-8")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "e2e_id": self.e2e_id,
            "mandate_id": self.mandate_id,
            "amount": str(self.amount),
            "currency": self.currency,
            "debtor_tax_id": self.debtor_tax_id,
            "debtor_name": self.debtor_name,
            "debtor_ispb": self.debtor_ispb,
            "debtor_account": self.debtor_account,
            "creditor_tax_id": self.creditor_tax_id,
            "creditor_name": self.creditor_name,
            "creditor_ispb": self.creditor_ispb,
            "creditor_account": self.creditor_account,
            "payment_purpose": self.payment_purpose,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class Pacs002Message:
    """Relatório de Status de Pagamento ISO 20022 pacs.002 emitido pelo SPI/BACEN."""
    msg_id: str
    original_msg_id: str
    original_e2e_id: str
    status: SpiPaymentStatus
    reason_code: Optional[SpiRejectReasonCode] = None
    reason_description: Optional[str] = None
    settlement_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    latency_ms: float = 0.0

    def to_xml(self) -> str:
        """Gera representação XML ISO 20022 pacs.002."""
        root = ET.Element("Document", {"xmlns": XML_NS_PACS002})
        rpt = ET.SubElement(root, "FIToFIPmtStsRpt")
        
        grp_hdr = ET.SubElement(rpt, "GrpHdr")
        ET.SubElement(grp_hdr, "MsgId").text = self.msg_id
        ET.SubElement(grp_hdr, "CreDtTm").text = self.settlement_timestamp.isoformat()
        
        tx_inf = ET.SubElement(rpt, "TxInfAndSts")
        ET.SubElement(tx_inf, "OrgnlEndToEndId").text = self.original_e2e_id
        ET.SubElement(tx_inf, "TxSts").text = self.status.value
        
        if self.reason_code:
            sts_rsn = ET.SubElement(tx_inf, "StsRsnInf")
            rsn = ET.SubElement(sts_rsn, "Rsn")
            ET.SubElement(rsn, "Prtry").text = self.reason_code.value
            if self.reason_description:
                ET.SubElement(sts_rsn, "AddtlInf").text = self.reason_description
                
        return ET.tostring(root, encoding="utf-8").decode("utf-8")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "original_msg_id": self.original_msg_id,
            "original_e2e_id": self.original_e2e_id,
            "status": self.status.value,
            "reason_code": self.reason_code.value if self.reason_code else None,
            "reason_description": self.reason_description,
            "settlement_timestamp": self.settlement_timestamp.isoformat(),
            "latency_ms": self.latency_ms,
        }


@dataclass
class SpiPiAccount:
    """Conta Pagamentos Instantâneos (Conta PI) mantida pelo participante no BACEN / SPI."""
    ispb: str
    account_number: str
    participant_name: str
    balance: Decimal
    available_limit: Decimal = Decimal("0.00")
    initial_balance: Decimal = field(init=False)
    total_debited: Decimal = Decimal("0.00")
    total_credited: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.ispb = validate_ispb(self.ispb)
        self.balance = _quantize_cents(self.balance)
        self.available_limit = _quantize_cents(self.available_limit)
        self.total_debited = _quantize_cents(self.total_debited)
        self.total_credited = _quantize_cents(self.total_credited)
        self.initial_balance = self.balance

    @property
    def total_liquidity(self) -> Decimal:
        """Liquidez total disponível (saldo próprio + limite concedido)."""
        return self.balance + self.available_limit

    def debit(self, amount: Decimal) -> None:
        """Efetua débito no SPI com validação estrita de liquidez."""
        amt = _quantize_cents(amount)
        if amt <= Decimal("0.00"):
            raise PixAutomaticoValidationError("Valor de débito deve ser estritamente positivo.")
        if self.total_liquidity < amt:
            raise InsufficientFundsSpiError(
                f"Conta PI {self.account_number} (ISPB {self.ispb}) não possui saldo/limite "
                f"suficiente: disponível R$ {self.total_liquidity}, solicitado R$ {amt}."
            )
        self.balance = _quantize_cents(self.balance - amt)
        self.total_debited = _quantize_cents(self.total_debited + amt)

    def credit(self, amount: Decimal) -> None:
        """Efetua crédito no SPI."""
        amt = _quantize_cents(amount)
        if amt <= Decimal("0.00"):
            raise PixAutomaticoValidationError("Valor de crédito deve ser estritamente positivo.")
        self.balance = _quantize_cents(self.balance + amt)
        self.total_credited = _quantize_cents(self.total_credited + amt)

    def verify_conservation(self) -> bool:
        """Verifica a equação de conservação contábil da Conta PI."""
        expected = self.initial_balance - self.total_debited + self.total_credited
        return self.balance == expected

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ispb": self.ispb,
            "account_number": self.account_number,
            "participant_name": self.participant_name,
            "balance": str(self.balance),
            "available_limit": str(self.available_limit),
            "initial_balance": str(self.initial_balance),
            "total_debited": str(self.total_debited),
            "total_credited": str(self.total_credited),
            "conserved": self.verify_conservation(),
        }


@dataclass
class B3ParticipantCollateralAccount:
    """Conta de custódia, colaterais e capacidade de risco de participante na B3."""
    participant_id: str
    subaccount_id: str
    tax_id: str
    cash_collateral_balance: Decimal = Decimal("0.00")
    required_margin: Decimal = Decimal("0.00")
    pretrade_risk_limit: Decimal = Decimal("50000000.00")
    pretrade_capacity_utilized: Decimal = Decimal("0.00")
    is_blocked_for_trading: bool = False
    is_in_default: bool = False

    def __post_init__(self) -> None:
        self.tax_id = validate_tax_id(self.tax_id)
        self.cash_collateral_balance = _quantize_cents(self.cash_collateral_balance)
        self.required_margin = _quantize_cents(self.required_margin)
        self.pretrade_risk_limit = _quantize_cents(self.pretrade_risk_limit)
        self.pretrade_capacity_utilized = _quantize_cents(self.pretrade_capacity_utilized)

    @property
    def margin_deficit(self) -> Decimal:
        """Déficit de margem na câmara: required_margin - cash_collateral_balance."""
        if self.required_margin > self.cash_collateral_balance:
            return _quantize_cents(self.required_margin - self.cash_collateral_balance)
        return Decimal("0.00")

    @property
    def available_pretrade_capacity(self) -> Decimal:
        """Capacidade livre para submissão de ordens no PUMA Trading System."""
        if self.is_blocked_for_trading or self.is_in_default:
            return Decimal("0.00")
        free_cap = self.pretrade_risk_limit - self.pretrade_capacity_utilized
        return max(Decimal("0.00"), free_cap)

    def apply_core_margin_deficit(self, required_amount: Decimal) -> Decimal:
        """Registra elevação de margem requerida pelo motor CORE e apura déficit."""
        self.required_margin = _quantize_cents(required_amount)
        return self.margin_deficit

    def credit_settled_collateral(self, amount: Decimal) -> None:
        """Credita garantia em dinheiro aportada via Pix Automático e reequilibra capacidade."""
        amt = _quantize_cents(amount)
        self.cash_collateral_balance = _quantize_cents(self.cash_collateral_balance + amt)
        if self.margin_deficit == Decimal("0.00") and self.is_blocked_for_trading and not self.is_in_default:
            self.is_blocked_for_trading = False

    def trigger_default(self, reason: str = "") -> None:
        """Coloca a conta em default operacional imediato, bloqueando ordens pré-trade."""
        self.is_in_default = True
        self.is_blocked_for_trading = True
        self.pretrade_capacity_utilized = self.pretrade_risk_limit  # esgota capacidade

    def to_dict(self) -> Dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "subaccount_id": self.subaccount_id,
            "tax_id": self.tax_id,
            "cash_collateral_balance": str(self.cash_collateral_balance),
            "required_margin": str(self.required_margin),
            "margin_deficit": str(self.margin_deficit),
            "pretrade_risk_limit": str(self.pretrade_risk_limit),
            "pretrade_capacity_utilized": str(self.pretrade_capacity_utilized),
            "available_pretrade_capacity": str(self.available_pretrade_capacity),
            "is_blocked_for_trading": self.is_blocked_for_trading,
            "is_in_default": self.is_in_default,
        }


@dataclass
class IntradayMarginCall:
    """Chamada de Margem Intradiária disparada pelo motor CORE B3."""
    margin_call_id: str
    participant_id: str
    subaccount_id: str
    payer_tax_id: str
    deficit_amount: Decimal
    deadline_seconds: int = 10
    trigger_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: MarginCallReason = MarginCallReason.INTRADAY_CORE_DEFICIT
    status: MarginCallStatus = MarginCallStatus.ISSUED
    mandate_id: Optional[str] = None
    settlement_e2e_id: Optional[str] = None
    settled_amount: Decimal = Decimal("0.00")
    settled_timestamp: Optional[datetime] = None
    failure_reason: Optional[str] = None
    default_triggered: bool = False

    def __post_init__(self) -> None:
        self.payer_tax_id = validate_tax_id(self.payer_tax_id)
        self.deficit_amount = _quantize_cents(self.deficit_amount)
        self.settled_amount = _quantize_cents(self.settled_amount)
        if self.deficit_amount <= Decimal("0.00"):
            raise PixAutomaticoValidationError("O valor do déficit da chamada de margem deve ser positivo.")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "margin_call_id": self.margin_call_id,
            "participant_id": self.participant_id,
            "subaccount_id": self.subaccount_id,
            "payer_tax_id": self.payer_tax_id,
            "deficit_amount": str(self.deficit_amount),
            "deadline_seconds": self.deadline_seconds,
            "trigger_timestamp": self.trigger_timestamp.isoformat(),
            "reason": self.reason.value,
            "status": self.status.value,
            "mandate_id": self.mandate_id,
            "settlement_e2e_id": self.settlement_e2e_id,
            "settled_amount": str(self.settled_amount),
            "settled_timestamp": self.settled_timestamp.isoformat() if self.settled_timestamp else None,
            "failure_reason": self.failure_reason,
            "default_triggered": self.default_triggered,
        }


@dataclass
class PixAutomaticoSettlementReceipt:
    """Comprovante e recibo irrefutável de liquidação instantânea D0 via Pix Automático."""
    receipt_id: str
    margin_call_id: str
    e2e_id: str
    mandate_id: str
    payer_tax_id: str
    payer_ispb: str
    receiver_ispb: str
    amount: Decimal
    status: SpiPaymentStatus
    settlement_timestamp: datetime
    latency_seconds: float
    sla_met: bool
    pretrade_capacity_restored: Decimal
    risk_status_after: str
    idempotent_replay: bool = False
    reject_code: Optional[str] = None
    reject_reason: Optional[str] = None

    def __post_init__(self) -> None:
        self.amount = _quantize_cents(self.amount)
        self.pretrade_capacity_restored = _quantize_cents(self.pretrade_capacity_restored)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "margin_call_id": self.margin_call_id,
            "e2e_id": self.e2e_id,
            "mandate_id": self.mandate_id,
            "payer_tax_id": self.payer_tax_id,
            "payer_ispb": self.payer_ispb,
            "receiver_ispb": self.receiver_ispb,
            "amount": str(self.amount),
            "status": self.status.value,
            "settlement_timestamp": self.settlement_timestamp.isoformat(),
            "latency_seconds": self.latency_seconds,
            "sla_met": self.sla_met,
            "pretrade_capacity_restored": str(self.pretrade_capacity_restored),
            "risk_status_after": self.risk_status_after,
            "idempotent_replay": self.idempotent_replay,
            "reject_code": self.reject_code,
            "reject_reason": self.reject_reason,
        }


# ---------------------------------------------------------------------------
# Motor de Negócio: PixAutomaticoMarginEngine
# ---------------------------------------------------------------------------

class PixAutomaticoMarginEngine:
    """
    Motor central de Gestão e Liquidação em Tempo Real de Chamadas de Margem
    Intradiária via Pix Automático e SPI da Câmara B3 (Fluxo FL-090).
    """

    def __init__(
        self,
        ispb_camara: str = ISPB_B3_DEFAULT,
        camara_account_number: str = B3_CLEARING_ACCOUNT_DEFAULT,
        camara_initial_balance: Decimal = Decimal("500000000.00")
    ) -> None:
        self.ispb_camara = validate_ispb(ispb_camara)
        self.camara_account_number = camara_account_number
        
        # Diretório de Chaves DICT (chave -> registro)
        self.dict_registry: Dict[str, DictKeyRecord] = {}
        
        # Mandatos de Pix Automático (mandate_id -> mandato)
        self.mandates: Dict[str, PixAutomaticoMandate] = {}
        self.mandates_by_payer: Dict[str, str] = {}  # tax_id -> mandate_id (mais recente ativo)
        
        # Contas Pagamentos Instantâneos (ISPB -> SpiPiAccount)
        self.pi_accounts: Dict[str, SpiPiAccount] = {}
        
        # Cria a Conta PI da B3 na inicialização
        self.pi_b3 = SpiPiAccount(
            ispb=self.ispb_camara,
            account_number=self.camara_account_number,
            participant_name=B3_NAME_DEFAULT,
            balance=camara_initial_balance,
            available_limit=Decimal("0.00")
        )
        self.pi_accounts[self.ispb_camara] = self.pi_b3

        # Contas de Colaterais B3 ((participant_id, subaccount_id) -> Account)
        self.collateral_accounts: Dict[Tuple[str, str], B3ParticipantCollateralAccount] = {}
        
        # Chamadas de Margem (margin_call_id -> MarginCall)
        self.margin_calls: Dict[str, IntradayMarginCall] = {}
        
        # Recibos e histórico indexados por E2EID
        self.settlement_receipts: Dict[str, PixAutomaticoSettlementReceipt] = {}
        self.pacs008_messages: Dict[str, Pacs008Message] = {}
        self.pacs002_messages: Dict[str, Pacs002Message] = {}

    # -----------------------------------------------------------------------
    # Gestão do DICT
    # -----------------------------------------------------------------------

    def register_dict_key(
        self,
        pix_key: str,
        key_type: PixKeyType,
        tax_id: str,
        name: str,
        ispb: str,
        branch: str,
        account_number: str,
        account_type: str = "CACC",
        is_active: bool = True
    ) -> DictKeyRecord:
        """Registra uma chave Pix no DICT local com validação estrita."""
        record = DictKeyRecord(
            pix_key=pix_key,
            key_type=key_type,
            tax_id=tax_id,
            name=name,
            ispb=ispb,
            branch=branch,
            account_number=account_number,
            account_type=account_type,
            is_active=is_active
        )
        self.dict_registry[pix_key] = record
        return record

    def lookup_dict_key(self, pix_key: str) -> Optional[DictKeyRecord]:
        """Consulta uma chave no DICT."""
        return self.dict_registry.get(pix_key)

    # -----------------------------------------------------------------------
    # Gestão de Mandatos Pix Automático (Consentimento)
    # -----------------------------------------------------------------------

    def register_mandate(self, mandate: PixAutomaticoMandate) -> PixAutomaticoMandate:
        """Cadastra um mandato prévio de autorização de débito recorrente."""
        self.mandates[mandate.mandate_id] = mandate
        if mandate.status == MandateStatus.ACTIVE:
            self.mandates_by_payer[mandate.payer_tax_id] = mandate.mandate_id
        return mandate

    def get_mandate(self, mandate_id: str) -> Optional[PixAutomaticoMandate]:
        """Recupera mandato pelo identificador."""
        return self.mandates.get(mandate_id)

    def get_active_mandate_for_payer(self, payer_tax_id: str) -> Optional[PixAutomaticoMandate]:
        """Recupera o mandato ativo vinculado ao titular pagador."""
        clean_tax_id = validate_tax_id(payer_tax_id)
        mid = self.mandates_by_payer.get(clean_tax_id)
        if mid and mid in self.mandates:
            m = self.mandates[mid]
            if m.status == MandateStatus.ACTIVE:
                return m
        # Busca iterativa fallback
        for m in self.mandates.values():
            if m.payer_tax_id == clean_tax_id and m.status == MandateStatus.ACTIVE:
                self.mandates_by_payer[clean_tax_id] = m.mandate_id
                return m
        return None

    def revoke_mandate(self, mandate_id: str, reason: str = "") -> None:
        """Revoga o mandato do titular pagador."""
        m = self.mandates.get(mandate_id)
        if not m:
            raise MandateNotFoundError(f"Mandato '{mandate_id}' não encontrado.")
        m.revoke(reason)
        if self.mandates_by_payer.get(m.payer_tax_id) == mandate_id:
            del self.mandates_by_payer[m.payer_tax_id]

    def suspend_mandate(self, mandate_id: str, reason: str = "") -> None:
        """Suspende preventivamente o mandato."""
        m = self.mandates.get(mandate_id)
        if not m:
            raise MandateNotFoundError(f"Mandato '{mandate_id}' não encontrado.")
        m.suspend(reason)

    def reactivate_mandate(self, mandate_id: str) -> None:
        """Reativa mandato suspenso."""
        m = self.mandates.get(mandate_id)
        if not m:
            raise MandateNotFoundError(f"Mandato '{mandate_id}' não encontrado.")
        m.reactivate()
        self.mandates_by_payer[m.payer_tax_id] = m.mandate_id

    # -----------------------------------------------------------------------
    # Gestão de Contas PI e Colaterais
    # -----------------------------------------------------------------------

    def register_pi_account(self, account: SpiPiAccount) -> None:
        """Cadastra uma Conta PI participante do SPI."""
        self.pi_accounts[account.ispb] = account
        if account.ispb == self.ispb_camara:
            self.pi_b3 = account

    def get_pi_account(self, ispb: str) -> SpiPiAccount:
        """Recupera Conta PI por ISPB."""
        clean_ispb = validate_ispb(ispb)
        if clean_ispb not in self.pi_accounts:
            raise PixAutomaticoValidationError(f"Conta PI com ISPB '{clean_ispb}' não encontrada no SPI.")
        return self.pi_accounts[clean_ispb]

    def register_collateral_account(self, account: B3ParticipantCollateralAccount) -> None:
        """Cadastra uma conta de colaterais/capacidade de risco na B3."""
        key = (account.participant_id, account.subaccount_id)
        self.collateral_accounts[key] = account

    def get_collateral_account(self, participant_id: str, subaccount_id: str) -> B3ParticipantCollateralAccount:
        """Recupera conta de colaterais da B3."""
        key = (participant_id, subaccount_id)
        if key not in self.collateral_accounts:
            raise PixAutomaticoValidationError(
                f"Conta de colaterais do participante {participant_id}/{subaccount_id} não cadastrada."
            )
        return self.collateral_accounts[key]

    # -----------------------------------------------------------------------
    # Emissão de Chamada de Margem Intradiária CORE
    # -----------------------------------------------------------------------

    def issue_margin_call(
        self,
        participant_id: str,
        subaccount_id: str,
        payer_tax_id: str,
        deficit_amount: Union[Decimal, int, float, str],
        reason: MarginCallReason = MarginCallReason.INTRADAY_CORE_DEFICIT,
        deadline_seconds: int = 10,
        trigger_timestamp: Optional[datetime] = None,
        margin_call_id: Optional[str] = None
    ) -> IntradayMarginCall:
        """
        Emite uma chamada de margem intradiária urgente a partir do motor de risco CORE.
        Verifica a existência do participante e vincula mandato Pix Automático previamente ativo.
        """
        clean_tax_id = validate_tax_id(payer_tax_id)
        deficit = _quantize_cents(deficit_amount)
        if deficit <= Decimal("0.00"):
            raise PixAutomaticoValidationError("Déficit de chamada de margem deve ser estritamente positivo.")

        collateral_acc = self.get_collateral_account(participant_id, subaccount_id)
        collateral_acc.apply_core_margin_deficit(collateral_acc.cash_collateral_balance + deficit)

        now = trigger_timestamp or datetime.now(timezone.utc)
        mc_id = margin_call_id or f"B3-MC-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

        mandate = self.get_active_mandate_for_payer(clean_tax_id)
        mandate_id = mandate.mandate_id if mandate else None

        margin_call = IntradayMarginCall(
            margin_call_id=mc_id,
            participant_id=participant_id,
            subaccount_id=subaccount_id,
            payer_tax_id=clean_tax_id,
            deficit_amount=deficit,
            deadline_seconds=deadline_seconds,
            trigger_timestamp=now,
            reason=reason,
            status=MarginCallStatus.ISSUED,
            mandate_id=mandate_id,
        )

        self.margin_calls[mc_id] = margin_call
        return margin_call

    # -----------------------------------------------------------------------
    # Execução e Liquidação Instantânea via Pix Automático e SPI
    # -----------------------------------------------------------------------

    def process_margin_call_settlement(
        self,
        margin_call_id: str,
        simulated_latency_seconds: float = 1.25,
        force_spi_reject_code: Optional[SpiRejectReasonCode] = None,
        override_e2e_id: Optional[str] = None
    ) -> PixAutomaticoSettlementReceipt:
        """
        Executa a liquidação instantânea D0 da chamada de margem via Pix Automático no SPI.
        
        Garantias:
        - Idempotência absoluta por EndToEndId / chamada já liquidada.
        - SLA rigoroso (< 10 segundos).
        - Soma-zero estrita no SPI: Débito Pagador == Crédito B3.
        - Liberação instantânea de capacidade pré-trade em caso de ACSC.
        - Transição imediata para DEFAULT e bloqueio pré-trade em caso de rejeição (RJCT).
        """
        if margin_call_id not in self.margin_calls:
            raise PixAutomaticoValidationError(f"Chamada de margem '{margin_call_id}' não encontrada.")

        mc = self.margin_calls[margin_call_id]

        # 1. Verificação de Idempotência: se já liquidada com sucesso
        if mc.status == MarginCallStatus.SETTLED and mc.settlement_e2e_id:
            existing_receipt = self.settlement_receipts.get(mc.settlement_e2e_id)
            if existing_receipt:
                return PixAutomaticoSettlementReceipt(
                    receipt_id=existing_receipt.receipt_id,
                    margin_call_id=existing_receipt.margin_call_id,
                    e2e_id=existing_receipt.e2e_id,
                    mandate_id=existing_receipt.mandate_id,
                    payer_tax_id=existing_receipt.payer_tax_id,
                    payer_ispb=existing_receipt.payer_ispb,
                    receiver_ispb=existing_receipt.receiver_ispb,
                    amount=existing_receipt.amount,
                    status=existing_receipt.status,
                    settlement_timestamp=existing_receipt.settlement_timestamp,
                    latency_seconds=existing_receipt.latency_seconds,
                    sla_met=existing_receipt.sla_met,
                    pretrade_capacity_restored=existing_receipt.pretrade_capacity_restored,
                    risk_status_after=existing_receipt.risk_status_after,
                    idempotent_replay=True,
                    reject_code=existing_receipt.reject_code,
                    reject_reason=existing_receipt.reject_reason,
                )

        collateral_acc = self.get_collateral_account(mc.participant_id, mc.subaccount_id)
        now = datetime.now(timezone.utc)

        # 2. Localização e validação de mandato
        mandate: Optional[PixAutomaticoMandate] = None
        if mc.mandate_id and mc.mandate_id in self.mandates:
            mandate = self.mandates[mc.mandate_id]
        else:
            mandate = self.get_active_mandate_for_payer(mc.payer_tax_id)

        # Se não há mandato ativo
        if not mandate:
            return self._handle_rejection(
                margin_call=mc,
                collateral_acc=collateral_acc,
                reject_code=SpiRejectReasonCode.RC07,
                reject_msg="Titular não possui mandato de débito recorrente Pix Automático cadastrado.",
                payer_ispb="00000000",
                e2e_id=override_e2e_id or generate_end_to_end_id("00000000", now),
                mandate_id="MNDT-NONE",
                latency_sec=simulated_latency_seconds
            )

        mc.mandate_id = mandate.mandate_id

        # 3. Validação dos termos e limites do mandato
        is_mandate_valid, reject_code, reject_msg = mandate.is_valid_for_call(mc.deficit_amount, now)
        if not is_mandate_valid:
            assert reject_code is not None
            return self._handle_rejection(
                margin_call=mc,
                collateral_acc=collateral_acc,
                reject_code=reject_code,
                reject_msg=reject_msg or "Violação de regras de mandato",
                payer_ispb=mandate.payer_ispb,
                e2e_id=override_e2e_id or generate_end_to_end_id(mandate.payer_ispb, now),
                mandate_id=mandate.mandate_id,
                latency_sec=simulated_latency_seconds
            )

        # 4. Validação de Chave DICT (se configurada)
        if mandate.payer_pix_key:
            dict_rec = self.lookup_dict_key(mandate.payer_pix_key)
            if not dict_rec or not dict_rec.is_active:
                return self._handle_rejection(
                    margin_call=mc,
                    collateral_acc=collateral_acc,
                    reject_code=SpiRejectReasonCode.BE05,
                    reject_msg=f"Chave Pix '{mandate.payer_pix_key}' inexistente ou inativa no DICT.",
                    payer_ispb=mandate.payer_ispb,
                    e2e_id=override_e2e_id or generate_end_to_end_id(mandate.payer_ispb, now),
                    mandate_id=mandate.mandate_id,
                    latency_sec=simulated_latency_seconds
                )

        # 5. Geração de EndToEndId único
        if override_e2e_id:
            e2e_id = validate_end_to_end_id(override_e2e_id)
        else:
            e2e_id = generate_end_to_end_id(mandate.payer_ispb, now)

        # Verificação de colisão prévia de E2EID (idempotência no nível SPI)
        if e2e_id in self.settlement_receipts:
            existing = self.settlement_receipts[e2e_id]
            return PixAutomaticoSettlementReceipt(
                receipt_id=existing.receipt_id,
                margin_call_id=mc.margin_call_id,
                e2e_id=e2e_id,
                mandate_id=mandate.mandate_id,
                payer_tax_id=mandate.payer_tax_id,
                payer_ispb=mandate.payer_ispb,
                receiver_ispb=mandate.receiver_ispb,
                amount=existing.amount,
                status=existing.status,
                settlement_timestamp=existing.settlement_timestamp,
                latency_seconds=existing.latency_seconds,
                sla_met=existing.sla_met,
                pretrade_capacity_restored=existing.pretrade_capacity_restored,
                risk_status_after=existing.risk_status_after,
                idempotent_replay=True,
                reject_code=existing.reject_code,
                reject_reason=existing.reject_reason,
            )

        # 6. Verificação de SLA (< 10 segundos)
        if simulated_latency_seconds > 10.0 or force_spi_reject_code == SpiRejectReasonCode.AB03:
            return self._handle_rejection(
                margin_call=mc,
                collateral_acc=collateral_acc,
                reject_code=SpiRejectReasonCode.AB03,
                reject_msg=f"Timeout operacional SPI: latência de {simulated_latency_seconds:.2f}s excedeu o SLA de 10.0s.",
                payer_ispb=mandate.payer_ispb,
                e2e_id=e2e_id,
                mandate_id=mandate.mandate_id,
                latency_sec=simulated_latency_seconds
            )

        # 7. Forçamento voluntário de rejeição para testes
        if force_spi_reject_code:
            return self._handle_rejection(
                margin_call=mc,
                collateral_acc=collateral_acc,
                reject_code=force_spi_reject_code,
                reject_msg=f"Rejeição forçada no motor SPI: {force_spi_reject_code.value}.",
                payer_ispb=mandate.payer_ispb,
                e2e_id=e2e_id,
                mandate_id=mandate.mandate_id,
                latency_sec=simulated_latency_seconds
            )

        # 8. Criação da Mensagem ISO 20022 pacs.008
        pacs008_msg_id = f"MSG-PACS008-{e2e_id[-11:]}"
        pacs008 = Pacs008Message(
            msg_id=pacs008_msg_id,
            e2e_id=e2e_id,
            mandate_id=mandate.mandate_id,
            amount=mc.deficit_amount,
            debtor_tax_id=mandate.payer_tax_id,
            debtor_name=mandate.payer_name,
            debtor_ispb=mandate.payer_ispb,
            debtor_account=mandate.payer_account_number,
            creditor_tax_id=mandate.receiver_tax_id,
            creditor_name=mandate.receiver_name,
            creditor_ispb=mandate.receiver_ispb,
            creditor_account=mandate.receiver_account,
            payment_purpose="B3_INTRADAY_MARGIN_CALL",
            created_at=now,
        )
        self.pacs008_messages[pacs008_msg_id] = pacs008
        mc.status = MarginCallStatus.PENDING_SETTLEMENT

        # 9. Execução atômica no SPI nas Contas PI do BACEN
        payer_pi = self.get_pi_account(mandate.payer_ispb)
        receiver_pi = self.get_pi_account(mandate.receiver_ispb)

        # Verificação de saldo e limite na Conta PI
        if payer_pi.total_liquidity < mc.deficit_amount:
            return self._handle_rejection(
                margin_call=mc,
                collateral_acc=collateral_acc,
                reject_code=SpiRejectReasonCode.AM04,
                reject_msg=(
                    f"Saldo insuficiente na Conta PI {payer_pi.account_number}: "
                    f"disponível R$ {payer_pi.total_liquidity}, necessário R$ {mc.deficit_amount}."
                ),
                payer_ispb=mandate.payer_ispb,
                e2e_id=e2e_id,
                mandate_id=mandate.mandate_id,
                latency_sec=simulated_latency_seconds
            )

        # Débito no pagador e Crédito na B3 (SOMA-ZERO EXATA)
        payer_pi.debit(mc.deficit_amount)
        receiver_pi.credit(mc.deficit_amount)

        # 10. Atualiza Mandato
        mandate.apply_utilization(mc.deficit_amount)

        # 11. Emissão de pacs.002 ACSC
        settle_time = datetime.now(timezone.utc)
        pacs002_msg_id = f"MSG-PACS002-{e2e_id[-11:]}"
        pacs002 = Pacs002Message(
            msg_id=pacs002_msg_id,
            original_msg_id=pacs008_msg_id,
            original_e2e_id=e2e_id,
            status=SpiPaymentStatus.ACSC,
            reason_code=None,
            reason_description=None,
            settlement_timestamp=settle_time,
            latency_ms=simulated_latency_seconds * 1000.0,
        )
        self.pacs002_messages[pacs002_msg_id] = pacs002

        # 12. Aporte de Garantia B3 e Liberação de Capacidade Pré-Trade D0
        collateral_acc.credit_settled_collateral(mc.deficit_amount)

        # 13. Conclusão da Chamada de Margem
        mc.status = MarginCallStatus.SETTLED
        mc.settlement_e2e_id = e2e_id
        mc.settled_amount = mc.deficit_amount
        mc.settled_timestamp = settle_time
        mc.failure_reason = None
        mc.default_triggered = False

        receipt_id = f"REC-PIX-B3-{e2e_id[-11:]}"
        receipt = PixAutomaticoSettlementReceipt(
            receipt_id=receipt_id,
            margin_call_id=mc.margin_call_id,
            e2e_id=e2e_id,
            mandate_id=mandate.mandate_id,
            payer_tax_id=mandate.payer_tax_id,
            payer_ispb=mandate.payer_ispb,
            receiver_ispb=mandate.receiver_ispb,
            amount=mc.deficit_amount,
            status=SpiPaymentStatus.ACSC,
            settlement_timestamp=settle_time,
            latency_seconds=simulated_latency_seconds,
            sla_met=simulated_latency_seconds <= 10.0,
            pretrade_capacity_restored=mc.deficit_amount,
            risk_status_after="NORMAL" if collateral_acc.margin_deficit == Decimal("0.00") else "WARNING",
            idempotent_replay=False,
            reject_code=None,
            reject_reason=None,
        )

        self.settlement_receipts[e2e_id] = receipt
        return receipt

    def _handle_rejection(
        self,
        margin_call: IntradayMarginCall,
        collateral_acc: B3ParticipantCollateralAccount,
        reject_code: SpiRejectReasonCode,
        reject_msg: str,
        payer_ispb: str,
        e2e_id: str,
        mandate_id: str,
        latency_sec: float
    ) -> PixAutomaticoSettlementReceipt:
        """Processa a rejeição no SPI e aciona default imediato conforme regulamento da B3."""
        now = datetime.now(timezone.utc)
        margin_call.status = MarginCallStatus.FAILED
        margin_call.default_triggered = True
        margin_call.failure_reason = f"[{reject_code.value}] {reject_msg}"
        margin_call.settlement_e2e_id = e2e_id

        # Aciona inadimplência e bloqueio de trading imediato na conta B3
        collateral_acc.trigger_default(margin_call.failure_reason)
        margin_call.status = MarginCallStatus.DEFAULTED

        pacs002_msg_id = f"MSG-PACS002-RJCT-{e2e_id[-11:]}"
        pacs002 = Pacs002Message(
            msg_id=pacs002_msg_id,
            original_msg_id=f"MSG-PACS008-{e2e_id[-11:]}",
            original_e2e_id=e2e_id,
            status=SpiPaymentStatus.RJCT,
            reason_code=reject_code,
            reason_description=reject_msg,
            settlement_timestamp=now,
            latency_ms=latency_sec * 1000.0,
        )
        self.pacs002_messages[pacs002_msg_id] = pacs002

        receipt_id = f"REC-RJCT-B3-{e2e_id[-11:]}"
        receipt = PixAutomaticoSettlementReceipt(
            receipt_id=receipt_id,
            margin_call_id=margin_call.margin_call_id,
            e2e_id=e2e_id,
            mandate_id=mandate_id,
            payer_tax_id=margin_call.payer_tax_id,
            payer_ispb=payer_ispb,
            receiver_ispb=self.ispb_camara,
            amount=Decimal("0.00"),
            status=SpiPaymentStatus.RJCT,
            settlement_timestamp=now,
            latency_seconds=latency_sec,
            sla_met=latency_sec <= 10.0,
            pretrade_capacity_restored=Decimal("0.00"),
            risk_status_after="DEFAULTED",
            idempotent_replay=False,
            reject_code=reject_code.value,
            reject_reason=reject_msg,
        )
        self.settlement_receipts[e2e_id] = receipt
        return receipt

    # -----------------------------------------------------------------------
    # Verificação Rigorosa de Invariantes Formais
    # -----------------------------------------------------------------------

    def verify_invariants(self, raise_on_error: bool = False) -> Dict[str, bool]:
        """
        Audita formalmente todas as leis de conservação matemática e regras regulatórias:
        
        1. spi_financial_conservation:
           Total debitado dos pagadores == Total creditado na B3 (Soma-Zero centavo a centavo).
           Para toda conta PI: saldo == inicial - débitos + créditos.
           Soma dos saldos PI do sistema == Soma dos saldos iniciais PI.
        2. e2e_id_uniqueness_and_format:
           Cada EndToEndId é rigorosamente único e cumpre a máscara BACEN de 32 posições.
        3. irrevocability_and_non_repudiation:
           Toda liquidação ACSC possui pacs.002 emitido e recibo correspondente,
           sendo estritamente irrevogável.
        4. risk_capacity_consistency:
           Toda margem liberada ou colateral creditado na B3 equivale estritamente
           aos valores das liquidações ACSC confirmadas no SPI.
        5. mandate_compliance_integrity:
           Todo débito Pix Automático decorre de mandato ativo e respeita
           estritamente max_limit_per_call e total_monthly_limit.
        6. sla_latency_compliance:
           Nenhuma operação liquidada com sucesso excede o SLA de 10 segundos.
        """
        results: Dict[str, bool] = {}

        try:
            # 1. Invariante de Conservação Financeira SPI
            total_debited_payers = Decimal("0.00")
            for ispb, acc in self.pi_accounts.items():
                # Validação individual da conta
                check(
                    acc.verify_conservation(),
                    f"Conta PI {ispb} violou conservação interna: saldo {acc.balance} != "
                    f"calculado {acc.initial_balance - acc.total_debited + acc.total_credited}",
                    law_code="LAW-SPI-001"
                )
                if ispb != self.ispb_camara:
                    total_debited_payers += acc.total_debited

            total_credited_b3 = self.pi_b3.total_credited
            check(
                total_debited_payers == total_credited_b3,
                f"Soma-zero SPI violada: Total Débito Pagadores R$ {total_debited_payers} != "
                f"Total Crédito B3 R$ {total_credited_b3}",
                law_code="LAW-SPI-CONSERVATION"
            )

            # Conservação global do sistema PI
            total_current_pi = sum(acc.balance for acc in self.pi_accounts.values())
            total_initial_pi = sum(acc.initial_balance for acc in self.pi_accounts.values())
            check(
                total_current_pi == total_initial_pi,
                f"Balanço global do SPI desbalanceado: Atual R$ {total_current_pi} != Inicial R$ {total_initial_pi}",
                law_code="LAW-SPI-GLOBAL-BALANCE"
            )
            results["spi_financial_conservation"] = True

            # 2. Invariante de Unicidade e Formato de E2EID
            seen_e2es = set()
            for e2e in self.settlement_receipts.keys():
                validate_end_to_end_id(e2e)
                check(e2e not in seen_e2es, f"Colisão detectada para E2EID '{e2e}'", law_code="LAW-E2E-UNIQUE")
                seen_e2es.add(e2e)
            results["e2e_id_uniqueness_and_format"] = True

            # 3. Invariante de Irrevogabilidade e Não-Repúdio
            for e2e, receipt in self.settlement_receipts.items():
                if receipt.status == SpiPaymentStatus.ACSC:
                    # Deve existir mensagem pacs.002 com status ACSC
                    matching_pacs002 = [
                        p for p in self.pacs002_messages.values()
                        if p.original_e2e_id == e2e and p.status == SpiPaymentStatus.ACSC
                    ]
                    check(
                        len(matching_pacs002) == 1,
                        f"Liquidação ACSC para E2EID '{e2e}' sem comprovação pacs.002 indelével no SPI.",
                        law_code="LAW-SPI-IRREVOCABLE"
                    )
            results["irrevocability_and_non_repudiation"] = True

            # 4. Invariante de Consistência de Capacidade de Risco
            total_settled_calls = sum(
                mc.settled_amount for mc in self.margin_calls.values()
                if mc.status == MarginCallStatus.SETTLED
            )
            total_collateral_credited = sum(
                acc.cash_collateral_balance for acc in self.collateral_accounts.values()
            )
            # Todo valor liquidado deve ter sido creditado na B3
            check(
                total_settled_calls == total_credited_b3,
                f"Divergência entre chamadas liquidadas R$ {total_settled_calls} e crédito B3 R$ {total_credited_b3}",
                law_code="LAW-RISK-CAPACITY-CONSISTENCY"
            )
            results["risk_capacity_consistency"] = True

            # 5. Invariante de Conformidade de Mandato
            for mc in self.margin_calls.values():
                if mc.status == MarginCallStatus.SETTLED:
                    check(
                        mc.mandate_id is not None and mc.mandate_id in self.mandates,
                        f"Chamada de margem {mc.margin_call_id} liquidou sem mandato cadastrado.",
                        law_code="LAW-MANDATE-INTEGRITY"
                    )
                    m = self.mandates[mc.mandate_id]
                    check(
                        mc.settled_amount <= m.max_limit_per_call,
                        f"Chamada liquidada R$ {mc.settled_amount} excedeu teto por chamada R$ {m.max_limit_per_call}",
                        law_code="LAW-MANDATE-LIMIT"
                    )
                    check(
                        m.current_monthly_utilized <= m.total_monthly_limit,
                        f"Mandato {m.mandate_id} excedeu limite mensal de débito recorrente.",
                        law_code="LAW-MANDATE-MONTHLY-LIMIT"
                    )
            results["mandate_compliance_integrity"] = True

            # 6. Invariante de Cumprimento do SLA de Latência (< 10 segundos)
            for receipt in self.settlement_receipts.values():
                if receipt.status == SpiPaymentStatus.ACSC:
                    check(
                        receipt.latency_seconds <= 10.0,
                        f"Recibo {receipt.receipt_id} liquidou com latência de {receipt.latency_seconds}s > 10.0s.",
                        law_code="LAW-SPI-SLA-10S"
                    )
            results["sla_latency_compliance"] = True

        except InvariantViolationError as err:
            if raise_on_error:
                raise PixAutomaticoInvariantViolationError(str(err)) from err
            results["all_satisfied"] = False
            return results

        results["all_satisfied"] = all(results.values())
        return results

    # -----------------------------------------------------------------------
    # Serialização de Estado e Carga de Fixture
    # -----------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado operacional integral do motor em dicionário."""
        return {
            "version": "1.0.0",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "scope": "FL-090 Pix Automático Intraday Margin Call Settlement Engine",
            "ispb_camara": self.ispb_camara,
            "camara_account_number": self.camara_account_number,
            "pi_accounts": [acc.to_dict() for acc in self.pi_accounts.values()],
            "dict_keys": [dk.to_dict() for dk in self.dict_registry.values()],
            "mandates": [m.to_dict() for m in self.mandates.values()],
            "collateral_accounts": [ca.to_dict() for ca in self.collateral_accounts.values()],
            "margin_calls": [mc.to_dict() for mc in self.margin_calls.values()],
            "settlement_receipts": [sr.to_dict() for sr in self.settlement_receipts.values()],
            "invariants": self.verify_invariants(raise_on_error=False),
        }

    def load_fixture(self, file_path: Union[str, Path]) -> None:
        """Carrega configuração inicial a partir de um arquivo JSON fixture."""
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"Arquivo de fixture '{file_path}' não encontrado.")
        
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "pi_accounts" in data:
            for item in data["pi_accounts"]:
                acc = SpiPiAccount(
                    ispb=item["ispb"],
                    account_number=item["account_number"],
                    participant_name=item["participant_name"],
                    balance=Decimal(str(item["balance"])),
                    available_limit=Decimal(str(item.get("available_limit", "0.00"))),
                )
                self.register_pi_account(acc)

        if "dict_keys" in data:
            for item in data["dict_keys"]:
                self.register_dict_key(
                    pix_key=item["pix_key"],
                    key_type=PixKeyType(item["key_type"]),
                    tax_id=item["tax_id"],
                    name=item["name"],
                    ispb=item["ispb"],
                    branch=item["branch"],
                    account_number=item["account_number"],
                    account_type=item.get("account_type", "CACC"),
                    is_active=item.get("is_active", True),
                )

        if "mandates" in data:
            for item in data["mandates"]:
                m = PixAutomaticoMandate(
                    mandate_id=item["mandate_id"],
                    payer_tax_id=item["payer_tax_id"],
                    payer_name=item["payer_name"],
                    payer_ispb=item["payer_ispb"],
                    payer_branch=item["payer_branch"],
                    payer_account_number=item["payer_account_number"],
                    payer_pix_key=item.get("payer_pix_key"),
                    receiver_tax_id=item.get("receiver_tax_id", TAX_ID_B3_DEFAULT),
                    receiver_name=item.get("receiver_name", B3_NAME_DEFAULT),
                    receiver_ispb=item.get("receiver_ispb", ISPB_B3_DEFAULT),
                    receiver_pix_key=item.get("receiver_pix_key", TAX_ID_B3_DEFAULT),
                    receiver_account=item.get("receiver_account", B3_CLEARING_ACCOUNT_DEFAULT),
                    max_limit_per_call=Decimal(str(item["max_limit_per_call"])),
                    total_monthly_limit=Decimal(str(item["total_monthly_limit"])),
                    current_monthly_utilized=Decimal(str(item.get("current_monthly_utilized", "0.00"))),
                    status=MandateStatus(item.get("status", "ACTIVE")),
                    authorization_channel=AuthorizationChannel(item.get("authorization_channel", "OPEN_FINANCE")),
                    recurrence_purpose=RecurrencePurpose(item.get("recurrence_purpose", "INTRADAY_MARGIN_CALL")),
                )
                self.register_mandate(m)

        if "collateral_accounts" in data:
            for item in data["collateral_accounts"]:
                ca = B3ParticipantCollateralAccount(
                    participant_id=item["participant_id"],
                    subaccount_id=item["subaccount_id"],
                    tax_id=item["tax_id"],
                    cash_collateral_balance=Decimal(str(item.get("cash_collateral_balance", "0.00"))),
                    required_margin=Decimal(str(item.get("required_margin", "0.00"))),
                    pretrade_risk_limit=Decimal(str(item.get("pretrade_risk_limit", "50000000.00"))),
                    pretrade_capacity_utilized=Decimal(str(item.get("pretrade_capacity_utilized", "0.00"))),
                    is_blocked_for_trading=item.get("is_blocked_for_trading", False),
                    is_in_default=item.get("is_in_default", False),
                )
                self.register_collateral_account(ca)
