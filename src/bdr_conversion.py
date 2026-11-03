"""Módulo de Emissão, Cancelamento e Eventos Corporativos de BDRs na B3 (FL-034).

Implementa o fluxo operacional e regulatório de *Brazilian Depositary Receipts* (BDRs)
Patrocinados (Níveis I, II e III) e Não Patrocinados (Nível I), em conformidade com a
Resolução CVM nº 182/2023 e o Manual de Procedimentos Operacionais da Central Depositária B3:

1. Paridade entre Ação-Lastro custodiada no exterior (DTC / Euroclear) e BDR na Depositária B3
   (ex: AAPL34 1:20, MSFT34 1:24, NVDC34 1:10, XPBR31 1:1).
2. Validação estrita de múltiplos inteiros da paridade (vedação de fração de ação-lastro no exterior).
3. Invariante de conservação estrita entre lastro bloqueado no custodiante internacional e
   saldo de BDRs emitidos na Central Depositária B3.
4. Cálculo determinístico de repasse de dividendos internacionais (USD -> BRL via PTAX,
   retenção de *Withholding Tax* na fonte estrangeira, IOF Câmbio de 0,38% e taxa da
   instituição depositária sobre o dividendo líquido).

100% biblioteca padrão Python (`dataclasses`, `Decimal`, `datetime`, `enum`, `json`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")

DEFAULT_US_WITHHOLDING_TAX_RATE = Decimal("0.3000")
DEFAULT_IOF_FX_RATE = Decimal("0.0038")
DEFAULT_DEPOSITARY_FEE_RATE = Decimal("0.0300")


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class BdrConversionError(Exception):
    """Exceção base para o motor de emissão, cancelamento e proventos de BDRs."""


class BdrValidationError(BdrConversionError):
    """Lançada quando dados cadastrais, parâmetros financeiros ou datas são inválidos."""


class ParityMultipleError(BdrValidationError):
    """Lançada quando a quantidade de BDRs/ações viola a paridade exata ou gera fração de ação no exterior."""


class InsufficientBdrBalanceError(BdrConversionError):
    """Lançada quando o cancelamento excede o saldo de BDRs emitidos ou o lastro bloqueado."""


class BackingConservationError(BdrConversionError):
    """Lançada quando a invariante de conservação entre lastro internacional e BDRs na B3 é violada."""


class ProgramNotFoundError(BdrConversionError):
    """Lançada quando o programa de BDR referenciado não está registrado no motor."""


# ---------------------------------------------------------------------------
# Funções Auxiliares de Conversão e Validação
# ---------------------------------------------------------------------------

def _to_decimal(value: Any, field_name: str) -> Decimal:
    """Converte entrada numérica ou string em Decimal finito."""
    if isinstance(value, Decimal):
        dec = value
    elif value is None or isinstance(value, bool):
        raise BdrValidationError(f"Campo '{field_name}' não pode ser nulo ou booleano.")
    else:
        try:
            dec = Decimal(str(value).strip())
        except (InvalidOperation, ValueError) as exc:
            raise BdrValidationError(
                f"Valor decimal inválido para '{field_name}': {value!r}"
            ) from exc
    if not dec.is_finite():
        raise BdrValidationError(f"Valor não finito para '{field_name}': {value!r}")
    return dec


def _to_exact_int(value: Any, field_name: str) -> int:
    """Converte valor para inteiro exato, rejeitando frações com ParityMultipleError."""
    if isinstance(value, bool) or value is None:
        raise BdrValidationError(f"Campo '{field_name}' deve ser um número inteiro válido.")
    if isinstance(value, int):
        return value
    dec = _to_decimal(value, field_name)
    if dec != dec.to_integral_value():
        raise ParityMultipleError(
            f"Fração não permitida no campo '{field_name}': {value!r}. "
            "Não são admitidas frações de ação-lastro ou de BDR."
        )
    return int(dec)


def _to_date(value: Any, field_name: str) -> date:
    """Converte string ISO-8601 (YYYY-MM-DD) ou date/datetime em objeto date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value or not isinstance(value, str):
        raise BdrValidationError(f"Data inválida para '{field_name}': {value!r}")
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise BdrValidationError(
            f"Formato de data inválido para '{field_name}' (esperado YYYY-MM-DD): {value!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Enumerações Regulatórias (Resolução CVM 182 e Manual Depositária B3)
# ---------------------------------------------------------------------------

class BdrSponsorLevel(str, Enum):
    """Níveis regulatórios de programas de BDR conforme Resolução CVM 182."""

    SPONSORED_LEVEL_1 = "SPONSORED_LEVEL_1"
    SPONSORED_LEVEL_2 = "SPONSORED_LEVEL_2"
    SPONSORED_LEVEL_3 = "SPONSORED_LEVEL_3"
    UNSPONSORED_LEVEL_1 = "UNSPONSORED_LEVEL_1"

    @property
    def is_sponsored(self) -> bool:
        """Indica se o programa é patrocinado pela companhia emissora estrangeira."""
        return self in (
            BdrSponsorLevel.SPONSORED_LEVEL_1,
            BdrSponsorLevel.SPONSORED_LEVEL_2,
            BdrSponsorLevel.SPONSORED_LEVEL_3,
        )

    @property
    def requires_cvm_issuer_registration(self) -> bool:
        """Indica se o nível exige registro de emissor estrangeiro na CVM."""
        return self in (
            BdrSponsorLevel.SPONSORED_LEVEL_2,
            BdrSponsorLevel.SPONSORED_LEVEL_3,
        )

    @property
    def expected_b3_suffixes(self) -> Tuple[str, ...]:
        """Retorna os sufixos de negociação padronizados pela B3 para o nível."""
        if self == BdrSponsorLevel.SPONSORED_LEVEL_1:
            return ("31",)
        if self == BdrSponsorLevel.SPONSORED_LEVEL_2:
            return ("32",)
        if self == BdrSponsorLevel.SPONSORED_LEVEL_3:
            return ("33",)
        return ("34", "35", "39")


class BdrOperationType(str, Enum):
    """Tipo de operação de conversão de BDR na Central Depositária B3."""

    ISSUANCE = "ISSUANCE"
    CANCELLATION = "CANCELLATION"


# ---------------------------------------------------------------------------
# Modelos de Domínio
# ---------------------------------------------------------------------------

@dataclass
class BdrProgram:
    """Programa de BDR registrado na B3 e vinculado a custodiante internacional.

    Mantém a razão de paridade determinística (`underlying_shares_per_block` : `bdr_per_share`)
    e os saldos de ações-lastro bloqueadas no exterior (`locked_underlying_shares`) e de
    certificados BDR em circulação na Depositária B3 (`issued_bdr_quantity`).
    """

    ticker: str
    bdr_isin: str
    underlying_ticker: str
    underlying_isin: str
    issuer_name: str
    sponsor_level: BdrSponsorLevel
    home_exchange: str
    overseas_custodian: str
    custodian_account: str
    depositary_institution: str
    bdr_per_share: int
    underlying_shares_per_block: int = 1
    currency: str = "USD"
    locked_underlying_shares: int = 0
    issued_bdr_quantity: int = 0

    def __post_init__(self) -> None:
        self.ticker = str(self.ticker or "").strip().upper()
        self.bdr_isin = str(self.bdr_isin or "").strip().upper()
        self.underlying_ticker = str(self.underlying_ticker or "").strip().upper()
        self.underlying_isin = str(self.underlying_isin or "").strip().upper()
        self.issuer_name = str(self.issuer_name or "").strip()
        self.home_exchange = str(self.home_exchange or "").strip().upper()
        self.overseas_custodian = str(self.overseas_custodian or "").strip().upper()
        self.custodian_account = str(self.custodian_account or "").strip()
        self.depositary_institution = str(self.depositary_institution or "").strip()
        self.currency = str(self.currency or "USD").strip().upper()

        if not isinstance(self.sponsor_level, BdrSponsorLevel):
            try:
                self.sponsor_level = BdrSponsorLevel(str(self.sponsor_level).strip().upper())
            except ValueError as exc:
                raise BdrValidationError(
                    f"Nível de patrocínio inválido: {self.sponsor_level!r}"
                ) from exc

        if len(self.ticker) < 5 or not self.ticker.endswith(self.sponsor_level.expected_b3_suffixes):
            raise BdrValidationError(
                f"Ticker BDR '{self.ticker}' incompatível com os sufixos esperados "
                f"{self.sponsor_level.expected_b3_suffixes} para {self.sponsor_level.value}."
            )

        if len(self.bdr_isin) != 12 or not self.bdr_isin.startswith("BR"):
            raise BdrValidationError(
                f"ISIN do BDR inválido (esperado 12 caracteres iniciando com 'BR'): {self.bdr_isin!r}"
            )
        if len(self.underlying_isin) != 12 or self.underlying_isin.startswith("BR"):
            raise BdrValidationError(
                f"ISIN do ativo-lastro estrangeiro inválido: {self.underlying_isin!r}"
            )
        if not self.underlying_ticker:
            raise BdrValidationError("Ticker do ativo-lastro no exterior é obrigatório.")
        if not self.overseas_custodian or not self.custodian_account:
            raise BdrValidationError(
                "Custodiante internacional (ex: DTC/Euroclear) e conta de custódia são obrigatórios."
            )

        self.underlying_shares_per_block = _to_exact_int(
            self.underlying_shares_per_block, "underlying_shares_per_block"
        )
        self.bdr_per_share = _to_exact_int(self.bdr_per_share, "bdr_per_share")
        if self.underlying_shares_per_block <= 0 or self.bdr_per_share <= 0:
            raise BdrValidationError(
                "Parâmetros de paridade (underlying_shares_per_block e bdr_per_share) devem ser > 0."
            )

        self.locked_underlying_shares = _to_exact_int(
            self.locked_underlying_shares, "locked_underlying_shares"
        )
        self.issued_bdr_quantity = _to_exact_int(
            self.issued_bdr_quantity, "issued_bdr_quantity"
        )
        if self.locked_underlying_shares < 0 or self.issued_bdr_quantity < 0:
            raise BdrValidationError(
                "Saldos de ações bloqueadas e BDRs emitidos não podem ser negativos."
            )

        self.validate_backing_conservation()

    @property
    def parity_ratio_description(self) -> str:
        """Retorna representação textual da paridade (ex: '1 Ação = 20 BDRs')."""
        return f"{self.underlying_shares_per_block} Share(s) = {self.bdr_per_share} BDR(s)"

    def shares_to_bdrs(self, underlying_shares: Union[int, Decimal]) -> int:
        """Calcula a quantidade exata de BDRs correspondente a `underlying_shares` ações-lastro."""
        shares_int = _to_exact_int(underlying_shares, "underlying_shares")
        if shares_int <= 0:
            raise BdrValidationError(
                f"Quantidade de ações-lastro deve ser estritamente positiva: {shares_int}"
            )
        if (shares_int * self.bdr_per_share) % self.underlying_shares_per_block != 0:
            raise ParityMultipleError(
                f"Quantidade de ações ({shares_int}) não é múltiplo válido do bloco "
                f"de paridade ({self.underlying_shares_per_block}:{self.bdr_per_share}) "
                f"para o programa {self.ticker}."
            )
        return (shares_int * self.bdr_per_share) // self.underlying_shares_per_block

    def bdrs_to_shares(self, bdr_quantity: Union[int, Decimal]) -> int:
        """Calcula a quantidade exata de ações-lastro correspondente a `bdr_quantity` BDRs.

        Lança `ParityMultipleError` caso a conversão resulte em fração de ação-lastro no exterior.
        """
        bdrs_int = _to_exact_int(bdr_quantity, "bdr_quantity")
        if bdrs_int <= 0:
            raise BdrValidationError(
                f"Quantidade de BDRs deve ser estritamente positiva: {bdrs_int}"
            )
        total_numerator = bdrs_int * self.underlying_shares_per_block
        if total_numerator % self.bdr_per_share != 0:
            fractional_shares = Decimal(total_numerator) / Decimal(self.bdr_per_share)
            raise ParityMultipleError(
                f"Quantidade de BDRs ({bdrs_int}) não é múltiplo exato da paridade "
                f"{self.underlying_shares_per_block}:{self.bdr_per_share} do programa {self.ticker} "
                f"(equivaleria a {fractional_shares} ações-lastro fracionárias no exterior)."
            )
        return total_numerator // self.bdr_per_share

    def validate_backing_conservation(self) -> None:
        """Verifica se o lastro bloqueado no exterior conserva exatamente os BDRs emitidos na B3."""
        expected_bdrs = (
            self.locked_underlying_shares * self.bdr_per_share
        )
        actual_scaled = self.issued_bdr_quantity * self.underlying_shares_per_block
        if expected_bdrs != actual_scaled:
            raise BackingConservationError(
                f"Violação de conservação de lastro no programa {self.ticker}: "
                f"locked_underlying_shares={self.locked_underlying_shares} com paridade "
                f"{self.underlying_shares_per_block}:{self.bdr_per_share} exige "
                f"{expected_bdrs // self.underlying_shares_per_block} BDRs, mas "
                f"issued_bdr_quantity={self.issued_bdr_quantity}."
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BdrProgram:
        """Instancia `BdrProgram` a partir de dicionário JSON."""
        return cls(
            ticker=data["ticker"],
            bdr_isin=data["bdr_isin"],
            underlying_ticker=data["underlying_ticker"],
            underlying_isin=data["underlying_isin"],
            issuer_name=data["issuer_name"],
            sponsor_level=BdrSponsorLevel(str(data["sponsor_level"]).strip().upper()),
            home_exchange=data.get("home_exchange", "NASDAQ"),
            overseas_custodian=data.get("overseas_custodian", "DTC"),
            custodian_account=data["custodian_account"],
            depositary_institution=data["depositary_institution"],
            underlying_shares_per_block=data.get("underlying_shares_per_block", 1),
            bdr_per_share=data["bdr_per_share"],
            currency=data.get("currency", "USD"),
            locked_underlying_shares=data.get("locked_underlying_shares", 0),
            issued_bdr_quantity=data.get("issued_bdr_quantity", 0),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o programa de BDR para dicionário compatível com JSON."""
        return {
            "ticker": self.ticker,
            "bdr_isin": self.bdr_isin,
            "underlying_ticker": self.underlying_ticker,
            "underlying_isin": self.underlying_isin,
            "issuer_name": self.issuer_name,
            "sponsor_level": self.sponsor_level.value,
            "home_exchange": self.home_exchange,
            "overseas_custodian": self.overseas_custodian,
            "custodian_account": self.custodian_account,
            "depositary_institution": self.depositary_institution,
            "underlying_shares_per_block": self.underlying_shares_per_block,
            "bdr_per_share": self.bdr_per_share,
            "currency": self.currency,
            "locked_underlying_shares": self.locked_underlying_shares,
            "issued_bdr_quantity": self.issued_bdr_quantity,
        }


@dataclass(frozen=True)
class BdrConversionRequest:
    """Solicitação formal de Emissão (`ISSUANCE`) ou Cancelamento (`CANCELLATION`) de BDRs."""

    request_id: str
    ticker: str
    operation_type: BdrOperationType
    participant_code: str
    investor_account: str
    overseas_broker_dtc_code: str
    request_date: date
    swift_reference: str
    underlying_shares_quantity: Optional[int] = None
    bdr_quantity: Optional[int] = None

    def __post_init__(self) -> None:
        req_id = str(self.request_id or "").strip()
        ticker = str(self.ticker or "").strip().upper()
        participant = str(self.participant_code or "").strip()
        account = str(self.investor_account or "").strip()
        dtc_code = str(self.overseas_broker_dtc_code or "").strip()
        swift_ref = str(self.swift_reference or "").strip()

        if not req_id:
            raise BdrValidationError("Campo 'request_id' é obrigatório.")
        if not ticker:
            raise BdrValidationError("Campo 'ticker' é obrigatório.")
        if not participant or not account:
            raise BdrValidationError("Código do participante e conta do investidor são obrigatórios.")
        if not dtc_code:
            raise BdrValidationError("Código do participante no custodiante internacional é obrigatório.")
        if not swift_ref:
            raise BdrValidationError("Referência SWIFT (MT540/MT542) é obrigatória.")

        op_type = self.operation_type
        if not isinstance(op_type, BdrOperationType):
            try:
                op_type = BdrOperationType(str(op_type).strip().upper())
            except ValueError as exc:
                raise BdrValidationError(
                    f"Tipo de operação BDR inválido: {self.operation_type!r}"
                ) from exc

        req_date = _to_date(self.request_date, "request_date")

        shares_qty: Optional[int] = None
        if self.underlying_shares_quantity is not None:
            shares_qty = _to_exact_int(
                self.underlying_shares_quantity, "underlying_shares_quantity"
            )
            if shares_qty <= 0:
                raise BdrValidationError(
                    f"Quantidade de ações-lastro deve ser > 0: {shares_qty}"
                )

        bdr_qty: Optional[int] = None
        if self.bdr_quantity is not None:
            bdr_qty = _to_exact_int(self.bdr_quantity, "bdr_quantity")
            if bdr_qty <= 0:
                raise BdrValidationError(
                    f"Quantidade de BDRs deve ser > 0: {bdr_qty}"
                )

        if shares_qty is None and bdr_qty is None:
            raise BdrValidationError(
                "Ao menos uma das quantidades ('underlying_shares_quantity' ou 'bdr_quantity') "
                "deve ser informada na solicitação de conversão."
            )

        object.__setattr__(self, "request_id", req_id)
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "operation_type", op_type)
        object.__setattr__(self, "participant_code", participant)
        object.__setattr__(self, "investor_account", account)
        object.__setattr__(self, "overseas_broker_dtc_code", dtc_code)
        object.__setattr__(self, "request_date", req_date)
        object.__setattr__(self, "swift_reference", swift_ref)
        object.__setattr__(self, "underlying_shares_quantity", shares_qty)
        object.__setattr__(self, "bdr_quantity", bdr_qty)

    def resolve_quantities(self, program: BdrProgram) -> Tuple[int, int]:
        """Resolve e valida o par (underlying_shares, bdr_quantity) frente à paridade do programa."""
        if program.ticker != self.ticker:
            raise BdrValidationError(
                f"Ticker da solicitação ({self.ticker}) difere do programa ({program.ticker})."
            )

        if self.underlying_shares_quantity is not None and self.bdr_quantity is not None:
            expected_bdrs = program.shares_to_bdrs(self.underlying_shares_quantity)
            expected_shares = program.bdrs_to_shares(self.bdr_quantity)
            if expected_bdrs != self.bdr_quantity or expected_shares != self.underlying_shares_quantity:
                raise ParityMultipleError(
                    f"Divergência de paridade na solicitação {self.request_id} ({self.ticker}): "
                    f"{self.underlying_shares_quantity} ações equivalem a {expected_bdrs} BDRs "
                    f"(paridade {program.underlying_shares_per_block}:{program.bdr_per_share}), "
                    f"mas foram informados {self.bdr_quantity} BDRs."
                )
            return self.underlying_shares_quantity, self.bdr_quantity

        if self.underlying_shares_quantity is not None:
            bdrs = program.shares_to_bdrs(self.underlying_shares_quantity)
            return self.underlying_shares_quantity, bdrs

        assert self.bdr_quantity is not None
        shares = program.bdrs_to_shares(self.bdr_quantity)
        return shares, self.bdr_quantity

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BdrConversionRequest:
        """Instancia `BdrConversionRequest` a partir de dicionário JSON."""
        return cls(
            request_id=data["request_id"],
            ticker=data["ticker"],
            operation_type=BdrOperationType(str(data["operation_type"]).strip().upper()),
            participant_code=data["participant_code"],
            investor_account=data["investor_account"],
            overseas_broker_dtc_code=data["overseas_broker_dtc_code"],
            underlying_shares_quantity=data.get("underlying_shares_quantity"),
            bdr_quantity=data.get("bdr_quantity"),
            request_date=_to_date(data["request_date"], "request_date"),
            swift_reference=data["swift_reference"],
        )


@dataclass(frozen=True)
class BdrConversionResult:
    """Resultado determinístico do processamento de uma emissão ou cancelamento de BDR."""

    request_id: str
    ticker: str
    operation_type: BdrOperationType
    participant_code: str
    investor_account: str
    underlying_shares_moved: int
    bdr_quantity_moved: int
    previous_locked_shares: int
    new_locked_shares: int
    previous_issued_bdrs: int
    new_issued_bdrs: int
    swift_reference: str
    status: str = "SETTLED"


@dataclass(frozen=True)
class BdrDividendDistribution:
    """Evento corporativo de distribuição de dividendos internacionais para programa de BDR.

    Calcula de forma determinística:
    1. Montante bruto em USD sobre as ações-lastro custodiadas (`gross_total_usd`).
    2. Imposto retido na fonte no país de origem (`withholding_tax_usd`, ex: 30% EUA).
    3. Montante líquido em USD recebido pela instituição depositária (`net_foreign_usd`).
    4. Conversão cambial via PTAX para BRL (`converted_gross_brl`).
    5. Retenção de IOF Câmbio (`iof_tax_brl`, ex: 0,38%).
    6. Taxa da instituição depositária (`depositary_fee_brl`, ex: 3% a 5% sobre líquido pós-IOF).
    7. Montante líquido total em BRL (`net_total_brl`) e valor líquido unitário por BDR (`net_dividend_per_bdr_brl`).
    """

    event_id: str
    ticker: str
    record_date: date
    payment_date_abroad: date
    payment_date_b3: date
    gross_dividend_per_share_usd: Decimal
    eligible_underlying_shares: int
    eligible_bdr_quantity: int
    ptax_exchange_rate: Decimal
    withholding_tax_rate: Decimal = DEFAULT_US_WITHHOLDING_TAX_RATE
    iof_fx_rate: Decimal = DEFAULT_IOF_FX_RATE
    depositary_fee_rate: Decimal = DEFAULT_DEPOSITARY_FEE_RATE
    currency: str = "USD"

    gross_total_usd: Decimal = field(init=False)
    withholding_tax_usd: Decimal = field(init=False)
    net_foreign_usd: Decimal = field(init=False)
    converted_gross_brl: Decimal = field(init=False)
    iof_tax_brl: Decimal = field(init=False)
    after_iof_brl: Decimal = field(init=False)
    depositary_fee_brl: Decimal = field(init=False)
    net_total_brl: Decimal = field(init=False)
    net_dividend_per_bdr_brl: Decimal = field(init=False)

    def __post_init__(self) -> None:
        ev_id = str(self.event_id or "").strip()
        ticker = str(self.ticker or "").strip().upper()
        curr = str(self.currency or "USD").strip().upper()

        if not ev_id:
            raise BdrValidationError("Campo 'event_id' é obrigatório.")
        if not ticker:
            raise BdrValidationError("Campo 'ticker' é obrigatório.")
        if curr != "USD":
            raise BdrValidationError(
                f"Moeda de origem do dividendo deve ser 'USD' (recebido: {curr!r})."
            )

        rec_date = _to_date(self.record_date, "record_date")
        pay_abroad = _to_date(self.payment_date_abroad, "payment_date_abroad")
        pay_b3 = _to_date(self.payment_date_b3, "payment_date_b3")

        if pay_abroad < rec_date:
            raise BdrValidationError(
                "Data de pagamento no exterior não pode ser anterior à data de corte (record_date)."
            )
        if pay_b3 < pay_abroad:
            raise BdrValidationError(
                "Data de pagamento na B3 não pode ser anterior à data de pagamento no exterior."
            )

        gross_per_share = _to_decimal(
            self.gross_dividend_per_share_usd, "gross_dividend_per_share_usd"
        )
        if gross_per_share <= Decimal("0"):
            raise BdrValidationError(
                "Dividendo bruto por ação em USD deve ser estritamente positivo."
            )

        shares = _to_exact_int(self.eligible_underlying_shares, "eligible_underlying_shares")
        bdrs = _to_exact_int(self.eligible_bdr_quantity, "eligible_bdr_quantity")
        if shares <= 0 or bdrs <= 0:
            raise BdrValidationError(
                "Quantidades elegíveis de ações-lastro e de BDRs devem ser estritamente positivas."
            )

        ptax = _to_decimal(self.ptax_exchange_rate, "ptax_exchange_rate").quantize(
            FOUR_PLACES, rounding=ROUND_HALF_UP
        )
        if ptax <= Decimal("0"):
            raise BdrValidationError("Taxa de câmbio PTAX deve ser estritamente positiva.")

        wht_rate = _to_decimal(self.withholding_tax_rate, "withholding_tax_rate")
        if wht_rate < Decimal("0") or wht_rate >= Decimal("1"):
            raise BdrValidationError(
                f"Alíquota de Withholding Tax fora do intervalo [0, 1): {wht_rate}"
            )

        iof_rate = _to_decimal(self.iof_fx_rate, "iof_fx_rate")
        if iof_rate < Decimal("0") or iof_rate >= Decimal("1"):
            raise BdrValidationError(
                f"Alíquota de IOF Câmbio fora do intervalo [0, 1): {iof_rate}"
            )

        fee_rate = _to_decimal(self.depositary_fee_rate, "depositary_fee_rate")
        if fee_rate < Decimal("0") or fee_rate >= Decimal("1"):
            raise BdrValidationError(
                f"Taxa da instituição depositária fora do intervalo [0, 1): {fee_rate}"
            )

        gross_usd = (Decimal(shares) * gross_per_share).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        wht_usd = (gross_usd * wht_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        net_usd = (gross_usd - wht_usd).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        converted_brl = (net_usd * ptax).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        iof_brl = (converted_brl * iof_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        after_iof = (converted_brl - iof_brl).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        dep_fee_brl = (after_iof * fee_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        net_brl = (after_iof - dep_fee_brl).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        net_per_bdr = (net_brl / Decimal(bdrs)).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)

        object.__setattr__(self, "event_id", ev_id)
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "record_date", rec_date)
        object.__setattr__(self, "payment_date_abroad", pay_abroad)
        object.__setattr__(self, "payment_date_b3", pay_b3)
        object.__setattr__(self, "currency", curr)
        object.__setattr__(self, "gross_dividend_per_share_usd", gross_per_share)
        object.__setattr__(self, "eligible_underlying_shares", shares)
        object.__setattr__(self, "eligible_bdr_quantity", bdrs)
        object.__setattr__(self, "ptax_exchange_rate", ptax)
        object.__setattr__(self, "withholding_tax_rate", wht_rate)
        object.__setattr__(self, "iof_fx_rate", iof_rate)
        object.__setattr__(self, "depositary_fee_rate", fee_rate)

        object.__setattr__(self, "gross_total_usd", gross_usd)
        object.__setattr__(self, "withholding_tax_usd", wht_usd)
        object.__setattr__(self, "net_foreign_usd", net_usd)
        object.__setattr__(self, "converted_gross_brl", converted_brl)
        object.__setattr__(self, "iof_tax_brl", iof_brl)
        object.__setattr__(self, "after_iof_brl", after_iof)
        object.__setattr__(self, "depositary_fee_brl", dep_fee_brl)
        object.__setattr__(self, "net_total_brl", net_brl)
        object.__setattr__(self, "net_dividend_per_bdr_brl", net_per_bdr)

    def calculate_investor_payout_brl(self, investor_bdr_quantity: Union[int, Decimal]) -> Decimal:
        """Calcula o crédito financeiro líquido em BRL para um comitente detentor de `investor_bdr_quantity`."""
        bdr_qty = _to_exact_int(investor_bdr_quantity, "investor_bdr_quantity")
        if bdr_qty <= 0:
            raise BdrValidationError("Quantidade de BDRs do investidor deve ser > 0.")
        if bdr_qty > self.eligible_bdr_quantity:
            raise BdrValidationError(
                f"Posição do investidor ({bdr_qty}) excede o total elegível do programa "
                f"({self.eligible_bdr_quantity})."
            )
        return (Decimal(bdr_qty) * self.net_dividend_per_bdr_brl).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BdrDividendDistribution:
        """Instancia `BdrDividendDistribution` a partir de dicionário JSON."""
        return cls(
            event_id=data["event_id"],
            ticker=data["ticker"],
            record_date=_to_date(data["record_date"], "record_date"),
            payment_date_abroad=_to_date(data["payment_date_abroad"], "payment_date_abroad"),
            payment_date_b3=_to_date(data["payment_date_b3"], "payment_date_b3"),
            currency=data.get("currency", "USD"),
            gross_dividend_per_share_usd=_to_decimal(
                data["gross_dividend_per_share_usd"], "gross_dividend_per_share_usd"
            ),
            eligible_underlying_shares=data["eligible_underlying_shares"],
            eligible_bdr_quantity=data["eligible_bdr_quantity"],
            withholding_tax_rate=_to_decimal(
                data.get("withholding_tax_rate", DEFAULT_US_WITHHOLDING_TAX_RATE),
                "withholding_tax_rate",
            ),
            ptax_exchange_rate=_to_decimal(data["ptax_exchange_rate"], "ptax_exchange_rate"),
            iof_fx_rate=_to_decimal(
                data.get("iof_fx_rate", DEFAULT_IOF_FX_RATE), "iof_fx_rate"
            ),
            depositary_fee_rate=_to_decimal(
                data.get("depositary_fee_rate", DEFAULT_DEPOSITARY_FEE_RATE),
                "depositary_fee_rate",
            ),
        )

    def to_summary_dict(self) -> Dict[str, Any]:
        """Retorna resumo estruturado do cálculo de dividendos."""
        return {
            "event_id": self.event_id,
            "ticker": self.ticker,
            "record_date": self.record_date.isoformat(),
            "payment_date_abroad": self.payment_date_abroad.isoformat(),
            "payment_date_b3": self.payment_date_b3.isoformat(),
            "currency": self.currency,
            "eligible_underlying_shares": self.eligible_underlying_shares,
            "eligible_bdr_quantity": self.eligible_bdr_quantity,
            "gross_dividend_per_share_usd": f"{self.gross_dividend_per_share_usd:.4f}",
            "gross_total_usd": f"{self.gross_total_usd:.2f}",
            "withholding_tax_rate": f"{self.withholding_tax_rate:.4f}",
            "withholding_tax_usd": f"{self.withholding_tax_usd:.2f}",
            "net_foreign_usd": f"{self.net_foreign_usd:.2f}",
            "ptax_exchange_rate": f"{self.ptax_exchange_rate:.4f}",
            "converted_gross_brl": f"{self.converted_gross_brl:.2f}",
            "iof_fx_rate": f"{self.iof_fx_rate:.4f}",
            "iof_tax_brl": f"{self.iof_tax_brl:.2f}",
            "after_iof_brl": f"{self.after_iof_brl:.2f}",
            "depositary_fee_rate": f"{self.depositary_fee_rate:.4f}",
            "depositary_fee_brl": f"{self.depositary_fee_brl:.2f}",
            "net_total_brl": f"{self.net_total_brl:.2f}",
            "net_dividend_per_bdr_brl": f"{self.net_dividend_per_bdr_brl:.6f}",
        }


# ---------------------------------------------------------------------------
# Motor de Emissão, Cancelamento e Eventos Corporativos de BDRs
# ---------------------------------------------------------------------------

class BdrConversionEngine:
    """Motor determinístico de gestão de programas de BDR, paridade de lastro e dividendos."""

    def __init__(self, programs: Optional[List[BdrProgram]] = None) -> None:
        self._programs: Dict[str, BdrProgram] = {}
        self._conversion_history: List[BdrConversionResult] = []
        self._dividend_history: List[BdrDividendDistribution] = []
        if programs:
            for program in programs:
                self.register_program(program)

    @property
    def programs(self) -> Dict[str, BdrProgram]:
        """Retorna mapa de programas registrados por ticker."""
        return dict(self._programs)

    @property
    def conversion_history(self) -> List[BdrConversionResult]:
        """Retorna histórico imutável de emissões e cancelamentos processados."""
        return list(self._conversion_history)

    @property
    def dividend_history(self) -> List[BdrDividendDistribution]:
        """Retorna histórico de distribuições de dividendos processadas."""
        return list(self._dividend_history)

    def register_program(self, program: BdrProgram) -> None:
        """Registra um programa de BDR após validar a conservação inicial de lastro."""
        program.validate_backing_conservation()
        self._programs[program.ticker] = program

    def get_program(self, ticker: str) -> BdrProgram:
        """Obtém um programa de BDR pelo código de negociação."""
        normalized = str(ticker or "").strip().upper()
        if normalized not in self._programs:
            raise ProgramNotFoundError(
                f"Programa de BDR '{normalized}' não encontrado na Central Depositária B3."
            )
        return self._programs[normalized]

    def process_conversion(self, request: BdrConversionRequest) -> BdrConversionResult:
        """Executa uma solicitação de Emissão (`ISSUANCE`) ou Cancelamento (`CANCELLATION`).

        Garante:
        - Validação de múltiplos exatos de paridade sem fração de ação-lastro.
        - Verificação de saldo suficiente em circulação para cancelamentos.
        - Atualização atômica e verificação da invariante de conservação de lastro.
        """
        program = self.get_program(request.ticker)
        program.validate_backing_conservation()

        shares_qty, bdr_qty = request.resolve_quantities(program)
        prev_shares = program.locked_underlying_shares
        prev_bdrs = program.issued_bdr_quantity

        if request.operation_type == BdrOperationType.ISSUANCE:
            new_shares = prev_shares + shares_qty
            new_bdrs = prev_bdrs + bdr_qty
        elif request.operation_type == BdrOperationType.CANCELLATION:
            if bdr_qty > prev_bdrs or shares_qty > prev_shares:
                raise InsufficientBdrBalanceError(
                    f"Saldo insuficiente para cancelamento no programa {program.ticker} "
                    f"(solicitação {request.request_id}): solicitado cancelar {bdr_qty} BDRs "
                    f"({shares_qty} ações-lastro), mas o saldo em circulação é de "
                    f"{prev_bdrs} BDRs ({prev_shares} ações-lastro bloqueadas)."
                )
            new_shares = prev_shares - shares_qty
            new_bdrs = prev_bdrs - bdr_qty
        else:
            raise BdrValidationError(f"Operação não suportada: {request.operation_type!r}")

        program.locked_underlying_shares = new_shares
        program.issued_bdr_quantity = new_bdrs
        program.validate_backing_conservation()

        result = BdrConversionResult(
            request_id=request.request_id,
            ticker=program.ticker,
            operation_type=request.operation_type,
            participant_code=request.participant_code,
            investor_account=request.investor_account,
            underlying_shares_moved=shares_qty,
            bdr_quantity_moved=bdr_qty,
            previous_locked_shares=prev_shares,
            new_locked_shares=new_shares,
            previous_issued_bdrs=prev_bdrs,
            new_issued_bdrs=new_bdrs,
            swift_reference=request.swift_reference,
            status="SETTLED",
        )
        self._conversion_history.append(result)
        return result

    def process_dividend_distribution(
        self,
        distribution: BdrDividendDistribution,
        *,
        enforce_current_program_balance: bool = True,
    ) -> BdrDividendDistribution:
        """Valida e processa um evento corporativo de dividendo internacional contra o programa."""
        program = self.get_program(distribution.ticker)
        program.validate_backing_conservation()

        expected_bdrs_from_shares = program.shares_to_bdrs(distribution.eligible_underlying_shares)
        if expected_bdrs_from_shares != distribution.eligible_bdr_quantity:
            raise BackingConservationError(
                f"Descasamento de paridade no evento de dividendo {distribution.event_id} "
                f"({program.ticker}): {distribution.eligible_underlying_shares} ações-lastro "
                f"correspondem a {expected_bdrs_from_shares} BDRs, mas o evento declarou "
                f"{distribution.eligible_bdr_quantity} BDRs."
            )

        if enforce_current_program_balance:
            if (
                distribution.eligible_underlying_shares != program.locked_underlying_shares
                or distribution.eligible_bdr_quantity != program.issued_bdr_quantity
            ):
                raise BackingConservationError(
                    f"Posição elegível do evento {distribution.event_id} "
                    f"({distribution.eligible_underlying_shares} ações / "
                    f"{distribution.eligible_bdr_quantity} BDRs) difere do saldo custodiado "
                    f"no programa {program.ticker} ({program.locked_underlying_shares} ações / "
                    f"{program.issued_bdr_quantity} BDRs)."
                )

        self._dividend_history.append(distribution)
        return distribution

    def verify_global_conservation(self) -> bool:
        """Valida a invariante de conservação estrita de lastro em todos os programas registrados."""
        for program in self._programs.values():
            program.validate_backing_conservation()
        return True

    @classmethod
    def load_and_process_fixture(
        cls, fixture_path: Union[str, Path]
    ) -> Tuple[BdrConversionEngine, List[BdrConversionResult], List[BdrDividendDistribution]]:
        """Carrega um lote JSON de programas, conversões e dividendos e processa na sequência."""
        path = Path(fixture_path)
        payload = json.loads(path.read_text(encoding="utf-8"))

        programs = [BdrProgram.from_dict(item) for item in payload.get("programs", [])]
        engine = cls(programs=programs)

        conversion_results: List[BdrConversionResult] = []
        for req_data in payload.get("conversion_requests", []):
            req = BdrConversionRequest.from_dict(req_data)
            conversion_results.append(engine.process_conversion(req))

        dividend_results: List[BdrDividendDistribution] = []
        for div_data in payload.get("dividend_distributions", []):
            dist = BdrDividendDistribution.from_dict(div_data)
            dividend_results.append(engine.process_dividend_distribution(dist))

        engine.verify_global_conservation()
        return engine, conversion_results, dividend_results
