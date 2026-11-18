"""Finds out why the NER models don't load. Run in the project folder with the venv on:
    python check_ner.py
"""
import os
import sys


class _Tee:
    """Print to the screen and to check_ner_output.txt at the same time."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


_out = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_ner_output.txt"), "w", encoding="utf-8")
sys.stdout = sys.stderr = _Tee(sys.__stdout__, _out)
import faulthandler
faulthandler.enable(file=_out)                         # a hard crash still writes where it happened
faulthandler.dump_traceback_later(120, exit=True, file=_out)  # a freeze longer than 2 min too


def step(text):
    print(text)
    _out.flush()

ROOT = os.path.dirname(os.path.abspath(__file__))
print("Python:        ", sys.executable)
print("Project folder:", ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    print("python-dotenv is not installed in this Python (wrong venv?)")

for name, default in (("NER_MODEL_DIR", "ner_model"), ("NER_DIRECTIONS_MODEL_DIR", "ner_directions_model")):
    env = os.environ.get(name, "").strip()
    folder = env or os.path.join(ROOT, default)
    print(f"\n[{default}]")
    if env:
        print(f"  {name} is set in your environment -> the app looks in {env} instead!")
    print("  folder:", folder, "| exists:", os.path.isdir(folder))
    if os.path.isdir(folder):
        print("  files: ", sorted(os.listdir(folder)))
    print("  config.json found:", os.path.isfile(os.path.join(folder, "config.json")))

print("\n[packages]")
for pkg in ("torch", "transformers"):
    step(f"  importing {pkg} ...")
    try:
        module = __import__(pkg)
        print(f"  {pkg}: {getattr(module, '__version__', '?')}")
    except Exception as error:
        print(f"  {pkg}: CANNOT IMPORT -> {type(error).__name__}: {error}")
        import traceback
        traceback.print_exc()

print("\n[loading the models]")
try:
    from transformers import AutoModelForTokenClassification, AutoTokenizer
    for default in ("ner_model", "ner_directions_model"):
        folder = os.environ.get("NER_MODEL_DIR" if default == "ner_model" else "NER_DIRECTIONS_MODEL_DIR", "").strip() \
            or os.path.join(ROOT, default)
        step(f"  loading {default} ...")
        try:
            AutoTokenizer.from_pretrained(folder)
            model = AutoModelForTokenClassification.from_pretrained(folder)
            print(f"  {default}: OK, labels {sorted(set(model.config.id2label.values()))}")
        except Exception as error:
            print(f"  {default}: FAILED -> {type(error).__name__}: {error}")
except Exception as error:
    print(f"  transformers could not be used -> {type(error).__name__}: {error}")

print("\n[the app's own check]")
try:
    step("  starting ner.py ...")
    import ner
    ner.init(ROOT, ["salt"], None, None)
    import time
    for _ in range(60):          # wait up to a minute for the background load
        status = ner.status()
        if "loading" not in status["text"]:
            break
        time.sleep(1)
    print("  ingredients:", status["text"])
except Exception as error:
    print(f"  ner.py failed -> {type(error).__name__}: {error}")

step("\n[embedding package]  importing sentence_transformers ...")
try:
    import sentence_transformers
    print("  sentence_transformers:", sentence_transformers.__version__)
except Exception as error:
    print(f"  sentence_transformers: CANNOT IMPORT -> {type(error).__name__}: {error}")

faulthandler.cancel_dump_traceback_later()
print("\nDone. Saved in check_ner_output.txt")
