"""Motor de Eventos Corporativos em Empréstimo de Ativos (BTC em Aberto) — B3 (FL-048).

Implementação 100% pura e determinística em Python stdlib para o tratamento de
eventos corporativos e repasse de proventos em contratos de empréstimo de ativos
(Banco de Títulos CBLC - BTC da B3), em conformidade com:
- Manual de Procedimentos Operacionais da Câmara B3 (Compensação e Liquidação).
- Lei Federal nº 9.249/1995 (Tributação de Dividendos e JCP).
- Instrução Normativa RFB nº 1.585/2015, Art. 13 (Compensação Tributária em BTC).
- Conservação invariante do valor econômico dos contratos de empréstimo em aberto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union


# ==============================================================================
# 1. Enumerações e Códigos Canônicos da B3
# ==============================================================================

class BtcEventType(str, Enum):
    """Tipologia de eventos corporativos aplicáveis aos contratos BTC da B3."""
    DIVIDEND = "DIVIDEND"                  # Dividendo em dinheiro (isento de IRRF)
    JCP = "JCP"                            # Juros sobre Capital Próprio (tributado 15% IRRF)
    BONUS = "BONUS"                        # Bonificação em ações (incorporação de reservas)
    SPLIT = "SPLIT"                        # Desdobramento de ações (forward split)
    REVERSE_SPLIT = "REVERSE_SPLIT"        # Grupamento de ações (reverse split)
    SUBSCRIPTION_RIGHT = "SUBSCRIPTION_RIGHT"  # Direitos de subscrição de ações

    @property
    def is_cash_event(self) -> bool:
        """Indica se o evento corporativo gera liquidação financeira direta."""
        return self in (BtcEventType.DIVIDEND, BtcEventType.JCP)

    @property
    def is_stock_event(self) -> bool:
        """Indica se o evento corporativo altera quantidade e preço do contrato."""
        return self in (BtcEventType.BONUS, BtcEventType.SPLIT, BtcEventType.REVERSE_SPLIT)

    @property
    def is_rights_event(self) -> bool:
        """Indica se o evento envolve direitos de preferência / subscrição."""
        return self == BtcEventType.SUBSCRIPTION_RIGHT

    @classmethod
    def from_value(cls, val: Any) -> BtcEventType:
        """Converte strings e códigos habituais da B3 (e.g. IMBARQ018) para BtcEventType."""
        if isinstance(val, BtcEventType):
            return val
        if isinstance(val, str):
            v = val.strip().upper()
            if v in ("DIVIDEND", "DIVID", "DIV", "DIVIDENDOS"):
                return cls.DIVIDEND
            if v in ("JCP", "JUROS", "INTEREST_ON_EQUITY", "JUROS_CAPITAL_PROPRIO"):
                return cls.JCP
            if v in ("BONUS", "BONIF", "BONIFICACAO", "STOCK_BONUS"):
                return cls.BONUS
            if v in ("SPLIT", "DESD", "DESDOBRAMENTO", "FORWARD_SPLIT"):
                return cls.SPLIT
            if v in ("REVERSE_SPLIT", "AGRUP", "AGRUPAMENTO", "REVERSE"):
                return cls.REVERSE_SPLIT
            if v in ("SUBSCRIPTION_RIGHT", "SUBSC", "SUBSCRICAO", "RIGHTS", "RIGHT"):
                return cls.SUBSCRIPTION_RIGHT
        raise ValueError(f"Tipo de evento corporativo BTC inválido ou desconhecido: {val!r}")


# ==============================================================================
# 2. Exceções Especializadas
# ==============================================================================

class BtcCorporateActionError(Exception):
    """Exceção base para erros no processamento de eventos corporativos em BTC."""
    pass


class IneligibleContractError(BtcCorporateActionError):
    """Lançada quando um contrato não é elegível para o evento corporativo."""
    pass


class ContractConservationError(BtcCorporateActionError):
    """Lançada quando o princípio de conservação de valor financeiro é violado."""
    pass


# ==============================================================================
# 3. Modelos de Dados Tipados (Dataclasses)
# ==============================================================================

@dataclass
class BtcLoanContract:
    """Contrato de empréstimo de ativos BTC em aberto na Câmara B3."""
    contract_number: int
    symbol: str
    isin: str
    lender_participant_id: int
    lender_account: int
    borrower_participant_id: int
    borrower_account: int
    quantity: int
    base_price: Decimal
    contract_value: Decimal = Decimal("0.00")
    rate: Decimal = Decimal("0.0000")
    open_date: date = field(default_factory=date.today)
    maturity_date: date = field(default_factory=date.today)
    status: str = "OPEN"
    is_tax_exempt: bool = False

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"Quantidade do contrato deve ser estritamente positiva: {self.quantity}")
        if not isinstance(self.base_price, Decimal):
            self.base_price = Decimal(str(self.base_price))
        if self.base_price <= Decimal("0"):
            raise ValueError(f"Preço-base do contrato deve ser estritamente positivo: {self.base_price}")
        if not isinstance(self.contract_value, Decimal) or self.contract_value <= Decimal("0"):
            self.contract_value = (Decimal(self.quantity) * self.base_price).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        if not isinstance(self.rate, Decimal):
            self.rate = Decimal(str(self.rate))
        if isinstance(self.open_date, str):
            self.open_date = date.fromisoformat(self.open_date)
        if isinstance(self.maturity_date, str):
            self.maturity_date = date.fromisoformat(self.maturity_date)

    @property
    def is_open(self) -> bool:
        """Verifica se o contrato está ativo / em aberto."""
        return self.status.upper() == "OPEN"

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o contrato em dicionário compatível com JSON."""
        return {
            "contract_number": self.contract_number,
            "symbol": self.symbol,
            "isin": self.isin,
            "lender_participant_id": self.lender_participant_id,
            "lender_account": self.lender_account,
            "borrower_participant_id": self.borrower_participant_id,
            "borrower_account": self.borrower_account,
            "quantity": self.quantity,
            "base_price": str(self.base_price),
            "contract_value": str(self.contract_value),
            "rate": str(self.rate),
            "open_date": self.open_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "status": self.status,
            "is_tax_exempt": self.is_tax_exempt,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BtcLoanContract:
        """Cria uma instância a partir de dicionário."""
        return cls(
            contract_number=int(data["contract_number"]),
            symbol=str(data["symbol"]).strip().upper(),
            isin=str(data["isin"]).strip().upper(),
            lender_participant_id=int(data["lender_participant_id"]),
            lender_account=int(data["lender_account"]),
            borrower_participant_id=int(data["borrower_participant_id"]),
            borrower_account=int(data["borrower_account"]),
            quantity=int(data["quantity"]),
            base_price=Decimal(str(data["base_price"])),
            contract_value=Decimal(str(data.get("contract_value", "0.00"))),
            rate=Decimal(str(data.get("rate", "0.0000"))),
            open_date=date.fromisoformat(data["open_date"]) if isinstance(data["open_date"], str) else data["open_date"],
            maturity_date=date.fromisoformat(data["maturity_date"]) if isinstance(data["maturity_date"], str) else data["maturity_date"],
            status=str(data.get("status", "OPEN")),
            is_tax_exempt=bool(data.get("is_tax_exempt", False)),
        )


@dataclass
class CorporateEventAnnouncement:
    """Anúncio deliberado de evento corporativo em data ex (compatível com IMBARQ018)."""
    event_id: str
    symbol: str
    isin: str
    event_type: BtcEventType
    record_date: date  # Data Com (Data de corte de custódia)
    ex_date: date      # Data Ex (Início da negociação ex)
    payment_date: Optional[date] = None  # Data de Liquidação / Homologação
    gross_rate_per_share: Decimal = Decimal("0.000000")  # Para DIVIDEND / JCP
    withholding_tax_rate: Decimal = Decimal("0.0000")    # 0.1500 para JCP padrão
    tax_exemption: bool = False                          # Se True, alíquota de IRRF = 0.0
    ratio_factor: Decimal = Decimal("0.0000")            # Fator multiplicador ou de bonificação
    ratio_from: int = 1                                  # Razão base (e.g. 10 em 10:1)
    ratio_to: int = 1                                    # Razão resultante (e.g. 1 em 10:1, ou 3 em 1:3)
    percentage: Decimal = Decimal("0.0000")              # Percentual (e.g. 10.0000%)
    subscription_ratio: Decimal = Decimal("0.0000")      # Razão de subscrição por ação
    subscription_price: Decimal = Decimal("0.00")        # Preço de emissão da nova ação
    right_market_value: Decimal = Decimal("0.00")        # Preço de mercado do direito para liquidação financeira
    right_symbol: str = ""                               # Ticker do direito (e.g. PETR1)
    description: str = ""

    def __post_init__(self) -> None:
        self.symbol = self.symbol.strip().upper()
        self.isin = self.isin.strip().upper()
        self.event_type = BtcEventType.from_value(self.event_type)

        if isinstance(self.record_date, str):
            self.record_date = date.fromisoformat(self.record_date)
        if isinstance(self.ex_date, str):
            self.ex_date = date.fromisoformat(self.ex_date)
        if isinstance(self.payment_date, str):
            self.payment_date = date.fromisoformat(self.payment_date)
        elif self.payment_date is None:
            self.payment_date = self.ex_date

        if not isinstance(self.gross_rate_per_share, Decimal):
            self.gross_rate_per_share = Decimal(str(self.gross_rate_per_share))
        if not isinstance(self.withholding_tax_rate, Decimal):
            self.withholding_tax_rate = Decimal(str(self.withholding_tax_rate))
        if not isinstance(self.ratio_factor, Decimal):
            self.ratio_factor = Decimal(str(self.ratio_factor))
        if not isinstance(self.percentage, Decimal):
            self.percentage = Decimal(str(self.percentage))
        if not isinstance(self.subscription_ratio, Decimal):
            self.subscription_ratio = Decimal(str(self.subscription_ratio))
        if not isinstance(self.subscription_price, Decimal):
            self.subscription_price = Decimal(str(self.subscription_price))
        if not isinstance(self.right_market_value, Decimal):
            self.right_market_value = Decimal(str(self.right_market_value))

        # Configuração de alíquota padrão para JCP caso não especificada
        if self.event_type == BtcEventType.JCP and self.withholding_tax_rate == Decimal("0.0000") and not self.tax_exemption:
            self.withholding_tax_rate = Decimal("0.1500")

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o evento corporativo em dict."""
        return {
            "event_id": self.event_id,
            "symbol": self.symbol,
            "isin": self.isin,
            "event_type": self.event_type.value,
            "record_date": self.record_date.isoformat(),
            "ex_date": self.ex_date.isoformat(),
            "payment_date": self.payment_date.isoformat() if self.payment_date else None,
            "gross_rate_per_share": str(self.gross_rate_per_share),
            "withholding_tax_rate": str(self.withholding_tax_rate),
            "tax_exemption": self.tax_exemption,
            "ratio_factor": str(self.ratio_factor),
            "ratio_from": self.ratio_from,
            "ratio_to": self.ratio_to,
            "percentage": str(self.percentage),
            "subscription_ratio": str(self.subscription_ratio),
            "subscription_price": str(self.subscription_price),
            "right_market_value": str(self.right_market_value),
            "right_symbol": self.right_symbol,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> CorporateEventAnnouncement:
        """Cria anúncio a partir de dicionário."""
        return cls(
            event_id=str(d["event_id"]),
            symbol=str(d["symbol"]),
            isin=str(d["isin"]),
            event_type=BtcEventType.from_value(d["event_type"]),
            record_date=date.fromisoformat(d["record_date"]) if isinstance(d["record_date"], str) else d["record_date"],
            ex_date=date.fromisoformat(d["ex_date"]) if isinstance(d["ex_date"], str) else d["ex_date"],
            payment_date=date.fromisoformat(d["payment_date"]) if d.get("payment_date") else None,
            gross_rate_per_share=Decimal(str(d.get("gross_rate_per_share", "0.000000"))),
            withholding_tax_rate=Decimal(str(d.get("withholding_tax_rate", "0.0000"))),
            tax_exemption=bool(d.get("tax_exemption", False)),
            ratio_factor=Decimal(str(d.get("ratio_factor", "0.0000"))),
            ratio_from=int(d.get("ratio_from", 1)),
            ratio_to=int(d.get("ratio_to", 1)),
            percentage=Decimal(str(d.get("percentage", "0.0000"))),
            subscription_ratio=Decimal(str(d.get("subscription_ratio", "0.0000"))),
            subscription_price=Decimal(str(d.get("subscription_price", "0.00"))),
            right_market_value=Decimal(str(d.get("right_market_value", "0.00"))),
            right_symbol=str(d.get("right_symbol", "")),
            description=str(d.get("description", "")),
        )


@dataclass
class BtcAdjustmentReceipt:
    """Comprovante de ajuste e liquidação de evento corporativo em contrato BTC."""
    receipt_id: str
    contract_number: int
    symbol: str
    isin: str
    event_id: str
    event_type: BtcEventType
    effective_date: date
    lender_participant_id: int
    lender_account: int
    borrower_participant_id: int
    borrower_account: int

    # Proventos em Ações (Bonificação, Split, Grupamento)
    original_quantity: int = 0
    adjusted_quantity: int = 0
    delta_quantity: int = 0
    original_base_price: Decimal = Decimal("0.00")
    adjusted_base_price: Decimal = Decimal("0.00")
    original_contract_value: Decimal = Decimal("0.00")
    adjusted_contract_value: Decimal = Decimal("0.00")
    fractional_shares: int = 0
    fraction_cash_settlement: Decimal = Decimal("0.00")

    # Proventos em Dinheiro (Dividendo, JCP)
    gross_cash_amount: Decimal = Decimal("0.00")
    withholding_tax_rate: Decimal = Decimal("0.0000")
    withholding_tax_amount: Decimal = Decimal("0.00")
    net_cash_amount: Decimal = Decimal("0.00")
    borrower_debit_amount: Decimal = Decimal("0.00")
    lender_credit_amount: Decimal = Decimal("0.00")
    tax_compensation_credit: Decimal = Decimal("0.00")
    payment_date: Optional[date] = None

    # Direitos de Subscrição
    rights_allocated: int = 0
    rights_exercised: bool = False
    rights_cash_settlement: Decimal = Decimal("0.00")
    rights_exercise_cost: Decimal = Decimal("0.00")

    # Status e integridade
    status: str = "APPLIED"
    value_conserved: bool = True
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o comprovante de ajuste em dicionário compatível com JSON."""
        return {
            "receipt_id": self.receipt_id,
            "contract_number": self.contract_number,
            "symbol": self.symbol,
            "isin": self.isin,
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "effective_date": self.effective_date.isoformat(),
            "lender_participant_id": self.lender_participant_id,
            "lender_account": self.lender_account,
            "borrower_participant_id": self.borrower_participant_id,
            "borrower_account": self.borrower_account,
            "original_quantity": self.original_quantity,
            "adjusted_quantity": self.adjusted_quantity,
            "delta_quantity": self.delta_quantity,
            "original_base_price": str(self.original_base_price),
            "adjusted_base_price": str(self.adjusted_base_price),
            "original_contract_value": str(self.original_contract_value),
            "adjusted_contract_value": str(self.adjusted_contract_value),
            "fractional_shares": self.fractional_shares,
            "fraction_cash_settlement": str(self.fraction_cash_settlement),
            "gross_cash_amount": str(self.gross_cash_amount),
            "withholding_tax_rate": str(self.withholding_tax_rate),
            "withholding_tax_amount": str(self.withholding_tax_amount),
            "net_cash_amount": str(self.net_cash_amount),
            "borrower_debit_amount": str(self.borrower_debit_amount),
            "lender_credit_amount": str(self.lender_credit_amount),
            "tax_compensation_credit": str(self.tax_compensation_credit),
            "payment_date": self.payment_date.isoformat() if self.payment_date else None,
            "rights_allocated": self.rights_allocated,
            "rights_exercised": self.rights_exercised,
            "rights_cash_settlement": str(self.rights_cash_settlement),
            "rights_exercise_cost": str(self.rights_exercise_cost),
            "status": self.status,
            "value_conserved": self.value_conserved,
            "notes": self.notes,
        }


# ==============================================================================
# 4. Motor Determinístico de Tratamento de Eventos Corporativos BTC
# ==============================================================================

class BtcCorporateActionsEngine:
    """Motor de cálculo e ajuste de proventos em contratos BTC da Câmara B3."""

    def __init__(self) -> None:
        self.contracts: Dict[int, BtcLoanContract] = {}
        self.events: Dict[str, CorporateEventAnnouncement] = {}
        self.receipts: List[BtcAdjustmentReceipt] = []

    def register_contract(self, contract: BtcLoanContract) -> None:
        """Registra ou atualiza um contrato de empréstimo de ativos no motor."""
        self.contracts[contract.contract_number] = contract

    def register_event(self, event: CorporateEventAnnouncement) -> None:
        """Registra um evento corporativo anunciado no motor."""
        self.events[event.event_id] = event

    def get_open_contracts_for_event(
        self, event: CorporateEventAnnouncement
    ) -> List[BtcLoanContract]:
        """Localiza contratos elegíveis em aberto na data de corte (Data Com)."""
        eligible = []
        for contract in self.contracts.values():
            if not contract.is_open:
                continue
            if contract.symbol != event.symbol and contract.isin != event.isin:
                continue
            # O contrato deve ter sido aberto até a Data Com e seu vencimento ser >= Data Ex
            if contract.open_date <= event.record_date and contract.maturity_date >= event.ex_date:
                eligible.append(contract)
        return eligible

    # --------------------------------------------------------------------------
    # 4.1 Cálculos Especializados por Categoria
    # --------------------------------------------------------------------------

    @staticmethod
    def calculate_dividend(
        contract: BtcLoanContract, event: CorporateEventAnnouncement
    ) -> BtcAdjustmentReceipt:
        """Calcula repasse integral de dividendos em dinheiro (isento de IRRF)."""
        if event.gross_rate_per_share <= Decimal("0"):
            raise ValueError(f"Valor bruto de provento por ação deve ser positivo: {event.gross_rate_per_share}")

        gross_amount = (Decimal(contract.quantity) * event.gross_rate_per_share).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        tax_rate = Decimal("0.0000")
        tax_amount = Decimal("0.00")
        net_amount = gross_amount

        # O Tomador é debitado no valor bruto; o Doador recebe crédito integral
        borrower_debit = gross_amount
        lender_credit = net_amount
        tax_compensation = Decimal("0.00")

        receipt_id = f"REC-DIV-{contract.contract_number}-{event.event_id}"
        return BtcAdjustmentReceipt(
            receipt_id=receipt_id,
            contract_number=contract.contract_number,
            symbol=contract.symbol,
            isin=contract.isin,
            event_id=event.event_id,
            event_type=BtcEventType.DIVIDEND,
            effective_date=event.ex_date,
            lender_participant_id=contract.lender_participant_id,
            lender_account=contract.lender_account,
            borrower_participant_id=contract.borrower_participant_id,
            borrower_account=contract.borrower_account,
            original_quantity=contract.quantity,
            adjusted_quantity=contract.quantity,
            delta_quantity=0,
            original_base_price=contract.base_price,
            adjusted_base_price=contract.base_price,
            original_contract_value=contract.contract_value,
            adjusted_contract_value=contract.contract_value,
            gross_cash_amount=gross_amount,
            withholding_tax_rate=tax_rate,
            withholding_tax_amount=tax_amount,
            net_cash_amount=net_amount,
            borrower_debit_amount=borrower_debit,
            lender_credit_amount=lender_credit,
            tax_compensation_credit=tax_compensation,
            payment_date=event.payment_date,
            status="APPLIED",
            value_conserved=True,
            notes="Repasse de dividendos em dinheiro integral conforme Lei 9.249/95 (isento de IRRF).",
        )

    @staticmethod
    def calculate_jcp(
        contract: BtcLoanContract, event: CorporateEventAnnouncement
    ) -> BtcAdjustmentReceipt:
        """Calcula repasse de JCP com retenção de IRRF e crédito tributário compensável."""
        if event.gross_rate_per_share <= Decimal("0"):
            raise ValueError(f"Valor bruto de JCP por ação deve ser positivo: {event.gross_rate_per_share}")

        gross_amount = (Decimal(contract.quantity) * event.gross_rate_per_share).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        is_exempt = contract.is_tax_exempt or event.tax_exemption
        if is_exempt:
            tax_rate = Decimal("0.0000")
            tax_amount = Decimal("0.00")
            net_amount = gross_amount
            tax_compensation = Decimal("0.00")
        else:
            tax_rate = event.withholding_tax_rate if event.withholding_tax_rate > Decimal("0") else Decimal("0.1500")
            tax_amount = (gross_amount * tax_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            net_amount = gross_amount - tax_amount
            tax_compensation = tax_amount

        # Conforme Manual da Câmara B3 e IN RFB 1.585/2015:
        # Tomador é debitado pelo valor bruto; Doador recebe crédito líquido (ou bruto se isento);
        # Tomador tem direito ao crédito tributário compensável no valor retido.
        borrower_debit = gross_amount
        lender_credit = net_amount

        receipt_id = f"REC-JCP-{contract.contract_number}-{event.event_id}"
        return BtcAdjustmentReceipt(
            receipt_id=receipt_id,
            contract_number=contract.contract_number,
            symbol=contract.symbol,
            isin=contract.isin,
            event_id=event.event_id,
            event_type=BtcEventType.JCP,
            effective_date=event.ex_date,
            lender_participant_id=contract.lender_participant_id,
            lender_account=contract.lender_account,
            borrower_participant_id=contract.borrower_participant_id,
            borrower_account=contract.borrower_account,
            original_quantity=contract.quantity,
            adjusted_quantity=contract.quantity,
            delta_quantity=0,
            original_base_price=contract.base_price,
            adjusted_base_price=contract.base_price,
            original_contract_value=contract.contract_value,
            adjusted_contract_value=contract.contract_value,
            gross_cash_amount=gross_amount,
            withholding_tax_rate=tax_rate,
            withholding_tax_amount=tax_amount,
            net_cash_amount=net_amount,
            borrower_debit_amount=borrower_debit,
            lender_credit_amount=lender_credit,
            tax_compensation_credit=tax_compensation,
            payment_date=event.payment_date,
            status="APPLIED",
            value_conserved=True,
            notes="Repasse de JCP com retenção de IRRF e informe de crédito tributário compensável (IN RFB 1585/15).",
        )

    @staticmethod
    def calculate_bonus(
        contract: BtcLoanContract, event: CorporateEventAnnouncement
    ) -> BtcAdjustmentReceipt:
        """Calcula ajuste de quantidade e preço por Bonificação de Ações."""
        factor = event.ratio_factor
        if factor <= Decimal("0") and event.percentage > Decimal("0"):
            factor = (event.percentage / Decimal("100.0")).quantize(Decimal("0.0001"))
        if factor <= Decimal("0"):
            raise ValueError(f"Fator de bonificação inválido: {factor}")

        orig_qty = contract.quantity
        orig_price = contract.base_price
        orig_val = contract.contract_value

        # Q_novo = int(Q_antigo * (1 + factor))
        multiplier = Decimal("1.0") + factor
        adj_qty = int(Decimal(orig_qty) * multiplier)
        delta_qty = adj_qty - orig_qty

        # P_novo = P_antigo / (1 + factor)
        adj_price = (orig_price / multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        adj_val = (Decimal(adj_qty) * adj_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Conservação financeira: tolerância máxima de 2 centavos por arredondamento de centavos
        diff = abs(adj_val - orig_val)
        conserved = diff <= Decimal("0.05")
        if not conserved:
            raise ContractConservationError(
                f"Quebra de conservação financeira em Bonificação: {orig_val} vs {adj_val} (diff={diff})"
            )

        receipt_id = f"REC-BON-{contract.contract_number}-{event.event_id}"
        return BtcAdjustmentReceipt(
            receipt_id=receipt_id,
            contract_number=contract.contract_number,
            symbol=contract.symbol,
            isin=contract.isin,
            event_id=event.event_id,
            event_type=BtcEventType.BONUS,
            effective_date=event.ex_date,
            lender_participant_id=contract.lender_participant_id,
            lender_account=contract.lender_account,
            borrower_participant_id=contract.borrower_participant_id,
            borrower_account=contract.borrower_account,
            original_quantity=orig_qty,
            adjusted_quantity=adj_qty,
            delta_quantity=delta_qty,
            original_base_price=orig_price,
            adjusted_base_price=adj_price,
            original_contract_value=orig_val,
            adjusted_contract_value=adj_val,
            status="APPLIED",
            value_conserved=conserved,
            notes=f"Bonificação de {factor * 100}% aplicada: {orig_qty} -> {adj_qty} ações a R$ {adj_price}.",
        )

    @staticmethod
    def calculate_split(
        contract: BtcLoanContract, event: CorporateEventAnnouncement
    ) -> BtcAdjustmentReceipt:
        """Calcula ajuste de quantidade e preço por Desdobramento de Ações (Split)."""
        factor = event.ratio_factor
        if factor <= Decimal("0") and event.ratio_to > 0 and event.ratio_from > 0:
            factor = Decimal(event.ratio_to) / Decimal(event.ratio_from)
        if factor <= Decimal("0"):
            raise ValueError(f"Fator de desdobramento inválido: {factor}")

        orig_qty = contract.quantity
        orig_price = contract.base_price
        orig_val = contract.contract_value

        adj_qty = int(Decimal(orig_qty) * factor)
        delta_qty = adj_qty - orig_qty
        adj_price = (orig_price / factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        adj_val = (Decimal(adj_qty) * adj_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        diff = abs(adj_val - orig_val)
        conserved = diff <= Decimal("0.05")
        if not conserved:
            raise ContractConservationError(
                f"Quebra de conservação financeira em Desdobramento: {orig_val} vs {adj_val} (diff={diff})"
            )

        receipt_id = f"REC-SPL-{contract.contract_number}-{event.event_id}"
        return BtcAdjustmentReceipt(
            receipt_id=receipt_id,
            contract_number=contract.contract_number,
            symbol=contract.symbol,
            isin=contract.isin,
            event_id=event.event_id,
            event_type=BtcEventType.SPLIT,
            effective_date=event.ex_date,
            lender_participant_id=contract.lender_participant_id,
            lender_account=contract.lender_account,
            borrower_participant_id=contract.borrower_participant_id,
            borrower_account=contract.borrower_account,
            original_quantity=orig_qty,
            adjusted_quantity=adj_qty,
            delta_quantity=delta_qty,
            original_base_price=orig_price,
            adjusted_base_price=adj_price,
            original_contract_value=orig_val,
            adjusted_contract_value=adj_val,
            status="APPLIED",
            value_conserved=conserved,
            notes=f"Desdobramento na proporção 1:{factor} aplicado: {orig_qty} -> {adj_qty} ações a R$ {adj_price}.",
        )

    @staticmethod
    def calculate_reverse_split(
        contract: BtcLoanContract, event: CorporateEventAnnouncement
    ) -> BtcAdjustmentReceipt:
        """Calcula ajuste de quantidade e preço por Grupamento (Reverse Split) com liquidação de frações."""
        ratio_from = event.ratio_from
        ratio_to = event.ratio_to
        if ratio_from <= 0 or ratio_to <= 0:
            if event.ratio_factor > Decimal("0"):
                # E.g. factor 0.10 => 10 para 1
                ratio_from = int(Decimal("1.0") / event.ratio_factor)
                ratio_to = 1
            else:
                raise ValueError("Parâmetros de proporção de grupamento inválidos")

        ratio = Decimal(ratio_from) / Decimal(ratio_to)
        orig_qty = contract.quantity
        orig_price = contract.base_price
        orig_val = contract.contract_value

        # Divisão inteira e frações
        adj_qty = orig_qty // int(ratio)
        fractional_shares = orig_qty % int(ratio)
        delta_qty = adj_qty - orig_qty

        # P_novo = P_antigo * ratio
        adj_price = (orig_price * ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        adj_val = (Decimal(adj_qty) * adj_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Frações são liquidadas financeiramente ao preço original (ou leilão de sobras)
        fraction_cash = (Decimal(fractional_shares) * orig_price).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        total_value_with_fraction = adj_val + fraction_cash
        diff = abs(total_value_with_fraction - orig_val)
        conserved = diff <= Decimal("0.05")
        if not conserved:
            raise ContractConservationError(
                f"Quebra de conservação financeira em Grupamento: original={orig_val} vs total={total_value_with_fraction}"
            )

        receipt_id = f"REC-REV-{contract.contract_number}-{event.event_id}"
        return BtcAdjustmentReceipt(
            receipt_id=receipt_id,
            contract_number=contract.contract_number,
            symbol=contract.symbol,
            isin=contract.isin,
            event_id=event.event_id,
            event_type=BtcEventType.REVERSE_SPLIT,
            effective_date=event.ex_date,
            lender_participant_id=contract.lender_participant_id,
            lender_account=contract.lender_account,
            borrower_participant_id=contract.borrower_participant_id,
            borrower_account=contract.borrower_account,
            original_quantity=orig_qty,
            adjusted_quantity=adj_qty,
            delta_quantity=delta_qty,
            original_base_price=orig_price,
            adjusted_base_price=adj_price,
            original_contract_value=orig_val,
            adjusted_contract_value=adj_val,
            fractional_shares=fractional_shares,
            fraction_cash_settlement=fraction_cash,
            gross_cash_amount=fraction_cash,
            borrower_debit_amount=fraction_cash,
            lender_credit_amount=fraction_cash,
            payment_date=event.payment_date,
            status="APPLIED",
            value_conserved=conserved,
            notes=(
                f"Grupamento {ratio_from}:{ratio_to} aplicado: {orig_qty} -> {adj_qty} ações a R$ {adj_price}. "
                f"Frações residuais: {fractional_shares} liquidadas a R$ {fraction_cash}."
            ),
        )

    @staticmethod
    def calculate_subscription_rights(
        contract: BtcLoanContract,
        event: CorporateEventAnnouncement,
        exercise: bool = False,
    ) -> BtcAdjustmentReceipt:
        """Calcula tratamento de Direitos de Subscrição (exercício solicitado vs liquidação a mercado)."""
        if event.subscription_ratio <= Decimal("0"):
            raise ValueError(f"Razão de subscrição inválida: {event.subscription_ratio}")

        rights_count = int(Decimal(contract.quantity) * event.subscription_ratio)
        rights_cost = Decimal("0.00")
        rights_cash_settlement = Decimal("0.00")
        borrower_debit = Decimal("0.00")
        lender_credit = Decimal("0.00")

        if exercise:
            # Doador solicita exercício: aporta valor do preço de emissão
            rights_cost = (Decimal(rights_count) * event.subscription_price).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            # Tomador fica obrigado a entregar as novas ações subscritas
            note = (
                f"Subscrição exercida para {rights_count} direitos ({event.right_symbol or contract.symbol}). "
                f"Custo total de aporte pelo Doador: R$ {rights_cost}."
            )
        else:
            # Doador não exerce: liquidação compulsória do valor de mercado do direito
            market_price = event.right_market_value
            if market_price > Decimal("0"):
                rights_cash_settlement = (Decimal(rights_count) * market_price).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                borrower_debit = rights_cash_settlement
                lender_credit = rights_cash_settlement
            note = (
                f"Subscrição não exercida: {rights_count} direitos liquidados financeiramente a R$ {event.right_market_value} "
                f"cada (Total: R$ {rights_cash_settlement})."
            )

        receipt_id = f"REC-SUB-{contract.contract_number}-{event.event_id}"
        return BtcAdjustmentReceipt(
            receipt_id=receipt_id,
            contract_number=contract.contract_number,
            symbol=contract.symbol,
            isin=contract.isin,
            event_id=event.event_id,
            event_type=BtcEventType.SUBSCRIPTION_RIGHT,
            effective_date=event.ex_date,
            lender_participant_id=contract.lender_participant_id,
            lender_account=contract.lender_account,
            borrower_participant_id=contract.borrower_participant_id,
            borrower_account=contract.borrower_account,
            original_quantity=contract.quantity,
            adjusted_quantity=contract.quantity,
            delta_quantity=0,
            original_base_price=contract.base_price,
            adjusted_base_price=contract.base_price,
            original_contract_value=contract.contract_value,
            adjusted_contract_value=contract.contract_value,
            gross_cash_amount=rights_cash_settlement,
            borrower_debit_amount=borrower_debit,
            lender_credit_amount=lender_credit,
            payment_date=event.payment_date,
            rights_allocated=rights_count,
            rights_exercised=exercise,
            rights_cash_settlement=rights_cash_settlement,
            rights_exercise_cost=rights_cost,
            status="APPLIED",
            value_conserved=True,
            notes=note,
        )

    # --------------------------------------------------------------------------
    # 4.2 Orquestração e Processamento de Eventos
    # --------------------------------------------------------------------------

    def process_event(
        self,
        event: CorporateEventAnnouncement,
        exercise_rights: bool = False,
        apply_to_contract: bool = True,
    ) -> List[BtcAdjustmentReceipt]:
        """Aplica um evento corporativo anunciado a todos os contratos elegíveis em aberto."""
        contracts = self.get_open_contracts_for_event(event)
        event_receipts = []

        for contract in contracts:
            receipt: BtcAdjustmentReceipt

            if event.event_type == BtcEventType.DIVIDEND:
                receipt = self.calculate_dividend(contract, event)
            elif event.event_type == BtcEventType.JCP:
                receipt = self.calculate_jcp(contract, event)
            elif event.event_type == BtcEventType.BONUS:
                receipt = self.calculate_bonus(contract, event)
            elif event.event_type == BtcEventType.SPLIT:
                receipt = self.calculate_split(contract, event)
            elif event.event_type == BtcEventType.REVERSE_SPLIT:
                receipt = self.calculate_reverse_split(contract, event)
            elif event.event_type == BtcEventType.SUBSCRIPTION_RIGHT:
                receipt = self.calculate_subscription_rights(contract, event, exercise=exercise_rights)
            else:
                raise ValueError(f"Tipo de evento não suportado: {event.event_type}")

            if apply_to_contract and receipt.event_type.is_stock_event:
                self._apply_receipt_to_contract(contract, receipt)

            event_receipts.append(receipt)
            self.receipts.append(receipt)

        return event_receipts

    def process_all_events(
        self,
        events: Optional[List[CorporateEventAnnouncement]] = None,
        rights_decisions: Optional[Dict[str, bool]] = None,
        apply_to_contract: bool = True,
    ) -> List[BtcAdjustmentReceipt]:
        """Processa em lote uma lista de eventos corporativos sobre os contratos registrados."""
        if events is None:
            events = list(self.events.values())
        if rights_decisions is None:
            rights_decisions = {}

        all_receipts = []
        for evt in events:
            exercise = rights_decisions.get(evt.event_id, False)
            rcpts = self.process_event(evt, exercise_rights=exercise, apply_to_contract=apply_to_contract)
            all_receipts.extend(rcpts)
        return all_receipts

    def _apply_receipt_to_contract(
        self, contract: BtcLoanContract, receipt: BtcAdjustmentReceipt
    ) -> None:
        """Atualiza a posição do contrato após evento de ações."""
        contract.quantity = receipt.adjusted_quantity
        contract.base_price = receipt.adjusted_base_price
        contract.contract_value = receipt.adjusted_contract_value

    # --------------------------------------------------------------------------
    # 4.3 Utilitários de Carregamento de Fixture e Resumo
    # --------------------------------------------------------------------------

    @classmethod
    def from_json_fixture(
        cls, fixture_path_or_dict: Union[str, Path, Dict[str, Any]]
    ) -> Tuple[BtcCorporateActionsEngine, List[CorporateEventAnnouncement]]:
        """Instancia o motor a partir de um arquivo ou dicionário JSON estruturado."""
        if isinstance(fixture_path_or_dict, (str, Path)):
            with open(fixture_path_or_dict, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = fixture_path_or_dict

        engine = cls()
        for c_dict in data.get("contracts", []):
            engine.register_contract(BtcLoanContract.from_dict(c_dict))

        events_list: List[CorporateEventAnnouncement] = []
        for e_dict in data.get("events", []):
            evt = CorporateEventAnnouncement.from_dict(e_dict)
            engine.register_event(evt)
            events_list.append(evt)

        return engine, events_list

    def summarize(self) -> Dict[str, Any]:
        """Gera resumo consolidado das operações e batimentos financeiros."""
        total_borrower_debits = sum(r.borrower_debit_amount for r in self.receipts)
        total_lender_credits = sum(r.lender_credit_amount for r in self.receipts)
        total_tax_retentions = sum(r.withholding_tax_amount for r in self.receipts)
        total_tax_compensations = sum(r.tax_compensation_credit for r in self.receipts)
        all_conserved = all(r.value_conserved for r in self.receipts)

        return {
            "total_contracts": len(self.contracts),
            "total_events": len(self.events),
            "total_receipts": len(self.receipts),
            "total_borrower_debits": str(total_borrower_debits),
            "total_lender_credits": str(total_lender_credits),
            "total_tax_retentions": str(total_tax_retentions),
            "total_tax_compensations": str(total_tax_compensations),
            "all_conserved": all_conserved,
        }


__all__ = [
    "BtcEventType",
    "BtcCorporateActionError",
    "IneligibleContractError",
    "ContractConservationError",
    "BtcLoanContract",
    "CorporateEventAnnouncement",
    "BtcAdjustmentReceipt",
    "BtcCorporateActionsEngine",
]
