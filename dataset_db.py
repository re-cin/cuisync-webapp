"""Saves the admin-editable dataset in Supabase (used by dataset_store.py).

Table (made by supabase_dataset.sql):
  public.recipes  - one row per dish: recipe_id, position (display order) and one column per
                    dish field (title, country, ingredient_text, ...). Any other field a dish
                    has (for example from an uploaded file) goes in the "extra" JSON column.
Only the dishes are in Supabase; the country list and GPT prompts stay in admin_data/.

Uses the same Supabase settings and helpers as accounts.py (service key, server side only).
If Supabase is not set up, or supabase_dataset.sql has not been run yet, available() is False
and dataset_store.py keeps using the admin_data/ folder like before.
"""
import accounts
from accounts import SupabaseError

BATCH = 300  # rows per request when uploading many dishes

# The dish fields that have their own column in public.recipes (see supabase_dataset.sql)
COLUMNS = (
    "title", "alternative_title", "country", "region", "country_region", "recipe_link", "image_link",
    "ingredient_text", "action_text", "cookware_text", "utensil_text",
    "ingredient_canon", "action_canon", "cookware_canon", "utensil_canon",
    "tags", "culture_notes", "history_notes",
)
NULLABLE = {"region"}  # the only field that is empty (None) in recipes.json
SELECT = "recipe_id," + ",".join(COLUMNS) + ",extra"


def available():
    """True when the dishes can be saved in Supabase.

    Checks three things: the Supabase keys are in .env, the recipes table exists, and it has
    the column layout this file expects (asks for one row with every column). If anything is
    missing it prints what to do and returns False, and the app saves to admin_data/ instead.
    """
    if not accounts.URL:
        accounts.init()
    if not accounts.configured():
        return False
    try:
        accounts._rest("GET", "recipes", params={"select": SELECT, "limit": "1"})
        return True
    except SupabaseError:
        print("[CuiSync] The recipes table is not in Supabase yet (or is an older version): run "
              "supabase_dataset.sql in the "
              "Supabase SQL Editor. Until then admin edits are saved in admin_data/ (local only).")
        return False


# ---------------- recipes ----------------
def load_recipes():
    """All dishes in display order (by position), as dicts shaped like recipes.json.
    Returns [] if the table is empty. Reads 1000 rows per request, because Supabase sends
    at most 1000 rows at a time."""
    out, start = [], 0
    while True:
        rows = accounts._rest("GET", "recipes", params={
            "select": SELECT, "order": "position.asc,recipe_id.asc",
            "offset": str(start), "limit": "1000"}).json()
        for row in rows:
            out.append(_dish(row))
        if len(rows) < 1000:
            return out
        start += 1000


def max_position():
    """The highest "position" in the table (-1 when empty). A new dish gets the next one,
    so it is shown and matched after all the others.
    """
    rows = accounts._rest("GET", "recipes", params={
        "select": "position", "order": "position.desc", "limit": "1"}).json()
    return rows[0]["position"] if rows else -1


def _dish(row):
    """Table row -> dish dict (the same shape as in recipes.json).
    Empty text columns come back as "" (region stays None, like in recipes.json), and any
    fields kept in the "extra" column are put back as normal dish fields."""
    dish = {"recipe_id": row["recipe_id"]}
    for col in COLUMNS:
        value = row.get(col)
        dish[col] = value if value is not None or col in NULLABLE else ""
    dish.update(row.get("extra") or {})
    return dish


def _fields(dish):
    """Dish dict -> table columns (without recipe_id / position).
    Every known field becomes text in its own column; fields that have no column go into
    "extra" (JSON), so nothing from an uploaded file is lost."""
    out = {}
    for col in COLUMNS:
        value = dish.get(col)
        if value is None and col not in NULLABLE:
            value = ""
        out[col] = None if value is None else str(value)
    out["extra"] = {k: v for k, v in dish.items() if k not in COLUMNS and k != "recipe_id"}
    return out


def _row(dish, position):
    """A full table row for a new dish: its fields plus recipe_id and position."""
    return dict(_fields(dish), recipe_id=int(dish["recipe_id"]), position=int(position))


def upsert_recipes(dishes_with_positions):
    """Insert dishes, or update them if their recipe_id already exists ("upsert").
    dishes_with_positions: list of (dish, position). Sent in batches of BATCH rows."""
    rows = [_row(d, p) for d, p in dishes_with_positions]
    for i in range(0, len(rows), BATCH):
        accounts._rest("POST", "recipes", params={"on_conflict": "recipe_id"}, json=rows[i:i + BATCH],
                       prefer="resolution=merge-duplicates,return=minimal")


def update_recipes(dishes):
    """Save edited dishes: one update per dish, matched by recipe_id. Their position (order)
    is not touched."""
    for d in dishes:
        accounts._rest("PATCH", "recipes", params={"recipe_id": f"eq.{int(d['recipe_id'])}"},
                       json=_fields(d),
                       prefer="return=minimal")


def delete_recipe(recipe_id):
    """Delete one dish row by its recipe_id."""
    accounts._rest("DELETE", "recipes", params={"recipe_id": f"eq.{int(recipe_id)}"}, prefer="return=minimal")


def replace_all_recipes(dishes):
    """Replace the whole dataset (admin upload with 'replace', or reset to the original):
    delete every row, then insert the new dishes with positions 0, 1, 2, ..."""
    accounts._rest("DELETE", "recipes", params={"recipe_id": "not.is.null"}, prefer="return=minimal")
    upsert_recipes([(d, i) for i, d in enumerate(dishes)])
