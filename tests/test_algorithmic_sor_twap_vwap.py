"""Suíte de Testes Unitários e Invariantes Formais — SOR e Fatiamento Algorítmico TWAP/VWAP/POV (FL-104).
====================================================================================================

Bateria abrangente de testes validando:
1. Carregamento de fixtures canônicas JSON e integridade dos parâmetros.
2. Execução da estratégia TWAP com jitter estocástico anti-predatório e determinismo por semente.
3. Execução da estratégia VWAP ponderada pela curva clássica em U (U-shape) da B3 via Hare-Niemeyer.
4. Execução da estratégia POV (Percent-of-Volume) com teto estrito de participação e gestão de residual.
5. Guardrails de segurança: Fat-Finger (nocional, quantidade, desvio), Túneis PUMA B3 e limites de Slippage.
6. Prova formal da invariante LAW-110: Conservação estrita da quantidade e não-transgressão de preço limite.
7. Roteamento inteligente de ordens (SOR) entre PUMA LIT, DARK MIDPOINT, RLP e LEILÃO conforme urgência.
8. Serialização e desserialização completa (`to_dict` / `from_dict`) sem perda de precisão.
"""

from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.algorithmic_sor_twap_vwap import (
    AlgorithmicSORError,
    AlgorithmicSOREngine,
    AlgorithmicStrategy,
    ChildOrder,
    ChildOrderStatus,
    ExecutionVenue,
    FatFingerGuardrail,
    FatFingerViolationError,
    InvariantConservationError,
    OrderSide,
    ParentOrder,
    PumaPriceTunnelViolationError,
    PumaTunnelGuardrail,
    SlicingPlan,
    SlippageExceededError,
    SlippageGuardrail,
    UrgencyLevel,
    DEFAULT_B3_INTRADAY_PROFILE,
    round_price,
)
from src.invariants import InvariantViolationError


class TestAlgorithmicSorTwapVwap(unittest.TestCase):
    """Bateria de testes unitários do motor de roteamento e fatiamento algorítmico FL-104."""

    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixture_file = self.fixtures_dir / "algorithmic_sor_twap_vwap.json"

        self.engine = AlgorithmicSOREngine()
        self.reference_price = Decimal("34.50")

    # --------------------------------------------------------------------------
    # 1. Carregamento e Validação da Fixture Canônica
    # --------------------------------------------------------------------------

    def test_load_from_fixture(self) -> None:
        """Valida o carregamento canônico dos dados a partir do JSON de fixture."""
        engine, parent, ref_price = AlgorithmicSOREngine.load_from_fixture(self.fixture_file)

        self.assertEqual(ref_price, Decimal("34.50"))
        self.assertEqual(parent.symbol, "PETR4")
        self.assertEqual(parent.side, OrderSide.BUY)
        self.assertEqual(parent.quantity, 100_000)
        self.assertEqual(parent.limit_price, Decimal("34.80"))
        self.assertEqual(parent.strategy, AlgorithmicStrategy.TWAP)
        self.assertEqual(len(engine.intraday_profile), 10)

    # --------------------------------------------------------------------------
    # 2. Estratégia TWAP com Jitter Estocástico Anti-Predação
    # --------------------------------------------------------------------------

    def test_twap_slicing_and_law110_conservation(self) -> None:
        """Valida que o TWAP divide em N janelas, aplica jitter e obedece estritamente a LAW-110."""
        parent = ParentOrder(
            parent_id="TEST-TWAP-001",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=100_000,
            arrival_price=Decimal("34.50"),
            limit_price=Decimal("34.80"),
            strategy=AlgorithmicStrategy.TWAP,
            time_windows=10,
            start_time="10:00:00",
            end_time="17:00:00",
            lot_size=100,
            jitter_range_pct=Decimal("0.15"),
            seed=42,
        )

        plan = self.engine.generate_twap_slices(parent, self.reference_price)

        # 1. Conservação estrita LAW-110: Soma(fatias) == Ordem Mãe
        self.assertEqual(plan.total_sliced_quantity, 100_000)
        self.assertEqual(plan.residual_unallocated_quantity, 0)
        self.assertEqual(sum(c.quantity for c in plan.child_orders), 100_000)

        # 2. Todas as fatias devem ser positivas e múltiplos de lot_size
        self.assertEqual(len(plan.child_orders), 10)
        for child in plan.child_orders:
            self.assertGreater(child.quantity, 0)
            self.assertEqual(child.quantity % 100, 0)
            # Preço não pode violar limit_price na ponta BUY
            self.assertLessEqual(child.target_price, Decimal("34.80"))

        # 3. Presença de variação gerada pelo Jitter (nem todas as fatias têm o mesmo tamanho base de 10.000)
        quantities = [c.quantity for c in plan.child_orders]
        self.assertFalse(all(q == 10_000 for q in quantities), "Jitter estocástico deve criar variabilidade anti-predatória")

        # 4. Invariante formal
        inv = plan.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["all_invariants_hold"])
        self.assertTrue(inv["law_110_quantity_conservation"])

    def test_twap_deterministic_seed(self) -> None:
        """Garante que a mesma semente produz fatias e agendamentos idênticos e reprodutíveis."""
        parent1 = ParentOrder(
            parent_id="SEED-01",
            symbol="VALE3",
            side=OrderSide.SELL,
            quantity=50_000,
            arrival_price=Decimal("60.00"),
            strategy=AlgorithmicStrategy.TWAP,
            time_windows=5,
            seed=12345,
        )
        parent2 = ParentOrder(
            parent_id="SEED-02",
            symbol="VALE3",
            side=OrderSide.SELL,
            quantity=50_000,
            arrival_price=Decimal("60.00"),
            strategy=AlgorithmicStrategy.TWAP,
            time_windows=5,
            seed=12345,
        )

        plan1 = self.engine.generate_twap_slices(parent1, Decimal("60.00"))
        plan2 = self.engine.generate_twap_slices(parent2, Decimal("60.00"))

        qtys1 = [c.quantity for c in plan1.child_orders]
        qtys2 = [c.quantity for c in plan2.child_orders]
        self.assertEqual(qtys1, qtys2)

        times1 = [c.time_offset_seconds for c in plan1.child_orders]
        times2 = [c.time_offset_seconds for c in plan2.child_orders]
        self.assertEqual(times1, times2)

    # --------------------------------------------------------------------------
    # 3. Estratégia VWAP com Perfil Histórico B3 (U-Shape Curve)
    # --------------------------------------------------------------------------

    def test_vwap_slicing_u_shape_and_law110_conservation(self) -> None:
        """Valida que o VWAP aloca proporcionalmente à curva em U e conserva 100% das cotas."""
        parent = ParentOrder(
            parent_id="TEST-VWAP-001",
            symbol="VALE3",
            side=OrderSide.SELL,
            quantity=150_000,
            arrival_price=Decimal("62.00"),
            limit_price=Decimal("61.50"),
            strategy=AlgorithmicStrategy.VWAP,
            time_windows=10,
            start_time="10:00:00",
            end_time="17:00:00",
            lot_size=100,
        )

        plan = self.engine.generate_vwap_slices(parent, Decimal("62.00"))

        # Conservação LAW-110
        self.assertEqual(plan.total_sliced_quantity, 150_000)
        self.assertEqual(sum(c.quantity for c in plan.child_orders), 150_000)
        self.assertEqual(plan.residual_unallocated_quantity, 0)

        # Checa padrão da curva em U: Abertura (janela 1: peso 15%) e Encerramento (janela 10: peso 16%)
        # devem ter volumes substancialmente maiores que o horário de almoço (janela 5: peso 5%)
        qty_open = plan.child_orders[0].quantity
        qty_midday = plan.child_orders[4].quantity
        qty_close = plan.child_orders[9].quantity

        self.assertGreater(qty_open, qty_midday)
        self.assertGreater(qty_close, qty_midday)
        self.assertAlmostEqual(qty_open, 22_500, delta=200)   # 15% de 150.000 = 22.500
        self.assertAlmostEqual(qty_close, 24_000, delta=200)  # 16% de 150.000 = 24.000
        self.assertAlmostEqual(qty_midday, 7_500, delta=200)  # 5% de 150.000 = 7.500

        # Verificação formal
        inv = plan.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["all_invariants_hold"])

    # --------------------------------------------------------------------------
    # 4. Estratégia POV (Percent-of-Volume / Participation Rate)
    # --------------------------------------------------------------------------

    def test_pov_slicing_within_market_capacity(self) -> None:
        """Valida que o POV respeita rigorosamente o teto de participação por janela quando há capacidade."""
        parent = ParentOrder(
            parent_id="TEST-POV-001",
            symbol="BBAS3",
            side=OrderSide.BUY,
            quantity=80_000,
            arrival_price=Decimal("28.00"),
            limit_price=Decimal("28.50"),
            strategy=AlgorithmicStrategy.POV,
            pov_participation_rate=Decimal("0.20"),  # Máximo de 20% do volume da janela
            lot_size=100,
        )

        projected_vols = [80_000, 70_000, 50_000, 40_000, 40_000, 50_000, 60_000, 110_000]
        # Capacidade total sob 20%: sum(vols) = 500.000 * 0.20 = 100.000 cotas.
        # Como a ordem é de 80.000 cotas, cabe integralmente.

        plan = self.engine.generate_pov_slices(parent, Decimal("28.00"), projected_vols)

        self.assertEqual(plan.total_sliced_quantity, 80_000)
        self.assertEqual(plan.residual_unallocated_quantity, 0)
        self.assertEqual(sum(c.quantity for c in plan.child_orders), 80_000)

        # Checa que nenhuma fatia excedeu 20% do volume da respectiva janela
        for child in plan.child_orders:
            win_idx = child.strategy_metadata["window_index"] - 1
            mkt_vol = projected_vols[win_idx]
            max_allowed = int(mkt_vol * 0.20)
            self.assertLessEqual(child.quantity, max_allowed)

        inv = plan.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["all_invariants_hold"])

    def test_pov_slicing_exceeding_capacity_with_residual(self) -> None:
        """Valida gestão de capacidade e conservação LAW-110 com residual quando a ordem excede o POV permitido."""
        parent = ParentOrder(
            parent_id="TEST-POV-002",
            symbol="ITUB4",
            side=OrderSide.BUY,
            quantity=100_000,
            arrival_price=Decimal("32.00"),
            strategy=AlgorithmicStrategy.POV,
            pov_participation_rate=Decimal("0.10"),  # Teto estrito de 10%
            lot_size=100,
        )

        # Volume projetado de mercado muito baixo (apenas 300.000 ações)
        projected_vols = [50_000, 50_000, 50_000, 50_000, 50_000, 50_000]
        # Capacidade máxima a 10% = 30.000 cotas.

        plan = self.engine.generate_pov_slices(parent, Decimal("32.00"), projected_vols)

        # Alocou o teto de 30.000 cotas
        self.assertEqual(plan.total_sliced_quantity, 30_000)
        # Residual não alocado de 70.000 cotas
        self.assertEqual(plan.residual_unallocated_quantity, 70_000)

        # A soma das fatias + residual continua exatamente igual à ordem-mãe (LAW-110)!
        self.assertEqual(plan.total_sliced_quantity + plan.residual_unallocated_quantity, parent.quantity)

        inv = plan.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["law_110_quantity_conservation"])
        self.assertTrue(inv["all_invariants_hold"])

    # --------------------------------------------------------------------------
    # 5. Guardrails de Segurança (Fat-Finger, Túneis PUMA, Slippage)
    # --------------------------------------------------------------------------

    def test_fat_finger_rejection_quantity_and_notional(self) -> None:
        """Testa bloqueio de ordens aberrantes por quantidade ou nocional excessivo."""
        # Quantidade acima do teto de 1 milhão
        parent_big_qty = ParentOrder(
            parent_id="FAT-01",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=2_000_000,
            arrival_price=Decimal("30.00"),
            strategy=AlgorithmicStrategy.TWAP,
        )
        with self.assertRaises(FatFingerViolationError):
            self.engine.generate_twap_slices(parent_big_qty, Decimal("30.00"))

        # Nocional acima de R$ 50 milhões (ex: 900.000 cotas a R$ 60,00 = R$ 54M)
        parent_big_notional = ParentOrder(
            parent_id="FAT-02",
            symbol="VALE3",
            side=OrderSide.BUY,
            quantity=900_000,
            arrival_price=Decimal("60.00"),
            strategy=AlgorithmicStrategy.TWAP,
        )
        with self.assertRaises(FatFingerViolationError):
            self.engine.generate_twap_slices(parent_big_notional, Decimal("60.00"))

    def test_fat_finger_price_deviation(self) -> None:
        """Testa bloqueio de ordem com preço limite muito distante do preço de referência."""
        parent_bad_price = ParentOrder(
            parent_id="FAT-03",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=10_000,
            arrival_price=Decimal("30.00"),
            limit_price=Decimal("35.00"),  # Desvio de ~16.6% vs Ref de 30.00 (limite é 10%)
            strategy=AlgorithmicStrategy.TWAP,
        )
        with self.assertRaises(FatFingerViolationError):
            self.engine.generate_twap_slices(parent_bad_price, Decimal("30.00"))

    def test_puma_price_tunnel_violation(self) -> None:
        """Testa rejeição caso o preço viole a banda máxima de oscilação PUMA B3 (5%)."""
        tunnel = PumaTunnelGuardrail(rejection_tunnel_pct=Decimal("0.05"))
        ref_price = Decimal("100.00")
        lower, upper = tunnel.get_bounds(ref_price)
        self.assertEqual(lower, Decimal("95.00"))
        self.assertEqual(upper, Decimal("105.00"))

        # Preço dentro do túnel deve passar
        tunnel.validate_price(Decimal("98.00"), ref_price)
        tunnel.validate_price(Decimal("104.50"), ref_price)

        # Preço fora do túnel deve disparar PumaPriceTunnelViolationError
        with self.assertRaises(PumaPriceTunnelViolationError):
            tunnel.validate_price(Decimal("106.00"), ref_price)

        with self.assertRaises(PumaPriceTunnelViolationError):
            tunnel.validate_price(Decimal("94.00"), ref_price)

    def test_slippage_guardrail_violation(self) -> None:
        """Testa verificação estrita de limite de slippage em basis points."""
        slip = SlippageGuardrail(max_slippage_bps=Decimal("50.0"))  # 50 bps = 0.50%
        arrival = Decimal("100.00")

        # Slippage de 20 bps em compra (preço 100.20) -> Permitido
        slip.validate_slippage(Decimal("100.20"), arrival, OrderSide.BUY)

        # Slippage de 60 bps em compra (preço 100.60) -> Violou o limite de 50 bps
        with self.assertRaises(SlippageExceededError):
            slip.validate_slippage(Decimal("100.60"), arrival, OrderSide.BUY)

        # Slippage em venda (preço 99.40 -> queda de 60 bps) -> Violou limite
        with self.assertRaises(SlippageExceededError):
            slip.validate_slippage(Decimal("99.40"), arrival, OrderSide.SELL)

    # --------------------------------------------------------------------------
    # 6. Invariante LAW-110: Testes de Violação Induzida
    # --------------------------------------------------------------------------

    def test_law110_quantity_tampering_detection(self) -> None:
        """Garante que qualquer inconsistência de conservação de cotas dispara exceção imediata."""
        parent = ParentOrder(
            parent_id="TEST-TAMPER-01",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=10_000,
            arrival_price=Decimal("30.00"),
            strategy=AlgorithmicStrategy.TWAP,
            time_windows=2,
            jitter_range_pct=None,
        )
        plan = self.engine.generate_twap_slices(parent, Decimal("30.00"))

        # Adultera manualmente a quantidade de uma fatia
        plan.child_orders[0].quantity += 100

        with self.assertRaises((InvariantViolationError, InvariantConservationError)):
            plan.verify_invariants(raise_on_error=True)

    def test_law110_price_limit_tampering_detection(self) -> None:
        """Garante que violação do preço limite da ordem-mãe dispara falha na invariante LAW-110."""
        parent = ParentOrder(
            parent_id="TEST-TAMPER-02",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=10_000,
            arrival_price=Decimal("30.00"),
            limit_price=Decimal("30.10"),
            strategy=AlgorithmicStrategy.TWAP,
            time_windows=2,
            jitter_range_pct=None,
        )
        plan = self.engine.generate_twap_slices(parent, Decimal("30.00"))

        # Adultera o preço para ultrapassar o preço limite de compra
        plan.child_orders[0].target_price = Decimal("30.20")

        with self.assertRaises((InvariantViolationError, InvariantConservationError)):
            plan.verify_invariants(raise_on_error=True)

    # --------------------------------------------------------------------------
    # 7. Roteamento Inteligente (SOR) e Urgência
    # --------------------------------------------------------------------------

    def test_sor_venue_selection_by_urgency(self) -> None:
        """Valida que o roteamento seleciona venues conforme a política de urgência."""
        # LOW Urgency com Dark Midpoint disponível
        parent_low = ParentOrder(
            parent_id="SOR-01",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=20_000,
            arrival_price=Decimal("30.00"),
            strategy=AlgorithmicStrategy.TWAP,
            urgency=UrgencyLevel.LOW,
            time_windows=2,
        )
        depth = {ExecutionVenue.PUMA_DARK_MIDPOINT: 50_000, ExecutionVenue.PUMA_LIT: 100_000}
        plan_low = self.engine.generate_twap_slices(parent_low, Decimal("30.00"), market_depth=depth)
        self.assertEqual(plan_low.child_orders[0].target_venue, ExecutionVenue.PUMA_DARK_MIDPOINT)

        # HIGH Urgency deve agredir PUMA_LIT
        parent_high = ParentOrder(
            parent_id="SOR-02",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=20_000,
            arrival_price=Decimal("30.00"),
            strategy=AlgorithmicStrategy.TWAP,
            urgency=UrgencyLevel.HIGH,
            time_windows=2,
        )
        plan_high = self.engine.generate_twap_slices(parent_high, Decimal("30.00"), market_depth=depth)
        self.assertEqual(plan_high.child_orders[0].target_venue, ExecutionVenue.PUMA_LIT)

    # --------------------------------------------------------------------------
    # 8. Testes de Serialização Roundtrip (to_dict / from_dict)
    # --------------------------------------------------------------------------

    def test_serialization_roundtrip(self) -> None:
        """Valida serialização e desserialização completa de todos os objetos do motor."""
        parent = ParentOrder(
            parent_id="SERIAL-01",
            symbol="B3SA3",
            side=OrderSide.BUY,
            quantity=50_000,
            arrival_price=Decimal("12.50"),
            limit_price=Decimal("12.60"),
            strategy=AlgorithmicStrategy.VWAP,
            time_windows=5,
            lot_size=100,
        )
        plan = self.engine.generate_vwap_slices(parent, Decimal("12.50"))

        data = plan.to_dict()
        json_str = json.dumps(data)
        loaded_data = json.loads(json_str)

        restored_plan = SlicingPlan.from_dict(loaded_data)
        self.assertEqual(restored_plan.parent_order.parent_id, "SERIAL-01")
        self.assertEqual(restored_plan.total_sliced_quantity, 50_000)
        self.assertEqual(len(restored_plan.child_orders), len(plan.child_orders))
        self.assertEqual(restored_plan.expected_benchmark_price, plan.expected_benchmark_price)

        # Valida invariantes do plano restaurado
        inv = restored_plan.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["all_invariants_hold"])


    # --------------------------------------------------------------------------
    # 9. Testes Adicionais de Casos de Borda e Resiliência
    # --------------------------------------------------------------------------

    def test_twap_single_window(self) -> None:
        """Testa fatiamento TWAP quando há apenas 1 janela."""
        parent = ParentOrder(
            parent_id="TWAP-SINGLE",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=5_000,
            arrival_price=Decimal("30.00"),
            strategy=AlgorithmicStrategy.TWAP,
            time_windows=1,
            lot_size=100,
        )
        plan = self.engine.generate_twap_slices(parent, Decimal("30.00"))
        self.assertEqual(len(plan.child_orders), 1)
        self.assertEqual(plan.child_orders[0].quantity, 5_000)
        self.assertEqual(plan.total_sliced_quantity, 5_000)
        inv = plan.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["all_invariants_hold"])

    def test_twap_lots_less_than_windows(self) -> None:
        """Garante que se total de lotes < janelas, o número de janelas é adaptado sem erro."""
        parent = ParentOrder(
            parent_id="TWAP-FEW-LOTS",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=300,  # Apenas 3 lotes de 100
            arrival_price=Decimal("30.00"),
            strategy=AlgorithmicStrategy.TWAP,
            time_windows=10,  # Pediu 10 janelas, mas só tem 3 lotes
            lot_size=100,
            jitter_range_pct=None,
        )
        plan = self.engine.generate_twap_slices(parent, Decimal("30.00"))
        self.assertEqual(len(plan.child_orders), 3)
        self.assertEqual(plan.total_sliced_quantity, 300)
        self.assertEqual(plan.residual_unallocated_quantity, 0)
        inv = plan.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["all_invariants_hold"])

    def test_vwap_custom_profile(self) -> None:
        """Valida VWAP com perfil histórico customizado de 4 períodos."""
        custom_weights = [Decimal("0.40"), Decimal("0.20"), Decimal("0.10"), Decimal("0.30")]
        parent = ParentOrder(
            parent_id="VWAP-CUSTOM",
            symbol="VALE3",
            side=OrderSide.BUY,
            quantity=10_000,
            arrival_price=Decimal("60.00"),
            strategy=AlgorithmicStrategy.VWAP,
            time_windows=4,
            lot_size=100,
        )
        plan = self.engine.generate_vwap_slices(
            parent,
            reference_price=Decimal("60.00"),
            custom_volume_profile=custom_weights,
        )
        self.assertEqual(len(plan.child_orders), 4)
        self.assertEqual(plan.child_orders[0].quantity, 4_000)
        self.assertEqual(plan.child_orders[1].quantity, 2_000)
        self.assertEqual(plan.child_orders[2].quantity, 1_000)
        self.assertEqual(plan.child_orders[3].quantity, 3_000)
        self.assertEqual(plan.total_sliced_quantity, 10_000)
        inv = plan.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["all_invariants_hold"])

    def test_vwap_zero_lots_adjustment(self) -> None:
        """Garante que nenhuma fatia VWAP fique com zero lotes mesmo quando o peso for ínfimo."""
        # 10 janelas, 1000 ações = 10 lotes de 100. Alguns pesos são quase zero.
        skewed_weights = [Decimal("0.91")] + [Decimal("0.01")] * 9
        parent = ParentOrder(
            parent_id="VWAP-SKEWED",
            symbol="VALE3",
            side=OrderSide.BUY,
            quantity=1_000,
            arrival_price=Decimal("60.00"),
            strategy=AlgorithmicStrategy.VWAP,
            time_windows=10,
            lot_size=100,
        )
        plan = self.engine.generate_vwap_slices(
            parent,
            reference_price=Decimal("60.00"),
            custom_volume_profile=skewed_weights,
        )
        # Todas as 10 fatias devem ter pelo menos 1 lote de 100 (estritamente > 0)
        self.assertEqual(len(plan.child_orders), 10)
        for child in plan.child_orders:
            self.assertGreaterEqual(child.quantity, 100)
        self.assertEqual(plan.total_sliced_quantity, 1_000)
        inv = plan.verify_invariants(raise_on_error=True)
        self.assertTrue(inv["all_invariants_hold"])

    def test_pov_missing_participation_rate(self) -> None:
        """Garante que POV sem taxa de participação gera erro AlgorithmicSORError."""
        parent = ParentOrder(
            parent_id="POV-NO-RATE",
            symbol="BBAS3",
            side=OrderSide.BUY,
            quantity=10_000,
            arrival_price=Decimal("25.00"),
            strategy=AlgorithmicStrategy.POV,
            pov_participation_rate=None,
        )
        with self.assertRaises(AlgorithmicSORError):
            self.engine.generate_pov_slices(parent, Decimal("25.00"), [10_000, 10_000])

    def test_verify_invariants_without_raising(self) -> None:
        """Garante que verify_invariants(raise_on_error=False) retorna dicionário falso sem quebrar."""
        parent = ParentOrder(
            parent_id="INVAR-FALSE",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=10_000,
            arrival_price=Decimal("30.00"),
            strategy=AlgorithmicStrategy.TWAP,
            time_windows=2,
            jitter_range_pct=None,
        )
        plan = self.engine.generate_twap_slices(parent, Decimal("30.00"))
        # Corrompe quantidade
        plan.child_orders[0].quantity += 500
        inv = plan.verify_invariants(raise_on_error=False)
        self.assertFalse(inv["all_invariants_hold"])
        self.assertFalse(inv["law_110_quantity_conservation"])

    def test_enum_and_helper_conversions(self) -> None:
        """Valida conversores auxiliares de enums e normalizações."""
        self.assertEqual(OrderSide.from_str("BUY"), OrderSide.BUY)
        self.assertEqual(OrderSide.from_str("compra"), OrderSide.BUY)
        self.assertEqual(OrderSide.from_str("1"), OrderSide.BUY)
        self.assertEqual(OrderSide.from_str("SELL"), OrderSide.SELL)
        self.assertEqual(OrderSide.from_str("venda"), OrderSide.SELL)
        self.assertEqual(OrderSide.from_str("2"), OrderSide.SELL)

        with self.assertRaises(ValueError):
            OrderSide.from_str("INVALID")

        self.assertEqual(AlgorithmicStrategy.from_str("twap"), AlgorithmicStrategy.TWAP)
        self.assertEqual(AlgorithmicStrategy.from_str("vwap"), AlgorithmicStrategy.VWAP)
        self.assertEqual(AlgorithmicStrategy.from_str("pov"), AlgorithmicStrategy.POV)

        with self.assertRaises(ValueError):
            AlgorithmicStrategy.from_str("UNKNOWN")

        self.assertEqual(round_price(Decimal("34.5678"), Decimal("0.01")), Decimal("34.57"))
        self.assertEqual(round_price(Decimal("34.50"), Decimal("0.05")), Decimal("34.50"))


if __name__ == "__main__":
    unittest.main()
