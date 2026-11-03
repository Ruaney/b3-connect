# Mapeamento do Piloto — IMBARQ006 (Posições da Câmara B3)

## 1. Contexto e Correção Material

Historicamente, o arquivo **IMBARQ001** era utilizado como o arquivo consolidado de posições e movimentações de custódia e liquidação na B3.

Em **30/06/2024**, o arquivo **IMBARQ001 foi oficialmente descontinuado** pela B3, tendo suas responsabilidades desagregadas em uma nova família de arquivos especializados por domínio operacional:

- **IMBARQ006**: Posições em aberto da Câmara B3 (processamento noturno — foco do piloto para ativos listados).
- **IMBARQ004**: Lançamentos financeiros.
- **IMBARQ011**: Posições de empréstimo de ativos (contratos de aluguel / BTC).
- **IMBARQ012**: Posições da Depositária de listados.
- **IMBARQ013**: Movimentações da Depositária de listados.
- **IMBARQ014**: Garantias e chamadas de margem.

### Por que o IMBARQ006 é o candidato correto para o piloto?
Para instituições que precisam realizar a conciliação diária de posições de bolsa (ações, opções, futuros e derivativos listados) contra a câmara de compensação e liquidação, o **IMBARQ006** reflete as posições oficiais consolidadas ao final do dia (D+0 noturno), servindo de base de verdade para custódia, back-office e gestão de risco.

---

## 2. Jornada Ponta a Ponta de Integração

```
[Instituição]                    [iMercado API B3]               [Azure Blob Storage]
     |                                   |                                |
     |-- 1. Autenticação (OAuth/mTLS) -->|                                |
     |<- Token de Acesso (JWT) ----------|                                |
     |                                   |                                |
     |-- 2. Consulta Status/Arquivos --->|                                |
     |<- Lista de arquivos disponíveis --|                                |
     |                                   |                                |
     |-- 3. Solicitação SAS URL -------->|                                |
     |<- SAS URL pré-assinada -----------|                                |
     |                                                                    |
     |-- 4. HTTP GET com SAS URL ---------------------------------------->|
     |<- Stream do IMBARQ006 ---------------------------------------------|
     |
     |-- 5. Validação de Layout & Checksum
     |-- 6. Ingestão e Reconciliação Interna
```

### 2.1 Jornada de Autorização e Credenciamento
- **Habilitação Institucional**: Acesso condicionado à contratação da plataforma iMercado junto à B3 e registro da instituição (corretora, custodiante, gestora ou participante de liquidação).
- **Identidade e Certificação**:
  - Certificado digital padrão ICP-Brasil (tipo A1 ou corporativo) para autenticação mTLS (Mutual TLS).
  - Provisionamento de credenciais da API (`Client ID` e `Client Secret` vinculados ao participante).
  - Cadastramento de faixas de IP autorizadas (quando exigido pelas políticas de borda da B3).

### 2.2 Monitoramento de Disponibilidade Operacional
- **Janela de Geração**: O IMBARQ006 é processado na grade noturna da câmara B3 após o encerramento do pregão e fechamento das rotinas de liquidação/compensação (geralmente concluído na madrugada anterior à abertura de D+1).
- **Verificação de Disponibilidade**:
  - Consulta periódica de metadados via endpoint de catalogação de arquivos da API iMercado Conciliação para a data de referência (`data_movimento`).
  - Tratamento de estados: arquivo em processamento, arquivo pronto ou reprocessamento excepcional por ajuste da câmara.

### 2.3 Autenticação e Gestão de Sessão
- **Protocolo**: OAuth 2.0 (`client_credentials`).
- **Endpoint**: Serviço de autenticação do ecossistema de APIs da B3.
- **Validação de Token**: Obtenção de token Bearer (JWT) com expiração definida (TTL). A arquitetura deve renovar o token preventivamente antes do vencimento sem interromper jobs batch de download.

### 2.4 Obtenção da URL SAS (Shared Access Signature)
- **Requisição**: A aplicação consulta o endpoint de download da API de Conciliação informando o tipo de relatório (`IMBARQ006`), código do participante e data do movimento.
- **Payload de Resposta**: Retorna metadados do arquivo (tamanho, hash SHA-256 ou MD5, timestamp de corte) e uma **SAS URL** do Azure Blob Storage com assinatura temporária (TTL curto, ex.: 15 a 60 minutos) com permissões restritas exclusivamente a leitura (`r`).

### 2.5 Download Resiliente
- **Requisição Direta**: Download via requisição HTTP GET contra o Azure Blob Storage utilizando a SAS URL obtida.
- **Resiliência e Recuperação**:
  - Suporte a retries com backoff exponencial para falhas transitórias de conexão.
  - Se a SAS URL expirar durante retries prolongados, a aplicação deve solicitar uma nova SAS URL na API iMercado.
  - Validação de integridade do transporte através do cabeçalho `Content-Length` e verificação de checksum ao concluir a gravação do stream.

### 2.6 Validação de Layout
O IMBARQ006 é disponibilizado no formato posicional plano (flat-file de largura fixa) e exige validação estrita em três níveis:

1. **Header do Arquivo**:
   - Tipo de registro (`00`).
   - Identificação do leiaute (`IMBARQ006`).
   - Data de geração e data de movimento das posições.
   - Código da instituição participante e número sequencial de remessa.
2. **Registros de Detalhe (Posições da Câmara)**:
   - Tipo de registro (`01`).
   - Código do participante e da conta de custódia / participante de liquidação.
   - Identificação do ativo (código de negociação / Ticker e código ISIN).
   - Mercado (à vista, opções, termo, futuros).
   - Sentido da posição (Devedora / Credora ou Comprada / Vendida).
   - Quantidade bruta, quantidade em trânsito e quantidade líquida.
   - Preço de ajuste do pregão e valor financeiro de referência.
3. **Trailler do Arquivo**:
   - Tipo de registro (`99`).
   - Total de registros do arquivo (soma de headers, detalhes e trailler).
   - Somatórios de controle quantitativo e financeiro para batimento de integridade.
4. **Política de Rejeição**:
   - Rejeição imediata em caso de linhas com tamanho diferente do especificado no catálogo oficial.
   - Rejeição se o somatório do Trailler não coincidir rigorosamente com a contagem física dos detalhes processados.

### 2.7 Reconciliação Interna
- **Cruzamento Posicional**: Comparação entre o extrato de posições do IMBARQ006 e as posições registradas no livro de custódia interno da instituição para a data base.
- **Detecção de Quebras**:
  - Posição na B3 inexistente na base interna (trade pendente ou divergência cadastral).
  - Posição interna inexistente na B3.
  - Divergência quantitativa entre posição apurada na câmara e saldo interno.
- **Relatório de Conciliação**: Emissão de relatório analítico diário com classificação das inconsistências para tratamento operacional pelo back-office.

---

## 3. Critérios de Aceite do Piloto

1. **Autenticação Segura**: Suporte automatizado a OAuth 2.0 via certificados digitais sem dependência de intervenção humana em ambiente de execução.
2. **Download Confiável**: Capacidade comprovada de obter o IMBARQ006 por meio da SAS URL com tolerância a quedas de rede e verificação de integridade criptográfica.
3. **Parser Estruturado e Determinístico**:
   - Parsing sem perdas dos registros de Header, Detalhe e Trailler.
   - Conversão tipada de campos numéricos decimais, inteiros e datas sem truncamento.
   - Emissão de erro estruturado caso o layout ou somatório de trailler divirja.
4. **Testes Sintéticos de Unidade e Integração**:
   - Cobertura com fixtures sintéticas representativas de diferentes mercados (ações, opções e futuros).
   - Teste de casos de borda: arquivo vazio (apenas header/trailler), posições nulas e grandes lotes de registros.
5. **Autonomia Operacional**: O conector piloto deve ser desacoplado e rodar em ambiente isolado sem exigir softwares proprietários de terceiros.

---

## 4. Lacunas e Próximos Passos para Certificação

1. **Acesso aos Cadernos Técnicos Atualizados**:
   - Obter a especificação exata do layout campo a campo vigente do IMBARQ006 diretamente da B3 (revisão pós-2024).
2. **Ambiente de Homologação (Sandbox/Extranet B3)**:
   - Homologação de conectividade de rede (VPN ou link dedicado quando requerido).
   - Liberação de credenciais de homologação junto ao time de onboarding de participantes da B3.
3. **Roteiro Formal de Homologação**:
   - Levantamento dos casos de teste exigidos pela B3 para validação e certificação do fluxo de conciliação.
4. **Tratamento de Exceções de Mercado**:
   - Validação do comportamento do layout em dias de exercício de opções, vencimento de futuros ou distribuição de proventos relevantes.
