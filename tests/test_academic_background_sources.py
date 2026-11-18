"""Offline regression suite: no external APIs or Supabase writes."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import academic_background_sources as s
import dish_backgrounds as b
from background_generation import generate
from dish_info_common import DATA_VERSION

RECIPE = {"title": "Kimchi", "country": "SOUTH KOREA", "recipe_id": 1}
ABSTRACT = ("Kimchi is a traditional dish in South Korea, prepared and shared in different family "
            "contexts. This study surveys cultural practices associated with preparing kimchi "
            "in domestic settings and examines accounts from participating communities.")


class MemoryStore:
    def __init__(self, row=None):
        self.row = row
        self.writes = []

    def get(self, table, filters):
        return dict(self.row) if self.row else None

    def upsert(self, table, row, on_conflict):
        self.row = dict(row)
        self.writes.append(dict(row))
        return dict(row)


def row(status='pending', **updates):
    result = {"id": 1, "dish_name": "Kimchi", "normalized_name": "kimchi", "country": "SOUTH KOREA",
              "historical_background": "Legacy account is uncertain.",
              "cultural_background": "Traditions vary by household.",
              "generation_method": "gpt_fallback",
              "generation_fields": {"historical_background": "gpt_fallback", "cultural_background": "gpt_fallback"},
              "verification_status": status, "data_version": DATA_VERSION,
              "sources": [], "updated_at": datetime.now(timezone.utc).isoformat()}
    result.update(updates)
    return result


def chat(history='Sourced historical context.', culture='Sourced cultural context.'):
    mock = Mock()
    mock.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps({"historical_background": history,
                                                    "cultural_background": culture})))])
    return mock


class AcademicSourcesTests(unittest.TestCase):
    def test_unesco_requires_country_and_dish_identity(self):
        matching = {"title_en": "Kimchi-making in the Republic of Korea", "countries": ["KR"],
                    "http_url_en": "https://ich.unesco.org/en/RL/01063", "description_en": "In the Republic of Korea, kimchi-making is a shared cultural practice."}
        wrong_country = {"title_en": "Kimchi-making in Somewhere Else", "countries_en": "Brazil"}
        wrong_dish = {"title_en": "Ceramic crafts of South Korea", "countries_en": "South Korea"}
        with patch.object(s, '_json', return_value={'results': [wrong_country, wrong_dish, matching]}):
            sources = s._unesco(RECIPE, ['Kimchi'], 'SOUTH KOREA')
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['source_type'], 'unesco_data')
        self.assertIn('shared cultural practice', sources[0]['excerpt'])
        self.assertEqual(sources[0]['url'], 'https://ich.unesco.org/en/RL/01063')

    def test_crossref_ignores_title_only_result(self):
        items = [{'DOI': '10.1000/example', 'title': ['Kimchi culture in South Korea']},
                 {'DOI': '10.1000/example2', 'title': ['Kimchi cuisine'], 'abstract': 'A study of an unrelated country. ' * 15}]
        with patch.object(s, '_json', return_value={'message': {'items': items}}):
            self.assertEqual(s._crossref(RECIPE, ['Kimchi'], 'SOUTH KOREA'), [])

    def test_crossref_requires_dish_and_country_together_in_abstract(self):
        items = [{'DOI': '10.1000/example', 'title': ['History of kimchi'], 'abstract': '<jats:p>' + ABSTRACT + '</jats:p>'}]
        with patch.object(s, '_json', return_value={'message': {'items': items}}):
            found = s._crossref(RECIPE, ['Kimchi'], 'SOUTH KOREA')
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['source_type'], 'crossref')
        self.assertTrue(found[0]['url'].startswith('https://doi.org/'))
        self.assertNotIn('<jats:', found[0]['excerpt'])

    def test_books_description_not_just_title(self):
        items = [{'id': 'bookABCD', 'volumeInfo': {'title': 'Kimchi in South Korea'}},
                 {'id': 'bookEFGH', 'volumeInfo': {'title': 'Asian Cooking', 'description': ABSTRACT}}]
        with patch.object(s, '_json', return_value={'items': items}):
            found = s._google_books(RECIPE, ['Kimchi'], 'SOUTH KOREA')
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['evidence_kind'], 'publisher_description')

    def test_primary_evidence_prevents_wikipedia_lookup(self):
        primary = s._source(title='UNESCO Kimchi', url='https://ich.unesco.org/en/RL/01063',
                            excerpt='Kimchi-making is a cultural tradition in South Korea.',
                            kind='unesco_data', country='SOUTH KOREA', alias='Kimchi',
                            basis='official_heritage_record', evidence_kind='official_listing')
        with patch('academic_background_sources.retrieve_academic_evidence', return_value=[primary]), \
             patch.object(b, '_retrieve_wikipedia_last_resort', side_effect=AssertionError('Wikipedia must not be called')):
            self.assertEqual(b.retrieve_evidence(RECIPE), [primary])

    def test_wikipedia_is_last_resort_after_empty_academic_search(self):
        last = {'title': 'Kimchi', 'url': 'https://en.wikipedia.org/wiki/Kimchi',
                'source_type': 'wikipedia', 'excerpt': 'Kimchi is a Korean dish.',
                'matched_country': 'SOUTH KOREA'}
        with patch('academic_background_sources.retrieve_academic_evidence', return_value=[]) as academic, \
             patch.object(b, '_retrieve_wikipedia_last_resort', return_value=[last]) as wiki:
            result = b.retrieve_evidence(RECIPE)
        academic.assert_called_once_with(RECIPE)
        wiki.assert_called_once_with(RECIPE)
        self.assertEqual([x['source_type'] for x in result], ['wikipedia'])
        self.assertEqual(result[0]['retrieval_tier'], 'wikipedia_last_resort')

    def test_wikipedia_not_substituted_when_any_academic_provider_matches(self):
        abstract = s._source(title='Study', url='https://doi.org/10.1000/kimchi',
                             excerpt=ABSTRACT, kind='crossref', country='SOUTH KOREA',
                             alias='Kimchi', basis='research_abstract_dish_country',
                             evidence_kind='publication_abstract')
        with patch.object(s, '_unesco', return_value=[]), \
             patch.object(s, '_crossref', return_value=[abstract]), \
             patch.object(s, '_google_books', return_value=[]), \
             patch.object(b, '_retrieve_wikipedia_last_resort',
                          side_effect=AssertionError('Wikipedia must be last resort only')):
            result = b.retrieve_evidence(RECIPE)
        self.assertEqual(result, [abstract])

    def test_all_three_api_calls_run_in_priority_order(self):
        calls = []
        def make(provider, result):
            def execute(*args):
                calls.append(provider)
                return result
            return execute
        with patch.object(s, '_unesco', side_effect=make('unesco', [])), \
             patch.object(s, '_crossref', side_effect=make('crossref', [])), \
             patch.object(s, '_google_books', side_effect=make('google_books', [])):
            self.assertEqual(s.retrieve_academic_evidence(RECIPE), [])
        self.assertEqual(calls, ['unesco', 'crossref', 'google_books'])

    def test_source_priority_overrides_excerpt_richness(self):
        unesco = {'url':'https://ich.unesco.org/en/RL/01063', 'excerpt': 'A heritage practice.', 'source_type':'unesco_data'}
        crossref = {'url':'https://doi.org/10.1000/kimchi', 'excerpt':'An extensive research abstract on Korean cuisine. ' * 45, 'source_type':'crossref'}
        books = {'url':'https://books.google.com/books?id=abcXYZ', 'excerpt':'Many informative book descriptions. ' * 60, 'source_type':'google_books'}
        order = s.choose_diverse_evidence([books, crossref, unesco])
        self.assertEqual([r['source_type'] for r in order], ['unesco_data', 'crossref', 'google_books'])

    def test_old_country_specific_sources_do_not_become_new_evidence(self):
        curated = {'url': 'https://example.org/old', 'excerpt':'Old hand-entered note',
                   'source_type':'country_curated'}
        self.assertEqual(s.choose_diverse_evidence([curated]), [])
        cached = {'data_version': DATA_VERSION, 'sources': [{
            **curated, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
            'matched_country': 'SOUTH KOREA', 'retrieval_method':'curated_paraphrase_not_live_API',
        }]}
        self.assertEqual(b._cached_sources(cached, RECIPE), [])

    def test_only_wikipedia_does_not_overwrite_existing_sourced_text(self):
        original = row(generation_method='source_based',
                       generation_fields={'historical_background':'source_based',
                                          'cultural_background':'source_based'},
                       sources=[{'source_type':'wikipedia',
                                 'url':'https://en.wikipedia.org/wiki/Kimchi',
                                 'excerpt':'Kimchi has a Korean history.'}])
        store = MemoryStore(original)
        wiki = dict(original['sources'][0], retrieval_tier='wikipedia_last_resort')
        diagnostic = {}
        with patch.object(b, 'retrieve_evidence', return_value=[wiki]):
            outcome = generate(RECIPE, store, chat(history='A much longer Wikipedia-based rewritten history that might sound informative.',
                                                   culture='Longer cultural story taken from Wikipedia.'),
                               upgrade_sources=True, improve_existing=True, diagnostics=diagnostic)
        self.assertEqual(outcome, original)
        self.assertEqual(store.writes, [])
        self.assertIn('Only Wikipedia fallback', diagnostic['reason'])

    def test_api_failure_still_allows_wikipedia_as_last_resort(self):
        failed = b.EvidenceUnavailable('Provider temporarily unavailable', code='provider_deferred', provider='api.crossref.org')
        wiki = {'source_type':'wikipedia', 'url':'https://en.wikipedia.org/wiki/Kimchi',
                'excerpt':'Kimchi is a Korean dish.', 'matched_country':'SOUTH KOREA'}
        with patch('academic_background_sources.retrieve_academic_evidence', side_effect=failed), \
             patch.object(b, '_retrieve_wikipedia_last_resort', return_value=[wiki]) as lookup:
            result = b.retrieve_evidence(RECIPE)
        lookup.assert_called_once()
        self.assertEqual(result[0]['retrieval_tier'], 'wikipedia_last_resort')

    def test_old_wikipedia_cache_is_not_reused_for_drafting(self):
        old = {"data_version": DATA_VERSION, "sources": [{"source_type": "wikipedia",
              "title": "Kimchi", "url": "https://en.wikipedia.org/wiki/Kimchi", "excerpt": "Old.",
              "matched_country": "SOUTH KOREA", "matched_aliases": ['Kimchi'],
              "retrieved_at": datetime.now(timezone.utc).isoformat()}]}
        self.assertEqual(b._cached_sources(old, RECIPE), [])

    def test_upgrade_does_nothing_if_no_new_evidence(self):
        cached = row()
        store = MemoryStore(cached)
        with patch.object(b, 'retrieve_evidence', return_value=[]):
            self.assertEqual(generate(RECIPE, store, chat(), upgrade_sources=True), cached)
        self.assertEqual(store.writes, [])

    def test_upgrade_writes_source_backed_claims_and_preserves_approval_state(self):
        source = s._source(title='Evidence', url='https://doi.org/10.1000/example', excerpt=ABSTRACT,
                           kind='crossref', country='SOUTH KOREA', alias='Kimchi',
                           basis='research_abstract_dish_country', evidence_kind='publication_abstract')
        store = MemoryStore(row())
        with patch.object(b, 'retrieve_evidence', return_value=[source]):
            result = generate(RECIPE, store, chat(), upgrade_sources=True)
        self.assertEqual(result['historical_background'], 'Sourced historical context.')
        self.assertEqual(result['generation_method'], 'source_based')
        self.assertEqual(result['sources'][0]['source_type'], 'crossref')
        self.assertEqual(result['verification_status'], 'pending')
        self.assertEqual(len(store.writes), 1)

    def test_partial_upgrade_keeps_previous_text(self):
        store = MemoryStore(row())
        source = s._source(title='Evidence', url='https://doi.org/10.1000/example', excerpt=ABSTRACT,
                           kind='crossref', country='SOUTH KOREA', alias='Kimchi',
                           basis='research_abstract_dish_country', evidence_kind='publication_abstract')
        with patch.object(b, 'retrieve_evidence', return_value=[source]):
            result = generate(RECIPE, store, chat(history='', culture='A more informative cultural account.'), upgrade_sources=True)
        self.assertEqual(result['historical_background'], 'Legacy account is uncertain.')
        self.assertEqual(result['generation_fields']['historical_background'], 'gpt_fallback')
        self.assertEqual(result['generation_fields']['cultural_background'], 'source_based')

    def test_approved_record_cannot_be_overwritten(self):
        cached = row(status='verified')
        store = MemoryStore(cached)
        with patch.object(b, 'retrieve_evidence', side_effect=AssertionError('No retrieval')):
            self.assertEqual(generate(RECIPE, store, chat(), upgrade_sources=True), cached)
        self.assertEqual(store.writes, [])

    def test_api_fixed_host_allowlist(self):
        with self.assertRaises(ValueError):
            b._fetch('https://evil.example/api')

    def test_preview_does_not_call_external_providers_or_write(self):
        from upgrade_background_sources import run
        store = MemoryStore(row())
        with patch.object(b, 'enrich_background', side_effect=AssertionError('preview must not enrich')):
            summary = run(store, [RECIPE], apply=False)
        self.assertEqual(summary['preview_only'], 1)
        self.assertEqual(store.writes, [])


if __name__ == '__main__':
    unittest.main()
