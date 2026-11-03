# Especificação Técnica — Derivativos de Balcão B3 (NoMe): Registro e Liquidação de Contratos de Swap e Termo de Moeda NDF (FL-040)

## 1. Visão Geral e Arcabouço Regulatório

O módulo de **Derivativos de Balcão Registrados na B3 (NoMe — Novo Mercado de Balcão / Registradora B3)** provê a infraestrutura de registro escritural, conciliação bilateral (duplo comando), apreçamento a mercado, apuração determinística de fatores acumulados, retenção tributária na fonte (IRRF) e liquidação financeira de **Contratos de Swap** e **Contratos a Termo de Moeda sem Entrega Física (*Non-Deliverable Forward* — NDF)**.

O fluxo **FL-040** modela rigorosamente as exigências normativas do Sistema Financeiro Nacional:
1. **Resolução CMN nº 4.952/2021 e Resolução BCB nº 264/2022**:
   - Torna obrigatório o registro em sistema de registro e de liquidação financeira de ativos autorizado pelo Banco Central do Brasil (BCB) ou pela Comissão de Valores Mobiliários (CVM) de todas as operações de derivativos de balcão contratadas por instituições financeiras e demais instituições autorizadas a funcionar pelo BCB.
   - Impõe a **identificação unívoca do contrato** (`contract_id` / protocolo NoMe), qualificação das partes (CNPJ da Instituição Financeira / *Dealer* e CNPJ/CPF da Contraparte Corporativa ou Institucional), modalidade de garantia (Com Garantia na Câmara B3 vs. Sem Garantia / Risco Bilateral de Crédito) e finalidade econômica (*Hedge* Corporativo vs. Posição Direcional/Tesouraria).
2. **Instrução Normativa RFB nº 1.585/2015 (arts. 56 a 64) e Lei nº 11.033/2004**:
   - Disciplina a tributação de operações de **Swap** e **Termo de Moeda (NDF)** no mercado de balcão organizado: o Imposto de Renda Retido na Fonte (**IRRF**) incide **exclusivamente sobre o ajuste financeiro líquido positivo (ganho de capital / rendimento bruto positivo)** apurado na liquidação (vencimento ou liquidação antecipada), segundo a tabela regressiva de renda fixa (22,5% até 180 dias; 20,0% de 181 a 360 dias; 17,5% de 361 a 720 dias; 15,0% acima de 720 dias) para contrapartes tributáveis, respeitada a isenção/não-retenção na fonte para instituições financeiras ou carteiras de fundos isentos (`irrf_exempt = True`).
3. **Princípio da Conservação Bilateral Soma-Zero (*Zero-Sum Bilateral Conservation*)**:
   - Todo contrato derivativo bilateral registrado no Balcão B3 é um jogo estritamente de soma-zero no nível bruto antes de impostos:
     $$\text{Ajuste Bruto}_{\text{Parte A}} + \text{Ajuste Bruto}_{\text{Parte B}} = \text{R\$ } 0,00$$
   - A parte que apura ajuste bruto positivo ($\text{Ajuste Bruto} > 0$) é a **Credora da Liquidação** (sofre retenção de IRRF se tributável e recebe o valor líquido), enquanto a contraparte que apura ajuste bruto negativo ($\text{Ajuste Bruto} < 0$) é a **Devedora da Liquidação** (desembolsa exatamente $|\text{Ajuste Bruto}|$, sem incidência de IRRF sobre perda).

---

## 2. Modalidades Operacionais e de Garantia no Balcão B3

| Modalidade de Garantia (`GuaranteeMode`) | Código | Papel da Câmara B3 / Registradora NoMe | Risco de Crédito e Margem |
|---|---|---|---|
| **Sem Garantia CCP** (Registro Escritural Bilateral) | `SEM_GARANTIA_CCP` | Atua como Central Registradora (NoMe) e liquidante financeira via STR/CETIP (NoMe Liquidação). Não há interposição de Contraparte Central. | Risco bilateral puro entre Banco (*Party A*) e Cliente/Empresa (*Party B*), mitigado opcionalmente por CDBs/Títulos gravados em Cessão Fiduciária (FL-037). |
| **Com Garantia CCP** (Câmara B3 como Contraparte Central) | `COM_GARANTIA_CCP` | A Câmara B3 interpõe-se por novação como compradora para o vendedor e vendedora para o comprador, garantindo a liquidação do ajuste final. | Exige depósito de Margem Inicial (CORE) e chamada de garantias pelos participantes de negociação/liquidação. |

---

## 3. Modelo Matemático — Contrato de Swap (`OtcSwapContract`)

Um **Contrato de Swap** consiste no acordo bilateral de troca de rentabilidades (indexadores) incidentes sobre um mesmo **Valor Base / Notional ($N$)** em Reais ($\text{BRL}$) durante o prazo compreendido entre a data de início ($T_0$) e a data de vencimento/liquidação ($T_v$), abrangendo $DU$ dias úteis (convenção padrão de mercado brasileiro $DU/252$) ou $DC$ dias corridos ($DC/360$ para cupom cambial linear).

Pela convenção de registro na B3, define-se a perspectiva da **Parte A (`party_a` — Titular da Ponta Ativa)**:
- **Ponta Ativa (`active_leg` — `SwapLegSpec`)**: Indexador e taxa cujo rendimento bruto a **Parte A recebe** (e a Parte B paga).
- **Ponta Passiva (`passive_leg` — `SwapLegSpec`)**: Indexador e taxa cujo rendimento bruto a **Parte A paga** (e a Parte B recebe).

### 3.1 Cálculo do Fator Acumulado de Cada Ponta ($F_{\text{leg}}$)

Os fatores acumulados ($F_{\text{ativo}}$ e $F_{\text{passivo}}$) são calculados com alta precisão decimal e truncados/arredondados para **8 casas decimais** (`0.00000001`, padrão Cetip/NoMe B3) conforme o indexador (`IndexerType`):

1. **Ponta Indexada ao CDI (`IndexerType.CDI`)**:
   - Quando fornecido o fator acumulado composto da série histórica de taxas DI-Over ($F_{\text{DI}}$) e um percentual do CDI ($p_{\text{CDI}}$, ex: $100\%$ ou $105\%$) ou *spread* exponencial ($s_{\text{aa}}$ em base 252):
   - Pela parametrização de taxa efetiva média anualizada do DI ($\text{DI}_{\text{aa}}$ em $\%$ a.a.), percentual do DI ($p_{\text{CDI}}$ em $\%$) e *spread* anual ($s_{\text{aa}}$ em $\%$ a.a.) por $DU$ dias úteis:
     $$F_{\text{DI\_puro}} = \left(1 + \frac{\text{DI}_{\text{aa}}}{100}\right)^{\frac{DU}{252}}$$
     $$F_{\text{CDI\_pct}} = 1 + \left(F_{\text{DI\_puro}} - 1\right) \times \frac{p_{\text{CDI}}}{100}$$
     $$F_{\text{leg}} = F_{\text{CDI\_pct}} \times \left(1 + \frac{s_{\text{aa}}}{100}\right)^{\frac{DU}{252}}$$
   - *(Observação: quando $p_{\text{CDI}} = 100\%$ e $s_{\text{aa}} = 0\%$, reduz-se exatamente a $F_{\text{leg}} = (1 + \text{DI}_{\text{aa}}/100)^{DU/252}$).*

2. **Ponta Prefixada (`IndexerType.PRE`)**:
   - Capitalização exponencial em dias úteis ($DU/252$) à taxa prefixada contratada ($r_{\text{pre}}$ em $\%$ a.a.):
     $$F_{\text{leg}} = \left(1 + \frac{r_{\text{pre}}}{100}\right)^{\frac{DU}{252}}$$

3. **Ponta Variação Cambial Dólar / PTAX + Cupom Cambial (`IndexerType.DOLAR` / `USD`)**:
   - Combina a variação da cotação PTAX de venda divulgada pelo Banco Central entre a data-base inicial ($S_0 = \text{PTAX}_{\text{inicial}}$) e a data de referência de liquidação ($S_{\text{final}} = \text{PTAX}_{\text{fechamento}}$) com o cupom cambial ($c_{\text{cupom}}$ em $\%$ a.a.):
   - Convenção linear ($DC/360$, padrão de mercado para Cupom Cambial Sujo/Limpo no Balcão B3) ou exponencial ($DU/252$, se parametrizado):
     - No regime padrão linear $DC/360$ (ou $DU/252$ quando `dc_days` não informado, usando $t = DC/360$ se $DC > 0$ senão $DU/252$):
       $$F_{\text{câmbio}} = \frac{S_{\text{final}}}{S_0}$$
       $$F_{\text{cupom}} = 1 + \left(\frac{c_{\text{cupom}}}{100} \times \frac{DC}{360}\right) \quad \text{(se } DC > 0\text{, ou } \left(1 + \frac{c_{\text{cupom}}}{100}\right)^{\frac{DU}{252}}\text{)}$$
       $$F_{\text{leg}} = F_{\text{câmbio}} \times F_{\text{cupom}}$$

4. **Ponta IPCA + Cupom de Juros Reais (`IndexerType.IPCA`)**:
   - Combina a razão do Número-Índice do IPCA/IBGE (ou variação percentual acumulada $\Delta_{\text{IPCA}}$) com o cupom real exponencial ($r_{\text{real}}$ em $\%$ a.a. base $DU/252$):
     $$F_{\text{leg}} = \left(1 + \frac{\Delta_{\text{IPCA}}}{100}\right) \times \left(1 + \frac{r_{\text{real}}}{100}\right)^{\frac{DU}{252}}$$

### 3.2 Apuração do Valor Final das Pontas e Ajuste Líquido no Vencimento

Dado o Valor Base ($N = \text{Notional}_{\text{BRL}}$):
- **Valor Atualizado da Ponta Ativa ($V_{\text{ativo}}$)**:
  $$V_{\text{ativo}} = N \times F_{\text{ativo}}$$
- **Valor Atualizado da Ponta Passiva ($V_{\text{passivo}}$)**:
  $$V_{\text{passivo}} = N \times F_{\text{passivo}}$$
- **Ajuste Financeiro Bruto da Parte A ($\text{Ajuste}_{\text{A}}$)**:
  $$\text{Ajuste}_{\text{A}} = \text{round}_2\left(N \times \left(F_{\text{ativo}} - F_{\text{passivo}}\right)\right)$$
- **Ajuste Financeiro Bruto da Parte B ($\text{Ajuste}_{\text{B}}$)**:
  $$\text{Ajuste}_{\text{B}} = -\text{Ajuste}_{\text{A}}$$

---

## 4. Modelo Matemático — Termo de Moeda sem Entrega Física (`OtcNdfContract` — NDF)

O **Termo de Moeda (*Non-Deliverable Forward* — NDF)** é o instrumento derivativo de balcão pelo qual duas partes travam uma **Cotação a Termo ($K$)** (expressa em $\text{R\$ / unidade de moeda estrangeira}$, ex: $\text{BRL/USD}$ ou $\text{BRL/EUR}$) sobre um **Notional em Moeda Estrangeira ($N_{\text{FX}}$)** para liquidação futura exclusivamente pela diferença financeira em moeda nacional ($\text{BRL}$), tendo como referência a **Cotação PTAX800 de Venda ($S_{\text{PTAX}}$)** apurada pelo Banco Central do Brasil no dia útil imediatamente anterior ao vencimento ($D-1$).

### 4.1 Direção da Posição (`NdfDirection`) e Ajuste Financeiro

Seja $N_{\text{FX}} > 0$ o notional em moeda estrangeira (ex: $\text{US\$ } 1.000.000,00$), $K > 0$ a taxa a termo contratada (até 4 casas decimais) e $S_{\text{PTAX}} > 0$ a PTAX de liquidação (até 4 casas decimais):

1. **Se a Parte A é COMPRADORA a Termo (`NdfDirection.BUYER` / *Long USD*)**:
   - A Parte A protege-se contra a alta da moeda estrangeira (ex: importador ou empresa com dívida em dólar) e a Parte B é vendedora a termo (*Short USD*):
     $$\text{Ajuste}_{\text{A}} = \text{round}_2\left(N_{\text{FX}} \times \left(S_{\text{PTAX}} - K\right)\right)$$
     $$\text{Ajuste}_{\text{B}} = -\text{Ajuste}_{\text{A}} = \text{round}_2\left(N_{\text{FX}} \times \left(K - S_{\text{PTAX}}\right)\right)$$
   - Se $S_{\text{PTAX}} > K$, a moeda estrangeira valorizou acima do termo: a Parte A (compradora) **recebe** a diferença positiva; se $S_{\text{PTAX}} < K$, a Parte A **paga** a diferença à Parte B.

2. **Se a Parte A é VENDEDORA a Termo (`NdfDirection.SELLER` / *Short USD*)**:
   - A Parte A protege-se contra a queda da moeda estrangeira (ex: exportador com recebíveis em dólar) e a Parte B é compradora a termo (*Long USD*):
     $$\text{Ajuste}_{\text{A}} = \text{round}_2\left(N_{\text{FX}} \times \left(K - S_{\text{PTAX}}\right)\right)$$
     $$\text{Ajuste}_{\text{B}} = -\text{Ajuste}_{\text{A}} = \text{round}_2\left(N_{\text{FX}} \times \left(S_{\text{PTAX}} - K\right)\right)$$
   - Se $S_{\text{PTAX}} < K$, a Parte A (vendedora a termo) **recebe** a diferença positiva porque travou a venda a uma taxa $K$ superior à PTAX de fechamento.

---

## 5. Tributação na Fonte (IRRF) e Liquidação Financeira (`OtcSettlementResult`)

Na data de liquidação do contrato (Swap ou NDF), o motor da Registradora B3 (`OtcDerivativesEngine`) apura a obrigação líquida de transferência financeira:

1. **Identificação do Ganhador (Credor Bruto) e do Perdedor (Devedor Bruto)**:
   - Se $\text{Ajuste}_{\text{A}} > 0$:
     - Parte A tem ganho bruto $G_{\text{A}} = \text{Ajuste}_{\text{A}}$; Parte B tem desembolso bruto $D_{\text{B}} = |\text{Ajuste}_{\text{B}}| = \text{Ajuste}_{\text{A}}$.
   - Se $\text{Ajuste}_{\text{A}} < 0$:
     - Parte B tem ganho bruto $G_{\text{B}} = \text{Ajuste}_{\text{B}} > 0$; Parte A tem desembolso bruto $D_{\text{A}} = |\text{Ajuste}_{\text{A}}|$.
   - Se $\text{Ajuste}_{\text{A}} = 0$:
     - Não há transferência financeira nem retenção de IRRF ($\text{IRRF} = 0$).

2. **Regra de Retenção de IRRF apenas sobre Ganho Positivo**:
   - Para cada parte $P \in \{\text{A}, \text{B}\}$ com alíquota efetiva $\tau_P \in [0, 1)$ (ex: $0,1500 = 15,0\%$, $0,2000 = 20,0\%$, $0,2250 = 22,5\%$) e indicador de isenção $\text{exempt}_P \in \{\text{True}, \text{False}\}$:
     $$\text{IRRF}_P = \begin{cases} \text{round}_2\left(\text{Ajuste}_P \times \tau_P\right), & \text{se } \text{Ajuste}_P > 0 \text{ e } \neg\text{exempt}_P \\ 0,00, & \text{se } \text{Ajuste}_P \le 0 \text{ ou } \text{exempt}_P \end{cases}$$

3. **Liquidação Financeira Líquida de Impostos ($\text{Net Settlement}_P$)**:
   $$\text{Net}_{\text{A}} = \text{Ajuste}_{\text{A}} - \text{IRRF}_{\text{A}}$$
   $$\text{Net}_{\text{B}} = \text{Ajuste}_{\text{B}} - \text{IRRF}_{\text{B}}$$
   - A parte devedora ($\text{Ajuste}_P < 0$) desembolsa integralmente o ajuste bruto ($\text{Net}_P = \text{Ajuste}_P < 0$, pois $\text{IRRF}_P = 0$).
   - A instituição financeira responsável tributária retém o $\text{IRRF}$ do ganhador tributável e repassa o valor líquido $\text{Net}_{\text{ganhador}} = \text{Ajuste}_{\text{ganhador}} - \text{IRRF}_{\text{ganhador}}$ via compensação NoMe/CETIP ou STR.

---

## 6. Fluxo Operacional de Registro e Liquidação na B3 (NoMe)

```mermaid
sequenceDiagram
    autonumber
    participant Dealer as Banco Dealer (Parte A)
    participant Corp as Empresa / Fundo (Parte B)
    participant NoMe as Registradora B3 (NoMe Balcão)
    participant BCB as Banco Central (Série DI / PTAX800)
    participantLiq as Liquidante Financeiro (CETIP / STR)

    Dealer->>NoMe: 1. Comando de Registro (Swap ou NDF: Notional, Taxas, Vencimento)
    Corp->>NoMe: 2. Duplo Comando / Confirmação Eletrônica (Adesão Res. CMN 4.952)
    NoMe-->>Dealer: 3. Emissão do Código de Registro Escritural (Protocolo NoMe Ativo)
    BCB->>NoMe: 4. Divulgação de Fatores DI-Over (DU/252) e Cotação PTAX800 (D-1)
    NoMe->>NoMe: 5. Apuração Determinística (F_ativo, F_passivo ou S_PTAX - K)
    NoMe->>NoMe: 6. Validação Invariante Soma-Zero (Gross_A + Gross_B == 0) e Cálculo IRRF
    NoMe->>participantLiq: 7. Instrução de Liquidação Financeira (Débito Devedor / Crédito Líquido Credor)
```

---

## 7. Invariantes Determinísticos Audíveis (FL-040)

1. **Invariante 1 — Conservação Bilateral Soma-Zero Bruta**:
   - Em 100% das apurações de `OtcSwapContract` e `OtcNdfContract`, a soma algébrica dos ajustes brutos das duas contrapartes é identicamente nula:
     $$\text{party\_a\_gross\_brl} + \text{party\_b\_gross\_brl} = \text{Decimal("0.00")}$$
2. **Invariante 2 — Assimetria Tributária Estrita (IRRF nunca incide sobre perda)**:
   - Se $\text{party\_a\_gross\_brl} \le 0$, então $\text{party\_a\_irrf\_brl} = 0,00$ e $\text{party\_a\_net\_brl} = \text{party\_a\_gross\_brl}$.
   - Analogamente, se $\text{party\_b\_gross\_brl} \le 0$, então $\text{party\_b\_irrf\_brl} = 0,00$ e $\text{party\_b\_net\_brl} = \text{party\_b\_gross\_brl}$.
   - A soma dos fluxos líquidos mais o IRRF retido para o Erário preserva o equilíbrio contábil total:
     $$\text{party\_a\_net\_brl} + \text{party\_b\_net\_brl} + \text{total\_irrf\_brl} = \text{Decimal("0.00")}$$
3. **Invariante 3 — Precisão de Fatores Acumulados (8 Casas Decimais) e Moeda (2 Casas Decimais)**:
   - Todos os fatores de atualização de pontas de Swap ($F_{\text{ativo}}$, $F_{\text{passivo}}$) são quantizados em 8 casas decimais (`0.00000001`) com arredondamento `ROUND_HALF_UP`, e todas as grandezas monetárias finais em $\text{BRL}$ são quantizadas em 2 casas decimais (`0.01`).
4. **Invariante 4 — Simetria Direcional no NDF**:
   - Inverter a direção do NDF (`BUYER` $\leftrightarrow$ `SELLER`) mantendo os mesmos parâmetros ($N_{\text{FX}}, K, S_{\text{PTAX}}$) inverte exatamente o sinal dos ajustes brutos de ambas as partes.
