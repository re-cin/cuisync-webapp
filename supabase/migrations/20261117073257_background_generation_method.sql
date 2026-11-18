ALTER TABLE public.dish_backgrounds
  ADD COLUMN generation_method TEXT CHECK (generation_method IN ('source_based', 'gpt_fallback')),
  ADD COLUMN generation_fields JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(generation_fields) = 'object'),
  ADD COLUMN retrieval_note TEXT NOT NULL DEFAULT '';
UPDATE public.dish_backgrounds SET generation_method = 'source_based',
  generation_fields = jsonb_strip_nulls(jsonb_build_object(
    'historical_background', CASE WHEN historical_background <> '' THEN 'source_based' END,
    'cultural_background', CASE WHEN cultural_background <> '' THEN 'source_based' END))
WHERE jsonb_array_length(sources) > 0 AND (historical_background <> '' OR cultural_background <> '');
COMMENT ON COLUMN public.dish_backgrounds.generation_method IS
  'gpt_fallback if any displayed field uses general knowledge; generation_fields preserves per-field provenance. No approval required.';
