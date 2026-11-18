"""Ensure the lightweight allergen UI still shows both dishes and accurate statuses."""
import unittest
from tests.test_dish_information_templates import DishInformationTemplateTests
from regional_allergens import regional_market_catalog, regional_allergen_alerts


class AllergenUILayoutTests(DishInformationTemplateTests):
    def setUp(self):
        super().setUp()
        source = self.context['dish_information']['source']['health']
        source['potential_allergens'] = [
            {'name': 'Soy', 'classification': 'identified', 'from': ['soy sauce'], 'avoid': True},
            {'name': 'Wheat', 'classification': 'possible', 'from': ['soy sauce'], 'avoid': False},
        ]
        for side in ('source', 'recommended'):
            health = self.context['dish_information'][side]['health']
            health['regional_markets'] = regional_market_catalog()
            health['regional_allergens'] = regional_allergen_alerts({'ingredient_text': 'soy sauce, chicken breast'})

    def test_renders_separate_identified_and_possible_groups(self):
        html = self.render()
        self.assertIn('rt-allergen-group--identified', html)
        self.assertIn('rt-allergen-group--possible', html)
        self.assertIn('From: soy sauce', html)
        self.assertIn('On your avoid list.', html)
        self.assertIn('check the package label', html)
        self.assertNotIn('Health Considerations and Potential Allergens', html)

    def test_disclaimer_not_repeated_in_countries(self):
        html = self.render()
        self.assertEqual(html.count("A recipe can't prove a dish is allergen-free."), 2)
        self.assertEqual(html.count('class="rt-market-select"'), 2)
        self.assertEqual(html.count('Check allergens for your country'), 2)

    def test_unavailable_allergens_do_not_leak_stale_findings(self):
        health = self.context['dish_information']['source']['health']
        health['status'] = 'unavailable'
        health['potential_allergens'].append({'name': 'STALE_SECRET_ALLERGEN', 'classification': 'identified'})
        html = self.render()
        self.assertNotIn('STALE_SECRET_ALLERGEN', html)
        self.assertIn("Allergy information isn't available for this dish right now.", html)


if __name__ == '__main__':
    unittest.main()
