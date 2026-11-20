"""Vectors for NEW items (ingredients, actions, cookware, utensils) that the admin adds.

The original vectors come from the vectorization notebook (<feature>_item_vecs.npy). When a dish
gets an item the notebook never saw, dataset_store.py gets a vector for it here:

  1. Supabase table public.item_vectors (made by supabase_item_vectors.sql): items that were
     vectorized before. Loaded once when the app starts.
  2. Otherwise the item is vectorized with the SAME model and settings as the notebook
     (all-MiniLM-L6-v2, normalize_embeddings=True) and saved in that table, so it is kept
     across restarts and Render redeploys.

Where the model runs (the first one that is set up):
  - REMOTE: the NER Hugging Face Space, POST /embed (uses NER_API_URL / NER_API_TOKEN from .env).
    This is what Render uses: torch is too big for the free plan.
  - LOCAL: if NER_API_URL is not set and sentence-transformers is installed
    (pip install sentence-transformers), the model runs on this computer.

Safety check: every request also vectorizes one item the notebook already has (a "probe") and
compares it with the notebook's vector. If they don't match (cosine < MIN_AGREEMENT), the new
vectors are not used, so a different model can never mix into the dataset.
"""
import os
import re
import time

import numpy as np

import accounts
from accounts import SupabaseError

MODEL_NAME = "all-MiniLM-L6-v2"     # same as the notebook
TABLE = "item_vectors"
MIN_AGREEMENT = 0.99                # probe vector vs the notebook's vector (1.0 = identical)
MAX_PER_REQUEST = 500               # items per /embed request (the Space accepts up to 500)
BATCH = 300                         # rows per Supabase request


class EmbedError(Exception):
    pass


# ---------------- Supabase table ----------------
_table_ok = None


def table_available():
    """True when public.item_vectors exists (checked once)."""
    global _table_ok
    if _table_ok is None:
        if not accounts.URL:
            accounts.init()
        if not accounts.configured():
            _table_ok = False
        else:
            try:
                accounts._rest("GET", TABLE, params={"select": "feature", "limit": "1"})
                _table_ok = True
            except SupabaseError:
                print("[CuiSync] The item_vectors table is not in Supabase yet: run "
                      "supabase_item_vectors.sql in the Supabase SQL Editor. Until then, new items "
                      "get vectors for this run of the app only.")
                _table_ok = False
    return _table_ok


def load_all(style=MODEL_NAME):
    """{feature: {item: vector}} for every item saved in the table with this vector style
    (MODEL_NAME for the plain model, V2_STYLE for the v2 vectorizer)."""
    out = {}
    start = 0
    while True:
        rows = accounts._rest("GET", TABLE, params={
            "select": "feature,item,vector", "model": f"eq.{style}",
            "order": "feature.asc,item.asc", "offset": str(start), "limit": "1000"}).json()
        for row in rows:
            out.setdefault(row["feature"], {})[row["item"]] = np.asarray(row["vector"], dtype=np.float32)
        if len(rows) < 1000:
            return out
        start += 1000


def save(feature, items, vecs, style=MODEL_NAME):
    """Insert or update (feature, item) rows, labelled with the vector style."""
    rows = [{"feature": feature, "item": item, "model": style,
             "vector": [round(float(x), 7) for x in vec]}
            for item, vec in zip(items, vecs)]
    for i in range(0, len(rows), BATCH):
        accounts._rest("POST", TABLE, params={"on_conflict": "feature,item"}, json=rows[i:i + BATCH],
                       prefer="resolution=merge-duplicates,return=minimal")


# ---------------- the model ----------------
def mode():
    """'remote' (NER Space), 'local' (sentence-transformers installed) or None."""
    if os.environ.get("NER_API_URL", "").strip():
        return "remote"
    try:
        import sentence_transformers  # noqa: F401
        return "local"
    except Exception as error:  # not installed, or it can't start (e.g. a Windows DLL problem)
        global _local_problem
        _local_problem = f"{type(error).__name__}: {str(error).splitlines()[0][:200]}" if str(error) else type(error).__name__
        return None


_local_problem = None   # why sentence-transformers couldn't be used, if it couldn't


_local_model = None


def _embed_local(texts):
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer
        _local_model = SentenceTransformer(MODEL_NAME)
    return _local_model.encode(texts, batch_size=64, normalize_embeddings=True)


def _embed_remote(texts):
    import requests
    url = os.environ.get("NER_API_URL", "").strip().rstrip("/") + "/embed"
    token = os.environ.get("NER_API_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    timeout = float(os.environ.get("NER_API_TIMEOUT", "180"))  # a sleeping Space needs time to wake
    out = []
    for start in range(0, len(texts), MAX_PER_REQUEST):
        chunk = texts[start:start + MAX_PER_REQUEST]
        deadline = time.monotonic() + timeout
        while True:
            try:
                response = requests.post(url, json={"items": chunk}, headers=headers,
                                         timeout=max(5.0, deadline - time.monotonic()))
                if response.status_code == 404:
                    raise EmbedError("The NER Space has no /embed route yet. Upload the new "
                                     "ner_space/ files to the Space.")
                if response.status_code not in (502, 503, 504):  # these mean the Space is waking up
                    data = response.json()
                    if response.status_code != 200:
                        raise EmbedError(data.get("message") or f"Space error ({response.status_code}).")
                    if data.get("model") != MODEL_NAME:
                        raise EmbedError(f"The Space uses {data.get('model')!r}, not {MODEL_NAME!r}.")
                    out.extend(data["vectors"])
                    break
                problem = f"HTTP {response.status_code}"
            except (requests.ConnectionError, requests.Timeout) as error:
                problem = type(error).__name__
            except (ValueError, KeyError):
                raise EmbedError("The NER Space sent an unreadable answer.")
            if time.monotonic() > deadline:
                raise EmbedError(f"The NER Space didn't answer in time ({problem}).")
            time.sleep(5)
    return out


def _model_vectors(texts):
    """Plain model output (length-1 vectors) for a list of texts, from the Space or locally."""
    m = mode()
    if m == "remote":
        vecs = _embed_remote(texts)
    elif m == "local":
        vecs = _embed_local(texts)
    else:
        raise EmbedError("No model to vectorize with: set NER_API_URL in .env (the NER Space) "
                         "or pip install sentence-transformers"
                         + (f" (sentence-transformers failed: {_local_problem})" if _local_problem else "") + ".")
    vecs = np.asarray(vecs, dtype=np.float32)
    if vecs.ndim != 2 or len(vecs) != len(texts):
        raise EmbedError("The model returned the wrong number of vectors.")
    return vecs


# ---------------- the v2 vectorizer (FINAL_VECTORIZATION.ipynb) ----------------
# The *_item_vecs.npy files are made by the notebook's "v2" cell, which does three things on top
# of the plain model. New items must get the SAME treatment, or they don't fit with the rest:
#   NORMALIZE       '2 tbsp chopped fresh ginger' -> 'ginger', synonyms ('scallion' -> 'spring onion')
#   CONTEXT_PREFIX  embed 'food ingredient: ginger' instead of 'ginger'
#   CENTER_VECTORS  subtract the frequency-weighted mean vector of the feature, then length 1 again
#   SOFT_DF         "df" counts recipes with the item OR a similar one; dataset_store.py redoes this
#                   for the admin's dataset with soft_document_frequency() below
# The mean isn't saved by the notebook, but it is rebuilt exactly from the files: every item in
# <feature>_items.json embedded with its prefix, weighted by "df_exact". (Checked: the rebuilt
# vectors match the saved ones with cosine 1.0000 for all four features.)
# Keep the lists below identical to the notebook if it is ever changed and run again.
V2_STYLE = f"{MODEL_NAME}+v2"        # label for new-item vectors saved in Supabase

CONTEXT = {
    "ingredients": "food ingredient",
    "actions": "cooking technique",
    "cookware": "cookware",
    "utensils": "kitchen utensil",
}
SYNONYMS = {
    "shoyu": "soy sauce", "toyo": "soy sauce", "ganjang": "soy sauce",
    "scallion": "spring onion", "green onion": "spring onion",
    "coriander leaves": "cilantro", "capsicum": "bell pepper",
    "prawn": "shrimp", "prawns": "shrimp",
}
_PREP = {"chopped", "minced", "sliced", "diced", "grated", "crushed", "finely", "roughly",
         "coarsely", "freshly", "fresh", "large", "small", "medium", "optional", "peeled",
         "cubed", "shredded", "thinly"}
_COUNTERS = {"clove", "cloves", "pinch", "dash", "bunch", "sprig", "sprigs", "stalk", "stalks",
             "slice", "slices", "piece", "pieces", "can", "cans"}   # only dropped after a number
_QTY = re.compile(r"\b\d+(?:[./]\d+)?\s*(?:g|kg|mg|ml|l|oz|lb|lbs|tsp|tbsp|cups?)?\b")


def normalize_item(text, synonyms=SYNONYMS):
    """'  2 tbsp Chopped FRESH Ginger (optional) ' -> 'ginger'. Same as the notebook."""
    s = re.sub(r"\([^)]*\)", " ", text.lower())
    had_qty = bool(_QTY.search(s))
    s = _QTY.sub(" ", s)
    s = re.sub(r"[^a-z' -]", " ", s)
    words = [w for w in s.split() if w not in _PREP]
    if had_qty and len(words) > 1 and words[0] in _COUNTERS:     # '2 cloves garlic' -> 'garlic'
        words = words[1:]
        if words[0] == "of" and len(words) > 1:
            words = words[1:]
    s = " ".join(words) if words else text.strip().lower()
    return synonyms.get(s, s)


SOFT_DF_THRESHOLD = 0.60   # the notebook's default; its items.json files say which one was used


def soft_df_threshold(data):
    """Threshold the notebook used for SOFT_DF, from a <feature>_items.json dict, or None when
    "df" there is a plain count (SOFT_DF off, or an older notebook)."""
    if "soft_df_threshold" in data:                      # the notebook says so itself
        t = data["soft_df_threshold"]
        return None if t is None else float(t)
    df, exact = data.get("df"), data.get("df_exact")    # older v2 files: compare the two counts
    if df is None or exact is None or len(df) != len(exact):
        return None
    return SOFT_DF_THRESHOLD if any(abs(float(a) - float(b)) > 1e-9 for a, b in zip(df, exact)) else None


def soft_document_frequency(vecs, recipe_ids, threshold, chunk=512):
    """For each item: how many recipes contain that item OR a similar one (cosine >= threshold).
    Same result as the notebook's version, without scipy (Render doesn't install it).
    An item always counts its own recipes, even if it has no vector yet (zero vector)."""
    vecs = np.asarray(vecs, dtype=np.float32)
    n = len(vecs)
    item_arr, rec_arr = [], []
    for r, ids in enumerate(recipe_ids):
        u = np.unique(np.asarray(ids, dtype=np.int64))
        item_arr.append(u)
        rec_arr.append(np.full(len(u), r, dtype=np.int64))
    if not item_arr or n == 0:
        return np.zeros(n)
    item_arr, rec_arr = np.concatenate(item_arr), np.concatenate(rec_arr)
    order = np.argsort(item_arr, kind="stable")
    rec_sorted = rec_arr[order]
    counts = np.bincount(item_arr, minlength=n)
    starts = np.concatenate([[0], np.cumsum(counts)])
    out = np.zeros(n)
    for s in range(0, n, chunk):
        e = min(s + chunk, n)
        near = vecs[s:e] @ vecs.T >= threshold
        near[np.arange(e - s), np.arange(s, e)] = True
        for k, row in enumerate(near):
            js = np.flatnonzero(row)
            if len(js) == 1:
                out[s + k] = counts[js[0]]
            else:
                out[s + k] = len(np.unique(np.concatenate(
                    [rec_sorted[starts[j]:starts[j + 1]] for j in js])))
    return out


def _center(vecs, mean):
    v = vecs - mean
    return (v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-8)).astype(np.float32)


_means = {}   # (feature, vocabulary fingerprint) -> mean vector


def _feature_mean(feature, vocab, df_exact):
    """The mean the notebook subtracted for this feature (rebuilt once, then kept in memory)."""
    key = (feature, len(vocab), vocab[0] if vocab else "", vocab[-1] if vocab else "",
           float(np.sum(df_exact)))
    if key not in _means:
        raw = _model_vectors([f"{CONTEXT[feature]}: {item}" for item in vocab])
        w = np.asarray(df_exact, dtype=np.float64)
        w = w / w.sum()
        _means[key] = (raw * w[:, None]).sum(axis=0).astype(np.float32)
    return _means[key]


def _check_probe(vec, probe):
    expected = np.asarray(probe[1], dtype=np.float32)
    if vec.shape[0] != expected.shape[0]:
        raise EmbedError(f"Vector size {vec.shape[0]} does not match the notebook's {expected.shape[0]}.")
    agreement = float(vec @ expected)
    if agreement < MIN_AGREEMENT:
        raise EmbedError(f"The model does not match the notebook (similarity {agreement:.3f} for "
                         f"{probe[0]!r}). Check that it is {MODEL_NAME} and that the vectorizer "
                         f"settings above match the notebook that made the .npy files.")


def embed(items, probe=None, feature=None, vocab=None, df_exact=None):
    """Vectors (float32, length 1) for a list of item names, made like the notebook made the
    .npy files.

    With feature / vocab / df_exact (the notebook's v2 files have "df_exact"), new items get the
    v2 treatment: normalized text, context prefix, centered with the feature's mean.
    Without them, the plain model is used (the older notebook's files).
    probe = (item, vector) from the notebook's files, used for the safety check above."""
    items = list(items)
    if not items:
        return np.zeros((0, 0), dtype=np.float32)
    v2 = feature in CONTEXT and vocab is not None and df_exact is not None and len(vocab) > 0
    if v2:
        prefix = CONTEXT[feature] + ": "
        texts = [prefix + normalize_item(item) for item in items]
        if probe is not None:
            texts.append(prefix + probe[0])      # vocabulary items are already normalized
        vecs = _center(_model_vectors(texts), _feature_mean(feature, vocab, df_exact))
    else:
        vecs = _model_vectors(items + ([probe[0]] if probe is not None else []))
    if probe is not None:
        _check_probe(vecs[-1], probe)
        vecs = vecs[:-1]
    return vecs
