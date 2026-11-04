"""Suíte de Testes Automatizados para Governança de Provedores de DMA e Filtros Pré-Trade B3 (FL-045).

Testa:
- Governança dos 4 modelos regulados de DMA: DMA 1, DMA 2, DMA 3 e DMA 4.
- Aprovação de ofertas em conformidade com as regras da Resolução CVM 35 e B3.
- Rejeição por violação de Limite de Quantidade Máxima por Ordem (MAX_ORDER_QUANTITY).
- Rejeição por violação de Limite de Valor Financeiro Máximo por Ordem (MAX_ORDER_VALUE).
- Rejeição por rompimento do Túnel de Preço Rejeição (PRICE_TUNNEL_EXCEEDED).
- Rejeição por Venda a Descoberto Desautorizada (UNCOVERED_POSITION_EXCEEDED).
- Rejeição por divergência de Modelo de DMA Homologado (DMA_MODEL_MISMATCH).
- Trava de Throttling e controle de taxa de mensagens por janela deslizante de 1,0s.
- Mecanismo mandatório de Kill-Switch: cancelamento massivo e bloqueio instantâneo (< 100 ms).
- Restabelecimento do canal DMA pós-auditoria de Kill-Switch.
- Ciclo de vida de ordens ativas: preenchimentos parciais/totais (fills) e cancelamentos.
- Validação de sanidade paramétrica e integridade cadastral de clientes.
- Carga completa de cenários e perfis a partir da fixture JSON oficial.
- Métricas e verificação de latência ultrabaixa de avaliação em microssegundos (< 1 ms).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import time
import unittest

# Garante que a raiz do repositório esteja no PYTHONPATH em qualquer ambiente
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.dma_governance import (
    DmaClientConfig,
    DmaGovernanceEngine,
    DmaModelType,
    DmaOrderRequest,
    DmaRejectionReason,
    DmaRiskEvaluationResult,
    KillSwitchExecution,
    OrderSide,
)

FIXTURES_DIR = ROOT_DIR / "fixtures"
DMA_FIXTURE_PATH = FIXTURES_DIR / "dma_governance_orders.json"


class TestDmaGovernance(unittest.TestCase):
    """Bateria de testes unitários do motor de governança DMA e filtros pré-trade B3."""

    def setUp(self) -> None:
        """Inicializa o motor e carrega o arquivo de fixtures de teste."""
        self.engine = DmaGovernanceEngine()
        self.assertTrue(DMA_FIXTURE_PATH.exists(), f"Fixture não encontrada: {DMA_FIXTURE_PATH}")
        self.engine.load_from_json_file(DMA_FIXTURE_PATH)

        with DMA_FIXTURE_PATH.open("r", encoding="utf-8") as f:
            self.fixture_data = json.load(f)

    # --------------------------------------------------------------------------
    # 1. Testes de Carga de Fixtures e Cadastros
    # --------------------------------------------------------------------------

    def test_fixture_loading_and_client_profiles(self) -> None:
        """Valida se todos os clientes e preços de referência foram carregados com sucesso."""
        # Preços de referência
        self.assertEqual(self.engine.get_reference_price("PETR4"), Decimal("38.00"))
        self.assertEqual(self.engine.get_reference_price("VALE3"), Decimal("62.50"))
        self.assertEqual(self.engine.get_reference_price("WINZ26"), Decimal("130000.00"))

        # Clientes dos 4 modelos
        dma1_cli = self.engine.get_client("CLI_DMA1_RETAIL")
        self.assertIsNotNone(dma1_cli)
        self.assertEqual(dma1_cli.dma_model, DmaModelType.DMA_1)
        self.assertEqual(dma1_cli.max_order_value, Decimal("100000.00"))
        self.assertEqual(dma1_cli.max_order_qty, 2000)
        self.assertEqual(dma1_cli.custody.get("PETR4"), 1000)

        dma2_cli = self.engine.get_client("CLI_DMA2_ALGO")
        self.assertIsNotNone(dma2_cli)
        self.assertEqual(dma2_cli.dma_model, DmaModelType.DMA_2)

        dma3_cli = self.engine.get_client("CLI_DMA3_INST")
        self.assertIsNotNone(dma3_cli)
        self.assertEqual(dma3_cli.dma_model, DmaModelType.DMA_3)

        dma4_cli = self.engine.get_client("CLI_DMA4_HFT")
        self.assertIsNotNone(dma4_cli)
        self.assertEqual(dma4_cli.dma_model, DmaModelType.DMA_4)

    # --------------------------------------------------------------------------
    # 2. Testes dos 4 Modelos de DMA: Aprovações Regulares
    # --------------------------------------------------------------------------

    def test_approved_orders_for_all_dma_models(self) -> None:
        """Verifica aprovação de ordens válidas nos modelos DMA 1, DMA 2, DMA 3 e DMA 4."""
        approved_scenarios = self.fixture_data["test_scenarios"]["approved_orders"]
        self.assertEqual(len(approved_scenarios), 4)

        for scenario in approved_scenarios:
            req_data = scenario["request"]
            order_req = DmaOrderRequest.from_dict(req_data)
            result = self.engine.evaluate_order(order_req)

            self.assertTrue(result.approved, f"Falha na ordem aprovada {order_req.order_id}: {result.message}")
            self.assertEqual(result.reason_code, DmaRejectionReason.APPROVED)
            self.assertEqual(result.dma_model.value, req_data["dma_model"])
            self.assertGreater(result.evaluation_latency_us, 0.0)

            # Verifica presença no livro de ordens ativas
            active_orders = self.engine.get_active_orders(order_req.client_id)
            self.assertTrue(any(o.order_id == order_req.order_id for o in active_orders))

    # --------------------------------------------------------------------------
    # 3. Teste de Divergência de Modelo de DMA
    # --------------------------------------------------------------------------

    def test_dma_model_mismatch_rejection(self) -> None:
        """Ordem declarando modelo DMA divergente da homologação do cliente deve ser rejeitada."""
        mismatch_req = DmaOrderRequest(
            order_id="ORD-MISMATCH-001",
            client_id="CLI_DMA1_RETAIL",  # Cliente homologado para DMA_1
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=100,
            price=Decimal("38.00"),
            timestamp=datetime.now(timezone.utc),
            dma_model=DmaModelType.DMA_3,  # Tentativa indevida de usar canal DMA_3
        )

        result = self.engine.evaluate_order(mismatch_req)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason_code, DmaRejectionReason.REJECTED_DMA_MODEL_MISMATCH)
        self.assertIn("está homologado exclusivamente para DMA_1", result.message)

    # --------------------------------------------------------------------------
    # 4. Testes de Filtros Pré-Trade: Quantidade e Valor Financeiro Máximo
    # --------------------------------------------------------------------------

    def test_max_order_quantity_rejection(self) -> None:
        """Verifica rejeição quando quantidade excede o teto contratual."""
        scenarios = self.fixture_data["test_scenarios"]["rejected_max_quantity"]
        for scn in scenarios:
            req = DmaOrderRequest.from_dict(scn["request"])
            res = self.engine.evaluate_order(req)

            self.assertFalse(res.approved)
            self.assertEqual(res.reason_code, DmaRejectionReason.REJECTED_MAX_ORDER_QUANTITY_EXCEEDED)
            self.assertIn("excede o teto contratual", res.message)

    def test_max_order_value_rejection(self) -> None:
        """Verifica rejeição quando valor financeiro excede o limite (Fat-Finger)."""
        scenarios = self.fixture_data["test_scenarios"]["rejected_max_value"]
        for scn in scenarios:
            req = DmaOrderRequest.from_dict(scn["request"])
            res = self.engine.evaluate_order(req)

            self.assertFalse(res.approved)
            self.assertEqual(res.reason_code, DmaRejectionReason.REJECTED_MAX_ORDER_VALUE_EXCEEDED)
            self.assertIn("excede o limite máximo parametrizado", res.message)

    # --------------------------------------------------------------------------
    # 5. Testes de Filtros Pré-Trade: Túnel de Preço Rejeição
    # --------------------------------------------------------------------------

    def test_price_tunnel_rejection(self) -> None:
        """Verifica rejeição de ofertas com preço divergente do túnel de rejeição B3."""
        scenarios = self.fixture_data["test_scenarios"]["rejected_price_tunnel"]
        for scn in scenarios:
            req = DmaOrderRequest.from_dict(scn["request"])
            res = self.engine.evaluate_order(req)

            self.assertFalse(res.approved)
            self.assertEqual(res.reason_code, DmaRejectionReason.REJECTED_PRICE_TUNNEL_EXCEEDED)
            self.assertIn("superando o túnel de rejeição", res.message)

    def test_price_tunnel_reference_price_missing(self) -> None:
        """Ativo sem preço de referência cadastrado deve ser rejeitado preventivamente."""
        req = DmaOrderRequest(
            order_id="ORD-UNKNOWN-SYM-001",
            client_id="CLI_DMA1_RETAIL",
            symbol="UNKNOWN3",
            side=OrderSide.BUY,
            quantity=100,
            price=Decimal("10.00"),
            timestamp=datetime.now(timezone.utc),
        )
        res = self.engine.evaluate_order(req)
        self.assertFalse(res.approved)
        self.assertEqual(res.reason_code, DmaRejectionReason.REJECTED_REFERENCE_PRICE_UNAVAILABLE)

    # --------------------------------------------------------------------------
    # 6. Testes de Filtros Pré-Trade: Controle de Venda a Descoberto
    # --------------------------------------------------------------------------

    def test_uncovered_short_position_rejection(self) -> None:
        """Venda descoberta além do limite contratual do cliente deve ser rejeitada."""
        scenarios = self.fixture_data["test_scenarios"]["rejected_uncovered_position"]
        for scn in scenarios:
            req = DmaOrderRequest.from_dict(scn["request"])
            res = self.engine.evaluate_order(req)

            self.assertFalse(res.approved)
            self.assertEqual(res.reason_code, DmaRejectionReason.REJECTED_UNCOVERED_POSITION_EXCEEDED)
            self.assertIn("Venda a descoberto projetada", res.message)

    def test_uncovered_short_position_with_accumulated_active_orders(self) -> None:
        """Vendas parciais ativas se acumulam e devem acionar a trava de venda a descoberto."""
        client_id = "CLI_DMA2_ALGO"
        # Custódia de PETR4 é 10.000, limite a descoberto é 5.000 (total máximo de vendas: 15.000)
        
        # 1. Primeira venda de 8.000 (coberta por custódia 10.000)
        ord1 = DmaOrderRequest(
            order_id="ORD-ACCUM-001",
            client_id=client_id,
            symbol="PETR4",
            side=OrderSide.SELL,
            quantity=8000,
            price=Decimal("38.00"),
            timestamp=datetime.now(timezone.utc),
            dma_model=DmaModelType.DMA_2,
        )
        res1 = self.engine.evaluate_order(ord1)
        self.assertTrue(res1.approved)

        # 2. Segunda venda de 5.000 (total vendas 13.000 -> descoberta = 3.000 <= limite 5.000)
        ord2 = DmaOrderRequest(
            order_id="ORD-ACCUM-002",
            client_id=client_id,
            symbol="PETR4",
            side=OrderSide.SELL,
            quantity=5000,
            price=Decimal("38.00"),
            timestamp=datetime.now(timezone.utc),
            dma_model=DmaModelType.DMA_2,
        )
        res2 = self.engine.evaluate_order(ord2)
        self.assertTrue(res2.approved)

        # 3. Terceira venda de 3.000 (total vendas projetadas 16.000 -> descoberta = 6.000 > limite 5.000)
        ord3 = DmaOrderRequest(
            order_id="ORD-ACCUM-003",
            client_id=client_id,
            symbol="PETR4",
            side=OrderSide.SELL,
            quantity=3000,
            price=Decimal("38.00"),
            timestamp=datetime.now(timezone.utc),
            dma_model=DmaModelType.DMA_2,
        )
        res3 = self.engine.evaluate_order(ord3)
        self.assertFalse(res3.approved)
        self.assertEqual(res3.reason_code, DmaRejectionReason.REJECTED_UNCOVERED_POSITION_EXCEEDED)

    # --------------------------------------------------------------------------
    # 7. Teste de Throttling e Janela Deslizante de Mensagens
    # --------------------------------------------------------------------------

    def test_message_throttling_sliding_window(self) -> None:
        """Verifica controle de taxa máxima de mensagens em janela de 1 segundo."""
        scenarios = self.fixture_data["test_scenarios"]["throttling_burst"]
        self.assertEqual(len(scenarios), 6)

        # As 5 primeiras devem ser aprovadas (dentro do teto de 5 msg/s)
        for i in range(5):
            req = DmaOrderRequest.from_dict(scenarios[i]["request"])
            res = self.engine.evaluate_order(req)
            self.assertTrue(res.approved, f"Ordem {i+1} deveria ser aprovada: {res.message}")

        # A 6ª ordem na mesma janela temporal de 100ms estoura o limite
        rej_req = DmaOrderRequest.from_dict(scenarios[5]["request"])
        rej_res = self.engine.evaluate_order(rej_req)
        self.assertFalse(rej_res.approved)
        self.assertEqual(rej_res.reason_code, DmaRejectionReason.REJECTED_THROTTLING_LIMIT_EXCEEDED)
        self.assertIn("Taxa de mensagens excedida", rej_res.message)

        # Após 1,5 segundo, o cliente volta a conseguir enviar ordens
        post_window_req = DmaOrderRequest(
            order_id="ORD-THROT-POST-1S",
            client_id="CLI_DMA3_KILLSWITCH_TARGET",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=100,
            price=Decimal("38.00"),
            timestamp=datetime.fromisoformat("2026-11-03T10:10:02.000000+00:00"),
            dma_model=DmaModelType.DMA_3,
        )
        post_res = self.engine.evaluate_order(post_window_req)
        self.assertTrue(post_res.approved, f"Ordem após avanço da janela deveria passar: {post_res.message}")

    # --------------------------------------------------------------------------
    # 8. Teste de Kill-Switch Mandatório (< 100 ms) e Desarme de Canal
    # --------------------------------------------------------------------------

    def test_kill_switch_execution_and_channel_blocking(self) -> None:
        """Valida cancelamento em massa em menos de 100ms e bloqueio total do canal."""
        ks_scenarios = self.fixture_data["test_scenarios"]["kill_switch_orders"]
        client_id = "CLI_DMA3_KILLSWITCH_TARGET"

        # 1. Envia duas ordens ativas
        ord1 = DmaOrderRequest.from_dict(ks_scenarios[0]["request"])
        ord2 = DmaOrderRequest.from_dict(ks_scenarios[1]["request"])
        res1 = self.engine.evaluate_order(ord1)
        res2 = self.engine.evaluate_order(ord2)
        self.assertTrue(res1.approved)
        self.assertTrue(res2.approved)

        active_orders = self.engine.get_active_orders(client_id)
        self.assertEqual(len(active_orders), 2)

        # 2. Aciona Kill-Switch emergencial
        t_start = time.perf_counter()
        execution = self.engine.trigger_kill_switch(
            client_id=client_id,
            reason="Anomalia crítica de envio detectada pelo monitor de risco",
            executed_by="RISK_OFFICER_HEAD",
        )
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        # Verificações obrigatórias da CVM 35 e B3
        self.assertLess(elapsed_ms, 100.0, "Disparo de Kill-Switch DEVE ocorrer em menos de 100ms!")
        self.assertLess(execution.latency_ms, 100.0)
        self.assertTrue(execution.channel_blocked)
        self.assertEqual(execution.canceled_orders_count, 2)
        self.assertIn(ord1.order_id, execution.canceled_order_ids)
        self.assertIn(ord2.order_id, execution.canceled_order_ids)

        # Confirma que o livro de ordens ativas do cliente foi completamente zerado
        self.assertEqual(len(self.engine.get_active_orders(client_id)), 0)

        # 3. Qualquer nova ordem submetida para este cliente deve ser IMEDIATAMENTE rejeitada
        post_ks_req = DmaOrderRequest.from_dict(ks_scenarios[2]["request"])
        post_ks_res = self.engine.evaluate_order(post_ks_req)
        self.assertFalse(post_ks_res.approved)
        self.assertEqual(post_ks_res.reason_code, DmaRejectionReason.REJECTED_KILL_SWITCH_ACTIVE)
        self.assertIn("Canal DMA bloqueado por acionamento emergencial", post_ks_res.message)

        # 4. Restabelecimento do canal pelo Compliance (Reset)
        reset_ok = self.engine.reset_kill_switch(client_id, reset_by="CHIEF_COMPLIANCE_OFFICER")
        self.assertTrue(reset_ok)

        # Nova ordem agora é aceita normalmente
        recovery_req = DmaOrderRequest(
            order_id="ORD-RECOVERY-001",
            client_id=client_id,
            symbol="VALE3",
            side=OrderSide.BUY,
            quantity=100,
            price=Decimal("62.50"),
            timestamp=datetime.fromisoformat("2026-11-03T10:15:10.000000+00:00"),
            dma_model=DmaModelType.DMA_3,
        )
        rec_res = self.engine.evaluate_order(recovery_req)
        self.assertTrue(rec_res.approved)

    # --------------------------------------------------------------------------
    # 9. Testes de Ciclo de Vida: Execuções Parciais/Totais (Fills) e Cancelamento
    # --------------------------------------------------------------------------

    def test_order_lifecycle_fill_and_cancel(self) -> None:
        """Verifica impacto de fills e cancelamento manual na custódia e no livro ativo."""
        client_id = "CLI_DMA1_RETAIL"
        initial_custody = self.engine.get_client_custody(client_id, "PETR4")
        self.assertEqual(initial_custody, 1000)

        # Cria e aprova ordem de compra de 400 PETR4 @ 38.00
        buy_req = DmaOrderRequest(
            order_id="ORD-FILL-BUY-001",
            client_id=client_id,
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=400,
            price=Decimal("38.00"),
            timestamp=datetime.now(timezone.utc),
            dma_model=DmaModelType.DMA_1,
        )
        res = self.engine.evaluate_order(buy_req)
        self.assertTrue(res.approved)

        # Fill parcial de 150 ações
        fill_partial = self.engine.register_fill(client_id, "ORD-FILL-BUY-001", filled_qty=150)
        self.assertTrue(fill_partial)
        # Custódia deve ter subido de 1000 para 1150
        self.assertEqual(self.engine.get_client_custody(client_id, "PETR4"), 1150)
        # Quantidade residual no livro deve ser 250
        active = self.engine.get_active_orders(client_id)
        self.assertEqual(active[0].quantity, 250)

        # Fill total dos 250 restantes
        fill_full = self.engine.register_fill(client_id, "ORD-FILL-BUY-001", filled_qty=250)
        self.assertTrue(fill_full)
        self.assertEqual(self.engine.get_client_custody(client_id, "PETR4"), 1400)
        # Ordem removida das ativas
        self.assertEqual(len(self.engine.get_active_orders(client_id)), 0)

        # Teste de cancelamento manual
        ord_to_cancel = DmaOrderRequest(
            order_id="ORD-CANCEL-001",
            client_id=client_id,
            symbol="VALE3",
            side=OrderSide.BUY,
            quantity=100,
            price=Decimal("62.50"),
            timestamp=datetime.now(timezone.utc),
            dma_model=DmaModelType.DMA_1,
        )
        self.assertTrue(self.engine.evaluate_order(ord_to_cancel).approved)
        self.assertEqual(len(self.engine.get_active_orders(client_id)), 1)
        canceled = self.engine.cancel_order(client_id, "ORD-CANCEL-001")
        self.assertTrue(canceled)
        self.assertEqual(len(self.engine.get_active_orders(client_id)), 0)

    # --------------------------------------------------------------------------
    # 10. Teste de Sanidade de Parâmetros e Integridade Cadastral
    # --------------------------------------------------------------------------

    def test_parameter_sanity_and_unknown_client(self) -> None:
        """Verifica rejeição preventiva para inputs inválidos ou clientes não cadastrados."""
        # Cliente inexistente
        unreg_req = DmaOrderRequest(
            order_id="ORD-ERR-001",
            client_id="CLI_INEXISTENTE",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=100,
            price=Decimal("38.00"),
            timestamp=datetime.now(timezone.utc),
        )
        res_unreg = self.engine.evaluate_order(unreg_req)
        self.assertFalse(res_unreg.approved)
        self.assertEqual(res_unreg.reason_code, DmaRejectionReason.REJECTED_CLIENT_NOT_FOUND)

        # Quantidade zero ou negativa
        bad_qty = DmaOrderRequest(
            order_id="ORD-ERR-002",
            client_id="CLI_DMA1_RETAIL",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=0,
            price=Decimal("38.00"),
            timestamp=datetime.now(timezone.utc),
        )
        res_qty = self.engine.evaluate_order(bad_qty)
        self.assertFalse(res_qty.approved)
        self.assertEqual(res_qty.reason_code, DmaRejectionReason.REJECTED_INVALID_ORDER_PARAMETERS)

        # Preço negativo ou zero
        bad_price = DmaOrderRequest(
            order_id="ORD-ERR-003",
            client_id="CLI_DMA1_RETAIL",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=100,
            price=Decimal("-10.00"),
            timestamp=datetime.now(timezone.utc),
        )
        res_price = self.engine.evaluate_order(bad_price)
        self.assertFalse(res_price.approved)
        self.assertEqual(res_price.reason_code, DmaRejectionReason.REJECTED_INVALID_ORDER_PARAMETERS)

        # Lado da ordem inválido
        bad_side = DmaOrderRequest(
            order_id="ORD-ERR-004",
            client_id="CLI_DMA1_RETAIL",
            symbol="PETR4",
            side="INVALID_SIDE",
            quantity=100,
            price=Decimal("38.00"),
            timestamp=datetime.now(timezone.utc),
        )
        res_side = self.engine.evaluate_order(bad_side)
        self.assertFalse(res_side.approved)
        self.assertEqual(res_side.reason_code, DmaRejectionReason.REJECTED_INVALID_ORDER_PARAMETERS)

        # Cliente inativo
        client = self.engine.get_client("CLI_DMA1_RETAIL")
        self.assertIsNotNone(client)
        client.is_active = False
        inactive_res = self.engine.evaluate_order(
            DmaOrderRequest(
                order_id="ORD-ERR-005",
                client_id="CLI_DMA1_RETAIL",
                symbol="PETR4",
                side=OrderSide.BUY,
                quantity=100,
                price=Decimal("38.00"),
                timestamp=datetime.now(timezone.utc),
            )
        )
        self.assertFalse(inactive_res.approved)
        self.assertEqual(inactive_res.reason_code, DmaRejectionReason.REJECTED_CLIENT_INACTIVE)
        client.is_active = True

    def test_serialization_and_dataclass_methods(self) -> None:
        """Verifica serialização to_dict e desserialização from_dict de todos os modelos."""
        client = self.engine.get_client("CLI_DMA4_HFT")
        self.assertIsNotNone(client)
        d_client = client.to_dict()
        self.assertEqual(d_client["client_id"], "CLI_DMA4_HFT")
        self.assertEqual(d_client["dma_model"], "DMA_4")
        rebuilt_client = DmaClientConfig.from_dict(d_client)
        self.assertEqual(rebuilt_client.max_order_qty, client.max_order_qty)

        order = DmaOrderRequest(
            order_id="TEST-SER-001",
            client_id="CLI_DMA4_HFT",
            symbol="VALE3",
            side=OrderSide.SELL,
            quantity=500,
            price=Decimal("62.50"),
            timestamp=datetime.now(timezone.utc),
            dma_model=DmaModelType.DMA_4,
            account="40004",
        )
        d_order = order.to_dict()
        self.assertEqual(d_order["side"], "SELL")
        self.assertEqual(d_order["order_value"], "31250.00")
        rebuilt_order = DmaOrderRequest.from_dict(d_order)
        self.assertEqual(rebuilt_order.order_id, order.order_id)
        self.assertEqual(rebuilt_order.order_value, order.order_value)

        # Kill-switch em cliente inexistente levanta KeyError
        with self.assertRaises(KeyError):
            self.engine.trigger_kill_switch("CLI_NON_EXISTENT", "Teste")

    # --------------------------------------------------------------------------
    # 11. Teste de Desempenho e Latência Ultrabaixa (< 1.000 µs)
    # --------------------------------------------------------------------------

    def test_sub_millisecond_evaluation_performance(self) -> None:
        """Mede a latência determinística do gateway de risco pré-trade em memória (< 1.000 µs)."""
        client_id = "CLI_DMA4_HFT"
        iterations = 1000
        latencies_us = []

        base_dt = datetime.now(timezone.utc)
        for i in range(iterations):
            req = DmaOrderRequest(
                order_id=f"PERF-ORD-{i:05d}",
                client_id=client_id,
                symbol="PETR4",
                side=OrderSide.BUY,
                quantity=100,
                price=Decimal("38.05"),
                timestamp=base_dt + timedelta(seconds=i * 2),  # Avança tempo para não acionar throttling
                dma_model=DmaModelType.DMA_4,
            )
            res = self.engine.evaluate_order(req)
            self.assertTrue(res.approved)
            latencies_us.append(res.evaluation_latency_us)

        avg_latency = sum(latencies_us) / len(latencies_us)
        max_latency = max(latencies_us)

        # Garantia de execução ultra rápida: média bem abaixo de 1 milissegundo (1000 µs)
        self.assertLess(avg_latency, 500.0, f"Latência média de {avg_latency:.2f} µs excedeu o teto esperado.")
        self.assertLess(max_latency, 2000.0, f"Pico de latência de {max_latency:.2f} µs excedeu tolerância.")


if __name__ == "__main__":
    unittest.main()
