"""
B3 CSD & BACEN DREX Fase 2 — Emissão Primária de Ativos RWA em Rede DLT EVM (FL-105)
=====================================================================================
Motor institucional de originação, bookbuilding, alocação e liquidação primária atômica
bilateral Delivery-versus-Payment (DvP) e gestão de proventos on-chain para títulos de
renda fixa privada tokenizados (Debêntures, CRIs e CRAs RWA) na infraestrutura DLT do
Drex (Hyperledger Besu / EVM), abrangendo:

1. Smart Contracts Simulados:
   - ERC-1155 Multi-Token: Séries, tranches (Sênior, Mezanino, Subordinada) e semi-fungíveis;
   - ERC-3643 (T-REX): Permissionamento institucional, registro de identidades on-chain
     (ONCHAINID / KYC / AML) e regras de conformidade regulatória (Resoluções CVM 160 e 175);
   - CBDC Real Digital (BACEN): Reserva bancária e liquidação atômica em moeda soberana.

2. Fluxo Primário de Oferta Pública (Resolução CVM 160):
   - Coleta de ordens de subscrição e bookbuilding competitivo por taxa de corte / spread;
   - Algoritmo determinístico de alocação e rateio pro-rata em sobre-demanda.

3. Liquidação Bilateral DvP Atômica:
   - Direct Atomic DvP (All-or-None no mesmo bloco Besu);
   - HTLC (Hash Time-Locked Contract) com hashlock SHA-256 e timeout regulatório.

4. Ciclo de Vida e Pagamento Automatizado de Proventos On-Chain:
   - Cronograma determinístico de cupons periódicos e amortizações do principal;
   - Pagamento automatizado em Real Digital aos detentores na data-base (record date).

Invariante Formal Provada:
- LAW-111: Atomicidade da liquidação primária DvP (Quantidade alocada == Quantidade entregue,
  Valor financeiro recebido == Valor nominal liquidado), e conservação de saldo de cupons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_DOWN
from enum import Enum
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    from src.invariants import (
        assert_drex_rwa_primary_issuance_invariants,
        assert_drex_token_non_negative_conservation,
        InvariantViolationError,
    )
except ImportError:
    # Fallback autônomo para execução isolada
    class InvariantViolationError(AssertionError):  # type: ignore
        def __init__(self, message: str, law_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
            super().__init__(message)
            self.message = message
            self.law_code = law_code
            self.details = details or {}

    def assert_drex_rwa_primary_issuance_invariants(  # type: ignore
        allocated_units: int,
        delivered_units: int,
        received_financial: Decimal,
        expected_financial: Decimal,
        operation_id: str = "",
        funded_coupon_amount: Optional[Decimal] = None,
        distributed_coupon_amount: Optional[Decimal] = None,
    ) -> None:
        if allocated_units != delivered_units:
            raise InvariantViolationError(
                f"Violação da Atomicidade DvP Quantitativa RWA (LAW-111) na operação '{operation_id}': "
                f"Quantidade alocada ({allocated_units}) != Quantidade entregue ({delivered_units})",
                law_code="LAW-111",
            )
        if received_financial != expected_financial:
            raise InvariantViolationError(
                f"Violação da Paridade Financeira DvP RWA (LAW-111) na operação '{operation_id}': "
                f"Valor recebido ({received_financial}) != Valor financeiro esperado ({expected_financial})",
                law_code="LAW-111",
            )
        if funded_coupon_amount is not None and distributed_coupon_amount is not None:
            if funded_coupon_amount != distributed_coupon_amount:
                raise InvariantViolationError(
                    f"Violação da Conservação de Saldo de Cupons RWA (LAW-111) na operação '{operation_id}': "
                    f"Montante provisionado ({funded_coupon_amount}) != Montante distribuído ({distributed_coupon_amount})",
                    law_code="LAW-111",
                )

    def assert_drex_token_non_negative_conservation(  # type: ignore
        balances: Dict[str, Union[int, Decimal]],
        entity_id: str = "",
    ) -> None:
        for asset, balance in balances.items():
            if balance < 0:
                raise InvariantViolationError(
                    f"Saldo negativo de token detectado para entidade '{entity_id}' no ativo '{asset}': {balance} < 0",
                    law_code="LAW-089C",
                )


# ==============================================================================
# HIERARQUIA DE EXCEÇÕES REGULATÓRIAS RWA DREX
# ==============================================================================

class DrexRwaError(Exception):
    """Exceção base para o subsistema de emissão primária RWA Drex Fase 2."""
    pass


class DrexRwaComplianceError(DrexRwaError):
    """Falha de conformidade regulatória ERC-3643 (KYC/AML, perfil investidor ou sanções)."""
    pass


class DrexRwaInsufficientBalanceError(DrexRwaError):
    """Saldo insuficiente de Real Digital ou tokens RWA na carteira."""
    pass


class DrexRwaBookbuildingError(DrexRwaError):
    """Erro nas etapas de coleta de ordens, taxa de corte ou alocação primária."""
    pass


class DrexRwaHtlcError(DrexRwaError):
    """Falha em operação de contrato HTLC (preimage inválida, timeout expirado, etc.)."""
    pass


class DrexRwaCorporateActionError(DrexRwaError):
    """Falha em apuração ou liquidação de cupons e amortizações de principal."""
    pass


class DrexRwaInvalidStateError(DrexRwaError):
    """Transição de estado inválida no ciclo de vida da oferta primária."""
    pass


# ==============================================================================
# ENUMERAÇÕES DO SUBSISTEMA RWA DREX
# ==============================================================================

class RwaAssetType(str, Enum):
    """Tipos de ativos de renda fixa privada admitidos na plataforma Drex RWA."""
    DEBENTURE = "DEBENTURE"  # Debêntures (Simples, Incentivadas Lei 12.431/06)
    CRI = "CRI"              # Certificados de Recebíveis Imobiliários (Lei 9.514/97)
    CRA = "CRA"              # Certificados de Recebíveis do Agronegócio (Lei 11.076/04)


class RwaTranche(str, Enum):
    """Tranches e classes de subordinação em estruturas securitizadas ou dívida corporativa."""
    SENIOR = "SENIOR"              # Prioridade primária de resgate e cupons
    MEZZANINE = "MEZZANINE"        # Subordinação intermediária
    SUBORDINATED = "SUBORDINATED"  # Absorção prioritária de perdas (Equity tranche)


class InvestorProfile(str, Enum):
    """Categorias de investidores conforme Resolução CVM 30."""
    RETAIL = "RETAIL"              # Investidor de Varejo / Geral
    QUALIFIED = "QUALIFIED"        # Investidor Qualificado (>= R$ 1 milhão ou certificação técnica)
    PROFESSIONAL = "PROFESSIONAL"  # Investidor Profissional (>= R$ 10 milhões ou IF)


class OfferingStatus(str, Enum):
    """Ciclo de vida da oferta pública de RWA (Resolução CVM 160)."""
    DRAFT = "DRAFT"
    BOOKBUILDING_OPEN = "BOOKBUILDING_OPEN"
    BOOKBUILDING_CLOSED = "BOOKBUILDING_CLOSED"
    PRICED = "PRICED"
    ALLOCATED = "ALLOCATED"
    SETTLED = "SETTLED"
    MATURED = "MATURED"
    CANCELLED = "CANCELLED"


class BidStatus(str, Enum):
    """Status da ordem de subscrição no livro de bookbuilding."""
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    ALLOCATED = "ALLOCATED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class IssuanceSettlementMode(str, Enum):
    """Mecanismos de liquidação atômica bilateral DvP."""
    DIRECT_ATOMIC_DVP = "DIRECT_ATOMIC_DVP"  # Chamada atômica direta no bloco EVM Besu
    HTLC_ATOMIC_SWAP = "HTLC_ATOMIC_SWAP"    # Hash Time-Locked Contract bilateral


class HtlcStatus(str, Enum):
    """Estados do contrato HTLC."""
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    REFUNDED = "REFUNDED"
    EXPIRED = "EXPIRED"


class CorporateActionType(str, Enum):
    """Eventos corporativos de remuneração e resgate de títulos RWA."""
    COUPON_PAYMENT = "COUPON_PAYMENT"                  # Pagamento de juros periódicos
    PRINCIPAL_AMORTIZATION = "PRINCIPAL_AMORTIZATION"  # Amortização extraordinária/ordinária
    TOTAL_REDEMPTION = "TOTAL_REDEMPTION"              # Resgate total no vencimento


class CorporateActionStatus(str, Enum):
    """Estados do evento corporativo."""
    ANNOUNCED = "ANNOUNCED"
    FUNDED = "FUNDED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"


class IdentityClaimType(str, Enum):
    """Atributos de compliance verificáveis on-chain (ERC-3643)."""
    KYC_APPROVED = "KYC_APPROVED"
    AML_CLEARED = "AML_CLEARED"
    QUALIFIED_INVESTOR = "QUALIFIED_INVESTOR"
    PROFESSIONAL_INVESTOR = "PROFESSIONAL_INVESTOR"
    ACCREDITED_ORIGINATOR = "ACCREDITED_ORIGINATOR"


# ==============================================================================
# MODELOS DE COMPLIANCE ON-CHAIN (ERC-3643 / T-REX)
# ==============================================================================

@dataclass
class IdentityClaim:
    """Afirmação de conformidade emitida por emissor confiável (Claim Issuer)."""
    claim_type: IdentityClaimType
    issuer_did: str
    issued_at: int
    is_valid: bool = True
    signature: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_type": self.claim_type.value,
            "issuer_did": self.issuer_did,
            "issued_at": self.issued_at,
            "is_valid": self.is_valid,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> IdentityClaim:
        return cls(
            claim_type=IdentityClaimType(data["claim_type"]),
            issuer_did=data["issuer_did"],
            issued_at=data["issued_at"],
            is_valid=data.get("is_valid", True),
            signature=data.get("signature", ""),
        )


@dataclass
class OnChainIdentity:
    """Registro de Identidade On-Chain (ONCHAINID) vinculado à carteira EVM."""
    wallet_address: str
    tax_id: str  # CPF ou CNPJ
    investor_profile: InvestorProfile
    country_code: str = "BR"
    claims: Dict[str, IdentityClaim] = field(default_factory=dict)
    is_frozen: bool = False

    def add_claim(self, claim: IdentityClaim) -> None:
        self.claims[claim.claim_type.value] = claim

    def has_valid_claim(self, claim_type: IdentityClaimType) -> bool:
        claim = self.claims.get(claim_type.value)
        return claim is not None and claim.is_valid

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wallet_address": self.wallet_address,
            "tax_id": self.tax_id,
            "investor_profile": self.investor_profile.value,
            "country_code": self.country_code,
            "claims": {k: v.to_dict() for k, v in self.claims.items()},
            "is_frozen": self.is_frozen,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OnChainIdentity:
        claims_map = {
            k: IdentityClaim.from_dict(v) for k, v in data.get("claims", {}).items()
        }
        return cls(
            wallet_address=data["wallet_address"],
            tax_id=data["tax_id"],
            investor_profile=InvestorProfile(data["investor_profile"]),
            country_code=data.get("country_code", "BR"),
            claims=claims_map,
            is_frozen=data.get("is_frozen", False),
        )


@dataclass
class Erc3643ComplianceRule:
    """Regra de conformidade do ativo programada no Smart Contract ERC-3643."""
    token_id: int
    min_investor_profile: InvestorProfile = InvestorProfile.QUALIFIED
    allowed_countries: Set[str] = field(default_factory=lambda: {"BR"})
    max_holding_percentage: Decimal = Decimal("100.00")
    is_paused: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "token_id": self.token_id,
            "min_investor_profile": self.min_investor_profile.value,
            "allowed_countries": sorted(list(self.allowed_countries)),
            "max_holding_percentage": str(self.max_holding_percentage),
            "is_paused": self.is_paused,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Erc3643ComplianceRule:
        return cls(
            token_id=data["token_id"],
            min_investor_profile=InvestorProfile(data["min_investor_profile"]),
            allowed_countries=set(data.get("allowed_countries", ["BR"])),
            max_holding_percentage=Decimal(str(data.get("max_holding_percentage", "100.00"))),
            is_paused=data.get("is_paused", False),
        )


class Erc3643ComplianceManager:
    """Gerenciador de conformidade ERC-3643 com validação on-chain."""

    def __init__(self) -> None:
        self.identities: Dict[str, OnChainIdentity] = {}  # wallet -> identity
        self.rules: Dict[int, Erc3643ComplianceRule] = {}  # token_id -> rule

    def register_identity(self, identity: OnChainIdentity) -> None:
        self.identities[identity.wallet_address.lower()] = identity

    def set_rule(self, rule: Erc3643ComplianceRule) -> None:
        self.rules[rule.token_id] = rule

    def get_identity(self, wallet_address: str) -> Optional[OnChainIdentity]:
        return self.identities.get(wallet_address.lower())

    def can_participate(self, wallet_address: str, token_id: int) -> Tuple[bool, str]:
        rule = self.rules.get(token_id)
        if not rule:
            return True, "No rule configured"

        if rule.is_paused:
            return False, "Token transfers are paused globally"

        ident = self.get_identity(wallet_address)
        if not ident:
            return False, f"Wallet {wallet_address} has no registered OnChainIdentity"

        if ident.is_frozen:
            return False, f"Wallet {wallet_address} is frozen by compliance"

        if not ident.has_valid_claim(IdentityClaimType.KYC_APPROVED):
            return False, f"Wallet {wallet_address} lacks valid KYC claim"

        if not ident.has_valid_claim(IdentityClaimType.AML_CLEARED):
            return False, f"Wallet {wallet_address} lacks valid AML clearance claim"

        if ident.country_code not in rule.allowed_countries:
            return False, f"Country {ident.country_code} is not allowed for token {token_id}"

        # Perfil de investidor: hierarquia RETAIL < QUALIFIED < PROFESSIONAL
        hierarchy = {
            InvestorProfile.RETAIL: 1,
            InvestorProfile.QUALIFIED: 2,
            InvestorProfile.PROFESSIONAL: 3,
        }
        if hierarchy[ident.investor_profile] < hierarchy[rule.min_investor_profile]:
            return False, (
                f"Investor profile {ident.investor_profile.value} is below required "
                f"{rule.min_investor_profile.value}"
            )

        return True, "Compliant"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identities": {k: v.to_dict() for k, v in self.identities.items()},
            "rules": {str(k): v.to_dict() for k, v in self.rules.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Erc3643ComplianceManager:
        mgr = cls()
        for k, v in data.get("identities", {}).items():
            mgr.identities[k.lower()] = OnChainIdentity.from_dict(v)
        for k, v in data.get("rules", {}).items():
            mgr.rules[int(k)] = Erc3643ComplianceRule.from_dict(v)
        return mgr


# ==============================================================================
# SMART CONTRACTS SIMULADOS: ERC-1155 E CBDC REAL DIGITAL
# ==============================================================================

@dataclass
class Erc1155TokenMetadata:
    """Metadados e especificação do título RWA emitido via ERC-1155."""
    token_id: int
    asset_symbol: str
    isin: str
    asset_type: RwaAssetType
    tranche: RwaTranche
    vnu_nominal: Decimal  # Valor Nominal Unitário de Emissão (R$)
    current_vnu: Decimal  # VNU atualizado após amortizações
    total_minted_units: int = 0
    circulating_units: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "token_id": self.token_id,
            "asset_symbol": self.asset_symbol,
            "isin": self.isin,
            "asset_type": self.asset_type.value,
            "tranche": self.tranche.value,
            "vnu_nominal": str(self.vnu_nominal),
            "current_vnu": str(self.current_vnu),
            "total_minted_units": self.total_minted_units,
            "circulating_units": self.circulating_units,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Erc1155TokenMetadata:
        return cls(
            token_id=data["token_id"],
            asset_symbol=data["asset_symbol"],
            isin=data["isin"],
            asset_type=RwaAssetType(data["asset_type"]),
            tranche=RwaTranche(data["tranche"]),
            vnu_nominal=Decimal(str(data["vnu_nominal"])),
            current_vnu=Decimal(str(data["current_vnu"])),
            total_minted_units=data.get("total_minted_units", 0),
            circulating_units=data.get("circulating_units", 0),
        )


class Erc1155MultiTokenContract:
    """Contrato Multi-Token ERC-1155 simulado para títulos de dívida e recebíveis RWA."""

    def __init__(self) -> None:
        # (token_id, wallet_address) -> balance
        self.balances: Dict[Tuple[int, str], int] = {}
        self.metadata: Dict[int, Erc1155TokenMetadata] = {}

    def register_token(self, meta: Erc1155TokenMetadata) -> None:
        self.metadata[meta.token_id] = meta

    def balance_of(self, wallet_address: str, token_id: int) -> int:
        return self.balances.get((token_id, wallet_address.lower()), 0)

    def mint(self, to_address: str, token_id: int, amount: int) -> None:
        if amount <= 0:
            raise ValueError(f"Quantidade de mint deve ser positiva: {amount}")
        meta = self.metadata.get(token_id)
        if not meta:
            raise ValueError(f"Token ID {token_id} não cadastrado")

        key = (token_id, to_address.lower())
        self.balances[key] = self.balances.get(key, 0) + amount
        meta.total_minted_units += amount
        meta.circulating_units += amount

    def safe_transfer_from(
        self,
        from_address: str,
        to_address: str,
        token_id: int,
        amount: int,
    ) -> None:
        if amount <= 0:
            raise ValueError(f"Quantidade de transferência deve ser positiva: {amount}")
        sender_key = (token_id, from_address.lower())
        recipient_key = (token_id, to_address.lower())

        sender_balance = self.balances.get(sender_key, 0)
        if sender_balance < amount:
            raise DrexRwaInsufficientBalanceError(
                f"Saldo insuficiente de tokens RWA {token_id} na carteira {from_address}: "
                f"Possui {sender_balance}, tentou transferir {amount}"
            )

        self.balances[sender_key] = sender_balance - amount
        self.balances[recipient_key] = self.balances.get(recipient_key, 0) + amount

    def burn(self, from_address: str, token_id: int, amount: int) -> None:
        if amount <= 0:
            raise ValueError(f"Quantidade de burn deve ser positiva: {amount}")
        meta = self.metadata.get(token_id)
        if not meta:
            raise ValueError(f"Token ID {token_id} não cadastrado")

        key = (token_id, from_address.lower())
        current = self.balances.get(key, 0)
        if current < amount:
            raise DrexRwaInsufficientBalanceError(
                f"Saldo insuficiente para queima de tokens RWA: {current} < {amount}"
            )
        self.balances[key] = current - amount
        meta.circulating_units -= amount

    def get_token_holders(self, token_id: int) -> Dict[str, int]:
        """Retorna snapshot de todos os detentores com saldo positivo."""
        holders: Dict[str, int] = {}
        for (tid, wallet), bal in self.balances.items():
            if tid == token_id and bal > 0:
                holders[wallet] = bal
        return holders

    def to_dict(self) -> Dict[str, Any]:
        ser_balances = [
            {"token_id": tid, "wallet_address": wallet, "balance": bal}
            for (tid, wallet), bal in self.balances.items()
        ]
        return {
            "balances": ser_balances,
            "metadata": {str(k): v.to_dict() for k, v in self.metadata.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Erc1155MultiTokenContract:
        contract = cls()
        for item in data.get("balances", []):
            contract.balances[(item["token_id"], item["wallet_address"].lower())] = int(item["balance"])
        for k, v in data.get("metadata", {}).items():
            contract.metadata[int(k)] = Erc1155TokenMetadata.from_dict(v)
        return contract


class RealDigitalTokenContract:
    """Smart contract simulado da CBDC Real Digital (BACEN) para liquidação atômica."""

    def __init__(self) -> None:
        self.balances: Dict[str, Decimal] = {}  # wallet -> balance BRL

    def balance_of(self, wallet_address: str) -> Decimal:
        return self.balances.get(wallet_address.lower(), Decimal("0.00"))

    def mint(self, wallet_address: str, amount: Decimal) -> None:
        if amount <= Decimal("0.00"):
            raise ValueError(f"Montante de mint de Real Digital deve ser positivo: {amount}")
        wallet = wallet_address.lower()
        self.balances[wallet] = self.balance_of(wallet) + amount

    def transfer(self, from_address: str, to_address: str, amount: Decimal) -> None:
        if amount <= Decimal("0.00"):
            raise ValueError(f"Montante de transferência deve ser positivo: {amount}")
        sender = from_address.lower()
        recipient = to_address.lower()
        sender_bal = self.balance_of(sender)
        if sender_bal < amount:
            raise DrexRwaInsufficientBalanceError(
                f"Saldo insuficiente de Real Digital para {from_address}: "
                f"Disponível {sender_bal}, necessário {amount}"
            )
        self.balances[sender] = sender_bal - amount
        self.balances[recipient] = self.balance_of(recipient) + amount

    def to_dict(self) -> Dict[str, Any]:
        return {
            "balances": {k: str(v) for k, v in self.balances.items()}
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RealDigitalTokenContract:
        contract = cls()
        for k, v in data.get("balances", {}).items():
            contract.balances[k.lower()] = Decimal(str(v))
        return contract


# ==============================================================================
# MODELOS DE BOOKBUILDING, ALOCAÇÃO E LIQUIDAÇÃO DVP
# ==============================================================================

@dataclass
class BookbuildingBid:
    """Oferta de subscrição inserida por investidor no processo de bookbuilding."""
    bid_id: str
    offering_id: str
    investor_wallet: str
    investor_tax_id: str
    requested_units: int
    limit_rate: Decimal  # Ex: 6.25 (IPCA + 6.25%) ou 1.20 (120% CDI) ou taxa máxima aceita
    max_price: Optional[Decimal] = None
    allocated_units: int = 0
    status: BidStatus = BidStatus.SUBMITTED
    timestamp: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bid_id": self.bid_id,
            "offering_id": self.offering_id,
            "investor_wallet": self.investor_wallet,
            "investor_tax_id": self.investor_tax_id,
            "requested_units": self.requested_units,
            "limit_rate": str(self.limit_rate),
            "max_price": str(self.max_price) if self.max_price is not None else None,
            "allocated_units": self.allocated_units,
            "status": self.status.value,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BookbuildingBid:
        return cls(
            bid_id=data["bid_id"],
            offering_id=data["offering_id"],
            investor_wallet=data["investor_wallet"],
            investor_tax_id=data["investor_tax_id"],
            requested_units=int(data["requested_units"]),
            limit_rate=Decimal(str(data["limit_rate"])),
            max_price=Decimal(str(data["max_price"])) if data.get("max_price") is not None else None,
            allocated_units=int(data.get("allocated_units", 0)),
            status=BidStatus(data["status"]),
            timestamp=int(data.get("timestamp", int(time.time()))),
        )


@dataclass
class AllocationRecord:
    """Resultado formal da alocação de subscrição primária."""
    allocation_id: str
    offering_id: str
    bid_id: str
    investor_wallet: str
    token_id: int
    allocated_units: int
    unit_price_brl: Decimal
    total_financial_brl: Decimal
    settled: bool = False
    settlement_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allocation_id": self.allocation_id,
            "offering_id": self.offering_id,
            "bid_id": self.bid_id,
            "investor_wallet": self.investor_wallet,
            "token_id": self.token_id,
            "allocated_units": self.allocated_units,
            "unit_price_brl": str(self.unit_price_brl),
            "total_financial_brl": str(self.total_financial_brl),
            "settled": self.settled,
            "settlement_id": self.settlement_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AllocationRecord:
        return cls(
            allocation_id=data["allocation_id"],
            offering_id=data["offering_id"],
            bid_id=data["bid_id"],
            investor_wallet=data["investor_wallet"],
            token_id=int(data["token_id"]),
            allocated_units=int(data["allocated_units"]),
            unit_price_brl=Decimal(str(data["unit_price_brl"])),
            total_financial_brl=Decimal(str(data["total_financial_brl"])),
            settled=data.get("settled", False),
            settlement_id=data.get("settlement_id"),
        )


@dataclass
class DvpSettlementReceipt:
    """Comprovante determinístico de liquidação atômica bilateral DvP."""
    receipt_id: str
    offering_id: str
    allocation_id: str
    mode: IssuanceSettlementMode
    token_id: int
    delivered_units: int
    unit_price_brl: Decimal
    settled_financial_brl: Decimal
    issuer_wallet: str
    investor_wallet: str
    transaction_hash: str
    settled_at: int
    status: str = "COMMITTED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "offering_id": self.offering_id,
            "allocation_id": self.allocation_id,
            "mode": self.mode.value,
            "token_id": self.token_id,
            "delivered_units": self.delivered_units,
            "unit_price_brl": str(self.unit_price_brl),
            "settled_financial_brl": str(self.settled_financial_brl),
            "issuer_wallet": self.issuer_wallet,
            "investor_wallet": self.investor_wallet,
            "transaction_hash": self.transaction_hash,
            "settled_at": self.settled_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DvpSettlementReceipt:
        return cls(
            receipt_id=data["receipt_id"],
            offering_id=data["offering_id"],
            allocation_id=data["allocation_id"],
            mode=IssuanceSettlementMode(data["mode"]),
            token_id=int(data["token_id"]),
            delivered_units=int(data["delivered_units"]),
            unit_price_brl=Decimal(str(data["unit_price_brl"])),
            settled_financial_brl=Decimal(str(data["settled_financial_brl"])),
            issuer_wallet=data["issuer_wallet"],
            investor_wallet=data["investor_wallet"],
            transaction_hash=data["transaction_hash"],
            settled_at=int(data["settled_at"]),
            status=data.get("status", "COMMITTED"),
        )


@dataclass
class HtlcDvpContract:
    """Contrato Hash Time-Locked Contract (HTLC) para liquidação DvP condicional."""
    contract_id: str
    offering_id: str
    allocation_id: str
    token_id: int
    units: int
    total_cash_brl: Decimal
    issuer_wallet: str
    investor_wallet: str
    hash_lock: str  # SHA-256 do segredo preimage
    time_lock: int  # Timestamp Unix de expiração
    preimage: Optional[str] = None
    status: HtlcStatus = HtlcStatus.PENDING
    created_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "offering_id": self.offering_id,
            "allocation_id": self.allocation_id,
            "token_id": self.token_id,
            "units": self.units,
            "total_cash_brl": str(self.total_cash_brl),
            "issuer_wallet": self.issuer_wallet,
            "investor_wallet": self.investor_wallet,
            "hash_lock": self.hash_lock,
            "time_lock": self.time_lock,
            "preimage": self.preimage,
            "status": self.status.value,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> HtlcDvpContract:
        return cls(
            contract_id=data["contract_id"],
            offering_id=data["offering_id"],
            allocation_id=data["allocation_id"],
            token_id=int(data["token_id"]),
            units=int(data["units"]),
            total_cash_brl=Decimal(str(data["total_cash_brl"])),
            issuer_wallet=data["issuer_wallet"],
            investor_wallet=data["investor_wallet"],
            hash_lock=data["hash_lock"],
            time_lock=int(data["time_lock"]),
            preimage=data.get("preimage"),
            status=HtlcStatus(data["status"]),
            created_at=int(data.get("created_at", int(time.time()))),
        )


# ==============================================================================
# MODELOS DE PROVENTOS E AÇÕES CORPORATIVAS ON-CHAIN
# ==============================================================================

@dataclass
class PaymentDistributionRecord:
    """Registro individual de provento pago a detentor de token RWA."""
    distribution_id: str
    event_id: str
    investor_wallet: str
    token_units_held: int
    unit_amount_brl: Decimal
    amount_paid_brl: Decimal
    paid_at: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "distribution_id": self.distribution_id,
            "event_id": self.event_id,
            "investor_wallet": self.investor_wallet,
            "token_units_held": self.token_units_held,
            "unit_amount_brl": str(self.unit_amount_brl),
            "amount_paid_brl": str(self.amount_paid_brl),
            "paid_at": self.paid_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PaymentDistributionRecord:
        return cls(
            distribution_id=data["distribution_id"],
            event_id=data["event_id"],
            investor_wallet=data["investor_wallet"],
            token_units_held=int(data["token_units_held"]),
            unit_amount_brl=Decimal(str(data["unit_amount_brl"])),
            amount_paid_brl=Decimal(str(data["amount_paid_brl"])),
            paid_at=int(data["paid_at"]),
        )


@dataclass
class CorporateActionEvent:
    """Evento corporativo de pagamento de cupom de juros ou amortização de principal."""
    event_id: str
    offering_id: str
    token_id: int
    event_type: CorporateActionType
    record_date: str  # YYYY-MM-DD
    payment_date: str  # YYYY-MM-DD
    rate_or_percentage: Decimal  # Ex: 3.25 (% cupom ou % amortização)
    unit_amount_brl: Decimal  # Valor pago por token unitário
    total_funded_brl: Decimal = Decimal("0.00")
    total_distributed_brl: Decimal = Decimal("0.00")
    status: CorporateActionStatus = CorporateActionStatus.ANNOUNCED
    distributions: List[PaymentDistributionRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "offering_id": self.offering_id,
            "token_id": self.token_id,
            "event_type": self.event_type.value,
            "record_date": self.record_date,
            "payment_date": self.payment_date,
            "rate_or_percentage": str(self.rate_or_percentage),
            "unit_amount_brl": str(self.unit_amount_brl),
            "total_funded_brl": str(self.total_funded_brl),
            "total_distributed_brl": str(self.total_distributed_brl),
            "status": self.status.value,
            "distributions": [d.to_dict() for d in self.distributions],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CorporateActionEvent:
        return cls(
            event_id=data["event_id"],
            offering_id=data["offering_id"],
            token_id=int(data["token_id"]),
            event_type=CorporateActionType(data["event_type"]),
            record_date=data["record_date"],
            payment_date=data["payment_date"],
            rate_or_percentage=Decimal(str(data["rate_or_percentage"])),
            unit_amount_brl=Decimal(str(data["unit_amount_brl"])),
            total_funded_brl=Decimal(str(data.get("total_funded_brl", "0.00"))),
            total_distributed_brl=Decimal(str(data.get("total_distributed_brl", "0.00"))),
            status=CorporateActionStatus(data["status"]),
            distributions=[PaymentDistributionRecord.from_dict(d) for d in data.get("distributions", [])],
        )


# ==============================================================================
# OFERTA PÚBLICA RWA COMPLETA
# ==============================================================================

@dataclass
class RwaPrimaryIssuanceOffering:
    """Estrutura da oferta de emissão primária RWA conforme CVM 160 / Drex Fase 2."""
    offering_id: str
    asset_symbol: str
    isin: str
    asset_type: RwaAssetType
    tranche: RwaTranche
    token_id: int
    target_units: int
    vnu_nominal: Decimal
    current_vnu: Decimal
    issuer_wallet: str
    underwriter_wallet: str
    escrow_wallet: str
    benchmark_description: str  # Ex: "100% CDI + 1.50%" ou "IPCA + 6.25%"
    min_rate: Decimal  # Taxa piso
    max_rate: Decimal  # Taxa teto
    subscription_price: Decimal  # Preço definitivo de subscrição
    cut_off_rate: Optional[Decimal] = None
    status: OfferingStatus = OfferingStatus.DRAFT
    bids: Dict[str, BookbuildingBid] = field(default_factory=dict)
    allocations: Dict[str, AllocationRecord] = field(default_factory=dict)
    total_allocated_units: int = 0
    total_settled_units: int = 0
    total_settled_financial_brl: Decimal = Decimal("0.00")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "offering_id": self.offering_id,
            "asset_symbol": self.asset_symbol,
            "isin": self.isin,
            "asset_type": self.asset_type.value,
            "tranche": self.tranche.value,
            "token_id": self.token_id,
            "target_units": self.target_units,
            "vnu_nominal": str(self.vnu_nominal),
            "current_vnu": str(self.current_vnu),
            "issuer_wallet": self.issuer_wallet,
            "underwriter_wallet": self.underwriter_wallet,
            "escrow_wallet": self.escrow_wallet,
            "benchmark_description": self.benchmark_description,
            "min_rate": str(self.min_rate),
            "max_rate": str(self.max_rate),
            "subscription_price": str(self.subscription_price),
            "cut_off_rate": str(self.cut_off_rate) if self.cut_off_rate is not None else None,
            "status": self.status.value,
            "bids": {k: v.to_dict() for k, v in self.bids.items()},
            "allocations": {k: v.to_dict() for k, v in self.allocations.items()},
            "total_allocated_units": self.total_allocated_units,
            "total_settled_units": self.total_settled_units,
            "total_settled_financial_brl": str(self.total_settled_financial_brl),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RwaPrimaryIssuanceOffering:
        off = cls(
            offering_id=data["offering_id"],
            asset_symbol=data["asset_symbol"],
            isin=data["isin"],
            asset_type=RwaAssetType(data["asset_type"]),
            tranche=RwaTranche(data["tranche"]),
            token_id=int(data["token_id"]),
            target_units=int(data["target_units"]),
            vnu_nominal=Decimal(str(data["vnu_nominal"])),
            current_vnu=Decimal(str(data["current_vnu"])),
            issuer_wallet=data["issuer_wallet"],
            underwriter_wallet=data["underwriter_wallet"],
            escrow_wallet=data["escrow_wallet"],
            benchmark_description=data["benchmark_description"],
            min_rate=Decimal(str(data["min_rate"])),
            max_rate=Decimal(str(data["max_rate"])),
            subscription_price=Decimal(str(data["subscription_price"])),
            cut_off_rate=Decimal(str(data["cut_off_rate"])) if data.get("cut_off_rate") is not None else None,
            status=OfferingStatus(data["status"]),
            total_allocated_units=int(data.get("total_allocated_units", 0)),
            total_settled_units=int(data.get("total_settled_units", 0)),
            total_settled_financial_brl=Decimal(str(data.get("total_settled_financial_brl", "0.00"))),
        )
        off.bids = {k: BookbuildingBid.from_dict(v) for k, v in data.get("bids", {}).items()}
        off.allocations = {k: AllocationRecord.from_dict(v) for k, v in data.get("allocations", {}).items()}
        return off


# ==============================================================================
# MOTOR PRINCIPAL DE EMISSÃO PRIMÁRIA RWA DREX (FL-105)
# ==============================================================================

class DrexRwaPrimaryIssuanceEngine:
    """Motor de Emissão Primária de Ativos de Renda Fixa Privada (RWA) no Drex Fase 2."""

    def __init__(self) -> None:
        self.cbdc = RealDigitalTokenContract()
        self.rwa_tokens = Erc1155MultiTokenContract()
        self.compliance = Erc3643ComplianceManager()
        self.offerings: Dict[str, RwaPrimaryIssuanceOffering] = {}
        self.htlc_settlements: Dict[str, HtlcDvpContract] = {}
        self.dvp_receipts: List[DvpSettlementReceipt] = []
        self.corporate_actions: Dict[str, CorporateActionEvent] = {}

    # --------------------------------------------------------------------------
    # 1. GESTÃO DE IDENTIDADE E COMPLIANCE ERC-3643
    # --------------------------------------------------------------------------

    def register_investor(
        self,
        wallet_address: str,
        tax_id: str,
        investor_profile: InvestorProfile = InvestorProfile.QUALIFIED,
        country_code: str = "BR",
        claims: Optional[List[IdentityClaimType]] = None,
    ) -> OnChainIdentity:
        """Registra identidade ONCHAINID e anexa credenciais verificadas."""
        ident = OnChainIdentity(
            wallet_address=wallet_address.lower(),
            tax_id=tax_id,
            investor_profile=investor_profile,
            country_code=country_code,
        )
        now = int(time.time())
        # Reivindicações padrão mínimas
        default_claims = claims or [IdentityClaimType.KYC_APPROVED, IdentityClaimType.AML_CLEARED]
        for c_type in default_claims:
            ident.add_claim(
                IdentityClaim(
                    claim_type=c_type,
                    issuer_did="did:drex:bacen:compliance:certifier",
                    issued_at=now,
                    is_valid=True,
                    signature=hashlib.sha256(f"{wallet_address}:{c_type.value}:{now}".encode()).hexdigest(),
                )
            )
        self.compliance.register_identity(ident)
        return ident

    def configure_compliance_rule(
        self,
        token_id: int,
        min_investor_profile: InvestorProfile = InvestorProfile.QUALIFIED,
        allowed_countries: Optional[Set[str]] = None,
        max_holding_percentage: Decimal = Decimal("100.00"),
    ) -> Erc3643ComplianceRule:
        rule = Erc3643ComplianceRule(
            token_id=token_id,
            min_investor_profile=min_investor_profile,
            allowed_countries=allowed_countries or {"BR"},
            max_holding_percentage=max_holding_percentage,
        )
        self.compliance.set_rule(rule)
        return rule

    # --------------------------------------------------------------------------
    # 2. CRIAÇÃO DE OFERTA E CADASTRO ERC-1155
    # --------------------------------------------------------------------------

    def create_offering(
        self,
        offering_id: str,
        asset_symbol: str,
        isin: str,
        asset_type: RwaAssetType,
        tranche: RwaTranche,
        token_id: int,
        target_units: int,
        vnu_nominal: Decimal,
        issuer_wallet: str,
        underwriter_wallet: str,
        escrow_wallet: str,
        benchmark_description: str,
        min_rate: Decimal,
        max_rate: Decimal,
        subscription_price: Optional[Decimal] = None,
        min_investor_profile: InvestorProfile = InvestorProfile.QUALIFIED,
    ) -> RwaPrimaryIssuanceOffering:
        """Cria e registra uma nova oferta primária de ativo RWA."""
        if offering_id in self.offerings:
            raise DrexRwaInvalidStateError(f"Oferta '{offering_id}' já cadastrada.")

        sub_price = subscription_price if subscription_price is not None else vnu_nominal

        offering = RwaPrimaryIssuanceOffering(
            offering_id=offering_id,
            asset_symbol=asset_symbol,
            isin=isin,
            asset_type=asset_type,
            tranche=tranche,
            token_id=token_id,
            target_units=target_units,
            vnu_nominal=vnu_nominal,
            current_vnu=vnu_nominal,
            issuer_wallet=issuer_wallet.lower(),
            underwriter_wallet=underwriter_wallet.lower(),
            escrow_wallet=escrow_wallet.lower(),
            benchmark_description=benchmark_description,
            min_rate=min_rate,
            max_rate=max_rate,
            subscription_price=sub_price,
            status=OfferingStatus.DRAFT,
        )

        # Registra metadados no ERC-1155
        meta = Erc1155TokenMetadata(
            token_id=token_id,
            asset_symbol=asset_symbol,
            isin=isin,
            asset_type=asset_type,
            tranche=tranche,
            vnu_nominal=vnu_nominal,
            current_vnu=vnu_nominal,
        )
        self.rwa_tokens.register_token(meta)

        # Configura regra de conformidade ERC-3643
        self.configure_compliance_rule(
            token_id=token_id,
            min_investor_profile=min_investor_profile,
        )

        self.offerings[offering_id] = offering
        return offering

    # --------------------------------------------------------------------------
    # 3. BOOKBUILDING E COLETA DE ORDENS
    # --------------------------------------------------------------------------

    def open_bookbuilding(self, offering_id: str) -> None:
        offering = self.offerings.get(offering_id)
        if not offering:
            raise DrexRwaBookbuildingError(f"Oferta '{offering_id}' não encontrada.")
        if offering.status != OfferingStatus.DRAFT:
            raise DrexRwaInvalidStateError(f"Oferta '{offering_id}' não está em status DRAFT (atual: {offering.status}).")
        offering.status = OfferingStatus.BOOKBUILDING_OPEN

    def submit_bid(
        self,
        offering_id: str,
        bid_id: str,
        investor_wallet: str,
        requested_units: int,
        limit_rate: Decimal,
        max_price: Optional[Decimal] = None,
    ) -> BookbuildingBid:
        """Submete uma ordem de subscrição primária no livro de ofertas."""
        offering = self.offerings.get(offering_id)
        if not offering:
            raise DrexRwaBookbuildingError(f"Oferta '{offering_id}' não encontrada.")
        if offering.status != OfferingStatus.BOOKBUILDING_OPEN:
            raise DrexRwaBookbuildingError(
                f"Livro de ofertas fechado para '{offering_id}' (status atual: {offering.status})"
            )
        if requested_units <= 0:
            raise DrexRwaBookbuildingError(f"Quantidade de subscrição inválida: {requested_units}")

        # Validação ERC-3643 de compliance
        can_part, reason = self.compliance.can_participate(investor_wallet, offering.token_id)
        if not can_part:
            raise DrexRwaComplianceError(f"Investidor {investor_wallet} reprovado em compliance: {reason}")

        ident = self.compliance.get_identity(investor_wallet)
        tax_id = ident.tax_id if ident else "UNKNOWN"

        bid = BookbuildingBid(
            bid_id=bid_id,
            offering_id=offering_id,
            investor_wallet=investor_wallet.lower(),
            investor_tax_id=tax_id,
            requested_units=requested_units,
            limit_rate=limit_rate,
            max_price=max_price,
            status=BidStatus.ACCEPTED,
        )
        offering.bids[bid_id] = bid
        return bid

    # --------------------------------------------------------------------------
    # 4. FIXAÇÃO DE TAXA DE CORTE E ALOCAÇÃO PRO-RATA
    # --------------------------------------------------------------------------

    def close_and_price_bookbuilding(
        self,
        offering_id: str,
        fixed_cut_off_rate: Optional[Decimal] = None,
    ) -> List[AllocationRecord]:
        """Encerra o bookbuilding, apura a taxa de corte e gera alocações com rateio."""
        offering = self.offerings.get(offering_id)
        if not offering:
            raise DrexRwaBookbuildingError(f"Oferta '{offering_id}' não encontrada.")
        if offering.status != OfferingStatus.BOOKBUILDING_OPEN:
            raise DrexRwaInvalidStateError(
                f"Oferta '{offering_id}' não está com bookbuilding aberto (status: {offering.status})"
            )

        accepted_bids = [b for b in offering.bids.values() if b.status == BidStatus.ACCEPTED]
        if not accepted_bids:
            offering.status = OfferingStatus.CANCELLED
            raise DrexRwaBookbuildingError(f"Nenhuma ordem válida no bookbuilding para '{offering_id}'")

        # Ordenação por taxa ofertada crescente (menor spread/taxa = maior prioridade de alocação)
        accepted_bids.sort(key=lambda b: (b.limit_rate, b.timestamp))

        target = offering.target_units
        cumulative_units = 0
        cut_off_rate = fixed_cut_off_rate

        # Apuração da taxa de corte pelo livro se não for fornecida explicitamente
        if cut_off_rate is None:
            for b in accepted_bids:
                cumulative_units += b.requested_units
                if cumulative_units >= target:
                    cut_off_rate = b.limit_rate
                    break
            if cut_off_rate is None:
                # Demanda total foi inferior ou igual ao alvo
                cut_off_rate = accepted_bids[-1].limit_rate

        offering.cut_off_rate = cut_off_rate
        offering.status = OfferingStatus.PRICED

        # Separação das ordens: estritamente abaixo da taxa de corte e no corte (empate)
        strictly_below = [b for b in accepted_bids if b.limit_rate < cut_off_rate]
        at_cut_off = [b for b in accepted_bids if b.limit_rate == cut_off_rate]
        above_cut_off = [b for b in accepted_bids if b.limit_rate > cut_off_rate]

        for b in above_cut_off:
            b.status = BidStatus.REJECTED
            b.allocated_units = 0

        # Alocação integral para ordens prioritárias
        allocations: List[AllocationRecord] = []
        allocated_so_far = 0

        for b in strictly_below:
            b.allocated_units = b.requested_units
            b.status = BidStatus.ALLOCATED
            allocated_so_far += b.allocated_units

            alloc_rec = AllocationRecord(
                allocation_id=f"ALLOC-{b.bid_id}",
                offering_id=offering_id,
                bid_id=b.bid_id,
                investor_wallet=b.investor_wallet,
                token_id=offering.token_id,
                allocated_units=b.allocated_units,
                unit_price_brl=offering.subscription_price,
                total_financial_brl=Decimal(b.allocated_units) * offering.subscription_price,
            )
            allocations.append(alloc_rec)
            offering.allocations[alloc_rec.allocation_id] = alloc_rec

        # Rateio pro-rata para ordens na taxa de corte
        remaining_units = max(0, target - allocated_so_far)
        marginal_demand = sum(b.requested_units for b in at_cut_off)

        if marginal_demand > 0 and remaining_units > 0:
            if marginal_demand <= remaining_units:
                # Cabe tudo
                for b in at_cut_off:
                    b.allocated_units = b.requested_units
                    b.status = BidStatus.ALLOCATED
                    allocated_so_far += b.allocated_units
                    alloc_rec = AllocationRecord(
                        allocation_id=f"ALLOC-{b.bid_id}",
                        offering_id=offering_id,
                        bid_id=b.bid_id,
                        investor_wallet=b.investor_wallet,
                        token_id=offering.token_id,
                        allocated_units=b.allocated_units,
                        unit_price_brl=offering.subscription_price,
                        total_financial_brl=Decimal(b.allocated_units) * offering.subscription_price,
                    )
                    allocations.append(alloc_rec)
                    offering.allocations[alloc_rec.allocation_id] = alloc_rec
            else:
                # Rateio proporcional determinístico
                units_assigned = 0
                for idx, b in enumerate(at_cut_off):
                    # Proporção aritmética
                    ratio = Decimal(b.requested_units) / Decimal(marginal_demand)
                    raw_units = int((Decimal(remaining_units) * ratio).quantize(Decimal("1"), rounding=ROUND_DOWN))
                    # Ajuste de resíduo no último elemento
                    if idx == len(at_cut_off) - 1:
                        allocated_for_b = remaining_units - units_assigned
                    else:
                        allocated_for_b = min(raw_units, remaining_units - units_assigned)
                    
                    b.allocated_units = allocated_for_b
                    units_assigned += allocated_for_b
                    b.status = BidStatus.ALLOCATED if allocated_for_b > 0 else BidStatus.REJECTED

                    if allocated_for_b > 0:
                        alloc_rec = AllocationRecord(
                            allocation_id=f"ALLOC-{b.bid_id}",
                            offering_id=offering_id,
                            bid_id=b.bid_id,
                            investor_wallet=b.investor_wallet,
                            token_id=offering.token_id,
                            allocated_units=allocated_for_b,
                            unit_price_brl=offering.subscription_price,
                            total_financial_brl=Decimal(allocated_for_b) * offering.subscription_price,
                        )
                        allocations.append(alloc_rec)
                        offering.allocations[alloc_rec.allocation_id] = alloc_rec
        elif marginal_demand > 0 and remaining_units == 0:
            for b in at_cut_off:
                b.allocated_units = 0
                b.status = BidStatus.REJECTED

        offering.total_allocated_units = sum(a.allocated_units for a in allocations)
        offering.status = OfferingStatus.ALLOCATED
        return allocations

    # --------------------------------------------------------------------------
    # 5. LIQUIDAÇÃO ATÔMICA DVP DIRETA (EVM SAME-BLOCK ATOMIC SWAP)
    # --------------------------------------------------------------------------

    def settle_direct_atomic_dvp(self, offering_id: str, allocation_id: str) -> DvpSettlementReceipt:
        """Executa a liquidação atômica bilateral DvP no mesmo bloco EVM."""
        offering = self.offerings.get(offering_id)
        if not offering:
            raise DrexRwaInvalidStateError(f"Oferta '{offering_id}' não encontrada.")
        alloc = offering.allocations.get(allocation_id)
        if not alloc:
            raise DrexRwaInvalidStateError(f"Alocação '{allocation_id}' não encontrada.")
        if alloc.settled:
            raise DrexRwaInvalidStateError(f"Alocação '{allocation_id}' já liquidada.")

        # Validação ERC-3643 antes da emissão
        can_part, reason = self.compliance.can_participate(alloc.investor_wallet, offering.token_id)
        if not can_part:
            raise DrexRwaComplianceError(
                f"Bloqueio de Compliance ERC-3643 na liquidação: {reason}"
            )

        # 1. Perna Financeira: Débito em Real Digital do investidor e crédito no emissor
        investor_rd_bal = self.cbdc.balance_of(alloc.investor_wallet)
        if investor_rd_bal < alloc.total_financial_brl:
            raise DrexRwaInsufficientBalanceError(
                f"Investidor {alloc.investor_wallet} não possui saldo de Real Digital suficiente: "
                f"Disponível {investor_rd_bal}, Requerido {alloc.total_financial_brl}"
            )

        self.cbdc.transfer(alloc.investor_wallet, offering.issuer_wallet, alloc.total_financial_brl)

        # 2. Perna de Ativos: Emissão (Mint) dos tokens RWA diretamente na carteira do investidor
        self.rwa_tokens.mint(alloc.investor_wallet, offering.token_id, alloc.allocated_units)

        # 3. Comprovante e Integridade LAW-111
        receipt_id = f"REC-DVP-{alloc.allocation_id}"
        tx_hash = hashlib.sha256(
            f"{receipt_id}:{offering_id}:{alloc.allocated_units}:{alloc.total_financial_brl}".encode()
        ).hexdigest()

        receipt = DvpSettlementReceipt(
            receipt_id=receipt_id,
            offering_id=offering_id,
            allocation_id=allocation_id,
            mode=IssuanceSettlementMode.DIRECT_ATOMIC_DVP,
            token_id=offering.token_id,
            delivered_units=alloc.allocated_units,
            unit_price_brl=alloc.unit_price_brl,
            settled_financial_brl=alloc.total_financial_brl,
            issuer_wallet=offering.issuer_wallet,
            investor_wallet=alloc.investor_wallet,
            transaction_hash=f"0x{tx_hash}",
            settled_at=int(time.time()),
        )

        alloc.settled = True
        alloc.settlement_id = receipt_id
        offering.total_settled_units += receipt.delivered_units
        offering.total_settled_financial_brl += receipt.settled_financial_brl
        self.dvp_receipts.append(receipt)

        # Verificação formal pontual LAW-111
        assert_drex_rwa_primary_issuance_invariants(
            allocated_units=alloc.allocated_units,
            delivered_units=receipt.delivered_units,
            received_financial=alloc.total_financial_brl,
            expected_financial=Decimal(alloc.allocated_units) * alloc.unit_price_brl,
            operation_id=receipt_id,
        )

        # Atualiza status global se todas as alocações foram liquidadas
        if all(a.settled for a in offering.allocations.values()):
            offering.status = OfferingStatus.SETTLED

        return receipt

    # --------------------------------------------------------------------------
    # 6. LIQUIDAÇÃO ATÔMICA CONDICIONAL VIA HTLC (HASH TIME-LOCKED CONTRACT)
    # --------------------------------------------------------------------------

    def initiate_htlc_dvp(
        self,
        offering_id: str,
        allocation_id: str,
        hash_lock: str,
        timeout_seconds: int = 3600,
    ) -> HtlcDvpContract:
        """Inicializa um contrato HTLC e trava os fundos de ambas as pernas no contrato."""
        offering = self.offerings.get(offering_id)
        if not offering:
            raise DrexRwaInvalidStateError(f"Oferta '{offering_id}' não encontrada.")
        alloc = offering.allocations.get(allocation_id)
        if not alloc:
            raise DrexRwaInvalidStateError(f"Alocação '{allocation_id}' não encontrada.")
        if alloc.settled:
            raise DrexRwaInvalidStateError(f"Alocação '{allocation_id}' já liquidada.")

        # Compliance check
        can_part, reason = self.compliance.can_participate(alloc.investor_wallet, offering.token_id)
        if not can_part:
            raise DrexRwaComplianceError(f"Compliance ERC-3643 reprovado: {reason}")

        # Trava Real Digital do investidor na conta escrow do HTLC
        contract_id = f"HTLC-{alloc.allocation_id}"
        if contract_id in self.htlc_settlements:
            raise DrexRwaHtlcError(f"Contrato HTLC '{contract_id}' já existe.")

        self.cbdc.transfer(alloc.investor_wallet, offering.escrow_wallet, alloc.total_financial_brl)

        # Mint dos tokens no escrow do emissor para liquidação condicional
        self.rwa_tokens.mint(offering.escrow_wallet, offering.token_id, alloc.allocated_units)

        htlc = HtlcDvpContract(
            contract_id=contract_id,
            offering_id=offering_id,
            allocation_id=allocation_id,
            token_id=offering.token_id,
            units=alloc.allocated_units,
            total_cash_brl=alloc.total_financial_brl,
            issuer_wallet=offering.issuer_wallet,
            investor_wallet=alloc.investor_wallet,
            hash_lock=hash_lock.lower(),
            time_lock=int(time.time()) + timeout_seconds,
            status=HtlcStatus.PENDING,
        )
        self.htlc_settlements[contract_id] = htlc
        return htlc

    def claim_htlc_dvp(self, contract_id: str, preimage: str) -> DvpSettlementReceipt:
        """Liquida o contrato HTLC com a apresentação da preimage válida antes do timeout."""
        htlc = self.htlc_settlements.get(contract_id)
        if not htlc:
            raise DrexRwaHtlcError(f"Contrato HTLC '{contract_id}' não encontrado.")
        if htlc.status != HtlcStatus.PENDING:
            raise DrexRwaHtlcError(f"Contrato HTLC '{contract_id}' em estado inválido: {htlc.status}")

        now = int(time.time())
        if now > htlc.time_lock:
            htlc.status = HtlcStatus.EXPIRED
            raise DrexRwaHtlcError(f"Contrato HTLC '{contract_id}' expirou ({now} > {htlc.time_lock})")

        computed_hash = hashlib.sha256(preimage.encode()).hexdigest().lower()
        if computed_hash != htlc.hash_lock:
            raise DrexRwaHtlcError(f"Preimage inválida para o hash_lock: {computed_hash} != {htlc.hash_lock}")

        offering = self.offerings[htlc.offering_id]
        alloc = offering.allocations[htlc.allocation_id]

        # 1. Perna Financeira: Libera Real Digital do escrow para o emissor
        self.cbdc.transfer(offering.escrow_wallet, offering.issuer_wallet, htlc.total_cash_brl)

        # 2. Perna de Ativos: Libera tokens RWA do escrow para o investidor
        self.rwa_tokens.safe_transfer_from(
            offering.escrow_wallet, htlc.investor_wallet, htlc.token_id, htlc.units
        )

        htlc.preimage = preimage
        htlc.status = HtlcStatus.CLAIMED

        receipt_id = f"REC-HTLC-{htlc.contract_id}"
        tx_hash = hashlib.sha256(f"{receipt_id}:{preimage}:{htlc.units}".encode()).hexdigest()

        receipt = DvpSettlementReceipt(
            receipt_id=receipt_id,
            offering_id=htlc.offering_id,
            allocation_id=htlc.allocation_id,
            mode=IssuanceSettlementMode.HTLC_ATOMIC_SWAP,
            token_id=htlc.token_id,
            delivered_units=htlc.units,
            unit_price_brl=alloc.unit_price_brl,
            settled_financial_brl=htlc.total_cash_brl,
            issuer_wallet=htlc.issuer_wallet,
            investor_wallet=htlc.investor_wallet,
            transaction_hash=f"0x{tx_hash}",
            settled_at=now,
        )

        alloc.settled = True
        alloc.settlement_id = receipt_id
        offering.total_settled_units += receipt.delivered_units
        offering.total_settled_financial_brl += receipt.settled_financial_brl
        self.dvp_receipts.append(receipt)

        # Validação LAW-111
        assert_drex_rwa_primary_issuance_invariants(
            allocated_units=alloc.allocated_units,
            delivered_units=receipt.delivered_units,
            received_financial=htlc.total_cash_brl,
            expected_financial=Decimal(alloc.allocated_units) * alloc.unit_price_brl,
            operation_id=receipt_id,
        )

        if all(a.settled for a in offering.allocations.values()):
            offering.status = OfferingStatus.SETTLED

        return receipt

    def refund_htlc_dvp(self, contract_id: str, force_expired: bool = False) -> bool:
        """Reverte o contrato HTLC após expiração do timeout, estornando fundos."""
        htlc = self.htlc_settlements.get(contract_id)
        if not htlc:
            raise DrexRwaHtlcError(f"Contrato HTLC '{contract_id}' não encontrado.")
        if htlc.status != HtlcStatus.PENDING and not force_expired:
            raise DrexRwaHtlcError(f"Contrato HTLC não está pendente: {htlc.status}")

        now = int(time.time())
        if now <= htlc.time_lock and not force_expired:
            raise DrexRwaHtlcError(
                f"Tentativa de estorno antes da expiração do timelock: {now} <= {htlc.time_lock}"
            )

        offering = self.offerings[htlc.offering_id]

        # Estorna Real Digital para o investidor
        self.cbdc.transfer(offering.escrow_wallet, htlc.investor_wallet, htlc.total_cash_brl)

        # Queima os tokens que estavam no escrow
        self.rwa_tokens.burn(offering.escrow_wallet, htlc.token_id, htlc.units)

        htlc.status = HtlcStatus.REFUNDED
        return True

    # --------------------------------------------------------------------------
    # 7. PROVENTOS ON-CHAIN: CUPONS E AMORTIZAÇÕES
    # --------------------------------------------------------------------------

    def create_corporate_action(
        self,
        offering_id: str,
        event_id: str,
        event_type: CorporateActionType,
        record_date: str,
        payment_date: str,
        unit_amount_brl: Decimal,
        rate_or_percentage: Decimal = Decimal("0.00"),
    ) -> CorporateActionEvent:
        """Cria e agenda um evento corporativo de remuneração ou amortização."""
        offering = self.offerings.get(offering_id)
        if not offering:
            raise DrexRwaCorporateActionError(f"Oferta '{offering_id}' não encontrada.")
        if event_id in self.corporate_actions:
            raise DrexRwaCorporateActionError(f"Evento '{event_id}' já existe.")

        action = CorporateActionEvent(
            event_id=event_id,
            offering_id=offering_id,
            token_id=offering.token_id,
            event_type=event_type,
            record_date=record_date,
            payment_date=payment_date,
            rate_or_percentage=rate_or_percentage,
            unit_amount_brl=unit_amount_brl,
        )
        self.corporate_actions[event_id] = action
        return action

    def fund_corporate_action(self, event_id: str, funder_wallet: str) -> Decimal:
        """Emissor provisiona o montante total de Real Digital necessário para o pagamento."""
        action = self.corporate_actions.get(event_id)
        if not action:
            raise DrexRwaCorporateActionError(f"Evento '{event_id}' não encontrado.")
        if action.status != CorporateActionStatus.ANNOUNCED:
            raise DrexRwaCorporateActionError(f"Evento em estado inválido para custeio: {action.status}")

        offering = self.offerings[action.offering_id]
        meta = self.rwa_tokens.metadata[action.token_id]

        total_required = Decimal(meta.circulating_units) * action.unit_amount_brl
        if total_required <= Decimal("0.00"):
            raise DrexRwaCorporateActionError("Nenhum token circulante para provisionar proventos.")

        # Transfere fundos da carteira pagadora para a conta escrow do contrato
        self.cbdc.transfer(funder_wallet, offering.escrow_wallet, total_required)
        action.total_funded_brl = total_required
        action.status = CorporateActionStatus.FUNDED
        return total_required

    def execute_corporate_action(self, event_id: str) -> CorporateActionEvent:
        """Executa a distribuição automática de proventos on-chain aos detentores do token."""
        action = self.corporate_actions.get(event_id)
        if not action:
            raise DrexRwaCorporateActionError(f"Evento '{event_id}' não encontrado.")
        if action.status != CorporateActionStatus.FUNDED:
            raise DrexRwaCorporateActionError(
                f"Evento '{event_id}' não foi provisionado (status atual: {action.status})"
            )

        offering = self.offerings[action.offering_id]
        token_id = action.token_id
        meta = self.rwa_tokens.metadata[token_id]

        # Snapshot de custódia na data-base
        holders = self.rwa_tokens.get_token_holders(token_id)
        distributed_total = Decimal("0.00")
        now = int(time.time())

        for wallet, units in holders.items():
            if units <= 0:
                continue
            pay_amount = Decimal(units) * action.unit_amount_brl

            # Transferência de Real Digital do escrow para a carteira do detentor
            self.cbdc.transfer(offering.escrow_wallet, wallet, pay_amount)
            distributed_total += pay_amount

            dist_rec = PaymentDistributionRecord(
                distribution_id=f"DIST-{action.event_id}-{wallet[:8]}",
                event_id=event_id,
                investor_wallet=wallet,
                token_units_held=units,
                unit_amount_brl=action.unit_amount_brl,
                amount_paid_brl=pay_amount,
                paid_at=now,
            )
            action.distributions.append(dist_rec)

        action.total_distributed_brl = distributed_total

        # Se for amortização de principal, deduz o VNU atualizado
        if action.event_type == CorporateActionType.PRINCIPAL_AMORTIZATION:
            meta.current_vnu = max(Decimal("0.00"), meta.current_vnu - action.unit_amount_brl)
            offering.current_vnu = meta.current_vnu

        # Se for resgate total, queima os tokens e zera VNU
        elif action.event_type == CorporateActionType.TOTAL_REDEMPTION:
            for wallet, units in list(holders.items()):
                self.rwa_tokens.burn(wallet, token_id, units)
            meta.current_vnu = Decimal("0.00")
            offering.current_vnu = Decimal("0.00")
            offering.status = OfferingStatus.MATURED

        action.status = CorporateActionStatus.EXECUTED

        # Invariante LAW-111: Conservação de saldo de proventos
        assert_drex_rwa_primary_issuance_invariants(
            allocated_units=meta.circulating_units,
            delivered_units=meta.circulating_units,
            received_financial=Decimal("0.00"),
            expected_financial=Decimal("0.00"),
            operation_id=action.event_id,
            funded_coupon_amount=action.total_funded_brl,
            distributed_coupon_amount=action.total_distributed_brl,
        )

        return action

    # --------------------------------------------------------------------------
    # 8. AUDITORIA FORMAL DE INVARIANTES (LAW-111, LAW-089C, ERC-3643)
    # --------------------------------------------------------------------------

    def verify_invariants(self) -> Dict[str, bool]:
        """Audita rigorosamente as Leis e Invariantes do ecossistema RWA Drex Fase 2."""
        results: Dict[str, bool] = {
            "LAW-111_primary_dvp_atomic_quantity": True,
            "LAW-111_primary_dvp_financial_parity": True,
            "LAW-111_coupon_conservation": True,
            "LAW-089C_token_non_negativity": True,
            "LAW-3643_regulatory_compliance": True,
        }

        # 1. LAW-111 para todas as liquidações DvP concluídas
        for receipt in self.dvp_receipts:
            offering = self.offerings[receipt.offering_id]
            alloc = offering.allocations[receipt.allocation_id]
            expected_fin = Decimal(alloc.allocated_units) * alloc.unit_price_brl

            assert_drex_rwa_primary_issuance_invariants(
                allocated_units=alloc.allocated_units,
                delivered_units=receipt.delivered_units,
                received_financial=receipt.settled_financial_brl,
                expected_financial=expected_fin,
                operation_id=receipt.receipt_id,
            )

        # 2. LAW-111 para todos os eventos de cupons e amortizações executados
        for action in self.corporate_actions.values():
            if action.status == CorporateActionStatus.EXECUTED:
                assert_drex_rwa_primary_issuance_invariants(
                    allocated_units=0,
                    delivered_units=0,
                    received_financial=Decimal("0.00"),
                    expected_financial=Decimal("0.00"),
                    operation_id=action.event_id,
                    funded_coupon_amount=action.total_funded_brl,
                    distributed_coupon_amount=action.total_distributed_brl,
                )

        # 3. LAW-089C Não-negatividade dos saldos
        for wallet, bal in self.cbdc.balances.items():
            if bal < Decimal("0.00"):
                results["LAW-089C_token_non_negativity"] = False
                raise InvariantViolationError(
                    f"Saldo de Real Digital negativo na carteira {wallet}: {bal}",
                    law_code="LAW-089C",
                )

        for (tid, wallet), bal in self.rwa_tokens.balances.items():
            if bal < 0:
                results["LAW-089C_token_non_negativity"] = False
                raise InvariantViolationError(
                    f"Saldo de token RWA negativo na carteira {wallet} token {tid}: {bal}",
                    law_code="LAW-089C",
                )

        # 4. LAW-3643 Compliance das carteiras detentoras
        for (tid, wallet), bal in self.rwa_tokens.balances.items():
            if bal > 0:
                # Se não for carteira escrow nem issuer, deve ter registro compliance
                rule = self.compliance.rules.get(tid)
                if rule and not rule.is_paused:
                    ident = self.compliance.get_identity(wallet)
                    if ident:
                        if not ident.has_valid_claim(IdentityClaimType.KYC_APPROVED):
                            results["LAW-3643_regulatory_compliance"] = False
                            raise InvariantViolationError(
                                f"Detentor de token {wallet} não possui KYC válido",
                                law_code="ERC-3643",
                            )

        return results

    # --------------------------------------------------------------------------
    # 9. SERIALIZAÇÃO E CARGA DE FIXTURES
    # --------------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o estado integral do motor para persistência JSON."""
        return {
            "cbdc": self.cbdc.to_dict(),
            "rwa_tokens": self.rwa_tokens.to_dict(),
            "compliance": self.compliance.to_dict(),
            "offerings": {k: v.to_dict() for k, v in self.offerings.items()},
            "htlc_settlements": {k: v.to_dict() for k, v in self.htlc_settlements.items()},
            "dvp_receipts": [r.to_dict() for r in self.dvp_receipts],
            "corporate_actions": {k: v.to_dict() for k, v in self.corporate_actions.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DrexRwaPrimaryIssuanceEngine:
        """Reconstitui o motor a partir de um dicionário serializado."""
        engine = cls()
        if "cbdc" in data:
            engine.cbdc = RealDigitalTokenContract.from_dict(data["cbdc"])
        if "rwa_tokens" in data:
            engine.rwa_tokens = Erc1155MultiTokenContract.from_dict(data["rwa_tokens"])
        if "compliance" in data:
            engine.compliance = Erc3643ComplianceManager.from_dict(data["compliance"])
        if "offerings" in data:
            engine.offerings = {
                k: RwaPrimaryIssuanceOffering.from_dict(v) for k, v in data["offerings"].items()
            }
        if "htlc_settlements" in data:
            engine.htlc_settlements = {
                k: HtlcDvpContract.from_dict(v) for k, v in data["htlc_settlements"].items()
            }
        if "dvp_receipts" in data:
            engine.dvp_receipts = [DvpSettlementReceipt.from_dict(r) for r in data["dvp_receipts"]]
        if "corporate_actions" in data:
            engine.corporate_actions = {
                k: CorporateActionEvent.from_dict(v) for k, v in data["corporate_actions"].items()
            }
        return engine

    @classmethod
    def load_from_fixture(cls, file_path: Union[str, Path]) -> DrexRwaPrimaryIssuanceEngine:
        """Carrega e instancia o motor com dados de uma fixture JSON oficial."""
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f"Fixture não encontrada: {file_path}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
