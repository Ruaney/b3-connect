"""Testes unitários para o Sistema de Registro de Operações (SRO) B3 / SUSEP."""

from decimal import Decimal
import unittest

from src.sro_seguros_engine import (
    CosseguradorShare,
    RamoSeguro,
    SroSegurosEngine,
    StatusApolice,
    StatusSinistro,
)


class TestSroSegurosEngine(unittest.TestCase):
    """Bateria de testes para emissão de apólices, cosseguro, sinistros e LAW-113 no SRO."""

    def setUp(self) -> None:
        self.engine = SroSegurosEngine()

    def test_registro_apolice_cosseguro_sucesso(self) -> None:
        """Testa o registro com partição de cosseguro somando exatamente 100%."""
        cosseguradores = [
            CosseguradorShare(
                cnpj="33000111000100",
                razao_social="Porto Seguro Cia de Seguros Gerais",
                percentual_quota=Decimal("60.00"),
                is_lider=True,
            ),
            CosseguradorShare(
                cnpj="61550141000172",
                razao_social="Bradesco Auto/RE Companhia de Seguros",
                percentual_quota=Decimal("40.00"),
                is_lider=False,
            ),
        ]

        apolice = self.engine.registrar_apolice(
            numero_proposta="PROP-AUTO-2026-99",
            ramo=RamoSeguro.AUTOMOVEL,
            seguradora_lider_cnpj="33000111000100",
            seguradora_lider_nome="Porto Seguro",
            segurado_documento="01234567890",
            segurado_nome="Transportadora Rápido Sol Ltda",
            data_emissao="2026-11-01",
            inicio_vigencia="2026-11-01T00:00:00Z",
            fim_vigencia="2027-11-01T23:59:59Z",
            limite_maximo_garantia=Decimal("500000.00"),
            premio_liquido=Decimal("15000.00"),
            cosseguradores=cosseguradores,
        )

        self.assertEqual(apolice.status, StatusApolice.VIGENTE)
        self.assertTrue(apolice.susep_hash)
        self.assertEqual(len(apolice.cosseguradores), 2)
        # Prêmio total com IOF (7.38% sobre 15000 = 1107.00 => 16107.00)
        self.assertEqual(apolice.premio_total_com_iof, Decimal("16107.00"))

        inv = self.engine.verify_invariants()
        self.assertTrue(inv["verified"])

    def test_violacao_law_113_cosseguro_diferente_de_100_pct(self) -> None:
        """Rejeita apólices onde a soma das quotas de cosseguro não fecha em 100%."""
        cosseguradores_invalidos = [
            CosseguradorShare(
                cnpj="111",
                razao_social="Cia A",
                percentual_quota=Decimal("50.00"),
                is_lider=True,
            ),
            CosseguradorShare(
                cnpj="222",
                razao_social="Cia B",
                percentual_quota=Decimal("45.00"),  # Soma = 95.00%
                is_lider=False,
            ),
        ]

        with self.assertRaises(ValueError) as ctx:
            self.engine.registrar_apolice(
                numero_proposta="PROP-FAIL-01",
                ramo=RamoSeguro.PATRIMONIAL_COMPREENSIVO,
                seguradora_lider_cnpj="111",
                seguradora_lider_nome="Cia A",
                segurado_documento="999",
                segurado_nome="Indústria Metalúrgica",
                data_emissao="2026-11-01",
                inicio_vigencia="2026-11-01T00:00:00Z",
                fim_vigencia="2027-11-01T23:59:59Z",
                limite_maximo_garantia=Decimal("1000000.00"),
                premio_liquido=Decimal("20000.00"),
                cosseguradores=cosseguradores_invalidos,
            )
        self.assertIn("LAW-113", str(ctx.exception))

    def test_ciclo_sinistros_e_limite_maximo_garantia(self) -> None:
        """Garante que a liquidação de sinistros não pode exceder o LMG contratado."""
        apolice = self.engine.registrar_apolice(
            numero_proposta="PROP-SIN-01",
            ramo=RamoSeguro.RURAL_AGRICOLA,
            seguradora_lider_cnpj="5550000100",
            seguradora_lider_nome="Agro Seguradora S/A",
            segurado_documento="888",
            segurado_nome="Fazenda Esperança",
            data_emissao="2026-11-01",
            inicio_vigencia="2026-11-01T00:00:00Z",
            fim_vigencia="2027-11-01T23:59:59Z",
            limite_maximo_garantia=Decimal("200000.00"),
            premio_liquido=Decimal("8000.00"),
        )

        # 1. Avisa sinistro
        sin1 = self.engine.avisar_sinistro(
            apolice_id=apolice.apolice_id,
            data_ocorrencia="2026-11-05",
            valor_estimado=Decimal("120000.00"),
            descricao_cobertura="GRANIZO_LAVOURA",
        )
        self.assertEqual(sin1.status, StatusSinistro.AVISADO)

        # 2. Liquida sinistro 1
        self.engine.liquidar_sinistro(
            apolice_id=apolice.apolice_id,
            sinistro_id=sin1.sinistro_id,
            valor_indenizado=Decimal("110000.00"),
        )
        self.assertEqual(sin1.status, StatusSinistro.LIQUIDADO)
        self.engine.verify_invariants()

        # 3. Avisa segundo sinistro e tenta liquidar valor que estoura o LMG
        sin2 = self.engine.avisar_sinistro(
            apolice_id=apolice.apolice_id,
            data_ocorrencia="2026-11-06",
            valor_estimado=Decimal("100000.00"),
            descricao_cobertura="SECA_ESTIAGEM",
        )

        # Saldo remanescente de LMG = 200.000 - 110.000 = 90.000
        with self.assertRaises(ValueError) as ctx:
            self.engine.liquidar_sinistro(
                apolice_id=apolice.apolice_id,
                sinistro_id=sin2.sinistro_id,
                valor_indenizado=Decimal("95000.00"),  # 110k + 95k = 205k > 200k
            )
        self.assertIn("LAW-113", str(ctx.exception))

        # Liquida dentro do saldo remanescente
        self.engine.liquidar_sinistro(
            apolice_id=apolice.apolice_id,
            sinistro_id=sin2.sinistro_id,
            valor_indenizado=Decimal("90000.00"),
        )
        self.assertEqual(sin2.status, StatusSinistro.LIQUIDADO)
        self.engine.verify_invariants()

    def test_lgpd_masking_and_sha256_audit_trail(self) -> None:
        """Audita a conformidade com a LGPD (Lei nº 13.709/2018) no SRO SUSEP:
        - Hashing criptográfico SHA-256 de CPF/CNPJ de segurados.
        - Mascaramento e minimização em logs de auditoria e fixtures.
        - Proteção contra vazamento de dados de apólices e beneficiários.
        """
        apolice = self.engine.registrar_apolice(
            numero_proposta="PROP-LGPD-2026",
            ramo=RamoSeguro.PATRIMONIAL_COMPREENSIVO,
            seguradora_lider_cnpj="61550141000172",
            seguradora_lider_nome="Bradesco Seguros S/A",
            segurado_documento="05888999000123",
            segurado_nome="Complexo Logístico Barueri Ltda",
            data_emissao="2026-11-01",
            inicio_vigencia="2026-11-01T00:00:00Z",
            fim_vigencia="2027-11-01T23:59:59Z",
            limite_maximo_garantia=Decimal("2000000.00"),
            premio_liquido=Decimal("45000.00"),
        )

        # 1. Mascaramento LGPD
        self.assertEqual(apolice.segurado_documento_mascarado, "05.***.***/0001-23")
        self.assertEqual(apolice.seguradora_lider_cnpj_mascarado, "61.***.***/0001-72")
        self.assertEqual(apolice.segurado_nome_mascarado, "Co*** Lo*** Ba*** Lt***")
        self.assertEqual(
            apolice.segurado_documento_sha256,
            "bbe8be7f9c7611b5bbf06398105e8e46d11c831effa6bd6de59710f42ac8e425",
        )

        # 2. Representação minimizada
        masked_dict = apolice.to_masked_dict()
        self.assertTrue(masked_dict["lgpd_minimizada"])
        self.assertEqual(masked_dict["segurado_documento_mascarado"], "05.***.***/0001-23")

        # 3. Trilha de auditoria em conformidade LGPD
        audit_entry = self.engine.audit_log[-1]
        self.assertEqual(audit_entry["action"], "REGISTRO_APOLICE_SRO")
        self.assertEqual(audit_entry["segurado_documento_masked"], "05.***.***/0001-23")
        self.assertEqual(
            audit_entry["segurado_documento_sha256"],
            "bbe8be7f9c7611b5bbf06398105e8e46d11c831effa6bd6de59710f42ac8e425",
        )
        self.assertTrue(audit_entry["lgpd_audit"]["minimization"])
        self.assertTrue(audit_entry["lgpd_audit"]["pseudonymized"])


if __name__ == "__main__":
    unittest.main()
