-- =============================================================================
-- CuiSync: vectors for NEW items (ingredients, actions, cookware, utensils)
-- Run once in Supabase: SQL Editor -> New query -> paste all of this -> Run.
-- Safe to run again.
--
-- The original vectors stay in the *_item_vecs.npy files from the vectorization
-- notebook. This table only holds items the admin added later, vectorized by the
-- app (item_vectors.py) with the same model, so they survive Render redeploys.
-- When the notebook is run again, these items are in the new .npy files and
-- the rows here are simply not needed any more (they can stay or be deleted).
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.item_vectors (
    feature    TEXT NOT NULL CHECK (feature IN ('ingredients', 'actions', 'cookware', 'utensils')),
    item       TEXT NOT NULL,                              -- lower-case, as in the dish lists
    model      TEXT NOT NULL DEFAULT 'all-MiniLM-L6-v2',   -- model that made the vector
    vector     REAL[] NOT NULL,                            -- 384 numbers, length 1 (normalized)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (feature, item)
);

-- Security: same as recipes (only the server's service key)
ALTER TABLE public.item_vectors ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.item_vectors FROM anon, authenticated;
GRANT ALL ON public.item_vectors TO service_role;

NOTIFY pgrst, 'reload schema';
