"""Cultural information for a dish (panel revision Oab 4): culture involved, history,
health benefits and allergens.

  - Allergens are found with rules from the dish's own ingredient list, so they never
    depend on GPT. Each allergen shows the ingredients that triggered it.
  - Culture, history and health notes come from, in this order:
      1. notes the admin typed for the dish (admin page)
      2. GPT (cached in admin_data/cuisync.db), clearly labelled as AI-generated
      3. a short template built from the dataset (when GPT is off)
"""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing

DEFAULT_PROMPT = """You write short cultural notes for CuiSync, a system that compares Asian dishes.
You get one JSON object with a dish name, its alternative name, its country, its region and its ingredient list.
Return ONLY a JSON object with these keys:
  "culture": 1-2 sentences on the culture or community the dish belongs to and when or how it is usually eaten.
  "history": 1-2 sentences on where the dish is generally believed to come from or how it developed. If the history is uncertain or you do not know it, say so plainly instead of guessing.
  "health": a list of 2-4 short, factual nutrition notes based ONLY on ingredients in the list (for example "Chicken thigh adds protein."). No medical claims, no cures, no disease prevention.
Rules: be neutral and respectful, do not rank cuisines, do not invent ingredients, keep each sentence under 30 words, write for home cooks and chefs."""

# Allergen groups (based on the common food allergens on labels). Each group lists words that
# trigger it and words that cancel a false hit (peanut butter is not milk, eggplant is not egg).
ALLERGENS = {
    "Peanuts": {"hit": ["peanut", "groundnut"], "not": []},
    "Tree nuts": {"hit": ["almond", "cashew", "walnut", "pistachio", "hazelnut", "pecan", "macadamia",
                          "candlenut", "pine nut", "brazil nut", "chestnut"], "not": ["water chestnut"]},
    "Milk": {"hit": ["milk", "butter", "cream", "cheese", "yogurt", "yoghurt", "ghee", "paneer", "curd",
                     "condensed milk", "evaporated milk", "khoa", "malai", "dahi", "labneh", "kaymak", "whey"],
             "not": ["coconut milk", "coconut cream", "peanut butter", "almond milk", "soy milk",
                     "cream of coconut", "bean curd", "cream of tartar", "cream cracker"]},
    "Egg": {"hit": ["egg", "mayonnaise"], "not": ["eggplant"]},
    "Fish": {"hit": ["fish", "anchovy", "anchovies", "bonito", "tuna", "salmon", "mackerel", "sardine",
                     "cod", "tilapia", "milkfish", "catfish", "snapper", "dashi", "katsuobushi", "mahi"],
             "not": ["fish ball sauce"]},
    "Shellfish": {"hit": ["shrimp", "prawn", "crab", "lobster", "clam", "mussel", "oyster", "scallop",
                          "squid", "octopus", "cuttlefish", "krill", "belacan", "bagoong", "cockle"], "not": []},
    "Soy": {"hit": ["soy", "soya", "tofu", "miso", "edamame", "tempeh", "natto", "doenjang", "gochujang",
                    "tamari", "kecap", "bean curd", "soybean", "taucu", "tauco"], "not": []},
    "Wheat/Gluten": {"hit": ["flour", "wheat", "bread", "panko", "breadcrumb", "semolina", "bulgur",
                             "couscous", "udon", "ramen", "soba", "noodle", "pasta", "wonton", "dumpling wrapper",
                             "spring roll wrapper", "pastry", "roti", "naan", "pita", "soy sauce", "barley",
                             "seitan", "beer", "yufka", "phyllo", "filo"],
                     "not": ["rice flour", "glutinous rice flour", "rice noodle", "tapioca flour", "corn flour",
                             "cornflour", "chickpea flour", "gram flour", "potato flour", "mung bean noodle",
                             "glass noodle", "rice paper", "cassava flour", "rice vermicelli", "buckwheat"]},
    "Sesame": {"hit": ["sesame", "tahini", "gomasio"], "not": []},
}

# Plain nutrition notes for common ingredients, used when GPT is off
NUTRITION_NOTES = [
    (("soy sauce", "fish sauce", "shrimp paste", "oyster sauce", "miso", "bagoong", "salt"), "{x} adds a lot of sodium, so use it in moderation."),
    (("chicken", "pork", "beef", "lamb", "mutton", "goat", "duck", "turkey"), "{x} adds protein."),
    (("fish", "salmon", "tuna", "mackerel", "sardine", "milkfish", "tilapia"), "{x} adds protein; oily fish also gives omega-3 fats."),
    (("shrimp", "prawn", "squid", "crab", "clam", "mussel", "scallop"), "{x} is a lean source of protein."),
    (("egg",), "{x} adds protein."),
    (("tofu", "tempeh", "edamame"), "{x} is a plant source of protein."),
    (("lentil", "chickpea", "mung bean", "black bean", "kidney bean", "dal"), "{x} adds plant protein and fiber."),
    (("spinach", "kangkong", "bok choy", "cabbage", "kale", "moringa", "malunggay", "mustard green", "pak choi"), "{x} adds fiber and vitamins."),
    (("carrot", "sweet potato", "pumpkin", "squash"), "{x} is a source of vitamin A (beta-carotene)."),
    (("tomato",), "{x} adds vitamin C."),
    (("lime", "lemon", "calamansi", "orange"), "{x} adds vitamin C and acidity."),
    (("brown rice", "oat", "barley", "whole wheat"), "{x} is a whole grain with fiber."),
    (("rice", "noodle", "potato", "bread", "flour"), "{x} is the main carbohydrate (energy) of the dish."),
    (("coconut milk", "coconut cream"), "{x} makes the dish rich but is high in saturated fat."),
    (("yogurt", "kimchi", "natto", "tempeh"), "{x} is a fermented food."),
    (("peanut", "cashew", "almond", "sesame"), "{x} adds healthy fats but also calories."),
    (("eggplant", "okra", "bean sprout", "green bean", "string bean", "bell pepper", "onion"), "{x} adds fiber."),
    (("sugar", "palm sugar", "condensed milk", "honey"), "{x} adds sweetness (added sugar)."),
]

DB_PATH = None
_get_prompt = None
_client = None
_model = None


def init(data_dir, get_prompt, client, model):
    global DB_PATH, _get_prompt, _client, _model
    DB_PATH = os.path.join(data_dir, "cuisync.db")
    _get_prompt, _client, _model = get_prompt, client, model
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS cultural_info (cache_key TEXT PRIMARY KEY, dish TEXT, "
                     "info TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")


def _ingredients(recipe):
    return [t.strip().lower() for t in str(recipe.get("ingredient_text") or "").split(",") if t.strip()]


def _has(text, word):
    return re.search(r"\b" + re.escape(word) + r"(s|es)?\b", text) is not None


def find_allergens(recipe):
    found = []
    for name, rule in ALLERGENS.items():
        triggers = []
        for ing in _ingredients(recipe):
            # Take out the look-alike phrases first ("coconut milk" is not milk)
            rest = ing
            for phrase in sorted(rule["not"], key=len, reverse=True):
                rest = re.sub(r"\b" + re.escape(phrase) + r"(s|es)?\b", " ", rest)
            if any(_has(rest, h) for h in rule["hit"]):
                triggers.append(ing)
        if triggers:
            found.append({"name": name, "from": sorted(set(triggers))})
    return found


def _template_notes(recipe, region_label, country_label):
    title = (recipe.get("title") or "").strip()
    alt = (recipe.get("alternative_title") or "").strip()
    named = f"{title} ({alt})" if alt and alt.lower() != title.lower() else title
    region = recipe.get("region")
    culture = f"{named} is a dish from {country_label}, part of the cuisines of {region_label}."
    if region and str(region).lower() not in ("none", "no specific"):
        culture += f" It is linked to the {region} region."
    health, used = [], set()
    for ing in _ingredients(recipe):
        for words, note in NUTRITION_NOTES:
            if any(_has(ing, w) for w in words):
                # Only the first rule that fits this ingredient, and each note once
                if note not in used:
                    health.append(note.format(x=ing.capitalize()))
                    used.add(note)
                break
        if len(health) == 4:
            break
    return {
        "culture": culture,
        "history": "No verified history has been added for this dish yet.",
        "health": health,
    }


def _cache_key(payload, prompt):
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False) + "|" + prompt + "|" + str(_model)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ask_gpt(payload, prompt):
    key = _cache_key(payload, prompt)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT info FROM cultural_info WHERE cache_key = ?", (key,)).fetchone()
    if row:
        return json.loads(row[0])
    resp = _client.chat.completions.create(
        model=_model,
        messages=[{"role": "system", "content": prompt},
                  {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        temperature=0.2,
        max_tokens=350,
        response_format={"type": "json_object"},
    )
    info = json.loads(resp.choices[0].message.content)
    info = {
        "culture": str(info.get("culture", "")).strip(),
        "history": str(info.get("history", "")).strip(),
        "health": [str(h).strip() for h in (info.get("health") or []) if str(h).strip()][:4],
    }
    if info["culture"]:
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO cultural_info (cache_key, dish, info) VALUES (?, ?, ?)",
                         (key, payload["dish"], json.dumps(info, ensure_ascii=False)))
    return info


def dish_info(recipe, region_label, country_label):
    payload = {
        "dish": (recipe.get("title") or "").strip(),
        "alternative_name": (recipe.get("alternative_title") or "").strip(),
        "country": country_label,
        "region": region_label,
        "ingredients": _ingredients(recipe),
    }
    info, source = None, "template"
    if _client is not None:
        try:
            info = _ask_gpt(payload, _get_prompt())
            source = "ai"
        except Exception as exc:  # GPT down / bad JSON -> template
            print("[CuiSync] cultural info GPT failed:", type(exc).__name__, exc)
            info = None
    if not info or not info.get("culture"):
        info, source = _template_notes(recipe, region_label, country_label), "template"
    # Notes typed by the admin always win
    admin_fields = []
    if (recipe.get("culture_notes") or "").strip():
        info["culture"] = recipe["culture_notes"].strip()
        admin_fields.append("culture")
    if (recipe.get("history_notes") or "").strip():
        info["history"] = recipe["history_notes"].strip()
        admin_fields.append("history")
    return {
        "title": payload["dish"],
        "country": country_label,
        "region": region_label,
        "culture": info["culture"],
        "history": info["history"],
        "health": info["health"],
        "allergens": find_allergens(recipe),
        "allergen_groups": list(ALLERGENS),   # every group, so the page can show all of them
        "nutrition": nutrition_template(recipe),
        "source": source,
        "admin_fields": admin_fields,
    }


# ---------------- Country connection (overview page) ----------------
# TEMPLATE VERSION. The overview page shows a "Historical & cultural connection" note
# between the origin country and the target country (or only the origin country when the
# target is "Any"). It will be written by GPT with the prompt below; until that is wired
# up, country_connection() builds a neutral note from the dataset instead, and the page
# labels it as a template preview.
CONNECTION_PROMPT = """You write short notes for CuiSync, a system that compares Asian dishes.
You get one JSON object with an origin country and, optionally, a target country, plus the
ingredients and cooking methods that are common in each country's dishes in the dataset.
If there is a target country, write 2 short paragraphs on the historical and cultural
connection between the two cuisines (trade, migration, shared influences, colonial history)
and how it shows in their food. If there is no target country, write 2 short paragraphs on
the culinary history and heritage of the origin country only.
Rules: be neutral and respectful, do not rank cuisines, say plainly when history is uncertain,
keep each paragraph under 90 words, return ONLY a JSON object {"paragraphs": ["...", "..."]}."""

_METHODS = {"Simmer", "Boil", "Fry", "Stir-Fry", "Deep-Fry", "Pan-Fry", "Steam", "Grill", "Roast",
            "Bake", "Braise", "Stew", "Marinate", "Ferment", "Blanch", "Poach", "Saute", "Toast",
            "Smoke", "Pickle", "Broil", "Sear"}
_TOO_COMMON = {"Salt", "Water", "Oil", "Vegetable Oil", "Cooking Oil", "Pepper", "Black Pepper",
               "Sugar", "Ice", "Hot Water", "Cold Water", "Neutral Oil"}


def _join(words):
    words = [w.lower() for w in words]
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _country_profile(recipes, country, parse_items):
    from collections import Counter
    dishes = [r for r in recipes if (r.get("country") or "").upper() == country]
    ings, acts = Counter(), Counter()
    for r in dishes:
        ings.update(i for i in set(parse_items(r.get("ingredient_text", ""))) if i not in _TOO_COMMON)
        acts.update(a for a in set(parse_items(r.get("action_text", ""))) if a in _METHODS)
    return {
        "count": len(dishes),
        "ingredients": [n for n, _ in ings.most_common(12)],
        "methods": [n for n, _ in acts.most_common(5)],
    }


def country_connection(recipes, origin, target, label_of, region_of, parse_items, dish_title=""):
    """Note for the overview page. target=None means "Any" (origin country only).
    Returns {"title", "subtitle", "paragraphs", "source", "prompt"}."""
    origin = (origin or "").upper()
    a_label, a_region = label_of(origin), region_of(origin)
    a = _country_profile(recipes, origin, parse_items)

    if not target or target.upper() == origin:
        paragraphs = [
            f"{dish_title or 'This dish'} comes from {a_label}, part of {a_region}. "
            f"{a_label} is represented by {a['count']} dishes in the CuiSync dataset.",
            (f"Across those dishes, {a_label} cooking most often builds on "
             f"{_join(a['ingredients'][:5])}, and its recipes commonly "
             f"{_join(a['methods'][:3]) or 'combine'} their ingredients. "
             f"This section will describe the history and heritage of {a_label}'s cuisine "
             f"once it is generated from the AI prompt."),
        ]
        return {"title": a_label, "subtitle": "Culinary origin & heritage",
                "paragraphs": paragraphs, "source": "template", "prompt": CONNECTION_PROMPT}

    target = target.upper()
    b_label, b_region = label_of(target), region_of(target)
    b = _country_profile(recipes, target, parse_items)
    same_region = a_region == b_region
    shared = [i for i in a["ingredients"] if i in set(b["ingredients"])]
    shared_methods = [m for m in a["methods"] if m in set(b["methods"])]
    only_a = [i for i in a["ingredients"] if i not in set(b["ingredients"])][:3]
    only_b = [i for i in b["ingredients"] if i not in set(a["ingredients"])][:3]

    p1 = (f"{a_label} and {b_label} "
          + (f"are both part of {a_region}" if same_region
             else f"come from different parts of Asia ({a_region} and {b_region})")
          + f". The CuiSync dataset has {a['count']} dishes from {a_label} "
            f"and {b['count']} from {b_label}.")
    p2 = ""
    if shared:
        p2 += f"Both cuisines often use {_join(shared[:4])}"
        p2 += (f", and both commonly {_join(shared_methods[:3])} their dishes. " if shared_methods else ". ")
    if only_a and only_b:
        p2 += (f"{a_label} dishes stand out for {_join(only_a)}, while {b_label} dishes lean on "
               f"{_join(only_b)}. ")
    p2 += (f"This section will describe the historical and cultural connection between the two "
           f"countries once it is generated from the AI prompt.")
    return {"title": f"{a_label} & {b_label}", "subtitle": "Historical & cultural connection",
            "paragraphs": [p1, p2], "source": "template", "prompt": CONNECTION_PROMPT}


# ---------------- Nutrition (Health Info tab of the comparison page) ----------------
# TEMPLATE VERSION. The values (calories, grams, mg) will come from a nutrition API.
# Until then each row only says which of the dish's own ingredients drive it, and the
# value is left empty, so nothing is made up.
NUTRIENT_SOURCES = [
    ("Calories", None),   # needs the API (depends on amounts)
    ("Protein", ("chicken", "pork", "beef", "lamb", "mutton", "goat", "duck", "turkey", "fish", "salmon",
                 "tuna", "shrimp", "prawn", "squid", "crab", "clam", "mussel", "egg", "tofu", "tempeh",
                 "lentil", "chickpea", "bean", "dal", "paneer", "offal", "liver")),
    ("Fat", ("oil", "butter", "ghee", "lard", "coconut milk", "coconut cream", "cream", "cheese", "peanut",
             "cashew", "pork belly", "chicharon", "bacon", "sausage")),
    ("Carbohydrates", ("rice", "noodle", "vermicelli", "bread", "flour", "potato", "sweet potato", "sugar",
                       "corn", "pasta", "roti", "naan", "tapioca", "cassava")),
    ("Sodium", ("salt", "soy sauce", "fish sauce", "shrimp paste", "oyster sauce", "miso", "bagoong",
                "stock", "broth", "bouillon", "kimchi", "pickled")),
]


def nutrition_template(recipe):
    ings = _ingredients(recipe)
    rows = []
    for name, words in NUTRIENT_SOURCES:
        sources = [] if words is None else sorted({i for i in ings if any(_has(i, w) for w in words)})
        rows.append({"name": name, "value": None, "from": sources[:4]})
    return {"rows": rows, "source": "template"}
