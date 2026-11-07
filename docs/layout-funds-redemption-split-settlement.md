# Especificação Técnica — Resgates de Cotas com Split Automático de Taxas (CVM 175) (FL-096)

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **FL-096** implementa a esteira integrada de cotização, apuração e liquidação financeira de resgates de cotas de Fundos de Investimento na Plataforma de Fundos B3 em estrita observância à **Resolução CVM nº 175/2022**.

### 1.1 Normativos Aplicáveis
- **Resolução CVM nº 175/2022 (Anexo Normativo I)**: Direitos de saída, cotização D+n e pagamento D+m.
- **Instrução Normativa RFB nº 1.585/2015**: Regime de tributação regressiva de fundos e retenção na fonte (IRRF).
- **Manual de Operações da Plataforma de Fundos B3**: Conciliação de contas de liquidação, repasses de taxa de administração e performance e envio de ordens de transferência bancária via STR/PI.

---

## 2. Leis Formais de Invariantes

- **LAW-102A (Conservação do Patrimônio Líquido no Resgate)**: O PL final da classe de cotas é estritamente igual ao PL inicial deduzido do resgate bruto.
- **LAW-102B (Inviolabilidade da Linha d'Água / High-Water Mark)**: A taxa de performance é nula se a cota de conversão for menor ou igual à Linha d'Água acumulada.
- **LAW-102C (Fechamento Financeiro Soma-Zero no Split de Pagamento)**: $\text{Débito Fundo} = \text{Crédito Cotista Líquido} + \text{Tx Adm} + \text{Tx Perf} + \text{IRRF Retido}$.
