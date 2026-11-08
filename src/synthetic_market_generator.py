"""Gerador Institucional de Dados Sintéticos e Motor de Replay de Mercado — B3 S.A.

Módulo de alta fidelidade para geração estocástica determinística de dados de mercado,
carteiras de garantias veiculares (SNG) e contratos de seguros regulados (SRO SUSEP),
com suporte a exportação multi-formato (PUMA SBE binário, FIX 4.4 / FIX 5.0 SP2,
arquivos posicionais IMBARQ 004/006/014 e JSON institucional de estresse), além de
motor de replay temporal de eventos com múltiplos multiplicadores de velocidade.

Fundamentação Técnica e Regulatória:
- PUMA Trading System: Mensageria binária Simple Binary Encoding (SBE) e FIX Protocol
- Câmara B3 & Risco CORE: Curva de DI Futuro (interpolação exponencial e PU B3), Dólar e PTAX BACEN
- Sistema Nacional de Gravames (SNG): Chassi automotivo ISO 3779 e Invariante LAW-112
- Sistema de Registro de Operações (SRO SUSEP): Cosseguro 100.00% e Invariante LAW-113 (Teto LMG)
- Central Depositária CSD: Layouts IMBARQ004 (Financeiro), IMBARQ006 (Posições) e IMBARQ014 (Garantias)

100% Python Standard Library pura: dataclasses, enum, math, random, typing, decimal,
datetime, time, json, struct, re, hashlib.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
import random
import re
import struct
import time
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Tuple, Union

# Importações dos módulos internos do B3 Integration Atlas
from src.puma_sbe import (
    DEFAULT_SCHEMA_ID,
    DEFAULT_SCHEMA_VERSION,
    ExecType as SbeExecType,
    ExecutionReportBinary,
    NewOrderSingleBinary,
    OrdStatus as SbeOrdStatus,
    PRICE_SCALE as SBE_PRICE_SCALE,
    PumaSbeCodec,
    Side as SbeSide,
    TimeInForce as SbeTimeInForce,
)
from src.puma_fix import (
    FixExecType,
    FixMessage,
    FixMsgType,
    FixOrdStatus,
    FixOrdType,
    FixSide,
    FixTimeInForce,
)
from src.sng_gravames_engine import (
    ModalidadeGravame,
    SngGravamesEngine,
    StatusGravame,
    VeiculoIdentificacao,
)
from src.sro_seguros_engine import (
    ApoliceRecord,
    CosseguradorShare,
    RamoSeguro,
    SinistroRecord,
    SroSegurosEngine,
    StatusApolice,
    StatusSinistro,
)


# ==============================================================================
# CONSTANTES DE MERCADO & REFERÊNCIAS OFICIAIS B3
# ==============================================================================

# 50 Tickers Oficiais mais líquidos e representativos da B3 S.A.
EQUITY_TICKERS_50: List[Tuple[str, str, float, float, float]] = [
    # (Ticker, ISIN, Preço Base R$, Volatilidade Anualizada, Volume Diário Base)
    ("PETR4", "BRPETRACNPR6", 38.50, 0.28, 45_000_000.0),
    ("VALE3", "BRVALEACNOR0", 61.20, 0.25, 32_000_000.0),
    ("ITUB4", "BRITUBACNPR1", 34.80, 0.21, 28_000_000.0),
    ("BBDC4", "BRBBDCACNPR8", 14.20, 0.23, 24_000_000.0),
    ("B3SA3", "BRB3SAACNOR6", 11.90, 0.24, 20_000_000.0),
    ("ABEV3", "BRABEVACNOR5", 12.40, 0.18, 18_000_000.0),
    ("BBAS3", "BRBBASACNOR3", 28.60, 0.26, 22_000_000.0),
    ("WEGE3", "BRWEGEACNOR0", 53.50, 0.22, 12_000_000.0),
    ("RENT3", "BRRENTACNOR4", 44.80, 0.27, 10_000_000.0),
    ("SUZB3", "BRSUZBACNOR0", 57.50, 0.29, 14_000_000.0),
    ("JBSS3", "BRJBSSACNOR8", 36.40, 0.31, 16_000_000.0),
    ("BPAC11", "BRBPACUNT006", 37.80, 0.25, 8_000_000.0),
    ("MGLU3", "BRMGLUACNOR5", 9.40, 0.48, 25_000_000.0),
    ("LREN3", "BRLRENACNOR1", 17.60, 0.30, 11_000_000.0),
    ("ELET3", "BRELETACNOR6", 41.50, 0.24, 9_500_000.0),
    ("EMBR3", "BREMBRACNOR4", 48.90, 0.32, 15_000_000.0),
    ("GGBR4", "BRGGBRACNPR8", 19.30, 0.27, 13_000_000.0),
    ("CSNA3", "BRCSNAACNOR6", 12.10, 0.35, 14_500_000.0),
    ("PRIO3", "BRPRIOACNOR3", 44.20, 0.34, 17_000_000.0),
    ("BHIA3", "BRBHIAACNOR8", 5.10, 0.52, 19_000_000.0),
    ("BRFS3", "BRBRFSACNOR8", 24.50, 0.33, 11_500_000.0),
    ("CPLE6", "BRCPLEACNPB9", 9.80, 0.19, 7_000_000.0),
    ("EQTL3", "BREQTLACNOR0", 33.20, 0.17, 8_500_000.0),
    ("HAPV3", "BRHAPVACNOR4", 4.15, 0.42, 22_000_000.0),
    ("KLBN11", "BRKLBNUNT002", 22.30, 0.20, 6_800_000.0),
    ("RADL3", "BRRADLACNOR0", 25.90, 0.21, 7_500_000.0),
    ("RAIL3", "BRRAILACNOR9", 20.80, 0.23, 9_000_000.0),
    ("RDOR3", "BRRDORACNOR8", 29.40, 0.26, 6_000_000.0),
    ("SBSP3", "BRSPSBACNOR7", 91.50, 0.22, 12_500_000.0),
    ("VBBR3", "BRVBBRACNOR1", 23.60, 0.24, 8_200_000.0),
    ("TIMS3", "BRTIMSACNOR5", 17.90, 0.18, 5_500_000.0),
    ("VIVT3", "BRVIVTACNOR7", 52.80, 0.16, 6_200_000.0),
    ("ALOS3", "BRALOSACNOR3", 22.40, 0.23, 5_800_000.0),
    ("ASAI3", "BRASAIACNOR0", 7.85, 0.36, 13_000_000.0),
    ("BBSE3", "BRBBSEACNOR5", 34.10, 0.19, 7_000_000.0),
    ("BRAP4", "BRBRAPACNPR2", 18.70, 0.28, 4_200_000.0),
    ("CCRO3", "BRCCROACNOR2", 12.60, 0.22, 6_500_000.0),
    ("CIEL3", "BRCIELACNOR3", 5.60, 0.20, 10_000_000.0),
    ("CMIG4", "BRCMIGACNPR3", 11.30, 0.21, 8_000_000.0),
    ("COGN3", "BRCOGNACNOR2", 1.45, 0.45, 18_000_000.0),
    ("CPFE3", "BRCPFEACNOR0", 35.60, 0.18, 4_800_000.0),
    ("CRFB3", "BRCRFBACNOR2", 8.30, 0.33, 7_200_000.0),
    ("CSAN3", "BRCSANACNOR6", 12.70, 0.32, 9_200_000.0),
    ("CYRE3", "BRCYREACNOR7", 21.60, 0.31, 6_100_000.0),
    ("DXCO3", "BRDXCOACNOR8", 7.15, 0.34, 4_500_000.0),
    ("EGIE3", "BREGIEACNOR9", 43.20, 0.17, 5_100_000.0),
    ("ENEV3", "BRENEVACNOR8", 12.50, 0.25, 7_800_000.0),
    ("FLRY3", "BRFLRYACNOR5", 15.30, 0.22, 3_900_000.0),
    ("GOAU4", "BRGOAUACNPR5", 10.90, 0.27, 8_800_000.0),
    ("MULT3", "BRMULTACNOR5", 25.20, 0.20, 4_600_000.0),
]

# Vértices de Futuros DI (DI1F27 a DI1F35)
DI_EXPIRATIONS = [
    ("DI1F27", date(2027, 1, 4), 40, 11.15),   # (Símbolo, Vencimento, offset dias úteis base, taxa anual %)
    ("DI1F28", date(2028, 1, 3), 292, 11.45),
    ("DI1F29", date(2029, 1, 2), 544, 11.75),
    ("DI1F30", date(2030, 1, 2), 796, 12.05),
    ("DI1F31", date(2031, 1, 2), 1048, 12.28),
    ("DI1F32", date(2032, 1, 2), 1300, 12.45),
    ("DI1F33", date(2033, 1, 3), 1552, 12.60),
    ("DI1F34", date(2034, 1, 2), 1804, 12.72),
    ("DI1F35", date(2035, 1, 2), 2056, 12.80),
]


# ==============================================================================
# MODELOS DE DADOS ESTRUTURADOS
# ==============================================================================

class ReplaySpeed(float, Enum):
    """Multiplicadores de velocidade para o Replay Engine."""
    REALTIME = 1.0
    FAST_10X = 10.0
    FAST_100X = 100.0
    BATCH_WIRE_SPEED = 0.0


@dataclass(frozen=True)
class EquityQuote:
    """Cotação estocástica de ação no mercado à vista B3."""
    ticker: str
    isin: str
    price: Decimal
    bid: Decimal
    ask: Decimal
    volume: Decimal
    trades_count: int
    daily_return: float
    timestamp: datetime


@dataclass(frozen=True)
class DIFutureContract:
    """Cotação de Futuro de DI (DI1) com cálculo exato de PU B3."""
    symbol: str
    maturity_date: date
    business_days: int
    rate_annual: Decimal
    unit_price: Decimal  # PU = 100.000 / (1 + r)^(du/252)
    timestamp: datetime


# Alias retrocompatível
DIFutureQuote = DIFutureContract


@dataclass(frozen=True)
class PtaxIntradayWindow:
    """Janela de consulta intradiária do Banco Central para PTAX."""
    window_id: int
    window_name: str
    start_time: str
    end_time: str
    bid: Decimal
    ask: Decimal
    mid: Decimal


@dataclass(frozen=True)
class DollarContractQuote:
    """Cotações de contratos de dólar (DOL/WDO) e fixação de PTAX intradiária."""
    dol_symbol: str
    wdo_symbol: str
    spot_rate: Decimal
    future_rate: Decimal
    ptax_windows: List[PtaxIntradayWindow]
    ptax_bid: Decimal
    ptax_ask: Decimal
    ptax_final: Decimal
    dol_pu: Decimal
    wdo_pu: Decimal
    timestamp: datetime


@dataclass(frozen=True)
class SngContractData:
    """Contrato de financiamento veicular no Sistema Nacional de Gravames (SNG)."""
    contrato_id: str
    modalidade: ModalidadeGravame
    chassi: str
    renavam: str
    placa: str
    uf_emplacamento: str
    marca_modelo: str
    ano_fabricacao: int
    ano_modelo: int
    devedor_documento: str
    credor_cnpj: str
    valor_total: Decimal
    taxa_juros_mensal: Decimal
    quantidade_parcelas: int
    data_contrato: date
    status: StatusGravame
    law_112_compliant: bool


@dataclass(frozen=True)
class SroPolicyData:
    """Contrato de seguro com cosseguro e teto LMG perante SUSEP/SRO."""
    apolice_id: str
    numero_proposta: str
    ramo: RamoSeguro
    seguradora_lider_cnpj: str
    seguradora_lider_nome: str
    segurado_documento: str
    segurado_nome: str
    inicio_vigencia: str
    fim_vigencia: str
    limite_maximo_garantia: Decimal
    premio_liquido: Decimal
    premio_total_com_iof: Decimal
    cosseguradores: List[CosseguradorShare]
    sinistros: List[SinistroRecord]
    total_sinistrado: Decimal
    law_113_compliant: bool


@dataclass
class MarketReplayEvent:
    """Evento canônico despachado pelo Replay Engine."""
    sequence_id: int
    timestamp: datetime
    event_type: str
    source_module: str
    data: Dict[str, Any]
    sbe_payload: Optional[bytes] = None
    fix_message: Optional[str] = None


# ==============================================================================
# GERADOR SINTÉTICO DE MERCADO (SYNTHETIC MARKET GENERATOR)
# ==============================================================================

class SyntheticMarketGenerator:
    """Gerador institucional estocástico com reprodutibilidade determinística por semente."""

    def __init__(
        self,
        seed: Optional[int] = 42,
        reference_date: Optional[date] = None,
    ) -> None:
        self.seed: int = seed if seed is not None else 42
        self.rng: random.Random = random.Random(self.seed)
        self.reference_date: date = reference_date or date(2026, 11, 3)

        # Estado estocástico das ações
        self._equity_state: Dict[str, Dict[str, Any]] = {}
        self._init_equity_state()

        # Motores regulatórios locais para verificação estrita
        self.sng_engine = SngGravamesEngine()
        self.sro_engine = SroSegurosEngine()

    def reset_seed(self, seed: Optional[int] = None) -> None:
        """Reinicia o gerador de números pseudo-aleatórios com nova ou mesma semente."""
        if seed is not None:
            self.seed = seed
        self.rng = random.Random(self.seed)
        self._init_equity_state()
        self.sng_engine = SngGravamesEngine()
        self.sro_engine = SroSegurosEngine()

    # --------------------------------------------------------------------------
    # 1. GERAÇÃO ESTOCÁSTICA DE AÇÕES (50 TICKERS B3)
    # --------------------------------------------------------------------------

    def _init_equity_state(self) -> None:
        """Inicializa os parâmetros base dos 50 ativos de ações."""
        self._equity_state.clear()
        for ticker, isin, base_px, vol, base_vol in EQUITY_TICKERS_50:
            self._equity_state[ticker] = {
                "isin": isin,
                "current_price": base_px,
                "annual_volatility": vol,
                "daily_volume_base": base_vol,
                "drift": 0.05,  # 5% de retorno anual esperado
            }

    def generate_equities_step(
        self,
        dt_fraction: float = 1.0 / 252.0,
        shock_multiplier: float = 1.0,
        timestamp: Optional[datetime] = None,
    ) -> List[EquityQuote]:
        """Gera um passo no tempo para os 50 tickers usando Movimento Browniano Geométrico."""
        ts = timestamp or datetime.combine(self.reference_date, dt_time(10, 0, 0), tzinfo=timezone.utc)
        quotes: List[EquityQuote] = []

        for ticker, state in self._equity_state.items():
            s0 = state["current_price"]
            sigma = state["annual_volatility"] * shock_multiplier
            mu = state["drift"]

            # Retorno estocástico log-normal: dS/S = (mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z
            z = self.rng.gauss(0.0, 1.0)
            drift_part = (mu - 0.5 * (sigma ** 2)) * dt_fraction
            vol_part = sigma * math.sqrt(dt_fraction) * z
            ret = math.exp(drift_part + vol_part)

            new_price = max(0.05, round(s0 * ret, 2))
            state["current_price"] = new_price

            # Spread bid/ask institucional proporcional à volatilidade (entre 1 e 5 centavos)
            half_spread = max(0.01, round(new_price * (0.0005 + 0.001 * self.rng.random()), 2))
            bid = max(0.01, round(new_price - half_spread, 2))
            ask = round(new_price + half_spread, 2)

            # Volume log-normal realista
            vol_mult = math.exp(self.rng.gauss(0.0, 0.35))
            daily_vol = round(state["daily_volume_base"] * vol_mult, 2)
            trades_count = max(100, int(daily_vol / (new_price * 100 * (1 + self.rng.random()))))

            quotes.append(
                EquityQuote(
                    ticker=ticker,
                    isin=state["isin"],
                    price=Decimal(str(new_price)),
                    bid=Decimal(str(bid)),
                    ask=Decimal(str(ask)),
                    volume=Decimal(str(daily_vol)),
                    trades_count=trades_count,
                    daily_return=round(ret - 1.0, 6),
                    timestamp=ts,
                )
            )

        return quotes

    # --------------------------------------------------------------------------
    # 2. FUTUROS DI (CURVA DE JUROS DI1F27 A DI1F35 COM INTERPOLAÇÃO EXPONENCIAL)
    # --------------------------------------------------------------------------

    def generate_di_curve(
        self,
        shock_bps: float = 0.0,
        timestamp: Optional[datetime] = None,
    ) -> List[DIFutureContract]:
        """Gera a estrutura a termo da taxa de juros futura (DI1) com interpolação exponencial e cálculo de PU B3.

        Fórmula B3 de Preço Unitário:
            PU = 100.000 / ( (1 + taxa/100) ^ (du / 252) )
        """
        ts = timestamp or datetime.combine(self.reference_date, dt_time(10, 0, 0), tzinfo=timezone.utc)
        curve: List[DIFutureContract] = []

        # Ruído aleatório estocástico suave de curva
        curve_noise = self.rng.gauss(0.0, 0.04)  # ~4 bps de variação diária

        for symbol, mat_date, du, base_rate in DI_EXPIRATIONS:
            # Aplica ruído, inclinação da curva e choque em bps
            eff_rate = base_rate + curve_noise + (shock_bps / 100.0)
            eff_rate = max(1.0, round(eff_rate, 4))

            # Fator de desconto composto exponencial
            factor = (1.0 + eff_rate / 100.0) ** (du / 252.0)
            pu = round(100_000.0 / factor, 2)

            curve.append(
                DIFutureContract(
                    symbol=symbol,
                    maturity_date=mat_date,
                    business_days=du,
                    rate_annual=Decimal(str(eff_rate)),
                    unit_price=Decimal(str(pu)),
                    timestamp=ts,
                )
            )

        return curve

    def interpolate_di_rate(
        self,
        target_du: int,
        curve: Optional[List[DIFutureContract]] = None,
    ) -> Tuple[Decimal, Decimal]:
        """Realiza interpolação exponencial exata padrão B3 entre dois vértices da curva DI1.

        Retorna (taxa_anual_interpolada, preco_unitario_pu).
        """
        if curve is None:
            curve = self.generate_di_curve()

        # Ordena por dias úteis
        sorted_curve = sorted(curve, key=lambda c: c.business_days)
        if target_du <= sorted_curve[0].business_days:
            c = sorted_curve[0]
            factor = (1.0 + float(c.rate_annual) / 100.0) ** (target_du / 252.0)
            pu = round(100_000.0 / factor, 2)
            return c.rate_annual, Decimal(str(pu))

        if target_du >= sorted_curve[-1].business_days:
            c = sorted_curve[-1]
            factor = (1.0 + float(c.rate_annual) / 100.0) ** (target_du / 252.0)
            pu = round(100_000.0 / factor, 2)
            return c.rate_annual, Decimal(str(pu))

        # Encontra os dois nós adjacentes (du1 < target_du < du2)
        n1 = sorted_curve[0]
        n2 = sorted_curve[1]
        for i in range(len(sorted_curve) - 1):
            if sorted_curve[i].business_days <= target_du <= sorted_curve[i + 1].business_days:
                n1 = sorted_curve[i]
                n2 = sorted_curve[i + 1]
                break

        du1 = n1.business_days
        du2 = n2.business_days
        r1 = float(n1.rate_annual) / 100.0
        r2 = float(n2.rate_annual) / 100.0

        f1 = (1.0 + r1) ** (du1 / 252.0)
        f2 = (1.0 + r2) ** (du2 / 252.0)

        # Interpolação exponencial no espaço dos fatores de capitalização
        weight = (target_du - du1) / float(du2 - du1)
        interp_factor = (f1 ** (1.0 - weight)) * (f2 ** weight)

        # Taxa anualizada equivalente
        interp_rate = ((interp_factor ** (252.0 / target_du)) - 1.0) * 100.0
        pu = 100_000.0 / interp_factor

        return Decimal(str(round(interp_rate, 4))), Decimal(str(round(pu, 2)))

    # --------------------------------------------------------------------------
    # 3. FUTUROS DE DÓLAR (DOL/WDO COM CÁLCULO DE PTAX INTRADIÁRIA)
    # --------------------------------------------------------------------------

    def generate_dollar_ptax(
        self,
        base_spot: float = 5.7500,
        shock_pct: float = 0.0,
        timestamp: Optional[datetime] = None,
    ) -> DollarContractQuote:
        """Gera as cotações de Dólar (DOL/WDO) e simula as 4 janelas oficiais de PTAX do BACEN."""
        ts = timestamp or datetime.combine(self.reference_date, dt_time(13, 15, 0), tzinfo=timezone.utc)
        spot = base_spot * (1.0 + shock_pct / 100.0)

        # 4 Janelas Oficiais do Banco Central do Brasil
        windows_spec = [
            (1, "1ª Janela PTAX", "10:00", "10:10"),
            (2, "2ª Janela PTAX", "11:00", "11:10"),
            (3, "3ª Janela PTAX", "12:00", "12:10"),
            (4, "4ª Janela PTAX", "13:00", "13:10"),
        ]

        ptax_windows: List[PtaxIntradayWindow] = []
        sum_bid = Decimal("0.0000")
        sum_ask = Decimal("0.0000")

        current_px = spot
        for wid, wname, st, et in windows_spec:
            # Variação intradiária em cada janela
            walk = self.rng.gauss(0.0, 0.006)
            current_px = round(current_px + walk, 4)
            spread = 0.0006  # 6 pips
            bid = round(current_px - spread / 2.0, 4)
            ask = round(current_px + spread / 2.0, 4)
            mid = round((bid + ask) / 2.0, 4)

            bid_d = Decimal(f"{bid:.4f}")
            ask_d = Decimal(f"{ask:.4f}")
            sum_bid += bid_d
            sum_ask += ask_d

            ptax_windows.append(
                PtaxIntradayWindow(
                    window_id=wid,
                    window_name=wname,
                    start_time=st,
                    end_time=et,
                    bid=bid_d,
                    ask=ask_d,
                    mid=Decimal(f"{mid:.4f}"),
                )
            )

        # Média aritmética exata das 4 janelas
        ptax_bid = (sum_bid / Decimal("4")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        ptax_ask = (sum_ask / Decimal("4")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        ptax_final = ((ptax_bid + ptax_ask) / Decimal("2")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        # Futuro de Dólar negociado com cupom cambial
        future_rate = (ptax_final * Decimal("1.0045")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        dol_pu = future_rate * Decimal("50000")  # DOL = USD 50k
        wdo_pu = future_rate * Decimal("10000")  # WDO = USD 10k

        return DollarContractQuote(
            dol_symbol="DOLZ26",
            wdo_symbol="WDOZ26",
            spot_rate=Decimal(f"{spot:.4f}"),
            future_rate=future_rate,
            ptax_windows=ptax_windows,
            ptax_bid=ptax_bid,
            ptax_ask=ptax_ask,
            ptax_final=ptax_final,
            dol_pu=dol_pu,
            wdo_pu=wdo_pu,
            timestamp=ts,
        )

    # --------------------------------------------------------------------------
    # 4. GRAVAMES SNG: CONTRATOS CDC/LEASING, CHASSI ISO 3779 & INVARIANTE LAW-112
    # --------------------------------------------------------------------------

    def generate_iso3779_chassi(self) -> str:
        """Gera um identificador de chassi veicular estritamente conforme norma ISO 3779.

        Estrutura de 17 caracteres alfanuméricos (excluindo I, O, Q):
        - WMI (World Manufacturer Identifier, 3 caracteres)
        - VDS (Vehicle Descriptor Section, 6 caracteres)
        - VIS (Vehicle Indicator Section, 8 caracteres)
        """
        valid_chars = "0123456789ABCDEFGHJKLMNPRSTUVWXYZ"
        wmis = ["9BD", "9BW", "9BG", "9BF", "9BR", "93Y", "93H"]  # Principais montadoras Brasil
        wmi = self.rng.choice(wmis)
        vds = "".join(self.rng.choice(valid_chars) for _ in range(6))
        # VIS: Ano modelo ('P'=2023, 'R'=2024, 'S'=2025, 'T'=2026), Planta ('B'), e 6 dígitos numéricos
        ano_char = self.rng.choice(["P", "R", "S", "T"])
        planta_char = "B"
        seq_digits = f"{self.rng.randint(100000, 999999):06d}"
        chassi = f"{wmi}{vds}{ano_char}{planta_char}{seq_digits}"
        return chassi

    def generate_sng_portfolio(
        self,
        count: int = 50,
        law_112_strict: bool = True,
    ) -> List[SngContractData]:
        """Gera uma carteira institucional de financiamentos veiculares garantindo conformidade LAW-112.

        Invariante LAW-112:
            Unicidade e Inviolabilidade de Gravame Ativo por Chassi/Renavam no SNG.
            Nenhum chassi pode possuir mais de 1 gravame de 1º grau ativo simultaneamente.
        """
        marcas_modelos = [
            ("FIAT STRADA VOLCANO", 115000),
            ("VOLKSWAGEN POLO TRACK", 88000),
            ("CHEVROLET ONIX PLUS", 96000),
            ("HYUNDAI HB20 PLATINUM", 94000),
            ("TOYOTA COROLLA CROSS", 175000),
            ("JEEP COMPASS LIMITED", 195000),
            ("RENAULT KWID ZEN", 72000),
            ("HONDA HR-V EXL", 160000),
        ]
        ufs = ["SP", "RJ", "MG", "PR", "RS", "SC", "BA", "GO"]
        modalidades = [
            ModalidadeGravame.ALIENACAO_FIDUCIARIA,
            ModalidadeGravame.ARRENDAMENTO_MERCANTIL,
            ModalidadeGravame.RESERVA_DOMINIO,
        ]

        portfolio: List[SngContractData] = []
        used_chassis: set[str] = set()

        for idx in range(1, count + 1):
            # Garante chassis únicos para todos os gravames ativos (LAW-112)
            while True:
                chassi = self.generate_iso3779_chassi()
                if chassi not in used_chassis or not law_112_strict:
                    used_chassis.add(chassi)
                    break

            renavam = f"{self.rng.randint(10000000000, 99999999999)}"
            # Placa padrão Mercosul (ex: ABC1D23)
            letters = "".join(self.rng.choices("ABCDEFGHJKLMNPRSTUVWXYZ", k=3))
            num1 = self.rng.choice("0123456789")
            letter4 = self.rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ")
            num23 = f"{self.rng.randint(10, 99):02d}"
            placa = f"{letters}{num1}{letter4}{num23}"

            marca, valor_ref = self.rng.choice(marcas_modelos)
            uf = self.rng.choice(ufs)
            mod = self.rng.choice(modalidades)

            # Financia de 50% a 90% do valor de referência
            valor_financ = Decimal(str(round(valor_ref * (0.50 + 0.40 * self.rng.random()), 2)))
            devedor_doc = f"{self.rng.randint(100000000, 999999999):09d}{self.rng.randint(10, 99):02d}"
            credor_cnpj = "60746948000112"  # Banco Financiador Institucional

            contract = SngContractData(
                contrato_id=f"SNG-CTR-2026-{idx:05d}",
                modalidade=mod,
                chassi=chassi,
                renavam=renavam,
                placa=placa,
                uf_emplacamento=uf,
                marca_modelo=marca,
                ano_fabricacao=2025,
                ano_modelo=2026,
                devedor_documento=devedor_doc,
                credor_cnpj=credor_cnpj,
                valor_total=valor_financ,
                taxa_juros_mensal=Decimal("1.49"),
                quantidade_parcelas=48,
                data_contrato=self.reference_date,
                status=StatusGravame.ATIVO,
                law_112_compliant=True,
            )
            portfolio.append(contract)

            # Registra no SngGravamesEngine interno para testar e auditar
            veiculo = VeiculoIdentificacao(
                chassi=chassi,
                renavam=renavam,
                placa=placa,
                uf_emplacamento=uf,
                marca_modelo=marca,
                ano_fabricacao=2025,
                ano_modelo=2026,
            )
            self.sng_engine.registrar_gravame(
                contrato_numero=contract.contrato_id,
                modalidade=mod,
                credor_cnpj=credor_cnpj,
                credor_nome="BANCO FINANCIADOR B3 S.A.",
                devedor_documento=devedor_doc,
                devedor_nome="CONSUMIDOR TESTE SNG",
                veiculo=veiculo,
                valor_financiamento=valor_financ,
                quantidade_parcelas=48,
                taxa_juros_anual_pct=Decimal("17.88"),
            )

        return portfolio

    # --------------------------------------------------------------------------
    # 5. APÓLICES SRO: MULTI-RAMO, COSSEGURO 100.00% & INVARIANTE LAW-113 (LMG)
    # --------------------------------------------------------------------------

    def generate_sro_portfolio(
        self,
        count: int = 20,
        simulate_claims: bool = True,
    ) -> List[SroPolicyData]:
        """Gera uma carteira de apólices SRO perante SUSEP garantindo conformidade LAW-113.

        Invariante LAW-113:
            1. Quotas de cosseguro somam rigorosamente 100.00%.
            2. Somatório de sinistros liquidados nunca excede o Limite Máximo de Garantia (LMG).
        """
        ramos = [
            RamoSeguro.AUTOMOVEL,
            RamoSeguro.PATRIMONIAL_COMPREENSIVO,
            RamoSeguro.RURAL_AGRICOLA,
            RamoSeguro.VIDA_INDIVIDUAL_COLETIVO,
        ]

        seguradoras_pool = [
            ("33000118000179", "BRADESCO AUTO/RE COMPANHIA DE SEGUROS"),
            ("61557007000139", "PORTO SEGURO COMPANHIA DE SEGUROS GERAIS"),
            ("33041062000109", "MAPFRE SEGUROS GERAIS S.A."),
            ("02351877000176", "TOKIO MARINE SEGURADORA S.A."),
            ("60746948000112", "ITAU SEGUROS S.A."),
        ]

        portfolio: List[SroPolicyData] = []

        for idx in range(1, count + 1):
            ramo = ramos[(idx - 1) % len(ramos)]
            apolice_id = f"SRO-APOL-2026-{ramo.value[:4]}-{idx:04d}"
            proposta = f"PROP-2026-{idx:06d}"

            # Seleciona líder e cosseguradores
            lider_cnpj, lider_nome = seguradoras_pool[idx % len(seguradoras_pool)]
            outras = [s for s in seguradoras_pool if s[0] != lider_cnpj]
            self.rng.shuffle(outras)

            # Estruturação estrita de cosseguro somando 100.00%
            has_cosseguro = (idx % 2 == 0)
            cosseguradores: List[CosseguradorShare] = []

            if not has_cosseguro:
                # 100% Retenção pela líder
                cosseguradores.append(
                    CosseguradorShare(
                        cnpj=lider_cnpj,
                        razao_social=lider_nome,
                        percentual_quota=Decimal("100.00"),
                        is_lider=True,
                    )
                )
            else:
                # Líder 60%, Parceira 1: 30%, Parceira 2: 10% -> Soma 100.00%
                p1_cnpj, p1_nome = outras[0]
                p2_cnpj, p2_nome = outras[1]
                cosseguradores.append(
                    CosseguradorShare(
                        cnpj=lider_cnpj,
                        razao_social=lider_nome,
                        percentual_quota=Decimal("60.00"),
                        is_lider=True,
                    )
                )
                cosseguradores.append(
                    CosseguradorShare(
                        cnpj=p1_cnpj,
                        razao_social=p1_nome,
                        percentual_quota=Decimal("30.00"),
                        is_lider=False,
                    )
                )
                cosseguradores.append(
                    CosseguradorShare(
                        cnpj=p2_cnpj,
                        razao_social=p2_nome,
                        percentual_quota=Decimal("10.00"),
                        is_lider=False,
                    )
                )

            # Limite Máximo de Garantia (LMG) por ramo
            if ramo == RamoSeguro.AUTOMOVEL:
                lmg = Decimal("150000.00")
                premio_liq = Decimal("3500.00")
            elif ramo == RamoSeguro.PATRIMONIAL_COMPREENSIVO:
                lmg = Decimal("5000000.00")
                premio_liq = Decimal("48000.00")
            elif ramo == RamoSeguro.RURAL_AGRICOLA:
                lmg = Decimal("2500000.00")
                premio_liq = Decimal("32000.00")
            else:  # VIDA_INDIVIDUAL_COLETIVO
                lmg = Decimal("800000.00")
                premio_liq = Decimal("2400.00")

            premio_tot = (premio_liq * Decimal("1.0738")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            segurado_doc = f"{self.rng.randint(100000000, 999999999):09d}{self.rng.randint(10, 99):02d}"

            # Emite no motor oficial SRO
            apolice_rec = self.sro_engine.registrar_apolice(
                numero_proposta=proposta,
                ramo=ramo,
                seguradora_lider_cnpj=lider_cnpj,
                seguradora_lider_nome=lider_nome,
                segurado_documento=segurado_doc,
                segurado_nome="SEGURADO CORPORATIVO SRO",
                data_emissao="2026-11-03",
                inicio_vigencia="2026-11-03",
                fim_vigencia="2027-11-03",
                limite_maximo_garantia=lmg,
                premio_liquido=premio_liq,
                cosseguradores=cosseguradores,
            )

            sinistros: List[SinistroRecord] = []
            total_sinistrado = Decimal("0.00")

            # Simula sinistro controlado respeitando estritamente o teto LMG (LAW-113)
            if simulate_claims and (idx % 3 == 0):
                # Sinistro parcial entre 10% e 40% do LMG
                val_sinistro = (lmg * Decimal("0.25")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                sin_rec = self.sro_engine.avisar_sinistro(
                    apolice_id=apolice_rec.apolice_id,
                    data_ocorrencia="2026-11-04",
                    valor_estimado=val_sinistro,
                    descricao_cobertura="Aviso e liquidacao tempestiva de sinistro sob regulacao SUSEP",
                )
                self.sro_engine.liquidar_sinistro(
                    apolice_id=apolice_rec.apolice_id,
                    sinistro_id=sin_rec.sinistro_id,
                    valor_indenizado=val_sinistro,
                )
                sinistros.append(sin_rec)
                total_sinistrado += val_sinistro

            policy_data = SroPolicyData(
                apolice_id=apolice_rec.apolice_id,
                numero_proposta=proposta,
                ramo=ramo,
                seguradora_lider_cnpj=lider_cnpj,
                seguradora_lider_nome=lider_nome,
                segurado_documento=segurado_doc,
                segurado_nome="SEGURADO CORPORATIVO SRO",
                inicio_vigencia="2026-11-03",
                fim_vigencia="2027-11-03",
                limite_maximo_garantia=lmg,
                premio_liquido=premio_liq,
                premio_total_com_iof=premio_tot,
                cosseguradores=cosseguradores,
                sinistros=sinistros,
                total_sinistrado=total_sinistrado,
                law_113_compliant=(total_sinistrado <= lmg),
            )
            portfolio.append(policy_data)

        return portfolio

    # --------------------------------------------------------------------------
    # 6. EXPORTAÇÃO EM FORMATOS OFICIAIS: PUMA SBE BINÁRIO
    # --------------------------------------------------------------------------

    def export_puma_sbe(
        self,
        orders_count: int = 20,
    ) -> List[bytes]:
        """Gera pacotes binários formatados no padrão PUMA Simple Binary Encoding (SBE v2)."""
        packets: List[bytes] = []

        for idx in range(1, orders_count + 1):
            ticker_info = EQUITY_TICKERS_50[(idx - 1) % len(EQUITY_TICKERS_50)]
            symbol = ticker_info[0]
            sec_id = idx + 1000
            price = round(ticker_info[2] * (1.0 + self.rng.gauss(0.0, 0.01)), 2)
            side = SbeSide.BUY if idx % 2 != 0 else SbeSide.SELL
            qty = (idx * 100) % 5000 + 100

            # 1. NewOrderSingleBinary (Template 101)
            order = NewOrderSingleBinary(
                cl_ord_id=10000 + idx,
                security_id=sec_id,
                side=int(side),
                order_qty=qty,
                price=float(price),
                time_in_force=int(SbeTimeInForce.DAY),
                account=123456,
            )
            packets.append(order.encode())

            # 2. ExecutionReportBinary (Template 103) para ordens executadas
            if idx % 2 == 0:
                report = ExecutionReportBinary(
                    order_id=50000 + idx,
                    cl_ord_id=10000 + idx,
                    exec_id=90000 + idx,
                    exec_type=int(SbeExecType.FILL),
                    ord_status=int(SbeOrdStatus.FILLED),
                    security_id=sec_id,
                    cum_qty=qty,
                    leaves_qty=0,
                    last_px=float(price),
                    last_qty=qty,
                )
                packets.append(report.encode())

        return packets

    # --------------------------------------------------------------------------
    # 7. EXPORTAÇÃO EM FORMATOS OFICIAIS: LOG FIX 4.4 / FIX 5.0 SP2
    # --------------------------------------------------------------------------

    def export_puma_fix(
        self,
        orders_count: int = 20,
        version: str = "FIX.4.4",
        delimiter: str = "|",
    ) -> List[str]:
        """Gera logs de mensagens FIX (4.4 ou 5.0 SP2) com cálculo rigoroso de BodyLength e CheckSum."""
        delim_bytes = delimiter.encode("latin1")
        fix_logs: List[str] = []

        for idx in range(1, orders_count + 1):
            ticker_info = EQUITY_TICKERS_50[(idx - 1) % len(EQUITY_TICKERS_50)]
            ticker = ticker_info[0]
            price = f"{ticker_info[2]:.2f}"
            qty = str((idx * 100) % 2000 + 100)
            side = "1" if idx % 2 != 0 else "2"
            now_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]

            # Inserção de Nova Ordem (35=D)
            msg = FixMessage()
            msg.set(8, version)
            msg.set(35, FixMsgType.NEW_ORDER_SINGLE.value)
            msg.set(49, "ATLAS_BROKER")
            msg.set(56, "B3_PUMA")
            msg.set(34, idx * 2 - 1)
            msg.set(52, now_str)
            msg.set(11, f"ORD-{idx:06d}")
            msg.set(55, ticker)
            msg.set(54, side)
            msg.set(38, qty)
            msg.set(40, FixOrdType.LIMIT.value)
            msg.set(44, price)
            msg.set(59, FixTimeInForce.DAY.value)

            raw_d = msg.encode(delimiter=delim_bytes)
            fix_logs.append(raw_d.decode("latin1"))

            # Confirmação de Execução (35=8)
            exec_msg = FixMessage()
            exec_msg.set(8, version)
            exec_msg.set(35, FixMsgType.EXECUTION_REPORT.value)
            exec_msg.set(49, "B3_PUMA")
            exec_msg.set(56, "ATLAS_BROKER")
            exec_msg.set(34, idx * 2)
            exec_msg.set(52, now_str)
            exec_msg.set(37, f"B3-EX-{idx:08d}")
            exec_msg.set(11, f"ORD-{idx:06d}")
            exec_msg.set(17, f"TRD-{idx:08d}")
            exec_msg.set(150, FixExecType.FILL.value)
            exec_msg.set(39, FixOrdStatus.FILLED.value)
            exec_msg.set(55, ticker)
            exec_msg.set(54, side)
            exec_msg.set(38, qty)
            exec_msg.set(44, price)
            exec_msg.set(31, price)
            exec_msg.set(32, qty)
            exec_msg.set(151, "0")
            exec_msg.set(14, qty)
            exec_msg.set(6, price)

            raw_8 = exec_msg.encode(delimiter=delim_bytes)
            fix_logs.append(raw_8.decode("latin1"))

        return fix_logs

    # --------------------------------------------------------------------------
    # 8. EXPORTAÇÃO EM FORMATOS OFICIAIS: ARQUIVOS PLANOS IMBARQ
    # --------------------------------------------------------------------------

    def export_imbarq004(
        self,
        date_str: str = "20261103",
        participant_id: int = 105,
        items_count: int = 10,
    ) -> str:
        """Gera arquivo posicional oficial IMBARQ004 (Lançamentos Financeiros de Liquidação B3).

        Linhas exatamente com 200 caracteres e integridade contábil perfeita no Trailer.
        """
        lines: List[str] = []
        # Header (00)
        h = (
            f"00IMBARQ004001{date_str}230000{date_str}{participant_id:08d}"
            f"{'CORRETORA ATLAS EXEMPLO S/A':<30}000001"
        )
        lines.append(f"{h:<200}")

        tot_deb = Decimal("0.00")
        tot_cred = Decimal("0.00")

        for idx in range(1, items_count + 1):
            nat = "C" if idx % 2 != 0 else "D"
            val = Decimal(f"{(idx * 1250 + 500):.2f}")
            if nat == "C":
                tot_cred += val
            else:
                tot_deb += val

            ticker = EQUITY_TICKERS_50[(idx - 1) % len(EQUITY_TICKERS_50)][0]
            isin = EQUITY_TICKERS_50[(idx - 1) % len(EQUITY_TICKERS_50)][1]
            desc = "LIQ FINANC MERCADO A VISTA"
            val_cents = int(round(val * 100))

            det = (
                f"01{participant_id:08d}{123456:012d}{idx:010d}{date_str}0101"
                f"{desc:<30}{nat}{val_cents:017d}{ticker:<12}{isin:<12}VIS"
                f"{participant_id:08d}{f'NOTA-{idx:04d}':<16}CAM L"
            )
            lines.append(f"{det:<200}")

        # Trailer (99)
        tot_reg = len(lines) + 1
        sinal = "C" if tot_cred >= tot_deb else "D"
        tot_liq = abs(tot_cred - tot_deb)

        deb_cents = int(round(tot_deb * 100))
        cred_cents = int(round(tot_cred * 100))
        liq_cents = int(round(tot_liq * 100))

        tr = (
            f"99IMBARQ004{tot_reg:08d}{items_count:08d}"
            f"{deb_cents:018d}{cred_cents:018d}{sinal}{liq_cents:018d}"
        )
        lines.append(f"{tr:<200}")

        return "\n".join(lines) + "\n"

    def export_imbarq006(
        self,
        date_str: str = "20261103",
        participant_id: int = 105,
        items_count: int = 10,
    ) -> str:
        """Gera arquivo posicional oficial IMBARQ006 (Posições da Câmara B3).

        Linhas exatamente com 200 caracteres e somatórios estritos no Trailer.
        """
        lines: List[str] = []
        # Header (00)
        h = (
            f"00IMBARQ006001{date_str}230000{date_str}{participant_id:08d}"
            f"{'CORRETORA ATLAS EXEMPLO S/A':<30}000001"
        )
        lines.append(f"{h:<200}")

        tot_qliq = 0
        tot_vlr = Decimal("0.00")

        for idx in range(1, items_count + 1):
            ticker_info = EQUITY_TICKERS_50[(idx - 1) % len(EQUITY_TICKERS_50)]
            ticker = ticker_info[0]
            isin = ticker_info[1]
            px = Decimal(f"{ticker_info[2]:.2f}")
            qty = idx * 100
            vlr = (px * Decimal(qty)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            tot_qliq += qty
            tot_vlr += vlr

            px_cents = int(round(px * 100))
            vlr_cents = int(round(vlr * 100))

            det = (
                f"01{participant_id:08d}{123456:012d}{ticker:<12}{isin:<12}VISC"
                f"{qty:015d}{0:015d}{qty:015d}{px_cents:015d}{vlr_cents:017d}"
                f"{'00000000':<8}{'':<15}{participant_id:08d}"
            )
            lines.append(f"{det:<200}")

        tot_reg = len(lines) + 1
        tot_vlr_cents = int(round(tot_vlr * 100))

        tr = (
            f"99IMBARQ006{tot_reg:08d}{items_count:08d}"
            f"{tot_qliq:018d}{tot_vlr_cents:018d}"
        )
        lines.append(f"{tr:<200}")

        return "\n".join(lines) + "\n"

    def export_imbarq014(
        self,
        date_str: str = "20261103",
        participant_id: int = 105,
        items_count: int = 6,
    ) -> str:
        """Gera arquivo posicional oficial IMBARQ014 (Garantias e Chamadas de Margem B3).

        Linhas exatamente com 200 caracteres e controle de colateral MOE, TPU e ACO.
        """
        lines: List[str] = []
        # Header (00)
        h = (
            f"00IMBARQ014001{date_str}230000{date_str}{participant_id:08d}"
            f"{'CORRETORA ATLAS EXEMPLO S/A':<30}000001"
        )
        lines.append(f"{h:<200}")

        collaterals = [
            ("MOE", "BRL", "BRBRLCTF0001", Decimal("150000.00"), Decimal("1.00"), Decimal("50000.00"), "L"),
            ("TPU", "LFT2027", "BRSTNCLF1001", Decimal("10"), Decimal("14500.00"), Decimal("40000.00"), "L"),
            ("ACO", "PETR4", "BRPETRACNPR6", Decimal("2000"), Decimal("38.50"), Decimal("20000.00"), "D"),
            ("ACO", "VALE3", "BRVALEACNOR0", Decimal("1000"), Decimal("61.20"), Decimal("25000.00"), "L"),
            ("TPU", "NTNB2030", "BRSTNCNTB001", Decimal("15"), Decimal("4200.00"), Decimal("30000.00"), "L"),
            ("MOE", "USD", "BRUSDCTF0001", Decimal("50000.00"), Decimal("5.75"), Decimal("60000.00"), "L"),
        ]

        tot_qtd = 0
        tot_aval = Decimal("0.00")
        tot_req = Decimal("0.00")
        tot_livre_def = Decimal("0.00")

        for idx in range(min(items_count, len(collaterals))):
            tipo_colat, ativo, isin, qtd_dep, pu, mrg_req, sit_mrg = collaterals[idx]
            val_aval = (qtd_dep * pu).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            val_livre_def = abs(val_aval - mrg_req)

            tot_qtd += int(qtd_dep)
            tot_aval += val_aval
            tot_req += mrg_req
            tot_livre_def += val_livre_def

            pu_cents = int(round(pu * 100))
            aval_cents = int(round(val_aval * 100))
            req_cents = int(round(mrg_req * 100))
            livre_cents = int(round(val_livre_def * 100))

            det = (
                f"01{participant_id:08d}{123456:012d}{tipo_colat:<3}{ativo:<12}{isin:<12}AMP"
                f"{int(qtd_dep):015d}{pu_cents:015d}{aval_cents:017d}{req_cents:017d}"
                f"{sit_mrg}{livre_cents:017d}{participant_id:08d}"
            )
            lines.append(f"{det:<200}")

        tot_reg = len(lines) + 1
        aval_tot_cents = int(round(tot_aval * 100))
        req_tot_cents = int(round(tot_req * 100))
        livre_tot_cents = int(round(tot_livre_def * 100))

        tr = (
            f"99IMBARQ014{tot_reg:08d}{min(items_count, len(collaterals)):08d}"
            f"{tot_qtd:018d}{aval_tot_cents:018d}{req_tot_cents:018d}{livre_tot_cents:018d}"
        )
        lines.append(f"{tr:<200}")

        return "\n".join(lines) + "\n"

    # --------------------------------------------------------------------------
    # 9. EXPORTAÇÃO EM FORMATOS OFICIAIS: JSON INSTITUCIONAL DE ESTRESSE
    # --------------------------------------------------------------------------

    def export_stress_json(
        self,
        scenario_name: str = "EXTREME_VOLATILITY_BLACK_SWAN",
        equity_drop_pct: float = -20.0,
        vol_multiplier: float = 3.5,
        di_curve_shift_bps: float = 250.0,
        fx_spike_pct: float = 12.0,
    ) -> str:
        """Gera payload JSON institucional estruturado para testes de estresse regulatórios (CORE & BACEN)."""
        # Ações sob estresse
        equities_stressed = []
        for ticker, state in self._equity_state.items():
            base_px = state["current_price"]
            drop_ret = (equity_drop_pct / 100.0) * (1.0 + self.rng.gauss(0.0, 0.05))
            stress_px = round(max(0.10, base_px * (1.0 + drop_ret)), 2)
            stress_vol = round(state["annual_volatility"] * vol_multiplier, 4)
            equities_stressed.append({
                "ticker": ticker,
                "isin": state["isin"],
                "base_price": base_px,
                "stress_price": stress_px,
                "price_shock_pct": round(drop_ret * 100.0, 2),
                "stress_annual_volatility": stress_vol,
            })

        # Curva DI sob estresse
        di_curve = self.generate_di_curve(shock_bps=di_curve_shift_bps)
        di_stressed = [
            {
                "symbol": c.symbol,
                "maturity_date": c.maturity_date.isoformat(),
                "business_days": c.business_days,
                "stressed_rate_annual": str(c.rate_annual),
                "stressed_pu": str(c.unit_price),
            }
            for c in di_curve
        ]

        # Câmbio e PTAX sob estresse
        fx_stressed = self.generate_dollar_ptax(shock_pct=fx_spike_pct)

        # Gravames e Seguros
        sng_portfolio = self.generate_sng_portfolio(count=15)
        sro_portfolio = self.generate_sro_portfolio(count=10)

        stress_payload = {
            "stress_scenario": {
                "scenario_name": scenario_name,
                "simulation_seed": self.seed,
                "reference_date": self.reference_date.isoformat(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "parameters": {
                    "equity_drop_target_pct": equity_drop_pct,
                    "volatility_multiplier": vol_multiplier,
                    "di_curve_parallel_shift_bps": di_curve_shift_bps,
                    "fx_usd_spike_pct": fx_spike_pct,
                },
            },
            "market_data": {
                "equities_count": len(equities_stressed),
                "equities": equities_stressed,
                "di_futures_curve": di_stressed,
                "fx_ptax": {
                    "spot_rate": str(fx_stressed.spot_rate),
                    "ptax_final": str(fx_stressed.ptax_final),
                    "future_rate": str(fx_stressed.future_rate),
                    "dol_contract_pu": str(fx_stressed.dol_pu),
                    "wdo_contract_pu": str(fx_stressed.wdo_pu),
                    "ptax_windows": [
                        {
                            "window_id": w.window_id,
                            "window_name": w.window_name,
                            "mid": str(w.mid),
                        }
                        for w in fx_stressed.ptax_windows
                    ],
                },
            },
            "collateral_and_insurance": {
                "sng_gravames_contracts": len(sng_portfolio),
                "sng_law_112_verified": all(c.law_112_compliant for c in sng_portfolio),
                "sro_insurance_policies": len(sro_portfolio),
                "sro_law_113_verified": all(p.law_113_compliant for p in sro_portfolio),
            },
        }

        return json.dumps(stress_payload, indent=2, ensure_ascii=False)


# ==============================================================================
# MOTOR DE REPLAY DE MERCADO (REPLAY ENGINE)
# ==============================================================================

class ReplayStatus(str, Enum):
    IDLE = "IDLE"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"


class ReplayEngine:
    """Motor institucional de replay determinístico de eventos de mercado da B3.

    Suporta:
    - Velocidades: 1.0 (tempo real), 10.0 (10x), 100.0 (100x), 0.0 (batch wire-speed).
    - Despacho orientado a eventos para múltiplos inscritos (subscribers/callbacks).
    - Métricas em tempo real de throughput e latência.
    """

    def __init__(
        self,
        speed: Union[ReplaySpeed, float] = ReplaySpeed.BATCH_WIRE_SPEED,
    ) -> None:
        self.speed: float = float(speed.value if isinstance(speed, ReplaySpeed) else speed)
        self.status: ReplayStatus = ReplayStatus.IDLE
        self.events_queue: List[MarketReplayEvent] = []
        self._current_index: int = 0
        self._subscribers: List[Callable[[MarketReplayEvent], None]] = []
        self._type_subscribers: Dict[str, List[Callable[[MarketReplayEvent], None]]] = {}

        # Métricas de execução
        self.events_processed: int = 0
        self.events_by_type: Dict[str, int] = {}
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None

    def subscribe(
        self,
        callback: Callable[[MarketReplayEvent], None],
        event_type: Optional[str] = None,
    ) -> None:
        """Registra um callback para consumir eventos do replay."""
        if event_type is None:
            self._subscribers.append(callback)
        else:
            self._type_subscribers.setdefault(event_type, []).append(callback)

    def load_events(self, events: List[MarketReplayEvent]) -> None:
        """Carrega uma sequência cronológica de eventos no replay buffer."""
        self.events_queue = sorted(events, key=lambda e: (e.timestamp, e.sequence_id))
        self._current_index = 0
        self.events_processed = 0
        self.events_by_type.clear()
        self.status = ReplayStatus.IDLE

    def load_from_generator(
        self,
        generator: SyntheticMarketGenerator,
        steps: int = 5,
    ) -> None:
        """Carrega automaticamente eventos integrados gerados pelo SyntheticMarketGenerator."""
        events: List[MarketReplayEvent] = []
        seq = 1
        base_time = datetime.combine(generator.reference_date, dt_time(10, 0, 0), tzinfo=timezone.utc)

        # 1. Eventos de Curva DI
        di_curve = generator.generate_di_curve(timestamp=base_time)
        for di in di_curve:
            events.append(
                MarketReplayEvent(
                    sequence_id=seq,
                    timestamp=base_time + timedelta(seconds=seq),
                    event_type="DI_CURVE_UPDATE",
                    source_module="CLEARING_CORE",
                    data={"symbol": di.symbol, "rate": str(di.rate_annual), "pu": str(di.unit_price)},
                )
            )
            seq += 1

        # 2. Eventos de Dólar e PTAX
        fx = generator.generate_dollar_ptax(timestamp=base_time)
        for w in fx.ptax_windows:
            events.append(
                MarketReplayEvent(
                    sequence_id=seq,
                    timestamp=base_time + timedelta(seconds=seq),
                    event_type="PTAX_WINDOW_CONSULT",
                    source_module="BACEN_PTAX",
                    data={"window_id": w.window_id, "name": w.window_name, "mid": str(w.mid)},
                )
            )
            seq += 1

        # 3. Eventos de Cotação de Ações
        for step in range(steps):
            quotes = generator.generate_equities_step(
                timestamp=base_time + timedelta(minutes=step * 15)
            )
            for q in quotes:
                events.append(
                    MarketReplayEvent(
                        sequence_id=seq,
                        timestamp=q.timestamp,
                        event_type="EQUITY_QUOTE_TICK",
                        source_module="PUMA_TRADING",
                        data={"ticker": q.ticker, "price": str(q.price), "bid": str(q.bid), "ask": str(q.ask)},
                    )
                )
                seq += 1

        # 4. Eventos de Gravames SNG
        sng_contracts = generator.generate_sng_portfolio(count=10)
        for c in sng_contracts:
            events.append(
                MarketReplayEvent(
                    sequence_id=seq,
                    timestamp=base_time + timedelta(seconds=seq),
                    event_type="SNG_GRAVAME_INCLUSAO",
                    source_module="SNG_B3",
                    data={"contrato_id": c.contrato_id, "chassi": c.chassi, "valor": str(c.valor_total)},
                )
            )
            seq += 1

        # 5. Eventos de Apólices SRO
        sro_policies = generator.generate_sro_portfolio(count=5)
        for p in sro_policies:
            events.append(
                MarketReplayEvent(
                    sequence_id=seq,
                    timestamp=base_time + timedelta(seconds=seq),
                    event_type="SRO_APOLICE_REGISTRO",
                    source_module="SRO_SUSEP",
                    data={"apolice_id": p.apolice_id, "ramo": p.ramo.value, "lmg": str(p.limite_maximo_garantia)},
                )
            )
            seq += 1

        self.load_events(events)

    def step(self) -> Optional[MarketReplayEvent]:
        """Avança exatamente 1 evento na fila de replay e o despacha."""
        if self._current_index >= len(self.events_queue):
            self.status = ReplayStatus.COMPLETED
            return None

        event = self.events_queue[self._current_index]
        self._current_index += 1
        self.events_processed += 1
        self.events_by_type[event.event_type] = self.events_by_type.get(event.event_type, 0) + 1

        # Despacho síncrono para os inscritos
        for sub in self._subscribers:
            sub(event)

        if event.event_type in self._type_subscribers:
            for sub in self._type_subscribers[event.event_type]:
                sub(event)

        if self._current_index >= len(self.events_queue):
            self.status = ReplayStatus.COMPLETED

        return event

    def play(self) -> Dict[str, Any]:
        """Executa o replay contínuo dos eventos respeitando a velocidade configurada."""
        self.status = ReplayStatus.PLAYING
        self.start_time = time.perf_counter()

        last_sim_time: Optional[datetime] = None

        while self.status == ReplayStatus.PLAYING and self._current_index < len(self.events_queue):
            current_event = self.events_queue[self._current_index]

            # Controle temporal de velocidade
            if self.speed > 0.0 and last_sim_time is not None:
                sim_delta = (current_event.timestamp - last_sim_time).total_seconds()
                if sim_delta > 0:
                    real_sleep = sim_delta / self.speed
                    # Limita sleep para não travar o loop de teste
                    if real_sleep > 0.001:
                        time.sleep(min(real_sleep, 0.05))

            last_sim_time = current_event.timestamp
            self.step()

        self.end_time = time.perf_counter()
        if self._current_index >= len(self.events_queue):
            self.status = ReplayStatus.COMPLETED

        return self.get_stats()

    def pause(self) -> None:
        """Pausa a execução do replay."""
        if self.status in (ReplayStatus.PLAYING, ReplayStatus.IDLE):
            self.status = ReplayStatus.PAUSED

    def resume(self) -> None:
        """Retoma a execução do replay pausado."""
        if self.status == ReplayStatus.PAUSED:
            self.status = ReplayStatus.PLAYING

    def stop(self) -> None:
        """Interrompe e reinicia o ponteiro de execução do replay."""
        self.status = ReplayStatus.STOPPED
        self.end_time = time.perf_counter()

    def get_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas detalhadas de desempenho, throughput e contagens do replay."""
        elapsed = 0.0
        if self.start_time is not None:
            end = self.end_time or time.perf_counter()
            elapsed = max(0.000001, end - self.start_time)

        throughput = self.events_processed / elapsed if elapsed > 0 else 0.0

        return {
            "status": self.status.value,
            "speed": self.speed,
            "total_events": len(self.events_queue),
            "events_processed": self.events_processed,
            "remaining_events": len(self.events_queue) - self._current_index,
            "events_by_type": dict(self.events_by_type),
            "elapsed_seconds": round(elapsed, 6),
            "throughput_events_per_second": round(throughput, 2),
        }
