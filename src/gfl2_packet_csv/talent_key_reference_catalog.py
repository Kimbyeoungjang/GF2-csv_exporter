"""Static model and skill indexes; not account state or confirmed enhancement rules."""
from collections import defaultdict
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from .attachment_affixes import _rows
from .key_catalog import catalog as key_catalog
from .common_key_enhancement_catalog import base_catalog, copy_catalog

@dataclass(frozen=True)
class KeySkillReference:
    key_id: int
    key_type: int | None
    type_position: int
    skill_id: int | None
    skill_position: int

@dataclass(frozen=True)
class CommonKeyModelReference:
    key_id: int
    property_id: int
    field15: int | None
    field16: int | None
    field17: int | None
    field19: int | None
    field21: int | None

def _optional(s):
    return None if s == '' else int(s)

def _load():
    types = key_catalog()
    skills = {int(r['skill_id']) for r in _rows('battle_skill_ids.csv')}
    keys, by_type, by_skill = {}, defaultdict(dict), defaultdict(dict)
    for r in _rows('talent_key_skill_index.csv'):
        ref = KeySkillReference(int(r['key_id']), _optional(r['key_type']), int(r['type_position']), _optional(r['skill_id']), int(r['skill_position']))
        if (ref.key_id <= 0 or ref.key_id in keys or ref.key_id not in types
                or ref.key_type != types[ref.key_id][0]
                or (ref.skill_id is not None and ref.skill_id not in skills)
                or min(ref.type_position, ref.skill_position) <= 0
                or ref.type_position in by_type[ref.key_type]
                or ref.skill_position in by_skill[ref.skill_id]):
            raise ValueError('invalid key index')
        keys[ref.key_id] = ref
        by_type[ref.key_type][ref.type_position] = ref.key_id
        by_skill[ref.skill_id][ref.skill_position] = ref.key_id
    if not keys or set(keys) != set(types):
        raise ValueError('incomplete key index')
    def ordered(groups):
        for values in groups.values():
            if set(values) != set(range(1,len(values)+1)):
                raise ValueError('noncontiguous index')
        return MappingProxyType({g:tuple(v[p] for p in range(1,len(v)+1)) for g,v in groups.items()})
    base, copies = base_catalog(), copy_catalog()
    if not base or not copies:
        raise ValueError('missing base or copy catalog')
    common = {}
    for r in _rows('common_key_model_reference.csv'):
        ref = CommonKeyModelReference(int(r['key_id']), int(r['property_id']), *(_optional(r[f'field{n}']) for n in (15,16,17,19,21)))
        if (ref.key_id in common or ref.key_id not in base or ref.property_id != base[ref.key_id][0].property_id
                or keys[ref.key_id].key_type != 2 or ref.field16 not in (1,2)
                or ref.field17 not in (None,1) or ref.field19 not in (None,1) or ref.field21 not in (None,1)
                or (ref.field15 is not None and ref.field15 <= 0)
                or (ref.field21 == 1) != (ref.key_id in copies)):
            raise ValueError('invalid common-key reference')
        common[ref.key_id] = ref
    if set(common) != set(base) or set(common) != set(by_type[2].values()):
        raise ValueError('incomplete common-key reference')
    return MappingProxyType(keys), ordered(by_type), ordered(by_skill), MappingProxyType(common)

@lru_cache(maxsize=1)
def catalogs():
    try:
        return _load()
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return tuple(MappingProxyType({}) for _ in range(4))

def key_reference(key_id):
    return catalogs()[0].get(key_id) if type(key_id) is int else None

def keys_for_type(key_type):
    """None selects the source group whose ID field is absent."""
    return catalogs()[1].get(key_type) if key_type is None or type(key_type) is int else None

def keys_for_skill(skill_id):
    """None selects keys without a static skill reference; never skill ID zero."""
    return catalogs()[2].get(skill_id) if skill_id is None or type(skill_id) is int else None

def common_key_reference(key_id):
    """Raw model flags, not an eligibility decision or player copy count."""
    return catalogs()[3].get(key_id) if type(key_id) is int else None
