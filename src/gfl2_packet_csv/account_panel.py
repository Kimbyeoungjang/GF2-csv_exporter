"""Opt-in, read-only partial panel projection from V35 and optional bound research CSV.
All numeric strings are exact rational values (percentage points for rates).
No result is certified as a game stat; unresolved inputs never become zero.
"""
from collections import Counter
from fractions import Fraction as F
from math import ceil
from .bundle_import import read_bundle, bound_archive_digest
from .panel_ledger import ledger, index, research_refs, int_or_none
from .panel_equipment import AFFIX_MAP, panel_parts, equipped_key_scenarios
from .panel_references import (read_static_csv, read_candidates, emblem_total,
    cooperation, management_for_model, oath_from_affinity, packed_food_candidate,
    basic_food_state_scenario, read_panel_state, food_stage_values, base_auxiliary_panel, group_catalog, read_doll_food_groups, element_food_projection, affinity_gate_projection, basic_food_metric_stage)
from . import doll_stat_catalog as ds, weapon_property_catalog as wp
from .panel_state_evidence import absent_helix_evidence, weapon_slot_evidence

SCHEMA='gf2-partial-panel-1'
BASIC=(('attack',2,29),('defense',5,32),('hp',3,30))
FIELDS=(2,5,3,29,32,30,9,10)


def term(value=None, basis='', blockers=()):
    """Candidate with explicit provenance, or null with reason. Zero is a value."""
    if value is None:
        return {'status':'unresolved','value':None,'basis':basis,'blockers':sorted(set(blockers or ('input_missing',)))}
    if blockers: raise ValueError('known term cannot have blockers')
    return {'status':'candidate','value':str(F(value)),'basis':basis,'blockers':[]}


def combine(terms):
    missing={k:v['blockers'] for k,v in terms.items() if v['value'] is None}
    subtotal=sum((F(v['value']) for v in terms.values() if v['value'] is not None),F(0))
    return {'value':None if missing else str(subtotal),'status':'incomplete' if missing else 'candidate',
            'known_term_subtotal':str(subtotal),'missing_sources':missing,'terms':terms}


def primary_projection(flat, rates, emblem, unresolved_rules=()):
    if set(flat)!=set(rates): raise ValueError('source coverage mismatch')
    b,p=combine(flat),combine(rates)
    bv=F(b['value']) if b['value'] is not None else None
    pv=F(p['value']) if p['value'] is not None else None
    ev=F(emblem['value']) if emblem['value'] is not None else None
    total=None if None in (bv,pv,ev) else bv*(1+pv/100)+ev
    rules=sorted(set(unresolved_rules))
    contributions={}
    for name,f in flat.items():
        r=rates[name]
        # A zero rate can be evaluated independently of an unknown base.
        if f['value'] is None or r['value'] is None or (bv is None and F(r['value'])!=0):
            contributions[name]=term(basis='flat_plus_base_times_rate',blockers=('source_or_base_incomplete',))
        else:
            contributions[name]=term(F(f['value'])+(bv or 0)*F(r['value'])/100,'flat_plus_base_times_rate')
    contributions['emblem']=emblem
    return {'base':b,'bonus_pp':p,'emblem':emblem,'contributions':contributions,
            'modeled_total':None if total is None else str(total),
            'total_candidate':None if total is None or rules else str(total),
            'overview_candidate':None if total is None or rules else ceil(total),
            'unresolved_rules':rules,'status':'incomplete' if total is None else 'rule_unresolved' if rules else 'candidate'}


def part_terms(comps, missing, info, *, round_each, weapon_slots_known):
    """Missing amplification blocks only its own stat code, unless structure is unknown."""
    result={}
    broad=set(missing)&{'weapon_not_observed','part_affix_structure_unavailable'}
    if not weapon_slots_known: broad.add('weapon_slot_state_missing')
    if any(code not in AFFIX_MAP for c in comps if c.source in ('part_base','part_amplified_candidate') for code,_ in c.values):
        broad.add('affix_code_unverified')
    for field in FIELDS:
        codes={code for code,(f,_,_) in AFFIX_MAP.items() if f==field}
        subset=[c for c in comps if c.source in ('part_base','part_amplified_candidate') and any(k in codes for k,_ in c.values)]
        values, blockers=panel_parts(subset,round_each=round_each)
        errors=broad|blockers
        if errors: result[field]=term(basis='affix_projection',blockers=errors)
        elif subset and not round_each:
            # The rounding rule has independent UI evidence; raw, unrounded sum is not a panel value.
            result[field]=term(basis='affix_projection',blockers=('per_affix_rounding_not_selected',))
        else:
            basis='per_affix_round_up_screen_candidate'
            if info.get('affix_absent_calibration_base_hypothesis'): basis+=';absent_calibration_base_hypothesis'
            result[field]=term(values.get(field,0),basis)
    return result


def key_terms(comps,missing,prog,info,stage_scale,keys,*,overlay):
    errors=set(missing)&{'common_key_reference','key_type_or_owner_reference'}
    for col in ('common_uids_present','bond_uids_present'):
        if prog.get(col)!='1':errors.add(col+'_missing')
    al=int_or_none(prog.get('affinity_level'))
    if info.get('bond_keys') and al not in stage_scale:errors.add('bond_scale_missing')
    errors.update(keys['blockers'])
    base=keys['base_raw']; enhanced=keys['overlay_raw_candidate']
    uncertain=set()
    for c in comps:
        if c.source=='common_key_enhancement_candidate':
            for code,_ in c.values:
                if code in AFFIX_MAP:uncertain.add(AFFIX_MAP[code][0])
                else: errors.add('key_stage_unsupported')
    def bond(field):
        n=sum(v for c in comps if c.source=='bond_key' for k,v in c.values if k==field)
        return F(n,10)*(1+F(stage_scale[al],100) if info.get('bond_keys') else 1)
    out={}
    for field in (29,32,30,9,10):
        blocked=errors|({'common_key_enhancement_unverified'} if field in uncertain and not overlay else set())
        if blocked:out[field]=term(basis='equipped_key_join',blockers=blocked)
        else:out[field]=term(F((enhanced if overlay else base).get(field,0),10)+bond(field),
            'equipped_key_stage_overlay_and_affinity_candidate' if overlay and uncertain else 'equipped_key_base_and_affinity_candidate')
    return out


def _safe_account(fn):
    try:return fn(),None
    except (ValueError,KeyError,TypeError,IndexError):return None,'account_reference_or_state_unavailable'



def rank_state(doll, *, scenario=False):
    """The no-panel-effect scenario must not accept malformed rank state.
    Luis' zero-rank absence corroborates, but does not establish, the default.
    """
    rank=int_or_none(doll.get('rank')); status=doll.get('rank_reference_status')
    did=int(doll['doll_id'])
    ref=ds.rank_reference(did,rank) if rank is not None else None
    if status=='catalog_consistent' and ref is not None:
        basis='explicit_rank_static_reference'; value=rank
    elif status=='field_absent' and rank is None and scenario and ds.rank_reference(did,0) is not None:
        basis='absent_rank_zero_hypothesis'; value=0
    else:
        basis='rank_unresolved'; value=None
    blockers=[] if scenario and value is not None else ['rank_panel_effect_unverified']
    if value is None and scenario:blockers.append('rank_state_unavailable')
    return {'value_candidate':value,'basis':basis,'rule_blockers':blockers}

def read_partial_panel(bundle_path, candidate_csv=None, *, panel_state_csv=None, scenario=False, key_overlay=False,
                       weapon_no_panel_effect=False, round_each_affix=True, absent_calibration_base=False,
                       assume_absent_slots_empty=False):
    """Return separate source terms and aggregate candidates. No capture dependency.

    Without sidecar, management and food are unknown; do not reconstruct from a
    different snapshot. Scenario switches opt in to the corresponding hypotheses.
    An incomplete bundle or contradictory positive ownership links are rejected.
    """
    if not scenario and (key_overlay or weapon_no_panel_effect or absent_calibration_base or assume_absent_slots_empty):
        raise ValueError('extra scenario switches require scenario=True')
    bundle=read_bundle(bundle_path)
    if not bundle.complete_capture:raise ValueError('complete bundle required')
    static=read_static_csv()
    control,food,*_=static
    if candidate_csv is not None and panel_state_csv is not None:
        raise ValueError('choose only one panel state source')
    if bundle.embedded_states:
        if panel_state_csv is not None or candidate_csv is not None:
            raise ValueError('integrated backup already contains panel state')
        panel_state_csv = bundle.embedded_states['panel_state']
    if panel_state_csv is not None:
        state=read_panel_state(panel_state_csv,bundle_path,bundle,control,food)
    else:
        state=(None,{}) if candidate_csv is None else read_candidates(candidate_csv,bundle_path,bundle,control)
    result=_panel_from_bundle(bundle,state,static,scenario=scenario,key_overlay=key_overlay,
        weapon_no_panel_effect=weapon_no_panel_effect,round_each_affix=round_each_affix,
        absent_calibration_base=absent_calibration_base,assume_absent_slots_empty=assume_absent_slots_empty)
    bound_archive_digest(bundle_path,bundle)
    return result


def _panel_from_bundle(bundle, state, static, *, scenario=False, key_overlay=False,
                       weapon_no_panel_effect=False, round_each_affix=True, absent_calibration_base=False,
                       assume_absent_slots_empty=False):
    """Internal projection from already validated inputs; no backup file reads."""
    if not scenario and (key_overlay or weapon_no_panel_effect or absent_calibration_base or assume_absent_slots_empty):
        raise ValueError('extra scenario switches require scenario=True')
    if not bundle.complete_capture:raise ValueError('complete bundle required')
    rows=bundle.rows
    control,food,refs,roles,links,growth=static
    food_groups=read_doll_food_groups()
    group_catalog(food)
    scales,love,cores,_,_=growth
    mg,states=state
    coop,co_error=_safe_account(lambda:cooperation(rows,refs))
    emblem,em_error=_safe_account(lambda:emblem_total(rows['items.csv'],links,inventory_complete=True))
    aff,_=research_refs(); data,_=ledger(bundle,assume_absent_calibration_base=absent_calibration_base)
    progress=index(rows['doll_progress.csv'],'doll_id'); weapons=index(rows['weapons.csv'],'uid'); keys=equipped_key_scenarios(rows)
    # Reverse-only links for additional dolls must not become general doll sources.
    from .equipment_graph_audit import audit_equipment_graph
    graph=audit_equipment_graph(bundle)
    for name in ('current_weapon_parts','current_doll_keys','current_doll_remoldings'):
        if graph[name]['counts'].get('reverse_without_matching_claim',0):raise ValueError('unresolved reverse-only equipment ownership')
    slot_contexts=weapon_slot_evidence(bundle,graph)
    general=set(progress); claimed={int(r['weapon_uid']) for r in rows['dolls.csv'] if r['weapon_uid'] not in ('','0')}
    reverse_general={int(w['equipped_doll_id']) for uid,w in weapons.items() if uid not in claimed and w['equipped_doll_id'] not in ('','0') and int(w['equipped_doll_id']) in general}
    results={}
    for doll in rows['dolls.csv']:
        did=int(doll['doll_id']); p=progress[did]; comps,missing,_,info=data[did]; missing=set(missing)
        w=weapons.get(int_or_none(doll['weapon_uid'])); level=int_or_none(doll['level']); cls=int_or_none(doll['class_stage']); al=int_or_none(p['affinity_level'])
        state=states.get(did); oath,obasis=oath_from_affinity(al,state['oath_candidate'] if state else None,scenario=scenario)
        gate=affinity_gate_projection(al,int_or_none(p.get('affinity_cumulative_xp')),p.get('affinity_reference_status'),state['oath_candidate'] if state else None)
        if gate['status']=='conflict':oath,obasis=None,gate['basis']
        elif oath is None and gate['vowed_candidate'] is False and state and state['oath_candidate'] is False:
            oath,obasis=False,gate['basis']+';paired_flag_absence'
        packed=(state['packed_food'] if 'packed_food' in state else packed_food_candidate(food,state['food_field61_raw'])) if state else None
        stages=basic_food_state_scenario(packed) if packed and scenario else None
        fv=food_stage_values(food,stages) if stages is not None else None
        mgv,mg_error=_safe_account(lambda:management_for_model(did,mg,roles,control))
        aux=base_auxiliary_panel(comps)
        wb=w is not None and 'weapon_reference' not in missing and did not in reverse_general
        wl=int_or_none(w['level']) if w else None
        # The legacy graph has a validated slot projection. Explicit absent slot data cannot establish zero.
        slot_context=slot_contexts.get(int(w['uid'])) if w else None
        slots_known=bool(slot_context and slot_context['observed_slot_rows'])
        absent_slots_assumed=bool(slot_context and not slots_known and assume_absent_slots_empty and slot_context['status']=='no_positive_reverse_owner_candidate')
        if absent_slots_assumed:slots_known=True
        parts=part_terms(comps,missing,info,round_each=round_each_affix,weapon_slots_known=slots_known and did not in reverse_general)
        if absent_slots_assumed:
            for t in parts.values():
                if t['value'] is not None:t['basis']+=';absent_slots_empty_hypothesis'
        kt=key_terms(comps,missing,p,info,love,keys[did],overlay=key_overlay)
        hknown=p.get('helix_present')=='1' and 'helix_mask' not in missing
        helix_context=absent_helix_evidence(did,level) if p.get('helix_present')=='0' and 'helix_mask' in missing else None
        def helix_term(v,field,basis):
            if hknown:return term(v,basis)
            if scenario and helix_context is not None and field not in helix_context['possibly_nonzero_fields']:
                return term(0,'conditional_static_level_gate_invariant')
            return term(basis=basis,blockers=('helix_state_missing',))
        dknown='base_or_level' not in missing
        def prop(src,c):return sum(v for x in comps if x.source==src for k,v in x.values if k==c)
        def ratio(src,c):return F(prop(src,c),10)
        def val(v,known,basis,reason):return term(v,basis) if known else term(basis=basis,blockers=(reason,))
        rank_context=rank_state(doll,scenario=scenario)
        rank_rule=rank_context['rule_blockers']
        weapon_rule=[] if w and w.get('rank_reference_status')=='within_observed_positive_range' and (w.get('rank')=='1' or weapon_no_panel_effect and w.get('rank') in ('2','3','4','5','6')) else ['weapon_calibration_unverified']
        rules=rank_rule+weapon_rule
        core=None
        if p.get('core_present')=='0':core=(0,0,0)
        elif p.get('core_present')=='1':
            cm,cl=int_or_none(p.get('core_model_id')),int_or_none(p.get('core_level')); cr=wp.level_reference(cl) if cl is not None else None
            if cm in cores and cr is not None:core=tuple(F(v*cr[0],1000) for v in cores[cm])
        affinity=Counter()
        if al is not None:
            for (m,l,_),values in aff.items():
                if m==did and l<=min(al,5):affinity.update(dict(values))
        metrics={}
        for i,(name,c,bc) in enumerate(BASIC):
            clsrefs=[ds.class_reference(did,s) for s in range(1,cls+1)] if cls is not None and 0<=cls<=5 else None
            dv=ceil(F(prop('doll_base',c)*scales[level][1][i],1000))+sum(x.property.value(c) or 0 for x in clsrefs) if dknown and level in scales and clsrefs is not None and all(x is not None for x in clsrefs) and 'class' not in missing else None
            wv=ceil(F(prop('weapon_base',c)*scales[wl][1][i],1000)) if wb and wl in scales else None
            # Food attack is separately identified in non-absent packed field; other groups remain unresolved.
            stage=basic_food_metric_stage(packed,name)
            single=[0,0,0]
            if stage is not None:single[i]=stage
            foodval=fv[i] if fv is not None else (food_stage_values(food,single)[i] if stage is not None else None)
            flat={'doll':term(dv,'growth_and_class_candidate'),'weapon':term(wv,'weapon_growth_candidate'),
                  'helix':helix_term(prop('helix_bit_candidate',c),c,'helix_mask_candidate'),
                  'keys':term(0,'keys_modeled_as_rates'), 'parts':parts[c],
                  'affinity':val(affinity[c],al is not None and 'affinity' not in missing,'affinity_cap5_candidate','affinity_state_missing'),
                  'remolding':term(core[i] if core is not None else None,'core_growth_candidate'),
                  'management':term(mgv[i] if mgv is not None else None,'management_packet_field_candidate', (mg_error,) if mgv is None else ()),
                  'food':term(foodval,'food_explicit_scenario' if fv is not None else 'basic_food_byte_candidate' if stage is not None else 'food_slot_unmapped'),
                  'oath':term(0,'oath_rate_only_model'),'cooperation':term(0,'cooperation_rate_only_model')}
            rates={k:term(0,'no_rate_in_current_panel_model') for k in flat}
            rates.update(weapon=val(ratio('weapon_base',bc),wb,'weapon_base_reference','weapon_state_missing'),
                         helix=helix_term(ratio('helix_bit_candidate',bc)+ratio('helix_bonus_fixed_unlocked_highest_candidate',bc),bc,'helix_highest_threshold_candidate'),
                         keys=kt[bc],parts=parts[bc],oath=term(5 if oath else 0,obasis) if oath is not None else term(basis=obasis,blockers=('oath_state_missing',)),
                         cooperation=term(coop['bonus_pp'][i] if coop else None,coop['status'] if coop else '',(co_error,) if not coop else ()))
            e=term(emblem[i] if emblem is not None else None,'stage_reward_property_join',(em_error,) if emblem is None else ())
            metrics[name]=primary_projection(flat,rates,e,rules)
        for name,c in (('crit_pp',9),('crit_damage_pp',10)):
            t={'doll':val(ratio('doll_base',c),dknown,'doll_base_reference','doll_base_missing'),
               'weapon':val(ratio('weapon_base',c),wb,'weapon_base_reference','weapon_state_missing'),
               'helix':helix_term(ratio('helix_bit_candidate',c)+ratio('helix_bonus_fixed_unlocked_highest_candidate',c),c,'helix_highest_threshold_candidate'),
               'keys':kt[c],'parts':parts[c]}
            if c==10:t['cooperation']=term(coop['crit_damage_pp'] if coop else None,coop['status'] if coop else '',(co_error,) if not coop else ())
            m=combine(t); m['unresolved_rules']=rules.copy(); m['total_candidate']=m['value'] if not rules else None
            if m['value'] is not None and rules:m['status']='rule_unresolved'
            metrics[name]=m
        results[did]={'metrics':metrics,'base_auxiliary_panel':aux,'food_state_candidate':packed,'oath_basis':obasis,'affinity_gate_candidate':gate,'rank_state_candidate':rank_context,
                      'helix_absence_context':helix_context,'weapon_slot_context':slot_context,
                      'element_food_candidate':element_food_projection(food,packed,food_groups.get(did),scenario=scenario),
                      'scope':'modeled_out_of_combat_panel_not_verified'}
    counts={name:dict(Counter(r['metrics'][name]['status'] for r in results.values())) for name in (*[x[0] for x in BASIC],'crit_pp','crit_damage_pp')}
    return {'schema':SCHEMA,'bundle_sha256':bundle.archive_sha256,'mode':'explicit_scenario' if scenario else 'partial_no_opt_in_scenarios',
            'confirmed_final_values':0,'options':{'scenario':scenario,'key_overlay':key_overlay,'weapon_no_panel_effect':weapon_no_panel_effect,'round_each_affix':round_each_affix,'absent_calibration_base':absent_calibration_base,'assume_absent_slots_empty':assume_absent_slots_empty},'metric_status_counts':counts,'doll_count':len(results),
            'cooperation_property_new_raw_candidate':coop['property_new_raw'] if coop else None,'private_results':results}
