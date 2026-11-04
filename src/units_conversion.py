"""Composição (Emissão) e Desmembramento (Cancelamento) de Units na Central Depositária B3 (FL-043).

Implementa o motor determinístico de conversão atômica em D+0 entre Ações Ordinárias (ON),
Ações Preferenciais (PN) e Certificados de Depósito de Ações (Units — sufixo 11), em
conformidade com o Art. 43 da Lei nº 6.404/1976 e regras da Central Depositária da B3 (CSD).

Recursos suportados:
1. Catálogo oficial de programas de Units B3 (SANB11, BPAC11, TAEE11, KLBN11, ENGI11).
2. Validação da proporção estrita de ações por Unit:
   - SANB11: 1 SANB3 + 1 SANB4
   - BPAC11: 1 BPAC3 + 2 BPAC4
   - TAEE11: 1 TAEE3 + 2 TAEE4
   - KLBN11: 1 KLBN3 + 4 KLBN4
   - ENGI11: 1 ENGI3 + 4 ENGI4
3. Bloqueio em períodos de suspensão de livros (Book Closure por AGE/AGO ou eventos).
4. Verificação de saldo livre exclusivo (vedada utilização de ativos gravados/em garantia).
5. Invariante de Conservação Física Total de Ações Equivalentes por Investidor:
   ON_Equivalente = ON_Livre + ON_Bloqueado + (Units_Livre + Units_Bloqueado) * Ratio_ON (constante)
   PN_Equivalente = PN_Livre + PN_Bloqueado + (Units_Livre + Units_Bloqueado) * Ratio_PN (constante)

100% biblioteca padrão do Python (`dataclasses`, `Decimal`, `datetime`, `enum`, `json`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")


def _quantize_money(val: Union[Decimal, str, int, float]) -> Decimal:
    return Decimal(str(val)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _to_date(val: Any, field_name: str) -> date:
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val.strip()[:10])
    raise ValueError(f"Campo '{field_name}' deve ser uma data válida.")


# ---------------------------------------------------------------------------
# Enumerações Tipadas
# ---------------------------------------------------------------------------

class UnitOperationType(str, Enum):
    COMPOSITION = "COMPOSITION"
    DISASSEMBLY = "DISASSEMBLY"


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class UnitsConversionError(Exception):
    """Exceção base para conversão de Units."""


class UnitProgramNotFoundError(UnitsConversionError):
    """Programa de Units não cadastrado na Depositária."""


class UnitCustodyAccountNotFoundError(UnitsConversionError):
    """Conta de custódia do investidor não cadastrada."""


class BookSuspensionError(UnitsConversionError):
    """Tentativa de conversão durante suspensão de livros (AGO/AGE)."""


class UnitRatioMismatchError(UnitsConversionError):
    """Proporção de ON/PN incompatível com a regra do programa de Units."""


class InsufficientFreeCustodyError(UnitsConversionError):
    """Saldo livre de ações ou Units insuficiente na conta de custódia."""


class UnitsConservationInvariantError(UnitsConversionError):
    """Violação da invariante de conservação de ações equivalentes."""


# ---------------------------------------------------------------------------
# Entidades de Domínio
# ---------------------------------------------------------------------------

@dataclass
class UnitProgramSpec:
    unit_ticker: str
    unit_isin: str
    issuer_name: str
    on_ticker: str
    on_isin: str
    ratio_on: int
    pn_ticker: str
    pn_isin: str
    ratio_pn: int
    book_keeper_institution: str = ""
    conversion_fee_per_unit: Decimal = Decimal("0.0000")
    min_conversion_fee_brl: Decimal = Decimal("0.00")
    book_suspended: bool = False
    suspension_reason: Optional[str] = None
    total_units_issued: int = 0
    locked_on_backing: int = 0
    locked_pn_backing: int = 0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> UnitProgramSpec:
        return cls(
            unit_ticker=str(d["unit_ticker"]).upper(),
            unit_isin=str(d["unit_isin"]).upper(),
            issuer_name=str(d["issuer_name"]),
            on_ticker=str(d["on_ticker"]).upper(),
            on_isin=str(d["on_isin"]).upper(),
            ratio_on=int(d["ratio_on"]),
            pn_ticker=str(d["pn_ticker"]).upper(),
            pn_isin=str(d["pn_isin"]).upper(),
            ratio_pn=int(d["ratio_pn"]),
            book_keeper_institution=str(d.get("book_keeper_institution", "")),
            conversion_fee_per_unit=Decimal(str(d.get("conversion_fee_per_unit", "0.0000"))),
            min_conversion_fee_brl=Decimal(str(d.get("min_conversion_fee_brl", "0.00"))),
            book_suspended=bool(d.get("book_suspended", False)),
            suspension_reason=d.get("suspension_reason"),
            total_units_issued=int(d.get("total_units_issued", 0)),
            locked_on_backing=int(d.get("locked_on_backing", 0)),
            locked_pn_backing=int(d.get("locked_pn_backing", 0)),
        )


@dataclass
class UnitCustodyAccount:
    participant_code: str
    investor_account: str
    investor_name: str
    investor_document: str
    unit_ticker: str
    on_free: int
    pn_free: int
    units_free: int
    on_encumbered: int = 0
    pn_encumbered: int = 0
    units_encumbered: int = 0

    @property
    def account_key(self) -> str:
        return f"{self.participant_code}:{self.investor_account}:{self.unit_ticker}"

    def equivalent_on(self, ratio_on: int) -> int:
        return (self.on_free + self.on_encumbered) + (self.units_free + self.units_encumbered) * ratio_on

    def equivalent_pn(self, ratio_pn: int) -> int:
        return (self.pn_free + self.pn_encumbered) + (self.units_free + self.units_encumbered) * ratio_pn

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> UnitCustodyAccount:
        return cls(
            participant_code=str(d["participant_code"]),
            investor_account=str(d["investor_account"]),
            investor_name=str(d.get("investor_name", "")),
            investor_document=str(d.get("investor_document", "")),
            unit_ticker=str(d["unit_ticker"]).upper(),
            on_free=int(d.get("on_free", 0)),
            pn_free=int(d.get("pn_free", 0)),
            units_free=int(d.get("units_free", 0)),
            on_encumbered=int(d.get("on_encumbered", 0)),
            pn_encumbered=int(d.get("pn_encumbered", 0)),
            units_encumbered=int(d.get("units_encumbered", 0)),
        )


@dataclass(frozen=True)
class UnitConversionInstruction:
    instruction_id: str
    unit_ticker: str
    operation_type: UnitOperationType
    participant_code: str
    investor_account: str
    unit_quantity: int
    on_quantity: int
    pn_quantity: int
    request_date: date
    waive_fee: bool = False
    external_reference: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> UnitConversionInstruction:
        return cls(
            instruction_id=str(d["instruction_id"]),
            unit_ticker=str(d["unit_ticker"]).upper(),
            operation_type=UnitOperationType(d["operation_type"]),
            participant_code=str(d["participant_code"]),
            investor_account=str(d["investor_account"]),
            unit_quantity=int(d["unit_quantity"]),
            on_quantity=int(d["on_quantity"]),
            pn_quantity=int(d["pn_quantity"]),
            request_date=_to_date(d["request_date"], "request_date"),
            waive_fee=bool(d.get("waive_fee", False)),
            external_reference=str(d.get("external_reference", "")),
        )


@dataclass(frozen=True)
class UnitConversionReceipt:
    instruction_id: str
    unit_ticker: str
    operation_type: UnitOperationType
    participant_code: str
    investor_account: str
    unit_quantity: int
    on_quantity: int
    pn_quantity: int
    settlement_date: date
    conversion_fee_brl: Decimal
    on_equivalent_before: int
    on_equivalent_after: int
    pn_equivalent_before: int
    pn_equivalent_after: int
    is_conservation_satisfied: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instruction_id": self.instruction_id,
            "unit_ticker": self.unit_ticker,
            "operation_type": self.operation_type.value,
            "participant_code": self.participant_code,
            "investor_account": self.investor_account,
            "unit_quantity": self.unit_quantity,
            "on_quantity": self.on_quantity,
            "pn_quantity": self.pn_quantity,
            "settlement_date": self.settlement_date.isoformat(),
            "conversion_fee_brl": str(self.conversion_fee_brl),
            "on_equivalent_before": self.on_equivalent_before,
            "on_equivalent_after": self.on_equivalent_after,
            "pn_equivalent_before": self.pn_equivalent_before,
            "pn_equivalent_after": self.pn_equivalent_after,
            "is_conservation_satisfied": self.is_conservation_satisfied,
        }


@dataclass(frozen=True)
class UnitsPortfolioSummary:
    total_programs: int
    total_accounts: int
    total_instructions_processed: int
    total_units_composed: int
    total_units_disassembled: int
    total_fees_collected_brl: Decimal
    all_conservations_satisfied: bool
    receipts: List[UnitConversionReceipt]


# ---------------------------------------------------------------------------
# Motor de Conversão de Units (UnitsConversionEngine)
# ---------------------------------------------------------------------------

class UnitsConversionEngine:
    """Motor determinístico da Depositária B3 para Composição e Desmembramento de Units."""

    def __init__(self) -> None:
        self._programs: Dict[str, UnitProgramSpec] = {}
        self._accounts: Dict[str, UnitCustodyAccount] = {}
        self._receipts: List[UnitConversionReceipt] = []

    @property
    def programs(self) -> Dict[str, UnitProgramSpec]:
        return self._programs

    @property
    def accounts(self) -> Dict[str, UnitCustodyAccount]:
        return self._accounts

    @property
    def receipts(self) -> List[UnitConversionReceipt]:
        return self._receipts

    def register_program(self, prog: UnitProgramSpec) -> UnitProgramSpec:
        self._programs[prog.unit_ticker] = prog
        return prog

    def register_account(self, acc: UnitCustodyAccount) -> UnitCustodyAccount:
        self._accounts[acc.account_key] = acc
        return acc

    def get_program(self, unit_ticker: str) -> UnitProgramSpec:
        t = unit_ticker.upper()
        if t not in self._programs:
            raise UnitProgramNotFoundError(f"Programa de Units '{t}' não encontrado.")
        return self._programs[t]

    def get_account(
        self, participant_code: str, investor_account: str, unit_ticker: str
    ) -> UnitCustodyAccount:
        key = f"{participant_code}:{investor_account}:{unit_ticker.upper()}"
        if key not in self._accounts:
            raise UnitCustodyAccountNotFoundError(
                f"Conta de custódia '{key}' não localizada para Units {unit_ticker}."
            )
        return self._accounts[key]

    def process_instruction(
        self, inst: UnitConversionInstruction
    ) -> UnitConversionReceipt:
        prog = self.get_program(inst.unit_ticker)
        acc = self.get_account(
            inst.participant_code, inst.investor_account, inst.unit_ticker
        )

        if prog.book_suspended:
            raise BookSuspensionError(
                f"Livro de conversão de '{prog.unit_ticker}' está suspenso: {prog.suspension_reason}"
            )

        # Validação de proporção exata
        req_on = inst.unit_quantity * prog.ratio_on
        req_pn = inst.unit_quantity * prog.ratio_pn
        if inst.on_quantity != req_on or inst.pn_quantity != req_pn:
            raise UnitRatioMismatchError(
                f"Proporção divergente para {inst.unit_quantity} {prog.unit_ticker}: "
                f"esperado ({req_on} ON, {req_pn} PN), informado ({inst.on_quantity} ON, {inst.pn_quantity} PN)."
            )

        equiv_on_before = acc.equivalent_on(prog.ratio_on)
        equiv_pn_before = acc.equivalent_pn(prog.ratio_pn)

        fee = Decimal("0.00")
        if not inst.waive_fee and prog.conversion_fee_per_unit > Decimal("0.0000"):
            calc_fee = Decimal(inst.unit_quantity) * prog.conversion_fee_per_unit
            fee = _quantize_money(max(calc_fee, prog.min_conversion_fee_brl))

        if inst.operation_type == UnitOperationType.COMPOSITION:
            # Requer ON_free >= req_on e PN_free >= req_pn
            if acc.on_free < req_on or acc.pn_free < req_pn:
                raise InsufficientFreeCustodyError(
                    f"Saldo livre insuficiente para compor {inst.unit_quantity} {prog.unit_ticker}: "
                    f"requer ({req_on} ON, {req_pn} PN), disponível ({acc.on_free} ON, {acc.pn_free} PN)."
                )
            acc.on_free -= req_on
            acc.pn_free -= req_pn
            acc.units_free += inst.unit_quantity
            prog.total_units_issued += inst.unit_quantity
            prog.locked_on_backing += req_on
            prog.locked_pn_backing += req_pn

        elif inst.operation_type == UnitOperationType.DISASSEMBLY:
            # Requer Units_free >= unit_quantity
            if acc.units_free < inst.unit_quantity:
                raise InsufficientFreeCustodyError(
                    f"Saldo livre de Units insuficiente para desmembrar {inst.unit_quantity} {prog.unit_ticker}: "
                    f"disponível {acc.units_free}."
                )
            acc.units_free -= inst.unit_quantity
            acc.on_free += req_on
            acc.pn_free += req_pn
            prog.total_units_issued -= inst.unit_quantity
            prog.locked_on_backing -= req_on
            prog.locked_pn_backing -= req_pn

        equiv_on_after = acc.equivalent_on(prog.ratio_on)
        equiv_pn_after = acc.equivalent_pn(prog.ratio_pn)

        conserved = (equiv_on_before == equiv_on_after) and (
            equiv_pn_before == equiv_pn_after
        )
        if not conserved:
            raise UnitsConservationInvariantError(
                f"Violação de conservação em {prog.unit_ticker}: "
                f"ON: {equiv_on_before} -> {equiv_on_after}, PN: {equiv_pn_before} -> {equiv_pn_after}."
            )

        receipt = UnitConversionReceipt(
            instruction_id=inst.instruction_id,
            unit_ticker=prog.unit_ticker,
            operation_type=inst.operation_type,
            participant_code=inst.participant_code,
            investor_account=inst.investor_account,
            unit_quantity=inst.unit_quantity,
            on_quantity=req_on,
            pn_quantity=req_pn,
            settlement_date=inst.request_date,
            conversion_fee_brl=fee,
            on_equivalent_before=equiv_on_before,
            on_equivalent_after=equiv_on_after,
            pn_equivalent_before=equiv_pn_before,
            pn_equivalent_after=equiv_pn_after,
            is_conservation_satisfied=conserved,
        )
        self._receipts.append(receipt)
        return receipt

    def process_all(
        self, instructions: Sequence[UnitConversionInstruction]
    ) -> List[UnitConversionReceipt]:
        return [self.process_instruction(i) for i in instructions]

    def summarize(self) -> UnitsPortfolioSummary:
        composed = sum(
            r.unit_quantity
            for r in self._receipts
            if r.operation_type == UnitOperationType.COMPOSITION
        )
        disassembled = sum(
            r.unit_quantity
            for r in self._receipts
            if r.operation_type == UnitOperationType.DISASSEMBLY
        )
        tot_fee = _quantize_money(sum(r.conversion_fee_brl for r in self._receipts))
        all_ok = all(r.is_conservation_satisfied for r in self._receipts)

        return UnitsPortfolioSummary(
            total_programs=len(self._programs),
            total_accounts=len(self._accounts),
            total_instructions_processed=len(self._receipts),
            total_units_composed=composed,
            total_units_disassembled=disassembled,
            total_fees_collected_brl=tot_fee,
            all_conservations_satisfied=all_ok,
            receipts=list(self._receipts),
        )

    portfolio_summary = summarize

    @classmethod
    def from_json_fixture(cls, path: Union[str, Path]) -> Tuple[UnitsConversionEngine, List[UnitConversionInstruction]]:
        fixture_path = Path(path)
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        engine = cls()

        for p_data in raw.get("programs", []):
            engine.register_program(UnitProgramSpec.from_dict(p_data))

        for a_data in raw.get("custody_accounts", []):
            engine.register_account(UnitCustodyAccount.from_dict(a_data))

        instructions = [
            UnitConversionInstruction.from_dict(i_data)
            for i_data in raw.get("conversion_instructions", [])
        ]
        return engine, instructions


__all__ = [
    "BookSuspensionError",
    "InsufficientFreeCustodyError",
    "UnitConversionInstruction",
    "UnitConversionReceipt",
    "UnitCustodyAccount",
    "UnitCustodyAccountNotFoundError",
    "UnitOperationType",
    "UnitProgramNotFoundError",
    "UnitProgramSpec",
    "UnitRatioMismatchError",
    "UnitsConservationInvariantError",
    "UnitsConversionEngine",
    "UnitsConversionError",
    "UnitsPortfolioSummary",
]
