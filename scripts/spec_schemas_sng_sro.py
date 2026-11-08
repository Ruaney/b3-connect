"""SNG Gravames & SRO Seguros Schemas for OpenAPI 3.1."""

def get_sng_sro_schemas():
    return {
        "ModalidadeGravameEnum": {
            "type": "string",
            "enum": ["ALIENACAO_FIDUCIARIA", "ARRENDAMENTO_MERCANTIL", "RESERVA_DOMINIO", "PENHOR_VEICULAR"],
            "example": "ALIENACAO_FIDUCIARIA"
        },
        "StatusGravameEnum": {
            "type": "string",
            "enum": ["ATIVO", "BAIXADO", "CANCELADO", "REJEITADO"],
            "example": "ATIVO"
        },
        "DetranUFEnum": {
            "type": "string",
            "enum": ["SP", "RJ", "MG", "PR", "RS", "SC", "BA", "PE", "GO", "DF", "ES", "CE", "MT", "MS", "PA", "AM", "PB", "RN", "AL", "SE", "PI", "MA", "TO", "RO", "AC", "AP", "RR"],
            "example": "SP"
        },
        "VeiculoIdentificacao": {
            "type": "object",
            "required": ["chassi", "renavam", "placa", "uf_emplacamento", "marca_modelo", "ano_fabricacao", "ano_modelo"],
            "properties": {
                "chassi": {"type": "string", "pattern": "^[A-HJ-NPR-Z0-9]{17}$", "example": "9BWZZZ377VT004211"},
                "renavam": {"type": "string", "pattern": "^[0-9]{9,11}$", "example": "00123456789"},
                "placa": {"type": "string", "pattern": "^[A-Z]{3}[0-9][A-Z0-9][0-9]{2}$", "example": "BRA2E19"},
                "uf_emplacamento": {"$ref": "#/components/schemas/DetranUFEnum"},
                "marca_modelo": {"type": "string", "example": "VOLKSWAGEN POLO TRACK 1.0"},
                "ano_fabricacao": {"type": "integer", "minimum": 1900, "maximum": 2030, "example": 2025},
                "ano_modelo": {"type": "integer", "minimum": 1900, "maximum": 2030, "example": 2026}
            }
        },
        "GravameCreateRequest": {
            "type": "object",
            "required": ["contrato_numero", "modalidade", "credor_cnpj", "credor_nome", "devedor_documento", "devedor_nome", "veiculo", "valor_financiamento", "quantidade_parcelas", "taxa_juros_anual_pct"],
            "properties": {
                "contrato_numero": {"type": "string", "example": "FIN-2026-990182"},
                "modalidade": {"$ref": "#/components/schemas/ModalidadeGravameEnum"},
                "credor_cnpj": {"type": "string", "pattern": "^[0-9]{14}$", "example": "60746948000112"},
                "credor_nome": {"type": "string", "example": "BANCO BRADESCO FINANCIAMENTOS S.A."},
                "devedor_documento": {"type": "string", "pattern": "^[0-9]{11}([0-9]{3})?$", "example": "12345678909"},
                "devedor_nome": {"type": "string", "example": "CARLOS EDUARDO PEREIRA"},
                "veiculo": {"$ref": "#/components/schemas/VeiculoIdentificacao"},
                "valor_financiamento": {"type": "number", "minimum": 100.00, "example": 68500.00},
                "quantidade_parcelas": {"type": "integer", "minimum": 1, "maximum": 120, "example": 48},
                "taxa_juros_anual_pct": {"type": "number", "minimum": 0.00, "maximum": 200.00, "example": 18.50}
            }
        },
        "GravameRecord": {
            "type": "object",
            "required": ["gravame_id", "contrato_numero", "modalidade", "credor_cnpj", "credor_nome", "devedor_documento", "devedor_nome", "veiculo", "valor_financiamento", "quantidade_parcelas", "taxa_juros_anual_pct", "data_inclusao", "status", "sha256_hash", "detran_protocolo"],
            "properties": {
                "gravame_id": {"type": "string", "example": "GRV-SP-20261108-00981"},
                "contrato_numero": {"type": "string", "example": "FIN-2026-990182"},
                "modalidade": {"$ref": "#/components/schemas/ModalidadeGravameEnum"},
                "credor_cnpj": {"type": "string", "example": "60746948000112"},
                "credor_nome": {"type": "string", "example": "BANCO BRADESCO FINANCIAMENTOS S.A."},
                "devedor_documento": {"type": "string", "example": "12345678909"},
                "devedor_nome": {"type": "string", "example": "CARLOS EDUARDO PEREIRA"},
                "veiculo": {"$ref": "#/components/schemas/VeiculoIdentificacao"},
                "valor_financiamento": {"type": "string", "example": "68500.00"},
                "quantidade_parcelas": {"type": "integer", "example": 48},
                "taxa_juros_anual_pct": {"type": "string", "example": "18.50"},
                "data_inclusao": {"type": "string", "format": "date-time", "example": "2026-11-08T02:45:00Z"},
                "status": {"$ref": "#/components/schemas/StatusGravameEnum"},
                "data_baixa": {"type": ["string", "null"], "format": "date-time", "example": None},
                "motivo_baixa": {"type": ["string", "null"], "example": None},
                "sha256_hash": {"type": "string", "example": "3a7b9c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b"},
                "detran_protocolo": {"type": "string", "example": "DETRAN-SP-AVERB-2026-88910"}
            }
        },
        "GravameBaixaRequest": {
            "type": "object",
            "required": ["motivo_baixa", "data_quitacao"],
            "properties": {
                "motivo_baixa": {"type": "string", "enum": ["QUITACAO_INTEGRAL", "SUBSTITUICAO_GARANTIA", "DETERMINACAO_JUDICIAL", "PERDA_TOTAL_INDENIZACAO"], "example": "QUITACAO_INTEGRAL"},
                "data_quitacao": {"type": "string", "format": "date", "example": "2026-11-08"},
                "documento_comprobatorio": {"type": "string", "example": "TERMO-QUIT-2026-990182"}
            }
        },
        "GravameBaixaResponse": {
            "type": "object",
            "required": ["gravame_id", "status", "data_baixa", "desalienacao_protocolo", "detran_status"],
            "properties": {
                "gravame_id": {"type": "string", "example": "GRV-SP-20261108-00981"},
                "status": {"type": "string", "example": "BAIXADO"},
                "data_baixa": {"type": "string", "format": "date-time", "example": "2026-11-08T02:47:00Z"},
                "desalienacao_protocolo": {"type": "string", "example": "DESAL-SP-2026-99812"},
                "detran_status": {"type": "string", "example": "LIBERADO_PARA_TRANSFERENCIA"}
            }
        },
        "GravameCancelamentoRequest": {
            "type": "object",
            "required": ["motivo_cancelamento", "justificativa_legal"],
            "properties": {
                "motivo_cancelamento": {"type": "string", "enum": ["ERRO_DIGITACAO", "DESISTENCIA_FINANCIAMENTO", "FRAUDE_DETECTADA"], "example": "ERRO_DIGITACAO"},
                "justificativa_legal": {"type": "string", "example": "Contrato cadastrado com divergência de chassi."}
            }
        },
        "GravameCancelamentoResponse": {
            "type": "object",
            "required": ["gravame_id", "status", "data_cancelamento", "protocolo_cancelamento"],
            "properties": {
                "gravame_id": {"type": "string", "example": "GRV-SP-20261108-00981"},
                "status": {"type": "string", "example": "CANCELADO"},
                "data_cancelamento": {"type": "string", "format": "date-time", "example": "2026-11-08T02:48:00Z"},
                "protocolo_cancelamento": {"type": "string", "example": "CANC-SNG-2026-0012"}
            }
        },
        "RamoSeguroEnum": {
            "type": "string",
            "enum": ["0531_AUTOMOVEL", "0118_PATRIMONIAL_EMPRESARIAL", "0993_VIDA", "1101_RURAL_AGRICOLA", "0622_TRANSPORTE_NACIONAL", "0351_RC_DO", "0775_GARANTIA_CONTRATUAL"],
            "example": "0531_AUTOMOVEL"
        },
        "StatusApoliceEnum": {
            "type": "string",
            "enum": ["VIGENTE", "ENDOSSADA", "CANCELADA", "EXPIRADA"],
            "example": "VIGENTE"
        },
        "StatusSinistroEnum": {
            "type": "string",
            "enum": ["AVISADO", "EM_REGULACAO", "LIQUIDADO", "ENCERRADO_SEM_INDENIZACAO"],
            "example": "AVISADO"
        },
        "CosseguradorShare": {
            "type": "object",
            "required": ["cnpj", "razao_social", "percentual_quota"],
            "properties": {
                "cnpj": {"type": "string", "pattern": "^[0-9]{14}$", "example": "33041210000103"},
                "razao_social": {"type": "string", "example": "SUL AMERICA SEGUROS S.A."},
                "percentual_quota": {"type": "number", "minimum": 0.01, "maximum": 100.00, "example": 60.00},
                "is_lider": {"type": "boolean", "example": True}
            }
        },
        "ApoliceCreateRequest": {
            "type": "object",
            "required": ["numero_proposta", "ramo", "seguradora_lider_cnpj", "seguradora_lider_nome", "segurado_documento", "segurado_nome", "inicio_vigencia", "fim_vigencia", "limite_maximo_garantia", "premio_liquido"],
            "properties": {
                "numero_proposta": {"type": "string", "example": "PROP-2026-AUTO-9988"},
                "ramo": {"$ref": "#/components/schemas/RamoSeguroEnum"},
                "seguradora_lider_cnpj": {"type": "string", "pattern": "^[0-9]{14}$", "example": "33041210000103"},
                "seguradora_lider_nome": {"type": "string", "example": "SUL AMERICA SEGUROS S.A."},
                "segurado_documento": {"type": "string", "pattern": "^[0-9]{11}([0-9]{3})?$", "example": "09876543210"},
                "segurado_nome": {"type": "string", "example": "MARIA FERNANDA SILVEIRA"},
                "inicio_vigencia": {"type": "string", "format": "date", "example": "2026-11-08"},
                "fim_vigencia": {"type": "string", "format": "date", "example": "2027-11-08"},
                "limite_maximo_garantia": {"type": "number", "minimum": 1.00, "example": 150000.00},
                "premio_liquido": {"type": "number", "minimum": 0.01, "example": 3200.00},
                "cosseguradores": {"type": "array", "items": {"$ref": "#/components/schemas/CosseguradorShare"}}
            }
        },
        "ApoliceRecord": {
            "type": "object",
            "required": ["apolice_id", "numero_proposta", "ramo", "seguradora_lider_cnpj", "seguradora_lider_nome", "segurado_documento", "segurado_nome", "data_emissao", "inicio_vigencia", "fim_vigencia", "limite_maximo_garantia", "premio_liquido", "status", "cosseguradores", "sha256_hash", "susep_hash_protocolo", "saldo_lmg_disponivel"],
            "properties": {
                "apolice_id": {"type": "string", "example": "APOL-2026-0531-998811"},
                "numero_proposta": {"type": "string", "example": "PROP-2026-AUTO-9988"},
                "ramo": {"$ref": "#/components/schemas/RamoSeguroEnum"},
                "seguradora_lider_cnpj": {"type": "string", "example": "33041210000103"},
                "seguradora_lider_nome": {"type": "string", "example": "SUL AMERICA SEGUROS S.A."},
                "segurado_documento": {"type": "string", "example": "09876543210"},
                "segurado_nome": {"type": "string", "example": "MARIA FERNANDA SILVEIRA"},
                "data_emissao": {"type": "string", "format": "date-time", "example": "2026-11-08T02:45:00Z"},
                "inicio_vigencia": {"type": "string", "format": "date", "example": "2026-11-08"},
                "fim_vigencia": {"type": "string", "format": "date", "example": "2027-11-08"},
                "limite_maximo_garantia": {"type": "string", "example": "150000.00"},
                "saldo_lmg_disponivel": {"type": "string", "example": "150000.00"},
                "premio_liquido": {"type": "string", "example": "3200.00"},
                "status": {"$ref": "#/components/schemas/StatusApoliceEnum"},
                "cosseguradores": {"type": "array", "items": {"$ref": "#/components/schemas/CosseguradorShare"}},
                "sha256_hash": {"type": "string", "example": "fa1c841e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92"},
                "susep_hash_protocolo": {"type": "string", "example": "SUSEP-SRO-V3-REC-2026-881923"},
                "total_sinistros_avisados": {"type": "integer", "example": 0}
            }
        },
        "EndossoCreateRequest": {
            "type": "object",
            "required": ["tipo_endosso", "data_alteracao", "justificativa"],
            "properties": {
                "tipo_endosso": {"type": "string", "enum": ["PRORROGACAO_VIGENCIA", "ALTERACAO_LMG", "ALTERACAO_PREMIO", "MUDANCA_SEGURADO", "CANCELAMENTO_ENDOSSO"], "example": "ALTERACAO_LMG"},
                "data_alteracao": {"type": "string", "format": "date", "example": "2026-11-08"},
                "novo_fim_vigencia": {"type": "string", "format": "date", "example": "2028-05-08"},
                "alteracao_premio": {"type": "number", "example": 450.00},
                "alteracao_lmg": {"type": "number", "example": 25000.00},
                "justificativa": {"type": "string", "example": "Inclusão de cobertura adicional."}
            }
        },
        "SinistroCreateRequest": {
            "type": "object",
            "required": ["data_ocorrencia", "data_aviso", "valor_estimado", "descricao_cobertura"],
            "properties": {
                "data_ocorrencia": {"type": "string", "format": "date", "example": "2026-11-07"},
                "data_aviso": {"type": "string", "format": "date", "example": "2026-11-08"},
                "valor_estimado": {"type": "number", "minimum": 0.01, "example": 32000.00},
                "descricao_cobertura": {"type": "string", "example": "COLISAO_PARCIAL_TERCEIROS"}
            }
        },
        "SinistroRecord": {
            "type": "object",
            "required": ["sinistro_id", "apolice_id", "data_ocorrencia", "data_aviso", "valor_estimado", "valor_liquidado", "status", "descricao_cobertura"],
            "properties": {
                "sinistro_id": {"type": "string", "example": "SIN-2026-00441"},
                "apolice_id": {"type": "string", "example": "APOL-2026-0531-998811"},
                "data_ocorrencia": {"type": "string", "format": "date", "example": "2026-11-07"},
                "data_aviso": {"type": "string", "format": "date", "example": "2026-11-08"},
                "valor_estimado": {"type": "string", "example": "32000.00"},
                "valor_liquidado": {"type": "string", "example": "0.00"},
                "status": {"$ref": "#/components/schemas/StatusSinistroEnum"},
                "data_liquidacao": {"type": ["string", "null"], "format": "date-time", "example": None},
                "descricao_cobertura": {"type": "string", "example": "COLISAO_PARCIAL_TERCEIROS"}
            }
        },
        "SinistroLiquidarRequest": {
            "type": "object",
            "required": ["valor_liquidado", "data_liquidacao", "comprovante_pagamento"],
            "properties": {
                "valor_liquidado": {"type": "number", "minimum": 0.01, "example": 31500.00},
                "data_liquidacao": {"type": "string", "format": "date", "example": "2026-11-08"},
                "comprovante_pagamento": {"type": "string", "example": "TED-STR-BACEN-20261108-99210"}
            }
        }
    }
