"""Verified repeated static pairs; no account state or inferred effect arithmetic.
None means unknown/unavailable, while () means the record has no such field.
"""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from .attachment_affixes import _rows

_FIELDS = MappingProxyType({'GunClassData':(7,), 'GunWeaponData':(25,26),
                            'SupportConfigData':(10,), 'SupportData':(11,),
                            'SupportSkillGroupData':(4,15)})

@dataclass(frozen=True)
class StaticPair:
    position: int
    key_raw: int
    value_raw: int


def _load_counts():
    counts = {}
    for row in _rows('static_pair_counts.csv'):
        table = row['table_name']
        rid, field, count = (int(row[n]) for n in ('record_id','field_number','pair_count'))
        k = table,rid,field
        if rid <= 0 or field not in _FIELDS.get(table,()) or not 0 <= count <= 100 or k in counts:
            raise ValueError('invalid count row')
        counts[k] = count
    # Exact cross-joins to the existing static model catalogs, not just row totals.
    expected = {
        'GunClassData':{int(r['class_record_id']) for r in _rows('doll_stat_class_reference.csv')},
        'GunWeaponData':{int(r['model_id']) for r in _rows('weapon_exp_model_reference.csv')},
        'SupportConfigData':{7},
        'SupportData':{int(r['unit_id']) for r in _rows('support_unit_group_reference.csv')},
        'SupportSkillGroupData':{int(r['skill_id']) for r in _rows('support_skill_group_reference.csv')},
    }
    if not all(expected.values()) or set(counts)!={(t,r,f) for t,ids in expected.items() for r in ids for f in _FIELDS[t]}:
        raise ValueError('missing or extra record/field reference')
    return counts


@lru_cache(maxsize=1)
def catalog():
    try:
        counts = _load_counts()
        grouped = {k:[] for k in counts}
        items = {int(r['item_id']) for r in _rows('item_reference.csv')}
        for row in _rows('static_pair_values.csv'):
            table = row['table_name']
            rid, field, pos, key, value = (int(row[n]) for n in ('record_id','field_number','position','key_raw','value_raw'))
            k = table,rid,field
            if k not in grouped or not 1 <= pos <= counts[k] or min(key,value)<0:
                raise ValueError('invalid pair row')
            if table in ('GunClassData','GunWeaponData') and (key not in items or value<=0):
                raise ValueError('invalid item pair')
            grouped[k].append(StaticPair(pos,key,value))
        for k, entries in grouped.items():
            entries.sort(key=lambda x:x.position)
            if len(entries)!=counts[k] or [x.position for x in entries]!=list(range(1,counts[k]+1)) or len({x.key_raw for x in entries})!=len(entries):
                raise ValueError('missing/duplicate pair')
        return MappingProxyType({k:tuple(v) for k,v in grouped.items()})
    except (OSError,ValueError,KeyError,TypeError):
        return MappingProxyType({})


def pair_reference(table, record_id, field_number):
    if type(table) is not str or type(record_id) is not int or type(field_number) is not int:
        return None
    return catalog().get((table,record_id,field_number))


def class_cost_reference(class_record_id):
    """Static item/quantity pairs for a class record; no payment-state inference."""
    return pair_reference('GunClassData',class_record_id,7)


def weapon_cost_reference(model_id, field_number):
    """Two independent raw cost sets; their action labels are not yet confirmed."""
    return pair_reference('GunWeaponData',model_id,field_number)
