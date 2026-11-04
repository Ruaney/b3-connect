# Especificação Técnica: Registro e Gravame de Recebíveis de Cartão de Crédito no NoMe B3 (FL-069)

## 1. Visão Geral e Contexto Regulatório

O sistema de **Registro e Gravame de Recebíveis de Arranjos de Pagamento (Cartões de Crédito e Débito)** no **Balcão NoMe B3** implementa a infraestrutura de mercado financeiro exigida pelo Banco Central do Brasil para viabilizar a desintermediação e democratização do crédito empresarial garantido por fluxos futuros de vendas no varejo.

O arcabouço normativo que rege o fluxo é composto por:

* **Resolução CMN nº 4.734, de 27 de junho de 2019**: Estabelece as condições e os limites para a realização de operações de desconto de recebíveis e de operações de crédito com garantia de recebíveis decorrentes de transações em arranjos de pagamento integrantes do SPB.
* **Circular BCB nº 3.952, de 4 de novembro de 2019**: Disciplina o funcionamento dos sistemas de registro de recebíveis, a governança da interoperabilidade entre entidades registradoras (B3 NoMe, CERC, TAG), credenciadoras e instituições financeiras concessionárias de crédito.
* **Resolução BCB nº 264, de 25 de novembro de 2022**: Disciplina as regras de registro e constituição de gravames e ônus reais sobre ativos financeiros e direitos creditórios registrados.

```
+-------------------------------------------------------------------------------------------------------------------------+
|                    ARQUITETURA DE REGISTRO E GRAVAMES DE RECEBÍVEIS DE CARTÃO NO NoMe B3 (FL-069)                      |
+-------------------------------------------------------------------------------------------------------------------------+
|                                                                                                                         |
|    +-----------------------------+                                                                                      |
|    |  CREDENCIADORAS / SUBACQ.   | Lote de Vendas de Cartão (Visa, Master, Elo...)                                      |
|    |  (Cielo, Rede, Stone, etc.) | Data de Liquidação Futura (D+30, parcelado, débito)                                  |
|    +-----------------------------+                                                                                      |
|                   |                                                                                                     |
|                   v                                                                                                     |
|    +---------------------------------------------------------------------------------------------------------------+    |
|    |                                  SISTEMA REGISTRADOR B3 BALCÃO NoMe (FL-069)                                  |    |
|    |                                                                                                               |    |
|    |   1. Agrupamento Canônico em UNIDADE DE RECEBÍVEIS (UR) pela Quádrupla Regulatória:                            |    |
|    |      [CNPJ Lojista + Credenciadora + Arranjo de Pagamento + Data Prevista de Liquidação]                      |    |
|    |                                                                                                               |    |
|    |   2. Gestão de Consentimento Ativo (Opt-in) do Lojista:                                                       |    |
|    |      - Autorização prévia por CNPJ para que a Instituição Financeira credora consulte e grave URs.            |    |
|    |                                                                                                               |    |
|    |   3. LEI DA UNICIDADE DO GRAVAME E CAPACIDADE DISPONÍVEL:                                                     |    |
|    |      V_disponivel = V_atual - V_comprometido (com V_disponivel >= 0)                                          |    |
|    |      -> Bloqueio absoluto de sobre-oneramento e duplicidade de garantias.                                     |    |
|    +---------------------------------------------------------------------------------------------------------------+    |
|                   |                                               |                                                     |
|         +---------+---------+                           +---------+---------+                                           |
|         |                   |                           |                   |                                           |
|         v                   v                           v                   v                                           |
|   +---------------+   +-------------------+       +---------------+   +-------------------+                             |
|   |  CONSTITUIÇÃO |   |    ANTECIPAÇÃO    |       |   ADITAMENTO  |   | DESCONSTITUIÇÃO   |                             |
|   |  DE GRAVAME   |   |   (PRÉ-PAGAMENTO) |       |  (DESONERAÇÃO |   | (BAIXA DE GRAVAME)|                             |
|   |  FIDUCIÁRIO   |   |                   |       |   PARCIAL)    |   |                   |                             |
|   |               |   | * Trava: apenas   |       |               |   | * Quitação da CCB |                             |
|   | * Banco Itaú  |   |   sobre V_livre!  |       | * Redução do  |   | * Restituição do  |                             |
|   | * Bradesco    |   | * Veda antecipar  |       |   valor da    |   |   saldo livre     |                             |
|   | * BTG Pactual |   |   parcela gravada |       |   garantia    |   |   integral ao     |                             |
|   | * Trava de    |   | * Desconto taxa   |       | * Libera margem   |   lojista         |                             |
|   |   Domicílio   |   | * V_novo=V-V_ant  |       |   livre na UR |   |                   |                             |
|   +---------------+   +-------------------+       +---------------+   +-------------------+                             |
|         |                   |                           |                   |                                           |
|         +-------------------+-------------+-------------+-------------------+                                           |
|                                           |                                                                             |
|                                           v                                                                             |
|    +---------------------------------------------------------------------------------------------------------------+    |
|    |                                 LIQUIDAÇÃO FINANCEIRA NA MATURIDADE (D0)                                      |    |
|    |                                                                                                               |    |
|    |   * Pagamento Prioritário aos Credores Garantidos nos domicílios vinculados (Trava Fiduciária).                |    |
|    |   * Repasse do Saldo Remanescente Livre para a conta ordinária do Lojista.                                     |    |
|    |   * CONSERVAÇÃO CONTÁBIL ABSOLUTA: Sum(Credores) + Lojista == V_atual.                                        |    |
|    |   * Baixa Registral: Status = LIQUIDADA.                                                                      |    |
|    +---------------------------------------------------------------------------------------------------------------+    |
|                                                                                                                         |
+-------------------------------------------------------------------------------------------------------------------------+
```

---

## 2. A Unidade de Recebíveis (UR) e sua Chave Canônica

A **Unidade de Recebíveis (UR)** é o ativo financeiro indivisível de registro definido pelo Banco Central do Brasil, correspondente ao conjunto de fluxos financeiros futuros de titularidade de um mesmo usuário recebedor (lojista).

### 2.1 A Quádrupla Regulatória Canônica

Toda UR registrada no Balcão NoMe B3 é univocamente delimitada pela quádrupla:

1. **`merchant_document`**: CPF (11 dígitos) ou CNPJ (14 dígitos) do estabelecimento comercial recebedor.
2. **`acquirer_code`**: Código identificador da Credenciadora ou Subcredenciadora de pagamento (ex: `CIELO`, `REDE`, `STONE`, `PAGBANK`).
3. **`payment_scheme`**: Arranjo de Pagamento autorizado pelo BACEN (bandeira e modalidade de liquidação, ex: `VISA_CREDITO_A_VISTA`, `MASTERCARD_CREDITO_PARCELADO`, `ELO_DEBITO`).
4. **`settlement_date`**: Data prevista para liquidação financeira do fluxo (`YYYY-MM-DD`).

### 2.2 Estrutura Contábil da UR

Cada UR mantém um balanço dinâmico e auditável de valores monetários expressos em `Decimal` (2 casas decimais):

| Campo | Descrição | Regra de Consistência e Conservação |
|---|---|---|
| `original_amount` | Valor acumulado inicial registrado das vendas | Incrementado a cada lote diário de vendas submetido. |
| `current_amount` | Valor de face total vigente da UR | $V_{\text{atual}} = V_{\text{comprometido}} + V_{\text{disponível}}$. Reduzido em antecipações. |
| `committed_amount` | Valor total gravado ou onerado por garantias | $\sum_{g \in \text{Gravames Ativos}} V_{\text{gravame}}(g)$. |
| `available_amount` | Valor livre e desonerado para novas operações | $V_{\text{disponível}} = V_{\text{atual}} - V_{\text{comprometido}}$. Obrigatoriamente $\ge 0$. |
| `settled_amount` | Volume financeiro já liquidado da UR | Acumula antecipações prévias e liquidação no vencimento. |

---

## 3. Leis Formais de Conservação e Invariantes

### 3.1 Lei da Unicidade do Gravame e Não-Sobreposição

> **Invariante Formal 1 (Capacidade Disponível Não-Negativa)**:
> Para qualquer UR em qualquer instante do tempo $t$:
> $$V_{\text{disponível}}(t) = V_{\text{atual}}(t) - V_{\text{comprometido}}(t) \ge 0$$

* Nenhuma instituição financeira pode constituir gravame cujo valor pretendido supere a capacidade livre:
  $$V_{\text{novo\_gravame}} \le V_{\text{disponível}}$$
* É terminantemente vedado o sobre-oneramento ou dupla alienação sobre a mesma fração de recebível.
* Quando $V_{\text{disponível}} = 0$, a UR atinge o status `TOTALMENTE_ONERADA`, rejeitando novas concessões de garantia ou antecipações.

### 3.2 Lei da Conservação Contábil na Antecipação de Recebíveis

> **Invariante Formal 2 (Vedação de Antecipação sobre Parcela Gravada)**:
> Conforme o Art. 13 da Resolução CMN nº 4.734/2019, a antecipação (pré-pagamento intra ou inter-credenciadora) incide **exclusivamente sobre o saldo livre**:
> $$V_{\text{antecipado}} \le V_{\text{disponível}}$$

* Ao antecipar $V_{\text{antecipado}}$ à taxa de desconto $d$:
  $$\text{Taxa de Desconto Retida} = V_{\text{antecipado}} \times d$$
  $$\text{Valor Líquido Desembolsado ao Lojista} = V_{\text{antecipado}} - \text{Taxa de Desconto Retida}$$
  $$\text{Conservação do Pré-pagamento}: \text{Valor Líquido} + \text{Taxa de Desconto} \equiv V_{\text{antecipado}}$$
* Atualização da UR:
  $$V_{\text{atual\_novo}} = V_{\text{atual\_anterior}} - V_{\text{antecipado}}$$
  $$V_{\text{liquidado\_novo}} = V_{\text{liquidado\_anterior}} + V_{\text{antecipado}}$$
  $$V_{\text{comprometido}} \text{ permanece inalterado (garantia bancária blindada)}$$

### 3.3 Lei da Conservação Contábil na Liquidação Final com Trava de Domicílio

> **Invariante Formal 3 (Fechamento Soma-Zero de Liquidação)**:
> Na data prevista de vencimento ($D0$), os fluxos são distribuídos em ordem estrita de prioridade (rank de gravames fiduciários e penhores):
> $$\sum_{g \in \text{Gravames Ativos}} V_{\text{liquidado\_credor}}(g) + V_{\text{liquidado\_lojista}} \equiv V_{\text{atual}}$$

* O credor titular de gravame com domicílio vinculado (*trava bancária*) tem prioridade sênior de recebimento em sua conta fiduciária designada.
* O saldo livre restante da UR é canalizado diretamente para a conta bancária padrão do lojista.
* A UR transiciona para o estado `LIQUIDADA` com saldos zerados.

---

## 4. Interfaces e Componentes do Módulo

| Componente | Tipo | Descrição Técnica |
|---|---|---|
| `PaymentScheme` | `Enum` | Arranjos autorizados: Visa, Mastercard e Elo em modalidades Crédito (à vista/parcelado) e Débito. |
| `LienModality` | `Enum` | Modalidades reais: Cessão Fiduciária, Penhor Mercantil, Cessão Definitiva e Bloqueio Cautelar. |
| `UrStatus` | `Enum` | Estados operacionais: `ATIVA`, `TOTALMENTE_ONERADA`, `LIQUIDADA` e `CANCELADA`. |
| `BankAccount` | `Dataclass` | Domicílio bancário com ISPB (8 dígitos), agência, conta, tipo e titular. |
| `ReceivableUnit` | `Dataclass` | Entidade patrimonial da UR com quádrupla e controle contábil. |
| `LienRecord` | `Dataclass` | Registro de gravame fiduciário com protocolo único, credor, valor e rank de prioridade. |
| `AnticipationRecord` | `Dataclass` | Recibo de pré-pagamento com valor de face, taxa de desconto e líquido desembolsado. |
| `UrSettlementReceipt` | `Dataclass` | Comprovante de liquidação na maturidade auditando o fechamento soma-zero. |
| `CardReceivablesRegistryEngine` | `Class` | Motor central determinístico do Balcão NoMe B3. |

### Rastreabilidade de Artefatos no B3 Integration Atlas
* **Código-Fonte**: `src/card_receivables_registry.py`
* **Fixture Sintética**: `fixtures/card_receivables.json`
* **Suíte de Testes**: `tests/test_card_receivables.py`
* **Especificação de Layout**: `docs/layout-card-receivables.md`
