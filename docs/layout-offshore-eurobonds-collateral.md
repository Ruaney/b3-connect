# Especificação Técnica — Depósito, Haircut e Bloqueio de Ativos Financeiros Offshore e Eurobonds como Colateral Multilateral na Câmara B3 (FL-083)

---

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **`FL-083` — Depósito, Haircut e Bloqueio de Ativos Financeiros Offshore e Eurobonds como Colateral Multilateral** especifica o motor determinístico e formal da Câmara B3 (sistema CORE / NoMe) para aceitação, custódia internacional segregada, avaliação a mercado, matriz de haircuts e bloqueio multilateral de títulos emitidos por empresas brasileiras no exterior.

### Ativos Elegíveis Cobertos:
1. **American Depositary Receipts (ADRs)**: Recibos Níveis II e III listados em bolsas norte-americanas (NYSE / NASDAQ), custodiados via The Depository Trust Company (DTC / DTCC).
2. **Global Depositary Receipts (GDRs)**: Recibos listados em praças financeiras globais (LSE, Luxembourg Stock Exchange), custodiados via Euroclear Bank ou Clearstream Banking.
3. **Eurobonds e Global Notes Corporativos**: Títulos de dívida em moeda estrangeira (USD ou EUR) emitidos offshore por entidades brasileiras ou suas subsidiárias financeiras no exterior (ex: Petrobras Global Finance, Vale Overseas, Votorantim Cimentos, Itaú Cayman), custodiados via ICSDs (Euroclear / Clearstream) ou DTC.

### Bases Normativas:
- **Manual de Risco e Garantias da Câmara B3 (CORE)**: Diretrizes de elegibilidade de colateral internacional, piso de rating de crédito e deságios de risco cambial e soberano.
- **Instrução CVM nº 160 / Resolução CVM nº 80**: Regras sobre emissores brasileiros e emissões de depositary receipts no exterior.
- **Regulamento da Câmara de Compensação e Liquidação B3**: Bloqueio de ativos em garantia para mitigação de risco de contraparte central (CCP) e procedimentos de excussão em caso de inadimplência multilateral.

---

## 2. Invariantes Formais Provadas

1. **Invariante 1 — Não-Negatividade e Limitação Superior de Colateral Elegível**:
   $$0 \le V_{\text{elegível\_brl}} \le V_{\text{bruto\_brl}}$$
   $$0 \le \text{Crédito Líquido de Margem}_{\text{brl}} \le V_{\text{bruto\_brl}}$$
   Nenhum colateral pode gerar valor líquido negativo, e o deságio regulatório nunca pode inflacionar o valor a mercado do ativo original.

2. **Invariante 2 — Segregação Internacional de Custódia**:
   $$\text{Saldo Total Custodiado} = \text{Saldo Bloqueado em Colateral} + \text{Saldo Livre Desimpedido}$$
   Garante conservação de saldos em todas as contas e depositárias internacionais (DTC, Euroclear, Clearstream).

3. **Invariante 3 — Segregação Estrita de Contas de Custódia**:
   Contas Próprias (`HOUSE`) e de Terceiros (`CLIENT`) mantêm segregação estrita sem vazamento patrimonial ou comunicação de posições.

4. **Invariante 4 — Co-domínio e Monotonicidade de Haircuts**:
   $$0.00 \le H_{\text{total}} < 1.00$$
   Ratings mais frágeis e prazos de maturidade residual maiores (tenor) produzem deságios monotonicamente não-decrescentes:
   $$H(R_1) \le H(R_2) \quad \forall \text{ Rank}(R_1) \le \text{ Rank}(R_2)$$
   $$H(T_1) \le H(T_2) \quad \forall T_1 \le T_2$$

5. **Invariante 5 — Trava Safe-Release Guardrail**:
   Desoneração de garantia offshore é sumariamente rejeitada se resultar em crédito residual inferior à margem requerida da Câmara B3:
   $$\text{Crédito Remanescente}_{\text{brl}} \ge \text{Margem Requerida}_{\text{brl}}$$

6. **Invariante 6 — Avaliação Prudencial PTAX**:
   A conversão de USD/EUR para BRL adota estritamente a taxa de Compra (bid) com margem de segurança cambial contra choques de volatilidade:
   $$0 < \text{PTAX}_{\text{bid}} \le \text{PTAX}_{\text{ask}}$$

---

## 3. Matriz de Haircuts e Metodologia de Cálculo

O haircut regulatório total é calculado analiticamente por adição com teto prudencial:
$$H_{\text{total}} = \min(H_{\text{país}} + H_{\text{rating}} + H_{\text{tenor/ativo}} + H_{\text{fx}}, 0.9000)$$

Componentes:
- **$H_{\text{país}}$**: Deságio por risco de jurisdição/país (ex: `BR_OFFSHORE` = 4.0%).
- **$H_{\text{rating}}$**: Escadinha de rating de crédito global (AAA: 2.0% até BB-: 18.0%; ratings inferiores a BB- são inelegíveis).
- **$H_{\text{tenor/ativo}}$**:
  - Para Eurobonds: Buckets de prazo residual ($\le 1Y$: 2.0%, $1Y-3Y$: 4.0%, $3Y-5Y$: 6.5%, $5Y-10Y$: 9.5%, $> 10Y$: 13.5%).
  - Para ADRs / GDRs: Volatilidade base de renda variável (ADR: 20.0%, GDR: 22.0%).
- **$H_{\text{fx}}$**: Haircut cambial PTAX com buffer de estresse (USD: 5.0% base + 2.5% estresse = 7.5%; EUR: 6.0% base + 3.0% estresse = 9.0%).

---

## 4. Interfaces e Arquivos do Módulo

- **Módulo Python**: `src/offshore_eurobonds_collateral.py`
- **Fixture JSON**: `fixtures/offshore_eurobonds_collateral.json`
- **Testes Unitários**: `tests/test_offshore_eurobonds_collateral.py`
- **Validador de Invariantes**: `scripts/verify_invariants.py`
