"""Back up and copy the app's two SQLite caches to Supabase without overwrites.

Usage: python migrate_sqlite_cache.py --apply
Run supabase_cache.sql first. Stop the app while copying so no new local writes
arrive after the snapshot. Without --apply this only inventories local rows.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import requests
from dotenv import dotenv_values

TABLES = (
    ("explanations_cache.db", "explanations",
     ("cache_key", "input_dish", "matched_dish", "explanation", "created_at")),
    ("admin_data/cuisync.db", "cultural_info", ("cache_key", "dish", "info", "created_at")),
)


class MigrationError(RuntimeError):
    pass


def timestamp(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)  # SQLite CURRENT_TIMESTAMP is UTC.
    return parsed.astimezone(timezone.utc).isoformat()


def read_rows(path, table, columns):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise MigrationError(f"SQLite integrity check failed: {path.name}")
        db.row_factory = sqlite3.Row
        # Only fixed table/column names from TABLES are interpolated here.
        rows = [dict(row) for row in db.execute(f"SELECT {','.join(columns)} FROM {table}")]
    for row in rows:
        if not row["cache_key"]:
            raise MigrationError(f"Empty cache key in {table}")
        if row.get("created_at"):
            row["created_at"] = timestamp(row["created_at"])
        else:
            row.pop("created_at", None)
    return rows


def migrate(root, apply=False):
    sources = [(relative, table, columns) for relative, table, columns in TABLES
               if (root / relative).is_file()]
    if not sources:
        raise MigrationError("No SQLite cache files found.")
    for relative, table, columns in sources:
        print(f"{relative}: {len(read_rows(root / relative, table, columns))} {table} rows")
    if not apply:
        print("Inventory only. Use --apply to back up and transfer these records.")
        return

    config = dotenv_values(root / ".env")
    url = (config.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (config.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise MigrationError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required in .env.")
    headers = {"apikey": key}
    if key.startswith("eyJ"):
        headers["Authorization"] = "Bearer " + key

    def request(method, table, **kwargs):
        try:
            response = requests.request(method, url + "/rest/v1/" + table,
                                        headers=dict(headers, **kwargs.pop("headers", {})),
                                        timeout=20, **kwargs)
        except requests.RequestException:
            raise MigrationError(f"{table}: connection failed; originals and backups are intact.") from None
        if not response.ok:
            raise MigrationError(f"{table}: HTTP {response.status_code}; check supabase_cache.sql and credentials.")
        return response

    # Validate all destinations before copying any data.
    for _, table, columns in sources:
        request("GET", table, params={"select": ",".join(columns), "limit": "0"})

    backup_dir = root / "admin_data" / "sqlite_migration_backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir.mkdir(parents=True, exist_ok=False)
    for relative, _, _ in sources:
        backup_path = backup_dir / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect((root / relative).resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(backup_path)) as target:
                source.backup(target)
    print(f"SQLite backups saved in {backup_dir}")

    for relative, table, columns in sources:
        rows = read_rows(backup_dir / relative, table, columns)
        for offset in range(0, len(rows), 100):
            request("POST", table, params={"on_conflict": "cache_key"}, json=rows[offset:offset + 100],
                    headers={"Prefer": "resolution=ignore-duplicates,return=minimal"})
        for row in rows:
            response = request("GET", table, params={
                "select": ",".join(columns), "cache_key": "eq." + row["cache_key"], "limit": "1"})
            results = response.json()
            if len(results) != 1:
                raise MigrationError(f"{table}: a transferred row was not found.")
            actual = results[0]
            actual["created_at"] = timestamp(actual.get("created_at"))
            if any(actual.get(column) != value for column, value in row.items()):
                raise MigrationError(f"{table}: verification found a conflicting existing record; it was not overwritten.")
        print(f"{table}: verified all {len(rows)} records, including contents and timestamps.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        migrate(args.root.resolve(), apply=args.apply)
    except MigrationError as error:
        parser.exit(1, f"Migration stopped: {error}\n")
