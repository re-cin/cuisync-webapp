"""Limit the on-screen nutrition notes without changing stored evidence."""
import unittest

from dish_information import _combined_background, _key_benefits
from nutritional_benefits import qualitative_protein_benefits, select_important_benefits


class KeyBenefitsTests(unittest.TestCase):
    def test_prioritizes_distinct_nutrients_up_to_three(self):
        notes = [
            {"ingredient": "spinach", "text": "spinach may contribute iron, which helps oxygen transport."},
            {"ingredient": "beans", "text": "beans may contribute protein, which supports tissue repair."},
            {"ingredient": "chicken", "text": "chicken may contribute protein, which supports tissue repair."},
            {"ingredient": "lentils", "text": "lentils may contribute dietary fiber, which supports regularity."},
            {"ingredient": "carrot", "text": "carrot may contribute vitamin C, which supports collagen."},
        ]
        selected = _key_benefits(notes)
        self.assertEqual(3, len(selected))
        self.assertEqual(["beans", "lentils", "spinach"], [x["ingredient"] for x in selected])
        self.assertEqual(notes[0]["ingredient"], "spinach")  # input remains intact

    def test_unrecognized_notes_still_display_without_duplicates(self):
        notes = [{"ingredient": "A", "text": "Same."}, {"ingredient": "A", "text": "Same."},
                 {"ingredient": "B", "text": "Another."}]
        self.assertEqual(2, len(_key_benefits(notes)))

    def test_chicken_adobo_qualitative_protein_outranks_minor_ingredient_claims(self):
        recipe = {"recipe_id": 844, "ingredient_text": "chicken breast, bay leaf, soy sauce, vinegar, potato, garlic, onion, water, butter"}
        old_notes = [
            {"ingredient": item, "text": f"{item} may contribute protein, which supports tissue repair."}
            for item in ("bay leaf", "soy sauce", "potato", "garlic", "onion")
        ]
        selected = _key_benefits(old_notes, recipe=recipe)
        self.assertEqual("chicken breast", selected[0]["ingredient"])
        self.assertNotIn("bay leaf", [note["ingredient"] for note in selected])
        self.assertNotIn("soy sauce", [note["ingredient"] for note in selected])
        self.assertEqual("qualitative_food_group", selected[0]["evidence_type"])
        self.assertTrue(selected[0]["source_url"].startswith("https://snaped.fns.usda.gov/"))
        self.assertEqual(5, len(old_notes))  # Supabase evidence is not changed.

    def test_compound_chicken_terms_and_eggplant_do_not_become_meat_or_egg(self):
        recipe = {"ingredient_text": "chicken stock, chicken spice, eggplant, chicken powder, chicken flavored oil"}
        self.assertEqual([], qualitative_protein_benefits(recipe))
        displayed = _key_benefits([], recipe=recipe)
        self.assertNotIn("protein", [note["nutrient"] for note in displayed])
        self.assertTrue(all(note["ingredient"] != "chicken stock" for note in displayed))

    def test_one_note_per_nutrient_even_with_two_protein_foods(self):
        recipe = {"ingredient_text": "chicken breast, egg, tofu, soy sauce"}
        selected = _key_benefits([], recipe=recipe)
        self.assertEqual(1, len(selected))
        self.assertEqual("chicken breast", selected[0]["ingredient"])

    def test_unmatched_chicken_with_no_usda_notes_can_still_display_qualitative_note(self):
        recipe = {"ingredient_text": "chicken breast, water, chicken stock"}
        selected = _key_benefits([], recipe=recipe)
        self.assertEqual(1, len(selected))
        self.assertIn("protein", selected[0]["text"].lower())
        self.assertNotIn("g per", selected[0]["text"].lower())

    def test_other_cuisines_use_main_ingredients_not_condiments(self):
        cases = [
            ("chicken thigh, ginger root, curry powder, fish sauce, coconut milk", "chicken thigh", "protein"),
            ("eggs, jasmine rice, garlic, soy sauce, salt", "eggs", "protein"),
            ("carrot, cauliflower, green capsicum, rice, garam masala", "carrot", "vitamin a"),
            ("salmon fillet, carrot, sesame seed, ginger", "salmon fillet", "protein"),
            ("condensed milk, water, coffee", "condensed milk", "calcium"),
            ("firm tofu, sushi rice, hot chili oil", "firm tofu", "protein"),
            ("lentil, black bean, onion, turmeric", "lentil", "protein"),
            ("rice, carrot, cauliflower, water", "carrot", "vitamin a"),
        ]
        for ingredients, name, nutrient in cases:
            with self.subTest(ingredients=ingredients):
                selection = select_important_benefits({"ingredient_text": ingredients}, [])
                self.assertTrue(selection)
                self.assertEqual((name, nutrient), (selection[0]["ingredient"], selection[0]["nutrient"]))
                self.assertLessEqual(len(selection), 3)
                self.assertEqual(len({note["nutrient"] for note in selection}), len(selection))
                self.assertEqual(len({note["ingredient"] for note in selection}), len(selection))

    def test_no_claims_for_coffee_spices_sugar_or_ambiguous_compounds(self):
        for ingredients in ("water, coffee, salt, sugar", "curry powder, salt, ginger, bay leaf",
                            "chicken stock, vegetable oil, garlic powder"):
            with self.subTest(ingredients=ingredients):
                self.assertEqual([], select_important_benefits({"ingredient_text": ingredients}, []))

    def test_potato_is_not_promoted_as_protein(self):
        recipe = {"ingredient_text": "chicken breast, potato, soy sauce, bay leaf"}
        notes = [{"ingredient": x, "text": f"{x} may contribute protein, which supports repair."}
                 for x in ("potato", "bay leaf", "soy sauce")]
        selected = select_important_benefits(recipe, notes)
        self.assertEqual("protein", selected[0]["nutrient"])
        self.assertEqual("chicken breast", selected[0]["ingredient"])
        self.assertNotIn("protein", [x["nutrient"] for x in selected[1:]])

    def test_dataset_wide_screening_uses_exact_ingredient_names_and_distinct_benefits(self):
        """All 900 recipes use the same public selector; don't special-case Adobo."""
        import json
        from pathlib import Path
        recipes_file = Path(__file__).resolve().parents[1] / "recipes.json"
        recipes = json.loads(recipes_file.read_text(encoding="utf-8"))
        self.assertEqual(900, len(recipes))
        for dish in recipes:
            with self.subTest(recipe_id=dish["recipe_id"]):
                selected = select_important_benefits(dish, [])
                self.assertLessEqual(len(selected), 3)
                self.assertEqual(len(selected), len({n["nutrient"] for n in selected}))
                self.assertEqual(len(selected), len({n["ingredient"] for n in selected}))
                allowed = {item.strip().casefold() for item in dish["ingredient_text"].split(",")}
                for item in selected:
                    self.assertIn(item["ingredient"].casefold(), allowed)
                    self.assertTrue(item["source_url"].startswith("https://"))
                    self.assertNotIn("g per serving", item["text"].lower())

    def test_dairy_calcium_is_preferred_and_coconut_milk_is_not_dairy(self):
        dairy = select_important_benefits({"ingredient_text": "condensed milk, coffee"}, [])
        self.assertEqual([("condensed milk", "calcium")], [(n["ingredient"], n["nutrient"]) for n in dairy])
        self.assertEqual([], select_important_benefits({"ingredient_text": "coconut milk, coffee"}, []))

    def test_combines_background_fields_into_one_normalized_paragraph(self):
        self.assertEqual(
            "Dishes were preserved with vinegar. Families prepare this dish for celebrations.",
            _combined_background("Historical Background: Dishes were preserved\nwith vinegar.",
                                 "Cultural Background: Families prepare this dish for celebrations."),
        )
        self.assertEqual("One explanation.", _combined_background("One explanation.", "One explanation."))
        self.assertEqual("", _combined_background(None, ""))

    def test_empty_notes_remain_empty(self):
        self.assertEqual([], _key_benefits([]))


if __name__ == "__main__":
    unittest.main()
