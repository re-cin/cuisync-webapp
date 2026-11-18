"""Source registry, market gating, label status and fixture safety tests."""
from pathlib import Path
import json
import unittest

from regional_allergens import COUNTRY_SPECS, regional_allergen_alerts, regional_market_catalog

EXPECTED = {'CAMBODIA','CHINA','INDIA','INDONESIA','JAPAN','MALAYSIA',
            'MYANMAR','PHILIPPINES','SAUDI ARABIA','SOUTH KOREA',
            'THAILAND','TURKIYE','VIETNAM'}

class RegionalThirteenMarketTests(unittest.TestCase):
    def test_country_set_exactly_matches_900_recipe_dataset(self):
        recipes = json.loads((Path(__file__).parents[1] / 'recipes.json').read_text('utf-8'))
        self.assertEqual(len(recipes), 900)
        self.assertEqual({r['country'] for r in recipes}, EXPECTED)
        self.assertEqual(set(COUNTRY_SPECS), EXPECTED)
        self.assertEqual(len(regional_market_catalog()), 13)
        self.assertEqual({m['country'] for m in regional_market_catalog()}, EXPECTED)

    def test_unverified_or_future_frameworks_emit_no_country_specific_findings(self):
        recipe = {'ingredient_text':'chicken breast, tomato, buckwheat, soy sauce, egg'}
        for country in ('CAMBODIA','CHINA','PHILIPPINES'):
            with self.subTest(country=country):
                self.assertEqual(regional_allergen_alerts(recipe,market=country),[])
                self.assertEqual(COUNTRY_SPECS[country]['coverage'],'reference_only')

    def test_japan_2026_mandatory_and_recommended(self):
        items = regional_allergen_alerts({'ingredient_text': 'cashew, walnuts, chicken breast, pistachio, buckwheat'}, market='JAPAN')
        statuses = {r['name']:r['label_status'] for r in items}
        self.assertEqual(statuses['Cashew'],'mandatory')
        self.assertEqual(statuses['Walnut'],'mandatory')
        self.assertEqual(statuses['Buckwheat'],'mandatory')
        self.assertEqual(statuses['Chicken'],'recommended')
        self.assertEqual(statuses['Pistachio'],'recommended')
        self.assertEqual(len(COUNTRY_SPECS['JAPAN']['categories']), 29)

    def test_korea_additional_food_categories(self):
        found = {(x['name'],x['classification']) for x in regional_allergen_alerts(
            {'ingredient_text':'chicken breast, beef, pork belly, tomato paste, buckwheat flour, squid'}, market='SOUTH KOREA')}
        self.assertTrue({('Chicken','identified'), ('Beef','identified'),('Pork','identified'),
                ('Tomato','identified'),('Buckwheat','identified'),('Squid','identified')} <= found)

    def test_thai_sesame_2026_but_not_sulphite_as_current_allergen_group(self):
        names = {x['name'] for x in regional_allergen_alerts(
            {'ingredient_text':'sesame seeds, sodium metabisulfite, peanut'},market='THAILAND')}
        self.assertIn('Sesame',names)
        self.assertNotIn('Sulphites',names)
        self.assertEqual(COUNTRY_SPECS['THAILAND']['source_url'].startswith('https://'),True)

    def test_malaysia_uses_its_actual_narrower_list(self):
        found = {x['name'] for x in regional_allergen_alerts(
            {'ingredient_text':'soy sauce, almond, egg, fish sauce'},market='MALAYSIA')}
        self.assertIn('Soy', found)
        self.assertIn('Egg', found)
        self.assertIn('Fish', found)
        self.assertNotIn('Tree nuts', found)

    def test_sulphite_requires_unknown_threshold_label(self):
        found = regional_allergen_alerts({'ingredient_text':'sodium metabisulfite'},market='MYANMAR')
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['name'],'Sulphites')
        self.assertEqual(found[0]['classification'],'possible')
        self.assertEqual(found[0]['label_status'],'conditional')

    def test_no_broad_substring_false_positives_and_market_whitelist(self):
        x = {'ingredient_text':'eggplant, chicken stock, coconut milk, soyabean flavour, peanut-free, fresh tomato leaf'}
        self.assertEqual(regional_allergen_alerts(x,market='SOUTH KOREA'),[])
        self.assertEqual(regional_allergen_alerts(x,market='JAPAN'),[])
        self.assertEqual(regional_allergen_alerts(x,market='SINGAPORE'),[])

    def test_only_13_country_references_no_external_frameworks_and_no_unsafe_urls(self):
        data = {'ingredient_text':'egg, shrimp, milk, peanut, soy sauce, chicken breast, buckwheat'}
        for x in regional_allergen_alerts(data):
            self.assertIn(x['country'], EXPECTED)
            self.assertTrue(x['source_url'].startswith('https://'))
            self.assertIn(x['classification'], ('identified','possible'))
            self.assertEqual(x['type'], 'regional_labeling_reference')
            self.assertTrue(x['from'])

    def test_all_dishes_stay_in_same_13_markets(self):
        recipes = json.loads((Path(__file__).parents[1]/'recipes.json').read_text('utf-8'))
        for recipe in recipes:
            alerts=regional_allergen_alerts(recipe)
            self.assertTrue(all(a['country'] in EXPECTED and a['from'] for a in alerts))
            self.assertTrue(all(a['name'] in COUNTRY_SPECS[a['country']].get('categories',{}) for a in alerts))

if __name__ == '__main__':
    unittest.main()

class SulphiteThresholdRegressions(unittest.TestCase):
    def test_no_numeric_sulphite_claims_without_ingredient_quantities(self):
        for market in ('SOUTH KOREA','INDONESIA','INDIA','TURKIYE','MYANMAR','VIETNAM','SAUDI ARABIA'):
            alerts=regional_allergen_alerts({'ingredient_text':'sodium metabisulphite'},market=market)
            sulphites=[a for a in alerts if a['name']=='Sulphites']
            if sulphites:
                self.assertTrue(all(a['classification']=='possible' for a in sulphites))
