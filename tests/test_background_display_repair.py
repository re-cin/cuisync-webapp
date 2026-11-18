"""Read-only presentation regressions: identity, saved reviews, and SDK retries."""
import copy
import os
import unittest
from unittest.mock import patch

import dish_information as information
import dish_information_store as stores
from test_dish_information import PublicStore, approved_background
from test_dish_information_templates import DishInformationTemplateTests


class BackgroundDisplayRepairTests(unittest.TestCase):
    recipe = {'recipe_id': 844, 'title': 'chicken Adobo', 'country': 'PHILIPPINES',
              'alternative_title': 'Adobong Manok'}

    def stored(self, **changes):
        return approved_background(dish_name='chicken Adobo', normalized_name='chicken adobo',
                                   country='PHILIPPINES', **changes)

    def test_saved_source_review_is_honored_without_database_changes(self):
        row = self.stored(verification_status='pending', generation_method='source_based',
                          generation_fields={'historical_background': 'source_based',
                                             'cultural_background': 'source_based',
                                             '_source_review': {'run_id': 'PRIVATE_REVIEW', 'section_status': {
                                                 'historical_background': 'unverified', 'cultural_background': 'supported'}}})
        before = copy.deepcopy(row)
        public = information.public_information(self.recipe, schedule=False, store=PublicStore([row]))['background']
        self.assertEqual('unverified', public['status'])
        self.assertEqual('unverified', public['section_status']['historical_background'])
        self.assertEqual('supported', public['section_status']['cultural_background'])
        self.assertEqual(row['historical_background'], public['historical_background'])
        self.assertNotIn('PRIVATE_REVIEW', str(public))
        self.assertEqual(before, row)

    def test_unsourced_statuses_preserve_text_as_unverified(self):
        for status in ('pending', 'unverified', 'gpt_fallback', 'verified', 'unavailable'):
            row = self.stored(verification_status=status, sources=[], generation_method='gpt_fallback')
            with self.subTest(status=status):
                public = information.public_information(self.recipe, schedule=False, store=PublicStore([row]))['background']
                self.assertEqual('unverified', public['status'])
                self.assertEqual(row['historical_background'], public['historical_background'])
                self.assertEqual('generated_unverified', public['verification'])

    def test_completed_unverified_descriptions_are_not_regenerated(self):
        from background_generation import generate
        from test_academic_background_sources import MemoryStore
        row = self.stored(verification_status='unavailable', sources=[])
        store = MemoryStore(row)
        with patch('dish_backgrounds.retrieve_evidence', side_effect=AssertionError('Must preserve stored text')):
            self.assertEqual(row, generate(self.recipe, store, refresh=True))
        self.assertEqual([], store.writes)

    def test_recipe_ids_share_background_by_name_and_country(self):
        store = PublicStore([self.stored()])
        first = information.public_information(self.recipe, schedule=False, store=store)
        other = information.public_information(dict(self.recipe, recipe_id=12345), schedule=False, store=store)
        self.assertEqual(first['background'], other['background'])
        for table, filters in store.lookups:
            if table == 'dish_backgrounds':
                self.assertEqual({'normalized_name': 'chicken adobo', 'country': 'PHILIPPINES'}, filters)

    def test_same_name_in_another_country_does_not_share_background(self):
        public = information.public_information(dict(self.recipe, country='JAPAN'), schedule=False,
                                                store=PublicStore([self.stored()]))['background']
        self.assertEqual('unavailable', public['status'])

    def test_nutrition_failure_does_not_hide_background(self):
        row = self.stored()
        class Store(PublicStore):
            def get(self, table, filters):
                if table == 'dish_health_info':
                    raise stores.DishStoreError('Unavailable')
                return super().get(table, filters)
        public = information.public_information(self.recipe, schedule=False, store=Store([row]))
        self.assertEqual(row['historical_background'], public['background']['historical_background'])
        self.assertEqual('unavailable', public['health']['status'])

    def test_missing_record_is_distinct_from_stored_unverified_text(self):
        public = information.public_information(self.recipe, schedule=False, store=PublicStore())['background']
        self.assertEqual('unavailable', public['status'])
        self.assertEqual('', public['combined_background'])

    def test_arbitrary_url_does_not_verify_a_description(self):
        public = information.public_information(self.recipe, schedule=False,
                                                store=PublicStore([self.stored()]))['background']
        self.assertNotEqual('verified', public['status'])

    def test_reviewed_dish_specific_evidence_displays_normally(self):
        source = {'title': 'Dish evidence', 'url': 'https://doi.org/10.1000/adobo', 'source_type': 'crossref',
                  'matched_country': 'PHILIPPINES',
                  'excerpt': 'Chicken adobo is a Filipino dish in the Philippines with documented history and cultural practices.'}
        row = self.stored(sources=[source])
        public = information.public_information(self.recipe, schedule=False, store=PublicStore([row]))['background']
        self.assertEqual('verified', public['status'])
        self.assertEqual([{'title': source['title'], 'url': source['url']}], public['sources'])

    def test_wrong_dish_source_cannot_verify_a_description(self):
        source = {'title': 'Other dish', 'url': 'https://doi.org/10.1000/example', 'source_type': 'crossref',
                  'matched_country': 'PHILIPPINES', 'excerpt': 'Sinigang is a Filipino dish in the Philippines.'}
        public = information.public_information(self.recipe, schedule=False,
                                                store=PublicStore([self.stored(sources=[source])]))['background']
        self.assertNotEqual('verified', public['status'])

    def test_explicit_negative_section_review_overrides_verified_record(self):
        source = {'title': 'Dish evidence', 'url': 'https://doi.org/10.1000/adobo', 'source_type': 'crossref',
                  'matched_country': 'PHILIPPINES',
                  'excerpt': 'Chicken adobo is a dish in the Philippines with historical and cultural context.'}
        row = self.stored(sources=[source], generation_fields={'_source_review': {'section_status': {
            'historical_background': 'unverified', 'cultural_background': 'supported'}}})
        public = information.public_information(self.recipe, schedule=False, store=PublicStore([row]))['background']
        self.assertEqual('unverified', public['status'])
        self.assertEqual('unverified', public['section_status']['historical_background'])

    def test_connection_initialization_failure_is_not_cached(self):
        stores.get_store.cache_clear()
        with patch.dict(os.environ, SUPABASE_URL='https://example.supabase.co', SUPABASE_SERVICE_ROLE_KEY='test'), \
             patch('supabase.create_client', side_effect=[RuntimeError('PRIVATE_KEY'), object()]) as create:
            self.assertIsNone(stores.get_store())
            self.assertIsNotNone(stores.get_store())
            self.assertEqual(2, create.call_count)
        stores.get_store.cache_clear()


class UnverifiedTemplateTests(DishInformationTemplateTests):
    def test_unverified_text_and_saved_sources_are_visible_with_clear_labels(self):
        bg = self.context['dish_information']['source']['background']
        bg.update(status='unverified', evidence_message='History: unverified; Culture: supported.')
        html = self.render()
        self.assertIn('Stored description · Unverified', html)
        self.assertIn('History: unverified; Culture: supported.', html)
        self.assertIn('Reviewed history for source.', html)
        self.assertIn('https://example.org/source/background', html)

    def test_missing_stylesheet_reference_removed(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        self.assertNotIn('css/similarity-warning.css', (root / 'templates/index.html').read_text(encoding='utf-8'))
        self.assertIn('.sim-warning-overlay', (root / 'static/css/index.css').read_text(encoding='utf-8'))
