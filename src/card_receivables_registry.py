"""Sistema de Registro e Gravame de Recebíveis de Cartão de Crédito no Balcão NoMe B3 (FL-069).

Implementa a infraestrutura regulatória para Unidades de Recebíveis (UR),
registro de operações por credenciadoras e arranjos de pagamento (Visa, Mastercard, Elo),
gestão de consentimento (opt-in de lojistas), constituição e baixa de gravames fiduciários
e penhores para garantia de crédito bancário, liquidação de antecipações intra e extra-arranjo,
e liquidação multilateral no vencimento com regras de trava de domicílio bancário,
em estrita conformidade com a Resolução CMN nº 4.734/2019, Circular BCB nº 3.952/2019
e Resolução BCB nº 264/2022.

Leis de Invariância e Conservação:
1. Lei da Unicidade de Gravame: Cada fração de UR é indivisível e única como lastro.
   A soma dos gravames ativos não pode exceder o valor corrente da UR (V_comprometido <= V_atual).
2. Lei da Conservação Contábil de UR:
   - Em todo instante t: V_atual(t) = V_comprometido(t) + V_disponivel(t).
   - Na antecipação: V_antecipado = V_liquido + V_taxa_desconto; V_novo = V_anterior - V_antecipado.
   - Na liquidação: V_liquidado_total = Somatorio(V_credores_gravame) + V_livre_lojista = V_atual.
   - Nenhuma parcela onerada pode sofrer antecipação sem desoneração prévia.

Zero dependências externas (100% biblioteca padrão Python).
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

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")


def _quantize_money(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda valores monetários em BRL para 2 casas decimais (ROUND_HALF_UP)."""
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _quantize_rate(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda taxas e percentuais para 4 casas decimais (ROUND_HALF_UP)."""
    return Decimal(str(value)).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def _parse_iso_date(value: Union[str, date]) -> str:
    """Valida e normaliza data no formato YYYY-MM-DD."""
    if isinstance(value, date):
        return value.isoformat()
    cleaned = value.strip()
    # Verifica formato estrito YYYY-MM-DD
    parts = cleaned.split("-")
    if len(parts) != 3 or len(parts[0]) != 4 or len(parts[1]) != 2 or len(parts[2]) != 2:
        raise ValueError(f"Data inválida: {value}. Esperado YYYY-MM-DD.")
    return cleaned


def _clean_doc(doc: str) -> str:
    """Remove caracteres não numéricos de CPF ou CNPJ."""
    return re.sub(r"\D", "", str(doc))


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------


class PaymentScheme(str, Enum):
    """Arranjos de pagamento autorizados pelo Banco Central do Brasil."""
    VISA_CREDITO_A_VISTA = "VISA_CREDITO_A_VISTA"
    VISA_CREDITO_PARCELADO = "VISA_CREDITO_PARCELADO"
    VISA_DEBITO = "VISA_DEBITO"
    MASTERCARD_CREDITO_A_VISTA = "MASTERCARD_CREDITO_A_VISTA"
    MASTERCARD_CREDITO_PARCELADO = "MASTERCARD_CREDITO_PARCELADO"
    MASTERCARD_DEBITO = "MASTERCARD_DEBITO"
    ELO_CREDITO_A_VISTA = "ELO_CREDITO_A_VISTA"
    ELO_CREDITO_PARCELADO = "ELO_CREDITO_PARCELADO"
    ELO_DEBITO = "ELO_DEBITO"


class LienModality(str, Enum):
    """Modalidades de gravames e ônus reais sobre Unidades de Recebíveis."""
    CESSAO_FIDUCIARIA = "CESSAO_FIDUCIARIA"          # Garantia fiduciária de crédito bancário
    PENHOR = "PENHOR"                                # Penhor mercantil de direitos creditórios
    CESSAO_DEFINITIVA = "CESSAO_DEFINITIVA"          # Venda/aquisição definitiva (FIDC/bancos)
    BLOQUEIO_CAUTELAR = "BLOQUEIO_CAUTELAR"          # Constrição judicial ou bloqueio regulatório


class UrStatus(str, Enum):
    """Ciclo de vida e estados operacionais da Unidade de Recebíveis no NoMe B3."""
    ATIVA = "ATIVA"                              # Ativa com saldo livre para negociação
    TOTALMENTE_ONERADA = "TOTALMENTE_ONERADA"    # 100% do saldo comprometido por gravames
    LIQUIDADA = "LIQUIDADA"                      # Totalmente liquidada no vencimento ou antecipada
    CANCELADA = "CANCELADA"                      # Estorno integral / cancelamento


class LienStatus(str, Enum):
    """Situação registral do gravame no NoMe B3."""
    ATIVO = "ATIVO"          # Gravame plenamente constituído e vigente
    ADITADO = "ADITADO"      # Gravame com valor ou termos contratuais alterados
    BAIXADO = "BAIXADO"      # Desconstituído por quitação da dívida
    EXECUTADO = "EXECUTADO"  # Liquidado financeiramente na data de vencimento da UR


class SettlementChannel(str, Enum):
    """Mecanismos de liquidação financeira."""
    STR_SPB = "STR_SPB"              # Sistema de Transferência de Reservas (TED / LDL)
    PIX = "PIX"                      # Arranjo Pix do Banco Central
    CREDITO_CONTA = "CREDITO_CONTA"  # Crédito em conta intra-bancário


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------


class CardReceivablesError(Exception):
    """Exceção base do sistema de recebíveis de cartão (FL-069)."""


class ReceivableUnitNotFoundError(CardReceivablesError):
    """Lançada quando a Unidade de Recebíveis não é encontrada."""


class DuplicateReceivableUnitError(CardReceivablesError):
    """Lançada ao tentar registrar UR duplicada fora das regras de acúmulo."""


class DuplicateLienError(CardReceivablesError):
    """Lançada ao tentar registrar gravame com protocolo já existente."""


class LienNotFoundError(CardReceivablesError):
    """Lançada quando o protocolo de gravame não existe no registro."""


class InsufficientReceivableCapacityError(CardReceivablesError):
    """Lançada quando o gravame pretendido excede o valor disponível da UR."""


class EncumberedReceivableAnticipationError(CardReceivablesError):
    """Lançada ao tentar antecipar valor de UR onerada sem saldo livre suficiente."""


class UnauthorizedConsentError(CardReceivablesError):
    """Lançada quando a instituição consulta ou onera recebíveis sem consentimento do lojista."""


class ReceivableConservationError(CardReceivablesError):
    """Lançada em caso de violação de invariante ou conservação contábil."""


class InvalidReceivableOperationError(CardReceivablesError):
    """Lançada para operações em desacordo com o ciclo de vida da UR."""


# ---------------------------------------------------------------------------
# Estruturas de Dados
# ---------------------------------------------------------------------------


@dataclass
class BankAccount:
    """Domicílio bancário para liquidação financeira de recebíveis de cartão."""
    ispb: str
    bank_name: str
    branch: str
    account_number: str
    account_type: str = "CONTA_CORRENTE"  # CONTA_CORRENTE, CONTA_PAGAMENTO, CONTA_POUPANCA
    holder_document: str = ""

    def __post_init__(self) -> None:
        self.ispb = _clean_doc(self.ispb).zfill(8)
        self.branch = self.branch.strip()
        self.account_number = self.account_number.strip()
        self.holder_document = _clean_doc(self.holder_document)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BankAccount:
        return cls(
            ispb=str(data.get("ispb", "")),
            bank_name=str(data.get("bank_name", "")),
            branch=str(data.get("branch", "")),
            account_number=str(data.get("account_number", "")),
            account_type=str(data.get("account_type", "CONTA_CORRENTE")),
            holder_document=str(data.get("holder_document", "")),
        )


@dataclass
class LienRecord:
    """Registro individual de gravame ou ônus sobre uma Unidade de Recebíveis."""
    lien_id: str
    ur_id: str
    creditor_ispb: str
    creditor_name: str
    contract_number: str
    modality: LienModality
    encumbered_amount: Decimal
    priority_rank: int
    beneficiary_bank_account: BankAccount
    status: LienStatus = LienStatus.ATIVO
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    released_at: Optional[str] = None

    def __post_init__(self) -> None:
        self.encumbered_amount = _quantize_money(self.encumbered_amount)
        if isinstance(self.modality, str):
            self.modality = LienModality(self.modality)
        if isinstance(self.status, str):
            self.status = LienStatus(self.status)
        if isinstance(self.beneficiary_bank_account, dict):
            self.beneficiary_bank_account = BankAccount.from_dict(self.beneficiary_bank_account)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lien_id": self.lien_id,
            "ur_id": self.ur_id,
            "creditor_ispb": self.creditor_ispb,
            "creditor_name": self.creditor_name,
            "contract_number": self.contract_number,
            "modality": self.modality.value,
            "encumbered_amount": str(self.encumbered_amount),
            "priority_rank": self.priority_rank,
            "beneficiary_bank_account": self.beneficiary_bank_account.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "released_at": self.released_at,
        }


@dataclass
class AnticipationRecord:
    """Registro de operação de pré-pagamento / antecipação de recebíveis."""
    anticipation_id: str
    ur_id: str
    requested_face_amount: Decimal
    discount_rate: Decimal
    discount_fee: Decimal
    net_disbursed_amount: Decimal
    anticipator_code: str
    settlement_channel: SettlementChannel
    recipient_account: BankAccount
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    conservation_held: bool = True

    def __post_init__(self) -> None:
        self.requested_face_amount = _quantize_money(self.requested_face_amount)
        self.discount_rate = _quantize_rate(self.discount_rate)
        self.discount_fee = _quantize_money(self.discount_fee)
        self.net_disbursed_amount = _quantize_money(self.net_disbursed_amount)
        if isinstance(self.settlement_channel, str):
            self.settlement_channel = SettlementChannel(self.settlement_channel)
        if isinstance(self.recipient_account, dict):
            self.recipient_account = BankAccount.from_dict(self.recipient_account)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "anticipation_id": self.anticipation_id,
            "ur_id": self.ur_id,
            "requested_face_amount": str(self.requested_face_amount),
            "discount_rate": str(self.discount_rate),
            "discount_fee": str(self.discount_fee),
            "net_disbursed_amount": str(self.net_disbursed_amount),
            "anticipator_code": self.anticipator_code,
            "settlement_channel": self.settlement_channel.value,
            "recipient_account": self.recipient_account.to_dict(),
            "timestamp": self.timestamp,
            "conservation_held": self.conservation_held,
        }


@dataclass
class SettlementPayment:
    """Parcela de liquidação financeira direcionada a credor garantido ou lojista."""
    recipient_type: str  # CREDITOR_LIEN ou MERCHANT_FREE_BALANCE
    recipient_name: str
    recipient_document: str
    amount: Decimal
    destination_account: BankAccount
    lien_id: Optional[str] = None
    priority_rank: Optional[int] = None
    payment_reference: str = ""

    def __post_init__(self) -> None:
        self.amount = _quantize_money(self.amount)
        if isinstance(self.destination_account, dict):
            self.destination_account = BankAccount.from_dict(self.destination_account)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recipient_type": self.recipient_type,
            "recipient_name": self.recipient_name,
            "recipient_document": self.recipient_document,
            "amount": str(self.amount),
            "destination_account": self.destination_account.to_dict(),
            "lien_id": self.lien_id,
            "priority_rank": self.priority_rank,
            "payment_reference": self.payment_reference,
        }


@dataclass
class UrSettlementReceipt:
    """Comprovante de liquidação financeira da UR na data prevista."""
    receipt_id: str
    ur_id: str
    settlement_date: str
    total_settled_amount: Decimal
    total_creditor_payments: Decimal
    total_merchant_payments: Decimal
    payments: List[SettlementPayment]
    invariant_conservation_held: bool
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "ur_id": self.ur_id,
            "settlement_date": self.settlement_date,
            "total_settled_amount": str(self.total_settled_amount),
            "total_creditor_payments": str(self.total_creditor_payments),
            "total_merchant_payments": str(self.total_merchant_payments),
            "payments": [p.to_dict() for p in self.payments],
            "invariant_conservation_held": self.invariant_conservation_held,
            "timestamp": self.timestamp,
        }


@dataclass
class ReceivableUnit:
    """Unidade de Recebíveis (UR) conforme Resolução CMN nº 4.734 e Circular BCB nº 3.952.
    
    Identificada pela quádrupla canônica:
    (merchant_document, acquirer_code, payment_scheme, settlement_date)
    """
    ur_id: str
    merchant_document: str
    merchant_name: str
    acquirer_code: str
    acquirer_cnpj: str
    payment_scheme: PaymentScheme
    settlement_date: str
    original_amount: Decimal
    current_amount: Decimal
    committed_amount: Decimal = Decimal("0.00")
    available_amount: Decimal = Decimal("0.00")
    settled_amount: Decimal = Decimal("0.00")
    default_merchant_account: Optional[BankAccount] = None
    status: UrStatus = UrStatus.ATIVA
    liens: Dict[str, LienRecord] = field(default_factory=dict)
    anticipations: List[AnticipationRecord] = field(default_factory=list)
    audit_trail: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        self.merchant_document = _clean_doc(self.merchant_document)
        self.acquirer_cnpj = _clean_doc(self.acquirer_cnpj)
        self.settlement_date = _parse_iso_date(self.settlement_date)
        self.original_amount = _quantize_money(self.original_amount)
        self.current_amount = _quantize_money(self.current_amount)
        self.committed_amount = _quantize_money(self.committed_amount)
        self.available_amount = _quantize_money(self.available_amount)
        self.settled_amount = _quantize_money(self.settled_amount)

        if isinstance(self.payment_scheme, str):
            self.payment_scheme = PaymentScheme(self.payment_scheme)
        if isinstance(self.status, str):
            self.status = UrStatus(self.status)
        if isinstance(self.default_merchant_account, dict):
            self.default_merchant_account = BankAccount.from_dict(self.default_merchant_account)

        # Se available_amount não foi calculado na carga, sincroniza
        calculated_avail = self.current_amount - self.committed_amount
        if self.available_amount != calculated_avail:
            self.available_amount = calculated_avail

        self._refresh_status()

    def _refresh_status(self) -> None:
        """Atualiza estado operacional da UR garantindo conformidade com regras contábeis."""
        if self.current_amount == Decimal("0.00") and self.settled_amount > Decimal("0.00"):
            self.status = UrStatus.LIQUIDADA
        elif self.available_amount == Decimal("0.00") and self.committed_amount > Decimal("0.00"):
            self.status = UrStatus.TOTALMENTE_ONERADA
        elif self.current_amount > Decimal("0.00"):
            self.status = UrStatus.ATIVA

    @classmethod
    def generate_canonical_id(
        cls,
        merchant_document: str,
        acquirer_code: str,
        payment_scheme: Union[PaymentScheme, str],
        settlement_date: str
    ) -> str:
        """Gera chave unívoca determinística padronizada da UR."""
        doc = _clean_doc(merchant_document)
        acq = acquirer_code.strip().upper()
        scheme_str = payment_scheme.value if isinstance(payment_scheme, PaymentScheme) else str(payment_scheme)
        dt = _parse_iso_date(settlement_date).replace("-", "")
        # Sufixo hash curto para segurança e legibilidade
        raw = f"{doc}:{acq}:{scheme_str}:{dt}"
        sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8].upper()
        return f"UR-{dt}-{acq}-{doc[-6:]}-{sha}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ur_id": self.ur_id,
            "merchant_document": self.merchant_document,
            "merchant_name": self.merchant_name,
            "acquirer_code": self.acquirer_code,
            "acquirer_cnpj": self.acquirer_cnpj,
            "payment_scheme": self.payment_scheme.value,
            "settlement_date": self.settlement_date,
            "original_amount": str(self.original_amount),
            "current_amount": str(self.current_amount),
            "committed_amount": str(self.committed_amount),
            "available_amount": str(self.available_amount),
            "settled_amount": str(self.settled_amount),
            "default_merchant_account": self.default_merchant_account.to_dict() if self.default_merchant_account else None,
            "status": self.status.value,
            "liens": {lid: l.to_dict() for lid, l in self.liens.items()},
            "anticipations": [a.to_dict() for a in self.anticipations],
            "audit_trail": self.audit_trail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# Motor do Registro: CardReceivablesRegistryEngine
# ---------------------------------------------------------------------------


class CardReceivablesRegistryEngine:
    """Motor central do Sistema de Registro e Gravame de Recebíveis de Cartão (FL-069).
    
    Implementa as regras de negócios da Resolução CMN nº 4.734 e Circular BCB nº 3.952:
    - Agrupamento em Unidades de Recebíveis (UR)
    - Consentimento ativo (opt-in) de lojistas
    - Lei de unicidade e não-sobreposição de gravame
    - Antecipação restrita à capacidade livre
    - Liquidação com trava fiduciária de domicílio bancário
    """

    def __init__(self) -> None:
        self.urs: Dict[str, ReceivableUnit] = {}
        # Mapeamento canônico: (merchant_doc, acquirer_code, scheme_value, settlement_date) -> ur_id
        self._canonical_index: Dict[Tuple[str, str, str, str], str] = {}
        self.merchants: Dict[str, Dict[str, Any]] = {}
        self.acquirers: Dict[str, Dict[str, Any]] = {}
        # Consentimentos: merchant_doc -> Dict[creditor_ispb, Dict[str, Any]]
        self.consents: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.settlement_receipts: Dict[str, UrSettlementReceipt] = {}

    # -----------------------------------------------------------------------
    # Cadastro de Participantes e Consentimento (Opt-in)
    # -----------------------------------------------------------------------

    def register_merchant(
        self,
        document: str,
        name: str,
        default_account: BankAccount
    ) -> None:
        """Cadastra estabelecimento comercial (Usuário Recebedor)."""
        doc = _clean_doc(document)
        self.merchants[doc] = {
            "document": doc,
            "name": name.strip(),
            "default_account": default_account,
        }

    def register_acquirer(
        self,
        acquirer_code: str,
        name: str,
        cnpj: str
    ) -> None:
        """Cadastra instituição credenciadora ou subcredenciadora."""
        code = acquirer_code.strip().upper()
        self.acquirers[code] = {
            "code": code,
            "name": name.strip(),
            "cnpj": _clean_doc(cnpj),
        }

    def grant_consent(
        self,
        merchant_document: str,
        creditor_ispb: str,
        creditor_name: str,
        expiration_date: str
    ) -> None:
        """Registra consentimento (opt-in) do lojista autorizando a IF a consultar e onerar URs."""
        doc = _clean_doc(merchant_document)
        ispb = _clean_doc(creditor_ispb).zfill(8)
        exp = _parse_iso_date(expiration_date)
        if doc not in self.consents:
            self.consents[doc] = {}
        self.consents[doc][ispb] = {
            "creditor_ispb": ispb,
            "creditor_name": creditor_name.strip(),
            "granted_at": datetime.now(timezone.utc).isoformat(),
            "expiration_date": exp,
            "active": True,
        }

    def revoke_consent(self, merchant_document: str, creditor_ispb: str) -> None:
        """Revoga consentimento de consulta e garantia da IF."""
        doc = _clean_doc(merchant_document)
        ispb = _clean_doc(creditor_ispb).zfill(8)
        if doc in self.consents and ispb in self.consents[doc]:
            self.consents[doc][ispb]["active"] = False

    def check_consent(self, merchant_document: str, creditor_ispb: str) -> bool:
        """Verifica se há consentimento ativo e não expirado para a IF."""
        doc = _clean_doc(merchant_document)
        ispb = _clean_doc(creditor_ispb).zfill(8)
        if doc not in self.consents or ispb not in self.consents[doc]:
            return False
        c = self.consents[doc][ispb]
        if not c.get("active", False):
            return False
        # Verifica se expirou
        today_str = datetime.now(timezone.utc).date().isoformat()
        if c.get("expiration_date", "") < today_str:
            return False
        return True

    # -----------------------------------------------------------------------
    # Registro de Recebíveis de Vendas por Credenciadoras
    # -----------------------------------------------------------------------

    def register_receivable_transaction(
        self,
        merchant_document: str,
        acquirer_code: str,
        payment_scheme: Union[PaymentScheme, str],
        settlement_date: str,
        amount: Union[Decimal, str, int, float],
        transaction_id: str = ""
    ) -> ReceivableUnit:
        """Registra transação de venda de cartão no NoMe B3, agrupando na respectiva UR.
        
        Se a UR da quádrupla canônica já existir e estiver ativa, adiciona o valor ao
        saldo original e vigente, recalculando a capacidade livre.
        Se não existir, instancia uma nova UR.
        """
        doc = _clean_doc(merchant_document)
        acq = acquirer_code.strip().upper()
        scheme = PaymentScheme(payment_scheme) if isinstance(payment_scheme, str) else payment_scheme
        dt = _parse_iso_date(settlement_date)
        amt = _quantize_money(amount)

        if amt <= Decimal("0.00"):
            raise InvalidReceivableOperationError(f"Valor da transação de venda deve ser estritamente positivo: {amt}")

        canon_key = (doc, acq, scheme.value, dt)

        if canon_key in self._canonical_index:
            ur_id = self._canonical_index[canon_key]
            ur = self.urs[ur_id]
            if ur.status == UrStatus.LIQUIDADA or ur.status == UrStatus.CANCELADA:
                raise InvalidReceivableOperationError(
                    f"Não é permitido adicionar vendas a UR em estado {ur.status.value}: {ur_id}"
                )

            # Acumula no saldo vigente e livre
            ur.original_amount = _quantize_money(ur.original_amount + amt)
            ur.current_amount = _quantize_money(ur.current_amount + amt)
            ur.available_amount = _quantize_money(ur.current_amount - ur.committed_amount)
            ur.updated_at = datetime.now(timezone.utc).isoformat()
            ur.audit_trail.append({
                "event": "SALES_INCREMENT",
                "amount": str(amt),
                "transaction_id": transaction_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "resulting_current_amount": str(ur.current_amount),
                "resulting_available_amount": str(ur.available_amount),
            })
            ur._refresh_status()
            self._assert_ur_conservation(ur)
            return ur

        # Cria nova UR
        ur_id = ReceivableUnit.generate_canonical_id(doc, acq, scheme, dt)
        merchant_info = self.merchants.get(doc, {})
        merchant_name = merchant_info.get("name", f"LOJISTA {doc}")
        default_account = merchant_info.get("default_account")

        acq_info = self.acquirers.get(acq, {})
        acq_cnpj = acq_info.get("cnpj", "00000000000000")

        ur = ReceivableUnit(
            ur_id=ur_id,
            merchant_document=doc,
            merchant_name=merchant_name,
            acquirer_code=acq,
            acquirer_cnpj=acq_cnpj,
            payment_scheme=scheme,
            settlement_date=dt,
            original_amount=amt,
            current_amount=amt,
            committed_amount=Decimal("0.00"),
            available_amount=amt,
            settled_amount=Decimal("0.00"),
            default_merchant_account=default_account,
            status=UrStatus.ATIVA,
            audit_trail=[{
                "event": "UR_CONSTITUTED",
                "initial_amount": str(amt),
                "transaction_id": transaction_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }]
        )
        self.urs[ur_id] = ur
        self._canonical_index[canon_key] = ur_id
        self._assert_ur_conservation(ur)
        return ur

    def register_sales_batch(self, batch: Sequence[Dict[str, Any]]) -> List[str]:
        """Processa lote diário de arquivos de vendas submetido por credenciadoras."""
        affected_urs: List[str] = []
        for item in batch:
            ur = self.register_receivable_transaction(
                merchant_document=item["merchant_document"],
                acquirer_code=item["acquirer_code"],
                payment_scheme=item["payment_scheme"],
                settlement_date=item["settlement_date"],
                amount=item["amount"],
                transaction_id=item.get("transaction_id", "")
            )
            if ur.ur_id not in affected_urs:
                affected_urs.append(ur.ur_id)
        return affected_urs

    # -----------------------------------------------------------------------
    # Consulta de Agendas de Recebíveis
    # -----------------------------------------------------------------------

    def query_merchant_urs(
        self,
        merchant_document: str,
        requester_ispb: str
    ) -> List[ReceivableUnit]:
        """Consulta as URs ativas de um lojista com base em consentimento (Circular BCB nº 3.952)."""
        doc = _clean_doc(merchant_document)
        ispb = _clean_doc(requester_ispb).zfill(8)
        if not self.check_consent(doc, ispb):
            raise UnauthorizedConsentError(
                f"Consulta rejeitada: Não há consentimento ativo do lojista {doc} para a IF {ispb}."
            )
        return [ur for ur in self.urs.values() if ur.merchant_document == doc]

    # -----------------------------------------------------------------------
    # Constituição e Gestão de Gravames Fiduciários
    # -----------------------------------------------------------------------

    def constitute_lien(
        self,
        ur_id: str,
        creditor_ispb: str,
        creditor_name: str,
        contract_number: str,
        modality: Union[LienModality, str],
        encumbered_amount: Union[Decimal, str, int, float],
        beneficiary_bank_account: BankAccount,
        priority_rank: int = 1,
        lien_id: Optional[str] = None
    ) -> LienRecord:
        """Constitui gravame sobre uma UR em favor de instituição financeira credora.
        
        Aplica a Lei da Unicidade do Gravame e Vedação ao Sobre-oneramento:
        O valor onerado NÃO pode exceder o valor disponível (saldo livre) da UR:
        encumbered_amount <= available_amount.
        """
        if ur_id not in self.urs:
            raise ReceivableUnitNotFoundError(f"UR não encontrada no registro: {ur_id}")

        ur = self.urs[ur_id]
        ispb = _clean_doc(creditor_ispb).zfill(8)
        amount = _quantize_money(encumbered_amount)

        if amount <= Decimal("0.00"):
            raise InvalidReceivableOperationError(f"Valor do gravame deve ser positivo: {amount}")

        # Valida consentimento regulatório
        if not self.check_consent(ur.merchant_document, ispb):
            raise UnauthorizedConsentError(
                f"Constituição de gravame rejeitada: Lojista {ur.merchant_document} não deu consentimento para IF {ispb}."
            )

        # Regra de ouro da Lei de Unicidade: encumbered_amount <= available_amount
        if amount > ur.available_amount:
            raise InsufficientReceivableCapacityError(
                f"Tentativa de sobre-oneramento: Valor pretendido R$ {amount} excede "
                f"a capacidade livre disponível de R$ {ur.available_amount} na UR {ur_id}."
            )

        if lien_id is None:
            seq = len(ur.liens) + 1
            lien_id = f"GRV-{ur.settlement_date.replace('-', '')}-{seq:03d}-{hashlib.md5(contract_number.encode()).hexdigest()[:6].upper()}"

        if lien_id in ur.liens:
            raise DuplicateLienError(f"Protocolo de gravame já registrado na UR: {lien_id}")

        mod = LienModality(modality) if isinstance(modality, str) else modality

        lien = LienRecord(
            lien_id=lien_id,
            ur_id=ur_id,
            creditor_ispb=ispb,
            creditor_name=creditor_name.strip(),
            contract_number=contract_number.strip(),
            modality=mod,
            encumbered_amount=amount,
            priority_rank=priority_rank,
            beneficiary_bank_account=beneficiary_bank_account,
            status=LienStatus.ATIVO
        )

        # Atualização atômica contábil
        ur.liens[lien_id] = lien
        ur.committed_amount = _quantize_money(ur.committed_amount + amount)
        ur.available_amount = _quantize_money(ur.current_amount - ur.committed_amount)
        ur.updated_at = datetime.now(timezone.utc).isoformat()
        ur.audit_trail.append({
            "event": "LIEN_CONSTITUTED",
            "lien_id": lien_id,
            "creditor": creditor_name,
            "contract": contract_number,
            "amount": str(amount),
            "resulting_committed": str(ur.committed_amount),
            "resulting_available": str(ur.available_amount),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        ur._refresh_status()
        self._assert_ur_conservation(ur)
        return lien

    def amend_lien(
        self,
        ur_id: str,
        lien_id: str,
        new_encumbered_amount: Optional[Union[Decimal, str, int, float]] = None,
        new_beneficiary_account: Optional[BankAccount] = None
    ) -> LienRecord:
        """Adita gravame existente (desoneração parcial ou alteração de conta de liquidação)."""
        if ur_id not in self.urs:
            raise ReceivableUnitNotFoundError(f"UR não encontrada: {ur_id}")
        ur = self.urs[ur_id]
        if lien_id not in ur.liens:
            raise LienNotFoundError(f"Gravame {lien_id} não encontrado na UR {ur_id}")

        lien = ur.liens[lien_id]
        if lien.status != LienStatus.ATIVO and lien.status != LienStatus.ADITADO:
            raise InvalidReceivableOperationError(f"Gravame em estado {lien.status.value} não pode ser aditado.")

        if new_encumbered_amount is not None:
            new_amount = _quantize_money(new_encumbered_amount)
            if new_amount <= Decimal("0.00"):
                raise InvalidReceivableOperationError("Novo valor onerado deve ser estritamente positivo. Para extinguir, use release_lien.")

            delta = new_amount - lien.encumbered_amount
            if delta > Decimal("0.00"):
                # Aumento de gravame: requer saldo disponível
                if delta > ur.available_amount:
                    raise InsufficientReceivableCapacityError(
                        f"Aditamento com aumento de R$ {delta} excede capacidade livre de R$ {ur.available_amount}."
                    )
                ur.committed_amount = _quantize_money(ur.committed_amount + delta)
                ur.available_amount = _quantize_money(ur.current_amount - ur.committed_amount)
            else:
                # Redução de gravame (desoneração parcial)
                reduction = abs(delta)
                ur.committed_amount = _quantize_money(ur.committed_amount - reduction)
                ur.available_amount = _quantize_money(ur.current_amount - ur.committed_amount)

            lien.encumbered_amount = new_amount

        if new_beneficiary_account is not None:
            lien.beneficiary_bank_account = new_beneficiary_account

        lien.status = LienStatus.ADITADO
        lien.updated_at = datetime.now(timezone.utc).isoformat()
        ur.updated_at = datetime.now(timezone.utc).isoformat()
        ur.audit_trail.append({
            "event": "LIEN_AMENDED",
            "lien_id": lien_id,
            "new_amount": str(lien.encumbered_amount),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        ur._refresh_status()
        self._assert_ur_conservation(ur)
        return lien

    def release_lien(
        self,
        ur_id: str,
        lien_id: str,
        reason: str = "DEBT_SETTLED"
    ) -> LienRecord:
        """Desconstitui (baixa) integralmente gravame por quitação da dívida garantida."""
        if ur_id not in self.urs:
            raise ReceivableUnitNotFoundError(f"UR não encontrada: {ur_id}")
        ur = self.urs[ur_id]
        if lien_id not in ur.liens:
            raise LienNotFoundError(f"Gravame {lien_id} não encontrado na UR {ur_id}")

        lien = ur.liens[lien_id]
        if lien.status == LienStatus.BAIXADO or lien.status == LienStatus.EXECUTADO:
            raise InvalidReceivableOperationError(f"Gravame {lien_id} já se encontra {lien.status.value}.")

        # Libera o valor comprometido
        released_amount = lien.encumbered_amount
        ur.committed_amount = _quantize_money(ur.committed_amount - released_amount)
        ur.available_amount = _quantize_money(ur.current_amount - ur.committed_amount)

        lien.status = LienStatus.BAIXADO
        lien.released_at = datetime.now(timezone.utc).isoformat()
        lien.updated_at = datetime.now(timezone.utc).isoformat()

        ur.updated_at = datetime.now(timezone.utc).isoformat()
        ur.audit_trail.append({
            "event": "LIEN_RELEASED",
            "lien_id": lien_id,
            "released_amount": str(released_amount),
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        ur._refresh_status()
        self._assert_ur_conservation(ur)
        return lien

    # -----------------------------------------------------------------------
    # Operações de Pré-pagamento / Antecipação de Recebíveis
    # -----------------------------------------------------------------------

    def anticipate_receivables(
        self,
        ur_id: str,
        requested_amount: Union[Decimal, str, int, float],
        discount_rate: Union[Decimal, str, int, float],
        anticipator_code: str,
        recipient_account: Optional[BankAccount] = None,
        settlement_channel: SettlementChannel = SettlementChannel.CREDITO_CONTA,
        anticipation_id: Optional[str] = None
    ) -> AnticipationRecord:
        """Executa operação de antecipação (pré-pagamento) de recebíveis de cartão.
        
        Regra Fundamental da Resolução CMN nº 4.734:
        É expressamente VEDADA a antecipação sobre parcela gravada ou onerada!
        Apenas o saldo livre/disponível pode ser antecipado:
        requested_amount <= available_amount.
        """
        if ur_id not in self.urs:
            raise ReceivableUnitNotFoundError(f"UR não encontrada: {ur_id}")
        ur = self.urs[ur_id]

        if ur.status == UrStatus.LIQUIDADA or ur.status == UrStatus.CANCELADA:
            raise InvalidReceivableOperationError(f"UR em estado {ur.status.value} não pode sofrer antecipação.")

        req_face = _quantize_money(requested_amount)
        rate = _quantize_rate(discount_rate)

        if req_face <= Decimal("0.00"):
            raise InvalidReceivableOperationError(f"Valor a antecipar deve ser positivo: {req_face}")

        # Trava fiduciária regulatória contra fraude de antecipação de gravames
        if req_face > ur.available_amount:
            raise EncumberedReceivableAnticipationError(
                f"Violação de Gravame: Tentativa de antecipar R$ {req_face}, mas a UR possui "
                f"apenas R$ {ur.available_amount} livres para negociação (R$ {ur.committed_amount} onerados por gravame)."
            )

        # Cálculo financeiro com conservação absoluta
        discount_fee = _quantize_money(req_face * rate)
        net_disbursed = _quantize_money(req_face - discount_fee)

        if (net_disbursed + discount_fee) != req_face:
            raise ReceivableConservationError(
                f"Erro de arredondamento na antecipação: {net_disbursed} + {discount_fee} != {req_face}"
            )

        dest_account = recipient_account or ur.default_merchant_account
        if dest_account is None:
            raise InvalidReceivableOperationError(f"Conta bancária de destino não definida para lojista na UR {ur_id}.")

        if anticipation_id is None:
            idx = len(ur.anticipations) + 1
            anticipation_id = f"ANT-{ur.settlement_date.replace('-', '')}-{idx:03d}-{hashlib.md5(str(req_face).encode()).hexdigest()[:6].upper()}"

        record = AnticipationRecord(
            anticipation_id=anticipation_id,
            ur_id=ur_id,
            requested_face_amount=req_face,
            discount_rate=rate,
            discount_fee=discount_fee,
            net_disbursed_amount=net_disbursed,
            anticipator_code=anticipator_code.strip().upper(),
            settlement_channel=settlement_channel,
            recipient_account=dest_account,
            conservation_held=True
        )

        # Atualiza a UR: o recebível antecipado foi liquidado antes da data
        ur.current_amount = _quantize_money(ur.current_amount - req_face)
        ur.settled_amount = _quantize_money(ur.settled_amount + req_face)
        ur.available_amount = _quantize_money(ur.current_amount - ur.committed_amount)
        ur.anticipations.append(record)
        ur.updated_at = datetime.now(timezone.utc).isoformat()
        ur.audit_trail.append({
            "event": "ANTICIPATION_SETTLED",
            "anticipation_id": anticipation_id,
            "face_amount": str(req_face),
            "net_disbursed": str(net_disbursed),
            "discount_fee": str(discount_fee),
            "anticipator": anticipator_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        ur._refresh_status()
        self._assert_ur_conservation(ur)
        return record

    # -----------------------------------------------------------------------
    # Liquidação Multilateral na Data Prevista (Vencimento da UR)
    # -----------------------------------------------------------------------

    def settle_ur_on_maturity(
        self,
        ur_id: str,
        execution_date: Optional[str] = None
    ) -> UrSettlementReceipt:
        """Executa a liquidação financeira da UR na data prevista (D0 de liquidação).
        
        Regras da Circular BCB nº 3.952 e Resolução CMN nº 4.734:
        1. Cascata de prioridade: Gravames ativos são liquidados prioritariamente para
           o domicílio vinculado do credor (trava bancária de domicílio fiduciário).
        2. O saldo remanescente livre (se houver) é liquidado para o domicílio bancário
           do lojista (usuário recebedor).
        3. Conservação absoluta: Somatório dos pagamentos == current_amount liquidado.
        """
        if ur_id not in self.urs:
            raise ReceivableUnitNotFoundError(f"UR não encontrada: {ur_id}")
        ur = self.urs[ur_id]

        if ur.status == UrStatus.LIQUIDADA:
            raise InvalidReceivableOperationError(f"UR {ur_id} já foi totalmente liquidada.")

        dt = execution_date or ur.settlement_date
        total_to_settle = ur.current_amount

        payments: List[SettlementPayment] = []
        creditor_total = Decimal("0.00")

        # 1. Ordena gravames ativos por priority_rank (1º grau, 2º grau...)
        active_liens = [l for l in ur.liens.values() if l.status in (LienStatus.ATIVO, LienStatus.ADITADO)]
        sorted_liens = sorted(active_liens, key=lambda x: (x.priority_rank, x.created_at))

        for lien in sorted_liens:
            pay = SettlementPayment(
                recipient_type="CREDITOR_LIEN",
                recipient_name=lien.creditor_name,
                recipient_document=lien.creditor_ispb,
                amount=lien.encumbered_amount,
                destination_account=lien.beneficiary_bank_account,
                lien_id=lien.lien_id,
                priority_rank=lien.priority_rank,
                payment_reference=f"TRAVA_FIDUCIARIA_{lien.contract_number}"
            )
            payments.append(pay)
            creditor_total = _quantize_money(creditor_total + lien.encumbered_amount)
            # Marca gravame como executado
            lien.status = LienStatus.EXECUTADO
            lien.updated_at = datetime.now(timezone.utc).isoformat()

        # 2. Saldo livre remanescente vai para o lojista
        merchant_free_balance = ur.available_amount
        if merchant_free_balance > Decimal("0.00"):
            if not ur.default_merchant_account:
                raise InvalidReceivableOperationError(f"UR {ur_id} não possui domicílio bancário do lojista configurado.")
            pay_merchant = SettlementPayment(
                recipient_type="MERCHANT_FREE_BALANCE",
                recipient_name=ur.merchant_name,
                recipient_document=ur.merchant_document,
                amount=merchant_free_balance,
                destination_account=ur.default_merchant_account,
                lien_id=None,
                priority_rank=None,
                payment_reference=f"REPASSE_LIVRE_LOJISTA_{ur.ur_id}"
            )
            payments.append(pay_merchant)

        # 3. Verificação de Conservação Contábil
        sum_payments = _quantize_money(sum(p.amount for p in payments))
        if sum_payments != total_to_settle:
            raise ReceivableConservationError(
                f"Violação de conservação contábil na liquidação da UR {ur_id}: "
                f"Soma dos pagamentos R$ {sum_payments} != Total a liquidar R$ {total_to_settle}."
            )

        # Atualiza a UR
        ur.settled_amount = _quantize_money(ur.settled_amount + total_to_settle)
        ur.current_amount = Decimal("0.00")
        ur.committed_amount = Decimal("0.00")
        ur.available_amount = Decimal("0.00")
        ur.status = UrStatus.LIQUIDADA
        ur.updated_at = datetime.now(timezone.utc).isoformat()

        receipt_id = f"RCP-LIQ-{ur.settlement_date.replace('-', '')}-{hashlib.md5(ur_id.encode()).hexdigest()[:8].upper()}"
        receipt = UrSettlementReceipt(
            receipt_id=receipt_id,
            ur_id=ur_id,
            settlement_date=dt,
            total_settled_amount=total_to_settle,
            total_creditor_payments=creditor_total,
            total_merchant_payments=merchant_free_balance,
            payments=payments,
            invariant_conservation_held=True,
            timestamp=datetime.now(timezone.utc).isoformat()
        )

        ur.audit_trail.append({
            "event": "UR_MATURITY_SETTLED",
            "receipt_id": receipt_id,
            "total_settled": str(total_to_settle),
            "creditor_payments": str(creditor_total),
            "merchant_payments": str(merchant_free_balance),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        self.settlement_receipts[receipt_id] = receipt
        self._assert_ur_conservation(ur)
        return receipt

    # -----------------------------------------------------------------------
    # Provas Formais e Invariantes
    # -----------------------------------------------------------------------

    def _assert_ur_conservation(self, ur: ReceivableUnit) -> None:
        """Verifica as leis matemáticas de conservação contábil e unicidade da UR."""
        # Invariante 1: V_disponivel >= 0 (Não sobre-oneramento)
        if ur.available_amount < Decimal("0.00"):
            raise ReceivableConservationError(
                f"Sobre-oneramento detectado na UR {ur.ur_id}: available_amount={ur.available_amount} < 0."
            )

        # Invariante 2: V_atual = V_comprometido + V_disponivel
        expected_current = _quantize_money(ur.committed_amount + ur.available_amount)
        if ur.current_amount != expected_current:
            raise ReceivableConservationError(
                f"Divergência de decomposição na UR {ur.ur_id}: current ({ur.current_amount}) != "
                f"committed ({ur.committed_amount}) + available ({ur.available_amount})."
            )

        # Invariante 3: V_comprometido = soma dos gravames ativos
        active_liens_sum = sum(
            l.encumbered_amount for l in ur.liens.values()
            if l.status in (LienStatus.ATIVO, LienStatus.ADITADO)
        )
        if ur.committed_amount != _quantize_money(active_liens_sum):
            raise ReceivableConservationError(
                f"Inconsistência de gravames ativos na UR {ur.ur_id}: committed ({ur.committed_amount}) != "
                f"soma gravames ({active_liens_sum})."
            )

    def verify_global_conservation(self) -> Dict[str, Any]:
        """Auditoria global em todas as URs da registradora NoMe B3."""
        total_original = Decimal("0.00")
        total_current = Decimal("0.00")
        total_committed = Decimal("0.00")
        total_available = Decimal("0.00")
        total_settled = Decimal("0.00")

        for ur in self.urs.values():
            self._assert_ur_conservation(ur)
            total_original += ur.original_amount
            total_current += ur.current_amount
            total_committed += ur.committed_amount
            total_available += ur.available_amount
            total_settled += ur.settled_amount

        total_original = _quantize_money(total_original)
        total_current = _quantize_money(total_current)
        total_committed = _quantize_money(total_committed)
        total_available = _quantize_money(total_available)
        total_settled = _quantize_money(total_settled)

        # Invariante contábil de ciclo completo: original = current + settled (para URs sem cancelamento)
        conservation_held = (total_current == _quantize_money(total_committed + total_available))

        return {
            "total_urs_count": len(self.urs),
            "total_original_volume": str(total_original),
            "total_current_volume": str(total_current),
            "total_committed_volume": str(total_committed),
            "total_available_volume": str(total_available),
            "total_settled_volume": str(total_settled),
            "global_conservation_held": conservation_held,
        }

    # -----------------------------------------------------------------------
    # Carga e Serialização
    # -----------------------------------------------------------------------

    @classmethod
    def load_from_fixture(cls, path_or_dict: Union[str, Path, Dict[str, Any]]) -> CardReceivablesRegistryEngine:
        """Instancia o motor carregando dados de fixture sintética padronizada."""
        if isinstance(path_or_dict, (str, Path)):
            with open(path_or_dict, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = path_or_dict

        engine = cls()

        # 1. Carrega Credenciadoras
        for acq in data.get("acquirers", []):
            engine.register_acquirer(
                acquirer_code=acq["acquirer_code"],
                name=acq["name"],
                cnpj=acq["cnpj"]
            )

        # 2. Carrega Estabelecimentos Comerciais (Lojistas)
        for m in data.get("merchants", []):
            acc_data = m["default_account"]
            acc = BankAccount.from_dict(acc_data)
            engine.register_merchant(
                document=m["document"],
                name=m["name"],
                default_account=acc
            )

        # 3. Carrega Consentimentos
        for c in data.get("consents", []):
            engine.grant_consent(
                merchant_document=c["merchant_document"],
                creditor_ispb=c["creditor_ispb"],
                creditor_name=c["creditor_name"],
                expiration_date=c["expiration_date"]
            )

        # 4. Registra Vendas Iniciais / URs
        for tx in data.get("receivable_transactions", []):
            engine.register_receivable_transaction(
                merchant_document=tx["merchant_document"],
                acquirer_code=tx["acquirer_code"],
                payment_scheme=PaymentScheme(tx["payment_scheme"]),
                settlement_date=tx["settlement_date"],
                amount=Decimal(str(tx["amount"])),
                transaction_id=tx.get("transaction_id", "")
            )

        # 5. Registra Gravames Iniciais
        for lien_data in data.get("initial_liens", []):
            ben_acc = BankAccount.from_dict(lien_data["beneficiary_bank_account"])
            engine.constitute_lien(
                ur_id=lien_data["ur_id"],
                creditor_ispb=lien_data["creditor_ispb"],
                creditor_name=lien_data["creditor_name"],
                contract_number=lien_data["contract_number"],
                modality=LienModality(lien_data["modality"]),
                encumbered_amount=Decimal(str(lien_data["encumbered_amount"])),
                beneficiary_bank_account=ben_acc,
                priority_rank=int(lien_data.get("priority_rank", 1)),
                lien_id=lien_data.get("lien_id")
            )

        return engine

    def export_state_dict(self) -> Dict[str, Any]:
        """Exporta estado completo do registro de recebíveis para serialização JSON."""
        return {
            "urs": {uid: ur.to_dict() for uid, ur in self.urs.items()},
            "consents": self.consents,
            "merchants": {doc: {k: v.to_dict() if isinstance(v, BankAccount) else v for k, v in m.items()} for doc, m in self.merchants.items()},
            "acquirers": self.acquirers,
            "settlement_receipts": {rid: r.to_dict() for rid, r in self.settlement_receipts.items()},
            "global_metrics": self.verify_global_conservation(),
        }
