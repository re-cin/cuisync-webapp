-- =============================================================================
-- CuiSync accounts in Supabase
-- Run once in Supabase: SQL Editor -> New query -> paste all of this -> Run.
-- Safe to run again. It builds on your existing public.profiles table and its
-- on_auth_user_created trigger; it does not delete any data.
-- =============================================================================

-- 1. Extra profile columns used by the app -----------------------------------
ALTER TABLE public.profiles
    ADD COLUMN IF NOT EXISTS username          TEXT,
    ADD COLUMN IF NOT EXISTS email             TEXT,
    ADD COLUMN IF NOT EXISTS cooking_role      TEXT   NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS home_country      TEXT   NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS favorite_cuisines TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS avoid_allergens   TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS bio               TEXT   NOT NULL DEFAULT '';

-- Usernames are stored in lower case and must be unique (login accepts email or username)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_username_format') THEN
        ALTER TABLE public.profiles ADD CONSTRAINT profiles_username_format
            CHECK (username IS NULL OR username ~ '^[a-z0-9._]{3,30}$');
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS profiles_username_key ON public.profiles (username);
CREATE INDEX IF NOT EXISTS profiles_email_idx ON public.profiles (lower(email));
CREATE INDEX IF NOT EXISTS profiles_role_idx  ON public.profiles (role);

-- Fill in the email of profiles that already exist
UPDATE public.profiles p
   SET email = u.email
  FROM auth.users u
 WHERE u.id = p.id AND p.email IS NULL;

-- 2. New sign-ups: also copy the username and email into the profile ----------
CREATE OR REPLACE FUNCTION public.handle_new_user() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
    INSERT INTO public.profiles (id, name, username, email)
    VALUES (
        NEW.id,
        COALESCE(NEW.raw_user_meta_data ->> 'name', ''),
        NULLIF(lower(NEW.raw_user_meta_data ->> 'username'), ''),
        NEW.email
    )
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;
-- (the on_auth_user_created trigger you already made keeps calling this function)

-- Keep profiles.email up to date if a user's email changes in Supabase Auth
CREATE OR REPLACE FUNCTION public.handle_user_email_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
    UPDATE public.profiles SET email = NEW.email WHERE id = NEW.id;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS on_auth_user_email_changed ON auth.users;
CREATE TRIGGER on_auth_user_email_changed
    AFTER UPDATE OF email ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_user_email_change();

-- 3. Search history -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.search_history (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         UUID        NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    search_type     TEXT        NOT NULL,          -- text, image, keyword, ingredients, tags
    query           TEXT        NOT NULL DEFAULT '',
    dish_name       TEXT        NOT NULL DEFAULT '',
    origin_country  TEXT        NOT NULL DEFAULT '',
    target_country  TEXT        NOT NULL DEFAULT '',
    recipe_id       TEXT        NOT NULL DEFAULT '',
    result_title    TEXT        NOT NULL DEFAULT '',
    result_country  TEXT        NOT NULL DEFAULT '',
    score           DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS search_history_user_idx    ON public.search_history (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS search_history_created_idx ON public.search_history (created_at DESC);

-- 3b. Recent searches (UI overhaul) -------------------------------------------
-- Every dish-vs-dish comparison a logged-in user opened (home page "Recent searches"
-- row and the All recent searches page). data = a small JSON entry made by app.py.
CREATE TABLE IF NOT EXISTS public.recent_comparisons (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     UUID        NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    pair_key    TEXT        NOT NULL,
    data        JSONB       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS recent_comparisons_user_idx ON public.recent_comparisons (user_id, id DESC);
ALTER TABLE public.recent_comparisons ENABLE ROW LEVEL SECURITY;

-- 4. Security: only the server (service key) reads and writes these tables ----
-- Row Level Security is on with no policies for anon users, so the public
-- anon / publishable key can't read or change anyone's profile or role.
ALTER TABLE public.profiles       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.search_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can read their own profile" ON public.profiles;
CREATE POLICY "Users can read their own profile" ON public.profiles
    FOR SELECT TO authenticated USING (auth.uid() = id);

-- 5. Numbers for the admin dashboard -------------------------------------------
CREATE OR REPLACE FUNCTION public.cuisync_search_counts()
RETURNS TABLE (user_id UUID, n BIGINT)
LANGUAGE sql STABLE SET search_path = '' AS $$
    SELECT h.user_id, count(*) FROM public.search_history h GROUP BY h.user_id;
$$;

CREATE OR REPLACE FUNCTION public.cuisync_top_dishes(top_n INT DEFAULT 5)
RETURNS TABLE (dish_name TEXT, origin_country TEXT, n BIGINT)
LANGUAGE sql STABLE SET search_path = '' AS $$
    SELECT h.dish_name, h.origin_country, count(*) AS n
      FROM public.search_history h
     WHERE h.dish_name <> ''
     GROUP BY h.dish_name, h.origin_country
     ORDER BY n DESC
     LIMIT top_n;
$$;

CREATE OR REPLACE FUNCTION public.cuisync_searches_per_day(days INT DEFAULT 7, tz TEXT DEFAULT 'Asia/Singapore')
RETURNS TABLE (day DATE, n BIGINT)
LANGUAGE sql STABLE SET search_path = '' AS $$
    SELECT (h.created_at AT TIME ZONE tz)::date AS day, count(*)
      FROM public.search_history h
     WHERE (h.created_at AT TIME ZONE tz)::date > (now() AT TIME ZONE tz)::date - days
     GROUP BY 1;
$$;

-- Only the server may call them
REVOKE ALL ON FUNCTION public.cuisync_search_counts()            FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.cuisync_top_dishes(INT)            FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.cuisync_searches_per_day(INT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.cuisync_search_counts()            TO service_role;
GRANT EXECUTE ON FUNCTION public.cuisync_top_dishes(INT)            TO service_role;
GRANT EXECUTE ON FUNCTION public.cuisync_searches_per_day(INT, TEXT) TO service_role;

-- Tell the API about the new columns / table / functions right away
NOTIFY pgrst, 'reload schema';
