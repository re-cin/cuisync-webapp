import unittest
from unittest.mock import patch
from test_dish_backgrounds import MemoryStore, client_with, record, RECIPE, persisted_source
import dish_backgrounds as b
from background_generation import DISCLAIMER, _parse
from dish_information import public_information
from test_dish_information import PublicStore
from enrichment_report import build_report
from test_enrichment_report import Store, recipe


class FallbackTests(unittest.TestCase):
    def test_rate_limited_sources_use_gpt_without_invented_sources_and_cache(self):
        client = client_with({'historical_background': 'Its specific origins are uncertain.',
                              'cultural_background': 'Its cultural associations vary by household.'})
        store = MemoryStore()
        error = b.EvidenceUnavailable('Temporary rate limit.', provider='en.wikipedia.org', attempts=0)
        with patch.object(b, 'retrieve_evidence', side_effect=error) as lookup:
            first = b.enrich_background(RECIPE, store, client)
            second = b.enrich_background(RECIPE, store, client)
        self.assertEqual(first, second)
        self.assertEqual('gpt_fallback', first['generation_method'])
        self.assertEqual([], first['sources'])
        self.assertEqual('pending', first['verification_status'])
        self.assertEqual(1, client.chat.completions.create.call_count)
        lookup.assert_called_once()
        self.assertIn('general knowledge', client.chat.completions.create.call_args.kwargs['messages'][0]['content'])

    def test_partial_source_text_and_links_survive_fallback(self):
        source = persisted_source()
        cached = record(historical_background='Existing sourced history.', cultural_background='',
                        generation_method='source_based', generation_fields={'historical_background':'source_based'},
                        enrichment_version=2, sources=[source])
        client = client_with({'historical_background': 'Must not overwrite history.', 'cultural_background':'Specific traditions are uncertain.'})
        # The evidence-only pass cannot support culture, then fallback fills it.
        response = client.chat.completions.create.return_value
        client.chat.completions.create.side_effect = [client_with({'historical_background':'', 'cultural_background':''}).chat.completions.create.return_value, response]
        result = b.enrich_background(RECIPE, MemoryStore(cached), client)
        self.assertEqual('Existing sourced history.', result['historical_background'])
        self.assertEqual('gpt_fallback', result['generation_fields']['cultural_background'])
        self.assertEqual('source_based', result['generation_fields']['historical_background'])
        self.assertEqual(source['url'], result['sources'][0]['url'])

    def test_completed_source_cache_never_downgraded_even_on_refresh(self):
        cached=record(days_old=300)
        client=client_with()
        with patch.object(b,'retrieve_evidence') as lookup:
            self.assertEqual(cached,b.enrich_background(RECIPE,MemoryStore(cached),client,refresh=True))
        lookup.assert_not_called();client.chat.completions.create.assert_not_called()

    def test_gpt_error_preserves_partial_text(self):
        cached=record(historical_background='Source history.', cultural_background='', sources=[persisted_source()],enrichment_version=2)
        client=client_with();client.chat.completions.create.side_effect=RuntimeError('private')
        result=b.enrich_background(RECIPE,MemoryStore(cached),client)
        self.assertEqual('Source history.',result['historical_background'])
        self.assertEqual('pending',result['verification_status'])
        self.assertNotIn('private',str(result))

    def test_fallback_dates_and_links_rejected(self):
        for text in ['Invented in 1800.', 'See https://invented.example/a']:
            with self.assertRaises(ValueError):
                _parse(client_with({'historical_background':text}).chat.completions.create.return_value, fallback=True)

    def test_source_free_pending_background_displays_disclaimer(self):
        row=record(sources=[],generation_method='gpt_fallback')
        result=public_information(RECIPE,schedule=False,store=PublicStore([row]))
        self.assertEqual(row['historical_background'],result['background']['historical_background'])
        self.assertEqual(DISCLAIMER,result['background']['disclaimer'])
        self.assertEqual('AI-Generated Information',result['background']['disclaimer_title'])
        self.assertEqual([],result['background']['sources'])

    def test_method_counts_are_disjoint(self):
        recipes=[recipe(1,'A'),recipe(2,'B'),recipe(3,'C'),recipe(4,'D')]
        rows=[dict(record(),normalized_name='a',country='SOUTH KOREA',generation_method='source_based'),
              dict(record(),normalized_name='b',country='SOUTH KOREA',sources=[],generation_method='gpt_fallback'),
              dict(record(),normalized_name='c',country='SOUTH KOREA',generation_method='gpt_fallback',generation_fields={'historical_background':'source_based','cultural_background':'gpt_fallback'})]
        r=build_report(recipes,Store(dish_backgrounds=rows))
        self.assertEqual([1,1,1,1],[r['background_generation'][key]['covered'] for key in ['source_based','gpt_fallback','mixed','unavailable']])
