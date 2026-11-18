"""Read-only live checks for CuiSync's History & Culture display.

Run with the same interpreter as Flask. No enrichment, GPT calls, reviews or
database writes are performed. Checks database IDs 844, 511 and 719, then
renders the real compare route and overview API with the saved descriptions.
"""
import os
from html import unescape
from unittest.mock import patch


def main():
    os.environ['DISH_INFO_AUTO_ENRICH'] = '0'
    os.environ.setdefault('SECRET_KEY', 'read-only-background-display-diagnostic')
    # No local OCR model loading or remote model warm-up is needed for this check.
    os.environ.setdefault('OCR_API_URL', 'http://127.0.0.1:1')
    import accounts
    import ner
    import ocr
    import dish_information
    from dish_info_common import normalize, country_key
    from dish_information_store import get_store

    store = get_store()
    if store is None:
        raise SystemExit('Supabase client unavailable. See the interpreter-specific log message.')
    snapshots = {}
    for recipe_id in (844, 511, 719):
        recipe = store.get('recipes', {'recipe_id': recipe_id})
        if recipe is None:
            raise AssertionError(f'Recipe {recipe_id} missing')
        identity = {'normalized_name': normalize(recipe['title']), 'country': country_key(recipe['country'])}
        row = store.get('dish_backgrounds', identity)
        snapshots[recipe_id] = (identity, row)
        public = dish_information.public_information(recipe, schedule=False, store=store)['background']
        print(f"recipe_id={recipe_id} | {recipe['title']} | background_id={(row or {}).get('id')} | "
              f"display={public['status']} | sources={len(public['sources'])} | "
              f"sections={public.get('section_status', {})}")
        if row and (row.get('historical_background') or row.get('cultural_background')):
            assert public['has_stored_description']
            assert public['historical_background'] == (row.get('historical_background') or '')
            assert public['cultural_background'] == (row.get('cultural_background') or '')

    # Avoid account creation at app startup; the diagnostic never acts as a user.
    with patch.object(accounts, 'ensure_admin', return_value=False), \
         patch.object(ner, 'init'), patch.object(ocr, 'warm_up'):
        import app as web
    source_idx = next(i for i, r in enumerate(web.RECIPES) if str(r.get('recipe_id')) == '844')
    target_idx = next(i for i, r in enumerate(web.RECIPES) if str(r.get('recipe_id')) == '719')
    print(f'compare positions: Chicken Adobo={source_idx}; Teriyaki Chicken Stir-Fry={target_idx}')
    with patch.object(web, 'record_recent'), patch.object(web, 'save_history'), \
         patch.object(web, 'get_explanation', return_value=('Diagnostic similarity summary.', 'template')), \
         patch.object(dish_information, '_schedule'), patch.object(dish_information, 'get_store', return_value=store):
        client = web.app.test_client()
        response = client.get('/compare', query_string={
            'dish_name': web.RECIPES[source_idx]['title'], 'origin_country': 'PHILIPPINES',
            'target_country': 'JAPAN', 'recipe_id': 844, 'pick': target_idx})
        assert response.status_code == 200, response.status_code
        html = unescape(response.get_data(as_text=True))
        for recipe_id in (844, 719):
            row = snapshots[recipe_id][1]
            if row:
                assert (row.get('historical_background') or '') in html
                assert (row.get('cultural_background') or '') in html
        assert 'Stored description' in html and 'Unverified' in html
        for idx in (source_idx, target_idx):
            response = client.get(f'/api/dish-info/{idx}')
            assert response.status_code == 200
            assert response.json['background']['has_stored_description']
        home = client.get('/').get_data(as_text=True)
        assert 'css/similarity-warning.css' not in home
        assert client.get('/static/css/index.css').status_code == 200
    for identity, before in snapshots.values():
        assert store.get('dish_backgrounds', identity) == before, 'A stored background changed during the check'
    print('PASS: real /compare, overview API, stylesheet, and unchanged database records.')


if __name__ == '__main__':
    main()
