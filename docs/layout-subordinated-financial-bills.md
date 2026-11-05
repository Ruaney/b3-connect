# Especificação Técnica — Escrituração, Liquidação e Absorção de Perdas de Letras Financeiras Subordinadas (Basileia III) (FL-073)

---

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **`FL-073` — Letras Financeiras Subordinadas e Absorção de Perdas** implementa o ciclo de vida completo de emissão, registro no Balcão NoMe B3, pagamento e cancelamento discricionário de cupons de juros, e os mecanismos determinísticos de absorção de perdas em instrumentos elegíveis a compor o **Patrimônio de Referência (PR)** bancário.

Fundamentação regulatória:
1. **Lei nº 12.249/2010**:
   - Marco legal da Letra Financeira (LF). Estabelece as condições de emissão por bancos múltiplos, bancos comerciais, bancos de investimento e sociedades de crédito.
2. **Resoluções CMN nº 4.958/2021 e nº 5.036/2022**:
   - Metodologia de apuração do Patrimônio de Referência (PR) em conformidade com o arcabouço internacional de **Basileia III**.
   - **Capital Nível 1 (Capital Complementar / Additional Tier 1 - AT1)**: Instrumentos perpétuos (sem data de vencimento), com cláusula expressa de cancelamento discricionário de remuneração (não-cumulativo) e absorção obrigatória de perdas quando o Capital Principal (CET1) cair abaixo de 5,125%.
   - **Capital Nível 2 (Tier 2)**: Instrumentos com prazo de vencimento mínimo de 5 anos (1.825 dias), cláusula de subordinação estrita e absorção de perdas no Ponto de Não-Viabilidade (**PonV - Point of Non-Viability**).
   - **Mecanismos de Absorção de Perdas**: Baixa contábil permanente (*Permanent Write-Down*), baixa temporária (*Temporary Write-Down* com recomposição / *Write-Up*) ou conversão compulsória em ações ordinárias (*Mandatory Equity Conversion*).

---

## 2. Invariantes Formais Provadas

1. **Inviolabilidade da Hierarquia de Capital (Loss Absorption Priority)**:
   Instrumentos AT1 (Nível 1) absorvem perdas integralmente antes de qualquer impacto sobre LFs Tier 2 (Nível 2).
2. **Identidade Escritural de Saldo Devedor**:
   `Saldo_Devedor == Quantidade * PU_Atual` centavo a centavo.
3. **Conservação Financeira na Absorção de Perdas**:
   `Perda_Absorvida == Quantidade * (PU_Antes - PU_Depois)`.
4. **Conservação Econômica em Conversão em Ações**:
   `Perda_Alocada == (Ações_Emitidas * Preço_Conversão) + Troco_Residual_Dinheiro`.
5. **Teto Estrito de Write-Up (Recomposição Temporária)**:
   `PU_Pós_Write_Up <= PU_Referência_Original`, condicionado à existência de lucros líquidos auditados e autorização prévia do Banco Central do Brasil.
6. **Não-Inadimplência na Suspensão Discricionária de Cupom**:
   A suspensão de cupom Nível 1 não aciona Default, não gera juros moratórios e ativa compulsoriamente a trava de dividendos (*Dividend Stopper*).
7. **Amortizador Linear Prudencial de Capital Nível 2**:
   Redutor linear de 20% ao ano nos últimos 5 anos de vida útil do título (`100% -> 80% -> 60% -> 40% -> 20% -> 0%`).

---

## 3. Interfaces do Módulo

- **Módulo Python**: `src/subordinated_financial_bills.py`
- **Fixture JSON**: `fixtures/subordinated_financial_bills.json`
- **Testes Unitários**: `tests/test_subordinated_financial_bills.py`
