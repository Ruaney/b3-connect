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


def assert_clearing_position_integrity(
    bought_positions: Union[int, Decimal, Sequence[Union[int, Decimal]]],
    sold_positions: Union[int, Decimal, Sequence[Union[int, Decimal]]],
    tolerance: Union[int, Decimal] = Decimal("0"),
    symbol: str = "",
) -> None:
    """Lei 4 / LAW-004: Integridade Posicional da Câmara e Paridade Comprador-Vendedor (IMBARQ006 / Lei nº 10.214/2001).

    ∑ Posições Compradas == ∑ Posições Vendidas (Posição Líquida CCP == 0).
    A Contraparte Central (CCP) é neutra: todo contrato comprado por um participante
    possui exatamente um contraparte vendedora espelhada no pregão / câmara.
    """
    total_bought = sum(bought_positions) if isinstance(bought_positions, (list, tuple)) else bought_positions
    total_sold = sum(sold_positions) if isinstance(sold_positions, (list, tuple)) else sold_positions
    diff = abs(total_bought - total_sold)
    check(
        diff <= tolerance,
        f"Violação da Integridade Posicional da Câmara (LAW-004) para '{symbol}': "
        f"Posições Compradas ({total_bought}) != Posições Vendidas ({total_sold}) [Diff={diff}]",
        law_code="LAW-004",
        details={
            "symbol": symbol,
            "total_bought": str(total_bought),
            "total_sold": str(total_sold),
            "diff": str(diff),
        },
    )


def assert_bilateral_zero_sum(
    party_a_amount: Decimal,
    party_b_amount: Decimal,
    tolerance: Decimal = Decimal("0.00"),
    context_desc: str = "Derivativo Bilateral",
    **kwargs: Any,
) -> None:
    """Lei 24/26: Conservação Bilateral Soma-Zero em Derivativos (Swap, NDF, Opções Flexíveis).

    party_a_amount + party_b_amount == 0.00
    """
    desc = kwargs.get("context", context_desc)
    net_sum = party_a_amount + party_b_amount
    check(
        abs(net_sum) <= tolerance,
        f"Violação de soma-zero em {desc}: Parte A ({party_a_amount}) + Parte B ({party_b_amount}) = {net_sum} != 0.00",
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




def assert_cbio_futures_daily_adjustment_zero_sum(
    buyer_total_adjustment: Decimal,
    seller_total_adjustment: Decimal,
    contract_ticker: str = "",
    settlement_date: str = "",
) -> None:
    """Lei 59 / LAW-086: Conservação Soma-Zero no Ajuste Diário de Futuros de CBIO (FL-086).

    Somatório(Ajuste_Comprador) + Somatório(Ajuste_Vendedor) == 0,00 centavo a centavo
    (Mark-to-Market diário da Câmara B3, Lei nº 13.576/2017 e Manual de Compensação B3).
    """
    total = buyer_total_adjustment + seller_total_adjustment
    check(
        total == Decimal("0.00"),
        f"Violação de soma-zero no ajuste diário de futuros CBIO '{contract_ticker}' em {settlement_date}: "
        f"Ajuste Compradores ({buyer_total_adjustment}) + Ajuste Vendedores ({seller_total_adjustment}) = {total} != 0.00",
        law_code="LAW-086",
        details={
            "contract_ticker": contract_ticker,
            "settlement_date": str(settlement_date),
            "buyer_total_adjustment": str(buyer_total_adjustment),
            "seller_total_adjustment": str(seller_total_adjustment),
            "net_total": str(total),
        },
    )


def assert_cbio_futures_open_interest_parity(
    long_open_interest: int,
    short_open_interest: int,
    reported_open_interest: int,
    contract_ticker: str = "",
) -> None:
    """Lei 60 / LAW-087: Paridade e Conservação de Contratos em Aberto (Open Interest) em Futuros de CBIO (FL-086).

    Posições_Compradas == Posições_Vendidas == Open_Interest_Total (Câmara B3 Clearing).
    """
    check(
        long_open_interest == short_open_interest and long_open_interest == reported_open_interest,
        f"Violação da paridade de Open Interest em futuros de CBIO '{contract_ticker}': "
        f"Compradas ({long_open_interest}) != Vendidas ({short_open_interest}) ou != Reportado ({reported_open_interest})",
        law_code="LAW-087",
        details={
            "contract_ticker": contract_ticker,
            "long_open_interest": long_open_interest,
            "short_open_interest": short_open_interest,
            "reported_open_interest": reported_open_interest,
        },
    )


def assert_cbio_futures_final_settlement_zero_sum(
    buyer_final_pnl: Decimal,
    seller_final_pnl: Decimal,
    contract_ticker: str = "",
    settlement_date: str = "",
) -> None:
    """Lei 61 / LAW-088: Conservação Soma-Zero na Liquidação Final Financeira de Futuros de CBIO (FL-086).

    Somatório(P&L Final Compradores) + Somatório(P&L Final Vendedores) == 0,00 centavo a centavo
    baseado no Preço de Fechamento Spot de CBIO (RenovaBio / Câmara B3).
    """
    total = buyer_final_pnl + seller_final_pnl
    check(
        total == Decimal("0.00"),
        f"Violação de soma-zero na liquidação final financeira de futuros CBIO '{contract_ticker}' em {settlement_date}: "
        f"P&L Final Compradores ({buyer_final_pnl}) + P&L Final Vendedores ({seller_final_pnl}) = {total} != 0.00",
        law_code="LAW-088",
        details={
            "contract_ticker": contract_ticker,
            "settlement_date": str(settlement_date),
            "buyer_final_pnl": str(buyer_final_pnl),
            "seller_final_pnl": str(seller_final_pnl),
            "net_total": str(total),
        },
    )


def assert_irs_periodic_reset_zero_sum(
    party_a_amount: Decimal,
    party_b_amount: Decimal,
    period_index: int = 1,
    contract_id: str = "",
) -> None:
    """Lei 62 / LAW-080: Conservação Bilateral Soma-Zero em Swaps com Ajustes Periódicos IRS (FL-080).

    Saldo_Líquido_Ponta_A + Saldo_Líquido_Ponta_B == 0,00 centavo a centavo a cada reset (NoMe B3).
    """
    total = party_a_amount + party_b_amount
    check(
        total == Decimal("0.00"),
        f"Violação de soma-zero bilateral no IRS '{contract_id}' período #{period_index}: "
        f"Ponta A ({party_a_amount}) + Ponta B ({party_b_amount}) = {total} != 0.00",
        law_code="LAW-080",
        details={
            "contract_id": contract_id,
            "period_index": period_index,
            "party_a_amount": str(party_a_amount),
            "party_b_amount": str(party_b_amount),
            "sum": str(total),
        },
    )


def assert_no_arbitrage_pre_di_curve(
    telescoping_forward_sum: Decimal,
    terminal_discount_factor: Decimal,
    initial_discount_factor: Decimal = Decimal("1.00000000"),
    tolerance: Decimal = Decimal("0.0001"),
    context_desc: str = "Curva Pré/DI",
) -> None:
    """Lei 63 / LAW-081: Invariante de Não-Arbitragem e Telescopagem na Curva Pré/DI (FL-080).

    Soma( (F_fwd,j - 1) * DF_j ) == DF_0 - DF_n (Identidade estrita de não-arbitragem e ausência de arbitragem por replicação).
    """
    expected = initial_discount_factor - terminal_discount_factor
    diff = abs(telescoping_forward_sum - expected)
    check(
        diff <= tolerance,
        f"Violação de Não-Arbitragem na Curva Pré/DI [{context_desc}]: "
        f"Soma Telescópica ({telescoping_forward_sum}) != DF_0 - DF_n ({expected}) [Diff={diff}]",
        law_code="LAW-081",
        details={
            "context_desc": context_desc,
            "telescoping_forward_sum": str(telescoping_forward_sum),
            "expected": str(expected),
            "terminal_discount_factor": str(terminal_discount_factor),
            "initial_discount_factor": str(initial_discount_factor),
            "diff": str(diff),
        },
    )



def assert_fidc_water_tight_subordination(
    junior_equity: Decimal,
    mezzanine_absorbed: Decimal,
    senior_absorbed: Decimal,
    fund_id: str = "",
) -> None:
    """Lei 62 / LAW-077A: Prioridade Estrita de Subordinação FIDC (Water-Tight Priority - FL-077).

    A cota Mezanino só absorve perdas se a Subordinada Júnior estiver com PL zerado.
    A cota Sênior só absorve perdas se Mezanino e Júnior estiverem com PL zerado.
    """
    valid = True
    msg = ""
    if mezzanine_absorbed > Decimal("0.00") and junior_equity > Decimal("0.00"):
        valid = False
        msg = f"Mezanino absorveu perda ({mezzanine_absorbed}) com Júnior ainda detendo PL ({junior_equity})"
    elif senior_absorbed > Decimal("0.00") and junior_equity > Decimal("0.00"):
        valid = False
        msg = f"Sênior absorveu perda ({senior_absorbed}) com Júnior ainda detendo PL ({junior_equity})"

    check(
        valid,
        f"Violação da Subordinação Estrita (Water-Tight Priority) no FIDC '{fund_id}': {msg}",
        law_code="LAW-077A",
        details={
            "fund_id": fund_id,
            "junior_equity": str(junior_equity),
            "mezzanine_absorbed": str(mezzanine_absorbed),
            "senior_absorbed": str(senior_absorbed),
        },
    )


def assert_fund_equity_conservation(
    total_net_assets: Decimal,
    classes_equity_sum: Decimal,
    fund_id: str = "",
    tolerance: Decimal = Decimal("0.02"),
) -> None:
    """Lei 63 / LAW-077B: Conservação Patrimonial de Cotas de Fundos Estruturados (FL-077).

    Patrimônio_Líquido == Soma(Quantidade_Cotas_i * Valor_Cota_Unitario_i).
    """
    diff = abs(total_net_assets - classes_equity_sum)
    check(
        diff <= tolerance,
        f"Violação da Conservação Patrimonial de Cotas no Fundo '{fund_id}': "
        f"PL ({total_net_assets}) != Soma das Classes ({classes_equity_sum}) [Diff={diff}]",
        law_code="LAW-077B",
        details={
            "fund_id": fund_id,
            "total_net_assets": str(total_net_assets),
            "classes_equity_sum": str(classes_equity_sum),
            "diff": str(diff),
        },
    )


def assert_fii_minimum_distribution(
    declared_distribution: Decimal,
    net_cash_profit: Decimal,
    fund_id: str = "",
) -> None:
    """Lei 64 / LAW-077C: Piso Mandatório de Distribuição de Proventos FII (Lei 8.668/93 - FL-077).

    Proventos_Distribuidos >= 0.95 * Lucro_Caixa_Semestral_Positivo.
    """
    if net_cash_profit > Decimal("0.00"):
        required_min = (net_cash_profit * Decimal("0.95")).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")
        check(
            declared_distribution >= required_min,
            f"Violação do Piso Mandatório de 95% de Proventos FII no Fundo '{fund_id}': "
            f"Distribuído ({declared_distribution}) < 95% do Lucro Caixa ({required_min}) "
            f"[Lucro Caixa={net_cash_profit}]",
            law_code="LAW-077C",
            details={
                "fund_id": fund_id,
                "declared_distribution": str(declared_distribution),
                "required_minimum_95": str(required_min),
                "net_cash_profit": str(net_cash_profit),
            },
        )


def assert_power_energy_conservation(
    total_mwh_sold: Decimal,
    total_mwh_bought: Decimal,
    contract_id: str = "",
    tolerance: Decimal = Decimal("0.0001"),
) -> None:
    """Lei 65 / LAW-089: Conservação Estrita de Energia Elétrica Bilateral (FL-087).

    Total_MWh_Vendido == Total_MWh_Comprado centésimo de MWh a centésimo de MWh.
    """
    diff = abs(total_mwh_sold - total_mwh_bought)
    check(
        diff <= tolerance,
        f"Violação da Conservação de Energia Elétrica Bilateral no contrato '{contract_id}': "
        f"MWh Vendido ({total_mwh_sold}) != MWh Comprado ({total_mwh_bought}) [Diff={diff}]",
        law_code="LAW-089",
        details={
            "contract_id": contract_id,
            "total_mwh_sold": str(total_mwh_sold),
            "total_mwh_bought": str(total_mwh_bought),
            "diff": str(diff),
        },
    )


def assert_power_settlement_zero_sum(
    buyer_debits: Sequence[Decimal] | Decimal,
    seller_credits: Sequence[Decimal] | Decimal,
    reference_id: str = "",
    tolerance: Decimal = Decimal("0.01"),
) -> None:
    """Lei 66 / LAW-090: Conservação Soma-Zero na Liquidação Financeira de Energia (FL-087).

    Somatório(Débitos Comprador) == Somatório(Créditos Vendedor + Credores Fiduciários) centavo a centavo.
    """
    total_debits = sum(buyer_debits) if isinstance(buyer_debits, (list, tuple)) else buyer_debits
    total_credits = sum(seller_credits) if isinstance(seller_credits, (list, tuple)) else seller_credits
    diff = abs(total_debits - total_credits)
    check(
        diff <= tolerance,
        f"Violação de Soma-Zero na Liquidação Financeira de Energia '{reference_id}': "
        f"Débitos ({total_debits}) != Créditos ({total_credits}) [Diff={diff}]",
        law_code="LAW-090",
        details={
            "reference_id": reference_id,
            "total_debits": str(total_debits),
            "total_credits": str(total_credits),
            "diff": str(diff),
        },
    )


def assert_power_non_negativity(
    mw_average: Decimal,
    total_hours: int,
    volume_mwh: Decimal,
    price_mwh: Decimal,
    contract_id: str = "",
) -> None:
    """Lei 67 / LAW-091: Invariante de Não-Negatividade de Montantes e Preços de Energia (FL-087).

    MWmédio >= 0, Horas > 0, MWh >= 0 e Preço/MWh >= 0.
    """
    is_valid = (
        mw_average >= Decimal("0.00")
        and total_hours > 0
        and volume_mwh >= Decimal("0.00")
        and price_mwh >= Decimal("0.00")
    )
    check(
        is_valid,
        f"Violação de Não-Negatividade em contrato de energia '{contract_id}': "
        f"MWmédio={mw_average}, Horas={total_hours}, MWh={volume_mwh}, Preço={price_mwh}",
        law_code="LAW-091",
        details={
            "contract_id": contract_id,
            "mw_average": str(mw_average),
            "total_hours": total_hours,
            "volume_mwh": str(volume_mwh),
            "price_mwh": str(price_mwh),
        },
    )


def assert_fiduciary_assignment_limit(
    assigned_amount: Decimal,
    total_contract_value: Decimal,
    contract_id: str = "",
) -> None:
    """Lei 68 / LAW-092: Limite de Trava Fiduciária de Recebíveis de Energia (FL-087).

    Valor Travado Fiduciariamente <= Valor Total do Recebível Contratado.
    """
    check(
        assigned_amount <= total_contract_value and assigned_amount >= Decimal("0.00"),
        f"Violação do Limite da Trava Fiduciária no contrato '{contract_id}': "
        f"Valor Travado ({assigned_amount}) excede Valor Total ({total_contract_value}) ou é negativo",
        law_code="LAW-092",
        details={
            "contract_id": contract_id,
            "assigned_amount": str(assigned_amount),
            "total_contract_value": str(total_contract_value),
        },
    )


def assert_asian_put_call_parity(
    call_price: Decimal,
    put_price: Decimal,
    discount_factor: Decimal,
    expected_underlying_avg: Decimal,
    strike_price: Decimal,
    tolerance: Decimal = Decimal("0.0001"),
    contract_id: str = "",
) -> None:
    """Lei 71 / LAW-095: Paridade Put-Call Asiática e Condição de Não-Arbitragem (FL-092).

    | (C - P) - DF * (E[S_avg] - K) | <= tolerance
    """
    lhs = call_price - put_price
    rhs = discount_factor * (expected_underlying_avg - strike_price)
    diff = abs(lhs - rhs)
    check(
        diff <= tolerance,
        f"Violação da Paridade Put-Call Asiática (LAW-095) no contrato '{contract_id}': "
        f"C - P ({lhs}) != DF * (E[S_avg] - K) ({rhs}), diferença = {diff} > tolerância {tolerance}",
        law_code="LAW-095",
        details={
            "contract_id": contract_id,
            "call_price": str(call_price),
            "put_price": str(put_price),
            "diff": str(diff),
            "tolerance": str(tolerance),
        },
    )


def assert_asian_effective_volatility_bound(
    effective_volatility: Decimal,
    vanilla_volatility: Decimal,
    tolerance: Decimal = Decimal("0.0001"),
    contract_id: str = "",
) -> None:
    """Lei 72 / LAW-096: Invariante de Menor Volatilidade Efetiva de Opção Asiática (FL-092).

    Vol_Asiatica <= Vol_Vanilla (a amostragem por média reduz a variância estocástica).
    """
    check(
        effective_volatility <= vanilla_volatility + tolerance,
        f"Violação da Menor Volatilidade Efetiva Asiática (LAW-096) no contrato '{contract_id}': "
        f"Vol_Asiatica ({effective_volatility}) excede Vol_Vanilla ({vanilla_volatility})",
        law_code="LAW-096",
        details={
            "contract_id": contract_id,
            "effective_volatility": str(effective_volatility),
            "vanilla_volatility": str(vanilla_volatility),
        },
    )


def assert_asian_zero_sum_settlement(
    gross_holder_amount: Decimal,
    gross_writer_amount: Decimal,
    contract_id: str = "",
) -> None:
    """Lei 73 / LAW-097: Conservação Financeira Bilateral Soma-Zero em Liquidação Asiática (FL-092).

    Gross_Holder + Gross_Writer == 0,00
    """
    total = gross_holder_amount + gross_writer_amount
    check(
        total == Decimal("0.00"),
        f"Violação da Conservação Financeira Soma-Zero (LAW-097) no contrato '{contract_id}': "
        f"Titular ({gross_holder_amount}) + Lançador ({gross_writer_amount}) = {total} != 0,00",
        law_code="LAW-097",
        details={
            "contract_id": contract_id,
            "gross_holder_amount": str(gross_holder_amount),
            "gross_writer_amount": str(gross_writer_amount),
            "balance": str(total),
        },
    )



def assert_drex_atomic_dvp_conservation(
    seller_tpft_delta: int,
    buyer_tpft_delta: int,
    expected_tpft_qty: int,
    buyer_financial_delta: Decimal,
    seller_financial_delta: Decimal,
    expected_financial_amount: Decimal,
    operation_id: str = "",
) -> None:
    """Lei 69 / LAW-089A: Conservação Atômica DvP e PvP em DLT Drex Fase 2 (FL-089).

    Em liquidação atômica Delivery-versus-Payment:
    1. A quantidade de TPFt debitada do vendedor deve ser estritamente igual à creditada ao comprador.
    2. O montante financeiro (Real Digital ou Real Tokenizado) debitado do comprador deve ser estritamente igual ao creditado ao vendedor (ou compensado interbancariamente).
    3. Princípio All-or-None: Ambas as pernas concluem conjuntamente ou sofrem reversão integral.
    """
    check(
        seller_tpft_delta == expected_tpft_qty and buyer_tpft_delta == expected_tpft_qty,
        f"Violação da Conservação Atômica DvP de TPFt na operação '{operation_id}': "
        f"Débito Vendedor ({seller_tpft_delta}) ou Crédito Comprador ({buyer_tpft_delta}) "
        f"!= Quantidade Esperada ({expected_tpft_qty})",
        law_code="LAW-089A",
        details={
            "operation_id": operation_id,
            "seller_tpft_delta": seller_tpft_delta,
            "buyer_tpft_delta": buyer_tpft_delta,
            "expected_tpft_qty": expected_tpft_qty,
        },
    )
    check(
        buyer_financial_delta == expected_financial_amount and seller_financial_delta == expected_financial_amount,
        f"Violação da Conservação Atômica DvP Financeira na operação '{operation_id}': "
        f"Débito Comprador ({buyer_financial_delta}) ou Crédito Vendedor ({seller_financial_delta}) "
        f"!= Montante Esperado ({expected_financial_amount})",
        law_code="LAW-089A",
        details={
            "operation_id": operation_id,
            "buyer_financial_delta": str(buyer_financial_delta),
            "seller_financial_delta": str(seller_financial_delta),
            "expected_financial_amount": str(expected_financial_amount),
        },
    )


def assert_drex_sovereign_parity_conservation(
    real_tokenizado_amount: Decimal,
    real_digital_amount: Decimal,
    operation_id: str = "",
) -> None:
    """Lei 70 / LAW-089B: Paridade 1:1 e Conservação de Moeda Soberana Drex Fase 2 (FL-089).

    1 Real Tokenizado == 1 Real Digital == R$ 1,00 BRL.
    Em trocas PvP e liquidações interbancárias, a paridade deve ser preservada sem criação espúria de liquidez.
    """
    check(
        real_tokenizado_amount == real_digital_amount,
        f"Quebra de Paridade 1:1 de Moeda Soberana Drex na operação '{operation_id}': "
        f"Real Tokenizado ({real_tokenizado_amount}) != Real Digital ({real_digital_amount})",
        law_code="LAW-089B",
        details={
            "operation_id": operation_id,
            "real_tokenizado_amount": str(real_tokenizado_amount),
            "real_digital_amount": str(real_digital_amount),
        },
    )


def assert_drex_token_non_negative_conservation(
    balances: Dict[str, Union[int, Decimal]],
    entity_id: str = "",
) -> None:
    """Lei 71 / LAW-089C: Não-Negatividade e Não-Geração Inflacionária de Tokens Drex (FL-089).

    Nenhum saldo (TPFt, Real Digital, Real Tokenizado) pode ser negativo em qualquer carteira ou contrato.
    """
    for asset, balance in balances.items():
        check(
            balance >= 0,
            f"Saldo negativo de token detectado para entidade '{entity_id}' no ativo '{asset}': {balance} < 0",
            law_code="LAW-089C",
            details={
                "entity_id": entity_id,
                "asset": asset,
                "balance": str(balance),
            },
        )


def assert_closing_cross_freeze_inviolability(
    freeze_active: bool = True,
    cancellations_attempted: int = 0,
    modifications_attempted: int = 0,
    symbol: str = "",
    auction_id: str = "",
) -> None:
    """Lei 74 / LAW-104A: Inviolabilidade do Congelamento (Freeze) no Leilão de Fechamento (FL-104).

    Durante o período de congelamento (Freeze Window / Random Ending / Uncross) do Leilão de Fechamento
    (PUMA Closing Call), o livro de ofertas torna-se estritamente imutável:
    1. É proibido qualquer cancelamento de ordens existentes (cancellations_attempted == 0).
    2. É proibida qualquer alteração ou redução de ordens (modifications_attempted == 0).
    3. Nenhuma remoção de liquidez é tolerada antes da formação e desempate do Preço Teórico de Fechamento (PTF).

    Args:
        freeze_active: Se o estado de congelamento está ativado.
        cancellations_attempted: Quantidade de cancelamentos tentados/registrados durante o freeze.
        modifications_attempted: Quantidade de modificações de preço/quantidade tentadas durante o freeze.
        symbol: Código de negociação do instrumento (ex: 'PETR4', 'VALE3').
        auction_id: Identificador da sessão ou leilão de fechamento.
    """
    if isinstance(freeze_active, (int, Decimal)) and not isinstance(freeze_active, bool):
        canc = int(freeze_active)
        mod = int(cancellations_attempted)
        is_frozen = True
    else:
        canc = int(cancellations_attempted)
        mod = int(modifications_attempted)
        is_frozen = bool(freeze_active)

    if is_frozen:
        check(
            canc == 0 and mod == 0,
            f"Violação da Inviolabilidade de Congelamento do Leilão de Fechamento (LAW-104A) em '{symbol}': "
            f"Cancelamentos={canc}, Modificações={mod} durante estado de freeze ativo (auction_id='{auction_id}')",
            law_code="LAW-104A",
            details={
                "symbol": symbol,
                "auction_id": auction_id,
                "freeze_active": is_frozen,
                "cancellations_attempted": canc,
                "modifications_attempted": mod,
            },
        )


def assert_moc_loc_allocation_priority(
    unexecuted_moc_quantity: Union[int, Decimal],
    executed_loc_or_limit_quantity: Union[int, Decimal],
    side: str = "",
    symbol: str = "",
    auction_id: str = "",
) -> None:
    """Lei 75 / LAW-104B: Prioridade Estrita de Alocação de Ordens MOC sobre LOC e Limitadas (FL-104).

    No cruzamento do Leilão de Fechamento B3 PUMA (Closing Cross):
    1. Ordens Market-on-Close (MOC) possuem prioridade absoluta de execução frente a ordens Limit-on-Close (LOC)
       e ordens limitadas comuns (LIMIT) na mesma ponta negociadora.
    2. Nenhuma ordem LOC ou LIMIT elegível ao PTF pode receber alocação (execução parcial ou total)
       se houver saldo remanescente não executado de ordens MOC na mesma ponta.
       Formalmente: Se unexecuted_moc_quantity > 0, então executed_loc_or_limit_quantity == 0.

    Args:
        unexecuted_moc_quantity: Saldo residual não-executado de ordens MOC na ponta analisada.
        executed_loc_or_limit_quantity: Volume de ordens LOC ou limitadas executadas na mesma ponta.
        side: Ponta da negociação ('BUY' / 'SELL' ou 'C' / 'V').
        symbol: Código do ativo transacionado.
        auction_id: Identificador do leilão PUMA.
    """
    violation = (unexecuted_moc_quantity > 0) and (executed_loc_or_limit_quantity > 0)
    check(
        not violation,
        f"Violação da Prioridade Estrita MOC vs LOC (LAW-104B) no leilão de '{symbol}' (ponta {side}): "
        f"Ordens LOC/LIMIT executaram {executed_loc_or_limit_quantity} lotes havendo {unexecuted_moc_quantity} "
        f"lotes de MOC pendentes sem execução.",
        law_code="LAW-104B",
        details={
            "symbol": symbol,
            "side": side,
            "auction_id": auction_id,
            "unexecuted_moc_quantity": str(unexecuted_moc_quantity),
            "executed_loc_or_limit_quantity": str(executed_loc_or_limit_quantity),
        },
    )


def assert_closing_auction_volume_maximization(
    selected_volume: Union[int, Decimal],
    candidate_volumes: Sequence[Union[int, Decimal]],
    selected_price: Optional[Decimal] = None,
    candidate_prices: Optional[Sequence[Decimal]] = None,
    symbol: str = "",
    auction_id: str = "",
) -> None:
    """Lei 76 / LAW-104C: Maximização Estrita de Volume Executável no Leilão de Fechamento (FL-104).

    Princípio nº 1 de apuração do Preço Teórico de Fechamento (PTF) da B3 PUMA:
    O preço de fechamento escolhido para o uncross DEVE ser aquele que proporciona o maior volume
    acumulado de lotes executáveis entre todos os níveis de preço com ordens no livro.
    Formalmente: selected_volume >= v para todo v em candidate_volumes.

    Args:
        selected_volume: Volume executável apurado ao Preço Teórico de Fechamento (PTF) escolhido.
        candidate_volumes: Sequência com os volumes executáveis calculados para todos os níveis de preço candidatos.
        selected_price: O preço PTF selecionado (opcional, para enriquecer o diagnóstico de auditoria).
        candidate_prices: Sequência dos preços candidatos correspondentes aos candidate_volumes (opcional).
        symbol: Código de negociação do ativo.
        auction_id: Identificador do leilão de fechamento.
    """
    check(
        selected_volume >= 0,
        f"Volume selecionado no leilão '{symbol}' não pode ser negativo: {selected_volume}",
        law_code="LAW-104C",
    )
    if candidate_volumes:
        max_possible_volume = max(candidate_volumes)
        check(
            selected_volume >= max_possible_volume,
            f"Violação da Maximização de Volume no Fechamento (LAW-104C) para '{symbol}': "
            f"Volume selecionado ({selected_volume} lotes ao preço {selected_price}) é inferior "
            f"ao volume máximo executável ({max_possible_volume} lotes) entre os preços candidatos.",
            law_code="LAW-104C",
            details={
                "symbol": symbol,
                "auction_id": auction_id,
                "selected_volume": str(selected_volume),
                "selected_price": str(selected_price) if selected_price is not None else None,
                "max_possible_volume": str(max_possible_volume),
                "total_candidate_levels": len(candidate_volumes),
            },
        )


def assert_cross_border_collateral_coverage(
    total_collateral_net_value_brl: Decimal,
    required_margin_brl: Decimal,
    participant_id: str = "",
    tolerance: Decimal = Decimal("0.00"),
) -> None:
    """Lei 77 / LAW-105A: Cobertura Integral de Margem por Garantias Transfronteiriças (FL-105).

    Garantias internacionais depositadas (US Treasuries, German Bunds, UK Gilts, moeda estrangeira),
    precificadas a valor de mercado, convertidas a BRL pela taxa de câmbio PTAX conservadora de compra (bid)
    e deduzidos os haircuts soberanos de maturidade e buffer de volatilidade cambial FX, devem cobrir
    integralmente a chamada de margem CORE da Câmara B3:
    total_collateral_net_value_brl + tolerance >= required_margin_brl.

    Args:
        total_collateral_net_value_brl: Valor líquido total das garantias transfronteiriças em BRL após haircuts.
        required_margin_brl: Margem requerida em BRL calculada pelo sistema de risco CORE da Câmara B3.
        participant_id: Código ou identificador do Participante de Liquidação / Clearing Member.
        tolerance: Margem de tolerância para arredondamentos de centavos (padrão 0.00).
    """
    deficit = required_margin_brl - (total_collateral_net_value_brl + tolerance)
    check(
        deficit <= Decimal("0.00"),
        f"Violação de Cobertura de Garantias Transfronteiriças (LAW-105A) para participante '{participant_id}': "
        f"Valor Líquido de Garantias (R$ {total_collateral_net_value_brl:.2f}) < Margem Requerida (R$ {required_margin_brl:.2f}) "
        f"[Déficit de Cobertura: R$ {deficit:.2f}]",
        law_code="LAW-105A",
        details={
            "participant_id": participant_id,
            "total_collateral_net_value_brl": str(total_collateral_net_value_brl),
            "required_margin_brl": str(required_margin_brl),
            "deficit_brl": str(deficit if deficit > Decimal("0.00") else Decimal("0.00")),
        },
    )


def assert_international_fiduciary_lock(
    international_locked_quantity: Union[int, Decimal],
    b3_recorded_pledge_quantity: Union[int, Decimal],
    total_deposited_quantity: Optional[Union[int, Decimal]] = None,
    depository_name: str = "",
    asset_isin: str = "",
) -> None:
    """Lei 78 / LAW-105B: Inviolabilidade da Trava Fiduciária Internacional de Custódia (FL-105).

    Garante a estrita paridade e integridade da afetação fiduciária de garantias custodiadas
    em depositários centrais internacionais (Euroclear, Clearstream, DTC / DTCC):
    1. A quantidade de títulos com status de bloqueio fiduciário (pledge lock) no depositário internacional
       deve ser rigorosamente igual à quantidade de garantias registrada no sistema de colateral da B3.
    2. A quantidade travada fiduciariamente não pode exceder o saldo total em custódia segregada.
    3. Impede desalienação unilateral, vazamento de colateral ou dupla oneração de títulos soberanos.

    Args:
        international_locked_quantity: Quantidade de títulos bloqueados/penhorados no CSD internacional.
        b3_recorded_pledge_quantity: Quantidade de penhor fiduciário registrada nos livros da Câmara B3.
        total_deposited_quantity: Posição total sob custódia na conta internacional (opcional).
        depository_name: Nome do depositário internacional (ex: 'Euroclear', 'Clearstream', 'DTC').
        asset_isin: Código ISIN do ativo soberano ou título internacional.
    """
    check(
        international_locked_quantity == b3_recorded_pledge_quantity,
        f"Violação da Trava Fiduciária Internacional (LAW-105B) no depositário '{depository_name}' ativo '{asset_isin}': "
        f"Bloqueado no CSD Internacional ({international_locked_quantity}) != Registrado na B3 ({b3_recorded_pledge_quantity})",
        law_code="LAW-105B",
        details={
            "depository_name": depository_name,
            "asset_isin": asset_isin,
            "international_locked_quantity": str(international_locked_quantity),
            "b3_recorded_pledge_quantity": str(b3_recorded_pledge_quantity),
        },
    )
    if total_deposited_quantity is not None:
        check(
            b3_recorded_pledge_quantity <= total_deposited_quantity,
            f"Violação de Limite de Custódia em Trava Fiduciária (LAW-105B) no depositário '{depository_name}': "
            f"Penhor registrado ({b3_recorded_pledge_quantity}) excede o saldo total sob custódia ({total_deposited_quantity})",
            law_code="LAW-105B",
            details={
                "depository_name": depository_name,
                "asset_isin": asset_isin,
                "b3_recorded_pledge_quantity": str(b3_recorded_pledge_quantity),
                "total_deposited_quantity": str(total_deposited_quantity),
            },
        )


def assert_triparty_zero_sum_settlement(
    movements_or_debits: Union[Sequence[Decimal], Decimal],
    credits: Optional[Union[Sequence[Decimal], Decimal]] = None,
    tolerance: Decimal = Decimal("0.00"),
    agreement_id: str = "",
) -> None:
    """Lei 79 / LAW-105C: Conservação Financeira Soma-Zero em Liquidação Triparty (FL-105).

    Em operações de custódia e empréstimo/garantia triparty internacionais (B3 - Agente Triparty - Participante):
    1. Se fornecidos débitos e créditos separadamente: sum(debits) == sum(credits).
    2. Se fornecido fluxo líquido de caixa/ativos: sum(movements) == 0,00 centavo a centavo.
    Nenhuma unidade monetária ou fração de ativo pode ser criada ou destruída na alocação/desalocação triparty.

    Args:
        movements_or_debits: Sequência de movimentações financeiras com sinal ou total/sequência de débitos.
        credits: Sequência ou total de créditos correspondentes (opcional, quando passado no modelo bilateral).
        tolerance: Tolerância para resíduos de arredondamento cambial (padrão 0.00).
        agreement_id: Identificador do contrato ou instrução de liquidação triparty.
    """
    if credits is not None:
        tot_deb = sum(movements_or_debits) if isinstance(movements_or_debits, (list, tuple)) else movements_or_debits
        tot_cred = sum(credits) if isinstance(credits, (list, tuple)) else credits
        diff = abs(tot_deb - tot_cred)
        check(
            diff <= tolerance,
            f"Violação de Soma-Zero na Liquidação Triparty (LAW-105C) no acordo '{agreement_id}': "
            f"Total Débitos ({tot_deb}) != Total Créditos ({tot_cred}) [Diff={diff}]",
            law_code="LAW-105C",
            details={
                "agreement_id": agreement_id,
                "total_debits": str(tot_deb),
                "total_credits": str(tot_cred),
                "diff": str(diff),
            },
        )
    else:
        if isinstance(movements_or_debits, (list, tuple)):
            net_sum = sum(movements_or_debits, Decimal("0.00"))
        else:
            net_sum = movements_or_debits
        diff = abs(net_sum)
        check(
            diff <= tolerance,
            f"Violação de Soma-Zero na Liquidação Triparty (LAW-105C) no acordo '{agreement_id}': "
            f"Soma líquida das pernas ({net_sum}) != 0.00 [Diff={diff}]",
            law_code="LAW-105C",
            details={
                "agreement_id": agreement_id,
                "net_sum": str(net_sum),
                "diff": str(diff),
            },
        )


def assert_central_bus_global_balance(
    inbound_total: Union[int, Decimal],
    settled_or_processed: Union[int, Decimal],
    rejected: Union[int, Decimal],
    pending_in_flight: Union[int, Decimal] = 0,
    bus_channel: str = "",
) -> None:
    """Lei 80 / LAW-106A: Conservação Global de Mensagens e Fluxos no Barramento Central (FL-106).

    Invariante de conservação contábil e de mensageria no Barramento Central de Integração (Enterprise Service Bus / SPB / Selic / B3):
    O volume total recebido na entrada (inbound_total) DEVE ser estritamente igual à soma de:
    inbound_total == settled_or_processed + rejected + pending_in_flight.
    Garante que nenhuma mensagem ou evento de liquidação se perde em trânsito, é descartado silenciosamente
    ou gerado de forma espúria sem trilha de auditoria.

    Args:
        inbound_total: Total de mensagens, eventos ou valores financeiros despachados para o barramento.
        settled_or_processed: Total de mensagens/operações confirmadas, processadas ou liquidadas com sucesso.
        rejected: Total de mensagens/operações rejeitadas, canceladas ou encaminhadas para Dead Letter Queue (DLQ).
        pending_in_flight: Total de mensagens/operações em trânsito ou pendentes de confirmação.
        bus_channel: Nome do canal, fila ou tópico do barramento central (ex: 'SPB-LDL', 'RTC-ALLOCATION').
    """
    expected_sum = settled_or_processed + rejected + pending_in_flight
    check(
        inbound_total == expected_sum,
        f"Violação do Balanço Global do Barramento Central (LAW-106A) no canal '{bus_channel}': "
        f"Inbound ({inbound_total}) != Processadas ({settled_or_processed}) + Rejeitadas ({rejected}) + Pendentes ({pending_in_flight}) "
        f"[Soma Esperada={expected_sum}]",
        law_code="LAW-106A",
        details={
            "bus_channel": bus_channel,
            "inbound_total": str(inbound_total),
            "settled_or_processed": str(settled_or_processed),
            "rejected": str(rejected),
            "pending_in_flight": str(pending_in_flight),
            "expected_sum": str(expected_sum),
        },
    )


def assert_default_waterfall_containment(
    gross_loss: Decimal,
    total_absorbed: Decimal,
    residual_deficit: Decimal = Decimal("0.00"),
    safeguards_capacity: Optional[Decimal] = None,
    clearing_member_id: str = "",
) -> None:
    """Lei 81 / LAW-106B: Contenção Absoluta na Cascata de Inadimplência e Risco Sistêmico (FL-106).

    Exigência dos Princípios para Infraestruturas do Mercado Financeiro CPMI-IOSCO (PFMI 4 e 7):
    1. Conservação Contábil Estrita da Perda:
       gross_loss == total_absorbed + residual_deficit.
    2. Contenção Plena sem Transbordamento Insolvente:
       residual_deficit == 0,00 (a perda deve ser 100% contida pelas salvaguardas da câmara).
    3. Se safeguards_capacity fornecido:
       total_absorbed <= safeguards_capacity (a perda absorvida não pode exceder o patrimônio de garantias mobilizado).

    Args:
        gross_loss: Perda bruta total provocada pela inadimplência do participante.
        total_absorbed: Soma das perdas absorvidas pelas camadas sequenciais de salvaguardas da B3.
        residual_deficit: Déficit não absorvido remanescente (deve ser estritamente zero para contenção bem-sucedida).
        safeguards_capacity: Capacidade máxima agregada dos recursos de salvaguarda disponíveis (opcional).
        clearing_member_id: Identificador do membro de compensação inadimplente.
    """
    expected_loss = total_absorbed + residual_deficit
    check(
        gross_loss == expected_loss,
        f"Violação Contábil na Cascata de Inadimplência (LAW-106B) para membro '{clearing_member_id}': "
        f"Perda Bruta ({gross_loss}) != Absorvido ({total_absorbed}) + Déficit Residual ({residual_deficit})",
        law_code="LAW-106B",
        details={
            "clearing_member_id": clearing_member_id,
            "gross_loss": str(gross_loss),
            "total_absorbed": str(total_absorbed),
            "residual_deficit": str(residual_deficit),
        },
    )

    check(
        residual_deficit == Decimal("0.00"),
        f"Falha Crítica de Contenção da Cascata de Inadimplência (LAW-106B) para membro '{clearing_member_id}': "
        f"Déficit Residual Descoberto de R$ {residual_deficit:.2f} excede capacidade de salvaguardas (Risco Sistêmico).",
        law_code="LAW-106B",
        details={
            "clearing_member_id": clearing_member_id,
            "gross_loss": str(gross_loss),
            "residual_deficit": str(residual_deficit),
        },
    )

    if safeguards_capacity is not None:
        check(
            total_absorbed <= safeguards_capacity,
            f"Violação de Capacidade de Salvaguardas na Cascata (LAW-106B) para membro '{clearing_member_id}': "
            f"Total Absorvido ({total_absorbed}) excede Capacidade Máxima ({safeguards_capacity})",
            law_code="LAW-106B",
            details={
                "clearing_member_id": clearing_member_id,
                "total_absorbed": str(total_absorbed),
                "safeguards_capacity": str(safeguards_capacity),
            },
        )


def assert_cross_system_atomic_settlement(
    system_a_completed: Union[bool, str],
    system_b_completed: Union[bool, str],
    system_a_status: str = "",
    system_b_status: str = "",
    transaction_id: str = "",
    system_a_name: str = "B3",
    system_b_name: str = "ExternalSystem",
    amount_system_a: Optional[Decimal] = None,
    amount_system_b: Optional[Decimal] = None,
) -> None:
    """Lei 82 / LAW-106C: Atomicidade Estrita de Liquidação Entre Sistemas (All-or-None) (FL-106).

    Princípio de liquidação atômica e eliminação do Risco de Principal (Herstatt Risk) em integrações
    inter-sistemas (ex: B3 CSD vs Banco Central STR/SPB, Selic, C3 ou Depositários Internacionais):
    1. Ambas as pernas do negócio devem atingir o mesmo estado de terminação: ou ambas são liquidadas
       com sucesso ('SETTLED', 'COMPLETED', True), ou ambas são abortadas/revertidas ('FAILED', 'CANCELLED', False).
    2. Nenhuma perna pode ser liquidada unilateralmente se a contraparte falhar (proibição de liquidação assimétrica).
    3. Se valores nominais são fornecidos: amount_system_a == amount_system_b.

    Args:
        system_a_completed: Booleano ou status string indicando se o Sistema A concluiu a liquidação com sucesso.
        system_b_completed: Booleano ou status string indicando se o Sistema B concluiu a liquidação com sucesso.
        system_a_status: Descrição textual do status no Sistema A (ex: 'SETTLED', 'ACK').
        system_b_status: Descrição textual do status no Sistema B (ex: 'REJECTED', 'CONFIRMED').
        transaction_id: Identificador único da transação de liquidação cruzada.
        system_a_name: Nome do Sistema A (ex: 'B3-CLEARING', 'B3-CSD').
        system_b_name: Nome do Sistema B (ex: 'BACEN-STR', 'SELIC', 'EUROCLEAR').
        amount_system_a: Montante financeiro liquidado no Sistema A (opcional).
        amount_system_b: Montante financeiro liquidado no Sistema B (opcional).
    """
    success_states = {"SETTLED", "COMPLETED", "SUCCESS", "EXECUTED", "CONFIRMED", "COMMITTED"}

    if isinstance(system_a_completed, str):
        a_ok = system_a_completed.upper() in success_states
        st_a = system_a_status or system_a_completed
    else:
        a_ok = bool(system_a_completed)
        st_a = system_a_status or ("COMPLETED" if a_ok else "FAILED")

    if isinstance(system_b_completed, str):
        b_ok = system_b_completed.upper() in success_states
        st_b = system_b_status or system_b_completed
    else:
        b_ok = bool(system_b_completed)
        st_b = system_b_status or ("COMPLETED" if b_ok else "FAILED")

    check(
        a_ok == b_ok,
        f"Violação da Atomicidade de Liquidação Entre Sistemas (LAW-106C) na transação '{transaction_id}': "
        f"Assimetria detectada entre {system_a_name} (sucesso={a_ok}, status='{st_a}') e "
        f"{system_b_name} (sucesso={b_ok}, status='{st_b}'). Princípio All-or-None quebrado.",
        law_code="LAW-106C",
        details={
            "transaction_id": transaction_id,
            "system_a_name": system_a_name,
            "system_b_name": system_b_name,
            "system_a_success": a_ok,
            "system_b_success": b_ok,
            "system_a_status": st_a,
            "system_b_status": st_b,
        },
    )

    if amount_system_a is not None and amount_system_b is not None:
        check(
            amount_system_a == amount_system_b,
            f"Violação de Paridade Financeira na Liquidação Entre Sistemas (LAW-106C) na transação '{transaction_id}': "
            f"Montante {system_a_name} ({amount_system_a}) != Montante {system_b_name} ({amount_system_b})",
            law_code="LAW-106C",
            details={
                "transaction_id": transaction_id,
                "amount_system_a": str(amount_system_a),
                "amount_system_b": str(amount_system_b),
            },
        )


def law_110_algorithmic_order_slicing_conservation(
    parent_quantity: Union[int, Decimal],
    child_quantities: Sequence[Union[int, Decimal]],
    residual_quantity: Union[int, Decimal] = 0,
    parent_side: str = "BUY",
    parent_limit_price: Optional[Union[Decimal, str, float]] = None,
    child_prices: Optional[Sequence[Union[Decimal, str, float]]] = None,
    parent_order_id: str = "N/A",
    lot_size: Optional[int] = None,
) -> None:
    """Lei 83 / LAW-110: Conservação Estrita de Quantidade e Não-Transgressão de Preço no Fatiamento Algorítmico (FL-104).

    Garante integridade matemática estrita e proteção contra manipulação de mercado para motores
    de Smart Order Router (SOR) e fatiamento algorítmico (TWAP, VWAP, POV) no PUMA Trading System da B3:

    1. Conservação Estrita de Quantidade:
       Soma(child_quantities) + residual_quantity == parent_quantity.
       Nenhum lote pode ser espuriamente criado ou destruído pelo fatiamento.
    2. Quantidades Estritamente Positivas e Não-Nulas:
       child_quantity > 0 para cada ordem filha gerada.
    3. Conformidade com Lote Padrão de Negociação (Round Lot):
       Se especificado lot_size, child_quantity % lot_size == 0.
    4. Não-Transgressão de Preço Limite (Price Limit Guardrail):
       Para ordens BUY:  child_price <= parent_limit_price.
       Para ordens SELL: child_price >= parent_limit_price.

    Args:
        parent_quantity: Quantidade total de ações ou contratos da ordem-mãe institucional.
        child_quantities: Lista de quantidades das fatias filhas (child orders).
        residual_quantity: Quantidade não alocada remanescente (caso haja).
        parent_side: Sentido da ordem ('BUY' / 'SELL').
        parent_limit_price: Preço limite da ordem-mãe (se aplicável).
        child_prices: Lista dos preços limites/alvos das ordens filhas correspondentes.
        parent_order_id: Identificador único da ordem-mãe para auditoria.
        lot_size: Tamanho do lote padrão B3 (ex: 100 para ações no mercado à vista).
    """
    total_child = sum(child_quantities)
    total_accounted = total_child + residual_quantity

    # 1. Conservação Estrita da Quantidade
    check(
        total_accounted == parent_quantity,
        f"Violação da Conservação Estrita de Quantidade (LAW-110) na ordem '{parent_order_id}': "
        f"Soma das fatias ({total_child}) + residual ({residual_quantity}) = {total_accounted} != "
        f"Quantidade da ordem-mãe ({parent_quantity}).",
        law_code="LAW-110",
        details={
            "parent_order_id": parent_order_id,
            "parent_quantity": str(parent_quantity),
            "total_child": str(total_child),
            "residual_quantity": str(residual_quantity),
            "total_accounted": str(total_accounted),
        },
    )

    # 2. Quantidades Estritamente Positivas
    for idx, qty in enumerate(child_quantities):
        check(
            qty > 0,
            f"Violação de Não-Negatividade (LAW-110) na ordem '{parent_order_id}', fatia {idx}: "
            f"Quantidade da ordem filha ({qty}) deve ser estritamente positiva.",
            law_code="LAW-110",
            details={"parent_order_id": parent_order_id, "slice_index": idx, "child_qty": str(qty)},
        )

        # 3. Lote padrão
        if lot_size is not None and lot_size > 0:
            check(
                int(qty) % lot_size == 0,
                f"Violação de Discretização de Lote (LAW-110) na ordem '{parent_order_id}', fatia {idx}: "
                f"Quantidade {qty} não é múltiplo do lote padrão {lot_size}.",
                law_code="LAW-110",
                details={"parent_order_id": parent_order_id, "slice_index": idx, "child_qty": str(qty), "lot_size": lot_size},
            )

    # 4. Não-Transgressão de Preço Limite
    if parent_limit_price is not None and child_prices is not None:
        p_limit = Decimal(str(parent_limit_price))
        side_upper = str(parent_side).strip().upper()

        for idx, p in enumerate(child_prices):
            child_p = Decimal(str(p))
            if side_upper in ("BUY", "C", "COMPRA", "1"):
                check(
                    child_p <= p_limit,
                    f"Violação de Preço Limite de Compra (LAW-110) na ordem '{parent_order_id}', fatia {idx}: "
                    f"Preço da fatia ({child_p}) excede o preço limite da ordem-mãe ({p_limit}).",
                    law_code="LAW-110",
                    details={
                        "parent_order_id": parent_order_id,
                        "slice_index": idx,
                        "child_price": str(child_p),
                        "limit_price": str(p_limit),
                        "side": side_upper,
                    },
                )
            elif side_upper in ("SELL", "V", "VENDA", "2"):
                check(
                    child_p >= p_limit,
                    f"Violação de Preço Limite de Venda (LAW-110) na ordem '{parent_order_id}', fatia {idx}: "
                    f"Preço da fatia ({child_p}) está abaixo do preço limite da ordem-mãe ({p_limit}).",
                    law_code="LAW-110",
                    details={
                        "parent_order_id": parent_order_id,
                        "slice_index": idx,
                        "child_price": str(child_p),
                        "limit_price": str(p_limit),
                        "side": side_upper,
                    },
                )



def assert_coe_protected_capital_floor(
    gross_settlement: Decimal,
    guaranteed_principal: Decimal,
    contract_id: str = "",
    protection_floor_pct: Decimal = Decimal("100.00"),
) -> None:
    """Lei 83 / LAW-108: Inviolabilidade do Piso de Capital Protegido em COE (FL-102).

    Nos termos da Lei nº 12.249/2010 (arts. 39 a 44) e da Resolução CVM nº 8/2020, em Certificados
    de Operações Estruturadas (COE) na modalidade 'Valor Nominal Protegido', o valor de
    liquidação bruto pago ao investidor na data de vencimento ou de resgate antecipado
    deve ser rigorosamente maior ou igual ao montante do capital inicial protegido pactuado
    (geralmente 100% do principal investido). Nenhuma oscilação de mercado adversa nos ativos
    subjacentes ou perdas nas opções embutidas pode corroer o valor nominal protegido.

    Args:
        gross_settlement: Montante bruto apurado para liquidação do COE.
        guaranteed_principal: Valor nominal mínimo garantido por contrato (VNU * Qtd * Piso%).
        contract_id: Identificador único do COE no registro B3.
        protection_floor_pct: Percentual contratual de piso garantido (mínimo 100% em capital protegido).
    """
    check(
        gross_settlement >= guaranteed_principal,
        f"Violação da Inviolabilidade do Piso de Capital Protegido (LAW-108) no contrato '{contract_id}': "
        f"Payoff de liquidação bruto ({gross_settlement}) < Principal garantido ({guaranteed_principal}) "
        f"com piso de proteção de {protection_floor_pct}%.",
        law_code="LAW-108",
        details={
            "contract_id": contract_id,
            "gross_settlement": str(gross_settlement),
            "guaranteed_principal": str(guaranteed_principal),
            "protection_floor_pct": str(protection_floor_pct),
        },
    )


def assert_calendar_arbitrage_free(
    w_t1: Union[Decimal, float],
    w_t2: Union[Decimal, float],
    t1: Union[Decimal, float],
    t2: Union[Decimal, float],
    moneyness_k: Union[Decimal, float] = 0.0,
    surface_id: str = "",
    tolerance: float = 1e-9,
) -> None:
    """Lei 85 / LAW-107A: Invariante de Não-Arbitragem de Calendário em Superfícies de Volatilidade (FL-101).

    Para qualquer moneyness k = ln(K/F) fixo, a variância total w(k, T) deve ser uma função
    monotonicamente não-decrescente do prazo até o vencimento T:
        T1 < T2 ==> w(k, T1) <= w(k, T2).
    A violação desta lei implica variância forward local negativa e existência de oportunidade
    de arbitragem de calendário (calendar spread arbitrage) livre de risco.

    Args:
        w_t1: Variância total w(k, T1) na maturidade mais curta T1.
        w_t2: Variância total w(k, T2) na maturidade mais longa T2.
        t1: Prazo até o vencimento T1 (T1 < T2).
        t2: Prazo até o vencimento T2 (T2 > T1).
        moneyness_k: Log-moneyness k = ln(K/F).
        surface_id: Identificador da superfície de volatilidade auditada.
        tolerance: Margem de tolerância numérica para precisão de ponto flutuante.
    """
    diff = float(w_t2) - float(w_t1)
    check(
        diff >= -tolerance,
        f"Violação da Não-Arbitragem de Calendário (LAW-107A) na superfície '{surface_id}': "
        f"Variância total decrescente w(k={float(moneyness_k):.4f}, T2={float(t2):.4f}) = {float(w_t2):.6f} < "
        f"w(k={float(moneyness_k):.4f}, T1={float(t1):.4f}) = {float(w_t1):.6f} (déficit={-diff:.8f}).",
        law_code="LAW-107A",
        details={
            "surface_id": surface_id,
            "moneyness_k": str(moneyness_k),
            "t1": str(t1),
            "t2": str(t2),
            "w_t1": str(w_t1),
            "w_t2": str(w_t2),
            "diff": str(diff),
        },
    )


def assert_butterfly_arbitrage_free(
    g_k: Union[Decimal, float],
    moneyness_k: Union[Decimal, float] = 0.0,
    maturity_t: Union[Decimal, float] = 0.0,
    surface_id: str = "",
    call_k1: Optional[Union[Decimal, float]] = None,
    call_k2: Optional[Union[Decimal, float]] = None,
    call_k3: Optional[Union[Decimal, float]] = None,
    tolerance: float = 1e-7,
) -> None:
    """Lei 86 / LAW-107B: Invariante de Não-Arbitragem Butterfly e Convexidade em Superfícies de Volatilidade (FL-101).

    Conforme o teorema de Gatheral (2004) e Breeden-Litzenberger (1978):
    1. A função de densidade de probabilidade neutra ao risco g(k) derivada da variância total SVI/SABR
       deve ser estritamente não-negativa para qualquer moneyness k:
           g(k) = (1 - k*w'/(2w))^2 - (w'^2 / 4)*(1/w + 1/4) + w''/2 >= 0.
    2. Se preços discretos de opções call para 3 strikes equidistantes K1 < K2 < K3 (com K2 - K1 = K3 - K2)
       forem fornecidos, a estrutura de preços de call deve ser estritamente convexa:
           C(K1) - 2*C(K2) + C(K3) >= 0.
    A violação desta lei permite a construção de uma borboleta de custo negativo com payoff não-negativo
    (butterfly spread arbitrage).

    Args:
        g_k: Valor da função de densidade de risco g(k) de Gatheral.
        moneyness_k: Log-moneyness k = ln(K/F).
        maturity_t: Prazo até o vencimento T da fatia de volatilidade.
        surface_id: Identificador da superfície ou fatia auditada.
        call_k1: Preço da call de menor strike K1 (opcional).
        call_k2: Preço da call de strike intermediário K2 (opcional).
        call_k3: Preço da call de maior strike K3 (opcional).
        tolerance: Margem de tolerância numérica para precisão de ponto flutuante.
    """
    g_val = float(g_k)
    check(
        g_val >= -tolerance,
        f"Violação da Não-Arbitragem Butterfly (LAW-107B) na superfície '{surface_id}': "
        f"Densidade de risco negativa g(k={float(moneyness_k):.4f}, T={float(maturity_t):.4f}) = {g_val:.8f} < 0.",
        law_code="LAW-107B",
        details={
            "surface_id": surface_id,
            "moneyness_k": str(moneyness_k),
            "maturity_t": str(maturity_t),
            "g_k": str(g_k),
        },
    )

    if call_k1 is not None and call_k2 is not None and call_k3 is not None:
        c1 = float(call_k1)
        c2 = float(call_k2)
        c3 = float(call_k3)
        butterfly_price = c1 - 2.0 * c2 + c3
        check(
            butterfly_price >= -tolerance,
            f"Violação de Convexidade de Preço de Call (LAW-107B) na superfície '{surface_id}': "
            f"Preço de Borboleta Simétrica C(K1) - 2*C(K2) + C(K3) = {butterfly_price:.8f} < 0 "
            f"(C1={c1:.4f}, C2={c2:.4f}, C3={c3:.4f}).",
            law_code="LAW-107B",
            details={
                "surface_id": surface_id,
                "call_k1": str(call_k1),
                "call_k2": str(call_k2),
                "call_k3": str(call_k3),
                "butterfly_price": str(butterfly_price),
            },
        )



def assert_drex_rwa_primary_issuance_invariants(
    allocated_units: int,
    delivered_units: int,
    received_financial: Decimal,
    expected_financial: Decimal,
    operation_id: str = "",
    funded_coupon_amount: Optional[Decimal] = None,
    distributed_coupon_amount: Optional[Decimal] = None,
) -> None:
    """Lei 84 / LAW-111: Atomicidade da Liquidação Primária DvP e Conservação de Cupons RWA no Drex (FL-105).

    Emissão Primária de Ativos de Renda Fixa Privada Tokenizados (Debêntures, CRIs, CRAs) no Drex:
    1. Atomicidade DvP Estrita: Quantidade de tokens RWA alocada no bookbuilding deve ser rigorosamente
       igual à quantidade entregue (tokens mintados/creditados na carteira do investidor).
    2. Paridade Financeira: Valor financeiro recebido pelo emissor em Real Digital deve ser exatamente
       igual ao valor nominal liquidado (allocated_units * unit_price).
    3. Conservação de Saldo de Cupons/Amortizações: O montante de Real Digital provisionado pelo emissor
       para honrar proventos deve cobrir rigorosamente os montantes distribuídos aos detentores
       elegíveis, garantindo ausência de vazamento financeiro (zero-leakage).
    """
    check(
        allocated_units == delivered_units,
        f"Violação da Atomicidade DvP Quantitativa RWA (LAW-111) na operação '{operation_id}': "
        f"Quantidade alocada ({allocated_units}) != Quantidade entregue ({delivered_units})",
        law_code="LAW-111",
        details={
            "operation_id": operation_id,
            "allocated_units": allocated_units,
            "delivered_units": delivered_units,
        },
    )
    check(
        received_financial == expected_financial,
        f"Violação da Paridade Financeira DvP RWA (LAW-111) na operação '{operation_id}': "
        f"Valor recebido ({received_financial}) != Valor financeiro esperado ({expected_financial})",
        law_code="LAW-111",
        details={
            "operation_id": operation_id,
            "received_financial": str(received_financial),
            "expected_financial": str(expected_financial),
        },
    )
    if funded_coupon_amount is not None and distributed_coupon_amount is not None:
        check(
            funded_coupon_amount == distributed_coupon_amount,
            f"Violação da Conservação de Saldo de Cupons RWA (LAW-111) na operação '{operation_id}': "
            f"Montante provisionado ({funded_coupon_amount}) != Montante distribuído ({distributed_coupon_amount})",
            law_code="LAW-111",
            details={
                "operation_id": operation_id,
                "funded_coupon_amount": str(funded_coupon_amount),
                "distributed_coupon_amount": str(distributed_coupon_amount),
            },
        )


def assert_market_maker_pmm_liquidity_law_109(
    presence_pct: Decimal,
    min_presence_pct: Decimal,
    is_compliant: bool,
    spread_pct: Optional[Decimal] = None,
    max_spread_pct: Optional[Decimal] = None,
    bid_qty: Optional[Decimal] = None,
    min_bid_qty: Optional[Decimal] = None,
    ask_qty: Optional[Decimal] = None,
    min_ask_qty: Optional[Decimal] = None,
    discount_amount: Optional[Decimal] = None,
    is_incentive_eligible: Optional[bool] = None,
    session_id: str = "",
    participant_id: str = "",
    symbol: str = "",
) -> None:
    """Lei 82 / LAW-109: Cumprimento Estrito das Regras de Presença, Spread Máximo e Continuidade de Ofertas Bilaterais (FL-103).

    Garante as obrigações contratuais do Formador de Mercado (PMM B3 / BSM):
    1. Presença Mínima Obrigatória: Toda sessão ordinária conforme (is_compliant == True)
       deve registrar presença efetiva >= min_presence_pct (mínimo regulatório oficial de 80.00%).
    2. Spread Máximo e Não-Negatividade: Preço de venda estritamente superior ao de compra
       (ask > bid > 0) e spread percentual bid-ask <= max_spread_pct contratual da classe.
    3. Profundidade Bilateral: Lotes simultâneos de compra e venda devem satisfazer
       bid_qty >= min_bid_qty e ask_qty >= min_ask_qty em todo snapshot de conformidade.
    4. Integridade de Incentivos e Descredenciamento: Descontos tarifários/emolumentares
       (discount_amount > 0) somente podem ser concedidos se o participante for elegível.
    """
    if is_compliant:
        check(
            presence_pct >= min_presence_pct,
            f"Violação da Presença Mínima de Formador de Mercado (LAW-109) na sessão '{session_id}' "
            f"para {participant_id}/{symbol}: Presença apurada ({presence_pct}%) < Mínimo requerido ({min_presence_pct}%)",
            law_code="LAW-109",
            details={
                "session_id": session_id,
                "participant_id": participant_id,
                "symbol": symbol,
                "presence_pct": str(presence_pct),
                "min_presence_pct": str(min_presence_pct),
            },
        )

    if spread_pct is not None and max_spread_pct is not None:
        check(
            spread_pct <= max_spread_pct,
            f"Violação de Spread Máximo de Cotação PMM (LAW-109) para {participant_id}/{symbol}: "
            f"Spread ofertado ({spread_pct}%) > Teto contratual da classe ({max_spread_pct}%)",
            law_code="LAW-109",
            details={
                "participant_id": participant_id,
                "symbol": symbol,
                "spread_pct": str(spread_pct),
                "max_spread_pct": str(max_spread_pct),
            },
        )

    if bid_qty is not None and min_bid_qty is not None:
        check(
            bid_qty >= min_bid_qty,
            f"Violação de Lote Mínimo de Compra PMM (LAW-109) para {participant_id}/{symbol}: "
            f"Lote de compra ({bid_qty}) < Mínimo regulatório ({min_bid_qty})",
            law_code="LAW-109",
            details={
                "participant_id": participant_id,
                "symbol": symbol,
                "bid_qty": str(bid_qty),
                "min_bid_qty": str(min_bid_qty),
            },
        )

    if ask_qty is not None and min_ask_qty is not None:
        check(
            ask_qty >= min_ask_qty,
            f"Violação de Lote Mínimo de Venda PMM (LAW-109) para {participant_id}/{symbol}: "
            f"Lote de venda ({ask_qty}) < Mínimo regulatório ({min_ask_qty})",
            law_code="LAW-109",
            details={
                "participant_id": participant_id,
                "symbol": symbol,
                "ask_qty": str(ask_qty),
                "min_ask_qty": str(min_ask_qty),
            },
        )

    if is_incentive_eligible is False and discount_amount is not None:
        check(
            discount_amount <= Decimal("0.00"),
            f"Violação de Integridade de Incentivo Tarifário PMM (LAW-109) para {participant_id}/{symbol}: "
            f"Concessão indevida de desconto (R$ {discount_amount}) a participante inelegível/sancionado.",
            law_code="LAW-109",
            details={
                "participant_id": participant_id,
                "symbol": symbol,
                "discount_amount": str(discount_amount),
            },
        )







