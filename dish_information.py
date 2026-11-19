"""Public presentation and bounded automatic enrichment of Supabase dish records."""
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import re
import threading

from dish_info_common import DATA_VERSION, country_key, fresh, ingredient_hash, normalize, safe_url
from dish_information_store import DishStoreError, get_store
from dish_health import HEALTH_ENRICHMENT_VERSION, health_cache_valid
from nutritional_benefits import select_important_benefits
from regional_allergens import regional_allergen_alerts, regional_market_catalog, REGIONAL_DISCLAIMER, user_allergen_of
from urllib.parse import urlsplit

log = logging.getLogger(__name__)
_client = None
_jobs = set()
_jobs_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dish-information")


def configure(client=None):
    global _client
    _client = client


def _sources(rows):
    return [{"title": str(row.get("title") or "Source"), "url": safe_url(row.get("url"))}
            for row in (rows or []) if isinstance(row, dict) and safe_url(row.get("url"))]


def _notes(rows):
    notes = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("text"):
            continue
        notes.append({"text": str(row["text"]), "ingredient": str(row.get("ingredient") or ""),
                      "source_url": safe_url(row.get("source_url")),
                      "nutrient": str(row.get("nutrient") or ""),
                      "evidence_type": str(row.get("evidence_type") or "")})
    return notes


def _key_benefits(notes, limit=3, recipe=None):
    """Select distinct, supported nutrient contributions for a concise UI.

    A recipe has ingredient names but no quantities: these are not rankings of
    nutrient amounts or statements of a dish's total nutrient content.
    """
    if recipe is not None:
        return select_important_benefits(recipe, notes, limit=limit)
    priority = ("protein", "dietary fiber", "iron", "calcium", "potassium", "vitamin c")
    selected = []
    seen_texts = set()
    for nutrient in priority:
        for note in notes:
            text = note.get("text", "").strip()
            if f"may contribute {nutrient}" in text.lower() and text.casefold() not in seen_texts:
                selected.append(note)
                seen_texts.add(text.casefold())
                break  # One ingredient example per nutrient type.
        if len(selected) >= limit:
            return selected
    for note in notes:
        text = note.get("text", "").strip()
        if text and text.casefold() not in seen_texts:
            selected.append(note)
            seen_texts.add(text.casefold())
        if len(selected) >= limit:
            break
    return selected


def _combined_background(history, culture):
    """Present the stored fields while preserving generated paragraph breaks.

    Keep the original Supabase fields intact for provenance. Strip formatting
    and repeated section labels when building a user-facing summary only.
    """
    parts = []
    for value in (history, culture):
        if not isinstance(value, str) or not value.strip():
            continue
        text = "\n\n".join(re.sub(r"\s+", " ", paragraph).strip()
                           for paragraph in re.split(r"\n\s*\n", value.replace("\r\n", "\n"))
                           if paragraph.strip())
        text = re.sub(r"^(?:historical background|cultural background|history|culture)\s*:\s*", "", text, flags=re.I)
        if text and (not parts or text.casefold() != parts[-1].casefold()):
            parts.append(text)
    return "\n\n".join(parts)


def _empty(recipe):
    return {
        "title": recipe.get("title", ""), "country": recipe.get("country", ""),
        "background": {"status": "unavailable", "historical_background": "", "cultural_background": "",
                       "combined_background": "",
                       "sources": [], "message": "Historical and cultural information is not available for this dish yet."},
        "health": {"status": "unavailable", "nutrition_status": "unavailable", "potential_benefits": [],
                   "health_considerations": [], "potential_allergens": [], "regional_allergens": [], "sources": [],
                   "message": "Verified ingredient information is not available yet.",
                   "nutrition_message": "Nutritional benefits are unavailable until ingredient matches are validated.",
                   "matched_ingredients": [], "unmatched_ingredients": [],
                   "allergen_message": "Ingredient names cannot establish that a dish is allergen-free. Check the full recipe, product labels and cross-contact risks."},
    }


def _public_background(background, recipe):
    """Project stored text without changing it or undoing field-level reviews."""
    from background_generation import DISCLAIMER
    from background_provenance import FIELDS, field_method, method_for_fields
    from academic_background_sources import _alias_match, _co_mentioned
    from dish_backgrounds import _aliases

    sources = _sources(background.get('sources'))
    methods = {field: field_method(background.get('generation_fields'), field,
                                   background.get('generation_method')) for field in FIELDS}
    methods = {field: value for field, value in methods.items() if value}
    review = (background.get('generation_fields') or {}).get('_source_review') or {}
    reviewed_sections = review.get('section_status') or {}
    # A URL alone cannot establish dish identity, section support or verification.
    hosts = {'unesco': {'ich.unesco.org'}, 'unesco_data': {'ich.unesco.org', 'data.unesco.org'},
             'crossref': {'doi.org'}, 'google_books': {'books.google.com'},
             'wikipedia': {'en.wikipedia.org'}}
    valid_evidence = []
    for source in background.get('sources') or []:
        if not isinstance(source, dict) or not safe_url(source.get('url')):
            continue
        kind = source.get('source_type')
        excerpt = str(source.get('excerpt') or '')
        alias = _alias_match(excerpt, _aliases(recipe))
        if (urlsplit(source['url']).hostname in hosts.get(kind, set()) and alias
                and _co_mentioned(excerpt, alias, country_key(recipe.get('country')))
                and source.get('matched_country') == country_key(recipe.get('country'))):
            valid_evidence.append(source)
    reviewed = bool(background.get('verification_status') == 'verified'
                    and background.get('reviewed_at') and str(background.get('review_note') or '').strip()
                    and valid_evidence)
    sections = {}
    for field in FIELDS:
        if not str(background.get(field) or '').strip():
            sections[field] = 'unavailable'
        elif (reviewed_sections.get(field) not in (None, 'supported', 'verified')
            or background.get('verification_status') not in {'pending', 'verified'}
              or methods.get(field) == 'gpt_fallback' or not sources):
            sections[field] = 'unverified'
        else:
            sections[field] = 'verified' if reviewed else ('supported' if reviewed_sections.get(field) == 'supported' else 'unverified')
    reviewed = reviewed and all(value == 'verified' for value in sections.values())
    status = 'verified' if reviewed else 'pending'
    if (background.get('verification_status') not in {'pending', 'verified'}
            or any(value not in {'supported', 'verified'} for value in reviewed_sections.values())
            or not sources):
        status = 'unverified'
    notes = '; '.join(('History' if field == 'historical_background' else 'Culture') + ': ' + sections[field]
                      for field in FIELDS) + '.'
    types = {s.get('source_type') for s in background.get('sources') or [] if isinstance(s, dict)}
    return dict(status=status, has_stored_description=True,
                historical_background=str(background.get('historical_background') or ''),
                cultural_background=str(background.get('cultural_background') or ''),
                combined_background=_combined_background(background.get('historical_background'), background.get('cultural_background')),
                sources=sources, wikipedia_fallback=bool(types) and types <= {'wikipedia', 'wikidata'},
                generation_method=method_for_fields(methods, background.get('generation_method')),
                generation_fields=methods, section_status=sections, evidence_message=notes,
                disclaimer_title='AI-Generated Information', disclaimer=DISCLAIMER,
                message='Previously reviewed information.' if reviewed else 'Stored AI-generated description; not independently verified.')


def _enrich_job(recipe, store, job_key):
    try:
        from dish_health import enrich_health
        from dish_backgrounds import enrich_background
        for component, enrich in (("background", lambda: enrich_background(recipe, store, client=_client)),
                                  ("health", lambda: enrich_health(recipe, store))):
            try:
                enrich()
            except Exception:
                log.warning("Dish %s enrichment could not finish; it can be resumed later.", component)
    finally:
        with _jobs_lock:
            _jobs.discard(job_key)


def _schedule(recipe, store):
    if os.environ.get("DISH_INFO_AUTO_ENRICH", "1").lower() not in {"1", "true", "yes"}:
        return
    key = (recipe.get("recipe_id"), ingredient_hash(recipe), normalize(recipe.get("title")),
           country_key(recipe.get("country")))
    with _jobs_lock:
        # Only track in-flight work in memory. All reusable data is stored in Supabase.
        if key in _jobs or len(_jobs) >= 8:
            return
        _jobs.add(key)
    try:
        _executor.submit(_enrich_job, dict(recipe), store, key)
    except RuntimeError:
        with _jobs_lock:
            _jobs.discard(key)


def public_information(recipe, avoid_allergens=(), schedule=True, store=None):
    """Display cached generated summaries immediately with provenance and disclaimers."""
    result = _empty(recipe)
    store = store or get_store()
    if store is None:
        result['background']['message'] = 'Historical and cultural information is temporarily unavailable because the database connection is unavailable.'
        return result
    try:
        background = store.get("dish_backgrounds", {
            "normalized_name": normalize(recipe.get("title")), "country": country_key(recipe.get("country"))})
    except DishStoreError:
        result['background']['message'] = 'Historical and cultural information is temporarily unavailable. Please try again later.'
        return result  # Do not hit providers if their results cannot be persisted.

    try:
        health = store.get("dish_health_info", {
            "recipe_id": recipe.get("recipe_id"), "ingredient_hash": ingredient_hash(recipe)}) if recipe.get("recipe_id") is not None else None
    except DishStoreError:
        health = None  # A nutrition outage must not hide an existing background.

    background_current = background and background.get("data_version") == DATA_VERSION
    if background_current and _client is not None:
        from background_generation import _stale_fields
        if _stale_fields(background):
            # Show the refreshed description in this response, as similarity
            # explanations already do. The generator keeps old text on failure.
            from dish_backgrounds import enrich_background
            try:
                background = enrich_background(recipe, store, client=_client)
            except Exception:
                log.warning("Prompt-change background refresh failed; keeping the saved description.")
    if (background_current
            and any(background.get(field) for field in ("historical_background", "cultural_background"))):
        result['background'].update(_public_background(background, recipe))

    # A partial USDA record is refreshed hourly, but the validated ingredient
    # notes already persisted in Supabase should not vanish while it refreshes.
    # Keep previously verified records visible for up to 30 days; this never
    # converts unknown allergens into a safety/absence claim.
    health_current = health_cache_valid(health)
    health_displayable = health_current or bool(
        health and health.get("verification_status") == "verified"
        and health.get("data_version") == DATA_VERSION
        and any(isinstance(n, dict) and n.get("type") == "coverage"
                and n.get("matcher_version") == HEALTH_ENRICHMENT_VERSION
                for n in health.get("health_considerations") or [])
        and fresh(health, max_age_days=30)
    )
    if health_displayable:
        coverage = next((note for note in health.get("health_considerations", [])
                         if isinstance(note, dict) and note.get("type") == "coverage"), {})
        unmatched = [{"ingredient": str(item.get("ingredient") or ""),
                      "reason": str(item.get("reason") or "No validated reference match")}
                     for item in coverage.get("unmatched_ingredients", []) if isinstance(item, dict)]
        matched, total = health.get("matched_ingredient_count", 0), health.get("ingredient_count", 0)
        result["health"].update(
            matched_ingredient_count=matched, ingredient_count=total,
            matched_ingredients=[str(name) for name in coverage.get("matched_ingredients", [])],
            unmatched_ingredients=unmatched,
            nutrition_message=(f"Validated nutritional matches: {matched} of {total} listed ingredients. "
                               + ("Still unmatched: " + ", ".join(item["ingredient"] for item in unmatched) + ". " if unmatched else "")
                               + "Benefits for unmatched ingredients are unavailable."))
    if health_displayable and health.get("verification_status") == "verified":
        allergens = []
        avoid = set(avoid_allergens)
        for item in health.get("potential_allergens") or []:
            if not isinstance(item, dict) or item.get("classification") not in {"identified", "possible", "assessed_none", "unknown"}:
                continue
            name = str(item.get("name") or "Unknown")
            allergens.append({"name": name, "classification": item["classification"],
                              "from": [str(x) for x in (item.get("from") or [])],
                              "source_url": safe_url(item.get("source_url")),
                              "avoid": name in avoid or user_allergen_of(name) in avoid})
        matched, total = health.get("matched_ingredient_count", 0), health.get("ingredient_count", 0)
        nutrition_status = health.get("nutrition_status", "unavailable")
        coverage = next((note for note in health.get("health_considerations", [])
                         if isinstance(note, dict) and note.get("type") == "coverage"), {})
        result["health"].update(
            status="verified", nutrition_status=nutrition_status,
            potential_benefits=_notes(health.get("potential_benefits")),
            key_benefits=_key_benefits(_notes(health.get("potential_benefits")), recipe=recipe),
            health_considerations=_notes(health.get("health_considerations")),
            potential_allergens=allergens, regional_allergens=regional_allergen_alerts(recipe),
            regional_markets=regional_market_catalog(),
            regional_allergen_disclaimer=REGIONAL_DISCLAIMER, sources=_sources(health.get("sources")),
            matched_ingredients=[str(name) for name in coverage.get("matched_ingredients", [])],
            unmatched_ingredients=[{"ingredient": str(item.get("ingredient") or ""),
                                    "reason": str(item.get("reason") or "No validated reference match")}
                                   for item in coverage.get("unmatched_ingredients", []) if isinstance(item, dict)],
            message="Source-backed ingredient notes; no dish totals or serving estimates.",
            nutrition_message=result["health"]["nutrition_message"])

    if health_displayable:
        matched, total = health.get("matched_ingredient_count", 0), health.get("ingredient_count", 0)
        fraction = matched / total if total else 0
        band = "complete" if total and matched == total else "high" if fraction >= .8 else "partial" if fraction >= .5 else "low" if matched else "unavailable"
        result["health"]["ingredient_coverage"] = {"band": band, "percent": round(100 * fraction, 2)}
        result["health"]["nutrition_message"] += f" Ingredient completeness: {band} ({fraction:.0%}). Availability of notes does not mean complete nutrition data."
        from curated_allergens import DISCLAIMER as ALLERGEN_DISCLAIMER
        result["health"]["allergen_message"] = ALLERGEN_DISCLAIMER
    if health_displayable:
        assessed = {name for group in result["health"].get("potential_allergens", []) if group["classification"] != "unknown" for name in group["from"]}
        total = health.get("ingredient_count", 0)
        unknown = sorted({name for group in result["health"].get("potential_allergens", []) if group["classification"] == "unknown" for name in group["from"]})
        percent = round(100 * len(assessed) / total, 2) if total else 0
        result["health"]["allergen_assessment"] = {"assessed_ingredients": len(assessed), "total_ingredients": total,
                                                  "percent": percent, "unknown_ingredients": unknown,
                                                  "framework": "UK FSA 14; FDA nine-group cross-reference"}
        result["health"]["allergen_message"] += f" Assessed ingredient names: {len(assessed)} of {health.get('ingredient_count', 0)}."
    result["background"]["verification"] = "generated_unverified" if result["background"]["status"] in {"pending", "unverified"} else "independently_verified" if result["background"]["status"] == "verified" else "unavailable"
    from background_generation import background_cache_valid
    background_valid = background_cache_valid(background)
    if schedule and (not background_valid or not health_current):
        _schedule(recipe, store)
    return result


def api_information(recipe, avoid_allergens=()):
    result = public_information(recipe, avoid_allergens)
    # Compatibility for the existing overview map, with no legacy GPT/cache calls.
    background = result["background"]
    result.update(culture=background["cultural_background"] or background["message"],
                  history=background["historical_background"] or background["message"],
                  source=background["status"])
    return result
