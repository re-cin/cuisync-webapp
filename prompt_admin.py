"""Admin-only prompt previews and version controls, independent of calculations."""
from copy import deepcopy
import secrets

from flask import abort, jsonify, request, session

import background_prompts
import cuisync_prompts
import explainer
from prompt_store import NAMES, PromptConflict, PromptUnavailable, defaults

DESCRIPTIONS = {
    "explanation": ("Similarity explanation", "Explains the supplied culinary evidence and Shapley contributions. Writing instructions never change the calculated scores or feature weights."),
    "background_source": ("Source-Based Generation", "Drafts history and culture from retrieved references. Unsupported fields stay empty."),
    "background_fallback": ("General-Knowledge Fallback", "Uses cautious general knowledge when sources cannot support a field. Descriptions remain AI-generated and unverified."),
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
                          rules=cuisync_prompts.PROTECTED_RULES if name == "explanation" else
                          background_prompts.protected_rules(name == "background_fallback"),
                          history=store.history(name)))
    legacy_pending = [name for name, value in store.legacy.items()
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
        return jsonify(error="Reload to obtain the active version before saving."), 409
    try:
        if action == "reset":
            text = defaults()[name]
        elif action == "restore":
            version = int(request.form.get("restore_version", "0"))
            previous = next((row for row in store.history(name) if row["version"] == version), None)
            if previous is None:
                raise ValueError("The requested previous version is unavailable.")
            text = previous["instructions"]
        else:
            text = request.form.get("prompt", "")
        administrator = str(user.get("id") or user.get("username") or "administrator")
        row = store.activate(name, text, administrator, expected)
        return jsonify(message=f"Version {row['version']} activated for future generations. Existing descriptions and references are preserved.",
                       version=row["version"], instructions=row["instructions"],
                       custom=row["instructions"] != defaults()[name],
                       administrator=row["administrator"], modified_at=row["modified_at"])
    except PromptConflict as exc:
        return jsonify(error=str(exc)), 409
    except PromptUnavailable as exc:
        return jsonify(error=str(exc)), 503
    except (ValueError, OSError) as exc:
        return jsonify(error=str(exc) if isinstance(exc, ValueError) else "Could not save instructions. Nothing was activated."), 400


def preview(recipes, background_store=None):
    name = checked_form()
    if explainer._client is None:
        return jsonify(error="GPT is unavailable. Configure the server's OpenAI client before testing a prompt."), 503
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
                           validation="Passed the existing vocabulary-grounding check. Review the wording against the supplied evidence.",
                           sample_note="Existing reference pair with fixed, precomputed score and contributions.")
        if not 0 <= index < len(recipes):
            raise ValueError("Select an existing sample dish.")
        result = preview_background(recipes[index], name, instructions, background_store)
        return jsonify(result)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        # Never return provider errors or credentials to the browser.
        return jsonify(error="GPT preview failed or source retrieval is temporarily unavailable. No prompt or production record was changed."), 502


def preview_background(recipe, name, instructions, store=None):
    import dish_backgrounds as b
    import background_generation as generation
    row = b._base_row(recipe)
    fallback = name == background_prompts.FALLBACK
    sources = []
    if not fallback:
        if store is not None:
            cached = store.get("dish_backgrounds", {"normalized_name": row["normalized_name"], "country": row["country"]})
            sources = b._cached_sources(cached, recipe)
        if not sources:
            sources = b.retrieve_evidence(recipe)
        if not sources:
            raise ValueError("No adequate sources were retrieved for this dish. Choose another dish or test General-Knowledge Fallback.")
    row["sources"] = sources
    row["fallback_fields" if fallback else "draft_fields"] = ["historical_background", "cultural_background"]
    response = b._draft_with_retry(explainer._client, row, instructions=instructions)
    fields = generation._parse(response, fallback=fallback)
    if not fallback and any(not generation._dates_grounded(text, sources) for text in fields.values()):
        raise ValueError("Preview rejected: a historical date was absent from the retrieved evidence.")
    if not any(fields.values()):
        raise ValueError("GPT returned no supported description for this sample.")
    return dict(**fields, sources_provided=bool(sources),
                sources=[{"title": s.get("title", "Source"), "url": s.get("url", ""),
                          "excerpt": s.get("excerpt", "")} for s in sources],
                validation="Passed the existing JSON, length and historical-date checks. These checks do not establish factual accuracy; review the supplied excerpts.",
                disclaimer=generation.DISCLAIMER)
