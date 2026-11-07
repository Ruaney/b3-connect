# Especificação Técnica — Leilão de Fechamento PUMA com Ordens MOC/LOC e Janela de Congelamento (FL-098)

## 1. Visão Geral e Arcabouço Operacional

O fluxo **FL-098** formaliza o motor de casamento e apuração do **Leilão de Fechamento (Closing Auction / Closing Cross)** do **PUMA Trading System B3**, contemplando o processamento especializado de ordens **Market-On-Close (MOC)** e **Limit-On-Close (LOC)**, salvaguardas regulatórias de **Janela de Congelamento (Freeze Window)** contra manipulação de preços (*anti-spoofing*) e as regras oficiais de desempate da B3 para formação do **Preço Teórico de Fechamento (PTP / PTF)**.

O encerramento do pregão na B3 constitui o momento de maior concentração de liquidez institucional do dia, utilizado para fixação dos preços de ajuste de derivativos, precificação de fechamento de índices (Ibovespa, IBrX, IDIV) e rebalanceamento passivo de carteiras e ETFs.

### 1.1 Normativos Aplicáveis
- **Manual de Procedimentos Operacionais de Negociação da B3**:
  - Capítulo: *Leilões de Negociação e Procedimentos Especiais*.
  - Procedimentos de Call de Fechamento, Prorrogação Randômica (*Random Extension*) e Determinação do Preço Teórico.
- **Ofício Circular B3 sobre Regras de Conduta no Fechamento**:
  - Restrições a cancelamentos intempestivos no encerramento de leilões (*Freeze Period*).
  - Prevenção a práticas abusivas e manipulação artificial de preço de fechamento (CVM Resolução nº 62 e BSM Supervisão de Mercados).

---

## 2. Tipos de Ordens Suportados no Closing Cross

| Tipo de Ordem | FIX Tag 40 / Atributo | Comportamento no Leilão de Fechamento | Prioridade de Alocação |
|---|---|---|---|
| **MOC (Market-On-Close)** | `OrdType=1 (Market)` com `TimeInForce=7 (At the Close)` | Sem preço estipulado (`price = None`). Compromisso incondicional de execução ao PTP final apurado. | **Prioridade Absoluta** sobre ordens LOC/LIMIT na mesma ponta. Desempate via FIFO temporal. |
| **LOC (Limit-On-Close)** | `OrdType=2 (Limit)` com `TimeInForce=7 (At the Close)` | Preço limite explicitado (`price > 0`). Só participa se o PTP for $\le \text{Preço}$ (compra) ou $\ge \text{Preço}$ (venda). | Subordinada à execução prévia integral de ordens MOC. Desempate: melhor preço e depois FIFO temporal. |
| **LIMIT (Ordem Limitada Regular)** | `OrdType=2 (Limit)` com `TimeInForce=0 (Day)` | Ordens limitadas remanescentes da negociação contínua ou enviadas no call. Participa nas mesmas condições de preço da LOC. | Subordinada à execução prévia integral de ordens MOC. |

---

## 3. Dinâmica das Fases e Janela de Congelamento (Freeze Window)

O motor opera através das seguintes fases estritas:

1. **CALL_PHASE (Fase de Acumulação Regular)**:
   - Participantes podem submeter livremente ordens MOC, LOC e LIMIT.
   - Cancelamentos e modificações de quantidade/preço são plenamente admitidos.
2. **FREEZE_WINDOW (Janela de Congelamento / No-Cancel)**:
   - Inicia-se nos instantes regulatórios finais do leilão antes do uncross.
   - **Cancelamentos são sumariamente rejeitados**: qualquer mensagem de cancelamento é bloqueada com disparo de `PumaFreezeWindowViolationError` e auditoria formal (*LAW-104A*).
   - **Modificações redutoras de quantidade ou prejudiciais de preço são proibidas**: aumento de quantidade é aceito, mas redução de volume ou piora de preço é rejeitada.
3. **RANDOM_EXTENSION (Prorrogação Randômica)**:
   - Intervalo aleatório estipulado pelo PUMA para evitar ataques de latência de última fração de segundo (*sniping*). Mantém todas as proteções da janela de congelamento.
4. **UNCROSS (Cruzamento e Alocação Determinística)**:
   - Apuração do Preço Teórico de Fechamento (PTP).
   - Casamento atômico das ordens elegíveis e emissão de execuções (*PumaClosingExecutionFill*).
5. **CLOSED (Encerramento do Pregão)**:
   - Book fechado, balanço de ordens executadas e residuais auditado.

---

## 4. Algoritmo Determinístico de Apuração do Preço Teórico (PTP) B3

Para cada preço discreto $P \in \text{Candidatos} = \{\text{Preços das Ordens Limitadas}\} \cup \{P_{\text{ref}}\}$:

- Demanda Acumulada:
  $$Q_{\text{buy}}(P) = \sum_{o \in \text{MOC}_{\text{buy}}} Q_{\text{rem}}(o) + \sum_{o \in \text{LOC/LIM}_{\text{buy}}, \, P_o \ge P} Q_{\text{rem}}(o)$$
- Oferta Acumulada:
  $$Q_{\text{sell}}(P) = \sum_{o \in \text{MOC}_{\text{sell}}} Q_{\text{rem}}(o) + \sum_{o \in \text{LOC/LIM}_{\text{sell}}, \, P_o \le P} Q_{\text{rem}}(o)$$
- Volume Executável: $V(P) = \min(Q_{\text{buy}}(P), Q_{\text{sell}}(P))$
- Desequilíbrio Absoluto: $I(P) = |Q_{\text{buy}}(P) - Q_{\text{sell}}(P)|$
- Desequilíbrio com Sinal: $S(P) = Q_{\text{buy}}(P) - Q_{\text{sell}}(P)$

### Hierarquia de Critérios Oficiais de Desempate:
1. **Regra 1 (Volume Máximo)**:
   $$P^* = \arg\max_P V(P)$$
   Se houver preço único que maximiza o volume, ele é selecionado imediatamente.
2. **Regra 2 (Menor Desbalanceamento)**:
   Entre os preços empatados no volume máximo, seleciona-se:
   $$P^* = \arg\min_{P \in \mathcal{P}_1} I(P)$$
3. **Regra 3 (Direção da Pressão de Mercado & Preço de Referência)**:
   Persistindo o empate em volume e desbalanceamento ($\mathcal{P}_2$):
   - Se todos os preços em $\mathcal{P}_2$ apresentarem **excesso de compra** ($S(P) > 0$): adota-se o **maior preço** em $\mathcal{P}_2$ (pressão compradora altista).
   - Se todos os preços em $\mathcal{P}_2$ apresentarem **excesso de venda** ($S(P) < 0$): adota-se o **menor preço** em $\mathcal{P}_2$ (pressão vendedora baixista).
   - Se o desbalanceamento for **nulo** ($S(P) = 0$) ou de sinais mistos: adota-se o preço de $\mathcal{P}_2$ mais próximo do **Preço de Referência** ($P_{\text{ref}}$). Em caso de equidistância perfeita, adota-se o maior preço.

---

## 5. Leis Formais de Invariantes

### LAW-104A: Inviolabilidade da Janela de Congelamento (No-Cancel)
> Durante as fases `FREEZE_WINDOW` e `RANDOM_EXTENSION`, nenhuma ordem presente no livro pode ser cancelada ou sofrer redução de volume. Toda tentativa externa é sumariamente rejeitada pelo motor e registrada na trilha de auditoria:
> $$\text{QtdCanceladaNoFreeze} = 0 \quad \land \quad \text{RejeiçõesAuditadas} \ge \text{TentativasDeCancelamentoNoFreeze}$$

### LAW-104B: Prioridade Absoluta MOC sobre LOC
> Ordens MOC possuem prioridade irrestrita de alocação sobre qualquer ordem LOC ou LIMIT na mesma ponta. Nenhuma ordem LOC/LIMIT pode ser executada se houver qualquer ordem MOC elegível não integralmente atendida:
> $$\forall \text{side} \in \{\text{BUY}, \text{SELL}\}: \quad \left( \exists o_{\text{LOC}} \mid Q_{\text{exec}}(o_{\text{LOC}}) > 0 \right) \implies \left( \forall o_{\text{MOC}} \mid Q_{\text{rem}}(o_{\text{MOC}}) = 0 \right)$$

### LAW-104C: Conservação de Volume no Uncross e Preço Único de Liquidação
> O processo de casamento é estritamente conservativo (nenhum lote é criado ou destruído). Cada lote executado na compra corresponde a exatamente um lote executado na venda, transacionado estritamente ao PTP único:
> $$\sum_{o} Q_{\text{inicial}}(o) = \sum_{o} Q_{\text{executada}}(o) + \sum_{o} Q_{\text{residual}}(o) + \sum_{o} Q_{\text{cancelada}}(o)$$
> $$\sum_{f \in \text{Execuções}} Q(f) = \sum_{o \in \text{BUY}} Q_{\text{exec}}(o) = \sum_{o \in \text{SELL}} Q_{\text{exec}}(o)$$
> $$P_{\text{exec}}(f) = P^* \quad \forall f \in \text{Execuções}$$

---

## 6. Estrutura de Arquivos e Componentes

- **Módulo Principal**: `src/puma_closing_cross_moc.py`
  - Classes: `PumaClosingCrossEngine`, `ClosingOrder`, `PumaClosingExecutionFill`, `TheoreticalPriceSummary`.
  - Enums: `ClosingOrderType`, `ClosingOrderSide`, `AuctionPhase`, `ClosingOrderStatus`.
  - Métodos: `add_order`, `enter_freeze_period`, `enter_random_extension`, `cancel_order`, `modify_order`, `calculate_theoretical_price`, `execute_uncross`, `verify_invariants`, `to_dict`, `from_dict`, `load_from_fixture`.
- **Suíte de Testes**: `tests/test_puma_closing_cross_moc.py`
- **Fixture Canônica**: `fixtures/puma_closing_cross_moc.json`
