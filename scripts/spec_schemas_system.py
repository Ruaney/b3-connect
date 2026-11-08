"""System, Simulation, Concepts & TLA+ Schemas for OpenAPI 3.1."""

def get_system_schemas():
    return {
        "ApiError": {
            "type": "object",
            "required": ["code", "message", "timestamp"],
            "properties": {
                "code": {"type": "string", "description": "Código de erro estável", "example": "FREEZE_WINDOW_VIOLATION"},
                "message": {"type": "string", "description": "Mensagem detalhada", "example": "Cancelamentos são proibidos na Janela de Congelamento (LAW-104A)."},
                "details": {"type": "object", "description": "Metadados adicionais", "additionalProperties": True},
                "request_id": {"type": "string", "example": "req-98e1163e-001"},
                "timestamp": {"type": "string", "format": "date-time", "example": "2026-11-08T02:45:00Z"}
            }
        },
        "ValidationErrorItem": {
            "type": "object",
            "required": ["field", "issue"],
            "properties": {
                "field": {"type": "string", "example": "veiculo.chassi"},
                "issue": {"type": "string", "example": "Chassi inválido conforme ISO 3779"},
                "rejected_value": {"type": "string", "example": "9BWZZZ377VT00421"}
            }
        },
        "ValidationError": {
            "type": "object",
            "required": ["code", "message", "validation_errors", "timestamp"],
            "properties": {
                "code": {"type": "string", "example": "VALIDATION_FAILED"},
                "message": {"type": "string", "example": "Falha na validação dos campos obrigatórios da requisição"},
                "validation_errors": {"type": "array", "items": {"$ref": "#/components/schemas/ValidationErrorItem"}},
                "request_id": {"type": "string", "example": "req-98e1163e-002"},
                "timestamp": {"type": "string", "format": "date-time", "example": "2026-11-08T02:45:00Z"}
            }
        },
        "SystemStatusResponse": {
            "type": "object",
            "required": ["version", "homologated_flows", "formal_conservation_laws", "architecture_modules", "documents_summary", "timestamp"],
            "properties": {
                "version": {"type": "string", "example": "2.3.0"},
                "homologated_flows": {"type": "integer", "description": "Fluxos ponta a ponta homologados", "example": 107},
                "formal_conservation_laws": {"type": "integer", "description": "Leis formais de conservação auditadas", "example": 91},
                "architecture_modules": {"type": "integer", "description": "Módulos estruturais B3", "example": 7},
                "documents_summary": {"type": "object", "additionalProperties": True},
                "timestamp": {"type": "string", "format": "date-time", "example": "2026-11-08T02:45:00Z"}
            }
        },
        "HealthResponse": {
            "type": "object",
            "required": ["status", "version", "offline"],
            "properties": {
                "status": {"type": "string", "enum": ["UP", "DOWN", "DEGRADED"], "example": "UP"},
                "version": {"type": "string", "example": "2.3.0"},
                "offline": {"type": "boolean", "example": True}
            }
        },
        "ArchitectureModule": {
            "type": "object",
            "required": ["id", "name", "scope", "protocols", "laws", "color"],
            "properties": {
                "id": {"type": "string", "enum": ["PUMA_TRADING", "CLEARING_RISK", "CSD_DEPOSITARY", "NOME_BALCAO", "SPB_SETTLEMENT", "TARIFF_CATALOG", "BSM_SURVEILLANCE"], "example": "PUMA_TRADING"},
                "name": {"type": "string", "example": "PUMA Trading System"},
                "scope": {"type": "string", "example": "Negociação eletrônica listada de ações, opções, futuros e leilões MOC/LOC"},
                "protocols": {"type": "array", "items": {"type": "string"}, "example": ["FIX 4.4 / 5.0 SP2", "PUMA SBE v2 (Binary)", "UMDF 2.0 (Multicast)"]},
                "laws": {"type": "array", "items": {"type": "string"}, "example": ["LAW-001", "LAW-002", "LAW-093", "LAW-104A", "LAW-104B", "LAW-104C"]},
                "color": {"type": "string", "example": "#0284c7"}
            }
        },
        "DocumentMetadata": {
            "type": "object",
            "required": ["id", "title", "category", "format"],
            "properties": {
                "id": {"type": "string", "example": "PUMA_TRADING_RULES"},
                "title": {"type": "string", "example": "Manual de Procedimentos Operacionais PUMA Trading System"},
                "category": {"type": "string", "example": "PUMA_TRADING"},
                "format": {"type": "string", "example": "markdown"},
                "page_count": {"type": "integer", "example": 142},
                "url": {"type": "string", "format": "uri", "example": "https://www.b3.com.br/pt_br/regulacao/estrutura-normativa/regulamentos-e-manuais/"},
                "sha256": {"type": "string", "example": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
                "local_path": {"type": "string", "example": "raw_docs/manual_negociacao_puma.md"}
            }
        },
        "DocumentDetailResponse": {
            "type": "object",
            "required": ["doc", "raw_content"],
            "properties": {
                "doc": {"$ref": "#/components/schemas/DocumentMetadata"},
                "raw_content": {"type": "string", "example": "# Manual de Negociação PUMA..."}
            }
        },
        "MarketEvent": {
            "type": "object",
            "required": ["sequence", "timestamp_sim", "module", "phase", "event_type", "details", "conservation_laws"],
            "properties": {
                "sequence": {"type": "integer", "example": 1},
                "timestamp_sim": {"type": "string", "example": "08:30:00"},
                "module": {"type": "string", "example": "CLEARING_RISK"},
                "phase": {"type": "string", "example": "Fase 1: Pré-Abertura & Colaterais Internacionais Triparty"},
                "event_type": {"type": "string", "example": "INGESTAO_COLATERAL_TRIPARTY"},
                "details": {"type": "object", "additionalProperties": True},
                "conservation_laws": {"type": "array", "items": {"type": "string"}, "example": ["LAW-005", "LAW-088"]}
            }
        },
        "DailySimulationSummary": {
            "type": "object",
            "required": ["total_equities_volume", "total_balcao_notional", "total_spb_settled", "total_drex_rwa_volume", "total_tariffs_collected", "bsm_surveillance_alerts"],
            "properties": {
                "total_equities_volume": {"type": "string", "example": "3550000.00"},
                "total_balcao_notional": {"type": "string", "example": "10000000.00"},
                "total_spb_settled": {"type": "string", "example": "13550000.00"},
                "total_drex_rwa_volume": {"type": "string", "example": "5000000.00"},
                "total_tariffs_collected": {"type": "string", "example": "1065.00"},
                "bsm_surveillance_alerts": {"type": "integer", "example": 0}
            }
        },
        "DailySimulationResult": {
            "type": "object",
            "required": ["simulation_id", "trading_date", "elapsed_seconds", "phases_completed", "events_count", "summary", "laws_verified", "events"],
            "properties": {
                "simulation_id": {"type": "string", "example": "SIM-B3-1731034000123"},
                "trading_date": {"type": "string", "format": "date", "example": "2026-11-06"},
                "elapsed_seconds": {"type": "number", "example": 0.084},
                "phases_completed": {"type": "integer", "example": 8},
                "events_count": {"type": "integer", "example": 24},
                "summary": {"$ref": "#/components/schemas/DailySimulationSummary"},
                "laws_verified": {"type": "array", "items": {"type": "string"}, "example": ["LAW-001", "LAW-004", "LAW-006", "LAW-104A", "LAW-104C", "LAW-106C"]},
                "events": {"type": "array", "items": {"$ref": "#/components/schemas/MarketEvent"}}
            }
        },
        "ChaosScenarioEnum": {
            "type": "string",
            "enum": ["CIRCUIT_BREAKER", "CLEARING_DEFAULT", "RECEIVABLES_LIEN_CONFLICT", "SPB_NETWORK_DISRUPTION"],
            "example": "CIRCUIT_BREAKER"
        },
        "ChaosStep": {
            "type": "object",
            "required": ["sequence", "timestamp_sim", "phase", "action", "outcome", "conservation_law"],
            "properties": {
                "sequence": {"type": "integer", "example": 1},
                "timestamp_sim": {"type": "string", "example": "10:15:00"},
                "phase": {"type": "string", "example": "PUMA_CIRCUIT_BREAKER_TRIGGER"},
                "action": {"type": "string", "example": "Queda do Ibovespa atinge 10.02%"},
                "outcome": {"type": "string", "example": "Suspensão automática das negociações por 30 minutos"},
                "conservation_law": {"type": "string", "example": "LAW-001"},
                "financial_impact": {"type": "string", "example": "0.00"},
                "details": {"type": "object", "additionalProperties": True}
            }
        },
        "ChaosSimulationResult": {
            "type": "object",
            "required": ["scenario", "title", "trading_date", "description", "is_resilient", "total_loss_absorbed_by_safeguards", "spb_liquidity_injected", "steps"],
            "properties": {
                "scenario": {"$ref": "#/components/schemas/ChaosScenarioEnum"},
                "title": {"type": "string", "example": "Cenário 1: Acionamento de Circuit Breaker Nível 1 & 2"},
                "trading_date": {"type": "string", "format": "date", "example": "2026-11-06"},
                "description": {"type": "string", "example": "Simula queda de 10% seguida de reabertura..."},
                "is_resilient": {"type": "boolean", "example": True},
                "total_loss_absorbed_by_safeguards": {"type": "string", "example": "450000000.00"},
                "spb_liquidity_injected": {"type": "string", "example": "0.00"},
                "steps": {"type": "array", "items": {"$ref": "#/components/schemas/ChaosStep"}}
            }
        },
        "Phase7FlowIdEnum": {
            "type": "string",
            "enum": ["FL-101", "FL-102", "FL-103", "FL-104", "FL-105", "FL-106", "FL-107"],
            "example": "FL-101"
        },
        "TLAModelCheckReport": {
            "type": "object",
            "required": ["spec_path", "passed", "distinct_states_explored", "transitions_evaluated", "max_depth_reached", "deadlocks_found", "properties"],
            "properties": {
                "spec_path": {"type": "string", "example": "specs/tla/CentralLiquidityBus.tla"},
                "passed": {"type": "boolean", "example": True},
                "distinct_states_explored": {"type": "integer", "example": 48},
                "transitions_evaluated": {"type": "integer", "example": 96},
                "max_depth_reached": {"type": "integer", "example": 12},
                "deadlocks_found": {"type": "integer", "example": 0},
                "properties": {
                    "type": "object",
                    "required": ["TypeOK", "ZeroSumAccountingConservation_LAW_106A", "WaterfallOrderInviolability_LAW_106B", "NoUnabsorbedDefaultAtSettlement_LAW_106B", "IntersystemSettlementAtomicity_LAW_106C", "EventualCycleTermination_Liveness"],
                    "properties": {
                        "TypeOK": {"type": "boolean", "example": True},
                        "ZeroSumAccountingConservation_LAW_106A": {"type": "boolean", "example": True},
                        "WaterfallOrderInviolability_LAW_106B": {"type": "boolean", "example": True},
                        "NoUnabsorbedDefaultAtSettlement_LAW_106B": {"type": "boolean", "example": True},
                        "IntersystemSettlementAtomicity_LAW_106C": {"type": "boolean", "example": True},
                        "EventualCycleTermination_Liveness": {"type": "boolean", "example": True}
                    }
                }
            }
        },
        "B3ConceptSummary": {
            "type": "object",
            "required": ["id", "category", "title", "badge", "summary"],
            "properties": {
                "id": {"type": "string", "example": "novacao-ccp"},
                "category": {"type": "string", "example": "CÂMARA & RISCO"},
                "title": {"type": "string", "example": "Novação Legal e Contraparte Central (Lei nº 10.214/2001)"},
                "badge": {"type": "string", "example": "Lei 10.214 / LAW-004"},
                "summary": {"type": "string", "example": "A Câmara B3 extingue o risco bilateral tornando-se compradora de todo vendedor."}
            }
        },
        "B3ConceptDetail": {
            "type": "object",
            "required": ["id", "category", "title", "badge", "summary", "what_is", "why_exists", "how_it_works", "math_rule", "practical_example", "diagram_ascii", "related_flows", "related_laws"],
            "properties": {
                "id": {"type": "string", "example": "novacao-ccp"},
                "category": {"type": "string", "example": "CÂMARA & RISCO"},
                "title": {"type": "string", "example": "Novação Legal e Contraparte Central (Lei nº 10.214/2001)"},
                "badge": {"type": "string", "example": "Lei 10.214 / LAW-004"},
                "summary": {"type": "string", "example": "A Câmara B3 extingue o risco bilateral..."},
                "what_is": {"type": "string", "example": "No exato milissegundo em que uma ordem é casada..."},
                "why_exists": {"type": "string", "example": "Sem a novação, a quebra de um único participante causaria risco sistêmico..."},
                "how_it_works": {"type": "array", "items": {"type": "string"}, "example": ["Trade Execution", "Novação civil", "Criação de 2 pernas garantidas"]},
                "math_rule": {"type": "string", "example": "∑ Netting + Posição_B3 = 0, onde Posição_B3 ≡ 0"},
                "practical_example": {"type": "string", "example": "Banco A compra ações de Corretora B..."},
                "diagram_ascii": {"type": "string", "example": "[A] <-> [B3 CCP] <-> [B]"},
                "related_flows": {"type": "array", "items": {"type": "string"}, "example": ["FL-001", "FL-004"]},
                "related_laws": {"type": "array", "items": {"type": "string"}, "example": ["LAW-004", "LAW-005"]}
            }
        },
        "ConceptSimulationRequest": {
            "type": "object",
            "properties": {
                "buyer": {"type": "string", "example": "Alpha DTVM"},
                "seller": {"type": "string", "example": "Beta Corretora"},
                "symbol": {"type": "string", "example": "PETR4"},
                "quantity": {"type": "integer", "minimum": 1, "example": 10000},
                "price": {"type": "number", "minimum": 0.01, "example": 38.50}
            }
        }
    }
