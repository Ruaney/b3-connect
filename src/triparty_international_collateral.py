"""Custódia Internacional e Gestão de Garantias Triparty Repo — Câmara B3 (FL-099).

Implementa a infraestrutura da Câmara B3 e Central Depositária para gestão de garantias
transfronteiriças (Cross-Border Collateral) e operações compromissadas internacionais
(Triparty Repo) integradas aos International Central Securities Depositories (ICSDs):
1. Custodiantes Internacionais Integrados:
   - Euroclear Bank SA/NV (Bruxelas - BIC: EACLBBEB);
   - Clearstream Banking S.A. (Luxemburgo - BIC: MRQCLULL);
   - The Depository Trust Company - DTC / DTCC (Nova York - BIC: DTCXPENY).
2. Conversão Cambial Diária PTAX BACEN:
   - Cotações PTAX de Compra (bid) e Venda (ask) para USD, EUR e GBP;
   - Avaliação prudencial estrita com base na taxa PTAX de Compra (bid) para garantias;
   - Regra estrita: 0 < PTAX_bid <= PTAX_ask.
3. Matriz Conservadora de Haircuts e Buffer Cambial:
   - Haircuts base soberanos escalonados por rating de crédito (AAA a BBB-) e tenor residual;
   - Rejeição absoluta de emissores Sub-Investment Grade (High Yield / junk) e sancionados;
   - FX Volatility Buffer paramétrico (USD: 8%, EUR: 10%, GBP: 12%) contra oscilações extremas;
   - Fator de corte combinado: H_total = min(H_base + H_fx, H_max), limitado ao teto prudencial de 50%.
4. Mensageria SWIFT ISO 15022 / ISO 20022 e Bloqueio Fiduciário:
   - MT540 (Receive Free): Instrução e confirmação de bloqueio fiduciário com incomunicabilidade;
   - MT542 (Deliver Free): Instrução de desoneração e liberação de garantias;
   - Campo :70D::SPCL com cláusula legal de incomunicabilidade patrimonial e garantia em favor da B3.
5. Incomunicabilidade Patrimonial e Safe-Release Guardrail:
   - Segregação patrimonial fiduciária nos termos da Lei nº 13.986/2020 e regulamento de custódia da B3;
   - Segregação estrita entre Contas Proprietárias (House) e Contas de Clientes (Client);
   - Trava Safe-Release: Qualquer liberação parcial ou total exige que o colateral remanescente
     cubra integralmente a margem requerida da operação.
6. Leis de Invariantes Formais:
   - LAW-105A: Cobertura cambial e haircut prudencial (0 <= H_total < 1.00, V_elegivel <= V_bruto, V_elegivel >= Margem);
   - LAW-105B: Inviolabilidade do bloqueio internacional (Conservação de custódia Q_total == Q_livre + Q_bloqueada,
     segregação de contas e incomunicabilidade fiduciária sem liberação em déficit);
   - LAW-105C: Conservação bilateral soma-zero na liquidação (Débito do tomador == Crédito do doador de caixa,
     centavo a centavo, sem geração nem dissipação de liquidez).

100% Python Standard Library (decimal, datetime, dataclasses, enum, json, pathlib, typing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# Constantes de Precisão Numérica B3 / ICSD
TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")
EIGHT_PLACES = Decimal("0.00000001")

MAX_REGULATORY_HAIRCUT = Decimal("0.500000")  # Teto de haircut (50%)
MIN_REGULATORY_HAIRCUT = Decimal("0.020000")  # Piso de haircut (2%)


def round_cents(val: Union[Decimal, str, float]) -> Decimal:
    """Arredonda valor financeiro para centavos (2 casas decimais) com ROUND_HALF_UP."""
    d = Decimal(str(val))
    return d.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def round_quantity(val: Union[Decimal, str, float]) -> Decimal:
    """Arredonda quantidade de títulos para 4 casas decimais."""
    d = Decimal(str(val))
    return d.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def round_rate(val: Union[Decimal, str, float]) -> Decimal:
    """Arredonda taxa, taxa de câmbio ou haircut para 6 casas decimais."""
    d = Decimal(str(val))
    return d.quantize(SIX_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Exceções Especializadas de Custódia e Triparty Repo
# ---------------------------------------------------------------------------

class TripartyCollateralError(Exception):
    """Exceção base para o fluxo de custódia internacional e triparty repo (FL-099)."""
    pass


class IneligibleCollateralError(TripartyCollateralError):
    """Lançada quando o ativo, rating soberano ou custodiante é inelegível."""
    pass


class InsufficientCollateralError(TripartyCollateralError):
    """Lançada quando o colateral internacional alocado não cobre a margem requerida."""
    pass


class MarginReleaseViolationError(TripartyCollateralError):
    """Lançada quando a desoneração de garantia violaria o Safe-Release Guardrail."""
    pass


class CustodyConservationError(TripartyCollateralError):
    """Lançada quando a conservação de custódia ou segregação de contas é violada."""
    pass


class SwiftMessagingError(TripartyCollateralError):
    """Lançada quando ocorre inconsistência na geração ou validação de mensagem SWIFT."""
    pass


# ---------------------------------------------------------------------------
# Enums do Domínio Internacional
# ---------------------------------------------------------------------------

class InternationalCustodian(str, Enum):
    """Depositárias centrais internacionais integradas à B3."""
    EUROCLEAR = "EUROCLEAR"      # Euroclear Bank SA/NV (Bruxelas) - BIC: EACLBBEB
    CLEARSTREAM = "CLEARSTREAM"  # Clearstream Banking S.A. (Luxemburgo) - BIC: MRQCLULL
    DTC = "DTC"                  # The Depository Trust Company (Nova York) - BIC: DTCXPENY

    @property
    def bic(self) -> str:
        mapping = {
            InternationalCustodian.EUROCLEAR: "EACLBBEB",
            InternationalCustodian.CLEARSTREAM: "MRQCLULL",
            InternationalCustodian.DTC: "DTCXPENY",
        }
        return mapping[self]


class AccountCategory(str, Enum):
    """Categoria regulatória de segregação de conta de custódia."""
    HOUSE = "HOUSE"    # Carteira Própria
    CLIENT = "CLIENT"  # Carteira de Clientes / Terceiros Segregada


class SovereignRating(str, Enum):
    """Escala de rating de crédito global para títulos soberanos internacionais."""
    AAA = "AAA"
    AA_PLUS = "AA+"
    AA = "AA"
    AA_MINUS = "AA-"
    A_PLUS = "A+"
    A = "A"
    A_MINUS = "A-"
    BBB_PLUS = "BBB+"
    BBB = "BBB"
    BBB_MINUS = "BBB-"
    SUB_INVESTMENT = "SUB_INVESTMENT"  # Inelegível (High Yield / Junk)


class InternationalAssetType(str, Enum):
    """Tipos de ativos elegíveis em custódia internacional."""
    US_TREASURY = "US_TREASURY"          # US T-Bills, T-Notes, T-Bonds (DTC / Euroclear)
    GERMAN_BUND = "GERMAN_BUND"          # German Bunds, Bobls, Bubills (Euroclear / Clearstream)
    UK_GILT = "UK_GILT"                  # UK Gilts (Euroclear)
    SUPRANATIONAL = "SUPRANATIONAL"      # World Bank, EIB, BIS Supranational Bonds


class SwiftMessageType(str, Enum):
    """Tipos de mensagens SWIFT ISO 15022 suportadas."""
    MT540 = "MT540"  # Receive Free (Bloqueio fiduciário em favor da B3)
    MT542 = "MT542"  # Deliver Free (Desoneração / Safe-Release de garantia)
    MT541 = "MT541"  # Receive Against Payment
    MT543 = "MT543"  # Deliver Against Payment


class SwiftMessageStatus(str, Enum):
    """Status de processamento da mensagem SWIFT."""
    GENERATED = "GENERATED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    MATCHED = "MATCHED"
    SETTLED = "SETTLED"
    REJECTED = "REJECTED"


class RepoStatus(str, Enum):
    """Ciclo de vida de uma operação de Triparty Repo."""
    INITIATED = "INITIATED"
    COLLATERAL_ALLOCATED = "COLLATERAL_ALLOCATED"
    LOCKED_FIDUCIARY = "LOCKED_FIDUCIARY"
    SETTLED = "SETTLED"
    DEFAULT_EXECUTED = "DEFAULT_EXECUTED"


# ---------------------------------------------------------------------------
# Estruturas de Dados do Domínio
# ---------------------------------------------------------------------------

@dataclass
class PtaxRate:
    """Cotação oficial de câmbio PTAX divulgada pelo Banco Central do Brasil."""
    currency: str
    ptax_bid: Decimal  # Taxa de Compra (utilizada conservadoramente para garantias)
    ptax_ask: Decimal  # Taxa de Venda
    rate_date: str

    def __post_init__(self) -> None:
        self.ptax_bid = round_rate(self.ptax_bid)
        self.ptax_ask = round_rate(self.ptax_ask)
        if self.ptax_bid <= Decimal("0.000000"):
            raise ValueError(f"Taxa PTAX bid ({self.ptax_bid}) deve ser estritamente positiva")
        if self.ptax_bid > self.ptax_ask:
            raise ValueError(f"Taxa PTAX bid ({self.ptax_bid}) não pode exceder ask ({self.ptax_ask})")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "currency": self.currency,
            "ptax_bid": str(self.ptax_bid),
            "ptax_ask": str(self.ptax_ask),
            "rate_date": self.rate_date,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PtaxRate:
        return cls(
            currency=data["currency"],
            ptax_bid=Decimal(data["ptax_bid"]),
            ptax_ask=Decimal(data["ptax_ask"]),
            rate_date=data["rate_date"],
        )


@dataclass
class CustodyPosition:
    """Posição custodiada de um ativo internacional em conta segregada."""
    isin: str
    symbol: str
    currency: str
    total_quantity: Decimal
    available_quantity: Decimal
    locked_quantity: Decimal

    def __post_init__(self) -> None:
        self.total_quantity = round_quantity(self.total_quantity)
        self.available_quantity = round_quantity(self.available_quantity)
        self.locked_quantity = round_quantity(self.locked_quantity)
        if self.available_quantity < Decimal("0.0000") or self.locked_quantity < Decimal("0.0000"):
            raise ValueError("Quantidades disponíveis e bloqueadas não podem ser negativas")
        if self.total_quantity != (self.available_quantity + self.locked_quantity):
            raise ValueError(
                f"Conservação violada para ISIN {self.isin}: "
                f"total ({self.total_quantity}) != disponivel ({self.available_quantity}) + bloqueado ({self.locked_quantity})"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "isin": self.isin,
            "symbol": self.symbol,
            "currency": self.currency,
            "total_quantity": str(self.total_quantity),
            "available_quantity": str(self.available_quantity),
            "locked_quantity": str(self.locked_quantity),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CustodyPosition:
        return cls(
            isin=data["isin"],
            symbol=data["symbol"],
            currency=data["currency"],
            total_quantity=Decimal(data["total_quantity"]),
            available_quantity=Decimal(data["available_quantity"]),
            locked_quantity=Decimal(data["locked_quantity"]),
        )


@dataclass
class CustodyAccount:
    """Conta de custódia internacional mantida no Euroclear, Clearstream ou DTC."""
    account_id: str
    custodian: InternationalCustodian
    category: AccountCategory
    participant_id: str
    is_fiduciary_segregated: bool = True  # Incomunicabilidade patrimonial garantida
    positions: Dict[str, CustodyPosition] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "custodian": self.custodian.value,
            "category": self.category.value,
            "participant_id": self.participant_id,
            "is_fiduciary_segregated": self.is_fiduciary_segregated,
            "positions": {isin: pos.to_dict() for isin, pos in self.positions.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CustodyAccount:
        acc = cls(
            account_id=data["account_id"],
            custodian=InternationalCustodian(data["custodian"]),
            category=AccountCategory(data["category"]),
            participant_id=data["participant_id"],
            is_fiduciary_segregated=data.get("is_fiduciary_segregated", True),
        )
        for isin, p_data in data.get("positions", {}).items():
            acc.positions[isin] = CustodyPosition.from_dict(p_data)
        return acc


@dataclass
class CollateralAllocation:
    """Alocação específica de ativo internacional como colateral de Triparty Repo."""
    allocation_id: str
    isin: str
    custodian: InternationalCustodian
    account_id: str
    rating: SovereignRating
    tenor_years: Decimal
    quantity: Decimal
    unit_price_foreign: Decimal
    currency: str
    ptax_bid_rate: Decimal
    base_haircut: Decimal
    fx_haircut_buffer: Decimal
    total_haircut: Decimal
    gross_value_foreign: Decimal
    gross_value_brl: Decimal
    eligible_value_brl: Decimal
    fiduciary_lock_reference: str
    swift_message_id: str

    def __post_init__(self) -> None:
        self.quantity = round_quantity(self.quantity)
        self.unit_price_foreign = Decimal(str(self.unit_price_foreign))
        self.ptax_bid_rate = round_rate(self.ptax_bid_rate)
        self.base_haircut = round_rate(self.base_haircut)
        self.fx_haircut_buffer = round_rate(self.fx_haircut_buffer)
        self.total_haircut = round_rate(self.total_haircut)
        self.gross_value_foreign = Decimal(str(self.gross_value_foreign))
        self.gross_value_brl = round_cents(self.gross_value_brl)
        self.eligible_value_brl = round_cents(self.eligible_value_brl)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allocation_id": self.allocation_id,
            "isin": self.isin,
            "custodian": self.custodian.value,
            "account_id": self.account_id,
            "rating": self.rating.value,
            "tenor_years": str(self.tenor_years),
            "quantity": str(self.quantity),
            "unit_price_foreign": str(self.unit_price_foreign),
            "currency": self.currency,
            "ptax_bid_rate": str(self.ptax_bid_rate),
            "base_haircut": str(self.base_haircut),
            "fx_haircut_buffer": str(self.fx_haircut_buffer),
            "total_haircut": str(self.total_haircut),
            "gross_value_foreign": str(self.gross_value_foreign),
            "gross_value_brl": str(self.gross_value_brl),
            "eligible_value_brl": str(self.eligible_value_brl),
            "fiduciary_lock_reference": self.fiduciary_lock_reference,
            "swift_message_id": self.swift_message_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CollateralAllocation:
        return cls(
            allocation_id=data["allocation_id"],
            isin=data["isin"],
            custodian=InternationalCustodian(data["custodian"]),
            account_id=data["account_id"],
            rating=SovereignRating(data["rating"]),
            tenor_years=Decimal(data["tenor_years"]),
            quantity=Decimal(data["quantity"]),
            unit_price_foreign=Decimal(data["unit_price_foreign"]),
            currency=data["currency"],
            ptax_bid_rate=Decimal(data["ptax_bid_rate"]),
            base_haircut=Decimal(data["base_haircut"]),
            fx_haircut_buffer=Decimal(data["fx_haircut_buffer"]),
            total_haircut=Decimal(data["total_haircut"]),
            gross_value_foreign=Decimal(data["gross_value_foreign"]),
            gross_value_brl=Decimal(data["gross_value_brl"]),
            eligible_value_brl=Decimal(data["eligible_value_brl"]),
            fiduciary_lock_reference=data["fiduciary_lock_reference"],
            swift_message_id=data["swift_message_id"],
        )


@dataclass
class SwiftMessage:
    """Mensagem SWIFT estruturada (MT540 / MT542) para bloqueio/liberação fiduciária."""
    message_id: str
    message_type: SwiftMessageType
    sender_bic: str
    receiver_bic: str
    settlement_date: str
    isin: str
    quantity: Decimal
    safekeeping_account: str
    status: SwiftMessageStatus
    fiduciary_lien_tag: str
    raw_mt_text: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "message_type": self.message_type.value,
            "sender_bic": self.sender_bic,
            "receiver_bic": self.receiver_bic,
            "settlement_date": self.settlement_date,
            "isin": self.isin,
            "quantity": str(self.quantity),
            "safekeeping_account": self.safekeeping_account,
            "status": self.status.value,
            "fiduciary_lien_tag": self.fiduciary_lien_tag,
            "raw_mt_text": self.raw_mt_text,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SwiftMessage:
        return cls(
            message_id=data["message_id"],
            message_type=SwiftMessageType(data["message_type"]),
            sender_bic=data["sender_bic"],
            receiver_bic=data["receiver_bic"],
            settlement_date=data["settlement_date"],
            isin=data["isin"],
            quantity=Decimal(data["quantity"]),
            safekeeping_account=data["safekeeping_account"],
            status=SwiftMessageStatus(data["status"]),
            fiduciary_lien_tag=data["fiduciary_lien_tag"],
            raw_mt_text=data["raw_mt_text"],
        )


@dataclass
class TripartyRepoContract:
    """Contrato de Triparty Repo lastreado em colateral transfronteiriço."""
    repo_id: str
    borrower_participant_id: str
    lender_participant_id: str
    triparty_custodian: InternationalCustodian
    nominal_cash_brl: Decimal
    repo_rate_annual: Decimal
    start_date: str
    end_date: str
    business_days: int
    required_margin_brl: Decimal
    status: RepoStatus = RepoStatus.INITIATED
    allocations: List[CollateralAllocation] = field(default_factory=list)
    repurchase_cash_brl: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.nominal_cash_brl = round_cents(self.nominal_cash_brl)
        self.required_margin_brl = round_cents(self.required_margin_brl)
        self.repo_rate_annual = round_rate(self.repo_rate_annual)
        if self.repurchase_cash_brl == Decimal("0.00"):
            daily_factor = (Decimal("1.0") + self.repo_rate_annual) ** (
                Decimal(str(self.business_days)) / Decimal("252")
            )
            self.repurchase_cash_brl = round_cents(self.nominal_cash_brl * daily_factor)

    def total_eligible_collateral_brl(self) -> Decimal:
        """Soma do valor elegível de todos os itens de colateral alocados."""
        return sum((a.eligible_value_brl for a in self.allocations), Decimal("0.00"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "borrower_participant_id": self.borrower_participant_id,
            "lender_participant_id": self.lender_participant_id,
            "triparty_custodian": self.triparty_custodian.value,
            "nominal_cash_brl": str(self.nominal_cash_brl),
            "repo_rate_annual": str(self.repo_rate_annual),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "business_days": self.business_days,
            "required_margin_brl": str(self.required_margin_brl),
            "status": self.status.value,
            "allocations": [a.to_dict() for a in self.allocations],
            "repurchase_cash_brl": str(self.repurchase_cash_brl),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TripartyRepoContract:
        contract = cls(
            repo_id=data["repo_id"],
            borrower_participant_id=data["borrower_participant_id"],
            lender_participant_id=data["lender_participant_id"],
            triparty_custodian=InternationalCustodian(data["triparty_custodian"]),
            nominal_cash_brl=Decimal(data["nominal_cash_brl"]),
            repo_rate_annual=Decimal(data["repo_rate_annual"]),
            start_date=data["start_date"],
            end_date=data["end_date"],
            business_days=int(data["business_days"]),
            required_margin_brl=Decimal(data["required_margin_brl"]),
            status=RepoStatus(data.get("status", "INITIATED")),
            repurchase_cash_brl=Decimal(data.get("repurchase_cash_brl", "0.00")),
        )
        for a_data in data.get("allocations", []):
            contract.allocations.append(CollateralAllocation.from_dict(a_data))
        return contract


# ---------------------------------------------------------------------------
# Motor Central de Triparty Repo e Colateral Internacional
# ---------------------------------------------------------------------------

class TripartyInternationalCollateralEngine:
    """Motor institucional da B3 para Triparty Repo e Garantias Transfronteiriças (FL-099)."""

    def __init__(self) -> None:
        self.accounts: Dict[str, CustodyAccount] = {}
        self.ptax_table: Dict[str, PtaxRate] = {}
        self.contracts: Dict[str, TripartyRepoContract] = {}
        self.swift_messages: List[SwiftMessage] = []
        self.settlement_records: List[Dict[str, Any]] = []

    # -----------------------------------------------------------------------
    # Gestão de Câmbio PTAX e Matriz de Haircuts
    # -----------------------------------------------------------------------

    def set_ptax_rate(self, ptax: PtaxRate) -> None:
        """Registra ou atualiza cotação oficial PTAX BACEN."""
        self.ptax_table[ptax.currency.upper()] = ptax

    def get_ptax_rate(self, currency: str) -> PtaxRate:
        curr = currency.upper()
        if curr not in self.ptax_table:
            raise ValueError(f"Cotação PTAX não disponível para moeda '{curr}'")
        return self.ptax_table[curr]

    @staticmethod
    def calculate_base_haircut(rating: SovereignRating, tenor_years: Decimal) -> Decimal:
        """Calcula o haircut prudencial base por rating de crédito soberano e prazo residual."""
        if rating == SovereignRating.SUB_INVESTMENT:
            raise IneligibleCollateralError("Ativo sub-investment grade inelegível como garantia internacional")

        # Escadinha por tenor
        if tenor_years <= Decimal("1.0"):
            base = Decimal("0.020000")
        elif tenor_years <= Decimal("3.0"):
            base = Decimal("0.035000")
        elif tenor_years <= Decimal("5.0"):
            base = Decimal("0.050000")
        elif tenor_years <= Decimal("10.0"):
            base = Decimal("0.075000")
        else:
            base = Decimal("0.100000")

        # Spread por rating soberano
        rating_spreads = {
            SovereignRating.AAA: Decimal("0.000000"),
            SovereignRating.AA_PLUS: Decimal("0.010000"),
            SovereignRating.AA: Decimal("0.015000"),
            SovereignRating.AA_MINUS: Decimal("0.020000"),
            SovereignRating.A_PLUS: Decimal("0.030000"),
            SovereignRating.A: Decimal("0.035000"),
            SovereignRating.A_MINUS: Decimal("0.040000"),
            SovereignRating.BBB_PLUS: Decimal("0.060000"),
            SovereignRating.BBB: Decimal("0.070000"),
            SovereignRating.BBB_MINUS: Decimal("0.080000"),
        }
        spread = rating_spreads.get(rating, Decimal("0.100000"))
        return round_rate(base + spread)

    @staticmethod
    def get_fx_haircut_buffer(currency: str) -> Decimal:
        """Retorna o buffer de volatilidade cambial para absorção de risco de liquidação."""
        c = currency.upper()
        if c == "USD":
            return Decimal("0.080000")  # 8% para USD
        elif c == "EUR":
            return Decimal("0.100000")  # 10% para EUR
        elif c == "GBP":
            return Decimal("0.120000")  # 12% para GBP
        elif c == "BRL":
            return Decimal("0.000000")
        else:
            raise ValueError(f"Moeda internacional '{c}' sem buffer de volatilidade definido")

    def calculate_total_haircut(
        self,
        rating: SovereignRating,
        tenor_years: Decimal,
        currency: str,
    ) -> Tuple[Decimal, Decimal, Decimal]:
        """Calcula a composição de haircut (base, buffer cambial e total regulatório)."""
        base_h = self.calculate_base_haircut(rating, tenor_years)
        fx_buffer = self.get_fx_haircut_buffer(currency)
        total = min(base_h + fx_buffer, MAX_REGULATORY_HAIRCUT)
        return (base_h, fx_buffer, round_rate(total))

    # -----------------------------------------------------------------------
    # Contas de Custódia e Posições
    # -----------------------------------------------------------------------

    def register_account(self, account: CustodyAccount) -> None:
        """Cadastra conta de custódia internacional."""
        if account.account_id in self.accounts:
            raise ValueError(f"Conta de custódia {account.account_id} já cadastrada")
        self.accounts[account.account_id] = account

    def deposit_collateral(
        self,
        account_id: str,
        isin: str,
        symbol: str,
        currency: str,
        quantity: Decimal,
    ) -> None:
        """Deposita títulos internacionais na conta de custódia (aumenta disponível)."""
        if account_id not in self.accounts:
            raise ValueError(f"Conta {account_id} não encontrada")
        account = self.accounts[account_id]
        qty = round_quantity(quantity)
        if qty <= Decimal("0.0000"):
            raise ValueError("Quantidade depositada deve ser estritamente positiva")

        if isin in account.positions:
            pos = account.positions[isin]
            pos.total_quantity = round_quantity(pos.total_quantity + qty)
            pos.available_quantity = round_quantity(pos.available_quantity + qty)
        else:
            account.positions[isin] = CustodyPosition(
                isin=isin,
                symbol=symbol,
                currency=currency.upper(),
                total_quantity=qty,
                available_quantity=qty,
                locked_quantity=Decimal("0.0000"),
            )

    # -----------------------------------------------------------------------
    # Operações de Triparty Repo e Mensageria SWIFT
    # -----------------------------------------------------------------------

    def create_repo_contract(self, contract: TripartyRepoContract) -> None:
        """Cadastra nova operação compromissada internacional (Triparty Repo)."""
        if contract.repo_id in self.contracts:
            raise ValueError(f"Contrato {contract.repo_id} já existe")
        self.contracts[contract.repo_id] = contract

    def allocate_collateral(
        self,
        repo_id: str,
        account_id: str,
        isin: str,
        quantity: Decimal,
        unit_price_foreign: Decimal,
        rating: SovereignRating,
        tenor_years: Decimal,
    ) -> CollateralAllocation:
        """Aloca ativo custodiado e efetua bloqueio fiduciário com emissão de SWIFT MT540."""
        if repo_id not in self.contracts:
            raise ValueError(f"Contrato {repo_id} não encontrado")
        repo = self.contracts[repo_id]

        if account_id not in self.accounts:
            raise ValueError(f"Conta de custódia {account_id} não encontrada")
        account = self.accounts[account_id]

        if isin not in account.positions:
            raise ValueError(f"ISIN {isin} não possui posição na conta {account_id}")
        pos = account.positions[isin]

        qty = round_quantity(quantity)
        if qty <= Decimal("0.0000"):
            raise ValueError("Quantidade alocada deve ser estritamente positiva")
        if qty > pos.available_quantity:
            raise CustodyConservationError(
                f"Quantidade requerida ({qty}) excede saldo disponível ({pos.available_quantity}) na conta {account_id}"
            )

        # Cotação PTAX e Haircut
        ptax = self.get_ptax_rate(pos.currency)
        base_h, fx_buffer, total_h = self.calculate_total_haircut(rating, tenor_years, pos.currency)

        # Valoração conservadora
        price_dec = Decimal(str(unit_price_foreign))
        gross_foreign = qty * price_dec
        gross_brl = round_cents(gross_foreign * ptax.ptax_bid)
        eligible_brl = round_cents(gross_brl * (Decimal("1.000000") - total_h))

        # Efetua bloqueio fiduciário interno na posição da conta
        pos.available_quantity = round_quantity(pos.available_quantity - qty)
        pos.locked_quantity = round_quantity(pos.locked_quantity + qty)

        # Gera instrução SWIFT MT540 (Receive Free / Bloqueio Fiduciário B3)
        msg_seq = len(self.swift_messages) + 1
        swift_id = f"SWIFT-MT540-{repo_id}-{msg_seq:04d}"
        fiduciary_ref = f"B3-FIDUCIARY-LOCK-LAW13986-{repo_id}-{isin}"
        settle_date = repo.start_date.replace("-", "")

        raw_mt540 = (
            f":16R:GENL\n"
            f":20C::SEME//{swift_id}\n"
            f":23G:NEWM\n"
            f":98A::SETT//{settle_date}\n"
            f":16S:GENL\n"
            f":16R:CONFDET\n"
            f":35B:ISIN {isin}\n"
            f":36B::SETT//UNIT/{qty}\n"
            f":97A::SAFE//{account_id}\n"
            f":70D::SPCL//INCOMUNICABILIDADE PATRIMONIAL LEI 13986 GARANTIA B3\n"
            f":16S:CONFDET"
        )

        swift_msg = SwiftMessage(
            message_id=swift_id,
            message_type=SwiftMessageType.MT540,
            sender_bic=account.custodian.bic,
            receiver_bic="BVMFBRSPXXX",  # BIC da B3
            settlement_date=repo.start_date,
            isin=isin,
            quantity=qty,
            safekeeping_account=account_id,
            status=SwiftMessageStatus.SETTLED,
            fiduciary_lien_tag=fiduciary_ref,
            raw_mt_text=raw_mt540,
        )
        self.swift_messages.append(swift_msg)

        alloc = CollateralAllocation(
            allocation_id=f"ALLOC-{repo_id}-{isin}-{msg_seq:03d}",
            isin=isin,
            custodian=account.custodian,
            account_id=account_id,
            rating=rating,
            tenor_years=tenor_years,
            quantity=qty,
            unit_price_foreign=price_dec,
            currency=pos.currency,
            ptax_bid_rate=ptax.ptax_bid,
            base_haircut=base_h,
            fx_haircut_buffer=fx_buffer,
            total_haircut=total_h,
            gross_value_foreign=gross_foreign,
            gross_value_brl=gross_brl,
            eligible_value_brl=eligible_brl,
            fiduciary_lock_reference=fiduciary_ref,
            swift_message_id=swift_id,
        )
        repo.allocations.append(alloc)
        return alloc

    def lock_repo(self, repo_id: str) -> None:
        """Confirma o bloqueio fiduciário total da operação e valida cobertura mínima de margem."""
        if repo_id not in self.contracts:
            raise ValueError(f"Contrato {repo_id} não encontrado")
        repo = self.contracts[repo_id]
        total_eligible = repo.total_eligible_collateral_brl()

        if total_eligible < repo.required_margin_brl:
            raise InsufficientCollateralError(
                f"Colateral elegível ({total_eligible} BRL) insuficiente para margem requerida ({repo.required_margin_brl} BRL)"
            )

        repo.status = RepoStatus.LOCKED_FIDUCIARY

    def request_safe_release(
        self,
        repo_id: str,
        allocation_id: str,
        quantity_to_release: Optional[Decimal] = None,
    ) -> SwiftMessage:
        """Executa a desoneração de garantia sob a trava estrita do Safe-Release Guardrail.

        Se o contrato não estiver liquidado, a liberação só é permitida se o colateral
        remanescente continuar cobrindo 100% da margem requerida da B3.
        Emite instrução SWIFT MT542 (Deliver Free).
        """
        if repo_id not in self.contracts:
            raise ValueError(f"Contrato {repo_id} não encontrado")
        repo = self.contracts[repo_id]

        target_alloc: Optional[CollateralAllocation] = None
        for a in repo.allocations:
            if a.allocation_id == allocation_id:
                target_alloc = a
                break

        if not target_alloc:
            raise ValueError(f"Alocação {allocation_id} não encontrada no contrato {repo_id}")

        qty_rel = round_quantity(quantity_to_release) if quantity_to_release is not None else target_alloc.quantity
        if qty_rel <= Decimal("0.0000") or qty_rel > target_alloc.quantity:
            raise ValueError(f"Quantidade a liberar ({qty_rel}) inválida para alocação de {target_alloc.quantity}")

        # Avaliação de impacto na margem se a operação ainda estiver ativa
        if repo.status in (RepoStatus.LOCKED_FIDUCIARY, RepoStatus.COLLATERAL_ALLOCATED):
            # Proporção liberada
            ratio_released = qty_rel / target_alloc.quantity
            released_eligible_brl = round_cents(target_alloc.eligible_value_brl * ratio_released)
            remaining_eligible_brl = repo.total_eligible_collateral_brl() - released_eligible_brl

            # Safe-Release Guardrail
            if remaining_eligible_brl < repo.required_margin_brl:
                raise MarginReleaseViolationError(
                    f"Safe-Release Guardrail: Liberação rejeitada! Colateral remanescente ({remaining_eligible_brl} BRL) "
                    f"seria inferior à margem requerida ({repo.required_margin_brl} BRL)"
                )

        # Atualiza a alocação e desbloqueia custódia na conta
        account = self.accounts[target_alloc.account_id]
        pos = account.positions[target_alloc.isin]

        pos.locked_quantity = round_quantity(pos.locked_quantity - qty_rel)
        pos.available_quantity = round_quantity(pos.available_quantity + qty_rel)

        # Ajusta ou remove alocação
        if qty_rel == target_alloc.quantity:
            repo.allocations.remove(target_alloc)
        else:
            ratio_rem = (target_alloc.quantity - qty_rel) / target_alloc.quantity
            target_alloc.quantity = round_quantity(target_alloc.quantity - qty_rel)
            target_alloc.gross_value_foreign = target_alloc.quantity * target_alloc.unit_price_foreign
            target_alloc.gross_value_brl = round_cents(target_alloc.gross_value_foreign * target_alloc.ptax_bid_rate)
            target_alloc.eligible_value_brl = round_cents(
                target_alloc.gross_value_brl * (Decimal("1.000000") - target_alloc.total_haircut)
            )

        # Emite SWIFT MT542 (Deliver Free / Desoneração Fiduciária)
        msg_seq = len(self.swift_messages) + 1
        swift_id = f"SWIFT-MT542-{repo_id}-{msg_seq:04d}"
        settle_date = datetime.now(timezone.utc).strftime("%Y%m%d")

        raw_mt542 = (
            f":16R:GENL\n"
            f":20C::SEME//{swift_id}\n"
            f":23G:NEWM\n"
            f":98A::SETT//{settle_date}\n"
            f":16S:GENL\n"
            f":16R:CONFDET\n"
            f":35B:ISIN {target_alloc.isin}\n"
            f":36B::SETT//UNIT/{qty_rel}\n"
            f":97A::SAFE//{target_alloc.account_id}\n"
            f":70D::SPCL//DESONERACAO FIDUCIARIA SAFE-RELEASE B3\n"
            f":16S:CONFDET"
        )

        swift_msg = SwiftMessage(
            message_id=swift_id,
            message_type=SwiftMessageType.MT542,
            sender_bic="BVMFBRSPXXX",
            receiver_bic=account.custodian.bic,
            settlement_date=datetime.now(timezone.utc).date().isoformat(),
            isin=target_alloc.isin,
            quantity=qty_rel,
            safekeeping_account=target_alloc.account_id,
            status=SwiftMessageStatus.SETTLED,
            fiduciary_lien_tag=f"SAFE-RELEASE-{repo_id}",
            raw_mt_text=raw_mt542,
        )
        self.swift_messages.append(swift_msg)
        return swift_msg

    # -----------------------------------------------------------------------
    # Liquidação Soma-Zero e Excussão em Caso de Default
    # -----------------------------------------------------------------------

    def settle_repo(self, repo_id: str) -> Dict[str, Any]:
        """Executa a liquidação financeira regular DvP com recompra soma-zero e devolução de colateral."""
        if repo_id not in self.contracts:
            raise ValueError(f"Contrato {repo_id} não encontrado")
        repo = self.contracts[repo_id]

        if repo.status != RepoStatus.LOCKED_FIDUCIARY:
            raise ValueError(f"Contrato {repo_id} deve estar em status LOCKED_FIDUCIARY para liquidação")

        # Libera todas as garantias fiduciárias bloqueadas
        released_qty_map: Dict[str, str] = {}
        for alloc in list(repo.allocations):
            account = self.accounts[alloc.account_id]
            pos = account.positions[alloc.isin]
            pos.locked_quantity = round_quantity(pos.locked_quantity - alloc.quantity)
            pos.available_quantity = round_quantity(pos.available_quantity + alloc.quantity)
            released_qty_map[alloc.isin] = str(alloc.quantity)

            # Emite SWIFT MT542 para cada ativo desonerado
            msg_seq = len(self.swift_messages) + 1
            swift_id = f"SWIFT-MT542-{repo_id}-{msg_seq:04d}"
            settle_date = repo.end_date.replace("-", "")
            raw_mt = (
                f":16R:GENL\n"
                f":20C::SEME//{swift_id}\n"
                f":23G:NEWM\n"
                f":98A::SETT//{settle_date}\n"
                f":16S:GENL\n"
                f":16R:CONFDET\n"
                f":35B:ISIN {alloc.isin}\n"
                f":36B::SETT//UNIT/{alloc.quantity}\n"
                f":97A::SAFE//{alloc.account_id}\n"
                f":70D::SPCL//LIQUIDACAO FINAL DVP DEVOLUCAO COLATERAL B3\n"
                f":16S:CONFDET"
            )
            self.swift_messages.append(
                SwiftMessage(
                    message_id=swift_id,
                    message_type=SwiftMessageType.MT542,
                    sender_bic="BVMFBRSPXXX",
                    receiver_bic=account.custodian.bic,
                    settlement_date=repo.end_date,
                    isin=alloc.isin,
                    quantity=alloc.quantity,
                    safekeeping_account=alloc.account_id,
                    status=SwiftMessageStatus.SETTLED,
                    fiduciary_lien_tag=f"FINAL-SETTLEMENT-{repo_id}",
                    raw_mt_text=raw_mt,
                )
            )

        repo.allocations.clear()
        repo.status = RepoStatus.SETTLED

        # Registro contábil soma-zero: tomador paga Recompra, doador de caixa recebe Recompra
        repurchase_val = repo.repurchase_cash_brl
        record = {
            "record_id": f"SETTLE-DVP-{repo_id}",
            "repo_id": repo_id,
            "settlement_type": "REGULAR_DvP",
            "borrower_paid_brl": str(repurchase_val),
            "lender_received_brl": str(repurchase_val),
            "surplus_returned_to_borrower_brl": "0.00",
            "collateral_released_qty": released_qty_map,
            "net_financial_delta_brl": "0.00",
            "settled_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        self.settlement_records.append(record)
        return record

    def execute_default(
        self,
        repo_id: str,
        auction_proceeds_foreign_dict: Dict[str, Decimal],
    ) -> Dict[str, Any]:
        """Excussão fiduciária internacional de colateral em caso de inadimplência (Default).

        Liquida os títulos nos mercados internacionais, converte pela PTAX Bid do dia,
        satisfaz a dívida de recompra e devolve estritamente qualquer saldo excedente
        ao tomador, preservando conservação soma-zero centavo a centavo.
        """
        if repo_id not in self.contracts:
            raise ValueError(f"Contrato {repo_id} não encontrado")
        repo = self.contracts[repo_id]

        if repo.status != RepoStatus.LOCKED_FIDUCIARY:
            raise ValueError(f"Contrato {repo_id} não está em LOCKED_FIDUCIARY para excussão")

        total_proceeds_brl = Decimal("0.00")
        released_qty_map: Dict[str, str] = {}

        for alloc in list(repo.allocations):
            account = self.accounts[alloc.account_id]
            pos = account.positions[alloc.isin]

            # Baixa de custódia física executada em leilão
            pos.locked_quantity = round_quantity(pos.locked_quantity - alloc.quantity)
            pos.total_quantity = round_quantity(pos.total_quantity - alloc.quantity)
            released_qty_map[alloc.isin] = str(alloc.quantity)

            # Preço obtido no leilão internacional
            foreign_proceeds = auction_proceeds_foreign_dict.get(
                alloc.isin, alloc.quantity * alloc.unit_price_foreign
            )
            ptax = self.get_ptax_rate(alloc.currency)
            brl_val = round_cents(foreign_proceeds * ptax.ptax_bid)
            total_proceeds_brl += brl_val

        debt_brl = repo.repurchase_cash_brl
        lender_received = min(total_proceeds_brl, debt_brl)
        surplus_returned = max(Decimal("0.00"), total_proceeds_brl - debt_brl)

        repo.allocations.clear()
        repo.status = RepoStatus.DEFAULT_EXECUTED

        record = {
            "record_id": f"DEFAULT-AUCTION-{repo_id}",
            "repo_id": repo_id,
            "settlement_type": "DEFAULT_AUCTION",
            "borrower_paid_brl": str(total_proceeds_brl),
            "lender_received_brl": str(lender_received),
            "surplus_returned_to_borrower_brl": str(surplus_returned),
            "collateral_released_qty": released_qty_map,
            "net_financial_delta_brl": "0.00",
            "settled_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        self.settlement_records.append(record)
        return record

    # -----------------------------------------------------------------------
    # Verificação Formal de Invariantes Formais (LAW-105A, 105B, 105C)
    # -----------------------------------------------------------------------

    def verify_invariants(self) -> Dict[str, bool]:
        """Auditoria formal das leis de integridade, bloqueio e conservação bilateral.

        - LAW-105A: Cobertura cambial e haircut prudencial.
        - LAW-105B: Inviolabilidade do bloqueio internacional e segregação de contas.
        - LAW-105C: Conservação bilateral soma-zero na liquidação.
        """
        # LAW-105A: Cobertura cambial e haircut prudencial
        law_105a_holds = True
        for contract in self.contracts.values():
            for a in contract.allocations:
                # 0 <= H_total < 1.00
                if not (Decimal("0.000000") <= a.total_haircut < Decimal("1.000000")):
                    law_105a_holds = False
                    break
                # V_elegivel <= V_bruto
                if a.eligible_value_brl > a.gross_value_brl:
                    law_105a_holds = False
                    break
                # PTAX positiva e bid <= ask
                ptax = self.ptax_table.get(a.currency)
                if not ptax or ptax.ptax_bid <= Decimal("0.000000") or ptax.ptax_bid > ptax.ptax_ask:
                    law_105a_holds = False
                    break

            if not law_105a_holds:
                break

            # Se a operação estiver ativa com bloqueio, a garantia deve cobrir a margem
            if contract.status == RepoStatus.LOCKED_FIDUCIARY:
                if contract.total_eligible_collateral_brl() < contract.required_margin_brl:
                    law_105a_holds = False
                    break

        # LAW-105B: Inviolabilidade do bloqueio internacional e segregação
        law_105b_holds = True
        house_participants: Set[str] = set()
        client_participants: Set[str] = set()

        for acc in self.accounts.values():
            if acc.category == AccountCategory.HOUSE:
                house_participants.add(f"{acc.custodian.value}:{acc.participant_id}:HOUSE")
            else:
                client_participants.add(f"{acc.custodian.value}:{acc.participant_id}:CLIENT")

            for pos in acc.positions.values():
                # Conservação de custódia Q_total == Q_livre + Q_bloqueada
                if pos.total_quantity != (pos.available_quantity + pos.locked_quantity):
                    law_105b_holds = False
                    break
                if pos.total_quantity < Decimal("0.0000") or pos.available_quantity < Decimal("0.0000") or pos.locked_quantity < Decimal("0.0000"):
                    law_105b_holds = False
                    break
            if not law_105b_holds:
                break

        # Segregação estrita: contas House e Client não podem ser promiscuamente unificadas
        for hp in house_participants:
            base_key = hp.replace(":HOUSE", "")
            if f"{base_key}:CLIENT" in client_participants:
                # Cada uma deve ser uma conta distinta
                house_accs = [a for a in self.accounts.values() if a.category == AccountCategory.HOUSE and a.participant_id == base_key.split(":")[1]]
                client_accs = [a for a in self.accounts.values() if a.category == AccountCategory.CLIENT and a.participant_id == base_key.split(":")[1]]
                if any(h.account_id == c.account_id for h in house_accs for c in client_accs):
                    law_105b_holds = False
                    break

        # Mensagens SWIFT de bloqueio MT540 devem conter tag fiduciária
        for msg in self.swift_messages:
            if msg.message_type == SwiftMessageType.MT540:
                if "INCOMUNICABILIDADE PATRIMONIAL" not in msg.raw_mt_text and "FIDUCIARY" not in msg.fiduciary_lien_tag:
                    law_105b_holds = False
                    break

        # LAW-105C: Conservação bilateral soma-zero na liquidação
        law_105c_holds = True
        for rec in self.settlement_records:
            b_paid = Decimal(rec["borrower_paid_brl"])
            l_recv = Decimal(rec["lender_received_brl"])
            surplus = Decimal(rec.get("surplus_returned_to_borrower_brl", "0.00"))
            delta = Decimal(rec.get("net_financial_delta_brl", "0.00"))

            # Conservação exata de fluxos financeiros
            if b_paid != (l_recv + surplus):
                law_105c_holds = False
                break
            if delta != Decimal("0.00"):
                law_105c_holds = False
                break

        all_hold = law_105a_holds and law_105b_holds and law_105c_holds
        return {
            "all_invariants_hold": all_hold,
            "law_105a_collateral_haircut_coverage_verified": law_105a_holds,
            "law_105b_international_lock_inviolability_verified": law_105b_holds,
            "law_105c_settlement_zero_sum_verified": law_105c_holds,
        }

    # -----------------------------------------------------------------------
    # Serialização JSON (to_dict / from_dict)
    # -----------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accounts": [acc.to_dict() for acc in self.accounts.values()],
            "ptax_table": [ptax.to_dict() for ptax in self.ptax_table.values()],
            "contracts": [c.to_dict() for c in self.contracts.values()],
            "swift_messages": [m.to_dict() for m in self.swift_messages],
            "settlement_records": self.settlement_records,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TripartyInternationalCollateralEngine:
        engine = cls()
        for p_data in data.get("ptax_table", []):
            ptax = PtaxRate.from_dict(p_data)
            engine.set_ptax_rate(ptax)

        for acc_data in data.get("accounts", []):
            acc = CustodyAccount.from_dict(acc_data)
            engine.register_account(acc)

        for c_data in data.get("contracts", []):
            c = TripartyRepoContract.from_dict(c_data)
            engine.create_repo_contract(c)

        for m_data in data.get("swift_messages", []):
            engine.swift_messages.append(SwiftMessage.from_dict(m_data))

        engine.settlement_records = list(data.get("settlement_records", []))
        return engine
