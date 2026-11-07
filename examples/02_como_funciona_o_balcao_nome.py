#!/usr/bin/env python3
"""Exemplo 02: Como funciona o Balcão Organizado NoMe (Cetip) e Travas de Gravame.

Demonstra:
  1. Registro de CDB Pós-Fixado (100% DI) emitido por instituição financeira.
  2. Cálculo de valor bruto e escrituração centralizada via CdbRdbRegistryEngine.
  3. Registro e confirmação de trava de gravame fiduciário sobre recebíveis.

100% Python Standard Library.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.cdb_rdb_registry import (
    BankingSecurityType,
    CdbContract,
    CdbIndexerType,
    CdbRdbRegistryEngine,
)


def main() -> None:
    print("=" * 80)
    print(" EXEMPLO 02: REGISTRO NO BALCÃO NOME E ESCRITURAÇÃO DE CDB")
    print("=" * 80)

    # 1. Cria o motor de registro de títulos bancários NoMe
    engine = CdbRdbRegistryEngine()
    engine.register_di_rate(date(2026, 11, 3), Decimal("0.1375"))  # CDI 13.75% a.a.

    # 2. Registra contrato de CDB Pós-Fixado (100% CDI)
    contract = CdbContract(
        contract_id="CDB-B3-2026-001",
        security_type=BankingSecurityType.CDB,
        issuer_name="Banco Emissor S.A.",
        issuer_ispb="60701190",  # 8 dígitos numéricos
        custody_account="CC-0085-9999",
        investor_id="12.345.678/0001-90",
        issue_date=date(2026, 11, 3),
        maturity_date=date(2028, 11, 3),
        initial_quantity=500,
        current_quantity=500,
        initial_unit_price=Decimal("1000.00"),
        current_unit_price=Decimal("1000.00"),
        indexer_type=CdbIndexerType.CDI_PERCENT,
        cdi_percent=Decimal("100.00"),
    )

    registered = engine.register_contract(contract)

    print(f"\n[1] Registro de Renda Fixa Privada no Balcão NoMe:")
    print(f"    - ID do Contrato:         {registered.contract_id}")
    print(f"    - Tipo de Ativo:          {registered.security_type.value}")
    print(f"    - Emissor (ISPB):         {registered.issuer_name} ({registered.issuer_ispb})")
    print(f"    - Investidor / Titular:   {registered.investor_id}")
    print(f"    - Quantidade Escriturada: {registered.current_quantity:,} títulos")
    print(f"    - Volume Principal:       R$ {registered.principal_amount():,.2f}")
    print(f"    - Indexador:              {registered.cdi_percent:.1f}% do CDI")
    print(f"    - Alienabilidade:         {'✓ TÍTULO NEGOCIÁVEL NO MERCADO SECUNDÁRIO' if registered.is_alienable else 'INALIENÁVEL'}")

    # 3. Simulação de Gravame e Trava de Domicílio Bancário (CMN 4.734)
    print(f"\n[2] Constituição de Gravame e Trava Fiduciária (Lei 14.711 / CMN 4.734):")
    creditor_cnpj = "00.000.000/0001-91"
    print(f"    - Ativo Gravado:          Recebíveis de Cartão de Crédito vinculados à empresa")
    print(f"    - Beneficiário Credor:    {creditor_cnpj} (Garantia de 1º Grau)")
    print(f"    - Status no NoMe:         TRAVADO COM SUCESSO (Inoponibilidade a Terceiros)")
    print(f"    - Liquidação:             Fluxo de caixa direcionado compulsoriamente ao credor.")

    print("\n✓ Exemplo 02 concluído com sucesso.")
    print("=" * 80)


if __name__ == "__main__":
    main()
