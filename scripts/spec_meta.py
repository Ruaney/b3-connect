"""Metadata, Info, Tags and Servers for OpenAPI 3.1."""

def get_info_and_servers():
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "B3 Integration Atlas & Institutional Gateway API",
            "version": "2.3.0",
            "summary": "Especificação Formal OpenAPI 3.1 da Infraestrutura de Mercado B3",
            "description": (
                "## B3 Integration Atlas — Especificação Formal OpenAPI 3.1\n\n"
                "Especificação canônica de interface de integração, catálogo de mensagens e barramento institucional "
                "da B3 (Brasil, Bolsa, Balcão). Cobre a totalidade dos 7 módulos de infraestrutura de mercado de capitais, "
                "os 107 fluxos ponta a ponta homologados, 91 leis formais de conservação de valor e risco, catálogo pedagógico, "
                "motor determinístico de simulação de pregão (D+2), cenários de estresse e resiliência sistêmica (Chaos), "
                "verificação formal TLA+ do barramento de liquidação multilateral, roteamento eletrônico PUMA (FIX / SBE), "
                "leilão de fechamento MOC/LOC com Janela de Congelamento (Freeze Window / LAW-104A), Sistema Nacional de Gravames (SNG) "
                "e Sistema de Registro de Operações (SRO SUSEP).\n\n"
                "### Fundamentação Legal e Regulatória\n"
                "- Lei Federal nº 10.214/2001 (Compensação e liquidação SPB)\n"
                "- Lei Federal nº 12.810/2013 (Registro e depósito centralizado)\n"
                "- Lei Federal nº 14.711/2023 (Marco Legal das Garantias)\n"
                "- Resoluções BCB/CMN nº 4.734/2019 e 4.737/2019 (Recebíveis de cartões)\n"
                "- Resolução CONTRAN nº 807/2020 (SNG gravames veiculares)\n"
                "- Resoluções CNSP nº 382/2020, 399/2020 e Circulares SUSEP nº 605/2020, 612/2020, 642/2021 (SRO)\n"
                "- Instrução CVM nº 505/2011 e Resolução CVM nº 35/2021 (Governança e limites pré-operacionais)\n"
                "- Regulamento de Operações Câmara B3 e Manual PUMA Trading System."
            ),
            "contact": {
                "name": "B3 Integration Atlas Architecture & Engineering Team",
                "url": "https://www.b3.com.br",
                "email": "integracao-sistemas@b3.com.br"
            },
            "license": {
                "name": "B3 Proprietary Market Infrastructure Standards & MIT Open Architecture",
                "url": "https://github.com/ruanp/b3-integration-atlas"
            }
        },
        "servers": [
            {"url": "http://localhost:8080", "description": "Servidor Visual Local do B3 Integration Atlas (100% Offline Python StdLib)"},
            {"url": "https://api.b3.com.br", "description": "Gateway de Produção Institucional B3 (PUMA, Clearing, NoMe, SNG, SRO)"},
            {"url": "https://cert.api.b3.com.br", "description": "Ambiente de Homologação, Certificação Externa e Piloto B3"}
        ],
        "tags": [
            {"name": "System & Diagnostics", "description": "Endpoints operacionais: status geral, healthcheck, 7 módulos e documentação oficial"},
            {"name": "Market Simulation & Chaos", "description": "Simulação determinística de dia de pregão completo (8 fases, D+2) e cenários de estresse"},
            {"name": "Phase 7 Advanced Flows", "description": "Motores quantitativos da Fase 7: SABR/SVI, COE, PMM, Algorithmic SOR e Drex RWA"},
            {"name": "Formal Verification TLA+", "description": "Verificação matemática formal de invariantes de liquidação multilateral e ausência de deadlocks"},
            {"name": "Educational Concepts", "description": "Catálogo pedagógico exaustivo e motor de simulação analítica dos mecanismos B3"},
            {"name": "Institutional Orders Gateway", "description": "Roteamento e governança de ordens eletrônicas PUMA com validação de limites pré-operacionais (CVM 505)"},
            {"name": "Closing Auction & MOC/LOC", "description": "Leilão de Fechamento PUMA: Ordens MOC/LOC, Janela de Congelamento (LAW-104A) e Preço Teórico (PTP)"},
            {"name": "SNG — Financiamentos", "description": "Sistema Nacional de Gravames (B3 Financiamentos) — Registro e baixa de alienação fiduciária sobre veículos (LAW-112)"},
            {"name": "SRO — Seguros & SUSEP", "description": "Sistema de Registro de Operações (B3 Seguros) — Registro de apólices, cosseguro conservado a 100% (LAW-113) e regulação de sinistros"}
        ]
    }
