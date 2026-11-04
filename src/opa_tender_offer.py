"""Leilões Especiais de OPA — Oferta Pública de Aquisição de Ações CVM 85, Tag-Along, Quórum e Rateio (FL-044).

Implementa o motor determinístico da B3 para apuração de Leilões Especiais de OPA no PUMA
Trading System e Central Depositária, em estrita conformidade com a Resolução CVM nº 85/2022
e Lei nº 6.404/1976 (Arts. 4º, 4º-A e 254-A).

Modalidades suportadas:
1. `OPA_FECHAMENTO_CAPITAL` (Cancelamento de Registro):
   - Apuração do quórum legal exato: concordância expressa de titulares de mais de 2/3 das ações habilitadas:
     3 * Q_conc > 2 * Q_hab (em aritmética inteira exata).
   - Verificação de elegibilidade ao Resgate Compulsório / Squeeze-Out (free float remanescente < 5% do total).
2. `OPA_ALIENACAO_CONTROLE` (Tag-Along Art. 254-A):
   - Validação de piso regulatório de preço: 100% do preço do controle no Novo Mercado e Nível 2;
     mínimo de 80% nos demais segmentos (Tradicional, Nível 1, Bovespa Mais).
   - Aquisição de 100% das ações habilitadas para venda pelos minoritários.
3. `OPA_VOLUNTARIA_PARCIAL` (Aquisição Parcial com Rateio Proporcional):
   - Rateio pro-rata determinístico pelo Método dos Maiores Restos (Hamilton).
   - Alocação exata da quantidade ofertada (Target) sem sobras ou arredondamentos indevidos.
4. Invariante de Conservação Física Total de Custódia:
   Soma(Ações Aceitas) + Soma(Ações Devolvidas) == Total de Ações Habilitadas.

100% biblioteca padrão do Python (`dataclasses`, `Decimal`, `datetime`, `enum`, `json`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

TWO_PLACES = Decimal("0.01")


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

class OpaModality(str, Enum):
    OPA_FECHAMENTO_CAPITAL = "OPA_FECHAMENTO_CAPITAL"
    OPA_ALIENACAO_CONTROLE = "OPA_ALIENACAO_CONTROLE"
    OPA_VOLUNTARIA_PARCIAL = "OPA_VOLUNTARIA_PARCIAL"
    OPA_AUMENTO_PARTICIPACAO = "OPA_AUMENTO_PARTICIPACAO"


class CorporateGovernanceSegment(str, Enum):
    NOVO_MERCADO = "NOVO_MERCADO"
    NIVEL_2 = "NIVEL_2"
    NIVEL_1 = "NIVEL_1"
    BOVESPA_MAIS = "BOVESPA_MAIS"
    TRADICIONAL = "TRADICIONAL"

    @property
    def tag_along_minimum_ratio(self) -> Decimal:
        """Percentual regulatório mínimo de Tag-Along (Art. 254-A / Regulamentos B3)."""
        if self in (CorporateGovernanceSegment.NOVO_MERCADO, CorporateGovernanceSegment.NIVEL_2):
            return Decimal("1.00")
        return Decimal("0.80")


class ShareholderOpaStance(str, Enum):
    TENDER_SELL = "TENDER_SELL"
    AGREE_NO_SALE = "AGREE_NO_SALE"
    DISSENT_HOLD = "DISSENT_HOLD"


class OpaAuctionStatus(str, Enum):
    HOMOLOGATED = "HOMOLOGATED"
    QUORUM_REJECTED = "QUORUM_REJECTED"
    MIN_ACCEPTANCE_NOT_MET = "MIN_ACCEPTANCE_NOT_MET"


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class OpaTenderOfferError(Exception):
    """Exceção base para leilões de OPA."""


class OpaValidationError(OpaTenderOfferError):
    """Erro de validação cadastral ou regulatória de OPA."""


class TagAlongPriceFloorViolationError(OpaValidationError):
    """Preço ofertado na OPA de Alienação de Controle inferior ao piso regulatório de Tag-Along."""


class OpaAuctionNotFoundError(OpaTenderOfferError):
    """Leilão de OPA não localizado."""


class OpaConservationInvariantError(OpaTenderOfferError):
    """Violação da invariante de conservação de ações."""


# ---------------------------------------------------------------------------
# Entidades de Domínio
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShareholderTenderOrder:
    order_id: str
    shareholder_id: str
    participant_code: int
    custody_account: str
    stance: ShareholderOpaStance
    enabled_quantity: int
    blocked_in_opa_portfolio: bool = True
    timestamp: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ShareholderTenderOrder:
        return cls(
            order_id=str(d["order_id"]),
            shareholder_id=str(d["shareholder_id"]),
            participant_code=int(d["participant_code"]),
            custody_account=str(d["custody_account"]),
            stance=ShareholderOpaStance(d["stance"]),
            enabled_quantity=int(d["enabled_quantity"]),
            blocked_in_opa_portfolio=bool(d.get("blocked_in_opa_portfolio", True)),
            timestamp=str(d.get("timestamp", "")),
        )


@dataclass(frozen=True)
class OpaAllocationResult:
    order_id: str
    shareholder_id: str
    participant_code: int
    custody_account: str
    stance: ShareholderOpaStance
    enabled_quantity: int
    accepted_quantity: int
    returned_quantity: int
    financial_amount_brl: Decimal

    @property
    def is_conserved(self) -> bool:
        return (self.accepted_quantity + self.returned_quantity) == self.enabled_quantity


@dataclass
class OpaAuctionConfig:
    auction_id: str
    company_name: str
    offeror_name: str
    ticker: str
    isin: str
    modality: OpaModality
    governance_segment: CorporateGovernanceSegment
    offer_price_per_share: Decimal
    control_block_price_per_share: Optional[Decimal]
    total_issued_shares: int
    total_free_float_shares: int
    max_target_quantity: Optional[int]
    min_acceptance_quantity: int
    auction_date: date
    settlement_date: date
    orders: List[ShareholderTenderOrder] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> OpaAuctionConfig:
        orders = [ShareholderTenderOrder.from_dict(o) for o in d.get("orders", [])]
        return cls(
            auction_id=str(d["auction_id"]),
            company_name=str(d["company_name"]),
            offeror_name=str(d["offeror_name"]),
            ticker=str(d["ticker"]).upper(),
            isin=str(d["isin"]).upper(),
            modality=OpaModality(d["modality"]),
            governance_segment=CorporateGovernanceSegment(d["governance_segment"]),
            offer_price_per_share=Decimal(str(d["offer_price_per_share"])),
            control_block_price_per_share=(
                Decimal(str(d["control_block_price_per_share"]))
                if d.get("control_block_price_per_share") is not None
                else None
            ),
            total_issued_shares=int(d["total_issued_shares"]),
            total_free_float_shares=int(d["total_free_float_shares"]),
            max_target_quantity=(
                int(d["max_target_quantity"])
                if d.get("max_target_quantity") is not None
                else None
            ),
            min_acceptance_quantity=int(d.get("min_acceptance_quantity", 0)),
            auction_date=_to_date(d["auction_date"], "auction_date"),
            settlement_date=_to_date(d["settlement_date"], "settlement_date"),
            orders=orders,
        )


@dataclass(frozen=True)
class OpaAuctionSummary:
    auction_id: str
    ticker: str
    modality: OpaModality
    governance_segment: CorporateGovernanceSegment
    offer_price_per_share: Decimal
    status: OpaAuctionStatus
    quorum_achieved: bool
    squeeze_out_eligible: bool
    total_enabled_shares: int
    total_accepted_shares: int
    total_returned_shares: int
    total_financial_volume_brl: Decimal
    pro_rata_factor: Decimal
    is_conservation_satisfied: bool
    allocations: List[OpaAllocationResult]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "auction_id": self.auction_id,
            "ticker": self.ticker,
            "modality": self.modality.value,
            "governance_segment": self.governance_segment.value,
            "offer_price_per_share": str(self.offer_price_per_share),
            "status": self.status.value,
            "quorum_achieved": self.quorum_achieved,
            "squeeze_out_eligible": self.squeeze_out_eligible,
            "total_enabled_shares": self.total_enabled_shares,
            "total_accepted_shares": self.total_accepted_shares,
            "total_returned_shares": self.total_returned_shares,
            "total_financial_volume_brl": str(self.total_financial_volume_brl),
            "pro_rata_factor": str(self.pro_rata_factor),
            "is_conservation_satisfied": self.is_conservation_satisfied,
            "allocations": [
                {
                    "order_id": a.order_id,
                    "shareholder_id": a.shareholder_id,
                    "stance": a.stance.value,
                    "enabled_quantity": a.enabled_quantity,
                    "accepted_quantity": a.accepted_quantity,
                    "returned_quantity": a.returned_quantity,
                    "financial_amount_brl": str(a.financial_amount_brl),
                }
                for a in self.allocations
            ],
        }


# ---------------------------------------------------------------------------
# Motor de Leilões Especiais de OPA (OpaTenderOfferEngine)
# ---------------------------------------------------------------------------

class OpaTenderOfferEngine:
    """Motor determinístico da B3 para apuração de Leilões Especiais de OPA."""

    def __init__(self) -> None:
        self._auctions: Dict[str, OpaAuctionConfig] = {}
        self._summaries: Dict[str, OpaAuctionSummary] = {}

    @property
    def auctions(self) -> Dict[str, OpaAuctionConfig]:
        return self._auctions

    @property
    def summaries(self) -> Dict[str, OpaAuctionSummary]:
        return self._summaries

    def register_auction(self, config: OpaAuctionConfig) -> OpaAuctionConfig:
        self._auctions[config.auction_id] = config
        return config

    def get_auction(self, auction_id: str) -> OpaAuctionConfig:
        if auction_id not in self._auctions:
            raise OpaAuctionNotFoundError(f"Leilão de OPA '{auction_id}' não encontrado.")
        return self._auctions[auction_id]

    def run_auction(self, auction_id: str) -> OpaAuctionSummary:
        cfg = self.get_auction(auction_id)

        # 1. Validação de piso regulatório de Tag-Along
        if cfg.modality == OpaModality.OPA_ALIENACAO_CONTROLE:
            if cfg.control_block_price_per_share is None:
                raise OpaValidationError(
                    f"OPA de Alienação de Controle '{cfg.auction_id}' requer 'control_block_price_per_share'."
                )
            ratio = cfg.governance_segment.tag_along_minimum_ratio
            min_price = _quantize_money(cfg.control_block_price_per_share * ratio)
            if cfg.offer_price_per_share < min_price:
                raise TagAlongPriceFloorViolationError(
                    f"Preço ofertado R$ {cfg.offer_price_per_share} abaixo do piso regulatório de Tag-Along "
                    f"R$ {min_price} ({int(ratio * 100)}% de R$ {cfg.control_block_price_per_share}) no segmento {cfg.governance_segment.value}."
                )

        total_hab = sum(o.enabled_quantity for o in cfg.orders)
        q_sell = sum(o.enabled_quantity for o in cfg.orders if o.stance == ShareholderOpaStance.TENDER_SELL)
        q_agree = sum(o.enabled_quantity for o in cfg.orders if o.stance == ShareholderOpaStance.AGREE_NO_SALE)
        q_dissent = sum(o.enabled_quantity for o in cfg.orders if o.stance == ShareholderOpaStance.DISSENT_HOLD)
        q_conc = q_sell + q_agree

        allocations: List[OpaAllocationResult] = []
        status = OpaAuctionStatus.HOMOLOGATED
        quorum_achieved = False
        squeeze_out_eligible = False
        pro_rata_factor = Decimal("1.00000000")

        if cfg.modality == OpaModality.OPA_FECHAMENTO_CAPITAL:
            # Condição exata: 3 * Q_conc > 2 * Q_hab
            if total_hab > 0 and (3 * q_conc > 2 * total_hab):
                quorum_achieved = True
                status = OpaAuctionStatus.HOMOLOGATED
                # Aceita 100% de TENDER_SELL, devolve AGREE e DISSENT
                for o in cfg.orders:
                    if o.stance == ShareholderOpaStance.TENDER_SELL:
                        accepted = o.enabled_quantity
                        returned = 0
                        fin = _quantize_money(Decimal(accepted) * cfg.offer_price_per_share)
                    else:
                        accepted = 0
                        returned = o.enabled_quantity
                        fin = Decimal("0.00")
                    allocations.append(
                        OpaAllocationResult(
                            order_id=o.order_id,
                            shareholder_id=o.shareholder_id,
                            participant_code=o.participant_code,
                            custody_account=o.custody_account,
                            stance=o.stance,
                            enabled_quantity=o.enabled_quantity,
                            accepted_quantity=accepted,
                            returned_quantity=returned,
                            financial_amount_brl=fin,
                        )
                    )

                # Verifica Squeeze-Out: free float remanescente < 5% do total emitido
                remaining_ff = cfg.total_free_float_shares - q_sell
                if remaining_ff < (Decimal("0.05") * Decimal(cfg.total_issued_shares)):
                    squeeze_out_eligible = True
            else:
                quorum_achieved = False
                status = OpaAuctionStatus.QUORUM_REJECTED
                # Devolve 100% de todas as ordens
                for o in cfg.orders:
                    allocations.append(
                        OpaAllocationResult(
                            order_id=o.order_id,
                            shareholder_id=o.shareholder_id,
                            participant_code=o.participant_code,
                            custody_account=o.custody_account,
                            stance=o.stance,
                            enabled_quantity=o.enabled_quantity,
                            accepted_quantity=0,
                            returned_quantity=o.enabled_quantity,
                            financial_amount_brl=Decimal("0.00"),
                        )
                    )

        elif cfg.modality == OpaModality.OPA_VOLUNTARIA_PARCIAL:
            max_target = cfg.max_target_quantity or q_sell
            if q_sell < cfg.min_acceptance_quantity:
                status = OpaAuctionStatus.MIN_ACCEPTANCE_NOT_MET
                for o in cfg.orders:
                    allocations.append(
                        OpaAllocationResult(
                            order_id=o.order_id,
                            shareholder_id=o.shareholder_id,
                            participant_code=o.participant_code,
                            custody_account=o.custody_account,
                            stance=o.stance,
                            enabled_quantity=o.enabled_quantity,
                            accepted_quantity=0,
                            returned_quantity=o.enabled_quantity,
                            financial_amount_brl=Decimal("0.00"),
                        )
                    )
            elif q_sell <= max_target:
                status = OpaAuctionStatus.HOMOLOGATED
                for o in cfg.orders:
                    accepted = o.enabled_quantity
                    fin = _quantize_money(Decimal(accepted) * cfg.offer_price_per_share)
                    allocations.append(
                        OpaAllocationResult(
                            order_id=o.order_id,
                            shareholder_id=o.shareholder_id,
                            participant_code=o.participant_code,
                            custody_account=o.custody_account,
                            stance=o.stance,
                            enabled_quantity=o.enabled_quantity,
                            accepted_quantity=accepted,
                            returned_quantity=0,
                            financial_amount_brl=fin,
                        )
                    )
            else:
                # Oversubscription: Rateio Proporcional pelo Método dos Maiores Restos
                status = OpaAuctionStatus.HOMOLOGATED
                pro_rata_factor = (Decimal(max_target) / Decimal(q_sell)).quantize(
                    Decimal("0.00000001"), rounding=ROUND_HALF_UP
                )

                floored_list: List[int] = []
                remainders: List[Tuple[int, int, str, int]] = []
                for idx, o in enumerate(cfg.orders):
                    prod = max_target * o.enabled_quantity
                    floor_q = prod // q_sell
                    rem_num = prod % q_sell
                    floored_list.append(floor_q)
                    remainders.append((rem_num, o.enabled_quantity, o.order_id, idx))

                residual = max_target - sum(floored_list)
                remainders.sort(key=lambda item: (-item[0], -item[1], item[2]))
                for i in range(residual):
                    winner_idx = remainders[i][3]
                    floored_list[winner_idx] += 1

                for o, accepted in zip(cfg.orders, floored_list):
                    returned = o.enabled_quantity - accepted
                    fin = _quantize_money(Decimal(accepted) * cfg.offer_price_per_share)
                    allocations.append(
                        OpaAllocationResult(
                            order_id=o.order_id,
                            shareholder_id=o.shareholder_id,
                            participant_code=o.participant_code,
                            custody_account=o.custody_account,
                            stance=o.stance,
                            enabled_quantity=o.enabled_quantity,
                            accepted_quantity=accepted,
                            returned_quantity=returned,
                            financial_amount_brl=fin,
                        )
                    )

        else:
            # OPA_ALIENACAO_CONTROLE ou OPA_AUMENTO_PARTICIPACAO: aceita 100% de TENDER_SELL
            status = OpaAuctionStatus.HOMOLOGATED
            for o in cfg.orders:
                if o.stance == ShareholderOpaStance.TENDER_SELL:
                    accepted = o.enabled_quantity
                    returned = 0
                    fin = _quantize_money(Decimal(accepted) * cfg.offer_price_per_share)
                else:
                    accepted = 0
                    returned = o.enabled_quantity
                    fin = Decimal("0.00")
                allocations.append(
                    OpaAllocationResult(
                        order_id=o.order_id,
                        shareholder_id=o.shareholder_id,
                        participant_code=o.participant_code,
                        custody_account=o.custody_account,
                        stance=o.stance,
                        enabled_quantity=o.enabled_quantity,
                        accepted_quantity=accepted,
                        returned_quantity=returned,
                        financial_amount_brl=fin,
                    )
                )

        tot_accepted = sum(a.accepted_quantity for a in allocations)
        tot_returned = sum(a.returned_quantity for a in allocations)
        tot_fin = _quantize_money(sum(a.financial_amount_brl for a in allocations))
        conserved = (tot_accepted + tot_returned == total_hab) and all(
            a.is_conserved for a in allocations
        )

        if not conserved:
            raise OpaConservationInvariantError(
                f"Violação de conservação no leilão '{cfg.auction_id}': "
                f"habilitado={total_hab} != aceito({tot_accepted}) + devolvido({tot_returned})."
            )

        summary = OpaAuctionSummary(
            auction_id=cfg.auction_id,
            ticker=cfg.ticker,
            modality=cfg.modality,
            governance_segment=cfg.governance_segment,
            offer_price_per_share=cfg.offer_price_per_share,
            status=status,
            quorum_achieved=quorum_achieved,
            squeeze_out_eligible=squeeze_out_eligible,
            total_enabled_shares=total_hab,
            total_accepted_shares=tot_accepted,
            total_returned_shares=tot_returned,
            total_financial_volume_brl=tot_fin,
            pro_rata_factor=pro_rata_factor,
            is_conservation_satisfied=conserved,
            allocations=allocations,
        )
        self._summaries[cfg.auction_id] = summary
        return summary

    def run_all(self) -> List[OpaAuctionSummary]:
        return [self.run_auction(aid) for aid in self._auctions]

    def summarize(self) -> Dict[str, Any]:
        summaries = self.run_all()
        return {
            "total_auctions": len(summaries),
            "all_conservations_satisfied": all(s.is_conservation_satisfied for s in summaries),
            "total_financial_volume_brl": str(
                _quantize_money(sum(s.total_financial_volume_brl for s in summaries))
            ),
            "summaries": [s.to_dict() for s in summaries],
        }

    portfolio_summary = summarize

    @classmethod
    def from_json_fixture(cls, path: Union[str, Path]) -> OpaTenderOfferEngine:
        fixture_path = Path(path)
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        engine = cls()
        for a_data in raw.get("auctions", []):
            engine.register_auction(OpaAuctionConfig.from_dict(a_data))
        return engine


__all__ = [
    "CorporateGovernanceSegment",
    "OpaAllocationResult",
    "OpaAuctionConfig",
    "OpaAuctionNotFoundError",
    "OpaAuctionStatus",
    "OpaAuctionSummary",
    "OpaConservationInvariantError",
    "OpaModality",
    "OpaTenderOfferEngine",
    "OpaTenderOfferError",
    "OpaValidationError",
    "ShareholderOpaStance",
    "ShareholderTenderOrder",
    "TagAlongPriceFloorViolationError",
]
