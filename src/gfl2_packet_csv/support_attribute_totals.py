"""Static support attributes; preserve observed values even on mismatch."""
from .reference_data import lru_cache
from .reference_data import files
import csv
from .remolding_catalog import primary_attributes, secondary_attribute_counts
from .support_catalog import room_levels


def _rows(name, header):
    with files('gfl2_packet_csv.static_tables').joinpath(name).open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != list(header):
            raise ValueError('invalid support catalog header')
        return list(reader)


@lru_cache(maxsize=1)
def secondary_values():
    try:
        result = {}
        for row in _rows('support_chip_secondary_values.csv', ('model_id','count','values_sorted')):
            model, count = int(row['model_id']), int(row['count'])
            vals = tuple(map(int, row['values_sorted'].split(';'))) if row['values_sorted'] else ()
            if model in result or count != secondary_attribute_counts().get(model) or len(vals) != count or any(v <= 0 for v in vals) or vals != tuple(sorted(vals)):
                return {}
            result[model] = vals
        return result if set(result) == set(primary_attributes()) else {}
    except (OSError, KeyError, TypeError, ValueError):
        return {}


def secondary_value_status(kind, model, attrs):
    if kind != 'normal':
        return 'not_applicable'
    refs = secondary_values()
    if not refs:
        return 'catalog_unavailable'
    if model not in refs:
        return 'unrecognized'
    primary = primary_attributes()[model][0]
    observed = tuple(sorted(v for i, v in enumerate(attrs, 1) if i != primary and v))
    return 'catalog_consistent' if refs[model] == observed else 'value_mismatch'


@lru_cache(maxsize=1)
def level_totals():
    try:
        result = {}
        fields = tuple(f'attr_{i}' for i in range(1,7))
        for row in _rows('support_level_attribute_totals.csv', ('level', *fields, 'capacity_candidate')):
            level = int(row['level'])
            values = tuple(int(row[k]) for k in (*fields, 'capacity_candidate'))
            if level in result or any(v <= 0 for v in values):
                return {}
            result[level] = values
        return result if set(result) == room_levels() else {}
    except (OSError, KeyError, TypeError, ValueError):
        return {}


def attribute_projection(level, formation, chips, attribute_id, observed):
    if not 1 <= attribute_id <= 6:
        return None, None, 'unrecognized_attribute'
    levels = level_totals()
    if not levels or level not in levels:
        return None, None, 'level_unavailable'
    base = levels[level][attribute_id-1]
    refs = [uid for frame in formation['frames'].values() for uid in frame['slots'] if uid is not None]
    if not refs:
        total = 0
    elif chips is None or any(uid not in chips['normal'] for uid in refs) or len(refs) != len(set(refs)):
        return base, None, 'chip_reference_unavailable'
    else:
        total = sum(chips['normal'][uid]['attributes'][attribute_id-1] for uid in refs)
    return base, total, 'field_absent' if observed is None else 'catalog_consistent_candidate' if observed == base + total else 'value_mismatch'
