# Especificação Técnica — Criptoativos, Tokens RWA e Custódia Híbrida CSD B3 (FL-075)

---

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **`FL-075` — Criptoativos, Tokens RWA e Custódia Híbrida CSD B3** estabelece a ponte interoperável entre a infraestrutura tradicional de Central Depositária (**CSD**) da **B3** e redes públicas/privadas de tecnologia de registro distribuído (**DLT / Blockchain**).

Permite a representação tokenizada de ativos do mundo real (**RWA - Real World Assets**), tais como debêntures corporativas, notas comerciais, cotas de fundos e títulos securitizados, sob os padrões **ERC-3643 (Permissioned Security Tokens)** e **ERC-20/ERC-1155**.

Regulação e conformidade:
1. **Lei nº 14.478/2022 (Marco Legal dos Criptoativos no Brasil)**:
   - Exige segregação patrimonial estrita entre os recursos dos clientes e o patrimônio da Prestadora de Serviços de Ativos Digitais (PSAV / VASP).
   - Determina comprovação de auditoria contínua de reservas (**Proof-of-Reserve - PoR**).
2. **Resolução CVM nº 175/2022 e Parecer de Orientação CVM nº 40/2022**:
   - Critérios para investimento de fundos regulados em ativos digitais e regras para que tokens representativos de valores mobiliários cumpram os requisitos de escrituração da Lei nº 6.404/76.
3. **Mecanismo de Bridge On-Chain / Off-Chain**:
   - Espelhamento fiduciário (*lock-and-mint*): o ativo físico/escritural é bloqueado na conta CSD da B3 e o respectivo token digital é emitido na carteira permissionada do investidor, com verificação de conformidade de regras de KYC/AML integradas via smart contract.

---

## 2. Invariantes Formais Provadas

1. **Paridade Estrita 1:1 de Proof-of-Reserve (PoR)**:
   `Lastro_Segregado_CSD == Supply_Circulante_Tokens` (auditado via Árvores de Merkle e conciliação em tempo real).
2. **Inviolabilidade da Segregação Patrimonial**:
   Contas de clientes e reservas fiduciárias não se comunicam com contas proprietárias do CSD ou da plataforma de tokenização.
3. **Atomicidade DvP Híbrido (Cross-Chain Settlement)**:
   Liquidação sincronizada de entrega versus pagamento eliminando o risco de entrega unilateral sem liquidação de caixa.
4. **Conservação em Processos de Emissão (Mint) e Resgate (Burn)**:
   `Delta_Lock_CSD == Delta_Mint_Token` e `Delta_Burn_Token == Delta_Unlock_CSD`.
5. **Conformidade de Whitelist ERC-3643**:
   Transferências on-chain são rejeitadas preventivamente se os endereços remetente ou destinatário não possuírem atestado de conformidade regulatória vigente.

---

## 3. Interfaces do Módulo

- **Módulo Python**: `src/crypto_digital_assets.py`
- **Fixture JSON**: `fixtures/crypto_digital_assets.json`
- **Testes Unitários**: `tests/test_crypto_digital_assets.py`
