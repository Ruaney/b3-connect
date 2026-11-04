# Especificação Técnica: PUMA Trading — Leilão de Fechamento e Ordens MOC/LOC (FL-059)

## 1. Visão Geral e Contexto de Mercado

O **Leilão de Fechamento (Closing Call / Closing Auction)** do PUMA Trading System da B3 é a fase mandatória de encerramento do pregão regular para ações e derivativos. É o mecanismo pelo qual se apura o **Preço Teórico de Fechamento (PTF)** e são processados os índices de referência de mercado (ex: Ibovespa, IBrX-100, rebalanceamento de ETFs).

### Tipos de Ordens Suportadas no Call de Fechamento
- **MOC (Market-on-Close)**: Ordem sem preço estipulado, com prioridade máxima de execução no leilão.
- **LOC (Limit-on-Close)**: Ordem limitada enviada especificamente para o call de fechamento, participante apenas se seu preço for executável ao PTF.
- **LIMIT (Ordem Limitada Regular)**: Ordens remanescentes da negociação contínua ou enviadas durante a fase de chamada.

### Regras Determinísticas de Formação do PTF
1. **Maximização do Volume Executável**: $\max \min(\text{CumBuy}(P), \text{CumSell}(P))$.
2. **Minimização do Desbalanceamento**: Menor excedente absoluto $|\text{CumBuy}(P) - \text{CumSell}(P)|$.
3. **Proximidade com o Preço de Referência**: Preço mais próximo do último negócio da fase contínua.

---

## 2. Leis Formais de Conservação e Invariantes

1. **Conservação de Lotes do Livro**:
   $$\sum Q_{\text{inicial}} = \sum Q_{\text{executada}} + \sum Q_{\text{residual}}$$
2. **Preço Único de Liquidação (Uncross Price)**:
   $$P_{\text{fill}}^{(i)} = P_{\text{uncross}} \quad \forall i \in \text{Execuções}$$
3. **Prioridade Estrita MOC**: Ordens MOC possuem prioridade de alocação temporal e de execução sobre ordens limitadas.

---

## 3. Interfaces do Motor

- Módulo Python: `src/puma_closing_auction.py`
- Fixture Sintética: `fixtures/puma_closing_auction.json`
- Testes Unitários: `tests/test_puma_closing_auction.py`
