"""Testes Unitários para o Módulo de Ordens Avançadas do PUMA Trading System B3 (FL-056).

Valida de forma exaustiva:
- Ciclo de vida e renovação de tranches de ordens Iceberg (DisplayQty).
- Disparo determinístico de ordens Stop-Loss (a mercado) e Stop-Limit (limitadas).
- Ajuste dinâmico (Ratchet Algorithm) e congelamento de ordens Trailing-Stop.
- Preservação matemática de leis de conservação de lotes:
  Quantidade_Executada + Quantidade_Oculta_Restante == Quantidade_Total
  Quantidade_Executada + Quantidade_Visivel + Quantidade_Oculta == Quantidade_Total
- Carga e execução integrada da fixture fixtures/puma_advanced_orders.json.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import time
import unittest

from src.puma_advanced_orders import (
    AdvancedOrderSpec,
    AdvancedOrderType,
    BookFillEvent,
    OrderExecutionTranche,
    OrderSide,
    OrderTriggerStatus,
    PumaAdvancedOrdersEngine,
)


class TestPumaAdvancedOrders(unittest.TestCase):
    """Bateria de testes unitários do motor de ordens avançadas PUMA da B3."""

    def setUp(self) -> None:
        self.engine = PumaAdvancedOrdersEngine()
        self.now = datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc)
        self.fixture_path = (
            Path(__file__).parent.parent / "fixtures" / "puma_advanced_orders.json"
        )

    # --------------------------------------------------------------------------
    # 1. Testes de Enumerações e Modelos de Dados
    # --------------------------------------------------------------------------

    def test_enums_and_parsers(self) -> None:
        """Verifica parsing e valores canônicos dos enums."""
        self.assertEqual(OrderSide.from_str("BUY"), OrderSide.BUY)
        self.assertEqual(OrderSide.from_str("1"), OrderSide.BUY)
        self.assertEqual(OrderSide.from_str("SELL"), OrderSide.SELL)
        self.assertEqual(OrderSide.from_str("2"), OrderSide.SELL)
        with self.assertRaises(ValueError):
            OrderSide.from_str("INVALID")

        self.assertEqual(AdvancedOrderType.from_str("ICEBERG"), AdvancedOrderType.ICEBERG)
        self.assertEqual(AdvancedOrderType.from_str("STOP_LOSS"), AdvancedOrderType.STOP_LOSS)
        self.assertEqual(AdvancedOrderType.from_str("STOP_LIMIT"), AdvancedOrderType.STOP_LIMIT)
        self.assertEqual(AdvancedOrderType.from_str("TRAILING_STOP"), AdvancedOrderType.TRAILING_STOP)
        with self.assertRaises(ValueError):
            AdvancedOrderType.from_str("UNKNOWN")

    def test_dataclass_serialization_roundtrip(self) -> None:
        """Verifica serialização e reconstrução de AdvancedOrderSpec, Tranche e FillEvent."""
        spec = AdvancedOrderSpec(
            order_id="TEST-SPEC-001",
            cl_ord_id="CL-TEST-001",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=AdvancedOrderType.ICEBERG,
            quantity=5000,
            display_qty=1000,
            price=Decimal("38.50"),
            account="12345",
            participant_code=88,
            timestamp=self.now,
        )
        d = spec.to_dict()
        self.assertEqual(d["quantity"], 5000)
        self.assertEqual(d["display_qty"], 1000)
        self.assertEqual(d["price"], "38.50")

        rebuilt = AdvancedOrderSpec.from_dict(d)
        self.assertEqual(rebuilt.order_id, spec.order_id)
        self.assertEqual(rebuilt.price, spec.price)
        self.assertEqual(rebuilt.display_qty, spec.display_qty)
        self.assertEqual(rebuilt.quantity, spec.quantity)

        # Tranche serialization
        tranche = OrderExecutionTranche(
            tranche_id="TR-001",
            order_id="TEST-SPEC-001",
            tranche_index=1,
            displayed_qty=1000,
            filled_qty=500,
            remaining_hidden_qty=4000,
            price=Decimal("38.50"),
            timestamp=self.now,
        )
        td = tranche.to_dict()
        rebuilt_tranche = OrderExecutionTranche.from_dict(td)
        self.assertEqual(rebuilt_tranche.tranche_id, tranche.tranche_id)
        self.assertEqual(rebuilt_tranche.filled_qty, 500)

        # Fill event serialization
        fill = BookFillEvent(
            fill_id="FILL-001",
            order_id="TEST-SPEC-001",
            symbol="PETR4",
            side=OrderSide.BUY,
            fill_qty=500,
            fill_price=Decimal("38.50"),
            timestamp=self.now,
            remaining_visible=500,
            remaining_hidden=4000,
            cum_qty=500,
            tranche_index=1,
        )
        fd = fill.to_dict()
        rebuilt_fill = BookFillEvent.from_dict(fd)
        self.assertEqual(rebuilt_fill.fill_id, fill.fill_id)
        self.assertEqual(rebuilt_fill.cum_qty, 500)

    # --------------------------------------------------------------------------
    # 2. Ordem Iceberg: Ciclo de Vida, Tranches e Perda de Prioridade Temporal
    # --------------------------------------------------------------------------

    def test_iceberg_order_setup_and_tranche_refresh(self) -> None:
        """Testa o desdobramento de tranches, refresh e atualização de prioridade temporal."""
        spec = AdvancedOrderSpec(
            order_id="ICE-PETR4-10K",
            cl_ord_id="CL-ICE-001",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=AdvancedOrderType.ICEBERG,
            quantity=10000,
            display_qty=2000,
            price=Decimal("38.00"),
            account="10001",
            participant_code=88,
            timestamp=self.now,
        )
        self.engine.register_order(spec)

        order = self.engine.get_order("ICE-PETR4-10K")
        self.assertEqual(order.visible_qty, 2000)
        self.assertEqual(order.hidden_qty, 8000)
        self.assertEqual(order.active_tranche_index, 1)
        self.assertEqual(len(order.tranches), 1)
        self.assertTrue(self.engine.verify_order_conservation("ICE-PETR4-10K"))

        # Execução parcial da primeira tranche (1000 de 2000)
        t1 = self.now + timedelta(seconds=10)
        fill1 = self.engine.process_fill("ICE-PETR4-10K", fill_qty=1000, fill_price=Decimal("38.00"), timestamp=t1)
        self.assertEqual(fill1.cum_qty, 1000)
        self.assertEqual(order.visible_qty, 1000)
        self.assertEqual(order.hidden_qty, 8000)
        self.assertEqual(order.executed_qty, 1000)
        self.assertEqual(order.leaves_qty, 9000)
        self.assertEqual(order.trigger_status, OrderTriggerStatus.PARTIALLY_FILLED)
        self.assertTrue(self.engine.verify_order_conservation("ICE-PETR4-10K"))

        # Esgotamento da primeira tranche (mais 1000) -> Gatilho automático de Refresh
        t2 = self.now + timedelta(seconds=20)
        fill2 = self.engine.process_fill("ICE-PETR4-10K", fill_qty=1000, fill_price=Decimal("38.00"), timestamp=t2)
        self.assertEqual(fill2.cum_qty, 2000)

        # Tranche 1 concluída, Tranche 2 criada com NOVO timestamp t2 (perda de prioridade temporal)
        self.assertEqual(len(order.tranches), 2)
        self.assertEqual(order.active_tranche_index, 2)
        self.assertTrue(order.tranches[0].is_completed)
        self.assertFalse(order.tranches[0].is_active)
        self.assertTrue(order.tranches[1].is_active)
        self.assertEqual(order.tranches[1].displayed_qty, 2000)
        self.assertEqual(order.tranches[1].timestamp, t2)
        self.assertEqual(order.time_priority_timestamp, t2)

        # Visível recarregado para 2000, Oculto restante reduzido para 6000
        self.assertEqual(order.visible_qty, 2000)
        self.assertEqual(order.hidden_qty, 6000)
        self.assertTrue(self.engine.verify_order_conservation("ICE-PETR4-10K"))

    def test_iceberg_full_exhaustion_with_remainder(self) -> None:
        """Testa esgotamento completo de Iceberg com tranche final irregular (resto < DisplayQty)."""
        # 5.500 ações com tranche de 2.000 -> Tranches: 2.000, 2.000, 1.500
        spec = AdvancedOrderSpec(
            order_id="ICE-PETR4-IRREGULAR",
            cl_ord_id="CL-ICE-002",
            symbol="PETR4",
            side=OrderSide.SELL,
            order_type=AdvancedOrderType.ICEBERG,
            quantity=5500,
            display_qty=2000,
            price=Decimal("38.50"),
            timestamp=self.now,
        )
        self.engine.register_order(spec)
        order = self.engine.get_order("ICE-PETR4-IRREGULAR")

        # Tranche 1: 2.000
        self.assertEqual(order.visible_qty, 2000)
        self.assertEqual(order.hidden_qty, 3500)
        self.engine.execute_iceberg_tranche("ICE-PETR4-IRREGULAR")

        # Tranche 2: 2.000
        self.assertEqual(order.visible_qty, 2000)
        self.assertEqual(order.hidden_qty, 1500)
        self.engine.execute_iceberg_tranche("ICE-PETR4-IRREGULAR")

        # Tranche 3: resto de 1.500 (menor que display_qty 2.000)
        self.assertEqual(order.visible_qty, 1500)
        self.assertEqual(order.hidden_qty, 0)
        self.engine.execute_iceberg_tranche("ICE-PETR4-IRREGULAR")

        # Conclusão total
        self.assertTrue(order.is_complete())
        self.assertEqual(order.executed_qty, 5500)
        self.assertEqual(order.leaves_qty, 0)
        self.assertEqual(order.visible_qty, 0)
        self.assertEqual(order.hidden_qty, 0)
        self.assertEqual(order.trigger_status, OrderTriggerStatus.FILLED)
        self.assertEqual(len(order.tranches), 3)
        self.assertTrue(self.engine.verify_order_conservation("ICE-PETR4-IRREGULAR"))

    def test_iceberg_validation_errors(self) -> None:
        """Valida que parâmetros inválidos de Iceberg são rejeitados com exceção."""
        # DisplayQty maior que total
        with self.assertRaises(ValueError):
            self.engine.register_order(
                AdvancedOrderSpec(
                    order_id="ICE-ERR-1",
                    cl_ord_id="C",
                    symbol="PETR4",
                    side=OrderSide.BUY,
                    order_type=AdvancedOrderType.ICEBERG,
                    quantity=1000,
                    display_qty=2000,
                    price=Decimal("38.00"),
                )
            )

        # Sem preço
        with self.assertRaises(ValueError):
            self.engine.register_order(
                AdvancedOrderSpec(
                    order_id="ICE-ERR-2",
                    cl_ord_id="C",
                    symbol="PETR4",
                    side=OrderSide.BUY,
                    order_type=AdvancedOrderType.ICEBERG,
                    quantity=1000,
                    display_qty=500,
                    price=None,
                )
            )

        # Fill maior que visível
        self.engine.register_order(
            AdvancedOrderSpec(
                order_id="ICE-ERR-3",
                cl_ord_id="C",
                symbol="PETR4",
                side=OrderSide.BUY,
                order_type=AdvancedOrderType.ICEBERG,
                quantity=1000,
                display_qty=500,
                price=Decimal("38.00"),
            )
        )
        with self.assertRaises(ValueError):
            self.engine.process_fill("ICE-ERR-3", fill_qty=600, fill_price=Decimal("38.00"))

    # --------------------------------------------------------------------------
    # 3. Ordem Stop-Loss (Gatilho a Mercado)
    # --------------------------------------------------------------------------

    def test_stop_loss_sell_trigger(self) -> None:
        """Verifica disparo de Stop-Loss de Venda quando cotação atinge ou perfura StopPx para baixo."""
        spec = AdvancedOrderSpec(
            order_id="STP-SELL-001",
            cl_ord_id="CL-STP-001",
            symbol="PETR4",
            side=OrderSide.SELL,
            order_type=AdvancedOrderType.STOP_LOSS,
            quantity=1000,
            stop_px=Decimal("37.50"),
            account="10001",
            timestamp=self.now,
        )
        self.engine.register_order(spec)
        order = self.engine.get_order("STP-SELL-001")
        self.assertEqual(order.trigger_status, OrderTriggerStatus.PENDING_TRIGGER)

        # Tick acima do StopPx não dispara
        triggered = self.engine.process_market_tick({"symbol": "PETR4", "price": "37.80"})
        self.assertEqual(len(triggered), 0)
        self.assertEqual(order.trigger_status, OrderTriggerStatus.PENDING_TRIGGER)

        # Tick exato em 37.50 -> DISPARA!
        triggered = self.engine.process_market_tick({"symbol": "PETR4", "price": "37.50"})
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0].order_id, "STP-SELL-001")
        self.assertEqual(order.trigger_status, OrderTriggerStatus.TRIGGERED)
        self.assertEqual(order.visible_qty, 1000)

        # Ordem ativada no CLOB pode agora ser executada a mercado
        fill = self.engine.execute_order("STP-SELL-001", fill_price=Decimal("37.48"))
        self.assertEqual(fill.cum_qty, 1000)
        self.assertEqual(order.trigger_status, OrderTriggerStatus.FILLED)
        self.assertTrue(self.engine.verify_order_conservation("STP-SELL-001"))

    def test_stop_loss_buy_trigger(self) -> None:
        """Verifica disparo de Stop-Loss de Compra quando cotação sobe >= StopPx."""
        spec = AdvancedOrderSpec(
            order_id="STP-BUY-001",
            cl_ord_id="CL-STP-002",
            symbol="VALE3",
            side=OrderSide.BUY,
            order_type=AdvancedOrderType.STOP_LOSS,
            quantity=500,
            stop_px=Decimal("63.00"),
            account="20002",
            timestamp=self.now,
        )
        self.engine.register_order(spec)
        order = self.engine.get_order("STP-BUY-001")

        # Tick abaixo não dispara
        self.assertEqual(len(self.engine.process_market_tick({"symbol": "VALE3", "price": "62.90"})), 0)

        # Gap de alta direto em 63.20 >= 63.00 -> DISPARA!
        triggered = self.engine.process_market_tick({"symbol": "VALE3", "price": "63.20"})
        self.assertEqual(len(triggered), 1)
        self.assertEqual(order.trigger_status, OrderTriggerStatus.TRIGGERED)

    # --------------------------------------------------------------------------
    # 4. Ordem Stop-Limit (Gatilho com Preço Limite)
    # --------------------------------------------------------------------------

    def test_stop_limit_buy_trigger_and_clob_insertion(self) -> None:
        """Verifica inserção no book central no preço Price após atingir StopPx."""
        spec = AdvancedOrderSpec(
            order_id="STL-BUY-001",
            cl_ord_id="CL-STL-001",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=AdvancedOrderType.STOP_LIMIT,
            quantity=2000,
            stop_px=Decimal("39.00"),
            price=Decimal("39.10"),
            account="30003",
            timestamp=self.now,
        )
        self.engine.register_order(spec)
        order = self.engine.get_order("STL-BUY-001")

        # Cotação abaixo de 39.00 não dispara
        self.assertEqual(len(self.engine.process_market_tick({"symbol": "PETR4", "price": "38.90"})), 0)

        # Cotação atinge 39.05 >= 39.00 -> Dispara e insere compra limitada a 39.10
        triggered = self.engine.process_market_tick({"symbol": "PETR4", "price": "39.05"})
        self.assertEqual(len(triggered), 1)
        self.assertEqual(order.trigger_status, OrderTriggerStatus.TRIGGERED)
        self.assertEqual(order.price, Decimal("39.10"))
        self.assertIn(order, self.engine.clob["PETR4"])

        # Execução contra o CLOB no preço limite
        fill = self.engine.process_fill("STL-BUY-001", fill_qty=2000, fill_price=Decimal("39.10"))
        self.assertEqual(fill.cum_qty, 2000)
        self.assertEqual(order.trigger_status, OrderTriggerStatus.FILLED)
        self.assertTrue(self.engine.verify_order_conservation("STL-BUY-001"))

    def test_stop_limit_missing_price_raises_error(self) -> None:
        """Verifica que Stop-Limit sem Price limite é rejeitado."""
        with self.assertRaises(ValueError):
            self.engine.register_order(
                AdvancedOrderSpec(
                    order_id="STL-ERR",
                    cl_ord_id="C",
                    symbol="PETR4",
                    side=OrderSide.BUY,
                    order_type=AdvancedOrderType.STOP_LIMIT,
                    quantity=100,
                    stop_px=Decimal("39.00"),
                    price=None,  # Inválido para Stop-Limit
                )
            )

    # --------------------------------------------------------------------------
    # 5. Ordem Trailing-Stop: Algoritmo Ratchet e Congelamento
    # --------------------------------------------------------------------------

    def test_trailing_stop_sell_ratchet_and_freeze(self) -> None:
        """Verifica avanço do StopPx em alta e congelamento do gatilho em recuo para Venda."""
        # Stop inicial em 37.50 com distância (trail_amount) de 0.50
        spec = AdvancedOrderSpec(
            order_id="TRS-SELL-001",
            cl_ord_id="CL-TRS-001",
            symbol="PETR4",
            side=OrderSide.SELL,
            order_type=AdvancedOrderType.TRAILING_STOP,
            quantity=3000,
            stop_px=Decimal("37.50"),
            trail_amount=Decimal("0.50"),
            timestamp=self.now,
        )
        self.engine.register_order(spec)
        order = self.engine.get_order("TRS-SELL-001")
        self.assertEqual(order.current_stop_px, Decimal("37.50"))

        # 1. Mercado sobe para 38.40 -> 38.40 - 0.50 = 37.90 > 37.50: Atualiza para 37.90
        self.engine.process_market_tick({"symbol": "PETR4", "price": "38.40"})
        self.assertEqual(order.current_stop_px, Decimal("37.90"))
        self.assertEqual(order.trigger_status, OrderTriggerStatus.PENDING_TRIGGER)

        # 2. Mercado sobe para 39.20 -> 39.20 - 0.50 = 38.70 > 37.90: Atualiza para 38.70
        self.engine.process_market_tick({"symbol": "PETR4", "price": "39.20"})
        self.assertEqual(order.current_stop_px, Decimal("38.70"))

        # 3. Mercado recua para 38.90 -> 38.90 - 0.50 = 38.40 < 38.70: Permanece CONGELADO em 38.70!
        self.engine.process_market_tick({"symbol": "PETR4", "price": "38.90"})
        self.assertEqual(order.current_stop_px, Decimal("38.70"))
        self.assertEqual(order.trigger_status, OrderTriggerStatus.PENDING_TRIGGER)

        # 4. Mercado cai para 38.65 <= 38.70 -> DISPARA!
        triggered = self.engine.process_market_tick({"symbol": "PETR4", "price": "38.65"})
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0].order_id, "TRS-SELL-001")
        self.assertEqual(order.trigger_status, OrderTriggerStatus.TRIGGERED)

    def test_trailing_stop_buy_ratchet_and_freeze(self) -> None:
        """Verifica avanço do StopPx para baixo em queda e congelamento em subida para Compra."""
        # Stop inicial em 38.50 com trail_amount de 0.40
        spec = AdvancedOrderSpec(
            order_id="TRB-BUY-001",
            cl_ord_id="CL-TRB-001",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=AdvancedOrderType.TRAILING_STOP,
            quantity=1500,
            stop_px=Decimal("38.50"),
            trail_amount=Decimal("0.40"),
            timestamp=self.now,
        )
        self.engine.register_order(spec)
        order = self.engine.get_order("TRB-BUY-001")

        # 1. Mercado cai para 37.80 -> 37.80 + 0.40 = 38.20 < 38.50: Atualiza para 38.20
        self.engine.process_market_tick({"symbol": "PETR4", "price": "37.80"})
        self.assertEqual(order.current_stop_px, Decimal("38.20"))

        # 2. Fundo em 37.20 -> 37.20 + 0.40 = 37.60 < 38.20: Atualiza para 37.60
        self.engine.process_market_tick({"symbol": "PETR4", "price": "37.20"})
        self.assertEqual(order.current_stop_px, Decimal("37.60"))

        # 3. Mercado sobe para 37.40 -> StopPx fica CONGELADO em 37.60
        self.engine.process_market_tick({"symbol": "PETR4", "price": "37.40"})
        self.assertEqual(order.current_stop_px, Decimal("37.60"))

        # 4. Rebote atinge 37.65 >= 37.60 -> DISPARA!
        triggered = self.engine.process_market_tick({"symbol": "PETR4", "price": "37.65"})
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0].order_id, "TRB-BUY-001")
        self.assertEqual(order.trigger_status, OrderTriggerStatus.TRIGGERED)

    # --------------------------------------------------------------------------
    # 6. Cancelamento e Invariante de Ordens Canceladas
    # --------------------------------------------------------------------------

    def test_order_cancellation_and_conservation(self) -> None:
        """Verifica cancelamento antes e após preenchimento parcial com conservação de lotes."""
        # Cancelamento antes de fill
        spec = AdvancedOrderSpec(
            order_id="CANC-001",
            cl_ord_id="C",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=AdvancedOrderType.ICEBERG,
            quantity=5000,
            display_qty=1000,
            price=Decimal("38.00"),
        )
        self.engine.register_order(spec)
        self.engine.cancel_order("CANC-001")
        order = self.engine.get_order("CANC-001")
        self.assertEqual(order.trigger_status, OrderTriggerStatus.CANCELLED)
        self.assertEqual(order.cancelled_qty, 5000)
        self.assertEqual(order.visible_qty, 0)
        self.assertEqual(order.hidden_qty, 0)
        self.assertTrue(self.engine.verify_order_conservation("CANC-001"))

        # Cancelamento após preenchimento parcial
        spec2 = AdvancedOrderSpec(
            order_id="CANC-002",
            cl_ord_id="C2",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=AdvancedOrderType.ICEBERG,
            quantity=5000,
            display_qty=1000,
            price=Decimal("38.00"),
        )
        self.engine.register_order(spec2)
        self.engine.process_fill("CANC-002", fill_qty=1000, fill_price=Decimal("38.00"))
        self.engine.cancel_order("CANC-002")
        order2 = self.engine.get_order("CANC-002")
        self.assertEqual(order2.executed_qty, 1000)
        self.assertEqual(order2.cancelled_qty, 4000)
        self.assertEqual(order2.executed_qty + order2.cancelled_qty, 5000)
        self.assertTrue(self.engine.verify_order_conservation("CANC-002"))

    # --------------------------------------------------------------------------
    # 7. Teste Integrado com Fixture Oficial
    # --------------------------------------------------------------------------

    def test_full_fixture_integration(self) -> None:
        """Carrega e executa o cenário completo da fixture puma_advanced_orders.json."""
        self.assertTrue(self.fixture_path.exists(), f"Fixture não encontrada: {self.fixture_path}")

        with open(self.fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.engine.load_from_dict(data)
        self.assertEqual(len(self.engine.orders), 6)

        # Executa a sequência de ticks do mercado
        all_triggered: List[str] = []
        for tick in data["market_ticks"]:
            triggered = self.engine.process_market_tick(tick)
            for t_ord in triggered:
                all_triggered.append(t_ord.order_id)

        # Verifica disparos esperados
        self.assertIn("ORD-STOP-LIMIT-001", all_triggered)
        self.assertIn("ORD-TRAILING-STOP-SELL-001", all_triggered)
        self.assertIn("ORD-STOP-LOSS-001", all_triggered)
        self.assertIn("ORD-TRAILING-STOP-BUY-001", all_triggered)

        # Executa tranches da ordem Iceberg ORD-ICEBERG-001
        self.engine.execute_iceberg_tranche("ORD-ICEBERG-001")
        self.engine.execute_iceberg_tranche("ORD-ICEBERG-001")
        iceberg_ord = self.engine.get_order("ORD-ICEBERG-001")
        self.assertEqual(iceberg_ord.executed_qty, 4000)
        self.assertEqual(iceberg_ord.leaves_qty, 6000)

        # Comprova 100% das leis de conservação em todas as ordens
        self.assertTrue(self.engine.verify_all_invariants())

    # --------------------------------------------------------------------------
    # 8. Casos de Borda e Performance Sub-Milissegundo (< 1.000 µs)
    # --------------------------------------------------------------------------

    def test_iceberg_display_equals_total(self) -> None:
        """Iceberg com DisplayQty igual ao total comporta-se com 1 tranche e zero oculto."""
        spec = AdvancedOrderSpec(
            order_id="ICE-EQUAL-001",
            cl_ord_id="C",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=AdvancedOrderType.ICEBERG,
            quantity=1000,
            display_qty=1000,
            price=Decimal("38.00"),
            timestamp=self.now,
        )
        self.engine.register_order(spec)
        order = self.engine.get_order("ICE-EQUAL-001")
        self.assertEqual(order.visible_qty, 1000)
        self.assertEqual(order.hidden_qty, 0)
        self.assertEqual(len(order.tranches), 1)
        self.assertTrue(self.engine.verify_order_conservation("ICE-EQUAL-001"))

        # Executa integralmente
        self.engine.execute_iceberg_tranche("ICE-EQUAL-001")
        self.assertTrue(order.is_complete())
        self.assertEqual(order.trigger_status, OrderTriggerStatus.FILLED)
        self.assertTrue(self.engine.verify_order_conservation("ICE-EQUAL-001"))

    def test_duplicate_order_id_raises_error(self) -> None:
        """Tentar registrar ordem com ID duplicado deve lançar ValueError."""
        spec = AdvancedOrderSpec(
            order_id="DUP-001",
            cl_ord_id="C1",
            symbol="PETR4",
            side=OrderSide.BUY,
            order_type=AdvancedOrderType.STOP_LOSS,
            quantity=100,
            stop_px=Decimal("37.00"),
        )
        self.engine.register_order(spec)
        with self.assertRaises(ValueError):
            self.engine.register_order(spec)

    def test_sub_millisecond_matching_performance(self) -> None:
        """Avalia performance em memória garantindo execução determinística ultrarrápida (< 500 µs)."""
        iterations = 500
        latencies_us = []

        for i in range(iterations):
            spec = AdvancedOrderSpec(
                order_id=f"PERF-ICE-{i:04d}",
                cl_ord_id=f"CL-{i}",
                symbol="PETR4",
                side=OrderSide.BUY,
                order_type=AdvancedOrderType.ICEBERG,
                quantity=1000,
                display_qty=200,
                price=Decimal("38.00"),
                timestamp=self.now,
            )
            t_start = time.perf_counter()
            self.engine.register_order(spec)
            self.engine.process_fill(f"PERF-ICE-{i:04d}", fill_qty=200, fill_price=Decimal("38.00"))
            t_end = time.perf_counter()
            latencies_us.append((t_end - t_start) * 1_000_000)

        avg_latency = sum(latencies_us) / len(latencies_us)
        self.assertLess(avg_latency, 500.0, f"Latência média {avg_latency:.2f} µs excedeu o teto esperado.")


if __name__ == "__main__":
    unittest.main()
