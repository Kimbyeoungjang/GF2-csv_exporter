"""Read-only support stage threshold candidates; no individual unit level inference."""
from collections import Counter, defaultdict
from .reference_data import lru_cache
from types import MappingProxyType
from .doll_growth_catalog import _rows
from .progression_reference_catalog import support_progression_catalog
from .support_catalog import group_references
from .bundle_import import read_bundle

@lru_cache(maxsize=1)
def thresholds():
    catalog = support_progression_catalog()
    if catalog is None:
        raise ValueError('support progression catalog unavailable')
    stages, _ = catalog
    counts = {}
    for r in _rows('static_pair_counts.csv'):
        if r['table_name'] != 'SupportSkillGroupData' or r['field_number'] != '4':
            continue
        rid, n = int(r['record_id']), int(r['pair_count'])
        if rid in counts or rid not in stages or n < 0:
            raise ValueError('invalid threshold count')
        counts[rid] = n
    if set(counts) != set(stages):
        raise ValueError('missing threshold counts')
    pairs = defaultdict(dict)
    for r in _rows('static_pair_values.csv'):
        if r['table_name'] != 'SupportSkillGroupData' or r['field_number'] != '4':
            continue
        rid, pos, attr, value = (int(r[k]) for k in ('record_id','position','key_raw','value_raw'))
        if rid not in counts or pos in pairs[rid] or not 1 <= attr <= 6 or value < 0:
            raise ValueError('invalid threshold pair')
        pairs[rid][pos] = (attr, value)
    out = {}
    for rid, n in counts.items():
        if set(pairs[rid]) != set(range(1,n+1)):
            raise ValueError('missing threshold pairs')
        p = tuple(pairs[rid][i] for i in range(1,n+1))
        if len({a for a,_ in p}) != n:
            raise ValueError('duplicate threshold attribute')
        out[rid] = p
    return MappingProxyType(out)


def evaluate_group(group_id, attrs):
    """Return possible stage bounds based only on the declared threshold tests.

    A missing attribute cannot satisfy a nonzero requirement. Unknown stages
    are retained; the next stage is relative to the guaranteed candidate, not
    a claim that a player can activate a skill in the engine.
    """
    catalog = support_progression_catalog()
    if catalog is None or group_id not in catalog[1]:
        raise ValueError('unknown support skill group')
    if any(type(a) is not int or not 1 <= a <= 6 or type(v) is not int or v < 0 for a,v in attrs.items()):
        raise ValueError('invalid support attribute')
    refs = thresholds(); detail = []
    for s in catalog[1][group_id]:
        requirements = []
        for a,v in refs[s.record_id]:
            known = attrs.get(a)
            requirements.append({'attribute_id':a,'required':v,'observed':known,
                                 'shortfall':None if known is None else max(0,v-known)})
        status = ('not_met' if any((r['shortfall'] or 0)>0 for r in requirements) else
                  'unknown' if any(r['shortfall'] is None for r in requirements) else 'met')
        detail.append({'skill_id':s.record_id,'stage':s.stage,'threshold_status':status,'requirements':requirements})
    met = max((x['stage'] for x in detail if x['threshold_status']=='met'),default=None)
    possible = max((x['stage'] for x in detail if x['threshold_status']!='not_met'),default=None)
    return {'group_id':group_id,'highest_met_candidate':met,'highest_possible_candidate':possible,
            'exact_threshold_candidate':met if met==possible else None,
            'next_stage':next((x for x in detail if met is not None and x['stage']==met+1),None),
            'stages':detail}


def support_stage_candidates(bundle):
    required = ('support_units.csv','support_attributes.csv')
    if any(k not in bundle.rows for k in required):
        return {'schema':'gf2-support-stage-candidates-1','status':'snapshot_unavailable','units':[],'counts':{}}
    attrs=defaultdict(dict); seen=set(); invalid=set()
    for r in bundle.rows[required[1]]:
        p,a=int(r['formation_position']),int(r['attribute_id'])
        if (p,a) in seen or not 1<=a<=6: invalid.add(p)
        seen.add((p,a))
        if r['value']!='':
            v=int(r['value'])
            if v<0: invalid.add(p)
            attrs[p][a]=v
    units, skills=group_references(); catalog=support_progression_catalog()
    if not units or not skills or catalog is None: raise ValueError('support catalog unavailable')
    out=[]; counts=Counter(); slots=Counter((r['formation_position'],r['slot_position']) for r in bundle.rows[required[0]])
    for r in bundle.rows[required[0]]:
        if not r['unit_id']: continue
        p,slot,u=int(r['formation_position']),int(r['slot_position']),int(r['unit_id'])
        sid=int(r['skill_id']) if r['skill_id'] else None
        row={'formation_position':p,'slot_position':slot,'unit_id':u,'observed_skill_id':sid,
             'individual_unit_level':None,'thresholds':None,'observed_stage':None}
        if slots[(r['formation_position'],r['slot_position'])]!=1 or p in invalid:
            status='conflicting_input'
        elif r['present']!='1' or u not in units or (sid is not None and skills.get(sid)!=units[u]):
            status='reference_mismatch'
        else:
            t=evaluate_group(units[u],attrs[p]); row['thresholds']=t
            if sid is not None: row['observed_stage']=catalog[0][sid].stage
            v=t['exact_threshold_candidate']; observed=row['observed_stage']
            status=('threshold_indeterminate' if v is None else 'observed_skill_absent' if observed is None else
                    'matches_observed' if v==observed else 'candidate_higher' if v>observed else 'candidate_lower')
        row['status']=status; counts[status]+=1;out.append(row)
    return {'schema':'gf2-support-stage-candidates-1','status':'threshold_candidates_not_engine_validation','units':out,'counts':dict(counts)}


def read_support_stage_candidates(path):
    return support_stage_candidates(read_bundle(path))
