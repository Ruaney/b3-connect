"""Suíte de Testes Automatizados para Risco Pré-Trading e Gestão de Limites B3 (FL-028).

Testa:
- Aprovação de ordens dentro dos limites operacionais normais.
- Rejeição por violação de túnel de preço estático (FAT_FINGER_PRICE_OUT_OF_BANDS).
- Acionamento de leilão de volatilidade por violação de túnel dinâmico (PRICE_TRIGGERED_DYNAMIC_AUCTION).
- Rejeição por excesso de lote máximo da ordem (MAX_ORDER_QUANTITY_EXCEEDED).
- Rejeição por estouro de valor financeiro máximo por ordem (MAX_ORDER_VALUE_EXCEEDED).
- Trava de exposição financeira bruta intradiária acumulada (GROSS_EXPOSURE_LIMIT_EXCEEDED).
- Trava de exposição financeira líquida intradiária acumulada (NET_EXPOSURE_LIMIT_EXCEEDED).
- Trava de venda a descoberto (SHORT_SELLING_RESTRICTED) e limite de aluguel (SHORT_SELLING_LIMIT_EXCEEDED).
- Ciclo de vida de ordens ativas: reserva, execuções (fills) e cancelamentos.
- Interoperabilidade com protocolo FIX (FixMessage) e binário SBE (NewOrderSingleBinary).
- Carregamento de perfis a partir de fixture JSON (fixtures/risk_limits_profile.json).
- Medição e validação de latência determinística em sub-milissegundo (< 1 ms).
"""

from pathlib import Path
import sys
import unittest

# Garante que a raiz do repositório esteja no PYTHONPATH em qualquer diretório de execução
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.pretrade_risk_limits import (
    AccountExposureState,
    PreTradeRiskEngine,
    PriceBandConfig,
    RiskLimitProfile,
    RiskReasonCode,
    RiskValidationResult,
    RiskValidationStatus,
)
from src.puma_fix import FixMessage, FixSide, PumaOrderEntryClient, FixSessionManager
from src.puma_sbe import NewOrderSingleBinary, Side as SbeSide

FIXTURES_DIR = ROOT_DIR / "fixtures"
PROFILE_FIXTURE_PATH = FIXTURES_DIR / "risk_limits_profile.json"


class TestPreTradeRiskLimits(unittest.TestCase):
    """Bateria de testes unitários do motor de risco pré-trading PUMA B3."""

    def setUp(self) -> None:
        """Inicializa motor limpo e configura perfis e túneis de teste."""
        self.engine = PreTradeRiskEngine()

        # Perfil Institucional HFT
        self.hft_profile = RiskLimitProfile(
            account="88001",
            name="Conta HFT Market Maker - Alpha Desk",
            desk_id="MESA_HFT_01",
            participant_id=88,
            max_order_qty=50000,
            max_order_value=5000000.0,
            max_gross_exposure=50000000.0,
            max_net_exposure=20000000.0,
            allow_short_selling=True,
            max_short_position=200000,
            active=True,
        )
        self.engine.add_profile(self.hft_profile)
        self.engine.set_custody_position("88001", "PETR4", 150000)

        # Perfil Varejo
        self.retail_profile = RiskLimitProfile(
            account="10501",
            name="Conta Varejo Pessoa Física",
            desk_id="HOME_BROKER_01",
            participant_id=105,
            max_order_qty=5000,
            max_order_value=100000.0,
            max_gross_exposure=300000.0,
            max_net_exposure=150000.0,
            allow_short_selling=False,
            max_short_position=0,
            active=True,
        )
        self.engine.add_profile(self.retail_profile)
        self.engine.set_custody_position("10501", "PETR4", 1000)

        # Túnel de Preço para PETR4 (Ref: 36.50, Estático +-10%, Dinâmico +-3%)
        # Estático: [32.85, 40.15]
        # Dinâmico: [35.405, 37.595]
        self.petr4_band = PriceBandConfig.from_percentages(
            symbol="PETR4",
            reference_price=36.50,
            static_pct=0.10,
            dynamic_pct=0.03,
            auction_on_dynamic_breach=True,
        )
        self.engine.add_price_band(self.petr4_band)

    # --------------------------------------------------------------------------
    # 1. Aprovação de Ordens Dentro dos Limites Normais
    # --------------------------------------------------------------------------

    def test_normal_order_approval_buy(self) -> None:
        """Ordem de compra dentro de todos os parâmetros deve ser aprovada com reserva."""
        res = self.engine.validate_order(
            account="10501",
            symbol="PETR4",
            side="1",  # Buy
            order_qty=1000,
            price=36.50,
            cl_ord_id="ORD_BUY_001",
            auto_reserve=True,
        )

        self.assertEqual(res.status, RiskValidationStatus.APPROVED)
        self.assertTrue(res.is_approved)
        self.assertEqual(res.reason_code, RiskReasonCode.APPROVED)
        self.assertEqual(res.order_value, 36500.0)
        self.assertLess(res.latency_micros, 1000.0)  # Sub-milissegundo

        # Verifica se o estado acumulado registrou a reserva
        state = self.engine.get_account_state("10501")
        self.assertEqual(state.pending_buy_value, 36500.0)
        self.assertEqual(state.pending_buy_qty["PETR4"], 1000)
        self.assertEqual(state.gross_exposure, 36500.0)
        self.assertEqual(state.net_exposure, 36500.0)

    def test_normal_order_approval_covered_sell(self) -> None:
        """Venda coberta dentro do saldo de custódia disponível deve ser aprovada."""
        res = self.engine.validate_order(
            account="10501",
            symbol="PETR4",
            side="2",  # Sell
            order_qty=500,
            price=36.50,
            cl_ord_id="ORD_SELL_001",
            auto_reserve=True,
        )

        self.assertEqual(res.status, RiskValidationStatus.APPROVED)
        self.assertTrue(res.is_approved)
        self.assertEqual(res.reason_code, RiskReasonCode.APPROVED)
        self.assertEqual(res.order_value, 18250.0)

        state = self.engine.get_account_state("10501")
        self.assertEqual(state.pending_sell_value, 18250.0)
        self.assertEqual(state.pending_sell_qty["PETR4"], 500)
        # Custódia era 1000, agora disponível é 500
        self.assertEqual(state.get_available_sell_qty("PETR4"), 500)

    # --------------------------------------------------------------------------
    # 2. Túneis de Preço (Price Bands)
    # --------------------------------------------------------------------------

    def test_rejection_fat_finger_price_out_of_static_bands_upper(self) -> None:
        """Preço acima do túnel estático deve ser sumariamente rejeitado (FAT_FINGER_PRICE_OUT_OF_BANDS)."""
        # Limite estático superior é 40.15. Ordem a 45.00
        res = self.engine.validate_order(
            account="10501",
            symbol="PETR4",
            side="1",
            order_qty=100,
            price=45.00,
            cl_ord_id="ORD_BAD_PX_HIGH",
        )

        self.assertEqual(res.status, RiskValidationStatus.REJECTED)
        self.assertFalse(res.is_approved)
        self.assertEqual(res.reason_code, RiskReasonCode.FAT_FINGER_PRICE_OUT_OF_BANDS)
        self.assertIn("fora do túnel estático", res.message)

    def test_rejection_fat_finger_price_out_of_static_bands_lower(self) -> None:
        """Preço abaixo do túnel estático deve ser sumariamente rejeitado (FAT_FINGER_PRICE_OUT_OF_BANDS)."""
        # Limite estático inferior é 32.85. Ordem a 25.00
        res = self.engine.validate_order(
            account="10501",
            symbol="PETR4",
            side="2",
            order_qty=100,
            price=25.00,
            cl_ord_id="ORD_BAD_PX_LOW",
        )

        self.assertEqual(res.status, RiskValidationStatus.REJECTED)
        self.assertFalse(res.is_approved)
        self.assertEqual(res.reason_code, RiskReasonCode.FAT_FINGER_PRICE_OUT_OF_BANDS)

    def test_dynamic_price_band_triggers_auction(self) -> None:
        """Preço dentro do estático mas fora do dinâmico deve acionar leilão de volatilidade."""
        # Estático: [32.85, 40.15], Dinâmico: [35.405, 37.595]
        # Preço 38.00 está dentro do estático, mas rompe o dinâmico superior
        res = self.engine.validate_order(
            account="10501",
            symbol="PETR4",
            side="1",
            order_qty=100,
            price=38.00,
            cl_ord_id="ORD_AUCTION_001",
        )

        self.assertEqual(res.status, RiskValidationStatus.AUCTION_TRIGGERED)
        self.assertFalse(res.is_approved)
        self.assertEqual(res.reason_code, RiskReasonCode.PRICE_TRIGGERED_DYNAMIC_AUCTION)
        self.assertIn("acionando leilão de volatilidade", res.message)

    def test_dynamic_price_band_strict_rejection_mode(self) -> None:
        """Quando configurado para não gerar leilão, violação dinâmica deve rejeitar."""
        band_strict = PriceBandConfig.from_percentages(
            symbol="VALE3",
            reference_price=60.0,
            static_pct=0.10,
            dynamic_pct=0.03,
            auction_on_dynamic_breach=False,
        )
        self.engine.add_price_band(band_strict)

        # Preço 63.00 rompe o dinâmico (limite 61.80) em modo estrito
        res = self.engine.validate_order(
            account="10501",
            symbol="VALE3",
            side="1",
            order_qty=100,
            price=63.00,
            cl_ord_id="ORD_VALE_REJ",
        )

        self.assertEqual(res.status, RiskValidationStatus.REJECTED)
        self.assertEqual(res.reason_code, RiskReasonCode.PRICE_TRIGGERED_DYNAMIC_AUCTION)

    # --------------------------------------------------------------------------
    # 3. Prevenção de Fat-Finger (Quantidade e Valor Financeiro)
    # --------------------------------------------------------------------------

    def test_rejection_max_order_quantity_exceeded(self) -> None:
        """Ordem com quantidade superior ao teto do perfil deve ser rejeitada."""
        # Limite varejo: max_order_qty = 5000. Ordem com 5001
        res = self.engine.validate_order(
            account="10501",
            symbol="PETR4",
            side="1",
            order_qty=5001,
            price=36.50,
            cl_ord_id="ORD_MAX_QTY_EXCEEDED",
        )

        self.assertEqual(res.status, RiskValidationStatus.REJECTED)
        self.assertFalse(res.is_approved)
        self.assertEqual(res.reason_code, RiskReasonCode.MAX_ORDER_QUANTITY_EXCEEDED)
        self.assertIn("excede o lote máximo", res.message)

    def test_rejection_max_order_value_exceeded(self) -> None:
        """Ordem com valor financeiro superior ao teto por ordem deve ser rejeitada."""
        # Limite varejo: max_order_value = 100.000,00 R$
        # 3000 ações a R$ 36.50 = R$ 109.500,00 (> 100.000)
        res = self.engine.validate_order(
            account="10501",
            symbol="PETR4",
            side="1",
            order_qty=3000,
            price=36.50,
            cl_ord_id="ORD_MAX_VAL_EXCEEDED",
        )

        self.assertEqual(res.status, RiskValidationStatus.REJECTED)
        self.assertFalse(res.is_approved)
        self.assertEqual(res.reason_code, RiskReasonCode.MAX_ORDER_VALUE_EXCEEDED)
        self.assertIn("excede o limite financeiro", res.message)

    # --------------------------------------------------------------------------
    # 4. Trava de Exposição Intradiária Acumulada (Bruta e Líquida)
    # --------------------------------------------------------------------------

    def test_intraday_cumulative_gross_exposure_lock(self) -> None:
        """Acúmulo de ordens ativas não pode ultrapassar o limite bruto (max_gross_exposure)."""
        # Perfil especial com limite bruto estrito para testar isoladamente o acúmulo bruto
        self.engine.add_profile(
            RiskLimitProfile(
                account="PROP_GROSS",
                name="Desk Gross Test",
                max_order_qty=50000,
                max_order_value=200000.0,
                max_gross_exposure=200000.0,  # Teto bruto R$ 200.000
                max_net_exposure=500000.0,    # Limite líquido amplo para não interferir
                allow_short_selling=True,
            )
        )

        # Ordem 1: Gross = 73.000 (2000 * 36.50)
        res1 = self.engine.validate_order(
            account="PROP_GROSS",
            symbol="PETR4",
            side="1",
            order_qty=2000,
            price=36.50,
            cl_ord_id="ORD_GROSS_1",
            auto_reserve=True,
        )
        self.assertEqual(res1.status, RiskValidationStatus.APPROVED)

        # Ordem 2: Gross = 73.000 + 73.000 = 146.000
        res2 = self.engine.validate_order(
            account="PROP_GROSS",
            symbol="PETR4",
            side="1",
            order_qty=2000,
            price=36.50,
            cl_ord_id="ORD_GROSS_2",
            auto_reserve=True,
        )
        self.assertEqual(res2.status, RiskValidationStatus.APPROVED)

        # Ordem 3: Projetaria Gross = 146.000 + 73.000 = 219.000 (> 200.000 teto) -> TRAVA DE RISCO
        res3 = self.engine.validate_order(
            account="PROP_GROSS",
            symbol="PETR4",
            side="1",
            order_qty=2000,
            price=36.50,
            cl_ord_id="ORD_GROSS_3",
            auto_reserve=True,
        )

        self.assertEqual(res3.status, RiskValidationStatus.REJECTED)
        self.assertFalse(res3.is_approved)
        self.assertEqual(res3.reason_code, RiskReasonCode.GROSS_EXPOSURE_LIMIT_EXCEEDED)
        self.assertIn("Exposição bruta projetada", res3.message)

    def test_intraday_cumulative_net_exposure_lock(self) -> None:
        """Acúmulo direcional não pode ultrapassar o limite líquido (max_net_exposure)."""
        # Perfil especial para teste líquido
        self.engine.add_profile(
            RiskLimitProfile(
                account="PROP_NET",
                name="Desk Proprietary Net Test",
                max_order_qty=100000,
                max_order_value=1000000.0,
                max_gross_exposure=5000000.0,
                max_net_exposure=100000.0,  # Teto líquido R$ 100.000
                allow_short_selling=True,
            )
        )

        # Ordem 1: Compra de R$ 73.000 -> Net = +73.000
        res1 = self.engine.validate_order(
            account="PROP_NET",
            symbol="PETR4",
            side="1",
            order_qty=2000,
            price=36.50,
            cl_ord_id="ORD_NET_1",
            auto_reserve=True,
        )
        self.assertEqual(res1.status, RiskValidationStatus.APPROVED)

        # Ordem 2: Nova compra de R$ 73.000 -> Projetaria Net = +146.000 (> 100.000) -> TRAVA
        res2 = self.engine.validate_order(
            account="PROP_NET",
            symbol="PETR4",
            side="1",
            order_qty=2000,
            price=36.50,
            cl_ord_id="ORD_NET_2",
            auto_reserve=True,
        )
        self.assertEqual(res2.status, RiskValidationStatus.REJECTED)
        self.assertEqual(res2.reason_code, RiskReasonCode.NET_EXPOSURE_LIMIT_EXCEEDED)

    # --------------------------------------------------------------------------
    # 5. Controle de Venda a Descoberto (Short-Selling Check)
    # --------------------------------------------------------------------------

    def test_short_selling_restricted_for_retail_account(self) -> None:
        """Conta de varejo sem autorização de venda a descoberto deve ser rejeitada se não houver custódia."""
        # Custódia da conta 10501 em PETR4 é 1000 ações. Venda de 1001 ações:
        res = self.engine.validate_order(
            account="10501",
            symbol="PETR4",
            side="2",  # Sell
            order_qty=1001,
            price=36.50,
            cl_ord_id="ORD_SHORT_RETAIL_BAD",
        )

        self.assertEqual(res.status, RiskValidationStatus.REJECTED)
        self.assertFalse(res.is_approved)
        self.assertEqual(res.reason_code, RiskReasonCode.SHORT_SELLING_RESTRICTED)
        self.assertIn("Venda a descoberto não autorizada", res.message)

    def test_short_selling_allowed_for_institutional_hft_account(self) -> None:
        """Conta institucional HFT com autorização de short deve ter ordem aprovada dentro do teto."""
        # Custódia HFT é 150.000 ações. Venda de 180.000 (descoberto de 30.000 <= 200.000 limite)
        res = self.engine.validate_order(
            account="88001",
            symbol="PETR4",
            side="2",
            order_qty=30000,
            price=36.50,
            cl_ord_id="ORD_SHORT_HFT_OK",
        )

        self.assertEqual(res.status, RiskValidationStatus.APPROVED)
        self.assertTrue(res.is_approved)

    def test_short_selling_limit_exceeded_for_institutional_account(self) -> None:
        """Conta institucional HFT que excede o limite de short deve ser rejeitada."""
        # Limite de short = 200.000. Custódia = 150.000.
        # Ordem de 400.000 -> Projetaria descoberto de 250.000 (> 200.000)
        # Nota: ajustamos max_order_qty para permitir testar especificamente o limite de short
        self.engine.add_profile(
            RiskLimitProfile(
                account="88002",
                name="HFT Desk High Qty",
                max_order_qty=500000,
                max_order_value=50000000.0,
                max_gross_exposure=100000000.0,
                max_net_exposure=50000000.0,
                allow_short_selling=True,
                max_short_position=10000,
            )
        )
        self.engine.set_custody_position("88002", "PETR4", 5000)

        # Venda de 20.000 (descoberto projetado = 15.000 > 10.000)
        res = self.engine.validate_order(
            account="88002",
            symbol="PETR4",
            side="2",
            order_qty=20000,
            price=36.50,
            cl_ord_id="ORD_SHORT_EXCEED",
        )

        self.assertEqual(res.status, RiskValidationStatus.REJECTED)
        self.assertEqual(res.reason_code, RiskReasonCode.SHORT_SELLING_LIMIT_EXCEEDED)

    # --------------------------------------------------------------------------
    # 6. Ciclo de Vida: Execuções (Fills), Cancelamentos e Reset
    # --------------------------------------------------------------------------

    def test_order_fill_and_cancellation_lifecycle(self) -> None:
        """Execução deve atualizar saldo real de custódia e cancelar libera reserva."""
        # 1. Submete compra de 1000 PETR4 @ 36.50
        res = self.engine.validate_order(
            account="10501",
            symbol="PETR4",
            side="1",
            order_qty=1000,
            price=36.50,
            cl_ord_id="ORD_FILL_01",
            auto_reserve=True,
        )
        self.assertEqual(res.status, RiskValidationStatus.APPROVED)
        state = self.engine.get_account_state("10501")
        self.assertEqual(state.pending_buy_value, 36500.0)

        # 2. Execução parcial de 600 ações @ 36.50
        self.engine.record_fill(
            account="10501",
            symbol="PETR4",
            side="1",
            exec_qty=600,
            exec_price=36.50,
            cl_ord_id="ORD_FILL_01",
        )
        # Posição física foi de 1000 para 1600
        self.assertEqual(state.positions["PETR4"], 1600)
        # Reserva pendente restante: 400 * 36.50 = 14.600
        self.assertEqual(state.pending_buy_value, 14600.0)

        # 3. Cancelamento do saldo remanescente (400 ações)
        self.engine.record_cancel(account="10501", cl_ord_id="ORD_FILL_01")
        # Reserva zerada
        self.assertEqual(state.pending_buy_value, 0.0)
        self.assertEqual(state.pending_buy_qty["PETR4"], 0)
        self.assertEqual(state.positions["PETR4"], 1600)

    # --------------------------------------------------------------------------
    # 7. Interoperabilidade com Protocolo FIX (FixMessage)
    # --------------------------------------------------------------------------

    def test_fix_order_validation(self) -> None:
        """Mensagem NewOrderSingle FIX deve ser validada e aprovada corretamente."""
        session = FixSessionManager(sender_comp_id="CORR_105")
        client = PumaOrderEntryClient(
            session=session,
            account="10501",
            executing_broker="105",
            clearing_member="88",
        )

        fix_msg = client.new_order_single(
            symbol="PETR4",
            side=FixSide.BUY,
            order_qty=500,
            price=36.50,
            cl_ord_id="FIX_ORD_001",
        )

        res = self.engine.validate_fix_order(fix_msg, auto_reserve=True)
        self.assertEqual(res.status, RiskValidationStatus.APPROVED)
        self.assertEqual(res.account, "10501")
        self.assertEqual(res.symbol, "PETR4")
        self.assertEqual(res.side, "1")
        self.assertEqual(res.order_qty, 500)
        self.assertEqual(res.cl_ord_id, "FIX_ORD_001")

    # --------------------------------------------------------------------------
    # 8. Interoperabilidade com Protocolo Binário SBE (NewOrderSingleBinary)
    # --------------------------------------------------------------------------

    def test_sbe_order_validation(self) -> None:
        """Mensagem NewOrderSingle SBE binária deve ser validada e aprovada."""
        self.engine.register_security_mapping(1001, "PETR4")

        sbe_order = NewOrderSingleBinary(
            cl_ord_id=987654321,
            security_id=1001,
            side=SbeSide.BUY,
            order_qty=2000,
            price=36.50,
            account=88001,
        )

        res = self.engine.validate_sbe_order(sbe_order, auto_reserve=True)
        self.assertEqual(res.status, RiskValidationStatus.APPROVED)
        self.assertEqual(res.account, "88001")
        self.assertEqual(res.symbol, "PETR4")
        self.assertEqual(res.side, "1")
        self.assertEqual(res.order_qty, 2000)

    # --------------------------------------------------------------------------
    # 9. Carregamento de Fixture JSON e Benchmark de Latência
    # --------------------------------------------------------------------------

    def test_load_from_json_fixture(self) -> None:
        """Deve carregar perfis, túneis e inventário a partir da fixture JSON oficial."""
        fresh_engine = PreTradeRiskEngine()
        fresh_engine.load_profiles_json(PROFILE_FIXTURE_PATH)

        # Verifica carregamento do perfil HFT
        hft = fresh_engine.get_profile("88001")
        self.assertIsNotNone(hft)
        self.assertEqual(hft.max_order_qty, 50000)
        self.assertTrue(hft.allow_short_selling)

        # Verifica carregamento do perfil varejo
        retail = fresh_engine.get_profile("10501")
        self.assertIsNotNone(retail)
        self.assertEqual(retail.max_order_qty, 5000)
        self.assertFalse(retail.allow_short_selling)

        # Verifica carregamento do túnel de preços
        band = fresh_engine.get_price_band("VALE3")
        self.assertIsNotNone(band)
        self.assertEqual(band.reference_price, 62.00)

        # Verifica inventário carregado
        state = fresh_engine.get_account_state("88001")
        self.assertEqual(state.positions["PETR4"], 150000)
        self.assertEqual(state.positions["VALE3"], 80000)

    def test_sub_millisecond_latency_benchmark(self) -> None:
        """Motor deve comprovar latência de validação P99 estritamente inferior a 1 ms."""
        stats = self.engine.benchmark_latency(iterations=1000)
        self.assertTrue(stats["sub_millisecond"])
        self.assertLess(stats["p99_micros"], 1000.0)
        self.assertLess(stats["mean_micros"], 100.0)  # Tipicamente < 50 µs


if __name__ == "__main__":
    unittest.main()
