"""Opt-in offline V35 enrichment. No capture access, no changes to exported CSV.

Set selection is a screen-corroborated candidate, not a combat trigger.
Copy eligibility and cost are static references, never observed copy history.
Accept only a validated complete bundle (use read_semantics for a ZIP path).
"""
from collections import Counter, defaultdict
from .reference_data import lru_cache
from types import MappingProxyType
from .attachment_affixes import _rows
from .bundle_import import read_bundle
from .equipment_graph_audit import audit_equipment_graph
from .common_key_enhancement_catalog import copy_catalog
from .key_catalog import catalog as key_catalog


@lru_cache(maxsize=1)
def set_catalog():
    effects = {}
    for r in _rows('attachment_set_effect_reference.csv'):
        e, t, s = (int(r[k]) for k in ('effect_id', 'required_parts', 'battle_skill_id'))
        if min(e,t,s)<=0 or e in effects or not r['name_ko']:
            raise ValueError('invalid set effect catalog')
        effects[e]=(t,s,r['name_ko'])
    models={}
    groups={}
    for r in _rows('attachment_set_model_reference.csv'):
        m=int(r['part_model_id']); g=int(r['plan_group_id']) if r['plan_group_id'] else None
        ids=tuple(int(x) for x in r['allowed_effect_ids'].split(';')) if r['allowed_effect_ids'] else ()
        if (m<=0 or m in models or (g is not None and g<=0) or bool(ids)!=(g is not None)
                or len(set(ids))!=len(ids) or not set(ids)<=effects.keys()):
            raise ValueError('invalid set model catalog')
        if g is not None and groups.setdefault(g,ids)!=ids:
            raise ValueError('inconsistent set group')
        models[m]=(g,ids)
    if not models or not effects or set(effects)!={e for _,ids in models.values() for e in ids}:
        raise ValueError('incomplete set catalog')
    return MappingProxyType(models),MappingProxyType(effects)


def set_selection(model,raw,models):
    if model not in models:return None,'unknown_model'
    g,allowed=models[model]
    if raw is None:return None,'no_static_set_plan' if g is None else 'field_absent_with_set_plan'
    if type(raw) is not int or raw not in allowed:return None,'unmapped_or_conflicting_field4'
    return raw,'screen_corroborated_set_id_candidate'


def attachment_sets(bundle, *, references=None, graph=None):
    if not bundle.complete_capture:raise ValueError('complete bundle required')
    models,effects=references if references is not None else set_catalog()
    g=(audit_equipment_graph(bundle) if graph is None else graph)['current_weapon_parts']; c=g['counts']
    if (not g['source_ready'] or not g['target_ready'] or c['positive_claims']!=c.get('matching_owner',0)
            or any(c.get(k,0) for k in ('repeated_same_owner_positions','reverse_without_matching_claim','multiple_owner_items'))):
        raise ValueError('ambiguous equipped part graph')
    parts={}; weapons={}
    for name,dest in [('attachments.csv',parts),('weapons.csv',weapons)]:
        for r in bundle.rows[name]:
            uid=int(r['uid'])
            if uid<=0 or uid in dest:raise ValueError('invalid inventory identity')
            dest[uid]=r
    part_rows=[]; selected=defaultdict(Counter); unknown=Counter(); status_counts=Counter()
    for uid,r in sorted(parts.items()):
        m=int(r['part_id']); w=int(r['weapon_uid'] or 0)
        s=r['upgrade_reference_status']; v=r['upgrade_level_candidate']
        if s=='catalog_consistent_candidate' and v:
            e,status=set_selection(m,int(v),models)
        elif s=='field_absent' and not v:
            e,status=set_selection(m,None,models)
        else:e,status=None,'legacy_field4_not_preserved'
        status_counts[status]+=1
        if w:
            if w not in weapons:raise ValueError('missing weapon')
            if e is not None:selected[w][e]+=1
            elif status!='no_static_set_plan':unknown[w]+=1
        part_rows.append(dict(uid=uid,part_model_id=m,weapon_uid=w,set_effect_id_candidate=e,
                              set_name_ko=effects[e][2] if e is not None else '',status=status))
    sets=[]; states=[]; active=Counter()
    for w in sorted(weapons):
        met=[]; incomplete=unknown[w]>0
        for e,n in sorted(selected[w].items()):
            t,_,name=effects[e]
            if n>=t:met.append(e);active[e]+=1
            sets.append(dict(weapon_uid=w,set_effect_id_candidate=e,set_name_ko=name,part_count=n,required_parts=t,
                threshold_status='threshold_met_candidate' if n>=t else 'incomplete_possible' if incomplete else 'below_threshold_candidate',unknown_parts=unknown[w]))
        states.append(dict(weapon_uid=w,known_set_parts=sum(selected[w].values()),unknown_parts=unknown[w],
            threshold_met_effect_ids=';'.join(map(str,met)),status='incomplete' if incomplete else 'candidate_complete'))
    return {'parts':part_rows,'weapon_sets':sets,'weapon_states':states,'summary':{
        'parts':len(parts),'weapons':len(weapons),'equipped_parts':c['positive_claims'],'part_status_counts':dict(status_counts),
        'weapons_without_unknown_parts':sum(r['status']=='candidate_complete' for r in states),
        'set_threshold_met_count':sum(active.values()),'set_threshold_met_by_effect':dict(sorted(active.items()))}}


def common_key_copy_models(bundle):
    """Model-level inventory and static costs. Never infer copies from multiplicity."""
    if not bundle.complete_capture:raise ValueError('complete bundle required')
    copies=copy_catalog(); types=key_catalog()
    if not copies or not types:raise ValueError('missing key reference catalog')
    counts=Counter(); unknown=0; seen=set()
    for r in bundle.rows['common_keys.csv']:
        uid=int(r['uid']); model=int(r['key_id'])
        if uid<=0 or uid in seen:raise ValueError('duplicate or invalid key UID')
        seen.add(uid)
        ref=types.get(model)
        if ref is None:unknown+=1;continue
        if ref[0]!=2:continue
        counts[model]+=1
    out=[]
    for m,n in sorted(counts.items()):
        ref=copies.get(m)
        out.append({'key_id':m,'observed_quantity':n,'static_copy_reference_present':ref is not None,
            'static_copy_limit':ref.limit if ref else None,'static_cost_item_id':ref.item_id if ref else None,
            'static_cost_quantities':list(ref.costs) if ref else None,
            'actual_copy_count':None,'remaining_copy_count':None,'copy_history_status':'unobserved'})
    return {'models':out,'summary':{'common_key_models':len(out),'common_key_records':sum(counts.values()),
        'owned_copy_reference_models':sum(r['static_copy_reference_present'] for r in out),
        'owned_copy_reference_models_with_multiple_instances':sum(r['static_copy_reference_present'] and r['observed_quantity']>1 for r in out),
        'models_without_copy_reference_with_multiple_instances':sum(not r['static_copy_reference_present'] and r['observed_quantity']>1 for r in out),
        'unknown_type_records':unknown}}


def affinity_oath_lower_bound(bundle):
    """A user-confirmed prerequisite: affinity >5 entails a vow.

    Lower levels never establish that a vow has not been made. No packet flag
    or date is exposed here; V35 does not retain these validated semantics.
    """
    if not bundle.complete_capture:raise ValueError('complete bundle required')
    out=[]; seen=set()
    dolls={int(r['doll_id']) for r in bundle.rows['dolls.csv']}
    for r in bundle.rows['doll_progress.csv']:
        did=int(r['doll_id']); level=int(r['affinity_level']) if r['affinity_level'] else None
        if did not in dolls or did in seen:raise ValueError('unexpected doll progress row')
        seen.add(did)
        yes=level is not None and 6<=level<=9
        out.append({'doll_id':did,'vowed':True if yes else None,
                    'status':'affinity_prerequisite' if yes else 'not_determined'})
    if seen!=dolls:raise ValueError('incomplete doll progress')
    return {'dolls':out,'summary':{'vowed_by_prerequisite':sum(r['vowed'] is True for r in out),
                  'undetermined':sum(r['vowed'] is None for r in out)}}


def read_semantics(path, *, equipment_state_csv=None):
    """Read-only library entry point for GF2Tools; caller owns private results."""
    b=read_bundle(path)
    equipment=None
    if b.embedded_states:
        if equipment_state_csv is not None:
            raise ValueError('integrated backup already contains equipment state')
        equipment_state_csv = b.embedded_states['equipment_state']
    if equipment_state_csv is not None:
        from .equipment_state import read_equipment_state
        equipment=read_equipment_state(equipment_state_csv,path,b)
    result=_semantics_from_bundle(b,equipment)
    from .bundle_import import bound_archive_digest
    bound_archive_digest(path,b)
    return result


def _semantics_from_bundle(b,equipment=None):
    result = {'schema':'v35-offline-semantics-1','attachment_sets':attachment_sets(b),
            'common_key_copy_models':common_key_copy_models(b),'affinity_oath_lower_bound':affinity_oath_lower_bound(b)}
    from .calibration_completion import calibration_completion
    result['calibration_completion'] = calibration_completion(b)
    if equipment is not None:
        result['equipment_state']=equipment
    return result
