"""Supabase-only, resumable allergen pass preserving nutrition/background data."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid

from allergen_assessment import assess, allergen_patch, rules_revision
from curated_allergens import plan_dataset_rules, FRAMEWORK, DISCLAIMER
from dish_info_common import ingredient_names, ingredient_hash, normalize
from dish_information_store import get_store, DishStoreError
from enrichment_batch import SnapshotStore, retry_store, dataset_hash, now
from enrichment_report import build_report, render_markdown

def fingerprint(rows):
    return hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()

def nutrition_fields(row):
    """Content invariant independent of allergen notes, citations and timestamps."""
    out={k:deepcopy(row.get(k)) for k in ('potential_benefits','nutrition_status','matched_ingredient_count','ingredient_count','ingredient_hash','data_version')}
    out['nutrition_notes']=[]
    for note in row.get('health_considerations',[]):
        if note.get('ingredient') or note.get('type')=='coverage':
            note=deepcopy(note);note.pop('unknown_allergen_ingredients',None)
            out['nutrition_notes'].append(note)
    return out

def report(recipes,rules):
    counts=Counter(normalize(i) for r in recipes for i in set(ingredient_names(r)))
    assessment=assess(sorted(counts),rules)
    unknown=[dict(d,recipe_count=counts[d['ingredient']]) for d in assessment['ingredients'] if d['classification']=='unknown']
    dishes=[]
    for recipe in recipes:
        a=assess(ingredient_names(recipe),rules)
        dishes.append({'recipe_id':recipe['recipe_id'],'dish':recipe['title'],'country':recipe['country'],
                       'assessed':a['assessed'],'total':a['total'],'percent':a['percent'],
                       'unknown':a['unknown'],'counts':a['counts']})
    return {'framework':FRAMEWORK,'measured_at':now(),'total_ingredients':len(counts),
            'assessed_ingredients':assessment['assessed'],'assessment_percent':assessment['percent'],
            'exclusive_counts':assessment['counts'],
            'identified_prevalence':sum(any(a['classification']=='identified' for a in d['allergens']) for d in assessment['ingredients']),
            'possible_prevalence':sum(any(a['classification']=='possible' for a in d['allergens']) for d in assessment['ingredients']),
            'unknown_ingredients':sorted(unknown,key=lambda d:(-d['recipe_count'],d['ingredient'])),
            'recipes':dishes,'fully_assessed_recipes':sum(d['assessed']==d['total'] and d['total']>0 for d in dishes),
            'disclaimer':DISCLAIMER}

def markdown(r):
    total=r['total_ingredients']; c=r['exclusive_counts']
    lines=['# CuiSync allergen mapping coverage','',f"Measured: {r['measured_at']}. Framework: **UK FSA 14 groups**, with FDA nine-group references.",'',DISCLAIMER,'',
           '| Category | Ingredients | Percent |','| --- | ---: | ---: |']
    for label,n in [('Total',total),('Assessed',r['assessed_ingredients']),('Identified (at least one)',c.get('identified',0)),
                    ('Possible only',c.get('possible',0)),('Assessed; no major allergen identified',c.get('assessed_none',0)),('Unknown',c.get('unknown',0))]:
        lines.append(f'| {label} | {n} | {n/total*100:.2f}% |')
    lines += ['',f"Possible-allergen prevalence including ingredients that also have identified allergens: **{r['possible_prevalence']}** ({r['possible_prevalence']/total*100:.2f}%). This overlaps identified prevalence; the four classification rows above are mutually exclusive.",
              '',f"Fully assessed recipe ingredient lists: **{r['fully_assessed_recipes']}/{len(r['recipes'])}** ({100*r['fully_assessed_recipes']/len(r['recipes']):.2f}%). Assessment completeness does not establish product formulation completeness or allergy safety.",
              '', '## All remaining unknown ingredients','', 'Every unknown below requires a full food identity or actual product/recipe ingredient list. Ambiguous fragments and non-food objects are retained in the denominator; they are not silently discarded.',
              '', '| Ingredient | Recipes using it |','| --- | ---: |']
    lines += [f"| {d['ingredient']} | {d['recipe_count']} |" for d in r['unknown_ingredients']]
    lines += ['', '## Assessment coverage for every recipe','', '| ID | Country | Dish | Assessed / ingredients | Coverage | Unknown |','| --- | --- | --- | ---: | ---: | --- |']
    lines += [f"| {d['recipe_id']} | {d['country']} | {d['dish']} | {d['assessed']} / {d['total']} | {d['percent']:.2f}% | {', '.join(d['unknown']) or 'None unresolved in the listed names'} |" for d in sorted(r['recipes'],key=lambda d:(d['country'],d['dish']))]
    lines += ['', '## References and limits','',
              '[FSA 14 allergens](https://www.food.gov.uk/business-guidance/allergen-guidance-for-food-businesses), [FDA nine major allergens](https://www.fda.gov/food/buy-store-serve-safe-food/food-allergies-what-you-need-know), [Food Standards Scotland food/derivative examples](https://www.foodstandards.gov.scot/business-guidance/running-a-food-business/managing-allergens), [Cambridge University Hospitals ingredient guidance](https://www.cuh.nhs.uk/patient-information/cows-milk-egg-wheat-and-soya-mews-free-diet/). Individual rules retain additional evidence URLs, scope notes, exact aliases and timestamps in Supabase.',
              '', 'Wheat is retained as a distinct display label within the FSA cereals-containing-gluten group. Sulphites are possible when product concentration is unknown; UK declaration uses more than 10 mg/kg or litre. Other allergies remain possible outside these regulated groups. Plain-food assessments apply to the stated identity only, and never silently extend to a sauce, blend, branded product or altered recipe.',
              '', 'No GPT or USDA request is used in this allergen-only pass. Existing valid/withdrawn rules are preserved. Nutrition record contents and background records are checked before and after reprocessing.']
    return '\n'.join(lines)+'\n'

def run(recipes,store,resume=None,workers=4):
    snapshot=SnapshotStore(store)
    old_rules=snapshot.list('ingredient_allergens')
    ingredients={normalize(i) for r in recipes for i in ingredient_names(r)}
    run=retry_store(store.get,'dish_enrichment_runs',{'id':resume}) if resume else None
    if resume and (not run or run['dataset_hash']!=dataset_hash(recipes) or run['options'].get('mode')!='allergens_only'):
        raise ValueError('Resume run must be an allergen run for this dataset.')
    if not run:
        run={'id':str(uuid.uuid4()),'dataset_hash':dataset_hash(recipes),'started_at':now(),'options':{'mode':'allergens_only'},'coverage_before':report(recipes,old_rules)}
    run.update(status='running',finished_at=None)
    run=retry_store(store.save_run,run)
    print('RUN_ID='+run['id'],flush=True)
    nutrition_before=snapshot.list('ingredient_nutrition'); backgrounds_before=snapshot.list('dish_backgrounds')
    health_before={(r['recipe_id'],r['ingredient_hash']):nutrition_fields(r) for r in snapshot.list('dish_health_info')}
    try:
        planned=plan_dataset_rules(ingredients,old_rules)
        for start in range(0,len(planned),100):
            # Ignore duplicates atomically: an admin change/withdrawal wins.
            chunk=[dict(r,updated_at=now()) for r in planned[start:start+100]]
            def insert():
                try:store.client.table('ingredient_allergens').upsert(chunk,on_conflict='normalized_name',ignore_duplicates=True).execute()
                except Exception:raise DishStoreError('Allergen rule insertion failed.') from None
            retry_store(insert)
        rules=retry_store(store.list,'ingredient_allergens')
        byname={r['normalized_name']:r for r in rules}
        assert all(byname[r['normalized_name']]==r for r in old_rules),'Existing rule changed'
        revision=rules_revision(rules)
        print(f'RULES existing={len(old_rules)} added={len(rules)-len(old_rules)}',flush=True)
        statuses=Counter()
        def process(recipe):
            filters={'recipe_id':recipe['recipe_id'],'ingredient_hash':ingredient_hash(recipe)}
            for attempt in range(3):
                row=retry_store(store.get,'dish_health_info',filters)
                if not row:raise DishStoreError('Recipe health record is missing; no nutrition fallback permitted.')
                previous=next((n for n in row.get('health_considerations',[]) if n.get('type')=='allergen_assessment'),{})
                if previous.get('rules_revision')==revision:
                    return 'cached'
                patch=allergen_patch(recipe,row,rules,revision)
                def save():
                    try:return store.client.table('dish_health_info').update(dict(patch,updated_at=now())).eq('id',row['id']).eq('updated_at',row['updated_at']).execute().data
                    except Exception:raise DishStoreError('Allergen health update failed.') from None
                saved=retry_store(save)
                if saved:
                    assert nutrition_fields(saved[0])==nutrition_fields(row),'Nutrition content changed'
                    return 'updated'
            raise DishStoreError('Health record changed concurrently; resume later.')
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures={pool.submit(process,r):r for r in recipes}
            for i,future in enumerate(as_completed(futures),1):
                recipe=futures[future]
                outcome=future.result();statuses[outcome]+=1
                if i%100==0 or i==len(recipes):
                    run['options']=dict(run['options'],processed=i,total=len(recipes),rules_revision=revision,statuses=dict(statuses))
                    retry_store(store.save_run,run)
                    print(f'RECIPES {i}/{len(recipes)} {dict(statuses)}',flush=True)
        after_snapshot=SnapshotStore(store)
        assert fingerprint(nutrition_before)==fingerprint(after_snapshot.list('ingredient_nutrition')),'Nutrition rows changed'
        assert fingerprint(backgrounds_before)==fingerprint(after_snapshot.list('dish_backgrounds')),'Background rows changed'
        for row in after_snapshot.list('dish_health_info'):
            assert nutrition_fields(row)==health_before[(row['recipe_id'],row['ingredient_hash'])]
        result=report(recipes,rules)
        result['preservation_checks']={'nutrition_rows_unchanged':True,'background_rows_unchanged':True,'recipe_nutrition_unchanged':True,'existing_rules_preserved':len(old_rules)}
        result['processing']=dict(statuses)
        result['run_id']=run['id']
        result['rules_revision']=revision
        run.update(status='partial' if result['unknown_ingredients'] else 'complete',finished_at=now(),coverage_after=result)
        retry_store(store.save_run,run)
        Path('ALLERGEN_REPORT.md').write_text(markdown(result),encoding='utf8')
        general=build_report(recipes,after_snapshot)
        Path('ENRICHMENT_REPORT.md').write_text(render_markdown(general)+'\nFull allergen report, every unresolved ingredient and all 900 recipe assessments: [ALLERGEN_REPORT.md](ALLERGEN_REPORT.md).\n',encoding='utf8')
        print(json.dumps({k:result[k] for k in ['run_id','total_ingredients','assessed_ingredients','assessment_percent','exclusive_counts','possible_prevalence','fully_assessed_recipes','preservation_checks','processing']},indent=2),flush=True)
        return result
    except BaseException:
        run.update(status='failed',finished_at=now())
        retry_store(store.save_run,run)
        print('Resume with --resume-run '+run['id'],flush=True)
        raise

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--resume-run');p.add_argument('--workers',type=int,default=4)
    args=p.parse_args()
    recipes=json.loads(Path(__file__).with_name('recipes.json').read_text(encoding='utf8'))
    run(recipes,get_store(),args.resume_run,max(1,min(args.workers,8)))
