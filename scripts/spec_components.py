"""Reusable OpenAPI 3.1 Components for B3 Integration Atlas."""

def get_parameters():
    return {
        "IdempotencyKeyHeader": {
            "name": "Idempotency-Key",
            "in": "header",
            "required": True,
            "description": "Chave única de idempotência (UUIDv4) para prevenir duplicidade de operações financeiras",
            "schema": {"type": "string", "format": "uuid", "example": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"}
        },
        "CorrelationIdHeader": {
            "name": "X-Correlation-Id",
            "in": "header",
            "required": False,
            "description": "Identificador de correlação para rastreabilidade de transações ponta a ponta",
            "schema": {"type": "string", "example": "corr-b3-20261108-001928"}
        },
        "DocIdPathParam": {
            "name": "doc_id",
            "in": "path",
            "required": True,
            "description": "Identificador único do documento oficial (ex.: PUMA_TRADING_RULES, CORE_MANUAL)",
            "schema": {"type": "string", "example": "CORE_MANUAL"}
        },
        "ScenarioPathParam": {
            "name": "scenario",
            "in": "path",
            "required": True,
            "description": "Identificador do cenário de estresse e caos financeiro sistêmico",
            "schema": {"$ref": "#/components/schemas/ChaosScenarioEnum"}
        },
        "FlowIdPathParam": {
            "name": "flow_id",
            "in": "path",
            "required": True,
            "description": "Identificador do fluxo avançado da Fase 7 (FL-101 a FL-105)",
            "schema": {"$ref": "#/components/schemas/Phase7FlowIdEnum"}
        },
        "ConceptIdPathParam": {
            "name": "concept_id",
            "in": "path",
            "required": True,
            "description": "Identificador canônico do conceito complexo B3 (ex.: novacao-ccp, default-waterfall, risco-core)",
            "schema": {"type": "string", "example": "novacao-ccp"}
        },
        "OrderIdPathParam": {
            "name": "order_id",
            "in": "path",
            "required": True,
            "description": "Identificador da ordem no PUMA Trading System",
            "schema": {"type": "string", "example": "ORD-20261108-0010928"}
        },
        "GravameIdPathParam": {
            "name": "gravame_id",
            "in": "path",
            "required": True,
            "description": "Identificador único do registro de gravame no SNG B3 Financiamentos",
            "schema": {"type": "string", "example": "GRV-SP-20261108-00981"}
        },
        "ApoliceIdPathParam": {
            "name": "apolice_id",
            "in": "path",
            "required": True,
            "description": "Identificador único da apólice no repositório SRO SUSEP B3 Seguros",
            "schema": {"type": "string", "example": "APOL-2026-0531-998811"}
        },
        "SinistroIdPathParam": {
            "name": "sinistro_id",
            "in": "path",
            "required": True,
            "description": "Identificador único do sinistro no repositório SRO",
            "schema": {"type": "string", "example": "SIN-2026-00441"}
        }
    }

def get_responses():
    return {
        "BadRequest": {
            "description": "Requisição inválida ou parâmetros incorretos",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}
        },
        "Unauthorized": {
            "description": "Autenticação ausente ou credenciais corporativas mTLS/API-Key inválidas",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}
        },
        "Forbidden": {
            "description": "Acesso não autorizado para o perfil do participante B3",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}
        },
        "NotFound": {
            "description": "Recurso solicitado não foi encontrado",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}
        },
        "Conflict": {
            "description": "Conflito de estado do recurso ou violação de regra de unicidade (ex.: gravame ativo concorrente LAW-112)",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}
        },
        "UnprocessableEntity": {
            "description": "Violação de regras de validação estrita de negócio ou invariantes formais",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ValidationError"}}}
        },
        "TooManyRequests": {
            "description": "Taxa de requisições excedeu os limites de governança (Rate Limit / Quota)",
            "headers": {
                "Retry-After": {"schema": {"type": "integer"}, "description": "Segundos até a liberação de novas requisições"},
                "X-RateLimit-Limit": {"schema": {"type": "integer"}, "description": "Limite máximo de requisições por janela"},
                "X-RateLimit-Remaining": {"schema": {"type": "integer"}, "description": "Requisições restantes na janela atual"}
            },
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}
        },
        "InternalError": {
            "description": "Erro interno no processamento do servidor ou falha não recuperável",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}
        }
    }

def get_security_schemes():
    return {
        "OAuth2MutualTLS": {
            "type": "oauth2",
            "description": "Autenticação corporativa B3 via OAuth 2.0 com mTLS (Certificado Digital ICP-Brasil A3/A4)",
            "flows": {
                "clientCredentials": {
                    "tokenUrl": "https://api.b3.com.br/oauth2/token",
                    "scopes": {
                        "trading:orders": "Roteamento e cancelamento de ordens no PUMA",
                        "trading:auction": "Submissão e consulta no Leilão de Fechamento MOC/LOC",
                        "clearing:risk": "Consulta de margens e posições de compensação",
                        "sng:gravames": "Inclusão e desalienação no Sistema Nacional de Gravames",
                        "sro:apolices": "Registro e administração de apólices e sinistros no SRO SUSEP",
                        "atlas:simulation": "Execução de simulações e diagnósticos do Atlas"
                    }
                }
            }
        },
        "InstitutionalApiKey": {
            "type": "apiKey",
            "in": "header",
            "name": "X-B3-Api-Key",
            "description": "Chave de acesso institucional emitida pela B3 aos Participantes de Negociação (PN) e Liquidação (PL)"
        }
    }
