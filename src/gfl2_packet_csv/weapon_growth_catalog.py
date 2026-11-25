"""Catalog bounds, not proof of the weapon rank's display offset."""
from __future__ import annotations
from .reference_data import lru_cache
from .doll_growth_catalog import _rows


@lru_cache(maxsize=1)
def limits():
    try:
        rows = _rows('weapon_growth_limits.csv')
        result = {int(r['model_id']): (int(r['level_cap']), int(r['rank_cap'])) for r in rows}
        if len(result) != len(rows) or any(k <= 0 or min(v) <= 0 for k,v in result.items()):
            return {}
        return result
    except (KeyError, TypeError, ValueError):
        return {}


def growth_projection(model, level, rank):
    catalog = limits()
    caps = catalog.get(model)
    if caps is None:
        status = 'unknown_model' if catalog else 'catalog_unavailable'
        return None, None, status, status
    def status(value, cap):
        if value is None:
            return 'field_absent'
        return 'within_observed_positive_range' if 1 <= value <= cap else 'outside_observed_positive_range'
    return *caps, status(level, caps[0]), status(rank, caps[1])
