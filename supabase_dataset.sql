-- =============================================================================
-- CuiSync dataset in Supabase (the dishes from recipes.json)
-- Run once in Supabase: SQL Editor -> New query -> paste all of this -> Run.
-- Safe to run again. If you ran the earlier version (one "data" JSON column),
-- this moves those dishes into the separate columns.
--
-- After running it, start the app once: if the recipes table is empty, the app
-- uploads recipes.json (or admin_data/recipes.json if you already edited dishes).
-- The country list and GPT prompts stay in admin_data/ for now.
-- The .npy vector files stay as files; the app rebuilds what it needs from them.
-- =============================================================================

-- 1. Dishes: one row per dish, one column per field ------------------------------
CREATE TABLE IF NOT EXISTS public.recipes (
    recipe_id  BIGINT PRIMARY KEY,
    position   INTEGER NOT NULL DEFAULT 0      -- order the dishes are shown and matched in
);

ALTER TABLE public.recipes
    ADD COLUMN IF NOT EXISTS title             TEXT  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS alternative_title TEXT  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS country           TEXT  NOT NULL DEFAULT '',   -- e.g. PHILIPPINES
    ADD COLUMN IF NOT EXISTS region            TEXT,                        -- province / area, often empty
    ADD COLUMN IF NOT EXISTS country_region    TEXT  NOT NULL DEFAULT '',   -- e.g. Southeast Asia
    ADD COLUMN IF NOT EXISTS recipe_link       TEXT  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS image_link        TEXT  NOT NULL DEFAULT '',
    -- comma-separated lists used for matching
    ADD COLUMN IF NOT EXISTS ingredient_text   TEXT  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS action_text       TEXT  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS cookware_text     TEXT  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS utensil_text      TEXT  NOT NULL DEFAULT '',
    -- the same lists as the NER pipeline produced them
    ADD COLUMN IF NOT EXISTS ingredient_canon  TEXT  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS action_canon      TEXT  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS cookware_canon    TEXT  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS utensil_canon     TEXT  NOT NULL DEFAULT '',
    -- added on the admin page
    ADD COLUMN IF NOT EXISTS tags              TEXT  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS culture_notes     TEXT  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS history_notes     TEXT  NOT NULL DEFAULT '',
    -- any other field a dish has (for example from an uploaded file)
    ADD COLUMN IF NOT EXISTS extra             JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS updated_at        TIMESTAMPTZ NOT NULL DEFAULT now();

-- 2. Upgrade from the earlier version: copy the "data" JSON into the columns, then drop it
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'recipes' AND column_name = 'data') THEN
        UPDATE public.recipes SET
            title             = COALESCE(data ->> 'title', ''),
            alternative_title = COALESCE(data ->> 'alternative_title', ''),
            country           = COALESCE(data ->> 'country', ''),
            region            = data ->> 'region',
            country_region    = COALESCE(data ->> 'country_region', ''),
            recipe_link       = COALESCE(data ->> 'recipe_link', ''),
            image_link        = COALESCE(data ->> 'image_link', ''),
            ingredient_text   = COALESCE(data ->> 'ingredient_text', ''),
            action_text       = COALESCE(data ->> 'action_text', ''),
            cookware_text     = COALESCE(data ->> 'cookware_text', ''),
            utensil_text      = COALESCE(data ->> 'utensil_text', ''),
            ingredient_canon  = COALESCE(data ->> 'ingredient_canon', ''),
            action_canon      = COALESCE(data ->> 'action_canon', ''),
            cookware_canon    = COALESCE(data ->> 'cookware_canon', ''),
            utensil_canon     = COALESCE(data ->> 'utensil_canon', ''),
            tags              = COALESCE(data ->> 'tags', ''),
            culture_notes     = COALESCE(data ->> 'culture_notes', ''),
            history_notes     = COALESCE(data ->> 'history_notes', ''),
            extra = data - ARRAY['title','alternative_title','country','region','country_region',
                                 'recipe_link','image_link','ingredient_text','action_text',
                                 'cookware_text','utensil_text','ingredient_canon','action_canon',
                                 'cookware_canon','utensil_canon','tags','culture_notes',
                                 'history_notes','recipe_id'];
        ALTER TABLE public.recipes DROP COLUMN data;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS recipes_position_idx ON public.recipes (position, recipe_id);
CREATE INDEX IF NOT EXISTS recipes_country_idx  ON public.recipes (country);
CREATE INDEX IF NOT EXISTS recipes_title_idx    ON public.recipes (lower(title));

-- 3. Keep updated_at current
CREATE OR REPLACE FUNCTION public.cuisync_touch_updated_at() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS recipes_touch ON public.recipes;
CREATE TRIGGER recipes_touch BEFORE UPDATE ON public.recipes
    FOR EACH ROW EXECUTE FUNCTION public.cuisync_touch_updated_at();

-- 4. Security: Row Level Security on, no policies. Only the server (service key) can
--    read or change this table; the public anon key gets nothing.
ALTER TABLE public.recipes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.recipes FROM anon, authenticated;
GRANT ALL ON public.recipes TO service_role;

-- Tell the Supabase API about the changed table right away
NOTIFY pgrst, 'reload schema';
