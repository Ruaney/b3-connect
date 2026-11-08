"""Testes unitários e de verificação formal do Model Checker TLA+ (`tla_model_checker.py`)."""

from pathlib import Path
import unittest

from src.tla_model_checker import CentralLiquidityBusModelChecker, TLAState


class TestTLAModelChecker(unittest.TestCase):
    """Valida a especificação formal TLA+ e o verificador exaustivo de espaço de estados."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.tla_file = self.repo_root / "specs" / "tla" / "CentralLiquidityBus.tla"
        self.cfg_file = self.repo_root / "specs" / "tla" / "CentralLiquidityBus.cfg"

    def test_tla_specification_files_exist_and_valid(self) -> None:
        """Verifica que os arquivos formais .tla e .cfg existem e contêm todas as invariantes."""
        self.assertTrue(self.tla_file.is_file(), "Arquivo CentralLiquidityBus.tla deve existir")
        self.assertTrue(self.cfg_file.is_file(), "Arquivo CentralLiquidityBus.cfg deve existir")

        tla_text = self.tla_file.read_text(encoding="utf-8")
        self.assertIn("MODULE CentralLiquidityBus", tla_text)
        self.assertIn("ZeroSumAccountingConservation", tla_text)
        self.assertIn("WaterfallOrderInviolability", tla_text)
        self.assertIn("NoUnabsorbedDefaultAtSettlement", tla_text)
        self.assertIn("IntersystemSettlementAtomicity", tla_text)
        self.assertIn("EventualCycleTermination", tla_text)

    def test_exhaustive_state_space_verification(self) -> None:
        """Executa o model checking BFS completo para 3 membros e 4 subsistemas."""
        checker = CentralLiquidityBusModelChecker(
            members=("M1", "M2", "M3"),
            subsystems=("PUMA", "NOME", "DREX", "SPB"),
            initial_collateral=20,
            initial_safeguard=30,
            obligation_step=10,
        )
        report = checker.run_model_check(self.tla_file)

        self.assertTrue(report.passed)
        self.assertEqual(report.deadlocks_found, 0)
        self.assertEqual(len(report.invariant_violations), 0)
        self.assertGreater(report.distinct_states_explored, 100)
        self.assertGreater(report.transitions_evaluated, 250)
        self.assertTrue(report.type_ok_verified)
        self.assertTrue(report.law_106a_zero_sum_verified)
        self.assertTrue(report.law_106b_waterfall_order_verified)
        self.assertTrue(report.law_106b_no_unabsorbed_default_verified)
        self.assertTrue(report.law_106c_atomicity_verified)
        self.assertTrue(report.liveness_termination_verified)

    def test_detects_adversarial_state_violations(self) -> None:
        """Garante que o verificador detecta estados corrompidos que violam LAW-106A/B/C."""
        checker = CentralLiquidityBusModelChecker()

        # Estado corrompido com soma diferente de zero para membros solventes (viola LAW-106A)
        bad_zero_sum = TLAState(
            cycle_phase="NETTING",
            member_net_positions=(("M1", -10), ("M2", 20), ("M3", 0)),
            member_status=(("M1", "SOLVENT"), ("M2", "SOLVENT"), ("M3", "SOLVENT")),
            collateral_pool=(("M1", 20), ("M2", 20), ("M3", 20)),
            safeguard_pool=30,
            ccp_equity=30,
            settled_subsystems=frozenset(),
            residual_deficit=0,
        )
        _, l106a, _, _, _, viols = checker.check_invariants(bad_zero_sum)
        self.assertFalse(l106a)
        self.assertGreater(len(viols), 0)

        # Estado corrompido com liquidação parcial de subsistemas (viola LAW-106C)
        bad_atomicity = TLAState(
            cycle_phase="SETTLED",
            member_net_positions=(("M1", 0), ("M2", 0), ("M3", 0)),
            member_status=(("M1", "SOLVENT"), ("M2", "SOLVENT"), ("M3", "SOLVENT")),
            collateral_pool=(("M1", 20), ("M2", 20), ("M3", 20)),
            safeguard_pool=30,
            ccp_equity=30,
            settled_subsystems=frozenset({"PUMA", "NOME"}),  # Parcial!
            residual_deficit=0,
        )
        _, _, _, _, l106c, viols_c = checker.check_invariants(bad_atomicity)
        self.assertFalse(l106c)
        self.assertGreater(len(viols_c), 0)


if __name__ == "__main__":
    unittest.main()
