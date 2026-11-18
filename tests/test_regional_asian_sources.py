"""Offline regression checks for Asian allergen and nutrient-source expansion."""
import csv
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regional_allergens import regional_allergen_alerts, COUNTRY_SPECS, regional_market_catalog
from regional_nutrition import (
    SOURCES, reviewed_import_row, eligible_regional_record, approved_regional_records,
)
from import_regional_nutrition import read_rows
from dish_info_common import ingredient_hash
from dish_health import enrich_health, _allergens, seed_allergen_rules
from tests.test_dish_health import MemoryStore


class AsianRegionalTests(unittest.TestCase):
    def test_korean_specific_foods_are_not_inferred_from_substrings(self):
        items = regional_allergen_alerts({"ingredient_text": "chicken breast, tomato, buckwheat flour, chicken stock, eggplant, beef powder"})
        pairs = {(x['name'], x['jurisdiction']) for x in items}
        self.assertIn(('Chicken', 'South Korea (MFDS)'), pairs)
        self.assertIn(('Tomato', 'South Korea (MFDS)'), pairs)
        self.assertIn(('Buckwheat', 'South Korea (MFDS)'), pairs)
        self.assertIn(('Buckwheat', 'Japan (CAA)'), pairs)
        self.assertNotIn(('Beef', 'South Korea (MFDS)'), pairs)
        self.assertFalse(any('stock' in z for x in items for z in x['from']))
        self.assertFalse(any('eggplant' in z for x in items for z in x['from']))

    def test_japan_cashew_and_walnut_and_soba_possible(self):
        items = regional_allergen_alerts({'ingredient_text': 'cashew, walnuts, soba noodle'})
        self.assertIn(('Cashew', 'identified'), {(x['name'], x['classification']) for x in items})
        self.assertIn(('Buckwheat', 'possible', 'Japan (CAA)'),
                      {(x['name'], x['classification'], x['jurisdiction']) for x in items})
        self.assertTrue(all(x['source_url'].startswith('https://') for x in items))

    def test_baseline_fsa_remains_separate(self):
        store = MemoryStore()
        seed_allergen_rules(store)
        groups, unknown = _allergens(['chicken breast'], store.list('ingredient_allergens'))
        self.assertEqual(['chicken breast'], unknown)  # baseline doesn't infer new rules
        self.assertTrue(any(x['name'] == 'Chicken' for x in regional_allergen_alerts({'ingredient_text':'chicken breast'})))

    def sample(self, **overrides):
        values = {
            'country':'PHILIPPINES', 'ingredient_name':'chicken breast',
            'food_name':'chicken breast', 'food_id':'SOURCE-123',
            'source_code':'PHILFCT', 'source_url':'https://i.fnri.dost.gov.ph/login/fct',
            'basis':'per_100g', 'protein_g':'12.2', 'calcium_mg':'8',
        }
        values.update(overrides)
        return values

    def test_strict_import_requires_exact_identity_provenance_and_basis(self):
        x = reviewed_import_row(self.sample(), approved=True)
        self.assertTrue(x['approved'])
        self.assertEqual('PHILFCT',x['source_code'])
        self.assertEqual(12.2,x['nutrients']['values']['protein']['value'])
        self.assertNotIn('fiber',x['nutrients']['values'])  # Missing != zero
        for edits in (
            {'food_name':'chicken meat'}, {'country':'JAPAN'},
            {'source_url':'https://evil.example/path'}, {'source_url':'http://i.fnri.dost.gov.ph/login/fct'},
            {'protein_g':'NaN'}, {'protein_g':'-4'}, {'basis':'per_serving'},
            {'protein_g':'', 'calcium_mg':''},
        ):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                reviewed_import_row(self.sample(**edits), approved=True)

    def test_unapproved_and_mismatched_regional_rows_are_not_used(self):
        row = reviewed_import_row(self.sample())
        self.assertIsNone(eligible_regional_record(row,'chicken breast','PHILIPPINES'))
        row['approved'] = True
        self.assertIsNone(eligible_regional_record(row,'chicken leg','PHILIPPINES'))
        self.assertIsNone(eligible_regional_record(row,'chicken breast','JAPAN'))
        self.assertEqual('verified', eligible_regional_record(row,'chicken breast','PHILIPPINES')['match_status'])

    def test_csv_preview_parsing_keeps_existing_rows_and_validates_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder)/'regional.csv'
            with file.open('w',newline='',encoding='utf8') as out:
                writer = csv.DictWriter(out,fieldnames=list(self.sample()))
                writer.writeheader()
                writer.writerow(self.sample())
            rows = read_rows(file)
            self.assertEqual(1,len(rows))
            self.assertFalse(rows[0]['approved'])
            with file.open('a',newline='',encoding='utf8') as out:
                writer=csv.DictWriter(out,fieldnames=list(self.sample()))
                writer.writerow(self.sample())
            with self.assertRaises(ValueError):
                read_rows(file)

    def test_approved_regional_record_preferred_without_usda_api_call(self):
        store=MemoryStore()
        recipe={'recipe_id':44,'title':'test adobo', 'country':'PHILIPPINES',
                'ingredient_text':'chicken breast'}
        row=reviewed_import_row(self.sample(),approved=True)
        store.regional_nutrition_matches=lambda names,country:[row]  # one batch per recipe
        with patch('dish_health._nutrition_record',side_effect=AssertionError('should not call USDA')):
            result=enrich_health(recipe,store)
        self.assertEqual(1,result['matched_ingredient_count'])
        self.assertEqual('verified',result['nutrition_status'])
        self.assertTrue(any(x['evidence_type']=='regional_per_100g' for x in result['potential_benefits']))
        self.assertTrue(any(x['url']==row['source_url'] for x in result['sources']))
        self.assertEqual([],store.rows.get('ingredient_nutrition',[]))

    def test_regional_does_not_override_other_country(self):
        row=reviewed_import_row(self.sample(),approved=True)
        store=MemoryStore()
        store.regional_nutrition_matches=lambda names,country:[row]
        self.assertEqual({},approved_regional_records(store,['chicken breast'],'JAPAN'))

    def test_all_900_recipes_never_create_unsupported_regional_flags(self):
        import json
        recipes=json.loads((Path(__file__).parents[1] / 'recipes.json').read_text('utf8'))
        self.assertEqual(900,len(recipes))
        for recipe in recipes:
            with self.subTest(recipe=recipe['recipe_id']):
                alerts=regional_allergen_alerts(recipe)
                self.assertTrue(all(a['classification'] in {'identified','possible'} for a in alerts))
                self.assertTrue(all(a['type']=='regional_labeling_reference' for a in alerts))
                self.assertTrue(all(a['country'] in COUNTRY_SPECS for a in alerts))
                self.assertTrue(all(a['from'] for a in alerts))

if __name__=='__main__':
    unittest.main()
