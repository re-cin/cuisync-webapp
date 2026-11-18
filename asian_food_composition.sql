-- Optional Asian food composition source table, isolated from existing USDA records.
-- Run this once in Supabase SQL Editor, or keep it as a deployment migration.
-- No public API policies: server-side service role only. Do not expose that key.
CREATE TABLE IF NOT EXISTS public.regional_ingredient_nutrition (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    country text NOT NULL,
    normalized_name text NOT NULL,
    ingredient_name text NOT NULL,
    food_name text NOT NULL,
    food_id text NOT NULL,
    source_code text NOT NULL,
    data_source text NOT NULL,
    source_url text NOT NULL,
    nutrients jsonb NOT NULL,
    approved boolean NOT NULL DEFAULT false,
    data_version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(country, normalized_name),
    CONSTRAINT regional_nutrition_country_chk CHECK (country IN
      ('PHILIPPINES','THAILAND','JAPAN','SOUTH KOREA','INDIA')),
    CONSTRAINT regional_nutrition_source_chk CHECK (source_code IN
      ('PHILFCT','THAI_FCD','MEXT_JP','RDA_KR','ICMR_IFCT'))
);
ALTER TABLE public.regional_ingredient_nutrition ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.regional_ingredient_nutrition FROM anon, authenticated;
CREATE INDEX IF NOT EXISTS idx_regional_nutrition_approved_country
    ON public.regional_ingredient_nutrition(country, normalized_name)
    WHERE approved = true;
