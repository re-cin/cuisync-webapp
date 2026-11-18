"""Expanded Asian ingredient rules identify uncertainty rather than safety."""
import unittest

import dish_health as health
from test_dish_health import MemoryStore


class AllergenExpansionTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        health.seed_allergen_rules(self.store)
        self.rules = self.store.list("ingredient_allergens", {"approved": True})

    def classify(self, name):
        rows, unknown = health._allergens([name], self.rules)
        return {(row["name"], row["classification"]) for row in rows}, unknown

    def test_seafood_and_fermented_regional_names(self):
        for name, allergen in [("saeujeot", "Crustaceans"), ("dried shrimp", "Crustaceans"),
                               ("prahok", "Fish"), ("ikan bilis", "Fish"), ("tobiko", "Fish"),
                               ("squid ink", "Molluscs"), ("white sesame seed", "Sesame"),
                               ("firm tofu", "Soy")]:
            with self.subTest(name=name):
                pairs, unknown = self.classify(name)
                self.assertIn((allergen, "identified"), pairs)
                self.assertFalse(unknown)

    def test_compound_formulations_are_possible_not_universally_confirmed(self):
        for name, allergen in [("gochujang", "Soy"), ("hoisin sauce", "Wheat"),
                               ("dashi stock granules", "Fish"), ("soba", "Wheat")]:
            pairs, _ = self.classify(name)
            self.assertIn((allergen, "possible"), pairs)
            self.assertNotIn((allergen, "identified"), pairs)

    def test_vegetarian_or_gluten_free_qualifiers_not_stripped(self):
        for name in ["vegetarian fish sauce", "vegan oyster sauce", "gluten-free hoisin sauce",
                     "soy-free tofu", "mystery paste", "rice flour", "eggplant", "coconut milk"]:
            rows, unknown = health._allergens([name], self.rules)
            self.assertEqual([name], unknown)
            self.assertTrue(all(row["classification"] == "unknown" for row in rows))

    def test_seed_expansion_preserves_withdrawn_existing_decisions(self):
        row = self.store.get("ingredient_allergens", {"normalized_name": "soy curd"})
        row["approved"] = False
        self.store.upsert("ingredient_allergens", row, "normalized_name")
        self.assertEqual(0, health.seed_allergen_rules(self.store))
        self.assertFalse(self.store.get("ingredient_allergens", {"normalized_name": "soy curd"})["approved"])


if __name__ == "__main__":
    unittest.main()
