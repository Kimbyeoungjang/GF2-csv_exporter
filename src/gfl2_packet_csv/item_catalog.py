"""Static ItemData and LoveGiftData projections. Never infer missing inventory.

Field 3 of LoveGiftData is an affinity-value candidate, not a confirmed
per-doll effective gift value. Preference groups are static research data.
"""
from .reference_data import lru_cache
from .doll_growth_catalog import _rows

FIELDS = ('item_category_code', 'item_rarity_code', 'item_reference_status',
          'gift_base_affinity_candidate', 'gift_reference_status')


@lru_cache(maxsize=1)
def catalog():
    try:
        rows = _rows('item_reference.csv')
        result = {}
        for row in rows:
            item = int(row['item_id'])
            category = int(row['category_code'])
            rarity = int(row['rarity_code']) if row['rarity_code'] else None
            listed = int(row['gift_listed'])
            affinity = int(row['gift_base_affinity_candidate']) if row['gift_base_affinity_candidate'] else None
            if (item <= 0 or item in result or category <= 0 or listed not in (0, 1)
                    or (rarity is not None and not 1 <= rarity <= 5)
                    or (listed and category != 122)
                    or (affinity is not None and (not listed or affinity <= 0))):
                return {}
            result[item] = (category, rarity, listed, affinity)
        return result
    except (OSError, KeyError, ValueError, TypeError):
        return {}


def projection(item):
    ref = catalog()
    if item not in ref:
        return None, None, 'unrecognized_item' if ref else 'catalog_unavailable', None, 'item_reference_unavailable'
    category, rarity, listed, affinity = ref[item]
    return (category, rarity, 'catalog_consistent', affinity,
            ('catalog_candidate' if affinity is not None else 'gift_value_absent') if listed else 'not_in_gift_catalog')


def validate_row(row):
    expected = tuple('' if v is None else str(v) for v in projection(int(row['item_id'])))
    if tuple(row[f] for f in FIELDS) != expected:
        raise ValueError('invalid item catalog projection')
    if not row['quantity']:
        raise ValueError('missing item quantity')
