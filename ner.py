"""Ingredient NER for the Admin page (Dish Management).

Uses the team's fine-tuned BERT model (BertForTokenClassification, labels O / B-INGREDIENT /
I-INGREDIENT) to pull the ingredients out of recipe text that the admin pastes, then compares
each one with the ingredients that already have a vector:

    existing   -> already in ingredients_items.json (has a vector, ready to use)
    suggested  -> close to an existing item ("Did you mean ...?")
    new        -> not in the dataset yet (borrows the closest vector until the vectorizer runs again)

The model can run in one of two places. The first one that is set up is used:

  1. LOCAL: put the model files in a folder named  ner_model/  next to app.py
     (config.json, model.safetensors, tokenizer.json, tokenizer_config.json)
     and install:  pip install transformers torch
     Good for running on your own computer.

  2. REMOTE: a Hugging Face Space running the NER server. Set in .env:
         NER_API_URL=https://<your-space>.hf.space
         NER_API_TOKEN=<the Space's token>
     Good for Render, where a 435 MB model + torch is too big for the free plan.

Directions (cooking actions, cookware, utensils): extract_directions(text)
  - with the directions model in  ner_directions_model/  (BertForTokenClassification, labels
    B-/I-ACTION, B-/I-COOKWARE, B-/I-UTENSIL), the model tags the directions, then each item is
    compared with actions_items.json / cookware_items.json / utensils_items.json
    (existing / suggested / new, like ingredients; "simmered" -> simmer, "frying pan" -> fry pan)
  - without it, the directions are matched against those item lists only (no packages needed).

Used by app.py: init(root, item_names, feature_items), status(), extract_and_match(text),
extract_directions(text), NerError.
"""
import difflib
import os
import re
import threading
import time

MIN_SCORE = 0.50        # lowest confidence still accepted for an ingredient
WINDOW_WORDS = 200      # words per BERT window (BERT takes at most 512 word pieces)
MAX_CHARS = 20000       # most recipe text accepted at once
SUGGEST_CUTOFF = 0.85   # how close a name must be to an existing item to suggest it

NER_API_URL = os.environ.get("NER_API_URL", "").strip().rstrip("/")
NER_API_TOKEN = os.environ.get("NER_API_TOKEN", "").strip()
NER_API_TIMEOUT = float(os.environ.get("NER_API_TIMEOUT", "180"))  # a sleeping Space needs time to wake


class NerError(Exception):
    """A message the admin should see."""


_model_dir = None
_tokenizer = _model = _torch = None
_id2label = {}
_load_error = None
_loaded = threading.Event()
_load_lock = threading.Lock()

_existing = {}   # normalized name -> name as written in ingredients_items.json
_keys = []


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def init(root, item_names, feature_items=None, recipes=None):
    """Call at startup and again after the dataset changes.
    item_names = MATCHER.items['ingredients']; feature_items = MATCHER.items (for directions)."""
    global _model_dir, _existing, _keys
    if feature_items is not None:
        _init_directions(feature_items, recipes)
    folder = os.environ.get("NER_MODEL_DIR", "").strip() or os.path.join(root, "ner_model")
    _model_dir = folder if os.path.isfile(os.path.join(folder, "config.json")) else None
    _existing = {}
    for item in item_names:
        _existing.setdefault(normalize(item), item)
    _keys = list(_existing)
    # Load the local model in the background so the first click is faster
    if _model_dir and not _loaded.is_set() and _load_error is None:
        threading.Thread(target=_load_local, daemon=True).start()
    folder = os.environ.get("NER_DIRECTIONS_MODEL_DIR", "").strip() or os.path.join(root, "ner_directions_model")
    DIRECTIONS_MODEL.set_folder(folder)


def mode():
    if _model_dir:
        return "local"
    if NER_API_URL:
        return "remote"
    return None


def status():
    """Short status for the admin pages."""
    out = _ingredient_status()
    out["directions"] = DIRECTIONS_MODEL.status()
    return out


def _ingredient_status():
    m = mode()
    if m == "local":
        if _load_error:
            return {"mode": m, "ready": False, "text": f"Local model could not load: {_load_error}"}
        return {"mode": m, "ready": _loaded.is_set(),
                "text": "Local model (ner_model/)" + ("" if _loaded.is_set() else ", loading…")}
    if m == "remote":
        return {"mode": m, "ready": True, "text": "Hugging Face Space (NER_API_URL)"}
    return {"mode": None, "ready": False,
            "text": "Not set up. Put the model in ner_model/ or set NER_API_URL in .env."}


# ---------------------------------------------------------------------------
# Normalizing: the same rules dataset_store.py uses to find an item's vector
# ---------------------------------------------------------------------------

def _singular(word):
    if len(word) > 3 and word.endswith("es") and word[:-2].endswith(("sh", "ch", "o", "x")):
        return word[:-2]
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalize(name):
    name = re.sub(r"[^\w\s\-']", " ", str(name).lower())
    return " ".join(_singular(w) for w in name.split())


# ---------------------------------------------------------------------------
# 1. Local model (transformers + torch)
# ---------------------------------------------------------------------------

def _load_local():
    global _tokenizer, _model, _torch, _id2label, _load_error
    with _load_lock:
        if _loaded.is_set() or _load_error:
            return
        try:
            import torch
            from transformers import AutoModelForTokenClassification, AutoTokenizer
        except ImportError:
            _load_error = "the packages are missing (run: pip install transformers torch)"
            print("[CuiSync] NER:", _load_error)
            return
        try:
            _tokenizer = AutoTokenizer.from_pretrained(_model_dir)
            _model = AutoModelForTokenClassification.from_pretrained(_model_dir)
            _model.eval()
            _torch = torch
            _id2label = {int(k): v for k, v in _model.config.id2label.items()}
            _loaded.set()
            print("[CuiSync] NER model loaded from", _model_dir)
        except Exception as error:  # broken or incomplete model folder
            _load_error = str(error).splitlines()[0][:200]
            print("[CuiSync] NER model could not load:", error)


# Words, numbers and fractions stay together; punctuation is its own token ("garlic," -> "garlic" ",")
_WORD = re.compile(r"\d+/\d+|\d+(?:\.\d+)?|[^\W\d_]+(?:['\-][^\W\d_]+)*|[^\w\s]", re.UNICODE)


def _tag_words(words):
    """Give each WORD one label: the label of its first word piece."""
    encoded = _tokenizer(words, is_split_into_words=True, truncation=True, max_length=512, return_tensors="pt")
    with _torch.no_grad():
        logits = _model(**encoded).logits[0]
    best_prob, best_id = _torch.softmax(logits, dim=-1).max(dim=-1)
    labels, seen = [("O", 0.0)] * len(words), set()
    for token_index, word_index in enumerate(encoded.word_ids(0)):
        if word_index is None or word_index in seen:
            continue
        seen.add(word_index)
        labels[word_index] = (_id2label[int(best_id[token_index])], float(best_prob[token_index]))
    return labels


def _join_entities(words, labels):
    """B-INGREDIENT starts an ingredient, I-INGREDIENT continues it, O ends it."""
    entities, current, scores = [], [], []

    def close():
        if current:
            entities.append((" ".join(current), sum(scores) / len(scores)))
        current.clear()
        scores.clear()

    for word, (label, score) in zip(words, labels):
        if label.endswith("INGREDIENT") and (label.startswith("B-") or not current):
            close()
            current.append(word)
            scores.append(score)
        elif label.endswith("INGREDIENT"):
            current.append(word)
            scores.append(score)
        else:
            close()
    close()
    return entities


def _clean(name):
    name = re.sub(r"\s*-\s*", "-", name)
    name = re.sub(r"\s*'\s*", "'", name)
    return " ".join(name.split()).lower()


def _extract_local(text):
    if not _loaded.is_set():
        if _load_error:
            raise NerError(f"The NER model could not load: {_load_error}")
        _load_local()  # first click before the background load finished: wait for it
        if not _loaded.is_set():
            raise NerError(f"The NER model could not load: {_load_error}")
    found = {}
    for line in (l.strip() for l in re.split(r"[\r\n]+", text)):
        line_words = _WORD.findall(line)
        for start in range(0, len(line_words), WINDOW_WORDS):
            words = line_words[start:start + WINDOW_WORDS]
            if not words:
                continue
            for name, score in _join_entities(words, _tag_words(words)):
                name = _clean(name)
                if not name or score < MIN_SCORE or not re.search(r"[^\W\d_]", name):
                    continue
                if name not in found or score > found[name]:
                    found[name] = score
    return [{"name": n, "score": round(s, 3)} for n, s in found.items()]


# ---------------------------------------------------------------------------
# 2. Remote model (Hugging Face Space)
# ---------------------------------------------------------------------------

def _extract_remote(text):
    import requests
    headers = {"Authorization": f"Bearer {NER_API_TOKEN}"} if NER_API_TOKEN else {}
    deadline = time.monotonic() + NER_API_TIMEOUT
    while True:
        try:
            response = requests.post(NER_API_URL + "/extract", json={"text": text}, headers=headers,
                                     timeout=max(5.0, deadline - time.monotonic()))
            if response.status_code not in (502, 503, 504):  # these mean the Space is waking up
                data = response.json()
                if response.status_code != 200:
                    raise NerError(data.get("message") or f"NER service error ({response.status_code}).")
                return data.get("ingredients", [])
            problem = f"HTTP {response.status_code}"
        except (requests.ConnectionError, requests.Timeout) as error:
            problem = type(error).__name__
        except ValueError:
            raise NerError("The NER service sent an unreadable answer.")
        if time.monotonic() > deadline:
            raise NerError(f"The NER service didn't answer in time ({problem}). Please try again.")
        time.sleep(5)


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def extract_and_match(text):
    """Ingredients in the recipe text, each marked existing / suggested / new."""
    text = (text or "").strip()
    if not text:
        raise NerError("Paste the recipe text first.")
    if len(text) > MAX_CHARS:
        raise NerError(f"The recipe text is too long (over {MAX_CHARS:,} characters).")
    m = mode()
    if m == "local":
        entities = _extract_local(text)
    elif m == "remote":
        entities = _extract_remote(text)
    else:
        raise NerError(status()["text"])

    results, seen = [], set()
    for entity in entities:
        shown = " ".join(str(entity.get("name", "")).lower().split())  # as the model found it
        key = normalize(shown)                                           # only used for matching
        if not key or key in seen:
            continue
        seen.add(key)
        score = entity.get("score")
        if key in _existing:
            results.append({"name": _existing[key], "status": "existing", "score": score})
            continue
        close = difflib.get_close_matches(key, _keys, n=1, cutoff=SUGGEST_CUTOFF)
        if close:
            results.append({"name": shown, "status": "suggested", "suggestion": _existing[close[0]], "score": score})
        else:
            results.append({"name": shown, "status": "new", "score": score})
    return results


# ---------------------------------------------------------------------------
# Directions: cooking actions, cookware and utensils
# ---------------------------------------------------------------------------

DIRECTION_FEATURES = ("actions", "cookware", "utensils")
# Dataset items that are not real actions / tools when they appear in plain text
_SKIP = {
    "actions": {"ad", "addition", "again", "aside", "aside option", "break upcut", "drain if", "lightly pepper",
                "beep", "piece", "side", "time", "minute", "hour", "heat source", "oil", "butter", "salt",
                "pepper", "flour", "water", "sauce", "bowl", "plate", "wok", "pan", "pot", "spoon", "knife"},
    "cookware": {"paneer", "clay", "iron", "dutch", "set", "steam", "pan set", "pan cover"},
    "utensils": {"bowlful", "cup", "spoonful", "piece", "tablespoon", "teaspoon"},
}
# Words that are both a thing and a cooking verb: kept as actions ("Grill the skewers")
_COOKING_VERBS = {"grill", "steam", "roast", "toast", "microwave", "barbecue", "bbq", "broil", "smoke",
                  "skewer", "blend", "whisk", "strain", "sieve", "mash", "grind", "brush", "season", "grease",
                  "zest", "core", "peel", "ladle", "knead", "toss", "pan fry", "stir fry", "deep fry", "dry fry",
                  "dry roast", "fry", "add", "saute", "mix", "cut", "chop", "place", "slice", "dice", "mince",
                  "press", "cover", "drain", "heat", "boil", "bake", "simmer", "stir", "pour", "serve", "garnish"}
_action_index = {}   # first word -> [(words, name)] longest first
_tool_index = {}     # first word -> [(words, name, feature)] longest first
_tool_count = {}     # (feature, name) -> how many dishes use it (to choose cookware vs utensil)
_feature_names = {}  # feature -> every item name in <feature>_items.json


def _index_items(items, skip):
    out = {}
    for item in items:
        name = str(item).strip().lower()
        if not name or name in skip or len(name) < 3:
            continue
        words = tuple(re.findall(r"[a-z]+", name))
        if words:
            out.setdefault(words[0], []).append((words, name))
    return out


def _init_directions(feature_items, recipes=None):
    global _action_index, _tool_index, _tool_count
    names = {f: {str(x).strip().lower() for x in feature_items.get(f, [])} for f in ("ingredients",) + DIRECTION_FEATURES}
    tools = names["cookware"] | names["utensils"]
    # an "action" that is really an ingredient or a tool is skipped, unless it is also a cooking verb
    not_actions = {a for a in names["actions"] if (a in names["ingredients"] or a in tools) and a not in _COOKING_VERBS}
    _action_index = _index_items(names["actions"], _SKIP["actions"] | not_actions)
    tool_index = {}
    for feature in ("cookware", "utensils"):
        other = names["actions"] | names["ingredients"]
        skip = _SKIP[feature] | {t for t in names[feature] if t in other and " " not in t and t not in
                                 {"bowl", "pan", "pot", "wok", "grill", "oven", "steamer", "sieve", "skewer", "spoon", "ladle",
                                  "brush", "whisk", "plate", "grater", "strainer", "tongs", "peeler"}}
        for first, entries in _index_items(names[feature], skip).items():
            tool_index.setdefault(first, []).extend((w, n, feature) for w, n in entries)
    for entries in list(_action_index.values()) + list(tool_index.values()):
        entries.sort(key=lambda e: -len(e[0]))
    _tool_index = tool_index
    global _feature_names
    _feature_names = {f: names[f] for f in DIRECTION_FEATURES}
    counts = {}
    for r in recipes or []:
        for feature, field in (("cookware", "cookware_text"), ("utensils", "utensil_text")):
            for t in {x.strip().lower() for x in str(r.get(field) or "").split(",") if x.strip()}:
                counts[(feature, t)] = counts.get((feature, t), 0) + 1
    _tool_count = counts


def _word_forms(word):
    """'simmered' -> {simmered, simmer, ...}: the word and the base forms it could come from."""
    forms = {word}
    def undouble(stem):
        return stem[:-1] if len(stem) > 2 and stem[-1] == stem[-2] and stem[-1] not in "lsz" else stem
    if word.endswith("ing") and len(word) > 5:
        stem = word[:-3]
        forms |= {stem, stem + "e", undouble(stem)}
        if stem.endswith("y"):
            forms.add(stem)                              # frying -> fry
    if word.endswith("ied") and len(word) > 4:
        forms.add(word[:-3] + "y")                       # fried -> fry
    elif word.endswith("ed") and len(word) > 4:
        stem = word[:-2]
        forms |= {stem, word[:-1], undouble(stem)}       # boiled -> boil, baked -> bake, chopped -> chop
    if word.endswith("ies") and len(word) > 4:
        forms.add(word[:-3] + "y")
    elif word.endswith("ves") and len(word) > 4:
        forms |= {word[:-3] + "fe", word[:-3] + "f"}     # knives -> knife
    elif word.endswith("es") and len(word) > 3:
        forms |= {word[:-2], word[:-1]}                  # dishes -> dish, plates -> plate
    elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        forms.add(word[:-1])                             # pans -> pan
    return forms


def _longest_at(index, words, forms, i):
    best = None
    for form in forms[i]:
        for entry in index.get(form, ()):
            item_words = entry[0]
            n = len(item_words)
            if (best is None or n > len(best[0])) and i + n <= len(words) and \
                    all(item_words[k] in forms[i + k] for k in range(n)):
                best = entry
    return best


def extract_directions(text):
    """Cooking actions, cookware and utensils in the directions (only items the dataset already has)."""
    text = (text or "").strip()
    if not text:
        raise NerError("Paste the directions first.")
    if len(text) > MAX_CHARS:
        raise NerError(f"The directions are too long (over {MAX_CHARS:,} characters).")
    if DIRECTIONS_MODEL.ready():
        result = _extract_directions_model(text)
        result["source"] = "model"
        return result
    if not _action_index:
        raise NerError("The cooking vocabulary is not loaded yet.")
    words = re.findall(r"[a-z]+", text.lower())
    forms = [_word_forms(w) for w in words]
    out = {"actions": [], "cookware": [], "utensils": []}

    # 1. tools: longest name wins; a name in both lists goes where the dataset uses it most
    tool_words = set()
    i = 0
    while i < len(words):
        best = _longest_at(_tool_index, words, forms, i)
        if not best:
            i += 1
            continue
        n = len(best[0])
        same = [e for e in _tool_index.get(best[0][0], ()) if e[0] == best[0]]
        name, feature = max(((e[1], e[2]) for e in same), key=lambda nf: _tool_count.get((nf[1], nf[0]), 0))
        if name not in out[feature]:
            out[feature].append(name)
        if n > 1:
            tool_words.update(range(i, i + n))          # "frying pan" is not the action "fry"
        i += n

    # 2. actions, skipping words that were part of a multi-word tool
    i = 0
    while i < len(words):
        best = None if i in tool_words else _longest_at(_action_index, words, forms, i)
        if not best:
            i += 1
            continue
        if best[1] not in out["actions"]:
            out["actions"].append(best[1])
        i += len(best[0])
    result = {f: [{"name": n, "status": "existing"} for n in out[f]] for f in DIRECTION_FEATURES}
    result["source"] = "vocabulary"
    return result



# ---------------------------------------------------------------------------
# Directions model (ner_directions_model/): ACTION, COOKWARE, UTENSIL
# ---------------------------------------------------------------------------

class _TokenModel:
    """A BertForTokenClassification model in a local folder, loaded once in the background."""

    def __init__(self, name):
        self.name, self.folder = name, None
        self.tokenizer = self.model = self.torch = None
        self.id2label, self.error = {}, None
        self.loaded, self.lock = threading.Event(), threading.Lock()

    def set_folder(self, folder):
        folder = folder if os.path.isfile(os.path.join(folder, "config.json")) else None
        if folder != self.folder:
            self.folder, self.error = folder, None
            self.loaded.clear()
        if self.folder and not self.loaded.is_set() and self.error is None:
            threading.Thread(target=self.load, daemon=True).start()

    def load(self):
        with self.lock:
            if self.loaded.is_set() or self.error or not self.folder:
                return
            try:
                import torch
                from transformers import AutoModelForTokenClassification, AutoTokenizer
            except ImportError:
                self.error = "the packages are missing (run: pip install transformers torch)"
                print(f"[CuiSync] {self.name}:", self.error)
                return
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(self.folder)
                self.model = AutoModelForTokenClassification.from_pretrained(self.folder)
                self.model.eval()
                self.torch = torch
                self.id2label = {int(k): v for k, v in self.model.config.id2label.items()}
                self.loaded.set()
                print(f"[CuiSync] {self.name} loaded from", self.folder)
            except Exception as error:
                self.error = str(error).splitlines()[0][:200]
                print(f"[CuiSync] {self.name} could not load:", error)

    def ready(self):
        if not self.folder:
            return False
        if not self.loaded.is_set() and not self.error:
            self.load()  # first click before the background load finished: wait for it
        if self.error:
            raise NerError(f"The {self.name} could not load: {self.error}")
        return True

    def status(self):
        if not self.folder:
            return {"mode": None, "ready": True, "text": "Dataset vocabulary (no ner_directions_model/ folder)"}
        if self.error:
            return {"mode": "local", "ready": False, "text": f"Directions model could not load: {self.error}"}
        return {"mode": "local", "ready": self.loaded.is_set(),
                "text": "Directions model (ner_directions_model/)" + ("" if self.loaded.is_set() else ", loading…")}

    def tag(self, words):
        """One (label, confidence) per word: the label of the word's first word piece."""
        encoded = self.tokenizer(words, is_split_into_words=True, truncation=True, max_length=512, return_tensors="pt")
        with self.torch.no_grad():
            logits = self.model(**encoded).logits[0]
        best_prob, best_id = self.torch.softmax(logits, dim=-1).max(dim=-1)
        labels, seen = [("O", 0.0)] * len(words), set()
        for token_index, word_index in enumerate(encoded.word_ids(0)):
            if word_index is None or word_index in seen:
                continue
            seen.add(word_index)
            labels[word_index] = (self.id2label[int(best_id[token_index])], float(best_prob[token_index]))
        return labels


DIRECTIONS_MODEL = _TokenModel("directions model")
_LABEL_FEATURE = {"ACTION": "actions", "COOKWARE": "cookware", "UTENSIL": "utensils"}


def _typed_entities(words, labels):
    """[(type, text, score)]: B-X starts an X, I-X continues the same X, anything else ends it."""
    out, kind, current, scores = [], None, [], []

    def close():
        if current:
            out.append((kind, " ".join(current), sum(scores) / len(scores)))
        current.clear()
        scores.clear()

    for word, (label, score) in zip(words, labels):
        prefix, _, ent = label.partition("-")
        if ent and (prefix == "B" or ent != kind or not current):
            close()
            kind = ent
            current.append(word)
            scores.append(score)
        elif ent:
            current.append(word)
            scores.append(score)
        else:
            close()
            kind = None
    close()
    return out


def _match_feature(feature, text):
    """Model output -> the dataset's own item name when there is one (existing / suggested / new)."""
    names = _feature_names.get(feature, set())
    words = re.findall(r"[a-z]+", text.lower())
    if not words:
        return None
    options = [[]]
    for w in words[:4]:
        forms = sorted(_word_forms(w), key=lambda f: (f != w, len(f)))[:6]
        options = [o + [f] for o in options for f in forms][:400]
    for o in options:
        candidate = " ".join(o)
        if candidate in names:
            return {"name": candidate, "status": "existing"}
    shown = " ".join(words)
    close = difflib.get_close_matches(shown, list(names), n=1, cutoff=SUGGEST_CUTOFF)
    if close:
        return {"name": shown, "status": "suggested", "suggestion": close[0]}
    return {"name": shown, "status": "new"}


def _extract_directions_model(text):
    found = {f: {} for f in DIRECTION_FEATURES}
    for line in (l.strip() for l in re.split(r"[\r\n]+|(?<=[.!?;])\s+", text)):
        line_words = _WORD.findall(line)
        for start in range(0, len(line_words), WINDOW_WORDS):
            words = line_words[start:start + WINDOW_WORDS]
            if not words:
                continue
            for kind, name, score in _typed_entities(words, DIRECTIONS_MODEL.tag(words)):
                feature = _LABEL_FEATURE.get(kind)
                if not feature or score < MIN_SCORE:
                    continue
                item = _match_feature(feature, _clean(name))
                if item and item["name"] not in found[feature]:
                    item["score"] = round(score, 3)
                    found[feature][item["name"]] = item
    return {f: list(found[f].values()) for f in DIRECTION_FEATURES}
