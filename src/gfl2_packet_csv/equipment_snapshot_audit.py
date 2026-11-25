"""Read-only complete snapshot comparisons. Results contain counts, not account IDs.

Compare source fields to CSV projections, not merged partial updates. Catalog
matches are evidence of reference consistency, not proof of game semantics.
"""
from collections import Counter, defaultdict
from .doll_snapshot_audit import _fields, _scalar, _vector, _index, _int
from .weapon_skin_catalog import known_weapon_skin_ids, known_weapon_part_skin_ids
from .growth_cost_catalog import weapon_model
from .support_catalog import reference_status, skill_group_status


def _messages(fields, n):
    for wire, raw in fields.get(n, ()):
        if wire != 2:
            raise ValueError('Invalid record wire type')
        yield _fields(raw)


def _keyed(fields, n):
    out = {}
    for record in _messages(fields, n):
        key = _scalar(record, 1)
        if not key or key in out:
            raise ValueError('Missing or duplicate record ID')
        out[key] = record
    return out


class _Audit:
    def __init__(self, rows, schemas):
        self.tables = {n: _index(rows[n], cols) for n, cols in schemas}
        self.expected = defaultdict(set)
        self.checks = defaultdict(Counter)

    def row(self, name, key, fields):
        self.expected[name].add(key)
        row = self.tables[name].get(key)
        for col, expected in fields.items():
            value = row[col] if row is not None else None
            if row is not None and (expected is None or isinstance(expected, int)):
                value = _int(value)
            self.checks[name + ':' + col]['missing' if row is None else
                'match' if expected == value else 'mismatch'] += 1

    def result(self):
        return {'checks': {k: {x: v[x] for x in ('match','mismatch','missing')}
                           for k,v in sorted(self.checks.items())},
                'extra_csv_rows': {k: len(set(t)-self.expected[k]) for k,t in self.tables.items()}}


def _skin(value, catalog):
    if value is None:
        return None, 'field_absent'
    if value == 0:
        return None, 'explicit_zero'
    return (value,'catalog_verified') if value in catalog else (None,'unrecognized')


def audit_weapon_snapshot(payload, rows):
    """Audit full 11021 inventory. Missing/suppressed slot CSV is not a pass."""
    top = _fields(payload)
    weapons = _keyed(top,1)
    owners = _keyed(top,2)
    if not weapons or set(owners)-set(weapons):
        raise ValueError('Invalid weapon inventory or owner link')
    if any(_scalar(r,2) is None for r in owners.values()):
        raise ValueError('Missing owner field')
    audit = _Audit(rows, [('weapons.csv',('uid',)),
                          ('weapon_slot_observations.csv',('weapon_uid','wire_position'))])
    counts = Counter()
    groups = defaultdict(list)
    for uid, r in weapons.items():
        model, level, rank, f7 = (_scalar(r,n) for n in (2,3,6,7))
        if not model or level is None or rank is None:
            raise ValueError('Complete weapon record required')
        skin, status = _skin(_scalar(r,5), known_weapon_skin_ids())
        audit.row('weapons.csv',(uid,),dict(item_id=model, level=level, rank=rank,
                  equipped_doll_id=_scalar(owners[uid],2) if uid in owners else 0,
                  weapon_skin_item_id=skin,weapon_skin_reference_status=status))
        counts['field7_present' if f7 is not None else 'field7_absent'] += 1
        if f7 is not None:
            static = weapon_model(model)
            counts['field7_grade_unknown' if static is None else
                   'field7_within_grade' if 1 <= f7 <= static.grade_code else 'field7_outside_grade'] += 1
            counts['field7_equals_rank'] += f7 == rank
            groups[(model,level,rank)].append(f7)
        for i, slot in enumerate(_messages(r,8),1):
            if set(slot)-{1,2}:
                raise ValueError('Unsupported weapon slot structure')
            part_skin, ps = _skin(_scalar(slot,2),known_weapon_part_skin_ids())
            audit.row('weapon_slot_observations.csv',(uid,i),dict(attachment_uid=_scalar(slot,1),
                      attachment_skin_item_id=part_skin,attachment_skin_reference_status=ps))
            counts['slot_positions'] += 1
    counts['same_model_level_rank_groups_with_distinct_field7'] = sum(len(set(v))>1 for v in groups.values())
    return {'weapons':len(weapons),'owner_records':len(owners),'observations':dict(counts),**audit.result()}


def audit_support_snapshot(owned_payload, formation_payload, rows):
    """Audit owned-unit sequence and per-formation unit/skill/ammo fields.

    No inference of individual unit level from skill ID or shared room level.
    """
    owned_fields = _fields(owned_payload)
    owned = _vector(owned_fields,1)
    if owned is None:
        raise ValueError('Owned-unit field not observed')
    raw = _scalar(_fields(formation_payload),1,2)
    if raw is None:
        raise ValueError('Support formation field not observed')
    base = _fields(raw)
    audit = _Audit(rows,[('support_owned_units.csv',('position',)),
                         ('support_units.csv',('formation_position','slot_position')),
                         ('support_unit_ammunition.csv',('formation_position','slot_position','position'))])
    for i, unit in enumerate(owned,1):
        audit.row('support_owned_units.csv',(i,),dict(unit_id=unit,reference_status=reference_status('support_unit',unit)))
    counts = Counter()
    for i, formation in enumerate(_messages(base,1),1):
        counts['formations'] += 1
        for j, unit in enumerate(_messages(formation,2),1):
            counts['slots'] += 1
            counts['nonempty_slots'] += bool(unit)
            counts['slots_with_other_fields'] += bool(set(unit)-{1,2,3})
            uid, skill = _scalar(unit,1),_scalar(unit,2)
            audit.row('support_units.csv',(i,j),dict(present=int(bool(unit)),unit_id=uid,skill_id=skill,
                  skill_group_reference_status=skill_group_status(uid,skill) if unit else 'empty_slot'))
            for k, ammo in enumerate(_vector(unit,3) or (),1):
                counts['ammunition_positions'] += 1
                audit.row('support_unit_ammunition.csv',(i,j,k),dict(ammunition_id=ammo,reference_status=reference_status('support_ammunition',ammo)))
    if not counts['formations']:
        raise ValueError('No support formations')
    return {'owned_positions':len(owned),'owned_other_fields':len(set(owned_fields)-{1}),
            'observations':dict(counts),**audit.result()}
