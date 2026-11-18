-- =============================================================================
-- CuiSync dataset in Supabase (the dishes from recipes.json)
-- Run once in Supabase: SQL Editor -> New query -> paste all of this -> Run.
-- Safe to run again. If you ran the earlier version (one "data" JSON column),
-- this moves those dishes into the separate columns.
--
-- After running it, start the app once: if the recipes table is empty, the app
-- uploads recipes.json (or admin_data/recipes.json if you already edited dishes).
-- The country list and its regions are in Supabase too (part 5 below).
-- The GPT prompts stay in admin_data/ for now.
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
    ADD COLUMN IF NOT EXISTS updated_at        TIMESTAMPTZ NOT NULL DEFAULT now();

-- Columns from an earlier version that the app no longer uses
ALTER TABLE public.recipes
    DROP COLUMN IF EXISTS tags,
    DROP COLUMN IF EXISTS culture_notes,
    DROP COLUMN IF EXISTS history_notes,
    DROP COLUMN IF EXISTS extra;

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
            utensil_canon     = COALESCE(data ->> 'utensil_canon', '');
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


-- =============================================================================
-- 5. Regions and countries (admin Countries page)
-- Replaces admin_data/countries.json. The admin can add, rename, reorder and
-- delete regions; every country points to one region.
-- =============================================================================

-- Regions, in the order they are shown in the dropdowns and on the Analysis page
CREATE TABLE IF NOT EXISTS public.regions (
    name       TEXT PRIMARY KEY,                 -- e.g. Southeast Asia
    sort_order INTEGER NOT NULL DEFAULT 100,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO public.regions (name, sort_order) VALUES
    ('East Asia', 1), ('Southeast Asia', 2), ('South Asia', 3),
    ('Central Asia', 4), ('West Asia', 5), ('Other Asia', 99)
ON CONFLICT (name) DO NOTHING;

-- Any other region the dishes already use (so no dish loses its region)
INSERT INTO public.regions (name, sort_order)
SELECT DISTINCT country_region, 50 FROM public.recipes WHERE country_region <> ''
ON CONFLICT (name) DO NOTHING;

-- Countries: name = the key the dishes use (e.g. TURKIYE), label = what users see (Turkey)
CREATE TABLE IF NOT EXISTS public.countries (
    name       TEXT PRIMARY KEY CHECK (name = upper(name)),
    label      TEXT NOT NULL,
    region     TEXT NOT NULL DEFAULT 'Other Asia',
    visible    BOOLEAN NOT NULL DEFAULT true,    -- "Show" in the dropdowns
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS countries_label_idx ON public.countries (lower(label));

-- A country's region must be a row in regions (renaming a region updates its countries).
-- Also replaces the fixed region list of an earlier version of this script.
ALTER TABLE public.countries DROP CONSTRAINT IF EXISTS countries_region_check;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'countries_region_fkey') THEN
        ALTER TABLE public.countries ADD CONSTRAINT countries_region_fkey
            FOREIGN KEY (region) REFERENCES public.regions (name) ON UPDATE CASCADE;
    END IF;
END $$;

-- First run: one country per country in the dishes, with the region most of its dishes use
WITH per_country AS (
    SELECT country, mode() WITHIN GROUP (ORDER BY NULLIF(country_region, '')) AS region
    FROM public.recipes
    WHERE country <> ''
    GROUP BY country
)
INSERT INTO public.countries (name, label, region)
SELECT p.country,
       CASE p.country WHEN 'TURKIYE' THEN 'Turkey' ELSE initcap(p.country) END,
       COALESCE(g.name, 'Other Asia')
FROM per_country p
LEFT JOIN public.regions g ON g.name = p.region
ON CONFLICT (name) DO NOTHING;

-- Every dish points to a row in countries (renaming a country key updates its dishes).
-- The app always saves a new country before the dishes that use it.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'recipes_country_fkey') THEN
        ALTER TABLE public.recipes ADD CONSTRAINT recipes_country_fkey
            FOREIGN KEY (country) REFERENCES public.countries (name) ON UPDATE CASCADE;
    END IF;
END $$;

-- Keep updated_at current
DROP TRIGGER IF EXISTS regions_touch ON public.regions;
CREATE TRIGGER regions_touch BEFORE UPDATE ON public.regions
    FOR EACH ROW EXECUTE FUNCTION public.cuisync_touch_updated_at();
DROP TRIGGER IF EXISTS countries_touch ON public.countries;
CREATE TRIGGER countries_touch BEFORE UPDATE ON public.countries
    FOR EACH ROW EXECUTE FUNCTION public.cuisync_touch_updated_at();

-- Security: same as recipes (only the server's service key)
ALTER TABLE public.regions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.countries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.regions, public.countries FROM anon, authenticated;
GRANT ALL ON public.regions, public.countries TO service_role;

NOTIFY pgrst, 'reload schema';
