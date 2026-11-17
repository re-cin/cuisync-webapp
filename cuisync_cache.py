import hashlib
import json
import os
import sqlite3

from cuisync_prompts import get_system_prompt


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "explanations_cache.db")


PROMPT_VERSION = "v8"
MODEL_NAME = os.environ.get("OPENAI_MODEL", "gpt-4o")


def _init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS explanations (
                cache_key   TEXT PRIMARY KEY,
                input_dish  TEXT,
                matched_dish TEXT,
                explanation TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def make_cache_key(evidence: dict) -> str:
    normalized = {}
    for k, v in evidence.items():
        if isinstance(v, list):
            normalized[k] = sorted(v)
        else:
            normalized[k] = v
    raw = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    raw += f"|{PROMPT_VERSION}|{MODEL_NAME}"
    # A prompt edited on the admin page gets its own cache entries
    from cuisync_prompts import DEFAULT_SYSTEM_PROMPT
    if get_system_prompt() != DEFAULT_SYSTEM_PROMPT:
        raw += "|" + hashlib.sha256(get_system_prompt().encode("utf-8")).hexdigest()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached(evidence: dict):
    key = make_cache_key(evidence)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT explanation FROM explanations WHERE cache_key = ?", (key,)
        ).fetchone()
    return row[0] if row else None


def save_cached(evidence: dict, explanation: str):
    key = make_cache_key(evidence)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO explanations "
            "(cache_key, input_dish, matched_dish, explanation) VALUES (?, ?, ?, ?)",
            (key, evidence.get("input_dish"), evidence.get("matched_dish"), explanation),
        )


def explain_with_cache(evidence: dict, generate_fn, is_valid_fn=None) -> str:
    cached = get_cached(evidence)
    if cached is not None:
        return cached

    explanation = generate_fn(evidence)

    if is_valid_fn is None or is_valid_fn(explanation, evidence):
        save_cached(evidence, explanation)
    return explanation


_init_db()


