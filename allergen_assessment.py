"""Allergen-only evaluation and patching: never calls USDA or GPT."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from dish_info_common import ingredient_names, normalize, safe_url
from curated_allergens import FRAMEWORK, DISCLAIMER, FSA

def rules_revision(rules):
    return hashlib.sha256(json.dumps(sorted(rules,key=lambda r:r['normalized_name']),sort_keys=True,default=str).encode()).hexdigest()

def assess(names, rules):
    from dish_health import _allergens
    groups,unknown=_allergens(names,rules)
    details=[]
    lookup={}
    for r in rules:
        if r.get('approved'):
            for a in [r.get('normalized_name',''),*(r.get('aliases') or [])]:
                lookup.setdefault(normalize(a),[]).append(r)
    for name in names:
        found=[g for g in groups if name in g.get('from',[])]
        state=('unknown' if name in unknown else 'identified' if any(g['classification']=='identified' for g in found)
               else 'possible' if any(g['classification']=='possible' for g in found) else 'assessed_none')
        matched=lookup.get(normalize(name),[])
        details.append({'ingredient':name,'classification':state,
                        'allergens':[{'name':g['name'],'classification':g['classification'],'source_url':g['source_url']}
                                     for g in found if g['classification'] in {'identified','possible'}],
                        'rule_names':[r['normalized_name'] for r in matched],
                        'evidence':[e for r in matched for e in r.get('evidence',[]) if safe_url(e.get('url'))],
                        'notes':list(dict.fromkeys(r['assessment_note'] for r in matched if r.get('assessment_note'))),
                        'source_urls':sorted({g['source_url'] for g in found if safe_url(g.get('source_url'))})})
    counts=Counter(d['classification'] for d in details)
    return {'groups':groups,'unknown':unknown,'ingredients':details,'counts':dict(counts),
            'assessed':len(names)-len(unknown),'total':len(names),
            'percent':round(100*(len(names)-len(unknown))/len(names),2) if names else 0,
            'framework':FRAMEWORK,'disclaimer':DISCLAIMER}

def allergen_patch(recipe, cached, rules, revision=None):
    """Only change allergen fields/notes; retain every nutrition field verbatim."""
    if not cached:
        raise ValueError('An existing health record is required; nutrition is not regenerated.')
    assessment=assess(ingredient_names(recipe),rules)
    notes=[]
    for note in deepcopy(cached.get('health_considerations') or []):
        if note.get('type')=='allergen_assessment':
            continue
        if str(note.get('text','')).startswith('Allergen coverage is unknown for:'):
            continue
        if note.get('text')=='Compound ingredient formulations vary. Possible allergens need confirmation from the actual product label.':
            continue
        if note.get('type')=='coverage':
            note['unknown_allergen_ingredients']=assessment['unknown']
        notes.append(note)
    notes.append(dict(assessment,type='allergen_assessment',rules_revision=revision or rules_revision(rules),
                      assessed_at=datetime.now(timezone.utc).isoformat(),
                      text=f"Allergen assessment: {assessment['assessed']} of {assessment['total']} ingredient names ({assessment['percent']}%).",
                      source_url=FSA))
    if assessment['unknown']:
        notes.append({'text':'Allergen coverage is unknown for: '+', '.join(assessment['unknown'])+'.'})
    if any(g['classification']=='possible' for g in assessment['groups']):
        notes.append({'text':'Compound ingredient formulations vary. Possible allergens need confirmation from the actual product label.'})
    sources=deepcopy(cached.get('sources') or [])
    present={s.get('url') for s in sources}
    for url in sorted({FSA,*[g['source_url'] for g in assessment['groups']]}):
        if safe_url(url) and url not in present:
            sources.append({'title':'Allergen assessment reference','url':url})
    return {'potential_allergens':assessment['groups'],'health_considerations':notes,'sources':sources,
            'verification_status':'verified' if assessment['assessed'] or cached.get('matched_ingredient_count') else cached.get('verification_status','unavailable')}
