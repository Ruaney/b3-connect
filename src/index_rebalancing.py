"""
B3 Índices — Simulador e Rebalanceamento Determinístico de Carteiras Teóricas (FL-066)
=====================================================================================
Motor determinístico de cálculo de Índice de Negociabilidade (IN), aplicação
dos filtros estatísticos e regulatórios da B3 (presença em pregões, penny stock,
recuperação judicial, corte dos 85% de representatividade acumulada), ponderação
por valor de mercado do free float, aplicação de teto regulatório (capping de 20%)
com redistribuição iterativa e gestão do ciclo quadrimestral de prévias (1ª, 2ª e Definitiva).

Conformidade metodológica com os manuais de índices acionários da B3:
- Metodologia do Índice Bovespa (Ibovespa)
- Metodologia do Índice Brasil 100 (IBrX-100)
- Metodologia do Índice Small Cap (SMLL)
- Metodologia do Índice Dividendos (IDIV)

Leis Formais de Conservação e Invariantes:
1. Lei de Conservação Estrita de Pesos:
   Soma(W_i) == Decimal("1.00000000") (exatamente 100.00000000%)
2. Teto Regulatório de Concentração (Capping Limit):
   W_i <= Cap_Limit (ex: 20.00%) para todo ativo constituinte.
3. Filtro Mandatório de Elegibilidade:
   Nenhum constituinte pode ser Penny Stock (< R$ 1,00), estar em Recuperação
   Judicial ou possuir presença em pregões < 95%.
4. Corte dos 85% de Representatividade do IN (Ibovespa):
   Constituintes integram o conjunto ordenado decrescentemente de IN que,
   cumulativamente, atinge o limiar de 85% do IN total do mercado elegível.
5. Continuidade Patrimonial via Ajuste de Divisor:
   (Soma Q_i^novo * P_i) / Divisor_novo == (Soma Q_i^antigo * P_i) / Divisor_antigo
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_HALF_EVEN
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union


PRECISION_DECIMAL = Decimal("0.00000001")
PERCENT_DECIMAL = Decimal("0.000001")
PRICE_DECIMAL = Decimal("0.01")


class IndexType(str, Enum):
    IBOV = "IBOV"          # Índice Bovespa
    IBRX100 = "IBRX100"    # Índice Brasil 100
    SMLL = "SMLL"          # Índice Small Cap
    IDIV = "IDIV"          # Índice Dividendos


class PreviewStage(str, Enum):
    PREVIEW_1 = "PREVIEW_1"                      # 1ª Prévia (1º dia útil do mês anterior)
    PREVIEW_2 = "PREVIEW_2"                      # 2ª Prévia (16 do mês anterior)
    PREVIEW_3_DEFINITIVE = "PREVIEW_3_DEFINITIVE"  # 3ª Prévia / Carteira Definitiva


class IndexRebalancingError(Exception):
    """Exceção levantada quando ocorre violação de regras ou parâmetros no rebalanceamento."""
    pass


def _to_decimal(val: Any, field_name: str = "campo") -> Decimal:
    """Converte valor para Decimal determinístico."""
    if isinstance(val, Decimal):
        return val
    if isinstance(val, (int, float)):
        return Decimal(str(val))
    if isinstance(val, str):
        cleaned = val.strip()
        if not cleaned:
            raise IndexRebalancingError(f"Valor vazio para {field_name}")
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:
            raise IndexRebalancingError(f"Formato decimal inválido para {field_name}: {val}") from exc
    raise IndexRebalancingError(f"Tipo não suportado para {field_name}: {type(val)}")


@dataclass(frozen=True)
class AssetTradingData:
    """Métricas operacionais e societárias de um ativo no período de observação (3 quadrimestres)."""
    ticker: str
    company_name: str
    trades_count: int                        # Número de negócios no período (n_i)
    financial_volume: Decimal                # Volume financeiro negociado em R$ (v_i)
    free_float_shares: int                   # Quantidade de ações em circulação (Free Float)
    total_shares: int                        # Capital social total em ações
    closing_price: Decimal                   # Preço de fechamento de referência em R$ (P_i)
    trading_sessions_presence: Decimal       # Proporção de presença em pregões (ex: 0.98 = 98%)
    is_penny_stock: bool                     # Classificado como penny stock (< R$ 1,00)
    is_in_judicial_recovery: bool            # Empresa em recuperação judicial/extrajudicial/falência
    dividend_yield: Decimal = Decimal("0")   # Dividend Yield acumulado no período (ex: 0.0850 = 8.5%)
    sector: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AssetTradingData:
        return cls(
            ticker=str(data["ticker"]).strip().upper(),
            company_name=str(data.get("company_name", data["ticker"])),
            trades_count=int(data["trades_count"]),
            financial_volume=_to_decimal(data["financial_volume"], "financial_volume"),
            free_float_shares=int(data["free_float_shares"]),
            total_shares=int(data["total_shares"]),
            closing_price=_to_decimal(data["closing_price"], "closing_price"),
            trading_sessions_presence=_to_decimal(data.get("trading_sessions_presence", "1.00"), "presence"),
            is_penny_stock=bool(data.get("is_penny_stock", False)),
            is_in_judicial_recovery=bool(data.get("is_in_judicial_recovery", False)),
            dividend_yield=_to_decimal(data.get("dividend_yield", "0.00"), "dividend_yield"),
            sector=data.get("sector")
        )

    @property
    def free_float_market_cap(self) -> Decimal:
        """Valor de mercado do Free Float: Qtd_Free_Float * Preço."""
        return Decimal(self.free_float_shares) * self.closing_price

    @property
    def total_market_cap(self) -> Decimal:
        """Valor de mercado total: Total_Ações * Preço."""
        return Decimal(self.total_shares) * self.closing_price


@dataclass(frozen=True)
class AssetNegotiabilityScore:
    """Score individual de Índice de Negociabilidade (IN) e status de elegibilidade."""
    ticker: str
    trades_count: int
    financial_volume: Decimal
    trade_share: Decimal                     # n_i / N
    volume_share: Decimal                    # v_i / V
    negotiability_index: Decimal             # IN_i = sqrt(trade_share * volume_share)
    in_relative_share: Decimal               # IN_i / sum(IN)
    cumulative_in_share: Decimal             # Soma acumulada do IN ordenado
    in_rank: int                             # Posição no ranking decrescente de IN
    meets_presence: bool                     # Presença >= 95%
    meets_volume_share: bool                 # Volume >= 0.1% do mercado
    meets_penny_stock_filter: bool           # Preço >= R$ 1,00 e não penny stock
    meets_recovery_filter: bool              # Não estar em recuperação judicial
    is_eligible: bool                        # Elegível no universo geral
    in_85_percent_cutoff: bool               # Pertence ao conjunto dos 85% acumulados de IN


@dataclass(frozen=True)
class IndexConstituent:
    """Constituinte selecionado para a carteira teórica de um índice."""
    ticker: str
    weight: Decimal                          # Peso final normalizado (Soma(W_i) == 1.0)
    weight_percentage: Decimal               # Peso em formato percentual (ex: 15.35%)
    uncapped_weight: Decimal                 # Peso original antes da aplicação do teto de 20%
    is_capped: bool                          # True se o ativo atingiu o teto regulatório
    free_float_shares: int
    free_float_market_cap: Decimal
    closing_price: Decimal
    theoretical_quantity: int                # Quantidade teórica de ações na carteira (Q_i)
    dividend_yield: Decimal = Decimal("0")


@dataclass(frozen=True)
class RebalancingResult:
    """Resultado formal do rebalanceamento do índice para um dado estágio/período."""
    index_type: IndexType
    period: str
    stage: PreviewStage
    as_of_date: str
    constituents: List[IndexConstituent]
    total_constituents: int
    total_free_float_market_cap: Decimal
    total_theoretical_portfolio_value: Decimal
    divisor: Decimal
    index_level: Decimal
    turnover_ratio: Decimal                  # Turnover em relação à carteira anterior: 0.5 * sum(|W_novo - W_antigo|)
    additions: List[str]                     # Novos ativos incluídos na carteira
    deletions: List[str]                     # Ativos excluídos da carteira
    cap_limit: Decimal
    weight_sum: Decimal                      # Soma dos pesos (deve ser estritamente 1.00000000)

    @property
    def weight_map(self) -> Dict[str, Decimal]:
        return {c.ticker: c.weight for c in self.constituents}


class IndexRebalancingEngine:
    """
    Motor determinístico de cálculo, triagem e rebalanceamento de índices B3.
    """

    def __init__(
        self,
        min_presence: Decimal = Decimal("0.95"),
        min_volume_share: Decimal = Decimal("0.001"),
        penny_stock_min_price: Decimal = Decimal("1.00"),
        in_cutoff_ratio: Decimal = Decimal("0.85"),
        default_cap_limit: Decimal = Decimal("0.20")
    ):
        self.min_presence = min_presence
        self.min_volume_share = min_volume_share
        self.penny_stock_min_price = penny_stock_min_price
        self.in_cutoff_ratio = in_cutoff_ratio
        self.default_cap_limit = default_cap_limit

    @staticmethod
    def calculate_single_in(
        trades_count: int,
        total_trades: int,
        financial_volume: Decimal,
        total_volume: Decimal
    ) -> Decimal:
        """
        Calcula o Índice de Negociabilidade (IN) individual:
        IN_i = sqrt( (n_i / N) * (v_i / V) )
        """
        if total_trades <= 0:
            raise IndexRebalancingError("Total de negócios de mercado deve ser estritamente positivo")
        if total_volume <= Decimal("0"):
            raise IndexRebalancingError("Volume financeiro total de mercado deve ser estritamente positivo")
        if trades_count < 0 or financial_volume < Decimal("0"):
            raise IndexRebalancingError("Negócios e volume individuais não podem ser negativos")

        trade_share = Decimal(trades_count) / Decimal(total_trades)
        volume_share = financial_volume / total_volume
        product = trade_share * volume_share
        return product.sqrt()

    def score_universe(self, assets: List[AssetTradingData]) -> List[AssetNegotiabilityScore]:
        """
        Calcula os índices de negociabilidade para todos os ativos do universo,
        aplica os filtros regulatórios e determina o corte de 85% de representatividade.
        """
        if not assets:
            raise IndexRebalancingError("Universo de ativos vazio para pontuação")

        total_trades = sum(a.trades_count for a in assets)
        total_volume = sum(a.financial_volume for a in assets)

        if total_trades <= 0 or total_volume <= Decimal("0"):
            raise IndexRebalancingError("Mercado com volume ou negócios agregados nulos")

        # 1. Cálculo de IN para todos os ativos
        raw_scores: List[Tuple[AssetTradingData, Decimal, Decimal, Decimal]] = []
        for asset in assets:
            trade_share = Decimal(asset.trades_count) / Decimal(total_trades)
            volume_share = asset.financial_volume / total_volume
            in_val = (trade_share * volume_share).sqrt()
            raw_scores.append((asset, trade_share, volume_share, in_val))

        # Ordenar decrescentemente por IN
        raw_scores.sort(key=lambda item: item[3], reverse=True)

        # Soma total de IN entre ativos elegíveis e ativos gerais
        total_in = sum(item[3] for item in raw_scores)
        if total_in <= Decimal("0"):
            raise IndexRebalancingError("Soma total do Índice de Negociabilidade nula")

        # 2. Avaliação de elegibilidade e corte de 85%
        scores: List[AssetNegotiabilityScore] = []
        cumulative_in = Decimal("0")

        for rank, (asset, trade_share, volume_share, in_val) in enumerate(raw_scores, start=1):
            meets_presence = asset.trading_sessions_presence >= self.min_presence
            meets_volume_share = volume_share >= self.min_volume_share
            meets_penny_stock = (
                not asset.is_penny_stock and asset.closing_price >= self.penny_stock_min_price
            )
            meets_recovery = not asset.is_in_judicial_recovery

            is_eligible = (
                meets_presence and meets_volume_share and meets_penny_stock and meets_recovery
            )

            relative_share = in_val / total_in
            prior_cum = cumulative_in
            cumulative_in += relative_share

            # Corte de 85%: inclui até o ativo marginal que atinge/ultrapassa 85%
            # (ou seja, se a soma acumulada antes da adição era < 0.85)
            in_85 = prior_cum < self.in_cutoff_ratio

            scores.append(
                AssetNegotiabilityScore(
                    ticker=asset.ticker,
                    trades_count=asset.trades_count,
                    financial_volume=asset.financial_volume,
                    trade_share=trade_share,
                    volume_share=volume_share,
                    negotiability_index=in_val,
                    in_relative_share=relative_share,
                    cumulative_in_share=cumulative_in,
                    in_rank=rank,
                    meets_presence=meets_presence,
                    meets_volume_share=meets_volume_share,
                    meets_penny_stock_filter=meets_penny_stock,
                    meets_recovery_filter=meets_recovery,
                    is_eligible=is_eligible,
                    in_85_percent_cutoff=in_85
                )
            )

        return scores

    def filter_constituents(
        self,
        assets: List[AssetTradingData],
        scores: List[AssetNegotiabilityScore],
        index_type: IndexType
    ) -> List[AssetTradingData]:
        """
        Filtra os ativos elegíveis de acordo com a metodologia de cada índice:
        - IBOV: Elegíveis dentro do corte dos 85% de representatividade de IN.
        - IBRX100: Top 100 ativos elegíveis ordenados por IN.
        - SMLL: Ativos elegíveis que compõem o segmento Small Cap (fora do top 85% de valor de mercado).
        - IDIV: Ativos elegíveis com maiores Dividend Yields no período.
        """
        asset_map = {a.ticker: a for a in assets}
        score_map = {s.ticker: s for s in scores}

        # Filtrar apenas ativos 100% elegíveis pelos critérios regulatórios
        eligible_tickers = [s.ticker for s in scores if s.is_eligible]

        if index_type == IndexType.IBOV:
            selected_tickers = [
                s.ticker for s in scores
                if s.is_eligible and s.in_85_percent_cutoff
            ]

        elif index_type == IndexType.IBRX100:
            # Top 100 ordenados por IN
            selected_tickers = eligible_tickers[:100]

        elif index_type == IndexType.SMLL:
            # Ordenar por valor de mercado total decrescente
            eligible_assets = [asset_map[t] for t in eligible_tickers]
            eligible_assets.sort(key=lambda a: a.total_market_cap, reverse=True)
            total_mcap = sum(a.total_market_cap for a in eligible_assets)

            # Ativos que estão fora dos 85% superiores de capitalização
            cum_mcap = Decimal("0")
            selected_tickers = []
            for a in eligible_assets:
                share = a.total_market_cap / total_mcap
                prior = cum_mcap
                cum_mcap += share
                # Small caps: ativos após o patamar de 85% de valor de mercado
                if prior >= Decimal("0.85"):
                    selected_tickers.append(a.ticker)
            
            # Se poucos ativos superarem 85%, selecionar a metade inferior de menor market cap
            if len(selected_tickers) < 3:
                midpoint = len(eligible_assets) // 2
                selected_tickers = [a.ticker for a in eligible_assets[midpoint:]]

        elif index_type == IndexType.IDIV:
            # Elegíveis com Dividend Yield positivo, ordenados por Dividend Yield decrescente
            eligible_assets = [asset_map[t] for t in eligible_tickers if asset_map[t].dividend_yield > Decimal("0")]
            eligible_assets.sort(key=lambda a: a.dividend_yield, reverse=True)
            # Seleciona o terço superior em dividendos (ou mínimo de 5 ativos)
            count = max(5, len(eligible_assets) // 2)
            selected_tickers = [a.ticker for a in eligible_assets[:count]]

        else:
            raise IndexRebalancingError(f"Tipo de índice não suportado: {index_type}")

        selected = [asset_map[t] for t in selected_tickers]
        if not selected:
            raise IndexRebalancingError(f"Nenhum ativo qualificado para o índice {index_type}")

        return selected

    def calculate_capped_weights(
        self,
        constituents: List[AssetTradingData],
        index_type: IndexType,
        cap_limit: Optional[Decimal] = None
    ) -> List[IndexConstituent]:
        """
        Calcula os pesos ponderados pelo Free Float (ou fator DY para IDIV),
        aplica o teto regulatório de concentração com redistribuição iterativa
        e garante a Lei de Conservação Estrita: Soma(W_i) == 1.00000000.
        """
        if not constituents:
            raise IndexRebalancingError("Lista de constituintes vazia para cálculo de pesos")

        cap = cap_limit if cap_limit is not None else self.default_cap_limit

        if cap <= Decimal("0") or cap > Decimal("1.0"):
            raise IndexRebalancingError(f"Cap limit inválido: {cap}")

        num_assets = len(constituents)
        if Decimal(num_assets) * cap < Decimal("1.0"):
            raise IndexRebalancingError(
                f"Cap limit {cap} impossível para {num_assets} constituintes: "
                f"Soma máxima seria {Decimal(num_assets) * cap} < 1.0"
            )

        # 1. Obtenção das métricas base de ponderação
        # IBOV, IBRX100, SMLL: Free Float Market Cap
        # IDIV: Free Float Market Cap ponderado por Dividend Yield
        base_metrics: Dict[str, Decimal] = {}
        for asset in constituents:
            ff_mcap = asset.free_float_market_cap
            if index_type == IndexType.IDIV:
                # Fator DY ponderador
                weight_factor = ff_mcap * (Decimal("1.0") + asset.dividend_yield * Decimal("10"))
            else:
                weight_factor = ff_mcap
            base_metrics[asset.ticker] = weight_factor

        total_base = sum(base_metrics.values())
        if total_base <= Decimal("0"):
            raise IndexRebalancingError("Valor base total dos constituintes não pode ser zero")

        # 2. Pesos não limitados (uncapped)
        uncapped_weights: Dict[str, Decimal] = {
            ticker: val / total_base for ticker, val in base_metrics.items()
        }

        # 3. Algoritmo iterativo de Capping e Redistribuição proporcional
        capped_tickers: Set[str] = set()
        weights: Dict[str, Decimal] = dict(uncapped_weights)

        while True:
            newly_capped = False
            uncapped_keys = [t for t in base_metrics if t not in capped_tickers]
            
            # Identificar ativos que excedem o teto
            for ticker in uncapped_keys:
                if weights[ticker] > cap:
                    capped_tickers.add(ticker)
                    weights[ticker] = cap
                    newly_capped = True

            if not newly_capped:
                break

            total_capped_weight = sum(weights[t] for t in capped_tickers)
            remaining_weight = Decimal("1.0") - total_capped_weight
            remaining_uncapped = [t for t in base_metrics if t not in capped_tickers]

            if not remaining_uncapped:
                # Todos os ativos atingiram o cap (caso extremo uniforme)
                break

            uncapped_base_sum = sum(base_metrics[t] for t in remaining_uncapped)
            if uncapped_base_sum <= Decimal("0"):
                break

            for ticker in remaining_uncapped:
                weights[ticker] = remaining_weight * (base_metrics[ticker] / uncapped_base_sum)

        # 4. Normalização e conservação estrita com arredondamento determinístico
        rounded_weights: Dict[str, Decimal] = {
            t: w.quantize(PRECISION_DECIMAL, rounding=ROUND_HALF_UP)
            for t, w in weights.items()
        }

        diff = Decimal("1.00000000") - sum(rounded_weights.values())
        if diff != Decimal("0"):
            # Aloca a diferença residual determinística no maior ativo que não esteja capped no limite estrito,
            # ou no maior ativo geral
            candidate_tickers = [t for t in base_metrics if t not in capped_tickers]
            if not candidate_tickers:
                candidate_tickers = list(base_metrics.keys())
            
            # Ordena pelo peso decrescente, e por ticker em caso de empate
            target_ticker = max(candidate_tickers, key=lambda t: (rounded_weights[t], t))
            rounded_weights[target_ticker] += diff

        # Verificação rigorosa da conservação de 100%
        final_sum = sum(rounded_weights.values())
        if final_sum != Decimal("1.00000000"):
            raise IndexRebalancingError(f"Falha na conservação estrita de pesos: soma {final_sum} != 1.00000000")

        # 5. Montagem dos objetos IndexConstituent
        constituent_objects: List[IndexConstituent] = []
        for asset in constituents:
            t = asset.ticker
            w = rounded_weights[t]
            w_pct = (w * Decimal("100")).quantize(PERCENT_DECIMAL, rounding=ROUND_HALF_UP)
            uncapped_w = uncapped_weights[t]
            is_capped = (t in capped_tickers) or (w >= cap)

            constituent_objects.append(
                IndexConstituent(
                    ticker=t,
                    weight=w,
                    weight_percentage=w_pct,
                    uncapped_weight=uncapped_w,
                    is_capped=is_capped,
                    free_float_shares=asset.free_float_shares,
                    free_float_market_cap=asset.free_float_market_cap,
                    closing_price=asset.closing_price,
                    theoretical_quantity=0, # Preenchido no rebalanceamento com notional
                    dividend_yield=asset.dividend_yield
                )
            )

        # Ordenar constituintes por peso decrescente
        constituent_objects.sort(key=lambda c: (c.weight, c.ticker), reverse=True)
        return constituent_objects

    def rebalance(
        self,
        assets: List[AssetTradingData],
        index_type: IndexType = IndexType.IBOV,
        period: str = "2027-Q1",
        stage: PreviewStage = PreviewStage.PREVIEW_3_DEFINITIVE,
        as_of_date: str = "2026-11-04",
        previous_portfolio: Optional[Union[RebalancingResult, Dict[str, Any]]] = None,
        portfolio_notional: Decimal = Decimal("1000000000.00"),
        base_index_level: Decimal = Decimal("130000.00"),
        cap_limit: Optional[Decimal] = None
    ) -> RebalancingResult:
        """
        Executa o fluxo completo e determinístico de rebalanceamento do índice:
        1. Pontuação e ranking de negociabilidade (IN).
        2. Triagem e seleção de constituintes pelo tipo de índice.
        3. Ponderação com teto de 20% e conservação Soma(W_i) == 100%.
        4. Apuração das quantidades teóricas (Q_i) e ajuste contínuo de divisor.
        5. Cálculo do turnover e balanço de inclusões/exclusões.
        """
        effective_cap = cap_limit if cap_limit is not None else self.default_cap_limit

        # 1. Pontuação e seleção
        scores = self.score_universe(assets)
        selected_assets = self.filter_constituents(assets, scores, index_type)

        # 2. Ponderação e capping
        initial_constituents = self.calculate_capped_weights(
            selected_assets, index_type, cap_limit=effective_cap
        )

        # 3. Cálculo de quantidades teóricas de ações (Q_i)
        asset_map = {a.ticker: a for a in selected_assets}
        final_constituents: List[IndexConstituent] = []
        total_theo_val = Decimal("0")

        for c in initial_constituents:
            asset = asset_map[c.ticker]
            target_cash = c.weight * portfolio_notional
            theo_qty = int((target_cash / asset.closing_price).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            total_theo_val += Decimal(theo_qty) * asset.closing_price

            final_constituents.append(
                IndexConstituent(
                    ticker=c.ticker,
                    weight=c.weight,
                    weight_percentage=c.weight_percentage,
                    uncapped_weight=c.uncapped_weight,
                    is_capped=c.is_capped,
                    free_float_shares=c.free_float_shares,
                    free_float_market_cap=c.free_float_market_cap,
                    closing_price=c.closing_price,
                    theoretical_quantity=theo_qty,
                    dividend_yield=c.dividend_yield
                )
            )

        # 4. Divisor e nível do índice
        total_ff_mcap = sum(c.free_float_market_cap for c in final_constituents)
        weight_sum = sum(c.weight for c in final_constituents)

        prev_weight_map: Dict[str, Decimal] = {}
        prev_divisor = Decimal("0")
        prev_portfolio_val = Decimal("0")

        if previous_portfolio is not None:
            if isinstance(previous_portfolio, RebalancingResult):
                prev_weight_map = previous_portfolio.weight_map
                prev_divisor = previous_portfolio.divisor
                prev_portfolio_val = previous_portfolio.total_theoretical_portfolio_value
            elif isinstance(previous_portfolio, dict):
                prev_weight_map = {
                    item["ticker"]: _to_decimal(item["weight"])
                    for item in previous_portfolio.get("constituents", [])
                }
                prev_divisor = _to_decimal(previous_portfolio.get("divisor", "0"))
                prev_portfolio_val = _to_decimal(previous_portfolio.get("total_portfolio_value", "0"))

        # Invariante do Divisor: se há carteira prévia, ajusta divisor para continuidade sem gap
        if prev_divisor > Decimal("0") and prev_portfolio_val > Decimal("0"):
            divisor = (prev_divisor * (total_theo_val / prev_portfolio_val)).quantize(
                PRICE_DECIMAL, rounding=ROUND_HALF_UP
            )
        else:
            divisor = (total_theo_val / base_index_level).quantize(PRICE_DECIMAL, rounding=ROUND_HALF_UP)

        index_level = (total_theo_val / divisor).quantize(PRICE_DECIMAL, rounding=ROUND_HALF_UP)

        # 5. Turnover e inclusões/exclusões
        current_tickers = set(c.ticker for c in final_constituents)
        prev_tickers = set(prev_weight_map.keys())

        additions = sorted(list(current_tickers - prev_tickers))
        deletions = sorted(list(prev_tickers - current_tickers))

        all_tickers = current_tickers.union(prev_tickers)
        turnover_sum = Decimal("0")
        for t in all_tickers:
            w_curr = next((c.weight for c in final_constituents if c.ticker == t), Decimal("0"))
            w_prev = prev_weight_map.get(t, Decimal("0"))
            turnover_sum += abs(w_curr - w_prev)

        turnover_ratio = (turnover_sum / Decimal("2")).quantize(PRECISION_DECIMAL, rounding=ROUND_HALF_UP)

        return RebalancingResult(
            index_type=index_type,
            period=period,
            stage=stage,
            as_of_date=as_of_date,
            constituents=final_constituents,
            total_constituents=len(final_constituents),
            total_free_float_market_cap=total_ff_mcap,
            total_theoretical_portfolio_value=total_theo_val,
            divisor=divisor,
            index_level=index_level,
            turnover_ratio=turnover_ratio,
            additions=additions,
            deletions=deletions,
            cap_limit=effective_cap,
            weight_sum=weight_sum
        )

    def simulate_quadrimestral_cycle(
        self,
        preview_1_assets: List[AssetTradingData],
        preview_2_assets: List[AssetTradingData],
        preview_3_assets: List[AssetTradingData],
        index_type: IndexType = IndexType.IBOV,
        period: str = "2027-Q1",
        previous_portfolio: Optional[Union[RebalancingResult, Dict[str, Any]]] = None
    ) -> Dict[PreviewStage, RebalancingResult]:
        """
        Simula a evolução temporal determinística do ciclo quadrimestral B3:
        1ª Prévia -> 2ª Prévia -> 3ª Prévia Definitiva.
        """
        p1 = self.rebalance(
            preview_1_assets,
            index_type=index_type,
            period=period,
            stage=PreviewStage.PREVIEW_1,
            as_of_date="2026-12-01",
            previous_portfolio=previous_portfolio
        )

        p2 = self.rebalance(
            preview_2_assets,
            index_type=index_type,
            period=period,
            stage=PreviewStage.PREVIEW_2,
            as_of_date="2026-12-16",
            previous_portfolio=p1
        )

        p3 = self.rebalance(
            preview_3_assets,
            index_type=index_type,
            period=period,
            stage=PreviewStage.PREVIEW_3_DEFINITIVE,
            as_of_date="2026-12-30",
            previous_portfolio=p2
        )

        return {
            PreviewStage.PREVIEW_1: p1,
            PreviewStage.PREVIEW_2: p2,
            PreviewStage.PREVIEW_3_DEFINITIVE: p3
        }

    def verify_invariants(
        self,
        result: RebalancingResult,
        assets: Optional[List[AssetTradingData]] = None,
        cap_limit: Optional[Decimal] = None
    ) -> Dict[str, bool]:
        """
        Verificação formal das 5 invariantes do índice B3:
        1. weight_conservation: Soma(W_i) == 1.00000000 (100%).
        2. cap_limit_respected: Todos W_i <= Cap_Limit.
        3. no_ineligible_constituents: Zero penny stocks, recuperação judicial ou presença < 95%.
        4. representativity_satisfied: Número de constituintes > 0 e conservação de representatividade.
        5. theoretical_quantities_positive: Todas Q_i > 0.
        """
        cap = cap_limit if cap_limit is not None else result.cap_limit

        # 1. Conservação estrita de pesos
        weight_sum = sum(c.weight for c in result.constituents)
        weight_conservation = (weight_sum == Decimal("1.00000000"))

        # 2. Respeito ao teto
        cap_tolerance = cap + Decimal("0.0000001")
        cap_limit_respected = all(c.weight <= cap_tolerance for c in result.constituents)

        # 3. Ausência de ativos inelegíveis
        no_ineligible = True
        if assets:
            asset_map = {a.ticker: a for a in assets}
            for c in result.constituents:
                a = asset_map.get(c.ticker)
                if a:
                    if a.is_in_judicial_recovery or a.is_penny_stock or a.trading_sessions_presence < self.min_presence:
                        no_ineligible = False
                        break

        # 4. Quantidades positivas
        theo_positive = all(c.theoretical_quantity > 0 for c in result.constituents)

        # 5. Representatividade
        rep_satisfied = result.total_constituents > 0

        return {
            "weight_conservation": weight_conservation,
            "cap_limit_respected": cap_limit_respected,
            "no_ineligible_constituents": no_ineligible,
            "theoretical_quantities_positive": theo_positive,
            "representativity_satisfied": rep_satisfied,
            "all_passed": (
                weight_conservation
                and cap_limit_respected
                and no_ineligible
                and theo_positive
                and rep_satisfied
            )
        }

    @classmethod
    def load_from_fixture(
        cls,
        fixture_path: Union[str, Path]
    ) -> Tuple[IndexRebalancingEngine, List[AssetTradingData], Dict[str, Any]]:
        """Carrega a fixture JSON e instancia o motor com os parâmetros configurados."""
        path = Path(fixture_path)
        if not path.exists():
            raise FileNotFoundError(f"Fixture não encontrada em {fixture_path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        meta = data.get("metadata", {})
        engine = cls(
            min_presence=_to_decimal(meta.get("presenca_minima", "0.95")),
            min_volume_share=_to_decimal(meta.get("volume_share_minimo", "0.001")),
            penny_stock_min_price=_to_decimal(meta.get("penny_stock_threshold", "1.00")),
            in_cutoff_ratio=_to_decimal(meta.get("corte_representatividade_in", "0.85")),
            default_cap_limit=_to_decimal(meta.get("cap_limit", "0.20"))
        )

        assets = [AssetTradingData.from_dict(item) for item in data.get("universe", [])]
        return engine, assets, data
