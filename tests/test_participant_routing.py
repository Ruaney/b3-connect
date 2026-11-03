"""Testes automatizados para o motor de governança e roteamento de participantes B3 (FL-020 e FL-018)."""

from decimal import Decimal
from pathlib import Path
import sys
import unittest

# Garante que a raiz do repositório esteja no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.participant_routing import (
    AllocationRouteRequest,
    ConnectivityProfile,
    NoMeRegistrationRouteRequest,
    OrderRouteRequest,
    ParticipantProfile,
    ParticipantRouter,
    ParticipantType,
    RoutingReasonCode,
    RoutingRule,
    RoutingStatus,
    RoutingValidationResult,
    SystemTarget,
)

FIXTURES_DIR = ROOT_DIR / "fixtures"


class TestParticipantRouting(unittest.TestCase):
    """Bateria de testes unitários para a matriz de governança e roteamento institucional B3."""

    def setUp(self) -> None:
        self.profiles_fixture_path = FIXTURES_DIR / "participant_profiles.json"
        self.rules_fixture_path = FIXTURES_DIR / "routing_rules_valid.json"
        self.router = ParticipantRouter.from_files(
            profiles_path=self.profiles_fixture_path,
            rules_path=self.rules_fixture_path,
        )

    def test_fixtures_loaded_successfully(self) -> None:
        """Verifica se os catálogos sintéticos de participantes e regras foram carregados corretamente."""
        self.assertGreaterEqual(self.router.participants_count, 10)
        self.assertGreaterEqual(self.router.rules_count, 8)

        p88 = self.router.get_participant(88)
        self.assertIsNotNone(p88)
        self.assertEqual(p88.participant_type, ParticipantType.PNP)
        self.assertTrue(p88.can_settle_directly)
        self.assertTrue(p88.can_execute_trades)

        p105 = self.router.get_participant(105)
        self.assertIsNotNone(p105)
        self.assertEqual(p105.participant_type, ParticipantType.PN)
        self.assertFalse(p105.can_settle_directly)
        self.assertEqual(p105.clearing_member_id, 3)

        p3 = self.router.get_participant(3)
        self.assertIsNotNone(p3)
        self.assertEqual(p3.participant_type, ParticipantType.PL)
        self.assertTrue(p3.can_settle_directly)

    def test_routing_order_pnp_with_direct_settlement(self) -> None:
        """Testa roteamento de ordem PUMA para participante pleno (PNP 88) com autoliquidação."""
        req = OrderRouteRequest(
            participant_id=88,
            account="88000",
            symbol="PETR4",
            quantity=Decimal("1000"),
            clearing_member_id=None,
        )
        result = self.router.route_puma_order(req)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.status, RoutingStatus.APPROVED)
        self.assertEqual(result.reason_code, RoutingReasonCode.OK)
        self.assertEqual(result.resolved_clearing_member_id, 88)
        self.assertEqual(result.resolved_settlement_account, "00088-0")
        self.assertEqual(
            result.route_path,
            ["PNP:88", "PL:88", "PUMA:MATCHING_ENGINE"],
        )

    def test_routing_order_pnp_with_delegated_settlement(self) -> None:
        """Testa roteamento de ordem PUMA para participante pleno (PNP 308) com liquidação delegada ao PL 72."""
        req = OrderRouteRequest(
            participant_id=308,
            account="30800",
            symbol="VALE3",
            quantity=Decimal("500"),
        )
        result = self.router.route_puma_order(req)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.status, RoutingStatus.APPROVED)
        self.assertEqual(result.reason_code, RoutingReasonCode.OK)
        self.assertEqual(result.resolved_clearing_member_id, 72)
        self.assertEqual(result.resolved_settlement_account, "00072-5")
        self.assertEqual(
            result.route_path,
            ["PNP:308", "PL:72", "PUMA:MATCHING_ENGINE"],
        )

    def test_routing_order_pl_direct(self) -> None:
        """Testa roteamento de ordem PUMA diretamente pelo membro liquidante titular (PL 3)."""
        req = OrderRouteRequest(
            participant_id=3,
            account="03000",
            symbol="ITUB4",
            quantity=Decimal("2000"),
        )
        result = self.router.route_puma_order(req)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.status, RoutingStatus.APPROVED)
        self.assertEqual(result.reason_code, RoutingReasonCode.OK)
        self.assertEqual(result.resolved_clearing_member_id, 3)
        self.assertEqual(result.resolved_settlement_account, "00003-9")
        self.assertEqual(result.route_path, ["PL:3", "PUMA:MATCHING_ENGINE"])

    def test_reject_pn_direct_settlement_attempt(self) -> None:
        """Testa bloqueio de PN tentando liquidar diretamente sem PL perante a Câmara B3."""
        req = OrderRouteRequest(
            participant_id=105,
            account="10500",
            symbol="BBDC4",
            quantity=Decimal("1500"),
            is_direct_settlement_requested=True,
        )
        result = self.router.route_puma_order(req)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, RoutingStatus.REJECTED)
        self.assertEqual(
            result.reason_code, RoutingReasonCode.ERR_PN_DIRECT_SETTLEMENT_PROHIBITED
        )
        self.assertIn("expressamente proibido", result.message)

    def test_reject_pn_order_without_clearing_member(self) -> None:
        """Testa bloqueio de ordem de PN quando não há clearing member configurado ou informado."""
        # Cria um PN avulso sem clearing_member_id
        pn_isolated = ParticipantProfile(
            participant_id=555,
            name="Corretora PN Isolada Sem PL",
            participant_type=ParticipantType.PN,
            active=True,
            permitted_systems=[SystemTarget.PUMA],
            clearing_member_id=None,
            can_settle_directly=False,
            can_execute_trades=True,
        )
        self.router.register_participant(pn_isolated)

        req = OrderRouteRequest(
            participant_id=555,
            account="55500",
            symbol="PETR4",
            quantity=Decimal("100"),
            clearing_member_id=None,
        )
        result = self.router.route_puma_order(req)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, RoutingStatus.REJECTED)
        self.assertEqual(
            result.reason_code, RoutingReasonCode.ERR_MISSING_CLEARING_MEMBER
        )

    def test_reject_pn_with_non_pl_clearing_member(self) -> None:
        """Testa rejeição quando PN aponta participante que não possui perfil de PL (ex: outro PN)."""
        req = OrderRouteRequest(
            participant_id=105,
            account="10500",
            symbol="PETR4",
            quantity=Decimal("200"),
            clearing_member_id=114,  # 114 é PN, não PL
        )
        result = self.router.route_puma_order(req)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, RoutingStatus.REJECTED)
        self.assertEqual(
            result.reason_code, RoutingReasonCode.ERR_CLEARING_MEMBER_NOT_PL
        )

    def test_pn_order_routed_via_valid_pl(self) -> None:
        """Testa aprovação de ordem do PN 105 resolvida deterministicamente para o PL 3."""
        req = OrderRouteRequest(
            participant_id=105,
            account="10500",
            symbol="PETR4",
            quantity=Decimal("1000"),
            clearing_member_id=3,
        )
        result = self.router.route_puma_order(req)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.status, RoutingStatus.APPROVED)
        self.assertEqual(result.reason_code, RoutingReasonCode.OK)
        self.assertEqual(result.resolved_clearing_member_id, 3)
        self.assertEqual(result.resolved_settlement_account, "00003-9")
        self.assertEqual(
            result.route_path,
            ["PN:105", "PL:3", "PUMA:MATCHING_ENGINE"],
        )

    def test_reject_asset_manager_and_custodian_direct_puma_execution(self) -> None:
        """Testa rejeição de ordens PUMA enviadas diretamente por Asset Manager ou Custodiante Central."""
        # 1. Asset Manager (9001)
        req_am = OrderRouteRequest(
            participant_id=9001,
            account="M9001",
            symbol="PETR4",
            quantity=Decimal("5000"),
        )
        res_am = self.router.route_puma_order(req_am)
        self.assertFalse(res_am.is_valid)
        # AM tem apenas RTC em permitted_systems, logo o validador básico pega ERR_SYSTEM_NOT_PERMITTED
        self.assertEqual(res_am.reason_code, RoutingReasonCode.ERR_SYSTEM_NOT_PERMITTED)

        # Se temporariamente habilitarmos PUMA para o AM, ele deve ser bloqueado com ERR_ASSET_MANAGER_DIRECT_EXECUTION
        am_profile = self.router.get_participant(9001)
        assert am_profile is not None
        am_with_puma = ParticipantProfile(
            participant_id=am_profile.participant_id,
            name=am_profile.name,
            participant_type=am_profile.participant_type,
            active=True,
            permitted_systems=[SystemTarget.PUMA, SystemTarget.RTC],
            can_execute_trades=True,
        )
        self.router.register_participant(am_with_puma)

        res_am2 = self.router.route_puma_order(req_am)
        self.assertFalse(res_am2.is_valid)
        self.assertEqual(
            res_am2.reason_code, RoutingReasonCode.ERR_ASSET_MANAGER_DIRECT_EXECUTION
        )

        # 2. Custodiante Central (1001)
        cust_profile = self.router.get_participant(1001)
        assert cust_profile is not None
        cust_with_puma = ParticipantProfile(
            participant_id=cust_profile.participant_id,
            name=cust_profile.name,
            participant_type=cust_profile.participant_type,
            active=True,
            permitted_systems=[SystemTarget.PUMA, SystemTarget.RTC],
            can_execute_trades=True,
        )
        self.router.register_participant(cust_with_puma)

        req_cust = OrderRouteRequest(
            participant_id=1001,
            account="01001-0",
            symbol="VALE3",
            quantity=Decimal("1000"),
        )
        res_cust = self.router.route_puma_order(req_cust)
        self.assertFalse(res_cust.is_valid)
        self.assertEqual(
            res_cust.reason_code, RoutingReasonCode.ERR_CUSTODIAN_DIRECT_EXECUTION
        )

    def test_rtc_allocation_and_institutional_give_up(self) -> None:
        """Testa alocação no RTC e give-up institucional para conta segregada de fundo da Asset Manager."""
        req = AllocationRouteRequest(
            executing_broker_id=88,
            trade_ref_id="TRD-20261103-009101",
            master_account="88000",
            target_investor_account="88801",  # Conta do Atlas Institutional Equities FIA
            target_clearing_member_id=3,
            target_custodian_id=1001,
            allocated_quantity=Decimal("5000"),
            give_up=True,
            asset_manager_id=9001,
        )
        result = self.router.route_rtc_allocation(req)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.status, RoutingStatus.APPROVED)
        self.assertEqual(result.reason_code, RoutingReasonCode.OK)
        self.assertEqual(result.resolved_clearing_member_id, 3)
        self.assertEqual(result.resolved_custody_account, "CSD-1001-CENTRAL")
        self.assertEqual(
            result.route_path,
            [
                "EB:88",
                "RTC_GIVE_UP",
                "PL:3",
                "ACCOUNT:88801",
                "CUSTODIAN:1001",
            ],
        )

    def test_reject_rtc_give_up_without_authorized_rule(self) -> None:
        """Testa bloqueio de Give-Up no RTC quando não há acordo bilateral entre Broker e Clearing Member."""
        # Executing broker 114 tentando repassar para PL 3 (sua regra autoriza somente PL 40)
        req = AllocationRouteRequest(
            executing_broker_id=114,
            trade_ref_id="TRD-20261103-009102",
            master_account="11400",
            target_investor_account="88801",
            target_clearing_member_id=3,
            give_up=True,
        )
        result = self.router.route_rtc_allocation(req)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, RoutingStatus.REJECTED)
        self.assertEqual(
            result.reason_code, RoutingReasonCode.ERR_INVALID_GIVE_UP_RELATION
        )

    def test_reject_unauthorized_segregated_account_for_asset_manager(self) -> None:
        """Testa bloqueio de alocação quando a subconta informada não pertence à relação de fundos da gestora."""
        req = AllocationRouteRequest(
            executing_broker_id=88,
            trade_ref_id="TRD-20261103-009103",
            master_account="88000",
            target_investor_account="99999",  # Conta inexistente ou não vinculada à Asset 9001
            target_clearing_member_id=3,
            target_custodian_id=1001,
            allocated_quantity=Decimal("1000"),
            give_up=True,
            asset_manager_id=9001,
        )
        result = self.router.route_rtc_allocation(req)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, RoutingStatus.REJECTED)
        self.assertEqual(
            result.reason_code,
            RoutingReasonCode.ERR_UNAUTHORIZED_SEGREGATED_ACCOUNT,
        )

    def test_nome_registration_flow(self) -> None:
        """Testa resolução de rotas para registro bilateral no NoMe Balcão."""
        # 1. PL 3 registrando swap contra PNP 88
        req_pl = NoMeRegistrationRouteRequest(
            participant_id=3,
            contract_type="SWAP_DI_PRE",
            contraparty_id=88,
        )
        res_pl = self.router.route_nome_registration(req_pl)
        self.assertTrue(res_pl.is_valid)
        self.assertEqual(res_pl.status, RoutingStatus.APPROVED)
        self.assertEqual(res_pl.resolved_clearing_member_id, 3)

        # 2. PN 105 registrando contrato via PL 3
        req_pn = NoMeRegistrationRouteRequest(
            participant_id=105,
            contract_type="TERMO_DOLAR",
            contraparty_id=88,
            clearing_member_id=3,
        )
        res_pn = self.router.route_nome_registration(req_pn)
        self.assertTrue(res_pn.is_valid)
        self.assertEqual(res_pn.status, RoutingStatus.APPROVED)
        self.assertEqual(res_pn.resolved_clearing_member_id, 3)

    def test_connectivity_mtls_and_ipsec_validation(self) -> None:
        """Testa verificação de credenciais mTLS e allowlist de IP/CIDR (FL-018)."""
        # 1. Conexão mTLS e IP válidos para PL 3
        res_valid = self.router.validate_connectivity(
            participant_id=3,
            client_ip="10.3.5.20",
            client_cert_cn="omega.clearing.b3.com.br",
        )
        self.assertTrue(res_valid.is_valid)
        self.assertEqual(res_valid.reason_code, RoutingReasonCode.OK)
        self.assertIn("mTLS:omega.clearing.b3.com.br", res_valid.route_path)

        # 2. Divergência de CN no certificado digital mTLS
        res_bad_cn = self.router.validate_connectivity(
            participant_id=3,
            client_ip="10.3.5.20",
            client_cert_cn="hacker.falso.b3.com.br",
        )
        self.assertFalse(res_bad_cn.is_valid)
        self.assertEqual(
            res_bad_cn.reason_code, RoutingReasonCode.ERR_CONNECTIVITY_MISMATCH
        )
        self.assertIn("Divergência de certificado mTLS", res_bad_cn.message)

        # 3. IP fora da sub-rede homologada RCC/IPSec
        res_bad_ip = self.router.validate_connectivity(
            participant_id=3,
            client_ip="189.1.2.3",  # IP externo não autorizado
            client_cert_cn="omega.clearing.b3.com.br",
        )
        self.assertFalse(res_bad_ip.is_valid)
        self.assertEqual(
            res_bad_ip.reason_code, RoutingReasonCode.ERR_CONNECTIVITY_MISMATCH
        )
        self.assertIn("não autorizada na allowlist RCC", res_bad_ip.message)

    def test_inactive_and_unknown_participant(self) -> None:
        """Testa rejeição estrita de participantes inativos ou não cadastrados."""
        # Participante 999 inativo/suspenso
        req_inactive = OrderRouteRequest(
            participant_id=999,
            account="99900",
            symbol="PETR4",
            quantity=Decimal("100"),
        )
        res_inactive = self.router.route_puma_order(req_inactive)
        self.assertFalse(res_inactive.is_valid)
        self.assertEqual(
            res_inactive.reason_code, RoutingReasonCode.ERR_PARTICIPANT_INACTIVE
        )

        # Participante inexistente (77777)
        req_unknown = OrderRouteRequest(
            participant_id=77777,
            account="00000",
            symbol="PETR4",
            quantity=Decimal("100"),
        )
        res_unknown = self.router.route_puma_order(req_unknown)
        self.assertFalse(res_unknown.is_valid)
        self.assertEqual(
            res_unknown.reason_code, RoutingReasonCode.ERR_PARTICIPANT_NOT_FOUND
        )

    def test_serialization_and_deserialization(self) -> None:
        """Testa serialização e deserialização para dicionários."""
        p = self.router.get_participant(88)
        assert p is not None
        d = p.to_dict()
        self.assertEqual(d["participant_id"], 88)
        self.assertEqual(d["participant_type"], "PNP")

        reconstructed = ParticipantProfile.from_dict(d)
        self.assertEqual(reconstructed.participant_id, 88)
        self.assertEqual(reconstructed.participant_type, ParticipantType.PNP)

        rule = self.router.get_rule("RULE-PUMA-PNP-88-DIRECT")
        assert rule is not None
        rule_dict = rule.to_dict()
        self.assertEqual(rule_dict["rule_id"], "RULE-PUMA-PNP-88-DIRECT")
        self.assertEqual(rule_dict["target_system"], "PUMA")

        reconstructed_rule = RoutingRule.from_dict(rule_dict)
        self.assertEqual(reconstructed_rule.rule_id, "RULE-PUMA-PNP-88-DIRECT")
        self.assertEqual(reconstructed_rule.target_system, SystemTarget.PUMA)


if __name__ == "__main__":
    unittest.main()
