"""Opções Flexíveis de Balcão B3 — Registro NoMe, Barreiras Knock-In/Knock-Out, Limitadores Cap/Floor e Liquidação Financeira (FL-042).

Implementa o motor determinístico de registro e liquidação exclusivamente financeira (D+1)
de Opções Flexíveis de Balcão na B3 (NoMe / Câmara B3) sobre USD/BRL PTAX, Índice Ibovespa
e Ações, em conformidade com a Resolução CMN nº 4.952/2021 e Resolução BCB nº 264/2022.

Recursos suportados:
1. Ativos subjacentes: `USD_PTAX`, `IBOVESPA`, `EQUITY`.
2. Estilos de exercício: `EUROPEAN` e `AMERICAN`.
3. Modalidades de garantia: `COM_GARANTIA_CCP` e `SEM_GARANTIA_CCP`.
4. Mecanismos de barreira contínua/discreta:
   - `NONE`: Opção vanilla flexível.
   - `UP_AND_IN`: Ativa somente se S_t >= B_up.
   - `UP_AND_OUT`: Extinta (Knocked-Out) se S_t >= B_up.
   - `DOWN_AND_IN`: Ativa somente se S_t <= B_down.
   - `DOWN_AND_OUT`: Extinta (Knocked-Out) se S_t <= B_down.
   - `DOUBLE_KNOCK_OUT`: Extinta se S_t <= B_down ou S_t >= B_up.
5. Limitador de Payoff (*Cap* / *Spread Limit* unitário ou notional) e compensação (*Rebate*).
6. Conservação bilateral soma-zero: Payoff_Bruto_Holder + Payoff_Bruto_Writer == 0.00.
7. Retenção na fonte de IRRF sobre ganho líquido positivo do Titular com balanço contábil.

100% biblioteca padrão do Python (`dataclasses`, `Decimal`, `datetime`, `enum`, `json`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")


def _quantize_money(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda valores monetários para 2 casas decimais (ROUND_HALF_UP)."""
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _quantize_four(value: Union[Decimal, str, int, float]) -> Decimal:
    """Arredonda preços e taxas para 4 casas decimais (ROUND_HALF_UP)."""
    return Decimal(str(value)).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def _to_date(val: Any, field_name: str) -> date:
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val.strip()[:10])
    raise ValueError(f"Campo '{field_name}' deve ser uma data válida.")


# ---------------------------------------------------------------------------
# Enumerações Tipadas
# ---------------------------------------------------------------------------

class FlexibleUnderlyingType(str, Enum):
    USD_PTAX = "USD_PTAX"
    IBOVESPA = "IBOVESPA"
    EQUITY = "EQUITY"


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class FlexibleExerciseStyle(str, Enum):
    EUROPEAN = "EUROPEAN"
    AMERICAN = "AMERICAN"


class FlexibleGuaranteeMode(str, Enum):
    COM_GARANTIA_CCP = "COM_GARANTIA_CCP"
    SEM_GARANTIA_CCP = "SEM_GARANTIA_CCP"


class BarrierType(str, Enum):
    NONE = "NONE"
    UP_AND_IN = "UP_AND_IN"
    UP_AND_OUT = "UP_AND_OUT"
    DOWN_AND_IN = "DOWN_AND_IN"
    DOWN_AND_OUT = "DOWN_AND_OUT"
    DOUBLE_KNOCK_OUT = "DOUBLE_KNOCK_OUT"

    @property
    def is_knock_in(self) -> bool:
        return self in (BarrierType.UP_AND_IN, BarrierType.DOWN_AND_IN)

    @property
    def is_knock_out(self) -> bool:
        return self in (
            BarrierType.UP_AND_OUT,
            BarrierType.DOWN_AND_OUT,
            BarrierType.DOUBLE_KNOCK_OUT,
        )


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class FlexibleOptionError(Exception):
    """Exceção base para Opções Flexíveis de Balcão B3."""


class FlexibleOptionValidationError(FlexibleOptionError):
    """Erro de validação cadastral ou operacional."""


class DuplicateFlexibleContractError(FlexibleOptionValidationError):
    """Contrato com identificador duplicado."""


class FlexibleContractNotFoundError(FlexibleOptionError):
    """Contrato não encontrado no NoMe."""


# ---------------------------------------------------------------------------
# Entidades de Domínio
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ObservationPrice:
    observation_date: date
    price: Decimal
    source: str = "BACEN_PTAX800_VENDA"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ObservationPrice:
        return cls(
            observation_date=_to_date(data["observation_date"], "observation_date"),
            price=Decimal(str(data["price"])),
            source=str(data.get("source", "BACEN_PTAX800_VENDA")),
        )


@dataclass(frozen=True)
class BarrierEvaluationResult:
    barrier_type: BarrierType
    triggered: bool
    is_active: bool
    is_knocked_out: bool
    trigger_date: Optional[date] = None
    trigger_price: Optional[Decimal] = None


@dataclass(frozen=True)
class FlexibleOptionContract:
    contract_id: str
    nome_protocol: str
    underlying_type: FlexibleUnderlyingType
    underlying_asset: str
    option_type: OptionType
    exercise_style: FlexibleExerciseStyle
    guarantee_mode: FlexibleGuaranteeMode
    trade_date: date
    expiration_date: date
    settlement_date: date
    spot_initial: Decimal
    strike_price: Decimal
    settlement_price: Decimal
    quantity: Decimal
    contract_multiplier: Decimal
    premium_per_unit: Decimal
    barrier_type: BarrierType
    barrier_level: Optional[Decimal] = None
    lower_barrier_level: Optional[Decimal] = None
    upper_barrier_level: Optional[Decimal] = None
    cap_per_unit: Optional[Decimal] = None
    cap_notional_brl: Optional[Decimal] = None
    rebate_per_unit: Decimal = Decimal("0.0000")
    deduct_premium_in_irrf_base: bool = True
    holder_name: str = ""
    holder_document: str = ""
    holder_irrf_rate: Decimal = Decimal("0.2250")
    holder_irrf_exempt: bool = False
    writer_name: str = ""
    writer_document: str = ""
    clearing_member_holder: Optional[int] = None
    clearing_member_writer: Optional[int] = None
    is_corporate_hedge: bool = True
    price_history: List[ObservationPrice] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FlexibleOptionContract:
        history = [
            ObservationPrice.from_dict(p) for p in data.get("price_history", [])
        ]
        return cls(
            contract_id=str(data["contract_id"]),
            nome_protocol=str(data["nome_protocol"]),
            underlying_type=FlexibleUnderlyingType(data["underlying_type"]),
            underlying_asset=str(data["underlying_asset"]),
            option_type=OptionType(data["option_type"]),
            exercise_style=FlexibleExerciseStyle(data["exercise_style"]),
            guarantee_mode=FlexibleGuaranteeMode(data["guarantee_mode"]),
            trade_date=_to_date(data["trade_date"], "trade_date"),
            expiration_date=_to_date(data["expiration_date"], "expiration_date"),
            settlement_date=_to_date(data["settlement_date"], "settlement_date"),
            spot_initial=Decimal(str(data["spot_initial"])),
            strike_price=Decimal(str(data["strike_price"])),
            settlement_price=Decimal(str(data["settlement_price"])),
            quantity=Decimal(str(data["quantity"])),
            contract_multiplier=Decimal(str(data.get("contract_multiplier", "1.00"))),
            premium_per_unit=Decimal(str(data.get("premium_per_unit", "0.0000"))),
            barrier_type=BarrierType(data.get("barrier_type", "NONE")),
            barrier_level=(
                Decimal(str(data["barrier_level"]))
                if data.get("barrier_level") is not None
                else None
            ),
            lower_barrier_level=(
                Decimal(str(data["lower_barrier_level"]))
                if data.get("lower_barrier_level") is not None
                else None
            ),
            upper_barrier_level=(
                Decimal(str(data["upper_barrier_level"]))
                if data.get("upper_barrier_level") is not None
                else None
            ),
            cap_per_unit=(
                Decimal(str(data["cap_per_unit"]))
                if data.get("cap_per_unit") is not None
                else None
            ),
            cap_notional_brl=(
                Decimal(str(data["cap_notional_brl"]))
                if data.get("cap_notional_brl") is not None
                else None
            ),
            rebate_per_unit=Decimal(str(data.get("rebate_per_unit", "0.0000"))),
            deduct_premium_in_irrf_base=bool(
                data.get("deduct_premium_in_irrf_base", True)
            ),
            holder_name=str(data.get("holder_name", "")),
            holder_document=str(data.get("holder_document", "")),
            holder_irrf_rate=Decimal(str(data.get("holder_irrf_rate", "0.2250"))),
            holder_irrf_exempt=bool(data.get("holder_irrf_exempt", False)),
            writer_name=str(data.get("writer_name", "")),
            writer_document=str(data.get("writer_document", "")),
            clearing_member_holder=data.get("clearing_member_holder"),
            clearing_member_writer=data.get("clearing_member_writer"),
            is_corporate_hedge=bool(data.get("is_corporate_hedge", True)),
            price_history=history,
        )


@dataclass(frozen=True)
class FlexibleOptionSettlement:
    contract_id: str
    nome_protocol: str
    underlying_type: FlexibleUnderlyingType
    option_type: OptionType
    barrier_type: BarrierType
    barrier_result: BarrierEvaluationResult
    is_active: bool
    is_knocked_out: bool
    raw_intrinsic_unit: Decimal
    capped_intrinsic_unit: Decimal
    rebate_applied: bool
    gross_holder_brl: Decimal
    gross_writer_brl: Decimal
    holder_irrf_brl: Decimal
    net_holder_brl: Decimal
    net_writer_brl: Decimal

    @property
    def is_zero_sum_gross(self) -> bool:
        """Payoff bruto de Titular e Lançador é estritamente soma-zero."""
        return (self.gross_holder_brl + self.gross_writer_brl) == Decimal("0.00")

    @property
    def is_cashflow_balanced(self) -> bool:
        """Net_Holder + Net_Writer + IRRF == 0.00."""
        return (
            self.net_holder_brl + self.net_writer_brl + self.holder_irrf_brl
        ) == Decimal("0.00")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "nome_protocol": self.nome_protocol,
            "underlying_type": self.underlying_type.value,
            "option_type": self.option_type.value,
            "barrier_type": self.barrier_type.value,
            "is_active": self.is_active,
            "is_knocked_out": self.is_knocked_out,
            "raw_intrinsic_unit": str(self.raw_intrinsic_unit),
            "capped_intrinsic_unit": str(self.capped_intrinsic_unit),
            "rebate_applied": self.rebate_applied,
            "gross_holder_brl": str(self.gross_holder_brl),
            "gross_writer_brl": str(self.gross_writer_brl),
            "holder_irrf_brl": str(self.holder_irrf_brl),
            "net_holder_brl": str(self.net_holder_brl),
            "net_writer_brl": str(self.net_writer_brl),
            "is_zero_sum_gross": self.is_zero_sum_gross,
            "is_cashflow_balanced": self.is_cashflow_balanced,
        }


@dataclass(frozen=True)
class FlexibleOptionsPortfolioSummary:
    total_contracts: int
    active_count: int
    knocked_out_count: int
    exercised_itm_count: int
    total_gross_volume_brl: Decimal
    total_irrf_brl: Decimal
    all_zero_sum_gross: bool
    all_cashflow_balanced: bool
    settlements: List[FlexibleOptionSettlement]


# ---------------------------------------------------------------------------
# Motor de Opções Flexíveis (FlexibleOptionsOtcEngine)
# ---------------------------------------------------------------------------

class FlexibleOptionsOtcEngine:
    """Motor da Registradora NoMe B3 para Opções Flexíveis com Barreiras e Limitadores."""

    def __init__(self) -> None:
        self._contracts: Dict[str, FlexibleOptionContract] = {}
        self._settlements: Dict[str, FlexibleOptionSettlement] = {}

    @property
    def contracts(self) -> Dict[str, FlexibleOptionContract]:
        return dict(self._contracts)

    @property
    def settlements(self) -> Dict[str, FlexibleOptionSettlement]:
        return dict(self._settlements)

    def register_contract(
        self, contract: FlexibleOptionContract
    ) -> FlexibleOptionContract:
        if contract.contract_id in self._contracts:
            raise DuplicateFlexibleContractError(
                f"Contrato flexível '{contract.contract_id}' já está registrado."
            )
        self._contracts[contract.contract_id] = contract
        return contract

    def get_contract(self, contract_id: str) -> FlexibleOptionContract:
        if contract_id not in self._contracts:
            raise FlexibleContractNotFoundError(
                f"Contrato flexível '{contract_id}' não encontrado."
            )
        return self._contracts[contract_id]

    @staticmethod
    def evaluate_barrier(
        contract: FlexibleOptionContract,
    ) -> BarrierEvaluationResult:
        """Avalia de forma determinística a barreira ao longo da série histórica de preços."""
        b_type = contract.barrier_type
        if b_type == BarrierType.NONE:
            return BarrierEvaluationResult(
                barrier_type=b_type,
                triggered=False,
                is_active=True,
                is_knocked_out=False,
            )

        triggered = False
        trigger_date: Optional[date] = None
        trigger_price: Optional[Decimal] = None

        up_lvl = contract.upper_barrier_level or contract.barrier_level
        down_lvl = contract.lower_barrier_level or contract.barrier_level

        for obs in contract.price_history:
            price = obs.price
            hit = False
            if b_type in (BarrierType.UP_AND_IN, BarrierType.UP_AND_OUT):
                if up_lvl is not None and price >= up_lvl:
                    hit = True
            elif b_type in (BarrierType.DOWN_AND_IN, BarrierType.DOWN_AND_OUT):
                if down_lvl is not None and price <= down_lvl:
                    hit = True
            elif b_type == BarrierType.DOUBLE_KNOCK_OUT:
                if (down_lvl is not None and price <= down_lvl) or (
                    up_lvl is not None and price >= up_lvl
                ):
                    hit = True

            if hit:
                triggered = True
                trigger_date = obs.observation_date
                trigger_price = price
                break  # Barreira acionada no primeiro evento

        if b_type.is_knock_in:
            is_active = triggered
            is_knocked_out = False
        else:  # knock-out
            is_active = not triggered
            is_knocked_out = triggered

        return BarrierEvaluationResult(
            barrier_type=b_type,
            triggered=triggered,
            is_active=is_active,
            is_knocked_out=is_knocked_out,
            trigger_date=trigger_date,
            trigger_price=trigger_price,
        )

    def settle_contract(
        self, contract_or_id: Union[str, FlexibleOptionContract]
    ) -> FlexibleOptionSettlement:
        """Processa a liquidação financeira determinística em D+1."""
        if isinstance(contract_or_id, FlexibleOptionContract):
            contract = contract_or_id
            if contract.contract_id not in self._contracts:
                self.register_contract(contract)
        else:
            contract = self.get_contract(contract_or_id)

        barrier_res = self.evaluate_barrier(contract)
        is_active = barrier_res.is_active
        is_knocked_out = barrier_res.is_knocked_out

        raw_intrinsic = Decimal("0.0000")
        capped_intrinsic = Decimal("0.0000")
        rebate_applied = False
        gross_holder = Decimal("0.00")

        multiplier = contract.quantity * contract.contract_multiplier

        if is_active:
            if contract.option_type == OptionType.CALL:
                raw_intrinsic = max(
                    Decimal("0.0000"), contract.settlement_price - contract.strike_price
                )
            else:  # PUT
                raw_intrinsic = max(
                    Decimal("0.0000"), contract.strike_price - contract.settlement_price
                )

            if contract.cap_per_unit is not None and contract.cap_per_unit > Decimal(
                "0.0000"
            ):
                capped_intrinsic = min(raw_intrinsic, contract.cap_per_unit)
            else:
                capped_intrinsic = raw_intrinsic

            unlimited_gross = multiplier * capped_intrinsic
            if (
                contract.cap_notional_brl is not None
                and contract.cap_notional_brl > Decimal("0.00")
            ):
                gross_holder = _quantize_money(
                    min(unlimited_gross, contract.cap_notional_brl)
                )
            else:
                gross_holder = _quantize_money(unlimited_gross)

        elif is_knocked_out and contract.rebate_per_unit > Decimal("0.0000"):
            rebate_applied = True
            gross_holder = _quantize_money(multiplier * contract.rebate_per_unit)

        # Invariante soma-zero bilateral
        gross_writer = _quantize_money(-gross_holder)

        # IRRF
        irrf_brl = Decimal("0.00")
        if gross_holder > Decimal("0.00") and not contract.holder_irrf_exempt:
            tax_base = gross_holder
            if contract.deduct_premium_in_irrf_base:
                total_premium = _quantize_money(multiplier * contract.premium_per_unit)
                tax_base = max(Decimal("0.00"), gross_holder - total_premium)
            irrf_brl = _quantize_money(tax_base * contract.holder_irrf_rate)

        net_holder = _quantize_money(gross_holder - irrf_brl)
        net_writer = gross_writer  # Débito integral do lançador

        settlement = FlexibleOptionSettlement(
            contract_id=contract.contract_id,
            nome_protocol=contract.nome_protocol,
            underlying_type=contract.underlying_type,
            option_type=contract.option_type,
            barrier_type=contract.barrier_type,
            barrier_result=barrier_res,
            is_active=is_active,
            is_knocked_out=is_knocked_out,
            raw_intrinsic_unit=raw_intrinsic,
            capped_intrinsic_unit=capped_intrinsic,
            rebate_applied=rebate_applied,
            gross_holder_brl=gross_holder,
            gross_writer_brl=gross_writer,
            holder_irrf_brl=irrf_brl,
            net_holder_brl=net_holder,
            net_writer_brl=net_writer,
        )
        self._settlements[contract.contract_id] = settlement
        return settlement

    def settle_all(self) -> List[FlexibleOptionSettlement]:
        return [self.settle_contract(cid) for cid in self._contracts]

    def summarize(self) -> FlexibleOptionsPortfolioSummary:
        settlements = self.settle_all()
        active_cnt = sum(1 for s in settlements if s.is_active)
        ko_cnt = sum(1 for s in settlements if s.is_knocked_out)
        itm_cnt = sum(1 for s in settlements if s.capped_intrinsic_unit > Decimal("0.0000"))
        tot_gross = _quantize_money(sum(s.gross_holder_brl for s in settlements))
        tot_irrf = _quantize_money(sum(s.holder_irrf_brl for s in settlements))
        zero_sum = all(s.is_zero_sum_gross for s in settlements)
        balanced = all(s.is_cashflow_balanced for s in settlements)

        return FlexibleOptionsPortfolioSummary(
            total_contracts=len(settlements),
            active_count=active_cnt,
            knocked_out_count=ko_cnt,
            exercised_itm_count=itm_cnt,
            total_gross_volume_brl=tot_gross,
            total_irrf_brl=tot_irrf,
            all_zero_sum_gross=zero_sum,
            all_cashflow_balanced=balanced,
            settlements=settlements,
        )

    portfolio_summary = summarize

    @classmethod
    def from_json_fixture(cls, path: Union[str, Path]) -> FlexibleOptionsOtcEngine:
        fixture_path = Path(path)
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        engine = cls()
        for item in raw.get("flexible_option_contracts", []):
            engine.register_contract(FlexibleOptionContract.from_dict(item))
        return engine


__all__ = [
    "BarrierEvaluationResult",
    "BarrierType",
    "DuplicateFlexibleContractError",
    "FlexibleContractNotFoundError",
    "FlexibleExerciseStyle",
    "FlexibleGuaranteeMode",
    "FlexibleOptionContract",
    "FlexibleOptionError",
    "FlexibleOptionSettlement",
    "FlexibleOptionValidationError",
    "FlexibleOptionsOtcEngine",
    "FlexibleOptionsPortfolioSummary",
    "FlexibleUnderlyingType",
    "ObservationPrice",
    "OptionType",
]
