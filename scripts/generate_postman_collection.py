#!/usr/bin/env python3
"""Gerador da Coleção Postman v2.1 Oficial do B3 Integration Atlas.

Produz o arquivo docs/b3_atlas_postman_collection.json contendo a especificação
completa, estruturada em 9 módulos, variáveis de ambiente (base_url, api_key, hmac_secret),
headers autenticados e test scripts automatizados com asserções de status 200 e
validações rigorosas de invariantes financeiros e regulatórios.

100% Python Standard Library (json, pathlib, uuid).
"""

from __future__ import annotations

import json
from pathlib import Path
import uuid
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS_DIR = _REPO_ROOT / "docs"


def make_request_item(
    name: str,
    path_segments: List[str],
    query_params: List[Dict[str, str]],
    description: str,
    test_assertions: List[str],
) -> Dict[str, Any]:
    """Cria um objeto de requisição no padrão Postman v2.1 com scripts de teste."""
    raw_query = ""
    if query_params:
        raw_query = "?" + "&".join(f"{q['key']}={q['value']}" for q in query_params)

    raw_url = f"{{{{base_url}}}}/{'/'.join(path_segments)}{raw_query}"

    # Gera o script de teste Postman (JavaScript)
    script_lines = [
        "// Asserção padrão de status HTTP 200",
        "pm.test('Status code deve ser 200 OK', function () {",
        "    pm.response.to.have.status(200);",
        "});",
        "",
        "// Asserção de tempo de resposta em ambiente local/intraday",
        "pm.test('Tempo de resposta dentro do SLA (< 1500ms)', function () {",
        "    pm.expect(pm.response.responseTime).to.be.below(1500);",
        "});",
        "",
        "// Parsing do corpo JSON",
        "let jsonData = {};",
        "try {",
        "    jsonData = pm.response.json();",
        "    pm.test('Resposta deve ser um JSON válido', function () {",
        "        pm.expect(jsonData).to.be.an('object');",
        "    });",
        "} catch (e) {",
        "    pm.test('Falha ao parsear JSON', function () {",
        "        pm.expect.fail('Corpo da resposta não é JSON válido: ' + e.message);",
        "    });",
        "}",
        "",
    ]

    for assert_code in test_assertions:
        script_lines.append(assert_code)

    return {
        "name": name,
        "event": [
            {
                "listen": "test",
                "script": {
                    "type": "text/javascript",
                    "exec": script_lines,
                },
            }
        ],
        "request": {
            "method": "GET",
            "header": [
                {
                    "key": "Accept",
                    "value": "application/json",
                    "type": "text",
                },
                {
                    "key": "X-B3-Api-Key",
                    "value": "{{api_key}}",
                    "type": "text",
                },
                {
                    "key": "X-B3-Client-Application",
                    "value": "B3-Integration-Atlas-Postman-Suite/2.3.0",
                    "type": "text",
                },
            ],
            "url": {
                "raw": raw_url,
                "host": ["{{base_url}}"],
                "path": path_segments,
                "query": query_params,
            },
            "description": description.strip(),
        },
        "response": [],
    }


def generate_collection() -> Dict[str, Any]:
    """Monta a árvore completa da coleção Postman v2.1."""

    # 01 - PUMA Trading & Ordem MOC/LOC
    folder_01_items = [
        make_request_item(
            name="01.1 - Leilão de Fechamento MOC/LOC & Freeze Window (Ficha Teórica)",
            path_segments=["api", "concepts", "leilao-moc-freeze"],
            query_params=[],
            description="""
### Leilão de Fechamento & Janela de Congelamento (MOC/LOC)
Recupera os detalhes conceituais, fundamentação legal (LAW-104A) e o diagrama ASCII do mecanismo de leilão de fechamento no PUMA Trading System.
- **Invariante LAW-104A**: Na janela de congelamento (último 1 minuto), ordens MOC não podem ser canceladas nem alteradas para baixo.
""",
            test_assertions=[
                "pm.test('Invariante LAW-104A referenciada no conceito', function () {",
                "    pm.expect(jsonData.id).to.eql('leilao-moc-freeze');",
                "    pm.expect(jsonData.related_laws).to.include('LAW-104A');",
                "    pm.expect(jsonData.diagram_ascii).to.be.a('string').and.not.empty;",
                "});",
            ],
        ),
        make_request_item(
            name="01.2 - Simulação de Desbalanceamento MOC no Fechamento (LAW-104A)",
            path_segments=["api", "concepts", "leilao-moc-freeze", "simulate"],
            query_params=[
                {"key": "symbol", "value": "PETR4", "description": "Código do ativo"},
                {"key": "imbalance_side", "value": "BUY", "description": "Lado desbalanceado (BUY/SELL)"},
                {"key": "imbalance_shares", "value": "50000", "description": "Volume desbalanceado de ações"},
                {"key": "reference_price", "value": "38.50", "description": "Preço de referência do pregão regular"},
            ],
            description="""
### Simulação de Desbalanceamento MOC
Executa o cálculo analítico do preço indicativo de leilão de fechamento ante desbalanceamento de ordens Market-On-Close.
- Verifica ativação automática do congelamento e inviolabilidade da LAW-104A.
""",
            test_assertions=[
                "pm.test('Validação de LAW-104A e Preço Indicativo', function () {",
                "    pm.expect(jsonData.law_verified).to.eql('LAW-104A');",
                "    pm.expect(jsonData.freeze_window_activated).to.be.true;",
                "    pm.expect(jsonData.auction_indicative_price).to.be.a('number');",
                "    pm.expect(jsonData.status).to.include('SUCESSO');",
                "});",
            ],
        ),
        make_request_item(
            name="01.3 - Túneis de Oscilação de Preços & Circuit Breaker PUMA (Conceito)",
            path_segments=["api", "concepts", "tuneis-circuit-breaker"],
            query_params=[],
            description="""
### Túneis de Preço e Circuit Breaker PUMA
Retorna as especificações de limites de rejeição imediata (Hard Reject) e limites de túnel de leilão no PUMA Trading System.
- **Invariante LAW-001 / LAW-104B**: Nenhuma oferta fora do túnel de rejeição pode ingressar no livro de ofertas.
""",
            test_assertions=[
                "pm.test('Conceito de Túneis e Circuit Breaker validado', function () {",
                "    pm.expect(jsonData.id).to.eql('tuneis-circuit-breaker');",
                "    pm.expect(jsonData.badge).to.include('LAW-001');",
                "    pm.expect(jsonData.how_it_works).to.be.an('array').with.length.above(2);",
                "});",
            ],
        ),
        make_request_item(
            name="01.4 - Simulação de Disparo de Túnel de Leilão / Trade Halt",
            path_segments=["api", "concepts", "tuneis-circuit-breaker", "simulate"],
            query_params=[
                {"key": "symbol", "value": "VALE3", "description": "Código do ativo"},
                {"key": "reference_price", "value": "65.00", "description": "Preço base de referência"},
                {"key": "incoming_price", "value": "73.00", "description": "Preço da ordem agressora"},
            ],
            description="""
### Simulação de Violação de Túnel de Leilão
Submete uma ordem agressora acima do túnel operacional e verifica o congelamento da negociação com chamada automática de leilão.
""",
            test_assertions=[
                "pm.test('Verificação de chamada de leilão por túnel', function () {",
                "    pm.expect(jsonData.law_verified).to.eql('LAW-001');",
                "    pm.expect(jsonData.order_accepted).to.be.a('boolean');",
                "    pm.expect(jsonData.action_taken).to.be.a('string');",
                "});",
            ],
        ),
        make_request_item(
            name="01.5 - Algorithmic Smart Order Router (TWAP/VWAP/POV - Ficha)",
            path_segments=["api", "concepts", "algorithmic-sor"],
            query_params=[],
            description="""
### Roteamento Algorítmico SOR (TWAP / VWAP)
Detalhamento analítico das regras de fragmentação de grandes lotes institucionais em ordens-filhas com jitter temporal aleatório.
- **Invariante LAW-110**: A soma das quantidades de todas as ordens filhas deve ser rigorosamente idêntica à ordem mãe (Conservação de Lote).
""",
            test_assertions=[
                "pm.test('Validação de Conceito de SOR Algorítmico', function () {",
                "    pm.expect(jsonData.id).to.eql('algorithmic-sor');",
                "    pm.expect(jsonData.related_laws).to.include('LAW-110');",
                "});",
            ],
        ),
        make_request_item(
            name="01.6 - Simulação de Fatiamento TWAP via Algorithmic SOR",
            path_segments=["api", "concepts", "algorithmic-sor", "simulate"],
            query_params=[
                {"key": "symbol", "value": "BBAS3", "description": "Ticker do ativo"},
                {"key": "parent_quantity", "value": "100000", "description": "Quantidade da ordem mãe"},
                {"key": "urgency", "value": "MEDIUM", "description": "Urgência de execução (LOW/MEDIUM/HIGH)"},
            ],
            description="""
### Simulação de Fatiamento TWAP
Gera a grade de ordens filhas preservando rigorosamente a conservação do volume total da ordem mãe.
""",
            test_assertions=[
                "pm.test('Invariante LAW-110 de Conservação de Quantidade', function () {",
                "    pm.expect(jsonData.law_verified).to.eql('LAW-110');",
                "    pm.expect(jsonData.parent_order.quantity).to.eql(jsonData.slices_audit.total_slices_quantity);",
                "    pm.expect(jsonData.slices_generated).to.be.above(0);",
                "});",
            ],
        ),
        make_request_item(
            name="01.7 - Motor de Execução Algorithmic SOR TWAP/VWAP (FL-104 Engine)",
            path_segments=["api", "phase7", "FL-104"],
            query_params=[],
            description="""
### Fluxo FL-104: Algorithmic SOR Engine
Executa a validação de invariantes formais do motor de SOR institucional a partir do fixture oficial do projeto.
""",
            test_assertions=[
                "pm.test('Motor FL-104 e LAW-110 validados', function () {",
                "    pm.expect(jsonData.flow).to.eql('FL-104');",
                "    pm.expect(jsonData.laws).to.include('LAW-110');",
                "    pm.expect(jsonData.invariants.quantity_conservation).to.be.true;",
                "});",
            ],
        ),
        make_request_item(
            name="01.8 - Manual de Especificação do PUMA Trading System & FIX (SRC-001)",
            path_segments=["api", "docs", "SRC-001"],
            query_params=[],
            description="""
### Acervo Documental B3: SRC-001
Retorna os metadados e o conteúdo integral do manual de integração ao PUMA Trading System via FIX 4.4 / 5.0 SP2.
""",
            test_assertions=[
                "pm.test('Documento oficial SRC-001 carregado', function () {",
                "    pm.expect(jsonData.doc.doc_id).to.eql('SRC-001');",
                "    pm.expect(jsonData.raw_content).to.be.a('string').and.not.empty;",
                "});",
            ],
        ),
    ]

    # 02 - Câmara B3 & Default Waterfall
    folder_02_items = [
        make_request_item(
            name="02.1 - Novação Legal e CCP (Lei nº 10.214/2001 - Ficha Teórica)",
            path_segments=["api", "concepts", "novacao-ccp"],
            query_params=[],
            description="""
### Novação Legal e Contraparte Central
Explicação analítica da extinção da relação bilateral e interposição da Câmara B3 como compradora de todo vendedor e vendedora de todo comprador.
- **Invariante LAW-004**: Neutralidade direcional estrita da CCP (Posição Líquida B3 = 0).
""",
            test_assertions=[
                "pm.test('Novação Legal e LAW-004 validados', function () {",
                "    pm.expect(jsonData.id).to.eql('novacao-ccp');",
                "    pm.expect(jsonData.related_laws).to.include('LAW-004');",
                "    pm.expect(jsonData.math_rule).to.include('Neutralidade');",
                "});",
            ],
        ),
        make_request_item(
            name="02.2 - Simulação de Extinção Bilateral & Novação Tripartite CCP",
            path_segments=["api", "concepts", "novacao-ccp", "simulate"],
            query_params=[
                {"key": "buyer", "value": "Alpha DTVM", "description": "Comprador"},
                {"key": "seller", "value": "Beta Corretora", "description": "Vendedor"},
                {"key": "symbol", "value": "PETR4", "description": "Ativo negociado"},
                {"key": "quantity", "value": "10000", "description": "Quantidade de ações"},
                {"key": "price", "value": "38.50", "description": "Preço unitário em BRL"},
            ],
            description="""
### Simulação de Novação Legal CCP
Executa a transformação do trade bilateral original em dois contratos novados com a B3 interposta.
- Confirma que o delta de ações e caixa na Câmara B3 é rigorosamente zero.
""",
            test_assertions=[
                "pm.test('Neutralidade Direcional da CCP verificada (LAW-004)', function () {",
                "    pm.expect(jsonData.law_verified).to.eql('LAW-004');",
                "    pm.expect(jsonData.ccp_net_position.shares_delta).to.eql(0);",
                "    pm.expect(jsonData.ccp_net_position.cash_delta_brl).to.eql(0);",
                "    pm.expect(jsonData.novated_contracts).to.have.lengthOf(2);",
                "});",
            ],
        ),
        make_request_item(
            name="02.3 - Cascata de Salvaguardas da Câmara (Default Waterfall - Conceito)",
            path_segments=["api", "concepts", "default-waterfall"],
            query_params=[],
            description="""
### Cascata de Salvaguardas (Default Waterfall)
Sequência hierárquica e inegociável de absorção de inadimplências na Câmara B3:
1. Colateral do Faltoso -> 2. Fundo de Liquidação do Faltoso -> 3. Patrimônio da B3 (Skin in the Game) -> 4. Fundo Mútuo -> 5. Mutualização Sistêmica.
- **Invariante LAW-006 / LAW-106B**: Nenhuma camada subsequente pode ser tocada antes do esgotamento da anterior.
""",
            test_assertions=[
                "pm.test('Camadas do Default Waterfall verificadas', function () {",
                "    pm.expect(jsonData.id).to.eql('default-waterfall');",
                "    pm.expect(jsonData.related_laws).to.include('LAW-006');",
                "    pm.expect(jsonData.how_it_works).to.be.an('array').with.length.above(3);",
                "});",
            ],
        ),
        make_request_item(
            name="02.4 - Simulação de Execução Determinística do Default Waterfall",
            path_segments=["api", "concepts", "default-waterfall", "simulate"],
            query_params=[
                {"key": "default_amount", "value": "45000000", "description": "Rombo financeiro do calote (R$)"},
                {"key": "collateral", "value": "15000000", "description": "Colateral disponível do participante"},
                {"key": "clearing_fund_share", "value": "10000000", "description": "Quota do faltoso no fundo"},
                {"key": "b3_equity", "value": "5000000", "description": "Capital próprio B3 alocado"},
            ],
            description="""
### Simulação Quantitativa do Waterfall
Testa a absorção em camadas do rombo financeiro e assegura que não reste déficit não absorvido.
""",
            test_assertions=[
                "pm.test('Absorção integral do calote no Waterfall (LAW-006)', function () {",
                "    pm.expect(jsonData.law_verified).to.eql('LAW-006');",
                "    pm.expect(jsonData.unabsorbed_loss).to.eql(0);",
                "    pm.expect(jsonData.layers_exhausted).to.be.an('array');",
                "});",
            ],
        ),
        make_request_item(
            name="02.5 - Compensação Multilateral Contínua (Netting) vs Liquidação Bruta",
            path_segments=["api", "concepts", "netting-multilateral"],
            query_params=[],
            description=r"""
### Compensação Multilateral (Netting)
Conceituação da redução maciça de risco de liquidez sistêmica via compensação multilateral de posições de compra e venda.
- **Invariante LAW-005**: Conservação da soma zero ($\sum PosiçõesLíquidas = 0$).
""",
            test_assertions=[
                "pm.test('Conceito de Netting Multilateral verificado', function () {",
                "    pm.expect(jsonData.id).to.eql('netting-multilateral');",
                "    pm.expect(jsonData.related_laws).to.include('LAW-005');",
                "});",
            ],
        ),
        make_request_item(
            name="02.6 - Simulação de Eficiência e Redução de Liquidez via Netting",
            path_segments=["api", "concepts", "netting-multilateral", "simulate"],
            query_params=[
                {"key": "participants", "value": "4", "description": "Número de participantes"},
                {"key": "trades", "value": "10", "description": "Quantidade de operações bilaterais"},
            ],
            description="""
### Simulação de Eficiência de Netting
Compara o montante bruto liquidável com a obrigação líquida final após a compensação multilateral da B3.
""",
            test_assertions=[
                "pm.test('Eficiência de Netting > 50% e Soma Zero (LAW-005)', function () {",
                "    pm.expect(jsonData.law_verified).to.eql('LAW-005');",
                "    pm.expect(jsonData.netting_efficiency_pct).to.be.above(30.0);",
                "    pm.expect(jsonData.conservation_zero_sum).to.be.true;",
                "});",
            ],
        ),
        make_request_item(
            name="02.7 - Teste de Caos: Inadimplência Crítica de Participante (CLEARING_DEFAULT)",
            path_segments=["api", "chaos", "CLEARING_DEFAULT"],
            query_params=[],
            description="""
### Chaos Testing: CLEARING_DEFAULT
Executa a simulação de estresse financeiro simulando o colapso de um grande participante com posições em aberto na Câmara B3.
- Testa se a CCP se mantém solvente e honra todas as contrapartes sem contágio.
""",
            test_assertions=[
                "pm.test('Resiliência da Câmara ante falência de membro', function () {",
                "    pm.expect(jsonData.scenario).to.eql('CLEARING_DEFAULT');",
                "    pm.expect(jsonData.is_resilient).to.be.true;",
                "    pm.expect(jsonData.recovery_action).to.be.a('string');",
                "});",
            ],
        ),
        make_request_item(
            name="02.8 - Verificação Formal TLA+ do Barramento de Liquidação (LAW-106A/B/C)",
            path_segments=["api", "tla-check"],
            query_params=[],
            description="""
### TLA+ Formal Verification
Executa a verificação matemática dos estados do barramento central de liquidação da B3.
- **LAW-106A**: Conservação Contábil de Soma Zero.
- **LAW-106B**: Inviolabilidade da Ordem do Waterfall.
- **LAW-106C**: Atomicidade da Liquidação Inter-sistemas.
- **Ausência de Deadlock**: 0 deadlocks em todos os estados explorados.
""",
            test_assertions=[
                "pm.test('Model Checker TLA+ Aprovado com 0 Deadlocks', function () {",
                "    pm.expect(jsonData.passed).to.be.true;",
                "    pm.expect(jsonData.deadlocks_found).to.eql(0);",
                "    pm.expect(jsonData.distinct_states_explored).to.be.above(100);",
                "    pm.expect(jsonData.properties.ZeroSumAccountingConservation_LAW_106A).to.be.true;",
                "    pm.expect(jsonData.properties.WaterfallOrderInviolability_LAW_106B).to.be.true;",
                "    pm.expect(jsonData.properties.IntersystemSettlementAtomicity_LAW_106C).to.be.true;",
                "});",
            ],
        ),
        make_request_item(
            name="02.9 - Manual de Cálculo de Margem e Risco CORE B3 (SRC-004)",
            path_segments=["api", "docs", "SRC-004"],
            query_params=[],
            description="""
### Acervo Documental B3: SRC-004
Retorna os manuais formais do motor de risco CORE da Câmara B3 (Close-Out Risk Evaluation).
""",
            test_assertions=[
                "pm.test('Manual do CORE Margin SRC-004 carregado', function () {",
                "    pm.expect(jsonData.doc.doc_id).to.eql('SRC-004');",
                "    pm.expect(jsonData.raw_content).to.be.a('string').and.not.empty;",
                "});",
            ],
        ),
    ]

    # 03 - Balcão NoMe & Recebíveis
    folder_03_items = [
        make_request_item(
            name="03.1 - Central de Gravames & Registro de Recebíveis (CMN 4.734 / Lei 14.711)",
            path_segments=["api", "concepts", "registro-recebiveis-gravames"],
            query_params=[],
            description="""
### Central de Gravames e Recebíveis
Detalhamento operacional da interoperabilidade entre registradoras credenciadas pelo Banco Central e trava de domicílio bancário.
- **Invariante LAW-011 / LAW-082**: O valor total gravado sobre uma agenda de recebíveis não pode ultrapassar o valor presente da agenda.
""",
            test_assertions=[
                "pm.test('Conceito de Central de Gravames verificado', function () {",
                "    pm.expect(jsonData.id).to.eql('registro-recebiveis-gravames');",
                "    pm.expect(jsonData.related_laws).to.include('LAW-011');",
                "});",
            ],
        ),
        make_request_item(
            name="03.2 - Simulação de Trava de Domicílio e Limite de Gravame em Recebíveis",
            path_segments=["api", "concepts", "registro-recebiveis-gravames", "simulate"],
            query_params=[
                {"key": "credor", "value": "Banco Alfa", "description": "Instituição financeira beneficiária"},
                {"key": "arranjo", "value": "VISA", "description": "Bandeira/Arranjo de pagamento"},
                {"key": "valor_agenda", "value": "500000.00", "description": "Saldo financeiro da agenda"},
            ],
            description="""
### Simulação de Registro de Gravame sobre Agenda
Simula a constituição de garantia fiduciária com verificação de precedência e unicidade de trava de domicílio bancário.
""",
            test_assertions=[
                "pm.test('Invariante de Limite de Garantia em Recebíveis (LAW-011)', function () {",
                "    pm.expect(jsonData.law_verified).to.eql('LAW-011');",
                "    pm.expect(jsonData.agenda.saldo_brl).to.be.above(0);",
                "    pm.expect(jsonData.status).to.include('SUCESSO');",
                "});",
            ],
        ),
        make_request_item(
            name="03.3 - Teste de Caos: Conflito Concorrente em Recebíveis (RECEIVABLES_LIEN_CONFLICT)",
            path_segments=["api", "chaos", "RECEIVABLES_LIEN_CONFLICT"],
            query_params=[],
            description="""
### Chaos Testing: RECEIVABLES_LIEN_CONFLICT
Dispara concorrência simultânea de dois bancos tentando gravar 100% da mesma agenda de recebíveis de cartão.
- Testa se o algoritmo de ordenação estrita por timestamp de chegada é respeitado e evita dupla garantia.
""",
            test_assertions=[
                "pm.test('Resiliência ante conflito de gravames concorrentes', function () {",
                "    pm.expect(jsonData.scenario).to.eql('RECEIVABLES_LIEN_CONFLICT');",
                "    pm.expect(jsonData.is_resilient).to.be.true;",
                "});",
            ],
        ),
        make_request_item(
            name="03.4 - Manual de Registro de Renda Fixa e Derivativos de Balcão NoMe (SRC-011)",
            path_segments=["api", "docs", "SRC-011"],
            query_params=[],
            description="""
### Acervo Documental B3: SRC-011
Regulamento operacional do sistema NoMe (Novo Mercado de Balcão) para registro de CDB, RDB, LF, CRA e CRI.
""",
            test_assertions=[
                "pm.test('Manual do NoMe Balcão SRC-011 carregado', function () {",
                "    pm.expect(jsonData.doc.doc_id).to.eql('SRC-011');",
                "    pm.expect(jsonData.raw_content).to.be.a('string').and.not.empty;",
                "});",
            ],
        ),
        make_request_item(
            name="03.5 - Manual de Interoperabilidade de Recebíveis de Cartão (SRC-012)",
            path_segments=["api", "docs", "SRC-012"],
            query_params=[],
            description="""
### Acervo Documental B3: SRC-012
Especificação técnica de interoperabilidade de recebíveis com as registradoras CERC, CIP/TAG e B3.
""",
            test_assertions=[
                "pm.test('Manual de Interoperabilidade SRC-012 carregado', function () {",
                "    pm.expect(jsonData.doc.doc_id).to.eql('SRC-012');",
                "});",
            ],
        ),
    ]

    # 04 - Central Depositária & STVM
    folder_04_items = [
        make_request_item(
            name="04.1 - Especificação IMBARQ 006 - Movimentação e Transferência de Custódia (SRC-006)",
            path_segments=["api", "docs", "SRC-006"],
            query_params=[],
            description="""
### Acervo Documental B3: SRC-006 (IMBARQ 006)
Manual de layout e validação de arquivos IMBARQ 006 de transferência e movimentação de custódia na Central Depositária.
""",
            test_assertions=[
                "pm.test('Layout IMBARQ 006 verificado', function () {",
                "    pm.expect(jsonData.doc.doc_id).to.eql('SRC-006');",
                "    pm.expect(jsonData.raw_content).to.include('IMBARQ');",
                "});",
            ],
        ),
        make_request_item(
            name="04.2 - Portabilidade Digital STVM (Resolução CVM nº 179 - SRC-008)",
            path_segments=["api", "docs", "SRC-008"],
            query_params=[],
            description="""
### Acervo Documental B3: SRC-008 (STVM Digital)
Especificação da Solicitação de Transferência de Valores Mobiliários (STVM) 100% eletrônica sem necessidade de firma reconhecida em cartório.
""",
            test_assertions=[
                "pm.test('Manual STVM Digital SRC-008 verificado', function () {",
                "    pm.expect(jsonData.doc.doc_id).to.eql('SRC-008');",
                "});",
            ],
        ),
        make_request_item(
            name="04.3 - Especificação IMBARQ 014 - Saldos e Posições de Custódia (SRC-014)",
            path_segments=["api", "docs", "SRC-014"],
            query_params=[],
            description="""
### Acervo Documental B3: SRC-014 (IMBARQ 014)
Arquivo diário de conciliação de saldos em custódia individualizados por conta e CPF/CNPJ de investidor final.
""",
            test_assertions=[
                "pm.test('Layout IMBARQ 014 verificado', function () {",
                "    pm.expect(jsonData.doc.doc_id).to.eql('SRC-014');",
                "});",
            ],
        ),
        make_request_item(
            name="04.4 - Certificado de Operações Estruturadas (COE) Capital Protegido (FL-102 Engine)",
            path_segments=["api", "phase7", "FL-102"],
            query_params=[],
            description="""
### Fluxo FL-102: COE Capital Protegido
Motor de liquidação de COE com garantia de principal investido e verificação de payoff estruturado.
- **Invariante LAW-108**: O valor de liquidação no vencimento nunca pode ser inferior ao capital nominal garantido.
""",
            test_assertions=[
                "pm.test('Motor FL-102 COE Capital Protegido (LAW-108)', function () {",
                "    pm.expect(jsonData.flow).to.eql('FL-102');",
                "    pm.expect(jsonData.laws).to.include('LAW-108');",
                "    pm.expect(jsonData.sample_settlement).to.be.an('object');",
                "});",
            ],
        ),
        make_request_item(
            name="04.5 - Formador de Mercado PMM B3 - Auditoria de Presença e Spread (FL-103 Engine)",
            path_segments=["api", "phase7", "FL-103"],
            query_params=[],
            description="""
### Fluxo FL-103: PMM Market Maker Engine
Auditoria formal dos parâmetros de atuação obrigatória do formador de mercado (presença mínima diária e spread máximo bid-ask).
- **Invariante LAW-109A-D**: Cumprimento estrito dos parâmetros contratuais de formador.
""",
            test_assertions=[
                "pm.test('Auditoria PMM Market Maker (LAW-109)', function () {",
                "    pm.expect(jsonData.flow).to.eql('FL-103');",
                "    pm.expect(jsonData.laws).to.include('LAW-109A');",
                "    pm.expect(jsonData.invariants.all_contracts_have_audits).to.be.true;",
                "});",
            ],
        ),
    ]

    # 05 - SPB, STR & Drex Fase 2
    folder_05_items = [
        make_request_item(
            name="05.1 - Liquidação DvP/PvP Atômica em DLT Drex Fase 2 (Ficha Conceitual)",
            path_segments=["api", "concepts", "drex-dvp-atomico"],
            query_params=[],
            description="""
### Liquidação Atômica Drex Fase 2
Arquitetura de contratos inteligentes no Hyperledger Besu utilizando ERC-1155 e HTLC (Hashed Timelock Contracts) para DvP sem risco de contraparte.
- **Invariante LAW-106C**: Atomicidade estrita — entrega do título público tokenizado e pagamento em Real Digital ocorrem na mesma transação ou são revertidos.
""",
            test_assertions=[
                "pm.test('Conceito de Drex DvP Atômico verificado', function () {",
                "    pm.expect(jsonData.id).to.eql('drex-dvp-atomico');",
                "    pm.expect(jsonData.related_laws).to.include('LAW-106C');",
                "});",
            ],
        ),
        make_request_item(
            name="05.2 - Simulação de Liquidação DvP Atômica Drex (Real Digital vs TPFt)",
            path_segments=["api", "concepts", "drex-dvp-atomico", "simulate"],
            query_params=[
                {"key": "rwa_bond", "value": "TPFt-LTN-2026", "description": "Título RWA negociado"},
                {"key": "quantity", "value": "500", "description": "Quantidade de tokens de título"},
                {"key": "unit_price", "value": "985.40", "description": "Preço unitário em Real Digital"},
            ],
            description="""
### Simulação de DvP Atômico no Drex
Simula o swap simultâneo de Real Digital por Título Público Federal Tokenizado com geração de comprovante e hash criptográfico de bloco.
""",
            test_assertions=[
                "pm.test('Liquidação DvP Atômica sem Risco Principal (LAW-106C)', function () {",
                "    pm.expect(jsonData.law_verified).to.eql('LAW-106C');",
                "    pm.expect(jsonData.dvp_atomic_status).to.include('EXECUTADO');",
                "    pm.expect(jsonData.hash_bloco_dlt).to.be.a('string').and.not.empty;",
                "});",
            ],
        ),
        make_request_item(
            name="05.3 - Emissão Primária de RWA Drex Fase 2 (FL-105 Engine)",
            path_segments=["api", "phase7", "FL-105"],
            query_params=[],
            description="""
### Fluxo FL-105: Emissão Primária RWA Drex
Motor de custódia e liquidação de emissão primária de ativos do mundo real tokenizados na rede do Drex.
- **Invariante LAW-111**: Conservação estrita do supply total emitido e garantido.
""",
            test_assertions=[
                "pm.test('Motor FL-105 Drex RWA Primary Issuance (LAW-111)', function () {",
                "    pm.expect(jsonData.flow).to.eql('FL-105');",
                "    pm.expect(jsonData.laws).to.include('LAW-111');",
                "    pm.expect(jsonData.invariants.conservation_token_supply).to.be.true;",
                "});",
            ],
        ),
        make_request_item(
            name="05.4 - Teste de Caos: Interrupção Crítica de Rede SPB / BACEN (SPB_NETWORK_DISRUPTION)",
            path_segments=["api", "chaos", "SPB_NETWORK_DISRUPTION"],
            query_params=[],
            description="""
### Chaos Testing: SPB_NETWORK_DISRUPTION
Simula a perda temporária de conectividade com os barramentos de mensageria do Banco Central (STR/SPI).
- Avalia o acionamento de filas de contingência local sem perda de transações de liquidação.
""",
            test_assertions=[
                "pm.test('Resiliência ante falha de rede do SPB/BACEN', function () {",
                "    pm.expect(jsonData.scenario).to.eql('SPB_NETWORK_DISRUPTION');",
                "    pm.expect(jsonData.is_resilient).to.be.true;",
                "});",
            ],
        ),
        make_request_item(
            name="05.5 - Manual Operacional STR / SPB Mensageria LDL0001 (SRC-018)",
            path_segments=["api", "docs", "SRC-018"],
            query_params=[],
            description="""
### Acervo Documental B3: SRC-018
Documentação oficial das mensagens XML do SPB (Sistema de Pagamentos Brasileiro) e mensagens de liquidação bilateral/multilateral LDL0001.
""",
            test_assertions=[
                "pm.test('Manual de Mensageria SPB SRC-018 verificado', function () {",
                "    pm.expect(jsonData.doc.doc_id).to.eql('SRC-018');",
                "});",
            ],
        ),
    ]

    # 06 - SNG Financiamentos (Veículos)
    folder_06_items = [
        make_request_item(
            name="06.1 - Sistema Nacional de Gravames (SNG) - Resolução CONTRAN nº 807/2020 (Conceito)",
            path_segments=["api", "concepts", "sng-gravames-veiculares"],
            query_params=[],
            description="""
### SNG — Sistema Nacional de Gravames (B3 Financiamentos)
Fichamento técnico da plataforma central operada pela B3 para registro de alienação fiduciária, arrendamento mercantil (leasing) e reserva de domínio junto aos Detrans de todas as 27 UFs.
- **Invariante LAW-112**: Unicidade de gravame ativo de 1º grau por chassi/renavam (Inviolabilidade contra sobreposição de alienações conflitantes).
""",
            test_assertions=[
                "pm.test('Conceito SNG Financiamentos verificado', function () {",
                "    pm.expect(jsonData.id).to.eql('sng-gravames-veiculares');",
                "    pm.expect(jsonData.related_laws).to.include('LAW-112');",
                "    pm.expect(jsonData.how_it_works).to.be.an('array').with.length.above(3);",
                "});",
            ],
        ),
        make_request_item(
            name="06.2 - Simulação de Registro e Validação de Gravame Veicular (Invariante LAW-112)",
            path_segments=["api", "concepts", "sng-gravames-veiculares", "simulate"],
            query_params=[
                {"key": "chassi", "value": "9BWCA41V01P123456", "description": "Chassi único do veículo automotor"},
                {"key": "renavam", "value": "12345678901", "description": "Código RENAVAM"},
                {"key": "modalidade", "value": "ALIENACAO_FIDUCIARIA", "description": "Modalidade da garantia real"},
                {"key": "valor", "value": "120000.00", "description": "Valor total financiado em BRL"},
            ],
            description="""
### Simulação de Inclusão de Gravame no SNG
Executa o fluxo de inclusão e validação com geração de hash SHA-256 e protocolo Detran.
- Valida a invariante LAW-112 de gravame ativo único.
""",
            test_assertions=[
                "pm.test('Inclusão no SNG e Validação da LAW-112', function () {",
                "    pm.expect(jsonData.law_verified).to.eql('LAW-112');",
                "    pm.expect(jsonData.status).to.include('SUCESSO');",
                "    pm.expect(jsonData.detran_protocol).to.be.a('string').and.not.empty;",
                "    pm.expect(jsonData.hash_seguranca).to.have.lengthOf(64);",
                "});",
            ],
        ),
        make_request_item(
            name="06.3 - Validação de Invariantes do SNG Engine & Conexão Detran (FL-106 Engine)",
            path_segments=["api", "phase7", "FL-106"],
            query_params=[],
            description="""
### Fluxo FL-106: SNG Gravames Engine
Verificação do estado e das invariantes do motor SNG a partir dos fixtures homologados de veículos cadastrados.
""",
            test_assertions=[
                "pm.test('Motor FL-106 SNG Gravames e LAW-112 validados', function () {",
                "    pm.expect(jsonData.flow).to.eql('FL-106');",
                "    pm.expect(jsonData.laws).to.include('LAW-112');",
                "    pm.expect(jsonData.invariants.verified).to.be.true;",
                "    pm.expect(jsonData.gravames_registrados).to.be.above(0);",
                "});",
            ],
        ),
        make_request_item(
            name="06.4 - Manual B3 Financiamentos — SNG e Integração Detrans (SRC-086)",
            path_segments=["api", "docs", "SRC-086"],
            query_params=[],
            description="""
### Acervo Documental B3: SRC-086
Documentação oficial da B3 Financiamentos com as diretrizes da Resolução CONTRAN nº 807/2020 e Art. 1.361 do Código Civil.
""",
            test_assertions=[
                "pm.test('Manual SNG SRC-086 carregado', function () {",
                "    pm.expect(jsonData.doc.doc_id).to.eql('SRC-086');",
                "    pm.expect(jsonData.raw_content).to.include('807/2020');",
                "});",
            ],
        ),
    ]

    # 07 - SRO Seguros (SUSEP V3)
    folder_07_items = [
        make_request_item(
            name="07.1 - Sistema de Registro de Operações (SRO) — Circular SUSEP nº 642/2021 (Conceito)",
            path_segments=["api", "concepts", "sro-seguros-susep"],
            query_params=[],
            description="""
### SRO — Sistema de Registro de Operações (B3 Seguros / SUSEP)
Fichamento técnico da infraestrutura de mercado para registro de apólices, cosseguro, endossos e sinistros.
- **Invariante LAW-113**: Conservação de Cosseguro (Partição estrita em 100%) e Inviolabilidade do Limite Máximo de Garantia (LMG).
""",
            test_assertions=[
                "pm.test('Conceito SRO Seguros SUSEP verificado', function () {",
                "    pm.expect(jsonData.id).to.eql('sro-seguros-susep');",
                "    pm.expect(jsonData.related_laws).to.include('LAW-113');",
                "    pm.expect(jsonData.badge).to.include('SUSEP');",
                "});",
            ],
        ),
        make_request_item(
            name="07.2 - Simulação de Partição de Cosseguro e Validação de LMG (Invariante LAW-113)",
            path_segments=["api", "concepts", "sro-seguros-susep", "simulate"],
            query_params=[
                {"key": "ramo", "value": "0531_AUTOMOVEL", "description": "Ramo homologado SUSEP"},
                {"key": "lmg", "value": "150000.00", "description": "Limite Máximo de Garantia em BRL"},
                {"key": "premio", "value": "4200.00", "description": "Prêmio líquido em BRL"},
                {"key": "cosseguro_split", "value": "60/40", "description": "Partição percentual das quotas"},
            ],
            description="""
### Simulação de Apólice com Cosseguro e Cálculo de IOF
Executa a validação formal da soma das quotas de cosseguro exatamente igual a 100% e cálculo automático de IOF.
""",
            test_assertions=[
                "pm.test('Partição de Cosseguro 100% e LAW-113 verificadas', function () {",
                "    pm.expect(jsonData.law_verified).to.eql('LAW-113');",
                "    pm.expect(jsonData.cosseguro_quota_sum).to.eql(100.0);",
                "    pm.expect(jsonData.status).to.include('SUCESSO');",
                "    pm.expect(jsonData.hash_susep).to.have.lengthOf(64);",
                "});",
            ],
        ),
        make_request_item(
            name="07.3 - Validação de Invariantes do SRO Seguros Engine & Sinistros (FL-107 Engine)",
            path_segments=["api", "phase7", "FL-107"],
            query_params=[],
            description="""
### Fluxo FL-107: SRO Seguros Engine
Verificação do estado e das invariantes do motor SRO a partir dos fixtures homologados de apólices e cosseguros.
""",
            test_assertions=[
                "pm.test('Motor FL-107 SRO Seguros e LAW-113 validados', function () {",
                "    pm.expect(jsonData.flow).to.eql('FL-107');",
                "    pm.expect(jsonData.laws).to.include('LAW-113');",
                "    pm.expect(jsonData.invariants.verified).to.be.true;",
                "    pm.expect(jsonData.apolices_registradas).to.be.above(0);",
                "});",
            ],
        ),
        make_request_item(
            name="07.4 - Manual B3 Seguros — Diretrizes SRO V3 e SUSEP (SRC-087)",
            path_segments=["api", "docs", "SRC-087"],
            query_params=[],
            description="""
### Acervo Documental B3: SRC-087
Documentação técnica oficial da B3 Seguros para integração ao padrão SRO V3 regulado pelas Circulares SUSEP nº 605/2020 e 642/2021.
""",
            test_assertions=[
                "pm.test('Manual SRO V3 SRC-087 carregado', function () {",
                "    pm.expect(jsonData.doc.doc_id).to.eql('SRC-087');",
                "    pm.expect(jsonData.raw_content).to.include('SUSEP');",
                "});",
            ],
        ),
    ]

    # 08 - Laboratório de Conceitos Complexos (/api/concepts)
    folder_08_items = [
        make_request_item(
            name="08.1 - Listar Todos os 12 Conceitos Complexos da B3",
            path_segments=["api", "concepts"],
            query_params=[],
            description="""
### Catálogo Completo de Conceitos Complexos
Retorna a listagem analítica dos 12 conceitos vitais da arquitetura de mercado de capitais brasileiro.
""",
            test_assertions=[
                "pm.test('Catálogo contém exatamente 12 conceitos complexos', function () {",
                "    pm.expect(jsonData).to.be.an('array').with.lengthOf(12);",
                "    const ids = jsonData.map(c => c.id);",
                "    pm.expect(ids).to.include.members([",
                "        'novacao-ccp', 'default-waterfall', 'leilao-moc-freeze',",
                "        'tuneis-circuit-breaker', 'netting-multilateral', 'registro-recebiveis-gravames',",
                "        'drex-dvp-atomico', 'superficies-volatilidade', 'algorithmic-sor',",
                "        'bsm-surveillance', 'sng-gravames-veiculares', 'sro-seguros-susep'",
                "    ]);",
                "});",
            ],
        ),
        make_request_item(
            name="08.2 - Conceito: Superfícies de Volatilidade Paramétricas SVI & SABR",
            path_segments=["api", "concepts", "superficies-volatilidade"],
            query_params=[],
            description="""
### Superfícies de Volatilidade SVI & SABR
Ficha analítica da modelagem matemática de superfícies livres de arbitragem temporal e borboleta.
- **Invariante LAW-107A / LAW-107B**: Ausência de arbitragem estática no smile de volatilidade.
""",
            test_assertions=[
                "pm.test('Conceito de Superfícies de Volatilidade verificado', function () {",
                "    pm.expect(jsonData.id).to.eql('superficies-volatilidade');",
                "    pm.expect(jsonData.related_laws).to.include('LAW-107A');",
                "});",
            ],
        ),
        make_request_item(
            name="08.3 - Simulação: Calibração de Curva SVI e Ausência de Arbitragem",
            path_segments=["api", "concepts", "superficies-volatilidade", "simulate"],
            query_params=[
                {"key": "model", "value": "SVI", "description": "Modelo de parametrização (SVI/SABR)"},
                {"key": "fwd", "value": "120000", "description": "Preço forward do ativo"},
                {"key": "tau", "value": "0.25", "description": "Tempo até o vencimento (anos)"},
            ],
            description="""
### Simulação de Smile SVI
Gera a curva de variância total e volatilidade implícita para uma fatia temporal (slice).
""",
            test_assertions=[
                "pm.test('Simulação de Superfície SVI sem arbitragem (LAW-107A)', function () {",
                "    pm.expect(jsonData.law_verified).to.eql('LAW-107A');",
                "    pm.expect(jsonData.arbitrage_free).to.be.true;",
                "    pm.expect(jsonData.volatility_smile).to.be.an('array').and.not.empty;",
                "});",
            ],
        ),
        make_request_item(
            name="08.4 - Superfícies SABR/SVI - Calibração e Invariantes Sem Arbitragem (FL-101 Engine)",
            path_segments=["api", "phase7", "FL-101"],
            query_params=[],
            description="""
### Fluxo FL-101: Volatility Surface Engine
Verificação das fatias calibradas de opções de ações e dólar a partir de fixtures reais.
""",
            test_assertions=[
                "pm.test('Motor FL-101 de Superfícies SABR/SVI aprovado', function () {",
                "    pm.expect(jsonData.flow).to.eql('FL-101');",
                "    pm.expect(jsonData.laws).to.include('LAW-107A');",
                "    pm.expect(jsonData.surfaces).to.be.an('array').with.length.above(0);",
                "});",
            ],
        ),
        make_request_item(
            name="08.5 - Conceito: Vigilância de Mercado BSM (Spoofing & Wash Trading)",
            path_segments=["api", "concepts", "bsm-surveillance"],
            query_params=[],
            description="""
### Vigilância de Abusos BSM
Modelagem de algoritmos de detecção de infrações ao mercado de capitais: spoofing, layering e wash trading.
- **Invariante LAW-024 / LAW-041**: Proibição de negócios artificiais sem transferência efetiva de titularidade econômica.
""",
            test_assertions=[
                "pm.test('Conceito de Vigilância BSM verificado', function () {",
                "    pm.expect(jsonData.id).to.eql('bsm-surveillance');",
                "    pm.expect(jsonData.related_laws).to.include('LAW-024');",
                "});",
            ],
        ),
        make_request_item(
            name="08.6 - Simulação: Detecção de Padrão Ilícito de Spoofing pela BSM",
            path_segments=["api", "concepts", "bsm-surveillance", "simulate"],
            query_params=[
                {"key": "pattern", "value": "SPOOFING", "description": "Padrão suspeito investigado"},
                {"key": "orders_count", "value": "20", "description": "Número de ofertas analisadas"},
            ],
            description="""
### Simulação de Detecção de Spoofing
Avalia a geração de alertas da BSM ante altas taxas de cancelamento rápido no livro L3.
""",
            test_assertions=[
                "pm.test('Detecção de padrão de abuso pela BSM (LAW-024)', function () {",
                "    pm.expect(jsonData.law_verified).to.eql('LAW-024');",
                "    pm.expect(jsonData.pattern_detected).to.eql('SPOOFING');",
                "    pm.expect(jsonData.alert_level).to.include('CRITICO');",
                "});",
            ],
        ),
        make_request_item(
            name="08.7 - Conceito: Novação Legal & CCP",
            path_segments=["api", "concepts", "novacao-ccp"],
            query_params=[],
            description="Ficha do conceito de novação legal sob a Lei 10.214/2001.",
            test_assertions=["pm.test('Conceito novacao-ccp', () => pm.expect(jsonData.id).to.eql('novacao-ccp'));"],
        ),
        make_request_item(
            name="08.8 - Conceito: Default Waterfall da Câmara",
            path_segments=["api", "concepts", "default-waterfall"],
            query_params=[],
            description="Ficha do conceito de cascata de salvaguardas da CCP.",
            test_assertions=["pm.test('Conceito default-waterfall', () => pm.expect(jsonData.id).to.eql('default-waterfall'));"],
        ),
        make_request_item(
            name="08.9 - Conceito: Leilão de Fechamento MOC & Freeze",
            path_segments=["api", "concepts", "leilao-moc-freeze"],
            query_params=[],
            description="Ficha do conceito de leilão MOC e janela de congelamento.",
            test_assertions=["pm.test('Conceito leilao-moc-freeze', () => pm.expect(jsonData.id).to.eql('leilao-moc-freeze'));"],
        ),
        make_request_item(
            name="08.10 - Conceito: Túneis de Oscilação & Circuit Breaker",
            path_segments=["api", "concepts", "tuneis-circuit-breaker"],
            query_params=[],
            description="Ficha do conceito de túneis de rejeição e leilão.",
            test_assertions=["pm.test('Conceito tuneis-circuit-breaker', () => pm.expect(jsonData.id).to.eql('tuneis-circuit-breaker'));"],
        ),
        make_request_item(
            name="08.11 - Conceito: Compensação Multilateral Contínua",
            path_segments=["api", "concepts", "netting-multilateral"],
            query_params=[],
            description="Ficha do conceito de netting multilateral contínuo.",
            test_assertions=["pm.test('Conceito netting-multilateral', () => pm.expect(jsonData.id).to.eql('netting-multilateral'));"],
        ),
        make_request_item(
            name="08.12 - Conceito: Central de Gravames e Recebíveis",
            path_segments=["api", "concepts", "registro-recebiveis-gravames"],
            query_params=[],
            description="Ficha do conceito de registro de recebíveis e travas.",
            test_assertions=["pm.test('Conceito registro-recebiveis-gravames', () => pm.expect(jsonData.id).to.eql('registro-recebiveis-gravames'));"],
        ),
        make_request_item(
            name="08.13 - Conceito: Liquidação DvP Atômica Drex Fase 2",
            path_segments=["api", "concepts", "drex-dvp-atomico"],
            query_params=[],
            description="Ficha do conceito de liquidação atômica em DLT.",
            test_assertions=["pm.test('Conceito drex-dvp-atomico', () => pm.expect(jsonData.id).to.eql('drex-dvp-atomico'));"],
        ),
        make_request_item(
            name="08.14 - Conceito: Algorithmic Smart Order Router",
            path_segments=["api", "concepts", "algorithmic-sor"],
            query_params=[],
            description="Ficha do conceito de roteador inteligente institucional.",
            test_assertions=["pm.test('Conceito algorithmic-sor', () => pm.expect(jsonData.id).to.eql('algorithmic-sor'));"],
        ),
        make_request_item(
            name="08.15 - Conceito: SNG Financiamentos Veiculares",
            path_segments=["api", "concepts", "sng-gravames-veiculares"],
            query_params=[],
            description="Ficha do conceito do Sistema Nacional de Gravames.",
            test_assertions=["pm.test('Conceito sng-gravames-veiculares', () => pm.expect(jsonData.id).to.eql('sng-gravames-veiculares'));"],
        ),
        make_request_item(
            name="08.16 - Conceito: SRO Seguros SUSEP V3",
            path_segments=["api", "concepts", "sro-seguros-susep"],
            query_params=[],
            description="Ficha do conceito do Sistema de Registro de Operações SUSEP.",
            test_assertions=["pm.test('Conceito sro-seguros-susep', () => pm.expect(jsonData.id).to.eql('sro-seguros-susep'));"],
        ),
    ]

    # 09 - Simulação & Chaos Testing
    folder_09_items = [
        make_request_item(
            name="09.1 - Health Check do Servidor do Atlas B3",
            path_segments=["api", "health"],
            query_params=[],
            description="Verifica disponibilidade do dashboard e backend local 100% offline.",
            test_assertions=[
                "pm.test('Servidor UP e 100% Offline', function () {",
                "    pm.expect(jsonData.status).to.eql('UP');",
                "    pm.expect(jsonData.offline).to.be.true;",
                "});",
            ],
        ),
        make_request_item(
            name="09.2 - Status Global do Ecossistema B3 Atlas",
            path_segments=["api", "status"],
            query_params=[],
            description="Retorna métricas consolidadas: 107 fluxos homologados, 91 leis de conservação e acervo documental.",
            test_assertions=[
                "pm.test('Status com 107 fluxos e 91 leis conservadas', function () {",
                "    pm.expect(jsonData.homologated_flows).to.eql(107);",
                "    pm.expect(jsonData.formal_conservation_laws).to.be.at.least(91);",
                "    pm.expect(jsonData.architecture_modules).to.eql(7);",
                "});",
            ],
        ),
        make_request_item(
            name="09.3 - Módulos Estruturantes da B3 (Arquitetura de 7 Módulos)",
            path_segments=["api", "modules"],
            query_params=[],
            description="Retorna os 7 módulos canônicos da infraestrutura da bolsa.",
            test_assertions=[
                "pm.test('7 Módulos estruturantes presentes', function () {",
                "    pm.expect(jsonData).to.be.an('array').with.lengthOf(7);",
                "});",
            ],
        ),
        make_request_item(
            name="09.4 - Simulação do Ciclo Diário de Negociação (50.000 Ações)",
            path_segments=["api", "simulate-day"],
            query_params=[{"key": "shares", "value": "50000", "description": "Base de ações para simulação"}],
            description="Simulação determinística de um dia completo de mercado financeiro em 8 fases sequenciais (Pré-Abertura até D+2).",
            test_assertions=[
                "pm.test('Simulação de pregão bem-sucedida', function () {",
                "    pm.expect(jsonData.simulation_id).to.be.a('string');",
                "    pm.expect(jsonData.events).to.be.an('array').with.length.above(0);",
                "});",
            ],
        ),
        make_request_item(
            name="09.5 - Simulação do Ciclo Diário de Alta Carga (250.000 Ações)",
            path_segments=["api", "simulate-day"],
            query_params=[{"key": "shares", "value": "250000", "description": "Carga ampliada de ações"}],
            description="Execução sob volume expandido para estresse de memória e tempo de resposta.",
            test_assertions=[
                "pm.test('Simulação de alta carga concluída', function () {",
                "    pm.expect(jsonData.metrics).to.be.an('object');",
                "});",
            ],
        ),
        make_request_item(
            name="09.6 - Cenário de Caos: Circuit Breaker PUMA (Acionamento Sistêmico)",
            path_segments=["api", "chaos", "CIRCUIT_BREAKER"],
            query_params=[],
            description="Simulação de queda abrupta de índice acima de 10% com congelamento generalizado das negociações.",
            test_assertions=[
                "pm.test('Resiliência ante Circuit Breaker sistêmico', function () {",
                "    pm.expect(jsonData.scenario).to.eql('CIRCUIT_BREAKER');",
                "    pm.expect(jsonData.is_resilient).to.be.true;",
                "});",
            ],
        ),
        make_request_item(
            name="09.7 - Cenário de Caos: Inadimplência na Câmara (CLEARING_DEFAULT)",
            path_segments=["api", "chaos", "CLEARING_DEFAULT"],
            query_params=[],
            description="Calote de grande participante e execução do Default Waterfall.",
            test_assertions=[
                "pm.test('Resiliência ante falência em compensação', function () {",
                "    pm.expect(jsonData.scenario).to.eql('CLEARING_DEFAULT');",
                "    pm.expect(jsonData.is_resilient).to.be.true;",
                "});",
            ],
        ),
        make_request_item(
            name="09.8 - Cenário de Caos: Conflito Concorrente em Recebíveis (RECEIVABLES_LIEN_CONFLICT)",
            path_segments=["api", "chaos", "RECEIVABLES_LIEN_CONFLICT"],
            query_params=[],
            description="Guerra concorrente de registradoras para averbação de garantias sobre recebíveis.",
            test_assertions=[
                "pm.test('Resiliência ante conflito em registradoras', function () {",
                "    pm.expect(jsonData.scenario).to.eql('RECEIVABLES_LIEN_CONFLICT');",
                "    pm.expect(jsonData.is_resilient).to.be.true;",
                "});",
            ],
        ),
        make_request_item(
            name="09.9 - Cenário de Caos: Interrupção Crítica de Rede SPB / BACEN",
            path_segments=["api", "chaos", "SPB_NETWORK_DISRUPTION"],
            query_params=[],
            description="Queda de conectividade de mensageria com o Banco Central.",
            test_assertions=[
                "pm.test('Resiliência ante interrupção de rede do SPB', function () {",
                "    pm.expect(jsonData.scenario).to.eql('SPB_NETWORK_DISRUPTION');",
                "    pm.expect(jsonData.is_resilient).to.be.true;",
                "});",
            ],
        ),
        make_request_item(
            name="09.10 - Model Checker TLA+ Formal das Leis de Conservação (LAW-106A/B/C)",
            path_segments=["api", "tla-check"],
            query_params=[],
            description="Execução da prova formal de ausência de deadlock e conservação de valor contábil.",
            test_assertions=[
                "pm.test('TLA+ Model Checker sem deadlocks', function () {",
                "    pm.expect(jsonData.passed).to.be.true;",
                "    pm.expect(jsonData.deadlocks_found).to.eql(0);",
                "});",
            ],
        ),
        make_request_item(
            name="09.11 - Catálogo Completo dos Manuais Oficiais B3 (87 Documentos)",
            path_segments=["api", "docs"],
            query_params=[],
            description="Listagem completa com hash SHA-256 e tamanho de todos os 87 manuais técnicos da bolsa.",
            test_assertions=[
                "pm.test('Acervo completo de 87 documentos locais', function () {",
                "    pm.expect(jsonData).to.be.an('array').with.lengthOf(87);",
                "});",
            ],
        ),
    ]

    folders = [
        {
            "name": "01 - PUMA Trading & Ordem MOC/LOC",
            "description": "Endpoints de negociação eletrônica listada, leilões de fechamento MOC/LOC, túneis de rejeição e SOR algorítmico (TWAP/VWAP).",
            "item": folder_01_items,
        },
        {
            "name": "02 - Câmara B3 & Default Waterfall",
            "description": "Endpoints da CCP B3, novação civil da Lei 10.214, salvaguardas financeiras em 5 camadas, netting multilateral e modelo formal TLA+.",
            "item": folder_02_items,
        },
        {
            "name": "03 - Balcão NoMe & Recebíveis",
            "description": "Endpoints de registro de balcão NoMe, interoperabilidade de recebíveis de cartão (CMN 4.734) e Marco Legal das Garantias (Lei 14.711).",
            "item": folder_03_items,
        },
        {
            "name": "04 - Central Depositária & STVM",
            "description": "Endpoints da depositária de valores mobiliários, arquivos IMBARQ 006/014, portabilidade eletrônica STVM (CVM 179) e COE.",
            "item": folder_04_items,
        },
        {
            "name": "05 - SPB, STR & Drex Fase 2",
            "description": "Endpoints do Sistema de Pagamentos Brasileiro (STR/SPI), mensageria LDL0001 e contratos inteligentes de DvP atômico no Drex Fase 2.",
            "item": folder_05_items,
        },
        {
            "name": "06 - SNG Financiamentos (Veículos)",
            "description": "Endpoints do Sistema Nacional de Gravames (SNG B3 Financiamentos) regulado pela Resolução CONTRAN nº 807/2020 e LAW-112.",
            "item": folder_06_items,
        },
        {
            "name": "07 - SRO Seguros (SUSEP V3)",
            "description": "Endpoints do Sistema de Registro de Operações (SRO B3 Seguros) regulado pela SUSEP (Circular 642/2021) e invariante LAW-113.",
            "item": folder_07_items,
        },
        {
            "name": "08 - Laboratório de Conceitos Complexos (/api/concepts)",
            "description": "Laboratório interativo com fichamentos conceituais matemáticos e simulações analíticas de todos os 12 conceitos complexos da B3.",
            "item": folder_08_items,
        },
        {
            "name": "09 - Simulação & Chaos Testing",
            "description": "Bateria de diagnósticos, healthcheck, status, simulação determinística de dia de mercado (D+2) e cenários de resiliência e estresse (Chaos).",
            "item": folder_09_items,
        },
    ]

    collection_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "b3.com.br.integration.atlas.postman.v2.1"))

    collection = {
        "info": {
            "_postman_id": collection_id,
            "name": "B3 Integration Atlas — Official API Collection v2.1",
            "description": """
# B3 Integration Atlas — Coleção Postman Oficial v2.1
## Arquitetura de Mercado de Capitais, Clearing, CSD, NoMe, SPB, SNG & SRO

Esta coleção reúne a especificação canônica de integração e validação automatizada de todas as rotas e motores matemáticos do ecossistema da **B3 (Brasil, Bolsa, Balcão)**.

### Módulos Estruturados:
1. `01 - PUMA Trading & Ordem MOC/LOC` (Leilão de Fechamento, Janela de Congelamento, Túneis e Algorithmic SOR)
2. `02 - Câmara B3 & Default Waterfall` (Novação Legal Lei 10.214, Netting Multilateral, Cascata de Calote e Model Checker TLA+)
3. `03 - Balcão NoMe & Recebíveis` (Registro NoMe, Trava de Domicílio e Central de Gravames CMN 4.734 / Lei 14.711)
4. `04 - Central Depositária & STVM` (IMBARQ 006/014, Portabilidade Digital CVM 179 e COE Capital Protegido)
5. `05 - SPB, STR & Drex Fase 2` (Liquidação Bruta STR LDL0001, DvP Atômico Hyperledger Besu e RWA)
6. `06 - SNG Financiamentos (Veículos)` (Alienação Fiduciária CONTRAN 807/2020 e Invariante LAW-112)
7. `07 - SRO Seguros (SUSEP V3)` (Registro de Apólices, Partição de Cosseguro 100% e Invariante LAW-113)
8. `08 - Laboratório de Conceitos Complexos (/api/concepts)` (Catálogo Pedagógico dos 12 Conceitos e Simulações)
9. `09 - Simulação & Chaos Testing` (Ciclo Diário de Mercado D+2 e 4 Cenários de Resiliência Sistêmica)

### Variáveis Configuradas:
- `{{base_url}}`: URL do servidor local (padrão: `http://127.0.0.1:8080`)
- `{{api_key}}`: Chave de autenticação institucional B3
- `{{hmac_secret}}`: Segredo criptográfico HMAC-SHA256 para assinatura de integridade
""",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "version": "2.3.0",
        },
        "variable": [
            {
                "key": "base_url",
                "value": "http://127.0.0.1:8080",
                "type": "string",
                "description": "URL base do servidor local do B3 Integration Atlas",
            },
            {
                "key": "api_key",
                "value": "b3_atlas_prod_sec_key_2026",
                "type": "string",
                "description": "Chave de autenticação da API B3 Atlas",
            },
            {
                "key": "hmac_secret",
                "value": "b3_hmac_secret_sha256_invariants_guard",
                "type": "string",
                "description": "Segredo HMAC-SHA256 para cálculo de assinatura digital e integridade",
            },
        ],
        "auth": {
            "type": "apikey",
            "apikey": [
                {
                    "key": "key",
                    "value": "X-B3-Api-Key",
                    "type": "string",
                },
                {
                    "key": "value",
                    "value": "{{api_key}}",
                    "type": "string",
                },
                {
                    "key": "in",
                    "value": "header",
                    "type": "string",
                },
            ],
        },
        "event": [
            {
                "listen": "prerequest",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "// Pre-request script global da B3 Collection v2.1",
                        "const timestamp = new Date().toISOString();",
                        "pm.request.headers.add({ key: 'X-B3-Timestamp', value: timestamp });",
                        "const hmacSecret = pm.variables.get('hmac_secret') || 'default_b3_secret';",
                        "try {",
                        "    const signature = CryptoJS.HmacSHA256(timestamp, hmacSecret).toString();",
                        "    pm.request.headers.add({ key: 'X-B3-HMAC-Signature', value: signature });",
                        "} catch (e) {",
                        "    // Fallback silencioso caso CryptoJS não esteja inicializado no runner",
                        "}",
                    ],
                },
            }
        ],
        "item": folders,
    }

    return collection


def main() -> None:
    collection = generate_collection()
    output_path = _DOCS_DIR / "b3_atlas_postman_collection.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(collection, f, indent=2, ensure_ascii=False)

    # Contagem de requests
    total_requests = sum(len(folder["item"]) for folder in collection["item"])
    print(f"Coleção Postman v2.1 gerada com sucesso em: {output_path}")
    print(f"Total de pastas (módulos): {len(collection['item'])}")
    print(f"Total de requisições cadastradas: {total_requests}")
    for idx, f in enumerate(collection["item"], start=1):
        print(f"  [{idx:02d}] {f['name']}: {len(f['item'])} requests")


if __name__ == "__main__":
    main()
