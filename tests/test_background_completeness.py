"""Conservative aliases, recoverable provider errors, and measurable background lookups."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dish_backgrounds as backgrounds
from dish_info_common import DATA_VERSION


RECIPE = {"title": "Beef Curry", "country": "INDONESIA", "alternative_title": "Rendang Daging"}


def response(data, status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = data.encode() if isinstance(data, str) else json.dumps(data).encode()
    result._content_consumed = True
    result.url = backgrounds.WIKIPEDIA_API
    result.headers.update(headers or {})
    return result


def article(title="Rendang", extract=None, **changes):
    return dict({"pageid": 1, "title": title,
                 "extract": extract or "Rendang is an Indonesian dish. It is commonly served at festive meals.",
                 "pageprops": {}}, **changes)


def wiki(page, **query):
    return response({"query": {"pages": [page], **query}})


def client():
    result = mock.Mock()
    result.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps({"historical_background": "A supported history.",
                                                    "cultural_background": "A supported cultural note."})))])
    return result


def cached(status="pending", version=1, **changes):
    return dict({"id": "background-id", "dish_name": "Beef Curry", "normalized_name": "beef curry",
                 "country": "INDONESIA", "historical_background": "A supported history.",
                 "cultural_background": "A supported cultural note.", "verification_status": status,
                 "unavailable_reason": "", "sources": [], "data_version": DATA_VERSION,
                 "enrichment_version": version, "updated_at": datetime.now(timezone.utc).isoformat()}, **changes)


class MemoryStore:
    def __init__(self, row=None):
        self.row, self.events = copy.deepcopy(row), []

    def get(self, table, filters):
        return copy.deepcopy(self.row)

    def upsert(self, table, row, on_conflict):
        self.row = copy.deepcopy(row)
        return copy.deepcopy(row)

    def record_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


class BackgroundCompletenessTests(unittest.TestCase):
    def setUp(self):
        # These are regression checks for the retired Wikipedia adapter. The
        # production retrieve_evidence() now uses only official/scholarly APIs.
        self.enterContext(mock.patch.object(backgrounds, "retrieve_evidence",
                                            wraps=backgrounds._retrieve_legacy_wikipedia_evidence))
        self.enterContext(mock.patch.dict(backgrounds._PROVIDER_BACKOFF, {}, clear=True))
        self.http = self.enterContext(mock.patch.object(requests, "get", side_effect=AssertionError("Unexpected live request")))
        self.sleep = self.enterContext(mock.patch.object(backgrounds.time, "sleep"))
        self.enterContext(mock.patch.object(backgrounds, "_ACTION_API_COOLDOWN_UNTIL", 0.0))
        self.enterContext(mock.patch.object(backgrounds, "_ACTION_API_COOLDOWN_REASON", "provider_error"))

    def test_authoritative_redirect_resolves_an_alternative_name(self):
        fixture = article()
        self.http.side_effect = [wiki(fixture, redirects=[{"from": "Rendang Daging", "to": "Rendang"}]), wiki(fixture)]
        sources = backgrounds.retrieve_evidence(RECIPE)
        self.assertEqual(sources[0]["name_match_basis"], "mediawiki_redirect")
        self.assertEqual(sources[0]["title"], "Rendang")
        self.assertEqual(sources[0]["matched_country"], "INDONESIA")

    def test_redirect_cannot_override_wrong_country(self):
        fixture = article(extract="Rendang is an Indonesian dish.")
        self.http.side_effect = [wiki(fixture, redirects=[{"from": "Rendang Daging", "to": "Rendang"}]),
                                 response({"query": {"search": []}})]
        self.assertEqual(backgrounds.retrieve_evidence(dict(RECIPE, country="JAPAN")), [])

    def test_wikidata_exact_alias_can_establish_article_name(self):
        fixture = article(title="Green curry", extract="Green curry is a Thai curry.", pageprops={"wikibase_item": "Q1"})
        self.http.side_effect = [wiki(fixture)]
        with mock.patch.object(backgrounds, "_entities", return_value={
            "Q1": {"aliases": {"en": [{"value": "Gaeng Keow Wan"}]}},
        }):
            sources = backgrounds._matching_sources([fixture], ["Gaeng Keow Wan"], "THAILAND")
        self.assertEqual(sources[0]["name_match_basis"], "wikidata_label_or_alias")

    def test_spacing_variants_are_exact_not_fuzzy_substitutions(self):
        self.assertTrue(backgrounds._title_matches("Sigeumchi-namul", ["Sigeumchi Namul"], ("korean",)))
        self.assertTrue(backgrounds._title_matches("Shengjianbao", ["Sheng Jian Bao"], ("chinese",)))
        self.assertFalse(backgrounds._title_matches("Banh xeo", ["Banh mi"], ("vietnamese",)))

    def test_food_word_in_dish_title_does_not_hide_its_definition(self):
        for text, country in [("Green curry is a Thai curry.", "THAILAND"),
                              ("Kimchi soup is a Korean soup.", "SOUTH KOREA"),
                              ("Rice cake is a Chinese dessert.", "CHINA")]:
            self.assertTrue(backgrounds._defines_food(text))
            self.assertTrue(backgrounds._text_matches_country(text, backgrounds._terms(country)))

    def test_all_dataset_supplied_names_are_searched(self):
        recipe = dict(RECIPE, alternative_names=["Daging Rendang", "Rendang"])
        with mock.patch.object(backgrounds, "_details", return_value=[]), \
                mock.patch.object(backgrounds, "_request", return_value={"query": {"search": []}}) as query:
            self.assertEqual(backgrounds._action_evidence(recipe), [])
        search = query.call_args.args[1]["srsearch"]
        for name in ("Rendang Daging", "Daging Rendang", "Rendang"):
            self.assertIn(name, search)
        self.assertIn(" OR ", search)

    def test_transient_http_failure_is_retried(self):
        self.http.side_effect = [response({}, 503), response({"query": {"pages": []}})]
        result = backgrounds._request(backgrounds.WIKIPEDIA_API, {"action": "query"})
        self.assertEqual(result["query"]["pages"], [])
        self.assertEqual(self.http.call_count, 2)
        self.sleep.assert_called_once_with(1)

    def test_short_retry_after_is_respected_but_long_limit_is_deferred(self):
        self.http.side_effect = [response({}, 429, {"Retry-After": "2"}), response({"query": {}})]
        backgrounds._request(backgrounds.WIKIPEDIA_API, {"action": "query"})
        self.sleep.assert_called_once_with(2.0)
        self.http.reset_mock()
        self.sleep.reset_mock()
        backgrounds._PROVIDER_BACKOFF.clear()
        self.http.side_effect = [response({}, 429, {"Retry-After": "600"})]
        with self.assertRaises(backgrounds.EvidenceUnavailable) as error:
            backgrounds._request(backgrounds.WIKIPEDIA_API, {"action": "query"})
        self.assertEqual(error.exception.code, "rate_limit")
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(self.http.call_count, 1)
        self.sleep.assert_not_called()

    def test_wikipedia_maxlag_retries_are_bounded(self):
        self.http.side_effect = [response({"error": {"code": "maxlag"}}) for _ in range(3)]
        with self.assertRaises(backgrounds.EvidenceUnavailable) as error:
            backgrounds._request(backgrounds.WIKIPEDIA_API, {"action": "query"})
        self.assertEqual(error.exception.attempts, 3)
        self.assertEqual(error.exception.code, "maxlag")
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [5, 10])

    def test_documented_rest_summary_is_used_after_exhausted_lag(self):
        self.http.side_effect = [response({}, 404), response({"type": "standard", **article()})]
        error = backgrounds.EvidenceUnavailable("Temporary lag", code="maxlag", provider="en.wikipedia.org", attempts=3)
        with mock.patch.object(backgrounds, "_action_evidence", side_effect=error):
            sources = backgrounds.retrieve_evidence(RECIPE)
        self.assertEqual(sources[0]["name_match_basis"], "rest_summary_redirect")
        self.assertTrue(sources[0]["summary_only"])
        self.assertTrue(all(call.args[0].startswith(backgrounds.WIKIPEDIA_REST_SUMMARY) for call in self.http.call_args_list))

    def test_rest_fallback_does_not_bypass_a_rate_limit(self):
        error = backgrounds.EvidenceUnavailable("Temporary rate limit", code="rate_limit", status_code=429)
        with mock.patch.object(backgrounds, "_action_evidence", side_effect=error), \
                mock.patch.object(backgrounds, "_rest_evidence") as fallback:
            with self.assertRaises(backgrounds.EvidenceUnavailable):
                backgrounds.retrieve_evidence(RECIPE)
        fallback.assert_not_called()

    def test_action_lag_circuit_prevents_repeated_action_api_requests(self):
        error = backgrounds.EvidenceUnavailable("Temporary lag", code="maxlag")
        with mock.patch.object(backgrounds, "_action_evidence", side_effect=error) as action, \
                mock.patch.object(backgrounds, "_rest_evidence", return_value=[{"title": "Rendang"}]) as fallback:
            backgrounds.retrieve_evidence(RECIPE)
            backgrounds.retrieve_evidence(dict(RECIPE, title="Beef Rendang"))
        self.assertEqual(action.call_count, 1)
        self.assertEqual(fallback.call_count, 2)

    def test_no_match_during_search_outage_is_not_misreported_as_permanent_unmatched(self):
        error = backgrounds.EvidenceUnavailable("Temporary lag", code="maxlag")
        with mock.patch.object(backgrounds, "_action_evidence", side_effect=error), \
                mock.patch.object(backgrounds, "_rest_evidence", return_value=[]), \
                mock.patch.object(backgrounds, "_heritage_sources", return_value=[]):
            with self.assertRaises(backgrounds.EvidenceUnavailable) as result:
                backgrounds.retrieve_evidence(RECIPE)
        self.assertEqual(result.exception.code, "maxlag")

    def test_rest_summary_still_rejects_wrong_cuisine_and_disambiguation(self):
        for data in [{"type": "standard", **article(extract="Rendang is a Japanese dish.")},
                     {"type": "disambiguation", **article()}]:
            self.assertEqual(backgrounds._summary_source(data, "INDONESIA", "Rendang Daging", backgrounds.EvidenceUnavailable("lag")), [])

    def test_sentence_case_summary_path_keeps_exact_dish_identity(self):
        self.http.side_effect = [response({}, 404), response({"type": "standard", **article(
            title="Green curry", extract="Green curry is a Thai curry.")})]
        sources = backgrounds._rest_evidence({"title": "Green Curry", "country": "THAILAND"}, backgrounds.EvidenceUnavailable("lag"))
        self.assertEqual(sources[0]["title"], "Green curry")
        self.assertEqual(self.http.call_args_list[0].args[0], backgrounds.WIKIPEDIA_REST_SUMMARY + "Green_Curry")
        self.assertEqual(self.http.call_args_list[1].args[0], backgrounds.WIKIPEDIA_REST_SUMMARY + "Green_curry")

    def test_rest_search_preserves_authoritative_matched_redirect_and_confirms_page_id(self):
        result = {"pages": [{"id": 1, "key": "Rendang", "title": "Rendang", "matched_title": "Rendang Daging"}]}
        with mock.patch.object(backgrounds, "_fetch", return_value=json.dumps(result).encode()) as fetch, \
                mock.patch.object(backgrounds, "_load_rest_summary", return_value={"type": "standard", **article()}) as summary:
            sources = backgrounds._rest_search_evidence(RECIPE, backgrounds._aliases(RECIPE), "INDONESIA", backgrounds.EvidenceUnavailable("lag"))
        self.assertEqual(sources[0]["name_match_basis"], "rest_search_redirect")
        self.assertEqual(sources[0]["matched_aliases"], ["Rendang Daging"])
        self.assertEqual(fetch.call_args.args[0], backgrounds.WIKIPEDIA_REST_SEARCH)
        self.assertIn('"Rendang Daging"', fetch.call_args.args[1]["q"])
        summary.assert_called_once_with("Rendang")

    def test_rest_search_does_not_accept_a_generic_parent_without_a_matching_alias(self):
        result = {"pages": [{"id": 1, "key": "Rendang", "title": "Rendang", "matched_title": None}]}
        with mock.patch.object(backgrounds, "_fetch", return_value=json.dumps(result).encode()), \
                mock.patch.object(backgrounds, "_load_rest_summary") as summary:
            self.assertEqual(backgrounds._rest_search_evidence(RECIPE, backgrounds._aliases(RECIPE), "INDONESIA", backgrounds.EvidenceUnavailable("lag")), [])
        summary.assert_not_called()

    def test_rest_search_rejects_mismatched_page_id_and_wrong_country(self):
        result = {"pages": [{"id": 1, "key": "Rendang", "title": "Rendang", "matched_title": "Rendang Daging"}]}
        for data in [{"type": "standard", **article(pageid=999)},
                     {"type": "standard", **article(extract="Rendang is a Japanese dish.")}]:
            with mock.patch.object(backgrounds, "_fetch", return_value=json.dumps(result).encode()), \
                    mock.patch.object(backgrounds, "_load_rest_summary", return_value=data):
                self.assertEqual(backgrounds._rest_search_evidence(RECIPE, backgrounds._aliases(RECIPE), "INDONESIA", backgrounds.EvidenceUnavailable("lag")), [])

    def test_one_429_blocks_other_endpoints_on_same_host_without_more_requests(self):
        self.http.side_effect = [response({}, 429), response({"entities": {}})]
        with self.assertRaises(backgrounds.EvidenceUnavailable) as limited:
            backgrounds._fetch(backgrounds.WIKIPEDIA_API)
        self.assertEqual(limited.exception.code, "rate_limit")
        for endpoint in [backgrounds.WIKIPEDIA_REST_SEARCH, backgrounds.WIKIPEDIA_REST_SUMMARY + "Rendang"]:
            with self.assertRaises(backgrounds.EvidenceUnavailable) as deferred:
                backgrounds._fetch(endpoint)
            self.assertEqual(deferred.exception.code, "provider_deferred")
            self.assertEqual(deferred.exception.attempts, 0)
            self.assertIsNotNone(deferred.exception.next_retry_at)
        self.assertEqual(self.http.call_count, 1)
        backgrounds._fetch(backgrounds.WIKIDATA_API)
        self.assertEqual(self.http.call_count, 2)  # A distinct host is not implicitly throttled.

    def test_resume_restores_only_future_known_provider_windows(self):
        future = (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()
        expired = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        events = [{"details": {"provider_cooldowns": [
            {"host": "en.wikipedia.org", "retry_at": future},
            {"host": "www.wikidata.org", "retry_at": expired},
            {"host": "untrusted.invalid", "retry_at": future},
        ]}}]
        self.assertEqual(backgrounds.restore_provider_cooldowns(events), ["en.wikipedia.org"])
        with self.assertRaises(backgrounds.EvidenceUnavailable) as deferred:
            backgrounds._fetch(backgrounds.WIKIPEDIA_REST_SUMMARY + "Rendang")
        self.assertEqual(deferred.exception.attempts, 0)
        self.http.assert_not_called()

    def test_zero_attempt_cooldown_is_logged_as_deferred_not_an_api_error(self):
        error = backgrounds.EvidenceUnavailable("Temporary provider cooldown; retry later.", code="provider_deferred",
                                                provider="en.wikipedia.org", attempts=0)
        store = MemoryStore()
        with mock.patch.object(backgrounds, "retrieve_evidence", side_effect=error):
            backgrounds.enrich_background(RECIPE, store, client())
        args, kwargs = store.events[0]
        self.assertEqual(args[2], "success")
        self.assertEqual(kwargs["details"]["api_error_count"], 0)
        self.assertEqual(kwargs["details"]["provider_attempts"], {"openai": 1})

    def test_rate_limit_retry_window_is_persisted_in_lookup_diagnostics(self):
        self.http.side_effect = [response({}, 429, {"Retry-After": "90"})]
        store = MemoryStore()
        backgrounds.enrich_background(RECIPE, store, client())
        args, kwargs = store.events[0]
        self.assertEqual(args[2], "success")
        self.assertEqual(kwargs["details"]["api_error_count"], 1)
        windows = kwargs["details"]["provider_cooldowns"]
        self.assertEqual(windows[0]["host"], "en.wikipedia.org")
        self.assertTrue(windows[0]["retry_at"])

    def test_old_incomplete_failure_is_retried_after_matcher_improvement(self):
        row = cached("unavailable", historical_background="", cultural_background="", unavailable_reason="No source matched")
        with mock.patch.object(backgrounds, "retrieve_evidence", return_value=[{"title": "Rendang", "excerpt": "Evidence."}] ) as retrieve:
            result = backgrounds.enrich_background(RECIPE, MemoryStore(row), client())
        retrieve.assert_called_once()
        self.assertEqual(result["enrichment_version"], backgrounds.BACKGROUND_RETRIEVAL_VERSION)
        self.assertEqual(result["verification_status"], "pending")

    def test_valid_existing_content_is_retained_without_network_or_event_writes(self):
        store = MemoryStore(cached(version=1))
        result = backgrounds.enrich_background(RECIPE, store, client())
        self.assertEqual(result, store.row)
        self.http.assert_not_called()
        self.assertEqual(store.events, [])

    def test_current_negative_result_is_cached_without_another_provider_call(self):
        row = cached("unavailable", version=backgrounds.BACKGROUND_RETRIEVAL_VERSION,
                     historical_background="", cultural_background="", unavailable_reason="No matching source")
        store = MemoryStore(row)
        result = backgrounds.enrich_background(RECIPE, store, client())
        self.assertEqual(result, row)
        self.http.assert_not_called()
        self.assertEqual(store.events, [])

    def test_retrieved_redirect_evidence_can_be_reused_without_article_title_equivalence(self):
        row = cached("unavailable", version=backgrounds.BACKGROUND_RETRIEVAL_VERSION,
                     historical_background="", cultural_background="", sources=[{
                         "title": "Rendang", "url": "https://en.wikipedia.org/wiki/Rendang", "excerpt": "Source evidence.",
                         "source_type": "wikipedia", "matched_country": "INDONESIA", "matched_aliases": ["Rendang Daging"],
                         "name_match_basis": "mediawiki_redirect", "retrieval_version": backgrounds.BACKGROUND_RETRIEVAL_VERSION,
                         "retrieved_at": datetime.now(timezone.utc).isoformat(),
                     }])
        # Wikipedia references remain attached to old records, but are no
        # longer reused as evidence for new source-grounded drafts.
        self.assertEqual(len(backgrounds._cached_sources(row, RECIPE)), 0)
        self.assertEqual(backgrounds._cached_sources(row, dict(RECIPE, alternative_title="Different Food")), [])

    def test_openai_transient_failure_is_retried_and_logged_without_response_contents(self):
        draft_client = client()
        answer = draft_client.chat.completions.create.return_value
        error = RuntimeError("private API key must never be logged")
        error.status_code = 429
        error.response = SimpleNamespace(headers={"Retry-After": "2"})
        draft_client.chat.completions.create.side_effect = [error, answer]
        store = MemoryStore()
        with mock.patch.object(backgrounds, "retrieve_evidence", return_value=[{"title": "Rendang", "excerpt": "Evidence"}]):
            result = backgrounds.enrich_background(RECIPE, store, draft_client)
        self.assertEqual(result["verification_status"], "pending")
        self.assertEqual(draft_client.chat.completions.create.call_count, 2)
        self.sleep.assert_called_once_with(2.0)
        self.assertEqual(store.events[0][1]["details"]["api_error_count"], 1)
        self.assertNotIn("private API key", json.dumps(store.events))

    def test_optional_official_heritage_source_has_explicit_identity_and_fixed_url(self):
        title = "Kimjang, making and sharing kimchi in the Republic of Korea"
        body = f'<html><title>{title}</title><p>Kimchi is made and shared among communities during kimjang in preparation for winter.</p></html>'
        self.http.side_effect = [response(body)]
        with mock.patch.object(backgrounds, "_action_evidence", return_value=[]):
            sources = backgrounds.retrieve_evidence({"title": "Kimchi", "country": "SOUTH KOREA"})
        self.assertEqual(sources[0]["source_type"], "unesco")
        self.assertEqual(sources[0]["name_match_basis"], "official_heritage_identity")
        self.assertIn(sources[0]["url"], backgrounds._HERITAGE_URLS)

    def test_provider_retries_are_logged_even_when_lookup_eventually_succeeds(self):
        fixture = article(title="Rendang Daging")
        self.http.side_effect = [response({}, 503), wiki(fixture), wiki(fixture)]
        store = MemoryStore()
        result = backgrounds.enrich_background(RECIPE, store, client())
        self.assertEqual(result["verification_status"], "pending")
        self.assertEqual(len(store.events), 1)
        args, kwargs = store.events[0]
        self.assertEqual(args[:3], ("background_lookup", "INDONESIA|beef curry", "success"))
        self.assertEqual(kwargs["details"]["api_error_count"], 1)
        self.assertEqual(kwargs["details"]["provider_attempts"], {"en.wikipedia.org": 3, "openai": 1})


if __name__ == "__main__":
    unittest.main()
