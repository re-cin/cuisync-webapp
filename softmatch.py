"""Soft ingredient matching for CuiSync.

Every item in dish A is matched to its closest item in dish B (and the other way around),
so 'chicken breast' and 'chicken thigh' count as almost the same, but 'chicken' and 'pork' don't.

Used by app.py: SoftMatcher(folder, recipes), scores(), pair_score(), explain().
A category that's empty in both dishes returns NaN, and app.py skips it.
"""
import json
import os
import re

import numpy as np

FEATURES = ("ingredients", "actions", "cookware", "utensils")

# Similarities at or below FLOOR count as 0, the rest get stretched to 0..1
FLOOR = 0.35

# 0 = every item counts the same. Higher = items listed first count more.
POSITION_DECAY = 0.0

# Common items (salt, oil, garlic...) count less, rare ones (chicken, lemongrass...) count more.
# 0 = off, 1 = normal, 2 = stronger. This stops shared seasonings from beating the main ingredient.
IDF_POWER = 1.0

# How much more recall counts than precision (1 = plain F1).
# Higher = extra ingredients in the target are forgiven more than missing main ingredients.
RECALL_WEIGHT = 2.0

# An ingredient that's also in the dish title ("chicken" in "Fried Chicken") counts this many times more. 1 = off.
TITLE_BOOST = 4.0

# Score of a category that's empty in BOTH dishes.
# None = skip it and re-balance the other weights in app.py (so identical dishes still get 100%).
# A number 0..1 = use that fixed score instead.
# Empty in only one of the dishes always scores 0.
BOTH_EMPTY_SCORE = None

_STOP = {"and", "with", "the", "of", "in", "a", "style", "or"}


def _words(text):
    """Lowercase words, with a simple plural fix: 'Eggs' -> 'egg'."""
    if isinstance(text, (list, tuple, set)):
        text = " ".join(str(t) for t in text)
    out = set()
    for w in re.findall(r"[a-z]+", str(text or "").lower()):
        if w in _STOP or len(w) < 3:
            continue
        out.add(w[:-1] if w.endswith("s") and len(w) > 3 else w)
    return out


def _weights(item_ids, idf, boost):
    """Weight of each item: rarer, earlier and title items count more."""
    position = 1.0 / (1.0 + POSITION_DECAY * np.arange(len(item_ids)))
    w = position * idf[item_ids] ** IDF_POWER * boost
    return w / w.sum()


def _fbeta(precision, recall, beta):
    """F-beta score. beta > 1 favors recall. Returns 0 where it can't be computed."""
    b2 = beta ** 2
    num = (1 + b2) * precision * recall
    den = b2 * precision + recall
    return np.divide(num, den, out=np.zeros_like(np.asarray(den, dtype=np.float64)), where=den > 0)


class SoftMatcher:
    def __init__(self, folder, recipes=None):
        self.vecs, self.recipe_ids, self.idf, self.items, self.boost = {}, {}, {}, {}, {}
        for feature in FEATURES:
            self.vecs[feature] = np.load(os.path.join(folder, f"{feature}_item_vecs.npy"))
            with open(os.path.join(folder, f"{feature}_items.json"), encoding="utf-8") as f:
                data = json.load(f)
            self.recipe_ids[feature] = [np.array(ids, dtype=np.int64) for ids in data["recipes"]]
            if "df" in data:  # made by the vectorizer
                df = np.array(data["df"], dtype=np.float64)
            else:  # older export, count it here
                df = np.zeros(len(data["items"]))
                for ids in data["recipes"]:
                    for i in set(ids):
                        df[i] += 1
            n_recipes = data.get("n_recipes", len(data["recipes"]))
            self.idf[feature] = np.log((n_recipes + 1.0) / (df + 1.0)) + 1.0
            self.items[feature] = data["items"]
            self.boost[feature] = [np.ones(len(ids)) for ids in self.recipe_ids[feature]]

        # Boost for ingredients named in the dish title
        if recipes is not None and TITLE_BOOST != 1.0:
            names = self.items["ingredients"]
            for r, (recipe, ids) in enumerate(zip(recipes, self.recipe_ids["ingredients"])):
                title_words = _words(recipe.get("title", "")) | _words(recipe.get("alternative_title", ""))
                self.boost["ingredients"][r] = np.array(
                    [TITLE_BOOST if _words(names[i]) & title_words else 1.0 for i in ids]
                )

        # Weights per recipe, computed once. All items are also put in one flat list
        # so every recipe can be scored with a few numpy calls.
        self.weights, self._flat = {}, {}
        for feature in FEATURES:
            ids_list = self.recipe_ids[feature]
            self.weights[feature] = [
                _weights(ids, self.idf[feature], self.boost[feature][r]) if len(ids) else np.zeros(0)
                for r, ids in enumerate(ids_list)
            ]
            nonempty = np.flatnonzero([len(ids) > 0 for ids in ids_list])
            if len(nonempty):
                flat_ids = np.concatenate([ids_list[r] for r in nonempty])
                flat_w = np.concatenate([self.weights[feature][r] for r in nonempty])
                lens = np.array([len(ids_list[r]) for r in nonempty])
                starts = np.concatenate([[0], np.cumsum(lens)[:-1]]).astype(np.int64)
            else:
                flat_ids, flat_w, starts = np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int64)
            self._flat[feature] = (flat_ids, flat_w, starts, nonempty)

    def __len__(self):
        return len(self.recipe_ids[FEATURES[0]])

    def _sim(self, a_ids, b_ids, feature):
        v = self.vecs[feature]
        return np.clip((v[a_ids] @ v[b_ids].T - FLOOR) / (1.0 - FLOOR), 0.0, 1.0)

    def scores(self, feature, source_idx, beta=None):
        """Similarity (0..1) of one dish to every recipe, for one feature.
        Not symmetric when beta != 1. NaN = empty in both dishes, skip it.
        """
        beta = RECALL_WEIGHT if beta is None else beta
        out = np.zeros(len(self.recipe_ids[feature]))
        src = self.recipe_ids[feature][source_idx]
        flat_ids, flat_w, starts, nonempty = self._flat[feature]
        if len(src) == 0:
            # The searched dish has nothing here. Dishes that also have nothing get BOTH_EMPTY_SCORE,
            # the rest get 0.
            fill = np.nan if BOTH_EMPTY_SCORE is None else BOTH_EMPTY_SCORE
            for r, ids in enumerate(self.recipe_ids[feature]):
                if len(ids) == 0:
                    out[r] = fill  # NaN = skip this category
            return out
        if len(flat_ids) == 0:
            return out
        vecs = self.vecs[feature]
        sim_v = np.clip((vecs[src] @ vecs.T - FLOOR) / (1.0 - FLOOR), 0.0, 1.0)  # searched dish x all items
        sim = sim_v[:, flat_ids]  # searched dish x each recipe's items
        recall = self.weights[feature][source_idx] @ np.maximum.reduceat(sim, starts, axis=1)
        precision = np.add.reduceat(sim.max(axis=0) * flat_w, starts)
        out[nonempty] = _fbeta(precision, recall, beta)
        return out

    def pair_score(self, feature, a_idx, b_idx, beta=1.0):
        """Score for one pair of dishes. With beta=1 it's the same both ways (A,B == B,A)."""
        a, b = self.recipe_ids[feature][a_idx], self.recipe_ids[feature][b_idx]
        if len(a) == 0 and len(b) == 0:
            return float("nan") if BOTH_EMPTY_SCORE is None else float(BOTH_EMPTY_SCORE)
        if len(a) == 0 or len(b) == 0:
            return 0.0
        sim = self._sim(a, b, feature)
        recall = float((sim.max(axis=1) * self.weights[feature][a_idx]).sum())
        precision = float((sim.max(axis=0) * self.weights[feature][b_idx]).sum())
        return float(_fbeta(precision, recall, beta))

    def explain(self, feature, source_idx, target_idx):
        """For each item of the searched dish: its best match in the other dish, the similarity and the weight."""
        src, tgt = self.recipe_ids[feature][source_idx], self.recipe_ids[feature][target_idx]
        if len(src) == 0 or len(tgt) == 0:
            return []
        sim = self._sim(src, tgt, feature)
        weights = self.weights[feature][source_idx]
        names = self.items[feature]
        return [(names[src[i]], names[tgt[sim[i].argmax()]], float(sim[i].max()), float(weights[i]))
                for i in range(len(src))]

    def similarity_matrix(self, feature, beta=1.0):
        """Full N x N similarity matrix (rows = searched dish)."""
        n = len(self)
        return np.stack([self.scores(feature, i, beta=beta) for i in range(n)])


def csls(matrix, k=10):
    """Hubness correction (CSLS): lowers the score of dishes that are close to everything
    (like plain fried rice). Gives a ranking score, not 0..1.
    """
    m = np.array(matrix, dtype=np.float64)
    masked = m.copy()
    np.fill_diagonal(masked, -np.inf)
    k = min(k, m.shape[1] - 1)
    r_row = np.sort(np.partition(masked, -k, axis=1)[:, -k:], axis=1).mean(axis=1)
    r_col = np.sort(np.partition(masked, -k, axis=0)[-k:, :], axis=0).mean(axis=0)
    return 2.0 * m - r_row[:, None] - r_col[None, :]