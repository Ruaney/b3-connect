# Especificação Técnica do Leiaute IMBARQ011 — Posições de Empréstimo de Ativos (BTC)

## 1. Visão Geral e Arquitetura do Arquivo

O arquivo **IMBARQ011** é o demonstrativo oficial gerado na grade de processamento da B3 para reportar as **posições em aberto de contratos de empréstimo de ativos** (aluguel de ações, cotas de ETF, BDRs e títulos elegíveis ao Banco de Títulos CBLC / BTC).

Com a descontinuação do arquivo consolidado IMBARQ001 em 30/06/2024, a frente de empréstimo de ativos foi segregada no relatório especializado IMBARQ011 (correspondente ao fluxo FL-009 do catálogo de integração B3).

### 1.1 Características Físicas
- **Formato**: Arquivo posicional de largura fixa (flat-file).
- **Tamanho do Registro**: Rigorosamente **200 caracteres** por linha (excluindo terminador de linha).
- **Terminador de Linha**: Padrão Unix (`\n` - LF, 0x0A) ou Windows (`\r\n` - CRLF, 0x0D0A).
- **Codificação**: ASCII / ISO-8859-1 (sem caracteres especiais ou acentuação gráfica nos identificadores).
- **Hierarquia de Registros**:
  1. **Header (00)**: Exatamente 1 linha no início do arquivo.
  2. **Contratos de Empréstimo (01)**: Zero ou mais linhas consecutivas com contratos ativos de aluguel.
  3. **Trailler (99)**: Exatamente 1 linha no encerramento do arquivo.

### 1.2 Convenção de Tipos de Dados

| Sigla | Descrição | Regra de Formatação / Alinhamento |
|---|---|---|
| `X` | Alfanumérico | Texto alinhado à esquerda, preenchido com espaços à direita (` `). |
| `N` | Numérico Inteiro | Dígitos decimais `0-9`, alinhado à direita, preenchido com zeros à esquerda (`0`). |
| `V` | Numérico Decimal | Casas decimais implícitas (sem ponto ou vírgula). Ex.: `N(13)V4` = 17 dígitos totais com 4 decimais (`00000000000025000` = `2,5000%`). |
| `D` | Data | 8 dígitos no formato `YYYYMMDD` (ano, mês, dia). Ex.: `20261103`. |
| `H` | Hora | 6 dígitos no formato `HHMMSS` (hora, minuto, segundo). Ex.: `230000`. |

---

## 2. Especificação dos Registros

### 2.1 Registro Header (Tipo 00) — Controle de Abertura

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `00`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ011`. |
| `versao_layout` | 12 | 14 | 3 | `N` | S | Versão da especificação: `001`. |
| `data_geracao` | 15 | 22 | 8 | `D` | S | Data de geração física do arquivo (`YYYYMMDD`). |
| `hora_geracao` | 23 | 28 | 6 | `H` | S | Hora de geração física do arquivo (`HHMMSS`). |
| `data_movimento` | 29 | 36 | 8 | `D` | S | Data de movimento/referência do pregão (`YYYYMMDD`). |
| `codigo_participante` | 37 | 44 | 8 | `N` | S | Código do participante na B3 (com zeros à esquerda). Ex.: `00000105`. |
| `nome_participante` | 45 | 74 | 30 | `X` | S | Razão social abreviada do participante. |
| `sequencial_remessa` | 75 | 80 | 6 | `N` | S | Sequencial diário do arquivo para a instituição. Ex.: `000001`. |
| `reservado` | 81 | 200 | 120 | `X` | S | Filler preenchido com espaços em branco. |

---

### 2.2 Registro de Detalhe (Tipo 01) — Contrato de Empréstimo de Ativos (BTC)

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `01`. |
| `numero_contrato` | 3 | 17 | 15 | `N` | S | Número único identificador do contrato no BTC. Ex.: `000000020260001`. |
| `codigo_participante_doador` | 18 | 25 | 8 | `N` | S | Código da corretora / PN do doador do ativo. Ex.: `00000105`. |
| `conta_doador` | 26 | 37 | 12 | `N` | S | Código da conta de custódia / investidor do doador. |
| `codigo_participante_tomador` | 38 | 45 | 8 | `N` | S | Código da corretora / PN do tomador do ativo. Ex.: `00000210`. |
| `conta_tomador` | 46 | 57 | 12 | `N` | S | Código da conta de custódia / investidor do tomador. |
| `codigo_negociacao` | 58 | 69 | 12 | `X` | S | Ticker do ativo objeto alugado. Ex.: `VALE3       `. |
| `codigo_isin` | 70 | 81 | 12 | `X` | S | Código ISIN internacional do ativo. Ex.: `BRVALEACNOR0`. |
| `quantidade_alugada` | 82 | 96 | 15 | `N` | S | Quantidade total de ativos do contrato de aluguel. |
| `taxa_aluguel` | 97 | 113 | 17 | `N(13)V4` | S | Taxa anual do contrato com 4 casas decimais implícitas (% a.a.). Ex.: `00000000000025000` = 2,5000%. |
| `data_abertura` | 114 | 121 | 8 | `D` | S | Data de registro e início da vigência do empréstimo (`YYYYMMDD`). |
| `data_vencimento` | 122 | 129 | 8 | `D` | S | Data acordada para encerramento/vencimento (`YYYYMMDD`). |
| `tipo_reversibilidade` | 130 | 130 | 1 | `X` | S | Indicador de reversibilidade: `D` (Doador), `T` (Tomador), `A` (Ambos), `N` (Não reversível / Fixo). |
| `preco_base` | 131 | 145 | 15 | `N(13)V2` | S | Preço de cotação do ativo base no fechamento com 2 decimais implícitas. Ex.: `000000000006250` = R$ 62,50. |
| `valor_contrato` | 146 | 162 | 17 | `N(15)V2` | S | Volume nocional do contrato (`quantidade_alugada * preco_base`) com 2 decimais implícitas. |
| `reservado` | 163 | 200 | 38 | `X` | S | Filler preenchido com espaços em branco. |

---

### 2.3 Registro Trailler (Tipo 99) — Totalização e Fechamento

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `99`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ011`. |
| `total_registros_arquivo` | 12 | 19 | 8 | `N` | S | Contagem total de linhas do arquivo (Header + Detalhes + Trailler). |
| `total_registros_detalhe` | 20 | 27 | 8 | `N` | S | Quantidade exata de registros do tipo `01`. |
| `total_quantidade_alugada`| 28 | 45 | 18 | `N` | S | Somatório aritmético simples do campo `quantidade_alugada` de todos os registros `01`. |
| `total_valor_contratos` | 46 | 63 | 18 | `N(16)V2` | S | Somatório aritmético do campo `valor_contrato` de todos os registros `01` (com 2 decimais implícitas). |
| `reservado` | 64 | 200 | 137 | `X` | S | Filler preenchido com espaços em branco. |

---

## 3. Regras de Negócio e Validações de Integridade

### 3.1 Integridade Estrutural
1. **Comprimento Invariante**: Cada registro deve possuir exatamente 200 colunas. Qualquer linha com tamanho diferente resulta em rejeição sumária (`InvalidRecordLengthError`).
2. **Sequência Canônica**:
   - Linha 1: obrigatoriamente Header (`00`).
   - Linhas 2 a N-1: obrigatoriamente Detalhes de Contrato (`01`).
   - Linha N: obrigatoriamente Trailler (`99`).
   - Arquivos vazios ou sem Header/Trailler são rejeitados com `InvalidRecordStructureError`.

### 3.2 Batimento Criptográfico e Numérico do Trailler
O Trailler atua como checksum posicional mandatório. O processador valida:
- `total_registros_arquivo == count(todas as linhas)`
- `total_registros_detalhe == count(linhas tipo 01)`
- `total_quantidade_alugada == sum(detalhe.quantidade_alugada)`
- `total_valor_contratos == sum(detalhe.valor_contrato)`

Havendo qualquer divergência aritmética, o arquivo é rejeitado sumariamente via `TrailerValidationError`, impedindo contaminação do livro de custódia e empréstimos da instituição.

### 3.3 Regras Específicas de Empréstimo de Ativos
- **Taxa de Aluguel (`taxa_aluguel`)**: O campo adota precisão de 4 casas decimais (`N(13)V4`) para acomodar frações finas de remuneração (ex.: 0,0500% a.a. até 25,1250% a.a.).
- **Vigência**: `data_abertura` deve ser igual ou anterior à `data_vencimento`.
- **Papéis**: Identificação expressa de doador (`codigo_participante_doador`, `conta_doador`) e tomador (`codigo_participante_tomador`, `conta_tomador`), permitindo conciliação bilateral entre as pontas.
