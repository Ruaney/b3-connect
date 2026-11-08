#!/usr/bin/env python3
"""Lab 01: Framing Binário PUMA SBE v2 Zero-Copy e Leilão de Fechamento MOC.

Demonstra na prática:
1. Empacotamento e decodificação zero-copy de uma ordem PUMA SBE v2 usando a
   camada C-ABI (`src.c_abi_compat.NewOrderSingleStruct` e `CustodyPositionStruct`).
2. Cálculo do preço de equilíbrio do Leilão de Fechamento (Market-on-Close / MOC)
   com os 4 critérios oficiais da B3 (`src.puma_closing_cross_moc.PumaClosingCrossEngine`).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.c_abi_compat import (
    CustodyPositionStruct,
    NewOrderSingleStruct,
    zero_copy_from_buffer,
)
from src.puma_closing_cross_moc import (
    ClosingOrder,
    ClosingOrderSide,
    ClosingOrderType,
    PumaClosingCrossEngine,
)


def run_lab() -> dict[str, object]:
    """Executa o Lab 01 e retorna telemetria estruturada para validação."""
    # 1. Framing Binário PUMA SBE v2 Little-Endian via C-ABI Zero-Copy
    order_struct = NewOrderSingleStruct()
    order_struct.populate(
        cl_ord_id=9000142,
        security_id=48001,  # PETR4
        side=1,  # 1 = BUY
        order_qty=1000,
        price=38.50,
        time_in_force=0,
        account=100234,
        schema_id=2,
        version=1,
    )
    raw_buf = bytearray(order_struct.as_bytes())
    decoded = zero_copy_from_buffer(NewOrderSingleStruct, raw_buf)

    # Validação C-ABI da Equação de Custódia (LAW-008)
    custody_c = CustodyPositionStruct(
        account_id=100234,
        security_id=48001,
        available_balance=10000,
        pledged_collateral=4000,
        judicial_blocked=1000,
        total_balance=15000,
    )

    # 2. Simulação do Leilão de Fechamento MOC (LAW-092)
    moc_engine = PumaClosingCrossEngine(symbol="PETR4", reference_price=Decimal("38.45"))
    moc_engine.add_order(
        ClosingOrder(
            order_id="B1",
            symbol="PETR4",
            side=ClosingOrderSide.BUY,
            order_type=ClosingOrderType.LIMIT,
            quantity=5000,
            price=Decimal("38.60"),
            timestamp_ms=1001,
        )
    )
    moc_engine.add_order(
        ClosingOrder(
            order_id="B2",
            symbol="PETR4",
            side=ClosingOrderSide.BUY,
            order_type=ClosingOrderType.LIMIT,
            quantity=3000,
            price=Decimal("38.50"),
            timestamp_ms=1002,
        )
    )
    moc_engine.add_order(
        ClosingOrder(
            order_id="S1",
            symbol="PETR4",
            side=ClosingOrderSide.SELL,
            order_type=ClosingOrderType.LIMIT,
            quantity=4500,
            price=Decimal("38.40"),
            timestamp_ms=1003,
        )
    )
    moc_engine.add_order(
        ClosingOrder(
            order_id="S2",
            symbol="PETR4",
            side=ClosingOrderSide.SELL,
            order_type=ClosingOrderType.LIMIT,
            quantity=3500,
            price=Decimal("38.50"),
            timestamp_ms=1004,
        )
    )
    moc_summary = moc_engine.execute_uncross()

    return {
        "lab": "LAB-01-PUMA-SBE-AND-MOC",
        "sbe_byte_length": len(raw_buf),
        "sbe_template_id": int(decoded.header.template_id),
        "sbe_decoded_price": Decimal(f"{decoded.price:.2f}"),
        "sbe_decoded_qty": int(decoded.order_qty),
        "moc_equilibrium_price": moc_summary.uncross_price,
        "moc_matched_quantity": moc_summary.executable_volume,
        "moc_imbalance": moc_summary.imbalance_quantity,
        "law008_c_abi_check": 1 if custody_c.is_consistent() else 0,
    }


if __name__ == "__main__":
    res = run_lab()
    print("=" * 72)
    print("🧪 LAB 01: PUMA SBE v2 ZERO-COPY & LEILÃO DE FECHAMENTO MOC")
    print("=" * 72)
    print(f"• Frame SBE v2 empacotado : {res['sbe_byte_length']} bytes (Template ID={res['sbe_template_id']})")
    print(f"• Ordem decodificada      : {res['sbe_decoded_qty']} ações @ R$ {res['sbe_decoded_price']:.2f}")
    print(f"• Preço Teórico MOC PETR4 : R$ {res['moc_equilibrium_price']:.2f}")
    print(f"• Volume Casado no MOC    : {res['moc_matched_quantity']} ações (Imbalance={res['moc_imbalance']})")
    print("=" * 72)
