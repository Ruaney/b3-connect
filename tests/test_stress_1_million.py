"""B3 Integration Atlas — Suíte de Testes de Estresse de Extrema Escala: 1 Milhão de Operações.

Avalia o processamento em extrema escala do Barramento Central de Liquidação da B3 (FL-100)
e do decodificador binário PUMA SBE Little-Endian:
1. Simulação contínua de 1.000.000 (1 milhão) de operações com zero alocações de heap em regime estacionário.
2. Medição em microssegundos de latência individual (Média, Mínima, p50, p95, p99, p99.9 e Máxima).
3. Throughput sustentado medido em mensagens por segundo (msg/s).
4. Auditoria contínua da Lei Invariante de Conservação Contábil e Mensageria (LAW-106A):
   - Equilíbrio contábil global estrito: Soma Ativos == Soma Passivos (tolerância R$ 0,00).
   - Conservação multilateral soma-zero entre todos os membros de compensação.
   - Balanço de mensageria: inbound_total == settled + rejected + pending.
5. Verificação de estabilidade de memória e ausência de vazamento de heap via tracemalloc.
"""

from __future__ import annotations

from decimal import Decimal
import gc
from pathlib import Path
import sys
import time
import tracemalloc
from typing import Any, Dict, List
import unittest

# Assegura PYTHONPATH apontando para a raiz do repositório
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.invariants import (
    InvariantViolationError,
    assert_central_bus_global_balance,
)
from src.puma_sbe import (
    NewOrderSingleBinary,
    PRICE_SCALE,
    Side,
    TimeInForce,
)
from src.central_settlement_liquidity_bus import (
    AccountingImbalanceError,
    B3CentralSettlementBus,
    ClearingSubsystem,
    ZeroAllocCentralBusEngine,
)

FIXTURES_DIR = REPO_ROOT / "fixtures"


class InstitutionalLatencyMetrics:
    """Estrutura e cálculo de métricas de latência e vazão para 1 milhão de operações."""

    @staticmethod
    def calculate(latencies_ns: List[int], elapsed_seconds: float) -> Dict[str, float]:
        n = len(latencies_ns)
        if n == 0:
            return {
                "count": 0,
                "elapsed_s": 0.0,
                "throughput_msg_s": 0.0,
                "mean_us": 0.0,
                "min_us": 0.0,
                "p50_us": 0.0,
                "p95_us": 0.0,
                "p99_us": 0.0,
                "p99_9_us": 0.0,
                "max_us": 0.0,
            }

        sorted_samples = sorted(latencies_ns)

        def pct(p: float) -> float:
            idx = min(int(p * n), n - 1)
            return sorted_samples[idx] / 1000.0  # ns -> us

        mean_us = (sum(sorted_samples) / n) / 1000.0
        min_us = sorted_samples[0] / 1000.0
        max_us = sorted_samples[-1] / 1000.0
        throughput = n / max(elapsed_seconds, 1e-9)

        return {
            "count": n,
            "elapsed_s": elapsed_seconds,
            "throughput_msg_s": throughput,
            "mean_us": mean_us,
            "min_us": min_us,
            "p50_us": pct(0.50),
            "p95_us": pct(0.95),
            "p99_us": pct(0.99),
            "p99_9_us": pct(0.999),
            "max_us": max_us,
        }

    @staticmethod
    def print_report(title: str, metrics: Dict[str, float]) -> None:
        print("\n" + "=" * 80)
        print(f"  {title.upper()}")
        print("=" * 80)
        print(f"  Mensagens Processadas : {int(metrics['count']):,}")
        print(f"  Tempo Total           : {metrics['elapsed_s']:.4f} s")
        print(f"  Throughput Sustentado : {metrics['throughput_msg_s']:,.0f} msg/s")
        print(f"  Latência Média        : {metrics['mean_us']:.3f} µs")
        print(f"  Latência Mínima       : {metrics['min_us']:.3f} µs")
        print(f"  Percentil p50         : {metrics['p50_us']:.3f} µs")
        print(f"  Percentil p95         : {metrics['p95_us']:.3f} µs")
        print(f"  Percentil p99         : {metrics['p99_us']:.3f} µs")
        print(f"  Percentil p99.9       : {metrics['p99_9_us']:.3f} µs")
        print(f"  Latência Máxima       : {metrics['max_us']:.3f} µs")
        print("=" * 80)


class TestStressOneMillionOperations(unittest.TestCase):
    """Bateria de Estresse de Extrema Escala: 1.000.000 de Operações FL-100 / PUMA SBE."""

    TOTAL_OPS: int = 1_000_000

    @classmethod
    def setUpClass(cls) -> None:
        """Inicializa fixtures binárias PUMA SBE e o Barramento Central FL-100."""
        fixture_path = FIXTURES_DIR / "central_settlement_liquidity_bus.json"
        cls.bus, cls.raw_data = B3CentralSettlementBus.from_fixture(fixture_path)

        # Ordem modelo binária PUMA SBE (PETR4, 100 ações @ R$ 34.50)
        cls.sample_order = NewOrderSingleBinary(
            cl_ord_id=2026110800000001,
            security_id=105829,
            side=Side.BUY,
            order_qty=100,
            price=34.50,
            time_in_force=TimeInForce.DAY,
            account=987654,
        )
        cls.sample_order_bytes = cls.sample_order.encode()

        # Warm-up do interpretador CPython
        for _ in range(5_000):
            _ = NewOrderSingleBinary.unpack_raw(cls.sample_order_bytes)

    # -------------------------------------------------------------------------
    # TESTE 1: Decodificação PUMA SBE Wire-Speed (1 Milhão de Mensagens)
    # -------------------------------------------------------------------------
    def test_01_stress_1_million_puma_sbe_wire_decode(self) -> None:
        """Avalia 1.000.000 de decodificações PUMA SBE zero-copy em velocidade de fio (wire speed)."""
        n = self.TOTAL_OPS
        raw_data = self.sample_order_bytes
        unpack = NewOrderSingleBinary.unpack_raw

        # Array pré-alocado para eliminar overhead de append durante o loop
        latencies_ns = [0] * n

        gc.collect()
        t_start = time.perf_counter()

        for i in range(n):
            t0 = time.perf_counter_ns()
            # Decodificação zero-copy sem alocação de heap
            cl_ord_id, sec_id, side, qty, px_scaled, tif, acct = unpack(raw_data)
            t1 = time.perf_counter_ns()
            latencies_ns[i] = t1 - t0

        t_end = time.perf_counter()
        elapsed = t_end - t_start

        metrics = InstitutionalLatencyMetrics.calculate(latencies_ns, elapsed)
        InstitutionalLatencyMetrics.print_report(
            "Benchmark 1.000.000 Mensagens: Decodificador PUMA SBE (Wire-Speed)",
            metrics,
        )

        self.assertEqual(metrics["count"], n)
        self.assertGreater(
            metrics["throughput_msg_s"],
            500_000,
            f"Throughput ({metrics['throughput_msg_s']:,.0f} msg/s) deve exceder 500.000 msg/s",
        )
        self.assertLess(
            metrics["p99_us"],
            15.0,
            f"Latência de cauda p99 ({metrics['p99_us']:.3f} µs) deve ser inferior a 15.0 µs",
        )

    # -------------------------------------------------------------------------
    # TESTE 2: Processamento End-to-End no Barramento Central FL-100 (1M Ops)
    # -------------------------------------------------------------------------
    def test_02_stress_1_million_central_bus_fl100_clearing(self) -> None:
        """Simula 1.000.000 de operações no Barramento Central (FL-100) com auditoria LAW-106A."""
        n = self.TOTAL_OPS
        raw_data = self.sample_order_bytes
        engine = self.bus.create_zero_alloc_engine()

        num_members = len(engine.member_ids)
        num_subsystems = len(ClearingSubsystem)

        # Array pré-alocado de latências para latência individual pura
        latencies_ns = [0] * n

        gc.collect()
        t_start = time.perf_counter()

        for i in range(n):
            debtor_idx = i % num_members
            creditor_idx = (i + 1) % num_members
            subsystem_idx = i % num_subsystems

            t0 = time.perf_counter_ns()
            # Processamento atômico zero-alloc no Barramento Central FL-100
            settled = engine.process_puma_sbe_wire_transfer(
                debtor_idx=debtor_idx,
                creditor_idx=creditor_idx,
                sbe_bytes=raw_data,
                subsystem_idx=subsystem_idx,
            )
            t1 = time.perf_counter_ns()

            latencies_ns[i] = t1 - t0

        t_end = time.perf_counter()
        elapsed = t_end - t_start

        metrics = InstitutionalLatencyMetrics.calculate(latencies_ns, elapsed)
        InstitutionalLatencyMetrics.print_report(
            "Benchmark 1.000.000 Operações: Barramento Central FL-100 & PUMA SBE",
            metrics,
        )

        # ---------------------------------------------------------------------
        # AUDITORIA DAS INVARIANTES DE CONSERVAÇÃO CONTÁBIL (LAW-106A)
        # ---------------------------------------------------------------------
        inv_audit = engine.verify_law_106a(raise_on_error=True)

        # 1. Validação estrita de conservação soma-zero multilateral (diferença == 0 centavos)
        self.assertTrue(
            inv_audit["LAW-106A_multilateral_zero_sum_conservation"],
            "Quebra na conservação soma-zero multilateral durante o milhão de mensagens!",
        )
        self.assertEqual(
            inv_audit["net_imbalance_cents"],
            0,
            f"Desbalanceamento contábil detectado: {inv_audit['net_imbalance_cents']} centavos",
        )

        # 2. Validação estrita de equilíbrio global (Soma Ativos == Soma Passivos)
        self.assertTrue(
            inv_audit["LAW-106A_global_accounting_equilibrium"],
            "Quebra no equilíbrio contábil global Ativos == Passivos!",
        )
        self.assertEqual(inv_audit["accounting_imbalance_cents"], 0)

        # 3. Validação de conservação de mensageria no Barramento Central (FL-100)
        self.assertEqual(inv_audit["inbound_total"], n)
        self.assertEqual(inv_audit["settled_count"], n)
        self.assertEqual(inv_audit["rejected_count"], 0)
        self.assertEqual(inv_audit["pending_count"], 0)

        # 4. Sincronização e verificação de conformidade no modelo canônico B3
        engine.sync_to_bus()
        canon_audit = self.bus.verify_invariants(raise_on_error=True)
        self.assertTrue(canon_audit["LAW-106A_global_accounting_equilibrium"])
        self.assertTrue(canon_audit["LAW-106A_multilateral_zero_sum_conservation"])
        self.assertTrue(canon_audit["all_invariants_hold"])

        # Metadados consolidados
        expected_financial_cents = n * ((100 * int(round(34.50 * PRICE_SCALE))) // 1_000_000)
        self.assertEqual(engine.total_financial_settled_cents, expected_financial_cents)
        self.assertEqual(engine.total_volume_shares, n * 100)

        # Asserções de performance de alta velocidade
        self.assertGreater(
            metrics["throughput_msg_s"],
            400_000,
            f"Throughput do Barramento Central ({metrics['throughput_msg_s']:,.0f} msg/s) deve exceder 400.000 msg/s",
        )
        self.assertLess(
            metrics["p99_us"],
            20.0,
            f"Latência de cauda p99 ({metrics['p99_us']:.3f} µs) deve ser inferior a 20.0 µs",
        )

    # -------------------------------------------------------------------------
    # TESTE 3: Estabilidade de Memória e Zero Alocações de Heap (tracemalloc)
    # -------------------------------------------------------------------------
    def test_03_stress_1_million_zero_allocation_heap_stability(self) -> None:
        """Audita a ausência de vazamento de memória e estabilidade da heap em 1.000.000 de mensagens."""
        n = self.TOTAL_OPS
        raw_data = self.sample_order_bytes
        engine = self.bus.create_zero_alloc_engine()
        num_members = len(engine.member_ids)

        # Warm-up de 50.000 mensagens
        for i in range(50_000):
            _ = engine.process_puma_sbe_wire_transfer(
                debtor_idx=i % num_members,
                creditor_idx=(i + 1) % num_members,
                sbe_bytes=raw_data,
            )

        gc.collect()
        tracemalloc.start()
        snap_before = tracemalloc.take_snapshot()

        # Executa lote completo de 1.000.000 de operações
        for i in range(n):
            _ = engine.process_puma_sbe_wire_transfer(
                debtor_idx=i % num_members,
                creditor_idx=(i + 1) % num_members,
                sbe_bytes=raw_data,
            )

        gc.collect()
        snap_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        diff = snap_after.compare_to(snap_before, "lineno")
        total_growth_kb = sum(stat.size_diff for stat in diff) / 1024.0

        print("\n" + "=" * 80)
        print("  AUDITORIA DE MEMÓRIA HEAP TRACEMALLOC: 1 MILHÃO DE OPERAÇÕES")
        print("=" * 80)
        print(f"  Variação Líquida de Heap : {total_growth_kb:.2f} KB em 1.000.000 mensagens")
        print(f"  Média por Operação       : {(total_growth_kb * 1024.0) / n:.4f} bytes/op")
        print("=" * 80)

        # A variação deve ser desprezível (< 50 KB após GC para 1 milhão de operações)
        self.assertLess(
            abs(total_growth_kb),
            50.0,
            f"Vazamento ou acúmulo de heap detectado: {total_growth_kb:.2f} KB",
        )

    # -------------------------------------------------------------------------
    # TESTE 4: Injeção Adversarial de Rejeições e Auditoria LAW-106A
    # -------------------------------------------------------------------------
    def test_04_stress_1_million_adversarial_rejection_and_law106a_audit(self) -> None:
        """Simula 1.000.000 de mensagens com injeção adversarial de fundos insuficientes."""
        n = self.TOTAL_OPS
        raw_data = self.sample_order_bytes
        engine = self.bus.create_zero_alloc_engine()

        # Configura membro 0 com saldo limitado de 1.000.000 centavos (R$ 10.000,00)
        # Cada ordem custa R$ 3.450,00 (345.000 centavos). O membro suportará apenas 2 ordens
        engine.cash_balances_cents[0] = 1_000_000
        engine.initial_balances_cents[0] = 1_000_000

        num_members = len(engine.member_ids)

        for i in range(n):
            debtor_idx = 0 if (i % 5 == 0) else (i % num_members)
            creditor_idx = (debtor_idx + 1) % num_members

            _ = engine.process_puma_sbe_wire_transfer(
                debtor_idx=debtor_idx,
                creditor_idx=creditor_idx,
                sbe_bytes=raw_data,
            )

        audit = engine.verify_law_106a(raise_on_error=True)

        # Garante que houve rejeições e liquidações sem perda de mensagens
        self.assertGreater(audit["rejected_count"], 0)
        self.assertGreater(audit["settled_count"], 0)
        self.assertEqual(audit["inbound_total"], n)
        self.assertEqual(audit["settled_count"] + audit["rejected_count"], n)

        # A conservação soma-zero e equilíbrio contábil devem se manter rigorosamente intactos
        self.assertTrue(audit["LAW-106A_multilateral_zero_sum_conservation"])
        self.assertTrue(audit["LAW-106A_global_accounting_equilibrium"])
        self.assertEqual(audit["net_imbalance_cents"], 0)


if __name__ == "__main__":
    unittest.main()
