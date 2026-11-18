-- Backend-only prompt instructions and immutable audit history.
CREATE TABLE public.prompt_versions (
    name text NOT NULL CHECK (name IN ('explanation', 'background_source', 'background_fallback')),
    version integer NOT NULL CHECK (version > 0),
    category text GENERATED ALWAYS AS (CASE WHEN name = 'explanation' THEN 'similarity' ELSE 'historical_cultural' END) STORED,
    generation_mode text GENERATED ALWAYS AS (CASE name WHEN 'explanation' THEN 'explanation' WHEN 'background_source' THEN 'source_based' ELSE 'general_knowledge_fallback' END) STORED,
    instructions text NOT NULL CHECK (length(btrim(instructions)) > 0),
    administrator text NOT NULL,
    modified_at timestamptz NOT NULL DEFAULT now(),
    active boolean NOT NULL DEFAULT false,
    PRIMARY KEY (name, version)
);
CREATE UNIQUE INDEX prompt_versions_one_active ON public.prompt_versions (name) WHERE active;
ALTER TABLE public.prompt_versions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.prompt_versions FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.prompt_versions TO service_role;

-- Transaction-scoped per-type lock covers initialization as well as updates.
-- The expected active version prevents lost edits and stale restores.
CREATE FUNCTION public.activate_prompt_version(
    p_name text, p_instructions text, p_administrator text,
    p_expected integer, p_initialize boolean DEFAULT false
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE current_row public.prompt_versions; next_row public.prompt_versions;
BEGIN
    IF p_name NOT IN ('explanation', 'background_source', 'background_fallback') THEN
        RAISE EXCEPTION 'Unknown prompt type' USING ERRCODE = '22023';
    END IF;
    IF p_instructions IS NULL OR length(btrim(p_instructions)) = 0
       OR p_administrator IS NULL OR length(btrim(p_administrator)) = 0 THEN
        RAISE EXCEPTION 'Prompt instructions and administrator are required' USING ERRCODE = '22023';
    END IF;
    IF NOT p_initialize AND length(btrim(p_instructions)) NOT BETWEEN 20 AND 20000 THEN
        RAISE EXCEPTION 'Invalid prompt length' USING ERRCODE = '22023';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(147201, pg_catalog.hashtext(p_name));
    SELECT * INTO current_row FROM public.prompt_versions WHERE name = p_name AND active;
    IF p_initialize AND current_row.version IS NOT NULL THEN
        RETURN to_jsonb(current_row); -- An import never replaces an active version.
    END IF;
    IF coalesce(current_row.version, 0) IS DISTINCT FROM p_expected THEN
        RAISE EXCEPTION 'Another administrator changed this prompt; reload before saving' USING ERRCODE = 'PT409';
    END IF;
    UPDATE public.prompt_versions SET active = false WHERE name = p_name AND active;
    INSERT INTO public.prompt_versions (name, version, instructions, administrator, active)
    VALUES (p_name, coalesce(current_row.version, 0) + 1, p_instructions, p_administrator, true)
    RETURNING * INTO next_row;
    RETURN to_jsonb(next_row);
END;
$$;
REVOKE ALL ON FUNCTION public.activate_prompt_version(text, text, text, integer, boolean) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.activate_prompt_version(text, text, text, integer, boolean) TO service_role;
COMMENT ON TABLE public.prompt_versions IS 'Backend-only writing instructions. Protected system rules remain in application code. Historical versions are retained when a new version is activated.';
