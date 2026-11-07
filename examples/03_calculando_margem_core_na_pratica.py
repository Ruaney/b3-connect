#!/usr/bin/env python3
"""Exemplo 03: Como calcular Margem de Risco CORE e Salvaguardas na Câmara B3.

Demonstra:
  1. Avaliação de risco de carteira multiativo (CORE Closeout Risk Evaluation).
  2. Aplicação de cenários de estresse de preços (Price Shock) e volatilidade.
  3. Haircuts em garantias elegíveis (Títulos Públicos vs Ações).
  4. Verificação de suficiência de colateral (LAW-105A).

100% Python Standard Library.
"""

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.invariants import assert_cross_border_collateral_coverage


def main() -> None:
    print("=" * 80)
    print(" EXEMPLO 03: METODOLOGIA DE RISCO CORE E MARGENS NA CÂMARA B3")
    print("=" * 80)

    # 1. Posições Abertas da Carteira
    portfolio_equity_notional = Decimal("1_000_000.00")  # Posição comprada em ações
    portfolio_future_notional = Decimal("500_000.00")    # Posição vendida em contratos futuros

    print(f"\n[1] Posições Abertas do Participante de Compensação:")
    print(f"    - Ações Listadas (Delta Comprado): R$ {portfolio_equity_notional:,.2f}")
    print(f"    - Futuros BM&F (Delta Vendido):    R$ {portfolio_future_notional:,.2f}")

    # 2. Cenários de Estresse CORE (Price Shock & Volatility Shock)
    equity_shock_pct = Decimal("0.12")   # Estresse de -12%
    future_shock_pct = Decimal("0.08")   # Estresse de +8%

    loss_equity = portfolio_equity_notional * equity_shock_pct
    loss_future = portfolio_future_notional * future_shock_pct
    correlation_offset = Decimal("0.20")  # Benefício de diversificação/hedge (20%)

    raw_risk = loss_equity + loss_future
    core_margin_required = (raw_risk * (Decimal("1.00") - correlation_offset)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    print(f"\n[2] Cálculo de Risco Pior Cenário (CORE):")
    print(f"    - Perda Potencial Ações (-12%):    R$ {loss_equity:,.2f}")
    print(f"    - Perda Potencial Futuros (+8%):   R$ {loss_future:,.2f}")
    print(f"    - Offset de Correlação / Hedge:    20.0%")
    print(f"    - Margem Exigida Líquida (CORE):   R$ {core_margin_required:,.2f}")

    # 3. Colaterais Depositados e Haircuts Prudenciais
    tpft_deposit = Decimal("120_000.00")  # Tesouro Selic (Haircut 2%)
    cash_deposit = Decimal("50_000.00")   # Moeda Soberana BRL (Haircut 0%)

    tpft_eligible = tpft_deposit * (Decimal("1.00") - Decimal("0.02"))
    cash_eligible = cash_deposit * Decimal("1.00")
    total_collateral_eligible = (tpft_eligible + cash_eligible).quantize(Decimal("0.01"))

    print(f"\n[3] Garantias Depositadas e Haircuts da Câmara:")
    print(f"    - Moeda BRL (Haircut 0%):          R$ {cash_eligible:,.2f}")
    print(f"    - Título Público (Haircut 2%):     R$ {tpft_eligible:,.2f}")
    print(f"    - Total de Garantias Elegíveis:    R$ {total_collateral_eligible:,.2f}")

    # 4. Verificação Formal de Suficiência (LAW-105A)
    print(f"\n[4] Auditoria de Salvaguardas da Câmara (LAW-105A):")
    assert_cross_border_collateral_coverage(
        total_collateral_net_value_brl=total_collateral_eligible,
        required_margin_brl=core_margin_required,
    )
    surplus = total_collateral_eligible - core_margin_required
    print(f"    ✓ Colateral Suficiente! Excedente de Margem: R$ {surplus:,.2f}")
    print(f"    - Status do Membro:               ADIMPLENTE & LIBERADO PARA OPERAR")

    print("\n✓ Cálculo de margem CORE homologado com sucesso.")
    print("=" * 80)


if __name__ == "__main__":
    main()
