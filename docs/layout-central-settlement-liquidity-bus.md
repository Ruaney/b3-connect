# Especificação Técnica: Barramento Central de Liquidação Unificada e Compensação Multilateral Contínua da B3 (FL-100)
## Arquitetura de Pós-Negociação Integrada para 10 Câmaras, Fechamento Contábil Consolidado D0/D+1/D+2, Liquidação Atômica Inter-Sistemas e Salvaguardas Integradas

---

## 1. Visão Geral e Fundamentação Regulatória

O fluxo **FL-100** consolida a espinha dorsal de infraestrutura de pós-negociação da B3 ao implementar o **Barramento Central de Liquidação Unificada e Compensação Multilateral Contínua (*B3 Central Settlement & Liquidity Bus*)**.

Como Contraparte Central (CCP) sistemicamente relevante no cenário financeiro global, a Câmara B3 atua como devedora de todo credor e credora de todo devedor. O barramento central unifica a mensageria, a compensação multilateral e os processos de liquidação de **10 subsistemas e câmaras especializadas**, eliminando silos de liquidez e propiciando máxima eficiência de colaterais através de netting cruzado contínuo e fechamento contábil intradiário e diário.

### Bases Regulatórias e Normativas

1. **Lei Federal nº 10.214/2001 (Compensação e Liquidação no SPB)**:
   - **Arts. 2º ao 5º**: Irrevogabilidade, infalibilidade e definitividade das liquidações financeiras multilaterais realizadas pelas câmaras e prestadores de serviços de compensação e liquidação autorizados pelo Banco Central do Brasil.
   - **Art. 4º**: Princípio da **Inoponibilidade e Segregação Patrimonial**, garantindo que as margens, garantias e fundos de liquidação vinculados às operações da câmara não respondem por obrigações de falência ou recuperação judicial do participante inadimplente.
2. **Princípios CPMI-IOSCO para Infraestruturas do Mercado Financeiro (PFMI)**:
   - **Princípio 4 (Risco de Crédito - Cover 2)**: Manutenção de recursos financeiros suficientes para absorver com alto grau de confiança as perdas geradas pelo default simultâneo dos dois maiores participantes em exposição agregada.
   - **Princípio 7 (Risco de Liquidez)**: Governança de liquidez em D0 para honrar todas as obrigações multilaterais de pagamento em moeda soberana sem descontinuidade operacional.
3. **Resolução BCB nº 105/2021**:
   - Regulamento do funcionamento de sistemas de liquidação e câmaras no âmbito do Sistema de Pagamentos Brasileiro (SPB).
4. **Resolução CVM nº 175/2022**:
   - Marco regulatório da indústria de fundos de investimento, disciplinando a segregação de patrimônio entre subclasses e os mecanismos de split de liquidação D0/D+2.
5. **Regulamento da Câmara B3 (Capítulo VII)** e **Manual de Risco CORE**:
   - Estrutura hierárquica e determinística da Cascata de Inadimplência (*Default Waterfall*).

---

## 2. Unificação dos 10 Subsistemas e Câmaras

O barramento central conecta e orquestra 10 subsistemas de mercado que historicamente operavam em redes e protocolos heterogêneos:

```
+-----------------------------------------------------------------------------------------------------------------------------------+
|                                     B3 CENTRAL SETTLEMENT & LIQUIDITY BUS (FL-100)                                                |
+-----------------------------------------------------------------------------------------------------------------------------------+
|                                                                                                                                   |
|    [01. EQUITIES]          [02. DERIVATIVES]        [03. NOME BALCÃO]       [04. RENDA FIXA]        [05. AGRO]                    |
|    Ações, ETFs, BDRs,      Futuros & Opções         Swaps, NDFs, Opções     TPF (SELIC) e Privada   CPR, CDA/WA, CIR,             |
|    Mercado à Vista (D+2)   Ajuste Diário (D+1)      Flexíveis (D0/D+1)      (CDB/Debêntures) (D+1)  Warrants Agro (D0/D+2)        |
|          |                       |                        |                       |                       |                       |
|          +-----------------------+------------------------+-----------------------+-----------------------+                       |
|                                                           |                                                                       |
|                                                           v                                                                       |
|                               +-------------------------------------------------------+                                           |
|                               |     COMPENSAÇÃO MULTILATERAL CONTÍNUA (NETTING)       |                                           |
|                               |     - Netting por Ciclo: D0 (Intraday), D1 e D2       |                                           |
|                               |     - Netting Consolidado Global                      |                                           |
|                               |     - Conservação Soma-Zero: Sum(Net_i) == 0.00       |                                           |
|                               +---------------------------+---------------------------+                                           |
|                                                           |                                                                       |
|          +-----------------------+------------------------+-----------------------+-----------------------+                       |
|          |                       |                        |                       |                       |                       |
|    [06. CVM 175]           [07. CRIPTO / DREX]      [08. PIX MARGEM]        [09. CCEE ENERGIA]      [10. CARBONO]                 |
|    Subscrição/Resgate      DvP Atômico On-Chain     Chamadas Intradiárias   Mercado Livre ACL       CBIO RenovaBio e              |
|    Cotas Split (D0/D+2)    CBDC / TPFt (D0)         SPI Instantâneo (D0)    e MCP (D0/D+1/D+2)      Voluntário (D0/D+1)           |
|                                                                                                                                   |
+-----------------------------------------------------------------------------------------------------------------------------------+
|                                        MOTORES CENTRAIS DE LIQUIDAÇÃO E SALVAGUARDA                                               |
+-----------------------------------------------------------------------------------------------------------------------------------+
|                                                                                                                                   |
|   +------------------------------------+   +------------------------------------+   +-----------------------------------------+   |
|   |   ROUTER DE ATOMICIDADE (LAW-106C) |   |    BALANÇO CONSOLIDADO (LAW-106A)  |   |   DEFAULT WATERFALL INTEGRADO (LAW-106B)|   |
|   |   All-or-Nothing Inter-Sistemas    |   |    Soma Ativos == Soma Passivos    |   |   Absorção em 5 Camadas Estanques       |   |
|   |   Zero Risco Herstatt / Descasamento|   |    Tolerância Exata: R$ 0,00       |   |   100% de Honra aos Credores            |   |
|   +------------------------------------+   +------------------------------------+   +-----------------------------------------+   |
|                                                                                                                                   |
+-----------------------------------------------------------------------------------------------------------------------------------+
```

### Detalhamento dos Subsistemas Integrados

1. **`EQUITIES`**: Mercado de ações, BDRs, cotas de Fundos Imobiliários (FII) e ETFs. Liquidação multilateral ordinária em ciclo $D+2$.
2. **`DERIVATIVES`**: Contratos futuros e de opções listadas no PUMA (DI1, Dólar, Ibovespa, Milho, Boi Gordo). Liquidação em $D+1$ do ajuste diário.
3. **`NOME_BALCAO`**: Plataforma NoMe para derivativos de balcão organizado (Swaps DI x Pré, NDF Cambial, Opções Flexíveis de Moedas e Ações). Liquidação em $D+0$ ou $D+1$.
4. **`FIXED_INCOME`**: Títulos Públicos Federais (LTN, LFT, NTN-B, NTN-F via SELIC/CSD) e Títulos Privados (CDB, RDB, Debêntures Incentivadas, CRIs e CRAs). Liquidação em $D+0$ ou $D+1$.
5. **`AGRO`**: Cédulas de Produto Rural (CPR física e financeira), Certificados de Depósito Agropecuário (CDA/WA) e Cédula Imobiliária Rural (CIR com patrimônio de afetação).
6. **`CVM_175`**: Registro, subscrição e resgate de subclasses de cotas de fundos mútuos e estruturados (FIDC, FIP, FIA), com segregação e split settlement ($D+0$ na taxa de administração/custódia e $D+2$ no principal).
7. **`CRYPTO_DREX`**: Criptoativos regulados e infraestrutura Hyperledger Besu do Piloto Drex (Banco Central / B3), viabilizando DvP e PvP atômico on-chain em Real Digital atacado.
8. **`PIX_MARGIN`**: Liquidação instantânea de aportes e chamadas intradiárias de margem de garantia via Sistema de Pagamentos Instantâneos (SPI) e Pix Automático.
9. **`CCEE_ENERGY`**: Liquidação financeira multilateral dos contratos bilaterais do Ambiente de Contratação Livre (ACL) e do Mercado de Curto Prazo (MCP) da Câmara de Comercialização de Energia Elétrica.
10. **`CARBON`**: Créditos de Descarbonização da Política Nacional de Biocombustíveis (CBIO / RenovaBio) e créditos florestais do mercado voluntário de carbono.

---

## 3. Ciclos de Liquidação (D0, D1 e D2)

O barramento suporta três ciclos temporais essenciais:

| Ciclo | Natureza Operacional | Subsistemas Típicos | Mecanismo de Liquidação |
|---|---|---|---|
| **`D0`** | Intradiário / Instantâneo / Real-time | Pix Margem, Drex DvP, Chamadas de Risco CORE, CBIO Spot | Bruto em Tempo Real (RTGS) ou Compensação Atômica Multilateral Instantânea via STR/SPI |
| **`D1`** | Próximo Dia Útil ($D+1$) | Derivativos Listados (Ajuste Diário), Balcão NoMe, TPFs | Janela Multilateral Matutina (Compensação e Débito/Crédito das posições líquidas) |
| **`D2`** | Dois Dias Úteis ($D+2$) | Mercado à Vista de Ações, Subscrição/Resgate CVM 175, CCEE MCP | Janela Multilateral Vespertina com entrega física e financeira simultânea |

---

## 4. Formulação Matemática das Invariantes do Sistema

O Barramento Central de Liquidação é regido por três leis invariantes determinísticas:

### Invariante LAW-106A: Equilíbrio Contábil Global Estrito

No balanço patrimonial consolidado da câmara e do barramento, a soma dos ativos totais deve ser matematicamente igual à soma dos passivos totais em qualquer instante de tempo, com tolerância zero centavos:

$$\text{Total Ativos}(t) \equiv \text{Total Passivos}(t) \quad \forall t$$
$$\text{Accounting Imbalance} = |\text{Total Ativos} - \text{Total Passivos}| \equiv 0.00$$

Adicionalmente, para qualquer ciclo $C \in \{D0, D1, D2\}$ ou na agregação global consolidada, a compensação multilateral satisfaz a lei de conservação soma-zero:

$$\sum_{i \in \text{Membros}} \text{NetBalance}_i(C) \equiv 0.00$$

### Invariante LAW-106B: Não-Propagação de Default e Absorção em Camadas

Caso um Membro de Compensação não honre sua obrigação financeira líquida devedora, a câmara aciona deterministamente a Cascata de Inadimplência (*Default Waterfall*). A perda de default ($D_{\text{unfunded}}$) é completamente absorvida pelas salvaguardas da câmara sem gerar quebras secundárias:

$$\text{Deficit} = \sum_{k=1}^5 \text{Consumo}_k$$
$$\text{Payout}_{\text{Credores}} = 100.00\%$$
$$\text{CascadeDefaults} = 0$$

Todas as contrapartes credoras não-inadimplentes recebem 100% de seus haveres líquidos, sem aplicação de qualquer desconto compulsório (*haircut* de perdas a terceiros).

### Invariante LAW-106C: Atomicidade de Liquidação Inter-Sistemas

Para qualquer transação composta por múltiplas pernas distribuídas entre os subsistemas integrados (ex.: venda de ações em Equities atrelada a liquidação em Drex, ou hedge de energia elétrica CCEE casado com futuros de juros na câmara de derivativos):

$$\forall tx_a, tx_b \in \text{Group}(g), \quad \text{Status}(tx_a) \equiv \text{Status}(tx_b) \in \{\text{SETTLED}, \text{ROLLED\_BACK}, \text{FAILED}\}$$

Não é admitida a liquidação de uma perna sem a liquidação simultânea de todas as demais pernas do grupo (*All-or-Nothing*).

---

## 5. Estrutura do Default Waterfall em 5 Camadas

```
+-----------------------------------------------------------------------------------------------------------------------+
|                                          DEFAULT WATERFALL DA CÂMARA B3 (FL-100)                                      |
+-----------------------------------------------------------------------------------------------------------------------+
|                                                                                                                       |
|   1. CAMADA L1: MARGENS E GARANTIAS PRÓPRIAS DO INADIMPLENTE                                                          |
|      Colaterais segregados (Moeda soberana BRL, Títulos Públicos, Ações elegíveis do IBOV).                           |
|                                                                                                                       |
|   2. CAMADA L2: COTA DO INADIMPLENTE NO FUNDO DE LIQUIDAÇÃO                                                           |
|      Aporte ordinário pré-financiado realizado pelo próprio membro faltoso.                                           |
|                                                                                                                       |
|   3. CAMADA L3: CAPITAL PRÓPRIO DA B3 DEDICADO AO RISCO (SKIN-IN-THE-GAME / SITG)                                     |
|      Patrimônio líquido pré-mutualizado da infraestrutura (ex.: R$ 500.000.000,00).                                   |
|                                                                                                                       |
|   4. CAMADA L4: FUNDO DE LIQUIDAÇÃO MÚTUO DOS MEMBROS ADIMPLENTES                                                     |
|      Rateio pro-rata entre os membros de compensação solventes conforme cota e perfil de risco.                        |
|                                                                                                                       |
|   5. CAMADA L5: CAPITAL ADICIONAL DA B3 E CHAMADA EXTRAORDINÁRIA DE AVAL (ASSESSMENT CALLS)                           |
|      Capital suplementar da B3 somado a chamadas extraordinárias de liquidez limitadas ao teto estatutário.          |
|                                                                                                                       |
+-----------------------------------------------------------------------------------------------------------------------+
```

---

## 6. Modelo de Classes e Interface Python

O módulo `src/central_settlement_liquidity_bus.py` é 100% aderente à Biblioteca Padrão do Python (*Standard Library* pura), sem dependências de frameworks externos:

- `ClearingSubsystem`: Enumeração com as 10 câmaras.
- `SettlementCycle`: Enumeração dos ciclos (`D0`, `D1`, `D2`).
- `InstructionStatus`: `PENDING`, `SETTLED`, `FAILED`, `ROLLED_BACK`, `WATERFALL_RESOLVED`.
- `SafeguardLayer`: Níveis `L1` a `L5` da cascata.
- `ClearingMember`: Entidade representando o Membro de Compensação, limites e contas.
- `SettlementInstruction`: Registro indivisível de liquidação.
- `MultilateralNetPosition`: Apuração multilateral por participante.
- `ConsolidatedBalanceSheet`: Balanço contábil formal em tempo real.
- `WaterfallStageResolution`: Relatório de consumo por camada de salvaguarda.
- `DefaultResolutionReport`: Instrumento de encerramento do processo de default.
- `B3CentralSettlementBus`: Motor orquestrador com métodos:
  - `register_member(...)`
  - `add_instruction(...)` / `ingest_instructions(...)`
  - `calculate_multilateral_netting(...)` / `calculate_consolidated_netting()`
  - `execute_d0_settlement(...)`
  - `execute_cycle_settlement(...)`
  - `execute_atomic_group(...)`
  - `declare_default_and_resolve(...)`
  - `get_consolidated_balance_sheet()`
  - `verify_invariants(raise_on_error=...)`
  - `to_dict()` / `from_dict()` / `from_fixture()`

---

## 7. Rastreabilidade e Cobertura de Testes

Os testes automatizados em `tests/test_central_settlement_liquidity_bus.py` e a fixture de homologação em `fixtures/central_settlement_liquidity_bus.json` cobrem:
- Validação cadastral dos 10 subsistemas de mercado.
- Conservação estrita do equilíbrio contábil global ($Ativos \equiv Passivos$).
- Compensação multilateral com fechamento soma-zero em $D0$, $D1$ e $D2$.
- Liquidação intradiária direta com débito e crédito instantâneo em contas BACEN/SPI.
- Atomicidade de transações cruzadas (*Cross-Subsystem DvP/PvP*) com commit simultâneo ou reversão integral (*Rollback*).
- Simulação de cenários de estresse severo com ativação sequencial do Default Waterfall até as camadas mútuas ($L4$ e $L5$) sem qualquer quebra em cascata.
- 100% de aprovação e auditoria formal via método `verify_invariants()`.
