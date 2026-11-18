"""Offline public-data boundary, recipe identity, and Supabase SDK checks."""
import copy
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dish_info_common import DATA_VERSION, fresh, ingredient_hash, normalize, safe_url
from dish_health import HEALTH_ENRICHMENT_VERSION
from dish_backgrounds import BACKGROUND_RETRIEVAL_VERSION

# Tests never load a real credentials file or construct a connected SDK client.
with patch("dotenv.load_dotenv", return_value=False):
    import dish_information as information
    from dish_information_store import DishInformationStore, DishStoreError


def now():
    return datetime.now(timezone.utc).isoformat()


def approved_background(**changes):
    row = {"id": "background-id", "dish_name": "Noodle soup", "normalized_name": "noodle soup", "country": "TEST",
           "historical_background": "Reviewed history.", "cultural_background": "Reviewed culture.",
           "draft_historical_background": "PRIVATE DRAFT", "source_evidence": [{"excerpt": "PRIVATE EXCERPT"}],
           "sources": [{"title": "Reviewed source", "url": "https://doi.org/10.1000/evidence",
                        "source_type": "crossref", "matched_country": "TEST",
                        "excerpt": "Noodle soup is a food in Test with documented history and communal cultural practices."}],
           "verification_status": "verified", "reviewed_by": "admin-id", "reviewed_at": now(),
           "review_note": "Compared both summaries with linked evidence.", "data_version": DATA_VERSION, "updated_at": now()}
    row.update(changes)
    return row


def verified_health(recipe, **changes):
    row = {"recipe_id": recipe["recipe_id"], "ingredient_hash": ingredient_hash(recipe),
           "verification_status": "verified", "nutrition_status": "verified", "data_version": DATA_VERSION,
           "updated_at": now(), "potential_benefits": [{"ingredient": "tofu", "text": "Ingredient contribution.",
                                                           "source_url": "https://example.org/nutrient-function"}],
           "health_considerations": [{"type": "coverage", "matcher_version": HEALTH_ENRICHMENT_VERSION}],
           "potential_allergens": [{"name": "Soy", "classification": "identified",
                                                                   "from": ["tofu"], "source_url": "https://example.org/allergen"}],
           "sources": [{"title": "Nutrient source", "url": "https://example.org/composition"}],
           "matched_ingredient_count": 1, "ingredient_count": 1}
    row.update(changes)
    return row


class PublicStore:
    def __init__(self, backgrounds=(), health=()):
        self.rows = {"dish_backgrounds": list(backgrounds), "dish_health_info": list(health)}
        self.lookups = []

    def get(self, table, filters):
        self.lookups.append((table, copy.deepcopy(filters)))
        return next((copy.deepcopy(row) for row in self.rows.get(table, [])
                     if all(row.get(key) == value for key, value in filters.items())), None)


class FakeQuery:
    """Small fluent fake that actually applies equality filters and writes."""
    def __init__(self, client, table):
        self.client, self.table = client, table
        self.filters = []
        self.operation, self.payload, self.conflict = "select", None, None
        self.ignore_duplicates = False
        self.window, self.maximum = None, None

    def select(self, _columns):
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def limit(self, value):
        self.maximum = value
        return self

    def order(self, _column):
        return self

    def range(self, start, end):
        self.window = (start, end)
        return self

    def upsert(self, payload, on_conflict, ignore_duplicates=False):
        self.operation, self.payload, self.conflict = "upsert", copy.deepcopy(payload), on_conflict
        self.ignore_duplicates = ignore_duplicates
        return self

    def update(self, payload):
        self.operation, self.payload = "update", copy.deepcopy(payload)
        return self

    def execute(self):
        self.client.queries.append(self)
        if self.client.failure:
            raise RuntimeError(self.client.failure)
        if self.client.empty_write and self.operation != "select":
            return SimpleNamespace(data=[])
        rows = self.client.rows.setdefault(self.table, [])
        if self.operation == "upsert":
            if self.client.change_during_upsert:
                self.client.change_during_upsert(rows)
            keys = self.conflict.split(",")
            if self.ignore_duplicates and any(all(r.get(k) == self.payload.get(k) for k in keys) for r in rows):
                return SimpleNamespace(data=[])
            rows[:] = [r for r in rows if not all(r.get(k) == self.payload.get(k) for k in keys)]
            rows.append(copy.deepcopy(self.payload))
            return SimpleNamespace(data=[copy.deepcopy(self.payload)])
        if self.operation == "update" and self.client.change_during_update:
            self.client.change_during_update(rows)
        matches = [r for r in rows if all(r.get(k) == v for k, v in self.filters)]
        if self.operation == "update":
            for row in matches:
                row.update(copy.deepcopy(self.payload))
        if self.window:
            matches = matches[self.window[0]:self.window[1] + 1]
        if self.maximum is not None:
            matches = matches[:self.maximum]
        return SimpleNamespace(data=copy.deepcopy(matches))


class FakeClient:
    def __init__(self, rows=None):
        self.rows = copy.deepcopy(rows or {})
        self.queries = []
        self.failure = None
        self.empty_write = False
        self.change_during_update = None
        self.change_during_upsert = None

    def table(self, table):
        return FakeQuery(self, table)


class IdentityTests(unittest.TestCase):
    def test_hash_tracks_actual_ingredients_not_dish_title(self):
        a = {"title": "Soup", "ingredient_text": "tofu, spinach"}
        b = dict(a, ingredient_text="beef, spinach")
        self.assertNotEqual(ingredient_hash(a), ingredient_hash(b))
        self.assertEqual(ingredient_hash(a), ingredient_hash(dict(a, title="Different title")))

    def test_hash_ignores_order_case_spacing_duplicates_but_keeps_preparation(self):
        self.assertEqual(ingredient_hash({"ingredient_text": "Tofu, Spinach"}),
                         ingredient_hash({"ingredient_text": " spinach ; tofu ; TOFU "}))
        self.assertNotEqual(ingredient_hash({"ingredient_text": "salted butter"}),
                            ingredient_hash({"ingredient_text": "unsalted butter"}))

    def test_stale_or_changed_data_version_is_invalid(self):
        self.assertTrue(fresh({"updated_at": now(), "data_version": DATA_VERSION}))
        self.assertFalse(fresh({"updated_at": now(), "data_version": DATA_VERSION - 1}))
        self.assertFalse(fresh({"updated_at": (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),
                                "data_version": DATA_VERSION}))

    def test_unsafe_source_urls_are_rejected(self):
        for value in ["javascript:alert(1)", "data:text/html,secret", "file:///secret", "//example.org",
                      "https://user:password@example.org/evidence"]:
            self.assertEqual("", safe_url(value))
        self.assertEqual("https://example.org/evidence", safe_url("https://example.org/evidence"))


class PublicBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.recipe = {"recipe_id": 7, "title": "Noodle soup", "country": "TEST", "ingredient_text": "tofu"}

    def test_temporary_provider_failure_can_retry_after_one_hour(self):
        row = approved_background(verification_status="unavailable", historical_background="", cultural_background="",
                                  unavailable_reason="Temporary evidence provider cooldown; retry later.",
                                  updated_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
        with patch.object(information, "_schedule") as schedule:
            information.public_information(self.recipe, store=PublicStore([row], [verified_health(self.recipe)]))
        schedule.assert_called_once()

    def test_genuine_source_gap_is_not_retried_on_every_view(self):
        row = approved_background(verification_status="unavailable", historical_background="", cultural_background="",
                                  unavailable_reason="No source matched this dish and country confidently.",
                                  enrichment_version=BACKGROUND_RETRIEVAL_VERSION, updated_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
        with patch.object(information, "_schedule") as schedule:
            information.public_information(self.recipe, store=PublicStore([row], [verified_health(self.recipe)]))
        schedule.assert_not_called()

    def test_partial_background_retry_window_is_one_day(self):
        for hours, expected in [(2, False), (25, True)]:
            with self.subTest(hours=hours), patch.object(information, "_schedule") as schedule:
                row = approved_background(verification_status="pending", cultural_background="",
                                          enrichment_version=BACKGROUND_RETRIEVAL_VERSION,
                                          updated_at=(datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat())
                result = information.public_information(self.recipe, store=PublicStore([row], [verified_health(self.recipe)]))
                self.assertEqual("Reviewed history.", result["background"]["historical_background"])
                self.assertEqual(expected, schedule.called)

    def test_source_grounded_pending_background_is_visible_without_review_metadata(self):
        draft = approved_background(verification_status="pending", historical_background="Source-grounded generated history.",
                                    cultural_background="Source-grounded generated culture.", reviewed_at=None,
                                    reviewed_by=None, review_note="", sources=[{
                                        "title": "Public evidence", "url": "https://example.org/evidence",
                                        "excerpt": "PRIVATE SOURCE EXCERPT", "retrieved_at": now(),
                                    }])
        public = information.public_information(self.recipe, schedule=False, store=PublicStore([draft]))
        background = public["background"]
        self.assertEqual("pending", background["status"])
        self.assertEqual("Source-grounded generated history.", background["historical_background"])
        self.assertEqual("Source-grounded generated culture.", background["cultural_background"])
        self.assertIn("ai-generated", background["message"].lower())
        self.assertIn("not independently verified", background["message"].lower())
        self.assertNotIn("awaiting", background["message"].lower())
        self.assertEqual([{"title": "Public evidence", "url": "https://example.org/evidence"}], background["sources"])
        for private_field in ["PRIVATE", "source_evidence", "excerpt", "reviewed_by", "reviewed_at", "review_note"]:
            self.assertNotIn(private_field, str(public))

    def test_unsourced_background_is_unverified_and_old_data_is_unavailable(self):
        for changes in [{"sources": []}, {"sources": [{"title": "Bad", "url": "javascript:bad"}]},
                        {"data_version": DATA_VERSION - 1}]:
            with self.subTest(changes=changes):
                draft = approved_background(verification_status="pending", historical_background="HIDDEN_UNSOURCED_HISTORY",
                                            cultural_background="HIDDEN_UNSOURCED_CULTURE", reviewed_by=None,
                                            reviewed_at=None, review_note="", **changes)
                result = information.public_information(self.recipe, schedule=False, store=PublicStore([draft]))
                if changes.get('data_version', DATA_VERSION) != DATA_VERSION:
                    self.assertEqual('', result['background']['historical_background'])
                else:
                    self.assertEqual('unverified', result['background']['status'])
                    self.assertEqual('HIDDEN_UNSOURCED_HISTORY', result['background']['historical_background'])
                    self.assertEqual([], result['background']['sources'])

    def test_pending_background_filters_unsafe_sources_and_private_evidence(self):
        draft = approved_background(verification_status="pending", reviewed_by=None, reviewed_at=None, review_note="",
                                    sources=[{"title": "Bad", "url": "javascript:PRIVATE"}, {
                                        "title": "<script>evidence title</script>", "url": "https://example.org/evidence",
                                        "excerpt": "PRIVATE EXCERPT", "retrieved_at": now(),
                                    }])
        result = information.public_information(self.recipe, schedule=False, store=PublicStore([draft]))
        self.assertEqual("pending", result["background"]["status"])
        self.assertEqual(1, len(result["background"]["sources"]))
        self.assertNotIn("PRIVATE", str(result))
        self.assertNotIn("javascript:", str(result))
        self.assertNotIn("excerpt", str(result))

    def test_approval_requires_verified_status_and_all_review_metadata(self):
        cases = [{"reviewed_at": None},
                 {"review_note": ""}, {"review_note": "   "}]
        for changes in cases:
            with self.subTest(changes=changes):
                result = information.public_information(self.recipe, schedule=False,
                                                        store=PublicStore([approved_background(**changes)]))
                self.assertNotEqual("verified", result["background"]["status"])
                self.assertIn("not independently verified", result["background"]["message"])
                self.assertEqual("Reviewed history.", result["background"]["historical_background"])
        result = information.public_information(self.recipe, schedule=False, store=PublicStore([approved_background()]))
        self.assertEqual("Reviewed history.", result["background"]["historical_background"])
        self.assertNotIn("PRIVATE", str(result))

    def test_deleted_reviewer_does_not_erase_a_completed_review(self):
        background = approved_background(reviewed_by=None)
        result = information.public_information(self.recipe, schedule=False, store=PublicStore([background]))
        self.assertEqual("verified", result["background"]["status"])
        self.assertEqual("Reviewed history.", result["background"]["historical_background"])

    def test_background_without_safe_evidence_cannot_be_presented_as_verified(self):
        background = approved_background(sources=[{"title": "Bad", "url": "javascript:private"}])
        result = information.public_information(self.recipe, schedule=False, store=PublicStore([background]))
        self.assertNotEqual("verified", result["background"]["status"])
        self.assertEqual('unverified', result['background']['status'])
        self.assertEqual("Reviewed history.", result["background"]["historical_background"])

    def test_public_sources_and_per_note_urls_are_sanitized(self):
        background = approved_background(sources=[{"title": "Bad", "url": "javascript:private"},
                                                  {"title": "Good", "url": "https://example.org/good"}])
        health = verified_health(self.recipe, potential_benefits=[{"text": "Potential contribution", "source_url": "javascript:private"}],
                                 potential_allergens=[{"name": "Soy", "classification": "identified", "from": ["tofu"],
                                                       "source_url": "data:text/html,private"}],
                                 sources=[{"title": "Bad", "url": "file:///secret"},
                                          {"title": "Good", "url": "https://example.org/composition"}])
        result = information.public_information(self.recipe, schedule=False, store=PublicStore([background], [health]))
        self.assertNotIn("javascript:", str(result))
        self.assertNotIn("data:text/html", str(result))
        self.assertNotIn("file:///", str(result))
        self.assertEqual(1, len(result["background"]["sources"]))
        self.assertEqual("", result["health"]["potential_benefits"][0]["source_url"])

    def test_same_dish_title_uses_ingredient_specific_health(self):
        other = dict(self.recipe, ingredient_text="beef")
        store = PublicStore([approved_background()], [verified_health(self.recipe)])
        first = information.public_information(self.recipe, schedule=False, store=store)
        changed = information.public_information(other, schedule=False, store=store)
        self.assertEqual("verified", first["health"]["status"])
        self.assertEqual("unavailable", changed["health"]["status"])
        self.assertEqual([], changed["health"]["potential_allergens"])
        self.assertEqual(first["background"], changed["background"])

    @patch("dish_information._schedule")
    def test_store_outage_returns_unavailable_and_never_schedules_providers(self, schedule):
        store = Mock()
        store.get.side_effect = DishStoreError("Unavailable")
        result = information.public_information(self.recipe, store=store)
        self.assertEqual("unavailable", result["health"]["status"])
        self.assertEqual("unavailable", result["background"]["status"])
        schedule.assert_not_called()

    @patch("dish_information._schedule")
    @patch("dish_information.get_store", return_value=None)
    def test_no_supabase_connection_does_not_use_providers(self, _store, schedule):
        result = information.public_information(self.recipe)
        self.assertEqual("unavailable", result["health"]["status"])
        schedule.assert_not_called()

    def test_expired_partial_health_remains_visible_during_background_refresh(self):
        # Partial USDA enrichment refreshes hourly. A valid stored assessment
        # must not disappear in the UI while that asynchronous work runs.
        row = verified_health(
            self.recipe, nutrition_status="partial",
            updated_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        )
        with patch.object(information, "_schedule") as schedule:
            result = information.public_information(
                self.recipe, store=PublicStore([approved_background()], [row]))
        self.assertEqual("verified", result["health"]["status"])
        self.assertEqual(1, len(result["health"]["potential_benefits"]))
        self.assertEqual("Soy", result["health"]["potential_allergens"][0]["name"])
        schedule.assert_called_once()

    def test_old_ingredient_health_is_not_shown(self):
        old = verified_health(self.recipe, updated_at=(datetime.now(timezone.utc) - timedelta(days=31)).isoformat())
        result = information.public_information(self.recipe, schedule=False, store=PublicStore(health=[old]))
        self.assertEqual("unavailable", result["health"]["status"])

    def test_fresh_old_enrichment_version_is_hidden_and_refresh_is_scheduled(self):
        for coverage in ([], [{"type": "coverage", "matcher_version": HEALTH_ENRICHMENT_VERSION - 1}]):
            with self.subTest(coverage=coverage), patch.object(information, "_schedule") as schedule:
                old = verified_health(self.recipe, health_considerations=coverage)
                result = information.public_information(
                    self.recipe, store=PublicStore([approved_background()], [old]))
                self.assertEqual("unavailable", result["health"]["status"])
                self.assertEqual([], result["health"]["potential_benefits"])
                self.assertEqual([], result["health"]["potential_allergens"])
                schedule.assert_called_once()

    def test_current_zero_match_health_still_exposes_named_unmatched_coverage(self):
        row = verified_health(self.recipe, verification_status="unavailable", nutrition_status="unavailable",
                              matched_ingredient_count=0, ingredient_count=1,
                              health_considerations=[{"type": "coverage", "matcher_version": HEALTH_ENRICHMENT_VERSION,
                                                      "matched_ingredients": [],
                                                      "unmatched_ingredients": [{"ingredient": "tofu", "reason": "No validated match"}]}])
        result = information.public_information(self.recipe, schedule=False, store=PublicStore(health=[row]))
        self.assertEqual(result["health"]["status"], "unavailable")
        self.assertEqual(result["health"]["matched_ingredient_count"], 0)
        self.assertEqual(result["health"]["ingredient_count"], 1)
        self.assertEqual(result["health"]["unmatched_ingredients"], [{"ingredient": "tofu", "reason": "No validated match"}])
        self.assertIn("0 of 1", result["health"]["nutrition_message"])
        self.assertIn("tofu", result["health"]["nutrition_message"])
        self.assertEqual(result["health"]["potential_benefits"], [])

    def test_profile_allergen_groups_match_more_specific_classifications(self):
        health = verified_health(self.recipe, potential_allergens=[
            {"name": "Molluscs", "classification": "possible", "from": ["oyster sauce"]},
            {"name": "Wheat", "classification": "possible", "from": ["soy sauce"]},
        ])
        result = information.public_information(self.recipe, avoid_allergens=["Shellfish", "Wheat/Gluten"],
                                                schedule=False, store=PublicStore(health=[health]))
        self.assertTrue(all(a["avoid"] for a in result["health"]["potential_allergens"]))


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        with information._jobs_lock:
            information._jobs.clear()
        self.executor = patch.object(information, "_executor")
        self.mock_executor = self.executor.start()
        self.addCleanup(self.executor.stop)
        self.env = patch.dict(os.environ, {"DISH_INFO_AUTO_ENRICH": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def tearDown(self):
        with information._jobs_lock:
            information._jobs.clear()

    def test_duplicate_inflight_recipe_is_submitted_once_and_queue_is_bounded(self):
        recipe = {"recipe_id": 1, "title": "Soup", "country": "TEST", "ingredient_text": "tofu"}
        store = Mock()
        information._schedule(recipe, store)
        information._schedule(dict(recipe), store)
        self.assertEqual(1, self.mock_executor.submit.call_count)
        for index in range(2, 12):
            information._schedule(dict(recipe, recipe_id=index), store)
        self.assertEqual(8, self.mock_executor.submit.call_count)
        self.assertEqual(8, len(information._jobs))

    def test_disabled_automatic_enrichment_submits_nothing(self):
        with patch.dict(os.environ, {"DISH_INFO_AUTO_ENRICH": "0"}):
            information._schedule({"recipe_id": 1}, Mock())
        self.mock_executor.submit.assert_not_called()

    def test_rejected_submission_releases_inflight_marker(self):
        self.mock_executor.submit.side_effect = RuntimeError("Executor unavailable")
        information._schedule({"recipe_id": 1}, Mock())
        self.assertEqual(set(), information._jobs)


class SDKStoreTests(unittest.TestCase):
    def setUp(self):
        self.pending = approved_background(verification_status="pending", reviewed_at=None, reviewed_by=None, review_note="")
        self.client = FakeClient({"profiles": [{"id": "admin-id", "role": "admin", "status": "active"}],
                                  "dish_backgrounds": [self.pending]})
        self.store = DishInformationStore(self.client)

    def test_upsert_persists_with_conflict_key_version_and_timestamp(self):
        original = {"normalized_name": "tofu", "match_status": "unavailable"}
        saved = self.store.upsert("ingredient_nutrition", original, "normalized_name")
        self.assertEqual(DATA_VERSION, saved["data_version"])
        self.assertTrue(saved["updated_at"])
        self.assertNotIn("updated_at", original)
        self.assertEqual("normalized_name", self.client.queries[-1].conflict)

    def test_sdk_failures_have_safe_logs_and_exception_messages(self):
        self.client.failure = "SERVICE_ROLE_KEY=PRIVATE-DO-NOT-PRINT raw private record"
        operations = [lambda: self.store.get("dish_backgrounds", {"id": "one"}),
                      lambda: self.store.list("dish_backgrounds"),
                      lambda: self.store.upsert("dish_backgrounds", {"dish_name": "One"}, "normalized_name,country")]
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertLogs("dish_information_store", level="WARNING") as logs:
                    with self.assertRaises(DishStoreError) as raised:
                        operation()
                self.assertNotIn("PRIVATE", str(raised.exception))
                self.assertNotIn("PRIVATE", " ".join(logs.output))

    def test_empty_database_write_response_is_not_reported_as_success(self):
        self.client.empty_write = True
        with self.assertRaises(DishStoreError):
            self.store.upsert("ingredient_nutrition", {"normalized_name": "tofu"}, "normalized_name")

    def test_table_allowlist_rejects_unrelated_writes(self):
        with self.assertRaises(ValueError):
            self.store.upsert("profiles", {"role": "admin"}, "id")
        self.assertEqual([], self.client.queries)

    def test_list_reads_beyond_the_first_database_page(self):
        self.client.rows["recipes"] = [{"recipe_id": i} for i in range(501)]
        rows = self.store.list("recipes")
        self.assertEqual(501, len(rows))
        self.assertEqual([(0, 499), (500, 999)], [q.window for q in self.client.queries])

    def test_only_an_active_admin_can_approve(self):
        for reviewer in [None, {"id": "admin-id", "role": "user", "status": "active"},
                         {"id": "admin-id", "role": "admin", "status": "disabled"}]:
            with self.subTest(reviewer=reviewer):
                self.client.rows["profiles"] = [reviewer] if reviewer else []
                with self.assertRaises(DishStoreError):
                    self.store.approve_background("background-id", "admin-id", "Reviewed",
                                                  expected_updated_at=self.pending["updated_at"])
                self.assertEqual("pending", self.client.rows["dish_backgrounds"][0]["verification_status"])

    def test_approval_requires_pending_complete_sourced_draft_and_nonempty_note(self):
        for changes, note in [({"verification_status": "unavailable"}, "Reviewed"),
                              ({"sources": []}, "Reviewed"), ({"historical_background": ""}, "Reviewed"),
                              ({"cultural_background": ""}, "Reviewed"), ({}, ""), ({}, "  "), ({}, None),
                              ({"sources": [{"url": "javascript:bad"}]}, "Reviewed")]:
            with self.subTest(changes=changes, note=note):
                self.client.rows["dish_backgrounds"] = [dict(copy.deepcopy(self.pending), **changes)]
                with self.assertRaises(DishStoreError):
                    self.store.approve_background("background-id", "admin-id", note,
                                                  expected_updated_at=self.pending["updated_at"])

    def test_approval_records_reviewer_note_and_uses_optimistic_concurrency(self):
        original_updated_at = self.pending["updated_at"]
        result = self.store.approve_background("background-id", "admin-id", "  Checked both summaries  ",
                                               expected_updated_at=original_updated_at)
        self.assertEqual("verified", result["verification_status"])
        self.assertEqual("admin-id", result["reviewed_by"])
        self.assertEqual("Checked both summaries", result["review_note"])
        self.assertTrue(result["reviewed_at"])
        query = next(q for q in self.client.queries if q.operation == "update")
        self.assertIn(("updated_at", original_updated_at), query.filters)
        self.assertIn(("verification_status", "pending"), query.filters)

    def test_concurrent_draft_edit_cannot_be_approved_using_old_evidence(self):
        def edit(rows):
            rows[0]["historical_background"] = "Concurrent new draft"
            rows[0]["updated_at"] = "2099-01-01T00:00:00+00:00"
        self.client.change_during_update = edit
        with self.assertRaises(DishStoreError):
            self.store.approve_background("background-id", "admin-id", "Reviewed the old draft",
                                          expected_updated_at=self.pending["updated_at"])
        self.assertEqual("pending", self.client.rows["dish_backgrounds"][0]["verification_status"])
        self.assertEqual("Concurrent new draft", self.client.rows["dish_backgrounds"][0]["historical_background"])

    def test_review_form_revision_is_required_and_must_still_match(self):
        for revision in [None, "old-version"]:
            with self.assertRaises(DishStoreError):
                self.store.approve_background("background-id", "admin-id", "Reviewed",
                                              expected_updated_at=revision)
        self.assertFalse(any(query.operation == "update" for query in self.client.queries))

    def test_background_save_inserts_absent_record_without_overwrite_permission(self):
        self.client.rows["dish_backgrounds"] = []
        draft = approved_background(verification_status="pending", historical_background="New draft")
        result = self.store.save_background(draft, observed=None)
        self.assertEqual("New draft", result["historical_background"])
        self.assertEqual(1, len(self.client.rows["dish_backgrounds"]))
        insertion = next(q for q in self.client.queries if q.operation == "upsert")
        self.assertTrue(insertion.ignore_duplicates)

    def test_background_save_does_not_replace_a_concurrent_first_writer(self):
        self.client.rows["dish_backgrounds"] = []
        winner = approved_background(historical_background="Concurrent approved information")
        def insert_winner(rows):
            rows.append(copy.deepcopy(winner))
        self.client.change_during_upsert = insert_winner
        draft = approved_background(verification_status="pending", historical_background="Slow competing draft")
        result = self.store.save_background(draft, observed=None)
        self.assertEqual("Concurrent approved information", result["historical_background"])
        self.assertEqual("verified", result["verification_status"])
        self.assertEqual(1, len(self.client.rows["dish_backgrounds"]))

    def test_background_save_updates_only_the_observed_revision_and_status(self):
        observed = copy.deepcopy(self.pending)
        draft = dict(observed, historical_background="Refreshed pending history")
        result = self.store.save_background(draft, observed=observed)
        self.assertEqual("Refreshed pending history", result["historical_background"])
        update = next(q for q in self.client.queries if q.operation == "update")
        self.assertIn(("id", observed["id"]), update.filters)
        self.assertIn(("updated_at", observed["updated_at"]), update.filters)
        self.assertIn(("verification_status", "pending"), update.filters)

    def test_background_save_preserves_approval_that_wins_while_drafting(self):
        observed = copy.deepcopy(self.pending)
        def approve(rows):
            rows[0].update(verification_status="verified", historical_background="Approved winner",
                           reviewed_by="admin-id", reviewed_at=now(), review_note="Reviewed the winner",
                           updated_at="2099-01-01T00:00:00+00:00")
        self.client.change_during_update = approve
        result = self.store.save_background(dict(observed, historical_background="Slow pending rewrite"), observed=observed)
        self.assertEqual("verified", result["verification_status"])
        self.assertEqual("Approved winner", result["historical_background"])
        self.assertEqual("Reviewed the winner", result["review_note"])

    def test_background_save_preserves_newer_draft_when_revision_changed(self):
        observed = copy.deepcopy(self.pending)
        def newer_draft(rows):
            rows[0].update(historical_background="Newer pending winner", updated_at="2099-01-01T00:00:00+00:00")
        self.client.change_during_update = newer_draft
        result = self.store.save_background(dict(observed, historical_background="Stale pending rewrite"), observed=observed)
        self.assertEqual("Newer pending winner", result["historical_background"])
        self.assertEqual("pending", result["verification_status"])

    def test_background_save_failure_has_no_private_database_details(self):
        self.client.failure = "SERVICE_ROLE_KEY=PRIVATE-DO-NOT-PRINT"
        with self.assertRaises(DishStoreError) as raised:
            self.store.save_background(dict(self.pending), observed=self.pending)
        self.assertNotIn("PRIVATE", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
