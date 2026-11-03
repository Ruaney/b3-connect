"""Suíte de testes automatizados para ETFs — Criação e Resgate no Mercado Primário via PCF na B3 (FL-035)."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.etf_creation_redemption import (
    BasketComponent,
    CustodyTransferLeg,
    EtfOrderType,
    EtfPrimaryMarketEngine,
    EtfPrimaryMarketError,
    EtfPrimaryOrder,
    EtfSettlementInstruction,
    EtfValidationError,
    InsufficientBasketCustodyError,
    InsufficientEtfQuotasError,
    InvalidCreationUnitMultipleError,
    PcfParityError,
    PortfolioCompositionFile,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_ROOT / "fixtures" / "etf_primary_basket.json"


class TestEtfCreationRedemptionFixture(unittest.TestCase):
    """Valida o carregamento do PCF oficial (BOVA11) e o processamento das ordens primárias da fixture."""

    def setUp(self) -> None:
        self.engine = EtfPrimaryMarketEngine.from_json(FIXTURE_PATH)
        self.pcf = self.engine.pcf

    def test_pcf_structure_and_accounting_parity(self) -> None:
        """Verifica se os 6 ativos componentes + Cash Component igualam exatamente Creation Unit * NAV."""
        self.assertEqual(self.pcf.pcf_id, "PCF-BOVA11-20261103")
        self.assertEqual(self.pcf.etf_ticker, "BOVA11")
        self.assertEqual(self.pcf.etf_isin, "BRBOVACTF003")
        self.assertEqual(self.pcf.creation_unit_size, 50000)
        self.assertEqual(self.pcf.nav_per_quota, Decimal("125.50"))
        self.assertEqual(len(self.pcf.components), 6)

        # 50.000 * 125,50 = R$ 6.275.000,00
        self.assertEqual(self.pcf.unit_nav_value, Decimal("6275000.00"))
        # Soma das 6 ações = R$ 6.273.000,00
        self.assertEqual(self.pcf.basket_equities_value_per_unit, Decimal("6273000.00"))
        # Cash Component = R$ 2.000,00
        self.assertEqual(self.pcf.cash_component_per_unit, Decimal("2000.00"))
        # Identidade fundamental: 6.273.000,00 + 2.000,00 == 6.275.000,00
        self.assertEqual(
            self.pcf.basket_equities_value_per_unit + self.pcf.cash_component_per_unit,
            self.pcf.unit_nav_value,
        )
        self.assertTrue(self.pcf.validate_nav_parity())

    def test_process_all_fixture_orders_and_verify_instructions(self) -> None:
        """Executa o lote de 4 ordens (2 Criações e 2 Resgates) da fixture e confere instruções e saldos."""
        self.assertEqual(len(self.engine.orders), 4)
        instructions = self.engine.process_batch()
        self.assertEqual(len(instructions), 4)

        # Ordem 1: Criação de 100.000 cotas (2 Lotes Mínimos)
        inst_create_1 = instructions[0]
        self.assertEqual(inst_create_1.order_id, "ORD-ETF-CREATE-001")
        self.assertEqual(inst_create_1.order_type, EtfOrderType.CREATION)
        self.assertEqual(inst_create_1.creation_units, 2)
        self.assertEqual(inst_create_1.quotas_quantity, 100000)
        self.assertEqual(inst_create_1.basket_equities_value, Decimal("12546000.00"))
        self.assertEqual(inst_create_1.cash_component_amount, Decimal("4000.00"))
        self.assertEqual(inst_create_1.total_nav_value, Decimal("12550000.00"))
        self.assertEqual(
            inst_create_1.basket_equities_value + inst_create_1.cash_component_amount,
            Decimal(inst_create_1.quotas_quantity) * inst_create_1.nav_per_quota,
        )
        self.assertEqual(inst_create_1.cash_settlement_direction, "AP_PAYS_FUND")
        self.assertEqual(inst_create_1.etf_quota_action, "MINT_TO_AP")
        self.assertEqual(inst_create_1.etf_quota_leg.from_custody_account, "99001-0")
        self.assertEqual(inst_create_1.etf_quota_leg.to_custody_account, "0114-00010-1")
        self.assertEqual(len(inst_create_1.equity_transfer_legs), 6)
        self.assertEqual(inst_create_1.required_equities["VALE3"], 60000)
        self.assertEqual(inst_create_1.required_equities["PETR4"], 70000)
        self.assertEqual(inst_create_1.required_equities["ITUB4"], 64000)
        self.assertEqual(inst_create_1.required_equities["WEGE3"], 30000)
        self.assertEqual(inst_create_1.required_equities["BBDC4"], 90000)
        self.assertEqual(inst_create_1.required_equities["B3SA3"], 80000)

        for leg in inst_create_1.equity_transfer_legs:
            self.assertEqual(leg.from_custody_account, "0114-00010-1")
            self.assertEqual(leg.to_custody_account, "99001-0")
            self.assertEqual(leg.transfer_type, "BASKET_IN_KIND_DELIVERY")

        # Ordem 2: Resgate de 50.000 cotas (1 Lote Mínimo)
        inst_redeem_1 = instructions[1]
        self.assertEqual(inst_redeem_1.order_id, "ORD-ETF-REDEEM-001")
        self.assertEqual(inst_redeem_1.order_type, EtfOrderType.REDEMPTION)
        self.assertEqual(inst_redeem_1.creation_units, 1)
        self.assertEqual(inst_redeem_1.quotas_quantity, 50000)
        self.assertEqual(inst_redeem_1.basket_equities_value, Decimal("6273000.00"))
        self.assertEqual(inst_redeem_1.cash_component_amount, Decimal("2000.00"))
        self.assertEqual(inst_redeem_1.total_nav_value, Decimal("6275000.00"))
        self.assertEqual(inst_redeem_1.cash_settlement_direction, "FUND_PAYS_AP")
        self.assertEqual(inst_redeem_1.etf_quota_action, "BURN_FROM_AP")
        self.assertEqual(inst_redeem_1.etf_quota_leg.from_custody_account, "0120-00020-9")
        self.assertEqual(inst_redeem_1.etf_quota_leg.to_custody_account, "99001-0")
        for leg in inst_redeem_1.equity_transfer_legs:
            self.assertEqual(leg.from_custody_account, "99001-0")
            self.assertEqual(leg.to_custody_account, "0120-00020-9")
            self.assertEqual(leg.transfer_type, "BASKET_IN_KIND_RECEIPT")

    def test_reconcile_primary_session_summary(self) -> None:
        """Valida a reconciliação líquida da sessão primária (5 lotes criados vs 3 lotes resgatados = +2 lotes)."""
        summary = self.engine.reconcile_primary_session()
        self.assertEqual(summary["total_orders_processed"], 4)
        self.assertEqual(summary["created_units"], 5)
        self.assertEqual(summary["redeemed_units"], 3)
        self.assertEqual(summary["net_units"], 2)
        self.assertEqual(summary["created_quotas"], 250000)
        self.assertEqual(summary["redeemed_quotas"], 150000)
        self.assertEqual(summary["net_quotas_change"], 100000)
        self.assertEqual(summary["net_cash_to_fund"], Decimal("4000.00"))
        self.assertEqual(summary["net_nav_change"], Decimal("12550000.00"))
        self.assertEqual(summary["net_equities_to_fund"]["VALE3"], 60000)
        self.assertEqual(summary["net_equities_to_fund"]["PETR4"], 70000)


class TestEtfCreationRedemptionEdgeCases(unittest.TestCase):
    """Testa casos de borda, rejeições de múltiplos fracionários, insuficiência de custódia e Cash Component negativo."""

    def setUp(self) -> None:
        self.engine = EtfPrimaryMarketEngine.from_json(FIXTURE_PATH)

    def test_reject_non_multiple_of_creation_unit(self) -> None:
        """Rejeita ordens cuja quantidade de cotas não seja múltiplo inteiro exato de 50.000 cotas."""
        for invalid_qty in (25000, 50001, 75000, 49999, 1):
            order = EtfPrimaryOrder(
                order_id=f"ORD-INVALID-{invalid_qty}",
                order_type=EtfOrderType.CREATION,
                ap_participant_code="0114",
                ap_custody_account="0114-00010-1",
                quotas_quantity=invalid_qty,
                available_equities={c.ticker: 1000000 for c in self.engine.pcf.components},
            )
            with self.assertRaises(InvalidCreationUnitMultipleError):
                self.engine.process_order(order)

    def test_reject_zero_or_negative_quotas(self) -> None:
        """Rejeita ordens com quantidade de cotas zero ou negativa."""
        for bad_qty in (0, -50000):
            with self.assertRaises(InvalidCreationUnitMultipleError):
                EtfPrimaryOrder(
                    order_id="ORD-ZERO",
                    order_type=EtfOrderType.CREATION,
                    ap_participant_code="0114",
                    ap_custody_account="0114-00010-1",
                    quotas_quantity=bad_qty,
                )

    def test_reject_creation_with_insufficient_equity_custody_balance(self) -> None:
        """Rejeita ordem de Criação quando falta saldo livre em uma ou mais ações da cesta (in-kind)."""
        # Para 1 Lote Mínimo (50.000 cotas), VALE3 exige 30.000 e WEGE3 exige 15.000
        insufficient_equities = {
            "VALE3": 29999,  # Falta 1 ação
            "PETR4": 35000,
            "ITUB4": 32000,
            "WEGE3": 10000,  # Faltam 5.000 ações
            "BBDC4": 45000,
            "B3SA3": 40000,
        }
        order = EtfPrimaryOrder(
            order_id="ORD-SHORT-EQ",
            order_type=EtfOrderType.CREATION,
            ap_participant_code="0114",
            ap_custody_account="0114-00010-1",
            quotas_quantity=50000,
            available_equities=insufficient_equities,
        )
        with self.assertRaises(InsufficientBasketCustodyError) as ctx:
            self.engine.process_order(order)

        self.assertIn("VALE3", ctx.exception.deficits)
        self.assertEqual(ctx.exception.deficits["VALE3"]["shortfall"], 1)
        self.assertIn("WEGE3", ctx.exception.deficits)
        self.assertEqual(ctx.exception.deficits["WEGE3"]["shortfall"], 5000)

    def test_reject_redemption_with_insufficient_etf_quotas_balance(self) -> None:
        """Rejeita ordem de Resgate quando o Agente Autorizado não possui cotas livres suficientes do ETF."""
        order = EtfPrimaryOrder(
            order_id="ORD-SHORT-QUOTAS",
            order_type=EtfOrderType.REDEMPTION,
            ap_participant_code="0120",
            ap_custody_account="0120-00020-9",
            quotas_quantity=100000,
            available_etf_quotas=50000,  # Faltam 50.000 cotas
        )
        with self.assertRaises(InsufficientEtfQuotasError) as ctx:
            self.engine.process_order(order)

        self.assertIn("BOVA11", ctx.exception.deficits)
        self.assertEqual(ctx.exception.deficits["BOVA11"]["shortfall"], 50000)

    def test_detect_broken_pcf_nav_parity(self) -> None:
        """Lança PcfParityError se a soma da cesta + Cash Component divergir de Creation Unit * NAV."""
        data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        # Altera cash_component_per_unit de 2000.00 para 1999.99 quebrando a paridade em R$ 0,01
        data["cash_component_per_unit"] = "1999.99"
        with self.assertRaises(PcfParityError):
            PortfolioCompositionFile.from_dict(data)

    def test_negative_cash_component_inverts_financial_settlement_direction(self) -> None:
        """Verifica que quando o Cash Component é negativo, a direção financeira se inverte corretamente."""
        data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        # Se NAV por cota for 125.40 -> Unit NAV = 50.000 * 125.40 = 6.270.000,00
        # Cesta = 6.273.000,00 -> Cash Component = -3.000,00
        data["nav_per_quota"] = "125.40"
        data["cash_component_per_unit"] = "-3000.00"
        pcf_neg = PortfolioCompositionFile.from_dict(data)
        engine_neg = EtfPrimaryMarketEngine(pcf=pcf_neg)

        create_order = EtfPrimaryOrder(
            order_id="ORD-NEG-CASH-CREATE",
            order_type="CREATION",
            ap_participant_code="0114",
            ap_custody_account="0114-00010-1",
            quotas_quantity=50000,
            available_equities={c.ticker: c.quantity_per_unit for c in pcf_neg.components},
        )
        inst_c = engine_neg.process_order(create_order)
        self.assertEqual(inst_c.cash_component_amount, Decimal("-3000.00"))
        self.assertEqual(inst_c.cash_settlement_direction, "FUND_PAYS_AP")
        self.assertEqual(inst_c.total_nav_value, Decimal("6270000.00"))

        redeem_order = EtfPrimaryOrder(
            order_id="ORD-NEG-CASH-REDEEM",
            order_type="REDEMPTION",
            ap_participant_code="0114",
            ap_custody_account="0114-00010-1",
            quotas_quantity=50000,
            available_etf_quotas=50000,
        )
        inst_r = engine_neg.process_order(redeem_order)
        self.assertEqual(inst_r.cash_component_amount, Decimal("-3000.00"))
        self.assertEqual(inst_r.cash_settlement_direction, "AP_PAYS_FUND")
        self.assertEqual(inst_r.total_nav_value, Decimal("6270000.00"))

    def test_serialization_roundtrip(self) -> None:
        """Verifica serialização to_dict() de PCF, Ordem e Instrução de Liquidação."""
        pcf_dict = self.engine.pcf.to_dict()
        pcf_reloaded = PortfolioCompositionFile.from_dict(pcf_dict)
        self.assertEqual(pcf_reloaded.unit_nav_value, self.engine.pcf.unit_nav_value)

        inst = self.engine.process_order(self.engine.orders[0])
        inst_dict = inst.to_dict()
        self.assertEqual(inst_dict["instruction_id"], "INST-BOVA11-ORD-ETF-CREATE-001")
        self.assertEqual(inst_dict["total_nav_value"], "12550000.00")
        self.assertTrue(inst_dict["parity_verified"])


if __name__ == "__main__":
    unittest.main()
