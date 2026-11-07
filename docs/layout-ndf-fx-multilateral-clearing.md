# Especificação Técnica — Compensação Multilateral e Margem de Variação de NDF FX (FL-095)

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **FL-095** estabelece a infraestrutura da **Câmara B3** para registro, novação subjetiva, compensação multilateral e liquidação financeira de contratos a termo de moeda sem entrega física (**NDF - Non-Deliverable Forward**).

### 1.1 Normativos Aplicáveis
- **Regulamento da Câmara de Compensação e Liquidação B3**: Regras de novação, compensação multilateral líquida e cálculo de margem de variação diária.
- **Resolução CMN nº 4.952/2021**: Obrigatoriedade de registro e tratamento de risco de derivativos de balcão novados.
- **Instrução Normativa RFB nº 1.585/2015**: Apuração do ganho líquido no encerramento de operações a termo e retenção na fonte (IRRF).

---

## 2. Leis Formais de Invariantes

- **LAW-101A (Fechamento Multilateral Soma-Zero de Margem de Variação)**: A soma algébrica dos créditos e débitos de margem de variação de todos os participantes é estritamente zero: $\sum \Delta VM_i = \text{R\$ } 0,00$.
- **LAW-101B (Paridade e Coerência de Taxas Forward)**: As taxas a termo negociadas e de marcação a mercado respeitam a relação de paridade de taxa de juros e cupom cambial.
- **LAW-101C (Conservação Financeira na Liquidação Diferencial)**: Para qualquer contrato liquidado: $\text{Valor Bruto} = \text{Valor Líquido} + \text{IRRF Retido}$.
