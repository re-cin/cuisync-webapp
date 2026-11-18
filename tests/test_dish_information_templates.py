"""Render the actual results template without importing the app or using APIs."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, select_autoescape


ROOT = Path(__file__).resolve().parents[1]


class RenderedPage(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.links = []
        self.elements = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag == "a":
            self.links.append(attrs)


class DishInformationTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        environment = Environment(
            loader=FileSystemLoader(ROOT / "templates"),
            autoescape=select_autoescape(("html", "xml")),
        )
        environment.globals.update(
            url_for=lambda endpoint, **kwargs: "/static/" + kwargs["filename"]
            if endpoint == "static" else "/" + endpoint,
            country_label=lambda value: value.title(),
            get_flashed_messages=lambda **kwargs: [],
        )
        cls.template = environment.get_template("results.html")

    def setUp(self):
        self.context = {
            "source_dish": {
                "idx": 0, "title": "Source dish", "country": "PHILIPPINES",
                "alternative_title": "", "image_link": "", "recipe_link": "",
            },
            "recommended_dish": {
                "idx": 1, "title": "Recommended dish", "country": "JAPAN",
                "alternative_title": "", "image_link": "", "recipe_link": "",
            },
            "similarity_score": 80,
            "target_country": "japan", "target_any": False,
            "overview_url": "/similar-dishes", "ingredient_note": "",
            "dish_rank": 1, "rank_total": 2, "rank_scope": "Japan",
            "source_tags": [], "target_tags": [],
            "entities": {
                name: {"shared": [], "source_unique": [], "target_unique": []}
                for name in ("ingredients", "actions", "cookware", "utensils")
            },
            "feature_scores": {name: 0.8 for name in ("ingredients", "actions", "cookware", "utensils")},
            "explanation": "Existing grounded similarity explanation.",
            "explanation_source": "template",
            "dish_information": {
                "source": self.information("source"),
                "recommended": self.information("recommended"),
            },
        }

    @staticmethod
    def information(side):
        return {
            "background": {
                "status": "verified",
                "historical_background": f"Reviewed history for {side}.",
                "cultural_background": f"Reviewed culture for {side}.",
                "message": "",
                "sources": [{"title": f"Background reference {side}", "url": f"https://example.org/{side}/background"}],
            },
            "health": {
                "status": "verified", "nutrition_status": "verified",
                "potential_benefits": [{
                    "ingredient": "Chicken", "text": f"Ingredient note for {side}.",
                    "source_url": f"https://example.org/{side}/nutrition",
                }],
                "health_considerations": [{"ingredient": "Soy sauce", "text": "Check the product label.", "source_url": "https://example.org/label"}],
                "potential_allergens": [{
                    "name": "Soy", "classification": "identified", "from": ["soy sauce"],
                    "source_url": "https://example.org/allergens", "avoid": False,
                }],
                "sources": [{"title": f"Health reference {side}", "url": f"https://example.org/{side}/health"}],
                "message": "", "nutrition_message": "", "allergen_message": "",
            },
        }

    def render(self):
        return self.template.render(**self.context)

    def test_combined_background_nutrition_and_allergens_render_for_both_recipes(self):
        html = self.render()
        for heading in (
            "Nutrition highlights", "Allergen check", "History &amp; Culture",
        ):
            with self.subTest(heading=heading):
                self.assertEqual(html.count(f">{heading}</h4>"), 2)
        for side in ("source", "recommended"):
            self.assertIn(f"Reviewed history for {side}.", html)
            self.assertIn(f"Reviewed culture for {side}.", html)
            self.assertIn(f"Ingredient note for {side}.", html)
        self.assertIn("Existing grounded similarity explanation.", html)
        self.assertIn('aria-label="80 percent similar"', html)

    def test_pending_generated_background_and_attribution_render_for_both_dishes(self):
        for side in ("source", "recommended"):
            background = self.context["dish_information"][side]["background"]
            background.update(
                status="pending", historical_background=f"Generated source-grounded history for {side}.",
                cultural_background=f"Generated source-grounded culture for {side}.",
                message="AI-generated from linked sources; not independently verified.",
                sources=[{"title": f"Public evidence {side}", "url": f"https://example.org/{side}/evidence"}],
            )
        html = self.render()
        for side in ("source", "recommended"):
            self.assertIn(f"Generated source-grounded history for {side}.", html)
            self.assertIn(f"Generated source-grounded culture for {side}.", html)
            self.assertIn(f"Public evidence {side}", html)
            self.assertIn(f"https://example.org/{side}/evidence", html)
        self.assertGreaterEqual(html.count("AI-generated"), 2)
        self.assertIn("AI-Generated Information", html)
        self.assertEqual(2, html.count("It may contain inaccuracies and should be used for general informational purposes only."))
        self.assertNotIn("Awaiting review", html)
        self.assertNotIn("awaiting administrator review", html.lower())

    def test_pending_background_escapes_text_and_hides_unsafe_sources_and_private_metadata(self):
        background = self.context["dish_information"]["source"]["background"]
        background.update(
            status="pending", historical_background='<script>alert("generated history")</script>',
            cultural_background='<img src=x onerror="alert(123)">',
            review_note="PRIVATE REVIEW NOTE", source_evidence=[{"excerpt": "PRIVATE EXCERPT"}],
            sources=[{"title": '<script>alert("source title")</script>', "url": "https://example.org/evidence",
                      "excerpt": "PRIVATE SOURCE EXCERPT"}, {"title": "Unsafe", "url": "javascript:alert(123)"}],
        )
        html = self.render()
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img src=x", html)
        self.assertNotIn('<script>alert("generated history")</script>', html)
        self.assertNotIn('<script>alert("source title")</script>', html)
        self.assertNotIn('javascript:alert(123)', html)
        self.assertNotIn("PRIVATE", html)

    def test_unavailable_health_never_renders_supplied_findings_or_sources(self):
        health = self.context["dish_information"]["source"]["health"]
        health.update(
            status="unavailable",
            potential_benefits=[{"ingredient": "HIDDEN_INGREDIENT", "text": "HIDDEN_BENEFIT"}],
            health_considerations=[{"text": "HIDDEN_CONSIDERATION"}],
            potential_allergens=[{"name": "HIDDEN_ALLERGEN"}],
            sources=[{"title": "HIDDEN_HEALTH_SOURCE", "url": "https://example.org/hidden-health"}],
        )
        html = self.render()
        for marker in ("HIDDEN_INGREDIENT", "HIDDEN_BENEFIT", "HIDDEN_CONSIDERATION", "HIDDEN_ALLERGEN", "HIDDEN_HEALTH_SOURCE", "https://example.org/hidden-health"):
            self.assertNotIn(marker, html)
        self.assertIn("Allergen information isn't available for this dish yet.", html)

    def test_text_is_escaped_and_source_urls_must_use_http_or_https(self):
        self.context["source_dish"]["title"] = 'Dish <script>alert("dish")</script>'
        source = self.context["dish_information"]["source"]
        source["background"]["historical_background"] = '<script>alert("history")</script>'
        source["background"]["sources"].append({"title": "Unsafe source", "url": "javascript:alert(123)"})
        source["health"]["potential_benefits"][0].update(text='<img src=x onerror="alert(123)">', source_url="data:text/html,unsafe")
        html = self.render()
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img src=x", html)
        self.assertNotIn('<script>alert("history")</script>', html)
        self.assertNotIn("javascript:alert(123)", html)
        self.assertNotIn("data:text/html,unsafe", html)
        references = [link for link in RenderedPage(html).links if link.get("href", "").startswith("https://example.org/")]
        self.assertTrue(references)
        for link in references:
            self.assertEqual(link.get("target"), "_blank")
            self.assertTrue({"noopener", "noreferrer"}.issubset(link.get("rel", "").split()))

    def test_missing_records_show_unavailable_messages_without_safety_claims(self):
        self.context.pop("dish_information")
        html = self.render()
        self.assertEqual(html.count("Information unavailable"), 2)
        self.assertIn("Historical and cultural information is unavailable for this dish.", html)
        self.assertIn("Nutrition highlights aren't available for this dish yet.", html)
        self.assertEqual(html.count("Allergen checks cannot confirm a dish is allergy-safe."), 1)
        self.assertNotIn("Not in this dish", html)
        self.assertNotIn("None of these were found", html)

    def test_partial_nutrition_and_allergen_certainty_are_explicit(self):
        health = self.context["dish_information"]["source"]["health"]
        health.update(nutrition_status="partial", nutrition_message="Some ingredients lack verified nutritional matches.")
        health["potential_allergens"][0].update(classification="possible", avoid=True)
        html = self.render()
        self.assertIn("Some ingredients have no matched nutrition data.", html)
        self.assertIn("May be present", html)
        self.assertIn("Listed ingredient", html)
        self.assertIn("On your avoid list", html)
        self.assertIn("not measured amounts or calories per serving", html)

    def test_important_nutrition_only_and_no_health_considerations_or_allergen_links(self):
        health = self.context["dish_information"]["source"]["health"]
        health["potential_benefits"] = [
            {"ingredient": f"Item {i}", "text": f"Nutritional benefit {i}.", "source_url": "https://example.org/nutrients"}
            for i in range(5)
        ]
        health["health_considerations"] = [{"ingredient": "Hidden", "text": "HIDDEN_HEALTH_NOTE", "source_url": "https://example.org/health-consideration"}]
        health["potential_allergens"][0]["source_url"] = "https://example.org/allergen-only-reference"
        health["sources"] = [{"title": "HIDDEN_ALLERGEN_SOURCE", "url": "https://example.org/allergen-source"}]
        html = self.render()
        for i in range(3):
            self.assertIn(f"Nutritional benefit {i}.", html)
        for i in range(3, 5):
            self.assertNotIn(f"Nutritional benefit {i}.", html)
        for forbidden in ("HIDDEN_HEALTH_NOTE", "Health Considerations and Potential Allergens",
                          "HIDDEN_ALLERGEN_SOURCE", "allergen-only-reference", "allergen-source",
                          "Allergen reference", "health-consideration"):
            self.assertNotIn(forbidden, html)
        self.assertIn("Allergen check", html)
        self.assertIn("Allergen checks cannot confirm a dish is allergy-safe.", html)

    def test_combined_background_has_one_disclaimer_per_dish(self):
        for side in ("source", "recommended"):
            self.context["dish_information"][side]["background"]["status"] = "pending"
        html = self.render()
        self.assertEqual(2, html.count("History &amp; Culture"))
        self.assertEqual(2, html.count("AI-Generated Information"))
        for side in ("source", "recommended"):
            self.assertIn(f"Reviewed history for {side}. Reviewed culture for {side}.", html)

    def test_unified_background_and_allergen_only_ui_regression(self):
        background = self.context["dish_information"]["source"]["background"]
        background.update(
            status="pending",
            combined_background="One unified historical and cultural summary.",
        )
        health = self.context["dish_information"]["source"]["health"]
        health["potential_allergens"].extend([
            {"name": "Unassessed allergens", "classification": "unknown", "from": ["mystery"], "avoid": False},
            {"name": "No identified major allergens", "classification": "assessed_none", "from": ["garlic"], "avoid": False},
        ])
        health["allergen_assessment"] = {"assessed_ingredients": 2, "total_ingredients": 3,
                                         "percent": 66.67, "unknown_ingredients": ["mystery"]}
        html = self.render()
        self.assertIn('data-cuisync-background="unified"', html)
        self.assertIn("One unified historical and cultural summary.", html)
        self.assertNotIn("Reviewed history for source. Reviewed culture for source.", html)
        self.assertNotIn("Unassessed allergens</span>", html)
        self.assertNotIn("No identified major allergens</span>", html)
        self.assertIn("Some ingredients could not be checked.", html)
        self.assertIn('content="simplified-health-v4"', html)
        self.assertNotIn("Nutrient reference", html)
        self.assertNotIn("Includes GPT general-knowledge content.", html)
        self.assertNotIn("SOURCE NOTES", html)


    def test_health_guide_and_single_safety_notice_for_both_dishes(self):
        html = self.render()
        self.assertIn('aria-label="How to use nutrition and allergen information"', html)
        self.assertIn("See what nutrients the listed ingredients may provide.", html)
        self.assertIn("Check ingredients that may trigger allergies or match your avoid list.", html)
        self.assertEqual(html.count('class="rt-health-disclaimer"'), 1)
        self.assertEqual(html.count("Allergen checks cannot confirm a dish is allergy-safe."), 1)
        self.assertEqual(html.count('rt-column rt-health-dish'), 2)
        self.assertNotIn('rt-health-note', html)

    def test_unassessed_ingredient_does_not_claim_allergen_free(self):
        health = self.context["dish_information"]["source"]["health"]
        health["potential_allergens"] = [
            {"name": "Unknown", "classification": "unknown", "from": ["mystery"], "avoid": False}
        ]
        health["allergen_assessment"] = {"unknown_ingredients": ["mystery"]}
        html = self.render()
        self.assertIn("No common allergens flagged in the ingredients checked.", html)
        self.assertIn("Some ingredients could not be checked.", html)
        self.assertNotIn("allergen-free", html.lower())

    def test_country_labeling_is_optional_and_uses_market_of_sale(self):
        health = self.context["dish_information"]["source"]["health"]
        health["regional_markets"] = [
            {"country": "PHILIPPINES", "coverage": "specified", "notes": "Market note."},
            {"country": "JAPAN", "coverage": "partial", "notes": "Country note."},
        ]
        health["regional_allergens"] = [{
            "country": "JAPAN", "name": "Buckwheat", "classification": "identified", "from": ["soba"]
        }]
        html = self.render()
        self.assertIn("Check allergen labels by country", html)
        self.assertIn("Country where sold", html)
        self.assertIn("not where the dish comes from", html)
        self.assertIn('data-market-code="JAPAN"', html)
        self.assertIn("This country's reference list may be incomplete.", html)
        self.assertIn("Buckwheat", html)

    def test_both_sides_keep_accessible_headings_and_responsive_column_structure(self):
        html = self.render()
        page = RenderedPage(html)
        by_id = {attrs["id"]: (tag, attrs) for tag, attrs in page.elements if "id" in attrs}
        for side in ("source", "recommended"):
            for section in ("background", "benefits", "allergens"):
                identifier = f"{section}-{side}"
                self.assertEqual(by_id[identifier][0], "h4")
                self.assertTrue(any(tag == "section" and attrs.get("aria-labelledby") == identifier for tag, attrs in page.elements))
        split_columns = [attrs for _, attrs in page.elements if "rt-columns--split" in attrs.get("class", "").split()]
        self.assertEqual(len(split_columns), 2)
        self.assertNotIn("/api/dish-info/", html)


if __name__ == "__main__":
    unittest.main()
