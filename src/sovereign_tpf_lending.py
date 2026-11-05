"""Módulo de Empréstimo e Operações Compromissadas com Títulos Públicos Federais (FL-076).

Implementa o motor determinístico de registro, custódia, empréstimo de ativos (BTC Soberano),
operações compromissadas bilaterais (Open Market BACEN / SELIC / Balcão B3), repasse integral
de cupons semestrais e cálculo de margem de garantia multilateral da Câmara B3 para Títulos
Públicos Federais (TPF):
- LTN: Letra do Tesouro Nacional (Prefixado puro, sem cupons).
- LFT: Letra Financeira do Tesouro (Pós-fixado indexado à Taxa SELIC).
- NTN-B: Nota do Tesouro Nacional Série B (Indexado ao IPCA com juros semestrais de 6% a.a.).
- NTN-F: Nota do Tesouro Nacional Série F (Prefixado com juros semestrais de 10% a.a.).

Regras de Negócio e Leis Invariantes de Conservação:
1. Lei de Conservação de Custódia Soberana (LAW-059):
   `Custódia_Total == Disponível + Emprestada + Bloqueada_Garantia`
   Validade estrita em todas as contas e posições de custódia depositária SELIC / B3.
2. Lei de Paridade de Recompra em Operações Compromissadas (LAW-060):
   `Preço_Recompra == Preço_Venda * (1 + Taxa_Repo * DU / 252)`
   Convenção do mercado monetário e Open Market BACEN centavo a centavo.
3. Colateralização Mínima de Margem Multilateral da Câmara B3:
   Todo empréstimo de TPF ativo deve possuir margem de garantia multilateral alocada
   com colateralização mínima de 100% do valor de mercado (PU_mercado * Quantidade).
4. Repasse Integral de Cupons Semestrais (NTN-B e NTN-F):
   O tomador do empréstimo de TPF deve repassar 100% do valor do cupom ao doador original
   nas datas de pagamento semestrais estipuladas pelo Tesouro Nacional.
5. Ciclos de Liquidação D0 e D+1:
   Suporte a liquidação física e financeira em D0 (intraday) e D+1 para empréstimos e repos.

100% Python Standard Library (sem dependências externas).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

try:
    from src.invariants import (
        assert_sovereign_custody_conservation,
        assert_sovereign_repo_repurchase_parity,
    )
except ImportError:
    try:
        from invariants import (  # type: ignore
            assert_sovereign_custody_conservation,
            assert_sovereign_repo_repurchase_parity,
        )
    except ImportError:
        def assert_sovereign_custody_conservation(*args: Any, **kwargs: Any) -> None:
            pass

        def assert_sovereign_repo_repurchase_parity(*args: Any, **kwargs: Any) -> None:
            pass


# =====================================================================
# CONSTANTES DE PRECISÃO E CALENDÁRIO B3
# =====================================================================

QUANTIZE_CENTS = Decimal("0.01")
QUANTIZE_PU = Decimal("0.000001")
QUANTIZE_RATE = Decimal("0.00000001")
QUANTIZE_QTY = Decimal("0.0001")

DAYS_PER_YEAR_WORKDAYS = Decimal("252")
MIN_COLLATERAL_RATIO_DEFAULT = Decimal("1.0000")  # 100% de margem mínima B3

DEFAULT_BRAZILIAN_HOLIDAYS: Set[Tuple[int, int]] = {
    (1, 1),    # Confraternização Universal
    (4, 21),   # Tiradentes
    (5, 1),    # Dia do Trabalho
    (9, 7),    # Independência do Brasil
    (10, 12),  # Nossa Senhora Aparecida
    (11, 2),   # Finados
    (11, 15),  # Proclamação da República
    (11, 20),  # Dia da Consciência Negra
    (12, 25),  # Natal
}


def round_cents(value: Decimal) -> Decimal:
    """Arredonda valor financeiro monetário para 2 casas decimais (centavos BRL)."""
    return value.quantize(QUANTIZE_CENTS, rounding=ROUND_HALF_UP)


def round_pu(value: Decimal) -> Decimal:
    """Arredonda Preço Unitário (PU) para 6 casas decimais (padrão Tesouro/B3)."""
    return value.quantize(QUANTIZE_PU, rounding=ROUND_HALF_UP)


def _to_decimal(val: Any, field_name: str = "") -> Decimal:
    """Converte valor para Decimal de forma determinística e segura."""
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val))
    except (InvalidOperation, TypeError, ValueError) as err:
        raise SovereignTpfValidationError(
            f"Valor numérico inválido no campo '{field_name}': {val!r}"
        ) from err


def _to_date(val: Any, field_name: str = "") -> date:
    """Converte representação ISO ou date para date."""
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        try:
            return date.fromisoformat(val.strip())
        except ValueError as err:
            raise SovereignTpfValidationError(
                f"Formato de data inválido no campo '{field_name}' ('{val}'). Use AAAA-MM-DD."
            ) from err
    raise SovereignTpfValidationError(
        f"Tipo inválido para data no campo '{field_name}': {type(val)}"
    )


def is_business_day(d: date, holidays: Optional[Set[Tuple[int, int]]] = None) -> bool:
    """Verifica se uma data é dia útil bancário e de balcão B3/SELIC."""
    if d.weekday() >= 5:  # Sábado (5) ou Domingo (6)
        return False
    h = holidays if holidays is not None else DEFAULT_BRAZILIAN_HOLIDAYS
    return (d.month, d.day) not in h


def count_business_days(
    start_date: date,
    end_date: date,
    holidays: Optional[Set[Tuple[int, int]]] = None,
) -> int:
    """Conta dias úteis (DU) entre start_date e end_date (exclusivo start, inclusivo end)."""
    if end_date < start_date:
        raise SovereignTpfValidationError(
            f"Data final ({end_date}) anterior à data inicial ({start_date})."
        )
    if start_date == end_date:
        return 0
    curr = start_date + timedelta(days=1)
    du = 0
    while curr <= end_date:
        if is_business_day(curr, holidays):
            du += 1
        curr += timedelta(days=1)
    return du


# =====================================================================
# EXCEÇÕES DE DOMÍNIO
# =====================================================================

class SovereignTpfError(Exception):
    """Exceção base para operações com Títulos Públicos Federais (FL-076)."""
    pass


class SovereignTpfValidationError(SovereignTpfError):
    """Erro de validação cadastral ou integridade de parâmetros."""
    pass


class SovereignCustodyConservationError(SovereignTpfError):
    """Violação da Lei de Conservação de Custódia Soberana (LAW-059)."""
    pass


class SovereignCollateralDeficitError(SovereignTpfError):
    """Subcolateralização em garantia multilateral B3 (mínimo 100% do PU de mercado)."""
    pass


class SovereignRepoParityError(SovereignTpfError):
    """Violação da Lei de Paridade de Recompra em Operação Compromissada (LAW-060)."""
    pass


class SovereignCouponForwardError(SovereignTpfError):
    """Erro no processamento ou repasse integral de cupons semestrais de NTN-B/NTN-F."""
    pass


class SovereignStateTransitionError(SovereignTpfError):
    """Transição de estado inválida em contrato de empréstimo ou compromissada."""
    pass


# =====================================================================
# ENUMERAÇÕES
# =====================================================================

class TPFBondType(str, Enum):
    """Tipologia canônica de Títulos Públicos Federais (TPF)."""
    LTN = "LTN"        # Letra do Tesouro Nacional (Prefixado puro, sem juros semestrais)
    LFT = "LFT"        # Letra Financeira do Tesouro (Pós-fixado indexado à Taxa SELIC)
    NTN_B = "NTN-B"    # Nota do Tesouro Nacional Série B (IPCA + Cupom semestral 6% a.a.)
    NTN_F = "NTN-F"    # Nota do Tesouro Nacional Série F (Prefixado + Cupom semestral 10% a.a.)

    @property
    def has_semiannual_coupon(self) -> bool:
        """Indica se o título público paga cupom semestral de juros."""
        return self in (TPFBondType.NTN_B, TPFBondType.NTN_F)

    @property
    def is_prefixed(self) -> bool:
        """Indica se o título possui remuneração pré-fixada (LTN ou NTN-F)."""
        return self in (TPFBondType.LTN, TPFBondType.NTN_F)

    @property
    def is_selic_postfixed(self) -> bool:
        """Indica se o título é pós-fixado SELIC (LFT)."""
        return self == TPFBondType.LFT

    @property
    def is_inflation_linked(self) -> bool:
        """Indica se o título é indexado à inflação IPCA (NTN-B)."""
        return self == TPFBondType.NTN_B

    @classmethod
    def from_str(cls, value: Any) -> TPFBondType:
        """Converte strings e variações para TPFBondType."""
        if isinstance(value, cls):
            return value
        if not value:
            raise SovereignTpfValidationError("Tipo de TPF não pode ser vazio.")
        s = str(value).strip().upper().replace(" ", "").replace("_", "-")
        mapping = {
            "LTN": cls.LTN,
            "LFT": cls.LFT,
            "NTN-B": cls.NTN_B,
            "NTNB": cls.NTN_B,
            "NTN-F": cls.NTN_F,
            "NTNF": cls.NTN_F,
        }
        if s in mapping:
            return mapping[s]
        valid = ", ".join(m.value for m in cls)
        raise SovereignTpfValidationError(
            f"Tipo de TPF inválido: {value!r}. Valores suportados: {valid}"
        )


class SettlementCycle(str, Enum):
    """Ciclo de liquidação física e financeira no Balcão B3 e SELIC."""
    D0 = "D0"    # Liquidação no mesmo dia da contratação (Same Day / Intraday)
    D1 = "D+1"   # Liquidação no dia útil subsequente

    @classmethod
    def from_str(cls, value: Any) -> SettlementCycle:
        if isinstance(value, cls):
            return value
        s = str(value).strip().upper()
        if s in ("D0", "0", "SAMEDAY", "SAME_DAY"):
            return cls.D0
        if s in ("D+1", "D1", "1", "NEXT_DAY"):
            return cls.D1
        raise SovereignTpfValidationError(f"Ciclo de liquidação inválido: {value!r}. Use D0 ou D+1.")


class LendingRateType(str, Enum):
    """Modalidade da taxa de remuneração do empréstimo de TPF no Balcão B3."""
    PASSIVE = "PASSIVE"  # Taxa passiva ofertada pelo doador no book
    ACTIVE = "ACTIVE"    # Taxa ativa tomada pelo tomador agressor

    @classmethod
    def from_str(cls, value: Any) -> LendingRateType:
        if isinstance(value, cls):
            return value
        s = str(value).strip().upper()
        if s in ("PASSIVE", "PASSIVA", "DOADOR", "RESTING"):
            return cls.PASSIVE
        if s in ("ACTIVE", "ATIVA", "TOMADOR", "AGGRESSIVE"):
            return cls.ACTIVE
        raise SovereignTpfValidationError(f"Tipo de taxa de aluguel inválido: {value!r}. Use PASSIVE ou ACTIVE.")


class LoanStatus(str, Enum):
    """Ciclo de vida do contrato de empréstimo de TPF (BTC Soberano)."""
    REGISTERED = "REGISTERED"          # Registrado, aguardando liquidação e aporte de garantia
    ACTIVE = "ACTIVE"                  # Em vigor, garantia alocada e custódia transferida
    RETURNED = "RETURNED"              # Liquidado com devolução integral e taxa de aluguel paga
    DEFAULTED = "DEFAULTED"            # Inadimplente (falha na devolução ou proventos)
    EARLY_TERMINATED = "EARLY_TERMINATED"  # Liquidado antecipadamente por acordo bilateral


class RepoStatus(str, Enum):
    """Ciclo de vida de Operação Compromissada Bilateral (Repo)."""
    REGISTERED = "REGISTERED"          # Registrada bilateralmente
    SETTLED_LEG1 = "SETTLED_LEG1"      # Perna de ida liquidada (venda à vista realizada)
    SETTLED_LEG2 = "SETTLED_LEG2"      # Perna de volta liquidada (recompra a termo realizada)
    DEFAULTED = "DEFAULTED"            # Inadimplência na perna de volta
    CANCELLED = "CANCELLED"            # Cancelada antes da perna de ida


class CollateralAssetType(str, Enum):
    """Tipo de ativo aportado em garantia multilateral na Câmara B3."""
    CASH = "CASH"                      # Moeda corrente nacional (BRL)
    SOVEREIGN_BOND = "SOVEREIGN_BOND"  # Outro TPF depositado como margem


# =====================================================================
# MODELOS DE DOMÍNIO (DATACLASSES)
# =====================================================================

@dataclass
class SovereignBond:
    """Título Público Federal emitido pela Secretaria do Tesouro Nacional (STN)."""
    bond_id: str
    bond_type: TPFBondType
    isin: str
    issue_date: date
    maturity_date: date
    market_unit_price: Decimal
    annual_coupon_rate: Decimal = Decimal("0.0000")
    description: str = ""

    def __post_init__(self) -> None:
        self.bond_id = str(self.bond_id).strip()
        if not self.bond_id:
            raise SovereignTpfValidationError("Identificador do TPF (bond_id) não pode ser vazio.")
        self.bond_type = TPFBondType.from_str(self.bond_type)
        self.isin = str(self.isin).strip().upper()
        self.issue_date = _to_date(self.issue_date, "issue_date")
        self.maturity_date = _to_date(self.maturity_date, "maturity_date")
        if self.maturity_date <= self.issue_date:
            raise SovereignTpfValidationError(
                f"Data de vencimento ({self.maturity_date}) deve ser posterior à emissão ({self.issue_date})."
            )
        self.market_unit_price = round_pu(_to_decimal(self.market_unit_price, "market_unit_price"))
        if self.market_unit_price <= Decimal("0.00"):
            raise SovereignTpfValidationError(
                f"PU de mercado deve ser estritamente positivo: {self.market_unit_price}"
            )
        self.annual_coupon_rate = _to_decimal(self.annual_coupon_rate, "annual_coupon_rate")

        # Alinhamento regulatório de cupom
        if self.bond_type == TPFBondType.NTN_F and self.annual_coupon_rate == Decimal("0.0000"):
            self.annual_coupon_rate = Decimal("0.1000")  # 10.00% a.a. regulatório NTN-F
        elif self.bond_type == TPFBondType.NTN_B and self.annual_coupon_rate == Decimal("0.0000"):
            self.annual_coupon_rate = Decimal("0.0600")  # 6.00% a.a. regulatório NTN-B
        elif not self.bond_type.has_semiannual_coupon:
            self.annual_coupon_rate = Decimal("0.0000")

    def calculate_semiannual_coupon_pu(self, reference_nominal_value: Optional[Decimal] = None) -> Decimal:
        """Calcula o valor financeiro do cupom semestral por unidade de título.

        NTN-F: Cupom = R$ 1.000,00 * ((1 + 0.10)^0.5 - 1) = R$ 48,808848
        NTN-B: Cupom = VNA * ((1 + 0.06)^0.5 - 1) = VNA * 0,02956301
        """
        if not self.bond_type.has_semiannual_coupon:
            return Decimal("0.000000")

        if self.bond_type == TPFBondType.NTN_F:
            v_nom = reference_nominal_value or Decimal("1000.000000")
            # Fator semestral = (1.10)^0.5 - 1 = 0.048808848...
            factor = Decimal(f"{pow(1.10, 0.5) - 1.0:.12f}")
            return round_pu(v_nom * factor)

        if self.bond_type == TPFBondType.NTN_B:
            v_nom = reference_nominal_value or self.market_unit_price
            # Fator semestral = (1.06)^0.5 - 1 = 0.029563014...
            factor = Decimal(f"{pow(1.06, 0.5) - 1.0:.12f}")
            return round_pu(v_nom * factor)

        return Decimal("0.000000")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bond_id": self.bond_id,
            "bond_type": self.bond_type.value,
            "isin": self.isin,
            "issue_date": self.issue_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "market_unit_price": str(self.market_unit_price),
            "annual_coupon_rate": str(self.annual_coupon_rate),
            "has_semiannual_coupon": self.bond_type.has_semiannual_coupon,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SovereignBond:
        return cls(
            bond_id=data["bond_id"],
            bond_type=TPFBondType.from_str(data["bond_type"]),
            isin=data["isin"],
            issue_date=data["issue_date"],
            maturity_date=data["maturity_date"],
            market_unit_price=Decimal(str(data["market_unit_price"])),
            annual_coupon_rate=Decimal(str(data.get("annual_coupon_rate", "0.0000"))),
            description=data.get("description", ""),
        )


@dataclass
class SovereignCustodyPosition:
    """Posição de custódia de um TPF específico sob a conta depositária SELIC / B3."""
    bond_id: str
    total_quantity: Decimal
    available_quantity: Decimal
    lent_quantity: Decimal = Decimal("0.0000")
    collateral_blocked_quantity: Decimal = Decimal("0.0000")

    def __post_init__(self) -> None:
        self.bond_id = str(self.bond_id).strip()
        self.total_quantity = _to_decimal(self.total_quantity, "total_quantity").quantize(QUANTIZE_QTY)
        self.available_quantity = _to_decimal(self.available_quantity, "available_quantity").quantize(QUANTIZE_QTY)
        self.lent_quantity = _to_decimal(self.lent_quantity, "lent_quantity").quantize(QUANTIZE_QTY)
        self.collateral_blocked_quantity = _to_decimal(
            self.collateral_blocked_quantity, "collateral_blocked_quantity"
        ).quantize(QUANTIZE_QTY)
        self.verify_conservation()

    def verify_conservation(self, account_id: str = "") -> bool:
        """Verifica formalmente a Lei 59 de Conservação de Custódia Soberana."""
        if any(
            q < Decimal("0.0000")
            for q in (self.total_quantity, self.available_quantity, self.lent_quantity, self.collateral_blocked_quantity)
        ):
            raise SovereignCustodyConservationError(
                f"Quantidade negativa detectada na conta '{account_id}', título '{self.bond_id}'."
            )
        expected_total = self.available_quantity + self.lent_quantity + self.collateral_blocked_quantity
        if self.total_quantity != expected_total:
            raise SovereignCustodyConservationError(
                f"Violação da conservação de custódia soberana na conta '{account_id}', título '{self.bond_id}': "
                f"Total ({self.total_quantity}) != Disponível ({self.available_quantity}) + "
                f"Emprestada ({self.lent_quantity}) + Bloqueada ({self.collateral_blocked_quantity})"
            )
        assert_sovereign_custody_conservation(
            total_quantity=self.total_quantity,
            available_quantity=self.available_quantity,
            lent_quantity=self.lent_quantity,
            collateral_blocked_quantity=self.collateral_blocked_quantity,
            account_id=account_id,
            bond_id=self.bond_id,
        )
        return True

    def lock_collateral(self, quantity: Decimal, account_id: str = "") -> None:
        qty = _to_decimal(quantity, "quantity").quantize(QUANTIZE_QTY)
        if qty <= Decimal("0.0000"):
            raise SovereignTpfValidationError("Quantidade para bloqueio de garantia deve ser positiva.")
        if self.available_quantity < qty:
            raise SovereignCustodyConservationError(
                f"Saldo disponível insuficiente ({self.available_quantity}) para bloquear {qty} em '{self.bond_id}'."
            )
        self.available_quantity -= qty
        self.collateral_blocked_quantity += qty
        self.verify_conservation(account_id)

    def release_collateral(self, quantity: Decimal, account_id: str = "") -> None:
        qty = _to_decimal(quantity, "quantity").quantize(QUANTIZE_QTY)
        if qty <= Decimal("0.0000"):
            raise SovereignTpfValidationError("Quantidade para liberação de garantia deve ser positiva.")
        if self.collateral_blocked_quantity < qty:
            raise SovereignCustodyConservationError(
                f"Saldo em garantia insuficiente ({self.collateral_blocked_quantity}) para liberar {qty} em '{self.bond_id}'."
            )
        self.collateral_blocked_quantity -= qty
        self.available_quantity += qty
        self.verify_conservation(account_id)

    def lend(self, quantity: Decimal, account_id: str = "") -> None:
        qty = _to_decimal(quantity, "quantity").quantize(QUANTIZE_QTY)
        if qty <= Decimal("0.0000"):
            raise SovereignTpfValidationError("Quantidade para empréstimo deve ser positiva.")
        if self.available_quantity < qty:
            raise SovereignCustodyConservationError(
                f"Saldo disponível insuficiente ({self.available_quantity}) para emprestar {qty} de '{self.bond_id}'."
            )
        self.available_quantity -= qty
        self.lent_quantity += qty
        self.verify_conservation(account_id)

    def return_lending(self, quantity: Decimal, account_id: str = "") -> None:
        qty = _to_decimal(quantity, "quantity").quantize(QUANTIZE_QTY)
        if qty <= Decimal("0.0000"):
            raise SovereignTpfValidationError("Quantidade de retorno de empréstimo deve ser positiva.")
        if self.lent_quantity < qty:
            raise SovereignCustodyConservationError(
                f"Saldo emprestado insuficiente ({self.lent_quantity}) para retorno de {qty} de '{self.bond_id}'."
            )
        self.lent_quantity -= qty
        self.available_quantity += qty
        self.verify_conservation(account_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bond_id": self.bond_id,
            "total_quantity": str(self.total_quantity),
            "available_quantity": str(self.available_quantity),
            "lent_quantity": str(self.lent_quantity),
            "collateral_blocked_quantity": str(self.collateral_blocked_quantity),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SovereignCustodyPosition:
        return cls(
            bond_id=data["bond_id"],
            total_quantity=Decimal(str(data["total_quantity"])),
            available_quantity=Decimal(str(data["available_quantity"])),
            lent_quantity=Decimal(str(data.get("lent_quantity", "0.0000"))),
            collateral_blocked_quantity=Decimal(str(data.get("collateral_blocked_quantity", "0.0000"))),
        )


@dataclass
class SovereignCustodyAccount:
    """Conta depositária de custódia soberana na B3 / SELIC."""
    account_id: str
    participant_id: str
    participant_name: str
    cash_balance: Decimal = Decimal("0.00")
    cash_blocked_collateral: Decimal = Decimal("0.00")
    positions: Dict[str, SovereignCustodyPosition] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.account_id = str(self.account_id).strip()
        self.participant_id = str(self.participant_id).strip()
        self.participant_name = str(self.participant_name).strip()
        self.cash_balance = round_cents(_to_decimal(self.cash_balance, "cash_balance"))
        self.cash_blocked_collateral = round_cents(
            _to_decimal(self.cash_blocked_collateral, "cash_blocked_collateral")
        )
        if self.cash_balance < Decimal("0.00") or self.cash_blocked_collateral < Decimal("0.00"):
            raise SovereignTpfValidationError("Saldos de caixa não podem ser negativos.")

    def get_position(self, bond_id: str) -> SovereignCustodyPosition:
        if bond_id not in self.positions:
            self.positions[bond_id] = SovereignCustodyPosition(
                bond_id=bond_id,
                total_quantity=Decimal("0.0000"),
                available_quantity=Decimal("0.0000"),
                lent_quantity=Decimal("0.0000"),
                collateral_blocked_quantity=Decimal("0.0000"),
            )
        return self.positions[bond_id]

    def deposit_cash(self, amount: Decimal) -> None:
        amt = round_cents(_to_decimal(amount, "amount"))
        if amt <= Decimal("0.00"):
            raise SovereignTpfValidationError("Depósito de caixa deve ser positivo.")
        self.cash_balance += amt

    def withdraw_cash(self, amount: Decimal) -> None:
        amt = round_cents(_to_decimal(amount, "amount"))
        if amt <= Decimal("0.00"):
            raise SovereignTpfValidationError("Saque de caixa deve ser positivo.")
        if self.cash_balance < amt:
            raise SovereignTpfValidationError(
                f"Saldo disponível em caixa insuficiente ({self.cash_balance}) para saque de {amt}."
            )
        self.cash_balance -= amt

    def lock_cash_margin(self, amount: Decimal) -> None:
        amt = round_cents(_to_decimal(amount, "amount"))
        if amt <= Decimal("0.00"):
            raise SovereignTpfValidationError("Bloqueio de caixa para garantia deve ser positivo.")
        if self.cash_balance < amt:
            raise SovereignCollateralDeficitError(
                f"Caixa insuficiente ({self.cash_balance}) para alocar garantia de {amt}."
            )
        self.cash_balance -= amt
        self.cash_blocked_collateral += amt

    def release_cash_margin(self, amount: Decimal) -> None:
        amt = round_cents(_to_decimal(amount, "amount"))
        if amt <= Decimal("0.00"):
            raise SovereignTpfValidationError("Liberação de caixa de garantia deve ser positiva.")
        if self.cash_blocked_collateral < amt:
            raise SovereignCollateralDeficitError(
                f"Caixa bloqueado insuficiente ({self.cash_blocked_collateral}) para liberar {amt}."
            )
        self.cash_blocked_collateral -= amt
        self.cash_balance += amt

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "participant_id": self.participant_id,
            "participant_name": self.participant_name,
            "cash_balance": str(self.cash_balance),
            "cash_blocked_collateral": str(self.cash_blocked_collateral),
            "positions": {b_id: pos.to_dict() for b_id, pos in self.positions.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SovereignCustodyAccount:
        acc = cls(
            account_id=data["account_id"],
            participant_id=data["participant_id"],
            participant_name=data["participant_name"],
            cash_balance=Decimal(str(data.get("cash_balance", "0.00"))),
            cash_blocked_collateral=Decimal(str(data.get("cash_blocked_collateral", "0.00"))),
        )
        if "positions" in data:
            for b_id, pos_raw in data["positions"].items():
                acc.positions[b_id] = SovereignCustodyPosition.from_dict(pos_raw)
        return acc


@dataclass
class CouponForwardEvent:
    """Registro auditável do repasse integral de cupom semestral de NTN-B/NTN-F."""
    event_id: str
    contract_id: str
    bond_id: str
    quantity: Decimal
    coupon_unit_value: Decimal
    total_coupon_amount: Decimal
    record_date: date
    payment_date: date
    borrower_account_id: str
    lender_account_id: str
    status: str = "SETTLED"

    def __post_init__(self) -> None:
        self.quantity = _to_decimal(self.quantity, "quantity").quantize(QUANTIZE_QTY)
        self.coupon_unit_value = round_pu(_to_decimal(self.coupon_unit_value, "coupon_unit_value"))
        self.total_coupon_amount = round_cents(_to_decimal(self.total_coupon_amount, "total_coupon_amount"))
        self.record_date = _to_date(self.record_date, "record_date")
        self.payment_date = _to_date(self.payment_date, "payment_date")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "contract_id": self.contract_id,
            "bond_id": self.bond_id,
            "quantity": str(self.quantity),
            "coupon_unit_value": str(self.coupon_unit_value),
            "total_coupon_amount": str(self.total_coupon_amount),
            "record_date": self.record_date.isoformat(),
            "payment_date": self.payment_date.isoformat(),
            "borrower_account_id": self.borrower_account_id,
            "lender_account_id": self.lender_account_id,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CouponForwardEvent:
        return cls(
            event_id=data["event_id"],
            contract_id=data["contract_id"],
            bond_id=data["bond_id"],
            quantity=Decimal(str(data["quantity"])),
            coupon_unit_value=Decimal(str(data["coupon_unit_value"])),
            total_coupon_amount=Decimal(str(data["total_coupon_amount"])),
            record_date=data["record_date"],
            payment_date=data["payment_date"],
            borrower_account_id=data["borrower_account_id"],
            lender_account_id=data["lender_account_id"],
            status=data.get("status", "SETTLED"),
        )


@dataclass
class CollateralMarginAllocation:
    """Alocação de garantia multilateral aportada à Câmara B3 para um empréstimo de TPF."""
    allocation_id: str
    contract_id: str
    account_id: str
    asset_type: CollateralAssetType
    cash_amount: Decimal = Decimal("0.00")
    bond_id: Optional[str] = None
    bond_quantity: Decimal = Decimal("0.0000")
    valuation_amount: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.cash_amount = round_cents(_to_decimal(self.cash_amount, "cash_amount"))
        self.bond_quantity = _to_decimal(self.bond_quantity, "bond_quantity").quantize(QUANTIZE_QTY)
        self.valuation_amount = round_cents(_to_decimal(self.valuation_amount, "valuation_amount"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allocation_id": self.allocation_id,
            "contract_id": self.contract_id,
            "account_id": self.account_id,
            "asset_type": self.asset_type.value,
            "cash_amount": str(self.cash_amount),
            "bond_id": self.bond_id,
            "bond_quantity": str(self.bond_quantity),
            "valuation_amount": str(self.valuation_amount),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CollateralMarginAllocation:
        return cls(
            allocation_id=data["allocation_id"],
            contract_id=data["contract_id"],
            account_id=data["account_id"],
            asset_type=CollateralAssetType(data["asset_type"]),
            cash_amount=Decimal(str(data.get("cash_amount", "0.00"))),
            bond_id=data.get("bond_id"),
            bond_quantity=Decimal(str(data.get("bond_quantity", "0.0000"))),
            valuation_amount=Decimal(str(data.get("valuation_amount", "0.00"))),
        )


@dataclass
class SovereignLoanContract:
    """Contrato de Empréstimo Eletrônico de TPF no Balcão B3 (BTC Soberano)."""
    contract_id: str
    bond_id: str
    lender_account_id: str
    borrower_account_id: str
    quantity: Decimal
    market_unit_price: Decimal
    start_date: date
    end_date: date
    settlement_cycle: SettlementCycle
    rate_type: LendingRateType
    rental_rate_annual: Decimal
    business_days: int
    collateral_requirement_pct: Decimal = Decimal("1.0000")
    margin_posted: Decimal = Decimal("0.00")
    status: LoanStatus = LoanStatus.REGISTERED
    rental_fee_accrued: Decimal = Decimal("0.00")
    coupons_forwarded: List[CouponForwardEvent] = field(default_factory=list)
    margin_allocations: List[CollateralMarginAllocation] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.contract_id = str(self.contract_id).strip()
        self.bond_id = str(self.bond_id).strip()
        self.lender_account_id = str(self.lender_account_id).strip()
        self.borrower_account_id = str(self.borrower_account_id).strip()
        if self.lender_account_id == self.borrower_account_id:
            raise SovereignTpfValidationError("Doador e tomador não podem ser a mesma conta de custódia.")
        self.quantity = _to_decimal(self.quantity, "quantity").quantize(QUANTIZE_QTY)
        if self.quantity <= Decimal("0.0000"):
            raise SovereignTpfValidationError(f"Quantidade do empréstimo deve ser positiva: {self.quantity}")
        self.market_unit_price = round_pu(_to_decimal(self.market_unit_price, "market_unit_price"))
        if self.market_unit_price <= Decimal("0.00"):
            raise SovereignTpfValidationError(f"PU de mercado deve ser positivo: {self.market_unit_price}")
        self.start_date = _to_date(self.start_date, "start_date")
        self.end_date = _to_date(self.end_date, "end_date")
        if self.end_date < self.start_date:
            raise SovereignTpfValidationError(
                f"Data final do empréstimo ({self.end_date}) anterior à inicial ({self.start_date})."
            )
        self.settlement_cycle = SettlementCycle.from_str(self.settlement_cycle)
        self.rate_type = LendingRateType.from_str(self.rate_type)
        self.rental_rate_annual = _to_decimal(self.rental_rate_annual, "rental_rate_annual")
        if self.rental_rate_annual < Decimal("0.0000"):
            raise SovereignTpfValidationError(f"Taxa de aluguel anual não pode ser negativa: {self.rental_rate_annual}")
        self.business_days = int(self.business_days)
        if self.business_days < 0:
            raise SovereignTpfValidationError("Prazo em dias úteis não pode ser negativo.")
        self.collateral_requirement_pct = _to_decimal(
            self.collateral_requirement_pct, "collateral_requirement_pct"
        )
        if self.collateral_requirement_pct < MIN_COLLATERAL_RATIO_DEFAULT:
            raise SovereignCollateralDeficitError(
                f"Exigência de colateralização ({self.collateral_requirement_pct}) inferior ao mínimo regulatório de 100%."
            )
        self.margin_posted = round_cents(_to_decimal(self.margin_posted, "margin_posted"))
        self.rental_fee_accrued = round_cents(_to_decimal(self.rental_fee_accrued, "rental_fee_accrued"))
        if self.rental_fee_accrued == Decimal("0.00"):
            self.rental_fee_accrued = self.calculate_rental_fee()

    @property
    def loan_principal(self) -> Decimal:
        """Valor financeiro principal de mercado do empréstimo (PU * Quantidade)."""
        return round_cents(self.quantity * self.market_unit_price)

    @property
    def required_margin(self) -> Decimal:
        """Valor de garantia multilateral mínima exigida pela Câmara B3."""
        return round_cents(self.loan_principal * self.collateral_requirement_pct)

    def calculate_rental_fee(self) -> Decimal:
        """Calcula a taxa de aluguel remunerada ao doador na convenção B3 (DU/252)."""
        factor = self.rental_rate_annual * Decimal(self.business_days) / DAYS_PER_YEAR_WORKDAYS
        return round_cents(self.loan_principal * factor)

    def verify_margin_coverage(self) -> bool:
        """Verifica se o total de garantias alocadas cobre a margem mínima de 100% de PU."""
        if self.margin_posted < self.required_margin:
            raise SovereignCollateralDeficitError(
                f"Margem de garantia insuficiente no contrato '{self.contract_id}': "
                f"Alocada ({self.margin_posted}) < Exigida ({self.required_margin}) para principal ({self.loan_principal})."
            )
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "bond_id": self.bond_id,
            "lender_account_id": self.lender_account_id,
            "borrower_account_id": self.borrower_account_id,
            "quantity": str(self.quantity),
            "market_unit_price": str(self.market_unit_price),
            "loan_principal": str(self.loan_principal),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "settlement_cycle": self.settlement_cycle.value,
            "rate_type": self.rate_type.value,
            "rental_rate_annual": str(self.rental_rate_annual),
            "business_days": self.business_days,
            "collateral_requirement_pct": str(self.collateral_requirement_pct),
            "required_margin": str(self.required_margin),
            "margin_posted": str(self.margin_posted),
            "status": self.status.value,
            "rental_fee_accrued": str(self.rental_fee_accrued),
            "coupons_forwarded": [c.to_dict() for c in self.coupons_forwarded],
            "margin_allocations": [m.to_dict() for m in self.margin_allocations],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SovereignLoanContract:
        contract = cls(
            contract_id=data["contract_id"],
            bond_id=data["bond_id"],
            lender_account_id=data["lender_account_id"],
            borrower_account_id=data["borrower_account_id"],
            quantity=Decimal(str(data["quantity"])),
            market_unit_price=Decimal(str(data["market_unit_price"])),
            start_date=data["start_date"],
            end_date=data["end_date"],
            settlement_cycle=SettlementCycle.from_str(data["settlement_cycle"]),
            rate_type=LendingRateType.from_str(data["rate_type"]),
            rental_rate_annual=Decimal(str(data["rental_rate_annual"])),
            business_days=int(data["business_days"]),
            collateral_requirement_pct=Decimal(str(data.get("collateral_requirement_pct", "1.0000"))),
            margin_posted=Decimal(str(data.get("margin_posted", "0.00"))),
            status=LoanStatus(data.get("status", LoanStatus.REGISTERED.value)),
            rental_fee_accrued=Decimal(str(data.get("rental_fee_accrued", "0.00"))),
        )
        if "coupons_forwarded" in data:
            contract.coupons_forwarded = [
                CouponForwardEvent.from_dict(c) for c in data["coupons_forwarded"]
            ]
        if "margin_allocations" in data:
            contract.margin_allocations = [
                CollateralMarginAllocation.from_dict(m) for m in data["margin_allocations"]
            ]
        return contract


@dataclass
class SovereignRepoAgreement:
    """Operação Compromissada Bilateral com Títulos Públicos Federais (Open Market BACEN / SELIC / B3)."""
    repo_id: str
    bond_id: str
    seller_account_id: str  # Vendedor original (Tomador de recursos na perna de ida)
    buyer_account_id: str   # Comprador original (Aplicador de recursos na perna de ida)
    quantity: Decimal
    spot_unit_price: Decimal
    repo_annual_rate: Decimal
    start_date: date
    repurchase_date: date
    business_days: int
    status: RepoStatus = RepoStatus.REGISTERED
    spot_settlement_amount: Decimal = Decimal("0.00")
    repurchase_settlement_amount: Decimal = Decimal("0.00")
    interest_accrued: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.repo_id = str(self.repo_id).strip()
        self.bond_id = str(self.bond_id).strip()
        self.seller_account_id = str(self.seller_account_id).strip()
        self.buyer_account_id = str(self.buyer_account_id).strip()
        if self.seller_account_id == self.buyer_account_id:
            raise SovereignTpfValidationError("Vendedor e comprador não podem ser a mesma conta de custódia.")
        self.quantity = _to_decimal(self.quantity, "quantity").quantize(QUANTIZE_QTY)
        if self.quantity <= Decimal("0.0000"):
            raise SovereignTpfValidationError(f"Quantidade da compromissada deve ser positiva: {self.quantity}")
        self.spot_unit_price = round_pu(_to_decimal(self.spot_unit_price, "spot_unit_price"))
        if self.spot_unit_price <= Decimal("0.00"):
            raise SovereignTpfValidationError(f"PU de venda à vista deve ser positivo: {self.spot_unit_price}")
        self.repo_annual_rate = _to_decimal(self.repo_annual_rate, "repo_annual_rate")
        self.start_date = _to_date(self.start_date, "start_date")
        self.repurchase_date = _to_date(self.repurchase_date, "repurchase_date")
        if self.repurchase_date < self.start_date:
            raise SovereignTpfValidationError(
                f"Data de recompra ({self.repurchase_date}) anterior à data de início ({self.start_date})."
            )
        self.business_days = int(self.business_days)
        if self.business_days < 0:
            raise SovereignTpfValidationError("Prazo da compromissada em dias úteis não pode ser negativo.")

        # Cálculo estrito da Lei de Paridade de Recompra Repo (LAW-060)
        calc_spot = round_cents(self.quantity * self.spot_unit_price)
        factor = Decimal("1") + (self.repo_annual_rate * Decimal(self.business_days) / DAYS_PER_YEAR_WORKDAYS)
        calc_repurchase = round_cents(calc_spot * factor)

        if self.spot_settlement_amount == Decimal("0.00"):
            self.spot_settlement_amount = calc_spot
        else:
            self.spot_settlement_amount = round_cents(_to_decimal(self.spot_settlement_amount, "spot_settlement_amount"))

        if self.repurchase_settlement_amount == Decimal("0.00"):
            self.repurchase_settlement_amount = calc_repurchase
        else:
            self.repurchase_settlement_amount = round_cents(
                _to_decimal(self.repurchase_settlement_amount, "repurchase_settlement_amount")
            )

        self.interest_accrued = round_cents(self.repurchase_settlement_amount - self.spot_settlement_amount)
        self.verify_repurchase_parity()

    def verify_repurchase_parity(self) -> bool:
        """Verifica estritamente a Lei de Paridade de Recompra Repo (LAW-060)."""
        factor = Decimal("1") + (self.repo_annual_rate * Decimal(self.business_days) / DAYS_PER_YEAR_WORKDAYS)
        expected_repurchase = round_cents(self.spot_settlement_amount * factor)
        if self.repurchase_settlement_amount != expected_repurchase:
            raise SovereignRepoParityError(
                f"Violação da Paridade de Recompra no Repo '{self.repo_id}': "
                f"Preço de Recompra ({self.repurchase_settlement_amount}) != Esperado ({expected_repurchase}) "
                f"[Preço_Venda={self.spot_settlement_amount}, Taxa={self.repo_annual_rate}, DU={self.business_days}]."
            )
        assert_sovereign_repo_repurchase_parity(
            repurchase_settlement_amount=self.repurchase_settlement_amount,
            spot_settlement_amount=self.spot_settlement_amount,
            repo_annual_rate=self.repo_annual_rate,
            business_days=self.business_days,
            repo_id=self.repo_id,
        )
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "bond_id": self.bond_id,
            "seller_account_id": self.seller_account_id,
            "buyer_account_id": self.buyer_account_id,
            "quantity": str(self.quantity),
            "spot_unit_price": str(self.spot_unit_price),
            "repo_annual_rate": str(self.repo_annual_rate),
            "start_date": self.start_date.isoformat(),
            "repurchase_date": self.repurchase_date.isoformat(),
            "business_days": self.business_days,
            "status": self.status.value,
            "spot_settlement_amount": str(self.spot_settlement_amount),
            "repurchase_settlement_amount": str(self.repurchase_settlement_amount),
            "interest_accrued": str(self.interest_accrued),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SovereignRepoAgreement:
        return cls(
            repo_id=data["repo_id"],
            bond_id=data["bond_id"],
            seller_account_id=data["seller_account_id"],
            buyer_account_id=data["buyer_account_id"],
            quantity=Decimal(str(data["quantity"])),
            spot_unit_price=Decimal(str(data["spot_unit_price"])),
            repo_annual_rate=Decimal(str(data["repo_annual_rate"])),
            start_date=data["start_date"],
            repurchase_date=data["repurchase_date"],
            business_days=int(data["business_days"]),
            status=RepoStatus(data.get("status", RepoStatus.REGISTERED.value)),
            spot_settlement_amount=Decimal(str(data.get("spot_settlement_amount", "0.00"))),
            repurchase_settlement_amount=Decimal(str(data.get("repurchase_settlement_amount", "0.00"))),
            interest_accrued=Decimal(str(data.get("interest_accrued", "0.00"))),
        )


# =====================================================================
# MOTOR PRINCIPAL DO FLUXO FL-076
# =====================================================================

class SovereignTpfLendingEngine:
    """Motor de Empréstimo e Operações Compromissadas com Títulos Públicos Federais (FL-076)."""

    def __init__(self, holidays: Optional[Set[Tuple[int, int]]] = None) -> None:
        self.holidays = holidays if holidays is not None else set(DEFAULT_BRAZILIAN_HOLIDAYS)
        self.bonds: Dict[str, SovereignBond] = {}
        self.accounts: Dict[str, SovereignCustodyAccount] = {}
        self.loans: Dict[str, SovereignLoanContract] = {}
        self.repos: Dict[str, SovereignRepoAgreement] = {}
        self.coupon_history: List[CouponForwardEvent] = []

    # -----------------------------------------------------------------
    # GESTÃO DE TÍTULOS PÚBLICOS FEDERAIS
    # -----------------------------------------------------------------

    def register_bond(self, bond: SovereignBond) -> None:
        """Registra um Título Público Federal (LTN, LFT, NTN-B, NTN-F)."""
        if bond.bond_id in self.bonds:
            raise SovereignTpfValidationError(f"TPF '{bond.bond_id}' já cadastrado.")
        self.bonds[bond.bond_id] = bond

    def get_bond(self, bond_id: str) -> SovereignBond:
        """Recupera TPF pelo identificador."""
        if bond_id not in self.bonds:
            raise SovereignTpfValidationError(f"TPF '{bond_id}' não encontrado no cadastro.")
        return self.bonds[bond_id]

    def update_bond_market_price(self, bond_id: str, new_pu: Decimal) -> None:
        """Atualiza a cotação de mercado (mark-to-market) do PU do TPF."""
        bond = self.get_bond(bond_id)
        pu = round_pu(_to_decimal(new_pu, "new_pu"))
        if pu <= Decimal("0.00"):
            raise SovereignTpfValidationError("Novo PU de mercado deve ser estritamente positivo.")
        bond.market_unit_price = pu

    # -----------------------------------------------------------------
    # GESTÃO DE CONTAS E CUSTÓDIA SOBERANA
    # -----------------------------------------------------------------

    def register_account(self, account: SovereignCustodyAccount) -> None:
        """Registra uma conta participante de custódia na B3 / SELIC."""
        if account.account_id in self.accounts:
            raise SovereignTpfValidationError(f"Conta '{account.account_id}' já cadastrada.")
        self.accounts[account.account_id] = account

    def get_account(self, account_id: str) -> SovereignCustodyAccount:
        """Recupera conta pelo identificador."""
        if account_id not in self.accounts:
            raise SovereignTpfValidationError(f"Conta de custódia '{account_id}' não encontrada.")
        return self.accounts[account_id]

    def deposit_bond_custody(self, account_id: str, bond_id: str, quantity: Decimal) -> None:
        """Deposita títulos públicos em custódia livre disponível."""
        acc = self.get_account(account_id)
        self.get_bond(bond_id)  # valida existência do título
        qty = _to_decimal(quantity, "quantity").quantize(QUANTIZE_QTY)
        if qty <= Decimal("0.0000"):
            raise SovereignTpfValidationError("Quantidade depositada deve ser estritamente positiva.")
        pos = acc.get_position(bond_id)
        pos.total_quantity += qty
        pos.available_quantity += qty
        pos.verify_conservation(account_id)

    def withdraw_bond_custody(self, account_id: str, bond_id: str, quantity: Decimal) -> None:
        """Retira títulos públicos da custódia disponível."""
        acc = self.get_account(account_id)
        pos = acc.get_position(bond_id)
        qty = _to_decimal(quantity, "quantity").quantize(QUANTIZE_QTY)
        if qty <= Decimal("0.0000"):
            raise SovereignTpfValidationError("Quantidade retirada deve ser estritamente positiva.")
        if pos.available_quantity < qty:
            raise SovereignCustodyConservationError(
                f"Saldo disponível insuficiente ({pos.available_quantity}) para retirada de {qty} de '{bond_id}'."
            )
        pos.available_quantity -= qty
        pos.total_quantity -= qty
        pos.verify_conservation(account_id)

    def deposit_cash(self, account_id: str, amount: Decimal) -> None:
        """Deposita moeda corrente nacional (BRL) na conta."""
        acc = self.get_account(account_id)
        acc.deposit_cash(amount)

    def withdraw_cash(self, account_id: str, amount: Decimal) -> None:
        """Saca moeda corrente nacional (BRL) da conta."""
        acc = self.get_account(account_id)
        acc.withdraw_cash(amount)

    # -----------------------------------------------------------------
    # APORTE DE GARANTIAS MULTILATERAIS (CÂMARA B3)
    # -----------------------------------------------------------------

    def post_cash_margin(self, contract_id: str, amount: Decimal) -> CollateralMarginAllocation:
        """Aporta garantia multilateral em dinheiro (BRL) para um contrato de empréstimo."""
        contract = self.get_loan(contract_id)
        if contract.status != LoanStatus.REGISTERED:
            raise SovereignStateTransitionError(
                f"Garantias só podem ser aportadas em contratos REGISTERED. Status atual: {contract.status}"
            )
        acc = self.get_account(contract.borrower_account_id)
        amt = round_cents(_to_decimal(amount, "amount"))
        acc.lock_cash_margin(amt)

        alloc = CollateralMarginAllocation(
            allocation_id=f"MAR-CASH-{contract_id}-{len(contract.margin_allocations)+1:02d}",
            contract_id=contract_id,
            account_id=acc.account_id,
            asset_type=CollateralAssetType.CASH,
            cash_amount=amt,
            bond_id=None,
            bond_quantity=Decimal("0.0000"),
            valuation_amount=amt,
        )
        contract.margin_allocations.append(alloc)
        contract.margin_posted += amt
        return alloc

    def post_bond_margin(
        self,
        contract_id: str,
        collateral_bond_id: str,
        quantity: Decimal,
        haircut_pct: Decimal = Decimal("0.0000"),
    ) -> CollateralMarginAllocation:
        """Aporta garantia multilateral em TPF para um contrato de empréstimo."""
        contract = self.get_loan(contract_id)
        if contract.status != LoanStatus.REGISTERED:
            raise SovereignStateTransitionError(
                f"Garantias só podem ser aportadas em contratos REGISTERED. Status atual: {contract.status}"
            )
        acc = self.get_account(contract.borrower_account_id)
        bond = self.get_bond(collateral_bond_id)
        pos = acc.get_position(collateral_bond_id)

        qty = _to_decimal(quantity, "quantity").quantize(QUANTIZE_QTY)
        pos.lock_collateral(qty, acc.account_id)

        haircut = _to_decimal(haircut_pct, "haircut_pct")
        gross_value = round_cents(qty * bond.market_unit_price)
        net_valuation = round_cents(gross_value * (Decimal("1.0000") - haircut))

        alloc = CollateralMarginAllocation(
            allocation_id=f"MAR-BOND-{contract_id}-{len(contract.margin_allocations)+1:02d}",
            contract_id=contract_id,
            account_id=acc.account_id,
            asset_type=CollateralAssetType.SOVEREIGN_BOND,
            cash_amount=Decimal("0.00"),
            bond_id=collateral_bond_id,
            bond_quantity=qty,
            valuation_amount=net_valuation,
        )
        contract.margin_allocations.append(alloc)
        contract.margin_posted += net_valuation
        return alloc

    # -----------------------------------------------------------------
    # FLUXO DE EMPRÉSTIMO DE TPF (BTC SOBERANO)
    # -----------------------------------------------------------------

    def register_loan(
        self,
        contract_id: str,
        bond_id: str,
        lender_account_id: str,
        borrower_account_id: str,
        quantity: Decimal,
        start_date: date,
        end_date: date,
        settlement_cycle: Union[SettlementCycle, str] = SettlementCycle.D0,
        rate_type: Union[LendingRateType, str] = LendingRateType.PASSIVE,
        rental_rate_annual: Decimal = Decimal("0.0250"),
        collateral_requirement_pct: Decimal = Decimal("1.0000"),
    ) -> SovereignLoanContract:
        """Registra novo contrato de empréstimo de TPF na B3."""
        if contract_id in self.loans:
            raise SovereignTpfValidationError(f"Contrato de empréstimo '{contract_id}' já existente.")
        bond = self.get_bond(bond_id)
        self.get_account(lender_account_id)
        self.get_account(borrower_account_id)

        s_date = _to_date(start_date, "start_date")
        e_date = _to_date(end_date, "end_date")
        du = count_business_days(s_date, e_date, self.holidays)

        contract = SovereignLoanContract(
            contract_id=contract_id,
            bond_id=bond_id,
            lender_account_id=lender_account_id,
            borrower_account_id=borrower_account_id,
            quantity=quantity,
            market_unit_price=bond.market_unit_price,
            start_date=s_date,
            end_date=e_date,
            settlement_cycle=SettlementCycle.from_str(settlement_cycle),
            rate_type=LendingRateType.from_str(rate_type),
            rental_rate_annual=rental_rate_annual,
            business_days=du,
            collateral_requirement_pct=collateral_requirement_pct,
        )
        self.loans[contract_id] = contract
        return contract

    def get_loan(self, contract_id: str) -> SovereignLoanContract:
        """Recupera contrato de empréstimo pelo identificador."""
        if contract_id not in self.loans:
            raise SovereignTpfValidationError(f"Contrato de empréstimo '{contract_id}' não encontrado.")
        return self.loans[contract_id]

    def settle_loan_start(self, contract_id: str) -> None:
        """Executa a liquidação de abertura do empréstimo (D0 ou D+1).

        1. Verifica conformidade da margem de garantia multilateral (>= 100% PU de mercado).
        2. Bloqueia e transfere títulos da custódia do doador (lender) para emprestada.
        3. Credita disponibilidade do título na custódia do tomador (borrower).
        4. Altera o estado para ACTIVE.
        """
        contract = self.get_loan(contract_id)
        if contract.status != LoanStatus.REGISTERED:
            raise SovereignStateTransitionError(
                f"Contrato '{contract_id}' não pode ser iniciado a partir do status '{contract.status}'."
            )

        # Regra 3: Validação da colateralização multilateral mínima da Câmara B3 (100% de PU)
        contract.verify_margin_coverage()

        # Regra 1: Débito na disponibilidade do doador e marcação como lent_quantity
        lender_acc = self.get_account(contract.lender_account_id)
        lender_pos = lender_acc.get_position(contract.bond_id)
        lender_pos.lend(contract.quantity, lender_acc.account_id)

        # Entrega física do TPF na custódia do tomador
        borrower_acc = self.get_account(contract.borrower_account_id)
        borrower_pos = borrower_acc.get_position(contract.bond_id)
        borrower_pos.total_quantity += contract.quantity
        borrower_pos.available_quantity += contract.quantity
        borrower_pos.verify_conservation(borrower_acc.account_id)

        contract.status = LoanStatus.ACTIVE

    def process_coupon_forward(
        self,
        contract_id: str,
        coupon_per_unit: Decimal,
        record_date: date,
        payment_date: date,
    ) -> CouponForwardEvent:
        """Executa o repasse integral de cupom semestral do tomador ao doador (NTN-B e NTN-F).

        Conforme regras B3 e SELIC:
        O tomador deve transferir 100% do valor econômico dos cupons ao doador original.
        """
        contract = self.get_loan(contract_id)
        if contract.status != LoanStatus.ACTIVE:
            raise SovereignStateTransitionError(
                f"Repasse de cupom só é permitido para empréstimos ativos. Status atual: {contract.status}"
            )

        bond = self.get_bond(contract.bond_id)
        if not bond.bond_type.has_semiannual_coupon:
            raise SovereignCouponForwardError(
                f"Título '{bond.bond_id}' ({bond.bond_type.value}) não possui previsão de cupom semestral."
            )

        coupon_pu = round_pu(_to_decimal(coupon_per_unit, "coupon_per_unit"))
        if coupon_pu <= Decimal("0.000000"):
            raise SovereignCouponForwardError(f"Valor unitário do cupom deve ser positivo: {coupon_pu}")

        total_coupon = round_cents(contract.quantity * coupon_pu)

        # Débito no caixa do tomador e crédito no caixa do doador
        borrower_acc = self.get_account(contract.borrower_account_id)
        lender_acc = self.get_account(contract.lender_account_id)

        if borrower_acc.cash_balance < total_coupon:
            contract.status = LoanStatus.DEFAULTED
            raise SovereignCouponForwardError(
                f"Tomador '{borrower_acc.account_id}' inadimplente por saldo insuficiente ({borrower_acc.cash_balance}) "
                f"para repassar cupom de R$ {total_coupon} ao doador '{lender_acc.account_id}'."
            )

        borrower_acc.withdraw_cash(total_coupon)
        lender_acc.deposit_cash(total_coupon)

        event = CouponForwardEvent(
            event_id=f"CPN-{contract_id}-{len(contract.coupons_forwarded)+1:02d}",
            contract_id=contract_id,
            bond_id=bond.bond_id,
            quantity=contract.quantity,
            coupon_unit_value=coupon_pu,
            total_coupon_amount=total_coupon,
            record_date=_to_date(record_date, "record_date"),
            payment_date=_to_date(payment_date, "payment_date"),
            borrower_account_id=borrower_acc.account_id,
            lender_account_id=lender_acc.account_id,
            status="SETTLED",
        )
        contract.coupons_forwarded.append(event)
        self.coupon_history.append(event)
        return event

    def settle_loan_return(self, contract_id: str) -> Decimal:
        """Executa a liquidação de encerramento/devolução do empréstimo de TPF.

        1. Tomador devolve os títulos públicos (disponíveis) ao doador.
        2. Doador recupera a disponibilidade e desfaz a marcação de 'lent_quantity'.
        3. Tomador paga a taxa de aluguel (rental_fee) ao doador via caixa.
        4. Câmara B3 desonera e libera 100% das garantias multilaterais bloqueadas.
        5. Altera status para RETURNED.
        """
        contract = self.get_loan(contract_id)
        if contract.status != LoanStatus.ACTIVE:
            raise SovereignStateTransitionError(
                f"Contrato '{contract_id}' não pode ser devolvido no status '{contract.status}'."
            )

        borrower_acc = self.get_account(contract.borrower_account_id)
        lender_acc = self.get_account(contract.lender_account_id)

        borrower_pos = borrower_acc.get_position(contract.bond_id)
        if borrower_pos.available_quantity < contract.quantity:
            contract.status = LoanStatus.DEFAULTED
            raise SovereignCustodyConservationError(
                f"Tomador '{borrower_acc.account_id}' não possui custódia disponível suficiente "
                f"({borrower_pos.available_quantity}) para devolver {contract.quantity} de '{contract.bond_id}'."
            )

        # Devolução de custódia
        borrower_pos.available_quantity -= contract.quantity
        borrower_pos.total_quantity -= contract.quantity
        borrower_pos.verify_conservation(borrower_acc.account_id)

        lender_pos = lender_acc.get_position(contract.bond_id)
        lender_pos.return_lending(contract.quantity, lender_acc.account_id)

        # Liquidação financeira da taxa de aluguel
        rental_fee = contract.calculate_rental_fee()
        if borrower_acc.cash_balance < rental_fee:
            contract.status = LoanStatus.DEFAULTED
            raise SovereignTpfValidationError(
                f"Tomador não possui caixa ({borrower_acc.cash_balance}) para liquidar taxa de aluguel ({rental_fee})."
            )

        borrower_acc.withdraw_cash(rental_fee)
        lender_acc.deposit_cash(rental_fee)
        contract.rental_fee_accrued = rental_fee

        # Desoneração integral das garantias multilaterais alocadas
        for alloc in contract.margin_allocations:
            if alloc.asset_type == CollateralAssetType.CASH:
                borrower_acc.release_cash_margin(alloc.cash_amount)
            elif alloc.asset_type == CollateralAssetType.SOVEREIGN_BOND and alloc.bond_id:
                b_pos = borrower_acc.get_position(alloc.bond_id)
                b_pos.release_collateral(alloc.bond_quantity, borrower_acc.account_id)

        contract.status = LoanStatus.RETURNED
        return rental_fee

    # -----------------------------------------------------------------
    # OPERAÇÕES COMPROMISSADAS (REPOS BILATERAIS OPEN MARKET / B3)
    # -----------------------------------------------------------------

    def register_repo(
        self,
        repo_id: str,
        bond_id: str,
        seller_account_id: str,
        buyer_account_id: str,
        quantity: Decimal,
        spot_unit_price: Decimal,
        repo_annual_rate: Decimal,
        start_date: date,
        repurchase_date: date,
    ) -> SovereignRepoAgreement:
        """Registra Operação Compromissada Bilateral com TPF (Open Market BACEN / SELIC / B3)."""
        if repo_id in self.repos:
            raise SovereignTpfValidationError(f"Compromissada '{repo_id}' já cadastrada.")
        self.get_bond(bond_id)
        self.get_account(seller_account_id)
        self.get_account(buyer_account_id)

        s_date = _to_date(start_date, "start_date")
        r_date = _to_date(repurchase_date, "repurchase_date")
        du = count_business_days(s_date, r_date, self.holidays)

        repo = SovereignRepoAgreement(
            repo_id=repo_id,
            bond_id=bond_id,
            seller_account_id=seller_account_id,
            buyer_account_id=buyer_account_id,
            quantity=quantity,
            spot_unit_price=spot_unit_price,
            repo_annual_rate=repo_annual_rate,
            start_date=s_date,
            repurchase_date=r_date,
            business_days=du,
        )
        self.repos[repo_id] = repo
        return repo

    def get_repo(self, repo_id: str) -> SovereignRepoAgreement:
        """Recupera compromissada pelo identificador."""
        if repo_id not in self.repos:
            raise SovereignTpfValidationError(f"Compromissada '{repo_id}' não encontrada.")
        return self.repos[repo_id]

    def settle_repo_leg1(self, repo_id: str) -> None:
        """Liquida a Perna de Ida (Spot Leg) da Operação Compromissada.

        1. Vendedor transfere títulos da custódia disponível para o Comprador.
        2. Comprador transfere recursos financeiros (Preço_Venda) para o Vendedor.
        3. Status -> SETTLED_LEG1.
        """
        repo = self.get_repo(repo_id)
        if repo.status != RepoStatus.REGISTERED:
            raise SovereignStateTransitionError(
                f"Perna de ida da compromissada '{repo_id}' não pode iniciar do status '{repo.status}'."
            )

        seller_acc = self.get_account(repo.seller_account_id)
        buyer_acc = self.get_account(repo.buyer_account_id)

        # Checagem de lastro do vendedor
        seller_pos = seller_acc.get_position(repo.bond_id)
        if seller_pos.available_quantity < repo.quantity:
            raise SovereignCustodyConservationError(
                f"Vendedor '{seller_acc.account_id}' sem lastro suficiente ({seller_pos.available_quantity}) "
                f"para entregar {repo.quantity} de '{repo.bond_id}' na ida."
            )

        # Checagem de caixa do comprador
        if buyer_acc.cash_balance < repo.spot_settlement_amount:
            raise SovereignTpfValidationError(
                f"Comprador '{buyer_acc.account_id}' sem caixa suficiente ({buyer_acc.cash_balance}) "
                f"para pagar {repo.spot_settlement_amount} na ida."
            )

        # Transferência física de custódia
        seller_pos.available_quantity -= repo.quantity
        seller_pos.total_quantity -= repo.quantity
        seller_pos.verify_conservation(seller_acc.account_id)

        buyer_pos = buyer_acc.get_position(repo.bond_id)
        buyer_pos.available_quantity += repo.quantity
        buyer_pos.total_quantity += repo.quantity
        buyer_pos.verify_conservation(buyer_acc.account_id)

        # Liquidação financeira
        buyer_acc.withdraw_cash(repo.spot_settlement_amount)
        seller_acc.deposit_cash(repo.spot_settlement_amount)

        repo.status = RepoStatus.SETTLED_LEG1

    def settle_repo_leg2(self, repo_id: str) -> None:
        """Liquida a Perna de Volta (Recompra a Termo) da Operação Compromissada.

        1. Comprador original devolve os títulos ao Vendedor original.
        2. Vendedor original paga o Preço de Recompra (Preço_Venda * (1 + Taxa*DU/252)) ao Comprador.
        3. Status -> SETTLED_LEG2.
        """
        repo = self.get_repo(repo_id)
        if repo.status != RepoStatus.SETTLED_LEG1:
            raise SovereignStateTransitionError(
                f"Perna de volta da compromissada '{repo_id}' requer perna de ida liquidada. Status: '{repo.status}'."
            )

        seller_acc = self.get_account(repo.seller_account_id)
        buyer_acc = self.get_account(repo.buyer_account_id)

        # Checagem de custódia do comprador para devolução
        buyer_pos = buyer_acc.get_position(repo.bond_id)
        if buyer_pos.available_quantity < repo.quantity:
            repo.status = RepoStatus.DEFAULTED
            raise SovereignCustodyConservationError(
                f"Comprador original '{buyer_acc.account_id}' sem custódia ({buyer_pos.available_quantity}) "
                f"para devolver {repo.quantity} de '{repo.bond_id}' na volta."
            )

        # Checagem de caixa do vendedor original para recompra
        if seller_acc.cash_balance < repo.repurchase_settlement_amount:
            repo.status = RepoStatus.DEFAULTED
            raise SovereignTpfValidationError(
                f"Vendedor original '{seller_acc.account_id}' sem caixa ({seller_acc.cash_balance}) "
                f"para recomprar por {repo.repurchase_settlement_amount}."
            )

        # Transferência física de custódia (devolução)
        buyer_pos.available_quantity -= repo.quantity
        buyer_pos.total_quantity -= repo.quantity
        buyer_pos.verify_conservation(buyer_acc.account_id)

        seller_pos = seller_acc.get_position(repo.bond_id)
        seller_pos.available_quantity += repo.quantity
        seller_pos.total_quantity += repo.quantity
        seller_pos.verify_conservation(seller_acc.account_id)

        # Liquidação financeira da recompra
        seller_acc.withdraw_cash(repo.repurchase_settlement_amount)
        buyer_acc.deposit_cash(repo.repurchase_settlement_amount)

        repo.status = RepoStatus.SETTLED_LEG2

    # -----------------------------------------------------------------
    # VERIFICAÇÃO FORMAL DE INVARIANTES DO SISTEMA
    # -----------------------------------------------------------------

    def verify_invariants(self) -> Dict[str, Any]:
        """Auditoria matemática formal e verificação das Leis Invariantes de TPFs na B3 e SELIC.

        Leis Auditadas:
        1. sovereign_custody_conservation (LAW-059):
           Total_Custódia == Disponível + Emprestada + Bloqueada_Garantia (em todas as contas).
        2. repo_repurchase_parity (LAW-060):
           Preço_Recompra == Preço_Venda * (1 + Taxa_Repo * DU/252) (em todas as compromissadas).
        3. multilateral_margin_coverage:
           Margem alocada >= 100% de PU de mercado em todos os empréstimos ativos.
        4. coupon_forward_integrity:
           Repasse total de cupons == Quantidade * PU_Cupom (somente NTN-B e NTN-F).
        5. non_negative_balances:
           Nenhum saldo físico ou financeiro negativo em qualquer conta.
        """
        violations: List[str] = []

        # 1. Lei de Conservação de Custódia Soberana e Não-Negatividade
        custody_holds = True
        non_negative_holds = True

        for acc_id, acc in self.accounts.items():
            if acc.cash_balance < Decimal("0.00") or acc.cash_blocked_collateral < Decimal("0.00"):
                non_negative_holds = False
                violations.append(f"Conta '{acc_id}' possui saldo de caixa negativo.")

            for b_id, pos in acc.positions.items():
                if any(
                    q < Decimal("0.0000")
                    for q in (pos.total_quantity, pos.available_quantity, pos.lent_quantity, pos.collateral_blocked_quantity)
                ):
                    non_negative_holds = False
                    violations.append(f"Conta '{acc_id}', TPF '{b_id}' possui quantidade física negativa.")

                expected = pos.available_quantity + pos.lent_quantity + pos.collateral_blocked_quantity
                if pos.total_quantity != expected:
                    custody_holds = False
                    violations.append(
                        f"Conta '{acc_id}', TPF '{b_id}': total ({pos.total_quantity}) != "
                        f"disp ({pos.available_quantity}) + emp ({pos.lent_quantity}) + bloq ({pos.collateral_blocked_quantity})"
                    )

        # 2. Lei de Paridade de Recompra Repo
        repo_parity_holds = True
        for repo_id, repo in self.repos.items():
            factor = Decimal("1") + (repo.repo_annual_rate * Decimal(repo.business_days) / DAYS_PER_YEAR_WORKDAYS)
            expected_repurchase = round_cents(repo.spot_settlement_amount * factor)
            if repo.repurchase_settlement_amount != expected_repurchase:
                repo_parity_holds = False
                violations.append(
                    f"Repo '{repo_id}': recompra ({repo.repurchase_settlement_amount}) != "
                    f"esperada ({expected_repurchase}) para spot ({repo.spot_settlement_amount})."
                )

        # 3. Colateralização Mínima de Margem Multilateral (100% de PU)
        margin_coverage_holds = True
        for loan_id, loan in self.loans.items():
            if loan.status == LoanStatus.ACTIVE:
                if loan.margin_posted < loan.required_margin:
                    margin_coverage_holds = False
                    violations.append(
                        f"Empréstimo ativo '{loan_id}': margem alocada ({loan.margin_posted}) < "
                        f"mínima exigida ({loan.required_margin})."
                    )

        # 4. Integridade do Repasse de Cupons Semestrais
        coupon_integrity_holds = True
        for ev in self.coupon_history:
            expected_coupon = round_cents(ev.quantity * ev.coupon_unit_value)
            if ev.total_coupon_amount != expected_coupon:
                coupon_integrity_holds = False
                violations.append(
                    f"Evento de cupom '{ev.event_id}': repasse ({ev.total_coupon_amount}) != "
                    f"esperado ({expected_coupon})."
                )
            bond = self.bonds.get(ev.bond_id)
            if bond and not bond.bond_type.has_semiannual_coupon:
                coupon_integrity_holds = False
                violations.append(f"Cupom repassado indevidamente para TPF '{ev.bond_id}' sem previsão de cupom.")

        all_holds = (
            custody_holds
            and repo_parity_holds
            and margin_coverage_holds
            and coupon_integrity_holds
            and non_negative_holds
        )

        return {
            "sovereign_custody_conservation": custody_holds,
            "repo_repurchase_parity": repo_parity_holds,
            "multilateral_margin_coverage": margin_coverage_holds,
            "coupon_forward_integrity": coupon_integrity_holds,
            "non_negative_balances": non_negative_holds,
            "all_invariants_hold": all_holds,
            "violations": violations,
        }

    # -----------------------------------------------------------------
    # SERIALIZAÇÃO E FIXTURE LOADER
    # -----------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serializa todo o estado operacional do motor para dicionário Python."""
        return {
            "flow_id": "FL-076",
            "description": "Empréstimo e Operações Compromissadas com Títulos Públicos Federais (TPF Lending / BTC Soberano / SELIC / B3)",
            "bonds": [b.to_dict() for b in self.bonds.values()],
            "accounts": [a.to_dict() for a in self.accounts.values()],
            "loans": [l.to_dict() for l in self.loans.values()],
            "repos": [r.to_dict() for r in self.repos.values()],
            "coupon_history": [c.to_dict() for c in self.coupon_history],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SovereignTpfLendingEngine:
        """Reconstitui o motor a partir de um dicionário serializado."""
        engine = cls()

        for b_raw in data.get("bonds", []):
            engine.register_bond(SovereignBond.from_dict(b_raw))

        for a_raw in data.get("accounts", []):
            engine.register_account(SovereignCustodyAccount.from_dict(a_raw))

        for l_raw in data.get("loans", []):
            loan = SovereignLoanContract.from_dict(l_raw)
            engine.loans[loan.contract_id] = loan

        for r_raw in data.get("repos", []):
            repo = SovereignRepoAgreement.from_dict(r_raw)
            engine.repos[repo.repo_id] = repo

        for c_raw in data.get("coupon_history", []):
            engine.coupon_history.append(CouponForwardEvent.from_dict(c_raw))

        return engine

    @classmethod
    def load_from_fixture(cls, filepath: Union[str, Path]) -> SovereignTpfLendingEngine:
        """Carrega e instancia o motor a partir de arquivo JSON de fixture."""
        p = Path(filepath)
        if not p.is_file():
            raise FileNotFoundError(f"Arquivo de fixture não encontrado: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
