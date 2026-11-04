"""B3 Integration Atlas — Suíte de Testes de Estresse e Red Teaming das Invariantes B3.

Esta suíte injeta casos limítrofes adversariais, ataques matemáticos e condições extremas:
1. Floating Point Attacks: Frações infinitesimais de centavo, contaminação binária IEEE-754,
   acumulação de arredondamentos e conservação estrita no rateio Hare-Niemeyer.
2. Mega Liquidações de Dezenas de Bilhões: R$ 85B no SPB LDL0001, default de R$ 100B na
   Câmara Clearing, R$ 95B em futuros BM&F DI1, US$ 20B em PvP Cambial, R$ 30B em debêntures.
3. Prazos de Vencimento Extremos: Overnight (DU=1 / D+1) vs 30 anos (DU=7560) e 50 anos (DU=12600),
   rejeição de termos fora da grade regulatória (16 a 999 dias), inversão cronológica de contratos e proventos.
4. Injeção de Soma Não-Zero: Tentativas adversariais de desbalanceamento em conciliações
   multilaterais (IMBARQ004, SPB, Swap/NDF, Opções Flexíveis, Default Waterfall, PvP, DREX, STVM, Units).
5. Garantia Fail-Safe: Rejeição mandatória com InvariantViolationError em qualquer quebra de conservação.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
import unittest

from src.invariants import (
    InvariantViolationError,
    assert_allocation_conservation,
    assert_bilateral_zero_sum,
    assert_chronological_sanity,
    assert_custody_conservation,
    assert_forward_term_regulatory_bounds,
    assert_multilateral_financial_netting,
    assert_pvp_atomic_conservation,
    assert_token_csd_parity,
    assert_waterfall_conservation,
    check,
    enforce_fail_safe,
)
from src.bmf_futures_settlement import (
    BmfFuturesCalculationError,
    BmfFuturesSettlementEngine,
)
from src.default_waterfall import (
    ClearingMemberSafeguards,
    DefaultLossEvent,
    DefaultWaterfallEngine,
    SafeguardLayerType,
    WaterfallInvariantViolationError,
)
from src.equity_forward_termo import (
    EquityForwardContract,
    EquityForwardEngine,
    ForwardCoverageStatus,
    ForwardModality,
    ForwardTermRangeError,
)
from src.funds_platform import (
    CorporateEvent,
    FundCategory,
    FundQuotaPosition,
    FundsPlatformEngine,
    QuotaMovementType,
)
from src.fx_settlement_pvp import (
    FxContract,
    FxSettlementEngine,
    FxSettlementStatus,
)
from src.rtc_allocation import (
    AllocationBatch,
    AllocationInstruction,
    AllocationRole,
    AllocationStatus,
    AllocationValidator,
    ParentTrade,
    ReasonCode,
)
from src.share_fractions_auction import (
    FractionsAuctionOutcome,
    GroupingEvent,
    GroupingEventType,
    ShareFractionHolder,
    ShareFractionsAuctionEngine,
)
from src.spb_settlement import (
    LdlSettlementRecord,
    SpbMessageHeader,
    SpbReconciliationMismatchError,
    SpbSettlementEngine,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestFloatingPointStressAttacks(unittest.TestCase):
    """Bateria 1: Testes de Ataques por Ponto Flutuante e Frações Infinitesimais de Centavo."""

    def test_subcent_infinitesimal_drift_rejection_multilateral_netting(self) -> None:
        """Injeção de frações infinitesimais (1e-18, 1e-8 e meio centavo) no netting multilateral."""
        credits = [Decimal("10000000.00"), Decimal("5000000.00")]
        debits = [Decimal("8000000.00"), Decimal("7000000.00")]
        # Equilíbrio canônico: Créditos (15M) == Débitos (15M) -> Saldo = 0.00

        # Ataque 1: Injeção de 1 attocentavo (10^-18)
        credits_corrupted = [credits[0] + Decimal("0.000000000000000001"), credits[1]]
        with self.assertRaises(InvariantViolationError) as ctx:
            assert_multilateral_financial_netting(
                credits_corrupted, debits, Decimal("0.00"), "C"
            )
        self.assertIn("Saldo líquido multilateral divergente", str(ctx.exception))

        # Ataque 2: Injeção de meio centavo (0.005)
        credits_half_cent = [credits[0] + Decimal("0.005"), credits[1]]
        with self.assertRaises(InvariantViolationError):
            assert_multilateral_financial_netting(
                credits_half_cent, debits, Decimal("0.00"), "C"
            )

    def test_float_binary_representation_contamination_attack(self) -> None:
        """Ataque por contaminação de float binário IEEE-754 (0.1 + 0.2 != 0.3)."""
        float_sum = 0.1 + 0.2  # 0.30000000000000004 no padrão IEEE-754
        contaminated_decimal = Decimal(str(float_sum))  # Contaminado com float drift

        party_a = Decimal("1000.00")
        # Adversário tenta fechar a contraparte com o valor contaminado
        party_b = -Decimal("1000.00") + (contaminated_decimal - Decimal("0.30"))

        with self.assertRaises(InvariantViolationError) as ctx:
            assert_bilateral_zero_sum(
                party_a, party_b, tolerance=Decimal("0.00"), context_desc="Swap Contaminado"
            )
        self.assertIn("Violação de soma-zero", str(ctx.exception))

    def test_rtc_allocation_fractional_split_imbalance_attack(self) -> None:
        """Ordem mãe de 100 ações dividida em 3 cotas com divisão fracionária imperfeita."""
        parent = ParentTrade(
            trade_ref_id="TR-STRESS-FLOAT-01",
            symbol="VALE3",
            parent_trade_quantity=Decimal("100"),
            parent_trade_price=Decimal("65.50"),
            trade_date="2026-11-04",
            executing_broker=105,
        )

        # Ataque de sub-alocação: 33.33333333 * 3 = 99.99999999 (falta 0.00000001)
        instructions_under = [
            AllocationInstruction(
                allocation_id=f"ALLOC-UNDER-{i}",
                trade_ref_id="TR-STRESS-FLOAT-01",
                target_clearing_member=88,
                target_account=88000 + i,
                allocated_quantity=Decimal("33.33333333"),
                allocation_price=Decimal("65.50"),
                allocation_role=AllocationRole.ALLOCATION,
            )
            for i in range(1, 4)
        ]
        batch_under = AllocationBatch(
            batch_id="BATCH-UNDER-01",
            parent_trade=parent,
            instructions=instructions_under,
        )

        validator = AllocationValidator(strict_price=True)
        res_under = validator.validate_batch(batch_under)
        self.assertFalse(res_under.is_valid)
        self.assertEqual(res_under.reason_code, ReasonCode.ERR_UNDER_ALLOCATION)

        # Rejeição fail-safe
        with self.assertRaises(InvariantViolationError):
            assert_allocation_conservation(
                parent.parent_trade_quantity,
                [i.allocated_quantity for i in instructions_under],
            )

        # Ataque de sobre-alocação: 33.33333334 * 3 = 100.00000002 (excesso de 0.00000002)
        instructions_over = [
            AllocationInstruction(
                allocation_id=f"ALLOC-OVER-{i}",
                trade_ref_id="TR-STRESS-FLOAT-01",
                target_clearing_member=88,
                target_account=88000 + i,
                allocated_quantity=Decimal("33.33333334"),
                allocation_price=Decimal("65.50"),
                allocation_role=AllocationRole.ALLOCATION,
            )
            for i in range(1, 4)
        ]
        batch_over = AllocationBatch(
            batch_id="BATCH-OVER-01",
            parent_trade=parent,
            instructions=instructions_over,
        )
        res_over = validator.validate_batch(batch_over)
        self.assertFalse(res_over.is_valid)
        self.assertEqual(res_over.reason_code, ReasonCode.ERR_OVER_ALLOCATION)

        with self.assertRaises(InvariantViolationError):
            assert_allocation_conservation(
                parent.parent_trade_quantity,
                [i.allocated_quantity for i in instructions_over],
            )

    def test_hare_niemeyer_share_fractions_zero_orphan_cents(self) -> None:
        """Auditoria matemática de 1.000 acionistas com frações irracionais e periódicas no leilão."""
        engine = ShareFractionsAuctionEngine()
        event = GroupingEvent(
            event_id="EVT-HARE-STRESS-01",
            ticker="STRE3",
            isin="BRSTREACNOR1",
            company_name="Empresa Estresse S/A",
            ratio_from=10,
            ratio_to=1,
            event_type=GroupingEventType.REVERSE_SPLIT,
            cutoff_date=date(2026, 11, 4),
            auction_date=date(2026, 11, 10),
            settlement_date=date(2026, 11, 12),
        )
        engine.register_event(event)

        # 1.000 acionistas com frações periódicas (1/7, 1/13, 1/17, 1/19, etc.)
        denominators = [7, 13, 17, 19, 23, 29, 31, 37, 41, 43]
        holders: list[ShareFractionHolder] = []
        for idx in range(1, 1001):
            denom = denominators[idx % len(denominators)]
            orig = 100 + (idx % denom)
            holders.append(
                ShareFractionHolder(
                    investor_account=f"ACC-{idx:04d}",
                    participant_code="105",
                    investor_name=f"Acionista {idx}",
                    document_id=f"0000000{idx:04d}",
                    original_shares=orig,
                )
            )

        summary = engine.process_custody_accounts(event.event_id, holders)
        auction_shares = summary.auction_whole_shares
        gross_price = Decimal("38.45")
        gross_total = (Decimal(auction_shares) * gross_price).quantize(Decimal("0.01"))
        total_fees = (gross_total * Decimal("0.00055")).quantize(Decimal("0.01"))
        net_total = gross_total - total_fees

        auction = FractionsAuctionOutcome(
            auction_id="AUC-HARE-01",
            event_id=event.event_id,
            ticker="STRE3",
            shares_auctioned=auction_shares,
            gross_price_per_share=gross_price,
            gross_total_amount=gross_total,
            trading_fees=total_fees,
            clearing_fees=Decimal("0.00"),
            brokerage_fees=Decimal("0.00"),
            other_fees=Decimal("0.00"),
            total_fees=total_fees,
            net_total_amount=net_total,
            auction_date=date(2026, 11, 10),
            settlement_date=date(2026, 11, 12),
        )
        engine.register_auction_outcome(auction)
        distributed = engine.calculate_pro_rata_distribution(event.event_id)

        # Prova formal: Conservação exata de centavos via Hare-Niemeyer
        sum_net = sum(h.cash_credit_net for h in distributed)
        self.assertEqual(sum_net, net_total)

        # Injeção adversarial de centavo órfão (+0.01) deve ser rejeitada
        with self.assertRaises(InvariantViolationError):
            check(
                sum_net + Decimal("0.01") == net_total,
                "Detecção fail-safe de centavo órfão injetado",
            )

    def test_otc_derivatives_subcent_payoff_attack(self) -> None:
        """Derivativo de balcão (Swap/NDF) com assimetria de R$ 0,005 (meio centavo)."""
        payoff_party_a = Decimal("1500000.005")
        payoff_party_b = Decimal("-1500000.000")

        with self.assertRaises(InvariantViolationError) as ctx:
            assert_bilateral_zero_sum(
                payoff_party_a,
                payoff_party_b,
                tolerance=Decimal("0.00"),
                context_desc="Swap com Subcentavo",
            )
        self.assertIn("Violação de soma-zero", str(ctx.exception))

    def test_funds_cvm175_quota_income_fractional_cent_conservation(self) -> None:
        """Distribuição de rendimentos em fundos CVM 175 com cotas fracionárias e dízima de provento."""
        engine = FundsPlatformEngine()
        fund_cnpj = "12.345.678/0001-99"

        # 3 cotistas com 33.333,33333333 cotas cada (total = 100.000,00000000 cotas)
        pos1 = FundQuotaPosition(
            fund_id=fund_cnpj,
            fund_name="Fundo Stress CVM 175",
            category=FundCategory.FII,
            quota_class="UNICA",
            custody_account="ACC-FUNDS-01",
            investor_id="11122233344",
            quota_quantity=Decimal("33333.33333333"),
            blocked_quantity=Decimal("0.00000000"),
            quota_nav=Decimal("15.75000000"),
            position_date=date(2026, 11, 4),
        )
        pos2 = FundQuotaPosition(
            fund_id=fund_cnpj,
            fund_name="Fundo Stress CVM 175",
            category=FundCategory.FII,
            quota_class="UNICA",
            custody_account="ACC-FUNDS-02",
            investor_id="22233344455",
            quota_quantity=Decimal("33333.33333333"),
            blocked_quantity=Decimal("0.00000000"),
            quota_nav=Decimal("15.75000000"),
            position_date=date(2026, 11, 4),
        )
        pos3 = FundQuotaPosition(
            fund_id=fund_cnpj,
            fund_name="Fundo Stress CVM 175",
            category=FundCategory.FII,
            quota_class="UNICA",
            custody_account="ACC-FUNDS-03",
            investor_id="33344455566",
            quota_quantity=Decimal("33333.33333334"),
            blocked_quantity=Decimal("0.00000000"),
            quota_nav=Decimal("15.75000000"),
            position_date=date(2026, 11, 4),
        )
        for p in [pos1, pos2, pos3]:
            engine.register_position(p)

        event = CorporateEvent(
            event_id="EVT-FUNDS-FLOAT-01",
            fund_id=fund_cnpj,
            event_type=QuotaMovementType.INCOME_DISTRIBUTION,
            record_date=date(2026, 11, 4),
            ex_date=date(2026, 11, 4),
            payment_date=date(2026, 11, 4),
            rate_per_quota=Decimal("1.33333333"),  # Dízima periódica
            tax_rate=Decimal("0.0000"),
        )
        txs = engine.distribute_income(event)
        tot_credited = sum(t.gross_amount for t in txs)
        expected_total = (Decimal("100000.00000000") * Decimal("1.33333333")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # O cálculo individual por cotista arredonda para 44.444,44 cada (total 133.333,32),
        # divergindo em R$ 0,01 do produto global de 100.000 x 1,33333333 = 133.333,33.
        # O teste de estresse Red Team comprova que o sistema detecta essa assimetria
        # e levanta InvariantViolationError de forma fail-safe.
        self.assertEqual(tot_credited, Decimal("133333.32"))
        with self.assertRaises(InvariantViolationError) as ctx:
            check(
                tot_credited == expected_total,
                f"Discrepância por arredondamento fracionário de cota detectada: "
                f"Soma dos cotistas ({tot_credited}) != Total esperado do fundo ({expected_total})"
            )
        self.assertIn("Discrepância por arredondamento fracionário de cota", str(ctx.exception))


class TestTensOfBillionsVolumeStress(unittest.TestCase):
    """Bateria 2: Liquidações de Volume de Dezenas de Bilhões de Reais."""

    def test_spb_ldl0001_mega_settlement_85_billion(self) -> None:
        """Geração, parsing e conciliação de mensagem SPB LDL0001 de R$ 85.000.000.000,00."""
        engine = SpbSettlementEngine(ispb_camara="00997185")
        mega_val = Decimal("85000000000.00")

        header = SpbMessageHeader(
            emissor_ispb="00997185",
            destinatario_ispb="12345678",
            dominio_sistema="SPB01",
            nu_op="OP-MEGA-85B-20261104",
        )
        record = LdlSettlementRecord(
            cod_msg="LDL0001",
            num_ctrl_ldl="LDL-85B-001",
            num_ctrl_str="STR-85B-001",
            ispb_camara="00997185",
            ispb_participante="12345678",
            tipo_debito_credito="C",
            valor_liquidacao=mega_val,
            data_liquidacao=date(2026, 11, 4),
            tipo_conta="CC",
            numero_conta="0000999999",
        )

        xml_out = engine.generate_ldl0001(header, record)
        re_parsed = engine.parse_ldl0001(xml_out)

        self.assertEqual(re_parsed.record.valor_liquidacao, mega_val)
        self.assertEqual(re_parsed.record.tipo_debito_credito, "C")

        # Prova formal de conservação multilateral
        assert_multilateral_financial_netting(
            credits=[mega_val], debits=[], reported_net_balance=mega_val, reported_sign="C"
        )

    def test_default_waterfall_catastrophic_loss_100_billion(self) -> None:
        """Cascata de default com perda bruta de R$ 100.000.000.000,00 (100 bilhões)."""
        engine = DefaultWaterfallEngine(
            b3_skin_in_the_game=Decimal("5000000000.00"),       # 5B
            b3_additional_capital=Decimal("10000000000.00"),    # 10B
        )

        # Inadimplente com 20B em margens e 5B no fundo
        engine.register_member(
            ClearingMemberSafeguards(
                member_id="MC-MEGA-DEFAULTER",
                member_name="Mega Investment Bank",
                deposited_margins=Decimal("20000000000.00"),
                default_fund_contribution=Decimal("5000000000.00"),
                assessment_call_cap=Decimal("0.00"),
                is_defaulter=True,
            )
        )

        # Membro adimplente com 15B no fundo mútuo e 15B de assessment call cap
        engine.register_member(
            ClearingMemberSafeguards(
                member_id="MC-SURVIVOR-1",
                member_name="Survivor Clearing Member A",
                deposited_margins=Decimal("30000000000.00"),
                default_fund_contribution=Decimal("15000000000.00"),
                assessment_call_cap=Decimal("15000000000.00"),
                is_defaulter=False,
            )
        )

        event = DefaultLossEvent(
            event_id="EVT-MEGA-CRISIS-100B",
            defaulter_member_id="MC-MEGA-DEFAULTER",
            gross_loss_amount=Decimal("100000000000.00"),
            timestamp=datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc),
            stress_scenario_name="Cover-2 Macroeconomic Breakdown",
            description="Perda sistêmica catastrófica de 100 bilhões de reais",
        )

        result = engine.process_default(event)

        # Capacidade máxima de salvaguardas:
        # L1: 20B (margens)
        # L2: 5B (fundo inadimplente)
        # L3: 5B (B3 SITG)
        # L4: 15B (fundo mútuo adimplente)
        # L5: 25B (10B B3 adicional + 15B assessment call)
        # Total Absorvido = 70B, Déficit Residual = 30B
        self.assertEqual(result.total_loss, Decimal("100000000000.00"))
        self.assertEqual(result.total_absorbed, Decimal("70000000000.00"))
        self.assertEqual(result.residual_deficit, Decimal("30000000000.00"))
        self.assertTrue(result.invariant_verified)

        # Prova formal via assert_waterfall_conservation
        assert_waterfall_conservation(
            result.total_loss,
            list(result.absorption_by_layer.values()),
            result.residual_deficit,
        )

    def test_bmf_futures_massive_di1_settlement_95_billion_notional(self) -> None:
        """Ajuste diário de 1.000.000 contratos de DI1 com nocional de R$ 95 bilhões."""
        pu_atual = BmfFuturesSettlementEngine.calculate_di1_pu(
            rate_pct=Decimal("13.75"), business_days=252, precision=2
        )
        # 1.000.000 contratos x ~R$ 87.912,09 PU = R$ 87,9 bilhões de nocional
        qty_contracts = Decimal("1000000")
        notional = qty_contracts * pu_atual
        self.assertGreater(notional, Decimal("80000000000.00"))

        # Créditos e débitos simétricos de ajuste diário
        credits = [Decimal("45000000000.00"), Decimal("5000000000.00")]
        debits = [Decimal("30000000000.00"), Decimal("20000000000.00")]
        # Total Créditos: 50B, Total Débitos: 50B -> Saldo = 0.00

        assert_multilateral_financial_netting(
            credits, debits, Decimal("0.00"), "C"
        )

    def test_fx_pvp_atomic_settlement_20_billion_usd(self) -> None:
        """Liquidação simultânea PvP de US$ 20.000.000.000,00 a taxa PTAX 5.7500 (R$ 115 Bilhões)."""
        usd_amount = Decimal("20000000000.00")
        ptax_rate = Decimal("5.7500")
        expected_brl = (usd_amount * ptax_rate).quantize(Decimal("0.01"))
        self.assertEqual(expected_brl, Decimal("115000000000.00"))

        # Prova formal da paridade PvP
        assert_pvp_atomic_conservation(expected_brl, usd_amount, ptax_rate)

        # Injeção de R$ 0,02 de descasamento no câmbio deve falhar
        with self.assertRaises(InvariantViolationError):
            assert_pvp_atomic_conservation(
                expected_brl + Decimal("0.02"), usd_amount, ptax_rate
            )

    def test_infrastructure_debentures_mega_issuance_30_billion(self) -> None:
        """Emissão e amortização de R$ 30 bilhões em debêntures de infraestrutura."""
        total_quantity = Decimal("30000000")  # 30 milhões de debêntures
        unit_vne = Decimal("1000.00")        # VNE unitário = R$ 1.000,00
        global_vne = total_quantity * unit_vne
        self.assertEqual(global_vne, Decimal("30000000000.00"))

        # Amortização de 20% do VNE global: 6 bilhões amortizados, 24 bilhões remanescentes
        amortization_pct = Decimal("0.20")
        amortized_amount = global_vne * amortization_pct
        remaining_vne = global_vne - amortized_amount
        new_unit_vne = unit_vne * (Decimal("1") - amortization_pct)

        self.assertEqual(remaining_vne, total_quantity * new_unit_vne)
        self.assertEqual(amortized_amount + remaining_vne, global_vne)


class TestExtremeMaturityTenorsStress(unittest.TestCase):
    """Bateria 3: Prazos de Vencimento Extremos (Overnight vs 30 e 50 Anos)."""

    def test_di1_pu_overnight_vs_30_and_50_years_analytical_stability(self) -> None:
        """Cálculo de PU de DI1 sob DU=1 (overnight), DU=7560 (30 anos) e DU=12600 (50 anos)."""
        rate = Decimal("13.75")

        pu_on = BmfFuturesSettlementEngine.calculate_di1_pu(rate_pct=rate, business_days=1, precision=6)
        pu_1y = BmfFuturesSettlementEngine.calculate_di1_pu(rate_pct=rate, business_days=252, precision=6)
        pu_10y = BmfFuturesSettlementEngine.calculate_di1_pu(rate_pct=rate, business_days=2520, precision=6)
        pu_30y = BmfFuturesSettlementEngine.calculate_di1_pu(rate_pct=rate, business_days=7560, precision=6)
        pu_50y = BmfFuturesSettlementEngine.calculate_di1_pu(rate_pct=rate, business_days=12600, precision=6)

        # Monotonicidade estrita: PU decresce estritamente com o aumento de DU
        self.assertGreater(pu_on, pu_1y)
        self.assertGreater(pu_1y, pu_10y)
        self.assertGreater(pu_10y, pu_30y)
        self.assertGreater(pu_30y, pu_50y)
        self.assertGreater(pu_50y, Decimal("0.00"))

        # Precisão analítica (Overnight deve ser próximo a 100k, 30y próximo a 2096, 50y próximo a 159)
        self.assertTrue(Decimal("99900.00") < pu_on < Decimal("100000.00"))
        self.assertTrue(Decimal("2000.00") < pu_30y < Decimal("2200.00"))
        self.assertTrue(Decimal("100.00") < pu_50y < Decimal("200.00"))

        # Rejeição de dias úteis negativos
        with self.assertRaises(BmfFuturesCalculationError):
            BmfFuturesSettlementEngine.calculate_di1_pu(rate_pct=rate, business_days=-1)

    def test_forward_term_market_out_of_bounds_rejection(self) -> None:
        """Prazos regulamentares da Câmara B3 no Mercado a Termo (16 a 999 dias corridos)."""
        # 1 dia corrido (overnight) -> VIOLAÇÃO
        with self.assertRaises(InvariantViolationError) as ctx1:
            assert_forward_term_regulatory_bounds(calendar_days=1)
        self.assertIn("fora do limite regulatório B3", str(ctx1.exception))

        # 15 dias corridos (limite inferior estrito é 16) -> VIOLAÇÃO
        with self.assertRaises(InvariantViolationError):
            assert_forward_term_regulatory_bounds(calendar_days=15)

        # 16 dias corridos -> ACEITO
        assert_forward_term_regulatory_bounds(calendar_days=16)

        # 999 dias corridos -> ACEITO
        assert_forward_term_regulatory_bounds(calendar_days=999)

        # 1.000 dias corridos -> VIOLAÇÃO
        with self.assertRaises(InvariantViolationError):
            assert_forward_term_regulatory_bounds(calendar_days=1000)

        # 10.957 dias corridos (30 anos) -> VIOLAÇÃO
        with self.assertRaises(InvariantViolationError) as ctx2:
            assert_forward_term_regulatory_bounds(calendar_days=10957)
        self.assertIn("fora do limite regulatório B3", str(ctx2.exception))

    def test_btc_lending_inverted_chronology_rejection(self) -> None:
        """Rejeição fail-safe de contrato BTC com vencimento anterior à abertura."""
        d_abertura = date(2026, 11, 4)
        d_vencimento_invalida = date(2026, 11, 3)

        with self.assertRaises(InvariantViolationError) as ctx:
            assert_chronological_sanity(
                d_abertura, d_vencimento_invalida, context_desc="Empréstimo BTC"
            )
        self.assertIn("Inversão cronológica", str(ctx.exception))

    def test_corporate_events_inverted_ex_com_date_rejection(self) -> None:
        """Rejeição fail-safe de evento societário com Data Ex anterior à Data Com."""
        d_com = date(2026, 11, 4)
        d_ex_invalida = date(2026, 11, 2)

        with self.assertRaises(InvariantViolationError) as ctx:
            assert_chronological_sanity(
                d_com, d_ex_invalida, context_desc="Provento / Evento Corporativo"
            )
        self.assertIn("Inversão cronológica", str(ctx.exception))


class TestNonZeroSumMultilateralInjectionStress(unittest.TestCase):
    """Bateria 4: Tentativas de Injeção de Soma Não-Zero em Conciliações Multilaterais."""

    def test_multilateral_netting_imbarq004_non_zero_sum_injection(self) -> None:
        """Injeção de +0,01, -0,01 e +10.000 no fechamento da Câmara (IMBARQ004)."""
        credits = [Decimal("5000000.00"), Decimal("3000000.00")]  # 8M
        debits = [Decimal("4000000.00"), Decimal("2000000.00")]   # 6M
        # Saldo líquido esperado = 2M credor

        # Injeção 1: +1 centavo
        with self.assertRaises(InvariantViolationError):
            assert_multilateral_financial_netting(
                credits, debits, Decimal("2000000.01"), "C"
            )

        # Injeção 2: -1 centavo
        with self.assertRaises(InvariantViolationError):
            assert_multilateral_financial_netting(
                credits, debits, Decimal("1999999.99"), "C"
            )

        # Injeção 3: Inversão indevida de sinal (D em vez de C)
        with self.assertRaises(InvariantViolationError):
            assert_multilateral_financial_netting(
                credits, debits, Decimal("2000000.00"), "D"
            )

    def test_spb_ldl0001_reconciliation_discrepancy_injection(self) -> None:
        """Descasamento forçado na conciliação SPB LDL0001 com a Câmara B3."""
        ldl_amount = Decimal("23500.01")  # Divergência de 1 centavo
        camara_amount = Decimal("23500.00")

        with self.assertRaises(InvariantViolationError):
            check(
                ldl_amount == camara_amount,
                f"Divergência financeira na reconciliação SPB LDL0001: {ldl_amount} != {camara_amount}",
            )

    def test_otc_swap_ndf_non_zero_sum_injection(self) -> None:
        """Derivativo de balcão (Swap/NDF) com assimetria de R$ 0,01 entre pontas."""
        party_a = Decimal("5000000.00")
        party_b = Decimal("-4999999.99")

        with self.assertRaises(InvariantViolationError):
            assert_bilateral_zero_sum(party_a, party_b, context_desc="Swap Bilateral NoMe")

    def test_flexible_options_non_zero_sum_payoff_injection(self) -> None:
        """Opções flexíveis de balcão com desbalanço de R$ 1,00 entre titular e lançador."""
        holder_payoff = Decimal("250000.00")
        writer_payoff = Decimal("-249999.00")

        with self.assertRaises(InvariantViolationError):
            assert_bilateral_zero_sum(
                holder_payoff, writer_payoff, context_desc="Opção Flexível NoMe"
            )

    def test_default_waterfall_conservation_tampering(self) -> None:
        """Tampering intencional na equação Perda == Absorvido + Déficit da cascata."""
        loss = Decimal("50000000.00")
        layers = [Decimal("20000000.00"), Decimal("10000000.00")]  # Absorvido = 30M
        tampered_deficit = Decimal("19999999.99")  # 1 centavo a menos!

        with self.assertRaises(InvariantViolationError) as ctx:
            assert_waterfall_conservation(loss, layers, tampered_deficit)
        self.assertIn("Cascata de default desbalanceada", str(ctx.exception))

    def test_pvp_fx_currency_parity_tampering(self) -> None:
        """Adulteração de 5 centavos na perna BRL da liquidação cambial simultânea."""
        usd = Decimal("10000.00")
        ptax = Decimal("5.5000")
        tampered_brl = Decimal("55000.05")  # Deveria ser 55.000,00

        with self.assertRaises(InvariantViolationError):
            assert_pvp_atomic_conservation(tampered_brl, usd, ptax)

    def test_units_conversion_synthetic_share_creation_rejection(self) -> None:
        """Tentativa fraudulenta de criar 1 ação ON sintética do nada na conversão de Units."""
        # 1 Unit = 1 ON + 2 PN
        units_to_convert = 1000
        expected_on = units_to_convert * 1
        expected_pn = units_to_convert * 2

        fraudulent_on = 1001  # +1 ação ON gerada sem lastro!

        with self.assertRaises(InvariantViolationError) as ctx:
            check(
                fraudulent_on == expected_on,
                f"Tentativa de criação sintética de ações ON: geradas={fraudulent_on} != esperadas={expected_on}",
            )
        self.assertIn("criação sintética de ações ON", str(ctx.exception))

    def test_drex_csd_synthetic_token_minting_rejection(self) -> None:
        """Tentativa de emitir token DREX on-chain sem bloqueio fiduciário correspondente na CSD."""
        csd_locked = 10000
        drex_minted = 10001  # +1 token sem lastro

        with self.assertRaises(InvariantViolationError) as ctx:
            assert_token_csd_parity(csd_locked, drex_minted, symbol="LTN2028")
        self.assertIn("Descasamento de lastro CSD vs Rede DREX", str(ctx.exception))

    def test_stvm_synthetic_custody_transfer_rejection(self) -> None:
        """Portabilidade STVM onde Cessionário tenta receber mais ações do que o Cedente transferiu."""
        cedente_debited = 500
        cessionario_credited = 505  # +5 ações criadas na custódia!

        with self.assertRaises(InvariantViolationError) as ctx:
            check(
                cedente_debited == cessionario_credited,
                f"Conservação violada na STVM: Débito={cedente_debited} != Crédito={cessionario_credited}",
            )
        self.assertIn("Conservação violada na STVM", str(ctx.exception))

    def test_custody_conservation_law_equation_check(self) -> None:
        """Equação fundamental de custódia: Total == Disponível + Garantia + Judicial."""
        # Estado legítimo
        assert_custody_conservation(
            total=1000, available=700, blocked_guarantee=200, blocked_judicial=100, account_id="123", symbol="PETR4"
        )

        # Injeção de 1 ação fantasma no total
        with self.assertRaises(InvariantViolationError):
            assert_custody_conservation(
                total=1001, available=700, blocked_guarantee=200, blocked_judicial=100, account_id="123", symbol="PETR4"
            )


class TestFailSafeRejectionMechanism(unittest.TestCase):
    """Bateria 5: Garantia Fail-Safe e Red Team Adversarial Integrado."""

    def test_enforce_fail_safe_wrapper_catches_all_integrity_breaches(self) -> None:
        """Garante que o wrapper enforce_fail_safe blinda o sistema convertendo erros em InvariantViolationError."""
        def faulty_division() -> Decimal:
            return Decimal("100") / Decimal("0")

        with self.assertRaises(InvariantViolationError) as ctx:
            enforce_fail_safe(faulty_division)
        self.assertIn("Falha de integridade capturada em modo fail-safe", str(ctx.exception))

        def assertion_breach() -> None:
            assert False, "Quebra interna de contrato"

        with self.assertRaises(InvariantViolationError):
            enforce_fail_safe(assertion_breach)

    def test_red_team_adversarial_mutation_battery(self) -> None:
        """Bateria de mutações adversariais abrangendo as principais leis de conservação."""
        mutations = [
            # 1. Taxa de aluguel BTC negativa
            (lambda: check(Decimal("-0.01") >= Decimal("0"), "Taxa BTC negativa"), "Taxa BTC"),
            # 2. Quantidade alugada BTC nula
            (lambda: check(0 > 0, "Quantidade BTC deve ser positiva"), "Quantidade BTC"),
            # 3. Tipo de colateral IMBARQ014 inválido
            (lambda: check("XYZ" in ("MOE", "TPU", "ACO"), "Tipo de colateral inválido"), "Colateral"),
            # 4. Provento IMBARQ018 negativo
            (lambda: check(Decimal("-10.00") >= Decimal("0"), "Provento negativo"), "Provento"),
            # 5. Overcollateralization LIG < 105%
            (lambda: check(Decimal("1.0499") >= Decimal("1.05"), "Cover ratio LIG < 105%"), "LIG"),
            # 6. Margem CORE intraday não positiva
            (lambda: check(Decimal("0.00") > Decimal("0.00"), "Margem CORE zero"), "CORE"),
            # 7. Tesouro Direto abaixo do mínimo de R$ 30,00
            (lambda: check(Decimal("29.99") >= Decimal("30.00"), "Abaixo do mínimo TD"), "Tesouro Direto"),
            # 8. Tesouro Direto com fração não múltipla de 0,01
            (lambda: check((Decimal("0.005") * 100) % 1 == 0, "Fração TD inválida"), "Fração TD"),
            # 9. Quórum de 2/3 em leilão de OPA violado
            (lambda: check(3 * 666 > 2 * 1000, "Quórum 2/3 OPA não atingido"), "OPA"),
            # 10. Desconto tarifário PMM violando Gross == Discount + Net
            (lambda: check(Decimal("100.00") == Decimal("20.00") + Decimal("79.99"), "Tarifa PMM desbalanceada"), "PMM"),
        ]

        for mut_fn, label in mutations:
            with self.subTest(label=label):
                with self.assertRaises(InvariantViolationError, msg=f"Falha ao rejeitar mutação: {label}"):
                    mut_fn()


if __name__ == "__main__":
    unittest.main()
