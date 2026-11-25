"""Offline equipment panel candidates; affix and property namespaces differ."""
from collections import Counter
from fractions import Fraction as F
from math import ceil
from .panel_ledger import index, group
from . import common_key_enhancement_catalog as ck
AFFIX_MAP = {1:(2,'flat','Pow'),2:(3,'flat','MaxHp'),4:(5,'flat','ShieldArmor'),
             9:(9,'percentage_points','Crit'),10:(10,'percentage_points','CritMult'),
             29:(29,'percentage_points','PowPercent'),30:(30,'percentage_points','MaxHpPercent'),
             33:(32,'percentage_points','ShieldArmorPercent')}

def affix_panel_value(value, unit, *, round_each=False):
    """Exact option value, optionally using the screen-corroborated per-option ceil."""
    if type(value) is not int or value < 0 or unit not in ('flat','percentage_points'):
        raise ValueError('invalid affix value or unit')
    v=F(value,1000)
    if round_each:
        scale=1 if unit=='flat' else 10
        return F(ceil(v*scale),scale)
    return v


def panel_parts(components, *, round_each=False):
    """Return partial contributions by PropertyData field, and explicit blockers."""
    totals = Counter(); blockers=set(); base=Counter(); amplified=Counter()
    for c in components:
        if c.source not in ('part_base','part_amplified_candidate'): continue
        for code,value in c.values:
            if code not in AFFIX_MAP:
                blockers.add('affix_code_unverified'); continue
            field,unit,_=AFFIX_MAP[code]
            if c.namespace != 'affix_'+unit+'_milli' or type(value) is not int or value<0:
                blockers.add('affix_unit_mismatch'); continue
            if c.source=='part_base':base[(c.reference,code)]+=1
            else:
                amplified[(c.reference,code)]+=1
                totals[field]+=affix_panel_value(value,unit,round_each=round_each)
    if base!=amplified:
        blockers.add('affix_amplification_missing')
    return dict(totals), blockers


def key_stage_scenarios(row, *, linear=False):
    """Compare union/replace with additive. Linear mode uses stage × 0.5 pp;\n    endpoints 1 and 10 observed, intermediate stages remain a preview-based candidate.\n    """
    ref=ck.base_properties(int(row['key_id']))
    if ref is None or row['key_type']!='2':return None
    base={x.field_code:x.value_units for x in ref}
    n=int(row['evolution_option_count_candidate'] or 0)
    if not n:return None
    extra={}
    for i in range(1,n+1):
        code=int(row[f'evolution_stat_code_{i}']);stage=int(row[f'evolution_stage_candidate_{i}'])
        if code not in AFFIX_MAP or AFFIX_MAP[code][1]!='percentage_points' or (not 1<=stage<=10 if linear else stage!=10):return None
        field=AFFIX_MAP[code][0]
        if field in extra:return None
        extra[field]=stage*5 # raw units: 0.5 pp per stage; candidate
    union=base|extra
    additive={k:base.get(k,0)+extra.get(k,0) for k in base.keys()|extra.keys()}
    return {'base':base,'max_stage_overlay_candidate':union,'additive_candidate':additive,
            'overlap':sorted(base.keys()&extra.keys()),'status':'unverified_application_candidate'}


def key_max_stage_scenarios(row):
    return key_stage_scenarios(row)


def equipped_key_scenarios(rows):
    """Join actual equipped common-key UIDs before combining hypotheses.
    Do not collapse duplicate models or count an unowned model's definition.
    """
    keys=index(rows['common_keys.csv'],'uid'); slots=group(rows['doll_key_values.csv'],'doll_id')
    out={}
    for d in rows['dolls.csv']:
        did=int(d['doll_id']); base=Counter(); overlay=Counter(); additive=Counter(); blockers=set(); n=0; seen=set()
        for slot in slots[did]:
            if slot['kind']!='common_uids' or slot['value'] in ('','0'):continue
            uid=int(slot['value'])
            if uid in seen:raise ValueError('duplicate equipped common-key UID')
            seen.add(uid); r=keys.get(uid)
            if r is None or r['key_type']!='2':
                blockers.add('key_type_or_reference_unavailable');continue
            refs=ck.base_properties(int(r['key_id']))
            if refs is None:
                blockers.add('key_base_reference_unavailable');continue
            b={x.field_code:x.value_units for x in refs};base.update(b)
            if int(r['evolution_option_count_candidate'] or 0):
                k=key_stage_scenarios(r,linear=True)
                if k is None:
                    blockers.add('key_stage_unsupported');continue
                n+=1;overlay.update(k['max_stage_overlay_candidate']);additive.update(k['additive_candidate'])
            elif r['evolution_observation_status']=='field_absent':
                # Candidate: no extra enhancement; never assert an absent field is decoded zero.
                overlay.update(b);additive.update(b)
            else:
                blockers.add('key_enhancement_state_unavailable')
        out[did]={'base_raw':dict(base),'overlay_raw_candidate':None if blockers else dict(overlay),
                  'additive_raw_candidate':None if blockers else dict(additive),'enhanced_keys':n,
                  'blockers':sorted(blockers),'status':'candidate'}
    return out

