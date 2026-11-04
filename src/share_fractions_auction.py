"""Central Depositária B3 — Tratamento de Frações de Ações Pós-Grupamento e Leilão Especial de Sobras (FL-052).

Implementa o ciclo de vida completo do tratamento de sobras fracionárias decorrentes
de eventos societários de Grupamento (Reverse Stock Split) e Incorporação de Ações, em estrita
conformidade com os Arts. 12 e 175 da Lei nº 6.404/1976 (Lei das S/A) e o Manual de
Procedimentos Operacionais da Central Depositária e do PUMA Trading System da B3:

1. Apuração de Frações por Conta de Custódia:
   - Cálculo determinístico do quociente inteiro de ações (creditadas imediatamente na conta).
   - Segregação da fração residual estritamente inferior a 1 ação para o Fractions Pool.
2. Agrupamento Centralizado de Frações (Fractions Pool):
   - Consolidação de todas as sobras fracionárias dos acionistas em lotes inteiros de negociação.
3. Leilão Especial de Sobras no PUMA Trading System:
   - Registro de fechamento de leilão, apuração de preço unitário bruto, taxas e emolumentos B3
     (negociação e liquidação), taxa de corretagem e apuração do montante líquido final.
4. Repasse Financeiro Pro-Rata com Conservação Absoluta de Centavos (Hare-Niemeyer):
   - Rateio proporcional exato entre os titulares das frações (`Crédito = Fração * Preço_Líquido_Médio`).
   - Alocação determinística de centavos residuais pelo método dos maiores resíduos,
     garantindo fechamento contábil perfeito sem centavos órfãos ou fictícios:
     `Soma(Crédito_Bruto) == Gross`, `Soma(Taxas) == Fees`, `Soma(Crédito_Líquido) == Net`.

100% biblioteca padrão Python (`dataclasses`, `Decimal`, `datetime`, `enum`, `json`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
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


def _quantize_fraction(val: Union[Decimal, str, int, float]) -> Decimal:
    """Quantiza fração de ação para 8 casas decimais."""
    return Decimal(str(val)).quantize(EIGHT_PLACES, rounding=ROUND_HALF_UP)


def _to_date(val: Any, field_name: str) -> date:
    """Converte string ISO ou objeto date em date válido."""
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val.strip()[:10])
    raise ValueError(f"Campo '{field_name}' deve ser uma data válida no formato YYYY-MM-DD.")


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class ShareFractionsError(Exception):
    """Exceção base do motor de frações de ações e leilão de sobras."""


class GroupingValidationError(ShareFractionsError):
    """Lançada quando parâmetros do evento societário de grupamento são inválidos."""


class CustodyAccountError(ShareFractionsError):
    """Lançada quando dados da conta de custódia ou posição acionária são inválidos."""


class AuctionValidationError(ShareFractionsError):
    """Lançada quando dados do leilão no PUMA ou parâmetros financeiros são inconsistentes."""


class FinancialConservationError(ShareFractionsError):
    """Lançada quando as invariantes de conservação financeira ou física são violadas."""


# ---------------------------------------------------------------------------
# Enumerações
# ---------------------------------------------------------------------------

class GroupingEventType(str, Enum):
    """Tipo do evento societário gerador de sobras fracionárias."""
    REVERSE_SPLIT = "REVERSE_SPLIT"  # Grupamento de Ações (Art. 12 Lei 6.404/76)
    INCORPORATION = "INCORPORATION"  # Incorporação de Ações com relação de troca não inteira
    STOCK_SPLIT = "STOCK_SPLIT"      # Desdobramento de Ações (informativo)


# ---------------------------------------------------------------------------
# Entidades de Dados
# ---------------------------------------------------------------------------

@dataclass
class ShareFractionHolder:
    """Representa a posição de custódia de um acionista individual e seu respectivo rateio."""
    investor_account: str
    participant_code: str
    investor_name: str
    document_id: str
    original_shares: int
    whole_shares: int = 0
    fraction_share: Decimal = Decimal("0")
    fraction_weight: Decimal = Decimal("0")
    cash_credit_gross: Decimal = Decimal("0.00")
    fee_deduction: Decimal = Decimal("0.00")
    cash_credit_net: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        if self.original_shares < 0:
            raise CustodyAccountError(
                f"Posição acionária original não pode ser negativa: {self.original_shares}"
            )
        self.fraction_share = _quantize_fraction(self.fraction_share)
        self.fraction_weight = _quantize_fraction(self.fraction_weight)
        self.cash_credit_gross = _quantize_money(self.cash_credit_gross)
        self.fee_deduction = _quantize_money(self.fee_deduction)
        self.cash_credit_net = _quantize_money(self.cash_credit_net)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o titular e seus resultados para dicionário."""
        return {
            "investor_account": self.investor_account,
            "participant_code": self.participant_code,
            "investor_name": self.investor_name,
            "document_id": self.document_id,
            "original_shares": self.original_shares,
            "whole_shares": self.whole_shares,
            "fraction_share": str(self.fraction_share.normalize()),
            "fraction_weight": str(self.fraction_weight),
            "cash_credit_gross": str(self.cash_credit_gross),
            "fee_deduction": str(self.fee_deduction),
            "cash_credit_net": str(self.cash_credit_net),
        }


@dataclass
class GroupingEvent:
    """Especificação do evento societário de Grupamento de Ações ou Incorporação."""
    event_id: str
    ticker: str
    isin: str
    company_name: str
    ratio_from: int
    ratio_to: int
    event_type: GroupingEventType = GroupingEventType.REVERSE_SPLIT
    approval_date: date = field(default_factory=date.today)
    cutoff_date: date = field(default_factory=date.today)
    adjustment_period_days: int = 30
    trading_ex_date: Optional[date] = None
    auction_date: Optional[date] = None
    settlement_date: Optional[date] = None

    def __post_init__(self) -> None:
        if self.ratio_from <= 0:
            raise GroupingValidationError(f"Fator de origem (ratio_from) deve ser > 0: {self.ratio_from}")
        if self.ratio_to <= 0:
            raise GroupingValidationError(f"Fator de destino (ratio_to) deve ser > 0: {self.ratio_to}")
        self.approval_date = _to_date(self.approval_date, "approval_date")
        self.cutoff_date = _to_date(self.cutoff_date, "cutoff_date")
        if self.trading_ex_date is not None:
            self.trading_ex_date = _to_date(self.trading_ex_date, "trading_ex_date")
        if self.auction_date is not None:
            self.auction_date = _to_date(self.auction_date, "auction_date")
        if self.settlement_date is not None:
            self.settlement_date = _to_date(self.settlement_date, "settlement_date")

    @property
    def grouping_factor(self) -> Decimal:
        """Fator multiplicativo do evento (ex: 1/10 = 0.1 para 10:1)."""
        return Decimal(self.ratio_to) / Decimal(self.ratio_from)

    @property
    def ratio_str(self) -> str:
        """Formatação textual da relação de troca (ex: '10:1')."""
        return f"{self.ratio_from}:{self.ratio_to}"

    def calculate_holder_split(self, original_shares: int) -> Tuple[int, Decimal]:
        """Calcula o quociente inteiro e a fração residual para uma dada quantidade original."""
        if original_shares < 0:
            raise CustodyAccountError(f"Saldo original não pode ser negativo: {original_shares}")
        whole = (original_shares * self.ratio_to) // self.ratio_from
        remainder_num = (original_shares * self.ratio_to) % self.ratio_from
        fraction = _quantize_fraction(Decimal(remainder_num) / Decimal(self.ratio_from))
        return whole, fraction

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o evento societário para dicionário."""
        return {
            "event_id": self.event_id,
            "ticker": self.ticker,
            "isin": self.isin,
            "company_name": self.company_name,
            "ratio_from": self.ratio_from,
            "ratio_to": self.ratio_to,
            "ratio_str": self.ratio_str,
            "event_type": self.event_type.value,
            "approval_date": self.approval_date.isoformat(),
            "cutoff_date": self.cutoff_date.isoformat(),
            "adjustment_period_days": self.adjustment_period_days,
            "trading_ex_date": self.trading_ex_date.isoformat() if self.trading_ex_date else None,
            "auction_date": self.auction_date.isoformat() if self.auction_date else None,
            "settlement_date": self.settlement_date.isoformat() if self.settlement_date else None,
        }


@dataclass
class FractionsPoolSummary:
    """Consolidação das sobras fracionárias apuradas pela Central Depositária B3."""
    event_id: str
    ticker: str
    total_holders: int
    holders_with_fractions: int
    total_original_shares: int
    total_whole_shares_allocated: int
    total_fractional_shares: Decimal
    auction_whole_shares: int
    residual_unauctioned_fraction: Decimal

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o sumário do pool para dicionário."""
        return {
            "event_id": self.event_id,
            "ticker": self.ticker,
            "total_holders": self.total_holders,
            "holders_with_fractions": self.holders_with_fractions,
            "total_original_shares": self.total_original_shares,
            "total_whole_shares_allocated": self.total_whole_shares_allocated,
            "total_fractional_shares": str(self.total_fractional_shares.normalize()),
            "auction_whole_shares": self.auction_whole_shares,
            "residual_unauctioned_fraction": str(self.residual_unauctioned_fraction.normalize()),
        }


@dataclass
class FractionsAuctionOutcome:
    """Resultado da alienação dos lotes inteiros de frações no PUMA Trading System."""
    auction_id: str
    event_id: str
    ticker: str
    shares_auctioned: int
    gross_price_per_share: Decimal
    gross_total_amount: Decimal
    trading_fees: Decimal = Decimal("0.00")
    clearing_fees: Decimal = Decimal("0.00")
    brokerage_fees: Decimal = Decimal("0.00")
    other_fees: Decimal = Decimal("0.00")
    total_fees: Decimal = Decimal("0.00")
    net_total_amount: Decimal = Decimal("0.00")
    net_price_per_share: Decimal = Decimal("0.00")
    auction_date: Optional[date] = None
    settlement_date: Optional[date] = None

    def __post_init__(self) -> None:
        if self.shares_auctioned <= 0:
            raise AuctionValidationError(f"Quantidade de ações leiloadas deve ser > 0: {self.shares_auctioned}")
        self.gross_price_per_share = _quantize_money(self.gross_price_per_share)
        self.gross_total_amount = _quantize_money(self.gross_total_amount)
        self.trading_fees = _quantize_money(self.trading_fees)
        self.clearing_fees = _quantize_money(self.clearing_fees)
        self.brokerage_fees = _quantize_money(self.brokerage_fees)
        self.other_fees = _quantize_money(self.other_fees)

        calculated_fees = self.trading_fees + self.clearing_fees + self.brokerage_fees + self.other_fees
        if self.total_fees == Decimal("0.00") and calculated_fees > Decimal("0.00"):
            self.total_fees = calculated_fees
        else:
            self.total_fees = _quantize_money(self.total_fees)

        if self.net_total_amount == Decimal("0.00"):
            self.net_total_amount = self.gross_total_amount - self.total_fees
        else:
            self.net_total_amount = _quantize_money(self.net_total_amount)

        if self.net_total_amount < Decimal("0.00"):
            raise AuctionValidationError(
                f"Montante líquido do leilão não pode ser negativo: {self.net_total_amount}"
            )

        if self.net_price_per_share == Decimal("0.00"):
            self.net_price_per_share = (self.net_total_amount / Decimal(self.shares_auctioned)).quantize(
                EIGHT_PLACES, rounding=ROUND_HALF_UP
            )

        if self.auction_date is not None:
            self.auction_date = _to_date(self.auction_date, "auction_date")
        if self.settlement_date is not None:
            self.settlement_date = _to_date(self.settlement_date, "settlement_date")

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o resultado do leilão para dicionário."""
        return {
            "auction_id": self.auction_id,
            "event_id": self.event_id,
            "ticker": self.ticker,
            "shares_auctioned": self.shares_auctioned,
            "gross_price_per_share": str(self.gross_price_per_share),
            "gross_total_amount": str(self.gross_total_amount),
            "trading_fees": str(self.trading_fees),
            "clearing_fees": str(self.clearing_fees),
            "brokerage_fees": str(self.brokerage_fees),
            "other_fees": str(self.other_fees),
            "total_fees": str(self.total_fees),
            "net_total_amount": str(self.net_total_amount),
            "net_price_per_share": str(self.net_price_per_share),
            "auction_date": self.auction_date.isoformat() if self.auction_date else None,
            "settlement_date": self.settlement_date.isoformat() if self.settlement_date else None,
        }


# ---------------------------------------------------------------------------
# Algoritmo Determinístico de Rateio Centavo a Centavo (Largest Remainder)
# ---------------------------------------------------------------------------

def _distribute_cents_largest_remainder(
    total_amount: Decimal,
    items: Sequence[Tuple[str, Decimal, Decimal]],  # (holder_id, weight, fraction_share)
) -> Dict[str, Decimal]:
    """Distribui montante financeiro em centavos com conservação exata da soma (Método Hare-Niemeyer).

    Args:
        total_amount: Montante total a ser distribuído (em R$).
        items: Lista de tuplas (identificador, peso proporcional no pool, fração residual).

    Returns:
        Dicionário mapeando identificador para o valor em Decimal monetário exato (R$ 0.01).
    """
    total_cents = int((total_amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if total_cents == 0 or not items:
        return {item_id: Decimal("0.00") for item_id, _, _ in items}

    # 1. Calcular cota teórica em centavos e cota base inteira
    calculated: List[Dict[str, Any]] = []
    sum_base_cents = 0

    for item_id, weight, fraction in items:
        exact_cents = Decimal(total_cents) * weight
        base_cents = int(exact_cents.quantize(Decimal("1"), rounding=ROUND_FLOOR))
        rem = exact_cents - Decimal(base_cents)
        sum_base_cents += base_cents
        calculated.append({
            "id": item_id,
            "weight": weight,
            "fraction": fraction,
            "base_cents": base_cents,
            "remainder": rem,
        })

    # 2. Apurar centavos residuais decorrentes do truncamento
    deficit_cents = total_cents - sum_base_cents
    if deficit_cents < 0:
        raise FinancialConservationError(f"Erro inesperado no truncamento de centavos: déficit {deficit_cents}")

    # 3. Ordenar beneficiários pelo maior resíduo com desempate determinístico:
    #    (maior resíduo DESC, maior fração original DESC, ID ASC)
    calculated.sort(key=lambda x: (-x["remainder"], -x["fraction"], x["id"]))

    # 4. Atribuir +1 centavo para os primeiros deficit_cents acionistas
    for i in range(deficit_cents):
        calculated[i]["base_cents"] += 1

    # 5. Montar resultado quantizado em moeda
    result: Dict[str, Decimal] = {}
    for entry in calculated:
        cents = entry["base_cents"]
        result[entry["id"]] = (Decimal(cents) / Decimal("100")).quantize(TWO_PLACES)

    return result


# ---------------------------------------------------------------------------
# Motor Central de Processamento
# ---------------------------------------------------------------------------

class ShareFractionsAuctionEngine:
    """Motor de processamento da Central Depositária B3 para apuração de frações e leilão de sobras."""

    def __init__(self) -> None:
        self.events: Dict[str, GroupingEvent] = {}
        self.holders: Dict[str, List[ShareFractionHolder]] = {}
        self.pools: Dict[str, FractionsPoolSummary] = {}
        self.auctions: Dict[str, FractionsAuctionOutcome] = {}

    def register_event(self, event: GroupingEvent) -> None:
        """Registra especificação de evento societário de grupamento."""
        if event.event_id in self.events:
            raise GroupingValidationError(f"Evento com ID '{event.event_id}' já está registrado.")
        self.events[event.event_id] = event

    def process_custody_accounts(
        self,
        event_id: str,
        holders: Sequence[ShareFractionHolder],
    ) -> FractionsPoolSummary:
        """Processa as contas de custódia na data de corte e aglutina as sobras no Fractions Pool."""
        if event_id not in self.events:
            raise GroupingValidationError(f"Evento societário '{event_id}' não encontrado.")
        if not holders:
            raise CustodyAccountError("Lista de contas de custódia não pode estar vazia.")

        event = self.events[event_id]
        processed_holders: List[ShareFractionHolder] = []

        total_original = 0
        total_whole = 0
        total_fractions = Decimal("0")
        holders_with_fractions = 0

        for h in holders:
            whole, frac = event.calculate_holder_split(h.original_shares)
            h.whole_shares = whole
            h.fraction_share = frac
            total_original += h.original_shares
            total_whole += whole
            total_fractions += frac
            if frac > Decimal("0"):
                holders_with_fractions += 1
            processed_holders.append(h)

        total_fractions = _quantize_fraction(total_fractions)
        auction_whole = int(total_fractions.quantize(Decimal("1"), rounding=ROUND_FLOOR))
        residual_unauctioned = _quantize_fraction(total_fractions - Decimal(auction_whole))

        # Calcular o peso de cada fração no pool total
        if total_fractions > Decimal("0"):
            for h in processed_holders:
                if h.fraction_share > Decimal("0"):
                    h.fraction_weight = _quantize_fraction(h.fraction_share / total_fractions)
                else:
                    h.fraction_weight = Decimal("0")

        self.holders[event_id] = processed_holders

        summary = FractionsPoolSummary(
            event_id=event_id,
            ticker=event.ticker,
            total_holders=len(processed_holders),
            holders_with_fractions=holders_with_fractions,
            total_original_shares=total_original,
            total_whole_shares_allocated=total_whole,
            total_fractional_shares=total_fractions,
            auction_whole_shares=auction_whole,
            residual_unauctioned_fraction=residual_unauctioned,
        )
        self.pools[event_id] = summary
        return summary

    def register_auction_outcome(self, outcome: FractionsAuctionOutcome) -> None:
        """Registra o resultado do leilão especial de sobras no PUMA Trading System."""
        if outcome.event_id not in self.events:
            raise AuctionValidationError(f"Evento societário '{outcome.event_id}' não encontrado para o leilão.")
        if outcome.event_id not in self.pools:
            raise AuctionValidationError(f"Pool de frações do evento '{outcome.event_id}' ainda não foi apurado.")

        pool = self.pools[outcome.event_id]
        if outcome.shares_auctioned != pool.auction_whole_shares:
            raise AuctionValidationError(
                f"Quantidade leiloada ({outcome.shares_auctioned}) diverge dos lotes inteiros apurados no pool ({pool.auction_whole_shares})."
            )

        # Validar consistência do valor bruto: preço unitário * ações leiloadas
        expected_gross = _quantize_money(outcome.gross_price_per_share * Decimal(outcome.shares_auctioned))
        if outcome.gross_total_amount != expected_gross:
            raise AuctionValidationError(
                f"Valor bruto ({outcome.gross_total_amount}) diverge de Preço * Ações ({expected_gross})."
            )

        # Validar líquido = bruto - taxas
        expected_net = outcome.gross_total_amount - outcome.total_fees
        if outcome.net_total_amount != expected_net:
            raise AuctionValidationError(
                f"Valor líquido informado ({outcome.net_total_amount}) diverge de Bruto - Taxas ({expected_net})."
            )

        self.auctions[outcome.event_id] = outcome

    def calculate_pro_rata_distribution(self, event_id: str) -> List[ShareFractionHolder]:
        """Executa o rateio financeiro pro-rata entre os acionistas titulares de frações com conservação exata."""
        if event_id not in self.holders:
            raise FinancialConservationError(f"Contas de custódia do evento '{event_id}' não foram processadas.")

        pool = self.pools[event_id]
        holders = self.holders[event_id]

        if pool.total_fractional_shares == Decimal("0"):
            # Nenhuma fração gerada, créditos zerados (sem leilão necessário)
            for h in holders:
                h.cash_credit_gross = Decimal("0.00")
                h.fee_deduction = Decimal("0.00")
                h.cash_credit_net = Decimal("0.00")
            return holders

        if event_id not in self.auctions:
            raise FinancialConservationError(f"Leilão de frações do evento '{event_id}' não foi registrado.")

        auction = self.auctions[event_id]

        # Preparar tuplas para o rateio determinístico
        # (account_id, weight, fraction_share)
        items: List[Tuple[str, Decimal, Decimal]] = [
            (h.investor_account, h.fraction_weight, h.fraction_share)
            for h in holders
            if h.fraction_share > Decimal("0")
        ]

        # 1. Distribuir o montante bruto conservando a soma exata
        gross_by_account = _distribute_cents_largest_remainder(auction.gross_total_amount, items)

        # 2. Distribuir as taxas/emolumentos conservando a soma exata
        fees_by_account = _distribute_cents_largest_remainder(auction.total_fees, items)

        # 3. Calcular o crédito líquido individual mantendo a coerência contábil de linha:
        #    Net_i = Gross_i - Fees_i
        for h in holders:
            if h.fraction_share > Decimal("0"):
                acc = h.investor_account
                gross_i = gross_by_account.get(acc, Decimal("0.00"))
                fee_i = fees_by_account.get(acc, Decimal("0.00"))
                net_i = gross_i - fee_i
                h.cash_credit_gross = gross_i
                h.fee_deduction = fee_i
                h.cash_credit_net = net_i
            else:
                h.cash_credit_gross = Decimal("0.00")
                h.fee_deduction = Decimal("0.00")
                h.cash_credit_net = Decimal("0.00")

        # 4. Validar conservação financeira estrita
        sum_gross = sum((h.cash_credit_gross for h in holders), Decimal("0.00"))
        sum_fees = sum((h.fee_deduction for h in holders), Decimal("0.00"))
        sum_net = sum((h.cash_credit_net for h in holders), Decimal("0.00"))

        if sum_gross != auction.gross_total_amount:
            raise FinancialConservationError(
                f"Violação de conservação de valor bruto: Soma({sum_gross}) != Leilão({auction.gross_total_amount})"
            )
        if sum_fees != auction.total_fees:
            raise FinancialConservationError(
                f"Violação de conservação de taxas: Soma({sum_fees}) != Total({auction.total_fees})"
            )
        if sum_net != auction.net_total_amount:
            raise FinancialConservationError(
                f"Violação de conservação de valor líquido: Soma({sum_net}) != Leilão({auction.net_total_amount})"
            )

        return holders

    def validate_conservation_invariants(self, event_id: str) -> Dict[str, Any]:
        """Audita formalmente todas as invariantes físicas e financeiras da operação."""
        event = self.events[event_id]
        pool = self.pools[event_id]
        holders = self.holders[event_id]
        auction = self.auctions.get(event_id)

        # 1. Invariante Física de Conservação de Ações:
        # Soma(Q_orig * ratio_to / ratio_from) == Soma(Q_int) + Soma(Q_frac)
        sum_orig = sum(h.original_shares for h in holders)
        raw_theoretical_shares = _quantize_fraction(Decimal(sum_orig) * event.grouping_factor)
        sum_whole = sum(h.whole_shares for h in holders)
        sum_frac = sum((h.fraction_share for h in holders), Decimal("0"))
        share_conservation_satisfied = (raw_theoretical_shares == _quantize_fraction(Decimal(sum_whole) + sum_frac))

        # 2. Invariante de Pool de Frações:
        # Lotes do Leilão + Resíduo == Soma das Frações
        pool_conservation_satisfied = (
            _quantize_fraction(Decimal(pool.auction_whole_shares) + pool.residual_unauctioned_fraction)
            == _quantize_fraction(pool.total_fractional_shares)
        )

        # 3. Invariante de Conservação Financeira do Leilão:
        sum_gross = sum((h.cash_credit_gross for h in holders), Decimal("0.00"))
        sum_fees = sum((h.fee_deduction for h in holders), Decimal("0.00"))
        sum_net = sum((h.cash_credit_net for h in holders), Decimal("0.00"))

        if auction is not None:
            expected_gross = auction.gross_total_amount
            expected_fees = auction.total_fees
            expected_net = auction.net_total_amount
            shares_sold = auction.shares_auctioned
        else:
            expected_gross = Decimal("0.00")
            expected_fees = Decimal("0.00")
            expected_net = Decimal("0.00")
            shares_sold = 0

        gross_cash_conservation = (sum_gross == expected_gross)
        fee_conservation = (sum_fees == expected_fees)
        net_cash_conservation = (sum_net == expected_net)

        # 4. Invariante de Linha (Crédito Líquido == Bruto - Taxas para cada conta)
        row_conservation_satisfied = all(
            h.cash_credit_net == (h.cash_credit_gross - h.fee_deduction)
            for h in holders
        )

        all_ok = (
            share_conservation_satisfied
            and pool_conservation_satisfied
            and gross_cash_conservation
            and fee_conservation
            and net_cash_conservation
            and row_conservation_satisfied
        )

        return {
            "event_id": event_id,
            "ticker": event.ticker,
            "share_conservation_satisfied": share_conservation_satisfied,
            "pool_conservation_satisfied": pool_conservation_satisfied,
            "gross_cash_conservation_satisfied": gross_cash_conservation,
            "fee_conservation_satisfied": fee_conservation,
            "net_cash_conservation_satisfied": net_cash_conservation,
            "row_conservation_satisfied": row_conservation_satisfied,
            "all_invariants_satisfied": all_ok,
            "metrics": {
                "sum_original_shares": sum_orig,
                "sum_whole_shares": sum_whole,
                "sum_fraction_shares": str(sum_frac.normalize()),
                "auction_shares_sold": shares_sold,
                "gross_total_amount": str(expected_gross),
                "total_fees": str(expected_fees),
                "net_total_amount": str(expected_net),
                "distributed_net_credit": str(sum_net),
            },
        }

    def run_full_cycle(
        self,
        event: GroupingEvent,
        holders: Sequence[ShareFractionHolder],
        auction: FractionsAuctionOutcome,
    ) -> Dict[str, Any]:
        """Executa o ciclo completo ponta a ponta: registro, segregação, leilão e rateio financeiro."""
        self.register_event(event)
        self.process_custody_accounts(event.event_id, holders)
        self.register_auction_outcome(auction)
        self.calculate_pro_rata_distribution(event.event_id)
        return self.validate_conservation_invariants(event.event_id)

    def export_liquidation_statement(self, event_id: str) -> List[Dict[str, Any]]:
        """Gera o extrato de liquidação financeira e física para as contas de custódia."""
        if event_id not in self.holders:
            raise CustodyAccountError(f"Evento '{event_id}' não possui extratos apurados.")
        return [h.to_dict() for h in self.holders[event_id]]

    @classmethod
    def from_json_fixture(
        cls, path: Union[str, Path]
    ) -> Tuple[ShareFractionsAuctionEngine, GroupingEvent, List[ShareFractionHolder], FractionsAuctionOutcome]:
        """Instancia o motor e carrega os componentes a partir de uma fixture JSON oficial."""
        fixture_path = Path(path)
        with fixture_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        event_data = data["event"]
        event = GroupingEvent(
            event_id=event_data["event_id"],
            ticker=event_data["ticker"],
            isin=event_data["isin"],
            company_name=event_data["company_name"],
            ratio_from=int(event_data["ratio_from"]),
            ratio_to=int(event_data["ratio_to"]),
            event_type=GroupingEventType(event_data.get("event_type", "REVERSE_SPLIT")),
            approval_date=date.fromisoformat(event_data["approval_date"]),
            cutoff_date=date.fromisoformat(event_data["cutoff_date"]),
            adjustment_period_days=int(event_data.get("adjustment_period_days", 30)),
            trading_ex_date=date.fromisoformat(event_data["trading_ex_date"]) if event_data.get("trading_ex_date") else None,
            auction_date=date.fromisoformat(event_data["auction_date"]) if event_data.get("auction_date") else None,
            settlement_date=date.fromisoformat(event_data["settlement_date"]) if event_data.get("settlement_date") else None,
        )

        holders: List[ShareFractionHolder] = []
        for h_data in data["holders"]:
            holder = ShareFractionHolder(
                investor_account=h_data["investor_account"],
                participant_code=h_data["participant_code"],
                investor_name=h_data["investor_name"],
                document_id=h_data["document_id"],
                original_shares=int(h_data["original_shares"]),
            )
            holders.append(holder)

        auc_data = data["auction"]
        auction = FractionsAuctionOutcome(
            auction_id=auc_data["auction_id"],
            event_id=event.event_id,
            ticker=auc_data.get("ticker", event.ticker),
            shares_auctioned=int(auc_data["shares_auctioned"]),
            gross_price_per_share=Decimal(str(auc_data["gross_price_per_share"])),
            gross_total_amount=Decimal(str(auc_data["gross_total_amount"])),
            trading_fees=Decimal(str(auc_data.get("trading_fees", "0.00"))),
            clearing_fees=Decimal(str(auc_data.get("clearing_fees", "0.00"))),
            brokerage_fees=Decimal(str(auc_data.get("brokerage_fees", "0.00"))),
            other_fees=Decimal(str(auc_data.get("other_fees", "0.00"))),
            total_fees=Decimal(str(auc_data.get("total_fees", "0.00"))),
            net_total_amount=Decimal(str(auc_data.get("net_total_amount", "0.00"))),
            net_price_per_share=Decimal(str(auc_data.get("net_price_per_share", "0.00"))),
            auction_date=date.fromisoformat(auc_data["auction_date"]) if auc_data.get("auction_date") else None,
            settlement_date=date.fromisoformat(auc_data["settlement_date"]) if auc_data.get("settlement_date") else None,
        )

        engine = cls()
        return engine, event, holders, auction


__all__ = [
    "ShareFractionsError",
    "GroupingValidationError",
    "CustodyAccountError",
    "AuctionValidationError",
    "FinancialConservationError",
    "GroupingEventType",
    "ShareFractionHolder",
    "GroupingEvent",
    "FractionsPoolSummary",
    "FractionsAuctionOutcome",
    "ShareFractionsAuctionEngine",
]
