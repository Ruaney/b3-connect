# GUIA DEFINITIVO DOS 7 MÓDULOS DA B3: ENTENDIMENTO HOLÍSTICO, REGRAS DE NEGÓCIO E SIMULAÇÃO LOCAL

> **Público-Alvo:** Desenvolvedores, Engenheiros de Software Financeiro, Analistas Quantitativos e Arquitetos de Sistemas.  
> **Filosofia do Atlas:** 100% Código Aberto, Execução Estritamente Local, Zero Dependências Externas (Python Standard Library).  
> **Base Normativa:** CVM, Banco Central do Brasil (BACEN), B3 S.A. e BSM Supervisão de Mercados.

---

## INTRODUÇÃO: POR QUE A B3 É MUITO MAIS QUE UMA "BOLSA DE AÇÕES"?

Para quem olha de fora, a B3 costuma ser associada apenas ao sobe-e-desce de ações como PETR4 e VALE3 no pregão eletrônico. No entanto, no ecossistema financeiro brasileiro, a **B3 S.A. (Brasil, Bolsa, Balcão)** é uma das maiores infraestruturas de mercado do mundo, integrando **7 subsistemas fundamentais** sob o mesmo teto:

1. **PUMA Trading System:** O motor de negociação de alta velocidade para bolsa listada (ações, opções, futuros).
2. **Balcão Organizado NoMe (antiga Cetip):** O registro e depósito de toda a renda fixa privada do país (CDB, LC, CRI, CRA, Debêntures), derivativos customizados (Swap, NDF) e recebíveis de cartões.
3. **Câmara B3 & Risco (Clearing):** A Contraparte Central Garantidora (CCP) que absorve o risco de crédito e executa a compensação multilateral diária.
4. **Central Depositária (CSD):** A guardiã fiduciária dos ativos, onde cada ação ou título é individualizado no CPF ou CNPJ do investidor final (princípio da incomunicabilidade patrimonial).
5. **SPB, BACEN & Drex:** A conexão com o Sistema de Pagamentos Brasileiro para liquidação financeira definitiva de reservas bancárias (STR) e o piloto de ativos digitais soberanos (Drex Fase 2).
6. **Tarifação & Emolumentos:** O modelo de precificação regressiva por ADTV (volume médio diário), casamento de day-trade e incentivos a Formadores de Mercado (PMM).
7. **BSM Supervisão de Mercados:** O braço de autorregulação que monitora cada oferta e negócio nos livros L2/L3 para coibir manipulação de preços e abusos de mercado.

A seguir, dissecamos cada um desses 7 módulos, explicando como funcionam por baixo do capô e como inspecioná-los localmente no projeto.

---

## 1. MÓDULO PUMA TRADING SYSTEM (NEGOCIAÇÃO LISTADA)

O **PUMA Trading System** é a plataforma de negociação eletrônica desenvolvida pela B3 em parceria com a Bolsa de Chicago (CME Group).

### 1.1 Protocolos de Entrada e Disseminação
* **FIX Entrypoint:** Protocolo texto/tag-value padrão FIX 4.4 e FIX 5.0 SP2 sobre TCP/IP persistente. Utilizado pela maioria das corretoras e sistemas institucionais para roteamento de ordens (`NewOrderSingle` - MsgType `D`).
* **Binary Entrypoint (SBE):** Protocolo binário ultrarrápido (*Simple Binary Encoding v2*) desenhado para negociação de alta frequência (HFT) e DMA (Direct Market Access). O processamento ocorre em microssegundos com deserialização *zero-allocation*.
* **UMDF 2.0 (Market Data):** Feed UDP Multicast distribuído em canais paralelos (Feed A e Feed B) para transmissão do livro de ofertas (*Market By Price* L2 e *Market By Order* L3).

### 1.2 Mecanismos de Proteção e Leilões
* **Túneis de Preço:** O PUMA rejeita automaticamente qualquer ordem com preço fora do túnel de rejeição (ex: ±10% do preço de referência) para evitar o erro do "dedo gordo" (*Fat-Finger*).
* **Leilão de Fechamento (Closing Cross) & Ordens MOC/LOC:**
  * **Freeze Window (Janela de Congelamento):** Nos minutos finais que antecedem o fechamento, o PUMA congela o livro: **cancelamentos e alterações são sumariamente rejeitados** (Lei LAW-104A).
  * **Prioridade MOC sobre LOC:** Ordens *Market-On-Close* têm prioridade absoluta de execução sobre ordens *Limit-On-Close* na formação do Preço Teórico de Fechamento (PTP).

---

## 2. MÓDULO BALCÃO ORGANIZADO NOME (CETIP)

Diferente do mercado de bolsa (onde os contratos são padronizados), o **Mercado de Balcão** lida com instrumentos customizados e emissões privadas entre instituições financeiras, empresas e investidores.

### 2.1 Renda Fixa Bancária e Corporativa
* **CDB, RDB, LCI, LCA e Letras Financeiras (LF):** Títulos emitidos por bancos para captação de recursos. O NoMe registra a emissão, calcula a curva de juros (Pré ou DI) e escritura a custódia.
* **Debêntures, CRI e CRA:** Instrumentos de crédito privado corporativo e securitização imobiliária/agropecuária.

### 2.2 Derivativos Customizados (Swaps e NDF)
* Operações bilaterais de troca de rentabilidade (ex: Taxa Pré vs Variação Cambial ou DI vs IPCA) registradas sem necessidade de padronização em lote, com cálculo diário de PU e marcação a mercado.

### 2.3 Sistema Registrador de Recebíveis de Cartão (Resolução CMN 4.734)
* A B3 opera o registrador central onde todas as vendas de maquininhas de cartão de crédito/débito no Brasil são registradas como Unidades de Recebíveis (UR).
* **Trava de Domicílio Bancário e Gravames:** Quando um lojista toma um empréstimo num banco oferecendo suas vendas futuras de cartão como garantia, a B3 registra o gravame e "trava" o pagamento para que o dinheiro caia obrigatoriamente na conta do credor (Lei LAW-018).

---

## 3. MÓDULO CÂMARA B3 & GESTÃO DE RISCO (CLEARING / CCP)

A **Câmara B3** atua como Contraparte Central Garantidora (*Central Counterparty* - CCP). Quando uma compra e venda é casada no PUMA:
1. **Novação Contratual:** O vínculo jurídico direto entre comprador e vendedor é extinto. A Câmara passa a ser a compradora para o vendedor e a vendedora para o comprador (Lei LAW-004).
2. **Metodologia de Risco CORE (Closeout Risk Evaluation):** Calcula o risco de liquidação considerando cenários de estresse de preços, volatilidade, correlação entre ativos e liquidez de mercado, exigindo garantias e margens dinâmicas.
3. **Default Waterfall (Cascata de Salvaguardas):** Em caso de falência de um membro compensador, as perdas são absorvidas em camadas estritas:
   $$\text{Margem do Inadimplente} \rightarrow \text{Fundo de Liquidação do Inadimplente} \rightarrow \text{Capital da B3 (Skin-in-the-game)} \rightarrow \text{Fundo Mútuo}$$
4. **Compensação Multilateral Contínua (Netting):** Reduz em mais de 75% o volume de dinheiro que precisa transitar no sistema bancário, consolidando todos os débitos e créditos de cada corretora em um único valor líquido diário soma-zero (Lei LAW-006).

---

## 4. MÓDULO CENTRAL DEPOSITÁRIA (CSD)

A Central Depositária da B3 é o cofre digital dos valores mobiliários brasileiros.

### 4.1 Princípio da Incomunicabilidade Patrimonial
Diferente de sistemas estrangeiros (como o modelo *Omnibus* dos EUA), no Brasil a regulamentação exige que **cada investidor final possua uma conta individualizada vinculada ao seu CPF ou CNPJ**.
* Se a corretora quebrar, os ativos do cliente estão protegidos e intocados na Central Depositária da B3, não integrando a massa falida da corretora.

### 4.2 Arquivos Posicionais IMBARQ
Diariamente, no fechamento do dia, a B3 disponibiliza para as corretoras e custodiantes os arquivos oficiais de conciliação:
* **IMBARQ004:** Posições financeiras a liquidar em D0, D+1 e D+2.
* **IMBARQ006:** Posições abertas de contratos e derivativos na Câmara.
* **IMBARQ011:** Posições de empréstimo de títulos (BTC).
* **IMBARQ012:** Extrato de custódia física de ações (Disponível, Bloqueado, Garantia, Judicial).
* **IMBARQ014:** Colaterais e garantias depositadas.
* **IMBARQ018:** Cronograma e valores de proventos corporativos (Dividendos e JCP).

---

## 5. MÓDULO SPB, BANCO CENTRAL & DREX

O dinheiro que transita na B3 se conecta diretamente com as contas de reservas bancárias do Banco Central do Brasil.

### 5.1 Sistema de Transferência de Reservas (STR)
* **Mensagem LDL0001:** No horário regulamentar (18:30 às 18:45), a B3 transmite ao BACEN a mensagem `LDL0001` contendo o débito e crédito multilateral de cada banco liquidante para fechamento no STR (Lei LAW-007).

### 5.2 Pix Margem SPI (24/7)
* Para chamadas de margem intradiárias e aportes urgentes de garantia, a B3 utiliza o SPI (Sistema de Pagamentos Instantâneos) com liquidação em menos de 10 segundos e rastreabilidade por `EndToEndId` unívoco (Lei LAW-090).

### 5.3 Piloto Drex Fase 2 (Liquidação Atômica DvP/PvP)
* Na infraestrutura DLT Hyperledger Besu, a B3 participa dos testes de Títulos Públicos Federais tokenizados (TPFt) em padrão ERC-1155 contra Real Digital (CBDC de atacado). A liquidação ocorre via contratos inteligentes HTLC (*Hashed Time-Lock Contract*), garantindo que a entrega do título e o pagamento em moeda digital ocorram na mesma transação atômica indivisível (*All-or-None* - Lei LAW-089A).

---

## 6. MÓDULO DE TARIFAÇÃO & EMOLUMENTOS

A B3 calcula seus custos operacionais de forma determinística e transparente:

### 6.1 Escadinha Regressiva de ADTV
* Quanto maior o volume financeiro médio diário (ADTV) movimentado por uma instituição ou investidor ao longo do mês, menores são as alíquotas cobradas (Tiers 1 a 7).
* Alíquotas normais variam de **0,0300%** (Tier 1) até **0,0150%** (Tier 7).
* Em operações de **Day-Trade** (compra e venda do mesmo ativo no mesmo dia), as alíquotas caem para **0,0200% a 0,0080%**, incentivando a liquidez intradiária.

### 6.2 Regime de Formador de Mercado (PMM)
* Agentes credenciados que mantêm ofertas ativas de compra e venda nos livros com spread máximo regulamentar têm **100% de isenção de emolumentos** e descontos em taxas de liquidação.

---

## 7. MÓDULO BSM SUPERVISÃO DE MERCADOS

A **BSM Supervisão de Mercados** é o braço autorregulador da B3, fiscalizando todos os negócios em tempo real e auditando as corretoras.

### 7.1 Algoritmos de Vigilância de Abusos
* **Wash Trading (Auto-Negociação):** Detecta negócios fechados onde a ponta compradora e a ponta vendedora pertencem ao mesmo beneficiário final (CPF/CNPJ), o que é vedado por simular liquidez artificial (Lei LAW-041).
* **Spoofing e Layering:** Identifica robôs que inserem grandes lotes de ordens distantes do book com a intenção de cancelá-las em microssegundos após induzir o mercado ao erro.
* **OTR (Order-to-Trade Ratio):** Mede a quantidade de ordens enviadas/canceladas para cada negócio efetivamente fechado. Relações desproporcionais geram multas e tarifação punitiva.

---

## COMO EXECUTAR E EXPERIMENTAR OS 7 MÓDULOS NO TERMINAL

Todos os 7 módulos podem ser executados, testados e inspecionados no ambiente local através dos comandos da CLI `b3-atlas`:

```bash
# 1. Visualizar o mapa arquitetural completo dos 7 módulos
b3-atlas modules

# 2. Simular um dia inteiro de mercado B3 (E2E cobrindo os 7 módulos em < 1 milissegundo)
b3-atlas simulate-day

# 3. Simular uma negociação de ações no PUMA com detecção de túnel de preço
b3-atlas trace-trade --ticker VALE3 --price 120.00 --fat-finger

# 4. Simular registro de Renda Fixa de Balcão NoMe com trava de gravame
b3-atlas trace-balcao --asset CDB --notional 1000000 --with-lien

# 5. Ler qualquer um dos 80 manuais e especificações oficiais gravados localmente em raw_docs/
b3-atlas doc-read SRC-006    # PUMA FIX Entrypoint
b3-atlas doc-read SRC-011    # NoMe Swaps e Derivativos
b3-atlas doc-read SRC-078    # Regras de Leilão de Fechamento MOC/LOC

# 6. Executar o verificador formal de todas as 84 Leis de Conservação
b3-verify
```
