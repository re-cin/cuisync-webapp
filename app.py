import csv
from collections import Counter, defaultdict
import difflib
import io
import json
import os
import re
import secrets
import threading
import time
import unicodedata
from functools import lru_cache, wraps

import cv2
import numpy as np
from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
import ocr
import softmatch as soft_settings
from softmatch import SoftMatcher
from explainer import init as init_explainer, build_evidence, get_explanation
from shapley import compute_shapley, influence_shares
import accounts
import ner
import cultural_info
import cuisync_prompts
import explainer
import search_tools
from dataset_store import DatasetStore, DISH_FIELDS, REGION_CHOICES


app = Flask(__name__)
# Render/Cloudflare put a proxy in front of the app. ProxyFix makes Flask see the real https link.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# Max upload size (16 MB)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
# Pick up template changes without restarting the app
app.config["TEMPLATES_AUTO_RELOAD"] = True


# The data files are not on GitHub (see README step 4). Check for them first so a missing file
# gives a clear message instead of a crash.
_FEATURES = ("ingredients", "actions", "cookware", "utensils")
_missing = [
    name
    for name in ["recipes.json"] + [f"{feature}_{suffix}" for feature in _FEATURES
                                    for suffix in ("item_vecs.npy", "items.json")]
    if not os.path.exists(os.path.join(app.root_path, name))
]
if _missing:
    raise SystemExit(
        "\n[CuiSync] Missing data files next to app.py: " + ", ".join(_missing)
        + "\nDownload them from the Google Drive folder in the README (step 4) "
          "and put them in the same folder as app.py.\n"
    )

# Load the recipe dataset.
# The admin page can change the dataset, so it is kept in a DatasetStore (dataset_store.py).
# Until the admin changes something, the original recipes.json and .npy files are used as is.
STORE = DatasetStore(app.root_path)
RECIPES = STORE.recipes

# Make sure recipes.json is in the same order as the .npy files
ORDER_FILE = os.path.join(app.root_path, 'embeddings_order.json')
if os.path.exists(ORDER_FILE) and not STORE.uses_admin_data:
    with open(ORDER_FILE, encoding='utf-8') as f:
        _order = json.load(f)
    if _order != [[r.get("title", ""), r.get("country", "")] for r in RECIPES]:
        raise RuntimeError("recipes.json does not match the .npy files. Re-run the vectorization.")

def build_matcher():
    """Soft matcher for the current dataset (original files, or the admin's version)."""
    folder = STORE.write_matcher_files() if STORE.uses_admin_data else app.root_path
    return SoftMatcher(folder, RECIPES)


MATCHER = build_matcher()
INGREDIENT_SEARCH = search_tools.IngredientSearch(MATCHER)
# Ingredient NER for the admin dish form (ner.py: local model in ner_model/ or the NER Space)
ner.init(app.root_path, MATCHER.items["ingredients"], MATCHER.items, RECIPES)

# Gunicorn on Render skips __main__, so wake up the OCR Space here
if ocr.OCR_API_URL and __name__ != "__main__":
    threading.Thread(target=ocr.warm_up, daemon=True).start()
if len(MATCHER) != len(RECIPES):
    raise RuntimeError(
        f"recipes.json has {len(RECIPES)} recipes but the exported files describe "
        f"{len(MATCHER)}. Use the recipes.json that was exported together with them."
    )


# Feature weights (should add up to 1.0)
WEIGHTS = {
    "ingredients": 0.93,  
    "actions":     0.04,  
    "cookware":    0.02,  
    "utensils":    0.01  
}


# Lowest score we still show results for. Below this the low similarity popup shows.
SIMILARITY_THRESHOLD = 0.35


def no_result_page(error_desc, error_title="We Can't Find Your Dish"):
    """Go back to the search page and show the error popup."""
    return render_template("index.html", error_title=error_title, error_desc=error_desc)


def split_names(text):
    """Split a list of names like "A, B; C (x, y) / D". Commas inside brackets don't split.
    Same as splitNames() in index.html.
    """
    names, current, depth = [], [], 0
    for ch in str(text):
        if ch in "([":
            depth += 1
        elif ch in ")]" and depth > 0:
            depth -= 1
        if depth == 0 and ch in ",;|/\n\r":
            names.append("".join(current))
            current = []
        else:
            current.append(ch)
    names.append("".join(current))
    return [n.strip() for n in names if n.strip()]


def normalize_name(text):
    """Lowercase and clean up a dish name so names can be compared."""
    return " ".join(unicodedata.normalize("NFKC", str(text)).casefold().split())


def recipe_names(recipe):
    """All the names of a recipe: its title and its alternative titles."""
    names = {normalize_name(recipe.get("title", ""))}
    raw = recipe.get("alternative_title") or recipe.get("alternative_titles") or []
    if not isinstance(raw, (list, tuple, set)):
        raw = [raw]
    for item in raw:
        if isinstance(item, str):
            names.add(normalize_name(item))
            names.update(normalize_name(name) for name in split_names(item))
    names.discard("")
    return names



def parse_items(text_field):
    if not text_field:
        return []
    return [item.strip().title() for item in text_field.split(",") if item.strip()]


# Give the explainer the dataset (for typo fixes and grounding)
init_explainer(RECIPES, parse_items)


# ==========================================================================
#  PANEL REVISIONS: accounts, admin data, cultural info
# ==========================================================================

# Session key for logins. Set SECRET_KEY in .env on a server; locally one is made once and kept.
_key_file = os.path.join(STORE.data_dir, "secret_key")
if os.environ.get("SECRET_KEY"):
    app.secret_key = os.environ["SECRET_KEY"]
else:
    if not os.path.exists(_key_file):
        with open(_key_file, "w") as f:
            f.write(secrets.token_hex(32))
    with open(_key_file) as f:
        app.secret_key = f.read().strip()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

# Accounts live in Supabase (accounts.py, supabase_setup.sql)
accounts.init(STORE.data_dir)
# First admin account, made in Supabase when there is no admin yet. Change the password at /admin/account.
ADMIN_USERNAME = os.environ.get("CUISYNC_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("CUISYNC_ADMIN_PASSWORD", "cuisync-admin")
ADMIN_EMAIL = os.environ.get("CUISYNC_ADMIN_EMAIL", "admin@cuisync.app")
if accounts.ensure_admin(ADMIN_USERNAME, ADMIN_PASSWORD, ADMIN_EMAIL):
    print(f"[CuiSync] Admin account created in Supabase: {ADMIN_USERNAME} ({ADMIN_EMAIL}) / {ADMIN_PASSWORD} "
          "(change the password at /admin/account)")

# GPT prompts the admin saved
cuisync_prompts.set_system_prompt(STORE.get_prompt("explanation", ""))
cultural_info.init(
    STORE.data_dir,
    lambda: STORE.get_prompt("cultural", cultural_info.DEFAULT_PROMPT),
    explainer._client,
    explainer.MODEL_NAME,
)


def reload_data():
    """Rebuild everything that depends on the dataset after the admin changes it."""
    global RECIPES, MATCHER, INGREDIENT_SEARCH
    with STORE.lock:
        RECIPES = STORE.recipes
        MATCHER = build_matcher()
        INGREDIENT_SEARCH = search_tools.IngredientSearch(MATCHER)
        ner.init(app.root_path, MATCHER.items["ingredients"], MATCHER.items, RECIPES)
        feature_scores_for_source.cache_clear()
        init_explainer(RECIPES, parse_items)
    return STORE.missing_vectors


def current_user():
    if "user" not in g:
        uid = session.get("user_id")
        try:
            g.user = accounts.get_user(uid) if uid else None
        except accounts.SupabaseError as error:  # Supabase unreachable: carry on logged out
            app.logger.warning("Could not load the user: %s", error)
            g.user = None
            return g.user
        if g.user is not None and not g.user["is_active"]:  # disabled by an admin
            g.user = None
        if uid and g.user is None:
            session.pop("user_id", None)
    return g.user


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please log in first.", "info")
            return redirect(url_for("login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user["is_admin"]:  # admins have their own log in page
            if user:
                flash("That page is for admins. Sign in with an admin account.", "error")
            return redirect(url_for("admin_login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapper


app.jinja_env.filters["fromjson"] = lambda text: json.loads(text or "{}")


@app.before_request
def keep_admins_in_admin():
    """A logged-in admin only sees the admin pages: any page of the user site sends them to /admin."""
    path = request.path
    # /sw.js (old PWA clean-up) must load for admins too
    if path.startswith(("/admin", "/static/", "/logout")) or path in (
            "/login", "/favicon.ico", "/sw.js"):
        return None
    user = current_user()
    if user and user["is_admin"]:
        if path.startswith("/api/"):
            return jsonify({"error": "admin", "message": "Admins use the admin pages."}), 403
        return redirect(url_for("admin_page"))
    return None


@app.context_processor
def inject_globals():
    return {
        "current_user": current_user(),
        "countries": STORE.dropdown_countries(),
        "country_label": STORE.label_of,
        # Tag groups for the "Search by tag" tab (only built when the template asks)
        "home_tag_groups": lambda: search_tools.all_tags(RECIPES, STORE.region_of)[0],
        # Recent searches row on the home page (newest 8)
        "recent_searches": lambda: recent_entries(limit=8),
        # Quick picks for the Ingredient sub-tab of "Search by tag"
        "home_ingredient_quick": quick_ingredients,
        # Countries for the Region tab of "Search by tag": [(value, label, number of dishes)]
        "home_country_options": home_country_options,
    }


def home_country_options():
    counts = Counter(r["country"].lower() for r in RECIPES)
    return [(c["value"], c["label"], counts[c["value"]]) for c in STORE.dropdown_countries()]


# Main ingredients offered as quick picks (only the ones in the dataset, with dish counts)
QUICK_INGREDIENTS = ("chicken", "pork", "beef", "shrimp", "fish", "egg", "tofu", "rice",
                     "noodle", "coconut milk", "tomato", "soy sauce")


@lru_cache(maxsize=4)
def _quick_ingredient_counts(data_version):
    out = []
    for name in QUICK_INGREDIENTS:
        # Same matching as the search: the ingredient and its cuts/forms ("chicken thigh")
        items = set(INGREDIENT_SEARCH.resolve(name)) | set(INGREDIENT_SEARCH.family(name))
        n = sum(1 for ids in INGREDIENT_SEARCH.recipe_ids if items & set(ids.tolist()))
        if n:
            out.append((name, n))
    return out


def quick_ingredients():
    return _quick_ingredient_counts(id(INGREDIENT_SEARCH))


def save_history(**entry):
    user = current_user()
    if user:
        try:
            accounts.add_history(user["id"], **entry)
        except Exception as error:  # history must never break a search
            app.logger.warning("Could not save search history: %s", error)


def with_card_info(matches):
    """Add tags, ingredients and recipe_id to OCR matches, so the image search popup can
    show the same dish cards as the text search popup."""
    out = []
    for match in matches or []:
        key = (normalize_name(match.get("title", "")), normalize_name(match.get("country", "")))
        idx = next((i for i, r in enumerate(RECIPES)
                    if (normalize_name(r.get("title", "")), normalize_name(r.get("country", ""))) == key), None)
        out.append({**dish_card(idx), **match} if idx is not None else match)
    return out


def dish_card(idx, score=None, found=None):
    """Small dict for the Explore page cards."""
    r = RECIPES[idx]
    return {
        "idx": idx,
        "recipe_id": r.get("recipe_id"),
        "title": (r.get("title") or "").strip(),
        "alternative_title": (r.get("alternative_title") or "").strip(),
        "country": r["country"],
        "country_label": STORE.label_of(r["country"]),
        "country_value": r["country"].lower(),
        "region": r.get("country_region") or STORE.region_of(r["country"]),
        "image_link": r.get("image_link") or "",
        "tags": search_tools.recipe_tags(r, STORE.region_of),
        "score": None if score is None else round(score * 100),
        "found": found or [],
        "ingredients": parse_items(r.get("ingredient_text", ""))[:8],
    }


def format_recipe_url(link):
    """Add https:// to recipe links that don't have it."""
    if not link:
        return ""
    if link.startswith("http://") or link.startswith("https://"):
        return link
    return f"https://{link}"

@app.route("/")
@app.route("/home")
def home():
    return render_template("index.html")


# Search logic: find the closest dish in the target country
def low_similarity_page(message, score, dish_title, origin_country, target_country, recipe_id=None,
                        ingredients=""):
    """Back to the search page with the low similarity popup.
    "View results anyway" sends the same search again with force=1.
    """
    return render_template(
        "index.html",
        similarity_warning=message,
        similarity_score=round(score),
        warn_dish=dish_title,
        warn_origin=origin_country,
        warn_target=target_country,
        warn_recipe_id="" if recipe_id is None else recipe_id,
        warn_ingredients=ingredients or "",
    )


def blend_scores(per_feature):
    """Combine the four feature scores using the weights. NaN = empty in both dishes, so it's skipped."""
    weighted_sum = np.zeros(len(RECIPES))
    weight_used = np.zeros(len(RECIPES))
    for feature_name, weight in WEIGHTS.items():
        feature_scores = per_feature[feature_name]
        counted = ~np.isnan(feature_scores)
        weighted_sum += weight * np.where(counted, feature_scores, 0.0)
        weight_used += weight * counted
    return np.divide(weighted_sum, weight_used, out=np.zeros(len(RECIPES)), where=weight_used > 0)


def shapley_for(per_feature, idx):
    """Shapley values, influence shares and feature scores for one pair of dishes."""
    raw_sims = {name: float(per_feature[name][idx]) for name in WEIGHTS}
    used = {name: not np.isnan(value) for name, value in raw_sims.items()}
    used_weight = sum(WEIGHTS[name] for name in WEIGHTS if used[name]) or 1.0
    effective_weights = {name: (WEIGHTS[name] / used_weight if used[name] else 0.0) for name in WEIGHTS}
    clean_sims = {name: (value if used[name] else 0.0) for name, value in raw_sims.items()}
    shapley = compute_shapley(clean_sims, effective_weights)
    return shapley, influence_shares(shapley), clean_sims


@lru_cache(maxsize=32)
def feature_scores_for_source(source_idx):
    """Scores of every recipe against one dish (cached so map pin clicks are faster)."""
    return {name: MATCHER.scores(name, source_idx) for name in WEIGHTS}


ANY_COUNTRY = "any"


def ingredient_filter(ingredients, target_country, exclude_idx):
    """Ingredient search with a specific target country: only the target country's dishes
    that use the ingredients can be recommended. Dishes that use ALL the ingredients are kept;
    if none do, the ones that use the most of them.
    Returns (allowed indices, ingredient names that were understood).
    """
    wanted = [w for w in split_names(ingredients) if w]
    results, unknown = INGREDIENT_SEARCH.search(RECIPES, wanted, target_country, limit=len(RECIPES))
    results = [r for r in results if r[0] != exclude_idx]
    known = [w for w in wanted if w not in unknown]
    if not results:
        return [], known
    most = max(len(found) for _, _, found in results)
    return [idx for idx, _, found in results if len(found) == most], known


def get_search_results(dish_name, origin_country, target_country, force=False, recipe_id=None,
                       search_type="text", ingredients="", pick=None, show_overview=False):
    """One search, two outcomes:
      - pick given -> the comparison page (results.html) for that dish
      - no pick (a search form was sent, or an old /similar-dishes link) -> check the search,
        save it to the history (new searches only) and redirect to the comparison page with
        the best match, so the browser's back button works without "Confirm form resubmission".
    """
    requested_target = (target_country or "").strip()
    # "Any" target country (panel revision): look in every country except the dish's own.
    # No target at all (the tag search has none) also means "Any".
    if not (target_country or "").strip():
        target_country = ANY_COUNTRY
    target_any = normalize_name(target_country) == ANY_COUNTRY


    # Find the searched dish (same name and origin country)
    source_dish = None
    source_idx = None
    # Some recipes share a name (like the many Banh Mi), so use recipe_id if we got one
    def is_match(recipe):
        return (
            normalize_name(dish_name) in recipe_names(recipe)
            and normalize_name(recipe["country"]) == normalize_name(origin_country)
        )

    if recipe_id not in (None, ""):
        for index, recipe in enumerate(RECIPES):
            if str(recipe.get("recipe_id")) == str(recipe_id).strip() and is_match(recipe):
                source_dish = recipe.copy()
                source_idx = index
                break

    if source_dish is None:
        for index, recipe in enumerate(RECIPES):
            if is_match(recipe):
                source_dish = recipe.copy()
                source_idx = index
                break

    if source_dish is None:
        same_title = sorted({r["country"] for r in RECIPES if normalize_name(dish_name) in recipe_names(r)})
        close = difflib.get_close_matches(dish_name.lower(), [r["title"].lower() for r in RECIPES], n=5, cutoff=0.6)
        if same_title:
            return no_result_page(
                f'"{dish_name}" is in our dataset, but under {", ".join(c.title() for c in same_title)} '
                f"rather than {origin_country.title()}."
            )
        return no_result_page(
            f'"{dish_name}" from {origin_country.title()} is not in our dataset, '
            "so there is nothing to compare."
        )


    if not source_dish.get("region"):
        source_dish["region"] = "No Specific"
    source_dish["recipe_link"] = format_recipe_url(source_dish.get("recipe_link", ""))
    source_dish["idx"] = source_idx

    recommended_dish = None
    similarity_score = 0.0
    regional_variations = []
    international_similarities = []

    # Score every recipe against the searched dish
    per_feature = feature_scores_for_source(source_idx)
    total_scores = blend_scores(per_feature)

    # Don't let the dish match itself (happens when origin and target are the same country)
    if target_any:
        target_indices = [
            index
            for index, recipe in enumerate(RECIPES)
            if recipe["country"].lower() != source_dish["country"].lower()
        ]
    else:
        target_indices = [
            index
            for index, recipe in enumerate(RECIPES)
            if recipe["country"].lower() == target_country.lower() and index != source_idx
        ]
    if not target_indices and target_any:
        return no_result_page("Our dataset has no recipes from other countries to compare with.")

    # Ingredient search with a specific target country (panel revision)
    ingredient_note = ""
    ingredients = (ingredients or "").strip()
    if ingredients and not target_any and target_indices:
        allowed, known = ingredient_filter(ingredients, target_country, source_idx)
        target_indices = [i for i in target_indices if i in set(allowed)]
        if not target_indices:
            return no_result_page(
                f"No dish from {STORE.label_of(target_country.upper())} in our dataset uses "
                f"{', '.join(known) or ingredients}, so there is nothing to compare with. "
                'Try other ingredients or choose "Any country".',
                error_title="No Matching Dishes",
            )
        ingredient_note = (f"Only {STORE.label_of(target_country.upper())} dishes that use "
                           f"{', '.join(known)} were compared ({len(target_indices)} "
                           f"dish{'es' if len(target_indices) != 1 else ''}).")
    if not target_indices:
        if origin_country.lower() == target_country.lower():
            return no_result_page(
                f'Our dataset has no other recipes from {target_country.title()} '
                f'to compare with "{source_dish["title"]}".'
            )
        return no_result_page(
            f"Our dataset has no recipes from {target_country.title()}."
        )

    ordered_targets = sorted(
        target_indices, key=lambda index: total_scores[index], reverse=True
    )

    # The dish to compare with (from a map pin, the top 10 list or a recent search).
    # Falls back to the best match if it isn't in the target country.
    picked = None
    if pick not in (None, ""):
        try:
            picked = int(pick)
        except (TypeError, ValueError):
            picked = None
        if picked is not None and picked not in set(target_indices):
            picked = None
    best_index = picked if picked is not None else ordered_targets[0]

    # Score too low: go back and ask the user first instead of loading the results
    if total_scores[best_index] < SIMILARITY_THRESHOLD and not force and picked is None:
        return low_similarity_page(
            f'No dish from {"any other country" if target_any else target_country.title()} in our dataset is similar enough to '
            f'"{source_dish["title"]}" to recommend (closest match is only '
            f"{total_scores[best_index] * 100:.0f}% similar).",
            float(total_scores[best_index]) * 100,
            source_dish["title"],
            source_dish["country"],
            ANY_COUNTRY if target_any else target_country,
            source_dish.get("recipe_id"),
            ingredients,
        )

    # Links to other comparisons of the same search (map pins, top 10 list)
    link_params = {
        "dish_name": source_dish["title"].strip(),
        "origin_country": source_dish["country"].lower(),
        "target_country": ANY_COUNTRY if target_any else requested_target.lower(),
        "recipe_id": source_dish.get("recipe_id") or "",
        "type": search_type,
    }
    if ingredients:
        link_params["ingredients"] = ingredients

    if picked is None and not show_overview and pick in (None, ""):
        best = RECIPES[best_index]
        save_history(
            search_type=search_type,
            dish_name=source_dish["title"].strip(),
            origin_country=source_dish["country"],
            target_country=ANY_COUNTRY if target_any else target_country,
            recipe_id=source_dish.get("recipe_id"),
            result_title=best["title"].strip(),
            result_country=best["country"],
            score=round(float(total_scores[best_index]) * 100, 2),
            query=json.dumps({"ingredients": ingredients}) if ingredients else "",
        )
    if picked is None:
        # Straight to the comparison with the best match
        return redirect(url_for("compare", pick=int(best_index), **link_params))

    # Interactive map at the bottom of the page (made before target_country changes below)
    map_data = results_map(source_idx, best_index, ordered_targets, total_scores,
                           target_any, target_country, link_params)

    if target_any:
        # From here on the target is the country of the picked dish
        target_country = RECIPES[best_index]["country"]

    similarity_score = round(float(total_scores[best_index]) * 100, 2)
    # Recent searches: every comparison that is opened
    record_recent(source_idx, best_index, similarity_score, link_params)

    # Shapley values for the GPT explanation. Empty categories are skipped so they still add up to the score.
    shapley, influence, clean_sims = shapley_for(per_feature, best_index)
    feature_scores = {name: round(value, 4) for name, value in clean_sims.items()}
    if abs(sum(shapley.values()) * 100 - similarity_score) > 0.01:
        app.logger.warning("Shapley values do not add up to the similarity score")
    recommended_dish = RECIPES[best_index].copy()
    recommended_dish["idx"] = int(best_index)
    if not recommended_dish.get("region"):
        recommended_dish["region"] = "No Specific"
    recommended_dish["recipe_link"] = format_recipe_url(
        recommended_dish.get("recipe_link", "")
    )

    # Where the picked dish is on the results list (shown on the comparison page)
    dish_rank = ordered_targets.index(best_index) + 1
    rank_scope = "all other countries" if target_any else STORE.label_of(target_country.upper())

    for idx in [i for i in ordered_targets if i != best_index][:3]:
        dish = RECIPES[idx].copy()
        dish["idx"] = int(idx)
        dish["score"] = round(float(total_scores[idx]) * 100, 2)
        dish["region"] = dish.get("region") or "No Specific"
        dish["recipe_link"] = format_recipe_url(dish.get("recipe_link", ""))
        regional_variations.append(dish)

    seen_countries = {target_country.lower(), origin_country.lower(), source_dish["country"].lower()}
    for index in np.argsort(total_scores)[::-1]:
        recipe = RECIPES[index]
        recipe_country = recipe["country"].lower()
        if recipe_country in seen_countries:
            continue
        seen_countries.add(recipe_country)
        dish = recipe.copy()
        dish["idx"] = int(index)
        dish["score"] = round(float(total_scores[index]) * 100, 2)
        dish["region"] = dish.get("region") or "No Specific"
        dish["recipe_link"] = format_recipe_url(dish.get("recipe_link", ""))
        international_similarities.append(dish)
        if len(international_similarities) == 5:
            break


    # Compare the entities of the two dishes
    source_ingredients = set(parse_items(source_dish.get("ingredient_text", "")))
    recommended_ingredients = (
        set(parse_items(recommended_dish.get("ingredient_text", "")))
        if recommended_dish
        else set()
    )
    source_actions = set(parse_items(source_dish.get("action_text", "")))
    recommended_actions = (
        set(parse_items(recommended_dish.get("action_text", "")))
        if recommended_dish
        else set()
    )
    source_cookware = set(parse_items(source_dish.get("cookware_text", "")))
    recommended_cookware = (
        set(parse_items(recommended_dish.get("cookware_text", "")))
        if recommended_dish
        else set()
    )
    source_utensils = set(parse_items(source_dish.get("utensil_text", "")))
    recommended_utensils = (
        set(parse_items(recommended_dish.get("utensil_text", "")))
        if recommended_dish
        else set()
    )

    entities = {
        "ingredients": {
            "shared": sorted(source_ingredients & recommended_ingredients),
            "source_unique": sorted(source_ingredients - recommended_ingredients),
            "target_unique": sorted(recommended_ingredients - source_ingredients),
        },
        "actions": {
            "shared": sorted(source_actions & recommended_actions),
            "source_unique": sorted(source_actions - recommended_actions),
            "target_unique": sorted(recommended_actions - source_actions),
        },
        "cookware": {
            "shared": sorted(source_cookware & recommended_cookware),
            "source_unique": sorted(source_cookware - recommended_cookware),
            "target_unique": sorted(recommended_cookware - source_cookware),
        },
        "utensils": {
            "shared": sorted(source_utensils & recommended_utensils),
            "source_unique": sorted(source_utensils - recommended_utensils),
            "target_unique": sorted(recommended_utensils - source_utensils),
        },
    }

    # GPT explanation (cached, uses the template if GPT fails)
    explanation = None
    explanation_source = None
    if best_index != source_idx:
        evidence = build_evidence(source_dish, recommended_dish, similarity_score, influence)
        explanation, explanation_source = get_explanation(evidence)

    # Weights in percent for the progress bars
    feature_weights = {
        key: round(value * 100) for key, value in WEIGHTS.items()
    }

    # (The search was already saved to the history when it was made, see above.)

    user = current_user()
    return render_template(
        "results.html",
        source_dish=source_dish,
        recommended_dish=recommended_dish,
        target_country=target_country,
        target_any=target_any,
        ingredient_note=ingredient_note,
        avoid_allergens=(user or {}).get("avoid_allergens", []),
        entities=entities,
        similarity_score=similarity_score,
        feature_weights=feature_weights,
        regional_variations=regional_variations,
        international_similarities=international_similarities,
        explanation=explanation,
        explanation_source=explanation_source,
        feature_scores=feature_scores,
        shap_values=shapley,
        influence=influence or {},
        map_data=map_data,
        dish_rank=dish_rank,
        rank_total=len(ordered_targets),
        rank_scope=rank_scope,
        # Clickable tags on the two dish cards (they open the Dish Dictionary)
        source_tags=search_tools.recipe_tags(RECIPES[source_idx], STORE.region_of),
        target_tags=search_tools.recipe_tags(RECIPES[best_index], STORE.region_of),
        # Cultural & History tab: connection between the two countries (template for now)
        connection=cultural_info.country_connection(
            RECIPES, source_dish["country"],
            None if recommended_dish["country"].upper() == source_dish["country"].upper()
            else recommended_dish["country"].upper(),
            STORE.label_of, STORE.region_of, parse_items, dish_title=source_dish["title"].strip(),
        ),
    )


# ---------------- Recent searches (home page row + "All recent searches" page) ----------------
# Every dish-vs-dish comparison someone opens is kept: logged-in users in the accounts
# database, guests in their browser session (cookie). Entries older than 30 days are not shown.
RECENT_DAYS = 30
GUEST_RECENT_MAX = 20     # the session cookie is small, so guests keep fewer


def record_recent(source_idx, target_idx, score, link_params):
    entry = {
        "s": int(source_idx), "st": (RECIPES[source_idx].get("title") or "").strip(),
        "t": int(target_idx), "tt": (RECIPES[target_idx].get("title") or "").strip(),
        "sc": int(round(score)),
        "tg": link_params.get("target_country", ANY_COUNTRY),
        "ty": link_params.get("type", "text"),
        "ing": link_params.get("ingredients", ""),
        "ts": int(time.time()),
    }
    key = f"{entry['st']}|{entry['tt']}|{entry['tg']}|{entry['ing']}".lower()
    user = current_user()
    try:
        if user:
            accounts.add_recent(user["id"], key, entry)
        else:
            entry["k"] = key
            items = [e for e in session.get("recent", []) if e.get("k") != key]
            session["recent"] = [entry] + items[:GUEST_RECENT_MAX - 1]
    except Exception as error:   # recent searches must never break a comparison
        app.logger.warning("Could not save recent search: %s", error)


def _find_recipe(idx, title):
    """The recipe a saved entry points to (the admin can add/remove dishes, so check the title)."""
    if 0 <= idx < len(RECIPES) and normalize_name(RECIPES[idx].get("title", "")) == normalize_name(title):
        return idx
    key = normalize_name(title)
    return next((i for i, r in enumerate(RECIPES) if normalize_name(r.get("title", "")) == key), None)


def recent_entries(limit=None):
    """Cards for the recent searches, newest first."""
    user = current_user()
    raw = accounts.list_recent(user["id"]) if user else session.get("recent", [])
    cutoff = time.time() - RECENT_DAYS * 86400
    cards = []
    for e in raw:
        if e.get("ts", 0) < cutoff:
            continue
        s_idx, t_idx = _find_recipe(e.get("s", -1), e.get("st", "")), _find_recipe(e.get("t", -1), e.get("tt", ""))
        if s_idx is None or t_idx is None:
            continue
        src, tgt = RECIPES[s_idx], RECIPES[t_idx]
        params = {
            "dish_name": (src.get("title") or "").strip(),
            "origin_country": src["country"].lower(),
            "target_country": e.get("tg", ANY_COUNTRY),
            "recipe_id": src.get("recipe_id") or "",
            "type": e.get("ty", "text"),
            "pick": t_idx,
        }
        if e.get("ing"):
            params["ingredients"] = e["ing"]
        cards.append({
            "source": {"title": params["dish_name"], "country": src["country"].lower(),
                       "country_label": STORE.label_of(src["country"]), "image": src.get("image_link") or ""},
            "target": {"title": (tgt.get("title") or "").strip(), "country": tgt["country"].lower(),
                       "country_label": STORE.label_of(tgt["country"]), "image": tgt.get("image_link") or ""},
            "score": e.get("sc", 0),
            "ts": e.get("ts", 0),
            "url": url_for("compare", **params),
        })
        if limit and len(cards) >= limit:
            break
    return cards


@app.route("/recent")
def recent_searches():
    """All recent searches (the "See all" link of the home page row)."""
    cards = recent_entries()
    origins = sorted({(c["source"]["country"], c["source"]["country_label"]) for c in cards},
                     key=lambda c: c[1])
    return render_template("recent.html", cards=cards, origins=origins, keep_days=RECENT_DAYS)


@app.route("/recent/clear", methods=["POST"])
def clear_recent_searches():
    user = current_user()
    if user:
        accounts.clear_recent(user["id"])
    else:
        session.pop("recent", None)
    flash("Your recent searches were cleared.", "success")
    return redirect(url_for("recent_searches"))


# ---------------- Interactive map on the comparison page ----------------
MAP_TARGET_PINS = 5     # with a target country: its 5 most similar dishes...
MAP_OTHER_PINS = 15     # ...and the 15 most similar from every other country
MAP_ANY_PINS = 20       # with "Any country": the 20 most similar from any country
MAP_LIST_SIZE = 10      # the side panel lists the top 10 of the pins


def results_map(source_idx, current_idx, ordered_targets, total_scores, target_any, target_country,
                link_params):
    """Pins for the map: the searched dish plus the most similar dishes (see the limits above),
    ranked by similarity. Each pin links to its own comparison page."""
    origin = RECIPES[source_idx]["country"].lower()
    target = None if target_any else target_country.lower()
    if target_any:
        chosen = list(ordered_targets[:MAP_ANY_PINS])
    else:
        others = [int(i) for i in np.argsort(total_scores)[::-1]
                  if int(i) != source_idx and RECIPES[int(i)]["country"].lower() not in (origin, target)]
        chosen = list(ordered_targets[:MAP_TARGET_PINS]) + others[:MAP_OTHER_PINS]
    chosen.sort(key=lambda i: total_scores[i], reverse=True)

    def pin(idx, rank):
        card = dish_card(int(idx), float(total_scores[idx]))
        country = card["country_value"]
        in_target = target_any or country == target
        # A dish from another country is compared with that country as the target
        params = dict(link_params) if in_target else {
            **{k: v for k, v in link_params.items() if k != "ingredients"}, "target_country": country}
        return {
            "idx": int(idx), "rank": rank, "score": card["score"],
            "title": card["title"], "country": card["country"], "label": card["country_label"],
            "image": card["image_link"], "url": url_for("compare", pick=int(idx), **params),
            "current": int(idx) == int(current_idx), "inTarget": bool(in_target and not target_any),
        }

    dishes = [pin(idx, rank) for rank, idx in enumerate(chosen, 1)]
    if int(current_idx) not in set(chosen):
        dishes.append(pin(current_idx, None))   # the dish on this page, even if it's further down

    countries = {}
    for d in dishes:
        entry = countries.setdefault(d["country"], {
            "country": d["country"], "label": d["label"], "count": 0, "best": d["title"], "score": d["score"]})
        entry["count"] += 1
    source = dish_card(source_idx)
    return {
        "origin": RECIPES[source_idx]["country"],
        "target": None if target_any else STORE.label_of(target_country.upper()),
        "countries": list(countries.values()),
        "source": {"idx": source_idx, "title": source["title"], "country": source["country"],
                   "label": source["country_label"], "image": source["image_link"]},
        "dishes": dishes,
        "listSize": MAP_LIST_SIZE,
    }


def _search_args():
    """The search fields of the compare links."""
    a = request.args
    return dict(
        dish_name=a.get("dish_name", "").strip(),
        origin_country=a.get("origin_country", "").strip(),
        target_country=a.get("target_country", "").strip(),
        recipe_id=a.get("recipe_id", "").strip() or None,
        search_type=a.get("type", "text") if a.get("type") in ("text", "image") else "text",
        ingredients=a.get("ingredients", ""),
    )


@app.route("/similar-dishes")
def similar_dishes():
    """Old overview links (the overview page was removed): open the best match's comparison."""
    return get_search_results(force=True, show_overview=True, **_search_args())


@app.route("/compare")
def compare():
    """Comparison of the searched dish with one similar dish (pick = its index)."""
    return get_search_results(force=True, pick=request.args.get("pick"), **_search_args())


@app.errorhandler(413)
def file_too_large(_error):
    """Upload is bigger than 16 MB."""
    message = "This photo is too large. Please upload an image under 16 MB."
    if request.path == "/extract-dish-info":
        return jsonify({"success": False, "error": "too_large", "message": message, "matches": []}), 413
    return render_template("index.html", error_title="Image Too Large", error_desc=message), 413


@app.route("/recipes.json")
def recipes_json():
    return jsonify(RECIPES)


@app.route("/search-text", methods=["POST"])
def search_text():
    return get_search_results(
        request.form.get("dish_name", "").strip(),
        request.form.get("origin_country", "").strip(),
        request.form.get("target_country", "").strip(),
        force=request.form.get("force") == "1",
        recipe_id=request.form.get("recipe_id", "").strip() or None,
        # Ingredients come from the Ingredient sub-tab of "Search by tag" (or an older ingredient search)
        ingredients=request.form.get("ingredients", "") if request.form.get("search_mode") in ("ingredients", "tags") else "",
    )


@app.route("/extract-dish-info", methods=["POST"])
def extract_dish_info():
    if "dish_image" not in request.files:
        return jsonify({"success": False, "message": "No image uploaded"})

    try:
        raw = request.files["dish_image"].read()
        image_bytes = np.frombuffer(raw, np.uint8)
        image = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)
        if image is None:
            return jsonify({"success": False, "message": "Invalid image format"})

        try:
            lines = ocr.automatic_ocr(image)
        except ocr.UnsupportedScriptError as error:
            # Khmer/Burmese or unreadable text. A dish might still match (like an English line), so try first.
            lines = error.lines
            if not ocr.find_recipes(ocr.romanize_lines(lines), recipes=RECIPES):
                return jsonify({
                    "success": False,
                    "error": "unsupported_script",
                    "message": str(error),
                    "raw_text": "\n".join(lines),
                    "romanized_text": "",
                    "matches": [],
                })

        if not lines:
            return jsonify({
                "success": False,
                "error": "no_text",
                "message": "We couldn't find any text in this photo or just try it again. Make sure the menu is in focus and the dish names are visible.",
                "raw_text": "(No readable text detected)",
                "romanized_text": "(None)",
                "matches": [],
            })

        romanized = ocr.romanize_lines(lines)
        matches = with_card_info(ocr.find_recipes(romanized, recipes=RECIPES))
        return jsonify({
            "success": bool(matches),
            "message": "" if matches else "No matching recipe found in dataset.",
            "raw_text": "\n".join(lines),
            "romanized_text": "\n".join(romanized),
            "matches": matches,
        })
    except Exception as error:
        return jsonify({"success": False, "message": f"Processing error: {error}"})


@app.route("/search-image", methods=["POST"])
def search_image():
    origin_country = request.form.get("origin_country", "").strip()
    target_country = request.form.get("target_country", "").strip()
    extracted_dish_name = request.form.get("extracted_dish_name", "").strip()

    try:
        # Dish already picked, so we don't need the photo again (the browser clears it after going back)
        if extracted_dish_name and origin_country:
            return get_search_results(extracted_dish_name, origin_country, target_country,
                                      search_type="image")

        if "dish_image" not in request.files:
            return render_template("index.html", error_title="No Recipe Record", error_desc="No image file was uploaded.")

        file = request.files["dish_image"]
        if not file.filename:
            return render_template("index.html", error_title="No Recipe Record", error_desc="No selected image file.")

        image_bytes = np.frombuffer(file.read(), np.uint8)
        image = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)
        if image is None:
            return render_template("index.html", error_title="No Recipe Record", error_desc="Image format is not supported or corrupted.")

        try:
            lines = ocr.automatic_ocr(image)
            unsupported_error = None
        except ocr.UnsupportedScriptError as error:
            lines = error.lines
            unsupported_error = error

        matches = ocr.find_recipes(ocr.romanize_lines(lines), recipes=RECIPES)
        if not matches and unsupported_error:
            return render_template("index.html", error_title="Language Not Supported", error_desc=str(unsupported_error))
        if not matches:
            return render_template("index.html", error_title="No Recipe Record", error_desc="The uploaded image does not contain a recipe name found in our dataset.")
        recipe = matches[0]
        return get_search_results(recipe["title"], recipe["country"], target_country,
                                  recipe_id=recipe.get("recipe_id"), search_type="image")
    except Exception as error:
        return render_template("index.html", error_title="Processing Error", error_desc=f"An error occurred while processing the image: {error}")


@app.route("/analysis")
def analysis():
    return render_template("analysis.html")

# --- ANALYSIS PAGE API ---

COUNTRY_REGIONS = {
    "CHINA": "East Asia",
    "JAPAN": "East Asia",
    "SOUTH KOREA": "East Asia",

    "CAMBODIA": "Southeast Asia",
    "INDONESIA": "Southeast Asia",
    "MALAYSIA": "Southeast Asia",
    "MYANMAR": "Southeast Asia",
    "PHILIPPINES": "Southeast Asia",
    "THAILAND": "Southeast Asia",
    "VIETNAM": "Southeast Asia",

    "INDIA": "South Asia",

    "SAUDI ARABIA": "West Asia",
    "TURKIYE": "West Asia"
}

# Analysis page 1: normalize country names and group them by region
def normalize_analysis_country(country):
    if not country:
        return ""

    return " ".join(
        str(country).strip().upper().split()
    )


def get_analysis_region(country):
    # Regions come from the admin's country list (falls back to the table above)
    return STORE.region_of(normalize_analysis_country(country)) or COUNTRY_REGIONS.get(
        normalize_analysis_country(country), "Other Asia"
    )

def get_analysis_countries():
    regions = {}

    for recipe in RECIPES:
        country = normalize_analysis_country(
            recipe.get("country")
        )

        if not country:
            continue

        region = get_analysis_region(country)

        if region not in regions:
            regions[region] = set()

        regions[region].add(country)

    return {
        region: sorted(countries)
        for region, countries in sorted(regions.items())
    }

# Analysis page 2: recipe count per country

def get_analysis_country_counts():
    counts = {}

    for recipe in RECIPES:
        country = normalize_analysis_country(
            recipe.get("country")
        )

        if not country:
            continue

        counts[country] = counts.get(country, 0) + 1

    return counts

# Analysis page 3: recipe count per region
def get_analysis_region_counts():
    counts = {}

    for recipe in RECIPES:
        country = normalize_analysis_country (recipe.get("country"))

        if not country:
            continue

        region = get_analysis_region(country)
        counts[region] = counts.get(region,0) + 1

    return counts

# Analysis page 4: count each entity, most common first
def get_analysis_top_entities(recipes, field, limit=15):
    total_recipes = len(recipes)

    if total_recipes == 0:
        return []

    entity_recipe_counts = {}

    for recipe in recipes:
        value = recipe.get(field, "")

        if not value:
            continue

        # Count each entity once per recipe
        items = set(parse_items(value))

        for item in items:
            if item:
                entity_recipe_counts[item] = (
                    entity_recipe_counts.get(item, 0) + 1
                )

    results = []

    for entity, count in entity_recipe_counts.items():
        percentage = (count / total_recipes) * 100

        results.append({
            "name": entity,
            "count": count,
            "percentage": round(percentage, 2)
        })

    results.sort(
        key=lambda x: x["count"],
        reverse=True
    )

    return results[:limit]

# Analysis page 5: analysis for the countries the user picked
def get_analysis_recipes_for_countries(countries):
    # No countries picked = use all of them
    if not countries:
        return RECIPES

    selected_countries = {
        normalize_analysis_country(country)
        for country in countries
        if normalize_analysis_country(country)
    }

    if not selected_countries:
        return RECIPES

    return [
        recipe
        for recipe in RECIPES
        if normalize_analysis_country(
            recipe.get("country")
        ) in selected_countries
    ]

# Analysis page 6: overview of the recipe counts
@app.route("/api/analysis/overview")
def analysis_overview():
    return jsonify({
        "countries_by_region":  # countries grouped by region
            get_analysis_countries(), 

        "country_counts":
            get_analysis_country_counts(),  # recipe count per country

        "region_counts":
            get_analysis_region_counts(),  # recipe count per region

        "total_recipes":  # total recipes in the dataset
            len(RECIPES)
    })

# Analysis page 7: top ingredients of the picked countries
@app.route("/api/analysis/ingredients")
def analysis_ingredients():
    countries = request.args.getlist("country")

    selected_recipes = (
        get_analysis_recipes_for_countries(countries)
    )

    return jsonify({
        "countries": countries,
        "selected_country_count": len(countries),
        "recipe_count": len(selected_recipes),
        "results": get_analysis_top_entities(
            selected_recipes,
            "ingredient_text",
            limit=15
        )
    })

# Analysis page 8: top cooking actions of the picked countries
@app.route("/api/analysis/instructions")
def analysis_instructions():
    countries = request.args.getlist("country")

    instruction_type = request.args.get(
        "type",
        "actions"
    ).strip().lower()

    field_map = {
        "actions": "action_text",
        "cooking_actions": "action_text",
        "utensils": "utensil_text",
        "cookware": "cookware_text"
    }

    field = field_map.get(
        instruction_type,
        "action_text"
    )

    selected_recipes = (
        get_analysis_recipes_for_countries(countries)
    )

    return jsonify({
        "countries": countries,
        "selected_country_count": len(countries),
        "type": instruction_type,
        "recipe_count": len(selected_recipes),
        "results": get_analysis_top_entities(
            selected_recipes,
            field,
            limit=10
        )
    })

@app.route("/faqs")
def faqs():
    return render_template("faqs.html")



# ==========================================================================
#  PANEL REVISIONS: routes
# ==========================================================================

# ---------------- accounts (Oab 2-3, Galang 5-6) ----------------
def _safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else url_for("home")


@app.route("/login", methods=["GET", "POST"])
def login():
    user = current_user()
    if request.method == "GET" and user and user["is_admin"]:
        return redirect(url_for("admin_page"))
    if request.method == "POST":
        try:
            user = accounts.authenticate(request.form.get("username"), request.form.get("password"))
        except accounts.SupabaseError as error:
            flash(str(error), "error")
            return render_template("auth.html", mode="login", next=request.values.get("next", "")), 400
        if user and user["is_admin"]:  # admins sign in on their own page
            flash("Admin accounts sign in on the admin log in page.", "error")
            return redirect(url_for("admin_login"))
        if user:
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = bool(request.form.get("remember"))  # "Keep me signed in"
            flash(f"Welcome back, {user['name']}!", "success")
            nxt = request.form.get("next")
            return redirect(_safe_next(nxt) if nxt else url_for("home"))
        flash("Wrong email / username or password.", "error")
    return render_template("auth.html", mode="login", next=request.values.get("next", ""))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        if request.form.get("password") != request.form.get("confirm"):
            flash("The two passwords don't match.", "error")
        else:
            try:
                uid = accounts.create_user(request.form.get("username"), request.form.get("password"),
                                           display_name=request.form.get("display_name", ""),
                                           email=request.form.get("email", ""))
                session.clear()
                session["user_id"] = uid
                flash("Your account is ready. Set up your profile below.", "success")
                return redirect(url_for("profile"))
            except ValueError as error:
                flash(str(error), "error")
    return render_template("auth.html", mode="register", next="",
                           form={k: request.form.get(k, "") for k in ("email", "username", "display_name")})


@app.route("/logout")
def logout():
    session.clear()
    flash("You are logged out.", "info")
    return redirect(url_for("home"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        try:
            accounts.update_profile(user["id"], request.form)
            flash("Profile saved.", "success")
        except accounts.SupabaseError as error:
            flash(str(error), "error")
        return redirect(url_for("profile"))
    return render_template(
        "profile.html",
        user=user,
        history=accounts.get_history(user["id"]),
        cooking_roles=accounts.COOKING_ROLES,
        allergen_choices=accounts.ALLERGEN_CHOICES,
        all_countries=STORE.countries,
    )


@app.route("/profile/password", methods=["POST"])
@login_required
def change_password():
    try:
        accounts.change_password(current_user()["id"], request.form.get("old"), request.form.get("new"))
        flash("Password changed.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("profile") + "#account")


@app.route("/history/<int:entry_id>")
@login_required
def open_history(entry_id):
    """Open a past search again (Oab 3: users can go back to past searches)."""
    entry = accounts.get_history_entry(current_user()["id"], entry_id)
    if not entry:
        abort(404)
    if entry["search_type"] in ("text", "image"):
        saved = json.loads(entry["query"] or "{}") if (entry["query"] or "").startswith("{") else {}
        return get_search_results(entry["dish_name"], entry["origin_country"], entry["target_country"],
                                  force=True, recipe_id=entry["recipe_id"] or None,
                                  search_type=entry["search_type"], ingredients=saved.get("ingredients", ""))
    params = json.loads(entry["query"] or "{}")
    if params.get("mode") == "tags":
        return redirect(url_for("home", mode="tags", tag=params.get("tag", []),
                                country=params.get("country", []),
                                **({"ing": params["ing"]} if params.get("ing") else {})) + "#search-section")
    if params.get("mode") in ("ingredients", "keyword"):
        return redirect(url_for("home", mode="ingredients" if params["mode"] == "ingredients" else "dish",
                                q=params.get("q", "")) + "#search-section")
    return redirect(url_for("explore", **params))


@app.route("/history/<int:entry_id>/delete", methods=["POST"])
@login_required
def delete_history(entry_id):
    accounts.delete_history(current_user()["id"], entry_id)
    return redirect(url_for("profile") + "#history")


@app.route("/history/clear", methods=["POST"])
@login_required
def clear_history():
    accounts.delete_history(current_user()["id"])
    flash("Search history cleared.", "info")
    return redirect(url_for("profile") + "#history")


@app.route("/api/me")
def api_me():
    user = current_user()
    if not user:
        return jsonify({"logged_in": False})
    return jsonify({"logged_in": True, "name": user["name"]})


# ---------------- explore: keyword, ingredient and tag search (Galang 1, 4, 7; Oab 6) ----------------
@app.route("/api/find-dishes")
def api_find_dishes():
    """Dishes for the popup of the text search (dish name or ingredients).
    mode=dish: every dish whose name contains the keyword ("adobo" -> all adobo dishes)
    mode=ingredients: dishes that use the ingredients + the cuisines you could cook
    """
    mode = request.args.get("mode", "dish")
    q = request.args.get("q", "").strip()
    if not q and not (mode == "tags" and (request.args.get("ing", "").strip()
                                          or request.args.get("country", "").strip())):
        return jsonify({"dishes": [], "total": 0})
    if mode == "ingredients":
        wanted = [w for w in split_names(q) if w]
        # A specific target country: only that country's dishes that use the ingredients
        country = request.args.get("country", "").strip().lower()
        if country == ANY_COUNTRY:
            country = ""
        results, unknown = INGREDIENT_SEARCH.search(RECIPES, wanted, country, limit=60)
        if country and results:
            most = max(len(found) for _, _, found in results)
            results = [r for r in results if len(r[2]) == most]
        cuisines = [] if country else search_tools.possible_cuisines(RECIPES, results, STORE.label_of)
        save_history(search_type="ingredients", query=json.dumps({"mode": "ingredients", "q": q}),
                     result_title=f"{len(results)} dishes")
        return jsonify({
            "mode": "ingredients",
            "total": len(results),
            "unknown": unknown,
            "cuisines": cuisines,
            "country": country,
            "country_label": STORE.label_of(country.upper()) if country else "",
            "dishes": [dish_card(idx, score, found) for idx, score, found in results],
        })
    if mode == "tags":
        # "Search by tag": tags (region, cooking method, dish type) and ingredients together.
        # Dishes must have all the tags; with ingredients they are ranked by how many they use.
        tags = [t for t in q.split("|") if t]
        wanted = [w for w in split_names(request.args.get("ing", "")) if w]
        # Countries of origin from the Region tab
        countries = {c.strip().lower() for c in request.args.get("country", "").split("|") if c.strip()}
        # Region tab = places: regions and countries are "any of them" (OR), so
        # South Korea + Southeast Asia -> every Korean dish and every Southeast Asian dish.
        # The other tabs (cooking method, dish type) must all match (AND).
        def region_of_recipe(r):
            return r.get("country_region") or STORE.region_of(r.get("country"))
        known_regions = {region_of_recipe(r) for r in RECIPES}
        place_regions = {t for t in tags if t in known_regions}
        other_tags = [t for t in tags if t not in place_regions]
        unknown, cuisines = [], []
        if wanted:
            results, unknown = INGREDIENT_SEARCH.search(RECIPES, wanted, "", limit=len(RECIPES))
            if other_tags:
                results = [r for r in results
                           if all(t in search_tools.recipe_tags(RECIPES[r[0]], STORE.region_of) for t in other_tags)]
        else:
            results = search_tools.tag_search(RECIPES, other_tags, "", STORE.region_of)
        if place_regions or countries:
            results = [r for r in results
                       if RECIPES[r[0]]["country"].lower() in countries
                       or region_of_recipe(RECIPES[r[0]]) in place_regions]
        if wanted:
            cuisines = search_tools.possible_cuisines(RECIPES, results, STORE.label_of)
        saved = {"mode": "tags", "tag": tags}
        if wanted:
            saved["ing"] = ", ".join(wanted)
        if countries:
            saved["country"] = sorted(countries)
        save_history(search_type="tags", query=json.dumps(saved), result_title=f"{len(results)} dishes")
        return jsonify({
            "mode": "tags",
            "tags": tags,
            "ingredients": wanted,
            "countries": [STORE.label_of(c.upper()) for c in sorted(countries)],
            "unknown": unknown,
            "cuisines": cuisines,
            "total": len(results),
            "dishes": [dish_card(idx, score if wanted else None, found) for idx, score, found in results[:60]],
        })
    results = search_tools.keyword_search(RECIPES, q, "", recipe_names)
    return jsonify({
        "mode": "dish",
        "total": len(results),
        "close_spelling": bool(results) and results[0][1] == 9,
        "dishes": [dish_card(idx) for idx, _, _ in results[:60]],
    })


@app.route("/explore")
def explore():
    mode = request.args.get("mode", "tags")
    if mode in ("keyword", "ingredients"):
        # Dish and ingredient search now live in the home page search bar
        return redirect(url_for("home", mode="dish" if mode == "keyword" else "ingredients",
                                q=request.args.get("q", "")) + "#search-section")
    # Tag search is now the "Search by tag" tab on the home page
    return redirect(url_for("home", mode="tags", tag=request.args.getlist("tag")) + "#search-section")
    mode = "tags"
    q = request.args.get("q", "").strip()
    country = request.args.get("country", "").strip()
    chosen_tags = [t for t in request.args.getlist("tag") if t]
    tag_groups, _ = search_tools.all_tags(RECIPES, STORE.region_of)

    results, unknown, cuisines, searched = [], [], [], False
    if mode == "keyword" and q:
        searched = True
        results = search_tools.keyword_search(RECIPES, q, country, recipe_names)
    elif mode == "ingredients" and q:
        searched = True
        wanted = [w for w in split_names(q) if w]
        results, unknown = INGREDIENT_SEARCH.search(RECIPES, wanted, country)
        cuisines = search_tools.possible_cuisines(RECIPES, results, STORE.label_of)
    elif mode == "tags" and chosen_tags:
        searched = True
        results = search_tools.tag_search(RECIPES, chosen_tags, country, STORE.region_of)

    if searched:
        params = {"mode": mode}
        if q:
            params["q"] = q
        if country:
            params["country"] = country
        if chosen_tags:
            params["tag"] = chosen_tags
        save_history(
            search_type=mode if mode != "tags" else "tags",
            query=json.dumps(params, ensure_ascii=False),
            dish_name="",
            origin_country=country,
            result_title=f"{len(results)} dishes",
        )

    page_size = 48
    try:
        shown = max(int(request.args.get("show", page_size)), page_size)
    except ValueError:
        shown = page_size
    more_args = request.args.to_dict(flat=False)
    more_args["show"] = [shown + page_size]
    close_spelling = mode == "keyword" and bool(results) and results[0][1] == 9
    cards = [dish_card(idx, score if mode == "ingredients" else None, found)
             for idx, score, found in results[:shown]]
    return render_template(
        "explore.html",
        mode=mode, q=q, country=country, chosen_tags=chosen_tags, tag_groups=tag_groups,
        cards=cards, total=len(results), shown=shown, page_size=page_size,
        unknown=unknown, cuisines=cuisines, searched=searched,
        close_spelling=close_spelling, more_url=url_for("explore", **more_args),
        avoid_allergens=(current_user() or {}).get("avoid_allergens", []),
    )


# ---------------- Dish Dictionary (every dish in the dataset, search + filters) ----------------
DICTIONARY_ENTITIES = {
    "ingredient": ("ingredient_text", "Ingredient"),
    "action": ("action_text", "Cooking action"),
    "cookware": ("cookware_text", "Cookware"),
    "utensil": ("utensil_text", "Utensil"),
}


_ALLERGEN_CACHE = {}   # (title, ingredient_text) -> allergen names (finding them is slow-ish)


def dish_allergens(recipe):
    key = (recipe.get("title"), recipe.get("ingredient_text"))
    if key not in _ALLERGEN_CACHE:
        _ALLERGEN_CACHE[key] = [a["name"] for a in cultural_info.find_allergens(recipe)]
    return _ALLERGEN_CACHE[key]


def entity_matches(item, wanted):
    """"pork" matches "pork", "pork belly", "ground pork" but not "pork stock" (same rule as the
    ingredient search in search_tools.py)."""
    item = item.lower()
    if item == wanted:
        return True
    m = re.search(r"\b" + re.escape(wanted) + r"(?:s|es)?\b", item)
    if not m:
        return False
    after = item[m.end():].split()
    return not (after and set(after) & search_tools.IngredientSearch.MADE_FROM)

_ENTITY_OPTIONS = {}   # kind -> [[name, number of dishes, [dish ids]], ...] (built once)


def dictionary_entity_options(dishes):
    """Checklist options for the "Browse by category" dropdown: every ingredient, cooking action,
    cookware and utensil in the dataset, with the dishes that have it (same matching rule as the
    ?ingredient= links, so "pork" also finds "pork belly")."""
    key = tuple(d["id"] for d in dishes)
    if _ENTITY_OPTIONS.get("key") == key:
        return _ENTITY_OPTIONS["value"]
    out = {}
    for kind, (field, label) in DICTIONARY_ENTITIES.items():
        dishes_with = defaultdict(set)          # item as written -> dish ids
        for d in dishes:
            for item in parse_items(RECIPES[d["id"]].get(field, "")):
                dishes_with[item.lower()].add(d["id"])
        names = list(dishes_with)
        options = []
        for wanted in names:
            ids = set()
            for item in names:
                if wanted in item and entity_matches(item, wanted):
                    ids |= dishes_with[item]
            options.append([wanted, len(ids), sorted(ids)])
        options.sort(key=lambda o: (-o[1], o[0]))
        out[kind] = {"label": label, "options": options}
    _ENTITY_OPTIONS.update(key=key, value=out)
    return out


@app.route("/dictionary")
def dictionary():
    """All dishes in the dataset. Clickable tags anywhere in the system link here
    with ?tag=<tag> (and countries with ?country=<name>). Filtering, sorting and paging
    happen in the browser (static/scripts/dictionary.js)."""
    visible = {c["value"] for c in STORE.dropdown_countries()}
    counts = Counter(r["country"].lower() for r in RECIPES if r["country"].lower() in visible)
    dishes = []
    for idx, r in enumerate(RECIPES):
        country = r["country"].lower()
        if country not in visible:
            continue
        dishes.append({
            "id": idx,
            "t": (r.get("title") or "").strip(),
            "a": (r.get("alternative_title") or "").strip(),
            "c": country,
            "l": STORE.label_of(r["country"]),
            "g": search_tools.recipe_tags(r, STORE.region_of),
            "x": dish_allergens(r),   # allergens, for the "Don't show" filter
            "i": r.get("image_link") or "",
        })
    tag_groups, _ = search_tools.all_tags([RECIPES[d["id"]] for d in dishes], STORE.region_of)

    # Ingredient / cooking action / cookware / utensil links (from the comparison page):
    # the matching dishes are found here, so the page doesn't need every recipe's item lists.
    entity_filters = []
    for kind, (field, label) in DICTIONARY_ENTITIES.items():
        for wanted in request.args.getlist(kind):
            wanted = " ".join(wanted.lower().split())
            if not wanted:
                continue
            ids = [d["id"] for d in dishes
                   if any(entity_matches(item, wanted) for item in parse_items(RECIPES[d["id"]].get(field, "")))]
            entity_filters.append({"kind": kind, "label": label, "value": wanted, "ids": ids})
    allergen_counts = Counter(a for d in dishes for a in d["x"])
    return render_template(
        "dictionary.html",
        entity_filters=entity_filters,
        # [(allergen, number of dishes that HAVE it)] for the "Don't show" dropdown
        allergen_options=[(a, allergen_counts.get(a, 0)) for a in cultural_info.ALLERGENS],
        dishes=dishes,
        entity_options=dictionary_entity_options(dishes),
        countries=[{**c, "count": counts[c["value"]]} for c in STORE.dropdown_countries()],
        tag_groups=tag_groups,
    )


@app.route("/api/ingredients")
def api_ingredients():
    """Ingredient names for the autocomplete on the Explore page."""
    counts = INGREDIENT_SEARCH.df
    names = sorted(((INGREDIENT_SEARCH.items[i], c) for i, c in counts.items()), key=lambda x: -x[1])
    # [name, number of dishes that use it], most used first
    return jsonify([[n, c] for n, c in names[:1500]])


# ---------------- cultural information (Oab 4) ----------------
@app.route("/api/dish-info/<int:idx>")
def api_dish_info(idx):
    if not 0 <= idx < len(RECIPES):
        return jsonify({"error": "invalid recipe index"}), 404
    recipe = RECIPES[idx]
    info = cultural_info.dish_info(recipe, STORE.region_of(recipe["country"]), STORE.label_of(recipe["country"]))
    info["tags"] = search_tools.recipe_tags(recipe, STORE.region_of)
    user = current_user()
    avoid = set((user or {}).get("avoid_allergens", []))
    for a in info["allergens"]:
        a["avoid"] = a["name"] in avoid
    return jsonify(info)


@app.route("/api/dish/<int:idx>")
def api_dish(idx):
    """Everything the Dish Dictionary popup shows for one dish: the card (name, country,
    image, tags), its recipe breakdown, culture/history (+ its country's heritage note),
    and health info. Also used by "Find similar dishes" to fill the home page search."""
    if not 0 <= idx < len(RECIPES):
        return jsonify({"error": "invalid recipe index"}), 404
    recipe = RECIPES[idx]
    country = recipe["country"]
    info = cultural_info.dish_info(recipe, STORE.region_of(country), STORE.label_of(country))
    avoid = set((current_user() or {}).get("avoid_allergens", []))
    for a in info["allergens"]:
        a["avoid"] = a["name"] in avoid
    title = (recipe.get("title") or "").strip()
    info.update(
        idx=idx,
        recipe_id=recipe.get("recipe_id"),
        title=title,
        alt=(recipe.get("alternative_title") or "").strip(),
        country=country.lower(),
        label=STORE.label_of(country),
        region=STORE.region_of(country),
        image=recipe.get("image_link") or "",
        recipe=format_recipe_url(recipe.get("recipe_link", "")),
        tags=search_tools.recipe_tags(recipe, STORE.region_of),
        # kind -> [items]; kind is also the Dish Dictionary filter name (?ingredient=...)
        entities={kind: parse_items(recipe.get(field, ""))
                  for kind, (field, _label) in DICTIONARY_ENTITIES.items()},
        # Template note about the dish's own country (same as the overview with "Any")
        connection=cultural_info.country_connection(
            RECIPES, country, None, STORE.label_of, STORE.region_of, parse_items, dish_title=title),
    )
    info.pop("prompt", None)
    info["connection"].pop("prompt", None)
    return jsonify(info)


# ---------------- admin (Oab 1, 2, 5; Galang 3) ----------------
ADMIN_TABS = ("overview", "users", "dishes", "countries", "prompts", "tags", "dataset")


@app.route("/admin")
@admin_required
def admin_page():
    tab = request.args.get("tab", "overview")
    if tab not in ADMIN_TABS:
        tab = "overview"
    ctx = {"tab": tab, "store": STORE, "regions": REGION_CHOICES}
    if tab == "overview":
        per_country = {}
        for r in RECIPES:
            per_country[r["country"]] = per_country.get(r["country"], 0) + 1
        try:  # account numbers come from Supabase
            per_day = accounts.searches_per_day(7)
            stats, recent = accounts.history_stats(), accounts.recent_searches(6)
        except accounts.SupabaseError as error:
            flash(f"Account numbers are not available: {error} (Did you run supabase_setup.sql?)", "error")
            import datetime as _dt
            per_day = [(_dt.date.today() - _dt.timedelta(days=6 - i), 0) for i in range(7)]
            stats, recent = {"total_searches": 0, "users": 0, "top_dishes": []}, []
        most = max([n for _, n in per_day] + [1])
        ctx.update(stats=stats, per_country=per_country, total=len(RECIPES),
                   missing=STORE.missing_vectors, gpt_on=explainer._client is not None,
                   per_day=[{"label": d.strftime("%a"), "value": n, "height": round(100 * n / most)}
                            for d, n in per_day],
                   recent=recent, ner_status=ner.status(),
                   visible_countries=len(STORE.dropdown_countries()))
    elif tab == "dishes":
        q = request.args.get("q", "").strip()
        country = request.args.get("country", "").strip().upper()
        rows = [
            (i, r) for i, r in enumerate(RECIPES)
            if (not country or r["country"] == country)
            and (not q or normalize_name(q) in normalize_name(f"{r.get('title')} {r.get('alternative_title')} {r.get('ingredient_text')}"))
        ]
        page = max(int(request.args.get("page", 1) or 1), 1)
        per_page = 40
        ctx.update(q=q, country=country, total=len(rows), page=page,
                   pages=max((len(rows) - 1) // per_page + 1, 1),
                   rows=rows[(page - 1) * per_page: page * per_page])
    elif tab == "countries":
        counts = {}
        for r in RECIPES:
            counts[r["country"]] = counts.get(r["country"], 0) + 1
        ctx.update(counts=counts)
    elif tab == "prompts":
        ctx.update(
            explanation_prompt=cuisync_prompts.get_system_prompt(),
            explanation_custom=bool(STORE.prompts.get("explanation")),
            cultural_prompt=STORE.get_prompt("cultural", cultural_info.DEFAULT_PROMPT),
            cultural_custom=bool(STORE.prompts.get("cultural")),
            gpt_on=explainer._client is not None,
        )
    elif tab == "users":
        q = request.args.get("q", "").strip().lower()
        role = request.args.get("role", "")
        try:
            all_users = accounts.list_users()
        except accounts.SupabaseError as error:
            flash(f"Could not load the users: {error} (Did you run supabase_setup.sql?)", "error")
            all_users = []
        users = [u for u in all_users
                 if (not q or q in f"{u['username']} {u['name']} {u.get('email') or ''}".lower())
                 and (not role or u["role"] == role)]
        selected = next((dict(u) for u in all_users if u["id"] == request.args.get("user")), None)
        if selected:
            selected["history"] = accounts.get_history(selected["id"], limit=5)
        ctx.update(users=users, me=current_user(), q=q, role=role, selected=selected)
    elif tab == "tags":
        groups, _ = search_tools.all_tags(RECIPES, STORE.region_of)
        custom = STORE.custom_tags()
        q = request.args.get("q", "").strip().lower()
        ctx.update(
            q=q,
            custom_tags=sorted(((t, n) for t, n in custom.items() if not q or q in t.lower()),
                               key=lambda x: (-x[1], x[0])),
            auto_groups={g: [(t, n) for t, n in ts if not q or q in t.lower()]
                         for g, ts in groups.items() if g != "Custom"},
            editing=request.args.get("edit", ""),
        )
    return render_template("admin/admin.html", **ctx)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Separate log in page for admins. Only accounts with role 'admin' get in."""
    nxt = request.values.get("next", "")
    user = current_user()
    if request.method == "GET" and user and user["is_admin"]:
        return redirect(url_for("admin_page"))
    login_value = ""
    if request.method == "POST":
        login_value = (request.form.get("username") or "").strip()
        try:
            found = accounts.authenticate(login_value, request.form.get("password"))
        except accounts.SupabaseError as error:
            flash(str(error), "error")
            return render_template("admin/admin_login.html", next=nxt, login_value=login_value), 400
        if found and found["is_admin"]:
            session.clear()
            session["user_id"] = found["id"]
            session.permanent = bool(request.form.get("remember"))
            flash(f"Welcome back, {found['name']}!", "success")
            ok = nxt.startswith("/admin") and not nxt.startswith("//") and not nxt.startswith("/admin/login")
            return redirect(nxt if ok else url_for("admin_page"))
        flash("This account is not an admin." if found else "Wrong email / username or password.", "error")
    return render_template("admin/admin_login.html", next=nxt, login_value=login_value)


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    flash("You are logged out.", "info")
    return redirect(url_for("admin_login"))


@app.route("/admin/tags/rename", methods=["POST"])
@admin_required
def admin_tag_rename():
    try:
        n = STORE.rename_tag(request.form.get("old"), request.form.get("new"))
        flash(f'Tag renamed on {n} dish(es).', "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("admin_page", tab="tags"))


@app.route("/admin/tags/delete", methods=["POST"])
@admin_required
def admin_tag_delete():
    try:
        n = STORE.delete_tag(request.form.get("name"))
        flash(f'Tag removed from {n} dish(es).', "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("admin_page", tab="tags"))


def _after_data_change(message):
    missing = reload_data()
    if missing:
        sample = ", ".join(item for _, item in missing[:6])
        flash(f"{message} Note: {len(missing)} new item(s) had no close match in the vectors "
              f"({sample}{'...' if len(missing) > 6 else ''}), so they can't add to similarity until "
              "the vectorizer is run again.", "info")
    else:
        flash(message, "success")


@app.route("/admin/dishes/new", methods=["GET", "POST"])
@app.route("/admin/dishes/<recipe_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_dish_form(recipe_id=None):
    dish = {}
    if recipe_id is not None:
        idx = STORE.find_index(recipe_id)
        if idx is None:
            abort(404)
        dish = RECIPES[idx]
    if request.method == "POST":
        try:
            if recipe_id is None:
                saved = STORE.add_dish(request.form)
                _after_data_change(f'Added "{saved["title"]}".')
            else:
                saved = STORE.update_dish(recipe_id, request.form)
                _after_data_change(f'Saved "{saved["title"]}".')
            return redirect(url_for("admin_page", tab="dishes", q=saved["title"]))
        except ValueError as error:
            flash(str(error), "error")
            dish = dict(request.form.items())
    return render_template("admin/admin_dish.html", dish=dish, recipe_id=recipe_id, fields=DISH_FIELDS,
                           all_countries=STORE.countries, store=STORE, regions=REGION_CHOICES,
                           ner_status=ner.status())


@app.route("/admin/api/extract-ingredients", methods=["POST"])
@admin_required
def admin_extract_ingredients():
    """Admin pastes recipe text; the BERT NER model finds the ingredients (ner.py)."""
    data = request.get_json(silent=True) or {}
    try:
        return jsonify({"ingredients": ner.extract_and_match(str(data.get("text", "")))})
    except ner.NerError as error:
        return jsonify({"error": "ner_failed", "message": str(error)}), 400
    except Exception as error:  # never show a raw crash to the admin
        app.logger.exception("NER failed")
        return jsonify({"error": "ner_failed", "message": f"Ingredient extraction failed: {error}"}), 500


@app.route("/admin/api/extract-directions", methods=["POST"])
@admin_required
def admin_extract_directions():
    """Admin pastes the directions; finds the cooking actions, cookware and utensils (ner.py)."""
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(ner.extract_directions(str(data.get("text", ""))))
    except ner.NerError as error:
        return jsonify({"error": "extract_failed", "message": str(error)}), 400


@app.route("/admin/dishes/<recipe_id>/delete", methods=["POST"])
@admin_required
def admin_dish_delete(recipe_id):
    try:
        removed = STORE.delete_dish(recipe_id)
        _after_data_change(f'Deleted "{removed["title"]}".')
    except ValueError as error:
        flash(str(error), "error")
    return redirect(request.referrer or url_for("admin_page", tab="dishes"))


@app.route("/admin/countries/add", methods=["POST"])
@admin_required
def admin_country_add():
    try:
        STORE.add_country(request.form.get("label"), request.form.get("region"))
        flash(f"Added {request.form.get('label')}. It shows in the dropdowns once it has a dish.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("admin_page", tab="countries"))


@app.route("/admin/countries/<name>/update", methods=["POST"])
@admin_required
def admin_country_update(name):
    try:
        if STORE.update_country(name, request.form.get("label"), request.form.get("region"),
                                request.form.get("visible") == "on"):
            reload_data()
        flash("Country saved.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("admin_page", tab="countries"))


@app.route("/admin/countries/<name>/delete", methods=["POST"])
@admin_required
def admin_country_delete(name):
    try:
        STORE.remove_country(name)
        flash("Country removed.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("admin_page", tab="countries"))


@app.route("/admin/dataset/upload", methods=["POST"])
@admin_required
def admin_dataset_upload():
    file = request.files.get("dataset")
    mode = "replace" if request.form.get("mode") == "replace" else "append"
    if not file or not file.filename:
        flash("Choose a .json or .csv file first.", "error")
        return redirect(url_for("admin_page", tab="dataset"))
    try:
        raw = file.read().decode("utf-8-sig")
        if file.filename.lower().endswith(".csv"):
            records = list(csv.DictReader(io.StringIO(raw)))
        else:
            records = json.loads(raw)
            if isinstance(records, dict):
                records = records.get("recipes") or [records]
        if not isinstance(records, list):
            raise ValueError("The file must contain a list of dishes.")
        count = STORE.import_dataset(records, mode)
        _after_data_change(f"{'Replaced the dataset with' if mode == 'replace' else 'Added'} {count} dishes.")
    except (ValueError, UnicodeDecodeError, csv.Error) as error:
        flash(f"Upload failed: {error}", "error")
    return redirect(url_for("admin_page", tab="dataset"))


@app.route("/admin/dataset/download")
@admin_required
def admin_dataset_download():
    if request.args.get("format") == "csv":
        out = io.StringIO()
        fields = ["recipe_id"] + list(DISH_FIELDS)
        writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in RECIPES:
            writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})
        return Response(out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=cuisync_dataset.csv"})
    return Response(json.dumps(RECIPES, ensure_ascii=False, indent=1), mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=cuisync_dataset.json"})


@app.route("/admin/dataset/reset", methods=["POST"])
@admin_required
def admin_dataset_reset():
    try:
        STORE.reset_to_original()
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("admin_page", tab="dataset"))
    reload_data()
    flash("The dataset is back to the original recipes.json." if STORE.db else
          "The dataset is back to the original recipes.json (your edited copy was kept as a .bak file).",
          "success")
    return redirect(url_for("admin_page", tab="dataset"))


@app.route("/admin/prompts", methods=["POST"])
@admin_required
def admin_prompts():
    which = request.form.get("which")
    if which not in ("explanation", "cultural"):
        abort(400)
    text = "" if request.form.get("action") == "reset" else request.form.get("prompt", "")
    if which == "explanation" and text.strip() == cuisync_prompts.DEFAULT_SYSTEM_PROMPT.strip():
        text = ""
    if which == "cultural" and text.strip() == cultural_info.DEFAULT_PROMPT.strip():
        text = ""
    try:
        STORE.set_prompt(which, text)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("admin_page", tab="prompts"))
    if which == "explanation":
        cuisync_prompts.set_system_prompt(text)
    flash("Prompt reset to the default." if not text else "Prompt saved. New searches use it right away.", "success")
    return redirect(url_for("admin_page", tab="prompts"))


@app.route("/admin/users/<user_id>/role", methods=["POST"])
@admin_required
def admin_user_role(user_id):
    try:
        accounts.set_role(user_id, request.form.get("role"))
        flash("Role updated.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("admin_page", tab="users", user=user_id))


@app.route("/admin/users/<user_id>/status", methods=["POST"])
@admin_required
def admin_user_status(user_id):
    if user_id == current_user()["id"]:
        flash("You can't disable your own account.", "error")
    else:
        try:
            accounts.set_status(user_id, request.form.get("status"))
            flash("Account " + ("disabled." if request.form.get("status") == "disabled" else "enabled."), "success")
        except ValueError as error:
            flash(str(error), "error")
    return redirect(url_for("admin_page", tab="users", user=user_id))


@app.route("/admin/users/<user_id>/delete", methods=["POST"])
@admin_required
def admin_user_delete(user_id):
    if user_id == current_user()["id"]:
        flash("You can't delete your own account here.", "error")
    else:
        try:
            accounts.delete_user(user_id)
            flash("User deleted.", "success")
        except ValueError as error:
            flash(str(error), "error")
    return redirect(url_for("admin_page", tab="users"))


@app.route("/admin/account", methods=["GET", "POST"])
@admin_required
def admin_account():
    """The admin's own name and password (so the admin never needs the public profile page)."""
    user = current_user()
    if request.method == "POST":
        if request.form.get("action") == "password":
            if request.form.get("new") != request.form.get("confirm"):
                flash("The two new passwords don't match.", "error")
            else:
                try:
                    accounts.change_password(user["id"], request.form.get("old"), request.form.get("new"))
                    flash("Password changed.", "success")
                except ValueError as error:
                    flash(str(error), "error")
        else:
            keep = {"favorite_cuisines": user.get("favorite_cuisines", []),
                    "avoid_allergens": user.get("avoid_allergens", [])}
            form = type("Form", (dict,), {"getlist": lambda self, k: keep.get(k, [])})(
                display_name=request.form.get("display_name", ""), cooking_role=user.get("cooking_role", ""),
                home_country=user.get("home_country", ""), bio=user.get("bio", ""))
            accounts.update_profile(user["id"], form)
            flash("Name saved.", "success")
        return redirect(url_for("admin_account"))
    return render_template("admin/admin_account.html", active="account", tab="account")


@app.errorhandler(403)
def forbidden(_error):
    return render_template("auth.html", mode="login", next="",
                           forbidden="Only admins can open that page."), 403



# ---------------- Old PWA clean-up ----------------
# The site used to install a service worker. Phones and browsers that still have it ask for
# /sw.js now and then: this one removes itself and its saved copies, then reloads the pages.
OLD_SW_CLEANUP = """
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k.startsWith('cuisync-')).map((k) => caches.delete(k)));
    await self.registration.unregister();
    const pages = await self.clients.matchAll({ type: 'window' });
    pages.forEach((page) => page.navigate(page.url));
  })());
});
"""


@app.route("/sw.js")
def old_service_worker_cleanup():
    response = app.response_class(OLD_SW_CLEANUP, mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response


if __name__ == "__main__":
    # Load the OCR models in the background so the first upload is faster.
    # Only load them in the real app process (the reloader starts the app twice).
    USE_RELOADER = False
    if not USE_RELOADER or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Thread(target=ocr.warm_up, daemon=True).start()

    app.run(host="127.0.0.1", port=8000, debug=False, use_reloader=USE_RELOADER)
