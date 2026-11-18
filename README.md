# Cuisync Web App

### *Note when updating on Github:*
*Only update/upload files through your respective branches. Click on the main drop down to select other branches (you will not be able to update it on the main branch).*
*To create pull request (merge your work with main branch), click **contribute** when an update has been made and then **create pull request** to notify the team for a new update.*


## Setup & Installation

Follow these steps to set up the environment and run the app locally.

### 1. Clone or Download the Repository
* Click the green **Code** button at the top of this repository page and select **Download ZIP**.
* Extract the `.zip` file to your computer.

### 2. Set Up a Virtual Environment (`venv`)
It is recommended to run this project inside a Python virtual environment to keep dependencies isolated. Type this into your terminal.

**On Windows:**
```bash
# Create the virtual environment
python -m venv venv

# Activate the virtual environment
venv\Scripts\activate
```

**On Mac/Linux:**
```bash
# Create the virtual environment
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate
```

### 3. Install Required Dependencies
With your virtual environment active, run:
```bash
pip install -r requirements.txt
```

### 4. Download Required Dataset Files
Download from this Drive folder: 
[Google Drive](https://drive.google.com/drive/folders/1OePoC482z8081cXCIbWCHIp_b-FbBUsh?usp=sharing)
and place all files in the main project folder (outside other folders) alongside **app.py.**

**This version needs these files next to `app.py`:** `softmatch.py`, `recipes.json`, and for each of `ingredients`, `actions`, `cookware`, `utensils`: `<name>_item_vecs.npy` and `<name>_items.json` (9 data files in total with `recipes.json`).


### 5. Set your OpenAI API key (for the AI explanation)
The similarity explanation is generated with GPT-4o and grounded in the four feature-type Shapley contributions. The easiest way is a `.env` file: create a file named exactly `.env` in the main project folder (next to **app.py**) containing one line:
```plaintext
OPENAI_API_KEY=your-key-here
OPENAI_MODEL=gpt-4o
```
Or set it in the terminal you will run the app from (it only lasts for that terminal window):

**Windows (Command Prompt):** `set OPENAI_API_KEY=your-key-here`
**Windows (PowerShell):** `$env:OPENAI_API_KEY="your-key-here"`
**Mac/Linux:** `export OPENAI_API_KEY=your-key-here`

Never write the key in the code or upload it to GitHub (`.env` is in `.gitignore`).
When the app starts, the terminal prints `GPT-4o explanations: ENABLED` or `DISABLED` with the reason. If it is disabled, the page shows a template explanation (badge: SUMMARY) instead of the AI one.

The project folder must also contain `explainer.py`, `shapley.py`, `cuisync_prompts.py` and `cuisync_cache.py` next to **app.py**. The Shapley calculation is implemented directly in `shapley.py`, so the `shap` Python package is not required. Explanations are cached in `explanations_cache.db` (created automatically, do not upload it).

### 6. Run program locally (for development only)
Only run it through **app.py** and open it on your browser with this url:
```plaintext
http://127.0.0.1:8000
```


### 7. Share the app online (Cloudflare Tunnel)
Use this to give teammates, panelists or your phone a public **https** link to the app running on your computer. Nothing is uploaded to a server: the link only works while your computer and the terminal window stay on.

**Install `cloudflared` once:**

**Mac:** `brew install cloudflared`
**Windows:** `winget install --id Cloudflare.cloudflared` (then open a new terminal)

**Run it** (with the virtual environment active, instead of `python app.py`):
```bash
python run_public.py
```
It starts the app, opens the tunnel and prints a link like `https://some-random-words.trycloudflare.com`. Send that link to anyone. Press **Ctrl+C** to stop both. A new random link is made every time you run it.

**Notes**
* Anyone with the link can use the app, and every comparison they run can call GPT-4o on **your** OpenAI key. Share the link only with people you trust and stop it when you're done.
* Uploads are limited to 16 MB per photo.
* Quick tunnels are meant for testing and demos, not as a permanent website.

**Optional: a fixed link on your own domain.** If you have a domain on Cloudflare, create a named tunnel once:
```bash
cloudflared tunnel login
cloudflared tunnel create cuisync
cloudflared tunnel route dns cuisync cuisync.yourdomain.com
```
Then run `python run_public.py --tunnel cuisync --url https://cuisync.yourdomain.com` and the app is always at that address while it runs.


### GPT + Shapley explanation flow

The application computes the recipe-level similarity first using `softmatch.py`. It then computes exact Shapley values over the four feature-type contributions in `shapley.py`. The resulting contribution shares, together with cleaned shared ingredients and cooking actions, are sent to the GPT explanation module in `explainer.py`.

The GPT prompt uses few-shot examples from `cuisync_prompts.py` and includes a grounding check so the generated paragraph is rejected if it introduces unsupported culinary entities. If the API is unavailable or the grounding check fails, the app uses a grounded template explanation instead.

The GPT explanation does not calculate the similarity itself. GPT explains the recommendation produced by CuiSync and uses the Shapley contributions as evidence for which feature type contributed most.


### Panel revisions (redefense, Nov 2026)

New files: `accounts.py` (users, profiles, search history, stored in Supabase), `dataset_store.py` (admin-editable dishes, countries and GPT prompts), `search_tools.py` (keyword, ingredient and tag search, possible cuisines), `cultural_info.py` (culture, history, health notes, allergens), plus the templates `explore.html`, `profile.html`, `auth.html` and `static/css/panel.css`. The admin templates are in `templates/admin/`.

* **Accounts (Supabase)**: logins, roles, profiles and search history are stored in Supabase.
  1. In Supabase, open **SQL Editor**, paste `supabase_setup.sql` and click **Run** (once; safe to run again). It adds the profile columns (username, email, cooking role, home country, favourite cuisines, allergens, bio), the `search_history` table and the dashboard functions to your `profiles` setup.
  2. Copy `.env.example` to `.env` and fill in `SUPABASE_URL`, `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY` (Supabase: **Project Settings → API**). The service key is secret: keep it in `.env` / the Render dashboard only.
  3. `pip install -r requirements.txt` (adds `requests`), then `python app.py`.
  Users sign up with email, username and password and log in with either their email or their username. Without the Supabase settings the site still runs; only log in and accounts are off.
* **Dishes in Supabase**: the dishes (`recipes.json`) that admins add, edit or delete are saved in Supabase, so edits stay after a restart or redeploy (on Render the disk is wiped each time). The country list and GPT prompts still stay in `admin_data/`.
  1. In Supabase, open **SQL Editor**, paste `supabase_dataset.sql` and click **Run** (once; safe to run again). It makes the `recipes` table.
  2. Start the app once. If the `recipes` table is empty, the app uploads `recipes.json` (or `admin_data/recipes.json` if dishes were already edited) into it by itself.
  The `.npy` and `*_items.json` vector files stay as files (from the Google Drive folder, or downloaded by `fetch_data.py` on Render); the app rebuilds what it needs from them and the dishes in Supabase. Without the tables the app keeps saving admin edits in `admin_data/` like before.
* **Admin page**: admins log in on their own page, **`/admin/login`** (normal users use `/login`). Admins only see the admin pages; normal users only see the user site. When Supabase has no admin yet, the app creates one on start: username `admin`, email `admin@cuisync.app`, password `cuisync-admin` (change them with `CUISYNC_ADMIN_USER`, `CUISYNC_ADMIN_EMAIL` and `CUISYNC_ADMIN_PASSWORD` in `.env` before the first run). Change the password at `/admin/account`. You can also make any account an admin in Supabase: **Table Editor → profiles → role = admin**.
* **Ingredient NER** (Add / Edit Dish in the admin): put the BERT model files (`config.json`, `model.safetensors`, `tokenizer.json`, `tokenizer_config.json` from `ingredient_BERT_FINAL_MODEL.zip`) in a folder `ner_model/` next to `app.py` and run `pip install "transformers>=5" torch`, or run it on a Hugging Face Space (`ner_space/`) and set `NER_API_URL` and `NER_API_TOKEN` in `.env`. See `ner.py`.
* Admin changes are saved in `admin_data/` (not uploaded to GitHub). The original `recipes.json` and `.npy` files are never overwritten. **Reset to original dataset** on the Dataset tab undoes dataset edits.
* New dishes reuse the vectors of known ingredients/actions; brand-new items borrow the closest known vector. The Overview tab lists items with no match (run the vectorizer again for those).
* Set `SECRET_KEY` in `.env` when the app runs on a server, so logins survive restarts.
