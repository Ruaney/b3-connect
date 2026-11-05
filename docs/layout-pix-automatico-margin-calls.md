# Especificação Técnica: Gestão e Liquidação em Tempo Real de Chamadas de Margem Intradiária via Pix Automático e SPI (FL-090)

## 1. Visão Geral e Contexto Regulatório

O gerenciamento de risco de contraparte central (CCP) da **Câmara B3** opera sob a metodologia **CORE (Closeout Risk Evaluation)**, monitorando em tempo contínuo a adequação de garantias depositadas contra potenciais perdas em cenários extremos de mercado. Em momentos de volatilidade atípica, choques de preços ou violação de limites de concentração intradiários, o motor CORE emite **Chamadas de Margem Adicional Intradiária (Intraday Margin Calls)** urgentes com prazos estritos para cobertura financeira.

Com a promulgação da **Resolução BCB nº 1/2020** (Regulamento do Pix e do Sistema de Pagamentos Instantâneos - SPI) e da **Resolução BCB nº 341/2023** (Regulamentação do **Pix Automático** e débito recorrente consentido), consolidou-se a infraestrutura tecnológica para a execução de débitos instantâneos sem necessidade de autorização transação-a-transação pelo comitente pagador.

O fluxo **`FL-090`** estabelece a integração determinística entre:
1. **Jornada de Consentimento Prévio de Débito Recorrente (Pix Automático)**: Mandato registrado previamente pelo investidor qualificado ou participante liquidante com parâmetros de teto financeiro por chamada e mensal;
2. **Disparo Imediato pelo Motor de Risco CORE**: Apuração em milissegundos do déficit de garantias e conversão da obrigação em instrução de pagamento instantâneo;
3. **Liquidação Interbancária no SPI / BACEN (SLA < 10 segundos)**: Envio da mensagem `pacs.008` ISO 20022 via canal SPB/SPI debitando a Conta Pagamentos Instantâneos (Conta PI) do participante e creditando a Conta PI de Liquidação da B3;
4. **Idempotência Estrita e Rastreabilidade por EndToEndId**: Código regulamentar unívoco de 32 posições alfanuméricas com timestamp UTC;
5. **Aporte em D0 e Liberação Pré-Trade**: Restauração da capacidade de risco no PUMA Trading System ou mitigação de inadimplência;
6. **Protocolo Fail-Safe de Default Imediato**: Em caso de rejeição no SPI (saldo insuficiente `AM04`, mandato revogado `RC07`, limite violado `ED05` ou timeout `AB03`), a chamada transita imediatamente para `DEFAULTED`, bloqueando a submissão de ordens pré-trade e acionando medidas de encerramento forçado de posições (closeout compulsório).

---

## 2. Arquitetura de Comunicação e Fluxo Operacional

```mermaid
sequenceDiagram
    autonumber
    participant CORE as Motor CORE B3 (Risco)
    participant Engine as Motor Pix Automático B3 (FL-090)
    participant DICT as DICT BACEN (Chaves Pix)
    participant PSP_Pagador as PSP Pagador (Conta PI)
    participant SPI as SPI BACEN (Reservas PI)
    participant Clearing as Conta PI B3 (ISPB 00997185)
    participant PUMA as PUMA Trading System (Pré-Trade)

    Note over CORE,PUMA: 1. Disparo de Chamada Intradiária
    CORE->>Engine: Apura Déficit de Margem (issue_margin_call)
    Engine->>Engine: Valida Mandato Ativo e Limites (Res. BCB 341/2023)
    Engine->>DICT: Valida Chave Pix e Dados Bancários
    
    Note over Engine,SPI: 2. Instrução ISO 20022 pacs.008 (SLA < 10s)
    Engine->>PSP_Pagador: Gera pacs.008 com EndToEndId (32 chars) e MandateId
    PSP_Pagador->>SPI: Transmite Débito Direto Recorrente (CLRG / SPI)
    
    alt Liquidação Concluída no SPI (ACSC)
        SPI->>PSP_Pagador: Debita Conta PI do Pagador
        SPI->>Clearing: Credita Conta PI B3 Clearing (Soma-Zero)
        SPI-->>Engine: pacs.002 ACSC (AcceptedSettlementCompleted)
        Engine->>Engine: Registra Recibo Irrevogável D0
        Engine->>PUMA: Desbloqueia Capacidade Pré-Trade e Aporta Colateral
    else Rejeição no SPI / Falha de Mandato (RJCT)
        SPI-->>Engine: pacs.002 RJCT (AM04, RC07, ED05, AB03, etc.)
        Engine->>Engine: Transita Chamada para DEFAULTED
        Engine->>PUMA: Bloqueio Imediato Pré-Trade (Trading Halt)
        Engine->>CORE: Aciona Closeout Compulsório e Execução de Garantias
    end
```

---

## 3. Mensageria ISO 20022 e Extensões para Pix Automático

### 3.1. Mensagem `pacs.008.001.08` (Customer Credit Transfer via Débito Recorrente)
A instrução ISO 20022 encaminhada ao SPI contém os metadados da obrigação de margem e a referência ao mandato:

```xml
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08" xmlns:pix="https://www.bcb.gov.br/pi/pacs.008/pix-automatico/1.0">
  <FIToFICstmrCdtTrf>
    <GrpHdr>
      <MsgId>MSG-PACS008-ALPHA123456</MsgId>
      <CreDtTm>2026-11-05T14:30:00Z</CreDtTm>
      <NbOfTxs>1</NbOfTxs>
      <SttlmInf>
        <SttlmMtd>CLRG</SttlmMtd>
        <ClrSys>
          <Prtry>SPI</Prtry>
        </ClrSys>
      </SttlmInf>
    </GrpHdr>
    <CdtTrfTxInf>
      <PmtId>
        <EndToEndId>E60701190202611051430ALPHA123456</EndToEndId>
      </PmtId>
      <IntrBkSttlmAmt Ccy="BRL">3500000.00</IntrBkSttlmAmt>
      <MndtRltdInf>
        <MndtId>MNDT-B3-ITAU-ALPHA-001</MndtId>
      </MndtRltdInf>
      <Dbtr>
        <Nm>Investidor Fundo Alpha Qualificado</Nm>
      </Dbtr>
      <DbtrAgt>
        <FinInstnId>
          <ISPB>60701190</ISPB>
        </FinInstnId>
      </DbtrAgt>
      <Cdtr>
        <Nm>B3 S.A. - Brasil, Bolsa, Balcão</Nm>
      </Cdtr>
      <CdtrAgt>
        <FinInstnId>
          <ISPB>00997185</ISPB>
        </FinInstnId>
      </CdtrAgt>
      <Purp>
        <Prtry>B3_INTRADAY_MARGIN_CALL</Prtry>
      </Purp>
    </CdtTrfTxInf>
  </FIToFICstmrCdtTrf>
</Document>
```

### 3.2. Mensagem `pacs.002.001.10` (Payment Status Report)
Reporta o aceite técnico e liquidação definitiva (`ACSC`) ou rejeição regulamentar (`RJCT`):

```xml
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.002.001.10">
  <FIToFIPmtStsRpt>
    <GrpHdr>
      <MsgId>MSG-PACS002-ALPHA123456</MsgId>
      <CreDtTm>2026-11-05T14:30:01.500Z</CreDtTm>
    </GrpHdr>
    <TxInfAndSts>
      <OrgnlEndToEndId>E60701190202611051430ALPHA123456</OrgnlEndToEndId>
      <TxSts>ACSC</TxSts>
    </TxInfAndSts>
  </FIToFIPmtStsRpt>
</Document>
```

---

## 4. Estrutura do End-to-End ID (Padrão BACEN SPI)

O identificador de ponta a ponta (`EndToEndId`) é composto por exatamente **32 caracteres alfanuméricos**:

| Posição | Tamanho | Conteúdo | Descrição |
|---|---|---|---|
| `[0]` | 1 | `E` | Prefixo fixo para transferências padrão Pix / SPI. |
| `[1..8]` | 8 | ISPB Pagador | 8 dígitos numéricos do PSP liquidante debitado. |
| `[9..20]` | 12 | `YYYYMMDDHHMM` | Carimbo de data/hora UTC de geração da ordem. |
| `[21..31]` | 11 | `[a-zA-Z0-9]{11}` | Sequencial determinístico ou aleatório único. |

---

## 5. Governança do Mandato Pix Automático (Resolução BCB nº 341/2023)

O comitente autoriza previamente o débito de garantias intradiárias através da jornada de consentimento:
1. **Canal de Autorização**: Open Finance, Internet Banking ou Mobile Banking;
2. **Teto por Chamada (`max_limit_per_call`)**: Limite financeiro máximo admitido por chamada individual;
3. **Teto Mensal (`total_monthly_limit`)**: Limite cumulativo de débitos no ciclo corrente;
4. **Estados do Mandato**:
   - `ACTIVE`: Apto a autorizar liquidações instantâneas;
   - `SUSPENDED`: Temporariamente bloqueado para apurações preventivas;
   - `REVOKED`: Cancelado definitivamente pelo titular pagador;
   - `EXPIRED`: Vencido por expiração temporal.

---

## 6. Tratamento de Rejeições e Transição para Default Imediato

Caso a transação no SPI não complete com sucesso dentro da janela regulamentar de **10 segundos**, o motor FL-090 aciona o protocolo de contingência:

| Código de Rejeição | Motivo BACEN | Consequência Operacional na Câmara B3 |
|---|---|---|
| `AM04` | *InsufficientFunds* (Saldo insuficiente na Conta PI) | Falha de Liquidação -> Default Imediato |
| `AC04` | *ClosedAccount* (Conta encerrada/bloqueada) | Falha Cadastral -> Default Imediato |
| `RC07` | *InvalidMandate* (Mandato revogado/inexistente) | Falha de Consentimento -> Default Imediato |
| `ED05` | *LimitExceeded* (Teto por chamada ou mensal violado) | Excesso de Limite -> Default Imediato |
| `BE05` | *InvalidPixKey* (Chave DICT inativa ou inexistente) | Inconsistência Cadastral -> Default Imediato |
| `AB03` | *SettlementTimeout* (Latência > 10.0s) | Estouro de SLA -> Default Imediato |

### Ações Executadas no Default Imediato:
1. Chamada de Margem transita de `ISSUED` / `PENDING_SETTLEMENT` para `FAILED` e `DEFAULTED`;
2. Conta de colaterais do participante tem seu atributo `is_blocked_for_trading = True` e `is_in_default = True`;
3. Capacidade de risco pré-trade no PUMA Trading System é imediatamente zerada;
4. Disparo do protocolo de encerramento forçado de posições em aberto e execução de colaterais já depositados (Closeout Waterfall B3).

---

## 7. Invariantes Formais Provadas

O motor FL-090 audita formalmente 6 leis invariantes de integridade matemática e regulatória através do método `verify_invariants(raise_on_error=True)`:

1. **Invariante de Conservação Financeira SPI (`spi_financial_conservation`)**:
   $$\sum \Delta \text{Saldo Pagadores} + \sum \Delta \text{Saldo B3} = 0{,}00$$
   $$\text{Total Débito Pagadores} \equiv \text{Total Crédito B3}$$
   Nenhum centavo é criado ou destruído nas Contas PI do Banco Central.
2. **Invariante de Unicidade e Formato de E2EID (`e2e_id_uniqueness_and_format`)**:
   Todo `EndToEndId` gerado é estritamente único e obedece à máscara `^E\d{8}\d{12}[a-zA-Z0-9]{11}$`.
3. **Invariante de Irrevogabilidade e Não-Repúdio (`irrevocability_and_non_repudiation`)**:
   Toda liquidação em status `ACSC` gera recibo imutável e comprovante `pacs.002`, sendo irrevogável e insuscetível de cancelamento unilateral.
4. **Invariante de Consistência de Risco Pré-Trade (`risk_capacity_consistency`)**:
   A margem coberta e capacidade de risco liberada na B3 equivale estritamente ao somatório dos valores liquidados com sucesso (`ACSC`).
5. **Invariante de Conformidade de Mandato (`mandate_compliance_integrity`)**:
   Toda transação possui mandato `ACTIVE` subjacente e respeita rigorosamente $\text{Valor} \le \text{max\_limit\_per\_call}$ e $\text{Utilizado} \le \text{total\_monthly\_limit}$.
6. **Invariante de Cumprimento de SLA (`sla_latency_compliance`)**:
   Toda liquidação `ACSC` foi concluída em tempo $\le 10{,}0\text{ s}$.

---

## 8. Execução de Testes e Certificação

Todos os cenários operacionais e de estresse foram verificados com 100% de sucesso através do executor nativo da suíte B3:

```bash
python3 -m unittest tests/test_pix_automatico_margin_calls.py
```
