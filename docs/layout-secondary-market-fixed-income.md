# Especificação Técnica — Negociação Secundária de Renda Fixa Privada (CRA, CRI e Debêntures) no Balcão NoMe B3 (FL-071)

---

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **`FL-071` — Negociação Secundária de Renda Fixa Privada no Balcão NoMe B3** estabelece a infraestrutura operacional de negociação contínua, cruzamento bilateral (*cross-trade*), apuração mark-to-market e liquidação **DvP (Delivery versus Payment)** no ambiente de balcão organizado da **B3**.

Abrange os principais títulos de dívida corporativa e securitização do mercado de capitais brasileiro:
- **Debêntures Corporativas e de Infraestrutura** (Lei nº 6.404/1976 e Lei nº 12.431/2011);
- **Certificados de Recebíveis Imobiliários (CRI)** (Lei nº 9.514/1997);
- **Certificados de Recebíveis do Agronegócio (CRA)** (Lei nº 11.076/2004).

Regulação aplicável:
1. **Padrão ANBIMA de Precificação de Renda Fixa Privada**:
   - Decomposição estrita de Preço Unitário: `PU_Sujo == PU_Limpo + Juros_Decorridos`.
   - Composição multiplicativa de spreads sobre taxas de referência: `(1 + r_bench) * (1 + spread) - 1`.
2. **Resolução CVM nº 35/2021 e Resolução CVM nº 135/2022**:
   - Registro de ofertas e regras de transparência pré e pós-negociação em sistemas de negociação multilateral e balcão organizado.
3. **Manual de Operações do Balcão NoMe B3**:
   - Aplicação obrigatória do **Túnel de Preços B3 / ANBIMA**: limites de oscilação percentual máxima permitida em relação ao PU de referência diário apurado pela ANBIMA, impedindo trades com preços fora da curva de mercado.

---

## 2. Invariantes Formais Provadas

1. **Identidade Escritural de PU Sujo e PU Limpo**:
   `PU_Sujo == round(PU_Limpo + Juros_Acruados, 8)` centavo a centavo.
2. **Conformidade ao Túnel de Preços ANBIMA (Price Tunnel Guardrail)**:
   Negociações com desvio superior ao limite parametrizado (ex: ±2,50% do PU de referência) são rejeitadas preventivamente com `SecondaryMarketTunnelViolationError`.
3. **Atomicidade e Conservação DvP (Delivery versus Payment)**:
   Transferência simultânea de custódia entre comprador e vendedor condicionada à liquidação financeira irrevogável em D0 ou D+1.
4. **Conservação de Posição Custodial e Financeira**:
   `Saldo_Financeiro_Comprador - Montante == Saldo_Financeiro_Vendedor + Montante`.
   `Custódia_Vendedor - Quantidade == Custódia_Comprador + Quantidade`.
5. **Mark-to-Market e Retorno Contábil**:
   `Valor_Mercado_Carteira == sum(Quantidade * PU_Mercado_Sujo)`.

---

## 3. Interfaces do Módulo

- **Módulo Python**: `src/secondary_market_fixed_income.py`
- **Fixture JSON**: `fixtures/secondary_fixed_income.json`
- **Testes Unitários**: `tests/test_secondary_market_fixed_income.py`
