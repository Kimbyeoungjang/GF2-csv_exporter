"""Complete offline snapshot comparisons, with aggregate-only results.

Wire traversal and expected rows are separate from production export. Static
catalogs and parameter projections are shared. No private progress values are
returned, and no new semantic assignments are made.
"""
from collections import Counter
from .doll_snapshot_audit import _fields, _scalar, _vector, _dolls
from .equipment_snapshot_audit import _messages
from .wardrobe_formation_snapshot_audit import _Compare, _message
from .skill_parameters import COLUMNS, projection
from .skill_strategy_order import movement_projection, conditional_directive_status
from .doll_promotion import catalog as promotion_catalog
from .progress_catalog import reference_projection
from .achievement_catalog import challenge_status


def _variants(setting):
    result = []
    for v in _messages(setting, 2):
        block = _message(v, 2)
        entries = []
        for e in _messages(block or {}, 1):
            skill = _scalar(e, 1)
            if not skill:
                raise ValueError('Missing strategy skill')
            entries.append((skill, _scalar(e, 2)))
        result.append((_scalar(v, 1), entries))
    if len(result) > 16:
        raise ValueError('Too many variants')
    return result


def audit_current_strategy(payload, rows):
    keys = {'doll_progress.csv': ('doll_id',), 'doll_skills.csv': ('doll_id','position'),
            'doll_saved_strategies.csv': ('doll_id','strategy_position'),
            'doll_saved_strategy_skills.csv': ('doll_id','strategy_position','position')}
    # The inactive pair is optional when none are present.
    compare = _Compare({**rows, **{n: rows.get(n, []) for n in tuple(keys)[2:]}}, keys)
    counts = Counter()
    for did, record in _dolls(payload).items():
        setting = _message(record, 57)
        mode = _scalar(setting, 1) if setting is not None else None
        variants = _variants(setting) if setting is not None else []
        active = None
        if len(variants) == 1:
            active = 0
        elif variants:
            matches = [i for i,(sub,_) in enumerate(variants) if mode is not None and sub == mode % 256]
            if len(matches) != 1:
                raise ValueError('Ambiguous active variant')
            active = matches[0]
        sub, entries = variants[active] if active is not None else (None, [])
        move, status = movement_projection(mode)
        compare.row('doll_progress.csv',(did,),dict(skill_present=int(setting is not None),skill_mode_raw=mode,
            skill_submode_raw=sub,skill_variant_count=len(variants) if setting is not None else None,
            skill_active_variant_position=active+1 if active is not None else None,
            skill_active_entry_count=len(entries) if setting is not None else None,
            movement_directive_code_candidate=move,movement_directive_status=status,
            conditional_directive_status=conditional_directive_status(mode)))
        counts['dolls'] += 1
        counts['settings'] += setting is not None
        counts['variants'] += len(variants)
        counts['active_skills'] += len(entries)
        def skill_row(name,key,skill,param):
            compare.row(name,key,dict(skill_id=skill,parameter_raw=param,**dict(zip(COLUMNS,projection(param)))))
        for pos,(skill,param) in enumerate(entries,1):
            skill_row('doll_skills.csv',(did,pos),skill,param)
        inactive = [v for i,v in enumerate(variants) if i != active]
        counts['inactive_variants'] += len(inactive)
        for i,(sub,entries) in enumerate(inactive,1):
            compare.row('doll_saved_strategies.csv',(did,i),dict(submode_raw=sub,skill_count=len(entries)))
            counts['inactive_skills'] += len(entries)
            for j,(skill,param) in enumerate(entries,1):
                skill_row('doll_saved_strategy_skills.csv',(did,i,j),skill,param)
    return {'observations':dict(counts),**compare.result()}


def audit_additional_doll(payload, rows, manifest):
    """Compare valid complete observations (or absent wrapper), not error recovery."""
    names = {'additional_doll_observations.csv':('doll_id',),
             'additional_doll_key_slots.csv':('doll_id','kind','position'),
             'additional_doll_skill_variants.csv':('doll_id','variant_position'),
             'additional_doll_skills.csv':('doll_id','variant_position','position')}
    compare = _Compare({n: rows.get(n,[]) for n in names},names)
    top = _fields(payload)
    wrapper = _message(top,3)
    counts = Counter()
    if wrapper is None:
        expected = ('not_observed','parent_unavailable')
        actual = (manifest['additional_doll_observation']['status'],manifest['additional_doll_details']['status'])
        counts['manifest_match'] += expected == actual
        counts['manifest_mismatch'] += expected != actual
        return {'observations':dict(counts),**compare.result()}
    r = _message(wrapper,2)
    if r is None:
        raise ValueError('Missing additional doll record')
    did = _scalar(r,1)
    owned = _dolls(payload)
    if not did or did != _scalar(wrapper,1) or did in owned:
        raise ValueError('Outside separate complete doll scope')
    core = _message(r,55) or {}
    mask = _vector(r,51)
    if mask is not None and len(mask)!=1:
        raise ValueError('Invalid additional mask')
    uid = _scalar(r,62)
    weapons = {int(w['uid']):w for w in rows['weapons.csv']}
    owner_status = ('field_absent' if uid is None else 'explicit_zero' if uid==0 else 'unmatched' if uid not in weapons else
        'verified' if weapons[uid]['equipped_doll_id']==str(did) and all(_scalar(d,62)!=uid for d in owned.values()) else 'reference_mismatch')
    pair = _message(top,4)
    pairs = promotion_catalog()
    base = promoted = None
    pair_status = 'field_absent'
    if pair is not None:
        a,b = _scalar(pair,1),_scalar(pair,2)
        if not a or not b:
            pair_status = 'invalid_field'
        elif not pairs:
            pair_status = 'catalog_unavailable'
        elif pairs.get(a)!=b:
            pair_status = 'unrecognized_pair'
        elif did not in (a,b):
            pair_status = 'unrelated_pair'
        else:
            base,promoted=a,b
            pair_status='catalog_pair_linked' if (b if did==a else a) in owned else 'counterpart_not_observed'
    counterpart = [a for a,b in pairs.items() if b==did and a in owned]
    active = None
    active_status = 'counterpart_unavailable'
    if len(counterpart)==1:
        active_status = 'pair_unverified'
        if pair_status=='field_absent':
            active,active_status=counterpart[0],'base_form_candidate_field_absent'
        elif pair_status=='catalog_pair_linked' and (base,promoted)==(counterpart[0],did):
            active,active_status=did,'promoted_form_candidate_field_present'
    values = {col:_scalar(r,f) for col,f in (('level',3),('rank',6),('class_stage',31),('costume_id',5))}
    compare.row('additional_doll_observations.csv',(did,),dict(**values,core_level=_scalar(core,1),core_model_id=_scalar(core,3),
        helix_mask_candidate=mask[0] if mask else None,weapon_uid=uid,weapon_reference_status=owner_status,
        promotion_base_doll_id=base,promotion_promoted_doll_id=promoted,promotion_reference_status=pair_status,
        promotion_active_doll_id_candidate=active,promotion_active_reference_status=active_status))
    counts['additional_dolls'] += 1
    counts['promotion_pair_present'] += pair is not None
    key_counts = {}
    for kind,field,uid_field in (('fixed',42,None),('common',43,45),('expansion',52,None),('bond',59,60)):
        ids = _vector(r,field)
        uids = _vector(r,uid_field) if uid_field is not None else (None,)*len(ids or ())
        if uid_field is not None and ((ids is None)!=(uids is None) or len(ids or ())!=len(uids or ())):
            raise ValueError('Unpaired additional keys')
        key_counts[kind] = len(ids) if ids is not None else None
        for i,(kid,kuid) in enumerate(zip(ids or (),uids or ()),1):
            compare.row('additional_doll_key_slots.csv',(did,kind,i),dict(key_id=kid,key_uid=kuid))
            counts['key_slots'] += 1
    setting = _message(r,57)
    variants = _variants(setting) if setting is not None else []
    for i,(sub,entries) in enumerate(variants,1):
        compare.row('additional_doll_skill_variants.csv',(did,i),dict(submode_code=sub,skill_count=len(entries)))
        counts['variants'] += 1
        for j,(skill,param) in enumerate(entries,1):
            compare.row('additional_doll_skills.csv',(did,i,j),dict(skill_id=skill,parameter_code=param,**dict(zip(COLUMNS,projection(param)))))
            counts['skills'] += 1
    meta = {'key_counts':key_counts,'skill_setting_present':setting is not None,'skill_mode_code':_scalar(setting,1) if setting is not None else None,'variant_count':len(variants)}
    details_present = setting is not None or any(v is not None for v in key_counts.values())
    expected_detail = {'status':'observed' if details_present else 'not_observed','projection':meta if details_present else None}
    expected_parent = {'status':'observed','file':'additional_doll_observations.csv','rows':1,'semantic_status':'separate_record_promotion_link_candidate_not_active_form_proof'}
    for a,b in ((manifest['additional_doll_details'],expected_detail),(manifest['additional_doll_observation'],expected_parent)):
        counts['manifest_match'] += a==b
        counts['manifest_mismatch'] += a!=b
    return {'observations':dict(counts),**compare.result()}


def audit_progress(payload, rows):
    """Compare 10833 public projections by wire positions, including redaction."""
    names={'account_progress_groups.csv':('group_position',),
           'account_progress_entries.csv':('group_position','source_field','entry_position'),
           'achievements.csv':('achievement_id',)}
    compare=_Compare(rows,names)
    top=_fields(payload)
    if set(top)!={1}:
        raise ValueError('Unexpected progress envelope')
    counts=Counter()
    for pos,g in enumerate(_messages(top,1),1):
        gid=_scalar(g,1)
        fieldcounts=Counter()
        other=0
        counts['groups']+=1
        for field, records in g.items():
            if field==1: continue
            for wire,blob in records:
                if field not in (4,5,6) or wire!=2:
                    other+=1
                    continue
                fieldcounts[field]+=1
                counts['entries']+=1
                try:
                    rec=_fields(blob)
                except ValueError:
                    rec=None
                if rec is None:
                    first=second=None
                    status='invalid_record'
                else:
                    try: first=_scalar(rec,1)
                    except ValueError: first=None
                    try:
                        second=_scalar(rec,2)
                        status='field_absent' if second is None else 'observed'
                    except ValueError:
                        second=None
                        status='invalid_field'
                pair=int(rec is not None and set(rec)=={1,2} and first is not None and second is not None)
                withheld=gid==50 and field==6
                semantic='group50_value_withheld_pending_semantics' if withheld else 'challenge_state_code_unconfirmed' if gid==50 and field==4 else 'structural_only'
                if status=='observed':
                    status='withheld_group50_additional' if withheld else 'binary_code_observed' if second in (0,1) else 'withheld_outside_observed_domain'
                counts[status]+=1
                ref,ref_status=reference_projection(gid,field,first)
                compare.row('account_progress_entries.csv',(pos,field,fieldcounts[field]),dict(group_id_raw=gid,value1_raw=first,
                    value2_raw=second if status=='binary_code_observed' else None,decoded_pair=pair,semantic_status=semantic,
                    reference_catalog_candidate=ref,reference_status=ref_status,value2_observation_status=status))
                if gid==50 and field==4:
                    if not pair or second not in (0,1) or not first:
                        raise ValueError('Outside complete achievement audit scope')
                    compare.row('achievements.csv',(first,),dict(state_raw=second,challenge_reference_status=challenge_status(first)))
                    counts['achievements']+=1
        compare.row('account_progress_groups.csv',(pos,),dict(group_id_raw=gid,field4_count=fieldcounts[4],field5_count=fieldcounts[5],field6_count=fieldcounts[6],other_fields_count=other))
    return {'observations':dict(counts),**compare.result()}
