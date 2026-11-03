"""Motor de Portabilidade de Custódia e Transferência STVM (FL-024) — B3 Depositária Central.

Este módulo implementa o modelo de dados, ciclo de vida e motor determinístico
de validação de elegibilidade e execução de Solicitação de Transferência de
Valores Mobiliários (STVM) no ambiente da Central Depositária da B3,
em conformidade com a Instrução CVM nº 505/2011 (art. 33 a 37), Resolução CVM nº 175/2022,
Resolução CVM nº 32/2021 e Manual de Transferência de Custódia da B3.

Regra Fundamental:
    A conservação de custódia é estritamente mantida:
    Saldo_Cedente_Final + Saldo_Cessionario_Final == Saldo_Cedente_Inicial + Saldo_Cessionario_Inicial.
    Apenas saldo livre/disponível pode ser transferido; bloqueios judiciais e
    garantias na câmara (CORE) impedem sumariamente a liquidação da STVM.

Utiliza estritamente a biblioteca padrão do Python (sem dependências externas).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------

class STVMStatus(str, Enum):
    """Ciclo de estados da Solicitação de Transferência de Valores Mobiliários."""

    SOLICITADA = "SOLICITADA"
    VALIDADA_CEDENTE = "VALIDADA_CEDENTE"
    ACEITA_CESSIONARIO = "ACEITA_CESSIONARIO"
    EFETIVADA_B3 = "EFETIVADA_B3"
    RECUSADA = "RECUSADA"


class TransferReason(str, Enum):
    """Motivos regulatórios de transferência de custódia (STVM)."""

    MESMA_TITULARIDADE = "MESMA_TITULARIDADE"
    DOACAO = "DOACAO"
    HERANCA_INVENTARIO = "HERANCA_INVENTARIO"
    ORDEM_JUDICIAL = "ORDEM_JUDICIAL"
    DISSOLUCAO_SOCIETARIA = "DISSOLUCAO_SOCIETARIA"
    INTEGRALIZACAO_CAPITAL = "INTEGRALIZACAO_CAPITAL"
    OUTROS = "OUTROS"

    @property
    def is_same_holder(self) -> bool:
        """Indica se a transferência mantém a mesma titularidade (mesmo CPF/CNPJ)."""
        return self == TransferReason.MESMA_TITULARIDADE

    @property
    def requires_documentation(self) -> bool:
        """Indica se há exigência legal de documentação comprobatória anexada."""
        return self != TransferReason.MESMA_TITULARIDADE


class STVMRejectionCode(str, Enum):
    """Códigos padronizados de recusa de STVM da Depositária B3."""

    REJ_SALDO_INSUFICIENTE = "REJ_SALDO_INSUFICIENTE"
    REJ_SALDO_BLOQUEADO_MARGEM = "REJ_SALDO_BLOQUEADO_MARGEM"
    REJ_SALDO_BLOQUEADO_JUDICIAL = "REJ_SALDO_BLOQUEADO_JUDICIAL"
    REJ_SALDO_BLOQUEADO_ALUGUEL = "REJ_SALDO_BLOQUEADO_ALUGUEL"
    REJ_TITULARIDADE_DIVERGENTE = "REJ_TITULARIDADE_DIVERGENTE"
    REJ_DOCUMENTACAO_AUSENTE = "REJ_DOCUMENTACAO_AUSENTE"
    REJ_CONTA_DESTINO_INVALIDA = "REJ_CONTA_DESTINO_INVALIDA"
    REJ_CONTA_ORIGEM_INVALIDA = "REJ_CONTA_ORIGEM_INVALIDA"
    REJ_PARTICIPANTE_INVALIDO = "REJ_PARTICIPANTE_INVALIDO"
    REJ_ATIVO_INEXISTENTE = "REJ_ATIVO_INEXISTENTE"
    REJ_PRAZO_EXPIRADO = "REJ_PRAZO_EXPIRADO"
    REJ_CANCELADA_PELO_INVESTIDOR = "REJ_CANCELADA_PELO_INVESTIDOR"


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class STVMError(Exception):
    """Exceção base para operações de portabilidade de custódia STVM."""
    pass


class STVMValidationError(STVMError):
    """Erro de validação cadastral ou estrutural na solicitação STVM."""

    def __init__(self, message: str, rejection_code: STVMRejectionCode = STVMRejectionCode.REJ_DOCUMENTACAO_AUSENTE) -> None:
        super().__init__(message)
        self.rejection_code = rejection_code


class STVMCustodyEligibilityError(STVMError):
    """Erro de elegibilidade de custódia (saldo insuficiente ou bloqueado)."""

    def __init__(
        self,
        message: str,
        symbol: str,
        available_quantity: Decimal,
        requested_quantity: Decimal,
        rejection_code: STVMRejectionCode,
    ) -> None:
        super().__init__(message)
        self.symbol = symbol
        self.available_quantity = available_quantity
        self.requested_quantity = requested_quantity
        self.rejection_code = rejection_code


class STVMTitularityMismatchError(STVMError):
    """Divergência de titularidade em transferência de mesma titularidade."""

    def __init__(
        self,
        message: str,
        source_investor_id: str,
        target_investor_id: str,
        rejection_code: STVMRejectionCode = STVMRejectionCode.REJ_TITULARIDADE_DIVERGENTE,
    ) -> None:
        super().__init__(message)
        self.source_investor_id = source_investor_id
        self.target_investor_id = target_investor_id
        self.rejection_code = rejection_code


class STVMStateTransitionError(STVMError):
    """Tentativa de transição de estado inválida no ciclo de vida da STVM."""
    pass


# ---------------------------------------------------------------------------
# Modelos de Dados (Dataclasses)
# ---------------------------------------------------------------------------

def _clean_id(raw_id: Optional[str]) -> str:
    """Higieniza CPF ou CNPJ removendo pontos, traços, barras e espaços."""
    if not raw_id:
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", str(raw_id)).strip()


@dataclass
class STVMAssetItem:
    """Item de ativo mobiliário individual submetido à transferência."""

    symbol: str
    isin: str
    quantity: Decimal
    asset_type: str = "ACAO"  # ACAO, FII, BDR, ETF
    declared_unit_cost: Optional[Decimal] = None

    def __post_init__(self) -> None:
        if not isinstance(self.quantity, Decimal):
            self.quantity = Decimal(str(self.quantity))
        if self.declared_unit_cost is not None and not isinstance(self.declared_unit_cost, Decimal):
            self.declared_unit_cost = Decimal(str(self.declared_unit_cost))
        if self.quantity <= 0:
            raise STVMValidationError(f"Quantidade do item {self.symbol} deve ser estritamente positiva.")

    def to_dict(self) -> Dict[str, Any]:
        """Converte o item em dicionário serializável."""
        res: Dict[str, Any] = {
            "symbol": self.symbol,
            "isin": self.isin,
            "quantity": int(self.quantity) if self.quantity == self.quantity.to_integral() else float(self.quantity),
            "asset_type": self.asset_type,
        }
        if self.declared_unit_cost is not None:
            res["declared_unit_cost"] = str(self.declared_unit_cost)
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> STVMAssetItem:
        """Constrói uma instância a partir de dicionário."""
        return cls(
            symbol=data["symbol"],
            isin=data.get("isin", ""),
            quantity=Decimal(str(data["quantity"])),
            asset_type=data.get("asset_type", "ACAO"),
            declared_unit_cost=Decimal(str(data["declared_unit_cost"])) if data.get("declared_unit_cost") is not None else None,
        )


@dataclass
class CustodyPosition:
    """Posição em livro de custódia depositária de um participante/conta/ativo."""

    participant: int
    account: str
    symbol: str
    isin: str
    total_quantity: Decimal
    available_quantity: Decimal
    blocked_margin_quantity: Decimal = Decimal("0")
    blocked_judicial_quantity: Decimal = Decimal("0")
    blocked_loan_quantity: Decimal = Decimal("0")
    other_blocked_quantity: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        self.total_quantity = Decimal(str(self.total_quantity))
        self.available_quantity = Decimal(str(self.available_quantity))
        self.blocked_margin_quantity = Decimal(str(self.blocked_margin_quantity))
        self.blocked_judicial_quantity = Decimal(str(self.blocked_judicial_quantity))
        self.blocked_loan_quantity = Decimal(str(self.blocked_loan_quantity))
        self.other_blocked_quantity = Decimal(str(self.other_blocked_quantity))

    def is_consistent(self) -> bool:
        """Verifica a equação canônica da custódia B3: Total == Livre + Bloqueios."""
        expected_total = (
            self.available_quantity
            + self.blocked_margin_quantity
            + self.blocked_judicial_quantity
            + self.blocked_loan_quantity
            + self.other_blocked_quantity
        )
        return self.total_quantity == expected_total


@dataclass
class CustodyMovement:
    """Movimentação de custódia física gerada na efetivação da transferência."""

    movement_id: str
    protocol: str
    movement_type: str  # TRANSF_CUST (IMBARQ013)
    symbol: str
    isin: str
    quantity: Decimal
    participant: int
    account: str
    direction: str  # "D" (Débito) ou "C" (Crédito)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """Converte a movimentação em dicionário serializável."""
        return {
            "movement_id": self.movement_id,
            "protocol": self.protocol,
            "movement_type": self.movement_type,
            "symbol": self.symbol,
            "isin": self.isin,
            "quantity": int(self.quantity) if self.quantity == self.quantity.to_integral() else float(self.quantity),
            "participant": self.participant,
            "account": self.account,
            "direction": self.direction,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class STVMAuditEvent:
    """Registro de evento para a trilha cronológica de auditoria regulatória."""

    timestamp: datetime
    from_status: Optional[STVMStatus]
    to_status: STVMStatus
    actor: str  # INVESTIDOR, CEDENTE, CESSIONARIO, B3_DEPOSITARIA
    action: str
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o evento de auditoria."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "from_status": self.from_status.value if self.from_status else None,
            "to_status": self.to_status.value,
            "actor": self.actor,
            "action": self.action,
            "details": self.details,
        }


@dataclass
class STVMRequest:
    """Solicitação de Transferência de Valores Mobiliários (STVM)."""

    protocol: str
    transfer_reason: Union[TransferReason, str]
    investor_id: str
    investor_name: str
    source_participant: int
    source_account: str
    target_participant: int
    target_account: str
    target_investor_id: Optional[str] = None
    target_investor_name: Optional[str] = None
    items: List[STVMAssetItem] = field(default_factory=list)
    status: STVMStatus = STVMStatus.SOLICITADA
    created_at: datetime = field(default_factory=datetime.now)
    regulatory_deadline: Optional[date] = None
    supporting_documents: List[str] = field(default_factory=list)
    rejection_code: Optional[str] = None
    rejection_reason: Optional[str] = None
    audit_trail: List[STVMAuditEvent] = field(default_factory=list)
    request_id: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.transfer_reason, str):
            try:
                self.transfer_reason = TransferReason(self.transfer_reason)
            except ValueError:
                # Caso venha um motivo em string não previsto, mapeia para OUTROS
                self.transfer_reason = TransferReason.OUTROS

        if isinstance(self.status, str):
            self.status = STVMStatus(self.status)

        if not self.request_id:
            self.request_id = self.protocol
        elif not self.protocol:
            self.protocol = self.request_id

        if not self.target_investor_id:
            self.target_investor_id = self.investor_id

        if not self.target_investor_name:
            self.target_investor_name = self.investor_name

        self.source_participant = int(self.source_participant)
        self.target_participant = int(self.target_participant)
        self.source_account = str(self.source_account).strip()
        self.target_account = str(self.target_account).strip()

        # Converte itens se necessário
        converted_items: List[STVMAssetItem] = []
        for it in self.items:
            if isinstance(it, dict):
                converted_items.append(STVMAssetItem.from_dict(it))
            else:
                converted_items.append(it)
        self.items = converted_items

    @property
    def clean_source_investor_id(self) -> str:
        """CPF/CNPJ do cedente sanitizado."""
        return _clean_id(self.investor_id)

    @property
    def clean_target_investor_id(self) -> str:
        """CPF/CNPJ do cessionário sanitizado."""
        return _clean_id(self.target_investor_id)

    def to_dict(self) -> Dict[str, Any]:
        """Exporta a requisição completa para dicionário estruturado."""
        return {
            "request_id": self.request_id or self.protocol,
            "protocol": self.protocol,
            "transfer_reason": self.transfer_reason.value if isinstance(self.transfer_reason, TransferReason) else str(self.transfer_reason),
            "investor_id": self.investor_id,
            "investor_name": self.investor_name,
            "source_participant": self.source_participant,
            "source_account": self.source_account,
            "target_participant": self.target_participant,
            "target_account": self.target_account,
            "target_investor_id": self.target_investor_id,
            "target_investor_name": self.target_investor_name,
            "items": [it.to_dict() for it in self.items],
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else str(self.created_at),
            "regulatory_deadline": self.regulatory_deadline.isoformat() if isinstance(self.regulatory_deadline, date) else self.regulatory_deadline,
            "supporting_documents": list(self.supporting_documents),
            "rejection_code": self.rejection_code,
            "rejection_reason": self.rejection_reason,
            "audit_trail": [ev.to_dict() for ev in self.audit_trail],
        }

    def to_json(self, indent: int = 2) -> str:
        """Converte a solicitação para JSON formatado."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> STVMRequest:
        """Constrói uma requisição a partir de um dicionário."""
        raw_created_at = data.get("created_at")
        if isinstance(raw_created_at, str):
            try:
                created_at = datetime.fromisoformat(raw_created_at)
            except ValueError:
                created_at = datetime.now()
        elif isinstance(raw_created_at, datetime):
            created_at = raw_created_at
        else:
            created_at = datetime.now()

        raw_deadline = data.get("regulatory_deadline")
        deadline: Optional[date] = None
        if isinstance(raw_deadline, str):
            try:
                deadline = date.fromisoformat(raw_deadline)
            except ValueError:
                deadline = None
        elif isinstance(raw_deadline, date):
            deadline = raw_deadline

        items = [STVMAssetItem.from_dict(it) if isinstance(it, dict) else it for it in data.get("items", [])]

        return cls(
            protocol=data.get("protocol") or data.get("request_id", ""),
            transfer_reason=data.get("transfer_reason", TransferReason.MESMA_TITULARIDADE),
            investor_id=data.get("investor_id", ""),
            investor_name=data.get("investor_name", ""),
            source_participant=int(data.get("source_participant", 0)),
            source_account=str(data.get("source_account", "")),
            target_participant=int(data.get("target_participant", 0)),
            target_account=str(data.get("target_account", "")),
            target_investor_id=data.get("target_investor_id"),
            target_investor_name=data.get("target_investor_name"),
            items=items,
            status=STVMStatus(data.get("status", STVMStatus.SOLICITADA)),
            created_at=created_at,
            regulatory_deadline=deadline,
            supporting_documents=list(data.get("supporting_documents", [])),
            rejection_code=data.get("rejection_code"),
            rejection_reason=data.get("rejection_reason"),
            request_id=data.get("request_id"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> STVMRequest:
        """Constrói uma requisição a partir de string JSON."""
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> STVMRequest:
        """Carrega e instancia a requisição a partir de arquivo JSON."""
        p = Path(path)
        with open(p, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


@dataclass
class STVMTransferResult:
    """Resultado executivo da efetivação da transferência de custódia na B3."""

    protocol: str
    status: STVMStatus
    executed_at: datetime
    items_transferred: List[STVMAssetItem]
    movements: List[CustodyMovement]
    notes: str = ""


# ---------------------------------------------------------------------------
# Feriados Nacionais Brasileiros Padrão (Mês, Dia)
# ---------------------------------------------------------------------------

DEFAULT_BRAZILIAN_HOLIDAYS: Set[Tuple[int, int]] = {
    (1, 1),    # Ano Novo
    (4, 21),   # Tiradentes
    (5, 1),    # Dia do Trabalho
    (9, 7),    # Independência
    (10, 12),  # Nossa Senhora Aparecida
    (11, 2),   # Finados
    (11, 15),  # Proclamação da República
    (11, 20),  # Dia da Consciência Negra
    (12, 25),  # Natal
}


# ---------------------------------------------------------------------------
# Motor de Portabilidade STVM
# ---------------------------------------------------------------------------

@dataclass
class STVMEngine:
    """Motor de orquestração, validação de elegibilidade e efetivação de STVM."""

    requests: Dict[str, STVMRequest] = field(default_factory=dict)
    custody_books: Dict[Tuple[int, str, str], CustodyPosition] = field(default_factory=dict)
    movements_log: List[CustodyMovement] = field(default_factory=list)
    national_holidays: Set[Tuple[int, int]] = field(default_factory=lambda: set(DEFAULT_BRAZILIAN_HOLIDAYS))
    _protocol_seq: int = 1000

    def set_custody_position(self, position: CustodyPosition) -> None:
        """Cadastra ou atualiza a posição de custódia no livro contábil da câmara/depositária."""
        key = (position.participant, position.account, position.symbol)
        self.custody_books[key] = position

    def get_custody_position(self, participant: int, account: str, symbol: str) -> Optional[CustodyPosition]:
        """Consulta a posição em livro para o participante, conta e ativo."""
        key = (participant, account, symbol)
        return self.custody_books.get(key)

    def is_business_day(self, d: date) -> bool:
        """Determina se uma data é dia útil bancário e de negociação B3."""
        # Sábado (5) ou Domingo (6)
        if d.weekday() >= 5:
            return False
        # Feriado nacional fixo
        if (d.month, d.day) in self.national_holidays:
            return False
        return True

    def calculate_regulatory_deadline(self, start_date: Union[date, datetime], business_days: int = 2) -> date:
        """Calcula o prazo regulatório de atendimento (D+2 útil conforme Instrução CVM 505)."""
        current_date = start_date.date() if isinstance(start_date, datetime) else start_date
        added_days = 0

        while added_days < business_days:
            current_date += timedelta(days=1)
            if self.is_business_day(current_date):
                added_days += 1

        return current_date

    def generate_protocol(self, date_ref: Optional[date] = None, participant: int = 105) -> str:
        """Gera protocolo determinístico e único de transferência no padrão B3."""
        d = date_ref or date.today()
        d_str = d.strftime("%Y%m%d")
        self._protocol_seq += 1
        return f"STVM-{d_str}-{participant:03d}{self._protocol_seq:04d}"

    def register_request(self, request: STVMRequest) -> STVMRequest:
        """Registra uma nova solicitação no motor com cálculo de prazo regulatório."""
        if not request.protocol:
            request.protocol = self.generate_protocol(request.created_at.date(), request.source_participant)
            request.request_id = request.protocol

        if not request.regulatory_deadline:
            request.regulatory_deadline = self.calculate_regulatory_deadline(request.created_at, business_days=2)

        if not request.items:
            raise STVMValidationError("Solicitação STVM deve conter ao menos um ativo mobiliário para transferência.")

        # Registra evento inicial de auditoria
        request.status = STVMStatus.SOLICITADA
        request.audit_trail.append(
            STVMAuditEvent(
                timestamp=datetime.now(),
                from_status=None,
                to_status=STVMStatus.SOLICITADA,
                actor="INVESTIDOR",
                action="REGISTRO_SOLICITACAO",
                details=f"Solicitação STVM registrada com {len(request.items)} item(ns). Prazo regulatório: {request.regulatory_deadline}.",
            )
        )

        self.requests[request.protocol] = request
        return request

    def validate_cedente(self, protocol: str, actor_id: str = "CEDENTE") -> STVMRequest:
        """Executa a validação formal e a auditoria de elegibilidade de custódia no Cedente."""
        request = self.requests.get(protocol)
        if not request:
            raise STVMValidationError(f"Protocolo STVM '{protocol}' não encontrado.")

        if request.status != STVMStatus.SOLICITADA:
            raise STVMStateTransitionError(
                f"Não é possível validar pelo Cedente no estado atual '{request.status.value}'. Esperado: '{STVMStatus.SOLICITADA.value}'."
            )

        # 1. Validação de Titularidade e Documentação
        if request.transfer_reason == TransferReason.MESMA_TITULARIDADE:
            if request.clean_source_investor_id != request.clean_target_investor_id:
                err_msg = (
                    f"Divergência de titularidade em transferência de Mesma Titularidade: "
                    f"Cedente ({request.investor_id}) != Cessionário ({request.target_investor_id})."
                )
                self._reject(request, STVMRejectionCode.REJ_TITULARIDADE_DIVERGENTE, err_msg, actor=actor_id)
                raise STVMTitularityMismatchError(
                    err_msg,
                    source_investor_id=request.investor_id,
                    target_investor_id=request.target_investor_id or "",
                )
        else:
            # Mudança de titularidade exige documentação comprobatória
            if not request.supporting_documents:
                err_msg = (
                    f"Transferência com mudança de titularidade ({request.transfer_reason.value}) "
                    f"exige documentação legal comprobatória (formal de partilha, guia ITCMD ou ordem judicial)."
                )
                self._reject(request, STVMRejectionCode.REJ_DOCUMENTACAO_AUSENTE, err_msg, actor=actor_id)
                raise STVMValidationError(err_msg, rejection_code=STVMRejectionCode.REJ_DOCUMENTACAO_AUSENTE)

        # 2. Validação de Elegibilidade de Custódia (Ativo por Ativo)
        for item in request.items:
            key = (request.source_participant, request.source_account, item.symbol)
            pos = self.custody_books.get(key)

            if not pos:
                err_msg = (
                    f"Ativo {item.symbol} não localizado na custódia do comitente "
                    f"(Participante: {request.source_participant}, Conta: {request.source_account})."
                )
                self._reject(request, STVMRejectionCode.REJ_ATIVO_INEXISTENTE, err_msg, actor=actor_id)
                raise STVMCustodyEligibilityError(
                    err_msg,
                    symbol=item.symbol,
                    available_quantity=Decimal("0"),
                    requested_quantity=item.quantity,
                    rejection_code=STVMRejectionCode.REJ_ATIVO_INEXISTENTE,
                )

            # Verifica se saldo livre atende à quantidade solicitada
            if pos.available_quantity < item.quantity:
                # Determina o motivo impeditivo específico
                if pos.blocked_judicial_quantity > 0:
                    rej_code = STVMRejectionCode.REJ_SALDO_BLOQUEADO_JUDICIAL
                    err_msg = (
                        f"Ativo {item.symbol} possui gravame judicial SISBAJUD de "
                        f"{pos.blocked_judicial_quantity} cotas/ações. Transferência bloqueada."
                    )
                elif pos.blocked_margin_quantity > 0:
                    rej_code = STVMRejectionCode.REJ_SALDO_BLOQUEADO_MARGEM
                    err_msg = (
                        f"Ativo {item.symbol} possui {pos.blocked_margin_quantity} cotas/ações "
                        f"depositadas em garantia perante a Câmara B3 (CORE). Transferência bloqueada."
                    )
                elif pos.blocked_loan_quantity > 0:
                    rej_code = STVMRejectionCode.REJ_SALDO_BLOQUEADO_ALUGUEL
                    err_msg = (
                        f"Ativo {item.symbol} vinculado a contrato doador de empréstimo de ativos "
                        f"({pos.blocked_loan_quantity} cotas/ações). Transferência bloqueada."
                    )
                else:
                    rej_code = STVMRejectionCode.REJ_SALDO_INSUFICIENTE
                    err_msg = (
                        f"Saldo disponível insuficiente de {item.symbol}: disponível "
                        f"{pos.available_quantity}, solicitado {item.quantity}."
                    )

                self._reject(request, rej_code, err_msg, actor=actor_id)
                raise STVMCustodyEligibilityError(
                    err_msg,
                    symbol=item.symbol,
                    available_quantity=pos.available_quantity,
                    requested_quantity=item.quantity,
                    rejection_code=rej_code,
                )

        # Se todas as validações passaram:
        prev_status = request.status
        request.status = STVMStatus.VALIDADA_CEDENTE
        request.audit_trail.append(
            STVMAuditEvent(
                timestamp=datetime.now(),
                from_status=prev_status,
                to_status=STVMStatus.VALIDADA_CEDENTE,
                actor=actor_id,
                action="VALIDACAO_CEDENTE_APROVADA",
                details="Custódia livre conferida e titularidade/documentação atestada com sucesso.",
            )
        )
        return request

    def accept_cessionario(
        self,
        protocol: str,
        confirmed: bool = True,
        notes: str = "",
        actor_id: str = "CESSIONARIO",
    ) -> STVMRequest:
        """Registra o aceite ou recusa do Participante Cessionário (Destino)."""
        request = self.requests.get(protocol)
        if not request:
            raise STVMValidationError(f"Protocolo STVM '{protocol}' não encontrado.")

        if request.status != STVMStatus.VALIDADA_CEDENTE:
            raise STVMStateTransitionError(
                f"Não é possível registrar aceite do Cessionário no estado atual '{request.status.value}'. Esperado: '{STVMStatus.VALIDADA_CEDENTE.value}'."
            )

        if not confirmed:
            err_msg = f"Cessionário recusou recebimento de custódia: {notes or 'Conta de destino inativa ou irregular'}."
            self._reject(request, STVMRejectionCode.REJ_CONTA_DESTINO_INVALIDA, err_msg, actor=actor_id)
            return request

        prev_status = request.status
        request.status = STVMStatus.ACEITA_CESSIONARIO
        request.audit_trail.append(
            STVMAuditEvent(
                timestamp=datetime.now(),
                from_status=prev_status,
                to_status=STVMStatus.ACEITA_CESSIONARIO,
                actor=actor_id,
                action="ACEITE_CESSIONARIO",
                details=f"Cessionário confirmou conta ativa e aptidão para recepcionar ativos. {notes}".strip(),
            )
        )
        return request

    def execute_transfer(
        self,
        protocol: str,
        b3_timestamp: Optional[datetime] = None,
        actor_id: str = "B3_DEPOSITARIA",
    ) -> STVMTransferResult:
        """Efetiva atomicamente a transferência de custódia na Central Depositária da B3."""
        request = self.requests.get(protocol)
        if not request:
            raise STVMValidationError(f"Protocolo STVM '{protocol}' não encontrado.")

        if request.status != STVMStatus.ACEITA_CESSIONARIO:
            raise STVMStateTransitionError(
                f"Não é possível efetivar STVM na B3 no estado atual '{request.status.value}'. Esperado: '{STVMStatus.ACEITA_CESSIONARIO.value}'."
            )

        exec_time = b3_timestamp or datetime.now()
        generated_movements: List[CustodyMovement] = []

        # Execução atômica dos lançamentos contábeis
        for item in request.items:
            source_key = (request.source_participant, request.source_account, item.symbol)
            source_pos = self.custody_books[source_key]

            # 1. Débito no Cedente (TRANSF_CUST - D)
            source_pos.available_quantity -= item.quantity
            source_pos.total_quantity -= item.quantity

            deb_mov = CustodyMovement(
                movement_id=f"MOV-{protocol}-DEB-{item.symbol}",
                protocol=protocol,
                movement_type="TRANSF_CUST",
                symbol=item.symbol,
                isin=item.isin,
                quantity=item.quantity,
                participant=request.source_participant,
                account=request.source_account,
                direction="D",
                timestamp=exec_time,
            )
            self.movements_log.append(deb_mov)
            generated_movements.append(deb_mov)

            # 2. Crédito no Cessionário (TRANSF_CUST - C)
            target_key = (request.target_participant, request.target_account, item.symbol)
            target_pos = self.custody_books.get(target_key)

            if target_pos is None:
                # Se não havia posição prévia daquele ativo na conta de destino, cria-se o registro
                target_pos = CustodyPosition(
                    participant=request.target_participant,
                    account=request.target_account,
                    symbol=item.symbol,
                    isin=item.isin,
                    total_quantity=item.quantity,
                    available_quantity=item.quantity,
                )
                self.custody_books[target_key] = target_pos
            else:
                target_pos.available_quantity += item.quantity
                target_pos.total_quantity += item.quantity

            cred_mov = CustodyMovement(
                movement_id=f"MOV-{protocol}-CRED-{item.symbol}",
                protocol=protocol,
                movement_type="TRANSF_CUST",
                symbol=item.symbol,
                isin=item.isin,
                quantity=item.quantity,
                participant=request.target_participant,
                account=request.target_account,
                direction="C",
                timestamp=exec_time,
            )
            self.movements_log.append(cred_mov)
            generated_movements.append(cred_mov)

        # Transição final do estado
        prev_status = request.status
        request.status = STVMStatus.EFETIVADA_B3
        request.audit_trail.append(
            STVMAuditEvent(
                timestamp=exec_time,
                from_status=prev_status,
                to_status=STVMStatus.EFETIVADA_B3,
                actor=actor_id,
                action="EFETIVACAO_DEPOSITARIA_B3",
                details=f"Transferência de custódia executada atomicamente pela B3 com {len(generated_movements)} movimentos contábeis.",
            )
        )

        return STVMTransferResult(
            protocol=protocol,
            status=STVMStatus.EFETIVADA_B3,
            executed_at=exec_time,
            items_transferred=list(request.items),
            movements=generated_movements,
            notes="Transferência STVM liquidada com sucesso no livro de custódia central B3.",
        )

    def reject_request(
        self,
        protocol: str,
        reason_code: Union[str, STVMRejectionCode],
        reason_message: str,
        rejected_by: str = "CEDENTE",
    ) -> STVMRequest:
        """Recusa formalmente uma solicitação STVM com registro regulatório."""
        request = self.requests.get(protocol)
        if not request:
            raise STVMValidationError(f"Protocolo STVM '{protocol}' não encontrado.")

        if request.status in (STVMStatus.EFETIVADA_B3, STVMStatus.RECUSADA):
            raise STVMStateTransitionError(
                f"Não é possível recusar uma STVM já no estado final '{request.status.value}'."
            )

        self._reject(request, reason_code, reason_message, actor=rejected_by)
        return request

    def _reject(
        self,
        request: STVMRequest,
        reason_code: Union[str, STVMRejectionCode],
        reason_message: str,
        actor: str,
    ) -> None:
        """Helper interno para aplicar a recusa e registrar o evento de auditoria."""
        code_str = reason_code.value if isinstance(reason_code, STVMRejectionCode) else str(reason_code)
        prev_status = request.status
        request.status = STVMStatus.RECUSADA
        request.rejection_code = code_str
        request.rejection_reason = reason_message

        request.audit_trail.append(
            STVMAuditEvent(
                timestamp=datetime.now(),
                from_status=prev_status,
                to_status=STVMStatus.RECUSADA,
                actor=actor,
                action="RECUSA_STVM",
                details=f"[{code_str}] {reason_message}",
            )
        )
