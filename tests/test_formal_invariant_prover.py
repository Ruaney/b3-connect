"""B3 Integration Atlas — Suíte de Testes Formais do Provador Simbólico de Invariantes (Agente 16).

Validação matemática e testes de conformidade formal para:
1. Catálogo completo das 91 Leis de Conservação Formal (LAW-001 a LAW-113)
2. Prova por indução de balanço patrimonial nulo de CCP (LAW-004)
3. Não-destruição de liquidez em netting multilateral e conservação de book (LAW-002 / LAW-027)
4. Conservação de fluxo, default waterfall e atomicidade no Barramento Central (LAW-106A/B/C)
5. Invariante de unicidade e inviolabilidade de gravame veicular ativo SNG (LAW-112)
6. Invariante de partição estrita de cosseguro e teto LMG no SRO SUSEP (LAW-113)
7. Execução exaustiva e certificação formal de 100% das 91 leis
"""

from decimal import Decimal
from pathlib import Path
import sys
import unittest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.formal_invariant_prover import (
    FormalInvariantProver,
    FormalLaw,
    LawCategory,
    ProofCertificate,
    ProofMethod,
    ProofStatus,
)


class TestFormalInvariantProver(unittest.TestCase):
    """Bateria de testes de verificação formal para o FormalInvariantProver."""

    def setUp(self) -> None:
        self.prover = FormalInvariantProver()

    # -----------------------------------------------------------------
    # 1. VALIDAÇÃO DO CATÁLOGO DAS 91 LEIS
    # -----------------------------------------------------------------
    def test_canonical_catalog_contains_exactly_91_laws(self) -> None:
        """Verifica que o catálogo canônico possui rigorosamente 91 leis de conservação mapeadas."""
        self.assertEqual(
            self.prover.total_laws_count,
            91,
            f"O catálogo deve conter exatamente 91 leis, encontrado {self.prover.total_laws_count}",
        )
        self.assertEqual(len(self.prover.laws), 91)

    def test_law_lookup_by_code_and_aliases(self) -> None:
        """Garante que a busca de leis funciona tanto pelo código quanto por aliases regulatórios."""
        # Busca por código canônico
        law_001 = self.prover.get_law("LAW-001")
        self.assertEqual(law_001.code, "LAW-001")
        self.assertEqual(law_001.category, LawCategory.CSD_DEPOSITARY)

        # Aliases do Barramento Central (FL-100)
        law_106a = self.prover.get_law("LAW-106A")
        self.assertEqual(law_106a.code, "LAW-054")
        self.assertIn("LAW-106A", law_106a.aliases)

        law_106b = self.prover.get_law("LAW-106B")
        self.assertEqual(law_106b.code, "LAW-055")

        law_106c = self.prover.get_law("LAW-106C")
        self.assertEqual(law_106c.code, "LAW-056")

        # Aliases do Closing Cross (FL-098)
        law_104a = self.prover.get_law("LAW-104A")
        self.assertEqual(law_104a.code, "LAW-048")

        # Aliases de Garantias Triparty (FL-099)
        law_105a = self.prover.get_law("LAW-105A")
        self.assertEqual(law_105a.code, "LAW-051")

        # SNG e SRO
        law_112 = self.prover.get_law("LAW-112")
        self.assertEqual(law_112.code, "LAW-112")
        self.assertIn("LAW-SNG-GRAVAME-UNIQUENESS", law_112.aliases)

        law_113 = self.prover.get_law("LAW-113")
        self.assertEqual(law_113.code, "LAW-113")

    def test_invalid_law_lookup_raises_key_error(self) -> None:
        """Garante que códigos de leis inexistentes disparam KeyError com mensagem explicativa."""
        with self.assertRaises(KeyError) as ctx:
            self.prover.get_law("LAW-999")
        self.assertIn("LAW-999", str(ctx.exception))

    # -----------------------------------------------------------------
    # 2. PROVA DA LEI LAW-004: BALANÇO PATRIMONIAL NULO DE CCP
    # -----------------------------------------------------------------
    def test_prove_law_004_ccp_zero_balance_default_trades(self) -> None:
        """Testa a prova matemática por indução de balanço nulo da CCP com trades padrão."""
        cert = self.prover.prove_law_004_ccp_zero_balance()

        self.assertEqual(cert.law_code, "LAW-004")
        self.assertEqual(cert.status, ProofStatus.PROVEN)
        self.assertTrue(cert.base_case_verified)
        self.assertTrue(cert.inductive_step_verified)
        self.assertEqual(cert.evidence["ccp_total_residual_assets"], 0)
        self.assertEqual(cert.evidence["ccp_total_residual_cash"], "0.00")
        self.assertEqual(cert.evidence["systemic_member_net_assets"], 0)
        self.assertEqual(cert.evidence["systemic_member_net_cash"], "0.00")
        self.assertIn("Q.E.D.", cert.formal_deduction)

    def test_prove_law_004_ccp_zero_balance_arbitrary_stress(self) -> None:
        """Testa a prova formal da LAW-004 sob lote arbitrário de 20 negócios bilaterais complexos."""
        custom_trades = []
        members = ["BCO_ITAU", "BCO_BRADESCO", "BCO_BTG", "BCO_SANTANDER", "XP_CORRETORA"]
        symbols = ["PETR4", "VALE3", "BBAS3", "ITUB4"]

        for i in range(20):
            buyer = members[i % len(members)]
            seller = members[(i + 1) % len(members)]
            sym = symbols[i % len(symbols)]
            qty = (i + 1) * 100
            price = Decimal("15.50") + Decimal(str(i * 1.25))
            custom_trades.append({
                "buyer": buyer,
                "seller": seller,
                "symbol": sym,
                "quantity": qty,
                "price": price,
            })

        cert = self.prover.prove_law_004_ccp_zero_balance(trades=custom_trades)

        self.assertEqual(cert.status, ProofStatus.PROVEN)
        self.assertEqual(cert.evidence["trades_evaluated"], 20)
        self.assertEqual(cert.evidence["ccp_total_residual_assets"], 0)
        self.assertEqual(cert.evidence["ccp_total_residual_cash"], "0.00")
        self.assertEqual(cert.evidence["systemic_member_net_assets"], 0)
        self.assertEqual(cert.evidence["systemic_member_net_cash"], "0.00")

    # -----------------------------------------------------------------
    # 3. PROVA DAS LEIS LAW-002 E LAW-027: NETTING E LIVRO L3
    # -----------------------------------------------------------------
    def test_prove_law_002_027_netting_and_book_conservation(self) -> None:
        """Testa o fechamento contábil soma-zero do netting multilateral e conservação do book L3."""
        cert = self.prover.prove_law_002_027_netting_liquidity_conservation()

        self.assertEqual(cert.law_code, "LAW-002")
        self.assertEqual(cert.status, ProofStatus.PROVEN)
        self.assertTrue(cert.base_case_verified)
        self.assertTrue(cert.inductive_step_verified)
        self.assertEqual(cert.evidence["multilateral_net_sum"], "0.00")

        # Eficiência de netting deve estar no intervalo fechado [0, 1]
        eff = Decimal(cert.evidence["netting_efficiency_ratio"])
        self.assertGreaterEqual(eff, Decimal("0.0"))
        self.assertLessEqual(eff, Decimal("1.0"))

        # Conservação de ordens no book L3
        self.assertTrue(cert.evidence["book_conservation_verified"])
        self.assertGreaterEqual(cert.evidence["book_final_quantity"], 0)

    def test_prove_law_002_large_multilateral_matrix(self) -> None:
        """Testa a invariante de netting com 10 participantes e fluxos cruzados densos."""
        dense_obs = [
            ("P1", "P2", Decimal("1000000.00")),
            ("P2", "P3", Decimal("1500000.00")),
            ("P3", "P4", Decimal("800000.00")),
            ("P4", "P1", Decimal("200000.00")),
            ("P2", "P5", Decimal("500000.00")),
            ("P5", "P3", Decimal("700000.00")),
            ("P6", "P7", Decimal("3000000.00")),
            ("P7", "P8", Decimal("2500000.00")),
            ("P8", "P6", Decimal("1200000.00")),
            ("P9", "P10", Decimal("4000000.00")),
            ("P10", "P9", Decimal("4000000.00")),  # netting perfeito
        ]
        cert = self.prover.prove_law_002_027_netting_liquidity_conservation(obligations=dense_obs)
        self.assertEqual(cert.status, ProofStatus.PROVEN)
        self.assertEqual(cert.evidence["multilateral_net_sum"], "0.00")

    # -----------------------------------------------------------------
    # 4. PROVA DAS LEIS LAW-106A/B/C: BARRAMENTO CENTRAL
    # -----------------------------------------------------------------
    def test_prove_law_106_central_bus_flow_conservation(self) -> None:
        """Testa o equilíbrio contábil global dos 10 subsistemas, default waterfall e atomicidade inter-sistemas."""
        cert = self.prover.prove_law_106_central_bus_flow_conservation()

        self.assertEqual(cert.law_code, "LAW-106A/B/C")
        self.assertEqual(cert.status, ProofStatus.PROVEN)
        self.assertTrue(cert.base_case_verified)
        self.assertTrue(cert.inductive_step_verified)
        self.assertTrue(cert.evidence["law_106a_balance_verified"])
        self.assertTrue(cert.evidence["law_106b_waterfall_contained"])
        self.assertTrue(cert.evidence["law_106c_atomicity_verified"])
        self.assertEqual(cert.evidence["total_assets_brl"], cert.evidence["total_liabilities_brl"])
        self.assertEqual(cert.evidence["subsystems_count"], 10)

    # -----------------------------------------------------------------
    # 5. PROVA DA LEI LAW-112: UNICIDADE DE GRAVAME VEICULAR SNG
    # -----------------------------------------------------------------
    def test_prove_law_112_vehicle_lien_uniqueness(self) -> None:
        """Testa a prova por indução de unicidade de gravame ativo de 1º grau por chassi no SNG."""
        cert = self.prover.prove_law_112_vehicle_lien_uniqueness()

        self.assertEqual(cert.law_code, "LAW-112")
        self.assertEqual(cert.status, ProofStatus.PROVEN)
        self.assertTrue(cert.base_case_verified)
        self.assertTrue(cert.inductive_step_verified)
        self.assertLessEqual(cert.evidence["max_active_liens_observed"], 1)
        self.assertTrue(cert.evidence["conflicting_double_lien_blocked"])

    # -----------------------------------------------------------------
    # 6. PROVA DA LEI LAW-113: COSSEGURO E TETO LMG NO SRO
    # -----------------------------------------------------------------
    def test_prove_law_113_coinsurance_partition_and_lmg(self) -> None:
        """Testa a partição estrita de risco em 100% e o limite máximo de garantia em sinistros no SRO."""
        cert = self.prover.prove_law_113_coinsurance_partition_and_lmg()

        self.assertEqual(cert.law_code, "LAW-113")
        self.assertEqual(cert.status, ProofStatus.PROVEN)
        self.assertTrue(cert.base_case_verified)
        self.assertTrue(cert.inductive_step_verified)
        self.assertEqual(cert.evidence["total_quota_percent"], "100.00")
        self.assertEqual(cert.evidence["net_premium_brl"], cert.evidence["total_split_premium_brl"])
        self.assertTrue(cert.evidence["lmg_respected"])
        accumulated = Decimal(cert.evidence["accumulated_liquidated_claims"])
        lmg = Decimal(cert.evidence["policy_lmg_brl"])
        self.assertLessEqual(accumulated, lmg)

    # -----------------------------------------------------------------
    # 7. PROVA EXAUSTIVA DAS 91 LEIS FORMALMENTE (100% PROVADAS)
    # -----------------------------------------------------------------
    def test_prove_all_91_laws_exhaustive(self) -> None:
        """Executa a verificação matemática de todas as 91 leis e valida 100% de status PROVEN."""
        all_proofs = self.prover.prove_all_91_laws()

        self.assertEqual(len(all_proofs), 91, "Deve certificar exatamente 91 leis.")

        for code, cert in all_proofs.items():
            self.assertEqual(
                cert.status,
                ProofStatus.PROVEN,
                f"A lei {code} ({cert.law_name}) falhou na prova formal com status {cert.status}",
            )
            self.assertTrue(
                cert.base_case_verified,
                f"A lei {code} falhou na verificação do caso base",
            )
            self.assertTrue(
                cert.inductive_step_verified,
                f"A lei {code} falhou na verificação do passo indutivo",
            )
            self.assertGreater(len(cert.steps), 0)
            self.assertIn("Q.E.D.", cert.formal_deduction)

    def test_summary_and_markdown_report_integrity(self) -> None:
        """Verifica a integridade do sumário executivo e do relatório gerado em Markdown."""
        summary = self.prover.get_summary()

        self.assertEqual(summary["total_formal_laws"], 91)
        self.assertEqual(summary["laws_proven_successfully"], 91)
        self.assertEqual(summary["pass_rate_percent"], 100.0)
        self.assertEqual(summary["status"], "ALL_91_LAWS_MATHEMATICALLY_PROVEN")
        self.assertGreater(len(summary["by_category"]), 0)
        self.assertGreater(len(summary["by_proof_method"]), 0)

        # Geração do relatório em Markdown
        md = self.prover.generate_proof_report_markdown()
        self.assertIn("# B3 Integration Atlas — Relatório Formal de Verificação", md)
        self.assertIn("100.00%", md)
        self.assertIn("LAW-001", md)
        self.assertIn("LAW-004", md)
        self.assertIn("LAW-002", md)
        self.assertIn("LAW-106A", md)
        self.assertIn("LAW-112", md)
        self.assertIn("LAW-113", md)


if __name__ == "__main__":
    unittest.main()
