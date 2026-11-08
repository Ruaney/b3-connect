"""System, Simulation, Concepts & Phase 7 Paths for OpenAPI 3.1."""

def get_system_paths():
    p = {}
    p["/api/status"] = {
        "get": {
            "tags": ["System & Diagnostics"],
            "summary": "Status do Atlas, contagem de fluxos homologados e leis de conservação",
            "description": "Retorna os metadados da versão do Atlas, total de fluxos ponta a ponta homologados (107), leis formais de conservação (91), módulos de mercado (7) e sumarização dos manuais oficiais indexados.",
            "operationId": "getSystemStatus",
            "responses": {
                "200": {"description": "Status operacional do B3 Integration Atlas", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SystemStatusResponse"}}}},
                "500": {"$ref": "#/components/responses/InternalError"}
            }
        }
    }
    p["/api/health"] = {
        "get": {
            "tags": ["System & Diagnostics"],
            "summary": "Healthcheck e disponibilidade do servidor",
            "description": "Sonda de liveness e readiness do servidor web local, atestando execução 100% autônoma.",
            "operationId": "getHealthCheck",
            "responses": {
                "200": {"description": "Servidor em perfeita integridade operacional", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HealthResponse"}}}}
            }
        }
    }
    p["/api/modules"] = {
        "get": {
            "tags": ["System & Diagnostics"],
            "summary": "Listagem dos 7 módulos da arquitetura de mercado financeiro B3",
            "description": "Retorna as definições estruturais dos 7 pilares canônicos da B3: PUMA Trading, Câmara & Risco CORE, Central Depositária (CSD), Balcão NoMe, SPB/BACEN/Drex, Tarifação e Supervisão BSM.",
            "operationId": "getArchitectureModules",
            "responses": {
                "200": {"description": "Lista dos 7 módulos arquiteturais", "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/ArchitectureModule"}}}}}
            }
        }
    }
    p["/api/docs"] = {
        "get": {
            "tags": ["System & Diagnostics"],
            "summary": "Catálogo de documentação e manuais oficiais B3",
            "description": "Lista os 80+ manuais normativos, regulamentos de compensação, catálogos de mensagens e especificações técnicas indexadas no Atlas.",
            "operationId": "listOfficialDocs",
            "responses": {
                "200": {"description": "Lista de metadados dos manuais e especificações oficiais", "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/DocumentMetadata"}}}}}
            }
        }
    }
    p["/api/docs/{doc_id}"] = {
        "get": {
            "tags": ["System & Diagnostics"],
            "summary": "Obtém documento oficial e manual normativo na íntegra",
            "description": "Recupera os metadados e o conteúdo textual/markdown bruto de um manual normativo oficial da B3 indexado localmente.",
            "operationId": "getSingleDoc",
            "parameters": [{"$ref": "#/components/parameters/DocIdPathParam"}],
            "responses": {
                "200": {"description": "Documento oficial recuperado com sucesso", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DocumentDetailResponse"}}}},
                "404": {"$ref": "#/components/responses/NotFound"}
            }
        }
    }
    p["/api/simulate-day"] = {
        "get": {
            "tags": ["Market Simulation & Chaos"],
            "summary": "Executa simulação determinística de dia de mercado completo (8 fases, D+2)",
            "description": "Executa a jornada operacional diária completa do mercado de capitais brasileiro: Ingestão de garantias Triparty, pré-abertura, leilão de abertura, negociação contínua, leilão de fechamento MOC/LOC, compensação multilateral e margens CORE, liquidação financeira no STR/BACEN e liquidação atômica Drex Fase 2.",
            "operationId": "simulateFullMarketDayGet",
            "parameters": [
                {"name": "shares", "in": "query", "required": False, "description": "Quantidade base de ações", "schema": {"type": "integer", "default": 100000, "minimum": 100}},
                {"name": "base_price", "in": "query", "required": False, "description": "Preço base de referência (BRL)", "schema": {"type": "number", "default": 35.50}},
                {"name": "notional_balcao", "in": "query", "required": False, "description": "Nocional em BRL (NoMe)", "schema": {"type": "number", "default": 10000000.00}}
            ],
            "responses": {
                "200": {"description": "Resultado detalhado da simulação", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DailySimulationResult"}}}},
                "400": {"$ref": "#/components/responses/BadRequest"}
            }
        },
        "post": {
            "tags": ["Market Simulation & Chaos"],
            "summary": "Dispara simulação de pregão institucional com payload parametrizado",
            "description": "Permite configurar parâmetros corporativos detalhados para simulação em lote de pregão diário e estresse de liquidação multilateral.",
            "operationId": "simulateFullMarketDayPost",
            "requestBody": {
                "required": False,
                "content": {"application/json": {"schema": {"type": "object", "properties": {"shares": {"type": "integer", "default": 100000}, "base_price": {"type": "number", "default": 35.50}, "notional_balcao": {"type": "number", "default": 10000000.00}}}}}
            },
            "responses": {
                "200": {"description": "Resultado da simulação diária", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DailySimulationResult"}}}}
            }
        }
    }
    p["/api/chaos/{scenario}"] = {
        "get": {
            "tags": ["Market Simulation & Chaos"],
            "summary": "Executa cenário de caos e estresse financeiro sistêmico",
            "description": "Submete os motores da B3 a estresses severos: acionamento de Circuit Breaker Nível 1 & 2 no PUMA, Default de Membro de Compensação e cascata de salvaguardas (Waterfall), Conflito de gravames e ônus em recebíveis de cartões, ou Interrupção do barramento SPB/BACEN.",
            "operationId": "runChaosScenarioGet",
            "parameters": [{"$ref": "#/components/parameters/ScenarioPathParam"}],
            "responses": {
                "200": {"description": "Execução do cenário com auditoria de leis de conservação", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ChaosSimulationResult"}}}},
                "400": {"$ref": "#/components/responses/BadRequest"}
            }
        },
        "post": {
            "tags": ["Market Simulation & Chaos"],
            "summary": "Injeta estresse sistêmico com parâmetros avançados",
            "description": "Execução programada de cenário de caos financeiro para homologação contínua de resiliência e failover.",
            "operationId": "runChaosScenarioPost",
            "parameters": [{"$ref": "#/components/parameters/ScenarioPathParam"}],
            "responses": {
                "200": {"description": "Resultado da auditoria de resiliência ao estresse", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ChaosSimulationResult"}}}}
            }
        }
    }
    p["/api/phase7/{flow_id}"] = {
        "get": {
            "tags": ["Phase 7 Advanced Flows"],
            "summary": "Executa e audita fluxos quantitativos avançados da Fase 7 (FL-101 a FL-105)",
            "description": "Executa os motores canônicos da Fase 7: FL-101 (Superfícies SABR/SVI e ausência de arbitragem), FL-102 (COE Capital Protegido e payoff), FL-103 (Formador de Mercado PMM e obrigação de presença), FL-104 (Algorithmic SOR TWAP/VWAP/POV) e FL-105 (Emissão Primária RWA Drex Fase 2 DvP).",
            "operationId": "getPhase7Flow",
            "parameters": [{"$ref": "#/components/parameters/FlowIdPathParam"}],
            "responses": {
                "200": {
                    "description": "Fluxo quantitativo executado e invariantes verificadas",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["flow", "title", "laws"],
                                "properties": {
                                    "flow": {"type": "string", "example": "FL-101"},
                                    "title": {"type": "string", "example": "Superfícies SABR/SVI"},
                                    "laws": {"type": "array", "items": {"type": "string"}, "example": ["LAW-107A", "LAW-107B"]}
                                },
                                "additionalProperties": True
                            }
                        }
                    }
                },
                "400": {"$ref": "#/components/responses/BadRequest"}
            }
        }
    }
    p["/api/tla-check"] = {
        "get": {
            "tags": ["Formal Verification TLA+"],
            "summary": "Executa verificação formal TLA+ do Barramento Central de Liquidez",
            "description": "Executa o verificador formal de modelo para CentralLiquidityBus.tla, demonstrando matematicamente TypeOK, conservação contábil zero-sum (LAW-106A), inviolabilidade da ordem do waterfall (LAW-106B), atomicidade entre sistemas (LAW-106C) e ausência de deadlocks.",
            "operationId": "runTlaModelCheckGet",
            "responses": {
                "200": {"description": "Relatório formal com invariantes provadas", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TLAModelCheckReport"}}}}
            }
        },
        "post": {
            "tags": ["Formal Verification TLA+"],
            "summary": "Dispara checagem formal TLA+ com profundidade parametrizada",
            "description": "Executa a verificação matemática formal do barramento de liquidação sob demanda.",
            "operationId": "runTlaModelCheckPost",
            "responses": {
                "200": {"description": "Relatório formal de verificação matemática", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TLAModelCheckReport"}}}}
            }
        }
    }
    p["/api/concepts"] = {
        "get": {
            "tags": ["Educational Concepts"],
            "summary": "Lista todos os conceitos complexos e pedagógicos catalogados da B3",
            "description": "Retorna o catálogo completo de conceitos analíticos do mercado de capitais: Novação CCP, Default Waterfall, Risco CORE, Multilateral Netting, MOC/LOC, Túneis de Preço, Central de Gravames, Drex DvP e SABR/SVI.",
            "operationId": "listConcepts",
            "responses": {
                "200": {"description": "Catálogo completo de conceitos analíticos B3", "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/B3ConceptSummary"}}}}}
            }
        }
    }
    p["/api/concepts/{concept_id}"] = {
        "get": {
            "tags": ["Educational Concepts"],
            "summary": "Obtém o fichamento detalhado e a formulação matemática de um conceito B3",
            "description": "Fornece explicação profunda: O que é, Por que existe, Como funciona, Regra matemática/fórmula, Exemplo prático real, Diagrama ASCII, Leis de conservação associadas e Fluxos relacionados.",
            "operationId": "getConceptById",
            "parameters": [{"$ref": "#/components/parameters/ConceptIdPathParam"}],
            "responses": {
                "200": {"description": "Fichamento analítico completo do conceito", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/B3ConceptDetail"}}}},
                "404": {"$ref": "#/components/responses/NotFound"}
            }
        }
    }
    p["/api/concepts/{concept_id}/simulate"] = {
        "get": {
            "tags": ["Educational Concepts"],
            "summary": "Executa simulação quantitativa e matemática do conceito via Query Params",
            "description": "Calcula os efeitos operacionais, contratos novados, payoffs de opções ou redistribuição de margens em tempo real com parâmetros informados via URL.",
            "operationId": "simulateConceptGet",
            "parameters": [
                {"$ref": "#/components/parameters/ConceptIdPathParam"},
                {"name": "quantity", "in": "query", "required": False, "schema": {"type": "integer", "default": 10000}},
                {"name": "price", "in": "query", "required": False, "schema": {"type": "number", "default": 38.50}},
                {"name": "symbol", "in": "query", "required": False, "schema": {"type": "string", "default": "PETR4"}}
            ],
            "responses": {
                "200": {"description": "Resultado detalhado da simulação quantitativa", "content": {"application/json": {"schema": {"type": "object", "additionalProperties": True}}}},
                "404": {"$ref": "#/components/responses/NotFound"}
            }
        },
        "post": {
            "tags": ["Educational Concepts"],
            "summary": "Executa simulação quantitativa do conceito via Payload JSON",
            "description": "Submete parâmetros corporativos customizados para simulação pedagógica da regra de negócio.",
            "operationId": "simulateConceptPost",
            "parameters": [{"$ref": "#/components/parameters/ConceptIdPathParam"}],
            "requestBody": {
                "required": False,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ConceptSimulationRequest"}}}
            },
            "responses": {
                "200": {"description": "Resultado analítico da simulação", "content": {"application/json": {"schema": {"type": "object", "additionalProperties": True}}}},
                "404": {"$ref": "#/components/responses/NotFound"}
            }
        }
    }
    p["/api/flows"] = {
        "get": {
            "tags": ["System & Diagnostics"],
            "summary": "Catálogo dos 107 fluxos operacionais e transacionais homologados",
            "description": "Lista a totalidade dos 107 fluxos canônicos de ponta a ponta da B3 (mercado à vista, derivativos, balcão, garantias, SPB, Drex e seguros), detalhando participantes, mensagens e regras de negócio.",
            "operationId": "listHomologatedFlows",
            "responses": {
                "200": {
                    "description": "Lista de fluxos homologados recuperada com sucesso",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "flow_id": {"type": "string", "example": "FL-001"},
                                        "title": {"type": "string", "example": "Negociação PUMA Equity Market Order"},
                                        "module": {"type": "string", "example": "PUMA_TRADING"}
                                    },
                                    "additionalProperties": True
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    return p
