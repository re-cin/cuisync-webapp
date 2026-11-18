"""Upgrade stored CuiSync dish backgrounds to UNESCO/Crossref/Books evidence.

Default is a no-write preview. Apply to a small pilot before all 869 dishes:
  python upgrade_background_sources.py --limit 5
  python upgrade_background_sources.py --limit 5 --apply
  python upgrade_background_sources.py --all --apply
No schema migration, no deletions, no update without a supported new draft.
"""
import argparse
import os
from collections import Counter
import time
from dish_info_common import country_key, normalize
from dish_information_store import get_store, DishStoreError


def run(store, recipes, client=None, apply=False, limit=5, recipe_id=None, offset=0, delay=1.0, improve_existing=False, diagnose=False):
    from dish_backgrounds import enrich_background
    rows, seen = [], set()
    for recipe in recipes:
        if recipe_id is not None and str(recipe.get('recipe_id')) != str(recipe_id):
            continue
        identity = (normalize(recipe.get('title')), country_key(recipe.get('country')))
        if not all(identity) or identity in seen:
            continue
        seen.add(identity)
        rows.append(recipe)
        if limit is not None and len(rows) >= offset + limit:
            break
    rows = rows[offset:offset + limit] if limit is not None else rows[offset:]
    counts = Counter()
    for i, recipe in enumerate(rows, offset + 1):
        identity = {'normalized_name': normalize(recipe.get('title')),
                    'country': country_key(recipe.get('country'))}
        original = store.get('dish_backgrounds', identity)
        if not original:
            counts['missing_records_preserved'] += 1
            continue
        if original.get('verification_status') == 'verified':
            counts['approved_preserved'] += 1
            continue
        if not apply:
            counts['preview_only'] += 1
            print(f'{i}/{len(rows)} PREVIEW: {recipe.get("title")} | {recipe.get("country")}')
            continue
        details = {} if diagnose else None
        options = {'diagnostics': details} if diagnose else {}
        updated = enrich_background(recipe, store, client=client, upgrade_sources=True, improve_existing=improve_existing, **options)
        if diagnose and details is not None:
            print('  EVIDENCE:', details.get('evidence_count', 'not retrieved'),
                  'providers=', details.get('evidence_providers', []))
            print('  DETAILS:', details.get('reason', 'No further details'))
            for field, reason in details.get('rejected_fields', {}).items():
                print(f'  REJECTED {field}: {reason}; draft_words={details.get("draft_word_counts", {}).get(field, 0)}')
            for title in details.get('evidence_titles', []):
                print('  SOURCE:', title)
        if delay:
            time.sleep(delay)
        if updated.get('updated_at') != original.get('updated_at'):
            counts['updated'] += 1
            generation_fields = updated.get("generation_fields") or {}
            history = generation_fields.get("historical_background", "unspecified")
            culture = generation_fields.get("cultural_background", "unspecified")
            print(f'{i}/{len(rows)} UPDATED: {recipe.get("title")} '
                  f'(overall={updated.get("generation_method")}; history={history}; culture={culture})')
        else:
            counts['unchanged_no_supported_draft'] += 1
            print(f'{i}/{len(rows)} KEPT: {recipe.get("title")} (no better supported text)')
    print('Summary:', dict(counts))
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--limit', type=int, default=5, help='Unique dish records to examine (default: 5)')
    group.add_argument('--all', action='store_true', help='Examine all unique dishes')
    parser.add_argument('--recipe-id', type=int, help='Select one specific recipe')
    parser.add_argument('--offset', type=int, default=0, help='Skip this many unique dishes to resume later')
    parser.add_argument('--delay', type=float, default=1.0, help='Seconds between live dish lookups (default: 1)')
    parser.add_argument('--apply', action='store_true', help='Call providers/GPT and save only improved drafts')
    parser.add_argument('--improve-existing', action='store_true', help='Also attempt to expand already academically sourced text (saves only if meaningfully richer)')
    parser.add_argument('--diagnose', action='store_true', help='Print source counts and field-level reasons why an upgrade was rejected')
    args = parser.parse_args()
    if (args.limit is not None and args.limit < 1) or args.offset < 0 or args.delay < 0:
        parser.error('--limit must be positive; --offset and --delay cannot be negative')
    store = get_store()
    if store is None:
        parser.error('Supabase .env settings are missing or incomplete.')
    client = None
    if args.apply:
        if not os.environ.get('OPENAI_API_KEY'):
            parser.error('OPENAI_API_KEY is required to draft source-grounded explanations.')
        from openai import OpenAI
        client = OpenAI(timeout=25, max_retries=0)
    return run(store, store.list('recipes'), client=client, apply=args.apply,
               limit=None if args.all else args.limit, recipe_id=args.recipe_id,
               offset=args.offset, delay=args.delay, improve_existing=args.improve_existing, diagnose=args.diagnose)


if __name__ == '__main__':
    try:
        main()
    except DishStoreError:
        raise SystemExit('Supabase is temporarily unavailable. No older records were deleted.') from None
