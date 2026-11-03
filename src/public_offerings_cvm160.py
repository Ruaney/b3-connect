"""Motor de Ofertas Públicas CVM 160, Reserva Eletrônica, Bookbuilding e Rateio na B3 (FL-033).

Implementação determinística 100% Python stdlib para o processamento eletrônico
de Ofertas Públicas Primárias e Secundárias sob a Resolução CVM 160/2022 na B3:
- Classificação regulatória de investidores (Profissional, Qualificado, Varejo).
- Modalidades cotadas por Preço Unitário (IPO, Follow-On, FII) ou por Taxa/Spread
  (Debêntures Incentivadas Lei 12.431/14.801, CRI, CRA).
- Formação de livro (Bookbuilding) sob leilão holandês de preço/taxa uniforme.
- Alocação segregada ou unificada por tranches (Varejo vs Institucional).
- Prioridade objetiva de Lock-up e rateio proporcional exato na faixa marginal
  (Método dos Maiores Restos / Hamilton), garantindo conservação estrita do lote
  ofertado sem exceder a quantidade disponível.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union


# ==============================================================================
# 1. Exceções Customizadas
# ==============================================================================


class PublicOfferingError(Exception):
    """Exceção base do módulo de Ofertas Públicas CVM 160 (FL-033)."""


class OfferingValidationError(PublicOfferingError):
    """Erro de validação de parâmetros da oferta ou de intenção de investimento."""


class IneligibleInvestorError(OfferingValidationError):
    """Investidor inelegível para o rito ou público-alvo da oferta CVM 160."""


class BookbuildingExecutionError(PublicOfferingError):
    """Erro durante a apuração do livro de demanda ou rateio de alocações."""


# ==============================================================================
# 2. Enumerações Regulatórias e Operacionais
# ==============================================================================


class InvestorCategory(str, Enum):
    """Classificação regulatória do investidor segundo a CVM (Res. CVM 30 e CVM 160)."""

    PROFESSIONAL = "PROFESSIONAL"  # Investidor Profissional (Art. 11 Res. CVM 30)
    QUALIFIED = "QUALIFIED"        # Investidor Qualificado (Art. 12 Res. CVM 30)
    RETAIL = "RETAIL"              # Investidor Não Institucional / Varejo

    @property
    def is_institutional_eligible(self) -> bool:
        """Indica se o investidor compõe o livro institucional de bookbuilding."""
        return self in (InvestorCategory.PROFESSIONAL, InvestorCategory.QUALIFIED)

    @property
    def is_retail(self) -> bool:
        """Indica se o investidor pertence ao segmento de Varejo (Não Institucional)."""
        return self == InvestorCategory.RETAIL

    @classmethod
    def from_value(cls, value: Any) -> InvestorCategory:
        """Converte strings e sinônimos habituais de mercado para InvestorCategory."""
        if isinstance(value, InvestorCategory):
            return value
        if isinstance(value, str):
            norm = value.strip().upper()
            if norm in ("PROFESSIONAL", "PROFISSIONAL", "INSTITUTIONAL", "INSTITUCIONAL", "PROF", "P"):
                return cls.PROFESSIONAL
            if norm in ("QUALIFIED", "QUALIFICADO", "QUAL", "Q"):
                return cls.QUALIFIED
            if norm in ("RETAIL", "VAREJO", "NAO_INSTITUCIONAL", "NON_INSTITUTIONAL", "RET", "R"):
                return cls.RETAIL
        raise OfferingValidationError(f"Categoria de investidor CVM 160 inválida: {value!r}")


class OfferingType(str, Enum):
    """Modalidade do valor mobiliário objeto da Oferta Pública na B3."""

    IPO = "IPO"                                        # Oferta Pública Inicial de Ações
    FOLLOW_ON = "FOLLOW_ON"                            # Oferta Subsequente de Ações / Units
    FII_QUOTA = "FII_QUOTA"                            # Emissão de Cotas de FII / FI-Infra / FIDC
    DEBENTURE_INCENTIVADA = "DEBENTURE_INCENTIVADA"    # Debêntures Incentivadas (Lei 12.431 / 14.801)
    DEBENTURE_SIMPLES = "DEBENTURE_SIMPLES"            # Debêntures Simples Corporativas
    CRI = "CRI"                                        # Certificados de Recebíveis Imobiliários
    CRA = "CRA"                                        # Certificados de Recebíveis do Agronegócio

    @property
    def is_rate_based(self) -> bool:
        """Retorna True se o bookbuilding competir por menor taxa/spread (% a.a.)."""
        return self in (
            OfferingType.DEBENTURE_INCENTIVADA,
            OfferingType.DEBENTURE_SIMPLES,
            OfferingType.CRI,
            OfferingType.CRA,
        )

    @property
    def is_price_based(self) -> bool:
        """Retorna True se o bookbuilding competir por maior preço unitário (R$)."""
        return not self.is_rate_based

    @classmethod
    def from_value(cls, value: Any) -> OfferingType:
        """Converte representações textuais para OfferingType."""
        if isinstance(value, OfferingType):
            return value
        if isinstance(value, str):
            norm = value.strip().upper().replace("-", "_").replace(" ", "_")
            aliases = {
                "IPO": cls.IPO,
                "FOLLOW_ON": cls.FOLLOW_ON,
                "FOLLOWON": cls.FOLLOW_ON,
                "FII_QUOTA": cls.FII_QUOTA,
                "FII": cls.FII_QUOTA,
                "DEBENTURE_INCENTIVADA": cls.DEBENTURE_INCENTIVADA,
                "DEBENTURE": cls.DEBENTURE_INCENTIVADA,
                "DEBENTURE_SIMPLES": cls.DEBENTURE_SIMPLES,
                "CRI": cls.CRI,
                "CRA": cls.CRA,
            }
            if norm in aliases:
                return aliases[norm]
        raise OfferingValidationError(f"Modalidade de oferta pública inválida: {value!r}")


class CvmRite(str, Enum):
    """Rito de registro da oferta pública sob a Resolução CVM 160."""

    ORDINARY = "ORDINARY"    # Rito de Registro Ordinário (amplo ao público geral)
    AUTOMATIC = "AUTOMATIC"  # Rito de Registro Automático

    @classmethod
    def from_value(cls, value: Any) -> CvmRite:
        if isinstance(value, CvmRite):
            return value
        if isinstance(value, str):
            norm = value.strip().upper()
            if norm in ("ORDINARY", "ORDINARIO", "ORDINÁRIO"):
                return cls.ORDINARY
            if norm in ("AUTOMATIC", "AUTOMATICO", "AUTOMÁTICO"):
                return cls.AUTOMATIC
        raise OfferingValidationError(f"Rito CVM 160 inválido: {value!r}")


class AllocationStatus(str, Enum):
    """Status final de alocação de cada proposta/reserva após o Bookbuilding."""

    FULL = "FULL"                          # Atendimento integral (100% da quantidade solicitada)
    PARTIAL_PRORATA = "PARTIAL_PRORATA"    # Atendimento parcial via rateio proporcional
    CUTOFF_EXCLUDED = "CUTOFF_EXCLUDED"    # Excluída por preço inferior ou taxa superior ao corte
    REJECTED = "REJECTED"                  # Rejeitada por violação regulatória ou de faixa


# ==============================================================================
# 3. Funções Utilitárias de Conversão e Normalização
# ==============================================================================


def _to_decimal(value: Any, field_name: str) -> Decimal:
    """Converte valor numérico/string para Decimal com validação estrita."""
    if isinstance(value, bool) or value is None:
        raise OfferingValidationError(f"Campo decimal inválido '{field_name}': {value!r}")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise OfferingValidationError(f"Campo decimal não finito '{field_name}': {value!r}")
        return value
    if isinstance(value, (int, float, str)):
        try:
            dec = Decimal(str(value).strip())
        except (InvalidOperation, ValueError) as exc:
            raise OfferingValidationError(
                f"Não foi possível converter '{field_name}' para Decimal: {value!r}"
            ) from exc
        if not dec.is_finite():
            raise OfferingValidationError(f"Campo decimal não finito '{field_name}': {value!r}")
        return dec
    raise OfferingValidationError(f"Tipo inválido para '{field_name}': {type(value).__name__}")


def _to_positive_int(value: Any, field_name: str, allow_zero: bool = False) -> int:
    """Valida e converte valor para inteiro positivo (ou não-negativo se allow_zero=True)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise OfferingValidationError(
            f"O campo '{field_name}' deve ser um número inteiro estrito: {value!r}"
        )
    if allow_zero and value < 0:
        raise OfferingValidationError(
            f"O campo '{field_name}' não pode ser negativo: {value!r}"
        )
    if not allow_zero and value <= 0:
        raise OfferingValidationError(
            f"O campo '{field_name}' deve ser estritamente positivo (> 0): {value!r}"
        )
    return value


def _parse_iso_timestamp(value: Any) -> datetime:
    """Converte string ISO-8601 ou objeto datetime em datetime timezone-aware (UTC)."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise OfferingValidationError(f"Timestamp ISO-8601 inválido: {value!r}") from exc
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    raise OfferingValidationError(f"Timestamp inválido: {value!r}")


# ==============================================================================
# 4. Modelos de Dados Tipados (Dataclasses)
# ==============================================================================


@dataclass(frozen=True)
class BookbuildingBid:
    """Proposta institucional ou Pedido de Reserva de varejo em Oferta Pública CVM 160."""

    bid_id: str
    investor_id: str
    participant_id: int
    investor_category: InvestorCategory
    quantity: int
    limit_price_or_rate: Decimal
    lockup_days: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_market_order: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.bid_id, str) or not self.bid_id.strip():
            raise OfferingValidationError("O identificador da proposta ('bid_id') é obrigatório.")
        object.__setattr__(self, "bid_id", self.bid_id.strip())

        if not isinstance(self.investor_id, str) or not self.investor_id.strip():
            raise OfferingValidationError("O identificador do investidor ('investor_id') é obrigatório.")
        object.__setattr__(self, "investor_id", self.investor_id.strip())

        object.__setattr__(
            self,
            "participant_id",
            _to_positive_int(self.participant_id, "participant_id", allow_zero=False),
        )
        object.__setattr__(
            self,
            "investor_category",
            InvestorCategory.from_value(self.investor_category),
        )
        object.__setattr__(
            self,
            "quantity",
            _to_positive_int(self.quantity, "quantity", allow_zero=False),
        )

        dec_price_or_rate = _to_decimal(self.limit_price_or_rate, "limit_price_or_rate")
        if dec_price_or_rate <= Decimal("0"):
            raise OfferingValidationError(
                f"O preço máximo ou taxa mínima ('limit_price_or_rate') deve ser positivo: {dec_price_or_rate}"
            )
        object.__setattr__(self, "limit_price_or_rate", dec_price_or_rate)

        object.__setattr__(
            self,
            "lockup_days",
            _to_positive_int(self.lockup_days, "lockup_days", allow_zero=True),
        )
        object.__setattr__(self, "timestamp", _parse_iso_timestamp(self.timestamp))
        object.__setattr__(self, "is_market_order", bool(self.is_market_order))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BookbuildingBid:
        """Constrói um BookbuildingBid a partir de um dicionário JSON."""
        return cls(
            bid_id=str(data.get("bid_id", "")),
            investor_id=str(data.get("investor_id", "")),
            participant_id=data.get("participant_id", 0),
            investor_category=InvestorCategory.from_value(data.get("investor_category")),
            quantity=data.get("quantity", 0),
            limit_price_or_rate=_to_decimal(
                data.get("limit_price_or_rate"), "limit_price_or_rate"
            ),
            lockup_days=int(data.get("lockup_days", 0))
            if not isinstance(data.get("lockup_days", 0), bool)
            else -1,
            timestamp=_parse_iso_timestamp(
                data.get("timestamp", datetime.now(timezone.utc))
            ),
            is_market_order=bool(data.get("is_market_order", False)),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a proposta para dicionário compatível com JSON."""
        return {
            "bid_id": self.bid_id,
            "investor_id": self.investor_id,
            "participant_id": self.participant_id,
            "investor_category": self.investor_category.value,
            "quantity": self.quantity,
            "limit_price_or_rate": str(self.limit_price_or_rate),
            "lockup_days": self.lockup_days,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "is_market_order": self.is_market_order,
        }


@dataclass(frozen=True)
class AllocationOutcome:
    """Resultado individual de alocação e rateio de uma proposta no Bookbuilding."""

    bid_id: str
    investor_id: str
    participant_id: int
    investor_category: InvestorCategory
    requested_quantity: int
    allocated_quantity: int
    limit_price_or_rate: Decimal
    clearing_price_or_rate: Decimal
    settlement_amount: Decimal
    allocation_ratio: Decimal
    lockup_days: int
    status: AllocationStatus
    tranche: str = "INSTITUTIONAL"
    reason: str = ""

    @property
    def unallocated_quantity(self) -> int:
        """Quantidade cortada/não atendida da proposta."""
        return self.requested_quantity - self.allocated_quantity

    @property
    def is_allocated(self) -> bool:
        """Retorna True se a proposta recebeu pelo menos 1 unidade alocada."""
        return self.allocated_quantity > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bid_id": self.bid_id,
            "investor_id": self.investor_id,
            "participant_id": self.participant_id,
            "investor_category": self.investor_category.value,
            "requested_quantity": self.requested_quantity,
            "allocated_quantity": self.allocated_quantity,
            "unallocated_quantity": self.unallocated_quantity,
            "limit_price_or_rate": str(self.limit_price_or_rate),
            "clearing_price_or_rate": str(self.clearing_price_or_rate),
            "settlement_amount": str(self.settlement_amount),
            "allocation_ratio": str(self.allocation_ratio),
            "lockup_days": self.lockup_days,
            "status": self.status.value,
            "tranche": self.tranche,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BookbuildingSummary:
    """Sumário executivo e trilha de auditoria do encerramento do Bookbuilding CVM 160."""

    offering_id: str
    issuer_name: str
    ticker: str
    isin: str
    offering_type: OfferingType
    cvm_rite: CvmRite
    total_offered_quantity: int
    total_requested_quantity: int
    eligible_requested_quantity: int
    total_allocated_quantity: int
    clearing_price_or_rate: Decimal
    oversubscription_ratio: Decimal
    marginal_prorata_factor: Decimal
    retail_allocated_quantity: int
    institutional_allocated_quantity: int
    total_settlement_amount: Decimal
    outcomes: Tuple[AllocationOutcome, ...]

    @property
    def unallocated_offered_quantity(self) -> int:
        """Saldo remanescente da oferta caso haja sub-subscrição."""
        return self.total_offered_quantity - self.total_allocated_quantity

    @property
    def is_fully_subscribed(self) -> bool:
        """Indica se 100% do lote ofertado foi alocado."""
        return self.total_allocated_quantity == self.total_offered_quantity

    def get_outcome(self, bid_id: str) -> Optional[AllocationOutcome]:
        """Busca o resultado de alocação pelo identificador da proposta."""
        for outcome in self.outcomes:
            if outcome.bid_id == bid_id:
                return outcome
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "offering_id": self.offering_id,
            "issuer_name": self.issuer_name,
            "ticker": self.ticker,
            "isin": self.isin,
            "offering_type": self.offering_type.value,
            "cvm_rite": self.cvm_rite.value,
            "total_offered_quantity": self.total_offered_quantity,
            "total_requested_quantity": self.total_requested_quantity,
            "eligible_requested_quantity": self.eligible_requested_quantity,
            "total_allocated_quantity": self.total_allocated_quantity,
            "unallocated_offered_quantity": self.unallocated_offered_quantity,
            "clearing_price_or_rate": str(self.clearing_price_or_rate),
            "oversubscription_ratio": str(self.oversubscription_ratio),
            "marginal_prorata_factor": str(self.marginal_prorata_factor),
            "retail_allocated_quantity": self.retail_allocated_quantity,
            "institutional_allocated_quantity": self.institutional_allocated_quantity,
            "total_settlement_amount": str(self.total_settlement_amount),
            "is_fully_subscribed": self.is_fully_subscribed,
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


# ==============================================================================
# 5. Motor de Bookbuilding e Alocação de Ofertas Públicas (PublicOfferingEngine)
# ==============================================================================


class PublicOfferingEngine:
    """Motor determinístico para formação de livro, preço de corte e rateio CVM 160."""

    def __init__(
        self,
        offering_id: str,
        issuer_name: str,
        ticker: str,
        isin: str,
        offering_type: Union[OfferingType, str],
        total_offered_quantity: int,
        min_price_or_rate: Union[Decimal, str, int, float],
        max_price_or_rate: Union[Decimal, str, int, float],
        cvm_rite: Union[CvmRite, str] = CvmRite.ORDINARY,
        retail_tranche_pct: Union[Decimal, str, int, float] = Decimal("0.00"),
        lockup_priority_enabled: bool = True,
        unit_nominal_price: Union[Decimal, str, int, float] = Decimal("1000.00"),
        allowed_categories: Optional[Iterable[Union[InvestorCategory, str]]] = None,
    ) -> None:
        if not offering_id or not str(offering_id).strip():
            raise OfferingValidationError("O campo 'offering_id' é obrigatório.")
        if not issuer_name or not str(issuer_name).strip():
            raise OfferingValidationError("O campo 'issuer_name' é obrigatório.")
        if not ticker or not str(ticker).strip():
            raise OfferingValidationError("O campo 'ticker' é obrigatório.")
        if not isin or len(str(isin).strip()) != 12:
            raise OfferingValidationError(f"Código ISIN inválido (esperado 12 caracteres): {isin!r}")

        self.offering_id = str(offering_id).strip()
        self.issuer_name = str(issuer_name).strip()
        self.ticker = str(ticker).strip().upper()
        self.isin = str(isin).strip().upper()
        self.offering_type = OfferingType.from_value(offering_type)
        self.cvm_rite = CvmRite.from_value(cvm_rite)
        self.total_offered_quantity = _to_positive_int(
            total_offered_quantity, "total_offered_quantity", allow_zero=False
        )

        self.min_price_or_rate = _to_decimal(min_price_or_rate, "min_price_or_rate")
        self.max_price_or_rate = _to_decimal(max_price_or_rate, "max_price_or_rate")
        if self.min_price_or_rate <= Decimal("0") or self.max_price_or_rate <= Decimal("0"):
            raise OfferingValidationError("Os limites de preço/taxa da oferta devem ser positivos.")
        if self.min_price_or_rate > self.max_price_or_rate:
            raise OfferingValidationError(
                f"Limite mínimo ({self.min_price_or_rate}) não pode exceder o limite máximo ({self.max_price_or_rate})."
            )

        self.retail_tranche_pct = _to_decimal(retail_tranche_pct, "retail_tranche_pct")
        if self.retail_tranche_pct < Decimal("0") or self.retail_tranche_pct > Decimal("1"):
            raise OfferingValidationError(
                f"O percentual da tranche de varejo deve estar entre 0 e 1: {self.retail_tranche_pct}"
            )

        self.lockup_priority_enabled = bool(lockup_priority_enabled)
        self.unit_nominal_price = _to_decimal(unit_nominal_price, "unit_nominal_price")
        if self.unit_nominal_price <= Decimal("0"):
            raise OfferingValidationError("O PU nominal ('unit_nominal_price') deve ser positivo.")

        if allowed_categories is not None:
            self.allowed_categories = frozenset(
                InvestorCategory.from_value(c) for c in allowed_categories
            )
        else:
            # No rito automático puro sem prospecto/lâmina, se retail_tranche_pct == 0 e
            # rito for AUTOMATIC sem especificação, aceita todas a menos que restringido,
            # mas preservamos flexibilidade explícita.
            self.allowed_categories = frozenset(
                {
                    InvestorCategory.PROFESSIONAL,
                    InvestorCategory.QUALIFIED,
                    InvestorCategory.RETAIL,
                }
            )

        self._bids: List[BookbuildingBid] = []
        self._bid_ids: set[str] = set()

    @classmethod
    def from_json_dict(cls, data: Dict[str, Any]) -> PublicOfferingEngine:
        """Instancia a engine e carrega propostas a partir de um dicionário JSON completo."""
        allowed = data.get("allowed_categories")
        engine = cls(
            offering_id=data["offering_id"],
            issuer_name=data["issuer_name"],
            ticker=data["ticker"],
            isin=data["isin"],
            offering_type=data["offering_type"],
            total_offered_quantity=data["total_offered_quantity"],
            min_price_or_rate=data["min_price_or_rate"],
            max_price_or_rate=data["max_price_or_rate"],
            cvm_rite=data.get("cvm_rite", "ORDINARY"),
            retail_tranche_pct=data.get("retail_tranche_pct", "0.00"),
            lockup_priority_enabled=data.get("lockup_priority_enabled", True),
            unit_nominal_price=data.get("unit_nominal_price", "1000.00"),
            allowed_categories=allowed,
        )
        for bid_dict in data.get("bids", []):
            engine.submit_bid(BookbuildingBid.from_dict(bid_dict))
        return engine

    @classmethod
    def from_json_file(cls, path: Union[str, Path]) -> PublicOfferingEngine:
        """Carrega configuração da oferta e livro de intenções a partir de arquivo JSON."""
        file_path = Path(path)
        with file_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        return cls.from_json_dict(payload)

    @property
    def bids(self) -> Tuple[BookbuildingBid, ...]:
        """Retorna todas as propostas submetidas ao livro."""
        return tuple(self._bids)

    def submit_bid(self, bid: Union[BookbuildingBid, Dict[str, Any]]) -> BookbuildingBid:
        """Registra uma proposta ou pedido de reserva após validações de integridade."""
        parsed_bid = BookbuildingBid.from_dict(bid) if isinstance(bid, dict) else bid
        if not isinstance(parsed_bid, BookbuildingBid):
            raise OfferingValidationError(f"Objeto de proposta inválido: {parsed_bid!r}")

        if parsed_bid.bid_id in self._bid_ids:
            raise OfferingValidationError(
                f"Proposta duplicada no livro de bookbuilding: {parsed_bid.bid_id!r}"
            )

        if parsed_bid.investor_category not in self.allowed_categories:
            raise IneligibleInvestorError(
                f"Categoria {parsed_bid.investor_category.value} não permitida para a oferta {self.offering_id}."
            )

        self._bids.append(parsed_bid)
        self._bid_ids.add(parsed_bid.bid_id)
        return parsed_bid

    # --------------------------------------------------------------------------
    # Regras de Competitividade, Elegibilidade à Faixa e Ordenação Canônica
    # --------------------------------------------------------------------------

    def is_within_offering_band(self, bid: BookbuildingBid) -> Tuple[bool, str]:
        """Verifica se o preço/taxa da proposta é compatível com a faixa da oferta.

        - Em ofertas por PREÇO (IPO/Follow-On/FII): se o preço máximo da proposta for
          inferior ao piso mínimo da oferta (`min_price_or_rate`), ela jamais poderá
          ser atendida.
        - Em ofertas por TAXA (Debêntures/CRI/CRA): se a taxa mínima exigida pelo
          investidor for superior à taxa teto da oferta (`max_price_or_rate`), ela
          jamais poderá ser atendida.
        """
        if bid.is_market_order:
            return True, ""

        if self.offering_type.is_price_based:
            if bid.limit_price_or_rate < self.min_price_or_rate:
                return (
                    False,
                    f"Preço limite ({bid.limit_price_or_rate}) abaixo do piso da faixa ({self.min_price_or_rate}).",
                )
        else:
            if bid.limit_price_or_rate > self.max_price_or_rate:
                return (
                    False,
                    f"Taxa mínima exigida ({bid.limit_price_or_rate}) acima da taxa teto ({self.max_price_or_rate}).",
                )
        return True, ""

    def effective_bid_level(self, bid: BookbuildingBid) -> Decimal:
        """Retorna o nível efetivo de preço ou taxa da proposta dentro da faixa da oferta."""
        if bid.is_market_order:
            return (
                self.max_price_or_rate
                if self.offering_type.is_price_based
                else self.min_price_or_rate
            )
        if self.offering_type.is_price_based:
            return min(bid.limit_price_or_rate, self.max_price_or_rate)
        return max(bid.limit_price_or_rate, self.min_price_or_rate)

    def _sort_key_bookbuilding(self, bid: BookbuildingBid) -> Tuple[Any, ...]:
        """Chave de ordenação determinística do livro de ofertas:
        1. Melhor preço (DESC) ou Melhor taxa (ASC)
        2. Maior prazo de lock-up (se lockup_priority_enabled=True)
        3. Menor timestamp (FIFO)
        4. Menor bid_id (desempate lexicográfico)
        """
        level = self.effective_bid_level(bid)
        price_or_rate_key = -level if self.offering_type.is_price_based else level
        lockup_key = -bid.lockup_days if self.lockup_priority_enabled else 0
        return (price_or_rate_key, lockup_key, bid.timestamp, bid.bid_id)

    def is_eligible_at_clearing(
        self, bid: BookbuildingBid, clearing_level: Decimal
    ) -> bool:
        """Verifica se a proposta aceita o preço/taxa de corte apurado."""
        if bid.is_market_order:
            return True
        if self.offering_type.is_price_based:
            return bid.limit_price_or_rate >= clearing_level
        return bid.limit_price_or_rate <= clearing_level

    def is_strictly_better_than_clearing(
        self, bid: BookbuildingBid, clearing_level: Decimal
    ) -> bool:
        """Verifica se a proposta é estritamente mais competitiva que o nível de corte."""
        level = self.effective_bid_level(bid)
        if self.offering_type.is_price_based:
            return level > clearing_level
        return level < clearing_level

    # --------------------------------------------------------------------------
    # Algoritmo de Rateio Proporcional Exato (Método de Hamilton / Maiores Restos)
    # --------------------------------------------------------------------------

    def _allocate_pro_rata_exact(
        self,
        bids_group: Sequence[BookbuildingBid],
        available_quantity: int,
    ) -> Dict[str, int]:
        """Rateia `available_quantity` proporcionalmente à quantidade demandada em `bids_group`.

        Garante matematicamente:
        - `0 <= allocated[bid_id] <= bid.quantity`
        - `sum(allocated.values()) == min(available_quantity, sum(b.quantity for b in bids_group))`
        - Determinismo absoluto nos arredondamentos de frações via Método dos Maiores Restos.
        """
        if not bids_group or available_quantity <= 0:
            return {b.bid_id: 0 for b in bids_group}

        total_group_demand = sum(b.quantity for b in bids_group)
        if total_group_demand <= available_quantity:
            return {b.bid_id: b.quantity for b in bids_group}

        dec_avail = Decimal(available_quantity)
        dec_demand = Decimal(total_group_demand)

        floor_allocs: Dict[str, int] = {}
        remainders: List[Tuple[Decimal, int, int, datetime, str]] = []

        for bid in bids_group:
            exact_share = (dec_avail * Decimal(bid.quantity)) / dec_demand
            floored = int(exact_share.to_integral_value(rounding=ROUND_DOWN))
            # Garantia defensiva para nunca exceder a quantidade solicitada
            floored = min(floored, bid.quantity)
            remainder = exact_share - Decimal(floored)
            floor_allocs[bid.bid_id] = floored
            lockup_tie = bid.lockup_days if self.lockup_priority_enabled else 0
            remainders.append(
                (remainder, lockup_tie, bid.quantity, bid.timestamp, bid.bid_id)
            )

        residual_units = available_quantity - sum(floor_allocs.values())
        if residual_units > 0:
            # Ordena por:
            # 1. Maior resto fracionário (-remainder)
            # 2. Maior lockup (-lockup_tie)
            # 3. Maior quantidade demandada (-quantity)
            # 4. Menor timestamp (FIFO)
            # 5. Menor bid_id
            remainders.sort(
                key=lambda item: (-item[0], -item[1], -item[2], item[3], item[4])
            )
            bid_map = {b.bid_id: b for b in bids_group}
            idx = 0
            while residual_units > 0 and idx < len(remainders):
                candidate_id = remainders[idx][4]
                if floor_allocs[candidate_id] < bid_map[candidate_id].quantity:
                    floor_allocs[candidate_id] += 1
                    residual_units -= 1
                idx += 1

        return floor_allocs

    def _allocate_waterfall_with_lockup_and_prorata(
        self,
        eligible_bids: Sequence[BookbuildingBid],
        clearing_level: Decimal,
        tranche_capacity: int,
    ) -> Tuple[Dict[str, int], Decimal]:
        """Executa a alocação em cascata (waterfall) para uma tranche ou livro unificado:

        1. Propostas estritamente melhores que o preço/taxa de corte são agrupadas
           por nível de preço/taxa (do mais competitivo ao menos competitivo). Se
           o saldo da tranche comportar o nível inteiro, recebem 100%; caso contrário,
           aplica-se prioridade de lock-up e rateio proporcional dentro daquele nível.
        2. No nível marginal (`level == clearing_level`), se `lockup_priority_enabled`
           estiver ativo, as propostas são agrupadas em escalões decrescentes de
           `lockup_days` (ex: 180d -> 90d -> 0d). Cada escalão é atendido integralmente
           enquanto houver saldo; o escalão onde o saldo se esgota sofre rateio
           proporcional exato (`_allocate_pro_rata_exact`).
        """
        allocations: Dict[str, int] = {b.bid_id: 0 for b in eligible_bids}
        remaining = tranche_capacity
        marginal_prorata_factor = Decimal("1.000000")

        if remaining <= 0 or not eligible_bids:
            return allocations, Decimal("0.000000")

        # Agrupa as propostas elegíveis por (nível de preço/taxa competitivo, escalão de lockup)
        # respeitando a ordem canônica do livro
        ordered = sorted(eligible_bids, key=self._sort_key_bookbuilding)

        # Construímos buckets sequenciais por (level, lockup_tier)
        buckets: List[Tuple[Decimal, int, List[BookbuildingBid]]] = []
        for bid in ordered:
            lvl = self.effective_bid_level(bid)
            lck = bid.lockup_days if self.lockup_priority_enabled else 0
            if not buckets or buckets[-1][0] != lvl or buckets[-1][1] != lck:
                buckets.append((lvl, lck, [bid]))
            else:
                buckets[-1][2].append(bid)

        for lvl, _lck, group in buckets:
            if remaining <= 0:
                break
            group_demand = sum(b.quantity for b in group)
            if group_demand <= remaining:
                for b in group:
                    allocations[b.bid_id] = b.quantity
                remaining -= group_demand
            else:
                # Este bucket é o bucket marginal de corte/rateio!
                marginal_prorata_factor = (
                    Decimal(remaining) / Decimal(group_demand)
                ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
                prorata_map = self._allocate_pro_rata_exact(group, remaining)
                for b_id, qty in prorata_map.items():
                    allocations[b_id] = qty
                remaining = 0
                break

        return allocations, marginal_prorata_factor

    # --------------------------------------------------------------------------
    # Apuração Principal do Bookbuilding (Clearing + Tranches + Rateio)
    # --------------------------------------------------------------------------

    def execute_bookbuilding(self) -> BookbuildingSummary:
        """Executa o procedimento completo de Bookbuilding, apuração do corte e rateio."""
        if not self._bids:
            raise BookbuildingExecutionError(
                f"Não há propostas registradas para a oferta {self.offering_id}."
            )

        valid_bids: List[BookbuildingBid] = []
        rejected_bids: List[Tuple[BookbuildingBid, str]] = []

        for bid in self._bids:
            ok, reason = self.is_within_offering_band(bid)
            if ok:
                valid_bids.append(bid)
            else:
                rejected_bids.append((bid, reason))

        if not valid_bids:
            raise BookbuildingExecutionError(
                f"Nenhuma proposta válida dentro da faixa indicativa da oferta {self.offering_id}."
            )

        # 1. Ordenação global do livro para descoberta do Preço / Taxa de Equilíbrio (Clearing)
        sorted_book = sorted(valid_bids, key=self._sort_key_bookbuilding)
        cumulative_qty = 0
        clearing_level = self.effective_bid_level(sorted_book[-1])

        for bid in sorted_book:
            cumulative_qty += bid.quantity
            if cumulative_qty >= self.total_offered_quantity:
                clearing_level = self.effective_bid_level(bid)
                break
        else:
            # Sub-subscrição: o preço de equilíbrio fica no limite da proposta mais fraca atendida
            # (ou no piso em oferta de preço / teto em oferta de taxa)
            clearing_level = (
                self.min_price_or_rate
                if self.offering_type.is_price_based
                else self.max_price_or_rate
            )

        clearing_level = clearing_level.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 2. Filtra propostas elegíveis no Preço/Taxa de Corte
        eligible_bids: List[BookbuildingBid] = []
        cutoff_excluded_bids: List[BookbuildingBid] = []

        for bid in sorted_book:
            if self.is_eligible_at_clearing(bid, clearing_level):
                eligible_bids.append(bid)
            else:
                cutoff_excluded_bids.append(bid)

        # 3. Alocação entre Tranche de Varejo (Não Institucional) e Institucional
        retail_eligible = [b for b in eligible_bids if b.investor_category.is_retail]
        inst_eligible = [b for b in eligible_bids if not b.investor_category.is_retail]

        alloc_map: Dict[str, int] = {}
        marginal_factors: List[Decimal] = []

        if self.retail_tranche_pct > Decimal("0") and retail_eligible and inst_eligible:
            target_retail_qty = int(
                (Decimal(self.total_offered_quantity) * self.retail_tranche_pct).to_integral_value(
                    rounding=ROUND_DOWN
                )
            )
            retail_demand = sum(b.quantity for b in retail_eligible)
            inst_demand = sum(b.quantity for b in inst_eligible)

            # Se uma tranche não consumir sua reserva, o saldo transborda (clawback/spillover)
            # automaticamente para a outra tranche
            actual_retail_cap = min(target_retail_qty, retail_demand)
            actual_inst_cap = self.total_offered_quantity - actual_retail_cap
            if inst_demand < actual_inst_cap:
                spare_inst = actual_inst_cap - inst_demand
                actual_retail_cap = min(
                    self.total_offered_quantity - inst_demand,
                    actual_retail_cap + spare_inst,
                    retail_demand,
                )
                actual_inst_cap = self.total_offered_quantity - actual_retail_cap

            ret_alloc, ret_factor = self._allocate_waterfall_with_lockup_and_prorata(
                retail_eligible, clearing_level, actual_retail_cap
            )
            inst_alloc, inst_factor = self._allocate_waterfall_with_lockup_and_prorata(
                inst_eligible, clearing_level, actual_inst_cap
            )
            alloc_map.update(ret_alloc)
            alloc_map.update(inst_alloc)
            if ret_factor < Decimal("1.000000"):
                marginal_factors.append(ret_factor)
            if inst_factor < Decimal("1.000000"):
                marginal_factors.append(inst_factor)
        else:
            # Livro único / unificado
            uni_alloc, uni_factor = self._allocate_waterfall_with_lockup_and_prorata(
                eligible_bids, clearing_level, self.total_offered_quantity
            )
            alloc_map.update(uni_alloc)
            if uni_factor < Decimal("1.000000"):
                marginal_factors.append(uni_factor)

        marginal_prorata_factor = (
            min(marginal_factors) if marginal_factors else Decimal("1.000000")
        )

        # 4. Construção dos AllocationOutcomes na ordem original de submissão (ou canônica)
        unit_settlement_price = (
            clearing_level if self.offering_type.is_price_based else self.unit_nominal_price
        )

        outcomes: List[AllocationOutcome] = []
        eligible_ids = {b.bid_id for b in eligible_bids}
        cutoff_ids = {b.bid_id for b in cutoff_excluded_bids}
        rejected_reasons = {b.bid_id: r for b, r in rejected_bids}

        for bid in self._bids:
            tranche_label = "RETAIL" if bid.investor_category.is_retail else "INSTITUTIONAL"
            if bid.bid_id in rejected_reasons:
                outcomes.append(
                    AllocationOutcome(
                        bid_id=bid.bid_id,
                        investor_id=bid.investor_id,
                        participant_id=bid.participant_id,
                        investor_category=bid.investor_category,
                        requested_quantity=bid.quantity,
                        allocated_quantity=0,
                        limit_price_or_rate=bid.limit_price_or_rate,
                        clearing_price_or_rate=clearing_level,
                        settlement_amount=Decimal("0.00"),
                        allocation_ratio=Decimal("0.000000"),
                        lockup_days=bid.lockup_days,
                        status=AllocationStatus.REJECTED,
                        tranche=tranche_label,
                        reason=rejected_reasons[bid.bid_id],
                    )
                )
            elif bid.bid_id in cutoff_ids:
                reason_msg = (
                    f"Preço máximo ofertado ({bid.limit_price_or_rate}) inferior ao preço de corte ({clearing_level})."
                    if self.offering_type.is_price_based
                    else f"Taxa mínima exigida ({bid.limit_price_or_rate}) superior à taxa de corte ({clearing_level})."
                )
                outcomes.append(
                    AllocationOutcome(
                        bid_id=bid.bid_id,
                        investor_id=bid.investor_id,
                        participant_id=bid.participant_id,
                        investor_category=bid.investor_category,
                        requested_quantity=bid.quantity,
                        allocated_quantity=0,
                        limit_price_or_rate=bid.limit_price_or_rate,
                        clearing_price_or_rate=clearing_level,
                        settlement_amount=Decimal("0.00"),
                        allocation_ratio=Decimal("0.000000"),
                        lockup_days=bid.lockup_days,
                        status=AllocationStatus.CUTOFF_EXCLUDED,
                        tranche=tranche_label,
                        reason=reason_msg,
                    )
                )
            elif bid.bid_id in eligible_ids:
                alloc_qty = alloc_map.get(bid.bid_id, 0)
                ratio = (
                    Decimal(alloc_qty) / Decimal(bid.quantity)
                ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
                settlement_val = (
                    Decimal(alloc_qty) * unit_settlement_price
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                if alloc_qty == bid.quantity:
                    status = AllocationStatus.FULL
                    reason_msg = "Atendimento integral no Bookbuilding."
                elif alloc_qty > 0:
                    status = AllocationStatus.PARTIAL_PRORATA
                    reason_msg = "Atendimento parcial via rateio proporcional na faixa marginal."
                else:
                    status = AllocationStatus.CUTOFF_EXCLUDED
                    reason_msg = (
                        "Proposta na faixa marginal preterida por prioridade de lock-up ou esgotamento de lote."
                    )

                outcomes.append(
                    AllocationOutcome(
                        bid_id=bid.bid_id,
                        investor_id=bid.investor_id,
                        participant_id=bid.participant_id,
                        investor_category=bid.investor_category,
                        requested_quantity=bid.quantity,
                        allocated_quantity=alloc_qty,
                        limit_price_or_rate=bid.limit_price_or_rate,
                        clearing_price_or_rate=clearing_level,
                        settlement_amount=settlement_val,
                        allocation_ratio=ratio,
                        lockup_days=bid.lockup_days,
                        status=status,
                        tranche=tranche_label,
                        reason=reason_msg,
                    )
                )

        # 5. Verificação estrita de invariantes de conservação de quantidade
        total_requested = sum(b.quantity for b in self._bids)
        eligible_requested = sum(b.quantity for b in eligible_bids)
        total_allocated = sum(o.allocated_quantity for o in outcomes)
        expected_allocated = min(self.total_offered_quantity, eligible_requested)

        if total_allocated != expected_allocated:
            raise BookbuildingExecutionError(
                f"Quebra de conservação no rateio: alocado={total_allocated}, esperado={expected_allocated}."
            )

        retail_allocated = sum(
            o.allocated_quantity for o in outcomes if o.investor_category.is_retail
        )
        inst_allocated = total_allocated - retail_allocated
        total_settlement = sum(
            (o.settlement_amount for o in outcomes), Decimal("0.00")
        )
        oversubscription = (
            Decimal(total_requested) / Decimal(self.total_offered_quantity)
        ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        return BookbuildingSummary(
            offering_id=self.offering_id,
            issuer_name=self.issuer_name,
            ticker=self.ticker,
            isin=self.isin,
            offering_type=self.offering_type,
            cvm_rite=self.cvm_rite,
            total_offered_quantity=self.total_offered_quantity,
            total_requested_quantity=total_requested,
            eligible_requested_quantity=eligible_requested,
            total_allocated_quantity=total_allocated,
            clearing_price_or_rate=clearing_level,
            oversubscription_ratio=oversubscription,
            marginal_prorata_factor=marginal_prorata_factor,
            retail_allocated_quantity=retail_allocated,
            institutional_allocated_quantity=inst_allocated,
            total_settlement_amount=total_settlement,
            outcomes=tuple(outcomes),
        )


__all__ = [
    "PublicOfferingError",
    "OfferingValidationError",
    "IneligibleInvestorError",
    "BookbuildingExecutionError",
    "InvestorCategory",
    "OfferingType",
    "CvmRite",
    "AllocationStatus",
    "BookbuildingBid",
    "AllocationOutcome",
    "BookbuildingSummary",
    "PublicOfferingEngine",
]
