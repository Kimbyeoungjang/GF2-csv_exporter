"""Offline panel component ledger. Candidate definitions, never live combat state."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from .reference_data import files
from .reference_data import lru_cache
import csv
from . import doll_stat_catalog as ds, helix_effect_catalog as he
from . import weapon_property_catalog as wp, common_key_enhancement_catalog as ck
from .equipment_graph_audit import audit_equipment_graph
CORE=(2,3,5)
AFFIX_FIELD_CANDIDATES={1:2,9:9,29:29}
HERE=files('gfl2_packet_csv.static_tables')

@dataclass(frozen=True)
class Component:
    source: str
    namespace: str
    reference: int
    values: tuple[tuple[int,int],...]


def int_or_none(value):
    return None if value in ('',None) else int(value)


def group(rows,key):
    out=defaultdict(list)
    for r in rows: out[int(r[key])].append(r)
    return out


def index(rows,key):
    out=group(rows,key)
    if any(len(v)!=1 for v in out.values()): raise ValueError('duplicate ledger key')
    return {k:v[0] for k,v in out.items()}


def research_refs():
    def read(name,keys,code,value):
        out=defaultdict(dict)
        with (HERE/name).open(encoding='utf-8-sig',newline='') as f:
            for r in csv.DictReader(f):
                k=tuple(int(r[x]) for x in keys); c=int(r[code]); v=int(r[value])
                if c in out[k]: raise ValueError('duplicate property code')
                out[k][c]=v
        return {k:tuple(sorted(v.items())) for k,v in out.items()}
    a=read('RC129_AFFINITY_PROPERTY_REFERENCE_SAFE.csv',('doll_model_id','level_reference','property_id'),'property_field_code','raw_value')
    b=read('RC129_BOND_KEY_PROPERTY_REFERENCE_SAFE.csv',('key_model_id','field15_model_reference','property_id'),'property_field_code','raw_value')
    return a,b


def raw_total(components, codes=CORE):
    if any(c.namespace!='property_raw' for c in components): raise ValueError('incompatible code or unit namespace')
    return {f:sum(v for c in components for k,v in c.values if k==f) for f in codes}


def stage_options(stages, current):
    """Return distinct current-only and cumulative hypotheses, not active effects."""
    if current is None or current not in stages: return None
    if any(i not in stages for i in range(min(stages),current+1)): return None
    return (stages[current], tuple(c for i in range(min(stages),current+1) for c in stages[i]))


def candidate_difference(current,cumulative):
    a,b=raw_total(current),raw_total(cumulative)
    return {k:b[k]-a[k] for k in CORE}


def mask_components(did,mask):
    refs=he.doll_references(did)
    if refs is None or mask is None or not 0<=mask<1<<len(refs): return None
    return tuple(Component('helix_bit_candidate','property_raw',n.property.property_id,n.property.values)
                 for i,n in enumerate(refs) if mask&(1<<i) and n.property is not None)


def ledger(bundle, *, assume_absent_calibration_base=False):
    """Private per-doll ledger in memory only; no final totals or UID output."""
    rows=bundle.rows
    graph=audit_equipment_graph(bundle)
    for name in ('current_doll_weapons','current_weapon_parts','current_doll_keys','current_doll_remoldings'):
        g=graph[name]; c=g['counts']
        if not g['source_ready'] or not g['target_ready'] or c.get('positive_claims',0)!=c.get('matching_owner',0) or any(c.get(k,0) for k in ('repeated_same_owner_positions','multiple_owner_items')):
            raise ValueError('unverified equipment links; ledger withheld')
    a,b=research_refs(); aff=defaultdict(dict); bonds={}
    for (did,l,pid),v in a.items():
        aff[did][l]=(Component('affinity','property_raw',pid,v),)
    for (key,did,pid),v in b.items(): bonds[key]=(did,Component('bond_key','property_raw',pid,v))
    progress=index(rows['doll_progress.csv'],'doll_id'); masks=group(rows['doll_helix.csv'],'doll_id')
    weapons=index(rows['weapons.csv'],'uid'); parts=index(rows['attachments.csv'],'uid'); slots=group(rows['weapon_slot_observations.csv'],'weapon_uid'); options=group(rows['attachment_affixes.csv'],'uid')
    keys=index(rows['common_keys.csv'],'uid'); keyslots=group(rows['doll_key_values.csv'],'doll_id'); remold=group(rows['doll_core_slots.csv'],'doll_id'); remoldopts=group(rows['remolding_options.csv'],'uid')
    support_count=len(rows['support_units.csv'])
    out={}
    for r in rows['dolls.csv']:
        did=int(r['doll_id']); p=progress[did]; comp=[]; withheld=[]; differences={}; info=Counter()
        def prop(name,x): return Component(name,'property_raw',x.property_id,x.values)
        model=ds.model_reference(did); level=ds.level_reference(did,int_or_none(r['level']))
        if model is None or level is None: withheld.append('base_or_level')
        else:
            comp.extend((prop('doll_base',model.base),prop('doll_level',level.property)))
            info['base_level_shared_codes']=len(dict(model.base.values).keys()&dict(level.property.values).keys())
        cls=int_or_none(r['class_stage']); st={i:(prop('class',ds.class_reference(did,i).property),) for i in range(1,6) if ds.class_reference(did,i) is not None}
        co=stage_options(st,cls)
        if co is None: withheld.append('class')
        else: comp.extend(co[0]);differences['class']=candidate_difference(*co)
        al=int_or_none(p['affinity_level']); stages={1:(),**aff[did]}
        # Explicit scenario: reference freezes at level 5, not proof of no later effect.
        ao=stage_options(stages,min(al,5) if al is not None else None)
        if ao is None: withheld.append('affinity')
        else:
            comp.extend(ao[0]);differences['affinity_cap5_candidate']=candidate_difference(*ao)
            info['affinity_above_reference_range']=int(al>5)
        rank=ds.rank_reference(did,int_or_none(r['rank']))
        if rank is None: withheld.append('rank_absent_or_unknown')
        else:
            info['rank_reference_present']+=1
            if rank.field8_raw is not None:
                comp.append(Component('rank_field8_raw','grade_field_raw',rank.record_id,((8,rank.field8_raw),)))
            if rank.field9_raw is not None:
                comp.append(Component('rank_field9_candidate','grade_coefficient_millionths',rank.record_id,((9,int(rank.field9_raw*1000000)),)))
        ms=masks[did]; mask=int(ms[0]['value_raw']) if len(ms)==1 else None
        hc=mask_components(did,mask)
        if hc is None: withheld.append('helix_mask')
        else:
            comp.extend(hc);info['helix_property_nodes']=len(hc)
            refs=he.doll_references(did); unlocked=sum(bool(mask&(1<<i)) and n.key_id is not None and n.key_id in {int(x['value']) for x in keyslots[did] if x['kind']=='fixed_keys' and int_or_none(x['value'])} for i,n in enumerate(refs))
            # Distinct hypotheses for thresholds, not active game bonuses.
            info['equipped_fixed_key_bit_matches']=unlocked
            counts={'fixed_unlocked':sum(bool(mask&(1<<i)) and n.node_type_raw==3 for i,n in enumerate(refs)), 'fixed_equipped':unlocked}
            for name,count in counts.items():
                bs=tuple(b for b in he.bonus_references(did) if b.threshold_raw<=count)
                if bs:
                    comp.append(prop('helix_bonus_'+name+'_highest_candidate',bs[-1].property))
                    info['helix_bonus_'+name+'_highest_threshold_'+str(bs[-1].threshold_raw)]+=1
                    info['helix_bonus_'+name+'_eligible_records']+=len(bs)
                else: info['helix_bonus_'+name+'_no_eligible_record']+=1
        wuid=int_or_none(r['weapon_uid'])
        if not wuid: withheld.append('weapon_not_observed')
        else:
            w=weapons[wuid]; wr=wp.base_properties(int(w['item_id'])); lr=wp.level_reference(int_or_none(w['level']))
            if wr is None or lr is None: withheld.append('weapon_reference')
            else:
                comp.append(Component('weapon_base','property_raw',wr[0].property_id,tuple((x.field_code,x.value_units) for x in wr)))
                info['weapon_level_coefficients_present']+=1
                comp.append(Component('weapon_level_coefficients','level_up_raw',int(w['level']),tuple(zip((3,4,5),lr))))
            for s in slots[wuid]:
                uid=int_or_none(s['attachment_uid'])
                if not uid: continue
                part=parts[uid]; info['parts']+=1
                if part['affix_reference_status'] not in ('catalog_consistent','explicit_zero') or int_or_none(part['affix_count'])!=len(options[uid]):
                    withheld.append('part_affix_structure_unavailable'); continue
                for o in options[uid]:
                    code=int(o['stat_code']); info['affix_rows']+=1; info['affix_code_mapping_candidate']+=code in AFFIX_FIELD_CANDIDATES
                    val=int_or_none(o['amplified_stat_milli_candidate']); baseval=int(o['base_stat_milli_candidate'])
                    comp.append(Component('part_base','affix_'+o['stat_unit_candidate']+'_milli',int(o['affix_id']),((code,baseval),)))
                    if val is None and assume_absent_calibration_base and part['calibration_observation_source']=='absent' and part['calibration_status']=='field_absent':
                        val=baseval
                        info['affix_absent_calibration_base_hypothesis']+=1
                    if val is None: info['affix_amplification_unobserved']+=1
                    else: comp.append(Component('part_amplified_candidate','affix_'+o['stat_unit_candidate']+'_milli',int(o['affix_id']),((code,val),)));info['affix_amplification_candidate']+=1
        for s in keyslots[did]:
            uid=int_or_none(s['value'])
            if s['kind'] not in ('common_uids','bond_uids'):continue
            if uid is None: info['key_uid_absent_positions']+=1;continue
            if uid==0: info['key_uid_explicit_zero_positions']+=1;continue
            k=keys[uid]; kid=int(k['key_id']);typ=int(k['key_type'])
            if typ==2:
                kp=ck.base_properties(kid)
                if kp is None: withheld.append('common_key_reference');continue
                comp.append(Component('common_key_base','property_raw',kp[0].property_id,tuple((x.field_code,x.value_units) for x in kp))); info['common_keys']+=1
                n=int_or_none(k['evolution_option_count_candidate'])
                if n:
                    for i in range(1,n+1):
                        code=int(k[f'evolution_stat_code_{i}']); stage=int(k[f'evolution_stage_candidate_{i}'])
                        comp.append(Component('common_key_enhancement_candidate','key_option_stage',kid,((code,stage),)));info['common_key_option_rows']+=1
            elif typ==4 and kid in bonds and bonds[kid][0]==did:
                comp.append(bonds[kid][1]); info['bond_keys']+=1
            else: withheld.append('key_type_or_owner_reference')
        for s in remold[did]:
            uid=int_or_none(s['uid'])
            if not uid: continue
            info['remolding_slots']+=1
            for o in remoldopts[uid]:
                comp.append(Component('remolding_option','remolding_option_level',int(o['option_id']),((int(o['feature_group_id']),int(o['option_level'])),)));info['remolding_options']+=1
        out[did]=(tuple(comp),tuple(withheld),differences,dict(info))
    return out,support_count

