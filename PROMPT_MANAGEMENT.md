# Admin Prompt Management

The two categories contain three editors: Similarity explanation, History and
culture: with sources, and History and culture: without sources. Each editor
shows the version in use, Default/Custom status, rules that always apply,
editable writing instructions, a preview button, and past versions.

## Deployment and preservation of existing instructions

1. **Before redeploying an existing Render instance**, copy its
   `admin_data/prompts.json` to a secure backup. Render's ephemeral filesystem
   cannot recover a file already lost during an earlier redeployment. Do not
   commit this file or service credentials.
2. Apply `supabase/migrations/20261118131029_prompt_version_management.sql` to
   the intended Supabase project. This creates only `prompt_versions` and its
   activation function; it does not change dishes, scores, caches, or accounts.
   Remote application of this migration requires project deployment approval.
3. Keep `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` on the server. Browser
   requests go through Flask's existing admin authorization plus a session CSRF
   token. Neither public nor signed-in Supabase browser roles can access the
   table or activation function. The function uses SECURITY INVOKER.
4. On first startup, each absent prompt imports its matching legacy JSON value,
   or its code default. Initialization is serialized in PostgreSQL and cannot
   replace an already active configuration. The original JSON is preserved.
   The obsolete `cultural` key remains in that backup but is never activated.
5. If a cloud configuration already exists, a startup worker does **not**
   overwrite it with its local JSON. The page flags differing local legacy
   instructions. Review and import a preserved file explicitly:

   ```powershell
   python migrate_prompts.py --source C:\secure-backup\prompts.json --administrator YOUR_ADMIN_ID
   python migrate_prompts.py --source C:\secure-backup\prompts.json --administrator YOUR_ADMIN_ID --activate
   ```

   The first command previews imports without changing remote prompts. The
   second imports only missing or untouched system defaults. To replace an
   existing custom version, explicitly include its reviewed active version,
   e.g. `--expected explanation=3`. Every write still checks that version in a
   single database transaction, so a concurrent change wins over a stale import.
   Each prompt imports separately; the command reports completed imports and
   leaves the source file intact. Older versions remain in history.
6. Check the three editors after deployment, test previews, and save a version.
   Restart the app and verify the same active version loads from Supabase.

## Runtime behavior

- Supabase is authoritative whenever its URL and service key are configured.
  Each new generation reads the active instructions; independent workers see
  subsequent saves without a process restart. During a database outage, reads
  use a last-good local SQLite snapshot, then preserved legacy instructions or
  code defaults if no snapshot exists. Protected rules apply in every case.
  Failed reads back off for ten seconds. Writes never silently fall back to
  local activation when Supabase is configured.
- Without Supabase, local development uses SQLite transactions and version
  history in `admin_data/prompt_versions.sqlite3`. Remote snapshots use a
  separate `admin_data/prompt_remote_snapshot.sqlite3`. Preserve this directory
  if local configuration must survive filesystem replacement.
- An activation records name, generated category/mode, monotonically increasing
  version, instructions, administrator identity, UTC timestamp, and active flag.
  A partial unique index allows only one active version per prompt type.
  A transaction lock and expected-version comparison reject stale saves and
  restores with HTTP 409. Restore creates a new audit version.
- Similarity generation freezes the instructions for cache lookup, GPT retries,
  validation and cache write. Keys hash instructions, protected rules, examples,
  model and evidence. Restoring identical instructions reuses compatible
  entries; older entries under the pre-protection contract are not reused.
- Background metadata continues storing content fingerprints per generated
  field under `generation_fields._prompt_versions`. Version numbers are audit
  identifiers; content fingerprints identify compatible generation behavior.
  A saved, reset, or restored prompt makes descriptions generated with different
  instructions stale. The next dish request schedules regeneration of affected
  historical and cultural fields, including complete and previously reviewed
  descriptions. The old text stays visible until a valid replacement is saved;
  a failed draft leaves it intact. A successful replacement clears prior review
  approval so the new text can be reviewed. Source citations remain associated
  with source-based drafts. The source-upgrade workflow stays separate.

## Preview and legacy endpoint behavior

Similarity previews use the three existing reference sample pairs and their
fixed scores and relative Shapley influence percentages. These are reference
fixtures, not newly calculated rankings. The preview shows the full evidence,
score, contributions and GPT text. It uses the same vocabulary-grounding check
and retry logic without reading or writing the production explanation cache.
The selected pair's reference answer is never sent to GPT, and custom writing
instructions omit default-format examples. Explicit two-paragraph and word-count
requests are checked and retried once rather than displaying a mismatched draft.
The results page preserves paragraph breaks already stored in explanations.

Background previews select a dataset dish. Source-based previews reuse eligible
cached excerpts or retrieve references through the current evidence pipeline.
When a source is unavailable or cannot support a field, the missing field uses
the active general-knowledge instructions instead. The preview shows one
combined History & Culture block, matching the dish page; source excerpts and
internal checks are not shown.
The same JSON, length, link and date checks apply internally.
Fixed sentence counts live in the editable defaults, not in protected rules,
so an admin can change the length and structure of either generation mode.
The history/culture display also preserves paragraph breaks in generated text.
Previews do not activate instructions, persist dish records, or fall back to a
template when GPT is unavailable.

`/api/dish/<id>` was the remaining public caller of `cultural_info.dish_info`.
It now projects current history/culture from `dish_information.api_information`
while retaining old ingredient-only health/allergen fields and recipe admin
notes for compatible consumers. `/api/dish-info/<id>` and the dictionary's
`/api/dish/<id>/information` already use the current pipeline. The legacy
`dish_info` helper no longer calls GPT. Its old cache helpers remain available
for existing cache tooling and tests; startup no longer initializes that legacy
GPT pipeline. Nutrition, allergens and similarity calculations remain separate.

## Verification

```powershell
python -m unittest discover -s tests -p test_prompt_management.py
python -m unittest discover -s tests -p test_dish_backgrounds.py
```

Tests exercise all three modes, safe import, history, restore, CSRF/admin
authorization, stale writes, local concurrent workers, remote refresh/outage
behavior, request-consistent cache keys, protected messages, preview isolation,
grounding failures, date validation, prompt-change refresh, and preservation of
old text when regeneration fails.
