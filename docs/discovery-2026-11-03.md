# Levantamento inicial — 2026-11-03

## Achados

O [índice do iMercado](https://www.b3.com.br/pt_br/solucoes/plataformas/middle-e-backoffice/imercado/documentos/) reúne relatórios, referências técnicas e uma entrada para automação de download. É a primeira trilha documental para o piloto. Os detalhes de acesso e layouts ainda precisam de revisão.

A página [Captura em bloco](https://clientes.b3.com.br/w/captura-em-bloco) identifica o RTC como componente de pós-negociação e lista catálogos, schemas e materiais de certificação. As datas dos anexos não comprovam vigência atual. O acesso aos arquivos não foi testado.

O [painel de atualizações do iMercado](https://b3.com.br/pt_br/solucoes/plataformas/middle-e-backoffice/imercado/painel-de-atualizacoes/) fornece sinais de evolução de layouts e funcionalidades. Deve compor a trilha de acompanhamento de mudanças; não substitui a leitura da especificação vigente.

## Atualização material: descontinuação do IMBARQ001 e recorte do piloto

O arquivo **IMBARQ001** (historicamente empregado para posições consolidadas de pós-negociação) foi **descontinuado pela B3 em 30/06/2024**, sendo substituído pela família de arquivos desagregados por especialidade:

- **IMBARQ006**: Posições em aberto da Câmara B3 (processamento noturno — foco do piloto de listados).
- **IMBARQ004**: Lançamentos financeiros.
- **IMBARQ011**: Empréstimo de ativos (contratos de aluguel / BTC).
- **IMBARQ012**: Posições da Depositária de listados.
- **IMBARQ013**: Movimentações da Depositária de listados.
- **IMBARQ014**: Garantias e chamadas de margem.

Essa correção altera a premissa de que haveria um arquivo único de posições: a conciliação de posições de bolsa da câmara passa a ser atendida especificamente pelo **IMBARQ006** (FL-002) em conjunto com a automação de download via API iMercado (FL-001). O detalhamento da jornada, critérios de aceite e lacunas de certificação está consolidado em [docs/pilot-imbarq006.md](pilot-imbarq006.md).

## Recomendação para o piloto

Aprofundar primeiro download e interpretação de posições (FL-001 + FL-002 via IMBARQ006), antes de comandos transacionais. Resultado esperado: instituição obtém o relatório autorizado via SAS URL gerada pela API iMercado e interpreta seus registros estruturados (header, detalhes e trailler) com fixtures sintéticas e validação estrita. Isso não resolve sozinho alocação, repasse ou liquidação transacional.

## Lacunas

- Elegibilidade, contratação, autenticação (mTLS/OAuth) e ambientes por categoria de participante.
- Últimas versões e cadernos técnicos oficiais pós-2024 dos layouts IMBARQ006.
- Catálogos atuais de RTC e interfaces efetivamente expostas ao participante.
- Calendários, janelas de processamento noturno, recuperação de falhas e regras de expiração de SAS token.
- Mapa de fontes fora do iMercado/RTC e entrevistas com tecnologia e operações.

## Resultado desta rodada

Cinco fontes registradas: três páginas consultadas e duas fontes localizadas. Dez fluxos candidatos mapeados (incluindo os desdobramentos da divisão do IMBARQ001). Mapeamento técnico inicial do piloto documentado em `docs/pilot-imbarq006.md`. Zero fluxos implementados, testados localmente com código ou validados em ambiente B3. Descoberta do ecossistema permanece incompleta.
