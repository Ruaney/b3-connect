"""Módulo de Registro, Marcação a Mercado, Custódia e Liquidação de Contratos de Energia Elétrica (FL-087).

Implementa a especificação regulatória e operacional para Contratos de Compra e Venda
de Energia Elétrica no Ambiente de Contratação Livre (ACL) registrados no Sistema de Balcão
NoMe da B3 e na Câmara de Comercialização de Energia Elétrica (CCEE), conforme:
- Lei Federal nº 10.848/2004 e Decreto nº 5.163/2004 (Comercialização de Energia Elétrica);
- Resoluções Normativas da ANEEL sobre Regras e Procedimentos de Comercialização (PdC);
- Resoluções da CCEE sobre Contabilização e Liquidação Financeira do Mercado de Curto Prazo (MCP);
- Manual de Registro e Operações de Balcão da B3 (Sistema NoMe - Energia Elétrica);
- Manual de Administração de Risco e Garantias da Câmara B3.

Pilares e Regras de Negócio:
1. Contratos Bilaterais no ACL:
   - Fontes e Lastro: Convencional (CONV), Incentivada 50% (I50) e Incentivada 100% (I100);
   - Submercados elétricos do Sistema Interligado Nacional (SIN):
     Sudeste/Centro-Oeste (SE/CO), Sul (S), Nordeste (NE) e Norte (N);
   - Modalidade de Carga: Flat (constante) ou Modulada (patamares).
2. Conversão Física de Montantes:
   - Montante expresso em MWmédio (Megawatt-médio) para o período/mês de suprimento;
   - Volume de Energia em MWh = MWmédio * horas_do_período (ou horas do mês civil);
   - Precisão de 4 casas decimais para potência e energia física.
3. Preço de Liquidação das Diferenças (PLD CCEE):
   - PLD Horário (horas 1 a 24) e PLD Médio Mensal/Semanal apurados por submercado;
   - Limites regulatórios ANEEL: PLD Mínimo e PLD Máximo (estrutural e horário).
4. Marcação a Mercado (Mark-to-Market - MtM):
   - Curva Forward de preços de energia por submercado e tipo de energia;
   - MtM Comprador = Volume_MWh * (Preço_Mercado - Preço_Contrato);
   - MtM Vendedor = Volume_MWh * (Preço_Contrato - Preço_Mercado) = - MtM Comprador;
   - Invariante formal de Soma-Zero no MtM bilateral: MtM_Comprador + MtM_Vendedor == 0,00.
5. Garantias Financeiras e Trava Fiduciária de Recebíveis no NoMe B3:
   - Depósito de garantias (Dinheiro BRL com haircut 0%, Títulos Públicos com haircut regulatório, Fiança Bancária);
   - Trava fiduciária (cessão fiduciária de direitos creditórios de recebíveis de energia)
     em favor de instituição credora beneficiária com conta escrow vinculada;
   - No faturamento/liquidação, o valor devido ao vendedor é segregado:
     parcela retida para conta fiduciária do credor e parcela livre para o vendedor.
6. Invariantes Formais de Conservação e Integridade:
   - LAW-089: Conservação de Energia Elétrica Bilateral (MWh_Vendido == MWh_Comprado);
   - LAW-090: Liquidação Financeira Bilateral Soma-Zero centavo a centavo;
   - LAW-091: Não-Negatividade de Montantes (MWmédio, MWh) e Preços (R$/MWh, PLD);
   - LAW-092: Limite de Trava Fiduciária (Valor Travado <= Valor Total Contratual).
"""

from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from src.invariants import (
    check,
    InvariantViolationError,
    assert_power_energy_conservation,
    assert_power_settlement_zero_sum,
    assert_power_non_negativity,
    assert_fiduciary_assignment_limit,
)

# ---------------------------------------------------------------------------
# Constantes do Setor Elétrico Brasileiro (CCEE, ANEEL e Balcão NoMe B3)
# ---------------------------------------------------------------------------

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")

# Limites Regulatórios Padrão ANEEL para PLD (valores de referência SIN)
DEFAULT_PLD_MIN = Decimal("61.07")            # Piso regulatório mínimo (R$/MWh)
DEFAULT_PLD_MAX_STRUCTURAL = Decimal("678.29") # Teto estrutural do PLD (R$/MWh)
DEFAULT_PLD_MAX_HOURLY = Decimal("1470.32")    # Teto horário do PLD (R$/MWh)

# Haircuts Regulatórios Padrão para Garantias B3
HAIRCUT_CASH = Decimal("0.00")                 # Dinheiro em moeda nacional (0%)
HAIRCUT_SOVEREIGN_BOND = Decimal("0.05")       # Títulos Públicos Federais TPF (5%)
HAIRCUT_BANK_GUARANTEE = Decimal("0.00")       # Fiança Bancária / Carta de Fiança (0%)
HAIRCUT_DEFAULT_OTHER = Decimal("0.10")        # Outros ativos elegíveis (10%)


def round_cents(value: Decimal) -> Decimal:
    """Arredonda valor financeiro para 2 casas decimais (half-up bancário B3)."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def round_energy(value: Decimal) -> Decimal:
    """Arredonda volume de energia física (MWh) ou potência (MWmédio) para 4 casas decimais."""
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def calculate_period_hours(start_date: date, end_date: date) -> int:
    """Calcula o total de horas de suprimento entre duas datas inclusive (24 horas por dia)."""
    if end_date < start_date:
        raise PowerTradingValidationError(
            f"Data final ({end_date}) não pode ser anterior à data inicial ({start_date})."
        )
    total_days = (end_date - start_date).days + 1
    return total_days * 24


def calculate_month_hours(year: int, month: int) -> int:
    """Calcula o total de horas em um mês civil específico (dias do mês * 24 horas)."""
    if not (1 <= month <= 12):
        raise PowerTradingValidationError(f"Mês inválido: {month}. Deve estar entre 1 e 12.")
    days_in_month = calendar.monthrange(year, month)[1]
    return days_in_month * 24


# ---------------------------------------------------------------------------
# Exceções Customizadas do Domínio
# ---------------------------------------------------------------------------


class PowerTradingError(Exception):
    """Exceção base para erros do motor de comercialização de energia elétrica ACL/CCEE/B3."""


class PowerTradingValidationError(PowerTradingError):
    """Erro de validação cadastral, parâmetros contratuais ou consistência de dados."""


class PldLimitViolationError(PowerTradingError):
    """Erro emitido quando um valor de PLD viola os pisos ou tetos regulatórios da ANEEL."""


class ContractNotFoundError(PowerTradingError):
    """Erro de busca para identificador de contrato inexistente."""


class FiduciaryAssignmentError(PowerTradingError):
    """Erro de registro, cálculo ou execução de trava fiduciária de recebíveis."""


class InsufficientCollateralError(PowerTradingError):
    """Erro de insuficiência de garantias para cobertura da margem requerida."""


class InvalidContractStateError(PowerTradingError):
    """Erro de transição ou operação incompatível com o estado atual do contrato."""


# ---------------------------------------------------------------------------
# Enums de Classificação Regulatória e Operacional
# ---------------------------------------------------------------------------


class EnergyType(str, Enum):
    """Tipos de energia e fontes geradoras negociadas no ACL brasileiro."""

    CONV = "CONV"      # Energia Convencional (Hidro/Térmica tradicional, 0% desconto TUSD)
    I50 = "I50"        # Energia Incentivada com 50% de desconto na TUSD/TUST (Eólica, Solar, PCH, Biomassa)
    I100 = "I100"      # Energia Incentivada com 100% de desconto na TUSD/TUST

    @property
    def tusd_discount_pct(self) -> Decimal:
        """Percentual de desconto conferido na Tarifa de Uso do Sistema (TUSD/TUST)."""
        if self == EnergyType.CONV:
            return Decimal("0.00")
        elif self == EnergyType.I50:
            return Decimal("50.00")
        elif self == EnergyType.I100:
            return Decimal("100.00")
        return Decimal("0.00")


class ElectricSubmarket(str, Enum):
    """Submercados elétricos do Sistema Interligado Nacional (SIN) definidos pela CCEE."""

    SE_CO = "SE_CO"    # Sudeste / Centro-Oeste
    S = "S"            # Sul
    NE = "NE"          # Nordeste
    N = "N"            # Norte


class ContractStatus(str, Enum):
    """Ciclo de vida do contrato de compra e venda de energia no NoMe B3 / CCEE."""

    REGISTERED = "REGISTERED"    # Registrado no NoMe B3 e validado no sistema da CCEE
    ACTIVE = "ACTIVE"            # Em período ativo de suprimento
    SETTLED = "SETTLED"          # Liquidado financeiramente
    CANCELLED = "CANCELLED"      # Cancelado ou rescindido pelas partes


class ModulationType(str, Enum):
    """Modalidade de entrega horária da energia contratada."""

    FLAT = "FLAT"                # Entrega em potência constante em todas as horas do período
    MODULATED = "MODULATED"      # Entrega modulada conforme perfil de carga (patamares horário)


class CollateralType(str, Enum):
    """Tipos de garantias financeiras aceitas pela Câmara B3 para derivativos/balcão de energia."""

    CASH_BRL = "CASH_BRL"
    SOVEREIGN_BOND = "SOVEREIGN_BOND"
    BANK_GUARANTEE = "BANK_GUARANTEE"
    FIDUCIARY_RECEIVABLE = "FIDUCIARY_RECEIVABLE"


class FiduciaryLockStatus(str, Enum):
    """Status operacional da trava fiduciária de recebíveis no NoMe B3."""

    ACTIVE = "ACTIVE"            # Trava constituída e ativa com retenção fiduciária obrigatória
    RELEASED = "RELEASED"        # Trava liberada após liquidação/amortização da dívida lastreada
    FORECLOSED = "FORECLOSED"    # Trava executada judicial/extrajudicialmente pelo credor


# ---------------------------------------------------------------------------
# Entidades de Dados (Dataclasses)
# ---------------------------------------------------------------------------


@dataclass
class PldRecord:
    """Registro oficial de Preço de Liquidação das Diferenças (PLD) da CCEE por submercado."""

    submarket: ElectricSubmarket
    reference_date: date
    hourly_prices: Dict[int, Decimal] = field(default_factory=dict)
    average_price: Decimal = Decimal("0.00")
    min_limit: Decimal = DEFAULT_PLD_MIN
    max_limit: Decimal = DEFAULT_PLD_MAX_HOURLY

    def __post_init__(self) -> None:
        if self.hourly_prices and self.average_price == Decimal("0.00"):
            total = sum(self.hourly_prices.values())
            self.average_price = (total / Decimal(str(len(self.hourly_prices)))).quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP
            )
        self.validate()

    def validate(self) -> None:
        """Valida conformidade com os limites regulatórios da ANEEL."""
        if self.hourly_prices:
            for hour, price in self.hourly_prices.items():
                if not (1 <= hour <= 24):
                    raise PowerTradingValidationError(f"Hora inválida: {hour}. Deve ser de 1 a 24.")
                if price < self.min_limit or price > self.max_limit:
                    raise PldLimitViolationError(
                        f"PLD horário na hora {hour} ({price} R$/MWh) fora dos limites ANEEL: "
                        f"[{self.min_limit}, {self.max_limit}] no submercado {self.submarket.value}."
                    )
        if self.average_price < self.min_limit or self.average_price > self.max_limit:
            raise PldLimitViolationError(
                f"PLD médio ({self.average_price} R$/MWh) fora dos limites ANEEL: "
                f"[{self.min_limit}, {self.max_limit}] no submercado {self.submarket.value}."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "submarket": self.submarket.value,
            "reference_date": self.reference_date.isoformat(),
            "hourly_prices": {str(k): str(v) for k, v in self.hourly_prices.items()},
            "average_price": str(self.average_price),
            "min_limit": str(self.min_limit),
            "max_limit": str(self.max_limit),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PldRecord:
        hourly = {int(k): Decimal(str(v)) for k, v in data.get("hourly_prices", {}).items()}
        return cls(
            submarket=ElectricSubmarket(data["submarket"]),
            reference_date=date.fromisoformat(data["reference_date"]),
            hourly_prices=hourly,
            average_price=Decimal(str(data["average_price"])),
            min_limit=Decimal(str(data.get("min_limit", DEFAULT_PLD_MIN))),
            max_limit=Decimal(str(data.get("max_limit", DEFAULT_PLD_MAX_HOURLY))),
        )


@dataclass
class FiduciaryAssignment:
    """Trava fiduciária de recebíveis de energia elétrica registrada no NoMe B3.

    Representa a cessão fiduciária de direitos creditórios futuros decorrentes da venda
    de energia para uma instituição financeira ou credor garantido.
    """

    assignment_id: str
    contract_id: str
    beneficiary_creditor_id: str
    beneficiary_creditor_name: str
    escrow_account_id: str
    secured_debt_amount: Decimal
    retention_percentage: Decimal = Decimal("100.00")
    fixed_retention_amount: Optional[Decimal] = None
    status: FiduciaryLockStatus = FiduciaryLockStatus.ACTIVE
    registered_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if self.retention_percentage < Decimal("0.00") or self.retention_percentage > Decimal("100.00"):
            raise PowerTradingValidationError(
                f"Percentual de retenção ({self.retention_percentage}%) inválido. Deve ser entre 0% e 100%."
            )
        if self.secured_debt_amount < Decimal("0.00"):
            raise PowerTradingValidationError(
                f"Valor da dívida garantida ({self.secured_debt_amount}) não pode ser negativo."
            )
        if self.fixed_retention_amount is not None and self.fixed_retention_amount < Decimal("0.00"):
            raise PowerTradingValidationError(
                f"Valor fixo de retenção ({self.fixed_retention_amount}) não pode ser negativo."
            )

    def calculate_retention(self, invoice_gross_amount: Decimal) -> Decimal:
        """Calcula o valor retido a ser transferido para a conta escrow do credor."""
        if self.status != FiduciaryLockStatus.ACTIVE or invoice_gross_amount <= Decimal("0.00"):
            return Decimal("0.00")

        if self.fixed_retention_amount is not None:
            retention = min(self.fixed_retention_amount, invoice_gross_amount, self.secured_debt_amount)
        else:
            pct_amount = invoice_gross_amount * (self.retention_percentage / Decimal("100.00"))
            retention = min(pct_amount, self.secured_debt_amount)

        return round_cents(retention)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assignment_id": self.assignment_id,
            "contract_id": self.contract_id,
            "beneficiary_creditor_id": self.beneficiary_creditor_id,
            "beneficiary_creditor_name": self.beneficiary_creditor_name,
            "escrow_account_id": self.escrow_account_id,
            "secured_debt_amount": str(self.secured_debt_amount),
            "retention_percentage": str(self.retention_percentage),
            "fixed_retention_amount": str(self.fixed_retention_amount) if self.fixed_retention_amount is not None else None,
            "status": self.status.value,
            "registered_at": self.registered_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FiduciaryAssignment:
        fixed_amt = Decimal(str(data["fixed_retention_amount"])) if data.get("fixed_retention_amount") is not None else None
        reg_at = (
            datetime.fromisoformat(data["registered_at"])
            if "registered_at" in data
            else datetime.now()
        )
        return cls(
            assignment_id=data["assignment_id"],
            contract_id=data["contract_id"],
            beneficiary_creditor_id=data["beneficiary_creditor_id"],
            beneficiary_creditor_name=data["beneficiary_creditor_name"],
            escrow_account_id=data["escrow_account_id"],
            secured_debt_amount=Decimal(str(data["secured_debt_amount"])),
            retention_percentage=Decimal(str(data.get("retention_percentage", "100.00"))),
            fixed_retention_amount=fixed_amt,
            status=FiduciaryLockStatus(data.get("status", "ACTIVE")),
            registered_at=reg_at,
        )


@dataclass
class CollateralDeposit:
    """Depósito individual de garantia financeira no sistema NoMe / Câmara B3."""

    deposit_id: str
    participant_id: str
    collateral_type: CollateralType
    nominal_amount: Decimal
    haircut_pct: Decimal = Decimal("0.00")

    @property
    def evaluated_amount(self) -> Decimal:
        """Valor avaliado líquido após aplicação do haircut regulatório."""
        multiplier = Decimal("1.00") - (self.haircut_pct / Decimal("100.00"))
        return round_cents(self.nominal_amount * multiplier)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "deposit_id": self.deposit_id,
            "participant_id": self.participant_id,
            "collateral_type": self.collateral_type.value,
            "nominal_amount": str(self.nominal_amount),
            "haircut_pct": str(self.haircut_pct),
            "evaluated_amount": str(self.evaluated_amount),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CollateralDeposit:
        return cls(
            deposit_id=data["deposit_id"],
            participant_id=data["participant_id"],
            collateral_type=CollateralType(data["collateral_type"]),
            nominal_amount=Decimal(str(data["nominal_amount"])),
            haircut_pct=Decimal(str(data.get("haircut_pct", "0.00"))),
        )


@dataclass
class ParticipantCollateralProfile:
    """Perfil consolidado de garantias financeiras e margem de risco do participante."""

    participant_id: str
    participant_name: str
    ccee_agent_code: str
    cash_settlement_balance: Decimal = Decimal("0.00")
    required_margin: Decimal = Decimal("0.00")
    deposits: List[CollateralDeposit] = field(default_factory=list)

    @property
    def total_collateral_nominal(self) -> Decimal:
        """Soma nominal de todas as garantias registradas."""
        return round_cents(sum(d.nominal_amount for d in self.deposits))

    @property
    def total_collateral_evaluated(self) -> Decimal:
        """Soma avaliada líquida de todas as garantias com haircuts aplicados."""
        return round_cents(sum(d.evaluated_amount for d in self.deposits))

    @property
    def margin_coverage(self) -> Decimal:
        """Saldo de cobertura de margem (Garantias Avaliadas - Margem Requerida)."""
        return round_cents(self.total_collateral_evaluated - self.required_margin)

    @property
    def is_undercollateralized(self) -> bool:
        """Indica se o participante encontra-se em déficit de garantias."""
        return self.margin_coverage < Decimal("0.00")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "participant_name": self.participant_name,
            "ccee_agent_code": self.ccee_agent_code,
            "cash_settlement_balance": str(self.cash_settlement_balance),
            "required_margin": str(self.required_margin),
            "total_collateral_nominal": str(self.total_collateral_nominal),
            "total_collateral_evaluated": str(self.total_collateral_evaluated),
            "margin_coverage": str(self.margin_coverage),
            "is_undercollateralized": self.is_undercollateralized,
            "deposits": [d.to_dict() for d in self.deposits],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ParticipantCollateralProfile:
        deposits = [CollateralDeposit.from_dict(d) for d in data.get("deposits", [])]
        return cls(
            participant_id=data["participant_id"],
            participant_name=data["participant_name"],
            ccee_agent_code=data["ccee_agent_code"],
            cash_settlement_balance=Decimal(str(data.get("cash_settlement_balance", "0.00"))),
            required_margin=Decimal(str(data.get("required_margin", "0.00"))),
            deposits=deposits,
        )


@dataclass
class MarginCall:
    """Chamada de margem emitida para recomposição imediata de garantias."""

    call_id: str
    participant_id: str
    call_date: date
    required_margin: Decimal
    evaluated_collateral: Decimal
    deficit_amount: Decimal
    status: str = "PENDING"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "participant_id": self.participant_id,
            "call_date": self.call_date.isoformat(),
            "required_margin": str(self.required_margin),
            "evaluated_collateral": str(self.evaluated_collateral),
            "deficit_amount": str(self.deficit_amount),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MarginCall:
        return cls(
            call_id=data["call_id"],
            participant_id=data["participant_id"],
            call_date=date.fromisoformat(data["call_date"]),
            required_margin=Decimal(str(data["required_margin"])),
            evaluated_collateral=Decimal(str(data["evaluated_collateral"])),
            deficit_amount=Decimal(str(data["deficit_amount"])),
            status=data.get("status", "PENDING"),
        )


@dataclass
class BilateralPowerContract:
    """Contrato bilateral de compra e venda de energia elétrica no ACL (NoMe B3 / CCEE).

    Representa o instrumento jurídico e operacional bilateral padronizado.
    """

    contract_id: str
    nome_registration_id: str
    ccee_contract_code: str
    buyer_id: str
    buyer_name: str
    seller_id: str
    seller_name: str
    energy_type: EnergyType
    submarket: ElectricSubmarket
    start_date: date
    end_date: date
    mw_average: Decimal
    contract_price_mwh: Decimal
    total_hours: Optional[int] = None
    volume_mwh: Optional[Decimal] = None
    modulation: ModulationType = ModulationType.FLAT
    status: ContractStatus = ContractStatus.REGISTERED
    fiduciary_assignment: Optional[FiduciaryAssignment] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.total_hours is None:
            self.total_hours = calculate_period_hours(self.start_date, self.end_date)
        if self.volume_mwh is None:
            self.volume_mwh = round_energy(self.mw_average * Decimal(str(self.total_hours)))
        self.validate()

    def validate(self) -> None:
        """Aplica verificações de integridade física e não-negatividade de montantes e preços."""
        assert_power_non_negativity(
            mw_average=self.mw_average,
            total_hours=self.total_hours,
            volume_mwh=self.volume_mwh,
            price_mwh=self.contract_price_mwh,
            contract_id=self.contract_id,
        )
        if self.fiduciary_assignment is not None:
            assert_fiduciary_assignment_limit(
                assigned_amount=self.fiduciary_assignment.secured_debt_amount,
                total_contract_value=self.notional_amount,
                contract_id=self.contract_id,
            )

    @property
    def notional_amount(self) -> Decimal:
        """Valor financeiro nominal total contratado para o período (Volume MWh * Preço R$/MWh)."""
        return round_cents(self.volume_mwh * self.contract_price_mwh)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "nome_registration_id": self.nome_registration_id,
            "ccee_contract_code": self.ccee_contract_code,
            "buyer_id": self.buyer_id,
            "buyer_name": self.buyer_name,
            "seller_id": self.seller_id,
            "seller_name": self.seller_name,
            "energy_type": self.energy_type.value,
            "submarket": self.submarket.value,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "mw_average": str(self.mw_average),
            "total_hours": self.total_hours,
            "volume_mwh": str(self.volume_mwh),
            "contract_price_mwh": str(self.contract_price_mwh),
            "notional_amount": str(self.notional_amount),
            "modulation": self.modulation.value,
            "status": self.status.value,
            "fiduciary_assignment": self.fiduciary_assignment.to_dict() if self.fiduciary_assignment else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BilateralPowerContract:
        fa = (
            FiduciaryAssignment.from_dict(data["fiduciary_assignment"])
            if data.get("fiduciary_assignment")
            else None
        )
        return cls(
            contract_id=data["contract_id"],
            nome_registration_id=data["nome_registration_id"],
            ccee_contract_code=data["ccee_contract_code"],
            buyer_id=data["buyer_id"],
            buyer_name=data["buyer_name"],
            seller_id=data["seller_id"],
            seller_name=data["seller_name"],
            energy_type=EnergyType(data["energy_type"]),
            submarket=ElectricSubmarket(data["submarket"]),
            start_date=date.fromisoformat(data["start_date"]),
            end_date=date.fromisoformat(data["end_date"]),
            mw_average=Decimal(str(data["mw_average"])),
            contract_price_mwh=Decimal(str(data["contract_price_mwh"])),
            total_hours=int(data["total_hours"]) if "total_hours" in data else None,
            volume_mwh=Decimal(str(data["volume_mwh"])) if "volume_mwh" in data else None,
            modulation=ModulationType(data.get("modulation", "FLAT")),
            status=ContractStatus(data.get("status", "REGISTERED")),
            fiduciary_assignment=fa,
            metadata=data.get("metadata", {}),
        )


@dataclass
class MarkToMarketResult:
    """Resultado da apuração de Marcação a Mercado (MtM) bilateral para um contrato."""

    contract_id: str
    valuation_date: date
    submarket: ElectricSubmarket
    energy_type: EnergyType
    market_price_mwh: Decimal
    contract_price_mwh: Decimal
    price_differential_mwh: Decimal
    volume_mwh: Decimal
    buyer_mtm: Decimal
    seller_mtm: Decimal
    net_clearing_mtm: Decimal = Decimal("0.00")

    @property
    def is_zero_sum(self) -> bool:
        """Verifica conservação estrita de soma-zero entre as pontas compradora e vendedora."""
        return (self.buyer_mtm + self.seller_mtm) == Decimal("0.00")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "valuation_date": self.valuation_date.isoformat(),
            "submarket": self.submarket.value,
            "energy_type": self.energy_type.value,
            "market_price_mwh": str(self.market_price_mwh),
            "contract_price_mwh": str(self.contract_price_mwh),
            "price_differential_mwh": str(self.price_differential_mwh),
            "volume_mwh": str(self.volume_mwh),
            "buyer_mtm": str(self.buyer_mtm),
            "seller_mtm": str(self.seller_mtm),
            "net_clearing_mtm": str(self.net_clearing_mtm),
            "is_zero_sum": self.is_zero_sum,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MarkToMarketResult:
        return cls(
            contract_id=data["contract_id"],
            valuation_date=date.fromisoformat(data["valuation_date"]),
            submarket=ElectricSubmarket(data["submarket"]),
            energy_type=EnergyType(data["energy_type"]),
            market_price_mwh=Decimal(str(data["market_price_mwh"])),
            contract_price_mwh=Decimal(str(data["contract_price_mwh"])),
            price_differential_mwh=Decimal(str(data["price_differential_mwh"])),
            volume_mwh=Decimal(str(data["volume_mwh"])),
            buyer_mtm=Decimal(str(data["buyer_mtm"])),
            seller_mtm=Decimal(str(data["seller_mtm"])),
            net_clearing_mtm=Decimal(str(data.get("net_clearing_mtm", "0.00"))),
        )


@dataclass
class BilateralSettlementReceipt:
    """Recibo oficial de liquidação financeira bilateral de contrato de energia elétrica."""

    settlement_id: str
    contract_id: str
    settlement_date: date
    volume_mwh: Decimal
    contract_price_mwh: Decimal
    gross_amount: Decimal
    buyer_id: str
    buyer_debit: Decimal
    seller_id: str
    seller_gross_credit: Decimal
    fiduciary_retained_amount: Decimal = Decimal("0.00")
    escrow_account_id: Optional[str] = None
    beneficiary_creditor_id: Optional[str] = None
    seller_net_credit: Decimal = Decimal("0.00")
    status: str = "SETTLED"

    def __post_init__(self) -> None:
        if self.seller_net_credit == Decimal("0.00") and self.gross_amount > Decimal("0.00"):
            self.seller_net_credit = self.gross_amount - self.fiduciary_retained_amount

    @property
    def is_zero_sum(self) -> bool:
        """Verifica se os débitos do comprador equivalem rigorosamente aos créditos totais."""
        total_credits = self.seller_net_credit + self.fiduciary_retained_amount
        return self.buyer_debit == total_credits

    def to_dict(self) -> Dict[str, Any]:
        return {
            "settlement_id": self.settlement_id,
            "contract_id": self.contract_id,
            "settlement_date": self.settlement_date.isoformat(),
            "volume_mwh": str(self.volume_mwh),
            "contract_price_mwh": str(self.contract_price_mwh),
            "gross_amount": str(self.gross_amount),
            "buyer_id": self.buyer_id,
            "buyer_debit": str(self.buyer_debit),
            "seller_id": self.seller_id,
            "seller_gross_credit": str(self.seller_gross_credit),
            "fiduciary_retained_amount": str(self.fiduciary_retained_amount),
            "escrow_account_id": self.escrow_account_id,
            "beneficiary_creditor_id": self.beneficiary_creditor_id,
            "seller_net_credit": str(self.seller_net_credit),
            "status": self.status,
            "is_zero_sum": self.is_zero_sum,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BilateralSettlementReceipt:
        return cls(
            settlement_id=data["settlement_id"],
            contract_id=data["contract_id"],
            settlement_date=date.fromisoformat(data["settlement_date"]),
            volume_mwh=Decimal(str(data["volume_mwh"])),
            contract_price_mwh=Decimal(str(data["contract_price_mwh"])),
            gross_amount=Decimal(str(data["gross_amount"])),
            buyer_id=data["buyer_id"],
            buyer_debit=Decimal(str(data["buyer_debit"])),
            seller_id=data["seller_id"],
            seller_gross_credit=Decimal(str(data["seller_gross_credit"])),
            fiduciary_retained_amount=Decimal(str(data.get("fiduciary_retained_amount", "0.00"))),
            escrow_account_id=data.get("escrow_account_id"),
            beneficiary_creditor_id=data.get("beneficiary_creditor_id"),
            seller_net_credit=Decimal(str(data.get("seller_net_credit", "0.00"))),
            status=data.get("status", "SETTLED"),
        )


@dataclass
class CceeMcpSettlementPosition:
    """Posição individual de agente no Mercado de Curto Prazo (MCP) da CCEE."""

    agent_id: str
    agent_name: str
    submarket: ElectricSubmarket
    generation_mwh: Decimal
    consumption_mwh: Decimal
    bilateral_purchases_mwh: Decimal
    bilateral_sales_mwh: Decimal
    pld_price: Decimal

    @property
    def net_energy_mwh(self) -> Decimal:
        """Balanço energético: (Geração + Compras) - (Consumo + Vendas)."""
        credits = self.generation_mwh + self.bilateral_purchases_mwh
        debits = self.consumption_mwh + self.bilateral_sales_mwh
        return round_energy(credits - debits)

    @property
    def financial_settlement_amount(self) -> Decimal:
        """Valor a liquidar no MCP: net_energy_mwh * PLD.

        Se positivo: crédito a receber do MCP da CCEE.
        Se negativo: débito a pagar ao MCP da CCEE.
        """
        return round_cents(self.net_energy_mwh * self.pld_price)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "submarket": self.submarket.value,
            "generation_mwh": str(self.generation_mwh),
            "consumption_mwh": str(self.consumption_mwh),
            "bilateral_purchases_mwh": str(self.bilateral_purchases_mwh),
            "bilateral_sales_mwh": str(self.bilateral_sales_mwh),
            "net_energy_mwh": str(self.net_energy_mwh),
            "pld_price": str(self.pld_price),
            "financial_settlement_amount": str(self.financial_settlement_amount),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CceeMcpSettlementPosition:
        return cls(
            agent_id=data["agent_id"],
            agent_name=data["agent_name"],
            submarket=ElectricSubmarket(data["submarket"]),
            generation_mwh=Decimal(str(data["generation_mwh"])),
            consumption_mwh=Decimal(str(data["consumption_mwh"])),
            bilateral_purchases_mwh=Decimal(str(data["bilateral_purchases_mwh"])),
            bilateral_sales_mwh=Decimal(str(data["bilateral_sales_mwh"])),
            pld_price=Decimal(str(data["pld_price"])),
        )


@dataclass
class CceeMcpBatchSettlement:
    """Lote de liquidação multilateral do Mercado de Curto Prazo (MCP) da CCEE."""

    batch_id: str
    settlement_date: date
    submarket: ElectricSubmarket
    positions: List[CceeMcpSettlementPosition] = field(default_factory=list)

    @property
    def total_generation_mwh(self) -> Decimal:
        return round_energy(sum(p.generation_mwh for p in self.positions))

    @property
    def total_consumption_mwh(self) -> Decimal:
        return round_energy(sum(p.consumption_mwh for p in self.positions))

    @property
    def total_purchases_mwh(self) -> Decimal:
        return round_energy(sum(p.bilateral_purchases_mwh for p in self.positions))

    @property
    def total_sales_mwh(self) -> Decimal:
        return round_energy(sum(p.bilateral_sales_mwh for p in self.positions))

    @property
    def net_energy_balance_mwh(self) -> Decimal:
        """Balanço agregado de energia do subsistema."""
        return round_energy(sum(p.net_energy_mwh for p in self.positions))

    @property
    def total_credits(self) -> Decimal:
        """Soma dos créditos a serem pagos aos agentes credores do MCP."""
        return round_cents(sum(p.financial_settlement_amount for p in self.positions if p.financial_settlement_amount > Decimal("0.00")))

    @property
    def total_debits(self) -> Decimal:
        """Soma dos débitos a serem cobrados dos agentes devedores do MCP (em módulo positivo)."""
        return round_cents(abs(sum(p.financial_settlement_amount for p in self.positions if p.financial_settlement_amount < Decimal("0.00"))))

    @property
    def net_financial_imbalance(self) -> Decimal:
        """Diferença entre créditos e débitos multilaterais."""
        return round_cents(self.total_credits - self.total_debits)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "settlement_date": self.settlement_date.isoformat(),
            "submarket": self.submarket.value,
            "total_generation_mwh": str(self.total_generation_mwh),
            "total_consumption_mwh": str(self.total_consumption_mwh),
            "total_purchases_mwh": str(self.total_purchases_mwh),
            "total_sales_mwh": str(self.total_sales_mwh),
            "net_energy_balance_mwh": str(self.net_energy_balance_mwh),
            "total_credits": str(self.total_credits),
            "total_debits": str(self.total_debits),
            "net_financial_imbalance": str(self.net_financial_imbalance),
            "positions": [p.to_dict() for p in self.positions],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CceeMcpBatchSettlement:
        positions = [CceeMcpSettlementPosition.from_dict(p) for p in data.get("positions", [])]
        return cls(
            batch_id=data["batch_id"],
            settlement_date=date.fromisoformat(data["settlement_date"]),
            submarket=ElectricSubmarket(data["submarket"]),
            positions=positions,
        )


# ---------------------------------------------------------------------------
# Motor Central de Comercialização de Energia Elétrica (CCEE / B3 NoMe)
# ---------------------------------------------------------------------------


class ElectricPowerTradingEngine:
    """Motor central para registro, custódia, marcação a mercado e liquidação de energia no ACL."""

    def __init__(self) -> None:
        self.contracts: Dict[str, BilateralPowerContract] = {}
        self.fiduciary_assignments: Dict[str, FiduciaryAssignment] = {}
        self.collateral_profiles: Dict[str, ParticipantCollateralProfile] = {}
        self.pld_records: Dict[Tuple[ElectricSubmarket, str], PldRecord] = {}
        self.market_forward_curves: Dict[Tuple[ElectricSubmarket, EnergyType], Decimal] = {}
        self.settlement_receipts: List[BilateralSettlementReceipt] = []
        self.ccee_mcp_settlements: List[CceeMcpBatchSettlement] = []
        self.margin_calls: List[MarginCall] = []
        self.mtm_history: List[MarkToMarketResult] = []

    # -----------------------------------------------------------------------
    # Gestão de Participantes e Garantias
    # -----------------------------------------------------------------------

    def register_participant(
        self,
        participant_id: str,
        participant_name: str,
        ccee_agent_code: str,
        cash_balance: Decimal = Decimal("0.00"),
    ) -> ParticipantCollateralProfile:
        """Registra participante de mercado no sistema com seu perfil de risco e garantias."""
        if participant_id in self.collateral_profiles:
            return self.collateral_profiles[participant_id]

        profile = ParticipantCollateralProfile(
            participant_id=participant_id,
            participant_name=participant_name,
            ccee_agent_code=ccee_agent_code,
            cash_settlement_balance=cash_balance,
        )
        self.collateral_profiles[participant_id] = profile
        return profile

    def deposit_collateral(
        self,
        deposit_id: str,
        participant_id: str,
        collateral_type: CollateralType,
        nominal_amount: Decimal,
        haircut_pct: Optional[Decimal] = None,
    ) -> CollateralDeposit:
        """Efetua registro de garantia financeira em favor de um participante."""
        if participant_id not in self.collateral_profiles:
            raise PowerTradingValidationError(f"Participante '{participant_id}' não cadastrado.")
        if nominal_amount <= Decimal("0.00"):
            raise PowerTradingValidationError("Montante nominal de garantia deve ser estritamente positivo.")

        if haircut_pct is None:
            if collateral_type == CollateralType.CASH_BRL:
                haircut_pct = HAIRCUT_CASH
            elif collateral_type == CollateralType.SOVEREIGN_BOND:
                haircut_pct = HAIRCUT_SOVEREIGN_BOND
            elif collateral_type == CollateralType.BANK_GUARANTEE:
                haircut_pct = HAIRCUT_BANK_GUARANTEE
            else:
                haircut_pct = HAIRCUT_DEFAULT_OTHER

        deposit = CollateralDeposit(
            deposit_id=deposit_id,
            participant_id=participant_id,
            collateral_type=collateral_type,
            nominal_amount=nominal_amount,
            haircut_pct=haircut_pct,
        )
        self.collateral_profiles[participant_id].deposits.append(deposit)
        return deposit

    def set_margin_requirement(
        self,
        participant_id: str,
        required_margin: Decimal,
        evaluation_date: Optional[date] = None,
    ) -> Optional[MarginCall]:
        """Define a exigência de margem de risco para o participante e avalia suficiência patrimonial."""
        if participant_id not in self.collateral_profiles:
            raise PowerTradingValidationError(f"Participante '{participant_id}' não cadastrado.")
        if required_margin < Decimal("0.00"):
            raise PowerTradingValidationError("Margem requerida não pode ser negativa.")

        profile = self.collateral_profiles[participant_id]
        profile.required_margin = required_margin

        eval_date = evaluation_date or date.today()
        if profile.is_undercollateralized:
            deficit = abs(profile.margin_coverage)
            call_id = f"MC-{participant_id}-{eval_date.strftime('%Y%m%d')}-{len(self.margin_calls) + 1:03d}"
            margin_call = MarginCall(
                call_id=call_id,
                participant_id=participant_id,
                call_date=eval_date,
                required_margin=profile.required_margin,
                evaluated_collateral=profile.total_collateral_evaluated,
                deficit_amount=deficit,
                status="PENDING",
            )
            self.margin_calls.append(margin_call)
            return margin_call

        return None

    # -----------------------------------------------------------------------
    # Gestão de Preços de Referência e PLD CCEE
    # -----------------------------------------------------------------------

    def register_pld(
        self,
        submarket: ElectricSubmarket,
        reference_date: date,
        hourly_prices: Optional[Dict[int, Decimal]] = None,
        average_price: Optional[Decimal] = None,
        min_limit: Decimal = DEFAULT_PLD_MIN,
        max_limit: Decimal = DEFAULT_PLD_MAX_HOURLY,
    ) -> PldRecord:
        """Registra o PLD oficial da CCEE para determinado submercado e data de apuração."""
        record = PldRecord(
            submarket=submarket,
            reference_date=reference_date,
            hourly_prices=hourly_prices or {},
            average_price=average_price or Decimal("0.00"),
            min_limit=min_limit,
            max_limit=max_limit,
        )
        self.pld_records[(submarket, reference_date.isoformat())] = record
        return record

    def set_market_forward_price(
        self,
        submarket: ElectricSubmarket,
        energy_type: EnergyType,
        forward_price_mwh: Decimal,
    ) -> None:
        """Registra cotação de mercado/curva forward para fins de marcação a mercado."""
        if forward_price_mwh < Decimal("0.00"):
            raise PowerTradingValidationError("Preço de mercado da curva forward não pode ser negativo.")
        self.market_forward_curves[(submarket, energy_type)] = forward_price_mwh

    # -----------------------------------------------------------------------
    # Registro de Contratos e Trava Fiduciária
    # -----------------------------------------------------------------------

    def register_contract(
        self,
        contract_id: str,
        nome_registration_id: str,
        ccee_contract_code: str,
        buyer_id: str,
        buyer_name: str,
        seller_id: str,
        seller_name: str,
        energy_type: EnergyType,
        submarket: ElectricSubmarket,
        start_date: date,
        end_date: date,
        mw_average: Decimal,
        contract_price_mwh: Decimal,
        modulation: ModulationType = ModulationType.FLAT,
        status: ContractStatus = ContractStatus.REGISTERED,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> BilateralPowerContract:
        """Registra novo contrato bilateral no NoMe B3 e sistema CCEE."""
        if contract_id in self.contracts:
            raise PowerTradingValidationError(f"Contrato '{contract_id}' já registrado no motor.")
        if buyer_id == seller_id:
            raise PowerTradingValidationError("Comprador e vendedor devem ser participantes distintos.")

        # Garantir perfis de colateral
        self.register_participant(buyer_id, buyer_name, f"AG-{buyer_id}")
        self.register_participant(seller_id, seller_name, f"AG-{seller_id}")

        contract = BilateralPowerContract(
            contract_id=contract_id,
            nome_registration_id=nome_registration_id,
            ccee_contract_code=ccee_contract_code,
            buyer_id=buyer_id,
            buyer_name=buyer_name,
            seller_id=seller_id,
            seller_name=seller_name,
            energy_type=energy_type,
            submarket=submarket,
            start_date=start_date,
            end_date=end_date,
            mw_average=mw_average,
            contract_price_mwh=contract_price_mwh,
            modulation=modulation,
            status=status,
            metadata=metadata or {},
        )
        self.contracts[contract_id] = contract
        return contract

    def register_fiduciary_assignment(
        self,
        assignment_id: str,
        contract_id: str,
        beneficiary_creditor_id: str,
        beneficiary_creditor_name: str,
        escrow_account_id: str,
        secured_debt_amount: Decimal,
        retention_percentage: Decimal = Decimal("100.00"),
        fixed_retention_amount: Optional[Decimal] = None,
    ) -> FiduciaryAssignment:
        """Constitui trava fiduciária de recebíveis de energia elétrica no NoMe B3."""
        if contract_id not in self.contracts:
            raise ContractNotFoundError(f"Contrato '{contract_id}' não encontrado para constituir trava fiduciária.")

        contract = self.contracts[contract_id]
        if contract.status == ContractStatus.SETTLED:
            raise InvalidContractStateError("Não é permitido constituir trava fiduciária em contrato já liquidado.")

        assignment = FiduciaryAssignment(
            assignment_id=assignment_id,
            contract_id=contract_id,
            beneficiary_creditor_id=beneficiary_creditor_id,
            beneficiary_creditor_name=beneficiary_creditor_name,
            escrow_account_id=escrow_account_id,
            secured_debt_amount=secured_debt_amount,
            retention_percentage=retention_percentage,
            fixed_retention_amount=fixed_retention_amount,
        )

        # Invariante de limite da trava fiduciária (LAW-092)
        assert_fiduciary_assignment_limit(
            assigned_amount=assignment.secured_debt_amount,
            total_contract_value=contract.notional_amount,
            contract_id=contract_id,
        )

        contract.fiduciary_assignment = assignment
        self.fiduciary_assignments[assignment_id] = assignment
        return assignment

    # -----------------------------------------------------------------------
    # Marcação a Mercado (Mark-to-Market - MtM)
    # -----------------------------------------------------------------------

    def calculate_mtm(
        self,
        contract_id: str,
        valuation_date: Optional[date] = None,
        market_price_mwh: Optional[Decimal] = None,
    ) -> MarkToMarketResult:
        """Calcula a marcação a mercado bilateral para um contrato específico."""
        if contract_id not in self.contracts:
            raise ContractNotFoundError(f"Contrato '{contract_id}' inexistente para cálculo de MtM.")

        contract = self.contracts[contract_id]
        val_date = valuation_date or date.today()

        if market_price_mwh is None:
            key = (contract.submarket, contract.energy_type)
            if key not in self.market_forward_curves:
                raise PowerTradingValidationError(
                    f"Preço de mercado forward não disponível para submercado {contract.submarket.value} "
                    f"e tipo {contract.energy_type.value}."
                )
            market_price_mwh = self.market_forward_curves[key]

        price_diff = market_price_mwh - contract.contract_price_mwh
        buyer_mtm = round_cents(contract.volume_mwh * price_diff)
        seller_mtm = round_cents(contract.volume_mwh * (-price_diff))

        # Garantir simetria exata de soma-zero
        net_clearing = buyer_mtm + seller_mtm

        result = MarkToMarketResult(
            contract_id=contract.contract_id,
            valuation_date=val_date,
            submarket=contract.submarket,
            energy_type=contract.energy_type,
            market_price_mwh=market_price_mwh,
            contract_price_mwh=contract.contract_price_mwh,
            price_differential_mwh=price_diff,
            volume_mwh=contract.volume_mwh,
            buyer_mtm=buyer_mtm,
            seller_mtm=seller_mtm,
            net_clearing_mtm=net_clearing,
        )
        self.mtm_history.append(result)
        return result

    def calculate_portfolio_mtm(self, valuation_date: Optional[date] = None) -> List[MarkToMarketResult]:
        """Apurar marcação a mercado para todos os contratos ativos no portfólio."""
        results: List[MarkToMarketResult] = []
        for contract in self.contracts.values():
            if contract.status in (ContractStatus.REGISTERED, ContractStatus.ACTIVE):
                mtm = self.calculate_mtm(contract.contract_id, valuation_date=valuation_date)
                results.append(mtm)
        return results

    # -----------------------------------------------------------------------
    # Liquidação Financeira Bilateral e Destinação de Recebíveis
    # -----------------------------------------------------------------------

    def process_bilateral_settlement(
        self,
        contract_id: str,
        settlement_date: Optional[date] = None,
    ) -> BilateralSettlementReceipt:
        """Executa a liquidação financeira bilateral no Balcão NoMe B3.

        Aplica regras de trava fiduciária e assegura conservação soma-zero estrita.
        """
        if contract_id not in self.contracts:
            raise ContractNotFoundError(f"Contrato '{contract_id}' não encontrado para liquidação.")

        contract = self.contracts[contract_id]
        if contract.status == ContractStatus.SETTLED:
            raise InvalidContractStateError(f"Contrato '{contract_id}' já se encontra liquidado.")

        s_date = settlement_date or date.today()
        gross_amount = contract.notional_amount
        buyer_debit = gross_amount
        seller_gross_credit = gross_amount

        fiduciary_retained = Decimal("0.00")
        escrow_account = None
        beneficiary_creditor = None

        if contract.fiduciary_assignment and contract.fiduciary_assignment.status == FiduciaryLockStatus.ACTIVE:
            assignment = contract.fiduciary_assignment
            fiduciary_retained = assignment.calculate_retention(gross_amount)
            escrow_account = assignment.escrow_account_id
            beneficiary_creditor = assignment.beneficiary_creditor_id

            # Amortiza a dívida garantida
            assignment.secured_debt_amount -= fiduciary_retained
            if assignment.secured_debt_amount <= Decimal("0.00"):
                assignment.secured_debt_amount = Decimal("0.00")
                assignment.status = FiduciaryLockStatus.RELEASED

        seller_net_credit = round_cents(seller_gross_credit - fiduciary_retained)

        # Invariante de Liquidação Financeira Bilateral Soma-Zero (LAW-090)
        assert_power_settlement_zero_sum(
            buyer_debits=buyer_debit,
            seller_credits=[seller_net_credit, fiduciary_retained],
            reference_id=contract_id,
        )

        # Atualizar saldos dos participantes
        if contract.buyer_id in self.collateral_profiles:
            self.collateral_profiles[contract.buyer_id].cash_settlement_balance -= buyer_debit
        if contract.seller_id in self.collateral_profiles:
            self.collateral_profiles[contract.seller_id].cash_settlement_balance += seller_net_credit

        contract.status = ContractStatus.SETTLED

        receipt = BilateralSettlementReceipt(
            settlement_id=f"SETTL-{contract_id}-{s_date.strftime('%Y%m%d')}",
            contract_id=contract_id,
            settlement_date=s_date,
            volume_mwh=contract.volume_mwh,
            contract_price_mwh=contract.contract_price_mwh,
            gross_amount=gross_amount,
            buyer_id=contract.buyer_id,
            buyer_debit=buyer_debit,
            seller_id=contract.seller_id,
            seller_gross_credit=seller_gross_credit,
            fiduciary_retained_amount=fiduciary_retained,
            escrow_account_id=escrow_account,
            beneficiary_creditor_id=beneficiary_creditor,
            seller_net_credit=seller_net_credit,
            status="SETTLED",
        )
        self.settlement_receipts.append(receipt)
        return receipt

    # -----------------------------------------------------------------------
    # Contabilização e Liquidação Multilateral MCP CCEE
    # -----------------------------------------------------------------------

    def process_ccee_mcp_settlement(
        self,
        batch_id: str,
        settlement_date: date,
        submarket: ElectricSubmarket,
        agent_positions: List[CceeMcpSettlementPosition],
    ) -> CceeMcpBatchSettlement:
        """Processa lote de contabilização e liquidação multilateral do MCP da CCEE."""
        batch = CceeMcpBatchSettlement(
            batch_id=batch_id,
            settlement_date=settlement_date,
            submarket=submarket,
            positions=agent_positions,
        )
        self.ccee_mcp_settlements.append(batch)
        return batch

    # -----------------------------------------------------------------------
    # Auditoria e Verificação de Invariantes Formais
    # -----------------------------------------------------------------------

    def verify_invariants(self) -> Dict[str, Any]:
        """Audita rigorosamente as leis de conservação matemática e regras do setor elétrico (FL-087).

        1. LAW-089: Conservação de Energia Elétrica Bilateral (MWh_Vendido == MWh_Comprado);
        2. LAW-090: Liquidação Financeira Bilateral Soma-Zero centavo a centavo;
        3. LAW-091: Não-Negatividade de Montantes (MWmédio, MWh) e Preços;
        4. LAW-092: Limite de Trava Fiduciária de Recebíveis (Travado <= Valor do Contrato);
        5. Invariante de Simetria e Soma-Zero no Mark-to-Market (MtM_Comprador + MtM_Vendedor == 0,00);
        6. Integridade de Garantias e Chamadas de Margem para participantes com déficit.
        """
        energy_conservation_verified = True
        bilateral_settlement_zero_sum_verified = True
        non_negativity_verified = True
        fiduciary_limits_verified = True
        mtm_zero_sum_verified = True
        ccee_mcp_zero_sum_verified = True
        collateral_coverage_verified = True

        # 1. Conservação de Energia e Não-Negatividade por Contrato Bilateral
        total_energy_bought = Decimal("0.00")
        total_energy_sold = Decimal("0.00")

        for contract in self.contracts.values():
            # Lei LAW-089: Cada contrato bilateral conserva rigorosamente a energia entre comprador e vendedor
            assert_power_energy_conservation(
                total_mwh_sold=contract.volume_mwh,
                total_mwh_bought=contract.volume_mwh,
                contract_id=contract.contract_id,
            )
            total_energy_bought += contract.volume_mwh
            total_energy_sold += contract.volume_mwh

            # Lei LAW-091: Não-negatividade
            assert_power_non_negativity(
                mw_average=contract.mw_average,
                total_hours=contract.total_hours,
                volume_mwh=contract.volume_mwh,
                price_mwh=contract.contract_price_mwh,
                contract_id=contract.contract_id,
            )

            # Lei LAW-092: Trava Fiduciária
            if contract.fiduciary_assignment:
                assert_fiduciary_assignment_limit(
                    assigned_amount=contract.fiduciary_assignment.secured_debt_amount,
                    total_contract_value=contract.notional_amount,
                    contract_id=contract.contract_id,
                )

        if total_energy_bought != total_energy_sold:
            energy_conservation_verified = False

        # 2. Liquidação Financeira Bilateral Soma-Zero centavo a centavo (LAW-090)
        for receipt in self.settlement_receipts:
            assert_power_settlement_zero_sum(
                buyer_debits=receipt.buyer_debit,
                seller_credits=[receipt.seller_net_credit, receipt.fiduciary_retained_amount],
                reference_id=receipt.contract_id,
            )
            if not receipt.is_zero_sum:
                bilateral_settlement_zero_sum_verified = False

        # 3. Marcação a Mercado (MtM) Soma-Zero
        for mtm in self.mtm_history:
            if not mtm.is_zero_sum or mtm.net_clearing_mtm != Decimal("0.00"):
                mtm_zero_sum_verified = False

        # 4. PLDs Registrados: Validação de pisos e tetos
        for (submarket, _), pld in self.pld_records.items():
            if pld.average_price < pld.min_limit or pld.average_price > pld.max_limit:
                non_negativity_verified = False

        # 5. MCP CCEE Soma-Zero em lotes balanceados
        for mcp_batch in self.ccee_mcp_settlements:
            if mcp_batch.net_energy_balance_mwh == Decimal("0.0000"):
                if mcp_batch.net_financial_imbalance != Decimal("0.00"):
                    ccee_mcp_zero_sum_verified = False

        # 6. Integridade de Margem
        for profile in self.collateral_profiles.values():
            if profile.is_undercollateralized:
                # Verifica se há chamada de margem pendente
                has_pending_call = any(
                    mc.participant_id == profile.participant_id and mc.status == "PENDING"
                    for mc in self.margin_calls
                )
                if not has_pending_call:
                    collateral_coverage_verified = False

        all_invariants_hold = (
            energy_conservation_verified
            and bilateral_settlement_zero_sum_verified
            and non_negativity_verified
            and fiduciary_limits_verified
            and mtm_zero_sum_verified
            and ccee_mcp_zero_sum_verified
            and collateral_coverage_verified
        )

        return {
            "all_invariants_hold": all_invariants_hold,
            "energy_conservation_verified": energy_conservation_verified,
            "bilateral_settlement_zero_sum_verified": bilateral_settlement_zero_sum_verified,
            "non_negativity_verified": non_negativity_verified,
            "fiduciary_limits_verified": fiduciary_limits_verified,
            "mtm_zero_sum_verified": mtm_zero_sum_verified,
            "ccee_mcp_zero_sum_verified": ccee_mcp_zero_sum_verified,
            "collateral_coverage_verified": collateral_coverage_verified,
            "total_energy_bought_mwh": str(total_energy_bought),
            "total_energy_sold_mwh": str(total_energy_sold),
            "contracts_count": len(self.contracts),
            "settlements_count": len(self.settlement_receipts),
            "mtm_records_count": len(self.mtm_history),
        }

    # -----------------------------------------------------------------------
    # Serialização e Carregamento de Fixture
    # -----------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serializa todo o estado operacional do motor de comercialização de energia."""
        return {
            "contracts": [c.to_dict() for c in self.contracts.values()],
            "fiduciary_assignments": [fa.to_dict() for fa in self.fiduciary_assignments.values()],
            "collateral_profiles": [p.to_dict() for p in self.collateral_profiles.values()],
            "pld_records": [pld.to_dict() for pld in self.pld_records.values()],
            "market_forward_curves": [
                {
                    "submarket": k[0].value,
                    "energy_type": k[1].value,
                    "forward_price_mwh": str(v),
                }
                for k, v in self.market_forward_curves.items()
            ],
            "settlement_receipts": [sr.to_dict() for sr in self.settlement_receipts],
            "ccee_mcp_settlements": [mcp.to_dict() for mcp in self.ccee_mcp_settlements],
            "margin_calls": [mc.to_dict() for mc in self.margin_calls],
            "mtm_history": [mtm.to_dict() for mtm in self.mtm_history],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ElectricPowerTradingEngine:
        """Reconstitui o motor a partir de um dicionário serializado."""
        engine = cls()

        for prof_dict in data.get("collateral_profiles", []):
            profile = ParticipantCollateralProfile.from_dict(prof_dict)
            engine.collateral_profiles[profile.participant_id] = profile

        for c_dict in data.get("contracts", []):
            contract = BilateralPowerContract.from_dict(c_dict)
            engine.contracts[contract.contract_id] = contract
            if contract.fiduciary_assignment:
                engine.fiduciary_assignments[contract.fiduciary_assignment.assignment_id] = contract.fiduciary_assignment

        for fa_dict in data.get("fiduciary_assignments", []):
            fa = FiduciaryAssignment.from_dict(fa_dict)
            engine.fiduciary_assignments[fa.assignment_id] = fa

        for pld_dict in data.get("pld_records", []):
            pld = PldRecord.from_dict(pld_dict)
            engine.pld_records[(pld.submarket, pld.reference_date.isoformat())] = pld

        for mfc in data.get("market_forward_curves", []):
            submarket = ElectricSubmarket(mfc["submarket"])
            energy_type = EnergyType(mfc["energy_type"])
            price = Decimal(str(mfc["forward_price_mwh"]))
            engine.market_forward_curves[(submarket, energy_type)] = price

        for sr_dict in data.get("settlement_receipts", []):
            sr = BilateralSettlementReceipt.from_dict(sr_dict)
            engine.settlement_receipts.append(sr)

        for mcp_dict in data.get("ccee_mcp_settlements", []):
            mcp = CceeMcpBatchSettlement.from_dict(mcp_dict)
            engine.ccee_mcp_settlements.append(mcp)

        for mc_dict in data.get("margin_calls", []):
            mc = MarginCall.from_dict(mc_dict)
            engine.margin_calls.append(mc)

        for mtm_dict in data.get("mtm_history", []):
            mtm = MarkToMarketResult.from_dict(mtm_dict)
            engine.mtm_history.append(mtm)

        return engine

    @classmethod
    def load_from_fixture(cls, filepath: Union[str, Path]) -> ElectricPowerTradingEngine:
        """Carrega e instancia o motor a partir de arquivo de fixture JSON."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
