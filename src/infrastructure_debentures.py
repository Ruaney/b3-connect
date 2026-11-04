"""Módulo de Balcão NoMe & Central Depositária B3 — Debêntures de Infraestrutura (FL-057).

Este módulo implementa o modelo determinístico de escrituração, atualização monetária,
apuração de cupom de juros, cronograma de amortização (Bullet, Constante, Variável),
tributação de IRRF e liquidação financeira para Debêntures Incentivadas e de Infraestrutura,
abrangendo:
- Lei nº 12.431/2011: Incentivo fiscal ao investidor (PF e INR com 0% IRRF, PJ com 15%).
- Lei nº 14.801/2024: Incentivo fiscal ao emissor (exclusão de 130% dos juros na apuração
  de IRPJ/CSLL: 100% como despesa financeira regular + 30% como bônus extraordinário).
- Conservação estrita do Saldo Devedor Global: VNE_Global == Quantidade * VNE_Unitario.
- Conservação da Liquidação Multilateral: Débito da Emissora == Créditos Líquidos + IRRF.

Estritamente baseado na biblioteca padrão do Python (sem dependências externas).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Constantes de Precisão Numérica e Arredondamento B3
# ---------------------------------------------------------------------------

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_UNIT = Decimal("0.00000001")
QUANTIZE_RATE = Decimal("0.00000001")
QUANTIZE_QTY = Decimal("1")


def round_cents(val: Decimal) -> Decimal:
    """Arredonda valor monetário para 2 casas decimais via ROUND_HALF_UP."""
    return val.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)


def round_unit(val: Decimal) -> Decimal:
    """Arredonda valor unitário (VNE/Juros/Amortização) para 8 casas decimais."""
    return val.quantize(QUANTIZE_UNIT, rounding=ROUND_HALF_UP)


def to_decimal(value: Any, field_name: str = "campo") -> Decimal:
    """Converte valor para Decimal com validação robusta."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise DebentureValidationError(f"Valor nulo não permitido para {field_name}.")
    try:
        cleaned = str(value).strip().replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError) as err:
        raise DebentureValidationError(f"Valor inválido '{value}' para {field_name}: {err}") from err


def to_date(value: Any, field_name: str = "data") -> date:
    """Converte string ISO 'YYYY-MM-DD' ou datetime/date para date."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value or not isinstance(value, str):
        raise DebentureValidationError(f"Data inválida ou vazia para {field_name}: {value}")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as err:
        raise DebentureValidationError(f"Formato inválido de data em {field_name} ('{value}'). Use YYYY-MM-DD.") from err


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class DebentureError(Exception):
    """Exceção base para o subsistema de Debêntures de Infraestrutura B3."""
    pass


class DebentureValidationError(DebentureError):
    """Violação de regras cadastrais, parâmetros ou integridade de dados."""
    pass


class DebentureSettlementError(DebentureError):
    """Falha no processamento financeiro de juros, amortização ou liquidação."""
    pass


class DebentureInvariantError(DebentureError):
    """Violação matemática de conservação de saldo devedor, custódia ou financeiro."""
    pass


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------

class DebentureLegalFramework(str, Enum):
    """Regime legal e fiscal aplicável à emissão de debêntures."""
    LEI_12431_2011 = "LEI_12431_2011"
    LEI_14801_2024 = "LEI_14801_2024"

    @classmethod
    def from_str(cls, value: Any) -> DebentureLegalFramework:
        if isinstance(value, cls):
            return value
        if not value:
            raise DebentureValidationError("Marco legal não pode ser vazio.")
        norm = str(value).strip().upper().replace(" ", "_").replace(".", "").replace("/", "_")
        for member in cls:
            if norm in (member.value, member.name) or member.value.replace("_", "") in norm:
                return member
        if "12431" in norm or "12.431" in norm:
            return cls.LEI_12431_2011
        if "14801" in norm or "14.801" in norm:
            return cls.LEI_14801_2024
        raise DebentureValidationError(f"Marco legal desconhecido: '{value}'. Opções: {[m.value for m in cls]}")


class AmortizationType(str, Enum):
    """Modalidade de amortização do principal da debênture."""
    BULLET = "BULLET"
    CONSTANT = "CONSTANT"
    VARIABLE = "VARIABLE"

    @classmethod
    def from_str(cls, value: Any) -> AmortizationType:
        if isinstance(value, cls):
            return value
        if not value:
            raise DebentureValidationError("Modalidade de amortização não pode ser vazia.")
        norm = str(value).strip().upper()
        for member in cls:
            if norm in (member.value, member.name):
                return member
        synonyms = {
            "BULLET": cls.BULLET,
            "UNICA": cls.BULLET,
            "VENCIMENTO": cls.BULLET,
            "CONSTANTE": cls.CONSTANT,
            "CONSTANT": cls.CONSTANT,
            "SAC": cls.CONSTANT,
            "VARIAVEL": cls.VARIABLE,
            "VARIABLE": cls.VARIABLE,
            "CUSTOM": cls.VARIABLE,
        }
        if norm in synonyms:
            return synonyms[norm]
        raise DebentureValidationError(f"Modalidade de amortização desconhecida: '{value}'.")


class InvestorTaxCategory(str, Enum):
    """Perfil tributário do titular da conta de custódia depositária."""
    INDIVIDUAL_PF = "INDIVIDUAL_PF"
    CORPORATE_PJ = "CORPORATE_PJ"
    NON_RESIDENT_INR = "NON_RESIDENT_INR"

    @classmethod
    def from_str(cls, value: Any) -> InvestorTaxCategory:
        if isinstance(value, cls):
            return value
        if not value:
            raise DebentureValidationError("Categoria tributária do investidor não pode ser vazia.")
        norm = str(value).strip().upper()
        for member in cls:
            if norm in (member.value, member.name):
                return member
        synonyms = {
            "PF": cls.INDIVIDUAL_PF,
            "PESSOA_FISICA": cls.INDIVIDUAL_PF,
            "INDIVIDUAL": cls.INDIVIDUAL_PF,
            "INDIVIDUAL_PF": cls.INDIVIDUAL_PF,
            "PJ": cls.CORPORATE_PJ,
            "PESSOA_JURIDICA": cls.CORPORATE_PJ,
            "CORPORATE": cls.CORPORATE_PJ,
            "CORPORATE_PJ": cls.CORPORATE_PJ,
            "INR": cls.NON_RESIDENT_INR,
            "NAO_RESIDENTE": cls.NON_RESIDENT_INR,
            "NON_RESIDENT": cls.NON_RESIDENT_INR,
            "NON_RESIDENT_INR": cls.NON_RESIDENT_INR,
        }
        if norm in synonyms:
            return synonyms[norm]
        raise DebentureValidationError(f"Categoria tributária desconhecida: '{value}'.")


class ScheduleEventType(str, Enum):
    """Natureza do evento programado no cronograma de pagamentos."""
    COUPON = "COUPON"
    AMORTIZATION = "AMORTIZATION"
    COUPON_AND_AMORTIZATION = "COUPON_AND_AMORTIZATION"

    @classmethod
    def from_str(cls, value: Any) -> ScheduleEventType:
        if isinstance(value, cls):
            return value
        if not value:
            return cls.COUPON
        norm = str(value).strip().upper()
        for member in cls:
            if norm in (member.value, member.name):
                return member
        synonyms = {
            "JUROS": cls.COUPON,
            "CUPOM": cls.COUPON,
            "AMORT": cls.AMORTIZATION,
            "AMORTIZACAO": cls.AMORTIZATION,
            "AMBOS": cls.COUPON_AND_AMORTIZATION,
            "JUROS_E_AMORTIZACAO": cls.COUPON_AND_AMORTIZATION,
        }
        if norm in synonyms:
            return synonyms[norm]
        raise DebentureValidationError(f"Tipo de evento de cronograma inválido: '{value}'.")


# ---------------------------------------------------------------------------
# Entidades de Dados (Dataclasses)
# ---------------------------------------------------------------------------

@dataclass
class CustodyPosition:
    """Posição individual de custódia mantida na Central Depositária B3."""
    account_id: str
    investor_name: str
    investor_document: str
    investor_tax_category: InvestorTaxCategory
    quantity: Decimal
    holding_days: int = 730

    def __post_init__(self) -> None:
        self.account_id = str(self.account_id).strip()
        self.investor_name = str(self.investor_name).strip()
        self.investor_document = str(self.investor_document).strip()
        self.investor_tax_category = InvestorTaxCategory.from_str(self.investor_tax_category)
        self.quantity = to_decimal(self.quantity, "quantity").quantize(QUANTIZE_QTY)
        if self.quantity <= Decimal("0"):
            raise DebentureValidationError(f"Quantidade em custódia deve ser positiva: {self.quantity}")
        if self.holding_days < 0:
            raise DebentureValidationError(f"Dias de custódia não podem ser negativos: {self.holding_days}")


@dataclass
class PaymentScheduleEvent:
    """Evento periódico do cronograma de pagamentos de juros e amortização."""
    event_id: str
    event_date: date
    event_type: ScheduleEventType
    inflation_rate: Decimal
    coupon_rate: Decimal
    amortization_percentage: Decimal
    processed: bool = False
    unit_vne_before: Optional[Decimal] = None
    unit_vne_updated: Optional[Decimal] = None
    unit_interest: Optional[Decimal] = None
    unit_amortization: Optional[Decimal] = None
    unit_vne_after: Optional[Decimal] = None
    global_vne_after: Optional[Decimal] = None
    total_issuer_debt_paid: Optional[Decimal] = None

    def __post_init__(self) -> None:
        self.event_id = str(self.event_id).strip()
        self.event_date = to_date(self.event_date, "event_date")
        self.event_type = ScheduleEventType.from_str(self.event_type)
        self.inflation_rate = to_decimal(self.inflation_rate, "inflation_rate")
        self.coupon_rate = to_decimal(self.coupon_rate, "coupon_rate")
        self.amortization_percentage = to_decimal(self.amortization_percentage, "amortization_percentage")

        if self.amortization_percentage < Decimal("0.00") or self.amortization_percentage > Decimal("1.00000001"):
            raise DebentureValidationError(
                f"Percentual de amortização deve estar entre 0 e 1 (100%): {self.amortization_percentage}"
            )
        if self.coupon_rate < Decimal("0.00"):
            raise DebentureValidationError(f"Taxa de cupom não pode ser negativa: {self.coupon_rate}")


@dataclass
class DebentureSettlementReceipt:
    """Recibo individual de liquidação financeira e repasse em conta de custódia."""
    receipt_id: str
    event_id: str
    settlement_date: date
    asset_code: str
    account_id: str
    investor_name: str
    investor_document: str
    investor_tax_category: InvestorTaxCategory
    quantity: Decimal
    unit_interest_amount: Decimal
    unit_amortization_amount: Decimal
    gross_interest_amount: Decimal
    gross_amortization_amount: Decimal
    total_gross_amount: Decimal
    tax_rate: Decimal
    withheld_tax_amount: Decimal
    net_settlement_amount: Decimal
    unit_vne_post_event: Decimal
    global_vne_post_event: Decimal
    issuer_tax_deduction_bonus: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.investor_tax_category = InvestorTaxCategory.from_str(self.investor_tax_category)
        self.settlement_date = to_date(self.settlement_date, "settlement_date")
        self.quantity = to_decimal(self.quantity, "quantity")
        self.unit_interest_amount = round_unit(to_decimal(self.unit_interest_amount, "unit_interest_amount"))
        self.unit_amortization_amount = round_unit(to_decimal(self.unit_amortization_amount, "unit_amortization_amount"))
        self.gross_interest_amount = round_cents(to_decimal(self.gross_interest_amount, "gross_interest_amount"))
        self.gross_amortization_amount = round_cents(to_decimal(self.gross_amortization_amount, "gross_amortization_amount"))
        self.total_gross_amount = round_cents(to_decimal(self.total_gross_amount, "total_gross_amount"))
        self.tax_rate = to_decimal(self.tax_rate, "tax_rate")
        self.withheld_tax_amount = round_cents(to_decimal(self.withheld_tax_amount, "withheld_tax_amount"))
        self.net_settlement_amount = round_cents(to_decimal(self.net_settlement_amount, "net_settlement_amount"))
        self.unit_vne_post_event = round_unit(to_decimal(self.unit_vne_post_event, "unit_vne_post_event"))
        self.global_vne_post_event = round_cents(to_decimal(self.global_vne_post_event, "global_vne_post_event"))
        self.issuer_tax_deduction_bonus = round_cents(to_decimal(self.issuer_tax_deduction_bonus, "issuer_tax_deduction_bonus"))


@dataclass
class DebentureEmission:
    """Cadastro central e escrituração de emissão de Debêntures na B3."""
    asset_code: str
    isin: str
    issuer_name: str
    issuer_cnpj: str
    sector: str
    legal_framework: DebentureLegalFramework
    amortization_type: AmortizationType
    emission_date: date
    maturity_date: date
    issued_quantity: Decimal
    circulating_quantity: Decimal
    nominal_original_value: Decimal
    nominal_escalonado_value: Decimal
    annual_coupon_rate: Decimal
    indexer: str = "IPCA"
    payment_schedule: List[PaymentScheduleEvent] = field(default_factory=list)
    custody_positions: List[CustodyPosition] = field(default_factory=list)
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        self.asset_code = str(self.asset_code).strip().upper()
        self.isin = str(self.isin).strip().upper()
        self.issuer_name = str(self.issuer_name).strip()
        self.issuer_cnpj = str(self.issuer_cnpj).strip()
        self.sector = str(self.sector).strip().upper()
        self.legal_framework = DebentureLegalFramework.from_str(self.legal_framework)
        self.amortization_type = AmortizationType.from_str(self.amortization_type)
        self.emission_date = to_date(self.emission_date, "emission_date")
        self.maturity_date = to_date(self.maturity_date, "maturity_date")
        self.issued_quantity = to_decimal(self.issued_quantity, "issued_quantity").quantize(QUANTIZE_QTY)
        self.circulating_quantity = to_decimal(self.circulating_quantity, "circulating_quantity").quantize(QUANTIZE_QTY)
        self.nominal_original_value = round_cents(to_decimal(self.nominal_original_value, "nominal_original_value"))
        self.nominal_escalonado_value = round_unit(to_decimal(self.nominal_escalonado_value, "nominal_escalonado_value"))
        self.annual_coupon_rate = to_decimal(self.annual_coupon_rate, "annual_coupon_rate")
        self.indexer = str(self.indexer).strip().upper()

        if self.maturity_date <= self.emission_date:
            raise DebentureValidationError("Data de vencimento deve ser posterior à data de emissão.")
        if self.circulating_quantity > self.issued_quantity:
            raise DebentureValidationError("Quantidade em circulação não pode exceder a quantidade emitida.")

    @property
    def global_nominal_escalonado_value(self) -> Decimal:
        """Calcula o Saldo Devedor Global consolidado do principal."""
        return round_cents(self.circulating_quantity * self.nominal_escalonado_value)

    @property
    def total_custody_quantity(self) -> Decimal:
        """Soma das quantidades custodiadas em todas as contas da Central Depositária."""
        return sum((pos.quantity for pos in self.custody_positions), Decimal("0"))


# ---------------------------------------------------------------------------
# Motor de Cálculo e Liquidação (Engine)
# ---------------------------------------------------------------------------

class InfrastructureDebenturesEngine:
    """Motor determinístico de escrituração, liquidação e auditoria de debêntures B3."""

    def __init__(self) -> None:
        self.emissions: Dict[str, DebentureEmission] = {}
        self.settlement_history: List[DebentureSettlementReceipt] = []

    @classmethod
    def load_from_json(cls, source: Union[str, Path, Dict[str, Any]]) -> InfrastructureDebenturesEngine:
        """Instancia o motor a partir de arquivo JSON ou dicionário estruturado."""
        engine = cls()
        data: Dict[str, Any]
        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.is_file():
                raise DebentureValidationError(f"Arquivo de fixture não encontrado: {path}")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        elif isinstance(source, dict):
            data = source
        else:
            raise DebentureValidationError("Fonte de dados deve ser arquivo JSON ou dict.")

        emissions_data = data.get("emissions", [])
        for em_raw in emissions_data:
            engine.add_emission_from_dict(em_raw)

        return engine

    def add_emission(self, emission: DebentureEmission) -> None:
        """Registra uma emissão no motor e valida a integridade de custódia inicial."""
        if emission.asset_code in self.emissions:
            raise DebentureValidationError(f"Debênture '{emission.asset_code}' já cadastrada.")

        # Validação do Invariante de Quantidade em Custódia
        if emission.custody_positions:
            total_custody = emission.total_custody_quantity
            if total_custody != emission.circulating_quantity:
                raise DebentureInvariantError(
                    f"Violação de conservação de custódia na debênture {emission.asset_code}: "
                    f"soma das contas ({total_custody}) != quantidade em circulação ({emission.circulating_quantity})."
                )

        self.emissions[emission.asset_code] = emission

    def add_emission_from_dict(self, data: Dict[str, Any]) -> DebentureEmission:
        """Converte dicionário em instância de DebentureEmission e cadastra no motor."""
        events: List[PaymentScheduleEvent] = []
        for ev_raw in data.get("payment_schedule", []):
            events.append(
                PaymentScheduleEvent(
                    event_id=ev_raw["event_id"],
                    event_date=ev_raw["event_date"],
                    event_type=ev_raw.get("event_type", ScheduleEventType.COUPON),
                    inflation_rate=ev_raw.get("inflation_rate", "0.00"),
                    coupon_rate=ev_raw.get("coupon_rate", "0.00"),
                    amortization_percentage=ev_raw.get("amortization_percentage", "0.00"),
                )
            )

        positions: List[CustodyPosition] = []
        for pos_raw in data.get("custody_positions", []):
            positions.append(
                CustodyPosition(
                    account_id=pos_raw["account_id"],
                    investor_name=pos_raw["investor_name"],
                    investor_document=pos_raw["investor_document"],
                    investor_tax_category=pos_raw["investor_tax_category"],
                    quantity=pos_raw["quantity"],
                    holding_days=int(pos_raw.get("holding_days", 730)),
                )
            )

        emission = DebentureEmission(
            asset_code=data["asset_code"],
            isin=data["isin"],
            issuer_name=data["issuer_name"],
            issuer_cnpj=data["issuer_cnpj"],
            sector=data["sector"],
            legal_framework=data["legal_framework"],
            amortization_type=data["amortization_type"],
            emission_date=data["emission_date"],
            maturity_date=data["maturity_date"],
            issued_quantity=data["issued_quantity"],
            circulating_quantity=data["circulating_quantity"],
            nominal_original_value=data["nominal_original_value"],
            nominal_escalonado_value=data.get("nominal_escalonado_value", data["nominal_original_value"]),
            annual_coupon_rate=data["annual_coupon_rate"],
            indexer=data.get("indexer", "IPCA"),
            payment_schedule=events,
            custody_positions=positions,
            status=data.get("status", "ACTIVE"),
        )
        self.add_emission(emission)
        return emission

    def get_emission(self, asset_code: str) -> DebentureEmission:
        """Obtém a emissão cadastrada pelo código do ativo."""
        code = str(asset_code).strip().upper()
        if code not in self.emissions:
            raise DebentureValidationError(f"Debênture '{code}' não encontrada.")
        return self.emissions[code]

    @staticmethod
    def calculate_tax_rate(
        legal_framework: DebentureLegalFramework,
        tax_category: InvestorTaxCategory,
        holding_days: int = 730,
    ) -> Decimal:
        """Determina deterministicamente a alíquota de IRRF conforme marco legal e perfil."""
        legal = DebentureLegalFramework.from_str(legal_framework)
        category = InvestorTaxCategory.from_str(tax_category)

        if legal == DebentureLegalFramework.LEI_12431_2011:
            if category in (InvestorTaxCategory.INDIVIDUAL_PF, InvestorTaxCategory.NON_RESIDENT_INR):
                return Decimal("0.00")
            if category == InvestorTaxCategory.CORPORATE_PJ:
                return Decimal("0.15")

        elif legal == DebentureLegalFramework.LEI_14801_2024:
            # Tabela regressiva de Renda Fixa (15% a 22,5%)
            if holding_days <= 180:
                return Decimal("0.225")
            elif holding_days <= 360:
                return Decimal("0.200")
            elif holding_days <= 720:
                return Decimal("0.175")
            else:
                return Decimal("0.150")

        raise DebentureValidationError(f"Combinação não mapeada: {legal} / {category}")

    def process_schedule_event(
        self, asset_code: str, event_id: str
    ) -> List[DebentureSettlementReceipt]:
        """Processa a liquidação financeira de um evento específico do cronograma."""
        emission = self.get_emission(asset_code)

        target_event: Optional[PaymentScheduleEvent] = None
        for ev in emission.payment_schedule:
            if ev.event_id == event_id:
                target_event = ev
                break

        if target_event is None:
            raise DebentureValidationError(
                f"Evento '{event_id}' não localizado no cronograma da debênture '{asset_code}'."
            )
        if target_event.processed:
            raise DebentureSettlementError(
                f"Evento '{event_id}' da debênture '{asset_code}' já foi liquidado anteriormente."
            )

        # Invariante pré-evento: conservação de saldo devedor
        expected_pre_global = round_cents(emission.circulating_quantity * emission.nominal_escalonado_value)
        if emission.global_nominal_escalonado_value != expected_pre_global:
            raise DebentureInvariantError(
                f"Invariante pré-evento violada: {emission.global_nominal_escalonado_value} != {expected_pre_global}"
            )

        vne_before = emission.nominal_escalonado_value
        target_event.unit_vne_before = vne_before

        # 1. Atualização monetária pelo fator de inflação (IPCA)
        vne_updated = round_unit(vne_before * (Decimal("1.00000000") + target_event.inflation_rate))
        target_event.unit_vne_updated = vne_updated

        # 2. Cálculo dos Juros (Cupom do Período) sobre o VNE atualizado
        unit_interest = round_unit(vne_updated * target_event.coupon_rate)
        target_event.unit_interest = unit_interest

        # 3. Cálculo da Amortização de Principal
        if target_event.amortization_percentage >= Decimal("0.99999999"):
            # Resgate / amortização total de 100%
            unit_amortization = vne_updated
            unit_vne_after = Decimal("0.00000000")
        elif target_event.amortization_percentage > Decimal("0.00000000"):
            unit_amortization = round_unit(vne_updated * target_event.amortization_percentage)
            unit_vne_after = round_unit(vne_updated - unit_amortization)
        else:
            unit_amortization = Decimal("0.00000000")
            unit_vne_after = vne_updated

        target_event.unit_amortization = unit_amortization
        target_event.unit_vne_after = unit_vne_after

        # Atualiza a emissão com o novo VNE unitário
        emission.nominal_escalonado_value = unit_vne_after
        global_vne_after = round_cents(emission.circulating_quantity * unit_vne_after)
        target_event.global_vne_after = global_vne_after

        if unit_vne_after == Decimal("0.00000000"):
            emission.status = "REDEEMED"

        # 4. Processamento individual por conta de custódia depositária
        event_receipts: List[DebentureSettlementReceipt] = []
        total_gross_event = Decimal("0.00")
        total_net_event = Decimal("0.00")
        total_tax_event = Decimal("0.00")

        for pos in emission.custody_positions:
            gross_amort = round_cents(pos.quantity * unit_amortization)
            gross_interest = round_cents(pos.quantity * unit_interest)
            total_gross = gross_amort + gross_interest

            tax_rate = self.calculate_tax_rate(
                emission.legal_framework,
                pos.investor_tax_category,
                pos.holding_days,
            )
            # IRRF incide estritamente sobre os juros auferidos (não sobre amortização)
            tax_amount = round_cents(gross_interest * tax_rate)
            net_amount = total_gross - tax_amount

            # Bônus extraordinário da Lei 14.801/2024 para a emissora (30% sobre os juros)
            issuer_bonus = Decimal("0.00")
            if emission.legal_framework == DebentureLegalFramework.LEI_14801_2024:
                issuer_bonus = round_cents(gross_interest * Decimal("0.30"))

            receipt = DebentureSettlementReceipt(
                receipt_id=f"REC-{asset_code}-{target_event.event_id}-{pos.account_id}",
                event_id=target_event.event_id,
                settlement_date=target_event.event_date,
                asset_code=asset_code,
                account_id=pos.account_id,
                investor_name=pos.investor_name,
                investor_document=pos.investor_document,
                investor_tax_category=pos.investor_tax_category,
                quantity=pos.quantity,
                unit_interest_amount=unit_interest,
                unit_amortization_amount=unit_amortization,
                gross_interest_amount=gross_interest,
                gross_amortization_amount=gross_amort,
                total_gross_amount=total_gross,
                tax_rate=tax_rate,
                withheld_tax_amount=tax_amount,
                net_settlement_amount=net_amount,
                unit_vne_post_event=unit_vne_after,
                global_vne_post_event=global_vne_after,
                issuer_tax_deduction_bonus=issuer_bonus,
            )
            event_receipts.append(receipt)

            total_gross_event += total_gross
            total_net_event += net_amount
            total_tax_event += tax_amount

        # 5. Invariante da Liquidação Multilateral: Débito Total == Créditos + IRRF
        if total_gross_event != (total_net_event + total_tax_event):
            raise DebentureInvariantError(
                f"Violação da invariante multilateral no evento {event_id}: "
                f"Total Bruto ({total_gross_event}) != Líquido ({total_net_event}) + IRRF ({total_tax_event})"
            )

        # 6. Invariante do Saldo Devedor Global pós-evento
        if emission.global_nominal_escalonado_value != global_vne_after:
            raise DebentureInvariantError(
                f"Violação da invariante de Saldo Devedor pós-evento: "
                f"{emission.global_nominal_escalonado_value} != {global_vne_after}"
            )

        target_event.processed = True
        target_event.total_issuer_debt_paid = total_gross_event
        self.settlement_history.extend(event_receipts)
        return event_receipts

    def process_all_events(self, asset_code: str) -> List[DebentureSettlementReceipt]:
        """Processa em sequência cronológica todos os eventos pendentes da debênture."""
        emission = self.get_emission(asset_code)
        all_receipts: List[DebentureSettlementReceipt] = []
        for ev in emission.payment_schedule:
            if not ev.processed:
                receipts = self.process_schedule_event(asset_code, ev.event_id)
                all_receipts.extend(receipts)
        return all_receipts

    def verify_invariants(self, asset_code: str) -> Dict[str, Any]:
        """Realiza auditoria formal completa das invariantes de conservação patrimonial."""
        emission = self.get_emission(asset_code)

        # 1. Conservação de custódia
        total_custody = emission.total_custody_quantity
        if total_custody != emission.circulating_quantity:
            raise DebentureInvariantError(
                f"Custódia inconsistente: {total_custody} != {emission.circulating_quantity}"
            )

        # 2. Conservação do Saldo Devedor Global
        expected_global_vne = round_cents(emission.circulating_quantity * emission.nominal_escalonado_value)
        if emission.global_nominal_escalonado_value != expected_global_vne:
            raise DebentureInvariantError(
                f"Saldo Devedor divergente: {emission.global_nominal_escalonado_value} != {expected_global_vne}"
            )

        # 3. Conciliação de Recibos Financeiros
        receipts = [r for r in self.settlement_history if r.asset_code == asset_code]
        total_gross = sum((r.total_gross_amount for r in receipts), Decimal("0.00"))
        total_net = sum((r.net_settlement_amount for r in receipts), Decimal("0.00"))
        total_tax = sum((r.withheld_tax_amount for r in receipts), Decimal("0.00"))
        total_bonus = sum((r.issuer_tax_deduction_bonus for r in receipts), Decimal("0.00"))

        if total_gross != (total_net + total_tax):
            raise DebentureInvariantError(
                f"Invariante financeira rompida no histórico de liquidação: "
                f"Bruto ({total_gross}) != Líquido ({total_net}) + IRRF ({total_tax})"
            )

        return {
            "asset_code": emission.asset_code,
            "status": emission.status,
            "circulating_quantity": emission.circulating_quantity,
            "unit_vne": emission.nominal_escalonado_value,
            "global_vne": emission.global_nominal_escalonado_value,
            "total_settlement_receipts": len(receipts),
            "total_gross_settled": total_gross,
            "total_net_credited": total_net,
            "total_irrf_withheld": total_tax,
            "total_issuer_tax_bonus": total_bonus,
            "custody_conservation_verified": True,
            "debtor_balance_conservation_verified": True,
            "multilateral_financial_conservation_verified": True,
        }
