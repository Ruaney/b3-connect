#!/usr/bin/env python3
"""Exemplo 01: Como enviar uma ordem ao PUMA Trading System com verificação de túnel de preço.

Demonstra:
  1. Instanciação do motor PUMA Circuit Breaker & Tunnels (PumaCircuitBreakerEngine).
  2. Submissão de ordem regular dentro dos túneis de rejeição.
  3. Tentativa de envio de ordem anômala (Fat-Finger) e rejeição automática.
  4. Preservação de integridade de book e leis de conservação formal.

100% Python Standard Library.
"""

from decimal import Decimal
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.puma_circuit_breaker_tunnels import AssetSegment, Order, OrderSide, OrderType, PumaCircuitBreakerEngine


def main() -> None:
    print("=" * 80)
    print(" EXEMPLO 01: FLUXO DE ORDEM E TÚNEIS DE PREÇO NO PUMA TRADING SYSTEM")
    print("=" * 80)

    # 1. Inicializa o motor PUMA para o ativo PETR4 com preço de abertura R$ 36.00
    engine = PumaCircuitBreakerEngine(
        symbol="PETR4",
        segment=AssetSegment.IBOV_EQUITY,
        opening_price=Decimal("36.00"),
    )
    bounds = engine.compute_bounds()

    print(f"\n[1] Parâmetros de Túnel do PUMA para PETR4:")
    print(f"    - Preço de Abertura:           R$ {engine.opening_price:.2f}")
    print(f"    - Túnel de Rejeição Superior:  R$ {bounds.rejection_upper:.2f}")
    print(f"    - Túnel de Rejeição Inferior:  R$ {bounds.rejection_lower:.2f}")

    # 2. Submissão de Ordem Válida de Compra (R$ 36.50)
    valid_order = Order(
        order_id="ORD-VALID-01",
        symbol="PETR4",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("36.50"),
        quantity=1000,
        participant_id="BR-BROKER-01",
    )

    print(f"\n[2] Enviando Ordem Válida de Compra (R$ 36.50, 1.000 ações):")
    success, msg = engine.submit_order(valid_order)
    print(f"    - Status:     {'✓ ACEITA' if success else '✗ REJEITADA'}")
    print(f"    - Mensagem:   {msg}")

    # 3. Submissão de Ordem Anômala / Fat-Finger (R$ 120.00)
    fat_finger_order = Order(
        order_id="ORD-FATFINGER-99",
        symbol="PETR4",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("120.00"),  # > limite superior
        quantity=5000,
        participant_id="BR-BROKER-01",
    )

    print(f"\n[3] Tentando Enviar Ordem Anômala / Fat-Finger (R$ 120.00):")
    success, msg = engine.submit_order(fat_finger_order)
    print(f"    - Status:     {'✓ ACEITA' if success else '✓ BLOQUEADA AUTOMATICAMENTE'}")
    print(f"    - Motivo B3:  {msg}")

    # 4. Verificação de Invariantes Formais do Motor PUMA
    inv = engine.verify_invariants()
    print(f"\n[4] Invariantes Formais de Mercado:")
    for k, v in inv.items():
        print(f"    - {k}: {'✓ OK' if v else 'FALHA'}")

    print("\n✓ Exemplo 01 concluído com sucesso.")
    print("=" * 80)


if __name__ == "__main__":
    main()
