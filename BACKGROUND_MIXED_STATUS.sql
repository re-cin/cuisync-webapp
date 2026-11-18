-- CuiSync: distinguish fully source-backed, partially source-backed and GPT fallback.
-- Safe for existing text: no historical/cultural fields or citations are modified.
ALTER TABLE public.dish_backgrounds
  DROP CONSTRAINT IF EXISTS dish_backgrounds_generation_method_check;
ALTER TABLE public.dish_backgrounds
  ADD CONSTRAINT dish_backgrounds_generation_method_check
  CHECK (generation_method IN ('source_based', 'mixed', 'gpt_fallback'));

-- Reclassify only rows whose independently tracked field methods are mixed.
-- Leave updated_at unchanged: this is metadata normalization, not new content.
UPDATE public.dish_backgrounds
SET generation_method = 'mixed'
WHERE generation_method IS DISTINCT FROM 'mixed'
  AND (
    (generation_fields->>'historical_background' = 'source_based'
     AND generation_fields->>'cultural_background' = 'gpt_fallback')
    OR
    (generation_fields->>'historical_background' = 'gpt_fallback'
     AND generation_fields->>'cultural_background' = 'source_based')
  );

COMMENT ON COLUMN public.dish_backgrounds.generation_method IS
  'source_based: both generated fields cite evidence; mixed: one source-based and one GPT fallback; gpt_fallback: both fallback. generation_fields remains authoritative. None implies independent verification.';
