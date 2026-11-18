"""Dataset-wide orchestration with Supabase-only checkpoints and outcome logs."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid

from dish_info_common import country_key, ingredient_hash, ingredient_names, normalize
from dish_information_store import DishStoreError


def now():
    return datetime.now(timezone.utc).isoformat()


def dataset_hash(recipes):
    identity = sorted((str(r.get("recipe_id")), normalize(r.get("title")),
                       country_key(r.get("country")), ingredient_hash(r)) for r in recipes)
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()


def retry_store(function, *args, **kwargs):
    """Idempotent API calls tolerate brief transport/database interruptions."""
    for attempt in range(3):
        try:
            return function(*args, **kwargs)
        except DishStoreError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


class SnapshotStore:
    """In-process read snapshot; every persistent write goes straight to Supabase."""
    KEYS = {"dish_backgrounds": ("normalized_name", "country"),
            "ingredient_nutrition": ("normalized_name",),
            "ingredient_allergens": ("normalized_name",),
            "dish_health_info": ("recipe_id", "ingredient_hash")}

    def __init__(self, store):
        self.store, self.failed = store, False
        self.cached_nutrition_only = False
        self.lock = threading.RLock()
        self.rows = {}
        self.journal = None
        for table in self.KEYS:
            self.rows[table] = {}
            for row in retry_store(store.list, table):
                self._remember(table, row)

    def _key(self, table, row):
        return tuple(str(row.get(name)) for name in self.KEYS[table])

    def _remember(self, table, row):
        with self.lock:
            self.rows[table][self._key(table, row)] = dict(row)

    def get(self, table, filters):
        if table in self.rows:
            with self.lock:
                for row in self.rows[table].values():
                    if all(str(row.get(k)) == str(v) for k, v in filters.items()):
                        return dict(row)
                return None
        return self.store.get(table, filters)

    def list(self, table, filters=None):
        if table in self.rows:
            with self.lock:
                return [dict(row) for row in self.rows[table].values()
                        if all(row.get(k) == v for k, v in (filters or {}).items())]
        return self.store.list(table, filters)

    def upsert(self, table, row, on_conflict):
        try:
            saved = retry_store(self.store.upsert, table, row, on_conflict=on_conflict)
            self._remember(table, saved)
            return saved
        except DishStoreError:
            self.failed = True
            raise

    def save_background(self, row, observed):
        try:
            saved = retry_store(self.store.save_background, row, observed)
            self._remember("dish_backgrounds", saved)
            return saved
        except DishStoreError:
            self.failed = True
            raise

    def record_event(self, *args, **kwargs):
        if self.journal:
            try:
                self.journal.record_event(*args, **kwargs)
            except DishStoreError:
                self.failed = True
                raise

    def regional_nutrition_matches(self, normalized_names, country):
        """Read separate approved Asian food tables; never treat as USDA rows."""
        finder = getattr(self.store, "regional_nutrition_matches", None)
        return finder(normalized_names, country) if callable(finder) else []

    def find_equivalent_nutrition(self, identity):
        from ingredient_matching import canonical_identity
        from dish_info_common import fresh
        with self.lock:
            for row in self.rows["ingredient_nutrition"].values():
                if row.get("match_status") == "verified" and fresh(row):
                    actual = (row.get("nutrients") or {}).get("canonical_identity") or canonical_identity(row.get("ingredient_name"))
                    if actual == identity:
                        return dict(row)
        return None


class RunJournal:
    def __init__(self, store, run, events=None):
        self.store, self.run = store, run
        self.lock = threading.Lock()
        self.events = {(r["component"], r["entity_key"]): r for r in (events or [])}

    def record_event(self, component, entity_key, status, reason="", details=None, provider=""):
        entity_key = str(entity_key)
        with self.lock:
            previous = self.events.get((component, entity_key), {})
            detail = dict(details or {})
            previous_errors = (previous.get("details") or {}).get("api_error_count", 0)
            detail["api_error_count"] = previous_errors + detail.get("api_error_count", 0)
            row = {"run_id": self.run["id"], "component": component,
                   "entity_key": str(entity_key), "status": status, "reason": str(reason),
                   "provider": provider, "details": detail}
            retry_store(self.store.save_event, row)
            self.events[(component, str(entity_key))] = row

    def heartbeat(self, phase, processed, total):
        self.run["options"] = dict(self.run.get("options") or {}, phase=phase, processed=processed, total=total)
        self.run = retry_store(self.store.save_run, self.run)

    def record_events(self, rows):
        records = [dict(row, run_id=self.run["id"]) for row in rows]
        if hasattr(self.store, "save_events"):
            retry_store(self.store.save_events, records)
        else:
            for record in records:
                retry_store(self.store.save_event, record)
        with self.lock:
            for row in records:
                self.events[(row["component"], row["entity_key"])] = row


def _phase(name, items, function, workers, journal, stop, delay=0):
    total, completed, statuses = len(items), 0, Counter()
    journal.heartbeat(name, 0, total)
    print(f"PHASE {name}: {total} unique entities; workers={workers}", flush=True)

    def work(item):
        if stop.is_set():
            return None
        try:
            result = function(item)
        except BaseException:
            stop.set()
            raise
        performed_lookup = True
        if isinstance(result, tuple):
            result, performed_lookup = result
        if delay and performed_lookup:
            time.sleep(delay)
        return result

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="batch-" + name) as pool:
        futures = [pool.submit(work, item) for item in items]
        try:
            for future in as_completed(futures):
                result = future.result()
                if result is None:
                    continue
                statuses[result] += 1
                completed += 1
                if completed % 25 == 0 or completed == total:
                    journal.heartbeat(name, completed, total)
                    print(f"PROGRESS {name}: {completed}/{total} {dict(statuses)}", flush=True)
        except BaseException:
            stop.set()
            for future in futures:
                future.cancel()
            raise
    return dict(statuses)


def run_dataset(store, recipes, *, client=None, resume_run=None, workers=4,
                mode="all", retry_failed=False, delay=0.1, report_only=False,
                cached_health_only=False, refresh_ingredients=()):
    from dish_health import _nutrition_record, enrich_health, seed_allergen_rules
    from dish_backgrounds import enrich_background
    from enrichment_report import build_report, render_markdown

    signature = dataset_hash(recipes)
    snapshot = SnapshotStore(store)
    run = store.get("dish_enrichment_runs", {"id": resume_run}) if resume_run else None
    if resume_run and (not run or run["dataset_hash"] != signature):
        raise DishStoreError("The resume run does not match this dataset.")
    old_events = store.list("dish_enrichment_events", {"run_id": resume_run}) if run else []
    from dish_backgrounds import restore_provider_cooldowns
    restore_provider_cooldowns(old_events)
    before = build_report(recipes, snapshot, events=old_events, run_id=resume_run)
    if report_only:
        print(render_markdown(before), flush=True)
        return before

    if run is None:
        run = {"id": str(uuid.uuid4()), "dataset_hash": signature,
               "status": "running", "started_at": now(), "coverage_before": before,
               "options": {"mode": mode, "workers": workers, "recipe_count": len(recipes)}}
    else:
        run.update(status="running", finished_at=None,
                   options=dict(run.get("options") or {}, mode=mode, workers=workers, recipe_count=len(recipes)))
    run = store.save_run(run)
    journal = RunJournal(store, run, old_events)
    snapshot.journal = journal
    stop = threading.Event()
    ingredients, dishes = {}, {}
    for recipe in recipes:
        dish = dishes.setdefault((normalize(recipe.get("title")), country_key(recipe.get("country"))), dict(recipe, alternative_names=[]))
        for alternative in [recipe.get("alternative_title"), *(recipe.get("alternative_names") or [])]:
            if alternative and alternative not in dish["alternative_names"]:
                dish["alternative_names"].append(alternative)
        for ingredient in ingredient_names(recipe):
            ingredients.setdefault(normalize(ingredient), ingredient)
    print(f"RUN_ID={run['id']}", flush=True)
    print(f"DATASET recipes={len(recipes)} unique_dishes={len(dishes)} unique_ingredients={len(ingredients)}", flush=True)
    refreshed_names = {normalize(name) for name in refresh_ingredients}
    try:
        seed_allergen_rules(snapshot)
        if mode in {"all", "health"}:
            from dish_health import _allergens
            rules = snapshot.list("ingredient_allergens", {"approved": True})
            allergen_events = []
            for key, ingredient in ingredients.items():
                found, unknown = _allergens([ingredient], rules)
                classification = "identified" if any(a["classification"] == "identified" for a in found) else "possible" if any(a["classification"] == "possible" for a in found) else "assessed_none" if any(a["classification"] == "assessed_none" for a in found) else "unknown"
                allergen_events.append({"component": "allergen", "entity_key": key,
                                        "status": "success" if classification != "unknown" else "unmatched",
                                        "provider": "validated_ingredient_rules", "reason": classification,
                                        "details": {"ingredient": ingredient, "classification": classification,
                                                    "allergens": found, "unknown_ingredients": unknown}})
            journal.record_events(allergen_events)
            print(f"Allergen classifications logged: {len(allergen_events)}", flush=True)
            from ingredient_matching import canonical_identity
            identity_locks = [threading.Lock() for _ in range(64)]
            def nutrition(item):
                key, ingredient = item
                previous = snapshot.get("ingredient_nutrition", {"normalized_name": key})
                force = key in refreshed_names or (retry_failed and previous and previous.get("match_status") == "error")
                with identity_locks[hash(canonical_identity(ingredient)) % len(identity_locks)]:
                    row = _nutrition_record(ingredient, snapshot, refresh=bool(force))
                if snapshot.failed:
                    raise DishStoreError("Persistence failed during ingredient enrichment.")
                status = "success" if row.get("match_status") == "verified" else "error" if row.get("match_status") == "error" else "unmatched"
                if row.get("match_reason") == "rate_limited":
                    status = "deferred"
                journal.record_event("ingredient", key, status, row.get("match_reason", ""),
                                     {"ingredient": ingredient, "match_status": row.get("match_status")}, "usda")
                return status
            selected_ingredients = [(key, name) for key, name in ingredients.items()
                                    if not cached_health_only or key in refreshed_names]
            if cached_health_only and any(snapshot.get("ingredient_nutrition", {"normalized_name": key}) is None
                                          for key in ingredients):
                raise DishStoreError("Populate missing ingredient lookups before a cached-health-only run.")
            _phase("ingredients", selected_ingredients, nutrition, workers, journal, stop, delay)

        if mode in {"all", "backgrounds"}:
            def background(item):
                (name, country), recipe = item
                previous = snapshot.get("dish_backgrounds", {"normalized_name": name, "country": country})
                force = retry_failed and previous and not all(
                    str(previous.get(field) or '').strip()
                    for field in ('historical_background', 'cultural_background'))
                row = enrich_background(recipe, snapshot, client=client, refresh=bool(force))
                if snapshot.failed:
                    raise DishStoreError("Persistence failed during background enrichment.")
                history, culture = bool(row.get("historical_background")), bool(row.get("cultural_background"))
                reason = row.get("unavailable_reason", "")
                status = "success" if history and culture else "incomplete" if history or culture else "deferred" if "cooldown" in reason.lower() or "deferred" in reason.lower() or "rate limit" in reason.lower() else "error" if reason.startswith("Temporary") else "unmatched"
                journal.record_event("background", country + "|" + name, status, reason,
                                     {"dish": recipe.get("title"), "country": country, "history": history, "culture": culture,
                                      "source_count": len(row.get("sources") or [])})
                cached = previous is not None and row.get("updated_at") == previous.get("updated_at")
                lookup = journal.events.get(("background_lookup", country + "|" + name), {})
                attempted = sum((lookup.get("details") or {}).get("provider_attempts", {}).values())
                return ("cached" if cached else status, bool(attempted) and not cached)
            _phase("backgrounds", list(dishes.items()), background, workers, journal, stop, delay)

        if mode in {"all", "health"}:
            snapshot.cached_nutrition_only = cached_health_only
            def health(recipe):
                if refreshed_names.intersection(normalize(name) for name in ingredient_names(recipe)):
                    with snapshot.lock:
                        snapshot.rows["dish_health_info"].pop((str(recipe["recipe_id"]), ingredient_hash(recipe)), None)
                row = enrich_health(recipe, snapshot)
                if snapshot.failed:
                    raise DishStoreError("Persistence failed during health enrichment.")
                status = "success" if row.get("nutrition_status") == "verified" else "incomplete" if row.get("verification_status") == "verified" else "unmatched"
                journal.record_event("health", str(recipe["recipe_id"]) + "|" + ingredient_hash(recipe), status,
                                     details={"recipe_id": recipe["recipe_id"], "dish": recipe.get("title"),
                                              "matched": row.get("matched_ingredient_count"), "ingredients": row.get("ingredient_count")})
                return status
            _phase("health", recipes, health, workers, journal, stop, delay)
    except BaseException as error:
        run = dict(journal.run, status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed", finished_at=now())
        try:
            run["coverage_after"] = build_report(recipes, snapshot, events=list(journal.events.values()), run_id=run["id"])
            store.save_run(run)
        except DishStoreError:
            pass
        print(f"Run stopped safely. Resume with --resume-run {run['id']}", flush=True)
        raise

    after = build_report(recipes, snapshot, events=list(journal.events.values()), run_id=run["id"])
    has_gaps = any(event["status"] in {"unmatched", "incomplete", "error", "deferred"} for event in journal.events.values())
    store.save_run(dict(journal.run, status="partial" if has_gaps else "complete", coverage_after=after, finished_at=now()))
    print(render_markdown(after), flush=True)
    print(f"RUN_FINISHED={run['id']} processing_complete=true coverage_complete={not has_gaps}", flush=True)
    return after
