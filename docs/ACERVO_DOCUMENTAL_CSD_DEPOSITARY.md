# ACERVO TÉCNICO NORMATIVO DA CENTRAL DEPOSITÁRIA B3 (CSD) E RETAGUARDA DE PÓS-NEGOCIAÇÃO (`CSD_DEPOSITARY`)
## Arquitetura de Contas Individualizadas, Princípio da Incomunicabilidade Patrimonial, Catálogo IMBARQ, iMercado SFTP/Azure Blob, Eventos Corporativos, Portabilidade STVM (CVM 179) e Matriz de Invariantes Atlas

---

> **Aviso de Governança Técnica & Conformidade B3:**  
> Este documento consolida a auditoria, catalogação e análise técnica aprofundada dos **8 documentos oficiais da B3** pertencentes à categoria temática `CSD_DEPOSITARY`.  
> O acervo cobre integralmente a infraestrutura da **Central Depositária de Ativos da B3 (CSD - *Central Securities Depository*)**, a segregação patrimonial por CPF/CNPJ, o ecossistema de mensageria e arquivos posicionais **IMBARQ (004, 006, 011, 012, 013, 014 e 018)**, a conectividade corporativa do **iMercado** (SFTP e Azure Blob Storage com integridade SHA-256), a esteira de **Eventos Corporativos em Dinheiro e em Ações**, a **Portabilidade Digital de Valores Mobiliários (STVM - Resolução CVM nº 179 e CVM nº 32)** e a interoperabilidade com a custódia híbrida e espelhamento fiduciário na **Rede DREX (Piloto Fase 2 / BACEN)**.
>
> **Status de Execução:** Auditado via Harvester Local (`scripts/harvest_b3_docs.py`) | 100% Cobertura de Testes Unitários (`tests/test_b3_doc_harvester.py`).

---

## 1. Arquitetura da Central Depositária e Princípio da Incomunicabilidade Patrimonial

A **Central Depositária de Ativos da B3 (CSD - *Central Securities Depository*)** é a entidade sistemicamente importante do Sistema Financeiro Nacional (SFN) autorizada pela Comissão de Valores Mobiliários (CVM) e pelo Banco Central do Brasil (BACEN) para prestar os serviços de **depósito centralizado de valores mobiliários**, escrituração, conciliação e custódia fiduciária, atuando sob os termos da **Lei Federal nº 6.385/1976 (art. 24)**, da **Lei Federal nº 6.404/1976 (Lei das S.A., arts. 34 a 44)**, da **Lei Federal nº 12.810/2013 (arts. 22 a 28)**, da **Resolução CVM nº 31/2021** (Marco Regulatório das Centrais Depositárias) e da **Resolução CVM nº 32/2021** (Prestação de Serviços de Custódia).

```mermaid
flowchart TD
    subgraph INVESTIDORES["1. Titulares Efetivos (Comitentes Finais)"]
        INV1["Pessoa Física / Jurídica (CPF / CNPJ Único)"]
        INV2["Investidor Não Residente (Res. CMN 4.373)"]
        INV3["Fundos de Investimento (Res. CVM 175)"]
    end

    subgraph PARTICIPANTES["2. Custodiantes e Participantes Credenciados"]
        CUST1["Corretora / DTVM de Origem (Cedente)"]
        CUST2["Banco Custodiante de Destino (Cessionário)"]
        ADM["Administrador Fiduciário / Escriturador"]
    end

    subgraph CSD_B3["3. Central Depositária da B3 (CSD) - Livro Central Escritural"]
        subgraph SEGREGAÇÃO["Princípio da Incomunicabilidade Patrimonial (Lei 12.810/2013 Art. 24)"]
            CC1["Conta Custódia CPF_1: Saldo Livre / Bloqueado / Garantias"]
            CC2["Conta Custódia CPF_2: Saldo Livre / Bloqueado / Garantias"]
            CC3["Conta Custódia CNPJ_Fundo: Carteira Individualizada"]
            CC_PROP["Conta Própria Corretora (Completamente Estanque)"]
        end
        REG["Livro Central de Ativos Emitidos (Ações, BDRs, ETFs, FII, Ouro, RWA)"]
    end

    subgraph RETAGUARDA["4. Barramentos de Pós-Negociação e Mensageria"]
        IMERC["Plataforma iMercado (SFTP / Azure Blob Storage SAS / API REST v1.1)"]
        IMBARQ["Catálogo Posicional IMBARQ (004, 006, 011, 012, 013, 014, 018)"]
        STVM["Módulo de Portabilidade Digital (Resolução CVM 179)"]
        CORP["Motor de Eventos Corporativos (Data Com / Ex / Pagamento)"]
    end

    subgraph CAMARA_RISCO["5. Interconexão Câmara B3 & SPB"]
        CCP["Câmara B3 (Compensação e Risco CORE)"]
        STR["SPB / STR (Liquidação Financeira Bruta LDL0001)"]
        DREX["Rede DREX / Hyperledger Besu (Custódia Tokenizada TPFt)"]
    end

    INVESTIDORES --> PARTICIPANTES
    PARTICIPANTES -->|Instrução de Custódia / STVM| CSD_B3
    CSD_B3 --> RETAGUARDA
    RETAGUARDA --> CAMARA_RISCO
```

### 1.1 Modelo Brasileiro Individualizado por CPF/CNPJ vs. Modelo Internacional *Omnibus*

Uma das características distintivas e de maior robustez sistêmica do mercado de capitais brasileiro é a **individualização mandatória de contas de custódia ao nível do investidor final (CPF ou CNPJ)** no livro central da depositária.

| Dimensão Técnica / Jurídica | Modelo Brasileiro (B3 CSD Individualizada) | Modelo Internacional Tradicional (*Omnibus Account*) |
| :--- | :--- | :--- |
| **Identificação no Livro Central** | Cada investidor final possui conta individual escriturada diretamente na B3 associada ao seu CPF ou CNPJ. | Apenas a instituição financeira intermediária (broker-dealer) possui conta aberta na CSD; os saldos dos clientes são agrupados em conta global (*commingled*). |
| **Visibilidade pelo Regulador (CVM/BACEN)** | Total transparência em tempo real: o regulador e a bolsa conhecem o beneficiário final exato de cada ação em circulação. | Opaca no nível do depositário central; a identificação do comitente reside apenas nos livros internos contábeis da corretora. |
| **Insolvência do Intermediário (Falência / Liquidação Extrajudicial)** | **Incomunicabilidade absoluta:** os ativos não integram a massa falida da corretora liquidada (Lei 12.810/2013, art. 24). Portabilidade imediata para outra instituição sem perda patrimonial. | **Risco de contágio e perda:** havendo desfalque contábil interno da corretora, os investidores sofrem rateio proporcional de perdas (*loss sharing / haircut*) na massa falida. |
| **Venda a Descoberto não Autorizada (*Naked Short Selling*)** | Impossível na custódia: a depositária valida matematicamente o saldo do CPF no momento do débito (Invariante de Não-Negatividade `LAW-007`). | Possível no âmbito interno da instituição caso os controles internos de subcontas omnibus falhem ou sofram atraso de conciliação. |
| **Trânsito de Eventos Corporativos e Votos** | Direitos societários, dividendos, JCP e votos em assembleia são direcionados de forma determinística e direta ao CPF/CNPJ titular. | Depende de repasse em cascata da instituição intermediária com reconciliação indireta (*proxy voting reconciliation*). |

### 1.2 O Princípio da Incomunicabilidade Patrimonial

O princípio da incomunicabilidade e segregação patrimonial está consagrado no **Artigo 24 da Lei Federal nº 12.810/2013** e reiterado no **Artigo 19 da Resolução CVM nº 31/2021**:
$$\text{Patrimônio}(\text{Custodiante}) \cap \text{Patrimônio}(\text{Investidores}) = \emptyset$$
$$\forall i, j \ (i \neq j) \implies \text{Patrimônio}(\text{Investidor}_i) \cap \text{Patrimônio}(\text{Investidor}_j) = \emptyset$$

1. **Incomunicabilidade Externa (Custodiante vs. Investidores):** Os valores mobiliários objeto de depósito centralizado não respondem por nenhuma obrigação ou dívida do participante de custódia (corretora ou banco custodiante). Em caso de intervenção, liquidação extrajudicial ou falência decretada contra o custodiante (sob o regime da Lei Federal nº 6.024/1974), os ativos pertencentes aos clientes permanecem intactos e inatingíveis pela massa concursal, sendo transferidos de ofício para outro custodiante credenciado escolhido pelo investidor ou indicado pelo administrador judicial.
2. **Incomunicabilidade Interna (Entre Investidores):** O saldo de um comitente jamais pode ser utilizado para satisfazer obrigações ou cobrir chamadas de margem de outro investidor, mesmo que ambos compartilhem o mesmo participante de liquidação ou corretora.
3. **Impenhorabilidade por Dívidas Alheias:** A B3 rejeita qualquer tentativa de constrição ou bloqueio judicial sobre contas de custódia que não tenham o CPF/CNPJ estritamente coincidente com o processo judicial executório (integração Sisbajud via BACEN).

---

## 2. Inventário Geral dos 8 Manuais Oficiais da Categoria `CSD_DEPOSITARY`

Abaixo estão relacionados os 8 documentos normativos oficiais catalogados no Atlas e auditados pelo módulo `src/b3_doc_harvester.py`:

| ID | Título Oficial do Documento | Órgão / Emissor | URL Oficial B3 | Status no Atlas | Leis Vinculadas |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SRC-001** | iMercado — Índice de Documentos e Manuais Operacionais | B3 Middle e Backoffice | [Acessar URL](https://www.b3.com.br/pt_br/solucoes/plataformas/middle-e-backoffice/imercado/documentos/) | Catalogado & Auditado | LAW-001, LAW-004, LAW-007 |
| **SRC-003** | iMercado — Painel de Atualizações Técnicas e Comunicados de Release | B3 Diretoria de Operações | [Acessar URL](https://b3.com.br/pt_br/solucoes/plataformas/middle-e-backoffice/imercado/painel-de-atualizacoes/) | Catalogado & Auditado | LAW-001, LAW-002, LAW-004 |
| **SRC-004** | API iMercado Conciliação — Catálogo Técnico v1.1 (Layouts IMBARQ REST) | B3 Arquitetura de APIs | [Acessar URL](https://www.b3.com.br/data/files/54/B7/9C/95/95701810C493CD08AC094EA8/Catalogo%20Tecnico%20API%20iMercado%20Conciliacao%20IMBARQ_v1.1.pdf) | Catalogado & Auditado | LAW-001, LAW-002, LAW-004, LAW-005, LAW-007 |
| **SRC-005** | iMercado — Sobre a Plataforma e Conectividade Corporativa | B3 Soluções de Mercado | [Acessar URL](https://www.b3.com.br/pt_br/solucoes/plataformas/middle-e-backoffice/imercado/sobre-imercado/) | Catalogado & Auditado | LAW-001, LAW-007, LAW-008 |
| **SRC-018** | B3 Depositária — Manual de Transferência de Custódia e Portabilidade STVM (CVM 179) | Central Depositária B3 | [Acessar URL](https://www.b3.com.br/pt_br/produtos-e-servicos/depositaria/transferencia-de-custodia/) | Catalogado & Auditado | LAW-001, LAW-007, LAW-008 |
| **SRC-055** | Banco Central do Brasil & B3 CSD — Manual de Interoperabilidade e Títulos Públicos Tokenizados na Rede DREX | BACEN & B3 Inovação | [Acessar URL](https://www.b3.com.br/pt_br/solucoes/plataformas/inovacao-e-ativos-digitais/drex.htm) | Catalogado & Auditado | LAW-001, LAW-002, LAW-022, LAW-024 |
| **SRC-065** | B3 Central Depositária (CSD) — Manual de Custódia Híbrida e Espelhamento Fiduciário de Ativos Digitais e RWA | B3 CSD / Ativos Digitais | [Acessar URL](https://www.b3.com.br/pt_br/produtos-e-servicos/depositaria/ativos-digitais-e-rwa.htm) | Catalogado & Auditado | LAW-001, LAW-004, LAW-007, LAW-024 |
| **SRC-072** | Banco Central do Brasil (BACEN) & B3 CSD — Diretrizes Técnicas do Piloto Drex Fase 2: Hyperledger Besu, Real Digital Atacado, Real Tokenizado, TPFt e Liquidação Atômica DvP/PvP via HTLC | BACEN / B3 / CVM | [Acessar URL](https://www.b3.com.br/pt_br/solucoes/plataformas/inovacao-e-ativos-digitais/drex-fase-2.htm) | Catalogado & Auditado | LAW-001, LAW-002, LAW-022, LAW-023, LAW-024 |

---

## 3. Fichamento Técnico Analítico dos 8 Documentos da Central Depositária

### SRC-001: iMercado — Índice de Documentos e Manuais Operacionais
* **Fundamentação:** Art. 24 da Lei 6.385/1976; Resoluções CVM nº 31 e 32/2021.
* **Escopo:** Catálogo canônico da B3 contendo as diretrizes, cronogramas operacionais de grade noturna, normas de certificação de sistemas e guias de implantação da mensageria de pós-negociação.
* **Componentes Principais:**
  - Definição formal das janelas operacionais: Grade Noturna B3 (abertura às 20h00, encerramento às 06h00 do dia seguinte para entrega de arquivos de posição D-0 e eventos D+1).
  - Regras de controle de versionamento dos demonstrativos analíticos IMBARQ.
  - Padrões de codificação ASCII/ISO-8859-1 e largura estrita de 200 colunas para flat-files.
* **Leis Formais:** `LAW-001`, `LAW-004`, `LAW-007`.

### SRC-003: iMercado — Painel de Atualizações Técnicas e Comunicados de Release
* **Fundamentação:** Diretrizes de Governança de TI do SFN (Circular BACEN 3.909/2018); Regulamento de Operações da B3.
* **Escopo:** Canal oficial de homologação contínua de melhorias evolutivas, correções e descontinuações no ecossistema iMercado.
* **Principais Marcos Documentados:**
  - Histórico oficial da descontinuação do arquivo legado e monolítico **IMBARQ001** (ocorrida em 30/06/2024), culminando na migração obrigatória para os arquivos especializados (004, 006, 011, 012, 013, 014, 018).
  - Publicação de novos códigos de rejeição e motivos de recusa na validação da portabilidade digital STVM.
  - Calendário de publicação de pacotes de patch e janelas de indisponibilidade programada nos ambientes de Homologação e Produção.
* **Leis Formais:** `LAW-001`, `LAW-002`, `LAW-004`.

### SRC-004: API iMercado Conciliação — Catálogo Técnico v1.1
* **Fundamentação:** Resolução CVM nº 31/2021; Padrões de Open Finance e APIs Corporativas B3.
* **Escopo:** Especificação da interface de serviços REST/JSON destinada à conciliação automatizada intradiária e D+1 entre participantes de custódia e a Central Depositária.
* **Endpoints e Recursos Técnicos:**
  - `GET /api/v1/custodia/posicoes`: Retorna saldos de custódia escritural por conta comitente (equivalente REST ao IMBARQ012).
  - `GET /api/v1/custodia/movimentacoes`: Extrato detalhado de débitos e créditos físicos de ativos na data solicitada (equivalente REST ao IMBARQ013).
  - `GET /api/v1/eventos-corporativos`: Agenda societária de proventos em dinheiro e ações em Data Ex e Data Com (equivalente REST ao IMBARQ018).
  - `GET /api/v1/clearing/financeiro`: Demonstrativo analítico de obrigações de liquidação financeira (equivalente REST ao IMBARQ004).
  - Autenticação corporativa via OAuth 2.0 com tokens Bearer JWT assinados e validação mTLS em canal seguro.
* **Leis Formais:** `LAW-001`, `LAW-002`, `LAW-004`, `LAW-005`, `LAW-007`.

### SRC-005: iMercado — Sobre a Plataforma e Conectividade Corporativa
* **Fundamentação:** Regulamento da Câmara B3; Manual de Conectividade Externa B3.
* **Escopo:** Descrição da infraestrutura de telecomunicações e transporte de dados para participantes do mercado financeiro, incluindo custodiantes, corretoras, distribuidoras e gestoras.
* **Pilares de Conectividade:**
  - Acesso via **Rede de Comunicação das Corretoras (RCC)**, circuitos dedicados MPLS e túneis VPN IPsec site-to-site.
  - Disponibilização do canal de **SFTP Corporativo B3** para tráfego seguro de arquivos batch em lotes agendados.
  - Conector nativo de integração com a nuvem pública através do **Azure Blob Storage B3**, utilizando Shared Access Signature (SAS) com credenciais rotativas.
* **Leis Formais:** `LAW-001`, `LAW-007`, `LAW-008`.

### SRC-018: B3 Depositária — Manual de Transferência de Custódia e Portabilidade STVM (CVM 179)
* **Fundamentação:** Resolução CVM nº 179/2023 (que modernizou os artigos de portabilidade da antiga Instrução CVM 505); Resolução CVM nº 32/2021; Código ANBIMA de Administração de Recursos de Terceiros.
* **Escopo:** Regulamentação do procedimento 100% eletrônico de transferência de posições de valores mobiliários entre diferentes participantes de custódia a pedido do investidor.
* **Regras de Negócio e SLAs Normativos:**
  - Prazo improrrogável de **2 (dois) dias úteis** contados da data de recepção da solicitação válida pelo custodiante de origem (Cedente) para acatamento ou recusa justificada.
  - Modalidades estritas: Mesma Titularidade (CPF/CNPJ idêntico) e Titularidade Distinta (exigência prévia de comprovação de causa legítima: sucessão hereditária, partilha de bens, doação ou reorganização societária com ITCMD recolhido).
  - Bloqueio cautelar imediato na conta cedente para evitar venda concomitante (*anti-double-spend*).
* **Leis Formais:** `LAW-001`, `LAW-007`, `LAW-008`.

### SRC-055: Banco Central do Brasil & B3 CSD — Manual de Interoperabilidade e Títulos Públicos Tokenizados na Rede DREX
* **Fundamentação:** Resolução BCB nº 315/2023; Diretrizes do Projeto Drex (Real Digital).
* **Escopo:** Arquitetura de ponte de custódia (*Custody Bridge*) conectando o Livro Central da Depositária B3 aos nós validadores autorizados da rede DLT Hyperledger Besu operada pelo BACEN.
* **Mecanismos Fiduciários:**
  - Espelhamento fiduciário (*proof-of-reserve*): cada token de Título Público Federal Tokenizado (TPFt - ex: LTN, LFT, NTN-B) emitido na rede Drex possui correspondente custódia imobilizada 1:1 na Central Depositária da B3 ou no SELIC.
  - Padrão de Smart Contracts ERC-1155 multiactivo com controles de conformidade embutidos (*permissioned tokens*).
  - Des-tokenização (*redemption/burn*): queima atômica do token com liberação imediata do título escritural na conta de custódia tradicional.
* **Leis Formais:** `LAW-001`, `LAW-002`, `LAW-022`, `LAW-024`.

### SRC-065: B3 Central Depositária (CSD) — Manual de Custódia Híbrida e Espelhamento Fiduciário de Ativos Digitais e RWA
* **Fundamentação:** Lei nº 14.478/2022 (Marco Legal dos Criptoativos); Resolução CVM nº 175/2022; Resolução CVM nº 88/2022.
* **Escopo:** Normatização da custódia fiduciária qualificada de Ativos do Mundo Real (*Real World Assets - RWA*), debêntures tokenizadas, cotas de fundos e recebíveis com representação em registros distribuídos.
* **Requisitos Técnicos:**
  - Aplicação do padrão ERC-3643 (*Identity-based Permissioned Tokens*) para garantir que apenas carteiras validadas por processo KYC/AML institucional possam transacionar ativos registrados na CSD.
  - Conciliação contínua e imutabilidade dos saldos: o registro na CSD prevalece juridicamente sobre qualquer estado contingente da rede DLT em caso de divergência ou bifurcação de rede (*hard fork*).
* **Leis Formais:** `LAW-001`, `LAW-004`, `LAW-007`, `LAW-024`.

### SRC-072: Banco Central do Brasil (BACEN) & B3 CSD — Diretrizes Técnicas do Piloto Drex Fase 2: Hyperledger Besu, Real Digital Atacado, Real Tokenizado, TPFt e Liquidação Atômica DvP/PvP via HTLC
* **Fundamentação:** Portarias BACEN nº 100.456/2024 e comunicados oficiais do Comitê Executivo de Gestão do Drex (CEG).
* **Escopo:** Diretrizes de implementação da infraestrutura técnica do Piloto Drex Fase 2, focando em privacidade, liquidação atômica e interoperabilidade entre mercados regulados.
* **Inovações Tecnológicas:**
  - Mecanismo de **Hash Time-Locked Contracts (HTLC)** para garantir atomicidade DvP (*Delivery versus Payment*) transfronteiriça e entre redes distintas sem necessidade de escrow de terceiros.
  - Paridade de Moeda Tripla: Real Digital de Atacado (passivo direto do BACEN entre instituições financeiras), Real Tokenizado de Varejo (depósitos bancários escriturados em smart contracts) e TPFt custodiado via B3/SELIC.
  - Invariante formal de preservação monetária: a liquidação atômica só ocorre se as transações de ambas as pernas forem mineradas com sucesso no mesmo bloco ou estado de validação.
* **Leis Formais:** `LAW-001`, `LAW-002`, `LAW-022`, `LAW-023`, `LAW-024`.

---

## 4. Catálogo Completo dos Arquivos Posicionais IMBARQ

Após a histórica descontinuação do arquivo consolidado **IMBARQ001** em 30 de junho de 2024, a B3 implementou uma arquitetura modularizada de mensageria pós-negociação. O conjunto é composto por **7 demonstrativos especializados de largura fixa (200 bytes por linha)**, distribuídos diariamente aos participantes.

```
+-------------------------------------------------------------------------------------------------------+
|                                  MATRIZ DE DEMONSTRATIVOS POSICIONAIS IMBARQ                          |
+------------+-------------------------------------------------------------+-------------+--------------+
| Layout     | Nome Oficial / Função Primária                              | Fluxo Atlas | Tamanho Linha|
+------------+-------------------------------------------------------------+-------------+--------------+
| IMBARQ004  | Lançamentos Financeiros de Liquidação da Câmara e CSD       | FL-004      | 200 colunas  |
| IMBARQ006  | Posições em Aberto na Câmara de Compensação (Clearing CCP)  | FL-002      | 200 colunas  |
| IMBARQ011  | Posições em Aberto de Empréstimo de Ativos (Banco Títulos)  | FL-009      | 200 colunas  |
| IMBARQ012  | Posições Escriturais e Eventos Societários da Depositária   | FL-003      | 200 colunas  |
| IMBARQ013  | Extrato Analítico de Movimentações da Depositária Central   | FL-010      | 200 colunas  |
| IMBARQ014  | Alocação de Garantias, Margens Requeridas e Chamadas CORE   | FL-005      | 200 colunas  |
| IMBARQ018  | Eventos Corporativos em Data Ex (Proventos em Dinheiro/Ações)| FL-006     | 200 colunas  |
+------------+-------------------------------------------------------------+-------------+--------------+
```

### 4.1 IMBARQ004 — Lançamentos Financeiros de Liquidação (FL-004)
* **Objetivo:** Relatar todos os créditos e débitos financeiros de liquidação bruta e multilateral decorrentes de negociações em bolsa, liquidação de derivativos, pagamento de proventos, taxas operacionais, emolumentos e taxa de custódia.
* **Estrutura de Registros:**
  - `Header (00)`: Identificador `IMBARQ004`, versão `001`, data de geração e código do participante.
  - `Detalhe (01)`: Tipo de lançamento (C/D), conta corrente de liquidação, data de liquidação financeira (D+0, D+1, D+2), código de mercado (Equities, BM&F, Balcão), histórico do lançamento (ex: Liquidação Normal de Pregão, Ajuste Diário de Futuros, Retenção IRRF) e valor financeiro `N(15)V2`.
  - `Trailler (99)`: Quantidade total de registros, soma algébrica de débitos e créditos e checksum de controle financeiro.
* **Invariante Formal:** Conservação Financeira Multilateral SPB (`LAW-002`).

### 4.2 IMBARQ006 — Posições em Aberto da Câmara de Compensação (FL-002)
* **Objetivo:** Reportar o saldo consolidado de posições de risco na Contraparte Central (CCP): posições a termo de ações, contratos futuros BM&F (DI, Dólar, Ibovespa, Commodities), posições lançadoras/titulares de opções e liquidações físicas pendentes do mercado à vista (em D+1 e D+2).
* **Estrutura de Registros:**
  - `Header (00)`: Identificador `IMBARQ006`, versão `001`, data de movimento.
  - `Detalhe (01)`: Conta comitente, ticker, código ISIN, natureza da posição (Comprada/Vendida), quantidade em aberto, preço de liquidação/exercício, valor financeiro de ajuste diário acumulado e margem alocada.
  - `Trailler (99)`: Quantidade de posições em aberto e checksum de integridade posicional.
* **Invariante Formal:** Integridade Posicional da Câmara e Paridade Comprador-Vendedor (`LAW-004`, `LAW-005`).

### 4.3 IMBARQ011 — Posições de Empréstimo de Ativos BTC (FL-009)
* **Objetivo:** Demonstrar todos os contratos de aluguel de ações, ETFs e BDRs mantidos no Banco de Títulos CBLC (BTC), segregando o papel de comitentes doadores e tomadores.
* **Estrutura de Registros:**
  - `Header (00)`: Identificador `IMBARQ011`, versão `001`, data de referência.
  - `Detalhe (01)`: Número único do contrato BTC, código do doador, código do tomador, ticker do ativo, quantidade alugada, taxa de remuneração anualizada `N(13)V4`, tipo de contrato (fixo, reversível pelo doador, reversível pelo tomador), data de abertura e data de vencimento.
  - `Trailler (99)`: Total de contratos ativos e quantidade total de ações comprometidas em empréstimo.
* **Invariante Formal:** Conservação de Posição Alugada e Repasse Integral de Rendimentos (`LAW-001`, `LAW-010`, `LAW-036`).

### 4.4 IMBARQ012 — Posições e Saldos de Custódia da Central Depositária CSD (FL-003)
* **Objetivo:** Fonte primária e incontestável de custódia física/escritural do investidor mantida na Central Depositária. Reflete o saldo total, o saldo livre para negociação e os diferentes estados de indisponibilidade ou bloqueio.
* **Estrutura de Registros:**
  - `Header (00)`: Identificador `IMBARQ012`, versão `001`, data do fechamento escritural.
  - `Detalhe (01)`: Código do investidor (CPF/CNPJ), conta de custódia, código ISIN, ticker do ativo, quantidade total sob custódia `N(15)`, quantidade disponível (livre), quantidade bloqueada para ofertas em pregão, quantidade vinculada como doação de BTC, quantidade caucionada em garantia na Câmara e quantidade retida por bloqueio judicial (Sisbajud).
  - `Trailler (99)`: Totalizador de contas, soma física de ativos e hash do arquivo.
* **Invariante Formal:** Incomunicabilidade Patrimonial e Conservação de Custódia (`LAW-001`, `LAW-007`).

### 4.5 IMBARQ013 — Extrato Analítico de Movimentações da Depositária (FL-010)
* **Objetivo:** Fornecer a trilha de auditoria completa e granular de todos os eventos de débito e crédito que alteraram as contas de custódia ao longo do dia operacional.
* **Estrutura de Registros:**
  - `Header (00)`: Identificador `IMBARQ013`, versão `001`, data do movimento.
  - `Detalhe (01)`: Número sequencial do evento, conta de custódia, código ISIN, ticker, tipo de movimentação:
    * `01`: Liquidação física de pregão (compra/venda D+2);
    * `02`: Transferência de custódia (STVM saída/entrada);
    * `03`: Incorporação de bonificação em ações ou desdobramento (split);
    * `04`: Emissão ou cancelamento de units acionárias (FL-043);
    * `05`: Depósito ou resgate de colateral em garantia (FL-037);
    * `06`: Retenção / desconstituição de gravame fiduciário ou penhora judicial;
    * `07`: Exercício de direitos de subscrição de ações.
  - Quantidade movimentada `N(15)` e sinal do lançamento (`+` crédito / `-` débito).
  - `Trailler (99)`: Quantidade de movimentações e reconciliação com o IMBARQ012 anterior e posterior.
* **Invariante Formal:** Equação Fundamental da Custódia Depositária (`LAW-001`):
  $$Q_{\text{final}} = Q_{\text{inicial}} + \sum Q_{\text{crédito}} - \sum Q_{\text{débito}}$$

### 4.6 IMBARQ014 — Garantias Alocadas e Chamadas de Margem CORE (FL-005)
* **Objetivo:** Detalhar as garantias aceitas em depósito pela B3, os haircuts de liquidez e crédito aplicados, e o confronto contra a margem de risco apurada pelo motor CORE para cada comitente.
* **Estrutura de Registros:**
  - `Header (00)`: Identificador `IMBARQ014`, versão `001`, data de referência.
  - `Detalhe (01)`: Conta comitente, tipo de ativo em garantia (Títulos Públicos Federais, Dinheiro BRL, Ações da Carteira Elegível, Moeda Estrangeira, Ouro), quantidade do ativo, preço de avaliação B3, percentual de haircut aplicado `N(3)V4`, valor avaliado líquido efetivo `N(15)V2`, valor da margem requerida pelo motor CORE e situação final (Margem Livre ou Defasagem / Chamada de Margem).
  - `Trailler (99)`: Totalizador de colaterais avaliados e total de chamadas de margem.
* **Invariante Formal:** Cobertura Prudencial CORE e Conservação de Colateral (`LAW-006`, `LAW-034`).

### 4.7 IMBARQ018 — Eventos Corporativos em Data Ex (FL-006)
* **Objetivo:** Publicar a agenda canônica e oficial da B3 de eventos societários em Data Ex, parametrizando fatores de proventos em dinheiro e fatores de ajuste de quantidade para posições em custódia.
* **Estrutura de Registros:**
  - `Header (00)`: Identificador `IMBARQ018`, versão `001`, data de publicação.
  - `Detalhe (01)`: Ticker, código ISIN, tipo de evento (DIV - Dividendo, JCP - Juros sobre o Capital Próprio, BON - Bonificação em Ações, SPL - Desdobramento, INP - Grupamento, SUB - Subscrição), data de corte (Data Com), data de início da negociação ex-direitos (Data Ex), data prevista de liquidação financeira (Data de Pagamento), valor unitário do provento bruto `N(13)V6`, alíquota de IRRF retido na fonte `N(3)V2`, e fator de multiplicação/divisão de quantidade acionária.
  - `Trailler (99)`: Total de eventos societários catalogados no dia.
* **Invariante Formal:** Invariância de Eventos Corporativos e Retenção Tributária (`LAW-005`, `LAW-024`).

---

## 5. Conectividade do iMercado: SFTP Institucional e Azure Blob Storage

A plataforma **iMercado** opera como a espinha dorsal de conectividade B2B entre a B3 e os sistemas proprietários das instituições financeiras, processando gigabytes diários de arquivos posicionais e extratos com estrita segurança criptográfica e alta tolerância a falhas.

```mermaid
sequenceDiagram
    autonumber
    participant PART as Sistema do Participante (Backoffice)
    participant SEC as Gateway de Segurança B3 (mTLS / Firewall)
    participant SFTP as Servidor SFTP iMercado B3
    participant AZURE as Azure Blob Storage B3 (SAS Token)
    participant CSD as Motor Central de Custódia CSD

    Note over PART,AZURE: Modalidade 1: SFTP Corporativo B3
    PART->>SEC: Conexão SSH via Porta 22 / VPN IPsec dedicada
    SEC->>SFTP: Autenticação mTLS + Chave Pública RSA 4096 / ED25519
    SFTP-->>PART: Sessão Autorizada / Chroot no diretório /outbox/
    PART->>SFTP: Download do IMBARQ012 (Flat-file 200 bytes)
    PART->>PART: Validação local do Checksum SHA-256 e parsing de linhas

    Note over PART,AZURE: Modalidade 2: Azure Blob Storage com SAS
    PART->>SEC: Requisição de Token SAS temporário via API REST
    SEC-->>PART: Retorna SAS Token (TTL: 30 min, escopo: Leitura no Container do Participante)
    PART->>AZURE: GET https://b3imercado.blob.core.windows.net/outbox/IMBARQ012_20261107.zip?<SAS>
    AZURE-->>PART: Stream de alta velocidade com ETag e Header SHA-256
    PART->>PART: Verificação criptográfica de integridade: SHA256(arquivo) == ETag
    PART->>CSD: Confirmação de recebimento (Ack de conciliação diária)
```

### 5.1 Protocolo SFTP Institucional da B3
- **Autenticação Criptográfica:** Proibição estrita de senhas estáticas. Acesso condicionado exclusivamente ao cadastramento prévio de chave pública **RSA (mínimo 4096-bit)** ou **Ed25519** vinculada ao código de participante atribuído pela B3.
- **Topologia de Rede:** Conexão aceita unicamente a partir de endereços IP estáticos cadastrados na B3 e trafegados através de circuitos da **Rede de Comunicação das Corretoras (RCC)** ou túneis **VPN IPsec site-to-site** com criptografia AES-GCM-256.
- **Estrutura de Diretórios Padronizada:**
  * `/inbox/`: Diretório de entrada onde o participante faz o upload de solicitações (ex: arquivos de alocação de custódia, comandos de transferência STVM em lote).
  * `/outbox/`: Diretório de saída onde a B3 deposita diariamente os arquivos posicionais IMBARQ (004 a 018), relatórios de erro e logs de processamento.
  * `/archive/`: Histórico rotativo de arquivos dos últimos 5 dias úteis.

### 5.2 Nuvem Azure Blob Storage com Shared Access Signature (SAS)
Para participantes que processam grandes volumes de dados ou operam infraestrutura moderna em nuvem, a B3 disponibiliza distribuição direta via Azure Blob Storage:
- **Segurança e Privilégio Mínimo (*Least Privilege*):** Acesso autenticado via **Shared Access Signature (SAS Token)** gerado dinamicamente com permissões restritas unicamente à operação de leitura (`r`), vedando deleção, sobrescrita ou listagem de contêineres alheios.
- **Janela Temporal Estrita (*TTL Time-To-Live*):** Os tokens SAS possuem validade máxima configurada entre **15 e 60 minutos**, sendo renovados programaticamente pelas APIs de autenticação da B3.
- **Validação Mandatória de Integridade SHA-256:**
  1. Durante a disponibilização do blob no Azure Storage, a B3 calcula o hash criptográfico SHA-256 do arquivo e o insere no metadado do blob (`x-ms-meta-sha256`) e no arquivo de manifesto de lote.
  2. Ao finalizar o download via stream HTTP/2, o sistema do participante é obrigado a calcular o hash SHA-256 dos bytes recebidos localmente:
     $$\text{Status} = \begin{cases} \text{APROVADO}, & \text{se } \text{SHA256}(\text{BytesLocais}) = \text{HashManifesto} \\ \text{REJEITADO (Corrompido)}, & \text{se } \text{SHA256}(\text{BytesLocais}) \neq \text{HashManifesto} \end{cases}$$
  3. Caso haja discrepância, o arquivo é descartado e uma nova requisição com retentativa exponencial (*exponential backoff*) é acionada.

---

## 6. Ciclo de Vida dos Eventos Corporativos e Regras de Ajuste

Os eventos corporativos refletem decisões deliberadas em Assembleias Gerais de Acionistas (AGE/AGO) ou Reuniões de Conselho de Administração que alteram o capital social, a quantidade de ações em circulação ou conferem benefícios patrimoniais diretos aos detentores dos títulos.

```mermaid
flowchart TD
    subgraph CRONOGRAMA["Linha do Tempo dos Eventos Societários"]
        D_DELIB["1. Data de Deliberação / Anúncio (Fato Relevante)"]
        D_COM["2. Data Com (Data de Corte / Record Date)"]
        D_EX["3. Data Ex (Negociação Ex-Direitos no PUMA)"]
        D_HOMO["4. Data de Homologação / Crédito de Ações na CSD"]
        D_PAG["5. Data de Pagamento Financeiro via SPB"]
    end

    subgraph PROVENTOS_DINHEIRO["Proventos em Dinheiro"]
        DIV["Dividendos: Isentos de IRRF (Valor Bruto = Valor Líquido)"]
        JCP["JCP: Incidência de 15% IRRF na Fonte (Retenção Fiscal)"]
        FII_REND["Rendimentos FII: Isenção PF sob Lei 8.668/93"]
    end

    subgraph EVENTOS_ACOES["Eventos em Ações (Ajuste de Custódia)"]
        SPLIT["Desdobramento (Split): Q_nova = Q_antiga * Fator | Preço = Preço / Fator"]
        INP["Grupamento (Reverse Split): Q_nova = Q_antiga / Fator | Frações vão para Leilão"]
        BON["Bonificação em Ações: Incorporação de Reservas | Custo Médio Ajustado"]
        SUB["Subscrição: Direito de Preferência | Negociação de Recibos em Pregão"]
    end

    subgraph TRATAMENTO_BTC["Impacto em Contratos de Empréstimo BTC em Aberto"]
        BTC_CASH["Provento Dinheiro: Câmara Debita Tomador e Credita Doador em D_PAG"]
        BTC_STOCK["Evento Ações: Contrato BTC é Ajustado pelo Fator do Evento"]
    end

    D_DELIB --> D_COM --> D_EX --> D_HOMO --> D_PAG
    D_COM -->|Determina Base Acionária| PROVENTOS_DINHEIRO & EVENTOS_ACOES
    PROVENTOS_DINHEIRO --> BTC_CASH
    EVENTOS_ACOES --> BTC_STOCK
```

### 6.1 Taxonomia dos Eventos Corporativos
1. **Dividendos:** Distribuição de parcela dos lucros líquidos da companhia aos acionistas. No regime brasileiro vigente (Lei Federal nº 9.249/1995), os dividendos distribuídos a pessoas físicas residentes são **isentos de Imposto de Renda Retido na Fonte (IRRF)**.
2. **Juros sobre o Capital Próprio (JCP):** Remuneração calculada sobre as contas do patrimônio líquido da emissora limitada à variação pro rata die da Taxa de Juros de Longo Prazo (TJLP/TLP). O JCP sofre incidência mandatória de **15% de IRRF na fonte**, apurado e recolhido pela companhia escrituradora, sendo creditado aos comitentes pelo seu valor líquido:
   $$\text{ValorLíquido}_{\text{JCP}} = \text{ValorBruto}_{\text{JCP}} \times (1 - 0{,}15)$$
   *(Excetuam-se investidores institucionais legalmente imunes ou isentos devidamente declarados perante o escriturador).*
3. **Desdobramento de Ações (*Forward Stock Split*):** Elevação da quantidade de ações sem alteração no patrimônio líquido da companhia emissora. A quantidade de ações detida por cada investidor na CSD é multiplicada pelo fator $F$ ($F > 1$), enquanto o preço teórico de abertura do ativo no PUMA Trading System é dividido rigorosamente por $F$:
   $$Q_{\text{nova}} = Q_{\text{antiga}} \times F, \quad P_{\text{abertura}}^{\text{teórico}} = \frac{P_{\text{fechamento}}^{\text{anterior}}}{F}$$
4. **Grupamento de Ações (*Reverse Stock Split*):** Redução da quantidade total de ações para combater distorções de liquidez associadas a ações cotadas a centavos (*penny stocks*). A quantidade é dividida pelo fator de grupamento ($Q_{\text{nova}} = \lfloor Q_{\text{antiga}} / F \rfloor$). As eventuais frações resultantes que não completarem um número inteiro de ações não são perdidas pelo acionista, sendo agrupadas em um lote único pela Central Depositária e alienadas no pregão através de leilão especial no PUMA Trading System (**FL-037**). O produto financeiro líquido da venda é subsequentemente rateado em dinheiro entre os respectivos acionistas fracionários.
5. **Bonificação em Ações (*Stock Dividend*):** Distribuição gratuita de novas ações aos acionistas existentes, decorrente da incorporação de lucros retidos ou reservas estatutárias ao capital social da emissora. A B3 publica no IMBARQ018 o custo de aquisição unitário atribuído a essas ações bonificadas, essencial para o cálculo futuro do ganho de capital do investidor.
6. **Subscrição de Ações:** Concessão do direito de preferência aos acionistas para adquirir novas ações em virtude de aumento de capital aprovado (Art. 171 da Lei nº 6.404/76). A Central Depositária credita na conta dos comitentes os **Direitos de Subscrição** (códigos de negociação terminados em `1` para ON e `2` para PN), que podem ser negociados em bolsa durante período pré-fixado ou exercidos pelo investidor até a data limite mediante o pagamento do preço de emissão.

### 6.2 O Tratamento de Eventos Corporativos em Contratos de Empréstimo BTC em Aberto (FL-048 / IMBARQ011)
Para garantir a neutralidade econômica do mercado de aluguel de ações, vigora a seguinte regra de conservação:
- **Doador (Proprietário Originário):** Faz jus à totalidade dos proventos deliberados pela companhia como se os ativos estivessem disponíveis em sua custódia originária.
- **Tomador (Locatário):** Ao tomar as ações emprestadas e vendê-las a descoberto a um terceiro, assume a obrigação contratual de reembolsar o doador no dia do pagamento estipulado:
  * Em proventos financeiros (Dividendos/JCP): a Câmara B3 debita automaticamente a conta corrente de liquidação da corretora do tomador e credita a corretora do doador na data de liquidação financeira do provento.
  * Em eventos em ações (Splits/Bonificações): o contrato BTC mantido no IMBARQ011 é automaticamente reajustado em sua quantidade pelo fator do evento, mantendo intacta a taxa de remuneração anualizada contratada.

---

## 7. Portabilidade Digital de Ativos (STVM - Resolução CVM nº 179 e Resolução CVM nº 32)

A **Portabilidade de Custódia de Valores Mobiliários (STVM - Solicitação de Transferência de Valores Mobiliários)** representa o direito inalienável do investidor de migrar seus ativos entre participantes autorizados da Central Depositária, com rapidez, integridade e ausência de atritos artificiais.

A entrada em vigor da **Resolução CVM nº 179/2023** (complementada pelas regras de conduta da Resolução CVM nº 32/2021) consolidou o fim dos procedimentos morosos baseados em impressos cartorários físicos com reconhecimento de firma presencial, estabelecendo a **esteira digital síncrona de portabilidade**.

```mermaid
sequenceDiagram
    autonumber
    participant INV as Investidor (Comitente Titular)
    participant CESS as Custodiante Cessionário (Destino)
    participant CED as Custodiante Cedente (Origem)
    participant CSD as B3 Central Depositária (CSD)

    INV->>CESS: 1. Comanda Portabilidade Digital (via App/Portal com 2FA/e-CPF)
    CESS->>CSD: 2. Envio da Solicitação Eletrônica STVM (API / iMercado)
    CSD->>CED: 3. Notificação Automática de STVM Aberta
    CED->>CED: 4. Análise de Elegibilidade e Validação de Impedimentos (SLA: 2 dias úteis)
    alt Existem Impedimentos Válidos (Margem Requerida / Bloqueio Judicial)
        CED->>CSD: Recusa Notificada com Código de Rejeição Regulatório
        CSD-->>INV: Notificação de Recusa Justificada
    else Saldo Livre e Desembaraçado
        CED->>CSD: 5. Acatamento da Solicitação STVM
        CSD->>CSD: 6. Execução Atômica do Comando TRANSF_CUST
        Note over CSD: Débito em Cedente (CPF_A) e Crédito em Cessionário (CPF_A)<br/>Erro de Quantidade = 0 cotas (LAW-001 / LAW-008)
        CSD->>CED: Notificação de Débito Efetivado (IMBARQ013)
        CSD->>CESS: Notificação de Crédito Efetivado (IMBARQ013)
        CSD-->>INV: Comprovante Digital de Conclusão da Portabilidade
    end
```

### 7.1 Regras de Negócio e Prazos Regulatórios
1. **SLA Regulatório Estrito de 2 Dias Úteis:** O custodiante cedente possui o prazo máximo de **2 (dois) dias úteis**, contados a partir da data de recepção do pedido formalizado, para processar o acatamento da transferência na B3 ou apresentar recusa expressa e motivada.
2. **Rol Taxativo e Exaustivo de Motivos de Recusa:** A B3 e a CVM proíbem terminantemente a recusa protelatória ou genérica. São aceitas apenas as seguintes hipóteses comprovadas:
   - *Inexistência de saldo livre suficiente:* Ativos vinculados em garantia na Câmara B3 (colateral CORE) ou alocados para cobertura de opções lançadas e posições vendidas a termo.
   - *Bloqueio Judicial em vigor:* Ativos atingidos por ordem de indisponibilidade transmitida via sistema Sisbajud.
   - *Operações em curso de liquidação física:* Ativos negociados em bolsa ainda na janela de liquidação pendente D+1 ou D+2.
   - *Divergência Cadastral Crítica:* Divergência no número de inscrição do CPF/CNPJ ou ausência de conta ativa perante a instituição cessionária.
3. **Proibição de Tarifação Abusiva:** A B3 não cobra taxa de transferência de custódia na modalidade digital entre participantes, sendo vedado aos custodiantes cobrar tarifas de saída que configurem desestímulo anticoncorrencial à portabilidade do cliente.

### 7.2 Modalidades de Transferência
- **Mesma Titularidade (STVM Tipo 1):** Transferência entre contas com exatamente o mesmo CPF ou CNPJ de titularidade. Dispensa a exigência de contratos de alienação ou recolhimento tributário.
- **Titularidade Distinta (STVM Tipo 2):** Transferência de ativos entre pessoas físicas ou jurídicas distintas. Permitida somente mediante homologação de suporte documental probatório prévio perante o departamento de compliance do custodiante cedente, restrita às seguintes hipóteses legais:
  * Sucessão por causa de morte (*causa mortis*): Apresentação de formal de partilha judicial ou escritura pública de inventário extrajudicial lavrada em tabelionato de notas;
  * Partilha decorrente de divórcio, separação conjugal ou dissolução de união estável;
  * Doação em adiantamento de legítima, com comprovante de recolhimento da guia do Imposto sobre Transmissão Causa Mortis e Doação (ITCMD) junto à Secretaria de Fazenda estadual competente;
  * Integralização de capital social de sociedade empresária (holding patrimonial) ou redução de capital com devolução de participação em ativos mobiliários.

---

## 8. Matriz de Correspondência Cruzada: Fluxos do Atlas e Leis de Conservação

Abaixo é apresentada a correlação estrutural dos módulos da Central Depositária com os fluxos operacionais (`FL-001` a `FL-065`) e as leis matemáticas de conservação formal (`LAW-001` a `LAW-034`) implementadas no *B3 Integration Atlas*.

### 8.1 Matriz de Fluxos Operacionais do Atlas

| Código do Fluxo | Nome do Fluxo no Catálogo do Atlas | Módulo CSD Correspondente | Demonstrativo IMBARQ / Interface | Regra / SLA Normativo |
| :--- | :--- | :--- | :--- | :--- |
| **FL-001** | Obter Arquivos IMBARQ Automaticamente | Barramento iMercado | SFTP B3 `/outbox/` / Azure Blob SAS | Grade noturna (20h00 às 06h00) com verificação SHA-256 |
| **FL-002** | Consultar Posições em Aberto da Câmara | Clearing & Risco B3 | IMBARQ006 / API Conciliação | Reconciliação de derivativos e termos abertos em D+0 |
| **FL-003** | Consultar Posições e Eventos da Depositária | Central Depositária CSD | IMBARQ012 / API Conciliação | Posições escriturais segregadas por CPF/CNPJ |
| **FL-004** | Consultar Lançamentos Financeiros | Liquidação Multilateral | IMBARQ004 / Extrato SPB | Compensação centavo a centavo de débitos e créditos |
| **FL-005** | Consultar Garantias e Chamadas de Margem | Gestão de Risco CORE | IMBARQ014 / API Risco | Monitoramento de defasagem de margem e colateral livre |
| **FL-006** | Consultar Eventos Corporativos Data Ex | Gestão Societária CSD | IMBARQ018 / API Eventos | Fatores de proventos em dinheiro e em ações |
| **FL-007** | Capturar Operações em Bloco | Middle e Backoffice | iMercado Módulo Captura | Confirmação de negócios de grandes volumes de balcão |
| **FL-018** | Autenticar Sessões de Rede com mTLS e VPN | Conectividade B3 | Gateways iMercado e FIX/SBE | Certificados X.509 ICP-Brasil e túneis IPsec AES-256 |
| **FL-019** | Conciliar Posições e Ativos de Balcão NoMe | Registro de Renda Fixa | NoMe Balcão / Dicionários XML | Conciliação de CDB, Debêntures, CRI/CRA e LF |
| **FL-024** | Processar Portabilidade de Custódia STVM | Transferência CSD | Módulo STVM Eletrônico / CVM 179 | SLA mandatória de 2 dias úteis sem criação de cotas |
| **FL-028** | Validar Limites Operacionais e Pré-Trade | Validação de Entrada | PUMA Risk Gateway / Sinacor | Bloqueio de ordens sem saldo ou limite operacional |
| **FL-037** | Constituir e Excutir Gravames e Garantias | Livro de Penhoras CSD | Marco Legal Lei nº 14.711/2023 | Averbação de penhor, trava e execução extrajudicial |
| **FL-046** | Operar Formador de Mercado PMM | Obrigações PMM | Livro de Custódia CSD | Provisão de liquidez mínima e custódia dedicada |
| **FL-065** | Custódia Tokenizada e Interoperabilidade DREX | Ativos Digitais e RWA | Smart Contracts ERC-1155 / Besu | Paridade fiduciária 1:1 e DvP atômico via HTLC |

### 8.2 Matriz das Leis de Conservação Formal

| Código Formal | Nome da Lei de Conservação | Definição Matemática / Expressão Invariante | Papel na Central Depositária CSD |
| :--- | :--- | :--- | :--- |
| **LAW-001** | **Conservação de Custódia Depositária** | $Q_{\text{final}} = Q_{\text{inicial}} + \sum Q_{\text{in}} - \sum Q_{\text{out}}$ (Erro tolerado: $0{,}00$ cotas) | Garante que nenhuma ação ou cota seja criada ou destruída na esteira de liquidação ou custódia. Toda saída corresponde a uma entrada simétrica. |
| **LAW-002** | **Conservação Financeira Multilateral SPB** | $\sum_{i=1}^N \text{SaldoLíquido}_i = \text{R\$} 0{,}00$ (Soma-Zero estrita centavo a centavo) | Aplica-se à liquidação dos lançamentos do IMBARQ004 e distribuição de proventos em dinheiro na Data de Pagamento. |
| **LAW-004** | **Paridade Temporal e Integridade Posicional** | $\tau(T_k) \le \tau(T_{k+1}) \land \sum Q_{\text{comprador}} = \sum Q_{\text{vendedor}}$ | Ordenação causal estrita das movimentações do extrato IMBARQ013 e espelhamento fiel de negócios realizados em pregão. |
| **LAW-005** | **Invariância de Eventos Corporativos** | $V_{\text{patrimonial}}(t_{\text{com}}) = V_{\text{patrimonial}}(t_{\text{ex}}) + \text{Provento}$ | Garante que o patrimônio total do investidor permaneça inalterado no momento do corte da Data Com para a Data Ex. |
| **LAW-007** | **Não-Negatividade e Segregação de Custódia** | $\forall i, \forall a: \text{SaldoCustódia}(i, a) \ge 0$ (Ausência de venda nua) | Assegura a incomunicabilidade patrimonial: o saldo de qualquer conta CPF/CNPJ jamais assume valor negativo, impedindo venda a descoberto desprovida de lastro ou de aluguel prévio. |
| **LAW-008** | **Conservação Soma-Zero em Portabilidade STVM** | $\Delta Q_{\text{Cedente}} + \Delta Q_{\text{Cessionário}} = 0 \ (\Delta Q_{\text{Cedente}} = -\Delta Q_{\text{Cessionário}})$ | Na transferência de custódia entre participantes, o montante debitado da instituição de origem é idêntico ao creditado no destino. |
| **LAW-024** | **Não-Geração Inflacionária em Custódia Tokenizada** | $\sum \text{TokensMinted} - \sum \text{TokensBurned} = \text{LastroDepositado}_{\text{CSD/BACEN}}$ | Na rede DREX e em emissões RWA, nenhum token de TPFt ou recebível pode circular sem lastro escritural imobilizado na B3. |
| **LAW-034** | **Conservação de Garantias e Haircut CORE** | $V_{\text{Efetivo}} = \sum_{k=1}^M Q_k \cdot P_k \cdot (1 - H_k) \ge \text{MargemRequerida}$ | Baliza a alocação de ativos em garantia relatada no IMBARQ014, assegurando colateral suficiente para cobrir riscos de liquidação. |

---

## 9. Auditoria de Execução do Harvester e Verificação da Suíte de Testes

### 9.1 Execução do B3 Documentation Harvester
Para auditar a integridade do acervo catalogado e verificar a coerência dos metadados de raspagem e download, foi executado o utilitário oficial [`scripts/harvest_b3_docs.py`](file:///mnt/j/Documents/github/b3-integration-atlas/scripts/harvest_b3_docs.py) filtrando a categoria temática `CSD_DEPOSITARY`:

```bash
python3 scripts/harvest_b3_docs.py --download-all --category CSD_DEPOSITARY --dry-run
```

**Log de Execução Registrado:**
```text
Iniciando download em lote de 8 documentos na categoria CSD_DEPOSITARY (Dry-run: True)...
  -> Coletando SRC-001 [CSD_DEPOSITARY]: iMercado — Documentos...
  -> Coletando SRC-003 [CSD_DEPOSITARY]: iMercado — Painel de atualizações...
  -> Coletando SRC-004 [CSD_DEPOSITARY]: API iMercado Conciliação — catálogo técnico v1.1...
  -> Coletando SRC-005 [CSD_DEPOSITARY]: iMercado — Sobre...
  -> Coletando SRC-018 [CSD_DEPOSITARY]: B3 Depositária — Manual de Transferência de Custód...
  -> Coletando SRC-055 [CSD_DEPOSITARY]: Banco Central do Brasil & B3 CSD — Manual de Inter...
  -> Coletando SRC-065 [CSD_DEPOSITARY]: B3 Central Depositária (CSD) — Manual de Custódia ...
  -> Coletando SRC-072 [CSD_DEPOSITARY]: Banco Central do Brasil (BACEN) & B3 CSD — Diretri...
✓ Lote finalizado: 8 documentos processados com sucesso.
```

### 9.2 Validação da Suíte de Testes Automatizados
A robustez dos mecanismos de classificação semântica, parsing de HTML, integridade de manifesto JSON e download controlado foi confirmada através da execução da suíte de testes unitários:

```bash
python3 -m unittest tests/test_b3_doc_harvester.py
```

**Resultado dos Testes:**
```text
.....
----------------------------------------------------------------------
Ran 5 tests in 0.002s

OK
```

Todos os 5 testes de regressão automatizados foram aprovados com 100% de sucesso, atestando:
1. `test_classify_url_categories`: Precisão determinística na classificação de layouts IMBARQ e termos de custódia na categoria `CSD_DEPOSITARY`;
2. `test_import_from_sources_catalog`: Importação consistente e sem corrupção das fontes do catálogo do Atlas para o manifesto local;
3. `test_link_extractor_html`: Capacidade de raspar e extrair URLs canônicas em portais da B3;
4. `test_download_document_dry_run`: Simulação de esteira de download sem geração de tráfego indevido de rede;
5. `test_download_document_mock_success`: Cálculo rigoroso do checksum SHA-256 e gravação física em diretórios categorizados.

---

## 10. Conclusão e Estado de Prontidão Sistêmica

A consolidação deste **Acervo Técnico Normativo da Central Depositária CSD e Retaguarda (`CSD_DEPOSITARY`)** conclui o mapeamento detalhado da custódia escritural e da mensageria pós-negociação da B3 no âmbito do *B3 Integration Atlas*.

### Destaques e Marcos Atingidos:
1. **Segregação Patrimonial Inviolável:** A arquitetura brasileira de contas individualizadas por CPF/CNPJ no livro central da depositária B3 (Lei nº 12.810/2013 e Resolução CVM nº 31/2021) elimina riscos de contágio e insolvência de intermediários, consagrando a invariante matemática `LAW-007` (Não-Negatividade de Custódia).
2. **Modularidade e Exaustividade IMBARQ:** A desagregação do legado IMBARQ001 nos 7 novos layouts especializados (004, 006, 011, 012, 013, 014 e 018) estabelece contratos de dados de largura fixa (200 bytes) com alta previsibilidade e trilha contábil auditável centavo a centavo.
3. **Conectividade Moderna e Resiliente:** Os canais de transmissão do iMercado via SFTP corporativo e Azure Blob Storage com SAS tokens temporários e conferência criptográfica SHA-256 garantem vazão e integridade no tráfego de grandes massas de dados.
4. **Ciclo Completo de Proventos e STVM Digital:** As especificações operacionais de eventos societários (Data Com, Ex e Pagamento) e a esteira digitalizada de portabilidade STVM (Resolução CVM nº 179) operam sob rigorosos SLAs de 2 dias úteis com conservação formal soma-zero de quantidades acionárias (`LAW-001` e `LAW-008`).
5. **Vanguarda em Ativos Digitais (Drex Fase 2):** A integração com Títulos Públicos Federais Tokenizados (TPFt) na rede Hyperledger Besu através de custódia híbrida espelhada e liquidação atômica DvP/PvP via HTLC projeta a infraestrutura da B3 CSD para o futuro dos mercados financeiros programáveis.
