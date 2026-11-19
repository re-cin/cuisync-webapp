"""Admin-only prompt previews and version controls, independent of calculations."""
from copy import deepcopy
import secrets

from flask import abort, jsonify, request, session

import background_prompts
import cuisync_prompts
import explainer
from prompt_store import NAMES, PromptConflict, PromptUnavailable, defaults

DESCRIPTIONS = {
    "explanation": ("Similarity explanation", "Controls the words shown when two dishes are compared. It cannot change the similarity score."),
    "background_source": ("History and culture: with sources", "Controls how GPT writes history and culture when useful source excerpts are available."),
    "background_fallback": ("History and culture: without sources", "Controls cautious writing when useful sources are unavailable. This information is unverified."),
}


def samples():
    # These existing reference pairs already have scores and influence values.
    # The preview never calls SoftMatcher, compute_shapley or the production cache.
    return [deepcopy(item["evidence"]) for item in cuisync_prompts.FEW_SHOT_EXAMPLES]


def context(store, recipes):
    store.refresh()
    session.setdefault("prompt_csrf", secrets.token_urlsafe(32))
    cards = []
    for name in NAMES:
        active = store.active(name, refresh=False)
        title, description = DESCRIPTIONS[name]
        cards.append(dict(name=name, title=title, description=description, prompt=active["instructions"],
                          version=active["version"], custom=active["instructions"] != defaults()[name],
                          history=store.history(name)))
    legacy_pending = [DESCRIPTIONS[name][0] for name, value in store.legacy.items()
                      if value != store.active(name, refresh=False)["instructions"]]
    return dict(prompt_cards=cards, explanation_custom=cards[0]["custom"],
                cultural_custom=any(card["custom"] for card in cards[1:]),
                prompt_pairs=samples(), prompt_dishes=recipes, prompt_csrf=session["prompt_csrf"],
                prompt_storage_warning=store.degraded, prompt_remote=store.remote, legacy_pending=legacy_pending,
                gpt_on=explainer._client is not None)


def checked_form():
    token = session.get("prompt_csrf", "")
    if not token or not secrets.compare_digest(token, request.form.get("csrf_token", "")):
        abort(400, "Prompt form expired. Reload the page and try again.")
    name = request.form.get("which")
    if name not in NAMES:
        abort(400, "Unknown prompt")
    return name


def activate(store, user):
    name = checked_form()
    action = request.form.get("action")
    if action not in {"save", "reset", "restore"}:
        abort(400, "Unknown prompt action")
    try:
        expected = int(request.form["expected_version"])
        if expected < 1:
            raise ValueError()
    except (KeyError, ValueError):
        return jsonify(error="Reload the page to see the current instructions, then try again."), 409
    try:
        if action == "reset":
            text = defaults()[name]
        elif action == "restore":
            version = int(request.form.get("restore_version", "0"))
            previous = next((row for row in store.history(name) if row["version"] == version), None)
            if previous is None:
                raise ValueError("That past version is unavailable right now. Reload the page and try again.")
            text = previous["instructions"]
        else:
            text = request.form.get("prompt", "")
        administrator = str(user.get("id") or user.get("username") or "administrator")
        row = store.activate(name, text, administrator, expected)
        return jsonify(message=f"Saved as version {row['version']}. GPT will use these instructions now. Existing text updates when a dish is opened; if rewriting fails, the previous text stays visible.",
                       version=row["version"], instructions=row["instructions"],
                       custom=row["instructions"] != defaults()[name],
                       administrator=row["administrator"], modified_at=row["modified_at"])
    except PromptConflict as exc:
        return jsonify(error=str(exc)), 409
    except PromptUnavailable as exc:
        return jsonify(error=str(exc)), 503
    except (ValueError, OSError) as exc:
        return jsonify(error=str(exc) if isinstance(exc, ValueError) else "Your instructions could not be saved. Nothing changed."), 400


def preview(recipes, background_store=None):
    name = checked_form()
    if explainer._client is None:
        return jsonify(error="AI writing is unavailable right now. Please try again later or ask the site maintainer for help."), 503
    try:
        instructions = background_prompts.validate_prompt(request.form.get("prompt", ""))
        index = int(request.form.get("sample", "-1"))
        if name == "explanation":
            pair_samples = samples()
            if not 0 <= index < len(pair_samples):
                raise ValueError("Select an existing sample dish pair.")
            evidence = pair_samples[index]
            text = explainer.preview_explanation(evidence, instructions)
            return jsonify(explanation=text, similarity_score=evidence["overall_similarity"],
                           shapley_contributions=evidence["influence"], evidence=evidence,
                           validation="The explanation did not add unsupported food items. Please still check that it reads well and matches the dish details.",
                           sample_note="These sample dishes have a fixed score. Editing the prompt cannot change it.")
        if not 0 <= index < len(recipes):
            raise ValueError("Choose a dish from the search results.")
        selected = request.form.get("sample_search", "").strip()
        if selected:
            expected = f"{recipes[index].get('title', '')} ({recipes[index].get('country', '')})"
            if selected.casefold() != expected.casefold():
                raise ValueError("Choose a dish from the search results, not only a partial name.")
        result = preview_background(recipes[index], name, instructions, background_store)
        return jsonify(result)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        # Never return provider errors or credentials to the browser.
        return jsonify(error="AI preview could not be completed. No prompt or dish record was changed. Please try again."), 502


def preview_background(recipe, name, instructions, store=None):
    import dish_backgrounds as b
    import background_generation as generation
    row = b._base_row(recipe)
    fallback = name == background_prompts.FALLBACK
    sources = []
    source_status = "This preview does not use sources. General knowledge will be used."
    if not fallback:
        if store is not None:
            cached = store.get("dish_backgrounds", {"normalized_name": row["normalized_name"], "country": row["country"]})
            sources = b._cached_sources(cached, recipe)
        if not sources:
            try:
                sources = b.retrieve_evidence(recipe)
            except Exception:
                sources = []
        if not sources:
            # Match production behavior: no usable source means use the active
            # general-knowledge instructions, while keeping this preview read-only.
            fallback = True
            source_status = "No usable source excerpts were available. General knowledge was used."
        else:
            source_status = "Source excerpts were used to write this preview."
    row["sources"] = sources if not fallback else []
    requested = ["historical_background", "cultural_background"]
    fields = {field: "" for field in requested}
    if not fallback:
        try:
            row["draft_fields"] = requested
            fields.update(generation._parse(
                b._draft_with_retry(explainer._client, row, instructions=instructions)))
            if any(not generation._dates_grounded(fields[field], sources) for field in requested):
                fields = {field: "" for field in requested}
        except Exception:
            fields = {field: "" for field in requested}
        if any(not fields[field] for field in requested):
            source_status = "Source excerpts did not support a complete response. General knowledge was used for this preview."
        fallback = any(not fields[field] for field in requested)
    if fallback:
        row["sources"] = []
        row["fallback_fields"] = [field for field in requested if not fields[field]]
        try:
            fields.update(generation._parse(
                b._draft_with_retry(explainer._client, row, instructions=None), fallback=True))
        except Exception:
            pass
        # A partial model response must never leave one half of the combined
        # history-and-culture preview blank. Fill only missing fields with the
        # same cautious, link-free uncertainty text used in production.
        for field in requested:
            if not str(fields.get(field) or '').strip():
                fields[field] = generation.UNCERTAINTY_TEXT[field]
    if not any(fields.values()):
        raise ValueError("GPT returned no supported description for this sample.")
    return dict(fields, source_status=source_status,
                source_links=[{"title": s.get("title", "Source"), "url": s.get("url", "")}
                              for s in sources if s.get("url")])
