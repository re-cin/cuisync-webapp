"""Country-scoped *food-labeling reference* flags for CuiSync's 13 cuisines.

This module does NOT test foods, certify packaged labels, replace the existing
UK/FDA ingredient_allergens rules, or infer which country's laws apply from a
recipe's origin. A market-of-sale dropdown in results.html selects a reference.

Sources are official food-safety / food-regulator documents where a specific
allergen list was verifiable. No rule is added for a country if only a generic
labeling reference (or a not-yet-effective rule) was found. Exact names only.
Reviewed: November 17, 2026. Re-review before legal/commercial use.
"""
from collections import defaultdict
from dish_info_common import ingredient_names, normalize

# These are ingredient-name *identities*, not substring/ML matches. Some
# processed forms have a POSSIBLE, not identified, classification. An unspecified
# sauce/stock/oil is never assumed to contain any particular allergen.
ALIASES = {
    'Wheat': ('wheat', 'wheat flour', 'wholewheat flour', 'whole wheat flour', 'semolina',
              'bulgur', 'couscous', 'freekeh', 'wheat noodle', 'wheat noodles'),
    'Cereals containing gluten': ('wheat', 'wheat flour', 'whole wheat flour', 'wholewheat flour',
        'semolina', 'bulgur', 'couscous', 'freekeh', 'wheat noodle', 'wheat noodles',
        'barley', 'barley flour', 'pearl barley', 'rye', 'rye flour', 'oats', 'oatmeal',
        'spelt', 'spelt flour'),
    'Shrimp': ('shrimp', 'shrimps', 'prawn', 'prawns', 'dried shrimp', 'shrimp paste',
               'prawn paste', 'fermented shrimp', 'bagoong alamang'),
    'Crab': ('crab', 'crabs', 'crab meat', 'crabmeat', 'salted crab'),
    'Crustaceans': ('shrimp', 'shrimps', 'prawn', 'prawns', 'dried shrimp', 'shrimp paste',
        'prawn paste', 'fermented shrimp', 'bagoong alamang', 'crab', 'crabs', 'crab meat',
        'crabmeat', 'salted crab', 'lobster', 'crayfish', 'krill'),
    'Egg': ('egg', 'eggs', 'egg white', 'egg yolk', 'quail eggs', 'duck eggs',
            'salted eggs', 'fried egg', 'egg noodle', 'egg noodles'),
    'Milk': ('milk', 'whole milk', 'cow milk', 'skim milk', 'milk powder', 'condensed milk',
             'evaporated milk', 'butter', 'ghee', 'cheese', 'cheddar cheese', 'cream',
             'heavy cream', 'yogurt', 'yoghurt', 'greek yogurt', 'paneer'),
    'Peanuts': ('peanut', 'peanuts', 'groundnut', 'groundnuts', 'peanut butter',
                 'peanut paste', 'peanut sauce', 'roasted peanuts'),
    'Soy': ('soybean', 'soybeans', 'soya beans', 'edamame', 'tofu', 'tempeh',
            'soy sauce', 'soya sauce', 'light soy sauce', 'dark soy sauce',
            'tamari', 'miso', 'natto', 'soy milk', 'soya milk', 'soy flour', 'soy paste'),
    'Fish': ('fish', 'fish fillet', 'fish sauce', 'anchovy', 'anchovies', 'anchovy paste',
             'tuna', 'salmon', 'mackerel', 'sardine', 'sardines', 'bonito flakes',
             'dried fish', 'catfish', 'tilapia', 'cod', 'haddock'),
    'Tree nuts': ('almond', 'almonds', 'almond flour', 'almond meal', 'cashew', 'cashews',
        'cashew nut', 'cashew nuts', 'cashew paste', 'walnut', 'walnuts', 'pistachio',
        'pistachios', 'hazelnut', 'hazelnuts', 'pecan', 'pecans', 'macadamia',
        'macadamia nuts', 'brazil nut', 'brazil nuts', 'pine nuts'),
    'Molluscs': ('oyster', 'oysters', 'oyster sauce', 'clam', 'clams', 'manila clams',
        'mussels', 'mussel', 'squid', 'calamari', 'octopus', 'octopi', 'abalone',
        'scallop', 'scallops', 'sea scallops', 'cockles'),
    'Sesame': ('sesame', 'sesame seeds', 'sesame seed', 'black sesame', 'white sesame',
               'sesame paste', 'tahini'),
    'Celery': ('celery', 'celery stalk', 'celery rib', 'celery seed', 'celeriac'),
    'Mustard': ('mustard', 'mustard seed', 'mustard seeds', 'mustard powder',
                'dijon mustard'),
    'Lupin': ('lupin', 'lupine', 'lupin flour', 'lupine flour'),
    'Buckwheat': ('buckwheat', 'buckwheat flour', 'buckwheat groats',
                  'buckwheat noodles', 'buckwheat noodle', 'buckwheat grain'),
    'Cashew': ('cashew', 'cashews', 'cashew nut', 'cashew nuts', 'cashew paste',
               'cashew powder', 'cashewnuts'),
    'Walnut': ('walnut', 'walnuts', 'toasted walnut', 'chopped walnuts'),
    'Almond': ('almond', 'almonds', 'almond meal', 'almond flour', 'almond paste'),
    'Abalone': ('abalone', 'abalones'),
    'Squid': ('squid', 'squid rings', 'squid meat', 'calamari', 'dried squid'),
    'Salmon roe': ('salmon roe', 'ikura'),
    'Orange': ('orange', 'oranges', 'mandarin orange'),
    'Kiwi': ('kiwi', 'kiwifruit', 'kiwi fruit'),
    'Beef': ('beef', 'ground beef', 'beef mince', 'beef sirloin', 'beef flank steak',
              'beef shank', 'beef ribs', 'beef brisket', 'beef tenderloin', 'oxtail'),
    'Salmon': ('salmon', 'salmon fillet', 'smoked salmon'),
    'Mackerel': ('mackerel', 'mackerel fillet', 'spanish mackerel', 'grilled mackerel'),
    'Chicken': ('chicken', 'chicken breast', 'chicken breast fillet', 'chicken breast fillets',
                'chicken thigh', 'chicken thighs', 'chicken legs', 'chicken wing',
                'chicken wings', 'chicken drumstick', 'chicken drumsticks',
                'chicken pieces', 'chicken meat', 'chicken liver', 'chicken gizzard'),
    'Banana': ('banana', 'bananas', 'ripe banana'),
    'Pistachio': ('pistachio', 'pistachios', 'pistachio nut'),
    'Pork': ('pork', 'pork belly', 'pork loin', 'pork shoulder', 'pork ribs',
             'pork rib', 'pork chop', 'ground pork', 'pork mince', 'pork tenderloin'),
    'Macadamia nuts': ('macadamia', 'macadamia nut', 'macadamia nuts'),
    'Peach': ('peach', 'peaches', 'white peach', 'yellow peach'),
    'Japanese yam': ('japanese yam', 'yamaimo', 'nagaimo'),
    'Apple': ('apple', 'apples', 'green apple', 'red apple'),
    'Gelatin': ('gelatin', 'gelatine'),
    'Tomato': ('tomato', 'tomatoes', 'tomato puree', 'tomato paste', 'cherry tomato',
               'roma tomato', 'green tomato'),
    'Clams': ('clam', 'clams', 'oyster', 'oysters', 'abalone', 'mussels', 'mussel',
               'manila clams'),
    'Pine nuts': ('pine nut', 'pine nuts', 'pine kernels', 'pine kernel'),
    # No concentration is known from a text recipe. These can ONLY be possible.
    'Sulphites': ('sulphites', 'sulfites', 'sulphur dioxide', 'sulfur dioxide',
                 'sodium metabisulphite', 'sodium metabisulfite', 'sodium bisulfite'),
}
POSSIBLE_ALIASES = {
    'Buckwheat': ('soba', 'soba noodle', 'soba noodles'),  # not always 100% buckwheat
    'Sesame': ('sesame oil', 'sesame seed oil'),  # may be highly refined
    'Peanuts': ('peanut oil',),
    'Soy': ('soybean oil', 'soy oil', 'soya oil'),
}

JAPAN_CAA = 'https://www.caa.go.jp/policies/policy/food_labeling/information/pamphlets/assets/food_labeling_cms201_261030_01.pdf'
KOREA_MFDS = 'https://www.mfds.go.kr/eng/wpge/m_14/contents.do'
INDONESIA_BPOM = 'https://rumahsiripo.pom.go.id/peraturan/label/contoh-label-pangan-olahan/'
INDIA_FSSAI = 'https://www.fssai.gov.in/food-law/regulations/amendments/labelling-display'
MALAYSIA_MOH = 'https://hq.moh.gov.my/fsq/peraturanperaturan-makanan-1985'
MYANMAR_FDA = 'https://www.myanmartradeportal.gov.mm/uploads/legals/2022/7/Directive%20No.%288-2022%29%20for%20labelling%20of%20prepackaged%20food.pdf'
VIETNAM_NIFC = 'https://nifc.gov.vn/xac-dinh-nguyen-nhan-ngo-doc/cac-chat-gay-di-ung-trong-thuc-pham-post2180.html'
THAILAND_FDA = 'https://food.fda.moph.go.th/press-release/law474-no2'
TURKIYE_MAF = 'https://www.tarimorman.gov.tr/Konular/Gida-Ve-Yem-Hizmetleri/Gida-Hizmetleri/Kodeks'
SAUDI_SFDA = 'https://www.sfda.gov.sa/en/news/86592'
PHILIPPINES_FDA = 'https://www.fda.gov.ph/administrative-order-no-2014-0030-a/'
CHINA_NHC = 'https://tradelinephilippines.dti.gov.ph/iw/web/tradeline-portal/home/-/asset_publisher/QAWRsyedkcbt/content/people-s-republic-of-china-prepackaged-food-labeling-standards'
CAMBODIA_CCF = 'https://cambodiantr.gov.kh/en/document/?title=prakas-no-1045-isc-cs001-2000-labeling-of-food-product'

# Documentation status is different from claiming a law is comprehensive or
# legally applicable. 'reference_only' countries deliberately generate NO rules.
COUNTRY_SPECS = {
    'CAMBODIA': dict(authority='Cambodia (CCF / CS 001-2000)', source_url=CAMBODIA_CCF,
        coverage='reference_only', notes='General prepackaged-food labeling reference; a current detailed national allergen list was not verified.'),
    'CHINA': dict(authority='China (NHC / SAMR)', source_url=CHINA_NHC,
        coverage='reference_only', notes='GB 7718-2025 allergen requirements begin March 16, 2027; not treated as in force in November 2026.'),
    'INDIA': dict(authority='India (FSSAI)', source_url=INDIA_FSSAI,
        coverage='specified', notes='Food Safety and Standards (Labelling and Display) Regulations, 2020; exemptions/thresholds may apply.',
        categories={'Cereals containing gluten':'required', 'Crustaceans':'required', 'Egg':'required',
                    'Fish':'required', 'Milk':'required', 'Peanuts':'required', 'Tree nuts':'required',
                    'Soy':'required', 'Sulphites':'conditional'}),
    'INDONESIA': dict(authority='Indonesia (BPOM)', source_url=INDONESIA_BPOM,
        coverage='specified', notes='BPOM processed-food labeling; sulphites depend on residual amount.',
        categories={'Cereals containing gluten':'required', 'Egg':'required', 'Fish':'required',
                    'Crustaceans':'required', 'Molluscs':'required', 'Peanuts':'required',
                    'Soy':'required', 'Milk':'required', 'Tree nuts':'required', 'Sulphites':'conditional'}),
    'JAPAN': dict(authority='Japan (CAA)', source_url=JAPAN_CAA,
        coverage='specified', notes='CAA October 2026: 9 mandatory and 20 recommended categories; transitional provisions may apply to older packaged goods.',
        categories={**{x:'mandatory' for x in ('Shrimp','Crab','Cashew','Walnut','Wheat',
                                             'Buckwheat','Egg','Milk','Peanuts')},
                    **{x:'recommended' for x in ('Almond','Abalone','Squid','Salmon roe',
                        'Orange','Kiwi','Beef','Sesame','Salmon','Mackerel','Soy','Chicken',
                        'Banana','Pistachio','Pork','Macadamia nuts','Peach',
                        'Japanese yam','Apple','Gelatin')}}),
    'MALAYSIA': dict(authority='Malaysia (MOH)', source_url=MALAYSIA_MOH,
        coverage='specified', notes='Food Regulations 1985 (reg. 11): specified hypersensitivity categories include gluten cereals, legumes/peanut/soy, fish, milk, egg. Do not infer a comprehensive tree-nut rule.',
        categories={x:'required' for x in ('Cereals containing gluten','Peanuts','Soy','Fish','Milk','Egg')}),
    'MYANMAR': dict(authority='Myanmar (FDA)', source_url=MYANMAR_FDA,
        coverage='specified', notes='Directive 8/2022, clause 7(e): eight food groups; sulphites require >=10 mg/kg.',
        categories={**{x:'required' for x in ('Cereals containing gluten','Crustaceans','Egg','Fish',
                              'Peanuts','Soy','Milk','Tree nuts')}, 'Sulphites':'conditional'}),
    'PHILIPPINES': dict(authority='Philippines (FDA)', source_url=PHILIPPINES_FDA,
        coverage='reference_only', notes='AO 2014-0030-A: applicable allergen information on prepackaged labels; a complete distinct national list was not independently verified.'),
    'SAUDI ARABIA': dict(authority='Saudi Arabia (SFDA)', source_url=SAUDI_SFDA,
        coverage='specified', notes='SFDA official food-service allergen disclosure reference; not an automatic rule for every packaged product.',
        categories={x:'required' for x in ('Cereals containing gluten','Crustaceans','Egg',
            'Fish','Peanuts','Tree nuts','Soy','Milk','Celery','Mustard','Sesame',
            'Lupin','Molluscs','Sulphites')}),
    'SOUTH KOREA': dict(authority='South Korea (MFDS)', source_url=KOREA_MFDS,
        coverage='specified', notes='MFDS food labeling list; sulphurous acid only when added and final SO2 >=10 mg/kg.',
        categories={**{x:'required' for x in ('Egg','Milk','Buckwheat','Peanuts','Soy',
                'Wheat','Mackerel','Crab','Shrimp','Pork','Peach','Tomato','Walnut',
                'Chicken','Beef','Squid','Clams','Pine nuts')}, 'Sulphites':'conditional'}),
    'THAILAND': dict(authority='Thailand (Thai FDA)', source_url=THAILAND_FDA,
        coverage='specified', notes='Notification 450 as amended by 474, effective Oct 31, 2026; sesame added. Sulphites and lactose have separate warnings, not these 10 groups.',
        categories={x:'required' for x in ('Cereals containing gluten','Crustaceans',
             'Egg','Fish','Peanuts','Soy','Milk','Tree nuts','Molluscs','Sesame')}),
    'TURKIYE': dict(authority='Türkiye (Ministry of Agriculture and Forestry)', source_url=TURKIYE_MAF,
        coverage='specified', notes='Turkish Food Codex: 14 allergen groups, broadly aligned with EU Annex II.',
        categories={x:'required' for x in ('Cereals containing gluten','Crustaceans','Egg',
            'Fish','Peanuts','Soy','Milk','Tree nuts','Celery','Mustard','Sesame',
            'Sulphites','Lupin','Molluscs')}),
    'VIETNAM': dict(authority='Vietnam (National Institute for Food Control)', source_url=VIETNAM_NIFC,
        coverage='specified', notes='NIFC explanation of Circular 34/2014 allergen listing; confirm applicability of current labeling instruments for legal use.',
        categories={**{x:'required' for x in ('Cereals containing gluten','Crustaceans',
            'Egg','Fish','Peanuts','Soy','Milk','Tree nuts')}, 'Sulphites':'conditional'}),
}

# Strictly limited to the 13 recipe-country keys in the dataset.
assert len(COUNTRY_SPECS) == 13
assert {cat for spec in COUNTRY_SPECS.values() for cat in spec.get('categories', ())} <= set(ALIASES)

_LOOKUP = defaultdict(list)
for code, spec in COUNTRY_SPECS.items():
    for category, label_status in spec.get('categories', {}).items():
        for name in ALIASES[category]:
            state = 'possible' if (label_status == 'conditional' or category == 'Sulphites') else 'identified'
            _LOOKUP[normalize(name)].append((code, category, state, label_status))
        for name in POSSIBLE_ALIASES.get(category, ()):
            _LOOKUP[normalize(name)].append((code, category, 'possible', label_status))


def regional_market_catalog():
    """Summary for all 13 countries. No source implies no guarantee of absence."""
    return [dict(country=country, jurisdiction=spec['authority'], source_url=spec['source_url'],
                 coverage=spec['coverage'], notes=spec['notes'],
                 category_count=len(spec.get('categories', {})))
            for country, spec in COUNTRY_SPECS.items()]


def regional_allergen_alerts(recipe, market=None):
    """Named-ingredient reference flags, optionally filtered by sales market.

    No guesses from culinary origin, substrings, missing brands, cross-contact or
    sulphite concentrations. Returns separate informational labels only.
    """
    country = str(market or '').strip().upper()
    if country and country not in COUNTRY_SPECS:
        return []
    groups = {}
    for ingredient in ingredient_names(recipe):
        for code, name, state, label_status in _LOOKUP.get(normalize(ingredient), ()):
            if country and country != code:
                continue
            spec = COUNTRY_SPECS[code]
            key = (code, name, state)
            group = groups.setdefault(key, {
                'country': code, 'name': name, 'classification': state,
                'jurisdiction': spec['authority'], 'label_status': label_status,
                'source_url': spec['source_url'], 'from': [],
                'type': 'regional_labeling_reference',
            })
            if ingredient not in group['from']:
                group['from'].append(ingredient)
    return sorted(groups.values(), key=lambda x: (x['country'], x['name'], x['classification']))


REGIONAL_DISCLAIMER = (
    'Market-specific labeling references only. These are named ingredient matches, '
    'not certified product-label declarations, food allergy diagnoses, or a guarantee '
    'of ingredient safety. The country where a recipe originated does NOT determine '
    'the law governing food sold in a market. Check current market requirements, '
    'the packaged product label, ingredient substitutions and cross-contact. '
    'Sulphite concentration and manufacturer exemptions cannot be determined from a recipe.'
)
