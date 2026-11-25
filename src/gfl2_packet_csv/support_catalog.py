"""Versioned static ID cross-checks, without inferring game actions."""
from __future__ import annotations
import csv
from .reference_data import lru_cache
from .reference_data import files

@lru_cache(maxsize=1)
def catalogs():
    result = {}
    root = files('gfl2_packet_csv.static_tables')
    for name, kind_col, id_col in (
        ('support_room_static_ko.csv', 'reference_kind', 'reference_id'),
        ('support_chip_model_reference_ids.csv', 'chip_kind', 'model_id'),
    ):
        try:
            with root.joinpath(name).open('r', encoding='utf-8-sig', newline='') as f:
                partial = {}
                for row in csv.DictReader(f):
                    kind, ident = row[kind_col], int(row[id_col])
                    if ident <= 0: raise ValueError('invalid catalog ID')
                    partial.setdefault(kind, set()).add(ident)
            result.update({k: frozenset(v) for k, v in partial.items()})
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return result

def reference_status(kind, value):
    if value is None: return 'field_absent'
    if value == 0: return 'explicit_zero'
    refs = catalogs().get(kind)
    if not refs: return 'catalog_unavailable'
    return 'catalog_consistent' if value in refs else 'unrecognized'

@lru_cache(maxsize=1)
def group_references():
    root = files('gfl2_packet_csv.static_tables')
    maps = []
    for filename, key, value in (
        ('support_unit_group_reference.csv','unit_id','skill_group_id'),
        ('support_skill_group_reference.csv','skill_id','skill_group_id'),
    ):
        try:
            with root.joinpath(filename).open('r',encoding='utf-8-sig',newline='') as f:
                rows = list(csv.DictReader(f))
            mapping = {int(r[key]):int(r[value]) for r in rows}
            if len(mapping) != len(rows) or any(k <= 0 or v <= 0 for k,v in mapping.items()):
                raise ValueError('invalid group reference')
            maps.append(mapping)
        except (OSError, ValueError, KeyError, TypeError):
            maps.append({})
    return tuple(maps)


def skill_group_status(unit_id, skill_id):
    if unit_id is None or skill_id is None:
        return 'field_absent'
    if not unit_id or not skill_id:
        return 'explicit_zero'
    units, skills = group_references()
    if not units or not skills:
        return 'catalog_unavailable'
    if unit_id not in units or skill_id not in skills:
        return 'unrecognized'
    return 'catalog_consistent' if units[unit_id] == skills[skill_id] else 'group_mismatch'


@lru_cache(maxsize=1)
def frame_slots():
    try:
        with files('gfl2_packet_csv.static_tables').joinpath('support_frame_slot_reference.csv').open('r',encoding='utf-8-sig',newline='') as f:
            rows = list(csv.DictReader(f))
        mapping = {(int(r['frame_id']),int(r['slot_position'])):int(r['static_slot_id']) for r in rows}
        if len(mapping) != len(rows) or len(set(mapping.values())) != len(rows) or any(a<=0 or not 1<=b<=7 or v<=0 for (a,b),v in mapping.items()):
            return {}
        if any((frame,slot) not in mapping for frame,_ in mapping for slot in range(1,8)):
            return {}
        return mapping
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def frame_slot_id(frame_id, position):
    return frame_slots().get((frame_id,position))


@lru_cache(maxsize=1)
def room_levels():
    try:
        with files('gfl2_packet_csv.static_tables').joinpath('support_level_static_reference.csv').open('r',encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f))
        refs=frozenset(int(r['field_1_raw']) for r in rows if r['field_1_raw']==r['field_2_raw'])
        return refs if len(refs)==len(rows) and all(x>0 for x in refs) else frozenset()
    except (OSError, ValueError, KeyError, TypeError):
        return frozenset()


def room_level_status(level):
    if level is None: return 'field_absent'
    if level == 0: return 'explicit_zero'
    refs=room_levels()
    if not refs: return 'catalog_unavailable'
    return 'catalog_consistent' if level in refs else 'unrecognized'


@lru_cache(maxsize=1)
def special_break_records():
    """Static stage position -> break record ID; no inference of absent account level."""
    try:
        with files('gfl2_packet_csv.static_tables').joinpath('support_chip_core_break_references.csv').open('r', encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        result = {}
        for row in rows:
            model, level, rid = (int(row[k]) for k in ('model_id','break_position','break_record_id'))
            present = row['stage_field_3_present']; stage = row['stage_field_3_raw']
            if model <= 0 or rid <= 0 or not 1 <= level <= 6 or (model,level) in result or row['matching_model_field_2'] != '1':
                return {}
            if (present, stage) != (('0','') if level == 1 else ('1',str(level-1))):
                return {}
            result[(model,level)] = rid
        models = {m for m,_ in result}
        if not models or models != catalogs().get('special') or any((m,l) not in result for m in models for l in range(1,7)):
            return {}
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def amplification_projection(kind, model_id, level):
    if kind != 'special':
        return None, 'not_applicable'
    if level is None:
        return None, 'field_absent'
    refs = special_break_records()
    if not refs:
        return None, 'catalog_unavailable'
    rid = refs.get((model_id,level))
    if rid is None:
        return None, 'unrecognized_model_or_stage'
    return rid, 'catalog_consistent_explicit_stage'
