# Atlas de Integrações B3

Projeto em levantamento de requisitos. Público inicial: equipes de tecnologia de instituições. Recorte: pós-negociação de ativos listados.

Objetivo: tornar integrações compreensíveis e implementáveis por meio de guias, exemplos e módulos abertos, operáveis de forma independente. Uma futura oferta comercial poderá administrar essas integrações. Não há vínculo oficial com a B3.

## Entregas

- [Catálogo de fontes](catalog/sources.json): evidências oficiais e limitações de consulta.
- [Catálogo de fluxos](catalog/flows.json): inventário inicial, ainda não exaustivo.
- [Roadmap](docs/roadmap.md): frentes técnicas e de operações/back office.
- [Roadmap de Expansão Arquitetural](docs/expansion-roadmap.md): arquitetura detalhada para Negociação de Listados (Puma FIX Entrypoint, Drop Copy, Binary SBE), Balcão NoMe (Renda Fixa Privada, Derivativos de Balcão e APIs) e Conectividade (RCC, mTLS ICP-Brasil, VPN IPSec e matriz de perfis PN/PNP/PL/Custodiante/Asset).
- [Método e decisões](docs/method.md): critérios de cobertura, coleta e próximos passos.
- [Relatório inicial](docs/discovery-2026-11-03.md): achados e recomendação de piloto.
- [Mapeamento do piloto (IMBARQ006)](docs/pilot-imbarq006.md): jornada de autorização, SAS URL, download, layout, reconciliação e critérios de aceite.
- [Especificação de layout IMBARQ004](docs/layout-imbarq004.md): dicionário de dados e posições de corte para Lançamentos Financeiros de Liquidação (Header, Lançamentos e Trailler).
- [Especificação de layout IMBARQ006](docs/layout-imbarq006.md): dicionário de dados e posições de corte (Header, Posições e Trailler).
- [Especificação de layout IMBARQ011](docs/layout-imbarq011.md): dicionário de dados e posições de corte para Empréstimo de Ativos / BTC (Header, Contratos e Trailler).
- [Especificação de layout IMBARQ012](docs/layout-imbarq012.md): dicionário de dados e posições de corte para Posições e Eventos da Depositária (Header, Custódia e Trailler).
- [Especificação de layout IMBARQ014](docs/layout-imbarq014.md): dicionário de dados e posições de corte para Garantias e Chamadas de Margem (Header, Colaterais/Margem e Trailler).
- [Especificação de layout IMBARQ018](docs/layout-imbarq018.md): dicionário de dados e posições de corte para Eventos Corporativos Data Ex (Header, Eventos Societários e Trailler).
- [Especificação técnica de Captura de Operações em Bloco RTC](docs/layout-rtc-capture.md): especificação do modelo de captura de trades e block trades em tempo real (FL-007), dicionário de campos e regras de validação.
- [Especificação técnica de Alocação e Repasse RTC](docs/layout-rtc-allocation.md): especificação do ciclo de vida de alocação/give-up, campos, regra estrita de equilíbrio quantitativo e códigos de motivo.
- [Especificação técnica de Mensageria Binária PUMA SBE](docs/layout-puma-sbe.md): especificação do protocolo binário SBE (Simple Binary Encoding) para algoritmos HFT e Market Makers no PUMA Trading System (FL-013).
- [Módulo cliente API iMercado](src/imercado_client.py): cliente de integração com suporte a mTLS, OAuth 2.0, emissão de SAS URL e download resiliente via streaming com validação SHA-256.
- [Módulo IMBARQ004](src/imbarq004.py): parser determinístico posicional para lançamentos financeiros de liquidação e batimento contábil.
- [Módulo do piloto IMBARQ006](src/imbarq006.py): parser determinístico posicional flat-file com validação estrita de integridade.
- [Módulo IMBARQ011](src/imbarq011.py): parser determinístico posicional para posições de contratos de aluguel (BTC).
- [Módulo IMBARQ012](src/imbarq012.py): parser determinístico posicional para posições da depositária central (CSD) e eventos corporativos pendentes.
- [Módulo IMBARQ014](src/imbarq014.py): parser determinístico posicional para garantias depositadas e chamadas de margem (CORE).
- [Módulo IMBARQ018](src/imbarq018.py): parser determinístico posicional para eventos corporativos e societários em data ex (proventos e eventos em ações).
- [Módulo Captura de Operações em Bloco RTC](src/rtc_capture.py): validador e parser determinístico de trades e blocos em tempo real com regras estritas de integridade.
- [Motor de Alocação e Repasse RTC](src/rtc_allocation.py): motor em tempo real de validação estrita de integridade quantitativa, paridade de preços e ciclo de vida de give-up.
- [Módulo PUMA SBE Binary Codec](src/puma_sbe.py): codec de mensagens binárias de alta performance e baixa latência (NewOrderSingle, OrderCancel, ExecutionReport) com empacotamento struct Little-Endian padrão SBE.
- [Motor de conciliação](src/reconciliation.py): batimento posicional determinístico (IMBARQ006 vs livro interno) com classificação tipada de quebras.
- [Pipeline Unificado de Fechamento Diário](src/pipeline.py): orquestrador central do fechamento da grade noturna (`IMBARQ004`, `IMBARQ006`, `IMBARQ011`, `IMBARQ012`, `IMBARQ014`), executando reconciliação contra custódia interna e gerando sumário executivo (`DailyClosingSummary`) com exportação estruturada em `.to_dict()`, `.to_json()` e `.to_markdown_report()`.
- [Guia Oficial de Conectividade e Certificação](docs/certification-guide.md): roteiro técnico institucional para corretoras, gestoras e custodiantes (perfis de participante, RCC/VPN/mTLS, ICP-Brasil A1/A3, homologação CET, casos de teste formais de Conciliação e RTC, e matriz de contingência/escalonamento).
- [Scripts de Governança Multiplataforma](scripts/): validação de integridade estrita (`scripts/test_catalog.py`) e cálculo de cobertura (`scripts/get_coverage.py`) em Python puro com suporte a CLI (`argparse`), JSON e Markdown.
- [Fixtures de teste](fixtures/): amostras sintéticas realistas (ações, opções, futuros, empréstimo BTC, custódia CSD, garantias/margem, eventos corporativos data ex, captura RTC, lotes de alocação RTC e payloads binários SBE) e cenários de erro.
- [Testes automatizados](tests/): 646 testes cobrindo parsing (IMBARQ004, 006, 011, 012, 014, 018), captura e alocação RTC, PUMA SBE/FIX 4.4, SPB STR/LDL0001, portabilidade STVM, CVM 175, CPR Agro, BSM Surveillance, Tesouro Direto, Futuros BM&F, Ofertas CVM 160, BDRs, ETFs, CBIO RenovaBio, Central de Garantias, Buy-In, Opções Listadas, Derivativos Swap/NDF, Termo de Ações, Opções Flexíveis, Units, OPA, DMA/PMM/OTR, LIGs, CORE Intradiário, Duplicatas Escriturais, Leilão de Frações, Tarifação Integrada B3, CDB/RDB, Cascata de Inadimplência, Ordens Avançadas PUMA, Debêntures Incentivadas, COEs, Leilão de Fechamento PUMA, Câmbio PvP e DREX/TPFt.

## Estado

Cobertura do inventário v1.2.0: **61 fluxos cadastrados, 100% documentados, implementados e testados localmente**. A suíte conta com **646 testes unitários automatizados com 100% de sucesso** e **45 Leis Formais de Invariante e Conservação Financeira** formalmente comprovadas via `scripts/verify_invariants.py`. Todos os módulos de software foram desenvolvidos com zero dependências externas (exclusivamente Python Standard Library).

Execute `python3 scripts/test_catalog.py --check-files`, `python3 scripts/get_coverage.py` e `python3 scripts/verify_invariants.py` para verificar a integridade, cobertura e consistência matemática.

## 💼 Suporte Corporativo & Plataforma Gerenciada B3 Enterprise

Para instituições financeiras (corretoras PNP/PN, distribuidoras DTVM, gestoras de recursos, bancos de investimento, agtechs e fintechs) que demandam **conectores de produção gerenciados**, **suporte de engenharia sênior 24/7 com SLA contratual**, **gateway de risco pré-trading em sub-milissegundo (<80µs)** e **consultoria assistida de homologação junto à B3 e Banco Central**:

👉 **[B3 Enterprise Platform — Conectores Gerenciados, Suporte 24/7 e Homologação B3](https://b3-enterprise-platform.vercel.app/#contato)**  
*(Acesse a plataforma institucional, simule seu ROI e solicite contato comercial com SLA contratual).*

## Publicação

O **B3 Integration Atlas** é mantido como iniciativa aberta e de referência técnica para a comunidade. A plataforma corporativa, o gateway de produção e os serviços gerenciados com SLA são operados de forma privada e segregada sob a B3 Enterprise Platform.

