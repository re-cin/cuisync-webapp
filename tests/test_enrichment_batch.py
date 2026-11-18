"""Offline batch limits, resumability, provider caching, and refresh deduplication."""
import contextlib
import io
import os
import unittest
from unittest.mock import Mock, patch

with patch("dotenv.load_dotenv", return_value=False):
    import enrich_dish_info as batch
    from dish_information_store import DishStoreError

import dish_health as health
from test_dish_health import MemoryStore, food, response


def recipe(recipe_id, title="Soup", country="TEST", ingredients="spinach"):
    return {"recipe_id": recipe_id, "title": title, "country": country, "ingredient_text": ingredients}


class IngredientRefreshTests(unittest.TestCase):
    @patch("dish_health.requests.post")
    def test_shared_refresh_set_requests_each_normalized_ingredient_once(self, post):
        health._usda_retry_until = 0
        store, refreshed = MemoryStore(), set()
        post.return_value = response([food()])
        with patch.dict(os.environ, {"USDA_API_KEY": "offline-test-key"}):
            first = health.enrich_health(recipe(1), store, refresh=True, refreshed_ingredients=refreshed)
            second = health.enrich_health(recipe(2, ingredients=" SPINACH "), store, refresh=True,
                                          refreshed_ingredients=refreshed)
            third = health.enrich_health(recipe(3), store, refresh=True, refreshed_ingredients=refreshed)
        self.assertEqual(1, post.call_count)
        self.assertEqual({"spinach"}, refreshed)
        self.assertEqual(3, len(store.rows["dish_health_info"]))
        self.assertTrue(all(row["nutrition_status"] == "verified" for row in [first, second, third]))
        self.assertEqual({1, 2, 3}, {r["recipe_id"] for r in store.rows["dish_health_info"]})

    @patch("dish_health.requests.post")
    def test_a_new_batch_set_can_explicitly_refresh_the_same_ingredient(self, post):
        health._usda_retry_until = 0
        store = MemoryStore()
        post.return_value = response([food()])
        with patch.dict(os.environ, {"USDA_API_KEY": "offline-test-key"}):
            health.enrich_health(recipe(1), store, refresh=True, refreshed_ingredients=set())
            health.enrich_health(recipe(1), store, refresh=True, refreshed_ingredients=set())
        self.assertEqual(2, post.call_count)
        self.assertEqual(1, len(store.rows["dish_health_info"]))


class BatchControlTests(unittest.TestCase):
    def setUp(self):
        self.recipes = [recipe(1), recipe(2, title=" soup ", country="test", ingredients="tofu"),
                        recipe(3, title="Soup", country="OTHER")]
        self.store = Mock()
        self.store.list.return_value = self.recipes
        self.seed_patch = patch("dish_health.seed_allergen_rules", return_value=0)
        self.health_patch = patch("dish_health.enrich_health", return_value={
            "verification_status": "verified", "nutrition_status": "partial"})
        self.background_patch = patch("dish_backgrounds.enrich_background", return_value={"verification_status": "pending"})
        self.seed = self.seed_patch.start()
        self.enrich_health = self.health_patch.start()
        self.enrich_background = self.background_patch.start()
        self.addCleanup(self.seed_patch.stop)
        self.addCleanup(self.health_patch.stop)
        self.addCleanup(self.background_patch.stop)
        self.output = io.StringIO()
        self.stdout = contextlib.redirect_stdout(self.output)
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)

    def test_limit_bounds_recipe_work(self):
        self.assertEqual(0, batch.run(self.store, limit=2, delay=0))
        self.assertEqual(2, self.enrich_health.call_count)
        self.assertEqual(1, self.enrich_background.call_count)
        self.assertEqual([1, 2], [call.args[0]["recipe_id"] for call in self.enrich_health.call_args_list])

    def test_same_normalized_dish_country_has_one_background_but_each_recipe_has_health(self):
        self.assertEqual(0, batch.run(self.store, delay=0))
        self.assertEqual(3, self.enrich_health.call_count)
        self.assertEqual(2, self.enrich_background.call_count)
        self.assertEqual([1, 3], [call.args[0]["recipe_id"] for call in self.enrich_background.call_args_list])

    def test_health_mode_never_requests_backgrounds(self):
        self.assertEqual(0, batch.run(self.store, mode="health", delay=0))
        self.assertEqual(3, self.enrich_health.call_count)
        self.enrich_background.assert_not_called()

    def test_background_mode_never_requests_health(self):
        self.assertEqual(0, batch.run(self.store, mode="backgrounds", delay=0))
        self.enrich_health.assert_not_called()
        self.assertEqual(2, self.enrich_background.call_count)

    def test_recipe_id_filter_is_applied_in_store(self):
        self.store.list.return_value = [self.recipes[0]]
        self.assertEqual(0, batch.run(self.store, recipe_id=1, delay=0))
        self.store.list.assert_called_once_with("recipes", {"recipe_id": 1})

    def test_refresh_flag_and_one_shared_ingredient_set_are_passed_through(self):
        self.assertEqual(0, batch.run(self.store, refresh=True, delay=0))
        calls = self.enrich_health.call_args_list
        self.assertTrue(all(call.kwargs["refresh"] for call in calls))
        shared_set = calls[0].kwargs["refreshed_ingredients"]
        self.assertIsInstance(shared_set, set)
        self.assertTrue(all(call.kwargs["refreshed_ingredients"] is shared_set for call in calls))
        self.assertTrue(all(call.kwargs["refresh"] for call in self.enrich_background.call_args_list))

    def test_database_error_stops_before_more_recipes_or_providers_without_leaking_details(self):
        self.enrich_health.side_effect = DishStoreError("PRIVATE database response or key")
        self.assertEqual(1, batch.run(self.store, delay=0))
        self.assertEqual(1, self.enrich_health.call_count)
        self.enrich_background.assert_not_called()
        self.assertIn("stopping safely", self.output.getvalue())
        self.assertNotIn("PRIVATE", self.output.getvalue())


class BatchResumeTests(unittest.TestCase):
    @patch("dish_backgrounds.retrieve_evidence")
    @patch("dish_health.requests.post")
    def test_actual_health_read_failure_caught_in_enricher_still_stops_batch(self, post, evidence):
        class FailedHealthRead(MemoryStore):
            def get(self, table, filters):
                if table == "dish_health_info":
                    raise DishStoreError("PRIVATE database lookup details")
                return super().get(table, filters)
        store = FailedHealthRead()
        store.rows["recipes"] = [recipe(1), recipe(2)]
        output = io.StringIO()
        with patch("dish_health.enrich_health", wraps=health.enrich_health) as enrich, contextlib.redirect_stdout(output):
            result = batch.run(store, delay=0)
        self.assertEqual(1, result)
        self.assertEqual(1, enrich.call_count)
        post.assert_not_called()
        evidence.assert_not_called()
        self.assertIn("stopping safely", output.getvalue())
        self.assertNotIn("PRIVATE", output.getvalue())

    @patch("dish_backgrounds.retrieve_evidence")
    @patch("dish_health.requests.post")
    def test_actual_health_write_failure_caught_in_enricher_stops_before_next_recipe(self, post, evidence):
        class FailedNutritionWrite(MemoryStore):
            def upsert(self, table, row, on_conflict):
                if table == "ingredient_nutrition":
                    raise DishStoreError("PRIVATE database save details")
                return super().upsert(table, row, on_conflict)
        health._usda_retry_until = 0
        store = FailedNutritionWrite()
        store.rows["recipes"] = [recipe(1), recipe(2)]
        post.return_value = response([food()])
        with patch.dict(os.environ, {"USDA_API_KEY": "offline-test-key"}), contextlib.redirect_stdout(io.StringIO()), \
                patch("dish_health.enrich_health", wraps=health.enrich_health) as enrich:
            result = batch.run(store, delay=0)
        self.assertEqual(1, result)
        self.assertEqual(1, enrich.call_count)
        self.assertEqual(1, post.call_count)
        evidence.assert_not_called()

    @patch("dish_backgrounds.retrieve_evidence", return_value=[])
    @patch("dish_health.requests.post")
    def test_rerun_uses_database_progress_without_repeating_external_requests(self, post, evidence):
        health._usda_retry_until = 0
        store = MemoryStore()
        store.rows["recipes"] = [recipe(1), recipe(2, ingredients="SPINACH")]
        post.return_value = response([food()])
        with patch.dict(os.environ, {"USDA_API_KEY": "offline-test-key"}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, batch.run(store, delay=0))
            self.assertEqual(1, post.call_count)
            self.assertEqual(1, evidence.call_count)
            self.assertEqual(2, len(store.rows["dish_health_info"]))
            self.assertEqual(1, len(store.rows["dish_backgrounds"]))
            post.reset_mock()
            evidence.reset_mock()
            self.assertEqual(0, batch.run(store, delay=0))
        post.assert_not_called()
        evidence.assert_not_called()
        self.assertEqual(2, len(store.rows["dish_health_info"]))
        self.assertEqual(1, len(store.rows["dish_backgrounds"]))

    @patch("dish_backgrounds.retrieve_evidence", return_value=[])
    @patch("dish_health.requests.post")
    def test_interrupted_limit_can_resume_remaining_recipes_using_persisted_ingredients(self, post, evidence):
        health._usda_retry_until = 0
        store = MemoryStore()
        store.rows["recipes"] = [recipe(1), recipe(2)]
        post.return_value = response([food()])
        with patch.dict(os.environ, {"USDA_API_KEY": "offline-test-key"}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, batch.run(store, limit=1, delay=0))
            self.assertEqual(1, len(store.rows["dish_health_info"]))
            self.assertEqual(0, batch.run(store, delay=0))
        self.assertEqual(2, len(store.rows["dish_health_info"]))
        self.assertEqual(1, post.call_count)
        self.assertEqual(1, evidence.call_count)


if __name__ == "__main__":
    unittest.main()
