"""Testes Unitários e de Invariantes Formais para o Futuro de CBIO (CBI) no PUMA e Câmara B3 (FL-086).

Cobre:
- Especificação regulatória do contrato padronizado CBI (Lei nº 13.576/2017, RenovaBio);
- Túneis dinâmicos PUMA (Túnel de Rejeição Hard Limit e Túnel de Leilão de Volatilidade);
- Matching contínuo por prioridade Preço-Tempo (FIFO) e cálculo de PTF em leilão;
- Gestão de risco CORE da Câmara B3, depósitos de garantias e chamadas de margem;
- Apuração determinística de Ajuste Diário Mark-to-Market em D+1 com conservação soma-zero;
- Liquidação final exclusivamente financeira baseada no preço spot de CBIO;
- Invariantes formais de conservação matemática e paridade de Open Interest.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.cbio_futures_puma import (
    AccountMarginProfile,
    CbioContract,
    CbioFuturesEngine,
    CbioFuturesError,
    CbioFuturesValidationError,
    CollateralDeposit,
    CollateralType,
    DailyAdjustmentResult,
    ExpiredContractError,
    FinalCashSettlementResult,
    InvalidTradingPhaseError,
    MarginCall,
    MarginDeficitError,
    Order,
    OrderNotFoundError,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
    PumaCollarViolationError,
    Trade,
    TradingPhase,
)
from src.invariants import InvariantViolationError


class TestCbioFuturesPuma(unittest.TestCase):
    """Bateria de testes completos do motor de futuros ambientais CBIO (CBI) B3."""

    def setUp(self) -> None:
        self.fixture_path = (
            Path(__file__).resolve().parent.parent / "fixtures" / "cbio_futures_puma.json"
        )
        self.engine = CbioFuturesEngine.load_from_fixture(self.fixture_path)

    # -----------------------------------------------------------------------
    # 1. Carregamento de Fixture e Invariantes
    # -----------------------------------------------------------------------

    def test_fixture_loading_and_invariants(self) -> None:
        """Garante que a fixture carregue com integridade e todas as invariantes formais passem."""
        report = self.engine.verify_invariants()
        self.assertTrue(report["all_invariants_hold"])
        self.assertTrue(report["daily_zero_sum_verified"])
        self.assertTrue(report["open_interest_verified"])
        self.assertTrue(report["trades_volume_verified"])
        self.assertTrue(report["collars_verified"])
        self.assertTrue(report["final_settlement_verified"])
        self.assertTrue(report["margin_calls_verified"])

    def test_serialization_roundtrip(self) -> None:
        """Valida que to_dict() e from_dict() preservem fielmente todo o estado operacional."""
        engine_dict = self.engine.to_dict()
        reconstituted = CbioFuturesEngine.from_dict(engine_dict)
        report = reconstituted.verify_invariants()
        self.assertTrue(report["all_invariants_hold"])
        self.assertEqual(len(reconstituted.contracts), len(self.engine.contracts))
        self.assertEqual(len(reconstituted.margin_profiles), len(self.engine.margin_profiles))
        self.assertEqual(len(reconstituted.trades), len(self.engine.trades))

    # -----------------------------------------------------------------------
    # 2. Especificação do Contrato e Túneis de Negociação
    # -----------------------------------------------------------------------

    def test_contract_validation(self) -> None:
        """Valida regras de cadastro do contrato futuro CBI."""
        # Ticker válido
        contract = CbioContract(
            ticker="CBIZ25",
            expiration_date=date(2025, 12, 30),
            reference_price=Decimal("90.00"),
        )
        self.assertEqual(contract.contract_multiplier, 100)
        self.assertEqual(contract.tick_size, Decimal("0.01"))
        self.assertEqual(contract.notional_per_contract(Decimal("90.00")), Decimal("9000.00"))

        # Tickers inválidos
        with self.assertRaises(CbioFuturesValidationError):
            CbioContract(
                ticker="PETR4",
                expiration_date=date(2025, 12, 30),
                reference_price=Decimal("90.00"),
            )
        with self.assertRaises(CbioFuturesValidationError):
            CbioContract(
                ticker="CBIW25",  # Mês W inválido
                expiration_date=date(2025, 12, 30),
                reference_price=Decimal("90.00"),
            )
        with self.assertRaises(CbioFuturesValidationError):
            CbioContract(
                ticker="CBIZ25",
                expiration_date=date(2025, 12, 30),
                reference_price=Decimal("-10.00"),
            )
        with self.assertRaises(CbioFuturesValidationError):
            # Hard limit menor ou igual ao túnel de leilão
            CbioContract(
                ticker="CBIZ25",
                expiration_date=date(2025, 12, 30),
                reference_price=Decimal("90.00"),
                hard_limit_pct=Decimal("3.00"),
                auction_collar_pct=Decimal("4.00"),
            )

    def test_tunnel_collar_calculation(self) -> None:
        """Valida o cálculo das faixas de preço para Túnel de Rejeição e Túnel de Leilão."""
        contract = CbioContract(
            ticker="CBIZ25",
            expiration_date=date(2025, 12, 30),
            reference_price=Decimal("100.00"),
            hard_limit_pct=Decimal("10.00"),  # +/- 10% -> [90.00, 110.00]
            auction_collar_pct=Decimal("4.00"),  # +/- 4% -> [96.00, 104.00]
        )
        min_h, max_h = contract.hard_limits()
        self.assertEqual(min_h, Decimal("90.00"))
        self.assertEqual(max_h, Decimal("110.00"))

        min_a, max_a = contract.auction_collars()
        self.assertEqual(min_a, Decimal("96.00"))
        self.assertEqual(max_a, Decimal("104.00"))

    def test_puma_hard_collar_rejection(self) -> None:
        """Valida que o PUMA rejeita imediatamente ordens com preços fora do túnel de rejeição."""
        engine = CbioFuturesEngine()
        contract = CbioContract(
            ticker="CBIZ25",
            expiration_date=date(2025, 12, 30),
            reference_price=Decimal("100.00"),
            hard_limit_pct=Decimal("10.00"),
            auction_collar_pct=Decimal("4.00"),
        )
        engine.register_contract(contract)
        engine.register_account("ACC-01", "INVESTIDOR A")
        engine.deposit_collateral("DEP-01", "ACC-01", CollateralType.CASH_BRL, Decimal("500000.00"))

        # Ordem de compra acima do hard limit (+10% -> max 110.00, preço 115.00)
        bad_order_high = Order(
            order_id="ORD-BAD-01",
            ticker="CBIZ25",
            account_id="ACC-01",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            price=Decimal("115.00"),
        )
        with self.assertRaises(PumaCollarViolationError):
            engine.submit_order(bad_order_high)
        self.assertEqual(bad_order_high.status, OrderStatus.REJECTED)
        self.assertIn("Túnel de Rejeição", bad_order_high.rejection_reason or "")

        # Ordem de venda abaixo do hard limit (-10% -> min 90.00, preço 85.00)
        bad_order_low = Order(
            order_id="ORD-BAD-02",
            ticker="CBIZ25",
            account_id="ACC-01",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=10,
            price=Decimal("85.00"),
        )
        with self.assertRaises(PumaCollarViolationError):
            engine.submit_order(bad_order_low)
        self.assertEqual(bad_order_low.status, OrderStatus.REJECTED)

    # -----------------------------------------------------------------------
    # 3. Leilão de Volatilidade (Auction Collar & Equilibrium PTF)
    # -----------------------------------------------------------------------

    def test_volatility_auction_trigger_and_uncrossing(self) -> None:
        """Valida acionamento do leilão por quebra de túnel dinâmico e apuração do preço de equilíbrio."""
        engine = CbioFuturesEngine()
        contract = CbioContract(
            ticker="CBIZ25",
            expiration_date=date(2025, 12, 30),
            reference_price=Decimal("100.00"),
            hard_limit_pct=Decimal("10.00"),
            auction_collar_pct=Decimal("4.00"),  # Túnel leilão: [96.00, 104.00]
        )
        engine.register_contract(contract)
        engine.register_account("ACC-BUY", "COMPRADOR AGRESSIVO")
        engine.register_account("ACC-SELL", "VENDEDOR PASSIVO")
        engine.deposit_collateral("DEP-01", "ACC-BUY", CollateralType.CASH_BRL, Decimal("1000000.00"))
        engine.deposit_collateral("DEP-02", "ACC-SELL", CollateralType.CASH_BRL, Decimal("1000000.00"))

        # Ordem de compra a 105.00 (dentro do hard limit 110.00, mas acima do auction collar 104.00)
        buy_collar_trigger = Order(
            order_id="ORD-AUCT-BUY-01",
            ticker="CBIZ25",
            account_id="ACC-BUY",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=30,
            price=Decimal("105.00"),
        )
        trades_initial = engine.submit_order(buy_collar_trigger)
        # Nenhuma execução imediata, transiciona para VOLATILITY_AUCTION
        self.assertEqual(len(trades_initial), 0)
        self.assertEqual(contract.trading_phase, TradingPhase.VOLATILITY_AUCTION)

        # Adiciona ordens no leilão
        sell_order_1 = Order(
            order_id="ORD-AUCT-SELL-01",
            ticker="CBIZ25",
            account_id="ACC-SELL",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=20,
            price=Decimal("104.50"),
        )
        engine.submit_order(sell_order_1)

        sell_order_2 = Order(
            order_id="ORD-AUCT-SELL-02",
            ticker="CBIZ25",
            account_id="ACC-SELL",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=10,
            price=Decimal("105.00"),
        )
        engine.submit_order(sell_order_2)

        # Resolve leilão de volatilidade
        ptf, auction_trades = engine.resolve_volatility_auction("CBIZ25")
        self.assertIsNotNone(ptf)
        self.assertEqual(ptf, Decimal("105.00"))
        self.assertEqual(len(auction_trades), 2)
        total_auction_qty = sum(t.quantity for t in auction_trades)
        self.assertEqual(total_auction_qty, 30)

        # Retorna para negociação contínua com novo preço de referência
        self.assertEqual(contract.trading_phase, TradingPhase.CONTINUOUS)
        self.assertEqual(contract.reference_price, Decimal("105.00"))

        # Invariantes passam
        rep = engine.verify_invariants()
        self.assertTrue(rep["all_invariants_hold"])

    # -----------------------------------------------------------------------
    # 4. Negociação Contínua FIFO e Cancelamento
    # -----------------------------------------------------------------------

    def test_continuous_matching_fifo(self) -> None:
        """Valida casamentos contínuos respeitando a prioridade Preço-Tempo."""
        engine = CbioFuturesEngine()
        contract = CbioContract(
            ticker="CBIF26",
            expiration_date=date(2026, 1, 30),
            reference_price=Decimal("80.00"),
            hard_limit_pct=Decimal("10.00"),
            auction_collar_pct=Decimal("5.00"),
        )
        engine.register_contract(contract)
        engine.register_account("ACC-A", "BROKER A")
        engine.register_account("ACC-B", "BROKER B")
        engine.register_account("ACC-C", "BROKER C")
        for acc in ("ACC-A", "ACC-B", "ACC-C"):
            engine.deposit_collateral(f"DEP-{acc}", acc, CollateralType.CASH_BRL, Decimal("500000.00"))

        # Ordens de venda passivas descansando no livro (Asks)
        sell1 = Order(
            order_id="ORD-S1",
            ticker="CBIF26",
            account_id="ACC-A",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=15,
            price=Decimal("80.20"),
        )
        sell2 = Order(
            order_id="ORD-S2",
            ticker="CBIF26",
            account_id="ACC-B",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=25,
            price=Decimal("80.50"),
        )
        engine.submit_order(sell1)
        engine.submit_order(sell2)

        # Ordem de compra agressiva varrendo o book até 80.50 (tamanho 30)
        # Deve preencher 15 a 80.20 e 15 a 80.50
        buy1 = Order(
            order_id="ORD-B1",
            ticker="CBIF26",
            account_id="ACC-C",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=30,
            price=Decimal("80.50"),
        )
        trades = engine.submit_order(buy1)

        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0].price, Decimal("80.20"))
        self.assertEqual(trades[0].quantity, 15)
        self.assertEqual(trades[1].price, Decimal("80.50"))
        self.assertEqual(trades[1].quantity, 15)

        self.assertEqual(sell1.status, OrderStatus.FILLED)
        self.assertEqual(sell2.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(sell2.remaining_quantity, 10)
        self.assertEqual(buy1.status, OrderStatus.FILLED)

        # Valida Open Interest
        self.assertEqual(engine.get_open_interest("CBIF26"), 30)

        # Cancela ordem residual
        cancelled_sell2 = engine.cancel_order("ORD-S2")
        self.assertEqual(cancelled_sell2.status, OrderStatus.CANCELLED)

    def test_order_cancellation_errors(self) -> None:
        """Garante exceções adequadas ao tentar cancelar ordem inexistente ou preenchida."""
        engine = CbioFuturesEngine()
        contract = CbioContract(
            ticker="CBIF26",
            expiration_date=date(2026, 1, 30),
            reference_price=Decimal("80.00"),
        )
        engine.register_contract(contract)
        engine.register_account("ACC-01", "ACC 01")
        engine.deposit_collateral("DEP-01", "ACC-01", CollateralType.CASH_BRL, Decimal("100000.00"))

        with self.assertRaises(OrderNotFoundError):
            engine.cancel_order("ORD-NON-EXISTENT")

    # -----------------------------------------------------------------------
    # 5. Gestão de Risco, Colateral e Chamada de Margem (CORE)
    # -----------------------------------------------------------------------

    def test_collateral_haircuts_and_margin_calls(self) -> None:
        """Valida que títulos públicos sofrem haircut e chamada de margem é emitida para déficit."""
        engine = CbioFuturesEngine()
        contract = CbioContract(
            ticker="CBIQ25",
            expiration_date=date(2025, 8, 29),
            reference_price=Decimal("85.00"),
        )
        engine.register_contract(contract)
        engine.register_account("ACC-RISK", "TESTE RISCO")

        # Depósito de R$ 100.000 em TPF com haircut de 5% -> Efetivo R$ 95.000,00
        dep_tpf = engine.deposit_collateral(
            deposit_id="DEP-TPF-1",
            account_id="ACC-RISK",
            collateral_type=CollateralType.SOVEREIGN_BOND,
            nominal_amount=Decimal("100000.00"),
            haircut_pct=Decimal("5.00"),
        )
        self.assertEqual(dep_tpf.effective_value, Decimal("95000.00"))
        profile = engine.margin_profiles["ACC-RISK"]
        self.assertEqual(profile.total_collateral_value, Decimal("95000.00"))

        # Tenta submeter ordem gigante que exige margem muito acima de R$ 95.000
        # 150 contratos @ 85.00 -> Nocional: 150 * 100 * 85 = R$ 1.275.000
        # Margem a 12%: R$ 153.000 > R$ 95.000
        huge_order = Order(
            order_id="ORD-HUGE-01",
            ticker="CBIQ25",
            account_id="ACC-RISK",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=150,
            price=Decimal("85.00"),
        )
        with self.assertRaises(MarginDeficitError):
            engine.submit_order(huge_order)

        # Uma chamada de margem deve ter sido registrada
        self.assertTrue(len(engine.margin_calls) > 0)
        call = engine.margin_calls[0]
        self.assertEqual(call.account_id, "ACC-RISK")
        self.assertFalse(call.resolved)

        # Depósito de caixa adicional para cobrir o déficit
        engine.deposit_collateral(
            deposit_id="DEP-CASH-CURE",
            account_id="ACC-RISK",
            collateral_type=CollateralType.CASH_BRL,
            nominal_amount=Decimal("200000.00"),
        )
        self.assertTrue(call.resolved)

    # -----------------------------------------------------------------------
    # 6. Ajuste Diário Mark-to-Market e Conservação Soma-Zero
    # -----------------------------------------------------------------------

    def test_daily_mark_to_market_zero_sum(self) -> None:
        """Valida que o ajuste diário calcula ganhos/perdas e obedece à invariante estrita de soma-zero."""
        engine = CbioFuturesEngine()
        contract = CbioContract(
            ticker="CBIZ24",
            expiration_date=date(2024, 12, 30),
            reference_price=Decimal("85.00"),
        )
        engine.register_contract(contract)
        engine.register_account("ACC-BUYER", "COMPRADOR CBIO")
        engine.register_account("ACC-SELLER", "VENDEDOR CBIO")
        engine.deposit_collateral("D1", "ACC-BUYER", CollateralType.CASH_BRL, Decimal("500000.00"))
        engine.deposit_collateral("D2", "ACC-SELLER", CollateralType.CASH_BRL, Decimal("500000.00"))

        # Executa negócio de 100 contratos a R$ 85,00
        order_b = Order(
            order_id="OB-1", ticker="CBIZ24", account_id="ACC-BUYER",
            side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=100, price=Decimal("85.00")
        )
        order_s = Order(
            order_id="OS-1", ticker="CBIZ24", account_id="ACC-SELLER",
            side=OrderSide.SELL, order_type=OrderType.LIMIT, quantity=100, price=Decimal("85.00")
        )
        engine.submit_order(order_s)
        engine.submit_order(order_b)

        # Ciclo Dia 1: Preço de Ajuste sobe para R$ 87,00 (+R$ 2,00 por CBIO)
        # Ganho Comprador: (87.00 - 85.00) * 100 * 100 = +R$ 20.000,00
        # Perda Vendedor:  (85.00 - 87.00) * 100 * 100 = -R$ 20.000,00
        adj_d1 = engine.run_daily_settlement("CBIZ24", Decimal("87.00"), date(2024, 12, 1))

        self.assertEqual(adj_d1.long_total_adjustment, Decimal("20000.00"))
        self.assertEqual(adj_d1.short_total_adjustment, Decimal("-20000.00"))
        self.assertEqual(adj_d1.net_clearing_adjustment, Decimal("0.00"))
        self.assertEqual(engine.margin_profiles["ACC-BUYER"].cash_settlement_balance, Decimal("20000.00"))
        self.assertEqual(engine.margin_profiles["ACC-SELLER"].cash_settlement_balance, Decimal("-20000.00"))

        # Ciclo Dia 2: Mercado recua para R$ 86,50 (-R$ 0,50 em relação a PA D1 87.00)
        # Comprador: (86.50 - 87.00) * 100 * 100 = -R$ 5.000,00 (Saldo acumulado: 15.000,00)
        # Vendedor:  (87.00 - 86.50) * 100 * 100 = +R$ 5.000,00 (Saldo acumulado: -15.000,00)
        adj_d2 = engine.run_daily_settlement("CBIZ24", Decimal("86.50"), date(2024, 12, 2))

        self.assertEqual(adj_d2.long_total_adjustment, Decimal("-5000.00"))
        self.assertEqual(adj_d2.short_total_adjustment, Decimal("5000.00"))
        self.assertEqual(adj_d2.net_clearing_adjustment, Decimal("0.00"))
        self.assertEqual(engine.margin_profiles["ACC-BUYER"].cash_settlement_balance, Decimal("15000.00"))
        self.assertEqual(engine.margin_profiles["ACC-SELLER"].cash_settlement_balance, Decimal("-15000.00"))

        # Invariantes formais passam
        rep = engine.verify_invariants()
        self.assertTrue(rep["all_invariants_hold"])

    # -----------------------------------------------------------------------
    # 7. Liquidação Final Exclusivamente Financeira no Vencimento
    # -----------------------------------------------------------------------

    def test_final_cash_settlement_at_expiration(self) -> None:
        """Valida liquidação financeira baseada no preço spot de CBIO e encerramento de Open Interest."""
        engine = CbioFuturesEngine()
        contract = CbioContract(
            ticker="CBIZ24",
            expiration_date=date(2024, 12, 30),
            reference_price=Decimal("85.00"),
        )
        engine.register_contract(contract)
        engine.register_account("ACC-LONG", "LONG HEDGER")
        engine.register_account("ACC-SHORT", "SHORT HEDGER")
        engine.deposit_collateral("D1", "ACC-LONG", CollateralType.CASH_BRL, Decimal("500000.00"))
        engine.deposit_collateral("D2", "ACC-SHORT", CollateralType.CASH_BRL, Decimal("500000.00"))

        # Executa negócio de 40 contratos a R$ 85,00
        engine.submit_order(Order(
            order_id="OS-1", ticker="CBIZ24", account_id="ACC-SHORT",
            side=OrderSide.SELL, order_type=OrderType.LIMIT, quantity=40, price=Decimal("85.00")
        ))
        engine.submit_order(Order(
            order_id="OB-1", ticker="CBIZ24", account_id="ACC-LONG",
            side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=40, price=Decimal("85.00")
        ))

        # Ajuste diário pré-vencimento a R$ 85,50
        engine.run_daily_settlement("CBIZ24", Decimal("85.50"), date(2024, 12, 29))
        self.assertEqual(engine.get_open_interest("CBIZ24"), 40)

        # No vencimento (2024-12-30), apuração do preço spot oficial de fechamento: R$ 86,20
        # P&L Final Comprador: (86.20 - 85.50) * 100 * 40 = +R$ 2.800,00
        # P&L Final Vendedor:  (85.50 - 86.20) * 100 * 40 = -R$ 2.800,00
        final_res = engine.run_final_cash_settlement(
            ticker="CBIZ24",
            spot_final_price=Decimal("86.20"),
            settlement_date=date(2024, 12, 30),
        )

        self.assertEqual(final_res.long_total_pnl, Decimal("2800.00"))
        self.assertEqual(final_res.short_total_pnl, Decimal("-2800.00"))
        self.assertEqual(final_res.net_clearing_pnl, Decimal("0.00"))
        self.assertEqual(final_res.closed_contracts, 80)  # 40 Long + 40 Short

        # Contrato expirado e Open Interest zerado
        self.assertEqual(contract.trading_phase, TradingPhase.EXPIRED)
        self.assertEqual(engine.get_open_interest("CBIZ24"), 0)

        # Tentativa de negociar em contrato expirado levanta erro
        with self.assertRaises(ExpiredContractError):
            engine.submit_order(Order(
                order_id="OB-AFTER", ticker="CBIZ24", account_id="ACC-LONG",
                side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=10, price=Decimal("86.20")
            ))

        # Invariantes pós-liquidação final
        rep = engine.verify_invariants()
        self.assertTrue(rep["all_invariants_hold"])

    # -----------------------------------------------------------------------
    # 8. Detecção Rigorosa de Quebra de Invariantes Formais
    # -----------------------------------------------------------------------

    def test_daily_adjustment_zero_sum_violation_detection(self) -> None:
        """Garante que violação sintética de soma-zero no ajuste diário é detectada por LAW-086."""
        # Altera intencionalmente o net_clearing_adjustment
        fake_adj = DailyAdjustmentResult(
            settlement_date=date(2024, 12, 1),
            ticker="CBIF25",
            settlement_price=Decimal("86.00"),
            account_adjustments={"A": Decimal("100.00"), "B": Decimal("-99.00")},
            long_total_adjustment=Decimal("100.00"),
            short_total_adjustment=Decimal("-99.00"),
            net_clearing_adjustment=Decimal("1.00"),  # Quebra de soma-zero
        )
        self.engine.daily_adjustments.append(fake_adj)

        with self.assertRaises(InvariantViolationError) as ctx:
            self.engine.verify_invariants()
        self.assertEqual(ctx.exception.law_code, "LAW-086")

    def test_open_interest_parity_violation_detection(self) -> None:
        """Garante que assimetria induzida entre pontas compradas e vendidas é capturada por LAW-087."""
        # Cria assimetria artificial na posição de uma conta
        pos = list(self.engine.positions.values())[2]  # CBIF25 LONG
        pos.quantity += 5  # Quebra paridade de open interest

        with self.assertRaises(InvariantViolationError) as ctx:
            self.engine.verify_invariants()
        self.assertEqual(ctx.exception.law_code, "LAW-087")


if __name__ == "__main__":
    unittest.main()
