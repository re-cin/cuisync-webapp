"""No-network regression coverage for partially source-grounded backgrounds."""
import unittest
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dish_backgrounds as backgrounds
from background_provenance import method_for_fields, field_method
from background_generation import generate
from test_academic_background_sources import MemoryStore, RECIPE, row, chat
from test_dish_information import PublicStore
from dish_information import public_information


SOURCE = {
    "source_type": "crossref", "title": "Cultural evidence", "url": "https://doi.org/10.1000/example",
    "excerpt": "Kimchi is both a Korean food and part of a shared cultural practice.",
    "matched_country": "SOUTH KOREA", "matched_aliases": ["Kimchi"]
}


class MixedStatusTests(unittest.TestCase):
    def test_three_statuses_are_exclusive(self):
        self.assertEqual(method_for_fields({'historical_background': 'source_based', 'cultural_background': 'source_based'}), 'source_based')
        self.assertEqual(method_for_fields({'historical_background': 'gpt_fallback', 'cultural_background': 'gpt_fallback'}), 'gpt_fallback')
        self.assertEqual(method_for_fields({'historical_background': 'gpt_fallback', 'cultural_background': 'source_based'}), 'mixed')
        self.assertEqual(method_for_fields({'historical_background': 'source_based', 'cultural_background': 'gpt_fallback'}), 'mixed')

    def test_partial_or_legacy_provenance_is_not_invented(self):
        self.assertEqual(method_for_fields({}, 'gpt_fallback'), 'gpt_fallback')
        self.assertEqual(method_for_fields({'historical_background': 'source_based'}, 'mixed'), 'source_based')
        self.assertIsNone(field_method({}, 'historical_background', 'mixed'))

    def test_cultural_evidence_upgrades_only_culture(self):
        store = MemoryStore(row())
        with patch.object(backgrounds, 'retrieve_evidence', return_value=[SOURCE]):
            saved = generate(RECIPE, store, chat(history='', culture='Supported communal practice.'), upgrade_sources=True)
        self.assertEqual(saved['historical_background'], 'Legacy account is uncertain.')
        self.assertEqual(saved['cultural_background'], 'Supported communal practice.')
        self.assertEqual(saved['generation_fields'], {'historical_background':'gpt_fallback', 'cultural_background':'source_based'})
        self.assertEqual(saved['generation_method'], 'mixed')
        self.assertEqual(len(store.writes), 1)

    def test_historical_evidence_upgrades_only_history(self):
        store = MemoryStore(row())
        with patch.object(backgrounds, 'retrieve_evidence', return_value=[SOURCE]):
            saved = generate(RECIPE, store, chat(history='Supported historical context.', culture=''), upgrade_sources=True)
        self.assertEqual(saved['historical_background'], 'Supported historical context.')
        self.assertEqual(saved['cultural_background'], 'Traditions vary by household.')
        self.assertEqual(saved['generation_method'], 'mixed')
        self.assertEqual(saved['generation_fields']['historical_background'], 'source_based')
        self.assertEqual(saved['generation_fields']['cultural_background'], 'gpt_fallback')

    def test_same_relevant_excerpt_can_support_both_fields(self):
        store = MemoryStore(row())
        with patch.object(backgrounds, 'retrieve_evidence', return_value=[SOURCE]):
            saved = generate(RECIPE, store, chat(history='Supported historical context.', culture='Supported cultural practices.'), upgrade_sources=True)
        self.assertEqual(saved['generation_method'], 'source_based')
        self.assertEqual(saved['generation_fields']['historical_background'], 'source_based')
        self.assertEqual(saved['generation_fields']['cultural_background'], 'source_based')
        self.assertEqual(len(saved['sources']), 1)

    def test_no_relevant_evidence_keeps_existing_without_writing(self):
        existing = row()
        store = MemoryStore(existing)
        with patch.object(backgrounds, 'retrieve_evidence', return_value=[]):
            saved = generate(RECIPE, store, chat(), upgrade_sources=True)
        self.assertEqual(saved, existing)
        self.assertEqual(store.writes, [])

    def test_mixed_source_is_combined_for_public_display(self):
        cache = row(generation_method='mixed', generation_fields={
            'historical_background': 'gpt_fallback', 'cultural_background': 'source_based'},
            sources=[SOURCE], verification_status='pending')
        result = public_information(RECIPE, schedule=False, store=PublicStore([cache]))
        self.assertEqual(result['background']['generation_method'], 'mixed')
        self.assertIn('Legacy account is uncertain.', result['background']['combined_background'])
        self.assertIn('Traditions vary by household.', result['background']['combined_background'])
        self.assertEqual(result['background']['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
