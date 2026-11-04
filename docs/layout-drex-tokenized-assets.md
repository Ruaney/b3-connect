# Especificação Técnica: B3 CSD & DREX — Títulos Públicos Tokenizados e DvP (FL-061)

## 1. Visão Geral da Arquitetura DREX / B3 CSD

A interoperabilidade entre a **Central Depositária B3 (CSD)** e a rede **DREX** (Real Digital / Hyperledger Besu) do Banco Central do Brasil viabiliza a tokenização de Títulos Públicos Federais (TPFt - LTN, LFT, NTN-B) e a liquidação atômica **Delivery versus Payment (DvP)** em contratos inteligentes permissionados.

### Ciclo Operacional
1. **Tokenização (Lock & Mint)**:
   - Bloqueio fiduciário das frações ou lotes de títulos na conta de custódia tradicional da CSD (`encumbered_drex_quantity`).
   - Emissão espelhada (*Mint*) de tokens compatíveis (ERC-1155) na carteira DREX do titular.
2. **Negociação Secundária e DvP On-Chain**:
   - Troca atômica e simultânea de TPFt por Real Digital entre carteiras com chaves privadas autorizadas.
   - Zero risco de contraparte: ambas as pernas executam no mesmo bloco transacional.
3. **Des-tokenização (Burn & Unlock)**:
   - Destruição (*Burn*) dos tokens on-chain e liberação imediata dos títulos originais na conta de custódia livre da CSD B3.

---

## 2. Leis Formais de Conservação e Invariantes

1. **Conservação Absoluta de Custódia Total**:
   $$Q_{\text{CSD\_Total}} = Q_{\text{CSD\_Disponível}} + Q_{\text{CSD\_Bloqueado\_DREX}}$$
   $$Q_{\text{CSD\_Bloqueado\_DREX}} \equiv \sum Q_{\text{Tokens\_DREX\_OnChain}}$$
2. **Anti-Duplo Gasto / Anti-Dupla Custódia**:
   Nenhum ativo tokenizado pode ser alienado ou transferido na B3 tradicional enquanto estiver sob custódia on-chain na rede DREX.
3. **Atomicidade DvP Indivisível**:
   A transferência de TPFt e a liquidação financeira em Real Digital ocorrem sob a garantia de atomicidade de Smart Contract (ambas executam com sucesso ou sofrem rollback total).

---

## 3. Interfaces do Motor

- Módulo Python: `src/drex_tokenized_assets.py`
- Fixture Sintética: `fixtures/drex_tokenized_assets.json`
- Testes Unitários: `tests/test_drex_tokenized_assets.py`
