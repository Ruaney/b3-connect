"""Leilão Especial de Deslistagem / OPA por Fechamento de Capital e Tag Along (FL-070).

Implementa o motor determinístico e validador de Leilões Especiais de OPA para
Cancelamento de Registro de Companhia Aberta (Deslistagem da B3) e Alienação de Controle
com Tag Along, em estrita conformidade com:
- Lei nº 6.404/1976 (Lei das Sociedades por Ações):
  * Art. 4º, § 4º: Oferta pública por preço justo para cancelamento de registro.
  * Art. 4º, § 5º: Resgate compulsório / Squeeze-Out se free float remanescente < 5% (controle > 95%).
  * Art. 4º-A: Apuração estrita do quórum de concordância de mais de 2/3 das ações habilitadas.
  * Art. 254-A: Garantia de Tag Along (80% a 100% do preço do bloco de controle).
- Resolução CVM nº 85/2022 e Resolução CVM nº 166/2022:
  * Procedimentos operacionais para ofertas públicas de aquisição de ações (OPAs).
  * Habilitação e bloqueio mandatório prévio de custódia na Central Depositária B3.
  * Unificação de OPA de fechamento de capital com alienação de controle e Tag Along.
  * Mecanismo determinístico de rateio de sobras pelo Método dos Maiores Restos (Hamilton).

Leis de Invariância e Conservação:
1. Invariante de Quórum Legal de 2/3 (Art. 4º-A):
   3 * Q_concordantes > 2 * Q_habilitadas em aritmética inteira exata.
   Se 3 * Q_conc <= 2 * Q_hab, o fechamento é rejeitado e 100% das ações são devolvidas.
2. Invariante do Piso Regulatório de Tag Along (Art. 254-A):
   Preço_Ofertado >= Preço_Controle * Piso_Tag_Along (100% no Novo Mercado e Nível 2;
   mínimo de 80% nos demais segmentos ou estatutário).
3. Invariante do Resgate Compulsório / Squeeze-Out (Art. 4º, § 5º):
   Se ações pós-leilão do controlador >= 95% do capital emitido (Free Float <= 5%),
   aciona o resgate determinístico de 100% das ações remanescentes ao preço da OPA.
4. Invariante de Conservação Absoluta de Custódia e Financeira:
   Para cada ordem i: Q_habilitada_i == Q_aceita_i + Q_devolvida_i + Q_squeeze_out_i.
   Débito_Total_Ofertante == Soma(Créditos_Investidores) centavo a centavo em Decimal (0.01).

100% biblioteca padrão Python (dataclasses, Decimal, datetime, enum, json, pathlib).
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
FOUR_PLACES = Decimal("0.0001")
EIGHT_PLACES = Decimal("0.00000001")


def _quantize_money(val: Union[Decimal, str, int, float]) -> Decimal:
    """Quantiza valores monetários em BRL para 2 casas decimais (ROUND_HALF_UP)."""
    return Decimal(str(val)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _quantize_rate(val: Union[Decimal, str, int, float]) -> Decimal:
    """Quantiza taxas e percentuais para 8 casas decimais (ROUND_HALF_UP)."""
    return Decimal(str(val)).quantize(EIGHT_PLACES, rounding=ROUND_HALF_UP)


def _to_date(val: Any, field_name: str) -> date:
    """Converte valor para date."""
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val.strip()[:10])
    raise ValueError(f"Campo '{field_name}' deve ser uma data válida.")


# ---------------------------------------------------------------------------
# Enumerações Tipadas
# ---------------------------------------------------------------------------

class CorporateGovernanceSegment(str, Enum):
    """Segmentos de listagem e governança corporativa da B3."""
    NOVO_MERCADO = "NOVO_MERCADO"
    NIVEL_2 = "NIVEL_2"
    NIVEL_1 = "NIVEL_1"
    BOVESPA_MAIS = "BOVESPA_MAIS"
    TRADICIONAL = "TRADICIONAL"

    @property
    def tag_along_minimum_ratio(self) -> Decimal:
        """Piso regulatório legal/regulamentar de Tag Along (Art. 254-A da Lei 6.404/76 e Regulamentos B3)."""
        if self in (CorporateGovernanceSegment.NOVO_MERCADO, CorporateGovernanceSegment.NIVEL_2):
            return Decimal("1.00")
        return Decimal("0.80")


class DelistingOpaModality(str, Enum):
    """Modalidades de OPA de Fechamento de Capital e Deslistagem."""
    FECHAMENTO_CAPITAL = "FECHAMENTO_CAPITAL"
    FECHAMENTO_COM_ALIENACAO_CONTROLE = "FECHAMENTO_COM_ALIENACAO_CONTROLE"
    DESLISTAGEM_VOLUNTARIA_CONCORRENTE = "DESLISTAGEM_VOLUNTARIA_CONCORRENTE"


class ShareholderDelistingStance(str, Enum):
    """Postura declarada do acionista minoritário habilitado no leilão de OPA."""
    TENDER_SELL = "TENDER_SELL"          # Concorda com a deslistagem e vende suas ações no leilão
    AGREE_NO_SALE = "AGREE_NO_SALE"      # Concorda com o cancelamento de registro, mas retém suas ações
    DISSENT_HOLD = "DISSENT_HOLD"        # Discorda do cancelamento de registro e retém suas ações


class DelistingAuctionStatus(str, Enum):
    """Status final de liquidação do leilão especial de OPA de deslistagem."""
    HOMOLOGATED_SUCCESS = "HOMOLOGATED_SUCCESS"
    REJECTED_INSUFFICIENT_QUORUM = "REJECTED_INSUFFICIENT_QUORUM"
    REJECTED_TAG_ALONG_VIOLATION = "REJECTED_TAG_ALONG_VIOLATION"
    REJECTED_MIN_ACCEPTANCE_NOT_MET = "REJECTED_MIN_ACCEPTANCE_NOT_MET"


class SqueezeOutAction(str, Enum):
    """Resultado da apuração de Squeeze-Out (Resgate Compulsório - Art. 4º, § 5º)."""
    EXECUTED = "EXECUTED"                      # Controle >= 95% e resgate compulsório total executado
    ELIGIBLE_NOT_REQUESTED = "ELIGIBLE_NOT_REQUESTED"  # Controle >= 95%, elegível mas não executado
    NOT_ELIGIBLE = "NOT_ELIGIBLE"              # Controle < 95% (free float remanescente > 5%)


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------

class DelistingOpaError(Exception):
    """Exceção base para o fluxo de OPA de Deslistagem e Tag Along."""


class DelistingValidationError(DelistingOpaError):
    """Erro de validação cadastral ou parâmetros da OPA."""


class TagAlongFloorViolationError(DelistingValidationError):
    """Preço ofertado viola o piso regulatório de Tag Along (Art. 254-A / Regulamentos B3)."""


class CustodyNotBlockedError(DelistingValidationError):
    """Ações habilitadas sem bloqueio mandatário na carteira de OPA da Depositária B3."""


class DelistingConservationError(DelistingOpaError):
    """Violação matemática da lei de conservação de ações ou balanço financeiro."""


class DelistingAuctionNotFoundError(DelistingOpaError):
    """Leilão de OPA não encontrado no motor."""


# ---------------------------------------------------------------------------
# Entidades de Domínio
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DelistingShareholderOrder:
    """Ordem individual de manifestação/habilitação de acionista no leilão."""
    order_id: str
    shareholder_id: str
    participant_code: int
    custody_account: str
    stance: ShareholderDelistingStance
    enabled_quantity: int
    blocked_in_custody: bool = True
    timestamp: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> DelistingShareholderOrder:
        return cls(
            order_id=str(d["order_id"]),
            shareholder_id=str(d["shareholder_id"]),
            participant_code=int(d["participant_code"]),
            custody_account=str(d["custody_account"]),
            stance=ShareholderDelistingStance(d["stance"]),
            enabled_quantity=int(d["enabled_quantity"]),
            blocked_in_custody=bool(d.get("blocked_in_custody", True)),
            timestamp=str(d.get("timestamp", "")),
        )


@dataclass(frozen=True)
class DelistingAllocationLine:
    """Resultado individual de liquidação e alocação por acionista habilitado."""
    order_id: str
    shareholder_id: str
    participant_code: int
    custody_account: str
    stance: ShareholderDelistingStance
    enabled_quantity: int
    tender_accepted_quantity: int
    returned_quantity: int
    squeeze_out_redeemed_quantity: int
    tender_financial_brl: Decimal
    squeeze_out_financial_brl: Decimal
    total_financial_brl: Decimal

    @property
    def total_disposed_quantity(self) -> int:
        return self.tender_accepted_quantity + self.squeeze_out_redeemed_quantity

    @property
    def is_conserved(self) -> bool:
        """Conservação física de ações: habilitadas == aceitas + devolvidas + resgatadas."""
        return (
            self.tender_accepted_quantity
            + self.returned_quantity
            + self.squeeze_out_redeemed_quantity
        ) == self.enabled_quantity


@dataclass(frozen=True)
class UnregisteredFreeFloatSqueezeOutDeposit:
    """Depósito em custódia fiduciária/escrow para ações do free float não habilitadas."""
    unregistered_quantity: int
    deposit_financial_brl: Decimal
    escrow_agent_or_bank: str = "BANCO_LIQUIDANTE_B3_ESCROW"


@dataclass
class DelistingAuctionConfig:
    """Configuração cadastral e parâmetros regulatórios do leilão especial de OPA."""
    auction_id: str
    company_name: str
    ticker: str
    isin: str
    offeror_name: str
    modality: DelistingOpaModality
    governance_segment: CorporateGovernanceSegment
    offer_price_per_share: Decimal
    control_block_price_per_share: Optional[Decimal]
    statutory_tag_along_ratio: Optional[Decimal]
    total_issued_shares: int
    initial_controller_shares: int
    initial_free_float_shares: int
    execute_squeeze_out: bool
    max_target_quantity: Optional[int]
    min_acceptance_quantity: int
    auction_date: date
    settlement_date: date
    orders: List[DelistingShareholderOrder] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> DelistingAuctionConfig:
        orders = [DelistingShareholderOrder.from_dict(o) for o in d.get("orders", [])]
        return cls(
            auction_id=str(d["auction_id"]),
            company_name=str(d["company_name"]),
            ticker=str(d["ticker"]).upper(),
            isin=str(d["isin"]).upper(),
            offeror_name=str(d["offeror_name"]),
            modality=DelistingOpaModality(d["modality"]),
            governance_segment=CorporateGovernanceSegment(d["governance_segment"]),
            offer_price_per_share=Decimal(str(d["offer_price_per_share"])),
            control_block_price_per_share=(
                Decimal(str(d["control_block_price_per_share"]))
                if d.get("control_block_price_per_share") is not None
                else None
            ),
            statutory_tag_along_ratio=(
                Decimal(str(d["statutory_tag_along_ratio"]))
                if d.get("statutory_tag_along_ratio") is not None
                else None
            ),
            total_issued_shares=int(d["total_issued_shares"]),
            initial_controller_shares=int(d["initial_controller_shares"]),
            initial_free_float_shares=int(d["initial_free_float_shares"]),
            execute_squeeze_out=bool(d.get("execute_squeeze_out", False)),
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
class DelistingAuctionSummary:
    """Relatório exaustivo de apuração do leilão especial de deslistagem."""
    auction_id: str
    company_name: str
    ticker: str
    isin: str
    modality: DelistingOpaModality
    governance_segment: CorporateGovernanceSegment
    offer_price_per_share: Decimal
    status: DelistingAuctionStatus
    delisting_approved: bool
    quorum_achieved: bool
    tag_along_floor_price: Optional[Decimal]
    tag_along_respected: bool
    total_enabled_shares: int
    total_sell_shares: int
    total_agree_shares: int
    total_dissent_shares: int
    total_concordant_shares: int
    concordance_ratio: Decimal
    concordance_condition_satisfied: bool
    post_auction_controller_shares: int
    post_auction_controller_ratio: Decimal
    post_auction_free_float_shares: int
    post_auction_free_float_ratio: Decimal
    squeeze_out_status: SqueezeOutAction
    squeeze_out_eligible: bool
    squeeze_out_shares_from_orders: int
    squeeze_out_unregistered_shares: int
    total_accepted_shares: int
    total_returned_shares: int
    total_financial_tender_brl: Decimal
    total_financial_squeeze_out_brl: Decimal
    total_financial_disbursed_brl: Decimal
    pro_rata_factor: Decimal
    is_conservation_satisfied: bool
    allocations: List[DelistingAllocationLine]
    unregistered_squeeze_out: Optional[UnregisteredFreeFloatSqueezeOutDeposit]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "auction_id": self.auction_id,
            "company_name": self.company_name,
            "ticker": self.ticker,
            "isin": self.isin,
            "modality": self.modality.value,
            "governance_segment": self.governance_segment.value,
            "offer_price_per_share": str(self.offer_price_per_share),
            "status": self.status.value,
            "delisting_approved": self.delisting_approved,
            "quorum_achieved": self.quorum_achieved,
            "tag_along_floor_price": str(self.tag_along_floor_price) if self.tag_along_floor_price is not None else None,
            "tag_along_respected": self.tag_along_respected,
            "total_enabled_shares": self.total_enabled_shares,
            "total_sell_shares": self.total_sell_shares,
            "total_agree_shares": self.total_agree_shares,
            "total_dissent_shares": self.total_dissent_shares,
            "total_concordant_shares": self.total_concordant_shares,
            "concordance_ratio": str(self.concordance_ratio),
            "concordance_condition_satisfied": self.concordance_condition_satisfied,
            "post_auction_controller_shares": self.post_auction_controller_shares,
            "post_auction_controller_ratio": str(self.post_auction_controller_ratio),
            "post_auction_free_float_shares": self.post_auction_free_float_shares,
            "post_auction_free_float_ratio": str(self.post_auction_free_float_ratio),
            "squeeze_out_status": self.squeeze_out_status.value,
            "squeeze_out_eligible": self.squeeze_out_eligible,
            "squeeze_out_shares_from_orders": self.squeeze_out_shares_from_orders,
            "squeeze_out_unregistered_shares": self.squeeze_out_unregistered_shares,
            "total_accepted_shares": self.total_accepted_shares,
            "total_returned_shares": self.total_returned_shares,
            "total_financial_tender_brl": str(self.total_financial_tender_brl),
            "total_financial_squeeze_out_brl": str(self.total_financial_squeeze_out_brl),
            "total_financial_disbursed_brl": str(self.total_financial_disbursed_brl),
            "pro_rata_factor": str(self.pro_rata_factor),
            "is_conservation_satisfied": self.is_conservation_satisfied,
            "allocations": [
                {
                    "order_id": a.order_id,
                    "shareholder_id": a.shareholder_id,
                    "stance": a.stance.value,
                    "enabled_quantity": a.enabled_quantity,
                    "tender_accepted_quantity": a.tender_accepted_quantity,
                    "returned_quantity": a.returned_quantity,
                    "squeeze_out_redeemed_quantity": a.squeeze_out_redeemed_quantity,
                    "tender_financial_brl": str(a.tender_financial_brl),
                    "squeeze_out_financial_brl": str(a.squeeze_out_financial_brl),
                    "total_financial_brl": str(a.total_financial_brl),
                }
                for a in self.allocations
            ],
            "unregistered_squeeze_out": (
                {
                    "unregistered_quantity": self.unregistered_squeeze_out.unregistered_quantity,
                    "deposit_financial_brl": str(self.unregistered_squeeze_out.deposit_financial_brl),
                    "escrow_agent_or_bank": self.unregistered_squeeze_out.escrow_agent_or_bank,
                }
                if self.unregistered_squeeze_out is not None
                else None
            ),
        }


# ---------------------------------------------------------------------------
# Motor de Leilões Especiais de Deslistagem (DelistingOpaAuctionEngine)
# ---------------------------------------------------------------------------

class DelistingOpaAuctionEngine:
    """Motor determinístico e validador de Leilões Especiais de Deslistagem e Tag Along."""

    def __init__(self) -> None:
        self._auctions: Dict[str, DelistingAuctionConfig] = {}
        self._summaries: Dict[str, DelistingAuctionSummary] = {}

    @property
    def auctions(self) -> Dict[str, DelistingAuctionConfig]:
        return self._auctions

    @property
    def summaries(self) -> Dict[str, DelistingAuctionSummary]:
        return self._summaries

    def register_auction(self, config: DelistingAuctionConfig) -> DelistingAuctionConfig:
        """Registra a configuração do leilão no catálogo em memória."""
        self.validate_auction_config(config)
        self._auctions[config.auction_id] = config
        return config

    def get_auction(self, auction_id: str) -> DelistingAuctionConfig:
        """Retorna a configuração cadastrada de um leilão."""
        if auction_id not in self._auctions:
            raise DelistingAuctionNotFoundError(f"Leilão de Deslistagem '{auction_id}' não encontrado.")
        return self._auctions[auction_id]

    def calculate_tag_along_floor(self, config: DelistingAuctionConfig) -> Tuple[Decimal, Decimal]:
        """Calcula o percentual regulatório efetivo e o preço piso de Tag Along.

        Returns:
            Tuple[Decimal, Decimal]: (effective_ratio, floor_price)
        """
        if config.control_block_price_per_share is None:
            raise DelistingValidationError(
                f"Leilão '{config.auction_id}' requer 'control_block_price_per_share' para apuração de Tag Along."
            )
        base_ratio = config.governance_segment.tag_along_minimum_ratio
        statutory_ratio = config.statutory_tag_along_ratio or Decimal("0.00")
        effective_ratio = max(base_ratio, statutory_ratio)
        floor_price = _quantize_money(config.control_block_price_per_share * effective_ratio)
        return effective_ratio, floor_price

    def validate_auction_config(self, config: DelistingAuctionConfig) -> None:
        """Valida a consistência jurídica e matemática dos parâmetros da OPA."""
        if config.total_issued_shares <= 0:
            raise DelistingValidationError(f"Total de ações emitidas ({config.total_issued_shares}) deve ser positivo.")

        if (config.initial_controller_shares + config.initial_free_float_shares) != config.total_issued_shares:
            raise DelistingValidationError(
                f"Soma de controle ({config.initial_controller_shares}) e free float ({config.initial_free_float_shares}) "
                f"diverge do total de ações emitidas ({config.total_issued_shares})."
            )

        if config.offer_price_per_share <= Decimal("0.00"):
            raise DelistingValidationError(f"Preço ofertado ({config.offer_price_per_share}) deve ser estritamente positivo.")

        # Validação do piso de Tag Along
        if (
            config.modality == DelistingOpaModality.FECHAMENTO_COM_ALIENACAO_CONTROLE
            or config.control_block_price_per_share is not None
        ):
            _, floor_price = self.calculate_tag_along_floor(config)
            if config.offer_price_per_share < floor_price:
                raise TagAlongFloorViolationError(
                    f"Preço ofertado R$ {config.offer_price_per_share} está abaixo do piso de Tag Along "
                    f"R$ {floor_price} para o segmento {config.governance_segment.value}."
                )

        # Validação de bloqueio em custódia para cada ordem
        for o in config.orders:
            if not o.blocked_in_custody:
                raise CustodyNotBlockedError(
                    f"Ordem '{o.order_id}' possui ações não bloqueadas na carteira de custódia da B3."
                )
            if o.enabled_quantity <= 0:
                raise DelistingValidationError(
                    f"Ordem '{o.order_id}' possui quantidade habilitada não positiva: {o.enabled_quantity}."
                )

    def run_auction(self, auction_id: str) -> DelistingAuctionSummary:
        """Executa a apuração determinística completa do leilão especial de OPA."""
        cfg = self.get_auction(auction_id)
        self.validate_auction_config(cfg)

        # 1. Totalização das manifestações dos acionistas
        total_hab = sum(o.enabled_quantity for o in cfg.orders)
        q_sell = sum(o.enabled_quantity for o in cfg.orders if o.stance == ShareholderDelistingStance.TENDER_SELL)
        q_agree = sum(o.enabled_quantity for o in cfg.orders if o.stance == ShareholderDelistingStance.AGREE_NO_SALE)
        q_dissent = sum(o.enabled_quantity for o in cfg.orders if o.stance == ShareholderDelistingStance.DISSENT_HOLD)
        q_conc = q_sell + q_agree

        concordance_ratio = (
            (Decimal(q_conc) / Decimal(total_hab)).quantize(EIGHT_PLACES, rounding=ROUND_HALF_UP)
            if total_hab > 0
            else Decimal("0.00000000")
        )

        tag_along_floor_price: Optional[Decimal] = None
        tag_along_respected = True
        if cfg.control_block_price_per_share is not None:
            _, tag_along_floor_price = self.calculate_tag_along_floor(cfg)
            tag_along_respected = (cfg.offer_price_per_share >= tag_along_floor_price)

        # 2. Apuração do Quórum Legal de 2/3 (Art. 4º-A da Lei 6.404/76)
        # Mais de 2/3 das ações habilitadas: 3 * Q_conc > 2 * Q_hab em inteiros exatos
        is_quorum_achieved = (total_hab > 0) and (3 * q_conc > 2 * total_hab)
        min_acceptance_met = (q_sell >= cfg.min_acceptance_quantity)

        allocations: List[DelistingAllocationLine] = []
        pro_rata_factor = Decimal("1.00000000")

        # -------------------------------------------------------------------
        # CENÁRIO A: Quórum Rejeitado ou Aceitação Mínima Não Atingida
        # -------------------------------------------------------------------
        if not is_quorum_achieved or not min_acceptance_met:
            delisting_approved = False
            status = (
                DelistingAuctionStatus.REJECTED_MIN_ACCEPTANCE_NOT_MET
                if is_quorum_achieved and not min_acceptance_met
                else DelistingAuctionStatus.REJECTED_INSUFFICIENT_QUORUM
            )

            # Devolve 100% de todas as ações habilitadas
            for o in cfg.orders:
                allocations.append(
                    DelistingAllocationLine(
                        order_id=o.order_id,
                        shareholder_id=o.shareholder_id,
                        participant_code=o.participant_code,
                        custody_account=o.custody_account,
                        stance=o.stance,
                        enabled_quantity=o.enabled_quantity,
                        tender_accepted_quantity=0,
                        returned_quantity=o.enabled_quantity,
                        squeeze_out_redeemed_quantity=0,
                        tender_financial_brl=Decimal("0.00"),
                        squeeze_out_financial_brl=Decimal("0.00"),
                        total_financial_brl=Decimal("0.00"),
                    )
                )

            summary = DelistingAuctionSummary(
                auction_id=cfg.auction_id,
                company_name=cfg.company_name,
                ticker=cfg.ticker,
                isin=cfg.isin,
                modality=cfg.modality,
                governance_segment=cfg.governance_segment,
                offer_price_per_share=cfg.offer_price_per_share,
                status=status,
                delisting_approved=False,
                quorum_achieved=is_quorum_achieved,
                tag_along_floor_price=tag_along_floor_price,
                tag_along_respected=tag_along_respected,
                total_enabled_shares=total_hab,
                total_sell_shares=q_sell,
                total_agree_shares=q_agree,
                total_dissent_shares=q_dissent,
                total_concordant_shares=q_conc,
                concordance_ratio=concordance_ratio,
                concordance_condition_satisfied=is_quorum_achieved,
                post_auction_controller_shares=cfg.initial_controller_shares,
                post_auction_controller_ratio=(
                    Decimal(cfg.initial_controller_shares) / Decimal(cfg.total_issued_shares)
                ).quantize(EIGHT_PLACES, rounding=ROUND_HALF_UP),
                post_auction_free_float_shares=cfg.initial_free_float_shares,
                post_auction_free_float_ratio=(
                    Decimal(cfg.initial_free_float_shares) / Decimal(cfg.total_issued_shares)
                ).quantize(EIGHT_PLACES, rounding=ROUND_HALF_UP),
                squeeze_out_status=SqueezeOutAction.NOT_ELIGIBLE,
                squeeze_out_eligible=False,
                squeeze_out_shares_from_orders=0,
                squeeze_out_unregistered_shares=0,
                total_accepted_shares=0,
                total_returned_shares=total_hab,
                total_financial_tender_brl=Decimal("0.00"),
                total_financial_squeeze_out_brl=Decimal("0.00"),
                total_financial_disbursed_brl=Decimal("0.00"),
                pro_rata_factor=pro_rata_factor,
                is_conservation_satisfied=True,
                allocations=allocations,
                unregistered_squeeze_out=None,
            )
            self._summaries[cfg.auction_id] = summary
            return summary

        # -------------------------------------------------------------------
        # CENÁRIO B: Quórum Aprovado (> 2/3) — Processamento e Alocação
        # -------------------------------------------------------------------
        delisting_approved = True
        status = DelistingAuctionStatus.HOMOLOGATED_SUCCESS

        # Determina o lote a ser aceito (com ou sem rateio)
        max_target = cfg.max_target_quantity if cfg.max_target_quantity is not None else q_sell
        needs_pro_rata = (cfg.max_target_quantity is not None) and (q_sell > cfg.max_target_quantity)

        # Mapa de alocações de compra da OPA
        order_tender_accepted: Dict[str, int] = {}
        order_returned_temp: Dict[str, int] = {}

        if not needs_pro_rata:
            # Sem rateio: aceita 100% de TENDER_SELL
            for o in cfg.orders:
                if o.stance == ShareholderDelistingStance.TENDER_SELL:
                    order_tender_accepted[o.order_id] = o.enabled_quantity
                    order_returned_temp[o.order_id] = 0
                else:
                    order_tender_accepted[o.order_id] = 0
                    order_returned_temp[o.order_id] = o.enabled_quantity
        else:
            # Com rateio proporcional determinístico: Método dos Maiores Restos (Hamilton)
            target = min(q_sell, max_target)
            pro_rata_factor = (Decimal(target) / Decimal(q_sell)).quantize(EIGHT_PLACES, rounding=ROUND_HALF_UP)

            selling_orders = [o for o in cfg.orders if o.stance == ShareholderDelistingStance.TENDER_SELL]
            floored_shares: List[int] = []
            remainders: List[Tuple[int, int, str, int]] = []

            for idx, o in enumerate(selling_orders):
                prod = target * o.enabled_quantity
                fl = prod // q_sell
                rem = prod % q_sell
                floored_shares.append(fl)
                remainders.append((rem, o.enabled_quantity, o.order_id, idx))

            residual = target - sum(floored_shares)
            # Ordena decrescente por maior resto, maior quantidade habilitada, e desempate por order_id estável
            remainders.sort(key=lambda item: (-item[0], -item[1], item[2]))
            for i in range(residual):
                winner_idx = remainders[i][3]
                floored_shares[winner_idx] += 1

            for o, accepted in zip(selling_orders, floored_shares):
                order_tender_accepted[o.order_id] = accepted
                order_returned_temp[o.order_id] = o.enabled_quantity - accepted

            # Ordens de AGREE_NO_SALE e DISSENT_HOLD não vendem no leilão
            for o in cfg.orders:
                if o.stance != ShareholderDelistingStance.TENDER_SELL:
                    order_tender_accepted[o.order_id] = 0
                    order_returned_temp[o.order_id] = o.enabled_quantity

        tot_accepted_tender = sum(order_tender_accepted.values())

        # 3. Apuração Pós-Leilão de Controle e Free Float
        post_controller_shares = cfg.initial_controller_shares + tot_accepted_tender
        post_free_float_shares = cfg.total_issued_shares - post_controller_shares

        controller_ratio = (
            Decimal(post_controller_shares) / Decimal(cfg.total_issued_shares)
        ).quantize(EIGHT_PLACES, rounding=ROUND_HALF_UP)
        free_float_ratio = (
            Decimal(post_free_float_shares) / Decimal(cfg.total_issued_shares)
        ).quantize(EIGHT_PLACES, rounding=ROUND_HALF_UP)

        # 4. Apuração e Execução de Squeeze-Out (Art. 4º, § 5º da Lei 6.404/76)
        # Condição legal: se o controlador passar a deter mais de 95% do total emitido
        # Em aritmética inteira exata: post_free_float_shares * 20 <= total_issued_shares (<= 5% free float)
        is_squeeze_out_eligible = (post_free_float_shares * 20 <= cfg.total_issued_shares)

        squeeze_out_action = SqueezeOutAction.NOT_ELIGIBLE
        squeeze_out_unreg_deposit: Optional[UnregisteredFreeFloatSqueezeOutDeposit] = None
        tot_squeeze_shares_from_orders = 0
        tot_unregistered_squeeze_shares = 0

        order_squeeze_redeemed: Dict[str, int] = {}
        order_final_returned: Dict[str, int] = {}

        if is_squeeze_out_eligible:
            if cfg.execute_squeeze_out:
                squeeze_out_action = SqueezeOutAction.EXECUTED

                # Resgata compulsoriamente todas as ações remanescentes das ordens habilitadas
                for o in cfg.orders:
                    rem_shares = order_returned_temp.get(o.order_id, 0)
                    order_squeeze_redeemed[o.order_id] = rem_shares
                    order_final_returned[o.order_id] = 0
                    tot_squeeze_shares_from_orders += rem_shares

                # Verifica se há ações no free float que sequer foram habilitadas no leilão
                unregistered_ff = cfg.initial_free_float_shares - total_hab
                if unregistered_ff > 0:
                    tot_unregistered_squeeze_shares = unregistered_ff
                    unreg_fin = _quantize_money(Decimal(unregistered_ff) * cfg.offer_price_per_share)
                    squeeze_out_unreg_deposit = UnregisteredFreeFloatSqueezeOutDeposit(
                        unregistered_quantity=unregistered_ff,
                        deposit_financial_brl=unreg_fin,
                        escrow_agent_or_bank="BANCO_LIQUIDANTE_B3_ESCROW",
                    )
            else:
                squeeze_out_action = SqueezeOutAction.ELIGIBLE_NOT_REQUESTED
                for o in cfg.orders:
                    order_squeeze_redeemed[o.order_id] = 0
                    order_final_returned[o.order_id] = order_returned_temp[o.order_id]
        else:
            squeeze_out_action = SqueezeOutAction.NOT_ELIGIBLE
            for o in cfg.orders:
                order_squeeze_redeemed[o.order_id] = 0
                order_final_returned[o.order_id] = order_returned_temp[o.order_id]

        # 5. Montagem das linhas de alocação e cálculo financeiro com centavos em Decimal
        for o in cfg.orders:
            accepted = order_tender_accepted[o.order_id]
            sq_redeemed = order_squeeze_redeemed[o.order_id]
            returned = order_final_returned[o.order_id]

            fin_tender = _quantize_money(Decimal(accepted) * cfg.offer_price_per_share)
            fin_squeeze = _quantize_money(Decimal(sq_redeemed) * cfg.offer_price_per_share)
            fin_total = fin_tender + fin_squeeze

            allocations.append(
                DelistingAllocationLine(
                    order_id=o.order_id,
                    shareholder_id=o.shareholder_id,
                    participant_code=o.participant_code,
                    custody_account=o.custody_account,
                    stance=o.stance,
                    enabled_quantity=o.enabled_quantity,
                    tender_accepted_quantity=accepted,
                    returned_quantity=returned,
                    squeeze_out_redeemed_quantity=sq_redeemed,
                    tender_financial_brl=fin_tender,
                    squeeze_out_financial_brl=fin_squeeze,
                    total_financial_brl=fin_total,
                )
            )

        # 6. Verificação Formal de Invariantes
        tot_accepted = sum(a.tender_accepted_quantity for a in allocations)
        tot_returned = sum(a.returned_quantity for a in allocations)
        tot_squeeze = sum(a.squeeze_out_redeemed_quantity for a in allocations)

        fin_tender_total = sum((a.tender_financial_brl for a in allocations), Decimal("0.00"))
        fin_squeeze_orders = sum((a.squeeze_out_financial_brl for a in allocations), Decimal("0.00"))
        fin_unreg_squeeze = (
            squeeze_out_unreg_deposit.deposit_financial_brl
            if squeeze_out_unreg_deposit is not None
            else Decimal("0.00")
        )
        fin_squeeze_total = fin_squeeze_orders + fin_unreg_squeeze
        fin_disbursed_total = fin_tender_total + fin_squeeze_total

        # Invariante de conservação estrita
        conserved_lines = all(a.is_conserved for a in allocations)
        conserved_total = (tot_accepted + tot_returned + tot_squeeze) == total_hab

        if not (conserved_lines and conserved_total):
            raise DelistingConservationError(
                f"Violação da lei de conservação de ações no leilão '{cfg.auction_id}': "
                f"Total Habilitado={total_hab} != Aceito({tot_accepted}) + Devolvido({tot_returned}) + Resgatado({tot_squeeze})."
            )

        summary = DelistingAuctionSummary(
            auction_id=cfg.auction_id,
            company_name=cfg.company_name,
            ticker=cfg.ticker,
            isin=cfg.isin,
            modality=cfg.modality,
            governance_segment=cfg.governance_segment,
            offer_price_per_share=cfg.offer_price_per_share,
            status=status,
            delisting_approved=delisting_approved,
            quorum_achieved=is_quorum_achieved,
            tag_along_floor_price=tag_along_floor_price,
            tag_along_respected=tag_along_respected,
            total_enabled_shares=total_hab,
            total_sell_shares=q_sell,
            total_agree_shares=q_agree,
            total_dissent_shares=q_dissent,
            total_concordant_shares=q_conc,
            concordance_ratio=concordance_ratio,
            concordance_condition_satisfied=is_quorum_achieved,
            post_auction_controller_shares=post_controller_shares,
            post_auction_controller_ratio=controller_ratio,
            post_auction_free_float_shares=post_free_float_shares,
            post_auction_free_float_ratio=free_float_ratio,
            squeeze_out_status=squeeze_out_action,
            squeeze_out_eligible=is_squeeze_out_eligible,
            squeeze_out_shares_from_orders=tot_squeeze_shares_from_orders,
            squeeze_out_unregistered_shares=tot_unregistered_squeeze_shares,
            total_accepted_shares=tot_accepted,
            total_returned_shares=tot_returned,
            total_financial_tender_brl=fin_tender_total,
            total_financial_squeeze_out_brl=fin_squeeze_total,
            total_financial_disbursed_brl=fin_disbursed_total,
            pro_rata_factor=pro_rata_factor,
            is_conservation_satisfied=True,
            allocations=allocations,
            unregistered_squeeze_out=squeeze_out_unreg_deposit,
        )
        self._summaries[cfg.auction_id] = summary
        return summary

    def run_all(self) -> List[DelistingAuctionSummary]:
        """Executa todos os leilões cadastrados no motor."""
        return [self.run_auction(aid) for aid in self._auctions]

    def summarize(self) -> Dict[str, Any]:
        """Retorna resumo executivo de todos os leilões processados."""
        summaries = self.run_all()
        return {
            "total_auctions": len(summaries),
            "all_conservations_satisfied": all(s.is_conservation_satisfied for s in summaries),
            "all_delistings_approved": all(s.delisting_approved for s in summaries),
            "total_financial_disbursed_brl": str(
                _quantize_money(sum((s.total_financial_disbursed_brl for s in summaries), Decimal("0.00")))
            ),
            "summaries": [s.to_dict() for s in summaries],
        }

    @classmethod
    def from_json_fixture(cls, path: Union[str, Path]) -> DelistingOpaAuctionEngine:
        """Carrega e instancia o motor a partir de arquivo de fixture JSON."""
        fixture_path = Path(path)
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        engine = cls()
        for a_data in raw.get("auctions", []):
            engine.register_auction(DelistingAuctionConfig.from_dict(a_data))
        return engine


__all__ = [
    "CorporateGovernanceSegment",
    "CustodyNotBlockedError",
    "DelistingAllocationLine",
    "DelistingAuctionConfig",
    "DelistingAuctionNotFoundError",
    "DelistingAuctionStatus",
    "DelistingAuctionSummary",
    "DelistingConservationError",
    "DelistingOpaAuctionEngine",
    "DelistingOpaError",
    "DelistingOpaModality",
    "DelistingShareholderOrder",
    "DelistingValidationError",
    "ShareholderDelistingStance",
    "SqueezeOutAction",
    "TagAlongFloorViolationError",
    "UnregisteredFreeFloatSqueezeOutDeposit",
]
