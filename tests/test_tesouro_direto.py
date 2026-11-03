"""Testes unitários para a Integração Tesouro Direto & API Hub de Varejo B3 (FL-031).

Valida:
1. Catálogo das 7 modalidades de títulos públicos do Tesouro Direto (LFT, LTN,
   NTN-F, NTN-B Principal, NTN-B, Tesouro Renda+ e Tesouro Educa+).
2. Regra de fração mínima e múltiplos exatos de 0,01 título.
3. Regra de valor financeiro mínimo por operação (R$ 30,00).
4. Cálculo determinístico de valor financeiro bruto, Taxa de Custódia B3
   (0,20% a.a. provisionada pro-rata die) e valor líquido de liquidação.
5. Controle de limite mensal de aplicação por CPF (R$ 1.000.000,00) e isenção
   de teto para resgates antecipados (recompras pelo Tesouro Nacional).
6. Liquidação D+1 útil, controle de saldo em custódia e processamento integral
   da fixture `fixtures/tesouro_direto_orders.json`.
"""

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.tesouro_direto import (
    CustodyFeeCalculation,
    InsufficientCustodyBalanceError,
    InvalidQuantityFractionError,
    MinimumAmountError,
    MonthlyLimitExceededError,
    OrderOperationType,
    TesouroDiretoEngine,
    TesouroValidationError,
    TreasuryBond,
    TreasuryBondType,
    TreasuryDirectOrder,
)

FIXTURES_DIR = ROOT_DIR / "fixtures"


class TestTesouroDireto(unittest.TestCase):
    """Suíte de testes determinísticos para o motor Tesouro Direto B3 (FL-031)."""

    def setUp(self) -> None:
        """Inicializa o motor Tesouro Direto antes de cada teste."""
        self.engine = TesouroDiretoEngine()
        self.fixture_path = FIXTURES_DIR / "tesouro_direto_orders.json"

    # -----------------------------------------------------------------------
    # 1. Modalidades de Títulos e Operações
    # -----------------------------------------------------------------------

    def test_supported_treasury_bond_types_and_synonyms(self) -> None:
        """Valida o suporte às 7 modalidades do Tesouro Direto e siglas STN/Selic."""
        self.assertEqual(TreasuryBondType.from_str("LFT"), TreasuryBondType.TESOURO_SELIC)
        self.assertEqual(TreasuryBondType.from_str("LTN"), TreasuryBondType.TESOURO_PREFIXADO)
        self.assertEqual(
            TreasuryBondType.from_str("NTN-F"),
            TreasuryBondType.TESOURO_PREFIXADO_JUROS_SEMESTRAIS,
        )
        self.assertEqual(
            TreasuryBondType.from_str("NTN-B Principal"),
            TreasuryBondType.TESOURO_IPCA,
        )
        self.assertEqual(
            TreasuryBondType.from_str("NTN-B"),
            TreasuryBondType.TESOURO_IPCA_JUROS_SEMESTRAIS,
        )
        self.assertEqual(
            TreasuryBondType.from_str("NTN-B1"),
            TreasuryBondType.TESOURO_RENDA_MAIS,
        )
        self.assertEqual(
            TreasuryBondType.from_str("Tesouro Educa+"),
            TreasuryBondType.TESOURO_EDUCA_MAIS,
        )

        self.assertEqual(TreasuryBondType.TESOURO_SELIC.stn_acronym, "LFT")
        self.assertEqual(TreasuryBondType.TESOURO_SELIC.indexer_name, "SELIC")
        self.assertEqual(TreasuryBondType.TESOURO_PREFIXADO.indexer_name, "PREFIXADO")
        self.assertEqual(TreasuryBondType.TESOURO_IPCA.indexer_name, "IPCA")
        self.assertEqual(TreasuryBondType.TESOURO_RENDA_MAIS.indexer_name, "IPCA")
        self.assertEqual(TreasuryBondType.TESOURO_EDUCA_MAIS.indexer_name, "IPCA")

    def test_order_operation_type_synonyms(self) -> None:
        """Valida normalização dos tipos de operação (Aplicação e Resgate/Recompra)."""
        self.assertEqual(OrderOperationType.from_str("COMPRA"), OrderOperationType.INVESTMENT)
        self.assertEqual(OrderOperationType.from_str("APLICACAO"), OrderOperationType.INVESTMENT)
        self.assertEqual(OrderOperationType.from_str("RESGATE"), OrderOperationType.REDEMPTION)
        self.assertEqual(OrderOperationType.from_str("RECOMPRA"), OrderOperationType.REDEMPTION)

    # -----------------------------------------------------------------------
    # 2. Validação de Fração Mínima (0,01 Título) e Valor Mínimo (R$ 30,00)
    # -----------------------------------------------------------------------

    def test_quantity_fraction_validation_multiples_of_001(self) -> None:
        """Garante que apenas quantidades positivas múltiplas de 0,01 título são aceitas."""
        self.assertEqual(
            self.engine.validate_quantity_fraction(Decimal("0.01")),
            Decimal("0.01"),
        )
        self.assertEqual(
            self.engine.validate_quantity_fraction(Decimal("1.25")),
            Decimal("1.25"),
        )

        for invalid_qty in [Decimal("0.00"), Decimal("-0.05"), Decimal("0.005"), Decimal("0.123")]:
            with self.subTest(invalid_qty=invalid_qty):
                with self.assertRaises(InvalidQuantityFractionError):
                    self.engine.validate_quantity_fraction(invalid_qty)

    def test_minimum_order_amount_validation_30_brl(self) -> None:
        """Garante rejeição de ordens cujo valor financeiro bruto seja inferior a R$ 30,00."""
        # 0.01 * 2999.00 = R$ 29.99 (< R$ 30.00 -> falha)
        order_below_min = TreasuryDirectOrder(
            order_id="ORD-MIN-FAIL",
            investor_cpf="123.456.789-01",
            investor_name="INVESTIDOR TESTE",
            custody_account="0114-0001",
            custody_agent_code="0114",
            operation_type=OrderOperationType.INVESTMENT,
            bond_code="TD-PRE-2028",
            bond_type=TreasuryBondType.TESOURO_PREFIXADO,
            quantity=Decimal("0.03"),
            unit_price=Decimal("812.40"),  # 0.03 * 812.40 = 24.37 < 30.00
            trade_date=date(2026, 11, 3),
        )
        with self.assertRaises(MinimumAmountError):
            self.engine.process_order(order_below_min)

        # Exatamente R$ 30,00 (0.03 * 1000.00 = R$ 30.00 -> aceito)
        order_exact_min = TreasuryDirectOrder(
            order_id="ORD-MIN-OK",
            investor_cpf="123.456.789-01",
            investor_name="INVESTIDOR TESTE",
            custody_account="0114-0001",
            custody_agent_code="0114",
            operation_type=OrderOperationType.INVESTMENT,
            bond_code="TD-PRE-2028",
            bond_type=TreasuryBondType.TESOURO_PREFIXADO,
            quantity=Decimal("0.03"),
            unit_price=Decimal("1000.00"),
            trade_date=date(2026, 11, 3),
        )
        processed = self.engine.process_order(order_exact_min)
        self.assertEqual(processed.gross_amount, Decimal("30.00"))
        self.assertEqual(processed.net_settlement_amount, Decimal("30.00"))
        self.assertEqual(processed.status, "SETTLED_D1")

    # -----------------------------------------------------------------------
    # 3. Taxa de Custódia B3 (0,20% a.a. Pro-Rata Die) e Liquidação Líquida
    # -----------------------------------------------------------------------

    def test_b3_custody_fee_pro_rata_die_and_net_redemption(self) -> None:
        """Valida o cálculo exato da taxa de custódia B3 (0,20% a.a.) em resgate antecipado."""
        # Resgate de 10 títulos NTN-B a R$ 4.380,00 = R$ 43.800,00 por 180 dias (base 365)
        # Taxa B3 = 43800 * (0.20 / 100) * (180 / 365) = 120 * 0.002 * 180 = R$ 43,20
        # Líquido de resgate = 43800.00 - 43.20 = R$ 43.756,80
        redemption_order = TreasuryDirectOrder(
            order_id="ORD-RED-01",
            investor_cpf="345.678.901-23",
            investor_name="CARLOS EDUARDO FERREIRA",
            custody_account="0114-88903-9",
            custody_agent_code="0114",
            operation_type=OrderOperationType.REDEMPTION,
            bond_code="TD-IPCA-JS-2032",
            bond_type=TreasuryBondType.TESOURO_IPCA_JUROS_SEMESTRAIS,
            quantity=Decimal("10.00"),
            unit_price=Decimal("4380.00"),
            trade_date=date(2026, 11, 3),
            settlement_date=date(2026, 11, 4),
            holding_days=180,
        )
        processed = self.engine.process_order(redemption_order)
        self.assertEqual(processed.gross_amount, Decimal("43800.00"))
        self.assertEqual(processed.b3_custody_fee, Decimal("43.20"))
        self.assertEqual(processed.net_settlement_amount, Decimal("43756.80"))
        self.assertIsInstance(processed.fee_calculation, CustodyFeeCalculation)
        self.assertEqual(processed.fee_calculation.total_custody_fee, Decimal("43.20"))

    def test_custody_fee_with_agent_fee_and_exemption(self) -> None:
        """Valida memória de cálculo com taxa de agente e parcela isenta."""
        calc = self.engine.calculate_custody_fee(
            gross_amount=Decimal("20000.00"),
            holding_days=365,
            annual_b3_fee_rate_pct=Decimal("0.20"),
            annual_agent_fee_rate_pct=Decimal("0.10"),
            exempt_amount=Decimal("10000.00"),
        )
        # Base tributável = 20000 - 10000 = 10000.00
        # Taxa B3 (1 ano) = 10000 * 0.20% = 20.00
        # Taxa Agente (1 ano) = 10000 * 0.10% = 10.00
        self.assertEqual(calc.taxable_base_amount, Decimal("10000.00"))
        self.assertEqual(calc.b3_custody_fee, Decimal("20.00"))
        self.assertEqual(calc.agent_custody_fee, Decimal("10.00"))
        self.assertEqual(calc.total_custody_fee, Decimal("30.00"))

    # -----------------------------------------------------------------------
    # 4. Limite Mensal de Aplicação por CPF e Liquidação D+1
    # -----------------------------------------------------------------------

    def test_monthly_cpf_investment_limit_enforcement(self) -> None:
        """Valida que um CPF não pode ultrapassar R$ 1.000.000,00 de compras no mesmo mês."""
        order_1 = TreasuryDirectOrder(
            order_id="ORD-LIM-01",
            investor_cpf="111.222.333-44",
            investor_name="INVESTIDOR ALTA RENDA",
            custody_account="0114-9999",
            custody_agent_code="0114",
            operation_type=OrderOperationType.INVESTMENT,
            bond_code="TD-SELIC-2029",
            bond_type=TreasuryBondType.TESOURO_SELIC,
            quantity=Decimal("60.00"),
            unit_price=Decimal("15000.00"),  # R$ 900.000,00
            trade_date=date(2026, 11, 3),
        )
        self.engine.process_order(order_1)
        self.assertEqual(
            self.engine.get_monthly_invested_by_cpf("111.222.333-44", "2026-11"),
            Decimal("900000.00"),
        )

        # Segunda compra de R$ 150.000,00 no mesmo mês excede R$ 1.000.000,00
        order_2_exceed = TreasuryDirectOrder(
            order_id="ORD-LIM-02",
            investor_cpf="11122233344",  # Mesmo CPF sem máscara
            investor_name="INVESTIDOR ALTA RENDA",
            custody_account="0114-9999",
            custody_agent_code="0114",
            operation_type=OrderOperationType.INVESTMENT,
            bond_code="TD-SELIC-2029",
            bond_type=TreasuryBondType.TESOURO_SELIC,
            quantity=Decimal("10.00"),
            unit_price=Decimal("15000.00"),  # R$ 150.000,00 -> Total R$ 1.050.000,00
            trade_date=date(2026, 11, 18),
        )
        with self.assertRaises(MonthlyLimitExceededError):
            self.engine.process_order(order_2_exceed)

        # Porém um resgate antecipado de R$ 500.000,00 é permitido sem restrição de teto
        redemption_order = TreasuryDirectOrder(
            order_id="ORD-LIM-RED",
            investor_cpf="111.222.333-44",
            investor_name="INVESTIDOR ALTA RENDA",
            custody_account="0114-9999",
            custody_agent_code="0114",
            operation_type=OrderOperationType.REDEMPTION,
            bond_code="TD-SELIC-2029",
            bond_type=TreasuryBondType.TESOURO_SELIC,
            quantity=Decimal("40.00"),
            unit_price=Decimal("15000.00"),  # R$ 600.000,00
            trade_date=date(2026, 11, 20),
            holding_days=15,
        )
        processed_red = self.engine.process_order(redemption_order)
        self.assertEqual(processed_red.gross_amount, Decimal("600000.00"))
        self.assertEqual(
            self.engine.get_custody_position("111.222.333-44", "TD-SELIC-2029"),
            Decimal("20.00"),
        )

    def test_d_plus_1_settlement_and_custody_balance_check(self) -> None:
        """Valida cálculo automático de liquidação D+1 útil e bloqueio por falta de saldo."""
        # Sexta-feira 2026-11-06 -> Segunda-feira 2026-11-09
        friday = date(2026, 11, 6)
        self.assertEqual(
            TesouroDiretoEngine.calculate_d_plus_1_settlement_date(friday),
            date(2026, 11, 9),
        )

        # Define saldo inicial de 1.50 título e tenta resgatar 2.00 títulos
        self.engine.set_custody_position("999.888.777-66", "TD-IPCA-2035", Decimal("1.50"))
        order_insufficient = TreasuryDirectOrder(
            order_id="ORD-INSUF-01",
            investor_cpf="999.888.777-66",
            investor_name="INVESTIDOR SALDO CURTO",
            custody_account="0114-7777",
            custody_agent_code="0114",
            operation_type=OrderOperationType.REDEMPTION,
            bond_code="TD-IPCA-2035",
            bond_type=TreasuryBondType.TESOURO_IPCA,
            quantity=Decimal("2.00"),
            unit_price=Decimal("2320.50"),
            trade_date=date(2026, 11, 3),
        )
        with self.assertRaises(InsufficientCustodyBalanceError):
            self.engine.process_order(order_insufficient)

    # -----------------------------------------------------------------------
    # 5. Processamento Integral da Fixture `tesouro_direto_orders.json`
    # -----------------------------------------------------------------------

    def test_load_and_process_fixture_batch(self) -> None:
        """Processa o lote oficial de ordens e confere os valores brutos, taxas e líquidos."""
        with self.fixture_path.open("r", encoding="utf-8") as fh:
            raw_payload = json.load(fh)

        summary = self.engine.load_and_process_batch(self.fixture_path)

        self.assertEqual(summary["batch_id"], "TD-BATCH-20261103-001")
        self.assertEqual(summary["total_orders"], 8)
        self.assertEqual(len(self.engine.bonds_catalog), 7)

        for raw_order, processed_order in zip(raw_payload["orders"], summary["orders"]):
            with self.subTest(order_id=raw_order["order_id"]):
                self.assertEqual(
                    processed_order.gross_amount,
                    Decimal(raw_order["expected_gross_amount"]),
                )
                self.assertEqual(
                    processed_order.b3_custody_fee,
                    Decimal(raw_order["expected_b3_custody_fee"]),
                )
                self.assertEqual(
                    processed_order.net_settlement_amount,
                    Decimal(raw_order["expected_net_settlement_amount"]),
                )
                self.assertEqual(processed_order.status, "SETTLED_D1")

        # Totais consolidados do lote:
        # Aplicações Brutas: 3855.13 + 2031.00 + 2814.00 + 460.00 + 164.25 = 9324.38
        # Resgates Brutos: 43800.00 + 4745.00 + 15330.00 = 63875.00
        # Taxas B3: 43.20 + 9.49 + 7.56 = 60.25
        self.assertEqual(summary["total_investments_gross"], Decimal("9324.38"))
        self.assertEqual(summary["total_redemptions_gross"], Decimal("63875.00"))
        self.assertEqual(summary["total_b3_custody_fees"], Decimal("60.25"))


if __name__ == "__main__":
    unittest.main()
