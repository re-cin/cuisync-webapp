"""Admin-editable data for CuiSync (panel revisions: admin module).

Holds everything the admin page can change without touching the original files:
  - the recipe dataset (add / edit / delete dishes, upload a new dataset)
  - the list of countries shown in the dropdowns (add / rename / hide / remove)
  - the GPT prompts (similarity explanation and cultural information)

The original recipes.json and the .npy files are never overwritten. Changes are saved
in Supabase (the dishes: table recipes, see supabase_dataset.sql and dataset_db.py; the
countries and GPT prompts stay in admin_data/), or all in admin_data/ when Supabase is not set up. The soft matcher is rebuilt from them:
  - dishes that were not changed keep their original item lists and vectors
  - a new ingredient (or action, cookware, utensil) that is already in the vocabulary
    reuses its vector
  - a brand-new item borrows the vector of the closest known item (by spelling or by
    shared words). Items with no close match get no vector, and the admin page says so,
    because a full update needs the vectorizer to be run again.
"""
import difflib
import json
import os
import re
import threading

import numpy as np

import dataset_db

FEATURES = ("ingredients", "actions", "cookware", "utensils")
TEXT_FIELD = {
    "ingredients": "ingredient_text",
    "actions": "action_text",
    "cookware": "cookware_text",
    "utensils": "utensil_text",
}

# Fields the admin form can edit, in the order they are shown
DISH_FIELDS = (
    "title", "alternative_title", "country", "region", "recipe_link", "image_link",
    "ingredient_text", "action_text", "cookware_text", "utensil_text",
    "tags", "culture_notes", "history_notes",
)

DEFAULT_REGIONS = {
    "CHINA": "East Asia", "JAPAN": "East Asia", "SOUTH KOREA": "East Asia",
    "CAMBODIA": "Southeast Asia", "INDONESIA": "Southeast Asia", "MALAYSIA": "Southeast Asia",
    "MYANMAR": "Southeast Asia", "PHILIPPINES": "Southeast Asia", "THAILAND": "Southeast Asia",
    "VIETNAM": "Southeast Asia", "INDIA": "South Asia",
    "SAUDI ARABIA": "West Asia", "TURKIYE": "West Asia",
}
DEFAULT_LABELS = {"TURKIYE": "Turkey"}
REGION_CHOICES = ("East Asia", "Southeast Asia", "South Asia", "Central Asia", "West Asia", "Other Asia")


def _split(text):
    """Turn "Garlic, Soy sauce , " into ["garlic", "soy sauce"] (comma list -> clean lower-case items)."""
    return [t.strip().lower() for t in str(text or "").split(",") if t.strip()]


def _dedupe(items):
    """Remove repeated items but keep the first-seen order."""
    return list(dict.fromkeys(items))


def _singular(word):
    """Very small plural -> singular rule ("tomatoes" -> "tomato", "cherries" -> "cherry",
    "onions" -> "onion"). Used only to find a known item with a slightly different spelling.
    """
    if len(word) > 3 and word.endswith("es") and word[:-2].endswith(("sh", "ch", "o", "x")):
        return word[:-2]
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _normal_item(item):
    """Make every word of an item singular, so "green onions" and "green onion" compare equal."""
    return " ".join(_singular(w) for w in item.split())


class DatasetStore:
    """Everything the admin page can change, kept in memory while the app runs.

    The app reads self.recipes / self.countries / self.prompts directly. Every change goes
    through a method here, which updates memory and saves it (Supabase or admin_data/).
    self.lock makes sure two admin requests never change the data at the same time.
    """

    def __init__(self, root):
        """Load the original files, then the current (possibly edited) dataset.

        root = the folder with app.py, recipes.json and the .npy / *_items.json files.
        self.base holds the original vectors per feature (read only). self.db is True when the
        dishes are saved in Supabase, False when they are saved in admin_data/recipes.json.
        """
        self.root = root
        self.data_dir = os.path.join(root, "admin_data")
        self.runtime_dir = os.path.join(self.data_dir, "matcher")
        os.makedirs(self.runtime_dir, exist_ok=True)
        self.recipes_path = os.path.join(self.data_dir, "recipes.json")
        self.countries_path = os.path.join(self.data_dir, "countries.json")
        self.prompts_path = os.path.join(self.data_dir, "prompts.json")
        self.lock = threading.RLock()

        # The original dataset and vectors (read only)
        with open(os.path.join(root, "recipes.json"), encoding="utf-8") as f:
            self.base_recipes = json.load(f)
        self.base = {}
        for feature in FEATURES:
            with open(os.path.join(root, f"{feature}_items.json"), encoding="utf-8") as f:
                data = json.load(f)
            vecs = np.load(os.path.join(root, f"{feature}_item_vecs.npy"))
            self.base[feature] = {
                "items": data["items"],
                "vecs": vecs,
                "index": {item.lower(): i for i, item in enumerate(data["items"])},
                "normal": {_normal_item(item.lower()): i for i, item in enumerate(data["items"])},
                "recipes": data["recipes"],
                "df": np.array(data.get("df") or [0] * len(data["items"]), dtype=np.float64),
            }
        # Original item lists by recipe, so untouched dishes keep exactly what the vectorizer made
        self.base_by_key = {}
        for r, recipe in enumerate(self.base_recipes):
            self.base_by_key[self._key(recipe)] = r

        self.missing_vectors = []
        # Where admin changes are saved: Supabase if it is set up, else the admin_data/ folder
        self.db = dataset_db.available()
        if self.db:
            try:
                self._load_from_db()
            except dataset_db.SupabaseError as error:
                print(f"[CuiSync] Could not load the dataset from Supabase ({error}). "
                      "Using the local files for now; admin edits are saved in admin_data/.")
                self.db = False
        if not self.db:
            self.uses_admin_data = os.path.exists(self.recipes_path)
            self.recipes = self._load_recipes()
            self.countries = self._load_countries()
            self.prompts = self._load_json(self.prompts_path, {})

    # ---------------- Supabase ----------------
    def _load_from_db(self):
        """Read the dishes from Supabase (countries and prompts stay in admin_data/). On the very
        first start the table is empty, so it is filled from this folder (admin_data/recipes.json
        if the admin already edited the dataset, otherwise the original recipes.json)."""
        recipes = dataset_db.load_recipes()
        if not recipes:
            recipes = self._load_recipes()
            print(f"[CuiSync] Uploading {len(recipes)} dishes to Supabase (first start)...")
            dataset_db.replace_all_recipes(recipes)
            print("[CuiSync] Dataset uploaded to Supabase.")
        for r in recipes:
            r["country"] = (r.get("country") or "").strip().upper()
        self.recipes = recipes
        self._next_position = dataset_db.max_position() + 1
        self.uses_admin_data = not self._same_as_base()
        self.countries = self._load_countries()
        self.prompts = self._load_json(self.prompts_path, {})

    def _same_as_base(self):
        """True when the dataset is still the original one (same dishes, order and item lists),
        so the original .npy files can be used as they are."""
        if len(self.recipes) != len(self.base_recipes):
            return False
        fields = list(TEXT_FIELD.values())
        for r, b in zip(self.recipes, self.base_recipes):
            if self._key(r) != self._key(b) or any((r.get(f) or "") != (b.get(f) or "") for f in fields):
                return False
        return True

    def _db_save(self, action):
        """Run one Supabase save. If it fails, go back to what Supabase has and tell the admin."""
        if not self.db:
            return
        try:
            action()
        except dataset_db.SupabaseError as error:
            try:
                self._load_from_db()
            except dataset_db.SupabaseError:
                pass
            print(f"[CuiSync] Saving the dataset to Supabase failed: {error}")
            raise ValueError("Could not save to Supabase, so nothing was changed. "
                             "Check your internet connection and try again.")

    # ---------------- sharing with the admin app ----------------
    # The admin app (admin_app.py) runs as a separate program and saves its changes in
    # admin_data/. The public app compares data_version() before each request and calls
    # reload_from_disk() when the admin changed something.
    def data_version(self):
        """A fingerprint of the saved files (their modification times). If it changes, another
        program edited admin_data/ and the data should be reloaded. Only used in file mode.
        """
        out = []
        for path in (self.recipes_path, self.countries_path, self.prompts_path):
            try:
                out.append(os.stat(path).st_mtime_ns)
            except OSError:
                out.append(None)
        return tuple(out)

    def reload_from_disk(self):
        """Read the dataset again from where it is saved (Supabase or admin_data/)."""
        with self.lock:
            if self.db:
                return self._load_from_db()
            self.uses_admin_data = os.path.exists(self.recipes_path)
            self.recipes = self._load_recipes()
            self.countries = self._load_countries()
            self.prompts = self._load_json(self.prompts_path, {})

    # ---------------- helpers ----------------
    @staticmethod
    def _key(recipe):
        """Identity of a dish: (id, title, country). Used to find the original version of a dish
        in recipes.json, so an unchanged dish can keep its original vectors.
        """
        return (str(recipe.get("recipe_id")), (recipe.get("title") or "").strip().lower(),
                (recipe.get("country") or "").strip().upper())

    @staticmethod
    def _load_json(path, default):
        """Read a JSON file, or return default if the file does not exist."""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return default

    def _save_json(self, path, data):
        """Write a JSON file safely: write to a .tmp file first, then swap it in, so a crash
        halfway never leaves a broken file.
        """
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    def _load_recipes(self):
        """Dishes from admin_data/recipes.json if the admin edited them, else a copy of the
        original recipes.json. Country names are always stored in upper case (e.g. "JAPAN").
        """
        recipes = self._load_json(self.recipes_path, None)
        if recipes is None:
            recipes = [dict(r) for r in self.base_recipes]
        for r in recipes:
            r["country"] = (r.get("country") or "").strip().upper()
        return recipes

    def _load_countries(self):
        """The country list for the dropdowns: admin_data/countries.json if the admin changed it,
        else one entry per country found in the dishes, with the default label and region.
        """
        saved = self._load_json(self.countries_path, None)
        if saved is not None:
            return saved
        names = sorted({r["country"] for r in self.recipes if r.get("country")})
        return [
            {
                "name": name,
                "label": DEFAULT_LABELS.get(name, name.title()),
                "region": DEFAULT_REGIONS.get(name, "Other Asia"),
                "visible": True,
            }
            for name in names
        ]

    # ---------------- recipes ----------------
    def save_recipes(self, changed=None):
        """Save the dataset. changed = the dishes that were edited (Supabase saves only those);
        None = save everything."""
        with self.lock:
            if self.db:
                if changed is None:
                    self._db_save(lambda: dataset_db.replace_all_recipes(self.recipes))
                    self._next_position = len(self.recipes)
                elif changed:
                    self._db_save(lambda: dataset_db.update_recipes(changed))
            else:
                self._save_json(self.recipes_path, self.recipes)
            self.uses_admin_data = True

    def next_recipe_id(self):
        """The id for a new dish: one more than the highest id in use."""
        ids = [int(r["recipe_id"]) for r in self.recipes if str(r.get("recipe_id", "")).isdigit()]
        return (max(ids) + 1) if ids else 1

    def find_index(self, recipe_id):
        """Position of a dish in self.recipes by its recipe_id, or None if it is not there."""
        for i, r in enumerate(self.recipes):
            if str(r.get("recipe_id")) == str(recipe_id):
                return i
        return None

    def clean_dish(self, form):
        """Check and tidy the admin form (or an uploaded row) into a dish dict.

        - trims every field; ingredient/action/cookware/utensil/tag lists become "a, b, c"
          (one comma list, no repeats)
        - country in upper case; title, country and at least one ingredient are required
          (raises ValueError with a message the admin sees)
        - fills the *_canon fields and country_region so the dish looks like the dataset ones
        """
        dish = {}
        for field in DISH_FIELDS:
            value = (form.get(field) or "").strip()
            if field.endswith("_text") or field == "tags":
                value = ", ".join(_dedupe(t.strip() for t in value.replace("\n", ",").split(",") if t.strip()))
            dish[field] = value
        dish["country"] = dish["country"].upper()
        dish["region"] = dish["region"] or None
        if not dish["title"]:
            raise ValueError("The dish needs a title.")
        if not dish["country"]:
            raise ValueError("The dish needs a country.")
        if not dish["ingredient_text"]:
            raise ValueError("The dish needs at least one ingredient (they are used for matching).")
        # The canon fields are what the NER pipeline produced. For admin-entered dishes they match the text.
        for feature in FEATURES:
            field = TEXT_FIELD[feature]
            dish[field.replace("_text", "_canon")] = dish[field]
        dish["country_region"] = self.region_of(dish["country"])
        return dish

    def add_dish(self, form):
        """Add a new dish from the admin form. Saves it right away (one new row in Supabase,
        placed last) and adds its country to the list if it is new. Returns the saved dish.
        """
        with self.lock:
            dish = self.clean_dish(form)
            dish["recipe_id"] = self.next_recipe_id()
            if self.db:
                self._db_save(lambda: dataset_db.upsert_recipes([(dish, self._next_position)]))
                self._next_position += 1
            self._ensure_country(dish["country"])
            self.recipes.append(dish)
            if not self.db:
                self.save_recipes()
            self.uses_admin_data = True
            return dish

    def update_dish(self, recipe_id, form):
        """Save the admin's changes to one dish (only that row is sent to Supabase).
        Raises ValueError if the dish does not exist or the form is not valid.
        """
        with self.lock:
            idx = self.find_index(recipe_id)
            if idx is None:
                raise ValueError("Dish not found.")
            dish = self.clean_dish(form)
            dish["recipe_id"] = self.recipes[idx]["recipe_id"]
            if self.db:
                updated = dict(self.recipes[idx], **dish)
                self._db_save(lambda: dataset_db.update_recipes([updated]))
            self._ensure_country(dish["country"])
            self.recipes[idx].update(dish)
            if not self.db:
                self.save_recipes()
            self.uses_admin_data = True
            return self.recipes[idx]

    def delete_dish(self, recipe_id):
        """Delete one dish (its row in Supabase, or from admin_data/recipes.json). Returns it."""
        with self.lock:
            idx = self.find_index(recipe_id)
            if idx is None:
                raise ValueError("Dish not found.")
            if self.db:
                rid = self.recipes[idx]["recipe_id"]
                self._db_save(lambda: dataset_db.delete_recipe(rid))
            removed = self.recipes.pop(idx)
            if not self.db:
                self.save_recipes()
            self.uses_admin_data = True
            return removed

    def import_dataset(self, records, mode="append"):
        """Upload a dataset (list of dish dicts). mode = append or replace."""
        with self.lock:
            cleaned = []
            next_id = self.next_recipe_id() if mode == "append" else 1
            for n, rec in enumerate(records, start=1):
                try:
                    dish = self.clean_dish({k: ("" if v is None else str(v)) for k, v in rec.items()})
                except ValueError as error:
                    raise ValueError(f"Row {n}: {error}")
                rid = str(rec.get("recipe_id") or "").strip()
                dish["recipe_id"] = int(rid) if rid.isdigit() and mode == "replace" else next_id
                next_id = max(next_id, int(dish["recipe_id"])) + 1
                cleaned.append(dish)
            if not cleaned:
                raise ValueError("The uploaded file has no dishes.")
            if self.db:
                if mode == "replace":
                    self._db_save(lambda: dataset_db.replace_all_recipes(cleaned))
                    self._next_position = len(cleaned)
                else:
                    start = self._next_position
                    self._db_save(lambda: dataset_db.upsert_recipes(
                        [(d, start + i) for i, d in enumerate(cleaned)]))
                    self._next_position += len(cleaned)
            if mode == "replace":
                self.recipes = cleaned
            else:
                self.recipes.extend(cleaned)
            for dish in cleaned:
                self._ensure_country(dish["country"])
            if not self.db:
                self.save_recipes()
            self.uses_admin_data = True
            return len(cleaned)

    def reset_to_original(self):
        """Go back to the original recipes.json: the dishes in Supabase are replaced by the
        original 900, and the edited country list is kept as admin_data/countries.json.bak.
        """
        with self.lock:
            if self.db:
                originals = [dict(r) for r in self.base_recipes]
                self._db_save(lambda: dataset_db.replace_all_recipes(originals))
                self.recipes = originals
                for r in self.recipes:
                    r["country"] = (r.get("country") or "").strip().upper()
                self._next_position = len(self.recipes)
                self.uses_admin_data = False
                if os.path.exists(self.countries_path):
                    os.replace(self.countries_path, self.countries_path + ".bak")
                self.countries = self._load_countries()
                return
            self.recipes = [dict(r) for r in self.base_recipes]
            for path in (self.recipes_path, self.countries_path):
                if os.path.exists(path):
                    os.replace(path, path + ".bak")
            self.uses_admin_data = False
            self.countries = self._load_countries()

    # ---------------- custom tags (admin Tag Management) ----------------
    def custom_tags(self):
        """Custom tags typed on dishes (the "tags" field), as {Tag Label: number of dishes}."""
        counts = {}
        for r in self.recipes:
            for t in _dedupe(_split(r.get("tags"))):
                label = t.title()
                counts[label] = counts.get(label, 0) + 1
        return counts

    def rename_tag(self, old, new):
        """Rename (or merge) a custom tag on every dish that has it. Returns the number of dishes changed."""
        old_key, new_label = (old or "").strip().lower(), (new or "").strip()
        if not old_key or not new_label:
            raise ValueError("Type the new tag name.")
        if "," in new_label:
            raise ValueError("A tag name can't contain a comma.")
        return self._change_tag(old_key, new_label)

    def delete_tag(self, name):
        """Remove a custom tag from every dish. Returns the number of dishes changed."""
        return self._change_tag((name or "").strip().lower(), None)

    def _change_tag(self, old_key, new_label):
        """Shared code for rename_tag and delete_tag. new_label=None removes the tag.
        Only the dishes that had the tag are saved. Returns how many dishes changed.
        """
        with self.lock:
            changed = 0
            edited = []
            for r in self.recipes:
                tags = [t.strip() for t in str(r.get("tags") or "").split(",") if t.strip()]
                if not any(t.lower() == old_key for t in tags):
                    continue
                out = []
                for t in tags:
                    if t.lower() == old_key:
                        t = new_label  # None = delete
                    if t and t.lower() not in {o.lower() for o in out}:
                        out.append(t)
                edited.append(dict(r, tags=", ".join(out)))
                changed += 1
            if edited:
                if self.db:
                    self._db_save(lambda: dataset_db.update_recipes(edited))
                by_id = {e["recipe_id"]: e["tags"] for e in edited}
                for r in self.recipes:
                    if r["recipe_id"] in by_id:
                        r["tags"] = by_id[r["recipe_id"]]
                if not self.db:
                    self.save_recipes()
            return changed

    # ---------------- countries ----------------
    def save_countries(self):
        """Save the country list to admin_data/countries.json."""
        with self.lock:
            self._save_json(self.countries_path, self.countries)

    def country(self, name):
        """The saved entry for a country name (any case), or None."""
        name = (name or "").strip().upper()
        return next((c for c in self.countries if c["name"] == name), None)

    def region_of(self, name):
        """Region of a country ("JAPAN" -> "East Asia"), with a default for unknown countries."""
        c = self.country(name)
        if c:
            return c["region"]
        return DEFAULT_REGIONS.get((name or "").upper(), "Other Asia")

    def label_of(self, name):
        """How a country is shown to users ("TURKIYE" -> "Turkey")."""
        c = self.country(name)
        return c["label"] if c else (name or "").title()

    def _ensure_country(self, name):
        """Add a country to the list when a dish uses one that is not there yet."""
        if not self.country(name):
            self.countries.append({"name": name, "label": name.title(),
                                   "region": DEFAULT_REGIONS.get(name, "Other Asia"), "visible": True})
            self.countries.sort(key=lambda c: c["name"])
            self.save_countries()

    def add_country(self, label, region):
        """Add a country from the Countries page. It shows in the dropdowns once it has a dish."""
        label = (label or "").strip()
        if not label:
            raise ValueError("Enter a country name.")
        name = label.upper()
        if self.country(name):
            raise ValueError(f"{label} is already in the list.")
        self.countries.append({"name": name, "label": label, "region": region or "Other Asia", "visible": True})
        self.countries.sort(key=lambda c: c["name"])
        self.save_countries()

    def update_country(self, name, label, region, visible):
        """Change how a country is shown (name in the dropdown, region, shown or hidden).
        The stored country key of the dishes stays the same, so OCR and old searches keep working.
        """
        with self.lock:
            c = self.country(name)
            if not c:
                raise ValueError("Country not found.")
            label = (label or "").strip() or c["label"]
            if any(o is not c and o["label"].lower() == label.lower() for o in self.countries):
                raise ValueError(f"{label} is already in the list.")
            region_changed = bool(region) and region != c["region"]
            c["label"] = label
            c["region"] = region or c["region"]
            c["visible"] = bool(visible)
            self.save_countries()
            if region_changed:
                moved = []
                for r in self.recipes:
                    if r["country"] == c["name"]:
                        r["country_region"] = c["region"]
                        moved.append(r)
                self.save_recipes(changed=moved)
            return region_changed

    def remove_country(self, name):
        """Remove a country, but only if no dish uses it (otherwise the admin is told to hide it)."""
        c = self.country(name)
        if not c:
            raise ValueError("Country not found.")
        count = sum(1 for r in self.recipes if r["country"] == c["name"])
        if count:
            raise ValueError(f"{c['label']} still has {count} dishes. Delete or move them first, "
                             "or untick 'Show in dropdown' to hide it.")
        self.countries.remove(c)
        self.save_countries()

    def dropdown_countries(self):
        """Countries for the search dropdowns: visible and with at least one dish."""
        with_dishes = {r["country"] for r in self.recipes}
        return [
            {"value": c["name"].lower(), "label": c["label"], "region": c["region"]}
            for c in sorted(self.countries, key=lambda c: c["label"])
            if c.get("visible", True) and c["name"] in with_dishes
        ]

    # ---------------- prompts ----------------
    def get_prompt(self, name, default):
        """The GPT prompt the admin saved ('explanation' or 'cultural'), or the default."""
        return self.prompts.get(name) or default

    def set_prompt(self, name, text):
        """Save a GPT prompt in admin_data/prompts.json. Empty text = go back to the default."""
        with self.lock:
            if text and text.strip():
                self.prompts[name] = text.strip()
            else:
                self.prompts.pop(name, None)
            self._save_json(self.prompts_path, self.prompts)

    # ---------------- matcher files ----------------
    def _vector_for(self, feature, item, new_items, new_vecs):
        """Vector for one item: known, borrowed from the closest known item, or None."""
        base = self.base[feature]
        if item in base["index"]:
            return base["vecs"][base["index"][item]]
        norm = _normal_item(item)
        if norm in base["normal"]:
            return base["vecs"][base["normal"][norm]]
        close = difflib.get_close_matches(item, list(base["index"]), n=1, cutoff=0.85)
        if close:
            return base["vecs"][base["index"][close[0]]]
        # Shared words: "smoked duck breast" borrows from "duck breast", "duck"...
        words = [w for w in re.findall(r"[a-z]+", norm) if len(w) > 2]
        picks = [base["normal"][w] for w in words if w in base["normal"]]
        if not picks:
            for known, i in base["normal"].items():
                if words and known.split()[-1:] == words[-1:]:
                    picks.append(i)
                    break
        if picks:
            v = base["vecs"][picks].mean(axis=0)
            n = np.linalg.norm(v)
            return v / n if n else None
        return None

    def write_matcher_files(self, write=True):
        """Write <feature>_items.json and <feature>_item_vecs.npy for the current dataset.

        write=False only works out self.missing_vectors (the admin app uses this; only the
        public app writes the matcher files, so the two programs never write them at once)."""
        with self.lock:
            missing = []
            for feature in FEATURES:
                base = self.base[feature]
                items = list(base["items"])
                index = dict(base["index"])
                vecs = [base["vecs"]]
                extra = []
                recipe_ids = []
                field = TEXT_FIELD[feature]
                for recipe in self.recipes:
                    b = self.base_by_key.get(self._key(recipe))
                    if b is not None and (recipe.get(field) or "") == (self.base_recipes[b].get(field) or ""):
                        recipe_ids.append(list(base["recipes"][b]))
                        continue
                    ids = []
                    for item in _dedupe(_split(recipe.get(field))):
                        if item not in index:
                            v = self._vector_for(feature, item, items, extra)
                            if v is None:
                                missing.append((feature, item))
                                v = np.zeros(base["vecs"].shape[1], dtype=np.float32)
                            index[item] = len(items)
                            items.append(item)
                            extra.append(np.asarray(v, dtype=np.float32))
                        ids.append(index[item])
                    recipe_ids.append(ids)
                if extra:
                    vecs.append(np.stack(extra))
                all_vecs = np.concatenate(vecs).astype(np.float32)
                # Start from the vectorizer's own document counts so unchanged dishes keep
                # (almost) the same scores, then adjust for the dishes that were added,
                # changed or removed.
                df = np.zeros(len(items))
                df[: len(base["df"])] = base["df"]
                kept = set()
                for recipe, ids in zip(self.recipes, recipe_ids):
                    b = self.base_by_key.get(self._key(recipe))
                    if b is not None and ids == list(base["recipes"][b]):
                        kept.add(b)
                    else:
                        for i in set(ids):
                            df[i] += 1
                for b, ids in enumerate(base["recipes"]):
                    if b not in kept:
                        for i in set(ids):
                            df[i] = max(df[i] - 1, 1.0)
                if not write:
                    continue
                np.save(os.path.join(self.runtime_dir, f"{feature}_item_vecs.npy"), all_vecs)
                with open(os.path.join(self.runtime_dir, f"{feature}_items.json"), "w", encoding="utf-8") as f:
                    json.dump({"items": items, "df": df.tolist(), "n_recipes": len(self.recipes),
                               "recipes": recipe_ids}, f)
            self.missing_vectors = sorted(set(missing))
            return self.runtime_dir
