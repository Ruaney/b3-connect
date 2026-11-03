# Especificação Técnica do Leiaute IMBARQ004 — Lançamentos Financeiros de Liquidação B3

## 1. Visão Geral e Arquitetura do Arquivo

O arquivo **IMBARQ004** (FL-004) é o demonstrativo oficial disponibilizado na grade de processamento da B3 para reportar os **lançamentos financeiros de liquidação** decorrentes das operações realizadas nos mercados administrados pela B3 (ações, derivativos, opções, taxas operacionais, emolumentos e ajustes).

Com a descontinuação e segregação do legado **IMBARQ001** (Arquivo de Posições e Movimentações da Câmara), a B3 modularizou a entrega de dados. O **IMBARQ004** assume exclusivamente o extrato e a conciliação financeira das contas de liquidação e custódia.

### 1.1 Características Físicas
- **Formato**: Arquivo posicional de largura fixa (*flat-file*).
- **Tamanho do Registro**: Rigorosamente **200 caracteres** por linha (excluindo terminadores de linha `\n` ou `\r\n`).
- **Terminador de Linha**: Padrão Unix (`\n` - LF, 0x0A) ou Windows (`\r\n` - CRLF, 0x0D0A).
- **Codificação**: ASCII / ISO-8859-1 (sem acentuação gráfica ou caracteres especiais de controle nos identificadores).
- **Hierarquia de Registros**:
  1. **Header (00)**: Exatamente 1 linha no início do arquivo.
  2. **Detalhes (01)**: Zero ou mais linhas consecutivas de lançamentos financeiros de liquidação.
  3. **Trailler (99)**: Exatamente 1 linha no encerramento do arquivo com totalizadores e checksum contábil.

### 1.2 Convenção de Tipos de Dados

| Sigla | Descrição | Regra de Formatação / Alinhamento |
|---|---|---|
| `X` | Alfanumérico | Texto alinhado à esquerda, preenchido com espaços à direita (` `). |
| `N` | Numérico Inteiro | Dígitos decimais `0-9`, alinhado à direita, preenchido com zeros à esquerda (`0`). |
| `V` | Numérico Decimal | Casas decimais implícitas (sem ponto ou vírgula). Ex.: `N(15)V2` = 17 dígitos totais com 2 casas decimais (`00000000002125000` = `R$ 21.250,00`). |
| `D` | Data | 8 dígitos no formato `YYYYMMDD` (ano, mês, dia). Ex.: `20261103`. |
| `H` | Hora | 6 dígitos no formato `HHMMSS` (hora, minuto, segundo). Ex.: `230000`. |

---

## 2. Especificação dos Registros

### 2.1 Registro Header (Tipo 00) — Controle de Abertura

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `00`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ004`. |
| `versao_layout` | 12 | 14 | 3 | `N` | S | Versão da especificação: `001`. |
| `data_geracao` | 15 | 22 | 8 | `D` | S | Data de geração física do arquivo (`YYYYMMDD`). |
| `hora_geracao` | 23 | 28 | 6 | `H` | S | Hora de geração física do arquivo (`HHMMSS`). |
| `data_movimento` | 29 | 36 | 8 | `D` | S | Data de referência do pregão/movimento (`YYYYMMDD`). |
| `codigo_participante` | 37 | 44 | 8 | `N` | S | Código do participante na B3 (com zeros à esquerda). Ex.: `00000105`. |
| `nome_participante` | 45 | 74 | 30 | `X` | S | Razão social abreviada do participante / corretora. |
| `sequencial_remessa` | 75 | 80 | 6 | `N` | S | Sequencial diário do arquivo para a instituição. Ex.: `000001`. |
| `reservado` | 81 | 200 | 120 | `X` | S | Filler preenchido com espaços em branco. |

---

### 2.2 Registro de Detalhe (Tipo 01) — Lançamento Financeiro de Liquidação

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `01`. |
| `codigo_participante` | 3 | 10 | 8 | `N` | S | Código do participante de negociação/liquidação na B3. |
| `codigo_conta_custodia` | 11 | 22 | 12 | `N` | S | Código da conta de custódia / investidor na B3. |
| `numero_lancamento` | 23 | 32 | 10 | `N` | S | Número sequencial único do lançamento no pregão/dia. |
| `data_liquidacao` | 33 | 40 | 8 | `D` | S | Data da efetiva liquidação financeira (`YYYYMMDD`). |
| `codigo_historico` | 41 | 44 | 4 | `N` | S | Código do histórico de liquidação na B3 (ex: `0101`, `0201`). |
| `descricao_historico` | 45 | 74 | 30 | `X` | S | Descrição textual padronizada do lançamento financeiro. |
| `natureza_lancamento` | 75 | 75 | 1 | `X` | S | Sentido financeiro do lançamento: `C` (Crédito) ou `D` (Débito). |
| `valor_lancamento` | 76 | 92 | 17 | `N(15)V2` | S | Valor monetário nominal do lançamento com 2 casas decimais implícitas. Ex.: `00000000002125000` = R$ 21.250,00. |
| `codigo_negociacao` | 93 | 104 | 12 | `X` | N | Ticker do ativo originador (ex: `PETRL350    `) ou espaços em branco para tarifas gerais. |
| `codigo_isin` | 105 | 116 | 12 | `X` | N | Código ISIN internacional ou espaços em branco. |
| `tipo_mercado` | 117 | 119 | 3 | `X` | N | Segmento de mercado: `VIS` (À Vista), `OPC` (Opções), `FUT` (Futuros), `TER` (Termo) ou espaços. |
| `participante_contraparte` | 120 | 127 | 8 | `N` | S | Código da contraparte ou câmara de compensação (`00000001`). |
| `numero_documento` | 128 | 143 | 16 | `X` | N | Referência de nota de corretagem, documento interno ou registro B3. |
| `canal_liquidacao` | 144 | 147 | 4 | `X` | S | Grade/Canal de liquidação: `CAM ` (Câmara B3), `STR ` (SPB/BACEN), `SEL ` (Selic). |
| `status_liquidacao` | 148 | 148 | 1 | `X` | S | Status do evento financeiro: `L` (Liquidado), `P` (Pendente), `C` (Cancelado). |
| `reservado` | 149 | 200 | 52 | `X` | S | Filler preenchido com espaços em branco. |

---

### 2.3 Registro Trailler (Tipo 99) — Totalização e Fechamento Contábil

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `99`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ004`. |
| `total_registros_arquivo` | 12 | 19 | 8 | `N` | S | Contagem total de linhas do arquivo (Header + Detalhes + Trailler). |
| `total_registros_detalhe`| 20 | 27 | 8 | `N` | S | Quantidade exata de registros do tipo `01`. |
| `total_valor_debito` | 28 | 45 | 18 | `N(16)V2` | S | Somatório de `valor_lancamento` dos registros com natureza `D` (2 decimais). |
| `total_valor_credito` | 46 | 63 | 18 | `N(16)V2` | S | Somatório de `valor_lancamento` dos registros com natureza `C` (2 decimais). |
| `sinal_saldo_liquido` | 64 | 64 | 1 | `X` | S | Indicador do saldo líquido: `C` se Crédito >= Débito; `D` se Débito > Crédito. |
| `total_saldo_liquido` | 65 | 82 | 18 | `N(16)V2` | S | Valor líquido absoluto `\|Crédito - Débito\|` com 2 casas decimais. |
| `reservado` | 83 | 200 | 118 | `X` | S | Filler preenchido com espaços em branco. |

---

## 3. Regras de Negócio e Validações de Integridade

### 3.1 Integridade Estrutural
1. **Comprimento Invariante**: Cada registro deve possuir exatamente **200 caracteres**. Qualquer desvio em linhas individuais gera rejeição imediata com `InvalidRecordLengthError`.
2. **Sequência Canônica**:
   - Linha 1: obrigatoriamente Header (`00`).
   - Linhas 2 a N-1: registros de Detalhe (`01`).
   - Linha N: obrigatoriamente Trailler (`99`).
   - Arquivos vazios ou desprovidos de Header/Trailler são rejeitados com `InvalidRecordStructureError`.

### 3.2 Batimento Contábil e Aritmético do Trailler
O Trailler implementa um controle de integridade transacional estrito. O parser confere:
- `total_registros_arquivo == count(todas as linhas)`
- `total_registros_detalhe == count(linhas tipo 01)`
- `total_valor_debito == sum(d.valor_lancamento for d in detalhes if d.natureza_lancamento == 'D')`
- `total_valor_credito == sum(d.valor_lancamento for d in detalhes if d.natureza_lancamento == 'C')`
- `sinal_saldo_liquido == ('C' if total_credito >= total_debito else 'D')`
- `total_saldo_liquido == abs(total_credito - total_debito)`

Havendo qualquer divergência de centavos ou contagens, o processamento deve ser abortado com `TrailerValidationError`, impedindo que relatórios corrompidos ou parciais sejam lançados no livro contábil da instituição.

### 3.3 Principais Históricos de Liquidação da B3
- **`0101` — Liquidação de Prêmio de Opções**: Débito para o comprador da opção (titular) e crédito para o lançador da opção (vendedor), liquidado em D+1.
- **`0201` — Taxa de Registro e Emolumentos**: Lançamentos a débito decorrentes dos serviços de registro, negociação e liquidação cobrados pela B3.
- **`0301` — Ajuste Diário de Futuros**: Crédito ou débito apurado no fechamento dos contratos futuros (ex: DI1, WDO, WIN), liquidado em D+1.
- **`0401` — Liquidação Financeira Mercado à Vista**: Crédito de vendas e débito de compras de ações e ETFs, liquidado em D+2.
