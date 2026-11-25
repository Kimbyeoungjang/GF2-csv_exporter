"""Account capture v2: independent, typed, normalized CSV files.

Shared (verbatim) by GF2-csv_exporter and GF2Tools. Read/write data, never game
files or process memory. A missing dataset is not an empty observed dataset.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from .account_extension import TYPES, VERSION, validate_document
from .skill_strategy_order import movement_projection, conditional_directive_status
from .doll_growth_catalog import affinity_status, core_model_status, helix_mask_projection
from .helix_catalog import group_projection
from .skill_parameters import COLUMNS as PARAM_COLUMNS, projection as param_projection, validate as validate_param
from .costume_integrity import current_projection, wardrobe_projection, part_projection, PART_COLUMNS
from .support_catalog import reference_status, skill_group_status, frame_slot_id, room_level_status, amplification_projection
from .achievement_catalog import challenge_status
from .support_capacity import capacity_projection, stage_skill_projection
from .support_attribute_totals import attribute_projection, secondary_value_status
from .remolding_catalog import model_status, option_projection, primary_projection, secondary_projection

STATUS_FILE = 'account_status.csv'
CSV_SCHEMA = 17
MAX_ROWS = 200_000
MAX_BYTES = 24 * 1024 * 1024

HEADERS = {
    'account_status.csv': 'format_version section status',
    'doll_progress.csv': 'doll_id costume_id costume_state_raw affinity_present affinity_level affinity_cumulative_xp core_present core_level core_model_id core_slots_present skill_present skill_mode_raw skill_submode_raw fixed_keys_present common_keys_present common_uids_present helix_present expansion_keys_present expansion_stage_raw bond_keys_present bond_uids_present affinity_reference_status core_model_reference_status costume_model_reference_status costume_wardrobe_state_status skill_variant_count skill_active_variant_position skill_active_entry_count movement_directive_code_candidate movement_directive_status conditional_directive_status',
    'doll_core_slots.csv': 'doll_id slot uid',
    'doll_key_values.csv': 'doll_id kind position value',
    'doll_helix.csv': 'doll_id position value_raw mask_set_bit_count_candidate mask_pattern_status helix_group_id static_node_count helix_group_reference_status',
    'doll_skills.csv': 'doll_id position skill_id parameter_raw',
    'doll_saved_strategies.csv': 'doll_id strategy_position submode_raw skill_count',
    'doll_saved_strategy_skills.csv': 'doll_id strategy_position position skill_id parameter_raw',
    'doll_costumes.csv': 'source costume_id state_raw static_doll_id costume_model_reference_status peer_state_status',
    'remolding_details.csv': 'uid model_id model_reference_status',
    'remolding_options.csv': 'uid position option_id feature_group_id option_level option_reference_status',
    'support_settings.csv': 'level selected_position_raw level_reference_status',
    'support_formations.csv': 'position used_capacity special_slots_present level_capacity_candidate occupied_slot_capacity_candidate capacity_comparison_status',
    'support_units.csv': 'formation_position slot_position present unit_id skill_id skill_group_reference_status',
    'support_unit_ammunition.csv': 'formation_position slot_position position ammunition_id reference_status',
    'support_attributes.csv': 'formation_position attribute_id value level_base_candidate normal_chip_sum attribute_sum_status',
    'support_frames.csv': 'formation_position frame_id',
    'support_frame_slots.csv': 'formation_position frame_id slot_position uid static_slot_id',
    'support_special_slots.csv': 'formation_position slot_position uid',
    'support_ammunition.csv': 'position ammunition_id reference_status',
    'support_chips.csv': 'kind uid model_id attr_1 attr_2 attr_3 attr_4 attr_5 attr_6 player_amplification_level model_reference_status primary_attribute_id primary_base_value primary_reference_status secondary_attribute_count_expected secondary_attribute_count_observed secondary_count_reference_status amplification_break_record_id amplification_reference_status secondary_value_reference_status amplification_skill_group_id amplification_skill_id',
    'support_owned_units.csv': 'position unit_id reference_status',
    'achievements.csv': 'achievement_id state_raw challenge_reference_status',
}
HEADERS = {key: value.split() for key, value in HEADERS.items()}
for _name in ('doll_progress.csv', 'doll_costumes.csv'):
    HEADERS[_name].extend(PART_COLUMNS)
for _name in ('doll_skills.csv','doll_saved_strategy_skills.csv'):
    HEADERS[_name].extend(PARAM_COLUMNS)
SECTION_FILES = {
    'dolls': ('doll_progress.csv','doll_core_slots.csv','doll_key_values.csv','doll_helix.csv','doll_skills.csv','doll_costumes.csv'),
    'remoldings': ('remolding_details.csv','remolding_options.csv'),
    'support_formations': ('support_settings.csv','support_formations.csv','support_units.csv','support_unit_ammunition.csv','support_attributes.csv','support_frames.csv','support_frame_slots.csv','support_special_slots.csv','support_ammunition.csv'),
    'support_chips': ('support_chips.csv',),
    'support_unit_ids': ('support_owned_units.csv',),
    'achievement_states': ('achievements.csv',),
    'wardrobe_11627': ('doll_costumes.csv',),
}
OPTIONAL_FILES = frozenset(HEADERS)
KINDS = {
    'fixed_key_ids_raw': 'fixed_keys',
    'common_key_ids_raw': 'common_keys',
    'common_key_uids_raw': 'common_uids',
    'expansion_key_ids_raw': 'expansion_keys',
    'bond_key_ids_raw': 'bond_keys',
    'bond_key_uids_raw': 'bond_uids',
}
REVERSE_KINDS = {v: k for k, v in KINDS.items()}


def _csv_bytes(name, rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(HEADERS[name])
    for index,row in enumerate(rows):
        if index >= MAX_ROWS:
            raise ValueError('CSV row limit: ' + name)
        if len(row) != len(HEADERS[name]):
            raise ValueError('CSV column mismatch: ' + name)
        writer.writerow(['' if x is None else x for x in row])
    result = stream.getvalue().encode('utf-8-sig')
    if len(result) > MAX_BYTES:
        raise ValueError('CSV size limit: ' + name)
    return result


def serialize(document):
    """Return filename->CSV UTF-8 BOM bytes (no account_extension.json)."""
    validate_document(document)
    if not document['sections'] and all(status == 'missing' for status in document['status'].values()):
        return {}
    out = {name: [] for key in document['sections'] for name in SECTION_FILES[key]}
    out[STATUS_FILE] = [(CSV_SCHEMA, k, document['status'][k]) for k in TYPES.values()]
    sec = document['sections']
    if 'dolls' in sec:
        d = sec['dolls']
        for doll_id, r in sorted(d['rows'].items(), key=lambda x: int(x[0])):
            a, c, skill = r['affinity'], r['core'], r['skill_setting_raw']
            out['doll_progress.csv'].append((doll_id,r['costume_id'],r['costume_state_raw'],int(a is not None),a['level'] if a else None,a['cumulative_xp'] if a else None,int(c is not None),c['level'] if c else None,c['model_id'] if c else None,int(c is not None and c['remolding_slots'] is not None),int(skill is not None),skill['mode_raw'] if skill else None,skill['submode_raw'] if skill else None,int(r['fixed_key_ids_raw'] is not None),int(r['common_key_ids_raw'] is not None),int(r['common_key_uids_raw'] is not None),int(r['helix_values_raw'] is not None),int(r['expansion_key_ids_raw'] is not None),r['expansion_stage_raw'],int(r['bond_key_ids_raw'] is not None),int(r['bond_key_uids_raw'] is not None),affinity_status(a),core_model_status(doll_id,c),*current_projection(int(doll_id),r['costume_id'],r['costume_state_raw'],d['wardrobe_11101']),skill['variant_count'] if skill is not None else None,skill['active_variant_position'] if skill is not None else None,len(skill['entries']) if skill is not None else None,*movement_projection(skill['mode_raw'] if skill else None),conditional_directive_status(skill['mode_raw'] if skill else None),*part_projection(r['costume_id'],r['costume_state_raw'])))
            if c and c['remolding_slots'] is not None:
                out['doll_core_slots.csv'].extend((doll_id,i,x) for i,x in enumerate(c['remolding_slots'],1))
            for key,kind in KINDS.items():
                if r[key] is not None:
                    out['doll_key_values.csv'].extend((doll_id,kind,i,x) for i,x in enumerate(r[key],1))
            if r['helix_values_raw'] is not None:
                out['doll_helix.csv'].extend((doll_id,i,x,*helix_mask_projection(x),*group_projection(int(doll_id),x)) for i,x in enumerate(r['helix_values_raw'],1))
            if skill is not None:
                out['doll_skills.csv'].extend((doll_id,i,x['skill_id'],x['parameter_raw'],*param_projection(x['parameter_raw'])) for i,x in enumerate(skill['entries'],1))
                if 'saved_variants_raw' in skill:
                    out.setdefault('doll_saved_strategies.csv', [])
                    out.setdefault('doll_saved_strategy_skills.csv', [])
                    for position, variant in enumerate(skill['saved_variants_raw'], 1):
                        out['doll_saved_strategies.csv'].append((doll_id, position, variant['submode_raw'],len(variant['entries'])))
                        out['doll_saved_strategy_skills.csv'].extend(
                            (doll_id, position, i, entry['skill_id'], entry['parameter_raw'],*param_projection(entry['parameter_raw']))
                            for i, entry in enumerate(variant['entries'], 1))
        out['doll_costumes.csv'].extend(('11101',r['costume_id'],r['state_raw'],*wardrobe_projection(r['costume_id'],r['state_raw'],sec.get('wardrobe_11627')),*part_projection(r['costume_id'],r['state_raw'])) for r in d['wardrobe_11101'].values())
    if 'wardrobe_11627' in sec:
        out['doll_costumes.csv'].extend(('11627',r['costume_id'],r['state_raw'],*wardrobe_projection(r['costume_id'],r['state_raw'],sec.get('dolls',{}).get('wardrobe_11101')),*part_projection(r['costume_id'],r['state_raw'])) for r in sec['wardrobe_11627'].values())
    if 'remoldings' in sec:
        for uid,r in sorted(sec['remoldings'].items(),key=lambda x:int(x[0])):
            out['remolding_details.csv'].append((uid,r['model_id'],model_status(r['model_id'])))
            out['remolding_options.csv'].extend((uid,i,x,*option_projection(x)) for i,x in enumerate(r['option_ids'],1))
    if 'support_formations' in sec:
        sf = sec['support_formations']
        out['support_settings.csv'].append((sf['level'],sf['selected_position_raw'],room_level_status(sf['level'])))
        out['support_ammunition.csv'].extend((i,x,reference_status('support_ammunition',x)) for i,x in enumerate(sf['ammunition_ids'],1))
        for r in sf['formations']:
            position = r['position']
            out['support_formations.csv'].append((position,r['used_capacity'],int(r['special_slots'] is not None),*capacity_projection(sf['level'],r,sec.get('support_chips'))))
            for i,u in enumerate(r['units'],1):
                out['support_units.csv'].append((position,i,int(u is not None),u['unit_id'] if u else None,u['skill_id'] if u else None,skill_group_status(u['unit_id'],u['skill_id']) if u else 'empty_slot'))
                if u is not None:
                    out['support_unit_ammunition.csv'].extend((position,i,j,x,reference_status('support_ammunition',x)) for j,x in enumerate(u['ammunition_ids'],1))
            out['support_attributes.csv'].extend((position,a['attribute_id'],a['value'],*attribute_projection(sf['level'],r,sec.get('support_chips'),a['attribute_id'],a['value'])) for a in r['attributes'].values())
            for f in r['frames'].values():
                out['support_frames.csv'].append((position,f['frame_id']))
                out['support_frame_slots.csv'].extend((position,f['frame_id'],i,x,frame_slot_id(f['frame_id'],i)) for i,x in enumerate(f['slots'],1))
            if r['special_slots'] is not None:
                out['support_special_slots.csv'].extend((position,i,x) for i,x in enumerate(r['special_slots'],1))
    if 'support_chips' in sec:
        for kind in ('normal','special'):
            for r in sec['support_chips'][kind].values():
                attrs = r['attributes'] if kind == 'normal' else [None]*6
                out['support_chips.csv'].append((kind,r['uid'],r['model_id'],*attrs,r.get('current_player_amplification_level') if kind == 'special' else None,reference_status(kind,r['model_id']),*primary_projection(kind,r['model_id'],attrs),*secondary_projection(kind,r['model_id'],attrs),*amplification_projection(kind,r['model_id'],r.get('current_player_amplification_level')),secondary_value_status(kind,r['model_id'],attrs),*stage_skill_projection(kind,r['model_id'],r.get('current_player_amplification_level'))))
    if 'support_unit_ids' in sec:
        out['support_owned_units.csv'].extend((i,x,reference_status('support_unit',x)) for i,x in enumerate(sec['support_unit_ids'],1))
    if 'achievement_states' in sec:
        out['achievements.csv'].extend((r['achievement_id'],r['state_raw'],challenge_status(r['achievement_id'])) for r in sec['achievement_states']['rows'].values())
    return {name: _csv_bytes(name,rows) for name,rows in out.items()}


def _read(name, raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('CSV file too large: ' + name)
    try:
        stream = io.StringIO(raw.decode('utf-8-sig'),newline='')
        reader = csv.DictReader(stream,restkey='_extra',strict=True)
        if reader.fieldnames != HEADERS[name]:
            raise ValueError('CSV header mismatch: '+name)
        rows=[]
        for row in reader:
            if len(rows)>=MAX_ROWS or '_extra' in row or None in row or any(v is None for v in row.values()):
                raise ValueError('Invalid CSV row: ' + name)
            rows.append(row)
        return rows
    except (UnicodeDecodeError,csv.Error) as e:
        raise ValueError('CSV encoding error: ' + name) from e


def _num(value, *, nullable=False, zero=True):
    if value=='' and nullable:
        return None
    if not isinstance(value,str) or not value.isascii() or not value.isdecimal() or len(value)>20:
        raise ValueError('Invalid numeric field')
    result=int(value)
    if result>=2**64 or (not zero and result==0):
        raise ValueError('Numeric out of range')
    return result


def _ammunition(row):
    value = _num(row['ammunition_id'])
    if row['reference_status'] != reference_status('support_ammunition', value):
        raise ValueError('Support ammunition reference status mismatch')
    return value


def _flag(value):
    if value not in ('0','1'):
        raise ValueError('Invalid presence flag')
    return value=='1'


def _uid(value):
    result = _num(value,nullable=True,zero=False)
    return str(result) if result is not None else None


def _sequence(indexed):
    if not indexed:
        return []
    if sorted(indexed)!=list(range(1,len(indexed)+1)):
        raise ValueError('Missing or duplicate sequence index')
    return [indexed[i] for i in range(1,len(indexed)+1)]


def deserialize(files: dict[str,bytes], *, doll_ids=None, remolding_uids=None):
    """Rebuild validated document; rejects ambiguous/missing CSVs and bad refs."""
    if STATUS_FILE not in files:
        raise ValueError('account_status.csv not found')
    illegal=set(files)-OPTIONAL_FILES
    if illegal:
        raise ValueError('Unexpected account CSVs: '+repr(illegal))
    data={name:_read(name,raw) for name,raw in files.items()}
    statuses={}
    for row in data[STATUS_FILE]:
        if _num(row['format_version']) != CSV_SCHEMA:
            raise ValueError('Unsupported account CSV schema version')
        k=row['section']
        if k in statuses or k not in TYPES.values():
            raise ValueError('Invalid account section name')
        statuses[k]=row['status']
    if set(statuses)!=set(TYPES.values()):
        raise ValueError('Missing section status')
    observed={name for name,s in statuses.items() if s=='observed'}
    if any(v not in {'observed','missing','invalid','conflict','core_mismatch','reference_mismatch'} for v in statuses.values()):
        raise ValueError('Invalid capture status')
    expected={STATUS_FILE}
    for name in observed:
        expected.update(SECTION_FILES[name])
    # The pair of sidecars is optional for old captures, but must appear
    # together whenever an inactive strategy was observed.
    saved_pair = {'doll_saved_strategies.csv', 'doll_saved_strategy_skills.csv'}
    if saved_pair & set(data):
        if 'dolls' not in observed or not saved_pair <= set(data):
            raise ValueError('Incomplete saved strategy CSV pair')
        expected.update(saved_pair)
        if not data['doll_saved_strategies.csv']:
            raise ValueError('Empty saved strategy CSV pair')
    if set(data)!=expected:
        raise ValueError('Account dataset CSV set mismatch: '+str(sorted(expected-set(data)))+' unexpected='+str(sorted(set(data)-expected)))
    sec={}
    def rows(name):return data.get(name,[])
    def unique(name,keys):
        seen=set()
        for r in rows(name):
            tup=tuple(r[k] for k in keys)
            if tup in seen:
                raise ValueError('Duplicate account key: '+name)
            seen.add(tup)
    unique_keys = {
        STATUS_FILE: ('section',),
        'doll_progress.csv': ('doll_id',),
        'doll_core_slots.csv': ('doll_id','slot'),
        'doll_key_values.csv': ('doll_id','kind','position'),
        'doll_helix.csv': ('doll_id','position'),
        'doll_skills.csv': ('doll_id','position'),
        'doll_saved_strategies.csv': ('doll_id','strategy_position'),
        'doll_saved_strategy_skills.csv': ('doll_id','strategy_position','position'),
        'doll_costumes.csv': ('source','costume_id'),
        'remolding_details.csv': ('uid',),
        'remolding_options.csv': ('uid','position'),
        'support_settings.csv': ('level','selected_position_raw'),
        'support_formations.csv': ('position',),
        'support_units.csv': ('formation_position','slot_position'),
        'support_unit_ammunition.csv': ('formation_position','slot_position','position'),
        'support_attributes.csv': ('formation_position','attribute_id'),
        'support_frames.csv': ('formation_position','frame_id'),
        'support_frame_slots.csv': ('formation_position','frame_id','slot_position'),
        'support_special_slots.csv': ('formation_position','slot_position'),
        'support_ammunition.csv': ('position',),
        'support_chips.csv': ('kind','uid'),
        'support_owned_units.csv': ('position',),
        'achievements.csv': ('achievement_id',),
    }
    for name in data:
        unique(name, unique_keys[name])
    # Validate linked rows as we consume them, and reject every orphan later.
    consumed={name:set() for name in data}
    # Build one index per table/key shape on demand. Keep source order and
    # original row positions so duplicate/orphan checks remain unchanged.
    indexes = {}
    def take(name, **filters):
        if not filters:
            selected = list(enumerate(rows(name)))
        else:
            columns = tuple(filters)
            key = (name, columns)
            if key not in indexes:
                groups = {}
                for i, r in enumerate(rows(name)):
                    groups.setdefault(tuple(r[c] for c in columns), []).append((i, r))
                indexes[key] = groups
            selected = indexes[key].get(tuple(filters.values()), ())
        consumed[name].update(i for i, _ in selected)
        return [r for _, r in selected]
    consumed[STATUS_FILE]=set(range(len(data[STATUS_FILE])))
    if 'dolls' in observed:
        ds={}
        for dr in take('doll_progress.csv'):
            did=_num(dr['doll_id'],zero=False)
            if str(did) in ds:
                raise ValueError('Duplicate doll progress')
            aff={'level':_num(dr['affinity_level'],nullable=True),'cumulative_xp':_num(dr['affinity_cumulative_xp'],nullable=True)} if _flag(dr['affinity_present']) else None
            if aff is None and (dr['affinity_level'] or dr['affinity_cumulative_xp']):raise ValueError('Unexpected affinity')
            if dr['affinity_reference_status'] != affinity_status(aff):raise ValueError('Affinity reference status mismatch')
            core={'level':_num(dr['core_level'],nullable=True),'model_id':_num(dr['core_model_id'],nullable=True),'remolding_slots':None} if _flag(dr['core_present']) else None
            if core is None and (dr['core_level'] or dr['core_model_id'] or _flag(dr['core_slots_present'])):raise ValueError('Unexpected core')
            if dr['core_model_reference_status'] != core_model_status(did,core):raise ValueError('Core model reference status mismatch')
            if core is not None and _flag(dr['core_slots_present']):
                slots=take('doll_core_slots.csv',doll_id=str(did))
                if len(slots)!=6:raise ValueError('Exactly six core slots required')
                core['remolding_slots']=[_uid(r['uid']) for r in indexed_rows(slots,'slot')]
            skill={'mode_raw':_num(dr['skill_mode_raw'],nullable=True),'submode_raw':_num(dr['skill_submode_raw'],nullable=True),'entries':[], 'variant_count':_num(dr['skill_variant_count']), 'active_variant_position':_num(dr['skill_active_variant_position'],nullable=True,zero=False)} if _flag(dr['skill_present']) else None
            if skill is None and any(dr[k] for k in ('skill_mode_raw','skill_submode_raw','skill_variant_count','skill_active_variant_position','skill_active_entry_count')):raise ValueError('Unexpected skill mode')
            if movement_projection(skill['mode_raw'] if skill else None) != (_num(dr['movement_directive_code_candidate'],nullable=True),dr['movement_directive_status']):
                raise ValueError('movement directive projection mismatch')
            if skill is not None:
                entries=take('doll_skills.csv',doll_id=str(did))
                skill['entries']=[{'skill_id':_num(r['skill_id'],zero=False),'parameter_raw':_num(r['parameter_raw'],nullable=True)} for r in indexed_rows(entries,'position')]
                if len(skill['entries']) != _num(dr['skill_active_entry_count']):
                    raise ValueError('active skill count mismatch')
                if saved_pair <= set(data):
                    variants = indexed_rows(take('doll_saved_strategies.csv',
                                                 doll_id=str(did)),
                                            'strategy_position')
                    if variants:
                        skill['saved_variants_raw'] = []
                        for position, variant in enumerate(variants, 1):
                            saved_entries = indexed_rows(take('doll_saved_strategy_skills.csv',
                                doll_id=str(did), strategy_position=str(position)), 'position')
                            if len(saved_entries) != _num(variant['skill_count']):
                                raise ValueError('saved skill count mismatch')
                            skill['saved_variants_raw'].append({
                                'submode_raw': _num(variant['submode_raw'], zero=False),
                                'entries': [{'skill_id': _num(item['skill_id'], zero=False),
                                             'parameter_raw': _num(item['parameter_raw'], nullable=True)}
                                            for item in saved_entries]})
            doll={'doll_id':did,'costume_id':_num(dr['costume_id'],nullable=True),'costume_state_raw':_num(dr['costume_state_raw'],nullable=True),'affinity':aff,'core':core,'skill_setting_raw':skill,'expansion_stage_raw':_num(dr['expansion_stage_raw'],nullable=True)}
            for field,kind in KINDS.items():
                present=_flag(dr[kind+'_present'])
                chosen=take('doll_key_values.csv',doll_id=str(did), kind=kind)
                if chosen and not present:raise ValueError('Orphan key rows')
                doll[field]=[(_uid(r['value']) if 'uids' in kind else _num(r['value'])) for r in indexed_rows(chosen,'position')] if present else None
            hv=take('doll_helix.csv',doll_id=str(did))
            hp=_flag(dr['helix_present'])
            if hv and not hp:raise ValueError('Orphan helix rows')
            doll['helix_values_raw']=[_num(r['value_raw']) for r in indexed_rows(hv,'position')] if hp else None
            for row in hv:
                count, status = helix_mask_projection(_num(row['value_raw']))
                if row['mask_set_bit_count_candidate'] != ('' if count is None else str(count)) or row['mask_pattern_status'] != status:
                    raise ValueError('invalid helix mask projection')
                group, nodes, ref_status = group_projection(did, _num(row['value_raw']))
                if (row['helix_group_id'],row['static_node_count'],row['helix_group_reference_status']) != ('' if group is None else str(group),'' if nodes is None else str(nodes),ref_status):
                    raise ValueError('invalid helix group projection')
            ds[str(did)]=doll
        wa={}
        for r in take('doll_costumes.csv',source='11101'):
            cid=_num(r['costume_id'],zero=False)
            if str(cid) in wa:raise ValueError('Duplicate costume')
            wa[str(cid)]={'costume_id':cid,'state_raw':_num(r['state_raw'],nullable=True)}
        sec['dolls']={'rows':ds,'wardrobe_11101':wa}
    if 'wardrobe_11627' in observed:
        wa={}
        for r in take('doll_costumes.csv',source='11627'):
            cid=_num(r['costume_id'],zero=False)
            if str(cid) in wa:raise ValueError('Duplicate costume')
            wa[str(cid)]={'costume_id':cid,'state_raw':_num(r['state_raw'],nullable=True)}
        sec['wardrobe_11627']=wa
    if 'remoldings' in observed:
        result={}
        for r in take('remolding_details.csv'):
            uid=_uid(r['uid'])
            if uid in result:raise ValueError('Duplicate remolding')
            opts=take('remolding_options.csv',uid=uid)
            model_id=_num(r['model_id'],zero=False)
            if r['model_reference_status'] != model_status(model_id):raise ValueError('Remolding model reference status mismatch')
            option_ids=[]
            for x in indexed_rows(opts,'position'):
                option_id=_num(x['option_id'])
                expected=option_projection(option_id)
                if (_num(x['feature_group_id'],nullable=True),_num(x['option_level'],nullable=True),x['option_reference_status']) != expected:
                    raise ValueError('Remolding option reference mismatch')
                option_ids.append(option_id)
            result[uid]={'uid':uid,'model_id':model_id,'option_ids':option_ids}
        sec['remoldings']=result
    if 'support_formations' in observed:
        settings=rows('support_settings.csv')
        if len(settings)!=1:raise ValueError('One support settings row required')
        consumed['support_settings.csv'].add(0)
        sf={'level':_num(settings[0]['level'],nullable=True),'selected_position_raw':_num(settings[0]['selected_position_raw'],nullable=True),'ammunition_ids':[_ammunition(r) for r in indexed_rows(take('support_ammunition.csv'),'position')], 'formations':[]}
        if settings[0]['level_reference_status'] != room_level_status(sf['level']):raise ValueError('Support room level reference status mismatch')
        for form in indexed_rows(take('support_formations.csv'),'position'):
            pos=_num(form['position'],zero=False)
            un=[]
            for r in indexed_rows(take('support_units.csv',formation_position=str(pos)),'slot_position'):
                extras=take('support_unit_ammunition.csv',formation_position=str(pos), slot_position=r['slot_position'])
                if _flag(r['present']):
                    unit_id,skill_id = _num(r['unit_id'],nullable=True),_num(r['skill_id'],nullable=True)
                    if r['skill_group_reference_status'] != skill_group_status(unit_id,skill_id):raise ValueError('Support skill group reference status mismatch')
                    un.append({'unit_id':unit_id,'skill_id':skill_id,'ammunition_ids':[_ammunition(x) for x in indexed_rows(extras,'position')]})
                else:
                    if extras or r['unit_id'] or r['skill_id'] or r['skill_group_reference_status']!='empty_slot':raise ValueError('Orphan support unit')
                    un.append(None)
            attrs={}
            for r in take('support_attributes.csv',formation_position=str(pos)):
                aid=_num(r['attribute_id'],zero=False)
                if str(aid) in attrs:raise ValueError('Duplicate attribute')
                attrs[str(aid)]={'attribute_id':aid,'value':_num(r['value'],nullable=True)}
            frames={}
            for frame in take('support_frames.csv',formation_position=str(pos)):
                fid=_num(frame['frame_id'],zero=False)
                if str(fid) in frames:raise ValueError('Duplicate support frame')
                slots=take('support_frame_slots.csv',formation_position=str(pos), frame_id=str(fid))
                if len(slots)!=7:raise ValueError('Each frame must have seven slots')
                ordered = indexed_rows(slots,'slot_position')
                if any(_num(x['static_slot_id'],nullable=True,zero=False)!=frame_slot_id(fid,i) for i,x in enumerate(ordered,1)):raise ValueError('Support static slot reference mismatch')
                frames[str(fid)]={'frame_id':fid,'slots':[_uid(x['uid']) for x in ordered]}
            ss=take('support_special_slots.csv',formation_position=str(pos))
            if ss and not _flag(form['special_slots_present']):raise ValueError('Unexpected special slot')
            sf['formations'].append({'position':pos,'units':un,'attributes':attrs,'used_capacity':_num(form['used_capacity'],nullable=True),'frames':frames,'special_slots':[_uid(x['uid']) for x in indexed_rows(ss,'slot_position')] if _flag(form['special_slots_present']) else None})
        sec['support_formations']=sf
    if 'support_chips' in observed:
        chips={'normal':{},'special':{}}
        for r in take('support_chips.csv'):
            kind=r['kind']
            if kind not in chips:raise ValueError('Invalid chip kind')
            uid=_uid(r['uid'])
            if uid in chips[kind]:raise ValueError('Duplicate chip UID')
            obj={'uid':uid,'model_id':_num(r['model_id'],zero=False)}
            if r['model_reference_status'] != reference_status(kind,obj['model_id']):raise ValueError('Support chip model reference status mismatch')
            if kind=='normal':
                if r['player_amplification_level']:raise ValueError('Unexpected normal amplification')
                obj['attributes']=[_num(r['attr_'+str(i)]) for i in range(1,7)]
            else:
                if any(r['attr_'+str(i)] for i in range(1,7)):raise ValueError('Unexpected special attributes')
                # Explicit captured Lv.2+ may be present; blank means unknown.
                # Do not fabricate Lv.1 when protobuf field 5 was omitted.
                level=_num(r['player_amplification_level'],nullable=True)
                if level is not None and not 1<=level<=6:
                    raise ValueError('Invalid special amplification level')
                obj['current_player_amplification_level']=level
            if (_num(r['primary_attribute_id'],nullable=True),_num(r['primary_base_value'],nullable=True),r['primary_reference_status']) != primary_projection(kind,obj['model_id'],obj.get('attributes')):
                raise ValueError('Support primary attribute reference mismatch')
            if (_num(r['secondary_attribute_count_expected'],nullable=True),_num(r['secondary_attribute_count_observed'],nullable=True),r['secondary_count_reference_status']) != secondary_projection(kind,obj['model_id'],obj.get('attributes')):
                raise ValueError('Support secondary attribute count mismatch')
            if (_num(r['amplification_break_record_id'],nullable=True),r['amplification_reference_status']) != amplification_projection(kind,obj['model_id'],obj.get('current_player_amplification_level')):
                raise ValueError('Support amplification reference mismatch')
            if r['secondary_value_reference_status'] != secondary_value_status(kind,obj['model_id'],obj.get('attributes')):
                raise ValueError('Support secondary value projection mismatch')
            if (_num(r['amplification_skill_group_id'],nullable=True),_num(r['amplification_skill_id'],nullable=True)) != stage_skill_projection(kind,obj['model_id'],obj.get('current_player_amplification_level')):
                raise ValueError('Support stage skill reference mismatch')
            chips[kind][uid]=obj
        sec['support_chips']=chips
    if 'support_unit_ids' in observed:
        owned=[]
        for r in indexed_rows(take('support_owned_units.csv'),'position'):
            unit_id=_num(r['unit_id'],zero=False)
            if r['reference_status'] != reference_status('support_unit',unit_id):raise ValueError('Support owned unit reference status mismatch')
            owned.append(unit_id)
        sec['support_unit_ids']=owned
    if 'achievement_states' in observed:
        ac={}
        for r in take('achievements.csv'):
            aid=_num(r['achievement_id'],zero=False)
            if str(aid) in ac:raise ValueError('Duplicate achievement')
            if r['challenge_reference_status'] != challenge_status(aid):raise ValueError('Achievement challenge reference status mismatch')
            ac[str(aid)]={'achievement_id':aid,'state_raw':_num(r['state_raw'],nullable=True)}
        sec['achievement_states']={'rows':ac,'counted_state1':sum(r['state_raw']==1 for r in ac.values())}
    for name,rr in data.items():
        if consumed[name]!=set(range(len(rr))):
            raise ValueError('Unreferenced rows: '+name)
    # Recompute cross-source projections only after both independent snapshots
    # have been reconstructed. A mismatch is diagnostic, not ownership proof.
    wardrobe11101 = sec.get('dolls', {}).get('wardrobe_11101')
    wardrobe11627 = sec.get('wardrobe_11627')
    for row in data.get('doll_progress.csv', []):
        expected = current_projection(_num(row['doll_id']), _num(row['costume_id'],nullable=True),
                                      _num(row['costume_state_raw'],nullable=True), wardrobe11101)
        if expected != (row['costume_model_reference_status'], row['costume_wardrobe_state_status']):
            raise ValueError('Invalid current costume projection')
    for row in data.get('doll_costumes.csv', []):
        peer = wardrobe11627 if row['source'] == '11101' else wardrobe11101
        owner, ref, relation = wardrobe_projection(_num(row['costume_id']),_num(row['state_raw'],nullable=True),peer)
        if ('' if owner is None else str(owner), ref, relation) != (row['static_doll_id'],row['costume_model_reference_status'],row['peer_state_status']):
            raise ValueError('Invalid wardrobe projection')
    for name in ('doll_skills.csv','doll_saved_strategy_skills.csv'):
        for row in data.get(name, []):
            validate_param(row, _num(row['parameter_raw'], nullable=True))
    for name, state_col in (('doll_progress.csv','costume_state_raw'), ('doll_costumes.csv','state_raw')):
        for row in data.get(name, []):
            expected = part_projection(_num(row['costume_id'],nullable=True),_num(row[state_col],nullable=True))
            actual = tuple(_num(row[k],nullable=True) for k in PART_COLUMNS[:-1]) + (row[PART_COLUMNS[-1]],)
            if expected != actual:
                raise ValueError('Invalid costume part projection')
            if name == 'doll_progress.csv' and conditional_directive_status(_num(row['skill_mode_raw'],nullable=True)) != row['conditional_directive_status']:
                raise ValueError('Invalid conditional directive projection')
    sf = sec.get('support_formations')
    if sf is not None:
        formations = {f['position']: f for f in sf['formations']}
        for row in data.get('support_formations.csv', []):
            expected = capacity_projection(sf['level'], formations[_num(row['position'])], sec.get('support_chips'))
            if expected != (_num(row['level_capacity_candidate'],nullable=True),_num(row['occupied_slot_capacity_candidate'],nullable=True),row['capacity_comparison_status']):
                raise ValueError('Support capacity projection mismatch')
        for row in data.get('support_attributes.csv', []):
            expected = attribute_projection(sf['level'], formations[_num(row['formation_position'])], sec.get('support_chips'), _num(row['attribute_id']), _num(row['value'],nullable=True))
            if expected != (_num(row['level_base_candidate'],nullable=True), _num(row['normal_chip_sum'],nullable=True), row['attribute_sum_status']):
                raise ValueError('Support attribute sum projection mismatch')
    return validate_document({'schema_id':'gf2-account-extension','schema_version':VERSION,'sections':sec,'status':statuses},doll_ids=doll_ids,remolding_uids=remolding_uids)


def indexed_rows(rows,index):
    indexed={}
    for r in rows:
        i=_num(r[index],zero=False)
        if i in indexed:raise ValueError('Duplicate index')
        indexed[i]=r
    return _sequence(indexed)


def import_files(root:Path, *, doll_ids=None, remolding_uids=None):
    if not (root/STATUS_FILE).exists():
        return None
    files={name:(root/name).read_bytes() for name in HEADERS if (root/name).is_file()}
    return deserialize(files,doll_ids=doll_ids,remolding_uids=remolding_uids)
