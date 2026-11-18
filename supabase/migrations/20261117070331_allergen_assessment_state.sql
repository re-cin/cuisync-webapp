ALTER TABLE public.ingredient_allergens DROP CONSTRAINT ingredient_allergens_classification_check;
ALTER TABLE public.ingredient_allergens ADD CONSTRAINT ingredient_allergens_classification_check
CHECK (classification IN ('identified', 'possible', 'assessed_none', 'unknown'));
COMMENT ON COLUMN public.ingredient_allergens.classification IS
'Source-backed ingredient assessment: assessed_none means no identified FSA major allergens for the named unmodified ingredient, never a safety guarantee.';
