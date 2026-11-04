# Especificação Técnica de Escrituração, Endosso e Liquidação de Duplicatas Escriturais Eletrônicas (FL-051) — Balcão NoMe B3

## 1. Visão Geral e Contexto Regulatório

A **duplicata escritural eletrônica** é o título de crédito emitido sob a forma escritural em sistema eletrônico autorizado pelo Banco Central do Brasil, representando a obrigação de pagamento decorrente de compra e venda mercantil ou de prestação de serviços. A modernização do mercado brasileiro de recebíveis de duplicatas foi consolidada pelo seguinte arcabouço normativo:

* **Lei nº 13.775, de 20 de dezembro de 2018**: Institui a duplicata escritural, estabelece que sua emissão deve ocorrer exclusivamente em sistema eletrônico gerido por entidade registradora ou escrituradora autorizada pelo Banco Central, e disciplina as regras de circulação, endosso, garantias e liquidação.
* **Circular BCB nº 4.016, de 4 de maio de 2020** (e resoluções complementares Resolução CMN nº 4.815/2020 e Resolução BCB nº 339/2023): Regulamenta a atividade de escrituração de duplicata escritural por entidades registradoras autorizadas, estipulando os requisitos técnicos, regras de interoperabilidade, mensageria e comprovação de lastro.
* **Lei nº 5.474, de 18 de julho de 1968 (Lei das Duplicatas)**: Subsidiária naquilo que não colidir com o regime escritural eletrônico, preservando o princípio da causalidade do título.

O módulo **B3 Balcão NoMe** atua como infraestrutura de mercado financeiro (IMF) provedora do serviço de registro e escrituração eletrônica de duplicatas, conectando emissores (sacadores/cedentes), compradores (sacados), bancos mandatários de cobrança, cessionários (FIDCs, securitizadoras, factorings, bancos financiadores) e sistemas de liquidação financeira (SPB e Pix).

```
+-------------------------------------------------------------------------------------------------------------------------+
|                              ARQUITETURA DE REGISTRO E CICLO DE VIDA DA DUPLICATA ESCRITURAL (FL-051)                   |
+-------------------------------------------------------------------------------------------------------------------------+
|                                                                                                                         |
|    +-----------------------------+                                                                                      |
|    |  Emissão da NF-e (SEFAZ)    | Chave de Acesso (44 dígitos)                                                         |
|    |  - Dados da Fatura/Parcelas | CNPJ Emitente / Sacador + CNPJ Sacado                                                |
|    +-----------------------------+                                                                                      |
|                   |                                                                                                     |
|                   v                                                                                                     |
|    +---------------------------------------------------------------------------------------------------------------+    |
|    |                               SISTEMA REGISTRADOR B3 BALCÃO NoMe (FL-051)                                     |    |
|    |                                                                                                               |    |
|    |   1. Validação Sintática e Causal: Chave NF-e (DV Mod 11) + Coerência com CNPJ Emitente                       |    |
|    |   2. MECANISMO ANTI-FRAUDE DE UNICIDADE DE LASTRO: Trava unívoca [Chave NF-e + Número da Parcela]             |    |
|    |      -> Bloqueio instantâneo de duplicidade intra-sistema e via Interoperabilidade de Registradoras           |    |
|    |   3. Escrituração Inicial: Status = REGISTRADA (Titular Inicial = Emitente / Sacador)                         |    |
|    +---------------------------------------------------------------------------------------------------------------+    |
|                   |                                                                                                     |
|         +---------+-----------------------------------+                                                                 |
|         |                                             |                                                                 |
|         v                                             v                                                                 |
|   +---------------------------------------+     +---------------------------------------+                               |
|   |         ENDOSSO TRANSLATIVO           |     |            ENDOSSO MANDATO            |                               |
|   | - Transferência plena de titularidade |     | - Procuração para cobrança bancária   |                               |
|   | - Cessionário: FIDC / Banco / Securit.|     | - Banco cobrador / Carteira simples   |                               |
|   | - Titularidade atualizada no NoMe     |     | - Titularidade creditória inalterada  |                               |
|   | - Status = ENDOSSADA                  |     | - Status = EM_COBRANCA                |                               |
|   +---------------------------------------+     +---------------------------------------+                               |
|         |                                             |                                                                 |
|         +---------------------+-----------------------+                                                                 |
|                               |                                                                                         |
|                               v                                                                                         |
|    +---------------------------------------------------------------------------------------------------------------+    |
|    |                                     LIQUIDAÇÃO FINANCEIRA E QUITAÇÃO (FL-051)                                 |    |
|    |                                                                                                               |    |
|    |   * Canal SPB (STR / TED / LDL0001) ou Arranjo PIX (E2E ID / Pix Cobrança)                                    |    |
|    |   * Conservação Contábil: Valor Liquidado == Principal + Juros + Multa - Descontos                             |    |
|    |   * Baixa Registral Automática: Saldo Devedor = R$ 0,00 -> Status = LIQUIDADA                                 |    |
|    +---------------------------------------------------------------------------------------------------------------+    |
|                                                                                                                         |
+-------------------------------------------------------------------------------------------------------------------------+
```

---

## 2. Registro a Partir de Dados da NF-e

A duplicata mercantil é um título **causal**, dependendo da existência prévia de uma operação mercantil comprovada por Nota Fiscal Eletrônica (NF-e modelo 55) emitida e autorizada pela SEFAZ.

### 2.1 Estrutura e Validação da Chave de Acesso da NF-e (44 dígitos)

A Chave de Acesso da NF-e é composta por 44 caracteres numéricos, subdivididos nos seguintes blocos obrigatórios:

| Campo | Posição | Tamanho | Descrição | Regra de Consistência B3 NoMe |
|---|---|---|---|---|
| `cUF` | 01 - 02 | 2 | Código IBGE da Unidade da Federação do emitente | Deve ser código UF IBGE válido (11 a 53). |
| `AAMM` | 03 - 06 | 4 | Ano e Mês de emissão da NF-e (ex: `2611` para Nov/2026) | Mês entre `01` e `12`. |
| `CNPJ` | 07 - 20 | 14 | CNPJ do emitente da NF-e (sacador da duplicata) | Deve coincidir exatamente com o CNPJ do emissor da duplicata. |
| `mod` | 21 - 22 | 2 | Modelo do documento fiscal (`55` para NF-e mercantil) | Exige modelo `55` para duplicatas mercantis. |
| `serie`| 23 - 25 | 3 | Série da NF-e (`000` a `999`) | Numérico. |
| `nNF` | 26 - 34 | 9 | Número do documento fiscal da NF-e | Numérico > 0. |
| `tpEmis`| 35 - 35 | 1 | Forma de emissão (1=Normal, 2=FS-IA, 3=SCAN, etc.) | Dígito numérico válido. |
| `cNF` | 36 - 43 | 8 | Código numérico que compõe a chave de acesso | Chave aleatória gerada pelo emissor. |
| `cDV` | 44 - 44 | 1 | Dígito Verificador calculado pelo Módulo 11 | Validação aritmética com pesos 2 a 9 da direita para esquerda. |

#### Algoritmo do Módulo 11 para Validação do DV da NF-e
1. Tomam-se os primeiros 43 dígitos da chave de acesso.
2. Cada dígito, da direita para a esquerda (a partir do dígito 43), é multiplicado sucessivamente pelos pesos `[2, 3, 4, 5, 6, 7, 8, 9]`, reiniciando em `2` após atingir o peso `9`.
3. Somam-se todos os produtos obtidos: $\text{Soma} = \sum_{i=1}^{43} d_{44-i} \times \text{peso}_i$.
4. Calcula-se o resto da divisão inteira por 11: $\text{Resto} = \text{Soma} \pmod{11}$.
5. Determina-se o Dígito Verificador ($cDV$):
   $$\text{Se } \text{Resto} \in \{0, 1\} \implies cDV = 0$$
   $$\text{Caso contrário} \implies cDV = 11 - \text{Resto}$$
6. Caso o 44º dígito informado divirja do $cDV$ calculado, o registro é sumariamente rejeitado pelo motor com código `INVALID_NFE_CHECK_DIGIT`.

### 2.2 Atributos Essenciais da Duplicata Escritural

Para cada duplicata registrada, os seguintes campos são obrigatórios:
* **Identificador da Duplicata (`trade_bill_id`)**: Identificador único alfanumérico no padrão B3 NoMe.
* **Chave de Acesso NF-e (`nfe_access_key`)**: 44 dígitos validados.
* **Número da Parcela (`installment_number`)**: Parcela associada à fatura (ex.: `1`, `2`, `3`).
* **Total de Parcelas (`total_installments`)**: Número total de parcelas da fatura/NF-e.
* **Sacador / Emitente (`issuer_document`, `issuer_name`)**: CNPJ e razão social do credor original (vendedor).
* **Sacado / Devedor (`drawee_document`, `drawee_name`)**: CNPJ e razão social do devedor (comprador).
* **Valor de Face (`face_value`)**: Valor nominal da parcela em moeda corrente nacional (`Decimal`).
* **Data de Emissão (`issue_date`)** e **Data de Vencimento (`due_date`)**: Prazos comerciais do título.
* **Titular Legítimo Atual (`current_owner_document`, `current_owner_name`)**: Inicialmente o emitente, atualizado dinamicamente a cada endosso translativo.

---

## 3. Mecanismo Anti-Fraude de Unicidade de Lastro (Ballast Uniqueness)

Historicamente, o mercado de duplicatas físicas em papel sofria com a prática criminosa de "duplicatas frias" ou "duplicatas simuladas" (emissão sem lastro mercantil real) e com a **duplicidade de desconto** (o mesmo título ou parcela descontado simultaneamente em múltiplos bancos ou FIDCs).

A Lei nº 13.775/2018 e a Circular BCB nº 4.016 impuseram a **unicidade absoluta de escrituração de recebíveis**:

1. **Chave Primária de Unicidade de Lastro**:
   $$\text{LastroKey} = (\text{nfe\_access\_key}, \text{installment\_number})$$
2. **Regra de Bloqueio Automático**:
   * O sistema mantém uma tabela em tempo real de tuplas $(\text{nfe\_access\_key}, \text{installment\_number})$.
   * Ao recepcionar um comando de escrituração, o motor consulta o índice de lastro.
   * Se a tupla já existir no repositório da B3 NoMe ou em qualquer registradora do ecossistema de interoperabilidade (CERC, TAG, B3), o motor **rejeita imediatamente a operação**, gerando o evento `DUPLICATE_BALLAST_DETECTED`.
   * Essa verificação impede fraude mesmo que o participante tente submeter valores ou sacados divergentes sob a mesma chave de NF-e e parcela.

---

## 4. Endosso Eletrônico e Cadeia de Titularidade

O endosso da duplicata escritural é registrado eletronicamente no sistema da entidade escrituradora, produzindo todos os efeitos jurídicos cambiais e de direito comum (art. 5º da Lei 13.775/2018).

```
+----------------------------------------------------------------------------------------------------+
|                                    MODALIDADES DE ENDOSSO ELETRÔNICO                               |
+----------------------------------------------------------------------------------------------------+
|                                                                                                    |
|  [ ENDOSSO TRANSLATIVO ]                                                                           |
|  - Finalidade: Transferência definitiva e plena da titularidade econômica do crédito.              |
|  - Casos de Uso: Cessão de crédito para FIDC, securitizadora, ou antecipação/desconto bancário.   |
|  - Efeito no Sistema:                                                                              |
|      * current_owner_document -> Atualizado para o CNPJ do Cessionário (ex: FIDC)                   |
|      * Status do Título -> Transita para ENDOSSADA                                                |
|      * Apenas o novo titular possui poderes para futuros endossos translativos.                    |
|                                                                                                    |
|  [ ENDOSSO MANDATO ]                                                                               |
|  - Finalidade: Constituição de mandatário exclusivo para execução de cobrança bancária simples.     |
|  - Casos de Uso: Envio de carteira simples para banco emissor de boleto bancário de cobrança.      |
|  - Efeito no Sistema:                                                                              |
|      * current_owner_document -> Permanece INALTERADO (o titular credor não muda).                 |
|      * mandatary_document -> Preenchido com o CNPJ da instituição cobradora (Banco).              |
|      * Status do Título -> Transita para EM_COBRANCA                                               |
|      * O mandatário está autorizado a emitir boleto/QR Code e receber quitação pelo titular.       |
|                                                                                                    |
+----------------------------------------------------------------------------------------------------+
```

### 4.1 Validação da Cadeia de Endossos
O motor `ElectronicTradeBillEngine` executa as seguintes verificações formais em cada operação de endosso:
1. **Legitimidade do Endossante**: O `endorser_document` deve coincidir exatamente com o `current_owner_document` atual do título. Terceiros ou titulares anteriores não possuem poder de disposição.
2. **Estado Elegível**: O título deve estar com status `REGISTRADA`, `ENDOSSADA` ou `EM_COBRANCA`. Títulos com status `LIQUIDADA` ou `BAIXADA` não podem receber endossos.
3. **Consistência Temporal**: O carimbo de data/hora do novo endosso deve ser cronologicamente posterior ou igual ao do último evento registrado e à escrituração inicial.
4. **Assinatura e Integridade**: Cada endosso gera um hash criptográfico (SHA-256) atestando a cadeia inquebrável de titularidade.

---

## 5. Liquidação Financeira, Quitação e Baixa Registral

A liquidação financeira extingue a obrigação creditícia da duplicata mediante pagamento efetuado pelo sacado (ou terceiro interveniente) em favor do legítimo titular ou de seu mandatário formal de cobrança.

### 5.1 Canais de Liquidação Suportados
* **PIX (Arranjo Pix do Banco Central)**: Liquidação instantânea 24/7 com geração de End-to-End ID (`E2E_ID`) padronizado de 32 caracteres.
* **SPB (Sistema de Pagamentos Brasileiro)**: Liquidação via STR (Sistema de Transferência de Reservas) ou CIP/SILOC (mensagens LDL0001 / STR0004) com identificador formal da mensagem financeira.
* **BOLETO**: Liquidação via compensação bancária tradicional com código de barras/linha digitável.

### 5.2 Lei de Conservação Contábil da Liquidação
A liquidação exige a exata conciliação dos componentes financeiros:
$$\text{settled\_amount} = \text{principal\_amount} + \text{interest\_amount} + \text{fine\_amount} - \text{discount\_amount}$$

Regras de negócio:
1. $\text{principal\_amount}$ deve corresponder exatamente ao saldo devedor ($\text{balance\_due}$) na liquidação total.
2. O pagador ($\text{payer\_document}$) deve corresponder ao sacado do título ($\text{drawee\_document}$).
3. O recebedor ($\text{receiver\_document}$) deve ser o titular atual ($\text{current\_owner\_document}$) ou o mandatário registrado ($\text{mandatary\_document}$).
4. Após o processamento da liquidação, $\text{balance\_due}$ é reduzido a zero e o status da duplicata é atualizado para `LIQUIDADA`.
5. Tentativas subsequentes de liquidação de um título já liquidado são rejeitadas com erro `TRADE_BILL_ALREADY_SETTLED`.

---

## 6. Layout de Mensageria JSON do NoMe Balcão (FL-051)

### 6.1 Registro de Duplicata Escritural (`trade_bills`)

```json
{
  "trade_bill_id": "TBILL-2026-NFE001-01",
  "nfe_access_key": "35261112345678000195550010001234561123456789",
  "installment_number": 1,
  "total_installments": 3,
  "issuer_document": "12345678000195",
  "issuer_name": "INDÚSTRIA DE ALIMENTOS PAULISTANA LTDA",
  "drawee_document": "98765432000198",
  "drawee_name": "REDE DE SUPERMERCADOS BRASIL S.A.",
  "face_value": "50000.00",
  "issue_date": "2026-11-01",
  "due_date": "2026-12-01",
  "status": "REGISTRADA",
  "registrar_code": "B3_NOME"
}
```

### 6.2 Registro de Endosso Eletrônico (`endorsements`)

```json
{
  "endorsement_id": "END-2026-001-01",
  "endorsement_type": "TRANSLATIVO",
  "endorser_document": "12345678000195",
  "endorser_name": "INDÚSTRIA DE ALIMENTOS PAULISTANA LTDA",
  "endorsee_document": "11223344000186",
  "endorsee_name": "FIDC ALPHA RECEBÍVEIS MULTISSETORIAL",
  "timestamp": "2026-11-02T10:30:00Z",
  "amount": "50000.00",
  "digital_signature_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "notes": "Cessão definitiva de direitos creditórios para carteira FIDC"
}
```

### 6.3 Registro de Liquidação Financeira (`settlement`)

```json
{
  "settlement_id": "SETTL-2026-001",
  "method": "PIX",
  "payer_document": "98765432000198",
  "receiver_document": "11223344000186",
  "principal_amount": "50000.00",
  "interest_amount": "0.00",
  "fine_amount": "0.00",
  "discount_amount": "500.00",
  "settled_amount": "49500.00",
  "settlement_timestamp": "2026-11-28T14:15:00Z",
  "channel_transaction_id": "E12345678202611281415A9876543210",
  "notes": "Liquidação Pix com desconto comercial de antecipação"
}
```

---

## 7. Sumário dos Invariantes e Regras Formais

1. **Unicidade de Lastro**: $\forall i, j \in \text{Bills}, \quad (\text{key}_i = \text{key}_j \land \text{inst}_i = \text{inst}_j) \iff i = j$.
2. **Conservação da Titularidade**: Um endosso translativo transfere a propriedade do credor $A$ para o credor $B$, de modo que $\sum \text{Titularidade} = 1$ para cada duplicata em qualquer instante temporal.
3. **Imutabilidade Pós-Liquidação**: $\text{Status} = \text{LIQUIDADA} \implies \Delta(\text{Endossos}) = \emptyset \land \Delta(\text{Liquidação}) = \emptyset$.
4. **Conservação de Saldo**: $\text{face\_value} = \text{settled\_principal} + \text{balance\_due}$.
