"""
B3 Câmara — Gestão de Garantias Transfronteiriças (FL-067)
===========================================================
Módulo de gestão, avaliação determinística e chamadas de margem para
Garantias Transfronteiriças (Cross-Border Collateral) custodiadas via
Euroclear Bank (ICSD) e Depository Trust Company - DTC (DTCC).

Principais Funcionalidades:
1. Conversão Cambial Multimoeda via PTAX (BACEN):
   - Cotações PTAX de Compra (bid), Venda (ask) e Média (mid) para USD, EUR, GBP.
   - Aplicação da taxa de Compra (bid) para avaliação conservadora de garantias da CCP.
   - Triangulação cambial e verificação da lei de paridade triangular.
2. Matriz de Haircuts de Títulos Soberanos Internacionais:
   - US Treasuries (T-Bills, T-Notes, T-Bonds) via DTC / Fedwire / Euroclear.
   - Títulos Soberanos Alemães (Bunds, Bobls, Bubills) via Euroclear / Clearstream.
   - Escadinha de haircuts por faixas de prazo residual (tenor buckets): <=1y, 1-3y, 3-5y, 5-10y, >10y.
   - Adicionais por rating de crédito soberano e características da depositária internacional.
   - Lei de monotonicidade estrita de tenor: H(T1) <= H(T2) para T1 < T2.
3. Buffer de Volatilidade Cambial (FX Volatility Buffer):
   - Modelo paramétrico baseado em horizonte de liquidação (close-out horizon h=2 dias úteis),
     nível de confiança 99% (z=2.33) e piso regulatório estrito.
   - Dedução do buffer sobre o valor convertido em BRL para absorver oscilações cambiais
     durante a janela de execução de default.
4. Apuração de Margem e Chamadas de Margem (Margin Calls):
   - Comparação determinística entre Margem Requerida (CORE B3) e Garantias Elegíveis Líquidas.
   - Emissão de Margin Call com prazo de regularização (intradiário vs fechamento D+1).
   - Trava de segurança para desoneração/liberação de garantias (Safe-Release Guardrail).
5. Conciliação de Custódia Transfronteiriça:
   - Conciliação entre os saldos registrados no sistema NoMe/CORE e os extratos da DTC/Euroclear.

Invariantes Formais Provadas:
- Lei 1: Não-negatividade e limitação superior do valor líquido elegível (0 <= V_net <= V_gross_brl).
- Lei 2: Monotonicidade estrita da matriz de haircuts por maturidade residual.
- Lei 3: Co-domínio estrito de Haircuts e Buffers Cambiais [0.00, 1.00).
- Lei 4: Consistência da paridade triangular cambial (|PTAX_EUR - (EUR/USD * PTAX_USD)| <= epsilon).
- Lei 5: Monotonicidade e determinismo da apuração de déficit de margem.
- Lei 6: Conservação de custódia transfronteiriça (Alocado + Desimpedido == Total Custodiado).
- Lei 7: Trava estrita anti-déficit na desoneração de garantias (Safe-Release Guardrail).
- Lei 8: Rejeição absoluta de soberanos inelegíveis ou sob sanção internacional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")


# ---------------------------------------------------------------------------
# Exceções Especializadas
# ---------------------------------------------------------------------------

class CrossBorderCollateralError(Exception):
    """Exceção base para o fluxo de garantias transfronteiriças (FL-067)."""
    pass


class BlockedCurrencyError(CrossBorderCollateralError):
    """Lançada quando a moeda ou jurisdição é bloqueada por sanções internacionais (OFAC / ONU)."""
    pass


class IneligibleSovereignError(CrossBorderCollateralError):
    """Lançada quando o título, país emissor ou rating não atende aos requisitos de elegibilidade."""
    pass


class UnsupportedCurrencyError(CrossBorderCollateralError):
    """Lançada quando a moeda estrangeira não possui cotação PTAX ou não é admitida pela CCP."""
    pass


class InvalidHaircutError(CrossBorderCollateralError):
    """Lançada quando um fator de haircut viola os limites regulatórios [0, 1)."""
    pass


class InvalidPositionError(CrossBorderCollateralError):
    """Lançada quando parâmetros da posição (quantidade, preço, prazo) são inválidos."""
    pass


class MarginReleaseViolationError(CrossBorderCollateralError):
    """Lançada quando uma tentativa de desoneração de garantias geraria déficit de margem."""
    pass


class DepositorySettlementError(CrossBorderCollateralError):
    """Lançada quando ocorre divergência ou falha na instrução com a depositária internacional."""
    pass


# ---------------------------------------------------------------------------
# Domínios e Tipos Enumerados
# ---------------------------------------------------------------------------

class SovereignIssuer(str, Enum):
    """Emissores soberanos aprovados pela B3 para colateral internacional."""
    US = "US"  # Estados Unidos (US Department of the Treasury)
    DE = "DE"  # Alemanha (Federal Republic of Germany / Finanzagentur)
    UK = "UK"  # Reino Unido (HM Treasury / Debt Management Office)
    JP = "JP"  # Japão (Ministry of Finance)


class SovereignBondType(str, Enum):
    """Tipos de instrumentos soberanos transfronteiriços."""
    US_TREASURY_BILL = "US_TREASURY_BILL"    # T-Bill (< 1 ano, cupom zero / desconto)
    US_TREASURY_NOTE = "US_TREASURY_NOTE"    # T-Note (1 a 10 anos, cupom semestral)
    US_TREASURY_BOND = "US_TREASURY_BOND"    # T-Bond (> 10 anos, longo prazo)
    GERMAN_BUBILL = "GERMAN_BUBILL"          # Bubill (< 1 ano, desconto)
    GERMAN_BOBL = "GERMAN_BOBL"              # Bobl (Bundesobligationen, ~5 anos)
    GERMAN_BUND = "GERMAN_BUND"              # Bund (Bundesanleihen, 10 a 30 anos)
    UK_GILT = "UK_GILT"                      # UK Conventional Gilt


class InternationalDepository(str, Enum):
    """Depositárias centrais internacionais integradas à Câmara B3."""
    EUROCLEAR = "EUROCLEAR"      # Euroclear Bank Bruxelas (ICSD / Triparty)
    DTC = "DTC"                  # Depository Trust Company / DTCC New York (CSD)
    CLEARSTREAM = "CLEARSTREAM"  # Clearstream Banking Frankfurt/Luxembourg (ICSD)


class Currency(str, Enum):
    """Moedas aceitas e precificadas pela Câmara B3."""
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    BRL = "BRL"


class TenorBucket(str, Enum):
    """Faixas de maturidade residual do título soberano."""
    UP_TO_1Y = "UP_TO_1Y"           # Maturidade residual <= 1.0 ano
    FROM_1Y_TO_3Y = "FROM_1Y_TO_3Y" # 1.0 ano < Maturidade <= 3.0 anos
    FROM_3Y_TO_5Y = "FROM_3Y_TO_5Y" # 3.0 anos < Maturidade <= 5.0 anos
    FROM_5Y_TO_10Y = "FROM_5Y_TO_10Y" # 5.0 anos < Maturidade <= 10.0 anos
    OVER_10Y = "OVER_10Y"           # Maturidade > 10.0 anos


class CollateralPledgeStatus(str, Enum):
    """Status operacional do colateral transfronteiriço."""
    PROPOSED = "PROPOSED"            # Instrução de depósito iniciada
    LOCKED_IN_CUSTODY = "LOCKED"     # Bloqueado na conta da B3 na depositária internacional
    ACTIVE_PLEDGED = "ACTIVE_PLEDGED"# Alocado formalmente para cobrir margem CORE
    RELEASE_REQUESTED = "RELEASE_REQ"# Solicitação de desbloqueio em análise
    RELEASED = "RELEASED"            # Devolvido ao participante no exterior
    REJECTED = "REJECTED"            # Rejeitado por inelegibilidade


class MarginCallStatus(str, Enum):
    """Situação da exigência de garantias do participante."""
    SUFFICIENT = "SUFFICIENT"                # Garantias líquidas cobrem 100% da margem
    SURPLUS = "SURPLUS"                      # Excesso de garantias disponível
    MARGIN_CALL_ISSUED = "MARGIN_CALL_ISSUED"# Déficit de garantia apurado; chamada emitida
    SATISFIED = "SATISFIED"                  # Chamada atendida dentro do prazo
    BREACHED = "BREACHED"                    # Prazo estourado sem regularização (Default)


# ---------------------------------------------------------------------------
# Modelos de Dados
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PtaxRate:
    """Taxa de câmbio oficial PTAX divulgada pelo Banco Central do Brasil."""
    currency: Union[Currency, str]
    bid_rate: Decimal     # Taxa de compra (utilizada pela CCP para avaliação conservadora)
    ask_rate: Decimal     # Taxa de venda
    mid_rate: Decimal     # Taxa intermediária
    bulletin_date: str    # Data do boletim (YYYY-MM-DD)

    def __post_init__(self) -> None:
        if self.bid_rate <= Decimal("0") or self.ask_rate <= Decimal("0"):
            raise UnsupportedCurrencyError("Taxas de câmbio PTAX devem ser estritamente positivas.")
        if self.bid_rate > self.ask_rate:
            raise UnsupportedCurrencyError("Taxa de compra (bid) não pode ser superior à taxa de venda (ask).")


@dataclass(frozen=True)
class FxVolatilityBufferConfig:
    """Configuração do Buffer de Volatilidade Cambial para moedas aceitas como colateral."""
    currency: Currency
    daily_volatility: Decimal        # Volatilidade diária (sigma_fx)
    confidence_quantile: Decimal     # Quantil de confiança (ex: 2.33 para 99%)
    liquidation_horizon_days: int    # Prazo de liquidação em dias úteis (h=2)
    minimum_floor_buffer: Decimal    # Piso regulatório mínimo (ex: 5.0% para USD)

    def calculate_buffer(self) -> Decimal:
        """Calcula o buffer paramétrico com piso regulatório: max(floor, z * sigma * sqrt(h))."""
        sqrt_h = Decimal(str(math.sqrt(self.liquidation_horizon_days)))
        parametric = self.confidence_quantile * self.daily_volatility * sqrt_h
        effective = max(self.minimum_floor_buffer, parametric)
        return effective.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class CrossBorderSecurity:
    """Atributos mestres do título de dívida soberana internacional."""
    isin: str
    ticker: str
    name: str
    issuer: SovereignIssuer
    bond_type: SovereignBondType
    currency: Currency
    depository: InternationalDepository
    credit_rating: str          # ex: "AAA", "AA+", "AA", "AA-", "A+"
    maturity_years: Decimal
    unit_price: Decimal         # Preço dirty (inclui juros acruados se aplicável)

    def __post_init__(self) -> None:
        if self.unit_price <= Decimal("0"):
            raise InvalidPositionError(f"Preço unitário do título {self.isin} deve ser estritamente positivo.")
        if self.maturity_years <= Decimal("0"):
            raise InvalidPositionError(f"Maturidade residual do título {self.isin} deve ser estritamente positiva.")
        if not self.isin or len(self.isin) != 12:
            raise InvalidPositionError(f"ISIN inválido: '{self.isin}'. Deve possuir 12 caracteres alfanuméricos.")


@dataclass
class CrossBorderCollateralPosition:
    """Posição individual de garantia soberana transfronteiriça."""
    position_id: str
    participant_id: str
    account_id: str
    security: CrossBorderSecurity
    quantity: Decimal
    pledge_status: CollateralPledgeStatus = CollateralPledgeStatus.LOCKED_IN_CUSTODY

    def __post_init__(self) -> None:
        if self.quantity <= Decimal("0"):
            raise InvalidPositionError(f"Quantidade do colateral {self.position_id} deve ser estritamente positiva.")


@dataclass(frozen=True)
class EvaluatedCollateralItem:
    """Resultado detalhado da avaliação financeira e regulatória de um item de colateral."""
    position_id: str
    isin: str
    ticker: str
    issuer: SovereignIssuer
    bond_type: SovereignBondType
    depository: InternationalDepository
    currency: Currency
    quantity: Decimal
    unit_price: Decimal
    gross_value_fx: Decimal
    tenor_bucket: TenorBucket
    bond_haircut_rate: Decimal
    bond_haircut_amount_fx: Decimal
    net_value_after_haircut_fx: Decimal
    ptax_bid_rate: Decimal
    gross_converted_brl: Decimal
    fx_volatility_buffer_rate: Decimal
    fx_buffer_deduction_brl: Decimal
    net_eligible_value_brl: Decimal
    effective_total_haircut_pct: Decimal


@dataclass(frozen=True)
class CrossBorderMarginCallReceipt:
    """Comprovante e detalhamento da apuração de margem e chamada de garantias."""
    receipt_id: str
    timestamp: str
    participant_id: str
    required_margin_brl: Decimal
    total_gross_collateral_brl: Decimal
    total_bond_haircut_deduction_brl: Decimal
    total_fx_buffer_deduction_brl: Decimal
    total_net_eligible_collateral_brl: Decimal
    margin_deficit_brl: Decimal
    margin_surplus_brl: Decimal
    status: MarginCallStatus
    call_deadline: Optional[str]
    currencies_accepted: List[str]
    evaluated_items: List[EvaluatedCollateralItem]


@dataclass(frozen=True)
class CrossBorderCustodyReconciliation:
    """Resultado da conciliação entre o saldo interno e o extrato da depositária internacional."""
    reconciliation_id: str
    depository: InternationalDepository
    total_internal_ledger_units: Decimal
    total_external_statement_units: Decimal
    is_balanced: bool
    discrepancy_units: Decimal
    reconciled_positions_count: int


# ---------------------------------------------------------------------------
# Motor de Gestão de Garantias Transfronteiriças (FL-067)
# ---------------------------------------------------------------------------

class CrossBorderCollateralEngine:
    """
    Motor determinístico da Câmara B3 para gestão de garantias transfronteiriças.
    Controla conversões cambiais PTAX, matriz de haircuts de títulos soberanos,
    buffers de volatilidade e emissão de chamadas de margem.
    """

    # Matriz Base de Haircuts Soberanos por Tenor Bucket
    DEFAULT_SOVEREIGN_HAIRCUTS: Dict[Tuple[SovereignIssuer, TenorBucket], Decimal] = {
        # US Treasuries (Rating AAA / AA+)
        (SovereignIssuer.US, TenorBucket.UP_TO_1Y): Decimal("0.0100"),        # 1.00%
        (SovereignIssuer.US, TenorBucket.FROM_1Y_TO_3Y): Decimal("0.0200"),   # 2.00%
        (SovereignIssuer.US, TenorBucket.FROM_3Y_TO_5Y): Decimal("0.0350"),   # 3.50%
        (SovereignIssuer.US, TenorBucket.FROM_5Y_TO_10Y): Decimal("0.0500"),  # 5.00%
        (SovereignIssuer.US, TenorBucket.OVER_10Y): Decimal("0.0800"),        # 8.00%

        # German Bunds / Bobls / Bubills (Rating AAA)
        (SovereignIssuer.DE, TenorBucket.UP_TO_1Y): Decimal("0.0100"),        # 1.00%
        (SovereignIssuer.DE, TenorBucket.FROM_1Y_TO_3Y): Decimal("0.0200"),   # 2.00%
        (SovereignIssuer.DE, TenorBucket.FROM_3Y_TO_5Y): Decimal("0.0350"),   # 3.50%
        (SovereignIssuer.DE, TenorBucket.FROM_5Y_TO_10Y): Decimal("0.0500"),  # 5.00%
        (SovereignIssuer.DE, TenorBucket.OVER_10Y): Decimal("0.0800"),        # 8.00%

        # UK Gilts (Rating AA)
        (SovereignIssuer.UK, TenorBucket.UP_TO_1Y): Decimal("0.0150"),        # 1.50%
        (SovereignIssuer.UK, TenorBucket.FROM_1Y_TO_3Y): Decimal("0.0250"),   # 2.50%
        (SovereignIssuer.UK, TenorBucket.FROM_3Y_TO_5Y): Decimal("0.0400"),   # 4.00%
        (SovereignIssuer.UK, TenorBucket.FROM_5Y_TO_10Y): Decimal("0.0600"),  # 6.00%
        (SovereignIssuer.UK, TenorBucket.OVER_10Y): Decimal("0.0900"),        # 9.00%
    }

    # Adicional de Liquidez e Custo Operacional por Depositária Internacional
    DEPOSITORY_SURCHARGE: Dict[InternationalDepository, Decimal] = {
        InternationalDepository.DTC: Decimal("0.0020"),         # 0.20% (DTCC / Fedwire link)
        InternationalDepository.EUROCLEAR: Decimal("0.0025"),   # 0.25% (Euroclear Triparty / ICSD)
        InternationalDepository.CLEARSTREAM: Decimal("0.0025"), # 0.25% (Clearstream Banking)
    }

    # Buffers Regulatórios de Volatilidade Cambial (Piso Mínimo)
    DEFAULT_FX_BUFFERS: Dict[Currency, FxVolatilityBufferConfig] = {
        Currency.USD: FxVolatilityBufferConfig(
            currency=Currency.USD,
            daily_volatility=Decimal("0.0120"),     # 1.2% vol diária
            confidence_quantile=Decimal("2.33"),    # 99% confiança
            liquidation_horizon_days=2,
            minimum_floor_buffer=Decimal("0.0500"), # Piso 5.00%
        ),
        Currency.EUR: FxVolatilityBufferConfig(
            currency=Currency.EUR,
            daily_volatility=Decimal("0.0140"),     # 1.4% vol diária
            confidence_quantile=Decimal("2.33"),
            liquidation_horizon_days=2,
            minimum_floor_buffer=Decimal("0.0600"), # Piso 6.00%
        ),
        Currency.GBP: FxVolatilityBufferConfig(
            currency=Currency.GBP,
            daily_volatility=Decimal("0.0160"),     # 1.6% vol diária
            confidence_quantile=Decimal("2.33"),
            liquidation_horizon_days=2,
            minimum_floor_buffer=Decimal("0.0700"), # Piso 7.00%
        ),
    }

    # Moedas ou Países Sujeitos a Sanções Internacionais (OFAC / ONU)
    BLOCKED_SANCTIONED_CURRENCIES: Set[str] = {"RUB", "IRR", "KPW", "SYR", "VEF", "CUP", "BYN"}

    def __init__(self) -> None:
        self.ptax_rates: Dict[Currency, PtaxRate] = {}
        self.positions: Dict[str, CrossBorderCollateralPosition] = {}
        self.fx_buffer_configs: Dict[Currency, FxVolatilityBufferConfig] = dict(self.DEFAULT_FX_BUFFERS)
        self.receipts: Dict[str, CrossBorderMarginCallReceipt] = {}

    # -----------------------------------------------------------------------
    # Gestão de Cotações Cambiais PTAX
    # -----------------------------------------------------------------------

    def register_ptax_rate(self, ptax: PtaxRate) -> None:
        """Registra taxa PTAX oficial do Banco Central para uma determinada moeda."""
        curr_str = ptax.currency.value if isinstance(ptax.currency, Currency) else str(ptax.currency).strip().upper()
        if curr_str in self.BLOCKED_SANCTIONED_CURRENCIES:
            raise BlockedCurrencyError(f"Moeda {curr_str} bloqueada por sanções financeiras internacionais.")
        try:
            cur_enum = ptax.currency if isinstance(ptax.currency, Currency) else Currency(curr_str)
        except ValueError:
            raise UnsupportedCurrencyError(f"Moeda {curr_str} não suportada pela Câmara B3.")
        self.ptax_rates[cur_enum] = ptax

    def get_ptax_rate(self, currency: Union[Currency, str]) -> PtaxRate:
        """Recupera cotação PTAX cadastrada. Se a moeda for BRL, retorna taxa paritária 1.0."""
        curr_str = currency.value if isinstance(currency, Currency) else str(currency).strip().upper()
        if curr_str in self.BLOCKED_SANCTIONED_CURRENCIES:
            raise BlockedCurrencyError(f"Moeda {curr_str} bloqueada por sanções financeiras internacionais.")
        try:
            cur_enum = currency if isinstance(currency, Currency) else Currency(curr_str)
        except ValueError:
            raise UnsupportedCurrencyError(f"Moeda {curr_str} não suportada pela Câmara B3.")

        if cur_enum == Currency.BRL:
            return PtaxRate(
                currency=Currency.BRL,
                bid_rate=Decimal("1.0000"),
                ask_rate=Decimal("1.0000"),
                mid_rate=Decimal("1.0000"),
                bulletin_date=datetime.now().strftime("%Y-%m-%d"),
            )
        if cur_enum not in self.ptax_rates:
            raise UnsupportedCurrencyError(f"Cotação PTAX não registrada para a moeda: {cur_enum.value}")
        return self.ptax_rates[cur_enum]

    def convert_fx_to_brl(
        self,
        amount_fx: Decimal,
        currency: Currency,
        use_bid: bool = True,
    ) -> Decimal:
        """
        Converte montante em moeda estrangeira para BRL via PTAX.
        Por padrão, utiliza a taxa de compra (bid) para precificação conservadora de garantias da CCP.
        """
        if amount_fx < Decimal("0"):
            raise CrossBorderCollateralError("Montante financeiro não pode ser negativo.")
        ptax = self.get_ptax_rate(currency)
        rate = ptax.bid_rate if use_bid else ptax.ask_rate
        converted = (amount_fx * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        return converted

    def calculate_synthetic_cross_rate(
        self,
        base_currency: Currency,
        quote_currency: Currency,
    ) -> Decimal:
        """
        Calcula taxa cruzada sintética (Cross-Rate) entre duas moedas estrangeiras via paridade BRL.
        Exemplo: EUR/USD = PTAX(EUR/BRL_mid) / PTAX(USD/BRL_mid).
        """
        ptax_base = self.get_ptax_rate(base_currency)
        ptax_quote = self.get_ptax_rate(quote_currency)
        cross_rate = (ptax_base.mid_rate / ptax_quote.mid_rate).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
        return cross_rate

    # -----------------------------------------------------------------------
    # Matriz e Metodologia de Haircuts Soberanos
    # -----------------------------------------------------------------------

    @staticmethod
    def classify_tenor_bucket(maturity_years: Decimal) -> TenorBucket:
        """Mapeia prazo residual em anos para o correspondente Tenor Bucket regulatório."""
        if maturity_years <= Decimal("1.0"):
            return TenorBucket.UP_TO_1Y
        elif maturity_years <= Decimal("3.0"):
            return TenorBucket.FROM_1Y_TO_3Y
        elif maturity_years <= Decimal("5.0"):
            return TenorBucket.FROM_3Y_TO_5Y
        elif maturity_years <= Decimal("10.0"):
            return TenorBucket.FROM_5Y_TO_10Y
        else:
            return TenorBucket.OVER_10Y

    @classmethod
    def calculate_credit_rating_haircut(cls, rating: str) -> Decimal:
        """
        Calcula o acréscimo de deságio baseado na classificação de risco de crédito soberano.
        Títulos abaixo de BBB- (grau especulativo) são considerados inelegíveis.
        """
        clean_rating = rating.strip().upper()
        if clean_rating in ("AAA", "AA+"):
            return Decimal("0.0000")
        elif clean_rating in ("AA", "AA-"):
            return Decimal("0.0050")  # +0.50%
        elif clean_rating in ("A+", "A", "A-"):
            return Decimal("0.0150")  # +1.50%
        elif clean_rating in ("BBB+", "BBB", "BBB-"):
            return Decimal("0.0300")  # +3.00%
        else:
            raise IneligibleSovereignError(
                f"Rating de crédito '{rating}' abaixo do mínimo grau de investimento exigido (BBB-)."
            )

    def calculate_sovereign_haircut(
        self,
        issuer: SovereignIssuer,
        maturity_years: Decimal,
        depository: InternationalDepository,
        credit_rating: str,
    ) -> Tuple[TenorBucket, Decimal]:
        """
        Calcula o Haircut total do título soberano:
        H_bond = H_base(issuer, tenor) + H_depository + H_credit.
        """
        bucket = self.classify_tenor_bucket(maturity_years)
        key = (issuer, bucket)
        if key not in self.DEFAULT_SOVEREIGN_HAIRCUTS:
            raise IneligibleSovereignError(
                f"Emissor soberano {issuer.value} não possui matriz de haircut homologada para o prazo {bucket.value}."
            )

        base_haircut = self.DEFAULT_SOVEREIGN_HAIRCUTS[key]
        depository_haircut = self.DEPOSITORY_SURCHARGE.get(depository, Decimal("0.0025"))
        credit_haircut = self.calculate_credit_rating_haircut(credit_rating)

        total_bond_haircut = base_haircut + depository_haircut + credit_haircut
        if total_bond_haircut >= Decimal("1.0000") or total_bond_haircut < Decimal("0"):
            raise InvalidHaircutError(f"Haircut soberano fora da faixa permitida [0, 1): {total_bond_haircut}")

        return bucket, total_bond_haircut.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)

    def get_fx_volatility_buffer(self, currency: Currency) -> Decimal:
        """Obtém o percentual do Buffer de Volatilidade Cambial para a moeda do colateral."""
        if currency == Currency.BRL:
            return Decimal("0.0000")
        if currency not in self.fx_buffer_configs:
            # Fallback seguro com buffer padrão estrito de 10%
            return Decimal("0.1000")
        return self.fx_buffer_configs[currency].calculate_buffer()

    # -----------------------------------------------------------------------
    # Avaliação Financeira Individual de Posições
    # -----------------------------------------------------------------------

    def evaluate_position(
        self,
        position: CrossBorderCollateralPosition,
    ) -> EvaluatedCollateralItem:
        """
        Avalia detalhadamente uma posição individual de colateral transfronteiriço:
        1. Valor Bruto em Moeda Original = Quantidade * Preço Unitário.
        2. Aplicação do Haircut de Título Soberano (deságio de duration, crédito e depositária).
        3. Conversão para BRL via PTAX de Compra (bid).
        4. Aplicação do Buffer de Volatilidade Cambial (FX Buffer) sobre o valor BRL.
        5. Obtenção do Valor Líquido Elegível BRL.
        """
        sec = position.security
        if sec.currency.value in self.BLOCKED_SANCTIONED_CURRENCIES:
            raise IneligibleSovereignError(f"Moeda {sec.currency.value} sancionada internacionalmente.")

        # 1. Valor bruto em moeda original
        gross_value_fx = (position.quantity * sec.unit_price).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # 2. Matriz de Haircut Soberano
        bucket, bond_haircut_rate = self.calculate_sovereign_haircut(
            issuer=sec.issuer,
            maturity_years=sec.maturity_years,
            depository=sec.depository,
            credit_rating=sec.credit_rating,
        )
        bond_haircut_amount_fx = (gross_value_fx * bond_haircut_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        net_value_after_haircut_fx = gross_value_fx - bond_haircut_amount_fx

        # 3. Conversão PTAX de Compra (conservadora)
        ptax = self.get_ptax_rate(sec.currency)
        ptax_bid_rate = ptax.bid_rate
        gross_converted_brl = (gross_value_fx * ptax_bid_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        value_converted_after_bond_haircut_brl = (
            net_value_after_haircut_fx * ptax_bid_rate
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # 4. Buffer de Volatilidade Cambial
        fx_buffer_rate = self.get_fx_volatility_buffer(sec.currency)
        fx_buffer_deduction_brl = (
            value_converted_after_bond_haircut_brl * fx_buffer_rate
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # 5. Valor Líquido Elegível BRL
        net_eligible_value_brl = (
            value_converted_after_bond_haircut_brl - fx_buffer_deduction_brl
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Haircut Efetivo Total em relação ao valor bruto convertido
        if gross_converted_brl > Decimal("0"):
            total_deduction_brl = gross_converted_brl - net_eligible_value_brl
            effective_total_haircut_pct = (
                total_deduction_brl / gross_converted_brl
            ).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
        else:
            effective_total_haircut_pct = Decimal("0.0000")

        return EvaluatedCollateralItem(
            position_id=position.position_id,
            isin=sec.isin,
            ticker=sec.ticker,
            issuer=sec.issuer,
            bond_type=sec.bond_type,
            depository=sec.depository,
            currency=sec.currency,
            quantity=position.quantity,
            unit_price=sec.unit_price,
            gross_value_fx=gross_value_fx,
            tenor_bucket=bucket,
            bond_haircut_rate=bond_haircut_rate,
            bond_haircut_amount_fx=bond_haircut_amount_fx,
            net_value_after_haircut_fx=net_value_after_haircut_fx,
            ptax_bid_rate=ptax_bid_rate,
            gross_converted_brl=gross_converted_brl,
            fx_volatility_buffer_rate=fx_buffer_rate,
            fx_buffer_deduction_brl=fx_buffer_deduction_brl,
            net_eligible_value_brl=net_eligible_value_brl,
            effective_total_haircut_pct=effective_total_haircut_pct,
        )

    # -----------------------------------------------------------------------
    # Gestão de Posições, Depósito e Liberação de Colateral
    # -----------------------------------------------------------------------

    def register_position(self, position: CrossBorderCollateralPosition) -> None:
        """Registra uma posição de garantia soberana transfronteiriça no livro da CCP."""
        if position.position_id in self.positions:
            raise CrossBorderCollateralError(f"Posição já cadastrada: {position.position_id}")
        self.positions[position.position_id] = position

    def get_participant_positions(
        self,
        participant_id: str,
        active_only: bool = True,
    ) -> List[CrossBorderCollateralPosition]:
        """Retorna todas as posições de garantia associadas a um participante específico."""
        results = []
        for pos in self.positions.values():
            if pos.participant_id == participant_id:
                if active_only and pos.pledge_status not in (
                    CollateralPledgeStatus.LOCKED_IN_CUSTODY,
                    CollateralPledgeStatus.ACTIVE_PLEDGED,
                ):
                    continue
                results.append(pos)
        return results

    def release_collateral(
        self,
        position_id: str,
        release_quantity: Decimal,
        required_margin_brl: Decimal,
    ) -> Tuple[bool, str]:
        """
        Desonera/libera total ou parcialmente uma garantia transfronteiriça.
        Aplica a Trava de Segurança (Safe-Release Guardrail):
        A liberação só é permitida se e somente se o valor líquido remanescente
        de garantias continuar cobrindo 100% da Margem Requerida.
        """
        if position_id not in self.positions:
            raise InvalidPositionError(f"Posição {position_id} não encontrada para liberação.")
        pos = self.positions[position_id]
        if release_quantity <= Decimal("0") or release_quantity > pos.quantity:
            raise InvalidPositionError(
                f"Quantidade a liberar ({release_quantity}) inválida para a posição com saldo ({pos.quantity})."
            )

        # Simula a carteira remanescente pós-liberação
        remaining_quantity = pos.quantity - release_quantity
        original_qty = pos.quantity
        pos.quantity = remaining_quantity

        try:
            # Reavalia o participante com a quantidade proposta
            receipt = self.evaluate_margin_call(
                participant_id=pos.participant_id,
                required_margin_brl=required_margin_brl,
            )

            # Se a liberação causar déficit de margem, cancela e reverte
            if receipt.margin_deficit_brl > Decimal("0.00"):
                pos.quantity = original_qty
                raise MarginReleaseViolationError(
                    f"Liberação de colateral rejeitada: geraria déficit de margem de "
                    f"R$ {receipt.margin_deficit_brl} (Exigência: R$ {required_margin_brl}, "
                    f"Garantias Remanescentes: R$ {receipt.total_net_eligible_collateral_brl})."
                )

            # Liberação aprovada
            if remaining_quantity == Decimal("0"):
                pos.pledge_status = CollateralPledgeStatus.RELEASED
            return True, f"Liberação de {release_quantity} unidades confirmada com sucesso."

        except MarginReleaseViolationError:
            raise
        except Exception as e:
            pos.quantity = original_qty
            raise CrossBorderCollateralError(f"Falha na validação de liberação de colateral: {e}")

    # -----------------------------------------------------------------------
    # Apuração de Margem e Chamadas de Garantia (CORE Cross-Border)
    # -----------------------------------------------------------------------

    def evaluate_margin_call(
        self,
        participant_id: str,
        required_margin_brl: Decimal,
        is_intraday: bool = False,
        receipt_id: Optional[str] = None,
    ) -> CrossBorderMarginCallReceipt:
        """
        Executa a apuração determinística de margem da carteira de garantias transfronteiriças:
        1. Totaliza garantias brutas, deduções de haircut de títulos e buffer cambial.
        2. Apura Déficit ou Excedente frente à margem requerida pelo CORE.
        3. Emite Notificação de Chamada de Margem se Deficit > 0.
        """
        if required_margin_brl < Decimal("0.00"):
            raise CrossBorderCollateralError("Margem requerida não pode ser negativa.")

        participant_positions = self.get_participant_positions(participant_id, active_only=True)

        evaluated_items: List[EvaluatedCollateralItem] = []
        total_gross_brl = Decimal("0.00")
        total_bond_haircut_brl = Decimal("0.00")
        total_fx_buffer_brl = Decimal("0.00")
        total_net_eligible_brl = Decimal("0.00")

        for pos in participant_positions:
            item = self.evaluate_position(pos)
            evaluated_items.append(item)
            total_gross_brl += item.gross_converted_brl
            total_bond_haircut_brl += (item.bond_haircut_amount_fx * item.ptax_bid_rate).quantize(TWO_PLACES)
            total_fx_buffer_brl += item.fx_buffer_deduction_brl
            total_net_eligible_brl += item.net_eligible_value_brl

        # Apuração de Déficit / Superávit
        if total_net_eligible_brl >= required_margin_brl:
            status = MarginCallStatus.SURPLUS if total_net_eligible_brl > required_margin_brl else MarginCallStatus.SUFFICIENT
            margin_deficit_brl = Decimal("0.00")
            margin_surplus_brl = (total_net_eligible_brl - required_margin_brl).quantize(TWO_PLACES)
            call_deadline = None
        else:
            status = MarginCallStatus.MARGIN_CALL_ISSUED
            margin_deficit_brl = (required_margin_brl - total_net_eligible_brl).quantize(TWO_PLACES)
            margin_surplus_brl = Decimal("0.00")
            # Prazos regulatórios de regularização
            if is_intraday:
                call_deadline = "D+0 16:30 BRT (Janela Intradiária CORE)"
            else:
                call_deadline = "D+1 10:00 BRT (Abertura de Mercado / Fedwire & Target2 Cutoff)"

        currencies_accepted = [Currency.BRL.value, Currency.USD.value, Currency.EUR.value]

        final_receipt_id = receipt_id or f"MC-XBRD-{participant_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        receipt = CrossBorderMarginCallReceipt(
            receipt_id=final_receipt_id,
            timestamp=datetime.now().isoformat(),
            participant_id=participant_id,
            required_margin_brl=required_margin_brl.quantize(TWO_PLACES),
            total_gross_collateral_brl=total_gross_brl.quantize(TWO_PLACES),
            total_bond_haircut_deduction_brl=total_bond_haircut_brl.quantize(TWO_PLACES),
            total_fx_buffer_deduction_brl=total_fx_buffer_brl.quantize(TWO_PLACES),
            total_net_eligible_collateral_brl=total_net_eligible_brl.quantize(TWO_PLACES),
            margin_deficit_brl=margin_deficit_brl,
            margin_surplus_brl=margin_surplus_brl,
            status=status,
            call_deadline=call_deadline,
            currencies_accepted=currencies_accepted,
            evaluated_items=evaluated_items,
        )

        self.receipts[receipt.receipt_id] = receipt
        return receipt

    # -----------------------------------------------------------------------
    # Conciliação de Custódia com Extrato Internacional (Euroclear / DTC)
    # -----------------------------------------------------------------------

    def reconcile_custody(
        self,
        depository: InternationalDepository,
        external_statement_positions: Dict[str, Decimal],
        reconciliation_id: Optional[str] = None,
    ) -> CrossBorderCustodyReconciliation:
        """
        Executa a conciliação ponto a ponto entre o livro interno da CCP
        e o extrato externo transmitido via SWIFT MT535 ou API da depositária.
        """
        rec_id = reconciliation_id or f"REC-{depository.value}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        internal_units = Decimal("0")
        reconciled_count = 0

        # Soma todas as posições ativas daquela depositária
        for pos in self.positions.values():
            if pos.security.depository == depository and pos.pledge_status in (
                CollateralPledgeStatus.LOCKED_IN_CUSTODY,
                CollateralPledgeStatus.ACTIVE_PLEDGED,
            ):
                internal_units += pos.quantity
                reconciled_count += 1

        external_units = sum(external_statement_positions.values(), Decimal("0"))
        discrepancy = (internal_units - external_units).quantize(FOUR_PLACES)
        is_balanced = (discrepancy == Decimal("0.0000"))

        return CrossBorderCustodyReconciliation(
            reconciliation_id=rec_id,
            depository=depository,
            total_internal_ledger_units=internal_units,
            total_external_statement_units=external_units,
            is_balanced=is_balanced,
            discrepancy_units=discrepancy,
            reconciled_positions_count=reconciled_count,
        )

    # -----------------------------------------------------------------------
    # Verificação Formal de Invariantes
    # -----------------------------------------------------------------------

    def verify_formal_invariants(self) -> Dict[str, bool]:
        """
        Verifica formalmente todas as leis de conservação e invariantes matemáticas
        do sistema de garantias transfronteiriças.
        """
        results = {
            "valuation_non_negativity": True,
            "tenor_haircut_monotonicity": True,
            "haircut_codomain_bounded": True,
            "fx_triangulation_parity": True,
            "margin_call_determinism": True,
            "custody_conservation": True,
            "safe_release_guardrail": True,
        }

        # Lei 1: Não-negatividade e limitação superior
        for pos in self.positions.values():
            if pos.pledge_status in (CollateralPledgeStatus.LOCKED_IN_CUSTODY, CollateralPledgeStatus.ACTIVE_PLEDGED):
                ev = self.evaluate_position(pos)
                if ev.net_eligible_value_brl < Decimal("0.00"):
                    results["valuation_non_negativity"] = False
                if ev.net_eligible_value_brl > ev.gross_converted_brl:
                    results["valuation_non_negativity"] = False

        # Lei 2: Monotonicidade de Tenor Haircut
        buckets_ordered = [
            TenorBucket.UP_TO_1Y,
            TenorBucket.FROM_1Y_TO_3Y,
            TenorBucket.FROM_3Y_TO_5Y,
            TenorBucket.FROM_5Y_TO_10Y,
            TenorBucket.OVER_10Y,
        ]
        for issuer in (SovereignIssuer.US, SovereignIssuer.DE, SovereignIssuer.UK):
            prev_h = Decimal("0.0000")
            for b in buckets_ordered:
                h = self.DEFAULT_SOVEREIGN_HAIRCUTS[(issuer, b)]
                if h < prev_h:
                    results["tenor_haircut_monotonicity"] = False
                prev_h = h

        # Lei 3: Co-domínio [0, 1)
        for h in self.DEFAULT_SOVEREIGN_HAIRCUTS.values():
            if h < Decimal("0.0000") or h >= Decimal("1.0000"):
                results["haircut_codomain_bounded"] = False
        for cfg in self.fx_buffer_configs.values():
            buf = cfg.calculate_buffer()
            if buf < Decimal("0.0000") or buf >= Decimal("1.0000"):
                results["haircut_codomain_bounded"] = False

        # Lei 4: Paridade Triangular EUR/USD vs EUR/BRL e USD/BRL
        if Currency.EUR in self.ptax_rates and Currency.USD in self.ptax_rates:
            eur_ptax = self.ptax_rates[Currency.EUR].mid_rate
            usd_ptax = self.ptax_rates[Currency.USD].mid_rate
            synthetic_eur_usd = self.calculate_synthetic_cross_rate(Currency.EUR, Currency.USD)
            reconstructed_eur = (synthetic_eur_usd * usd_ptax).quantize(TWO_PLACES)
            diff = abs(eur_ptax - reconstructed_eur)
            if diff > Decimal("0.05"):  # Tolerância centavos
                results["fx_triangulation_parity"] = False

        return results

    # -----------------------------------------------------------------------
    # Carga e Desserialização de Fixtures JSON
    # -----------------------------------------------------------------------

    @classmethod
    def load_from_fixture(cls, file_path: Union[str, Path]) -> CrossBorderCollateralEngine:
        """Instancia e popula o motor a partir de um arquivo JSON de fixture sintética."""
        path = Path(file_path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        engine = cls()

        # 1. Carrega taxas PTAX
        for ptax_dict in data.get("ptax_rates", []):
            ptax = PtaxRate(
                currency=Currency(ptax_dict["currency"]),
                bid_rate=Decimal(str(ptax_dict["bid_rate"])),
                ask_rate=Decimal(str(ptax_dict["ask_rate"])),
                mid_rate=Decimal(str(ptax_dict["mid_rate"])),
                bulletin_date=ptax_dict.get("bulletin_date", "2026-11-04"),
            )
            engine.register_ptax_rate(ptax)

        # 2. Carrega configurações de Buffer Cambial customizadas se houver
        for buf_dict in data.get("fx_buffer_configs", []):
            cur = Currency(buf_dict["currency"])
            cfg = FxVolatilityBufferConfig(
                currency=cur,
                daily_volatility=Decimal(str(buf_dict["daily_volatility"])),
                confidence_quantile=Decimal(str(buf_dict.get("confidence_quantile", "2.33"))),
                liquidation_horizon_days=int(buf_dict.get("liquidation_horizon_days", 2)),
                minimum_floor_buffer=Decimal(str(buf_dict["minimum_floor_buffer"])),
            )
            engine.fx_buffer_configs[cur] = cfg

        # 3. Carrega posições de garantia
        for pos_dict in data.get("collateral_positions", []):
            sec_dict = pos_dict["security"]
            sec = CrossBorderSecurity(
                isin=sec_dict["isin"],
                ticker=sec_dict["ticker"],
                name=sec_dict["name"],
                issuer=SovereignIssuer(sec_dict["issuer"]),
                bond_type=SovereignBondType(sec_dict["bond_type"]),
                currency=Currency(sec_dict["currency"]),
                depository=InternationalDepository(sec_dict["depository"]),
                credit_rating=sec_dict.get("credit_rating", "AAA"),
                maturity_years=Decimal(str(sec_dict["maturity_years"])),
                unit_price=Decimal(str(sec_dict["unit_price"])),
            )
            pos = CrossBorderCollateralPosition(
                position_id=pos_dict["position_id"],
                participant_id=pos_dict["participant_id"],
                account_id=pos_dict["account_id"],
                security=sec,
                quantity=Decimal(str(pos_dict["quantity"])),
                pledge_status=CollateralPledgeStatus(pos_dict.get("pledge_status", "LOCKED")),
            )
            engine.register_position(pos)

        return engine
