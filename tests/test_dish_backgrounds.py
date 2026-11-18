"""History/culture source identity, approval, and Supabase-cache boundaries."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dish_backgrounds as backgrounds
from dish_info_common import DATA_VERSION, normalize


RECIPE = {"title": "Adobo", "country": "PHILIPPINES", "alternative_title": ""}
SOURCE = {"title": "Philippine adobo", "url": "https://en.wikipedia.org/wiki/Philippine_adobo",
          "excerpt": "Philippine adobo is a Filipino dish.", "source_type": "wikipedia"}


def response(body, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode("utf-8")
    result._content_consumed = True
    result.url = backgrounds.WIKIPEDIA_API
    return result


def page(title="Philippine adobo", extract=None, **kwargs):
    return dict({"pageid": 1, "title": title, "extract": extract or
                 "Philippine adobo is a Filipino dish. It is commonly served with rice.",
                 "pageprops": {}}, **kwargs)


def wiki(*pages):
    return response({"query": {"pages": list(pages)}})


def client_with(draft=None):
    client = mock.Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(draft or {
            "historical_background": "", "cultural_background": "It is commonly served with rice.",
        })))])
    return client


class MemoryStore:
    def __init__(self, row=None):
        self.row = copy.deepcopy(row)
        self.writes = []

    def get(self, table, filters):
        assert table == "dish_backgrounds"
        assert set(filters) == {"normalized_name", "country"}
        return copy.deepcopy(self.row)

    def upsert(self, table, row, on_conflict):
        assert table == "dish_backgrounds"
        assert on_conflict == "normalized_name,country"
        self.row = copy.deepcopy(row)
        self.writes.append(self.row)
        return copy.deepcopy(self.row)


def record(status="pending", days_old=0, **changes):
    return dict({"dish_name": "Adobo", "country": "PHILIPPINES", "normalized_name": "adobo",
                 "historical_background": "Approved history.", "cultural_background": "Approved culture.",
                 "sources": [SOURCE], "verification_status": status, "unavailable_reason": "",
                 "data_version": DATA_VERSION,
                 "updated_at": (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()}, **changes)


def persisted_source(days_old=0):
    # Active source cache now requires scholarly/official sources, not Wikipedia.
    return dict(title="Philippine adobo in culinary scholarship", url="https://doi.org/10.1000/adobo",
                excerpt="Philippine adobo is a Filipino dish and this research examines its traditions.",
                source_type="crossref", evidence_kind="publication_abstract",
                retrieval_version=backgrounds.BACKGROUND_RETRIEVAL_VERSION,
                matched_country="PHILIPPINES", matched_aliases=["Adobo"],
                retrieved_at=(datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat())


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        # These are regression checks for the retired Wikipedia adapter. The
        # production retrieve_evidence() now uses only official/scholarly APIs.
        self.enterContext(mock.patch.object(backgrounds, "retrieve_evidence",
                                            wraps=backgrounds._retrieve_legacy_wikipedia_evidence))
        self.enterContext(mock.patch.dict(backgrounds._PROVIDER_BACKOFF, {}, clear=True))
        self.http_patch = mock.patch.object(requests, "get", side_effect=AssertionError("Unexpected HTTP call"))
        self.http = self.http_patch.start()
        self.addCleanup(self.http_patch.stop)

    def test_source_identity_is_dish_and_country_not_name_alone(self):
        self.http.side_effect = [wiki(page(title="Adobo", extract="Adobo is a Mexican dish.")),
                                 response({"query": {"search": []}})]
        store, client = MemoryStore(), client_with()
        result = backgrounds.enrich_background(RECIPE, store, client)
        self.assertEqual(result["verification_status"], "pending")
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["sources"], [])
        # A negative result is also persistently cached.
        backgrounds.enrich_background(RECIPE, store, client)
        self.assertEqual(self.http.call_count, 2)

    def test_later_mention_of_another_cuisine_is_not_identity_evidence(self):
        extract = "Adobo is a Spanish marinade. Philippine adobo is a Filipino dish."
        self.assertFalse(backgrounds._text_matches_country(extract, backgrounds._terms("PHILIPPINES")))

    def test_country_qualified_title_cannot_match_another_cuisine(self):
        self.assertFalse(backgrounds._title_matches("Adobo (Mexican cuisine)", ["Adobo"],
                                                      backgrounds._terms("PHILIPPINES")))
        self.assertTrue(backgrounds._title_matches("Philippine adobo", ["Adobo"],
                                                     backgrounds._terms("PHILIPPINES")))

    def test_disambiguation_article_is_rejected(self):
        result = backgrounds._matching_sources([
            page(pageprops={"disambiguation": ""}),
        ], ["Adobo"], "PHILIPPINES")
        self.assertEqual(result, [])
        self.http.assert_not_called()

    def test_identically_named_restaurant_is_not_treated_as_dish(self):
        result = backgrounds._matching_sources([
            page(title="Adobo", extract="Adobo is a Filipino restaurant serving rice dishes."),
        ], ["Adobo"], "PHILIPPINES")
        self.assertEqual(result, [])
        self.http.assert_not_called()

    def test_alternative_title_can_identify_correct_dish(self):
        fixture = page(title="Biko", extract="Biko is a Filipino rice cake.")
        self.http.side_effect = [wiki(fixture), wiki(fixture)]
        recipe = {"title": "Bibingkang Malagkit", "alternative_title": "Biko", "country": "Philippines"}
        result = backgrounds.enrich_background(recipe, MemoryStore(), client_with())
        self.assertEqual(result["verification_status"], "pending")
        self.assertEqual(result["normalized_name"], "bibingkang malagkit")
        self.assertEqual(result["sources"][0]["title"], "Biko")

    def test_wikidata_conflicting_origin_overrides_passing_country_mention(self):
        origin = {"rank": "normal", "mainsnak": {"datavalue": {"value": {"id": "Q2"}}}}
        with mock.patch.object(backgrounds, "_entities", side_effect=[
            {"Q1": {"claims": {"P495": [origin]}}},
            {"Q2": {"labels": {"en": {"value": "Mexico"}}}},
        ]):
            result = backgrounds._matching_sources([
                page(pageprops={"wikibase_item": "Q1"}),
            ], ["Adobo"], "PHILIPPINES")
        self.assertEqual(result, [])

    def test_shared_korean_origin_matches_south_korean_dish_without_claiming_exclusivity(self):
        origin = {"mainsnak": {"datavalue": {"value": {"id": "Q2"}}}}
        fixture = page(title="Kimchi", extract="Kimchi is a traditional Korean side dish.",
                       pageprops={"wikibase_item": "Q1"})
        self.http.side_effect = [wiki(fixture)]
        with mock.patch.object(backgrounds, "_entities", side_effect=[
            {"Q1": {"claims": {"P495": [origin]}}},
            {"Q2": {"labels": {"en": {"value": "Korea"}}}},
        ]):
            sources = backgrounds._matching_sources([fixture], ["Kimchi"], "SOUTH KOREA")
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]["country_match_basis"], "shared_korean_origin_and_korean_dish_lead")
        self.assertIn("Country of origin: Korea", sources[1]["excerpt"])

    def test_shared_korean_origin_cannot_match_unrelated_country(self):
        origin = {"mainsnak": {"datavalue": {"value": {"id": "Q2"}}}}
        fixture = page(title="Kimchi", extract="Kimchi is a traditional Korean side dish.",
                       pageprops={"wikibase_item": "Q1"})
        with mock.patch.object(backgrounds, "_entities", side_effect=[
            {"Q1": {"claims": {"P495": [origin]}}},
            {"Q2": {"labels": {"en": {"value": "Korea"}}}},
        ]):
            self.assertEqual(backgrounds._matching_sources([fixture], ["Kimchi"], "JAPAN"), [])
        self.http.assert_not_called()

    def test_shared_korean_origin_requires_korean_dish_lead(self):
        origin = {"mainsnak": {"datavalue": {"value": {"id": "Q2"}}}}
        fixture = page(title="Kimchi", extract="Kimchi is a Japanese side dish.",
                       pageprops={"wikibase_item": "Q1"})
        with mock.patch.object(backgrounds, "_entities", side_effect=[
            {"Q1": {"claims": {"P495": [origin]}}},
            {"Q2": {"labels": {"en": {"value": "Korea"}}}},
        ]):
            self.assertEqual(backgrounds._matching_sources([fixture], ["Kimchi"], "SOUTH KOREA"), [])
        self.http.assert_not_called()

    def test_optional_wikidata_maxlag_still_allows_clear_wikipedia_identity(self):
        fixture = page(title="Kimchi", extract="Kimchi is a traditional Korean side dish.",
                       pageprops={"wikibase_item": "Q1"})
        self.http.side_effect = [wiki(fixture), response({"error": {"code": "maxlag"}}), wiki(fixture)]
        sources = backgrounds.retrieve_evidence({"title": "Kimchi", "country": "SOUTH KOREA"})
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["source_type"], "wikipedia")
        self.assertEqual(sources[0]["wikidata_status"], "temporarily_unavailable")
        self.assertEqual(sources[0]["country_match_basis"], "wikipedia_dish_and_country_lead")
        self.assertEqual(self.http.call_count, 3)

    def test_wikidata_rate_limit_does_not_trigger_more_provider_requests(self):
        fixture = page(title="Kimchi", extract="Kimchi is a traditional Korean side dish.",
                       pageprops={"wikibase_item": "Q1"})
        self.http.side_effect = [wiki(fixture), response({}, status=429)]
        with self.assertRaises(backgrounds.EvidenceUnavailable) as error:
            backgrounds.retrieve_evidence({"title": "Kimchi", "country": "SOUTH KOREA"})
        self.assertEqual(error.exception.code, "rate_limit")
        self.assertEqual(self.http.call_count, 2)

    def test_long_display_name_does_not_truncate_persistent_identity_key(self):
        title = "Very long dish name " * 20
        row = backgrounds._base_row({"title": title, "country": "SOUTH KOREA"})
        self.assertEqual(row["normalized_name"], normalize(title))
        self.assertLessEqual(len(row["dish_name"]), 250)

    def test_matching_evidence_produces_pending_draft_with_canonical_sources(self):
        fixture = page(fullurl="https://untrusted.invalid/article")
        self.http.side_effect = [wiki(fixture), wiki(fixture)]
        client = client_with({"historical_background": "", "cultural_background": "It is served with rice.",
                              "verification_status": "verified"})
        result = backgrounds.enrich_background(
            dict(RECIPE, source_url="http://127.0.0.1/private"), MemoryStore(), client,
        )
        self.assertEqual(result["verification_status"], "pending")
        self.assertEqual(result["sources"][0]["url"], "https://en.wikipedia.org/wiki/Philippine_adobo")
        self.assertEqual(client.chat.completions.create.call_args.kwargs["timeout"], 30)
        args, kwargs = self.http.call_args
        self.assertEqual(args[0], backgrounds.WIKIPEDIA_API)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], backgrounds.TIMEOUT)
        self.assertIn("source_excerpts", client.chat.completions.create.call_args.kwargs["messages"][1]["content"])

    def test_full_article_is_retrieved_only_for_identified_page(self):
        full = page(extract="Philippine adobo is a Filipino dish.\n\nHistory\nA source-supported historical note.")
        self.http.side_effect = [wiki(page()), wiki(full)]
        sources = backgrounds.retrieve_evidence(RECIPE)
        self.assertIn("source-supported historical note", sources[0]["excerpt"])
        initial = self.http.call_args_list[0].kwargs["params"]
        complete = self.http.call_args_list[1].kwargs["params"]
        self.assertEqual(initial["exintro"], "1")
        self.assertEqual(complete["exlimit"], "1")
        self.assertNotIn("exintro", complete)
        self.assertEqual(complete["pageids"], "1")

    def test_human_verified_record_is_reused_even_when_old(self):
        approved = record("verified", days_old=100)
        store = MemoryStore(approved)
        result = backgrounds.enrich_background(RECIPE, store, client_with())
        self.assertEqual(result, approved)
        self.assertEqual(store.writes, [])
        self.http.assert_not_called()

    def test_old_version_verified_record_requires_new_evidence_and_review(self):
        approved = record("verified", data_version=DATA_VERSION - 1, sources=[persisted_source()])
        store = MemoryStore(approved)
        with mock.patch.object(backgrounds, "retrieve_evidence", return_value=[persisted_source()]) as retrieve:
            result = backgrounds.enrich_background(RECIPE, store, client_with())
        retrieve.assert_called_once_with(RECIPE)
        self.assertEqual(result["data_version"], DATA_VERSION)
        self.assertEqual(result["verification_status"], "pending")
        self.assertEqual(len(store.writes), 1)

    def test_fresh_pending_record_does_not_repeat_provider_calls(self):
        cached = record()
        result = backgrounds.enrich_background(RECIPE, MemoryStore(cached), client_with())
        self.assertEqual(result, cached)
        self.http.assert_not_called()

    def test_explicit_refresh_preserves_complete_source_backed_cache(self):
        store = MemoryStore(record("verified"))
        with mock.patch.object(backgrounds, "retrieve_evidence", return_value=[SOURCE]):
            result = backgrounds.enrich_background(RECIPE, store, client_with(), refresh=True)
        self.assertEqual(result["verification_status"], "verified")
        self.assertEqual(len(store.writes), 0)

    def test_completed_pending_record_does_not_expire(self):
        store = MemoryStore(record(days_old=31))
        with mock.patch.object(backgrounds, "retrieve_evidence", return_value=[SOURCE]) as retrieve:
            result = backgrounds.enrich_background(RECIPE, store, client_with())
        retrieve.assert_not_called()
        self.assertEqual(result["verification_status"], "pending")

    def test_retry_after_missing_gpt_reuses_fresh_persisted_source_evidence(self):
        source = persisted_source(days_old=2)
        cached = record("unavailable", days_old=2, sources=[source], historical_background='', cultural_background='',
                        unavailable_reason="Source evidence was found, but summary drafting is unavailable.")
        store = MemoryStore(cached)
        with mock.patch.object(backgrounds, "retrieve_evidence") as retrieve:
            result = backgrounds.enrich_background(RECIPE, store, client_with())
        retrieve.assert_not_called()
        self.http.assert_not_called()
        self.assertEqual(result["verification_status"], "pending")
        self.assertEqual(result["sources"][0]["retrieved_at"], source["retrieved_at"])

    def test_repeated_gpt_failure_does_not_refetch_or_extend_evidence_age(self):
        source = persisted_source(days_old=5)
        cached = record("unavailable", days_old=0.1, sources=[source], historical_background='', cultural_background='',
                        unavailable_reason="Temporary summary drafting failure; retry later.")
        store, client = MemoryStore(cached), client_with()
        client.chat.completions.create.side_effect = RuntimeError("private API failure")
        with mock.patch.object(backgrounds, "retrieve_evidence") as retrieve:
            result = backgrounds.enrich_background(RECIPE, store, client)
        retrieve.assert_not_called()
        self.http.assert_not_called()
        self.assertEqual(result["verification_status"], "unavailable")
        self.assertEqual(result["sources"][0]["retrieved_at"], source["retrieved_at"])
        self.assertNotEqual(result["updated_at"], cached["updated_at"])

    def test_expired_evidence_is_refetched_even_if_failure_rows_were_recently_saved(self):
        cached = record("unavailable", days_old=2, sources=[persisted_source(days_old=31)], historical_background='', cultural_background='')
        with mock.patch.object(backgrounds, "retrieve_evidence", return_value=[persisted_source()]) as retrieve:
            result = backgrounds.enrich_background(RECIPE, MemoryStore(cached), client_with())
        retrieve.assert_called_once()
        self.assertEqual(result["verification_status"], "pending")

    def test_source_without_retrieval_timestamp_is_not_reused(self):
        cached = record("unavailable", days_old=2, sources=[dict(SOURCE, matched_country="PHILIPPINES")], historical_background='', cultural_background='')
        with mock.patch.object(backgrounds, "retrieve_evidence", return_value=[persisted_source()]) as retrieve:
            backgrounds.enrich_background(RECIPE, MemoryStore(cached), client_with())
        retrieve.assert_called_once()

    def test_partial_source_support_remains_pending_with_missing_field_reason(self):
        with mock.patch.object(backgrounds, "retrieve_evidence", return_value=[persisted_source()]):
            result = backgrounds.enrich_background(RECIPE, MemoryStore(), client_with({
                "historical_background": "", "cultural_background": "It is commonly served with rice.",
            }))
        self.assertEqual(result["verification_status"], "pending")
        self.assertEqual(result["historical_background"], "")
        self.assertIn("One background field remains unavailable", result["unavailable_reason"])
        self.assertNotIn("approval", result["unavailable_reason"])

    def test_rate_limit_is_persisted_and_not_retried_immediately(self):
        self.http.side_effect = [response({"error": "private provider response"}, status=429)]
        store = MemoryStore()
        first = backgrounds.enrich_background(RECIPE, store, client_with())
        second = backgrounds.enrich_background(RECIPE, store, client_with())
        self.assertEqual(first["verification_status"], "pending")
        self.assertTrue(first["retrieval_note"].startswith("Temporary "))
        self.assertNotIn("private provider response", json.dumps(first))
        self.assertEqual(second, first)
        self.assertEqual(self.http.call_count, 1)

    def test_database_read_failure_never_calls_upstream(self):
        store = mock.Mock()
        store.get.side_effect = RuntimeError("secret credentials in provider exception")
        result = backgrounds.enrich_background(RECIPE, store, client_with())
        self.assertEqual(result["verification_status"], "unavailable")
        self.assertNotIn("secret credentials", json.dumps(result))
        self.http.assert_not_called()
        store.upsert.assert_not_called()

    def test_draft_write_failure_is_unavailable_without_exposing_text_as_verified(self):
        store = mock.Mock(spec=MemoryStore)
        store.get.return_value = None
        store.upsert.side_effect = RuntimeError("secret service key")
        with mock.patch.object(backgrounds, "retrieve_evidence", return_value=[SOURCE]):
            result = backgrounds.enrich_background(RECIPE, store, client_with())
        self.assertEqual(result["verification_status"], "unavailable")
        self.assertEqual(result["cultural_background"], "")
        self.assertNotIn("secret service key", json.dumps(result))

    def test_gpt_failure_is_cached_as_unavailable_without_secret_details(self):
        store, client = MemoryStore(), client_with()
        client.chat.completions.create.side_effect = RuntimeError("private API key")
        with mock.patch.object(backgrounds, "retrieve_evidence", return_value=[SOURCE]):
            result = backgrounds.enrich_background(RECIPE, store, client)
        self.assertEqual(result["verification_status"], "unavailable")
        self.assertEqual(result["historical_background"], "")
        self.assertNotIn("private API key", json.dumps(result))
        self.assertEqual(len(store.writes), 1)

    def test_conditional_save_receives_original_observation_and_preserves_late_approval(self):
        observed = record(days_old=31, id="background-id", historical_background="", cultural_background="")
        approved = record("verified", id="background-id", cultural_background="Reviewed culture.")
        store = mock.Mock(spec=["get", "save_background", "upsert"])
        store.get.return_value = copy.deepcopy(observed)
        # The production method returns the latest row when its conditional
        # update loses to a concurrent approval.
        store.save_background.return_value = approved
        with mock.patch.object(backgrounds, "retrieve_evidence", return_value=[persisted_source()]):
            result = backgrounds.enrich_background(RECIPE, store, client_with())
        self.assertEqual(result, approved)
        self.assertEqual(store.save_background.call_args.kwargs["observed"], observed)
        self.assertEqual(store.save_background.call_args.args[0]["verification_status"], "pending")
        store.upsert.assert_not_called()

    def test_conditional_insert_observes_originally_missing_row(self):
        store = mock.Mock(spec=["get", "save_background", "upsert"])
        store.get.return_value = None
        store.save_background.side_effect = lambda row, observed: row
        with mock.patch.object(backgrounds, "retrieve_evidence", return_value=[persisted_source()]):
            backgrounds.enrich_background(RECIPE, store, client_with())
        self.assertIsNone(store.save_background.call_args.kwargs["observed"])
        store.upsert.assert_not_called()

    def test_source_failure_also_uses_original_observation_when_approval_arrives(self):
        observed = record(days_old=31, id="background-id", historical_background="", cultural_background="")
        approved = record("verified", id="background-id")
        store = mock.Mock(spec=["get", "save_background", "upsert"])
        store.get.return_value = copy.deepcopy(observed)
        store.save_background.return_value = approved
        with mock.patch.object(backgrounds, "retrieve_evidence", return_value=[]):
            result = backgrounds.enrich_background(RECIPE, store, client_with())
        self.assertEqual(result, approved)
        self.assertEqual(store.save_background.call_args.kwargs["observed"], observed)
        self.assertEqual(store.save_background.call_args.args[0]["verification_status"], "pending")
        store.upsert.assert_not_called()

    def test_concurrent_recipes_of_same_dish_share_one_provider_call(self):
        first_fetch_started = threading.Event()
        release_fetch = threading.Event()
        second_call_started = threading.Event()
        second_cache_read = threading.Event()
        count_lock = threading.Lock()

        class ConcurrentStore(MemoryStore):
            reads = 0

            def get(self, table, filters):
                with count_lock:
                    self.reads += 1
                    if self.reads > 1:
                        second_cache_read.set()
                return super().get(table, filters)

        def fetch(recipe):
            first_fetch_started.set()
            if not release_fetch.wait(timeout=3):
                raise AssertionError("Test did not release provider call")
            return [persisted_source()]

        store, client = ConcurrentStore(), client_with()
        first_recipe = dict(RECIPE, recipe_id=1, ingredient_text="pork, vinegar")
        second_recipe = dict(RECIPE, recipe_id=2, title=" adobo ", country="Philippines",
                             ingredient_text="chicken, vinegar")

        def second_enrichment():
            second_call_started.set()
            return backgrounds.enrich_background(second_recipe, store, client)

        with mock.patch.object(backgrounds, "retrieve_evidence", side_effect=fetch) as retrieve, \
                ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(backgrounds.enrich_background, first_recipe, store, client)
            self.assertTrue(first_fetch_started.wait(timeout=2))
            second = pool.submit(second_enrichment)
            try:
                self.assertTrue(second_call_started.wait(timeout=2))
                # The second cache lookup must occur after the first save, under
                # the same dish lock, rather than seeing another cache miss.
                self.assertFalse(second_cache_read.wait(timeout=0.1))
            finally:
                release_fetch.set()
            first_result, second_result = first.result(timeout=3), second.result(timeout=3)
        self.assertEqual(first_result, second_result)
        self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(client.chat.completions.create.call_count, 3)  # evidence pass plus two bounded attempts at the missing field
        self.assertEqual(len(store.writes), 1)


if __name__ == "__main__":
    unittest.main()
