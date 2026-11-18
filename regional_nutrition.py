"""Regional Asian food-composition registry and strict, optional evidence lookup.

No automatic scraping or speculative values. Regional nutrient rows are used
only after an exact ingredient/food match, documented source and human approval.
When unavailable, CuiSync retains its existing verified USDA/fallback behavior.
"""
import logging
import math
from urllib.parse import urlsplit
from dish_info_common import DATA_VERSION, normalize

log = logging.getLogger(__name__)

SOURCES = {
    "PHILFCT": {
        "country": "PHILIPPINES", "name": "Philippine Food Composition Tables (DOST-FNRI)",
        "url": "https://i.fnri.dost.gov.ph/login/fct", "hosts": ("i.fnri.dost.gov.ph",),
    },
    "THAI_FCD": {
        "country": "THAILAND", "name": "Thai Food Composition Database (Mahidol INMU)",
        "url": "https://inmu.mahidol.ac.th/thaifcd/home", "hosts": ("inmu.mahidol.ac.th",),
    },
    "MEXT_JP": {
        "country": "JAPAN", "name": "Standard Tables of Food Composition in Japan (MEXT)",
        "url": "https://www.mext.go.jp/a_menu/syokuhinseibun/mext_00001.html", "hosts": ("www.mext.go.jp", "fooddb.mext.go.jp"),
    },
    "RDA_KR": {
        "country": "SOUTH KOREA", "name": "Korean National Standard Food Composition Database (RDA)",
        "url": "https://koreanfood.rda.go.kr/", "hosts": ("koreanfood.rda.go.kr", "www.rda.go.kr"),
    },
    "ICMR_IFCT": {
        "country": "INDIA", "name": "Indian Food Composition Tables (ICMR-NIN)",
        "url": "https://www.nin.res.in/ebooks/IFCT2017.pdf", "hosts": ("www.nin.res.in", "www.icmr.gov.in"),
    },
}

# FAO/INFOODS indexes additional country sources; it is NOT itself a source of
# ingredient-level numerical values in this integration.
ASIAN_DIRECTORY = "https://www.fao.org/food-composition/tables-and-databases/en/"

# Shared nutrient names follow the existing dish_health._NUTRIENTS convention.
NUTRIENT_FIELDS = {
    "protein_g": ("protein", "g"),
    "fiber_g": ("fiber", "g"),
    "iron_mg": ("iron", "mg"),
    "calcium_mg": ("calcium", "mg"),
    "potassium_mg": ("potassium", "mg"),
    "vitamin_c_mg": ("vitamin_c", "mg"),
    "sodium_mg": ("sodium", "mg"),
    "saturated_fat_g": ("saturated_fat", "g"),
}


def reviewed_import_row(data, approved=False):
    """Validate one manually prepared row against published table provenance.

    Never infer ingredient names or preparation, convert serving sizes to 100 g,
    fill missing nutrient values with zero, or invent food IDs.
    """
    source_code = str(data.get("source_code") or "").strip().upper()
    spec = SOURCES.get(source_code)
    if not spec:
        raise ValueError("Unrecognized country food-composition source")
    country = str(data.get("country") or "").strip().upper()
    if country != spec["country"]:
        raise ValueError("Food source and country must match")
    ingredient = str(data.get("ingredient_name") or "").strip()
    food_name = str(data.get("food_name") or "").strip()
    food_id = str(data.get("food_id") or "").strip()
    if not ingredient or not food_name or not food_id or len(food_id) > 100:
        raise ValueError("An exact reviewed ingredient, source food name and food ID are required")
    if normalize(ingredient) != normalize(food_name):
        raise ValueError("Ingredient must exactly match the source food name; review differences separately")
    url = str(data.get("source_url") or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ValueError("Invalid source URL") from None
    if (parts.scheme != "https" or parts.hostname not in spec["hosts"]
            or parts.username or parts.password):
        raise ValueError("Use a direct HTTPS link to the named official source")
    if str(data.get("basis") or "per_100g").strip() != "per_100g":
        raise ValueError("Only per-100-g composition is supported")
    values = {}
    for field, (nutrient, unit) in NUTRIENT_FIELDS.items():
        raw = str(data.get(field) or "").strip()
        if not raw:
            continue  # Missing is missing, never zero.
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"Non-numeric nutrient: {field}") from None
        if not math.isfinite(value) or value < 0 or value > (100 if unit == 'g' else 100000):
            raise ValueError(f"Implausible per-100-g nutrient: {field}")
        values[nutrient] = {"value": value, "unit": unit}
    if not values:
        raise ValueError("At least one measured/compiled nutrient value is needed")
    return {
        "country": country, "ingredient_name": ingredient,
        "normalized_name": normalize(ingredient), "food_name": food_name,
        "food_id": food_id, "source_code": source_code,
        "source_url": url, "data_source": spec['name'],
        "nutrients": {"basis": "per_100g", "food_description": food_name, "values": values},
        "approved": bool(approved), "data_version": DATA_VERSION,
    }


def eligible_regional_record(row, name, country):
    """Reject partial, mismatched, unapproved or malformed regional data."""
    if not isinstance(row, dict) or row.get('approved') is not True:
        return None
    if str(row.get('country', '')).upper() != str(country or '').upper():
        return None
    if normalize(row.get('normalized_name')) != normalize(name):
        return None
    if row.get('data_version') != DATA_VERSION:
        return None
    try:
        spec = SOURCES[row.get('source_code')]
        url = urlsplit(row.get('source_url') or '')
        if (spec['country'] != country or url.scheme != 'https' or url.hostname not in spec['hosts']
                or not row.get('food_id') or normalize(row.get('food_name')) != normalize(name)):
            return None
        nutrition = row.get('nutrients') or {}
        values = nutrition.get('values') or {}
        if nutrition.get('basis') != 'per_100g' or not isinstance(values, dict) or not values:
            return None
        for nutrient, item in values.items():
            if nutrient not in {n for n, _ in NUTRIENT_FIELDS.values()}:
                return None
            unit = next(u for n, u in NUTRIENT_FIELDS.values() if n == nutrient)
            if item.get('unit') != unit or not isinstance(item.get('value'), (float, int)):
                return None
            if not math.isfinite(item['value']) or item['value'] < 0:
                return None
        return {
            'match_status': 'verified', 'match_reason': 'exact_reviewed_regional_identity',
            'nutrients': nutrition, 'source_url': row['source_url'],
            'food_data_source': row['data_source'], 'source_food_id': row['food_id'],
        }
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


def approved_regional_records(store, names, country):
    """One batch lookup per recipe; silently fall back to USDA if not installed."""
    finder = getattr(store, 'regional_nutrition_matches', None)
    if not callable(finder) or str(country or '').upper() not in {v['country'] for v in SOURCES.values()}:
        return {}
    try:
        records = finder([normalize(x) for x in names], country) or []
    except Exception:
        log.warning('Regional composition lookup failed; retaining standard nutrition matching.')
        return {}
    return {key: value for row in records
            if (key := normalize(row.get('normalized_name')))
            if (value := eligible_regional_record(row, key, country))}
