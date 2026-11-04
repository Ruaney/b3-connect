# Especificação Técnica de Governança de Negociação Algorítmica e HFT (FL-047)
## Diretrizes CVM 505 / CVM 35, MiFID II / RTS 25, Ratios OTR e Supervisão BSM / B3

---

## 1. Visão Geral e Contexto Regulatório

O crescimento exponencial das operações automatizadas no mercado de capitais brasileiro impõe exigências rigorosas de transparência, rastreabilidade e integridade sistêmica sobre os participantes de negociação da **B3 S.A. – Brasil, Bolsa, Balcão**. Sob a égide da **Resolução CVM nº 35/2021** (que consolidou e substituiu a Instrução CVM nº 505/2011) e em estrita harmonia com as melhores práticas internacionais preconizadas pela **IOSCO** e pelo regime europeu **MiFID II / RTS 25 (Regulatory Technical Standard 25 - Commission Delegated Regulation (EU) 2017/574)**, a B3 e a **BSM Supervisão de Mercados** implementam uma infraestrutura mandatória de governança algorítmica.

O fluxo **`FL-047` — Governança de Negociação Algorítmica e HFT** define os requisitos arquiteturais, protocolares e matemáticos para:
1. **Identificação e Rastreabilidade Regulatória Estrita**: Etiquetagem obrigatória em cada mensagem FIX/SBE enviada ao PUMA Trading System, distinguindo sem ambiguidade ordens manuais discricionárias, algoritmos convencionais (ex.: VWAP, TWAP, POV) e algoritmos de Alta Frequência (*High Frequency Trading* - HFT).
2. **Controle de Order-to-Trade Ratio (OTR) Dinâmico**: Mensuração contínua e determinística da relação entre ordens inseridas/alteradas e negócios efetivamente executados, tanto por volume de mensagens quanto por quantidade de contratos/ativos ofertados.
3. **Política de Tarifação Punitiva de OTR da B3**: Aplicação de sobretaxas progressivas sobre participantes e algoritmos que sobrecarregam o livro de ofertas (*order book*) sem prover liquidez efetiva, desencorajando estratégias predatórias ou descalibradas.
4. **Sincronização de Relógios e Trilha de Auditoria (RTS 25)**: Rastreabilidade temporal com precisão de microssegundos em relação à escala UTC (Tempo Universal Coordenado), garantindo tolerância de desvio (*clock drift*) inferior a 100 microssegundos para fluxos HFT.

```
+-------------------------------------------------------------------------------------------------------------+
|                            FLUXO DE GOVERNANÇA ALGORÍTMICA, OTR E AUDITORIA RTS 25 (FL-047)                 |
+-------------------------------------------------------------------------------------------------------------+
|                                                                                                             |
|   +--------------------------+                     +---------------------------+                            |
|   | Ordens HFT / DMA / Algo  |                     |  Ordens Manuais de Mesa   |                            |
|   | Tag 1031=D, Tag 57=ALGO  |                     |  Tag 1031=W, Tag 1724=1   |                            |
|   +--------------------------+                     +---------------------------+                            |
|                 |                                                |                                          |
|                 \-----------------------\   /--------------------/                                          |
|                                         v   v                                                               |
|   +-----------------------------------------------------------------------------------------------------+   |
|   |                               MOTOR DE VALIDAÇÃO REGULATÓRIA FIX / RTS 25                            |   |
|   |                                                                                                     |   |
|   |   - Tag 1031 (CustOrderHandlingType)              - Tag 57 (TargetSubID / TradingAlgorithmID)      |   |
|   |   - Tag 1724 (OrderOriginationRole)               - Tag 8015 (ExecutingTrader / PQO)                |   |
|   |   - Sincronização UTC RTS 25: Tolerância < 100 µs (HFT) / < 1 ms (Algo) / < 1 s (Manual)           |   |
|   +-----------------------------------------------------------------------------------------------------+   |
|                                                     |                                                       |
|                                                     v                                                       |
|   +-----------------------------------------------------------------------------------------------------+   |
|   |                           MOTOR DETERMINÍSTICO DE ORDER-TO-TRADE RATIO (OTR)                        |   |
|   +-----------------------------------------------------------------------------------------------------+   |
|         |                                 |                               |                         |       |
|         v                                 v                               v                         v       |
|   +---------------+               +---------------+               +---------------+         +---------------+
|   | Ações Ibov    |               |  Small Caps   |               |    Futuros    |         |    Opções     |
|   | (Large Caps)  |               |  (Fora Ibov)  |               |  DI1/DOL/IND  |         | (Ações/Fut)   |
|   | OTRvol <= 150 |               | OTRvol <= 300 |               | OTRvol <= 100 |         | OTRvol <= 500 |
|   +---------------+               +---------------+               +---------------+         +---------------+
|         \                                 |                               /                         /       |
|          \--------------------------------+------------------------------/-------------------------/        |
|                                           v                                                                 |
|   +-----------------------------------------------------------------------------------------------------+   |
|   |                            APURAÇÃO DE TARIFAS PUNITIVAS DE OTR (B3 SURCHARGE)                      |   |
|   |   - Cálculo de Ordens Excedentes: Excess = Max(0, N_ordens - (OTR_max * Max(1, N_trades)))           |   |
|   |   - Faixas Progressivas: Tier 1 (1.0x-1.5x), Tier 2 (1.5x-2.5x), Tier 3 (>2.5x + Taxa Grave)       |   |
|   +-----------------------------------------------------------------------------------------------------+   |
|                                                     |                                                       |
|                                                     v                                                       |
|                           [ Relatório de Governança B3 / Dossiê de Vigilância BSM ]                         |
+-------------------------------------------------------------------------------------------------------------+
```

---

## 2. Identificação e Rastreabilidade Obrigatória de Algoritmos (CVM 505 / RTS 25)

A Resolução CVM nº 35/2021 exige que os intermediários mantenham sistemas capazes de identificar a origem de qualquer ordem e a cadeia de custódia e decisão. Quando a ordem decorre de negociação algorítmica, o protocolo FIX B3 (tags da mensagem `NewOrderSingle` 35=D e `OrderCancelReplaceRequest` 35=G) estabelece os seguintes campos mandatórios:

| Tag FIX | Nome do Campo | Tipo | Obrigatório | Descrição Técnica & Valores Padronizados |
| :--- | :--- | :--- | :--- | :--- |
| **`1031`** | `CustOrderHandlingType` | `String (char)` | **Sim** | Canal de entrada e manuseio da ordem:<br>• `D` = Algorithmic (roteamento automatizado / robô)<br>• `N` = Direct Market Access (DMA tradicional)<br>• `W` = Desk / Manual (mesa de operações / discricionário humano)<br>• `Y` = Direct Market Access via Provedor Autorizado (Sponsored DMA) |
| **`57`** | `TargetSubID` / `TradingAlgorithmID` | `String` | **Condicional** | Identificador unívoco do algoritmo registrado e homologado perante o participante e a B3 (ex.: `ALGO-MM-001`, `ALGO-STATARB-99`).<br>**Mandatório quando Tag 1031 = 'D' ou categoria é Algorítmica/HFT.** Proibido para ordens 100% manuais de mesa. |
| **`1724`** | `OrderOriginationRole` | `Int` | **Sim** | Papel da entidade originadora no fluxo decisório:<br>• `1` = Agency (Ordem por conta de terceiros / cliente institucional)<br>• `2` = Principal (Carteira própria / Prop Desk)<br>• `3` = Riskless Principal<br>• `4` = Algorithm / DEA Investment Firm (Ordem decidida diretamente por inteligência algorítmica)<br>• `5` = Systematic Internaliser |
| **`8015`** | `ExecutingTrader` | `String` | **Sim** | Código do operador homologado no PQO B3 (ex.: `PQO-8841`) ou identificador cadastral da licença do algoritmo operador na mesa de operações. |

### 2.1. Diferenciação de Classes de Algoritmo

A taxonomia adotada na governança B3/BSM categoriza o fluxo em três classes operacionais:

1. **`MANUAL_DISCRETIONARY` (Manual Discricionária)**:
   - Ordem inserida manualmente por operador humano credenciado no PQO via terminal de negociação ou Home Broker.
   - Tag 1031 = `'W'`.
   - Ausência de `TradingAlgorithmID` (Tag 57 = None/Empty).
   - Tolerância de sincronização temporal RTS 25: até 1 segundo (1.000.000 µs).

2. **`ALGORITHMIC_STANDARD` (Negociação Algorítmica Convencional)**:
   - Algoritmos de execução de ordens baseados em parâmetros pré-programados de fatiamento temporal ou volume (ex.: TWAP, VWAP, Percentage of Volume - POV, Iceberg, Smart Order Routing).
   - Tag 1031 = `'D'`, Tag 1724 = `1` ou `4`.
   - Tag 57 (`TradingAlgorithmID`) obrigatória e cadastrada.
   - Frequência de mensagens moderada, sem requisitos de latência de sub-milissegundo.
   - Tolerância de sincronização temporal RTS 25: até 1 milissegundo (1.000 µs).

3. **`HIGH_FREQUENCY_TRADING` (Alta Frequência - HFT)**:
   - Subclasse de negociação algorítmica caracterizada por:
     a) Infraestrutura de co-location ou proximidade física extrema dos servidores do PUMA Matching Engine;
     b) Tomada de decisão e envio automatizado de ofertas sem intervenção humana em escala de sub-milissegundo;
     c) Alta taxa intradiária de cotações, cancelamentos e alterações;
     d) Posição patrimonial fechada ou neutralizada no final do pregão (*end-of-day flat position*);
     e) Estratégias típicas de formador de mercado (*Market Making*), arbitragem estatística de latência (*latency arbitrage*) ou liquidez cruzada.
   - Tag 1031 = `'D'`, Tag 1724 = `4` (ou `2` para prop desks).
   - Tag 57 obrigatória e auditada pela BSM.
   - Tolerância de sincronização temporal RTS 25: rigorosamente **< 100 microssegundos (< 100 µs)**.

---

## 3. Order-to-Trade Ratio (OTR) Dinâmico da B3

O **Order-to-Trade Ratio (OTR)** é a métrica regulatória e tarifária oficial que avalia a eficiência do envio de mensagens ao livro de ofertas, contrapondo a quantidade de eventos ou cotas ofertadas ao volume efetivamente convertido em negócios fechados.

### 3.1. Fórmulas de Cálculo

Conforme metodologia B3 e alinhamento com a diretriz MiFID II RTS 9:

#### A) OTR por Volume de Mensagens ($OTR_{vol}$)
Mede o número de eventos de inserção e alteração de ordens necessários para gerar uma execução:
$$OTR_{vol} = \frac{\text{Total de Ordens Inseridas e Alteradas}}{\max(1, \text{Negócios Executados})} = \frac{N_{new} + N_{replace}}{\max(1, N_{trades})}$$

*Nota*: Caso nenhum negócio tenha sido executado ($N_{trades} = 0$), o denominador assume o piso determinístico de `1`, refletindo o risco integral de cotações sem liquidez (ordens fantasmas).

#### B) OTR por Quantidade de Títulos/Contratos ($OTR_{qty}$)
Mede a quantidade acumulada de ativos ou contratos ofertados em relação à quantidade executada:
$$OTR_{qty} = \frac{\text{Quantidade Total Ofertada}}{\max(1, \text{Quantidade Executada})} = \frac{\sum Q_{new} + \sum Q_{replace}}{\max(1, \sum Q_{executed})}$$

### 3.2. Tetos Máximos de OTR por Classe de Ativo (`OtrAssetLimit`)

Os tetos de OTR são parametrizados pela B3 de acordo com a liquidez, volatilidade e profundidade natural de cada segmento de mercado:

| Classe de Ativo | Exemplos Típicos | $OTR_{vol}$ Máximo | $OTR_{qty}$ Máximo | Justificativa de Mercado e Mecânica |
| :--- | :--- | :---: | :---: | :--- |
| **Ações Ibovespa (Large Caps)** | `PETR4`, `VALE3`, `ITUB4` | **150 : 1** | **250 : 1** | Livro ultra-líquido com múltiplos participantes e grande profundidade; sobrecarga de cancelamentos é estritamente reprimida. |
| **Small Caps (Fora do Ibovespa)** | `CASH3`, `MGLU3`, `LWSA3` | **300 : 1** | **500 : 1** | Menor liquidez orgânica e spreads mais amplos demandam maior reposicionamento de cotações para captura de ordens. |
| **Futuros Financeiros** | `DI1F29`, `DOLG27`, `WINF27` | **100 : 1** | **150 : 1** | Livros de altíssima velocidade e lotes expressivos; risco sistêmico de fila fictícia impõe limites severos. |
| **Opções Listadas** | `PETRB300`, `VALEA650` | **500 : 1** | **1.000 : 1** | Complexidade de precificação de gregas (Delta/Gamma) e cotação de múltiplas séries simultâneas por Market Makers. |

---

## 4. Política de Tarifação e Penalidades Punitivas de OTR da B3

Para mitigar a externalidade negativa imposta pelos robôs de alta frequência que inundam o sistema de negociação sem contribuir proporcionalmente com liquidez executada, a B3 aplica uma **Tarifa de Mensageria Excedente de OTR**.

### 4.1. Apuração de Ordens Excedentes
A quantidade de ordens excedentes em volume ($Excess_{orders}$) é apurada pela fórmula:
$$MaxAllowed_{orders} = \lfloor OTR_{vol\_limit} \times \max(1, N_{trades}) \rfloor$$
$$Excess_{orders} = \max(0, (N_{new} + N_{replace}) - MaxAllowed_{orders})$$

### 4.2. Estrutura Progressiva de Tarifação

A sobretaxa é calculada em faixas progressivas com base na Razão de Excesso ($E_r = \frac{OTR_{vol}}{OTR_{vol\_limit}}$):

```
+-------------------------------------------------------------------------------------------------------------+
|                                TABELA PROGRESSIVA DE TARIFAÇÃO DE OTR B3                                    |
+---------------------+-------------------+-------------------------------+-----------------------------------+
| Faixa de Excesso    | Razão de Excesso  | Alíquota por Ordem Excedente  | Multa Fixa Administrativa         |
+---------------------+-------------------+-------------------------------+-----------------------------------+
| Tier 1 (Moderado)   | 1.0 < E_r <= 1.5  | R$ 0,05                       | Isento                            |
| Tier 2 (Grave)      | 1.5 < E_r <= 2.5  | R$ 0,15                       | Isento                            |
| Tier 3 (Crítico)    | E_r > 2.5         | R$ 0,35                       | R$ 1.000,00 por algoritmo/ativo   |
+---------------------+-------------------+-------------------------------+-----------------------------------+
```

#### Surcharge de Quantidade Excedente ($Excess_{qty}$):
Caso o algoritmo também viole o teto de quantidade ($OTR_{qty} > OTR_{qty\_limit}$), aplica-se uma taxa suplementar de **R$ 0,001 por unidade/contrato ofertado em excesso**:
$$Excess_{qty} = \max(0, Q_{offered} - (OTR_{qty\_limit} \times \max(1, Q_{executed})))$$
$$Surcharge_{qty} = Excess_{qty} \times R\$\,0,001$$

$$\text{Penalidade Total (BRL)} = (Excess_{orders} \times \text{Alíquota}_{Tier}) + \text{Multa Fixa}_{Tier} + Surcharge_{qty}$$

---

## 5. Trilha de Auditoria e Sincronização UTC RTS 25

O RTS 25 (MiFID II) e as diretrizes operacionais da B3 exigem que todos os sistemas participantes (gateways, motores de risco e servidores algorítmicos) sincronizem seus relógios de sistema com uma fonte rastreável ao **UTC (BIPM)** ou **UTC(ON) - Observatório Nacional do Brasil**.

### 5.1. Níveis de Tolerância de Desvio (*Clock Drift*)

| Categoria Operacional | Tolerância Máxima de Desvio ($|\Delta t|$) | Resolução Mínima do Timestamp |
| :--- | :---: | :---: |
| **High Frequency Trading (HFT)** | **< 100 microssegundos (100 µs)** | **1 microssegundo (1 µs)** |
| **Algoritmo Standard (VWAP, TWAP, SOR)** | **< 1.000 microssegundos (1 ms)** | **1 milissegundo (1 ms)** |
| **Ordens Manuais Discricionárias** | **< 1.000.000 microssegundos (1 s)** | **1 segundo (1 s)** |

### 5.2. Formato Canônico de Timestamp
O timestamp de auditoria de cada evento deve conter resolução completa em microssegundos:
`YYYY-MM-DDTHH:MM:SS.ffffffZ` (ou com deslocamento oficial brasileiro `-03:00` / BRT devidamente sincronizado).

Eventos gerados por algoritmos HFT cujo desvio absoluto em relação à fonte de tempo exceder 100 µs são classificados como **Infracionários com Risco Crítico de Trilha**, ensejando notificação mandatória à BSM e possibilidade de desativação cautelar do conector pelo participante.

---

## 6. Fluxo de Supervisão e Alertas BSM

A **BSM Supervisão de Mercados** realiza o cruzamento contínuo das tags regulatórias e dos dados de Drop Copy e PUMA Trade Capture:

1. **Validação Sintática e Semântica de Tags**:
   - Rejeição ou notificação de ordens algorítmicas desprovidas de Tag 57 (`TradingAlgorithmID`) ou Tag 8015 (`ExecutingTrader`).
   - Identificação de fraudes de rotulagem (ex.: etiquetar ordem HFT como manual `W` para tentar contornar limites de OTR e tolerâncias de clock drift).
2. **Vigilância Intradiária de OTR**:
   - Apuração contínua dos índices $OTR_{vol}$ e $OTR_{qty}$ em janelas móveis e consolidado do pregão.
   - Emissão de avisos de advertência em 80% do limite normativo (*Early Warning Threshold*).
   - Cobrança de tarifas punitivas e instauração de Processo Administrativo Disciplinar (PAD) em casos de reincidência sistemática em Tier 3.
3. **Cruzamento com Tipologias de Manipulação**:
   - O excesso crônico de OTR combinado com alta proporção de cancelamentos na ponta de compra ou venda é correlacionado a práticas tipificadas como *Spoofing* ou *Layering* (CVM 135 / CVM 35).

---

## 7. Referências Normativas

- **Resolução CVM nº 35/2021**: Regras de conduta e deveres de verificação dos intermediários.
- **Instrução CVM nº 505/2011**: Diretrizes históricas de cadastro, ordens e identificação.
- **Resolução CVM nº 135/2022**: Mercados regulamentados e autorregulação da B3/BSM.
- **MiFID II / RTS 25 (Regulation (EU) 2017/574)**: Requisitos de sincronização de relógios.
- **Manual de Tarifação e Catálogo de Tarifas B3**: Política de tarifas de mensageria e OTR.
- **FIX Protocol 4.4 / 5.0 SP2**: Tag definitions para Algorithmic Trading e Trade Capture Report.
