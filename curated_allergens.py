"""Reviewed exact-name seed catalogue, not a probabilistic or substring classifier.

The runtime database is Supabase. This versioned catalogue is the reproducible
curation input; it contains no provider cache or recipe processing state.
"""
from copy import deepcopy
from dish_info_common import DATA_VERSION, normalize, safe_url

RULE_VERSION = 1
FRAMEWORK = 'UK_FSA_14_with_FDA_9_cross_reference'
FSA = 'https://www.food.gov.uk/business-guidance/allergen-guidance-for-food-businesses'
FDA = 'https://www.fda.gov/food/buy-store-serve-safe-food/food-allergies-what-you-need-know'
FSS = 'https://www.foodstandards.gov.scot/business-guidance/running-a-food-business/managing-allergens'
NHS = 'https://www.cuh.nhs.uk/patient-information/cows-milk-egg-wheat-and-soya-mews-free-diet/'
SPICES = 'https://aicrps.res.in/crops/'
OILS = 'https://anaphylaxis.org.uk/factsheets/plant-based-oils/'
HING = 'https://www.essential-trading.coop/document/3125/H508%20-%20Asafoetida%20-%20Compounded%20-20251203.pdf'
KIMCHI = 'https://www.bbcgoodfood.com/recipes/homemade-kimchi'
SOY = 'https://www.kikkoman.eu/food-service/products/detail/kikkoman-naturally-brewed-sweet-soy-sauce'
DISCLAIMER = ('Allergen assessment uses the UK FSA 14 groups, with FDA nine-group references. '
               'It is based on listed ingredient names, not a laboratory test or a complete product label. '
               'Formulations, substitutions and cross-contact can change the risk. '
               'Unknown ingredients remain unassessed. No identified major allergens does not mean safe or allergen-free. '
               'Check the actual product labels and preparation with the person providing the food.')
NONE_NOTE = ('Explicit assessment of the named plain food against the UK FSA 14-group list; '
             'no listed major-allergen identity is identified. This does not assess added ingredients, '
             'cross-contact, other food allergies, or the complete formulation of a branded product.')
POSSIBLE_NOTE = ('This food category can have the listed allergenic ingredients; formulations vary. '
                 'These are candidates requiring the actual ingredient label, not confirmed contents '
                 'or an exhaustive analysis of every product.')

def names(text):
    return [normalize(n) for n in text.replace('\n', '|').split('|') if normalize(n)]

# Exact plain-food identities only. No blanket "no matching rule => none" rule,
# no removal of vegan/gluten-free qualifiers, and no substring matching.
PLAIN = '''
agave syrup|alfalfa sprout|allspice|allspice berry|amaranth flour|anaheim|anise|anise seed|anise star|annatto powder
apple|apple juice|apricot|arbol chili|arborio rice|arrowroot starch|artichoke|arugula|asian pear|asparagus|assam tea bag|avocado|avocado oil
baby leek|baby spinach|baking cocoa|baking soda|bamboo shoot|banana|banana flower|banana leaf|banana pepper|banana plantain
basil|basil leaf|basmati rice|bay leaf|beef|beef chuck|beef flank steak|beef oxtail|beef rump|beef shank|beef short rib|beef sirloin|beef sirloin steak|beef steak|beef stew meat|beef strip|beef tenderloin|beef top sirloin
beet|beetroot|bell pepper|bengal gram|besan|besan flour|besan flour chickpea|besan powder
bird chili|bird eye chili|bird eye chili pepper|bird pepper|bitter gourd|bitter melon
black bean|black cardamom|black cardamom pod|black cumin seed|black eyed bean|black fungus|black garlic|black gram|black ground pepper|black mushroom|black pepper|black pepper powder|black peppercorn|black rice|black salt|black tea|black tea leaf
blood orange|blueberry|bok choy|bok choy leaf|boston lettuce|brinjal|broad field bean|broccoflower|broccoli|broccoli florets|broccoli pieces|brown sugar|brussels sprout|buckwheat flour|button mushroom
cabbage|cabbage leaf|calamondins|california dried red chili|camias|cane sugar|cannellini bean|canola oil|capsicum|capsicum pepper|caraway seed|cardamom|cardamom pod|cardamom powder|cardamom seed|carom|carom seed|carrot greens|carrot strip|cassava|caster sugar|cauliflower|cayenne|cayenne pepper|cayenne powder|ceylon tea bag
chana|chana dal powder|chana powder|chayote squash|chee fah chili|cherry|cherry tomato
chicken|chicken backs|chicken breast|chicken breast fillet|chicken drumsticks|chicken gizzard|chicken leg quarters|chicken legs|chicken liver|chicken meat|chicken pieces|chicken thigh|chicken thigh meat|chicken wings|chickpea|chickpea flour
chili|chili de arbol|chili flake|chili leaf|chili padi|chili pepper|chili pepper flake
chinese cabbage|chinese chives|chinese mushroom|chinese parsley root|chinese radish|chinese red chili|chives|chives kucai|chrysanthemum greens
cilantro|cilantro leaf|cilantro root|cilantro sprig|cilantro stem|cinnamon|cinnamon stick|citric acid|citrus juice|clove|clove garlic|club steak|coarse sea salt|cocoa powder|coconut|coconut leaf|coconut oil|coconut water|coffee ground|cold water|cooking banana|copra
coriander|coriander leaf|coriander powder|coriander root|coriander seed|coriander sprig|corn|corn flour|corn husks|corn kernel|corn meal|corn oil|corn starch|corn syrup|cornflour|cornmeal|cornstarch|cream of tartar|cremini|cucumber|cucumber slice|cumin|cumin jeera|cumin powder|cumin seed|curry leaf
daikon|daikon radish|dark brown sugar|dark cocoa powder|dark palm sugar|dashi kombu|daun kunyit leaf|daun salam|dill|dried basil|dried chili|dried chili pepper flake|dried chinese mushroom|dried lime|dried mango powder|dried mint|dried red chili|dried red chili pepper|dried red pepper|dry coconut|dry red chili|dry yeast|duck|duck breast|duck legs
egg plant|eggplant|egoma leaf|egyption rice|english cucumber|enoki|enoki mushroom|fennel powder|fennel seed|fennel seed powder|fenugreek|fenugreek leaf|fenugreek powder|fenugreek seed|fiddleheads|finger millet|finger millet flour|flank steak|flap meat|french bean|fresh chives|fresh serrano
gai lan|galangal|garbanzo|garlic bits|garlic clove|garlic powder|ginger leaf|ginger powder|ginger strip|gingerroot|glucose|glutinous rice|glutinous rice flour|glutinous white rice|gochugaru|golden brown sugar|gongura|gongura leaf|gram flour|granny smith|grape leaf|grape tomato|grapefruit|grapeseed|grapeseed oil
green apple|green bean|green bell pepper|green bird eye chili|green cabbage|green capsicum|green cardamom|green cardamom powder|green carom seed|green cayenne|green chili|green chili leaf|green chili pepper|green corn|green ginger|green gram|green jackfruit|green jalapeno chili|green leaf lettuce|green long pepper|green mango|green mango powder|green mango sour|green mung bean|green onion|green onion top|green papaya|green pea|green pepper|green scallion|green serrano chili|green tea leaf|green tomato|green unripe mango
ground beef|ground lamb|ground pepper|grounded beef|habanero pepper|haldi|honey|horseradish|hot chili chopped|hot chili pepper|hot pepper flake
ice|ice cube|ice water|iceberg lettuce|indian gooseberry|italian parsley|jaggery|jalapeno|jalapeno chili|jalapeno pepper|japanese leek|japanese white rice|jasmine rice|javitri|jeera|kabocha squash|kabosu|kaffir lime leaf|kaiware|kale|kang kung|kangkong leaf|kashmiri chili|kashmiri dried red chili|kasuri methi|kasuri methi fenugreek|kasuri methi leaf|katakuriko|kelp|kencur|kerisik|kernel corn|kiwi|kohlrabi|kokum petals|kombu|korean chili|korean chili flake|korean dried chili pepper|korean green chili|korean green chili pepper|korean red chili flake|korean red chili pepper|korean red pepper flake|korean red pepper thread|korean zucchini|kumara|kumquats
lamb|lamb loin chop|lamb shoulder chop|lard|lavang|leaf parsley|lean stewing beef|leek|lemon|lemon grass|lemon grass stalks|lemon juice|lemon wedges|lemon zest|lemongrass|lemongrass stalks|lengkuas|lentil|lettuce|lettuce leaf|light brown sugar|light muscovado sugar|lily bulbs|lime|lime juice|lime leaf|lime wedges|lime zest|long bean|lukewarm water
macapuno coconut|mace|mandarin|mandarin orange segment|mange touts|mango|mango powder|mango puree|maple syrup|melon|melon seed|methi leaf|methi powder|millet flour|mineral water|mint|mint flake|mint leaf|molasses|monosodium glutamate|moth sprout|msg|mung|mung bean|mung bean sprout|mushroom|mushroom scraps|mutton|mutton chop|mutton fat|mutton leg|mutton mince
napa cabbage|napa cabbage leaf|napa chinese cabbage|negi|new mexico chili|new york strip steak|nigella seed|nori|nori seaweed|nori seaweed sheet|nori sheet|nutmeg|nutmeg powder
okra|okra pod|olive oil|onion chopped|onion finely chopped|onion leaf|onion powder|orange|orange juice|orange peel|orange rind|orange sweet potato|orange zest|oregano|oxtail|oxtail fat|oyster mushroom
pak choi|palak spinach leaf|palm sugar|pandan leaf|pandanus leaf|papaya|paprika|paprika powder|parsley|parsley leaf|pea|pea pod|peaches|pear|pear juice|pearl onion|pearl sago|pearl tapioca|pepper|pepper flake|pepper green|pepper powder|peppercorn|perrier water|persian cucumber|pigeon pea|pigs ear|pineapple|pineapple chunks|pineapple juice|pineapple ring|pineapple tidbits|pink salt|plain rice|plantain|pomegranate juice|pomegranate powder|pomegranate seed|pomelo|poppy seed
pork|pork belly|pork belly sliced|pork blood|pork butt|pork chop|pork fat|pork fatback|pork loin|pork rib|pork shoulder|pork skin|pork tenderloin|porterhouse steak|portobello|potato flour|potato starch|powdered turmeric|pudina|pumpkin|purple basil|purple cabbage|purple daikon radish|purple onion|purple sweet potato|purple yam|purple yam powder
radish|ragi flour|rajma|red bean|red bell pepper|red cabbage|red capsicum|red cayenne|red chili|red chili flake|red chili pepper|red fresno chili|red friggiteli pepper|red hot chili|red jalapeno|red jalapeno chili|red kidney bean|red leaf lettuce|red lentil|red lentil split|red onion|red pepper|red pepper flake|red pepper sliced|red radish|red serrano|rib eye|rib eye beef|rib eye steak|rice flour|rice paddy herb|rice water|ripe banana|ripe mango|roasted chana|roasted chana flour|rock salt|roma tomato|romaine hearts|romaine leaf|romaine lettuce|romanesco cauliflower|rose buds|russet potato
saffron|saffron strand|sage|sago|salam leaf|salted water|saunf|saunf powder|savoy cabbage|scallion|scallion greens|scallion top|screw pine|screw pine leaf|seedless cucumber|seedless watermelon|sendha|serai lemon grass|serrano|serrano pepper|shallot|shaved ice|sheep meat|shiitake|shiitake mushroom|shiitake mushroom caps|sirloin|sirloin steak|sirloin tip|sliced onion|snake bean|snap pea|snow pea|soda bicarbonate|sorghum syrup|sorrel leaf|spanish paprika|spearmint|spinach|spinach leaf|spinach puree|split bengal gram|split black gram|split green gram|sprouted moth|squash|star anise|star anise pod|stewing beef|straw mushroom|string bean|sugar snap pea|sumac|sunflower oil|sunflower seed|sunflower seed oil|superfine sugar|sweet corn kernel|sweet pepper|sweet potato|sweet rice|sweetcorn
tamarind|tamarind juice|tamarind leaf|tamarind pulp|tamarind water|tangerine juice|tangerine peel|tapioca|tapioca flour|tapioca pearl|tapioca powder|tapioca starch|tea bag|thai basil|thai basil leaf|thai bird chili|thai chili|thyme|thyme leave|toasted pumpkin seed|tomato|tomato paste|tomato puree|toor|turkey|turkey breast|turkey drumsticks|turmeric|turmeric leaf|turmeric powder|turmeric root|turnip|ube|under ripe mango|unripe papaya|urfa chili
vanilla bean|wagyu|water chestnuts|water spinach|watercress|watermelon rind|white basmati rice|white cabbage|white jasmine rice|white onion|white pepper|white pepper powder|white peppercorn|white rice flour|wood ear mushroom|yak meat|yeast|yellow bell pepper|yellow chives|yellow lentil|yellow onion|yellow pepper|yellow rock sugar|yellow split pea|yellow squash|yu choy|zucchini
'''

# Fields on these rows distinguish "named allergen present" from other possible
# ingredients in a compound. Species/cut qualifiers are explicit, never stripped.
IDENTIFIED = {
 'Milk': '''akkawi cheese|american cheese|cheddar cheese|cottage cheese|emmentaler cheese|feta|feta cheese|gouda cheese|hung curd greek yogurt|hung curd yogurt|indian cheese|kasseri cheese|mawa|monterey jack cheese|mozzarella cheese|paneer indian cheese|pure ghee|ricotta cheese|turkish clotted cream|unsweetened butter|whipping cream|white cheese|yogurt cheese|yogurt curd|yogurt paste''',
 'Egg': 'duck eggs|quail eggs|salted eggs',
 'Tree nuts': 'cashew powder|cashewnuts|macadamia nut|pistachio nut|seed cashew nut|toasted walnut|unsalted pistachio',
 'Peanuts': 'black peanuts|roasted peanuts|salted peanuts|unsalted peanuts|unsalted roasted peanuts',
 'Fish': '''albacore tuna|anchovy paste|blue tail mullet|bonito flake|catfish|catfish fillet|dried sardine|halibut steak|marlin|pollack|red snapper|salmon fillet|salmon steak|sardine extract|swordfish steak|tilapia fillet|tuna steak|white tuna|yellowfin|yellowtail sashimi''',
 'Crustaceans': 'green king prawn|green prawn|green shrimp|krill|salted crab|soft shell crab',
 'Molluscs': 'cockles|manila clams|octopi|sea scallops',
 'Soy': 'cake tofu|soya bean|soya bean cake|soya chunks|soya nuggets|soymilk|sundubu',
 'Wheat': 'chinese braised gluten|freekeh|semolina flour|wholewheat flour',
 'Cereals containing gluten': 'pearl barley',
 'Celery': 'celery rib|celery stick',
 'Mustard': 'mustard greens|mustard root|mustard stem|split mustard seed',
}

def catalogue():
    rows = {}
    def add(values, identified=(), possible=(), source=FSS, note=None, extra_sources=()):
        for name in names(values):
            allergens=[{'name':a,'classification':'identified','source_url':source} for a in identified]
            allergens += [{'name':a,'classification':'possible','source_url':source} for a in possible if a not in identified]
            state='identified' if identified else 'possible' if possible else 'assessed_none'
            evidence=[{'url':FSA,'role':'framework'}, {'url':source,'role':'identity_or_formulation'}]
            evidence += [{'url':u,'role':'supporting_reference'} for u in extra_sources]
            rows[name]={'ingredient_name':name,'normalized_name':name,'aliases':[], 'allergens':allergens,
                        'classification':state,'source_url':source,'approved':True,'data_version':DATA_VERSION,
                        'framework':FRAMEWORK,'evidence':evidence,'rule_version':RULE_VERSION,
                        'assessment_note':note or (POSSIBLE_NOTE if possible else 'The named ingredient is a member or derivative of the identified FSA allergen group.' if identified else NONE_NOTE)}
    add(PLAIN,source=NHS,extra_sources=(SPICES,))
    add('spring onion|sichuan pepper|sichuan peppercorn|aleppo pepper|aleppo chili pepper flake|amchur powder|ajwain|carrot boiled|cooked rice|steamed rice|sticky rice|sushi rice|puffed rice|sparkling water|club soda|green coconut juice|vanilla extract|vanilla|vanilla powder|vanilla sugar|rose water|rosewater|orange blossom water|orange flower water|water|chilkewali mung dal',source=NHS,extra_sources=(SPICES,))
    for allergen, values in IDENTIFIED.items():
        add(values,identified=(allergen,))
    add('kecap manis|ketjap manis',identified=('Soy',),possible=('Wheat',),source=SOY,
        extra_sources=('https://www.ah.nl/producten/product/wi199175/conimex-ketjap-manis',))
    add('brown miso|white miso paste|korean fermented soybean paste|korean miso',identified=('Soy',),possible=('Cereals containing gluten',),source=NHS)
    add('korean fermented hot pepper paste|korean hot pepper paste|korean chili sauce|hot bean sauce|black bean paste|chinese black bean sauce',possible=('Soy','Wheat'),source=NHS)
    add('kimchi|cabbage kimchi|napa cabbage kimchi|napa kimchi|sour kimchi|kimchi juice|kimchi liquid|korean pickled cabbage',possible=('Fish','Crustaceans'),source=KIMCHI)
    add('noodle|noodle sheet|asian noodle|chinese noodle|dried noodle|vermicelli|vermicelli noodle|angel hair|angel hair pasta|elbow macaroni|fettuccine|linguine|lo mein|ramen noodle|ramen style noodle|udon noodle|somen noodle|chukasoba noodle|yakisoba noodle|e fu noodle|ee fu noodle|yee mein|jaa jang myun noodle',possible=('Wheat','Egg'),source=FSS)
    add('buckwheat soba noodle',possible=('Wheat',),source=FSS)
    add('dumpling wrappers|gyoza wrappers|spring roll wrap|spring roll wrappers|wonton|phyllo|phyllo dough|phyllo pastry|phyllo pastry dough|filo dough|yufka',possible=('Wheat','Egg'),source=FSS)
    add('baguette|baguette roll|bread flour|breadcrumb|cake flour|crackers|crouton|dough|farina cereal|french bread|hamburger buns|hoagie buns|knead flour|naan bread|panko breadcrumb|panko japanese bread crumbs|pav|pavs|pita bread|pizza dough|refined flour|turkish bread|turkish flatbread|white bread|white flour',possible=('Wheat',),source=NHS)
    add('bake powder|baking powder',possible=('Wheat',),source=FSS)
    add('asafoetida|asafoetida powder|hing',possible=('Wheat',),source=HING)
    add('curry powder|burmese curry powder|hot curry powder|indian curry powder|madras curry powder|garam masala|garam masala powder|masala|goda masala|chaat masala|chaat masala powder|tandoori masala|tandoori masala powder|chili powder|green chili powder|red chili powder|kashmiri chili powder|kashmiri red chili powder|turmeric chili powder',possible=('Wheat','Mustard'),source=NHS,extra_sources=(FSS,),note=POSSIBLE_NOTE+' Powder/blend names do not establish a pure single-spice formulation.')
    add('baharat|baharat mix|baharat mixed spice|blackening seasoning|chicken spice|chicken chili powder|cumin chili powder|five spice powder|chinese five spice powder|kabsa spice mix|seasoning|spicy pickle|pickle|pickle relish|chutney|mango chutney|mint chutney|green chutney|red chutney|tamarind chutney',possible=('Wheat','Mustard'),source=NHS,extra_sources=(FSS,))
    add('beef bouillon|beef bouillon cube|beef base|beef broth|beef stock|beef dashida|bonito soup stock|bouillon cube|broth|broth powder|chicken bouillon granules|chicken broth|chicken powder|chicken stock|chinese soup stock|dashi soup stock|dashida|low sodium broth|meat stock|mutton stock|oxtail stock|soup base|stock vegetable|turkey stock|vegetable broth|vegetable stock|gravy',possible=('Celery','Wheat','Soy','Milk'),source=NHS,extra_sources=(FSS,))
    add('bacon|beef balls|breakfast sausage|chinese bacon|chinese sausage|chorizo sausage|ham|hot dogs|italian sausage|luncheon meat|meatballs|pork meatballs|pork sausage|sausage|spam|spicy chicken sausage|spreadable pork|vienna sausage|vietnamese pork roll sausage|liver pate',possible=('Wheat','Soy','Milk'),source=NHS)
    add('imitation crab legs',identified=('Fish',),possible=('Wheat','Egg','Soy'),source=NHS)
    add('prawn crackers',identified=('Crustaceans',),possible=('Egg','Wheat'),source=FSS,extra_sources=('https://www.cuh.nhs.uk/patient-information/egg-free-diet/',))
    add('worcestershire sauce',possible=('Fish','Soy','Wheat'),source=FSS,extra_sources=(NHS,))
    add('teriyaki',possible=('Soy','Wheat'),source=NHS)
    add('japanese mayonnaise',possible=('Egg',),source=FSS)
    add('margarine|shortening|cooking spray',possible=('Milk','Soy'),source=NHS)
    add('oil|cooking oil|vegetable oil|salad oil|neutral oil',possible=('Soy','Peanuts','Sesame'),source=OILS,note=POSSIBLE_NOTE+' Botanical source and refining process are unspecified; no universal oil formulation is assumed.')
    add('oil ghee',possible=('Milk',),source=FSS,note='Ambiguous combined oil/ghee name: ghee is milk-derived, but the chosen fat is not specified.')
    add('oil mustard',possible=('Mustard',),source=OILS)
    add('dijon|dijon mustard',identified=('Mustard',),possible=('Wheat',),source=NHS)
    add('beer|dry sherry|sherry|sherry wine|sweet sherry|red wine|white wine|vermouth|cooking wine|white cooking wine|asian rice wine|chinese rice wine|chinese wine|japanese rice wine|japanese sake|japanese sweet rice wine|japanese sweet wine|rice wine|sake|shaoxing rice wine|shaoxing wine|xiao shing wine|mirin|raisins|golden raisins|dates|seedless dates|currant|figs',possible=('Sulphites',),source=FSS,note=POSSIBLE_NOTE+' Sulphites require concentration evidence (>10 mg/kg or litre for UK declaration); quantity is unknown, so this is possible, not confirmed.')
    add('pancake|vanilla wafers|custard|pudding|leche flan|lemon curd|ice cream|vanilla ice cream',possible=('Milk','Egg','Wheat'),source=FSS,extra_sources=(NHS,))
    add('amaretti cookie',possible=('Tree nuts','Egg','Wheat'),source=FSS)
    add('chocolate|bittersweet chocolate|chocolate syrup|cocoa mix',possible=('Milk','Soy'),source=NHS)
    add('chocolate covered peanuts',identified=('Peanuts',),possible=('Milk','Soy'),source=NHS)
    add('nutella',possible=('Tree nuts','Milk','Soy'),source='https://www.nutella.com/int/en/products/nutella',note=POSSIBLE_NOTE+' Manufacturer classic jar lists hazelnut, milk and soya; an unspecified product name does not establish variant or market.')
    # Synonyms are explicit and limited to the same food identity; preparation
    # variants above are independent rows, not nutritional equivalences.
    for canonical, aliases in {
        'cilantro':'coriander leaf', 'ginger':'gingerroot', 'turmeric':'haldi',
        'cumin':'jeera', 'fenugreek leaf':'methi leaf|kasuri methi|kasuri methi leaf',
        'lemongrass':'lemon grass|serai lemon grass', 'eggplant':'egg plant|brinjal',
        'bok choy':'pak choi', 'cornstarch':'corn starch', 'soy milk':'soymilk',
    }.items():
        # Add alias metadata only to rows in this catalogue; existing rules are
        # preserved by the planner. Each alias also has its own exact row.
        if canonical in rows:
            rows[canonical]['aliases']=names(aliases)
    return rows

def plan_dataset_rules(ingredients, existing):
    """Preserve every existing rule, including withdrawals; insert exact gaps."""
    from dish_health import _allergens
    curated=catalogue()
    direct={r['normalized_name']:r for r in existing}
    withdrawn={normalize(a) for r in existing if not r.get('approved')
               for a in [r.get('normalized_name',''),*(r.get('aliases') or [])]}
    planned=[]
    for name in sorted(set(map(normalize, ingredients))):
        if name in direct or name in withdrawn:
            continue
        groups,unknown=_allergens([name],existing)
        if not unknown:
            # Materialize an existing approved alias without reinterpreting it.
            matches=[r for r in existing if r.get('approved') and r.get('data_version')==DATA_VERSION
                     and name in [normalize(a) for a in [r.get('normalized_name',''),*(r.get('aliases') or [])]]]
            allergens=[{'name':g['name'],'classification':g['classification'],'source_url':g['source_url']}
                       for g in groups if g['classification'] in {'identified','possible'}]
            state='identified' if any(g['classification']=='identified' for g in groups) else 'possible' if allergens else 'assessed_none'
            source=next(g['source_url'] for g in groups if safe_url(g.get('source_url')))
            row={'ingredient_name':name,'normalized_name':name,'aliases':[], 'allergens':allergens,
                 'classification':state,'source_url':source,'approved':True,'data_version':DATA_VERSION,
                 'framework':FRAMEWORK,'rule_version':RULE_VERSION,
                 'evidence':[{'url':r['source_url'],'role':'preserved_rule','rule':r['normalized_name']} for r in matches],
                 'assessment_note':'Exact approved alias of an existing source-backed Supabase rule; original rule preserved.'}
        elif name in curated:
            row=deepcopy(curated[name])
        else:
            row={'ingredient_name':name,'normalized_name':name,'aliases':[],'allergens':[],
                 'classification':'unknown','source_url':FSA,'approved':True,'data_version':DATA_VERSION,
                 'framework':FRAMEWORK,'rule_version':RULE_VERSION,'evidence':[{'url':FSA,'role':'framework_only'}],
                 'assessment_note':'Insufficient validated identity or formulation evidence. Full ingredient name, product label or recipe specification is required; no safety inference.'}
        # Do not create a new alias that overrides any existing/withdrawn rule.
        row['aliases']=[a for a in row.get('aliases',[]) if a not in direct and a not in withdrawn]
        planned.append(row)
    return planned
