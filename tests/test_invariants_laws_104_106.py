"""B3 Integration Atlas — Suíte de Testes Formais das Leis LAW-104, LAW-105 e LAW-106.

Validação unitária e matemática exaustiva das 9 novas leis invariantes:
- LAW-104A: assert_closing_cross_freeze_inviolability
- LAW-104B: assert_moc_loc_allocation_priority
- LAW-104C: assert_closing_auction_volume_maximization
- LAW-105A: assert_cross_border_collateral_coverage
- LAW-105B: assert_international_fiduciary_lock
- LAW-105C: assert_triparty_zero_sum_settlement
- LAW-106A: assert_central_bus_global_balance
- LAW-106B: assert_default_waterfall_containment
- LAW-106C: assert_cross_system_atomic_settlement
"""

from __future__ import annotations

from decimal import Decimal
import unittest

from src.invariants import (
    InvariantViolationError,
    assert_central_bus_global_balance,
    assert_closing_auction_volume_maximization,
    assert_closing_cross_freeze_inviolability,
    assert_cross_border_collateral_coverage,
    assert_cross_system_atomic_settlement,
    assert_default_waterfall_containment,
    assert_international_fiduciary_lock,
    assert_moc_loc_allocation_priority,
    assert_triparty_zero_sum_settlement,
    enforce_fail_safe,
)


class TestInvariantsLaws104to106(unittest.TestCase):
    """Testes formais de conformidade e violação para as leis LAW-104, LAW-105 e LAW-106."""

    # -------------------------------------------------------------------------
    # LAW-104A: assert_closing_cross_freeze_inviolability
    # -------------------------------------------------------------------------
    def test_law_104a_freeze_inviolability_success(self) -> None:
        # Estado congelado sem cancelamentos nem modificações
        assert_closing_cross_freeze_inviolability(
            freeze_active=True,
            cancellations_attempted=0,
            modifications_attempted=0,
            symbol="PETR4",
            auction_id="AUC-20261107-001",
        )
        # Freeze desativado permite operações
        assert_closing_cross_freeze_inviolability(
            freeze_active=False,
            cancellations_attempted=5,
            modifications_attempted=10,
            symbol="VALE3",
        )

    def test_law_104a_freeze_inviolability_violations(self) -> None:
        # Tentativa de cancelamento durante freeze ativo
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_closing_cross_freeze_inviolability(
                freeze_active=True,
                cancellations_attempted=1,
                modifications_attempted=0,
                symbol="PETR4",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-104A")
        self.assertIn("Cancelamentos=1", ctx.exception.message)

        # Tentativa de modificação durante freeze ativo
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_closing_cross_freeze_inviolability(
                freeze_active=True,
                cancellations_attempted=0,
                modifications_attempted=3,
                symbol="PETR4",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-104A")
        self.assertIn("Modificações=3", ctx.exception.message)

    # -------------------------------------------------------------------------
    # LAW-104B: assert_moc_loc_allocation_priority
    # -------------------------------------------------------------------------
    def test_law_104b_moc_loc_allocation_priority_success(self) -> None:
        # MOC 100% alocado, LOC alocado subsequentemente
        assert_moc_loc_allocation_priority(
            unexecuted_moc_quantity=0,
            executed_loc_or_limit_quantity=15000,
            side="BUY",
            symbol="PETR4",
        )
        # MOC parcialmente pendente, mas LOC não executou nada
        assert_moc_loc_allocation_priority(
            unexecuted_moc_quantity=5000,
            executed_loc_or_limit_quantity=0,
            side="SELL",
            symbol="VALE3",
        )

    def test_law_104b_moc_loc_allocation_priority_violation(self) -> None:
        # Violação: LOC executou havendo MOC pendente
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_moc_loc_allocation_priority(
                unexecuted_moc_quantity=2000,
                executed_loc_or_limit_quantity=1000,
                side="BUY",
                symbol="ITUB4",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-104B")
        self.assertIn("Ordens LOC/LIMIT executaram 1000", ctx.exception.message)

    # -------------------------------------------------------------------------
    # LAW-104C: assert_closing_auction_volume_maximization
    # -------------------------------------------------------------------------
    def test_law_104c_volume_maximization_success(self) -> None:
        candidate_vols = [10000, 25000, 40000, 32000, 18000]
        assert_closing_auction_volume_maximization(
            selected_volume=40000,
            candidate_volumes=candidate_vols,
            selected_price=Decimal("38.50"),
            symbol="PETR4",
        )

    def test_law_104c_volume_maximization_violation(self) -> None:
        candidate_vols = [10000, 25000, 45000, 32000]
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_closing_auction_volume_maximization(
                selected_volume=30000,
                candidate_volumes=candidate_vols,
                selected_price=Decimal("38.40"),
                symbol="PETR4",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-104C")
        self.assertIn("inferior ao volume máximo executável (45000 lotes)", ctx.exception.message)

    # -------------------------------------------------------------------------
    # LAW-105A: assert_cross_border_collateral_coverage
    # -------------------------------------------------------------------------
    def test_law_105a_cross_border_collateral_coverage_success(self) -> None:
        # Cobertura exata
        assert_cross_border_collateral_coverage(
            total_collateral_net_value_brl=Decimal("15000000.00"),
            required_margin_brl=Decimal("15000000.00"),
            participant_id="MC-00105",
        )
        # Superávit
        assert_cross_border_collateral_coverage(
            total_collateral_net_value_brl=Decimal("18500000.00"),
            required_margin_brl=Decimal("15000000.00"),
            participant_id="MC-00105",
        )

    def test_law_105a_cross_border_collateral_coverage_violation(self) -> None:
        # Déficit de cobertura
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_cross_border_collateral_coverage(
                total_collateral_net_value_brl=Decimal("14200000.00"),
                required_margin_brl=Decimal("15000000.00"),
                participant_id="MC-00105",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-105A")
        self.assertIn("Déficit de Cobertura: R$ 800000.00", ctx.exception.message)

    # -------------------------------------------------------------------------
    # LAW-105B: assert_international_fiduciary_lock
    # -------------------------------------------------------------------------
    def test_law_105b_international_fiduciary_lock_success(self) -> None:
        assert_international_fiduciary_lock(
            international_locked_quantity=5000,
            b3_recorded_pledge_quantity=5000,
            total_deposited_quantity=10000,
            depository_name="Euroclear",
            asset_isin="US912828ZD14",
        )

    def test_law_105b_international_fiduciary_lock_violations(self) -> None:
        # Quantidade internacional diverge do registro B3
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_international_fiduciary_lock(
                international_locked_quantity=4500,
                b3_recorded_pledge_quantity=5000,
                depository_name="DTC",
                asset_isin="US912828ZD14",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-105B")
        self.assertIn("Bloqueado no CSD Internacional (4500) != Registrado na B3 (5000)", ctx.exception.message)

        # Penhor excede total custodiado
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_international_fiduciary_lock(
                international_locked_quantity=6000,
                b3_recorded_pledge_quantity=6000,
                total_deposited_quantity=5000,
                depository_name="Euroclear",
                asset_isin="DE0001102580",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-105B")
        self.assertIn("excede o saldo total sob custódia", ctx.exception.message)

    # -------------------------------------------------------------------------
    # LAW-105C: assert_triparty_zero_sum_settlement
    # -------------------------------------------------------------------------
    def test_law_105c_triparty_zero_sum_settlement_success(self) -> None:
        # Teste com fluxos líquidos somando zero
        movements = [Decimal("1000000.00"), Decimal("-700000.00"), Decimal("-300000.00")]
        assert_triparty_zero_sum_settlement(
            movements_or_debits=movements,
            agreement_id="TRIPARTY-EUROCLEAR-20261107",
        )

        # Teste com débitos e créditos explícitos
        debits = [Decimal("500000.00"), Decimal("500000.00")]
        credits = [Decimal("1000000.00")]
        assert_triparty_zero_sum_settlement(
            movements_or_debits=debits,
            credits=credits,
            agreement_id="TRIPARTY-BNY-20261107",
        )

    def test_law_105c_triparty_zero_sum_settlement_violation(self) -> None:
        movements = [Decimal("1000000.00"), Decimal("-700000.00"), Decimal("-299990.00")]
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_triparty_zero_sum_settlement(
                movements_or_debits=movements,
                agreement_id="TRIPARTY-EUROCLEAR-ERR",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-105C")
        self.assertIn("Soma líquida das pernas (10.00) != 0.00", ctx.exception.message)

    # -------------------------------------------------------------------------
    # LAW-106A: assert_central_bus_global_balance
    # -------------------------------------------------------------------------
    def test_law_106a_central_bus_global_balance_success(self) -> None:
        assert_central_bus_global_balance(
            inbound_total=10000,
            settled_or_processed=9500,
            rejected=300,
            pending_in_flight=200,
            bus_channel="SPB-SETTLEMENT-QUEUE",
        )
        # Valores financeiros Decimal
        assert_central_bus_global_balance(
            inbound_total=Decimal("50000000.00"),
            settled_or_processed=Decimal("48000000.00"),
            rejected=Decimal("1500000.00"),
            pending_in_flight=Decimal("500000.00"),
            bus_channel="RTC-ALLOCATION-BUS",
        )

    def test_law_106a_central_bus_global_balance_violation(self) -> None:
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_central_bus_global_balance(
                inbound_total=10000,
                settled_or_processed=9500,
                rejected=300,
                pending_in_flight=100,  # Falta 100
                bus_channel="SPB-SETTLEMENT-QUEUE",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-106A")
        self.assertIn("Inbound (10000) != Processadas (9500) + Rejeitadas (300) + Pendentes (100)", ctx.exception.message)

    # -------------------------------------------------------------------------
    # LAW-106B: assert_default_waterfall_containment
    # -------------------------------------------------------------------------
    def test_law_106b_default_waterfall_containment_success(self) -> None:
        assert_default_waterfall_containment(
            gross_loss=Decimal("120000000.00"),
            total_absorbed=Decimal("120000000.00"),
            residual_deficit=Decimal("0.00"),
            safeguards_capacity=Decimal("500000000.00"),
            clearing_member_id="MC-00105",
        )

    def test_law_106b_default_waterfall_containment_violations(self) -> None:
        # Quebra contábil: Perda != Absorvido + Residual
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_default_waterfall_containment(
                gross_loss=Decimal("150000000.00"),
                total_absorbed=Decimal("120000000.00"),
                residual_deficit=Decimal("0.00"),
                clearing_member_id="MC-00105",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-106B")
        self.assertIn("Perda Bruta (150000000.00) != Absorvido (120000000.00)", ctx.exception.message)

        # Falha de contenção: Déficit residual > 0 (risco sistêmico)
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_default_waterfall_containment(
                gross_loss=Decimal("150000000.00"),
                total_absorbed=Decimal("120000000.00"),
                residual_deficit=Decimal("30000000.00"),
                clearing_member_id="MC-00105",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-106B")
        self.assertIn("Déficit Residual Descoberto de R$ 30000000.00", ctx.exception.message)

        # Absorvido excede capacidade máxima de salvaguardas
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_default_waterfall_containment(
                gross_loss=Decimal("600000000.00"),
                total_absorbed=Decimal("600000000.00"),
                residual_deficit=Decimal("0.00"),
                safeguards_capacity=Decimal("500000000.00"),
                clearing_member_id="MC-00105",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-106B")
        self.assertIn("excede Capacidade Máxima (500000000.00)", ctx.exception.message)

    # -------------------------------------------------------------------------
    # LAW-106C: assert_cross_system_atomic_settlement
    # -------------------------------------------------------------------------
    def test_law_106c_cross_system_atomic_settlement_success(self) -> None:
        # Ambos concluídos com sucesso (booleano)
        assert_cross_system_atomic_settlement(
            system_a_completed=True,
            system_b_completed=True,
            system_a_name="B3-CSD",
            system_b_name="BACEN-STR",
            transaction_id="TX-20261107-001",
            amount_system_a=Decimal("2500000.00"),
            amount_system_b=Decimal("2500000.00"),
        )
        # Ambos abortados em rollback atômico
        assert_cross_system_atomic_settlement(
            system_a_completed=False,
            system_b_completed=False,
            system_a_status="ROLLED_BACK",
            system_b_status="CANCELLED",
            transaction_id="TX-20261107-002",
        )
        # Sucesso com strings
        assert_cross_system_atomic_settlement(
            system_a_completed="SETTLED",
            system_b_completed="COMPLETED",
            transaction_id="TX-20261107-003",
        )

    def test_law_106c_cross_system_atomic_settlement_violations(self) -> None:
        # Assimetria: Sistema A concluiu mas Sistema B falhou
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_cross_system_atomic_settlement(
                system_a_completed=True,
                system_b_completed=False,
                system_a_name="B3-CLEARING",
                system_b_name="SELIC",
                transaction_id="TX-20261107-ERR1",
            )
        self.assertEqual(ctx.exception.law_code, "LAW-106C")
        self.assertIn("Princípio All-or-None quebrado", ctx.exception.message)

        # Divergência de montante financeiro entre pernas
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_cross_system_atomic_settlement(
                system_a_completed=True,
                system_b_completed=True,
                system_a_name="B3-CLEARING",
                system_b_name="SPB",
                transaction_id="TX-20261107-ERR2",
                amount_system_a=Decimal("1000000.00"),
                amount_system_b=Decimal("1000001.00"),
            )
        self.assertEqual(ctx.exception.law_code, "LAW-106C")
        self.assertIn("Montante B3-CLEARING (1000000.00) != Montante SPB (1000001.00)", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
