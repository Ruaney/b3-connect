# Especificação Técnica do Leiaute IMBARQ012 — Posições e Eventos da Depositária de Listados B3

## 1. Visão Geral e Arquitetura do Arquivo

O arquivo **IMBARQ012** é o demonstrativo oficial gerado na grade de processamento noturno da B3 para reportar as **posições em custódia e eventos societários pendentes na Depositária Central de Ativos Listados** (ações, cotas de fundos/ETFs, BDRs e units).

Com a descontinuação do antigo arquivo consolidado **IMBARQ001** em 30/06/2024, a B3 desagregou a visão de pós-negociação em demonstrativos especializados. Enquanto o **IMBARQ006** reflete as posições em aberto na Câmara de Compensação e Liquidação (Clearing), o **IMBARQ012** reflete os saldos escriturais de custódia do investidor mantidos na Depositária Central (CSD).

### 1.1 Características Físicas
- **Formato**: Arquivo posicional de largura fixa (flat-file).
- **Tamanho do Registro**: Rigorosamente **200 caracteres** por linha (excluindo terminador de linha).
- **Terminador de Linha**: Padrão Unix (`\n` - LF, 0x0A) ou Windows (`\r\n` - CRLF, 0x0D0A).
- **Codificação**: ASCII / ISO-8859-1 (sem caracteres especiais ou acentuação gráfica nos identificadores).
- **Hierarquia de Registros**:
  1. **Header (00)**: Exatamente 1 linha no início do arquivo.
  2. **Detalhes (01)**: Zero ou mais linhas consecutivas de posições da depositária.
  3. **Trailler (99)**: Exatamente 1 linha no encerramento do arquivo.

### 1.2 Convenção de Tipos de Dados

| Sigla | Descrição | Regra de Formatação / Alinhamento |
|---|---|---|
| `X` | Alfanumérico | Texto alinhado à esquerda, completado com espaços à direita (` `). |
| `N` | Numérico Inteiro | Dígitos decimais `0-9`, alinhado à direita, completado com zeros à esquerda (`0`). |
| `V` | Numérico Decimal | Casas decimais implícitas (sem ponto ou vírgula). Ex.: `N(13)V2` = 15 dígitos totais com 2 casas decimais (`000000000003850` = `38,50`). |
| `D` | Data | 8 dígitos no formato `YYYYMMDD` (ano, mês, dia). Ex.: `20261103`. |
| `H` | Hora | 6 dígitos no formato `HHMMSS` (hora, minuto, segundo). Ex.: `230000`. |

---

## 2. Especificação dos Registros

### 2.1 Registro Header (Tipo 00) — Controle de Abertura

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `00`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ012`. |
| `versao_layout` | 12 | 14 | 3 | `N` | S | Versão da especificação: `001`. |
| `data_geracao` | 15 | 22 | 8 | `D` | S | Data de geração física do arquivo (`YYYYMMDD`). |
| `hora_geracao` | 23 | 28 | 6 | `H` | S | Hora de geração física do arquivo (`HHMMSS`). |
| `data_movimento` | 29 | 36 | 8 | `D` | S | Data de referência da custódia (`YYYYMMDD`). |
| `codigo_participante` | 37 | 44 | 8 | `N` | S | Código do custodiante/participante na B3 (zeros à esquerda). Ex.: `00000105`. |
| `nome_participante` | 45 | 74 | 30 | `X` | S | Razão social abreviada do custodiante. |
| `sequencial_remessa` | 75 | 80 | 6 | `N` | S | Sequencial diário do arquivo para a instituição. Ex.: `000001`. |
| `reservado` | 81 | 200 | 120 | `X` | S | Filler preenchido com espaços em branco. |

---

### 2.2 Registro de Detalhe (Tipo 01) — Posição da Depositária de Listados

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `01`. |
| `codigo_participante` | 3 | 10 | 8 | `N` | S | Código do participante / custodiante na B3. |
| `codigo_conta_custodia` | 11 | 22 | 12 | `N` | S | Conta de custódia do investidor / comitente na B3. Ex.: `000000123456`. |
| `codigo_negociacao` | 23 | 34 | 12 | `X` | S | Código de negociação do ativo (Ticker). Ex.: `PETR4       `. |
| `codigo_isin` | 35 | 46 | 12 | `X` | S | Código ISIN internacional do ativo. Ex.: `BRPETRACNPR6`. |
| `tipo_mercado` | 47 | 49 | 3 | `X` | S | `VIS` (À Vista), `ETF` (Fundos de Índice), `FII` (Imobiliário), `BDR` (Dep. Receipts). |
| `quantidade_total` | 50 | 64 | 15 | `N` | S | Quantidade total escritural mantida na conta de custódia. |
| `quantidade_disponivel` | 65 | 79 | 15 | `N` | S | Quantidade livre para negociação, liquidação de vendas ou transferência. |
| `quantidade_bloqueada_garantia` | 80 | 94 | 15 | `N` | S | Quantidade alocada como colateral/garantia na Clearing ou em contratos. |
| `quantidade_bloqueada_judicial` | 95 | 109 | 15 | `N` | S | Quantidade indisponível por ordem ou bloqueio judicial. |
| `quantidade_eventos_pendentes` | 110 | 124 | 15 | `N` | S | Quantidade de direitos provisionados / eventos societários em trânsito. |
| `preco_fechamento` | 125 | 139 | 15 | `N(13)V2` | S | Preço unitário de fechamento na data de movimento (2 decimais). Ex.: `000000000003850` = R$ 38,50. |
| `valor_financeiro` | 140 | 156 | 17 | `N(15)V2` | S | Valor financeiro total avaliado da posição (2 decimais). Ex.: `00000000003850000` = R$ 38.500,00. |
| `data_liquidacao_evento` | 157 | 164 | 8 | `D` | N | Data prevista de liquidação do evento societário (`YYYYMMDD`) ou `00000000`. |
| `tipo_evento` | 165 | 172 | 8 | `X` | N | Identificador do evento corporativo (ex: `BONIF   `, `SUBSC   `, `DIVID   `) ou espaços. |
| `reservado` | 173 | 200 | 28 | `X` | S | Filler preenchido com espaços em branco. |

---

### 2.3 Registro Trailler (Tipo 99) — Totalização e Fechamento

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `99`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ012`. |
| `total_registros_arquivo` | 12 | 19 | 8 | `N` | S | Contagem total de linhas do arquivo (Header + Detalhes + Trailler). |
| `total_registros_detalhe` | 20 | 27 | 8 | `N` | S | Quantidade exata de registros do tipo `01`. |
| `total_quantidade_total` | 28 | 45 | 18 | `N` | S | Somatório de `quantidade_total` de todos os registros `01`. |
| `total_quantidade_disponivel` | 46 | 63 | 18 | `N` | S | Somatório de `quantidade_disponivel` de todos os registros `01`. |
| `total_quantidade_bloqueada` | 64 | 81 | 18 | `N` | S | Somatório de todas as quantidades bloqueadas (`bloqueada_garantia + bloqueada_judicial`). |
| `total_eventos_pendentes` | 82 | 99 | 18 | `N` | S | Somatório de `quantidade_eventos_pendentes` de todos os registros `01`. |
| `total_valor_financeiro` | 100 | 117 | 18 | `N(16)V2` | S | Somatório aritmético do campo `valor_financeiro` de todos os registros `01` (2 decimais). |
| `reservado` | 118 | 200 | 83 | `X` | S | Filler preenchido com espaços em branco. |

---

## 3. Regras de Negócio e Validações de Integridade

### 3.1 Integridade Estrutural
1. **Comprimento Invariante**: Cada registro deve possuir exatamente 200 colunas. Qualquer linha com tamanho diferente resulta em rejeição sumária (`InvalidRecordLengthError`).
2. **Sequência Canônica**:
   - Linha 1: obrigatoriamente Header (`00`).
   - Linhas 2 a N-1: obrigatoriamente Detalhes (`01`).
   - Linha N: obrigatoriamente Trailler (`99`).
   - Arquivos com menos de 2 linhas são rejeitados (`InvalidRecordStructureError`).

### 3.2 Consistência Algébrica de Custódia (Nível Registro)
Para cada registro de detalhe `01`, a custódia exige rigorosa reconciliação dos saldos:
$$\text{quantidade\_total} = \text{quantidade\_disponivel} + \text{quantidade\_bloqueada\_garantia} + \text{quantidade\_bloqueada\_judicial}$$

Caso qualquer registro apresente divergência entre o saldo total e a soma de suas parcelas (ou quantidades negativas), o processamento é abortado com `PositionConsistencyError`.

### 3.3 Tratamento de Eventos Societários Pendentes
- Direitos decorrentes de eventos corporativos (ex.: frações de bonificação, sobras de subscrição) que se encontram em período de subscrição ou provisionamento são declarados em `quantidade_eventos_pendentes`.
- Quando houver evento pendente (`quantidade_eventos_pendentes > 0`), os campos `tipo_evento` e `data_liquidacao_evento` identificam a natureza do direito e a data prevista de crédito/liquidação.

### 3.4 Batimento Numérico e Checksum do Trailler
O Trailler funciona como um checksum posicional obrigatório. O parser executa as seguintes verificações:
- `total_registros_arquivo == count(todas as linhas)`
- `total_registros_detalhe == count(linhas tipo 01)`
- `total_quantidade_total == sum(detalhe.quantidade_total)`
- `total_quantidade_disponivel == sum(detalhe.quantidade_disponivel)`
- `total_quantidade_bloqueada == sum(detalhe.quantidade_bloqueada_garantia + detalhe.quantidade_bloqueada_judicial)`
- `total_eventos_pendentes == sum(detalhe.quantidade_eventos_pendentes)`
- `total_valor_financeiro == sum(detalhe.valor_financeiro)`

Se qualquer uma das somas divergir, o processamento deve ser abortado com `TrailerValidationError`, impedindo que dados truncados ou adulterados cheguem ao livro de custódia interno.
