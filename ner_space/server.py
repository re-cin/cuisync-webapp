"""CuiSync ingredient NER service (runs on a Hugging Face Space).

Uses the fine-tuned BERT model (BertForTokenClassification, labels O / B-INGREDIENT / I-INGREDIENT)
to pull the ingredients out of recipe text that an admin pastes on the Admin page.

    GET  /health   -> {"status": "ok", "model_ready": true/false}
    POST /extract  -> JSON {"text": "..."}
                      returns {"ingredients": [{"name": "...", "score": 0.97}, ...]}

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
from transformers import AutoModelForTokenClassification, AutoTokenizer

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

def _tag_words(words):
    """Tokenize the words, run BERT, and give each WORD one label.

    BERT splits words into word pieces ("lemongrass" -> "lemon", "##grass").
    Each word takes the label and confidence of its FIRST piece,
    which is how token classification models are usually trained.
    """
    encoded = _tokenizer(
        words,
        is_split_into_words=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )
    with torch.no_grad():
        logits = _model(**encoded).logits[0]
    probs = torch.softmax(logits, dim=-1)
    best_prob, best_id = probs.max(dim=-1)

    labels = [("O", 0.0)] * len(words)
    seen = set()
    for token_index, word_index in enumerate(encoded.word_ids(0)):
        if word_index is None or word_index in seen:  # [CLS], [SEP], or a later word piece
            continue
        seen.add(word_index)
        labels[word_index] = (_id2label[int(best_id[token_index])], float(best_prob[token_index]))
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


# ---------- Routes ----------

@app.get("/")
@app.get("/health")
def health():
    return jsonify({"status": "ok", "model_ready": MODEL_READY.is_set()})


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
