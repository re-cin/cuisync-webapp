"""Measured enrichment coverage against the source dataset, never a local cache.

The caller may store the returned JSON-serializable report in Supabase or render a
human-readable audit. Reading all four tables once keeps the report reproducible
and avoids measuring unrelated or obsolete database rows as dataset coverage.
"""
from collections import Counter
from datetime import datetime, timezone
import math
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dish_info_common import (DATA_VERSION, country_key, fresh, ingredient_hash,
                              ingredient_names, normalize, safe_url)
from dish_health import health_cache_valid
from background_provenance import method_for_fields


def _metric(covered, total, denominator):
    return {"covered": covered, "total": total,
            "percent": round(100.0 * covered / total, 2) if total else 0.0,
            "denominator": denominator}


def _newest(rows, key):
    selected = {}
    for row in rows:
        identity = key(row)
        if identity is None:
            continue
        previous = selected.get(identity)
        if previous is None or str(row.get("updated_at", "")) > str(previous.get("updated_at", "")):
            selected[identity] = row
    return selected


def _sources(row):
    return [{"title": str(source.get("title") or "Source"), "url": safe_url(source.get("url"))}
            for source in row.get("sources", [])
            if isinstance(source, dict) and safe_url(source.get("url"))]


def _usable_nutrition(row):
    if not row or row.get("match_status") != "verified" or not fresh(row, max_age_days=30):
        return False
    url = safe_url(row.get("source_url"))
    if not url or urlsplit(url).hostname not in {"fdc.nal.usda.gov", "api.nal.usda.gov"}:
        return False
    source_id = str(row.get("source_food_id") or "")
    if not source_id.isdigit() or int(source_id) <= 0:
        return False
    nutrients = row.get("nutrients")
    if not isinstance(nutrients, dict) or nutrients.get("basis") != "per_100g":
        return False
    values = nutrients.get("values")
    if not isinstance(values, dict):
        return False
    return any(isinstance(item, dict) and not isinstance(item.get("value"), bool)
               and isinstance(item.get("value"), (int, float)) and math.isfinite(item["value"])
               and item["value"] >= 0 and item.get("unit")
               for item in values.values())


def _background_state(row):
    """Generated summaries count as available, while their unverified status remains explicit."""
    if not row:
        return "missing"
    if row.get("data_version") != DATA_VERSION:
        return "stale"
    status = row.get("verification_status")
    if (status in {"verified", "pending"} and (_sources(row) or row.get("generation_method") in {"gpt_fallback", "mixed"})
            and any(str(row.get(f) or "").strip() for f in ("historical_background", "cultural_background"))):
        return "reviewed" if status == "verified" and row.get("reviewed_at") and str(row.get("review_note") or "").strip() else "generated_unverified"
    if _deferred_reason(row.get("unavailable_reason")):
        return "deferred"
    return "error" if _temporary_reason(row.get("unavailable_reason")) else "unavailable"


def _deferred_reason(reason):
    lowered = str(reason or "").lower()
    return any(term in lowered for term in ("cooldown", "deferred", "rate limit", "rate_limit"))


def _temporary_reason(reason):
    lowered = str(reason or "").lower()
    return any(term in lowered for term in ("temporary", "rate limit", "rate_limit", "api error",
                                            "provider error", "connection", "database failure"))


def _valid_health(row):
    return bool(row and row.get("verification_status") == "verified" and health_cache_valid(row))


def _recipe_id(recipe):
    value = recipe.get("recipe_id", recipe.get("id"))
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _health_key(row):
    recipe_id = _recipe_id(row)
    return (recipe_id, row.get("ingredient_hash")) if recipe_id is not None else None


def build_report(recipes, store, *, events=None, run_id=None, example_recipe_ids=None):
    """Return coverage for the supplied recipes, with explicit, nonoverlapping gaps.

    Required table failures propagate; they must never produce a fictitious zero
    or complete report. Optional run journals may be supplied by the batch caller.
    No external information providers are called, and nothing is written here.
    """
    recipes = list(recipes)
    backgrounds = _newest(store.list("dish_backgrounds"), lambda r: (
        normalize(r.get("normalized_name") or r.get("dish_name")), country_key(r.get("country"))))
    nutrition = _newest(store.list("ingredient_nutrition"), lambda r: normalize(r.get("normalized_name") or r.get("ingredient_name")))
    rules = store.list("ingredient_allergens")
    health = _newest(store.list("dish_health_info"), _health_key)
    dishes, ingredients, country_recipes = {}, {}, {}
    for recipe in recipes:
        dish_key = (normalize(recipe.get("title") or recipe.get("alternative_title")), country_key(recipe.get("country")))
        dishes.setdefault(dish_key, recipe)
        country_recipes.setdefault(dish_key[1], []).append(recipe)
        for name in dict.fromkeys(normalize(n) for n in ingredient_names(recipe)):
            if not name:
                continue
            original = next(n for n in ingredient_names(recipe) if normalize(n) == name)
            ingredient = ingredients.setdefault(name, {"ingredient": original, "normalized_name": name,
                                                       "recipe_count": 0})
            ingredient["recipe_count"] += 1

    dish_counts = Counter()
    unavailable_dishes = []
    for key, recipe in dishes.items():
        row = backgrounds.get(key)
        state = _background_state(row)
        usable = state in {"reviewed", "generated_unverified"}
        historical = usable and bool(str(row.get("historical_background") or "").strip())
        cultural = usable and bool(str(row.get("cultural_background") or "").strip())
        dish_counts[state] += 1
        dish_counts["historical"] += bool(historical)
        dish_counts["cultural"] += bool(cultural)
        dish_counts["both"] += bool(historical and cultural)
        dish_counts["incomplete"] += bool(historical != cultural)
        dish_counts["without_either"] += not (historical or cultural)
        if not historical or not cultural:
            unavailable_dishes.append({"dish_name": recipe.get("title") or recipe.get("alternative_title") or "",
                                       "country": key[1], "state": state,
                                       "historical_available": bool(historical), "cultural_available": bool(cultural),
                                       "reason": str((row or {}).get("unavailable_reason") or state)})

    nutrition_counts = Counter()
    unmatched, matched_names = [], []
    for key, ingredient in ingredients.items():
        row = nutrition.get(key)
        if _usable_nutrition(row):
            state = "matched"
            matched_names.append(key)
        elif row is None:
            state = "missing"
        elif row.get("data_version") != DATA_VERSION or (row.get("match_status") == "verified" and not fresh(row, max_age_days=30)):
            state = "stale"
        elif row.get("match_status") == "error":
            state = "error"
        else:
            state = "unmatched"
        nutrition_counts[state] += 1
        if state != "matched":
            unmatched.append(dict(ingredient, state=state,
                                  status=str((row or {}).get("match_status") or "missing"),
                                  reason=str((row or {}).get("match_reason") or
                                             ("unusable_verified_record" if row and row.get("match_status") == "verified" else state))))

    # Use the same actual source-linked stored rules as the application. A rule
    # miss is unknown, not an inference that a food is safe or allergen-free.
    from dish_health import _allergens
    allergen_counts = Counter()
    unknown_allergens, allergen_details = [], []
    for key, ingredient in ingredients.items():
        groups, unknown = _allergens([ingredient["ingredient"]], rules)
        known = [g for g in groups if g.get("classification") in {"identified", "possible"}]
        state = ("identified" if any(g.get("classification") == "identified" for g in known)
                 else "possible" if known else "assessed_none" if any(g.get("classification") == "assessed_none" for g in groups) else "unknown")
        allergen_counts[state] += 1
        if state == "unknown":
            unknown_allergens.append(dict(ingredient))
        else:
            allergen_details.append(dict(ingredient, classification=state, allergens=[{
                "name": g["name"], "classification": g["classification"], "source_url": g.get("source_url", "")}
                for g in known]))

    recipe_counts, recipe_results = Counter(), []
    matched_set = set(matched_names)
    for recipe in recipes:
        row = health.get((_recipe_id(recipe), ingredient_hash(recipe)))
        valid = _valid_health(row)
        recipe_counts["current_health_records"] += valid
        recipe_counts["missing_or_stale_health_records"] += not valid
        benefits = bool(valid and any(isinstance(n, dict) and n.get("text") and safe_url(n.get("source_url"))
                                     for n in row.get("potential_benefits", [])))
        known_allergens = [a for a in (row.get("potential_allergens", []) if valid else [])
                           if isinstance(a, dict) and a.get("classification") in {"identified", "possible"}
                           and safe_url(a.get("source_url"))]
        considerations = bool(valid and any(isinstance(n, dict) and n.get("ingredient") and n.get("text")
                                           and safe_url(n.get("source_url"))
                                           for n in row.get("health_considerations", [])))
        recipe_counts["with_potential_benefits"] += benefits
        recipe_counts["with_specific_considerations_or_classified_allergens"] += bool(considerations or known_allergens)
        recipe_counts["with_classified_allergens"] += bool(known_allergens)
        recipe_counts["with_nutrition_matches"] += bool(valid and row.get("matched_ingredient_count", 0) > 0)
        names = list(dict.fromkeys(ingredient_names(recipe)))
        matched_ingredients = [n for n in names if normalize(n) in matched_set]
        unmatched_ingredients = [n for n in names if normalize(n) not in matched_set]
        background = backgrounds.get((normalize(recipe.get("title") or recipe.get("alternative_title")), country_key(recipe.get("country"))))
        background_state = _background_state(background)
        background_valid = background_state in {"reviewed", "generated_unverified"}
        recipe_results.append({
            "recipe_id": _recipe_id(recipe), "dish_name": recipe.get("title") or recipe.get("alternative_title") or "",
            "country": country_key(recipe.get("country")), "health_record_current": bool(valid),
            "nutrition_status": row.get("nutrition_status", "unavailable") if valid else "unavailable",
            "matched_ingredient_count": len(matched_ingredients), "ingredient_count": len(names),
            "ingredient_match_percent": round(100 * len(matched_ingredients) / len(names), 2) if names else 0.0,
            "matched_ingredients": matched_ingredients, "unmatched_ingredients": unmatched_ingredients,
            "potential_benefits_available": benefits,
            "specific_health_considerations_available": considerations,
            "classified_allergens": [{"name": a.get("name"), "classification": a.get("classification"),
                                      "from": a.get("from", [])} for a in known_allergens],
            "historical_available": bool(background_valid and str(background.get("historical_background") or "").strip()),
            "cultural_available": bool(background_valid and str(background.get("cultural_background") or "").strip()),
            "generation_method": (background or {}).get("generation_method"), "generation_fields": (background or {}).get("generation_fields") or {},
            "background_status": background_state, "background_sources": _sources(background) if background_valid else [],
        })

    events_available = events is not None
    if events is None and run_id is not None:
        try:
            events = store.list("dish_enrichment_events", {"run_id": run_id})
            events_available = True
        except Exception:
            events = []
    events = [event for event in (events or []) if run_id is None or str(event.get("run_id")) == str(run_id)]
    lookup_events = [event for event in events if event.get("component") in {"nutrition_lookup", "background_lookup"}]
    deferred_events = [event for event in lookup_events if event.get("status") == "deferred"
                       or _deferred_reason(event.get("reason"))]
    failed_events = [event for event in lookup_events if event.get("status") in {"error", "failed"}
                     and event not in deferred_events]
    event_counts = Counter(event.get("status", "unknown") for event in events)
    provider_failures = Counter(str(event.get("provider") or "unknown") for event in failed_events)
    recorded_error_attempts = 0
    for event in lookup_events:
        count = (event.get("details") or {}).get("api_error_count", 0)
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            recorded_error_attempts += count
    total_dishes, total_ingredients, total_recipes = len(dishes), len(ingredients), len(recipes)
    bands = Counter()
    countries = {}
    for row in recipe_results:
        fraction = row["matched_ingredient_count"] / row["ingredient_count"] if row["ingredient_count"] else 0
        band = "complete" if row["ingredient_count"] and fraction == 1 else "high" if fraction >= .8 else "partial" if fraction >= .5 else "low" if fraction > 0 else "unavailable"
        row["ingredient_coverage_band"] = band
        bands[band] += 1
    for (name, country), recipe in dishes.items():
        bucket = countries.setdefault(country, {"total": 0, "historical": 0, "cultural": 0, "independently_verified_historical": 0, "independently_verified_cultural": 0, "deferred": 0, "failed": 0})
        row = backgrounds.get((name, country)) or {}
        state = _background_state(row)
        bucket["total"] += 1
        for field, column in [("historical", "historical_background"), ("cultural", "cultural_background")]:
            available = state in {"reviewed", "generated_unverified"} and bool(row.get(column))
            bucket[field] += available
            bucket["independently_verified_" + field] += bool(available and state == "reviewed")
        bucket["deferred"] += state == "deferred"
        bucket["failed"] += state == "error"
    for bucket in countries.values():
        for field in ("historical", "cultural"):
            bucket[field + "_percent"] = round(100 * bucket[field] / bucket["total"], 2)
    generation = Counter()
    for key in dishes:
        row = backgrounds.get(key) or {}
        if _background_state(row) not in {"reviewed", "generated_unverified"}:
            generation["unavailable"] += 1
            continue
        label = method_for_fields(row.get("generation_fields"), row.get("generation_method"))
        generation[label or "unavailable"] += 1
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "run_id": str(run_id) if run_id is not None else None,
        "data_version": DATA_VERSION,
        "ingredient_coverage_bands": {band: _metric(bands[band], total_recipes, "recipes") for band in ["complete", "high", "partial", "low", "unavailable"]},
        "coverage_band_definitions": "Complete=100%; high=80 to <100%; partial=50 to <80%; low=>0 to <50%; unavailable=0%.",
        "background_coverage_by_country": countries,
        "allergen_assessment": {
            "assessed": _metric(total_ingredients - allergen_counts["unknown"], total_ingredients, "unique ingredients"),
            "identified_prevalence": _metric(allergen_counts["identified"], total_ingredients, "unique ingredients"),
            "possible_only": _metric(allergen_counts["possible"], total_ingredients, "unique ingredients"),
            "assessed_none": _metric(allergen_counts["assessed_none"], total_ingredients, "unique ingredients"),
            "unknown": _metric(allergen_counts["unknown"], total_ingredients, "unique ingredients")},
        "background_generation": {key: _metric(generation[key], total_dishes, "unique dishes") for key in ("source_based", "gpt_fallback", "mixed", "unavailable")},
        "dataset": {"recipes": total_recipes, "unique_dishes": total_dishes,
                    "unique_ingredients": total_ingredients, "countries": len(country_recipes)},
        "coverage": {
            "historical_background": _metric(dish_counts["historical"], total_dishes, "unique dishes"),
            "cultural_background": _metric(dish_counts["cultural"], total_dishes, "unique dishes"),
            "potential_nutritional_benefits": _metric(recipe_counts["with_potential_benefits"], total_recipes, "recipes"),
            "health_considerations_or_classified_allergens": _metric(recipe_counts["with_specific_considerations_or_classified_allergens"], total_recipes, "recipes"),
            "usda_ingredient_matches": _metric(nutrition_counts["matched"], total_ingredients, "unique ingredients"),
            "allergen_ingredient_classifications": _metric(allergen_counts["identified"] + allergen_counts["possible"], total_ingredients, "unique ingredients"),
            "current_health_records": _metric(recipe_counts["current_health_records"], total_recipes, "recipes"),
        },
        "dishes": {"total_unique": total_dishes, "with_historical": dish_counts["historical"],
                   "with_cultural": dish_counts["cultural"], "with_both": dish_counts["both"],
                   "without_either": dish_counts["without_either"], "incomplete_one_feature": dish_counts["incomplete"],
                   "with_unavailable_information": dish_counts["without_either"] + dish_counts["incomplete"],
                   "generated_unverified_records": dish_counts["generated_unverified"], "reviewed_records": dish_counts["reviewed"],
                   "missing_records": dish_counts["missing"], "stale_records": dish_counts["stale"],
                   "failed_lookups": dish_counts["error"], "deferred_lookups": dish_counts["deferred"],
                   "unavailable_or_incomplete": unavailable_dishes},
        "ingredients": {"total_unique": total_ingredients, "matched_usda": nutrition_counts["matched"],
                        "unmatched_usda": nutrition_counts["unmatched"], "failed_usda_lookups": nutrition_counts["error"],
                        "missing_usda_records": nutrition_counts["missing"], "stale_usda_records": nutrition_counts["stale"],
                        "not_matched_total": total_ingredients - nutrition_counts["matched"],
                        "identified_allergen_ingredients": allergen_counts["identified"],
                        "possible_only_allergen_ingredients": allergen_counts["possible"],
                        "classified_allergen_ingredients": allergen_counts["identified"] + allergen_counts["possible"],
                        "unknown_allergen_ingredients": allergen_counts["unknown"],
                        "unmatched": sorted(unmatched, key=lambda item: (-item["recipe_count"], item["normalized_name"])),
                        "unknown_allergens": sorted(unknown_allergens, key=lambda item: (-item["recipe_count"], item["normalized_name"])),
                        "allergen_classifications": allergen_details},
        "recipes": dict(recipe_counts, total=total_recipes),
        "api": {"run_events_available": events_available, "run_event_statuses": dict(event_counts),
                "failed_lookup_events": len(failed_events), "rate_limited_or_deferred_events": len(deferred_events),
                "recorded_api_error_attempts": recorded_error_attempts,
                "failed_events_by_provider": dict(provider_failures),
                "stored_current_failed_lookups": nutrition_counts["error"] + dish_counts["error"],
                "stored_current_deferred_lookups": dish_counts["deferred"],
                "note": "Run events are final per-lookup outcomes, not an invented request-attempt count. Deferred and failed lookup outcomes are separate; actual API error attempts include HTTP rate-limit responses. Missing/unmatched data is reported separately."},
        "recipe_results": recipe_results,
    }
    if example_recipe_ids is not None:
        wanted = {str(value) for value in example_recipe_ids}
        report["examples"] = [row for row in recipe_results if str(row["recipe_id"]) in wanted]
    else:
        preferred = ["SOUTH KOREA", "JAPAN", "THAILAND", "PHILIPPINES", "INDIA", "CHINA", "VIETNAM", "INDONESIA"]
        countries = preferred + sorted(set(country_recipes) - set(preferred))
        examples = []
        for country in countries:
            candidates = [row for row in recipe_results if row["country"] == country]
            if candidates:
                examples.append(max(candidates, key=lambda row: (row["historical_available"] + row["cultural_available"], row["health_record_current"], row["matched_ingredient_count"])))
            if len(examples) >= 13:
                break
        report["examples"] = examples
    return report


def render_markdown(report, unmatched_limit=30):
    """Render a derived audit, not a persistent enrichment data source."""
    dataset = report["dataset"]
    measured = datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Taipei"))
    lines = ["# CuiSync enrichment coverage", "", f"Measured at {measured:%Y-%m-%d %H:%M:%S} Asia/Taipei.", "",
             f"Dataset: **{dataset['recipes']} recipes**, **{dataset['unique_dishes']} unique dishes**, "
             f"**{dataset['unique_ingredients']} unique ingredients** across **{dataset['countries']} countries**.", "",
             "| Feature | Covered / denominator | Coverage |", "| --- | --- | --- |"]
    labels = {"historical_background": "Historical background", "cultural_background": "Cultural background",
              "potential_nutritional_benefits": "Potential nutritional benefit notes",
              "health_considerations_or_classified_allergens": "Specific health considerations or classified allergens",
              "usda_ingredient_matches": "USDA ingredient matches", "allergen_ingredient_classifications": "Identified/possible allergen ingredients",
              "current_health_records": "Current recipe health records"}
    for key, label in labels.items():
        metric = report["coverage"][key]
        lines.append(f"| {label} | {metric['covered']} / {metric['total']} {metric['denominator']} | {metric['percent']:.2f}% |")
    dishes, ingredients, api = report["dishes"], report["ingredients"], report["api"]
    lines.extend(["", f"Dishes with neither historical nor cultural information: **{dishes['without_either']}**. "
                  f"Dishes with only one background feature: **{dishes['incomplete_one_feature']}**. "
                  f"Generated/unverified records: **{dishes['generated_unverified_records']}**; independently reviewed records: **{dishes['reviewed_records']}**.",
                  "", f"Ingredients not matched to usable USDA data: **{ingredients['not_matched_total']}** "
                  f"({ingredients['unmatched_usda']} unmatched, {ingredients['failed_usda_lookups']} API errors, "
                  f"{ingredients['missing_usda_records']} missing, {ingredients['stale_usda_records']} stale). "
                  f"Unknown allergen classifications: **{ingredients['unknown_allergen_ingredients']}**. Unknown does not mean safe.",
                  "", f"Stored current failed lookups: **{api['stored_current_failed_lookups']}**; "
                  f"stored current deferred lookups: **{api['stored_current_deferred_lookups']}**. "
                  f"Run failed-lookup events: **{api['failed_lookup_events']}**; recorded API error attempts (including recovered retries): **{api['recorded_api_error_attempts']}**; rate-limited/deferred events: **{api['rate_limited_or_deferred_events']}**. "
                  f"Run event log available: **{api['run_events_available']}**.", "", "## Tested dataset examples", "",
                  "| Dish / country | USDA ingredient matches | Historical | Cultural | Background status |",
                  "| --- | --- | --- | --- | --- |"])
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    for example in report.get("examples", []):
        lines.append(f"| {cell(example['dish_name'])} / {cell(example['country'])} | {example['matched_ingredient_count']} / "
                     f"{example['ingredient_count']} ({example['ingredient_match_percent']:.2f}%) | "
                     f"{'Available' if example['historical_available'] else 'Unavailable'} | "
                     f"{'Available' if example['cultural_available'] else 'Unavailable'} | {cell(example['background_status'])} |")
    lines.extend(["", "## Remaining ingredient gaps", "", "| Ingredient | Recipes containing it | State | Reason |", "| --- | --- | --- | --- |"])
    gaps = ingredients["unmatched"] if unmatched_limit is None else ingredients["unmatched"][:unmatched_limit]
    for ingredient in gaps:
        lines.append(f"| {cell(ingredient['ingredient'])} | {ingredient['recipe_count']} | {cell(ingredient['state'])} | {cell(ingredient['reason'])} |")
    if unmatched_limit is not None and len(ingredients["unmatched"]) > unmatched_limit:
        lines.extend(["", f"Showing {unmatched_limit} of {len(ingredients['unmatched'])} gaps. The full report retains every unmatched ingredient and reason."])
    lines += ["", "## Ingredient completeness", "", report["coverage_band_definitions"], "", "| Band | Recipes | Percent |", "| --- | --- | --- |"]
    for band, metric in report["ingredient_coverage_bands"].items():
        lines.append(f"| {band} | {metric['covered']} | {metric['percent']}% |")
    lines += ["", "## Allergen assessment versus prevalence", "", "No identified major allergens does not mean allergen-free or safe.", "", "| Assessment | Ingredients | Percent |", "| --- | --- | --- |"]
    for category, metric in report["allergen_assessment"].items():
        lines.append(f"| {category} | {metric['covered']} | {metric['percent']}% |")
    lines += ["", "## Background coverage by country", "", "| Country | Dishes | History | Culture | Reviewed history/culture | Deferred | Failed |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for country, row in sorted(report["background_coverage_by_country"].items()):
        lines.append(f"| {country} | {row['total']} | {row['historical']} ({row['historical_percent']}%) | {row['cultural']} ({row['cultural_percent']}%) | {row['independently_verified_historical']}/{row['independently_verified_cultural']} | {row['deferred']} | {row['failed']} |")
    lines += ["", "## Background generation methods", "", "Mixed means source-backed text was retained and a missing field used GPT general knowledge.", "", "| Method | Dishes | Percent |", "| --- | --- | --- |"]
    for method, metric in report["background_generation"].items():
        lines.append(f"| {method} | {metric['covered']} | {metric['percent']}% |")
    return "\n".join(lines) + "\n"


def print_report(report):
    print(render_markdown(report))
