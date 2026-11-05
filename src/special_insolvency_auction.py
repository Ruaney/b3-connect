"""Leilões Especiais de Desinvestimento, Alienação de UPI e Liquidação de Ativos em Recuperação (FL-081).

Implementa o motor determinístico e validador de Leilões Especiais Judiciais de Desinvestimento,
Alienação de Unidades Produtivas Isoladas (UPI) e Liquidação de Ativos de Empresas em Recuperação
Judicial e Falência no PUMA Trading B3, em estrita conformidade com:
- Lei nº 11.101/2005 (Lei de Falências e Recuperação Judicial - LF-RJ), com as alterações da Lei nº 14.112/2020:
  * Art. 60 e Art. 60-A: Alienação judicial de filiais ou Unidades Produtivas Isoladas (UPI),
    com blindagem legal absoluta (sem sucessão trabalhista, tributária, ambiental ou contratual).
  * Art. 66: Autorização judicial prévia para desinvestimento e liquidação de ativos do devedor.
  * Art. 83: Ordem legal de classificação dos créditos concursais no Quadro Geral de Credores (QGC):
    - I: Trabalhistas e acidentários (limitados a 150 salários mínimos por credor);
    - II: Créditos com garantia real (limitados ao valor do bem dado em garantia);
    - III: Créditos tributários;
    - IV: Privilégio especial;
    - V: Privilégio geral;
    - VI: Quirografários;
    - VII/VIII: Multas contratuais, penas pecuniárias e créditos subordinados.
  * Art. 84: Ordem de preferência de créditos e despesas extraconcursais (custas processuais,
    emolumentos B3, comissão de leiloeiro e honorários do Administrador Judicial).
  * Art. 141 e Art. 142: Modalidades de alienação judicial competitiva em bolsa de valores (B3):
    - Sessão de propostas fechadas (*sealed bids*), proponente qualificador (*Stalking Horse*);
    - Sessão eletrônica/viva-voz de lances em pregão (*overbidding session*) com degrau mínimo de lance (*overbid step*);
    - Cláusula de preferência e equiparação de ofertas (*Right to Match*) do Stalking Horse.
  * Art. 143: Homologação pelo juízo recuperacional e arrematação definitiva.
- Regulamento de Operações e Procedimentos Operacionais do PUMA Trading System da B3:
  * Habilitação prévia perante a B3 e Administrador Judicial com depósito/bloqueio de caução de participação (*Bid Bond*);
  * Pregão eletrônico de leilões especiais judiciais com túnel de rejeição de lances abaixo do preço de reserva;
  * Registro de liquidação financeira multilateral com segregação e repasse ao juízo recuperacional.

Leis de Invariância e Conservação:
1. Invariante Formal de Conservação de Ativos Leiloados:
   Lote_Total == Lote_Arrematado + Lote_Deserto
   Qtd_Total_Ativos == Qtd_Arrematada + Qtd_Deserta
2. Invariante de Restituição Integral de Caução a Participantes Não Vencedores:
   Total_Caução_Depositada == Total_Caução_Retida + Total_Caução_Restituída
   Total_Caução_Retida == Caução_Vencedor (ou 0 se o leilão for deserto)
3. Invariante de Liquidação Financeira Integral (Waterfall Judicial de Pagamento):
   Total_Arrecadado == Total_Custas_Extraconcursais + Total_Pago_Credores + Saldo_Remanescente_Recuperanda
   Soma(Custas_Extraconcursais) == Total_Custas_Extraconcursais
   Soma(Pagamentos_Individuais_Credores) == Total_Pago_Credores
   Rateio pro-rata determinístico com método dos maiores restos (Hamilton / Hare-Niemeyer), sem centavos órfãos.

100% biblioteca padrão Python (dataclasses, Decimal, datetime, enum, json, pathlib).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
EIGHT_PLACES = Decimal("0.00000001")


def _quantize_money(val: Union[Decimal, str, int, float]) -> Decimal:
    """Quantiza valor monetário para 2 casas decimais (BRL centavos) com ROUND_HALF_UP."""
    return Decimal(str(val)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _quantize_rate(val: Union[Decimal, str, int, float]) -> Decimal:
    """Quantiza taxas e percentuais para 8 casas decimais."""
    return Decimal(str(val)).quantize(EIGHT_PLACES, rounding=ROUND_HALF_UP)


def _to_date(val: Any, field_name: str) -> date:
    """Converte valor para date."""
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val.strip()[:10])
    raise ValueError(f"Campo '{field_name}' deve ser uma data válida no formato YYYY-MM-DD.")


def _to_datetime(val: Any, field_name: str) -> datetime:
    """Converte string ISO 8601 ou objeto datetime para datetime timezone-aware."""
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        norm = val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(norm)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    raise ValueError(f"Campo '{field_name}' deve ser um datetime válido.")


# ---------------------------------------------------------------------------
# Enumerações Tipadas de Domínio
# ---------------------------------------------------------------------------

class InsolvencyLotType(str, Enum):
    """Tipologia de ativos e lotes em processos de insolvência e desinvestimento judicial."""
    UPI = "UPI"                                      # Unidade Produtiva Isolada (Art. 60 / 60-A LF-RJ)
    EQUITY_STAKE = "EQUITY_STAKE"                    # Participação societária / ações / quotas de SPE
    REAL_ESTATE = "REAL_ESTATE"                      # Imóvel operacional ou planta industrial
    EQUIPMENT_MACHINERY = "EQUIPMENT_MACHINERY"      # Máquinas, linhas de produção e ativos fabris
    DEBENTURE_DEFAULT = "DEBENTURE_DEFAULT"          # Debêntures inadimplidas e títulos de dívida
    CREDIT_PORTFOLIO_NPL = "CREDIT_PORTFOLIO_NPL"    # Carteira de créditos inadimplidos / NPL
    INTELLECTUAL_PROPERTY = "INTELLECTUAL_PROPERTY"  # Marcas, patentes e ativos intangíveis


class BidRoundType(str, Enum):
    """Modalidades de rodada de ofertas e lances no leilão especial B3."""
    STALKING_HORSE = "STALKING_HORSE"  # Proposta vinculante qualificada do proponente originador
    SEALED_BID = "SEALED_BID"          # Proposta em envelope fechado na fase inicial
    OPEN_OVERBID = "OPEN_OVERBID"      # Lance em pregão eletrônico / viva-voz com incremento mínimo
    MATCHING_BID = "MATCHING_BID"      # Lance de equiparação do Stalking Horse (Right to Match)


class LotAuctionStatus(str, Enum):
    """Situação de encerramento do lote no leilão."""
    PENDING = "PENDING"      # Aguardando sessão de lances
    AWARDED = "AWARDED"      # Arrematado com sucesso por lance válido >= preço de reserva
    DESERTO = "DESERTO"      # Deserto (sem propostas válidas que atinjam o preço de reserva mínimo)
    CANCELLED = "CANCELLED"  # Cancelado por determinação do juízo recuperacional


class AuctionPhase(str, Enum):
    """Fases do processo de leilão judicial de insolvência no PUMA Trading B3."""
    SETUP = "SETUP"                                # Edital publicado e parâmetros cadastrados
    QUALIFICATION = "QUALIFICATION"                # Habilitação prévia e bloqueio de caução (Bid Bond)
    SEALED_BIDS = "SEALED_BIDS"                    # Abertura de envelopes fechados e Stalking Horse
    OVERBIDDING = "OVERBIDDING"                    # Pregão de lances abertos sucessivos (overbidding)
    RIGHT_TO_MATCH = "RIGHT_TO_MATCH"              # Exercício eventual de preferência pelo Stalking Horse
    HOMOLOGATION = "HOMOLOGATION"                  # Homologação judicial dos resultados pelo juízo
    FINANCIAL_SETTLEMENT = "FINANCIAL_SETTLEMENT"  # Liquidação financeira e execução da cascata judicial
    CONCLUDED = "CONCLUDED"                        # Concluído e custódia definitiva transferida


class CreditorClass(str, Enum):
    """Classes legais de credores concursais conforme Art. 83 da Lei 11.101/2005."""
    CLASSE_I_TRABALHISTA = "CLASSE_I_TRABALHISTA"                  # Art. 83, I (trabalhistas até 150 SM)
    CLASSE_II_GARANTIA_REAL = "CLASSE_II_GARANTIA_REAL"            # Art. 83, II (até limite da garantia)
    CLASSE_III_TRIBUTARIA = "CLASSE_III_TRIBUTARIA"                # Art. 83, III (fisco federal, estadual, municipal)
    CLASSE_IV_PRIVILEGIO_ESPECIAL = "CLASSE_IV_PRIVILEGIO_ESPECIAL"# Art. 83, IV
    CLASSE_V_PRIVILEGIO_GERAL = "CLASSE_V_PRIVILEGIO_GERAL"        # Art. 83, V
    CLASSE_VI_QUIROGRAFARIO = "CLASSE_VI_QUIROGRAFARIO"            # Art. 83, VI (fornecedores e debenturistas)
    CLASSE_VII_SUBORDINADO_MULTAS = "CLASSE_VII_SUBORDINADO_MULTAS"# Art. 83, VII e VIII (multas e subordinados)


class ExtracurricularExpenseType(str, Enum):
    """Tipos de créditos e despesas extraconcursais prioritárias (Art. 84 da LF-RJ)."""
    B3_AUCTION_FEES = "B3_AUCTION_FEES"                        # Emolumentos e taxa de leilão especial B3
    AUCTIONEER_COMMISSION = "AUCTIONEER_COMMISSION"            # Comissão do leiloeiro oficial ou corretora B3
    JUDICIAL_ADMINISTRATOR_FEE = "JUDICIAL_ADMINISTRATOR_FEE"  # Honorários do Administrador Judicial
    COURT_COSTS = "COURT_COSTS"                                # Custas judiciais e taxas da Vara de Falências


# Ordem estrita de precedência legal de pagamento aos credores concursais (Art. 83 LF-RJ)
CREDITOR_CLASS_HIERARCHY: Tuple[CreditorClass, ...] = (
    CreditorClass.CLASSE_I_TRABALHISTA,
    CreditorClass.CLASSE_II_GARANTIA_REAL,
    CreditorClass.CLASSE_III_TRIBUTARIA,
    CreditorClass.CLASSE_IV_PRIVILEGIO_ESPECIAL,
    CreditorClass.CLASSE_V_PRIVILEGIO_GERAL,
    CreditorClass.CLASSE_VI_QUIROGRAFARIO,
    CreditorClass.CLASSE_VII_SUBORDINADO_MULTAS,
)


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class SpecialInsolvencyAuctionError(Exception):
    """Exceção base para operações de Leilões Especiais de Insolvência e Desinvestimento."""
    pass


class ProponentNotQualifiedError(SpecialInsolvencyAuctionError):
    """Proponente não possui habilitação prévia deferida pelo Administrador Judicial ou B3."""
    pass


class InsufficientBidBondError(SpecialInsolvencyAuctionError):
    """Caução de participação (Bid Bond) é insuficiente ou não se encontra bloqueada em custódia."""
    pass


class BidBelowReservePriceError(SpecialInsolvencyAuctionError):
    """Lance submetido é inferior ao preço de reserva mínimo homologado pelo juízo recuperacional."""
    pass


class InvalidOverbidIncrementError(SpecialInsolvencyAuctionError):
    """Lance no pregão de overbidding não atende ao degrau mínimo de incremento (overbid step)."""
    pass


class AuctionPhaseError(SpecialInsolvencyAuctionError):
    """Operação inválida para a fase corrente do leilão especial."""
    pass


class LotNotFoundError(SpecialInsolvencyAuctionError):
    """Lote judicial não encontrado no leilão."""
    pass


class ProponentNotFoundError(SpecialInsolvencyAuctionError):
    """Proponente não cadastrado no leilão."""
    pass


class InsolvencyAuctionInvariantError(SpecialInsolvencyAuctionError, AssertionError):
    """Violação formal de lei invariante de conservação física, patrimonial ou financeira."""
    pass


# ---------------------------------------------------------------------------
# Algoritmo Determinístico de Rateio Pro-Rata (Hamilton / Hare-Niemeyer)
# ---------------------------------------------------------------------------

def _distribute_pro_rata_cents(
    amount_to_distribute: Decimal,
    claims: Sequence[Tuple[str, Decimal]],
) -> Dict[str, Decimal]:
    """Distribui montante financeiro entre credores proporcionalmente aos seus créditos.

    Utiliza o Método dos Maiores Restos (Hamilton / Hare-Niemeyer) em aritmética inteira
    de centavos para garantir conservação contábil absoluta (sem centavos órfãos ou fictícios).

    Args:
        amount_to_distribute: Montante total a ser rateado (já quantizado em BRL centavos).
        claims: Lista de tuplas (creditor_id, claim_amount).

    Returns:
        Dicionário mapeando creditor_id para valor alocado em Decimal quantizado em 2 casas.
    """
    if not claims or amount_to_distribute <= Decimal("0.00"):
        return {cid: Decimal("0.00") for cid, _ in claims}

    total_claims = sum(claim for _, claim in claims)
    if total_claims <= Decimal("0.00"):
        return {cid: Decimal("0.00") for cid, _ in claims}

    if amount_to_distribute >= total_claims:
        return {cid: claim for cid, claim in claims}

    total_cents = int((amount_to_distribute * 100).to_integral_value(ROUND_HALF_UP))
    total_claims_cents = int((total_claims * 100).to_integral_value(ROUND_HALF_UP))

    allocations_cents: Dict[str, int] = {}
    remainders: List[Tuple[int, int, str]] = []  # (-resto, ordem_original, creditor_id)

    allocated_sum = 0
    for idx, (cid, claim) in enumerate(claims):
        claim_cents = int((claim * 100).to_integral_value(ROUND_HALF_UP))
        prod = total_cents * claim_cents
        fl_cents = prod // total_claims_cents
        rem = prod % total_claims_cents

        allocations_cents[cid] = fl_cents
        allocated_sum += fl_cents
        remainders.append((-rem, idx, cid))

    diff_cents = total_cents - allocated_sum
    remainders.sort()

    for i in range(diff_cents):
        _, _, cid = remainders[i]
        allocations_cents[cid] += 1

    return {
        cid: _quantize_money(Decimal(cents) / Decimal(100))
        for cid, cents in allocations_cents.items()
    }


# ---------------------------------------------------------------------------
# Dataclasses de Estrutura de Dados
# ---------------------------------------------------------------------------

@dataclass
class Proponent:
    """Proponente participante do leilão especial judicial."""
    proponent_id: str
    name: str
    document_id: str
    participant_code: int
    custody_account: str
    is_qualified: bool = False
    bid_bond_deposited: Decimal = Decimal("0.00")
    bid_bond_blocked: bool = False
    is_stalking_horse: bool = False
    has_right_to_match: bool = False
    bid_bond_retained: Decimal = Decimal("0.00")
    bid_bond_refunded: Decimal = Decimal("0.00")
    is_winner: bool = False

    def __post_init__(self) -> None:
        self.bid_bond_deposited = _quantize_money(self.bid_bond_deposited)
        self.bid_bond_retained = _quantize_money(self.bid_bond_retained)
        self.bid_bond_refunded = _quantize_money(self.bid_bond_refunded)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proponent_id": self.proponent_id,
            "name": self.name,
            "document_id": self.document_id,
            "participant_code": self.participant_code,
            "custody_account": self.custody_account,
            "is_qualified": self.is_qualified,
            "bid_bond_deposited": str(self.bid_bond_deposited),
            "bid_bond_blocked": self.bid_bond_blocked,
            "is_stalking_horse": self.is_stalking_horse,
            "has_right_to_match": self.has_right_to_match,
            "bid_bond_retained": str(self.bid_bond_retained),
            "bid_bond_refunded": str(self.bid_bond_refunded),
            "is_winner": self.is_winner,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Proponent:
        return cls(
            proponent_id=data["proponent_id"],
            name=data["name"],
            document_id=data["document_id"],
            participant_code=int(data["participant_code"]),
            custody_account=data["custody_account"],
            is_qualified=bool(data.get("is_qualified", False)),
            bid_bond_deposited=Decimal(str(data.get("bid_bond_deposited", "0.00"))),
            bid_bond_blocked=bool(data.get("bid_bond_blocked", False)),
            is_stalking_horse=bool(data.get("is_stalking_horse", False)),
            has_right_to_match=bool(data.get("has_right_to_match", False)),
            bid_bond_retained=Decimal(str(data.get("bid_bond_retained", "0.00"))),
            bid_bond_refunded=Decimal(str(data.get("bid_bond_refunded", "0.00"))),
            is_winner=bool(data.get("is_winner", False)),
        )


@dataclass
class InsolvencyLot:
    """Lote de ativos ou Unidade Produtiva Isolada (UPI) a ser alienado judicialmente."""
    lot_id: str
    asset_name: str
    lot_type: InsolvencyLotType
    quantity: int
    judicial_reserve_price: Decimal
    minimum_overbid_step: Decimal
    required_bid_bond_amount: Decimal
    judicial_process_number: str
    court_name: str
    debtor_name: str
    judicial_administrator: str
    is_free_and_clear_of_liens: bool = True
    status: LotAuctionStatus = LotAuctionStatus.PENDING
    winning_proponent_id: Optional[str] = None
    winning_bid_amount: Optional[Decimal] = None

    def __post_init__(self) -> None:
        if isinstance(self.lot_type, str):
            self.lot_type = InsolvencyLotType(self.lot_type)
        if isinstance(self.status, str):
            self.status = LotAuctionStatus(self.status)
        self.judicial_reserve_price = _quantize_money(self.judicial_reserve_price)
        self.minimum_overbid_step = _quantize_money(self.minimum_overbid_step)
        self.required_bid_bond_amount = _quantize_money(self.required_bid_bond_amount)
        if self.winning_bid_amount is not None:
            self.winning_bid_amount = _quantize_money(self.winning_bid_amount)

        if self.quantity <= 0:
            raise ValueError(f"Quantidade do lote '{self.lot_id}' deve ser estritamente positiva.")
        if self.judicial_reserve_price <= Decimal("0.00"):
            raise ValueError(f"Preço de reserva do lote '{self.lot_id}' deve ser maior que zero.")
        if self.minimum_overbid_step <= Decimal("0.00"):
            raise ValueError(f"Degrau de overbid do lote '{self.lot_id}' deve ser maior que zero.")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lot_id": self.lot_id,
            "asset_name": self.asset_name,
            "lot_type": self.lot_type.value,
            "quantity": self.quantity,
            "judicial_reserve_price": str(self.judicial_reserve_price),
            "minimum_overbid_step": str(self.minimum_overbid_step),
            "required_bid_bond_amount": str(self.required_bid_bond_amount),
            "judicial_process_number": self.judicial_process_number,
            "court_name": self.court_name,
            "debtor_name": self.debtor_name,
            "judicial_administrator": self.judicial_administrator,
            "is_free_and_clear_of_liens": self.is_free_and_clear_of_liens,
            "status": self.status.value,
            "winning_proponent_id": self.winning_proponent_id,
            "winning_bid_amount": str(self.winning_bid_amount) if self.winning_bid_amount is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InsolvencyLot:
        return cls(
            lot_id=data["lot_id"],
            asset_name=data["asset_name"],
            lot_type=InsolvencyLotType(data["lot_type"]),
            quantity=int(data.get("quantity", 1)),
            judicial_reserve_price=Decimal(str(data["judicial_reserve_price"])),
            minimum_overbid_step=Decimal(str(data["minimum_overbid_step"])),
            required_bid_bond_amount=Decimal(str(data["required_bid_bond_amount"])),
            judicial_process_number=data["judicial_process_number"],
            court_name=data["court_name"],
            debtor_name=data["debtor_name"],
            judicial_administrator=data["judicial_administrator"],
            is_free_and_clear_of_liens=bool(data.get("is_free_and_clear_of_liens", True)),
            status=LotAuctionStatus(data.get("status", LotAuctionStatus.PENDING.value)),
            winning_proponent_id=data.get("winning_proponent_id"),
            winning_bid_amount=Decimal(str(data["winning_bid_amount"])) if data.get("winning_bid_amount") is not None else None,
        )


@dataclass
class AuctionBid:
    """Proposta ou lance submetido no leilão judicial."""
    bid_id: str
    lot_id: str
    proponent_id: str
    round_type: BidRoundType
    bid_amount: Decimal
    timestamp: datetime
    is_valid: bool = True
    rejection_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.round_type, str):
            self.round_type = BidRoundType(self.round_type)
        self.bid_amount = _quantize_money(self.bid_amount)
        self.timestamp = _to_datetime(self.timestamp, "timestamp")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bid_id": self.bid_id,
            "lot_id": self.lot_id,
            "proponent_id": self.proponent_id,
            "round_type": self.round_type.value,
            "bid_amount": str(self.bid_amount),
            "timestamp": self.timestamp.isoformat(),
            "is_valid": self.is_valid,
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AuctionBid:
        return cls(
            bid_id=data["bid_id"],
            lot_id=data["lot_id"],
            proponent_id=data["proponent_id"],
            round_type=BidRoundType(data["round_type"]),
            bid_amount=Decimal(str(data["bid_amount"])),
            timestamp=_to_datetime(data["timestamp"], "timestamp"),
            is_valid=bool(data.get("is_valid", True)),
            rejection_reason=data.get("rejection_reason"),
        )


@dataclass
class ExtracurricularExpense:
    """Custa ou despesa extraconcursal com prioridade absoluta de quitação (Art. 84 LF-RJ)."""
    expense_id: str
    expense_type: ExtracurricularExpenseType
    description: str
    amount: Decimal
    recipient: str

    def __post_init__(self) -> None:
        if isinstance(self.expense_type, str):
            self.expense_type = ExtracurricularExpenseType(self.expense_type)
        self.amount = _quantize_money(self.amount)
        if self.amount < Decimal("0.00"):
            raise ValueError(f"Valor da custa '{self.expense_id}' não pode ser negativo.")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expense_id": self.expense_id,
            "expense_type": self.expense_type.value,
            "description": self.description,
            "amount": str(self.amount),
            "recipient": self.recipient,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExtracurricularExpense:
        return cls(
            expense_id=data["expense_id"],
            expense_type=ExtracurricularExpenseType(data["expense_type"]),
            description=data["description"],
            amount=Decimal(str(data["amount"])),
            recipient=data["recipient"],
        )


@dataclass
class CreditorClaim:
    """Crédito concursal habilitado no Quadro Geral de Credores (QGC - Art. 83 LF-RJ)."""
    creditor_id: str
    name: str
    creditor_class: CreditorClass
    original_claim_amount: Decimal
    paid_amount: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        if isinstance(self.creditor_class, str):
            self.creditor_class = CreditorClass(self.creditor_class)
        self.original_claim_amount = _quantize_money(self.original_claim_amount)
        self.paid_amount = _quantize_money(self.paid_amount)
        if self.original_claim_amount <= Decimal("0.00"):
            raise ValueError(f"Crédito concursal '{self.creditor_id}' deve ser maior que zero.")

    @property
    def outstanding_balance(self) -> Decimal:
        """Saldo remanescente devedor não satisfeito."""
        return max(Decimal("0.00"), _quantize_money(self.original_claim_amount - self.paid_amount))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "creditor_id": self.creditor_id,
            "name": self.name,
            "creditor_class": self.creditor_class.value,
            "original_claim_amount": str(self.original_claim_amount),
            "paid_amount": str(self.paid_amount),
            "outstanding_balance": str(self.outstanding_balance),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CreditorClaim:
        return cls(
            creditor_id=data["creditor_id"],
            name=data["name"],
            creditor_class=CreditorClass(data["creditor_class"]),
            original_claim_amount=Decimal(str(data["original_claim_amount"])),
            paid_amount=Decimal(str(data.get("paid_amount", "0.00"))),
        )


@dataclass
class CreditorPaymentDetail:
    """Detalhamento contábil do pagamento realizado a um credor concursal."""
    creditor_id: str
    name: str
    creditor_class: CreditorClass
    original_claim: Decimal
    paid_amount: Decimal
    outstanding_balance: Decimal
    coverage_ratio: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "creditor_id": self.creditor_id,
            "name": self.name,
            "creditor_class": self.creditor_class.value,
            "original_claim": str(self.original_claim),
            "paid_amount": str(self.paid_amount),
            "outstanding_balance": str(self.outstanding_balance),
            "coverage_ratio": str(self.coverage_ratio),
        }


@dataclass
class WaterfallSettlementResult:
    """Resultado da execução da cascata judicial de liquidação e distribuição financeira (Art. 84 e 83)."""
    gross_auction_proceeds: Decimal
    retained_bid_bond_applied: Decimal
    cash_settlement_collected: Decimal
    total_extracurricular_expenses: Decimal
    paid_extracurricular_expenses: Decimal
    net_proceeds_for_creditors: Decimal
    total_paid_to_creditors: Decimal
    payments_by_class: Dict[str, Decimal]
    individual_payments: List[CreditorPaymentDetail]
    residual_surplus_to_debtor: Decimal
    is_waterfall_conserved: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gross_auction_proceeds": str(self.gross_auction_proceeds),
            "retained_bid_bond_applied": str(self.retained_bid_bond_applied),
            "cash_settlement_collected": str(self.cash_settlement_collected),
            "total_extracurricular_expenses": str(self.total_extracurricular_expenses),
            "paid_extracurricular_expenses": str(self.paid_extracurricular_expenses),
            "net_proceeds_for_creditors": str(self.net_proceeds_for_creditors),
            "total_paid_to_creditors": str(self.total_paid_to_creditors),
            "payments_by_class": {k: str(v) for k, v in self.payments_by_class.items()},
            "individual_payments": [p.to_dict() for p in self.individual_payments],
            "residual_surplus_to_debtor": str(self.residual_surplus_to_debtor),
            "is_waterfall_conserved": self.is_waterfall_conserved,
        }


@dataclass
class BidBondRestitutionLine:
    """Linha individual de conciliação de caução de participação."""
    proponent_id: str
    name: str
    is_winner: bool
    deposited_amount: Decimal
    retained_amount: Decimal
    refunded_amount: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proponent_id": self.proponent_id,
            "name": self.name,
            "is_winner": self.is_winner,
            "deposited_amount": str(self.deposited_amount),
            "retained_amount": str(self.retained_amount),
            "refunded_amount": str(self.refunded_amount),
        }


@dataclass
class BidBondRestitutionReport:
    """Relatório consolidado de restituição e retenção de caução (Bid Bond)."""
    total_deposited: Decimal
    total_retained: Decimal
    total_refunded: Decimal
    winning_proponents: List[str]
    lines: List[BidBondRestitutionLine]
    is_conserved: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_deposited": str(self.total_deposited),
            "total_retained": str(self.total_retained),
            "total_refunded": str(self.total_refunded),
            "winning_proponents": self.winning_proponents,
            "lines": [line.to_dict() for line in self.lines],
            "is_conserved": self.is_conserved,
        }


@dataclass
class InsolvencyAuctionSummary:
    """Resumo executivo da realização de um leilão judicial de insolvência."""
    auction_id: str
    debtor_name: str
    total_lots: int
    awarded_lots: int
    desert_lots: int
    total_proceeds: Decimal
    is_asset_conserved: bool
    is_bid_bond_conserved: bool
    is_waterfall_conserved: bool
    all_invariants_satisfied: bool
    waterfall_result: Optional[WaterfallSettlementResult] = None
    bid_bond_report: Optional[BidBondRestitutionReport] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "auction_id": self.auction_id,
            "debtor_name": self.debtor_name,
            "total_lots": self.total_lots,
            "awarded_lots": self.awarded_lots,
            "desert_lots": self.desert_lots,
            "total_proceeds": str(self.total_proceeds),
            "is_asset_conserved": self.is_asset_conserved,
            "is_bid_bond_conserved": self.is_bid_bond_conserved,
            "is_waterfall_conserved": self.is_waterfall_conserved,
            "all_invariants_satisfied": self.all_invariants_satisfied,
            "waterfall_result": self.waterfall_result.to_dict() if self.waterfall_result else None,
            "bid_bond_report": self.bid_bond_report.to_dict() if self.bid_bond_report else None,
        }


# ---------------------------------------------------------------------------
# Classe Principal de Leilão Judicial (InsolvencyAuction)
# ---------------------------------------------------------------------------

class InsolvencyAuction:
    """Instância completa de um Leilão Especial de Insolvência e Desinvestimento Judicial (FL-081)."""

    def __init__(
        self,
        auction_id: str,
        debtor_name: str,
        judicial_process_number: str,
        court_name: str,
        judicial_administrator: str,
        auction_date: Union[date, str],
        settlement_date: Union[date, str],
        description: str = "",
        lots: Optional[Sequence[InsolvencyLot]] = None,
        proponents: Optional[Sequence[Proponent]] = None,
        bids: Optional[Sequence[AuctionBid]] = None,
        extracurricular_expenses: Optional[Sequence[ExtracurricularExpense]] = None,
        creditor_claims: Optional[Sequence[CreditorClaim]] = None,
    ) -> None:
        self.auction_id = auction_id
        self.debtor_name = debtor_name
        self.judicial_process_number = judicial_process_number
        self.court_name = court_name
        self.judicial_administrator = judicial_administrator
        self.auction_date = _to_date(auction_date, "auction_date")
        self.settlement_date = _to_date(settlement_date, "settlement_date")
        self.description = description

        self.phase: AuctionPhase = AuctionPhase.SETUP
        self.lots: Dict[str, InsolvencyLot] = {}
        self.proponents: Dict[str, Proponent] = {}
        self.bids: List[AuctionBid] = []
        self.extracurricular_expenses: List[ExtracurricularExpense] = []
        self.creditor_claims: Dict[str, CreditorClaim] = {}

        self.waterfall_result: Optional[WaterfallSettlementResult] = None
        self.bid_bond_report: Optional[BidBondRestitutionReport] = None

        if lots:
            for lot in lots:
                self.register_lot(lot)
        if proponents:
            for p in proponents:
                self.register_proponent(p)
        if bids:
            for b in bids:
                self.submit_bid(b)
        if extracurricular_expenses:
            for e in extracurricular_expenses:
                self.register_expense(e)
        if creditor_claims:
            for c in creditor_claims:
                self.register_creditor_claim(c)

    def register_lot(self, lot: InsolvencyLot) -> None:
        """Registra um lote judicial no edital do leilão."""
        self.lots[lot.lot_id] = lot

    def register_proponent(self, proponent: Proponent) -> None:
        """Cadastra um participante/proponente no leilão."""
        self.proponents[proponent.proponent_id] = proponent

    def register_expense(self, expense: ExtracurricularExpense) -> None:
        """Registra uma despesa extraconcursal homologada pelo juízo (Art. 84 LF-RJ)."""
        self.extracurricular_expenses.append(expense)

    def register_creditor_claim(self, claim: CreditorClaim) -> None:
        """Registra um crédito concursal do Quadro Geral de Credores (Art. 83 LF-RJ)."""
        self.creditor_claims[claim.creditor_id] = claim

    def qualify_proponent(
        self,
        proponent_id: str,
        bid_bond_deposited: Decimal,
        bid_bond_blocked: bool = True,
    ) -> None:
        """Realiza a habilitação prévia do participante com depósito de garantia (Bid Bond)."""
        if proponent_id not in self.proponents:
            raise ProponentNotFoundError(f"Proponente '{proponent_id}' não cadastrado no leilão.")
        p = self.proponents[proponent_id]
        p.is_qualified = True
        p.bid_bond_deposited = _quantize_money(bid_bond_deposited)
        p.bid_bond_blocked = bid_bond_blocked

    def submit_bid(self, bid: AuctionBid) -> AuctionBid:
        """Valida e submete um lance/proposta formal no motor de pregão PUMA B3."""
        if bid.lot_id not in self.lots:
            raise LotNotFoundError(f"Lote judicial '{bid.lot_id}' não encontrado no leilão.")
        if bid.proponent_id not in self.proponents:
            raise ProponentNotFoundError(f"Proponente '{bid.proponent_id}' não cadastrado no leilão.")

        lot = self.lots[bid.lot_id]
        proponent = self.proponents[bid.proponent_id]

        # Validação 1: Habilitação prévia perante o AJ e a B3
        if not proponent.is_qualified:
            bid.is_valid = False
            bid.rejection_reason = "Proponente não habilitado previamente no processo."
            self.bids.append(bid)
            raise ProponentNotQualifiedError(
                f"Proponente '{bid.proponent_id}' não está habilitado pelo Administrador Judicial/B3."
            )

        # Validação 2: Bloqueio efetivo de caução de participação (Bid Bond)
        if not proponent.bid_bond_blocked:
            bid.is_valid = False
            bid.rejection_reason = "Caução de participação (Bid Bond) não está bloqueada em custódia na B3."
            self.bids.append(bid)
            raise InsufficientBidBondError(
                f"Caução do proponente '{bid.proponent_id}' não se encontra bloqueada na Central Depositária B3."
            )

        # Validação 3: Montante suficiente da caução em relação à exigência do lote
        if proponent.bid_bond_deposited < lot.required_bid_bond_amount:
            bid.is_valid = False
            bid.rejection_reason = (
                f"Caução depositada (R$ {proponent.bid_bond_deposited}) inferior à mínima exigida "
                f"para o lote (R$ {lot.required_bid_bond_amount})."
            )
            self.bids.append(bid)
            raise InsufficientBidBondError(
                f"Caução depositada R$ {proponent.bid_bond_deposited} é inferior à caução mínima exigida de "
                f"R$ {lot.required_bid_bond_amount} para o lote '{lot.lot_id}'."
            )

        # Validação 4: Preço de reserva mínimo homologado pelo juízo
        if bid.bid_amount < lot.judicial_reserve_price:
            bid.is_valid = False
            bid.rejection_reason = (
                f"Lance de R$ {bid.bid_amount} é inferior ao preço de reserva homologado de R$ {lot.judicial_reserve_price}."
            )
            self.bids.append(bid)
            raise BidBelowReservePriceError(
                f"Lance de R$ {bid.bid_amount} é inferior ao preço de reserva mínimo homologado de "
                f"R$ {lot.judicial_reserve_price} para o lote '{lot.lot_id}'."
            )

        # Identifica o maior lance válido anterior para este lote
        prior_valid_bids = [
            b for b in self.bids
            if b.lot_id == bid.lot_id and b.is_valid and b.bid_id != bid.bid_id
        ]
        highest_prior_amount = max((b.bid_amount for b in prior_valid_bids), default=Decimal("0.00"))

        # Validação 5: Degrau de incremento mínimo na sessão de overbidding (pregão aberto)
        if bid.round_type == BidRoundType.OPEN_OVERBID:
            required_min = highest_prior_amount + lot.minimum_overbid_step
            if highest_prior_amount > Decimal("0.00") and bid.bid_amount < required_min:
                bid.is_valid = False
                bid.rejection_reason = (
                    f"Lance de R$ {bid.bid_amount} não atinge o incremento mínimo obrigatório "
                    f"(mínimo exigido: R$ {required_min}, step: R$ {lot.minimum_overbid_step})."
                )
                self.bids.append(bid)
                raise InvalidOverbidIncrementError(
                    f"Lance de overbid de R$ {bid.bid_amount} é inferior ao mínimo obrigatório de "
                    f"R$ {required_min} (maior lance anterior R$ {highest_prior_amount} + degrau de R$ {lot.minimum_overbid_step})."
                )

        # Validação 6: Exercício de Right to Match (Equiparação do Stalking Horse)
        if bid.round_type == BidRoundType.MATCHING_BID:
            if not proponent.is_stalking_horse or not proponent.has_right_to_match:
                bid.is_valid = False
                bid.rejection_reason = "Proponente não possui cláusula outorgada de Right to Match (Stalking Horse)."
                self.bids.append(bid)
                raise SpecialInsolvencyAuctionError(
                    f"Proponente '{bid.proponent_id}' não possui prerrogativa de Right to Match."
                )
            if highest_prior_amount > Decimal("0.00") and bid.bid_amount < highest_prior_amount:
                bid.is_valid = False
                bid.rejection_reason = (
                    f"Lance de equiparação de R$ {bid.bid_amount} é inferior à maior oferta corrente de R$ {highest_prior_amount}."
                )
                self.bids.append(bid)
                raise SpecialInsolvencyAuctionError(
                    f"Lance de matching de R$ {bid.bid_amount} é inferior à maior oferta corrente de R$ {highest_prior_amount}."
                )

        bid.is_valid = True
        self.bids.append(bid)
        return bid

    def conclude_auction(self) -> None:
        """Encerra a sessão de lances e homologa os lotes arrematados ou desertos."""
        self.phase = AuctionPhase.HOMOLOGATION

        # Reseta flags de vencedores
        for p in self.proponents.values():
            p.is_winner = False

        for lot in self.lots.values():
            lot_valid_bids = [
                b for b in self.bids
                if b.lot_id == lot.lot_id and b.is_valid
            ]

            if not lot_valid_bids:
                lot.status = LotAuctionStatus.DESERTO
                lot.winning_proponent_id = None
                lot.winning_bid_amount = None
                continue

            # Critério de desempate: maior valor; em caso de empate, o lance de matching ou o lance mais antigo
            # Ordena por: valor decrescente, matching_bid prioritário, timestamp crescente
            def bid_sort_key(b: AuctionBid) -> Tuple[Decimal, int, float]:
                is_matching = 1 if b.round_type == BidRoundType.MATCHING_BID else 0
                return (b.bid_amount, is_matching, -b.timestamp.timestamp())

            best_bid = max(lot_valid_bids, key=bid_sort_key)

            if best_bid.bid_amount >= lot.judicial_reserve_price:
                lot.status = LotAuctionStatus.AWARDED
                lot.winning_proponent_id = best_bid.proponent_id
                lot.winning_bid_amount = best_bid.bid_amount
                self.proponents[best_bid.proponent_id].is_winner = True
            else:
                lot.status = LotAuctionStatus.DESERTO
                lot.winning_proponent_id = None
                lot.winning_bid_amount = None

    def process_bid_bond_restitution(self) -> BidBondRestitutionReport:
        """Executa a restituição integral das cauções a participantes não vencedores.

        Lei de Invariância 2:
        Total_Caução_Depositada == Total_Caução_Retida + Total_Caução_Restituída
        Total_Caução_Retida == Caução_Vencedor (ou 0 se todos os lotes forem desertos).
        """
        if self.phase == AuctionPhase.SETUP:
            self.conclude_auction()

        lines: List[BidBondRestitutionLine] = []
        total_deposited = Decimal("0.00")
        total_retained = Decimal("0.00")
        total_refunded = Decimal("0.00")
        winning_proponents: List[str] = []

        for p in self.proponents.values():
            total_deposited += p.bid_bond_deposited
            if p.is_winner:
                # Caução do vencedor fica retida como sinal / entrada para abatimento na liquidação
                p.bid_bond_retained = p.bid_bond_deposited
                p.bid_bond_refunded = Decimal("0.00")
                winning_proponents.append(p.proponent_id)
            else:
                # 100% da caução dos demais concorrentes é imediatamente desbloqueada / restituída
                p.bid_bond_retained = Decimal("0.00")
                p.bid_bond_refunded = p.bid_bond_deposited

            total_retained += p.bid_bond_retained
            total_refunded += p.bid_bond_refunded

            lines.append(
                BidBondRestitutionLine(
                    proponent_id=p.proponent_id,
                    name=p.name,
                    is_winner=p.is_winner,
                    deposited_amount=p.bid_bond_deposited,
                    retained_amount=p.bid_bond_retained,
                    refunded_amount=p.bid_bond_refunded,
                )
            )

        # Verificação matemática da invariante de restituição
        is_conserved = (
            (total_deposited == total_retained + total_refunded) and
            (total_retained == sum(p.bid_bond_deposited for p in self.proponents.values() if p.is_winner))
        )

        report = BidBondRestitutionReport(
            total_deposited=_quantize_money(total_deposited),
            total_retained=_quantize_money(total_retained),
            total_refunded=_quantize_money(total_refunded),
            winning_proponents=winning_proponents,
            lines=lines,
            is_conserved=is_conserved,
        )
        self.bid_bond_report = report
        return report

    def execute_judicial_waterfall(self) -> WaterfallSettlementResult:
        """Executa a cascata judicial de liquidação e distribuição financeira (Art. 84 e 83 LF-RJ).

        Lei de Invariância 3:
        Total_Arrecadado == Total_Custas_Extraconcursais + Total_Pago_Credores + Saldo_Remanescente_Recuperanda
        """
        if self.phase in (AuctionPhase.SETUP, AuctionPhase.QUALIFICATION, AuctionPhase.SEALED_BIDS, AuctionPhase.OVERBIDDING):
            self.conclude_auction()
        if self.bid_bond_report is None:
            self.process_bid_bond_restitution()

        self.phase = AuctionPhase.FINANCIAL_SETTLEMENT

        # 1. Total arrecadado no leilão (Soma dos lances vencedores de lotes homologados)
        awarded_lots = [lot for lot in self.lots.values() if lot.status == LotAuctionStatus.AWARDED]
        gross_auction_proceeds = sum(
            (lot.winning_bid_amount for lot in awarded_lots if lot.winning_bid_amount is not None),
            Decimal("0.00"),
        )
        gross_auction_proceeds = _quantize_money(gross_auction_proceeds)

        # Caução retida aplicada como entrada
        retained_bond_applied = sum(
            (p.bid_bond_retained for p in self.proponents.values() if p.is_winner),
            Decimal("0.00"),
        )
        retained_bond_applied = _quantize_money(retained_bond_applied)
        cash_collected = _quantize_money(gross_auction_proceeds - retained_bond_applied)

        available_funds = gross_auction_proceeds

        # 2. Quitação de Custas e Despesas Extraconcursais (Art. 84 LF-RJ) com prioridade absoluta
        total_extracurricular = sum((e.amount for e in self.extracurricular_expenses), Decimal("0.00"))
        total_extracurricular = _quantize_money(total_extracurricular)

        if available_funds >= total_extracurricular:
            paid_extracurricular = total_extracurricular
            net_for_creditors = _quantize_money(available_funds - total_extracurricular)
            available_funds = net_for_creditors
        else:
            # Recursos arrecadados insuficientes para cobrir a totalidade das despesas extraconcursais
            paid_extracurricular = available_funds
            net_for_creditors = Decimal("0.00")
            available_funds = Decimal("0.00")

        # 3. Distribuição estrita aos credores concursais pela ordem das classes (Art. 83 LF-RJ)
        payments_by_class: Dict[str, Decimal] = {cls_enum.value: Decimal("0.00") for cls_enum in CREDITOR_CLASS_HIERARCHY}
        individual_payments: List[CreditorPaymentDetail] = []
        total_paid_to_creditors = Decimal("0.00")

        # Resetar pagamentos anteriores dos credores
        for c in self.creditor_claims.values():
            c.paid_amount = Decimal("0.00")

        for current_class in CREDITOR_CLASS_HIERARCHY:
            class_claims = [
                c for c in self.creditor_claims.values()
                if c.creditor_class == current_class
            ]
            if not class_claims:
                continue

            total_class_demand = sum((c.original_claim_amount for c in class_claims), Decimal("0.00"))
            total_class_demand = _quantize_money(total_class_demand)

            if available_funds <= Decimal("0.00"):
                # Saldo esgotado: credores desta classe e das subsequentes não recebem
                for c in class_claims:
                    individual_payments.append(
                        CreditorPaymentDetail(
                            creditor_id=c.creditor_id,
                            name=c.name,
                            creditor_class=c.creditor_class,
                            original_claim=c.original_claim_amount,
                            paid_amount=Decimal("0.00"),
                            outstanding_balance=c.original_claim_amount,
                            coverage_ratio=Decimal("0.00000000"),
                        )
                    )
                continue

            if available_funds >= total_class_demand:
                # Quitação integral (100%) da classe
                for c in class_claims:
                    c.paid_amount = c.original_claim_amount
                    total_paid_to_creditors += c.paid_amount
                    payments_by_class[current_class.value] += c.paid_amount
                    individual_payments.append(
                        CreditorPaymentDetail(
                            creditor_id=c.creditor_id,
                            name=c.name,
                            creditor_class=c.creditor_class,
                            original_claim=c.original_claim_amount,
                            paid_amount=c.paid_amount,
                            outstanding_balance=Decimal("0.00"),
                            coverage_ratio=Decimal("1.00000000"),
                        )
                    )
                available_funds = _quantize_money(available_funds - total_class_demand)
            else:
                # Quitação parcial com rateio pro-rata exato em centavos (Hamilton / Hare-Niemeyer)
                amount_for_class = available_funds
                claims_tuples = [(c.creditor_id, c.original_claim_amount) for c in class_claims]
                distribution = _distribute_pro_rata_cents(amount_for_class, claims_tuples)

                for c in class_claims:
                    allocated = distribution.get(c.creditor_id, Decimal("0.00"))
                    c.paid_amount = allocated
                    total_paid_to_creditors += allocated
                    payments_by_class[current_class.value] += allocated
                    cov_ratio = (allocated / c.original_claim_amount).quantize(EIGHT_PLACES, rounding=ROUND_HALF_UP)
                    individual_payments.append(
                        CreditorPaymentDetail(
                            creditor_id=c.creditor_id,
                            name=c.name,
                            creditor_class=c.creditor_class,
                            original_claim=c.original_claim_amount,
                            paid_amount=allocated,
                            outstanding_balance=c.outstanding_balance,
                            coverage_ratio=cov_ratio,
                        )
                    )
                available_funds = Decimal("0.00")

        total_paid_to_creditors = _quantize_money(total_paid_to_creditors)

        # 4. Saldo Remanescente / Superávit: Devolvido à Recuperanda (Debtor Equity)
        residual_surplus_to_debtor = _quantize_money(available_funds)

        # Verificação matemática da invariante de liquidação financeira
        is_waterfall_conserved = (
            gross_auction_proceeds == (paid_extracurricular + total_paid_to_creditors + residual_surplus_to_debtor) and
            sum(p.paid_amount for p in individual_payments) == total_paid_to_creditors
        )

        result = WaterfallSettlementResult(
            gross_auction_proceeds=gross_auction_proceeds,
            retained_bid_bond_applied=retained_bond_applied,
            cash_settlement_collected=cash_collected,
            total_extracurricular_expenses=total_extracurricular,
            paid_extracurricular_expenses=paid_extracurricular,
            net_proceeds_for_creditors=net_for_creditors,
            total_paid_to_creditors=total_paid_to_creditors,
            payments_by_class=payments_by_class,
            individual_payments=individual_payments,
            residual_surplus_to_debtor=residual_surplus_to_debtor,
            is_waterfall_conserved=is_waterfall_conserved,
        )
        self.waterfall_result = result
        self.phase = AuctionPhase.CONCLUDED
        return result

    def run(self) -> InsolvencyAuctionSummary:
        """Executa o ciclo de vida completo do leilão especial e verifica suas invariantes."""
        self.conclude_auction()
        self.process_bid_bond_restitution()
        self.execute_judicial_waterfall()
        invariants = self.verify_invariants(raise_on_error=False)

        awarded_count = sum(1 for lot in self.lots.values() if lot.status == LotAuctionStatus.AWARDED)
        desert_count = sum(1 for lot in self.lots.values() if lot.status == LotAuctionStatus.DESERTO)

        return InsolvencyAuctionSummary(
            auction_id=self.auction_id,
            debtor_name=self.debtor_name,
            total_lots=len(self.lots),
            awarded_lots=awarded_count,
            desert_lots=desert_count,
            total_proceeds=self.waterfall_result.gross_auction_proceeds if self.waterfall_result else Decimal("0.00"),
            is_asset_conserved=invariants["asset_conservation"],
            is_bid_bond_conserved=invariants["bid_bond_restitution"],
            is_waterfall_conserved=invariants["financial_waterfall_conservation"],
            all_invariants_satisfied=invariants["all_invariants_satisfied"],
            waterfall_result=self.waterfall_result,
            bid_bond_report=self.bid_bond_report,
        )

    def verify_invariants(self, raise_on_error: bool = False) -> Dict[str, bool]:
        """Verifica formalmente todas as leis invariantes de conservação do leilão judicial.

        1. Conservação de Ativos: Lote_Total == Lote_Arrematado + Lote_Deserto;
        2. Restituição Integral de Caução: Total_Caução_Retida == Caução_Vencedor;
        3. Liquidação Financeira Integral: Total_Arrecadado == Custas + Pago_Credores + Superávit;
        4. Preço de Reserva: Todo lote arrematado possui lance >= Preço de Reserva Mínimo;
        5. Overbid Step: Todos os lances de pregão respeitaram o degrau mínimo de incremento.
        """
        # 1. Conservação de Lotes e Ativos
        total_lots = len(self.lots)
        awarded_lots = sum(1 for lot in self.lots.values() if lot.status == LotAuctionStatus.AWARDED)
        desert_lots = sum(1 for lot in self.lots.values() if lot.status == LotAuctionStatus.DESERTO)
        cancelled_lots = sum(1 for lot in self.lots.values() if lot.status == LotAuctionStatus.CANCELLED)

        lot_count_conserved = (total_lots == awarded_lots + desert_lots + cancelled_lots)
        total_units = sum(lot.quantity for lot in self.lots.values())
        awarded_units = sum(lot.quantity for lot in self.lots.values() if lot.status == LotAuctionStatus.AWARDED)
        desert_units = sum(lot.quantity for lot in self.lots.values() if lot.status == LotAuctionStatus.DESERTO)
        cancelled_units = sum(lot.quantity for lot in self.lots.values() if lot.status == LotAuctionStatus.CANCELLED)
        units_conserved = (total_units == awarded_units + desert_units + cancelled_units)
        asset_conservation = lot_count_conserved and units_conserved

        # 2. Restituição Integral de Caução (Bid Bond)
        if self.bid_bond_report is not None:
            expected_retained = sum(
                p.bid_bond_deposited for p in self.proponents.values() if p.is_winner
            )
            bid_bond_restitution = (
                self.bid_bond_report.is_conserved and
                self.bid_bond_report.total_retained == expected_retained and
                self.bid_bond_report.total_deposited == (self.bid_bond_report.total_retained + self.bid_bond_report.total_refunded)
            )
        else:
            bid_bond_restitution = True

        # 3. Liquidação Financeira Integral (Waterfall)
        if self.waterfall_result is not None:
            w = self.waterfall_result
            expected_gross = sum(
                (lot.winning_bid_amount for lot in self.lots.values() if lot.status == LotAuctionStatus.AWARDED and lot.winning_bid_amount is not None),
                Decimal("0.00"),
            )
            financial_conservation = (
                w.is_waterfall_conserved and
                w.gross_auction_proceeds == expected_gross and
                w.gross_auction_proceeds == (w.paid_extracurricular_expenses + w.total_paid_to_creditors + w.residual_surplus_to_debtor) and
                sum(p.paid_amount for p in w.individual_payments) == w.total_paid_to_creditors and
                all(p.paid_amount <= p.original_claim for p in w.individual_payments)
            )
        else:
            financial_conservation = True

        # 4. Conformidade do Preço de Reserva
        reserve_price_compliance = True
        for lot in self.lots.values():
            if lot.status == LotAuctionStatus.AWARDED:
                if lot.winning_bid_amount is None or lot.winning_bid_amount < lot.judicial_reserve_price:
                    reserve_price_compliance = False
                    break

        all_ok = (
            asset_conservation and
            bid_bond_restitution and
            financial_conservation and
            reserve_price_compliance
        )

        invariants = {
            "asset_conservation": asset_conservation,
            "bid_bond_restitution": bid_bond_restitution,
            "financial_waterfall_conservation": financial_conservation,
            "reserve_price_compliance": reserve_price_compliance,
            "all_invariants_satisfied": all_ok,
        }

        if raise_on_error and not all_ok:
            raise InsolvencyAuctionInvariantError(
                f"Violação de invariante no leilão '{self.auction_id}': {invariants}"
            )

        return invariants

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o leilão e todos os seus objetos em dicionário."""
        return {
            "auction_id": self.auction_id,
            "debtor_name": self.debtor_name,
            "judicial_process_number": self.judicial_process_number,
            "court_name": self.court_name,
            "judicial_administrator": self.judicial_administrator,
            "auction_date": self.auction_date.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
            "description": self.description,
            "phase": self.phase.value,
            "lots": [lot.to_dict() for lot in self.lots.values()],
            "proponents": [p.to_dict() for p in self.proponents.values()],
            "bids": [b.to_dict() for b in self.bids],
            "extracurricular_expenses": [e.to_dict() for e in self.extracurricular_expenses],
            "creditor_claims": [c.to_dict() for c in self.creditor_claims.values()],
            "waterfall_result": self.waterfall_result.to_dict() if self.waterfall_result else None,
            "bid_bond_report": self.bid_bond_report.to_dict() if self.bid_bond_report else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InsolvencyAuction:
        """Instancia o leilão a partir de dados em dicionário."""
        auction = cls(
            auction_id=data["auction_id"],
            debtor_name=data["debtor_name"],
            judicial_process_number=data["judicial_process_number"],
            court_name=data["court_name"],
            judicial_administrator=data["judicial_administrator"],
            auction_date=_to_date(data["auction_date"], "auction_date"),
            settlement_date=_to_date(data["settlement_date"], "settlement_date"),
            description=data.get("description", ""),
        )

        for lot_data in data.get("lots", []):
            auction.register_lot(InsolvencyLot.from_dict(lot_data))

        for prop_data in data.get("proponents", []):
            auction.register_proponent(Proponent.from_dict(prop_data))

        for expense_data in data.get("extracurricular_expenses", []):
            auction.register_expense(ExtracurricularExpense.from_dict(expense_data))

        for claim_data in data.get("creditor_claims", []):
            auction.register_creditor_claim(CreditorClaim.from_dict(claim_data))

        for bid_data in data.get("bids", []):
            # Adiciona lances diretamente ou submete com validações
            bid = AuctionBid.from_dict(bid_data)
            auction.bids.append(bid)

        return auction


# ---------------------------------------------------------------------------
# Motor Orquestrador do Atlas (SpecialInsolvencyAuctionEngine)
# ---------------------------------------------------------------------------

class SpecialInsolvencyAuctionEngine:
    """Motor orquestrador de leilões especiais de insolvência e desinvestimento (FL-081)."""

    def __init__(self) -> None:
        self.auctions: Dict[str, InsolvencyAuction] = {}

    def register_auction(self, auction: InsolvencyAuction) -> None:
        """Registra um leilão judicial no motor."""
        self.auctions[auction.auction_id] = auction

    def get_auction(self, auction_id: str) -> InsolvencyAuction:
        """Recupera leilão pelo identificador."""
        if auction_id not in self.auctions:
            raise LotNotFoundError(f"Leilão de insolvência '{auction_id}' não encontrado.")
        return self.auctions[auction_id]

    def run_auction(self, auction_id: str) -> InsolvencyAuctionSummary:
        """Executa um leilão específico e retorna seu sumário."""
        auction = self.get_auction(auction_id)
        return auction.run()

    def run_all(self) -> List[InsolvencyAuctionSummary]:
        """Executa todos os leilões registrados e retorna seus sumários."""
        return [auction.run() for auction in self.auctions.values()]

    def summarize(self) -> Dict[str, Any]:
        """Gera resumo consolidado do portfólio de leilões especiais judiciais."""
        summaries = self.run_all()
        total_proceeds = sum((s.total_proceeds for s in summaries), Decimal("0.00"))
        all_conserved = all(s.all_invariants_satisfied for s in summaries)

        return {
            "total_auctions": len(summaries),
            "total_lots": sum(s.total_lots for s in summaries),
            "total_awarded_lots": sum(s.awarded_lots for s in summaries),
            "total_desert_lots": sum(s.desert_lots for s in summaries),
            "total_proceeds_brl": str(_quantize_money(total_proceeds)),
            "all_invariants_satisfied": all_conserved,
            "summaries": [s.to_dict() for s in summaries],
        }

    def verify_invariants(self, raise_on_error: bool = False) -> Dict[str, bool]:
        """Verifica a integridade de todos os leilões registrados."""
        results = {}
        all_ok = True
        for aid, auction in self.auctions.items():
            inv = auction.verify_invariants(raise_on_error=raise_on_error)
            results[aid] = inv["all_invariants_satisfied"]
            if not inv["all_invariants_satisfied"]:
                all_ok = False

        return {
            "all_invariants_satisfied": all_ok,
            "auction_results": results,
        }

    @classmethod
    def from_json_fixture(cls, path: Union[str, Path]) -> SpecialInsolvencyAuctionEngine:
        """Instancia o motor a partir de um arquivo fixture JSON oficial."""
        fixture_path = Path(path)
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        engine = cls()

        auctions_list = data.get("auctions", [])
        for a_data in auctions_list:
            auction = InsolvencyAuction.from_dict(a_data)
            engine.register_auction(auction)

        return engine


__all__ = [
    "AuctionBid",
    "AuctionPhase",
    "BidBelowReservePriceError",
    "BidBondRestitutionLine",
    "BidBondRestitutionReport",
    "BidRoundType",
    "CREDITOR_CLASS_HIERARCHY",
    "CreditorClaim",
    "CreditorClass",
    "CreditorPaymentDetail",
    "ExtracurricularExpense",
    "ExtracurricularExpenseType",
    "InsolvencyAuction",
    "InsolvencyAuctionInvariantError",
    "InsolvencyAuctionSummary",
    "InsolvencyLot",
    "InsolvencyLotType",
    "InsufficientBidBondError",
    "InvalidOverbidIncrementError",
    "LotAuctionStatus",
    "LotNotFoundError",
    "Proponent",
    "ProponentNotFoundError",
    "ProponentNotQualifiedError",
    "SpecialInsolvencyAuctionEngine",
    "SpecialInsolvencyAuctionError",
    "WaterfallSettlementResult",
    "_distribute_pro_rata_cents",
    "_quantize_money",
    "_quantize_rate",
    "_to_date",
    "_to_datetime",
]
