"""Offline direct raw-to-CSV audit of a complete 11101 snapshot.

Unknown scalar values are compared only in memory; results contain counts,
not identifiers or raw account values. No semantic promotion is performed.
This is not intended for merged/sparse collector updates.
"""
from collections import Counter, defaultdict
from .wire_probe import parse_fields, _varint
from .doll_growth_catalog import class_projection, rank_projection

UNKNOWN = (2, 4, 9, 12, 41, 44, 47, 61)
KEYS = {42: ('fixed_keys', 'fixed_keys_present'),
        43: ('common_keys', 'common_keys_present'),
        45: ('common_uids', 'common_uids_present'),
        52: ('expansion_keys', 'expansion_keys_present'),
        59: ('bond_keys', 'bond_keys_present'),
        60: ('bond_uids', 'bond_uids_present')}


def _fields(data):
    result = defaultdict(list)
    for n, w, value in parse_fields(data):
        result[n].append((w, value))
    return result


def _scalar(fields, n, wire=0):
    entries = fields.get(n, ())
    if not entries:
        return None
    if len(entries) != 1 or entries[0][0] != wire:
        raise ValueError('Invalid singular doll field')
    return entries[0][1]


def _vector(fields, n):
    if n not in fields:
        return None
    out = []
    for wire, value in fields[n]:
        if wire == 0:
            out.append(value)
        elif wire == 2:
            pos = 0
            while pos < len(value):
                val, pos = _varint(value, pos)
                out.append(val)
        else:
            raise ValueError('Invalid doll vector wire type')
    return tuple(out)


def _dolls(payload):
    top = _fields(payload)
    out = {}
    for wire, value in top.get(1, ()):
        if wire != 2:
            raise ValueError('Invalid doll envelope')
        row = _fields(value)
        key = _scalar(row, 1)
        if not key or key in out:
            raise ValueError('Missing or duplicate doll ID')
        out[key] = row
    if not out:
        raise ValueError('No ordinary doll records')
    return out


def _int(s):
    return None if s == '' else int(s)


def _index(rows, key_cols):
    result = {}
    for row in rows:
        key = tuple(row[k] if k == 'kind' else int(row[k]) for k in key_cols)
        if key in result:
            raise ValueError('Duplicate CSV audit key')
        result[key] = row
    return result


def _csv_vector(index, prefix, column):
    selected = sorted((key[-1], _int(r[column])) for key, r in index.items() if key[:-1] == prefix)
    if [p for p, _ in selected] != list(range(1, len(selected)+1)):
        raise ValueError('Noncontiguous CSV audit positions')
    return tuple(v for _, v in selected)


def audit_doll_snapshot(payload: bytes, rows: dict) -> dict:
    """Compare direct projections and raw list ordering; return counts per path.

    Missing row and mismatched value are distinct. List presence is checked
    separately from values; UID zero placeholders project as empty cells.
    """
    dolls = _dolls(payload)
    tables = {name: _index(rows[name], cols) for name, cols in (
        ('dolls.csv', ('doll_id',)), ('doll_progress.csv', ('doll_id',)),
        ('doll_key_values.csv', ('doll_id', 'kind', 'position')),
        ('doll_helix.csv', ('doll_id', 'position')),
        ('doll_core_slots.csv', ('doll_id', 'slot')))}
    checks = defaultdict(Counter)
    expected_keys = defaultdict(set)
    unknown = {f: Counter() for f in UNKNOWN}
    relationships = Counter()

    def check(path, expected, observed, *, missing=False):
        checks[path]['missing' if missing else 'match' if expected == observed else 'mismatch'] += 1

    for did, fields in dolls.items():
        prefix = (did,)
        d = tables['dolls.csv'].get(prefix)
        p = tables['doll_progress.csv'].get(prefix)
        for name in ('dolls.csv', 'doll_progress.csv'):
            expected_keys[name].add(prefix)
        def scalar_check(path, val, row, col):
            check(path, val, _int(row[col]) if row is not None else None, missing=row is None)
        def text_check(path, val, row, col):
            check(path, val, row[col] if row is not None else None, missing=row is None)
        for f, col in ((1, 'doll_id'), (3, 'level'), (6, 'rank'), (62, 'weapon_uid')):
            scalar_check(str(f), _scalar(fields, f), d, col)
        stage, status = class_projection(_scalar(fields, 31), _scalar(fields, 3))
        scalar_check('31.projection', stage, d, 'class_stage')
        text_check('31.status', status, d, 'class_stage_reference_status')
        grade, status = rank_projection(did, _scalar(fields, 6))
        scalar_check('6.grade_candidate', grade, d, 'grade_record_id_candidate')
        text_check('6.status', status, d, 'rank_reference_status')
        for f, col in ((5, 'costume_id'), (54, 'costume_state_raw'), (58, 'expansion_stage_raw')):
            scalar_check(str(f), _scalar(fields, f), p, col)
        core_raw = _scalar(fields, 55, 2)
        core = _fields(core_raw) if core_raw is not None else {}
        scalar_check('55.present', int(core_raw is not None), p, 'core_present')
        for f, col in ((1, 'core_level'), (3, 'core_model_id')):
            scalar_check(f'55.{f}', _scalar(core, f), p, col)
        for field, (kind, presence) in KEYS.items():
            vals = _vector(fields, field)
            scalar_check(f'{field}.present', int(vals is not None), p, presence)
            expected = tuple(None if not v and field in (45, 60) else v for v in (vals or ()))
            key = (did, kind)
            observed = _csv_vector(tables['doll_key_values.csv'], key, 'value')
            check(str(field), expected, observed)
            expected_keys['doll_key_values.csv'].update(key+(i,) for i in range(1,len(expected)+1))
        for path, vals, name, col, presence in (
            ('51', _vector(fields, 51), 'doll_helix.csv', 'value_raw', 'helix_present'),
            ('55.2', _vector(core, 2), 'doll_core_slots.csv', 'uid', 'core_slots_present')):
            scalar_check(path+'.present', int(vals is not None), p, presence)
            expected = tuple(None if not v and path == '55.2' else v for v in (vals or ()))
            check(path, expected, _csv_vector(tables[name], prefix, col))
            expected_keys[name].update(prefix+(i,) for i in range(1,len(expected)+1))
        values = {f: _scalar(fields, f) for f in UNKNOWN}
        for f, v in values.items():
            unknown[f]['present' if v is not None else 'absent'] += 1
        relationships['44_47_presence_mismatch'] += (values[44] is None) != (values[47] is None)
        relationships['44_47_both_present'] += values[44] is not None and values[47] is not None
        relationships['44_47_value_mismatch'] += values[44] is not None and values[47] is not None and values[44] != values[47]
        relationships['44_helix_presence_mismatch'] += (values[44] is not None) != (51 in fields)
        relationships['47_helix_presence_mismatch'] += (values[47] is not None) != (51 in fields)
        relationships['44_present_fixed_slots_empty'] += values[44] is not None and not any(_vector(fields, 42) or ())
        relationships['47_present_common_slots_empty'] += values[47] is not None and not any(_vector(fields, 45) or ())
    return {'dolls': len(dolls), 'checks': {k: {t: v[t] for t in ('match','mismatch','missing')} for k,v in checks.items()},
            'extra_csv_rows': {k: len(set(t)-expected_keys[k]) for k,t in tables.items()},
            'unknown_presence': {str(k): {t: v[t] for t in ('present','absent')} for k,v in unknown.items()},
            'relationships': dict(relationships)}


def compare_unknown_doll_fields(before: bytes, after: bytes) -> dict:
    """Compare unknown fields across two full snapshots without returning values."""
    left, right = _dolls(before), _dolls(after)
    common = left.keys() & right.keys()
    fields = {}
    for f in UNKNOWN:
        pairs = [(_scalar(left[k],f),_scalar(right[k],f)) for k in common]
        fields[str(f)] = {'both_absent':sum(a is None and b is None for a,b in pairs),
                          'unchanged_present':sum(a is not None and a==b for a,b in pairs),
                          'added':sum(a is None and b is not None for a,b in pairs),
                          'removed':sum(a is not None and b is None for a,b in pairs),
                          'changed_present':sum(a is not None and b is not None and a!=b for a,b in pairs)}
    return {'common_dolls':len(common),'added_dolls':len(right.keys()-left.keys()),
            'removed_dolls':len(left.keys()-right.keys()),'fields':fields}
