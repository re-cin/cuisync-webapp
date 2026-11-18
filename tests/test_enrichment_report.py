"""Coverage measurements use actual dataset identities and persisted evidence only."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dish_info_common import DATA_VERSION, ingredient_hash
from dish_health import HEALTH_ENRICHMENT_VERSION
from enrichment_report import build_report, render_markdown


def now(days=0):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def recipe(recipe_id=1, title="Kimchi", country="SOUTH KOREA", ingredients="garlic, soy sauce"):
    return {"recipe_id": recipe_id, "title": title, "country": country, "ingredient_text": ingredients}


def nutrition(name="garlic", **changes):
    row = {"normalized_name": name, "ingredient_name": name, "match_status": "verified",
           "source_food_id": "123", "source_url": "https://fdc.nal.usda.gov/food-details/123/nutrients",
           "nutrients": {"basis": "per_100g", "values": {"protein": {"value": 6.0, "unit": "g"}}},
           "data_version": DATA_VERSION, "updated_at": now()}
    row.update(changes)
    return row


def background(name="kimchi", country="SOUTH KOREA", **changes):
    row = {"normalized_name": name, "dish_name": name, "country": country,
           "historical_background": "A supported history.", "cultural_background": "A supported cultural note.",
           "sources": [{"title": "A source", "url": "https://en.wikipedia.org/wiki/Kimchi", "excerpt": "Not printed"}],
           "verification_status": "pending", "data_version": DATA_VERSION, "updated_at": now()}
    row.update(changes)
    return row


def health(item, **changes):
    row = {"recipe_id": item["recipe_id"], "ingredient_hash": ingredient_hash(item),
           "verification_status": "verified", "nutrition_status": "partial", "matched_ingredient_count": 1,
           "ingredient_count": 2, "data_version": DATA_VERSION, "updated_at": now(),
           "potential_benefits": [{"ingredient": "garlic", "text": "A potential contribution.", "source_url": "https://www.fda.gov/nutrition"}],
           "health_considerations": [{"type": "coverage", "matcher_version": HEALTH_ENRICHMENT_VERSION,
                                       "text": "General disclaimer only."}],
           "potential_allergens": [{"name": "Soy", "classification": "identified", "from": ["soy sauce"], "source_url": "https://www.food.gov.uk/allergens"}]}
    row.update(changes)
    return row


def rules():
    return [{"normalized_name": "soy sauce", "ingredient_name": "soy sauce", "approved": True,
             "classification": "identified", "allergens": [{"name": "Soy", "classification": "identified"},
                                                            {"name": "Wheat", "classification": "possible"}],
             "aliases": ["tamari"], "source_url": "https://www.food.gov.uk/allergens", "data_version": DATA_VERSION}]


class Store:
    def __init__(self, **tables):
        self.tables, self.calls = tables, []

    def list(self, table, filters=None):
        self.calls.append((table, filters))
        rows = self.tables.get(table, [])
        return copy.deepcopy([row for row in rows if not filters or all(row.get(key) == value for key, value in filters.items())])


class ReportTests(unittest.TestCase):
    def test_unique_identities_and_feature_denominators_do_not_count_unrelated_rows(self):
        first = recipe()
        second = recipe(2, ingredients="garlic, tamari")
        third = recipe(3, "Rice soup", "JAPAN", "water")
        store = Store(dish_backgrounds=[background(), background("unrelated", "ELSEWHERE")],
                      ingredient_nutrition=[nutrition(), nutrition("irrelevant")], ingredient_allergens=rules(),
                      dish_health_info=[health(first)])
        report = build_report([first, second, third], store)
        self.assertEqual(report["dataset"], {"recipes": 3, "unique_dishes": 2, "unique_ingredients": 4, "countries": 2})
        self.assertEqual(report["coverage"]["historical_background"], {"covered": 1, "total": 2, "percent": 50.0, "denominator": "unique dishes"})
        self.assertEqual(report["coverage"]["potential_nutritional_benefits"]["percent"], 33.33)
        self.assertEqual(report["ingredients"]["matched_usda"], 1)
        self.assertEqual(report["ingredients"]["classified_allergen_ingredients"], 2)
        self.assertEqual(report["ingredients"]["unknown_allergen_ingredients"], 2)
        self.assertEqual(len(store.calls), 4)
        self.assertEqual(report["dishes"]["generated_unverified_records"], 1)
        self.assertEqual(report["dishes"]["reviewed_records"], 0)

    def test_nutrition_partition_explicitly_separates_missing_unmatched_errors_stale(self):
        item = recipe(ingredients="a, b, c, d, e")
        rows = [nutrition("a"), nutrition("b", match_status="ambiguous", match_reason="no_unique_match"),
                nutrition("c", match_status="error", match_reason="rate_limited"),
                nutrition("d", data_version=DATA_VERSION - 1)]
        report = build_report([item], Store(ingredient_nutrition=rows))
        ingredients = report["ingredients"]
        self.assertEqual([ingredients[key] for key in ("matched_usda", "unmatched_usda", "failed_usda_lookups", "stale_usda_records", "missing_usda_records")], [1, 1, 1, 1, 1])
        self.assertEqual(ingredients["not_matched_total"], 4)
        self.assertEqual(report["coverage"]["usda_ingredient_matches"]["percent"], 20.0)
        self.assertEqual(report["api"]["stored_current_failed_lookups"], 1)

    def test_invalid_verified_nutrition_does_not_inflate_match_count(self):
        for changes in ({"source_url": "javascript:bad"}, {"source_url": "https://example.org/unknown"},
                        {"source_food_id": ""}, {"source_food_id": "0"}, {"nutrients": {}},
                        {"nutrients": {"basis": "per_serving", "values": {"protein": {"value": 1, "unit": "g"}}}},
                        {"nutrients": {"basis": "per_100g", "values": {"protein": {"value": float("nan"), "unit": "g"}}}},
                        {"nutrients": {"basis": "per_100g", "values": {"protein": {"value": True, "unit": "g"}}}}):
            with self.subTest(changes=changes):
                result = build_report([recipe(ingredients="garlic")], Store(ingredient_nutrition=[nutrition(**changes)]))
                self.assertEqual(result["ingredients"]["matched_usda"], 0)

    def test_stale_and_wrong_recipe_hash_health_records_are_excluded(self):
        first = recipe()
        changed = recipe(ingredients="peanut, soy sauce")
        result = build_report([changed], Store(dish_health_info=[health(first)]))
        self.assertEqual(result["recipes"]["current_health_records"], 0)
        result = build_report([first], Store(dish_health_info=[health(first, updated_at=now(2))]))
        self.assertEqual(result["recipes"]["current_health_records"], 0)
        self.assertEqual(result["coverage"]["potential_nutritional_benefits"]["covered"], 0)

    def test_disclaimers_alone_are_not_specific_health_coverage(self):
        item = recipe()
        result = build_report([item], Store(dish_health_info=[health(item, potential_allergens=[])]))
        self.assertEqual(result["coverage"]["health_considerations_or_classified_allergens"]["covered"], 0)
        self.assertEqual(result["coverage"]["potential_nutritional_benefits"]["covered"], 1)

    def test_fresh_old_enrichment_version_does_not_count_as_current_health(self):
        item = recipe()
        for coverage in ([], [{"type": "coverage", "matcher_version": HEALTH_ENRICHMENT_VERSION - 1}]):
            with self.subTest(coverage=coverage):
                row = health(item, health_considerations=coverage)
                result = build_report([item], Store(dish_health_info=[row]))
                self.assertEqual(result["recipes"]["current_health_records"], 0)
                self.assertEqual(result["coverage"]["potential_nutritional_benefits"]["covered"], 0)
                self.assertEqual(result["coverage"]["health_considerations_or_classified_allergens"]["covered"], 0)

    def test_backgrounds_distinguish_partial_unavailable_stale_and_reviewed(self):
        items = [recipe(i + 1, title=f"Dish {i}", country="TEST") for i in range(5)]
        rows = [background("dish 0", "TEST", historical_background=""),
                background("dish 1", "TEST", verification_status="unavailable", unavailable_reason="No matching source"),
                background("dish 2", "TEST", data_version=DATA_VERSION - 1),
                background("dish 3", "TEST", verification_status="verified", reviewed_at=now(), review_note="Sources checked")]
        result = build_report(items, Store(dish_backgrounds=rows))
        self.assertEqual(result["dishes"]["with_historical"], 1)
        self.assertEqual(result["dishes"]["with_cultural"], 2)
        self.assertEqual(result["dishes"]["incomplete_one_feature"], 1)
        self.assertEqual(result["dishes"]["without_either"], 3)
        self.assertEqual(result["dishes"]["with_unavailable_information"], 4)
        self.assertEqual(result["dishes"]["missing_records"], 1)
        self.assertEqual(result["dishes"]["stale_records"], 1)
        self.assertEqual(result["dishes"]["reviewed_records"], 1)

    def test_unreviewed_pending_information_counts_but_incorrect_verification_does_not(self):
        row = background(verification_status="verified", reviewed_at=None, review_note="")
        result = build_report([recipe()], Store(dish_backgrounds=[row]))
        self.assertEqual(result["dishes"]["with_both"], 1)
        self.assertEqual(result["dishes"]["reviewed_records"], 0)
        result = build_report([recipe()], Store(dish_backgrounds=[background()]))
        self.assertEqual(result["dishes"]["with_both"], 1)
        self.assertEqual(result["dishes"]["generated_unverified_records"], 1)

    def test_deferred_backgrounds_are_unavailable_but_not_failed_lookups(self):
        reasons = ["Temporary evidence provider cooldown; retry later.",
                   "Temporary encyclopedic search is deferred after provider failure; retry later.",
                   "Temporary evidence provider rate limit; retry later.",
                   "Temporary rate_limit response; retry later."]
        items = [recipe(i + 1, title=f"Dish {i}", country="TEST") for i in range(5)]
        rows = [background(f"dish {i}", "TEST", verification_status="unavailable",
                           unavailable_reason=reason) for i, reason in enumerate(reasons)]
        rows.append(background("dish 4", "TEST", verification_status="unavailable",
                               unavailable_reason="Temporary evidence connection failure; retry later."))
        result = build_report(items, Store(dish_backgrounds=rows))
        self.assertEqual(result["dishes"]["deferred_lookups"], 4)
        self.assertEqual(result["dishes"]["failed_lookups"], 1)
        self.assertEqual(result["api"]["stored_current_deferred_lookups"], 4)
        self.assertEqual(result["api"]["stored_current_failed_lookups"], 1)
        self.assertEqual(result["dishes"]["with_unavailable_information"], 5)
        self.assertEqual(result["dishes"]["without_either"], 5)
        self.assertEqual(result["coverage"]["historical_background"]["covered"], 0)
        self.assertEqual(result["coverage"]["cultural_background"]["covered"], 0)
        self.assertEqual([row["state"] for row in result["dishes"]["unavailable_or_incomplete"]],
                         ["deferred", "deferred", "deferred", "deferred", "error"])
        rendered = render_markdown(result)
        self.assertIn("failed lookups: **1**", rendered)
        self.assertIn("deferred lookups: **4**", rendered)

    def test_unknown_and_withdrawn_allergen_rules_never_become_safe(self):
        withdrawn = dict(rules()[0], approved=False)
        result = build_report([recipe(ingredients="soy sauce, eggplant")], Store(ingredient_allergens=[withdrawn]))
        self.assertEqual(result["ingredients"]["classified_allergen_ingredients"], 0)
        self.assertEqual(result["ingredients"]["unknown_allergen_ingredients"], 2)

    def test_events_are_filtered_to_the_run_and_report_actual_final_failures(self):
        events = [{"run_id": "one", "component": "nutrition_lookup", "status": "error", "provider": "USDA", "reason": "rate_limited", "details": {"api_error_count": 3}},
                  {"run_id": "one", "component": "background_lookup", "status": "error", "provider": "Wikimedia", "reason": "timeout"},
                  {"run_id": "one", "component": "ingredient", "status": "success"},
                  {"run_id": "two", "component": "nutrition_lookup", "status": "error", "provider": "USDA"}]
        report = build_report([recipe()], Store(), events=events, run_id="one")
        self.assertEqual(report["api"]["failed_lookup_events"], 1)
        self.assertEqual(report["api"]["recorded_api_error_attempts"], 3)
        self.assertEqual(report["api"]["rate_limited_or_deferred_events"], 1)
        self.assertEqual(report["api"]["failed_events_by_provider"], {"Wikimedia": 1})
        self.assertEqual(report["api"]["run_event_statuses"], {"error": 2, "success": 1})

    def test_unknown_api_table_does_not_invent_a_run_error_count(self):
        class BaselineStore(Store):
            def list(self, table, filters=None):
                if table == "dish_enrichment_events":
                    raise ValueError("Table not created yet")
                return super().list(table, filters)
        report = build_report([recipe()], BaselineStore(), run_id="not-yet-created")
        self.assertFalse(report["api"]["run_events_available"])
        self.assertEqual(report["api"]["failed_lookup_events"], 0)

    def test_examples_give_actual_two_of_eleven_and_named_remaining_ingredients(self):
        names = ["garlic", "ginger"] + [f"ingredient {i}" for i in range(9)]
        item = recipe(760, ingredients=", ".join(names))
        result = build_report([item], Store(ingredient_nutrition=[nutrition(), nutrition("ginger")]), example_recipe_ids=[760])
        example = result["examples"][0]
        self.assertEqual((example["matched_ingredient_count"], example["ingredient_count"]), (2, 11))
        self.assertEqual(example["ingredient_match_percent"], 18.18)
        self.assertEqual(len(example["unmatched_ingredients"]), 9)

    def test_markdown_is_derived_and_report_is_json_serializable(self):
        report = build_report([recipe()], Store(dish_backgrounds=[background()], ingredient_nutrition=[nutrition()]))
        json.dumps(report, allow_nan=False)
        markdown = render_markdown(report)
        self.assertIn("1 / 1 unique dishes", markdown)
        self.assertIn("Unknown does not mean safe", markdown)
        self.assertIn("generated_unverified", markdown)
        self.assertNotIn("Not printed", markdown)

    def test_empty_dataset_has_zero_denominators_and_zero_percentages(self):
        report = build_report([], Store())
        self.assertTrue(all(metric["percent"] == 0.0 for metric in report["coverage"].values()))
        self.assertEqual(report["examples"], [])


if __name__ == "__main__":
    unittest.main()
