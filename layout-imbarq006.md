# Especificação Técnica do Leiaute IMBARQ006 — Posições da Câmara B3

## 1. Visão Geral e Arquitetura do Arquivo

O arquivo **IMBARQ006** é o demonstrativo oficial gerado na grade de processamento noturno da B3 para reportar as **posições em aberto mantidas na câmara de compensação e liquidação** (derivativos listados, ações a termo, opções e posições de bolsa).

### 1.1 Características Físicas
- **Formato**: Arquivo posicional de largura fixa (flat-file).
- **Tamanho do Registro**: Rigorosamente **200 caracteres** por linha (excluindo terminador de linha).
- **Terminador de Linha**: Padrão Unix (`\n` - LF, 0x0A) ou Windows (`\r\n` - CRLF, 0x0D0A).
- **Codificação**: ASCII / ISO-8859-1 (sem caracteres especiais ou acentuação gráfica nos identificadores).
- **Hierarquia de Registros**:
  1. **Header (00)**: Exatamente 1 linha no início do arquivo.
  2. **Detalhes (01)**: Zero ou mais linhas consecutivas de posições da câmara.
  3. **Trailler (99)**: Exatamente 1 linha no encerramento do arquivo.

### 1.2 Convenção de Tipos de Dados

| Sigla | Descrição | Regra de Formatação / Alinhamento |
|---|---|---|
| `X` | Alfanumérico | Texto alinhado à esquerda, completado com espaços à direita (` `). |
| `N` | Numérico Inteiro | Dígitos decimais `0-9`, alinhado à direita, completado com zeros à esquerda (`0`). |
| `V` | Numérico Decimal | Casas decimais implícitas (sem ponto ou vírgula). Ex.: `N(13)V2` = 15 dígitos totais com 2 casas decimais (`000000000012350` = `123,50`). |
| `D` | Data | 8 dígitos no formato `YYYYMMDD` (ano, mês, dia). Ex.: `20261103`. |
| `H` | Hora | 6 dígitos no formato `HHMMSS` (hora, minuto, segundo). Ex.: `230000`. |

---

## 2. Especificação dos Registros

### 2.1 Registro Header (Tipo 00) — Controle de Abertura

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `00`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ006`. |
| `versao_layout` | 12 | 14 | 3 | `N` | S | Versão da especificação: `001`. |
| `data_geracao` | 15 | 22 | 8 | `D` | S | Data de geração física do arquivo (`YYYYMMDD`). |
| `hora_geracao` | 23 | 28 | 6 | `H` | S | Hora de geração física do arquivo (`HHMMSS`). |
| `data_movimento` | 29 | 36 | 8 | `D` | S | Data de movimento/referência do pregão (`YYYYMMDD`). |
| `codigo_participante` | 37 | 44 | 8 | `N` | S | Código do participante na B3 (com zeros à esquerda). Ex.: `00000105`. |
| `nome_participante` | 45 | 74 | 30 | `X` | S | Razão social abreviada do participante. |
| `sequencial_remessa` | 75 | 80 | 6 | `N` | S | Sequencial diário do arquivo para a instituição. Ex.: `000001`. |
| `reservado` | 81 | 200 | 120 | `X` | S | Filler preenchido com espaços em branco. |

---

### 2.2 Registro de Detalhe (Tipo 01) — Posições da Câmara

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `01`. |
| `codigo_participante` | 3 | 10 | 8 | `N` | S | Código da corretora / participante de negociação (PN). |
| `codigo_conta_custodia` | 11 | 22 | 12 | `N` | S | Código da conta do comitente/investidor na B3. |
| `codigo_negociacao` | 23 | 34 | 12 | `X` | S | Código de negociação do ativo (Ticker). Ex.: `PETR4       `. |
| `codigo_isin` | 35 | 46 | 12 | `X` | S | Código ISIN internacional do ativo. Ex.: `BRPETRACNPR6`. |
| `tipo_mercado` | 47 | 49 | 3 | `X` | S | `VIS` (À Vista), `OPC` (Opções), `FUT` (Futuros), `TER` (Termo). |
| `sentido_posicao` | 50 | 50 | 1 | `X` | S | `C` (Comprada / Credora) ou `V` (Vendida / Devedora). |
| `quantidade_bruta` | 51 | 65 | 15 | `N` | S | Quantidade total da posição em aberto (inteiro). |
| `quantidade_bloqueada` | 66 | 80 | 15 | `N` | S | Quantidade bloqueada ou em trânsito de liquidação. |
| `quantidade_liquida` | 81 | 95 | 15 | `N` | S | Saldo disponível da posição (= Bruta - Bloqueada). |
| `preco_fechamento_ajuste` | 96 | 110 | 15 | `N(13)V2` | S | Preço de fechamento ou ajuste do ativo com 2 decimais implícitas. Ex.: `00000000003850` = R$ 38,50. |
| `valor_financeiro` | 111 | 127 | 17 | `N(15)V2` | S | Valor financeiro de referência com 2 decimais implícitas. Ex.: `0000000003850000` = R$ 38.500,00. |
| `data_vencimento` | 128 | 135 | 8 | `D` | N | Data de vencimento do contrato (`YYYYMMDD`) ou `00000000` para ações. |
| `codigo_serie` | 136 | 150 | 15 | `X` | N | Identificador da série (opções/futuros) ou espaços. Ex.: `PETRL350       `. |
| `participante_liquidacao` | 151 | 158 | 8 | `N` | S | Código do participante de liquidação (PL) responsável. |
| `reservado` | 159 | 200 | 42 | `X` | S | Filler preenchido com espaços em branco. |

---

### 2.3 Registro Trailler (Tipo 99) — Totalização e Fechamento

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `99`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ006`. |
| `total_registros_arquivo` | 12 | 19 | 8 | `N` | S | Contagem total de linhas do arquivo (Header + Detalhes + Trailler). |
| `total_registros_detalhe`| 20 | 27 | 8 | `N` | S | Quantidade exata de registros do tipo `01`. |
| `total_quantidade_liquida`| 28 | 45 | 18 | `N` | S | Somatório aritmético simples do campo `quantidade_liquida` de todos os registros `01`. |
| `total_valor_financeiro` | 46 | 63 | 18 | `N(16)V2` | S | Somatório aritmético do campo `valor_financeiro` de todos os registros `01` (com 2 decimais implícitas). |
| `reservado` | 64 | 200 | 137 | `X` | S | Filler preenchido com espaços em branco. |

---

## 3. Regras de Negócio e Validações de Integridade

### 3.1 Integridade Estrutural
1. **Comprimento Invariante**: Cada registro deve possuir exatamente 200 colunas. Qualquer linha com tamanho diferente resulta em rejeição sumária (`InvalidRecordLengthError`).
2. **Sequência Canônica**:
   - Linha 1: obrigatoriamente Header (`00`).
   - Linhas 2 a N-1: obrigatoriamente Detalhes (`01`).
   - Linha N: obrigatoriamente Trailler (`99`).
   - Arquivos vazios sem registros ou sem Header/Trailler são rejeitados.

### 3.2 Batimento Criptográfico e Numérico do Trailler
O Trailler funciona como um checksum posicional obrigatório. O parser executa as seguintes verificações:
- `total_registros_arquivo == count(todas as linhas)`
- `total_registros_detalhe == count(linhas tipo 01)`
- `total_quantidade_liquida == sum(detalhe.quantidade_liquida)`
- `total_valor_financeiro == sum(detalhe.valor_financeiro)`

Se qualquer uma das somas divergir, o processamento deve ser abortado com `TrailerValidationError`, impedindo que dados truncados ou adulterados cheguem ao livro de posições interno.

### 3.3 Regras por Segmento de Mercado
- **Mercado à Vista (`VIS`)**: `data_vencimento` preenchido com zeros (`00000000`) e `codigo_serie` preenchido com espaços.
- **Mercado de Opções (`OPC`)**: `data_vencimento` obrigatória válida, `codigo_serie` preenchido com o código da série negociada.
- **Mercado Futuro (`FUT`)**: `data_vencimento` obrigatória válida (geralmente primeiro dia útil do mês de vencimento), `codigo_serie` preenchido com o contrato futuro (ex: `WDOZ26`).
