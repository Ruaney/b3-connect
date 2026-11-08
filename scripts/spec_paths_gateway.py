"""Gateway Paths (Orders, Auction, SNG, SRO) for OpenAPI 3.1."""

def get_gateway_paths():
    p = {}
    p["/api/v1/orders"] = {
        "get": {
            "tags": ["Institutional Orders Gateway"],
            "summary": "Consulta histórico de ordens institucionais submetidas",
            "description": "Permite aos Participantes de Negociação consultar ordens no PUMA Trading System com filtros por conta, ticker, status ou intervalo de tempo.",
            "operationId": "listOrders",
            "security": [{"OAuth2MutualTLS": ["trading:orders"]}, {"InstitutionalApiKey": []}],
            "parameters": [
                {"name": "account", "in": "query", "required": False, "schema": {"type": "string"}},
                {"name": "symbol", "in": "query", "required": False, "schema": {"type": "string"}},
                {"name": "status", "in": "query", "required": False, "schema": {"$ref": "#/components/schemas/OrderStatusEnum"}}
            ],
            "responses": {
                "200": {"description": "Lista de ordens institucionais encontradas", "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/OrderResponse"}}}}},
                "401": {"$ref": "#/components/responses/Unauthorized"},
                "403": {"$ref": "#/components/responses/Forbidden"}
            }
        },
        "post": {
            "tags": ["Institutional Orders Gateway"],
            "summary": "Submissão de nova ordem de negociação no PUMA Trading System",
            "description": "Valida limites pré-operacionais (Pre-Trade Risk / CVM 505 / LAW-001 / LAW-002: fat finger, túnel de preço e limite financeiro bruto), registra no livro de ofertas e emite resposta atômica de aceitação com chave de idempotência obrigatória.",
            "operationId": "createOrder",
            "security": [{"OAuth2MutualTLS": ["trading:orders"]}, {"InstitutionalApiKey": []}],
            "parameters": [
                {"$ref": "#/components/parameters/IdempotencyKeyHeader"},
                {"$ref": "#/components/parameters/CorrelationIdHeader"}
            ],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/OrderCreateRequest"}}}
            },
            "responses": {
                "201": {"description": "Ordem aceita e registrada no livro PUMA", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/OrderResponse"}}}},
                "400": {"$ref": "#/components/responses/BadRequest"},
                "401": {"$ref": "#/components/responses/Unauthorized"},
                "422": {"description": "Rejeição por violação de risco ou túnel de preço", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ValidationError"}}}},
                "429": {"$ref": "#/components/responses/TooManyRequests"}
            }
        }
    }
    p["/api/v1/orders/{order_id}"] = {
        "get": {
            "tags": ["Institutional Orders Gateway"],
            "summary": "Consulta situação e histórico de execuções de uma ordem específica",
            "description": "Retorna o estado detalhado da ordem, volume preenchido, residual e fills apurados.",
            "operationId": "getOrderById",
            "security": [{"OAuth2MutualTLS": ["trading:orders"]}, {"InstitutionalApiKey": []}],
            "parameters": [{"$ref": "#/components/parameters/OrderIdPathParam"}],
            "responses": {
                "200": {"description": "Detalhes da ordem recuperados", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/OrderResponse"}}}},
                "404": {"$ref": "#/components/responses/NotFound"}
            }
        },
        "delete": {
            "tags": ["Institutional Orders Gateway"],
            "summary": "Cancelamento de ordem ativa no PUMA Trading System",
            "description": "Solicita o cancelamento tempestivo do saldo remanescente da ordem. Se a ordem estiver em leilão durante a Janela de Congelamento (Freeze Window), o cancelamento é sumariamente bloqueado em conformidade com LAW-104A.",
            "operationId": "cancelOrder",
            "security": [{"OAuth2MutualTLS": ["trading:orders"]}, {"InstitutionalApiKey": []}],
            "parameters": [
                {"$ref": "#/components/parameters/OrderIdPathParam"},
                {"$ref": "#/components/parameters/IdempotencyKeyHeader"}
            ],
            "responses": {
                "200": {"description": "Ordem cancelada com sucesso", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/OrderCancelResponse"}}}},
                "403": {"description": "Cancelamento proibido na Janela de Congelamento (LAW-104A)", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}},
                "404": {"$ref": "#/components/responses/NotFound"}
            }
        }
    }
    p["/api/v1/auction/moc"] = {
        "get": {
            "tags": ["Closing Auction & MOC/LOC"],
            "summary": "Consulta livro de ordens MOC/LOC ativas no Leilão de Fechamento",
            "description": "Lista ordens Market-On-Close (MOC) e Limit-On-Close (LOC) elegíveis para participação no closing cross.",
            "operationId": "listMocLocOrders",
            "security": [{"OAuth2MutualTLS": ["trading:auction"]}, {"InstitutionalApiKey": []}],
            "parameters": [{"name": "symbol", "in": "query", "required": True, "description": "Ticker do ativo", "schema": {"type": "string"}}],
            "responses": {
                "200": {"description": "Ordens MOC/LOC ativas no leilão", "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/MocLocOrderResponse"}}}}}
            }
        },
        "post": {
            "tags": ["Closing Auction & MOC/LOC"],
            "summary": "Submissão especializada de ordens MOC ou LOC no Leilão de Fechamento PUMA (FL-098)",
            "description": "Submete ordem MOC ou LOC garantindo prioridade absoluta MOC sobre LOC (LAW-104B) e proteção da Freeze Window (LAW-104A).",
            "operationId": "createMocLocOrder",
            "security": [{"OAuth2MutualTLS": ["trading:auction"]}, {"InstitutionalApiKey": []}],
            "parameters": [{"$ref": "#/components/parameters/IdempotencyKeyHeader"}],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/MocLocOrderCreateRequest"}}}
            },
            "responses": {
                "201": {"description": "Ordem MOC/LOC registrada no leilão", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/MocLocOrderResponse"}}}},
                "400": {"$ref": "#/components/responses/BadRequest"},
                "422": {"$ref": "#/components/responses/UnprocessableEntity"}
            }
        }
    }
    p["/api/v1/auction/theoretical-price"] = {
        "get": {
            "tags": ["Closing Auction & MOC/LOC"],
            "summary": "Consulta em tempo real do Preço Teórico de Fechamento (PTP), desbalanceamento e fase",
            "description": "Calcula e divulga o Preço Teórico de Fechamento (PTP/PTF), volume transacionável projetado, desbalanceamento de ofertas (Imbalance Side e Qty) e status da Janela de Congelamento.",
            "operationId": "getTheoreticalPrice",
            "parameters": [{"name": "symbol", "in": "query", "required": True, "schema": {"type": "string", "example": "VALE3"}}],
            "responses": {
                "200": {"description": "Projeção analítica do preço teórico", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TheoreticalPriceResponse"}}}},
                "404": {"$ref": "#/components/responses/NotFound"}
            }
        },
        "post": {
            "tags": ["Closing Auction & MOC/LOC"],
            "summary": "Simula ou aciona o Uncross determinístico do Leilão de Fechamento",
            "description": "Executa o cruzamento atômico de ofertas ao Preço Teórico Único, verificando a conservação estrita de volume: Compras == Vendas == Fills (LAW-104C).",
            "operationId": "simulateUncross",
            "security": [{"OAuth2MutualTLS": ["trading:auction"]}, {"InstitutionalApiKey": []}],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/UncrossSimulationRequest"}}}
            },
            "responses": {
                "200": {"description": "Resultado atômico do Uncross consolidado", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/UncrossSimulationResult"}}}}
            }
        }
    }
    p["/api/v1/sng/gravames"] = {
        "get": {
            "tags": ["SNG — Financiamentos"],
            "summary": "Consulta de restrições financeiras registradas no Sistema Nacional de Gravames (SNG)",
            "description": "Permite consultar a existência de gravame ativo ou histórico por Chassi, RENAVAM, número de contrato ou CNPJ da instituição credora.",
            "operationId": "listGravames",
            "security": [{"OAuth2MutualTLS": ["sng:gravames"]}, {"InstitutionalApiKey": []}],
            "parameters": [
                {"name": "chassi", "in": "query", "required": False, "schema": {"type": "string"}},
                {"name": "renavam", "in": "query", "required": False, "schema": {"type": "string"}},
                {"name": "credor_cnpj", "in": "query", "required": False, "schema": {"type": "string"}},
                {"name": "status", "in": "query", "required": False, "schema": {"$ref": "#/components/schemas/StatusGravameEnum"}}
            ],
            "responses": {
                "200": {"description": "Registros de gravames localizados", "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/GravameRecord"}}}}},
                "401": {"$ref": "#/components/responses/Unauthorized"}
            }
        },
        "post": {
            "tags": ["SNG — Financiamentos"],
            "summary": "Registro e inclusão de gravame sobre veículo automotor (Resolução CONTRAN nº 807/2020)",
            "description": "Averba alienação fiduciária, leasing ou reserva de domínio perante o Detran. Impõe a invariante LAW-112: Unicidade de Gravame Ativo por Chassi/Renavam (conflito 409 se já existir gravame ativo).",
            "operationId": "createGravame",
            "security": [{"OAuth2MutualTLS": ["sng:gravames"]}, {"InstitutionalApiKey": []}],
            "parameters": [{"$ref": "#/components/parameters/IdempotencyKeyHeader"}],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/GravameCreateRequest"}}}
            },
            "responses": {
                "201": {"description": "Gravame averbado com sucesso no SNG", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/GravameRecord"}}}},
                "400": {"$ref": "#/components/responses/BadRequest"},
                "409": {"description": "Conflito: Gravame ativo existente para Chassi/Renavam (LAW-112)", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}},
                "422": {"$ref": "#/components/responses/UnprocessableEntity"}
            }
        }
    }
    p["/api/v1/sng/gravames/{gravame_id}"] = {
        "get": {
            "tags": ["SNG — Financiamentos"],
            "summary": "Consulta detalhe do gravame por identificador único",
            "description": "Retorna o registro mestre do contrato, hash SHA-256 de auditoria e protocolo do Detran.",
            "operationId": "getGravameById",
            "security": [{"OAuth2MutualTLS": ["sng:gravames"]}, {"InstitutionalApiKey": []}],
            "parameters": [{"$ref": "#/components/parameters/GravameIdPathParam"}],
            "responses": {
                "200": {"description": "Registro do gravame recuperado", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/GravameRecord"}}}},
                "404": {"$ref": "#/components/responses/NotFound"}
            }
        }
    }
    p["/api/v1/sng/gravames/{gravame_id}/baixa"] = {
        "post": {
            "tags": ["SNG — Financiamentos"],
            "summary": "Baixa e desalienação fiduciária do veículo por quitação integral",
            "description": "Envia comando oficial de desalienação ao Detran competente após confirmação de quitação integral do crédito automotivo.",
            "operationId": "baixaGravame",
            "security": [{"OAuth2MutualTLS": ["sng:gravames"]}, {"InstitutionalApiKey": []}],
            "parameters": [
                {"$ref": "#/components/parameters/GravameIdPathParam"},
                {"$ref": "#/components/parameters/IdempotencyKeyHeader"}
            ],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/GravameBaixaRequest"}}}
            },
            "responses": {
                "200": {"description": "Baixa efetuada e protocolo emitido", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/GravameBaixaResponse"}}}},
                "404": {"$ref": "#/components/responses/NotFound"},
                "409": {"description": "Gravame já baixado ou cancelado", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}}
            }
        }
    }
    p["/api/v1/sng/gravames/{gravame_id}/cancelamento"] = {
        "post": {
            "tags": ["SNG — Financiamentos"],
            "summary": "Cancelamento administrativo de gravame por erro formal ou desistência",
            "description": "Anula o registro de gravame antes de sua consolidação formal perante o Detran.",
            "operationId": "cancelGravame",
            "security": [{"OAuth2MutualTLS": ["sng:gravames"]}, {"InstitutionalApiKey": []}],
            "parameters": [
                {"$ref": "#/components/parameters/GravameIdPathParam"},
                {"$ref": "#/components/parameters/IdempotencyKeyHeader"}
            ],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/GravameCancelamentoRequest"}}}
            },
            "responses": {
                "200": {"description": "Cancelamento homologado", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/GravameCancelamentoResponse"}}}},
                "404": {"$ref": "#/components/responses/NotFound"}
            }
        }
    }
    p["/api/v1/sro/apolices"] = {
        "get": {
            "tags": ["SRO — Seguros & SUSEP"],
            "summary": "Consulta de apólices registradas no repositório SRO SUSEP B3",
            "description": "Lista apólices vigentes, endossadas ou canceladas com filtros por ramo SUSEP, segurado, seguradora líder ou proposta.",
            "operationId": "listApolices",
            "security": [{"OAuth2MutualTLS": ["sro:apolices"]}, {"InstitutionalApiKey": []}],
            "parameters": [
                {"name": "ramo", "in": "query", "required": False, "schema": {"$ref": "#/components/schemas/RamoSeguroEnum"}},
                {"name": "seguradora_lider_cnpj", "in": "query", "required": False, "schema": {"type": "string"}},
                {"name": "segurado_documento", "in": "query", "required": False, "schema": {"type": "string"}},
                {"name": "status", "in": "query", "required": False, "schema": {"$ref": "#/components/schemas/StatusApoliceEnum"}}
            ],
            "responses": {
                "200": {"description": "Apólices localizadas", "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/ApoliceRecord"}}}}},
                "401": {"$ref": "#/components/responses/Unauthorized"}
            }
        },
        "post": {
            "tags": ["SRO — Seguros & SUSEP"],
            "summary": "Registro de apólice de seguro individual ou cosseguro perante a SUSEP",
            "description": "Registra emissão da apólice. Se houver cosseguro, impõe a verificação estrita da invariante LAW-113: Conservação de Cosseguro a exatamente 100.00% das quotas e respeito ao Limite Máximo de Garantia (LMG).",
            "operationId": "createApolice",
            "security": [{"OAuth2MutualTLS": ["sro:apolices"]}, {"InstitutionalApiKey": []}],
            "parameters": [{"$ref": "#/components/parameters/IdempotencyKeyHeader"}],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApoliceCreateRequest"}}}
            },
            "responses": {
                "201": {"description": "Apólice registrada e protocolo SUSEP SRO emitido", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApoliceRecord"}}}},
                "400": {"$ref": "#/components/responses/BadRequest"},
                "422": {"description": "Violação da invariante LAW-113 (cosseguro != 100%)", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ValidationError"}}}}
            }
        }
    }
    p["/api/v1/sro/apolices/{apolice_id}"] = {
        "get": {
            "tags": ["SRO — Seguros & SUSEP"],
            "summary": "Consulta detalhada de apólice de seguro registrada no SRO",
            "description": "Retorna o registro mestre, cotas de cosseguro, saldo de LMG remanescente e hash de integridade SUSEP.",
            "operationId": "getApoliceById",
            "security": [{"OAuth2MutualTLS": ["sro:apolices"]}, {"InstitutionalApiKey": []}],
            "parameters": [{"$ref": "#/components/parameters/ApoliceIdPathParam"}],
            "responses": {
                "200": {"description": "Dados da apólice recuperados com sucesso", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApoliceRecord"}}}},
                "404": {"$ref": "#/components/responses/NotFound"}
            }
        }
    }
    p["/api/v1/sro/apolices/{apolice_id}/endosso"] = {
        "post": {
            "tags": ["SRO — Seguros & SUSEP"],
            "summary": "Registro de endosso modificativo ou de cancelamento perante a SUSEP",
            "description": "Aplica alteração formal na vigência, valor de prêmio ou Limite Máximo de Garantia (LMG) da apólice registrada.",
            "operationId": "createEndosso",
            "security": [{"OAuth2MutualTLS": ["sro:apolices"]}, {"InstitutionalApiKey": []}],
            "parameters": [
                {"$ref": "#/components/parameters/ApoliceIdPathParam"},
                {"$ref": "#/components/parameters/IdempotencyKeyHeader"}
            ],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/EndossoCreateRequest"}}}
            },
            "responses": {
                "200": {"description": "Endosso homologado e apólice atualizada", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApoliceRecord"}}}},
                "404": {"$ref": "#/components/responses/NotFound"}
            }
        }
    }
    p["/api/v1/sro/apolices/{apolice_id}/sinistros"] = {
        "get": {
            "tags": ["SRO — Seguros & SUSEP"],
            "summary": "Consulta histórico de sinistros vinculados a uma apólice",
            "description": "Lista sinistros avisados, em regulação e liquidados perante a SUSEP.",
            "operationId": "listSinistros",
            "security": [{"OAuth2MutualTLS": ["sro:apolices"]}, {"InstitutionalApiKey": []}],
            "parameters": [{"$ref": "#/components/parameters/ApoliceIdPathParam"}],
            "responses": {
                "200": {"description": "Lista de sinistros vinculados", "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/SinistroRecord"}}}}},
                "404": {"$ref": "#/components/responses/NotFound"}
            }
        },
        "post": {
            "tags": ["SRO — Seguros & SUSEP"],
            "summary": "Aviso e regulação de sinistro securitário com dedução de LMG",
            "description": "Registra ocorrência e aviso de sinistro, verificando a disponibilidade de saldo no Limite Máximo de Garantia (LMG) da apólice conforme a invariante LAW-113.",
            "operationId": "createSinistro",
            "security": [{"OAuth2MutualTLS": ["sro:apolices"]}, {"InstitutionalApiKey": []}],
            "parameters": [
                {"$ref": "#/components/parameters/ApoliceIdPathParam"},
                {"$ref": "#/components/parameters/IdempotencyKeyHeader"}
            ],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SinistroCreateRequest"}}}
            },
            "responses": {
                "201": {"description": "Sinistro registrado no SRO", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SinistroRecord"}}}},
                "400": {"$ref": "#/components/responses/BadRequest"},
                "422": {"description": "Sinistro excede o Limite Máximo de Garantia (LAW-113)", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ValidationError"}}}}
            }
        }
    }
    p["/api/v1/sro/apolices/{apolice_id}/sinistros/{sinistro_id}/liquidar"] = {
        "post": {
            "tags": ["SRO — Seguros & SUSEP"],
            "summary": "Liquidação e pagamento financeiro de indenização securitária",
            "description": "Atesta a liquidação financeira do sinistro perante o segurado ou beneficiário com número de ordem bancária no SPB (STR/PIX), encerrando a pendência e consolidando a baixa do LMG.",
            "operationId": "liquidarSinistro",
            "security": [{"OAuth2MutualTLS": ["sro:apolices"]}, {"InstitutionalApiKey": []}],
            "parameters": [
                {"$ref": "#/components/parameters/ApoliceIdPathParam"},
                {"$ref": "#/components/parameters/SinistroIdPathParam"},
                {"$ref": "#/components/parameters/IdempotencyKeyHeader"}
            ],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SinistroLiquidarRequest"}}}
            },
            "responses": {
                "200": {"description": "Sinistro liquidado e averbado na SUSEP", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SinistroRecord"}}}},
                "404": {"$ref": "#/components/responses/NotFound"}
            }
        }
    }
    return p
