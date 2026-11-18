"""Conservative qualitative nutrition notes and ranking for CuiSync.

A food-group fact is NOT a USDA nutrient-composition match for a specific
recipe ingredient. No serving quantities or recipe totals are inferred.
"""
import re

from dish_info_common import ingredient_names, normalize

# USDA Food and Nutrition Service: chicken, meat, seafood, eggs, legumes,
# nuts and seeds are protein foods. This is a group-level reference, not a
# statement about a specified USDA food identity or a recipe serving.
USDA_PROTEIN_FOODS = (
    "https://snaped.fns.usda.gov/sites/default/files/documents/"
    "familymeals_lookforleanproteinfoods.pdf"
)

# Exact, plain ingredient identities only. A generic "chicken stock", "chicken
# powder", "meat flavor", or compound sauce must never inherit a meat claim.
POULTRY = {
    "chicken", "chicken breast", "chicken breast fillet", "chicken breast fillets",
    "chicken thigh", "chicken thighs", "chicken thigh meat", "chicken legs",
    "chicken drumstick", "chicken drumsticks", "chicken wings", "chicken pieces",
    "chicken meat", "chicken leg quarters", "chicken gizzard", "chicken liver",
    "chicken backs", "turkey", "turkey breast", "turkey drumsticks", "duck",
    "duck breast", "duck legs",
}
MEAT = {
    "beef", "ground beef", "grounded beef", "beef steak", "beef shank",
    "beef tenderloin", "beef sirloin", "beef sirloin steak", "beef chuck",
    "beef brisket", "beef flank steak", "beef ribs", "beef short rib",
    "beef strip", "beef top sirloin", "beef rump", "beef oxtail",
    "stewing beef", "lean stewing beef", "oxtail", "sirloin steak", "steak", "flank steak", "rib eye",
    "pork", "pork loin", "pork belly", "pork belly sliced", "pork chop",
    "pork shoulder", "pork tenderloin", "pork rib", "pork butt",
    "lamb", "lamb shoulder", "lamb loin chop", "lamb shoulder chop",
    "mutton", "mutton chop", "mutton leg", "mutton mince", "goat meat", "yak meat",
}
SEAFOOD = {
    "fish", "salmon", "salmon fillet", "salmon steak", "tuna", "tuna steak",
    "tilapia", "tilapia fillet", "catfish", "catfish fillet", "cod",
    "sardine", "sardines", "mackerel", "pollack", "halibut steak",
    "red snapper", "shrimp", "prawn", "prawns", "squid", "octopus", "white tuna", "green prawn",
    "mussels", "mussel", "crab meat", "crab", "scallops", "clams",
}
EGGS = {"egg", "eggs", "egg white", "egg yolk", "duck egg", "duck eggs",
        "quail egg", "quail eggs", "salted egg", "salted eggs"}
LEGUMES = {
    "chickpea", "chickpeas", "garbanzo", "lentil", "lentils", "red lentil",
    "red lentil split", "green lentil", "yellow lentil", "kidney bean",
    "red kidney bean", "black bean", "mung bean", "green mung bean",
    "pigeon pea", "split green gram", "split black gram", "split bengal gram",
    "green gram", "black gram", "black eyed bean", "broad field bean", "yellow split pea",
    "red bean",
    "tofu", "firm tofu", "silken tofu", "soybean", "soybeans", "soya bean",
    "tempeh", "edamame", "firm tofu", "silken tofu", "sundubu",
    "besan", "besan flour", "chickpea flour", "gram flour", "soya chunks", "soya nuggets",
}
NUTS = {"almond", "almonds", "walnut", "walnuts", "cashew", "cashews",
        "peanut", "peanuts", "pistachio", "pistachios", "sunflower seed"}
DAIRY = {"milk", "whole milk", "cow milk", "yogurt", "yoghurt", "greek yogurt",
         "cheese", "paneer", "cottage cheese", "ricotta cheese"}
PROTEIN_IDENTITIES = POULTRY | MEAT | SEAFOOD | EGGS | LEGUMES | NUTS | DAIRY

# Evidence quality of notes already cached from USDA. Seasonings/sauces used
# in small, unknown quantities must not rank above substantial ingredients.
SEASONING_TOKENS = {
    "bay leaf", "pepper", "garlic", "onion powder", "garlic powder", "spice",
    "cumin", "turmeric", "saffron", "clove", "cinnamon", "cardamom",
    "soy sauce", "fish sauce", "vinegar", "salt", "ginger", "vanilla",
    "oregano", "thyme", "paprika", "chili powder", "coriander powder",
    "seasoning", "stock", "broth", "oil", "sugar", "sauce", "paste",
}
VEGETABLE_TOKENS = {"potato", "potatoes", "carrot", "carrots", "spinach", "broccoli", "cauliflower", "cabbage",
                    "kale", "sweet potato", "tomato", "pumpkin", "peas", "green pea"}
NUTRIENTS = ("protein", "dietary fiber", "iron", "calcium", "potassium", "vitamin c", "vitamin a", "carbohydrates")



# Conservative named-food evidence for meaningful qualitative benefits.
# These are not exact FDC matches and do not support a claim about quantities
# or the finished dish. Only exact named ingredients are eligible.
NIA_FOOD_GROUPS = "https://www.nia.nih.gov/health/publication/whats-your-plate/important-nutrients-know"
NIH_VITAMIN_A = "https://ods.od.nih.gov/factsheets/VitaminA-Consumer/"
NIH_VITAMIN_C = "https://ods.od.nih.gov/factsheets/VitaminC-Consumer/"
NIH_POTASSIUM = "https://ods.od.nih.gov/factsheets/Potassium-Consumer/"
NIH_CALCIUM = "https://ods.od.nih.gov/factsheets/Calcium-Consumer/"
USDA_FIBER = "https://www.fda.gov/food/nutrition-food-labeling-and-critical-foods/questions-and-answers-dietary-fiber"

VITAMIN_A_FOODS = {
    "carrot", "carrots", "carrot boiled", "carrot strip", "sweet potato",
    "orange sweet potato", "spinach", "spinach leaf",
    "spinach puree", "palak spinach leaf", "kale", "pumpkin", "winter squash",
    "butternut squash", "broccoli", "broccoli florets", "gai lan",
}
VITAMIN_C_FOODS = {
    "broccoli", "broccoli florets", "cauliflower", "bell pepper",
    "green bell pepper", "red bell pepper", "yellow bell pepper",
    "green capsicum", "red capsicum", "capsicum", "sweet pepper",
    "orange", "orange juice", "lemon", "lime", "grapefruit", "kiwi",
    "tomato", "roma tomato", "tomato puree", "red tomato",
    "green tomato", "papaya", "green papaya", "unripe papaya",
    "gai lan", "pineapple", "kiwifruit",
}
POTASSIUM_FOODS = {
    "potato", "potatoes", "russet potato", "sweet potato", "banana",
    "ripe banana", "plantain", "cooking banana", "spinach",
}
DAIRY_CALCIUM_FOODS = {
    "milk", "cow milk", "whole milk", "skim milk", "powdered milk", "milk powder",
    "condensed milk", "evaporated milk", "yogurt", "yoghurt", "greek yogurt",
    "hung curd yogurt", "hung curd greek yogurt", "cheese", "cheddar cheese",
    "mozzarella cheese", "feta cheese", "paneer", "indian cheese",
    "cottage cheese", "ricotta cheese", "soymilk fortified with calcium",
}
FIBER_FOODS = {
    "chickpea", "chickpeas", "garbanzo", "lentil", "lentils", "red lentil",
    "red lentil split", "green lentil", "yellow lentil", "black bean", "red bean",
    "red kidney bean", "kidney bean", "green gram", "black gram", "mung bean",
    "green mung bean", "green pea", "green peas", "french bean", "green bean",
    "pigeon pea", "black eyed bean", "broad field bean", "yellow split pea", "french bean", "green bean", "green peas", "peas",
    "oats", "oat", "oatmeal", "wholewheat flour", "whole wheat flour",
    "brown rice", "freekeh", "pearl barley", "barley", "bulgur", "quinoa",
    "carrot", "carrots", "carrot boiled", "spinach", "spinach leaf",
    "broccoli", "cauliflower", "cabbage", "napa cabbage", "napa chinese cabbage",
    "sweet potato", "potato", "pumpkin", "green pea", "apple", "banana",
    "ripe banana", "pear", "mango", "ripe mango", "okra", "eggplant",
    "red cabbage", "purple cabbage", "lettuce", "romaine lettuce",
    "gai lan", "zucchini", "cucumber", "persian cucumber", "english cucumber",
    "dates", "figs", "pear", "green papaya", "pineapple",
}
STARCH_GRAIN_FOODS = {
    "rice", "white rice", "brown rice", "basmati rice", "jasmine rice",
    "white basmati rice", "white jasmine rice", "cooked rice", "steamed rice",
    "sticky rice", "glutinous rice", "sushi rice", "sweet rice",
    "glutinous white rice", "rice noodle", "rice noodles", "rice flour",
    "glutinous rice flour", "white rice flour", "noodle", "noodles", "udon noodle",
    "soba noodle", "couscous", "vermicelli", "rice cake", "korean rice cake",
    "dduk", "oats", "oatmeal", "wholewheat flour", "whole wheat flour",
    "barley", "freekeh", "potato", "sweet potato", "tapioca",
    "pearl tapioca", "tapioca pearl", "corn", "whole corn", "quinoa",
    "all purpose flour", "bread flour", "white flour", "refined flour",
    "wheat flour", "semolina", "noodle flour", "whole wheat bread",
    "buckwheat flour", "finger millet flour", "ragi flour",
}

# We deliberately do not use broad substrings: 'coconut milk' is not animal
# milk, 'eggplant' is not egg, and 'chicken powder' is not chicken meat.
QUALITATIVE_GROUPS = (
    ("vitamin a", VITAMIN_A_FOODS, NIH_VITAMIN_A,
     "contains provitamin A carotenoids that the body can convert to vitamin A, which supports normal vision", 92),
    ("vitamin c", VITAMIN_C_FOODS, NIH_VITAMIN_C,
     "can contribute vitamin C, which supports collagen formation and normal immune function", 86),
    ("calcium", DAIRY_CALCIUM_FOODS, NIH_CALCIUM,
     "can contribute calcium, which supports normal bones and teeth", 88),
    ("dietary fiber", FIBER_FOODS, USDA_FIBER,
     "can contribute dietary fiber, which supports normal bowel function", 74),
    ("potassium", POTASSIUM_FOODS, NIH_POTASSIUM,
     "can contribute potassium, which supports normal muscle and nerve function", 77),
    ("carbohydrates", STARCH_GRAIN_FOODS, NIA_FOOD_GROUPS,
     "provides carbohydrates, which the body uses for energy", 54),
)


def qualitative_nutrition_benefits(recipe):
    """Evidence-backed ingredient categories for display, not quantitative USDA matches.

    Each claim is scoped to a plain food identity and conditional on the amount
    and form used; no group rule claims a measured value for a prepared dish.
    """
    candidates = qualitative_protein_benefits(recipe)
    for ingredient in ingredient_names(recipe):
        key = normalize(ingredient)
        for nutrient, identities, source, description, priority in QUALITATIVE_GROUPS:
            if key not in identities:
                continue
            candidates.append({
                "ingredient": ingredient,
                "nutrient": nutrient,
                "evidence_type": "qualitative_food_group",
                "priority": priority,
                "text": f"{ingredient} {description}. The contribution depends on the amount used and preparation.",
                "source_url": source,
            })
    return candidates

def _group_priority(name):
    key = normalize(name)
    if key in POULTRY | MEAT | SEAFOOD | EGGS:
        return 100
    if key in LEGUMES | DAIRY | NUTS:
        return 85
    if key in VEGETABLE_TOKENS or any(key.startswith(f"{v} ") for v in VEGETABLE_TOKENS):
        return 65
    if any(term in key.split() for term in ("bean", "lentil", "vegetable", "fruit")):
        return 55
    if any(token in key for token in SEASONING_TOKENS):
        return 0
    return 35


def _nutrient_type(note):
    declared = str(note.get("nutrient") or "").lower()
    if declared in NUTRIENTS:
        return declared
    text = str(note.get("text") or "").lower()
    if " is a protein food" in text:
        return "protein"
    return next((n for n in NUTRIENTS if re.search(r"\b(?:may contribute|provides?|source of)\s+" + re.escape(n) + r"\b", text)), None)


def qualitative_protein_benefits(recipe):
    """Return at most one USDA food-group note per *recognized* ingredient.

    Does not promote USDA match status or assign a numeric nutrient value.
    """
    notes = []
    for ingredient in ingredient_names(recipe):
        if normalize(ingredient) in PROTEIN_IDENTITIES:
            notes.append({
                "ingredient": ingredient,
                "nutrient": "protein",
                "evidence_type": "qualitative_food_group",
                "text": (f"{ingredient} is a protein food. Protein supports normal "
                         "growth and maintenance of body tissues; the amount "
                         "in this dish depends on how much is used."),
                "source_url": USDA_PROTEIN_FOODS,
            })
    return notes


def select_important_benefits(recipe, verified_notes, limit=3):
    """Rank known food-group contributions ahead of trivial USDA notes.

    Select a maximum of one note per nutrient category, so three identical
    protein statements are never passed off as three different benefits.
    Unknown ingredients and unverified nutrient quantities remain unknown.
    """
    candidates = qualitative_nutrition_benefits(recipe) + list(verified_notes or [])
    ranked = []
    for index, note in enumerate(candidates):
        if not isinstance(note, dict) or not note.get("text"):
            continue
        name = str(note.get("ingredient") or "")
        nutrient = _nutrient_type(note)
        priority = _group_priority(name)
        if nutrient and priority == 0 and note.get("evidence_type") != "qualitative_food_group":
            continue  # Do not highlight trivial protein contributions from spices/sauces.
        if nutrient == "protein" and priority < 70:
            continue  # Protein notes require an actual protein-food ingredient.
        if nutrient and priority < 45 and note.get("evidence_type") != "qualitative_food_group":
            continue  # Generic unquantified statements are not useful.
        # Food-group statements are relevant to major ingredient identities but
        # do not claim an exact USDA composition match.
        # Curated food-group priorities capture ingredient relevance, not amounts.
        # A checked plain-food claim should outrank a trace per-100g nutrient
        # in a condiment, while nutrient duplication remains disallowed.
        if note.get("evidence_type") == "regional_per_100g":
            # A reviewed exact match from a country-specific composition table
            # outranks a broad qualitative category for the same nutrient.
            score = priority + 18
        elif note.get("evidence_type") == "qualitative_food_group":
            score = int(note.get("priority", 95 if nutrient == "protein" else priority))
            if nutrient == "protein":
                # Calcium is generally more distinctive than protein in dairy;
                # avoid concealing it when both notes refer to the same milk.
                score = 77 if normalize(name) in DAIRY_CALCIUM_FOODS else max(score, priority + 5)
        else:
            score = priority - (15 if nutrient == "protein" else 0)
        ranked.append((score, -index, nutrient, note))

    ranked.sort(reverse=True, key=lambda r: (r[0], r[1]))
    selected, seen_nutrients, seen_texts, seen_ingredients = [], set(), set(), set()
    for _, __, nutrient, note in ranked:
        text = str(note["text"]).strip()
        key = normalize(note.get("ingredient"))
        if (text.casefold() in seen_texts or (nutrient and nutrient in seen_nutrients)
                or (key and key in seen_ingredients)):
            continue
        selected.append(note)
        seen_texts.add(text.casefold())
        if key:
            seen_ingredients.add(key)
        if nutrient:
            seen_nutrients.add(nutrient)
        if len(selected) >= limit:
            break
    return selected
