"""Static cross-checks, not proof of a controlled growth operation.
Only catalog-consistent stage values are exported. Affinity observations are
not rewritten or inferred from a threshold or a missing field.
"""
from __future__ import annotations
import csv
from .reference_data import lru_cache
from .reference_data import files


def _rows(name):
    try:
        with files('gfl2_packet_csv.static_tables').joinpath(name).open('r', encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f))
    except (OSError, ValueError):
        return []


@lru_cache(maxsize=1)
def class_caps():
    try:
        rows = _rows('doll_class_caps.csv')
        result = {int(r['class_stage']): int(r['level_cap']) for r in rows}
        if not result or len(result) != len(rows) or any(k <= 0 or v <= 0 for k, v in result.items()):
            return {}
        return result
    except (KeyError, ValueError, TypeError):
        return {}


@lru_cache(maxsize=1)
def affinity_thresholds():
    try:
        rows = _rows('doll_affinity_thresholds.csv')
        result = {int(r['level']): (int(r['lower_xp']), int(r['upper_xp']) if r['upper_xp'] else None) for r in rows}
        if not result or len(result) != len(rows) or any(k <= 0 or lo < 0 or (hi is not None and hi < lo) for k,(lo,hi) in result.items()):
            return {}
        return result
    except (KeyError, ValueError, TypeError):
        return {}


def class_projection(stage, level):
    if stage is None:
        return None, 'field_absent'
    if stage == 0:
        return None, 'explicit_zero'
    caps = class_caps()
    if not caps:
        return None, 'catalog_unavailable'
    if stage not in caps:
        return None, 'unrecognized'
    if level is None:
        return None, 'level_unavailable'
    if not 1 <= level <= caps[stage]:
        return None, 'level_cap_mismatch'
    return stage, 'catalog_consistent'


def affinity_status(affinity):
    if affinity is None:
        return 'message_absent'
    level, xp = affinity['level'], affinity['cumulative_xp']
    if level is None:
        return 'level_absent'
    if xp is None:
        return 'xp_absent'
    bounds = affinity_thresholds()
    if not bounds:
        return 'catalog_unavailable'
    if level not in bounds:
        return 'unrecognized_level'
    lo, hi = bounds[level]
    # Inclusive boundary: a gate/reward may hold the level at its threshold.
    return 'threshold_consistent' if lo <= xp and (hi is None or xp <= hi) else 'threshold_mismatch'


@lru_cache(maxsize=1)
def core_models():
    try:
        rows = _rows('doll_core_models.csv')
        result = {int(r['doll_id']): int(r['core_model_id']) for r in rows}
        if (not result or len(result) != len(rows) or len(set(result.values())) != len(result)
                or any(min(d, c) <= 0 for d, c in result.items())):
            return {}
        return result
    except (KeyError, ValueError, TypeError):
        return {}


@lru_cache(maxsize=1)
def rank_records():
    try:
        rows = _rows('doll_rank_records.csv')
        result = {(int(r['doll_id']), int(r['rank'])): int(r['grade_record_id']) for r in rows}
        if (not result or len(result) != len(rows)
                or any(d <= 0 or not 0 <= rank <= 6 or record != d * 100 + rank + 1
                       for (d, rank), record in result.items())
                or any({r for d, r in result if d == did} != set(range(7))
                       for did in {d for d, _ in result})):
            return {}
        return result
    except (KeyError, ValueError, TypeError):
        return {}


def core_model_status(doll_id, core):
    if core is None:
        return 'message_absent'
    model = core['model_id']
    if model is None:
        return 'model_absent'
    catalog = core_models()
    if not catalog:
        return 'catalog_unavailable'
    expected = catalog.get(int(doll_id))
    if expected is None:
        return 'unknown_doll'
    return 'catalog_consistent' if model == expected else 'doll_model_mismatch'


def rank_projection(doll_id, rank):
    # Missing rank is not proof of zero even though a static rank-zero row exists.
    if rank is None:
        return None, 'field_absent'
    catalog = rank_records()
    if not catalog:
        return None, 'catalog_unavailable'
    if not 0 <= rank <= 6:
        return None, 'outside_rank_domain'
    record = catalog.get((int(doll_id), rank))
    return (record, 'catalog_consistent') if record is not None else (None, 'unknown_doll')


def helix_mask_projection(value):
    """A population count is structural only; not an unlocked-node count."""
    if not isinstance(value, int) or not 0 <= value < (1 << 64):
        raise ValueError('invalid helix mask candidate')
    if value in (8191, 16383):
        return value.bit_count(), 'observed_contiguous_mask_candidate'
    # Unobserved patterns are preserved in the existing value column, but are
    # not promoted into a bit count or a semantic interpretation.
    return None, 'unverified_pattern'
