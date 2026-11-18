"""Preview or import reviewer-normalized official Asian food-composition CSV.

First obtain data under its publisher's permitted terms; this script does NOT
scrape, download, infer translations or guess nutrition amounts. Review every
source record and precise food identity. --apply inserts as unapproved;
--apply --approve enables the entries for use in CuiSync immediately.
Existing rows are never overwritten by this importer.
"""
import argparse
import csv
from pathlib import Path
from regional_nutrition import reviewed_import_row


def read_rows(path, approve=False):
    with open(path, encoding='utf-8-sig', newline='') as file:
        rows = []
        for i, raw in enumerate(csv.DictReader(file), start=2):
            try:
                rows.append(reviewed_import_row(raw, approved=approve))
            except ValueError as ex:
                raise ValueError(f'CSV line {i}: {ex}') from None
    keys = [(x['country'], x['normalized_name']) for x in rows]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate country/ingredient combination; resolve the food identity manually')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path, help='Reviewed, normalized per-100g food composition CSV')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--approve', action='store_true', help='Explicitly approve every reviewed row (requires --apply)')
    args = parser.parse_args()
    if args.approve and not args.apply:
        parser.error('--approve requires --apply')
    rows = read_rows(args.csv, approve=args.approve)
    print(f'Validated {len(rows)} regional food records; approved={args.approve}')
    if not args.apply:
        print('PREVIEW ONLY: No Supabase changes. Use --apply after reviewing your source CSV.')
        return
    from dish_information_store import get_store
    store = get_store()
    if not store:
        raise RuntimeError('Supabase is not configured. No data uploaded.')
    # Do not overwrite edited/withdrawn approved food records.
    inserted = 0
    for row in rows:
        try:
            result = (store.client.table('regional_ingredient_nutrition')
                      .upsert(row, on_conflict='country,normalized_name', ignore_duplicates=True)
                      .execute())
        except Exception:
            raise RuntimeError('Regional nutrition import failed; check migration and server credentials.') from None
        inserted += len(result.data or [])
    print(f'Inserted {inserted} new records; existing records were preserved.')

if __name__ == '__main__':
    main()
