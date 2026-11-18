"""Official Supabase client for new dish information; never persists to local files."""
from datetime import datetime, timezone
from functools import lru_cache
import logging
import os
import sys
from pathlib import Path

import httpx

from dotenv import load_dotenv

from dish_info_common import DATA_VERSION, safe_url

load_dotenv(Path(__file__).resolve().parent / ".env")
log = logging.getLogger(__name__)
INFORMATION_TABLES = frozenset({"dish_backgrounds", "ingredient_nutrition",
                              "ingredient_allergens", "dish_health_info"})
READ_TABLES = INFORMATION_TABLES | {"recipes", "profiles", "dish_enrichment_runs", "dish_enrichment_events"}


class DishStoreError(RuntimeError):
    """Safe operational error; never includes credentials or database record bodies."""


class DishInformationStore:
    def __init__(self, client):
        self.client = client

    def _query(self, table, filters=None):
        if table not in READ_TABLES:
            raise ValueError("Unknown dish information table")
        query = self.client.table(table).select("*")
        for name, value in (filters or {}).items():
            query = query.eq(name, value)
        return query

    def regional_nutrition_matches(self, normalized_names, country):
        """Read approved, exact country/ingredient food-table matches in one query.

        This separate table never overwrites USDA cache rows. When an older
        installation has not run the optional migration, callers use USDA.
        """
        names = sorted({str(n) for n in normalized_names if n})
        if not names:
            return []
        try:
            return (self.client.table("regional_ingredient_nutrition")
                    .select("country,normalized_name,food_name,food_id,source_code,data_source,source_url,nutrients,approved,data_version")
                    .eq("country", country).eq("approved", True)
                    .in_("normalized_name", names).execute().data or [])
        except Exception:
            log.warning("Optional regional nutrition table is unavailable; falling back to USDA.")
            return []

    def get(self, table, filters):
        try:
            rows = self._query(table, filters).limit(1).execute().data
            return rows[0] if rows else None
        except Exception:
            log.warning("Dish information lookup failed for %s.", table)
            raise DishStoreError("Dish information is temporarily unavailable.") from None

    def list(self, table, filters=None):
        rows, start = [], 0
        try:
            while True:
                page = (self._query(table, filters)
                        .order("recipe_id" if table == "recipes" else "id")
                        .range(start, start + 499).execute().data)
                rows.extend(page)
                if len(page) < 500:
                    return rows
                start += 500
        except Exception:
            log.warning("Dish information listing failed for %s.", table)
            raise DishStoreError("Dish information is temporarily unavailable.") from None

    def upsert(self, table, row, on_conflict):
        if table not in INFORMATION_TABLES:
            raise ValueError("Unknown dish information table")
        record = dict(row)
        record.setdefault("data_version", DATA_VERSION)
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            rows = self.client.table(table).upsert(record, on_conflict=on_conflict).execute().data
            if not rows:
                raise DishStoreError("No persisted record returned.")
            return rows[0]
        except Exception:
            log.warning("Dish information save failed for %s.", table)
            raise DishStoreError("Dish information could not be saved.") from None

    def approve_background(self, record_id, reviewer_id, note, expected_updated_at=None):
        reviewer = self.get("profiles", {"id": reviewer_id})
        if not reviewer or reviewer.get("role") != "admin" or reviewer.get("status") != "active":
            raise DishStoreError("An active administrator must review this background.")
        row = self.get("dish_backgrounds", {"id": record_id})
        sources = (row or {}).get("sources") or []
        valid_sources = any(isinstance(source, dict) and safe_url(source.get("url")) for source in sources)
        if (not row or row.get("verification_status") != "pending" or not valid_sources
                or not row.get("historical_background") or not row.get("cultural_background")
                or not isinstance(note, str) or not note.strip()):
            raise DishStoreError("A sourced pending draft and review note are required.")
        if not expected_updated_at or row.get("updated_at") != expected_updated_at:
            raise DishStoreError("The draft changed. Reload it before approving.")
        now = datetime.now(timezone.utc).isoformat()
        try:
            updated = (self.client.table("dish_backgrounds").update({
                "verification_status": "verified", "reviewed_by": reviewer_id,
                "reviewed_at": now, "review_note": str(note).strip(), "updated_at": now,
            }).eq("id", record_id).eq("verification_status", "pending")
               .eq("updated_at", expected_updated_at).execute().data)
            if not updated:
                raise DishStoreError("Draft changed during review; reload and review again.")
            return updated[0]
        except Exception:
            raise DishStoreError("Background approval failed; reload and review again.") from None

    def save_background(self, row, observed):
        """Persist only if the draft/approval has not changed during provider calls."""
        record = dict(row)
        record.setdefault("data_version", DATA_VERSION)
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        filters = {"normalized_name": record["normalized_name"], "country": record["country"]}
        try:
            if observed is None:
                rows = self.client.table("dish_backgrounds").upsert(
                    record, on_conflict="normalized_name,country", ignore_duplicates=True).execute().data
            else:
                rows = (self.client.table("dish_backgrounds").update(record)
                        .eq("id", observed["id"]).eq("updated_at", observed["updated_at"])
                        .eq("verification_status", observed["verification_status"]).execute().data)
            if rows:
                return rows[0]
            latest = self.get("dish_backgrounds", filters)
            if latest:
                return latest  # A concurrent approval or newer draft always wins.
            raise DishStoreError("The background changed while enriching it.")
        except Exception:
            log.warning("Dish background conditional save failed.")
            raise DishStoreError("Dish information could not be saved.") from None

    def save_run(self, row):
        """Update a persistent batch journal without adding feature-version fields."""
        record = dict(row)
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            rows = self.client.table("dish_enrichment_runs").upsert(record, on_conflict="id").execute().data
            if not rows:
                raise DishStoreError("No saved run returned.")
            return rows[0]
        except Exception:
            raise DishStoreError("Enrichment progress could not be saved.") from None

    def save_event(self, row):
        record = dict(row)
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            self.client.table("dish_enrichment_events").upsert(
                record, on_conflict="run_id,component,entity_key").execute()
        except Exception:
            raise DishStoreError("Enrichment outcome could not be saved.") from None

    def save_events(self, rows):
        records = [dict(row, updated_at=datetime.now(timezone.utc).isoformat()) for row in rows]
        try:
            for start in range(0, len(records), 100):
                self.client.table("dish_enrichment_events").upsert(
                    records[start:start + 100], on_conflict="run_id,component,entity_key").execute()
        except Exception:
            raise DishStoreError("Enrichment outcomes could not be saved.") from None


@lru_cache(maxsize=1)
def _connected_store(url, key):
    """Cache successful clients only; a failed import must be retryable."""
    try:
        from supabase import ClientOptions, create_client
    except ImportError:
        log.warning('Supabase SDK cannot import in %s. Install with: "%s" -m pip install -r requirements.txt',
                    sys.executable, sys.executable)
        raise DishStoreError("Supabase SDK unavailable.") from None
    transport = None
    try:
        # The shared HTTP/2 connection produced concurrent ReadError failures in
        # live enrichment. HTTP/1.1 keeps a normal connection pool per client.
        transport = httpx.Client(http2=False, timeout=8)
        client = create_client(url, key, options=ClientOptions(
            auto_refresh_token=False, persist_session=False, postgrest_client_timeout=8,
            httpx_client=transport))
        return DishInformationStore(client)
    except Exception:
        if transport is not None:
            transport.close()
        log.warning("Dish Information Module could not initialize the Supabase client in %s.", sys.executable)
        raise DishStoreError("Supabase client initialization failed.") from None


def get_store():
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        log.warning("Dish information Supabase connection is not configured; check backend .env settings.")
        return None
    try:
        return _connected_store(url, key)
    except DishStoreError:
        return None


get_store.cache_clear = _connected_store.cache_clear
