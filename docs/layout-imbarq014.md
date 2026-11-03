# Especificação Técnica do Leiaute IMBARQ014 — Garantias e Chamadas de Margem B3

## 1. Visão Geral e Arquitetura do Arquivo

O arquivo **IMBARQ014** é o demonstrativo oficial gerado na grade de processamento da B3 para reportar a **alocação de garantias depositadas, a margem requerida calculada pelo motor de risco (CORE - Close-Out Risk Evaluation) e as situações de margem livre ou defasagem (chamada de margem)** para cada conta comitente cadastrada.

Historicamente originado da desagregação do arquivo consolidado IMBARQ001 (descontinuado em 30/06/2024), o IMBARQ014 compõe a frente **FL-005 (Consultar garantias e chamadas de margem)** da infraestrutura pós-negociação da B3.

### 1.1 Características Físicas
- **Formato**: Arquivo posicional de largura fixa (flat-file).
- **Tamanho do Registro**: Rigorosamente **200 caracteres** por linha (excluindo o terminador de linha).
- **Terminador de Linha**: Padrão Unix (`\n` - LF, 0x0A) ou Windows (`\r\n` - CRLF, 0x0D0A).
- **Codificação**: ASCII / ISO-8859-1 (sem caracteres especiais ou acentuação gráfica nos identificadores).
- **Hierarquia de Registros**:
  1. **Header (00)**: Exatamente 1 linha no início do arquivo.
  2. **Detalhes (01)**: Zero ou mais linhas consecutivas de colaterais e margens.
  3. **Trailler (99)**: Exatamente 1 linha no encerramento do arquivo.

### 1.2 Convenção de Tipos de Dados

| Sigla | Descrição | Regra de Formatação / Alinhamento |
|---|---|---|
| `X` | Alfanumérico | Texto alinhado à esquerda, preenchido com espaços à direita (` `). |
| `N` | Numérico Inteiro | Dígitos decimais `0-9`, alinhado à direita, preenchido com zeros à esquerda (`0`). |
| `V` | Numérico Decimal | Casas decimais implícitas (sem ponto ou vírgula). Ex.: `N(15)V2` = 17 dígitos com 2 decimais (`00000000015000000` = R$ 150.000,00). |
| `D` | Data | 8 dígitos no formato `YYYYMMDD` (ano, mês, dia). Ex.: `20261103`. |
| `H` | Hora | 6 dígitos no formato `HHMMSS` (hora, minuto, segundo). Ex.: `230000`. |

---

## 2. Especificação dos Registros

### 2.1 Registro Header (Tipo 00) — Controle de Abertura

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `00`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ014`. |
| `versao_layout` | 12 | 14 | 3 | `N` | S | Versão da especificação: `001`. |
| `data_geracao` | 15 | 22 | 8 | `D` | S | Data de geração física do arquivo (`YYYYMMDD`). |
| `hora_geracao` | 23 | 28 | 6 | `H` | S | Hora de geração física do arquivo (`HHMMSS`). |
| `data_movimento` | 29 | 36 | 8 | `D` | S | Data de movimento/referência do pregão (`YYYYMMDD`). |
| `codigo_participante` | 37 | 44 | 8 | `N` | S | Código do participante na B3 (com zeros à esquerda). Ex.: `00000105`. |
| `nome_participante` | 45 | 74 | 30 | `X` | S | Razão social abreviada do participante. |
| `sequencial_remessa` | 75 | 80 | 6 | `N` | S | Sequencial diário do arquivo para a instituição. Ex.: `000001`. |
| `reservado` | 81 | 200 | 120 | `X` | S | Filler preenchido com espaços em branco. |

---

### 2.2 Registro de Detalhe (Tipo 01) — Garantias e Chamadas de Margem

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `01`. |
| `codigo_participante` | 3 | 10 | 8 | `N` | S | Código da corretora / participante de negociação (PN). |
| `codigo_conta_custodia` | 11 | 22 | 12 | `N` | S | Código da conta do comitente/investidor na B3. |
| `tipo_colateral` | 23 | 25 | 3 | `X` | S | Categoria do colateral: `MOE` (Moeda BRL), `TPU` (Título Público Federal), `ACO` (Ações Elegíveis). |
| `identificador_ativo` | 26 | 37 | 12 | `X` | S | Identificador do ativo (Ticker ou código). Ex.: `BRL         `, `LFT2027     `, `PETR4       `. |
| `codigo_isin` | 38 | 49 | 12 | `X` | S | Código ISIN internacional ou código do instrumento. Ex.: `BRBRLCTF0001`, `BRSTNCLF1001`, `BRPETRACNPR6`. |
| `finalidade_garantia` | 50 | 52 | 3 | `X` | S | Finalidade da garantia: `AMP` (Ampla/Geral), `ESP` (Específica para contrato/opção). |
| `quantidade_depositada` | 53 | 67 | 15 | `N` | S | Quantidade de ativos depositados (unidades de moeda, títulos ou ações). |
| `preco_avaliacao_unitario` | 68 | 82 | 15 | `N(13)V2` | S | Preço unitário de avaliação com 2 decimais implícitas (pós-haircut/deságio da B3). |
| `valor_avaliacao` | 83 | 99 | 17 | `N(15)V2` | S | Valor financeiro total avaliado do colateral com 2 decimais implícitas. |
| `margem_requerida` | 100 | 116 | 17 | `N(15)V2` | S | Margem de garantia exigida pelo modelo CORE da B3 com 2 decimais implícitas. |
| `situacao_margem` | 117 | 117 | 1 | `X` | S | Situação da margem: `L` (Margem Livre / Excedente) ou `D` (Defasagem / Chamada de Margem). |
| `valor_margem_livre_defasagem`| 118 | 134 | 17 | `N(15)V2` | S | Valor financeiro da margem livre ou defasagem com 2 decimais implícitas. |
| `participante_liquidacao` | 135 | 142 | 8 | `N` | S | Código do participante de liquidação (PL) responsável. |
| `reservado` | 143 | 200 | 58 | `X` | S | Filler preenchido com espaços em branco. |

---

### 2.3 Registro Trailler (Tipo 99) — Totalização e Fechamento

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `99`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ014`. |
| `total_registros_arquivo` | 12 | 19 | 8 | `N` | S | Contagem total de linhas do arquivo (Header + Detalhes + Trailler). |
| `total_registros_detalhe` | 20 | 27 | 8 | `N` | S | Quantidade exata de registros do tipo `01`. |
| `total_quantidade_depositada`| 28 | 45 | 18 | `N` | S | Somatório da `quantidade_depositada` de todos os registros `01`. |
| `total_valor_avaliacao` | 46 | 63 | 18 | `N(16)V2` | S | Somatório de `valor_avaliacao` de todos os registros `01` (com 2 decimais implícitas). |
| `total_margem_requerida` | 64 | 81 | 18 | `N(16)V2` | S | Somatório de `margem_requerida` de todos os registros `01` (com 2 decimais implícitas). |
| `total_margem_livre_defasagem`| 82 | 99 | 18 | `N(16)V2` | S | Somatório de `valor_margem_livre_defasagem` de todos os registros `01` (com 2 decimais implícitas). |
| `reservado` | 100 | 200 | 101 | `X` | S | Filler preenchido com espaços em branco. |

---

## 3. Regras de Negócio e Validações de Integridade

### 3.1 Integridade Estrutural
1. **Comprimento Invariante**: Cada registro deve possuir exatamente 200 colunas. Qualquer linha com tamanho divergente resulta em rejeição sumária (`InvalidRecordLengthError`).
2. **Sequência Canônica**:
   - Linha 1: obrigatoriamente Header (`00`).
   - Linhas 2 a N-1: obrigatoriamente Detalhes (`01`).
   - Linha N: obrigatoriamente Trailler (`99`).
   - Arquivos vazios ou sem Header/Trailler são rejeitados com `InvalidRecordStructureError`.

### 3.2 Batimento Criptográfico e Numérico do Trailler
O Trailler opera como um checksum de integridade posicional estrito:
- `total_registros_arquivo == count(todas as linhas)`
- `total_registros_detalhe == count(linhas tipo 01)`
- `total_quantidade_depositada == sum(detalhe.quantidade_depositada)`
- `total_valor_avaliacao == sum(detalhe.valor_avaliacao)`
- `total_margem_requerida == sum(detalhe.margem_requerida)`
- `total_margem_livre_defasagem == sum(detalhe.valor_margem_livre_defasagem)`

Se qualquer uma das somas divergir, o processamento deve ser abortado com `TrailerValidationError`, impedindo a ingestão de relatórios de risco corrompidos ou incompletos.

### 3.3 Regras por Categoria de Colateral
- **Moeda (`MOE`)**:
  - Depósito em espécie em Reais (BRL).
  - Preço de avaliação unitário base fixado em 1,00 (`000000000000100`).
  - Sem incidência de haircut de volatilidade cambial para contas em moeda nacional.
- **Títulos Públicos Federais (`TPU`)**:
  - LFT, LTN, NTN-B, NTN-F custodiados no Selic e aceitos na B3.
  - Preço de avaliação reflete o PU Selic/Anbima com aplicação de deságio de risco B3.
- **Ações (`ACO`)**:
  - Ações e units listadas na B3 elegíveis à grade de garantias da câmara.
  - Preço unitário reflete a cotação de fechamento reduzida pelo fator de haircut estipulado pelo modelo CORE para o ativo em questão.

### 3.4 Regras de Margem e Chamada de Margem
- **Margem Livre (`L`)**:
  - Ocorre quando `valor_avaliacao >= margem_requerida`.
  - O saldo livre disponível para saque ou alavancagem é `valor_avaliacao - margem_requerida`.
- **Defasagem / Chamada de Margem (`D`)**:
  - Ocorre quando `valor_avaliacao < margem_requerida`.
  - O participante/comitente está desenquadrado no sistema de risco B3.
  - O valor da defasagem a ser coberto até o horário limite da câmara (ex.: D+1 10:00) é `margem_requerida - valor_avaliacao`.
