# Especificação Técnica do Leiaute IMBARQ013 — Movimentações da Depositária de Listados B3

## 1. Visão Geral e Arquitetura do Arquivo

O arquivo **IMBARQ013** é o demonstrativo oficial disponibilizado na grade de processamento da B3 para reportar as **movimentações escriturais e extrato de eventos de custódia na Depositária Central de Ativos Listados** (ações, cotas de fundos/ETFs, BDRs, units, direitos e recibos).

Com a descontinuação do antigo arquivo consolidado **IMBARQ001** (Posições e Movimentações da Câmara) em 30/06/2024, a B3 segregou a entrega operacional em componentes especializados:
- **IMBARQ004 (FL-004)**: Extrato financeiro e conciliação de liquidação financeira.
- **IMBARQ006 (FL-001)**: Posições em aberto na Câmara de Compensação (Clearing).
- **IMBARQ011 (FL-009)**: Posições em aberto de empréstimo de ativos (BTC).
- **IMBARQ012 (FL-003)**: Posições escriturais e eventos societários pendentes da Depositária.
- **IMBARQ013 (FL-010)**: Movimentações da Depositária de Listados (extrato analítico de débitos e créditos na custódia escritural).
- **IMBARQ014 (FL-005)**: Posição de garantias e chamadas de margem.

O IMBARQ013 constitui a fonte primária de verdade para conciliação física de custódia, permitindo auditar cada entrada e saída de ativos da conta escritural do investidor, seja decorrente de liquidação de pregão, transferências de custódia, bloqueios/desbloqueios judiciais ou depósitos/retiradas de colaterais.

### 1.1 Características Físicas

- **Formato**: Arquivo posicional de largura fixa (*flat-file*).
- **Tamanho do Registro**: Rigorosamente **200 caracteres** por linha (excluindo terminadores de linha `\n` ou `\r\n`).
- **Terminador de Linha**: Padrão Unix (`\n` - LF, 0x0A) ou Windows (`\r\n` - CRLF, 0x0D0A).
- **Codificação**: ASCII / ISO-8859-1 (sem acentuação gráfica nos identificadores ou campos estruturais).
- **Hierarquia de Registros**:
  1. **Header (00)**: Exatamente 1 linha no início do arquivo.
  2. **Movimentações / Detalhes (01)**: Zero ou mais linhas consecutivas de lançamentos analíticos de custódia.
  3. **Trailler (99)**: Exatamente 1 linha no encerramento do arquivo com totalizadores e controle de integridade contábil/aritmética.

### 1.2 Convenção de Tipos de Dados

| Sigla | Descrição | Regra de Formatação / Alinhamento |
|---|---|---|
| `X` | Alfanumérico | Texto alinhado à esquerda, completado com espaços à direita (` `). |
| `N` | Numérico Inteiro | Dígitos decimais `0-9`, alinhado à direita, completado com zeros à esquerda (`0`). |
| `D` | Data | 8 dígitos no formato `YYYYMMDD` (ano, mês, dia). Ex.: `20261103`. |
| `H` | Hora | 6 dígitos no formato `HHMMSS` (hora, minuto, segundo). Ex.: `230000`. |

---

## 2. Especificação dos Registros

### 2.1 Registro Header (Tipo 00) — Controle de Abertura

O Header identifica o remetente, a instituição participante destinatária, o lote do arquivo e a data de referência da custódia.

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `00`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ013`. |
| `versao_layout` | 12 | 14 | 3 | `N` | S | Versão da especificação: `001`. |
| `data_geracao` | 15 | 22 | 8 | `D` | S | Data de geração física do arquivo (`YYYYMMDD`). |
| `hora_geracao` | 23 | 28 | 6 | `H` | S | Hora de geração física do arquivo (`HHMMSS`). |
| `data_movimento` | 29 | 36 | 8 | `D` | S | Data de movimento/referência da custódia (`YYYYMMDD`). |
| `codigo_participante` | 37 | 44 | 8 | `N` | S | Código do participante / custodiante na B3 (zeros à esquerda). Ex.: `00000105`. |
| `nome_participante` | 45 | 74 | 30 | `X` | S | Razão social abreviada do participante. |
| `sequencial_remessa` | 75 | 80 | 6 | `N` | S | Sequencial diário do arquivo para a instituição. Ex.: `000001`. |
| `reservado` | 81 | 200 | 120 | `X` | S | Filler preenchido com espaços em branco. |

---

### 2.2 Registro de Detalhe (Tipo 01) — Movimentação / Extrato de Custódia

Cada registro `01` reporta uma movimentação física individual que alterou ou provisionou o saldo escritural de um ativo listado.

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `01`. |
| `numero_controle` | 3 | 17 | 15 | `N` | S | Número único sequencial de controle da movimentação na Depositária. Ex.: `000000020260001`. |
| `conta_custodia_origem` | 18 | 29 | 12 | `N` | S | Conta de custódia de origem da movimentação (zeros à esquerda se liquidado pela câmara). Ex.: `000000123456`. |
| `conta_custodia_destino` | 30 | 41 | 12 | `N` | S | Conta de custódia de destino da movimentação (zeros à esquerda em bloqueios judiciais ou saídas). Ex.: `000000987654`. |
| `codigo_negociacao` | 42 | 53 | 12 | `X` | S | Código de negociação do ativo (Ticker B3). Ex.: `PETR4       `. |
| `codigo_isin` | 54 | 65 | 12 | `X` | S | Código internacional de identificação do ativo (ISIN). Ex.: `BRPETRACNPR6`. |
| `tipo_movimentacao` | 66 | 77 | 12 | `X` | S | Código padronizado do tipo de movimentação de custódia (ver tabela 2.2.1). Ex.: `LIQ_PREGAO  `. |
| `data_movimento` | 78 | 85 | 8 | `D` | S | Data em que a movimentação foi registrada na Depositária (`YYYYMMDD`). |
| `data_liquidacao_fisica`| 86 | 93 | 8 | `D` | S | Data em que ocorreu ou ocorrerá a efetiva liquidação física (`YYYYMMDD`). |
| `indicador_debito_credito` | 94 | 94 | 1 | `X` | S | Indicador de sentido na conta de custódia do investidor: `D` (Débito) ou `C` (Crédito). |
| `quantidade_movimentada` | 95 | 109 | 15 | `N` | S | Quantidade nominal inteira de ativos movimentados (N15). Ex.: `000000000005000`. |
| `motivo_historico` | 110 | 149 | 40 | `X` | S | Descrição textual abreviada ou histórico padronizado da movimentação. |
| `reservado` | 150 | 200 | 51 | `X` | S | Filler preenchido com espaços em branco. |

#### 2.2.1 Domínio dos Códigos de Tipo de Movimentação

| Código | Descrição de Negócio | Sentido Típico |
|---|---|---|
| `LIQ_PREGAO` | Liquidação física de operações realizadas em bolsa/pregão (Clearing D+2) | `C` (compra) / `D` (venda) |
| `TRANSF_CUST` | Transferência de custódia entre participantes (STVM / portabilidade) ou contas | `D` (saída) / `C` (entrada) |
| `BLOQ_JUD` | Bloqueio judicial por determinação de ofício/SisbaJud | `D` (indisponibilização) |
| `DESB_JUD` | Desbloqueio judicial e restituição à custódia disponível | `C` (restituição) |
| `DEPOS_GAR` | Depósito/vinculação de ativos em carteira de garantia na Clearing B3 | `D` (saída do livre) |
| `RETIR_GAR` | Retirada/desvinculação de garantias com retorno à custódia livre | `C` (retorno ao livre) |
| `EXERC_OPC` | Liquidação física por exercício de opções sobre ações / índices | `C` (titular compra) / `D` (lançador) |

---

### 2.3 Registro Trailler (Tipo 99) — Totalização e Fechamento

O Trailler atua como checksum criptográfico/aritmético do arquivo, garantindo a integridade total do transporte e do processamento noturno.

| Campo | De | Até | Tam | Tipo | Obrig | Descrição / Exemplo |
|---|---|---|---|---|---|---|
| `tipo_registro` | 1 | 2 | 2 | `N` | S | Identificador do registro: fixo `99`. |
| `identificador_layout` | 3 | 11 | 9 | `X` | S | Identificador do leiaute: fixo `IMBARQ013`. |
| `total_registros_arquivo` | 12 | 19 | 8 | `N` | S | Contagem total de linhas do arquivo (Header + Detalhes + Trailler). |
| `total_registros_detalhe` | 20 | 27 | 8 | `N` | S | Quantidade exata de registros do tipo `01`. |
| `total_quantidade_debito` | 28 | 45 | 18 | `N` | S | Somatório da `quantidade_movimentada` de todos os registros com indicador `D`. |
| `total_quantidade_credito` | 46 | 63 | 18 | `N` | S | Somatório da `quantidade_movimentada` de todos os registros com indicador `C`. |
| `sinal_saldo_liquido` | 64 | 64 | 1 | `X` | S | Sinal do saldo líquido: `C` se Crédito >= Débito; `D` se Débito > Crédito. |
| `saldo_liquido_movimentacao`| 65 | 82 | 18 | `N` | S | Quantidade líquida absoluta das movimentações (`\|total_credito - total_debito\|`). |
| `reservado` | 83 | 200 | 118 | `X` | S | Filler preenchido com espaços em branco. |

---

## 3. Regras de Negócio e Validações de Integridade

### 3.1 Integridade Estrutural

1. **Comprimento Invariante**: Cada registro deve possuir rigorosamente **200 caracteres** (excluindo os caracteres terminadores de linha). Qualquer linha com tamanho divergente dispara a exceção `InvalidRecordLengthError`.
2. **Sequência Canônica de Registros**:
   - Linha 1: obrigatoriamente Header (`00`).
   - Linhas 2 a N-1: obrigatoriamente Detalhes de Movimentação (`01`).
   - Linha N: obrigatoriamente Trailler (`99`).
   - Arquivos vazios ou sem Header/Trailler são rejeitados com `InvalidRecordStructureError`.

### 3.2 Validação de Domínio dos Campos

1. **Tipos de Movimentação Homologados**: O campo `tipo_movimentacao` aceita estritamente o conjunto:
   `{'LIQ_PREGAO', 'TRANSF_CUST', 'BLOQ_JUD', 'DESB_JUD', 'DEPOS_GAR', 'RETIR_GAR', 'EXERC_OPC'}`. Valores desconhecidos geram `RecordParsingError`.
2. **Indicador de Sentido Débito/Crédito**: O campo `indicador_debito_credito` aceita unicamente `'D'` ou `'C'`.
3. **Consistência Temporal de Liquidação**: A data de liquidação física (`data_liquidacao_fisica`) não pode ser anterior à data de movimento (`data_movimento`), visto que a liquidação física ocorre no mesmo dia (D+0 para bloqueios/transferências) ou em ciclos futuros (D+1 ou D+2 para pregão/opções).

### 3.3 Batimento Aritmético e Numérico do Trailler

O processamento do IMBARQ013 somente é concluído com sucesso após verificação de 100% dos somatórios:
- `total_registros_arquivo == count(todas as linhas)`
- `total_registros_detalhe == count(linhas tipo 01)`
- `total_quantidade_debito == sum(quantidade_movimentada onde indicador == 'D')`
- `total_quantidade_credito == sum(quantidade_movimentada onde indicador == 'C')`
- `sinal_saldo_liquido == ('C' if total_credito >= total_debito else 'D')`
- `saldo_liquido_movimentacao == abs(total_credito - total_debito)`

Qualquer divergência nas contagens ou totalizadores resulta em `TrailerValidationError`, impedindo que extratos corrompidos contaminem a base de reconciliação de custódia.
