"""B3 Developer Portal & Interactive OpenAPI 3.1 Documentation Engine.

100% Offline, zero external dependencies (no remote CDNs, no external JS/CSS).
Renders an interactive API explorer, live sandbox console ("Try it Out"),
and step-by-step institutional integration guides for the B3 Integration Atlas.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent


def get_openapi_spec() -> Dict[str, Any]:
    """Carrega a especificação canônica OpenAPI 3.1 do B3 Integration Atlas."""
    spec_path = _REPO_ROOT / "docs" / "openapi_b3_atlas_complete.json"
    if not spec_path.is_file():
        return {
            "openapi": "3.1.0",
            "info": {
                "title": "B3 Integration Atlas API",
                "version": "2.3.0",
                "description": "Especificação local indisponível.",
            },
            "paths": {},
        }
    with open(spec_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_guides_catalog() -> List[Dict[str, Any]]:
    """Carrega o catálogo dos 4 guias práticos de integração."""
    guides_dir = _REPO_ROOT / "docs" / "guides"
    catalog = [
        {
            "id": "puma-trading",
            "filename": "GUIA_INTEGRACAO_PUMA_TRADING.md",
            "title": "PUMA Trading System — FIX/SBE, Ordens e Leilão MOC",
            "category": "Negociação & Book",
            "badge": "FL-001 / FL-002 / FL-098",
            "laws": ["LAW-001", "LAW-002", "LAW-104A", "LAW-104B"],
            "summary": "Conexão de sessões FIX 4.4/5.0 e PUMA SBE v2, envio e gestão de ordens limitadas, leilão de fechamento MOC/LOC com Freeze Window.",
        },
        {
            "id": "sng-gravames",
            "filename": "GUIA_INTEGRACAO_SNG_GRAVAMES.md",
            "title": "SNG — Sistema Nacional de Gravames (CDC & Leasing)",
            "category": "Financiamentos & Garantias",
            "badge": "FL-106",
            "laws": ["LAW-112"],
            "summary": "Inclusão, consulta, baixa e cancelamento de garantias reais veiculares conectando instituições financeiras aos 27 DETRANs estaduais.",
        },
        {
            "id": "sro-seguros",
            "filename": "GUIA_INTEGRACAO_SRO_SEGUROS.md",
            "title": "SRO — Registro de Apólices SUSEP V3 & Sinistros",
            "category": "Seguros & Previdência",
            "badge": "FL-107",
            "laws": ["LAW-113"],
            "summary": "Registro de apólices no padrão SUSEP V3, partição estrita de cosseguro em 100%, aviso e liquidação com barreira de teto LMG.",
        },
        {
            "id": "drex-fase2",
            "filename": "GUIA_INTEGRACAO_DREX_FASE2.md",
            "title": "Drex Fase 2 & RWA — DvP Atômico e Smart Contracts",
            "category": "DLT & Moeda Digital",
            "badge": "FL-089 / FL-105",
            "laws": ["LAW-089A", "LAW-089B", "LAW-111"],
            "summary": "Liquidação atômica bilateral Delivery-versus-Payment (DvP) em Hyperledger Besu, Real Digital BACEN, Real Tokenizado e debêntures RWA.",
        },
    ]

    for item in catalog:
        file_path = guides_dir / item["filename"]
        if file_path.is_file():
            with open(file_path, "r", encoding="utf-8") as f:
                item["content"] = f.read()
        else:
            item["content"] = f"# {item['title']}\n\nGuia ainda não gerado em disco."

    return catalog


def render_developer_portal_html(current_path: str = "/docs/api") -> str:
    """Gera o HTML visual do Developer Portal e documentação interativa OpenAPI 3.1.
    
    100% offline, zero dependências externas de CDN, JavaScript e CSS embutidos.
    """
    spec = get_openapi_spec()
    guides = get_guides_catalog()
    
    # Escapa </script> no JSON para prevenir quebra de script HTML
    spec_json_escaped = json.dumps(spec, ensure_ascii=False).replace("</script>", "<\\/script>")
    guides_json_escaped = json.dumps(guides, ensure_ascii=False).replace("</script>", "<\\/script>")

    default_mode = "interactive" if current_path == "/docs/interactive" else "explorer"

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>B3 Integration Atlas — Portal de Desenvolvedores & OpenAPI 3.1 Interativo</title>
  <style>
    :root {{
      --bg: #090d16;
      --panel: #0c1220;
      --card: #111827;
      --card-hover: #162032;
      --border: #1f2937;
      --border-focus: #38bdf8;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --accent-blue: #0284c7;
      --accent-cyan: #38bdf8;
      --accent-gold: #c39e5c;
      --accent-green: #10b981;
      --accent-red: #ef4444;
      --accent-amber: #f59e0b;
      --accent-purple: #8b5cf6;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    body {{ background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }}
    
    /* Top Navbar */
    .topbar {{
      height: 60px;
      background: #080c14;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 1.5rem;
      flex-shrink: 0;
    }}
    .brand-wrap {{ display: flex; align-items: center; gap: 0.75rem; }}
    .brand-title {{ font-size: 1.05rem; font-weight: 700; color: #fff; letter-spacing: -0.02em; display: flex; align-items: center; gap: 0.5rem; }}
    .brand-badge {{
      font-size: 0.7rem;
      background: rgba(195, 158, 92, 0.15);
      color: var(--accent-gold);
      border: 1px solid rgba(195, 158, 92, 0.35);
      padding: 0.2rem 0.5rem;
      border-radius: 4px;
      font-weight: 600;
    }}
    .nav-tabs {{ display: flex; gap: 0.5rem; }}
    .nav-tab {{
      background: transparent;
      border: 1px solid transparent;
      color: var(--text-muted);
      padding: 0.45rem 0.9rem;
      border-radius: 6px;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 0.4rem;
      transition: all 0.2s;
      text-decoration: none;
    }}
    .nav-tab:hover {{ color: #fff; background: rgba(255, 255, 255, 0.05); }}
    .nav-tab.active {{
      background: rgba(2, 132, 199, 0.15);
      color: var(--accent-cyan);
      border-color: rgba(2, 132, 199, 0.4);
    }}
    .topbar-right {{ display: flex; align-items: center; gap: 0.75rem; }}
    .status-pill {{
      font-size: 0.75rem;
      background: rgba(16, 185, 129, 0.12);
      color: var(--accent-green);
      border: 1px solid rgba(16, 185, 129, 0.3);
      padding: 0.25rem 0.65rem;
      border-radius: 9999px;
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }}
    .status-dot {{ width: 7px; height: 7px; border-radius: 50%; background: var(--accent-green); box-shadow: 0 0 6px var(--accent-green); }}

    /* Layout Workspace */
    .workspace {{ display: flex; flex: 1; overflow: hidden; }}
    
    /* Sidebar */
    .sidebar {{
      width: 380px;
      background: var(--panel);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
    }}
    .sidebar-header {{
      padding: 1rem;
      border-bottom: 1px solid var(--border);
    }}
    .search-input {{
      width: 100%;
      background: #090d16;
      border: 1px solid var(--border);
      color: #fff;
      padding: 0.55rem 0.75rem;
      border-radius: 6px;
      font-size: 0.82rem;
      outline: none;
      transition: border-color 0.2s;
    }}
    .search-input:focus {{ border-color: var(--border-focus); }}
    .tag-filter-bar {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.3rem;
      margin-top: 0.6rem;
    }}
    .tag-chip {{
      font-size: 0.7rem;
      padding: 0.2rem 0.5rem;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border);
      border-radius: 4px;
      color: var(--text-muted);
      cursor: pointer;
      user-select: none;
    }}
    .tag-chip:hover, .tag-chip.active {{
      background: rgba(2, 132, 199, 0.2);
      color: var(--accent-cyan);
      border-color: rgba(2, 132, 199, 0.4);
    }}
    .endpoints-list {{
      flex: 1;
      overflow-y: auto;
      padding: 0.5rem;
    }}
    .tag-group-header {{
      font-size: 0.72rem;
      text-transform: uppercase;
      font-weight: 700;
      color: var(--accent-gold);
      padding: 0.6rem 0.5rem 0.3rem 0.5rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .endpoint-item {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.5rem 0.6rem;
      border-radius: 6px;
      cursor: pointer;
      font-size: 0.8rem;
      margin-bottom: 0.2rem;
      border: 1px solid transparent;
      transition: all 0.15s;
    }}
    .endpoint-item:hover {{
      background: rgba(255, 255, 255, 0.03);
      border-color: rgba(255, 255, 255, 0.06);
    }}
    .endpoint-item.active {{
      background: rgba(2, 132, 199, 0.12);
      border-color: rgba(2, 132, 199, 0.35);
    }}
    .endpoint-path {{
      font-family: monospace;
      color: #e5e7eb;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      flex: 1;
    }}
    
    /* Method Badges */
    .method-badge {{
      font-size: 0.65rem;
      font-weight: 800;
      padding: 0.15rem 0.4rem;
      border-radius: 4px;
      text-transform: uppercase;
      min-width: 48px;
      text-align: center;
      letter-spacing: 0.04em;
    }}
    .method-badge.get {{ background: rgba(2, 132, 199, 0.2); color: #38bdf8; border: 1px solid rgba(2, 132, 199, 0.4); }}
    .method-badge.post {{ background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }}
    .method-badge.put {{ background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }}
    .method-badge.delete {{ background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }}

    /* Main Stage */
    .stage {{
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow-y: auto;
      background: var(--bg);
      padding: 1.5rem 2rem;
    }}
    
    /* Endpoint View Card */
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
    }}
    .endpoint-hero {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      margin-bottom: 0.75rem;
      flex-wrap: wrap;
    }}
    .endpoint-hero .method-badge {{ font-size: 0.85rem; padding: 0.3rem 0.75rem; }}
    .endpoint-hero-path {{ font-family: monospace; font-size: 1.25rem; font-weight: 700; color: #fff; }}
    .endpoint-summary {{ font-size: 1.05rem; font-weight: 600; color: #f3f4f6; margin-bottom: 0.5rem; }}
    .endpoint-desc {{ font-size: 0.88rem; color: #d1d5db; line-height: 1.5; margin-bottom: 1rem; }}
    .laws-badge-list {{ display: flex; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 1rem; }}
    .law-badge {{
      font-size: 0.72rem;
      font-family: monospace;
      font-weight: 700;
      padding: 0.2rem 0.55rem;
      border-radius: 4px;
      background: rgba(195, 158, 92, 0.15);
      color: var(--accent-gold);
      border: 1px solid rgba(195, 158, 92, 0.35);
    }}
    
    /* Interactive Console Sections */
    .section-title {{
      font-size: 0.82rem;
      text-transform: uppercase;
      font-weight: 700;
      color: var(--accent-cyan);
      margin-bottom: 0.75rem;
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }}
    .params-table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-bottom: 1.25rem; }}
    .params-table th, .params-table td {{ padding: 0.6rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border); }}
    .params-table th {{ background: rgba(0, 0, 0, 0.2); color: var(--text-muted); font-weight: 600; }}
    .param-input {{
      width: 100%;
      background: #090d16;
      border: 1px solid var(--border);
      color: #fff;
      padding: 0.35rem 0.55rem;
      border-radius: 4px;
      font-family: monospace;
      font-size: 0.8rem;
    }}
    .param-input:focus {{ border-color: var(--border-focus); outline: none; }}
    
    /* Request Body Editor */
    .body-editor-wrap {{ margin-bottom: 1.25rem; }}
    .body-textarea {{
      width: 100%;
      min-height: 160px;
      background: #090d16;
      border: 1px solid var(--border);
      color: #38bdf8;
      font-family: monospace;
      font-size: 0.82rem;
      padding: 0.75rem;
      border-radius: 6px;
      resize: vertical;
      line-height: 1.4;
    }}
    .body-textarea:focus {{ border-color: var(--border-focus); outline: none; }}
    .btn-bar {{ display: flex; gap: 0.6rem; align-items: center; margin-top: 0.5rem; }}
    .btn {{
      padding: 0.55rem 1.1rem;
      border-radius: 6px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid transparent;
      display: flex;
      align-items: center;
      gap: 0.4rem;
      transition: all 0.15s;
    }}
    .btn-primary {{
      background: var(--accent-blue);
      color: #fff;
    }}
    .btn-primary:hover {{ background: #0369a1; }}
    .btn-secondary {{
      background: rgba(255, 255, 255, 0.06);
      color: #e5e7eb;
      border-color: var(--border);
    }}
    .btn-secondary:hover {{ background: rgba(255, 255, 255, 0.1); }}
    
    /* Response Panel */
    .response-card {{
      background: #080c14;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.25rem;
      margin-top: 1.25rem;
      display: none;
    }}
    .response-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.75rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 0.5rem;
    }}
    .status-code-badge {{
      font-family: monospace;
      font-weight: 700;
      font-size: 0.85rem;
      padding: 0.2rem 0.6rem;
      border-radius: 4px;
    }}
    .status-2xx {{ background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }}
    .status-4xx {{ background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }}
    .status-5xx {{ background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }}
    .response-meta {{ font-size: 0.78rem; color: var(--text-muted); display: flex; gap: 0.75rem; }}
    .response-body-pre {{
      font-family: monospace;
      font-size: 0.8rem;
      color: #67e8f9;
      white-space: pre-wrap;
      max-height: 400px;
      overflow-y: auto;
      line-height: 1.4;
      background: #05080e;
      padding: 0.75rem;
      border-radius: 6px;
      border: 1px solid rgba(255, 255, 255, 0.05);
    }}
    
    /* Guides View */
    .guides-view {{ display: none; }}
    .guides-nav {{ display: flex; gap: 0.5rem; margin-bottom: 1.5rem; flex-wrap: wrap; }}
    .guide-btn {{
      padding: 0.55rem 1rem;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 0.82rem;
      font-weight: 600;
      transition: all 0.2s;
    }}
    .guide-btn:hover, .guide-btn.active {{
      background: rgba(195, 158, 92, 0.15);
      color: var(--accent-gold);
      border-color: rgba(195, 158, 92, 0.4);
    }}
    .markdown-rendered {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 2rem;
      line-height: 1.6;
      color: #e5e7eb;
    }}
    .markdown-rendered h1 {{ font-size: 1.6rem; color: #fff; margin-bottom: 0.5rem; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem; }}
    .markdown-rendered h2 {{ font-size: 1.25rem; color: var(--accent-cyan); margin: 1.5rem 0 0.5rem 0; }}
    .markdown-rendered h3 {{ font-size: 1.05rem; color: var(--accent-gold); margin: 1.25rem 0 0.4rem 0; }}
    .markdown-rendered p {{ margin-bottom: 1rem; font-size: 0.9rem; }}
    .markdown-rendered ul, .markdown-rendered ol {{ padding-left: 1.5rem; margin-bottom: 1rem; font-size: 0.9rem; }}
    .markdown-rendered li {{ margin-bottom: 0.3rem; }}
    .markdown-rendered pre {{
      background: #090d16;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 1rem;
      overflow-x: auto;
      margin: 1rem 0;
      font-family: monospace;
      font-size: 0.82rem;
      color: #38bdf8;
      position: relative;
    }}
    .markdown-rendered code {{ font-family: monospace; font-size: 0.85em; background: rgba(255, 255, 255, 0.08); padding: 0.15rem 0.35rem; border-radius: 3px; }}
    .markdown-rendered pre code {{ background: transparent; padding: 0; }}
    .markdown-rendered table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.85rem; }}
    .markdown-rendered th, .markdown-rendered td {{ border: 1px solid var(--border); padding: 0.6rem 0.8rem; text-align: left; }}
    .markdown-rendered th {{ background: #0c1220; color: var(--accent-cyan); }}
    
    /* Quick Presets Console */
    .presets-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 0.75rem;
      margin-bottom: 1.5rem;
    }}
    .preset-card {{
      background: #0c1220;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.85rem;
      cursor: pointer;
      transition: all 0.2s;
    }}
    .preset-card:hover {{ border-color: var(--accent-blue); transform: translateY(-2px); }}
    .preset-name {{ font-size: 0.85rem; font-weight: 600; color: #fff; margin-bottom: 0.25rem; }}
    .preset-endpoint {{ font-family: monospace; font-size: 0.72rem; color: var(--accent-cyan); }}
  </style>
</head>
<body>
  <!-- Topbar -->
  <header class="topbar">
    <div class="brand-wrap">
      <div class="brand-title">
        🏛️ B3 INTEGRATION ATLAS
        <span class="brand-badge">PORTAL DO DESENVOLVEDOR</span>
      </div>
    </div>

    <nav class="nav-tabs">
      <button class="nav-tab active" id="tab-btn-explorer" onclick="setPortalTab('explorer')">
        📖 Explorador OpenAPI 3.1
      </button>
      <button class="nav-tab" id="tab-btn-interactive" onclick="setPortalTab('interactive')">
        ⚡ Console Interativo (Sandbox)
      </button>
      <button class="nav-tab" id="tab-btn-guides" onclick="setPortalTab('guides')">
        📚 Guias de Integração
      </button>
      <a href="/" class="nav-tab" style="color: var(--accent-gold);">
        📊 Dashboard Principal
      </a>
    </nav>

    <div class="topbar-right">
      <div class="status-pill">
        <span class="status-dot"></span> 100% Offline • Zero CDN
      </div>
    </div>
  </header>

  <!-- Main Workspace -->
  <div class="workspace">
    <!-- Sidebar -->
    <aside class="sidebar" id="sidebar-panel">
      <div class="sidebar-header">
        <input type="text" id="api-search" class="search-input" placeholder="🔍 Filtrar endpoints, tags ou leis (ex: LAW-104A, MOC)..." oninput="filterEndpoints()">
        <div class="tag-filter-bar" id="tag-filter-container"></div>
      </div>
      <div class="endpoints-list" id="endpoints-list-container"></div>
    </aside>

    <!-- Stage Content Area -->
    <main class="stage" id="stage-panel">
      <!-- Section: OpenAPI Explorer / Console View -->
      <div id="explorer-view-container">
        <div class="card" id="endpoint-detail-card">
          <div style="color: var(--text-muted); font-size: 0.9rem; padding: 2rem; text-align: center;">
            👈 Selecione um endpoint na barra lateral para inspecionar parâmetros, schemas e executar testes interativos em tempo real.
          </div>
        </div>
      </div>

      <!-- Section: Integration Guides View -->
      <div id="guides-view-container" class="guides-view">
        <div class="guides-nav" id="guides-tab-bar"></div>
        <div class="markdown-rendered" id="guide-content-area"></div>
      </div>
    </main>
  </div>

  <!-- Embedded OpenAPI Spec and Guides Catalog -->
  <script id="openapi-spec-data" type="application/json">{spec_json_escaped}</script>
  <script id="guides-catalog-data" type="application/json">{guides_json_escaped}</script>

  <script>
    let spec = {{}};
    let guides = [];
    let allEndpoints = [];
    let selectedTag = 'ALL';
    let currentEndpoint = null;
    let currentMode = '{default_mode}';

    // Inicialização ao carregar o DOM
    window.addEventListener('DOMContentLoaded', () => {{
      try {{
        spec = JSON.parse(document.getElementById('openapi-spec-data').textContent);
        guides = JSON.parse(document.getElementById('guides-catalog-data').textContent);
      }} catch (e) {{
        console.error('Erro ao ler especificações embutidas:', e);
      }}

      parseOpenApiEndpoints();
      renderTagFilters();
      renderEndpointsList();
      renderGuidesTabs();

      // Seleciona o primeiro endpoint por padrão
      if (allEndpoints.length > 0) {{
        selectEndpoint(allEndpoints[0]);
      }}

      if (currentMode === 'interactive') {{
        setPortalTab('interactive');
      }} else if (currentMode === 'guides') {{
        setPortalTab('guides');
      }} else {{
        setPortalTab('explorer');
      }}
    }});

    function setPortalTab(tab) {{
      currentMode = tab;
      document.querySelectorAll('.nav-tab').forEach(b => b.classList.remove('active'));
      const btn = document.getElementById('tab-btn-' + tab);
      if (btn) btn.classList.add('active');

      const explorerView = document.getElementById('explorer-view-container');
      const guidesView = document.getElementById('guides-view-container');
      const sidebar = document.getElementById('sidebar-panel');

      if (tab === 'guides') {{
        explorerView.style.display = 'none';
        guidesView.style.display = 'block';
        sidebar.style.display = 'none';
        if (guides.length > 0) selectGuide(guides[0].id);
      }} else {{
        guidesView.style.display = 'none';
        explorerView.style.display = 'block';
        sidebar.style.display = 'flex';
        if (tab === 'interactive' && currentEndpoint) {{
          // Rola suavemente até o widget de teste interativo
          const tester = document.getElementById('interactive-tester-section');
          if (tester) tester.scrollIntoView({{ behavior: 'smooth' }});
        }}
      }}
    }}

    function parseOpenApiEndpoints() {{
      allEndpoints = [];
      const paths = spec.paths || {{}};
      const methods = ['get', 'post', 'put', 'delete', 'patch'];

      for (const [path, pathItem] of Object.entries(paths)) {{
        for (const m of methods) {{
          if (pathItem[m]) {{
            const op = pathItem[m];
            const tags = op.tags || ['Geral'];
            const summary = op.summary || '';
            const description = op.description || '';
            
            // Detecta referências a Leis Formais (LAW-xxx)
            const lawMatches = (summary + ' ' + description).match(/LAW-\\d+[A-Z]?/g) || [];
            const uniqueLaws = [...new Set(lawMatches)];

            allEndpoints.push({{
              method: m.toUpperCase(),
              path: path,
              tags: tags,
              summary: summary,
              description: description,
              operationId: op.operationId || '',
              parameters: op.parameters || [],
              requestBody: op.requestBody || null,
              responses: op.responses || {{}},
              laws: uniqueLaws,
              rawOp: op,
            }});
          }}
        }}
      }}
    }}

    function renderTagFilters() {{
      const container = document.getElementById('tag-filter-container');
      const tags = new Set(['ALL']);
      allEndpoints.forEach(ep => ep.tags.forEach(t => tags.add(t)));

      container.innerHTML = Array.from(tags).map(t => `
        <span class="tag-chip ${{t === selectedTag ? 'active' : ''}}" onclick="filterByTag('${{t}}')">
          ${{t === 'ALL' ? 'Todos (' + allEndpoints.length + ')' : t}}
        </span>
      `).join('');
    }}

    function filterByTag(tag) {{
      selectedTag = tag;
      renderTagFilters();
      filterEndpoints();
    }}

    function filterEndpoints() {{
      const query = (document.getElementById('api-search').value || '').toLowerCase().trim();
      const filtered = allEndpoints.filter(ep => {{
        const matchesTag = (selectedTag === 'ALL' || ep.tags.includes(selectedTag));
        const matchesQuery = !query || (
          ep.path.toLowerCase().includes(query) ||
          ep.summary.toLowerCase().includes(query) ||
          ep.method.toLowerCase().includes(query) ||
          ep.laws.some(l => l.toLowerCase().includes(query)) ||
          ep.tags.some(t => t.toLowerCase().includes(query))
        );
        return matchesTag && matchesQuery;
      }});
      renderEndpointsList(filtered);
    }}

    function renderEndpointsList(list = allEndpoints) {{
      const container = document.getElementById('endpoints-list-container');
      if (list.length === 0) {{
        container.innerHTML = '<div style="color: var(--text-muted); font-size: 0.78rem; padding: 1rem; text-align: center;">Nenhum endpoint encontrado.</div>';
        return;
      }}

      // Agrupa por Tag primária
      const groups = {{}};
      list.forEach(ep => {{
        const primaryTag = ep.tags[0] || 'Outros';
        if (!groups[primaryTag]) groups[primaryTag] = [];
        groups[primaryTag].push(ep);
      }});

      let html = '';
      for (const [tag, items] of Object.entries(groups)) {{
        html += `<div class="tag-group-header"><span>${{tag}}</span><span>${{items.length}}</span></div>`;
        items.forEach(ep => {{
          const isSelected = currentEndpoint && currentEndpoint.method === ep.method && currentEndpoint.path === ep.path;
          html += `
            <div class="endpoint-item ${{isSelected ? 'active' : ''}}" onclick="selectEndpointByRef('${{ep.method}}', '${{ep.path}}')">
              <span class="method-badge ${{ep.method.toLowerCase()}}">${{ep.method}}</span>
              <span class="endpoint-path" title="${{ep.path}}">${{ep.path}}</span>
            </div>
          `;
        }});
      }}
      container.innerHTML = html;
    }}

    function selectEndpointByRef(method, path) {{
      const ep = allEndpoints.find(e => e.method === method && e.path === path);
      if (ep) selectEndpoint(ep);
    }}

    function selectEndpoint(ep) {{
      currentEndpoint = ep;
      renderEndpointsList();

      const card = document.getElementById('endpoint-detail-card');
      const sampleBody = generateSampleRequestBody(ep);

      card.innerHTML = `
        <div class="endpoint-hero">
          <span class="method-badge ${{ep.method.toLowerCase()}}">${{ep.method}}</span>
          <span class="endpoint-hero-path">${{ep.path}}</span>
        </div>

        <div class="endpoint-summary">${{ep.summary || 'Sem resumo cadastrado'}}</div>

        ${{ep.laws.length > 0 ? `
          <div class="laws-badge-list">
            ${{ep.laws.map(l => `<span class="law-badge">🛡️ ${{l}}</span>`).join('')}}
          </div>
        ` : ''}}

        <div class="endpoint-desc">${{formatMarkdownDesc(ep.description)}}</div>

        <!-- Parâmetros de Requisição -->
        ${{ep.parameters && ep.parameters.length > 0 ? `
          <div class="section-title">⚙️ Parâmetros da Operação</div>
          <table class="params-table">
            <thead>
              <tr>
                <th style="width: 140px;">Nome</th>
                <th style="width: 80px;">Tipo</th>
                <th style="width: 70px;">Obrigatório</th>
                <th>Descrição</th>
                <th style="width: 220px;">Valor para Teste</th>
              </tr>
            </thead>
            <tbody>
              ${{ep.parameters.map((p, idx) => `
                <tr>
                  <td><code>${{p.name}}</code></td>
                  <td><span class="tag-chip">${{p.in}}</span></td>
                  <td>${{p.required ? '<b style="color: var(--accent-red)">Sim</b>' : 'Não'}}</td>
                  <td>${{p.description || '-'}}</td>
                  <td>
                    <input type="text" class="param-input" id="param-${{p.name}}" data-in="${{p.in}}" data-name="${{p.name}}" value="${{getDefaultParamValue(p)}}">
                  </td>
                </tr>
              `).join('')}}
            </tbody>
          </table>
        ` : ''}}

        <!-- Request Body Editor para POST/PUT -->
        ${{['POST', 'PUT', 'PATCH'].includes(ep.method) ? `
          <div class="section-title">📦 Payload da Requisição (JSON Body)</div>
          <div class="body-editor-wrap">
            <textarea class="body-textarea" id="request-body-textarea" spellcheck="false">${{sampleBody}}</textarea>
            <div class="btn-bar">
              <button class="btn btn-secondary" onclick="formatRequestBody()">✨ Formatar JSON</button>
              <button class="btn btn-secondary" onclick="resetRequestBody()">🔄 Restaurar Exemplo</button>
            </div>
          </div>
        ` : ''}}

        <!-- Botões de Execução Interativa (Try it Out) -->
        <div id="interactive-tester-section" style="border-top: 1px solid var(--border); padding-top: 1.25rem; margin-top: 1rem;">
          <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.75rem;">
            <div style="display: flex; gap: 0.75rem;">
              <button class="btn btn-primary" id="btn-execute-call" onclick="executeInteractiveCall()">
                ▶️ Executar Chamada no Servidor Local
              </button>
              <button class="btn btn-secondary" onclick="copyCurlCommand()">
                📋 Copiar cURL
              </button>
            </div>
            <div style="font-size: 0.78rem; color: var(--text-muted);">
              Alvo: <span style="font-family: monospace; color: var(--accent-cyan);">http://127.0.0.1:8080${{ep.path}}</span>
            </div>
          </div>

          <!-- Painel de Resposta da Execução -->
          <div class="response-card" id="response-panel">
            <div class="response-header">
              <div style="display: flex; align-items: center; gap: 0.75rem;">
                <span class="status-code-badge" id="response-status-badge">200 OK</span>
                <span style="font-size: 0.8rem; font-weight: 600; color: #fff;" id="response-time-badge">12 ms</span>
              </div>
              <div class="response-meta">
                <button class="btn btn-secondary" style="padding: 0.25rem 0.6rem; font-size: 0.72rem;" onclick="copyResponseBody()">📋 Copiar Resposta</button>
              </div>
            </div>
            <div class="section-title" style="font-size: 0.72rem; margin-bottom: 0.4rem;">Corpo da Resposta (Response JSON):</div>
            <pre class="response-body-pre" id="response-body-pre"></pre>
          </div>
        </div>

        <!-- Respostas Documentadas na Especificação -->
        <div style="border-top: 1px solid var(--border); padding-top: 1.25rem; margin-top: 1.5rem;">
          <div class="section-title">📋 Especificação Formal de Respostas (Responses)</div>
          <table class="params-table">
            <thead>
              <tr>
                <th style="width: 100px;">Código HTTP</th>
                <th>Descrição do Retorno Formal</th>
              </tr>
            </thead>
            <tbody>
              ${{Object.entries(ep.responses).map(([code, r]) => `
                <tr>
                  <td><span class="status-code-badge ${{code.startsWith('2') ? 'status-2xx' : code.startsWith('4') ? 'status-4xx' : 'status-5xx'}}">${{code}}</span></td>
                  <td>${{r.description || 'Resposta padrão da API'}}</td>
                </tr>
              `).join('')}}
            </tbody>
          </table>
        </div>
      `;
    }}

    function getDefaultParamValue(p) {{
      if (p.name === 'doc_id') return 'SRC-006';
      if (p.name === 'scenario') return 'CIRCUIT_BREAKER';
      if (p.name === 'flow_id') return 'FL-101';
      if (p.name === 'concept_id') return 'novacao-ccp';
      if (p.name === 'order_id') return 'ORD-20261108-001';
      if (p.name === 'gravame_id') return 'SNG-SP-0000001';
      if (p.name === 'apolice_id') return 'SRO-B3-AUTO-0000001';
      if (p.name === 'sinistro_id') return 'SIN-2026-001';
      if (p.name === 'shares') return '50000';
      if (p.name === 'symbol') return 'PETR4';
      if (p.schema && p.schema.default !== undefined) return p.schema.default;
      return '';
    }}

    function generateSampleRequestBody(ep) {{
      if (!ep.requestBody) return '';
      const content = ep.requestBody.content || {{}};
      const jsonContent = content['application/json'] || {{}};
      if (jsonContent.example) {{
        return JSON.stringify(jsonContent.example, null, 2);
      }}

      // Exemplos contextualizados por endpoint
      if (ep.path === '/api/v1/orders') {{
        return JSON.stringify({{
          "client_order_id": "CLORD-2026-PETR4-001",
          "symbol": "PETR4",
          "side": "BUY",
          "order_type": "LIMIT",
          "quantity": 1000,
          "price": 38.50,
          "time_in_force": "DAY",
          "account": "ACC-8819203"
        }}, null, 2);
      }}
      if (ep.path === '/api/v1/auction/moc') {{
        return JSON.stringify({{
          "client_order_id": "MOC-PETR4-2026-001",
          "symbol": "PETR4",
          "side": "BUY",
          "quantity": 50000,
          "account": "ACC-8819203"
        }}, null, 2);
      }}
      if (ep.path === '/api/v1/sng/gravames') {{
        return JSON.stringify({{
          "contrato_numero": "FIN-2026-994821",
          "modalidade": "ALIENACAO_FIDUCIARIA",
          "credor_cnpj": "60701190000104",
          "credor_nome": "BANCO ITAU S.A.",
          "devedor_documento": "12345678909",
          "devedor_nome": "CARLOS EDUARDO ALMEIDA",
          "veiculo": {{
            "chassi": "9BWAA05U1HP001234",
            "renavam": "11984726351",
            "placa": "BRA2E19",
            "uf_emplacamento": "SP",
            "marca_modelo": "VW POLO HIGHLINE 200 TSI",
            "ano_fabricacao": 2024,
            "ano_modelo": 2025
          }},
          "valor_financiamento": 85000.00,
          "quantidade_parcelas": 48,
          "taxa_juros_anual_pct": 14.85
        }}, null, 2);
      }}
      if (ep.path.includes('/sng/gravames/') && ep.path.endsWith('/baixa')) {{
        return JSON.stringify({{
          "motivo_baixa": "QUITACAO_INTEGRAL_CONTRATO",
          "data_quitacao": "2026-11-08T10:00:00Z",
          "operador_matricula": "OP88192"
        }}, null, 2);
      }}
      if (ep.path === '/api/v1/sro/apolices') {{
        return JSON.stringify({{
          "numero_proposta": "PROP-AGRO-2026-8841",
          "ramo": "RAMO_1101",
          "seguradora_lider_cnpj": "33000118000179",
          "seguradora_lider_nome": "PORTO SEGURO COMPANHIA DE SEGUROS",
          "segurado_documento": "12345678000195",
          "segurado_nome": "AGROPECUARIA VALE VERDE LTDA",
          "data_emissao": "2026-11-08",
          "inicio_vigencia": "2026-11-08",
          "fim_vigencia": "2027-11-08",
          "limite_maximo_garantia": 5000000.00,
          "premio_liquido": 120000.00,
          "iof_aliquota_pct": 7.38,
          "cosseguradores": [
            {{
              "cnpj": "33000118000179",
              "razao_social": "PORTO SEGURO",
              "percentual_quota": 60.00,
              "is_lider": true
            }},
            {{
              "cnpj": "61074175000138",
              "razao_social": "MAPFRE SEGUROS GERAIS S.A.",
              "percentual_quota": 40.00,
              "is_lider": false
            }}
          ]
        }}, null, 2);
      }}
      if (ep.path.includes('/sro/apolices/') && ep.path.endsWith('/sinistros')) {{
        return JSON.stringify({{
          "numero_sinistro": "SIN-2026-001",
          "data_ocorrencia": "2026-11-08",
          "valor_estimado": 750000.00,
          "causa_evento": "TEMPESTADE_GRANIZO"
        }}, null, 2);
      }}
      if (ep.path.endsWith('/liquidar')) {{
        return JSON.stringify({{
          "valor_indenizado": 720000.00,
          "data_pagamento": "2026-11-08",
          "comprovante_spb": "STR2026110899201"
        }}, null, 2);
      }}

      return '{{\\n  "key": "value"\\n}}';
    }}

    function formatRequestBody() {{
      const ta = document.getElementById('request-body-textarea');
      if (!ta) return;
      try {{
        const parsed = JSON.parse(ta.value);
        ta.value = JSON.stringify(parsed, null, 2);
      }} catch (err) {{
        alert('JSON Inválido: ' + err.message);
      }}
    }}

    function resetRequestBody() {{
      if (!currentEndpoint) return;
      const ta = document.getElementById('request-body-textarea');
      if (ta) ta.value = generateSampleRequestBody(currentEndpoint);
    }}

    async function executeInteractiveCall() {{
      if (!currentEndpoint) return;
      const ep = currentEndpoint;
      const btn = document.getElementById('btn-execute-call');
      const responsePanel = document.getElementById('response-panel');
      const statusBadge = document.getElementById('response-status-badge');
      const timeBadge = document.getElementById('response-time-badge');
      const bodyPre = document.getElementById('response-body-pre');

      btn.disabled = true;
      btn.innerText = '⏳ Executando Chamada...';
      responsePanel.style.display = 'block';
      bodyPre.innerText = '// Enviando requisição HTTP ao servidor local B3...';

      let resolvedPath = ep.path;
      const queryParams = new URLSearchParams();

      // Substitui parâmetros de path e query
      document.querySelectorAll('.param-input').forEach(input => {{
        const paramIn = input.dataset.in;
        const paramName = input.dataset.name;
        const val = input.value.trim();
        if (paramIn === 'path') {{
          resolvedPath = resolvedPath.replace('{{' + paramName + '}}', encodeURIComponent(val));
        }} else if (paramIn === 'query' && val) {{
          queryParams.append(paramName, val);
        }}
      }});

      const queryString = queryParams.toString();
      const finalUrl = resolvedPath + (queryString ? '?' + queryString : '');

      const fetchOptions = {{
        method: ep.method,
        headers: {{
          'Accept': 'application/json',
        }},
      }};

      if (['POST', 'PUT', 'PATCH'].includes(ep.method)) {{
        const ta = document.getElementById('request-body-textarea');
        fetchOptions.headers['Content-Type'] = 'application/json';
        fetchOptions.body = ta ? ta.value : '{{}}';
      }}

      const startTime = performance.now();
      try {{
        const res = await fetch(finalUrl, fetchOptions);
        const elapsed = Math.round(performance.now() - startTime);
        const text = await res.text();

        statusBadge.className = 'status-code-badge ' + (res.status >= 200 && res.status < 300 ? 'status-2xx' : res.status < 500 ? 'status-4xx' : 'status-5xx');
        statusBadge.innerText = res.status + ' ' + (res.statusText || 'OK');
        timeBadge.innerText = elapsed + ' ms';

        try {{
          const data = JSON.parse(text);
          bodyPre.innerText = JSON.stringify(data, null, 2);
        }} catch (_) {{
          bodyPre.innerText = text;
        }}
      }} catch (err) {{
        const elapsed = Math.round(performance.now() - startTime);
        statusBadge.className = 'status-code-badge status-5xx';
        statusBadge.innerText = 'ERRO DE CONEXÃO';
        timeBadge.innerText = elapsed + ' ms';
        bodyPre.innerText = 'Erro ao contactar servidor: ' + err.message;
      }} finally {{
        btn.disabled = false;
        btn.innerText = '▶️ Executar Chamada no Servidor Local';
      }}
    }}

    function copyCurlCommand() {{
      if (!currentEndpoint) return;
      let resolvedPath = currentEndpoint.path;
      const queryParams = new URLSearchParams();

      document.querySelectorAll('.param-input').forEach(input => {{
        const paramIn = input.dataset.in;
        const paramName = input.dataset.name;
        const val = input.value.trim();
        if (paramIn === 'path') {{
          resolvedPath = resolvedPath.replace('{{' + paramName + '}}', val);
        }} else if (paramIn === 'query' && val) {{
          queryParams.append(paramName, val);
        }}
      }});

      const qs = queryParams.toString();
      const url = 'http://127.0.0.1:8080' + resolvedPath + (qs ? '?' + qs : '');
      let curl = `curl -X ${{currentEndpoint.method}} "${{url}}"`;

      if (['POST', 'PUT', 'PATCH'].includes(currentEndpoint.method)) {{
        const ta = document.getElementById('request-body-textarea');
        const bodyContent = ta ? ta.value.replace(/\\n/g, '').replace(/"/g, '\\"') : '{{}}';
        curl += ` -H "Content-Type: application/json" -d "${{bodyContent}}"`;
      }}

      navigator.clipboard.writeText(curl).then(() => {{
        alert('Comando cURL copiado com sucesso para a área de transferência!');
      }});
    }}

    function copyResponseBody() {{
      const text = document.getElementById('response-body-pre').innerText;
      navigator.clipboard.writeText(text).then(() => {{
        alert('Resposta JSON copiada com sucesso!');
      }});
    }}

    function renderGuidesTabs() {{
      const bar = document.getElementById('guides-tab-bar');
      bar.innerHTML = guides.map((g, idx) => `
        <button class="guide-btn ${{idx === 0 ? 'active' : ''}}" onclick="selectGuide('${{g.id}}')">
          ${{g.title.split('—')[0].trim()}}
        </button>
      `).join('');
    }}

    function selectGuide(guideId) {{
      const g = guides.find(item => item.id === guideId);
      if (!g) return;

      document.querySelectorAll('.guide-btn').forEach(b => b.classList.remove('active'));
      event.target && event.target.classList.add('active');

      const contentArea = document.getElementById('guide-content-area');
      contentArea.innerHTML = renderMarkdown(g.content);
    }}

    function formatMarkdownDesc(desc) {{
      if (!desc) return '';
      return desc
        .replace(/\\n\\n/g, '<br><br>')
        .replace(/\\*\\*(.*?)\\*\\*/g, '<b>$1</b>')
        .replace(/\\*(.*?)\\*/g, '<i>$1</i>');
    }}

    // Parser simples e resiliente de Markdown offline
    function renderMarkdown(md) {{
      if (!md) return '';
      let html = md
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

      // Code blocks com syntax tags
      html = html.replace(/```([a-z0-9_-]*)\\n([\\s\\S]*?)```/g, (match, lang, code) => {{
        return `<pre><code class="lang-${{lang}}">${{code.trim()}}</code></pre>`;
      }});

      // Headers
      html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');
      html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
      html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
      html = html.replace(/^#### (.*$)/gim, '<h4 style="color: #fff; margin-top: 1rem;">$1</h4>');

      // Bold & Italic
      html = html.replace(/\\*\\*(.*?)\\*\\*/gim, '<b>$1</b>');
      html = html.replace(/\\*(.*?)\\*/gim, '<i>$1</i>');

      // Inline code
      html = html.replace(/`([^`]+)`/gim, '<code>$1</code>');

      // Blockquotes
      html = html.replace(/^> (.*$)/gim, '<blockquote style="border-left: 4px solid var(--accent-gold); padding-left: 1rem; color: #d1d5db; margin: 1rem 0;">$1</blockquote>');

      // Unordered lists
      html = html.replace(/^\\* (.*$)/gim, '<li>$1</li>');
      html = html.replace(/^- (.*$)/gim, '<li>$1</li>');

      // Tables
      html = html.replace(/\\|(.+)\\|/g, (match) => {{
        const cells = match.split('|').filter(c => c.trim() !== '');
        if (cells.some(c => c.includes('---'))) return '';
        const tds = cells.map(c => `<td>${{c.trim()}}</td>`).join('');
        return `<tr>${{tds}}</tr>`;
      }});

      html = html.replace(/(<tr>[\\s\\S]*?<\\/tr>)/g, '<table style="width: 100%; border-collapse: collapse; margin: 1rem 0;">$1</table>');

      // Paragraphs
      html = html.replace(/\\n\\n/g, '<p></p>');

      return html;
    }}
  </script>
</body>
</html>
"""
    return html
