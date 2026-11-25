"""Static gift affinity reference for GF2Tools, independent of account exports.

LoveConfigData fields 2..4, cross-checked against three published doll guides.
This is a static calculation, not an observation of a gift-use transaction.
"""
from dataclasses import dataclass
from .reference_data import lru_cache
from .doll_growth_catalog import _rows
from .item_catalog import catalog

RAM_IDS = frozenset((300003, 300004, 300005))


@lru_cache(maxsize=1)
def preference_bonuses():
    try:
        rows = _rows('gift_preference_bonuses.csv')
        values = {int(r['source_field']): int(r['bonus_percent']) for r in rows}
        if len(rows) != 3 or set(values) != {2, 3, 4} or not 100 >= values[2] > values[3] > values[4] > 0:
            return {}
        return values
    except (KeyError, TypeError, ValueError):
        return {}


@lru_cache(maxsize=1)
def preference_groups():
    """Validate the entire static projection before using a non-preferred default."""
    try:
        items = catalog()
        if not items:
            return {}
        slots = {}
        result = {}
        for r in _rows('gift_preference_group_candidates.csv'):
            doll, field, pos, item = (int(r[k]) for k in ('doll_id', 'source_field', 'position', 'gift_item_id'))
            if doll <= 0 or field not in (2, 3, 4) or not 1 <= pos <= 8 or item in RAM_IDS or item not in items or not items[item][2] or items[item][3] is None:
                return {}
            slot = (field, pos)
            if slot in slots.setdefault(doll, set()) or item in result.setdefault(doll, {}):
                return {}
            slots[doll].add(slot)
            result[doll][item] = field
        expected = {(f, p) for f in (2, 3, 4) for p in range(1, 9)}
        if not result or any(v != expected for v in slots.values()):
            return {}
        return result
    except (KeyError, TypeError, ValueError):
        return {}


@dataclass(frozen=True)
class GiftAffinity:
    base: int | None
    bonus_percent: int | None
    # Exact numerator in hundredths of an affinity point; no UI rounding assumption.
    effective_hundredths: int | None
    status: str


def gift_affinity(doll_id: int, item_id: int) -> GiftAffinity:
    """Return static reference only. Missing/unsupported values never become zero."""
    if type(doll_id) is not int or type(item_id) is not int or doll_id <= 0 or item_id <= 0:
        raise ValueError('invalid doll or item ID')
    items = catalog()
    if not items:
        return GiftAffinity(None, None, None, 'item_catalog_unavailable')
    if item_id not in items or not items[item_id][2]:
        return GiftAffinity(None, None, None, 'not_in_gift_catalog')
    base = items[item_id][3]
    if base is None:
        return GiftAffinity(None, None, None, 'gift_value_absent')
    if item_id in RAM_IDS:
        return GiftAffinity(base, 0, base * 100, 'universal_ram_reference')
    groups, bonuses = preference_groups(), preference_bonuses()
    if not groups or not bonuses:
        return GiftAffinity(base, None, None, 'preference_catalog_unavailable')
    if doll_id not in groups:
        return GiftAffinity(base, None, None, 'doll_preference_unavailable')
    # An item missing from the entire category projection may be a new special gift.
    if not any(item_id in g for g in groups.values()):
        return GiftAffinity(base, None, None, 'gift_preference_unavailable')
    field = groups[doll_id].get(item_id)
    bonus = bonuses[field] if field is not None else 0
    return GiftAffinity(base, bonus, base * (100 + bonus), 'static_preference_reference')
