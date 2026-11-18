"""Finds which model (and text format) made the *_item_vecs.npy files.

Run in the project folder with the venv on:   python find_embedding_model.py
Each candidate model is downloaded the first time (50-130 MB each). It stops as soon as one
reproduces your vectors (similarity >= 0.99). The result is saved in find_embedding_model_output.txt.
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = open(os.path.join(ROOT, "find_embedding_model_output.txt"), "w", encoding="utf-8")


def say(text=""):
    print(text)
    OUT.write(text + "\n")
    OUT.flush()


# Items to test, with the vectors already in your files
PROBES = {"ingredients": ["salt", "chicken", "soy sauce", "spring onion"], "actions": ["fry", "boil"],
          "cookware": [], "utensils": []}
targets = []
for feature, words in PROBES.items():
    if not words:
        continue
    data = json.load(open(os.path.join(ROOT, f"{feature}_items.json"), encoding="utf-8"))
    vecs = np.load(os.path.join(ROOT, f"{feature}_item_vecs.npy"))
    index = {item: i for i, item in enumerate(data["items"])}
    for w in words:
        if w in index:
            targets.append((feature, w, vecs[index[w]]))
say(f"Testing with: {[w for _, w, _ in targets]}")

MODELS = [
    "sentence-transformers/all-MiniLM-L6-v2",
    "sentence-transformers/all-MiniLM-L12-v2",
    "sentence-transformers/paraphrase-MiniLM-L6-v2",
    "sentence-transformers/multi-qa-MiniLM-L6-cos-v1",
    "BAAI/bge-small-en-v1.5",
    "thenlper/gte-small",
    "intfloat/e5-small-v2",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "sentence-transformers/paraphrase-MiniLM-L3-v2",
    "sentence-transformers/all-MiniLM-L6-v1",
    "sentence-transformers/msmarco-MiniLM-L6-cos-v5",
]
TEMPLATES = ["{x}", "{X}", "{T}", "query: {x}", "passage: {x}", "{f}: {x}", "{x} ({f})",
             "Represent this sentence for searching relevant passages: {x}"]
SINGULAR = {"ingredients": "ingredient", "actions": "action", "cookware": "cookware", "utensils": "utensil"}

from sentence_transformers import SentenceTransformer

best = (0.0, None, None)
for name in MODELS:
    try:
        model = SentenceTransformer(name)
    except Exception as error:
        say(f"{name}: could not load ({type(error).__name__}: {error})")
        continue
    if model.get_sentence_embedding_dimension() != targets[0][2].shape[0]:
        say(f"{name}: wrong size, skipped")
        continue
    for template in TEMPLATES:
        texts = [template.format(x=w, X=w.capitalize(), T=w.title(), f=SINGULAR[f]) for f, w, _ in targets]
        got = model.encode(texts, normalize_embeddings=True)
        sims = [float(g @ t) for g, (_, _, t) in zip(got, targets)]
        score = float(np.mean(sims))
        say(f"{name:58s} {template!r:45s} avg {score:.3f}  min {min(sims):.3f}")
        if score > best[0]:
            best = (score, name, template)
    if best[0] >= 0.99:
        break

say()
if best[0] >= 0.99:
    say(f"FOUND: {best[1]} with text format {best[2]!r} (similarity {best[0]:.3f})")
else:
    say(f"NOT FOUND. Closest: {best[1]} with {best[2]!r} ({best[0]:.3f}). The vectors were probably made "
        "by a fine-tuned or other model: check the script your team used to make the .npy files.")
say("Saved in find_embedding_model_output.txt")
