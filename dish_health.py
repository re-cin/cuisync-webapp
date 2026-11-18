"""Recipe-specific, source-backed nutrition and allergen notes, cached in Supabase.

Ingredient names have no quantities. USDA composition is kept as internal per-100-g
evidence only; this module never computes dish totals or serving-level claims.
Only approved stored allergen rules are used. An unmatched rule is unknown, never
evidence that an ingredient or dish is free of allergens.
"""
import logging
import math
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from dish_info_common import DATA_VERSION, fresh, ingredient_hash, ingredient_names, normalize, safe_url
from nutritional_benefits import qualitative_protein_benefits, _group_priority, PROTEIN_IDENTITIES
from regional_nutrition import approved_regional_records
from ingredient_matching import (MATCHER_VERSION, audit_match, canonical_identity,
                                 query_for, select_food)

HEALTH_ENRICHMENT_VERSION = 4

log = logging.getLogger(__name__)
USDA_SEARCH = "https://api.nal.usda.gov/fdc/v1/foods/search"
USDA_GUIDE = "https://fdc.nal.usda.gov/api-guide/"
FDA_ALLERGENS = "https://www.fda.gov/food/buy-store-serve-safe-food/food-allergies-what-you-need-know"
FSA_ALLERGENS = "https://www.food.gov.uk/business-guidance/allergen-guidance-for-food-businesses"
FSA_COMPOUNDS = "https://www.food.gov.uk/sites/default/files/media/document/food-allergies.pdf"
SOY_REFERENCE = ("https://www.fda.gov/food/nutrition-food-labeling-and-critical-foods/"
                 "questions-and-answers-gluten-free-food-labeling-final-rule")
SAUCE_REFERENCE = "https://www.cuh.nhs.uk/patient-information/cows-milk-egg-wheat-and-soya-mews-free-diet/"
PROTEIN_REFERENCE = "https://www.accessdata.fda.gov/scripts/interactivenutritionfactslabel/protein.cfm"
FIBER_REFERENCE = "https://www.fda.gov/food/nutrition-food-labeling-and-critical-foods/questions-and-answers-dietary-fiber"
SODIUM_REFERENCE = "https://www.accessdata.fda.gov/scripts/interactivenutritionfactslabel/sodium.cfm"
FAT_REFERENCE = "https://www.accessdata.fda.gov/scripts/interactivenutritionfactslabel/saturated-fat.cfm"

# Source-linked seed rules. Exact aliases deliberately avoid substring mistakes
# such as eggplant -> egg, coconut milk -> dairy, or rice flour -> wheat.
# Compound formulations vary: only the named food is identified; additional
# plausible ingredients remain possible and require the manufacturer's label.
_RULES = [
    ("milk", ["Milk"], [], ["whole milk", "cow milk", "cows milk", "skim milk", "milk powder"], FDA_ALLERGENS),
    ("butter", ["Milk"], [], ["ghee", "clarified butter"], FSA_COMPOUNDS),
    ("cream", ["Milk"], [], ["heavy cream", "double cream"], FSA_COMPOUNDS),
    ("cheese", ["Milk"], [], ["paneer", "cheddar", "parmesan", "cream cheese"], FSA_COMPOUNDS),
    ("yogurt", ["Milk"], [], ["yoghurt", "greek yogurt"], FDA_ALLERGENS),
    ("egg", ["Egg"], [], ["eggs", "egg white", "egg whites", "egg yolk", "egg yolks", "chicken egg"], FDA_ALLERGENS),
    ("mayonnaise", [], ["Egg"], ["mayo"], FSA_COMPOUNDS),
    ("peanut", ["Peanuts"], [], ["peanuts", "groundnut", "groundnuts", "peanut butter"], FDA_ALLERGENS),
    ("tree nuts", ["Tree nuts"], [], ["almond", "almonds", "cashew", "cashews", "walnut", "walnuts",
                                    "hazelnut", "hazelnuts", "pecan", "pecans", "pistachio", "pistachios",
                                    "macadamia", "macadamia nuts", "brazil nuts"], FSA_ALLERGENS),
    ("soybean", ["Soy"], [], ["soybeans", "soya beans", "soy beans", "edamame", "tofu", "tempeh",
                            "miso", "natto", "soy milk", "soya milk", "soy protein"], SAUCE_REFERENCE),
    ("soy sauce", ["Soy"], ["Wheat"], ["soya sauce", "light soy sauce", "dark soy sauce", "tamari"], SOY_REFERENCE),
    ("wheat", ["Wheat"], [], ["wheat flour", "wheat gluten", "semolina", "bulgur", "couscous"], FSA_ALLERGENS),
    ("flour", [], ["Wheat"], ["all purpose flour", "plain flour"], FSA_COMPOUNDS),
    ("bread crumbs", [], ["Wheat"], ["breadcrumbs", "panko", "bread"], FSA_COMPOUNDS),
    ("barley", ["Cereals containing gluten"], [], ["barley flour", "barley malt", "rye", "rye flour", "oats"], FSA_ALLERGENS),
    ("fish", ["Fish"], [], ["salmon", "tuna", "cod", "tilapia", "milkfish", "anchovy", "anchovies",
                           "sardine", "sardines", "mackerel", "bonito", "katsuobushi"], FDA_ALLERGENS),
    ("fish sauce", ["Fish"], [], [], FSA_COMPOUNDS),
    ("shrimp", ["Crustaceans"], [], ["shrimps", "prawn", "prawns", "crab", "lobster", "crayfish",
                                    "shrimp paste", "prawn paste", "belacan", "bagoong alamang"], FSA_COMPOUNDS),
    ("oyster", ["Molluscs"], [], ["oysters", "mussel", "mussels", "clam", "clams", "scallop", "scallops",
                                "squid", "octopus", "cuttlefish"], FSA_ALLERGENS),
    ("oyster sauce", ["Molluscs"], ["Soy", "Wheat"], [], FSA_COMPOUNDS),
    ("sesame", ["Sesame"], [], ["sesame seed", "sesame seeds", "tahini"], FDA_ALLERGENS),
    ("sesame oil", [], ["Sesame"], [], FDA_ALLERGENS),
    ("mustard", ["Mustard"], [], ["mustard seed", "mustard seeds", "mustard powder"], FSA_COMPOUNDS),
    ("celery", ["Celery"], [], ["celeriac", "celery seed", "celery seeds", "celery salt"], FSA_COMPOUNDS),
    ("lupin", ["Lupin"], [], ["lupin flour", "lupine", "lupine flour"], FSA_ALLERGENS),
    ("curry paste", [], ["Crustaceans", "Mustard", "Wheat"], ["red curry paste", "green curry paste",
                                                           "yellow curry paste", "thai curry paste"], FSA_COMPOUNDS),
    ("bagoong", [], ["Fish", "Crustaceans"], [], FSA_COMPOUNDS),
    ("stock", [], ["Celery", "Wheat"], ["stock cube", "stock cubes", "bouillon"], FSA_COMPOUNDS),
    ("peanut oil", [], ["Peanuts"], ["groundnut oil"], FDA_ALLERGENS),
    ("soybean oil", [], ["Soy"], ["soy oil", "soya oil"], SAUCE_REFERENCE),
    ("sulphites", [], ["Sulphites"], ["sulfites", "sulphur dioxide", "sulfur dioxide",
                                     "sodium metabisulphite", "sodium metabisulfite"], FSA_ALLERGENS),
]

# Additional name rules use sources identifying the allergen food itself or
# manufacturer ingredient declarations. A manufacturer's example establishes
# a *possible* formulation, never every product of that sauce/paste category.
FSS_ALLERGENS = "https://www.foodstandards.gov.scot/business-guidance/running-a-food-business/managing-allergens"
KIKKOMAN_HOISIN = "https://kikkomanusa.com/foodmanufacturers/products/hoisin-sauce/"
_RULES += [
    ("soy curd", ["Soy"], [], ["bean curd", "firm tofu", "silken tofu", "soft tofu", "fried tofu", "tofu puff",
                               "tofu puffs", "tofu skin", "bean curd skin", "yuba", "aburaage"], FSS_ALLERGENS),
    ("soy flour", ["Soy"], [], ["soya flour", "kinako", "roasted soybean flour", "soybean flour"], FSS_ALLERGENS),
    ("soy sauce seasoning", [], ["Soy", "Wheat"], ["soy sauce powder", "seasoning sauce", "liquid seasoning"], SAUCE_REFERENCE),
    ("fermented soybean paste", ["Soy"], ["Wheat"], ["doenjang", "doenjang paste", "fermented soy paste", "soybean paste",
                                                     "soy bean paste", "yellow bean paste"], SAUCE_REFERENCE),
    ("miso seasoning", ["Soy"], [], ["miso paste", "white miso", "red miso", "shiro miso", "aka miso"], FSS_ALLERGENS),
    ("fermented black bean sauce", [], ["Soy", "Wheat"], ["black bean sauce", "black bean garlic sauce", "douchi sauce"], SAUCE_REFERENCE),
    ("fermented black soybeans", ["Soy"], [], ["douchi", "fermented black soybean", "salted black soybean",
                                             "salted black soybeans"], FSS_ALLERGENS),
    ("hoisin sauce", [], ["Soy", "Wheat"], ["hoisin"], KIKKOMAN_HOISIN),
    ("teriyaki sauce", [], ["Soy", "Wheat"], ["teriyaki marinade"], SAUCE_REFERENCE),
    ("chili bean paste", [], ["Soy", "Wheat"], ["doubanjiang", "toban djan", "chilli bean paste", "chili bean sauce"], SAUCE_REFERENCE),
    ("korean chili paste", [], ["Soy", "Wheat"], ["gochujang", "gochujang paste", "korean chilli paste"], "https://www.bibigousa.com/products/shop-gochujang-sauce-hot-sweet"),
    ("korean dipping paste", [], ["Soy", "Wheat", "Sesame"], ["ssamjang"], FSS_ALLERGENS),
    ("peanut sauce", ["Peanuts"], ["Soy", "Wheat"], ["satay sauce", "satay peanut sauce"], FDA_ALLERGENS),
    ("sesame paste", ["Sesame"], [], ["sesame seed paste", "zhi ma jiang", "white sesame seed", "white sesame seeds",
                                    "black sesame seed", "black sesame seeds", "roasted sesame seed", "toasted sesame seeds"], FDA_ALLERGENS),
    ("dried shrimp", ["Crustaceans"], [], ["dried shrimps", "dried prawn", "dried prawns", "ebi", "sakura ebi",
                                         "shrimp powder", "shrimp stock", "shrimp broth"], FSS_ALLERGENS),
    ("fermented shrimp", ["Crustaceans"], [], ["fermented shrimp paste", "saeujeot", "saeu jeot", "gapi", "kapi",
                                             "terasi", "trassi", "alamang"], FSS_ALLERGENS),
    ("fermented fish", ["Fish"], [], ["fermented fish paste", "prahok", "padaek", "pla ra", "ngapi fish",
                                     "bagoong isda", "bagoong monamon"], FDA_ALLERGENS),
    ("dried fish", ["Fish"], [], ["dried anchovy", "dried anchovies", "ikan bilis", "dilis", "niboshi", "iriko",
                                  "bonito flakes", "dried bonito", "fish powder", "anchovy powder", "fish stock", "fish broth"], FSS_ALLERGENS),
    ("fish roe", ["Fish"], [], ["salmon roe", "ikura", "tobiko", "masago", "mentaiko", "tarako"], FDA_ALLERGENS),
    ("fish cake", ["Fish"], ["Egg", "Wheat", "Soy"], ["fish cakes", "fish ball", "fish balls", "surimi",
                                                      "imitation crab", "kamaboko", "narutomaki", "chikuwa", "eomuk"], FSS_ALLERGENS),
    ("seafood seasoning", [], ["Fish", "Crustaceans", "Molluscs"], ["seafood stock", "seafood broth", "seafood paste",
                                                                  "jeotgal", "seafood seasoning powder"], FSS_ALLERGENS),
    ("dashi seasoning", [], ["Fish", "Soy", "Wheat"], ["dashi", "dashi stock", "dashi stock granules", "instant dashi",
                                                      "dashi powder", "hondashi"], FSS_ALLERGENS),
    ("anchovy sauce", ["Fish"], [], ["anchovy fish sauce", "aekjeot", "myeolchi aekjeot", "nuoc mam", "nam pla", "patis"], FDA_ALLERGENS),
    ("mollusc seasoning", ["Molluscs"], [], ["oyster extract", "oyster powder", "clam juice", "clam stock", "squid ink"], FSS_ALLERGENS),
    ("egg noodle", ["Egg"], ["Wheat"], ["egg noodles", "fresh egg noodles"], FSS_ALLERGENS),
    ("asian wheat noodles", [], ["Wheat", "Egg"], ["ramen", "ramen noodles", "udon", "udon noodles", "somen",
                                                  "somen noodles", "wonton wrapper", "wonton wrappers", "dumpling wrapper"], FSS_ALLERGENS),
    ("soba noodles", [], ["Wheat"], ["soba", "buckwheat noodles"], FSS_ALLERGENS),
    ("panko coating", [], ["Wheat"], ["panko breadcrumbs", "japanese breadcrumbs", "tempura batter", "tempura flour"], FSS_ALLERGENS),
    ("condensed milk", ["Milk"], [], ["sweetened condensed milk", "evaporated milk", "milk solids", "skimmed milk powder",
                                     "buttermilk", "whey powder", "milk protein", "casein", "caseinate"], FDA_ALLERGENS),
    ("nut paste", [], ["Tree nuts", "Peanuts"], ["mixed nut paste", "nut butter"], FDA_ALLERGENS),
    ("cashew paste", ["Tree nuts"], [], ["cashew butter", "almond paste", "almond butter", "almond flour",
                                       "cashew nut", "cashew nuts", "pistachio nuts", "walnut pieces"], FDA_ALLERGENS),
    ("mustard oil", [], ["Mustard"], ["cold pressed mustard oil"], FSS_ALLERGENS),
    ("seasoning blend", [], ["Celery", "Wheat", "Soy"], ["chicken stock cube", "chicken stock powder", "bouillon powder",
                                                        "chicken bouillon", "seasoning powder"], FSS_ALLERGENS),
    # FDA Seafood List validates the squid/calamari naming equivalent:
    # https://hfpappexternal.fda.gov/scripts/Fdcc/index.cfm?id=Sepioteuthis_sepioidea&set=SeafoodList
    ("calamari", ["Molluscs"], [], ["squid meat", "squid rings"], FSA_ALLERGENS),
    ("crab meat", ["Crustaceans"], [], ["crabmeat", "fresh crab meat"], FSA_ALLERGENS),
    ("soba noodle", [], ["Wheat"], ["buckwheat noodle"], FSS_ALLERGENS),
]


# Explicit single-ingredient assessments against the FSA major-allergen list.
# This category is limited to unmodified named ingredients, not allergy safety.
_RULES += [(name, [], [], aliases, FSA_ALLERGENS) for name, aliases in [
    ("water", ["drinking water", "tap water"]), ("salt", ["table salt", "sea salt", "kosher salt"]),
    ("sugar", ["white sugar", "granulated sugar"]), ("carrot", ["carrots"]),
    ("potato", ["potatoes"]), ("onion", ["onions"]), ("garlic", []),
    ("ginger", ["fresh ginger", "ginger root"]), ("rice", ["white rice", "brown rice"])
]]
_RULES += [
    ("milk ingredient", ["Milk"], [], ["full fat milk", "skimmed milk", "whole milk powder", "dried milk", "powdered milk"], FDA_ALLERGENS),
    ("butter ingredient", ["Milk"], [], ["unsalted butter", "salted butter", "melted butter"], FSA_COMPOUNDS),
    ("shrimp ingredient", ["Crustaceans"], [], ["raw shrimp", "cooked shrimp", "peeled shrimp", "fresh shrimp", "jumbo shrimp", "tiger prawn", "king prawn"], FSA_COMPOUNDS),
    ("almond ingredient", ["Tree nuts"], [], ["ground almond", "ground almonds", "sliced almond", "slivered almond", "blanched almond", "roasted almond"], FDA_ALLERGENS),
    ("egg ingredient", ["Egg"], [], ["beaten egg", "boiled egg", "duck egg", "quail egg"], FDA_ALLERGENS),
    ("soy ingredient", ["Soy"], [], ["soy", "soya", "soy bean", "soybean sprout"], FDA_ALLERGENS),
    ("wheat noodle", ["Wheat"], [], ["wheat noodles", "wheat noodle", "wheat bread"], FSA_ALLERGENS),
    ("sesame ingredient", ["Sesame"], [], ["roasted sesame seeds", "toasted sesame seed", "ground sesame"], FDA_ALLERGENS),
]

_RULES += [
    ("fresh milk", ["Milk"], [], ["full cream milk"], FDA_ALLERGENS),
    ("almond meal", ["Tree nuts"], [], ["almond slivers"], FDA_ALLERGENS),
    ("frozen shrimp", ["Crustaceans"], [], ["baby shrimp", "sakura shrimp", "dried shrimp paste"], FSA_COMPOUNDS),
    ("asian fish sauce", ["Fish"], [], ["vietnamese fish sauce"], FSA_COMPOUNDS),
    ("white fish", ["Fish"], [], ["fish fillet", "white fish fillet", "white flaky fish", "fish sausage", "fish paste stick"], FDA_ALLERGENS),
    ("fish bouillon cube", [], ["Fish", "Wheat", "Celery"], ["fish curry powder"], FSA_COMPOUNDS),
    ("sweet soy sauce", ["Soy"], ["Wheat"], ["indonesian sweet soy sauce", "low sodium soy sauce", "mushroom soy sauce", "tamari soy sauce"], SOY_REFERENCE),
    ("fried egg", ["Egg"], [], ["pickled egg"], FDA_ALLERGENS),
    ("broken wheat", ["Wheat"], [], ["bulgur wheat", "wheat baguette", "wheat bread crumbs", "whole wheat vermicelli"], FSA_ALLERGENS),
    ("wheat starch", [], ["Wheat"], [], SOY_REFERENCE),
    ("egg roll wrappers", [], ["Egg", "Wheat"], ["egg roll wrap"], FSA_COMPOUNDS),
    ("white sesame paste", ["Sesame"], [], ["sesame tahini"], FDA_ALLERGENS),
    ("sesame seed oil", [], ["Sesame"], ["asian sesame oil"], FDA_ALLERGENS),
    ("sesame seed kaiser roll", ["Sesame"], ["Wheat"], [], FSA_COMPOUNDS),
]

_COMPOUND_SOURCES = {
    ("oyster sauce", "Soy"): SAUCE_REFERENCE,
    ("oyster sauce", "Wheat"): "https://uk.lkk.com/products/premium-oyster-sauce",
    ("curry paste", "Crustaceans"): "https://data.food.gov.uk/codes/alerts/def/allergen/crustaceans",
    ("curry paste", "Wheat"): SAUCE_REFERENCE,
}

_NUTRIENTS = {
    1003: ("protein", "g"), 1079: ("fiber", "g"), 1087: ("calcium", "mg"),
    1089: ("iron", "mg"), 1092: ("potassium", "mg"), 1093: ("sodium", "mg"),
    1162: ("vitamin_c", "mg"), 1258: ("saturated_fat", "g"),
}
_BENEFITS = [
    ("protein", 1, "protein, which helps maintain and repair body tissues", PROTEIN_REFERENCE),
    ("fiber", 1, "dietary fiber, which supports regular bowel movements", FIBER_REFERENCE),
    ("iron", 0.5, "iron, which the body uses for oxygen transport", "https://ods.od.nih.gov/factsheets/Iron-Consumer/"),
    ("calcium", 20, "calcium, which supports bone structure", "https://ods.od.nih.gov/factsheets/Calcium-Consumer/"),
    ("potassium", 100, "potassium, which supports normal nerve and muscle function", "https://ods.od.nih.gov/factsheets/Potassium-Consumer/"),
    ("vitamin_c", 5, "vitamin C, which the body uses to make collagen", "https://ods.od.nih.gov/factsheets/VitaminC-Consumer/"),
]
_SINGULAR = {"carrots": "carrot", "tomatoes": "tomato", "onions": "onion", "potatoes": "potato",
             "eggs": "egg", "peanuts": "peanut", "almonds": "almond", "cashews": "cashew",
             "beans": "bean", "peas": "pea", "lentils": "lentil", "peppers": "pepper",
             "mushrooms": "mushroom", "shrimps": "shrimp", "prawns": "prawn"}
_usda_retry_until = 0.0
_usda_circuit_reason = "rate_limited"
_usda_rate_remaining = None


def seed_allergen_rules(store):
    """Insert source-validated seed rules once, preserving existing/admin-reviewed rows.

    Possible compound components are precautionary candidates, not assertions
    about an individual product. The source-linked rules can be revised or
    withdrawn (approved=false) in Supabase without being overwritten here.
    """
    existing = {r.get("normalized_name") for r in store.list("ingredient_allergens")}
    count = 0
    for name, identified, possible, aliases, source in _RULES:
        key = normalize(name)
        if key in existing:
            continue
        allergens = ([{"name": n, "classification": "identified"} for n in identified]
                     + [{"name": n, "classification": "possible"} for n in possible])
        for allergen in allergens:
            reference = _COMPOUND_SOURCES.get((name, allergen["name"]))
            if reference:
                allergen["source_url"] = reference
        store.upsert("ingredient_allergens", {
            "ingredient_name": name, "normalized_name": key, "allergens": allergens,
            "classification": "identified" if identified else "possible" if possible else "assessed_none",
            "source_url": source, "aliases": aliases, "approved": True, "data_version": DATA_VERSION,
        }, on_conflict="normalized_name")
        count += 1
    return count


def _match_key(name):
    # Never strip meaningful variety, processing, fortification, or cooking terms.
    # Raw/fresh denote the default unprepared ingredient reference, not a cooked dish.
    return tuple(sorted(_SINGULAR.get(w, w) for w in normalize(name).split()
                        if w not in {"raw", "fresh", "uncooked"}))


def _select_food(name, foods):
    return select_food(name, foods)


def _nutrients(food):
    values = {}
    for n in food.get("foodNutrients") or []:
        if not isinstance(n, dict):
            continue
        nested = n.get("nutrient") or {}
        nutrient_id = n.get("nutrientId", nested.get("id"))
        try:
            spec = _NUTRIENTS.get(int(nutrient_id))
            value = float(n.get("value", n.get("amount")))
        except (TypeError, ValueError, OverflowError):
            continue
        unit = str(n.get("unitName", nested.get("unitName", ""))).lower()
        if spec and unit == spec[1] and math.isfinite(value) and value >= 0:
            values[spec[0]] = {"value": value, "unit": spec[1]}
    return {"basis": "per_100g", "food_description": food.get("description", ""), "values": values}


def _nutrition_record(name, store, refresh=False):
    global _usda_retry_until, _usda_circuit_reason, _usda_rate_remaining
    key = normalize(name)
    cached = store.get("ingredient_nutrition", {"normalized_name": key})
    if getattr(store, "cached_nutrition_only", False) is True:
        current_version = (cached or {}).get("match_status") == "verified" or ((cached or {}).get("nutrients") or {}).get("matcher_version") == MATCHER_VERSION
        if cached and current_version and fresh(cached, max_age_days=30):
            return cached
        return {"ingredient_name": name, "normalized_name": key, "match_status": "error",
                "match_reason": "cached_data_stale" if cached else "cached_data_missing",
                "nutrients": {"matcher_version": MATCHER_VERSION}, "data_version": DATA_VERSION}
    api_key = os.environ.get("USDA_API_KEY", "").strip()
    if cached and not refresh:
        age = 1 / 24 if cached.get("match_status") == "error" else 30
        missing_key_fixed = cached.get("match_reason") == "missing_api_key" and bool(api_key)
        current_negative = (cached.get("match_status") == "verified"
                            or (cached.get("nutrients") or {}).get("matcher_version") == MATCHER_VERSION)
        if not missing_key_fixed and current_negative and fresh(cached, max_age_days=age):
            return cached
    query = query_for(name)
    find_equivalent = getattr(store, "find_equivalent_nutrition", None)
    equivalent = find_equivalent(canonical_identity(name)) if callable(find_equivalent) and not refresh else None
    if equivalent and equivalent.get("match_status") == "verified" and fresh(equivalent):
        row = {k: equivalent[k] for k in ("nutrients", "food_data_source", "source_food_id", "source_url", "match_status", "match_reason", "data_version") if k in equivalent}
        row.update(ingredient_name=name, normalized_name=key)
        row["nutrients"] = dict(row.get("nutrients") or {}, matcher_version=MATCHER_VERSION,
                                input_name=name, query=query, reused_equivalent=True)
        return store.upsert("ingredient_nutrition", row, on_conflict="normalized_name") or row
    row = {"ingredient_name": name, "normalized_name": key,
           "nutrients": {"matcher_version": MATCHER_VERSION, "input_name": name, "query": query},
           "food_data_source": "USDA FoodData Central", "source_food_id": "",
           "source_url": USDA_GUIDE, "match_status": "error", "match_reason": "",
           "data_version": DATA_VERSION}
    if not api_key:
        row["match_reason"] = "missing_api_key"
    elif time.monotonic() < _usda_retry_until:
        row["match_reason"] = _usda_circuit_reason
    else:
        for attempt in range(1, 4):
            retry_delay = min(2 ** (attempt - 1), 10)
            try:
                response = requests.post(USDA_SEARCH, params={"api_key": api_key}, json={
                    "query": query, "dataType": ["Foundation", "SR Legacy", "Survey (FNDDS)"], "pageSize": 50,
                }, timeout=(3, 8))
                status = response.status_code
                try:
                    _usda_rate_remaining = int(response.headers.get("X-RateLimit-Remaining"))
                except (TypeError, ValueError, AttributeError):
                    _usda_rate_remaining = None
                if _usda_rate_remaining == 0:
                    _usda_retry_until = time.monotonic() + _quota_reset_delay(response)
                    _usda_circuit_reason = "rate_limited"
                if status == 429:
                    retry_delay = _retry_after(response, default=_quota_reset_delay(response))
                    _usda_retry_until = time.monotonic() + retry_delay
                    _usda_circuit_reason = "rate_limited"
                    row["match_reason"] = "rate_limited"
                    row["nutrients"]["retry_after_seconds"] = retry_delay
                    _nutrition_event(store, key, "error", "rate_limited", query, attempt, status)
                    if retry_delay > 10 or attempt == 3:
                        break
                elif status in (401, 403):
                    row["match_reason"] = "invalid_api_key" if status == 401 else "upstream_forbidden"
                    _usda_circuit_reason = row["match_reason"]
                    _usda_retry_until = time.monotonic() + _retry_after(response, default=60)
                    _nutrition_event(store, key, "error", row["match_reason"], query, attempt, status)
                    break
                elif status >= 500:
                    row["match_reason"] = "temporary_server_error"
                    _nutrition_event(store, key, "error", row["match_reason"], query, attempt, status)
                    if attempt == 3:
                        break
                elif status >= 400:
                    row["match_reason"] = "upstream_unavailable"
                    _nutrition_event(store, key, "error", row["match_reason"], query, attempt, status)
                    break
                else:
                    body = response.json()
                    foods = body.get("foods", []) if isinstance(body, dict) else []
                    food = _select_food(name, foods) if isinstance(foods, list) else None
                    if food is None:
                        row.update(match_status="ambiguous" if foods else "unavailable",
                                   match_reason="no_validated_identity_match" if foods else "no_results")
                        row["nutrients"]["candidate_count"] = len(foods) if isinstance(foods, list) else 0
                    else:
                        evidence = _nutrients(food)
                        evidence.update(audit_match(name, query, food))
                        if evidence["values"]:
                            row.update(nutrients=evidence, source_food_id=str(food["fdcId"]),
                                       source_url=f"https://fdc.nal.usda.gov/food-details/{food['fdcId']}/nutrients",
                                       match_status="verified", match_reason="validated_generic_identity")
                        else:
                            row.update(match_status="unavailable", match_reason="nutrients_missing")
                    _nutrition_event(store, key, "success" if row["match_status"] == "verified" else "unmatched",
                                     row["match_reason"], query, attempt, status)
                    break
            except (requests.RequestException, ValueError, TypeError, AttributeError):
                # Never log requests exceptions: their URLs can contain the API key.
                row["match_reason"] = "temporary_api_error"
                _nutrition_event(store, key, "error", row["match_reason"], query, attempt)
                if attempt == 3:
                    break
            time.sleep(retry_delay)
    return store.upsert("ingredient_nutrition", row, on_conflict="normalized_name") or row


def _retry_after(response, default=60):
    try:
        value = response.headers.get("Retry-After")
        if value is None:
            return default
        try:
            return max(0, float(value))
        except ValueError:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, AttributeError):
        return default


def _quota_reset_delay(response):
    try:
        value = float(response.headers.get("X-RateLimit-Reset") or response.headers.get("RateLimit-Reset"))
        return max(1, value - time.time() if value > 1_000_000_000 else value)
    except (TypeError, ValueError, AttributeError):
        return 60


def _nutrition_event(store, key, status, reason, query, attempt, http_status=None):
    recorder = getattr(store, "record_event", None)
    if callable(recorder):
        recorder("nutrition_lookup", key, status, reason=reason, provider="usda",
                 details={"http_status": http_status, "attempts": attempt, "query": query,
                          "matcher_version": MATCHER_VERSION, "api_error_count": 1 if status == "error" else 0,
                          "quota_remaining": _usda_rate_remaining})


def reset_usda_circuit():
    global _usda_retry_until, _usda_circuit_reason, _usda_rate_remaining
    _usda_retry_until = 0.0
    _usda_circuit_reason = "rate_limited"
    _usda_rate_remaining = None


def usda_retry_seconds():
    return max(0.0, _usda_retry_until - time.monotonic())


def usda_quota_remaining():
    return _usda_rate_remaining


def health_cache_valid(row):
    if not row:
        return False
    coverage = next((note for note in row.get("health_considerations", [])
                     if isinstance(note, dict) and note.get("type") == "coverage"), {})
    age = 30 if row.get("nutrition_status") == "verified" else 1 / 24
    return coverage.get("matcher_version") == HEALTH_ENRICHMENT_VERSION and fresh(row, max_age_days=age)


def nutrition_reason_label(reason):
    return {
        "no_results": "No USDA food was found for this name",
        "no_validated_identity_match": "USDA candidates did not match this ingredient's identity or preparation",
        "no_unique_exact_generic_match": "No validated ingredient equivalent was available",
        "nutrients_missing": "The matched food had no usable nutrient values",
        "rate_limited": "Lookup deferred by a USDA rate limit",
        "invalid_api_key": "USDA credentials were rejected",
        "upstream_forbidden": "USDA denied the request",
        "temporary_server_error": "USDA server errors persisted after retries",
        "temporary_api_error": "Network or response errors persisted after retries",
        "upstream_unavailable": "USDA could not complete the lookup",
        "missing_api_key": "A USDA API key is not configured",
        "unusable_nutrients": "Stored nutrient evidence could not be validated",
    }.get(reason, str(reason).replace("_", " "))


def _allergens(names, rules):
    lookup = {}
    for rule in rules:
        if rule.get("approved") is not True or rule.get("data_version") != DATA_VERSION:
            continue
        for alias in [rule.get("normalized_name", ""), *(rule.get("aliases") or [])]:
            if normalize(alias):
                lookup.setdefault(normalize(alias), []).append(rule)
    grouped, unknown = {}, []
    for ingredient in names:
        matches = lookup.get(normalize(ingredient), [])
        covered = False
        for rule in matches:
            source = safe_url(rule.get("source_url"))
            if not source or rule.get("classification") == "unknown":
                continue
            if rule.get("classification") == "assessed_none" and not rule.get("allergens"):
                covered = True
                group = grouped.setdefault(("No identified major allergens", "assessed_none", source), {
                    "name": "No identified major allergens", "classification": "assessed_none", "from": [], "source_url": source})
                if ingredient not in group["from"]:
                    group["from"].append(ingredient)
            for item in rule.get("allergens") or []:
                classification = item.get("classification")
                if classification not in {"identified", "possible"} or not item.get("name"):
                    continue
                item_source = safe_url(item.get("source_url")) or source
                covered = True
                group = grouped.setdefault((item["name"], classification, item_source), {
                    "name": item["name"], "classification": classification, "from": [], "source_url": item_source,
                })
                if ingredient not in group["from"]:
                    group["from"].append(ingredient)
        if not covered:
            unknown.append(ingredient)
    if unknown:
        grouped[("Unassessed allergens", "unknown", FSA_ALLERGENS)] = {
            "name": "Unassessed allergens", "classification": "unknown", "from": unknown,
            "source_url": FSA_ALLERGENS,
        }
    return list(grouped.values()), unknown


def _recipe_id(recipe):
    value = recipe.get("recipe_id", recipe.get("id"))
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _unavailable(recipe, names, reason):
    return {"recipe_id": _recipe_id(recipe), "ingredient_hash": ingredient_hash(recipe),
            "potential_benefits": [], "health_considerations": [{"text": reason}, {
                "text": "Ingredient names cannot establish allergen safety. Check product labels and preparation details.",
                "source_url": FDA_ALLERGENS}], "potential_allergens": [],
            "sources": [{"title": "FDA: Food allergies", "url": FDA_ALLERGENS}],
            "verification_status": "unavailable", "nutrition_status": "unavailable",
            "matched_ingredient_count": 0, "ingredient_count": len(names), "data_version": DATA_VERSION}


def enrich_health(recipe, store, refresh=False, refreshed_ingredients=None):
    """Return/cache notes for this exact stored recipe and ingredient list.

    Supabase failures stop enrichment before upstream API calls. Transient USDA
    failures are short-lived negative records; an explicit refresh retries them.
    The health status covers deterministic notes, while nutrition_status separately
    reports missing matches. Neither status certifies a dish's allergy safety.
    """
    names = ingredient_names(recipe)
    recipe_id = _recipe_id(recipe)
    if recipe_id is None:
        return _unavailable(recipe, names, "Health information is unavailable until this recipe has a stored numeric recipe ID.")
    filters = {"recipe_id": recipe_id, "ingredient_hash": ingredient_hash(recipe)}
    try:
        cached = store.get("dish_health_info", filters)
        if cached and not refresh:
            if health_cache_valid(cached):
                return cached
        rules = store.list("ingredient_allergens", {"approved": True})
        allergens, unknown = _allergens(names, rules)
        benefits, considerations, sources, matched = [], [], {}, 0
        regional = approved_regional_records(store, names, str(recipe.get("country") or "").upper())
        matched_names, unmatched_names = [], []

        def source(title, url):
            if url:
                sources[url] = {"title": title, "url": url}

        source("FDA: Food allergies", FDA_ALLERGENS)
        for allergen in allergens:
            source("Allergen ingredient reference", allergen["source_url"])
        for ingredient in names:
            normalized = normalize(ingredient)
            refresh_ingredient = refresh and (refreshed_ingredients is None or normalized not in refreshed_ingredients)
            record = regional.get(normalized) or _nutrition_record(ingredient, store, refresh=refresh_ingredient)
            if refreshed_ingredients is not None:
                refreshed_ingredients.add(normalized)
            if record.get("match_status") != "verified":
                unmatched_names.append({"ingredient": ingredient, "status": record.get("match_status", "unavailable"),
                                        "reason": record.get("match_reason", "no_match")})
                continue
            nutrients = record.get("nutrients") or {}
            values = nutrients.get("values") or {}
            if nutrients.get("basis") != "per_100g" or not values:
                unmatched_names.append({"ingredient": ingredient, "status": "unavailable", "reason": "unusable_nutrients"})
                continue
            matched += 1
            matched_names.append(ingredient)
            is_regional = record.get("match_reason") == "exact_reviewed_regional_identity"
            source(("Official regional food composition: " if is_regional else "USDA FoodData Central: ")
                   + ingredient, record.get("source_url"))
            # Choose useful nutrients by food role, not simply protein first.
            # This avoids highlighting tiny protein contributions from bay leaves,
            # garlic, or soy sauce above the dish's principal ingredients.
            role = _group_priority(ingredient)
            options = (_BENEFITS if normalize(ingredient) in PROTEIN_IDENTITIES
                       else [item for item in _BENEFITS if item[0] != "protein"])
            if role >= 45:
                for nutrient, threshold, function, reference in options:
                    evidence = values.get(nutrient) or {}
                    if isinstance(evidence.get("value"), (int, float)) and evidence["value"] >= threshold:
                        benefits.append({"ingredient": ingredient, "nutrient": nutrient,
                                         "evidence_type": "regional_per_100g" if is_regional else "usda_per_100g",
                                         "text": f"{ingredient} may contribute {function}. "
                                                 "Its contribution depends on the amount used and preparation.",
                                         "source_url": reference})
                        source("Nutrient function reference", reference)
                        break  # One relevant potential contribution per ingredient.
            for nutrient, threshold, text, reference in [
                ("sodium", 100, "contains sodium; consider the amount used and check product labels", SODIUM_REFERENCE),
                ("saturated_fat", 1, "can contribute saturated fat; the amount depends on preparation and portion size", FAT_REFERENCE),
            ]:
                evidence = values.get(nutrient) or {}
                if isinstance(evidence.get("value"), (int, float)) and evidence["value"] >= threshold:
                    considerations.append({"ingredient": ingredient, "text": f"{ingredient} {text}.", "source_url": reference})
                    source("Nutrient consideration reference", reference)
        # Add reference-backed food-group notes for unambiguous protein foods
        # that lack a sufficiently specific USDA food-composition match.
        # These are qualitative; matched_ingredient_count remains unchanged.
        existing_qualitative = {normalize(n.get("ingredient")) for n in benefits
                                if n.get("evidence_type") == "qualitative_food_group"}
        for note in qualitative_protein_benefits(recipe):
            if normalize(note["ingredient"]) not in existing_qualitative:
                benefits.append(note)
                source("USDA: Protein foods", note["source_url"])
        nutrition_status = "verified" if names and matched == len(names) else "partial" if matched else "unavailable"
        considerations.append({
            "type": "coverage", "matcher_version": HEALTH_ENRICHMENT_VERSION,
            "matched_ingredients": matched_names, "unmatched_ingredients": unmatched_names,
            "unknown_allergen_ingredients": unknown,
            "text": (f"Nutritional matches: {matched} of {len(names)} ingredients. "
                     + ("Still unmatched: " + "; ".join(f"{r['ingredient']} ({nutrition_reason_label(r['reason'])})" for r in unmatched_names) + "."
                        if unmatched_names else "Every listed ingredient has a validated reference match.")),
        })
        if matched < len(names) or not names:
            considerations.append({"text": f"Verified nutritional matches are available for {matched} of {len(names)} ingredients. "
                                   "Nutritional benefits for unmatched ingredients are unavailable."})
        if unknown:
            considerations.append({"text": "Allergen coverage is unknown for: " + ", ".join(unknown) + "."})
        if any("gluten free" in normalize(ingredient) for ingredient in names):
            considerations.append({
                "text": "A gluten-free claim does not necessarily mean wheat-free. Check the declared ingredients for wheat allergy.",
                "source_url": SOY_REFERENCE,
            })
            source("FDA: Gluten-free labeling and soy sauce", SOY_REFERENCE)
        considerations.extend([
            {"text": "Ingredient quantities are unavailable, so dish totals and nutrients per serving cannot be calculated."},
            {"text": "Ingredient names cannot establish allergen safety or cross-contact risk. Check product labels and preparation details.",
             "source_url": FDA_ALLERGENS},
        ])
        if any(a["classification"] == "possible" for a in allergens):
            considerations.append({"text": "Compound ingredient formulations vary. Possible allergens need confirmation from the actual product label."})
        row = dict(filters, potential_benefits=benefits, health_considerations=considerations,
                   potential_allergens=allergens, sources=list(sources.values()),
                   verification_status="verified" if matched or any(a["classification"] != "unknown" for a in allergens) else "unavailable",
                   nutrition_status=nutrition_status, matched_ingredient_count=matched,
                   ingredient_count=len(names), data_version=DATA_VERSION)
        from allergen_assessment import allergen_patch
        row.update(allergen_patch(recipe, row, rules))
        return store.upsert("dish_health_info", row, on_conflict="recipe_id,ingredient_hash") or row
    except Exception:
        # Store/client exceptions can contain credentials or private response data.
        log.warning("Dish health information could not be retrieved or persisted.")
        return _unavailable(recipe, names, "Dish health information is temporarily unavailable. Please try again later.")
