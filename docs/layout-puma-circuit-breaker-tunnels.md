# Especificação Técnica — Leilão de Circuit Breaker e Túneis de Oscilação PUMA B3 (FL-093)

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **FL-093** implementa o mecanismo de salvaguarda operacional e controle de volatilidade do **PUMA Trading System** da B3, estruturado em três túneis concêntricos de preços:

1. **Túnel de Rejeição**: Banda extrema (+/-15% a 20% do preço de referência anterior). Ordens com preço limite fora deste intervalo são sumariamente rejeitadas com código FIX `35=j` / Reject.
2. **Túnel de Leilão Estático**: Banda intermediária (+/-5% a 10% do preço estático/abertura). Se um negócio no mercado contínuo for cotado fora desta faixa, a negociação contínua é suspensa e um leilão não-programado é aberto.
3. **Túnel de Leilão Dinâmico**: Banda estreita (+/-1% a 3% do último preço executado). Protege contra variações abruptas de preço entre negócios consecutivos.

### 1.1 Normativos Aplicáveis
- **Manual de Negociação da B3 (PUMA Trading System)**: Diretrizes de túneis de negociação, acionamento de leilão e regras de desempate de Preço Teórico de Previsão (PTP).
- **Ofício Circular B3**: Tabelas paramétricas por classe de ativos (Ações Ibovespa, Ações Padrão, Futuros DI, Dólar e Índice).

---

## 2. Leis Formais de Invariantes

- **LAW-098A (Não-Transgressão de Túnel em Mercado Contínuo)**: Nenhum trade no livro contínuo pode ser executado a preço fora do túnel de rejeição.
- **LAW-098B (Maximização de Volume em Desfecho de Leilão)**: O uncross deve apurar o PTP que maximiza estritamente o volume executável.
- **LAW-098C (Conservação de Ordens no Book)**: Durante a transição para leilão e reabertura para mercado contínuo, a quantidade total de cada ordem é conservada: `quantity == cum_qty + remaining_qty`.
