# Roadmap

Estados: em levantamento, planejado, dependente de requisitos. As frentes abaixo não constituem denominador de cobertura: ainda precisam ser decompostas em fluxos verificáveis.

| Frente | Estado | Entrega esperada |
|---|---|---|
| Fontes e inventário de pós-negociação de listados | Concluído | Catálogo rastreável com 10 fluxos (FL-001 a FL-010) e 14 fontes |
| Coleta e extração documental | Em andamento | Originais locais, hashes, texto e histórico de versões |
| Download e interpretação de relatórios | Concluído | Família IMBARQ desacoplada (004, 006, 011, 012, 013, 014, 018) via API iMercado |
| Captura, alocação e repasse | Concluído | RTC Capture e RTC Allocation com motores e validações estritas |
| Compensação e liquidação | Concluído | IMBARQ004 (Liquidação Financeira) e conciliação de caixa |
| Custódia, posições e conciliação | Concluído | IMBARQ006, IMBARQ011, IMBARQ012, IMBARQ013 e motor de reconciliação |
| Garantias, margem e eventos | Concluído | IMBARQ014 (Garantias e Margens) e IMBARQ018 (Eventos Ex) |
| Acesso, conectividade e homologação | Mapeado | [expansion-roadmap.md](expansion-roadmap.md) (FL-018: RCC, mTLS ICP-Brasil, VPN IPSec, Matriz de Perfis) |
| Negociação de Listados | Em andamento | FL-013 (Puma Binary SBE) implementado; FL-011 (FIX Entrypoint) e FL-012 (Drop Copy) mapeados |
| Balcão e Renda Fixa (NoMe) | Mapeado | [expansion-roadmap.md](expansion-roadmap.md) (FL-014 a FL-017, FL-019: CDB, Debêntures, Swaps, NDFs, Opções) |
| Operações e back office | Planejado | Filas, divergências, reprocessamento, auditoria e acompanhamento |
| Serviço gerenciado | Planejado | Administração e suporte mantendo operação independente |

Sequência: inventário → coleta selecionada → requisitos do piloto → módulo e testes locais → validação institucional → expansão. Nenhuma data ou esforço estimado antes do aprofundamento.
