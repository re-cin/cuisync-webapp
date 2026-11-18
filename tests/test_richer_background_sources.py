"""Tests for deeper source excerpts, diversity, conservative upgrades and CLI behavior."""
import unittest
from pathlib import Path
import sys
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import academic_background_sources as a
import dish_backgrounds as b
from background_generation import _is_improvement, _dates_grounded, generate
from test_academic_background_sources import MemoryStore, row, chat, RECIPE


def s(kind, url, excerpt):
    return {'source_type': kind, 'url': url, 'excerpt': excerpt,
            'matched_aliases': ['Kimchi'], 'matched_country': 'SOUTH KOREA'}


class RicherBackgroundTests(unittest.TestCase):
    def test_richer_excerpt_is_ranked_higher_within_same_provider(self):
        a1 = s('crossref', 'https://doi.org/a', 'Kimchi is a Korean food.')
        a2 = s('crossref', 'https://doi.org/b', 'Korean kimchi has documented historical developments and cultural traditions. ' * 10)
        picked = a.choose_diverse_evidence([a1, a2])
        self.assertEqual(picked[0]['url'], a2['url'])

    def test_preserves_diversity_when_one_provider_dominates(self):
        abstracts = [s('crossref', f'https://doi.org/{n}', ('Historical origins and cultural tradition of kimchi in Korea. ' * 12)) for n in range(8)]
        book = s('google_books', 'https://books.google.com/books?id=abc123', 'A publisher description of Korean kimchi cultural customs.')
        selection = a.choose_diverse_evidence(abstracts + [book], limit=4)
        self.assertIn('google_books', {x['source_type'] for x in selection})
        self.assertEqual(len(selection), 4)

    def test_deduplicates_sources_by_url(self):
        item = s('crossref', 'https://doi.org/abc', 'A Korean kimchi history.')
        self.assertEqual(len(a.choose_diverse_evidence([item, dict(item)])), 1)

    def test_three_recipe_aliases_are_queried(self):
        recipe = dict(RECIPE)
        called = []
        with patch.object(a, '_json', side_effect=lambda ep, params: called.append(params) or {'items': []}):
            result = a._google_books(recipe, ['a kimchi recipe', 'kimchi', 'Korean kimchi'], 'SOUTH KOREA')
        self.assertEqual(result, [])
        self.assertEqual(len(called), 3)

    def test_crossref_queries_publication_metadata_not_just_title(self):
        called = []
        with patch.object(a, '_json', side_effect=lambda ep, params: called.append(params) or {'message': {'items': []}}):
            a._crossref(RECIPE, ['Kimchi'], 'SOUTH KOREA')
        self.assertIn('query.bibliographic', called[0])

    def test_unsubstantiated_year_is_rejected(self):
        source = s('crossref', 'https://doi.org/a', 'This study examines cultural kimchi in Korea.')
        self.assertFalse(_dates_grounded('Kimchi originated in 1642.', [source]))
        self.assertTrue(_dates_grounded('Kimchi has an evolving cultural history.', [source]))

    def test_cited_year_is_accepted_as_a_token_not_as_verified_claim(self):
        source = s('crossref', 'https://doi.org/a', 'Study describes kimchi in 1900 within Korea.')
        self.assertTrue(_dates_grounded('Kimchi was discussed in 1900.', [source]))

    def test_long_existing_explanation_is_not_shortened_to_gain_citation(self):
        self.assertFalse(_is_improvement('Korean culinary history and communal practice. ' * 10,
                                         'Korean food is traditional.'))

    def test_short_wikipedia_paraphrase_requires_more_information(self):
        old = 'Kimchi is a Korean dish traditionally served with meals and prepared with fermented vegetables.'
        self.assertFalse(_is_improvement(old, 'Kimchi is traditionally eaten with meals in Korea.', old_wikipedia_only=True))
        self.assertTrue(_is_improvement(old, 'Kimchi is a Korean dish with a documented culinary history. Community preparation and shared consumption form an important part of its cultural identity and traditions.', old_wikipedia_only=True))

    def test_existing_research_based_text_only_overwritten_when_richer(self):
        self.assertFalse(_is_improvement('An adequately detailed source-based explanation about Korean kimchi and its cultural context.',
                                         'A different Korean kimchi description with no added details.',
                                         previously_sourced=True, improving_sourced=True))

    def test_upgrade_option_revisits_trusted_sourced_fields(self):
        old = row(generation_method='source_based', generation_fields={
            'historical_background': 'source_based', 'cultural_background': 'source_based'},
            sources=[s('crossref', 'https://doi.org/10.1234/old',
                       'Kimchi has an established Korean cultural background and history.')])
        store = MemoryStore(old)
        new = s('crossref', 'https://doi.org/10.1234/new',
                'This research abstract documents historical changes and cultural traditions involving kimchi in Korea.')
        client = chat(history='This carefully documented account expands Korean kimchi history and cultural development through several generations while preserving the evidence-based historical context of the dish.',
                      culture='Korean kimchi continues to hold importance in shared culinary traditions and communal preparation and plays a role in contemporary cultural identity and social food practices.')
        with patch.object(b, 'retrieve_evidence', return_value=[new]):
            skipped = generate(RECIPE, store, client, upgrade_sources=True)
        self.assertEqual(skipped['historical_background'], old['historical_background'])
        self.assertEqual(store.writes, [])
        # explicit option is required to revisit already trusted source text
        store = MemoryStore(old)
        with patch.object(b, 'retrieve_evidence', return_value=[new]):
            updated = generate(RECIPE, store, client, upgrade_sources=True, improve_existing=True)
        self.assertGreater(len(store.writes), 0)
        self.assertIn('carefully documented', updated['historical_background'])

    def test_missing_evidence_preserves_reviewed_record(self):
        old = row(status='verified')
        store = MemoryStore(old)
        with patch.object(b, 'retrieve_evidence', side_effect=AssertionError('no API')):
            self.assertEqual(generate(RECIPE, store, chat(), upgrade_sources=True, improve_existing=True), old)
        self.assertEqual(store.writes, [])

if __name__ == '__main__':
    unittest.main()
