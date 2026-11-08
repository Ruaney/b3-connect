"""Testes unitários para o Catálogo Pedagógico de Conceitos Complexos B3."""

import unittest

from src.b3_concepts_catalog import CONCEPTS, B3Concept, B3ConceptsCatalog


class TestB3ConceptsCatalog(unittest.TestCase):
    """Bateria de testes para o catálogo de conceitos complexos da B3."""

    def setUp(self) -> None:
        self.catalog = B3ConceptsCatalog()

    def test_catalog_has_all_key_concepts(self) -> None:
        """Verifica que o catálogo possui os 10 conceitos estruturantes mapeados."""
        all_concepts = self.catalog.list_all()
        self.assertEqual(len(all_concepts), 10)

        expected_ids = {
            "novacao-ccp",
            "default-waterfall",
            "leilao-moc-freeze",
            "tuneis-circuit-breaker",
            "netting-multilateral",
            "registro-recebiveis-gravames",
            "drex-dvp-atomico",
            "superficies-volatilidade",
            "algorithmic-sor",
            "bsm-surveillance",
        }
        actual_ids = {c["id"] for c in all_concepts}
        self.assertEqual(actual_ids, expected_ids)

    def test_concepts_integrity_and_fields(self) -> None:
        """Garante que cada conceito possui todos os campos analíticos e didáticos preenchidos."""
        for c in CONCEPTS:
            self.assertIsInstance(c, B3Concept)
            self.assertTrue(c.id)
            self.assertTrue(c.category)
            self.assertTrue(c.title)
            self.assertTrue(c.badge)
            self.assertTrue(c.summary)
            self.assertTrue(c.what_is)
            self.assertTrue(c.why_exists)
            self.assertGreaterEqual(len(c.how_it_works), 3)
            self.assertTrue(c.math_rule)
            self.assertTrue(c.practical_example)
            self.assertTrue(c.diagram_ascii)
            self.assertGreater(len(c.related_flows), 0)
            self.assertGreater(len(c.related_laws), 0)

    def test_get_by_id_and_search(self) -> None:
        """Testa busca por ID exato e busca textual por palavras-chave."""
        c = self.catalog.get_by_id("novacao-ccp")
        self.assertIsNotNone(c)
        self.assertEqual(c.id, "novacao-ccp")
        self.assertIn("Lei nº 10.214/2001", c.title)

        # Busca insensível a maiúsculas/minúsculas
        search_res = self.catalog.search("waterfall")
        self.assertEqual(len(search_res), 1)
        self.assertEqual(search_res[0].id, "default-waterfall")

        # Busca por lei formal
        search_law = self.catalog.search("LAW-104A")
        self.assertTrue(any(c.id == "leilao-moc-freeze" for c in search_law))

    def test_filter_by_category(self) -> None:
        """Testa filtragem de conceitos por categoria operacional."""
        trading_concepts = self.catalog.filter_by_category("PUMA TRADING")
        self.assertGreaterEqual(len(trading_concepts), 2)
        ids = {c.id for c in trading_concepts}
        self.assertIn("leilao-moc-freeze", ids)
        self.assertIn("tuneis-circuit-breaker", ids)

    def test_simulate_all_concepts(self) -> None:
        """Valida que todos os 10 conceitos possuem simuladores operacionais executáveis."""
        for c in CONCEPTS:
            res = self.catalog.simulate(c.id)
            self.assertNotIn("error", res, f"Falha na simulação de {c.id}")
            self.assertEqual(res["concept_id"], c.id)
            self.assertIn("law_verified", res)
            self.assertIn("title", res)


if __name__ == "__main__":
    unittest.main()
