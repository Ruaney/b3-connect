# Especificação Técnica do Leiaute IMBARQ018 — Eventos Corporativos Data Ex B3

## 1. Visão Geral e Arquitetura do Arquivo

O arquivo **IMBARQ018** é o demonstrativo oficial gerado na grade de processamento e mensageria pós-negociação da B3 para reportar **eventos corporativos e societários em data ex (proventos em dinheiro e eventos em ações)** aplicáveis aos ativos mantidos em custódia pelos participantes e seus comitentes.

Historicamente originado da desagregação do arquivo consolidado IMBARQ001 (descontinuado em 30/06/2024), o IMBARQ018 compõe a frente **FL-006 (Consultar eventos corporativos data ex)** da infraestrutura pós-negociação da B3.

### 1.1 Características Físicas
- **Formato**: Arquivo posicional de largura fixa (flat-file).
- **Tamanho do Registro**: Rigorosamente **200 caracteres** por linha (excluindo o terminador de linha).
- **Terminador de Linha**: Padrão Unix (`\n` - LF, 0x0A) ou Windows (`\r\n` - CRLF, 0x0D0A).
- **Codificação**: ASCII / ISO-8859-1 (sem caracteres especiais ou acentuação gráfica nos identificadores).
- **Hierarquia de Registros**:
  1. **Header (00)**: Exatamente 1 linha no início do arquivo.
  2. **Detalhes (01)**: Zero ou mais linhas consecutivas de eventos corporativos.
  3. **Trailler (99)**: Exatamente 1 linha no encerramento do arquivo.

### 1.2 Convenção de Tipos de Dados

| Sigla | Descrição | Regra de Formatação / Alinhamento |
|---|---|---|
| `X` | Alfanumérico | Texto alinhado à esquerda, preenchido com espaços à direita (` `). |
| `N` | Numérico Inteiro | Dígitos decimais `0-9`, alinhado à direita, preenchido com zeros à esquerda (`0`). |
| `V` | Numérico Decimal | Casas decimais implícitas (sem ponto ou vírgula). Ex.: `N(13)V6` = 19 dígitos com 6 decimais (`0000000000001250000` = R$ 1,250000). |
| `D` | Data | 8 dígitos no formato `YYYYMMDD` (ano, mês, dia). Ex.: `20261103`. |
| `H` | Hora | 6 dígitos no formato `HHMMSS` (hora, minuto, segundo). Ex.: `230000`. |

---

## 2. Especificação dos Registros

### 2.1 Registro Header (Tipo 00) — Controle de Abertura

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `00`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ018`. |
| `versao_layout` | 12 | 14 | 3 | `N` | S | Versão da especificação: `001`. |
| `data_geracao` | 15 | 22 | 8 | `D` | S | Data de geração física do arquivo (`YYYYMMDD`). |
| `hora_geracao` | 23 | 28 | 6 | `H` | S | Hora de geração física do arquivo (`HHMMSS`). |
| `data_movimento` | 29 | 36 | 8 | `D` | S | Data de movimento/referência do pregão (`YYYYMMDD`). |
| `codigo_participante` | 37 | 44 | 8 | `N` | S | Código do participante na B3 (com zeros à esquerda). Ex.: `00000105`. |
| `nome_participante` | 45 | 74 | 30 | `X` | S | Razão social abreviada do participante. |
| `sequencial_remessa` | 75 | 80 | 6 | `N` | S | Sequencial diário do arquivo para a instituição. Ex.: `000001`. |
| `reservado` | 81 | 200 | 120 | `X` | S | Filler preenchido com espaços em branco. |

---

### 2.2 Registro de Detalhe (Tipo 01) — Evento Corporativo em Data Ex

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `01`. |
| `codigo_participante` | 3 | 10 | 8 | `N` | S | Código da corretora / participante de custódia na B3. Ex.: `00000105`. |
| `codigo_conta_custodia` | 11 | 22 | 12 | `N` | S | Código da conta de custódia do comitente/investidor na B3. Ex.: `000000123456`. |
| `codigo_negociacao` | 23 | 34 | 12 | `X` | S | Ticker do ativo objeto na B3. Ex.: `PETR4       `. |
| `codigo_isin` | 35 | 46 | 12 | `X` | S | Código ISIN internacional do ativo. Ex.: `BRPETRACNPR6`. |
| `codigo_evento` | 47 | 52 | 6 | `X` | S | Tipo de evento corporativo: `DIVID `, `JCP   `, `BONIF `, `SUBSC `, `DESD  `, `AGRUP `. |
| `data_com` | 53 | 60 | 8 | `D` | S | Última data com direito ao evento corporativo (`YYYYMMDD`). |
| `data_ex` | 61 | 68 | 8 | `D` | S | Início da negociação sem direito ao evento (`YYYYMMDD`). |
| `data_pagamento` | 69 | 76 | 8 | `D` | N | Data de efetivo pagamento ou homologação (`YYYYMMDD`) ou `00000000` se a definir. |
| `valor_provento` | 77 | 95 | 19 | `N(13)V6` | S | Valor bruto do provento por ação com 6 decimais implícitas. Ex.: `0000000000001250000` = R$ 1,250000. |
| `percentual_evento` | 96 | 112 | 17 | `N(13)V4` | S | Percentual de bonificação/subscrição com 4 decimais implícitas. Ex.: `00000000000050000` = 5,0000%. |
| `indicador_tributacao` | 113 | 113 | 1 | `X` | S | Indicador de tributação na fonte: `S` (Tributado, ex: JCP) ou `N` (Isento, ex: Dividendos PF). |
| `status_evento` | 114 | 118 | 5 | `X` | S | Situação do evento corporativo: `APROV` (Aprovado), `CANC ` (Cancelado), `PEND ` (Pendente). |
| `reservado` | 119 | 200 | 82 | `X` | S | Filler preenchido com espaços em branco. |

---

### 2.3 Registro Trailler (Tipo 99) — Totalização e Fechamento

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `99`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ018`. |
| `total_registros_arquivo` | 12 | 19 | 8 | `N` | S | Contagem total de linhas do arquivo (Header + Detalhes + Trailler). |
| `total_registros_detalhe` | 20 | 27 | 8 | `N` | S | Quantidade exata de registros do tipo `01`. |
| `total_valor_proventos` | 28 | 46 | 19 | `N(13)V6` | S | Somatório de `valor_provento` de todos os registros `01` (com 6 decimais implícitas). |
| `reservado` | 47 | 200 | 154 | `X` | S | Filler preenchido com espaços em branco. |

---

## 3. Regras de Negócio e Validações de Integridade

### 3.1 Integridade Estrutural
1. **Comprimento Invariante**: Cada registro deve possuir rigorosamente 200 colunas ASCII. Qualquer linha divergente dispara `InvalidRecordLengthError`.
2. **Sequência Canônica**:
   - Linha 1: obrigatoriamente Header (`00`).
   - Linhas 2 a N-1: obrigatoriamente Detalhes (`01`).
   - Linha N: obrigatoriamente Trailler (`99`).
   - Arquivos vazios ou sem Header/Trailler disparam `InvalidRecordStructureError`.

### 3.2 Batimento Numérico e Checksum do Trailler
O Trailler opera como checksum de integridade posicional estrito:
- `total_registros_arquivo == count(todas as linhas)`
- `total_registros_detalhe == count(linhas tipo 01)`
- `total_valor_proventos == sum(detalhe.valor_provento)`

Qualquer divergência nas contagens ou no somatório financeiro resulta em interrupção imediata com `TrailerValidationError`.

### 3.3 Tipos de Eventos Corporativos Suportados

| Código | Descrição | Escala Financeira | Percentual | Tributação |
|---|---|---|---|---|
| `DIVID` | Dividendos | `valor_provento > 0` | `0` | Geralmente `N` (isento para PF) |
| `JCP` | Juros sobre Capital Próprio | `valor_provento > 0` | `0` | `S` (sujeito a 15% de IRRF) |
| `BONIF` | Bonificação em Ações | `0` | `percentual_evento > 0` | Geralmente `N` |
| `SUBSC` | Subscrição de Ações | `valor_provento` (preço de emissão) | `percentual_evento > 0` | Geralmente `N` |
| `DESD` | Desdobramento (Split) | `0` | Proporção / percentual | `N` |
| `AGRUP` | Grupamento (Reverse Split) | `0` | Proporção / percentual | `N` |

### 3.4 Status de Eventos Corporativos
- `APROV` (Aprovado): Evento societário homologado e deliberado em assembleia/conselho com datas e valores fixados.
- `CANC` (Cancelado): Evento cancelado ou revogado pelo emissor ou órgão regulador.
- `PEND` (Pendente): Evento anunciado, sujeito a condições precedentes ou homologação final.
