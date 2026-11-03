"""Módulo de Garantias em Moeda Estrangeira e Ativos Internacionais (FL-022) — Câmara B3.

Este módulo implementa o modelo de depósito, custódia e avaliação de garantias
multimoeda e internacionais da Câmara B3 (sistema CORE):
- USD em espécie (Cash Collateral mantido em conta de liquidação no exterior / Citibank NY).
- US Treasuries (T-Bills, T-Notes e T-Bonds custodiados via DTCC / Euroclear).
- Ouro físico nacional (Contratos OZ1D de 250g, OZ2D de 10g e OZ3D de 0,225g no cofre central B3).
- BDRs de dívida corporativa e títulos globais.

Metodologia de Haircuts regulatórios:
  H_total = min(H_fx + H_vol + H_liq, 1.00)
Conversão Cambial PTAX e apuração de margem requerida da câmara (CORE).

Zero dependências externas (exclusivamente biblioteca padrão Python).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")


# ---------------------------------------------------------------------------
# Exceções Especializadas
# ---------------------------------------------------------------------------

class ForeignCollateralError(Exception):
    """Exceção base para operações de garantias internacionais e multimoeda."""
    pass


class IneligibleAssetError(ForeignCollateralError):
    """Lançada quando um ativo ou tipo de instrumento não é elegível para depósito."""
    pass


class BlockedCurrencyError(ForeignCollateralError):
    """Lançada quando uma moeda é bloqueada por sanções internacionais ou restrições cambiais."""
    pass


class UnsupportedCurrencyError(ForeignCollateralError):
    """Lançada quando a moeda não é suportada pela Câmara B3."""
    pass


class InvalidDepositDataError(ForeignCollateralError):
    """Lançada quando parâmetros do depósito (quantidade, preço, ISIN) são inválidos."""
    pass


class HaircutConfigurationError(ForeignCollateralError):
    """Lançada quando os parâmetros de haircut violam as faixas regulatórias permitidas."""
    pass


# ---------------------------------------------------------------------------
# Domínios e Tipos Enumerados
# ---------------------------------------------------------------------------

class ForeignAssetType(str, Enum):
    """Tipos de garantias em moeda estrangeira e ouro aceitos pela Câmara B3."""
    USD_CASH = "USD_CASH"
    EUR_CASH = "EUR_CASH"
    US_TREASURY_BILL = "US_TREASURY_BILL"
    US_TREASURY_NOTE = "US_TREASURY_NOTE"
    US_TREASURY_BOND = "US_TREASURY_BOND"
    GOLD_OZ1D = "GOLD_OZ1D"
    GOLD_OZ2D = "GOLD_OZ2D"
    GOLD_OZ3D = "GOLD_OZ3D"
    DEBT_BDR = "DEBT_BDR"

    @property
    def is_cash(self) -> bool:
        """Indica se é moeda em espécie."""
        return self in (ForeignAssetType.USD_CASH, ForeignAssetType.EUR_CASH)

    @property
    def is_us_treasury(self) -> bool:
        """Indica se é título soberano norte-americano."""
        return self in (
            ForeignAssetType.US_TREASURY_BILL,
            ForeignAssetType.US_TREASURY_NOTE,
            ForeignAssetType.US_TREASURY_BOND,
        )

    @property
    def is_gold(self) -> bool:
        """Indica se é contrato padrão de ouro físico B3."""
        return self in (
            ForeignAssetType.GOLD_OZ1D,
            ForeignAssetType.GOLD_OZ2D,
            ForeignAssetType.GOLD_OZ3D,
        )

    @property
    def default_currency(self) -> str:
        """Retorna a moeda nativa de precificação do ativo."""
        if self.is_gold or self == ForeignAssetType.DEBT_BDR:
            return "BRL"
        if self == ForeignAssetType.EUR_CASH:
            return "EUR"
        return "USD"


# ---------------------------------------------------------------------------
# Modelos de Dados Tipados
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CollateralHaircut:
    """Parâmetros e metodologia de Haircut regulatório para um ativo de garantia.

    Decompõe o deságio em risco cambial (fx), volatilidade/duration (vol) e liquidez (liq).
    H_total = min(fx_haircut + volatility_haircut + liquidity_haircut, 1.00)
    """
    tipo_ativo: ForeignAssetType
    fx_haircut: Decimal = field(default=Decimal("0.0000"))
    volatility_haircut: Decimal = field(default=Decimal("0.0000"))
    liquidity_haircut: Decimal = field(default=Decimal("0.0000"))
    total_haircut: Decimal = field(default=Decimal("0.0000"))
    descricao: str = ""

    def __post_init__(self) -> None:
        # Validação de não-negatividade e teto unitário
        for name, val in [
            ("fx_haircut", self.fx_haircut),
            ("volatility_haircut", self.volatility_haircut),
            ("liquidity_haircut", self.liquidity_haircut),
        ]:
            if val < Decimal("0") or val > Decimal("1.0000"):
                raise HaircutConfigurationError(
                    f"Haircut '{name}' deve estar entre 0.00 e 1.00 (valor: {val})."
                )

        calculated = min(
            self.fx_haircut + self.volatility_haircut + self.liquidity_haircut,
            Decimal("1.0000"),
        )
        # Se total_haircut for 0 mas os componentes forem > 0, assume o calculado
        if self.total_haircut == Decimal("0.0000") and calculated > Decimal("0"):
            object.__setattr__(self, "total_haircut", calculated)
        elif self.total_haircut < Decimal("0") or self.total_haircut > Decimal("1.0000"):
            raise HaircutConfigurationError(
                f"Haircut total deve estar entre 0.00 e 1.00 (valor: {self.total_haircut})."
            )

    @property
    def asset_type(self) -> ForeignAssetType:
        """Alias em inglês para compatibilidade."""
        return self.tipo_ativo

    @property
    def description(self) -> str:
        """Alias em inglês para compatibilidade."""
        return self.descricao


@dataclass(frozen=True)
class ForeignCollateralDeposit:
    """Registro individual de depósito de garantia internacional ou ouro."""
    item_id: str
    tipo_ativo: ForeignAssetType
    ticker: str
    isin: str
    quantidade: Decimal
    preco_unitario: Decimal
    moeda: str
    local_custodia: str
    haircut: Optional[CollateralHaircut] = None
    descricao: str = ""
    codigo_participante: Optional[int] = None
    codigo_conta_custodia: Optional[int] = None
    data_deposito: Optional[date] = None

    def __post_init__(self) -> None:
        if self.quantidade <= Decimal("0"):
            raise InvalidDepositDataError(
                f"Quantidade de depósito deve ser estritamente positiva (informado: {self.quantidade})."
            )
        if self.preco_unitario <= Decimal("0"):
            raise InvalidDepositDataError(
                f"Preço unitário do ativo deve ser estritamente positivo (informado: {self.preco_unitario})."
            )
        if not self.ticker or not self.ticker.strip():
            raise InvalidDepositDataError("Ticker do ativo não pode ser vazio.")
        if not self.isin or not self.isin.strip():
            raise InvalidDepositDataError("Código ISIN do ativo não pode ser vazio.")

    @property
    def asset_type(self) -> ForeignAssetType:
        return self.tipo_ativo

    @property
    def quantity(self) -> Decimal:
        return self.quantidade

    @property
    def unit_price(self) -> Decimal:
        return self.preco_unitario

    @property
    def currency(self) -> str:
        return self.moeda

    @property
    def custody_location(self) -> str:
        return self.local_custodia

    @property
    def valor_bruto_moeda_original(self) -> Decimal:
        """Valor financeiro bruto na moeda nativa de precificação."""
        return (self.quantidade * self.preco_unitario).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class CollateralEvaluationResult:
    """Resultado da avaliação financeira e aplicação de haircut para um item de garantia."""
    item_id: str
    tipo_ativo: ForeignAssetType
    ticker: str
    isin: str
    moeda: str
    quantidade: Decimal
    preco_unitario: Decimal
    valor_bruto_moeda: Decimal
    taxa_cambio: Decimal
    valor_bruto_brl: Decimal
    haircut_total: Decimal
    deducao_haircut_brl: Decimal
    valor_liquido_brl: Decimal
    local_custodia: str

    @property
    def asset_type(self) -> ForeignAssetType:
        return self.tipo_ativo

    @property
    def currency(self) -> str:
        return self.moeda

    @property
    def quantity(self) -> Decimal:
        return self.quantidade

    @property
    def unit_price(self) -> Decimal:
        return self.preco_unitario

    @property
    def gross_currency_value(self) -> Decimal:
        return self.valor_bruto_moeda

    @property
    def fx_rate(self) -> Decimal:
        return self.taxa_cambio

    @property
    def gross_brl_value(self) -> Decimal:
        return self.valor_bruto_brl

    @property
    def haircut_pct(self) -> Decimal:
        return self.haircut_total

    @property
    def haircut_deduction_brl(self) -> Decimal:
        return self.deducao_haircut_brl

    @property
    def net_brl_value(self) -> Decimal:
        return self.valor_liquido_brl


@dataclass(frozen=True)
class BasketEvaluationSummary:
    """Relatório consolidado de avaliação da cesta de garantias multimoeda e cobertura CORE."""
    lote_id: str
    codigo_participante: int
    codigo_conta_custodia: int
    data_avaliacao: date
    taxas_cambio: Dict[str, Decimal]
    total_bruto_brl: Decimal
    total_haircut_brl: Decimal
    total_liquido_brl: Decimal
    margem_requerida_brl: Decimal
    saldo_margem_brl: Decimal
    situacao_margem: str  # 'L' (Livre/Coberto) ou 'D' (Defasagem/Chamada)
    itens: List[CollateralEvaluationResult]

    @property
    def is_covered(self) -> bool:
        """Indica se a margem requerida está 100% coberta pelas garantias líquidas."""
        return self.situacao_margem == "L"

    @property
    def is_margin_call(self) -> bool:
        """Indica se há chamada de margem (defasagem)."""
        return self.situacao_margem == "D"

    @property
    def total_gross_brl(self) -> Decimal:
        return self.total_bruto_brl

    @property
    def total_net_brl(self) -> Decimal:
        return self.total_liquido_brl

    @property
    def required_margin_brl(self) -> Decimal:
        return self.margem_requerida_brl

    @property
    def margin_surplus_deficit_brl(self) -> Decimal:
        return self.saldo_margem_brl


# ---------------------------------------------------------------------------
# Motor de Cálculo Determinístico (Engine)
# ---------------------------------------------------------------------------

class ForeignCollateralEngine:
    """Motor determinístico de garantias multimoeda e internacionais da B3."""

    # Moedas expressamente autorizadas pela B3 Clearing
    AUTHORIZED_CURRENCIES: Set[str] = {"BRL", "USD", "EUR"}

    # Moedas bloqueadas por sanções internacionais ou controle cambial restritivo
    BLOCKED_CURRENCIES: Set[str] = {"RUB", "IRR", "KPW", "SYR", "VEF", "CUP", "BYN"}

    def __init__(self) -> None:
        self._haircut_rules: Dict[ForeignAssetType, CollateralHaircut] = {}
        self._initialize_default_haircuts()

    def _initialize_default_haircuts(self) -> None:
        """Registra a grade regulatória padrão de haircuts da Câmara B3."""
        defaults = [
            CollateralHaircut(
                tipo_ativo=ForeignAssetType.USD_CASH,
                fx_haircut=Decimal("0.1000"),
                volatility_haircut=Decimal("0.0000"),
                liquidity_haircut=Decimal("0.0000"),
                total_haircut=Decimal("0.1000"),
                descricao="Haircut cambial padrão B3 de 10% para USD em espécie",
            ),
            CollateralHaircut(
                tipo_ativo=ForeignAssetType.EUR_CASH,
                fx_haircut=Decimal("0.1200"),
                volatility_haircut=Decimal("0.0000"),
                liquidity_haircut=Decimal("0.0000"),
                total_haircut=Decimal("0.1200"),
                descricao="Haircut cambial regulatório B3 de 12% para EUR em espécie",
            ),
            CollateralHaircut(
                tipo_ativo=ForeignAssetType.US_TREASURY_BILL,
                fx_haircut=Decimal("0.0000"),
                volatility_haircut=Decimal("0.0300"),
                liquidity_haircut=Decimal("0.0200"),
                total_haircut=Decimal("0.0500"),
                descricao="Haircut consolidado de T-Bills (<1 ano) de 5%",
            ),
            CollateralHaircut(
                tipo_ativo=ForeignAssetType.US_TREASURY_NOTE,
                fx_haircut=Decimal("0.0200"),
                volatility_haircut=Decimal("0.0600"),
                liquidity_haircut=Decimal("0.0200"),
                total_haircut=Decimal("0.1000"),
                descricao="Haircut para T-Notes (1 a 10 anos) de 10%",
            ),
            CollateralHaircut(
                tipo_ativo=ForeignAssetType.US_TREASURY_BOND,
                fx_haircut=Decimal("0.0300"),
                volatility_haircut=Decimal("0.0800"),
                liquidity_haircut=Decimal("0.0300"),
                total_haircut=Decimal("0.1400"),
                descricao="Haircut para T-Bonds (>10 anos) de 14%",
            ),
            CollateralHaircut(
                tipo_ativo=ForeignAssetType.GOLD_OZ1D,
                fx_haircut=Decimal("0.0000"),
                volatility_haircut=Decimal("0.1200"),
                liquidity_haircut=Decimal("0.0300"),
                total_haircut=Decimal("0.1500"),
                descricao="Haircut de volatilidade e liquidez física B3 para Ouro OZ1D de 15%",
            ),
            CollateralHaircut(
                tipo_ativo=ForeignAssetType.GOLD_OZ2D,
                fx_haircut=Decimal("0.0000"),
                volatility_haircut=Decimal("0.1200"),
                liquidity_haircut=Decimal("0.0300"),
                total_haircut=Decimal("0.1500"),
                descricao="Haircut B3 para Ouro OZ2D de 15%",
            ),
            CollateralHaircut(
                tipo_ativo=ForeignAssetType.GOLD_OZ3D,
                fx_haircut=Decimal("0.0000"),
                volatility_haircut=Decimal("0.1200"),
                liquidity_haircut=Decimal("0.0300"),
                total_haircut=Decimal("0.1500"),
                descricao="Haircut B3 para Ouro OZ3D de 15%",
            ),
            CollateralHaircut(
                tipo_ativo=ForeignAssetType.DEBT_BDR,
                fx_haircut=Decimal("0.0500"),
                volatility_haircut=Decimal("0.0800"),
                liquidity_haircut=Decimal("0.0500"),
                total_haircut=Decimal("0.1800"),
                descricao="Haircut consolidado BDR de dívida internacional de 18%",
            ),
        ]
        for rule in defaults:
            self.register_haircut_rule(rule)

    def register_haircut_rule(self, rule: CollateralHaircut) -> None:
        """Registra ou sobrescreve uma regra de haircut para determinado tipo de ativo."""
        self._haircut_rules[rule.tipo_ativo] = rule

    def get_haircut_rule(self, asset_type: Union[ForeignAssetType, str]) -> CollateralHaircut:
        """Obtém a regra de haircut cadastrada para o tipo de ativo."""
        if isinstance(asset_type, str):
            try:
                asset_type = ForeignAssetType(asset_type)
            except ValueError:
                raise IneligibleAssetError(f"Tipo de ativo não reconhecido: {asset_type}")
        if asset_type not in self._haircut_rules:
            raise HaircutConfigurationError(f"Nenhuma regra de haircut configurada para {asset_type.value}.")
        return self._haircut_rules[asset_type]

    def validate_deposit(self, deposit: ForeignCollateralDeposit) -> None:
        """Valida a elegibilidade jurídica e operacional de um depósito de garantia."""
        currency_norm = deposit.moeda.upper().strip()

        # 1. Checagem de moedas bloqueadas (sanções internacionais)
        if currency_norm in self.BLOCKED_CURRENCIES:
            raise BlockedCurrencyError(
                f"Moeda '{currency_norm}' bloqueada por sanções regulatórias internacionais."
            )

        # 2. Checagem de moedas autorizadas
        if currency_norm not in self.AUTHORIZED_CURRENCIES:
            raise UnsupportedCurrencyError(
                f"Moeda '{currency_norm}' não autorizada pela Câmara B3. Moedas permitidas: {sorted(self.AUTHORIZED_CURRENCIES)}"
            )

        # 3. Validação do tipo de ativo
        if not isinstance(deposit.tipo_ativo, ForeignAssetType):
            try:
                ForeignAssetType(deposit.tipo_ativo)
            except ValueError:
                raise IneligibleAssetError(f"Ativo '{deposit.tipo_ativo}' não elegível como colateral.")

        # 4. Consistência entre moeda e ativo
        if deposit.tipo_ativo.is_gold and currency_norm != "BRL":
            raise InvalidDepositDataError(
                f"Contrato de Ouro físico da B3 ({deposit.tipo_ativo.value}) deve ser precificado em BRL."
            )
        if deposit.tipo_ativo == ForeignAssetType.USD_CASH and currency_norm != "USD":
            raise InvalidDepositDataError(
                f"Depósito de USD_CASH exige moeda USD (informado: {currency_norm})."
            )

    def evaluate_deposit(
        self,
        deposit: ForeignCollateralDeposit,
        fx_rates: Dict[str, Decimal],
    ) -> CollateralEvaluationResult:
        """Avalia deterministamente um item de depósito, convertendo para BRL e aplicando haircut."""
        self.validate_deposit(deposit)

        currency_norm = deposit.moeda.upper().strip()
        if currency_norm == "BRL":
            fx_rate = Decimal("1.0000")
        else:
            if currency_norm not in fx_rates:
                raise InvalidDepositDataError(
                    f"Taxa de câmbio PTAX não fornecida para a moeda '{currency_norm}'."
                )
            fx_rate = fx_rates[currency_norm]
            if fx_rate <= Decimal("0"):
                raise InvalidDepositDataError(
                    f"Taxa de câmbio para '{currency_norm}' deve ser positiva (informado: {fx_rate})."
                )

        # Haircut: utiliza o do próprio depósito se especificado, senão obtém a regra padrão
        haircut_rule = deposit.haircut if deposit.haircut is not None else self.get_haircut_rule(deposit.tipo_ativo)
        haircut_total = haircut_rule.total_haircut

        # Cálculo do valor na moeda original
        gross_currency = (deposit.quantidade * deposit.preco_unitario).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Conversão para BRL
        gross_brl = (gross_currency * fx_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Dedução do haircut e apuração do valor líquido
        deduction_brl = (gross_brl * haircut_total).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        net_brl = gross_brl - deduction_brl

        return CollateralEvaluationResult(
            item_id=deposit.item_id,
            tipo_ativo=deposit.tipo_ativo,
            ticker=deposit.ticker,
            isin=deposit.isin,
            moeda=currency_norm,
            quantidade=deposit.quantidade,
            preco_unitario=deposit.preco_unitario,
            valor_bruto_moeda=gross_currency,
            taxa_cambio=fx_rate,
            valor_bruto_brl=gross_brl,
            haircut_total=haircut_total,
            deducao_haircut_brl=deduction_brl,
            valor_liquido_brl=net_brl,
            local_custodia=deposit.local_custodia,
        )

    def evaluate_basket(
        self,
        deposits: List[ForeignCollateralDeposit],
        fx_rates: Dict[str, Decimal],
        required_margin_brl: Decimal,
        lote_id: str = "LOTE-DEFAULT",
        participant_id: int = 105,
        account_id: int = 987654,
        valuation_date: Optional[date] = None,
    ) -> BasketEvaluationSummary:
        """Avalia uma cesta de depósitos multimoeda frente à margem requerida da Câmara (CORE)."""
        if required_margin_brl < Decimal("0"):
            raise InvalidDepositDataError(
                f"Margem requerida não pode ser negativa (informado: {required_margin_brl})."
            )

        if valuation_date is None:
            valuation_date = date.today()

        evaluated_items: List[CollateralEvaluationResult] = []
        total_gross = Decimal("0.00")
        total_deduction = Decimal("0.00")
        total_net = Decimal("0.00")

        for dep in deposits:
            eval_res = self.evaluate_deposit(dep, fx_rates)
            evaluated_items.append(eval_res)
            total_gross += eval_res.valor_bruto_brl
            total_deduction += eval_res.deducao_haircut_brl
            total_net += eval_res.valor_liquido_brl

        margin_surplus_deficit = total_net - required_margin_brl
        situacao = "L" if margin_surplus_deficit >= Decimal("0.00") else "D"

        return BasketEvaluationSummary(
            lote_id=lote_id,
            codigo_participante=participant_id,
            codigo_conta_custodia=account_id,
            data_avaliacao=valuation_date,
            taxas_cambio={k: Decimal(str(v)) for k, v in fx_rates.items()},
            total_bruto_brl=total_gross,
            total_haircut_brl=total_deduction,
            total_liquido_brl=total_net,
            margem_requerida_brl=required_margin_brl,
            saldo_margem_brl=margin_surplus_deficit,
            situacao_margem=situacao,
            itens=evaluated_items,
        )

    def revalue_intraday(
        self,
        original_deposits: List[ForeignCollateralDeposit],
        updated_fx_rates: Dict[str, Decimal],
        required_margin_brl: Decimal,
        updated_prices: Optional[Dict[str, Decimal]] = None,
        lote_id: str = "LOTE-INTRADAY",
        participant_id: int = 105,
        account_id: int = 987654,
        valuation_date: Optional[date] = None,
    ) -> BasketEvaluationSummary:
        """Executa a reavaliação intradiária da cesta após oscilações de PTAX ou preços de mercado."""
        effective_deposits: List[ForeignCollateralDeposit] = []
        for dep in original_deposits:
            if updated_prices and dep.ticker in updated_prices:
                new_price = updated_prices[dep.ticker]
                effective_deposits.append(
                    ForeignCollateralDeposit(
                        item_id=dep.item_id,
                        tipo_ativo=dep.tipo_ativo,
                        ticker=dep.ticker,
                        isin=dep.isin,
                        quantidade=dep.quantidade,
                        preco_unitario=new_price,
                        moeda=dep.moeda,
                        local_custodia=dep.local_custodia,
                        haircut=dep.haircut,
                        descricao=dep.descricao,
                        codigo_participante=dep.codigo_participante,
                        codigo_conta_custodia=dep.codigo_conta_custodia,
                        data_deposito=dep.data_deposito,
                    )
                )
            else:
                effective_deposits.append(dep)

        return self.evaluate_basket(
            deposits=effective_deposits,
            fx_rates=updated_fx_rates,
            required_margin_brl=required_margin_brl,
            lote_id=lote_id,
            participant_id=participant_id,
            account_id=account_id,
            valuation_date=valuation_date,
        )

    def load_deposit_batch_from_json(
        self,
        json_content_or_dict: Union[str, Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], List[ForeignCollateralDeposit]]:
        """Carrega e desserializa um lote de depósito a partir de JSON ou dicionário Python."""
        if isinstance(json_content_or_dict, str):
            payload = json.loads(json_content_or_dict)
        else:
            payload = json_content_or_dict

        metadata = {
            "lote_id": payload.get("lote_id", "LOTE-DEFAULT"),
            "codigo_participante": int(payload.get("codigo_participante", 0)),
            "codigo_conta_custodia": int(payload.get("codigo_conta_custodia", 0)),
            "data_deposito": (
                datetime.strptime(payload["data_deposito"], "%Y-%m-%d").date()
                if "data_deposito" in payload
                else date.today()
            ),
            "margem_requerida_brl": Decimal(str(payload.get("margem_requerida_brl", "0.00"))),
        }

        deposits: List[ForeignCollateralDeposit] = []
        for raw_item in payload.get("itens", []):
            tipo_ativo = ForeignAssetType(raw_item["tipo_ativo"])
            haircut = None
            if "haircut" in raw_item and raw_item["haircut"]:
                h_data = raw_item["haircut"]
                haircut = CollateralHaircut(
                    tipo_ativo=tipo_ativo,
                    fx_haircut=Decimal(str(h_data.get("fx_haircut", "0.0000"))),
                    volatility_haircut=Decimal(str(h_data.get("volatility_haircut", "0.0000"))),
                    liquidity_haircut=Decimal(str(h_data.get("liquidity_haircut", "0.0000"))),
                    total_haircut=Decimal(str(h_data.get("total_haircut", "0.0000"))),
                    descricao=h_data.get("descricao", ""),
                )

            dep = ForeignCollateralDeposit(
                item_id=raw_item["item_id"],
                tipo_ativo=tipo_ativo,
                ticker=raw_item["ticker"],
                isin=raw_item["isin"],
                quantidade=Decimal(str(raw_item["quantidade"])),
                preco_unitario=Decimal(str(raw_item["preco_unitario"])),
                moeda=raw_item["moeda"],
                local_custodia=raw_item["local_custodia"],
                haircut=haircut,
                descricao=raw_item.get("descricao", ""),
                codigo_participante=metadata["codigo_participante"],
                codigo_conta_custodia=metadata["codigo_conta_custodia"],
                data_deposito=metadata["data_deposito"],
            )
            deposits.append(dep)

        return metadata, deposits

    def load_deposit_batch_from_file(
        self,
        file_path: Union[str, Path],
    ) -> Tuple[Dict[str, Any], List[ForeignCollateralDeposit]]:
        """Carrega e desserializa um lote de garantias a partir do arquivo no disco."""
        path = Path(file_path)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return self.load_deposit_batch_from_json(content)
