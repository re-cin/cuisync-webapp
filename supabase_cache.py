"""Server-side persistent caches. Failures are cache misses, never app failures."""
import logging
import os
from datetime import datetime, timezone

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger(__name__)
_COLUMNS = {"explanations": "explanation", "cultural_info": "info"}
TIMEOUT = 5


def configured():
    return bool(os.environ.get("SUPABASE_URL", "").strip()
                and os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip())


def _request(method, table, **kwargs):
    if table not in _COLUMNS:
        raise ValueError("Unknown cache table")
    if not configured():
        return None
    url = os.environ["SUPABASE_URL"].strip().rstrip("/")
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
    headers = {"apikey": key}
    if key.startswith("eyJ"):
        headers["Authorization"] = "Bearer " + key
    headers.update(kwargs.pop("headers", {}))
    try:
        response = requests.request(method, url + "/rest/v1/" + table,
                                    headers=headers, timeout=TIMEOUT, **kwargs)
        response.raise_for_status()
        return response
    except requests.RequestException:
        # Do not log exception text: it may contain URLs, keys or record contents.
        log.warning("Supabase %s cache %s failed; continuing without persistence.", table, method)
        return None


def get(table, cache_key, column):
    if _COLUMNS.get(table) != column:
        raise ValueError("Unknown cache column")
    response = _request("GET", table, params={
        "select": column, "cache_key": "eq." + cache_key, "limit": "1"})
    if response is None:
        return None
    try:
        rows = response.json()
        return rows[0][column] if rows else None
    except (ValueError, KeyError, IndexError, TypeError):
        log.warning("Supabase %s cache returned invalid data; treating as a cache miss.", table)
        return None


def save(table, row):
    if table not in _COLUMNS:
        raise ValueError("Unknown cache table")
    record = dict(row)
    record.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    response = _request("POST", table, params={"on_conflict": "cache_key"},
                        headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                        json=record)
    return response is not None
