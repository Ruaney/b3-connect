"""Motor de alocação e repasse de operações em tempo real (RTC B3 - FL-008).

Este módulo implementa o ciclo de vida, modelos e validações estritas de
alocação e repasse (give-up) de negócios executados na B3.

Regra Fundamental:
    A soma das parcelas alocadas DEVE igualar a quantidade total do trade pai:
    sum(AllocatedQuantity) == ParentTradeQuantity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union


class AllocationStatus(str, Enum):
    """Estado do ciclo de vida da instrução/lote de alocação."""

    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class AllocationRole(str, Enum):
    """Papel da instrução de alocação."""

    GIVE_UP = "GIVE_UP"
    ALLOCATION = "ALLOCATION"
    INVESTOR = "INVESTOR"
    EXECUTING_BROKER = "EXECUTING_BROKER"


class ReasonCode(str, Enum):
    """Códigos padronizados de motivo e resultado de validação."""

    OK = "OK"
    ERR_SUM_MISMATCH = "ERR_SUM_MISMATCH"
    ERR_OVER_ALLOCATION = "ERR_OVER_ALLOCATION"
    ERR_UNDER_ALLOCATION = "ERR_UNDER_ALLOCATION"
    ERR_PRICE_MISMATCH = "ERR_PRICE_MISMATCH"
    ERR_QTY_NON_POSITIVE = "ERR_QTY_NON_POSITIVE"
    ERR_INVALID_ACCOUNT = "ERR_INVALID_ACCOUNT"
    ERR_INVALID_CLEARING_MEMBER = "ERR_INVALID_CLEARING_MEMBER"
    ERR_DUPLICATE_ALLOCATION_ID = "ERR_DUPLICATE_ALLOCATION_ID"
    ERR_TRADE_REF_MISMATCH = "ERR_TRADE_REF_MISMATCH"
    ERR_EMPTY_BATCH = "ERR_EMPTY_BATCH"


class AllocationError(Exception):
    """Exceção base para erros no motor de alocação."""

    def __init__(self, message: str, reason_code: ReasonCode = ReasonCode.ERR_SUM_MISMATCH) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class AllocationSumMismatchError(AllocationError):
    """Lançada quando a soma das parcelas diverge da quantidade do trade pai."""

    def __init__(
        self,
        message: str,
        parent_quantity: Decimal,
        allocated_quantity: Decimal,
        reason_code: ReasonCode = ReasonCode.ERR_SUM_MISMATCH,
    ) -> None:
        super().__init__(message, reason_code=reason_code)
        self.parent_quantity = parent_quantity
        self.allocated_quantity = allocated_quantity


class AllocationValidationError(AllocationError):
    """Lançada quando há erro de validação cadastral ou estrutural no lote."""

    def __init__(self, message: str, reason_code: ReasonCode) -> None:
        super().__init__(message, reason_code=reason_code)


@dataclass(frozen=True)
class ParentTrade:
    """Dados de referência da operação mãe (negócio executado no PUMA B3)."""

    trade_ref_id: str
    symbol: str
    executing_broker: int
    parent_trade_quantity: Decimal
    parent_trade_price: Decimal
    trade_date: Optional[str] = None
    side: str = "BUY"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ParentTrade:
        """Instancia ParentTrade a partir de dicionário."""
        trade_ref = data.get("trade_ref_id") or data.get("TradeRefID")
        symbol = data.get("symbol") or data.get("Symbol")
        broker = data.get("executing_broker") or data.get("ExecutingBroker")
        qty = data.get("parent_trade_quantity") or data.get("ParentTradeQuantity")
        price = data.get("parent_trade_price") or data.get("ParentTradePrice")
        t_date = data.get("trade_date") or data.get("TradeDate")
        side = data.get("side") or data.get("Side") or "BUY"

        if not trade_ref:
            raise AllocationValidationError("trade_ref_id obrigatório", ReasonCode.ERR_TRADE_REF_MISMATCH)
        if not symbol:
            raise AllocationValidationError("symbol obrigatório", ReasonCode.ERR_EMPTY_BATCH)
        if broker is None:
            raise AllocationValidationError("executing_broker obrigatório", ReasonCode.ERR_INVALID_CLEARING_MEMBER)
        if qty is None:
            raise AllocationValidationError("parent_trade_quantity obrigatório", ReasonCode.ERR_SUM_MISMATCH)
        if price is None:
            raise AllocationValidationError("parent_trade_price obrigatório", ReasonCode.ERR_PRICE_MISMATCH)

        return cls(
            trade_ref_id=str(trade_ref).strip(),
            symbol=str(symbol).strip(),
            executing_broker=int(broker),
            parent_trade_quantity=Decimal(str(qty)),
            parent_trade_price=Decimal(str(price)),
            trade_date=str(t_date).strip() if t_date else None,
            side=str(side).strip().upper(),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o trade pai para dicionário compatível com JSON."""
        return {
            "trade_ref_id": self.trade_ref_id,
            "symbol": self.symbol,
            "executing_broker": self.executing_broker,
            "parent_trade_quantity": int(self.parent_trade_quantity)
            if self.parent_trade_quantity % 1 == 0
            else float(self.parent_trade_quantity),
            "parent_trade_price": str(self.parent_trade_price),
            "trade_date": self.trade_date,
            "side": self.side,
        }


@dataclass
class AllocationInstruction:
    """Instrução individual de alocação/repasse para uma conta de custódia alvo."""

    allocation_id: str
    trade_ref_id: str
    target_account: int
    target_clearing_member: int
    allocated_quantity: Decimal
    allocation_price: Decimal
    allocation_role: Union[AllocationRole, str] = AllocationRole.GIVE_UP
    status: Union[AllocationStatus, str] = AllocationStatus.SUBMITTED
    reason_code: Optional[Union[ReasonCode, str]] = None
    investor_name: Optional[str] = None
    timestamp: Optional[str] = None

    def __post_init__(self) -> None:
        """Garante coerência de tipos Decimal e Enums."""
        if not isinstance(self.allocated_quantity, Decimal):
            self.allocated_quantity = Decimal(str(self.allocated_quantity))
        if not isinstance(self.allocation_price, Decimal):
            self.allocation_price = Decimal(str(self.allocation_price))
        if isinstance(self.allocation_role, str):
            try:
                self.allocation_role = AllocationRole(self.allocation_role)
            except ValueError:
                pass
        if isinstance(self.status, str):
            try:
                self.status = AllocationStatus(self.status)
            except ValueError:
                pass
        if isinstance(self.reason_code, str):
            try:
                self.reason_code = ReasonCode(self.reason_code)
            except ValueError:
                pass

    def accept(self) -> None:
        """Transita o status para ACCEPTED e marca ReasonCode como OK."""
        self.status = AllocationStatus.ACCEPTED
        self.reason_code = ReasonCode.OK

    def reject(self, reason: Union[ReasonCode, str]) -> None:
        """Transita o status para REJECTED com o código de motivo fornecido."""
        self.status = AllocationStatus.REJECTED
        self.reason_code = ReasonCode(reason) if isinstance(reason, str) and reason in ReasonCode._value2member_map_ else reason

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AllocationInstruction:
        """Cria instrução a partir de dicionário flexível (chaves minúsculas ou PascalCase)."""
        alloc_id = data.get("allocation_id") or data.get("AllocationID")
        trade_ref = data.get("trade_ref_id") or data.get("TradeRefID")
        target_acc = data.get("target_account") or data.get("TargetAccount")
        clearing_mem = data.get("target_clearing_member") or data.get("TargetClearingMember")
        qty = (
            data.get("allocated_quantity")
            if "allocated_quantity" in data
            else data.get("AllocatedQuantity")
        )
        price = (
            data.get("allocation_price")
            if "allocation_price" in data
            else data.get("AllocationPrice")
        )
        role = data.get("allocation_role") or data.get("AllocationRole") or AllocationRole.GIVE_UP
        status = data.get("status") or data.get("Status") or AllocationStatus.SUBMITTED
        reason = data.get("reason_code") or data.get("ReasonCode")
        investor = data.get("investor_name") or data.get("InvestorName")
        ts = data.get("timestamp") or data.get("Timestamp")

        if alloc_id is None:
            raise AllocationValidationError("allocation_id ausente", ReasonCode.ERR_DUPLICATE_ALLOCATION_ID)
        if trade_ref is None:
            raise AllocationValidationError("trade_ref_id ausente", ReasonCode.ERR_TRADE_REF_MISMATCH)
        if target_acc is None:
            raise AllocationValidationError("target_account ausente", ReasonCode.ERR_INVALID_ACCOUNT)
        if clearing_mem is None:
            raise AllocationValidationError("target_clearing_member ausente", ReasonCode.ERR_INVALID_CLEARING_MEMBER)
        if qty is None:
            raise AllocationValidationError("allocated_quantity ausente", ReasonCode.ERR_QTY_NON_POSITIVE)
        if price is None:
            raise AllocationValidationError("allocation_price ausente", ReasonCode.ERR_PRICE_MISMATCH)

        return cls(
            allocation_id=str(alloc_id).strip(),
            trade_ref_id=str(trade_ref).strip(),
            target_account=int(target_acc),
            target_clearing_member=int(clearing_mem),
            allocated_quantity=Decimal(str(qty)),
            allocation_price=Decimal(str(price)),
            allocation_role=role,
            status=status,
            reason_code=reason,
            investor_name=str(investor).strip() if investor else None,
            timestamp=str(ts).strip() if ts else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Converte a instrução para dicionário serializável em JSON."""
        return {
            "allocation_id": self.allocation_id,
            "trade_ref_id": self.trade_ref_id,
            "target_account": self.target_account,
            "target_clearing_member": self.target_clearing_member,
            "allocated_quantity": int(self.allocated_quantity)
            if self.allocated_quantity % 1 == 0
            else float(self.allocated_quantity),
            "allocation_price": str(self.allocation_price),
            "allocation_role": self.allocation_role.value if isinstance(self.allocation_role, Enum) else str(self.allocation_role),
            "status": self.status.value if isinstance(self.status, Enum) else str(self.status),
            "investor_name": self.investor_name,
            "reason_code": self.reason_code.value if isinstance(self.reason_code, Enum) else self.reason_code,
            "timestamp": self.timestamp,
        }


@dataclass
class AllocationBatch:
    """Lote de alocação associado a um trade pai executado."""

    batch_id: str
    parent_trade: ParentTrade
    instructions: List[AllocationInstruction] = field(default_factory=list)
    status: Union[AllocationStatus, str] = AllocationStatus.SUBMITTED
    reason_code: Optional[Union[ReasonCode, str]] = None

    def total_allocated_quantity(self) -> Decimal:
        """Calcula a soma das quantidades de todas as parcelas do lote."""
        return sum((i.allocated_quantity for i in self.instructions), Decimal("0"))

    def is_balanced(self) -> bool:
        """Verifica se a soma das parcelas alocadas é estritamente igual à quantidade do trade pai."""
        return self.total_allocated_quantity() == self.parent_trade.parent_trade_quantity

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o lote completo para dicionário serializável em JSON."""
        return {
            "batch_id": self.batch_id,
            "parent_trade": self.parent_trade.to_dict(),
            "status": self.status.value if isinstance(self.status, Enum) else str(self.status),
            "reason_code": self.reason_code.value if isinstance(self.reason_code, Enum) else self.reason_code,
            "instructions": [instr.to_dict() for instr in self.instructions],
        }

    def to_json(self, indent: int = 2) -> str:
        """Converte lote para string JSON formatada."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AllocationBatch:
        """Cria uma instância de AllocationBatch a partir de dicionário."""
        batch_id = data.get("batch_id") or data.get("BatchID") or "BATCH-UNKNOWN"
        parent_raw = data.get("parent_trade") or data.get("ParentTrade")
        if not parent_raw or not isinstance(parent_raw, dict):
            raise AllocationValidationError("parent_trade ausente ou inválido no lote", ReasonCode.ERR_EMPTY_BATCH)

        parent = ParentTrade.from_dict(parent_raw)
        instr_list = data.get("instructions") or data.get("Instructions") or []
        instructions = [AllocationInstruction.from_dict(i) for i in instr_list]

        status = data.get("status") or data.get("Status") or AllocationStatus.SUBMITTED
        reason = data.get("reason_code") or data.get("ReasonCode")

        return cls(
            batch_id=str(batch_id),
            parent_trade=parent,
            instructions=instructions,
            status=status,
            reason_code=reason,
        )

    @classmethod
    def from_json(cls, json_str: str) -> AllocationBatch:
        """Cria uma instância de AllocationBatch a partir de string JSON."""
        data = json.loads(json_str)
        return cls.from_dict(data)

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> AllocationBatch:
        """Carrega e instancia um AllocationBatch a partir de arquivo JSON."""
        path_obj = Path(path)
        with path_obj.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


@dataclass(frozen=True)
class ValidationResult:
    """Resultado detalhado da validação de um lote de alocação."""

    is_valid: bool
    batch_id: str
    parent_trade_quantity: Decimal
    total_allocated_quantity: Decimal
    reason_code: ReasonCode
    errors: List[str] = field(default_factory=list)
    instruction_errors: Dict[str, List[str]] = field(default_factory=dict)


class AllocationValidator:
    """Motor de validação e processamento de regras de negócio de alocação RTC."""

    def __init__(self, strict_price: bool = True) -> None:
        """Inicializa o validador.
        
        Args:
            strict_price: Se True, exige que toda parcela tenha preço exatamente
                          igual ao preço do trade pai.
        """
        self.strict_price = strict_price

    def validate_batch(self, batch: AllocationBatch) -> ValidationResult:
        """Valida detalhadamente um lote de alocação sem alterar seus dados.
        
        Aplica:
          1. Verificação de lote não vazio.
          2. Integridade cadastral de cada instrução (contas, participantes, IDs únicos).
          3. Consistência de preço e TradeRefID com o trade pai.
          4. Regra estrita de equilíbrio quantitativo: sum(AllocatedQty) == ParentQty.
        """
        errors: List[str] = []
        instruction_errors: Dict[str, List[str]] = {}
        seen_ids: Set[str] = set()

        if not batch.instructions:
            return ValidationResult(
                is_valid=False,
                batch_id=batch.batch_id,
                parent_trade_quantity=batch.parent_trade.parent_trade_quantity,
                total_allocated_quantity=Decimal("0"),
                reason_code=ReasonCode.ERR_EMPTY_BATCH,
                errors=["Lote de alocação não contém nenhuma instrução."],
            )

        # 1. Validação individual das instruções
        for instr in batch.instructions:
            i_errs: List[str] = []

            # Unicidade do allocation_id
            if instr.allocation_id in seen_ids:
                i_errs.append(f"AllocationID duplicado no lote: '{instr.allocation_id}'.")
            seen_ids.add(instr.allocation_id)

            # TradeRefID deve ser idêntico ao pai
            if instr.trade_ref_id != batch.parent_trade.trade_ref_id:
                i_errs.append(
                    f"TradeRefID '{instr.trade_ref_id}' diverge do trade pai '{batch.parent_trade.trade_ref_id}'."
                )

            # TargetAccount válido
            if instr.target_account <= 0:
                i_errs.append(f"TargetAccount inválida: {instr.target_account}.")

            # TargetClearingMember válido
            if instr.target_clearing_member <= 0:
                i_errs.append(f"TargetClearingMember inválido: {instr.target_clearing_member}.")

            # Quantidade estritamente positiva
            if instr.allocated_quantity <= 0:
                i_errs.append(f"AllocatedQuantity deve ser positiva: {instr.allocated_quantity}.")

            # Preço consistente com o pai
            if self.strict_price and instr.allocation_price != batch.parent_trade.parent_trade_price:
                i_errs.append(
                    f"AllocationPrice '{instr.allocation_price}' diverge do preço do trade pai '{batch.parent_trade.parent_trade_price}'."
                )

            if i_errs:
                instruction_errors[instr.allocation_id] = i_errs
                errors.extend(i_errs)

        # 2. Regra estrita de equilíbrio quantitativo
        total_allocated = batch.total_allocated_quantity()
        parent_qty = batch.parent_trade.parent_trade_quantity

        if total_allocated != parent_qty:
            if total_allocated > parent_qty:
                err_msg = (
                    f"Sobre-alocação detectada (ERR_OVER_ALLOCATION): soma alocada ({total_allocated}) "
                    f"excede a quantidade do trade pai ({parent_qty}). Diferença: +{total_allocated - parent_qty}."
                )
                errors.append(err_msg)
                primary_reason = ReasonCode.ERR_OVER_ALLOCATION
            else:
                err_msg = (
                    f"Sub-alocação detectada (ERR_UNDER_ALLOCATION): soma alocada ({total_allocated}) "
                    f"é inferior à quantidade do trade pai ({parent_qty}). Saldo residual: {parent_qty - total_allocated}."
                )
                errors.append(err_msg)
                primary_reason = ReasonCode.ERR_UNDER_ALLOCATION
        elif errors:
            # Houve erros em instruções mesmo com soma correta
            if any("AllocationID duplicado" in e for e in errors):
                primary_reason = ReasonCode.ERR_DUPLICATE_ALLOCATION_ID
            elif any("AllocatedQuantity deve ser positiva" in e for e in errors):
                primary_reason = ReasonCode.ERR_QTY_NON_POSITIVE
            elif any("diverge do preço" in e for e in errors):
                primary_reason = ReasonCode.ERR_PRICE_MISMATCH
            elif any("TradeRefID" in e for e in errors):
                primary_reason = ReasonCode.ERR_TRADE_REF_MISMATCH
            elif any("TargetAccount" in e for e in errors):
                primary_reason = ReasonCode.ERR_INVALID_ACCOUNT
            else:
                primary_reason = ReasonCode.ERR_INVALID_CLEARING_MEMBER
        else:
            primary_reason = ReasonCode.OK

        is_valid = len(errors) == 0

        return ValidationResult(
            is_valid=is_valid,
            batch_id=batch.batch_id,
            parent_trade_quantity=parent_qty,
            total_allocated_quantity=total_allocated,
            reason_code=primary_reason,
            errors=errors,
            instruction_errors=instruction_errors,
        )

    def process_batch(
        self,
        batch: AllocationBatch,
        raise_on_error: bool = False,
    ) -> AllocationBatch:
        """Processa e transita os estados das instruções e do lote com base na validação.

        Em caso de sucesso:
            - batch.status -> ACCEPTED
            - batch.reason_code -> OK
            - todas as instruções -> status ACCEPTED, reason_code OK

        Em caso de falha:
            - batch.status -> REJECTED
            - batch.reason_code -> ReasonCode identificado
            - instruções com erro recebem o código específico; demais recebem o código do lote.
            - se raise_on_error for True, dispara a exceção correspondente.
        """
        result = self.validate_batch(batch)

        if result.is_valid:
            batch.status = AllocationStatus.ACCEPTED
            batch.reason_code = ReasonCode.OK
            for instr in batch.instructions:
                instr.accept()
        else:
            batch.status = AllocationStatus.REJECTED
            batch.reason_code = result.reason_code

            # Determina o código para as parcelas
            for instr in batch.instructions:
                specific_errs = result.instruction_errors.get(instr.allocation_id, [])
                if specific_errs:
                    if any("AllocatedQuantity deve ser positiva" in e for e in specific_errs):
                        instr.reject(ReasonCode.ERR_QTY_NON_POSITIVE)
                    elif any("AllocationPrice" in e for e in specific_errs):
                        instr.reject(ReasonCode.ERR_PRICE_MISMATCH)
                    elif any("TradeRefID" in e for e in specific_errs):
                        instr.reject(ReasonCode.ERR_TRADE_REF_MISMATCH)
                    elif any("TargetAccount" in e for e in specific_errs):
                        instr.reject(ReasonCode.ERR_INVALID_ACCOUNT)
                    elif any("TargetClearingMember" in e for e in specific_errs):
                        instr.reject(ReasonCode.ERR_INVALID_CLEARING_MEMBER)
                    elif any("duplicado" in e for e in specific_errs):
                        instr.reject(ReasonCode.ERR_DUPLICATE_ALLOCATION_ID)
                    else:
                        instr.reject(result.reason_code)
                else:
                    # Se o lote foi rejeitado por soma divergente, as parcelas herdam o erro de soma
                    instr.reject(result.reason_code)

            if raise_on_error:
                if result.reason_code in (
                    ReasonCode.ERR_SUM_MISMATCH,
                    ReasonCode.ERR_OVER_ALLOCATION,
                    ReasonCode.ERR_UNDER_ALLOCATION,
                ):
                    raise AllocationSumMismatchError(
                        f"Rejeição de lote por soma divergente: {'; '.join(result.errors)}",
                        parent_quantity=result.parent_trade_quantity,
                        allocated_quantity=result.total_allocated_quantity,
                        reason_code=result.reason_code,
                    )
                raise AllocationValidationError(
                    f"Rejeição de lote por validação: {'; '.join(result.errors)}",
                    reason_code=result.reason_code,
                )

        return batch
