"""Resume Supabase-only enrichment; progress is the database, never a local file."""
import argparse
import json
import os
from pathlib import Path
import time

from dish_info_common import country_key, normalize
from dish_information_store import DishStoreError, get_store


class BatchStore:
    """Track persistence failures even when graceful UI enrichers swallow errors."""
    def __init__(self, store):
        self.store, self.failed = store, False

    def _call(self, method, *args, **kwargs):
        try:
            return getattr(self.store, method)(*args, **kwargs)
        except DishStoreError:
            self.failed = True
            raise

    def regional_nutrition_matches(self, names, country):
        finder = getattr(self.store, "regional_nutrition_matches", None)
        return self._call("regional_nutrition_matches", names, country) if callable(finder) else []

    def get(self, *args, **kwargs):
        return self._call("get", *args, **kwargs)

    def list(self, *args, **kwargs):
        return self._call("list", *args, **kwargs)

    def upsert(self, *args, **kwargs):
        return self._call("upsert", *args, **kwargs)

    def save_background(self, row, observed):
        if hasattr(self.store, "save_background"):
            return self._call("save_background", row, observed)
        return self.upsert("dish_backgrounds", row, on_conflict="normalized_name,country")


def run(store, client=None, limit=None, recipe_id=None, refresh=False, mode="all", delay=0.2):
    from dish_health import enrich_health, seed_allergen_rules
    from dish_backgrounds import enrich_background
    store = BatchStore(store)
    seeded = seed_allergen_rules(store)
    print(f"Allergen rules added: {seeded}")
    recipes = store.list("recipes", {"recipe_id": recipe_id} if recipe_id is not None else None)
    if limit is not None:
        recipes = recipes[:limit]
    seen_dishes, refreshed_ingredients = set(), set()
    for position, recipe in enumerate(recipes, 1):
        key = (normalize(recipe.get("title")), country_key(recipe.get("country")))
        try:
            if mode in {"all", "health"}:
                row = enrich_health(recipe, store, refresh=refresh, refreshed_ingredients=refreshed_ingredients)
                if store.failed:
                    raise DishStoreError("Persistence failed during enrichment.")
                print(f"{position}/{len(recipes)} recipe {recipe['recipe_id']}: health {row['verification_status']}, nutrition {row['nutrition_status']}")
            if mode in {"all", "backgrounds"} and key not in seen_dishes:
                row = enrich_background(recipe, store, client=client, refresh=refresh)
                if store.failed:
                    raise DishStoreError("Persistence failed during enrichment.")
                print(f"{position}/{len(recipes)} recipe {recipe['recipe_id']}: background {row['verification_status']}")
                seen_dishes.add(key)
        except DishStoreError:
            print(f"Recipe {recipe['recipe_id']}: database unavailable; stopping safely. Re-run to resume.")
            return 1
        if delay:
            time.sleep(delay)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="Maximum recipes; start small to check provider usage.")
    parser.add_argument("--recipe-id", type=int)
    parser.add_argument("--mode", choices=("all", "health", "backgrounds"), default="all")
    parser.add_argument("--refresh", action="store_true", help="Explicitly refresh valid records; backgrounds become pending again.")
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parent / "recipes.json",
                        help="Dataset recipe identities/ingredients; results and progress persist only in Supabase.")
    parser.add_argument("--workers", type=int, default=4, help="Bounded concurrent lookups (1-8).")
    parser.add_argument("--resume-run", help="Resume a Supabase run ID with the same dataset.")
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed provider lookups while preserving valid data.")
    parser.add_argument("--report-only", action="store_true", help="Read and print measured coverage without calling providers.")
    parser.add_argument("--cached-health-only", action="store_true", help="Rebuild health from populated ingredient records without rerunning all lookups.")
    parser.add_argument("--refresh-ingredient", action="append", default=[], help="Explicitly retry one named dataset ingredient; repeat as needed.")
    args = parser.parse_args()
    if args.cached_health_only and args.mode != "health":
        parser.error("--cached-health-only requires --mode health")
    if (args.limit is not None and args.limit < 1) or args.delay < 0 or not 1 <= args.workers <= 8:
        parser.error("limit must be positive and delay cannot be negative")
    store = get_store()
    if store is None:
        parser.error("Configure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and install requirements first.")
    if args.seed_only:
        from dish_health import seed_allergen_rules
        print(f"Allergen rules added: {seed_allergen_rules(store)}")
        return 0
    client = None
    if os.environ.get("OPENAI_API_KEY") and args.mode != "health":
        from openai import OpenAI
        client = OpenAI(timeout=20, max_retries=0)
    if not args.dataset.is_file():
        parser.error("The dataset recipes.json file was not found.")
    recipes = json.loads(args.dataset.read_text(encoding="utf-8"))
    remote = {str(r["recipe_id"]): r for r in store.list("recipes")}
    from dish_info_common import ingredient_hash
    mismatches = [r.get("recipe_id") for r in recipes
                  if str(r.get("recipe_id")) not in remote
                  or ingredient_hash(r) != ingredient_hash(remote[str(r.get("recipe_id"))])
                  or normalize(r.get("title")) != normalize(remote[str(r.get("recipe_id"))].get("title"))
                  or country_key(r.get("country")) != country_key(remote[str(r.get("recipe_id"))].get("country"))]
    if mismatches:
        parser.error(f"{len(mismatches)} source recipes differ from Supabase; synchronize/review the dataset before enriching it.")
    if args.recipe_id is not None:
        recipes = [r for r in recipes if str(r.get("recipe_id")) == str(args.recipe_id)]
    if args.limit is not None:
        recipes = recipes[:args.limit]
    if not recipes:
        parser.error("No recipes matched this request.")
    if args.refresh:
        # Explicit legacy per-recipe refresh remains available; full enrichment
        # otherwise skips source-backed valid records and retries versioned gaps.
        return run(store, client, args.limit, args.recipe_id, True, args.mode, args.delay)
    from enrichment_batch import run_dataset
    run_dataset(store, recipes, client=client, resume_run=args.resume_run, workers=args.workers,
                mode=args.mode, retry_failed=args.retry_failed, delay=args.delay, report_only=args.report_only,
                cached_health_only=args.cached_health_only, refresh_ingredients=args.refresh_ingredient)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DishStoreError:
        raise SystemExit("Supabase is unavailable; existing records are retained. Re-run to resume.") from None
