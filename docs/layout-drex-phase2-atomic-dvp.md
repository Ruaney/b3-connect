# Especificação Técnica: B3 CSD & BACEN DREX Piloto Fase 2 — Liquidação Atômica DvP e PvP em Rede DLT EVM (FL-089)

## 1. Visão Geral da Arquitetura Drex Piloto Fase 2 (BACEN & B3)

O fluxo **FL-089** implementa o motor de liquidação atômica **Delivery versus Payment (DvP)** e **Payment versus Payment (PvP)** em rede permissionada DLT baseada em **EVM / Hyperledger Besu**, conforme as diretrizes e diretivas regulatórias do **Piloto Drex Fase 2** emitidas pelo **Banco Central do Brasil (BACEN)** em conjunto com a **B3 (Central Depositária / CSD)** e a Secretaria do Tesouro Nacional (STN).

O ecossistema Drex Fase 2 estrutura-se em um modelo hierárquico e segregado de **3 camadas**:

```
+--------------------------------------------------------------------------+
|                  CAMADA 1: REAL DIGITAL (CBDC ATACADO)                   |
|  - Emitido estritamente pelo BACEN via Contas de Reserva DLT             |
|  - Circulação exclusiva interbancária entre Instituições Financeiras (IFs)|
|  - Lastro e liquidez primária da infraestrutura nacional de pagamentos    |
+------------------------------------+-------------------------------------+
                                     | Liquidação Interbancária / PvP
                                     v
+------------------------------------+-------------------------------------+
|              CAMADA 2: REAL TOKENIZADO (MOEDA DE VAREJO)                 |
|  - Depósitos bancários escriturais tokenizados por IFs autorizadas       |
|  - Circulação entre clientes finais (PF, PJ e Fundos Institucionais)     |
|  - Paridade soberana estrita 1:1 com o Real Digital (R$ 1,00 BRL)        |
+------------------------------------+-------------------------------------+
                                     | Liquidação DvP Atômica
                                     v
+------------------------------------+-------------------------------------+
|        CAMADA 3: TÍTULOS PÚBLICOS FEDERAIS TOKENIZADOS (TPFt)            |
|  - LTN (Prefixado), LFT (Pós SELIC) e NTN-B (IPCA + Cupom Semestral)    |
|  - Espelhamento de custódia fiduciária na Central Depositária B3 / SELIC|
|  - Smart Contracts ERC-1155 / Tokenized Bonds em Hyperledger Besu        |
+--------------------------------------------------------------------------+
```

---

## 2. Modalidades de Liquidação e Atomicidade

### 2.1 DvP Atacado (IF vs IF)
- **Contrapartes**: Duas Instituições Financeiras participantes.
- **Perna de Ativos**: Transferência simultânea de frações ou lotes de TPFt da carteira proprietária da IF vendedora para a IF compradora.
- **Perna de Liquidação**: Débito de Real Digital (CBDC Atacado) na conta de reserva da IF compradora e crédito na conta de reserva da IF vendedora.
- **Atomicidade**: Executada via Smart Contract no mesmo bloco DLT, sem risco de entrega sem pagamento.

### 2.2 DvP Varejo Interbancário (Cliente IF A vs Cliente IF B)
- **Cenário**: Cliente da IF A (Vendedor) vende TPFt para Cliente da IF B (Comprador).
- **Execução Atômica Quadri-partida**:
  1. **TPFt**: Transferido da carteira do Cliente A para o Cliente B;
  2. **Real Tokenizado de Varejo**: Débito do Real Tokenizado emitido pela IF B na carteira do Cliente B; crédito de Real Tokenizado emitido pela IF A na carteira do Cliente A;
  3. **Real Digital Atacado (Compensação Interbancária)**: Débito na conta de reserva da IF B e crédito na conta de reserva da IF A junto ao BACEN;
  4. **Emissão e Queima Controladas**: A IF B destrói/reduz o saldo de Real Tokenizado emitido e a IF A emite/aumenta o saldo correspondente, preservando o valor soberano global.
- **Princípio All-or-None**: Caso qualquer participante ou IF não disponha de saldo ou esteja com a conta bloqueada, toda a transação sofre reversão integral (*revert*).

### 2.3 PvP Interbancário (Transferência Monetária entre Clientes)
- Transferência direta de valor entre clientes de bancos distintos sem risco de descasamento:
  $$\Delta \text{RT}_{\text{Remetente}} = -V, \quad \Delta \text{RD}_{\text{IF Remetente}} = -V, \quad \Delta \text{RD}_{\text{IF Destinatária}} = +V, \quad \Delta \text{RT}_{\text{Destinatário}} = +V$$

### 2.4 Hash Time-Locked Contracts (HTLC) e Reversão por Timeout
- Mecanismo criptográfico com trava de hash ($\mathcal{H} = \text{SHA-256}(S)$) e trava de tempo regulatório ($T_{\text{timeout}}$).
- **Liquidação Condicional**: O destinatário só pode resgatar o ativo apresentando a *preimage* secreta $S$ tal que $\text{SHA-256}(S) = \mathcal{H}$ antes de $t > T_{\text{timeout}}$.
- **Reversão Automática**: Se o prazo expirar sem apresentação de $S$, o contrato permite ao remetente original recuperar 100% dos ativos travados, anulando o risco de retenção arbitrária.
- **Troca Atômica Bilateral Cruzada**: Dois HTLCs compartilhando a mesma $\mathcal{H}$ são resolvidos em simultâneo no momento em que a chave $S$ é revelada.

---

## 3. Leis Formais de Conservação e Invariantes

### Lei 69 / LAW-089A: Conservação Atômica DvP e PvP (All-or-None) em DLT
$$\Delta Q_{\text{TPFt}}(\text{Vendedor}) = -\Delta Q_{\text{TPFt}}(\text{Comprador}) = Q_{\text{Operação}}$$
$$\Delta F(\text{Comprador}) = -\Delta F(\text{Vendedor}) = Q_{\text{Operação}} \times \text{PU}$$
Ou todas as 4 pernas concluem com sucesso, ou o estado permanece inalterado.

### Lei 70 / LAW-089B: Paridade 1:1 e Conservação de Moeda Soberana
$$1,00 \text{ Real Tokenizado} \equiv 1,00 \text{ Real Digital} \equiv \text{R\$ } 1,00 \text{ BRL}$$
A emissão de Real Tokenizado por qualquer IF autorizada deve possuir contrapartida equivalente em depósitos ou reservas, impedindo geração espúria de base monetária.

### Lei 71 / LAW-089C: Não-Negatividade e Não-Geração Inflacionária de Tokens
$$\forall w \in \text{Wallets}, \forall a \in \text{Ativos}, \quad \text{Saldo}(w, a) \ge 0$$
$$\sum_{\text{DLT}} Q_{\text{TPFt}}(s) \le Q_{\text{SELIC/CSD}}(s)$$

---

## 4. Interfaces do Motor e Arquitetura de Entrega

- **Módulo Python**: `src/drex_phase2_atomic_dvp.py`
  - Classe Principal: `DrexPhase2AtomicDvpEngine`
  - Métodos Chave:
    - `settle_wholesale_dvp()`: DvP Atacado entre IFs (TPFt x Real Digital).
    - `settle_interbank_retail_dvp()`: DvP Varejo com liquidação interbancária simultânea.
    - `settle_interbank_pvp()`: Transferência PvP entre clientes de diferentes bancos.
    - `create_htlc()`, `claim_htlc()`, `refund_htlc()`, `cancel_htlc()`.
    - `execute_htlc_atomic_dvp()`: Orquestração bilateral de troca atômica HTLC.
    - `verify_invariants()`: Auditoria formal de todas as leis LAW-089.
    - `to_dict()` e `load_from_fixture()`.
- **Fixture JSON**: `fixtures/drex_phase2_atomic_dvp.json`
- **Suíte de Testes**: `tests/test_drex_phase2_atomic_dvp.py`
- **Registro no Catálogo**: `catalog/flows.json` (FL-089) e `catalog/sources.json` (SRC-072).
