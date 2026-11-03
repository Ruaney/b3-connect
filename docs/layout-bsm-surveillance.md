# Especificação Técnica de Auditoria Regulatória e Supervisão de Mercado BSM (FL-030)

## 1. Visão Geral e Contexto Regulatório

A **BSM Supervisão de Mercados** é a entidade autorreguladora do mercado de capitais brasileiro vinculada à **B3 S.A. – Brasil, Bolsa, Balcão**. Operando com autonomia administrativa e orçamentária sob a égide da **Resolução CVM nº 135/2022** (anteriormente Instrução CVM 461/2007) e da **Resolução CVM nº 35/2021** (substituta da Instrução CVM 505/2011), a BSM tem a responsabilidade de fiscalizar, supervisionar e aplicar penalidades a todos os participantes, emissores, operadores e investidores autorizados a negociar nos mercados administrados pela B3.

O fluxo **`FL-030` — Gerar trilha de auditoria e conformidade regulatória BSM** formaliza os requisitos arquiteturais e algorítmicos necessários para:
1. **Trilha de Auditoria Auditável e Determinística**: Registro cronológico de alta precisão (microsegundos) de todas as intenções de negociação, ofertas, cancelamentos, substituições e execuções no **PUMA Trading System** e sistemas de registro.
2. **Identificação Rigorosa do Beneficiário Final**: Conformidade com a Resolução CVM 35 (CVM 505), descaracterizando contas de passagem e identificando a pessoa natural ou jurídica titular final do investimento (CPF/CNPJ/Código CVM).
3. **Identificação e Rastreabilidade de Algoritmos**: Homologação e etiquetagem obrigatória de robôs (*Trading Algorithm ID*) e códigos de operadores autorizados no PQO (*Programa de Qualificação Operacional*).
4. **Detecção Preventiva e Monitoramento de Condutas Abusivas**: Mecanismos analíticos internos para prevenção, triagem e envio de dossiês à BSM sobre condutas vedadas, tais como *Spoofing*, *Layering*, *Wash Trading* e *Churning*.
5. **Enquadramento no Order-to-Trade Ratio (OTR)**: Cálculo normativo da relação entre ofertas enviadas/canceladas e negócios executados para prevenir sobrecarga de infraestrutura e práticas manipuladoras.

```
+-------------------------------------------------------------------------------------------------------------+
|                                  FLUXO DE AUDITORIA E SUPERVISÃO REGULATÓRIA BSM                             |
+-------------------------------------------------------------------------------------------------------------+
|                                                                                                             |
|   +--------------------------+                     +---------------------------+                            |
|   | Ordens / FIX Entrypoint  |                     | Negócios / Drop Copy FIX  |                            |
|   | (NewOrder, Cancel, Mod)  |                     | (ExecutionReport 35=8)    |                            |
|   +--------------------------+                     +---------------------------+                            |
|                 |                                                |                                          |
|                 \-----------------------\   /--------------------/                                          |
|                                         v   v                                                               |
|   +-----------------------------------------------------------------------------------------------------+   |
|   |                               MOTOR DE AUDITORIA E LOG BSM (AuditRecord)                            |   |
|   |                                                                                                     |   |
|   |   - Timestamp PUMA (microssegundos)               - Beneficiário Final (CPF/CNPJ CVM 35)            |   |
|   |   - Operador Homologado (PQO B3)                  - Tag de Algoritmo (Trading Algo ID)              |   |
|   |   - ID Corretora (PN/PNP) & PL                    - Identificador Único de Oferta / Execução        |   |
|   +-----------------------------------------------------------------------------------------------------+   |
|                                                     |                                                       |
|                                                     v                                                       |
|   +-----------------------------------------------------------------------------------------------------+   |
|   |                           MOTOR ANALÍTICO DE VIGILÂNCIA DE MERCADO (BSM Engine)                      |   |
|   +-----------------------------------------------------------------------------------------------------+   |
|         |                                 |                               |                         |       |
|         v                                 v                               v                         v       |
|   +--------------+                 +--------------+                 +--------------+         +--------------+
|   | Wash Trading |                 | Spoofing/OTR |                 |   Layering   |         |   Churning   |
|   | (Auto-Match) |                 | (Cancelem.)  |                 | (Profundid.) |         | (Giro Exc.)  |
|   +--------------+                 +--------------+                 +--------------+         +--------------+
|         \                                 |                               /                         /       |
|          \--------------------------------+------------------------------/-------------------------/        |
|                                           v                                                                 |
|   +-----------------------------------------------------------------------------------------------------+   |
|   |                                 DOSSIÊ DE AUDITORIA E RELATÓRIO BSM                                 |   |
|   |                           (Evidências, Índices OTR, Alertas & Trilha CVM)                           |   |
|   +-----------------------------------------------------------------------------------------------------+   |
|                                                     |                                                       |
|                                                     v                                                       |
|                           [ BSM Supervisão de Mercados / Diretoria de Auto-Regulação ]                      |
+-------------------------------------------------------------------------------------------------------------+
```

---

## 2. Metadados Obrigatórios da Trilha de Auditoria

Em observância às diretrizes conjuntas da B3, BSM e Comissão de Valores Mobiliários (CVM), cada evento de ordem ou execução deve conter metadados estritos que permitam reconstituir a integridade do livro de ofertas em qualquer instante $t$.

### 2.1 Identificação do Beneficiário Final (CVM 35 / CVM 505)
* **CPF / CNPJ do Comitente**: É vedada a consolidação de ordens sem a identificação do titular efetivo.
  - Para pessoas físicas nacionais: CPF (11 dígitos formatados ou canônicos numéricos).
  - Para pessoas jurídicas: CNPJ (14 dígitos).
  - Para investidores não residentes (INR - Resolução CMN 4.373): código CVM do investidor e código de custodiante local.
  - Para Fundos de Investimento (Resolução CVM 175): CNPJ do fundo e da classe/subclasse específica.
* **Validação Cadastral**: Não são permitidos identificadores genéricos como `"HOUSE_ACCOUNT"` ou `"CONSOLIDATED"` em negociações bilaterais sem a devida alocação prévia ou pós-negociação em até $D+0$ (via RTC).

### 2.2 Código de Operador Homologado (Trader ID)
* **Credenciamento PQO**: O operador de mesa, broker ou gestor deve possuir certificação ativa no Programa de Qualificação Operacional da B3.
* **Tag FIX**: Campo FIX `SenderSubID` (Tag 50) ou bloco `NoPartyIDs` (`PartyRole=11` - *Order Origination Trader* / `PartyRole=12` - *Executing Trader*).
* **Identificador de Mesa**: Identifica unicamente o indivíduo perante o cadastro de operadores da B3.

### 2.3 Tag de Algoritmo de Negociação (Trading Algorithm ID)
* **Classificação Regulatória**: Qualquer ordem gerada de maneira automatizada sem intervenção humana discricionária imediata deve portar a marcação regulatória de algoritmo.
* **Tags FIX PUMA**:
  - `Tag 1003` (`TradeID` / `TradingAlgorithmID`) ou `Tag 521` (`ContingencyType`).
  - Bloco `PartyRole=76` (*Order Origination Algorithm*) e `PartyRole=84` (*Execution Algorithm*).
* **Homologação Técnica**: Os algoritmos devem ter descrição de estratégia, limites de risco de túnel e perfil de latência homologados junto à diretoria de risco do participante e informados à B3.

### 2.4 Timestamp de Alta Precisão (Microssegundos)
* **Precisão Mínima**: Microssegundos (`YYYY-MM-DDTHH:MM:SS.ffffffZ` ou `YYYYMMDD-HH:MM:SS.ffffff`).
* **Tags FIX / SBE**:
  - FIX `TransactTime` (`Tag 60`).
  - FIX `SendingTime` (`Tag 52`).
  - SBE PUMA Binary: Timestamp de 64-bit inteiros representando nanossegundos ou microssegundos desde Unix Epoch.
* **Ordenação Estrita**: Permite estabelecer a causalidade física dos eventos e a prioridade de preço/tempo (*Price-Time Priority*) no livro central.

---

## 3. Padrões de Detecção Preventiva de Condutas Abusivas

A BSM monitora o mercado de forma contínua com algoritmos de vigilância em tempo real e em $D+1$. A infraestrutura do participante deve implementar sistemas de prevenção prévia (*First Line of Defense*).

### 3.1 *Spoofing* (Manipulação por Cancelamento Sistemático)
* **Definição**: Inserção deliberada de uma ou mais ordens sem a intenção genuína de executá-las, com o propósito de inflar artificialmente o livro de ofertas em um dos lados (compra ou venda), criando falsa pressão de mercado para favorecer a execução de uma ordem real no lado oposto, seguida do cancelamento imediato da(s) ordem(ns) fictícia(s).
* **Indicadores Analíticos**:
  - Tempo de permanência no book extremamente curto ($< 500\text{ ms}$).
  - Volume cancelado desproporcional ao volume executado (Cancel Ratio $> 80\%$).
  - Inserção de ordem grande no *Bid* (compra) $\rightarrow$ execução de ordem no *Ask* (venda) $\rightarrow$ cancelamento imediato da ordem no *Bid*.

### 3.2 *Layering* (Manipulação em Camadas de Preço)
* **Definição**: Modalidade de spoofing caracterizada pela inserção de múltiplas ordens em múltiplos níveis escalonados de preço (*layers*) fora do melhor preço do book, simulando liquidez profunda e induzindo participantes e algoritmos adversários a movimentar suas cotações.
* **Indicadores Analíticos**:
  - Inserção em cascata de 3 ou mais ordens no mesmo lado com preços sucessivos.
  - Ausência de execução das camadas e cancelamento em massa (bloco) após reação do mercado.

### 3.3 *Wash Trading* (Auto-Negociação sem Troca Efetiva de Titularidade)
* **Definição**: Execução de operações de compra e venda do mesmo instrumento financeiro em que ambas as pontas (compradora e vendedora) pertencem ao mesmo beneficiário final ou são geridas sob coordenação da mesma entidade, sem que ocorra alteração material na titularidade jurídica ou no risco econômico da posição.
* **Propósito Ilegal**: Criar falsa impressão de liquidez, forçar preços de fechamento/ajuste ou manipular taxas de derivativos.
* **Mecanismo de Detecção**:
  - Batimento cruzado de execuções no mesmo ativo onde:
    $$\text{Beneficiário}_{\text{comprador}} == \text{Beneficiário}_{\text{vendedor}}$$
  - Operações cruzadas entre contas distintas de um mesmo conglomerado com mesmo CPF raiz ou CNPJ base.

### 3.4 *Churning* (Giro Excessivo de Carteira)
* **Definição**: Prática em que o intermediário ou operador que detém mandato discricionário sobre a carteira de um cliente realiza um número desmesurado de operações de compra e venda com o intuito primordial de gerar comissões de corretagem e taxas de administração, em detrimento dos objetivos e do patrimônio do investidor.
* **Indicadores Analíticos**:
  - *Turnover Ratio* (Giro Financeiro) elevado: volume financeiro total negociado dividido pelo patrimônio médio do cliente excede limites prudentes em um curto intervalo temporal.
  - *Cost-to-Equity Ratio*: custos operacionais (corretagem, emolumentos B3) consomem percentual relevante da rentabilidade ou do patrimônio líquido da carteira.

---

## 4. Metodologia Normativa do Order-to-Trade Ratio (OTR)

A B3 e a BSM estabelecem limites regulatórios para o indicador **Order-to-Trade Ratio (OTR)**, visando evitar o congestionamento das sessões FIX do PUMA Trading System e reprimir comportamentos predatórios de algoritmos HFT e *quote stuffing*.

### 4.1 Equação de Contagem de Mensagens ($\text{OTR}_{\text{count}}$)
$$\text{OTR}_{\text{count}} = \frac{N_{\text{ordens}} + N_{\text{cancelamentos}} + N_{\text{alterações}}}{\max(1, N_{\text{negócios}})}$$

Onde:
* $N_{\text{ordens}}$: Total de ordens novas submetidas (`ActionType.NEW`).
* $N_{\text{cancelamentos}}$: Total de cancelamentos efetuados (`ActionType.CANCEL`).
* $N_{\text{alterações}}$: Total de substituições/modificações (`ActionType.REPLACE`).
* $N_{\text{negócios}}$: Total de negócios executados (`ActionType.TRADE`).

### 4.2 Equação de Volume de Títulos ($\text{OTR}_{\text{volume}}$)
$$\text{OTR}_{\text{volume}} = \frac{Q_{\text{inserida}} + Q_{\text{cancelada}} + Q_{\text{alterada}}}{\max(1, Q_{\text{executada}})}$$

### 4.3 Faixas de Severidade e Enquadramento BSM
| Limiar OTR | Classificação | Ação de Supervisão |
| :--- | :--- | :--- |
| **OTR $\le 10$** | **Normal / Conforme** | Nenhuma ação necessária. |
| **$10 < \text{OTR} \le 50$** | **Atenção (Low / Medium)** | Monitoramento preventivo; verificação de estratégia de Market Making. |
| **$50 < \text{OTR} \le 100$** | **Elevado (High)** | Notificação ao operador; verificação de logs e justificativa operacional. |
| **$\text{OTR} > 100$** | **Crítico / Abusivo** | Alerta crítico BSM; cobrança de tarifa punitiva B3; abertura de dossiê de auditoria. |

---

## 5. Estrutura do Dossiê de Auditoria BSM

Quando um alerta analítico atinge severidade `HIGH` ou `CRITICAL`, o motor de supervisão gera um dossiê estruturado contendo:
1. **Identificador do Dossiê e Carimbo Temporal**: Protocolo único de fiscalização com data/hora UTC e timezone local de Brasília.
2. **Dados do Participante e Operador**: Código PN/PNP, Participante Liquidante (PL), ID do Operador PQO e identificação do Algoritmo.
3. **Dados do Investidor**: Beneficiário final (CPF/CNPJ desmascarado para a autoridade), conta custódia, categoria CVM.
4. **Resumo Métrico**: OTR apurado, número de mensagens, volume financeiro, taxa de cancelamento.
5. **Cadeia de Evidências Auditáveis**: Lista exata de `record_id` que compõem o evento suspeito, com hashes criptográficos para garantia de não-repúdio.
