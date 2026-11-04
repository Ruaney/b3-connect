# Especificação Técnica: Simulador e Rebalanceamento de Índices B3 (FL-066)

## 1. Visão Geral e Contexto de Mercado

Os índices de ações da B3 (como o **Ibovespa**, **IBrX-100**, **SMLL - Small Cap** e **IDIV - Índice Dividendos**) funcionam como os principais termômetros do mercado de capitais brasileiro, servindo de benchmark para fundos de investimento, precificação de derivativos no PUMA Trading System e lastro para Exchange Traded Funds (ETFs).

As carteiras teóricas desses índices passam por um processo periódico e mandatório de **rebalanceamento quadrimestral**, com vigência definida nos seguintes períodos:
- **1º Quadrimestre**: Janeiro a Abril (divulgação das prévias ao longo de Dezembro);
- **2º Quadrimestre**: Maio a Agosto (divulgação das prévias ao longo de Abril);
- **3º Quadrimestre**: Setembro a Dezembro (divulgação das prévias ao longo de Agosto).

O motor do fluxo **FL-066** modela deterministicamente todas as etapas matemáticas, estatísticas e regulatórias do rebalanceamento conforme a metodologia oficial divulgada pela B3.

---

## 2. Metodologia de Elegibilidade e Seleção

### 2.1. Filtros Mandatórios de Elegibilidade
Para que uma ação ou unit seja elegível a qualquer índice acionário da B3, ela deve satisfazer cumulativamente:
1. **Presença em Pregões**: Ter estado presente em pelo menos **95%** dos pregões realizados no período de observação (últimos 3 quadrimestres / 12 meses anteriores).
2. **Representatividade de Volume**: Participar com no mínimo **0,1%** do volume financeiro total negociado no mercado à vista no período.
3. **Filtro Penny Stock**: Não ser classificada como "Penny Stock" (preço médio ou de fechamento no período não pode ser inferior a R$ 1,00).
4. **Filtro de Recuperação Judicial**: Não estar sob regime de recuperação judicial, recuperação extrajudicial, administração especial temporária ou processo de falência.

### 2.2. Cálculo do Índice de Negociabilidade (IN)
O **Índice de Negociabilidade (IN)** mede a liquidez relativa combinando a representatividade do número de negócios e do volume financeiro:

$$IN_i = \sqrt{\left(\frac{n_i}{N}\right) \times \left(\frac{v_i}{V}\right)}$$

Onde:
- $n_i$: Número de negócios do ativo $i$ no período de observação no mercado à vista;
- $N$: Número total de negócios de todas as ações elegíveis no mercado à vista;
- $v_i$: Volume financeiro transacionado pelo ativo $i$ no período de observação (em R$);
- $V$: Volume financeiro total transacionado por todas as ações elegíveis no mercado à vista (em R$).

A fatia relativa de negociabilidade do ativo no universo elegível é:
$$IN\_share_i = \frac{IN_i}{\sum_{j \in \text{Elegíveis}} IN_j}$$

### 2.3. Corte dos 85% de Representatividade Acumulada (Ibovespa)
Os ativos elegíveis são ordenados de forma estritamente decrescente pelo seu $IN_i$. São selecionadas para compor o **Ibovespa** as ações que, cumulativamente, correspondam a **85%** do somatório total dos Índices de Negociabilidade:
$$\sum_{j=1}^k IN\_share_j \ge 0.85$$
O ativo marginal $k$ cuja adição faz a soma atingir ou superar 85% é incluído na carteira; os ativos subsequentes são cortados por insuficiência de negociabilidade.

### 2.4. Critérios para Outros Índices
- **IBrX-100**: Seleciona as 100 ações com maior $IN_i$ que atendem a todos os filtros de elegibilidade.
- **SMLL (Small Cap)**: Seleciona ações elegíveis fora do bloco dos 85% de maior valor de mercado, focando no segmento de média e baixa capitalização.
- **IDIV (Índice Dividendos)**: Filtra os ativos elegíveis com histórico positivo de proventos e classifica-os pelo Dividend Yield (DY), selecionando os maiores pagadores.

---

## 3. Ponderação, Teto Regulatório (Capping) e Conservação de Pesos

### 3.1. Valor de Mercado do Free Float
A ponderação inicial de cada ativo é proporcional ao seu valor de mercado em circulação (Free Float):
$$FFCap_i = Q_{i,\text{free\_float}} \times P_i$$

Para o IDIV, o fator base é ponderado pelo retorno de dividendos:
$$WeightFactor_i^{\text{IDIV}} = FFCap_i \times (1 + 10 \times DY_i)$$

O peso não limitado (uncapped) é:
$$W_i^{(0)} = \frac{FFCap_i}{\sum_{j \in \text{Carteira}} FFCap_j}$$

### 3.2. Algoritmo Iterativo de Capping (Teto de 20%)
Para evitar concentração excessiva e risco sistêmico em um único emissor, a B3 estabelece um teto regulatório de concentração de peso:
$$W_i \le Cap\_Limit \quad (\text{onde } Cap\_Limit = 20.00\%)$$

Quando um ou mais ativos excedem o teto:
1. Os ativos que excedem $Cap\_Limit$ têm seus pesos fixados em exatamente $Cap\_Limit$.
2. O peso excedente total é redistribuído proporcionalmente entre os ativos ainda não limitados:
   $$W_k = (1.0 - \sum_{m \in Capped} Cap\_Limit) \times \frac{FFCap_k}{\sum_{u \in Uncapped} FFCap_u}$$
3. O teste é repetido iterativamente até que nenhum ativo viole o teto regulatório.

### 3.3. Lei de Conservação Estrita de Pesos
O motor aplica ajuste residual determinístico no ativo de maior peso que não esteja no teto para absorver resíduos numéricos decorrentes do quantize em 8 casas decimais (`0.00000001`):

$$\sum_{i=1}^M W_i \equiv 1.00000000 \quad (100.000000\%)$$

---

## 4. Ciclo Quadrimestral de Prévias e Continuidade Patrimonial

### 4.1. Calendário Determinístico das Três Prévias
Antes da entrada em vigor da nova carteira no primeiro dia útil do quadrimestre subsequente, a B3 divulga 3 prévias sucessivas:
1. **1ª Prévia**: Divulgada no 1º dia útil do mês anterior à vigência (ex: 1º dia útil de dezembro para a carteira JAN-ABR).
2. **2ª Prévia**: Divulgada no 1º dia útil da segunda quinzena do mês anterior (ex: dia 16 de dezembro).
3. **3ª Prévia / Carteira Definitiva**: Divulgada no penúltimo pregão de vigência da carteira anterior, vigorando no fechamento do último pregão.

### 4.2. Invariante do Divisor e Continuidade do Nível do Índice
As quantidades teóricas de ações ($Q_i$) são calculadas a partir do patrimônio financeiro da carteira:
$$Q_i = \left\lfloor \frac{W_i \times V_{\text{carteira}}}{P_i} + 0.5 \right\rfloor$$

Para garantir que o evento de rebalanceamento não introduza saltos artificiais de preço (drift zero), o **Divisor do Índice ($D$)** é ajustado exatamente na proporção dos valores teóricos:
$$D_{\text{novo}} = D_{\text{antigo}} \times \frac{\sum_{i=1}^{M_{\text{novo}}} Q_{i,\text{novo}} \times P_i}{\sum_{j=1}^{M_{\text{antigo}}} Q_{j,\text{antigo}} \times P_j}$$

Garantindo que:
$$\text{Nível do Índice}_{\text{novo}} = \frac{V_{\text{novo}}}{D_{\text{novo}}} = \frac{V_{\text{antigo}}}{D_{\text{antigo}}} = \text{Nível do Índice}_{\text{antigo}}$$

### 4.3. Turnover e Rebalanceamento Institucional
O turnover da carteira entre duas vigências mede a fração total do capital que precisa ser transacionada no mercado pelos fundos indexados e ETFs:
$$\text{Turnover} = \frac{1}{2} \sum_{i \in \text{Universo}} |W_{i,\text{novo}} - W_{i,\text{antigo}}|$$

---

## 5. Invariantes Formais e Leis de Conservação Provadas

| # | Invariante / Lei de Conservação | Expressão Matemática | Status |
|---|----------------------------------|----------------------|--------|
| **1** | **Conservação Estrita de Pesos** | $\sum_{i=1}^M W_i = 1.00000000$ (100%) | **Provada** |
| **2** | **Teto Regulatório de Concentração** | $W_i \le 20.000000\% \quad \forall i$ | **Provada** |
| **3** | **Filtro de Inelegibilidade Estrita** | $P_i \ge 1.00 \land \text{Pres}_i \ge 0.95 \land \neg \text{RecJud}_i$ | **Provada** |
| **4** | **Corte dos 85% de Representatividade** | $\sum_{j=1}^{k-1} IN\_share_j < 0.85 \le \sum_{j=1}^k IN\_share_j$ | **Provada** |
| **5** | **Continuidade Patrimonial via Divisor** | $\Delta \text{IndexLevel}_{\text{rebalance}} = 0$ | **Provada** |

---

## 6. Arquivos e Implementação

- **Módulo Python**: `src/index_rebalancing.py`
  - Classes: `IndexRebalancingEngine`, `AssetTradingData`, `AssetNegotiabilityScore`, `IndexConstituent`, `RebalancingResult`, `IndexType`, `PreviewStage`, `IndexRebalancingError`.
- **Fixture Sintética**: `fixtures/index_rebalancing.json`
  - 28 ativos modelados cobrindo blue chips, mid caps, small caps, utilities de dividendos, penny stock (PENN3), empresas em recuperação judicial (OIBR3, AMER3), ativo inativo (INAC3) e baixo volume (LOWV3).
- **Suíte de Testes**: `tests/test_index_rebalancing.py`
  - 10 testes cobrindo fórmula exata de IN, filtros, capping de 20%, conservação de 100%, índices Ibovespa, IBrX-100, SMLL, IDIV, ciclo quadrimestral e tratamento de erros.
- **Documentação de Arquitetura**: `docs/layout-index-rebalancing.md`
