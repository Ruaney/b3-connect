# Especificação Técnica: Roteamento Inteligente de Ordens (SOR) & Agregação de Liquidez (FL-068)

## 1. Visão Geral e Contexto de Mercado

O **Smart Order Router (SOR)** e o **Agregador de Liquidez Multi-Mercado** constituem o componente nuclear de roteamento do ecossistema de negociação moderna da B3. Com o advento de múltiplos ambientes de liquidez — incluindo o livro central transparente (**PUMA B3 CLOB / Lit Book**), os **Dark Pools Institucionais** (livros não exibidos com cruzamento ao Midpoint), o **Balcão Organizado / Retail Liquidity Provider (RLP)** e os ambientes de **Block Trading (Grandes Lotes)** —, o roteador garante que a ordem-mãe (*parent order*) seja fragmentada e despachada de maneira matematicamente ótima e determinística.

### Ambientes de Execução Conectados (Venues)
1. **PUMA B3 Lit CLOB (`PUMA_LIT`)**: Livro de ofertas central transparente contínuo da B3 com prioridade preço-tempo. Exibe cotações de compra e venda (*level 2 depth*), sofrendo impacto de mercado visual (*market footprint*) quando grandes ordens varrem múltiplos níveis.
2. **Institutional Dark Pools (`DARK_POOL`)**: Ambientes de cruzamento sem exibição pré-negociação que casam ordens estritamente ao preço médio do spread (*NBBO Midpoint*) ou com desconto negociado. Protegem investidores institucionais contra *adverse selection* e vazamento de fluxo de ordens.
3. **Balcão Organizado & RLP (`ORGANIZED_OTC`)**: Provedores de liquidez no varejo e balcão organizado que fornecem melhoria de preço (*price improvement*) em relação ao topo do livro lit (ex: execução a meio tick ou com desconto de spread) e isenção ou redução tarifária.
4. **Block Trading Facility (`BLOCK_CROSSING`)**: Ambientes regulados para cruzamento de grandes lotes em bloco (tamanho mínimo institucional elevado), viabilizando transações volumosas a custos reduzidos.

---

## 2. Estratégias Algorítmicas de Fragmentação (Split SOR)

O motor implementa quatro estratégias algorítmicas determinísticas:

### 2.1. BEST_PRICE_DEPTH (Best Execution / NBBO Sweeping)
- Realiza a consolidação da profundidade de mercado de todos os ambientes ativos em uma fila unificada de preços.
- **Compra**: Varre as ofertas de venda da mais barata para a mais cara ($P_{\min} \to P_{\max}$).
- **Venda**: Varre as ofertas de compra da mais cara para a mais barata ($P_{\max} \to P_{\min}$).
- Em empates de preço entre venues, prioriza ambientes não exibidos (*dark pools*) para mitigar sinalização ao mercado aberto, seguido de menor tarifa (*fee_bps*) e menor latência.

### 2.2. MINIMAL_MARKET_IMPACT (Absorção Oculta Prioritária)
- **Fase 1 (Oculta)**: Esgota prioritariamente a liquidez de *Dark Pools* e ambientes Midpoint até o limite estipulado ou esgotamento do volume escuro.
- **Fase 2 (Visível)**: O saldo residual não atendido na Fase 1 é roteado para o livro lit PUMA e balcão RLP utilizando varredura de melhor preço.
- **Resultado Microestrutural**: Redução drástica do *market impact score* e minimização do risco de *front-running* ou reação adversa por parte de formadores de mercado HFT.

### 2.3. COST_FEE_OPTIMIZED (Otimização All-In Líquida)
- Avalia o preço líquido efetivo após aplicação de taxas de acesso e emolumentos de cada ambiente:
  $$P_{\text{net, buy}} = P_{\text{gross}} \times \left(1 + \frac{\text{Fee}_{\text{bps}}}{10.000}\right)$$
  $$P_{\text{net, sell}} = P_{\text{gross}} \times \left(1 - \frac{\text{Fee}_{\text{bps}}}{10.000}\right)$$
- Ordena as fatias pelo menor preço líquido de aquisição (na compra) ou maior receita líquida (na venda).

### 2.4. PRO_RATA_LIQUIDITY (Rateio Proporcional com Resíduo Inteiro)
- Em níveis de preço onde múltiplos ambientes oferecem liquidez e o volume agregado supera o saldo restante da ordem, a quantidade é dividida proporcionalmente à profundidade ofertada por cada venue.
- Para assegurar que o lote seja integralmente conservado sem frações decimais de ações, aplica-se o **Método dos Maiores Restos (Hare-Niemeyer)**:
  $$\text{Share}_{v} = \left\lfloor Q_{\text{rem}} \times \frac{Q_{\text{avail}, v}}{\sum Q_{\text{avail}}} \right\rfloor$$
  Os restos fracionários $\left(Q_{\text{rem}} \times \frac{Q_v}{Q_{\text{tot}}} - \text{Share}_v\right)$ são ordenados decrescentemente e recebem 1 ação cada até completar $Q_{\text{rem}}$.

---

## 3. Leis Formais de Conservação e Invariantes Matemáticas

1. **Lei Fundamental de Conservação de Quantidade do Lote Original**:
   $$\sum_{k=1}^m Q_{\text{slice}, k} + Q_{\text{residual}} = Q_{\text{ordem\_mãe}}$$
   Para ordens com preenchimento total (`FULLY_ROUTED`):
   $$\sum_{k=1}^m Q_{\text{slice}, k} = Q_{\text{ordem\_mãe}} \quad (Q_{\text{residual}} = 0)$$
   Nenhuma ação ou lote é criado, destruído ou duplicado durante a fragmentação e despacho.

2. **Invariante de Prevenção de Trade-Through (Best Execution CVM 35 / CVM 505)**:
   Nenhuma fatia $k$ pode ser despachada a um preço $P > P^*$ (compra) ou $P < P^*$ (venda) se houver liquidez executável remanescente a preço melhor $P^*$ em qualquer ambiente habilitado.

3. **Invariante de Limite de Preço (Price Limit Protection)**:
   Para ordens `LIMIT`:
   $$\forall k \in [1, m], \quad P_{\text{slice}, k} \le P_{\text{limit}} \ (\text{Compra}), \quad P_{\text{slice}, k} \ge P_{\text{limit}} \ (\text{Venda})$$

4. **Invariante de Não-Superalocação de Livro**:
   $$\sum_{k \in \text{Fatias}(v, P)} Q_{\text{slice}, k} \le Q_{\text{livro}}(v, P)$$

5. **Invariante de Discretização Inteira e Tamanho Mínimo**:
   $$Q_{\text{slice}, k} \in \mathbb{Z}^+, \quad Q_{\text{slice}, k} \ge \text{MinSliceQty}(v)$$

---

## 4. Métricas Financeiras e Microestruturais

- **Preço Médio Ponderado por Volume (VWAP)**:
  $$\text{VWAP} = \frac{\sum_{k=1}^m (Q_{\text{slice}, k} \times P_{\text{slice}, k})}{\sum_{k=1}^m Q_{\text{slice}, k}}$$

- **Slippage em Relação ao Arrival Price**:
  $$\text{Slippage (bps)} = \frac{|\text{VWAP} - P_{\text{arrival}}|}{P_{\text{arrival}}} \times 10.000$$

- **Market Impact Score (0.0000 a 1.0000)**:
  Pondera a proporção do volume roteado para livros transparentes (*lit*) versus a profundidade do topo do livro:
  $$\text{Impact} = \left(\frac{Q_{\text{lit}}}{Q_{\text{total}}}\right) \times \min\left(1.0, \frac{Q_{\text{lit}}}{2 \times Q_{\text{lit\_top}}}\right)$$
  *(Ordens 100% absorvidas em Dark Pools auferem $\text{Impact} = 0.0000$)*.

---

## 5. Estrutura do Sistema

- **Motor Central**: `src/smart_order_router.py`
- **Fixture Oficial de Cenários**: `fixtures/smart_order_router.json`
- **Suíte de Testes Unitários**: `tests/test_smart_order_router.py`
