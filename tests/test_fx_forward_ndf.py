"""Bateria de testes unitários e verificação formal do Módulo de Câmbio a Termo e NDF (FL-074).

Valida conformidade estrita com:
- Resolução CMN nº 4.952/2021 e Resolução BCB nº 264/2022.
- Circular BCB nº 3.691/2013 (Mercado de Câmbio e Derivativos de Balcão).
- Invariante Formal de Conservação Bilateral Soma-Zero (LAW-061).
- Invariante de Paridade Cambial na Fixação PTAX (LAW-062).
- Paridade de Taxa a Termo (Covered Interest Parity) e Relação Cupom Limpo vs Sujo.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.fx_forward_ndf import (
    CompoundingMethod,
    ContractNotFoundError,
    ContractSide,
    ContractStateError,
    ContractStatus,
    Currency,
    CurrencyPair,
    DuplicateContractError,
    FixingSettlementLag,
    FxContractType,
    FxForwardContract,
    FxForwardNdfError,
    FxForwardNdfRegistry,
    FxForwardNdfValidationError,
    MarkToMarketResult,
    SettlementResult,
    calculate_cupom_limpo,
    calculate_cupom_sujo,
    calculate_theoretical_forward_rate,
    convert_cupom_limpo_to_sujo,
    convert_cupom_sujo_to_limpo,
    round_cents,
    round_fx_rate,
    round_rate,
)
from src.invariants import InvariantViolationError


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "fx_forward_ndf.json"
)


class TestFxForwardNdf(unittest.TestCase):
    """Suíte abrangente de testes determinísticos para Contratos a Termo de Moedas e NDF (FL-074)."""

    def setUp(self) -> None:
        self.registry = FxForwardNdfRegistry.from_json_file(FIXTURE_PATH)
        with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
            self.fixture_data = json.load(f)

    # =====================================================================
    # 1. TESTES DE CARREGAMENTO DE FIXTURE E REGISTRO
    # =====================================================================

    def test_fixture_loading_and_contract_counts(self) -> None:
        """Verifica carregamento completo e integridade dos 6 contratos da fixture."""
        self.assertEqual(len(self.registry.contracts), 6)
        report = self.registry.verify_invariants()
        self.assertTrue(report["all_invariants_hold"])
        self.assertEqual(len(report["violations"]), 0)
        self.assertEqual(report["aggregate_mtm_sum"], "0.00")
        self.assertEqual(report["aggregate_settlement_sum"], "0.00")

    def test_query_by_status_and_currency_pair(self) -> None:
        """Verifica filtragem por modalidade, par cambial e status."""
        ndf_contracts = self.registry.list_contracts(contract_type=FxContractType.NDF)
        self.assertEqual(len(ndf_contracts), 4)

        deliverable_contracts = self.registry.list_contracts(contract_type=FxContractType.DELIVERABLE)
        self.assertEqual(len(deliverable_contracts), 2)

        usd_contracts = self.registry.list_contracts(currency_pair=CurrencyPair.USD_BRL)
        self.assertEqual(len(usd_contracts), 4)

        eur_contracts = self.registry.list_contracts(currency_pair=CurrencyPair.EUR_BRL)
        self.assertEqual(len(eur_contracts), 2)

    def test_query_by_nome_protocol(self) -> None:
        """Verifica indexação e recuperação por protocolo NoMe B3."""
        contract = self.registry.get_by_nome_protocol("NOME202611040001001")
        self.assertEqual(contract.contract_id, "NDF-USD-BRL-001")
        self.assertEqual(contract.currency_pair, CurrencyPair.USD_BRL)

        with self.assertRaises(ContractNotFoundError):
            self.registry.get_by_nome_protocol("PROTOCOLO-INEXISTENTE-999")

    def test_serialization_roundtrip(self) -> None:
        """Verifica roundtrip de serialização to_dict() e from_dict()."""
        contract = self.registry.get_contract("NDF-USD-BRL-001")
        c_dict = contract.to_dict()
        reconstituted = FxForwardContract.from_dict(c_dict)

        self.assertEqual(contract.contract_id, reconstituted.contract_id)
        self.assertEqual(contract.notional_foreign, reconstituted.notional_foreign)
        self.assertEqual(contract.forward_rate, reconstituted.forward_rate)
        self.assertEqual(contract.currency_pair, reconstituted.currency_pair)
        self.assertEqual(contract.trade_date, reconstituted.trade_date)
        self.assertEqual(contract.fixing_date, reconstituted.fixing_date)
        self.assertEqual(contract.settlement_date, reconstituted.settlement_date)

        reg_dict = self.registry.to_dict()
        reg_reconstituted = FxForwardNdfRegistry.from_dict(reg_dict)
        self.assertEqual(len(reg_reconstituted.contracts), len(self.registry.contracts))

    # =====================================================================
    # 2. TESTES DE PARIDADE DE JUROS E TAXA A TERMO TEÓRICA
    # =====================================================================

    def test_theoretical_forward_rate_linear_parity(self) -> None:
        """Valida a fórmula linear de paridade: F = S * ((1 + i_brl * DU/252) / (1 + i_usd * DC/360))."""
        spot = Decimal("5.0000")
        i_brl = Decimal("0.1050")   # 10.50% a.a.
        i_usd = Decimal("0.0500")   # 5.00% a.a.
        du = 126                    # 0.5 ano útil
        dc = 180                    # 0.5 ano corrido

        # Factor BRL = 1 + 0.1050 * 126/252 = 1 + 0.0525 = 1.0525
        # Factor USD = 1 + 0.0500 * 180/360 = 1 + 0.0250 = 1.0250
        # F = 5.0000 * (1.0525 / 1.0250) = 5.13414634... -> 5.1341
        f = calculate_theoretical_forward_rate(spot, i_brl, i_usd, du, dc, CompoundingMethod.LINEAR)
        self.assertEqual(f, Decimal("5.1341"))

    def test_theoretical_forward_rate_exponential_brl(self) -> None:
        """Valida paridade com capitalização composta em BRL (padrão DI Futuro)."""
        spot = Decimal("5.0000")
        i_brl = Decimal("0.1200")
        i_usd = Decimal("0.0500")
        du = 252  # 1 ano útil completo
        dc = 360  # 1 ano corrido completo

        # Com DU = 252: (1 + 0.12)^(252/252) = 1.1200
        # Com DC = 360: 1 + 0.05 * 360/360 = 1.0500
        # F = 5.0000 * (1.1200 / 1.0500) = 5.333333... -> 5.3333
        f = calculate_theoretical_forward_rate(spot, i_brl, i_usd, du, dc, CompoundingMethod.EXPONENTIAL_BRL)
        self.assertEqual(f, Decimal("5.3333"))

    def test_theoretical_forward_rate_zero_du(self) -> None:
        """Verifica caso limite DU = 0: Factor BRL = 1.0000."""
        spot = Decimal("5.2000")
        i_brl = Decimal("0.1000")
        i_usd = Decimal("0.0500")
        f = calculate_theoretical_forward_rate(spot, i_brl, i_usd, du=0, dc=36, compounding=CompoundingMethod.LINEAR)
        # Factor BRL = 1, Factor USD = 1 + 0.05 * 36/360 = 1.005
        # F = 5.2000 / 1.0050 = 5.174129... -> 5.1741
        self.assertEqual(f, Decimal("5.1741"))

    def test_invalid_parameters_for_forward_rate(self) -> None:
        """Rejeita parâmetros inválidos (spot <= 0, dc <= 0, du < 0)."""
        with self.assertRaises(FxForwardNdfValidationError):
            calculate_theoretical_forward_rate(Decimal("-5.0000"), Decimal("0.10"), Decimal("0.05"), 100, 150)
        with self.assertRaises(FxForwardNdfValidationError):
            calculate_theoretical_forward_rate(Decimal("5.0000"), Decimal("0.10"), Decimal("0.05"), 100, 0)
        with self.assertRaises(FxForwardNdfValidationError):
            calculate_theoretical_forward_rate(Decimal("5.0000"), Decimal("0.10"), Decimal("0.05"), -10, 150)

    # =====================================================================
    # 3. TESTES DE APURAÇÃO DE CUPOM CAMBIAL LIMPO E SUJO
    # =====================================================================

    def test_cupom_sujo_and_limpo_identity_when_spot_equals_ptax_ref(self) -> None:
        """Quando S == PTAX_ref, Cupom Sujo é rigorosamente idêntico ao Cupom Limpo."""
        spot = Decimal("5.3500")
        ptax_ref = Decimal("5.3500")
        forward = Decimal("5.4800")
        i_brl = Decimal("0.1100")
        du = 126
        dc = 180

        sujo = calculate_cupom_sujo(spot, forward, i_brl, du, dc)
        limpo = calculate_cupom_limpo(ptax_ref, forward, i_brl, du, dc)
        self.assertEqual(sujo, limpo)

    def test_cupom_limpo_to_sujo_conversion(self) -> None:
        """Valida que (1 + i_sujo * DC/360) == (1 + i_limpo * DC/360) * (S / PTAX_ref)."""
        spot = Decimal("5.4000")
        ptax_ref = Decimal("5.3500")
        forward = Decimal("5.5000")
        i_brl = Decimal("0.1100")
        du = 100
        dc = 150

        sujo_calc = calculate_cupom_sujo(spot, forward, i_brl, du, dc)
        limpo_calc = calculate_cupom_limpo(ptax_ref, forward, i_brl, du, dc)

        # Como S > PTAX_ref, Cupom Sujo deve ser estritamente superior ao Cupom Limpo
        self.assertGreater(sujo_calc, limpo_calc)

        # Conversão direta
        sujo_from_limpo = convert_cupom_limpo_to_sujo(limpo_calc, spot, ptax_ref, dc)
        self.assertEqual(sujo_calc, sujo_from_limpo)

        # Conversão reversa
        limpo_from_sujo = convert_cupom_sujo_to_limpo(sujo_calc, spot, ptax_ref, dc)
        self.assertEqual(limpo_calc, limpo_from_sujo)

    def test_reconstruct_forward_rate_from_implied_cupom(self) -> None:
        """Verifica consistência bidirecional: F calculado a partir de i_sujo fecha exatamente no F de entrada."""
        spot = Decimal("5.3800")
        f_target = Decimal("5.5200")
        i_brl = Decimal("0.1125")
        du = 85
        dc = 120

        implied_cupom = calculate_cupom_sujo(spot, f_target, i_brl, du, dc)
        f_reconstructed = calculate_theoretical_forward_rate(spot, i_brl, implied_cupom, du, dc)
        self.assertEqual(f_target, f_reconstructed)

    # =====================================================================
    # 4. TESTES DE MARCAÇÃO A MERCADO (MtM) E CONSERVAÇÃO SOMA-ZERO
    # =====================================================================

    def test_mark_to_market_bilateral_zero_sum(self) -> None:
        """Garante que Ganho_Comprador + Perda_Vendedor == 0,00 na MtM (LAW-061)."""
        contract = self.registry.get_contract("NDF-USD-BRL-001")
        val_date = date(2026, 8, 3)

        mtm = contract.mark_to_market(
            valuation_date=val_date,
            current_spot=Decimal("5.3000"),
            i_brl=Decimal("0.1080"),
            i_foreign=Decimal("0.0510"),
            remaining_du=63,
            remaining_dc=93,
        )

        self.assertEqual(mtm.buyer_mtm_brl + mtm.seller_mtm_brl, Decimal("0.00"))
        self.assertTrue(mtm.zero_sum_verified)
        self.assertEqual(contract.status, ContractStatus.ACTIVE)

        # Verificação formal da invariante
        contract_inv = contract.verify_invariants()
        self.assertTrue(contract_inv["all_invariants_hold"])

    def test_mtm_spot_sensitivity(self) -> None:
        """Verifica a sensibilidade delta: alta de spot eleva MtM do comprador e reduz do vendedor."""
        contract = self.registry.get_contract("NDF-USD-BRL-001")
        val_date = date(2026, 8, 3)

        mtm_low = contract.mark_to_market(
            valuation_date=val_date,
            current_spot=Decimal("5.1000"),
            i_brl=Decimal("0.1050"),
            i_foreign=Decimal("0.0525"),
            remaining_du=63,
            remaining_dc=93,
        )
        mtm_high = contract.mark_to_market(
            valuation_date=val_date,
            current_spot=Decimal("5.4000"),
            i_brl=Decimal("0.1050"),
            i_foreign=Decimal("0.0525"),
            remaining_du=63,
            remaining_dc=93,
        )

        self.assertGreater(mtm_high.buyer_mtm_brl, mtm_low.buyer_mtm_brl)
        self.assertLess(mtm_high.seller_mtm_brl, mtm_low.seller_mtm_brl)
        self.assertEqual(mtm_high.buyer_mtm_brl + mtm_high.seller_mtm_brl, Decimal("0.00"))

    def test_mtm_at_maturity(self) -> None:
        """No vencimento (remaining_dc = 0), MtM é exatamente igual ao payoff não-descontado."""
        contract = self.registry.get_contract("NDF-USD-BRL-001")
        mtm = contract.mark_to_market(
            valuation_date=contract.settlement_date,
            current_spot=Decimal("5.4500"),
            i_brl=Decimal("0.1050"),
            i_foreign=Decimal("0.0525"),
            remaining_du=0,
            remaining_dc=0,
        )

        # Payoff = 1,000,000 * (5.4500 - 5.2500) = +200,000.00 BRL
        self.assertEqual(mtm.buyer_mtm_brl, Decimal("200000.00"))
        self.assertEqual(mtm.seller_mtm_brl, Decimal("-200000.00"))
        self.assertEqual(mtm.discount_factor_brl, Decimal("1.00000000"))

    # =====================================================================
    # 5. TESTES DE FIXAÇÃO PTAX (BACEN) E LIQUIDAÇÃO FINANCEIRA NDF
    # =====================================================================

    def test_ndf_settlement_buyer_gain(self) -> None:
        """Cenário 1: PTAX > ForwardRate -> Comprador lucra, Vendedor paga ajuste em BRL."""
        contract = self.registry.get_contract("NDF-USD-BRL-001")
        # Nocional: 1.000.000 USD, ForwardRate: 5.2500
        # PTAX de fixação: 5.4200
        # Diferencial: 5.4200 - 5.2500 = +0.1700 BRL/USD
        # Ajuste Comprador: +170.000,00 BRL
        # Ajuste Vendedor: -170.000,00 BRL

        contract.fix_ptax(Decimal("5.4200"), fixing_date=date(2026, 11, 4))
        self.assertEqual(contract.status, ContractStatus.FIXED)

        res = contract.settle(date(2026, 11, 4))
        self.assertEqual(res.settlement_status, "SETTLED")
        self.assertEqual(res.buyer_gross_adjustment_brl, Decimal("170000.00"))
        self.assertEqual(res.seller_gross_adjustment_brl, Decimal("-170000.00"))
        self.assertEqual(res.foreign_currency_delivered, Decimal("0.00"))
        self.assertEqual(res.brl_cash_delivered, Decimal("170000.00"))

        # Invariante soma-zero estrita
        self.assertTrue(res.zero_sum_conserved)
        self.assertEqual(res.buyer_gross_adjustment_brl + res.seller_gross_adjustment_brl, Decimal("0.00"))

        # Fluxos de caixa
        self.assertEqual(len(res.cash_flows), 2)
        seller_flow = [cf for cf in res.cash_flows if cf.participant_id == contract.seller_participant_id][0]
        buyer_flow = [cf for cf in res.cash_flows if cf.participant_id == contract.buyer_participant_id][0]

        self.assertEqual(seller_flow.amount, Decimal("-170000.00"))
        self.assertEqual(seller_flow.direction, "PAY")
        self.assertEqual(buyer_flow.amount, Decimal("170000.00"))
        self.assertEqual(buyer_flow.direction, "RECEIVE")

    def test_ndf_settlement_buyer_loss(self) -> None:
        """Cenário 2: PTAX < ForwardRate -> Comprador perde, paga ajuste em BRL ao Vendedor (Lag D1)."""
        contract = self.registry.get_contract("NDF-USD-BRL-002")
        # Nocional: 5.000.000 USD, ForwardRate: 5.5000, FixingLag: D1
        # Fixing Date: 2026-11-03, Settlement Date: 2026-11-04
        # PTAX de fixação: 5.3850
        # Diferencial: 5.3850 - 5.5000 = -0.1150 BRL/USD
        # Ajuste Comprador: 5.000.000 * (-0.1150) = -575.000,00 BRL
        # Ajuste Vendedor: +575.000,00 BRL

        contract.fix_ptax(Decimal("5.3850"), fixing_date=date(2026, 11, 3))
        self.assertEqual(contract.status, ContractStatus.FIXED)

        res = contract.settle(date(2026, 11, 4))
        self.assertEqual(res.buyer_gross_adjustment_brl, Decimal("-575000.00"))
        self.assertEqual(res.seller_gross_adjustment_brl, Decimal("575000.00"))
        self.assertTrue(res.zero_sum_conserved)
        self.assertEqual(res.buyer_gross_adjustment_brl + res.seller_gross_adjustment_brl, Decimal("0.00"))

        buyer_flow = [cf for cf in res.cash_flows if cf.participant_id == contract.buyer_participant_id][0]
        seller_flow = [cf for cf in res.cash_flows if cf.participant_id == contract.seller_participant_id][0]

        self.assertEqual(buyer_flow.direction, "PAY")
        self.assertEqual(buyer_flow.amount, Decimal("-575000.00"))
        self.assertEqual(seller_flow.direction, "RECEIVE")
        self.assertEqual(seller_flow.amount, Decimal("575000.00"))

    def test_ndf_settlement_eur_gain(self) -> None:
        """Cenário 3: NDF EUR/BRL com lucro para comprador."""
        contract = self.registry.get_contract("NDF-EUR-BRL-001")
        # Nocional: 2.000.000 EUR, ForwardRate: 5.8000
        # PTAX Fixação: 5.9125
        # Diferencial: 5.9125 - 5.8000 = +0.1125 BRL/EUR
        # Ajuste Comprador: 2.000.000 * 0.1125 = +225.000,00 BRL
        contract.fix_ptax(Decimal("5.9125"), fixing_date=date(2026, 11, 4))
        res = contract.settle(date(2026, 11, 4))

        self.assertEqual(res.buyer_gross_adjustment_brl, Decimal("225000.00"))
        self.assertEqual(res.seller_gross_adjustment_brl, Decimal("-225000.00"))
        self.assertTrue(res.zero_sum_conserved)

    def test_ndf_settlement_flat_zero_adjustment(self) -> None:
        """Cenário 4: PTAX == ForwardRate -> Ajuste rigorosamente zero."""
        contract = self.registry.get_contract("NDF-USD-BRL-FLAT")
        contract.fix_ptax(Decimal("5.3500"), fixing_date=date(2026, 11, 4))
        res = contract.settle(date(2026, 11, 4))

        self.assertEqual(res.buyer_gross_adjustment_brl, Decimal("0.00"))
        self.assertEqual(res.seller_gross_adjustment_brl, Decimal("0.00"))
        self.assertTrue(res.zero_sum_conserved)

    # =====================================================================
    # 6. TESTES DE LIQUIDAÇÃO COM ENTREGA FÍSICA (DELIVERABLE FORWARD)
    # =====================================================================

    def test_deliverable_usd_settlement(self) -> None:
        """Valida entrega física de USD contra pagamento de BRL à taxa contratada."""
        contract = self.registry.get_contract("FWD-DELIV-USD-001")
        # Nocional: 3.000.000 USD, ForwardRate: 5.3000
        # BRL contratado: 3.000.000 * 5.3000 = 15.900.000,00 BRL
        # PTAX Fixação: 5.4500
        contract.fix_ptax(Decimal("5.4500"), fixing_date=date(2026, 11, 4))
        res = contract.settle(date(2026, 11, 4))

        self.assertEqual(res.settlement_type, FxContractType.DELIVERABLE)
        self.assertEqual(res.foreign_currency_delivered, Decimal("3000000.00"))
        self.assertEqual(res.brl_cash_delivered, Decimal("15900000.00"))

        # PnL econômico frente à PTAX: 3.000.000 * (5.4500 - 5.3000) = +450.000,00 BRL
        self.assertEqual(res.buyer_gross_adjustment_brl, Decimal("450000.00"))
        self.assertEqual(res.seller_gross_adjustment_brl, Decimal("-450000.00"))
        self.assertTrue(res.zero_sum_conserved)

        # 4 fluxos de caixa: BRL (comprador paga, vendedor recebe) e USD (vendedor entrega, comprador recebe)
        self.assertEqual(len(res.cash_flows), 4)

        buyer_brl = [cf for cf in res.cash_flows if cf.participant_id == contract.buyer_participant_id and cf.currency == Currency.BRL][0]
        seller_brl = [cf for cf in res.cash_flows if cf.participant_id == contract.seller_participant_id and cf.currency == Currency.BRL][0]
        buyer_usd = [cf for cf in res.cash_flows if cf.participant_id == contract.buyer_participant_id and cf.currency == Currency.USD][0]
        seller_usd = [cf for cf in res.cash_flows if cf.participant_id == contract.seller_participant_id and cf.currency == Currency.USD][0]

        self.assertEqual(buyer_brl.amount, Decimal("-15900000.00"))
        self.assertEqual(seller_brl.amount, Decimal("15900000.00"))
        self.assertEqual(buyer_usd.amount, Decimal("3000000.00"))
        self.assertEqual(seller_usd.amount, Decimal("-3000000.00"))

    def test_deliverable_eur_settlement(self) -> None:
        """Valida entrega física de EUR contra BRL com lag D1."""
        contract = self.registry.get_contract("FWD-DELIV-EUR-001")
        # Nocional: 1.500.000 EUR, ForwardRate: 6.0000
        # BRL contratado: 1.500.000 * 6.0000 = 9.000.000,00 BRL
        contract.fix_ptax(Decimal("5.9500"), fixing_date=date(2026, 11, 3))
        res = contract.settle(date(2026, 11, 4))

        self.assertEqual(res.foreign_currency_delivered, Decimal("1500000.00"))
        self.assertEqual(res.brl_cash_delivered, Decimal("9000000.00"))
        self.assertEqual(res.buyer_gross_adjustment_brl, Decimal("-75000.00"))
        self.assertEqual(res.seller_gross_adjustment_brl, Decimal("75000.00"))
        self.assertTrue(res.zero_sum_conserved)

    # =====================================================================
    # 7. TESTES DE INVARIANTES FORMAIS E RESILIÊNCIA ADVERSARIAL
    # =====================================================================

    def test_rejection_of_wash_trade(self) -> None:
        """Rejeita contrato com mesmo participante na ponta compradora e vendedora."""
        with self.assertRaises(FxForwardNdfValidationError):
            FxForwardContract(
                contract_id="NDF-WASH-001",
                nome_registration_id="NOME2026WASH001",
                contract_type=FxContractType.NDF,
                currency_pair=CurrencyPair.USD_BRL,
                trade_date=date(2026, 11, 4),
                fixing_date=date(2026, 12, 4),
                settlement_date=date(2026, 12, 4),
                fixing_lag=FixingSettlementLag.D0,
                notional_foreign=Decimal("1000000.00"),
                forward_rate=Decimal("5.3500"),
                buyer_participant_id="PART-SAME-BANK",
                seller_participant_id="PART-SAME-BANK",
            )

    def test_temporal_order_validations(self) -> None:
        """Rejeita cronogramas de datas inconsistentes."""
        # trade_date posterior à liquidação
        with self.assertRaises(FxForwardNdfValidationError):
            FxForwardContract(
                contract_id="NDF-INV-DATE-01",
                nome_registration_id="NOME2026INVDATE01",
                contract_type=FxContractType.NDF,
                currency_pair=CurrencyPair.USD_BRL,
                trade_date=date(2026, 12, 1),
                fixing_date=date(2026, 11, 4),
                settlement_date=date(2026, 11, 4),
                fixing_lag=FixingSettlementLag.D0,
                notional_foreign=Decimal("100000.00"),
                forward_rate=Decimal("5.3500"),
                buyer_participant_id="PART-A",
                seller_participant_id="PART-B",
            )

        # D0 com fixing_date != settlement_date
        with self.assertRaises(FxForwardNdfValidationError):
            FxForwardContract(
                contract_id="NDF-INV-DATE-02",
                nome_registration_id="NOME2026INVDATE02",
                contract_type=FxContractType.NDF,
                currency_pair=CurrencyPair.USD_BRL,
                trade_date=date(2026, 10, 1),
                fixing_date=date(2026, 11, 3),
                settlement_date=date(2026, 11, 4),
                fixing_lag=FixingSettlementLag.D0,
                notional_foreign=Decimal("100000.00"),
                forward_rate=Decimal("5.3500"),
                buyer_participant_id="PART-A",
                seller_participant_id="PART-B",
            )

        # D1 com fixing_date >= settlement_date
        with self.assertRaises(FxForwardNdfValidationError):
            FxForwardContract(
                contract_id="NDF-INV-DATE-03",
                nome_registration_id="NOME2026INVDATE03",
                contract_type=FxContractType.NDF,
                currency_pair=CurrencyPair.USD_BRL,
                trade_date=date(2026, 10, 1),
                fixing_date=date(2026, 11, 4),
                settlement_date=date(2026, 11, 4),
                fixing_lag=FixingSettlementLag.D1,
                notional_foreign=Decimal("100000.00"),
                forward_rate=Decimal("5.3500"),
                buyer_participant_id="PART-A",
                seller_participant_id="PART-B",
            )

    def test_duplicate_registration_rejection(self) -> None:
        """Garante que contratos ou protocolos NoMe duplicados são rejeitados."""
        c = self.registry.get_contract("NDF-USD-BRL-001")
        with self.assertRaises(DuplicateContractError):
            self.registry.register_contract(c)

    def test_settle_unfixed_contract_rejection(self) -> None:
        """Rejeita liquidação de contrato sem PTAX previamente fixada."""
        contract = self.registry.get_contract("NDF-USD-BRL-001")
        with self.assertRaises(ContractStateError):
            contract.settle(date(2026, 11, 4))

    def test_settle_already_settled_contract_rejection(self) -> None:
        """Rejeita liquidação repetida de contrato já liquidado."""
        contract = self.registry.get_contract("NDF-USD-BRL-001")
        contract.fix_ptax(Decimal("5.4000"), date(2026, 11, 4))
        contract.settle(date(2026, 11, 4))
        with self.assertRaises(ContractStateError):
            contract.settle(date(2026, 11, 4))

    def test_cancel_lifecycle(self) -> None:
        """Verifica fluxo de cancelamento de contrato ativo."""
        c = FxForwardContract(
            contract_id="NDF-TO-CANCEL",
            nome_registration_id="NOME2026CANCEL001",
            contract_type=FxContractType.NDF,
            currency_pair=CurrencyPair.USD_BRL,
            trade_date=date(2026, 11, 4),
            fixing_date=date(2026, 12, 4),
            settlement_date=date(2026, 12, 4),
            fixing_lag=FixingSettlementLag.D0,
            notional_foreign=Decimal("100000.00"),
            forward_rate=Decimal("5.3500"),
            buyer_participant_id="PART-A",
            seller_participant_id="PART-B",
        )
        self.registry.register_contract(c)
        c.cancel("Acordo mútuo entre as partes")
        self.assertEqual(c.status, ContractStatus.CANCELLED)

        # Não permite MtM após cancelamento
        with self.assertRaises(ContractStateError):
            c.mark_to_market(date(2026, 11, 10), Decimal("5.40"), Decimal("0.11"), Decimal("0.05"), 20, 24)

    # =====================================================================
    # 8. TESTE DE CICLO COMPLETO DA CARTEIRA E INVARIANTES AGREGADAS
    # =====================================================================

    def test_full_portfolio_execution_and_aggregate_invariants(self) -> None:
        """Executa ciclo completo (Fixação PTAX e Liquidação) em todos os contratos da fixture."""
        ptax_by_contract = {
            "NDF-USD-BRL-001": (Decimal("5.4200"), date(2026, 11, 4)),
            "NDF-USD-BRL-002": (Decimal("5.3850"), date(2026, 11, 3)),
            "NDF-EUR-BRL-001": (Decimal("5.9125"), date(2026, 11, 4)),
            "FWD-DELIV-USD-001": (Decimal("5.4500"), date(2026, 11, 4)),
            "FWD-DELIV-EUR-001": (Decimal("5.9500"), date(2026, 11, 3)),
            "NDF-USD-BRL-FLAT": (Decimal("5.3500"), date(2026, 11, 4)),
        }

        for cid, (ptax, fix_date) in ptax_by_contract.items():
            self.registry.fix_ptax(cid, fixing_ptax=ptax, fixing_date=fix_date)

        for cid in ptax_by_contract:
            contract = self.registry.get_contract(cid)
            res = self.registry.settle_contract(cid, settlement_date=contract.settlement_date)
            self.assertEqual(res.settlement_status, "SETTLED")

        # Verificação formal agregada
        report = self.registry.verify_invariants()
        self.assertTrue(report["all_invariants_hold"])
        self.assertEqual(len(report["violations"]), 0)
        self.assertEqual(report["aggregate_settlement_sum"], "0.00")
        self.assertEqual(report["total_contracts"], 6)


if __name__ == "__main__":
    unittest.main()
