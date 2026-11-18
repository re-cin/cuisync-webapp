"""Conservative USDA food identity matching, with auditable Asian name equivalents.

No fuzzy score is an acceptance rule. Preparation, variety and formulation terms
remain significant unless an explicit equivalent below documents the identity.
"""
from dish_info_common import normalize

MATCHER_VERSION = 2
USDA_REFERENCE = "https://fdc.nal.usda.gov/api-guide/"
DAIKON_REFERENCE = "https://www.ams.usda.gov/grades-standards/daikon-inspection-instructions"

# USDA descriptions themselves document most of these name equivalents (e.g.
# scallion / green onion; pe-tsai / napa; coriander / cilantro). Daikon names are
# also documented by USDA AMS. Specific products/varieties never share a key
# merely because they are nutritionally similar.
_GROUPS = {
    "potato": (["potato", "potatoes", "potatoes flesh and skin raw"], "potatoes flesh and skin raw", USDA_REFERENCE),
    "shrimp": (["shrimp", "shrimps", "crustaceans shrimp raw"], "crustaceans shrimp raw", USDA_REFERENCE),
    "almond": (["almond", "almonds", "nuts almonds"], "nuts almonds", USDA_REFERENCE),
    "lemongrass": (["lemongrass", "lemon grass", "lemon grass citronella raw"], "lemon grass citronella raw", USDA_REFERENCE),
    "coriander seed": (["coriander powder", "ground coriander", "coriander seed", "coriander seeds", "spices coriander seed"], "spices coriander seed", USDA_REFERENCE),
    "napa cabbage": (["napa chinese cabbage", "napa cabbage", "pe tsai", "pe tsai cabbage",
                      "cabbage chinese pe tsai raw"],
                     "cabbage chinese pe tsai raw", USDA_REFERENCE),
    "bok choy": (["bok choy", "bok choi", "pak choi", "pak choy", "pakchoi", "bokchoy",
                  "cabbage chinese pak choi raw"], "cabbage chinese pak choi raw", USDA_REFERENCE),
    "scallion": (["scallion", "scallions", "green onion", "green onions", "spring onion", "spring onions",
                  "onions spring or scallions includes tops and bulb raw",
                  "green onion scallion bulb and greens root removed raw"], "scallions raw", USDA_REFERENCE),
    "daikon": (["daikon", "daikon radish", "japanese radish", "chinese radish", "oriental radish", "mooli",
                "radishes oriental raw"], "radishes oriental raw", DAIKON_REFERENCE),
    "cilantro": (["cilantro", "coriander leaf", "coriander leaves", "fresh coriander",
                  "coriander cilantro leaves raw"], "coriander cilantro leaves raw", USDA_REFERENCE),
    "ginger": (["ginger", "fresh ginger", "ginger root", "fresh ginger root", "ginger root raw"],
               "ginger root raw", USDA_REFERENCE),
    "green chili": (["green chili", "green chilli", "green chilies", "green chillies",
                     "green chili pepper", "green chilli pepper", "peppers hot chili green raw"],
                    "peppers hot chili green raw", USDA_REFERENCE),
    "ghee": (["ghee", "clarified butter", "ghee clarified butter", "butter clarified butter ghee"],
             "ghee", USDA_REFERENCE),
    "salt": (["salt", "table salt", "salt table"], "salt table", USDA_REFERENCE),
    "sugar": (["sugar", "white sugar", "granulated sugar", "white granulated sugar", "sugars granulated"],
              "sugars granulated", USDA_REFERENCE),
    "water": (["water", "drinking water"], "water", USDA_REFERENCE),
    "tap water": (["tap water", "water tap", "water tap drinking", "beverages water tap drinking"],
                  "water tap drinking", USDA_REFERENCE),
    "tamari": (["tamari", "tamari soy sauce", "soy sauce made from soy tamari"], "tamari", USDA_REFERENCE),
    "shoyu": (["shoyu", "shoyu soy sauce", "soy sauce made from soy and wheat shoyu"], "shoyu", USDA_REFERENCE),
    "fish sauce": (["fish sauce", "nam pla", "nuoc mam", "patis", "sauce fish ready to serve"],
                   "fish sauce", USDA_REFERENCE),
    "egg": (["egg", "eggs", "whole egg", "whole eggs", "egg whole raw fresh"], "egg whole raw fresh", USDA_REFERENCE),
    "olive oil": (["olive oil", "oil olive salad or cooking"], "olive oil", USDA_REFERENCE),
    "sesame oil": (["sesame oil", "oil sesame salad or cooking"], "sesame oil", USDA_REFERENCE),
    "peanut oil": (["peanut oil", "groundnut oil", "oil peanut salad or cooking"], "peanut oil", USDA_REFERENCE),
    "cumin": (["cumin", "cumin seed", "cumin seeds", "ground cumin", "cumin powder", "spices cumin seed"],
              "spices cumin seed", USDA_REFERENCE),
    "cinnamon": (["cinnamon", "ground cinnamon", "cinnamon powder", "spices cinnamon ground"],
                 "spices cinnamon ground", USDA_REFERENCE),
    "turmeric ground": (["turmeric powder", "ground turmeric", "spices turmeric ground"], "spices turmeric ground", USDA_REFERENCE),
    "black pepper": (["black pepper", "ground black pepper", "peppercorn", "black peppercorn", "black peppercorns",
                      "spices pepper black"], "spices pepper black", USDA_REFERENCE),
    "ginger ground": (["ginger powder", "ground ginger", "spices ginger ground"], "spices ginger ground", USDA_REFERENCE),
    "rice vinegar": (["rice vinegar", "rice wine vinegar", "vinegar rice"], "vinegar rice", USDA_REFERENCE),
}

_PLURALS = {"carrots": "carrot", "tomatoes": "tomato", "onions": "onion", "potatoes": "potato",
            "eggs": "egg", "peanuts": "peanut", "almonds": "almond", "cashews": "cashew",
            "beans": "bean", "peas": "pea", "lentils": "lentil", "peppers": "pepper",
            "mushrooms": "mushroom", "shrimps": "shrimp", "prawns": "prawn", "sugars": "sugar",
            "radishes": "radish", "seeds": "seed", "spices": "spice", "leaves": "leaf"}
_PLURALS["shallots"] = "shallot"
_RAW = {"raw", "fresh", "uncooked", "unprepared"}
_LOOKUP = {normalize(alias): (key, query, reference)
           for key, (aliases, query, reference) in _GROUPS.items() for alias in aliases}


def canonical_identity(value):
    key = normalize(value)
    if key in _LOOKUP:
        return _LOOKUP[key][0]
    unprepared = " ".join(w for w in key.split() if w not in _RAW)
    if unprepared in _LOOKUP:
        return _LOOKUP[unprepared][0]
    words = [_PLURALS.get(w, w) for w in key.split()]
    # 'Spices' is a USDA category prefix, not part of the ingredient identity.
    if words[:1] == ["spice"]:
        words = words[1:]
    words = [w for w in words if w not in _RAW]
    return " ".join(sorted(words))


def query_for(value):
    key = normalize(value)
    return _LOOKUP[key][1] if key in _LOOKUP else str(value).strip()


def equivalence_source(value):
    return _LOOKUP.get(normalize(value), (None, None, USDA_REFERENCE))[2]


def select_food(name, foods):
    """Return one equivalent generic food; never choose a merely similar dish."""
    identity = canonical_identity(name)
    ranks = {"Foundation": 0, "SR Legacy": 1, "Survey (FNDDS)": 2}
    eligible = []
    for food in foods or []:
        if (not isinstance(food, dict) or food.get("dataType") not in ranks
                or food.get("brandOwner") or food.get("brandName")
                or not str(food.get("fdcId", "")).isdigit()):
            continue
        if canonical_identity(food.get("description", "")) == identity:
            eligible.append(food)
    if not eligible:
        return None
    # All candidates have the same preparation-sensitive identity. Prefer direct
    # composition data, then a stable ID to avoid rejecting duplicate samples.
    return min(eligible, key=lambda food: (ranks[food["dataType"]], int(food["fdcId"])))


def audit_match(name, query, food):
    return {"matcher_version": MATCHER_VERSION, "input_name": str(name), "query": str(query),
            "matched_description": str(food.get("description") or ""),
            "data_type": str(food.get("dataType") or ""),
            "canonical_identity": canonical_identity(name), "equivalence_source": equivalence_source(name),
            "selection": "preparation_sensitive_name_equivalence"}
