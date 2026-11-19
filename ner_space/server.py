"""CuiSync ingredient NER service (runs on a Hugging Face Space).

Uses the fine-tuned BERT model (BertForTokenClassification, labels O / B-INGREDIENT / I-INGREDIENT)
to pull the ingredients out of recipe text that an admin pastes on the Admin page.

    GET  /health   -> {"status": "ok", "model_ready": ..., "embed_ready": ..., "directions_ready": ...}
    POST /extract_directions -> JSON {"text": "..."}
                      returns {"entities": [{"type": "ACTION", "name": "simmer", "score": 0.95}, ...]}
                      (needs the directions model in the folder DIRECTIONS_MODEL_DIR, default ./directions)
    POST /extract  -> JSON {"text": "..."}
                      returns {"ingredients": [{"name": "...", "score": 0.97}, ...]}
    POST /embed    -> JSON {"items": ["gochugaru", "air fryer", ...]}
                      returns {"model": "all-MiniLM-L6-v2", "dim": 384, "vectors": [[...], ...]}
                      Vectors for new items, made exactly like the team's vectorization notebook
                      (SentenceTransformer("all-MiniLM-L6-v2"), normalize_embeddings=True).

If NER_API_TOKEN is set on the Space, requests need "Authorization: Bearer <token>".
The model files (config.json, model.safetensors, tokenizer.json, tokenizer_config.json)
go in the folder MODEL_DIR (default: ./model).
"""
import hmac
import os
import re
import threading

import torch
from flask import Flask, jsonify, request
from transformers import AutoModel, AutoModelForTokenClassification, AutoTokenizer

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # recipe text only

TOKEN = os.environ.get("NER_API_TOKEN", "").strip()
MODEL_DIR = os.environ.get("MODEL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "model"))

# Words per window sent to BERT. BERT takes at most 512 tokens, and one word can become
# several word pieces, so 200 words stays safely under the limit.
WINDOW_WORDS = 200
# Lowest confidence we still accept for an ingredient
MIN_SCORE = 0.50
# Most text accepted in one request
MAX_CHARS = 20000

MODEL_READY = threading.Event()
_tokenizer = None
_model = None
_id2label = {}


def _load_model():
    global _tokenizer, _model, _id2label
    _tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)
    _model = AutoModelForTokenClassification.from_pretrained(MODEL_DIR)
    _model.eval()
    _id2label = {int(k): v for k, v in _model.config.id2label.items()}
    MODEL_READY.set()
    print("[NER] Model loaded from", MODEL_DIR)


threading.Thread(target=_load_model, daemon=True).start()


# ---------- Directions model (ACTION / COOKWARE / UTENSIL) ----------
# Optional second model in ./directions (config.json, model.safetensors, tokenizer files).
DIRECTIONS_DIR = os.environ.get("DIRECTIONS_MODEL_DIR",
                                os.path.join(os.path.dirname(os.path.abspath(__file__)), "directions"))
DIRECTIONS_READY = threading.Event()
_dir_tokenizer = None
_dir_model = None
_dir_id2label = {}
_dir_error = None


def _load_directions_model():
    global _dir_tokenizer, _dir_model, _dir_id2label, _dir_error
    if not os.path.isfile(os.path.join(DIRECTIONS_DIR, "config.json")):
        _dir_error = "no directions model in " + DIRECTIONS_DIR
        print("[NER]", _dir_error)
        return
    try:
        _dir_tokenizer = AutoTokenizer.from_pretrained(DIRECTIONS_DIR)
        _dir_model = AutoModelForTokenClassification.from_pretrained(DIRECTIONS_DIR)
        _dir_model.eval()
        _dir_id2label = {int(k): v for k, v in _dir_model.config.id2label.items()}
        DIRECTIONS_READY.set()
        print("[NER] Directions model loaded from", DIRECTIONS_DIR)
    except Exception as error:  # the ingredient part keeps working without it
        _dir_error = str(error).splitlines()[0][:200]
        print("[NER] Could not load the directions model:", error)


threading.Thread(target=_load_directions_model, daemon=True).start()


def _authorized():
    if not TOKEN:
        return True
    sent = request.headers.get("Authorization", "")
    if sent.startswith("Bearer "):
        sent = sent[len("Bearer "):]
    return hmac.compare_digest(sent.strip(), TOKEN)


# ---------- Step 1: split the text into words ----------

# Words, numbers and fractions stay together; punctuation becomes its own token,
# so "garlic," -> "garlic" + ","
_WORD = re.compile(r"\d+/\d+|\d+(?:\.\d+)?|[^\W\d_]+(?:['\-][^\W\d_]+)*|[^\w\s]", re.UNICODE)


def _split_words(text):
    """Split recipe text into lines, then each line into words."""
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    return [_WORD.findall(line) for line in lines]


def _windows(words):
    """Long lines are cut into windows of WINDOW_WORDS words."""
    for start in range(0, len(words), WINDOW_WORDS):
        yield words[start:start + WINDOW_WORDS]


# ---------- Step 2 + 3: tokenize and tag ----------

def _tag_words(words, tokenizer=None, model=None, id2label=None):
    """Tokenize the words, run BERT, and give each WORD one label.

    BERT splits words into word pieces ("lemongrass" -> "lemon", "##grass").
    Each word takes the label and confidence of its FIRST piece,
    which is how token classification models are usually trained.
    """
    tokenizer = tokenizer or _tokenizer
    model = model or _model
    id2label = id2label or _id2label
    encoded = tokenizer(
        words,
        is_split_into_words=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )
    with torch.no_grad():
        logits = model(**encoded).logits[0]
    probs = torch.softmax(logits, dim=-1)
    best_prob, best_id = probs.max(dim=-1)

    labels = [("O", 0.0)] * len(words)
    seen = set()
    for token_index, word_index in enumerate(encoded.word_ids(0)):
        if word_index is None or word_index in seen:  # [CLS], [SEP], or a later word piece
            continue
        seen.add(word_index)
        labels[word_index] = (id2label[int(best_id[token_index])], float(best_prob[token_index]))
    return labels


# ---------- Step 4: join tagged words into ingredients ----------

def _join_entities(words, labels):
    """B-INGREDIENT starts an ingredient, I-INGREDIENT continues it, O ends it."""
    entities, current, scores = [], [], []

    def close():
        if current:
            entities.append((" ".join(current), sum(scores) / len(scores)))
        current.clear()
        scores.clear()

    for word, (label, score) in zip(words, labels):
        if label == "B-INGREDIENT":
            close()
            current.append(word)
            scores.append(score)
        elif label == "I-INGREDIENT" and current:
            current.append(word)
            scores.append(score)
        elif label == "I-INGREDIENT":
            # I- without a B- before it: treat it as the start of a new ingredient
            current.append(word)
            scores.append(score)
        else:
            close()
    close()
    return entities


def _clean(name):
    """Tidy the spacing around hyphens and apostrophes that the word split added."""
    name = re.sub(r"\s*-\s*", "-", name)
    name = re.sub(r"\s*'\s*", "'", name)
    return " ".join(name.split()).lower()


def extract_ingredients(text):
    found = {}
    for line_words in _split_words(text):
        for words in _windows(line_words):
            for name, score in _join_entities(words, _tag_words(words)):
                name = _clean(name)
                if not name or score < MIN_SCORE or not re.search(r"[^\W\d_]", name):
                    continue
                # Same ingredient twice: keep the higher confidence
                if name not in found or score > found[name]:
                    found[name] = score
    return [{"name": name, "score": round(score, 3)} for name, score in found.items()]


# ---------- Directions: actions, cookware, utensils (POST /extract_directions) ----------

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


def extract_directions(text):
    """Every tagged item with its type (ACTION / COOKWARE / UTENSIL) and confidence.
    The web app matches them with its own item lists."""
    found = {}
    for line in (l.strip() for l in re.split(r"[\r\n]+|(?<=[.!?;])\s+", text)):
        line_words = _WORD.findall(line)
        for words in _windows(line_words):
            if not words:
                continue
            labels = _tag_words(words, _dir_tokenizer, _dir_model, _dir_id2label)
            for kind, name, score in _typed_entities(words, labels):
                name = _clean(name)
                if not kind or not name or score < MIN_SCORE or not re.search(r"[^\W\d_]", name):
                    continue
                key = (kind, name)
                if key not in found or score > found[key]:
                    found[key] = score
    return [{"type": kind, "name": name, "score": round(score, 3)} for (kind, name), score in found.items()]


# ---------- Item vectors (POST /embed) ----------
# all-MiniLM-L6-v2 is a public model. The Dockerfile downloads it into ./embed_model when the
# Space is built, so nothing has to be uploaded for it.
# What SentenceTransformer("all-MiniLM-L6-v2").encode(items, normalize_embeddings=True) does:
# BERT word-piece vectors -> average over the real tokens (mean pooling) -> scale to length 1.
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
EMBED_MODEL_DIR = os.environ.get("EMBED_MODEL_DIR",
                                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "embed_model"))
EMBED_MAX_TOKENS = 256    # the model's max_seq_length in sentence-transformers
EMBED_MAX_ITEMS = 500     # most items in one request
EMBED_BATCH = 64          # same batch size as the notebook

EMBED_READY = threading.Event()
_embed_tokenizer = None
_embed_model = None


def _load_embed_model():
    global _embed_tokenizer, _embed_model
    try:
        _embed_tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL_DIR)
        _embed_model = AutoModel.from_pretrained(EMBED_MODEL_DIR)
        _embed_model.eval()
        EMBED_READY.set()
        print("[EMBED] Model loaded from", EMBED_MODEL_DIR)
    except Exception as error:  # the NER part keeps working without it
        print("[EMBED] Could not load the embedding model:", error)


threading.Thread(target=_load_embed_model, daemon=True).start()


def embed_items(items):
    vectors = []
    for start in range(0, len(items), EMBED_BATCH):
        batch = items[start:start + EMBED_BATCH]
        encoded = _embed_tokenizer(batch, padding=True, truncation=True,
                                   max_length=EMBED_MAX_TOKENS, return_tensors="pt")
        with torch.no_grad():
            tokens = _embed_model(**encoded).last_hidden_state          # (batch, tokens, 384)
        mask = encoded["attention_mask"].unsqueeze(-1).to(tokens.dtype)  # 1 = real token, 0 = padding
        mean = (tokens * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
        vectors.extend(torch.nn.functional.normalize(mean, p=2, dim=1).tolist())
    return vectors


# ---------- Routes ----------

@app.get("/")
@app.get("/health")
def health():
    return jsonify({"status": "ok", "model_ready": MODEL_READY.is_set(), "embed_ready": EMBED_READY.is_set(),
                    "directions_ready": DIRECTIONS_READY.is_set()})


@app.post("/extract")
def extract():
    if not _authorized():
        return jsonify({"error": "unauthorized", "message": "Wrong or missing NER token."}), 401
    if not MODEL_READY.is_set():
        return jsonify({"error": "loading", "message": "The model is still loading. Try again in a moment."}), 503

    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"error": "no_text", "message": "No recipe text was sent."}), 400
    if len(text) > MAX_CHARS:
        return jsonify({"error": "too_long", "message": f"Recipe text is over {MAX_CHARS} characters."}), 400

    try:
        return jsonify({"ingredients": extract_ingredients(text)})
    except Exception as error:
        app.logger.exception("NER failed")
        return jsonify({"error": "ner_failed", "message": f"NER failed: {error}"}), 500


@app.post("/extract_directions")
def extract_directions_route():
    if not _authorized():
        return jsonify({"error": "unauthorized", "message": "Wrong or missing NER token."}), 401
    if not DIRECTIONS_READY.is_set():
        if _dir_error:
            return jsonify({"error": "no_directions_model", "message": "The directions model is not available."}), 404
        return jsonify({"error": "loading", "message": "The directions model is still loading. Try again in a moment."}), 503

    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"error": "no_text", "message": "No directions were sent."}), 400
    if len(text) > MAX_CHARS:
        return jsonify({"error": "too_long", "message": f"Directions are over {MAX_CHARS} characters."}), 400

    try:
        return jsonify({"entities": extract_directions(text)})
    except Exception as error:
        app.logger.exception("Directions NER failed")
        return jsonify({"error": "ner_failed", "message": f"Directions NER failed: {error}"}), 500


@app.post("/embed")
def embed():
    if not _authorized():
        return jsonify({"error": "unauthorized", "message": "Wrong or missing NER token."}), 401
    if not EMBED_READY.is_set():
        return jsonify({"error": "loading", "message": "The embedding model is still loading. Try again in a moment."}), 503

    data = request.get_json(silent=True) or {}
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return jsonify({"error": "no_items", "message": "Send {\"items\": [\"...\", ...]}."}), 400
    if len(items) > EMBED_MAX_ITEMS:
        return jsonify({"error": "too_many", "message": f"At most {EMBED_MAX_ITEMS} items per request."}), 400
    items = [str(item).strip().lower()[:200] for item in items]

    try:
        vectors = embed_items(items)
        return jsonify({"model": EMBED_MODEL_NAME, "dim": len(vectors[0]), "vectors": vectors})
    except Exception as error:
        app.logger.exception("Embedding failed")
        return jsonify({"error": "embed_failed", "message": f"Embedding failed: {error}"}), 500
