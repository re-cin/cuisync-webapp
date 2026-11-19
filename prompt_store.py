"""Versioned server-side writing instructions; never stores protected rules.

Supabase is authoritative when configured. Reads fall back to the last good
snapshot (SQLite on disk); failed remote writes are never activated locally.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time

import requests

NAMES = ("explanation", "background_source", "background_fallback")
log = logging.getLogger(__name__)


class PromptUnavailable(RuntimeError):
    pass


class PromptConflict(ValueError):
    pass


def defaults():
    import cuisync_prompts
    import background_prompts
    return {"explanation": cuisync_prompts.DEFAULT_SYSTEM_PROMPT, **background_prompts.defaults()}


class PromptStore:
    def __init__(self, data_dir, remote=None, initialize=True):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        self.key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        self.remote = bool(self.url and self.key) if remote is None else remote
        self.initialize = initialize
        self.path = self.data_dir / ("prompt_remote_snapshot.sqlite3" if self.remote else "prompt_versions.sqlite3")
        self.lock = threading.RLock()
        self.retry_after = 0
        self.degraded = False
        with closing(self._connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS prompt_versions (
                name TEXT NOT NULL, version INTEGER NOT NULL, instructions TEXT NOT NULL,
                administrator TEXT NOT NULL, modified_at TEXT NOT NULL, active INTEGER NOT NULL,
                PRIMARY KEY(name, version))""")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS prompt_one_active ON prompt_versions(name) WHERE active = 1")
        self.legacy = self._legacy()
        if not self.remote:
            for name, default in defaults().items():
                with closing(self._connect()) as conn, conn:
                    conn.execute("INSERT OR IGNORE INTO prompt_versions VALUES (?,1,?,?,?,1)",
                                 (name, self.legacy.get(name) or default, "legacy import" if self.legacy.get(name) else "system default", self._now()))
        self.refresh()

    def _legacy(self):
        try:
            values = json.loads((self.data_dir / "prompts.json").read_text(encoding="utf-8"))
            return {k: v.strip() for k, v in values.items() if k in NAMES and isinstance(v, str) and v.strip()}
        except (OSError, ValueError, AttributeError):
            return {}

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _request(self, method, endpoint, **kwargs):
        headers = {"apikey": self.key, "Content-Type": "application/json"}
        if self.key.startswith("eyJ"):
            headers["Authorization"] = "Bearer " + self.key
        try:
            response = requests.request(method, self.url + "/rest/v1/" + endpoint,
                                        headers=headers, timeout=4, **kwargs)
            if response.status_code == 409:
                raise PromptConflict("Someone else saved this prompt while you were editing. Reload the page, review the current instructions, and try again.")
            response.raise_for_status()
            return response.json()
        except PromptConflict:
            raise
        except (requests.RequestException, ValueError) as exc:
            raise PromptUnavailable("Your instructions could not be saved right now. Nothing changed. Please try again later.") from exc

    def refresh(self, strict=False):
        if not self.remote:
            return
        with self.lock:
            if not strict and time.monotonic() < self.retry_after:
                return
            try:
                rows = self._request("GET", "prompt_versions", params={"active": "eq.true", "select": "*"})
                missing = set(NAMES) - {row["name"] for row in rows}
                for name in missing if self.initialize else ():
                    # The database serializes this import and never replaces a saved version.
                    self._request("POST", "rpc/activate_prompt_version", json={
                        "p_name": name, "p_instructions": self.legacy.get(name) or defaults()[name],
                        "p_administrator": "legacy import" if self.legacy.get(name) else "system default",
                        "p_expected": 0, "p_initialize": True})
                if missing and self.initialize:
                    rows = self._request("GET", "prompt_versions", params={"active": "eq.true", "select": "*"})
                if self.initialize and {row["name"] for row in rows} != set(NAMES):
                    raise PromptUnavailable("Prompt database returned an incomplete configuration.")
                with closing(self._connect()) as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    for row in rows:
                        self._cache_active(conn, row)
                self.degraded = False
                self.retry_after = 0
            except (PromptUnavailable, KeyError, TypeError):
                self.degraded = True
                self.retry_after = time.monotonic() + 10
                log.warning("Prompt storage unavailable; using last known instructions with protected backend rules.")
                if strict:
                    raise PromptUnavailable("Prompt database is unavailable or returned an incomplete configuration.") from None

    @staticmethod
    def _cache(conn, row):
        conn.execute("INSERT OR REPLACE INTO prompt_versions VALUES (?,?,?,?,?,?)", (
            row["name"], row["version"], row["instructions"], row["administrator"], row["modified_at"], int(row["active"])))

    @classmethod
    def _cache_active(cls, conn, row):
        latest = conn.execute("SELECT max(version) FROM prompt_versions WHERE name=?", (row["name"],)).fetchone()[0]
        if latest is not None and latest > row["version"]:
            return  # An older in-flight read cannot undo a newer local snapshot.
        conn.execute("UPDATE prompt_versions SET active=0 WHERE name=?", (row["name"],))
        cls._cache(conn, row)

    def active(self, name, refresh=True):
        if name not in NAMES:
            raise ValueError("Unknown prompt")
        if refresh:
            self.refresh()
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM prompt_versions WHERE name=? AND active=1", (name,)).fetchone()
        return dict(row) if row else dict(name=name, version=0, instructions=self.legacy.get(name) or defaults()[name],
                                        administrator="offline fallback", modified_at="", active=True)

    def get_prompt(self, name, default=None):
        return self.active(name)["instructions"]

    def history(self, name):
        if self.remote:
            try:
                return self._request("GET", "prompt_versions", params={"name": "eq." + name, "select": "*", "order": "version.desc", "limit": "100"})
            except PromptUnavailable:
                self.degraded = True
        with closing(self._connect()) as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM prompt_versions WHERE name=? ORDER BY version DESC LIMIT 100", (name,))]

    def activate(self, name, instructions, administrator, expected):
        import background_prompts
        if name not in NAMES:
            raise ValueError("Unknown prompt")
        instructions = background_prompts.validate_prompt(instructions)
        if self.remote:
            row = self._request("POST", "rpc/activate_prompt_version", json={
                "p_name": name, "p_instructions": instructions, "p_administrator": str(administrator),
                "p_expected": expected, "p_initialize": False})
            with closing(self._connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                self._cache_active(conn, row)
            self.degraded = False
            return row
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT version FROM prompt_versions WHERE name=? AND active=1", (name,)).fetchone()
            if not row or row[0] != expected:
                raise PromptConflict("Someone else saved this prompt while you were editing. Reload the page, review the current instructions, and try again.")
            version = expected + 1
            conn.execute("UPDATE prompt_versions SET active=0 WHERE name=?", (name,))
            conn.execute("INSERT INTO prompt_versions VALUES (?,?,?,?,?,1)",
                         (name, version, instructions, str(administrator), self._now()))
        return self.active(name, refresh=False)


_store = None
_store_lock = threading.RLock()


def configure(data_dir):
    global _store
    with _store_lock:
        _store = PromptStore(data_dir)
    return _store


def get_store():
    global _store
    with _store_lock:
        if _store is None:
            _store = PromptStore(Path(__file__).resolve().parent / "admin_data")
        return _store
