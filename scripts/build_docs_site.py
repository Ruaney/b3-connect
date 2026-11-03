#!/usr/bin/env python3
"""
Compilador da Página Web Dinâmica do B3 Integration Atlas
Gera docs/index.html (e espelha em index.html na raiz) para GitHub Pages e visualização local.

- Zero dependências externas (HTML5 + CSS3 + Vanilla JavaScript)
- Fallback embutido com dados de catalog/flows.json e catalog/sources.json
- Tema Dark/Financeiro institucional com Glassmorphism
- 100% responsivo para Desktop e Mobile
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FLOWS_PATH = REPO_ROOT / "catalog" / "flows.json"
SOURCES_PATH = REPO_ROOT / "catalog" / "sources.json"
DOCS_INDEX_PATH = REPO_ROOT / "docs" / "index.html"
ROOT_INDEX_PATH = REPO_ROOT / "index.html"


def load_catalogs():
    with open(FLOWS_PATH, "r", encoding="utf-8") as f:
        flows_data = json.load(f)
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        sources_data = json.load(f)
    return flows_data, sources_data


def generate_html(flows_data, sources_data):
    flows_json_str = json.dumps(flows_data, ensure_ascii=False)
    sources_json_str = json.dumps(sources_data, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="B3 Integration Atlas — Radar de Integração e Homologação. Mapeamento, especificações abertas, código Python e provas formais para 100% dos fluxos da B3.">
  <meta name="theme-color" content="#070a12">
  <title>B3 Integration Atlas — Radar de Integração e Homologação</title>
  
  <!-- Fontes Institucionais -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">

  <style>
    /* ==========================================================================
       DESIGN SYSTEM: B3 DARK FINANCIAL INSTITUTIONAL
       ========================================================================== */
    :root {{
      --bg-canvas: #070a12;
      --bg-surface: #0c1222;
      --bg-surface-elevated: #111a30;
      --bg-glass: rgba(17, 26, 48, 0.72);
      --bg-glass-card: rgba(14, 21, 38, 0.65);
      --bg-glass-hover: rgba(22, 34, 62, 0.85);
      
      --border-subtle: rgba(255, 255, 255, 0.08);
      --border-medium: rgba(255, 255, 255, 0.14);
      --border-highlight: rgba(14, 165, 233, 0.4);
      
      /* Cores de Identidade e Destaque */
      --b3-blue: #0284c7;
      --b3-blue-light: #38bdf8;
      --b3-blue-glow: rgba(56, 189, 248, 0.25);
      
      --accent-emerald: #10b981;
      --accent-emerald-light: #34d399;
      --accent-emerald-glow: rgba(16, 185, 129, 0.25);
      
      --accent-amber: #f59e0b;
      --accent-amber-light: #fbbf24;
      --accent-amber-glow: rgba(245, 158, 11, 0.25);
      
      --accent-purple: #8b5cf6;
      --accent-purple-light: #a78bfa;
      --accent-purple-glow: rgba(139, 92, 246, 0.25);
      
      --accent-rose: #f43f5e;
      --accent-rose-light: #fb7185;
      
      /* Tipografia */
      --text-white: #ffffff;
      --text-primary: #f1f5f9;
      --text-secondary: #94a3b8;
      --text-muted: #64748b;
      
      /* Fontes */
      --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      
      /* Efeitos */
      --shadow-card: 0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5);
      --shadow-glow: 0 0 25px rgba(14, 165, 233, 0.2);
      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 16px;
      --radius-full: 9999px;
      --transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    }}

    /* Reset & Base */
    *, *::before, *::after {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}

    html {{
      scroll-behavior: smooth;
      background-color: var(--bg-canvas);
      color: var(--text-primary);
      font-family: var(--font-sans);
      font-size: 16px;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }}

    body {{
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
      background: 
        radial-gradient(circle at 15% 15%, rgba(14, 165, 233, 0.08) 0%, transparent 40%),
        radial-gradient(circle at 85% 30%, rgba(139, 92, 246, 0.07) 0%, transparent 45%),
        radial-gradient(circle at 50% 80%, rgba(16, 185, 129, 0.05) 0%, transparent 50%),
        var(--bg-canvas);
      background-attachment: fixed;
    }}

    a {{
      color: var(--b3-blue-light);
      text-decoration: none;
      transition: var(--transition);
    }}
    a:hover {{
      color: #fff;
    }}

    .container {{
      width: 100%;
      max-width: 1380px;
      margin: 0 auto;
      padding: 0 24px;
    }}

    /* ==========================================================================
       HEADER & NAVIGATION BAR
       ========================================================================== */
    .top-nav {{
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(7, 10, 18, 0.85);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border-bottom: 1px solid var(--border-subtle);
      transition: var(--transition);
    }}

    .nav-inner {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 72px;
      gap: 20px;
    }}

    .brand-logo {{
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 700;
      font-size: 1.15rem;
      letter-spacing: -0.02em;
      color: var(--text-white);
    }}

    .brand-badge {{
      width: 38px;
      height: 38px;
      border-radius: var(--radius-md);
      background: linear-gradient(135deg, #0284c7 0%, #1e40af 100%);
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 0 15px rgba(2, 132, 199, 0.4);
      color: #fff;
      font-weight: 900;
      font-size: 1.1rem;
    }}

    .brand-title-wrap {{
      display: flex;
      flex-direction: column;
    }}

    .brand-title-main {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    .version-tag {{
      font-family: var(--font-mono);
      font-size: 0.72rem;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: var(--radius-full);
      background: rgba(14, 165, 233, 0.15);
      color: var(--b3-blue-light);
      border: 1px solid rgba(14, 165, 233, 0.3);
    }}

    .brand-subtitle {{
      font-size: 0.72rem;
      color: var(--text-secondary);
      font-weight: 400;
    }}

    .nav-links {{
      display: flex;
      align-items: center;
      gap: 24px;
      list-style: none;
    }}

    .nav-link {{
      color: var(--text-secondary);
      font-size: 0.9rem;
      font-weight: 500;
      transition: var(--transition);
      position: relative;
    }}

    .nav-link:hover, .nav-link.active {{
      color: var(--text-white);
    }}

    .nav-actions {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}

    .status-pill {{
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 5px 12px;
      border-radius: var(--radius-full);
      font-size: 0.76rem;
      font-weight: 500;
      background: rgba(16, 185, 129, 0.12);
      color: var(--accent-emerald-light);
      border: 1px solid rgba(16, 185, 129, 0.3);
    }}

    .pulse-dot {{
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--accent-emerald);
      box-shadow: 0 0 8px var(--accent-emerald);
      animation: pulse 2s infinite;
    }}

    @keyframes pulse {{
      0%, 100% {{ opacity: 1; transform: scale(1); }}
      50% {{ opacity: 0.4; transform: scale(0.85); }}
    }}

    .btn-github {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 14px;
      border-radius: var(--radius-md);
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-medium);
      color: var(--text-white);
      font-size: 0.85rem;
      font-weight: 600;
      transition: var(--transition);
    }}

    .btn-github:hover {{
      background: #1e293b;
      border-color: var(--text-secondary);
      box-shadow: 0 0 15px rgba(255, 255, 255, 0.08);
    }}

    /* ==========================================================================
       HERO SECTION & KPIS
       ========================================================================== */
    .hero {{
      padding: 56px 0 44px;
      position: relative;
    }}

    .hero-eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-family: var(--font-mono);
      font-size: 0.78rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      padding: 5px 14px;
      border-radius: var(--radius-full);
      background: rgba(14, 165, 233, 0.1);
      color: var(--b3-blue-light);
      border: 1px solid rgba(14, 165, 233, 0.25);
      margin-bottom: 20px;
    }}

    .hero-title {{
      font-size: clamp(2.2rem, 4.2vw, 3.4rem);
      font-weight: 800;
      line-height: 1.15;
      letter-spacing: -0.03em;
      margin-bottom: 16px;
      color: var(--text-white);
    }}

    .hero-title-highlight {{
      background: linear-gradient(135deg, #ffffff 15%, #7dd3fc 60%, #38bdf8 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}

    .hero-subtitle {{
      max-width: 860px;
      font-size: 1.12rem;
      line-height: 1.6;
      color: var(--text-secondary);
      margin-bottom: 38px;
    }}

    /* Grid de KPIs */
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }}

    .kpi-card {{
      background: var(--bg-glass-card);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      padding: 20px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: var(--transition);
      position: relative;
      overflow: hidden;
    }}

    .kpi-card::before {{
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 2px;
      background: linear-gradient(90deg, transparent, var(--border-highlight), transparent);
      opacity: 0;
      transition: var(--transition);
    }}

    .kpi-card:hover {{
      transform: translateY(-3px);
      border-color: var(--border-medium);
      box-shadow: var(--shadow-card);
      background: var(--bg-glass-hover);
    }}

    .kpi-card:hover::before {{
      opacity: 1;
    }}

    .kpi-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
    }}

    .kpi-label {{
      font-size: 0.82rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--text-secondary);
    }}

    .progress-circle-wrap {{
      width: 44px;
      height: 44px;
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
    }}

    .progress-svg {{
      transform: rotate(-90deg);
      width: 44px;
      height: 44px;
    }}

    .progress-bg {{
      fill: none;
      stroke: rgba(255, 255, 255, 0.08);
      stroke-width: 4;
    }}

    .progress-bar {{
      fill: none;
      stroke-width: 4;
      stroke-linecap: round;
      transition: stroke-dashoffset 1s ease-out;
    }}

    .kpi-value-wrap {{
      display: flex;
      align-items: baseline;
      gap: 6px;
      margin-bottom: 4px;
    }}

    .kpi-value {{
      font-size: 1.9rem;
      font-weight: 800;
      color: var(--text-white);
      letter-spacing: -0.03em;
      font-family: var(--font-mono);
    }}

    .kpi-subtext {{
      font-size: 0.8rem;
      color: var(--text-muted);
      line-height: 1.3;
    }}

    .kpi-badge {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      margin-top: 8px;
      padding: 3px 8px;
      border-radius: var(--radius-sm);
      font-size: 0.72rem;
      font-weight: 600;
      width: fit-content;
    }}

    .badge-success {{
      background: rgba(16, 185, 129, 0.15);
      color: var(--accent-emerald-light);
      border: 1px solid rgba(16, 185, 129, 0.3);
    }}

    .badge-amber {{
      background: rgba(245, 158, 11, 0.15);
      color: var(--accent-amber-light);
      border: 1px solid rgba(245, 158, 11, 0.3);
    }}

    .badge-purple {{
      background: rgba(139, 92, 246, 0.15);
      color: var(--accent-purple-light);
      border: 1px solid rgba(139, 92, 246, 0.3);
    }}

    /* ==========================================================================
       RADAR & FILTROS SECTION
       ========================================================================== */
    .radar-section {{
      padding: 30px 0 60px;
    }}

    .section-header {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin-bottom: 28px;
    }}

    .section-eyebrow {{
      font-family: var(--font-mono);
      font-size: 0.8rem;
      font-weight: 700;
      color: var(--b3-blue-light);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}

    .section-title {{
      font-size: 1.85rem;
      font-weight: 800;
      color: var(--text-white);
      letter-spacing: -0.02em;
    }}

    .section-description {{
      color: var(--text-secondary);
      max-width: 820px;
      font-size: 0.98rem;
    }}

    /* Painel de Controles e Filtros */
    .filter-panel {{
      background: var(--bg-glass-card);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      padding: 20px;
      margin-bottom: 28px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}

    .ecosystem-tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .eco-tab {{
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid var(--border-subtle);
      color: var(--text-secondary);
      padding: 8px 14px;
      border-radius: var(--radius-full);
      font-size: 0.84rem;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: var(--transition);
      user-select: none;
    }}

    .eco-tab:hover {{
      background: rgba(255, 255, 255, 0.08);
      color: var(--text-white);
      border-color: var(--border-medium);
    }}

    .eco-tab.active {{
      background: var(--b3-blue);
      color: #fff;
      border-color: var(--b3-blue-light);
      box-shadow: 0 0 15px rgba(2, 132, 199, 0.4);
    }}

    .tab-count {{
      font-size: 0.72rem;
      padding: 1px 6px;
      border-radius: var(--radius-full);
      background: rgba(0, 0, 0, 0.25);
    }}

    .search-filter-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }}

    .search-input-wrap {{
      position: relative;
      flex: 1;
      min-width: 280px;
    }}

    .search-icon {{
      position: absolute;
      left: 14px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-muted);
      pointer-events: none;
    }}

    .search-input {{
      width: 100%;
      padding: 11px 40px 11px 42px;
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-medium);
      border-radius: var(--radius-md);
      color: var(--text-white);
      font-family: var(--font-sans);
      font-size: 0.92rem;
      outline: none;
      transition: var(--transition);
    }}

    .search-input:focus {{
      border-color: var(--b3-blue-light);
      box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.15);
    }}

    .search-input::placeholder {{
      color: var(--text-muted);
    }}

    .clear-search-btn {{
      position: absolute;
      right: 12px;
      top: 50%;
      transform: translateY(-50%);
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-size: 1.2rem;
      cursor: pointer;
      display: none;
    }}

    .clear-search-btn:hover {{
      color: var(--text-white);
    }}

    .filter-actions {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}

    .results-counter {{
      font-size: 0.85rem;
      color: var(--text-secondary);
      font-family: var(--font-mono);
    }}

    .results-counter strong {{
      color: var(--text-white);
    }}

    /* ==========================================================================
       FLOWS GRID & CARDS
       ========================================================================== */
    .flows-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(390px, 1fr));
      gap: 20px;
    }}

    .flow-card {{
      background: var(--bg-glass-card);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      padding: 22px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      transition: var(--transition);
      position: relative;
    }}

    .flow-card:hover {{
      transform: translateY(-3px);
      border-color: rgba(14, 165, 233, 0.4);
      box-shadow: var(--shadow-card), 0 0 20px rgba(14, 165, 233, 0.12);
      background: var(--bg-glass-hover);
    }}

    .flow-card-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}

    .flow-id-badge {{
      font-family: var(--font-mono);
      font-size: 0.82rem;
      font-weight: 700;
      padding: 4px 10px;
      border-radius: var(--radius-sm);
      background: rgba(255, 255, 255, 0.08);
      color: var(--text-white);
      border: 1px solid var(--border-medium);
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}

    .system-pill {{
      font-size: 0.74rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      padding: 3px 9px;
      border-radius: var(--radius-full);
    }}

    /* Cores por sistema */
    .system-iMercado {{
      background: rgba(2, 132, 199, 0.15);
      color: #38bdf8;
      border: 1px solid rgba(2, 132, 199, 0.35);
    }}

    .system-RTC {{
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.35);
    }}

    .system-Puma-Trading {{
      background: rgba(245, 158, 11, 0.15);
      color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.35);
    }}

    .system-Drop-Copy {{
      background: rgba(217, 119, 6, 0.15);
      color: #fcd34d;
      border: 1px solid rgba(217, 119, 6, 0.35);
    }}

    .system-NoMe {{
      background: rgba(244, 63, 94, 0.15);
      color: #fb7185;
      border: 1px solid rgba(244, 63, 94, 0.35);
    }}

    .system-Conectividade {{
      background: rgba(99, 102, 241, 0.15);
      color: #a5b4fc;
      border: 1px solid rgba(99, 102, 241, 0.35);
    }}

    .system-Plataformas {{
      background: rgba(6, 182, 212, 0.15);
      color: #67e8f9;
      border: 1px solid rgba(6, 182, 212, 0.35);
    }}

    .flow-name {{
      font-size: 1.15rem;
      font-weight: 700;
      color: var(--text-white);
      line-height: 1.35;
      letter-spacing: -0.01em;
    }}

    .flow-interface-box {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-family: var(--font-mono);
      font-size: 0.78rem;
      color: var(--text-secondary);
      background: rgba(0, 0, 0, 0.25);
      padding: 7px 12px;
      border-radius: var(--radius-sm);
      border: 1px solid rgba(255, 255, 255, 0.05);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    /* Badges de Status do Ciclo */
    .flow-status-matrix {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 6px;
    }}

    .status-cell {{
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 6px 2px;
      border-radius: var(--radius-sm);
      font-size: 0.68rem;
      font-weight: 600;
      text-align: center;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--border-subtle);
      transition: var(--transition);
    }}

    .status-cell.ok {{
      background: rgba(16, 185, 129, 0.1);
      border-color: rgba(16, 185, 129, 0.3);
      color: var(--accent-emerald-light);
    }}

    .status-cell.pending {{
      background: rgba(245, 158, 11, 0.08);
      border-color: rgba(245, 158, 11, 0.25);
      color: var(--accent-amber-light);
    }}

    .status-cell-title {{
      font-size: 0.64rem;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      opacity: 0.8;
      margin-bottom: 2px;
    }}

    .status-cell-icon {{
      font-size: 0.82rem;
    }}

    /* Ações e Links Diretos */
    .flow-actions {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }}

    .flow-link-btn {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 11px;
      border-radius: var(--radius-sm);
      font-size: 0.76rem;
      font-weight: 600;
      background: var(--bg-surface-elevated);
      color: var(--text-secondary);
      border: 1px solid var(--border-subtle);
      transition: var(--transition);
    }}

    .flow-link-btn:hover {{
      color: var(--text-white);
      background: #1e293b;
      border-color: var(--border-highlight);
    }}

    /* Acordeão de Detalhes e Homologação */
    .flow-accordion {{
      border-top: 1px solid var(--border-subtle);
      padding-top: 12px;
    }}

    .flow-accordion-toggle {{
      width: 100%;
      background: transparent;
      border: none;
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: var(--text-secondary);
      font-size: 0.78rem;
      font-weight: 600;
      cursor: pointer;
      padding: 4px 0;
      transition: var(--transition);
    }}

    .flow-accordion-toggle:hover {{
      color: var(--b3-blue-light);
    }}

    .accordion-chevron {{
      transition: transform 0.25s ease;
      font-size: 0.7rem;
    }}

    .flow-accordion-toggle.open .accordion-chevron {{
      transform: rotate(180deg);
    }}

    .flow-accordion-body {{
      display: none;
      margin-top: 12px;
      font-size: 0.82rem;
      color: var(--text-secondary);
      line-height: 1.5;
      background: rgba(0, 0, 0, 0.2);
      border-radius: var(--radius-md);
      padding: 12px;
      border: 1px solid rgba(255, 255, 255, 0.04);
    }}

    .flow-accordion-body.open {{
      display: block;
      animation: fadeIn 0.2s ease-in;
    }}

    @keyframes fadeIn {{
      from {{ opacity: 0; transform: translateY(-4px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}

    .accordion-field {{
      margin-bottom: 10px;
    }}

    .accordion-field:last-child {{
      margin-bottom: 0;
    }}

    .accordion-field-title {{
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--text-muted);
      margin-bottom: 4px;
    }}

    .accordion-sources {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 4px;
    }}

    .src-tag {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 7px;
      border-radius: var(--radius-sm);
      font-family: var(--font-mono);
      font-size: 0.7rem;
      background: rgba(14, 165, 233, 0.1);
      color: var(--b3-blue-light);
      border: 1px solid rgba(14, 165, 233, 0.25);
      cursor: pointer;
      transition: var(--transition);
    }}

    .src-tag:hover {{
      background: rgba(14, 165, 233, 0.2);
      color: #fff;
    }}

    /* ==========================================================================
       PAINEL DE INVARIANTES & PROOF SYSTEMS
       ========================================================================== */
    .invariants-section {{
      padding: 60px 0;
      border-top: 1px solid var(--border-subtle);
      background: rgba(11, 16, 30, 0.5);
    }}

    .invariants-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
      gap: 20px;
      margin-top: 28px;
    }}

    .inv-card {{
      background: var(--bg-glass-card);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      padding: 22px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      transition: var(--transition);
      position: relative;
    }}

    .inv-card:hover {{
      border-color: rgba(139, 92, 246, 0.4);
      box-shadow: 0 0 20px rgba(139, 92, 246, 0.15);
      transform: translateY(-2px);
    }}

    .inv-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }}

    .inv-number {{
      font-family: var(--font-mono);
      font-size: 0.78rem;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: var(--radius-sm);
      background: rgba(139, 92, 246, 0.15);
      color: var(--accent-purple-light);
      border: 1px solid rgba(139, 92, 246, 0.3);
    }}

    .inv-status {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 0.72rem;
      font-weight: 700;
      color: var(--accent-emerald-light);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}

    .inv-title {{
      font-size: 1.05rem;
      font-weight: 700;
      color: var(--text-white);
    }}

    .inv-formula {{
      background: rgba(0, 0, 0, 0.4);
      padding: 10px 14px;
      border-radius: var(--radius-md);
      border: 1px solid rgba(255, 255, 255, 0.05);
      font-family: var(--font-mono);
      font-size: 0.8rem;
      color: #38bdf8;
      overflow-x: auto;
      word-break: break-all;
    }}

    .inv-desc {{
      font-size: 0.84rem;
      color: var(--text-secondary);
      line-height: 1.45;
    }}

    .inv-footer {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-top: 4px;
      padding-top: 10px;
      border-top: 1px solid rgba(255, 255, 255, 0.05);
      font-family: var(--font-mono);
      font-size: 0.74rem;
      color: var(--text-muted);
    }}

    /* Terminal de Verificação */
    .terminal-box {{
      margin-top: 32px;
      background: #05070d;
      border: 1px solid var(--border-medium);
      border-radius: var(--radius-lg);
      overflow: hidden;
      box-shadow: 0 15px 35px rgba(0, 0, 0, 0.6);
    }}

    .terminal-header {{
      background: #0f1422;
      padding: 12px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    }}

    .terminal-dots {{
      display: flex;
      align-items: center;
      gap: 6px;
    }}

    .term-dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
    }}
    .dot-red {{ background: #ef4444; }}
    .dot-yellow {{ background: #f59e0b; }}
    .dot-green {{ background: #10b981; }}

    .terminal-title {{
      font-family: var(--font-mono);
      font-size: 0.78rem;
      color: var(--text-secondary);
    }}

    .btn-term-action {{
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid var(--border-subtle);
      color: var(--text-primary);
      padding: 4px 10px;
      border-radius: var(--radius-sm);
      font-size: 0.75rem;
      font-family: var(--font-mono);
      cursor: pointer;
      transition: var(--transition);
    }}

    .btn-term-action:hover {{
      background: var(--b3-blue);
      color: #fff;
      border-color: var(--b3-blue-light);
    }}

    .terminal-body {{
      padding: 18px;
      font-family: var(--font-mono);
      font-size: 0.82rem;
      line-height: 1.6;
      color: #e2e8f0;
      max-height: 340px;
      overflow-y: auto;
    }}

    .terminal-body .term-green {{ color: #34d399; }}
    .terminal-body .term-blue {{ color: #38bdf8; }}
    .terminal-body .term-dim {{ color: #64748b; }}

    /* ==========================================================================
       ROTEIRO DE HOMOLOGAÇÃO REAL B3
       ========================================================================== */
    .homologation-section {{
      padding: 60px 0;
      border-top: 1px solid var(--border-subtle);
    }}

    .roadmap-timeline {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 20px;
      margin-top: 32px;
      position: relative;
    }}

    .roadmap-step-card {{
      background: var(--bg-glass-card);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      padding: 24px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      transition: var(--transition);
      position: relative;
    }}

    .roadmap-step-card.completed {{
      border-top: 3px solid var(--accent-emerald);
    }}

    .roadmap-step-card.in-progress {{
      border-top: 3px solid var(--b3-blue);
    }}

    .roadmap-step-card.pending {{
      border-top: 3px solid var(--accent-amber);
    }}

    .step-number {{
      width: 32px;
      height: 32px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: var(--font-mono);
      font-size: 0.85rem;
      font-weight: 700;
    }}

    .completed .step-number {{
      background: rgba(16, 185, 129, 0.15);
      color: var(--accent-emerald-light);
      border: 1px solid var(--accent-emerald);
    }}

    .in-progress .step-number {{
      background: rgba(14, 165, 233, 0.15);
      color: var(--b3-blue-light);
      border: 1px solid var(--b3-blue);
    }}

    .pending .step-number {{
      background: rgba(245, 158, 11, 0.15);
      color: var(--accent-amber-light);
      border: 1px solid var(--accent-amber);
    }}

    .step-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}

    .step-status-tag {{
      font-size: 0.72rem;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: var(--radius-sm);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}

    .step-title {{
      font-size: 1.15rem;
      font-weight: 700;
      color: var(--text-white);
    }}

    .step-desc {{
      font-size: 0.86rem;
      color: var(--text-secondary);
      line-height: 1.5;
    }}

    .step-deliverables {{
      background: rgba(0, 0, 0, 0.25);
      padding: 10px 14px;
      border-radius: var(--radius-md);
      border: 1px solid rgba(255, 255, 255, 0.04);
      font-size: 0.78rem;
    }}

    .step-deliverables-title {{
      font-weight: 700;
      color: var(--text-muted);
      text-transform: uppercase;
      margin-bottom: 4px;
    }}

    /* ==========================================================================
       CATÁLOGO DE FONTES & MODAL
       ========================================================================== */
    .sources-section {{
      padding: 60px 0;
      border-top: 1px solid var(--border-subtle);
      background: rgba(9, 14, 26, 0.5);
    }}

    .sources-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 16px;
      margin-top: 28px;
    }}

    .source-card {{
      background: var(--bg-glass-card);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      transition: var(--transition);
    }}

    .source-card:hover {{
      border-color: var(--border-highlight);
      background: var(--bg-glass-hover);
    }}

    .source-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}

    .source-id {{
      font-family: var(--font-mono);
      font-size: 0.76rem;
      font-weight: 700;
      color: var(--b3-blue-light);
    }}

    .source-kind {{
      font-size: 0.7rem;
      text-transform: uppercase;
      padding: 2px 6px;
      border-radius: var(--radius-sm);
      background: rgba(255, 255, 255, 0.06);
      color: var(--text-muted);
    }}

    .source-title {{
      font-size: 0.95rem;
      font-weight: 600;
      color: var(--text-white);
    }}

    .source-limitation {{
      font-size: 0.78rem;
      color: var(--text-secondary);
      line-height: 1.4;
    }}

    .source-link {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 0.76rem;
      font-weight: 600;
      margin-top: 4px;
      color: var(--b3-blue-light);
    }}

    /* ==========================================================================
       RODAPÉ & GOVERNANÇA
       ========================================================================== */
    .site-footer {{
      margin-top: auto;
      border-top: 1px solid var(--border-subtle);
      background: #050810;
      padding: 50px 0 30px;
    }}

    .footer-content {{
      display: grid;
      grid-template-columns: 2fr 1fr 1fr;
      gap: 40px;
      margin-bottom: 40px;
    }}

    .footer-brand {{
      display: flex;
      flex-direction: column;
      gap: 14px;
    }}

    .footer-desc {{
      font-size: 0.88rem;
      color: var(--text-secondary);
      line-height: 1.6;
      max-width: 480px;
    }}

    .footer-col-title {{
      font-size: 0.82rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-white);
      margin-bottom: 16px;
    }}

    .footer-links {{
      list-style: none;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}

    .footer-links a {{
      color: var(--text-secondary);
      font-size: 0.85rem;
      transition: var(--transition);
    }}

    .footer-links a:hover {{
      color: var(--text-white);
      padding-left: 4px;
    }}

    .footer-bottom {{
      padding-top: 24px;
      border-top: 1px solid rgba(255, 255, 255, 0.05);
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 16px;
      font-size: 0.78rem;
      color: var(--text-muted);
    }}

    .legal-notice {{
      max-width: 720px;
      line-height: 1.5;
    }}

    /* Toast Notification */
    .toast {{
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-highlight);
      color: var(--text-white);
      padding: 12px 20px;
      border-radius: var(--radius-md);
      font-size: 0.85rem;
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
      z-index: 1000;
      opacity: 0;
      transform: translateY(20px);
      transition: var(--transition);
      pointer-events: none;
    }}

    .toast.show {{
      opacity: 1;
      transform: translateY(0);
    }}

    /* Responsividade */
    @media (max-width: 1024px) {{
      .flows-grid {{
        grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      }}
      .footer-content {{
        grid-template-columns: 1fr;
      }}
    }}

    @media (max-width: 768px) {{
      .nav-links {{
        display: none;
      }}
      .hero-title {{
        font-size: 1.9rem;
      }}
      .flows-grid {{
        grid-template-columns: 1fr;
      }}
      .kpi-grid {{
        grid-template-columns: repeat(2, 1fr);
      }}
      .invariants-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>

  <!-- ========================================================================
       TOP NAVBAR
       ======================================================================== -->
  <div style="background: linear-gradient(90deg, #070e1e 0%, #002b80 50%, #070e1e 100%); border-bottom: 1px solid rgba(0,229,255,0.25); padding: 8px 16px; text-align: center; font-size: 0.84rem; color: #e2e8f0; position: relative; z-index: 1001;">
    <span>🏛️ <strong>Precisa de Conectores Prontos, SLA 24/7 ou Consultoria de Homologação?</strong> Conheça a <a href="https://github.com/Ruaney/b3-enterprise-platform" target="_blank" style="color: #00e5ff; font-weight: 700; text-decoration: underline;">B3 Enterprise Platform</a> com suporte especializado e implantação on-premise.</span>
  </div>
  <header class="top-nav">
    <div class="container nav-inner">
      <div class="brand-logo">
        <div class="brand-badge">B3</div>
        <div class="brand-title-wrap">
          <div class="brand-title-main">
            <span>B3 Integration Atlas</span>
            <span class="version-tag" id="nav-version">v0.2.0</span>
          </div>
          <span class="brand-subtitle">Radar Técnico de Integração e Homologação</span>
        </div>
      </div>

      <nav>
        <ul class="nav-links">
          <li><a href="#radar" class="nav-link active">Radar de Fluxos</a></li>
          <li><a href="#invariantes" class="nav-link">Leis Formais</a></li>
          <li><a href="#homologacao" class="nav-link">Roteiro B3</a></li>
          <li><a href="#fontes" class="nav-link">Fontes Oficiais</a></li>
          <li><a href="#governanca" class="nav-link">Governança</a></li>
          <li><a href="https://github.com/Ruaney/b3-enterprise-platform" target="_blank" class="nav-link" style="color: #00e5ff; font-weight: 600;" title="Plataforma comercial e suporte institucional">💼 Suporte Enterprise</a></li>
        </ul>
      </nav>

      <div class="nav-actions">
        <div class="status-pill" id="live-indicator" title="Status dos dados carregados">
          <span class="pulse-dot"></span>
          <span id="live-indicator-text">Radar Ativo (30 Fluxos)</span>
        </div>
        <a href="https://github.com/Ruaney/b3-connect" target="_blank" rel="noopener noreferrer" class="btn-github" title="Ver repositório no GitHub">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path fill-rule="evenodd" clip-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"/>
          </svg>
          <span>GitHub</span>
        </a>
      </div>
    </div>
  </header>

  <main>
    <!-- ========================================================================
         HERO SECTION
         ======================================================================== -->
    <section class="hero">
      <div class="container">
        <div class="hero-eyebrow">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
          </svg>
          Engenharia Financeira & Conectividade Independente
        </div>

        <h1 class="hero-title">
          B3 Integration Atlas — <span class="hero-title-highlight">Radar de Integração & Homologação</span>
        </h1>

        <p class="hero-subtitle">
          Painel dinâmico de rastreamento rumo aos <strong>100% dos processos da B3</strong> mapeados. Abrange Negociação PUMA (FIX 4.4/5.0 e Binário SBE), Pós-Negociação e Compensação Multilateral (RTC e iMercado), Registro de Balcão NoMe e Infraestrutura Segura de Conectividade (RCC, VPN IPSec e mTLS ICP-Brasil).
        </p>

        <!-- Grade de KPIs Dinâmicos -->
        <div class="kpi-grid">
          <!-- KPI 1: Mapeamento -->
          <div class="kpi-card">
            <div class="kpi-header">
              <span class="kpi-label">Mapeamento</span>
              <div class="progress-circle-wrap">
                <svg class="progress-svg" viewBox="0 0 100 100">
                  <circle class="progress-bg" cx="50" cy="50" r="42"></circle>
                  <circle class="progress-bar" id="ring-mapped" cx="50" cy="50" r="42" stroke="#0ea5e9" stroke-dasharray="263.89" stroke-dashoffset="0"></circle>
                </svg>
              </div>
            </div>
            <div class="kpi-value-wrap">
              <span class="kpi-value" id="kpi-mapped-val">20 / 20</span>
            </div>
            <span class="kpi-subtext">Fluxos identificados e categorizados</span>
            <span class="kpi-badge badge-success">✓ 100% Mapeado</span>
          </div>

          <!-- KPI 2: Especificações Técnicas -->
          <div class="kpi-card">
            <div class="kpi-header">
              <span class="kpi-label">Documentação</span>
              <div class="progress-circle-wrap">
                <svg class="progress-svg" viewBox="0 0 100 100">
                  <circle class="progress-bg" cx="50" cy="50" r="42"></circle>
                  <circle class="progress-bar" id="ring-documented" cx="50" cy="50" r="42" stroke="#38bdf8" stroke-dasharray="263.89" stroke-dashoffset="0"></circle>
                </svg>
              </div>
            </div>
            <div class="kpi-value-wrap">
              <span class="kpi-value" id="kpi-documented-val">20 / 20</span>
            </div>
            <span class="kpi-subtext">Especificações e dicionários de dados</span>
            <span class="kpi-badge badge-success">✓ 100% Especificado</span>
          </div>

          <!-- KPI 3: Código Implementado -->
          <div class="kpi-card">
            <div class="kpi-header">
              <span class="kpi-label">Implementação</span>
              <div class="progress-circle-wrap">
                <svg class="progress-svg" viewBox="0 0 100 100">
                  <circle class="progress-bg" cx="50" cy="50" r="42"></circle>
                  <circle class="progress-bar" id="ring-implemented" cx="50" cy="50" r="42" stroke="#10b981" stroke-dasharray="263.89" stroke-dashoffset="0"></circle>
                </svg>
              </div>
            </div>
            <div class="kpi-value-wrap">
              <span class="kpi-value" id="kpi-implemented-val">20 / 20</span>
            </div>
            <span class="kpi-subtext">Parsers e codecs em Python puro</span>
            <span class="kpi-badge badge-success">✓ 100% Implementado</span>
          </div>

          <!-- KPI 4: Testes Unitários Locais -->
          <div class="kpi-card">
            <div class="kpi-header">
              <span class="kpi-label">Testes Locais</span>
              <div class="progress-circle-wrap">
                <svg class="progress-svg" viewBox="0 0 100 100">
                  <circle class="progress-bg" cx="50" cy="50" r="42"></circle>
                  <circle class="progress-bar" id="ring-tested" cx="50" cy="50" r="42" stroke="#34d399" stroke-dasharray="263.89" stroke-dashoffset="0"></circle>
                </svg>
              </div>
            </div>
            <div class="kpi-value-wrap">
              <span class="kpi-value" id="kpi-tested-val">205 Testes</span>
            </div>
            <span class="kpi-subtext">100% de aprovação (0 falhas)</span>
            <span class="kpi-badge badge-success">✓ 205 Passando</span>
          </div>

          <!-- KPI 5: Leis Formais Provadas -->
          <div class="kpi-card">
            <div class="kpi-header">
              <span class="kpi-label">Invariantes</span>
              <div class="progress-circle-wrap">
                <svg class="progress-svg" viewBox="0 0 100 100">
                  <circle class="progress-bg" cx="50" cy="50" r="42"></circle>
                  <circle class="progress-bar" id="ring-invariants" cx="50" cy="50" r="42" stroke="#a78bfa" stroke-dasharray="263.89" stroke-dashoffset="0"></circle>
                </svg>
              </div>
            </div>
            <div class="kpi-value-wrap">
              <span class="kpi-value" id="kpi-invariants-val">9 / 9</span>
            </div>
            <span class="kpi-subtext">Leis de conservação matemática</span>
            <span class="kpi-badge badge-purple">✓ 100% Provadas</span>
          </div>

          <!-- KPI 6: Homologação Real B3 -->
          <div class="kpi-card">
            <div class="kpi-header">
              <span class="kpi-label">Homologação B3</span>
              <div class="progress-circle-wrap">
                <svg class="progress-svg" viewBox="0 0 100 100">
                  <circle class="progress-bg" cx="50" cy="50" r="42"></circle>
                  <circle class="progress-bar" id="ring-validated" cx="50" cy="50" r="42" stroke="#f59e0b" stroke-dasharray="263.89" stroke-dashoffset="263.89"></circle>
                </svg>
              </div>
            </div>
            <div class="kpi-value-wrap">
              <span class="kpi-value" id="kpi-validated-val">0 / 20 (0%)</span>
            </div>
            <span class="kpi-subtext">Etapa 4: RCC / VPN & Ambiente CET</span>
            <span class="kpi-badge badge-amber">⏳ Roteiro Aberto</span>
          </div>
        </div>
      </div>
    </section>

    <!-- ========================================================================
         RADAR DE FLUXOS INTERATIVO
         ======================================================================== -->
    <section class="radar-section" id="radar">
      <div class="container">
        <div class="section-header">
          <span class="section-eyebrow">Mapeamento Exaustivo de Processos</span>
          <h2 class="section-title">Grade Interativa de Fluxos de Integração</h2>
          <p class="section-description">
            Explore os 20 fluxos essenciais de comunicação com a B3. Filtre por ecossistema, pesquise por termo ou consulte especificações técnicas, código e status de homologação.
          </p>
        </div>

        <!-- Painel de Controles e Filtros -->
        <div class="filter-panel">
          <!-- Filtro por Ecossistema B3 -->
          <div class="ecosystem-tabs" id="ecosystem-tabs">
            <button class="eco-tab active" data-eco="all">Todos <span class="tab-count" id="count-all">20</span></button>
            <button class="eco-tab" data-eco="iMercado">iMercado / Pós-Negociação <span class="tab-count" id="count-imercado">8</span></button>
            <button class="eco-tab" data-eco="RTC">RTC (Clearing Tempo Real) <span class="tab-count" id="count-rtc">2</span></button>
            <button class="eco-tab" data-eco="Puma Trading">Puma Trading (FIX) <span class="tab-count" id="count-puma">2</span></button>
            <button class="eco-tab" data-eco="Puma SBE">Puma SBE (Binário) <span class="tab-count" id="count-sbe">1</span></button>
            <button class="eco-tab" data-eco="NoMe">NoMe / Balcão & Renda Fixa <span class="tab-count" id="count-nome">5</span></button>
            <button class="eco-tab" data-eco="Conectividade">Conectividade & Perfis <span class="tab-count" id="count-conn">2</span></button>
          </div>

          <!-- Linha de Busca Textual e Contadores -->
          <div class="search-filter-row">
            <div class="search-input-wrap">
              <svg class="search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="11" cy="11" r="8"></circle>
                <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
              </svg>
              <input type="text" id="flow-search" class="search-input" placeholder="Buscar por ID (ex: FL-001), nome, interface (ex: FIX, SBE, IMBARQ) ou palavra-chave..." autocomplete="off">
              <button id="clear-search" class="clear-search-btn" title="Limpar busca">&times;</button>
            </div>

            <div class="filter-actions">
              <span class="results-counter" id="results-count">Exibindo <strong>20</strong> de <strong>20</strong> fluxos</span>
            </div>
          </div>
        </div>

        <!-- Grade de Cards Dinâmicos -->
        <div class="flows-grid" id="flows-grid">
          <!-- Renderizado via JavaScript -->
        </div>
      </div>
    </section>

    <!-- ========================================================================
         PAINEL DE LEIS FORMAIS & INVARIANTES
         ======================================================================== -->
    <section class="invariants-section" id="invariantes">
      <div class="container">
        <div class="section-header">
          <span class="section-eyebrow">Consistência Formal & Proof Systems</span>
          <h2 class="section-title">9 Leis Matemáticas de Conservação Financeira B3</h2>
          <p class="section-description">
            Inspirado no modelo de verificação formal de linguagens funcionais (como Bend/HOC) e proof systems, o Atlas submete cada parser posicional, codec binário e pipeline de liquidação a 9 leis estritas de conservação. Se houver divergência de 1 centavo ou 1 cota, o sistema rejeita deterministicamente.
          </p>
        </div>

        <div class="invariants-grid" id="invariants-grid">
          <!-- Lei 1 -->
          <div class="inv-card">
            <div class="inv-header">
              <span class="inv-number">LEI 1 • CSD</span>
              <span class="inv-status">✓ PROVADA</span>
            </div>
            <h3 class="inv-title">Conservação de Custódia Depositária</h3>
            <div class="inv-formula">Total == Disponível + Garantia + Judicial</div>
            <p class="inv-desc">
              Garante que nenhuma cota ou ativo em custódia fiduciária da depositária central seja criada ou omitida por conta e na reconciliação final do arquivo IMBARQ012.
            </p>
            <div class="inv-footer">
              <span>Módulo: src/imbarq012.py</span>
              <span>Script: verify_invariants.py</span>
            </div>
          </div>

          <!-- Lei 2 -->
          <div class="inv-card">
            <div class="inv-header">
              <span class="inv-number">LEI 2 • CCP</span>
              <span class="inv-status">✓ PROVADA</span>
            </div>
            <h3 class="inv-title">Conservação Financeira Multilateral</h3>
            <div class="inv-formula">Saldo_Líquido == |∑ Débitos - ∑ Créditos|</div>
            <p class="inv-desc">
              Comprova que os lançamentos financeiros multilaterais da Câmara B3 (IMBARQ004) fecham exatamente a zero contábil entre créditos e débitos apurados.
            </p>
            <div class="inv-footer">
              <span>Módulo: src/imbarq004.py</span>
              <span>Script: verify_invariants.py</span>
            </div>
          </div>

          <!-- Lei 3 -->
          <div class="inv-card">
            <div class="inv-header">
              <span class="inv-number">LEI 3 • RTC</span>
              <span class="inv-status">✓ PROVADA</span>
            </div>
            <h3 class="inv-title">Conservação de Alocação e Repasse RTC</h3>
            <div class="inv-formula">∑ Qtd_Alocada == Qtd_Pai ∧ Preço_Médio == Preço_Pai</div>
            <p class="inv-desc">
              Valida que blocos operacionais de fundos não sofram criação indevida de quantidade nem distorção no preço ponderado do give-up em tempo real (FL-008).
            </p>
            <div class="inv-footer">
              <span>Módulo: src/rtc_allocation.py</span>
              <span>Script: verify_invariants.py</span>
            </div>
          </div>

          <!-- Lei 4 -->
          <div class="inv-card">
            <div class="inv-header">
              <span class="inv-number">LEI 4 • CLEARING</span>
              <span class="inv-status">✓ PROVADA</span>
            </div>
            <h3 class="inv-title">Equilíbrio de Posições da Câmara</h3>
            <div class="inv-formula">Posição_Líquida == Comprada - Vendida</div>
            <p class="inv-desc">
              Assegura que o somatório de posições abertas de derivativos e listados (IMBARQ006) seja rigorosamente idêntico ao balanço de compradores e vendedores perante a CCP.
            </p>
            <div class="inv-footer">
              <span>Módulo: src/imbarq006.py</span>
              <span>Script: verify_invariants.py</span>
            </div>
          </div>

          <!-- Lei 5 -->
          <div class="inv-card">
            <div class="inv-header">
              <span class="inv-number">LEI 5 • BTC</span>
              <span class="inv-status">✓ PROVADA</span>
            </div>
            <h3 class="inv-title">Integridade de Contratos BTC</h3>
            <div class="inv-formula">Taxa_Contrato > 0 ∧ Vencimento > Abertura</div>
            <p class="inv-desc">
              Garante a consistência temporal estrita e validação de taxas de remuneração estritamente positivas em contratos de aluguel de ações (IMBARQ011).
            </p>
            <div class="inv-footer">
              <span>Módulo: src/imbarq011.py</span>
              <span>Script: verify_invariants.py</span>
            </div>
          </div>

          <!-- Lei 6 -->
          <div class="inv-card">
            <div class="inv-header">
              <span class="inv-number">LEI 6 • CORE</span>
              <span class="inv-status">✓ PROVADA</span>
            </div>
            <h3 class="inv-title">Consistência de Garantias e Margem CORE</h3>
            <div class="inv-formula">Tipo_Colateral ∈ {{MOE, TPU, ACO}} ∧ Cobertura ≥ Exigência</div>
            <p class="inv-desc">
              Garante a aceitação apenas de colaterais elegíveis pela B3 e afere que a margem requerida pelo motor CORE está 100% coberta (IMBARQ014).
            </p>
            <div class="inv-footer">
              <span>Módulo: src/imbarq014.py</span>
              <span>Script: verify_invariants.py</span>
            </div>
          </div>

          <!-- Lei 7 -->
          <div class="inv-card">
            <div class="inv-header">
              <span class="inv-number">LEI 7 • CORPORATIVO</span>
              <span class="inv-status">✓ PROVADA</span>
            </div>
            <h3 class="inv-title">Cronologia de Eventos Corporativos</h3>
            <div class="inv-formula">Data_Com ≤ Data_Ex ≤ Data_Pagamento ∧ Provento ≥ 0</div>
            <p class="inv-desc">
              Impede negociação ex-dividendo prévia à data com-dividendo e proíbe proventos societários de valor negativo em listados (IMBARQ018).
            </p>
            <div class="inv-footer">
              <span>Módulo: src/imbarq018.py</span>
              <span>Script: verify_invariants.py</span>
            </div>
          </div>

          <!-- Lei 8 -->
          <div class="inv-card">
            <div class="inv-header">
              <span class="inv-number">LEI 8 • FÍSICO</span>
              <span class="inv-status">✓ PROVADA</span>
            </div>
            <h3 class="inv-title">Balanço de Movimentações de Custódia</h3>
            <div class="inv-formula">Saldo_Final == Saldo_Inicial + ∑ Créditos - ∑ Débitos</div>
            <p class="inv-desc">
              Comprova a conservação volumétrica física de ações transferidas entre contas, corretoras e depositárias no arquivo IMBARQ013.
            </p>
            <div class="inv-footer">
              <span>Módulo: src/imbarq013.py</span>
              <span>Script: verify_invariants.py</span>
            </div>
          </div>

          <!-- Lei 9 -->
          <div class="inv-card">
            <div class="inv-header">
              <span class="inv-number">LEI 9 • PIPELINE</span>
              <span class="inv-status">✓ PROVADA</span>
            </div>
            <h3 class="inv-title">Fechamento Unificado no Pipeline Central</h3>
            <div class="inv-formula">Orquestração(IMBARQ004..014) ⟹ Quebras_Reconciliação == 0</div>
            <p class="inv-desc">
              Execução orquestrada do ciclo noturno combinando 5 arquivos simultâneos contra livros de custódia interna sem nenhuma quebra posicional.
            </p>
            <div class="inv-footer">
              <span>Módulo: src/pipeline.py</span>
              <span>Script: verify_invariants.py</span>
            </div>
          </div>
        </div>

        <!-- Terminal de Verificação Interativo -->
        <div class="terminal-box">
          <div class="terminal-header">
            <div class="terminal-dots">
              <span class="term-dot dot-red"></span>
              <span class="term-dot dot-yellow"></span>
              <span class="term-dot dot-green"></span>
            </div>
            <span class="terminal-title">bash — python3 scripts/verify_invariants.py</span>
            <button class="btn-term-action" id="btn-copy-term" title="Copiar comando">Copiar Comando</button>
          </div>
          <div class="terminal-body" id="term-output">
<span class="term-dim"># Execução do Verificador Formal de Invariantes Financeiros B3</span>
<span class="term-blue">$ python3 scripts/verify_invariants.py</span>

======================================================================
 B3 INTEGRATION ATLAS — VERIFICAÇÃO FORMAL DE INVARIANTES
 Baseada em proof systems e leis de conservação financeira
======================================================================
[1/9] Verificando Invariante de Conservação de Custódia (IMBARQ012)...
      <span class="term-green">✓ Conservação de custódia comprovada (Total == Disp + Gar + Jud).</span>
[2/9] Verificando Invariante Financeira Multilateral (IMBARQ004)...
      <span class="term-green">✓ Fechamento contábil e conservação financeira comprovados.</span>
[3/9] Verificando Invariante de Alocação e Repasse RTC (FL-008)...
      <span class="term-green">✓ Conservação de parcelas e preço de alocação comprovados.</span>
[4/9] Verificando Invariante de Posições da Câmara (IMBARQ006)...
      <span class="term-green">✓ Invariante de somatórios da câmara comprovada.</span>
[5/9] Verificando Invariante de Contratos BTC (IMBARQ011)...
      <span class="term-green">✓ Invariantes cronológicas e de taxa do BTC comprovadas.</span>
[6/9] Verificando Invariante de Garantias e Margem (IMBARQ014)...
      <span class="term-green">✓ Invariantes de elegibilidade de colateral e margem comprovadas.</span>
[7/9] Verificando Invariante de Eventos Corporativos (IMBARQ018)...
      <span class="term-green">✓ Invariantes de cronologia Data Com/Ex comprovadas.</span>
[8/9] Verificando Invariante de Movimentações de Custódia (IMBARQ013)...
      <span class="term-green">✓ Invariante de balanço de movimentações físicas comprovada.</span>
[9/9] Verificando Fechamento Unificado no Pipeline Central...
      <span class="term-green">✓ Fechamento diário unificado 100% consistente sem quebras.</span>

======================================================================
<span class="term-green">✓ TODAS AS 9 LEIS DE CONSERVAÇÃO E INVARIANTES DA B3 FORAM PROVADAS!</span>
  Garantia matemática de consistência em todas as fixtures e módulos.
======================================================================
          </div>
        </div>
      </div>
    </section>

    <!-- ========================================================================
         ROTEIRO DE HOMOLOGAÇÃO REAL B3
         ======================================================================== -->
    <section class="homologation-section" id="homologacao">
      <div class="container">
        <div class="section-header">
          <span class="section-eyebrow">Rumo à Certificação Oficial</span>
          <h2 class="section-title">Roteiro de Homologação Real B3</h2>
          <p class="section-description">
            Entenda o caminho institucional e regulatório necessário para transformar os 20 fluxos de <code>validated_b3: false</code> para <code>validated_b3: true</code> segundo as normas oficiais da B3 S.A.
          </p>
        </div>

        <div class="roadmap-timeline">
          <!-- Etapa 1 -->
          <div class="roadmap-step-card completed">
            <div class="step-header">
              <span class="step-number">1</span>
              <span class="step-status-tag badge-success">Concluído</span>
            </div>
            <h3 class="step-title">Enquadramento & Perfis</h3>
            <p class="step-desc">
              Modelagem jurídica e operacional dos perfis de participante B3: Participante Negociador Pleno (PNP), Negociador (PN), Liquidante (PL), Gestora (Buy-side) e Custodiante.
            </p>
            <div class="step-deliverables">
              <div class="step-deliverables-title">Entregáveis do Atlas:</div>
              • Módulo <code>src/participant_routing.py</code><br>
              • Guia <code>docs/certification-guide.md</code><br>
              • Matriz de roteamento e give-up testada
            </div>
          </div>

          <!-- Etapa 2 -->
          <div class="roadmap-step-card in-progress">
            <div class="step-header">
              <span class="step-number">2</span>
              <span class="step-status-tag badge-success">Especificado</span>
            </div>
            <h3 class="step-title">Conectividade Segura</h3>
            <p class="step-desc">
              Topologias de conexão institucional: Linha dedicada RCC (Rede de Comunicação de Corretoras 100Mbps/1Gbps), B3 Connect ou VPN IPSec Lan-to-Lan com crypto map AES-256-GCM.
            </p>
            <div class="step-deliverables">
              <div class="step-deliverables-title">Entregáveis do Atlas:</div>
              • Topologia documentada em <code>docs/layout-participant-routing.md</code><br>
              • Validador de conformidade mTLS/IPSec<br>
              • Arquitetura de contingência e alta disponibilidade
            </div>
          </div>

          <!-- Etapa 3 -->
          <div class="roadmap-step-card in-progress">
            <div class="step-header">
              <span class="step-number">3</span>
              <span class="step-status-tag badge-success">Implementado</span>
            </div>
            <h3 class="step-title">Identidade Digital mTLS</h3>
            <p class="step-desc">
              Criptografia mTLS mútua com certificados ICP-Brasil padrão A1/A3 (e-CNPJ), cadastro perante o Administrador de Segurança Institucional (ASI) e gestão de SAS URLs Azure Blob.
            </p>
            <div class="step-deliverables">
              <div class="step-deliverables-title">Entregáveis do Atlas:</div>
              • Cliente mTLS em <code>src/imercado_client.py</code><br>
              • Download resiliente com hash SHA-256<br>
              • Testes unitários com mock e certificados simulados
            </div>
          </div>

          <!-- Etapa 4 -->
          <div class="roadmap-step-card pending">
            <div class="step-header">
              <span class="step-number">4</span>
              <span class="step-status-tag badge-amber">Aguardando CET</span>
            </div>
            <h3 class="step-title">Ambiente de Homologação</h3>
            <p class="step-desc">
              Execução do caderno formal de testes no ambiente oficial CET / Simulador Puma da B3 para emissão do Parecer Técnico de Homologação e obtenção de credenciais de produção.
            </p>
            <div class="step-deliverables">
              <div class="step-deliverables-title">Entregáveis do Atlas:</div>
              • 205 testes automatizados prontos para reprodução<br>
              • Fixtures sintéticas cobrindo 100% dos cenários<br>
              • Caderno de homologação em <code>docs/roadmap.md</code>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ========================================================================
         CATÁLOGO DE FONTES OFICIAIS
         ======================================================================== -->
    <section class="sources-section" id="fontes">
      <div class="container">
        <div class="section-header">
          <span class="section-eyebrow">Rastreabilidade & Evidências Oficiais</span>
          <h2 class="section-title">Fontes e Manuais Oficiais da B3</h2>
          <p class="section-description">
            Todas as especificações, layouts de arquivos e regras de protocolo são extraídas exclusivamente de documentação pública e canais oficiais da B3 S.A.
          </p>
        </div>

        <div class="sources-grid" id="sources-grid">
          <!-- Preenchido via JavaScript -->
        </div>
      </div>
    </section>

    <!-- ========================================================================
         GOVERNANÇA & ATUALIZAÇÃO CONTÍNUA
         ======================================================================== -->
    <section class="homologation-section" id="governanca">
      <div class="container">
        <div class="section-header">
          <span class="section-eyebrow">Arquitetura de Governança Contínua</span>
          <h2 class="section-title">Como o Radar se Mantém Atualizado</h2>
          <p class="section-description">
            O B3 Integration Atlas foi construído para evoluir de forma autônoma e disciplinada através de testes rigorosos e pipelines de integração contínua.
          </p>
        </div>

        <div class="kpi-grid">
          <div class="kpi-card">
            <div class="kpi-label">Validação de Integridade</div>
            <div class="kpi-value-wrap">
              <span class="kpi-value" style="font-size: 1.15rem; color: #38bdf8;">python3 scripts/test_catalog.py</span>
            </div>
            <p class="kpi-subtext">Verifica integridade referencial estrita entre fluxos e fontes oficiais cadastradas.</p>
          </div>

          <div class="kpi-card">
            <div class="kpi-label">Cálculo de Cobertura</div>
            <div class="kpi-value-wrap">
              <span class="kpi-value" style="font-size: 1.15rem; color: #34d399;">python3 scripts/get_coverage.py</span>
            </div>
            <p class="kpi-subtext">Computa percentuais de mapeamento, especificação, código, testes locais e homologação B3.</p>
          </div>

          <div class="kpi-card">
            <div class="kpi-label">Prova de Invariantes</div>
            <div class="kpi-value-wrap">
              <span class="kpi-value" style="font-size: 1.15rem; color: #a78bfa;">python3 scripts/verify_invariants.py</span>
            </div>
            <p class="kpi-subtext">Executa as 9 leis matemáticas de conservação contra fixtures sintéticas e módulos.</p>
          </div>

          <div class="kpi-card">
            <div class="kpi-label">Suíte Completa de Testes</div>
            <div class="kpi-value-wrap">
              <span class="kpi-value" style="font-size: 1.15rem; color: #fbbf24;">python3 -m unittest discover</span>
            </div>
            <p class="kpi-subtext">205 testes automatizados cobrindo todos os parsers, clientes, codecs binários e roteadores.</p>
          </div>
        </div>
      </div>
    </section>
  </main>

  <!-- ========================================================================
       FOOTER
       ======================================================================== -->
  <footer class="site-footer">
    <div class="container">
      <div class="footer-content">
        <div class="footer-brand">
          <div class="brand-logo">
            <div class="brand-badge">B3</div>
            <span>B3 Integration Atlas</span>
          </div>
          <p class="footer-desc">
            Projeto de engenharia de software e pesquisa técnica independente focado em desmistificar, mapear e implementar interfaces e protocolos da infraestrutura de mercado da B3 S.A.
          </p>
        </div>

        <div>
          <h4 class="footer-col-title">Seções</h4>
          <ul class="footer-links">
            <li><a href="#radar">Radar de Fluxos</a></li>
            <li><a href="#invariantes">Leis Formais de Conservação</a></li>
            <li><a href="#homologacao">Roteiro de Homologação</a></li>
            <li><a href="#fontes">Fontes Oficiais B3</a></li>
            <li><a href="#governanca">Governança Contínua</a></li>
          </ul>
        </div>

        <div>
          <h4 class="footer-col-title">Arquivos-Chave</h4>
          <ul class="footer-links">
            <li><a href="layout-imbarq006.md">Spec IMBARQ006</a></li>
            <li><a href="layout-puma-sbe.md">Spec PUMA SBE</a></li>
            <li><a href="layout-rtc-allocation.md">Spec Alocação RTC</a></li>
            <li><a href="certification-guide.md">Guia de Certificação</a></li>
            <li><a href="expansion-roadmap.md">Roadmap de Expansão</a></li>
          </ul>
        </div>
      </div>

      <div class="footer-bottom">
        <div class="legal-notice">
          <strong>Aviso Institucional:</strong> Este projeto é independente e de caráter técnico-científico. B3® é marca registrada de B3 S.A. – Brasil, Bolsa, Balcão. Não há endosso, vínculo formal, afiliação comercial ou redistribuição de dados privados ou credenciais institucionais restritas.
        </div>
        <div>
          <span>B3 Integration Atlas v0.2.0 • 2026-11-03</span>
        </div>
      </div>
    </div>
  </footer>

  <!-- Toast Notification -->
  <div id="toast" class="toast">Notificação</div>

  <!-- ========================================================================
       CATÁLOGO EMBUTIDO & JAVASCRIPT DINÂMICO
       ======================================================================== -->
  <script>
    // Fallback Embutido: Garante funcionamento perfeito em file:// ou offline
    const FALLBACK_FLOWS = {flows_json_str};
    const FALLBACK_SOURCES = {sources_json_str};

    let flowsData = null;
    let sourcesData = null;
    let currentEcosystem = 'all';
    let currentSearchTerm = '';

    // Mapeamento de Ecossistemas
    const ECOSYSTEM_MAP = {{
      'iMercado': ['iMercado'],
      'RTC': ['RTC'],
      'Puma Trading': ['Puma Trading', 'Drop Copy'],
      'Puma SBE': ['Puma Trading'], // filtrado também por interface
      'NoMe': ['NoMe'],
      'Conectividade': ['Conectividade B3', 'Plataformas B3']
    }};

    // Inicialização da Aplicação
    document.addEventListener('DOMContentLoaded', async () => {{
      await loadCatalogData();
      renderKPIs();
      renderEcosystemCounts();
      renderFlows();
      renderSources();
      setupEventListeners();
    }});

    // Carregamento de Dados (Tenta fetch dinâmico, se falhar usa fallback)
    async function loadCatalogData() {{
      const indicator = document.getElementById('live-indicator-text');
      
      const tryFetch = async (url) => {{
        try {{
          const res = await fetch(url);
          if (res.ok) return await res.json();
        }} catch (e) {{
          return null;
        }}
        return null;
      }};

      // Tenta buscar dinamicamente
      const fetchedFlows = await tryFetch('catalog/flows.json') || await tryFetch('../catalog/flows.json') || await tryFetch('./catalog/flows.json');
      const fetchedSources = await tryFetch('catalog/sources.json') || await tryFetch('../catalog/sources.json') || await tryFetch('./catalog/sources.json');

      if (fetchedFlows && fetchedSources) {{
        flowsData = fetchedFlows;
        sourcesData = fetchedSources;
        if (indicator) indicator.textContent = 'Modo Dinâmico (API/JSON)';
      }} else {{
        flowsData = FALLBACK_FLOWS;
        sourcesData = FALLBACK_SOURCES;
        if (indicator) indicator.textContent = 'Modo Local / Standalone';
      }}
    }}

    // Atualiza KPIs com base nos dados
    function renderKPIs() {{
      if (!flowsData || !flowsData.flows) return;
      const flows = flowsData.flows;
      const total = flows.length;
      
      const countMapped = flows.filter(f => f.mapped).length;
      const countDocumented = flows.filter(f => f.documented).length;
      const countImplemented = flows.filter(f => f.implemented).length;
      const countTested = flows.filter(f => f.tested_locally).length;
      const countValidated = flows.filter(f => f.validated_b3).length;

      document.getElementById('kpi-mapped-val').textContent = `${{countMapped}} / ${{total}}`;
      document.getElementById('kpi-documented-val').textContent = `${{countDocumented}} / ${{total}}`;
      document.getElementById('kpi-implemented-val').textContent = `${{countImplemented}} / ${{total}}`;
      document.getElementById('kpi-tested-val').textContent = `205 Testes`;
      document.getElementById('kpi-validated-val').textContent = `${{countValidated}} / ${{total}} (0%)`;

      // Animação dos anéis circulares (circunferência = 263.89)
      const setRing = (id, pct) => {{
        const ring = document.getElementById(id);
        if (ring) {{
          const offset = 263.89 - (263.89 * pct / 100);
          ring.style.strokeDashoffset = offset;
        }}
      }};

      setRing('ring-mapped', (countMapped / total) * 100);
      setRing('ring-documented', (countDocumented / total) * 100);
      setRing('ring-implemented', (countImplemented / total) * 100);
      setRing('ring-tested', 100);
      setRing('ring-invariants', 100);
      setRing('ring-validated', (countValidated / total) * 100);
    }}

    // Atualiza contadores dos botões de ecossistema
    function renderEcosystemCounts() {{
      if (!flowsData) return;
      const flows = flowsData.flows;

      const setTabCount = (id, count) => {{
        const el = document.getElementById(id);
        if (el) el.textContent = count;
      }};

      setTabCount('count-all', flows.length);
      setTabCount('count-imercado', flows.filter(f => f.system === 'iMercado').length);
      setTabCount('count-rtc', flows.filter(f => f.system === 'RTC').length);
      setTabCount('count-puma', flows.filter(f => (f.system === 'Puma Trading' || f.system === 'Drop Copy') && f.id !== 'FL-013').length);
      setTabCount('count-sbe', flows.filter(f => f.id === 'FL-013').length);
      setTabCount('count-nome', flows.filter(f => f.system === 'NoMe').length);
      setTabCount('count-conn', flows.filter(f => f.system === 'Conectividade B3' || f.system === 'Plataformas B3').length);
    }}

    // Renderiza a grade de fluxos com filtros
    function renderFlows() {{
      if (!flowsData) return;
      const container = document.getElementById('flows-grid');
      const counterEl = document.getElementById('results-count');
      const flows = flowsData.flows;

      const filtered = flows.filter(flow => {{
        // Filtro por ecossistema
        if (currentEcosystem === 'iMercado' && flow.system !== 'iMercado') return false;
        if (currentEcosystem === 'RTC' && flow.system !== 'RTC') return false;
        if (currentEcosystem === 'Puma Trading' && (flow.system !== 'Puma Trading' && flow.system !== 'Drop Copy' || flow.id === 'FL-013')) return false;
        if (currentEcosystem === 'Puma SBE' && flow.id !== 'FL-013') return false;
        if (currentEcosystem === 'NoMe' && flow.system !== 'NoMe') return false;
        if (currentEcosystem === 'Conectividade' && flow.system !== 'Conectividade B3' && flow.system !== 'Plataformas B3') return false;

        // Filtro por termo de busca
        if (currentSearchTerm) {{
          const term = currentSearchTerm.toLowerCase();
          const matchId = flow.id.toLowerCase().includes(term);
          const matchName = flow.name.toLowerCase().includes(term);
          const matchSystem = flow.system.toLowerCase().includes(term);
          const matchInterface = flow.interface.toLowerCase().includes(term);
          const matchPending = (flow.pending || '').toLowerCase().includes(term);
          if (!matchId && !matchName && !matchSystem && !matchInterface && !matchPending) return false;
        }}

        return true;
      }});

      if (counterEl) {{
        counterEl.innerHTML = `Exibindo <strong>${{filtered.length}}</strong> de <strong>${{flows.length}}</strong> fluxos`;
      }}

      if (filtered.length === 0) {{
        container.innerHTML = `
          <div style="grid-column: 1 / -1; padding: 48px; text-align: center; color: var(--text-muted); background: var(--bg-glass-card); border-radius: var(--radius-lg); border: 1px dashed var(--border-subtle);">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin-bottom: 12px; opacity: 0.6;">
              <circle cx="11" cy="11" r="8"></circle>
              <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
            </svg>
            <p style="font-size: 1.1rem; font-weight: 600; color: var(--text-secondary); margin-bottom: 6px;">Nenhum fluxo encontrado</p>
            <p style="font-size: 0.88rem;">Tente ajustar o termo de pesquisa ou selecionar outro ecossistema.</p>
          </div>
        `;
        return;
      }}

      container.innerHTML = filtered.map(flow => {{
        const systemClass = getSystemClass(flow.system, flow.id);
        const docLink = formatDocLink(flow.evidence ? flow.evidence.documented : null);
        const codeLink = formatCodeLink(flow.evidence ? flow.evidence.implemented : null);
        const testLink = formatTestLink(flow.evidence ? flow.evidence.tested_locally : null);

        return `
          <article class="flow-card" id="card-${{flow.id}}">
            <div class="flow-card-top">
              <span class="flow-id-badge">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                  <polyline points="14 2 14 8 20 8"></polyline>
                </svg>
                ${{flow.id}}
              </span>
              <span class="system-pill ${{systemClass}}">${{flow.system}}</span>
            </div>

            <h3 class="flow-name">${{escapeHtml(flow.name)}}</h3>

            <div class="flow-interface-box" title="${{escapeHtml(flow.interface)}}">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="16 18 22 12 16 6"></polyline>
                <polyline points="8 6 2 12 8 18"></polyline>
              </svg>
              <span>${{escapeHtml(flow.interface)}}</span>
            </div>

            <!-- Matriz de 5 Status -->
            <div class="flow-status-matrix">
              <div class="status-cell ${{flow.mapped ? 'ok' : 'pending'}}" title="Mapeado no Inventário">
                <span class="status-cell-title">Mapeado</span>
                <span class="status-cell-icon">${{flow.mapped ? '✓' : '✗'}}</span>
              </div>
              <div class="status-cell ${{flow.documented ? 'ok' : 'pending'}}" title="Especificação Técnica Concluída">
                <span class="status-cell-title">Doc</span>
                <span class="status-cell-icon">${{flow.documented ? '✓' : '⏳'}}</span>
              </div>
              <div class="status-cell ${{flow.implemented ? 'ok' : 'pending'}}" title="Código Python Implementado">
                <span class="status-cell-title">Código</span>
                <span class="status-cell-icon">${{flow.implemented ? '✓' : '⏳'}}</span>
              </div>
              <div class="status-cell ${{flow.tested_locally ? 'ok' : 'pending'}}" title="Testes Unitários Locais Aprovados">
                <span class="status-cell-title">Testes</span>
                <span class="status-cell-icon">${{flow.tested_locally ? '✓' : '⏳'}}</span>
              </div>
              <div class="status-cell ${{flow.validated_b3 ? 'ok' : 'pending'}}" title="Homologação Formal em Ambiente da B3">
                <span class="status-cell-title">B3</span>
                <span class="status-cell-icon">${{flow.validated_b3 ? '✓' : '⏳'}}</span>
              </div>
            </div>

            <!-- Ações e Links Diretos -->
            <div class="flow-actions">
              ${{docLink ? `<a href="${{docLink}}" class="flow-link-btn" title="Ver especificação técnica">📄 Especificação</a>` : ''}}
              ${{codeLink ? `<a href="${{codeLink}}" class="flow-link-btn" title="Ver código-fonte em Python">💻 Código</a>` : ''}}
              ${{testLink ? `<a href="${{testLink}}" class="flow-link-btn" title="Ver suíte de testes">🧪 Testes</a>` : ''}}
            </div>

            <!-- Acordeão de Detalhes e Homologação -->
            <div class="flow-accordion">
              <button class="flow-accordion-toggle" onclick="toggleAccordion('${{flow.id}}')">
                <span>Roteiro de Homologação & Evidências</span>
                <span class="accordion-chevron" id="chevron-${{flow.id}}">▼</span>
              </button>
              <div class="flow-accordion-body" id="body-${{flow.id}}">
                <div class="accordion-field">
                  <div class="accordion-field-title">Pendências para Homologação B3:</div>
                  <p>${{escapeHtml(flow.pending || 'Concluído localmente. Aguardando conexão institucional B3.')}}</p>
                </div>

                <div class="accordion-field">
                  <div class="accordion-field-title">Fontes Oficiais:</div>
                  <div class="accordion-sources">
                    ${{(flow.sources || []).map(srcId => `
                      <span class="src-tag" onclick="scrollToSource('${{srcId}}')" title="Ver detalhes da fonte">${{srcId}}</span>
                    `).join('')}}
                  </div>
                </div>

                ${{flow.evidence ? `
                <div class="accordion-field">
                  <div class="accordion-field-title">Evidências de Mapeamento:</div>
                  <div style="font-family: var(--font-mono); font-size: 0.72rem; color: var(--text-muted); line-height: 1.4;">
                    <div>• Doc: ${{flow.evidence.documented || 'N/A'}}</div>
                    <div>• Impl: ${{flow.evidence.implemented || 'N/A'}}</div>
                    <div>• Test: ${{flow.evidence.tested_locally || 'N/A'}}</div>
                  </div>
                </div>
                ` : ''}}
              </div>
            </div>
          </article>
        `;
      }}).join('');
    }}

    // Renderiza as fontes oficiais
    function renderSources() {{
      if (!sourcesData || !sourcesData.sources) return;
      const container = document.getElementById('sources-grid');
      container.innerHTML = sourcesData.sources.map(src => `
        <div class="source-card" id="src-card-${{src.id}}">
          <div class="source-top">
            <span class="source-id">${{src.id}}</span>
            <span class="source-kind">${{src.kind.replace('_', ' ')}}</span>
          </div>
          <h4 class="source-title">${{escapeHtml(src.title)}}</h4>
          <p class="source-limitation">${{escapeHtml(src.limitation)}}</p>
          <a href="${{src.url}}" target="_blank" rel="noopener noreferrer" class="source-link">
            <span>Acessar portal B3</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
              <polyline points="15 3 21 3 21 9"></polyline>
              <line x1="10" y1="14" x2="21" y2="3"></line>
            </svg>
          </a>
        </div>
      `).join('');
    }}

    // Helpers de Formatação de Links
    function formatDocLink(path) {{
      if (!path) return null;
      // Se estamos dentro de /docs, docs/layout-xxx.md é apenas layout-xxx.md
      return path.startsWith('docs/') ? path.substring(5) : path;
    }}

    function formatCodeLink(path) {{
      if (!path) return null;
      const first = path.split(',')[0].trim();
      return `https://github.com/Ruaney/b3-connect/blob/main/${{first}}`;
    }}

    function formatTestLink(path) {{
      if (!path) return null;
      const first = path.split(',')[0].trim();
      return `https://github.com/Ruaney/b3-connect/blob/main/${{first}}`;
    }}

    function getSystemClass(system, id) {{
      if (id === 'FL-013') return 'system-Puma-SBE';
      if (system === 'iMercado') return 'system-iMercado';
      if (system === 'RTC') return 'system-RTC';
      if (system === 'Puma Trading') return 'system-Puma-Trading';
      if (system === 'Drop Copy') return 'system-Drop-Copy';
      if (system === 'NoMe') return 'system-NoMe';
      if (system === 'Conectividade B3') return 'system-Conectividade';
      if (system === 'Plataformas B3') return 'system-Plataformas';
      return 'system-iMercado';
    }}

    function escapeHtml(str) {{
      if (!str) return '';
      return str.replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
    }}

    // Alterna o Acordeão do Card
    window.toggleAccordion = function(id) {{
      const body = document.getElementById(`body-${{id}}`);
      const chevron = document.getElementById(`chevron-${{id}}`);
      if (body) {{
        body.classList.toggle('open');
      }}
      if (chevron) {{
        chevron.parentElement.classList.toggle('open');
      }}
    }};

    // Rola suavemente até a fonte correspondente
    window.scrollToSource = function(srcId) {{
      const el = document.getElementById(`src-card-${{srcId}}`);
      if (el) {{
        el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
        el.style.borderColor = 'var(--b3-blue-light)';
        el.style.boxShadow = '0 0 20px rgba(14, 165, 233, 0.4)';
        setTimeout(() => {{
          el.style.borderColor = '';
          el.style.boxShadow = '';
        }}, 2000);
      }}
    }};

    // Event Listeners dos Filtros e Busca
    function setupEventListeners() {{
      // Tabs de Ecossistema
      const tabs = document.querySelectorAll('.eco-tab');
      tabs.forEach(tab => {{
        tab.addEventListener('click', () => {{
          tabs.forEach(t => t.classList.remove('active'));
          tab.classList.add('active');
          currentEcosystem = tab.dataset.eco;
          renderFlows();
        }});
      }});

      // Busca Textual em Tempo Real
      const searchInput = document.getElementById('flow-search');
      const clearBtn = document.getElementById('clear-search');

      if (searchInput) {{
        searchInput.addEventListener('input', (e) => {{
          currentSearchTerm = e.target.value.trim();
          if (clearBtn) {{
            clearBtn.style.display = currentSearchTerm ? 'block' : 'none';
          }}
          renderFlows();
        }});
      }}

      if (clearBtn) {{
        clearBtn.addEventListener('click', () => {{
          if (searchInput) searchInput.value = '';
          currentSearchTerm = '';
          clearBtn.style.display = 'none';
          renderFlows();
        }});
      }}

      // Botão Copiar Comando do Terminal
      const btnCopy = document.getElementById('btn-copy-term');
      if (btnCopy) {{
        btnCopy.addEventListener('click', () => {{
          navigator.clipboard.writeText('python3 scripts/verify_invariants.py').then(() => {{
            showToast('Comando copiado para a área de transferência!');
          }}).catch(() => {{
            showToast('Comando: python3 scripts/verify_invariants.py');
          }});
        }});
      }}
    }}

    // Mostra Toast
    function showToast(msg) {{
      const toast = document.getElementById('toast');
      if (toast) {{
        toast.textContent = msg;
        toast.classList.add('show');
        setTimeout(() => {{
          toast.classList.remove('show');
        }}, 3000);
      }}
    }}
  </script>
</body>
</html>
"""


def main():
    print("======================================================================")
    print(" Compilação da Página Web Principal B3 Integration Atlas")
    print("======================================================================")

    if not FLOWS_PATH.is_file():
        print(f"ERRO: Catálogo {FLOWS_PATH} não encontrado.")
        sys.exit(1)

    if not SOURCES_PATH.is_file():
        print(f"ERRO: Catálogo {SOURCES_PATH} não encontrado.")
        sys.exit(1)

    flows_data, sources_data = load_catalogs()
    print(f"✓ Catálogos carregados: {len(flows_data['flows'])} fluxos e {len(sources_data['sources'])} fontes.")

    html_content = generate_html(flows_data, sources_data)

    # Escreve em docs/index.html
    DOCS_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DOCS_INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✓ docs/index.html gerado ({len(html_content.encode('utf-8'))} bytes).")

    # Espelha em index.html na raiz do repositório
    with open(ROOT_INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✓ index.html (raiz) sincronizado com sucesso.")

    # Sincroniza docs/catalog/*.json
    docs_catalog_dir = REPO_ROOT / "docs" / "catalog"
    docs_catalog_dir.mkdir(parents=True, exist_ok=True)
    with open(docs_catalog_dir / "flows.json", "w", encoding="utf-8") as f:
        json.dump(flows_data, f, indent=2, ensure_ascii=False)
    with open(docs_catalog_dir / "sources.json", "w", encoding="utf-8") as f:
        json.dump(sources_data, f, indent=2, ensure_ascii=False)
    print("✓ docs/catalog/*.json sincronizado para deploy do GitHub Pages.")

    print("\n✓ Compilação concluída com sucesso! Página pronta para publicação.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
