"""Testes de verificação formal TLA+ e model checking para o SNG e SRO (LAW-112 e LAW-113)."""

from pathlib import Path
import unittest

from src.tla_model_checker import (
    ChassisStateRecord,
    SngGravameRecord,
    SngSroModelChecker,
    SngSroState,
    SroPolicyRecord,
)


class TestTlaSngSroGuarantees(unittest.TestCase):
    """Suíte de verificação formal de espaço de estados para SNG (LAW-112) e SRO (LAW-113)."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.tla_file = self.repo_root / "specs" / "tla" / "SngSroGuarantees.tla"
        self.cfg_file = self.repo_root / "specs" / "tla" / "SngSroGuarantees.cfg"

    def test_specification_files_exist_and_contain_formal_definitions(self) -> None:
        """Verifica que os arquivos formais TLA+ e CFG existem com suas invariantes matemáticas."""
        self.assertTrue(self.tla_file.is_file(), "Arquivo SngSroGuarantees.tla deve existir")
        self.assertTrue(self.cfg_file.is_file(), "Arquivo SngSroGuarantees.cfg deve existir")

        tla_text = self.tla_file.read_text(encoding="utf-8")
        self.assertIn("MODULE SngSroGuarantees", tla_text)
        self.assertIn("UniqueActiveGravame_LAW_112", tla_text)
        self.assertIn("CosseguroConservation_LAW_113", tla_text)
        self.assertIn("IncludeGravame", tla_text)
        self.assertIn("ReleaseGravame", tla_text)
        self.assertIn("CancelGravame", tla_text)
        self.assertIn("PayClaim", tla_text)
        self.assertIn("NoGravame", tla_text)
        self.assertIn("GravameActive", tla_text)
        self.assertIn("Released", tla_text)

        cfg_text = self.cfg_file.read_text(encoding="utf-8")
        self.assertIn("UniqueActiveGravame_LAW_112", cfg_text)
        self.assertIn("CosseguroConservation_LAW_113", cfg_text)
        self.assertIn("TypeOK", cfg_text)

    def test_exhaustive_bfs_model_checking_sng_sro(self) -> None:
        """Executa a verificação exaustiva BFS do grafo de estados finitos provando LAW-112 e LAW-113."""
        checker = SngSroModelChecker(
            chassis=("CHA", "CHB"),
            creditors=("CRED_A", "CRED_B"),
            contracts=("CTR1",),
            policy_ids=("POL1", "POL2"),
            insurers=("INS_A", "INS_B"),
            initial_lmg=40,
            claim_step=20,
        )
        report = checker.run_model_check(self.tla_file)

        self.assertTrue(report.passed, f"Model checking falhou com violações: {report.invariant_violations}")
        self.assertEqual(report.deadlocks_found, 0, "Nenhum deadlock deve ocorrer")
        self.assertEqual(len(report.invariant_violations), 0)
        self.assertGreaterEqual(report.distinct_states_explored, 1000, "Deve explorar espaço representativo de estados")
        self.assertGreaterEqual(report.transitions_evaluated, 5000, "Deve avaliar milhares de transições")
        self.assertGreaterEqual(report.max_depth_reached, 10)
        self.assertTrue(report.type_ok_verified, "Tipagem estrita TypeOK deve ser mantida")
        self.assertTrue(report.law_112_unique_active_gravame_verified, "LAW-112 deve ser preservada em 100% dos estados")
        self.assertTrue(report.law_113_cosseguro_lmg_conservation_verified, "LAW-113 deve ser preservada em 100% dos estados")
        self.assertTrue(report.liveness_verified, "Liveness sem deadlocks deve ser garantida")

    def test_adversarial_duplicate_active_gravame_detected(self) -> None:
        """Garante que tentativa de registrar 2 gravames ativos no mesmo chassi é detectada e barrada (LAW-112)."""
        checker = SngSroModelChecker()
        bad_gravames = frozenset([
            SngGravameRecord("CHA:CRED_A:CTR1", "CHA", "CRED_A", "CTR1", "ACTIVE"),
            SngGravameRecord("CHA:CRED_B:CTR2", "CHA", "CRED_B", "CTR2", "ACTIVE"),  # Duplicado ativo!
        ])
        corrupted_state = SngSroState(
            chassis_state=(
                ("CHA", ChassisStateRecord("GravameActive", "CRED_A", "CTR1")),
                ("CHB", ChassisStateRecord("NoGravame", "NONE", "NONE")),
            ),
            gravames=bad_gravames,
            policies=frozenset([
                SroPolicyRecord("POL1", (("INS_A", 60), ("INS_B", 40)), 40, 0)
            ]),
        )
        type_ok, law_112, law_113, violations = checker.check_invariants(corrupted_state)
        self.assertFalse(law_112, "LAW-112 deve falhar diante de dois gravames ativos no mesmo chassi")
        self.assertTrue(any("UniqueActiveGravame_LAW_112" in v for v in violations))
        self.assertTrue(any("CHA" in v for v in violations))

    def test_adversarial_claim_exceeding_lmg_detected(self) -> None:
        """Garante que pagamento cumulativo de sinistro acima do LMG é detectado como violação (LAW-113)."""
        checker = SngSroModelChecker()
        corrupted_state = SngSroState(
            chassis_state=(("CHA", ChassisStateRecord("NoGravame", "NONE", "NONE")),),
            gravames=frozenset(),
            policies=frozenset([
                # Sinistro pago R$ 60 excede LMG de R$ 40!
                SroPolicyRecord("POL1", (("INS_A", 60), ("INS_B", 40)), 40, 60)
            ]),
        )
        type_ok, law_112, law_113, violations = checker.check_invariants(corrupted_state)
        self.assertFalse(law_113, "LAW-113 deve falhar quando total de sinistros pagos excede o LMG")
        self.assertTrue(any("CosseguroConservation_LAW_113" in v for v in violations))
        self.assertTrue(any("excedendo LMG" in v for v in violations))

    def test_adversarial_cosseguro_shares_not_100_detected(self) -> None:
        """Garante que distribuição de cosseguro cuja soma difere de 100% é violada (LAW-113)."""
        checker = SngSroModelChecker()
        corrupted_state = SngSroState(
            chassis_state=(("CHA", ChassisStateRecord("NoGravame", "NONE", "NONE")),),
            gravames=frozenset(),
            policies=frozenset([
                # Quotas somam 60 + 30 = 90% != 100%!
                SroPolicyRecord("POL1", (("INS_A", 60), ("INS_B", 30)), 40, 0)
            ]),
        )
        type_ok, law_112, law_113, violations = checker.check_invariants(corrupted_state)
        self.assertFalse(law_113, "LAW-113 deve falhar quando a soma das quotas de cosseguro != 100%")
        self.assertTrue(any("soma de quotas=90%" in v for v in violations))

    def test_sng_lifecycle_transitions(self) -> None:
        """Verifica transições do ciclo de vida SNG: NoGravame -> Active -> Released -> Active -> Cancelled."""
        checker = SngSroModelChecker(
            chassis=("CHA",),
            creditors=("CRED_A",),
            contracts=("CTR1",),
            policy_ids=("POL1",),
        )
        init = checker.initial_state()
        self.assertEqual(init.chassis_dict()["CHA"].status, "NoGravame")

        # Gerar sucessores a partir do estado inicial
        succs = checker.next_states(init)
        active_states = [s for s in succs if s.chassis_dict()["CHA"].status == "GravameActive"]
        self.assertGreater(len(active_states), 0, "Deve haver transição para GravameActive")

        # A partir do estado com gravame ativo, verificar transições para Released e Cancelled
        active_state = active_states[0]
        succs_from_active = checker.next_states(active_state)
        released_states = [s for s in succs_from_active if s.chassis_dict()["CHA"].status == "Released"]
        cancelled_states = [s for s in succs_from_active if s.chassis_dict()["CHA"].status == "NoGravame"]
        self.assertGreater(len(released_states), 0, "Deve haver transição para Released via ReleaseGravame")
        self.assertGreater(len(cancelled_states), 0, "Deve haver transição para NoGravame via CancelGravame")

    def test_sro_claims_payment_saturation(self) -> None:
        """Verifica saturação de pagamento de sinistros até o teto LMG sem ultrapassagem."""
        checker = SngSroModelChecker(
            chassis=("CHA",),
            creditors=("CRED_A",),
            contracts=("CTR1",),
            policy_ids=("POL1",),
            initial_lmg=40,
            claim_step=20,
        )
        init = checker.initial_state()
        p0 = init.policy_dict()["POL1"]
        self.assertEqual(p0.total_claims_paid, 0)

        # Primeiro pagamento: 0 -> 20
        succs1 = [s for s in checker.next_states(init) if s.policy_dict()["POL1"].total_claims_paid == 20]
        self.assertEqual(len(succs1), 1)

        # Segundo pagamento: 20 -> 40 (atinge LMG)
        succs2 = [s for s in checker.next_states(succs1[0]) if s.policy_dict()["POL1"].total_claims_paid == 40]
        self.assertEqual(len(succs2), 1)

        # Terceiro passo: não pode pagar mais (pois 40 + 20 = 60 > 40)
        succs3 = [s for s in checker.next_states(succs2[0]) if s.policy_dict()["POL1"].total_claims_paid > 40]
        self.assertEqual(len(succs3), 0, "Nenhum sucessor pode pagar acima do LMG")


if __name__ == "__main__":
    unittest.main()
