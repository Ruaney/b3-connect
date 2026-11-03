"""Testes unitários para o módulo de Fundos de Investimento B3 (FL-029).

Valida:
1. Apuração de rendimentos de FII com crédito exato por cota na conta de custódia.
2. Amortização periódica de cotas de FIDC reduzindo o valor patrimonial unitário (NAV).
3. Conciliação determinística do extrato consolidado de cotistas B3 contra o livro interno.
4. Identificação e categorização de quebras de conciliação (QUANTITY_MISMATCH,
   NAV_MISMATCH, EQUITY_MISMATCH, MISSING_IN_INTERNAL, MISSING_IN_STATEMENT).
5. Ciclo de integralização primária, resgate de cotas e transferência de custódia (STVM).
6. Leis de conservação matemática e regras de validação cadastral CVM 175.
"""

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

# Garante que a raiz do projeto esteja no PYTHONPATH independentemente do diretório de execução
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.funds_platform import (
    CorporateEvent,
    FundBreakType,
    FundCategory,
    FundQuotaPosition,
    FundReconciliationBreak,
    FundReconciliationResult,
    FundsEventError,
    FundsPlatformEngine,
    FundsPlatformError,
    FundsReconciliationError,
    FundsValidationError,
    QuotaClassType,
    QuotaMovementType,
    QuotaTransaction,
)

FIXTURES_DIR = ROOT_DIR / "fixtures"


class TestFundsPlatform(unittest.TestCase):
    """Suíte de testes para a plataforma de registro e custódia de fundos B3."""

    def setUp(self) -> None:
        """Inicializa o motor de fundos antes de cada teste."""
        self.engine = FundsPlatformEngine()
        self.fixture_path = FIXTURES_DIR / "fund_quota_statement.json"

    # -----------------------------------------------------------------------
    # 1. Teste de Apuração de Rendimentos de FII com Crédito Exato por Cota
    # -----------------------------------------------------------------------

    def test_fii_income_distribution_exact_credit(self) -> None:
        """Valida que a apuração de rendimentos de FII credita o valor monetário exato

        sem alterar a quantidade de cotas nem o NAV patrimonial.
        """
        # Cadastro de posições do FII HGLG11 (CSHG Logística)
        fund_id = "11.222.333/0001-44"
        initial_nav = Decimal("165.25000000")

        pos_1 = FundQuotaPosition(
            fund_id=fund_id,
            fund_name="CSHG LOGISTICA FII",
            category=FundCategory.FII,
            quota_class="UNICA",
            custody_account="105001",
            investor_id="123.456.789-00",
            investor_name="CARLOS EDUARDO PEREIRA",
            position_date=date(2026, 10, 31),
            quota_quantity=Decimal("10000.00000000"),
            quota_nav=initial_nav,
            ticker="HGLG11",
            isin="BRHGLGCTF001",
        )
        pos_2 = FundQuotaPosition(
            fund_id=fund_id,
            fund_name="CSHG LOGISTICA FII",
            category=FundCategory.FII,
            quota_class="UNICA",
            custody_account="105002",
            investor_id="987.654.321-99",
            investor_name="MARIA HELENA SANTOS",
            position_date=date(2026, 10, 31),
            quota_quantity=Decimal("25000.00000000"),
            quota_nav=initial_nav,
            blocked_quantity=Decimal("5000.00000000"),
            ticker="HGLG11",
            isin="BRHGLGCTF001",
        )
        self.engine.register_position(pos_1)
        self.engine.register_position(pos_2)

        # Evento corporativo: rendimento mensal de R$ 1,10 por cota (isento PF)
        rate = Decimal("1.10000000")
        event = CorporateEvent(
            event_id="EVT-HGLG11-202610-01",
            fund_id=fund_id,
            event_type=QuotaMovementType.INCOME_DISTRIBUTION,
            record_date=date(2026, 10, 31),
            ex_date=date(2026, 11, 1),
            payment_date=date(2026, 11, 14),
            rate_per_quota=rate,
            tax_rate=Decimal("0.0000"),
            description="Rendimentos mensais de locacao de galpoes logisticos",
        )

        transactions = self.engine.distribute_income(event)
        self.assertEqual(len(transactions), 2)

        tx_1 = next(t for t in transactions if t.custody_account == "105001")
        tx_2 = next(t for t in transactions if t.custody_account == "105002")

        # Crédito exato conta 1: 10.000 cotas * R$ 1,10 = R$ 11.000,00
        self.assertEqual(tx_1.quantity, Decimal("10000.00000000"))
        self.assertEqual(tx_1.unit_price, rate)
        self.assertEqual(tx_1.gross_amount, Decimal("11000.00"))
        self.assertEqual(tx_1.tax_amount, Decimal("0.00"))
        self.assertEqual(tx_1.net_amount, Decimal("11000.00"))
        self.assertEqual(tx_1.movement_type, QuotaMovementType.INCOME_DISTRIBUTION)

        # Crédito exato conta 2: 25.000 cotas * R$ 1,10 = R$ 27.500,00
        # (mesmo com 5.000 bloqueadas para margem, rendimentos são devidos sobre a titularidade integral)
        self.assertEqual(tx_2.quantity, Decimal("25000.00000000"))
        self.assertEqual(tx_2.unit_price, rate)
        self.assertEqual(tx_2.gross_amount, Decimal("27500.00"))
        self.assertEqual(tx_2.tax_amount, Decimal("0.00"))
        self.assertEqual(tx_2.net_amount, Decimal("27500.00"))

        # Invariante: Quantidades de cotas e NAV permanecem estritamente inalterados
        p1_after = self.engine.get_position(fund_id, "105001")
        p2_after = self.engine.get_position(fund_id, "105002")
        assert p1_after is not None and p2_after is not None

        self.assertEqual(p1_after.quota_quantity, Decimal("10000.00000000"))
        self.assertEqual(p1_after.quota_nav, initial_nav)
        self.assertEqual(p1_after.total_equity_value, Decimal("1652500.00"))

        self.assertEqual(p2_after.quota_quantity, Decimal("25000.00000000"))
        self.assertEqual(p2_after.quota_nav, initial_nav)
        self.assertEqual(p2_after.total_equity_value, Decimal("4131250.00"))

    def test_fii_income_distribution_with_withholding_tax(self) -> None:
        """Valida retenção de Imposto de Renda na fonte em proventos de cotistas tributáveis."""
        fund_id = "11.222.333/0001-44"
        pos = FundQuotaPosition(
            fund_id=fund_id,
            fund_name="CSHG LOGISTICA FII",
            category=FundCategory.FII,
            quota_class="UNICA",
            custody_account="105099",
            investor_id="99.888.777/0001-66",
            position_date=date(2026, 10, 31),
            quota_quantity=Decimal("10000.00000000"),
            quota_nav=Decimal("165.25000000"),
        )
        self.engine.register_position(pos)

        event = CorporateEvent(
            event_id="EVT-HGLG11-PJ-01",
            fund_id=fund_id,
            event_type=QuotaMovementType.INCOME_DISTRIBUTION,
            record_date=date(2026, 10, 31),
            ex_date=date(2026, 11, 1),
            payment_date=date(2026, 11, 14),
            rate_per_quota=Decimal("1.00000000"),
            tax_rate=Decimal("0.1500"),  # 15% IR PJ
        )

        txs = self.engine.distribute_income(event)
        self.assertEqual(len(txs), 1)
        tx = txs[0]
        self.assertEqual(tx.gross_amount, Decimal("10000.00"))
        self.assertEqual(tx.tax_amount, Decimal("1500.00"))
        self.assertEqual(tx.net_amount, Decimal("8500.00"))

    # -----------------------------------------------------------------------
    # 2. Teste de Amortização de Cotas de FIDC Reduzindo o Valor Patrimonial
    # -----------------------------------------------------------------------

    def test_fidc_principal_amortization_reduces_nav(self) -> None:
        """Valida que a amortização de principal de cotas de FIDC:

        1. Credita o valor financeiro correspondente aos cotistas.
        2. Reduz o NAV unitário da cota (NAV_novo = NAV_antigo - taxa_amortizacao).
        3. Preserva a quantidade de cotas em circulação.
        4. Reduz o valor patrimonial total da posição.
        """
        fund_id = "34.567.890/0001-12"
        initial_nav = Decimal("1000.00000000")
        amortization_rate = Decimal("75.50000000")
        expected_nav = Decimal("924.50000000")

        # Posições de cotas Seniores
        pos_senior_1 = FundQuotaPosition(
            fund_id=fund_id,
            fund_name="CREDIT FLOW FIDC MULTISSETORIAL",
            category=FundCategory.FIDC,
            quota_class="SENIOR",
            custody_account="105003",
            investor_id="11.222.333/0001-88",
            investor_name="ALPHA CAPITAL ASSET MANAGEMENT LTDA",
            position_date=date(2026, 10, 25),
            quota_quantity=Decimal("5000.00000000"),
            quota_nav=initial_nav,
        )
        pos_senior_2 = FundQuotaPosition(
            fund_id=fund_id,
            fund_name="CREDIT FLOW FIDC MULTISSETORIAL",
            category=FundCategory.FIDC,
            quota_class="SENIOR",
            custody_account="105004",
            investor_id="22.333.444/0001-77",
            investor_name="BETA PREVIDENCIA PRIVADA S.A.",
            position_date=date(2026, 10, 25),
            quota_quantity=Decimal("12000.00000000"),
            quota_nav=initial_nav,
        )
        # Cota Subordinada (não deve ser afetada pela amortização exclusiva da classe sênior)
        pos_sub = FundQuotaPosition(
            fund_id=fund_id,
            fund_name="CREDIT FLOW FIDC MULTISSETORIAL",
            category=FundCategory.FIDC,
            quota_class="SUBORDINATED",
            custody_account="105005",
            investor_id="33.444.555/0001-66",
            position_date=date(2026, 10, 25),
            quota_quantity=Decimal("3000.00000000"),
            quota_nav=Decimal("1050.00000000"),
        )

        self.engine.register_position(pos_senior_1)
        self.engine.register_position(pos_senior_2)
        self.engine.register_position(pos_sub)

        # Evento de amortização programada da classe sênior com 15% de IR
        event = CorporateEvent(
            event_id="EVT-CFCR11-202610-01",
            fund_id=fund_id,
            event_type=QuotaMovementType.AMORTIZATION,
            record_date=date(2026, 10, 25),
            ex_date=date(2026, 10, 26),
            payment_date=date(2026, 10, 31),
            rate_per_quota=amortization_rate,
            tax_rate=Decimal("0.1500"),
            quota_class="SENIOR",
            description="Amortizacao periodica programada de principal da cota senior",
        )

        txs, resulting_nav = self.engine.amortize_principal(event)
        self.assertEqual(len(txs), 2)
        self.assertEqual(resulting_nav, expected_nav)

        tx_1 = next(t for t in txs if t.custody_account == "105003")
        tx_2 = next(t for t in txs if t.custody_account == "105004")

        # Conta 105003: 5.000 cotas * 75.50 = R$ 377.500,00 bruto
        # IR 15% = R$ 56.625,00 | Líquido = R$ 320.875,00
        self.assertEqual(tx_1.quantity, Decimal("5000.00000000"))
        self.assertEqual(tx_1.gross_amount, Decimal("377500.00"))
        self.assertEqual(tx_1.tax_amount, Decimal("56625.00"))
        self.assertEqual(tx_1.net_amount, Decimal("320875.00"))

        # Conta 105004: 12.000 cotas * 75.50 = R$ 906.000,00 bruto
        # IR 15% = R$ 135.900,00 | Líquido = R$ 770.100,00
        self.assertEqual(tx_2.quantity, Decimal("12000.00000000"))
        self.assertEqual(tx_2.gross_amount, Decimal("906000.00"))
        self.assertEqual(tx_2.tax_amount, Decimal("135900.00"))
        self.assertEqual(tx_2.net_amount, Decimal("770100.00"))

        # Verificação da redução do NAV no livro interno
        p1 = self.engine.get_position(fund_id, "105003", "SENIOR")
        p2 = self.engine.get_position(fund_id, "105004", "SENIOR")
        psub = self.engine.get_position(fund_id, "105005", "SUBORDINATED")

        assert p1 is not None and p2 is not None and psub is not None

        # NAV unitário reduzido exatamente para 924.50
        self.assertEqual(p1.quota_nav, expected_nav)
        self.assertEqual(p2.quota_nav, expected_nav)

        # Quantidade de cotas mantida intacta
        self.assertEqual(p1.quota_quantity, Decimal("5000.00000000"))
        self.assertEqual(p2.quota_quantity, Decimal("12000.00000000"))

        # Patrimônio recalculado consistente com a nova cota
        self.assertEqual(p1.total_equity_value, Decimal("4622500.00"))
        self.assertEqual(p2.total_equity_value, Decimal("11094000.00"))

        # Cota subordinada permaneceu inalterada
        self.assertEqual(psub.quota_nav, Decimal("1050.00000000"))
        self.assertEqual(psub.total_equity_value, Decimal("3150000.00"))

    def test_amortization_exceeding_nav_raises_validation_error(self) -> None:
        """Valida que tentativa de amortizar valor superior ao NAV unitário é rejeitada."""
        fund_id = "34.567.890/0001-12"
        pos = FundQuotaPosition(
            fund_id=fund_id,
            fund_name="FIDC TESTE",
            category=FundCategory.FIDC,
            quota_class="SENIOR",
            custody_account="105003",
            investor_id="11.222.333/0001-88",
            position_date=date(2026, 10, 25),
            quota_quantity=Decimal("100.00000000"),
            quota_nav=Decimal("50.00000000"),
        )
        self.engine.register_position(pos)

        event = CorporateEvent(
            event_id="EVT-ERR-01",
            fund_id=fund_id,
            event_type=QuotaMovementType.AMORTIZATION,
            record_date=date(2026, 10, 25),
            ex_date=date(2026, 10, 26),
            payment_date=date(2026, 10, 31),
            rate_per_quota=Decimal("60.00000000"),  # 60 > 50 NAV
        )

        with self.assertRaises(FundsValidationError) as ctx:
            self.engine.amortize_principal(event)
        self.assertIn("excede o NAV da cota", str(ctx.exception))

    # -----------------------------------------------------------------------
    # 3. Teste de Conciliação do Extrato de Cotistas Contra o Livro Interno
    # -----------------------------------------------------------------------

    def test_reconciliation_clean_statement_against_internal_ledger(self) -> None:
        """Valida conciliação 100% perfeita entre o extrato oficial B3 e o livro interno."""
        # Carrega fixture oficial
        statement_positions = self.engine.load_statement_from_json(self.fixture_path)
        self.assertEqual(len(statement_positions), 7)

        # Popula o livro interno espelhando o extrato oficial
        for pos in statement_positions:
            self.engine.register_position(pos)

        # Executa conciliação determinística
        result = self.engine.reconcile_statement(statement_positions)

        self.assertTrue(result.is_clean)
        self.assertEqual(result.total_positions_checked, 7)
        self.assertEqual(result.matched_positions, 7)
        self.assertEqual(result.unmatched_positions, 0)
        self.assertEqual(len(result.breaks), 0)
        self.assertIn("CONCILIADO 100% OK", result.summary())

    def test_reconciliation_breaks_detection(self) -> None:
        """Valida detecção precisa e categorização de todas as quebras de conciliação."""
        # Carrega posições base da fixture
        statement_positions = self.engine.load_statement_from_json(self.fixture_path)

        # Cadastra posições no livro interno, inserindo divergências deliberadas:
        for pos in statement_positions:
            # 1. Divergência de quantidade na conta 105001 (FII HGLG11)
            if pos.custody_account == "105001":
                internal_pos = FundQuotaPosition(
                    fund_id=pos.fund_id,
                    fund_name=pos.fund_name,
                    category=pos.category,
                    quota_class=pos.quota_class,
                    custody_account=pos.custody_account,
                    investor_id=pos.investor_id,
                    position_date=pos.position_date,
                    quota_quantity=Decimal("9500.00000000"),  # Extrato tem 10.000 (diff 500)
                    quota_nav=pos.quota_nav,
                )
                self.engine.register_position(internal_pos)

            # 2. Divergência de NAV na conta 105003 (FIDC CFCR11)
            elif pos.custody_account == "105003":
                internal_pos = FundQuotaPosition(
                    fund_id=pos.fund_id,
                    fund_name=pos.fund_name,
                    category=pos.category,
                    quota_class=pos.quota_class,
                    custody_account=pos.custody_account,
                    investor_id=pos.investor_id,
                    position_date=pos.position_date,
                    quota_quantity=pos.quota_quantity,
                    quota_nav=Decimal("920.00000000"),  # Extrato tem 924.50 (diff 4.50)
                )
                self.engine.register_position(internal_pos)

            # 3. Omite a conta 105007 (ETF BOVA11) no livro interno -> MISSING_IN_INTERNAL
            elif pos.custody_account == "105007":
                continue

            # Demais contas espelhadas identicamente
            else:
                self.engine.register_position(pos)

        # 4. Adiciona posição fantasma interna não presente no extrato -> MISSING_IN_STATEMENT
        ghost_pos = FundQuotaPosition(
            fund_id="99.999.999/0001-99",
            fund_name="FUNDO FANTASMA INTERNO",
            category=FundCategory.FIDC,
            quota_class="UNICA",
            custody_account="105999",
            investor_id="000.111.222-33",
            position_date=date(2026, 11, 3),
            quota_quantity=Decimal("1500.00000000"),
            quota_nav=Decimal("100.00000000"),
        )
        self.engine.register_position(ghost_pos)

        # Executa conciliação
        result = self.engine.reconcile_statement(statement_positions)

        self.assertFalse(result.is_clean)
        self.assertGreater(len(result.breaks), 0)

        break_types = [b.break_type for b in result.breaks]

        # Verifica presença de todas as modalidades de divergência
        self.assertIn(FundBreakType.QUANTITY_MISMATCH, break_types)
        self.assertIn(FundBreakType.NAV_MISMATCH, break_types)
        self.assertIn(FundBreakType.MISSING_IN_INTERNAL, break_types)
        self.assertIn(FundBreakType.MISSING_IN_STATEMENT, break_types)

        # Valida quebra de quantidade
        qty_break = next(b for b in result.breaks if b.break_type == FundBreakType.QUANTITY_MISMATCH)
        self.assertEqual(qty_break.custody_account, "105001")
        self.assertEqual(qty_break.internal_value, Decimal("9500.00000000"))
        self.assertEqual(qty_break.statement_value, Decimal("10000.00000000"))
        self.assertEqual(qty_break.diff, Decimal("-500.00000000"))

        # Valida quebra de NAV
        nav_break = next(b for b in result.breaks if b.break_type == FundBreakType.NAV_MISMATCH)
        self.assertEqual(nav_break.custody_account, "105003")
        self.assertEqual(nav_break.internal_value, Decimal("920.00000000"))
        self.assertEqual(nav_break.statement_value, Decimal("924.50000000"))

        # Valida posição faltante no livro interno (ETF BOVA11 conta 105007)
        missing_int = next(b for b in result.breaks if b.break_type == FundBreakType.MISSING_IN_INTERNAL)
        self.assertEqual(missing_int.custody_account, "105007")

        # Valida posição fantasma interna faltante no extrato
        missing_stmt = next(b for b in result.breaks if b.break_type == FundBreakType.MISSING_IN_STATEMENT)
        self.assertEqual(missing_stmt.custody_account, "105999")

        summary = result.summary()
        self.assertIn("DIVERGÊNCIAS DETECTADAS", summary)

    # -----------------------------------------------------------------------
    # 4. Testes Complementares de Ciclo de Vida de Cotas
    # -----------------------------------------------------------------------

    def test_paid_in_capital_and_redemption_lifecycle(self) -> None:
        """Valida integralização primária de cotas seguida de resgate parcial."""
        fund_id = "41.123.456/0001-78"
        custody_account = "105010"
        investor_id = "555.666.777-88"

        # 1. Integralização de 10.000 cotas a R$ 10,00
        tx_integ = self.engine.process_paid_in_capital(
            fund_id=fund_id,
            fund_name="RIZOMA AGRO FIAGRO",
            category=FundCategory.FIAGRO,
            custody_account=custody_account,
            investor_id=investor_id,
            quantity=Decimal("10000.00000000"),
            issue_price=Decimal("10.00000000"),
            trade_date=date(2026, 11, 1),
            settlement_date=date(2026, 11, 3),
        )

        self.assertEqual(tx_integ.movement_type, QuotaMovementType.PAID_IN_CAPITAL)
        self.assertEqual(tx_integ.gross_amount, Decimal("100000.00"))

        pos = self.engine.get_position(fund_id, custody_account)
        assert pos is not None
        self.assertEqual(pos.quota_quantity, Decimal("10000.00000000"))
        self.assertEqual(pos.available_quantity, Decimal("10000.00000000"))
        self.assertEqual(pos.total_equity_value, Decimal("100000.00"))

        # 2. Resgate parcial de 3.000 cotas
        tx_resg = self.engine.process_redemption(
            fund_id=fund_id,
            custody_account=custody_account,
            quantity=Decimal("3000.00000000"),
            trade_date=date(2026, 11, 4),
            settlement_date=date(2026, 11, 5),
        )

        self.assertEqual(tx_resg.movement_type, QuotaMovementType.REDEMPTION)
        self.assertEqual(tx_resg.gross_amount, Decimal("30000.00"))

        pos_after = self.engine.get_position(fund_id, custody_account)
        assert pos_after is not None
        self.assertEqual(pos_after.quota_quantity, Decimal("7000.00000000"))
        self.assertEqual(pos_after.total_equity_value, Decimal("70000.00"))

    def test_transfer_stvm_conserves_total_quotas(self) -> None:
        """Valida transferência de cotas entre contas de custódia (portabilidade STVM)."""
        fund_id = "11.222.333/0001-44"
        acc_origin = "105001"
        acc_dest = "105002"

        pos_orig = FundQuotaPosition(
            fund_id=fund_id,
            fund_name="CSHG LOGISTICA FII",
            category=FundCategory.FII,
            quota_class="UNICA",
            custody_account=acc_origin,
            investor_id="111.222.333-44",
            position_date=date(2026, 11, 1),
            quota_quantity=Decimal("5000.00000000"),
            quota_nav=Decimal("165.25000000"),
        )
        self.engine.register_position(pos_orig)

        tx_out, tx_in = self.engine.process_transfer(
            fund_id=fund_id,
            quota_class="UNICA",
            from_account=acc_origin,
            to_account=acc_dest,
            to_investor_id="999.888.777-66",
            to_investor_name="INVESTIDOR DESTINO",
            quantity=Decimal("2000.00000000"),
            transfer_date=date(2026, 11, 3),
        )

        self.assertEqual(tx_out.movement_type, QuotaMovementType.TRANSFER_OUT)
        self.assertEqual(tx_in.movement_type, QuotaMovementType.TRANSFER_IN)
        self.assertEqual(tx_out.quantity, Decimal("2000.00000000"))
        self.assertEqual(tx_in.quantity, Decimal("2000.00000000"))

        orig_after = self.engine.get_position(fund_id, acc_origin)
        dest_after = self.engine.get_position(fund_id, acc_dest)
        assert orig_after is not None and dest_after is not None

        # Conservação da quantidade: 3.000 + 2.000 = 5.000
        self.assertEqual(orig_after.quota_quantity, Decimal("3000.00000000"))
        self.assertEqual(dest_after.quota_quantity, Decimal("2000.00000000"))

    def test_validation_errors_on_invalid_position_data(self) -> None:
        """Valida disparos de exceção para dados cadastrais inconsistentes."""
        fund_id = "11.222.333/0001-44"

        # Quantidade negativa
        with self.assertRaises(FundsValidationError):
            FundQuotaPosition(
                fund_id=fund_id,
                fund_name="FII TESTE",
                category=FundCategory.FII,
                quota_class="UNICA",
                custody_account="105001",
                investor_id="123",
                position_date=date.today(),
                quota_quantity=Decimal("-10.00000000"),
                quota_nav=Decimal("100.00000000"),
            )

        # Quantidade bloqueada maior que total
        with self.assertRaises(FundsValidationError):
            FundQuotaPosition(
                fund_id=fund_id,
                fund_name="FII TESTE",
                category=FundCategory.FII,
                quota_class="UNICA",
                custody_account="105001",
                investor_id="123",
                position_date=date.today(),
                quota_quantity=Decimal("100.00000000"),
                quota_nav=Decimal("100.00000000"),
                blocked_quantity=Decimal("150.00000000"),
            )


if __name__ == "__main__":
    unittest.main()
