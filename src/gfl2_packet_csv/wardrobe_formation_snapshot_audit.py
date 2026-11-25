"""Offline complete-snapshot wardrobe and saved formation comparison.

Only aggregate counts leave the audit. Reference catalog and semantic projection
helpers are shared; the message traversal and row construction are independent
of the production decoder and exporter. Not a partial-update merge audit.
"""
from collections import Counter, defaultdict
from .doll_snapshot_audit import _fields, _scalar, _vector
from .equipment_snapshot_audit import _messages, _keyed
from .dorm_costumes import known_doll_costumes
from .helix_catalog import catalog as helix_catalog
from .costume_integrity import PART_COLUMNS, part_projection
from .formation_slots import _catalogs, saved_key_projection
from .skill_strategy_order import movement_projection
from .skill_parameters import COLUMNS, projection


def _cell(x):
    return '' if x is None else str(x)


class _Compare:
    def __init__(self, rows, keys):
        self.tables = {}
        self.expected = defaultdict(set)
        self.counts = defaultdict(Counter)
        for name, cols in keys.items():
            table = {}
            for row in rows[name]:
                key = tuple(row[col] for col in cols)
                if key in table:
                    raise ValueError('Duplicate CSV audit key')
                table[key] = row
            self.tables[name] = table

    def row(self, name, key, values):
        key = tuple(map(_cell, key))
        if key in self.expected[name]:
            raise ValueError('Duplicate source audit key')
        self.expected[name].add(key)
        row = self.tables[name].get(key)
        for col, val in values.items():
            status = 'missing' if row is None else 'match' if row[col] == _cell(val) else 'mismatch'
            self.counts[f'{name}:{col}'][status] += 1

    def result(self):
        return {'checks': {k: {s: v[s] for s in ('match','mismatch','missing')} for k,v in sorted(self.counts.items())},
                'extra_csv_rows': {k:len(set(t)-self.expected[k]) for k,t in self.tables.items()}}


def _message(fields, n):
    raw = _scalar(fields,n,2)
    return None if raw is None else _fields(raw)


def _wardrobe(fields):
    return {key:_scalar(row,2) for key,row in _keyed(fields,2).items()}


def audit_wardrobe_snapshot(doll_payload, dorm_payload, rows):
    doll, dorm = _fields(doll_payload), _fields(dorm_payload)
    wardrobes = {'11101':_wardrobe(doll), '11627':_wardrobe(dorm)}
    refs = known_doll_costumes()
    owners = defaultdict(set)
    for did, cid in refs:
        owners[cid].add(did)
    compare = _Compare(rows, {'doll_costumes.csv':('source','costume_id'),
        'dorm_doll_costumes.csv':('doll_id',), 'doll_progress.csv':('doll_id',)})
    count = Counter()
    for source, values in wardrobes.items():
        peer = wardrobes['11627' if source == '11101' else '11101']
        count[f'wardrobe_{source}'] = len(values)
        for cid,state in values.items():
            ds = owners.get(cid,())
            ref = 'catalog_unavailable' if not refs else 'unknown_costume' if not ds else 'catalog_consistent' if len(ds)==1 else 'ambiguous_doll'
            relation = ('not_in_peer_snapshot' if cid not in peer else 'peer_state_unavailable' if state is None or peer[cid] is None else 'peer_state_equal' if state==peer[cid] else 'peer_state_different')
            compare.row('doll_costumes.csv',(source,cid),dict(state_raw=state,static_doll_id=next(iter(ds)) if len(ds)==1 else None,
                costume_model_reference_status=ref,peer_state_status=relation,**dict(zip(PART_COLUMNS,part_projection(cid,state)))))
            count[f'wardrobe_{relation}'] += 1
    dolls = _keyed(doll,1)
    if not dolls:
        raise ValueError('Missing ordinary doll snapshot')
    for did,r in dolls.items():
        cid,state = _scalar(r,5),_scalar(r,54)
        ref = ('field_absent' if cid is None else 'explicit_zero' if cid==0 else 'catalog_unavailable' if not refs else 'unknown_costume' if cid not in owners else 'catalog_consistent' if did in owners[cid] else 'doll_model_mismatch')
        w = wardrobes['11101']
        relation = ('costume_unavailable' if not cid else 'not_in_wardrobe_snapshot' if cid not in w else 'state_unavailable' if state is None or w[cid] is None else 'state_equal' if state==w[cid] else 'state_different')
        compare.row('doll_progress.csv',(did,),dict(costume_id=cid,costume_state_raw=state,costume_model_reference_status=ref,
            costume_wardrobe_state_status=relation,**dict(zip(PART_COLUMNS,part_projection(cid,state)))))
        count['current_dolls'] += 1
    outer = _message(dorm,1)
    if outer is None or 3 not in outer:
        raise ValueError('Dorm roster not observed; outside complete audit scope')
    roster = _keyed(outer,3)
    if not roster or set(roster)-set(dolls):
        raise ValueError('Missing or unmatched dorm roster')
    active = _scalar(outer,9)
    candidate = active in roster and active in helix_catalog()
    for did,r in roster.items():
        inner = _message(r,2)
        if inner is None or _scalar(inner,1)!=did:
            raise ValueError('Dorm inner/outer doll mismatch')
        cid = _scalar(inner,8)
        status = 'field_absent' if cid is None else 'explicit_zero' if cid==0 else 'catalog_verified' if (did,cid) in refs else 'unrecognized'
        compare.row('dorm_doll_costumes.csv',(did,),dict(costume_id=cid if status=='catalog_verified' else None,
            costume_reference_status=status,dorm_reference_match_candidate=int(did==active) if candidate else None))
        count['dorm_dolls'] += 1
        count['dorm_'+status] += 1
    return {'observations':dict(count),**compare.result()}


def audit_saved_formation_snapshot(payload, rows):
    keys = {'formation_saved_groups.csv':('formation_position',), 'formation_slots.csv':('formation_position','slot_position'),
        'formation_slot_equipment_refs.csv':('formation_position','slot_position','source_field','reference_position'),
        'formation_slot_key_ids.csv':('formation_position','slot_position','key_kind','key_position'),
        'formation_slot_skill_variants.csv':('formation_position','slot_position','variant_position'),
        'formation_slot_skills.csv':('formation_position','slot_position','variant_position','skill_position')}
    compare = _Compare(rows,keys)
    inventories = {kind:{int(r['uid']):r for r in rows[file]} for kind,file in (
        ('weapon','weapons.csv'),('attachment','attachments.csv'),('common_key','common_keys.csv'),('remolding','remolding_details.csv'))}
    static_keys,static_skills,static_cores = _catalogs()
    counts = Counter()
    def verify(uid, kind):
        status = 'not_observed' if uid is None else 'empty' if uid==0 else 'verified' if uid in inventories[kind] else 'unmatched'
        counts['inventory_'+status] += 1
        return uid if status=='verified' else None,status
    wrapper = _message(_fields(payload),1)
    if wrapper is None:
        raise ValueError('Saved formation wrapper not observed')
    for i,form in enumerate(_messages(wrapper,1),1):
        raw_name = _scalar(form,1,2)
        name = raw_name.decode('utf-8') if raw_name is not None else ''
        slots = list(_messages(form,2))
        compare.row('formation_saved_groups.csv',(i,),dict(formation_name=name,slot_count=len(slots)))
        counts['formations'] += 1
        for j,s in enumerate(slots,1):
            did = _scalar(s,1)
            if s and not did:
                raise ValueError('Missing saved doll ID')
            uid,status = verify(_scalar(s,2),'weapon')
            core,strategy = _message(s,7),_message(s,8)
            model = _scalar(core,3) if core is not None else None
            core_status = 'not_observed' if model is None else 'verified' if model in static_cores else 'unmatched'
            core_slots = _vector(core,2) if core is not None else None
            if core_slots is not None and len(core_slots)!=6:
                raise ValueError('Invalid saved core slot count')
            mode = _scalar(strategy,1) if strategy is not None else None
            variants = list(_messages(strategy,2)) if strategy is not None else None
            sequences = {n:_vector(s,n) for n in (3,4,5,6,9)}
            movement,movement_status = movement_projection(mode)
            compare.row('formation_slots.csv',(i,j),dict(formation_name=name,doll_id=did,weapon_uid=uid,weapon_reference_status=status,
                core_present=int(core is not None),core_level=_scalar(core,1) if core is not None else None,
                core_model_id=model if core_status=='verified' else None,core_model_status=core_status,core_slots_present=int(core_slots is not None),
                fixed_keys_present=int(sequences[4] is not None),expansion_keys_present=int(sequences[5] is not None),
                skill_setting_present=int(strategy is not None),skill_mode_code=mode,
                **{col:len(sequences[n]) if sequences[n] is not None else None for n,col in ((3,'attachment_ref_count'),(6,'common_key_ref_count'),(9,'bond_key_ref_count'),(4,'fixed_key_count'),(5,'expansion_key_count'))},
                skill_variant_count=len(variants) if variants is not None else None,movement_directive_code_candidate=movement,movement_directive_status=movement_status))
            counts['slots'] += 1
            counts['occupied_slots'] += bool(did)
            for n,kind,seq in ((3,'attachment',sequences[3]),(6,'common_key',sequences[6]),(9,'common_key',sequences[9]),('7.2','remolding',core_slots)):
                for pos,value in enumerate(seq or (),1):
                    uid,status = verify(value,kind)
                    key_id = int(inventories[kind][uid]['key_id']) if kind=='common_key' and uid is not None else None
                    compare.row('formation_slot_equipment_refs.csv',(i,j,n,pos),dict(reference_kind=kind,uid=uid,reference_status=status,
                        **dict(zip(('key_role','key_model_id','key_type','key_type_reference_status'),saved_key_projection(n,status,key_id)))))
                    counts['equipment_refs'] += 1
            for n,kind,typ in ((4,'fixed_key',1),(5,'expansion_key',3)):
                for pos,value in enumerate(sequences[n] or (),1):
                    status = 'empty' if value==0 else 'verified' if static_keys.get(value)==(typ,did) else 'unmatched'
                    compare.row('formation_slot_key_ids.csv',(i,j,kind,pos),dict(key_id=value if status=='verified' else None,reference_status=status))
                    counts['key_refs'] += 1
            seen = set()
            if variants is not None and len(variants)>16:
                raise ValueError('Too many saved variants')
            for pos,v in enumerate(variants or (),1):
                sub = _scalar(v,1)
                if not sub or sub in seen:
                    raise ValueError('Missing or duplicate saved variant')
                seen.add(sub)
                block = _message(v,2)
                entries = list(_messages(block,1)) if block is not None else []
                compare.row('formation_slot_skill_variants.csv',(i,j,pos),dict(submode_code=sub,active=int(mode%256==sub) if mode is not None else None,skill_count=len(entries)))
                counts['variants'] += 1
                for p,e in enumerate(entries,1):
                    skill,param = _scalar(e,1),_scalar(e,2)
                    if not skill:
                        raise ValueError('Missing saved skill ID')
                    status = 'verified' if skill in static_skills else 'unmatched'
                    if status!='verified': skill=param=None
                    compare.row('formation_slot_skills.csv',(i,j,pos,p),dict(skill_id=skill,parameter_code=param,reference_status=status,
                        **dict(zip(COLUMNS,projection(param)))))
                    counts['skills'] += 1
    return {'observations':dict(counts),**compare.result()}
