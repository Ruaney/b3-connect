"""Testes unitários e de invariantes para Derivativos de Balcão B3 — Swap e NDF (FL-040)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from src.otc_derivatives_swap_ndf import (
    DuplicateOtcContractError,
    GuaranteeMode,
    IndexerType,
    NdfDirection,
    OtcContractNotFoundError,
    OtcDerivativeType,
    OtcDerivativeValidationError,
    OtcDerivativesEngine,
    OtcNdfContract,
    OtcSwapContract,
    SwapLegSpec,
    regressive_irrf_rate_for_days,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "otc_swap_ndf_contracts.json"
)


class TestOtcDerivativesSwapNdf(unittest.TestCase):
    """Suíte de testes determinísticos para o motor FL-040 (Swap e NDF no Balcão B3)."""

    def test_load_fixture_and_settle_all_invariants(self) -> None:
        """Carrega a fixture oficial e valida todos os invariantes soma-zero e tributários."""
        engine = OtcDerivativesEngine.from_json_fixture(FIXTURE_PATH)
        self.assertEqual(len(engine.swap_contracts), 3)
        self.assertEqual(len(engine.ndf_contracts), 3)

        results = engine.settle_all()
        self.assertEqual(len(results), 6)

        for res in results:
            self.assertTrue(
                res.is_zero_sum_conserved,
                f"Falha soma-zero bruta no contrato {res.contract_id}",
            )
            self.assertTrue(
                res.is_cashflow_balanced,
                f"Falha equilíbrio líquido + IRRF no contrato {res.contract_id}",
            )
            self.assertEqual(
                res.party_a_gross_brl + res.party_b_gross_brl,
                Decimal("0.00"),
            )
            # Parte perdedora nunca sofre retenção de IRRF
            if res.party_a_gross_brl <= Decimal("0.00"):
                self.assertEqual(res.party_a_irrf_brl, Decimal("0.00"))
                self.assertEqual(res.party_a_net_brl, res.party_a_gross_brl)
            if res.party_b_gross_brl <= Decimal("0.00"):
                self.assertEqual(res.party_b_irrf_brl, Decimal("0.00"))
                self.assertEqual(res.party_b_net_brl, res.party_b_gross_brl)

        summary = engine.portfolio_summary()
        self.assertEqual(summary["total_contracts"], 6)
        self.assertEqual(summary["swap_count"], 3)
        self.assertEqual(summary["ndf_count"], 3)
        self.assertTrue(summary["all_zero_sum_conserved"])
        self.assertTrue(summary["all_cashflow_balanced"])

    def test_swap_cdi_vs_pre_exact_factors_and_settlement(self) -> None:
        """Valida o cálculo exato de F_ativo (CDI) vs F_passivo (Pré) em 252 DU (1 ano base)."""
        swap = OtcSwapContract(
            contract_id="SWAP-TEST-252DU",
            nome_protocol="NOME2026TEST0001",
            trade_date=date(2025, 11, 3),
            maturity_date=date(2026, 11, 3),
            business_days_du=252,
            notional_brl=Decimal("1000000.00"),
            party_a_name="Empresa Exportadora S.A.",
            party_a_document="12.345.678/0001-90",
            party_a_irrf_rate=Decimal("0.1750"),
            party_a_irrf_exempt=False,
            party_b_name="Banco Dealer S.A.",
            party_b_document="98.765.432/0001-10",
            party_b_irrf_rate=Decimal("0.0000"),
            party_b_irrf_exempt=True,
            guarantee_mode=GuaranteeMode.SEM_GARANTIA_CCP,
            active_leg=SwapLegSpec(
                indexer=IndexerType.CDI,
                rate_annual_pct=Decimal("12.0000"),
                cdi_percentage=Decimal("100.0000"),
            ),
            passive_leg=SwapLegSpec(
                indexer=IndexerType.PRE,
                rate_annual_pct=Decimal("10.0000"),
            ),
        )

        # Em 252 DU: F_ativo = 1.12000000 e F_passivo = 1.10000000
        self.assertEqual(swap.active_factor, Decimal("1.12000000"))
        self.assertEqual(swap.passive_factor, Decimal("1.10000000"))
        self.assertEqual(swap.active_leg_final_value_brl, Decimal("1120000.00"))
        self.assertEqual(swap.passive_leg_final_value_brl, Decimal("1100000.00"))

        engine = OtcDerivativesEngine()
        res = engine.settle_swap(swap)

        # Ajuste Bruto Parte A = 1.000.000 * (1.12 - 1.10) = +20.000,00
        self.assertEqual(res.party_a_gross_brl, Decimal("20000.00"))
        self.assertEqual(res.party_b_gross_brl, Decimal("-20000.00"))
        # IRRF Parte A = 20.000,00 * 17,5% = 3.500,00
        self.assertEqual(res.party_a_irrf_brl, Decimal("3500.00"))
        self.assertEqual(res.party_a_net_brl, Decimal("16500.00"))
        # Parte B (perdedora) paga 20.000,00 sem IRRF
        self.assertEqual(res.party_b_irrf_brl, Decimal("0.00"))
        self.assertEqual(res.party_b_net_brl, Decimal("-20000.00"))
        self.assertEqual(res.winning_party_document, "12.345.678/0001-90")
        self.assertEqual(res.losing_party_document, "98.765.432/0001-10")

    def test_swap_dolar_cupom_vs_cdi_settlement(self) -> None:
        """Valida Swap Cambial (Variação Cambial PTAX + Cupom Linear DC/360 vs CDI)."""
        swap = OtcSwapContract(
            contract_id="SWAP-FX-TEST-01",
            nome_protocol="NOME2026TEST0002",
            trade_date=date(2026, 5, 4),
            maturity_date=date(2026, 11, 3),
            business_days_du=126,
            calendar_days_dc=180,
            notional_brl=Decimal("10000000.00"),
            party_a_name="Importadora Industrial S.A.",
            party_a_document="22.333.444/0001-55",
            party_a_irrf_rate=Decimal("0.2250"),
            party_a_irrf_exempt=False,
            party_b_name="Banco Itaú BBA S.A.",
            party_b_document="17.298.092/0001-30",
            party_b_irrf_rate=Decimal("0.0000"),
            party_b_irrf_exempt=True,
            guarantee_mode=GuaranteeMode.COM_GARANTIA_CCP,
            active_leg=SwapLegSpec(
                indexer=IndexerType.DOLAR,
                rate_annual_pct=Decimal("6.0000"),  # 6% a.a. em 180 dias -> +3% de cupom
                initial_fx_ptax=Decimal("5.0000"),
                final_fx_ptax=Decimal("5.5000"),    # +10% variação cambial
                linear_coupon_360=True,
            ),
            passive_leg=SwapLegSpec(
                indexer=IndexerType.CDI,
                explicit_accumulated_factor=Decimal("1.05500000"),
            ),
        )
        # F_ativo = (5.50 / 5.00) * (1 + 0.06 * 180/360) = 1.10 * 1.03 = 1.13300000
        self.assertEqual(swap.active_factor, Decimal("1.13300000"))
        self.assertEqual(swap.passive_factor, Decimal("1.05500000"))

        engine = OtcDerivativesEngine()
        res = engine.settle_swap(swap)

        # Ajuste Bruto Parte A = 10.000.000 * (1.133 - 1.055) = 10.000.000 * 0.078 = 780.000,00
        self.assertEqual(res.party_a_gross_brl, Decimal("780000.00"))
        self.assertEqual(res.party_b_gross_brl, Decimal("-780000.00"))
        # IRRF 22,5% sobre 780.000,00 = 175.500,00
        self.assertEqual(res.party_a_irrf_brl, Decimal("175500.00"))
        self.assertEqual(res.party_a_net_brl, Decimal("604500.00"))

    def test_ndf_buyer_and_seller_symmetry_and_irrf(self) -> None:
        """Valida o ajuste do Termo de Moeda (NDF) para Comprador e Vendedor e a simetria bilateral."""
        engine = OtcDerivativesEngine()

        # Caso 1: Parte A é Compradora a termo (K = 5.2000), PTAX sobe para 5.4500 -> Parte A ganha
        ndf_buyer_win = OtcNdfContract(
            contract_id="NDF-BUYER-WIN",
            nome_protocol="NOME2026NDF001",
            trade_date=date(2026, 8, 1),
            maturity_date=date(2026, 11, 3),
            currency="USD",
            notional_foreign=Decimal("1000000.00"),
            forward_rate_k=Decimal("5.2000"),
            settlement_ptax=Decimal("5.4500"),
            party_a_direction=NdfDirection.BUYER,
            party_a_name="Empresa Compradora NDF",
            party_a_document="10.000.000/0001-01",
            party_a_irrf_rate=Decimal("0.2250"),
            party_a_irrf_exempt=False,
            party_b_name="Banco Vendedor NDF",
            party_b_document="20.000.000/0001-02",
            party_b_irrf_rate=Decimal("0.0000"),
            party_b_irrf_exempt=True,
        )
        res_win = engine.settle_ndf(ndf_buyer_win)
        # Ajuste = 1.000.000 * (5.4500 - 5.2000) = +250.000,00
        self.assertEqual(res_win.party_a_gross_brl, Decimal("250000.00"))
        self.assertEqual(res_win.party_b_gross_brl, Decimal("-250000.00"))
        self.assertEqual(res_win.party_a_irrf_brl, Decimal("56250.00"))
        self.assertEqual(res_win.party_a_net_brl, Decimal("193750.00"))
        self.assertEqual(res_win.party_b_net_brl, Decimal("-250000.00"))

        # Caso 2: Parte A é Compradora a termo (K = 5.6000), PTAX cai para 5.3500 -> Parte A perde
        ndf_buyer_loss = OtcNdfContract(
            contract_id="NDF-BUYER-LOSS",
            nome_protocol="NOME2026NDF002",
            trade_date=date(2026, 8, 1),
            maturity_date=date(2026, 11, 3),
            currency="USD",
            notional_foreign=Decimal("1000000.00"),
            forward_rate_k=Decimal("5.6000"),
            settlement_ptax=Decimal("5.3500"),
            party_a_direction=NdfDirection.BUYER,
            party_a_name="Empresa Compradora NDF",
            party_a_document="10.000.000/0001-01",
            party_a_irrf_rate=Decimal("0.2250"),
            party_a_irrf_exempt=False,
            party_b_name="Fundo Institucional Contraparte",
            party_b_document="30.000.000/0001-03",
            party_b_irrf_rate=Decimal("0.1500"),
            party_b_irrf_exempt=False,
        )
        res_loss = engine.settle_ndf(ndf_buyer_loss)
        # Parte A perde -250.000,00 (sem IRRF); Parte B ganha +250.000,00 (com 15% de IRRF = 37.500,00)
        self.assertEqual(res_loss.party_a_gross_brl, Decimal("-250000.00"))
        self.assertEqual(res_loss.party_a_irrf_brl, Decimal("0.00"))
        self.assertEqual(res_loss.party_a_net_brl, Decimal("-250000.00"))
        self.assertEqual(res_loss.party_b_gross_brl, Decimal("250000.00"))
        self.assertEqual(res_loss.party_b_irrf_brl, Decimal("37500.00"))
        self.assertEqual(res_loss.party_b_net_brl, Decimal("212500.00"))

    def test_zero_adjustment_when_rates_equal(self) -> None:
        """Verifica que quando S_PTAX == K no NDF (ou F_ativo == F_passivo no Swap), o ajuste é R$ 0,00."""
        engine = OtcDerivativesEngine()
        ndf_flat = OtcNdfContract(
            contract_id="NDF-FLAT-01",
            nome_protocol="NOME2026FLAT001",
            trade_date=date(2026, 9, 1),
            maturity_date=date(2026, 11, 3),
            currency="USD",
            notional_foreign=Decimal("500000.00"),
            forward_rate_k=Decimal("5.4000"),
            settlement_ptax=Decimal("5.4000"),
            party_a_direction=NdfDirection.SELLER,
            party_a_name="Exportadora A",
            party_a_document="11.111.111/0001-11",
            party_a_irrf_rate=Decimal("0.2250"),
            party_b_name="Banco B",
            party_b_document="22.222.222/0001-22",
        )
        res = engine.settle_ndf(ndf_flat)
        self.assertEqual(res.party_a_gross_brl, Decimal("0.00"))
        self.assertEqual(res.party_b_gross_brl, Decimal("0.00"))
        self.assertEqual(res.total_irrf_brl, Decimal("0.00"))
        self.assertIsNone(res.winning_party_document)
        self.assertIsNone(res.losing_party_document)

    def test_validation_and_duplicate_errors(self) -> None:
        """Verifica as rejeições de validação cadastral, duplicidade de protocolo e consulta."""
        engine = OtcDerivativesEngine.from_json_fixture(FIXTURE_PATH)

        # Protocolo duplicado
        with self.assertRaises(DuplicateOtcContractError):
            engine.register_ndf(
                OtcNdfContract(
                    contract_id="NDF-NEW-ID",
                    nome_protocol="NOME202611030001847",  # Já usado por SWAP-B3-2026-0001
                    trade_date=date(2026, 8, 1),
                    maturity_date=date(2026, 11, 3),
                    currency="USD",
                    notional_foreign=Decimal("100000.00"),
                    forward_rate_k=Decimal("5.1000"),
                    settlement_ptax=Decimal("5.2000"),
                    party_a_direction=NdfDirection.BUYER,
                    party_a_name="A",
                    party_a_document="11.111.111/0001-11",
                    party_b_name="B",
                    party_b_document="22.222.222/0001-22",
                )
            )

        # Contrato inexistente
        with self.assertRaises(OtcContractNotFoundError):
            engine.settle_contract("INEXISTENTE-999")

        # Operação consigo mesmo (mesmo CNPJ nas duas pontas)
        with self.assertRaises(OtcDerivativeValidationError):
            OtcNdfContract(
                contract_id="NDF-SELF-TRADE",
                nome_protocol="NOME2026SELF001",
                trade_date=date(2026, 8, 1),
                maturity_date=date(2026, 11, 3),
                currency="USD",
                notional_foreign=Decimal("100000.00"),
                forward_rate_k=Decimal("5.1000"),
                settlement_ptax=Decimal("5.2000"),
                party_a_direction=NdfDirection.BUYER,
                party_a_name="Mesma Empresa",
                party_a_document="11.111.111/0001-11",
                party_b_name="Mesma Empresa Filial",
                party_b_document="11.111.111/0001-11",
            )

        # Tabela regressiva de IRRF
        self.assertEqual(regressive_irrf_rate_for_days(90), Decimal("0.2250"))
        self.assertEqual(regressive_irrf_rate_for_days(210), Decimal("0.2000"))
        self.assertEqual(regressive_irrf_rate_for_days(500), Decimal("0.1750"))
        self.assertEqual(regressive_irrf_rate_for_days(900), Decimal("0.1500"))


if __name__ == "__main__":
    unittest.main()
