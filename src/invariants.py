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



