-- Refresh only allergen assessments from validated ingredient mappings.
-- Does NOT overwrite nutrition, history, or culture.
-- Can be re-run after mapping changes.
WITH ingredients AS (
  SELECT r.recipe_id,
         trim(t.item) AS ingredient,
         trim(regexp_replace(lower(trim(t.item)), '[^[:alnum:]_]+', ' ', 'g')) AS norm
  FROM public.recipes r
  CROSS JOIN LATERAL regexp_split_to_table(
      coalesce(nullif(r.ingredient_text,''), r.ingredient_canon, ''), '[,;\n]+'
  ) AS t(item)
  WHERE trim(t.item) <> ''
),
rules AS (
  SELECT normalized_name AS key, normalized_name, classification,
         allergens, source_url, 0 AS priority
  FROM public.ingredient_allergens
  WHERE approved AND data_version = 1
  UNION ALL
  SELECT a.alias AS key, r.normalized_name, r.classification,
         r.allergens, r.source_url, 1 AS priority
  FROM public.ingredient_allergens r
  CROSS JOIN LATERAL jsonb_array_elements_text(r.aliases) a(alias)
  WHERE r.approved AND r.data_version = 1
),
matched AS (
  SELECT DISTINCT ON (i.recipe_id, i.ingredient)
         i.recipe_id, i.ingredient, r.classification, r.allergens,
         r.source_url
  FROM ingredients i
  LEFT JOIN rules r ON r.key = i.norm
  ORDER BY i.recipe_id, i.ingredient,
           r.priority ASC NULLS LAST, r.normalized_name ASC
),
expanded AS (
  SELECT m.recipe_id, m.ingredient,
         el.item->>'name' AS name,
         el.item->>'classification' AS classification,
         coalesce(nullif(el.item->>'source_url',''),m.source_url,
                  'https://www.food.gov.uk/business-guidance/allergen-guidance-for-food-businesses') AS source_url
  FROM matched m
  CROSS JOIN LATERAL jsonb_array_elements(coalesce(m.allergens,'[]'::jsonb)) el(item)
  WHERE m.classification IN ('identified','possible')
    AND el.item->>'classification' IN ('identified','possible')
    AND nullif(el.item->>'name','') IS NOT NULL
  UNION ALL
  SELECT m.recipe_id,m.ingredient,'No identified major allergens','assessed_none',
         coalesce(m.source_url,'https://www.food.gov.uk/business-guidance/allergen-guidance-for-food-businesses')
  FROM matched m WHERE m.classification='assessed_none'
  UNION ALL
  SELECT m.recipe_id,m.ingredient,'Unassessed allergens','unknown',
         'https://www.food.gov.uk/business-guidance/allergen-guidance-for-food-businesses'
  FROM matched m WHERE m.classification IS NULL OR m.classification='unknown'
                     OR (m.classification IN ('identified','possible')
                         AND jsonb_array_length(coalesce(m.allergens,'[]'::jsonb))=0)
),
by_group AS (
  SELECT recipe_id,name,classification,
         min(source_url) AS source_url,
         jsonb_agg(DISTINCT ingredient) AS from_ingredients
  FROM expanded
  GROUP BY recipe_id,name,classification
),
payload AS (
  SELECT recipe_id,jsonb_agg(jsonb_build_object(
    'name',name, 'classification',classification,
    'source_url',source_url, 'from',from_ingredients
  ) ORDER BY classification,name) AS allergen_groups
  FROM by_group
  GROUP BY recipe_id
),
stats AS (
  SELECT recipe_id,
         count(DISTINCT ingredient) AS total,
         count(DISTINCT ingredient) FILTER (WHERE classification <> 'unknown') AS assessed,
         coalesce(jsonb_agg(DISTINCT ingredient) FILTER (WHERE classification='unknown'),
                  '[]'::jsonb) AS unknown
  FROM expanded
  GROUP BY recipe_id
),
updated AS (
  UPDATE public.dish_health_info h
  SET potential_allergens = p.allergen_groups,
      health_considerations = (
        SELECT coalesce(jsonb_agg(
          CASE WHEN n.item->>'type'='coverage'
               THEN jsonb_set(n.item,'{unknown_allergen_ingredients}',s.unknown,true)
               ELSE n.item END
        ),'[]'::jsonb)
        FROM jsonb_array_elements(coalesce(h.health_considerations,'[]'::jsonb)) n(item)
        WHERE coalesce(n.item->>'type','') <> 'allergen_assessment'
          AND coalesce(n.item->>'text','') NOT LIKE 'Allergen coverage is unknown for:%'
          AND coalesce(n.item->>'text','') <> 'Compound ingredient formulations vary. Possible allergens need confirmation from the actual product label.'
      ) || jsonb_build_array(jsonb_build_object(
        'type','allergen_assessment',
        'text',format('Allergen assessment: %s of %s ingredient names (%s%%).',
                    s.assessed,s.total,
                    round(100.0*s.assessed/nullif(s.total,0),2)),
        'assessed',s.assessed,'total',s.total,
        'percent',round(100.0*s.assessed/nullif(s.total,0),2),
        'unknown',s.unknown,
        'framework','UK FSA 14; FDA nine-group cross-reference',
        'source_url','https://www.food.gov.uk/business-guidance/allergen-guidance-for-food-businesses',
        'rules_revision','supabase-backfill-v1'
      )),
      updated_at=now()
  FROM payload p JOIN stats s ON s.recipe_id=p.recipe_id
  WHERE h.recipe_id=p.recipe_id
  RETURNING h.recipe_id
)
SELECT count(*) AS updated_recipes FROM updated;
