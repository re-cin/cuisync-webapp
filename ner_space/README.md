---
title: CuiSync NER
sdk: docker
app_port: 7860
pinned: false
---

# CuiSync ingredient NER service

Also makes vectors for new dish items (`POST /embed`), so items an admin adds count in
similarity right away instead of waiting for the vectorization notebook to run again.

Extracts ingredients from recipe text with the team's fine-tuned BERT model
(`BertForTokenClassification`, labels `O`, `B-INGREDIENT`, `I-INGREDIENT`).
The CuiSync Admin page sends recipe text here when an admin adds or edits a dish.

## Files in this Space

```
Dockerfile
README.md
requirements.txt
server.py
model/
    config.json
    model.safetensors
    tokenizer.json
    tokenizer_config.json
```

Copy the files from `ingredient_BERT_FINAL_MODEL.zip` into `model/`.
The embedding model (`all-MiniLM-L6-v2`, the same one the vectorization notebook uses) is
public: the Dockerfile downloads it into `embed_model/` while the Space builds, so you do
**not** upload anything for it.
`training_args.bin` is not needed. `model.safetensors` is about 435 MB, so upload it
through the Hugging Face website ("Add file" > "Upload files") or with Git LFS.

## Settings

Set a secret named `NER_API_TOKEN` in the Space settings. The web app must send the same value.

## Test

```bash
curl -X POST https://<your-space>.hf.space/extract \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"text": "2 cups coconut milk\n1 stalk lemongrass, chopped\n500 g chicken thighs"}'
```

Vectors for new items (same model and settings as the vectorization notebook):

```bash
curl -X POST https://<your-space>.hf.space/embed \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"items": ["gochugaru", "air fryer"]}'
```
