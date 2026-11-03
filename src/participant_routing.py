"""Motor de governança e roteamento de operações por perfil de participante B3 (FL-020 e FL-018).

Este módulo implementa a validação determinística de permissões operacionais,
segregação patrimonial entre contas master e contas de investimento (CVM 175/555),
resolução de rotas institucionais (Puma, RTC Give-Up e NoMe Balcão) e
verificação de conformidade de conectividade mTLS / IPSec (RCC).

Zero dependências externas (apenas Python Standard Library).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import Enum
import ipaddress
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


class ParticipantType(str, Enum):
    """Categorias formais de participantes da Câmara e Depositária B3."""

    PN = "PN"  # Participante Não Liquidante
    PNP = "PNP"  # Participante Negociador Pleno
    PL = "PL"  # Participante Liquidante (Clearing Member)
    CUSTODIAN = "CUSTODIAN"  # Custodiante Central / Banco Custodiante CSD
    ASSET_MANAGER = "ASSET_MANAGER"  # Gestora de Recursos / Investidor Institucional


class SystemTarget(str, Enum):
    """Sistemas e barramentos da infraestrutura B3."""

    PUMA = "PUMA"  # PUMA Trading System (Order Entry FIX)
    RTC = "RTC"  # Real-Time Clearing (Alocação e Give-Up)
    NOME = "NOME"  # NoMe Derivativos e Registro de Balcão


class RoutingStatus(str, Enum):
    """Status da avaliação de rota."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RoutingReasonCode(str, Enum):
    """Códigos padronizados de governança e validação de roteamento."""

    OK = "OK"
    ERR_PARTICIPANT_NOT_FOUND = "ERR_PARTICIPANT_NOT_FOUND"
    ERR_PARTICIPANT_INACTIVE = "ERR_PARTICIPANT_INACTIVE"
    ERR_SYSTEM_NOT_PERMITTED = "ERR_SYSTEM_NOT_PERMITTED"
    ERR_PN_DIRECT_SETTLEMENT_PROHIBITED = "ERR_PN_DIRECT_SETTLEMENT_PROHIBITED"
    ERR_MISSING_CLEARING_MEMBER = "ERR_MISSING_CLEARING_MEMBER"
    ERR_CLEARING_MEMBER_NOT_FOUND = "ERR_CLEARING_MEMBER_NOT_FOUND"
    ERR_CLEARING_MEMBER_INACTIVE = "ERR_CLEARING_MEMBER_INACTIVE"
    ERR_CLEARING_MEMBER_NOT_PL = "ERR_CLEARING_MEMBER_NOT_PL"
    ERR_CLEARING_MEMBER_MISMATCH = "ERR_CLEARING_MEMBER_MISMATCH"
    ERR_INVALID_GIVE_UP_RELATION = "ERR_INVALID_GIVE_UP_RELATION"
    ERR_UNAUTHORIZED_SEGREGATED_ACCOUNT = "ERR_UNAUTHORIZED_SEGREGATED_ACCOUNT"
    ERR_CONNECTIVITY_MISMATCH = "ERR_CONNECTIVITY_MISMATCH"
    ERR_MISSING_CUSTODY_ACCOUNT = "ERR_MISSING_CUSTODY_ACCOUNT"
    ERR_ASSET_MANAGER_DIRECT_EXECUTION = "ERR_ASSET_MANAGER_DIRECT_EXECUTION"
    ERR_CUSTODIAN_DIRECT_EXECUTION = "ERR_CUSTODIAN_DIRECT_EXECUTION"
    ERR_INVALID_QUANTITY = "ERR_INVALID_QUANTITY"
    ERR_EMPTY_ACCOUNT = "ERR_EMPTY_ACCOUNT"
    ERR_RULE_NOT_FOUND = "ERR_RULE_NOT_FOUND"
    ERR_CONTRAPARTY_NOT_FOUND = "ERR_CONTRAPARTY_NOT_FOUND"
    ERR_CONTRAPARTY_INACTIVE = "ERR_CONTRAPARTY_INACTIVE"


@dataclass(frozen=True)
class ConnectivityProfile:
    """Parâmetros de conectividade e credenciais de rede seguras (FL-018)."""

    mtls_enabled: bool = True
    certificate_cn: Optional[str] = None
    ipsec_tunnel_id: Optional[str] = None
    rcc_circuit_id: Optional[str] = None
    ip_allowlist: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional[ConnectivityProfile]:
        """Constrói perfil de conectividade a partir de dicionário."""
        if not data:
            return None
        return cls(
            mtls_enabled=bool(data.get("mtls_enabled", True)),
            certificate_cn=data.get("certificate_cn"),
            ipsec_tunnel_id=data.get("ipsec_tunnel_id"),
            rcc_circuit_id=data.get("rcc_circuit_id"),
            ip_allowlist=list(data.get("ip_allowlist", [])),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa perfil de conectividade para dicionário."""
        return asdict(self)


@dataclass(frozen=True)
class ParticipantProfile:
    """Perfil cadastral, permissões e parâmetros operacionais do participante B3."""

    participant_id: int
    name: str
    participant_type: ParticipantType
    active: bool = True
    permitted_systems: List[SystemTarget] = field(default_factory=list)
    clearing_member_id: Optional[int] = None
    default_settlement_account: Optional[str] = None
    master_account: Optional[str] = None
    custody_account: Optional[str] = None
    can_settle_directly: bool = False
    can_execute_trades: bool = False
    segregated_accounts: List[str] = field(default_factory=list)
    connectivity: Optional[ConnectivityProfile] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ParticipantProfile:
        """Cria ParticipantProfile a partir de dicionário."""
        raw_systems = data.get("permitted_systems", [])
        systems = [
            SystemTarget(s) if not isinstance(s, SystemTarget) else s
            for s in raw_systems
        ]
        raw_type = data.get("participant_type", ParticipantType.PN)
        p_type = raw_type if isinstance(raw_type, ParticipantType) else ParticipantType(raw_type)

        return cls(
            participant_id=int(data["participant_id"]),
            name=str(data.get("name", "")),
            participant_type=p_type,
            active=bool(data.get("active", True)),
            permitted_systems=systems,
            clearing_member_id=(
                int(data["clearing_member_id"])
                if data.get("clearing_member_id") is not None
                else None
            ),
            default_settlement_account=data.get("default_settlement_account"),
            master_account=data.get("master_account"),
            custody_account=data.get("custody_account"),
            can_settle_directly=bool(data.get("can_settle_directly", False)),
            can_execute_trades=bool(data.get("can_execute_trades", False)),
            segregated_accounts=list(data.get("segregated_accounts", [])),
            connectivity=ConnectivityProfile.from_dict(data.get("connectivity")),
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa perfil do participante para dicionário."""
        d = asdict(self)
        d["participant_type"] = self.participant_type.value
        d["permitted_systems"] = [s.value for s in self.permitted_systems]
        return d


@dataclass(frozen=True)
class RoutingRule:
    """Regra determinística de roteamento, give-up e liquidação autorizada."""

    rule_id: str
    source_participant_id: int
    target_system: SystemTarget
    clearing_member_id: Optional[int] = None
    custodian_id: Optional[int] = None
    allowed_accounts: List[str] = field(default_factory=list)
    allow_give_up: bool = False
    give_up_parties: List[int] = field(default_factory=list)
    active: bool = True
    description: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RoutingRule:
        """Instancia RoutingRule a partir de dicionário."""
        raw_target = data.get("target_system", SystemTarget.PUMA)
        target = raw_target if isinstance(raw_target, SystemTarget) else SystemTarget(raw_target)
        give_up_parties = [int(p) for p in data.get("give_up_parties", [])]

        return cls(
            rule_id=str(data["rule_id"]),
            source_participant_id=int(data["source_participant_id"]),
            target_system=target,
            clearing_member_id=(
                int(data["clearing_member_id"])
                if data.get("clearing_member_id") is not None
                else None
            ),
            custodian_id=(
                int(data["custodian_id"])
                if data.get("custodian_id") is not None
                else None
            ),
            allowed_accounts=list(data.get("allowed_accounts", [])),
            allow_give_up=bool(data.get("allow_give_up", False)),
            give_up_parties=give_up_parties,
            active=bool(data.get("active", True)),
            description=str(data.get("description", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa regra de roteamento."""
        d = asdict(self)
        d["target_system"] = self.target_system.value
        return d


@dataclass(frozen=True)
class OrderRouteRequest:
    """Requisição de roteamento de ordem no PUMA Trading System."""

    participant_id: int
    account: str
    symbol: str
    quantity: Decimal
    clearing_member_id: Optional[int] = None
    client_id: Optional[str] = None
    is_direct_settlement_requested: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OrderRouteRequest:
        """Cria requisição a partir de dicionário."""
        return cls(
            participant_id=int(data["participant_id"]),
            account=str(data["account"]),
            symbol=str(data["symbol"]).upper(),
            quantity=Decimal(str(data["quantity"])),
            clearing_member_id=(
                int(data["clearing_member_id"])
                if data.get("clearing_member_id") is not None
                else None
            ),
            client_id=data.get("client_id"),
            is_direct_settlement_requested=bool(
                data.get("is_direct_settlement_requested", False)
            ),
        )


@dataclass(frozen=True)
class AllocationRouteRequest:
    """Requisição de alocação e repasse institucional no RTC (FL-008 / FL-020)."""

    executing_broker_id: int
    trade_ref_id: str
    master_account: str
    target_investor_account: str
    target_clearing_member_id: int
    target_custodian_id: Optional[int] = None
    allocated_quantity: Decimal = Decimal("0")
    give_up: bool = True
    asset_manager_id: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AllocationRouteRequest:
        """Cria requisição de alocação a partir de dicionário."""
        return cls(
            executing_broker_id=int(data["executing_broker_id"]),
            trade_ref_id=str(data["trade_ref_id"]),
            master_account=str(data["master_account"]),
            target_investor_account=str(data["target_investor_account"]),
            target_clearing_member_id=int(data["target_clearing_member_id"]),
            target_custodian_id=(
                int(data["target_custodian_id"])
                if data.get("target_custodian_id") is not None
                else None
            ),
            allocated_quantity=Decimal(str(data.get("allocated_quantity", 0))),
            give_up=bool(data.get("give_up", True)),
            asset_manager_id=(
                int(data["asset_manager_id"])
                if data.get("asset_manager_id") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class NoMeRegistrationRouteRequest:
    """Requisição de registro bilateral de balcão no NoMe."""

    participant_id: int
    contract_type: str
    contraparty_id: int
    settlement_account: Optional[str] = None
    custody_account: Optional[str] = None
    clearing_member_id: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NoMeRegistrationRouteRequest:
        """Cria requisição NoMe a partir de dicionário."""
        return cls(
            participant_id=int(data["participant_id"]),
            contract_type=str(data["contract_type"]),
            contraparty_id=int(data["contraparty_id"]),
            settlement_account=data.get("settlement_account"),
            custody_account=data.get("custody_account"),
            clearing_member_id=(
                int(data["clearing_member_id"])
                if data.get("clearing_member_id") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class RoutingValidationResult:
    """Resultado formal do roteamento institucional e avaliação de permissões."""

    status: RoutingStatus
    is_valid: bool
    reason_code: RoutingReasonCode
    message: str
    source_participant_id: int
    target_system: SystemTarget
    resolved_clearing_member_id: Optional[int] = None
    resolved_settlement_account: Optional[str] = None
    resolved_custody_account: Optional[str] = None
    route_path: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @classmethod
    def approve(
        cls,
        source_participant_id: int,
        target_system: SystemTarget,
        message: str,
        route_path: List[str],
        resolved_clearing_member_id: Optional[int] = None,
        resolved_settlement_account: Optional[str] = None,
        resolved_custody_account: Optional[str] = None,
    ) -> RoutingValidationResult:
        """Fábrica para retorno aprovado."""
        return cls(
            status=RoutingStatus.APPROVED,
            is_valid=True,
            reason_code=RoutingReasonCode.OK,
            message=message,
            source_participant_id=source_participant_id,
            target_system=target_system,
            resolved_clearing_member_id=resolved_clearing_member_id,
            resolved_settlement_account=resolved_settlement_account,
            resolved_custody_account=resolved_custody_account,
            route_path=route_path,
            errors=[],
        )

    @classmethod
    def reject(
        cls,
        source_participant_id: int,
        target_system: SystemTarget,
        reason_code: RoutingReasonCode,
        message: str,
        errors: Optional[List[str]] = None,
    ) -> RoutingValidationResult:
        """Fábrica para retorno rejeitado."""
        err_list = errors or [message]
        return cls(
            status=RoutingStatus.REJECTED,
            is_valid=False,
            reason_code=reason_code,
            message=message,
            source_participant_id=source_participant_id,
            target_system=target_system,
            resolved_clearing_member_id=None,
            resolved_settlement_account=None,
            resolved_custody_account=None,
            route_path=[],
            errors=err_list,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o resultado da validação."""
        d = asdict(self)
        d["status"] = self.status.value
        d["reason_code"] = self.reason_code.value
        d["target_system"] = self.target_system.value
        return d


class ParticipantRouter:
    """Motor de resolução de rotas, governança de perfis e conformidade B3."""

    def __init__(
        self,
        profiles: Optional[Sequence[ParticipantProfile]] = None,
        rules: Optional[Sequence[RoutingRule]] = None,
    ) -> None:
        self._profiles: Dict[int, ParticipantProfile] = {}
        self._rules: Dict[str, RoutingRule] = {}

        if profiles:
            for p in profiles:
                self.register_participant(p)

        if rules:
            for r in rules:
                self.register_rule(r)

    def register_participant(self, profile: ParticipantProfile) -> None:
        """Registra ou atualiza um perfil de participante."""
        self._profiles[profile.participant_id] = profile

    def register_rule(self, rule: RoutingRule) -> None:
        """Registra ou atualiza uma regra de roteamento."""
        self._rules[rule.rule_id] = rule

    def get_participant(self, participant_id: int) -> Optional[ParticipantProfile]:
        """Obtém participante pelo identificador B3."""
        return self._profiles.get(participant_id)

    def get_rule(self, rule_id: str) -> Optional[RoutingRule]:
        """Obtém regra pelo ID."""
        return self._rules.get(rule_id)

    @property
    def participants_count(self) -> int:
        """Retorna o número de participantes cadastrados."""
        return len(self._profiles)

    @property
    def rules_count(self) -> int:
        """Retorna o número de regras cadastradas."""
        return len(self._rules)

    # -------------------------------------------------------------------------
    # Construtores auxiliares a partir de arquivos e dicionários
    # -------------------------------------------------------------------------
    @classmethod
    def from_dict(
        cls,
        profiles_data: Dict[str, Any],
        rules_data: Optional[Dict[str, Any]] = None,
    ) -> ParticipantRouter:
        """Instancia ParticipantRouter a partir de dicionários decodificados."""
        profiles: List[ParticipantProfile] = []
        for item in profiles_data.get("participants", []):
            profiles.append(ParticipantProfile.from_dict(item))

        rules: List[RoutingRule] = []
        if rules_data:
            for item in rules_data.get("rules", []):
                rules.append(RoutingRule.from_dict(item))

        return cls(profiles=profiles, rules=rules)

    @classmethod
    def from_files(
        cls,
        profiles_path: Union[str, Path],
        rules_path: Optional[Union[str, Path]] = None,
    ) -> ParticipantRouter:
        """Instancia ParticipantRouter a partir de caminhos de arquivos JSON."""
        p_path = Path(profiles_path)
        with open(p_path, "r", encoding="utf-8") as f:
            profiles_data = json.load(f)

        rules_data: Optional[Dict[str, Any]] = None
        if rules_path:
            r_path = Path(rules_path)
            with open(r_path, "r", encoding="utf-8") as f:
                rules_data = json.load(f)

        return cls.from_dict(profiles_data, rules_data)

    # -------------------------------------------------------------------------
    # Validação Básica de Participante e Conectividade (FL-018)
    # -------------------------------------------------------------------------
    def _validate_basic_participant(
        self, participant_id: int, target_system: SystemTarget
    ) -> Tuple[bool, Optional[RoutingValidationResult], Optional[ParticipantProfile]]:
        """Valida existência, status ativo e permissão de sistema para o participante."""
        profile = self.get_participant(participant_id)
        if not profile:
            res = RoutingValidationResult.reject(
                source_participant_id=participant_id,
                target_system=target_system,
                reason_code=RoutingReasonCode.ERR_PARTICIPANT_NOT_FOUND,
                message=f"Participante ID {participant_id} não cadastrado na base B3.",
            )
            return False, res, None

        if not profile.active:
            res = RoutingValidationResult.reject(
                source_participant_id=participant_id,
                target_system=target_system,
                reason_code=RoutingReasonCode.ERR_PARTICIPANT_INACTIVE,
                message=f"Participante ID {participant_id} ({profile.name}) encontra-se inativo ou suspenso perante a B3.",
            )
            return False, res, profile

        if target_system not in profile.permitted_systems:
            res = RoutingValidationResult.reject(
                source_participant_id=participant_id,
                target_system=target_system,
                reason_code=RoutingReasonCode.ERR_SYSTEM_NOT_PERMITTED,
                message=(
                    f"Participante {profile.participant_type.value}:{participant_id} "
                    f"não possui permissão para acessar o sistema {target_system.value}."
                ),
            )
            return False, res, profile

        return True, None, profile

    def validate_connectivity(
        self,
        participant_id: int,
        client_ip: Optional[str] = None,
        client_cert_cn: Optional[str] = None,
    ) -> RoutingValidationResult:
        """Valida credenciais mTLS e conformidade de rede IPSec/RCC (FL-018)."""
        valid, err_res, profile = self._validate_basic_participant(
            participant_id, SystemTarget.PUMA
        )
        if not valid and err_res and err_res.reason_code != RoutingReasonCode.ERR_SYSTEM_NOT_PERMITTED:
            return err_res

        assert profile is not None
        conn = profile.connectivity
        if not conn:
            return RoutingValidationResult.approve(
                source_participant_id=participant_id,
                target_system=SystemTarget.PUMA,
                message=f"Participante {participant_id} sem restrições de conectividade explícitas.",
                route_path=[f"PARTICIPANT:{participant_id}", "NETWORK:OPEN"],
            )

        # Validação do certificado mTLS (CN)
        if conn.mtls_enabled and conn.certificate_cn and client_cert_cn is not None:
            if client_cert_cn.lower().strip() != conn.certificate_cn.lower().strip():
                return RoutingValidationResult.reject(
                    source_participant_id=participant_id,
                    target_system=SystemTarget.PUMA,
                    reason_code=RoutingReasonCode.ERR_CONNECTIVITY_MISMATCH,
                    message=(
                        f"Divergência de certificado mTLS para participante {participant_id}: "
                        f"esperado CN='{conn.certificate_cn}', recebido CN='{client_cert_cn}'."
                    ),
                )

        # Validação de allowlist de sub-redes/IPs RCC
        if client_ip and conn.ip_allowlist:
            client_ip_clean = client_ip.strip()
            ip_obj = ipaddress.ip_address(client_ip_clean)
            ip_matched = False
            for entry in conn.ip_allowlist:
                try:
                    if "/" in entry:
                        net = ipaddress.ip_network(entry, strict=False)
                        if ip_obj in net:
                            ip_matched = True
                            break
                    else:
                        if ip_obj == ipaddress.ip_address(entry):
                            ip_matched = True
                            break
                except ValueError:
                    continue

            if not ip_matched:
                return RoutingValidationResult.reject(
                    source_participant_id=participant_id,
                    target_system=SystemTarget.PUMA,
                    reason_code=RoutingReasonCode.ERR_CONNECTIVITY_MISMATCH,
                    message=(
                        f"Origem IP {client_ip} não autorizada na allowlist RCC do "
                        f"participante {participant_id}."
                    ),
                )

        route_tags = [f"PARTICIPANT:{participant_id}"]
        if conn.rcc_circuit_id:
            route_tags.append(f"RCC:{conn.rcc_circuit_id}")
        if conn.ipsec_tunnel_id:
            route_tags.append(f"IPSEC:{conn.ipsec_tunnel_id}")
        if conn.certificate_cn:
            route_tags.append(f"mTLS:{conn.certificate_cn}")

        return RoutingValidationResult.approve(
            source_participant_id=participant_id,
            target_system=SystemTarget.PUMA,
            message="Sessão segura de conectividade validada com sucesso.",
            route_path=route_tags,
        )

    # -------------------------------------------------------------------------
    # Roteamento de Ordens no PUMA Trading System (FL-020)
    # -------------------------------------------------------------------------
    def route_puma_order(self, request: OrderRouteRequest) -> RoutingValidationResult:
        """Valida e resolve a rota determinística de envio de ordem para o PUMA."""
        valid, err_res, profile = self._validate_basic_participant(
            request.participant_id, SystemTarget.PUMA
        )
        if not valid:
            assert err_res is not None
            return err_res

        assert profile is not None

        # Vedações por Perfil
        if profile.participant_type == ParticipantType.ASSET_MANAGER:
            return RoutingValidationResult.reject(
                source_participant_id=request.participant_id,
                target_system=SystemTarget.PUMA,
                reason_code=RoutingReasonCode.ERR_ASSET_MANAGER_DIRECT_EXECUTION,
                message=(
                    f"Gestora de recursos (Asset Manager {profile.participant_id}) não possui "
                    f"permissão para execução direta no livro de ofertas do PUMA; "
                    f"ordem deve ser transmitida via corretora credenciada (PN ou PNP)."
                ),
            )

        if profile.participant_type == ParticipantType.CUSTODIAN:
            return RoutingValidationResult.reject(
                source_participant_id=request.participant_id,
                target_system=SystemTarget.PUMA,
                reason_code=RoutingReasonCode.ERR_CUSTODIAN_DIRECT_EXECUTION,
                message=(
                    f"Custodiante central ({profile.participant_id}) não está habilitado "
                    f"para envio de ordens ao PUMA Trading System; atuação restrita a custódia e liquidação."
                ),
            )

        if not profile.can_execute_trades:
            return RoutingValidationResult.reject(
                source_participant_id=request.participant_id,
                target_system=SystemTarget.PUMA,
                reason_code=RoutingReasonCode.ERR_SYSTEM_NOT_PERMITTED,
                message=f"Participante {profile.name} não possui habilitação para executar negociações.",
            )

        # Validação quantitativa e estrutural
        if request.quantity <= 0:
            return RoutingValidationResult.reject(
                source_participant_id=request.participant_id,
                target_system=SystemTarget.PUMA,
                reason_code=RoutingReasonCode.ERR_INVALID_QUANTITY,
                message=f"Quantidade da ordem deve ser estritamente positiva: {request.quantity}.",
            )

        if not request.account or not request.account.strip():
            return RoutingValidationResult.reject(
                source_participant_id=request.participant_id,
                target_system=SystemTarget.PUMA,
                reason_code=RoutingReasonCode.ERR_EMPTY_ACCOUNT,
                message="Conta operacional da ordem é obrigatória e não pode ser vazia.",
            )

        # Validação estrita de Participante Não Liquidante (PN)
        if profile.participant_type == ParticipantType.PN:
            if request.is_direct_settlement_requested:
                return RoutingValidationResult.reject(
                    source_participant_id=request.participant_id,
                    target_system=SystemTarget.PUMA,
                    reason_code=RoutingReasonCode.ERR_PN_DIRECT_SETTLEMENT_PROHIBITED,
                    message=(
                        f"Participante Não Liquidante (PN {profile.participant_id}) é expressamente "
                        f"proibido de realizar liquidação financeira direta perante a Câmara B3."
                    ),
                )

            # Resolução do Clearing Member (PL)
            target_cm_id = request.clearing_member_id or profile.clearing_member_id
            if target_cm_id is None:
                return RoutingValidationResult.reject(
                    source_participant_id=request.participant_id,
                    target_system=SystemTarget.PUMA,
                    reason_code=RoutingReasonCode.ERR_MISSING_CLEARING_MEMBER,
                    message=(
                        f"Ordem de Participante Não Liquidante (PN {profile.participant_id}) rejeitada: "
                        f"nenhum Participante Liquidante (PL) associado ou informado na requisição."
                    ),
                )

            cm_profile = self.get_participant(target_cm_id)
            if not cm_profile:
                return RoutingValidationResult.reject(
                    source_participant_id=request.participant_id,
                    target_system=SystemTarget.PUMA,
                    reason_code=RoutingReasonCode.ERR_CLEARING_MEMBER_NOT_FOUND,
                    message=f"Membro liquidante indicado ID {target_cm_id} não cadastrado na base B3.",
                )

            if not cm_profile.active:
                return RoutingValidationResult.reject(
                    source_participant_id=request.participant_id,
                    target_system=SystemTarget.PUMA,
                    reason_code=RoutingReasonCode.ERR_CLEARING_MEMBER_INACTIVE,
                    message=f"Membro liquidante indicado ID {target_cm_id} ({cm_profile.name}) está inativo.",
                )

            if cm_profile.participant_type != ParticipantType.PL:
                return RoutingValidationResult.reject(
                    source_participant_id=request.participant_id,
                    target_system=SystemTarget.PUMA,
                    reason_code=RoutingReasonCode.ERR_CLEARING_MEMBER_NOT_PL,
                    message=(
                        f"O membro de liquidação indicado ID {target_cm_id} possui perfil "
                        f"{cm_profile.participant_type.value}, mas a liquidação perante a Câmara "
                        f"exige obrigatoriamente perfil PL."
                    ),
                )

            route_path = [
                f"PN:{profile.participant_id}",
                f"PL:{cm_profile.participant_id}",
                "PUMA:MATCHING_ENGINE",
            ]
            settlement_acc = cm_profile.default_settlement_account

            return RoutingValidationResult.approve(
                source_participant_id=request.participant_id,
                target_system=SystemTarget.PUMA,
                message=(
                    f"Ordem aprovada: PN {profile.participant_id} roteada via "
                    f"membro liquidante PL {cm_profile.participant_id}."
                ),
                route_path=route_path,
                resolved_clearing_member_id=cm_profile.participant_id,
                resolved_settlement_account=settlement_acc,
                resolved_custody_account=profile.custody_account or cm_profile.custody_account,
            )

        # Validação de Participante Negociador Pleno (PNP)
        if profile.participant_type == ParticipantType.PNP:
            # Caso 1: Liquidação Própria
            if profile.can_settle_directly and (
                request.clearing_member_id is None
                or request.clearing_member_id == profile.participant_id
            ):
                route_path = [
                    f"PNP:{profile.participant_id}",
                    f"PL:{profile.participant_id}",
                    "PUMA:MATCHING_ENGINE",
                ]
                return RoutingValidationResult.approve(
                    source_participant_id=request.participant_id,
                    target_system=SystemTarget.PUMA,
                    message=(
                        f"Ordem aprovada: PNP {profile.participant_id} operando com "
                        f"autoliquidação direta perante a câmara B3."
                    ),
                    route_path=route_path,
                    resolved_clearing_member_id=profile.participant_id,
                    resolved_settlement_account=profile.default_settlement_account,
                    resolved_custody_account=profile.custody_account,
                )

            # Caso 2: Liquidação Delegada a Terceiro PL
            target_cm_id = request.clearing_member_id or profile.clearing_member_id
            if target_cm_id is None:
                return RoutingValidationResult.reject(
                    source_participant_id=request.participant_id,
                    target_system=SystemTarget.PUMA,
                    reason_code=RoutingReasonCode.ERR_MISSING_CLEARING_MEMBER,
                    message=(
                        f"Participante Negociador Pleno (PNP {profile.participant_id}) sem autoliquidação "
                        f"requer indicação de PL liquidante delegado."
                    ),
                )

            cm_profile = self.get_participant(target_cm_id)
            if not cm_profile or not cm_profile.active or cm_profile.participant_type != ParticipantType.PL:
                return RoutingValidationResult.reject(
                    source_participant_id=request.participant_id,
                    target_system=SystemTarget.PUMA,
                    reason_code=RoutingReasonCode.ERR_CLEARING_MEMBER_NOT_PL,
                    message=f"Liquidante delegado ID {target_cm_id} inválido, inativo ou não-PL.",
                )

            route_path = [
                f"PNP:{profile.participant_id}",
                f"PL:{cm_profile.participant_id}",
                "PUMA:MATCHING_ENGINE",
            ]
            return RoutingValidationResult.approve(
                source_participant_id=request.participant_id,
                target_system=SystemTarget.PUMA,
                message=(
                    f"Ordem aprovada: PNP {profile.participant_id} roteada com "
                    f"liquidação delegada ao PL {cm_profile.participant_id}."
                ),
                route_path=route_path,
                resolved_clearing_member_id=cm_profile.participant_id,
                resolved_settlement_account=cm_profile.default_settlement_account,
                resolved_custody_account=profile.custody_account or cm_profile.custody_account,
            )

        # Validação de Participante Liquidante (PL)
        if profile.participant_type == ParticipantType.PL:
            route_path = [
                f"PL:{profile.participant_id}",
                "PUMA:MATCHING_ENGINE",
            ]
            return RoutingValidationResult.approve(
                source_participant_id=request.participant_id,
                target_system=SystemTarget.PUMA,
                message=f"Ordem aprovada: membro liquidante PL {profile.participant_id} operando diretamente.",
                route_path=route_path,
                resolved_clearing_member_id=profile.participant_id,
                resolved_settlement_account=profile.default_settlement_account,
                resolved_custody_account=profile.custody_account,
            )

        return RoutingValidationResult.reject(
            source_participant_id=request.participant_id,
            target_system=SystemTarget.PUMA,
            reason_code=RoutingReasonCode.ERR_SYSTEM_NOT_PERMITTED,
            message=f"Perfil {profile.participant_type.value} não suportado para negociação no PUMA.",
        )

    # -------------------------------------------------------------------------
    # Roteamento e Governança de Alocação e Give-Up no RTC (FL-008 / FL-020)
    # -------------------------------------------------------------------------
    def route_rtc_allocation(
        self, request: AllocationRouteRequest
    ) -> RoutingValidationResult:
        """Valida e autoriza desdobramento e repasse (give-up) no barramento RTC."""
        # 1. Validação do Executing Broker
        valid, err_res, eb_profile = self._validate_basic_participant(
            request.executing_broker_id, SystemTarget.RTC
        )
        if not valid:
            assert err_res is not None
            return err_res
        assert eb_profile is not None

        # 2. Validação do Target Clearing Member (PL)
        target_cm = self.get_participant(request.target_clearing_member_id)
        if not target_cm:
            return RoutingValidationResult.reject(
                source_participant_id=request.executing_broker_id,
                target_system=SystemTarget.RTC,
                reason_code=RoutingReasonCode.ERR_CLEARING_MEMBER_NOT_FOUND,
                message=f"Target Clearing Member {request.target_clearing_member_id} não cadastrado na B3.",
            )

        if not target_cm.active:
            return RoutingValidationResult.reject(
                source_participant_id=request.executing_broker_id,
                target_system=SystemTarget.RTC,
                reason_code=RoutingReasonCode.ERR_CLEARING_MEMBER_INACTIVE,
                message=f"Target Clearing Member {request.target_clearing_member_id} está inativo.",
            )

        if target_cm.participant_type != ParticipantType.PL:
            return RoutingValidationResult.reject(
                source_participant_id=request.executing_broker_id,
                target_system=SystemTarget.RTC,
                reason_code=RoutingReasonCode.ERR_CLEARING_MEMBER_NOT_PL,
                message=(
                    f"Target Clearing Member {request.target_clearing_member_id} possui perfil "
                    f"{target_cm.participant_type.value}; destino de give-up exige perfil PL."
                ),
            )

        # 3. Validação de Relação Bilateral de Give-Up
        if request.give_up:
            is_authorized = False
            # Verifica se é uma alocação interna na mesma instituição
            if request.executing_broker_id == request.target_clearing_member_id:
                is_authorized = True
            else:
                # Procura por regra ativa de give-up entre o broker e o PL
                for rule in self._rules.values():
                    if (
                        rule.active
                        and rule.source_participant_id == request.executing_broker_id
                        and rule.target_system == SystemTarget.RTC
                        and rule.allow_give_up
                        and (
                            request.target_clearing_member_id in rule.give_up_parties
                            or rule.clearing_member_id == request.target_clearing_member_id
                        )
                    ):
                        is_authorized = True
                        break

            if not is_authorized:
                return RoutingValidationResult.reject(
                    source_participant_id=request.executing_broker_id,
                    target_system=SystemTarget.RTC,
                    reason_code=RoutingReasonCode.ERR_INVALID_GIVE_UP_RELATION,
                    message=(
                        f"Relação de Give-Up não homologada entre Executing Broker "
                        f"{request.executing_broker_id} e Clearing Member {request.target_clearing_member_id}."
                    ),
                )

        # 4. Validação de Segregação Patrimonial e Asset Manager (CVM 175 / 555)
        if request.asset_manager_id:
            am_profile = self.get_participant(request.asset_manager_id)
            if not am_profile:
                return RoutingValidationResult.reject(
                    source_participant_id=request.executing_broker_id,
                    target_system=SystemTarget.RTC,
                    reason_code=RoutingReasonCode.ERR_PARTICIPANT_NOT_FOUND,
                    message=f"Asset Manager ID {request.asset_manager_id} não localizada no cadastro.",
                )

            if not am_profile.active:
                return RoutingValidationResult.reject(
                    source_participant_id=request.executing_broker_id,
                    target_system=SystemTarget.RTC,
                    reason_code=RoutingReasonCode.ERR_PARTICIPANT_INACTIVE,
                    message=f"Asset Manager ID {request.asset_manager_id} está inativa.",
                )

            if am_profile.participant_type != ParticipantType.ASSET_MANAGER:
                return RoutingValidationResult.reject(
                    source_participant_id=request.executing_broker_id,
                    target_system=SystemTarget.RTC,
                    reason_code=RoutingReasonCode.ERR_SYSTEM_NOT_PERMITTED,
                    message=f"Participante {request.asset_manager_id} não é do tipo ASSET_MANAGER.",
                )

            # A conta alvo deve ser uma das contas segregadas da gestora
            if request.target_investor_account not in am_profile.segregated_accounts:
                return RoutingValidationResult.reject(
                    source_participant_id=request.executing_broker_id,
                    target_system=SystemTarget.RTC,
                    reason_code=RoutingReasonCode.ERR_UNAUTHORIZED_SEGREGATED_ACCOUNT,
                    message=(
                        f"Conta alvo {request.target_investor_account} não pertence ao portfólio "
                        f"de contas segregadas da gestora {am_profile.participant_id} ({am_profile.name})."
                    ),
                )

        # 5. Validação de Custodiante (CSD)
        custody_acc = None
        if request.target_custodian_id:
            cust_profile = self.get_participant(request.target_custodian_id)
            if not cust_profile or not cust_profile.active:
                return RoutingValidationResult.reject(
                    source_participant_id=request.executing_broker_id,
                    target_system=SystemTarget.RTC,
                    reason_code=RoutingReasonCode.ERR_MISSING_CUSTODY_ACCOUNT,
                    message=f"Custodiante indicado {request.target_custodian_id} não encontrado ou inativo.",
                )
            custody_acc = cust_profile.custody_account or cust_profile.default_settlement_account

        # 6. Rota Determinística de Alocação
        route_path = [
            f"EB:{request.executing_broker_id}",
            "RTC_GIVE_UP" if request.give_up else "RTC_INTERNAL_ALLOCATION",
            f"PL:{request.target_clearing_member_id}",
            f"ACCOUNT:{request.target_investor_account}",
        ]
        if request.target_custodian_id:
            route_path.append(f"CUSTODIAN:{request.target_custodian_id}")

        return RoutingValidationResult.approve(
            source_participant_id=request.executing_broker_id,
            target_system=SystemTarget.RTC,
            message=(
                f"Alocação RTC autorizada para conta segregada {request.target_investor_account} "
                f"via PL {request.target_clearing_member_id}."
            ),
            route_path=route_path,
            resolved_clearing_member_id=request.target_clearing_member_id,
            resolved_settlement_account=target_cm.default_settlement_account,
            resolved_custody_account=custody_acc or target_cm.custody_account,
        )

    # -------------------------------------------------------------------------
    # Roteamento e Registro no NoMe Balcão
    # -------------------------------------------------------------------------
    def route_nome_registration(
        self, request: NoMeRegistrationRouteRequest
    ) -> RoutingValidationResult:
        """Valida e autoriza registro de contratos derivativos e balcão no NoMe."""
        valid, err_res, profile = self._validate_basic_participant(
            request.participant_id, SystemTarget.NOME
        )
        if not valid:
            assert err_res is not None
            return err_res
        assert profile is not None

        # Validação da Contraparte
        contra = self.get_participant(request.contraparty_id)
        if not contra:
            return RoutingValidationResult.reject(
                source_participant_id=request.participant_id,
                target_system=SystemTarget.NOME,
                reason_code=RoutingReasonCode.ERR_CONTRAPARTY_NOT_FOUND,
                message=f"Contraparte ID {request.contraparty_id} não cadastrada no NoMe.",
            )

        if not contra.active:
            return RoutingValidationResult.reject(
                source_participant_id=request.participant_id,
                target_system=SystemTarget.NOME,
                reason_code=RoutingReasonCode.ERR_CONTRAPARTY_INACTIVE,
                message=f"Contraparte ID {request.contraparty_id} ({contra.name}) está inativa.",
            )

        # PN registrando no NoMe exige PL associado
        resolved_cm_id: Optional[int] = None
        if profile.participant_type == ParticipantType.PN:
            resolved_cm_id = request.clearing_member_id or profile.clearing_member_id
            if resolved_cm_id is None:
                return RoutingValidationResult.reject(
                    source_participant_id=request.participant_id,
                    target_system=SystemTarget.NOME,
                    reason_code=RoutingReasonCode.ERR_MISSING_CLEARING_MEMBER,
                    message=f"Registro NoMe por PN {profile.participant_id} exige indicação de PL liquidante.",
                )

            cm_prof = self.get_participant(resolved_cm_id)
            if not cm_prof or not cm_prof.active or cm_prof.participant_type != ParticipantType.PL:
                return RoutingValidationResult.reject(
                    source_participant_id=request.participant_id,
                    target_system=SystemTarget.NOME,
                    reason_code=RoutingReasonCode.ERR_CLEARING_MEMBER_NOT_PL,
                    message=f"Clearing member indicado ID {resolved_cm_id} inválido para registro NoMe.",
                )
        elif profile.participant_type in (ParticipantType.PL, ParticipantType.PNP):
            resolved_cm_id = profile.clearing_member_id or profile.participant_id

        route_path = [
            f"REGISTRAR:{profile.participant_type.value}:{profile.participant_id}",
            f"CONTRAPARTY:{contra.participant_type.value}:{contra.participant_id}",
            "NOME:REGISTRATION_ENGINE",
        ]
        if resolved_cm_id:
            route_path.insert(1, f"PL:{resolved_cm_id}")

        return RoutingValidationResult.approve(
            source_participant_id=request.participant_id,
            target_system=SystemTarget.NOME,
            message=f"Registro NoMe aprovado para contrato {request.contract_type}.",
            route_path=route_path,
            resolved_clearing_member_id=resolved_cm_id,
            resolved_settlement_account=request.settlement_account or profile.default_settlement_account,
            resolved_custody_account=request.custody_account or profile.custody_account,
        )
