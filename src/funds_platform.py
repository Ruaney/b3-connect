"""Plataforma de Fundos de Investimento B3 — Registro e Custódia Escritural (FL-029).

Este módulo implementa o modelo de mensageria, classes tipadas, regras de negócio
e motor de apuração de eventos corporativos e conciliação para cotas de fundos
de investimento estruturados e listados na B3 (Resolução CVM 175), incluindo:
- FIDC (Fundos de Investimento em Direitos Creditórios);
- FII (Fundos de Investimento Imobiliário);
- FIAGRO (Fundos nas Cadeias Produtivas Agroindustriais);
- ETF (Exchange Traded Funds / Fundos de Índice).

Suporta apuração de rendimentos distribuídos por cota, amortização de principal
com redução do valor patrimonial unitário (NAV) e conciliação determinística
do extrato de cotistas B3 contra o livro interno de custódia.

Estritamente baseado na biblioteca padrão do Python (sem dependências externas).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class FundsPlatformError(Exception):
    """Exceção base para operações da Plataforma de Fundos B3."""
    pass


class FundsValidationError(FundsPlatformError):
    """Violação de regras de validação ou consistência cadastral/operacional."""
    pass


class FundsReconciliationError(FundsPlatformError):
    """Erro no processo de conciliação de extratos de cotistas."""
    pass


class FundsEventError(FundsPlatformError):
    """Erro na apuração ou liquidação de eventos corporativos de fundos."""
    pass


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------

class FundCategory(str, Enum):
    """Categorias de fundos de investimento suportadas na B3 (Resolução CVM 175)."""
    FIDC = "FIDC"
    FII = "FII"
    FIAGRO = "FIAGRO"
    ETF = "ETF"

    @classmethod
    def from_str(cls, value: Any) -> FundCategory:
        """Converte e normaliza variações textuais para FundCategory."""
        if isinstance(value, cls):
            return value
        if not value:
            raise FundsValidationError("Categoria do fundo não pode ser vazia.")
        norm = str(value).strip().upper()
        synonyms = {
            "FIDC": cls.FIDC,
            "FUNDO_DIREITOS_CREDITORIOS": cls.FIDC,
            "DIREITOS_CREDITORIOS": cls.FIDC,
            "FII": cls.FII,
            "FUNDO_IMOBILIARIO": cls.FII,
            "IMOBILIARIO": cls.FII,
            "FIAGRO": cls.FIAGRO,
            "AGRO": cls.FIAGRO,
            "ETF": cls.ETF,
            "INDEX_FUND": cls.ETF,
            "FUNDO_DE_INDICE": cls.ETF,
        }
        if norm in synonyms:
            return synonyms[norm]
        valid = ", ".join(m.value for m in cls)
        raise FundsValidationError(
            f"Categoria de fundo inválida: {value!r}. Valores permitidos: {valid}"
        )


class QuotaMovementType(str, Enum):
    """Tipos de movimentações e eventos sobre cotas de fundos."""
    SUBSCRIPTION = "SUBSCRIPTION"
    PAID_IN_CAPITAL = "PAID_IN_CAPITAL"
    REDEMPTION = "REDEMPTION"
    AMORTIZATION = "AMORTIZATION"
    INCOME_DISTRIBUTION = "INCOME_DISTRIBUTION"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"

    @classmethod
    def from_str(cls, value: Any) -> QuotaMovementType:
        """Converte e normaliza variações textuais para QuotaMovementType."""
        if isinstance(value, cls):
            return value
        if not value:
            raise FundsValidationError("Tipo de movimentação não pode ser vazio.")
        norm = str(value).strip().upper()
        synonyms = {
            "SUBSCRIPTION": cls.SUBSCRIPTION,
            "SUBSCRICAO": cls.SUBSCRIPTION,
            "PAID_IN_CAPITAL": cls.PAID_IN_CAPITAL,
            "INTEGRALIZACAO": cls.PAID_IN_CAPITAL,
            "APORTE": cls.PAID_IN_CAPITAL,
            "REDEMPTION": cls.REDEMPTION,
            "RESGATE": cls.REDEMPTION,
            "AMORTIZATION": cls.AMORTIZATION,
            "AMORTIZACAO": cls.AMORTIZATION,
            "INCOME_DISTRIBUTION": cls.INCOME_DISTRIBUTION,
            "PROVENTOS": cls.INCOME_DISTRIBUTION,
            "RENDIMENTOS": cls.INCOME_DISTRIBUTION,
            "DIVIDENDOS": cls.INCOME_DISTRIBUTION,
            "TRANSFER_IN": cls.TRANSFER_IN,
            "TRANSFERENCIA_ENTRADA": cls.TRANSFER_IN,
            "TRANSFER_OUT": cls.TRANSFER_OUT,
            "TRANSFERENCIA_SAIDA": cls.TRANSFER_OUT,
        }
        if norm in synonyms:
            return synonyms[norm]
        valid = ", ".join(m.value for m in cls)
        raise FundsValidationError(
            f"Tipo de movimentação de cota inválido: {value!r}. Valores permitidos: {valid}"
        )


class QuotaClassType(str, Enum):
    """Classes e subclasses de cotas sob o arcabouço CVM 175."""
    SENIOR = "SENIOR"
    MEZZANINE = "MEZZANINE"
    SUBORDINATED = "SUBORDINATED"
    SINGLE = "UNICA"


class FundBreakType(str, Enum):
    """Categorias de divergências identificadas na conciliação de cotas."""
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    NAV_MISMATCH = "NAV_MISMATCH"
    EQUITY_MISMATCH = "EQUITY_MISMATCH"
    MISSING_IN_INTERNAL = "MISSING_IN_INTERNAL"
    MISSING_IN_STATEMENT = "MISSING_IN_STATEMENT"


# ---------------------------------------------------------------------------
# Funções Auxiliares de Conversão e Formatação
# ---------------------------------------------------------------------------

def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Converte valor genérico para Decimal com validação."""
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FundsValidationError(f"Não foi possível converter {value!r} para Decimal.") from exc


def _to_date(value: Any) -> date:
    """Converte string ou date para objeto date."""
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        v = value.strip()
        # Suporta ISO (YYYY-MM-DD), DDMMAAAA ou YYYYMMDD
        if len(v) == 10 and "-" in v:
            return datetime.strptime(v, "%Y-%m-%d").date()
        if len(v) == 8 and v.isdigit():
            # Tenta YYYYMMDD
            try:
                return datetime.strptime(v, "%Y%m%d").date()
            except ValueError:
                return datetime.strptime(v, "%d%m%Y").date()
    raise FundsValidationError(f"Formato de data inválido: {value!r}")


# ---------------------------------------------------------------------------
# Dataclasses de Domínio
# ---------------------------------------------------------------------------

@dataclass
class FundQuotaPosition:
    """Representa a posição escritural de cotas de um cotista em uma conta de custódia."""
    fund_id: str
    fund_name: str
    category: FundCategory
    quota_class: str
    custody_account: str
    investor_id: str
    position_date: date
    quota_quantity: Decimal
    quota_nav: Decimal
    blocked_quantity: Decimal = field(default_factory=lambda: Decimal("0.00000000"))
    pending_settlement_quantity: Decimal = field(default_factory=lambda: Decimal("0.00000000"))
    ticker: Optional[str] = None
    isin: Optional[str] = None
    investor_name: Optional[str] = None

    def __post_init__(self) -> None:
        # Normalizações de tipos
        if not isinstance(self.category, FundCategory):
            self.category = FundCategory.from_str(self.category)
        self.position_date = _to_date(self.position_date)
        self.quota_quantity = _to_decimal(self.quota_quantity)
        self.quota_nav = _to_decimal(self.quota_nav)
        self.blocked_quantity = _to_decimal(self.blocked_quantity)
        self.pending_settlement_quantity = _to_decimal(self.pending_settlement_quantity)
        self.fund_id = str(self.fund_id).strip()
        self.quota_class = str(self.quota_class).strip().upper()
        self.custody_account = str(self.custody_account).strip()
        self.investor_id = str(self.investor_id).strip()

        # Validações de integridade
        if self.quota_quantity < Decimal("0"):
            raise FundsValidationError(f"Quantidade de cotas não pode ser negativa: {self.quota_quantity}")
        if self.quota_nav < Decimal("0"):
            raise FundsValidationError(f"NAV da cota não pode ser negativo: {self.quota_nav}")
        if self.blocked_quantity < Decimal("0"):
            raise FundsValidationError(f"Quantidade bloqueada não pode ser negativa: {self.blocked_quantity}")
        if self.blocked_quantity > self.quota_quantity:
            raise FundsValidationError(
                f"Quantidade bloqueada ({self.blocked_quantity}) excede quantidade total ({self.quota_quantity})"
            )

    @property
    def available_quantity(self) -> Decimal:
        """Quantidade de cotas livremente disponível para negociação ou transferência."""
        return self.quota_quantity - self.blocked_quantity

    @property
    def total_equity_value(self) -> Decimal:
        """Valor patrimonial total bruto da posição: Quantidade * NAV."""
        return (self.quota_quantity * self.quota_nav).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def to_dict(self) -> Dict[str, Any]:
        """Converte a posição para representação em dicionário serializável."""
        return {
            "fund_id": self.fund_id,
            "fund_name": self.fund_name,
            "category": self.category.value,
            "quota_class": self.quota_class,
            "custody_account": self.custody_account,
            "investor_id": self.investor_id,
            "investor_name": self.investor_name,
            "position_date": self.position_date.isoformat(),
            "quota_quantity": str(self.quota_quantity),
            "quota_nav": str(self.quota_nav),
            "blocked_quantity": str(self.blocked_quantity),
            "pending_settlement_quantity": str(self.pending_settlement_quantity),
            "available_quantity": str(self.available_quantity),
            "total_equity_value": str(self.total_equity_value),
            "ticker": self.ticker,
            "isin": self.isin,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FundQuotaPosition:
        """Instancia a posição a partir de dicionário."""
        return cls(
            fund_id=data["fund_id"],
            fund_name=data.get("fund_name", ""),
            category=data["category"],
            quota_class=data.get("quota_class", "UNICA"),
            custody_account=data["custody_account"],
            investor_id=data["investor_id"],
            position_date=data["position_date"],
            quota_quantity=data["quota_quantity"],
            quota_nav=data["quota_nav"],
            blocked_quantity=data.get("blocked_quantity", Decimal("0")),
            pending_settlement_quantity=data.get("pending_settlement_quantity", Decimal("0")),
            ticker=data.get("ticker"),
            isin=data.get("isin"),
            investor_name=data.get("investor_name"),
        )


@dataclass
class QuotaTransaction:
    """Registro de evento ou transação financeira sobre cotas."""
    transaction_id: str
    fund_id: str
    movement_type: QuotaMovementType
    trade_date: date
    settlement_date: date
    custody_account: str
    investor_id: str
    quantity: Decimal
    unit_price: Decimal
    gross_amount: Decimal
    tax_amount: Decimal = field(default_factory=lambda: Decimal("0.00"))
    net_amount: Decimal = field(default_factory=lambda: Decimal("0.00"))
    quota_class: str = "UNICA"
    description: Optional[str] = None
    status: str = "SETTLED"

    def __post_init__(self) -> None:
        if not isinstance(self.movement_type, QuotaMovementType):
            self.movement_type = QuotaMovementType.from_str(self.movement_type)
        self.trade_date = _to_date(self.trade_date)
        self.settlement_date = _to_date(self.settlement_date)
        self.quantity = _to_decimal(self.quantity)
        self.unit_price = _to_decimal(self.unit_price)
        self.gross_amount = _to_decimal(self.gross_amount)
        self.tax_amount = _to_decimal(self.tax_amount)
        self.net_amount = _to_decimal(self.net_amount)
        self.quota_class = str(self.quota_class).strip().upper()

        if self.net_amount == Decimal("0.00") and self.gross_amount != Decimal("0.00"):
            self.net_amount = self.gross_amount - self.tax_amount

    def to_dict(self) -> Dict[str, Any]:
        """Converte a transação para dicionário serializável."""
        return {
            "transaction_id": self.transaction_id,
            "fund_id": self.fund_id,
            "movement_type": self.movement_type.value,
            "trade_date": self.trade_date.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
            "custody_account": self.custody_account,
            "investor_id": self.investor_id,
            "quantity": str(self.quantity),
            "unit_price": str(self.unit_price),
            "gross_amount": str(self.gross_amount),
            "tax_amount": str(self.tax_amount),
            "net_amount": str(self.net_amount),
            "quota_class": self.quota_class,
            "description": self.description,
            "status": self.status,
        }


@dataclass
class CorporateEvent:
    """Evento corporativo anunciado pelo fundo (rendimentos ou amortização)."""
    event_id: str
    fund_id: str
    event_type: QuotaMovementType
    record_date: date
    ex_date: date
    payment_date: date
    rate_per_quota: Decimal
    tax_rate: Decimal = field(default_factory=lambda: Decimal("0.0000"))
    quota_class: Optional[str] = None
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, QuotaMovementType):
            self.event_type = QuotaMovementType.from_str(self.event_type)
        if self.event_type not in (QuotaMovementType.AMORTIZATION, QuotaMovementType.INCOME_DISTRIBUTION):
            raise FundsValidationError(
                f"Tipo de evento inválido para CorporateEvent: {self.event_type}. "
                "Permitidos: AMORTIZATION ou INCOME_DISTRIBUTION."
            )
        self.record_date = _to_date(self.record_date)
        self.ex_date = _to_date(self.ex_date)
        self.payment_date = _to_date(self.payment_date)
        self.rate_per_quota = _to_decimal(self.rate_per_quota)
        self.tax_rate = _to_decimal(self.tax_rate)
        if self.quota_class:
            self.quota_class = str(self.quota_class).strip().upper()

        if self.rate_per_quota <= Decimal("0"):
            raise FundsValidationError(f"Taxa por cota do evento deve ser estritamente positiva: {self.rate_per_quota}")
        if self.tax_rate < Decimal("0") or self.tax_rate > Decimal("1"):
            raise FundsValidationError(f"Alíquota tributária deve estar entre 0 e 1: {self.tax_rate}")
        if self.record_date > self.payment_date:
            raise FundsValidationError(
                f"Data-base ({self.record_date}) não pode ser posterior à data de pagamento ({self.payment_date})"
            )


@dataclass
class FundReconciliationBreak:
    """Quebra ou divergência isolada durante a conciliação de cotas."""
    break_type: FundBreakType
    fund_id: str
    custody_account: str
    investor_id: str
    internal_value: Optional[Decimal] = None
    statement_value: Optional[Decimal] = None
    diff: Optional[Decimal] = None
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "break_type": self.break_type.value,
            "fund_id": self.fund_id,
            "custody_account": self.custody_account,
            "investor_id": self.investor_id,
            "internal_value": str(self.internal_value) if self.internal_value is not None else None,
            "statement_value": str(self.statement_value) if self.statement_value is not None else None,
            "diff": str(self.diff) if self.diff is not None else None,
            "details": self.details,
        }


@dataclass
class FundReconciliationResult:
    """Resultado consolidado da conciliação do extrato de cotistas contra o livro interno."""
    as_of_date: date
    total_positions_checked: int
    matched_positions: int
    unmatched_positions: int
    breaks: List[FundReconciliationBreak] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """Retorna True se não houver nenhuma quebra de conciliação."""
        return len(self.breaks) == 0

    def summary(self) -> str:
        """Gera sumário textual executivo da conciliação."""
        status = "CONCILIADO 100% OK" if self.is_clean else f"DIVERGÊNCIAS DETECTADAS ({len(self.breaks)} quebras)"
        lines = [
            f"=== RELATÓRIO DE CONCILIAÇÃO DE FUNDOS B3 (Data: {self.as_of_date}) ===",
            f"Status Geral: {status}",
            f"Posições Verificadas: {self.total_positions_checked}",
            f"Posições Conciliadas: {self.matched_positions}",
            f"Posições com Divergência: {self.unmatched_positions}",
        ]
        if self.breaks:
            lines.append("Quebras Identificadas:")
            for idx, brk in enumerate(self.breaks, 1):
                lines.append(
                    f"  [{idx}] {brk.break_type.value} | Fundo: {brk.fund_id} | Conta: {brk.custody_account} | "
                    f"Int: {brk.internal_value} | Ext: {brk.statement_value} | Diff: {brk.diff} | Detalhes: {brk.details}"
                )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Motor de Fundos de Investimento (FundsPlatformEngine)
# ---------------------------------------------------------------------------

class FundsPlatformEngine:
    """Motor central de registro, apuração de eventos e conciliação de cotas B3."""

    def __init__(self) -> None:
        # Chave primária de posição: (fund_id, custody_account, quota_class)
        self._positions: Dict[Tuple[str, str, str], FundQuotaPosition] = {}
        self._transactions: List[QuotaTransaction] = []
        self._events_log: List[CorporateEvent] = []
        self._tx_counter: int = 1000

    def _next_tx_id(self, prefix: str = "TX-FUNDS") -> str:
        """Gera identificador sequencial único de transação."""
        self._tx_counter += 1
        return f"{prefix}-{datetime.now().strftime('%Y%m%d')}-{self._tx_counter:06d}"

    def register_position(self, position: FundQuotaPosition) -> None:
        """Cadastra ou atualiza uma posição no livro interno de custódia."""
        if not isinstance(position, FundQuotaPosition):
            raise FundsValidationError("Instância fornecida não é um FundQuotaPosition válido.")
        key = (position.fund_id, position.custody_account, position.quota_class)
        self._positions[key] = position

    def get_position(
        self,
        fund_id: str,
        custody_account: str,
        quota_class: Optional[str] = None
    ) -> Optional[FundQuotaPosition]:
        """Obtém posição por fundo, conta e classe opcional."""
        f_id = str(fund_id).strip()
        c_acc = str(custody_account).strip()
        if quota_class is not None:
            q_class = str(quota_class).strip().upper()
            return self._positions.get((f_id, c_acc, q_class))

        # Se não especificou classe, busca primeira correspondente
        for (fid, acc, _), pos in self._positions.items():
            if fid == f_id and acc == c_acc:
                return pos
        return None

    def list_positions(self, fund_id: Optional[str] = None) -> List[FundQuotaPosition]:
        """Retorna lista de todas as posições ou filtradas por fundo."""
        if fund_id is None:
            return list(self._positions.values())
        f_id = str(fund_id).strip()
        return [pos for pos in self._positions.values() if pos.fund_id == f_id]

    def list_transactions(self) -> List[QuotaTransaction]:
        """Retorna histórico de todas as transações processadas."""
        return list(self._transactions)

    # -----------------------------------------------------------------------
    # Operações Primárias e Movimentações
    # -----------------------------------------------------------------------

    def process_paid_in_capital(
        self,
        fund_id: str,
        fund_name: str,
        category: Union[FundCategory, str],
        custody_account: str,
        investor_id: str,
        quantity: Decimal,
        issue_price: Decimal,
        trade_date: date,
        settlement_date: date,
        quota_class: str = "UNICA",
        ticker: Optional[str] = None,
        isin: Optional[str] = None,
        investor_name: Optional[str] = None,
        description: str = "Integralizacao de cotas no mercado primario"
    ) -> QuotaTransaction:
        """Processa integralização de cotas: debita valor financeiro e credita cotas."""
        qty = _to_decimal(quantity)
        price = _to_decimal(issue_price)
        if qty <= Decimal("0"):
            raise FundsValidationError("Quantidade a integralizar deve ser positiva.")
        if price <= Decimal("0"):
            raise FundsValidationError("Preço unitário de emissão deve ser positivo.")

        gross_amount = (qty * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        f_id = str(fund_id).strip()
        c_acc = str(custody_account).strip()
        q_cls = str(quota_class).strip().upper()
        cat = FundCategory.from_str(category)

        # Atualiza ou cria posição
        pos = self.get_position(f_id, c_acc, q_cls)
        if pos:
            new_qty = pos.quota_quantity + qty
            pos.quota_quantity = new_qty
            pos.position_date = _to_date(settlement_date)
        else:
            pos = FundQuotaPosition(
                fund_id=f_id,
                fund_name=fund_name,
                category=cat,
                quota_class=q_cls,
                custody_account=c_acc,
                investor_id=investor_id,
                position_date=_to_date(settlement_date),
                quota_quantity=qty,
                quota_nav=price,
                ticker=ticker,
                isin=isin,
                investor_name=investor_name,
            )
            self.register_position(pos)

        tx = QuotaTransaction(
            transaction_id=self._next_tx_id("TX-INTEG"),
            fund_id=f_id,
            movement_type=QuotaMovementType.PAID_IN_CAPITAL,
            trade_date=_to_date(trade_date),
            settlement_date=_to_date(settlement_date),
            custody_account=c_acc,
            investor_id=investor_id,
            quantity=qty,
            unit_price=price,
            gross_amount=gross_amount,
            net_amount=gross_amount,
            quota_class=q_cls,
            description=description,
        )
        self._transactions.append(tx)
        return tx

    def process_redemption(
        self,
        fund_id: str,
        custody_account: str,
        quantity: Decimal,
        trade_date: date,
        settlement_date: date,
        quota_class: str = "UNICA",
        description: str = "Resgate de cotas em fundo aberto"
    ) -> QuotaTransaction:
        """Processa resgate de cotas: cancela cotas e apura crédito financeiro."""
        qty = _to_decimal(quantity)
        f_id = str(fund_id).strip()
        c_acc = str(custody_account).strip()
        q_cls = str(quota_class).strip().upper()

        pos = self.get_position(f_id, c_acc, q_cls)
        if not pos:
            raise FundsValidationError(f"Posição não encontrada para resgate: {f_id} conta {c_acc}")
        if pos.available_quantity < qty:
            raise FundsValidationError(
                f"Saldo disponível insuficiente para resgate. Solicitado: {qty}, Disponível: {pos.available_quantity}"
            )

        unit_nav = pos.quota_nav
        gross_amount = (qty * unit_nav).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        pos.quota_quantity -= qty
        pos.position_date = _to_date(settlement_date)

        tx = QuotaTransaction(
            transaction_id=self._next_tx_id("TX-RESG"),
            fund_id=f_id,
            movement_type=QuotaMovementType.REDEMPTION,
            trade_date=_to_date(trade_date),
            settlement_date=_to_date(settlement_date),
            custody_account=c_acc,
            investor_id=pos.investor_id,
            quantity=qty,
            unit_price=unit_nav,
            gross_amount=gross_amount,
            net_amount=gross_amount,
            quota_class=q_cls,
            description=description,
        )
        self._transactions.append(tx)
        return tx

    def process_transfer(
        self,
        fund_id: str,
        from_account: str,
        to_account: str,
        to_investor_id: str,
        to_investor_name: Optional[str],
        quantity: Decimal,
        transfer_date: date,
        quota_class: str = "UNICA",
        description: str = "Transferencia de cotas escriturais STVM"
    ) -> Tuple[QuotaTransaction, QuotaTransaction]:
        """Realiza transferência / portabilidade de custódia entre contas (STVM)."""
        qty = _to_decimal(quantity)
        f_id = str(fund_id).strip()
        from_acc = str(from_account).strip()
        to_acc = str(to_account).strip()
        q_cls = str(quota_class).strip().upper()

        origin = self.get_position(f_id, from_acc, q_cls)
        if not origin:
            raise FundsValidationError(f"Conta de origem {from_acc} não possui posição no fundo {f_id}")
        if origin.available_quantity < qty:
            raise FundsValidationError(
                f"Saldo insuficiente na conta de origem {from_acc}. Disponível: {origin.available_quantity}, Solicitado: {qty}"
            )

        # Débito da origem
        origin.quota_quantity -= qty
        origin.position_date = _to_date(transfer_date)

        # Crédito no destino
        dest = self.get_position(f_id, to_acc, q_cls)
        if dest:
            dest.quota_quantity += qty
            dest.position_date = _to_date(transfer_date)
        else:
            dest = FundQuotaPosition(
                fund_id=f_id,
                fund_name=origin.fund_name,
                category=origin.category,
                quota_class=q_cls,
                custody_account=to_acc,
                investor_id=to_investor_id,
                position_date=_to_date(transfer_date),
                quota_quantity=qty,
                quota_nav=origin.quota_nav,
                ticker=origin.ticker,
                isin=origin.isin,
                investor_name=to_investor_name,
            )
            self.register_position(dest)

        tx_out = QuotaTransaction(
            transaction_id=self._next_tx_id("TX-STVM-OUT"),
            fund_id=f_id,
            movement_type=QuotaMovementType.TRANSFER_OUT,
            trade_date=_to_date(transfer_date),
            settlement_date=_to_date(transfer_date),
            custody_account=from_acc,
            investor_id=origin.investor_id,
            quantity=qty,
            unit_price=origin.quota_nav,
            gross_amount=Decimal("0.00"),
            net_amount=Decimal("0.00"),
            quota_class=q_cls,
            description=f"{description} (Para: {to_acc})",
        )
        tx_in = QuotaTransaction(
            transaction_id=self._next_tx_id("TX-STVM-IN"),
            fund_id=f_id,
            movement_type=QuotaMovementType.TRANSFER_IN,
            trade_date=_to_date(transfer_date),
            settlement_date=_to_date(transfer_date),
            custody_account=to_acc,
            investor_id=to_investor_id,
            quantity=qty,
            unit_price=origin.quota_nav,
            gross_amount=Decimal("0.00"),
            net_amount=Decimal("0.00"),
            quota_class=q_cls,
            description=f"{description} (De: {from_acc})",
        )

        self._transactions.extend([tx_out, tx_in])
        return tx_out, tx_in

    # -----------------------------------------------------------------------
    # Apuração de Eventos Corporativos (Rendimentos e Amortizações)
    # -----------------------------------------------------------------------

    def distribute_income(self, event: CorporateEvent) -> List[QuotaTransaction]:
        """Apurar e creditar distribuição periódica de rendimentos/proventos por cota.

        Regras:
        - Aplica-se às posições do fundo detidas na record_date.
        - Crédito financeiro exato: Quantidade * rate_per_quota.
        - Não altera a quantidade de cotas nem o NAV do fundo.
        """
        if event.event_type != QuotaMovementType.INCOME_DISTRIBUTION:
            raise FundsEventError(f"Tipo de evento inválido para distribute_income: {event.event_type}")

        f_id = event.fund_id.strip()
        q_cls = event.quota_class.strip().upper() if event.quota_class else None
        generated_txs: List[QuotaTransaction] = []

        matching_positions = [
            pos for pos in self._positions.values()
            if pos.fund_id == f_id and (q_cls is None or pos.quota_class == q_cls)
        ]

        if not matching_positions:
            raise FundsEventError(f"Nenhuma posição ativa encontrada para o fundo {f_id} no evento {event.event_id}")

        for pos in matching_positions:
            if pos.quota_quantity <= Decimal("0"):
                continue

            gross = (pos.quota_quantity * event.rate_per_quota).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            tax = (gross * event.tax_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            net = gross - tax

            tx = QuotaTransaction(
                transaction_id=self._next_tx_id("TX-REND"),
                fund_id=pos.fund_id,
                movement_type=QuotaMovementType.INCOME_DISTRIBUTION,
                trade_date=event.record_date,
                settlement_date=event.payment_date,
                custody_account=pos.custody_account,
                investor_id=pos.investor_id,
                quantity=pos.quota_quantity,
                unit_price=event.rate_per_quota,
                gross_amount=gross,
                tax_amount=tax,
                net_amount=net,
                quota_class=pos.quota_class,
                description=event.description or f"Distribuicao de rendimentos ({event.event_id})",
            )
            self._transactions.append(tx)
            generated_txs.append(tx)

        self._events_log.append(event)
        return generated_txs

    def amortize_principal(self, event: CorporateEvent) -> Tuple[List[QuotaTransaction], Decimal]:
        """Processa amortização periódica de principal.

        Regras:
        - Crédito financeiro aos cotistas: Quantidade * rate_per_quota.
        - Reduz o NAV unitário da cota: NAV_novo = NAV_antigo - rate_per_quota.
        - A quantidade de cotas permanece inalterada.
        - O novo NAV não pode ser negativo.
        """
        if event.event_type != QuotaMovementType.AMORTIZATION:
            raise FundsEventError(f"Tipo de evento inválido para amortize_principal: {event.event_type}")

        f_id = event.fund_id.strip()
        q_cls = event.quota_class.strip().upper() if event.quota_class else None
        generated_txs: List[QuotaTransaction] = []

        matching_positions = [
            pos for pos in self._positions.values()
            if pos.fund_id == f_id and (q_cls is None or pos.quota_class == q_cls)
        ]

        if not matching_positions:
            raise FundsEventError(f"Nenhuma posição ativa encontrada para o fundo {f_id} no evento {event.event_id}")

        # Validação prévia de NAV suficiente em todas as posições
        for pos in matching_positions:
            if pos.quota_nav < event.rate_per_quota:
                raise FundsValidationError(
                    f"Valor de amortização ({event.rate_per_quota}) excede o NAV da cota ({pos.quota_nav}) "
                    f"na conta {pos.custody_account}"
                )

        new_nav = Decimal("0")
        for pos in matching_positions:
            gross = (pos.quota_quantity * event.rate_per_quota).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            tax = (gross * event.tax_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            net = gross - tax

            # Redução do NAV unitário da cota
            pos.quota_nav = pos.quota_nav - event.rate_per_quota
            new_nav = pos.quota_nav

            tx = QuotaTransaction(
                transaction_id=self._next_tx_id("TX-AMORT"),
                fund_id=pos.fund_id,
                movement_type=QuotaMovementType.AMORTIZATION,
                trade_date=event.record_date,
                settlement_date=event.payment_date,
                custody_account=pos.custody_account,
                investor_id=pos.investor_id,
                quantity=pos.quota_quantity,
                unit_price=event.rate_per_quota,
                gross_amount=gross,
                tax_amount=tax,
                net_amount=net,
                quota_class=pos.quota_class,
                description=event.description or f"Amortizacao de principal ({event.event_id})",
            )
            self._transactions.append(tx)
            generated_txs.append(tx)

        self._events_log.append(event)
        return generated_txs, new_nav

    def update_nav(
        self,
        fund_id: str,
        new_nav: Decimal,
        position_date: date,
        quota_class: Optional[str] = None
    ) -> int:
        """Atualiza o valor patrimonial da cota (NAV) de todas as posições do fundo."""
        n_nav = _to_decimal(new_nav)
        if n_nav < Decimal("0"):
            raise FundsValidationError(f"Novo NAV da cota não pode ser negativo: {new_nav}")

        f_id = str(fund_id).strip()
        q_cls = str(quota_class).strip().upper() if quota_class else None
        p_date = _to_date(position_date)
        count = 0

        for pos in self._positions.values():
            if pos.fund_id == f_id and (q_cls is None or pos.quota_class == q_cls):
                pos.quota_nav = n_nav
                pos.position_date = p_date
                count += 1
        return count

    # -----------------------------------------------------------------------
    # Carga de Extrato B3 e Conciliação Determinística
    # -----------------------------------------------------------------------

    @staticmethod
    def load_statement_from_json(source: Union[str, Path, Dict[str, Any]]) -> List[FundQuotaPosition]:
        """Carrega e desserializa posições de um extrato JSON B3 oficial."""
        data: Dict[str, Any]
        if isinstance(source, (str, Path)):
            p = Path(source)
            if not p.is_file():
                raise FundsValidationError(f"Arquivo de extrato não encontrado: {p}")
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
        elif isinstance(source, dict):
            data = source
        else:
            raise FundsValidationError(f"Fonte de dados inválida para extrato: {type(source)}")

        position_date = _to_date(data.get("position_date", date.today()))
        positions: List[FundQuotaPosition] = []

        funds_list = data.get("funds", [])
        for f_data in funds_list:
            f_id = f_data["fund_id"]
            f_name = f_data.get("fund_name", "")
            cat = FundCategory.from_str(f_data["category"])
            default_nav = _to_decimal(f_data.get("quota_nav", "0.00000000"))
            ticker = f_data.get("ticker")
            isin = f_data.get("isin")

            for p_data in f_data.get("positions", []):
                q_nav = _to_decimal(p_data.get("quota_nav", default_nav))
                pos = FundQuotaPosition(
                    fund_id=f_id,
                    fund_name=f_name,
                    category=cat,
                    quota_class=p_data.get("quota_class", "UNICA"),
                    custody_account=p_data["custody_account"],
                    investor_id=p_data["investor_id"],
                    investor_name=p_data.get("investor_name"),
                    position_date=position_date,
                    quota_quantity=_to_decimal(p_data["quota_quantity"]),
                    quota_nav=q_nav,
                    blocked_quantity=_to_decimal(p_data.get("blocked_quantity", "0.00000000")),
                    pending_settlement_quantity=_to_decimal(p_data.get("pending_settlement_quantity", "0.00000000")),
                    ticker=ticker,
                    isin=isin,
                )
                positions.append(pos)

        return positions

    def reconcile_statement(
        self,
        statement_positions: Sequence[FundQuotaPosition],
        tolerance: Decimal = Decimal("0.0001"),
        as_of_date: Optional[date] = None
    ) -> FundReconciliationResult:
        """Realiza conciliação determinística entre o extrato B3 e o livro interno.

        Detecta:
        - QUANTITY_MISMATCH: divergência de quantidade de cotas > tolerance;
        - NAV_MISMATCH: divergência no valor patrimonial da cota > tolerance;
        - EQUITY_MISMATCH: divergência no valor patrimonial total da conta > 0.01;
        - MISSING_IN_INTERNAL: posição informada no extrato B3, mas ausente no livro interno;
        - MISSING_IN_STATEMENT: posição registrada no livro interno, mas ausente no extrato B3.
        """
        ref_date = as_of_date or (statement_positions[0].position_date if statement_positions else date.today())
        breaks: List[FundReconciliationBreak] = []

        stmt_map: Dict[Tuple[str, str, str], FundQuotaPosition] = {
            (p.fund_id, p.custody_account, p.quota_class): p for p in statement_positions
        }
        internal_map: Dict[Tuple[str, str, str], FundQuotaPosition] = dict(self._positions)

        all_keys = set(stmt_map.keys()).union(set(internal_map.keys()))
        matched_count = 0
        unmatched_count = 0

        for key in sorted(all_keys):
            f_id, c_acc, q_cls = key
            stmt_pos = stmt_map.get(key)
            int_pos = internal_map.get(key)

            if stmt_pos is not None and int_pos is None:
                # Posição na B3 ausente no livro interno
                unmatched_count += 1
                breaks.append(
                    FundReconciliationBreak(
                        break_type=FundBreakType.MISSING_IN_INTERNAL,
                        fund_id=f_id,
                        custody_account=c_acc,
                        investor_id=stmt_pos.investor_id,
                        internal_value=None,
                        statement_value=stmt_pos.quota_quantity,
                        diff=stmt_pos.quota_quantity,
                        details=(
                            f"Posição de {stmt_pos.quota_quantity} cotas (classe {q_cls}) informada no extrato B3, "
                            "porém ausente no livro interno de custódia."
                        ),
                    )
                )
                continue

            if stmt_pos is None and int_pos is not None:
                # Posição interna ausente no extrato da B3
                if int_pos.quota_quantity > Decimal("0"):
                    unmatched_count += 1
                    breaks.append(
                        FundReconciliationBreak(
                            break_type=FundBreakType.MISSING_IN_STATEMENT,
                            fund_id=f_id,
                            custody_account=c_acc,
                            investor_id=int_pos.investor_id,
                            internal_value=int_pos.quota_quantity,
                            statement_value=None,
                            diff=int_pos.quota_quantity,
                            details=(
                                f"Posição interna de {int_pos.quota_quantity} cotas (classe {q_cls}) "
                                "não consta no extrato oficial da registradora B3."
                            ),
                        )
                    )
                continue

            # Ambos existem: comparar atributos
            assert stmt_pos is not None and int_pos is not None
            has_break = False

            # 1. Divergência de Quantidade de Cotas
            qty_diff = abs(int_pos.quota_quantity - stmt_pos.quota_quantity)
            if qty_diff > tolerance:
                has_break = True
                breaks.append(
                    FundReconciliationBreak(
                        break_type=FundBreakType.QUANTITY_MISMATCH,
                        fund_id=f_id,
                        custody_account=c_acc,
                        investor_id=int_pos.investor_id,
                        internal_value=int_pos.quota_quantity,
                        statement_value=stmt_pos.quota_quantity,
                        diff=int_pos.quota_quantity - stmt_pos.quota_quantity,
                        details=(
                            f"Divergência de quantidade de cotas: Livro={int_pos.quota_quantity}, "
                            f"Extrato={stmt_pos.quota_quantity}, Diff={int_pos.quota_quantity - stmt_pos.quota_quantity}"
                        ),
                    )
                )

            # 2. Divergência de Valor Patrimonial da Cota (NAV)
            nav_diff = abs(int_pos.quota_nav - stmt_pos.quota_nav)
            if nav_diff > tolerance:
                has_break = True
                breaks.append(
                    FundReconciliationBreak(
                        break_type=FundBreakType.NAV_MISMATCH,
                        fund_id=f_id,
                        custody_account=c_acc,
                        investor_id=int_pos.investor_id,
                        internal_value=int_pos.quota_nav,
                        statement_value=stmt_pos.quota_nav,
                        diff=int_pos.quota_nav - stmt_pos.quota_nav,
                        details=(
                            f"Divergência de NAV unitário: Livro={int_pos.quota_nav}, "
                            f"Extrato={stmt_pos.quota_nav}, Diff={int_pos.quota_nav - stmt_pos.quota_nav}"
                        ),
                    )
                )

            # 3. Divergência de Patrimônio Líquido Total
            eq_diff = abs(int_pos.total_equity_value - stmt_pos.total_equity_value)
            if eq_diff > Decimal("0.01"):
                has_break = True
                breaks.append(
                    FundReconciliationBreak(
                        break_type=FundBreakType.EQUITY_MISMATCH,
                        fund_id=f_id,
                        custody_account=c_acc,
                        investor_id=int_pos.investor_id,
                        internal_value=int_pos.total_equity_value,
                        statement_value=stmt_pos.total_equity_value,
                        diff=int_pos.total_equity_value - stmt_pos.total_equity_value,
                        details=(
                            f"Divergência de valor financeiro total da posição: Livro={int_pos.total_equity_value}, "
                            f"Extrato={stmt_pos.total_equity_value}, Diff={int_pos.total_equity_value - stmt_pos.total_equity_value}"
                        ),
                    )
                )

            if has_break:
                unmatched_count += 1
            else:
                matched_count += 1

        return FundReconciliationResult(
            as_of_date=ref_date,
            total_positions_checked=len(all_keys),
            matched_positions=matched_count,
            unmatched_positions=unmatched_count,
            breaks=breaks,
        )


__all__ = [
    "FundBreakType",
    "FundCategory",
    "FundQuotaPosition",
    "FundReconciliationBreak",
    "FundReconciliationResult",
    "FundsEventError",
    "FundsPlatformEngine",
    "FundsPlatformError",
    "FundsReconciliationError",
    "FundsValidationError",
    "QuotaClassType",
    "QuotaMovementType",
    "QuotaTransaction",
    "CorporateEvent",
]
