"""Cache persistence checks; all HTTP and GPT calls are mocked."""
import contextlib
import importlib
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import requests


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(ROOT.parent))

TEST_ENV = {
    "SUPABASE_URL": "https://cache-test.invalid",
    "SUPABASE_SERVICE_ROLE_KEY": "sb_secret_test_only",
}

# Import with fake settings and prevent .env reads or SQLite initialization.
with mock.patch.dict(os.environ, TEST_ENV), mock.patch("dotenv.load_dotenv"), \
        mock.patch("sqlite3.connect") as _import_sqlite:
    supabase_cache = importlib.import_module("supabase_cache")
    cuisync_cache = importlib.import_module("cuisync_cache")
    cultural_info = importlib.import_module("cultural_info")
IMPORT_OPENED_SQLITE = _import_sqlite.called


def response(status=200, body=None):
    result = requests.Response()
    result.status_code = status
    result.url = "https://cache-test.invalid/rest/v1/explanations"
    result._content = json.dumps(body).encode("utf-8")
    result.headers["Content-Type"] = "application/json"
    return result


@contextlib.contextmanager
def captured_output():
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    logging.getLogger().addHandler(handler)
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            yield buffer
    finally:
        logging.getLogger().removeHandler(handler)


class SupabaseCacheTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, TEST_ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.http = mock.patch.object(requests, "request")
        self.request = self.http.start()
        self.addCleanup(self.http.stop)

    def test_import_does_not_open_sqlite_with_supabase_configured(self):
        self.assertFalse(IMPORT_OPENED_SQLITE)

    def test_read_returns_stored_text_and_uses_server_secret_only(self):
        self.request.return_value = response(body=[{"explanation": "Cached explanation."}])
        result = supabase_cache.get("explanations", "cache-key", "explanation")
        self.assertEqual(result, "Cached explanation.")
        args, kwargs = self.request.call_args
        self.assertEqual(args[0].upper(), "GET")
        self.assertEqual(kwargs["params"]["cache_key"], "eq.cache-key")
        self.assertEqual(kwargs["headers"]["apikey"], TEST_ENV["SUPABASE_SERVICE_ROLE_KEY"])
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertEqual(kwargs["timeout"], 5)

    def test_empty_result_is_a_cache_miss(self):
        self.request.return_value = response(body=[])
        self.assertIsNone(supabase_cache.get("cultural_info", "missing", "info"))

    def test_legacy_server_key_uses_bearer_authentication(self):
        self.request.return_value = response(body=[])
        with mock.patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": "eyJ.test-only"}):
            supabase_cache.get("explanations", "key", "explanation")
        headers = self.request.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer eyJ.test-only")

    def test_save_uses_upsert_for_existing_cache_keys(self):
        self.request.return_value = response(status=201, body=None)
        row = {"cache_key": "same-key", "explanation": "Updated explanation."}
        self.assertTrue(supabase_cache.save("explanations", row))
        args, kwargs = self.request.call_args
        self.assertEqual(args[0].upper(), "POST")
        self.assertEqual(kwargs["params"]["on_conflict"], "cache_key")
        self.assertIn("resolution=merge-duplicates", kwargs["headers"]["Prefer"])
        self.assertEqual(kwargs["json"]["explanation"], row["explanation"])

    def test_timeout_does_not_leak_details_or_break_cache_calls(self):
        secret = TEST_ENV["SUPABASE_SERVICE_ROLE_KEY"]
        self.request.side_effect = requests.Timeout("private server data " + secret)
        with captured_output() as output:
            self.assertIsNone(supabase_cache.get("explanations", "key", "explanation"))
            self.assertFalse(supabase_cache.save("cultural_info", {"cache_key": "key", "info": "{}"}))
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn("private server data", output.getvalue())

    def test_permission_failure_is_a_miss_and_does_not_expose_response(self):
        self.request.return_value = response(status=403, body={
            "message": "private server data " + TEST_ENV["SUPABASE_SERVICE_ROLE_KEY"],
        })
        with captured_output() as output:
            self.assertIsNone(supabase_cache.get("explanations", "key", "explanation"))
            self.assertFalse(supabase_cache.save("explanations", {
                "cache_key": "key", "explanation": "Generated text.",
            }))
        self.assertNotIn(TEST_ENV["SUPABASE_SERVICE_ROLE_KEY"], output.getvalue())
        self.assertNotIn("private server data", output.getvalue())

    def test_invalid_table_or_column_cannot_be_requested(self):
        with self.assertRaises(ValueError):
            supabase_cache.get("profiles", "key", "email")
        with self.assertRaises(ValueError):
            supabase_cache.get("explanations", "key", "info")
        self.request.assert_not_called()


class ApplicationCacheTests(unittest.TestCase):
    EVIDENCE = {"input_dish": "Dish A", "matched_dish": "Dish B", "shared_ingredients": ["rice"]}
    PAYLOAD = {"dish": "Dish A", "ingredients": ["rice"], "country": "Test country"}
    INFO = {"culture": "Stored culture.", "history": "Stored history.", "health": ["Rice adds energy."]}

    def setUp(self):
        self.env = mock.patch.dict(os.environ, TEST_ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.http = mock.patch.object(requests, "request", side_effect=AssertionError("Unmocked HTTP call"))
        self.http.start()
        self.addCleanup(self.http.stop)

    def test_remote_explanation_hit_skips_generation_and_sqlite(self):
        generate = mock.Mock(side_effect=AssertionError("GPT must not be called for cache hit"))
        with mock.patch.object(supabase_cache, "get", return_value="Cached explanation."), \
                mock.patch.object(sqlite3, "connect", side_effect=AssertionError("SQLite must not be opened")):
            self.assertEqual(cuisync_cache.explain_with_cache(self.EVIDENCE, generate), "Cached explanation.")
        generate.assert_not_called()

    def test_failed_remote_write_still_returns_generated_explanation(self):
        with mock.patch.object(supabase_cache, "get", return_value=None), \
                mock.patch.object(supabase_cache, "save", return_value=False), \
                mock.patch.object(sqlite3, "connect", side_effect=AssertionError("SQLite must not be opened")):
            result = cuisync_cache.explain_with_cache(self.EVIDENCE, lambda evidence: "Fresh explanation.")
        self.assertEqual(result, "Fresh explanation.")

    def test_explanations_still_persist_locally_without_supabase(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": ""}), \
                mock.patch.object(cuisync_cache, "DB_PATH", str(Path(directory) / "explanations.db")):
            cuisync_cache._init_db()
            cuisync_cache.save_cached(self.EVIDENCE, "Local explanation.")
            self.assertEqual(cuisync_cache.get_cached(self.EVIDENCE), "Local explanation.")

    def test_remote_cultural_hit_skips_gpt_and_sqlite(self):
        client = mock.Mock()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(supabase_cache, "get", return_value=json.dumps(self.INFO)), \
                mock.patch.object(sqlite3, "connect", side_effect=AssertionError("SQLite must not be opened")):
            cultural_info.init(directory, lambda: "Test prompt", client, "test-model")
            self.assertEqual(cultural_info._ask_gpt(self.PAYLOAD, "Test prompt"), self.INFO)
        client.chat.completions.create.assert_not_called()

    def test_remote_cultural_write_failure_preserves_generated_notes(self):
        client = mock.Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.INFO))),
        ])
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(supabase_cache, "get", return_value=None), \
                mock.patch.object(supabase_cache, "save", return_value=False), \
                mock.patch.object(sqlite3, "connect", side_effect=AssertionError("SQLite must not be opened")):
            cultural_info.init(directory, lambda: "Test prompt", client, "test-model")
            self.assertEqual(cultural_info._ask_gpt(self.PAYLOAD, "Test prompt"), self.INFO)

    def test_existing_local_cultural_cache_is_read_without_supabase(self):
        client = mock.Mock()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": ""}):
            cultural_info.init(directory, lambda: "Test prompt", client, "test-model")
            key = cultural_info._cache_key(self.PAYLOAD, "Test prompt")
            with contextlib.closing(sqlite3.connect(cultural_info.DB_PATH)) as connection, connection:
                connection.execute(
                    "INSERT INTO cultural_info (cache_key, dish, info) VALUES (?, ?, ?)",
                    (key, self.PAYLOAD["dish"], json.dumps(self.INFO)),
                )
            self.assertEqual(cultural_info._ask_gpt(self.PAYLOAD, "Test prompt"), self.INFO)
        client.chat.completions.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
