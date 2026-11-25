"""Static reference projections; no capture-derived identifiers in catalogs."""
from .reference_data import lru_cache
from .reference_data import files
import csv


def _load(filename, columns):
    try:
        with files('gfl2_packet_csv.static_tables').joinpath(filename).open('r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != list(columns):
                return {}
            result = {}
            for row in reader:
                key, *values = (int(row[c]) for c in columns)
                if key <= 0 or key in result or any(v <= 0 for v in values):
                    return {}
                result[key] = tuple(values)
            return result
    except (OSError, ValueError, TypeError, KeyError):
        return {}


@lru_cache(maxsize=1)
def models():
    return _load('remolding_model_ids.csv', ('model_id',))


@lru_cache(maxsize=1)
def options():
    result = _load('remolding_option_catalog.csv', ('option_id','feature_group_id','option_level'))
    return result if all(k == g*100+l for k,(g,l) in result.items()) else {}


def model_status(model_id):
    return 'catalog_unavailable' if not models() else 'catalog_consistent' if model_id in models() else 'unrecognized'


def option_projection(option_id):
    ref = options().get(option_id)
    return (*ref, 'catalog_consistent') if ref else (None, None, 'unrecognized' if options() else 'catalog_unavailable')


@lru_cache(maxsize=1)
def primary_attributes():
    result = _load('support_normal_chip_primary.csv', ('model_id','attribute_id','base_value'))
    return result if all(1 <= a <= 6 for a,b in result.values()) else {}


def primary_projection(kind, model_id, attrs):
    if kind != 'normal':
        return None, None, 'not_applicable'
    ref = primary_attributes().get(model_id)
    if ref is None:
        return None, None, 'unrecognized' if primary_attributes() else 'catalog_unavailable'
    attribute_id, base_value = ref
    return attribute_id, base_value, 'catalog_consistent' if attrs[attribute_id-1] == base_value else 'value_mismatch'


@lru_cache(maxsize=1)
def secondary_attribute_counts():
    """Static field 8 is ASCII 0..3, not a packed integer or account data."""
    try:
        with files('gfl2_packet_csv.static_tables').joinpath('support_normal_chip_secondary_counts.csv').open('r', encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        result = {int(r['model_id']): int(r['secondary_attribute_count']) for r in rows}
        if len(result) != len(rows) or set(result) != set(primary_attributes()) or any(not 0 <= n <= 3 for n in result.values()):
            return {}
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def secondary_projection(kind, model_id, attrs):
    if kind != 'normal':
        return None, None, 'not_applicable'
    counts = secondary_attribute_counts()
    if not counts:
        return None, None, 'catalog_unavailable'
    if model_id not in counts:
        return None, None, 'unrecognized'
    primary = primary_attributes()[model_id][0]
    expected = counts[model_id]
    observed = sum(value != 0 for position, value in enumerate(attrs, 1) if position != primary)
    return expected, observed, 'catalog_consistent' if expected == observed else 'count_mismatch'
