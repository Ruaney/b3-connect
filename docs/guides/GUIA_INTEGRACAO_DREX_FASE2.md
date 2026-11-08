# Guia de Integração — Drex Fase 2 & RWA Tokenization
## Liquidação Atômica DvP, Real Digital e Emissão Primária de Ativos Tokenizados (FL-089, FL-105)

---

### 1. Visão Geral da Rede Drex (Banco Central do Brasil & B3)
O **Drex** (anteriormente Real Digital) é a infraestrutura brasileira de Moeda Digital de Banco Central (CBDC) e ativos virtuais regulados. Operada em uma rede Distributed Ledger Technology (DLT) baseada no **Hyperledger Besu** (protocolo EVM permissionado com consenso IBFT 2.0 ou QBFT), o Drex visa introduzir liquidação atômica e programabilidade financeira no atacado e varejo.

A B3 desempenha papel central na governança e arquitetura do Drex:
* **Entidade de Registro e Custódia Tokenizada:** Espelhamento da Central Depositária (CSD B3) e SELIC na rede DLT.
* **Orquestradora de Liquidação DvP (Delivery-versus-Payment):** Execução atômica garantida onde a transferência da titularidade do ativo só ocorre se e somente se o débito e crédito financeiro forem concluídos no mesmo bloco.
* **Originação de Ativos do Mundo Real (Real World Assets - RWA):** Debêntures, CRIs, CRAs e cotas de fundos tokenizados sob as Resoluções CVM nº 160 e nº 175.

---

### 2. Arquitetura em Três Camadas do Drex

```
┌────────────────────────────────────────────────────────────────────────┐
│                        CAMADA 1: REAL DIGITAL                          │
│               CBDC Atacado / Reservas Bancárias BACEN                  │
│       Contas de Liquidação das IFs / Transações Interbancárias         │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Mint / Burn garantido 1:1
┌───────────────────────────────────▼────────────────────────────────────┐
│                      CAMADA 2: REAL TOKENIZADO                         │
│           Moeda Escritural Tokenizada (Depósitos Bancários)            │
│         Carteiras de Clientes Finais (Pessoas Físicas e Jurídicas)     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Troca Atômica DvP (Smart Contracts)
┌───────────────────────────────────▼────────────────────────────────────┐
│           CAMADA 3: ATIVOS TOKENIZADOS (TPFt & RWA PRIVADOS)           │
│     • Títulos Públicos Federais (TPFt): LTN, LFT, NTN-B                │
│     • Renda Fixa Privada RWA (ERC-1155 / ERC-3643): Debêntures, CRIs   │
└────────────────────────────────────────────────────────────────────────┘
```

---

### 3. Padrões de Smart Contracts na Rede Drex

| Padrão | Nome do Padrão | Finalidade no Drex | Recursos Principais |
|---|---|---|---|
| **RealDigital.sol** | ERC-20 Estendido | CBDC Atacado gerida exclusivamente pelo BACEN. | Mint/Burn restrito ao Banco Central, Transfer com controle de acesso institucional. |
| **RealTokenizado.sol** | ERC-20 Estendido | Depósitos bancários de clientes emissores. | Paridade rígida de R$ 1,00 para cada Real Tokenizado (LAW-089B). |
| **TPFt.sol** | ERC-1155 Multi-Token | Títulos Públicos Federais Tokenizados. | Identificação por código de ativo (LTN, NTN-B), data de vencimento e custódia fracionada. |
| **RwaDebenture.sol** | ERC-3643 (T-REX) + ERC-1155 | Debêntures e notas comerciais privadas RWA. | Conformidade CVM on-chain, KYC/AML e qualificação de investidores validados via `ONCHAINID`. |
| **DvpSwap.sol** | Atomic Swap & HTLC | Orquestração da liquidação atômica bilateral. | Transferência atômica All-or-None ou reversão imediata por timeout regulatório. |

---

### 4. Mecanismos de Liquidação Atômica DvP

#### 4.1 Direct Atomic DvP (Mesmo Bloco EVM)
Quando o comprador e o vendedor mantêm contas na mesma instituição ou o contrato atômico tem permissão de movimentação em ambos os saldos, o smart contract executa o débito da moeda e a transferência do título em uma única transação indivisível:

```solidity
// Pseudocódigo do Smart Contract DvP Atômico
function executeAtomicDvP(
    address seller,
    address buyer,
    uint256 assetId,
    uint256 assetAmount,
    uint256 cashAmount
) external onlyAuthorizedSettlementAgent {
    // 1. Debita o Real Digital do comprador e credita ao vendedor
    realDigital.transferFrom(buyer, seller, cashAmount);
    
    // 2. Transfere a titularidade do ativo RWA do vendedor ao comprador
    rwaAsset.safeTransferFrom(seller, buyer, assetId, assetAmount, "");
    
    // Invariante LAW-089A: Se qualquer uma das operações falhar, reverte toda a transação.
}
```

#### 4.2 DvP Condicional via HTLC (Hash Time-Locked Contracts)
Em cenários cross-chain ou liquidações interbancárias assíncronas com contrapartes externas:
1. O vendedor bloqueia o ativo no contrato definindo um `hashlock = SHA-256(secret)` e um `timelock` (ex: 30 minutos).
2. O comprador deposita o Real Digital sob o mesmo hashlock.
3. A revelação do `secret` pela contraparte libera simultaneamente o valor e os ativos.
4. Caso o prazo expire sem a revelação, ambos os saldos retornam aos seus proprietários originais (*No Capital Loss Guarantee*).

---

### 5. Emissão Primária de Ativos RWA (Oferta CVM 160 / FL-105)

O fluxo **FL-105** formaliza a originação, bookbuilding e liquidação primária de dívida corporativa tokenizada:

```
[Emissor RWA (Ex: Empresa Agro / Energia)]
                    │
                    │ 1. Criação da Emissão RWA (Tranches Sênior/Mezanino)
                    ▼
          [B3 DLT RWA Platform]
                    │
                    │ 2. Bookbuilding: Coleta de ordens de subscrição
                    ▼
          [Investidores Qualificados / Fundos]
                    │
                    │ 3. Apuração da Taxa de Corte & Alocação Pro-Rata
                    ▼
          [Motor de Alocação B3]
                    │
                    │ 4. Liquidação Primária Atômica (Drex DvP)
                    ▼
  ┌─────────────────────────────────────────────────────────┐
  │ Débito de Real Digital (Investidor) ──> Emissor         │
  │ Mint / Transfer de Tokens RWA ─────────> Investidor     │
  └─────────────────────────────────────────────────────────┘
```

---

### 6. Implementação Completa em Python (Simulação Drex DvP)

```python
"""Exemplo de Liquidação Atômica DvP na Rede Drex (Hyperledger Besu)."""
from decimal import Decimal
from src.drex_phase2_atomic_dvp import (
    DrexPhase2AtomicDvPEngine,
    DrexParticipant,
    DrexAssetType,
    DvpSettlementType,
)

def executar_dvp_drex():
    # Inicializa o motor de liquidação atômica Drex
    engine = DrexPhase2AtomicDvPEngine()

    # Cria participantes: Banco Alfa (Vendedor de TPFt) e Banco Beta (Comprador)
    alfa = engine.register_participant("IF_ALFA", "00360305000104", "BANCO ALFA S.A.")
    beta = engine.register_participant("IF_BETA", "60701190000104", "BANCO BETA S.A.")

    # Alocação de saldos iniciais (BACEN Mint)
    engine.mint_real_digital("IF_BETA", Decimal("10000000.00"))  # Beta tem R$ 10 Mi em Real Digital
    engine.deposit_tpft(
        participant_id="IF_ALFA",
        asset_code="LTN-2027",
        quantity=Decimal("10000"),
        unit_price=Decimal("950.00")
    )  # Alfa tem 10.000 títulos LTN valorados em R$ 9,5 Mi

    print(f"Saldo Inicial Beta (Real Digital): R$ {beta.real_digital_balance}")
    print(f"Saldo Inicial Alfa (LTN-2027): {alfa.tpft_balances['LTN-2027']} títulos")

    # Execução de DvP Atômico Direto
    print("\n-> Executando Liquidação Atômica DvP (Direct Settlement)...")
    receipt = engine.execute_atomic_dvp(
        seller_id="IF_ALFA",
        buyer_id="IF_BETA",
        asset_code="LTN-2027",
        quantity=Decimal("10000"),
        unit_price=Decimal("950.00"),
        settlement_type=DvpSettlementType.DIRECT_ATOMIC
    )

    print(f"✔ Transação DvP Concluída! TxHash Besu: {receipt.tx_hash}")
    print(f"  Status: {receipt.status} | Bloco: {receipt.block_number}")
    print(f"  Volume Financeiro Liquidado: R$ {receipt.gross_amount}")

    # Saldo final verificado
    print(f"\nSaldo Final Alfa (Real Digital): R$ {alfa.real_digital_balance}")
    print(f"Saldo Final Beta (LTN-2027): {beta.tpft_balances['LTN-2027']} títulos")

    # Verificação das Leis Formais de Conservação
    engine.verify_invariants()
    print("\n✔ Invariantes LAW-089A, LAW-089B e LAW-089C verificadas com sucesso.")

if __name__ == "__main__":
    executar_dvp_drex()
```

---

### 7. Invariantes Formais Provadas
* **LAW-089A (Conservação DvP All-or-None):**
  $$\Delta \text{Ativo}_{\text{Vendedor}} = -Q \iff \Delta \text{Moeda}_{\text{Vendedor}} = + (Q \times P)$$
* **LAW-089B (Paridade Soberana 1:1):**
  $$\forall t \in \text{Rede}, \quad 1{,}00 \text{ Real Tokenizado} \equiv 1{,}00 \text{ Real Digital} \equiv \text{R\$} 1{,}00 \text{ BRL}$$
* **LAW-111 (Emissão Primária RWA):**
  $$\sum \text{Tokens Alocados} = \text{Quantidade Emitida} \quad \land \quad \sum \text{Financeiro Recebido} = \text{Volume Total da Oferta}$$
