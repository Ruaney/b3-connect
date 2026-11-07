"""Testes unitários da Fachada Unificada de Ciclo de Vida da B3 (b3_unified_lifecycle.py).

Cobre a execução ponta a ponta através dos 7 módulos:
  1. Pré-Trade Risk
  2. PUMA Matching
  3. NoMe / RTC Registry
  4. Câmara B3 (CCP) Novation
  5. SPB / STR Settlement
  6. Central Depositária (CSD) Custody
  7. BSM Surveillance
"""

from decimal import Decimal
import unittest

from src.b3_unified_lifecycle import (
    B3Module,
    B3UnifiedLifecycleEngine,
    InvestorAccount,
    Order,
    OrderSide,
    OrderType,
    OTCAssetType,
    OTCContract,
    Participant,
    StepStatus,
)


class TestB3UnifiedLifecycle(unittest.TestCase):
    """Suíte de validação formal do ciclo de vida unificado da B3."""

    def setUp(self) -> None:
        self.engine = B3UnifiedLifecycleEngine()

        # Cadastra participantes institucionais
        self.p_itau = Participant(
            participant_id="PART-001",
            name="Itaú Corretora",
            dma_tier="DMA4",
            collateral_limit=Decimal("50000000.00"),
            cash_balance=Decimal("20000000.00"),
        )
        self.p_xp = Participant(
            participant_id="PART-002",
            name="XP Investimentos",
            dma_tier="DMA3",
            collateral_limit=Decimal("30000000.00"),
            cash_balance=Decimal("15000000.00"),
        )
        self.engine.register_participant(self.p_itau)
        self.engine.register_participant(self.p_xp)

        # Cadastra contas individualizadas por CPF
        self.acc_comprador = InvestorAccount(
            investor_id="CPF-111.222.333-44",
            name="Investidor Institucional Alpha",
            participant_id="PART-001",
            available_cash=Decimal("1000000.00"),
            custody_shares={"PETR4": 5000},
        )
        self.acc_vendedor = InvestorAccount(
            investor_id="CPF-555.666.777-88",
            name="Fundo de Ações Beta",
            participant_id="PART-002",
            available_cash=Decimal("500000.00"),
            custody_shares={"PETR4": 10000},
        )
        self.engine.register_account(self.acc_comprador)
        self.engine.register_account(self.acc_vendedor)

    def test_complete_listed_trade_lifecycle_success(self) -> None:
        """Testa um trade listado normal percorrendo com sucesso todos os 7 módulos."""
        order_buy = Order(
            order_id="ORD-BUY-101",
            investor_id="CPF-111.222.333-44",
            participant_id="PART-001",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=1000,
            price=Decimal("38.50"),
        )
        order_sell = Order(
            order_id="ORD-SELL-102",
            investor_id="CPF-555.666.777-88",
            participant_id="PART-002",
            symbol="PETR4",
            side=OrderSide.SELL,
            quantity=1000,
            price=Decimal("38.50"),
        )

        report = self.engine.execute_listed_trade(
            buy_order=order_buy,
            sell_order=order_sell,
            reference_price=Decimal("38.40"),
        )

        self.assertTrue(report.is_settled)
        self.assertTrue(report.is_compliant)
        self.assertEqual(len(report.steps), 7)
        self.assertEqual(report.total_volume, 1000)
        self.assertEqual(report.executed_price, Decimal("38.50"))
        self.assertEqual(report.total_notional, Decimal("38500.00"))

        # Valida que todos os 7 módulos foram percorridos
        expected_modules = [
            B3Module.PRE_TRADE_RISK,
            B3Module.PUMA_MATCHING,
            B3Module.NOME_REGISTRY,
            B3Module.CCP_CLEARING,
            B3Module.SPB_SETTLEMENT,
            B3Module.CSD_CUSTODY,
            B3Module.BSM_SURVEILLANCE,
        ]
        actual_modules = [s.module for s in report.steps]
        self.assertEqual(actual_modules, expected_modules)

        # Valida conservação de custódia (LAW-001)
        # Comprador: 5000 + 1000 = 6000
        # Vendedor: 10000 - 1000 = 9000
        self.assertEqual(self.acc_comprador.custody_shares["PETR4"], 6000)
        self.assertEqual(self.acc_vendedor.custody_shares["PETR4"], 9000)

        # Valida liquidação financeira (LAW-002)
        # Comprador debitado: 1.000.000 - 38.500 = 961.500
        # Vendedor creditado: 500.000 + 38.500 = 538.500
        self.assertEqual(self.acc_comprador.available_cash, Decimal("961500.00"))
        self.assertEqual(self.acc_vendedor.available_cash, Decimal("538500.00"))

        # Valida que a saída textual é legível e contém as informações estruturadas
        text = report.summary_text()
        self.assertIn("B3 UNIFIED LIFECYCLE TRACE REPORT", text)
        self.assertIn("PETR4", text)
        self.assertIn("TOTALMENTE CONFORME", text)

    def test_pre_trade_risk_fat_finger_rejection(self) -> None:
        """Testa a rejeição imediata no Módulo 1 quando ocorre erro de digitação (Fat-Finger)."""
        order_buy_bad = Order(
            order_id="ORD-BUY-FAT",
            investor_id="CPF-111.222.333-44",
            participant_id="PART-001",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=1000,
            price=Decimal("50.00"),  # Desvio > 30% em relação a R$ 38,00
        )
        order_sell = Order(
            order_id="ORD-SELL-001",
            investor_id="CPF-555.666.777-88",
            participant_id="PART-002",
            symbol="PETR4",
            side=OrderSide.SELL,
            quantity=1000,
            price=Decimal("38.00"),
        )

        report = self.engine.execute_listed_trade(
            buy_order=order_buy_bad,
            sell_order=order_sell,
            reference_price=Decimal("38.00"),
        )

        self.assertFalse(report.is_settled)
        self.assertFalse(report.is_compliant)
        self.assertEqual(len(report.steps), 1)
        self.assertEqual(report.steps[0].module, B3Module.PRE_TRADE_RISK)
        self.assertEqual(report.steps[0].status, StepStatus.REJECTED)
        self.assertIn("FAT_FINGER_REJECTION", report.bsm_flags)

    def test_pre_trade_risk_credit_limit_exceeded(self) -> None:
        """Testa a rejeição no Módulo 1 por violação de limite de risco da corretora."""
        order_buy_giant = Order(
            order_id="ORD-BUY-GIANT",
            investor_id="CPF-111.222.333-44",
            participant_id="PART-001",
            symbol="VALE3",
            side=OrderSide.BUY,
            quantity=2000000,
            price=Decimal("60.00"),  # Nocional: R$ 120.000.000 (excede o limite de R$ 50M)
        )
        order_sell = Order(
            order_id="ORD-SELL-001",
            investor_id="CPF-555.666.777-88",
            participant_id="PART-002",
            symbol="VALE3",
            side=OrderSide.SELL,
            quantity=2000000,
            price=Decimal("60.00"),
        )

        report = self.engine.execute_listed_trade(
            buy_order=order_buy_giant,
            sell_order=order_sell,
            reference_price=Decimal("60.00"),
        )

        self.assertFalse(report.is_settled)
        self.assertEqual(report.steps[0].status, StepStatus.REJECTED)
        self.assertIn("CREDIT_LIMIT_EXCEEDED", report.bsm_flags)

    def test_puma_matching_spread_gap_no_cross(self) -> None:
        """Testa que no Módulo 2 ordens com gap de spread não cruzam no book."""
        order_buy = Order(
            order_id="ORD-BUY-001",
            investor_id="CPF-111.222.333-44",
            participant_id="PART-001",
            symbol="B3SA3",
            side=OrderSide.BUY,
            quantity=500,
            price=Decimal("11.00"),
        )
        order_sell = Order(
            order_id="ORD-SELL-001",
            investor_id="CPF-555.666.777-88",
            participant_id="PART-002",
            symbol="B3SA3",
            side=OrderSide.SELL,
            quantity=500,
            price=Decimal("11.50"),  # Venda a R$ 11,50 não casa com compra a R$ 11,00
        )

        report = self.engine.execute_listed_trade(
            buy_order=order_buy,
            sell_order=order_sell,
            reference_price=Decimal("11.20"),
        )

        self.assertFalse(report.is_settled)
        self.assertEqual(len(report.steps), 2)
        self.assertEqual(report.steps[1].module, B3Module.PUMA_MATCHING)
        self.assertEqual(report.steps[1].status, StepStatus.REJECTED)

    def test_bsm_surveillance_wash_trading_detection(self) -> None:
        """Testa o Módulo 7 detectando Wash Trading quando o mesmo CPF opera nas duas pontas."""
        same_cpf = "CPF-111.222.333-44"
        order_buy = Order(
            order_id="ORD-BUY-WASH",
            investor_id=same_cpf,
            participant_id="PART-001",
            symbol="ITUB4",
            side=OrderSide.BUY,
            quantity=2000,
            price=Decimal("35.00"),
        )
        order_sell = Order(
            order_id="ORD-SELL-WASH",
            investor_id=same_cpf,  # Mesmo beneficiário final!
            participant_id="PART-002",
            symbol="ITUB4",
            side=OrderSide.SELL,
            quantity=2000,
            price=Decimal("35.00"),
        )

        report = self.engine.execute_listed_trade(
            buy_order=order_buy,
            sell_order=order_sell,
            reference_price=Decimal("35.00"),
        )

        self.assertTrue(report.is_settled)
        self.assertFalse(report.is_compliant)
        self.assertIn("WASH_TRADING_DETECTED", report.bsm_flags)
        last_step = report.steps[-1]
        self.assertEqual(last_step.module, B3Module.BSM_SURVEILLANCE)
        self.assertEqual(last_step.status, StepStatus.FLAGGED)
        self.assertIn("Wash Trading", last_step.summary)

    def test_otc_registration_with_fiduciary_lien(self) -> None:
        """Testa o ciclo de vida completo de registro em balcão NoMe/Cetip com gravame."""
        contract = OTCContract(
            contract_id="CDB-2026-9901",
            issuer_id="BANCO-EMISSOR-01",
            investor_id="CPF-111.222.333-44",
            asset_type=OTCAssetType.CDB,
            notional=Decimal("250000.00"),
            annual_rate=Decimal("0.1175"),
            maturity_days=730,
            lien_holder="BANCO-CREDOR-02",  # Gravame registrado
            asset_reference="RECEIVABLE-TRAVA-DOMICILIO",
        )

        report = self.engine.execute_otc_registration(contract)

        self.assertTrue(report.is_settled)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.operation_type, "OTC_REGISTRATION")
        self.assertEqual(report.total_notional, Decimal("250000.00"))

        # Valida presença dos módulos NoMe, CCP, SPB, CSD e BSM
        modules = [s.module for s in report.steps]
        self.assertIn(B3Module.NOME_REGISTRY, modules)
        self.assertIn(B3Module.CSD_CUSTODY, modules)
        self.assertIn(B3Module.BSM_SURVEILLANCE, modules)

        # Valida que o gravame foi registrado
        gravame_step = [s for s in report.steps if s.conservation_law == "LAW-037"]
        self.assertEqual(len(gravame_step), 1)
        self.assertIn("Trava de domicílio", gravame_step[0].summary)


if __name__ == "__main__":
    unittest.main()
