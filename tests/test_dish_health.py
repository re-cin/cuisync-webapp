"""Offline health enrichment checks: no credentials or live services required."""
import copy
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import Mock, patch

import requests

import dish_health as health
from dish_info_common import DATA_VERSION, ingredient_hash


class MemoryStore:
    def __init__(self):
        self.rows = {}
        self.writes = []

    def get(self, table, filters):
        return next((copy.deepcopy(r) for r in self.rows.get(table, [])
                     if all(r.get(k) == v for k, v in filters.items())), None)

    def list(self, table, filters=None):
        return [copy.deepcopy(r) for r in self.rows.get(table, [])
                if all(r.get(k) == v for k, v in (filters or {}).items())]

    def upsert(self, table, row, on_conflict):
        row = copy.deepcopy(row)
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        keys = on_conflict.split(",")
        rows = self.rows.setdefault(table, [])
        rows[:] = [r for r in rows if not all(r.get(k) == row.get(k) for k in keys)]
        rows.append(row)
        self.writes.append((table, row))
        return copy.deepcopy(row)


def food(description="Spinach, raw", fdc_id=42, data_type="Foundation", nutrients=None):
    return {"description": description, "fdcId": fdc_id, "dataType": data_type,
            "foodNutrients": nutrients if nutrients is not None else [
                {"nutrientId": 1003, "unitName": "G", "value": 2.8},
                {"nutrientId": 1079, "unitName": "G", "value": 2.2},
                {"nutrientId": 1093, "unitName": "MG", "value": 80},
            ]}


def response(foods=None, status=200):
    result = Mock(status_code=status)
    result.json.return_value = {"foods": foods or []}
    return result


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"USDA_API_KEY": "offline-test-key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        health._usda_retry_until = 0
        self.store = MemoryStore()
        self.recipe = {"recipe_id": 1, "title": "Test", "country": "TEST", "ingredient_text": "spinach"}

    def test_seed_idempotent_and_preserves_review_decisions(self):
        count = health.seed_allergen_rules(self.store)
        self.assertGreater(count, 20)
        self.assertEqual(0, health.seed_allergen_rules(self.store))
        soy = self.store.get("ingredient_allergens", {"normalized_name": "soy sauce"})
        soy["approved"] = False
        self.store.upsert("ingredient_allergens", soy, "normalized_name")
        health.seed_allergen_rules(self.store)
        self.assertFalse(self.store.get("ingredient_allergens", {"normalized_name": "soy sauce"})["approved"])

    @patch("dish_health.requests.post")
    def test_compound_allergens_keep_identified_and_possible_separate(self, post):
        health.seed_allergen_rules(self.store)
        with patch.dict(os.environ, {"USDA_API_KEY": ""}):
            row = health.enrich_health(dict(self.recipe, ingredient_text="soy sauce, oyster sauce, curry paste"), self.store)
        pairs = {(a["name"], a["classification"]) for a in row["potential_allergens"]}
        self.assertIn(("Soy", "identified"), pairs)
        self.assertIn(("Wheat", "possible"), pairs)
        self.assertIn(("Molluscs", "identified"), pairs)
        self.assertIn(("Crustaceans", "possible"), pairs)
        self.assertEqual("unavailable", row["nutrition_status"])
        post.assert_not_called()

    @patch("dish_health.requests.post")
    def test_exact_allergen_names_avoid_false_positives(self, post):
        health.seed_allergen_rules(self.store)
        with patch.dict(os.environ, {"USDA_API_KEY": ""}):
            row = health.enrich_health(dict(self.recipe, ingredient_text="eggplant, coconut milk, rice flour"), self.store)
        self.assertTrue(row["potential_allergens"])
        self.assertTrue(all(a["classification"] == "unknown" for a in row["potential_allergens"]))
        self.assertIn("cannot establish allergen safety", str(row["health_considerations"]))
        self.assertEqual("unavailable", row["verification_status"])

    def test_unapproved_rule_does_not_identify_an_allergen(self):
        health.seed_allergen_rules(self.store)
        rule = self.store.get("ingredient_allergens", {"normalized_name": "milk"})
        rule["approved"] = False
        allergens, unknown = health._allergens(["milk"], [rule])
        self.assertEqual(["milk"], unknown)
        self.assertEqual("unknown", allergens[0]["classification"])

    def test_additional_exact_seafood_and_singular_noodle_rules(self):
        health.seed_allergen_rules(self.store)
        rules = self.store.list("ingredient_allergens")
        groups = [(["calamari", "squid meat", "squid rings"], "Molluscs", "identified", health.FSA_ALLERGENS),
                  (["crab meat", "crabmeat", "fresh crab meat"], "Crustaceans", "identified", health.FSA_ALLERGENS),
                  (["soba noodle", "buckwheat noodle"], "Wheat", "possible", health.FSS_ALLERGENS)]
        for names, expected_name, classification, source in groups:
            for name in names:
                with self.subTest(name=name):
                    found, unknown = health._allergens([name], rules)
                    self.assertEqual([], unknown)
                    self.assertEqual([(expected_name, classification, source)],
                                     [(a["name"], a["classification"], a["source_url"]) for a in found])

    def test_qualified_variations_do_not_inherit_new_unqualified_rules(self):
        health.seed_allergen_rules(self.store)
        rules = self.store.list("ingredient_allergens")
        names = ["vegan calamari", "imitation calamari", "vegan crab meat", "imitation crab meat",
                 "gluten-free soba noodle", "gluten free buckwheat noodle", "100 percent buckwheat noodle"]
        found, unknown = health._allergens(names, rules)
        self.assertEqual(names, unknown)
        self.assertTrue(all(a["classification"] == "unknown" for a in found))
        # Existing explicit imitation-crab rule remains distinct from real crab.
        found, unknown = health._allergens(["imitation crab"], rules)
        self.assertEqual([], unknown)
        self.assertIn(("Fish", "identified"), {(a["name"], a["classification"]) for a in found})
        self.assertNotIn(("Crustaceans", "identified"), {(a["name"], a["classification"]) for a in found})

    def test_new_rule_withdrawals_are_preserved_by_seeding(self):
        health.seed_allergen_rules(self.store)
        for name in ["calamari", "crab meat", "soba noodle"]:
            row = self.store.get("ingredient_allergens", {"normalized_name": name})
            row["approved"] = False
            self.store.upsert("ingredient_allergens", row, "normalized_name")
        self.assertEqual(0, health.seed_allergen_rules(self.store))
        for name in ["calamari", "crab meat", "soba noodle"]:
            found, unknown = health._allergens([name], self.store.list("ingredient_allergens"))
            self.assertEqual([name], unknown)
            self.assertTrue(all(a["classification"] == "unknown" for a in found))

    @patch("dish_health.requests.post")
    def test_version_bump_refreshes_allergens_with_cached_nutrition_only(self, post):
        health.seed_allergen_rules(self.store)
        recipe = dict(self.recipe, ingredient_text="calamari")
        self.store.cached_nutrition_only = True
        self.store.upsert("ingredient_nutrition", {
            "normalized_name": "calamari", "ingredient_name": "calamari", "match_status": "ambiguous",
            "match_reason": "no_validated_identity_match", "data_version": DATA_VERSION,
            "nutrients": {"matcher_version": health.MATCHER_VERSION}}, "normalized_name")
        old = {"recipe_id": recipe["recipe_id"], "ingredient_hash": ingredient_hash(recipe),
               "verification_status": "unavailable", "nutrition_status": "unavailable",
               "data_version": DATA_VERSION, "health_considerations": [{
                   "type": "coverage", "matcher_version": health.HEALTH_ENRICHMENT_VERSION - 1}]}
        old = self.store.upsert("dish_health_info", old, "recipe_id,ingredient_hash")
        self.assertFalse(health.health_cache_valid(old))
        row = health.enrich_health(recipe, self.store)
        self.assertTrue(health.health_cache_valid(row))
        self.assertIn(("Molluscs", "identified"), {(a["name"], a["classification"]) for a in row["potential_allergens"]})
        post.assert_not_called()

    @patch("dish_health.requests.post")
    def test_numeric_recipe_id_is_required_without_side_effects(self, post):
        for bad in [None, "not-a-database-id", True, -1]:
            row = health.enrich_health(dict(self.recipe, recipe_id=bad), self.store)
            self.assertEqual("unavailable", row["verification_status"])
        self.assertEqual([], self.store.writes)
        post.assert_not_called()

    @patch("dish_health.requests.post")
    def test_zero_recipe_id_is_valid(self, post):
        post.return_value = response([food()])
        row = health.enrich_health(dict(self.recipe, recipe_id=0), self.store)
        self.assertEqual(0, row["recipe_id"])
        self.assertEqual("verified", row["nutrition_status"])

    @patch("dish_health.requests.post")
    def test_gluten_free_qualifier_is_not_assigned_generic_sauce_rule(self, post):
        health.seed_allergen_rules(self.store)
        with patch.dict(os.environ, {"USDA_API_KEY": ""}):
            row = health.enrich_health(dict(self.recipe, ingredient_text="gluten-free soy sauce"), self.store)
        self.assertTrue(all(a["classification"] == "unknown" for a in row["potential_allergens"]))
        self.assertIn("does not necessarily mean wheat-free", str(row["health_considerations"]))

    @patch("dish_health.requests.post")
    def test_negative_and_error_rows_match_non_null_food_id_schema(self, post):
        for index, mode in enumerate(["missing_key", "no_results", "ambiguous", "timeout", "rate_limit"]):
            health._usda_retry_until = 0
            self.store = MemoryStore()
            post.side_effect = None
            post.return_value = response([])
            if mode == "ambiguous":
                post.return_value = response([food("Spinach, cooked, boiled", 1), food("Spinach, canned", 2)])
            elif mode == "timeout":
                post.side_effect = requests.Timeout()
            elif mode == "rate_limit":
                post.return_value = response(status=429)
            with patch.dict(os.environ, {"USDA_API_KEY": "" if mode == "missing_key" else "offline-test-key"}):
                health.enrich_health(dict(self.recipe, recipe_id=index), self.store)
            record = self.store.rows["ingredient_nutrition"][0]
            self.assertEqual("", record["source_food_id"])
            self.assertIsInstance(record["nutrients"], dict)
            self.assertIsInstance(record["match_reason"], str)
            self.assertEqual(DATA_VERSION, record["data_version"])

    @patch("dish_health.requests.post")
    def test_usda_verified_evidence_is_internal_not_dish_quantities(self, post):
        post.return_value = response([food()])
        row = health.enrich_health(self.recipe, self.store)
        self.assertEqual("verified", row["nutrition_status"])
        self.assertEqual(1, row["matched_ingredient_count"])
        self.assertEqual("verified", row["verification_status"])
        self.assertEqual("spinach", row["potential_benefits"][0]["ingredient"])
        self.assertIn("may contribute dietary fiber", row["potential_benefits"][0]["text"])
        self.assertNotIn("2.8", str(row['potential_benefits']) + str(row['health_considerations']))
        cached = self.store.get("ingredient_nutrition", {"normalized_name": "spinach"})
        self.assertEqual("per_100g", cached["nutrients"]["basis"])
        self.assertEqual("42", cached["source_food_id"])
        self.assertTrue(any("food-details/42" in s["url"] for s in row["sources"]))

    @patch("dish_health.requests.post")
    def test_cached_health_and_normalized_ingredient_reused(self, post):
        post.return_value = response([food()])
        first = health.enrich_health(self.recipe, self.store)
        self.assertEqual(first, health.enrich_health(self.recipe, self.store))
        second_recipe = dict(self.recipe, recipe_id=2)
        health.enrich_health(second_recipe, self.store)
        self.assertEqual(1, post.call_count)

    @patch("dish_health.requests.post")
    def test_recipe_ingredient_hash_invalidates_same_named_recipe(self, post):
        post.side_effect = [response([food()]), response([food("Carrots, raw", 43)])]
        first = health.enrich_health(self.recipe, self.store)
        changed = dict(self.recipe, ingredient_text="carrots")
        second = health.enrich_health(changed, self.store)
        self.assertNotEqual(first["ingredient_hash"], second["ingredient_hash"])
        self.assertEqual(2, len(self.store.rows["dish_health_info"]))
        self.assertEqual("carrots", second["potential_benefits"][0]["ingredient"])

    @patch("dish_health.requests.post")
    def test_ambiguous_and_unavailable_matches_are_negative_cached(self, post):
        post.return_value = response([food("Spinach, cooked, boiled", 1), food("Spinach, canned", 2)])
        first = health.enrich_health(self.recipe, self.store)
        health.enrich_health(dict(self.recipe, recipe_id=2), self.store)
        self.assertEqual("unavailable", first["nutrition_status"])
        self.assertEqual("ambiguous", self.store.rows["ingredient_nutrition"][0]["match_status"])
        self.assertEqual(1, post.call_count)

    def test_no_inappropriate_food_substitutions_or_branded_matches(self):
        self.assertIsNone(health._select_food("soy sauce", [food("Sauce, fish")]))
        self.assertIsNone(health._select_food("spinach", [food("Spinach, cooked, boiled")]))
        self.assertIsNone(health._select_food("rice", [food("Rice, brown, cooked")]))
        self.assertIsNone(health._select_food("spinach", [food(data_type="Branded")]))
        self.assertIsNone(health._select_food("spinach", [dict(food(), brandOwner="Example")]))
        self.assertEqual(42, health._select_food("spinach", [food()])["fdcId"])

    @patch("dish_health.requests.post")
    def test_partial_coverage_is_explicit(self, post):
        post.side_effect = [response([food()]), response([])]
        row = health.enrich_health(dict(self.recipe, ingredient_text="spinach, mystery paste"), self.store)
        self.assertEqual("partial", row["nutrition_status"])
        self.assertEqual(1, row["matched_ingredient_count"])
        self.assertIn("1 of 2", str(row["health_considerations"]))
        self.assertTrue(all(b["ingredient"] == "spinach" for b in row["potential_benefits"]))

    @patch("dish_health.requests.post")
    def test_missing_nutrients_are_unavailable_not_verified(self, post):
        post.return_value = response([food(nutrients=[])])
        row = health.enrich_health(self.recipe, self.store)
        self.assertEqual("unavailable", row["nutrition_status"])
        self.assertEqual([], row["potential_benefits"])
        self.assertEqual("nutrients_missing", self.store.rows["ingredient_nutrition"][0]["match_reason"])

    def test_bad_units_negative_or_nonfinite_values_cannot_support_claims(self):
        result = health._nutrients(food(nutrients=[
            {"nutrientId": 1003, "unitName": "MG", "value": 30},
            {"nutrientId": 1089, "unitName": "MG", "value": -1},
            {"nutrientId": 1079, "unitName": "G", "value": float("nan")},
        ]))
        self.assertEqual({}, result["values"])

    @patch("dish_health.requests.post")
    def test_transient_api_failure_is_short_lived_not_permanent(self, post):
        post.side_effect = requests.Timeout("Do not log a URL with api_key=secret")
        health.enrich_health(self.recipe, self.store)
        health.enrich_health(dict(self.recipe, recipe_id=2), self.store)
        self.assertEqual(3, post.call_count)
        cached = self.store.rows["ingredient_nutrition"][0]
        self.assertEqual("error", cached["match_status"])
        cached["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        post.side_effect = None
        post.return_value = response([food()])
        row = health.enrich_health(dict(self.recipe, recipe_id=3), self.store)
        self.assertEqual("verified", row["nutrition_status"])
        self.assertEqual(4, post.call_count)

    @patch("dish_health.requests.post")
    def test_rate_limit_does_not_fan_out_to_other_ingredients(self, post):
        post.return_value = response(status=429)
        row = health.enrich_health(dict(self.recipe, ingredient_text="spinach, carrot, onion"), self.store)
        self.assertEqual(1, post.call_count)
        self.assertEqual("unavailable", row["nutrition_status"])
        self.assertEqual(3, len(self.store.rows["ingredient_nutrition"]))

    @patch("dish_health.requests.post")
    def test_missing_api_key_does_not_make_calls_and_can_recover(self, post):
        with patch.dict(os.environ, {"USDA_API_KEY": ""}):
            row = health.enrich_health(self.recipe, self.store)
            self.assertEqual("unavailable", row["nutrition_status"])
            post.assert_not_called()
        post.return_value = response([food()])
        recovered = health.enrich_health(dict(self.recipe, recipe_id=2), self.store)
        self.assertEqual("verified", recovered["nutrition_status"])

    @patch("dish_health.requests.post")
    def test_persistence_failure_prevents_external_calls(self, post):
        self.store.get = Mock(side_effect=RuntimeError("private response content"))
        row = health.enrich_health(self.recipe, self.store)
        self.assertEqual("unavailable", row["verification_status"])
        self.assertNotIn("private response", str(row))
        post.assert_not_called()

    @patch("dish_health.requests.post")
    def test_old_data_version_refreshes_cached_food(self, post):
        self.store.upsert("ingredient_nutrition", {"normalized_name": "spinach", "match_status": "unavailable",
                          "data_version": DATA_VERSION - 1}, "normalized_name")
        post.return_value = response([food()])
        row = health.enrich_health(self.recipe, self.store)
        self.assertEqual("verified", row["nutrition_status"])
        self.assertEqual(1, post.call_count)


if __name__ == "__main__":
    unittest.main()
