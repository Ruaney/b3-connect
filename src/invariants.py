"""B3 Integration Atlas — Núcleo Formal de Invariantes e Leis de Conservação.

Implementa a hierarquia de exceções de invariantes formais, o mecanismo fail-safe
de verificação e as funções de auditoria matemática para as 45 leis de conservação
da B3 (Custódia CSD, Liquidação Multilateral SPB, Alocação RTC, Derivativos e Câmaras).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union


class InvariantViolationError(AssertionError):
    """Exceção levantada quando uma lei invariante matemática ou regra de conservação é violada.

    Herda de AssertionError para compatibilidade com proof systems formais e asserções
    de verificação estática/dinâmica.
    """

    def __init__(self, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.law_code = law_code
        self.details = details or {}

    def __str__(self) -> str:
        prefix = f"[{self.law_code}] " if self.law_code else ""
        return f"VIOLAÇÃO DE INVARIANTE: {prefix}{self.message}"


def check(condition: bool, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
    """Verifica uma condição booleana e levanta InvariantViolationError se for falsa.

    Args:
        condition: Expressão booleana que deve ser estritamente verdadeira.
        message: Mensagem explicativa da invariante violada.
        law_code: Código opcional da lei de conservação (ex: 'LAW-001', 'FL-004').
        details: Dicionário opcional com metadados para depuração e auditoria.
    """
    if not condition:
        raise InvariantViolationError(message, law_code=law_code, details=details)


def enforce_fail_safe(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Executa uma função garantindo que qualquer quebra de integridade seja propagada como InvariantViolationError.

    Captura AssertionError, ValueError, ArithmeticError ou outras exceções de integridade
    e as re-emite como InvariantViolationError fail-safe.
    """
    try:
        return fn(*args, **kwargs)
    except InvariantViolationError:
        raise
    except (AssertionError, ValueError, ArithmeticError, KeyError) as e:
        raise InvariantViolationError(f"Falha de integridade capturada em modo fail-safe: {str(e)}") from e


# ===========================================================================
# Verificadores Matemáticos Formais Especializados
# ===========================================================================


def assert_custody_conservation(
    total: Union[int, Decimal],
    available: Union[int, Decimal],
    blocked_guarantee: Union[int, Decimal],
    blocked_judicial: Union[int, Decimal],
    account_id: str = "",
    symbol: str = "",
) -> None:
    """Lei 1: Conservação Absoluta de Custódia Depositária (IMBARQ012 / CSD).

    Q_total == Q_disponivel + Q_bloqueada_garantia + Q_bloqueada_judicial
    """
    total_calc = available + blocked_guarantee + blocked_judicial
    check(
        total == total_calc,
        f"Custódia desbalanceada para conta '{account_id}' ativo '{symbol}': "
        f"Total ({total}) != Disponível ({available}) + Garantia ({blocked_guarantee}) + Judicial ({blocked_judicial})",
        law_code="LAW-001",
        details={
            "account_id": account_id,
            "symbol": symbol,
            "total": total,
            "available": available,
            "blocked_guarantee": blocked_guarantee,
            "blocked_judicial": blocked_judicial,
        },
    )


def assert_multilateral_financial_netting(
    credits: Sequence[Decimal],
    debits: Sequence[Decimal],
    reported_net_balance: Decimal,
    reported_sign: str,
) -> None:
    """Lei 2: Conservação Financeira Multilateral de Liquidação (IMBARQ004).

    Saldo Líquido == |Total Créditos - Total Débitos|
    Sinal == 'C' se Créditos >= Débitos senão 'D'
    """
    tot_cred = sum(credits, Decimal("0.00"))
    tot_deb = sum(debits, Decimal("0.00"))
    expected_net = abs(tot_cred - tot_deb)
    expected_sign = "C" if tot_cred >= tot_deb else "D"

    check(
        reported_net_balance == expected_net,
        f"Saldo líquido multilateral divergente: informado {reported_net_balance} != esperado {expected_net} (Créditos={tot_cred}, Débitos={tot_deb})",
        law_code="LAW-002",
    )
    check(
        reported_sign == expected_sign,
        f"Sinal do saldo líquido incorreto: informado '{reported_sign}' != esperado '{expected_sign}'",
        law_code="LAW-002",
    )


def assert_allocation_conservation(
    parent_quantity: Union[int, Decimal],
    allocated_quantities: Sequence[Union[int, Decimal]],
    parent_price: Optional[Decimal] = None,
    allocated_prices: Optional[Sequence[Decimal]] = None,
) -> None:
    """Lei 3: Conservação Quantitativa e de Preço em Alocações RTC (FL-008).

    sum(AllocatedQuantity) == ParentTradeQuantity
    E para todo i: AllocatedPrice_i == ParentPrice
    """
    total_allocated = sum(allocated_quantities, Decimal("0") if isinstance(parent_quantity, Decimal) else 0)
    check(
        total_allocated == parent_quantity,
        f"Soma alocada ({total_allocated}) diverge da ordem mãe ({parent_quantity})",
        law_code="LAW-003",
    )
    if parent_price is not None and allocated_prices is not None:
        for idx, p in enumerate(allocated_prices):
            check(
                p == parent_price,
                f"Preço da parcela {idx} ({p}) diverge do preço do trade pai ({parent_price})",
                law_code="LAW-003",
            )


def assert_bilateral_zero_sum(
    party_a_amount: Decimal,
    party_b_amount: Decimal,
    tolerance: Decimal = Decimal("0.00"),
    context_desc: str = "Derivativo Bilateral",
) -> None:
    """Lei 24/26: Conservação Bilateral Soma-Zero em Derivativos (Swap, NDF, Opções Flexíveis).

    party_a_amount + party_b_amount == 0.00
    """
    net_sum = party_a_amount + party_b_amount
    check(
        abs(net_sum) <= tolerance,
        f"Violação de soma-zero em {context_desc}: Parte A ({party_a_amount}) + Parte B ({party_b_amount}) = {net_sum} != 0.00",
        law_code="LAW-ZERO-SUM",
    )


def assert_chronological_sanity(
    start_date: Union[date, datetime],
    end_date: Union[date, datetime],
    context_desc: str = "Contrato",
) -> None:
    """Lei 5/7: Sanidade Cronológica de Contratos e Proventos.

    end_date >= start_date
    """
    check(
        end_date >= start_date,
        f"Inversão cronológica em {context_desc}: data de vencimento/fim ({end_date}) anterior à data de início/abertura ({start_date})",
        law_code="LAW-CHRONO",
    )


def assert_waterfall_conservation(
    gross_loss: Decimal,
    absorbed_by_layers: Sequence[Decimal],
    residual_deficit: Decimal,
) -> None:
    """Lei 39: Conservação Contábil na Cascata de Inadimplência CPMI-IOSCO.

    gross_loss == sum(absorbed_by_layers) + residual_deficit
    """
    total_absorbed = sum(absorbed_by_layers, Decimal("0.00"))
    check(
        gross_loss == total_absorbed + residual_deficit,
        f"Cascata de default desbalanceada: Perda ({gross_loss}) != Absorvido ({total_absorbed}) + Déficit Residual ({residual_deficit})",
        law_code="LAW-039",
    )


def assert_token_csd_parity(
    csd_locked_quantity: Union[int, Decimal],
    drex_circulating_quantity: Union[int, Decimal],
    symbol: str = "",
) -> None:
    """Lei 45: Conservação Absoluta de Custódia Total CSD + DREX (FL-061).

    csd_locked_quantity == drex_circulating_quantity
    """
    check(
        csd_locked_quantity == drex_circulating_quantity,
        f"Descasamento de lastro CSD vs Rede DREX no ativo '{symbol}': "
        f"Bloqueado CSD ({csd_locked_quantity}) != Circulante DREX ({drex_circulating_quantity})",
        law_code="LAW-045",
    )


def assert_crypto_digital_assets_por_parity(
    total_segregated_backing: Union[int, Decimal],
    total_circulating_supply: Union[int, Decimal],
    asset_code: str = "",
) -> None:
    """Lei 53: Conservação Estrita de Paridade 1:1 e Proof-of-Reserve em Ativos Digitais (FL-075).

    total_segregated_backing == total_circulating_supply (Lei nº 14.478/2022).
    """
    check(
        total_segregated_backing == total_circulating_supply,
        f"Quebra de Paridade 1:1 em Ativos Digitais / RWA no ativo '{asset_code}': "
        f"Lastro Segregado CSD ({total_segregated_backing}) != Supply Circulante ({total_circulating_supply})",
        law_code="LAW-053",
    )


def assert_pvp_atomic_conservation(
    brl_amount: Decimal,
    usd_amount: Decimal,
    exchange_rate: Decimal,
    tolerance_cents: Decimal = Decimal("0.01"),
) -> None:
    """Lei 44: Conservação Bilateral PvP e Eliminação do Risco Herstatt (FL-060).

    brl_amount == round(usd_amount * exchange_rate, 2)
    """
    expected_brl = (usd_amount * exchange_rate).quantize(Decimal("0.01"))
    diff = abs(brl_amount - expected_brl)
    check(
        diff <= tolerance_cents,
        f"Paridade cambial violada na liquidação PvP: BRL={brl_amount} != USD({usd_amount}) * Taxa({exchange_rate}) [Esperado={expected_brl}, Diff={diff}]",
        law_code="LAW-044",
    )


def assert_forward_term_regulatory_bounds(
    calendar_days: int,
    min_days: int = 16,
    max_days: int = 999,
) -> None:
    """Lei 25: Prazos Regulamentares de Mercado a Termo B3 (16 a 999 dias corridos)."""
    check(
        min_days <= calendar_days <= max_days,
        f"Prazo do contrato a termo ({calendar_days} dias corridos) fora do limite regulatório B3 [{min_days}..{max_days}]",
        law_code="LAW-025",
    )


def assert_book_quantity_conservation(
    delta_bid: Union[int, Decimal],
    delta_ask: Union[int, Decimal],
    delta_executed: Union[int, Decimal],
    delta_cancelled: Union[int, Decimal],
    delta_injected: Union[int, Decimal] = 0,
    symbol: str = "",
) -> None:
    """Lei 46 / LAW-070: Lei de Conservação de Quantidade no Livro de Ofertas (FL-070).

    Delta(Bid) + Delta(Ask) + Delta(Executado) + Delta(Cancelado) - Delta(Injetado) == 0.
    Para transições internas sem novas entradas (ex: cancelamento, execução, flash crash):
    Delta(Bid) + Delta(Ask) + Delta(Executado) + Delta(Cancelado) == 0.
    """
    total_delta = (delta_bid + delta_ask) + delta_executed + delta_cancelled - delta_injected
    check(
        total_delta == 0,
        f"Violação da Lei de Conservação de Quantidade no Book '{symbol}': "
        f"Delta(Bid)={delta_bid} + Delta(Ask)={delta_ask} + Delta(Exec)={delta_executed} + Delta(Canc)={delta_cancelled} "
        f"- Delta(Inj)={delta_injected} = {total_delta} != 0",
        law_code="LAW-070",
    )


def assert_delisting_opa_quorum(
    tendered_shares: Union[int, Decimal],
    free_float_shares: Union[int, Decimal],
    quorum_ratio: Decimal = Decimal("0.66666667"),
    offer_id: str = "",
) -> None:
    """Lei 53: Quórum Qualificado de 2/3 e Conservação em Leilão de Deslistagem OPA (FL-070).

    tendered_shares / free_float_shares >= 2/3 (Art. 4º-A Lei 6.404/76 e Res. CVM 166).
    """
    check(
        free_float_shares > 0,
        f"Free float nulo ou inválido na OPA '{offer_id}': {free_float_shares}",
        law_code="LAW-053",
    )
    actual_ratio = Decimal(tendered_shares) / Decimal(free_float_shares)
    check(
        actual_ratio >= quorum_ratio,
        f"Quórum de 2/3 do Free Float não atingido na OPA '{offer_id}': "
        f"{tendered_shares}/{free_float_shares} = {actual_ratio:.4f} < {quorum_ratio:.4f}",
        law_code="LAW-053",
    )


def assert_intraday_liquidity_risk_non_negative(
    net_cash_buffer: Decimal,
    participant_code: int = 0,
) -> None:
    """Lei 55: Preservação de Buffer de Liquidez Intradiária Não-Negativo e Estresse PTAX (FL-072).

    net_cash_buffer >= 0 sob estresse cambial e liquidação multilateral (BCBS 248 / Circular BACEN 3.749).
    """
    check(
        net_cash_buffer >= Decimal("0.00"),
        f"Déficit crítico de liquidez intradiária no participante {participant_code}: "
        f"Buffer líquido = R$ {net_cash_buffer:.2f} < 0",
        law_code="LAW-055",
    )


def assert_secondary_fixed_income_identity(
    dirty_price: Decimal,
    clean_price: Decimal,
    accrued_interest: Decimal,
    tolerance_cents: Decimal = Decimal("0.0001"),
    asset_ticker: str = "",
) -> None:
    """Lei 56: Identidade Escritural PU Sujo == PU Limpo + Juros Decorridos (FL-071).

    dirty_price == clean_price + accrued_interest (Padrão ANBIMA e B3 NoMe).
    """
    diff = abs(dirty_price - (clean_price + accrued_interest))
    check(
        diff <= tolerance_cents,
        f"Quebra da identidade de preço unitário em renda fixa no ativo '{asset_ticker}': "
        f"PU Sujo ({dirty_price}) != PU Limpo ({clean_price}) + Juros ({accrued_interest}) [Diff={diff}]",
        law_code="LAW-056",
    )


def assert_subordinated_capital_hierarchy(
    tier1_balance: Decimal,
    tier2_loss_absorbed: Decimal,
    issuer_cnpj: str = "",
) -> None:
    """Lei 57: Inviolabilidade da Hierarquia de Capital e Absorção Prioritária AT1 (FL-073).

    Se tier2_loss_absorbed > 0, então tier1_balance DEVE ser rigorosamente 0,00 (Res. CMN nº 4.958/2021).
    """
    if tier2_loss_absorbed > Decimal("0.00"):
        check(
            tier1_balance == Decimal("0.00"),
            f"Violação da hierarquia de capital no emissor '{issuer_cnpj}': "
            f"Tier 2 absorveu R$ {tier2_loss_absorbed} havendo R$ {tier1_balance} remanescente em AT1 Nível 1.",
            law_code="LAW-057",
        )


def assert_agricultural_warrant_conservation(
    initial_quantity: Decimal,
    effective_quantity: Decimal,
    technical_loss_quantity: Decimal,
    warrant_id: str = "",
) -> None:
    """Lei 58: Conservação Física Estrita de Sacas/Massa e Quebra Técnica em Warrants B3 (FL-078).

    initial_quantity == effective_quantity + technical_loss_quantity (Lei nº 11.076/2004).
    """
    check(
        initial_quantity == (effective_quantity + technical_loss_quantity),
        f"Violação da conservação de massa física no warrant agrícola '{warrant_id}': "
        f"Inicial ({initial_quantity}) != Efetiva ({effective_quantity}) + Quebra ({technical_loss_quantity})",
        law_code="LAW-058",
    )


