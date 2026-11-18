"""Admin-editable data for CuiSync (panel revisions: admin module).

Holds everything the admin page can change without touching the original files:
  - the recipe dataset (add / edit / delete dishes, upload a new dataset)
  - the list of countries shown in the dropdowns (add / rename / hide / remove)
  - the regions the countries are grouped in (add / rename / reorder / delete)
  - the GPT prompts (similarity explanation and cultural information)

The original recipes.json and the .npy files are never overwritten. Changes are saved
in Supabase (tables recipes, regions and countries, see supabase_dataset.sql and dataset_db.py;
the GPT prompts stay in admin_data/), or all in admin_data/ when Supabase is not set up. The soft matcher is rebuilt from them:
  - dishes that were not changed keep their original item lists and vectors
  - a new ingredient (or action, cookware, utensil) that is already in the vocabulary
    reuses its vector
  - a brand-new item gets no vector (it does not count towards similarity yet), and the
    admin page lists it, because it needs the vectorizer to be run again.
"""
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
)

DEFAULT_REGIONS = {
    "CHINA": "East Asia", "JAPAN": "East Asia", "SOUTH KOREA": "East Asia",
    "CAMBODIA": "Southeast Asia", "INDONESIA": "Southeast Asia", "MALAYSIA": "Southeast Asia",
    "MYANMAR": "Southeast Asia", "PHILIPPINES": "Southeast Asia", "THAILAND": "Southeast Asia",
    "VIETNAM": "Southeast Asia", "INDIA": "South Asia",
    "SAUDI ARABIA": "West Asia", "TURKIYE": "West Asia",
}
DEFAULT_LABELS = {"TURKIYE": "Turkey"}
# The starting regions (the admin can add more on the Countries page)
REGION_CHOICES = ("East Asia", "Southeast Asia", "South Asia", "Central Asia", "West Asia", "Other Asia")
# Region for countries that have none. It always exists and can't be renamed or deleted.
FALLBACK_REGION = "Other Asia"


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
        self.regions_path = os.path.join(self.data_dir, "regions.json")
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
        # Where admin changes are saved: Supabase if it is set up, else the admin_data/ folder.
        # self.geo_db: the regions and countries are in Supabase too (None = not checked yet).
        self.geo_db = None
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
            self._load_geo()
            self.prompts = self._load_json(self.prompts_path, {})

    # ---------------- Supabase ----------------
    def _load_from_db(self):
        """Read the dishes, regions and countries from Supabase (prompts stay in admin_data/). On the very
        first start the table is empty, so it is filled from this folder (admin_data/recipes.json
        if the admin already edited the dataset, otherwise the original recipes.json)."""
        recipes = dataset_db.load_recipes()
        upload = not recipes
        if upload:
            recipes = self._load_recipes()
        for r in recipes:
            r["country"] = (r.get("country") or "").strip().upper()
        self.recipes = recipes
        # Countries first: recipes.country points to countries.name (foreign key), so every
        # country must be in Supabase before a dish that uses it.
        self._load_geo()
        if upload:
            print(f"[CuiSync] Uploading {len(recipes)} dishes to Supabase (first start)...")
            dataset_db.replace_all_recipes(recipes)
            print("[CuiSync] Dataset uploaded to Supabase.")
        self._next_position = dataset_db.max_position() + 1
        self.uses_admin_data = not self._same_as_base()
        self.prompts = self._load_json(self.prompts_path, {})

    def _load_geo(self):
        """Load self.regions (names in display order) and self.countries.

        From Supabase when the regions / countries tables exist, else from admin_data/. On the
        first start with Supabase the tables are filled from admin_data/ (or the defaults), and
        any country a dish uses but the table does not have yet is added.
        """
        if self.db and self.geo_db is None:
            self.geo_db = dataset_db.geo_available()
        if self.db and self.geo_db:
            try:
                regions = dataset_db.load_regions()
                countries = dataset_db.load_countries()
                if not regions:
                    regions = self._file_regions()
                    print(f"[CuiSync] Uploading {len(regions)} regions to Supabase (first start)...")
                    dataset_db.save_regions(regions)
                if FALLBACK_REGION not in regions:
                    regions.append(FALLBACK_REGION)
                    dataset_db.save_regions(regions)
                self.regions = regions
                if not countries:
                    countries = self._load_countries()
                    extra = [c["region"] for c in countries if c["region"] not in self.regions]
                    if extra:  # regions only the saved country list knows
                        for region in dict.fromkeys(extra):
                            self.regions.insert(self._before_fallback(), region)
                        dataset_db.save_regions(self.regions)
                    print(f"[CuiSync] Uploading {len(countries)} countries to Supabase (first start)...")
                    dataset_db.save_countries(countries)
                self.countries = countries
                added = self._add_missing_countries()
                if added:
                    dataset_db.save_countries(added)
                return
            except dataset_db.SupabaseError as error:
                if getattr(self, "regions", None) is not None:
                    raise  # already running: keep using Supabase, the caller tells the admin
                print(f"[CuiSync] Could not load the regions / countries from Supabase ({error}). "
                      "Using admin_data/ for now (local only).")
                self.geo_db = False
        self.regions = self._file_regions()
        self.countries = self._load_countries()
        for c in self.countries:  # a region only the saved country list knows
            if c["region"] not in self.regions:
                self.regions.insert(self._before_fallback(), c["region"])
        self._add_missing_countries()

    def _file_regions(self):
        """Regions from admin_data/regions.json, else the default list. Always has the fallback."""
        regions = list(self._load_json(self.regions_path, None) or REGION_CHOICES)
        if FALLBACK_REGION not in regions:
            regions.append(FALLBACK_REGION)
        return regions

    def _before_fallback(self):
        """Index to insert a new region at, so the fallback region stays last."""
        if self.regions and self.regions[-1] == FALLBACK_REGION:
            return len(self.regions) - 1
        return len(self.regions)

    def _add_missing_countries(self):
        """Add every country a dish uses that is not in the list yet (in memory). Returns them."""
        known = {c["name"] for c in self.countries}
        added = []
        for name in sorted({r["country"] for r in self.recipes if r.get("country")} - known):
            added.append({"name": name, "label": DEFAULT_LABELS.get(name, name.title()),
                          "region": self._default_region(name), "visible": True})
        if added:
            self.countries.extend(added)
            self.countries.sort(key=lambda c: c["name"])
        return added

    def _default_region(self, name):
        """Region for a country that has none yet: the one most of its dishes use, else the
        default for that country, else the fallback. Always one of self.regions."""
        used = [r.get("country_region") for r in self.recipes
                if r.get("country") == name and r.get("country_region") in self.regions]
        if used:
            return max(set(used), key=used.count)
        region = DEFAULT_REGIONS.get(name)
        return region if region in self.regions else FALLBACK_REGION

    def _geo_save(self, action):
        """Run one Supabase save for the regions / countries. If it fails, go back to what
        Supabase has and tell the admin."""
        try:
            action()
        except dataset_db.SupabaseError as error:
            try:
                self.regions = dataset_db.load_regions() or self.regions
                self.countries = dataset_db.load_countries() or self.countries
            except dataset_db.SupabaseError:
                pass
            print(f"[CuiSync] Saving the regions / countries to Supabase failed: {error}")
            raise ValueError("Could not save to Supabase, so nothing was changed. "
                             "Check your internet connection and try again.")

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
        for path in (self.recipes_path, self.countries_path, self.regions_path, self.prompts_path):
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
            self._load_geo()
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
                "region": self._default_region(name),
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

        - trims every field; ingredient/action/cookware/utensil lists become "a, b, c"
          (one comma list, no repeats)
        - country in upper case; title, country and at least one ingredient are required
          (raises ValueError with a message the admin sees)
        - fills the *_canon fields and country_region so the dish looks like the dataset ones
        """
        dish = {}
        for field in DISH_FIELDS:
            value = (form.get(field) or "").strip()
            if field.endswith("_text"):
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
            self._ensure_country(dish["country"])  # before the dish (foreign key in Supabase)
            if self.db:
                self._db_save(lambda: dataset_db.upsert_recipes([(dish, self._next_position)]))
                self._next_position += 1
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
            self._ensure_country(dish["country"])  # before the dish (foreign key in Supabase)
            if self.db:
                updated = dict(self.recipes[idx], **dish)
                self._db_save(lambda: dataset_db.update_recipes([updated]))
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
            self._ensure_countries(d["country"] for d in cleaned)  # before the dishes (foreign key)
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
            if not self.db:
                self.save_recipes()
            self.uses_admin_data = True
            return len(cleaned)

    def reset_to_original(self):
        """Go back to the original recipes.json: the dishes in Supabase are replaced by the
        original 900. The country list and regions in Supabase are kept (countries that only the
        original dishes use are added); without Supabase the edited country list is kept as
        admin_data/countries.json.bak.
        """
        with self.lock:
            if self.db:
                originals = [dict(r) for r in self.base_recipes]
                for r in originals:
                    r["country"] = (r.get("country") or "").strip().upper()
                if self.geo_db:  # countries before the dishes (foreign key)
                    self._ensure_countries(r["country"] for r in originals)
                self._db_save(lambda: dataset_db.replace_all_recipes(originals))
                self.recipes = originals
                for r in self.recipes:
                    r["country"] = (r.get("country") or "").strip().upper()
                self._next_position = len(self.recipes)
                self.uses_admin_data = False
                if self.geo_db:
                    return
                if os.path.exists(self.countries_path):
                    os.replace(self.countries_path, self.countries_path + ".bak")
                self._load_geo()
                return
            self.recipes = [dict(r) for r in self.base_recipes]
            for path in (self.recipes_path, self.countries_path):
                if os.path.exists(path):
                    os.replace(path, path + ".bak")
            self.uses_admin_data = False
            self._load_geo()

    # ---------------- countries ----------------
    def save_countries(self, changed=None):
        """Save the country list. changed = the countries that were added or edited (Supabase
        saves only those); None = save all. Without Supabase: admin_data/countries.json."""
        with self.lock:
            if self.geo_db:
                rows = self.countries if changed is None else changed
                self._geo_save(lambda: dataset_db.save_countries(rows))
            else:
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
        region = DEFAULT_REGIONS.get((name or "").upper())
        return region if region in (getattr(self, "regions", None) or REGION_CHOICES) else FALLBACK_REGION

    def label_of(self, name):
        """How a country is shown to users ("TURKIYE" -> "Turkey")."""
        c = self.country(name)
        return c["label"] if c else (name or "").title()

    def _ensure_country(self, name):
        """Add a country to the list when a dish uses one that is not there yet."""
        self._ensure_countries([name])

    def _ensure_countries(self, names):
        """Add (and save, in one go) every country in names that is not in the list yet."""
        added = []
        for name in dict.fromkeys(n for n in names if n):
            if not self.country(name):
                c = {"name": name, "label": DEFAULT_LABELS.get(name, name.title()),
                     "region": self._default_region(name), "visible": True}
                self.countries.append(c)
                added.append(c)
        if added:
            self.countries.sort(key=lambda c: c["name"])
            try:
                self.save_countries(added)
            except ValueError:
                for c in added:
                    if c in self.countries:
                        self.countries.remove(c)
                raise

    def add_country(self, label, region):
        """Add a country from the Countries page. It shows in the dropdowns once it has a dish."""
        label = (label or "").strip()
        if not label:
            raise ValueError("Enter a country name.")
        name = label.upper()
        if self.country(name) or any(c["label"].lower() == label.lower() for c in self.countries):
            raise ValueError(f"{label} is already in the list.")
        with self.lock:
            c = {"name": name, "label": label, "region": self._region_or_new(region), "visible": True}
            self.countries.append(c)
            self.countries.sort(key=lambda c: c["name"])
            self.save_countries([c])

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
            region = self._region_or_new(region) if (region or "").strip() else c["region"]
            region_changed = region != c["region"]
            c["label"] = label
            c["region"] = region
            c["visible"] = bool(visible)
            self.save_countries([c])
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
        with self.lock:
            if self.geo_db:
                self._geo_save(lambda: dataset_db.delete_country(c["name"]))
            self.countries.remove(c)
            if not self.geo_db:
                self.save_countries()

    def dropdown_countries(self):
        """Countries for the search dropdowns: visible and with at least one dish."""
        with_dishes = {r["country"] for r in self.recipes}
        return [
            {"value": c["name"].lower(), "label": c["label"], "region": c["region"]}
            for c in sorted(self.countries, key=lambda c: c["label"])
            if c.get("visible", True) and c["name"] in with_dishes
        ]

    # ---------------- regions ----------------
    def region(self, name):
        """The saved spelling of a region name (any case), or None."""
        key = " ".join((name or "").split()).lower()
        return next((r for r in self.regions if r.lower() == key), None)

    def _check_region(self, name):
        """The region name as saved, or ValueError if there is no such region."""
        found = self.region(name)
        if not found:
            raise ValueError(f"There is no region called {name}. Add it under Regions first.")
        return found

    def _region_or_new(self, name):
        """The region the admin typed: an existing one (any case), or a new one that is added
        to the list (and saved) right away. Empty = the fallback region."""
        if not " ".join((name or "").split()):
            return FALLBACK_REGION
        return self.region(name) or self.add_region(name)

    def region_counts(self):
        """{region: number of countries in it}."""
        counts = {r: 0 for r in self.regions}
        for c in self.countries:
            counts[c["region"]] = counts.get(c["region"], 0) + 1
        return counts

    def save_regions(self):
        """Save the regions in their current order (Supabase or admin_data/regions.json)."""
        with self.lock:
            if self.geo_db:
                self._geo_save(lambda: dataset_db.save_regions(self.regions))
            else:
                self._save_json(self.regions_path, self.regions)

    @staticmethod
    def _clean_region_name(name):
        name = " ".join((name or "").split())
        if not name:
            raise ValueError("Enter a region name.")
        if len(name) > 40:
            raise ValueError("Keep the region name under 40 characters.")
        return name

    def add_region(self, name):
        """Add a region (shown before the fallback region). Returns its name."""
        name = self._clean_region_name(name)
        with self.lock:
            if self.region(name):
                raise ValueError(f"{name} is already a region.")
            self.regions.insert(self._before_fallback(), name)
            try:
                self.save_regions()
            except ValueError:
                if name in self.regions:
                    self.regions.remove(name)
                raise
            return name

    def rename_region(self, old, new):
        """Rename a region. Its countries and their dishes move with it.
        Returns the number of dishes whose region was changed."""
        new = self._clean_region_name(new)
        with self.lock:
            old = self._check_region(old)
            if old == FALLBACK_REGION:
                raise ValueError(f"{FALLBACK_REGION} is used for countries without a region, "
                                 "so it can't be renamed.")
            if new == old:
                return 0
            other = self.region(new)
            if other and other != old:
                raise ValueError(f"{new} is already a region.")
            if self.geo_db:  # Supabase moves the countries too (ON UPDATE CASCADE)
                self._geo_save(lambda: dataset_db.rename_region(old, new))
            self.regions[self.regions.index(old)] = new
            for c in self.countries:
                if c["region"] == old:
                    c["region"] = new
            if not self.geo_db:
                self.save_regions()
                self.save_countries()
            moved = [r for r in self.recipes if r.get("country_region") == old]
            for r in moved:
                r["country_region"] = new
            if moved:
                self.save_recipes(changed=moved)
            return len(moved)

    def delete_region(self, name):
        """Delete a region that no country uses."""
        with self.lock:
            name = self._check_region(name)
            if name == FALLBACK_REGION:
                raise ValueError(f"{FALLBACK_REGION} is used for countries without a region, "
                                 "so it can't be deleted.")
            used = [c["label"] for c in self.countries if c["region"] == name]
            if used:
                raise ValueError(f"{name} still has {len(used)} "
                                 f"{'country' if len(used) == 1 else 'countries'} ({', '.join(used[:5])}). "
                                 f"Move {'it' if len(used) == 1 else 'them'} to another region first.")
            if self.geo_db:
                self._geo_save(lambda: dataset_db.delete_region(name))
            self.regions.remove(name)
            if not self.geo_db:
                self.save_regions()

    def move_region(self, name, step):
        """Move a region up (step -1) or down (step +1) in the display order."""
        with self.lock:
            name = self._check_region(name)
            i = self.regions.index(name)
            j = i + (1 if step > 0 else -1)
            if not 0 <= j < len(self.regions):
                return
            before = list(self.regions)
            self.regions[i], self.regions[j] = self.regions[j], self.regions[i]
            try:
                self.save_regions()
            except ValueError:
                self.regions = before
                raise

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
        """Vector for one item: only items the vectorizer already knows (the same item, or its
        singular/plural form). Anything else gets None until the vectorizer is run again."""
        base = self.base[feature]
        if item in base["index"]:
            return base["vecs"][base["index"][item]]
        norm = _normal_item(item)
        if norm in base["normal"]:
            return base["vecs"][base["normal"][norm]]
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
