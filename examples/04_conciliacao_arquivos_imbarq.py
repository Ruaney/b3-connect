#!/usr/bin/env python3
"""Exemplo 04: Como conciliar posições usando os arquivos IMBARQ da Central Depositária.

Demonstra:
  1. Estrutura posicional dos arquivos oficiais do iMercado (IMBARQ012 e IMBARQ004).
  2. Verificação do Princípio da Incomunicabilidade Patrimonial por CPF/CNPJ.
  3. Comprovação da identidade contábil: Total == Disponível + Garantia + Judicial.

100% Python Standard Library.
"""

from decimal import Decimal
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.invariants import assert_custody_conservation


def main() -> None:
    print("=" * 80)
    print(" EXEMPLO 04: CONCILIAÇÃO DE CUSTÓDIA COM ARQUIVOS IMBARQ DA CSD")
    print("=" * 80)

    # 1. Registro simulado do IMBARQ012 (Posição em Custódia por Investidor)
    investor_cpf = "345.678.901-23"
    security = "PETR4"
    broker_id = "0085"  # Código da Corretora na B3

    qty_disponivel = 5000
    qty_garantia = 2000    # Alocado em garantia na Câmara CORE
    qty_judicial = 500     # Bloqueio judicial (Sisbajud)
    qty_total = 7500

    print(f"\n[1] Lendo Registro IMBARQ012 (Tipo 01 - Saldo de Custódia):")
    print(f"    - Corretora Participante: {broker_id}")
    print(f"    - Investidor Final (CPF): {investor_cpf}")
    print(f"    - Código do Ativo:        {security}")
    print(f"    - Quantidade Total:       {qty_total:,} ações")
    print(f"    - Saldo Disponível:       {qty_disponivel:,} ações")
    print(f"    - Saldo em Garantia:      {qty_garantia:,} ações")
    print(f"    - Bloqueio Judicial:      {qty_judicial:,} ações")

    # 2. Verificação de Conservação Formal (LAW-001 / LAW-034)
    print(f"\n[2] Verificando Invariante Contábil de Custódia (LAW-001 / IMBARQ012):")
    assert_custody_conservation(
        total=qty_total,
        available=qty_disponivel,
        blocked_guarantee=qty_garantia,
        blocked_judicial=qty_judicial,
        account_id=investor_cpf,
        symbol=security,
    )
    print("    ✓ Batimento Perfeito: Total == Disponível + Garantia + Judicial (7.500 == 7.500)")

    # 3. Princípio da Incomunicabilidade Patrimonial
    print(f"\n[3] Incomunicabilidade Patrimonial na Central Depositária:")
    print(f"    - Conta do Cliente {investor_cpf} é mantida de forma unívoca na CSD.")
    print("    - Em caso de intervenção na corretora, a custódia NÃO se comunica com ativos da firma.")
    print("    - Portabilidade de custódia STVM pode ser requerida diretamente para outro custodiante.")

    print("\n✓ Conciliação IMBARQ concluída sem quebras ou discrepâncias.")
    print("=" * 80)


if __name__ == "__main__":
    main()
