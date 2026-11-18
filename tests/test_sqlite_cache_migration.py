"""Migration checks using temporary SQLite files and an in-memory REST stub."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

import requests


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import migrate_sqlite_cache as migration


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "explanations_cache.db"
        with contextlib.closing(sqlite3.connect(self.source)) as connection, connection:
            connection.execute(
                "CREATE TABLE explanations (cache_key TEXT PRIMARY KEY, input_dish TEXT, "
                "matched_dish TEXT, explanation TEXT NOT NULL, created_at TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO explanations VALUES (?, ?, ?, ?, ?)",
                ("original-key", "Dish A", "Dish B", "Original explanation.", "2026-11-17 01:02:03"),
            )
        self.original_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.remote = {}
        self.config = mock.patch.object(migration, "dotenv_values", return_value={
            "SUPABASE_URL": "https://migration-test.invalid",
            "SUPABASE_SERVICE_ROLE_KEY": "sb_secret_test_only",
        })
        self.config.start()
        self.addCleanup(self.config.stop)
        self.http = mock.patch.object(requests, "request", side_effect=self.rest)
        self.request = self.http.start()
        self.addCleanup(self.http.stop)

    def rest(self, method, url, **kwargs):
        self.assertEqual(url, "https://migration-test.invalid/rest/v1/explanations")
        params = kwargs.get("params", {})
        if method == "POST":
            self.assertEqual(params["on_conflict"], "cache_key")
            self.assertIn("resolution=ignore-duplicates", kwargs["headers"]["Prefer"])
            for row in kwargs["json"]:
                self.remote.setdefault(row["cache_key"], copy.deepcopy(row))
            return self.response(201, None)
        self.assertEqual(method, "GET")
        if params.get("limit") == "0":
            return self.response(200, [])
        key = params["cache_key"].removeprefix("eq.")
        return self.response(200, [self.remote[key]] if key in self.remote else [])

    @staticmethod
    def response(status, body):
        result = requests.Response()
        result.status_code = status
        result.url = "https://migration-test.invalid/rest/v1/explanations"
        result._content = json.dumps(body).encode("utf-8")
        return result

    def run_migration(self):
        with contextlib.redirect_stdout(io.StringIO()):
            migration.migrate(self.root, apply=True)

    def source_rows(self, path):
        return migration.read_rows(path, "explanations", migration.TABLES[0][2])

    def test_migration_preserves_source_backup_and_all_field_values(self):
        self.run_migration()
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.original_hash)
        original = self.source_rows(self.source)
        self.assertEqual(list(self.remote.values()), original)
        backups = list((self.root / "admin_data/sqlite_migration_backups").glob("*/explanations_cache.db"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.source_rows(backups[0]), original)
        self.assertEqual(original[0]["created_at"], "2026-11-17T01:02:03+00:00")

    def test_repeated_migration_does_not_duplicate_records(self):
        self.run_migration()
        self.run_migration()
        self.assertEqual(len(self.remote), 1)
        self.assertEqual(list(self.remote.values()), self.source_rows(self.source))
        backups = list((self.root / "admin_data/sqlite_migration_backups").glob("*/explanations_cache.db"))
        self.assertEqual(len(backups), 2)

    def test_conflicting_remote_record_is_preserved_and_reported(self):
        existing = self.source_rows(self.source)[0]
        existing["explanation"] = "Existing remote explanation."
        self.remote["original-key"] = copy.deepcopy(existing)
        with self.assertRaisesRegex(migration.MigrationError, "conflicting existing record"):
            self.run_migration()
        self.assertEqual(self.remote["original-key"], existing)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.original_hash)
        backups = list((self.root / "admin_data/sqlite_migration_backups").glob("*/explanations_cache.db"))
        self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main()
