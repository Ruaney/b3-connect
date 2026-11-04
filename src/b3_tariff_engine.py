"""Motor de Tarifação Integrada de Negociação e Pós-Trade da B3 (FL-053).

Implementa os padrões determinísticos de tarifação oficial da B3 S.A. para o
Mercado à Vista (Ações, BDRs, ETFs e Units) e Derivativos no PUMA Trading System
e na Câmara B3 (CCP), em conformidade com:
1. Resolução CVM nº 135/2022 (governança de mercados regulamentados).
2. Regulamento e Manual de Procedimentos Operacionais da Câmara B3.
3. Política oficial de tarifação de Renda Variável (Escadinha Regressiva ADTV Tiers 1 a 7).
4. Desconto especial para operações intradiárias (Day-Trade).
5. Descontos e isenções regulatórias para Investidores Não Residentes (Resolução CMN 4.373/2014),
   Fundos de Investimento (Resolução CVM 175/2022) e Formadores de Mercado (PMM - FL-046).

Zero dependências externas (100% Python Standard Library).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


# ==============================================================================
# 1. EXCEÇÕES CUSTOMIZADAS DO MOTOR TARIFÁRIO
# ==============================================================================

class B3TariffError(Exception):
    """Exceção base para o subsistema de tarifação B3."""


class TariffValidationError(B3TariffError):
    """Disparada quando dados de entrada ou parâmetros da execução são inválidos."""


class TariffIntegrityError(B3TariffError):
    """Disparada quando o fechamento tarifário diário viola leis de conservação ou integridade."""


# ==============================================================================
# 2. ENUMERAÇÕES REGULATÓRIAS B3
# ==============================================================================

class InvestorCategory(str, Enum):
    """Classificação regulatória do comitente/investidor na B3."""

    INDIVIDUAL = "INDIVIDUAL"  # Pessoa Física / Varejo
    INSTITUTIONAL = "INSTITUTIONAL"  # Fundos CVM 175, Entidades de Previdência e Seguradoras
    NON_RESIDENT = "NON_RESIDENT"  # Investidor Não Residente (Resolução CMN nº 4.373)
    FINANCIAL_INSTITUTION = "FINANCIAL_INSTITUTION"  # Carteira Própria / Bancos e Corretoras
    MARKET_MAKER = "MARKET_MAKER"  # Formador de Mercado Credenciado (PMM - FL-046)


class TradingModality(str, Enum):
    """Modalidade operacional de liquidação do negócio no pregão."""

    REGULAR = "REGULAR"  # Operação normal / swing trade / posição
    DAY_TRADE = "DAY_TRADE"  # Day-Trade (compra e venda no mesmo dia, ativo e corretora)


class AdtvTier(str, Enum):
    """Faixas regressivas de Volume Médio Diário (ADTV) para Renda Variável B3."""

    TIER_1 = "TIER_1"  # Até R$ 1.000.000,00
    TIER_2 = "TIER_2"  # De R$ 1.000.000,01 até R$ 5.000.000,00
    TIER_3 = "TIER_3"  # De R$ 5.000.000,01 até R$ 20.000.000,00
    TIER_4 = "TIER_4"  # De R$ 20.000.000,01 até R$ 50.000.000,00
    TIER_5 = "TIER_5"  # De R$ 50.000.000,01 até R$ 100.000.000,00
    TIER_6 = "TIER_6"  # De R$ 100.000.000,01 até R$ 300.000.000,00
    TIER_7 = "TIER_7"  # Acima de R$ 300.000.000,00

    @classmethod
    def from_volume(cls, volume: Union[Decimal, float, int, str]) -> AdtvTier:
        """Determina a faixa de ADTV a partir do volume médio diário em R$."""
        vol = _to_decimal(volume)
        if vol < Decimal("0.00"):
            raise TariffValidationError(f"Volume ADTV não pode ser negativo: {vol}")

        if vol <= Decimal("1000000.00"):
            return cls.TIER_1
        elif vol <= Decimal("5000000.00"):
            return cls.TIER_2
        elif vol <= Decimal("20000000.00"):
            return cls.TIER_3
        elif vol <= Decimal("50000000.00"):
            return cls.TIER_4
        elif vol <= Decimal("100000000.00"):
            return cls.TIER_5
        elif vol <= Decimal("300000000.00"):
            return cls.TIER_6
        else:
            return cls.TIER_7

    @property
    def volume_limits(self) -> Tuple[Decimal, Optional[Decimal]]:
        """Retorna (limite_inferior, limite_superior) da faixa em R$."""
        limits_map = {
            AdtvTier.TIER_1: (Decimal("0.00"), Decimal("1000000.00")),
            AdtvTier.TIER_2: (Decimal("1000000.01"), Decimal("5000000.00")),
            AdtvTier.TIER_3: (Decimal("5000000.01"), Decimal("20000000.00")),
            AdtvTier.TIER_4: (Decimal("20000000.01"), Decimal("50000000.00")),
            AdtvTier.TIER_5: (Decimal("50000000.01"), Decimal("100000000.00")),
            AdtvTier.TIER_6: (Decimal("100000000.01"), Decimal("300000000.00")),
            AdtvTier.TIER_7: (Decimal("300000000.01"), None),
        }
        return limits_map[self]


# ==============================================================================
# 3. TABELA OFICIAL DE ALÍQUOTAS B3 (ESCADINHA REGRESSIVA)
# ==============================================================================

# Alíquotas oficiais de Renda Variável para Mercado à Vista (Ações, BDRs, ETFs e Units)
# Formato: alíquota unitária (ex: 0,0050% = Decimal("0.000050"))
B3_STANDARD_TARIFF_RATES: Dict[AdtvTier, Dict[TradingModality, Dict[str, Decimal]]] = {
    AdtvTier.TIER_1: {
        TradingModality.REGULAR: {
            "trading": Decimal("0.000050"),    # 0,0050%
            "settlement": Decimal("0.000250"), # 0,0250%
            "registration": Decimal("0.000000"),
        },
        TradingModality.DAY_TRADE: {
            "trading": Decimal("0.000030"),    # 0,0030%
            "settlement": Decimal("0.000170"), # 0,0170%
            "registration": Decimal("0.000000"),
        },
    },
    AdtvTier.TIER_2: {
        TradingModality.REGULAR: {
            "trading": Decimal("0.000045"),    # 0,0045%
            "settlement": Decimal("0.000230"), # 0,0230%
            "registration": Decimal("0.000000"),
        },
        TradingModality.DAY_TRADE: {
            "trading": Decimal("0.000027"),    # 0,0027%
            "settlement": Decimal("0.000153"), # 0,0153%
            "registration": Decimal("0.000000"),
        },
    },
    AdtvTier.TIER_3: {
        TradingModality.REGULAR: {
            "trading": Decimal("0.000040"),    # 0,0040%
            "settlement": Decimal("0.000210"), # 0,0210%
            "registration": Decimal("0.000000"),
        },
        TradingModality.DAY_TRADE: {
            "trading": Decimal("0.000024"),    # 0,0024%
            "settlement": Decimal("0.000136"), # 0,0136%
            "registration": Decimal("0.000000"),
        },
    },
    AdtvTier.TIER_4: {
        TradingModality.REGULAR: {
            "trading": Decimal("0.000035"),    # 0,0035%
            "settlement": Decimal("0.000190"), # 0,0190%
            "registration": Decimal("0.000000"),
        },
        TradingModality.DAY_TRADE: {
            "trading": Decimal("0.000021"),    # 0,0021%
            "settlement": Decimal("0.000119"), # 0,0119%
            "registration": Decimal("0.000000"),
        },
    },
    AdtvTier.TIER_5: {
        TradingModality.REGULAR: {
            "trading": Decimal("0.000030"),    # 0,0030%
            "settlement": Decimal("0.000170"), # 0,0170%
            "registration": Decimal("0.000000"),
        },
        TradingModality.DAY_TRADE: {
            "trading": Decimal("0.000018"),    # 0,0018%
            "settlement": Decimal("0.000102"), # 0,0102%
            "registration": Decimal("0.000000"),
        },
    },
    AdtvTier.TIER_6: {
        TradingModality.REGULAR: {
            "trading": Decimal("0.000025"),    # 0,0025%
            "settlement": Decimal("0.000150"), # 0,0150%
            "registration": Decimal("0.000000"),
        },
        TradingModality.DAY_TRADE: {
            "trading": Decimal("0.000015"),    # 0,0015%
            "settlement": Decimal("0.000085"), # 0,0085%
            "registration": Decimal("0.000000"),
        },
    },
    AdtvTier.TIER_7: {
        TradingModality.REGULAR: {
            "trading": Decimal("0.000020"),    # 0,0020%
            "settlement": Decimal("0.000130"), # 0,0130%
            "registration": Decimal("0.000000"),
        },
        TradingModality.DAY_TRADE: {
            "trading": Decimal("0.000012"),    # 0,0012%
            "settlement": Decimal("0.000068"), # 0,0068%
            "registration": Decimal("0.000000"),
        },
    },
}


# ==============================================================================
# 4. FUNÇÕES UTILITÁRIAS
# ==============================================================================

def _to_decimal(val: Any) -> Decimal:
    """Converte valor para Decimal de forma determinística."""
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val))


def quantize_currency(val: Decimal) -> Decimal:
    """Arredonda valor monetário em R$ para 2 casas decimais (ROUND_HALF_UP)."""
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_datetime(val: Union[str, datetime]) -> datetime:
    """Converte string ISO-8601 ou objeto datetime para datetime timezone-aware (UTC)."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    cleaned = str(val).strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ==============================================================================
# 5. MODELOS DE DADOS DA EXECUÇÃO E EXTRATO TARIFÁRIO
# ==============================================================================

@dataclass
class TradeTariffExecution:
    """Representa a tarifação calculada para uma execução individual no PUMA/Câmara B3."""

    trade_id: str
    symbol: str
    side: str  # "BUY" ou "SELL"
    quantity: Decimal
    price: Decimal
    financial_volume: Decimal
    investor_id: str
    investor_category: InvestorCategory
    broker_id: str
    modality: TradingModality
    adtv_tier: AdtvTier
    timestamp: datetime
    trading_fee_rate: Decimal
    settlement_fee_rate: Decimal
    registration_fee_rate: Decimal
    trading_fee: Decimal
    settlement_fee: Decimal
    registration_fee: Decimal
    total_tariff: Decimal
    day_trade_savings: Decimal = field(default=Decimal("0.00"))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Converte a execução tarifada para dicionário com campos serializáveis."""
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": int(self.quantity) if self.quantity == self.quantity.to_integral() else str(self.quantity),
            "price": str(self.price),
            "financial_volume": str(self.financial_volume),
            "investor_id": self.investor_id,
            "investor_category": self.investor_category.value,
            "broker_id": self.broker_id,
            "modality": self.modality.value,
            "adtv_tier": self.adtv_tier.value,
            "timestamp": self.timestamp.isoformat(),
            "trading_fee_rate": str(self.trading_fee_rate),
            "settlement_fee_rate": str(self.settlement_fee_rate),
            "registration_fee_rate": str(self.registration_fee_rate),
            "trading_fee": str(self.trading_fee),
            "settlement_fee": str(self.settlement_fee),
            "registration_fee": str(self.registration_fee),
            "total_tariff": str(self.total_tariff),
            "day_trade_savings": str(self.day_trade_savings),
            "metadata": self.metadata,
        }


@dataclass
class DailyTariffSummary:
    """Extrato tarifário consolidado diário com conferência de integridade de fechamento."""

    trading_date: date
    total_trades: int
    total_financial_volume: Decimal
    regular_financial_volume: Decimal
    day_trade_financial_volume: Decimal
    total_trading_fee: Decimal
    total_settlement_fee: Decimal
    total_registration_fee: Decimal
    total_tariff: Decimal
    total_day_trade_savings: Decimal
    trades_by_tier: Dict[str, int]
    trades_by_modality: Dict[str, int]
    volume_by_symbol: Dict[str, Decimal]
    executions: List[TradeTariffExecution]
    participant_id: Optional[str] = None
    investor_id: Optional[str] = None

    def verify_integrity(self) -> bool:
        """Valida formalmente todas as equações contábeis de fechamento do extrato."""
        if self.total_trades != len(self.executions):
            raise TariffIntegrityError(
                f"Contagem de negócios diverge: {self.total_trades} declarado vs {len(self.executions)} na lista"
            )

        calc_total_volume = sum((t.financial_volume for t in self.executions), Decimal("0.00"))
        calc_reg_volume = sum(
            (t.financial_volume for t in self.executions if t.modality == TradingModality.REGULAR),
            Decimal("0.00"),
        )
        calc_dt_volume = sum(
            (t.financial_volume for t in self.executions if t.modality == TradingModality.DAY_TRADE),
            Decimal("0.00"),
        )

        if self.total_financial_volume != calc_total_volume:
            raise TariffIntegrityError(
                f"Volume total diverge: {self.total_financial_volume} vs somatório {calc_total_volume}"
            )

        if (self.regular_financial_volume + self.day_trade_financial_volume) != self.total_financial_volume:
            raise TariffIntegrityError(
                f"Equação de conservação de volume violada: "
                f"Reg ({self.regular_financial_volume}) + DT ({self.day_trade_financial_volume}) "
                f"!= Total ({self.total_financial_volume})"
            )

        if self.regular_financial_volume != calc_reg_volume or self.day_trade_financial_volume != calc_dt_volume:
            raise TariffIntegrityError("Decomposição de volume entre Regular e Day-Trade diverge do somatório real")

        calc_trading_fee = sum((t.trading_fee for t in self.executions), Decimal("0.00"))
        calc_settlement_fee = sum((t.settlement_fee for t in self.executions), Decimal("0.00"))
        calc_reg_fee = sum((t.registration_fee for t in self.executions), Decimal("0.00"))
        calc_total_tariff = sum((t.total_tariff for t in self.executions), Decimal("0.00"))
        calc_savings = sum((t.day_trade_savings for t in self.executions), Decimal("0.00"))

        if self.total_trading_fee != calc_trading_fee:
            raise TariffIntegrityError(
                f"Emolumentos divergem: {self.total_trading_fee} declarado vs somatório {calc_trading_fee}"
            )
        if self.total_settlement_fee != calc_settlement_fee:
            raise TariffIntegrityError(
                f"Taxa de liquidação diverge: {self.total_settlement_fee} vs somatório {calc_settlement_fee}"
            )
        if self.total_registration_fee != calc_reg_fee:
            raise TariffIntegrityError(
                f"Taxa de registro diverge: {self.total_registration_fee} vs somatório {calc_reg_fee}"
            )

        if self.total_tariff != (self.total_trading_fee + self.total_settlement_fee + self.total_registration_fee):
            raise TariffIntegrityError(
                f"Equação de composição de tarifa violada: "
                f"{self.total_tariff} != {self.total_trading_fee} + {self.total_settlement_fee} + {self.total_registration_fee}"
            )

        if self.total_tariff != calc_total_tariff:
            raise TariffIntegrityError(
                f"Soma de tarifas individuais {calc_total_tariff} diverge do total declarado {self.total_tariff}"
            )

        if self.total_day_trade_savings != calc_savings:
            raise TariffIntegrityError(
                f"Soma de economia Day-Trade diverge: {self.total_day_trade_savings} vs {calc_savings}"
            )

        return True

    def to_dict(self) -> Dict[str, Any]:
        """Converte o extrato consolidado para dicionário serializável."""
        return {
            "trading_date": self.trading_date.isoformat(),
            "participant_id": self.participant_id,
            "investor_id": self.investor_id,
            "total_trades": self.total_trades,
            "total_financial_volume": str(self.total_financial_volume),
            "regular_financial_volume": str(self.regular_financial_volume),
            "day_trade_financial_volume": str(self.day_trade_financial_volume),
            "total_trading_fee": str(self.total_trading_fee),
            "total_settlement_fee": str(self.settlement_fee_formatted()),
            "total_registration_fee": str(self.total_registration_fee),
            "total_tariff": str(self.total_tariff),
            "total_day_trade_savings": str(self.total_day_trade_savings),
            "trades_by_tier": self.trades_by_tier,
            "trades_by_modality": self.trades_by_modality,
            "volume_by_symbol": {k: str(v) for k, v in self.volume_by_symbol.items()},
            "executions": [t.to_dict() for t in self.executions],
        }

    def settlement_fee_formatted(self) -> Decimal:
        return self.total_settlement_fee


# ==============================================================================
# 6. MOTOR PRINCIPAL DE TARIFAÇÃO B3
# ==============================================================================

class B3TariffEngine:
    """Motor de cálculo de tarifas, desconto regressivo ADTV e casamento de Day-Trade."""

    def __init__(
        self,
        custom_rates: Optional[Dict[AdtvTier, Dict[TradingModality, Dict[str, Decimal]]]] = None,
        non_resident_settlement_discount_pct: Decimal = Decimal("20.00"),
    ) -> None:
        """Inicializa o motor tarifário com alíquotas oficiais B3 e regras regulatórias."""
        self.rates = custom_rates or B3_STANDARD_TARIFF_RATES
        self.non_resident_settlement_discount_pct = _to_decimal(non_resident_settlement_discount_pct)
        self._investors_registry: Dict[str, Dict[str, Any]] = {}

    def register_investor(
        self,
        investor_id: str,
        name: str,
        category: Union[InvestorCategory, str],
        monthly_adtv: Union[Decimal, float, int, str],
    ) -> AdtvTier:
        """Cadastra investidor e seu ADTV histórico mensal, retornando a faixa correspondente."""
        inv_cat = InvestorCategory(category) if isinstance(category, str) else category
        adtv_dec = _to_decimal(monthly_adtv)
        tier = AdtvTier.from_volume(adtv_dec)

        self._investors_registry[investor_id] = {
            "name": name,
            "category": inv_cat,
            "monthly_adtv": adtv_dec,
            "tier": tier,
        }
        return tier

    def get_investor_profile(self, investor_id: str) -> Optional[Dict[str, Any]]:
        """Retorna os dados cadastrais e de faixa do investidor, se registrado."""
        return self._investors_registry.get(investor_id)

    def resolve_rates(
        self,
        tier: AdtvTier,
        modality: TradingModality,
        category: InvestorCategory,
    ) -> Tuple[Decimal, Decimal, Decimal]:
        """Resolve as alíquotas efetivas de (negociação, liquidação, registro).

        Aplica isenções regulatórias:
        - MARKET_MAKER: 100% de isenção em negociação e liquidação (FL-046).
        - NON_RESIDENT: desconto de 20% na taxa de liquidação (Resolução CMN 4.373).
        - INSTITUTIONAL: taxa de registro zerada em mercado à vista.
        """
        base_rates = self.rates[tier][modality]
        trading_rate = base_rates["trading"]
        settlement_rate = base_rates["settlement"]
        reg_rate = base_rates.get("registration", Decimal("0.000000"))

        if category == InvestorCategory.MARKET_MAKER:
            # Isenção integral de formador de mercado
            return Decimal("0.000000"), Decimal("0.000000"), Decimal("0.000000")

        if category == InvestorCategory.NON_RESIDENT:
            # Resolução CMN 4.373: desconto na taxa de liquidação
            discount_factor = (Decimal("100.00") - self.non_resident_settlement_discount_pct) / Decimal("100.00")
            settlement_rate = (settlement_rate * discount_factor)

        if category == InvestorCategory.INSTITUTIONAL:
            # Resolução CVM 175: isenção de registro em à vista
            reg_rate = Decimal("0.000000")

        return trading_rate, settlement_rate, reg_rate

    def calculate_trade_tariff(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        quantity: Union[Decimal, int, str],
        price: Union[Decimal, float, str],
        investor_id: str,
        broker_id: str,
        modality: Union[TradingModality, str] = TradingModality.REGULAR,
        investor_category: Optional[Union[InvestorCategory, str]] = None,
        adtv_tier: Optional[Union[AdtvTier, str]] = None,
        timestamp: Optional[Union[datetime, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TradeTariffExecution:
        """Calcula deterministicamente a tarifação de um negócio no padrão da B3."""
        qty = _to_decimal(quantity)
        prc = _to_decimal(price)

        if qty <= Decimal("0.00"):
            raise TariffValidationError(f"Quantidade do negócio deve ser estritamente positiva: {qty}")
        if prc <= Decimal("0.00"):
            raise TariffValidationError(f"Preço do negócio deve ser estritamente positivo: {prc}")

        norm_side = side.upper().strip()
        if norm_side not in ("BUY", "SELL", "C", "V"):
            raise TariffValidationError(f"Lado da operação inválido (esperado BUY/SELL/C/V): {side}")
        canonical_side = "BUY" if norm_side in ("BUY", "C") else "SELL"

        norm_modality = TradingModality(modality) if isinstance(modality, str) else modality

        # Resolução de Categoria e Tier a partir do cadastro do investidor ou parâmetros
        profile = self.get_investor_profile(investor_id)

        if investor_category is not None:
            cat = InvestorCategory(investor_category) if isinstance(investor_category, str) else investor_category
        elif profile is not None:
            cat = profile["category"]
        else:
            cat = InvestorCategory.INDIVIDUAL

        if adtv_tier is not None:
            resolved_tier = AdtvTier(adtv_tier) if isinstance(adtv_tier, str) else adtv_tier
        elif profile is not None:
            resolved_tier = profile["tier"]
        else:
            resolved_tier = AdtvTier.TIER_1

        ts = parse_datetime(timestamp) if timestamp is not None else datetime.now(timezone.utc)

        # Volume financeiro bruto
        financial_volume = quantize_currency(qty * prc)

        # Resolução de alíquotas efetivas
        trading_rate, settlement_rate, reg_rate = self.resolve_rates(resolved_tier, norm_modality, cat)

        # Cálculo das parcelas monetárias com arredondamento comercial oficial
        trading_fee = quantize_currency(financial_volume * trading_rate)
        settlement_fee = quantize_currency(financial_volume * settlement_rate)
        registration_fee = quantize_currency(financial_volume * reg_rate)
        total_tariff = trading_fee + settlement_fee + registration_fee

        # Cálculo de economia por Day-Trade (comparando com o custo que teria em REGULAR)
        day_trade_savings = Decimal("0.00")
        if norm_modality == TradingModality.DAY_TRADE and cat != InvestorCategory.MARKET_MAKER:
            reg_tr_rate, reg_st_rate, reg_rg_rate = self.resolve_rates(
                resolved_tier, TradingModality.REGULAR, cat
            )
            reg_trading_fee = quantize_currency(financial_volume * reg_tr_rate)
            reg_settlement_fee = quantize_currency(financial_volume * reg_st_rate)
            reg_registration_fee = quantize_currency(financial_volume * reg_rg_rate)
            regular_hypothetical_total = reg_trading_fee + reg_settlement_fee + reg_registration_fee
            day_trade_savings = max(Decimal("0.00"), regular_hypothetical_total - total_tariff)

        return TradeTariffExecution(
            trade_id=trade_id,
            symbol=symbol.upper().strip(),
            side=canonical_side,
            quantity=qty,
            price=prc,
            financial_volume=financial_volume,
            investor_id=investor_id,
            investor_category=cat,
            broker_id=broker_id,
            modality=norm_modality,
            adtv_tier=resolved_tier,
            timestamp=ts,
            trading_fee_rate=trading_rate,
            settlement_fee_rate=settlement_rate,
            registration_fee_rate=reg_rate,
            trading_fee=trading_fee,
            settlement_fee=settlement_fee,
            registration_fee=registration_fee,
            total_tariff=total_tariff,
            day_trade_savings=day_trade_savings,
            metadata=metadata or {},
        )

    def match_and_classify_day_trades(
        self,
        raw_trades: Sequence[Dict[str, Any]],
    ) -> List[TradeTariffExecution]:
        """Realiza o casamento intradiário (Day-Trade Netting) no padrão da B3.

        Para cada grupo (investor_id, broker_id, symbol):
        - Soma a quantidade total de compras e de vendas no pregão.
        - Identifica a quantidade máxima casada min(compras, vendas).
        - Desmembra e classifica o volume casado como DAY_TRADE e o excedente como REGULAR.
        """
        # Agrupar negócios por chave de liquidação (investor_id, broker_id, symbol)
        groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        for trade in raw_trades:
            inv = trade["investor_id"]
            brk = trade["broker_id"]
            sym = trade["symbol"].upper().strip()
            key = (inv, brk, sym)
            groups.setdefault(key, []).append(trade)

        executed_tariffs: List[TradeTariffExecution] = []

        for (inv, brk, sym), trades in groups.items():
            # Ordenar cronologicamente
            sorted_trades = sorted(
                trades,
                key=lambda t: parse_datetime(t.get("timestamp", datetime.now(timezone.utc))),
            )

            total_buy_qty = sum(
                (_to_decimal(t["quantity"]) for t in sorted_trades if t.get("side", "").upper() in ("BUY", "C")),
                Decimal("0"),
            )
            total_sell_qty = sum(
                (_to_decimal(t["quantity"]) for t in sorted_trades if t.get("side", "").upper() in ("SELL", "V")),
                Decimal("0"),
            )

            matched_dt_qty_available = min(total_buy_qty, total_sell_qty)

            # Consumir quantidade elegível de Day-Trade para compras e vendas
            remaining_dt_buy = matched_dt_qty_available
            remaining_dt_sell = matched_dt_qty_available

            for t in sorted_trades:
                trade_qty = _to_decimal(t["quantity"])
                side = "BUY" if t.get("side", "").upper() in ("BUY", "C") else "SELL"
                price = _to_decimal(t["price"])
                trade_id = t["trade_id"]
                ts = t.get("timestamp")
                cat = t.get("investor_category")
                tier = t.get("adtv_tier")

                # Se a modalidade já foi forçada e fornecida explicitamente no input, respeitamos
                if "modality" in t and t["modality"] is not None:
                    modality = TradingModality(t["modality"])
                    exec_item = self.calculate_trade_tariff(
                        trade_id=trade_id,
                        symbol=sym,
                        side=side,
                        quantity=trade_qty,
                        price=price,
                        investor_id=inv,
                        broker_id=brk,
                        modality=modality,
                        investor_category=cat,
                        adtv_tier=tier,
                        timestamp=ts,
                        metadata=t.get("metadata"),
                    )
                    executed_tariffs.append(exec_item)
                    continue

                # Casamento automático por cota de Day-Trade
                remaining_quota = remaining_dt_buy if side == "BUY" else remaining_dt_sell

                if remaining_quota <= Decimal("0"):
                    # Sem cota de Day-Trade restante: 100% REGULAR
                    exec_item = self.calculate_trade_tariff(
                        trade_id=trade_id,
                        symbol=sym,
                        side=side,
                        quantity=trade_qty,
                        price=price,
                        investor_id=inv,
                        broker_id=brk,
                        modality=TradingModality.REGULAR,
                        investor_category=cat,
                        adtv_tier=tier,
                        timestamp=ts,
                        metadata=t.get("metadata"),
                    )
                    executed_tariffs.append(exec_item)
                elif trade_qty <= remaining_quota:
                    # Todo o lote cabe na cota de Day-Trade
                    if side == "BUY":
                        remaining_dt_buy -= trade_qty
                    else:
                        remaining_dt_sell -= trade_qty

                    exec_item = self.calculate_trade_tariff(
                        trade_id=trade_id,
                        symbol=sym,
                        side=side,
                        quantity=trade_qty,
                        price=price,
                        investor_id=inv,
                        broker_id=brk,
                        modality=TradingModality.DAY_TRADE,
                        investor_category=cat,
                        adtv_tier=tier,
                        timestamp=ts,
                        metadata=t.get("metadata"),
                    )
                    executed_tariffs.append(exec_item)
                else:
                    # O lote precisa ser desmembrado entre Day-Trade e Regular
                    dt_part = remaining_quota
                    reg_part = trade_qty - remaining_quota

                    if side == "BUY":
                        remaining_dt_buy = Decimal("0")
                    else:
                        remaining_dt_sell = Decimal("0")

                    exec_dt = self.calculate_trade_tariff(
                        trade_id=f"{trade_id}-DT",
                        symbol=sym,
                        side=side,
                        quantity=dt_part,
                        price=price,
                        investor_id=inv,
                        broker_id=brk,
                        modality=TradingModality.DAY_TRADE,
                        investor_category=cat,
                        adtv_tier=tier,
                        timestamp=ts,
                        metadata={**(t.get("metadata") or {}), "split_parent": trade_id, "split_role": "DAY_TRADE"},
                    )
                    exec_reg = self.calculate_trade_tariff(
                        trade_id=f"{trade_id}-REG",
                        symbol=sym,
                        side=side,
                        quantity=reg_part,
                        price=price,
                        investor_id=inv,
                        broker_id=brk,
                        modality=TradingModality.REGULAR,
                        investor_category=cat,
                        adtv_tier=tier,
                        timestamp=ts,
                        metadata={**(t.get("metadata") or {}), "split_parent": trade_id, "split_role": "REGULAR"},
                    )
                    executed_tariffs.extend([exec_dt, exec_reg])

        return executed_tariffs

    def generate_daily_summary(
        self,
        executions: Sequence[TradeTariffExecution],
        trading_date: Optional[Union[date, str]] = None,
        participant_id: Optional[str] = None,
        investor_id: Optional[str] = None,
    ) -> DailyTariffSummary:
        """Gera e valida o extrato tarifário consolidado diário com conferência estrita."""
        if not executions:
            raise TariffValidationError("Não é possível gerar extrato diário a partir de uma lista vazia de negócios")

        if trading_date is None:
            t_date = executions[0].timestamp.date()
        elif isinstance(trading_date, str):
            t_date = date.fromisoformat(trading_date)
        else:
            t_date = trading_date

        total_trades = len(executions)
        total_vol = Decimal("0.00")
        regular_vol = Decimal("0.00")
        dt_vol = Decimal("0.00")

        total_trading_fee = Decimal("0.00")
        total_settlement_fee = Decimal("0.00")
        total_reg_fee = Decimal("0.00")
        total_tariff = Decimal("0.00")
        total_savings = Decimal("0.00")

        trades_by_tier: Dict[str, int] = {}
        trades_by_modality: Dict[str, int] = {}
        volume_by_symbol: Dict[str, Decimal] = {}

        for exec_item in executions:
            vol = exec_item.financial_volume
            total_vol += vol

            if exec_item.modality == TradingModality.REGULAR:
                regular_vol += vol
            else:
                dt_vol += vol

            total_trading_fee += exec_item.trading_fee
            total_settlement_fee += exec_item.settlement_fee
            total_reg_fee += exec_item.registration_fee
            total_tariff += exec_item.total_tariff
            total_savings += exec_item.day_trade_savings

            tier_key = exec_item.adtv_tier.value
            trades_by_tier[tier_key] = trades_by_tier.get(tier_key, 0) + 1

            mod_key = exec_item.modality.value
            trades_by_modality[mod_key] = trades_by_modality.get(mod_key, 0) + 1

            sym = exec_item.symbol
            volume_by_symbol[sym] = volume_by_symbol.get(sym, Decimal("0.00")) + vol

        summary = DailyTariffSummary(
            trading_date=t_date,
            total_trades=total_trades,
            total_financial_volume=total_vol,
            regular_financial_volume=regular_vol,
            day_trade_financial_volume=dt_vol,
            total_trading_fee=total_trading_fee,
            total_settlement_fee=total_settlement_fee,
            total_registration_fee=total_reg_fee,
            total_tariff=total_tariff,
            total_day_trade_savings=total_savings,
            trades_by_tier=trades_by_tier,
            trades_by_modality=trades_by_modality,
            volume_by_symbol=volume_by_symbol,
            executions=list(executions),
            participant_id=participant_id,
            investor_id=investor_id,
        )

        # Validação formal das equações de fechamento
        summary.verify_integrity()
        return summary

    def load_fixture_and_process(self, fixture_path: Union[str, Path]) -> DailyTariffSummary:
        """Carrega e executa a fixture JSON de negócios da B3, retornando o extrato consolidado."""
        path = Path(fixture_path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Cadastrar investidores da fixture
        for inv in data.get("registered_investors", []):
            self.register_investor(
                investor_id=inv["investor_id"],
                name=inv["name"],
                category=inv["category"],
                monthly_adtv=inv["monthly_adtv"],
            )

        # Executar negócios
        trades_input = data.get("trades", [])
        executions: List[TradeTariffExecution] = []
        for t in trades_input:
            exec_item = self.calculate_trade_tariff(
                trade_id=t["trade_id"],
                symbol=t["symbol"],
                side=t["side"],
                quantity=t["quantity"],
                price=t["price"],
                investor_id=t["investor_id"],
                broker_id=t["broker_id"],
                modality=t.get("modality", TradingModality.REGULAR),
                investor_category=t.get("investor_category"),
                adtv_tier=t.get("adtv_tier"),
                timestamp=t.get("timestamp"),
                metadata=t.get("metadata"),
            )
            executions.append(exec_item)

        t_date_str = data.get("metadata", {}).get("trading_date", "2026-11-03")
        return self.generate_daily_summary(executions, trading_date=t_date_str)
