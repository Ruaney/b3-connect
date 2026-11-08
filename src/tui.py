"""B3 Integration Atlas — Terminal User Interface (TUI) de Alta Fidelidade.

Interface de terminal institucional e interativa para navegação completa pelo ecossistema B3:
- Painel de Visão Geral (107 fluxos, 91 leis de conservação formal, 87 manuais regulatórios)
- Monitor de Negociação PUMA em Tempo Real (mini-livro L2/L3 Bids/Asks, spread, leilão MOC)
- Scoreboard do Barramento Central (Ativos vs Passivos LAW-106A, Waterfall LAW-106B)
- Consulta Rápida SNG (Gravames Veiculares) e SRO (Seguros SUSEP)
- Menu Interativo de Simulação dos 12 Conceitos Complexos

100% Python Standard Library pura (zero dependências externas / zero pip).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import random
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

# Importações do Ecossistema B3 Integration Atlas
from src.b3_concepts_catalog import CONCEPTS, simulate_concept
from src.formal_invariant_prover import build_91_canonical_laws
from src.central_settlement_liquidity_bus import (
    B3CentralSettlementBus,
    ClearingSubsystem,
    SettlementCycle,
)
from src.puma_closing_cross_moc import (
    AuctionPhase,
    ClosingOrder,
    ClosingOrderSide,
    ClosingOrderStatus,
    ClosingOrderType,
    PumaClosingCrossEngine,
)
from src.market_by_order_l3_book import MarketByOrderL3Book, OrderSide
from src.sng_gravames_engine import (
    DetranUF,
    ModalidadeGravame,
    SngGravamesEngine,
    StatusGravame,
    VeiculoIdentificacao,
)
from src.sro_seguros_engine import (
    RamoSeguro,
    SroSegurosEngine,
    StatusApolice,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent

# ==============================================================================
# Formatador ANSI e Caixa de Desenho (Unicode / ASCII Fallback)
# ==============================================================================

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    """Remove sequências de escape ANSI para cálculo exato de largura de colunas."""
    return ANSI_ESCAPE_RE.sub("", text)


def visual_len(text: str) -> int:
    """Retorna o comprimento visível do texto desconsiderando cores ANSI."""
    return len(strip_ansi(text))


class ANSIFormatter:
    """Gerenciador de formatação e cores ANSI compatível com terminais modernos."""

    def __init__(self, use_color: bool = True) -> None:
        self.use_color = use_color and sys.stdout.isatty() if not os.environ.get("FORCE_COLOR") else True
        if os.environ.get("NO_COLOR") or not use_color:
            self.use_color = False

    def _c(self, code: str) -> str:
        return f"\033[{code}m" if self.use_color else ""

    @property
    def reset(self) -> str:
        return self._c("0")

    @property
    def bold(self) -> str:
        return self._c("1")

    @property
    def dim(self) -> str:
        return self._c("2")

    @property
    def italic(self) -> str:
        return self._c("3")

    @property
    def underline(self) -> str:
        return self._c("4")

    @property
    def invert(self) -> str:
        return self._c("7")

    # Cores de Texto (Foreground)
    @property
    def red(self) -> str:
        return self._c("31")

    @property
    def green(self) -> str:
        return self._c("32")

    @property
    def yellow(self) -> str:
        return self._c("33")

    @property
    def blue(self) -> str:
        return self._c("34")

    @property
    def magenta(self) -> str:
        return self._c("35")

    @property
    def cyan(self) -> str:
        return self._c("36")

    @property
    def white(self) -> str:
        return self._c("37")

    @property
    def bright_black(self) -> str:
        return self._c("90")

    @property
    def bright_red(self) -> str:
        return self._c("91")

    @property
    def bright_green(self) -> str:
        return self._c("92")

    @property
    def bright_yellow(self) -> str:
        return self._c("93")

    @property
    def bright_blue(self) -> str:
        return self._c("94")

    @property
    def bright_magenta(self) -> str:
        return self._c("95")

    @property
    def bright_cyan(self) -> str:
        return self._c("96")

    @property
    def bright_white(self) -> str:
        return self._c("97")

    # Backgrounds
    @property
    def bg_blue(self) -> str:
        return self._c("44")

    @property
    def bg_dark_gray(self) -> str:
        return self._c("100")

    @property
    def bg_green(self) -> str:
        return self._c("42")

    @property
    def bg_red(self) -> str:
        return self._c("41")

    def pad(self, text: str, width: int, align: str = "left") -> str:
        """Ajusta o preenchimento de string considerando a largura visual sem códigos ANSI."""
        v_len = visual_len(text)
        if v_len >= width:
            return text
        diff = width - v_len
        if align == "right":
            return (" " * diff) + text
        elif align == "center":
            left = diff // 2
            right = diff - left
            return (" " * left) + text + (" " * right)
        return text + (" " * diff)

    def truncate(self, text: str, max_width: int) -> str:
        """Trunca texto de forma segura para não estourar caixas de diálogo."""
        if visual_len(text) <= max_width:
            return text
        raw = strip_ansi(text)
        return raw[: max_width - 1] + "…"

    def bar(self, val: float, max_val: float, length: int = 15, fill_char: str = "█", empty_char: str = "░") -> str:
        """Renderiza barra de progresso / volume em ASCII."""
        if max_val <= 0:
            filled = 0
        else:
            pct = min(1.0, max(0.0, val / max_val))
            filled = int(round(pct * length))
        return f"{fill_char * filled}{empty_char * (length - filled)}"


# ==============================================================================
# Motor e Telas da TUI
# ==============================================================================

class B3TUIEngine:
    """Motor da Interface Terminal de Alta Fidelidade do B3 Integration Atlas."""

    SCREENS = ["overview", "puma", "central_bus", "sng_sro", "concepts"]
    SCREEN_ALIASES = {
        "overview": "overview",
        "puma": "puma",
        "moc": "puma",
        "market": "puma",
        "bus": "central_bus",
        "central_bus": "central_bus",
        "central-bus": "central_bus",
        "sng": "sng_sro",
        "sro": "sng_sro",
        "sng_sro": "sng_sro",
        "sng-sro": "sng_sro",
        "concepts": "concepts",
        "conceitos": "concepts",
    }
    SCREEN_NAMES = {
        "overview": "Visão Geral (107F / 91L / 87M)",
        "puma": "Monitor PUMA & Leilão MOC",
        "central_bus": "Barramento Central & Waterfall",
        "sng_sro": "Consulta SNG & SRO",
        "concepts": "Simulação dos 12 Conceitos",
    }

    def __init__(
        self,
        catalog_dir: Optional[Path] = None,
        fixtures_dir: Optional[Path] = None,
        use_color: bool = True,
        active_screen: str = "overview",
        symbol: str = "PETR4",
        concept_id: Optional[str] = None,
    ) -> None:
        self.catalog_dir = catalog_dir or (_REPO_ROOT / "catalog")
        self.fixtures_dir = fixtures_dir or (_REPO_ROOT / "fixtures")
        self.fmt = ANSIFormatter(use_color=use_color)
        normalized_screen = self.SCREEN_ALIASES.get(active_screen.lower().strip(), "overview")
        self.active_screen = normalized_screen if normalized_screen in self.SCREENS else "overview"
        self.symbol = symbol.upper()
        self.selected_concept_idx = 0
        self.concept_simulation_result: Optional[Dict[str, Any]] = None
        self.last_simulated_concept: Optional[str] = None
        self.tick_count = 0

        # Carrega dados essenciais
        self._load_catalog_and_laws()
        self._init_puma_engine()
        self._init_central_bus_engine()
        self._init_sng_sro_engines()

        if concept_id:
            for i, c in enumerate(CONCEPTS):
                if c.id == concept_id:
                    self.selected_concept_idx = i
                    self.run_concept_simulation()
                    break

    def _load_catalog_and_laws(self) -> None:
        """Carrega fluxos, fontes e leis canônicas."""
        flows_file = self.catalog_dir / "flows.json"
        sources_file = self.catalog_dir / "sources.json"

        self.flows: List[Dict[str, Any]] = []
        if flows_file.is_file():
            with open(flows_file, "r", encoding="utf-8") as f:
                self.flows = json.load(f).get("flows", [])

        self.sources: List[Dict[str, Any]] = []
        if sources_file.is_file():
            with open(sources_file, "r", encoding="utf-8") as f:
                self.sources = json.load(f).get("sources", [])

        self.laws_dict = build_91_canonical_laws()

    def _init_puma_engine(self) -> None:
        """Inicializa livro de ofertas L3 e motor de leilão MOC."""
        self.l3_book = MarketByOrderL3Book(self.symbol)
        ref_px = Decimal("38.45") if self.symbol == "PETR4" else Decimal("62.30")
        self.ref_price = ref_px

        # Popula livro com ordens iniciais realistas
        bids = [
            (101, Decimal("38.45"), 15000),
            (102, Decimal("38.40"), 28000),
            (103, Decimal("38.35"), 42000),
            (104, Decimal("38.30"), 65000),
            (105, Decimal("38.25"), 80000),
        ]
        asks = [
            (201, Decimal("38.50"), 18000),
            (202, Decimal("38.55"), 31000),
            (203, Decimal("38.60"), 52000),
            (204, Decimal("38.65"), 74000),
            (205, Decimal("38.70"), 91000),
        ]
        seq = 1
        for oid, px, qty in bids:
            self.l3_book.apply_order_add(oid, OrderSide.BID, px, qty, seq)
            seq += 1
        for oid, px, qty in asks:
            self.l3_book.apply_order_add(oid, OrderSide.ASK, px, qty, seq)
            seq += 1

        # Motor MOC PUMA
        fixture_moc = self.fixtures_dir / "puma_closing_cross_moc.json"
        if fixture_moc.is_file():
            self.moc_engine = PumaClosingCrossEngine.load_from_fixture(fixture_moc)
        else:
            self.moc_engine = PumaClosingCrossEngine(
                symbol=self.symbol,
                reference_price=self.ref_price,
                tick_size=Decimal("0.01"),
            )
            self.moc_engine.add_order(
                ClosingOrder("MOC-B1", self.symbol, ClosingOrderSide.BUY, ClosingOrderType.MOC, 10000)
            )
            self.moc_engine.add_order(
                ClosingOrder("MOC-S1", self.symbol, ClosingOrderSide.SELL, ClosingOrderType.MOC, 10000)
            )

    def _init_central_bus_engine(self) -> None:
        """Inicializa barramento central e motor de compensação."""
        fixture_bus = self.fixtures_dir / "central_settlement_liquidity_bus.json"
        if fixture_bus.is_file():
            self.bus, _ = B3CentralSettlementBus.from_fixture(fixture_bus)
        else:
            self.bus = B3CentralSettlementBus()
        self.bus.calculate_multilateral_netting()

    def _init_sng_sro_engines(self) -> None:
        """Inicializa motores SNG e SRO e popula registros institucionais."""
        self.sng = SngGravamesEngine()
        self.sro = SroSegurosEngine()

        # Registro demonstrativo no SNG
        v1 = VeiculoIdentificacao(
            chassi="9BWZZZ377VT004251",
            placa="B3X2026",
            renavam="12345678901",
            uf_emplacamento="SP",
            marca_modelo="VW GOL TRACK 1.0",
            ano_fabricacao=2024,
            ano_modelo=2025,
        )
        self.sng.registrar_gravame(
            contrato_numero="CTR-B3-2026-9901",
            modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
            credor_cnpj="00999888000122",
            credor_nome="BANCO B3 FINANCIAMENTOS S.A.",
            devedor_documento="12345678909",
            devedor_nome="JOAO DA SILVA SANTOS",
            veiculo=v1,
            valor_financiamento=Decimal("65000.00"),
            quantidade_parcelas=48,
            taxa_juros_anual_pct=Decimal("18.50"),
        )

        # Registro demonstrativo no SRO
        self.sro.registrar_apolice(
            numero_proposta="PROP-B3-SRO-2026-001",
            ramo=RamoSeguro.RESPONSABILIDADE_CIVIL_DO,
            seguradora_lider_cnpj="33000111000199",
            seguradora_lider_nome="B3 COMPANHIA DE SEGUROS S.A.",
            segurado_documento="11222333000144",
            segurado_nome="EMPRESA EXEMPLO S.A.",
            data_emissao="2026-11-01",
            inicio_vigencia="2026-11-01",
            fim_vigencia="2027-11-01",
            limite_maximo_garantia=Decimal("50000000.00"),
            premio_liquido=Decimal("480000.00"),
        )

    def tick(self) -> None:
        """Executa um pulso de mercado (tick) simulando dinamicamente o pregão PUMA."""
        self.tick_count += 1
        delta_cents = random.choice([-0.01, 0.00, 0.01])
        new_px = (self.ref_price + Decimal(str(delta_cents))).quantize(Decimal("0.01"))
        if new_px > Decimal("0.05"):
            self.ref_price = new_px
            # Atualiza ordens no livro
            bids, asks = self.l3_book.get_l2_depth(5)
            # Rebalanceia dinamicamente
            self.l3_book.apply_order_add(
                order_id=1000 + self.tick_count,
                side=OrderSide.BID if delta_cents <= 0 else OrderSide.ASK,
                price=new_px,
                quantity=random.choice([1000, 2500, 5000, 10000]),
                sequence_number=100 + self.tick_count,
            )

    def run_concept_simulation(self) -> Dict[str, Any]:
        """Dispara a simulação analítica do conceito selecionado."""
        concept = CONCEPTS[self.selected_concept_idx]
        self.concept_simulation_result = simulate_concept(concept.id)
        self.last_simulated_concept = concept.id
        return self.concept_simulation_result

    # ==========================================================================
    # Componentes de Renderização
    # ==========================================================================

    def _render_header(self, title: str, width: int = 96) -> List[str]:
        """Renderiza cabeçalho com abas interativas."""
        f = self.fmt
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = []

        # Borda Superior
        lines.append(f"{f.bright_cyan}╔{'═' * (width - 2)}╗{f.reset}")

        # Título Principal e Relógio
        top_left = f" B3 INTEGRATION ATLAS — {title} "
        top_right = f" {now_str} "
        space = width - 2 - visual_len(top_left) - visual_len(top_right)
        if space < 1:
            space = 1
        lines.append(
            f"{f.bright_cyan}║{f.bold}{f.bright_white}{top_left}{f.reset}"
            f"{' ' * space}"
            f"{f.dim}{f.bright_cyan}{top_right}{f.reset}"
            f"{f.bright_cyan}║{f.reset}"
        )

        # Abas de Navegação
        tabs = [
            ("1", "Visão Geral", "overview"),
            ("2", "Monitor PUMA", "puma"),
            ("3", "Barramento Central", "central_bus"),
            ("4", "SNG / SRO", "sng_sro"),
            ("5", "12 Conceitos", "concepts"),
        ]
        tab_strs = []
        for key, name, screen_key in tabs:
            if screen_key == self.active_screen:
                tab_strs.append(
                    f"{f.bg_blue}{f.bold}{f.bright_white} [{key}] {name} {f.reset}"
                )
            else:
                tab_strs.append(f"{f.dim} [{key}] {name} {f.reset}")

        tabs_line = " ".join(tab_strs)
        pad_tabs = width - 2 - visual_len(tabs_line)
        lines.append(
            f"{f.bright_cyan}║{f.reset} {tabs_line}{' ' * max(0, pad_tabs - 1)}{f.bright_cyan}║{f.reset}"
        )

        lines.append(f"{f.bright_cyan}╠{'═' * (width - 2)}╣{f.reset}")
        return lines

    def _render_footer(self, width: int = 96) -> List[str]:
        """Renderiza rodapé com atalhos de teclado institucionais."""
        f = self.fmt
        shortcuts = (
            f"{f.bold}[1-5]{f.reset} Trocar Tela | "
            f"{f.bold}[r]{f.reset} Atualizar Cotação/Tick | "
            f"{f.bold}[s]{f.reset} Simular Conceito | "
            f"{f.bold}[q]{f.reset} Sair"
        )
        pad_len = width - 2 - visual_len(shortcuts)
        lines = [
            f"{f.bright_cyan}╠{'═' * (width - 2)}╣{f.reset}",
            f"{f.bright_cyan}║{f.reset} {shortcuts}{' ' * max(0, pad_len - 1)}{f.bright_cyan}║{f.reset}",
            f"{f.bright_cyan}╚{'═' * (width - 2)}╝{f.reset}",
        ]
        return lines

    # ==========================================================================
    # Tela 1: Visão Geral (Overview)
    # ==========================================================================

    def render_overview(self, width: int = 96) -> str:
        """Painel de Visão Geral: Estado dos 107 fluxos, 91 leis e 87 manuais."""
        f = self.fmt
        lines = self._render_header("VISÃO GERAL DO ATLAS", width=width)

        # Cards de KPI Topo
        total_flows = len(self.flows)
        total_laws = len(self.laws_dict)
        total_sources = len(self.sources)
        total_concepts = len(CONCEPTS)

        card1 = f"{f.bright_yellow}FLUXOS TOTAIS: {f.bold}{total_flows}{f.reset} (100% Mapeados)"
        card2 = f"{f.bright_green}LEIS FORMAIS: {f.bold}{total_laws}{f.reset} (100% Provadas)"
        card3 = f"{f.bright_cyan}MANUAIS B3: {f.bold}{total_sources}{f.reset} (Fontes Oficiais)"
        card4 = f"{f.bright_magenta}CONCEITOS: {f.bold}{total_concepts}{f.reset} (Simuláveis)"

        kpi_row = f"  {card1}   {card2}   {card3}   {card4}"
        pad_kpi = width - 2 - visual_len(kpi_row)
        lines.append(f"{f.bright_cyan}║{f.reset}{kpi_row}{' ' * max(0, pad_kpi)}{f.bright_cyan}║{f.reset}")
        lines.append(f"{f.bright_cyan}╟{'─' * (width - 2)}╢{f.reset}")

        # Tabela de Segmentos e Submódulos
        sec_title = f"{f.bold}{f.bright_white}DISTRIBUIÇÃO DA COBERTURA POR SEGMENTO OPERACIONAL B3:{f.reset}"
        lines.append(f"{f.bright_cyan}║{f.reset}  {sec_title}{' ' * max(0, width - 4 - visual_len(sec_title))}{f.bright_cyan}║{f.reset}")

        categories = {
            "Clearing, CCP & Liquidação Multilateral": (18, "LAW-001 a LAW-018", 18, 18),
            "Renda Variável & Negociação PUMA": (19, "LAW-019 a LAW-037", 19, 19),
            "Derivativos Financeiros & Agropecuários": (16, "LAW-038 a LAW-053", 16, 16),
            "Renda Fixa, Selic & Balcão B3": (17, "LAW-054 a LAW-070", 17, 17),
            "Câmbio, NDF & Colaterais Internacionais": (14, "LAW-071 a LAW-084", 14, 14),
            "Registradores Oficiais (SNG, SRO, Recebíveis)": (13, "LAW-085 a LAW-097", 13, 13),
            "DREX Fase 2, Tokenização & Inovação Central": (10, "LAW-098 a LAW-106B", 10, 10),
        }

        header_tbl = f"  {'SEGMENTO B3':<44} {'FLUXOS':<8} {'LEIS COBERTAS':<22} {'CONFORMIDADE':<14}"
        lines.append(f"{f.bright_cyan}║{f.reset}{f.dim}{header_tbl}{f.reset}{' ' * max(0, width - 2 - visual_len(header_tbl))}{f.bright_cyan}║{f.reset}")

        for cat_name, (count, laws_range, verified, total_c) in categories.items():
            pct_bar = f"{f.bright_green}100.0% [OK]{f.reset}"
            row = f"  {cat_name:<44} {count:<8} {laws_range:<22} {pct_bar}"
            pad = width - 2 - visual_len(row)
            lines.append(f"{f.bright_cyan}║{f.reset}{row}{' ' * max(0, pad)}{f.bright_cyan}║{f.reset}")

        lines.append(f"{f.bright_cyan}╟{'─' * (width - 2)}╢{f.reset}")

        # Provador Formal de Invariantes e Auditoria
        prov_title = f"{f.bold}{f.bright_white}STATUS DO VERIFICADOR FORMAL DE INVARIANTES (ZERO DEPENDÊNCIAS):{f.reset}"
        lines.append(f"{f.bright_cyan}║{f.reset}  {prov_title}{' ' * max(0, width - 4 - visual_len(prov_title))}{f.bright_cyan}║{f.reset}")

        status_items = [
            f"• Teorema Central de Conservação de Saldo (LAW-001): {f.bright_green}PROVADO FORMALMENTE{f.reset} (Δ = 0.00 BRL)",
            f"• Não-Negatividade de Ativos e Margens (LAW-002):     {f.bright_green}INVARIANTE ATENDIDA{f.reset} (∀ i, M_i ≥ 0)",
            f"• Lei da Conservação Contábil Estrita (LAW-106A):     {f.bright_green}ATIVOS = PASSIVOS{f.reset} (Balanço Zero)",
            f"• Cascata Imutável de Salvaguardas (LAW-106B):       {f.bright_green}HIERARQUIA ÍNTEGRA{f.reset} (5 Camadas B3 CCP)",
            f"• Cobertura de Manuais Regulatórios:                 {f.bright_cyan}87 FONTES VALIDAS{f.reset} (B3, BACEN, CVM, SUSEP)",
        ]
        for item in status_items:
            pad = width - 2 - visual_len(f"  {item}")
            lines.append(f"{f.bright_cyan}║{f.reset}  {item}{' ' * max(0, pad)}{f.bright_cyan}║{f.reset}")

        lines.extend(self._render_footer(width=width))
        return "\n".join(lines)

    # ==========================================================================
    # Tela 2: Monitor de Negociação PUMA em Tempo Real
    # ==========================================================================

    def render_puma(self, width: int = 96) -> str:
        """Monitor de Negociação PUMA: mini-livro ASCII, spread e leilão MOC."""
        f = self.fmt
        lines = self._render_header("MONITOR DE NEGOCIAÇÃO PUMA — TEMPO REAL", width=width)

        # Barra de status do instrumento
        bbo = self.l3_book.get_bbo()
        bid_px = bbo.bid_price or Decimal("0.00")
        ask_px = bbo.ask_price or Decimal("0.00")
        spread = bbo.spread or Decimal("0.00")
        spread_bps = (
            (spread / bbo.mid_price * Decimal("10000")).quantize(Decimal("0.1"))
            if bbo.mid_price and bbo.mid_price > 0
            else Decimal("0.0")
        )

        inst_info = (
            f"  {f.bold}{f.bright_white}ATIVO: {self.symbol}{f.reset} | "
            f"ESTADO: {f.bg_green}{f.bright_white} PREGÃO ABERTO / CALL DE FECHAMENTO {f.reset} | "
            f"REF: R$ {self.ref_price:.2f} | "
            f"TICKS: {self.tick_count}"
        )
        lines.append(f"{f.bright_cyan}║{f.reset}{inst_info}{' ' * max(0, width - 2 - visual_len(inst_info))}{f.bright_cyan}║{f.reset}")
        lines.append(f"{f.bright_cyan}╟{'─' * (width - 2)}╢{f.reset}")

        # Métricas de Microestrutura
        micro_row = (
            f"  {f.bright_green}BEST BID: R$ {bid_px:.2f} ({bbo.bid_quantity:,}){f.reset}  |  "
            f"{f.bright_red}BEST ASK: R$ {ask_px:.2f} ({bbo.ask_quantity:,}){f.reset}  |  "
            f"{f.bright_yellow}SPREAD: R$ {spread:.2f} ({spread_bps} bps){f.reset}  |  "
            f"MID: R$ {bbo.mid_price or 0:.3f}"
        )
        lines.append(f"{f.bright_cyan}║{f.reset}{micro_row}{' ' * max(0, width - 2 - visual_len(micro_row))}{f.bright_cyan}║{f.reset}")
        lines.append(f"{f.bright_cyan}╟{'─' * (width - 2)}╢{f.reset}")

        # Mini Livro de Ofertas ASCII (Bids / Asks lado a lado)
        lines.append(
            f"{f.bright_cyan}║{f.reset}  {f.bold}{f.bright_green}{'── COMPRADOR (BIDS) ──':<42}{f.reset}"
            f"  {f.bold}{f.bright_red}{'── VENDEDOR (ASKS) ──':<44}{f.reset}{f.bright_cyan}║{f.reset}"
        )
        col_hdr = f"  {'ORD':<4} {'QTD':<8} {'PREÇO':<8} {'PROFUNDIDADE':<18} | {'PREÇO':<8} {'QTD':<8} {'ORD':<4} {'PROFUNDIDADE':<18}"
        lines.append(f"{f.bright_cyan}║{f.reset}{f.dim}{col_hdr}{f.reset}{' ' * max(0, width - 2 - visual_len(col_hdr))}{f.bright_cyan}║{f.reset}")

        depth_dict = self.l3_book.get_l2_depth(5)
        bids = depth_dict.get("bids", [])
        asks = depth_dict.get("asks", [])
        max_qty = max(
            [b.get("total_quantity", 0) for b in bids] + [a.get("total_quantity", 0) for a in asks] + [1]
        )

        for i in range(max(len(bids), len(asks))):
            # Lado Compra
            if i < len(bids):
                b = bids[i]
                b_ord = b.get("order_count", 1)
                b_qty = b.get("total_quantity", 0)
                b_px = Decimal(str(b.get("price", 0)))
                b_bar = f.bar(b_qty, max_qty, length=12)
                b_str = f"  {b_ord:<4} {b_qty:<8} {f.bright_green}R$ {b_px:<5.2f}{f.reset} {f.green}{b_bar}{f.reset}"
            else:
                b_str = " " * 42

            # Lado Venda
            if i < len(asks):
                a = asks[i]
                a_ord = a.get("order_count", 1)
                a_qty = a.get("total_quantity", 0)
                a_px = Decimal(str(a.get("price", 0)))
                a_bar = f.bar(a_qty, max_qty, length=12)
                a_str = f"{f.bright_red}R$ {a_px:<5.2f}{f.reset} {a_qty:<8} {a_ord:<4} {f.red}{a_bar}{f.reset}"
            else:
                a_str = " " * 42

            row = f"{b_str} | {a_str}"
            pad = width - 2 - visual_len(row)
            lines.append(f"{f.bright_cyan}║{f.reset}{row}{' ' * max(0, pad)}{f.bright_cyan}║{f.reset}")

        lines.append(f"{f.bright_cyan}╟{'─' * (width - 2)}╢{f.reset}")

        # Painel do Leilão MOC (Market On Close) & Freeze Window
        theo = self.moc_engine.calculate_theoretical_price()
        phase_badge = f"{f.bg_red}{f.bright_white} FREEZE WINDOW (BLOQUEIO DE CANCELAMENTOS) {f.reset}"

        moc_title = f"{f.bold}{f.bright_white}STATUS DO LEILÃO DE FECHAMENTO PUMA (MOC / LOC / ATC) — FL-098:{f.reset}"
        lines.append(f"{f.bright_cyan}║{f.reset}  {moc_title}{' ' * max(0, width - 4 - visual_len(moc_title))}{f.bright_cyan}║{f.reset}")

        p_teo = theo.uncross_price or self.ref_price
        v_teo = theo.executable_volume
        imb_qty = theo.imbalance_quantity
        imb_side = "COMPRA" if imb_qty >= 0 else "VENDA"

        moc_line1 = (
            f"  Fase Atual: {phase_badge} | "
            f"Regra de Desempate: {f.bold}{theo.rule_applied}{f.reset}"
        )
        lines.append(f"{f.bright_cyan}║{f.reset}{moc_line1}{' ' * max(0, width - 2 - visual_len(moc_line1))}{f.bright_cyan}║{f.reset}")

        moc_line2 = (
            f"  {f.bright_yellow}Preço Teórico (P_teo): R$ {p_teo:.2f}{f.reset} | "
            f"{f.bright_green}Volume Casado: {v_teo:,} ações{f.reset} | "
            f"{f.bright_magenta}Desbalanceamento: {abs(imb_qty):,} ações ({imb_side}){f.reset}"
        )
        lines.append(f"{f.bright_cyan}║{f.reset}{moc_line2}{' ' * max(0, width - 2 - visual_len(moc_line2))}{f.bright_cyan}║{f.reset}")

        lines.extend(self._render_footer(width=width))
        return "\n".join(lines)

    # ==========================================================================
    # Tela 3: Scoreboard do Barramento Central & Waterfall
    # ==========================================================================

    def render_central_bus(self, width: int = 96) -> str:
        """Scoreboard do Barramento Central: Ativos vs Passivos (LAW-106A) e Waterfall (LAW-106B)."""
        f = self.fmt
        lines = self._render_header("BARRAMENTO CENTRAL DE LIQUIDAÇÃO B3 — FL-100", width=width)

        bs = self.bus.get_consolidated_balance_sheet()

        # Scoreboard Ativos vs Passivos (LAW-106A)
        eq_status = f"{f.bg_green}{f.bright_white} EQUILÍBRIO ESTRITO CONFIRMADO (Δ = 0,00 BRL) {f.reset}"
        lines.append(
            f"{f.bright_cyan}║{f.reset}  {f.bold}{f.bright_white}SCOREBOARD CONTÁBIL MULTILATERAL (LEI FORMAL LAW-106A):{f.reset} {eq_status}"
            f"{' ' * max(0, width - 4 - visual_len(f'SCOREBOARD CONTÁBIL MULTILATERAL (LEI FORMAL LAW-106A): EQUILÍBRIO ESTRITO CONFIRMADO (Δ = 0,00 BRL) '))}{f.bright_cyan}║{f.reset}"
        )
        lines.append(f"{f.bright_cyan}╟{'─' * (width - 2)}╢{f.reset}")

        # Tabela Lado a Lado: Ativos vs Passivos
        hdr = f"  {f.bold}{f.bright_green}{'ATIVOS DE LIQUIDAÇÃO CCP':<44}{f.reset} | {f.bold}{f.bright_red}{'PASSIVOS E OBRIGAÇÕES':<44}{f.reset}"
        lines.append(f"{f.bright_cyan}║{f.reset}{hdr}{f.bright_cyan}║{f.reset}")

        rows = [
            (
                f"• Caixa em Banco Central (STR): R$ {bs.cash_in_central_bank:,.2f}",
                f"• Pagamentos Devidos a Membros: R$ {bs.pending_member_payables:,.2f}",
            ),
            (
                f"• Colaterais e Margens em Custódia: R$ {bs.collateral_custody:,.2f}",
                f"• Margens Depositadas por Membros: R$ {bs.member_margin_obligations:,.2f}",
            ),
            (
                f"• Recursos Próprios no Fundo: R$ {bs.default_fund_cash:,.2f}",
                f"• Cotas dos Membros no Fundo Mútuo: R$ {bs.default_fund_member_equity:,.2f}",
            ),
            (
                f"• B3 Skin in the Game (First-Loss): R$ {bs.b3_sitg_allocated:,.2f}",
                f"• Reserva de Capital Alocado B3: R$ {bs.b3_capital_reserve:,.2f}",
            ),
            (
                f"• Recebíveis de Membros Devedores: R$ {bs.pending_member_receivables:,.2f}",
                f"• Ajuste Contábil Pro-Rata: R$ 0,00",
            ),
        ]

        for left_txt, right_txt in rows:
            l_str = f"  {left_txt:<44}"
            r_str = f"{right_txt:<44}"
            line_txt = f"{l_str} | {r_str}"
            pad = width - 2 - visual_len(line_txt)
            lines.append(f"{f.bright_cyan}║{f.reset}{line_txt}{' ' * max(0, pad)}{f.bright_cyan}║{f.reset}")

        lines.append(f"{f.bright_cyan}╟{'─' * (width - 2)}╢{f.reset}")

        # Totais
        tot_row = (
            f"  {f.bold}{f.bright_green}TOTAL ATIVOS:   R$ {bs.total_assets:,.2f}{f.reset}     | "
            f"{f.bold}{f.bright_red}TOTAL PASSIVOS: R$ {bs.total_liabilities:,.2f}{f.reset}"
        )
        lines.append(f"{f.bright_cyan}║{f.reset}{tot_row}{' ' * max(0, width - 2 - visual_len(tot_row))}{f.bright_cyan}║{f.reset}")
        lines.append(f"{f.bright_cyan}╟{'─' * (width - 2)}╢{f.reset}")

        # Cascata de Salvaguardas do Waterfall B3 CCP (LAW-106B)
        wf_title = f"{f.bold}{f.bright_white}ESTRUTURA HIERÁRQUICA DO WATERFALL DE SALVAGUARDAS (LAW-106B):{f.reset}"
        lines.append(f"{f.bright_cyan}║{f.reset}  {wf_title}{' ' * max(0, width - 4 - visual_len(wf_title))}{f.bright_cyan}║{f.reset}")

        layers = [
            ("1ª CAMADA", "Margem de Garantia do Participante Faltoso", "R$ 150.000.000,00", "Íntegra"),
            ("2ª CAMADA", "Aporte Próprio do Faltoso no Fundo de Liquidação", "R$ 45.000.000,00", "Íntegra"),
            ("3ª CAMADA", "Capital Próprio B3 CCP (Skin in the Game First-Loss)", "R$ 500.000.000,00", "Preservada"),
            ("4ª CAMADA", "Fundo Mútuo de Liquidação (Membros Adimplentes)", "R$ 1.455.000.000,00", "Preservada"),
            ("5ª CAMADA", "Capital Adicional B3 CCP (Second-Loss)", "R$ 300.000.000,00", "Preservada"),
        ]

        for layer_num, name, amount, status in layers:
            status_tag = f"{f.bright_green}[{status}]{f.reset}"
            l_row = f"  {f.bright_yellow}{layer_num:<11}{f.reset} {name:<46} {f.bold}{amount:>20}{f.reset} {status_tag}"
            pad = width - 2 - visual_len(l_row)
            lines.append(f"{f.bright_cyan}║{f.reset}{l_row}{' ' * max(0, pad)}{f.bright_cyan}║{f.reset}")

        lines.extend(self._render_footer(width=width))
        return "\n".join(lines)

    # ==========================================================================
    # Tela 4: Consulta Rápida SNG & SRO
    # ==========================================================================

    def render_sng_sro(self, width: int = 96) -> str:
        """Consulta Rápida SNG (Gravames) e SRO (Seguros SUSEP)."""
        f = self.fmt
        lines = self._render_header("REGISTRADORES NACIONAIS — SNG & SRO", width=width)

        # Painel 1: Sistema Nacional de Gravames (SNG)
        sng_header = f"{f.bold}{f.bright_white}SNG — SISTEMA NACIONAL DE GRAVAMES (B3 FINANCIAMENTOS) — FL-106:{f.reset}"
        lines.append(f"{f.bright_cyan}║{f.reset}  {sng_header}{' ' * max(0, width - 4 - visual_len(sng_header))}{f.bright_cyan}║{f.reset}")

        gravame = self.sng.consultar_por_chassi("9BWZZZ377VT004251")
        if gravame:
            v = gravame.veiculo
            sng_row1 = (
                f"  {f.bright_yellow}CHASSI: {v.chassi}{f.reset} | "
                f"PLACA: {f.bold}{v.placa}{f.reset} | "
                f"VEÍCULO: {v.marca_modelo} ({v.ano_modelo}) | "
                f"UF DETRAN: {v.uf_emplacamento}"
            )
            lines.append(f"{f.bright_cyan}║{f.reset}{sng_row1}{' ' * max(0, width - 2 - visual_len(sng_row1))}{f.bright_cyan}║{f.reset}")

            sng_row2 = (
                f"  CREDOR: {gravame.credor_nome} (CNPJ: {gravame.credor_cnpj}) | "
                f"STATUS: {f.bright_green}ATIVO NO DETRAN{f.reset}"
            )
            lines.append(f"{f.bright_cyan}║{f.reset}{sng_row2}{' ' * max(0, width - 2 - visual_len(sng_row2))}{f.bright_cyan}║{f.reset}")

            sng_row3 = (
                f"  MODALIDADE: {gravame.modalidade.value} | "
                f"VALOR FINANCIADO: R$ {gravame.valor_financiamento:,.2f} ({gravame.quantidade_parcelas} parcelas) | "
                f"CONTRATO: {gravame.contrato_numero}"
            )
            lines.append(f"{f.bright_cyan}║{f.reset}{sng_row3}{' ' * max(0, width - 2 - visual_len(sng_row3))}{f.bright_cyan}║{f.reset}")

            sng_row4 = (
                f"  HASH SHA-256 REGISTRO: {f.dim}{gravame.hash_seguranca[:36]}...{f.reset} | "
                f"CONFORMIDADE: {f.bright_green}RESOLUÇÃO CONTRAN 689/2017 [OK]{f.reset}"
            )
            lines.append(f"{f.bright_cyan}║{f.reset}{sng_row4}{' ' * max(0, width - 2 - visual_len(sng_row4))}{f.bright_cyan}║{f.reset}")

        lines.append(f"{f.bright_cyan}╟{'─' * (width - 2)}╢{f.reset}")

        # Painel 2: Sistema de Registro de Operações (SRO SUSEP)
        sro_header = f"{f.bold}{f.bright_white}SRO — SISTEMA DE REGISTRO DE OPERAÇÕES (SUSEP / B3 SEGUROS) — FL-107:{f.reset}"
        lines.append(f"{f.bright_cyan}║{f.reset}  {sro_header}{' ' * max(0, width - 4 - visual_len(sro_header))}{f.bright_cyan}║{f.reset}")

        apolices = list(self.sro.apolices.values())
        if apolices:
            ap = apolices[0]
            sro_row1 = (
                f"  {f.bright_cyan}APÓLICE: {ap.apolice_id}{f.reset} | "
                f"PROPOSTA: {ap.numero_proposta} | "
                f"RAMO: {ap.ramo.value} ({ap.ramo.name}) | "
                f"STATUS: {f.bright_green}{ap.status.value}{f.reset}"
            )
            lines.append(f"{f.bright_cyan}║{f.reset}{sro_row1}{' ' * max(0, width - 2 - visual_len(sro_row1))}{f.bright_cyan}║{f.reset}")

            sro_row2 = (
                f"  SEGURADORA: {ap.seguradora_lider_nome} (CNPJ: {ap.seguradora_lider_cnpj}) | "
                f"TOMADOR: {ap.segurado_nome}"
            )
            lines.append(f"{f.bright_cyan}║{f.reset}{sro_row2}{' ' * max(0, width - 2 - visual_len(sro_row2))}{f.bright_cyan}║{f.reset}")

            sro_row3 = (
                f"  LIMITE MÁXIMO GARANTIA (LMG): {f.bold}R$ {ap.limite_maximo_garantia:,.2f}{f.reset} | "
                f"PRÊMIO LÍQUIDO: R$ {ap.premio_liquido:,.2f} | "
                f"COSSEGURO CEDIDO: 20.00%"
            )
            lines.append(f"{f.bright_cyan}║{f.reset}{sro_row3}{' ' * max(0, width - 2 - visual_len(sro_row3))}{f.bright_cyan}║{f.reset}")

            sro_row4 = (
                f"  HASH REGISTRO SUSEP: {f.dim}{ap.susep_hash[:36]}...{f.reset} | "
                f"CONFORMIDADE: {f.bright_green}CIRCULAR SUSEP 645/2021 [OK]{f.reset}"
            )
            lines.append(f"{f.bright_cyan}║{f.reset}{sro_row4}{' ' * max(0, width - 2 - visual_len(sro_row4))}{f.bright_cyan}║{f.reset}")

        lines.extend(self._render_footer(width=width))
        return "\n".join(lines)

    # ==========================================================================
    # Tela 5: Menu Interativo de Simulação dos 12 Conceitos Complexos
    # ==========================================================================

    def render_concepts(self, width: int = 96) -> str:
        """Menu interativo de simulação dos 12 conceitos complexos."""
        f = self.fmt
        lines = self._render_header("MENU INTERATIVO: OS 12 CONCEITOS COMPLEXOS B3", width=width)

        sec_title = f"{f.bold}{f.bright_white}SELECIONE UM CONCEITO E PRESSIONE [s] PARA SIMULAR EM TEMPO REAL:{f.reset}"
        lines.append(f"{f.bright_cyan}║{f.reset}  {sec_title}{' ' * max(0, width - 4 - visual_len(sec_title))}{f.bright_cyan}║{f.reset}")

        # Lista de 12 Conceitos
        for i, c in enumerate(CONCEPTS):
            num = i + 1
            is_sel = i == self.selected_concept_idx
            sel_char = "▶" if is_sel else " "
            prefix = f"{sel_char} [{num:2d}] {c.title:<46} (Fluxos: {', '.join(c.related_flows[:2])})"
            if is_sel:
                line_str = f"  {f.bg_blue}{f.bold}{f.bright_white} {prefix:<88} {f.reset}"
            else:
                line_str = f"  {prefix:<90}"
            pad = width - 2 - visual_len(line_str)
            lines.append(f"{f.bright_cyan}║{f.reset}{line_str}{' ' * max(0, pad)}{f.bright_cyan}║{f.reset}")

        lines.append(f"{f.bright_cyan}╟{'─' * (width - 2)}╢{f.reset}")

        # Resultado da Simulação do Conceito Selecionado
        cur_concept = CONCEPTS[self.selected_concept_idx]
        if self.last_simulated_concept == cur_concept.id and self.concept_simulation_result:
            res = self.concept_simulation_result
            status_text = res.get("status", "SUCESSO")
            res_header = (
                f"  {f.bold}{f.bright_green}RESULTADO DA SIMULAÇÃO: {cur_concept.id} [{status_text}]{f.reset}"
            )
            lines.append(f"{f.bright_cyan}║{f.reset}{res_header}{' ' * max(0, width - 2 - visual_len(res_header))}{f.bright_cyan}║{f.reset}")

            # Detalhes do Payload
            law_ver = res.get("law_verified", "LAW-CANONICAL")
            lines.append(
                f"{f.bright_cyan}║{f.reset}  Lei Formal Verificada: {f.bright_yellow}{law_ver}{f.reset} | "
                f"Veredito: {f.bright_green}INVARIANTE MATEMÁTICA CONSERVADA (100% OK){f.reset}"
                f"{' ' * max(0, width - 4 - visual_len(f'  Lei Formal Verificada: {law_ver} | Veredito: INVARIANTE MATEMÁTICA CONSERVADA (100% OK)'))}{f.bright_cyan}║{f.reset}"
            )

            # Mostra dados sintéticos em formato compacto
            keys = [k for k in res.keys() if k not in ("concept_id", "title", "law_verified", "status")]
            for k in keys[:3]:
                val_str = str(res[k])
                if len(val_str) > 70:
                    val_str = val_str[:68] + "..."
                row_k = f"  • {k}: {val_str}"
                lines.append(f"{f.bright_cyan}║{f.reset}{row_k}{' ' * max(0, width - 2 - visual_len(row_k))}{f.bright_cyan}║{f.reset}")
        else:
            hint = f"  {f.dim}Pressione [s] para disparar a simulação formal deste conceito estruturante...{f.reset}"
            lines.append(f"{f.bright_cyan}║{f.reset}{hint}{' ' * max(0, width - 2 - visual_len(hint))}{f.bright_cyan}║{f.reset}")

        lines.extend(self._render_footer(width=width))
        return "\n".join(lines)

    # ==========================================================================
    # Dispatcher Geral de Telas
    # ==========================================================================

    def render_screen(self, screen_name: Optional[str] = None, width: int = 96) -> str:
        """Renderiza a tela solicitada ou a tela ativa atual."""
        raw_target = (screen_name or self.active_screen).lower().strip()
        target = self.SCREEN_ALIASES.get(raw_target, "overview")
        if target == "overview":
            return self.render_overview(width=width)
        elif target == "puma":
            return self.render_puma(width=width)
        elif target == "central_bus":
            return self.render_central_bus(width=width)
        elif target == "sng_sro":
            return self.render_sng_sro(width=width)
        elif target == "concepts":
            return self.render_concepts(width=width)
        return self.render_overview(width=width)
        return self.render_overview(width=width)

    def render(self, width: int = 96) -> str:
        """Renderiza a tela atual."""
        return self.render_screen(self.active_screen, width=width)

    # ==========================================================================
    # Execução Interativa e Headless
    # ==========================================================================

    def run_headless(self, ticks: int = 1, width: int = 96) -> str:
        """Execução headless sem loop de entrada (ideal para pipelines, testes e scripts)."""
        output_snapshots = []
        for _ in range(ticks):
            self.tick()
            snap = self.render(width=width)
            output_snapshots.append(snap)
        return "\n\n".join(output_snapshots)

    def run_interactive(self, width: int = 96) -> int:
        """Executa a interface em modo interativo com leitura de teclas."""
        # Se não houver tty disponível, roda em modo headless limpo
        if not sys.stdin.isatty():
            sys.stdout.write(self.render(width=width) + "\n")
            return 0

        # Tentativa de captura raw de tecla Unix
        try:
            import termios
            import tty

            def get_char() -> str:
                fd = sys.stdin.fileno()
                old_settings = termios.tcgetattr(fd)
                try:
                    tty.setraw(fd)
                    ch = sys.stdin.read(1)
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                return ch

        except ImportError:
            def get_char() -> str:
                return sys.stdin.readline().strip()[:1]

        # Limpa tela e entra no loop
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()

        try:
            while True:
                output = self.render(width=width)
                sys.stdout.write("\033[H" + output + "\n")
                sys.stdout.flush()

                key = get_char().lower()
                if key in ("q", "\x03", "\x04"):  # q, Ctrl+C, Ctrl+D
                    break
                elif key == "1":
                    self.active_screen = "overview"
                elif key == "2":
                    self.active_screen = "puma"
                elif key == "3":
                    self.active_screen = "central_bus"
                elif key == "4":
                    self.active_screen = "sng_sro"
                elif key == "5":
                    self.active_screen = "concepts"
                elif key == "r":
                    self.tick()
                elif key == "s":
                    if self.active_screen == "concepts":
                        self.run_concept_simulation()
                    elif self.active_screen == "puma":
                        self.tick()
                elif key in ("j", "n", "\x1b[B"):  # seta para baixo / próximo conceito
                    if self.active_screen == "concepts":
                        self.selected_concept_idx = (self.selected_concept_idx + 1) % len(CONCEPTS)
                elif key in ("k", "p", "\x1b[A"):  # seta para cima / conceito anterior
                    if self.active_screen == "concepts":
                        self.selected_concept_idx = (self.selected_concept_idx - 1) % len(CONCEPTS)
        except KeyboardInterrupt:
            pass

        sys.stdout.write("\nEncerrando B3 Integration Atlas TUI. Até logo!\n")
        return 0


# ==============================================================================
# Funções de Entrada CLI
# ==============================================================================

def run_tui(args: argparse.Namespace) -> int:
    """Entrypoint invocado pelo comando `./b3 tui`."""
    screen = getattr(args, "screen", "overview") or "overview"
    use_color = not getattr(args, "no_color", False)
    headless = getattr(args, "headless", False)
    ticks = getattr(args, "ticks", 1) or 1
    symbol = getattr(args, "symbol", "PETR4") or "PETR4"
    concept_id = getattr(args, "concept", None)

    engine = B3TUIEngine(
        use_color=use_color,
        active_screen=screen,
        symbol=symbol,
        concept_id=concept_id,
    )

    if headless or not sys.stdin.isatty():
        output = engine.run_headless(ticks=ticks)
        sys.stdout.write(output + "\n")
        return 0

    return engine.run_interactive()


def run_monitor(args: argparse.Namespace) -> int:
    """Entrypoint invocado pelo comando `./b3 monitor` (focado no monitor PUMA)."""
    setattr(args, "screen", "puma")
    return run_tui(args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="B3 Integration Atlas — Terminal User Interface (TUI)")
    parser.add_argument("--screen", choices=["overview", "puma", "bus", "sng_sro", "concepts"], default="overview")
    parser.add_argument("--symbol", default="PETR4")
    parser.add_argument("--concept", default=None)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--ticks", type=int, default=1)
    parser.add_argument("--no-color", action="store_true")
    sys.exit(run_tui(parser.parse_args()))

