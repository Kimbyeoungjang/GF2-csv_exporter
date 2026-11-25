"""Saved formation schema v2; exact nested core and strategy message decoding.

All output is account data, not Debug. Catalog-checked static IDs and inventory
references retain original positions; unrecognized values are not emitted.
Core field 7 is a protobuf message, NOT a packed 12-integer vector.
"""
from __future__ import annotations

import csv
from .reference_data import lru_cache
from .reference_data import files
from .account_extension import parse, records, scalar, bytes1, nums, msg, ident
from .key_catalog import project as key_projection
from .skill_strategy_order import movement_projection
from .skill_parameters import COLUMNS as PARAM_COLUMNS, projection as param_projection

SLOTS_FILE = 'formation_slots.csv'
REFS_FILE = 'formation_slot_equipment_refs.csv'
GROUPS_FILE = 'formation_saved_groups.csv'
KEYS_FILE = 'formation_slot_key_ids.csv'
VARIANTS_FILE = 'formation_slot_skill_variants.csv'
SKILLS_FILE = 'formation_slot_skills.csv'
FILES = (GROUPS_FILE, SLOTS_FILE, REFS_FILE, KEYS_FILE, VARIANTS_FILE, SKILLS_FILE)
GROUP_HEADERS = ['formation_position','formation_name','slot_count']
SLOT_HEADERS = ['formation_position','formation_name','slot_position','doll_id','weapon_uid','weapon_reference_status',
                'core_present','core_level','core_model_id','core_model_status','core_slots_present','fixed_keys_present','expansion_keys_present',
                'skill_setting_present','skill_mode_code',
                'attachment_ref_count','common_key_ref_count','bond_key_ref_count','fixed_key_count','expansion_key_count','skill_variant_count',
                'movement_directive_code_candidate','movement_directive_status']
REF_HEADERS = ['formation_position','slot_position','source_field','reference_kind','reference_position','uid','reference_status','key_role','key_model_id','key_type','key_type_reference_status']
KEY_HEADERS = ['formation_position','slot_position','key_kind','key_position','key_id','reference_status']
VARIANT_HEADERS = ['formation_position','slot_position','variant_position','submode_code','active','skill_count']
SKILL_HEADERS = ['formation_position','slot_position','variant_position','skill_position','skill_id','parameter_code','reference_status',*PARAM_COLUMNS]
REF_FIELDS = {3:'attachment', 6:'common_key', '7.2':'remolding', 9:'common_key'}
KEY_REF_ROLES = {'6': ('common_key', 2), '9': ('bond_key', 4)}

def saved_key_projection(field, status, key_id):
    role = KEY_REF_ROLES.get(str(field))
    if role is None:
        return '', None, None, ''
    name, expected = role
    if status != 'verified':
        return name, None, None, status
    if key_id is None:
        return name, None, None, 'inventory_model_missing'
    kind, validation = key_projection(key_id)
    if validation != 'catalog_consistent':
        return name, None, None, validation
    return name, key_id, kind, 'catalog_consistent' if kind == expected else 'role_mismatch'

KEY_FIELDS = {4:('fixed_key',1),5:('expansion_key',3)}

@lru_cache(maxsize=1)
def _catalogs():
    try:
        root = files('gfl2_packet_csv.static_tables')
        with root.joinpath('formation_talent_key_catalog.csv').open('r',encoding='utf-8',newline='') as f:
            keys = {int(r['key_id']):(int(r['key_type']),int(r['doll_id'])) for r in csv.DictReader(f)}
        with root.joinpath('battle_skill_ids.csv').open('r',encoding='utf-8',newline='') as f:
            skills = frozenset(int(r['skill_id']) for r in csv.DictReader(f))
        with root.joinpath('formation_core_model_ids.csv').open('r',encoding='utf-8',newline='') as f:
            cores = frozenset(int(r['model_id']) for r in csv.DictReader(f))
        return keys,skills,cores
    except (OSError, ValueError, KeyError, TypeError):
        return {},frozenset(),frozenset()


def _core(slot):
    if 7 not in slot:
        return None
    c = msg(slot, 7)
    if set(c) - {1,2,3}:
        raise ValueError('unsupported saved core fields')
    values = nums(c,2) if 2 in c else None
    if values is not None and len(values) != 6:
        raise ValueError('invalid saved core slot count')
    return {'level':scalar(c,1),'model_id':scalar(c,3),'slots':values}


def _strategy(slot):
    if 8 not in slot:
        return None
    s = msg(slot,8)
    if set(s) - {1,2}:
        raise ValueError('unsupported saved strategy fields')
    mode = scalar(s,1)
    variants=[]
    for v in records(s,2):
        if set(v)-{1,2}:
            raise ValueError('unsupported saved strategy variant fields')
        sub=ident(v,1)
        block=msg(v,2)
        if set(block)-{1}:
            raise ValueError('unsupported saved strategy block')
        entries=[]
        for entry in records(block,1):
            if set(entry)-{1,2}:
                raise ValueError('unsupported saved skill fields')
            entries.append((ident(entry,1),scalar(entry,2)))
        variants.append((sub,entries))
    if len(variants)>16 or len({v[0] for v in variants})!=len(variants):
        raise ValueError('invalid saved strategy variants')
    return {'mode':mode,'variants':variants}


def decode_saved_formations(data):
    top = parse(data)
    if not top:
        return []
    wrapper = parse(bytes1(top,1))
    out=[]
    for formation in records(wrapper,1):
        name=bytes1(formation,1).decode('utf-8') if 1 in formation else ''
        slots=[]
        for slot in records(formation,2):
            doll_id=scalar(slot,1)
            weapon_uid=scalar(slot,2)
            if slot and not doll_id:
                raise ValueError('missing saved formation doll identifier')
            core=_core(slot)
            strategy=_strategy(slot)
            refs={f:list(enumerate(nums(slot,f),1)) for f in (3,6,9) if f in slot}
            if core is not None and core['slots'] is not None:
                refs['7.2']=list(enumerate(core['slots'],1))
            keys={f:nums(slot,f) for f in KEY_FIELDS if f in slot}
            slots.append({'doll_id':doll_id,'weapon_uid':weapon_uid,'refs':refs,'keys':keys,'core':core,'strategy':strategy})
        out.append((name,slots))
    return out


def export_saved_formations(formations, *, weapons, attachments, common_keys, remoldings, available):
    slots,refs,groups,keys,variants,skills=[],[],[],[],[],[]
    counts={'verified':0,'unavailable':0,'unmatched':0,'empty':0,'not_observed':0}
    static_counts={'keys_verified':0,'keys_unmatched':0,'keys_empty':0,'skills_verified':0,'skills_unmatched':0,'core_models_verified':0,'core_models_unmatched':0}
    inventories={'weapon':weapons,'attachment':attachments,'common_key':common_keys,'remolding':{int(uid) for uid in remoldings}}
    key_catalog,skill_catalog,core_catalog=_catalogs()
    def verified(uid,kind):
        if uid is None: status='not_observed'
        elif uid==0: status='empty'
        elif kind not in available: status='unavailable'
        elif uid not in inventories[kind]: status='unmatched'
        else: status='verified'
        counts[status]+=1
        return (uid if status=='verified' else '',status)
    for i,(name,formation) in enumerate(formations,1):
        groups.append(dict(zip(GROUP_HEADERS,(i,name,len(formation)))))
        for j,slot in enumerate(formation,1):
            doll=slot['doll_id']; core=slot['core']; strategy=slot['strategy']
            uid,status=verified(slot['weapon_uid'],'weapon')
            model=core['model_id'] if core else None
            core_status='not_observed' if model is None else 'verified' if model in core_catalog else 'unmatched'
            if model is not None:
                static_counts['core_models_'+core_status]+=1
            slots.append(dict(zip(SLOT_HEADERS,(i,name,j,doll,uid,status,int(core is not None),
                core['level'] if core else None,model if core_status=='verified' else None,core_status,int(core is not None and core['slots'] is not None),
                int(4 in slot['keys']),int(5 in slot['keys']),int(strategy is not None),strategy['mode'] if strategy else None,
                *(len(slot['refs'][f]) if f in slot['refs'] else None for f in (3,6,9)),
                *(len(slot['keys'][f]) if f in slot['keys'] else None for f in (4,5)),
                len(strategy['variants']) if strategy is not None else None,
                *movement_projection(strategy['mode'] if strategy else None)))))
            for field,values in slot['refs'].items():
                kind=REF_FIELDS[field]
                for k,value in values:
                    uid,status=verified(value,kind)
                    key_id = common_keys.get(uid, {}).get('key_id') if kind == 'common_key' and status == 'verified' else None
                    projected = saved_key_projection(field, status, key_id)
                    refs.append(dict(zip(REF_HEADERS,(i,j,field,kind,k,uid,status,*projected))))
            for field,values in slot['keys'].items():
                kind,typ=KEY_FIELDS[field]
                for k,value in enumerate(values,1):
                    status='empty' if value==0 else 'verified' if key_catalog.get(value)==(typ,doll) else 'unmatched'
                    static_counts['keys_'+status]+=1
                    keys.append(dict(zip(KEY_HEADERS,(i,j,kind,k,value if status=='verified' else None,status))))
            if strategy is not None:
                for k,(sub,entries) in enumerate(strategy['variants'],1):
                    active=None if strategy['mode'] is None else int((strategy['mode']&255)==sub)
                    variants.append(dict(zip(VARIANT_HEADERS,(i,j,k,sub,active,len(entries)))))
                    for p,(skill,param) in enumerate(entries,1):
                        status='verified' if skill in skill_catalog else 'unmatched'
                        static_counts['skills_'+status]+=1
                        skills.append(dict(zip(SKILL_HEADERS,(i,j,k,p,skill if status=='verified' else None,param if status=='verified' else None,status,*param_projection(param if status=='verified' else None)))))
    output={GROUPS_FILE:groups,SLOTS_FILE:slots,REFS_FILE:refs,KEYS_FILE:keys,VARIANTS_FILE:variants,SKILLS_FILE:skills}
    return output,{'schema_version':6,'formations':len(formations),'slots':len(slots),
        'occupied_slots':sum(bool(r['doll_id']) for r in slots),'reference_counts':counts,
        'catalog_validation':static_counts,
        'saved_key_type_audit':saved_key_audit(refs),'core_records':sum(r['core_present'] for r in slots),
        'skill_settings':sum(r['skill_setting_present'] for r in slots),'skill_variants':len(variants),'skill_entries':len(skills),
        'semantic_status':'saved_reference_links_not_current_equipment; skill_codes_structural_only',
        'unverified_fields_omitted':True,'files':list(FILES)}

def saved_key_audit(rows):
    from collections import Counter
    return {role: dict(sorted(Counter(r['key_type_reference_status'] for r in rows if r['key_role']==role).items()))
            for role, _ in KEY_REF_ROLES.values()}

HEADERS={GROUPS_FILE:GROUP_HEADERS,SLOTS_FILE:SLOT_HEADERS,REFS_FILE:REF_HEADERS,
         KEYS_FILE:KEY_HEADERS,VARIANTS_FILE:VARIANT_HEADERS,SKILLS_FILE:SKILL_HEADERS}
