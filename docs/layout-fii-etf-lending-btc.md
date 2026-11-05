# Especificação Técnica — Registro, Alocação, Proventos e Devolução de Empréstimo de Cotas de Fundos Imobiliários (FII) e ETFs na Câmara B3 (FL-084)

---

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **`FL-084` — Registro, Alocação, Proventos e Devolução de Empréstimo de Cotas de FII e ETFs** implementa o ciclo operacional completo de empréstimo de cotas de Fundos de Investimento Imobiliário (FII) e Exchange Traded Funds (ETFs de Ações e ETFs de Renda Fixa) no Sistema de Empréstimo de Ativos (BTC / Banco de Títulos CBLC) da Câmara B3 e Central Depositária.

Ativos elegíveis:
- **Fundos de Investimento Imobiliário (FII)**: Tickers com terminação 11 (ex: HGLG11, KNIP11, MXRF11, XPLG11), cotas escriturais registradas na Central Depositária.
- **ETFs de Renda Variável (Ações)**: Fundos de índice referenciados em índices de ações locais e internacionais (ex: BOVA11, SMAL11, IVVB11).
- **ETFs de Renda Fixa**: Fundos de índice com carteira de títulos públicos ou privados (ex: IMAB11, B5P211, FIXA11).

Bases normativas:
1. **Manual de Procedimentos Operacionais da Câmara B3 (Compensação e Liquidação)**:
   - Disciplina a contratação, transferência fiduciária de posse escritural na Central Depositária, garantias multilaterais e devolução física das cotas.
2. **Manual de Operações do Sistema BTC B3**:
   - Estabelece a remuneração pro-rata die pela convenção DU/252, prazos de empréstimo, reversibilidade antecipada a pedido do tomador ou doador, e quitação financeira.
3. **Lei nº 8.668/1993 e Lei nº 11.033/2004**:
   - Tratamento tributário de rendimentos mensais de FIIs distribuídos a cotistas (isenção de IRRF para pessoas físicas sob os requisitos legais).
4. **Instrução Normativa RFB nº 1.585/2015 (Art. 13)**:
   - Regras de compensação financeira e repasse compulsório de proventos para contratos de empréstimo de ativos em aberto na Record Date (Data Com).

---

## 2. Invariantes Formais Provadas

1. **Lei de Conservação de Cotas em Empréstimo (LAW-084)**:
   `Cotas_Doador == Cotas_Emprestadas_Ativas + Cotas_Devolvidas`.
   O patrimônio total do doador original é preservado em todas as etapas, desde a contratação até a liquidação final.
2. **Lei de Paridade Estrita no Repasse de Proventos BTC (LAW-085)**:
   `Provento_Recebido_Tomador == Provento_Repassado_Doador` (centavo a centavo).
   Débito do tomador equivale rigorosamente ao crédito transferido ao doador na data de liquidação estipulada pelo fundo emissor.
3. **Conservação Multilateral de Custódia Depositária**:
   `Sum(Lent_Active_Qty) == Sum(Borrowed_Active_Qty)` para cada ativo no livro central da Central Depositária (zero-sum estrito).
4. **Não-Negatividade de Saldos Escriturais**:
   Todas as contas e posições de custódia mantêm saldos disponíveis, emprestados e tomados estritamente não-negativos (`available_qty >= 0`, `lent_active_qty >= 0`, `borrowed_active_qty >= 0`).
5. **Remuneração Pro-Rata Die Exponencial (DU/252)**:
   `Remuneração == Volume * ((1 + Taxa_aa) ^ (DU / 252) - 1)` quantizado a 2 casas decimais BRL via `ROUND_HALF_UP`.

---

## 3. Interfaces do Módulo

- **Módulo Python**: `src/fii_etf_lending_btc.py`
- **Fixture JSON**: `fixtures/fii_etf_lending_btc.json`
- **Testes Unitários**: `tests/test_fii_etf_lending_btc.py`
