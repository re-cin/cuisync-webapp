"""Coverage and recovery checks for historical and cultural generation."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import dish_backgrounds as backgrounds
import dish_information as information
from background_generation import background_cache_valid
from dish_info_common import country_key, normalize
from test_dish_backgrounds import MemoryStore, RECIPE, client_with, record


class AllDishBackgroundTests(unittest.TestCase):
    def test_every_dataset_dish_can_generate_both_sections_without_research(self):
        recipes = json.loads((Path(__file__).resolve().parents[1] / 'recipes.json').read_text(encoding='utf-8'))
        dishes = {(normalize(r.get('title')), country_key(r.get('country'))): r for r in recipes}
        client = client_with({'historical_background': 'Its specific historical origins are uncertain.',
                              'cultural_background': 'Its specific cultural associations are uncertain.'})
        with patch.object(backgrounds, 'retrieve_evidence', return_value=[]):
            for identity, recipe in dishes.items():
                with self.subTest(dish=identity):
                    store = MemoryStore()
                    row = backgrounds.enrich_background(recipe, store, client)
                    self.assertTrue(row['historical_background'])
                    self.assertTrue(row['cultural_background'])
                    self.assertEqual('gpt_fallback', row['generation_method'])
                    self.assertEqual([], row['sources'])
                    self.assertEqual(row, store.row)
        self.assertEqual(len(dishes), client.chat.completions.create.call_count)

    def test_partial_fallback_retries_only_missing_section(self):
        client = client_with()
        client.chat.completions.create.side_effect = [
            client_with({'historical_background': 'Origins are uncertain.', 'cultural_background': ''}).chat.completions.create.return_value,
            client_with({'historical_background': 'Must not replace saved text.', 'cultural_background': 'Cultural associations are uncertain.'}).chat.completions.create.return_value,
        ]
        with patch.object(backgrounds, 'retrieve_evidence', return_value=[]):
            row = backgrounds.enrich_background(RECIPE, MemoryStore(), client)
        self.assertEqual('Origins are uncertain.', row['historical_background'])
        self.assertTrue(row['cultural_background'])
        payload = json.loads(client.chat.completions.create.call_args.kwargs['messages'][1]['content'])
        self.assertEqual(['cultural_background'], payload['requested_fields'])

    def test_temporary_partial_failure_retries_after_one_hour(self):
        cached = record(cultural_background='', enrichment_version=backgrounds.BACKGROUND_RETRIEVAL_VERSION,
                        generation_fields={'historical_background': 'gpt_fallback'},
                        unavailable_reason='Temporary general-knowledge drafting failure; retry later.',
                        updated_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
        self.assertFalse(background_cache_valid(cached))
        with patch.object(backgrounds, 'retrieve_evidence', return_value=[]):
            row = backgrounds.enrich_background(RECIPE, MemoryStore(cached), client_with({
                'historical_background': '', 'cultural_background': 'Cultural associations are uncertain.'}))
        self.assertEqual(cached['historical_background'], row['historical_background'])
        self.assertTrue(row['cultural_background'])

    def test_enrichment_components_do_not_block_each_other(self):
        for failing in ('enrich_health', 'enrich_background'):
            with self.subTest(failing=failing), \
                    patch('dish_health.enrich_health') as health, \
                    patch('dish_backgrounds.enrich_background') as background:
                (health if failing == 'enrich_health' else background).side_effect = RuntimeError('private')
                key = ('offline-test',)
                information._jobs.add(key)
                information._enrich_job(RECIPE, MemoryStore(), key)
                health.assert_called_once()
                background.assert_called_once()
                self.assertNotIn(key, information._jobs)


if __name__ == '__main__':
    unittest.main()
