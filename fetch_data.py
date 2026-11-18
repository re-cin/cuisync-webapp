
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
FEATURES = ("ingredients", "actions", "cookware", "utensils")
REQUIRED = ["recipes.json"] + [
    f"{feature}_{suffix}" for feature in FEATURES for suffix in ("item_vecs.npy", "items.json")
]
OPTIONAL = ["embeddings_order.json"]  # app.py only uses it as a safety check


def main():
    missing = [name for name in REQUIRED + OPTIONAL if not os.path.exists(os.path.join(ROOT, name))]
    if not missing:
        print("[fetch_data] All data files are already here.")
        return

    repo = os.environ.get("HF_DATA_REPO", "").strip()
    if not repo:
        sys.exit("[fetch_data] Missing data files and HF_DATA_REPO is not set: " + ", ".join(missing))

    from huggingface_hub import hf_hub_download

    token = os.environ.get("HF_TOKEN") or None
    for name in missing:
        try:
            hf_hub_download(repo_id=repo, repo_type="dataset", filename=name, local_dir=ROOT, token=token)
            print(f"[fetch_data] Downloaded {name}")
        except Exception as error:
            if name in OPTIONAL:
                print(f"[fetch_data] Skipped optional {name}: {error}")
            else:
                sys.exit(f"[fetch_data] Could not download {name} from {repo}: {error}")


if __name__ == "__main__":
    main()
