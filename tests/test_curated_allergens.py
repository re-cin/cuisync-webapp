import copy
import unittest
from unittest.mock import patch
from curated_allergens import catalogue, plan_dataset_rules, FRAMEWORK
from allergen_assessment import assess, allergen_patch
from enrich_allergens import nutrition_fields, report
from test_dish_health import MemoryStore
from dish_health import seed_allergen_rules
from test_dish_information import verified_health


class CuratedAllergenTests(unittest.TestCase):
    def setUp(self):
        store=MemoryStore();seed_allergen_rules(store)
        self.old=store.list('ingredient_allergens')
        self.rules=self.old+plan_dataset_rules(list(catalogue())+['mystery sauce','coconut milk','vegan fish sauce'],self.old)

    def test_major_allergen_names_independent_of_usda_context(self):
        for name,group in [('milk','Milk'),('shrimp','Crustaceans'),('almond','Tree nuts'),('duck eggs','Egg'),('soymilk','Soy'),('wheat flour','Wheat'),('salmon fillet','Fish'),('sesame seed','Sesame'),('celery stick','Celery')]:
            with self.subTest(name=name):
                a=assess([name],self.rules)
                self.assertEqual(1,a['assessed'])
                self.assertTrue(any(g['name']==group and g['classification']=='identified' for g in a['groups']))

    def test_plain_food_assessment_is_explicit_not_a_default(self):
        for name in ['chicken','tomato','turmeric','eggplant','rice flour','cilantro','spring onion']:
            self.assertEqual('assessed_none',assess([name],self.rules)['ingredients'][0]['classification'])
        for name in ['mystery sauce','vegan fish sauce','coconut milk','gluten-free mystery noodles','powder']:
            self.assertEqual([name],assess([name],self.rules)['unknown'])

    def test_compounds_remain_possible_and_keep_responsible_ingredients(self):
        a=assess(['kimchi','soy sauce','asafoetida','oil'],self.rules)
        self.assertTrue(any(g['name']=='Fish' and g['classification']=='possible' and 'kimchi' in g['from'] for g in a['groups']))
        self.assertTrue(any(g['name']=='Soy' and g['classification']=='identified' and 'soy sauce' in g['from'] for g in a['groups']))
        self.assertTrue(any(g['name']=='Wheat' and g['classification']=='possible' and 'asafoetida' in g['from'] for g in a['groups']))
        self.assertNotIn('assessed_none',{g['classification'] for g in a['groups']})

    def test_existing_and_withdrawn_rules_are_not_rewritten(self):
        old=copy.deepcopy(self.old)
        old[0]['approved']=False
        before=copy.deepcopy(old)
        additions=plan_dataset_rules(['milk','whole milk','tomato'],old)
        self.assertEqual(before,old)
        self.assertEqual(['tomato'],[r['normalized_name'] for r in additions])

    def test_every_new_rule_retains_scope_evidence_and_framework(self):
        for r in plan_dataset_rules(['kimchi','tomato','powder','whole milk'],self.old):
            self.assertEqual(FRAMEWORK,r['framework'])
            self.assertTrue(r['assessment_note'])
            self.assertTrue(r['evidence'])
            self.assertTrue(r['source_url'].startswith('https://'))
        unknown=plan_dataset_rules(['powder'],self.old)[0]
        self.assertEqual('unknown',unknown['classification'])
        self.assertEqual([],unknown['allergens'])

    @patch('requests.post',side_effect=AssertionError('No nutrition requests allowed'))
    def test_reassessment_preserves_nutrition_verbatim(self,_request):
        recipe={'recipe_id':7,'title':'Sample','country':'TEST','ingredient_text':'milk, tomato, mystery sauce'}
        health=verified_health(recipe)
        original=copy.deepcopy(health)
        updated=dict(health,**allergen_patch(recipe,health,self.rules))
        self.assertEqual(original,health)
        self.assertEqual(nutrition_fields(original),nutrition_fields(updated))
        self.assertEqual(['mystery sauce'],next(n for n in updated['health_considerations'] if n.get('type')=='allergen_assessment')['unknown'])

    def test_report_denominators_and_overlap_are_explicit(self):
        recipes=[{'recipe_id':1,'title':'A','country':'TEST','ingredient_text':'soy sauce, tomato, mystery sauce'},
                 {'recipe_id':2,'title':'B','country':'TEST','ingredient_text':'tomato, kimchi'}]
        r=report(recipes,self.rules)
        self.assertEqual(4,r['total_ingredients'])
        self.assertEqual(3,r['assessed_ingredients'])
        self.assertEqual(75,r['assessment_percent'])
        self.assertEqual(2,r['possible_prevalence'])
        self.assertEqual(1,r['exclusive_counts']['possible'])
        self.assertEqual(1,r['fully_assessed_recipes'])
        self.assertEqual('mystery sauce',r['unknown_ingredients'][0]['ingredient'])

    def test_unsafe_reference_never_produces_assessed_none(self):
        rules=[dict(catalogue()['tomato'],source_url='javascript:bad',evidence=[])]
        self.assertEqual(['tomato'],assess(['tomato'],rules)['unknown'])

if __name__=='__main__':unittest.main()
