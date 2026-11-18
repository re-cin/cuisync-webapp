"""Identity and freshness rules for the Supabase-only Dish Information Module."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit

DATA_VERSION = 1


def normalize(text):
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def country_key(text):
    return normalize(text).upper()


def ingredient_names(recipe):
    value = recipe.get("ingredient_text") or recipe.get("ingredient_canon") or ""
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = re.split(r"[,;\n]+", str(value))
    # Preparation qualifiers are kept: a salted food must not be treated as unsalted.
    return list(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))


def ingredient_hash(recipe):
    normalized = sorted({normalize(item) for item in ingredient_names(recipe)})
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False).encode("utf-8")).hexdigest()


def fresh(record, max_age_days=30):
    if not record or record.get("data_version") != DATA_VERSION:
        return False
    try:
        updated = datetime.fromisoformat(str(record["updated_at"]).replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - updated
        return -timedelta(minutes=5) <= age <= timedelta(days=max_age_days)
    except (KeyError, TypeError, ValueError):
        return False


def safe_url(value):
    value = str(value or "")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme in ("https", "http") and parsed.hostname
                and not parsed.username and not parsed.password):
            return value
    except ValueError:
        pass
    return ""
