import unittest
from unittest.mock import patch
import dish_health as h
from ingredient_matching import select_food
from test_dish_health import MemoryStore
from test_enrichment_report import recipe, Store, nutrition
from enrichment_report import build_report

class RevisionTests(unittest.TestCase):
    def test_specific_reference_and_preparation_boundaries(self):
        for name, description in [('potato','Potatoes, flesh and skin, raw'),('shrimp','Crustaceans, shrimp, raw'),('almond','Nuts, almonds'),('lemongrass','Lemon grass (citronella), raw')]:
            food={'fdcId':123,'description':description,'dataType':'SR Legacy'}
            self.assertIsNotNone(select_food(name,[food]))
            self.assertIsNone(select_food('cooked '+name,[food]))
        self.assertIsNone(select_food('turmeric',[{'fdcId':1,'description':'Spices, turmeric, ground','dataType':'SR Legacy'}]))
    def test_explicit_none_is_not_an_unmatched_rule_or_safety_claim(self):
        s=MemoryStore();h.seed_allergen_rules(s);rules=s.list('ingredient_allergens')
        groups,unknown=h._allergens(['carrot','mystery sauce','salt'],rules)
        self.assertEqual(unknown,['mystery sauce'])
        self.assertIn('assessed_none',{r['classification'] for r in groups})
        groups,unknown=h._allergens(['salted butter','cooked shrimp','sliced almond','soy'],rules)
        self.assertFalse(unknown)
        self.assertTrue(all(r['classification']=='identified' for r in groups))
    def test_completeness_bands_include_all_recipes_and_unknowns(self):
        recipes=[recipe(1,ingredients='garlic'),recipe(2,ingredients='water')]
        report=build_report(recipes,Store(ingredient_nutrition=[nutrition()]))
        self.assertEqual(report['ingredient_coverage_bands']['complete']['covered'],1)
        self.assertEqual(report['ingredient_coverage_bands']['unavailable']['covered'],1)
        self.assertEqual(report['allergen_assessment']['unknown']['covered'],2)
        self.assertEqual(report['background_coverage_by_country']['SOUTH KOREA']['total'],1)
