"""USDA identity matching and cache-upgrade checks; no live provider calls."""
import copy
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch

from ingredient_matching import MATCHER_VERSION, canonical_identity, query_for, select_food
import dish_health as health
from test_dish_health import MemoryStore, food, response


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"USDA_API_KEY": "offline-test-key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        health.reset_usda_circuit()

    def test_kimchi_terms_match_explicit_usda_descriptions(self):
        cases = {
            "napa chinese cabbage": "Cabbage, chinese (pe-tsai), raw",
            "salt": "Salt, table", "tamari": "Soy sauce made from soy (tamari)",
            "fish sauce": "Sauce, fish, ready-to-serve", "chili powder": "Spices, chili powder",
            "scallion": "Onions, spring or scallions (includes tops and bulb), raw",
            "green onion": "Green onion, (scallion), bulb and greens, root removed, raw",
            "carrot": "Carrots, raw", "garlic": "Garlic, raw", "ginger": "Ginger root, raw",
        }
        for name, description in cases.items():
            with self.subTest(name=name):
                self.assertIsNotNone(select_food(name, [food(description)]))

    def test_preparation_variety_and_formulation_are_not_erased(self):
        for name, description in [
            ("napa cabbage", "Cabbage, chinese (pak-choi), raw"),
            ("napa cabbage", "Cabbage, napa, leaf, destemmed, raw"),
            ("daikon", "Radishes, red, raw"), ("rice", "Rice, brown, cooked"),
            ("turmeric", "Spices, turmeric, ground"),
            ("ginger", "Spices, ginger, ground"),
            ("scallion", "Onions, young green, tops only"),
            ("soy sauce", "Soy sauce, reduced sodium"),
            ("gochugaru", "Spices, pepper, red or cayenne"),
            ("sesame oil", "Oil, sesame, toasted"),
        ]:
            with self.subTest(name=name, description=description):
                self.assertIsNone(select_food(name, [food(description)]))

    def test_generic_survey_food_allowed_only_for_same_identity(self):
        generic = food("Soy sauce", 2707442, "Survey (FNDDS)")
        tamari = food("Soy sauce made from soy (tamari)", 174278, "SR Legacy")
        self.assertEqual(2707442, select_food("soy sauce", [tamari, generic])["fdcId"])
        self.assertIsNone(select_food("tamari", [generic]))
        self.assertIsNone(select_food("soy sauce", [food("Beef with soy-based sauce", 2, "Survey (FNDDS)")]))

    def test_frequent_exact_equivalents_do_not_accept_other_preparations(self):
        for name, description, identity in [
            ("shallot", "Shallots, raw", 170499),
            ("green chili", "Peppers, hot chili, green, raw", 170497),
            ("ghee", "Butter, Clarified butter (ghee)", 171314),
        ]:
            with self.subTest(name=name):
                selected = select_food(name, [food(description, identity, "SR Legacy")])
                self.assertEqual(identity, selected["fdcId"])
        for name, description in [
            ("shallot", "Shallots, freeze-dried"),
            ("shallot", "Shallots, bulb, peeled, root removed, raw"),
            ("green chili", "Peppers, chili, green, canned"),
            ("green chili", "Peppers, sweet, green, raw"),
            ("green pepper", "Peppers, hot chili, green, raw"),
            ("butter", "Butter, Clarified butter (ghee)"),
            ("ghee", "Butter, salted"),
        ]:
            with self.subTest(name=name, description=description):
                self.assertIsNone(select_food(name, [food(description)]))

    def test_unspecified_staples_do_not_choose_arbitrary_food_defaults(self):
        for name, descriptions in {
            "butter": ["Butter, salted", "Butter, stick, unsalted", "Butter, NFS"],
            "potato": ["Potato, NFS", "Potatoes, boiled, without skin"],
            "water": ["Beverages, water, tap, drinking", "Water, bottled, generic", "Water, NFS"],
        }.items():
            with self.subTest(name=name):
                self.assertIsNone(select_food(name, [food(desc, index + 1) for index, desc in enumerate(descriptions)]))

    def test_tap_water_requires_explicit_tap_identity(self):
        tap = food("Beverages, water, tap, drinking", 173647, "SR Legacy")
        self.assertEqual(173647, select_food("tap water", [tap])["fdcId"])
        self.assertIsNone(select_food("water", [tap]))
        self.assertIsNone(select_food("drinking water", [tap]))
        self.assertIsNone(select_food("mineral water", [tap]))
        self.assertEqual("water", query_for("water"))
        self.assertEqual("water tap drinking", query_for("tap water"))

    def test_duplicate_equivalent_foods_choose_stable_representative(self):
        candidates = [food(fdc_id=9), food(fdc_id=3), food(fdc_id=1, data_type="SR Legacy")]
        self.assertEqual(3, select_food("spinach", candidates)["fdcId"])
        self.assertEqual(3, select_food("spinach", candidates[::-1])["fdcId"])

    def test_branded_products_and_unknown_food_types_never_accepted(self):
        self.assertIsNone(select_food("soy sauce", [food("Soy sauce", data_type="Branded")]))
        self.assertIsNone(select_food("soy sauce", [dict(food("Soy sauce"), brandOwner="Brand")]))

    def test_daikon_and_cilantro_queries_use_validated_equivalent_names(self):
        self.assertEqual("radishes oriental raw", query_for("daikon"))
        self.assertIsNotNone(select_food("daikon", [food("Radishes, oriental, raw")]))
        self.assertIsNotNone(select_food("cilantro", [food("Coriander (cilantro) leaves, raw")]))
        self.assertIsNone(select_food("cilantro", [food("Spices, coriander leaf, dried")]))

    @patch("dish_health.requests.post")
    def test_old_negative_cache_retried_and_audit_saved(self, post):
        store = MemoryStore()
        store.upsert("ingredient_nutrition", {"normalized_name": "tamari", "ingredient_name": "tamari",
                     "match_status": "ambiguous", "match_reason": "old_matcher", "nutrients": {},
                     "data_version": 1}, "normalized_name")
        post.return_value = response([food("Soy sauce made from soy (tamari)", 174278)])
        row = health._nutrition_record("tamari", store)
        self.assertEqual("verified", row["match_status"])
        self.assertEqual(MATCHER_VERSION, row["nutrients"]["matcher_version"])
        self.assertEqual("tamari", row["nutrients"]["input_name"])
        self.assertIn("matched_description", row["nutrients"])
        self.assertIn("equivalence_source", row["nutrients"])
        self.assertEqual(1, post.call_count)

    @patch("dish_health.requests.post")
    def test_current_negative_cache_does_not_repeat_lookup(self, post):
        store = MemoryStore()
        post.return_value = response([])
        health._nutrition_record("unmatched special paste", store)
        health._nutrition_record("unmatched special paste", store)
        self.assertEqual(1, post.call_count)

    @patch("dish_health.time.sleep")
    @patch("dish_health.requests.post")
    def test_retry_after_is_respected_or_deferred(self, post, sleep):
        limited = response(status=429)
        limited.headers = {"Retry-After": "120"}
        post.return_value = limited
        row = health._nutrition_record("spinach", MemoryStore())
        self.assertEqual("error", row["match_status"])
        self.assertEqual("rate_limited", row["match_reason"])
        self.assertGreater(health.usda_retry_seconds(), 100)
        self.assertEqual(1, post.call_count)
        sleep.assert_not_called()

    @patch("dish_health.time.sleep")
    @patch("dish_health.requests.post")
    def test_temporary_server_failure_retries_then_succeeds(self, post, sleep):
        post.side_effect = [response(status=503), response([food()])]
        row = health._nutrition_record("spinach", MemoryStore())
        self.assertEqual("verified", row["match_status"])
        self.assertEqual(2, post.call_count)
        sleep.assert_called_once_with(1)

    def test_old_health_without_new_coverage_metadata_is_not_reused(self):
        self.assertFalse(health.health_cache_valid({"nutrition_status": "verified", "data_version": 1,
                        "updated_at": datetime.now(timezone.utc).isoformat(), "health_considerations": []}))

    @patch("dish_health.requests.post")
    def test_zero_remaining_quota_defers_next_ingredient_without_another_request(self, post):
        success = response([food()])
        success.headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "120"}
        post.return_value = success
        store = MemoryStore()
        self.assertEqual("verified", health._nutrition_record("spinach", store)["match_status"])
        next_row = health._nutrition_record("carrot", store)
        self.assertEqual("rate_limited", next_row["match_reason"])
        self.assertEqual(1, post.call_count)
        self.assertEqual(0, health.usda_quota_remaining())

    @patch("dish_health.requests.post")
    def test_recovered_api_error_event_counts_only_actual_failed_attempt(self, post):
        store = MemoryStore()
        events = []
        store.record_event = lambda *args, **kwargs: events.append((args, kwargs))
        post.side_effect = [response(status=503), response([food()])]
        with patch("dish_health.time.sleep"):
            self.assertEqual("verified", health._nutrition_record("spinach", store)["match_status"])
        self.assertEqual(1, sum(event[1]["details"]["api_error_count"] for event in events))
        self.assertNotIn("offline-test-key", str(events))


if __name__ == "__main__":
    unittest.main()
