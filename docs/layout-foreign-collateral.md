# Especificação Técnica — Garantias em Moeda Estrangeira e Ativos Internacionais (FL-022)

## 1. Visão Geral e Arquitetura de Risco da Câmara B3

A Câmara B3 atua como Contraparte Central (CCP - *Central Counterparty*) para os mercados financeiro e de capitais no Brasil. Para mitigar o risco de crédito de contraparte em operações com derivativos listados, empréstimo de ativos e liquidação multilateral de ações e renda fixa, a B3 adota um modelo multicamadas de salvaguardas (*waterfall*).

O sistema **CORE** (*Close-Out Risk Evaluation*) calcula a **Margem Requerida** para a carteira de cada comitente em moeda nacional (BRL). Para cobrir esse requerimento, os participantes podem depositar, além de ativos domésticos (reais em espécie, NTN-B, LFT, LTN e ações do índice IBrX-100), uma cesta diversificada de **Garantias Internacionais e Ouro**:
1. **USD em espécie (*Cash Collateral*)**: Depósitos em dólares norte-americanos mantidos em contas de custódia e liquidação da B3 no exterior (Citibank N.A. New York / Federal Reserve Bank).
2. **US Treasuries (*T-Bills, T-Notes e T-Bonds*)**: Títulos da dívida pública soberana dos Estados Unidos, custodiados na *Depository Trust & Clearing Corporation* (DTCC) ou via *Euroclear* / *Clearstream*.
3. **Ouro Físico (*Contratos OZ1D, OZ2D e OZ3D*)**: Ativo negociado na própria B3 com custódia física garantida em cofre central especializado (Cofre Central Banco do Brasil / B3 São Paulo), com teor de pureza mínimo de 999,0 partes por mil (padrão *London Good Delivery*).
4. **BDRs de Dívida (*Debt BDRs*)**: Certificados de depósito de valores mobiliários representativos de títulos de dívida corporativa ou governamental internacional (*Global Bonds* / *Eurobonds*), negociados no mercado local.

Esta especificação define o modelo matemático de avaliação, a matriz de *haircuts* regulatórios, a regra de conversão cambial via PTAX e a lógica de apuração de cobertura de margem requerida da câmara (FL-022).

---

## 2. Categorias de Ativos e Metodologia de Custódia

| Código de Ativo | Tipo de Instrumento | Denominação | Local de Custódia | Liquidação / Movimentação |
|---|---|---|---|---|
| `USD_CASH` | Dólar em espécie | USD | Citibank N.A. (NY) / Fedwire | Transferência via SWIFT MT202 / Fedwire na conta B3 NY. |
| `US_TREASURY_BILL` | US Treasury Bill (< 1 ano) | USD | DTCC / Euroclear | Transferência livre de pagamento (FOP) via DTC account. |
| `US_TREASURY_NOTE` | US Treasury Note (1 a 10 anos)| USD | DTCC / Euroclear | Transferência FOP via DTC / Fedwire Securities. |
| `GOLD_OZ1D` | Ouro Ativo Financeiro (250g) | BRL | Cofre Central B3 / BB SP | Bloqueio eletrônico de custódia do lingote custodiado. |
| `GOLD_OZ2D` | Ouro Ativo Financeiro (10g) | BRL | Cofre Central B3 / BB SP | Bloqueio eletrônico de custódia. |
| `GOLD_OZ3D` | Ouro Ativo Financeiro (0,225g) | BRL | Cofre Central B3 / BB SP | Bloqueio eletrônico de custódia. |
| `DEBT_BDR` | BDR de Renda Fixa / Dívida | BRL | Depositária B3 (CSD) | Bloqueio de custódia de BDRs escriturais no sistema NoMe/RTC. |

### 2.1 Critérios de Elegibilidade Estrita
1. **Ativos em Moeda Estrangeira**:
   - Apenas moedas autorizadas são aceitas: **USD** (Dólar norte-americano), **EUR** (Euro) e **BRL** (moeda doméstica).
   - Moedas sancionadas internacionalmente (OFAC / ONU) ou com controles de capital severos são expressamente **bloqueadas**: `RUB`, `IRR`, `KPW`, `SYR`, `VEF`, `CUP`, `BYN`. Depósitos nestas moedas sofrem rejeição imediata com código de erro `BLOCKED_CURRENCY`.
2. **US Treasuries**:
   - Devem ter prazo remanescente até o vencimento (*maturity*) superior a 3 dias úteis.
   - Devem possuir precificação pública de mercado (*closing price* divulgado pela Bloomberg, Refinitiv ou Federal Reserve H.15).
3. **Ouro Físico**:
   - Contratos padrões da B3 (OZ1D, OZ2D, OZ3D).
   - Obrigatoriedade de certificado de ensaio (*assay certificate*) emitido por refinador ou fundidor credenciado pela B3.

---

## 3. Matriz e Metodologia de Haircuts Regulatórios

O *haircut* (deságio de avaliação) é um fator percentual redutor aplicado ao valor de mercado do colateral para absorver eventuais perdas financeiras entre a inadimplência do participante e a efetiva liquidação/conversão do ativo em reais pela CCP.

O modelo da Câmara B3 decompõe o *Haircut Total* ($H_{total}$) em três componentes aditivos independentes:

$$H_{total} = \min\left(H_{fx} + H_{vol} + H_{liq}, 1.00\right)$$

Onde:
1. **$H_{fx}$ (Haircut Cambial / Risco de Câmbio)**:
   - Absorve variações adversas na cotação cambial da moeda estrangeira frente ao Real entre o momento da chamada de margem e o fechamento do câmbio (*close-out* D+1 / D+2).
   - Padrão regulatório: **10.00%** para USD; **12.00%** para EUR. Ativos cotados nativamente em BRL possuem $H_{fx} = 0.00\%$.
2. **$H_{vol}$ (Haircut de Volatilidade e Preço / Duration)**:
   - Absorve flutuações de taxa de juros e preço do instrumento no mercado primário e secundário.
   - US Treasury Bills (duration curtíssima < 1 ano): **3.00%** (ou 5.00% consolidado).
   - US Treasury Notes (duration média 1 a 10 anos): **6.00%**.
   - Ouro físico OZ1D (volatilidade de commodity internacional): **12.00%**.
   - BDR de Dívida Corporativa (risco de spread de crédito): **8.00%**.
3. **$H_{liq}$ (Haircut de Liquidez e Custos Operacionais)**:
   - Absorve o custo de transação, *bid-ask spread* alargado em cenário de estresse e tarifas de remessa internacional / liquidação física.
   - Caixa (USD/EUR): **0.00%** (liquidez imediata).
   - T-Bills: **2.00%**.
   - Ouro físico: **3.00%** (custos de frete seguro, custódia e refino se necessário).
   - BDR de Dívida: **5.00%** (mercado secundário menos líquido).

### 3.1 Tabela Regulatória Padrão da Câmara

| Ativo | Categoria | Moeda | $H_{fx}$ | $H_{vol}$ | $H_{liq}$ | $H_{total}$ | Descrição Metodológica |
|---|---|---|---|---|---|---|---|
| `USD_CASH` | Moeda em Espécie | USD | 10.00% | 0.00% | 0.00% | **10.00%** | Deságio cambial puro regulamentar da câmara. |
| `US_TREASURY_BILL` | T-Bill Soberano | USD | 0.00%* | 3.00% | 2.00% | **5.00%** | Título público dos EUA < 1 ano (haircut total nominal 5%). |
| `US_TREASURY_NOTE` | T-Note Soberano | USD | 2.00% | 6.00% | 2.00% | **10.00%** | Título dos EUA 1 a 10 anos com risco de duration. |
| `GOLD_OZ1D` | Ouro Físico 250g | BRL | 0.00% | 12.00% | 3.00% | **15.00%** | Lingote padrão B3 no cofre central com volatilidade de commodity. |
| `DEBT_BDR` | BDR de Renda Fixa | BRL | 5.00% | 8.00% | 5.00% | **18.00%** | Certificado de dívida privada/externa com risco de crédito. |

*\*Nota Operacional: No caso de US Treasury Bills sob convênio bilateral de colateral internacional da B3, a tabela de haircut direto consolidada fixa o deságio global em 5.00%, facilitando a equivalência de margem para participantes internacionais (PNP/PL).*

---

## 4. Regras de Conversão Cambial PTAX e Avaliação em BRL

O motor de risco CORE consolida todos os saldos devedores e credores em **Reais (BRL)**. Desta forma, a avaliação de ativos multimoeda segue as seguintes etapas determinísticas:

### 4.1 Taxa de Conversão Cambial
- **PTAX Venda**: Cotação média de venda calculada e divulgada pelo Banco Central do Brasil (BACEN) no fechamento diário (Boletim 5 / PTAX de Fechamento) ou boletins intradiários (10h, 11h, 12h e 13h).
- Para ativos denominados em BRL (Ouro OZ1D e BDRs), a taxa de câmbio é unitária ($FX = 1.0000$).
- Para ativos denominados em USD, a taxa $FX$ corresponde à cotação do dólar comercial PTAX de venda.

### 4.2 Formulação Matemática
Para cada depósito $i$ do lote:

1. **Valor Bruto em Moeda Original**:
   $$V_{bruto\_orig}^{(i)} = Q^{(i)} \times P_{unit\_orig}^{(i)}$$

2. **Valor Bruto em BRL**:
   $$V_{bruto\_BRL}^{(i)} = V_{bruto\_orig}^{(i)} \times FX^{(i)}$$

3. **Dedução do Haircut**:
   $$D_{haircut\_BRL}^{(i)} = V_{bruto\_BRL}^{(i)} \times H_{total}^{(i)}$$

4. **Valor Líquido de Avaliação em BRL**:
   $$V_{net\_BRL}^{(i)} = V_{bruto\_BRL}^{(i)} - D_{haircut\_BRL}^{(i)} = V_{bruto\_BRL}^{(i)} \times (1 - H_{total}^{(i)})$$

5. **Consolidação da Cesta Multimoeda do Comitente**:
   $$V_{net\_total} = \sum_{i=1}^{N} V_{net\_BRL}^{(i)}$$

### 4.3 Cobertura de Margem Requerida (CORE)
Dada a **Margem Requerida** ($M_{req}$) em BRL calculada pelo CORE para o comitente:

$$S_{margem} = V_{net\_total} - M_{req}$$

A situação da margem é definida de forma binária:
- Se $S_{margem} \ge 0$: **Situação = "L" (Margem Livre / Excedente)**. O participante cumpre integralmente os requisitos de garantias e possui sobra de colateral para novas operações.
- Se $S_{margem} < 0$: **Situação = "D" (Defasagem / Chamada de Margem)**. O participante é notificado para depósito imediato da defasagem $|S_{margem}|$ até a janela de corte intradiária estipulada pela câmara.

---

## 5. Reavaliação Intradiária e Sensibilidade Cambial

A câmara executa reavaliações periódicas intradiárias (*Mark-to-Market*) à medida que novos boletins PTAX são divulgados pelo BACEN.
A sensibilidade do valor de avaliação frente a oscilações no câmbio ($FX$) é dada pela derivada:

$$\frac{\partial V_{net\_BRL}^{(i)}}{\partial FX} = Q^{(i)} \times P_{unit\_orig}^{(i)} \times (1 - H_{total}^{(i)}) > 0$$

- **Cenário de Desvalorização do Real (Alta da PTAX)**:
  O valor líquido de avaliação dos depósitos em USD aumenta em reais, gerando expansão de margem livre disponível ($S_{margem}$).
- **Cenário de Apreciação do Real (Queda da PTAX)**:
  O valor líquido de avaliação em reais diminui. Se a margem requerida se mantiver constante, a margem livre pode se extinguir, gerando chamada de margem compulsória (*Situation D*).

---

## 6. Layout e Formato do Arquivo de Depósito de Garantias

Os lotes de garantia em moeda estrangeira e ouro podem ser recebidos via mensageria JSON/REST na API de Colateral da Câmara B3. Cada lote contém o cabeçalho do participante e uma lista tipada de ativos.

### 6.1 Exemplo Estrutural de Lote JSON

```json
{
  "lote_id": "DEP-20261103-001",
  "codigo_participante": 105,
  "codigo_conta_custodia": 987654,
  "data_deposito": "2026-11-03",
  "margem_requerida_brl": 1500000.00,
  "itens": [
    {
      "item_id": "DEP-ITEM-01",
      "tipo_ativo": "USD_CASH",
      "ticker": "USD_CASH",
      "isin": "US000000USD0",
      "quantidade": 100000.00,
      "preco_unitario": 1.00,
      "moeda": "USD",
      "local_custodia": "CITIBANK_NY",
      "haircut": {
        "fx_haircut": 0.10,
        "volatility_haircut": 0.00,
        "liquidity_haircut": 0.00,
        "total_haircut": 0.10
      }
    },
    {
      "item_id": "DEP-ITEM-02",
      "tipo_ativo": "US_TREASURY_BILL",
      "ticker": "TBILL_2027",
      "isin": "US912797HR42",
      "quantidade": 2000.00,
      "preco_unitario": 98.50,
      "moeda": "USD",
      "local_custodia": "DTCC",
      "haircut": {
        "fx_haircut": 0.00,
        "volatility_haircut": 0.03,
        "liquidity_haircut": 0.02,
        "total_haircut": 0.05
      }
    },
    {
      "item_id": "DEP-ITEM-03",
      "tipo_ativo": "GOLD_OZ1D",
      "ticker": "OZ1D",
      "isin": "BROZ1DMULT01",
      "quantidade": 3.00,
      "preco_unitario": 112500.00,
      "moeda": "BRL",
      "local_custodia": "B3_VAULT_SP",
      "haircut": {
        "fx_haircut": 0.00,
        "volatility_haircut": 0.12,
        "liquidity_haircut": 0.03,
        "total_haircut": 0.15
      }
    }
  ]
}
```

---

## 7. Rastreabilidade com Normas da B3 e Banco Central

- **Resolução CMN nº 2.873/2001 e Circular BACEN nº 3.082**: Diretrizes de aceitação de garantias em moeda estrangeira e ouro físico por câmaras de compensação e liquidação autorizadas.
- **Manual de Risco da Câmara B3**: Capítulos IV (Garantias Aceitas) e V (Metodologia de Haircuts e Limites de Concentração).
- **Catálogo de Integrações B3**: Fluxo **FL-022** (*Depositar e gerenciar garantias em moeda estrangeira e ouro*), integrando a camada de colaterais com o sistema CORE.
