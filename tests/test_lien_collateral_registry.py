"""Testes unitários e de integração para Central de Garantias e Gravames B3 (FL-037)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unittest

from src.lien_collateral_registry import (
    CollateralAssetPool,
    CollateralPoolNotFoundError,
    DuplicateLienError,
    DuplicatePoolError,
    ExcussionWaterfallResult,
    FreeTradingBlockedError,
    InsufficientCollateralCapacityError,
    InvalidLienOperationError,
    LienCollateralRegistryEngine,
    LienModality,
    LienNotFoundError,
    LienPriorityRank,
    LienRegistration,
    LienStatus,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "lien_collateral_registry.json"
)


class TestLienCollateralRegistry(unittest.TestCase):
    """Suíte determinística para o fluxo FL-037 (Lei 14.711/2023 e Resolução BCB 264)."""

    def test_load_fixture_pools_and_multi_rank_liens(self) -> None:
        """Valida o carregamento do lote de CDB, Debêntures e Ações com múltiplos graus."""
        engine = LienCollateralRegistryEngine.from_fixture(
            FIXTURE_PATH, execute_events=False
        )
        self.assertEqual(len(engine.pools), 3)
        self.assertEqual(len(engine.liens), 7)

        # Pool 1: CDB (1.000 * R$ 1.050,00 = R$ 1.050.000,00 bruto; haircut 5% -> R$ 997.500,00 líquido)
        cdb_pool = engine.get_pool("POOL-CDB-2026-001")
        self.assertEqual(cdb_pool.gross_market_value_brl, Decimal("1050000.00"))
        self.assertEqual(cdb_pool.net_valuation_amount_brl, Decimal("997500.00"))
        # 1º grau (R$ 600.000) + 2º grau (R$ 250.000) = R$ 850.000 comprometidos
        self.assertEqual(
            engine.committed_valuation_brl("POOL-CDB-2026-001"), Decimal("850000.00")
        )
        self.assertEqual(
            engine.available_valuation_capacity_brl("POOL-CDB-2026-001"),
            Decimal("147500.00"),
        )
        self.assertTrue(engine.is_free_trading_blocked("POOL-CDB-2026-001"))

        # Pool 2: Debêntures VALE38 (500 * R$ 2.000 = R$ 1.000.000 bruto; haircut 10% -> R$ 900.000 líquido)
        # 1º grau (R$ 500k) + 2º grau (R$ 250k) + 3º grau (R$ 100k) = R$ 850k comprometidos
        deb_pool = engine.get_pool("POOL-DEB-2026-002")
        self.assertEqual(deb_pool.net_valuation_amount_brl, Decimal("900000.00"))
        self.assertEqual(
            engine.committed_valuation_brl("POOL-DEB-2026-002"), Decimal("850000.00")
        )
        self.assertEqual(
            engine.available_valuation_capacity_brl("POOL-DEB-2026-002"),
            Decimal("50000.00"),
        )

        # Pool 3: Ações ITUB4 (50.000 * R$ 35,00 = R$ 1.750.000 bruto; haircut 20% -> R$ 1.400.000 líquido)
        # 1º grau (R$ 900k) + 2º grau (R$ 300k) = R$ 1.200.000 comprometidos
        acao_pool = engine.get_pool("POOL-ACAO-2026-003")
        self.assertEqual(acao_pool.net_valuation_amount_brl, Decimal("1400000.00"))
        self.assertEqual(
            engine.committed_valuation_brl("POOL-ACAO-2026-003"), Decimal("1200000.00")
        )
        self.assertEqual(
            engine.available_valuation_capacity_brl("POOL-ACAO-2026-003"),
            Decimal("200000.00"),
        )

    def test_fixture_events_excussion_and_release(self) -> None:
        """Executa os eventos de excussão e baixa da fixture e valida saldos finais."""
        engine = LienCollateralRegistryEngine.from_fixture(
            FIXTURE_PATH, execute_events=True
        )
        self.assertEqual(len(engine.excussion_history), 1)

        exc_result: ExcussionWaterfallResult = engine.excussion_history[0]
        self.assertEqual(exc_result.event_id, "EXC-2026-0001")
        self.assertEqual(exc_result.pool_id, "POOL-CDB-2026-001")
        self.assertEqual(exc_result.gross_proceeds_brl, Decimal("980000.00"))
        self.assertEqual(exc_result.execution_costs_brl, Decimal("5000.00"))
        self.assertEqual(exc_result.net_proceeds_brl, Decimal("975000.00"))

        # 1º Grau recebe R$ 600.000,00 (100%)
        self.assertEqual(
            exc_result.PayoutByRank(LienPriorityRank.PRIMEIRO_GRAU),
            Decimal("600000.00"),
        )
        # 2º Grau recebe R$ 250.000,00 (100%)
        self.assertEqual(
            exc_result.PayoutByRank(LienPriorityRank.SEGUNDO_GRAU),
            Decimal("250000.00"),
        )
        # Sobra devolvida ao devedor garantidor: 975.000 - 850.000 = R$ 125.000,00
        self.assertEqual(
            exc_result.grantor_surplus_returned_brl, Decimal("125000.00")
        )
        self.assertTrue(exc_result.senior_fully_satisfied)
        self.assertTrue(exc_result.prelacy_order_respected)

        # Evento de baixa no Pool 3 (GRV-2026-000301 baixado, mas GRV-2026-000302 de 2º grau continua ativo)
        released_lien = engine.liens["GRV-2026-000301"]
        self.assertEqual(released_lien.status, LienStatus.BAIXADO_LIBERADO)
        self.assertEqual(
            released_lien.release_authorization_token, "AUTH-XP-20261103-99812"
        )
        # Como o 2º grau (R$ 300k) segue ativo, o pool continua bloqueado para negociação livre
        self.assertTrue(engine.is_free_trading_blocked("POOL-ACAO-2026-003"))
        self.assertEqual(
            engine.committed_valuation_brl("POOL-ACAO-2026-003"), Decimal("300000.00")
        )

    def test_reject_lien_exceeding_net_valuation_capacity(self) -> None:
        """Impede averbação de gravame cujo valor supere a capacidade líquida disponível."""
        engine = LienCollateralRegistryEngine.from_fixture(
            FIXTURE_PATH, execute_events=False
        )
        # Pool DEB tem apenas R$ 50.000,00 disponíveis de avaliação líquida
        over_capacity_lien = LienRegistration(
            lien_id="GRV-OVER-999",
            pool_id="POOL-DEB-2026-002",
            modality=LienModality.PENHOR,
            priority_rank=LienPriorityRank.TERCEIRO_GRAU,
            status=LienStatus.ATIVO_BLOQUEADO,
            creditor_document="11111111000199",
            creditor_name="CREDOR EXCEDENTE S.A.",
            contract_reference="CCB-OVER-01",
            secured_amount_brl=Decimal("50000.01"),
            earmarked_quantity=Decimal("10.0000"),
            registration_timestamp="2026-11-03T10:00:00Z",
        )
        with self.assertRaises(InsufficientCollateralCapacityError):
            engine.constitute_lien(over_capacity_lien)

    def test_waterfall_partial_coverage_strict_prelacy_order(self) -> None:
        """Verifica que na excussão deficitária o 1º grau recebe 100% antes do 2º grau e 3º grau recebe zero."""
        engine = LienCollateralRegistryEngine.from_fixture(
            FIXTURE_PATH, execute_events=False
        )
        # No POOL-DEB-2026-002:
        # 1º Grau = R$ 500.000,00
        # 2º Grau = R$ 250.000,00
        # 3º Grau = R$ 100.000,00
        # Simulamos excussão com produto líquido de R$ 680.000,00 (Bruto 685k - Custos 5k)
        result = engine.execute_excussion_waterfall(
            event_id="EXC-PARTIAL-DEB",
            pool_id="POOL-DEB-2026-002",
            gross_proceeds_brl=Decimal("685000.00"),
            execution_costs_brl=Decimal("5000.00"),
            execution_timestamp="2026-11-03T18:00:00Z",
        )

        self.assertEqual(result.net_proceeds_brl, Decimal("680000.00"))
        self.assertTrue(result.senior_fully_satisfied)
        self.assertTrue(result.prelacy_order_respected)

        alloc_1, alloc_2, alloc_3 = result.allocations
        # 1º Grau recebe 100% (R$ 500.000,00)
        self.assertEqual(alloc_1.priority_rank, LienPriorityRank.PRIMEIRO_GRAU)
        self.assertEqual(alloc_1.paid_amount_brl, Decimal("500000.00"))
        self.assertEqual(alloc_1.shortfall_brl, Decimal("0.00"))
        self.assertTrue(alloc_1.fully_satisfied)

        # 2º Grau recebe apenas o excedente (R$ 180.000,00) e fica com shortfall de R$ 70.000,00
        self.assertEqual(alloc_2.priority_rank, LienPriorityRank.SEGUNDO_GRAU)
        self.assertEqual(alloc_2.paid_amount_brl, Decimal("180000.00"))
        self.assertEqual(alloc_2.shortfall_brl, Decimal("70000.00"))
        self.assertFalse(alloc_2.fully_satisfied)

        # 3º Grau recebe R$ 0,00 e fica com 100% de shortfall (R$ 100.000,00)
        self.assertEqual(alloc_3.priority_rank, LienPriorityRank.TERCEIRO_GRAU)
        self.assertEqual(alloc_3.paid_amount_brl, Decimal("0.00"))
        self.assertEqual(alloc_3.shortfall_brl, Decimal("100000.00"))
        self.assertFalse(alloc_3.fully_satisfied)

        # Sobra devolvida ao devedor garantidor = R$ 0,00
        self.assertEqual(result.grantor_surplus_returned_brl, Decimal("0.00"))
        self.assertEqual(result.total_creditors_paid_brl, Decimal("680000.00"))
        self.assertEqual(result.total_shortfall_brl, Decimal("170000.00"))

    def test_waterfall_severe_stress_only_senior_partially_paid(self) -> None:
        """Verifica estresse extremo onde o valor apurado não cobre sequer 100% do 1º grau."""
        engine = LienCollateralRegistryEngine.from_fixture(
            FIXTURE_PATH, execute_events=False
        )
        # No POOL-CDB-2026-001: 1º Grau = R$ 600k, 2º Grau = R$ 250k
        # Valor líquido apurado = R$ 420.000,00
        result = engine.execute_excussion_waterfall(
            event_id="EXC-STRESS-CDB",
            pool_id="POOL-CDB-2026-001",
            gross_proceeds_brl=Decimal("422000.00"),
            execution_costs_brl=Decimal("2000.00"),
        )
        self.assertFalse(result.senior_fully_satisfied)
        self.assertTrue(result.prelacy_order_respected)
        self.assertEqual(
            result.PayoutByRank(LienPriorityRank.PRIMEIRO_GRAU), Decimal("420000.00")
        )
        self.assertEqual(
            result.PayoutByRank(LienPriorityRank.SEGUNDO_GRAU), Decimal("0.00")
        )
        self.assertEqual(result.grantor_surplus_returned_brl, Decimal("0.00"))

    def test_free_trading_blocking_and_full_release_unblocking(self) -> None:
        """Garante que a negociação livre é bloqueada enquanto houver gravame e liberada após baixa total."""
        engine = LienCollateralRegistryEngine.from_fixture(
            FIXTURE_PATH, execute_events=False
        )
        pool_id = "POOL-ACAO-2026-003"

        with self.assertRaises(FreeTradingBlockedError):
            engine.validate_free_trade_order(pool_id, Decimal("100.0000"))
        self.assertEqual(engine.free_tradable_quantity(pool_id), Decimal("0.0000"))

        # Baixa o 1º grau -> ainda resta o 2º grau, deve permanecer bloqueado
        engine.release_lien(
            "GRV-2026-000301", "2026-11-03T12:00:00Z", "TOKEN-CREDOR-1"
        )
        self.assertTrue(engine.is_free_trading_blocked(pool_id))
        with self.assertRaises(FreeTradingBlockedError):
            engine.validate_free_trade_order(pool_id, Decimal("100.0000"))

        # Baixa o 2º grau -> sem gravames ativos, ativo retorna à livre negociação na conta 2101-6
        engine.release_lien(
            "GRV-2026-000302", "2026-11-03T12:30:00Z", "TOKEN-CREDOR-2"
        )
        self.assertFalse(engine.is_free_trading_blocked(pool_id))
        self.assertEqual(engine.free_tradable_quantity(pool_id), Decimal("50000.0000"))
        engine.validate_free_trade_order(pool_id, Decimal("50000.0000"))

    def test_domain_validations_and_edge_cases(self) -> None:
        """Valida regras de domínio: ISIN inválido, duplicidade, modalidades e sumário."""
        with self.assertRaises(InvalidLienOperationError):
            CollateralAssetPool(
                pool_id="POOL-BAD-ISIN",
                asset_type="CDB",
                ticker_or_cetip_code="CDB001",
                isin="INVALID",
                grantor_document="12345678000190",
                grantor_name="TESTE S.A.",
                custodian_participant_code="00114",
                total_quantity=Decimal("100"),
                unit_market_price=Decimal("1000"),
                haircut_pct=Decimal("0.05"),
            )

        self.assertTrue(LienModality.ALIENACAO_FIDUCIARIA.transfers_fiduciary_ownership)
        self.assertTrue(LienModality.CESSAO_FIDUCIARIA.transfers_fiduciary_ownership)
        self.assertFalse(LienModality.PENHOR.transfers_fiduciary_ownership)
        self.assertFalse(LienModality.USUFRUTO.transfers_fiduciary_ownership)

        engine = LienCollateralRegistryEngine.from_fixture(
            FIXTURE_PATH, execute_events=True
        )
        report = engine.summary_report()
        self.assertEqual(report["total_pools"], 3)
        self.assertEqual(report["total_registered_liens"], 7)
        self.assertEqual(report["total_excussions_executed"], 1)

        with self.assertRaises(DuplicatePoolError):
            engine.register_pool(engine.get_pool("POOL-CDB-2026-001"))

        with self.assertRaises(DuplicateLienError):
            engine.constitute_lien(engine.liens["GRV-2026-000201"])

        with self.assertRaises(CollateralPoolNotFoundError):
            engine.get_pool("POOL-INEXISTENTE")

        with self.assertRaises(LienNotFoundError):
            engine.release_lien("GRV-INEXISTENTE", "2026-11-03T12:00:00Z", "TOKEN")


if __name__ == "__main__":
    unittest.main()
