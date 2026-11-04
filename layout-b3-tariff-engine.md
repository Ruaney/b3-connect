# Especificação Técnica do Motor de Tarifação B3 (FL-053)

## PUMA Trading System — Tarifação de Negociação e Liquidação com Escadinha Regressiva ADTV e Desconto de Day-Trade

---

## 1. Visão Geral e Fundamentação Regulatória

O ecossistema de negociação e pós-trade da **B3 S.A. – Brasil, Bolsa, Balcão** adota uma política de tarifação integrada para o **Mercado à Vista** (Ações Ordinárias/Preferenciais, BDRs, ETFs e Units) e **Derivativos**, concebida para alinhar incentivos operacionais, remunerar os serviços de contraparte central (CCP) e mitigar o custo de transação de grandes provedores de liquidez e investidores institucionais.

A regulamentação e parametrização do modelo tarifário apoiam-se nos seguintes dispositivos:
- **Resolução CVM nº 135/2022**: Dispõe sobre o funcionamento dos mercados regulamentados de valores mobiliários e governança de taxas e tarifas praticadas por entidades administradoras de mercado organizado.
- **Regulamento e Manual de Procedimentos Operacionais da Câmara B3**: Estabelece os componentes de tarifação de compensação e liquidação, novação e custódia.
- **Tabela de Tarifas e Emolumentos de Renda Variável da B3**: Define a metodologia de apuração do **Volume Médio Diário (ADTV - Average Daily Trading Volume)**, as alíquotas regressivas em escadinha (Tiers 1 a 7) e os regimes especiais de Day-Trade, Formador de Mercado (PMM) e Investidores Não Residentes.
- **Resolução CMN nº 4.373/2014**: Regulamenta as aplicações de investidores não residentes no mercado financeiro e de capitais brasileiro, com disciplina especial de incentivo tributário e desoneração tarifária.
- **Resolução CVM nº 175/2022**: Marco regulatório dos Fundos de Investimento, permitindo a agregação consolidada de faturamento por gestora/administradora para enquadramento em faixas de maior volume.

---

## 2. Componentes Tarifários Oficiais da B3

A cobrança tarifária incidente sobre as operações em ambiente de bolsa no PUMA Trading System desdobra-se em três parcelas analíticas:

```
+-------------------------------------------------------------------------------+
|                        TARIFA TOTAL B3 (Total Tariff)                         |
+-------------------------------------------------------------------------------+
|  1. Emolumentos (PUMA)  |  2. Taxa de Liquidação (CCP)  |  3. Taxa de Registro|
|     (Taxa de Negociação)|     (Câmara de Compensação)   |     (Derivativos)   |
+-------------------------------------------------------------------------------+
```

### 2.1 Emolumentos (Taxa de Negociação do PUMA Trading System)
- **Finalidade**: Remunera a infraestrutura tecnológica de negociação em bolsa fornecida pela B3, englobando o motor de casamento de ordens (*matching engine* de ultrabaixa latência do PUMA), gateways FIX/SBE, conectividade DMA (Modelos 1 a 4) e mensageria de mercado.
- **Base de Cálculo**: Incide *ad valorem* (alíquota percentual) sobre o valor financeiro bruto da transação ($V = Q \times P$).
- **Alíquota Regular Base (Tier 1)**: $0{,}0050\%$ ($0{,}000050$).
- **Alíquota Day-Trade Base (Tier 1)**: $0{,}0030\%$ ($0{,}000030$).

### 2.2 Taxa de Liquidação (Clearing B3)
- **Finalidade**: Remunera os serviços de pós-trade prestados pela Câmara B3 na qualidade de Contraparte Central (CCP). Engloba a novação jurídica bilateral, cálculo de margem de garantia em tempo real (sistema CORE), alocação multilateral de obrigações e custódia depositária para liquidação física e financeira em $D+2$ (mercado à vista) ou $D+1$ (futuros e balcão).
- **Base de Cálculo**: Incide *ad valorem* sobre o valor financeiro bruto da transação ($V = Q \times P$).
- **Alíquota Regular Base (Tier 1)**: $0{,}0250\%$ ($0{,}000250$).
- **Alíquota Day-Trade Base (Tier 1)**: $0{,}0170\%$ ($0{,}000170$).

### 2.3 Taxa de Registro e Custódia
- **Finalidade**: Aplicável no registro de contratos de derivativos listados, mercado a termo de ações, opções flexíveis e operações de balcão registrado (sistema NoMe). No mercado à vista tradicional de ações, a alíquota básica de registro é $0{,}0000\%$, sendo as taxas consolidadas em Emolumentos + Liquidação.

---

## 3. Escadinha Regressiva de ADTV (Average Daily Trading Volume)

### 3.1 Metodologia de Apuração do ADTV

O **ADTV (Volume Médio Diário de Negociação)** é o critério oficial adotado pela B3 para conceder descontos por escala. O enquadramento em faixas regressivas baseia-se na movimentação média diária do investidor no mês calendário anterior (ou mês de apuração vigente para novos entrantes):

$$\text{ADTV} = \frac{\sum_{d=1}^{N} \text{Volume Financeiro Negociado no Dia } d}{N_{\text{dias úteis}}}$$

Onde:
- A agregação é realizada pelo número raiz do documento fiscal (**CPF** para pessoas físicas e **CNPJ** para fundos/gestoras e pessoas jurídicas).
- São somadas as compras e vendas em todos os ativos elegíveis de renda variável (ações, BDRs, cotas de ETFs e Units).

### 3.2 Tabela de Faixas Regressivas (Tiers 1 a 7) — Mercado à Vista

A política de escadinha regressiva divide os volumes em 7 faixas tarifárias (*Tiers*), reduzindo progressivamente tanto a taxa de negociação quanto a taxa de liquidação:

| Tier | Faixa de ADTV Mensal (R$) | Negociação Regular (%) | Liquidação Regular (%) | Total Regular (%) | Negociação Day-Trade (%) | Liquidação Day-Trade (%) | Total Day-Trade (%) |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Tier 1** | Até R$ 1.000.000,00 | $0{,}0050\%$ | $0{,}0250\%$ | **$0{,}0300\%$** | $0{,}0030\%$ | $0{,}0170\%$ | **$0{,}0200\%$** |
| **Tier 2** | De R$ 1.000.000,01 a R$ 5.000.000,00 | $0{,}0045\%$ | $0{,}0230\%$ | **$0{,}0275\%$** | $0{,}0027\%$ | $0{,}0153\%$ | **$0{,}0180\%$** |
| **Tier 3** | De R$ 5.000.000,01 a R$ 20.000.000,00 | $0{,}0040\%$ | $0{,}0210\%$ | **$0{,}0250\%$** | $0{,}0024\%$ | $0{,}0136\%$ | **$0{,}0160\%$** |
| **Tier 4** | De R$ 20.000.000,01 a R$ 50.000.000,00 | $0{,}0035\%$ | $0{,}0190\%$ | **$0{,}0225\%$** | $0{,}0021\%$ | $0{,}0119\%$ | **$0{,}0140\%$** |
| **Tier 5** | De R$ 50.000.000,01 a R$ 100.000.000,00 | $0{,}0030\%$ | $0{,}0170\%$ | **$0{,}0200\%$** | $0{,}0018\%$ | $0{,}0102\%$ | **$0{,}0120\%$** |
| **Tier 6** | De R$ 100.000.000,01 a R$ 300.000.000,00 | $0{,}0025\%$ | $0{,}0150\%$ | **$0{,}0175\%$** | $0{,}0015\%$ | $0{,}0085\%$ | **$0{,}0100\%$** |
| **Tier 7** | Acima de R$ 300.000.000,00 | $0{,}0020\%$ | $0{,}0130\%$ | **$0{,}0150\%$** | $0{,}0012\%$ | $0{,}0068\%$ | **$0{,}0080\%$** |

> [!NOTE]
> Observe a progressão geométrica do benefício: um investidor qualificado no **Tier 7** em operações regulares paga metade da taxa cobrada de um participante no **Tier 1** ($0{,}0150\%$ vs $0{,}0300\%$). Em operações de Day-Trade, a economia no Tier 7 chega a $60\%$ em relação ao Tier 1 regular ($0{,}0080\%$ vs $0{,}0200\%$).

---

## 4. Desconto Especial de Day-Trade

### 4.1 Caracterização Regulatória de Day-Trade na B3

Para efeito de aplicação do regime tarifário diferenciado da B3, considera-se **Day-Trade** a conjugação de operações de compra e venda realizadas:
1. No **mesmo pregão** (mesma data de negociação).
2. Pelo **mesmo investidor final** (mesmo código de cliente / CPF ou CNPJ).
3. Com o **mesmo ativo objeto** (mesmo código ISIN / símbolo de negociação, ex: `PETR4` ou `VALE3`).
4. Executadas por intermédio da **mesma corretora de valores** (mesmo Participante de Negociação / Broker ID).

### 4.2 Algoritmo de Casamento de Day-Trade (*Intraday Netting & Matching*)

No fechamento diário do pregão ($D0$), o motor de compensação realiza o casamento entre compras e vendas:

$$\begin{aligned}
Q_{\text{comprada}} &= \sum_{t \in \text{compras}} Q_t \\
Q_{\text{vendida}} &= \sum_{t \in \text{vendas}} Q_t \\
Q_{\text{DayTrade}} &= \min(Q_{\text{comprada}}, Q_{\text{vendida}}) \\
Q_{\text{Regular}} &= |Q_{\text{comprada}} - Q_{\text{vendida}}|
\end{aligned}$$

- O volume correspondente a $Q_{\text{DayTrade}}$ é faturado com as **alíquotas de Day-Trade** (redução média de $33{,}33\%$ nos custos em relação à alíquota regular do respectivo Tier).
- A quantidade residual em aberto $Q_{\text{Regular}}$ é classificada como **operação normal (posição)** e tarifada pelas alíquotas regulares correspondentes ao Tier do investidor.

### 4.3 Redução de Risco Sistêmico e Justificativa Econômica

O desconto substancial de Day-Trade concedido pela B3 fundamenta-se na **ausência de risco de liquidação física em D+2**. Como as posições são casadas e extintas financeiramente no próprio ciclo intradiário, a Câmara B3 não incorre em custos de transferência de custódia na Central Depositária nem em risco de contraparte estendido por 48 horas.

---

## 5. Regimes Especiais de Isenção e Desconto

### 5.1 Investidores Não Residentes (Resolução CMN nº 4.373)
- Investidores internacionais registrados sob a Resolução CMN 4.373 possuem regime especial para evitar bitributação e custos excessivos de transação transfronteiriça.
- A B3 concede um **abatimento de $20{,}00\%$ sobre a taxa de liquidação da Câmara** em operações regulares e day-trade, além de isenção de tarifas de abertura de conta de custódia internacional.

### 5.2 Fundos de Investimento (Resolução CVM nº 175)
- Fundos e clubes de investimento têm seu ADTV calculado de forma agregada no nível do Administrador ou Gestor Fiduciário.
- Isenção de taxa de registro e tarifas de movimentação de custódia decorrentes de subscrição, resgate em cotas ou rebalanceamento passivo de carteira.

### 5.3 Formadores de Mercado (Programa PMM — FL-046)
- Conforme especificado na governança de formador de mercado (FL-046), instituições atuando sob contrato formal de formador de mercado credenciado na B3 contam com **isenção integral ($100\%$ de desconto)** sobre emolumentos e taxas de liquidação nas operações executadas no livro de ofertas do respectivo ativo.

---

## 6. Fórmulas de Cálculo e Fechamento Contábil Determinístico

Para assegurar total rastreabilidade, o motor tarifário calcula as grandezas financeiras com precisão decimal exata (`Decimal`), aplicando arredondamento bancário/comercial (`ROUND_HALF_UP`) aos centavos ($R\$\;0{,}01$):

### 6.1 Volume Financeiro Bruto
$$V_t = Q_t \times P_t$$

### 6.2 Parcelas Tarifárias
$$\begin{aligned}
\text{Emolumento}_t &= \text{round}(V_t \times \text{Rate}_{\text{negociação}}^{\text{Tier, Modality}}, 2) \\
\text{TaxaLiquidação}_t &= \text{round}(V_t \times \text{Rate}_{\text{liquidação}}^{\text{Tier, Modality}} \times (1 - \text{Discount}_{\text{categoria}}), 2) \\
\text{TaxaRegistro}_t &= \text{round}(V_t \times \text{Rate}_{\text{registro}}, 2) \\
\text{TarifaTotal}_t &= \text{Emolumento}_t + \text{TaxaLiquidação}_t + \text{TaxaRegistro}_t
\end{aligned}$$

### 6.3 Economia Gerada por Day-Trade (*Day-Trade Savings*)
Para cada negócio classificado como Day-Trade, a economia em relação ao custo regular é calculada deterministamente:

$$\text{Savings}_t = \text{TarifaTotal}_{t,\text{regular}} - \text{TarifaTotal}_{t,\text{daytrade}}$$

### 6.4 Invariantes de Fechamento Diário (Extrato Tarifário)
No extrato tarifário consolidado (`DailyTariffSummary`), as seguintes leis de conservação devem ser rigorosamente atendidas:

$$\sum_{t=1}^{M} V_t = V_{\text{Regular}} + V_{\text{DayTrade}}$$
$$\text{TotalTarifas} = \text{TotalEmolumentos} + \text{TotalTaxaLiquidação} + \text{TotalTaxaRegistro}$$
$$\sum_{t=1}^{M} \text{TarifaTotal}_t = \text{TotalTarifas}$$
