"""Full-dataset orchestration checks; all database and provider work is mocked."""
import copy
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Never load real .env credentials while importing the store wrapper.
with patch("dotenv.load_dotenv", return_value=False):
    from dish_information_store import DishStoreError
    from enrichment_batch import RunJournal, SnapshotStore, dataset_hash, run_dataset
from dish_info_common import DATA_VERSION, ingredient_hash, normalize


def now():
    return datetime.now(timezone.utc).isoformat()


def recipe(recipe_id, name="Kimchi", country="SOUTH KOREA", ingredients="garlic, soy sauce"):
    return {"recipe_id": recipe_id, "title": name, "country": country, "ingredient_text": ingredients}


class Store:
    KEYS = SnapshotStore.KEYS

    def __init__(self, **tables):
        self.tables = copy.deepcopy(tables)
        self.reads, self.writes = [], []
        self.fail_table = None
        self.counter = 0

    def list(self, table, filters=None):
        self.reads.append(("list", table, copy.deepcopy(filters)))
        return copy.deepcopy([row for row in self.tables.get(table, [])
                              if all(str(row.get(k)) == str(v) for k, v in (filters or {}).items())])

    def get(self, table, filters):
        self.reads.append(("get", table, copy.deepcopy(filters)))
        return next((copy.deepcopy(row) for row in self.tables.get(table, [])
                     if all(str(row.get(k)) == str(v) for k, v in filters.items())), None)

    def upsert(self, table, row, on_conflict):
        if table == self.fail_table:
            raise DishStoreError("Database unavailable; private details must not be logged.")
        self.writes.append(("upsert", table, copy.deepcopy(row)))
        keys = tuple(on_conflict.split(","))
        saved = dict(copy.deepcopy(row), updated_at=now())
        self.counter += 1
        existing = next((item for item in self.tables.setdefault(table, [])
                         if all(str(item.get(k)) == str(saved.get(k)) for k in keys)), None)
        if existing is None:
            saved.setdefault("id", f"row-{self.counter}")
            self.tables[table].append(saved)
        else:
            existing.update(saved)
            saved = existing
        return copy.deepcopy(saved)

    def save_background(self, row, observed):
        self.writes.append(("conditional-background", "dish_backgrounds", copy.deepcopy(observed)))
        current = self.get("dish_backgrounds", {"normalized_name": row["normalized_name"], "country": row["country"]})
        if current and (observed is None or current.get("updated_at") != observed.get("updated_at")):
            return current
        return self.upsert("dish_backgrounds", row, "normalized_name,country")

    def save_run(self, row):
        return self.upsert("dish_enrichment_runs", row, "id")

    def save_event(self, row):
        return self.upsert("dish_enrichment_events", row, "run_id,component,entity_key")


class ProviderHarness:
    """Only cache misses cause simulated upstream work; each result is persisted."""
    def __init__(self):
        self.nutrition_calls, self.background_calls, self.health_calls = [], [], []
        self.nutrition_visits, self.background_visits, self.health_visits = [], [], []

    def nutrition(self, name, store, refresh=False):
        key = normalize(name)
        self.nutrition_visits.append(key)
        row = store.get("ingredient_nutrition", {"normalized_name": key})
        if row and row.get("match_status") == "verified" and not refresh:
            return row
        self.nutrition_calls.append(key)
        return store.upsert("ingredient_nutrition", {"normalized_name": key, "ingredient_name": name,
                             "match_status": "verified", "match_reason": "validated_equivalent", "data_version": DATA_VERSION},
                            on_conflict="normalized_name")

    def background(self, item, store, client=None, refresh=False):
        filters = {"normalized_name": normalize(item["title"]), "country": item["country"]}
        self.background_visits.append(tuple(filters.values()))
        previous = store.get("dish_backgrounds", filters)
        if previous and previous.get("verification_status") == "pending" and not refresh:
            return previous
        self.background_calls.append(tuple(filters.values()))
        row = dict(filters, historical_background="Source-grounded history.", cultural_background="Source-grounded culture.",
                   sources=[{"title": "Evidence", "url": "https://en.wikipedia.org/wiki/Food"}],
                   verification_status="pending", unavailable_reason="", data_version=DATA_VERSION)
        return store.save_background(row, previous)

    def health(self, item, store, **kwargs):
        filters = {"recipe_id": item["recipe_id"], "ingredient_hash": ingredient_hash(item)}
        self.health_visits.append(item["recipe_id"])
        cached = store.get("dish_health_info", filters)
        if cached:
            return cached
        self.health_calls.append(item["recipe_id"])
        return store.upsert("dish_health_info", dict(filters, verification_status="verified", nutrition_status="partial",
                            matched_ingredient_count=1, ingredient_count=2, data_version=DATA_VERSION),
                            on_conflict="recipe_id,ingredient_hash")


def report(recipes, store, **kwargs):
    return {"recipes": len(recipes), "health_rows": len(store.list("dish_health_info")),
            "background_rows": len(store.list("dish_backgrounds")), "run_id": kwargs.get("run_id")}


class SnapshotTests(unittest.TestCase):
    def test_dataset_hash_ignores_order_but_not_recipe_identity_country_or_ingredients(self):
        one, two = recipe(1), recipe(2, "Miso soup", "JAPAN", "tofu, miso")
        original = dataset_hash([one, two])
        self.assertEqual(original, dataset_hash([two, one]))
        self.assertEqual(original, dataset_hash([dict(one, ingredient_text="soy sauce, garlic"), two]))
        for changes in ({"recipe_id": 3}, {"title": "Another dish"}, {"country": "CHINA"}, {"ingredient_text": "shrimp"}):
            self.assertNotEqual(original, dataset_hash([dict(one, **changes), two]))

    def test_snapshot_reads_once_per_table_and_writes_through(self):
        source = Store(ingredient_nutrition=[{"normalized_name": "garlic", "match_status": "verified"}])
        snapshot = SnapshotStore(source)
        self.assertEqual(len(source.reads), 4)
        self.assertEqual(snapshot.get("ingredient_nutrition", {"normalized_name": "garlic"})["match_status"], "verified")
        self.assertEqual(snapshot.list("ingredient_nutrition"), [{"normalized_name": "garlic", "match_status": "verified"}])
        self.assertEqual(len(source.reads), 4)
        saved = snapshot.upsert("ingredient_nutrition", {"normalized_name": "ginger", "match_status": "verified"}, "normalized_name")
        self.assertEqual(snapshot.get("ingredient_nutrition", {"normalized_name": "ginger"}), saved)
        self.assertEqual(source.get("ingredient_nutrition", {"normalized_name": "ginger"}), saved)

    def test_snapshot_keeps_concurrent_background_winner(self):
        winner = {"id": "winner", "normalized_name": "kimchi", "country": "SOUTH KOREA", "updated_at": now(),
                  "verification_status": "verified", "historical_background": "Reviewed winner."}
        source = Store(dish_backgrounds=[winner])
        snapshot = SnapshotStore(source)
        result = snapshot.save_background(dict(winner, historical_background="Slow generated replacement."), observed=None)
        self.assertEqual(result["historical_background"], "Reviewed winner.")
        self.assertEqual(snapshot.get("dish_backgrounds", {"normalized_name": "kimchi", "country": "SOUTH KOREA"}), winner)

    def test_write_failures_set_failed_flag_even_when_caller_catches_them(self):
        source, snapshot = Store(), None
        source.fail_table = "ingredient_nutrition"
        snapshot = SnapshotStore(source)
        try:
            snapshot.upsert("ingredient_nutrition", {"normalized_name": "garlic"}, "normalized_name")
        except DishStoreError:
            pass
        self.assertTrue(snapshot.failed)

    def test_journal_events_persist_and_resume_without_duplicate_checkpoint_rows(self):
        source = Store()
        journal = RunJournal(source, {"id": "run-one", "options": {"mode": "all"}})
        journal.record_event("nutrition_lookup", "garlic", "error", "timeout", {"api_error_count": 1}, "usda")
        events = source.list("dish_enrichment_events", {"run_id": "run-one"})
        resumed = RunJournal(source, {"id": "run-one", "options": {"mode": "all"}}, events)
        resumed.record_event("nutrition_lookup", "garlic", "success", "retry_succeeded", {"api_error_count": 1}, "usda")
        resumed.heartbeat("ingredients", 1, 10)
        stored = source.list("dish_enrichment_events", {"run_id": "run-one"})
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["status"], "success")
        self.assertEqual(stored[0]["details"]["api_error_count"], 2)
        self.assertEqual(source.get("dish_enrichment_runs", {"id": "run-one"})["options"]["processed"], 1)


class DatasetRunTests(unittest.TestCase):
    def setUp(self):
        self.recipes = [recipe(1), recipe(2, ingredients="garlic, tamari"), recipe(3, "Miso soup", "JAPAN", "garlic, miso")]
        self.store, self.providers = Store(), ProviderHarness()

    def run_with(self, **kwargs):
        with ExitStack() as stack:
            stack.enter_context(patch("enrichment_batch.time.sleep", return_value=None))
            stack.enter_context(patch("dish_health.seed_allergen_rules", return_value=0))
            nutrition = stack.enter_context(patch("dish_health._nutrition_record", side_effect=self.providers.nutrition))
            background = stack.enter_context(patch("dish_backgrounds.enrich_background", side_effect=self.providers.background))
            health = stack.enter_context(patch("dish_health.enrich_health", side_effect=self.providers.health))
            stack.enter_context(patch("enrichment_report.build_report", side_effect=report))
            stack.enter_context(patch("enrichment_report.render_markdown", return_value="Measured report"))
            output = stack.enter_context(redirect_stdout(io.StringIO()))
            result = run_dataset(self.store, self.recipes, workers=1, delay=0, **kwargs)
        return result, output.getvalue(), (nutrition, background, health)

    def populate_cached_ingredients(self):
        names = {normalize(name.strip()) for item in self.recipes for name in item["ingredient_text"].split(",")}
        self.store.tables["ingredient_nutrition"] = [{
            "normalized_name": name, "ingredient_name": name, "match_status": "verified",
            "match_reason": "validated_generic_identity", "data_version": DATA_VERSION, "updated_at": now(),
        } for name in names]

    def populate_cached_health(self):
        self.store.tables["dish_health_info"] = [{
            "id": f"health-{item['recipe_id']}", "recipe_id": item["recipe_id"],
            "ingredient_hash": ingredient_hash(item), "verification_status": "verified",
            "nutrition_status": "partial", "matched_ingredient_count": 1,
            "ingredient_count": 2, "data_version": DATA_VERSION, "updated_at": now(),
        } for item in self.recipes]

    def test_unique_ingredient_and_dish_phases_preserve_per_recipe_health(self):
        result, output, calls = self.run_with()
        self.assertEqual(result["recipes"], 3)
        self.assertEqual(set(self.providers.nutrition_calls), {"garlic", "soy sauce", "tamari", "miso"})
        self.assertEqual(len(self.providers.nutrition_calls), 4)
        self.assertEqual(len(self.providers.background_calls), 2)
        self.assertEqual(self.providers.health_calls, [1, 2, 3])
        self.assertEqual(len(self.store.tables["dish_health_info"]), 3)
        health_keys = {(row["recipe_id"], row["ingredient_hash"]) for row in self.store.tables["dish_health_info"]}
        self.assertEqual(len(health_keys), 3)
        self.assertIn("coverage_complete=False", output)
        self.assertEqual(self.store.tables["dish_enrichment_runs"][0]["status"], "partial")

    def test_rerunning_valid_records_does_not_repeat_provider_work(self):
        self.run_with()
        expected = (len(self.providers.nutrition_calls), len(self.providers.background_calls), len(self.providers.health_calls))
        self.run_with()
        self.assertEqual(expected, (len(self.providers.nutrition_calls), len(self.providers.background_calls), len(self.providers.health_calls)))

    def test_report_only_does_not_seed_write_or_call_providers(self):
        result, output, calls = self.run_with(report_only=True)
        self.assertEqual(result["recipes"], 3)
        self.assertEqual(self.store.writes, [])
        self.assertTrue(all(not call.called for call in calls))
        self.assertEqual(output.strip(), "Measured report")

    def test_resume_with_another_dataset_is_rejected_without_any_writes(self):
        self.store.tables["dish_enrichment_runs"] = [{"id": "old", "dataset_hash": dataset_hash([recipe(99)])}]
        with self.assertRaises(DishStoreError), patch("dish_health._nutrition_record") as lookup:
            run_dataset(self.store, self.recipes, resume_run="old", delay=0)
        self.assertEqual(self.store.writes, [])
        lookup.assert_not_called()

    def test_resume_existing_run_reuses_checkpoint_id_and_preserves_events(self):
        self.run_with()
        previous = copy.deepcopy(self.store.tables["dish_enrichment_runs"][0])
        before_events = len(self.store.tables["dish_enrichment_events"])
        result, output, _ = self.run_with(resume_run=previous["id"])
        self.assertEqual(result["run_id"], previous["id"])
        self.assertEqual(len(self.store.tables["dish_enrichment_runs"]), 1)
        self.assertEqual(len(self.store.tables["dish_enrichment_events"]), before_events)
        self.assertIn("RUN_FINISHED=" + previous["id"], output)

    def test_health_mode_does_not_call_background_sources(self):
        _, _, calls = self.run_with(mode="health")
        self.assertEqual(calls[1].call_count, 0)
        self.assertEqual(len(self.providers.health_calls), 3)

    def test_background_mode_does_not_call_usda_or_health(self):
        _, _, calls = self.run_with(mode="backgrounds")
        self.assertEqual(calls[0].call_count, 0)
        self.assertEqual(calls[2].call_count, 0)
        self.assertEqual(len(self.providers.background_calls), 2)

    def test_cached_health_only_rejects_missing_ingredient_rows_before_provider_work(self):
        self.populate_cached_ingredients()
        self.store.tables["ingredient_nutrition"] = [row for row in self.store.tables["ingredient_nutrition"]
                                                       if row["normalized_name"] != "miso"]
        with self.assertRaisesRegex(DishStoreError, "Populate missing ingredient lookups"):
            self.run_with(mode="health", cached_health_only=True)
        self.assertEqual(self.providers.nutrition_visits, [])
        self.assertEqual(self.providers.health_visits, [])
        self.assertEqual(self.store.tables["dish_enrichment_runs"][0]["status"], "failed")

    def test_cached_health_only_does_not_lookup_any_fully_cached_ingredients(self):
        self.populate_cached_ingredients()
        _, _, calls = self.run_with(mode="health", cached_health_only=True)
        calls[0].assert_not_called()
        calls[1].assert_not_called()
        self.assertEqual(self.providers.health_visits, [1, 2, 3])

    def test_cached_health_only_refreshes_only_explicit_normalized_names_with_force(self):
        self.populate_cached_ingredients()
        _, _, calls = self.run_with(mode="health", cached_health_only=True,
                                   refresh_ingredients=("GARLIC", "garlic"))
        self.assertEqual(calls[0].call_count, 1)
        self.assertEqual(calls[0].call_args.args[0], "garlic")
        self.assertIs(calls[0].call_args.kwargs["refresh"], True)
        self.assertEqual(self.providers.nutrition_visits, ["garlic"])
        self.assertEqual(self.providers.nutrition_calls, ["garlic"])

    def test_selected_refresh_invalidates_only_affected_health_snapshot_not_database_rows(self):
        self.recipes[2]["ingredient_text"] = "tofu, miso"
        self.populate_cached_ingredients()
        self.populate_cached_health()
        original = self.providers.health
        observations = {}

        def check_health(item, snapshot, **kwargs):
            filters = {"recipe_id": item["recipe_id"], "ingredient_hash": ingredient_hash(item)}
            observations[item["recipe_id"]] = snapshot.get("dish_health_info", filters)
            # Invalidating a cached view must not delete the persistent record.
            self.assertIsNotNone(self.store.get("dish_health_info", filters))
            return original(item, snapshot, **kwargs)

        self.providers.health = check_health
        self.run_with(mode="health", cached_health_only=True, refresh_ingredients=("garlic",))
        self.assertIsNone(observations[1])
        self.assertIsNone(observations[2])
        self.assertIsNotNone(observations[3])
        self.assertEqual(self.providers.health_calls, [1, 2])
        self.assertEqual(len(self.store.tables["dish_health_info"]), 3)
        self.assertEqual(self.store.get("dish_health_info", {"recipe_id": 3})["id"], "health-3")

    def test_cached_health_only_never_uses_api_for_an_expired_error_record(self):
        from datetime import timedelta
        self.recipes = [recipe(1, ingredients="garlic")]
        self.store.tables["ingredient_nutrition"] = [{
            "normalized_name": "garlic", "ingredient_name": "garlic", "match_status": "error",
            "match_reason": "temporary_api_error", "nutrients": {}, "data_version": DATA_VERSION,
            "updated_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        }]
        original_nutrition = copy.deepcopy(self.store.tables["ingredient_nutrition"])
        with patch("dish_health.seed_allergen_rules", return_value=0), \
                patch("dish_health.requests.post", side_effect=AssertionError("Cached-only run must not call USDA")) as post, \
                patch.dict("os.environ", {"USDA_API_KEY": "offline-test-key"}), \
                patch("enrichment_batch.time.sleep", return_value=None), \
                patch("enrichment_report.build_report", side_effect=report), \
                patch("enrichment_report.render_markdown", return_value="Measured report"), \
                redirect_stdout(io.StringIO()):
            import dish_health
            dish_health.reset_usda_circuit()
            run_dataset(self.store, self.recipes, mode="health", workers=1, delay=0, cached_health_only=True)
        post.assert_not_called()
        self.assertEqual(self.store.tables["ingredient_nutrition"], original_nutrition)

    def test_persistence_failure_aborts_before_other_phases_and_marks_run_failed(self):
        self.store.fail_table = "ingredient_nutrition"
        with self.assertRaises(DishStoreError), redirect_stdout(io.StringIO()):
            self.run_with()
        self.assertEqual(self.providers.background_calls, [])
        self.assertEqual(self.providers.health_calls, [])
        self.assertEqual(self.store.tables["dish_enrichment_runs"][0]["status"], "failed")
        self.assertLess(len(self.providers.nutrition_calls), 4)

    def test_swallowed_health_persistence_error_still_marks_the_run_failed(self):
        # Use actual enrich_health: it intentionally catches DB errors for UI use.
        # The snapshot failed flag must make the dataset runner abort anyway.
        self.store.fail_table = "dish_health_info"
        with patch("dish_health.seed_allergen_rules", return_value=0), \
                patch("enrichment_batch.time.sleep", return_value=None), \
                patch("dish_health._nutrition_record", side_effect=self.providers.nutrition), \
                patch("dish_backgrounds.enrich_background", side_effect=self.providers.background), \
                patch("enrichment_report.build_report", side_effect=report), \
                patch("enrichment_report.render_markdown", return_value="Measured report"), \
                redirect_stdout(io.StringIO()), self.assertRaises(DishStoreError):
            run_dataset(self.store, self.recipes, mode="health", workers=1, delay=0)
        self.assertEqual(self.store.tables["dish_enrichment_runs"][0]["status"], "failed")
        self.assertEqual(len(self.store.tables.get("dish_health_info", [])), 0)


class PacingTests(unittest.TestCase):
    def test_only_actual_provider_work_uses_pacing_delay(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import Mock
        from enrichment_batch import _phase
        outcomes = {0: ("deferred", False), 1: ("cached", False), 2: ("success", True)}
        journal = SimpleNamespace(heartbeat=Mock())
        with patch("enrichment_batch.time.sleep") as sleep, redirect_stdout(io.StringIO()):
            result = _phase("test", [0, 1, 2], outcomes.__getitem__, 1, journal, threading.Event(), 1)
        self.assertEqual(result, {"deferred": 1, "cached": 1, "success": 1})
        sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
