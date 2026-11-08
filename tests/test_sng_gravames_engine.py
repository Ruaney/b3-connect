"""Testes unitários para o Sistema Nacional de Gravames (SNG) B3 Financiamentos."""

from decimal import Decimal
import unittest

from src.sng_gravames_engine import (
    ModalidadeGravame,
    SngGravamesEngine,
    StatusGravame,
    VeiculoIdentificacao,
)


class TestSngGravamesEngine(unittest.TestCase):
    """Bateria de testes para inclusão, consulta, baixa e invariantes da LAW-112 no SNG."""

    def setUp(self) -> None:
        self.engine = SngGravamesEngine()
        self.veiculo_valido = VeiculoIdentificacao(
            chassi="9BWCA41V01P123456",
            renavam="12345678901",
            placa="ABC1D23",
            uf_emplacamento="SP",
            marca_modelo="VOLKSWAGEN POLO 1.0 TSI",
            ano_fabricacao=2024,
            ano_modelo=2025,
        )

    def test_inclusao_e_consulta_gravame_sucesso(self) -> None:
        """Testa o registro bem-sucedido de alienação fiduciária com geração de hash."""
        rec = self.engine.registrar_gravame(
            contrato_numero="CTR-FIN-2026-001",
            modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
            credor_cnpj="60701190000104",
            credor_nome="Banco Itaú Veículos S/A",
            devedor_documento="12345678909",
            devedor_nome="Carlos Silva",
            veiculo=self.veiculo_valido,
            valor_financiamento=Decimal("65000.00"),
            quantidade_parcelas=48,
            taxa_juros_anual_pct=Decimal("18.5"),
        )
        self.assertEqual(rec.status, StatusGravame.ATIVO)
        self.assertTrue(rec.gravame_id.startswith("SNG-SP-"))
        self.assertTrue(rec.detran_protocolo.startswith("DETRAN-SP-"))
        self.assertTrue(rec.hash_seguranca)

        # Consulta pelo chassi
        consultado = self.engine.consultar_por_chassi("9BWCA41V01P123456")
        self.assertIsNotNone(consultado)
        self.assertEqual(consultado.gravame_id, rec.gravame_id)

    def test_violacao_law_112_duplicidade_gravame_ativo(self) -> None:
        """Garante que dois credores não conseguem registrar gravames simultâneos no mesmo chassi."""
        self.engine.registrar_gravame(
            contrato_numero="CTR-001",
            modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
            credor_cnpj="60701190000104",
            credor_nome="Banco A",
            devedor_documento="111",
            devedor_nome="Cliente 1",
            veiculo=self.veiculo_valido,
            valor_financiamento=Decimal("50000.00"),
            quantidade_parcelas=36,
            taxa_juros_anual_pct=Decimal("16.0"),
        )

        # Tentativa de segundo financiamento sobre o mesmo chassi
        with self.assertRaises(ValueError) as ctx:
            self.engine.registrar_gravame(
                contrato_numero="CTR-002",
                modalidade=ModalidadeGravame.ARRENDAMENTO_MERCANTIL,
                credor_cnpj="90400888000142",
                credor_nome="Banco Santander Leasing",
                devedor_documento="222",
                devedor_nome="Cliente 2",
                veiculo=self.veiculo_valido,
                valor_financiamento=Decimal("40000.00"),
                quantidade_parcelas=24,
                taxa_juros_anual_pct=Decimal("17.0"),
            )
        self.assertIn("LAW-112", str(ctx.exception))

    def test_ciclo_completo_inclusao_e_baixa(self) -> None:
        """Valida que a baixa libera o chassi para novo gravame ou transferência."""
        rec = self.engine.registrar_gravame(
            contrato_numero="CTR-003",
            modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
            credor_cnpj="60701190000104",
            credor_nome="Banco A",
            devedor_documento="111",
            devedor_nome="Cliente 1",
            veiculo=self.veiculo_valido,
            valor_financiamento=Decimal("30000.00"),
            quantidade_parcelas=12,
            taxa_juros_anual_pct=Decimal("15.0"),
        )
        self.engine.verify_invariants()

        # Baixa do gravame
        baixado = self.engine.baixar_gravame(rec.gravame_id, "QUITACAO_INTEGRAL")
        self.assertEqual(baixado.status, StatusGravame.BAIXADO)
        self.assertIsNotNone(baixado.data_baixa)

        # Chassi não consta mais como ativo
        self.assertIsNone(self.engine.consultar_por_chassi("9BWCA41V01P123456"))
        self.engine.verify_invariants()

        # Agora é possível registrar novo gravame (ex: refinanciamento)
        novo_rec = self.engine.registrar_gravame(
            contrato_numero="CTR-004",
            modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
            credor_cnpj="90400888000142",
            credor_nome="Banco B",
            devedor_documento="111",
            devedor_nome="Cliente 1",
            veiculo=self.veiculo_valido,
            valor_financiamento=Decimal("35000.00"),
            quantidade_parcelas=24,
            taxa_juros_anual_pct=Decimal("14.5"),
        )
        self.assertEqual(novo_rec.status, StatusGravame.ATIVO)
        self.engine.verify_invariants()


if __name__ == "__main__":
    unittest.main()
