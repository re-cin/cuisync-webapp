"""Import a preserved prompts.json without overwriting concurrent admin edits.

Dry-run by default. --activate imports only into a default configuration unless
an explicit --expected NAME=VERSION authorizes replacing that active version.
The original JSON is never written or deleted.
"""
import argparse
import json
from pathlib import Path

import accounts  # Loads server-side .env; never prints credentials.
from prompt_store import NAMES, PromptStore, defaults


def migrate(store, values, administrator, activate=False, expected=None):
    expected = expected or {}
    store.refresh(strict=True)
    result = []
    for name in NAMES:
        text = values.get(name)
        if not isinstance(text, str) or not text.strip():
            continue
        current = store.active(name, refresh=False)
        if text.strip() == current["instructions"]:
            result.append((name, "already imported"))
            continue
        safe_default = current["version"] == 0 or (current["administrator"] == "system default" and current["instructions"] == defaults()[name])
        allowed = expected.get(name) == current["version"] or safe_default
        if activate and allowed:
            row = store.activate(name, text, administrator, current["version"])
            result.append((name, f"imported as version {row['version']}"))
        else:
            result.append((name, f"{'ready to import' if allowed else 'needs explicit expected version'}; active version {current['version']}"))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Preserved legacy prompts.json (server only)")
    parser.add_argument("--administrator", required=True, help="Administrator ID or deployment operator identity for the audit")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--expected", action="append", default=[], metavar="NAME=VERSION")
    args = parser.parse_args()
    values = json.loads(args.source.read_text(encoding="utf-8"))
    if not isinstance(values, dict):
        parser.error("The source must be a JSON object.")
    expected = {}
    for pair in args.expected:
        try:
            name, version = pair.split("=", 1)
            if name not in NAMES:
                raise ValueError()
            expected[name] = int(version)
        except ValueError:
            parser.error("Expected NAME=VERSION using a known prompt name and numeric version.")
    store = PromptStore(Path(__file__).resolve().parent / "admin_data", initialize=False)
    for name, message in migrate(store, values, args.administrator, args.activate, expected):
        print(f"{name}: {message}")
    print("Original source file preserved. No dish records or explanation caches were modified.")


if __name__ == "__main__":
    main()
