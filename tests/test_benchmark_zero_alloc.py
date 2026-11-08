"""Testes unitários para o utilitário de microbenchmarks e invariantes LAW-004."""

from decimal import Decimal
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.benchmark_zero_alloc import (
    NanosecondProfiler,
    build_benchmark_suites,
    generate_markdown_report,
)
from src.invariants import (
    InvariantViolationError,
    assert_clearing_position_integrity,
)


class TestBenchmarkZeroAlloc(unittest.TestCase):
    """Testa a integridade do profiler e da validação da LAW-004."""

    def test_law_004_position_integrity_success(self) -> None:
        """Garante que posições compradas iguais a vendidas passam na LAW-004."""
        assert_clearing_position_integrity(
            bought_positions=[1000, 2500, 1500],
            sold_positions=[3000, 2000],
            symbol="PETR4",
        )

    def test_law_004_position_integrity_scalar_success(self) -> None:
        assert_clearing_position_integrity(
            bought_positions=Decimal("50000.00"),
            sold_positions=Decimal("50000.00"),
            symbol="VALE3",
        )

    def test_law_004_position_integrity_violation(self) -> None:
        """Garante que descasamento entre compras e vendas aciona LAW-004."""
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_clearing_position_integrity(
                bought_positions=[1000, 2000],
                sold_positions=[2500],
                symbol="ITUB4",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-004")
        self.assertIn("Posições Compradas (3000) != Posições Vendidas (2500)", ctx.exception.message)

    def test_nanosecond_profiler_runs(self) -> None:
        """Testa se o NanosecondProfiler executa medições com precisão e sem erros."""
        profiler = NanosecondProfiler(timer_overhead_correction=True)
        res = profiler.run_benchmark(
            name="Test_Dummy_Op",
            suite="Test Suite",
            description="Operação de teste",
            fn=lambda: 1 + 1,
            iterations=1_000,
            warmup_iterations=100,
        )
        self.assertEqual(res.name, "Test_Dummy_Op")
        self.assertGreater(res.mean_ns, 0)
        self.assertGreater(res.throughput_ops_sec, 0)
        self.assertTrue(res.is_zero_alloc)


if __name__ == "__main__":
    unittest.main()
