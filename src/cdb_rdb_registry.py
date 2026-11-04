"""Módulo de Registro, Atualização Diária de PU e Liquidação de CDBs e RDBs (FL-054).

Especificação técnica e regras de negócio para Certificados de Depósito Bancário (CDB)
e Recibos de Depósito Bancário (RDB) registrados no Balcão NoMe B3, em estrita
conformidade com:
- Resolução CMN nº 5.036/2022 (emissão de CDB e RDB)
- Lei nº 4.728/1965 (mercado de capitais)
- Lei nº 11.033/2004 (tabela regressiva de IRRF em renda fixa)
- Decreto nº 6.306/2007 (tabela regressiva de IOF em resgates até 29 dias)
- Manuais operacionais do NoMe Balcão B3 (convenção DU/252, arredondamentos e integridade escritural)

100% biblioteca padrão Python (dataclasses, Decimal, datetime, enum, json).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union


# ---------------------------------------------------------------------------
# Constantes Numéricas e de Precisão B3 / Cetip
# ---------------------------------------------------------------------------

DECIMAL_MONEY_PRECISION = Decimal("0.01")
DECIMAL_PU_PRECISION = Decimal("0.00000001")
DECIMAL_FACTOR_PRECISION = Decimal("0.00000001")
DAYS_PER_YEAR_WORKDAYS = 252

# Tabela regressiva de IOF para aplicações de curto prazo (dias 1 a 29)
IOF_REGRESSIVE_RATES: Dict[int, Decimal] = {
    1: Decimal("96.00"),
    2: Decimal("93.00"),
    3: Decimal("90.00"),
    4: Decimal("86.00"),
    5: Decimal("83.00"),
    6: Decimal("80.00"),
    7: Decimal("76.00"),
    8: Decimal("73.00"),
    9: Decimal("70.00"),
    10: Decimal("66.00"),
    11: Decimal("63.00"),
    12: Decimal("60.00"),
    13: Decimal("56.00"),
    14: Decimal("53.00"),
    15: Decimal("50.00"),
    16: Decimal("46.00"),
    17: Decimal("43.00"),
    18: Decimal("40.00"),
    19: Decimal("36.00"),
    20: Decimal("33.00"),
    21: Decimal("30.00"),
    22: Decimal("26.00"),
    23: Decimal("23.00"),
    24: Decimal("20.00"),
    25: Decimal("16.00"),
    26: Decimal("13.00"),
    27: Decimal("10.00"),
    28: Decimal("6.00"),
    29: Decimal("3.00"),
}

# Feriados nacionais padrão do Brasil (Mês, Dia)
DEFAULT_BRAZILIAN_HOLIDAYS: Set[Tuple[int, int]] = {
    (1, 1),    # Confraternização Universal
    (4, 21),   # Tiradentes
    (5, 1),    # Dia do Trabalho
    (9, 7),    # Independência do Brasil
    (10, 12),  # Nossa Senhora Aparecida
    (11, 2),   # Finados
    (11, 15),  # Proclamação da República
    (11, 20),  # Dia Nacional de Zumbi e da Consciência Negra
    (12, 25),  # Natal
}


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class CdbRdbError(Exception):
    """Exceção base para o subsistema de CDB / RDB do Balcão B3."""
    pass


class CdbValidationError(CdbRdbError):
    """Erro de validação cadastral ou violação de regras da Resolução CMN 5.036."""
    pass


class CdbCalculationError(CdbRdbError):
    """Erro no motor de precificação, curva de PU ou apuração tributária."""
    pass


class CdbSettlementError(CdbRdbError):
    """Erro no fluxo de liquidação, resgate antecipado ou batimento financeiro."""
    pass


class InalienableRdbError(CdbRdbError):
    """Lançada ao tentar alienar, ceder ou negociar no mercado secundário um RDB inalienável."""
    pass


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------

class BankingSecurityType(str, Enum):
    """Tipo de título bancário registrado no NoMe B3."""
    CDB = "CDB"  # Certificado de Depósito Bancário (transferível, admite mercado secundário)
    RDB = "RDB"  # Recibo de Depósito Bancário (intransferível e inalienável a terceiros)

    @classmethod
    def from_str(cls, value: Any) -> BankingSecurityType:
        if isinstance(value, cls):
            return value
        if not value:
            raise CdbValidationError("Tipo de título bancário não pode ser vazio.")
        normalized = str(value).strip().upper()
        if normalized in cls.__members__:
            return cls[normalized]
        valid = ", ".join(m.value for m in cls)
        raise CdbValidationError(f"Tipo de título bancário inválido: {value!r}. Permitidos: {valid}")


class CdbIndexerType(str, Enum):
    """Indexadores financeiros suportados para remuneração de CDB e RDB."""
    PRE = "PRE"                  # Taxa pré-fixada (% a.a., convenção DU/252)
    CDI_PERCENT = "CDI_PERCENT"  # Pós-fixado em percentual do CDI Over (ex: 105% do CDI)
    CDI_SPREAD = "CDI_SPREAD"    # CDI Over + Spread anual base 252 (ex: CDI + 1.50% a.a.)
    IPCA = "IPCA"                # IPCA + Juros Reais (% a.a., DU/252)

    @classmethod
    def from_str(cls, value: Any) -> CdbIndexerType:
        if isinstance(value, cls):
            return value
        if not value:
            raise CdbValidationError("Indexador financeiro não pode ser vazio.")
        normalized = str(value).strip().upper().replace(" ", "_")
        mapping = {
            "PRE": cls.PRE,
            "PREFIXADO": cls.PRE,
            "CDI_PERCENT": cls.CDI_PERCENT,
            "PERCENTUAL_CDI": cls.CDI_PERCENT,
            "CDI": cls.CDI_PERCENT,
            "DI": cls.CDI_PERCENT,
            "CDI_SPREAD": cls.CDI_SPREAD,
            "DI_SPREAD": cls.CDI_SPREAD,
            "IPCA": cls.IPCA,
            "IPCA_SPREAD": cls.IPCA,
        }
        if normalized in mapping:
            return mapping[normalized]
        valid = ", ".join(m.value for m in cls)
        raise CdbValidationError(f"Indexador financeiro inválido: {value!r}. Permitidos: {valid}")


class SettlementType(str, Enum):
    """Modalidade de liquidação ou resgate do título."""
    TOTAL_MATURITY = "TOTAL_MATURITY"  # Liquidação integral na data de vencimento
    EARLY_TOTAL = "EARLY_TOTAL"        # Resgate antecipado total
    EARLY_PARTIAL = "EARLY_PARTIAL"    # Resgate antecipado parcial


class ContractStatus(str, Enum):
    """Status operacional do contrato escriturado no NoMe."""
    ACTIVE = "ACTIVE"                        # Ativo com posição integral
    PARTIALLY_REDEEMED = "PARTIALLY_REDEEMED"  # Posição parcialmente resgatada
    SETTLED = "SETTLED"                      # Liquidado integralmente
    CANCELLED = "CANCELLED"                  # Cancelado


# ---------------------------------------------------------------------------
# Funções de Tributação Regressiva (IOF e IRRF)
# ---------------------------------------------------------------------------

def get_iof_regressive_rate(calendar_days: int) -> Decimal:
    """Retorna a alíquota de IOF conforme tabela regressiva do Decreto nº 6.306/2007.

    - Do 1º ao 29º dia corrido: alíquota de 96% a 3%
    - A partir do 30º dia corrido: 0% (isento)
    - Se calendar_days <= 1: 96%
    """
    if calendar_days < 1:
        return Decimal("96.00")
    if calendar_days >= 30:
        return Decimal("0.00")
    return IOF_REGRESSIVE_RATES.get(calendar_days, Decimal("0.00"))


def get_irrf_regressive_rate(calendar_days: int) -> Decimal:
    """Retorna a alíquota regressiva de IRRF conforme Lei nº 11.033/2004.

    - Até 180 dias: 22,5%
    - De 181 a 360 dias: 20,0%
    - De 361 a 720 dias: 17,5%
    - Acima de 720 dias: 15,0%
    """
    if calendar_days <= 180:
        return Decimal("22.50")
    if calendar_days <= 360:
        return Decimal("20.00")
    if calendar_days <= 720:
        return Decimal("17.50")
    return Decimal("15.00")


# ---------------------------------------------------------------------------
# Estruturas de Dados (Dataclasses)
# ---------------------------------------------------------------------------

@dataclass
class TaxCalculationBreakdown:
    """Memória de cálculo detalhada da tributação regressiva (IOF + IRRF)."""
    calendar_days: int
    gross_yield: Decimal
    iof_rate: Decimal
    iof_amount: Decimal
    irrf_base: Decimal
    irrf_rate: Decimal
    irrf_amount: Decimal
    total_tax: Decimal
    net_yield: Decimal

    def __post_init__(self) -> None:
        self.calendar_days = int(self.calendar_days)
        self.gross_yield = Decimal(str(self.gross_yield)).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        self.iof_rate = Decimal(str(self.iof_rate)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.iof_amount = Decimal(str(self.iof_amount)).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        self.irrf_base = Decimal(str(self.irrf_base)).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        self.irrf_rate = Decimal(str(self.irrf_rate)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.irrf_amount = Decimal(str(self.irrf_amount)).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        self.total_tax = Decimal(str(self.total_tax)).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        self.net_yield = Decimal(str(self.net_yield)).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

        # Invariante de conservação tributária
        expected_total_tax = (self.iof_amount + self.irrf_amount).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        if self.total_tax != expected_total_tax:
            raise CdbCalculationError(
                f"Total de impostos ({self.total_tax}) diverge da soma de IOF + IRRF ({expected_total_tax})"
            )

        expected_net_yield = (self.gross_yield - self.total_tax).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        if self.net_yield != expected_net_yield:
            raise CdbCalculationError(
                f"Rendimento líquido ({self.net_yield}) diverge de Bruto - Impostos ({expected_net_yield})"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "calendar_days": self.calendar_days,
            "gross_yield": str(self.gross_yield),
            "iof_rate": str(self.iof_rate),
            "iof_amount": str(self.iof_amount),
            "irrf_base": str(self.irrf_base),
            "irrf_rate": str(self.irrf_rate),
            "irrf_amount": str(self.irrf_amount),
            "total_tax": str(self.total_tax),
            "net_yield": str(self.net_yield),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaxCalculationBreakdown:
        return cls(
            calendar_days=int(data["calendar_days"]),
            gross_yield=Decimal(str(data["gross_yield"])),
            iof_rate=Decimal(str(data["iof_rate"])),
            iof_amount=Decimal(str(data["iof_amount"])),
            irrf_base=Decimal(str(data["irrf_base"])),
            irrf_rate=Decimal(str(data["irrf_rate"])),
            irrf_amount=Decimal(str(data["irrf_amount"])),
            total_tax=Decimal(str(data["total_tax"])),
            net_yield=Decimal(str(data["net_yield"])),
        )


@dataclass
class CdbSettlementReceipt:
    """Recibo formal de liquidação financeira (vencimento ou resgate antecipado)."""
    receipt_id: str
    contract_id: str
    settlement_type: SettlementType
    settlement_date: date
    calendar_days: int
    business_days: int
    redeemed_quantity: int
    remaining_quantity: int
    unit_price: Decimal
    principal_amount: Decimal
    gross_amount: Decimal
    tax_breakdown: TaxCalculationBreakdown
    net_amount: Decimal

    def __post_init__(self) -> None:
        self.receipt_id = str(self.receipt_id).strip()
        self.contract_id = str(self.contract_id).strip()
        if not isinstance(self.settlement_type, SettlementType):
            self.settlement_type = SettlementType(self.settlement_type)
        if isinstance(self.settlement_date, str):
            self.settlement_date = date.fromisoformat(self.settlement_date)

        self.calendar_days = int(self.calendar_days)
        self.business_days = int(self.business_days)
        self.redeemed_quantity = int(self.redeemed_quantity)
        self.remaining_quantity = int(self.remaining_quantity)

        self.unit_price = Decimal(str(self.unit_price)).quantize(
            DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP
        )
        self.principal_amount = Decimal(str(self.principal_amount)).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        self.gross_amount = Decimal(str(self.gross_amount)).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        self.net_amount = Decimal(str(self.net_amount)).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

        # Invariante Formal 1: Bruto == Principal + Rendimento Bruto
        expected_gross = (self.principal_amount + self.tax_breakdown.gross_yield).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        if self.gross_amount != expected_gross:
            raise CdbSettlementError(
                f"Violação de conservação: Bruto ({self.gross_amount}) != Principal + Rendimento ({expected_gross})"
            )

        # Invariante Formal 2: Liquido == Bruto - IOF - IRRF
        expected_net_from_gross = (
            self.gross_amount - self.tax_breakdown.iof_amount - self.tax_breakdown.irrf_amount
        ).quantize(DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP)
        if self.net_amount != expected_net_from_gross:
            raise CdbSettlementError(
                f"Violação de conservação: Liquido ({self.net_amount}) != Bruto - IOF - IRRF ({expected_net_from_gross})"
            )

        # Invariante Formal 3: Liquido == Principal + Rendimento Liquido
        expected_net_from_yield = (self.principal_amount + self.tax_breakdown.net_yield).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        if self.net_amount != expected_net_from_yield:
            raise CdbSettlementError(
                f"Violação de conservação: Liquido ({self.net_amount}) != Principal + RendimentoLiquido ({expected_net_from_yield})"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "contract_id": self.contract_id,
            "settlement_type": self.settlement_type.value,
            "settlement_date": self.settlement_date.isoformat(),
            "calendar_days": self.calendar_days,
            "business_days": self.business_days,
            "redeemed_quantity": self.redeemed_quantity,
            "remaining_quantity": self.remaining_quantity,
            "unit_price": str(self.unit_price),
            "principal_amount": str(self.principal_amount),
            "gross_amount": str(self.gross_amount),
            "tax_breakdown": self.tax_breakdown.to_dict(),
            "net_amount": str(self.net_amount),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CdbSettlementReceipt:
        return cls(
            receipt_id=data["receipt_id"],
            contract_id=data["contract_id"],
            settlement_type=SettlementType(data["settlement_type"]),
            settlement_date=date.fromisoformat(data["settlement_date"]),
            calendar_days=int(data["calendar_days"]),
            business_days=int(data["business_days"]),
            redeemed_quantity=int(data["redeemed_quantity"]),
            remaining_quantity=int(data["remaining_quantity"]),
            unit_price=Decimal(str(data["unit_price"])),
            principal_amount=Decimal(str(data["principal_amount"])),
            gross_amount=Decimal(str(data["gross_amount"])),
            tax_breakdown=TaxCalculationBreakdown.from_dict(data["tax_breakdown"]),
            net_amount=Decimal(str(data["net_amount"])),
        )


@dataclass
class CdbContract:
    """Contrato escritural de CDB ou RDB registrado no Balcão NoMe B3."""
    contract_id: str
    security_type: BankingSecurityType
    issuer_name: str
    issuer_ispb: str
    custody_account: str
    investor_id: str
    issue_date: date
    maturity_date: date
    initial_quantity: int
    current_quantity: int
    initial_unit_price: Decimal
    current_unit_price: Decimal
    indexer_type: CdbIndexerType
    annual_rate: Optional[Decimal] = None
    cdi_percent: Optional[Decimal] = None
    ipca_spread_annual: Optional[Decimal] = None
    semiannual_coupon: bool = False
    status: ContractStatus = ContractStatus.ACTIVE
    settlement_history: List[CdbSettlementReceipt] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.contract_id = str(self.contract_id).strip()
        if not self.contract_id:
            raise CdbValidationError("Identificador do contrato ('contract_id') não pode ser vazio.")

        if not isinstance(self.security_type, BankingSecurityType):
            self.security_type = BankingSecurityType.from_str(self.security_type)
        if not isinstance(self.indexer_type, CdbIndexerType):
            self.indexer_type = CdbIndexerType.from_str(self.indexer_type)
        if not isinstance(self.status, ContractStatus):
            self.status = ContractStatus(self.status)

        self.issuer_name = str(self.issuer_name).strip()
        self.issuer_ispb = str(self.issuer_ispb).strip()
        self.custody_account = str(self.custody_account).strip()
        self.investor_id = str(self.investor_id).strip()

        if len(self.issuer_ispb) != 8 or not self.issuer_ispb.isdigit():
            raise CdbValidationError(f"ISPB do emissor inválido: {self.issuer_ispb!r} (deve possuir 8 dígitos numéricos)")

        if isinstance(self.issue_date, str):
            self.issue_date = date.fromisoformat(self.issue_date)
        if isinstance(self.maturity_date, str):
            self.maturity_date = date.fromisoformat(self.maturity_date)

        if self.maturity_date <= self.issue_date:
            raise CdbValidationError(
                f"Data de vencimento ({self.maturity_date}) deve ser posterior à data de emissão ({self.issue_date})"
            )

        self.initial_quantity = int(self.initial_quantity)
        self.current_quantity = int(self.current_quantity)
        if self.initial_quantity <= 0:
            raise CdbValidationError(f"Quantidade inicial deve ser estritamente positiva: {self.initial_quantity}")
        if self.current_quantity < 0:
            raise CdbValidationError(f"Quantidade atual não pode ser negativa: {self.current_quantity}")
        if self.current_quantity > self.initial_quantity:
            raise CdbValidationError(
                f"Quantidade atual ({self.current_quantity}) excede a quantidade inicial ({self.initial_quantity})"
            )

        self.initial_unit_price = Decimal(str(self.initial_unit_price)).quantize(
            DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP
        )
        self.current_unit_price = Decimal(str(self.current_unit_price)).quantize(
            DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP
        )

        if self.initial_unit_price <= Decimal("0"):
            raise CdbValidationError(f"Preço Unitário inicial deve ser positivo: {self.initial_unit_price}")
        if self.current_unit_price <= Decimal("0"):
            raise CdbValidationError(f"Preço Unitário atual deve ser positivo: {self.current_unit_price}")

        # Validações dos parâmetros de remuneração conforme indexador
        if self.indexer_type == CdbIndexerType.PRE:
            if self.annual_rate is None or Decimal(str(self.annual_rate)) <= Decimal("0"):
                raise CdbValidationError("Contrato pré-fixado requer taxa anual positiva ('annual_rate').")
            self.annual_rate = Decimal(str(self.annual_rate))
        elif self.indexer_type == CdbIndexerType.CDI_PERCENT:
            if self.cdi_percent is None or Decimal(str(self.cdi_percent)) <= Decimal("0"):
                raise CdbValidationError("Contrato pós-fixado %CDI requer percentual positivo ('cdi_percent').")
            self.cdi_percent = Decimal(str(self.cdi_percent))
        elif self.indexer_type == CdbIndexerType.CDI_SPREAD:
            if self.annual_rate is None or Decimal(str(self.annual_rate)) < Decimal("0"):
                raise CdbValidationError("Contrato CDI + Spread requer spread anual não-negativo ('annual_rate').")
            self.annual_rate = Decimal(str(self.annual_rate))
            if self.cdi_percent is None:
                self.cdi_percent = Decimal("100.00")
            else:
                self.cdi_percent = Decimal(str(self.cdi_percent))
        elif self.indexer_type == CdbIndexerType.IPCA:
            if self.ipca_spread_annual is None:
                self.ipca_spread_annual = Decimal("0.00")
            else:
                self.ipca_spread_annual = Decimal(str(self.ipca_spread_annual))

    @property
    def is_alienable(self) -> bool:
        """Indica se o título admite alienação e negociação no mercado secundário (CDB sim, RDB não)."""
        return self.security_type == BankingSecurityType.CDB

    def principal_amount(self, quantity: Optional[int] = None) -> Decimal:
        """Calcula o valor do principal original para uma quantidade de títulos."""
        q = self.current_quantity if quantity is None else int(quantity)
        return (Decimal(q) * self.initial_unit_price).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

    def gross_financial_value(self, unit_price: Optional[Decimal] = None, quantity: Optional[int] = None) -> Decimal:
        """Calcula o montante financeiro bruto total para o PU e quantidade indicados."""
        q = self.current_quantity if quantity is None else int(quantity)
        pu = self.current_unit_price if unit_price is None else unit_price
        return (Decimal(q) * pu).quantize(DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP)

    def gross_yield(self, unit_price: Optional[Decimal] = None, quantity: Optional[int] = None) -> Decimal:
        """Calcula o rendimento bruto acumulado (Valor Bruto - Principal)."""
        gross = self.gross_financial_value(unit_price=unit_price, quantity=quantity)
        principal = self.principal_amount(quantity=quantity)
        return (gross - principal).quantize(DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "security_type": self.security_type.value,
            "issuer_name": self.issuer_name,
            "issuer_ispb": self.issuer_ispb,
            "custody_account": self.custody_account,
            "investor_id": self.investor_id,
            "issue_date": self.issue_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "initial_quantity": self.initial_quantity,
            "current_quantity": self.current_quantity,
            "initial_unit_price": str(self.initial_unit_price),
            "current_unit_price": str(self.current_unit_price),
            "indexer_type": self.indexer_type.value,
            "annual_rate": str(self.annual_rate) if self.annual_rate is not None else None,
            "cdi_percent": str(self.cdi_percent) if self.cdi_percent is not None else None,
            "ipca_spread_annual": str(self.ipca_spread_annual) if self.ipca_spread_annual is not None else None,
            "semiannual_coupon": self.semiannual_coupon,
            "status": self.status.value,
            "settlement_history": [r.to_dict() for r in self.settlement_history],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CdbContract:
        history = [
            CdbSettlementReceipt.from_dict(r) for r in data.get("settlement_history", [])
        ]
        return cls(
            contract_id=data["contract_id"],
            security_type=BankingSecurityType.from_str(data["security_type"]),
            issuer_name=data["issuer_name"],
            issuer_ispb=data["issuer_ispb"],
            custody_account=data["custody_account"],
            investor_id=data["investor_id"],
            issue_date=date.fromisoformat(data["issue_date"]),
            maturity_date=date.fromisoformat(data["maturity_date"]),
            initial_quantity=int(data["initial_quantity"]),
            current_quantity=int(data["current_quantity"]),
            initial_unit_price=Decimal(str(data["initial_unit_price"])),
            current_unit_price=Decimal(str(data["current_unit_price"])),
            indexer_type=CdbIndexerType.from_str(data["indexer_type"]),
            annual_rate=Decimal(str(data["annual_rate"])) if data.get("annual_rate") is not None else None,
            cdi_percent=Decimal(str(data["cdi_percent"])) if data.get("cdi_percent") is not None else None,
            ipca_spread_annual=Decimal(str(data["ipca_spread_annual"])) if data.get("ipca_spread_annual") is not None else None,
            semiannual_coupon=bool(data.get("semiannual_coupon", False)),
            status=ContractStatus(data.get("status", ContractStatus.ACTIVE.value)),
            settlement_history=history,
        )


# ---------------------------------------------------------------------------
# Motor de Registro e Liquidação de Balcão B3 (CdbRdbRegistryEngine)
# ---------------------------------------------------------------------------

@dataclass
class CdbRdbRegistryEngine:
    """Motor de escrituração, precificação de PU na curva e liquidação de CDB/RDB."""

    contracts: Dict[str, CdbContract] = field(default_factory=dict)
    di_rates: Dict[date, Decimal] = field(default_factory=dict)
    ipca_indices: Dict[date, Decimal] = field(default_factory=dict)
    settlement_receipts: List[CdbSettlementReceipt] = field(default_factory=list)
    national_holidays: Set[Tuple[int, int]] = field(
        default_factory=lambda: set(DEFAULT_BRAZILIAN_HOLIDAYS)
    )
    _receipt_seq: int = 1

    # -----------------------------------------------------------------------
    # Calendário e Contagem de Dias Úteis (Convenção B3 / Anbima)
    # -----------------------------------------------------------------------

    def is_business_day(self, d: date) -> bool:
        """Verifica se a data é dia útil de negociação bancária e de balcão B3."""
        if d.weekday() >= 5:  # Sábado (5) ou Domingo (6)
            return False
        if (d.month, d.day) in self.national_holidays:
            return False
        return True

    def count_business_days(self, start_date: date, end_date: date) -> int:
        """Conta a quantidade de dias úteis entre start_date e end_date (convenção B3: exclusivo start, inclusivo end)."""
        if end_date < start_date:
            raise CdbValidationError(f"Data final ({end_date}) anterior à data inicial ({start_date})")
        if start_date == end_date:
            return 0

        curr = start_date + timedelta(days=1)
        du = 0
        while curr <= end_date:
            if self.is_business_day(curr):
                du += 1
            curr += timedelta(days=1)
        return du

    # -----------------------------------------------------------------------
    # Gestão de Dados de Mercado (DI Over e IPCA)
    # -----------------------------------------------------------------------

    def register_di_rate(self, dt: Union[date, str], annual_rate: Union[Decimal, str, float]) -> None:
        """Registra a Taxa DI Over anual divulgada pela B3 para uma data útil."""
        d = date.fromisoformat(dt) if isinstance(dt, str) else dt
        rate_d = Decimal(str(annual_rate)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        self.di_rates[d] = rate_d

    def register_di_rates_batch(self, rates: Dict[Union[date, str], Union[Decimal, str, float]]) -> None:
        """Registra lote de taxas DI Over."""
        for dt, rate in rates.items():
            self.register_di_rate(dt, rate)

    def register_ipca_index(self, dt: Union[date, str], index_value: Union[Decimal, str, float]) -> None:
        """Registra o índice de inflação IPCA divulgado para a data."""
        d = date.fromisoformat(dt) if isinstance(dt, str) else dt
        val_d = Decimal(str(index_value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        self.ipca_indices[d] = val_d

    # -----------------------------------------------------------------------
    # Registro de Contratos
    # -----------------------------------------------------------------------

    def register_contract(self, contract: CdbContract) -> CdbContract:
        """Registra um contrato de CDB ou RDB no livro de escrituração NoMe."""
        if contract.contract_id in self.contracts:
            raise CdbValidationError(f"Contrato já cadastrado no NoMe: {contract.contract_id}")
        self.contracts[contract.contract_id] = contract
        return contract

    def get_contract(self, contract_id: str) -> CdbContract:
        """Recupera um contrato pelo seu identificador único."""
        if contract_id not in self.contracts:
            raise CdbValidationError(f"Contrato não encontrado no NoMe: {contract_id}")
        return self.contracts[contract_id]

    # -----------------------------------------------------------------------
    # Precificação na Curva e PU Diário
    # -----------------------------------------------------------------------

    def calculate_pu(
        self,
        contract: CdbContract,
        target_date: date,
        projected_di_rate: Optional[Union[Decimal, str, float]] = None,
    ) -> Decimal:
        """Calcula o Preço Unitário (PU) do contrato para a data de avaliação target_date.

        Convenções B3:
        - PRE: PU = PU0 * (1 + taxa_aa/100) ^ (DU / 252)
        - CDI_PERCENT: PU = PU0 * Prod(FDI_k ponderado pelo %CDI)
        - CDI_SPREAD: PU = PU0 * Prod(FDI_k) * (1 + spread_aa/100) ^ (DU / 252)
        - IPCA: PU = PU0 * (IPCA_t / IPCA_0) * (1 + spread_aa/100) ^ (DU / 252)
        """
        if target_date < contract.issue_date:
            raise CdbCalculationError(
                f"Data alvo ({target_date}) não pode ser anterior à emissão ({contract.issue_date})"
            )
        if target_date == contract.issue_date:
            return contract.initial_unit_price

        du = self.count_business_days(contract.issue_date, target_date)
        pu0 = contract.initial_unit_price

        if contract.indexer_type == CdbIndexerType.PRE:
            taxa_aa = float(contract.annual_rate or Decimal("0.00")) / 100.0
            exp = float(du) / float(DAYS_PER_YEAR_WORKDAYS)
            fator_pre = pow(1.0 + taxa_aa, exp)
            fator_pre_dec = Decimal(f"{fator_pre:.16f}")
            pu = (pu0 * fator_pre_dec).quantize(DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP)
            return pu

        elif contract.indexer_type == CdbIndexerType.CDI_PERCENT:
            p_cdi = contract.cdi_percent or Decimal("100.00")
            fator_acum = self._accumulate_cdi_factor(
                contract.issue_date, target_date, p_cdi, projected_di_rate
            )
            pu = (pu0 * fator_acum).quantize(DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP)
            return pu

        elif contract.indexer_type == CdbIndexerType.CDI_SPREAD:
            p_cdi = contract.cdi_percent or Decimal("100.00")
            fator_cdi = self._accumulate_cdi_factor(
                contract.issue_date, target_date, p_cdi, projected_di_rate
            )
            spread_aa = float(contract.annual_rate or Decimal("0.00")) / 100.0
            exp = float(du) / float(DAYS_PER_YEAR_WORKDAYS)
            fator_spread = pow(1.0 + spread_aa, exp)
            fator_spread_dec = Decimal(f"{fator_spread:.8f}").quantize(
                DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP
            )
            fator_total = (fator_cdi * fator_spread_dec).quantize(
                DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP
            )
            pu = (pu0 * fator_total).quantize(DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP)
            return pu

        elif contract.indexer_type == CdbIndexerType.IPCA:
            # Variação do índice IPCA se disponível
            ipca_0 = self.ipca_indices.get(contract.issue_date)
            ipca_t = self.ipca_indices.get(target_date)

            if ipca_0 is not None and ipca_t is not None and ipca_0 > Decimal("0"):
                fator_ipca = (ipca_t / ipca_0).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
            else:
                fator_ipca = Decimal("1.00000000")

            spread_aa = float(contract.ipca_spread_annual or Decimal("0.00")) / 100.0
            exp = float(du) / float(DAYS_PER_YEAR_WORKDAYS)
            fator_juros = pow(1.0 + spread_aa, exp)
            fator_juros_dec = Decimal(f"{fator_juros:.8f}").quantize(
                DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP
            )

            fator_total = (fator_ipca * fator_juros_dec).quantize(
                DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP
            )
            pu = (pu0 * fator_total).quantize(DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP)
            return pu

        raise CdbCalculationError(f"Indexador não suportado: {contract.indexer_type}")

    def _accumulate_cdi_factor(
        self,
        start_date: date,
        end_date: date,
        cdi_percent: Decimal,
        projected_di_rate: Optional[Union[Decimal, str, float]] = None,
    ) -> Decimal:
        """Acumula os fatores diários de CDI ponderados pelo percentual do CDI entre as duas datas."""
        curr = start_date + timedelta(days=1)
        fator_acum = Decimal("1.0000000000000000")

        while curr <= end_date:
            if self.is_business_day(curr):
                # Busca taxa DI over do dia útil
                di_rate = self.di_rates.get(curr)
                if di_rate is None:
                    if projected_di_rate is not None:
                        di_rate = Decimal(str(projected_di_rate))
                    else:
                        raise CdbCalculationError(
                            f"Taxa DI Over não encontrada para o dia útil {curr.isoformat()} e sem taxa projetada."
                        )

                # 1. Fator diário do CDI anualizado (8 casas)
                fdi_float = pow(1.0 + float(di_rate) / 100.0, 1.0 / 252.0)
                fdi = Decimal(f"{fdi_float:.8f}")

                # 2. Fator diário remunerado ponderado pelo % CDI (8 casas)
                taxa_diaria = fdi - Decimal("1")
                fator_k = (Decimal("1") + (taxa_diaria * cdi_percent / Decimal("100"))).quantize(
                    DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP
                )
                fator_acum = fator_acum * fator_k
            curr += timedelta(days=1)

        return fator_acum.quantize(DECIMAL_FACTOR_PRECISION, rounding=ROUND_HALF_UP)

    def update_daily_pu(
        self, target_date: date, projected_di_rate: Optional[Union[Decimal, str, float]] = None
    ) -> Dict[str, Decimal]:
        """Atualiza o Preço Unitário de todos os contratos ativos no livro escritural para target_date."""
        updated: Dict[str, Decimal] = {}
        for contract_id, contract in self.contracts.items():
            if contract.status in (ContractStatus.ACTIVE, ContractStatus.PARTIALLY_REDEEMED):
                pu = self.calculate_pu(contract, target_date, projected_di_rate=projected_di_rate)
                contract.current_unit_price = pu
                updated[contract_id] = pu
        return updated

    # -----------------------------------------------------------------------
    # Apuração de Tributos Regressivos (IOF + IRRF)
    # -----------------------------------------------------------------------

    def calculate_taxes(self, gross_yield: Decimal, calendar_days: int) -> TaxCalculationBreakdown:
        """Calcula o IOF e o IRRF regressivo com exata aderência à legislação e conservação formal."""
        if calendar_days < 0:
            raise CdbCalculationError(f"Dias corridos não podem ser negativos: {calendar_days}")

        gross_yield = gross_yield.quantize(DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP)

        if gross_yield <= Decimal("0.00"):
            return TaxCalculationBreakdown(
                calendar_days=calendar_days,
                gross_yield=gross_yield,
                iof_rate=Decimal("0.00"),
                iof_amount=Decimal("0.00"),
                irrf_base=Decimal("0.00"),
                irrf_rate=Decimal("0.00"),
                irrf_amount=Decimal("0.00"),
                total_tax=Decimal("0.00"),
                net_yield=gross_yield,
            )

        # IOF regressivo
        iof_rate = get_iof_regressive_rate(calendar_days)
        iof_amount = (gross_yield * (iof_rate / Decimal("100"))).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

        # Base de cálculo do IRRF = Rendimento Bruto - IOF
        irrf_base = (gross_yield - iof_amount).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

        # IRRF regressivo
        irrf_rate = get_irrf_regressive_rate(calendar_days)
        irrf_amount = (irrf_base * (irrf_rate / Decimal("100"))).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

        total_tax = (iof_amount + irrf_amount).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        net_yield = (gross_yield - total_tax).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

        return TaxCalculationBreakdown(
            calendar_days=calendar_days,
            gross_yield=gross_yield,
            iof_rate=iof_rate,
            iof_amount=iof_amount,
            irrf_base=irrf_base,
            irrf_rate=irrf_rate,
            irrf_amount=irrf_amount,
            total_tax=total_tax,
            net_yield=net_yield,
        )

    # -----------------------------------------------------------------------
    # Liquidação de Vencimento e Resgate Antecipado
    # -----------------------------------------------------------------------

    def settle_contract(
        self,
        contract_id: str,
        settlement_date: Union[date, str],
        redeemed_quantity: Optional[int] = None,
        projected_di_rate: Optional[Union[Decimal, str, float]] = None,
        override_unit_price: Optional[Union[Decimal, str, float]] = None,
    ) -> CdbSettlementReceipt:
        """Executa a liquidação financeira de vencimento ou resgate antecipado total/parcial.

        Validações:
        - Contrato deve estar ativo ou parcialmente resgatado.
        - Quantidade a resgatar deve ser 1 <= Q <= Q_atual.
        - Apura PU da data, montante bruto, principal proporcional, IOF e IRRF regressivos.
        - Atualiza o livro escritural do contrato.
        - Emite CdbSettlementReceipt garantindo todas as leis de conservação.
        """
        contract = self.get_contract(contract_id)
        s_date = date.fromisoformat(settlement_date) if isinstance(settlement_date, str) else settlement_date

        if contract.status not in (ContractStatus.ACTIVE, ContractStatus.PARTIALLY_REDEEMED):
            raise CdbSettlementError(
                f"Contrato {contract_id} não pode ser liquidado com status: {contract.status.value}"
            )

        if s_date < contract.issue_date:
            raise CdbSettlementError(
                f"Data de liquidação ({s_date}) não pode ser anterior à data de emissão ({contract.issue_date})"
            )

        qty_to_redeem = contract.current_quantity if redeemed_quantity is None else int(redeemed_quantity)
        if qty_to_redeem <= 0:
            raise CdbSettlementError(f"Quantidade a resgatar deve ser estritamente positiva: {qty_to_redeem}")
        if qty_to_redeem > contract.current_quantity:
            raise CdbSettlementError(
                f"Quantidade solicitada ({qty_to_redeem}) excede o saldo remanescente disponível ({contract.current_quantity})"
            )

        # Determina modalidade de liquidação
        is_total = (qty_to_redeem == contract.current_quantity)
        if is_total:
            if s_date >= contract.maturity_date:
                settlement_type = SettlementType.TOTAL_MATURITY
            else:
                settlement_type = SettlementType.EARLY_TOTAL
        else:
            settlement_type = SettlementType.EARLY_PARTIAL

        # Prazos
        calendar_days = (s_date - contract.issue_date).days
        business_days = self.count_business_days(contract.issue_date, s_date)

        # Preço Unitário na data de liquidação
        if override_unit_price is not None:
            pu = Decimal(str(override_unit_price)).quantize(
                DECIMAL_PU_PRECISION, rounding=ROUND_HALF_UP
            )
        else:
            pu = self.calculate_pu(contract, s_date, projected_di_rate=projected_di_rate)

        # Montantes Financeiros
        principal_amount = (Decimal(qty_to_redeem) * contract.initial_unit_price).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        gross_amount = (Decimal(qty_to_redeem) * pu).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        gross_yield = (gross_amount - principal_amount).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

        # Apuração Tributária Regressiva
        tax_breakdown = self.calculate_taxes(gross_yield, calendar_days)

        # Valor Líquido = Bruto - Tributos
        net_amount = (gross_amount - tax_breakdown.total_tax).quantize(
            DECIMAL_MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

        # Atualização Escritural do Contrato
        remaining_qty = contract.current_quantity - qty_to_redeem
        contract.current_quantity = remaining_qty
        contract.current_unit_price = pu

        if remaining_qty == 0:
            contract.status = ContractStatus.SETTLED
        else:
            contract.status = ContractStatus.PARTIALLY_REDEEMED

        # Emissão do Recibo
        receipt_id = f"REC-{contract.security_type.value}-{s_date.strftime('%Y%m%d')}-{self._receipt_seq:04d}"
        self._receipt_seq += 1

        receipt = CdbSettlementReceipt(
            receipt_id=receipt_id,
            contract_id=contract.contract_id,
            settlement_type=settlement_type,
            settlement_date=s_date,
            calendar_days=calendar_days,
            business_days=business_days,
            redeemed_quantity=qty_to_redeem,
            remaining_quantity=remaining_qty,
            unit_price=pu,
            principal_amount=principal_amount,
            gross_amount=gross_amount,
            tax_breakdown=tax_breakdown,
            net_amount=net_amount,
        )

        contract.settlement_history.append(receipt)
        self.settlement_receipts.append(receipt)
        return receipt

    def validate_rdb_transfer_attempt(self, contract_id: str, new_holder_id: str) -> None:
        """Verifica a regra de inalienabilidade e intransferibilidade do RDB conforme Resolução CMN 5.036."""
        contract = self.get_contract(contract_id)
        if contract.security_type == BankingSecurityType.RDB:
            if contract.investor_id != new_holder_id:
                raise InalienableRdbError(
                    f"RDB {contract_id} é inalienável e intransferível a terceiros. "
                    f"Titular original: {contract.investor_id}, Tentativa de cessão para: {new_holder_id}."
                )
