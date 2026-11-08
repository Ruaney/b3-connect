#!/usr/bin/env python3
"""Lab 02: Conciliação Automática de Custódia B3 (IMBARQ006) e Detecção da LAW-008.

Demonstra na prática:
1. Verificação da Equação Fundamental de Conservação de Custódia (LAW-008):
   Total = Disponível + Garantia + Bloqueio Judicial (`src.c_abi_compat.CustodyPositionStruct`).
2. Conciliação automática entre a custódia interna da instituição e o extrato
   oficial da Central Depositária B3 (`IMBARQ006`) usando `src.reconciliation.PositionReconciler`.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.c_abi_compat import CustodyPositionStruct
from src.imbarq006 import PositionDetailRecord
from src.reconciliation import InternalPosition, PositionReconciler


def run_lab() -> dict[str, object]:
    """Executa o Lab 02 de conciliação IMBARQ006 e retorna o diagnóstico."""
    # 1. Verificação da Equação de Conservação de Custódia (LAW-008)
    petr4_custody = CustodyPositionStruct(
        account_id=100234,
        security_id=48001,
        available_balance=10000,
        pledged_collateral=4000,
        judicial_blocked=1000,
        total_balance=15000,
    )

    # 2. Conciliação entre extrato B3 (IMBARQ006) e Livro Interno do Backoffice
    b3_positions = [
        PositionDetailRecord(
            tipo_registro="01",
            codigo_participante=88,
            codigo_conta_custodia=100234,
            codigo_negociacao="PETR4",
            codigo_isin="BRPETRACNPR6",
            tipo_mercado="010",
            sentido_posicao="C",
            quantidade_bruta=15000,
            quantidade_bloqueada=0,
            quantidade_liquida=15000,
            preco_fechamento_ajuste=Decimal("38.50"),
            valor_financeiro=Decimal("577500.00"),
            data_vencimento=None,
            codigo_serie=None,
            participante_liquidacao=88,
            linha_numero=2,
            raw_line="01",
        ),
        PositionDetailRecord(
            tipo_registro="01",
            codigo_participante=88,
            codigo_conta_custodia=100234,
            codigo_negociacao="VALE3",
            codigo_isin="BRVALEACNOR0",
            tipo_mercado="010",
            sentido_posicao="C",
            quantidade_bruta=8000,
            quantidade_bloqueada=0,
            quantidade_liquida=8000,
            preco_fechamento_ajuste=Decimal("62.00"),
            valor_financeiro=Decimal("496000.00"),
            data_vencimento=None,
            codigo_serie=None,
            participante_liquidacao=88,
            linha_numero=3,
            raw_line="01",
        ),
    ]

    internal_positions = [
        InternalPosition(
            codigo_conta_custodia=100234,
            codigo_negociacao="PETR4",
            quantidade_liquida=15000,
            sentido_posicao="C",
        ),
        InternalPosition(
            codigo_conta_custodia=100234,
            codigo_negociacao="VALE3",
            quantidade_liquida=7800,  # Divergência de 200 ações no backoffice
            sentido_posicao="C",
        ),
    ]

    reconciler = PositionReconciler()
    report = reconciler.reconcile(b3_source=b3_positions, internal_source=internal_positions)

    return {
        "lab": "LAB-02-IMBARQ006-RECONCILIATION",
        "petr4_law008_conserved": petr4_custody.is_consistent(),
        "total_positions_checked": report.total_posicoes_b3,
        "matched_count": len(report.matches),
        "breaks_detected": len(report.breaks),
        "first_break_ticker": report.breaks[0].codigo_negociacao if report.breaks else None,
    }


if __name__ == "__main__":
    res = run_lab()
    print("=" * 72)
    print("🧪 LAB 02: CONCILIAÇÃO DE CUSTÓDIA IMBARQ006 & INVARIANTE LAW-008")
    print("=" * 72)
    print(f"• Equação LAW-008 (PETR4) : {'CONSERVADA' if res['petr4_law008_conserved'] else 'VIOLADA'}")
    print(f"• Posições Auditadas      : {res['total_positions_checked']}")
    print(f"• Posições Conciliadas    : {res['matched_count']}")
    print(f"• Quebras Detectadas      : {res['breaks_detected']} (Ativo: {res['first_break_ticker']})")
    print("=" * 72)
