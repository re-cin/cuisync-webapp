import logging
import os
import re
import sys
import threading
import time
from functools import lru_cache

import cv2
import numpy as np
from flask import has_app_context

try:  # lets OCR_API_URL also come from the .env file
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- Where the OCR runs ---
# Locally PaddleOCR runs on this computer. If OCR_API_URL is set (like on Render),
# the photo is sent to the Hugging Face Space instead, so PaddleOCR isn't needed here.
OCR_API_URL = os.environ.get("OCR_API_URL", "").strip().rstrip("/")
OCR_API_TOKEN = os.environ.get("OCR_API_TOKEN", "").strip()
# A sleeping Space can take a minute or two to wake up
OCR_API_TIMEOUT = float(os.environ.get("OCR_API_TIMEOUT", "240"))

if OCR_API_URL:
    # Import it once here. Importing inside the function broke when two threads
    # did it at the same time.
    import requests
else:
    from paddleocr import TextDetection, TextRecognition
    from paddlex.inference.pipelines.components import CropByPolys, SortQuadBoxes


# --- Setup ---

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR = os.path.join(ROOT_DIR, ".vendor")

# Some packages are in .vendor instead of the venv
if os.path.isdir(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

try:
    from unidecode import unidecode
except ModuleNotFoundError:
    raise ModuleNotFoundError(
        "The 'unidecode' package is required for OCR romanization. "
        "Install it with: pip install unidecode"
    ) from None

try:
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate
except ModuleNotFoundError:
    raise ModuleNotFoundError(
        "The 'indic-transliteration' package is required for "
        "Devanagari romanization. "
        "Install it with: pip install indic-transliteration"
    ) from None

# pykakasi is optional, without it Japanese uses unidecode
try:
    import pykakasi
    _kakasi = pykakasi.kakasi()
except ModuleNotFoundError:
    try:
        if VENDOR_DIR not in sys.path:
            sys.path.insert(0, VENDOR_DIR)
        import pykakasi
        _kakasi = pykakasi.kakasi()
    except ModuleNotFoundError:
        _kakasi = None

# PyThaiNLP is optional too. Without it Thai uses unidecode, which messes up the letters.
try:
    from pythainlp.tokenize import word_tokenize as _thai_word_tokenize
    from pythainlp.transliterate import romanize as _thai_romanize
except ModuleNotFoundError:
    _thai_word_tokenize = None
    _thai_romanize = None
    print(
        "[CuiSync] WARNING: PyThaiNLP is not installed, so Thai menus will be "
        "romanized badly and won't match. Fix: python -m pip install pythainlp",
        flush=True,
    )

logger = logging.getLogger(__name__)


# --- Languages and models ---

# Country -> PaddleOCR language code
COUNTRY_LANG_MAP = {
    "china": "ch",
    "india": "hi",
    "indonesia": "id",
    "japan": "japan",
    "malaysia": "ms",
    "philippines": "tl",
    "saudi arabia": "ar",
    "south korea": "korean",
    "thailand": "th",
    "turkiye": "tr",
    "turkey": "tr",
    "vietnam": "vi",
}

# Recognition model for each language. Some share one model (like all Latin ones).
PP_OCRV5_REC_MODELS = {
    "ch": "PP-OCRv5_mobile_rec",  # also reads Japanese and English
    "japan": "PP-OCRv5_mobile_rec",
    "korean": "korean_PP-OCRv5_mobile_rec",
    "th": "th_PP-OCRv5_mobile_rec",
    "ar": "arabic_PP-OCRv5_mobile_rec",
    "hi": "devanagari_PP-OCRv5_mobile_rec",
    "en": "en_PP-OCRv5_mobile_rec",
    "id": "latin_PP-OCRv5_mobile_rec",
    "ms": "latin_PP-OCRv5_mobile_rec",
    "tl": "latin_PP-OCRv5_mobile_rec",
    "tr": "latin_PP-OCRv5_mobile_rec",
    "vi": "latin_PP-OCRv5_mobile_rec",
}

# This model finds where the text is, the ones above read it
DETECTION_MODEL = "PP-OCRv5_mobile_det"

# Languages to try when we don't know the country.
# English is first so it wins ties on English menus.
AUTO_OCR_LANGUAGES = (
    "en", "japan", "ch", "korean", "th", "hi",
    "ar", "vi", "id", "ms", "tl", "tr",
)

# Languages that use the Latin alphabet
LATIN_LANGUAGES = ("en", "vi", "id", "ms", "tl", "tr")

# The language check only reads this many of the widest lines
PROBE_LINES = 3

# Lines below this confidence are thrown away
MIN_LINE_CONFIDENCE = 0.20

# How many lines a model reads at once
RECOGNITION_BATCH_SIZE = 8

# Lines below this are read again by the other models and the most sure reading wins.
# This catches a dish name in a different script, like a Korean title on an English recipe.
REREAD_CONFIDENCE = 0.70

# Too many unsure lines = the whole photo is bad (blurry or unsupported),
# so re-reading won't help and just makes it slower
MAX_REREAD_LINES = 8

# A Latin model can be "sure" about junk it reads from a Korean or Thai title.
# So short lines (dish names) are also re-read by the non-Latin models,
# and only kept if that model is sure and really finds its own script.
SHORT_LINE_CHARS = 20
MAX_SHORT_REREAD_LINES = 30


# --- Matching settings ---

# A dish name can wrap onto at most this many lines
MAX_JOINED_LINES = 3

# Typo tolerance for OCR mistakes. Kept strict so a different dish isn't matched.
FUZZY_MIN_LEN = 8  # shorter names must match exactly
FUZZY_MAX_LEN_DIFF = 2
FUZZY_LETTERS_PER_EDIT = 8  # about 1 wrong letter per 8 letters

# Only check the first 60 lines so big menus stay fast
MAX_CANDIDATE_LINES = 60

# Sounds-alike matching for Thai and Arabic. Since it's looser, Thai text is only
# compared with Thai dishes and Arabic with Saudi dishes (native name only).
LOOSE_MATCH_COUNTRY = {"th": "thailand", "ar": "saudi arabia"}
LOOSE_FUZZY_MIN_LEN = 7  # shorter keys must match exactly

# For picking the language: bonus if a model finds its own script,
# penalty if it only read Latin text (so it doesn't win on English menus)
SCRIPT_MATCH_BONUS = 3.0
SCRIPT_MISMATCH_PENALTY = 0.5


# --- Script detection ---

KANA_RANGE = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")  # Japanese kana
CJK_RANGE = re.compile(r"[\u4E00-\u9FFF]")  # Chinese characters / kanji
HANGUL_RANGE = re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF]")  # Korean
THAI_RANGE = re.compile(r"[\u0E00-\u0E7F]")
SEA_RANGE = THAI_RANGE  # only Thai for now
ARABIC_RANGE = re.compile(r"[\u0600-\u06FF]")
DEVANAGARI_RANGE = re.compile(r"[\u0900-\u097F]")  # Hindi
LATIN_DIACRITIC_RANGE = re.compile(r"[\u00C0-\u024F\u1E00-\u1EFF]")  # accented letters (Vietnamese, Turkish)

# Khmer and Burmese. PaddleOCR has no model for them, we only use these to detect them.
KHMER_RANGE = re.compile(r"[\u1780-\u17FF\u19E0-\u19FF]")
MYANMAR_RANGE = re.compile(r"[\u1000-\u109F\uAA60-\uAA7F]")

# Readable menus usually get 0.9+ confidence. Khmer/Burmese only get around 0.55
# since every model is guessing, so below this = unsupported script.
MIN_READABLE_CONFIDENCE = 0.70

UNSUPPORTED_SCRIPT_MESSAGE = (
    "We couldn't read the text on this menu. It looks like it may be written "
    "in Khmer or Burmese, which CuiSync can't read yet (or the photo may be too "
    "blurry). Try a clearer photo, or a menu with English or romanized dish names."
)


# Photos with no text (food, scenery) still get a few weak "text" boxes.
# Khmer/Burmese also read as gibberish, so the reading can't tell them apart,
# but the detector can: it's sure about real writing, not about things that only look like text.
# Below this average box score = no text.
MIN_TEXT_DETECTION_SCORE = 0.70


class UnsupportedScriptError(Exception):
    """The text is in a script we can't read (like Khmer or Burmese).
    `lines` keeps whatever could still be read.
    """

    def __init__(self, message=UNSUPPORTED_SCRIPT_MESSAGE, lines=None):
        super().__init__(message)
        self.lines = lines or []


# The script each non-Latin model should find
SCRIPT_BY_LANGUAGE = {
    "korean": HANGUL_RANGE,
    "th": THAI_RANGE,
    "hi": DEVANAGARI_RANGE,
    "ar": ARABIC_RANGE,
}


def _print_timing(message):
    # print() so it always shows in the terminal, even without debug mode
    print(f"[CuiSync] {message}", flush=True)


def get_logger():
    # Flask's logger inside a request, the normal one outside
    if has_app_context():
        from flask import current_app
        return current_app.logger
    return logger


# --- Running OCR ---
# 1. Find where the text is (one detection model).
# 2. Read the widest lines with every model and pick the best language.
# 3. Read all the lines with only that model.
# Reading is the slow part (about 2s per model), so this is way faster than using every model.

def prepare_ocr_image(image, max_side=1600):
    """Shrink big photos before OCR. Phone photos can be 4000px+, which is slow
    and doesn't help. Small images are left alone.
    """
    if image is None:
        raise ValueError("OCR image is None.")
    if not isinstance(image, np.ndarray):
        raise TypeError(
            "OCR image must be a numpy.ndarray. "
            f"Received: {type(image).__name__}"
        )
    if image.size == 0:
        raise ValueError("OCR image is empty.")

    height, width = image.shape[:2]
    longest_side = max(height, width)
    if longest_side <= max_side:
        return image

    scale = max_side / float(longest_side)
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))
    return cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)


# Settings for every model. enable_mkldnn=False because MKLDNN crashes Paddle 3.3
# on Linux servers like the Hugging Face Space. Macs don't use it anyway.
_COMMON_MODEL_KWARGS = dict(engine="paddle_static", enable_mkldnn=False)

_DETECTOR = None
_RECOGNIZERS = {}  # model name -> loaded model
_MODEL_LOCK = threading.Lock()

# PaddleOCR helpers: sort boxes in reading order, then cut out each line
if not OCR_API_URL:
    _sort_boxes = SortQuadBoxes()
    _crop_by_polys = CropByPolys(det_box_type="quad")


# --- Remote OCR (Hugging Face Space) ---

def _remote_request(method, path, **kwargs):
    """Call the OCR Space. While it's waking up it returns 503 errors,
    so keep retrying until OCR_API_TIMEOUT runs out.
    """

    headers = {"Authorization": f"Bearer {OCR_API_TOKEN}"} if OCR_API_TOKEN else {}
    deadline = time.monotonic() + OCR_API_TIMEOUT
    while True:
        try:
            response = requests.request(
                method, OCR_API_URL + path, headers=headers,
                timeout=max(5.0, deadline - time.monotonic()), **kwargs,
            )
            if response.status_code not in (502, 503, 504):
                response.raise_for_status()
                return response.json()
            problem = f"HTTP {response.status_code}"
        except (requests.ConnectionError, requests.Timeout) as error:
            problem = type(error).__name__
        if time.monotonic() > deadline:
            raise RuntimeError(f"The OCR service didn't answer in time ({problem}). Please try again.")
        _print_timing(f"OCR service not ready yet ({problem}), retrying...")
        time.sleep(5)


def _remote_ocr(image, country=""):
    """Send the photo to the Space and get back the lines it read."""
    start_time = time.perf_counter()
    image = prepare_ocr_image(image)
    ok, encoded = cv2.imencode(".png", image)  # PNG so no quality is lost
    if not ok:
        raise ValueError("Could not encode the image for the OCR service.")

    data = _remote_request(
        "POST", "/ocr",
        files={"image": ("menu.png", encoded.tobytes(), "image/png")},
        data={"country": country or ""},
    )
    lines = data.get("lines") or []
    _print_timing(f"Remote OCR finished in {time.perf_counter() - start_time:.2f}s ({len(lines)} lines read)")
    if data.get("error") == "unsupported_script":
        raise UnsupportedScriptError(data.get("message") or UNSUPPORTED_SCRIPT_MESSAGE, lines=lines)
    if data.get("error"):
        raise RuntimeError(data.get("message") or data["error"])
    return lines


def _get_detector():
    """The detection model, loaded the first time it's needed."""
    global _DETECTOR
    if _DETECTOR is None:
        with _MODEL_LOCK:
            if _DETECTOR is None:
                get_logger().info("Loading OCR detection model %s", DETECTION_MODEL)
                _DETECTOR = TextDetection(
                    model_name=DETECTION_MODEL,
                    limit_side_len=736,
                    limit_type="max",
                    thresh=0.30,
                    box_thresh=0.50,
                    unclip_ratio=1.6,
                    **_COMMON_MODEL_KWARGS,
                )
    return _DETECTOR


def _get_recognizer(model_name):
    """A recognition model, loaded the first time it's needed.
    Languages that share a model share the same loaded copy.
    """
    recognizer = _RECOGNIZERS.get(model_name)
    if recognizer is None:
        with _MODEL_LOCK:
            recognizer = _RECOGNIZERS.get(model_name)
            if recognizer is None:
                get_logger().info("Loading OCR recognition model %s", model_name)
                recognizer = TextRecognition(model_name=model_name, **_COMMON_MODEL_KWARGS)
                _RECOGNIZERS[model_name] = recognizer
    return recognizer


def warm_up():
    """Load every model and run each once on a blank image.

    Loading takes 20+ seconds, so app.py runs this in the background at startup
    and the first upload doesn't have to wait. On Render it just wakes up the Space.
    """
    start_time = time.perf_counter()
    if OCR_API_URL:
        try:
            _remote_request("GET", "/health")
            _print_timing(f"OCR service awake ({time.perf_counter() - start_time:.1f}s)")
        except Exception as error:
            _print_timing(f"Could not wake the OCR service: {error}")
        return
    blank = np.full((48, 240, 3), 255, dtype=np.uint8)
    try:
        list(_get_detector().predict(blank))
        for model_name in dict.fromkeys(PP_OCRV5_REC_MODELS.values()):
            list(_get_recognizer(model_name).predict([blank]))
    except Exception:
        get_logger().exception("OCR warm-up failed.")
        return
    _print_timing(f"OCR models ready in {time.perf_counter() - start_time:.1f}s")


def find_text_lines_with_scores(image):
    """Like find_text_lines, but also returns the detector's score for each box."""
    result = next(iter(_get_detector().predict(image)), None)
    if result is None or len(result["dt_polys"]) == 0:
        return [], []

    try:
        det_scores = [float(score) for score in result["dt_scores"]]
    except (KeyError, TypeError):
        det_scores = []
    boxes = _sort_boxes(result["dt_polys"])
    crops = _crop_by_polys(image, boxes)
    crops = [crop for crop in crops if crop.size and crop.shape[0] and crop.shape[1]]
    return crops, det_scores


def find_text_lines(image):
    """Find the text and cut out each line as a small image, in reading order."""
    crops, _ = find_text_lines_with_scores(image)
    return crops


def read_lines(crops, model_name):
    """Read every cropped line with one model.
    Returns a (text, confidence) pair for each line, in the same order.
    """
    if not crops:
        return []

    # Lines with a similar shape read faster together, so sort by width/height
    # and put the results back in order after
    order = sorted(range(len(crops)), key=lambda i: crops[i].shape[1] / float(crops[i].shape[0]))
    results = [("", 0.0)] * len(crops)

    predictions = _get_recognizer(model_name).predict(
        [crops[i] for i in order], batch_size=RECOGNITION_BATCH_SIZE
    )
    for i, prediction in zip(order, predictions):
        results[i] = (str(prediction["rec_text"]).strip(), float(prediction["rec_score"]))

    return results


def _good_lines(readings):
    """The lines worth keeping, plus every line's confidence (empty = 0)
    for the unsupported-script check.
    """
    lines = [text for text, score in readings if text and score >= MIN_LINE_CONFIDENCE]
    scores = [score if text else 0.0 for text, score in readings]
    return lines, scores


def ocr_quality(lines, scores, language=""):
    """Score how good one language's result is, so we can pick the best one.

    Base score = amount of text read x confidence. Then it goes up or down
    depending on whether the model found its own script (the Korean model only
    gets a bonus if it really found Korean letters).
    """
    base = sum(max(score, 0.0) * len(line) for line, score in zip(lines, scores))

    full_text = "".join(lines)
    has_kana = bool(KANA_RANGE.search(full_text))
    has_cjk = bool(CJK_RANGE.search(full_text))

    if language == "japan":
        if has_kana:
            base *= SCRIPT_MATCH_BONUS
        elif not has_cjk:
            base *= SCRIPT_MISMATCH_PENALTY

    elif language == "ch":
        if has_cjk and not has_kana:
            base *= 1.8
        elif has_kana:
            # Kana means Japanese, so the Japanese model should win
            base *= SCRIPT_MISMATCH_PENALTY
        elif not has_cjk:
            base *= SCRIPT_MISMATCH_PENALTY

    elif language in SCRIPT_BY_LANGUAGE:
        # Korean, Thai, Hindi, Arabic
        if SCRIPT_BY_LANGUAGE[language].search(full_text):
            base *= SCRIPT_MATCH_BONUS
        else:
            base *= SCRIPT_MISMATCH_PENALTY

    elif language in ("vi", "tr"):
        # Vietnamese and Turkish have lots of accents
        if LATIN_DIACRITIC_RANGE.search(full_text):
            base *= 1.3

    elif language == "en":
        # Small boost so English wins ties
        base *= 1.05

    return base


def _average(values):
    return sum(values) / len(values) if values else 0.0


def looks_like_no_text(lines, box_scores, det_scores):
    """True if the photo most likely has no real text."""
    full_text = "".join(lines)
    if KHMER_RANGE.search(full_text) or MYANMAR_RANGE.search(full_text):
        return False

    best_average = max((_average(scores) for scores in box_scores), default=0.0)
    det_average = _average(det_scores)
    _print_timing(
        f"Reading confidence {best_average:.2f}, detection score {det_average:.2f} "
        f"({len(det_scores)} boxes)"
    )

    # Read fine, so it's real text
    if best_average >= MIN_READABLE_CONFIDENCE:
        return False

    # No detector scores, let the unsupported-script check decide
    if not det_scores:
        return False

    return det_average < MIN_TEXT_DETECTION_SCORE


def check_supported_script(lines, box_scores):
    """Raise UnsupportedScriptError if the text looks like Khmer or Burmese,
    or if text was found but even the best model was just guessing.
    """
    full_text = "".join(lines)
    if KHMER_RANGE.search(full_text) or MYANMAR_RANGE.search(full_text):
        raise UnsupportedScriptError(lines=lines)

    found_text = any(box_scores)
    best_average = max((_average(scores) for scores in box_scores), default=0.0)

    if found_text and best_average < MIN_READABLE_CONFIDENCE:
        _print_timing(f"Text looks unreadable (best average confidence {best_average:.2f})")
        raise UnsupportedScriptError(lines=lines)


def _pick_language(crops):
    """The quick language check. Reads the widest lines with every model and
    returns the best language, plus each model's confidences.
    """
    probe = sorted(crops, key=lambda crop: crop.shape[1], reverse=True)[:PROBE_LINES]

    model_groups = {}
    for language in AUTO_OCR_LANGUAGES:
        model_groups.setdefault(PP_OCRV5_REC_MODELS[language], []).append(language)

    best_language, best_score, best_lines = "en", -1.0, []
    scores_per_model = []

    for model_name, languages in model_groups.items():
        try:
            lines, scores = _good_lines(read_lines(probe, model_name))
        except Exception:
            get_logger().exception("OCR model '%s' failed.", model_name)
            continue

        scores_per_model.append(scores)
        kept_scores = [score for score in scores if score >= MIN_LINE_CONFIDENCE]

        # Languages sharing a model read the same text but get scored a bit differently
        for language in languages:
            score = ocr_quality(lines, kept_scores, language)
            if score > best_score:
                best_language, best_score, best_lines = language, score, lines

    return best_language, scores_per_model, best_lines


def _model_script(model_name):
    """The script a non-Latin model is made for (None for English/Latin)."""
    if model_name == PP_OCRV5_REC_MODELS["ch"]:
        return re.compile(f"{CJK_RANGE.pattern}|{KANA_RANGE.pattern}")
    for language, script in SCRIPT_BY_LANGUAGE.items():
        if model_name == PP_OCRV5_REC_MODELS[language]:
            return script
    return None


def read_lines_with_reread(crops, model_name):
    """Read every line with the chosen model, then fix lines in a different script:

    1. Unsure lines are re-read by every other model, the most confident reading wins.
    2. If the model is English/Latin, short lines (likely dish names) are also re-read
       by the non-Latin models, and kept only if that model is sure and finds its
       own script (like the Korean model reading Korean letters).

    Returns (text, confidence) pairs like read_lines.
    """
    readings = read_lines(crops, model_name)

    # DEBUG (temporary): what the model read on each line
    _print_timing(f"[debug] model={model_name}, {len(readings)} lines")
    for i, (text, score) in enumerate(readings):
        _print_timing(f"[debug]   line {i}: {score:.2f}  {text!r}")

    unsure = [i for i, (_, score) in enumerate(readings) if score < REREAD_CONFIDENCE]
    if len(unsure) > MAX_REREAD_LINES or len(unsure) > len(readings) / 2:
        unsure = []

    short = []
    if _model_script(model_name) is None:
        short = [
            i for i, (text, _) in enumerate(readings)
            if i not in unsure and text and len(text) <= SHORT_LINE_CHARS
        ][:MAX_SHORT_REREAD_LINES]

    _print_timing(f"[debug] unsure lines: {unsure}, short lines: {short}")
    to_reread = unsure + short
    if not to_reread:
        return readings

    readings = list(readings)
    reread_crops = [crops[i] for i in to_reread]
    unsure_set = set(unsure)

    for other_model in dict.fromkeys(PP_OCRV5_REC_MODELS.values()):
        if other_model == model_name:
            continue
        script = _model_script(other_model)
        if script is None and not unsure:
            continue  # Latin models only help with the unsure lines
        try:
            results = read_lines(reread_crops, other_model)
        except Exception:
            get_logger().exception("OCR re-read with model '%s' failed.", other_model)
            continue

        for i, (text, score) in zip(to_reread, results):
            _print_timing(f"[debug]   re-read line {i} with {other_model}: {score:.2f}  {text!r}")  # DEBUG
            if not text or score < REREAD_CONFIDENCE:
                continue
            if i in unsure_set:
                # Unsure line: keep the most confident reading
                if score > readings[i][1]:
                    readings[i] = (text, score)
            elif script is not None and script.search(text):
                # Short line: only replace it with real non-Latin text
                if _model_script_text(readings[i][0]) is None:
                    readings[i] = (text, score)
                elif score > readings[i][1]:
                    readings[i] = (text, score)

    _print_timing(f"Re-read {len(to_reread)} line(s) with the other models")
    return readings


def _model_script_text(text):
    """Which non-Latin script the text is in (None if Latin only)."""
    for pattern in (CJK_RANGE, KANA_RANGE, HANGUL_RANGE, THAI_RANGE,
                    ARABIC_RANGE, DEVANAGARI_RANGE):
        if pattern.search(text):
            return pattern
    return None


def _second_latin_reading(best_lines, crops):
    """Extra lines for accented Latin menus (mostly Vietnamese), read by the Chinese model.

    The Latin model drops stacked accents ("Phở" -> "Ph") but the Chinese model
    swaps in a look-alike ("Phó"), which romanizes to the right spelling.
    Lines that are the same as the first reading are skipped.
    """
    lines, _ = _good_lines(read_lines(crops, PP_OCRV5_REC_MODELS["ch"]))
    seen = {match_key(line) for line in best_lines}
    extra = []

    for line in lines:
        if CJK_RANGE.search(line) or KANA_RANGE.search(line):
            continue
        key = match_key(line)
        if key and key not in seen:
            seen.add(key)
            extra.append(line)

    return extra


def rescue_readable_lines(crops):
    """Only used when the main text can't be read (Khmer, Burmese...).
    Reads every line with every model and keeps the lines some model read
    with 0.70+ confidence (like an English dish name), in reading order.
    """
    start_time = time.perf_counter()
    crops = crops[:MAX_CANDIDATE_LINES]
    best = [("", 0.0)] * len(crops)

    for model_name in dict.fromkeys(PP_OCRV5_REC_MODELS.values()):
        try:
            results = read_lines(crops, model_name)
        except Exception:
            get_logger().exception("OCR rescue read with model '%s' failed.", model_name)
            continue
        for i, (text, score) in enumerate(results):
            if text and score > best[i][1]:
                best[i] = (text, score)

    rescued = [text for text, score in best if text and score >= REREAD_CONFIDENCE]
    _print_timing(
        f"Rescue read finished in {time.perf_counter() - start_time:.2f}s "
        f"({len(rescued)} of {len(crops)} lines readable): {rescued}"
    )
    return rescued


def automatic_ocr(image):
    """Read the menu when we don't know the country.
    Returns the lines of text that were read.
    """
    if OCR_API_URL:
        return _remote_ocr(image)
    start_time = time.perf_counter()
    image = prepare_ocr_image(image)

    crops, det_scores = find_text_lines_with_scores(image)
    if not crops:
        _print_timing(f"OCR finished in {time.perf_counter() - start_time:.2f}s (no text found)")
        return []

    language, probe_scores, probe_lines = _pick_language(crops)

    # No real text, app.py shows "No Text Detected"
    if looks_like_no_text(probe_lines, probe_scores, det_scores):
        _print_timing(f"OCR finished in {time.perf_counter() - start_time:.2f}s (no text found)")
        return []

    # Khmer, Burmese or unreadable: stop here. But first save any line some model
    # can read (like an English "Fish Amok" on a Khmer menu) so it can still match.
    try:
        check_supported_script(probe_lines, probe_scores)
    except UnsupportedScriptError:
        rescued = rescue_readable_lines(crops)
        raise UnsupportedScriptError(lines=rescued + probe_lines) from None

    lines, scores = _good_lines(read_lines_with_reread(crops, PP_OCRV5_REC_MODELS[language]))

    # Vietnamese/Turkish: also add the Chinese model's reading,
    # it keeps some accents the Latin model drops
    if language in LATIN_LANGUAGES and LATIN_DIACRITIC_RANGE.search("".join(lines)):
        lines = lines + _second_latin_reading(lines, crops)

    elapsed = time.perf_counter() - start_time
    _print_timing(
        f"OCR finished in {elapsed:.2f}s "
        f"({len(crops)} text lines found, picked '{language}', {len(lines)} lines read)"
    )

    check_supported_script(lines, [scores])
    return lines


def automatic_ocr_for_country(image, country):
    """Faster version when the country is known: only that country's model
    reads the menu (English if the country isn't listed).
    """
    if OCR_API_URL:
        return _remote_ocr(image, country)
    start_time = time.perf_counter()
    image = prepare_ocr_image(image)

    country_key = (country or "").strip().lower()
    language = COUNTRY_LANG_MAP.get(country_key, "en")

    try:
        crops = find_text_lines(image)
        lines, scores = _good_lines(read_lines_with_reread(crops, PP_OCRV5_REC_MODELS[language]))
        # Same as automatic_ocr: Vietnamese/Turkish also get the Chinese model's reading
        if language in LATIN_LANGUAGES and LATIN_DIACRITIC_RANGE.search("".join(lines)):
            lines = lines + _second_latin_reading(lines, crops)
    except Exception:
        get_logger().exception("OCR failed for country='%s', language='%s'.", country, language)
        _print_timing(f"OCR failed after {time.perf_counter() - start_time:.2f}s")
        return []

    get_logger().info("PP-OCR extracted [%s/%s]: %s", country_key or "unknown", language, lines)
    _print_timing(
        f"OCR finished in {time.perf_counter() - start_time:.2f}s "
        f"(1 model, '{language}', {len(lines)} lines read)"
    )

    check_supported_script(lines, [scores])
    return lines


# --- Romanization ---
# Turns other scripts into Latin letters so they can be compared with the dataset.
# It doesn't translate: 红烧肉 becomes "Hong Shao Rou".

def romanize_japanese(text):
    """Japanese to Hepburn romaji (like ラーメン -> raamen)."""
    if not _kakasi:
        return str(text)

    try:
        result = _kakasi.convert(str(text))
        return " ".join(item["hepburn"] for item in result if item.get("hepburn")).strip()
    except Exception:
        get_logger().exception("Japanese romanization failed.")
        return str(text)


THAI_TONE_MARKS = re.compile(r"[\u0E48-\u0E4C]")


def romanize_thai(text):
    """Thai to Latin letters with the official Thai system (RTGS),
    like ไก่ย่างขมิ้น -> "kaiyang khmin".

    Thai has no spaces, so split it into words first. Tone marks are removed
    because they don't change the spelling and they confuse the romanizer.
    """
    words = _thai_word_tokenize(text, keep_whitespace=False)
    return " ".join(_thai_romanize(THAI_TONE_MARKS.sub("", word), engine="royin") for word in words)


@lru_cache(maxsize=8192)
def romanize_text(text, mode="standard"):
    """Romanize one piece of text. mode="japanese" uses Japanese readings for kanji
    instead of Chinese pinyin.
    """
    text = str(text)

    try:
        if DEVANAGARI_RANGE.search(text):
            text = transliterate(text, sanscript.DEVANAGARI, sanscript.ITRANS)
            # ITRANS adds a silent "a" at the end of Hindi words (गाजर -> "gAjara"),
            # but the dataset spells it "Gajar", so remove a small "a" after a consonant at the end
            text = re.sub(r"(?<=[b-df-hj-np-tv-z])a(?=\s|$)", "", text)
        elif mode == "japanese" and _kakasi:
            text = romanize_japanese(text)
        elif THAI_RANGE.search(text) and _thai_romanize:
            text = unidecode(romanize_thai(text))
        else:
            text = unidecode(text)
    except Exception:
        get_logger().exception("Romanization failed for text: %s", text)

    return re.sub(r"\s+", " ", text).strip()


class RomanizedLine(str):
    """A romanized line that remembers its script ("th", "ar" or None).
    Works like a normal string everywhere else.
    """
    script = None


def _line_script(line):
    if THAI_RANGE.search(line):
        return "th"
    if ARABIC_RANGE.search(line):
        return "ar"
    return None


def _tag(text, script):
    line = RomanizedLine(text)
    line.script = script
    return line


def romanize_lines(lines):
    """Romanize all OCR lines. Chinese/Japanese text gets two versions
    (pinyin and romaji) since kanji can be read both ways.
    """
    clean_lines = [str(line).strip() for line in lines if str(line).strip()]
    romanized_std = [
        _tag(romanize_text(line, mode="standard"), _line_script(line))
        for line in clean_lines
    ]

    has_cjk = any(CJK_RANGE.search(line) or KANA_RANGE.search(line) for line in clean_lines)

    if has_cjk:
        romanized_ja = [romanize_text(line, mode="japanese") for line in clean_lines]
        get_logger().info("Romanized text (Standard/Pinyin): %s", " ".join(romanized_std))
        get_logger().info("Romanized text (Japanese Hepburn): %s", " ".join(romanized_ja))
        return list(dict.fromkeys(romanized_std + romanized_ja))

    has_sea = any(SEA_RANGE.search(line) for line in clean_lines)

    if has_sea:
        get_logger().info("Romanized text (Thai): %s", " ".join(romanized_std))
        return list(dict.fromkeys(romanized_std))

    get_logger().info("Romanized text: %s", " ".join(romanized_std))
    return romanized_std


# --- Matching OCR text to recipes ---

@lru_cache(maxsize=16384)
def match_key(text, mode="standard"):
    """Simplify a name for comparing: lowercase, no spaces, accents or punctuation.
    "Kare-Kare" and "kare kare" both become "karekare".
    """
    romanized = romanize_text(text, mode=mode)
    return re.sub(r"[^a-z0-9]+", "", romanized.casefold())


def clean_menu_line(line):
    """Remove prices and dots from a menu line,
    like "Chicken Adobo ..... P150.00" -> "Chicken Adobo".
    """
    line = str(line)

    # Numbers and currency symbols (peso, dollar, euro, yen, pound, won, baht)
    line = re.sub(r"[\d\u20B1$\u20AC\u00A5\u00A3\u20A9\u0E3F]+", " ", line)

    # Runs of dots, dashes and similar separators
    line = re.sub(r"[.\u2026_\-\u2013\u2014|:]{2,}", " ", line)

    return re.sub(r"\s+", " ", line).strip()


def menu_candidates(romanized_lines):
    """Possible dish names on the menu. Each line counts on its own, and up to
    MAX_JOINED_LINES lines in a row are also joined (for names that wrap onto the next line).
    """
    raw_keys = []

    for line in romanized_lines:
        line_str = clean_menu_line(line)
        if not line_str:
            continue

        key_std = match_key(line_str, mode="standard")
        if key_std:
            raw_keys.append(key_std)

        # Also add the Japanese reading if the line still has kanji/kana
        if CJK_RANGE.search(line_str) or KANA_RANGE.search(line_str):
            key_ja = match_key(line_str, mode="japanese")
            if key_ja and key_ja != key_std:
                raw_keys.append(key_ja)

    raw_keys = raw_keys[:MAX_CANDIDATE_LINES]
    candidates = set(raw_keys)

    # Join lines in a row (for names that wrap)
    for start in range(len(raw_keys)):
        joined = raw_keys[start]
        for end in range(start + 1, min(start + MAX_JOINED_LINES, len(raw_keys))):
            joined += raw_keys[end]
            candidates.add(joined)

    return candidates


def loose_key(text, script):
    """A "sounds-alike" version of a Thai or Arabic name.

    The dataset spells these the casual way ("Gai", "Baqsam") but the romanizer doesn't
    ("kai", and Arabic has no short vowels so بقسم is just "bqsm"). So drop the vowels,
    treat letters that sound alike as the same, and squash double letters:
        "Gai Yang Khamin" and "kaiyang khmin" -> "kynkmn"
        "Baqsam" and "bqsm"                   -> "bksm"
    """
    key = match_key(text)

    if script == "th":
        for a, b in (("ph", "p"), ("th", "t"), ("kh", "k"), ("ch", "c"),
                     ("j", "c"), ("g", "k"), ("d", "t"), ("b", "p")):
            key = key.replace(a, b)
        key = re.sub(r"[aeiou]", "", key)
    else:
        for a, b in (("q", "k"), ("g", "k"), ("kh", "k"), ("th", "t"),
                     ("dh", "d"), ("sh", "s")):
            key = key.replace(a, b)
        # w and y are usually long vowels in Arabic, so drop them too
        key = re.sub(r"[aeiouwy]", "", key)

    return re.sub(r"(.)\1+", r"\1", key)


def loose_candidates(romanized_lines):
    """Sounds-alike keys for the Thai and Arabic lines, grouped by script.
    Lines in a row are joined too, like menu_candidates().
    """
    by_script = {}
    previous = []  # (script, key) of the previous lines

    for line in list(romanized_lines)[:MAX_CANDIDATE_LINES]:
        script = getattr(line, "script", None)
        if script not in LOOSE_MATCH_COUNTRY:
            previous = []
            continue

        key = loose_key(clean_menu_line(line), script)
        if not key:
            continue

        keys = by_script.setdefault(script, set())
        keys.add(key)

        previous = [(s, k) for s, k in previous if s == script][-(MAX_JOINED_LINES - 1):]
        joined = key
        for _, earlier in reversed(previous):
            joined = earlier + joined
            keys.add(joined)
        previous.append((script, key))

    return by_script


def is_loose_match(name_key, loose_keys):
    """Return the sounds-alike key that matches, or None."""
    if not name_key:
        return None
    if name_key in loose_keys:
        return name_key
    if len(name_key) >= LOOSE_FUZZY_MIN_LEN:
        for candidate in loose_keys:
            if len(candidate) >= LOOSE_FUZZY_MIN_LEN and edit_distance(name_key, candidate, 1) <= 1:
                return candidate
    return None


def _single_line_keys(romanized_lines):
    """Keys of each menu line on its own (not joined), in the same form
    find_recipes() stores in "_matched".
    """
    keys = set()
    for line in list(romanized_lines)[:MAX_CANDIDATE_LINES]:
        text = clean_menu_line(line)
        if not text:
            continue
        keys.add(match_key(text, mode="standard"))
        if CJK_RANGE.search(text) or KANA_RANGE.search(text):
            keys.add(match_key(text, mode="japanese"))
        script = getattr(line, "script", None)
        if script in LOOSE_MATCH_COUNTRY:
            keys.add("~" + loose_key(text, script))
    return keys


def build_candidate_index(candidate_keys):
    """Set up the candidates for fast lookup: a set for exact matches and
    a dict by length so typo checks only compare names of similar length.
    """
    candidate_set = set(candidate_keys)

    length_buckets = {}
    for candidate in candidate_set:
        length_buckets.setdefault(len(candidate), []).append(candidate)

    return candidate_set, length_buckets


def is_recipe_match(title_key, candidate_set, length_buckets):
    """Check if a menu line is this recipe's name. Returns that line, or None.

    The whole line has to be the dish name, otherwise "Pork Adobo" would match
    a dish called "Pork". Small typos are only allowed for longer names.
    """
    if not title_key:
        return None

    # Exact match
    if title_key in candidate_set:
        return title_key

    # Short names must be exact, 1 wrong letter could be another dish
    if len(title_key) < FUZZY_MIN_LEN:
        return None

    max_edits = max(1, len(title_key) // FUZZY_LETTERS_PER_EDIT)

    for length in range(
        len(title_key) - FUZZY_MAX_LEN_DIFF,
        len(title_key) + FUZZY_MAX_LEN_DIFF + 1,
    ):
        for candidate in length_buckets.get(length, ()):
            if edit_distance(title_key, candidate, max_edits) <= max_edits:
                return candidate

    return None


def edit_distance(a, b, limit):
    """How many letters to add, remove or change to turn a into b.
    Stops early once it's over `limit`, since that's all we need to know.
    """
    if abs(len(a) - len(b)) > limit:
        return limit + 1

    previous = list(range(len(b) + 1))

    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(
                previous[j] + 1,  # remove a letter
                current[j - 1] + 1,  # add a letter
                previous[j - 1] + (char_a != char_b),  # change a letter
            ))

        if min(current) > limit:
            return limit + 1

        previous = current

    return previous[-1]


@lru_cache(maxsize=16384)
def title_key_variants(title, alternative_title="", country=""):
    """All the ways a recipe can be matched: its title and alternative title,
    plus Japanese readings for Japanese recipes.
    """
    is_japan = (country or "").strip().lower() in ("japan", "japanese")

    raw_names = [title, alternative_title]
    keys = []
    seen = set()

    for name in raw_names:
        if not name:
            continue

        if is_japan:
            key_ja = match_key(name, mode="japanese")
            if key_ja and key_ja not in seen:
                keys.append(key_ja)
                seen.add(key_ja)

        key_std = match_key(name, mode="standard")
        if key_std and key_std not in seen:
            keys.append(key_std)
            seen.add(key_std)

    return keys


def find_recipes(romanized_lines, origin_country="", recipes=None):
    """Find the recipes whose names are on the menu, and print how long it took."""
    start_time = time.perf_counter()
    result = _find_recipes(romanized_lines, origin_country, recipes)

    elapsed = time.perf_counter() - start_time
    _print_timing(f"Search finished in {elapsed:.3f}s ({len(result)} dishes found)")
    return result


def _find_recipes(romanized_lines, origin_country, recipes):
    """The actual search behind find_recipes()."""
    if not recipes:
        return []

    candidate_keys = menu_candidates(romanized_lines)
    if not candidate_keys:
        return []

    loose_by_script = loose_candidates(romanized_lines)

    candidate_set, length_buckets = build_candidate_index(candidate_keys)

    origin_key = (origin_country or "").strip().casefold()
    matches = []
    seen = set()

    # Only recipes from the chosen country (or all if none)
    filtered_recipes = []
    for recipe in recipes:
        title = (recipe.get("title", "") or "").strip()
        if not title:
            continue

        country = (recipe.get("country", "") or "").strip()
        if origin_key and country.casefold() != origin_key:
            continue

        filtered_recipes.append((recipe, title, country))

    for recipe, title, country in filtered_recipes:
        title_keys = title_key_variants(
            title,
            recipe.get("alternative_title", "") or "",
            country=country,
        )
        if not title_keys:
            continue

        # First menu line that matches any name of this recipe
        matched_candidate = next(
            (
                candidate
                for candidate in (
                    is_recipe_match(title_key, candidate_set, length_buckets)
                    for title_key in title_keys
                )
                if candidate
            ),
            None,
        )

        # Thai / Arabic: try the sounds-alike match on the native name
        if not matched_candidate:
            for script, loose_keys in loose_by_script.items():
                if country.casefold() != LOOSE_MATCH_COUNTRY[script]:
                    continue
                alt_key = loose_key(recipe.get("alternative_title", "") or "", script)
                hit = is_loose_match(alt_key, loose_keys)
                if hit:
                    matched_candidate = "~" + hit  # "~" keeps these apart from normal keys
                    break

        if not matched_candidate:
            continue

        # Skip duplicates (same title and country)
        identity = (title.casefold(), country.casefold())
        if identity in seen:
            continue

        matches.append({
            "title": title,
            "country": country,
            "alternative_title": (recipe.get("alternative_title", "") or "").strip(),
            "image_link": (recipe.get("image_link", "") or "").strip(),
            "_matched": matched_candidate,
        })
        seen.add(identity)

    # If "Chicken" / "Adobo" on two lines matched "Chicken Adobo", drop dishes that only
    # matched a piece of it (like "Chicken"). Only for joined lines: if "Udon" and
    # "Miso Udon" are both their own lines, both are real dishes.
    single_lines = _single_line_keys(romanized_lines)
    result = []
    for match in matches:
        matched_key = match["_matched"]
        is_redundant = any(
            match is not other
            and other["_matched"] not in single_lines
            and matched_key != other["_matched"]
            and matched_key in other["_matched"]
            for other in matches
        )
        if not is_redundant:
            result.append(match)

    # Remove the helper key only after all the comparisons are done
    for match in result:
        match.pop("_matched", None)

    return result
