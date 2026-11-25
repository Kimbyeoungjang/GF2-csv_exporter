"""Read-only rank-specific static skill references from an account bundle.

GunGradeData field 11 is a list of skill IDs, not the full runtime skill set.
Do not substitute configured tactics or helix skills for that list, or apply
rank coefficients to panel stats based on this reference.
"""
from collections import Counter, defaultdict
from .reference_data import lru_cache
from types import MappingProxyType
from .doll_growth_catalog import _rows, rank_records
from .weapon_property_catalog import break_reference
from .bundle_import import read_bundle


@lru_cache(maxsize=1)
def grade_skill_catalog():
    known = {int(r['skill_id']) for r in _rows('battle_skill_ids.csv')}
    ranks = rank_records()
    out = defaultdict(dict)
    for r in _rows('doll_grade_current_skill_reference.csv'):
        did, rank, record, pos, sid = (int(r[k]) for k in ('doll_id','rank','grade_record_id','position','skill_id'))
        if ranks.get((did,rank)) != record or sid not in known or pos < 1 or pos in out[did,rank]:
            raise ValueError('invalid rank skill reference')
        out[did,rank][pos] = sid
    if not ranks or set(out) != set(ranks):
        raise ValueError('incomplete rank skill catalog')
    result = {}
    for k,v in out.items():
        if set(v) != set(range(1,len(v)+1)) or len(set(v.values())) != len(v):
            raise ValueError('invalid rank skill sequence')
        result[k] = tuple(v[i] for i in range(1,len(v)+1))
    return MappingProxyType(result)


@lru_cache(maxsize=1)
def grade_skill_parents():
    known = {int(r['skill_id']) for r in _rows('battle_skill_ids.csv')}
    targets = {sid for ids in grade_skill_catalog().values() for sid in ids}
    out = {}
    for row in _rows('doll_grade_skill_parent_reference.csv'):
        sid, parent = (int(row[k]) for k in ('skill_id','field10_parent_skill_id'))
        if sid in out or sid not in known or parent not in targets or sid == parent:
            raise ValueError('invalid skill field10 reference')
        out[sid] = parent
    if not out:
        raise ValueError('skill parent catalog unavailable')
    return MappingProxyType(out)


@lru_cache(maxsize=1)
def grade_key_skills():
    ranks = rank_records()
    known = {int(r['skill_id']) for r in _rows('battle_skill_ids.csv')}
    owners = {int(r['key_id']): int(r['doll_id']) for r in _rows('formation_talent_key_catalog.csv') if r['doll_id']}
    out = defaultdict(dict)
    for r in _rows('doll_grade_key_skill_reference.csv'):
        did, rank, record, key, sid = (int(r[k]) for k in ('doll_id','rank','grade_record_id','key_id','skill_id'))
        if ranks.get((did,rank)) != record or owners.get(key) != did or sid not in known or sid in out[did,rank]:
            raise ValueError('invalid grade key skill reference')
        out[did,rank][sid] = key
    return MappingProxyType({k:MappingProxyType(v) for k,v in out.items()})


def skill_reference(sid, ids, parents, key_skills, selected_keys):
    # A shared explicit field10 reference is a family match only, not a
    # proof of the current rank variant or of its runtime availability.
    if ids is None:
        return {'status':'rank_unresolved'}
    if sid in ids:
        return {'status':'direct_static_list_match'}
    parent = parents.get(sid)
    if parent in ids:
        return {'status':'one_hop_field10_match','field10_reference':parent}
    if sid in key_skills:
        key = key_skills[sid]
        return {'status':'grade_key_skill_reference_match','key_id_candidate':key,
                'key_selection_observed':key in selected_keys}
    matches = [i for i in ids if parent is not None and parents.get(i) == parent]
    if matches:
        return {'status':'shared_field10_reference_match','field10_reference':parent,
                'current_rank_skill_candidates':matches}
    return {'status':'not_in_current_static_list'}


def doll_grade_skills(doll, *, assume_absent_rank_zero=False):
    did = int(doll['doll_id'])
    raw = doll.get('rank')
    status = doll.get('rank_reference_status')
    grade = grade_skill_catalog()
    rank = None
    basis = 'rank_unresolved'
    if status == 'catalog_consistent' and raw not in ('',None) and (did,int(raw)) in grade:
        rank, basis = int(raw), 'explicit_rank_static_reference'
    elif status == 'field_absent' and raw in ('',None) and assume_absent_rank_zero and (did,0) in grade:
        rank, basis = 0, 'absent_rank_zero_hypothesis'
    ids = grade.get((did,rank))
    return {'rank_candidate':rank,'basis':basis,'static_skill_ids':list(ids) if ids is not None else None}


def weapon_grade_skill(weapon):
    raw = weapon.get('rank')
    ref = None
    if weapon.get('rank_reference_status') == 'within_observed_positive_range' and raw not in ('',None) and int(raw)>0:
        ref = break_reference(int(weapon['item_id']),int(raw))
    return {'skill_id_candidate':ref.skill_id if ref else None,
            'display_level_candidate':ref.display_level if ref else None,
            'basis':'explicit_rank_static_reference' if ref else 'rank_or_reference_unresolved'}


def growth_skill_states(bundle, *, assume_absent_rank_zero=False):
    if not bundle.complete_capture:
        raise ValueError('complete bundle required')
    parents = grade_skill_parents(); key_catalog = grade_key_skills()
    selected = defaultdict(set)
    for r in bundle.rows.get('doll_key_values.csv', ()):
        if r['kind'] in ('fixed_keys','expansion_keys','common_keys','bond_keys') and r['value']:
            selected[int(r['doll_id'])].add(int(r['value']))
    strategies = defaultdict(list)
    for r in bundle.rows['doll_skills.csv']:
        strategies[int(r['doll_id'])].append(r)
    dolls = {}; weapons = {}; counts = Counter()
    for r in bundle.rows['dolls.csv']:
        did = int(r['doll_id'])
        if did in dolls: raise ValueError('duplicate doll')
        state = doll_grade_skills(r,assume_absent_rank_zero=assume_absent_rank_zero)
        ids = state['static_skill_ids']; compared=[]
        for s in strategies[did]:
            sid=int(s['skill_id'])
            evidence=skill_reference(sid,ids,parents,key_catalog.get((did,state['rank_candidate']),{}),selected[did])
            counts[evidence['status']]+=1
            compared.append({'position':int(s['position']),'skill_id':sid,**evidence})
        state['strategy_comparison']=compared
        dolls[did]=state
    for r in bundle.rows['weapons.csv']:
        uid=int(r['uid'])
        if uid in weapons: raise ValueError('duplicate weapon')
        weapons[uid]=weapon_grade_skill(r)
    return {'schema':'gf2-growth-skill-references-2','scope':'static_reference_candidates_not_runtime_skills',
            'dolls':dolls,'weapons':weapons,'summary':{'dolls':len(dolls),'weapons':len(weapons),
            'doll_rank_references':sum(x['static_skill_ids'] is not None for x in dolls.values()),
            'weapon_rank_references':sum(x['skill_id_candidate'] is not None for x in weapons.values()),
            'strategy_comparison':dict(counts)}}


def read_growth_skill_states(bundle_path, *, assume_absent_rank_zero=False):
    return growth_skill_states(read_bundle(bundle_path),assume_absent_rank_zero=assume_absent_rank_zero)
