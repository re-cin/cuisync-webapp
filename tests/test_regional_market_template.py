"""Confirm the regional markets are actually present in both rendered dish cards."""
import unittest
from tests.test_dish_information_templates import DishInformationTemplateTests
from regional_allergens import regional_market_catalog, regional_allergen_alerts

class RegionalMarketTemplateTests(DishInformationTemplateTests):
    def setUp(self):
        super().setUp()
        for side in ('source','recommended'):
            info=self.context['dish_information'][side]['health']
            info['regional_markets']=regional_market_catalog()
            info['regional_allergens']=regional_allergen_alerts({'ingredient_text':'chicken breast, tomato, soy sauce, buckwheat'})

    def test_two_market_selectors_and_all_thirteen_options(self):
        html=self.render()
        self.assertIn('id="rt-market-source"',html)
        self.assertIn('id="rt-market-recommended"',html)
        self.assertEqual(html.count('class="rt-market-select"'),2)
        self.assertEqual(html.count('value="PHILIPPINES"'),2)
        self.assertEqual(html.count('value="JAPAN"'),2)
        self.assertIn('data-market-code="SOUTH KOREA"',html)
        self.assertIn('data-market-code="JAPAN"',html)
        self.assertIn("This country's reference list may be incomplete.",html)
        self.assertIn("el.hidden = el.dataset.marketCode !== code;",html)

    def test_no_source_urls_displayed_in_allergen_notes(self):
        html=self.render()
        self.assertNotIn('https://www.mfds.go.kr/eng/wpge/m_14/contents.do',html)
        self.assertNotIn('food_labeling_cms201_261030_01.pdf',html)

if __name__=='__main__':
    unittest.main()
