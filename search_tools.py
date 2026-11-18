"""Extra search features for CuiSync (panel revisions, Galang 1, 4, 7 and Oab 6).

  keyword_search()     - every dish whose name contains the keyword (not just one)
  ingredient_search()  - dishes ranked by how well they cover the ingredients you have,
                         using the same ingredient vectors as the similarity score
  possible_cuisines()  - groups those dishes by country: which cuisines you could cook
  recipe_tags()        - tags for a dish (region, cooking method, main protein, dish type,
                         spicy, vegetarian, plus any tags the admin added)
"""
import difflib
import re
import unicodedata
from collections import Counter, defaultdict

import numpy as np


def _norm(text):
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).casefold().split())


def _items(text):
    return [t.strip().lower() for t in str(text or "").split(",") if t.strip()]


def _words(text):
    return set(re.findall(r"[a-z]+", _norm(text)))


# ---------------- tags ----------------
METHOD_TAGS = {
    "Grilled": {"grill", "barbecue", "broil", "char", "skewer"},
    "Stir-fried": {"stir-fry", "stir fry", "wok"},
    "Deep-fried": {"deep-fry", "deep fry"},
    "Fried": {"fry", "pan-fry", "pan fry", "shallow fry"},
    "Steamed": {"steam"},
    "Boiled": {"boil", "blanch", "parboil"},
    "Simmered": {"simmer", "poach"},
    "Braised": {"braise"},
    "Stewed": {"stew"},
    "Baked": {"bake"},
    "Roasted": {"roast", "toast"},
    "Marinated": {"marinate"},
    "Fermented": {"ferment"},
    "Smoked": {"smoke"},
    "Pickled": {"pickle"},
}
PROTEIN_TAGS = {
    "Chicken": {"chicken"},
    "Pork": {"pork", "bacon", "ham", "lard", "pancetta"},
    "Beef": {"beef", "oxtail", "brisket", "steak", "veal"},
    "Lamb & Goat": {"lamb", "mutton", "goat"},
    "Duck": {"duck"},
    "Seafood": {"shrimp", "prawn", "fish", "squid", "crab", "clam", "mussel", "scallop", "octopus",
                "lobster", "salmon", "tuna", "mackerel", "anchovy", "cod", "tilapia", "milkfish", "catfish"},
    "Egg": {"egg"},
    "Tofu": {"tofu"},
}
MEAT_WORDS = set().union(*(PROTEIN_TAGS[k] for k in PROTEIN_TAGS if k not in ("Egg", "Tofu"))) | {
    "meat", "sausage", "chorizo", "liver", "tripe", "gelatin", "bonito", "dashi", "fish sauce",
    "shrimp paste", "oyster sauce", "oyster", "belacan", "anchovies", "chicken stock", "beef stock",
    "bone broth", "broth"}
TYPE_TAGS = {
    "Noodles": {"noodle", "noodles", "pho", "ramen", "udon", "soba", "pancit", "mee", "mie", "laksa", "pad", "bihun", "japchae"},
    "Rice": {"rice", "biryani", "nasi", "pilaf", "congee", "jook", "bibimbap", "donburi", "onigiri", "sushi", "risotto"},
    "Soup": {"soup", "broth", "sinigang", "tom", "pho", "ramen", "laksa", "jjigae", "guk", "tang", "shorba", "corba", "soto"},
    "Curry": {"curry", "kari", "masala", "rendang", "korma", "vindaloo", "kare"},
    "Salad": {"salad", "som", "yum", "ensalada", "rojak", "gado", "kerabu"},
    "Dumplings & Buns": {"dumpling", "dumplings", "bao", "buns", "bun", "gyoza", "mandu", "momo", "siomai", "siopao", "wonton", "manti"},
    "Bread & Pastry": {"bread", "roti", "naan", "paratha", "pide", "simit", "pastry", "borek", "pancake", "crepe"},
    "Dessert": {"cake", "pudding", "dessert", "sweet", "halo", "mochi", "kulfi", "halwa", "baklava", "kheer",
                "cookie", "cookies", "jelly", "custard", "flan", "leche", "ice", "candy", "tart", "pie"},
    "Grilled Skewers": {"satay", "sate", "yakitori", "kebab", "kabob", "inasal", "skewer", "skewers"},
}
SPICY_WORDS = {"chili", "chilli", "chile", "chilies", "cayenne", "gochujang", "gochugaru", "sambal",
               "jalapeno", "bird's eye", "red pepper flake", "chili flake", "chili powder", "sriracha", "harissa"}

CONDIMENT_WORDS = {"sauce", "paste", "stock", "broth", "powder", "oil", "bouillon", "cube", "extract",
                   "seasoning", "granule", "granules", "essence", "fat", "dripping", "drippings"}

_TAG_CACHE = {}


def recipe_tags(recipe, region_of=None):
    key = (id(recipe), recipe.get("title"), recipe.get("ingredient_text"), recipe.get("action_text"),
           recipe.get("tags"), recipe.get("country"))
    if key in _TAG_CACHE:
        return _TAG_CACHE[key]
    tags = []
    region = recipe.get("country_region") or (region_of(recipe.get("country")) if region_of else "")
    if region:
        tags.append(region)

    actions = set(_items(recipe.get("action_text")))
    action_words = actions | {w for a in actions for w in a.split()}
    for tag, words in METHOD_TAGS.items():
        if action_words & words:
            tags.append(tag)
    if "Deep-fried" in tags and "Fried" in tags:
        tags.remove("Fried")

    ingredients = _items(recipe.get("ingredient_text"))
    # Condiments don't make a main protein ("fish sauce" is not a seafood dish)
    main_items = [i for i in ingredients if not set(i.split()) & CONDIMENT_WORDS]
    ing_words = {w for i in main_items for w in re.findall(r"[a-z]+", i)}
    for tag, words in PROTEIN_TAGS.items():
        if ing_words & words and not (tag == "Egg" and ing_words & {"eggplant"} and "egg" not in ing_words):
            tags.append(tag)

    title_words = _words(recipe.get("title")) | _words(recipe.get("alternative_title"))
    for tag, words in TYPE_TAGS.items():
        if title_words & words:
            tags.append(tag)

    text = ", ".join(ingredients)
    if any(w in text for w in SPICY_WORDS):
        tags.append("Spicy")
    if not any(re.search(r"\b" + re.escape(w) + r"s?\b", text) for w in MEAT_WORDS):
        tags.append("Vegetarian")

    for custom in _items(recipe.get("tags")):
        label = custom.strip().title()
        if label and label not in tags:
            tags.append(label)
    _TAG_CACHE[key] = tags
    return tags


def all_tags(recipes, region_of=None):
    counts = Counter()
    for r in recipes:
        counts.update(recipe_tags(r, region_of))
    groups = {
        "Region": [t for t in counts if t.endswith("Asia")],
        "Cooking method": [t for t in METHOD_TAGS if t in counts],
        "Main protein": [t for t in PROTEIN_TAGS if t in counts],
        "Dish type": [t for t in TYPE_TAGS if t in counts],
        "Diet & flavor": [t for t in ("Spicy", "Vegetarian") if t in counts],
    }
    used = {t for g in groups.values() for t in g}
    groups["Custom"] = sorted(t for t in counts if t not in used)
    return {g: [(t, counts[t]) for t in sorted(ts, key=lambda t: -counts[t]) if counts[t]]
            for g, ts in groups.items() if ts}, counts


def tag_search(recipes, tags, country="", region_of=None):
    wanted = [t for t in tags if t]
    out = []
    for idx, r in enumerate(recipes):
        if country and r["country"].lower() != country.lower():
            continue
        rt = recipe_tags(r, region_of)
        if all(t in rt for t in wanted):
            out.append((idx, 1.0, []))
    return out


# ---------------- keyword search ----------------
def keyword_search(recipes, query, country="", names_fn=None):
    """Every dish whose title or alternative title contains the keyword."""
    q = _norm(query)
    if not q:
        return []
    q_words = q.split()
    results = []
    for idx, r in enumerate(recipes):
        if country and r["country"].lower() != country.lower():
            continue
        names = names_fn(r) if names_fn else {_norm(r.get("title")), _norm(r.get("alternative_title"))}
        best = None
        for name in names:
            if not name:
                continue
            if name == q:
                rank = 0
            elif name.startswith(q):
                rank = 1
            elif re.search(r"\b" + re.escape(q), name):
                rank = 2
            elif q in name:
                rank = 3
            elif all(w in name for w in q_words):
                rank = 4
            else:
                continue
            best = rank if best is None else min(best, rank)
        if best is not None:
            results.append((idx, best))
    results.sort(key=lambda x: (x[1], _norm(recipes[x[0]].get("title")), recipes[x[0]]["country"]))
    if not results:
        # Nothing contains the keyword: suggest close spellings
        titles = {_norm(r.get("title")): i for i, r in enumerate(recipes)
                  if not country or r["country"].lower() == country.lower()}
        for t in difflib.get_close_matches(q, list(titles), n=12, cutoff=0.6):
            results.append((titles[t], 9))
    return [(idx, rank, []) for idx, rank in results]


# ---------------- ingredient search ----------------
class IngredientSearch:
    """Rank dishes by how well they cover a list of ingredients."""

    FLOOR = 0.35   # same floor as softmatch.py
    MATCH_MIN = 0.55  # an ingredient counts as "found" in a dish at this similarity or higher

    def __init__(self, matcher):
        self.items = [i.lower() for i in matcher.items["ingredients"]]
        self.index = {i: n for n, i in enumerate(self.items)}
        self.vecs = matcher.vecs["ingredients"]
        self.recipe_ids = matcher.recipe_ids["ingredients"]
        df = Counter(i for ids in self.recipe_ids for i in set(ids.tolist()))
        self.df = df

    def resolve(self, ingredient):
        """Known vocabulary items for what the user typed."""
        q = _norm(ingredient)
        if not q:
            return []
        if q in self.index:
            return [self.index[q]]
        singular = q[:-1] if q.endswith("s") else q
        if singular in self.index:
            return [self.index[singular]]
        # "chicken" -> the most common items that contain the word
        contains = [n for n, item in enumerate(self.items)
                    if re.search(r"\b" + re.escape(singular) + r"s?\b", item)]
        if contains:
            contains.sort(key=lambda n: (-self.df.get(n, 0), len(self.items[n])))
            return contains[:3]
        close = difflib.get_close_matches(q, self.items, n=1, cutoff=0.75)
        return [self.index[close[0]]] if close else []

    # Words after the ingredient that mean "made from it", not the ingredient itself
    # ("chicken stock", "fish sauce", "rice vinegar", "rice noodles", "egg noodles")
    MADE_FROM = CONDIMENT_WORDS | {"flour", "vinegar", "wine", "paper", "noodle", "noodles",
                                   "vermicelli", "starch", "cake", "cakes", "sauce", "paste",
                                   "plant", "roll", "wrap", "wrapper", "wrappers", "syrup",
                                   "ketchup", "soup"}

    def family(self, ingredient):
        """Vocabulary items that are the ingredient itself in another form or cut."""
        q = _norm(ingredient)
        q = q[:-1] if q.endswith("s") and q[:-1] in self.index else q
        if not q:
            return []
        pattern = re.compile(r"\b" + re.escape(q) + r"(?:s|es)?\b")
        out = []
        for n, item in enumerate(self.items):
            m = pattern.search(item)
            if not m:
                continue
            after = item[m.end():].split()
            if after and set(after) & self.MADE_FROM:
                continue
            out.append(n)
        return out

    def search(self, recipes, ingredients, country="", limit=60):
        resolved = [(ing, self.resolve(ing)) for ing in ingredients]
        known = [(ing, ids) for ing, ids in resolved if ids]
        unknown = [ing for ing, ids in resolved if not ids]
        if not known:
            return [], unknown
        q_vecs = np.stack([self.vecs[ids].mean(axis=0) / (np.linalg.norm(self.vecs[ids].mean(axis=0)) or 1)
                           for _, ids in known])
        sim_all = np.clip((q_vecs @ self.vecs.T - self.FLOOR) / (1 - self.FLOOR), 0, 1)
        # exact vocabulary hits always count as a full match, and so do cuts/forms of the
        # ingredient ("chicken" also finds "chicken thigh", "chicken legs", "ground pork")
        for row, (ing, ids) in enumerate(known):
            sim_all[row, ids] = 1.0
            family = self.family(ing)
            if family:
                sim_all[row, family] = 1.0
        results = []
        for idx, r in enumerate(recipes):
            if country and r["country"].lower() != country.lower():
                continue
            ids = self.recipe_ids[idx] if idx < len(self.recipe_ids) else []
            if len(ids) == 0:
                continue
            sims = sim_all[:, ids]
            best = sims.max(axis=1)
            coverage = float(best.mean())
            if coverage <= 0:
                continue
            found = []
            for row, (ing, _) in enumerate(known):
                if best[row] >= self.MATCH_MIN:
                    found.append((ing, self.items[ids[int(sims[row].argmax())]]))
            if not found:
                continue
            # small bonus for dishes that don't need many other ingredients
            focus = len(found) / max(len(set(ids.tolist())), 1)
            score = 0.9 * coverage + 0.1 * focus
            results.append((idx, score, found))
        results.sort(key=lambda x: (-len(x[2]), -x[1]))
        return results[:limit], unknown


def possible_cuisines(recipes, results, label_of=None, top=5):
    """Which cuisines (countries) you could cook from the chosen ingredients (Oab 6)."""
    by_country = defaultdict(list)
    for idx, score, found in results:
        by_country[recipes[idx]["country"]].append((idx, score, found))
    cuisines = []
    for country, rows in by_country.items():
        rows.sort(key=lambda x: (-len(x[2]), -x[1]))
        best = rows[:3]
        cuisines.append({
            "country": country,
            "label": label_of(country) if label_of else country.title(),
            "dish_count": len(rows),
            "score": round(100 * sum(s for _, s, _ in best) / len(best)),
            "examples": [recipes[i]["title"].strip() for i, _, _ in best],
            "example_ids": [i for i, _, _ in best],
        })
    cuisines.sort(key=lambda c: (-c["score"], -c["dish_count"]))
    return cuisines[:top]
