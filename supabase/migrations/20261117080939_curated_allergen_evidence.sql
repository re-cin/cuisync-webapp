ALTER TABLE public.ingredient_allergens
  ADD COLUMN IF NOT EXISTS framework text NOT NULL DEFAULT 'legacy_FSA_14_FDA_9',
  ADD COLUMN IF NOT EXISTS evidence jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(evidence) = 'array'),
  ADD COLUMN IF NOT EXISTS assessment_note text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS rule_version integer NOT NULL DEFAULT 0 CHECK (rule_version >= 0);
COMMENT ON COLUMN public.ingredient_allergens.evidence IS
  'Source URLs and their evidence role; framework-only sources do not establish an ingredient identity.';
COMMENT ON COLUMN public.ingredient_allergens.assessment_note IS
  'Scope, uncertainty and formulation limitations; assessed_none is not a safety guarantee.';
-- Existing rules, RLS, grants and aliases are preserved. No public write access.
